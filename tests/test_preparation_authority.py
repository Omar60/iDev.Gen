"""Focused tests for OpenSpec Task 2.3: preparation authority matrix enforcement.

Validates:
1. Rejection of public raw begin/complete on automatic and manual authoring plans with 409.
2. Rejection of direct single/bulk preparation on automatic authoring plans with 409.
3. Zero ready-state mutation, zero pending row mutation, and zero assistant/writer/provider
   work on all rejected paths.
4. Preflight and zero partial mutation in bulk preparation.
5. Preservation of pre-authoring expert plans and historical begin/complete/prepare flows.
6. Preservation of legacy non-resource composition 400 contracts.
7. Fail-closed plan authoring classification.
8. Strict validation and error precedence (503 -> 404 -> 400 -> 409 stale -> 422 take -> 409 authority).
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import backend.enhance
import db
import main
import backend.resource_preparation as backend_resource_preparation
import backend.session_plan as backend_session_plan
import resource_store


# Invented English-only fixture constants
INV_LOOK = "A clean studio with a grey background and soft diffuse daylight."
INV_WARDROBE = "A white cotton shirt and dark denim trousers."
INV_LIBRARY_KEY = "inv_authority_rooms"
INV_SOURCE_ID = "inv_room_auth_01"
INV_ROOM_PAYLOAD = {
    "label": "invented studio room",
    "scene_theme": "minimalist studio lighting",
    "weight": 1.0,
}


def _setup_resource_revision() -> dict:
    lib_id = resource_store.ensure_library(INV_LIBRARY_KEY, kind="rooms")
    rev_id = resource_store.record_revision(
        lib_id,
        INV_SOURCE_ID,
        INV_ROOM_PAYLOAD,
        translation={"label": "invented studio room", "scene_theme": "minimalist studio lighting"},
    )
    rev = resource_store.get_revision(revision_id=rev_id)
    assert rev is not None
    return {
        "library_id": lib_id,
        "library_key": INV_LIBRARY_KEY,
        "source_id": INV_SOURCE_ID,
        "content_digest": rev["content_digest"],
    }


def _create_resource_session(client, seeded, *, mode: str = "resource-v1", name: str = "authority session") -> int:
    resp = client.post(
        "/api/sessions",
        json={
            "model_id": seeded["model_id"],
            "name": name,
            "composition_mode": mode,
            "look": INV_LOOK,
            "wardrobe": INV_WARDROBE,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _create_legacy_session(client, seeded, name: str = "legacy session") -> int:
    now = db.now()
    return db.run(
        "INSERT INTO session (model_id, name, settings, created_at) VALUES (?, ?, ?, ?)",
        seeded["model_id"],
        name,
        json.dumps({"composition_mode": "legacy"}),
        now,
    )


def _seed_plan(
    sid: int,
    seeded: dict,
    rev: dict,
    *,
    authoring_mode: str | None = None,
    custom_authoring: dict | None = None,
    takes: list[dict] | None = None,
) -> tuple[int, dict]:
    if takes is None:
        takes = [
            {
                "take_id": "take-001",
                "label": "take 1",
                "camera": "eye level",
                "framing": "medium shot",
                "pose": "standing",
                "expression": "neutral",
            }
        ]

    plan = {
        "version": "resource-v1",
        "look": INV_LOOK,
        "initial_wardrobe": INV_WARDROBE,
        "takes": takes,
        "selected_resources": [
            {
                "library_key": rev["library_key"],
                "source_id": rev["source_id"],
                "content_digest": rev["content_digest"],
            }
        ],
        "wardrobe_changes": [],
    }
    backend_session_plan.save_draft(sid, plan, expected_revision=0)

    if authoring_mode is not None or custom_authoring is not None:
        if custom_authoring is not None:
            authoring_block = custom_authoring
        else:
            authoring_block = {
                "schema_version": 1,
                "mode": authoring_mode,
                "brief": "invented portrait brief",
                "scene_anchor": {
                    "library_key": rev["library_key"],
                    "source_id": rev["source_id"],
                    "content_digest": rev["content_digest"],
                },
                "workflow_binding": {
                    "workflow_id": seeded["workflow_id"],
                    "kind": "t2i",
                    "graph_digest": "a" * 64,
                    "node_map_digest": "b" * 64,
                },
                "variation_policy": {
                    "camera": {"mode": "vary"},
                    "framing": {"mode": "vary"},
                    "pose": {"mode": "vary"},
                    "expression": {"mode": "vary"},
                },
                "shared_state": {
                    "look": {"origin": "none", "evidence_id": None},
                    "initial_wardrobe": {"origin": "none", "evidence_id": None},
                },
                "evidence": [],
                "look_snapshot": None,
                "wardrobe_progression": None,
            }
        plan_with_auth = dict(plan)
        if custom_authoring == "OMIT":
            plan_with_auth.pop("authoring", None)
        else:
            plan_with_auth["authoring"] = authoring_block

        db.run(
            "UPDATE session_plan SET plan_json = ? WHERE session_id = ?",
            json.dumps(plan_with_auth),
            sid,
        )
        plan = plan_with_auth

    return 1, plan


class TestPreparationAuthorityMatrix:
    """Normative matrix tests for automatic, manual, and pre-authoring plans."""

    def test_01_automatic_raw_begin_rejected_with_409(self, client, seeded, monkeypatch):
        """1. Automatic raw begin -> 409, no pending/ready mutation, no persistence call."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="automatic")

        begin_spy = MagicMock(side_effect=main.session_plan.begin_preparation)
        monkeypatch.setattr(main.session_plan, "begin_preparation", begin_spy)

        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert resp.status_code == 409, resp.text
        assert "server_owned_preparation_required" in resp.json()["detail"]
        assert begin_spy.call_count == 0

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_02_automatic_raw_complete_rejected_with_409(self, client, seeded, monkeypatch):
        """2. Automatic raw complete -> 409, no ready mutation, no persistence call."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="automatic")

        complete_spy = MagicMock(side_effect=main.session_plan.complete_preparation)
        monkeypatch.setattr(main.session_plan, "complete_preparation", complete_spy)

        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={
                "plan_revision": 1,
                "take_id": "take-001",
                "final_prompt": "forged client final prompt",
                "effective_state": {"take_choices": {"camera": "client"}},
                "mapping_version": "1.0",
                "compiler_version": "1.0",
                "provenance": {"source": "forged"},
            },
        )
        assert resp.status_code == 409, resp.text
        assert "server_owned_preparation_required" in resp.json()["detail"]
        assert complete_spy.call_count == 0

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_03_manual_raw_begin_rejected_with_409(self, client, seeded, monkeypatch):
        """3. Manual raw begin -> 409, no pending/ready mutation."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="manual")

        begin_spy = MagicMock(side_effect=main.session_plan.begin_preparation)
        monkeypatch.setattr(main.session_plan, "begin_preparation", begin_spy)

        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert resp.status_code == 409, resp.text
        assert "server_owned_preparation_required" in resp.json()["detail"]
        assert begin_spy.call_count == 0

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_04_manual_raw_complete_rejected_with_409(self, client, seeded, monkeypatch):
        """4. Manual raw complete -> 409, existing pending row unchanged."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="manual")

        # Plant an existing pending row to verify it is untouched
        now = db.now()
        db.run(
            "INSERT INTO prepared_take (session_id, plan_revision, take_id, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            sid, 1, "take-001", backend_session_plan.PREPARED_TAKE_STATUS_PENDING, now, now,
        )

        complete_spy = MagicMock(side_effect=main.session_plan.complete_preparation)
        monkeypatch.setattr(main.session_plan, "complete_preparation", complete_spy)

        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={
                "plan_revision": 1,
                "take_id": "take-001",
                "final_prompt": "manual forged prompt",
                "effective_state": {},
                "mapping_version": "1.0",
                "compiler_version": "1.0",
                "provenance": {},
            },
        )
        assert resp.status_code == 409, resp.text
        assert "server_owned_preparation_required" in resp.json()["detail"]
        assert complete_spy.call_count == 0

        row = db.one(
            "SELECT status FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
            sid, 1, "take-001",
        )
        assert row is not None
        assert row["status"] == backend_session_plan.PREPARED_TAKE_STATUS_PENDING

    def test_05_automatic_single_direct_prepare_with_manual_completion_rejected_with_409(
        self, client, seeded, monkeypatch
    ):
        """5. Automatic single direct prepare with non-empty manual_completion -> 409, zero work."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="automatic")

        finalize_spy = MagicMock(side_effect=main.resource_preparation.finalize_take_preparation)
        synth_spy = MagicMock(side_effect=main.resource_preparation.synthesize_unlocked_fields)
        monkeypatch.setattr(main.resource_preparation, "finalize_take_preparation", finalize_spy)
        monkeypatch.setattr(main.resource_preparation, "synthesize_unlocked_fields", synth_spy)

        resp = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={"plan_revision": 1, "manual_completion": {"camera": "close up"}},
        )
        assert resp.status_code == 409, resp.text
        assert "automatic_requires_operation" in resp.json()["detail"]
        assert finalize_spy.call_count == 0
        assert synth_spy.call_count == 0

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_06_automatic_single_direct_prepare_with_omitted_or_none_or_empty_manual_completion(
        self, client, seeded, monkeypatch
    ):
        """6. Automatic single direct prepare with manual_completion=None and {} -> 409."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="automatic")

        finalize_spy = MagicMock(side_effect=main.resource_preparation.finalize_take_preparation)
        monkeypatch.setattr(main.resource_preparation, "finalize_take_preparation", finalize_spy)

        for body in [
            {"plan_revision": 1},
            {"plan_revision": 1, "manual_completion": None},
            {"plan_revision": 1, "manual_completion": {}},
        ]:
            resp = client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json=body)
            assert resp.status_code == 409, resp.text
            assert "automatic_requires_operation" in resp.json()["detail"]
            assert finalize_spy.call_count == 0

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_07_automatic_bulk_prepare_rejected_with_409_zero_partial_mutations(
        self, client, seeded, monkeypatch
    ):
        """7. Automatic bulk manual_completions with automatic target -> 409, zero partial mutations."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [
            {"take_id": f"take-{i:03d}", "label": f"t{i}", "camera": "wide", "framing": "full", "pose": "p", "expression": "e"}
            for i in range(1, 4)
        ]
        _seed_plan(sid, seeded, rev, authoring_mode="automatic", takes=takes)

        finalize_spy = MagicMock(side_effect=main.resource_preparation.finalize_take_preparation)
        monkeypatch.setattr(main.resource_preparation, "finalize_take_preparation", finalize_spy)

        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/prepare",
            json={
                "plan_revision": 1,
                "take_ids": ["take-001", "take-002", "take-003"],
                "manual_completions": {"take-001": {"camera": "close up"}},
            },
        )
        assert resp.status_code == 409, resp.text
        assert "automatic_requires_operation" in resp.json()["detail"]
        assert finalize_spy.call_count == 0

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_08_automatic_direct_prepare_with_no_manual_map_rejected_with_409(
        self, client, seeded, monkeypatch
    ):
        """8. Automatic direct preparation with no manual map -> 409."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="automatic")

        finalize_spy = MagicMock(side_effect=main.resource_preparation.finalize_take_preparation)
        monkeypatch.setattr(main.resource_preparation, "finalize_take_preparation", finalize_spy)

        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/prepare",
            json={"plan_revision": 1},
        )
        assert resp.status_code == 409, resp.text
        assert "automatic_requires_operation" in resp.json()["detail"]
        assert finalize_spy.call_count == 0

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_09_manual_valid_manual_completion_reaches_ready(self, client, seeded):
        """9. Manual valid manual completion -> existing manual path reaches ready without assistant."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [
            {"take_id": "take-001", "label": "t1"}  # unset camera, framing, pose, expression
        ]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        resp = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {
                    "camera": "wide 35mm",
                    "framing": "waist up",
                    "pose": "standing still",
                    "expression": "neutral smile",
                },
            },
        )
        assert resp.status_code == 200, resp.text
        snap = resp.json()
        assert snap["status"] == "ready"
        assert snap["take_id"] == "take-001"

        row = db.one(
            "SELECT status FROM prepared_take WHERE session_id = ? AND take_id = ?",
            sid, "take-001",
        )
        assert row is not None
        assert row["status"] == "ready"

    def test_10_manual_locked_or_already_set_field_rejected(self, client, seeded):
        """10. Manual locked/already-set field -> existing validator refusal remains, no ready mutation."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [
            {
                "take_id": "take-001",
                "label": "t1",
                "camera": "locked 50mm",
                "framing": "medium",
                "pose": "standing",
                "expression": "calm",
            }
        ]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        resp = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {"camera": "override attempt"},
            },
        )
        assert resp.status_code == 422, resp.text
        assert "manual_completion cannot override an existing take choice" in resp.json()["detail"]

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_11_manual_reserved_or_unknown_override_rejected(self, client, seeded):
        """11. Manual reserved/unknown override -> existing validator refusal remains."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{"take_id": "take-001", "label": "t1"}]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        resp = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {"look": "attempt override look"},
            },
        )
        assert resp.status_code == 422, resp.text
        assert "not an allowed take descriptive choice" in resp.json()["detail"]

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_12_manual_authority_rejection_causes_zero_assistant_calls(self, client, seeded, monkeypatch):
        """12. Manual authority rejection -> no writer/assistant/provider call."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="manual")

        synth_spy = MagicMock()
        monkeypatch.setattr(main.resource_preparation, "synthesize_unlocked_fields", synth_spy)

        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert resp.status_code == 409, resp.text
        assert synth_spy.call_count == 0

    def test_13_pre_authoring_raw_begin_succeeds(self, client, seeded):
        """13. Pre-authoring resource-v1 raw begin -> historical pending behavior."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode=None)  # No authoring key

        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending"

        row = db.one(
            "SELECT status FROM prepared_take WHERE session_id = ? AND take_id = ?",
            sid, "take-001",
        )
        assert row is not None
        assert row["status"] == "pending"

    def test_14_pre_authoring_raw_complete_succeeds(self, client, seeded):
        """14. Pre-authoring resource-v1 raw complete -> historical ready behavior."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode=None)

        # Begin first
        resp_begin = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert resp_begin.status_code == 200

        resp_complete = client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={
                "plan_revision": 1,
                "take_id": "take-001",
                "final_prompt": "historical expert final prompt",
                "effective_state": {"take_choices": {"camera": "eye level"}},
                "mapping_version": "1.0",
                "compiler_version": "1.0",
                "provenance": {"source": "expert"},
            },
        )
        assert resp_complete.status_code == 200, resp_complete.text
        snap = resp_complete.json()
        assert snap["status"] == "ready"
        assert snap["final_prompt"] == "historical expert final prompt"

        row = db.one(
            "SELECT status, final_prompt FROM prepared_take WHERE session_id = ? AND take_id = ?",
            sid, "take-001",
        )
        assert row is not None
        assert row["status"] == "ready"
        assert row["final_prompt"] == "historical expert final prompt"

    def test_15_pre_authoring_historical_manual_completion_unchanged(self, client, seeded):
        """15. Pre-authoring historical manual completion -> unchanged where existing finalizer supports it."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{"take_id": "take-001", "label": "t1"}]
        _seed_plan(sid, seeded, rev, authoring_mode=None, takes=takes)

        resp = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {
                    "camera": "wide",
                    "framing": "medium",
                    "pose": "standing",
                    "expression": "neutral",
                },
            },
        )
        assert resp.status_code == 200, resp.text
        snap = resp.json()
        assert snap["status"] == "ready"

    def test_16_legacy_non_resource_routes_return_400(self, client, seeded):
        """16. Legacy/non-resource raw and direct routes -> 400 SessionNotInResourceMode, not 409."""
        sid = _create_legacy_session(client, seeded)

        # 1. Raw begin
        r1 = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert r1.status_code == 400, r1.text
        assert "expected 'resource-v1'" in r1.json()["detail"]

        # 2. Raw complete
        r2 = client.post(
            f"/api/sessions/{sid}/plan/preparations/complete",
            json={
                "plan_revision": 1,
                "take_id": "take-001",
                "final_prompt": "prompt",
                "effective_state": {},
                "mapping_version": "1.0",
                "compiler_version": "1.0",
                "provenance": {},
            },
        )
        assert r2.status_code == 400, r2.text
        assert "expected 'resource-v1'" in r2.json()["detail"]

        # 3. Direct single prepare
        r3 = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={"plan_revision": 1},
        )
        assert r3.status_code == 400, r3.text
        assert "expected 'resource-v1'" in r3.json()["detail"]

        # 4. Direct bulk prepare
        r4 = client.post(
            f"/api/sessions/{sid}/plan/preparations/prepare",
            json={"plan_revision": 1},
        )
        assert r4.status_code == 400, r4.text
        assert "expected 'resource-v1'" in r4.json()["detail"]

    def test_17_bulk_preflight_rejection_zero_partial_mutation(self, client, seeded):
        """17. Bulk authority/preflight rejection -> zero partial pending/ready writes."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [
            {"take_id": "take-001", "label": "t1"},  # all unset
            {
                "take_id": "take-002",
                "label": "t2",
                "camera": "fixed 50mm",  # camera is already established
                "framing": "medium",
                "pose": "standing",
                "expression": "neutral",
            },
        ]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        # take-001 has valid manual completion, but take-002 tries to override locked camera
        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/prepare",
            json={
                "plan_revision": 1,
                "take_ids": ["take-001", "take-002"],
                "manual_completions": {
                    "take-001": {
                        "camera": "35mm",
                        "framing": "waist up",
                        "pose": "standing",
                        "expression": "calm",
                    },
                    "take-002": {
                        "camera": "override attempt",
                    },
                },
            },
        )
        assert resp.status_code == 422, resp.text
        assert "manual_completion cannot override an existing take choice" in resp.json()["detail"]

        # Crucial assertion: take-001 was NOT prepared/mutated before take-002 failed!
        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0, f"Expected 0 prepared rows, found {len(rows)}"

        # Also verify unknown manual_completion target ID rejection
        resp_unknown = client.post(
            f"/api/sessions/{sid}/plan/preparations/prepare",
            json={
                "plan_revision": 1,
                "take_ids": ["take-001"],
                "manual_completions": {
                    "unknown-take-id": {"camera": "35mm"},
                },
            },
        )
        assert resp_unknown.status_code == 422, resp_unknown.text
        assert "not in target take_ids" in resp_unknown.json()["detail"]

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_18_existing_ready_immutability_and_reuse_behavior(self, client, seeded):
        """18. Existing ready/generated immutability and re-use behavior -> no regression."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [
            {
                "take_id": "take-001",
                "camera": "35mm",
                "framing": "wide",
                "pose": "standing",
                "expression": "neutral",
            }
        ]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        # Prepare once
        resp1 = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={"plan_revision": 1},
        )
        assert resp1.status_code == 200
        snap1 = resp1.json()
        assert snap1["status"] == "ready"

        # Prepare again with matching inputs (idempotent reuse)
        resp2 = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={"plan_revision": 1},
        )
        assert resp2.status_code == 200
        snap2 = resp2.json()
        assert snap2["status"] == "ready"
        assert snap2["final_prompt"] == snap1["final_prompt"]

    def test_19_classifier_fail_closed_cases(self, client, seeded):
        """19. Classifier fail-closed cases: malformed authoring returns 422 before mutation."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode=None)

        malformed_cases = [
            None,  # authoring: null
            {},    # authoring: {}
            "automatic",  # authoring: non-object
            {"schema_version": 2, "mode": "automatic"},  # wrong schema version
            {"schema_version": True, "mode": "automatic"},  # boolean True schema version
            {"schema_version": False, "mode": "automatic"},  # boolean False schema version
            {"schema_version": 1.0, "mode": "automatic"},  # float 1.0 schema version
            {"schema_version": "1", "mode": "automatic"},  # string "1" schema version
            {"schema_version": 0, "mode": "automatic"},  # integer 0 != 1
            {"schema_version": 1, "mode": "invalid_mode"},  # unknown mode
            {"schema_version": 1},  # missing mode
        ]

        for bad_auth in malformed_cases:
            # Update plan_json directly with malformed authoring
            current_rev, plan = backend_session_plan._load_current_resource_plan(sid)
            plan["authoring"] = bad_auth
            db.run(
                "UPDATE session_plan SET plan_json = ? WHERE session_id = ?",
                json.dumps(plan), sid,
            )

            # Direct classifier test
            with pytest.raises(backend_session_plan.PlanValidationError):
                backend_session_plan.classify_plan_authoring(plan)

            # Endpoint test: raw begin must fail closed with 422, not treated as pre-authoring!
            resp_begin = client.post(
                f"/api/sessions/{sid}/plan/preparations/begin",
                json={"plan_revision": 1, "take_id": "take-001"},
            )
            assert resp_begin.status_code == 422, f"Expected 422 for {bad_auth!r}, got {resp_begin.status_code}"

            # Endpoint test: direct prepare must fail closed with 422
            resp_prepare = client.post(
                f"/api/sessions/{sid}/plan/takes/take-001/prepare",
                json={"plan_revision": 1},
            )
            assert resp_prepare.status_code == 422, f"Expected 422 for {bad_auth!r}, got {resp_prepare.status_code}"

            rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
            assert len(rows) == 0

    def test_20_precedence_order_enforced(self, client, seeded, monkeypatch):
        """20. Precedence: 503 -> 404 -> 400 -> 409 stale -> 422 take -> 409 authority."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="automatic")

        # 1. Malformed Pydantic body -> 422
        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": "not_an_int", "take_id": "take-001"},
        )
        assert resp.status_code == 422

        # 2. Resource planning disabled -> 503 (even on automatic plan)
        monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "0")
        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert resp.status_code == 503
        monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)

        # 3. Nonexistent session -> 404
        resp = client.post(
            "/api/sessions/999999/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert resp.status_code == 404

        # 4. Legacy session -> 400 (not 409 authority)
        legacy_sid = _create_legacy_session(client, seeded)
        resp = client.post(
            f"/api/sessions/{legacy_sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert resp.status_code == 400

        # 5. Stale revision -> 409 stale conflict (not authority conflict)
        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 999, "take_id": "take-001"},
        )
        assert resp.status_code == 409
        assert "requested preparation revision is 999" in resp.json()["detail"]

        # 6. Nonexistent take ID -> 422 (not 409 authority)
        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "nonexistent-take"},
        )
        assert resp.status_code == 422
        assert "is not present in plan revision" in resp.json()["detail"]

        # 7. Valid target on automatic plan -> 409 authority conflict
        resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/begin",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert resp.status_code == 409
        assert "server_owned_preparation_required" in resp.json()["detail"]

    def test_21_direct_finalize_take_preparation_automatic_rejected_with_conflict(
        self, client, seeded, monkeypatch
    ):
        """21. Direct Python call to finalize_take_preparation on automatic plan -> PreparationAuthorityConflict, zero mutations."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="automatic")

        prepare_inputs_spy = MagicMock(side_effect=backend_resource_preparation.prepare_take_inputs)
        synth_spy = MagicMock(side_effect=backend_resource_preparation.synthesize_unlocked_fields)
        begin_spy = MagicMock(side_effect=backend_resource_preparation.session_plan.begin_preparation)
        complete_spy = MagicMock(side_effect=backend_resource_preparation.session_plan.complete_preparation)

        monkeypatch.setattr(backend_resource_preparation, "prepare_take_inputs", prepare_inputs_spy)
        monkeypatch.setattr(backend_resource_preparation, "synthesize_unlocked_fields", synth_spy)
        monkeypatch.setattr(backend_resource_preparation.session_plan, "begin_preparation", begin_spy)
        monkeypatch.setattr(backend_resource_preparation.session_plan, "complete_preparation", complete_spy)

        expected_exc = (
            backend_session_plan.PreparationAuthorityConflict,
            backend_resource_preparation.session_plan.PreparationAuthorityConflict,
        )
        with pytest.raises(expected_exc) as exc_info:
            backend_resource_preparation.finalize_take_preparation(sid, 1, "take-001")

        assert "automatic_requires_operation" in str(exc_info.value)
        assert prepare_inputs_spy.call_count == 0
        assert synth_spy.call_count == 0
        assert begin_spy.call_count == 0
        assert complete_spy.call_count == 0

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0
        pending_rows = db.q("SELECT * FROM prepared_take WHERE session_id = ? AND status = 'pending'", sid)
        assert len(pending_rows) == 0
        ready_rows = db.q("SELECT * FROM prepared_take WHERE session_id = ? AND status = 'ready'", sid)
        assert len(ready_rows) == 0

    def test_22_classifier_schema_version_strict_integer_one(self):
        """22. Strict schema_version type check: only int 1 is allowed, rejecting 1.0, True, False, '1', 0, 2."""
        valid_plan = {
            "version": "resource-v1",
            "authoring": {"schema_version": 1, "mode": "automatic"},
        }
        assert backend_session_plan.classify_plan_authoring(valid_plan) == backend_session_plan.PLAN_AUTHORING_KIND_AUTOMATIC

        for invalid_ver in [1.0, True, False, "1", None, 0, 2, -1, 1.5]:
            bad_plan = {
                "version": "resource-v1",
                "authoring": {"schema_version": invalid_ver, "mode": "automatic"},
            }
            with pytest.raises(backend_session_plan.PlanValidationError) as exc_info:
                backend_session_plan.classify_plan_authoring(bad_plan)
            assert "schema_version must be 1" in str(exc_info.value)
