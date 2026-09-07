"""Tests for the resource-store persistence layer (task 2.1 of
`adopt-resource-session-planning`).

The migration that creates the underlying tables is in
``backend.db.SCHEMA``; this file exercises the persistence layer
``backend.resource_store`` exposes on top of that schema. Every test
opens a fresh, isolated database under ``tmp_path`` so a test cannot
see another test's rows; the `client`-style global fixture is not used
because its lifetime is the whole pytest session and would defeat the
isolation this task requires.

The scenarios pinned here are the ones the task description names:

  * a full nested payload round-trips exactly;
  * translation and coverage data remain separate from the original
    payload;
  * identical (library, source_id, content) does not create a duplicate
    revision;
  * changed content for the same library and source entry creates a
    distinct immutable revision while the prior revision remains
    readable and unchanged;
  * schema initialisation and migration can run repeatedly against the
    same isolated database;
  * legacy schema and data required by existing tests remain
    unaffected.

The fixtures and the test data are English-only, invented prose; no
source-corpus text, no machine paths, no real names, no generated
images. The strings the tests write are deliberately chosen to
exercise round-tripping of nested objects, escapes, unicode-free but
non-ASCII-safe strings, lists, numbers, booleans, and ``None`` values.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

import db
import resource_store


# ---- Helpers --------------------------------------------------------------


def _open(path: Path) -> sqlite3.Connection:
    """Open a fresh, isolated database for one test."""
    db._conn = None  # noqa: SLF001 (reset the global between tests)
    return db.connect(path)


def _close_silently() -> None:
    """Close the current connection without raising.

    ``db.connect`` opens SQLite connections with ``check_same_thread=False``
    and stores them in ``db._conn``. A second ``db.connect`` for a
    different path overwrites the global; this helper just makes sure a
    WAL file the previous connection opened is released before the next
    test starts on Windows, where an open WAL file holds the DB.
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
    """Yield a fresh, isolated database, closing it on teardown.

    Each test gets a brand-new file under ``tmp_path``; the global
    ``db._conn`` is reset before and after, so no other test's rows
    leak in and this test's WAL file is released before the next one
    runs.
    """
    path = Path(tmp_path) / "resource-store.db"
    _open(path)
    try:
        yield path
    finally:
        _close_silently()


# The canonical privacy-regex set: a private fork of this file would
# silently drift from ``test_no_personal_data.py`` if it kept a copy,
# and the two sets disagreeing is the leak that scan is supposed to
# catch. Reuse it instead.
from test_no_personal_data import PATTERNS as PRIVACY_PATTERNS  # noqa: E402


# ---- Invented English-only fixtures --------------------------------------


# A deeply nested object whose every leaf is an English-only invented
# string. The choice of escapes, punctuation, and nested-list / nested-
# dict shapes is on purpose: the round-trip test asserts each one
# survives the database exactly.
NESTED_PAYLOAD = {
    "id": "inv_room_studio_dawn",
    "label": "invented studio at dawn",
    "scene_theme": (
        "A bare studio with a tall north-facing window. Soft grey light "
        "enters from the side and leaves the back wall in shadow. A "
        "single wooden chair stands between the subject and the camera, "
        "and a folded white sheet covers the floor."
    ),
    "tags": ["indoor", "studio", "morning"],
    "weight": 1.5,
    "enabled": True,
    "props": ["chair", "sheet", "window", "no lamp", "no studio light"],
    "nested": {
        "first": {
            "second": {
                "third": {
                    "leaf_string": "an invented leaf string",
                    "leaf_int": 42,
                    "leaf_float": 3.14159,
                    "leaf_bool": False,
                    "leaf_list": ["a", "b", "c", ""],
                    "leaf_dict": {
                        "edge": "escaped \"quote\" and \\backslash",
                        "newline": "line one\nline two",
                        "tab": "col1\tcol2",
                    },
                },
            },
        },
    },
    "empty_list": [],
    "empty_dict": {},
    "none_value": None,
}


NESTED_PAYLOAD_V2 = {
    **NESTED_PAYLOAD,
    "scene_theme": (
        "A bare studio with a tall north-facing window. A different "
        "wording of the same scene, the kind a refreshed source file "
        "carries, with the back wall now lit by a soft warm bounce."
    ),
    "weight": 1.7,
    "nested": {
        **NESTED_PAYLOAD["nested"],
        "first": {
            **NESTED_PAYLOAD["nested"]["first"],
            "second": {
                **NESTED_PAYLOAD["nested"]["first"]["second"],
                "third": {
                    **NESTED_PAYLOAD["nested"]["first"]["second"]["third"],
                    "leaf_string": "an invented leaf string, v2",
                },
            },
        },
    },
}


ENGLISH_TRANSLATION = {
    "label": "invented studio at dawn",
    "scene_theme": NESTED_PAYLOAD["scene_theme"],
    "field_notes": {
        "id": "kept as identifier, not translated",
        "weight": "kept as numeric selection, not translated",
    },
}


ENGLISH_COVERAGE = {
    "fields": {
        "id": {"role": "identity", "translated": True},
        "label": {"role": "descriptive_input", "translated": True},
        "scene_theme": {"role": "descriptive_input", "translated": True},
        "tags": {"role": "descriptive_input", "translated": True},
        "weight": {"role": "selection_metadata", "translated": False},
    },
    "missing_translations": [],
    "unmapped_fields": ["private_internal_flag"],
}


# ---- Tests ----------------------------------------------------------------


class TestMigrationAdditive:
    """The new tables are present, the legacy tables are untouched."""

    def test_the_new_tables_exist_after_connect_on_a_fresh_database(
        self, isolated_db,
    ):
        names = {
            r["name"]
            for r in db.q(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # The two new tables the migration adds.
        assert "resource_library" in names, sorted(names)
        assert "asset_revision" in names, sorted(names)
        # The legacy tables the migration MUST NOT touch.
        for legacy in (
            "workflow", "model", "session", "shot", "component", "cell",
            "reading", "garment", "outfit",
        ):
            assert legacy in names, f"legacy table {legacy!r} missing"

    def test_resource_library_schema_is_what_the_spec_requires(
        self, isolated_db,
    ):
        cols = {r["name"]: r for r in db.q("PRAGMA table_info(resource_library)")}
        assert set(cols) == {"id", "library_key", "display_name", "kind", "created_at"}, set(cols)
        # library_key is UNIQUE: the idempotency rule.
        idx = {r["name"] for r in db.q("PRAGMA index_list(resource_library)")}
        assert "sqlite_autoindex_resource_library_1" in idx, idx

    def test_asset_revision_schema_is_what_the_spec_requires(
        self, isolated_db,
    ):
        cols = {r["name"]: r for r in db.q("PRAGMA table_info(asset_revision)")}
        assert set(cols) == {
            "id", "library_id", "source_id", "content_digest",
            "payload", "translation", "coverage", "created_at",
        }, set(cols)
        # Translation and coverage default to '{}' so an empty revision
        # has well-defined neutral values. SQLite returns string
        # defaults wrapped in their original single quotes, so the
        # expected form is the quoted string "'{}'".
        assert cols["translation"]["dflt_value"] == "'{}'", cols["translation"]
        assert cols["coverage"]["dflt_value"] == "'{}'", cols["coverage"]
        # The composite uniqueness on (library_id, source_id, content_digest)
        # is what makes the no-duplicate-revision rule enforceable.
        row = db.one(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='asset_revision'"
        )
        assert "UNIQUE(library_id, source_id, content_digest)" in (row["sql"] or ""), row

    def test_legacy_table_columns_are_unchanged(self, isolated_db):
        # The legacy tables the rest of the suite reads and writes
        # must keep every column they had before the migration. The
        # expected set is hand-written (a parsed-from-SCHEMA
        # extractor is fragile to formatting) and the test is a
        # strict equality check: a column rename or a column drop
        # shows up here as a precise failure with the exact missing
        # or extra name.
        expected_columns: dict[str, set[str]] = {
            "workflow": {
                "id", "name", "graph", "node_map", "kind", "created_at",
            },
            "model": {
                "id", "name", "lora_name", "trigger", "lora_strength",
                "base_positive", "base_negative", "workflow_id", "settings",
                "notes", "created_at",
            },
            "session": {
                "id", "model_id", "name", "status", "workflow_id",
                "reference_workflow_id", "anchor_shot_ids", "look", "wardrobe",
                "settings", "tags", "manner", "checkpoint", "room_key",
                "origin", "created_at",
            },
            "shot": {
                "id", "session_id", "shot_index", "shot_label", "prompt",
                "negative", "use_reference", "mute_wardrobe",
                "reference_shot_ids", "reference_strength", "origin_shot_id",
                "seed", "status", "prompt_id", "filename", "rating",
                "components", "verdicts", "rejected", "error", "created_at",
                "finished_at",
            },
            "component": {
                "id", "concept_key", "slot", "manner", "family", "faces",
                "wording", "judge_label", "cameras", "needs", "retired_at",
                "created_at",
            },
            "cell": {
                "camera_wording", "act_wording", "framing_wording", "manner",
                "checkpoint", "judged", "arrived", "contradicted",
            },
            "reading": {
                "id", "slot", "manner", "session_id", "key", "label",
                "axis", "created_at",
            },
            "garment": {
                "id", "key", "wording", "aside", "retired_at", "created_at",
            },
            "outfit": {
                "id", "key", "label", "garments", "retired_at", "created_at",
            },
        }
        for table, want in expected_columns.items():
            got = {r["name"] for r in db.q(f"PRAGMA table_info({table})")}
            assert got == want, (table, sorted(want - got), sorted(got - want))


class TestMigrationIdempotent:
    """The migration can run repeatedly on the same isolated database."""

    def test_connect_twice_against_the_same_file_does_not_raise(
        self, tmp_path,
    ):
        path = Path(tmp_path) / "double-connect.db"
        _open(path)
        # Plant a row so a re-run also has to keep existing data.
        lib_id = resource_store.ensure_library(
            "inv_double_connect", display_name="first", kind="rooms",
        )
        rev_id = resource_store.record_revision(
            lib_id, "inv_double_connect_entry_01", {"id": "inv_double_connect_entry_01", "label": "one"},
        )
        # The re-open below must not raise, must not lose the planted
        # row, and must not duplicate the migration.
        _close_silently()
        _open(path)
        try:
            again = resource_store.get_revision(revision_id=rev_id)
            assert again is not None
            assert again["source_id"] == "inv_double_connect_entry_01"
            assert resource_store.ensure_library("inv_double_connect") == lib_id
        finally:
            _close_silently()

    def test_three_sequential_connects_keep_every_table(self, tmp_path):
        path = Path(tmp_path) / "triple-connect.db"
        for i in range(3):
            _open(path)
            try:
                names = {
                    r["name"]
                    for r in db.q(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                for table in (
                    "resource_library", "asset_revision", "workflow", "model",
                    "session", "shot", "component", "cell", "reading",
                    "garment", "outfit",
                ):
                    assert table in names, (i, table, sorted(names))
            finally:
                _close_silently()

    def test_migration_runs_on_a_database_that_predates_the_tables(
        self, tmp_path,
    ):
        # Open a database with the legacy schema only, drop the new
        # tables, plant a legacy row, close. Re-open: the migration
        # adds the new tables and the legacy row is intact.
        path = Path(tmp_path) / "old-resource.db"
        _open(path)
        # Plant a legacy row.
        db.run(
            "INSERT INTO model (name, trigger, created_at) VALUES "
            "('inv_legacy_model', 'invtrigger', ?)",
            db.now(),
        )
        # Drop the new tables, simulating a database written before
        # the migration.
        db.run("DROP TABLE asset_revision")
        db.run("DROP TABLE resource_library")
        _close_silently()

        _open(path)
        try:
            names = {
                r["name"]
                for r in db.q(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert "resource_library" in names, sorted(names)
            assert "asset_revision" in names, sorted(names)
            # Legacy row survives the migration.
            row = db.one("SELECT name FROM model WHERE name = 'inv_legacy_model'")
            assert row is not None and row["name"] == "inv_legacy_model"
        finally:
            _close_silently()


class TestNestedPayloadRoundTrip:
    """The full nested payload round-trips exactly through the database."""

    def test_a_full_nested_object_round_trips_byte_for_byte(
        self, isolated_db,
    ):
        lib_id = resource_store.ensure_library(
            "inv_nested_lib", display_name="invented nested", kind="rooms",
        )
        rev_id = resource_store.record_revision(
            lib_id, NESTED_PAYLOAD["id"], NESTED_PAYLOAD,
            translation=ENGLISH_TRANSLATION, coverage=ENGLISH_COVERAGE,
        )
        got = resource_store.get_revision(revision_id=rev_id)
        assert got is not None
        # Payload is a deep copy of the input, not a re-encoded blob.
        assert got["payload"] == NESTED_PAYLOAD, got["payload"]
        # The strings that are most likely to be silently mangled by a
        # naive encode are asserted individually so a regression shows
        # the exact line and the exact byte that drifted.
        third = got["payload"]["nested"]["first"]["second"]["third"]
        assert third["leaf_string"] == "an invented leaf string"
        assert third["leaf_int"] == 42
        assert third["leaf_float"] == 3.14159
        assert third["leaf_bool"] is False
        assert third["leaf_list"] == ["a", "b", "c", ""]
        assert third["leaf_dict"]["edge"] == "escaped \"quote\" and \\backslash"
        assert third["leaf_dict"]["newline"] == "line one\nline two"
        assert third["leaf_dict"]["tab"] == "col1\tcol2"
        # Empty containers are preserved as empty containers.
        assert got["payload"]["empty_list"] == []
        assert got["payload"]["empty_dict"] == {}
        # None is preserved as None, not silently dropped.
        assert "none_value" in got["payload"]
        assert got["payload"]["none_value"] is None

    def test_canonical_digest_is_stable_across_key_order(
        self, isolated_db,
    ):
        # Two dicts that compare equal as JSON values but differ in
        # key order must produce the same digest. The point of the
        # canonical form is to make the digest a property of the
        # VALUE, not the textual layout a particular JSON writer used.
        a = {"id": "inv_digest_a", "label": "label-a", "weight": 1.0}
        b = {"weight": 1.0, "label": "label-a", "id": "inv_digest_a"}
        assert resource_store.canonical_digest(a) == resource_store.canonical_digest(b)
        # A change in any single character produces a different digest.
        c = {"id": "inv_digest_a", "label": "label-a", "weight": 1.01}
        assert resource_store.canonical_digest(a) != resource_store.canonical_digest(c)

    def test_canonical_digest_is_stable_across_whole_number_floats(
        self, isolated_db,
    ):
        # 1.0 and 1 must share a digest so a writer that emits either
        # form produces the same revision. The canonical form
        # normalises whole-number floats to int.
        assert (
            resource_store.canonical_digest({"v": 1.0})
            == resource_store.canonical_digest({"v": 1})
        )

    def test_payload_storage_preserves_list_order(self, isolated_db):
        lib_id = resource_store.ensure_library("inv_order_lib")
        ordered = {
            "id": "inv_order_entry",
            "steps": ["first", "second", "third", "fourth"],
            "nested_list": [[1, 2], [3, 4], [5, 6]],
        }
        rev_id = resource_store.record_revision(lib_id, "inv_order_entry", ordered)
        got = resource_store.get_revision(revision_id=rev_id)
        assert got is not None
        assert got["payload"]["steps"] == ["first", "second", "third", "fourth"]
        assert got["payload"]["nested_list"] == [[1, 2], [3, 4], [5, 6]]


class TestTranslationAndCoverageAreSeparate:
    """The two sidecar columns are independent of the original payload."""

    def test_translation_is_returned_verbatim_alongside_the_payload(
        self, isolated_db,
    ):
        lib_id = resource_store.ensure_library("inv_sidecar_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, NESTED_PAYLOAD["id"], NESTED_PAYLOAD,
            translation=ENGLISH_TRANSLATION, coverage=ENGLISH_COVERAGE,
        )
        got = resource_store.get_revision(revision_id=rev_id)
        assert got is not None
        # Sidecars are returned as their original Python values.
        assert got["translation"] == ENGLISH_TRANSLATION, got["translation"]
        assert got["coverage"] == ENGLISH_COVERAGE, got["coverage"]
        # The three columns are read off different physical columns:
        # changing one in the row does not change the other two. The
        # assertion below touches the row directly so a future
        # accidental column-collapse shows as a precise failure.
        db.run(
            "UPDATE asset_revision SET translation = ? WHERE id = ?",
            json.dumps({"only": "sidecar"}), rev_id,
        )
        reread = resource_store.get_revision(revision_id=rev_id)
        assert reread is not None
        assert reread["translation"] == {"only": "sidecar"}
        # Payload and coverage are untouched by a translation-only
        # write. This is the "translation data remain separate from
        # the original payload" rule.
        assert reread["payload"] == NESTED_PAYLOAD
        assert reread["coverage"] == ENGLISH_COVERAGE

    def test_coverage_is_returned_verbatim_alongside_the_payload(
        self, isolated_db,
    ):
        lib_id = resource_store.ensure_library("inv_coverage_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, NESTED_PAYLOAD["id"], NESTED_PAYLOAD,
            translation=ENGLISH_TRANSLATION, coverage=ENGLISH_COVERAGE,
        )
        got = resource_store.get_revision(revision_id=rev_id)
        assert got is not None
        # The original coverage survives byte for byte: nested dicts,
        # nested lists, booleans, all of it.
        assert got["coverage"] == ENGLISH_COVERAGE
        # Writing only the coverage column does not touch the payload
        # or translation columns.
        new_coverage = {
            "fields": {"id": {"role": "identity", "translated": True}},
            "missing_translations": ["label"],
            "unmapped_fields": [],
        }
        db.run(
            "UPDATE asset_revision SET coverage = ? WHERE id = ?",
            json.dumps(new_coverage), rev_id,
        )
        reread = resource_store.get_revision(revision_id=rev_id)
        assert reread is not None
        assert reread["coverage"] == new_coverage
        assert reread["payload"] == NESTED_PAYLOAD
        assert reread["translation"] == ENGLISH_TRANSLATION

    def test_omitting_translation_and_coverage_records_empty_objects(
        self, isolated_db,
    ):
        # The two sidecars default to '{}' at the schema level, and
        # the service returns them as the empty dict when the caller
        # does not supply them. A future task can fill them in
        # without touching the original payload.
        lib_id = resource_store.ensure_library("inv_no_sidecar_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, NESTED_PAYLOAD["id"], NESTED_PAYLOAD,
        )
        got = resource_store.get_revision(revision_id=rev_id)
        assert got is not None
        assert got["translation"] == {}
        assert got["coverage"] == {}


class TestRevisionUniqueness:
    """Identical content does not create a duplicate revision."""

    def test_identical_content_for_the_same_triple_returns_the_existing_id(
        self, isolated_db,
    ):
        lib_id = resource_store.ensure_library("inv_unique_lib", kind="rooms")
        first = resource_store.record_revision(
            lib_id, "inv_unique_entry_01", NESTED_PAYLOAD,
            translation=ENGLISH_TRANSLATION, coverage=ENGLISH_COVERAGE,
        )
        # A second record_revision with the SAME triple must not
        # create a second row: the unique key is on
        # (library_id, source_id, content_digest), the content
        # digest is identical, so the row id is the existing one.
        second = resource_store.record_revision(
            lib_id, "inv_unique_entry_01", NESTED_PAYLOAD,
            translation=ENGLISH_TRANSLATION, coverage=ENGLISH_COVERAGE,
        )
        assert first == second
        count = db.one(
            "SELECT COUNT(*) AS c FROM asset_revision WHERE library_id = ?",
            lib_id,
        )["c"]
        assert count == 1, count

    def test_a_dict_whose_keys_are_reordered_is_the_same_revision(
        self, isolated_db,
    ):
        # The unique key is on the canonical digest, which is
        # order-independent. A record_revision with the same payload
        # in a different insertion order must hit the same row. The
        # assertion below uses the FULL nested payload, not a
        # subset, so the digest really compares two dicts that
        # differ only in their top-level key order.
        lib_id = resource_store.ensure_library("inv_reorder_lib", kind="rooms")
        first = resource_store.record_revision(
            lib_id, "inv_reorder_entry", NESTED_PAYLOAD,
        )
        reordered = {k: NESTED_PAYLOAD[k] for k in reversed(list(NESTED_PAYLOAD))}
        # Sanity: the dicts really do have a different insertion
        # order at the top level, otherwise the test would pass for
        # the wrong reason.
        assert list(reordered) != list(NESTED_PAYLOAD)
        # The two payloads still compare equal as JSON values, so
        # their canonical digests match.
        assert resource_store.canonical_digest(NESTED_PAYLOAD) == \
               resource_store.canonical_digest(reordered)
        second = resource_store.record_revision(
            lib_id, "inv_reorder_entry", reordered,
        )
        assert first == second
        count = db.one(
            "SELECT COUNT(*) AS c FROM asset_revision "
            "WHERE library_id = ? AND source_id = ?",
            lib_id, "inv_reorder_entry",
        )["c"]
        assert count == 1, count

    def test_direct_insert_of_a_duplicate_revision_is_refused_by_sqlite(
        self, isolated_db,
    ):
        # The unique key must be enforced at the SQL level, not only
        # at the Python service. A direct INSERT that bypasses the
        # service and tries to write the same (library, source_id,
        # content_digest) twice must fail. This pins the spec's
        # "explicit uniqueness constraints" requirement to the
        # schema, where a future code path cannot quietly disable
        # it.
        lib_id = resource_store.ensure_library("inv_direct_lib", kind="rooms")
        digest = resource_store.canonical_digest(NESTED_PAYLOAD)
        payload_text = json.dumps(NESTED_PAYLOAD, ensure_ascii=False)
        first = db.run(
            "INSERT INTO asset_revision "
            "(library_id, source_id, content_digest, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            lib_id, "inv_direct_entry", digest, payload_text, db.now(),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.run(
                "INSERT INTO asset_revision "
                "(library_id, source_id, content_digest, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                lib_id, "inv_direct_entry", digest, payload_text, db.now(),
            )
        # The first insert succeeded and is still readable.
        got = resource_store.get_revision(revision_id=first)
        assert got is not None
        assert got["source_id"] == "inv_direct_entry"


class TestRevisionImmutabilityAtSqlLevel:
    """Direct SQL UPDATEs of the protected columns are rejected.

    The service-level ``record_revision`` API has no UPDATE path, and
    the unique key on (library_id, source_id, content_digest) makes
    duplicate INSERTs impossible. But a hand-rolled ``UPDATE`` statement
    against the table could still rewrite the original payload, move
    a revision to a different library, change its source identity, or
    tamper with its content digest - the kind of silent rewrite that
    would corrupt the unit of evidence the spec demands be immutable.

    The schema-level guard that prevents this is a ``BEFORE UPDATE OF
    <protected-columns>`` trigger. The trigger fires only when the
    UPDATE statement's SET clause names one of the protected columns
    (id, library_id, source_id, content_digest, payload, created_at);
    the translation and coverage columns are deliberately NOT in that
    list, so a later task can fill or update them without rewriting
    the original payload. The tests in this class pin both halves of
    the contract: every protected field is locked, and every writable
    field stays writable.

    The protection has to be the schema, not a Python check, so a
    future code path that builds an UPDATE statement cannot quietly
    bypass the service-level contract. The trigger is the only
    thing standing between an operator and a corrupted revision.
    """

    def _plant_revision(
        self,
        isolated_db,
        *,
        library_key: str = "inv_immut_sql_lib",
        entry_id: str = "inv_immut_sql_entry",
        translation: dict | None = None,
        coverage: dict | None = None,
    ) -> tuple[int, int]:
        """Register a library and a revision, returning ``(lib_id, rev_id)``.

        The sidecars default to ``None`` (the empty dict in the
        schema), and the planted row is captured for the
        ``_assert_row_unchanged`` helper so the test asserts the
        row matches its own pre-update state, not a hard-coded
        pair of values.
        """
        lib_id = resource_store.ensure_library(library_key, kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, entry_id, NESTED_PAYLOAD,
            translation=translation, coverage=coverage,
        )
        # Capture the row's state immediately after planting, so
        # the unchanged-row assertion can compare against the
        # exact bytes the original INSERT wrote.
        initial = db.one(
            "SELECT payload, translation, coverage, content_digest, "
            "library_id, source_id, created_at "
            "FROM asset_revision WHERE id = ?",
            rev_id,
        )
        assert initial is not None
        # Stash the captured state on the instance for the
        # assertion helper to read. A fresh test method gets a
        # fresh instance, so this never leaks across tests.
        self._initial_state = dict(initial)
        return lib_id, rev_id

    def _assert_row_unchanged(self, rev_id: int) -> None:
        """The row is byte-for-byte identical to the post-plant state.

        "Unchanged" is the row's pre-update state, not a
        hard-coded reference value: the test pinned down exactly
        what the original INSERT wrote, and the assertion checks
        every column of interest is the same string after the
        rejected UPDATE. An accidental rewrite of any protected
        column shows up as a precise string diff.
        """
        initial = getattr(self, "_initial_state", None)
        assert initial is not None, (
            "_assert_row_unchanged called without _plant_revision"
        )
        after = db.one(
            "SELECT payload, translation, coverage, content_digest, "
            "library_id, source_id, created_at "
            "FROM asset_revision WHERE id = ?",
            rev_id,
        )
        assert after is not None
        # The protected columns are identical, byte for byte, to
        # what the original INSERT wrote. The raw string
        # comparison is the strongest form of the assertion: a
        # in-place whitespace rewrite of the JSON would still
        # parse to the same value but would fail the string
        # equality.
        for col in (
            "payload", "translation", "coverage", "content_digest",
            "library_id", "source_id", "created_at",
        ):
            assert after[col] == initial[col], (
                f"column {col!r} was rewritten: "
                f"before={initial[col]!r} after={after[col]!r}"
            )
        # The decoded payload is also the input the test planted.
        # This guards against a future change that re-encodes the
        # payload column with a different layout (which would
        # still match the byte-level check above for an unchanged
        # row, but a divergence here would catch it early).
        assert json.loads(after["payload"]) == NESTED_PAYLOAD

    # -- Blocked updates: every protected column ----------------------------

    def test_direct_update_of_payload_is_rejected_and_row_unchanged(
        self, isolated_db,
    ):
        _, rev_id = self._plant_revision(
            isolated_db,
            translation=ENGLISH_TRANSLATION, coverage=ENGLISH_COVERAGE,
        )
        forged = json.dumps({"id": "inv_forged", "label": "forged payload"})
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.run(
                "UPDATE asset_revision SET payload = ? WHERE id = ?",
                forged, rev_id,
            )
        # The error message names the rule, so an operator who hits
        # it can see the contract that was violated.
        assert "immutable" in str(exc_info.value).lower()
        self._assert_row_unchanged(rev_id)

    def test_direct_update_of_library_id_is_rejected(self, isolated_db):
        # A second library stands ready as a "victim" the update
        # would otherwise move the revision into. The trigger must
        # block the move and the row stays where it was.
        lib_id, rev_id = self._plant_revision(isolated_db)
        other_lib = resource_store.ensure_library("inv_immut_sql_other_lib")
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.run(
                "UPDATE asset_revision SET library_id = ? WHERE id = ?",
                other_lib, rev_id,
            )
        assert "immutable" in str(exc_info.value).lower()
        # library_id on the row is unchanged.
        raw = db.one(
            "SELECT library_id FROM asset_revision WHERE id = ?", rev_id,
        )
        assert raw["library_id"] == lib_id
        self._assert_row_unchanged(rev_id)

    def test_direct_update_of_source_id_is_rejected(self, isolated_db):
        _, rev_id = self._plant_revision(isolated_db)
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.run(
                "UPDATE asset_revision SET source_id = ? WHERE id = ?",
                "inv_forged_source", rev_id,
            )
        assert "immutable" in str(exc_info.value).lower()
        raw = db.one(
            "SELECT source_id FROM asset_revision WHERE id = ?", rev_id,
        )
        assert raw["source_id"] == "inv_immut_sql_entry"
        self._assert_row_unchanged(rev_id)

    def test_direct_update_of_content_digest_is_rejected(self, isolated_db):
        _, rev_id = self._plant_revision(isolated_db)
        forged_digest = resource_store.canonical_digest(NESTED_PAYLOAD_V2)
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.run(
                "UPDATE asset_revision SET content_digest = ? WHERE id = ?",
                forged_digest, rev_id,
            )
        assert "immutable" in str(exc_info.value).lower()
        raw = db.one(
            "SELECT content_digest FROM asset_revision WHERE id = ?", rev_id,
        )
        assert raw["content_digest"] == resource_store.canonical_digest(NESTED_PAYLOAD)
        self._assert_row_unchanged(rev_id)

    def test_direct_update_of_created_at_is_rejected(self, isolated_db):
        _, rev_id = self._plant_revision(isolated_db)
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.run(
                "UPDATE asset_revision SET created_at = ? WHERE id = ?",
                "1999-01-01T00:00:00+00:00", rev_id,
            )
        assert "immutable" in str(exc_info.value).lower()
        self._assert_row_unchanged(rev_id)

    def test_direct_update_of_id_is_rejected(self, isolated_db):
        # The primary key is also protected, even though the unique
        # index on it would refuse a collision anyway. The trigger
        # is the first guard, so the rejection message names the
        # immutability rule rather than a unique-constraint
        # message.
        _, rev_id = self._plant_revision(isolated_db)
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.run(
                "UPDATE asset_revision SET id = ? WHERE id = ?",
                rev_id + 999_999, rev_id,
            )
        assert "immutable" in str(exc_info.value).lower()
        # The row still exists under its original id.
        got = resource_store.get_revision(revision_id=rev_id)
        assert got is not None
        self._assert_row_unchanged(rev_id)

    def test_a_combined_update_that_mentions_a_protected_column_is_rejected(
        self, isolated_db,
    ):
        # An UPDATE that touches both translation AND payload is
        # rejected because payload is in the trigger's OF list. The
        # whole statement is rejected (SQLite's RAISE(ABORT) rolls
        # back the statement's changes), so translation is also not
        # written. The "translation and coverage are independently
        # writable" rule is the trigger NOT firing; "the original
        # payload is immutable" is the trigger firing here. The two
        # rules co-exist because the trigger fires on payload even
        # when other columns are also being set.
        _, rev_id = self._plant_revision(isolated_db)
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.run(
                "UPDATE asset_revision SET translation = ?, payload = ? "
                "WHERE id = ?",
                json.dumps({"new": "translation"}),
                json.dumps({"forged": "payload"}),
                rev_id,
            )
        assert "immutable" in str(exc_info.value).lower()
        # The translation is also not written: the whole statement
        # is rejected, not just the payload assignment. The original
        # row is fully intact.
        self._assert_row_unchanged(rev_id)

    def test_even_a_no_op_update_of_a_protected_column_is_rejected(
        self, isolated_db,
    ):
        # BEFORE UPDATE OF <col> fires when the column is named in
        # the SET clause, regardless of whether the new value
        # differs from the old. A hand-rolled UPDATE that sets
        # payload to its current value (a no-op rewrite) must still
        # be rejected, because the contract is "no UPDATE statement
        # may mention the protected columns", not "no UPDATE may
        # change the protected columns". The test pins that
        # distinction: a careless rewrite with the same bytes is
        # not silently allowed.
        _, rev_id = self._plant_revision(isolated_db)
        current = json.dumps(NESTED_PAYLOAD, ensure_ascii=False)
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            db.run(
                "UPDATE asset_revision SET payload = ? WHERE id = ?",
                current, rev_id,
            )
        assert "immutable" in str(exc_info.value).lower()
        self._assert_row_unchanged(rev_id)

    # -- Allowed updates: translation and coverage stay writable ------------

    def test_translation_remains_independently_writable(self, isolated_db):
        # A direct UPDATE that names ONLY translation in its SET
        # clause does not fire the trigger (translation is not in
        # the OF list). The row is rewritten in place, and the
        # original payload and coverage columns are untouched. The
        # test reads the raw row to assert the absence of any
        # payload or coverage rewrite at the SQL level.
        _, rev_id = self._plant_revision(
            isolated_db,
            translation=ENGLISH_TRANSLATION, coverage=ENGLISH_COVERAGE,
        )
        new_translation = {"updated": "translation", "version": 2}
        db.run(
            "UPDATE asset_revision SET translation = ? WHERE id = ?",
            json.dumps(new_translation), rev_id,
        )
        got = resource_store.get_revision(revision_id=rev_id)
        assert got is not None
        assert got["translation"] == new_translation
        assert got["payload"] == NESTED_PAYLOAD
        assert got["coverage"] == ENGLISH_COVERAGE
        # The raw row: the payload and coverage columns on disk are
        # the JSON the original INSERT wrote, not a re-encoding of
        # the unchanged values.
        raw = db.one(
            "SELECT payload, coverage FROM asset_revision WHERE id = ?",
            rev_id,
        )
        assert json.loads(raw["payload"]) == NESTED_PAYLOAD
        assert json.loads(raw["coverage"]) == ENGLISH_COVERAGE

    def test_coverage_remains_independently_writable(self, isolated_db):
        _, rev_id = self._plant_revision(
            isolated_db,
            translation=ENGLISH_TRANSLATION, coverage=ENGLISH_COVERAGE,
        )
        new_coverage = {
            "fields": {"id": {"role": "identity", "translated": True}},
            "missing_translations": ["label", "scene_theme"],
            "unmapped_fields": [],
        }
        db.run(
            "UPDATE asset_revision SET coverage = ? WHERE id = ?",
            json.dumps(new_coverage), rev_id,
        )
        got = resource_store.get_revision(revision_id=rev_id)
        assert got is not None
        assert got["coverage"] == new_coverage
        assert got["payload"] == NESTED_PAYLOAD
        assert got["translation"] == ENGLISH_TRANSLATION
        raw = db.one(
            "SELECT payload, translation FROM asset_revision WHERE id = ?",
            rev_id,
        )
        assert json.loads(raw["payload"]) == NESTED_PAYLOAD
        assert json.loads(raw["translation"]) == ENGLISH_TRANSLATION

    def test_translation_and_coverage_can_be_updated_in_one_statement(
        self, isolated_db,
    ):
        # The common case for a later task: fill in both sidecars at
        # once. The SET clause names translation and coverage only;
        # the trigger's OF list does not contain either, so the
        # statement succeeds. The original payload survives.
        _, rev_id = self._plant_revision(isolated_db)
        new_t = {"label": "translated", "scene_theme": "translated"}
        new_c = {"fields": {}, "missing_translations": [], "unmapped_fields": []}
        db.run(
            "UPDATE asset_revision SET translation = ?, coverage = ? "
            "WHERE id = ?",
            json.dumps(new_t), json.dumps(new_c), rev_id,
        )
        got = resource_store.get_revision(revision_id=rev_id)
        assert got is not None
        assert got["translation"] == new_t
        assert got["coverage"] == new_c
        assert got["payload"] == NESTED_PAYLOAD

    def test_a_translation_only_update_on_a_revision_with_no_sidecars_works(
        self, isolated_db,
    ):
        # The trigger must not fire when translation is named in the
        # SET clause, even if the existing translation is the empty
        # default. The empty default is the safe neutral state the
        # schema declares, and a later task that fills it in must
        # not be blocked by the immutability guard.
        _, rev_id = self._plant_revision(isolated_db)  # no translation/coverage
        assert resource_store.get_revision(revision_id=rev_id)["translation"] == {}
        new_translation = {"first": "sidecar"}
        db.run(
            "UPDATE asset_revision SET translation = ? WHERE id = ?",
            json.dumps(new_translation), rev_id,
        )
        got = resource_store.get_revision(revision_id=rev_id)
        assert got is not None
        assert got["translation"] == new_translation
        assert got["payload"] == NESTED_PAYLOAD
        assert got["coverage"] == {}

    # -- The trigger is part of the schema, not the application -----------

    def test_the_trigger_is_listed_in_sqlite_master(self, isolated_db):
        names = {
            r["name"]
            for r in db.q(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert "asset_revision_protect_immutable" in names, sorted(names)

    def test_the_trigger_defines_a_before_update_with_the_protected_columns(
        self, isolated_db,
    ):
        # Read the trigger's SQL out of sqlite_master and assert the
        # protected-column list and the sidecar exclusion are what
        # the spec requires. A future change to the trigger's name,
        # its event, or its column list is caught here.
        row = db.one(
            "SELECT sql FROM sqlite_master "
            "WHERE type='trigger' AND name='asset_revision_protect_immutable'"
        )
        assert row is not None, "trigger not present in sqlite_master"
        sql = row["sql"] or ""
        assert "BEFORE UPDATE" in sql, sql
        # The protected columns are the ones the spec names.
        for col in (
            "id", "library_id", "source_id", "content_digest",
            "payload", "created_at",
        ):
            assert col in sql, (col, sql)
        # translation and coverage are deliberately NOT in the OF
        # list: an UPDATE that names them succeeds. The "OF" clause
        # in the SQL is what makes the trigger fire selectively.
        # We assert that the trigger's column-list contains none of
        # the sidecar names by reading the OF <cols> portion.
        of_match = re.search(r"OF\s+([^\)]+)\s+ON\s+asset_revision", sql)
        assert of_match is not None, sql
        of_cols = set(of_match.group(1).split(","))
        for col in ("translation", "coverage"):
            assert col not in of_cols, (col, of_cols)
        # The error message names the rule an operator who hits it
        # will see, so a future rename of the rule or the column
        # list is caught here too.
        assert "immutable" in sql.lower(), sql

    def test_an_older_database_gains_the_trigger_on_reopen(
        self, tmp_path,
    ):
        # Simulate a database that has the table but pre-dates the
        # trigger. The next db.connect() must add the trigger
        # without raising, without losing data, and without
        # rewriting any legacy row. The IF NOT EXISTS in SCHEMA is
        # what makes the trigger creation additive: every connect
        # re-runs SCHEMA, and a trigger that does not exist is
        # created with no destructive side effect.
        path = Path(tmp_path) / "old-trigger.db"
        _open(path)
        # Plant a row so a re-open has to keep data.
        lib_id = resource_store.ensure_library("inv_old_trigger_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "inv_old_trigger_entry", NESTED_PAYLOAD,
        )
        # Drop the trigger. The next connect has to add it back.
        db.run("DROP TRIGGER IF EXISTS asset_revision_protect_immutable")
        # And confirm the trigger is gone.
        assert db.one(
            "SELECT name FROM sqlite_master "
            "WHERE type='trigger' AND name='asset_revision_protect_immutable'"
        ) is None
        _close_silently()

        _open(path)
        try:
            # Trigger is back.
            row = db.one(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND name='asset_revision_protect_immutable'"
            )
            assert row is not None
            # The pre-existing row survived.
            got = resource_store.get_revision(revision_id=rev_id)
            assert got is not None
            assert got["payload"] == NESTED_PAYLOAD
            # And the immutability rule is enforced on the
            # surviving row, which is the proof that the trigger
            # is live and not just present in sqlite_master.
            with pytest.raises(sqlite3.IntegrityError):
                db.run(
                    "UPDATE asset_revision SET payload = ? WHERE id = ?",
                    json.dumps({"id": "forged", "label": "forged"}), rev_id,
                )
            # The row is still intact after the rejected rewrite.
            again = resource_store.get_revision(revision_id=rev_id)
            assert again is not None
            assert again["payload"] == NESTED_PAYLOAD
        finally:
            _close_silently()

    def test_repeated_connect_keeps_the_trigger_idempotent(
        self, tmp_path,
    ):
        # The trigger CREATE uses IF NOT EXISTS, so a sequence of
        # connects on the same database does not raise, does not
        # create a second trigger, and does not drop the existing
        # one. The test is the additive-migration guarantee: every
        # connect leaves the schema one step further forward, never
        # a step back.
        path = Path(tmp_path) / "trigger-idempotent.db"
        for _ in range(3):
            _open(path)
            try:
                rows = db.q(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='trigger' "
                    "AND name='asset_revision_protect_immutable'"
                )
                assert len(rows) == 1, rows
            finally:
                _close_silently()


class TestRevisionImmutability:
    """A changed source entry creates a new immutable revision."""

    def test_a_changed_source_entry_creates_a_new_revision(
        self, isolated_db,
    ):
        lib_id = resource_store.ensure_library("inv_changed_lib", kind="rooms")
        first = resource_store.record_revision(
            lib_id, "inv_changed_entry", NESTED_PAYLOAD,
        )
        second = resource_store.record_revision(
            lib_id, "inv_changed_entry", NESTED_PAYLOAD_V2,
        )
        # Different ids, both readable.
        assert first != second
        v1 = resource_store.get_revision(revision_id=first)
        v2 = resource_store.get_revision(revision_id=second)
        assert v1 is not None and v2 is not None
        assert v1["payload"] == NESTED_PAYLOAD
        assert v2["payload"] == NESTED_PAYLOAD_V2
        # The two revisions carry different content digests, which
        # is the schema's reason to admit both rows.
        assert v1["content_digest"] != v2["content_digest"]

    def test_the_prior_revision_remains_unchanged_after_a_new_one_is_added(
        self, isolated_db,
    ):
        lib_id = resource_store.ensure_library("inv_prior_lib", kind="rooms")
        first = resource_store.record_revision(
            lib_id, "inv_prior_entry", NESTED_PAYLOAD,
        )
        # Capture the prior revision's stored form, byte for byte, in
        # the database, so the assertion below can read the raw column
        # without going through the service.
        prior_payload_raw = db.one(
            "SELECT payload FROM asset_revision WHERE id = ?", first,
        )["payload"]
        # Insert the second revision.
        second = resource_store.record_revision(
            lib_id, "inv_prior_entry", NESTED_PAYLOAD_V2,
        )
        # Re-read the first row: it is still the original payload, in
        # the original textual form, on the original id.
        again = db.one(
            "SELECT id, payload, content_digest, created_at "
            "FROM asset_revision WHERE id = ?",
            first,
        )
        assert again is not None
        assert again["id"] == first
        assert again["payload"] == prior_payload_raw, "the prior revision's payload was rewritten"
        assert again["content_digest"] == resource_store.canonical_digest(NESTED_PAYLOAD)
        # The new revision sits alongside it.
        assert second != first
        all_rows = db.q(
            "SELECT id FROM asset_revision WHERE library_id = ? AND source_id = ?",
            lib_id, "inv_prior_entry",
        )
        assert {r["id"] for r in all_rows} == {first, second}

    def test_three_revisions_for_one_source_entry_are_all_immutable(
        self, isolated_db,
    ):
        lib_id = resource_store.ensure_library("inv_triple_lib", kind="rooms")
        v1 = {"id": "inv_triple", "label": "first"}
        v2 = {"id": "inv_triple", "label": "second"}
        v3 = {"id": "inv_triple", "label": "third"}
        a = resource_store.record_revision(lib_id, "inv_triple", v1)
        b = resource_store.record_revision(lib_id, "inv_triple", v2)
        c = resource_store.record_revision(lib_id, "inv_triple", v3)
        # A repeat of v1 must hit the existing row.
        a_again = resource_store.record_revision(lib_id, "inv_triple", v1)
        assert a_again == a
        listed = resource_store.list_revisions(lib_id, "inv_triple")
        assert [r["payload"]["label"] for r in listed] == ["first", "second", "third"]
        assert {r["id"] for r in listed} == {a, b, c}

    def test_revisions_for_different_libraries_are_isolated(
        self, isolated_db,
    ):
        lib_a = resource_store.ensure_library("inv_lib_a", kind="rooms")
        lib_b = resource_store.ensure_library("inv_lib_b", kind="rooms")
        # Same source_id, same payload, different libraries: the
        # unique key is (library_id, source_id, content_digest), so
        # the two rows are distinct revisions of distinct libraries.
        rev_a = resource_store.record_revision(
            lib_a, "inv_shared_id", NESTED_PAYLOAD,
        )
        rev_b = resource_store.record_revision(
            lib_b, "inv_shared_id", NESTED_PAYLOAD,
        )
        assert rev_a != rev_b
        assert resource_store.get_revision(
            library_id=lib_a, source_id="inv_shared_id",
        )["id"] == rev_a
        assert resource_store.get_revision(
            library_id=lib_b, source_id="inv_shared_id",
        )["id"] == rev_b


class TestLookupByTriple:
    """The natural-key lookup returns the right revision."""

    def test_lookup_by_triple_returns_one_specific_revision(
        self, isolated_db,
    ):
        lib_id = resource_store.ensure_library("inv_lookup_lib", kind="rooms")
        first = resource_store.record_revision(
            lib_id, "inv_lookup_entry", NESTED_PAYLOAD,
        )
        second = resource_store.record_revision(
            lib_id, "inv_lookup_entry", NESTED_PAYLOAD_V2,
        )
        # Lookup with the canonical digest of v1 returns the v1 row.
        v1_digest = resource_store.canonical_digest(NESTED_PAYLOAD)
        v2_digest = resource_store.canonical_digest(NESTED_PAYLOAD_V2)
        got_v1 = resource_store.get_revision(
            library_id=lib_id,
            source_id="inv_lookup_entry",
            content_digest=v1_digest,
        )
        got_v2 = resource_store.get_revision(
            library_id=lib_id,
            source_id="inv_lookup_entry",
            content_digest=v2_digest,
        )
        assert got_v1["id"] == first
        assert got_v2["id"] == second
        assert got_v1["payload"] == NESTED_PAYLOAD
        assert got_v2["payload"] == NESTED_PAYLOAD_V2

    def test_lookup_by_source_id_without_digest_returns_the_latest(
        self, isolated_db,
    ):
        # When a caller asks for (library_id, source_id) without a
        # content digest, the spec wants the latest revision. The
        # service implements this as a single ORDER BY id DESC LIMIT 1
        # query.
        lib_id = resource_store.ensure_library("inv_latest_lib", kind="rooms")
        first = resource_store.record_revision(
            lib_id, "inv_latest_entry", NESTED_PAYLOAD,
        )
        second = resource_store.record_revision(
            lib_id, "inv_latest_entry", NESTED_PAYLOAD_V2,
        )
        got = resource_store.get_revision(
            library_id=lib_id, source_id="inv_latest_entry",
        )
        assert got["id"] == second
        assert got["payload"] == NESTED_PAYLOAD_V2
        # The first row is still there for the precise lookup.
        v1_digest = resource_store.canonical_digest(NESTED_PAYLOAD)
        first_got = resource_store.get_revision(
            library_id=lib_id, source_id="inv_latest_entry",
            content_digest=v1_digest,
        )
        assert first_got["id"] == first

    def test_lookup_returns_none_for_an_unknown_triple(
        self, isolated_db,
    ):
        lib_id = resource_store.ensure_library("inv_empty_lookup_lib")
        assert resource_store.get_revision(
            library_id=lib_id, source_id="inv_does_not_exist",
        ) is None
        assert resource_store.get_revision(revision_id=999_999) is None


class TestLibraryRegistration:
    """The library registration is idempotent and isolated."""

    def test_ensure_library_is_idempotent(self, isolated_db):
        a = resource_store.ensure_library("inv_lib_key", display_name="first", kind="rooms")
        b = resource_store.ensure_library("inv_lib_key", display_name="different",
                                          kind="fused_scenes")
        assert a == b
        # The label and kind of the FIRST registration are preserved:
        # a re-registration does not rewrite the row. The whole point
        # of the unique key is that the first writer wins.
        libs = resource_store.list_libraries()
        assert len(libs) == 1
        assert libs[0]["library_key"] == "inv_lib_key"
        assert libs[0]["display_name"] == "first"
        assert libs[0]["kind"] == "rooms"

    def test_two_libraries_with_different_keys(self, isolated_db):
        a = resource_store.ensure_library("inv_lib_alpha", kind="rooms")
        b = resource_store.ensure_library("inv_lib_beta", kind="fused_scenes")
        assert a != b
        assert {lib["library_key"] for lib in resource_store.list_libraries()} == {
            "inv_lib_alpha", "inv_lib_beta",
        }

    def test_ensure_library_rejects_an_empty_key(self, isolated_db):
        with pytest.raises(ValueError):
            resource_store.ensure_library("")


class TestLegacySchemaUnaffected:
    """The legacy schema and data the rest of the suite relies on survives."""

    def test_legacy_session_creation_still_works(self, isolated_db):
        # Plant the minimum a session needs and read it back. This is
        # the smallest possible smoke test that the migration did not
        # break the legacy tables.
        db.run(
            "INSERT INTO model (name, trigger, created_at) VALUES (?, ?, ?)",
            "inv_legacy_model", "invtrigger", db.now(),
        )
        mid = db.one("SELECT id FROM model WHERE name = 'inv_legacy_model'")["id"]
        sid = db.run(
            "INSERT INTO session (model_id, name, created_at) VALUES (?, ?, ?)",
            mid, "inv_legacy_session", db.now(),
        )
        got = db.one("SELECT name FROM session WHERE id = ?", sid)
        assert got is not None
        assert got["name"] == "inv_legacy_session"

    def test_resource_library_is_deleted_when_the_cascade_fires(
        self, isolated_db,
    ):
        # Foreign-key cascade test: deleting a library deletes its
        # revisions. This is a property the schema declares and the
        # spec does not require, but a future task that drops a
        # library will rely on it, and the test pins the cascade so
        # the schema is the one we wrote.
        lib_id = resource_store.ensure_library("inv_cascade_lib", kind="rooms")
        resource_store.record_revision(lib_id, "inv_cascade_entry", NESTED_PAYLOAD)
        assert db.one(
            "SELECT COUNT(*) AS c FROM asset_revision WHERE library_id = ?",
            lib_id,
        )["c"] == 1
        db.run("DELETE FROM resource_library WHERE id = ?", lib_id)
        assert db.one(
            "SELECT COUNT(*) AS c FROM asset_revision WHERE library_id = ?",
            lib_id,
        )["c"] == 0


# ---- Privacy: the new files add no personal data ------------------------


def test_new_modules_are_free_of_personal_data_and_non_english_glyphs():
    """The new modules this task adds do not introduce personal data or CJK
    glyphs. The repo-wide scanner in ``test_no_personal_data.py`` already
    covers tracked files; this focused test pins the rule for the two
    files this task adds so a future refactor of either module is caught
    with a message that names the offender.
    """
    new_files = (
        Path(__file__).resolve().parent.parent / "backend" / "resource_store.py",
        Path(__file__).resolve(),
    )
    for path in new_files:
        text = path.read_text(encoding="utf-8")
        for label, pattern in PRIVACY_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text[:match.start()].count("\n") + 1
                pytest.fail(f"{path}:{line}: {label}: {match.group(0)}")
        # English-only: anything outside the ASCII range is a
        # violation of the working rules in AGENTS.md. The two new
        # modules have no reason to carry non-ASCII characters.
        for ch in text:
            if ord(ch) > 0x7F:
                line = text[:text.index(ch)].count("\n") + 1
                pytest.fail(f"{path}:{line}: non-ASCII character U+{ord(ch):04X}")
