"""Persistence layer for resource libraries and immutable asset revisions
(task 2.1 of `adopt-resource-session-planning`).

The migration that creates the underlying tables lives in
``backend.db.SCHEMA``; this module is the small Python surface the rest
of the resource-mode work calls against it. The contract is deliberately
narrow: a library is registered once and looked up by a safe key, a
revision is recorded immutably against a (library, source_id, content)
triple, and revisions are read back exactly as they were written.

Three rules are pinned here and nowhere else:

  1. A revision is identified by the tuple
     ``(library_id, source_id, content_digest)``. Identical content for
     the same library and the same source entry does NOT create a second
     row; the same call returns the existing row's id. This is the
     uniqueness guarantee the spec requires. A second row for a CHANGED
     source entry DOES get created, because the digest is different.

  2. The original accepted object is stored in the ``payload`` column
     as JSON, preserving every nested structure and every original
     string verbatim. The canonical form used for the digest is
     key-sorted, but the stored payload is NOT re-serialized in any way
     that would change strings, the order of items in a list, the
     number-form of a number, or the presence of an empty mapping. A
     round trip through ``record_revision`` then ``get_revision``
     returns a value that compares equal to the input.

  3. The ``translation`` and ``coverage`` columns are stored SEPARATELY
     from ``payload``. They are JSON objects (defaults are empty
     objects) and can be updated by a future task without rewriting
     the original payload. A caller that does not supply them records a
     revision with the empty defaults, which is the safe neutral state.

Nothing in this module parses source files, walks directories, builds
import reports, opens HTTP routes, calls out to ComfyUI, decides
readiness or invents a session plan. Those are the next tasks. This
module's only job is to make the schema write and read what the
``resource-store`` spec promises.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

import db


# -- The content digest -----------------------------------------------------


def canonical_digest(payload: Any) -> str:
    """The hex sha256 of the canonical form of ``payload``.

    Two payloads that compare equal as JSON values produce the same
    digest regardless of key order or insignificant whitespace. A
    payload that differs by even one character in any string, by one
    item in any list, or by one key in any object, produces a different
    digest. Floats and ints are preserved as their original numeric
    form (``_canonical_form`` round-trips through ``int(value)`` for
    whole-number floats to keep the digest stable across platforms that
    serialise ``1.0`` and ``1`` differently).
    """
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    """Canonical JSON bytes for an arbitrary JSON-compatible value.

    Object keys are sorted, lists keep their order, scalars keep their
    type, and floats that are whole numbers are written without a
    trailing ``.0`` so ``1.0`` and ``1`` share a digest.
    """
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canonicalize(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canonicalize(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


# -- Libraries --------------------------------------------------------------


def ensure_library(
    library_key: str,
    *,
    display_name: str = "",
    kind: str = "",
) -> int:
    """Return the id of the library with ``library_key``, creating it if absent.

    The unique key on ``resource_library.library_key`` is what makes
    this idempotent: a second call with the same key returns the
    existing row's id and does not write a second row. The
    ``display_name`` and ``kind`` of an existing row are NOT updated;
    registration is one-shot, the operator who first registered a
    library owns its label.
    """
    if not isinstance(library_key, str) or not library_key:
        raise ValueError("library_key must be a non-empty string")
    row = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        library_key,
    )
    if row is not None:
        return int(row["id"])
    return int(db.run(
        """INSERT INTO resource_library
           (library_key, display_name, kind, created_at)
           VALUES (?, ?, ?, ?)""",
        library_key,
        display_name,
        kind,
        db.now(),
    ))


def list_libraries() -> list[dict]:
    """Every registered library, ordered by id."""
    return db.q("SELECT id, library_key, display_name, kind, created_at "
                "FROM resource_library ORDER BY id")


# -- Revisions --------------------------------------------------------------


def record_revision(
    library_id: int,
    source_id: str,
    payload: Any,
    *,
    translation: Any | None = None,
    coverage: Any | None = None,
) -> int:
    """Return the id of the revision for this triple, creating it if absent.

    A revision is uniquely identified by
    ``(library_id, source_id, content_digest)``. Identical content for
    the same library and source entry returns the existing row's id and
    does NOT create a second row. A CHANGED source entry (a payload
    whose canonical digest is different) creates a new row alongside
    the prior one; both stay readable and immutable.

    The ``payload`` column stores the original accepted object as
    JSON. The ``translation`` and ``coverage`` columns store their own
    JSON values separately (default ``{}``) and can be supplied or
    omitted. None of the three is rewritten once stored: a later call
    with the same triple and different translation/coverage is
    rejected as a duplicate, because the unique key is on the content
    alone and the row's payload, translation and coverage are written
    once and read back exactly.
    """
    if not isinstance(library_id, int) or isinstance(library_id, bool):
        raise TypeError(
            f"library_id must be an int, got {type(library_id).__name__}"
        )
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("source_id must be a non-empty string")
    if not isinstance(payload, dict):
        raise TypeError(
            f"payload must be a dict, got {type(payload).__name__}"
        )
    digest = canonical_digest(payload)
    existing = db.one(
        "SELECT id FROM asset_revision "
        "WHERE library_id = ? AND source_id = ? AND content_digest = ?",
        library_id, source_id, digest,
    )
    if existing is not None:
        return int(existing["id"])
    return int(db.run(
        """INSERT INTO asset_revision
           (library_id, source_id, content_digest,
            payload, translation, coverage, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        library_id, source_id, digest,
        _encode_payload(payload),
        _encode_json_object(translation),
        _encode_json_object(coverage),
        db.now(),
    ))


def get_revision(
    *,
    revision_id: int | None = None,
    library_id: int | None = None,
    source_id: str | None = None,
    content_digest: str | None = None,
) -> dict | None:
    """Fetch a revision, decoding the JSON columns.

    The lookup is by one of:
      * ``revision_id`` alone (the primary key);
      * the triple ``(library_id, source_id, content_digest)`` (the
        natural key, which is the only one that identifies a single
        immutable revision when the same source entry has been imported
        more than once with different content);
      * the pair ``(library_id, source_id)`` (the latest revision
        for a source entry, which the spec calls for in 2.5's import
        report but is also useful for an inspection read).

    The function returns ``None`` when nothing matches; an empty
    database never raises. The returned dict carries the decoded
    ``payload``, ``translation`` and ``coverage`` Python values, so
    callers can compare them to the original input without a separate
    JSON-decode step.
    """
    if revision_id is not None:
        if library_id is not None or source_id is not None:
            raise ValueError(
                "pass revision_id alone or the (library_id, source_id[, "
                "content_digest]) triple, not both"
            )
        row = db.one("SELECT * FROM asset_revision WHERE id = ?", revision_id)
    elif library_id is not None and source_id is not None:
        if content_digest is None:
            row = db.one(
                "SELECT * FROM asset_revision "
                "WHERE library_id = ? AND source_id = ? "
                "ORDER BY id DESC LIMIT 1",
                library_id, source_id,
            )
        else:
            row = db.one(
                "SELECT * FROM asset_revision "
                "WHERE library_id = ? AND source_id = ? AND content_digest = ?",
                library_id, source_id, content_digest,
            )
    else:
        raise ValueError(
            "get_revision requires either revision_id or "
            "(library_id, source_id[, content_digest])"
        )
    if row is None:
        return None
    return _decode_revision(row)


def list_revisions(
    library_id: int,
    source_id: str | None = None,
) -> list[dict]:
    """Every revision for ``library_id``, optionally narrowed to one source.

    The result is ordered by id ascending, which is also the order the
    revisions were recorded. ``None`` for ``source_id`` returns every
    revision the library holds. The returned dicts carry decoded
    ``payload``, ``translation`` and ``coverage`` Python values.
    """
    if source_id is None:
        rows = db.q(
            "SELECT * FROM asset_revision WHERE library_id = ? ORDER BY id",
            library_id,
        )
    else:
        rows = db.q(
            "SELECT * FROM asset_revision "
            "WHERE library_id = ? AND source_id = ? "
            "ORDER BY id",
            library_id, source_id,
        )
    return [_decode_revision(r) for r in rows]


def update_translation(
    revision_id: int,
    translation: dict[str, Any],
) -> None:
    """Update the translation column for a revision.

    The row is identified by ``revision_id``. The function encodes
    ``translation`` as JSON and updates only the ``translation`` column.
    The database trigger ``asset_revision_protect_immutable`` protects
    all other columns against mutation.
    """
    if not isinstance(revision_id, int):
        raise TypeError(f"revision_id must be an int, got {type(revision_id).__name__}")
    if not isinstance(translation, dict):
        raise TypeError(f"translation must be a dict, got {type(translation).__name__}")
    encoded = _encode_json_object(translation)
    db.run(
        "UPDATE asset_revision SET translation = ? WHERE id = ?",
        encoded,
        revision_id,
    )


# -- Auxiliary resources ----------------------------------------------------


# The four kinds the parser recognises as auxiliary and the auxiliary
# table's CHECK accepts. Mirrored here so the Python surface can
# validate a kind argument before it reaches the SQL CHECK.
AUXILIARY_KINDS: tuple[str, ...] = (
    "translation_map", "cut_map", "mined_families", "mined_labels",
)


def record_auxiliary(
    library_id: int,
    kind: str,
    payload: Any,
) -> int:
    """Return the id of the auxiliary row for this triple, creating
    it if absent.

    A row is uniquely identified by
    ``(library_id, kind, content_digest)``. Identical content for the
    same library and kind returns the existing row's id and does NOT
    create a second row. A CHANGED payload (a different content
    digest) creates a new row alongside the prior one; both stay
    readable and immutable.

    The ``payload`` column stores the complete original auxiliary
    map as JSON, preserving every nested structure and every
    original string verbatim. The canonical form used for the
    digest is key-sorted, but the stored payload is NOT
    re-serialized in any way that would change strings, the order
    of items in a list, the number-form of a number, or the
    presence of an empty mapping. A round trip through
    ``record_auxiliary`` then ``list_auxiliary`` returns a value
    that compares equal to the input.
    """
    if not isinstance(library_id, int) or isinstance(library_id, bool):
        raise TypeError(
            f"library_id must be an int, got {type(library_id).__name__}"
        )
    if kind not in AUXILIARY_KINDS:
        raise ValueError(
            f"kind must be one of {AUXILIARY_KINDS}, got {kind!r}"
        )
    if not isinstance(payload, dict):
        raise TypeError(
            f"payload must be a dict, got {type(payload).__name__}"
        )
    digest = canonical_digest(payload)
    existing = db.one(
        "SELECT id FROM auxiliary_resource "
        "WHERE library_id = ? AND kind = ? AND content_digest = ?",
        library_id, kind, digest,
    )
    if existing is not None:
        return int(existing["id"])
    return int(db.run(
        """INSERT INTO auxiliary_resource
           (library_id, kind, content_digest, payload, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        library_id, kind, digest,
        _encode_payload(payload),
        db.now(),
    ))


def list_auxiliary(
    library_id: int,
    kind: str | None = None,
) -> list[dict]:
    """Every auxiliary row for ``library_id``, optionally narrowed by
    ``kind``.

    The result is ordered by id ascending, which is also the order
    the rows were recorded. The returned dicts carry the decoded
    ``payload`` Python value.
    """
    if kind is None:
        rows = db.q(
            "SELECT * FROM auxiliary_resource WHERE library_id = ? ORDER BY id",
            library_id,
        )
    else:
        rows = db.q(
            "SELECT * FROM auxiliary_resource "
            "WHERE library_id = ? AND kind = ? "
            "ORDER BY id",
            library_id, kind,
        )
    return [_decode_auxiliary(r) for r in rows]


# -- Internal helpers -------------------------------------------------------


def _encode_payload(payload: Any) -> str:
    """Encode ``payload`` for the ``payload`` column.

    The stored form is JSON of the original value with no
    key-reordering, no float-normalisation, no list-reversal: the
    original object round-trips exactly. ``ensure_ascii=False`` keeps
    the bytes form readable; ``separators`` keeps the form compact and
    deterministic for the test that compares stored vs original
    through the database.
    """
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _encode_json_object(value: Any | None) -> str:
    """Encode ``value`` for the ``translation`` / ``coverage`` columns.

    A ``None`` becomes the empty object, which is the safe neutral
    state the schema declares as the default. A non-None value is
    validated as a JSON object (a dict) and JSON-encoded.
    """
    if value is None:
        return "{}"
    if not isinstance(value, dict):
        raise TypeError(
            f"translation/coverage must be a dict or None, got "
            f"{type(value).__name__}"
        )
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode_revision(row: Any) -> dict:
    """Decode the JSON columns of a fetched revision row in place."""
    out = dict(row)
    for col in ("payload", "translation", "coverage"):
        raw = out.get(col)
        if isinstance(raw, str) and raw:
            out[col] = json.loads(raw)
        elif isinstance(raw, str):
            out[col] = {}
    return out


def _decode_auxiliary(row: Any) -> dict:
    """Decode the JSON payload column of a fetched auxiliary row in place."""
    out = dict(row)
    raw = out.get("payload")
    if isinstance(raw, str) and raw:
        out["payload"] = json.loads(raw)
    return out
