"""Tests for the resource translation workflow and sidecar storage (task 2.2).

Covers:
  - Canonical field names and semantic family resolution;
  - Stable canonical keys in pending_fields and source_field metadata;
  - Rejection of alias collisions and conflicting map translations;
  - Independent descriptive fields retention;
  - Zero payload fallback for required descriptive fields during preparation;
  - Omission of untranslated non-English optional descriptive fields;
  - Two-phase preview -> apply with HMAC-SHA256 attestation;
  - TOCTOU drift detection raising TranslationConflictError;
  - Atomic database updates and validated merge semantics;
  - Idempotency and safe handling of unmatched valid map entries;
  - Immutability of historical finalized prompts.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import time
from typing import Any
import uuid

import pytest

from backend import resource_translation as backend_resource_translation
import db
import resource_preparation
import resource_prompts
import resource_parser
import resource_readiness
import resource_selection
import resource_store
import resource_translation
import session_plan


INV_LOOK = "A clean portrait look with natural side lighting."
INV_WARDROBE = "grey cotton shirt and dark trousers"


def _open(path: Path) -> sqlite3.Connection:
    """Open a fresh, isolated database for one test."""
    db._conn = None  # noqa: SLF001
    return db.connect(path)


def _close_silently() -> None:
    """Close current connection safely."""
    conn = db._conn  # noqa: SLF001
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    db._conn = None  # noqa: SLF001


@pytest.fixture
def isolated_db(tmp_path):
    """Yield a fresh isolated database for each test."""
    path = Path(tmp_path) / "resource-translation.db"
    _open(path)
    try:
        yield path
    finally:
        _close_silently()


_SESSION_COUNTER: list[int] = [0]


def _create_test_session(isolated_db: Any) -> int:
    """Helper to create a minimal session for testing."""
    now = db.now()
    _SESSION_COUNTER[0] += 1
    model_id = db.run(
        "INSERT INTO model (name, created_at) VALUES (?, ?)",
        f"invented translation model {_SESSION_COUNTER[0]}", now,
    )
    settings = json.dumps({"composition_mode": "resource-v1"})
    return db.run(
        "INSERT INTO session (model_id, name, settings, created_at) "
        "VALUES (?, ?, ?, ?)",
        model_id,
        f"invented translation session {_SESSION_COUNTER[0]}",
        settings, now,
    )


class TestStrictPreparationContract:
    def test_refuses_preparation_when_required_field_lacks_translation(self, isolated_db):
        """Preparation strictly consumes the translation sidecar for required fields;
        even if payload has English, lack of authorized translation sidecar raises
        PreparationFieldError."""
        library_id = resource_store.ensure_library("inv_rooms_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            library_id, "inv_room_01",
            {
                "id": "inv_room_01",
                "label": "invented sunny room",
                "scene_theme": "a bright sunny studio",
            },
            translation=None,
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None
        full_rev = {**rev, "kind": "rooms", "library_key": "inv_rooms_lib"}

        with pytest.raises(resource_preparation.PreparationFieldError) as exc_info:
            resource_preparation._prepare_resource(full_rev)
        assert "lacks an authorized English translation" in str(exc_info.value)
        assert "label" in str(exc_info.value)

    def test_accepts_preparation_when_required_fields_have_authorized_translation(self, isolated_db):
        """When translation sidecar supplies authorized English for required fields,
        preparation succeeds and populates descriptive_inputs."""
        library_id = resource_store.ensure_library("inv_rooms_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            library_id, "inv_room_01",
            {
                "id": "inv_room_01",
                "name": "SOURCE NAME",
                "theme": "SOURCE THEME",
            },
            translation={
                "label": "Authorized English Room",
                "scene_theme": "Authorized English Theme",
            },
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None
        full_rev = {**rev, "kind": "rooms", "library_key": "inv_rooms_lib"}

        prep = resource_preparation._prepare_resource(full_rev)
        assert prep["descriptive_inputs"]["label"] == "Authorized English Room"
        assert prep["descriptive_inputs"]["scene_theme"] == "Authorized English Theme"
        assert prep["translation"]["label"] == "Authorized English Room"

    def test_fused_scene_refuses_preparation_without_prompt_translation(self, isolated_db):
        """Fused scene requires an authorized prompt translation in the sidecar."""
        library_id = resource_store.ensure_library("inv_fused_lib", kind="fused_scenes")
        rev_id = resource_store.record_revision(
            library_id, "inv_fused_01",
            {
                "id": "inv_fused_01",
                "prompt": "She stands in a tall studio.",
            },
            translation={},
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None
        full_rev = {**rev, "kind": "fused_scenes", "library_key": "inv_fused_lib"}

        with pytest.raises(resource_preparation.PreparationFieldError) as exc_info:
            resource_preparation._prepare_resource(full_rev)
        assert "prompt" in str(exc_info.value)

    def test_untranslated_non_english_optional_descriptive_field_is_omitted(self, isolated_db):
        """Optional descriptive fields containing non-English text without translation
        are omitted from descriptive_inputs to prevent foreign script leakage."""
        library_id = resource_store.ensure_library("inv_rooms_lib", kind="rooms")
        # Non-English unicode characters in description
        non_english_desc = "\u30b9\u30bf\u30b8\u30aa\u306e\u98a8\u666f"
        rev_id = resource_store.record_revision(
            library_id, "inv_room_opt",
            {
                "id": "inv_room_opt",
                "label": "room",
                "scene_theme": "theme",
                "description": non_english_desc,
                "props": ["chair", "\u6728\u88fd\u306e\u673a"],
            },
            translation={
                "label": "English Room",
                "scene_theme": "English Theme",
            },
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None
        full_rev = {**rev, "kind": "rooms", "library_key": "inv_rooms_lib"}

        prep = resource_preparation._prepare_resource(full_rev)
        assert "description" not in prep["descriptive_inputs"]
        # props had a non-English item, so omitted
        assert "props" not in prep["descriptive_inputs"]

    def test_translated_optional_descriptive_field_is_included(self, isolated_db):
        """Optional descriptive fields with valid English translations are included."""
        library_id = resource_store.ensure_library("inv_rooms_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            library_id, "inv_room_opt2",
            {
                "id": "inv_room_opt2",
                "label": "room",
                "scene_theme": "theme",
                "description": "\u30b9\u30bf\u30b8\u30aa\u306e\u98a8\u666f",
            },
            translation={
                "label": "English Room",
                "scene_theme": "English Theme",
                "description": "A view of the studio interior",
            },
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None
        full_rev = {**rev, "kind": "rooms", "library_key": "inv_rooms_lib"}

        prep = resource_preparation._prepare_resource(full_rev)
        assert prep["descriptive_inputs"]["description"] == "A view of the studio interior"

    def test_independent_fields_do_not_satisfy_required_scene_theme(self, isolated_db):
        """Supplying description or props does NOT satisfy required scene_theme."""
        library_id = resource_store.ensure_library("inv_rooms_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            library_id, "inv_room_indep",
            {
                "id": "inv_room_indep",
                "label": "room",
                "description": "A quiet studio",
            },
            translation={
                "label": "English Room",
                "description": "A quiet studio",
            },
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None
        full_rev = {**rev, "kind": "rooms", "library_key": "inv_rooms_lib"}

        with pytest.raises(resource_preparation.PreparationFieldError) as exc_info:
            resource_preparation._prepare_resource(full_rev)
        assert "scene_theme" in str(exc_info.value)


class TestReadinessCanonicalKeys:
    def test_pending_fields_uses_canonical_key_and_records_source_field(self, isolated_db):
        """Pending fields use canonical key 'scene_theme' and indicate source_field = 'theme'."""
        payload = {
            "id": "inv_room_alias",
            "name": "SOURCE NAME",
            "theme": "SOURCE THEME",
        }
        report = resource_readiness.evaluate_readiness("rooms", payload, translation=None)
        assert not report.is_ready
        assert "label" in report.pending_fields
        assert "scene_theme" in report.pending_fields
        assert "source_field = 'theme'" in report.pending_fields["scene_theme"]
        assert "source_field = 'name'" in report.pending_fields["label"]

        cov = report.coverage
        assert cov["fields"]["scene_theme"]["source_field"] == "theme"
        assert cov["fields"]["label"]["source_field"] == "name"

    def test_tag_alias_maps_to_canonical_tags(self):
        """The alias 'tag' maps to the canonical family 'tags'."""
        assert resource_prompts.canonical_field_name("tag") == "tags"
        assert resource_prompts.canonical_field_name("tags") == "tags"
        assert resource_prompts.is_canonical_family_alias("tag") is True
        assert resource_prompts.is_canonical_family_alias("tags") is False


class TestCanonicalTranslationValidation:
    def test_rejects_alias_collision_in_translation(self):
        """Supplying both canonical and alias names with conflicting values raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            resource_prompts.canonicalize_translation_dict({
                "theme": "English Theme A",
                "scene_theme": "English Theme B",
            })
        assert "Conflicting translations provided for semantic family 'scene_theme'" in str(exc_info.value)

    def test_canonicalizes_allowed_aliases(self):
        """Allowed aliases are canonicalized cleanly."""
        result = resource_prompts.canonicalize_translation_dict({
            "name": "Authorized Label",
            "theme": "Authorized Theme",
            "tag": ["outdoor", "morning"],
        })
        assert result == {
            "label": "Authorized Label",
            "scene_theme": "Authorized Theme",
            "tags": ["outdoor", "morning"],
        }


class TestTwoPhaseTranslationMap:
    def test_preview_is_read_only_and_issues_attestation_token(self, isolated_db):
        """Preview validates translation map, calculates metrics, and issues token without modifying DB."""
        library_id = resource_store.ensure_library("inv_preview_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            library_id, "inv_room_prev_01",
            {
                "id": "inv_room_prev_01",
                "name": "CHAMBRE_SOLEIL",
                "theme": "THEME_LUMIERE",
            },
            translation=None,
        )

        map_data = {
            "CHAMBRE_SOLEIL": {
                "source": "CHAMBRE_SOLEIL",
                "translation": "Sunlit Room",
                "fields": ["name"],
            },
            "THEME_LUMIERE": {
                "source": "THEME_LUMIERE",
                "translation": "Warm morning light across pale walls",
                "fields": ["theme"],
            },
        }

        # Check DB before preview
        before_rev = resource_store.get_revision(revision_id=rev_id)
        assert before_rev["translation"] == {}

        preview = resource_translation.preview_translation_map("inv_preview_lib", map_data)
        assert preview["library_key"] == "inv_preview_lib"
        assert preview["total_revisions"] == 1
        assert preview["matched_revisions"] == 1
        assert preview["would_update"] == 1
        assert preview["would_be_ready"] == 1
        assert preview["would_remain_pending"] == 0
        assert preview["unmatched_map_entries"] == 0
        assert "attestation_token" in preview
        assert preview["expires_at"] > time.time()

        # Check DB after preview - strictly unchanged!
        after_rev = resource_store.get_revision(revision_id=rev_id)
        assert after_rev["translation"] == {}

    def test_apply_translation_map_updates_database_atomically(self, isolated_db):
        """Applying the map with valid attestation token updates translation sidecars atomically."""
        library_id = resource_store.ensure_library("inv_apply_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            library_id, "inv_room_apply_01",
            {
                "id": "inv_room_apply_01",
                "name": "CHAMBRE_SOLEIL",
                "theme": "THEME_LUMIERE",
            },
            translation=None,
        )

        map_data = {
            "CHAMBRE_SOLEIL": {
                "source": "CHAMBRE_SOLEIL",
                "translation": "Sunlit Room",
                "fields": ["name"],
            },
            "THEME_LUMIERE": {
                "source": "THEME_LUMIERE",
                "translation": "Warm morning light across pale walls",
                "fields": ["theme"],
            },
        }

        preview = resource_translation.preview_translation_map("inv_apply_lib", map_data)
        token = preview["attestation_token"]

        result = resource_translation.apply_translation_map("inv_apply_lib", map_data, token)
        assert result["updated"] == 1
        assert result["ready"] == 1
        assert result["pending"] == 0

        # Check DB row
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None
        assert rev["translation"] == {
            "label": "Sunlit Room",
            "scene_theme": "Warm morning light across pale walls",
        }
        # Payload remains strictly immutable!
        assert rev["payload"]["name"] == "CHAMBRE_SOLEIL"
        assert rev["payload"]["theme"] == "THEME_LUMIERE"

        # Readiness is now ready
        report = resource_readiness.evaluate_revision_readiness(
            library_id, "inv_room_apply_01", content_digest=rev["content_digest"],
        )
        assert report.is_ready

    def test_apply_is_idempotent(self, isolated_db):
        """Re-applying the translation map without changes reports updated=0, unchanged=N."""
        library_id = resource_store.ensure_library("inv_idem_lib", kind="rooms")
        resource_store.record_revision(
            library_id, "inv_room_idem",
            {"id": "inv_room_idem", "name": "SRC_LABEL", "theme": "SRC_THEME"},
            translation=None,
        )
        map_data = {
            "SRC_LABEL": {"source": "SRC_LABEL", "translation": "Label", "fields": ["name"]},
            "SRC_THEME": {"source": "SRC_THEME", "translation": "Theme", "fields": ["theme"]},
        }

        # First run
        p1 = resource_translation.preview_translation_map("inv_idem_lib", map_data)
        r1 = resource_translation.apply_translation_map("inv_idem_lib", map_data, p1["attestation_token"])
        assert r1["updated"] == 1

        # Second run
        p2 = resource_translation.preview_translation_map("inv_idem_lib", map_data)
        r2 = resource_translation.apply_translation_map("inv_idem_lib", map_data, p2["attestation_token"])
        assert r2["updated"] == 0
        assert r2["unchanged"] == 1

    def test_invalid_translation_map_aborts_without_database_writes(self, isolated_db):
        """Invalid map (non-English translation or missing fields) aborts with ValueError and no DB writes."""
        library_id = resource_store.ensure_library("inv_invalid_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            library_id, "inv_room_inv",
            {"id": "inv_room_inv", "name": "SRC_A", "theme": "SRC_B"},
            translation=None,
        )

        bad_map = {
            "SRC_A": {
                "source": "SRC_A",
                "translation": "\u65e5\u672c\u8a9e",  # Non-English!
                "fields": ["name"],
            },
        }

        with pytest.raises(ValueError) as exc_info:
            resource_translation.preview_translation_map("inv_invalid_lib", bad_map)
        assert "contains non-English characters" in str(exc_info.value)

        # Ensure database is clean
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev["translation"] == {}

    def test_unmatched_map_entries_are_reported_without_error(self, isolated_db):
        """Unmatched entries in a valid translation map do not cause failure."""
        library_id = resource_store.ensure_library("inv_unmatched_lib", kind="rooms")
        resource_store.record_revision(
            library_id, "inv_room_unm",
            {"id": "inv_room_unm", "name": "SRC_MATCH", "theme": "SRC_THEME"},
            translation=None,
        )

        map_data = {
            "SRC_MATCH": {"source": "SRC_MATCH", "translation": "Matched Label", "fields": ["name"]},
            "SRC_THEME": {"source": "SRC_THEME", "translation": "Matched Theme", "fields": ["theme"]},
            "SRC_EXTRA": {"source": "SRC_EXTRA", "translation": "Unmatched Label", "fields": ["name"]},
        }

        preview = resource_translation.preview_translation_map("inv_unmatched_lib", map_data)
        assert preview["unmatched_map_entries"] == 1
        assert preview["matched_revisions"] == 1

        result = resource_translation.apply_translation_map(
            "inv_unmatched_lib", map_data, preview["attestation_token"],
        )
        assert result["updated"] == 1


class TestAttestationAndTOCTOUProtection:
    def test_apply_detects_library_drift_and_raises_conflict(self, isolated_db):
        """When revisions in library change after preview, apply raises TranslationConflictError."""
        library_id = resource_store.ensure_library("inv_toctou_lib", kind="rooms")
        resource_store.record_revision(
            library_id, "inv_room_t1",
            {"id": "inv_room_t1", "name": "ROOM_A", "theme": "THEME_A"},
            translation=None,
        )
        map_data = {
            "ROOM_A": {"source": "ROOM_A", "translation": "Room A", "fields": ["name"]},
            "THEME_A": {"source": "THEME_A", "translation": "Theme A", "fields": ["theme"]},
        }

        preview = resource_translation.preview_translation_map("inv_toctou_lib", map_data)
        token = preview["attestation_token"]

        # Interleaved mutation: add another revision to the library
        resource_store.record_revision(
            library_id, "inv_room_t2",
            {"id": "inv_room_t2", "name": "ROOM_B", "theme": "THEME_B"},
            translation=None,
        )

        with pytest.raises(resource_translation.TranslationConflictError) as exc_info:
            resource_translation.apply_translation_map("inv_toctou_lib", map_data, token)
        assert "changed since preview" in str(exc_info.value)

    def test_apply_detects_map_drift_and_raises_conflict(self, isolated_db):
        """When translation map content differs from the previewed one, apply raises TranslationConflictError."""
        library_id = resource_store.ensure_library("inv_map_drift_lib", kind="rooms")
        resource_store.record_revision(
            library_id, "inv_room_md",
            {"id": "inv_room_md", "name": "ROOM_A", "theme": "THEME_A"},
            translation=None,
        )
        map_preview = {
            "ROOM_A": {"source": "ROOM_A", "translation": "Room A", "fields": ["name"]},
            "THEME_A": {"source": "THEME_A", "translation": "Theme A", "fields": ["theme"]},
        }
        preview = resource_translation.preview_translation_map("inv_map_drift_lib", map_preview)
        token = preview["attestation_token"]

        # Alter the map before apply
        map_modified = {
            "ROOM_A": {"source": "ROOM_A", "translation": "Modified Room A", "fields": ["name"]},
            "THEME_A": {"source": "THEME_A", "translation": "Theme A", "fields": ["theme"]},
        }

        with pytest.raises(resource_translation.TranslationConflictError) as exc_info:
            resource_translation.apply_translation_map("inv_map_drift_lib", map_modified, token)
        assert "changed since preview" in str(exc_info.value)

    def test_invalid_or_expired_token_raises_value_error(self, isolated_db):
        """Tampered or invalid attestation token raises ValueError."""
        library_id = resource_store.ensure_library("inv_bad_token_lib", kind="rooms")
        map_data = {
            "A": {"source": "A", "translation": "Alpha", "fields": ["name"]},
        }
        with pytest.raises(ValueError) as exc_info:
            resource_translation.apply_translation_map("inv_bad_token_lib", map_data, "bad.token")
        assert "signature verification failed" in str(exc_info.value)


class TestSingleRevisionTranslation:
    def test_apply_revision_translation_validates_and_merges(self, isolated_db):
        """Updating single revision translation validates English, merges, and updates readiness."""
        library_id = resource_store.ensure_library("inv_single_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            library_id, "inv_room_s1",
            {"id": "inv_room_s1", "name": "SRC_NAME", "theme": "SRC_THEME"},
            translation={"label": "Initial English Label"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None

        res = resource_translation.apply_revision_translation(
            "inv_single_lib", "inv_room_s1", rev["content_digest"],
            {"theme": "Newly Added Theme"},  # Uses alias 'theme'
        )
        assert res["is_ready"] is True
        # Merged and canonicalized!
        assert res["translation"] == {
            "label": "Initial English Label",
            "scene_theme": "Newly Added Theme",
        }

    def test_apply_revision_translation_rejects_non_english(self, isolated_db):
        """Single revision translation rejects non-English input."""
        library_id = resource_store.ensure_library("inv_single_bad", kind="rooms")
        rev_id = resource_store.record_revision(
            library_id, "inv_room_s2",
            {"id": "inv_room_s2", "name": "SRC_NAME", "theme": "SRC_THEME"},
            translation=None,
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None

        with pytest.raises(ValueError) as exc_info:
            resource_translation.apply_revision_translation(
                "inv_single_bad", "inv_room_s2", rev["content_digest"],
                {"label": "\u30c6\u30b9\u30c8"},
            )
        assert "not valid English" in str(exc_info.value)


class TestHistoricalPromptsImmutable:
    def test_finalized_take_prompt_remains_immutable_after_translation_update(self, isolated_db):
        """Finalized take preparations record an immutable snapshot that is never mutated
        by subsequent translation updates."""
        sid = _create_test_session(isolated_db)
        library_id = resource_store.ensure_library("inv_hist_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            library_id, "inv_room_hist",
            {
                "id": "inv_room_hist",
                "label": "Old Label",
                "scene_theme": "A quiet vintage studio room",
            },
            translation={
                "label": "Old Label",
                "scene_theme": "A quiet vintage studio room",
            },
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None

        plan = {
            "version": "resource-v1",
            "look": INV_LOOK,
            "initial_wardrobe": INV_WARDROBE,
            "takes": [{
                "take_id": "take-001",
                "camera": "35mm",
                "framing": "medium",
                "pose": "standing",
                "expression": "neutral",
            }],
            "selected_resources": [{
                "library_key": "inv_hist_lib",
                "source_id": "inv_room_hist",
                "content_digest": rev["content_digest"],
            }],
            "wardrobe_changes": [],
        }
        session_plan.save_draft(sid, plan, expected_revision=0)

        # Finalize take preparation
        snapshot = resource_preparation.finalize_take_preparation(sid, 1, "take-001")
        original_prompt = snapshot["final_prompt"]
        assert "A quiet vintage studio room" in original_prompt

        # Now update the translation sidecar for that revision
        resource_translation.apply_revision_translation(
            "inv_hist_lib", "inv_room_hist", rev["content_digest"],
            {"scene_theme": "A totally renovated futuristic cyber studio"},
        )

        # The finalized snapshot in session_plan must remain 100% byte-for-byte unchanged!
        saved_snapshot = resource_preparation.finalize_take_preparation(sid, 1, "take-001")
        assert saved_snapshot is not None
        assert saved_snapshot["final_prompt"] == original_prompt
        assert "futuristic cyber studio" not in saved_snapshot["final_prompt"]

        row = db.one(
            "SELECT final_prompt FROM prepared_take "
            "WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
            sid, 1, "take-001",
        )
        assert row is not None
        assert row["final_prompt"] == original_prompt


class TestTranslationApiRoutes:
    def test_preview_and_apply_endpoints_round_trip(self, client):
        lib_id = resource_store.ensure_library("api_preview_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "api_room_01",
            {"id": "api_room_01", "name": "SALLE_A", "theme": "THEME_A"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None

        map_data = {
            "SALLE_A": {"source": "SALLE_A", "translation": "Room Alpha", "fields": ["name"]},
            "THEME_A": {"source": "THEME_A", "translation": "Theme Alpha", "fields": ["theme"]},
        }

        # 1. Preview
        resp = client.post(
            "/api/resources/libraries/api_preview_lib/translations/preview",
            json={"translation_map": map_data},
        )
        assert resp.status_code == 200
        preview = resp.json()
        assert preview["matched_revisions"] == 1
        assert preview["would_update"] == 1
        assert "attestation_token" in preview
        token = preview["attestation_token"]

        # 2. Apply
        apply_resp = client.post(
            "/api/resources/libraries/api_preview_lib/translations/apply",
            json={"translation_map": map_data, "attestation_token": token},
        )
        assert apply_resp.status_code == 200
        applied = apply_resp.json()
        assert applied["updated"] == 1
        assert applied["ready"] == 1

        # 3. Check DB
        updated_rev = resource_store.get_revision(revision_id=rev_id)
        assert updated_rev["translation"] == {
            "label": "Room Alpha",
            "scene_theme": "Theme Alpha",
        }

    def test_source_rows_use_bulk_attestation_and_preserve_bulk_authorization(
        self, client, monkeypatch,
    ):
        lib_id = resource_store.ensure_library("manual_rows_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "manual-room",
            {"id": "manual-room", "name": "SALLE_M", "theme": "THEME_M"},
        )

        def forbidden_shortcut(*args, **kwargs):
            raise AssertionError("manual rows must not call apply_revision_translation")

        monkeypatch.setattr(
            backend_resource_translation, "apply_revision_translation", forbidden_shortcut,
        )
        rows_response = client.get(
            "/api/resources/libraries/manual_rows_lib/translations/rows"
        )
        assert rows_response.status_code == 200
        translations = {"label": "Manual Room", "scene_theme": "Manual Theme"}
        map_data = {
            row["source_value"]: {
                "source": row["source_value"],
                "translation": translations[row["field"]],
                "fields": [row["field"]],
            }
            for row in rows_response.json()["rows"]
            if row["required"]
        }

        preview = client.post(
            "/api/resources/libraries/manual_rows_lib/translations/preview",
            json={"translation_map": map_data},
        )
        assert preview.status_code == 200
        applied = client.post(
            "/api/resources/libraries/manual_rows_lib/translations/apply",
            json={
                "translation_map": map_data,
                "attestation_token": preview.json()["attestation_token"],
            },
        )
        assert applied.status_code == 200
        assert resource_store.get_revision(revision_id=rev_id)["translation"] == translations

        before = resource_store.get_revision(revision_id=rev_id)["translation"]
        forged = client.post(
            "/api/resources/libraries/manual_rows_lib/translations/preview",
            json={
                "translation_map": {
                    "SALLE_M": {
                        "source": "SALLE_M",
                        "translation": "Forged",
                        "fields": ["weight"],
                    },
                },
            },
        )
        assert forged.status_code == 422
        assert "only descriptive input fields" in forged.json()["detail"]
        assert resource_store.get_revision(revision_id=rev_id)["translation"] == before

    def test_preview_missing_library_returns_404(self, client):
        resp = client.post(
            "/api/resources/libraries/non_existent_lib/translations/preview",
            json={"translation_map": {}},
        )
        assert resp.status_code == 404

    def test_preview_invalid_map_returns_422(self, client):
        lib_id = resource_store.ensure_library("api_err_lib", kind="rooms")
        resp = client.post(
            "/api/resources/libraries/api_err_lib/translations/preview",
            json={"translation_map": "not valid json and not a file"},
        )
        assert resp.status_code == 422

    def test_apply_tampered_token_returns_422(self, client):
        lib_id = resource_store.ensure_library("api_bad_token_lib", kind="rooms")
        resp = client.post(
            "/api/resources/libraries/api_bad_token_lib/translations/apply",
            json={"translation_map": {}, "attestation_token": "invalid.token"},
        )
        assert resp.status_code == 422

    def test_single_revision_translation_endpoint(self, client):
        lib_id = resource_store.ensure_library("api_single_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "api_single_01",
            {"id": "api_single_01", "name": "SALLE_S", "theme": "THEME_S"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev is not None

        resp = client.post(
            f"/api/resources/revisions/api_single_lib/api_single_01/{rev['content_digest']}/translation",
            json={"translation": {"name": "Single Room Label", "theme": "Single Room Theme"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_ready"] is True
        assert data["translation"] == {
            "label": "Single Room Label",
            "scene_theme": "Single Room Theme",
        }


class TestCorrectiveHardening:
    def test_attestation_key_scoped_to_active_db_and_isolates_directories(self, tmp_path):
        """Private key .resource-translation-preview-key is scoped to active db directory.
        Tokens from different databases/directories cannot cross-validate.
        """
        dir1 = tmp_path / "db1"
        dir1.mkdir()
        db1_path = dir1 / "test1.db"

        dir2 = tmp_path / "db2"
        dir2.mkdir()
        db2_path = dir2 / "test2.db"

        # 1. Preview in DB1
        _open(db1_path)
        lib1 = resource_store.ensure_library("key_scope_lib", kind="rooms")
        resource_store.record_revision(lib1, "s1", {"id": "s1", "label": "SRC", "scene_theme": "THM"})
        map1 = {"SRC": {"source": "SRC", "translation": "Room Alpha", "fields": ["label"]}}
        prev1 = resource_translation.preview_translation_map("key_scope_lib", map1)
        token1 = prev1["attestation_token"]
        key1_path = dir1 / ".resource-translation-preview-key"
        assert key1_path.is_file()
        _close_silently()

        # 2. Preview in DB2
        _open(db2_path)
        lib2 = resource_store.ensure_library("key_scope_lib", kind="rooms")
        resource_store.record_revision(lib2, "s1", {"id": "s1", "label": "SRC", "scene_theme": "THM"})
        prev2 = resource_translation.preview_translation_map("key_scope_lib", map1)
        key2_path = dir2 / ".resource-translation-preview-key"
        assert key2_path.is_file()
        assert key1_path.read_bytes() != key2_path.read_bytes()

        # Attempt to apply token1 in DB2 -> signature verification fails
        with pytest.raises(ValueError) as exc:
            resource_translation.apply_translation_map("key_scope_lib", map1, token1)
        assert "signature verification failed" in str(exc.value)
        _close_silently()

    def test_old_static_salt_token_fails_verification(self, isolated_db):
        """Tokens signed with old static salt are rejected."""
        import base64
        import hmac
        lib = resource_store.ensure_library("salt_lib", kind="rooms")
        resource_store.record_revision(lib, "s1", {"id": "s1", "label": "SRC", "scene_theme": "THM"})
        map_data = {"SRC": {"source": "SRC", "translation": "Room Alpha", "fields": ["label"]}}
        # Create key so key exists
        resource_translation.preview_translation_map("salt_lib", map_data)

        payload_data = {
            "library_key": "salt_lib",
            "library_id": lib,
            "library_kind": "rooms",
            "library_created_at": "2026-09-10T00:00:00Z",
            "map_digest": "dummy",
            "library_fingerprint": "dummy",
            "exp": int(time.time()) + 3600,
        }
        b64 = base64.urlsafe_b64encode(json.dumps(payload_data).encode()).decode().rstrip("=")
        old_sig = hmac.new(b"idevgen-resource-translation-attestation-v1", b64.encode(), "sha256").hexdigest()
        old_token = f"{b64}.{old_sig}"

        with pytest.raises(ValueError) as exc:
            resource_translation.apply_translation_map("salt_lib", map_data, old_token)
        assert "signature verification failed" in str(exc.value)

    def test_metadata_drift_raises_409_before_map_field_authorization(self, isolated_db):
        """If library metadata drifts, 409 TranslationConflictError is raised BEFORE 422 map check."""
        lib = resource_store.ensure_library("drift_lib", kind="rooms")
        resource_store.record_revision(lib, "s1", {"id": "s1", "label": "SRC", "scene_theme": "THM"})
        # Map is valid for rooms (label is descriptive_input for rooms)
        map_data = {"SRC": {"source": "SRC", "translation": "Room Alpha", "fields": ["label"]}}
        prev = resource_translation.preview_translation_map("drift_lib", map_data)
        token = prev["attestation_token"]

        # Before apply, change library kind to fused_scenes where 'label' is NOT descriptive_input
        db.run("UPDATE resource_library SET kind = 'fused_scenes' WHERE id = ?", lib)

        with pytest.raises(resource_translation.TranslationConflictError) as exc:
            resource_translation.apply_translation_map("drift_lib", map_data, token)
        assert "metadata changed" in str(exc.value)

    def test_raw_alias_mutation_raises_409_conflict(self, isolated_db):
        """Changing raw translation sidecar JSON in DB invalidates the preview fingerprint (409)."""
        lib = resource_store.ensure_library("raw_alias_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib, "s1",
            {"id": "s1", "label": "SRC", "scene_theme": "THM"},
            translation={"name": "Alpha"},
        )
        map_data = {"THM": {"source": "THM", "translation": "Theme Alpha", "fields": ["scene_theme"]}}
        prev = resource_translation.preview_translation_map("raw_alias_lib", map_data)
        token = prev["attestation_token"]

        # Mutate raw JSON in DB to use 'label' instead of 'name' (same canonical family, different raw text)
        db.run("UPDATE asset_revision SET translation = ? WHERE id = ?", json.dumps({"label": "Alpha"}), rev_id)

        with pytest.raises(resource_translation.TranslationConflictError) as exc:
            resource_translation.apply_translation_map("raw_alias_lib", map_data, token)
        assert "state or translation map changed" in str(exc.value)

    def test_stale_coverage_repair_on_apply_preserves_preview_counts(self, isolated_db):
        """Coverage changes do NOT alter raw fingerprint; Apply repairs stale coverage while updated==would_update."""
        lib = resource_store.ensure_library("stale_cov_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib, "s1",
            {"id": "s1", "label": "SRC", "scene_theme": "THM"},
            translation={"label": "Room Alpha", "scene_theme": "Theme Alpha"},
        )
        # Corrupt / make coverage stale
        db.run("UPDATE asset_revision SET coverage = ? WHERE id = ?", json.dumps({"status": "pending"}), rev_id)

        map_data = {"XYZ": {"source": "XYZ", "translation": "Something", "fields": ["label"]}}
        prev = resource_translation.preview_translation_map("stale_cov_lib", map_data)
        assert prev["would_update"] == 0
        assert prev["unchanged"] == 1
        assert prev["would_be_ready"] == 1

        applied = resource_translation.apply_translation_map("stale_cov_lib", map_data, prev["attestation_token"])
        assert applied["updated"] == 0  # would_update == updated
        assert applied["unchanged"] == 1
        assert applied["ready"] == 1

        # Check that stored coverage was repaired in DB
        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev["coverage"]["missing_translations"] == []

    def test_upfront_map_field_authorization(self, isolated_db):
        """Map entry with non-descriptive field is rejected up front with ValueError."""
        lib = resource_store.ensure_library("upfront_lib", kind="rooms")
        resource_store.record_revision(lib, "s1", {"id": "s1", "label": "SRC", "scene_theme": "THM"})

        # 'weight' is selection_metadata, not descriptive_input
        bad_map = {"SRC": {"source": "SRC", "translation": "Heavy", "fields": ["weight"]}}
        with pytest.raises(ValueError) as exc:
            resource_translation.preview_translation_map("upfront_lib", bad_map)
        assert "selection_metadata" in str(exc.value)
        assert "only descriptive input fields" in str(exc.value)

    def test_inspect_translation_sidecar_pure_and_non_throwing(self):
        """inspect_translation_sidecar does not throw on invalid/unmapped/conflicting fields."""
        payload = {"label": "Habitación", "scene_theme": "Tema"}

        # 1. Disallowed field
        insp1 = resource_readiness.inspect_translation_sidecar(
            "rooms", payload, {"label": "Room", "weight": "Heavy"}
        )
        assert not insp1.is_valid
        assert any("weight" in e and "selection_metadata" in e for e in insp1.errors)
        assert "weight" in insp1.invalid_families

        # 2. Unmapped field
        insp2 = resource_readiness.inspect_translation_sidecar(
            "rooms", payload, {"label": "Room", "unknown_key": "val"}
        )
        assert not insp2.is_valid
        assert any("unknown_key" in e and "unmapped" in e for e in insp2.errors)

        # 3. Conflicting aliases
        insp3 = resource_readiness.inspect_translation_sidecar(
            "rooms", payload, {"label": "Room A", "name": "Room B"}
        )
        assert not insp3.is_valid
        assert any("Conflicting alias entries" in e for e in insp3.errors)

        # 4. Equal duplicate aliases -> valid!
        insp4 = resource_readiness.inspect_translation_sidecar(
            "rooms", payload, {"label": "Room A", "name": "Room A"}
        )
        assert insp4.is_valid
        assert insp4.canonical_translation["label"] == "Room A"

        # 5. List shape mismatch
        insp5 = resource_readiness.inspect_translation_sidecar(
            "rooms", payload, {"label": ["Room", "Alpha"]}
        )
        assert not insp5.is_valid
        assert any("shape mismatch" in e for e in insp5.errors)

    def test_optional_list_matching_semantics(self):
        """List-valued optional fields: omission on untranslated non-English, candidate on clean English, no candidate if no match."""
        # Case A: at least one match + other items already English -> candidate generated
        payload_a = {"label": "Hab", "scene_theme": "Thm", "tags": ["terraza", "sunny"]}
        map_a = {"terraza": {"source": "terraza", "translation": "terrace", "fields": ["tags"]}}
        matches_a, matched_srcs_a = resource_translation.match_translation_map(payload_a, map_a, "rooms")
        assert matches_a.get("tags") == ["terrace", "sunny"]
        assert "terraza" in matched_srcs_a

        # Case B: at least one match + other items untranslated non-English (CJK) -> candidate omitted, matched source tracked
        payload_b = {"label": "Hab", "scene_theme": "Thm", "tags": ["terraza", "\u65e5\u5149"]}
        matches_b, matched_srcs_b = resource_translation.match_translation_map(payload_b, map_a, "rooms")
        assert "tags" not in matches_b
        assert "terraza" in matched_srcs_b

        # Case C: all items English, but NO map match -> candidate NOT generated
        payload_c = {"label": "Hab", "scene_theme": "Thm", "tags": ["balcony", "sunny"]}
        map_c = {"other": {"source": "other", "translation": "other", "fields": ["tags"]}}
        matches_c, matched_srcs_c = resource_translation.match_translation_map(payload_c, map_c, "rooms")
        assert "tags" not in matches_c
        assert len(matched_srcs_c) == 0

    def test_single_revision_atomic_validation(self, isolated_db):
        """Single revision translation update rejects unauthorized fields and accepts equal aliases."""
        lib = resource_store.ensure_library("single_atom_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib, "s1",
            {"id": "s1", "label": "Hab", "scene_theme": "Thm"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)

        # 1. Reject disallowed field
        with pytest.raises(ValueError) as exc1:
            resource_translation.apply_revision_translation(
                "single_atom_lib", "s1", rev["content_digest"],
                {"label": "Room", "weight": "Heavy"},
            )
        assert "selection_metadata" in str(exc1.value)

        # 2. Reject conflicting aliases in update
        with pytest.raises(ValueError) as exc2:
            resource_translation.apply_revision_translation(
                "single_atom_lib", "s1", rev["content_digest"],
                {"label": "Room A", "name": "Room B"},
            )
        assert "Conflicting alias entries" in str(exc2.value)

        # 3. Accept equal duplicate aliases
        res3 = resource_translation.apply_revision_translation(
            "single_atom_lib", "s1", rev["content_digest"],
            {"label": "Room A", "name": "Room A", "scene_theme": "Theme A"},
        )
        assert res3["is_ready"] is True
        assert res3["translation"]["label"] == "Room A"

    def test_preview_feature_flag_503(self, client, monkeypatch):
        """Preview route returns 503 when resource planning feature flag is disabled."""
        import main
        monkeypatch.setattr(main, "is_resource_planning_enabled", lambda: False)
        resp = client.post(
            "/api/resources/libraries/any_lib/translations/preview",
            json={"translation_map": {}},
        )
        assert resp.status_code == 503
        assert "Resource planning is disabled" in resp.json()["detail"]

    def test_preview_changes_zero_database_state_and_reuses_signing_key(self, tmp_path):
        """Preview changes zero resource/database state while permitting first-key file creation,
        and reconnect to the same DB directory reuses the translation signing key."""
        db_path = tmp_path / "test_zero_state.db"
        _open(db_path)
        lib_id = resource_store.ensure_library("zero_state_lib", kind="rooms")
        resource_store.record_revision(
            lib_id, "s1", {"id": "s1", "label": "SRC", "scene_theme": "THM"}
        )
        map_data = {"SRC": {"source": "SRC", "translation": "Room Alpha", "fields": ["label"]}}

        key_file = tmp_path / ".resource-translation-preview-key"
        assert not key_file.exists()

        before_lib = db.q("SELECT * FROM resource_library")
        before_rev = db.q("SELECT * FROM asset_revision")

        prev = resource_translation.preview_translation_map("zero_state_lib", map_data)
        assert prev["would_update"] == 1
        assert key_file.is_file()
        key_bytes = key_file.read_bytes()
        assert len(key_bytes) == 32

        after_lib = db.q("SELECT * FROM resource_library")
        after_rev = db.q("SELECT * FROM asset_revision")
        assert before_lib == after_lib
        assert before_rev == after_rev

        _close_silently()
        _open(db_path)
        prev2 = resource_translation.preview_translation_map("zero_state_lib", map_data)
        assert key_file.read_bytes() == key_bytes
        _close_silently()

    def test_required_descriptive_fields_as_lists_fail_closed(self, isolated_db):
        """Required descriptive fields (label, scene_theme, prompt) as lists must fail closed
        both when translation map matches and when no map entry matches."""
        # 1. rooms: label as list
        payload_label_match = {"id": "r1", "label": ["SRC_LABEL"], "scene_theme": "Valid Theme"}
        map_match_label = {"SRC_LABEL": {"source": "SRC_LABEL", "translation": "Room One", "fields": ["label"]}}
        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_label_match, map_match_label, "rooms")
        assert "Required field 'label'" in str(exc.value)
        assert "must be a scalar string" in str(exc.value)

        map_no_match = {"OTHER": {"source": "OTHER", "translation": "Other", "fields": ["label"]}}
        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_label_match, map_no_match, "rooms")
        assert "Required field 'label'" in str(exc.value)
        assert "must be a scalar string" in str(exc.value)

        # 2. rooms: scene_theme as list
        payload_theme_match = {"id": "r2", "label": "Valid Room", "scene_theme": ["SRC_THEME"]}
        map_match_theme = {"SRC_THEME": {"source": "SRC_THEME", "translation": "Theme One", "fields": ["scene_theme"]}}
        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_theme_match, map_match_theme, "rooms")
        assert "Required field 'scene_theme'" in str(exc.value)
        assert "must be a scalar string" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_theme_match, map_no_match, "rooms")
        assert "Required field 'scene_theme'" in str(exc.value)
        assert "must be a scalar string" in str(exc.value)

        # 3. fused_scenes: prompt as list
        payload_prompt_match = {"id": "fs1", "prompt": ["SRC_PROMPT"]}
        map_match_prompt = {"SRC_PROMPT": {"source": "SRC_PROMPT", "translation": "Prompt One", "fields": ["prompt"]}}
        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_prompt_match, map_match_prompt, "fused_scenes")
        assert "Required field 'prompt'" in str(exc.value)
        assert "must be a scalar string" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_prompt_match, map_no_match, "fused_scenes")
        assert "Required field 'prompt'" in str(exc.value)
        assert "must be a scalar string" in str(exc.value)

    def test_optional_descriptive_lists_structural_prevalidation(self, isolated_db):
        """Optional descriptive list fields must fail closed if any item is not a string,
        both when a map entry matches another item and when no map entry matches."""
        # 1. ["chair", 4]
        payload_int = {"id": "r1", "label": "Room", "scene_theme": "Theme", "tags": ["chair", 4]}
        map_chair = {"chair": {"source": "chair", "translation": "chair", "fields": ["tags"]}}
        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_int, map_chair, "rooms")
        assert "contains non-string items; source lists must be list[str]" in str(exc.value)

        map_other = {"other": {"source": "other", "translation": "other", "fields": ["tags"]}}
        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_int, map_other, "rooms")
        assert "contains non-string items; source lists must be list[str]" in str(exc.value)

        # 2. ["chair", {"name": "table"}]
        payload_dict = {"id": "r1", "label": "Room", "scene_theme": "Theme", "tags": ["chair", {"name": "table"}]}
        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_dict, map_chair, "rooms")
        assert "contains non-string items; source lists must be list[str]" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_dict, map_other, "rooms")
        assert "contains non-string items; source lists must be list[str]" in str(exc.value)

        # 3. [None, "lamp"]
        payload_none = {"id": "r1", "label": "Room", "scene_theme": "Theme", "tags": [None, "lamp"]}
        map_lamp = {"lamp": {"source": "lamp", "translation": "lamp", "fields": ["tags"]}}
        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_none, map_lamp, "rooms")
        assert "contains non-string items; source lists must be list[str]" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            resource_translation.match_translation_map(payload_none, map_other, "rooms")
        assert "contains non-string items; source lists must be list[str]" in str(exc.value)

        # 4. validate_translation_value_for_source with non-string source list
        with pytest.raises(ValueError) as exc:
            resource_readiness.validate_translation_value_for_source(
                "rooms", {"tags": [1, 2]}, "tags", ["One", "Two"]
            )
        assert "contains non-string elements; only list[str] sources can be translated" in str(exc.value)

        with pytest.raises(ValueError) as exc:
            resource_readiness.validate_translation_value_for_source(
                "rooms", {"tags": ["chair", {"name": "table"}]}, "tags", ["Chair", "Table"]
            )
        assert "contains non-string elements; only list[str] sources can be translated" in str(exc.value)

    def test_translation_map_input_shapes_and_literal_keys(self, isolated_db):
        """Direct dict, direct list, duplicate source rejection, literal source keys 'items' and 'translation_map'."""
        lib = resource_store.ensure_library("map_shapes_lib", kind="rooms")
        resource_store.record_revision(
            lib, "s1",
            {"id": "s1", "label": "SRC", "scene_theme": "THM", "description": "items"},
        )

        # 1. Historical direct dictionary map
        dict_map = {
            "SRC": {"source": "SRC", "translation": "Room Alpha", "fields": ["label"]},
            "THM": {"source": "THM", "translation": "Theme Alpha", "fields": ["scene_theme"]},
        }
        normalized_dict = resource_translation.normalize_translation_map_input(dict_map)
        assert len(normalized_dict) == 2
        prev_dict = resource_translation.preview_translation_map("map_shapes_lib", dict_map)
        assert prev_dict["would_update"] == 1

        # 2. Current supported direct list map
        list_map = [
            {"source": "SRC", "translation": "Room Alpha", "fields": ["label"]},
            {"source": "THM", "translation": "Theme Alpha", "fields": ["scene_theme"]},
        ]
        normalized_list = resource_translation.normalize_translation_map_input(list_map)
        assert len(normalized_list) == 2

        # 3. Duplicate source in list map is rejected
        dup_list = [
            {"source": "SRC", "translation": "Invalid Target", "fields": ["weight"]},
            {"source": "SRC", "translation": "Valid Target", "fields": ["label"]},
        ]
        with pytest.raises(ValueError) as exc:
            resource_translation.normalize_translation_map_input(dup_list)
        assert "Duplicate source entry 'SRC' in translation map at index 1" in str(exc.value)

        # 4. Literal source "items" works as normal direct map
        items_map = {
            "items": {"source": "items", "translation": "Items Description", "fields": ["description"]},
        }
        normalized_items = resource_translation.normalize_translation_map_input(items_map)
        assert "items" in normalized_items
        assert normalized_items["items"]["translation"] == "Items Description"

        # 5. Literal source "translation_map" as sole entry
        tmap_map = {
            "translation_map": {"source": "translation_map", "translation": "Translation map", "fields": ["description"]},
        }
        normalized_tmap = resource_translation.normalize_translation_map_input(tmap_map)
        assert "translation_map" in normalized_tmap
        assert normalized_tmap["translation_map"]["translation"] == "Translation map"

        # 6. Literal source "translation_map" with sibling entries
        tmap_sibling_map = {
            "translation_map": {"source": "translation_map", "translation": "Translation map", "fields": ["description"]},
            "SRC": {"source": "SRC", "translation": "Room Alpha", "fields": ["label"]},
        }
        normalized_sibling = resource_translation.normalize_translation_map_input(tmap_sibling_map)
        assert len(normalized_sibling) == 2
        assert "translation_map" in normalized_sibling
        assert "SRC" in normalized_sibling

    def test_deleted_library_after_preview_raises_409_conflict(self, isolated_db, client):
        """Deleting a library between Preview and Apply raises TranslationConflictError (HTTP 409)."""
        lib_id = resource_store.ensure_library("del_lib", kind="rooms")
        resource_store.record_revision(
            lib_id, "s1", {"id": "s1", "label": "SRC", "scene_theme": "THM"}
        )
        map_data = {"SRC": {"source": "SRC", "translation": "Room Alpha", "fields": ["label"]}}

        prev = resource_translation.preview_translation_map("del_lib", map_data)
        token = prev["attestation_token"]

        db.run("DELETE FROM resource_library WHERE library_key = 'del_lib'")

        with pytest.raises(resource_translation.TranslationConflictError) as exc:
            resource_translation.apply_translation_map("del_lib", map_data, token)
        assert "deleted since preview" in str(exc.value)

        lib_id2 = resource_store.ensure_library("del_api_lib", kind="rooms")
        resource_store.record_revision(
            lib_id2, "s1", {"id": "s1", "label": "SRC", "scene_theme": "THM"}
        )
        prev_resp = client.post(
            "/api/resources/libraries/del_api_lib/translations/preview",
            json={"translation_map": map_data},
        )
        assert prev_resp.status_code == 200
        api_token = prev_resp.json()["attestation_token"]

        db.run("DELETE FROM resource_library WHERE library_key = 'del_api_lib'")

        apply_resp = client.post(
            "/api/resources/libraries/del_api_lib/translations/apply",
            json={"translation_map": map_data, "attestation_token": api_token},
        )
        assert apply_resp.status_code == 409
        assert "deleted since preview" in apply_resp.json()["detail"]

    def test_mixed_valid_and_invalid_map_rejected_atomically(self, isolated_db):
        """Mixed valid and invalid map fails validation with zero database writes."""
        lib_id = resource_store.ensure_library("atom_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "s1", {"id": "s1", "label": "SRC", "scene_theme": "THM"}
        )
        mixed_map = {
            "SRC": {"source": "SRC", "translation": "Room Alpha", "fields": ["label"]},
            "THM": {"source": "THM", "translation": "Heavy", "fields": ["weight"]},
        }
        with pytest.raises(ValueError):
            resource_translation.preview_translation_map("atom_lib", mixed_map)

        rev = resource_store.get_revision(revision_id=rev_id)
        assert rev["translation"] == {}

    def test_invalid_sidecar_inspectable_in_readiness_but_fails_preparation(self, isolated_db):
        """Invalid sidecar remains inspectable by evaluate_readiness() with sidecar_error,
        but fails closed during preparation with PreparationFieldError."""
        lib_id = resource_store.ensure_library("bad_sidecar_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "s1",
            {"id": "s1", "label": "SRC", "scene_theme": "THM"},
            translation={"weight": "Heavy"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)

        report = resource_readiness.evaluate_readiness("rooms", rev["payload"], rev["translation"])
        assert not report.is_ready
        assert report.coverage.get("sidecar_error") is not None
        assert "weight" in report.coverage["sidecar_error"]

        full_rev = {**rev, "kind": "rooms", "library_key": "bad_sidecar_lib"}
        with pytest.raises(resource_preparation.PreparationFieldError) as exc:
            resource_preparation._prepare_resource(full_rev)
        assert "invalid translation sidecar" in str(exc.value)

    def test_noop_single_revision_update_performs_no_unnecessary_sql_updates(self, isolated_db):
        """Calling apply_revision_translation with identical translation executes no SQL updates."""
        lib_id = resource_store.ensure_library("noop_rev_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "s1",
            {"id": "s1", "label": "SRC", "scene_theme": "THM"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)

        db.run("CREATE TABLE test_rev_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT)")
        db.run(
            "CREATE TRIGGER test_rev_update_trigger AFTER UPDATE ON asset_revision "
            "BEGIN INSERT INTO test_rev_audit (ts) VALUES ('updated'); END;"
        )

        res1 = resource_translation.apply_revision_translation(
            "noop_rev_lib", "s1", rev["content_digest"],
            {"label": "Room Alpha", "scene_theme": "Theme Alpha"},
        )
        assert res1["is_ready"] is True
        audit_count_1 = db.one("SELECT COUNT(*) as cnt FROM test_rev_audit")["cnt"]
        assert audit_count_1 > 0

        res2 = resource_translation.apply_revision_translation(
            "noop_rev_lib", "s1", rev["content_digest"],
            {"label": "Room Alpha", "scene_theme": "Theme Alpha"},
        )
        assert res2["is_ready"] is True
        audit_count_2 = db.one("SELECT COUNT(*) as cnt FROM test_rev_audit")["cnt"]
        assert audit_count_2 == audit_count_1

    def test_bulk_apply_suppresses_redundant_coverage_updates_when_coverage_unchanged(self, isolated_db):
        """Bulk apply updates translation without redundant coverage update when coverage is unchanged,
        and repairs coverage without translation update when coverage is stale."""
        lib_id = resource_store.ensure_library("cov_suppress_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "s1",
            {"id": "s1", "label": "SRC1", "scene_theme": "SRC2"},
            translation={"label": "Room Alpha", "scene_theme": "Theme Alpha"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        # Ensure initial coverage is populated
        report = resource_readiness.evaluate_readiness("rooms", rev["payload"], rev["translation"])
        resource_readiness.set_revision_readiness(rev_id, report.coverage)

        db.run("CREATE TABLE test_audit_trans_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT)")
        db.run("CREATE TABLE test_audit_cov_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT)")
        db.run(
            "CREATE TRIGGER test_audit_trans AFTER UPDATE OF translation ON asset_revision "
            "BEGIN INSERT INTO test_audit_trans_log (ts) VALUES ('trans'); END;"
        )
        db.run(
            "CREATE TRIGGER test_audit_cov AFTER UPDATE OF coverage ON asset_revision "
            "BEGIN INSERT INTO test_audit_cov_log (ts) VALUES ('cov'); END;"
        )

        # 1. Update translation only: change label to Room Beta
        # The coverage fields will remain translated=True and missing_translations=[]
        map_update = {
            "SRC1": {"source": "SRC1", "translation": "Room Beta", "fields": ["label"]},
        }
        prev = resource_translation.preview_translation_map("cov_suppress_lib", map_update)
        assert prev["would_update"] == 1
        res1 = resource_translation.apply_translation_map(
            "cov_suppress_lib", map_update, prev["attestation_token"]
        )
        assert res1["updated"] == 1
        assert res1["unchanged"] == 0

        # Verify translation was updated, but coverage was NOT updated
        trans_count_1 = db.one("SELECT COUNT(*) as cnt FROM test_audit_trans_log")["cnt"]
        cov_count_1 = db.one("SELECT COUNT(*) as cnt FROM test_audit_cov_log")["cnt"]
        assert trans_count_1 == 1
        assert cov_count_1 == 0

        # 2. Tamper coverage to simulate stale coverage repair
        db.run("UPDATE asset_revision SET coverage = '{}' WHERE id = ?", rev_id)
        # Coverage trigger fired once on the manual update
        cov_count_tampered = db.one("SELECT COUNT(*) as cnt FROM test_audit_cov_log")["cnt"]
        assert cov_count_tampered == 1

        # Re-apply same map (translation is unchanged, but coverage is stale)
        prev2 = resource_translation.preview_translation_map("cov_suppress_lib", map_update)
        assert prev2["would_update"] == 0
        res2 = resource_translation.apply_translation_map(
            "cov_suppress_lib", map_update, prev2["attestation_token"]
        )
        assert res2["updated"] == 0
        assert res2["unchanged"] == 1

        # Translation was NOT updated, but coverage WAS repaired
        trans_count_2 = db.one("SELECT COUNT(*) as cnt FROM test_audit_trans_log")["cnt"]
        cov_count_2 = db.one("SELECT COUNT(*) as cnt FROM test_audit_cov_log")["cnt"]
        assert trans_count_2 == 1  # unchanged
        assert cov_count_2 == 2    # incremented by coverage repair


class TestSelectedTranslationMapWorkflow:
    """Acceptance tests for OpenSpec Task 3.1.

    Covers:
      - Selected translation-map preview and apply integration;
      - Server-side byte authority and rejection of client overrides/mixed forms;
      - Cross-selection isolation and target/auxiliary validation;
      - Revision and staged file fingerprint integrity;
      - Descriptive-field authorization, canonical digest, and library fingerprint checks;
      - 10 MiB actual-streamed body limit on bulk preview/apply (missing/false Content-Length);
      - Pre-parse rejection verification (spy count == 0);
      - Zero-write assertions on oversized requests and validation failures;
      - Exact 10 MiB boundary and 10 MiB + 1 byte behavior.
    """

    @staticmethod
    def _create_staged_translation_file(
        client,
        library_key: str,
        map_payload: dict[str, Any],
        auxiliary_kind: str = "translation_map",
        file_name: str = "translations.json",
    ) -> tuple[str, str, int]:
        """Helper to create a selection, upload file, and patch its effective target."""
        req_id = str(uuid.uuid4())
        create_resp = client.post("/api/resources/import-selections", json={"request_id": req_id})
        assert create_resp.status_code == 201
        sid = create_resp.json()["selection_id"]

        raw_bytes = json.dumps(map_payload).encode("utf-8")
        upload_resp = client.post(
            f"/api/resources/import-selections/{sid}/files",
            files={"file": (file_name, io.BytesIO(raw_bytes), "application/octet-stream")},
            data={"upload_id": f"up_{uuid.uuid4().hex}"},
        )
        assert upload_resp.status_code == 201
        upload_data = upload_resp.json()
        fid = upload_data["files"][0]["file_id"]
        rev_after_upload = upload_data["selection_revision"]

        patch_resp = client.patch(
            f"/api/resources/import-selections/{sid}/files/{fid}",
            json={
                "expected_revision": rev_after_upload,
                "effective_library_key": library_key,
                "effective_auxiliary_kind": auxiliary_kind,
            },
        )
        assert patch_resp.status_code == 200
        current_rev = patch_resp.json()["selection_revision"]
        return sid, fid, current_rev

    def test_selected_map_preview_and_apply_round_trip(self, client):
        """Selected translation map reaches bulk preview and apply with zero preview writes."""
        lib_id = resource_store.ensure_library("sel_preview_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id,
            "room_01",
            {"id": "room_01", "name": "SRC_ROOM", "theme": "SRC_THEME"},
        )
        rev_before = resource_store.get_revision(revision_id=rev_id)
        assert rev_before["translation"] == {}

        map_data = {
            "SRC_ROOM": {"source": "SRC_ROOM", "translation": "Sunlit Studio", "fields": ["name"]},
            "SRC_THEME": {"source": "SRC_THEME", "translation": "Warm Palette", "fields": ["theme"]},
        }
        sid, fid, rev = self._create_staged_translation_file(client, "sel_preview_lib", map_data)

        # 1. Preview using selection reference
        prev_resp = client.post(
            "/api/resources/libraries/sel_preview_lib/translations/preview",
            json={"selection_id": sid, "file_id": fid, "expected_revision": rev},
        )
        assert prev_resp.status_code == 200
        preview = prev_resp.json()
        assert preview["matched_revisions"] == 1
        assert preview["would_update"] == 1
        assert "attestation_token" in preview
        token = preview["attestation_token"]

        # Preview must be strictly write-free
        rev_after_preview = resource_store.get_revision(revision_id=rev_id)
        assert rev_after_preview["translation"] == {}

        # 2. Apply using selection reference and preview token
        apply_resp = client.post(
            "/api/resources/libraries/sel_preview_lib/translations/apply",
            json={
                "selection_id": sid,
                "file_id": fid,
                "expected_revision": rev,
                "attestation_token": token,
            },
        )
        assert apply_resp.status_code == 200
        applied = apply_resp.json()
        assert applied["updated"] == 1
        assert applied["ready"] == 1

        # Check that translation rows and readiness were updated
        rev_after_apply = resource_store.get_revision(revision_id=rev_id)
        assert rev_after_apply["translation"] == {
            "label": "Sunlit Studio",
            "scene_theme": "Warm Palette",
        }

    def test_server_side_byte_authority_and_client_override_rejection(self, client):
        """Server-side staged file is the sole byte authority; client overrides are ignored or rejected."""
        lib_id = resource_store.ensure_library("sel_authority_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id,
            "r1",
            {"id": "r1", "name": "AUTH_SRC", "theme": "AUTH_THEME"},
        )

        staged_map = {
            "AUTH_SRC": {"source": "AUTH_SRC", "translation": "Staged Studio", "fields": ["name"]},
            "AUTH_THEME": {"source": "AUTH_THEME", "translation": "Staged Theme", "fields": ["theme"]},
        }
        sid, fid, rev = self._create_staged_translation_file(client, "sel_authority_lib", staged_map)

        # 1. Mixed form (selection_id + translation_map) must be rejected with 422
        mixed_resp = client.post(
            "/api/resources/libraries/sel_authority_lib/translations/preview",
            json={
                "selection_id": sid,
                "file_id": fid,
                "expected_revision": rev,
                "translation_map": {"FAKE": {"source": "FAKE", "translation": "Fake", "fields": ["name"]}},
            },
        )
        assert mixed_resp.status_code == 422
        assert "mixed" in mixed_resp.json().get("detail", "").lower()

        # 2. Mixed form with map_path must also be rejected with 422
        mixed_path_resp = client.post(
            "/api/resources/libraries/sel_authority_lib/translations/preview",
            json={
                "selection_id": sid,
                "file_id": fid,
                "expected_revision": rev,
                "map_path": "/some/client/path.json",
            },
        )
        assert mixed_path_resp.status_code == 422

        # 3. Unrecognized client override field (e.g. 'content') does NOT replace staged file
        prev_resp = client.post(
            "/api/resources/libraries/sel_authority_lib/translations/preview",
            json={
                "selection_id": sid,
                "file_id": fid,
                "expected_revision": rev,
                "content": {"AUTH_SRC": {"source": "AUTH_SRC", "translation": "Hacked", "fields": ["name"]}},
            },
        )
        assert prev_resp.status_code == 200
        token = prev_resp.json()["attestation_token"]

        apply_resp = client.post(
            "/api/resources/libraries/sel_authority_lib/translations/apply",
            json={
                "selection_id": sid,
                "file_id": fid,
                "expected_revision": rev,
                "attestation_token": token,
                "content": "arbitrary_client_bytes",
            },
        )
        assert apply_resp.status_code == 200
        rev_row = resource_store.get_revision(revision_id=rev_id)
        assert rev_row["translation"]["label"] == "Staged Studio"

    def test_selection_reference_validation_and_cross_selection_isolation(self, client):
        """Incomplete reference, wrong selection, wrong file, or target mismatch fail closed."""
        lib_a = resource_store.ensure_library("sel_lib_a", kind="rooms")
        lib_b = resource_store.ensure_library("sel_lib_b", kind="rooms")

        map_data = {
            "S1": {"source": "S1", "translation": "Studio", "fields": ["name"]},
        }
        sid_1, fid_1, rev_1 = self._create_staged_translation_file(client, "sel_lib_a", map_data)
        sid_2, fid_2, rev_2 = self._create_staged_translation_file(client, "sel_lib_a", map_data)

        # 1. Incomplete selection reference (missing expected_revision) -> 422
        resp = client.post(
            "/api/resources/libraries/sel_lib_a/translations/preview",
            json={"selection_id": sid_1, "file_id": fid_1},
        )
        assert resp.status_code == 422

        # 2. Incomplete selection reference (missing file_id) -> 422
        resp = client.post(
            "/api/resources/libraries/sel_lib_a/translations/preview",
            json={"selection_id": sid_1, "expected_revision": rev_1},
        )
        assert resp.status_code == 422

        # 3. File from another selection -> 404
        resp = client.post(
            "/api/resources/libraries/sel_lib_a/translations/preview",
            json={"selection_id": sid_1, "file_id": fid_2, "expected_revision": rev_1},
        )
        assert resp.status_code == 404

        # 4. Unknown file ID in valid selection -> 404
        resp = client.post(
            "/api/resources/libraries/sel_lib_a/translations/preview",
            json={"selection_id": sid_1, "file_id": "nonexistent_file", "expected_revision": rev_1},
        )
        assert resp.status_code == 404

        # 5. Unknown selection ID -> 404
        resp = client.post(
            "/api/resources/libraries/sel_lib_a/translations/preview",
            json={"selection_id": "nonexistent_sel", "file_id": fid_1, "expected_revision": rev_1},
        )
        assert resp.status_code == 404

        # 6. Target mismatch: staged file targets sel_lib_a, requested on sel_lib_b -> 422
        resp = client.post(
            "/api/resources/libraries/sel_lib_b/translations/preview",
            json={"selection_id": sid_1, "file_id": fid_1, "expected_revision": rev_1},
        )
        assert resp.status_code == 422

    def test_stale_revision_and_tampered_staged_bytes_rejected(self, client):
        """Stale revision and disk-tampered staged file return 409 before bulk translation."""
        lib_id = resource_store.ensure_library("sel_stale_lib", kind="rooms")
        map_data = {
            "S_STALE": {"source": "S_STALE", "translation": "Stale Room", "fields": ["name"]},
        }
        sid, fid, current_rev = self._create_staged_translation_file(client, "sel_stale_lib", map_data)

        # 1. Stale revision returns 409
        resp = client.post(
            "/api/resources/libraries/sel_stale_lib/translations/preview",
            json={"selection_id": sid, "file_id": fid, "expected_revision": current_rev - 1},
        )
        assert resp.status_code == 409

        # 2. Tampering with staged file bytes on disk triggers 409 PreviewMismatchError
        file_row = db.one("SELECT staged_path FROM resource_selection_file WHERE file_id = ?", fid)
        assert file_row is not None
        staged_path = Path(file_row["staged_path"])
        original_bytes = staged_path.read_bytes()
        try:
            staged_path.write_bytes(original_bytes + b" ")
            resp_tampered = client.post(
                "/api/resources/libraries/sel_stale_lib/translations/preview",
                json={"selection_id": sid, "file_id": fid, "expected_revision": current_rev},
            )
            assert resp_tampered.status_code == 409
        finally:
            staged_path.write_bytes(original_bytes)

    def test_selected_map_uses_one_materialized_read_during_path_replacement(
        self, client, monkeypatch, tmp_path,
    ):
        """Replacing the path after inspection cannot change the parsed staged bytes."""
        map_a = {
            "SOURCE_A": {
                "source": "SOURCE_A",
                "translation": "Map Alpha",
                "fields": ["name"],
            },
        }
        map_b = {
            "SOURCE_B": {
                "source": "SOURCE_B",
                "translation": "Map Bravo",
                "fields": ["name"],
            },
        }
        sid, fid, rev = self._create_staged_translation_file(client, "sel_toctou_lib", map_a)
        file_row = db.one(
            "SELECT staged_path FROM resource_selection_file WHERE file_id = ?",
            fid,
        )
        staged_path = Path(file_row["staged_path"])
        replacement_path = tmp_path / "replacement.json"
        replacement_path.write_bytes(json.dumps(map_b).encode("utf-8"))

        original_detect = resource_parser.detect_auxiliary_candidates
        replaced = False

        def replace_path_after_materialization(data):
            nonlocal replaced
            if not replaced:
                replacement_path.replace(staged_path)
                replaced = True
            return original_detect(data)

        monkeypatch.setattr(
            resource_parser,
            "detect_auxiliary_candidates",
            replace_path_after_materialization,
        )

        resolved = resource_selection.resolve_selected_translation_map(
            selection_id=sid,
            file_id=fid,
            expected_revision=rev,
            expected_library_key="sel_toctou_lib",
        )

        assert replaced is True
        assert resolved == map_a
        assert json.loads(staged_path.read_text(encoding="utf-8")) == map_b

    def test_semantically_invalid_selected_map_returns_422_without_writes(
        self, client, monkeypatch,
    ):
        """Selected map semantics are validated once by bulk preview inside the 422 boundary."""
        lib_id = resource_store.ensure_library("sel_bad_semantics_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id,
            "r_bad",
            {"id": "r_bad", "name": "BAD", "theme": "VALID_THEME"},
        )
        bad_map = {
            "BAD": {
                "source": 123,
                "translation": "Valid",
                "fields": ["name"],
            },
        }
        sid, fid, rev = self._create_staged_translation_file(
            client, "sel_bad_semantics_lib", bad_map,
        )
        file_row = db.one(
            "SELECT staged_path FROM resource_selection_file WHERE file_id = ?",
            fid,
        )
        staged_path = Path(file_row["staged_path"])
        normalize_calls = 0
        original_normalize = backend_resource_translation.normalize_translation_map_input

        def spy_normalize(data):
            nonlocal normalize_calls
            normalize_calls += 1
            return original_normalize(data)

        monkeypatch.setattr(
            backend_resource_translation,
            "normalize_translation_map_input",
            spy_normalize,
        )

        response = client.post(
            "/api/resources/libraries/sel_bad_semantics_lib/translations/preview",
            json={"selection_id": sid, "file_id": fid, "expected_revision": rev},
        )

        assert response.status_code == 422
        assert normalize_calls == 1
        assert resource_store.get_revision(revision_id=rev_id)["translation"] == {}
        detail = response.json()["detail"]
        assert str(staged_path).lower() not in detail.lower()
        assert str(staged_path.parent).lower() not in detail.lower()
        assert "traceback" not in detail.lower()

    def test_authorization_unchanged_on_selected_map(self, client):
        """Unauthorized descriptive fields in selected map are rejected with 422 and zero writes."""
        lib_id = resource_store.ensure_library("sel_authz_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id,
            "r_authz",
            {"id": "r_authz", "name": "SRC_AUTHZ", "theme": "TH_AUTHZ"},
        )

        # 'weight' is not an authorized descriptive field for 'rooms' kind
        unauthorized_map = {
            "SRC_AUTHZ": {"source": "SRC_AUTHZ", "translation": "Heavy", "fields": ["weight"]},
        }
        sid, fid, rev = self._create_staged_translation_file(client, "sel_authz_lib", unauthorized_map)

        # Preview returns 422
        prev_resp = client.post(
            "/api/resources/libraries/sel_authz_lib/translations/preview",
            json={"selection_id": sid, "file_id": fid, "expected_revision": rev},
        )
        assert prev_resp.status_code == 422

        # Apply with arbitrary token returns 422
        apply_resp = client.post(
            "/api/resources/libraries/sel_authz_lib/translations/apply",
            json={
                "selection_id": sid,
                "file_id": fid,
                "expected_revision": rev,
                "attestation_token": "dummy.token",
            },
        )
        assert apply_resp.status_code == 422

        # Zero writes
        rev_row = resource_store.get_revision(revision_id=rev_id)
        assert rev_row["translation"] == {}

    def test_library_fingerprint_drift_on_selected_apply(self, client):
        """Library modification between preview and apply returns 409 with zero writes."""
        lib_id = resource_store.ensure_library("sel_drift_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id,
            "r_drift",
            {"id": "r_drift", "name": "SRC_DRIFT", "theme": "TH_DRIFT"},
        )

        map_data = {
            "SRC_DRIFT": {"source": "SRC_DRIFT", "translation": "Drifted Room", "fields": ["name"]},
            "TH_DRIFT": {"source": "TH_DRIFT", "translation": "Drifted Theme", "fields": ["theme"]},
        }
        sid, fid, rev = self._create_staged_translation_file(client, "sel_drift_lib", map_data)

        prev_resp = client.post(
            "/api/resources/libraries/sel_drift_lib/translations/preview",
            json={"selection_id": sid, "file_id": fid, "expected_revision": rev},
        )
        assert prev_resp.status_code == 200
        token = prev_resp.json()["attestation_token"]

        # Induce library fingerprint drift by adding another revision
        resource_store.record_revision(
            lib_id,
            "r_drift_2",
            {"id": "r_drift_2", "name": "SRC_2", "theme": "TH_2"},
        )

        # Apply must detect fingerprint mismatch and fail with 409
        apply_resp = client.post(
            "/api/resources/libraries/sel_drift_lib/translations/apply",
            json={
                "selection_id": sid,
                "file_id": fid,
                "expected_revision": rev,
                "attestation_token": token,
            },
        )
        assert apply_resp.status_code == 409

        # Target revision remains unchanged
        rev_row = resource_store.get_revision(revision_id=rev_id)
        assert rev_row["translation"] == {}

    @pytest.mark.parametrize("route_name", ["preview", "apply"])
    def test_oversized_missing_content_length_returns_413_pre_parse(self, client, monkeypatch, route_name):
        """Streamed chunked request body >10 MiB lacking Content-Length returns 413 before parsing."""
        lib_id = resource_store.ensure_library(f"sel_oversized_miss_{route_name}", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id,
            "r_ov",
            {"id": "r_ov", "name": "SRC_OV", "theme": "TH_OV"},
        )

        parse_calls = 0
        orig_normalize = resource_translation.normalize_translation_map_input

        def spy_normalize(data):
            nonlocal parse_calls
            parse_calls += 1
            return orig_normalize(data)

        monkeypatch.setattr(resource_translation, "normalize_translation_map_input", spy_normalize)

        # Snapshot DB before request
        before_rev = resource_store.get_revision(revision_id=rev_id)
        before_count = db.one("SELECT COUNT(*) as c FROM asset_revision WHERE library_id = ?", lib_id)["c"]

        chunk_size = 2 * 1024 * 1024
        chunks = [b"a" * chunk_size for _ in range(6)]  # 12 MiB total

        resp = client.post(
            f"/api/resources/libraries/sel_oversized_miss_{route_name}/translations/{route_name}",
            content=(c for c in chunks),
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 413
        assert resp.json() == {"detail": "Request entity too large"}
        assert parse_calls == 0

        # Zero DB writes
        after_rev = resource_store.get_revision(revision_id=rev_id)
        assert after_rev == before_rev
        after_count = db.one("SELECT COUNT(*) as c FROM asset_revision WHERE library_id = ?", lib_id)["c"]
        assert after_count == before_count

    @pytest.mark.parametrize("route_name", ["preview", "apply"])
    def test_oversized_false_small_content_length_returns_413_pre_parse(self, client, monkeypatch, route_name):
        """Streamed request body >10 MiB with false-small Content-Length returns 413 before parsing."""
        lib_id = resource_store.ensure_library(f"sel_oversized_false_{route_name}", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id,
            "r_ov2",
            {"id": "r_ov2", "name": "SRC_OV2", "theme": "TH_OV2"},
        )

        parse_calls = 0
        orig_normalize = resource_translation.normalize_translation_map_input

        def spy_normalize(data):
            nonlocal parse_calls
            parse_calls += 1
            return orig_normalize(data)

        monkeypatch.setattr(resource_translation, "normalize_translation_map_input", spy_normalize)

        before_rev = resource_store.get_revision(revision_id=rev_id)
        before_count = db.one("SELECT COUNT(*) as c FROM asset_revision WHERE library_id = ?", lib_id)["c"]

        chunk_size = 2 * 1024 * 1024
        chunks = [b"b" * chunk_size for _ in range(6)]  # 12 MiB total

        resp = client.post(
            f"/api/resources/libraries/sel_oversized_false_{route_name}/translations/{route_name}",
            content=(c for c in chunks),
            headers={
                "content-length": "64",
                "content-type": "application/json",
            },
        )
        assert resp.status_code == 413
        assert resp.json() == {"detail": "Request entity too large"}
        assert parse_calls == 0

        # Zero DB writes
        after_rev = resource_store.get_revision(revision_id=rev_id)
        assert after_rev == before_rev
        after_count = db.one("SELECT COUNT(*) as c FROM asset_revision WHERE library_id = ?", lib_id)["c"]
        assert after_count == before_count

    @pytest.mark.parametrize("route_name", ["preview", "apply"])
    def test_exact_10_mib_boundary_and_plus_one(self, client, monkeypatch, route_name):
        """Exact 10 MiB request is not rejected by limiter; 10 MiB + 1 byte returns 413 pre-parse."""
        lib_id = resource_store.ensure_library(f"sel_bound_{route_name}", kind="rooms")

        parse_calls = 0
        orig_normalize = resource_translation.normalize_translation_map_input

        def spy_normalize(data):
            nonlocal parse_calls
            parse_calls += 1
            return orig_normalize(data)

        monkeypatch.setattr(resource_translation, "normalize_translation_map_input", spy_normalize)

        limit_bytes = 10 * 1024 * 1024

        # 1. Exactly 10 MiB payload reaches downstream (not 413)
        exact_payload = b" " * limit_bytes
        resp_exact = client.post(
            f"/api/resources/libraries/sel_bound_{route_name}/translations/{route_name}",
            content=exact_payload,
            headers={"content-type": "application/json"},
        )
        assert resp_exact.status_code != 413

        # 2. 10 MiB + 1 byte is rejected with 413 before domain parsing
        parse_calls = 0
        plus_one_payload = b" " * (limit_bytes + 1)
        resp_plus_one = client.post(
            f"/api/resources/libraries/sel_bound_{route_name}/translations/{route_name}",
            content=plus_one_payload,
            headers={"content-type": "application/json"},
        )
        assert resp_plus_one.status_code == 413
        assert resp_plus_one.json() == {"detail": "Request entity too large"}
        assert parse_calls == 0


# ===========================================================================
# Task 3.3 Integration Tests: Resource Translation Proposals
# ===========================================================================


class TestResourceTranslationProposals:
    """Task 3.3 tests: field-preserving assistant transport for translation proposals."""

    @pytest.fixture(autouse=True)
    def setup_config(self, monkeypatch):
        import main
        monkeypatch.setitem(main.CONFIG, "llm_url", "http://127.0.0.1:11434/v1")
        monkeypatch.setitem(main.CONFIG, "llm_model", "test-assistant-model")

    def test_proposals_feature_flag_blocks_provider_and_reenables_normal_flow(
        self, client, monkeypatch,
    ):
        import main

        lib_id = resource_store.ensure_library("prop_flag_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "room-flag", {"id": "room-flag", "name": "комната_flag"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        request_body = {
            "entries": [{
                "source_id": "room-flag",
                "content_digest": rev["content_digest"],
                "field": "label",
                "source_shape": "scalar",
                "list_index": None,
            }],
        }
        provider_calls = []

        async def spy_run_structured(config, p, image=""):
            provider_calls.append(p)
            return {"entry-0": "Flag Room"}

        monkeypatch.setattr(main.enhance, "run_structured", spy_run_structured)
        before_revision = resource_store.get_revision(revision_id=rev_id)
        before_revision_count = db.one("SELECT COUNT(*) AS c FROM asset_revision")["c"]

        monkeypatch.setattr(main, "is_resource_planning_enabled", lambda: False)
        disabled = client.post(
            "/api/resources/libraries/prop_flag_lib/translations/proposals",
            json=request_body,
        )

        assert disabled.status_code == 503
        assert disabled.json()["detail"] == "Resource planning is disabled by configuration"
        assert len(provider_calls) == 0
        assert resource_store.get_revision(revision_id=rev_id) == before_revision
        assert db.one("SELECT COUNT(*) AS c FROM asset_revision")["c"] == before_revision_count

        disabled_malformed = client.post(
            "/api/resources/libraries/prop_flag_lib/translations/proposals",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        assert disabled_malformed.status_code == 503
        assert disabled_malformed.json()["detail"] == "Resource planning is disabled by configuration"
        assert len(provider_calls) == 0

        monkeypatch.setattr(main, "is_resource_planning_enabled", lambda: True)
        enabled = client.post(
            "/api/resources/libraries/prop_flag_lib/translations/proposals",
            json=request_body,
        )

        assert enabled.status_code == 200
        assert enabled.json()["proposals"][0]["translation"] == "Flag Room"
        assert len(provider_calls) == 1

    def test_proposals_max_20_accepted_and_21_rejected_before_provider(self, client, monkeypatch):
        import main
        lib_id = resource_store.ensure_library("prop_cardinality_lib", kind="rooms")
        revisions = []
        for i in range(21):
            rev_id = resource_store.record_revision(
                lib_id,
                f"room-card-{i}",
                {"id": f"room-card-{i}", "name": f"комната-{i}", "theme": f"THEME_{i}"},
            )
            revisions.append(resource_store.get_revision(revision_id=rev_id))

        calls = []

        async def spy_run_structured(config, p, image=""):
            calls.append(p)
            return {f"entry-{j}": f"Room {j}" for j in range(20)}

        monkeypatch.setattr(main.enhance, "run_structured", spy_run_structured)

        # 1. 21 entries: rejected before provider call (422)
        entries_21 = [
            {
                "source_id": rev["source_id"],
                "content_digest": rev["content_digest"],
                "field": "label",
                "source_shape": "scalar",
                "list_index": None,
            }
            for rev in revisions[:21]
        ]
        resp_21 = client.post(
            "/api/resources/libraries/prop_cardinality_lib/translations/proposals",
            json={"entries": entries_21},
        )
        assert resp_21.status_code == 422
        assert len(calls) == 0

        # 2. Exactly 20 entries: accepted, exactly one provider call
        entries_20 = entries_21[:20]
        resp_20 = client.post(
            "/api/resources/libraries/prop_cardinality_lib/translations/proposals",
            json={"entries": entries_20},
        )
        assert resp_20.status_code == 200
        assert len(calls) == 1
        data_20 = resp_20.json()
        assert data_20["library_key"] == "prop_cardinality_lib"
        assert len(data_20["proposals"]) == 20
        for idx, prop in enumerate(data_20["proposals"]):
            assert prop["revision"]["source_id"] == f"room-card-{idx}"
            assert prop["field"] == "label"
            assert prop["source_shape"] == "scalar"
            assert prop["list_index"] is None
            assert prop["source_value"] == f"комната-{idx}"
            assert prop["translation"] == f"Room {idx}"

    def test_proposals_duplicate_request_identity_rejected(self, client, monkeypatch):
        import main
        lib_id = resource_store.ensure_library("prop_dup_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "room-dup", {"id": "room-dup", "name": "комната_dup"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)

        calls = []

        async def spy_run_structured(config, p, image=""):
            calls.append(p)
            return {"entry-0": "Room Dup"}

        monkeypatch.setattr(main.enhance, "run_structured", spy_run_structured)

        entry = {
            "source_id": "room-dup",
            "content_digest": rev["content_digest"],
            "field": "label",
            "source_shape": "scalar",
            "list_index": None,
        }
        resp = client.post(
            "/api/resources/libraries/prop_dup_lib/translations/proposals",
            json={"entries": [entry, entry]},
        )
        assert resp.status_code == 422
        assert len(calls) == 0

    @pytest.mark.parametrize("bad_list_index", ["0", 0.0, True, False])
    def test_proposals_strict_list_index_rejection(self, client, monkeypatch, bad_list_index):
        import main
        calls = []

        async def spy_run_structured(config, p, image=""):
            calls.append(p)
            return {"entry-0": "Valid"}

        monkeypatch.setattr(main.enhance, "run_structured", spy_run_structured)

        lib_id = resource_store.ensure_library("prop_strict_idx_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "room-idx", {"id": "room-idx", "name": "комната", "tags": ["тег-0"]},
        )
        rev = resource_store.get_revision(revision_id=rev_id)

        before_revisions_count = db.one("SELECT COUNT(*) as c FROM asset_revision")["c"]

        # Call with coerced/invalid list_index
        resp = client.post(
            "/api/resources/libraries/prop_strict_idx_lib/translations/proposals",
            json={
                "entries": [
                    {
                        "source_id": "room-idx",
                        "content_digest": rev["content_digest"],
                        "field": "tags",
                        "source_shape": "list",
                        "list_index": bad_list_index,
                    },
                ],
            },
        )
        assert resp.status_code == 422
        assert len(calls) == 0
        assert db.one("SELECT COUNT(*) as c FROM asset_revision")["c"] == before_revisions_count

        # Confirm list_index = 0 is valid
        resp_valid = client.post(
            "/api/resources/libraries/prop_strict_idx_lib/translations/proposals",
            json={
                "entries": [
                    {
                        "source_id": "room-idx",
                        "content_digest": rev["content_digest"],
                        "field": "tags",
                        "source_shape": "list",
                        "list_index": 0,
                    },
                ],
            },
        )
        assert resp_valid.status_code == 200
        assert len(calls) == 1

    def test_proposals_no_leak_of_request_markers_on_422(self, client, monkeypatch):
        import main
        calls = []

        async def spy_run_structured(config, p, image=""):
            calls.append(p)
            return {}

        monkeypatch.setattr(main.enhance, "run_structured", spy_run_structured)

        marker_sid = "PRIVATE-SOURCE-MARKER"
        marker_digest = "a" * 64
        marker_extra = "PRIVATE-EXTRA-MARKER"

        # Case A: duplicate identity
        entry = {
            "source_id": marker_sid,
            "content_digest": marker_digest,
            "field": "label",
            "source_shape": "scalar",
            "list_index": None,
        }
        resp_a = client.post(
            "/api/resources/libraries/any_lib/translations/proposals",
            json={"entries": [entry, entry]},
        )
        assert resp_a.status_code == 422
        assert len(calls) == 0
        assert marker_sid not in resp_a.text
        assert resp_a.json()["detail"] == "Invalid proposal request schema"

        # Case B: extra field
        resp_b = client.post(
            "/api/resources/libraries/any_lib/translations/proposals",
            json={"entries": [entry], "secret_field": marker_extra},
        )
        assert resp_b.status_code == 422
        assert len(calls) == 0
        assert marker_extra not in resp_b.text
        assert "secret_field" not in resp_b.text
        assert resp_b.json()["detail"] == "Invalid proposal request schema"

        # Case C: invalid/coerced list_index
        bad_entry = {**entry, "source_shape": "list", "list_index": "0"}
        resp_c = client.post(
            "/api/resources/libraries/any_lib/translations/proposals",
            json={"entries": [bad_entry]},
        )
        assert resp_c.status_code == 422
        assert len(calls) == 0
        assert marker_sid not in resp_c.text
        assert resp_c.json()["detail"] == "Invalid proposal request schema"

    def test_proposals_server_owned_canonical_ordering_from_reordered_client_request(self, client, monkeypatch):
        import main
        lib_id = resource_store.ensure_library("prop_order_reg_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id,
            "room-order-reg",
            {
                "id": "room-order-reg",
                "name": "комната_reg",
                "tags": ["тег-0", "тег-1", "тег-2"],
            },
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        digest = rev["content_digest"]

        prompt_captured = []

        async def spy_run_structured(config, p, image=""):
            prompt_captured.append(p.instruction)
            # Provider returns keys in arbitrary/scrambled order
            return {
                "entry-1": "tag 1",
                "entry-2": "tag 2",
                "entry-0": "tag 0",
            }

        monkeypatch.setattr(main.enhance, "run_structured", spy_run_structured)

        # Client requests list items 2, 0, 1
        reordered_entries = [
            {"source_id": "room-order-reg", "content_digest": digest, "field": "tags", "source_shape": "list", "list_index": 2},
            {"source_id": "room-order-reg", "content_digest": digest, "field": "tags", "source_shape": "list", "list_index": 0},
            {"source_id": "room-order-reg", "content_digest": digest, "field": "tags", "source_shape": "list", "list_index": 1},
        ]
        resp = client.post(
            "/api/resources/libraries/prop_order_reg_lib/translations/proposals",
            json={"entries": reordered_entries},
        )
        assert resp.status_code == 200
        proposals = resp.json()["proposals"]

        # Server-owned canonical order in provider prompt: entry-0 -> index 0, entry-1 -> index 1, entry-2 -> index 2
        assert len(prompt_captured) == 1
        instr = prompt_captured[0]
        pos_entry0 = instr.find('"key": "entry-0"')
        pos_entry1 = instr.find('"key": "entry-1"')
        pos_entry2 = instr.find('"key": "entry-2"')
        assert pos_entry0 < pos_entry1 < pos_entry2
        # Verify correspondence in prompt
        assert '"list_index": 0' in instr
        assert '"list_index": 1' in instr
        assert '"list_index": 2' in instr

        # Server-owned canonical order in public response: index 0, index 1, index 2
        assert [p["list_index"] for p in proposals] == [0, 1, 2]
        assert [p["translation"] for p in proposals] == ["tag 0", "tag 1", "tag 2"]
        assert [p["source_value"] for p in proposals] == ["тег-0", "тег-1", "тег-2"]

        # Repeated source text test: two items with distinct identities but identical source_value
        rev_dup_id = resource_store.record_revision(
            lib_id,
            "room-order-dup",
            {
                "id": "room-order-dup",
                "name": "комната_dup",
                "tags": ["балкон", "балкон"],
            },
        )
        rev_dup = resource_store.get_revision(revision_id=rev_dup_id)
        dup_entries = [
            {"source_id": "room-order-dup", "content_digest": rev_dup["content_digest"], "field": "tags", "source_shape": "list", "list_index": 1},
            {"source_id": "room-order-dup", "content_digest": rev_dup["content_digest"], "field": "tags", "source_shape": "list", "list_index": 0},
        ]
        async def spy_dup(config, p, image=""):
            return {"entry-0": "balcony A", "entry-1": "balcony B"}
        monkeypatch.setattr(main.enhance, "run_structured", spy_dup)

        resp_dup = client.post(
            "/api/resources/libraries/prop_order_reg_lib/translations/proposals",
            json={"entries": dup_entries},
        )
        assert resp_dup.status_code == 200
        proposals_dup = resp_dup.json()["proposals"]
        assert len(proposals_dup) == 2
        assert [p["list_index"] for p in proposals_dup] == [0, 1]
        assert proposals_dup[0]["translation"] == "balcony A"
        assert proposals_dup[1]["translation"] == "balcony B"

    def test_proposals_closed_schema_rejection(self, client, monkeypatch):
        import main
        calls = []

        async def spy_run_structured(config, p, image=""):
            calls.append(p)
            return {}

        monkeypatch.setattr(main.enhance, "run_structured", spy_run_structured)

        valid_entry = {
            "source_id": "room-1",
            "content_digest": "a" * 64,
            "field": "label",
            "source_shape": "scalar",
            "list_index": None,
        }

        # Extra key on body
        resp = client.post(
            "/api/resources/libraries/lib/translations/proposals",
            json={"entries": [valid_entry], "extra": "forbidden"},
        )
        assert resp.status_code == 422

        # Extra key on entry
        bad_entry = {**valid_entry, "source_value": "attempt"}
        resp = client.post(
            "/api/resources/libraries/lib/translations/proposals",
            json={"entries": [bad_entry]},
        )
        assert resp.status_code == 422

        # Scalar with non-null list_index
        bad_scalar = {**valid_entry, "list_index": 0}
        resp = client.post(
            "/api/resources/libraries/lib/translations/proposals",
            json={"entries": [bad_scalar]},
        )
        assert resp.status_code == 422

        # List with null list_index
        bad_list = {**valid_entry, "source_shape": "list", "list_index": None}
        resp = client.post(
            "/api/resources/libraries/lib/translations/proposals",
            json={"entries": [bad_list]},
        )
        assert resp.status_code == 422

        # Empty entries
        resp = client.post(
            "/api/resources/libraries/lib/translations/proposals",
            json={"entries": []},
        )
        assert resp.status_code == 422
        assert len(calls) == 0

    def test_proposals_provider_keys_and_order_preservation(self, client, monkeypatch):
        import main
        lib_id = resource_store.ensure_library("prop_order_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id,
            "room-order",
            {
                "id": "room-order",
                "name": "комната-1",
                "theme": "SUITE_A",
                "tags": ["балконы", "балконы"],
            },
        )
        rev = resource_store.get_revision(revision_id=rev_id)
        digest = rev["content_digest"]

        prompt_captured = []

        async def spy_run_structured(config, p, image=""):
            prompt_captured.append(p.instruction)
            # Return keys out of order intentionally
            return {
                "entry-2": "balconies 2",
                "entry-0": "Room Order",
                "entry-1": "balconies 1",
            }

        monkeypatch.setattr(main.enhance, "run_structured", spy_run_structured)

        entries = [
            {"source_id": "room-order", "content_digest": digest, "field": "label", "source_shape": "scalar", "list_index": None},
            {"source_id": "room-order", "content_digest": digest, "field": "tags", "source_shape": "list", "list_index": 0},
            {"source_id": "room-order", "content_digest": digest, "field": "tags", "source_shape": "list", "list_index": 1},
        ]
        resp = client.post(
            "/api/resources/libraries/prop_order_lib/translations/proposals",
            json={"entries": entries},
        )
        assert resp.status_code == 200
        proposals = resp.json()["proposals"]
        # Public response order must match server-owned request order: entry-0, entry-1, entry-2
        assert [p["translation"] for p in proposals] == ["Room Order", "balconies 1", "balconies 2"]
        assert [p["list_index"] for p in proposals] == [None, 0, 1]
        assert len(prompt_captured) == 1
        assert "entry-0" in prompt_captured[0]
        assert "entry-1" in prompt_captured[0]
        assert "entry-2" in prompt_captured[0]

    @pytest.mark.parametrize(
        "provider_answer",
        [
            {"entry-0": "Valid", "entry-extra": "Extra"},  # extra key
            {},  # missing key
            {"entry-0": ""},  # empty string
            {"entry-0": "   "},  # whitespace string
            {"entry-0": 123},  # number
            {"entry-0": None},  # null
            {"entry-0": ["Room"]},  # list
            {"entry-0": {"translation": "Room"}},  # nested object
            {"entry-0": "комната"},  # Cyrillic/non-English
        ],
    )
    def test_proposals_provider_malformed_output_rejected_safely(
        self, client, monkeypatch, provider_answer,
    ):
        import main
        lib_id = resource_store.ensure_library("prop_malformed_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "room-mal", {"id": "room-mal", "name": "комната_m"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)

        async def spy_run_structured(config, p, image=""):
            return provider_answer

        monkeypatch.setattr(main.enhance, "run_structured", spy_run_structured)

        resp = client.post(
            "/api/resources/libraries/prop_malformed_lib/translations/proposals",
            json={
                "entries": [
                    {
                        "source_id": "room-mal",
                        "content_digest": rev["content_digest"],
                        "field": "label",
                        "source_shape": "scalar",
                        "list_index": None,
                    },
                ],
            },
        )
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "invalid proposal response" in detail
        # Invariant: raw provider output is NOT reflected in detail
        assert "комната" not in detail

    def test_proposals_assistant_not_configured(self, client, monkeypatch):
        import main
        monkeypatch.setitem(main.CONFIG, "llm_url", "")
        monkeypatch.setitem(main.CONFIG, "llm_model", "")

        lib_id = resource_store.ensure_library("prop_noconf_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "room-noconf", {"id": "room-noconf", "name": "комната_nc"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)

        resp = client.post(
            "/api/resources/libraries/prop_noconf_lib/translations/proposals",
            json={
                "entries": [
                    {
                        "source_id": "room-noconf",
                        "content_digest": rev["content_digest"],
                        "field": "label",
                        "source_shape": "scalar",
                        "list_index": None,
                    },
                ],
            },
        )
        assert resp.status_code == 400
        assert "No prompt assistant is configured" in resp.json()["detail"]

    def test_proposals_write_free_proof(self, client, monkeypatch):
        import main
        lib_id = resource_store.ensure_library("prop_writefree_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "room-wf", {"id": "room-wf", "name": "комната_wf"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)

        async def mock_run_structured(config, p, image=""):
            return {"entry-0": "Clean Room"}

        monkeypatch.setattr(main.enhance, "run_structured", mock_run_structured)

        # Before snapshots
        before_rev = resource_store.get_revision(revision_id=rev_id)
        before_revisions_count = db.one("SELECT COUNT(*) as c FROM asset_revision")["c"]
        before_auxiliary_count = db.one("SELECT COUNT(*) as c FROM auxiliary_resource")["c"]
        before_library_count = db.one("SELECT COUNT(*) as c FROM resource_library")["c"]

        # Call proposals endpoint (success)
        resp = client.post(
            "/api/resources/libraries/prop_writefree_lib/translations/proposals",
            json={
                "entries": [
                    {
                        "source_id": "room-wf",
                        "content_digest": rev["content_digest"],
                        "field": "label",
                        "source_shape": "scalar",
                        "list_index": None,
                    },
                ],
            },
        )
        assert resp.status_code == 200

        # After assertions: zero DB writes
        after_rev = resource_store.get_revision(revision_id=rev_id)
        assert after_rev == before_rev
        assert after_rev["translation"] == before_rev["translation"]
        assert db.one("SELECT COUNT(*) as c FROM asset_revision")["c"] == before_revisions_count
        assert db.one("SELECT COUNT(*) as c FROM auxiliary_resource")["c"] == before_auxiliary_count
        assert db.one("SELECT COUNT(*) as c FROM resource_library")["c"] == before_library_count

        # Call proposals endpoint (failure)
        from fastapi import HTTPException
        async def mock_fail(config, p, image=""):
            raise HTTPException(502, "Provider error")

        monkeypatch.setattr(main.enhance, "run_structured", mock_fail)
        resp_fail = client.post(
            "/api/resources/libraries/prop_writefree_lib/translations/proposals",
            json={
                "entries": [
                    {
                        "source_id": "room-wf",
                        "content_digest": rev["content_digest"],
                        "field": "label",
                        "source_shape": "scalar",
                        "list_index": None,
                    },
                ],
            },
        )
        assert resp_fail.status_code == 502

        # After assertions: still zero DB writes
        assert resource_store.get_revision(revision_id=rev_id) == before_rev
        assert db.one("SELECT COUNT(*) as c FROM asset_revision")["c"] == before_revisions_count

    def test_proposals_no_shortcuts_or_preview_apply_called(self, client, monkeypatch):
        import main
        lib_id = resource_store.ensure_library("prop_noshortcut_lib", kind="rooms")
        rev_id = resource_store.record_revision(
            lib_id, "room-ns", {"id": "room-ns", "name": "комната_ns"},
        )
        rev = resource_store.get_revision(revision_id=rev_id)

        def forbidden_call(*args, **kwargs):
            raise AssertionError("Forbidden function called during proposals!")

        monkeypatch.setattr(backend_resource_translation, "apply_revision_translation", forbidden_call)
        monkeypatch.setattr(backend_resource_translation, "preview_translation_map", forbidden_call)
        monkeypatch.setattr(backend_resource_translation, "apply_translation_map", forbidden_call)

        async def mock_run_structured(config, p, image=""):
            return {"entry-0": "No Shortcut Room"}

        monkeypatch.setattr(main.enhance, "run_structured", mock_run_structured)

        resp = client.post(
            "/api/resources/libraries/prop_noshortcut_lib/translations/proposals",
            json={
                "entries": [
                    {
                        "source_id": "room-ns",
                        "content_digest": rev["content_digest"],
                        "field": "label",
                        "source_shape": "scalar",
                        "list_index": None,
                    },
                ],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["proposals"][0]["translation"] == "No Shortcut Room"
