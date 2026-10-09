"""Explicit coverage survives CAS, composition, copy-forward and linked history."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3

import pytest

import db
import main
import resource_preparation
import session_plan
from conftest import EDIT_GRAPH
from backend import saved_looks
from backend import authoring_operations
from test_authoring_operation_api import (
    _configure_assistant, _create_guided_session, _persist_response_for_state_test, _start_body,
)
from test_task13_appearance_ownership import (
    _apply_progression, _profile, _publish, _saved_bracelet_look, _session,
)
from test_task9_13_looks_demonstration import _prepare, _save, _state, _view


WARDROBE = "She wears a pleated skirt."
COVERAGE = "Her chest is bare; her hips are covered by the skirt; her feet are bare. No jacket is worn."
FULL_PROFILE_COVERAGE = (
    "The chest is fully covered. The hips are fully covered. "
    "The legs are fully covered. The feet are fully covered."
)
UNCOVERED_PROFILE_COVERAGE = (
    "The chest is uncovered. The hips are uncovered. "
    "The legs are uncovered. The feet are uncovered."
)


@pytest.fixture(autouse=True)
def isolated_authoring(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda *_args: None)

    def unexpected(*_args, **_kwargs):
        pytest.fail("coverage editing must not call a provider or run generation")

    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)
    monkeypatch.setattr(main.enhance, "run_structured", unexpected)


def _plan(client, seeded, *, mode="manual", count=2):
    sid, _ = _create_guided_session(
        client, seeded, mode=mode, photo_count=count, initial_wardrobe=WARDROBE,
    )
    view = _view(client, sid)
    if mode == "manual":
        for index, take in enumerate(view["plan"]["takes"]):
            take.update(camera="50mm", framing="full body", pose=f"beside marker {index}", expression="calm")
    view["plan"]["takes"][0]["wardrobe_coverage"] = COVERAGE
    return sid, _save(client, sid, view)


def _state(session_id):
    return {
        table: db.q(f"SELECT * FROM {table} WHERE session_id = ? ORDER BY rowid", session_id)
        for table in (
            "session_plan", "session_profile_evidence", "session_profile_coverage_revision",
            "session_take_coverage_evidence", "prepared_take", "take_resource_adaptation",
            "session_plan_approval", "authoring_operation", "shot",
        )
    }


def _ready_profile(saved):
    profile = _profile(saved, bracelet_owner="garment")
    provenance = profile["authored_provenance"]
    regions = ("chest", "hips", "legs", "feet")
    profile["baseline_coverage"] = {
        "regions": {region: "none" for region in regions},
        "provenance": provenance,
    }
    garment = saved["outfit"]["garments"][0]
    profile["garment_coverage"][garment["key"]]["worn"]["regions"] = {
        region: "full" for region in regions
    }
    profile["inventory_complete"]["value"] = True
    return profile


def _apply_profile(client, session_id, revision, saved, published):
    response = client.post(
        f"/api/sessions/{session_id}/plan/apply-automation-profile",
        json={
            "expected_revision": revision,
            "look_key": saved["key"],
            "version": saved["version"],
            "content_digest": saved["content_digest"],
            "profile_revision": published["profile_revision"],
            "profile_digest": published["profile_digest"],
            "decisions": {"look": "replace", "initial_wardrobe": "replace"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _establish_profile_look(client, session_id, view, *, ready=True):
    saved = _saved_bracelet_look()
    ordinary = session_plan.apply_saved_look(
        session_id, view["plan_revision"], saved["key"], saved["version"],
        {"look": "replace", "initial_wardrobe": "replace"},
        planning_enabled=True,
    )
    if ready:
        profile = _ready_profile(saved)
    else:
        profile = _profile(saved, bracelet_owner="garment")
    published = _publish(saved, profile)
    applied = _apply_profile(
        client, session_id, ordinary["plan_revision"], saved, published,
    )
    return saved, published, _view(client, session_id)


def _profiled_progressive_session(client, seeded):
    session_id, view = _session(client, seeded, count=2)
    saved, published, view = _establish_profile_look(client, session_id, view)
    preview = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/preview",
        json={
            "expected_revision": view["plan_revision"],
            "start_take_id": "take-001",
            "end_take_id": "take-002",
            "stage_indices": [0, 1],
            "event_policy": "replace",
        },
    )
    assert preview.status_code == 200, preview.text
    view = _apply_progression(client, session_id, preview.json())
    return session_id, saved, published, view


@pytest.mark.parametrize("bad", [None, False, [], {}, 5, "x" * 2001, "bad\x00text", "bad\x7ftext"])
def test_bad_coverage_refuses_cas_without_writes(client, seeded, bad):
    sid, view = _plan(client, seeded)
    before = _state(sid)
    view["plan"]["takes"][0]["wardrobe_coverage"] = bad
    response = client.post(f"/api/sessions/{sid}/plan", json={
        "expected_revision": view["plan_revision"], "plan": view["plan"],
    })
    assert response.status_code == 422, response.text
    assert "wardrobe_coverage" in response.text
    assert _state(sid) == before


def test_stale_coverage_save_has_zero_writes(client, seeded):
    sid, view = _plan(client, seeded)
    old = copy.deepcopy(view)
    view["plan"]["takes"][0]["wardrobe_coverage"] = "Her feet are bare."
    _save(client, sid, view)
    before = _state(sid)
    response = client.post(f"/api/sessions/{sid}/plan", json={
        "expected_revision": old["plan_revision"], "plan": old["plan"],
    })
    assert response.status_code == 409
    assert _state(sid) == before


def test_coverage_composes_once_and_invalidates_only_affected_manual_work(client, seeded):
    sid, view = _plan(client, seeded)
    rows = _prepare(client, sid, view["plan_revision"], ["take-001", "take-002"])
    first, second = rows
    assert first["final_prompt"].count(COVERAGE) == 1
    assert f"{WARDROBE} {COVERAGE}" in first["final_prompt"]
    assert first["effective_state"]["wardrobe_coverage"] == COVERAGE
    assert "wardrobe_coverage" not in second["effective_state"]
    assert COVERAGE not in second["final_prompt"]
    before = copy.deepcopy(first)

    view["plan"]["takes"][0]["wardrobe_coverage"] = "Her chest is bare."
    view = _save(client, sid, view)
    current = db.q("SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ?", sid, view["plan_revision"])
    assert [row["take_id"] for row in current] == ["take-002"]
    assert current[0]["final_prompt"] == second["final_prompt"]
    assert json.loads(current[0]["provenance"])["copy_forward"]["source_prepared_id"] == second["id"]
    invalidated = db.one("SELECT * FROM prepared_take WHERE id = ?", first["id"])
    assert invalidated["status"] == "invalidated"
    assert invalidated["final_prompt"] == before["final_prompt"]
    changed = _prepare(client, sid, view["plan_revision"], ["take-001"])[0]
    assert "Her chest is bare." in changed["final_prompt"]
    assert COVERAGE not in changed["final_prompt"]


def test_automatic_preparation_and_verified_copy_forward_keep_explicit_coverage(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    sid, view = _plan(client, seeded, mode="automatic")
    started = client.post(f"/api/sessions/{sid}/plan/authoring/operations", json=_start_body(
        revision=view["plan_revision"], take_ids=["take-001", "take-002"],
    ))
    assert started.status_code == 202, started.text
    claim = authoring_operations.load_worker_claim(sid, started.json()["operation_id"])
    for take in view["plan"]["takes"]:
        ticket = authoring_operations.renew_operation_lease(claim)
        _persist_response_for_state_test(claim, ticket, [{"target": take["take_id"], "result": {
            "camera": "50mm", "framing": "full body", "pose": f"beside {take['take_id']}", "expression": "calm",
        }}])
    original = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND take_id = 'take-001'", sid)
    assert original["final_prompt"].count(COVERAGE) == 1
    assert f"{WARDROBE} {COVERAGE}" in original["final_prompt"]
    view["plan"]["takes"][1]["wardrobe_coverage"] = "Her feet are bare."
    view = _save(client, sid, view)
    copied = db.one("SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ?", sid, view["plan_revision"])
    assert copied["take_id"] == "take-001"
    assert copied["final_prompt"] == original["final_prompt"]
    assert json.loads(copied["effective_state"])["wardrobe_coverage"] == COVERAGE
    resource_preparation.validate_authoring_prepared_evidence(sid, view["plan_revision"], "take-001")


def test_linked_coverage_history_is_frozen(client, seeded):
    sid, view = _plan(client, seeded, count=1)
    prepared = _prepare(client, sid, view["plan_revision"], ["take-001"])[0]
    approved = client.post(f"/api/sessions/{sid}/plan/review/approve", json={"plan_revision": view["plan_revision"]})
    assert approved.status_code == 200, approved.text
    linked = client.post(f"/api/sessions/{sid}/plan/preparations/submit", json={
        "plan_revision": view["plan_revision"], "take_id": "take-001",
    })
    assert linked.status_code == 200, linked.text
    before_row = db.one("SELECT * FROM prepared_take WHERE id = ?", prepared["id"])
    before_shot = db.one("SELECT * FROM shot WHERE id = ?", before_row["linked_shot_id"])
    view["plan"]["takes"][0]["wardrobe_coverage"] = "Her feet are bare."
    _save(client, sid, view)
    assert db.one("SELECT * FROM prepared_take WHERE id = ?", prepared["id"]) == before_row
    assert db.one("SELECT * FROM shot WHERE id = ?", before_row["linked_shot_id"]) == before_shot


def test_automatic_context_locks_user_coverage_and_marks_downstream_affected(client, seeded):
    sid, view = _plan(client, seeded, mode="automatic")
    prep = resource_preparation.prepare_take_inputs(sid, view["plan_revision"], "take-001")
    request = resource_preparation.assemble_writer_request(prep)
    assert request["effective_state"]["wardrobe_coverage"] == COVERAGE
    assert "wardrobe_coverage" not in request["requested_fields"]
    with pytest.raises(resource_preparation.WriterOutputInvalid):
        resource_preparation.validate_writer_output(prep, {"wardrobe_coverage": "fabricated"})
    changed = copy.deepcopy(view["plan"])
    changed["takes"][0]["wardrobe_coverage"] = "Her feet are bare."
    assert session_plan._compute_affected_take_ids_for_plan_change(
        view["plan"], changed, invalidate_automatic_downstream=True,
    ) == {"take-001", "take-002"}


def test_profile_coverage_tracks_materialized_stages_into_prepared_evidence(client, seeded):
    sid, saved, published, view = _profiled_progressive_session(client, seeded)
    assert [take["wardrobe_coverage"] for take in view["plan"]["takes"]] == [
        FULL_PROFILE_COVERAGE, UNCOVERED_PROFILE_COVERAGE,
    ]
    profile_evidence = db.one(
        "SELECT * FROM session_profile_evidence WHERE session_id = ? AND plan_revision = ?",
        sid, view["plan_revision"],
    )
    coverage_rows = db.q(
        "SELECT * FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ? ORDER BY take_id",
        sid, view["plan_revision"],
    )
    assert [row["stage_index"] for row in coverage_rows] == [0, 1]
    coverage_revision = db.one(
        "SELECT * FROM session_profile_coverage_revision "
        "WHERE session_id = ? AND plan_revision = ?",
        sid, view["plan_revision"],
    )
    assert json.loads(coverage_revision["derived_take_ids_json"]) == ["take-001", "take-002"]
    assert [row["compiler_version"] for row in coverage_rows] == ["compiler-v1"] * 2
    assert all(row["profile_digest"] == published["profile_digest"] for row in coverage_rows)
    assert all(row["look_key"] == saved["key"] for row in coverage_rows)
    application = session_plan._load_session_profile_application(
        sid, view["plan_revision"], view["plan"],
    )
    ownership_digest = hashlib.sha256(json.dumps(
        application["profile"]["appearance_segments"], ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")).hexdigest()
    assert all(row["ownership_digest"] == ownership_digest for row in coverage_rows)
    assert ownership_digest != profile_evidence["projection_digest"]
    profile_stages = saved_looks._automation_profile_coverage_stage_states(saved)
    for index, row in enumerate(coverage_rows):
        stage_identity = {
            "stage_index": row["stage_index"],
            "stage_state": profile_stages[row["stage_index"]],
            "wardrobe": session_plan.resolve_effective_wardrobes(view["plan"])[row["take_id"]],
        }
        assert row["stage_digest"] == hashlib.sha256(json.dumps(
            stage_identity, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")).hexdigest()
        assert index == row["stage_index"]
    assert [row["text_digest"] for row in coverage_rows] == [
        hashlib.sha256(text.encode("utf-8")).hexdigest()
        for text in (FULL_PROFILE_COVERAGE, UNCOVERED_PROFILE_COVERAGE)
    ]
    with pytest.raises(sqlite3.IntegrityError, match="session take coverage evidence is immutable"):
        with db.transaction():
            db.run(
                "UPDATE session_take_coverage_evidence SET stage_index = 2 "
                "WHERE session_id = ? AND plan_revision = ? AND take_id = 'take-001'",
                sid, view["plan_revision"],
            )
    with pytest.raises(sqlite3.IntegrityError, match="session profile coverage revision is immutable"):
        with db.transaction():
            db.run(
                "UPDATE session_profile_coverage_revision SET created_at = 'changed' "
                "WHERE session_id = ? AND plan_revision = ?",
                sid, view["plan_revision"],
            )

    prepared = _prepare(client, sid, view["plan_revision"], ["take-001", "take-002"])
    for row, coverage in zip(prepared, (FULL_PROFILE_COVERAGE, UNCOVERED_PROFILE_COVERAGE)):
        assert row["effective_state"]["wardrobe_coverage"] == coverage
        assert row["final_prompt"].count(coverage) == 1
        provenance = row["provenance"]
        if isinstance(provenance, str):
            provenance = json.loads(provenance)
        consumed = provenance["profile_coverage_evidence"]
        assert consumed["profile_digest"] == published["profile_digest"]
        assert consumed["ownership_digest"] == ownership_digest
        assert consumed["coverage_text_digest"] == hashlib.sha256(coverage.encode("utf-8")).hexdigest()
        assert consumed["derived_coverage"]["stage_index"] == coverage_rows[
            0 if row["take_id"] == "take-001" else 1
        ]["stage_index"]
        assert consumed["derived_coverage"]["stage_digest"] == coverage_rows[
            0 if row["take_id"] == "take-001" else 1
        ]["stage_digest"]

    stored = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = 'take-001'",
        sid, view["plan_revision"],
    )
    tampered = dict(stored)
    tampered_provenance = json.loads(tampered["provenance"])
    tampered_provenance["profile_coverage_evidence"]["ownership_digest"] = "0" * 64
    tampered["provenance"] = json.dumps(tampered_provenance)
    before_validation = _state(sid)
    with pytest.raises(session_plan.AuthoringEvidenceInvalid):
        resource_preparation.validate_authoring_prepared_evidence(
            sid, view["plan_revision"], "take-001", row=tampered,
        )
    assert _state(sid) == before_validation


def test_profile_free_copy_digest_keeps_historical_shape():
    row = {
        "effective_state": "{\"wardrobe_coverage\":\"\"}",
        "mapping_version": "map-v1",
        "compiler_version": "compiler-v1",
    }
    provenance = {
        "selected_resource_revisions": [],
        "authoring_evidence": {
            "resource_projection": {"selected": []},
            "writer_context": None,
            "predecessor_projection": None,
        },
    }
    expected = resource_preparation.resource_store.canonical_digest({
        "effective_state": row["effective_state"],
        "mapping_version": row["mapping_version"],
        "compiler_version": row["compiler_version"],
        "selected_resource_revisions": [],
        "resource_projection": {"selected": []},
        "writer_context": None,
        "predecessor_projection": None,
    })
    assert resource_preparation._copy_input_digest(row, provenance) == expected
    assert resource_preparation._copy_input_digest(
        row, {**provenance, "profile_coverage_evidence": None},
    ) == expected
    assert resource_preparation._copy_input_digest(
        row, {**provenance, "profile_coverage_evidence": {
            "ownership_digest": "a" * 64,
            "coverage_text_digest": "b" * 64,
        }},
    ) != expected


def test_missing_derived_coverage_row_refuses_preparation_without_writes(client, seeded):
    sid, _saved, _published, view = _profiled_progressive_session(client, seeded)
    marker = db.one(
        "SELECT derived_take_ids_json FROM session_profile_coverage_revision "
        "WHERE session_id = ? AND plan_revision = ?",
        sid, view["plan_revision"],
    )
    assert json.loads(marker["derived_take_ids_json"]) == ["take-001", "take-002"]
    db.run(
        "DELETE FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = 'take-001'",
        sid, view["plan_revision"],
    )
    before = _state(sid)

    response = client.post(
        f"/api/sessions/{sid}/plan/preparations/prepare",
        json={"plan_revision": view["plan_revision"], "take_ids": ["take-001"]},
    )
    assert response.status_code == 422, response.text
    assert "profile coverage rows do not match their revision evidence contract" in response.text
    assert _state(sid) == before


def test_manual_coverage_edit_revokes_derived_claim_and_keeps_linked_history(client, seeded):
    sid, _saved, _published, view = _profiled_progressive_session(client, seeded)
    prepared = _prepare(client, sid, view["plan_revision"], ["take-001", "take-002"])
    approved = client.post(f"/api/sessions/{sid}/plan/review/approve", json={
        "plan_revision": view["plan_revision"],
    })
    assert approved.status_code == 200, approved.text
    linked = client.post(f"/api/sessions/{sid}/plan/preparations/submit", json={
        "plan_revision": view["plan_revision"], "take_id": "take-001",
    })
    assert linked.status_code == 200, linked.text
    before_prepared = db.one("SELECT * FROM prepared_take WHERE id = ?", prepared[0]["id"])
    before_shot = db.one("SELECT * FROM shot WHERE id = ?", before_prepared["linked_shot_id"])
    historical_evidence = db.one(
        "SELECT * FROM session_take_coverage_evidence WHERE session_id = ? "
        "AND plan_revision = ? AND take_id = 'take-001'",
        sid, view["plan_revision"],
    )

    view["plan"]["takes"][0]["wardrobe_coverage"] = "Her chest is bare."
    updated = _save(client, sid, view)
    current_evidence = db.q(
        "SELECT take_id FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ? ORDER BY take_id",
        sid, updated["plan_revision"],
    )
    assert current_evidence == [{"take_id": "take-002"}]
    current_contract = db.one(
        "SELECT derived_take_ids_json FROM session_profile_coverage_revision "
        "WHERE session_id = ? AND plan_revision = ?",
        sid, updated["plan_revision"],
    )
    assert json.loads(current_contract["derived_take_ids_json"]) == ["take-002"]
    assert db.one("SELECT * FROM session_take_coverage_evidence WHERE session_id = ? "
                  "AND plan_revision = ? AND take_id = 'take-001'",
                  sid, updated["plan_revision"]) is None
    assert db.one("SELECT * FROM session_take_coverage_evidence WHERE session_id = ? "
                  "AND plan_revision = ? AND take_id = 'take-001'",
                  sid, view["plan_revision"]) == historical_evidence
    assert db.one("SELECT * FROM prepared_take WHERE id = ?", prepared[0]["id"]) == before_prepared
    assert db.one("SELECT * FROM shot WHERE id = ?", before_prepared["linked_shot_id"]) == before_shot

    copied = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ?",
        sid, updated["plan_revision"],
    )
    assert copied["take_id"] == "take-002"
    assert copied["final_prompt"] == prepared[1]["final_prompt"]
    assert json.loads(copied["provenance"])["copy_forward"]["source_prepared_id"] == prepared[1]["id"]


def test_incomplete_profile_application_preserves_manual_coverage_without_claim(client, seeded):
    sid, view = _session(client, seeded, count=2)
    saved = _saved_bracelet_look()
    ordinary = session_plan.apply_saved_look(
        sid, view["plan_revision"], saved["key"], saved["version"],
        {"look": "replace", "initial_wardrobe": "replace"}, planning_enabled=True,
    )
    view = _view(client, sid)
    view["plan"]["takes"][0]["wardrobe_coverage"] = COVERAGE
    view = _save(client, sid, view)
    published = _publish(saved, _profile(saved, bracelet_owner="garment"))
    applied = _apply_profile(client, sid, view["plan_revision"], saved, published)
    accepted = _view(client, sid)
    assert db.one(
        "SELECT evidence_version FROM session_profile_coverage_revision "
        "WHERE session_id = ? AND plan_revision = ?",
        sid, applied["plan_revision"],
    ) == {"evidence_version": 1}
    assert db.one(
        "SELECT derived_take_ids_json FROM session_profile_coverage_revision "
        "WHERE session_id = ? AND plan_revision = ?",
        sid, applied["plan_revision"],
    ) == {"derived_take_ids_json": "[]"}
    assert accepted["plan"]["takes"][0]["wardrobe_coverage"] == COVERAGE
    assert db.q(
        "SELECT * FROM session_take_coverage_evidence WHERE session_id = ? AND plan_revision = ?",
        sid, applied["plan_revision"],
    ) == []

    prepared = _prepare(client, sid, applied["plan_revision"], ["take-001"])[0]
    assert prepared["effective_state"]["wardrobe_coverage"] == COVERAGE
    assert prepared["final_prompt"].count(COVERAGE) == 1
    provenance = prepared["provenance"]
    if isinstance(provenance, str):
        provenance = json.loads(provenance)
    assert provenance["profile_coverage_evidence"]["derived_coverage"] is None
    assert provenance["profile_coverage_evidence"]["profile_digest"] == published["profile_digest"]
    assert provenance["profile_coverage_evidence"]["coverage_text_digest"] == hashlib.sha256(
        COVERAGE.encode("utf-8")
    ).hexdigest()
    stored = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = 'take-001'",
        sid, applied["plan_revision"],
    )
    tampered = dict(stored)
    tampered_provenance = json.loads(tampered["provenance"])
    tampered_provenance.pop("profile_coverage_evidence")
    tampered["provenance"] = json.dumps(tampered_provenance)
    with pytest.raises(session_plan.AuthoringEvidenceInvalid):
        resource_preparation.validate_authoring_prepared_evidence(
            sid, applied["plan_revision"], "take-001", row=tampered,
        )

    # Unmarked profile-bound rows predate this evidence contract and keep their
    # legacy shape until a new plan revision is explicitly established.
    db.run(
        "DELETE FROM session_profile_coverage_revision "
        "WHERE session_id = ? AND plan_revision = ?",
        sid, applied["plan_revision"],
    )
    resource_preparation.validate_authoring_prepared_evidence(
        sid, applied["plan_revision"], "take-001", row=tampered,
    )
    rebound = _apply_profile(
        client, sid, applied["plan_revision"], saved, published,
    )
    assert rebound["plan_revision"] == applied["plan_revision"] + 1
    assert db.one(
        "SELECT evidence_version FROM session_profile_coverage_revision "
        "WHERE session_id = ? AND plan_revision = ?",
        sid, rebound["plan_revision"],
    ) == {"evidence_version": 1}
    assert ordinary["plan_revision"] < applied["plan_revision"]


def test_manual_edit_to_automatic_derived_coverage_invalidates_downstream_preparations(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    sid, view = _plan(client, seeded, mode="automatic")
    saved = _saved_bracelet_look()
    published = _publish(saved, _ready_profile(saved))
    applied = _apply_profile(client, sid, view["plan_revision"], saved, published)
    view = _view(client, sid)
    started = client.post(f"/api/sessions/{sid}/plan/authoring/operations", json=_start_body(
        revision=applied["plan_revision"], take_ids=["take-001", "take-002"],
    ))
    assert started.status_code == 202, started.text
    claim = authoring_operations.load_worker_claim(sid, started.json()["operation_id"])
    for take in view["plan"]["takes"]:
        ticket = authoring_operations.renew_operation_lease(claim)
        _persist_response_for_state_test(claim, ticket, [{"target": take["take_id"], "result": {
            "camera": "50mm", "framing": "full body", "pose": f"beside {take['take_id']}", "expression": "calm",
        }}])
    original = db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? ORDER BY take_id",
        sid, applied["plan_revision"],
    )
    assert [row["effective_state"] for row in original]
    assert all(json.loads(row["effective_state"])["wardrobe_coverage"] == FULL_PROFILE_COVERAGE for row in original)

    view["plan"]["takes"][0]["wardrobe_coverage"] = "Her chest is bare."
    updated = _save(client, sid, view)
    assert [db.one("SELECT status FROM prepared_take WHERE id = ?", row["id"])["status"]
            for row in original] == ["invalidated", "invalidated"]
    assert db.q(
        "SELECT take_id FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ? ORDER BY take_id",
        sid, updated["plan_revision"],
    ) == [{"take_id": "take-002"}]


@pytest.mark.parametrize("kind, included", [("edit", False), ("", False), ("guide", True)])
def test_reference_prompt_preserves_graph_kind_behavior(client, seeded, kind, included):
    sid, view = _plan(client, seeded)
    workflow = client.post("/api/workflows", json={"name": "invented reference workflow", "graph": EDIT_GRAPH, "kind": kind})
    assert workflow.status_code == 200, workflow.text
    db.run("UPDATE session SET reference_workflow_id = ? WHERE id = ?", workflow.json()["id"], sid)
    prep = resource_preparation.prepare_take_inputs(sid, view["plan_revision"], "take-001")
    prep["reference"] = True
    prompt = resource_preparation.compose_final_prompt(sid, prep)
    assert (COVERAGE in prompt) is included
    assert (WARDROBE in prompt) is included
    assert prompt.count(COVERAGE) == int(included)


@pytest.mark.parametrize("coverage", [None, "", " \t\r\n "])
def test_omitted_or_blank_coverage_keeps_default_prompt_and_state(client, seeded, coverage):
    sid, view = _plan(client, seeded)
    view["plan"]["takes"][0].pop("wardrobe_coverage")
    baseline = _save(client, sid, view)
    baseline_prep = resource_preparation.prepare_take_inputs(sid, baseline["plan_revision"], "take-001")
    baseline_prompt = resource_preparation.compose_final_prompt(sid, baseline_prep)
    if coverage is not None:
        baseline["plan"]["takes"][0]["wardrobe_coverage"] = coverage
        baseline = _save(client, sid, baseline)
    prep = resource_preparation.prepare_take_inputs(sid, baseline["plan_revision"], "take-001")
    assert prep["effective_state"] == baseline_prep["effective_state"]
    assert resource_preparation.compose_final_prompt(sid, prep) == baseline_prompt
