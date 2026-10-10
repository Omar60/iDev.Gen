"""Independent acceptance probes for profile-aware prompt finalization."""
from __future__ import annotations

import pytest

import db
import main
import resource_preparation
import session_plan
from backend import authoring_operations, saved_looks
from conftest import GRAPH
from test_authoring_operation_api import _start_body
from test_wardrobe_coverage import (
    _automatic_profiled_progressive_session,
    _profiled_progressive_session,
    _ready_profile,
    _state,
    _view as _wardrobe_view,
)
from test_automation_profile_readiness import _state_for_regions


@pytest.fixture(autouse=True)
def isolated_acceptance(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.setitem(main.CONFIG, "llm_url", "http://assistant.local/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "test-model")
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda *_args: None)

    def unexpected(*_args, **_kwargs):
        pytest.fail("acceptance probes must not call a provider or generate an image")

    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)
    monkeypatch.setattr(main.enhance, "run_structured", unexpected)


def _final_take_preparation(client, sid, view):
    return resource_preparation.prepare_take_inputs(
        sid, view["plan_revision"], "take-002",
    )


def _writer_operation_result(client, sid, view, pose):
    started = client.post(
        f"/api/sessions/{sid}/plan/authoring/operations",
        json=_start_body(revision=view["plan_revision"], take_ids=["take-002"]),
    )
    assert started.status_code == 202, started.text
    claim = authoring_operations.load_worker_claim(sid, started.json()["operation_id"])
    ticket = authoring_operations.renew_operation_lease(claim)
    preparation = _final_take_preparation(client, sid, view)
    unlocked = resource_preparation.compute_unlocked_fields(preparation)
    request = resource_preparation.assemble_writer_request(preparation, unlocked)
    context, predecessors = resource_preparation.build_automatic_writer_context(
        sid, view["plan_revision"], "take-002", preparation, request,
    )
    writer_request = {
        "request": request,
        "assistant_request": {
            "messages": [
                {"role": "system", "content": "Invented acceptance fixture."},
                {
                    "role": "user",
                    "content": resource_preparation.automatic_writer_instruction(context),
                },
            ],
            "model": "test-model",
            "parameters": {"response_format": {"type": "json_object"}},
        },
    }
    return preparation, (
        ticket,
        writer_request,
        {
            "camera": "50mm",
            "framing": "full body",
            "pose": pose,
            "expression": "calm",
        },
        context,
        predecessors,
    )


def test_automatic_writer_cannot_duplicate_locked_coverage_inside_pose(client, seeded):
    sid, _, _, view = _automatic_profiled_progressive_session(client, seeded)
    initial = _final_take_preparation(client, sid, view)
    coverage = initial["effective_state"]["wardrobe_coverage"]
    preparation, operation_result = _writer_operation_result(
        client, sid, view,
        f"She stands beside the window. {coverage}",
    )
    try:
        prepared = resource_preparation.finalize_take_preparation(
            sid, view["plan_revision"], "take-002",
            _operation_result=operation_result,
        )
    except resource_preparation.ConflictUnresolvedError:
        assert db.q(
            "SELECT * FROM prepared_take WHERE session_id = ? "
            "AND plan_revision = ? AND take_id = 'take-002' AND status = 'ready'",
            sid, view["plan_revision"],
        ) == []
        return

    count = prepared["final_prompt"].count(coverage)
    assert count == 1, (
        f"compiled coverage appears {count} times in the accepted prompt: "
        f"prepared_id={prepared.get('prepared_take_id')!r}; "
        f"{prepared['final_prompt']!r}"
    )


def test_automatic_writer_cannot_repeat_one_compiled_coverage_sentence(client, seeded):
    sid, _, _, view = _automatic_profiled_progressive_session(client, seeded)
    preparation = _final_take_preparation(client, sid, view)
    coverage = preparation["effective_state"]["wardrobe_coverage"]
    first_sentence = coverage.split(" The hips", 1)[0]
    _, operation_result = _writer_operation_result(
        client, sid, view,
        f"She stands beside the window. {first_sentence}",
    )
    try:
        prepared = resource_preparation.finalize_take_preparation(
            sid, view["plan_revision"], "take-002",
            _operation_result=operation_result,
        )
    except resource_preparation.ConflictUnresolvedError:
        assert db.q(
            "SELECT * FROM prepared_take WHERE session_id = ? "
            "AND plan_revision = ? AND take_id = 'take-002' AND status = 'ready'",
            sid, view["plan_revision"],
        ) == []
        return

    assert prepared["final_prompt"].casefold().count(first_sentence.casefold()) == 1, (
        "a writer pose repeated one locked compiler sentence in the accepted prompt: "
        f"{prepared['final_prompt']!r}"
    )


def test_aside_stage_rejects_a_positive_wearing_assertion(client, seeded):
    saved = saved_looks.create({
        "name": "Invented bracelet aside acceptance look",
        "appearance": "A portrait in a quiet room. She wears a brass bracelet.",
        "garments": [{
            "wording": "a brass bracelet",
            "aside": "a brass bracelet resting on the table",
        }],
    })
    profile = _ready_profile(saved)
    garment_key = saved["outfit"]["garments"][0]["key"]
    profile["garment_coverage"][garment_key]["aside"] = _state_for_regions(
        saved, {},
    )
    sid, _, _, view = _profiled_progressive_session(
        client, seeded, saved=saved, profile=profile,
    )
    model_id = db.one("SELECT model_id FROM session WHERE id = ?", sid)["model_id"]
    db.run(
        "UPDATE model SET base_positive = ? WHERE id = ?",
        "She wears a brass bracelet.", model_id,
    )
    preparation = _final_take_preparation(client, sid, view)
    assert preparation["profile_coverage_evidence"]["derived_coverage"]["stage_index"] == 1

    before = _state(sid)
    try:
        result = resource_preparation.finalize_take_preparation(
            sid, view["plan_revision"], "take-002",
        )
    except resource_preparation.ConflictUnresolvedError:
        assert _state(sid) == before
        return

    ready = db.q(
        "SELECT id, status, final_prompt FROM prepared_take "
        "WHERE session_id = ? AND status = 'ready' ORDER BY id",
        sid,
    )
    assert False, (
        "aside-stage wearing contradiction was finalized; "
        f"result={result!r}; ready_rows={ready!r}"
    )


def test_exact_accepted_aside_phrase_remains_usable(client, seeded):
    aside = "a brass bracelet resting on the table"
    saved = saved_looks.create({
        "name": "Invented legal aside wording acceptance look",
        "appearance": "A portrait in a quiet room. She wears a brass bracelet.",
        "garments": [{"wording": "a brass bracelet", "aside": aside}],
    })
    profile = _ready_profile(saved)
    garment_key = saved["outfit"]["garments"][0]["key"]
    profile["garment_coverage"][garment_key]["aside"] = _state_for_regions(
        saved, {},
    )
    sid, _, _, view = _profiled_progressive_session(
        client, seeded, saved=saved, profile=profile,
    )
    view["plan"]["takes"][1]["pose"] = aside
    response = client.post(
        f"/api/sessions/{sid}/plan",
        json={"expected_revision": view["plan_revision"], "plan": view["plan"]},
    )
    assert response.status_code == 200, response.text
    view = _wardrobe_view(client, sid)

    prepared = resource_preparation.finalize_take_preparation(
        sid, view["plan_revision"], "take-002",
    )
    coverage = prepared["effective_state"]["wardrobe_coverage"]
    assert prepared["status"] == "ready"
    assert aside in prepared["final_prompt"]
    assert prepared["final_prompt"].count(coverage) == 1


def test_ambiguous_room_mention_is_labeled_uncertain_if_it_blocks(client, seeded):
    sid, _, _, view = _profiled_progressive_session(
        client, seeded,
        scene="A quiet room has a brass bracelet resting on the table.",
    )
    preparation = _final_take_preparation(client, sid, view)
    markers = [
        item for item in resource_preparation.build_review_state(preparation)["conflicts"]
        if item["kind"] == resource_preparation.CONFLICT_KIND_PROFILED_GARMENT_REINTRODUCED
    ]

    assert not markers or all(
        "detector checks canonical wording only" in item.get("message", "").casefold()
        for item in markers
    ), "literal matching must disclose its limited evidence rather than imply semantic detection"


def test_manual_coverage_edit_keeps_materialized_stage_authoritative(client, seeded):
    saved = saved_looks.create({
        "name": "Invented duplicate-stage bracelet acceptance look",
        "appearance": "A portrait in a quiet room. She wears a brass bracelet.",
        "garments": [{"wording": "a brass bracelet", "aside": "a brass bracelet"}],
    })
    profile = _ready_profile(saved)
    garment_key = saved["outfit"]["garments"][0]["key"]
    profile["garment_coverage"][garment_key]["aside"] = _state_for_regions(
        saved, {},
    )
    sid, _, _, view = _profiled_progressive_session(
        client, seeded, saved=saved, profile=profile,
    )
    take = view["plan"]["takes"][1]
    take["wardrobe_coverage"] = "The user supplied this explicit coverage detail."
    response = client.post(
        f"/api/sessions/{sid}/plan",
        json={"expected_revision": view["plan_revision"], "plan": view["plan"]},
    )
    assert response.status_code == 200, response.text
    view = _wardrobe_view(client, sid)
    model_id = db.one("SELECT model_id FROM session WHERE id = ?", sid)["model_id"]
    db.run(
        "UPDATE model SET base_positive = ? WHERE id = ?",
        "She wears a brass bracelet.", model_id,
    )
    preparation = _final_take_preparation(client, sid, view)
    assert preparation["profile_coverage_evidence"]["derived_coverage"] is None
    assert view["plan"]["authoring"]["wardrobe_progression"]["stage_indices"] == [0, 1]

    before = _state(sid)
    try:
        result = resource_preparation.finalize_take_preparation(
            sid, view["plan_revision"], "take-002",
        )
    except resource_preparation.ConflictUnresolvedError:
        assert _state(sid) == before
        return

    ready = db.q(
        "SELECT id, status, final_prompt FROM prepared_take "
        "WHERE session_id = ? AND status = 'ready' ORDER BY id",
        sid,
    )
    assert False, (
        "authoritative materialized aside stage was ignored after manual coverage "
        f"edit; result={result!r}; ready_rows={ready!r}"
    )


def test_tampered_preparation_evidence_fails_without_further_writes(client, seeded):
    sid, _, _, view = _profiled_progressive_session(client, seeded)
    preparation = _final_take_preparation(client, sid, view)
    tampered_state = _state(sid)
    evidence = preparation["profile_coverage_evidence"]
    evidence["profile_digest"] = "0" * 64

    with pytest.raises((session_plan.PlanValidationError, saved_looks.SavedLookError)):
        resource_preparation.build_review_state(preparation)

    assert _state(sid) == tampered_state


def test_composer_does_not_trust_forged_reference_flag_over_t2i_plan(client, seeded):
    sid, _, _, view = _profiled_progressive_session(client, seeded)
    preparation = _final_take_preparation(client, sid, view)
    model_id = db.one("SELECT model_id FROM session WHERE id = ?", sid)["model_id"]
    db.run(
        "UPDATE model SET base_positive = ? WHERE id = ?",
        "Invented bound model base.", model_id,
    )
    preparation["reference"] = True

    try:
        prompt = resource_preparation.compose_final_prompt(sid, preparation)
    except resource_preparation.PreparationArgumentError:
        return

    assert "Invented bound model base." in prompt
    assert preparation["effective_state"]["look"] in prompt
    assert preparation["effective_state"]["wardrobe_coverage"] in prompt


def test_reference_guide_keeps_profile_context_and_coverage_once(client, seeded):
    sid, _, _, view = _profiled_progressive_session(client, seeded)
    workflow = client.post("/api/workflows", json={
        "name": "invented profile guide workflow",
        "graph": GRAPH,
        "kind": "guide",
    })
    assert workflow.status_code == 200, workflow.text
    db.run(
        "UPDATE session SET reference_workflow_id = ? WHERE id = ?",
        workflow.json()["id"], sid,
    )
    view["plan"]["takes"][1]["reference"] = True
    view["plan"]["takes"][1]["pose"] = "standing beside an open window"
    response = client.post(
        f"/api/sessions/{sid}/plan",
        json={"expected_revision": view["plan_revision"], "plan": view["plan"]},
    )
    assert response.status_code == 200, response.text
    view = _wardrobe_view(client, sid)
    preparation = _final_take_preparation(client, sid, view)
    prepared = resource_preparation.finalize_take_preparation(
        sid, view["plan_revision"], "take-002",
    )
    prompt = prepared["final_prompt"]
    coverage = preparation["effective_state"]["wardrobe_coverage"]

    assert prompt.count(coverage) == 1
    assert prompt.index("4da woman") < prompt.index("photo, 35mm")
    assert prompt.index("photo, 35mm") < prompt.index(preparation["effective_state"]["look"])
    assert prompt.index(preparation["effective_state"]["look"]) < prompt.index(coverage)
    assert prompt.index(coverage) < prompt.index("standing beside an open window")
