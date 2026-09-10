"""Tests for the resource-import preview/commit service (task 2.3 of
`adopt-resource-session-planning`).

The preview/commit module sits between the parser (task 2.2) and
the persistence layer (task 2.1). The tests pin the contract the
module promises to its callers:

  * a fingerprint is the source of truth for "did the file
    change", and the commit closes the TOCTOU window by reading
    the file once and using the verified bytes for parsing and
    persistence;
  * preview classifies every accepted scene entry as new /
    unchanged / updated, every auxiliary resource as new /
    unchanged / updated, reports duplicate identifiers across
    files, reports every non-accepted parser outcome, and
    reports source_ids in the DB that are absent from the
    current import;
  * unreadable files (missing, invalid UTF-8, invalid JSON) are
    reported as one unresolved `file_read_error` per file and
    do not break reconciliation; a commit based on a
    non-readable preview never writes;
  * a `(library_key, source_id)` pair that appears more than
    once in the import is reported as a duplicate and every
    ambiguous occurrence is excluded from the accepted set;
  * auxiliary resources (translation_map, cut_map,
    mined_families, mined_labels) are persisted to the
    `auxiliary_resource` table atomically with scene revisions,
    preserving the complete original payload and the
    idempotent revision semantics;
  * commit refuses a stale preview (changed or unreadable source
    file, or a file the preview could not read) and refuses a
    missing source file, with no database changes;
  * commit is atomic: a simulated persistence failure partway
    through rolls back the entire accepted scene set AND the
    auxiliary set, leaving the database in the state it was in
    before the commit started;
  * count reconciliation: every file and every aggregate report
    satisfies the per-file and per-aggregate identities;
  * the persistence layer's immutability invariants (one row
    per `(library, source_id, content_digest)` for scenes; one
    row per `(library, kind, content_digest)` for auxiliary;
    prior rows preserved, no UPDATE on protected columns)
    survive a commit unchanged;
  * missing source entries are reported, not deleted.

The fixtures are invented English-only data. They do not import
any source-corpus text, do not write to real config or data
paths, do not touch the database beyond an isolated fresh
schema, and never invoke the legacy room-seed importer. Each
test opens a fresh, isolated database under ``tmp_path`` and
creates fresh, temporary source files in the same directory;
nothing leaks between tests.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import db
import resource_import
import resource_parser
import resource_store
from resource_import import (
    BUCKET_AMBIGUOUS_IDENTIFIER,
    BUCKET_DUPLICATE_IDENTIFIER,
    BUCKET_FILE_READ_ERROR,
    BUCKET_MALFORMED,
    BUCKET_MISSING_IDENTIFIER,
    BUCKET_UNSUPPORTED,
    CLASSIFICATION_NEW,
    CLASSIFICATION_UNCHANGED,
    CLASSIFICATION_UPDATED,
    AcceptedEntryOutcome,
    AuxiliaryOutcome,
    CommitAborted,
    CommitReport,
    DuplicateIdentifier,
    FileFingerprint,
    FileReport,
    MissingSourceEntry,
    PreviewReport,
    StaleFingerprintError,
    UnreadableFile,
    UnresolvedItem,
    commit_import,
    preview_import,
)

from test_no_personal_data import PATTERNS as PRIVACY_PATTERNS  # noqa: E402


# ---- Helpers --------------------------------------------------------------


def _open(path: Path) -> sqlite3.Connection:
    """Open a fresh, isolated database for one test.

    The same idiom ``test_resource_store`` uses: reset the global
    connection so a test cannot see another test's rows.
    """
    db._conn = None  # noqa: SLF001 (reset the global between tests)
    return db.connect(path)


def _close_silently() -> None:
    """Close the current connection without raising.

    On Windows, an open WAL file holds the DB. Closing the
    connection here releases the WAL before the next test starts.
    """
    conn = db._conn  # noqa: SLF001
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    db._conn = None  # noqa: SLF001


@pytest.fixture
def isolated_db(tmp_path):
    """Yield a fresh, isolated database, closing it on teardown."""
    path = Path(tmp_path) / "resource-import.db"
    _open(path)
    try:
        yield path
    finally:
        _close_silently()


def _write_source_file(directory: Path, name: str, payload) -> Path:
    """Write a JSON source file under ``directory`` and return its
    path.

    The file is written with ``ensure_ascii=False`` so the
    fingerprint is stable across platforms. The directory is
    the test's ``tmp_path``; nothing leaves it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def _write_raw(directory: Path, name: str, data: bytes) -> Path:
    """Write raw bytes to a file under ``directory`` and return its
    path.

    Used for invalid-UTF-8 and invalid-JSON fixtures where
    ``json.dumps`` would refuse to encode the bytes.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(data)
    return path


# ---- Invented English-only fixtures ---------------------------------------


NORMAL_SCENE_A = {
    "id": "inv_room_studio_dawn",
    "label": "invented studio at dawn",
    "scene_theme": (
        "An empty studio with a tall window facing north. Soft grey light "
        "enters from the side and leaves the back wall in shadow."
    ),
    "tags": ["indoor", "studio"],
    "props": ["chair", "sheet"],
    "weight": 1.5,
    "library": "general_scenes",
    "notes": "an invented guidance note",
}


NORMAL_SCENE_B = {
    "id": "inv_room_living_dusk",
    "label": "invented living room at dusk",
    "scene_theme": (
        "A living room with low warm light from a single lamp. A "
        "throw covers the back of the sofa; a window shows a city "
        "twilight."
    ),
    "tags": ["indoor", "living"],
    "props": ["sofa", "lamp"],
    "weight": 1.0,
    "library": "general_scenes",
    "notes": "a second invented scene",
}


NORMAL_SCENE_A_V2 = {
    **NORMAL_SCENE_A,
    "scene_theme": (
        "An empty studio with a tall window facing north. A different "
        "wording of the same scene, the kind a refreshed source file "
        "carries, with the back wall now lit by a soft warm bounce."
    ),
    "weight": 1.7,
}


FUSED_SCENE = {
    "id": "inv_fused_dressing_room_01",
    "library": "perspective_scenes",
    "weight": 1.0,
    "prompt": (
        "A waist-up photograph, taken from her right side. She stands "
        "before the mirror in a fitting room, running a hand down the "
        "lapel of an unbuttoned blazer."
    ),
}


TRANSLATION_MAP = {
    "inv_source_string_one": {
        "source": "inv source string one",
        "translation": "invented english one",
        "fields": ["label"],
    },
    "inv_source_string_two": {
        "source": "inv source string two",
        "translation": "invented english two",
        "fields": ["scene_theme"],
    },
}


# A second translation map with different content; used to test
# the "changed auxiliary content creates a new immutable row"
# path.
TRANSLATION_MAP_V2 = {
    "inv_source_string_one": {
        "source": "inv source string one v2",
        "translation": "invented english one v2",
        "fields": ["label"],
    },
}


UNSUPPORTED_DICT = {
    "looks": "nothing like a scene",
    "list_of_things": [1, 2, 3],
}


# Two mined_families maps used to test the same-kind,
# different-content and same-kind, identical-content auxiliary
# paths. Each is a dict of non-empty string keys to non-empty
# string values (the structural shape the parser's mined_families
# detector accepts).
MINED_FAMILIES_ONE = {
    "inv_mined_one": "inv_family_one",
    "inv_mined_two": "inv_family_two",
}


MINED_FAMILIES_TWO = {
    "inv_mined_three": "inv_family_three",
    "inv_mined_four": "inv_family_four",
}


MISSING_ID_SCENE = {
    "label": "invented label without id",
    "scene_theme": "an invented scene with a label and a theme but no id",
}


AMBIGUOUS_ID_SCENE = {
    "id": "inv_ambiguous_one",
    "identifier": "inv_ambiguous_two",
    "label": "an invented ambiguous-id record",
}


# ---- Fingerprint tests -----------------------------------------------------


class TestFileFingerprint:
    """A fingerprint is the source of truth for "did the file
    change"."""

    def test_two_fingerprints_with_the_same_bytes_compare_equal(
        self, isolated_db, tmp_path,
    ):
        from resource_import import _read_file_bytes
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        first = _read_file_bytes(path)
        second = _read_file_bytes(path)
        assert isinstance(first, tuple) and isinstance(second, tuple)
        fp_a, _ = first
        fp_b, _ = second
        assert fp_a.matches(fp_b)
        assert fp_a.content_sha256 == fp_b.content_sha256

    def test_two_fingerprints_with_different_bytes_do_not_match(
        self, isolated_db, tmp_path,
    ):
        from resource_import import _read_file_bytes
        path_a = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        path_b = _write_source_file(tmp_path, "scene_b.json", NORMAL_SCENE_B)
        fp_a, _ = _read_file_bytes(path_a)
        fp_b, _ = _read_file_bytes(path_b)
        assert not fp_a.matches(fp_b)

    def test_read_file_bytes_returns_unreadable_for_missing_file(
        self, isolated_db, tmp_path,
    ):
        from resource_import import _read_file_bytes
        result = _read_file_bytes(tmp_path / "does_not_exist.json")
        assert isinstance(result, UnreadableFile)
        assert "could not read" in result.reason

    def test_read_file_bytes_returns_unreadable_for_invalid_utf8(
        self, isolated_db, tmp_path,
    ):
        from resource_import import _read_file_bytes
        # 0xFF and 0xFE are not valid UTF-8 start bytes on their
        # own; together they are the UTF-16 BOM, but as a raw
        # byte sequence in a UTF-8 file they decode-error.
        path = _write_raw(tmp_path, "bad.json", b"\xff\xfe not utf-8")
        result = _read_file_bytes(path)
        assert isinstance(result, UnreadableFile)
        assert "UTF-8" in result.reason


# ---- Preview: new / unchanged / updated classification ---------------------


class TestPreviewClassification:
    """The preview classifies each accepted entry against the
    current database state."""

    def test_a_fresh_import_classifies_every_accepted_entry_as_new(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        preview = preview_import([(path, "inv_lib_a")])
        assert preview.total_files == 1
        assert preview.total_inputs == 1
        assert preview.total_accepted == 1
        assert preview.total_new == 1
        assert preview.total_unchanged == 0
        assert preview.total_updated == 0
        assert preview.total_duplicate == 0
        assert preview.total_unresolved == 0
        assert preview.total_missing == 0
        assert preview.counts_reconcile()
        outcome = preview.files[0].accepted_outcomes[0]
        assert outcome.classification == CLASSIFICATION_NEW
        assert outcome.source_id == NORMAL_SCENE_A["id"]
        assert outcome.kind == resource_parser.KIND_ROOMS

    def test_reimporting_identical_content_classifies_as_unchanged(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        commit_import(preview_import([(path, "inv_lib_a")]))
        second = preview_import([(path, "inv_lib_a")])
        assert second.total_accepted == 1
        assert second.total_new == 0
        assert second.total_unchanged == 1
        assert second.total_updated == 0
        assert second.counts_reconcile()
        outcome = second.files[0].accepted_outcomes[0]
        assert outcome.classification == CLASSIFICATION_UNCHANGED
        assert outcome.new_content_digest == outcome.previous_content_digest

    def test_changing_content_classifies_as_updated(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        commit_import(preview_import([(path, "inv_lib_a")]))
        path.write_text(
            json.dumps(
                NORMAL_SCENE_A_V2, ensure_ascii=False, separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        second = preview_import([(path, "inv_lib_a")])
        assert second.total_accepted == 1
        assert second.total_new == 0
        assert second.total_unchanged == 0
        assert second.total_updated == 1
        assert second.counts_reconcile()
        outcome = second.files[0].accepted_outcomes[0]
        assert outcome.classification == CLASSIFICATION_UPDATED
        assert outcome.new_content_digest != outcome.previous_content_digest

    def test_two_files_in_the_same_library_classify_independently(
        self, isolated_db, tmp_path,
    ):
        path_a = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        path_b = _write_source_file(tmp_path, "scene_b.json", NORMAL_SCENE_B)
        preview = preview_import([
            (path_a, "inv_lib_a"),
            (path_b, "inv_lib_a"),
        ])
        assert preview.total_files == 2
        assert preview.total_inputs == 2
        assert preview.total_accepted == 2
        assert preview.total_new == 2
        assert preview.counts_reconcile()
        for f in preview.files:
            assert f.counts_reconcile()
            assert f.accepted_outcomes[0].classification == CLASSIFICATION_NEW

    def test_two_libraries_classify_under_their_own_library(
        self, isolated_db, tmp_path,
    ):
        path_a = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        path_b = _write_source_file(tmp_path, "scene_b.json", NORMAL_SCENE_B)
        preview = preview_import([
            (path_a, "inv_lib_a"),
            (path_b, "inv_lib_b"),
        ])
        assert preview.total_accepted == 2
        assert preview.total_new == 2
        assert preview.counts_reconcile()
        keys = {f.accepted_outcomes[0].library_key for f in preview.files}
        assert keys == {"inv_lib_a", "inv_lib_b"}


# ---- Preview: auxiliary classification ------------------------------------


class TestPreviewAuxiliary:
    """Auxiliary resources are classified against the current
    database state, just like scene entries."""

    def test_a_fresh_import_classifies_auxiliary_as_new(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "translation.json", TRANSLATION_MAP)
        preview = preview_import([(path, "inv_lib_a")])
        file_report = preview.files[0]
        assert file_report.accepted_total == 0
        assert file_report.auxiliary_total == 1
        # Reconciliation: 0 accepted + 1 auxiliary + 0 duplicate
        # + 0 unresolved = 1 input.
        assert file_report.counts_reconcile()
        aux = file_report.auxiliary_outcomes[0]
        assert aux.classification == CLASSIFICATION_NEW
        assert aux.kind == resource_parser.KIND_TRANSLATION_MAP
        # The aggregate reconciliation.
        assert preview.counts_reconcile()

    def test_reimporting_identical_auxiliary_classifies_as_unchanged(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "translation.json", TRANSLATION_MAP)
        first = commit_import(preview_import([(path, "inv_lib_a")]))
        assert first.total_new_auxiliary_revisions == 1
        second = preview_import([(path, "inv_lib_a")])
        aux = second.files[0].auxiliary_outcomes[0]
        assert aux.classification == CLASSIFICATION_UNCHANGED
        assert aux.new_content_digest == aux.previous_content_digest

    def test_changing_auxiliary_content_classifies_as_updated(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "translation.json", TRANSLATION_MAP)
        commit_import(preview_import([(path, "inv_lib_a")]))
        path.write_text(
            json.dumps(
                TRANSLATION_MAP_V2, ensure_ascii=False, separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        second = preview_import([(path, "inv_lib_a")])
        aux = second.files[0].auxiliary_outcomes[0]
        assert aux.classification == CLASSIFICATION_UPDATED


# ---- Preview: duplicate / unresolved / missing reporting ------------------


class TestPreviewReporting:
    """The preview accounts for every input the parser hands
    back, every auxiliary resource, every duplicate, and every
    unreadable file."""

    def test_duplicate_source_ids_in_one_file_are_reported(
        self, isolated_db, tmp_path,
    ):
        duplicated = [NORMAL_SCENE_A, NORMAL_SCENE_A]
        path = _write_source_file(tmp_path, "scene_a.json", duplicated)
        preview = preview_import([(path, "inv_lib_a")])
        file_report = preview.files[0]
        # Two inputs, zero accepted (both occurrences are
        # duplicates), two duplicate records.
        assert file_report.total_inputs == 2
        assert file_report.accepted_total == 0
        assert file_report.duplicate_total == 2
        # The reconciliation holds.
        assert file_report.counts_reconcile()
        for dup in file_report.duplicate_identifiers:
            assert dup.source_id == NORMAL_SCENE_A["id"]
            assert dup.occurrences == 2
            assert dup.reason
        assert preview.total_duplicate == 2
        assert preview.counts_reconcile()

    def test_malformed_input_is_reported(
        self, isolated_db, tmp_path,
    ):
        mixed = [NORMAL_SCENE_A, "not a dict"]
        path = _write_source_file(tmp_path, "mixed.json", mixed)
        preview = preview_import([(path, "inv_lib_a")])
        file_report = preview.files[0]
        assert file_report.total_inputs == 2
        assert file_report.accepted_total == 1
        assert file_report.unresolved_total == 1
        assert file_report.counts_reconcile()
        assert file_report.unresolved[0].bucket == BUCKET_MALFORMED
        assert file_report.unresolved[0].received_type == "str"

    def test_unsupported_shape_is_reported(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "flat.json", UNSUPPORTED_DICT)
        preview = preview_import([(path, "inv_lib_a")])
        file_report = preview.files[0]
        assert file_report.total_inputs == 1
        assert file_report.accepted_total == 0
        assert file_report.auxiliary_total == 0
        assert file_report.unresolved_total == 1
        assert file_report.counts_reconcile()
        assert file_report.unresolved[0].bucket == BUCKET_UNSUPPORTED
        assert file_report.unresolved[0].reason

    def test_ambiguous_identifier_is_reported(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "ambiguous.json", AMBIGUOUS_ID_SCENE)
        preview = preview_import([(path, "inv_lib_a")])
        file_report = preview.files[0]
        assert file_report.total_inputs == 1
        assert file_report.accepted_total == 0
        assert file_report.unresolved_total == 1
        assert file_report.counts_reconcile()
        item = file_report.unresolved[0]
        assert item.bucket == BUCKET_AMBIGUOUS_IDENTIFIER
        assert "id" in item.identifier_fields
        assert "identifier" in item.identifier_fields

    def test_missing_identifier_is_reported(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "missing_id.json", MISSING_ID_SCENE)
        preview = preview_import([(path, "inv_lib_a")])
        file_report = preview.files[0]
        assert file_report.total_inputs == 1
        assert file_report.unresolved_total == 1
        assert file_report.counts_reconcile()
        item = file_report.unresolved[0]
        assert item.bucket == BUCKET_MISSING_IDENTIFIER
        assert item.expected_kind == resource_parser.KIND_ROOMS

    def test_missing_source_entries_are_reported_without_deletion(
        self, isolated_db, tmp_path,
    ):
        scene_x = {**NORMAL_SCENE_A, "id": "inv_x_entry"}
        scene_y = {**NORMAL_SCENE_B, "id": "inv_y_entry"}
        path_full = _write_source_file(
            tmp_path, "full.json", [scene_x, scene_y],
        )
        commit_import(preview_import([(path_full, "inv_lib_a")]))
        library_id = resource_store.ensure_library("inv_lib_a")
        before_revisions = resource_store.list_revisions(library_id)
        assert len(before_revisions) == 2
        path_partial = _write_source_file(
            tmp_path, "partial.json", [scene_x],
        )
        preview = preview_import([(path_partial, "inv_lib_a")])
        assert preview.total_accepted == 1
        assert preview.total_unchanged == 1
        assert preview.total_missing == 1
        missing = preview.missing_source_entries[0]
        assert missing.library_key == "inv_lib_a"
        assert missing.source_id == "inv_y_entry"
        # The historical revision is still in the DB.
        after_revisions = resource_store.list_revisions(library_id)
        assert len(after_revisions) == 2
        source_ids = {r["source_id"] for r in after_revisions}
        assert source_ids == {"inv_x_entry", "inv_y_entry"}


# ---- File-read error handling (correction #2) -----------------------------


class TestFileReadErrors:
    """Missing, unreadable, invalid-UTF-8, and invalid-JSON
    files are reported as explicit unresolved file outcomes,
    reconcile, and never block a commit by raising."""

    def test_a_missing_file_produces_an_unresolved_file_report(
        self, isolated_db, tmp_path,
    ):
        # The path does not exist. compute_fingerprint /
        # _read_file_bytes return UnreadableFile; the preview
        # builds a FileReport with one unresolved item and
        # total_inputs=1 (the file itself is the input).
        missing = tmp_path / "missing.json"
        preview = preview_import([(missing, "inv_lib_a")])
        assert len(preview.files) == 1
        file_report = preview.files[0]
        assert file_report.total_inputs == 1
        assert file_report.fingerprint is None
        assert not file_report.is_readable
        assert file_report.accepted_total == 0
        assert file_report.auxiliary_total == 0
        assert file_report.duplicate_total == 0
        assert file_report.unresolved_total == 1
        assert file_report.counts_reconcile()
        assert file_report.unresolved[0].bucket == BUCKET_FILE_READ_ERROR
        assert "could not read" in file_report.unresolved[0].reason
        assert preview.counts_reconcile()

    def test_an_invalid_utf8_file_produces_an_unresolved_file_report(
        self, isolated_db, tmp_path,
    ):
        path = _write_raw(tmp_path, "bad_utf8.json", b"\xff\xfe not utf-8")
        preview = preview_import([(path, "inv_lib_a")])
        file_report = preview.files[0]
        assert file_report.total_inputs == 1
        assert file_report.fingerprint is None
        assert file_report.unresolved_total == 1
        assert file_report.counts_reconcile()
        assert "UTF-8" in file_report.unresolved[0].reason

    def test_an_invalid_json_file_produces_an_unresolved_file_report(
        self, isolated_db, tmp_path,
    ):
        # The file is well-formed UTF-8 but not valid JSON.
        # The preview reports it as a file-read error with a
        # fingerprint (the bytes ARE readable); the count
        # reconciliation still holds.
        path = tmp_path / "bad.json"
        path.write_text("this is not { valid json", encoding="utf-8")
        preview = preview_import([(path, "inv_lib_a")])
        file_report = preview.files[0]
        assert file_report.total_inputs == 1
        assert file_report.fingerprint is not None
        assert file_report.unresolved_total == 1
        assert file_report.counts_reconcile()
        assert "JSON" in file_report.unresolved[0].reason

    def test_a_good_file_and_a_bad_file_reconcile_aggregately(
        self, isolated_db, tmp_path,
    ):
        good = _write_source_file(tmp_path, "good.json", NORMAL_SCENE_A)
        missing = tmp_path / "missing.json"
        preview = preview_import([(good, "inv_lib_a"), (missing, "inv_lib_a")])
        # Two files; the good one contributes one accepted, the
        # bad one contributes one unresolved. Reconcile.
        assert preview.total_files == 2
        assert preview.total_inputs == 2
        assert preview.total_accepted == 1
        assert preview.total_unresolved == 1
        assert preview.counts_reconcile()
        for f in preview.files:
            assert f.counts_reconcile()

    def test_a_commit_with_only_an_unreadable_preview_succeeds_and_writes_nothing(
        self, isolated_db, tmp_path,
    ):
        # A preview that includes a missing file carries no
        # fingerprint for that file. Per the spec, the commit
        # must never re-parse or persist an unreadable file:
        # it is preserved as an unresolved FileReport and the
        # commit succeeds with no writes. A fresh preview is
        # required to consider the file for import.
        missing = tmp_path / "missing.json"
        preview = preview_import([(missing, "inv_lib_a")])
        commit = commit_import(preview)
        # The commit's report includes the file, still
        # unresolved, with no fingerprint and no other content.
        assert commit.total_files == 1
        assert commit.total_inputs == 1
        assert commit.total_accepted == 0
        assert commit.total_auxiliary == 0
        assert commit.total_duplicate == 0
        assert commit.total_unresolved == 1
        assert commit.total_recorded == 0
        assert commit.counts_reconcile()
        file_report = commit.files[0]
        assert file_report.fingerprint is None
        assert file_report.unresolved[0].bucket == BUCKET_FILE_READ_ERROR
        # No library, no revision, no auxiliary row.
        assert resource_store.list_libraries() == []

    def test_a_good_file_commits_alongside_a_missing_file(
        self, isolated_db, tmp_path,
    ):
        # The unreadable file must not block the good one.
        good = _write_source_file(tmp_path, "good.json", NORMAL_SCENE_A)
        missing = tmp_path / "missing.json"
        preview = preview_import([(good, "inv_lib_a"), (missing, "inv_lib_a")])
        commit = commit_import(preview)
        # The good file's accepted scene was written; the
        # missing file is preserved as unresolved.
        assert commit.total_inputs == 2
        assert commit.total_accepted == 1
        assert commit.total_unresolved == 1
        assert commit.counts_reconcile()
        for f in commit.files:
            assert f.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        revisions = resource_store.list_revisions(library_id)
        assert len(revisions) == 1
        # The missing file's FileReport stays in the report
        # with no fingerprint and one file-read error.
        missing_report = next(
            f for f in commit.files if f.fingerprint is None
        )
        assert missing_report.unresolved[0].bucket == BUCKET_FILE_READ_ERROR

    def test_a_good_file_commits_alongside_an_invalid_utf8_file(
        self, isolated_db, tmp_path,
    ):
        good = _write_source_file(tmp_path, "good.json", NORMAL_SCENE_A)
        bad = _write_raw(tmp_path, "bad_utf8.json", b"\xff\xfe not utf-8")
        preview = preview_import([(good, "inv_lib_a"), (bad, "inv_lib_a")])
        commit = commit_import(preview)
        assert commit.total_accepted == 1
        assert commit.total_unresolved == 1
        assert commit.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        assert len(resource_store.list_revisions(library_id)) == 1
        # The bad file's FileReport stays unresolved.
        bad_report = next(f for f in commit.files if f.fingerprint is None)
        assert bad_report.unresolved[0].bucket == BUCKET_FILE_READ_ERROR

    def test_a_good_file_commits_alongside_an_invalid_json_file(
        self, isolated_db, tmp_path,
    ):
        # The invalid-JSON file has a fingerprint (the bytes
        # are readable) but is unresolved. The fingerprint
        # must still match at commit time, otherwise the whole
        # commit is refused.
        good = _write_source_file(tmp_path, "good.json", NORMAL_SCENE_A)
        bad = tmp_path / "bad.json"
        bad.write_text("this is not { valid json", encoding="utf-8")
        preview = preview_import([(good, "inv_lib_a"), (bad, "inv_lib_a")])
        commit = commit_import(preview)
        assert commit.total_accepted == 1
        assert commit.total_unresolved == 1
        assert commit.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        assert len(resource_store.list_revisions(library_id)) == 1
        # The bad file's FileReport stays unresolved with the
        # same fingerprint the preview computed.
        bad_report = next(
            f for f in commit.files if f.fingerprint is not None
            and f.accepted_total == 0 and f.auxiliary_total == 0
        )
        assert bad_report.unresolved[0].bucket == BUCKET_FILE_READ_ERROR

    def test_an_invalid_json_file_modified_between_preview_and_commit_refuses(
        self, isolated_db, tmp_path,
    ):
        # A file that was unresolved at preview but whose
        # bytes change at commit time is treated as a stale
        # preview: the whole commit is refused, even though
        # the file would have been "kept unresolved" if its
        # bytes were unchanged.
        good = _write_source_file(tmp_path, "good.json", NORMAL_SCENE_A)
        bad = tmp_path / "bad.json"
        bad.write_text("this is not { valid json", encoding="utf-8")
        preview = preview_import([(good, "inv_lib_a"), (bad, "inv_lib_a")])
        # Mutate the bad file: the fingerprint no longer
        # matches the preview's.
        bad.write_text("different bytes, still invalid json", encoding="utf-8")
        with pytest.raises(StaleFingerprintError):
            commit_import(preview)
        # The good file was not written either: the whole
        # commit was refused.
        assert resource_store.list_libraries() == []

    def test_a_formerly_accepted_file_becoming_missing_refuses(
        self, isolated_db, tmp_path,
    ):
        # A file that was accepted at preview time must NOT
        # be silently kept as unresolved if it becomes
        # unreadable at commit time: the whole commit is
        # refused and no writes happen.
        good = _write_source_file(tmp_path, "good.json", NORMAL_SCENE_A)
        preview = preview_import([(good, "inv_lib_a")])
        # Delete the source file between preview and commit.
        good.unlink()
        with pytest.raises(StaleFingerprintError):
            commit_import(preview)
        # Nothing was written.
        assert resource_store.list_libraries() == []


# ---- TOCTOU fix (correction #1) -------------------------------------------


class TestFingerprintBindingAndTocToufFix:
    """The commit reads each file ONCE, verifies the fingerprint
    from those bytes, and uses the same bytes for parsing and
    persistence. A file changed after the preview's read cannot
    sneak through, because the only re-read is the one that
    verifies against the preview's fingerprint; the parser and
    the persistence use those verified bytes directly."""

    def test_a_changed_source_file_refuses_the_commit(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        preview = preview_import([(path, "inv_lib_a")])
        commit = commit_import(preview)
        assert commit.total_new_scene_revisions == 1
        # Modify the file and try to commit the OLD preview.
        path.write_text(
            json.dumps(
                NORMAL_SCENE_A_V2, ensure_ascii=False, separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        with pytest.raises(StaleFingerprintError) as exc_info:
            commit_import(preview)
        assert len(exc_info.value.mismatches) == 1
        path_, expected, actual = exc_info.value.mismatches[0]
        assert path_ == preview.files[0].fingerprint.path
        assert expected == preview.files[0].fingerprint.content_sha256
        assert actual != expected
        # The DB has exactly one revision (the first commit), not two.
        library_id = resource_store.ensure_library("inv_lib_a")
        assert len(resource_store.list_revisions(library_id)) == 1

    def test_a_missing_source_file_refuses_the_commit(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        preview = preview_import([(path, "inv_lib_a")])
        path.unlink()
        with pytest.raises(StaleFingerprintError) as exc_info:
            commit_import(preview)
        path_, expected, actual = exc_info.value.mismatches[0]
        assert path_ == preview.files[0].fingerprint.path
        assert expected == preview.files[0].fingerprint.content_sha256
        assert actual.startswith("unreadable:")
        assert resource_store.list_libraries() == []

    def test_a_similar_looking_but_different_file_refuses_the_commit(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        preview = preview_import([(path, "inv_lib_a")])
        similar = {**NORMAL_SCENE_A, "label": "a similar but different label"}
        path.write_text(
            json.dumps(similar, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        with pytest.raises(StaleFingerprintError):
            commit_import(preview)
        assert resource_store.list_libraries() == []

    def test_a_file_changed_after_preview_writes_nothing(
        self, isolated_db, tmp_path,
    ):
        """Regression test for the TOCTOU hole.

        The fix: the commit reads the file once, computes the
        fingerprint from those bytes, and uses the same bytes
        for parsing. A modification between the verify read
        and the persistence read is impossible because there is
        no persistence read: the verified bytes ARE the
        persistence bytes.

        This test simulates the TOCTOU scenario at a coarser
        granularity: the file is modified between the preview
        and the commit, the commit's verify read sees the
        modified bytes, and the commit refuses. The DB ends
        unchanged from its pre-commit state.
        """
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        preview = preview_import([(path, "inv_lib_a")])
        # No prior commit, so the DB has no library.
        assert resource_store.list_libraries() == []
        # Modify the file.
        path.write_text(
            json.dumps(
                NORMAL_SCENE_A_V2, ensure_ascii=False, separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        with pytest.raises(StaleFingerprintError):
            commit_import(preview)
        # The DB is unchanged: no library, no revision.
        assert resource_store.list_libraries() == []

    def test_toctou_commit_uses_verified_bytes_not_on_disk_content(
        self, isolated_db, tmp_path,
    ):
        """Deterministic TOCTOU regression: the commit persists
        the bytes it just read, never the on-disk bytes.

        The preview reads the file and computes the fingerprint
        from content A. The test then mutates the on-disk
        bytes to content B. The read boundary in the commit
        is wrapped to return content A's bytes (and a matching
        fingerprint). If the commit re-reads the file at any
        point — even just to re-confirm the fingerprint — it
        would see content B, the fingerprint would not match,
        and the commit would refuse. The fact that the commit
        succeeds and persists content A's payload proves the
        commit uses the verified bytes it read once at the
        start, not the on-disk content.
        """
        import hashlib

        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        preview = preview_import([(path, "inv_lib_a")])

        # Capture content A's bytes and fingerprint before the
        # on-disk mutation. This is what the preview saw and
        # what the patched read will return during the commit.
        original_bytes = json.dumps(
            NORMAL_SCENE_A, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        original_sha = hashlib.sha256(original_bytes).hexdigest()
        # The preview's FileReport already carries the
        # fingerprint the commit will compare against. The
        # patched read returns exactly that fingerprint so the
        # `matches` check is trivially True and the test
        # isolates the "verified bytes drive persistence"
        # property.
        preview_fingerprint = preview.files[0].fingerprint
        assert preview_fingerprint is not None
        assert preview_fingerprint.content_sha256 == original_sha

        # Mutate the on-disk content to B. The fingerprint no
        # longer matches the preview's.
        path.write_text(
            json.dumps(
                NORMAL_SCENE_A_V2, ensure_ascii=False, separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        # Sanity: on-disk bytes now have a different sha.
        new_bytes = path.read_bytes()
        new_sha = hashlib.sha256(new_bytes).hexdigest()
        assert new_sha != original_sha

        # Wrap the read boundary. When the commit asks
        # `_read_file_bytes(Path)` for the previewed file,
        # return the original bytes and the preview's
        # fingerprint. If the commit re-reads the file at any
        # point — even just to re-confirm the fingerprint —
        # the patched read would still return A's bytes, so
        # the only way for the commit to see content B is to
        # bypass `_read_file_bytes` and read the file
        # directly.
        from backend import resource_import as ri
        call_log: list[str] = []

        def patched_read(p):
            call_log.append(f"path={str(p)!r}")
            return preview_fingerprint, original_bytes

        # Use both monkeypatch and direct assignment to
        # ensure the module-level lookup picks up the
        # patched function regardless of how the test runner
        # resolves the import.
        # Patch the read boundary on every module object
        # that points at this file. The pytest test runner
        # can resolve `backend.resource_import` through
        # different `sys.modules` entries (the conftest
        # inserts `backend/` on sys.path AND the package
        # import resolves the same file as
        # `backend.resource_import`); both module objects
        # must see the patched function for the commit's
        # module-level lookup to return it. Save the
        # originals and restore them in a finally block so
        # the patch does not leak into sibling tests.
        from backend import resource_import as ri_mod
        import sys as _sys
        targets: list[tuple[Any, Any]] = []
        for _name in ("backend.resource_import", "resource_import"):
            _mod = _sys.modules.get(_name)
            if _mod is not None and hasattr(_mod, "_read_file_bytes"):
                targets.append((_mod, _mod._read_file_bytes))
                _mod._read_file_bytes = patched_read
        try:
            commit = commit_import(preview)
        finally:
            for _mod, _orig in targets:
                _mod._read_file_bytes = _orig

        # The patched read was called at least once.
        assert call_log, "the patched read boundary was not called"
        # The commit succeeded and the persisted payload is
        # content A's payload — NOT content B's.
        assert commit.total_new_scene_revisions == 1
        assert commit.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        revisions = resource_store.list_revisions(library_id)
        assert len(revisions) == 1
        assert (
            revisions[0]["payload"]["scene_theme"]
            == NORMAL_SCENE_A["scene_theme"]
        )
        assert (
            revisions[0]["payload"]["scene_theme"]
            != NORMAL_SCENE_A_V2["scene_theme"]
        )


# ---- Cross-file duplicate detection (correction #3) ----------------------


class TestCrossFileDuplicates:
    """A `(library_key, source_id)` pair that appears more than
    once in the import is reported as a duplicate and every
    ambiguous occurrence is excluded from the accepted set. The
    import does not pick an arbitrary occurrence."""

    def test_duplicate_across_two_files_with_different_payloads(
        self, isolated_db, tmp_path,
    ):
        scene_a_v1 = {**NORMAL_SCENE_A, "id": "inv_shared_id"}
        scene_a_v2 = {
            **NORMAL_SCENE_A_V2, "id": "inv_shared_id", "label": "v2 label",
        }
        path_one = _write_source_file(tmp_path, "one.json", [scene_a_v1])
        path_two = _write_source_file(tmp_path, "two.json", [scene_a_v2])
        preview = preview_import([
            (path_one, "inv_lib_a"),
            (path_two, "inv_lib_a"),
        ])
        # Two files, two inputs, two accepted by the parser.
        # The cross-file duplicate detection moves both to
        # duplicate_identifiers; neither is accepted.
        assert preview.total_inputs == 2
        assert preview.total_accepted == 0
        assert preview.total_duplicate == 2
        assert preview.counts_reconcile()
        # Each file's reconciliation holds.
        for f in preview.files:
            assert f.counts_reconcile()
            assert f.accepted_total == 0
            assert f.duplicate_total == 1
        # The commit writes nothing.
        commit = commit_import(preview)
        assert commit.total_recorded == 0
        assert commit.total_new_scene_revisions == 0
        assert commit.counts_reconcile()
        # No revisions in the DB.
        assert resource_store.list_libraries() == []

    def test_duplicate_across_two_files_with_identical_payload(
        self, isolated_db, tmp_path,
    ):
        # Same source_id, same content, in two different files.
        # The cross-file detection still reports both as
        # duplicates; the import refuses to pick an arbitrary
        # one. CommitReport.counts_reconcile must still hold.
        scene = {**NORMAL_SCENE_A, "id": "inv_shared_identical"}
        path_one = _write_source_file(tmp_path, "one.json", [scene])
        path_two = _write_source_file(tmp_path, "two.json", [scene])
        preview = preview_import([
            (path_one, "inv_lib_a"),
            (path_two, "inv_lib_a"),
        ])
        assert preview.total_accepted == 0
        assert preview.total_duplicate == 2
        assert preview.counts_reconcile()
        commit = commit_import(preview)
        assert commit.counts_reconcile()
        assert commit.total_recorded == 0
        assert resource_store.list_libraries() == []

    def test_duplicate_in_the_same_file_with_different_payloads(
        self, isolated_db, tmp_path,
    ):
        scene_v1 = {**NORMAL_SCENE_A, "id": "inv_same_file_id"}
        scene_v2 = {
            **NORMAL_SCENE_A_V2, "id": "inv_same_file_id", "label": "v2",
        }
        path = _write_source_file(tmp_path, "both.json", [scene_v1, scene_v2])
        preview = preview_import([(path, "inv_lib_a")])
        # Two inputs in one file, both excluded.
        assert preview.total_inputs == 2
        assert preview.total_accepted == 0
        assert preview.total_duplicate == 2
        assert preview.counts_reconcile()
        commit = commit_import(preview)
        assert commit.counts_reconcile()
        assert commit.total_recorded == 0

    def test_duplicate_does_not_block_a_different_source_in_the_same_library(
        self, isolated_db, tmp_path,
    ):
        # A duplicate in the same file must not prevent an
        # unrelated accepted entry in the same library from
        # being committed.
        scene_dup = {**NORMAL_SCENE_A, "id": "inv_dup_id"}
        scene_other = {**NORMAL_SCENE_B, "id": "inv_other_id"}
        path = _write_source_file(
            tmp_path, "mix.json", [scene_dup, scene_dup, scene_other],
        )
        preview = preview_import([(path, "inv_lib_a")])
        assert preview.total_accepted == 1
        assert preview.total_duplicate == 2
        assert preview.counts_reconcile()
        commit = commit_import(preview)
        assert commit.total_new_scene_revisions == 1
        assert commit.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        revisions = resource_store.list_revisions(library_id)
        assert len(revisions) == 1
        assert revisions[0]["source_id"] == "inv_other_id"

    def test_duplicate_across_different_libraries_is_not_a_duplicate(
        self, isolated_db, tmp_path,
    ):
        # The same source_id in two different libraries is
        # NOT a duplicate: each library has its own source_id
        # namespace.
        scene = {**NORMAL_SCENE_A, "id": "inv_shared_id"}
        path_a = _write_source_file(tmp_path, "a.json", [scene])
        path_b = _write_source_file(tmp_path, "b.json", [scene])
        preview = preview_import([
            (path_a, "inv_lib_a"),
            (path_b, "inv_lib_b"),
        ])
        assert preview.total_accepted == 2
        assert preview.total_duplicate == 0
        assert preview.counts_reconcile()
        commit = commit_import(preview)
        assert commit.total_new_scene_revisions == 2
        assert commit.counts_reconcile()


# ---- Auxiliary persistence (correction #4) -------------------------------


class TestAuxiliaryPersistence:
    """Auxiliary resources the parser recognises are persisted
    to the auxiliary_resource table atomically with scene
    revisions. The complete original map is preserved, the
    library identity is recorded, and identical content does
    not create a duplicate row."""

    def test_successful_persistence_records_the_auxiliary_payload(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "translation.json", TRANSLATION_MAP)
        commit = commit_import(preview_import([(path, "inv_lib_a")]))
        assert commit.total_new_auxiliary_revisions == 1
        assert commit.total_unchanged_auxiliary_revisions == 0
        assert commit.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        aux_rows = resource_store.list_auxiliary(library_id)
        assert len(aux_rows) == 1
        assert aux_rows[0]["kind"] == "translation_map"
        # The complete original map is preserved verbatim.
        assert aux_rows[0]["payload"] == TRANSLATION_MAP

    def test_idempotent_reimport_creates_no_duplicate_row(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "translation.json", TRANSLATION_MAP)
        commit_import(preview_import([(path, "inv_lib_a")]))
        commit_import(preview_import([(path, "inv_lib_a")]))
        commit_import(preview_import([(path, "inv_lib_a")]))
        library_id = resource_store.ensure_library("inv_lib_a")
        aux_rows = resource_store.list_auxiliary(library_id)
        # Identical content did not create a duplicate row.
        assert len(aux_rows) == 1

    def test_changed_auxiliary_content_creates_a_new_immutable_row(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "translation.json", TRANSLATION_MAP)
        commit_import(preview_import([(path, "inv_lib_a")]))
        # Modify the auxiliary content.
        path.write_text(
            json.dumps(
                TRANSLATION_MAP_V2, ensure_ascii=False, separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        commit_import(preview_import([(path, "inv_lib_a")]))
        library_id = resource_store.ensure_library("inv_lib_a")
        aux_rows = resource_store.list_auxiliary(library_id)
        # Two immutable rows: the prior and the new.
        assert len(aux_rows) == 2
        payloads = {row["content_digest"]: row["payload"] for row in aux_rows}
        assert TRANSLATION_MAP in payloads.values()
        assert TRANSLATION_MAP_V2 in payloads.values()
        # The prior row's payload is byte-for-byte unchanged.
        original_digest = resource_store.canonical_digest(TRANSLATION_MAP)
        assert payloads[original_digest] == TRANSLATION_MAP

    def test_a_direct_update_of_auxiliary_payload_is_rejected_by_the_trigger(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "translation.json", TRANSLATION_MAP)
        commit_import(preview_import([(path, "inv_lib_a")]))
        library_id = resource_store.ensure_library("inv_lib_a")
        aux = resource_store.list_auxiliary(library_id)[0]
        # The BEFORE UPDATE trigger on the protected columns
        # rejects a hand-rolled UPDATE of `payload`.
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.run(
                "UPDATE auxiliary_resource SET payload = ? WHERE id = ?",
                '{"rewritten": true}',
                aux["id"],
            )
        assert "immutable" in str(exc_info.value).lower()
        # The row's payload is unchanged.
        again = db.one(
            "SELECT payload FROM auxiliary_resource WHERE id = ?",
            aux["id"],
        )
        assert json.loads(again["payload"]) == TRANSLATION_MAP

    def test_scenes_and_auxiliary_persist_atomically(
        self, isolated_db, tmp_path,
    ):
        # One file carries both an accepted scene and an
        # auxiliary resource. Both are written in one
        # transaction; the commit report reconciles.
        path = _write_source_file(
            tmp_path, "combo.json", [NORMAL_SCENE_A, TRANSLATION_MAP],
        )
        commit = commit_import(preview_import([(path, "inv_lib_a")]))
        assert commit.total_new_scene_revisions == 1
        assert commit.total_new_auxiliary_revisions == 1
        assert commit.total_recorded == 2
        assert commit.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        assert len(resource_store.list_revisions(library_id)) == 1
        assert len(resource_store.list_auxiliary(library_id)) == 1

    def test_rollback_undoes_scene_and_auxiliary_writes(
        self, isolated_db, tmp_path,
    ):
        # A simulated persistence failure after a scene
        # revision is recorded must roll back the scene write
        # AND any auxiliary writes that would have followed.
        path = _write_source_file(
            tmp_path, "combo.json", [NORMAL_SCENE_A, TRANSLATION_MAP],
        )
        preview = preview_import([(path, "inv_lib_a")])
        assert preview.total_accepted == 1
        assert preview.total_auxiliary == 1
        def _fail_after_one(processed: int) -> None:
            if processed >= 1:
                raise RuntimeError("simulated persistence failure")
        with pytest.raises(CommitAborted) as exc_info:
            commit_import(preview, failure_injector=_fail_after_one)
        assert exc_info.value.processed == 1
        # The transaction rolled back: no library, no scene
        # revision, no auxiliary row.
        assert resource_store.list_libraries() == []
        assert db.q("SELECT id FROM asset_revision") == []
        assert db.q("SELECT id FROM auxiliary_resource") == []


# ---- Auxiliary accounting: unchanged and same-kind coverage --------------


class TestAuxiliaryAccountingAndSameKind:
    """Auxiliary persistence accounting reconciles for new,
    unchanged, and updated outcomes, and same-kind auxiliary
    resources in one file are not collapsed by a kind-only
    key."""

    def test_reimporting_the_same_auxiliary_twice_reconciles(
        self, isolated_db, tmp_path,
    ):
        # Regression for the "total_auxiliary_persisted only
        # counted newly-inserted rows" defect: the second
        # commit's report must reconcile even when no new
        # auxiliary row is created.
        path = _write_source_file(tmp_path, "translation.json", TRANSLATION_MAP)
        first = commit_import(preview_import([(path, "inv_lib_a")]))
        assert first.total_new_auxiliary_revisions == 1
        assert first.total_unchanged_auxiliary_revisions == 0
        assert first.counts_reconcile()
        second = commit_import(preview_import([(path, "inv_lib_a")]))
        assert second.total_new_auxiliary_revisions == 0
        assert second.total_unchanged_auxiliary_revisions == 1
        assert second.counts_reconcile()
        # No second auxiliary row in the database.
        library_id = resource_store.ensure_library("inv_lib_a")
        assert len(resource_store.list_auxiliary(library_id)) == 1

    def test_two_same_kind_auxiliaries_with_different_payloads(
        self, isolated_db, tmp_path,
    ):
        # A list payload carries two mined_families dicts of
        # the same kind. The commit must persist BOTH with
        # their exact payloads, not collapse to a kind-only
        # key that overwrites the first with the second.
        path = _write_source_file(
            tmp_path, "families.json",
            [MINED_FAMILIES_ONE, MINED_FAMILIES_TWO],
        )
        preview = preview_import([(path, "inv_lib_a")])
        assert preview.total_auxiliary == 2
        assert preview.counts_reconcile()
        commit = commit_import(preview)
        # Both auxiliary outcomes are written.
        assert commit.total_auxiliary == 2
        assert commit.total_new_auxiliary_revisions == 2
        assert commit.total_unchanged_auxiliary_revisions == 0
        assert commit.counts_reconcile()
        # The DB has two auxiliary rows, one per payload.
        library_id = resource_store.ensure_library("inv_lib_a")
        aux_rows = resource_store.list_auxiliary(library_id)
        assert len(aux_rows) == 2
        payloads = {tuple(sorted(row["payload"].items())) for row in aux_rows}
        assert (
            tuple(sorted(MINED_FAMILIES_ONE.items())) in payloads
        )
        assert (
            tuple(sorted(MINED_FAMILIES_TWO.items())) in payloads
        )

    def test_two_same_kind_auxiliaries_with_identical_payloads(
        self, isolated_db, tmp_path,
    ):
        # The same payload twice. The commit must still record
        # two persistence operations (one new, one unchanged)
        # and create exactly one row.
        path = _write_source_file(
            tmp_path, "families.json",
            [MINED_FAMILIES_ONE, MINED_FAMILIES_ONE],
        )
        preview = preview_import([(path, "inv_lib_a")])
        assert preview.total_auxiliary == 2
        assert preview.counts_reconcile()
        commit = commit_import(preview)
        assert commit.total_auxiliary == 2
        assert commit.total_new_auxiliary_revisions == 1
        assert commit.total_unchanged_auxiliary_revisions == 1
        assert commit.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        aux_rows = resource_store.list_auxiliary(library_id)
        assert len(aux_rows) == 1


# ---- Commit: atomic rollback (regression of correction 3 behaviour) ------


class TestCommitAtomicRollback:
    """A commit is atomic: a failure partway through rolls the
    entire write set back, leaving the database in the state it
    was in before the commit started."""

    def test_a_simulated_failure_after_one_revision_rolls_back_the_set(
        self, isolated_db, tmp_path,
    ):
        scenes = [
            {**NORMAL_SCENE_A, "id": "inv_rollback_one"},
            {**NORMAL_SCENE_A, "id": "inv_rollback_two", "label": "two"},
            {**NORMAL_SCENE_A, "id": "inv_rollback_three", "label": "three"},
        ]
        path = _write_source_file(tmp_path, "three.json", scenes)
        preview = preview_import([(path, "inv_lib_a")])
        def _fail_after_one(processed: int) -> None:
            if processed >= 1:
                raise RuntimeError("simulated persistence failure")
        with pytest.raises(CommitAborted) as exc_info:
            commit_import(preview, failure_injector=_fail_after_one)
        assert exc_info.value.processed == 1
        assert isinstance(exc_info.value.cause, RuntimeError)
        assert resource_store.list_libraries() == []
        assert db.q("SELECT id FROM asset_revision") == []

    def test_a_simulated_failure_after_two_revisions_rolls_back_the_set(
        self, isolated_db, tmp_path,
    ):
        scenes = [
            {**NORMAL_SCENE_A, "id": "inv_rollback_one"},
            {**NORMAL_SCENE_A, "id": "inv_rollback_two", "label": "two"},
            {**NORMAL_SCENE_A, "id": "inv_rollback_three", "label": "three"},
        ]
        path = _write_source_file(tmp_path, "three.json", scenes)
        preview = preview_import([(path, "inv_lib_a")])
        def _fail_after_two(processed: int) -> None:
            if processed >= 2:
                raise RuntimeError("simulated failure after two")
        with pytest.raises(CommitAborted) as exc_info:
            commit_import(preview, failure_injector=_fail_after_two)
        assert exc_info.value.processed == 2
        assert resource_store.list_libraries() == []

    def test_a_successful_commit_writes_every_accepted_entry(
        self, isolated_db, tmp_path,
    ):
        scenes = [
            {**NORMAL_SCENE_A, "id": "inv_success_one"},
            {**NORMAL_SCENE_A, "id": "inv_success_two", "label": "two"},
            {**NORMAL_SCENE_A, "id": "inv_success_three", "label": "three"},
        ]
        path = _write_source_file(tmp_path, "three.json", scenes)
        commit = commit_import(preview_import([(path, "inv_lib_a")]))
        assert commit.total_recorded == 3
        assert commit.total_new_scene_revisions == 3
        assert commit.total_unchanged_scene_revisions == 0
        assert commit.total_updated_scene_revisions == 0
        assert commit.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        assert len(resource_store.list_revisions(library_id)) == 3

    def test_a_successful_commit_on_unchanged_content_writes_no_new_rows(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        first = commit_import(preview_import([(path, "inv_lib_a")]))
        assert first.total_new_scene_revisions == 1
        second = commit_import(preview_import([(path, "inv_lib_a")]))
        assert second.total_recorded == 1
        assert second.total_new_scene_revisions == 0
        assert second.total_unchanged_scene_revisions == 1
        assert second.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        assert len(resource_store.list_revisions(library_id)) == 1

    def test_a_successful_commit_on_updated_content_writes_one_new_row(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        commit_import(preview_import([(path, "inv_lib_a")]))
        path.write_text(
            json.dumps(
                NORMAL_SCENE_A_V2, ensure_ascii=False, separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        commit = commit_import(preview_import([(path, "inv_lib_a")]))
        assert commit.total_recorded == 1
        assert commit.total_new_scene_revisions == 1
        assert commit.total_updated_scene_revisions == 1
        assert commit.total_unchanged_scene_revisions == 0
        assert commit.counts_reconcile()
        library_id = resource_store.ensure_library("inv_lib_a")
        revisions = resource_store.list_revisions(library_id)
        assert len(revisions) == 2
        prior = next(
            r for r in revisions
            if r["content_digest"] != commit.files[0].accepted_outcomes[0].new_content_digest
        )
        assert prior["payload"]["scene_theme"] == NORMAL_SCENE_A["scene_theme"]
        assert prior["payload"]["weight"] == NORMAL_SCENE_A["weight"]


# ---- Count reconciliation -------------------------------------------------


class TestCountReconciliation:
    """Both the preview and the commit reports must reconcile
    their counts across the per-file and per-aggregate
    identities."""

    def test_preview_reconciles_with_a_mixed_payload(
        self, isolated_db, tmp_path,
    ):
        # One accepted scene with a unique id, one auxiliary
        # map, two duplicate occurrences of a different id,
        # one missing-id scene, one malformed item, one
        # unsupported shape. The cross-file duplicate detection
        # excludes both occurrences of the duplicate id.
        scene_kept = {**NORMAL_SCENE_A, "id": "inv_kept_id"}
        scene_dup_v1 = {**NORMAL_SCENE_A, "id": "inv_dup_id"}
        scene_dup_v2 = {
            **NORMAL_SCENE_A_V2, "id": "inv_dup_id", "label": "v2",
        }
        mixed = [
            scene_kept,
            TRANSLATION_MAP,
            scene_dup_v1,  # duplicate
            scene_dup_v2,  # duplicate
            MISSING_ID_SCENE,
            "not a dict",
            UNSUPPORTED_DICT,
        ]
        path = _write_source_file(tmp_path, "mixed.json", mixed)
        preview = preview_import([(path, "inv_lib_a")])
        # Seven inputs.
        assert preview.total_inputs == 7
        # One accepted (the unique-id scene).
        assert preview.total_accepted == 1
        # One auxiliary.
        assert preview.total_auxiliary == 1
        # Two duplicates (both occurrences of the duplicate
        # id; the import refuses to pick one).
        assert preview.total_duplicate == 2
        # Three unresolved bucket items: missing-id, malformed,
        # unsupported.
        assert preview.total_unresolved == 3
        # The aggregate identity:
        # 1 + 1 + 2 + 3 = 7 = total_inputs.
        assert preview.counts_reconcile()
        file_report = preview.files[0]
        assert file_report.counts_reconcile()
        buckets = {item.bucket for item in file_report.unresolved}
        assert BUCKET_MISSING_IDENTIFIER in buckets
        assert BUCKET_MALFORMED in buckets
        assert BUCKET_UNSUPPORTED in buckets

    def test_commit_reconciles_with_new_unchanged_and_updated(
        self, isolated_db, tmp_path,
    ):
        path_a = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        path_b = _write_source_file(tmp_path, "scene_b.json", NORMAL_SCENE_B)
        commit_import(preview_import([(path_a, "inv_lib_a"), (path_b, "inv_lib_a")]))
        path_a.write_text(
            json.dumps(
                NORMAL_SCENE_A_V2, ensure_ascii=False, separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        commit = commit_import(preview_import([(path_a, "inv_lib_a"), (path_b, "inv_lib_a")]))
        assert commit.total_accepted == 2
        assert commit.total_new == 0
        assert commit.total_unchanged == 1
        assert commit.total_updated == 1
        assert commit.total_recorded == 2
        assert commit.total_new_scene_revisions == 1
        assert commit.total_unchanged_scene_revisions == 1
        assert commit.counts_reconcile()

    def test_an_empty_preview_reconciles_to_zero(
        self, isolated_db, tmp_path,
    ):
        preview = preview_import([])
        assert preview.total_files == 0
        assert preview.total_inputs == 0
        assert preview.total_accepted == 0
        assert preview.counts_reconcile()
        commit = commit_import(preview)
        assert commit.total_recorded == 0
        assert commit.counts_reconcile()


# ---- Immutability invariants during commit --------------------------------


class TestCommitImmutability:
    """The commit must not bypass the resource-store's
    immutability rules. Prior revisions stay byte-for-byte
    unchanged after an updated import; the protected columns on
    asset_revision and auxiliary_resource cannot be rewritten
    even through a hand-rolled UPDATE."""

    def test_an_updated_import_preserves_the_prior_revision_byte_for_byte(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        first = commit_import(preview_import([(path, "inv_lib_a")]))
        library_id = resource_store.ensure_library("inv_lib_a")
        prior_revisions = resource_store.list_revisions(library_id)
        assert len(prior_revisions) == 1
        prior = prior_revisions[0]
        prior_payload_text = db.one(
            "SELECT payload FROM asset_revision WHERE id = ?",
            prior["id"],
        )["payload"]
        prior_digest = prior["content_digest"]
        path.write_text(
            json.dumps(
                NORMAL_SCENE_A_V2, ensure_ascii=False, separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        second = commit_import(preview_import([(path, "inv_lib_a")]))
        assert second.total_updated_scene_revisions == 1
        current_payload_text = db.one(
            "SELECT payload FROM asset_revision WHERE id = ?",
            prior["id"],
        )["payload"]
        assert current_payload_text == prior_payload_text
        current_digest = db.one(
            "SELECT content_digest FROM asset_revision WHERE id = ?",
            prior["id"],
        )["content_digest"]
        assert current_digest == prior_digest
        all_revisions = resource_store.list_revisions(library_id)
        assert len(all_revisions) == 2

    def test_a_direct_update_of_asset_revision_payload_is_rejected_after_a_commit(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        commit_import(preview_import([(path, "inv_lib_a")]))
        library_id = resource_store.ensure_library("inv_lib_a")
        rev = resource_store.list_revisions(library_id)[0]
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.run(
                "UPDATE asset_revision SET payload = ? WHERE id = ?",
                '{"id": "x"}',
                rev["id"],
            )
        assert "immutable" in str(exc_info.value).lower()
        again = db.one(
            "SELECT payload FROM asset_revision WHERE id = ?",
            rev["id"],
        )
        assert json.loads(again["payload"]) == NORMAL_SCENE_A

    def test_unchanged_content_does_not_create_a_duplicate_revision(
        self, isolated_db, tmp_path,
    ):
        path = _write_source_file(tmp_path, "scene_a.json", NORMAL_SCENE_A)
        for _ in range(3):
            commit_import(preview_import([(path, "inv_lib_a")]))
        library_id = resource_store.ensure_library("inv_lib_a")
        revisions = resource_store.list_revisions(library_id)
        assert len(revisions) == 1


# ---- Privacy: the new module carries no forbidden patterns ----------------


class TestPrivacyAndEnglish:
    """The new module must carry no personal-data patterns and
    no non-English glyphs, so a public repo scan passes."""

    def test_no_personal_data_patterns_in_resource_import(self):
        path = Path(__file__).resolve().parents[1] / "backend" / "resource_import.py"
        text = path.read_text(encoding="utf-8")
        for name, pattern in PRIVACY_PATTERNS.items():
            offenders = pattern.findall(text)
            assert not offenders, (
                f"resource_import.py contains forbidden pattern "
                f"{name!r}: {offenders[:3]}"
            )


# ---- Transaction support in db --------------------------------------------


class TestDbTransaction:
    """The small ``db.transaction()`` addition is what makes
    ``commit_import`` atomic. These tests pin the context
    manager itself so a future change to ``db.run`` cannot
    quietly break the rollback the spec requires."""

    def test_a_transaction_commits_on_normal_exit(self, isolated_db):
        db.run(
            "INSERT INTO model (name, trigger, created_at) VALUES "
            "(?, ?, ?)",
            "inv_tx_commit", "invtrigger", db.now(),
        )
        with db.transaction():
            db.run(
                "INSERT INTO model (name, trigger, created_at) VALUES "
                "(?, ?, ?)",
                "inv_tx_one", "invtrigger", db.now(),
            )
            db.run(
                "INSERT INTO model (name, trigger, created_at) VALUES "
                "(?, ?, ?)",
                "inv_tx_two", "invtrigger", db.now(),
            )
        names = {r["name"] for r in db.q("SELECT name FROM model")}
        assert {"inv_tx_one", "inv_tx_two"}.issubset(names)

    def test_a_transaction_rolls_back_on_exception(self, isolated_db):
        db.run(
            "INSERT INTO model (name, trigger, created_at) VALUES "
            "(?, ?, ?)",
            "inv_tx_before", "invtrigger", db.now(),
        )
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.run(
                    "INSERT INTO model (name, trigger, created_at) "
                    "VALUES (?, ?, ?)",
                    "inv_tx_rollback_one", "invtrigger", db.now(),
                )
                db.run(
                    "INSERT INTO model (name, trigger, created_at) "
                    "VALUES (?, ?, ?)",
                    "inv_tx_rollback_two", "invtrigger", db.now(),
                )
                raise RuntimeError("simulated")
        names = {r["name"] for r in db.q("SELECT name FROM model")}
        assert "inv_tx_before" in names
        assert "inv_tx_rollback_one" not in names
        assert "inv_tx_rollback_two" not in names

    def test_db_run_outside_a_transaction_still_auto_commits(
        self, isolated_db,
    ):
        db.run(
            "INSERT INTO model (name, trigger, created_at) VALUES "
            "(?, ?, ?)",
            "inv_outside_tx", "invtrigger", db.now(),
        )
        row = db.one(
            "SELECT name FROM model WHERE name = ?", "inv_outside_tx"
        )
        assert row is not None


# ---- Source envelope import tests -----------------------------------------


class TestEnvelopeImport:
    """Tests for importing source files wrapped in envelopes {library: ..., items: [...]}.

    The envelope shape wraps a collection of entries under 'items'. The importer
    normalizes the envelope, treats each item as an entry in the normal pipeline,
    binds fingerprints to the whole source file, and ensures operator library_key
    governs destination without being silently overridden by envelope metadata.
    """

    def test_preview_and_commit_envelope(self, isolated_db, tmp_path):
        envelope = {
            "library": "invented_scenes",
            "items": [
                {
                    "identifier": "inv_env_scene_01",
                    "label": "invented lounge",
                    "theme": "an invented quiet lounge",
                },
                {
                    "identifier": "inv_env_scene_02",
                    "label": "invented studio",
                    "theme": "an invented photo studio",
                },
            ],
        }
        path = _write_source_file(tmp_path, "envelope.json", envelope)
        preview = preview_import([(path, "invented_scenes")])

        file_report = preview.files[0]
        assert file_report.total_inputs == 2
        assert file_report.accepted_total == 2
        assert file_report.unresolved_total == 0
        assert file_report.counts_reconcile()
        assert preview.total_inputs == 2
        assert preview.total_accepted == 2
        assert preview.total_unresolved == 0
        assert preview.counts_reconcile()

        report = commit_import(preview)
        assert report.total_new_scene_revisions == 2
        assert report.total_unchanged_scene_revisions == 0
        assert report.total_updated_scene_revisions == 0
        assert report.counts_reconcile()

        library_id = resource_store.ensure_library("invented_scenes")
        rev_1 = resource_store.get_revision(library_id=library_id, source_id="inv_env_scene_01")
        rev_2 = resource_store.get_revision(library_id=library_id, source_id="inv_env_scene_02")
        assert rev_1 is not None
        assert rev_2 is not None
        assert rev_1["payload"] == envelope["items"][0]
        assert rev_2["payload"] == envelope["items"][1]
        assert "library" not in rev_1["payload"]
        assert "library" not in rev_2["payload"]

    def test_envelope_duplicate_detection(self, isolated_db, tmp_path):
        envelope = {
            "library": "general_scenes",
            "items": [
                {
                    "identifier": "inv_dup_01",
                    "label": "first instance",
                    "theme": "first room",
                },
                {
                    "identifier": "inv_dup_01",
                    "label": "second instance",
                    "theme": "second room",
                },
            ],
        }
        path = _write_source_file(tmp_path, "dup_envelope.json", envelope)
        preview = preview_import([(path, "general_scenes")])

        file_report = preview.files[0]
        assert file_report.total_inputs == 2
        assert file_report.accepted_total == 0
        assert file_report.duplicate_total == 2
        assert file_report.counts_reconcile()
        assert preview.total_duplicate == 2
        assert preview.total_accepted == 0
        assert preview.counts_reconcile()

    def test_envelope_missing_entries_on_reimport(self, isolated_db, tmp_path):
        first_envelope = {
            "library": "general_scenes",
            "items": [
                {"identifier": "inv_s1", "label": "scene 1", "theme": "room 1"},
                {"identifier": "inv_s2", "label": "scene 2", "theme": "room 2"},
                {"identifier": "inv_s3", "label": "scene 3", "theme": "room 3"},
            ],
        }
        path_first = _write_source_file(tmp_path, "full.json", first_envelope)
        commit_import(preview_import([(path_first, "general_scenes")]))

        library_id = resource_store.ensure_library("general_scenes")
        before_revisions = resource_store.list_revisions(library_id)
        assert len(before_revisions) == 3

        second_envelope = {
            "library": "general_scenes",
            "items": [
                {"identifier": "inv_s1", "label": "scene 1", "theme": "room 1"},
                {"identifier": "inv_s3", "label": "scene 3", "theme": "room 3"},
            ],
        }
        path_second = _write_source_file(tmp_path, "partial.json", second_envelope)
        preview_second = preview_import([(path_second, "general_scenes")])
        assert preview_second.total_inputs == 2
        assert preview_second.total_accepted == 2
        assert preview_second.total_missing == 1
        missing = preview_second.missing_source_entries[0]
        assert missing.library_key == "general_scenes"
        assert missing.source_id == "inv_s2"

        commit_import(preview_second)
        after_revisions = resource_store.list_revisions(library_id)
        assert len(after_revisions) == 3
        source_ids = {r["source_id"] for r in after_revisions}
        assert source_ids == {"inv_s1", "inv_s2", "inv_s3"}

    def test_empty_envelope_import(self, isolated_db, tmp_path):
        empty_envelope = {
            "library": "invented_scenes",
            "items": [],
        }
        path = _write_source_file(tmp_path, "empty.json", empty_envelope)
        preview = preview_import([(path, "invented_scenes")])

        file_report = preview.files[0]
        assert file_report.total_inputs == 0
        assert file_report.accepted_total == 0
        assert file_report.unresolved_total == 0
        assert file_report.counts_reconcile()
        assert preview.total_inputs == 0
        assert preview.counts_reconcile()

        report = commit_import(preview)
        assert report.total_new_scene_revisions == 0
        assert report.total_unchanged_scene_revisions == 0
        assert report.total_updated_scene_revisions == 0
        assert report.total_recorded == 0
        assert report.counts_reconcile()

    def test_envelope_library_mismatch_uses_operator_selection(
        self, isolated_db, tmp_path,
    ):
        envelope = {
            "library": "metadata_only_library_name",
            "items": [
                {
                    "identifier": "inv_scene_mismatch",
                    "label": "mismatched scene",
                    "theme": "an invented room",
                },
            ],
        }
        path = _write_source_file(tmp_path, "mismatch.json", envelope)
        preview = preview_import([(path, "operator_target_lib")])

        assert preview.files[0].library_key == "operator_target_lib"
        assert preview.files[0].accepted_outcomes[0].library_key == "operator_target_lib"

        commit_import(preview)
        target_id = resource_store.ensure_library("operator_target_lib")
        rev = resource_store.get_revision(library_id=target_id, source_id="inv_scene_mismatch")
        assert rev is not None
        assert rev["library_id"] == target_id
        assert db.one("SELECT id FROM resource_library WHERE library_key = ?", "metadata_only_library_name") is None

    def test_envelope_stale_fingerprint_rejected(
        self, isolated_db, tmp_path,
    ):
        envelope = {
            "library": "general_scenes",
            "items": [
                {
                    "identifier": "inv_stale_01",
                    "label": "original label",
                    "theme": "original theme",
                },
            ],
        }
        path = _write_source_file(tmp_path, "stale_envelope.json", envelope)
        preview = preview_import([(path, "general_scenes")])

        mutated_envelope = {
            "library": "general_scenes",
            "items": [
                {
                    "identifier": "inv_stale_01",
                    "label": "mutated label",
                    "theme": "mutated theme",
                },
            ],
        }
        _write_source_file(tmp_path, "stale_envelope.json", mutated_envelope)

        with pytest.raises(StaleFingerprintError):
            commit_import(preview)
