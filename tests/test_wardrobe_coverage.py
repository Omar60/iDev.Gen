"""Explicit coverage survives CAS, composition, copy-forward and linked history."""
from __future__ import annotations

import copy
import json

import pytest

import db
import main
import resource_preparation
import session_plan
from conftest import EDIT_GRAPH
from backend import authoring_operations
from test_authoring_operation_api import (
    _configure_assistant, _create_guided_session, _persist_response_for_state_test, _start_body,
)
from test_task9_13_looks_demonstration import _prepare, _save, _state, _view


WARDROBE = "She wears a pleated skirt."
COVERAGE = "Her chest is bare; her hips are covered by the skirt; her feet are bare. No jacket is worn."


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
