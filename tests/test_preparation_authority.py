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

    @pytest.mark.parametrize("field", ["look", "initial_wardrobe", "label"])
    def test_11_manual_reserved_or_unknown_override_rejected(self, client, seeded, field):
        """11. Manual reserved/unknown override -> existing validator refusal remains."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{"take_id": "take-001", "label": "t1"}]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        resp = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {field: f"attempt override {field}"},
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


class TestTask24AuthoringEvidenceContract:
    """Normative tests for OpenSpec Task 2.4: Authoring evidence derivation, validation, and guards."""

    def test_01_manual_authoring_derives_final_prompt_server_side(self, client, seeded):
        """1. Manual authoring derives final_prompt server-side; client cannot choose or alter it."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{"take_id": "take-001", "label": "t1"}]
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
                "final_prompt": "client attempted custom prompt",
            },
        )
        assert resp.status_code == 200, resp.text
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        assert row is not None
        assert "client attempted custom prompt" not in row["final_prompt"]
        assert "wide 35mm" in row["final_prompt"]
        assert "standing still" in row["final_prompt"]

        # Directly attempting complete_preparation with altered prompt fails validation
        db.run("UPDATE prepared_take SET status = 'pending' WHERE id = ?", row["id"])
        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid) as exc_info:
            backend_session_plan.complete_preparation(
                sid, 1, "take-001",
                final_prompt="altered prompt without matching evidence",
                effective_state=json.loads(row["effective_state"]),
                mapping_version=row["mapping_version"],
                compiler_version=row["compiler_version"],
                provenance=json.loads(row["provenance"]),
            )
        assert "authoring_evidence_invalid" in str(exc_info.value)

    def test_02_manual_authoring_persists_exact_server_derived_effective_state(self, client, seeded):
        """2. Manual authoring persists exact server-derived effective_state."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{"take_id": "take-001", "label": "t1"}]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        resp = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {
                    "camera": "close-up",
                    "framing": "headshot",
                    "pose": "looking forward",
                    "expression": "thoughtful",
                },
            },
        )
        assert resp.status_code == 200
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        eff = json.loads(row["effective_state"])
        assert eff["look"] == INV_LOOK
        assert eff["wardrobe"] == INV_WARDROBE
        assert eff["take_choices"] == {
            "camera": "close-up",
            "framing": "headshot",
            "pose": "looking forward",
            "expression": "thoughtful",
        }

    def test_03_manual_authoring_persists_exact_server_versions(self, client, seeded):
        """3. Manual authoring persists exact server compiler_version and mapping_version."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{"take_id": "take-001", "label": "t1"}]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        resp = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {
                    "camera": "eye level",
                    "framing": "medium shot",
                    "pose": "standing",
                    "expression": "neutral",
                },
            },
        )
        assert resp.status_code == 200
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        assert row["compiler_version"] == backend_resource_preparation.COMPILER_VERSION
        assert row["mapping_version"] == backend_resource_preparation.MAPPING_VERSION

        prov = json.loads(row["provenance"])
        assert prov["compiler_version"] == backend_resource_preparation.COMPILER_VERSION
        assert prov["mapping_version"] == backend_resource_preparation.MAPPING_VERSION
        assert prov["preparation_version"] == backend_resource_preparation.PREPARATION_VERSION

    def test_04_accepted_manual_completion_present_as_manual_provenance(self, client, seeded):
        """4. Accepted manual completion is present as manual provenance."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{"take_id": "take-001", "label": "t1"}]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {
                    "camera": "35mm prime",
                    "framing": "close up",
                    "pose": "profile turn",
                    "expression": "calm",
                },
            },
        )
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        prov = json.loads(row["provenance"])
        auth_ev = prov["authoring_evidence"]
        assert auth_ev["manual_completion"]["descriptive_inputs"] == {
            "camera": "35mm prime",
            "framing": "close up",
            "pose": "profile turn",
            "expression": "calm",
        }

    def test_05_manual_provenance_has_no_fake_assistant_request_output_or_op_id(self, client, seeded):
        """5. Manual provenance has no fake assistant request/output or operation ID."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{"take_id": "take-001", "label": "t1"}]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {
                    "camera": "35mm",
                    "framing": "wide",
                    "pose": "standing",
                    "expression": "neutral",
                },
            },
        )
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        prov = json.loads(row["provenance"])
        auth_ev = prov["authoring_evidence"]
        assert auth_ev["source"] == "manual"
        assert auth_ev["operation_id"] is None
        assert auth_ev["predecessor_projection"] == {"status": "not_applicable"}
        assert auth_ev["duplicate_flags"] == {"status": "not_applicable", "flags": []}
        assert auth_ev["writer_synthesis"]["kind"] == "manual"
        assert auth_ev["writer_synthesis"]["writer_input"] is None
        assert auth_ev["writer_synthesis"]["writer_output"] is None
        assert auth_ev["writer_synthesis"]["requested_fields"] == []

    def test_06_direct_automatic_finalization_remains_rejected_before_assistant_work(self, client, seeded):
        """6. Direct automatic finalization remains rejected before assistant work or ready mutation."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="automatic")

        with pytest.raises(backend_session_plan.PreparationAuthorityConflict):
            backend_resource_preparation.finalize_take_preparation(sid, 1, "take-001")

        rows = db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid)
        assert len(rows) == 0

    def test_07_ready_row_with_missing_evidence_rejected_by_review(self, client, seeded):
        """7. A current authoring ready row with missing evidence is rejected by review with 409."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        # Directly insert a ready row without authoring_evidence in provenance
        now = db.now()
        fake_prov = json.dumps({
            "compiler_version": backend_resource_preparation.COMPILER_VERSION,
            "mapping_version": backend_resource_preparation.MAPPING_VERSION,
            "preparation_version": backend_resource_preparation.PREPARATION_VERSION,
        })
        db.run(
            "INSERT INTO prepared_take (session_id, plan_revision, take_id, final_prompt, "
            "effective_state, mapping_version, compiler_version, provenance, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)",
            sid, 1, "take-001", "A simple prompt", "{}",
            backend_resource_preparation.MAPPING_VERSION,
            backend_resource_preparation.COMPILER_VERSION,
            fake_prov, now, now,
        )

        resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert resp.status_code == 409
        assert "authoring_evidence_invalid" in resp.json()["detail"]

        resp_plan = client.get(f"/api/sessions/{sid}/plan/review")
        assert resp_plan.status_code == 409
        assert "authoring_evidence_invalid" in resp_plan.json()["detail"]

    def test_08_altered_evidence_fields_rejected_by_review(self, client, seeded):
        """8. Altered final prompt/effective state/version/provenance/resource digest is rejected by review."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        assert row is not None

        # a) Altered final_prompt
        db.run("UPDATE prepared_take SET final_prompt = 'altered text' WHERE id = ?", row["id"])
        resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert resp.status_code == 409
        assert "authoring_evidence_invalid" in resp.json()["detail"]

        # Restore prompt, alter digest
        db.run("UPDATE prepared_take SET final_prompt = ? WHERE id = ?", row["final_prompt"], row["id"])
        prov = json.loads(row["provenance"])
        prov["authoring_evidence"]["effective_resource_input_digest"]["digest"] = "0" * 64
        db.run("UPDATE prepared_take SET provenance = ? WHERE id = ?", json.dumps(prov), row["id"])
        resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert resp.status_code == 409
        assert "authoring_evidence_invalid" in resp.json()["detail"]

        # Restore provenance, alter effective_state.take_choices semantically
        db.run("UPDATE prepared_take SET provenance = ? WHERE id = ?", row["provenance"], row["id"])
        eff = json.loads(row["effective_state"])
        assert "take_choices" in eff
        assert eff["take_choices"].get("pose") == "standing"
        eff["take_choices"]["pose"] = "sitting"
        corrupt_eff = json.dumps(eff)
        db.run("UPDATE prepared_take SET effective_state = ? WHERE id = ?", corrupt_eff, row["id"])

        # Direct validator rejects
        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_resource_preparation.validate_authoring_prepared_evidence(sid, 1, "take-001")

        # Review returns HTTP 409 with authoring_evidence_invalid
        resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert resp.status_code == 409
        assert "authoring_evidence_invalid" in resp.json()["detail"]

        # The row remains exactly as mutated; no backfill/repair occurs
        row_after = db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])
        assert row_after["effective_state"] == corrupt_eff

    def test_09_wrong_mode_evidence_rejected_by_review(self, client, seeded):
        """9. Wrong-mode evidence is rejected by review."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        prov = json.loads(row["provenance"])
        prov["authoring_evidence"]["mode"] = "automatic"
        db.run("UPDATE prepared_take SET provenance = ? WHERE id = ?", json.dumps(prov), row["id"])

        resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert resp.status_code == 409
        assert "authoring_evidence_invalid" in resp.json()["detail"]

    def test_10_recovery_does_not_list_invalid_authoring_row_as_completed_and_does_not_mutate(self, client, seeded):
        """10. Recovery does not list an invalid authoring row as completed and does not mutate it."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        prov = json.loads(row["provenance"])
        prov["authoring_evidence"]["effective_resource_input_digest"]["digest"] = "f" * 64
        corrupt_prov = json.dumps(prov)
        db.run("UPDATE prepared_take SET provenance = ? WHERE id = ?", corrupt_prov, row["id"])

        rec = backend_session_plan.recover_preparation(sid)
        assert rec["completed"] == []
        assert len(rec["incomplete"]) == 1
        assert rec["incomplete"][0]["take_id"] == "take-001"
        assert rec["incomplete"][0]["status"] == "invalid_evidence"
        assert rec["incomplete"][0]["diagnostic"] == "authoring_evidence_invalid"

        # Verify zero mutation in database
        after = db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])
        assert after["provenance"] == corrupt_prov

    def test_11_approve_rejects_invalid_evidence_before_creating_updating_approval(self, client, seeded):
        """11. Approve rejects invalid evidence before creating/updating approval."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        db.run("UPDATE prepared_take SET final_prompt = 'corrupt' WHERE id = ?", row["id"])

        resp = client.post(f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": 1})
        assert resp.status_code == 409
        assert "authoring_evidence_invalid" in resp.json()["detail"]

        approvals = db.q("SELECT * FROM session_plan_approval WHERE session_id = ?", sid)
        assert len(approvals) == 0

    def test_12_submit_rejects_invalid_evidence_with_zero_new_writes(self, client, seeded):
        """12. Submit rejects invalid evidence before shot insertion or prepared-row linking, with zero new writes."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        # Approve before tampering
        backend_session_plan.approve_plan_review(sid, 1)

        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        db.run("UPDATE prepared_take SET final_prompt = 'tampered' WHERE id = ?", row["id"])

        resp = client.post(f"/api/sessions/{sid}/plan/preparations/submit", json={"plan_revision": 1, "take_id": "take-001"})
        assert resp.status_code == 409
        assert "authoring_evidence_invalid" in resp.json()["detail"]

        shots = db.q("SELECT * FROM shot WHERE session_id = ?", sid)
        assert len(shots) == 0

        after_row = db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])
        assert after_row["status"] == "ready"
        assert after_row["linked_shot_id"] is None

    def test_13_valid_manual_snapshot_follows_flow_and_idempotent_retry(self, client, seeded):
        """13. A valid manual snapshot follows the existing review -> approve -> submit flow and remains idempotent on generated retry."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        # 1. Prepare
        prep_resp = client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        assert prep_resp.status_code == 200

        # 2. Review
        rev_resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert rev_resp.status_code == 200
        assert rev_resp.json()["snapshot"] is not None

        # 3. Approve
        app_resp = client.post(f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": 1})
        assert app_resp.status_code == 200
        assert app_resp.json()["approved"] is True

        # 4. Submit
        sub_resp1 = client.post(f"/api/sessions/{sid}/plan/preparations/submit", json={"plan_revision": 1, "take_id": "take-001"})
        assert sub_resp1.status_code == 200
        shot_id1 = sub_resp1.json()["shot_id"]
        assert shot_id1 is not None

        # 5. Idempotent retry
        sub_resp2 = client.post(f"/api/sessions/{sid}/plan/preparations/submit", json={"plan_revision": 1, "take_id": "take-001"})
        assert sub_resp2.status_code == 200
        assert sub_resp2.json()["shot_id"] == shot_id1

        shots = db.q("SELECT * FROM shot WHERE session_id = ?", sid)
        assert len(shots) == 1

    def test_14_pre_authoring_historical_snapshots_use_existing_arbitrary_behavior(self, client, seeded):
        """14. Pre-authoring historical snapshots still use their existing arbitrary completion/provenance behavior."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode=None, takes=takes)  # pre-authoring plan

        # Raw begin and complete with arbitrary provenance
        backend_session_plan.begin_preparation(sid, 1, "take-001")
        arbitrary_prov = {"historical": True, "custom_field": 123}
        backend_session_plan.complete_preparation(
            sid, 1, "take-001",
            final_prompt="historical custom prompt",
            effective_state={"look": "historical"},
            mapping_version="hist-v1",
            compiler_version="hist-v1",
            provenance=arbitrary_prov,
        )

        # Review succeeds without requiring authoring_evidence
        rev_resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert rev_resp.status_code == 200

        # Approve and submit succeed
        app_resp = client.post(f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": 1})
        assert app_resp.status_code == 200

        sub_resp = client.post(f"/api/sessions/{sid}/plan/preparations/submit", json={"plan_revision": 1, "take_id": "take-001"})
        assert sub_resp.status_code == 200
        assert sub_resp.json()["shot_id"] is not None

        # Recovery lists as completed
        rec = backend_session_plan.recover_preparation(sid)
        assert len(rec["completed"]) == 1

    def test_15_legacy_sessions_remain_unchanged(self, client, seeded):
        """15. Legacy sessions remain unchanged."""
        sid = _create_legacy_session(client, seeded)
        resp = client.get(f"/api/sessions/{sid}/plan/review")
        assert resp.status_code == 400

        resp = client.post(f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": 1})
        assert resp.status_code == 400

    def test_16_existing_current_authoring_ready_reuse_validated_before_return(self, client, seeded):
        """16. Existing current authoring ready reuse is validated before return; a corrupt row is not silently reused."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        prov = json.loads(row["provenance"])
        prov["authoring_evidence"]["operation_id"] = "fabricated_operation_id"
        db.run("UPDATE prepared_take SET provenance = ? WHERE id = ?", json.dumps(prov), row["id"])

        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_resource_preparation.finalize_take_preparation(sid, 1, "take-001")

    def test_17_evidence_validator_is_read_only_and_never_repairs(self, client, seeded):
        """17. The evidence validator is read-only and never backfills/repairs a row."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        db.run("UPDATE prepared_take SET final_prompt = 'altered' WHERE id = ?", row["id"])

        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_resource_preparation.validate_authoring_prepared_evidence(sid, 1, "take-001")

        after = db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])
        assert after["final_prompt"] == "altered"

    def test_18_no_authoring_http_route_accepts_client_supplied_authority(self, client, seeded):
        """18. No authoring HTTP route accepts client-supplied final prompt, effective state, versions, provenance, or digest as authority."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        forged_effective_state = {
            "forged": True,
            "look": "caller forged look",
            "wardrobe": "caller forged wardrobe",
            "take_choices": {"pose": "caller forged pose"},
        }
        resp = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "final_prompt": "client_forged_prompt",
                "effective_state": forged_effective_state,
                "provenance": {"forged": True},
                "compiler_version": "forged_v99",
                "mapping_version": "forged_v99",
                "writer_output": {"forged_output": True},
                "writer_input": {"forged_input": True},
                "operation_id": "forged_op_123",
                "authoring_evidence": {
                    "writer_synthesis": {
                        "writer_output": {"forged_output": True},
                    },
                    "operation_id": "forged_op_123",
                },
            },
        )
        assert resp.status_code == 200
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        assert "client_forged_prompt" not in row["final_prompt"]
        assert row["compiler_version"] == backend_resource_preparation.COMPILER_VERSION
        assert row["mapping_version"] == backend_resource_preparation.MAPPING_VERSION

        # Verify persisted effective_state is server-owned and not caller-supplied
        persisted_effective_state = json.loads(row["effective_state"])
        assert persisted_effective_state != forged_effective_state
        assert "forged" not in persisted_effective_state

        prep = backend_resource_preparation.prepare_take_inputs(sid, 1, "take-001")
        expected_effective_state = dict(prep.get("effective_state") or {})
        expected_effective_state["take_choices"] = dict(prep.get("effective_take_choices") or {})
        assert persisted_effective_state == expected_effective_state
        assert persisted_effective_state["look"] == INV_LOOK
        assert persisted_effective_state["wardrobe"] == INV_WARDROBE
        assert persisted_effective_state["take_choices"] == {
            "camera": "eye level",
            "framing": "medium shot",
            "pose": "standing",
            "expression": "neutral",
        }

        prov = json.loads(row["provenance"])
        assert "forged" not in prov
        auth_ev = prov["authoring_evidence"]
        assert auth_ev["operation_id"] is None
        assert auth_ev["source"] == "manual"
        assert auth_ev["writer_synthesis"]["writer_input"] is None
        assert auth_ev["writer_synthesis"]["writer_output"] is None
        assert prov.get("operation_id") is None
        assert prov["writer_synthesis"]["writer_input"] is None
        assert prov["writer_synthesis"]["writer_output"] is None

    def test_19_resource_projection_order_and_canonical_digest_deterministic(self, client, seeded):
        """19. Resource projection order and canonical digest are deterministic, use authorized effective values, and exclude unused metadata."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="manual")

        prep = backend_resource_preparation.prepare_take_inputs(sid, 1, "take-001")
        proj1, digest1 = backend_resource_preparation.build_canonical_resource_projection(prep)
        proj2, digest2 = backend_resource_preparation.build_canonical_resource_projection(prep)

        assert proj1 == proj2
        assert digest1 == digest2
        assert len(digest1["digest"]) == 64
        assert digest1["digest"] == digest1["digest"].lower()
        assert digest1["version"] == 1

        # Check projection structure
        assert "selected_resource_triples" in proj1
        assert "effective_descriptive_inputs" in proj1
        assert "consumed_adaptations" in proj1

    def test_20_generated_linked_history_not_reauthored_or_duplicated(self, client, seeded):
        """20. Generated/linked history is not reauthored or duplicated by recovery or retry."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        backend_session_plan.approve_plan_review(sid, 1)
        sub1 = backend_session_plan.submit_prepared_take(sid, 1, "take-001")
        shot_id1 = sub1["shot_id"]

        # Recovery includes generated take in completed
        rec = backend_session_plan.recover_preparation(sid)
        assert len(rec["completed"]) == 1
        assert rec["completed"][0]["take_id"] == "take-001"
        assert rec["completed"][0]["status"] == "generated"
        assert rec["completed"][0]["linked_shot_id"] == shot_id1

        # Retry submit returns existing shot without duplicate
        sub2 = backend_session_plan.submit_prepared_take(sid, 1, "take-001")
        assert sub2["shot_id"] == shot_id1
        shots = db.q("SELECT * FROM shot WHERE session_id = ?", sid)
        assert len(shots) == 1


class TestTask24ClosedNestedShapesAndProbes:
    """Rigorous tests for closed nested shapes, server recomputation, and probes A/B/C."""

    @staticmethod
    def _prepared_manual_row(client, seeded):
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="manual")
        response = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={"plan_revision": 1},
        )
        assert response.status_code == 200
        row = db.one(
            "SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?",
            sid,
            "take-001",
        )
        assert row is not None
        return sid, row

    @staticmethod
    def _set_evidence_path(provenance, path, value):
        target = provenance
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    @pytest.mark.parametrize(
        ("path", "value"),
        [
            (path, value)
            for path in (
                ("session_id",),
                ("plan_revision",),
                ("authoring_evidence", "schema_version"),
                ("authoring_evidence", "resource_projection", "version"),
                ("authoring_evidence", "effective_resource_input_digest", "version"),
            )
            for value in (True, False, 1.0, "1", None)
        ],
        ids=lambda value: repr(value),
    )
    def test_all_persisted_integer_fields_require_strict_int(self, client, seeded, path, value):
        """Every authoring-v1 integer rejects bool, float, string, and null values."""
        sid, row = self._prepared_manual_row(client, seeded)
        provenance = json.loads(row["provenance"])
        self._set_evidence_path(provenance, path, value)
        db.run(
            "UPDATE prepared_take SET provenance = ? WHERE id = ?",
            json.dumps(provenance),
            row["id"],
        )

        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_resource_preparation.validate_authoring_prepared_evidence(
                sid, 1, "take-001"
            )

    @pytest.mark.parametrize("root", [[], None, "text", 1, True])
    def test_valid_json_non_object_provenance_fails_closed(self, client, seeded, root):
        """Valid JSON scalar and collection roots never escape as built-in exceptions."""
        sid, row = self._prepared_manual_row(client, seeded)
        db.run(
            "UPDATE prepared_take SET provenance = ? WHERE id = ?",
            json.dumps(root),
            row["id"],
        )

        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_resource_preparation.validate_authoring_prepared_evidence(
                sid, 1, "take-001"
            )

    def test_malformed_provenance_json_fails_closed(self, client, seeded):
        """Syntactically invalid authoring provenance maps to the closed evidence error."""
        sid, row = self._prepared_manual_row(client, seeded)
        db.run("UPDATE prepared_take SET provenance = ? WHERE id = ?", "{", row["id"])

        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_resource_preparation.validate_authoring_prepared_evidence(
                sid, 1, "take-001"
            )

    @pytest.mark.parametrize(
        ("mutation", "value"),
        [
            ("schema_version", True),
            ("resource_projection.version", 1.0),
            ("plan_revision", True),
            ("provenance_root", []),
        ],
    )
    def test_numeric_and_root_shape_corruption_all_surfaces(
        self, client, seeded, mutation, value
    ):
        """Representative integer and root-shape corruption is write-free on every surface."""
        sid, row = self._prepared_manual_row(client, seeded)
        provenance = json.loads(row["provenance"])
        if mutation == "schema_version":
            provenance["authoring_evidence"]["schema_version"] = value
            persisted = json.dumps(provenance)
        elif mutation == "resource_projection.version":
            provenance["authoring_evidence"]["resource_projection"]["version"] = value
            persisted = json.dumps(provenance)
        elif mutation == "plan_revision":
            provenance["plan_revision"] = value
            persisted = json.dumps(provenance)
        else:
            persisted = json.dumps(value)
        db.run("UPDATE prepared_take SET provenance = ? WHERE id = ?", persisted, row["id"])
        expected_row = dict(db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"]))

        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_resource_preparation.validate_authoring_prepared_evidence(
                sid, 1, "take-001"
            )

        review = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert review.status_code == 409
        assert "authoring_evidence_invalid" in review.json()["detail"]
        assert dict(db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])) == expected_row

        recovery = backend_session_plan.recover_preparation(sid)
        assert recovery["completed"] == []
        invalid = [item for item in recovery["incomplete"] if item["take_id"] == "take-001"]
        assert invalid[0]["status"] == "invalid_evidence"
        assert invalid[0]["diagnostic"] == "authoring_evidence_invalid"
        assert dict(db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])) == expected_row

        approve = client.post(
            f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": 1}
        )
        assert approve.status_code == 409
        assert "authoring_evidence_invalid" in approve.json()["detail"]
        assert db.q("SELECT * FROM session_plan_approval WHERE session_id = ?", sid) == []

        db.run(
            "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) "
            "VALUES (?, ?, ?)",
            sid,
            1,
            db.now(),
        )
        submit = client.post(
            f"/api/sessions/{sid}/plan/preparations/submit",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert submit.status_code == 409
        assert "authoring_evidence_invalid" in submit.json()["detail"]
        assert db.q("SELECT * FROM shot WHERE session_id = ?", sid) == []
        after_submit = db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])
        assert after_submit["linked_shot_id"] is None
        assert after_submit["status"] == "ready"

    def test_raw_complete_rejects_coherent_authoring_snapshot_without_writes(
        self, client, seeded
    ):
        """Raw completion cannot promote even a coherent hand-built authoring snapshot."""
        sid, ready = self._prepared_manual_row(client, seeded)
        snapshot = {
            "final_prompt": ready["final_prompt"],
            "effective_state": json.loads(ready["effective_state"]),
            "mapping_version": ready["mapping_version"],
            "compiler_version": ready["compiler_version"],
            "provenance": json.loads(ready["provenance"]),
        }
        db.run(
            "UPDATE prepared_take SET final_prompt = '', effective_state = '{}', "
            "mapping_version = '', compiler_version = '', provenance = '{}', "
            "status = 'pending' WHERE id = ?",
            ready["id"],
        )
        before = dict(db.one("SELECT * FROM prepared_take WHERE id = ?", ready["id"]))
        approvals_before = db.q(
            "SELECT * FROM session_plan_approval WHERE session_id = ?", sid
        )
        shots_before = db.q("SELECT * FROM shot WHERE session_id = ?", sid)

        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_session_plan.complete_preparation(
                sid, 1, "take-001", **snapshot
            )

        assert dict(db.one("SELECT * FROM prepared_take WHERE id = ?", ready["id"])) == before
        assert before["status"] == "pending"
        assert before["linked_shot_id"] is None
        assert db.q("SELECT * FROM session_plan_approval WHERE session_id = ?", sid) == approvals_before
        assert db.q("SELECT * FROM shot WHERE session_id = ?", sid) == shots_before

    def test_hand_built_authoring_result_with_wrong_seal_is_rejected(
        self, client, seeded
    ):
        """The authoring persistence boundary refuses a freely constructed substitute."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        _seed_plan(sid, seeded, rev, authoring_mode="manual")
        backend_session_plan.begin_preparation(sid, 1, "take-001")
        before = dict(db.one(
            "SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?",
            sid,
            "take-001",
        ))
        substitute = backend_resource_preparation._AuthoringPreparedResult(
            object(),
            sid,
            1,
            "take-001",
            "forged prompt",
            {},
            backend_resource_preparation.MAPPING_VERSION,
            backend_resource_preparation.COMPILER_VERSION,
            {},
        )

        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_session_plan.complete_authoring_preparation(substitute)

        after = db.one("SELECT * FROM prepared_take WHERE id = ?", before["id"])
        assert dict(after) == before

    @pytest.mark.parametrize(
        "corruption",
        ["compiler_version", "nested_manual_input", "final_prompt"],
    )
    def test_authoring_http_errors_never_leak_corrupt_markers(
        self, client, seeded, corruption
    ):
        """Review, Approve, Submit, and Recovery expose only stable evidence errors."""
        marker = "SECRET_INJECTED_VALUE_2_4"
        sid, row = self._prepared_manual_row(client, seeded)
        provenance = json.loads(row["provenance"])
        if corruption == "compiler_version":
            provenance["compiler_version"] = marker
            db.run(
                "UPDATE prepared_take SET provenance = ? WHERE id = ?",
                json.dumps(provenance),
                row["id"],
            )
        elif corruption == "nested_manual_input":
            provenance["authoring_evidence"]["manual_completion"][
                "descriptive_inputs"
            ]["camera"] = marker
            db.run(
                "UPDATE prepared_take SET provenance = ? WHERE id = ?",
                json.dumps(provenance),
                row["id"],
            )
        else:
            db.run(
                "UPDATE prepared_take SET final_prompt = ? WHERE id = ?",
                marker,
                row["id"],
            )

        review = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert review.status_code == 409
        assert review.json()["detail"] == backend_session_plan.AUTHORING_EVIDENCE_PUBLIC_MESSAGE
        assert marker not in review.text

        recovery = backend_session_plan.recover_preparation(sid)
        assert recovery["completed"] == []
        invalid = [item for item in recovery["incomplete"] if item["take_id"] == "take-001"]
        assert invalid[0]["status"] == "invalid_evidence"
        assert invalid[0]["diagnostic"] == "authoring_evidence_invalid"
        assert marker not in json.dumps(invalid[0])

        approve = client.post(
            f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": 1}
        )
        assert approve.status_code == 409
        assert approve.json()["detail"] == backend_session_plan.AUTHORING_EVIDENCE_PUBLIC_MESSAGE
        assert marker not in approve.text
        assert db.q("SELECT * FROM session_plan_approval WHERE session_id = ?", sid) == []

        db.run(
            "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) "
            "VALUES (?, ?, ?)",
            sid,
            1,
            db.now(),
        )
        submit = client.post(
            f"/api/sessions/{sid}/plan/preparations/submit",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert submit.status_code == 409
        assert submit.json()["detail"] == backend_session_plan.AUTHORING_EVIDENCE_PUBLIC_MESSAGE
        assert marker not in submit.text
        assert db.q("SELECT * FROM shot WHERE session_id = ?", sid) == []
        after = db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])
        assert after["linked_shot_id"] is None
        assert after["status"] == "ready"

    @pytest.mark.parametrize(
        "mutation_name",
        [
            "manual_completion.extra",
            "writer_synthesis.extra",
            "field_mappings.forged",
        ],
    )
    def test_probes_a_b_c_all_surfaces(self, client, seeded, mutation_name):
        """Probes A, B, C: corrupt nested evidence rejected across validator, Review, Recovery, Approve, Submit."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        # Finalize valid take
        client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        assert row is not None
        prov = json.loads(row["provenance"])

        if mutation_name == "manual_completion.extra":
            prov["authoring_evidence"]["manual_completion"]["extra"] = "forbidden_manual_key"
        elif mutation_name == "writer_synthesis.extra":
            prov["authoring_evidence"]["writer_synthesis"]["extra"] = "forbidden_writer_key"
            prov["writer_synthesis"]["extra"] = "forbidden_writer_key"
        elif mutation_name == "field_mappings.forged":
            prov["field_mappings"]["forged"] = {"field": "forged", "role": "descriptive_input"}
        else:
            pytest.fail(f"unknown mutation {mutation_name}")

        db.run("UPDATE prepared_take SET provenance = ? WHERE id = ?", json.dumps(prov), row["id"])

        # 1. Direct validator
        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_resource_preparation.validate_authoring_prepared_evidence(sid, 1, "take-001")

        # 2. Review surface: HTTP 409 authoring_evidence_invalid
        rev_resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert rev_resp.status_code == 409
        assert "authoring_evidence_invalid" in rev_resp.json()["detail"]

        # 3. Recovery surface: incomplete invalid_evidence, NOT completed, zero mutations
        rec = backend_session_plan.recover_preparation(sid)
        assert len(rec["completed"]) == 0
        inc_match = [t for t in rec["incomplete"] if t.get("take_id") == "take-001"]
        assert len(inc_match) == 1
        assert inc_match[0]["status"] == "invalid_evidence"
        assert inc_match[0]["diagnostic"] == "authoring_evidence_invalid"

        db_row_after_rec = db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])
        assert db_row_after_rec["status"] == "ready"
        assert db_row_after_rec["provenance"] == json.dumps(prov)

        # 4. Approve surface: HTTP 409 and zero approval writes
        app_resp = client.post(f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": 1})
        assert app_resp.status_code == 409
        assert "authoring_evidence_invalid" in app_resp.json()["detail"]
        approvals = db.q("SELECT * FROM session_plan_approval WHERE session_id = ?", sid)
        assert len(approvals) == 0

        # 5. Submit surface: HTTP 409 and zero shot/link writes
        # Insert a valid approval bypass so submit reaches evidence validation
        db.run(
            "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) VALUES (?, ?, ?)",
            sid, 1, db.now(),
        )
        sub_resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/submit",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert sub_resp.status_code == 409
        assert "authoring_evidence_invalid" in sub_resp.json()["detail"]
        shots = db.q("SELECT * FROM shot WHERE session_id = ?", sid)
        assert len(shots) == 0
        db_row_after_sub = db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])
        assert db_row_after_sub["status"] == "ready"
        assert db_row_after_sub["linked_shot_id"] is None

    @pytest.mark.parametrize(
        "block_name",
        [
            "manual_completion",
            "writer_synthesis",
            "predecessor_projection",
            "duplicate_flags",
            "effective_resource_input_digest",
            "resource_projection",
            "manual_completion.descriptive_inputs",
            "provenance.writer_synthesis",
            "provenance.field_mappings",
            "provenance.adaptations",
        ],
    )
    def test_nested_unknown_keys_rejected(self, client, seeded, block_name):
        """All nested closed blocks reject injected unknown keys with AuthoringEvidenceInvalid."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        row = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?", sid, "take-001")
        prov = json.loads(row["provenance"])
        auth_ev = prov["authoring_evidence"]

        if block_name == "manual_completion":
            auth_ev["manual_completion"]["injected_unknown_key"] = "bad"
        elif block_name == "writer_synthesis":
            auth_ev["writer_synthesis"]["injected_unknown_key"] = "bad"
            prov["writer_synthesis"]["injected_unknown_key"] = "bad"
        elif block_name == "predecessor_projection":
            auth_ev["predecessor_projection"]["injected_unknown_key"] = "bad"
        elif block_name == "duplicate_flags":
            auth_ev["duplicate_flags"]["injected_unknown_key"] = "bad"
        elif block_name == "effective_resource_input_digest":
            auth_ev["effective_resource_input_digest"]["injected_unknown_key"] = "bad"
        elif block_name == "resource_projection":
            auth_ev["resource_projection"]["injected_unknown_key"] = "bad"
        elif block_name == "manual_completion.descriptive_inputs":
            auth_ev["manual_completion"]["descriptive_inputs"]["injected_unknown_key"] = "bad"
        elif block_name == "provenance.writer_synthesis":
            prov["writer_synthesis"]["injected_unknown_key"] = "bad"
        elif block_name == "provenance.field_mappings":
            prov["field_mappings"]["injected_unknown_key"] = {}
        elif block_name == "provenance.adaptations":
            prov["adaptations"] = [{"injected_unknown_key": "bad"}]
        else:
            pytest.fail(f"unknown block {block_name}")

        db.run("UPDATE prepared_take SET provenance = ? WHERE id = ?", json.dumps(prov), row["id"])

        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_resource_preparation.validate_authoring_prepared_evidence(sid, 1, "take-001")

    def test_positive_manual_snapshot_passes_all_surfaces(self, client, seeded):
        """A valid manual snapshot passes validator, Review, Recovery, Approve, and Submit."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode="manual", takes=takes)

        # Prepare
        prep_res = client.post(f"/api/sessions/{sid}/plan/takes/take-001/prepare", json={"plan_revision": 1})
        assert prep_res.status_code == 200

        # 1. Validator directly
        val = backend_resource_preparation.validate_authoring_prepared_evidence(sid, 1, "take-001")
        assert val is not None
        assert val.is_authoring is True
        assert val.authoring_evidence["mode"] == "manual"

        # 2. Review
        rev_resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert rev_resp.status_code == 200
        assert rev_resp.json()["snapshot"] is not None

        # 3. Recovery
        rec = backend_session_plan.recover_preparation(sid)
        assert len(rec["completed"]) == 1
        assert rec["completed"][0]["take_id"] == "take-001"
        assert len(rec["incomplete"]) == 0

        # 4. Approve
        app_resp = client.post(f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": 1})
        assert app_resp.status_code == 200
        assert app_resp.json()["approved"] is True

        # 5. Submit
        sub_resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/submit",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert sub_resp.status_code == 200
        assert sub_resp.json()["shot_id"] is not None

    def test_pre_authoring_snapshot_ignores_nested_shape_constraints(self, client, seeded):
        """Historical pre-authoring snapshot without authoring block ignores authoring shape requirements."""
        rev = _setup_resource_revision()
        sid = _create_resource_session(client, seeded)
        takes = [{
            "take_id": "take-001",
            "camera": "eye level", "framing": "medium shot",
            "pose": "standing", "expression": "neutral",
        }]
        _seed_plan(sid, seeded, rev, authoring_mode=None, takes=takes)

        backend_session_plan.begin_preparation(sid, 1, "take-001")
        arbitrary_prov = {
            "custom_arbitrary_key": "any_value",
            "nested_unclosed": {"anything": 123},
        }
        backend_session_plan.complete_preparation(
            sid, 1, "take-001",
            final_prompt="historical custom prompt",
            effective_state={"look": "historical"},
            mapping_version="hist-v1",
            compiler_version="hist-v1",
            provenance=arbitrary_prov,
        )

        val = backend_resource_preparation.validate_authoring_prepared_evidence(sid, 1, "take-001")
        assert val is not None
        assert val.is_authoring is False
        assert val.authoring_evidence is None

        # Review succeeds
        rev_resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert rev_resp.status_code == 200

        # Recovery succeeds
        rec = backend_session_plan.recover_preparation(sid)
        assert len(rec["completed"]) == 1

        # Approve succeeds
        app_resp = client.post(f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": 1})
        assert app_resp.status_code == 200

        # Submit succeeds
        sub_resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/submit",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert sub_resp.status_code == 200

    def test_forged_assistant_output_in_manual_evidence_rejected(self, client, seeded):
        """A manual authoring snapshot cannot inject structured assistant output/input into evidence."""
        sid, row = self._prepared_manual_row(client, seeded)
        prov = json.loads(row["provenance"])

        # Coherent-looking manual snapshot with structured forged assistant output/input
        forged_output = {"adapted_fields": {"pose": "standing"}, "summary": "forged assistant completion"}
        forged_input = {"requested_fields": ["pose"]}

        prov["authoring_evidence"]["writer_synthesis"]["writer_output"] = forged_output
        prov["authoring_evidence"]["writer_synthesis"]["writer_input"] = forged_input
        prov["writer_synthesis"]["writer_output"] = forged_output
        prov["writer_synthesis"]["writer_input"] = forged_input

        # Keep manual source and operation_id null
        assert prov["authoring_evidence"]["source"] == "manual"
        assert prov["authoring_evidence"]["operation_id"] is None

        db.run("UPDATE prepared_take SET provenance = ? WHERE id = ?", json.dumps(prov), row["id"])

        # 1. Direct validator rejects because manual authoring cannot have assistant output
        with pytest.raises(backend_session_plan.AuthoringEvidenceInvalid):
            backend_resource_preparation.validate_authoring_prepared_evidence(sid, 1, "take-001")

        # 2. Review surface rejects with 409
        resp = client.get(f"/api/sessions/{sid}/plan/takes/take-001/review")
        assert resp.status_code == 409
        assert "authoring_evidence_invalid" in resp.json()["detail"]

        # 3. Recovery does not list row as completed, reports incomplete with invalid_evidence status and diagnostic
        rec = backend_session_plan.recover_preparation(sid)
        assert len(rec["completed"]) == 0
        assert len(rec["incomplete"]) == 1
        assert rec["incomplete"][0]["take_id"] == "take-001"
        assert rec["incomplete"][0]["status"] == "invalid_evidence"
        assert rec["incomplete"][0]["diagnostic"] == "authoring_evidence_invalid"

        # 4. Approve surface rejects before updating approval
        app_resp = client.post(f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": 1})
        assert app_resp.status_code == 409
        assert "authoring_evidence_invalid" in app_resp.json()["detail"]
        assert len(db.q("SELECT * FROM session_plan_approval WHERE session_id = ?", sid)) == 0

        # 5. Submit surface rejects before creating shot (with approval bypass so it reaches evidence validation)
        db.run(
            "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) VALUES (?, ?, ?)",
            sid, 1, db.now(),
        )
        sub_resp = client.post(
            f"/api/sessions/{sid}/plan/preparations/submit",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert sub_resp.status_code == 409
        assert "authoring_evidence_invalid" in sub_resp.json()["detail"]

        # Verify zero-write state after rejected submit: row stays ready, linked_shot_id stays None, no shot created
        after_sub = db.one("SELECT * FROM prepared_take WHERE id = ?", row["id"])
        assert after_sub["status"] == "ready"
        assert after_sub["linked_shot_id"] is None
        shots = db.q("SELECT * FROM shot WHERE session_id = ?", sid)
        assert len(shots) == 0
