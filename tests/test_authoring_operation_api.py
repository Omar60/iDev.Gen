"""API coverage for authoring operation start and read-only status."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
import sqlite3
import threading
import time
import uuid

import db
import main
import resource_store
import pytest
from backend import authoring_operations


_REAL_SCHEDULE_SHARED_SUGGESTION_OPERATION = main._schedule_shared_suggestion_operation
_REAL_PERSIST_OPERATION_RESPONSE = authoring_operations.persist_operation_response


def _persist_response_for_state_test(claim, ticket, items, **kwargs):
    """Exercise operation state transitions with a real sealed take snapshot."""
    row = db.one("SELECT kind FROM authoring_operation WHERE operation_id = ?", claim.operation_id)
    if row is None or row["kind"] != "prepare_takes":
        return _REAL_PERSIST_OPERATION_RESPONSE(claim, ticket, items, **kwargs)
    if "finalize_take" in kwargs:
        return _REAL_PERSIST_OPERATION_RESPONSE(claim, ticket, items, **kwargs)

    converted = []
    requests = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("target"), str):
            converted.append(item)
            continue
        target = item["target"]
        try:
            prep = main.resource_preparation.prepare_take_inputs(
                claim.session_id, claim.plan_revision, target,
            )
            unlocked = main.resource_preparation.compute_unlocked_fields(prep)
            request = main.resource_preparation.assemble_writer_request(prep, unlocked)
            context, predecessor_projection = main.resource_preparation.build_automatic_writer_context(
                claim.session_id, claim.plan_revision, target, prep, request,
            )
        except Exception:
            converted.append(item)
            continue
        requests[target] = {"request": request, "assistant_request": {
            "messages": [
                {"role": "system", "content": "Invented test system."},
                {"role": "user", "content": main.resource_preparation.automatic_writer_instruction(context)},
            ],
            "model": "test-model",
            "parameters": {"response_format": {"type": "json_object"}},
        }}
        requests[target]["context"] = context
        requests[target]["predecessor_projection"] = predecessor_projection
        converted.append({"target": target, "result": {
            field: f"invented {field} for {target}" for field in unlocked
        }})

    def finalize(target, result):
        return main.resource_preparation.finalize_take_preparation(
            claim.session_id, claim.plan_revision, target,
            _operation_result=(
                ticket,
                {key: requests[target][key] for key in ("request", "assistant_request")},
                result,
                requests[target]["context"],
                requests[target]["predecessor_projection"],
            ),
        )

    return _REAL_PERSIST_OPERATION_RESPONSE(
        claim, ticket, converted, finalize_take=finalize, **kwargs,
    )


def _assert_snapshot_results(items, session_id, targets):
    assert [item["target"] for item in items] == targets
    for item in items:
        result = item["result"]
        assert result["take_id"] == item["target"]
        row = db.one("SELECT * FROM prepared_take WHERE id = ?", result["prepared_take_id"])
        assert row["session_id"] == session_id
        assert row["take_id"] == item["target"]
        assert row["status"] == "ready"
        evidence = json.loads(row["provenance"])["authoring_evidence"]["writer_synthesis"]
        assert result["assistant_output_digest"] == resource_store.canonical_digest(
            evidence["writer_output"] or {}
        )
        assert result["assistant_request_digest"] == resource_store.canonical_digest(
            (evidence["writer_input"] or {}).get("assistant_request", {})
        )


@pytest.fixture(autouse=True)
def _disable_background_suggestion_dispatch(monkeypatch):
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda view: None)


def _enable_background_suggestion_dispatch(monkeypatch):
    monkeypatch.setattr(
        main,
        "_schedule_shared_suggestion_operation",
        _REAL_SCHEDULE_SHARED_SUGGESTION_OPERATION,
    )


def _room_anchor() -> dict[str, str]:
    library_key = f"operation-test-{uuid.uuid4().hex}"
    source_id = "scene-001"
    label = "invented studio"
    description = "Soft light enters an empty studio from a high window."
    library_id = resource_store.ensure_library(library_key, kind="rooms")
    revision_id = resource_store.record_revision(
        library_id,
        source_id,
        {"id": source_id, "label": label, "scene_theme": description},
        translation={"label": label, "scene_theme": description},
    )
    revision = resource_store.get_revision(revision_id=revision_id)
    assert revision is not None
    return {
        "library_key": library_key,
        "source_id": source_id,
        "content_digest": revision["content_digest"],
    }


def _create_guided_session(
    client,
    seeded,
    *,
    mode="automatic",
    photo_count=4,
    look="",
    initial_wardrobe="",
    brief="",
):
    response = client.post(
        "/api/sessions/guided",
        json={
            "request_id": str(uuid.uuid4()),
            "character_id": seeded["model_id"],
            "scene_anchor": _room_anchor(),
            "photo_count": photo_count,
            "mode": mode,
            "brief": brief,
            "look": look,
            "initial_wardrobe": initial_wardrobe,
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    return result["session_id"], result["plan_revision"]


def _start_body(kind="prepare_takes", *, request_id=None, revision=1, take_ids=None):
    body = {
        "request_id": request_id or str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": kind,
    }
    if kind == "prepare_takes":
        body["take_ids"] = ["take-001"] if take_ids is None else take_ids
    return body


def _error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert isinstance(detail["message"], str) and detail["message"]
    return detail


def _operation_count(session_id: int) -> int:
    return int(db.one(
        "SELECT COUNT(*) AS n FROM authoring_operation WHERE session_id = ?",
        session_id,
    )["n"])


def _configure_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "http://assistant.local/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "test-model")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)


def _install_fake_structured_assistant(monkeypatch, outputs, *, entered=None, release=None):
    captured = []
    pending = list(outputs)
    json_module = json

    class FakeResponse:
        status_code = 200
        text = ""

        def __init__(self, output):
            self.output = output

        def json(self):
            return {
                "choices": [{
                    "message": {"content": json_module.dumps(self.output, ensure_ascii=False)},
                }],
            }

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, *, json, headers):
            captured.append({"url": url, "body": json, "headers": headers})
            if entered is not None:
                entered.set()
            if release is not None and not await asyncio.to_thread(release.wait, 5):
                raise TimeoutError("fake assistant was not released")
            if not pending:
                raise AssertionError("the fake assistant received an unexpected call")
            return FakeResponse(pending.pop(0))

    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", FakeAsyncClient)
    return captured


def _wait_for_operation_state(client, session_id, operation_id, expected_state, timeout=4):
    url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["state"] == expected_state:
            return latest
        time.sleep(0.01)
    assert latest is not None
    assert latest["state"] == expected_state, latest
    return latest


def _context_from_call(call):
    user_message = next(
        message["content"] for message in call["body"]["messages"]
        if message["role"] == "user"
    )
    prefix, separator, serialized = user_message.partition("Context: ")
    assert separator and prefix
    return json.loads(serialized)


def test_prepare_takes_commits_ready_snapshot_with_assistant_evidence(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    output = {
        "camera": "eye-level camera",
        "framing": "full body framing",
        "pose": "standing beside the window",
        "expression": "a calm expression",
    }
    calls = _install_fake_structured_assistant(monkeypatch, [output])
    operation_body = _start_body(revision=revision, request_id=str(uuid.uuid4()))
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=operation_body,
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    view = _wait_for_operation_state(client, session_id, operation_id, "succeeded")
    row = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, "take-001",
    )
    assert row["status"] == "ready"
    evidence = json.loads(row["provenance"])["authoring_evidence"]
    assert view["result"] == {"items": [{
        "target": "take-001",
        "result": {
            "prepared_take_id": row["id"], "take_id": "take-001",
            "assistant_output_digest": resource_store.canonical_digest(output),
            "assistant_request_digest": resource_store.canonical_digest(
                evidence["writer_synthesis"]["writer_input"]["assistant_request"]
            ),
        },
    }]}
    assert evidence["operation_id"] == operation_id
    assert evidence["source"] == "assistant"
    assert evidence["writer_synthesis"]["writer_output"] == output
    assistant_request = evidence["writer_synthesis"]["writer_input"]["assistant_request"]
    sent_body = calls[0]["body"]
    assert assistant_request == {
        "messages": sent_body["messages"],
        "model": sent_body["model"],
        "parameters": {
            key: sent_body[key]
            for key in ("temperature", "stream", "response_format", "reasoning_effort")
            if key in sent_body
        },
    }
    assert len(calls) == 1
    provenance_before_replay = row["provenance"]
    replayed = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=operation_body,
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == view
    assert len(calls) == 1
    assert db.one(
        "SELECT provenance FROM prepared_take WHERE id = ?", row["id"],
    )["provenance"] == provenance_before_replay

    main.resource_preparation.validate_authoring_prepared_evidence(
        session_id, revision, "take-001",
    )
    recovered = main.session_plan.recover_preparation(session_id)
    assert [item["take_id"] for item in recovered["completed"]] == ["take-001"]

    changed = json.loads(row["provenance"])
    changed["writer_synthesis"]["writer_input"]["assistant_request"]["model"] = "forged-model"
    changed["authoring_evidence"]["writer_synthesis"]["writer_input"]["assistant_request"]["model"] = "forged-model"
    db.run(
        "UPDATE prepared_take SET provenance = ? WHERE id = ?",
        json.dumps(changed), row["id"],
    )
    with pytest.raises(main.session_plan.AuthoringEvidenceInvalid):
        main.resource_preparation.validate_authoring_prepared_evidence(
            session_id, revision, "take-001",
        )
    recovered = main.session_plan.recover_preparation(session_id)
    assert {"take_id": "take-001", "status": "invalid_evidence", "diagnostic": "authoring_evidence_invalid"} in recovered["incomplete"]


def test_prepare_takes_persists_ordered_five_take_context_and_replays(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    brief = "Keep the studio quiet and the subject relaxed."
    look = "A consistent appearance with a soft natural finish."
    wardrobe = "a cream linen shirt and dark trousers"
    session_id, revision = _create_guided_session(
        client, seeded, photo_count=12, look=look,
        initial_wardrobe=wardrobe, brief=brief,
    )
    outputs = [
        {
            "camera": f"camera choice {index}",
            "framing": f"framing choice {index}",
            "pose": f"pose choice {index}",
            "expression": f"expression choice {index}",
        }
        for index in range(1, 13)
    ]
    calls = _install_fake_structured_assistant(monkeypatch, outputs)
    request_body = _start_body(
        revision=revision,
        take_ids=[f"take-{index:03d}" for index in range(1, 13)],
    )
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=request_body,
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    view = _wait_for_operation_state(client, session_id, operation_id, "succeeded")
    assert len(calls) == 12

    saved = client.get(f"/api/sessions/{session_id}/plan")
    assert saved.status_code == 200, saved.text
    plan = saved.json()["plan"]
    context = _context_from_call(calls[-1])
    assert context["brief"] == brief
    assert context["scene_anchor"] == plan["authoring"]["scene_anchor"]
    assert context["shared_values"] == {
        "look": look,
        "initial_wardrobe": wardrobe,
    }
    assert context["workflow_binding"] == plan["authoring"]["workflow_binding"]
    assert context["variation_policy"] == plan["authoring"]["variation_policy"]
    assert context["take_id"] == "take-012"
    assert context["ordinal"] == 12
    assert context["preparation"]["resource_descriptive_inputs"][0]["descriptive_inputs"]["scene_theme"] == (
        "Soft light enters an empty studio from a high window."
    )

    expected_summaries = [
        {"take_id": f"take-{index:03d}", **outputs[index - 1]}
        for index in range(7, 12)
    ]
    assert context["predecessors"] == expected_summaries
    assert all(
        set(summary) == {"take_id", "camera", "framing", "pose", "expression"}
        for summary in context["predecessors"]
    )
    context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
    assert "photo_count" not in context_json
    assert "total_count" not in context_json
    assert "prepared_take_id" not in context_json
    assert "final_prompt" not in context_json
    assert "data:image/" not in context_json
    assert "image_url" not in context_json
    for index in range(1, 12):
        previous = db.one(
            "SELECT final_prompt FROM prepared_take WHERE session_id = ? "
            "AND plan_revision = ? AND take_id = ?",
            session_id, revision, f"take-{index:03d}",
        )
        assert previous["final_prompt"] not in context_json

    row = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, "take-012",
    )
    evidence = json.loads(row["provenance"])["authoring_evidence"]
    assert evidence["schema_version"] == 2
    assert evidence["writer_context"] == context
    expected_refs = [
        {
            "take_id": f"take-{index:03d}",
            "prepared_take_id": db.one(
                "SELECT id FROM prepared_take WHERE session_id = ? "
                "AND plan_revision = ? AND take_id = ?",
                session_id, revision, f"take-{index:03d}",
            )["id"],
            "plan_revision": revision,
        }
        for index in range(7, 12)
    ]
    assert evidence["predecessor_projection"] == {
        "status": "recorded",
        "snapshots": expected_refs,
    }
    main.resource_preparation.validate_authoring_prepared_evidence(
        session_id, revision, "take-012",
    )

    provenance_before_replay = row["provenance"]
    replayed = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=request_body,
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == view
    assert len(calls) == 12
    assert db.one(
        "SELECT provenance FROM prepared_take WHERE id = ?", row["id"],
    )["provenance"] == provenance_before_replay

    for parent_path, field, value in (
        (("writer_context",), "brief", "forged brief"),
        (("predecessor_projection", "snapshots", 0), "prepared_take_id", -1),
    ):
        tampered = json.loads(provenance_before_replay)
        target = tampered["authoring_evidence"]
        for key in parent_path:
            target = target[key]
        target[field] = value
        db.run(
            "UPDATE prepared_take SET provenance = ? WHERE id = ?",
            json.dumps(tampered), row["id"],
        )
        with pytest.raises(main.session_plan.AuthoringEvidenceInvalid):
            main.resource_preparation.validate_authoring_prepared_evidence(
                session_id, revision, "take-012",
            )
        db.run(
            "UPDATE prepared_take SET provenance = ? WHERE id = ?",
            provenance_before_replay, row["id"],
        )


def test_fixed_automatic_take_persists_context_without_writer_call(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, photo_count=2)
    draft = client.get(f"/api/sessions/{session_id}/plan")
    assert draft.status_code == 200, draft.text
    plan = draft.json()["plan"]
    plan["takes"][0].update({
        "camera": "a fixed eye-level camera",
        "framing": "a fixed full-body frame",
        "pose": "a fixed standing pose",
        "expression": "a fixed calm expression",
    })
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"expected_revision": revision, "plan": plan},
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["plan_revision"]
    calls = _install_fake_structured_assistant(monkeypatch, [])
    request_body = _start_body(revision=revision, take_ids=["take-001"])
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=request_body,
    )
    assert started.status_code == 202, started.text
    view = _wait_for_operation_state(
        client, session_id, started.json()["operation_id"], "succeeded",
    )
    assert view["progress"]["completed"] == ["take-001"]
    assert calls == []

    row = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, "take-001",
    )
    evidence = json.loads(row["provenance"])["authoring_evidence"]
    assert evidence["source"] == "fixed"
    assert evidence["writer_synthesis"]["kind"] == "none"
    assert evidence["writer_synthesis"]["writer_input"] is None
    assert evidence["writer_context"]["take_id"] == "take-001"
    assert evidence["writer_context"]["ordinal"] == 1
    assert evidence["writer_context"]["preparation"]["requested_fields"] == []
    assert evidence["predecessor_projection"] == {
        "status": "recorded",
        "snapshots": [],
    }
    main.resource_preparation.validate_authoring_prepared_evidence(
        session_id, revision, "take-001",
    )


def test_invalid_predecessor_context_invalidates_descendant_recovery_and_approval(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded, photo_count=2)
    calls = _install_fake_structured_assistant(monkeypatch, [
        {"camera": f"camera {index}", "framing": f"framing {index}",
         "pose": f"pose {index}", "expression": f"expression {index}"}
        for index in (1, 2)
    ])
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision, take_ids=["take-001", "take-002"]),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    _wait_for_operation_state(client, session_id, operation_id, "succeeded")
    assert len(calls) == 2

    first = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, "take-001",
    )
    tampered = json.loads(first["provenance"])
    tampered["authoring_evidence"]["writer_context"]["brief"] = "altered predecessor brief"
    db.run(
        "UPDATE prepared_take SET provenance = ? WHERE id = ?",
        json.dumps(tampered), first["id"],
    )
    rows_before_checks = db.q(
        "SELECT id, take_id, status, linked_shot_id, updated_at, provenance "
        "FROM prepared_take WHERE session_id = ? AND plan_revision = ? ORDER BY id",
        session_id, revision,
    )

    recovered_response = client.get(f"/api/sessions/{session_id}/plan")
    assert recovered_response.status_code == 200, recovered_response.text
    recovery = recovered_response.json()["preparation"]
    assert recovery["completed"] == []
    assert [item["take_id"] for item in recovery["incomplete"]] == [
        "take-001", "take-002",
    ]
    assert all(item["status"] == "invalid_evidence" for item in recovery["incomplete"])

    approval = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approval.status_code == 409, approval.text
    assert "Prepared authoring evidence is invalid" in approval.text
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?",
        session_id,
    ) is None
    assert db.q(
        "SELECT id, take_id, status, linked_shot_id, updated_at, provenance "
        "FROM prepared_take WHERE session_id = ? AND plan_revision = ? ORDER BY id",
        session_id, revision,
    ) == rows_before_checks


def test_automatic_writer_context_accepts_maximum_plan_ordinal(client, seeded):
    session_id, revision = _create_guided_session(client, seeded, photo_count=500)
    preparation = main.resource_preparation.prepare_take_inputs(
        session_id, revision, "take-500",
    )
    unlocked = main.resource_preparation.compute_unlocked_fields(preparation)
    context, projection = main.resource_preparation.build_automatic_writer_context(
        session_id,
        revision,
        "take-500",
        preparation,
        main.resource_preparation.assemble_writer_request(preparation, unlocked),
    )

    assert context["ordinal"] == 500
    assert context["predecessors"] == []
    assert projection == {"status": "recorded", "snapshots": []}


def test_automatic_evidence_validation_is_json_type_sensitive(client, seeded, monkeypatch):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001", "take-002"],
    )
    for index, take_id in enumerate(("take-001", "take-002"), 1):
        ticket = authoring_operations.renew_operation_lease(claim)
        view = _persist_response_for_state_test(
            claim,
            ticket,
            [{"target": take_id, "result": {
                "camera": f"camera {index}",
                "framing": f"framing {index}",
                "pose": f"pose {index}",
                "expression": f"expression {index}",
            }}],
        )
    assert view["state"] == "succeeded"

    first = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, "take-001",
    )
    second = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, "take-002",
    )
    assert first["id"] == 1
    assert first["plan_revision"] == 1
    tamper_cases = []
    first_provenance = json.loads(first["provenance"])
    first_provenance["authoring_evidence"]["writer_context"]["ordinal"] = True
    tamper_cases.append((first, first_provenance))
    assert json.loads(second["provenance"])["authoring_evidence"]["predecessor_projection"]["snapshots"][0]["prepared_take_id"] == 1
    for field in ("prepared_take_id", "plan_revision"):
        second_provenance = json.loads(second["provenance"])
        second_provenance["authoring_evidence"]["predecessor_projection"]["snapshots"][0][field] = True
        tamper_cases.append((second, second_provenance))

    for row, tampered in tamper_cases:
        tampered_json = json.dumps(tampered)
        db.run(
            "UPDATE prepared_take SET provenance = ? WHERE id = ?",
            tampered_json, row["id"],
        )
        with pytest.raises(main.session_plan.AuthoringEvidenceInvalid):
            main.resource_preparation.validate_authoring_prepared_evidence(
                session_id, row["plan_revision"], row["take_id"],
            )
        after = db.one(
            "SELECT status, updated_at, provenance FROM prepared_take WHERE id = ?",
            row["id"],
        )
        assert after == {
            "status": row["status"],
            "updated_at": row["updated_at"],
            "provenance": tampered_json,
        }
        db.run(
            "UPDATE prepared_take SET provenance = ? WHERE id = ?",
            row["provenance"], row["id"],
        )


def test_finalization_rejects_boolean_context_ordinal_without_writes(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    preparation = main.resource_preparation.prepare_take_inputs(
        session_id, claim.plan_revision, "take-001",
    )
    unlocked = main.resource_preparation.compute_unlocked_fields(preparation)
    request = main.resource_preparation.assemble_writer_request(preparation, unlocked)
    context, predecessor_projection = main.resource_preparation.build_automatic_writer_context(
        session_id, claim.plan_revision, "take-001", preparation, request,
    )
    tampered_context = dict(context, ordinal=True)
    writer_input = {
        "request": request,
        "assistant_request": {
            "messages": [
                {"role": "system", "content": "Invented test system."},
                {
                    "role": "user",
                    "content": main.resource_preparation.automatic_writer_instruction(
                        tampered_context,
                    ),
                },
            ],
            "model": "test-model",
            "parameters": {"response_format": {"type": "json_object"}},
        },
    }
    output = {
        "camera": "camera one",
        "framing": "full-body framing",
        "pose": "standing pose",
        "expression": "calm expression",
    }
    operation_before = db.one(
        "SELECT state, completed_json, remaining_json, result_json "
        "FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )

    with pytest.raises(main.resource_preparation.PreparationArgumentError):
        main.resource_preparation.finalize_take_preparation(
            session_id,
            claim.plan_revision,
            "take-001",
            _operation_result=(
                ticket, writer_input, output, tampered_context, predecessor_projection,
            ),
        )

    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, "take-001",
    ) is None
    assert db.one(
        "SELECT state, completed_json, remaining_json, result_json "
        "FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    ) == operation_before


@pytest.mark.parametrize("field", ["prepared_take_id", "plan_revision"])
def test_finalization_rejects_boolean_predecessor_reference_without_writes(
    client, seeded, monkeypatch, field,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001", "take-002"],
    )
    first_ticket = authoring_operations.renew_operation_lease(claim)
    _persist_response_for_state_test(
        claim,
        first_ticket,
        [{"target": "take-001", "result": {
            "camera": "camera one",
            "framing": "full-body framing",
            "pose": "standing pose",
            "expression": "calm expression",
        }}],
    )

    ticket = authoring_operations.renew_operation_lease(claim)
    preparation = main.resource_preparation.prepare_take_inputs(
        session_id, claim.plan_revision, "take-002",
    )
    unlocked = main.resource_preparation.compute_unlocked_fields(preparation)
    request = main.resource_preparation.assemble_writer_request(preparation, unlocked)
    context, predecessor_projection = main.resource_preparation.build_automatic_writer_context(
        session_id, claim.plan_revision, "take-002", preparation, request,
    )
    assert predecessor_projection["snapshots"][0][field] == 1
    tampered_projection = json.loads(json.dumps(predecessor_projection))
    tampered_projection["snapshots"][0][field] = True
    writer_input = {
        "request": request,
        "assistant_request": {
            "messages": [
                {"role": "system", "content": "Invented test system."},
                {
                    "role": "user",
                    "content": main.resource_preparation.automatic_writer_instruction(context),
                },
            ],
            "model": "test-model",
            "parameters": {"response_format": {"type": "json_object"}},
        },
    }
    output = {
        "camera": "camera two",
        "framing": "full-body framing",
        "pose": "standing pose",
        "expression": "calm expression",
    }
    operation_before = db.one(
        "SELECT state, completed_json, remaining_json, result_json "
        "FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )
    rows_before = db.q(
        "SELECT id, take_id, status, updated_at, provenance FROM prepared_take "
        "WHERE session_id = ? AND plan_revision = ? ORDER BY id",
        session_id, claim.plan_revision,
    )

    with pytest.raises(main.resource_preparation.PreparationArgumentError):
        main.resource_preparation.finalize_take_preparation(
            session_id,
            claim.plan_revision,
            "take-002",
            _operation_result=(
                ticket, writer_input, output, context, tampered_projection,
            ),
        )

    assert db.q(
        "SELECT id, take_id, status, updated_at, provenance FROM prepared_take "
        "WHERE session_id = ? AND plan_revision = ? ORDER BY id",
        session_id, claim.plan_revision,
    ) == rows_before
    assert db.one(
        "SELECT state, completed_json, remaining_json, result_json "
        "FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    ) == operation_before


@pytest.mark.parametrize("output", [
    {"camera": "eye-level", "framing": "full body", "pose": "standing", "expression": "calm", "look": "changed"},
    {"camera": "eye-level", "framing": "full body", "pose": "standing", "expression": 7},
    {"camera": "eye-level", "framing": "full body", "pose": "standing"},
    ["not a JSON object"],
])
def test_prepare_takes_rejects_malformed_or_partial_output_without_snapshot(
    client, seeded, monkeypatch, output,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    _install_fake_structured_assistant(monkeypatch, [output])
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision),
    )
    operation_id = started.json()["operation_id"]
    failed = _wait_for_operation_state(client, session_id, operation_id, "failed")
    assert failed["progress"]["completed"] == []
    assert failed["result"] is None
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, "take-001",
    ) is None


@pytest.mark.parametrize("locked_field", ["camera", "look", "initial_wardrobe"])
def test_prepare_takes_refuses_locked_fields_without_mutating_plan_or_ready_state(
    client, seeded, monkeypatch, locked_field,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    fixed_look = "A consistent studio appearance."
    fixed_wardrobe = "a navy cotton shirt"
    session_id, revision = _create_guided_session(
        client, seeded, look=fixed_look, initial_wardrobe=fixed_wardrobe,
    )
    draft_response = client.get(f"/api/sessions/{session_id}/plan")
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()
    plan = draft["plan"]
    fixed_camera = "a fixed 50mm camera"
    if locked_field == "camera":
        plan["takes"][0]["camera"] = fixed_camera
        saved = client.post(
            f"/api/sessions/{session_id}/plan",
            json={"expected_revision": draft["plan_revision"], "plan": plan},
        )
        assert saved.status_code == 200, saved.text
        revision = saved.json()["plan_revision"]

    output = {
        "camera": "an unapproved 24mm camera",
        "framing": "full body",
        "pose": "standing beside a window",
        "expression": "calm",
        locked_field: "an assistant override",
    }
    calls = _install_fake_structured_assistant(monkeypatch, [output])
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision),
    )
    assert started.status_code == 202, started.text
    failed = _wait_for_operation_state(
        client, session_id, started.json()["operation_id"], "failed",
    )
    assert failed["progress"]["completed"] == []
    assert failed["result"] is None
    assert len(calls) == 1
    user_message = next(
        item["content"] for item in calls[0]["body"]["messages"]
        if item["role"] == "user"
    )
    context = json.loads(user_message.partition("Context: ")[2])
    requested_fields = context["preparation"]["requested_fields"]
    assert locked_field not in requested_fields
    assert "look" not in requested_fields
    assert "initial_wardrobe" not in requested_fields

    current = client.get(f"/api/sessions/{session_id}/plan")
    assert current.status_code == 200, current.text
    current_plan = current.json()["plan"]
    assert current_plan["look"] == fixed_look
    assert current_plan["initial_wardrobe"] == fixed_wardrobe
    assert current_plan["takes"][0].get("camera") == (
        fixed_camera if locked_field == "camera" else plan["takes"][0].get("camera")
    )
    assert current.json()["preparation"]["completed"] == []
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, "take-001",
    ) is None


def test_prepare_takes_rejects_unresolved_writer_placeholder_without_snapshot(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    calls = _install_fake_structured_assistant(monkeypatch, [{
        "camera": "eye-level camera",
        "framing": "full body",
        "pose": "standing at {another_location}",
        "expression": "calm",
    }])
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision),
    )
    assert started.status_code == 202, started.text
    failed = _wait_for_operation_state(
        client, session_id, started.json()["operation_id"], "failed",
    )
    assert failed["progress"]["completed"] == []
    assert failed["result"] is None
    assert len(calls) == 1
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, "take-001",
    ) is None


def test_prepare_takes_preserves_fused_camera_pose_and_location_context_for_review(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, _ = _create_guided_session(client, seeded)
    authorized_description = (
        "A fixed overhead camera looks into an invented greenhouse. "
        "She kneels beside its north wall, facing the lens."
    )
    library_key = f"operation-test-{uuid.uuid4().hex}"
    source_id = "scene-001"
    library_id = resource_store.ensure_library(library_key, kind="fused_scenes")
    revision_id = resource_store.record_revision(
        library_id,
        source_id,
        {"id": source_id, "prompt": authorized_description},
        translation={"prompt": authorized_description},
    )
    fused_revision = resource_store.get_revision(revision_id=revision_id)
    assert fused_revision is not None
    draft_response = client.get(f"/api/sessions/{session_id}/plan")
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()
    plan = draft["plan"]
    plan["selected_resources"].append({
        "library_key": library_key,
        "source_id": source_id,
        "content_digest": fused_revision["content_digest"],
    })
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"expected_revision": draft["plan_revision"], "plan": plan},
    )
    assert saved.status_code == 200, saved.text
    revision = saved.json()["plan_revision"]
    output = {
        "camera": "A low-angle camera looks up from floor level.",
        "framing": "a tight portrait",
        "pose": "standing in a quiet city cafe across town",
        "expression": "a calm expression",
    }
    calls = _install_fake_structured_assistant(monkeypatch, [output])
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision),
    )
    assert started.status_code == 202, started.text
    terminal = _wait_for_operation_state(
        client, session_id, started.json()["operation_id"], "succeeded",
    )
    assert terminal["progress"]["completed"] == ["take-001"]
    assert len(calls) == 1

    reviewed = client.get(
        f"/api/sessions/{session_id}/plan/takes/take-001/review?plan_revision={revision}",
    )
    assert reviewed.status_code == 200, reviewed.text
    review = reviewed.json()
    fused = next(
        item for item in review["fused_descriptions"]
        if item["library_key"] == library_key
    )
    assert fused["descriptive_inputs"]["prompt"] == authorized_description
    assert review["snapshot"]["final_prompt"] == review["final_prompt"]
    for exact_text in (authorized_description, *output.values()):
        assert exact_text in review["final_prompt"]


def test_prepare_takes_discards_cancelled_inflight_response(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    entered = threading.Event()
    release = threading.Event()
    _install_fake_structured_assistant(monkeypatch, [{
        "camera": "eye-level", "framing": "full body",
        "pose": "standing", "expression": "calm",
    }], entered=entered, release=release)
    url = f"/api/sessions/{session_id}/plan/authoring/operations"
    started = client.post(url, json=_start_body(revision=revision))
    operation_id = started.json()["operation_id"]
    assert entered.wait(timeout=2)
    cancelled = client.post(
        f"{url}/{operation_id}/cancel", json={"expected_revision": revision},
    )
    assert cancelled.status_code == 202
    release.set()
    terminal = _wait_for_operation_state(client, session_id, operation_id, "cancelled")
    assert terminal["progress"]["completed"] == []
    assert terminal["result"] is None
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, "take-001",
    ) is None


def test_prepare_takes_snapshot_rolls_back_when_progress_write_fails(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    entered = threading.Event()
    release = threading.Event()
    _install_fake_structured_assistant(monkeypatch, [{
        "camera": "eye-level", "framing": "full body",
        "pose": "standing", "expression": "calm",
    }], entered=entered, release=release)
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision),
    )
    operation_id = started.json()["operation_id"]
    assert entered.wait(timeout=2)
    db.conn().execute("""
        CREATE TRIGGER reject_completed_progress
        BEFORE UPDATE OF completed_json ON authoring_operation
        WHEN NEW.completed_json != OLD.completed_json
        BEGIN SELECT RAISE(ABORT, 'progress write refused'); END
    """)
    release.set()
    failed = _wait_for_operation_state(client, session_id, operation_id, "failed")
    assert failed["progress"]["completed"] == []
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, "take-001",
    ) is None
    db.conn().execute("DROP TRIGGER reject_completed_progress")


def test_start_returns_closed_view_replays_and_reads_without_assistant_call(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    calls = []

    async def unexpected_assistant_call(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("operation start and status must not call the assistant")

    monkeypatch.setattr(main.enhance, "_request_completion", unexpected_assistant_call)
    request_id = str(uuid.uuid4())
    body = _start_body(
        revision=revision,
        request_id=request_id,
        take_ids=["take-002", "take-004"],
    )
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations", json=body,
    )

    assert started.status_code == 202, started.text
    view = started.json()
    assert set(view) == {
        "operation_id", "session_id", "plan_revision", "kind", "state",
        "created_at", "updated_at", "lease_expires_at", "progress", "result",
        "error", "can_cancel", "can_resume",
    }
    assert view["session_id"] == session_id
    assert view["plan_revision"] == revision
    assert view["kind"] == "prepare_takes"
    assert view["state"] == "active"
    assert view["lease_expires_at"] is not None
    assert view["progress"] == {
        "requested": ["take-002", "take-004"],
        "completed": [],
        "failed": None,
        "remaining": ["take-002", "take-004"],
    }
    assert view["result"] is None and view["error"] is None
    assert view["can_cancel"] is True and view["can_resume"] is False
    assert not {"request_id", "request_digest", "fencing_token", "owner"}.intersection(view)

    replay_body = {**body, "request_id": request_id.upper()}
    replay = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations", json=replay_body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == view

    status = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{view['operation_id']}"
    )
    assert status.status_code == 200, status.text
    assert status.json() == view
    assert calls == []
    assert _operation_count(session_id) == 1


def test_shared_suggestions_have_no_take_targets_and_only_request_missing_fields(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body("shared_suggestions", revision=revision),
    )
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "shared_suggestions"
    assert response.json()["progress"] == {
        "requested": ["look", "initial_wardrobe"],
        "completed": [],
        "failed": None,
        "remaining": ["look", "initial_wardrobe"],
    }


def test_request_id_replay_and_changed_body_conflict(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    request_id = str(uuid.uuid4())
    url = f"/api/sessions/{session_id}/plan/authoring/operations"
    first = client.post(url, json=_start_body(request_id=request_id, revision=revision))
    assert first.status_code == 202, first.text

    changed = client.post(
        url,
        json=_start_body(
            request_id=request_id,
            revision=revision,
            take_ids=["take-002"],
        ),
    )
    _error(changed, 409, "idempotency_conflict")
    assert _operation_count(session_id) == 1

    operation_id = first.json()["operation_id"]
    db.run(
        "UPDATE authoring_operation SET state='succeeded', lease_expires_at=NULL, "
        "result_json=?, updated_at=? WHERE operation_id=?",
        '{"prepared":true}',
        db.now(),
        operation_id,
    )
    terminal = client.post(url, json=_start_body(request_id=request_id, revision=revision))
    assert terminal.status_code == 200, terminal.text
    assert terminal.json()["operation_id"] == operation_id
    assert terminal.json()["state"] == "succeeded"
    assert terminal.json()["result"] == {"prepared": True}
    status = client.get(f"{url}/{operation_id}")
    assert status.status_code == 200, status.text
    assert status.json() == terminal.json()


def test_request_id_is_scoped_to_session_but_immutable_within_a_session(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    first_session, first_revision = _create_guided_session(client, seeded)
    second_session, second_revision = _create_guided_session(client, seeded)
    request_id = str(uuid.uuid4())

    first_url = f"/api/sessions/{first_session}/plan/authoring/operations"
    first_body = _start_body(
        "shared_suggestions", request_id=request_id, revision=first_revision,
    )
    first = client.post(first_url, json=first_body)
    assert first.status_code == 202, first.text

    changed_body = _start_body(
        request_id=request_id, revision=first_revision, take_ids=["take-002"],
    )
    _error(client.post(first_url, json=changed_body), 409, "idempotency_conflict")

    second_url = f"/api/sessions/{second_session}/plan/authoring/operations"
    second = client.post(
        second_url,
        json=_start_body(
            "shared_suggestions", request_id=request_id, revision=second_revision,
        ),
    )
    assert second.status_code == 202, second.text
    assert second.json()["operation_id"] != first.json()["operation_id"]
    assert _operation_count(first_session) == 1
    assert _operation_count(second_session) == 1


def test_active_conflict_is_shared_across_operation_kinds(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    url = f"/api/sessions/{session_id}/plan/authoring/operations"
    first = client.post(
        url,
        json=_start_body("shared_suggestions", revision=revision),
    )
    assert first.status_code == 202, first.text
    conflict = client.post(url, json=_start_body(revision=revision))

    detail = _error(conflict, 409, "authoring_active")
    assert detail["operation"] == first.json()
    assert _operation_count(session_id) == 1


def test_start_validates_closed_body_targets_order_and_missing_records(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    url = f"/api/sessions/{session_id}/plan/authoring/operations"

    missing_targets = _start_body(revision=revision)
    missing_targets.pop("take_ids")
    _error(client.post(url, json=missing_targets), 422, "missing_field")

    suggestion_targets = _start_body("shared_suggestions", revision=revision)
    suggestion_targets["take_ids"] = []
    _error(client.post(url, json=suggestion_targets), 422, "invalid_request")

    extra = _start_body(revision=revision)
    extra["owner"] = "client"
    _error(client.post(url, json=extra), 422, "extra_field_forbidden")

    forged_ticket = _start_body(revision=revision)
    forged_ticket["lease_ticket"] = {"input_fingerprint": "0" * 64}
    _error(client.post(url, json=forged_ticket), 422, "extra_field_forbidden")

    duplicate = _start_body(revision=revision, take_ids=["take-001", "take-001"])
    _error(client.post(url, json=duplicate), 422, "invalid_request")

    unordered = _start_body(revision=revision, take_ids=["take-003", "take-001"])
    _error(client.post(url, json=unordered), 422, "invalid_request")

    unknown_take = _start_body(revision=revision, take_ids=["take-missing"])
    _error(client.post(url, json=unknown_take), 404, "take_not_found")

    invalid_revision = _start_body(revision=True)
    _error(client.post(url, json=invalid_revision), 422, "invalid_request")

    bad_kind = _start_body(revision=revision)
    bad_kind["kind"] = "prepare"
    _error(client.post(url, json=bad_kind), 422, "invalid_request")
    assert _operation_count(session_id) == 0


def test_stale_plan_missing_session_and_missing_operation_use_stable_errors(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, _ = _create_guided_session(client, seeded)
    url = f"/api/sessions/{session_id}/plan/authoring/operations"
    _error(client.post(url, json=_start_body(revision=2)), 409, "plan_revision_stale")
    _error(
        client.post(
            f"/api/sessions/99999999/plan/authoring/operations",
            json=_start_body(),
        ),
        404,
        "session_not_found",
    )
    _error(
        client.get(f"{url}/{uuid.uuid4()}"),
        404,
        "operation_not_found",
    )
    assert _operation_count(session_id) == 0


def test_missing_assistant_and_manual_plan_are_refused_without_claims(
    client, seeded, monkeypatch,
):
    monkeypatch.setitem(main.CONFIG, "llm_url", "")
    monkeypatch.setitem(main.CONFIG, "llm_model", "")
    session_id, revision = _create_guided_session(client, seeded)
    url = f"/api/sessions/{session_id}/plan/authoring/operations"
    _error(
        client.post(url, json=_start_body(revision=revision)),
        409,
        "assistant_unavailable",
    )
    assert _operation_count(session_id) == 0

    manual_id, manual_revision = _create_guided_session(client, seeded, mode="manual")
    _error(
        client.post(
            f"/api/sessions/{manual_id}/plan/authoring/operations",
            json=_start_body(revision=manual_revision),
        ),
        422,
        "invalid_request",
    )
    assert _operation_count(manual_id) == 0


def test_disabled_start_does_not_write_and_existing_status_stays_available(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    url = f"/api/sessions/{session_id}/plan/authoring/operations"
    body = _start_body(revision=revision)
    started = client.post(url, json=body)
    assert started.status_code == 202, started.text

    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setitem(main.CONFIG, "llm_url", "")
    status_url = f"{url}/{started.json()['operation_id']}"
    status = client.get(status_url)
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "cancelled"
    assert status.json()["lease_expires_at"] is None
    assert "feature_disabled" in status.json()["error"]
    assert status.json()["can_cancel"] is False
    assert status.json()["can_resume"] is False

    replay = client.post(url, json=body)
    _error(replay, 503, "resource_planning_disabled")

    malformed_replay = client.post(url, content="{", headers={"content-type": "application/json"})
    _error(malformed_replay, 503, "resource_planning_disabled")

    disabled = client.post(url, json=_start_body(revision=revision))
    _error(disabled, 503, "resource_planning_disabled")
    assert _operation_count(session_id) == 1


def test_shared_suggestions_respect_explicit_empty_user_origins(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)

    explicit_id, _ = _create_guided_session(
        client,
        seeded,
        look="temporary appearance choice",
        initial_wardrobe="temporary clothing choice",
    )
    explicit = client.get(f"/api/sessions/{explicit_id}/plan").json()
    explicit_plan = explicit["plan"]
    explicit_plan["look"] = ""
    explicit_plan["initial_wardrobe"] = ""
    saved = client.post(
        f"/api/sessions/{explicit_id}/plan",
        json={"plan": explicit_plan, "expected_revision": explicit["plan_revision"]},
    )
    assert saved.status_code == 200, saved.text
    explicit_saved = client.get(f"/api/sessions/{explicit_id}/plan").json()
    assert explicit_saved["plan"]["look"] == ""
    assert explicit_saved["plan"]["initial_wardrobe"] == ""
    assert explicit_saved["plan"]["authoring"]["shared_state"] == {
        "look": {"origin": "user", "evidence_id": None},
        "initial_wardrobe": {"origin": "user", "evidence_id": None},
    }

    explicit_suggestion = client.post(
        f"/api/sessions/{explicit_id}/plan/authoring/operations",
        json=_start_body("shared_suggestions", revision=saved.json()["plan_revision"]),
    )
    _error(explicit_suggestion, 422, "invalid_request")
    assert _operation_count(explicit_id) == 0

    mixed_id, _ = _create_guided_session(
        client,
        seeded,
        look="temporary appearance choice",
        initial_wardrobe="",
    )
    mixed = client.get(f"/api/sessions/{mixed_id}/plan").json()
    mixed_plan = mixed["plan"]
    mixed_plan["look"] = ""
    mixed_saved = client.post(
        f"/api/sessions/{mixed_id}/plan",
        json={"plan": mixed_plan, "expected_revision": mixed["plan_revision"]},
    )
    assert mixed_saved.status_code == 200, mixed_saved.text
    mixed_current = client.get(f"/api/sessions/{mixed_id}/plan").json()
    assert mixed_current["plan"]["authoring"]["shared_state"] == {
        "look": {"origin": "user", "evidence_id": None},
        "initial_wardrobe": {"origin": "none", "evidence_id": None},
    }

    suggestion = client.post(
        f"/api/sessions/{mixed_id}/plan/authoring/operations",
        json=_start_body("shared_suggestions", revision=mixed_saved.json()["plan_revision"]),
    )
    assert suggestion.status_code == 202, suggestion.text
    assert suggestion.json()["progress"]["requested"] == ["initial_wardrobe"]
    assert suggestion.json()["progress"]["remaining"] == ["initial_wardrobe"]
    assert _operation_count(mixed_id) == 1


def test_concurrent_distinct_starts_create_one_claim(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    url = f"/api/sessions/{session_id}/plan/authoring/operations"
    barrier = threading.Barrier(2)

    def post(request_id: str):
        barrier.wait(timeout=5)
        return client.post(
            url,
            json=_start_body(request_id=request_id, revision=revision),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(post, (str(uuid.uuid4()), str(uuid.uuid4()))))

    assert sorted(response.status_code for response in responses) == [202, 409]
    accepted = next(response for response in responses if response.status_code == 202)
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["detail"]["code"] == "authoring_active"
    assert rejected.json()["detail"]["operation"] == accepted.json()
    assert _operation_count(session_id) == 1


def test_concurrent_mixed_kind_starts_create_one_session_claim(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    url = f"/api/sessions/{session_id}/plan/authoring/operations"
    requests = (
        _start_body("shared_suggestions", revision=revision),
        _start_body("prepare_takes", revision=revision, take_ids=["take-001"]),
    )
    barrier = threading.Barrier(2)

    def post(body):
        barrier.wait(timeout=5)
        return client.post(url, json=body)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(post, requests))

    assert sorted(response.status_code for response in responses) == [202, 409]
    accepted = next(response for response in responses if response.status_code == 202)
    rejected = next(response for response in responses if response.status_code == 409)
    assert accepted.json()["kind"] in {"shared_suggestions", "prepare_takes"}
    assert rejected.json()["detail"]["code"] == "authoring_active"
    assert rejected.json()["detail"]["operation"] == accepted.json()
    assert _operation_count(session_id) == 1
    assert db.one(
        "SELECT COUNT(*) AS n FROM authoring_operation WHERE session_id = ? "
        "AND state IN ('active', 'cancel_requested')",
        session_id,
    )["n"] == 1


def test_suggestion_operation_requires_a_missing_shared_choice(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(
        client,
        seeded,
        look="short dark hair",
        initial_wardrobe="a blue jacket",
    )
    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body("shared_suggestions", revision=revision),
    )
    _error(response, 422, "invalid_request")
    assert _operation_count(session_id) == 0


def _start_worker(client, seeded, monkeypatch, *, take_ids):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=revision, take_ids=take_ids),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    claim = authoring_operations.load_worker_claim(session_id, operation_id)
    return session_id, operation_id, claim


def _start_shared_worker(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body("shared_suggestions", revision=revision),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    claim = authoring_operations.load_worker_claim(session_id, operation_id)
    return session_id, operation_id, claim


def _complete_shared_suggestions(client, seeded, monkeypatch, *, output=None):
    session_id, operation_id, claim = _start_shared_worker(client, seeded, monkeypatch)
    revision = claim.plan_revision
    suggestions = output or {
        "look": "A soft natural makeup look.",
        "initial_wardrobe": "A navy blouse.",
    }
    ticket = authoring_operations.renew_operation_lease(claim)
    view = _persist_response_for_state_test(
        claim,
        ticket,
        [
            {"target": field, "result": suggestions[field]}
            for field in ("look", "initial_wardrobe")
        ],
        suggestion_input={
            "messages": [
                {"role": "system", "content": "Suggest missing shared choices."},
                {"role": "user", "content": "Use the authorized scene summary."},
            ],
            "model": "invented-assistant-model",
            "parameters": {
                "temperature": 0.2,
                "stream": False,
                "response_format": {"type": "json_object"},
                "reasoning_effort": "none",
            },
            "plan_revision": revision,
        },
    )
    assert view["state"] == "succeeded"
    assert "input" not in view["result"]
    return session_id, operation_id, revision, suggestions


def test_shared_suggestions_run_once_in_background_with_authorized_context_and_exact_evidence(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    secret = "synthetic-assistant-secret"
    monkeypatch.setitem(main.CONFIG, "llm_key", secret)
    session_id, revision = _create_guided_session(
        client,
        seeded,
        brief="A quiet editorial portrait.",
    )
    entered = threading.Event()
    release = threading.Event()
    output = {
        "look": "Soft natural makeup and loose dark hair.",
        "initial_wardrobe": "A cream linen blouse with dark trousers.",
    }
    calls = _install_fake_structured_assistant(
        monkeypatch,
        [output],
        entered=entered,
        release=release,
    )
    url = f"/api/sessions/{session_id}/plan/authoring/operations"
    body = _start_body("shared_suggestions", revision=revision)

    started = client.post(url, json=body)

    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    assert entered.wait(timeout=2), "the suggestion worker did not reach the fake assistant"
    active = client.get(f"{url}/{operation_id}")
    assert active.status_code == 200, active.text
    assert active.json()["state"] == "active"

    replay = client.post(url, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["operation_id"] == operation_id
    assert len(calls) == 1

    request = calls[0]
    user_message = request["body"]["messages"][1]["content"]
    assert "A quiet editorial portrait." in user_message
    assert "4da woman" in user_message
    assert "photo, 35mm" in user_message
    assert "Soft light enters an empty studio from a high window." in user_message
    assert json.dumps(["look", "initial_wardrobe"]) in user_message
    assert request["body"]["model"] == "test-model"
    assert request["headers"]["Authorization"] == f"Bearer {secret}"

    release.set()
    succeeded = _wait_for_operation_state(client, session_id, operation_id, "succeeded")
    assert succeeded["result"] == {
        "items": [
            {"target": "look", "result": output["look"]},
            {"target": "initial_wardrobe", "result": output["initial_wardrobe"]},
        ],
    }
    current = client.get(f"/api/sessions/{session_id}/plan").json()
    assert current["plan_revision"] == revision
    assert current["plan"]["look"] == ""
    assert current["plan"]["initial_wardrobe"] == ""
    assert current["plan"]["authoring"]["evidence"] == []
    assert current["plan"]["authoring"]["shared_state"] == {
        "look": {"origin": "none", "evidence_id": None},
        "initial_wardrobe": {"origin": "none", "evidence_id": None},
    }
    assert db.one(
        "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?",
        session_id,
    )["n"] == 0

    stored = json.loads(db.one(
        "SELECT result_json FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )["result_json"])
    assert stored["input"] == {
        "messages": request["body"]["messages"],
        "model": request["body"]["model"],
        "parameters": {
            "temperature": request["body"]["temperature"],
            "stream": request["body"]["stream"],
            "response_format": request["body"]["response_format"],
            "reasoning_effort": request["body"]["reasoning_effort"],
        },
        "plan_revision": revision,
    }
    saved_text = json.dumps(stored, ensure_ascii=False)
    assert secret not in saved_text
    assert "assistant.local" not in saved_text


def test_shared_suggestion_discards_response_after_model_context_changes(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    entered = threading.Event()
    release = threading.Event()
    response_checked = threading.Event()
    output = {
        "look": "A stale suggested look.",
        "initial_wardrobe": "A stale suggested outfit.",
    }
    calls = _install_fake_structured_assistant(
        monkeypatch,
        [output],
        entered=entered,
        release=release,
    )

    lease_renewed = []
    original_renew = authoring_operations.renew_operation_lease
    original_request = main._shared_suggestion_request
    original_persist = authoring_operations.persist_operation_response

    def record_renew(*args, **kwargs):
        ticket = original_renew(*args, **kwargs)
        lease_renewed.append(ticket)
        return ticket

    def require_renew_before_request(*args, **kwargs):
        assert lease_renewed, "the worker must renew its lease before constructing the prompt"
        return original_request(*args, **kwargs)

    def observe_response_check(*args, **kwargs):
        try:
            return original_persist(*args, **kwargs)
        finally:
            response_checked.set()

    monkeypatch.setattr(authoring_operations, "renew_operation_lease", record_renew)
    monkeypatch.setattr(main, "_shared_suggestion_request", require_renew_before_request)
    monkeypatch.setattr(authoring_operations, "persist_operation_response", observe_response_check)

    plan_before = client.get(f"/api/sessions/{session_id}/plan").json()
    operations_url = f"/api/sessions/{session_id}/plan/authoring/operations"
    started = client.post(
        operations_url,
        json=_start_body("shared_suggestions", revision=revision),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    try:
        assert entered.wait(timeout=2), "the suggestion worker did not reach the fake assistant"
        request = calls[0]["body"]["messages"][1]["content"]
        assert "4da woman" in request
        assert "photo, 35mm" in request

        model = client.get(f"/api/models/{seeded['model_id']}").json()
        patch_fields = (
            "name", "lora_name", "trigger", "lora_strength", "base_positive",
            "base_negative", "workflow_id", "settings", "notes",
        )
        changed_model = {field: model[field] for field in patch_fields}
        changed_model.update(
            trigger="4da changed woman",
            base_positive="new verified portrait prompt",
        )
        patched = client.patch(f"/api/models/{seeded['model_id']}", json=changed_model)
        assert patched.status_code == 200, patched.text
    finally:
        release.set()

    assert response_checked.wait(timeout=2), "the suggestion response was not checked"
    status = client.get(f"{operations_url}/{operation_id}")
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "cancelled"
    assert status.json()["result"] is None
    operation_row = db.one(
        "SELECT state, result_json FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )
    assert operation_row["state"] != "succeeded"
    assert operation_row["result_json"] is None

    acceptance = client.post(
        f"{operations_url}/{operation_id}/accept",
        json={"expected_revision": revision, "accepted": output},
    )
    _error(acceptance, 409, "operation_not_succeeded")
    plan_after = client.get(f"/api/sessions/{session_id}/plan").json()
    assert plan_after["plan_revision"] == plan_before["plan_revision"] == revision
    assert plan_after["plan"] == plan_before["plan"]
    assert plan_after["plan"]["authoring"]["evidence"] == []
    assert db.one(
        "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?",
        session_id,
    )["n"] == 0


def test_invalid_shared_suggestion_is_not_partially_saved_and_resume_can_accept_an_empty_edit(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    output = {
        "look": "A soft studio makeup look.",
        "initial_wardrobe": "A navy blouse.",
    }
    calls = _install_fake_structured_assistant(
        monkeypatch,
        [
            {"look": "A partial suggestion.", "initial_wardrobe": 17},
            output,
        ],
    )
    operations_url = f"/api/sessions/{session_id}/plan/authoring/operations"
    started = client.post(
        operations_url,
        json=_start_body("shared_suggestions", revision=revision),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]

    failed = _wait_for_operation_state(client, session_id, operation_id, "failed")
    assert failed["result"] is None
    row = db.one(
        "SELECT result_json FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )
    assert row["result_json"] is None
    plan = client.get(f"/api/sessions/{session_id}/plan").json()
    assert plan["plan_revision"] == revision
    assert plan["plan"]["authoring"]["evidence"] == []
    assert len(calls) == 1

    resumed = client.post(
        f"{operations_url}/{operation_id}/resume",
        json={"expected_revision": revision},
    )
    assert resumed.status_code == 202, resumed.text
    succeeded = _wait_for_operation_state(client, session_id, operation_id, "succeeded")
    assert len(calls) == 2
    assert succeeded["result"]["items"] == [
        {"target": "look", "result": output["look"]},
        {"target": "initial_wardrobe", "result": output["initial_wardrobe"]},
    ]
    still_pending = client.get(f"/api/sessions/{session_id}/plan").json()
    assert still_pending["plan_revision"] == revision
    assert still_pending["plan"]["authoring"]["evidence"] == []

    accepted = {"look": "", "initial_wardrobe": output["initial_wardrobe"]}
    accept_url = f"{operations_url}/{operation_id}/accept"
    body = {"expected_revision": revision, "accepted": accepted}
    response = client.post(accept_url, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["accepted"] == accepted
    assert result["evidence"]["output"] == output
    assert result["evidence"]["accepted"] == accepted
    saved_plan = client.get(f"/api/sessions/{session_id}/plan").json()
    assert saved_plan["plan_revision"] == revision + 1
    assert saved_plan["plan"]["look"] == ""
    assert saved_plan["plan"]["authoring"]["shared_state"]["look"] == {
        "origin": "assistant_edited",
        "evidence_id": operation_id,
    }
    replay = client.post(accept_url, json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json() == result
    assert client.get(f"/api/sessions/{session_id}/plan").json()["plan_revision"] == revision + 1


@pytest.mark.parametrize(
    "output",
    [
        {"look": "A suggested look.", "initial_wardrobe": "A suggested outfit."},
        {"look": 17, "initial_wardrobe": "A suggested outfit."},
    ],
)
def test_shared_suggestion_cancellation_discards_late_provider_response(
    client, seeded, monkeypatch, output,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    entered = threading.Event()
    release = threading.Event()
    calls = _install_fake_structured_assistant(
        monkeypatch,
        [output],
        entered=entered,
        release=release,
    )
    operations_url = f"/api/sessions/{session_id}/plan/authoring/operations"
    started = client.post(
        operations_url,
        json=_start_body("shared_suggestions", revision=revision),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    assert entered.wait(timeout=2), "the suggestion worker did not reach the fake assistant"

    cancelled = client.post(
        f"{operations_url}/{operation_id}/cancel",
        json={"expected_revision": revision},
    )
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["state"] == "cancel_requested"
    release.set()

    terminal = _wait_for_operation_state(client, session_id, operation_id, "cancelled")
    assert terminal["result"] is None
    assert len(calls) == 1
    current = client.get(f"/api/sessions/{session_id}/plan").json()
    assert current["plan_revision"] == revision
    assert current["plan"]["authoring"]["evidence"] == []
    assert db.one(
        "SELECT result_json FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )["result_json"] is None


def test_shared_suggestion_response_cannot_replace_a_newer_manual_plan_value(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    _enable_background_suggestion_dispatch(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    entered = threading.Event()
    release = threading.Event()
    calls = _install_fake_structured_assistant(
        monkeypatch,
        [{"look": "A late assistant look.", "initial_wardrobe": "A late assistant outfit."}],
        entered=entered,
        release=release,
    )
    operations_url = f"/api/sessions/{session_id}/plan/authoring/operations"
    started = client.post(
        operations_url,
        json=_start_body("shared_suggestions", revision=revision),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    assert entered.wait(timeout=2), "the suggestion worker did not reach the fake assistant"

    current = client.get(f"/api/sessions/{session_id}/plan").json()
    current["plan"]["look"] = "An operator-chosen look."
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": current["plan"], "expected_revision": revision},
    )
    assert saved.status_code == 200, saved.text
    release.set()

    terminal = _wait_for_operation_state(client, session_id, operation_id, "cancelled")
    assert terminal["result"] is None
    assert len(calls) == 1
    latest = client.get(f"/api/sessions/{session_id}/plan").json()
    assert latest["plan_revision"] == revision + 1
    assert latest["plan"]["look"] == "An operator-chosen look."
    assert latest["plan"]["authoring"]["shared_state"]["look"] == {
        "origin": "user",
        "evidence_id": None,
    }
    assert latest["plan"]["authoring"]["evidence"] == []


def test_manual_mode_keeps_explicit_empty_choices_without_starting_assistant_work(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    calls = _install_fake_structured_assistant(monkeypatch, [])
    session_id, revision = _create_guided_session(
        client,
        seeded,
        mode="manual",
        look="A temporary look.",
        initial_wardrobe="A temporary outfit.",
    )
    current = client.get(f"/api/sessions/{session_id}/plan").json()
    current["plan"]["look"] = ""
    current["plan"]["initial_wardrobe"] = ""
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": current["plan"], "expected_revision": revision},
    )
    assert saved.status_code == 200, saved.text
    current = client.get(f"/api/sessions/{session_id}/plan").json()
    assert current["shared_summary"]["available"] is True
    assert current["plan"]["authoring"]["shared_state"] == {
        "look": {"origin": "user", "evidence_id": None},
        "initial_wardrobe": {"origin": "user", "evidence_id": None},
    }

    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body("shared_suggestions", revision=saved.json()["plan_revision"]),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"
    assert _operation_count(session_id) == 0
    assert calls == []


def _operation_snapshot(operation_id):
    row = db.one(
        "SELECT state, fencing_token, lease_expires_at, input_digest, completed_json, "
        "failed_item, error, remaining_json, result_json, updated_at FROM authoring_operation "
        "WHERE operation_id = ?",
        operation_id,
    )
    assert row is not None
    return row


def _acceptance_snapshot(operation_id):
    row = db.one(
        "SELECT acceptance_digest, acceptance_result_json FROM authoring_operation WHERE operation_id = ?",
        operation_id,
    )
    assert row is not None
    return row


def test_succeeded_shared_suggestions_are_accepted_with_exact_provenance_and_replay(
    client, seeded, monkeypatch,
):
    output = {
        "look": "A natural studio makeup look.",
        "initial_wardrobe": "A navy blouse.",
    }
    session_id, operation_id, revision, _ = _complete_shared_suggestions(
        client, seeded, monkeypatch, output=output,
    )
    accepted = {"look": output["look"], "initial_wardrobe": ""}
    url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/accept"

    response = client.post(
        url,
        json={"expected_revision": revision, "accepted": accepted},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["plan_revision"] == revision + 1
    assert result["accepted"] == accepted
    assert result["evidence"] == {
        "id": operation_id,
        "kind": "shared_choices",
        "input": {
            "messages": [
                {"role": "system", "content": "Suggest missing shared choices."},
                {"role": "user", "content": "Use the authorized scene summary."},
            ],
            "model": "invented-assistant-model",
            "parameters": {
                "temperature": 0.2,
                "stream": False,
                "response_format": {"type": "json_object"},
                "reasoning_effort": "none",
            },
            "plan_revision": revision,
        },
        "output": output,
        "accepted": accepted,
    }
    persisted = client.get(f"/api/sessions/{session_id}/plan").json()
    assert persisted["plan_revision"] == revision + 1
    assert persisted["plan"]["look"] == output["look"]
    assert persisted["plan"]["initial_wardrobe"] == ""
    assert persisted["plan"]["authoring"]["shared_state"] == {
        "look": {"origin": "assistant", "evidence_id": operation_id},
        "initial_wardrobe": {"origin": "assistant_edited", "evidence_id": operation_id},
    }
    saved_operation = _acceptance_snapshot(operation_id)
    assert saved_operation["acceptance_digest"]
    assert json.loads(saved_operation["acceptance_result_json"]) == result

    replay = client.post(
        url,
        json={"expected_revision": revision, "accepted": accepted},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == result
    assert client.get(f"/api/sessions/{session_id}/plan").json()["plan_revision"] == revision + 1

    changed = client.post(
        url,
        json={"expected_revision": revision, "accepted": {**accepted, "look": "Another look."}},
    )
    _error(changed, 409, "acceptance_conflict")
    assert client.get(f"/api/sessions/{session_id}/plan").json()["plan_revision"] == revision + 1

    status = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    )
    assert status.status_code == 200, status.text
    assert status.json()["result"] == {
        "items": [
            {"target": "look", "result": output["look"]},
            {"target": "initial_wardrobe", "result": output["initial_wardrobe"]},
        ]
    }


def test_concurrent_identical_suggestion_acceptance_has_one_plan_cas(client, seeded, monkeypatch):
    session_id, operation_id, revision, output = _complete_shared_suggestions(client, seeded, monkeypatch)
    url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/accept"
    body = {
        "expected_revision": revision,
        "accepted": output,
    }
    barrier = threading.Barrier(2)

    def accept():
        barrier.wait(timeout=5)
        return client.post(url, json=body)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: accept(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["plan_revision"] == revision + 1
    assert client.get(f"/api/sessions/{session_id}/plan").json()["plan_revision"] == revision + 1


def test_single_missing_shared_field_can_be_accepted_without_touching_explicit_value(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(
        client,
        seeded,
        look="An explicit existing look.",
        initial_wardrobe="",
    )
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body("shared_suggestions", revision=revision),
    )
    assert started.status_code == 202, started.text
    operation_id = started.json()["operation_id"]
    claim = authoring_operations.load_worker_claim(session_id, operation_id)
    ticket = authoring_operations.renew_operation_lease(claim)
    _persist_response_for_state_test(
        claim,
        ticket,
        [{"target": "initial_wardrobe", "result": "A suggested linen shirt."}],
        suggestion_input={
            "messages": [{"role": "user", "content": "Suggest the missing wardrobe."}],
            "model": "invented-assistant-model",
            "parameters": {},
            "plan_revision": revision,
        },
    )

    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/accept",
        json={"expected_revision": revision, "accepted": {"initial_wardrobe": ""}},
    )

    assert response.status_code == 200, response.text
    current = client.get(f"/api/sessions/{session_id}/plan").json()
    assert current["plan"]["look"] == "An explicit existing look."
    assert current["plan"]["initial_wardrobe"] == ""
    assert current["plan"]["authoring"]["shared_state"] == {
        "look": {"origin": "user", "evidence_id": None},
        "initial_wardrobe": {"origin": "assistant_edited", "evidence_id": operation_id},
    }


def test_suggestion_acceptance_stale_revision_is_write_free(client, seeded, monkeypatch):
    session_id, operation_id, revision, _ = _complete_shared_suggestions(client, seeded, monkeypatch)
    before_plan = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    db.run(
        "UPDATE session_plan SET plan_revision = ? WHERE session_id = ?",
        revision + 1,
        session_id,
    )
    after_setup = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    assert after_setup["plan_json"] == before_plan["plan_json"]
    url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/accept"

    response = client.post(
        url,
        json={
            "expected_revision": revision,
            "accepted": {"look": "A changed look.", "initial_wardrobe": "A changed outfit."},
        },
    )

    _error(response, 409, "plan_revision_stale")
    current_plan = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    assert current_plan == after_setup
    assert _acceptance_snapshot(operation_id) == {
        "acceptance_digest": None,
        "acceptance_result_json": None,
    }
    assert _operation_snapshot(operation_id)["state"] == "succeeded"


def test_invalid_suggestion_result_is_rejected_without_plan_or_operation_write(
    client, seeded, monkeypatch,
):
    session_id, operation_id, revision, _ = _complete_shared_suggestions(client, seeded, monkeypatch)
    db.run(
        "UPDATE authoring_operation SET result_json = ? WHERE operation_id = ?",
        json.dumps({
            "items": [
                {"target": "look", "result": "A suggestion."},
                {"target": "initial_wardrobe", "result": 17},
            ],
            "input": {
                "messages": [],
                "model": "invented-assistant-model",
                "parameters": {},
                "plan_revision": revision,
            },
        }),
        operation_id,
    )
    before_plan = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    before_acceptance = _acceptance_snapshot(operation_id)

    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/accept",
        json={
            "expected_revision": revision,
            "accepted": {"look": "A suggestion.", "initial_wardrobe": ""},
        },
    )

    _error(response, 409, "operation_result_invalid")
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    ) == before_plan
    assert _acceptance_snapshot(operation_id) == before_acceptance


def test_suggestion_request_rejects_secret_parameters_before_operation_write(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_shared_worker(client, seeded, monkeypatch)
    revision = claim.plan_revision
    ticket = authoring_operations.renew_operation_lease(claim)
    before_operation = _operation_snapshot(operation_id)
    before_plan = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )

    with pytest.raises(authoring_operations.AuthoringOperationError) as caught:
        _persist_response_for_state_test(
            claim,
            ticket,
            [
                {"target": "look", "result": "A suggestion."},
                {"target": "initial_wardrobe", "result": "A suggested outfit."},
            ],
            suggestion_input={
                "messages": [{"role": "user", "content": "Suggest missing choices."}],
                "model": "invented-assistant-model",
                "parameters": {"api_key": "synthetic-secret"},
                "plan_revision": revision,
            },
        )

    assert caught.value.code == "operation_result_invalid"
    assert _operation_snapshot(operation_id) == before_operation
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    ) == before_plan


def test_secret_suggestion_evidence_is_rejected_without_acceptance_write(client, seeded, monkeypatch):
    session_id, operation_id, revision, output = _complete_shared_suggestions(client, seeded, monkeypatch)
    row = db.one("SELECT result_json FROM authoring_operation WHERE operation_id = ?", operation_id)
    saved_result = json.loads(row["result_json"])
    saved_result["input"]["parameters"] = {"api_key": "synthetic-secret"}
    db.run(
        "UPDATE authoring_operation SET result_json = ? WHERE operation_id = ?",
        json.dumps(saved_result),
        operation_id,
    )
    before_operation = _operation_snapshot(operation_id)
    before_plan = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    before_acceptance = _acceptance_snapshot(operation_id)

    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/accept",
        json={"expected_revision": revision, "accepted": output},
    )

    _error(response, 409, "operation_result_invalid")
    assert _operation_snapshot(operation_id) == before_operation
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    ) == before_plan
    assert _acceptance_snapshot(operation_id) == before_acceptance


def test_suggestion_acceptance_is_blocked_by_disabled_gate_before_body_parsing(
    client, seeded, monkeypatch,
):
    session_id, operation_id, revision, _ = _complete_shared_suggestions(client, seeded, monkeypatch)
    before_plan = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)

    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/accept",
        content="{",
        headers={"content-type": "application/json"},
    )

    _error(response, 503, "resource_planning_disabled")
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    ) == before_plan
    assert _acceptance_snapshot(operation_id) == {
        "acceptance_digest": None,
        "acceptance_result_json": None,
    }


def test_suggestion_acceptance_respects_generated_continuity_freeze(client, seeded, monkeypatch):
    session_id, operation_id, revision, _ = _complete_shared_suggestions(client, seeded, monkeypatch)
    now = db.now()
    db.run(
        "INSERT INTO prepared_take (session_id, plan_revision, take_id, status, created_at, updated_at) "
        "VALUES (?, ?, 'take-001', 'generated', ?, ?)",
        session_id,
        revision,
        now,
        now,
    )
    before_plan = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )

    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/accept",
        json={
            "expected_revision": revision,
            "accepted": {"look": "A new look.", "initial_wardrobe": "A new outfit."},
        },
    )

    _error(response, 409, "plan_constants_frozen")
    assert db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    ) == before_plan
    assert db.one(
        "SELECT status FROM prepared_take WHERE session_id = ? AND take_id = 'take-001'",
        session_id,
    )["status"] == "generated"
    assert _acceptance_snapshot(operation_id) == {
        "acceptance_digest": None,
        "acceptance_result_json": None,
    }


def _apply_scene_translation_change(client, session_id):
    plan_row = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    plan = json.loads(plan_row["plan_json"])
    selected = next(
        item
        for item in plan["selected_resources"]
        if db.one(
            "SELECT kind FROM resource_library WHERE library_key = ?",
            item["library_key"],
        )["kind"] == "rooms"
    )
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        selected["library_key"],
    )
    before = resource_store.get_revision(
        library_id=library["id"],
        source_id=selected["source_id"],
        content_digest=selected["content_digest"],
    )
    assert before is not None

    rows = client.get(
        f"/api/resources/libraries/{selected['library_key']}/translations/rows"
    )
    assert rows.status_code == 200, rows.text
    scene_theme = next(row for row in rows.json()["rows"] if row["field"] == "scene_theme")
    source = scene_theme["source_value"]
    translation_map = {
        source: {
            "source": source,
            "translation": "A revised scene description for operation validation.",
            "fields": ["scene_theme"],
        },
    }
    preview = client.post(
        f"/api/resources/libraries/{selected['library_key']}/translations/preview",
        json={"translation_map": translation_map},
    )
    assert preview.status_code == 200, preview.text
    applied = client.post(
        f"/api/resources/libraries/{selected['library_key']}/translations/apply",
        json={
            "translation_map": translation_map,
            "attestation_token": preview.json()["attestation_token"],
        },
    )
    assert applied.status_code == 200, applied.text
    after = resource_store.get_revision(revision_id=before["id"])
    assert after is not None
    current_plan_revision = db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?",
        session_id,
    )["plan_revision"]
    assert current_plan_revision == plan_row["plan_revision"]
    assert after["content_digest"] == before["content_digest"]
    assert after["translation"] != before["translation"]
    return before, after


def _assert_refused_without_writes(claim, ticket, operation_id, items, expected_code):
    before = _operation_snapshot(operation_id)
    recovered = None
    for action in (
        lambda: authoring_operations.renew_operation_lease(claim),
        lambda: _persist_response_for_state_test(claim, ticket, items),
    ):
        with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
            action()
        assert exc_info.value.code == expected_code
        after = _operation_snapshot(operation_id)
        if expected_code == "authoring_lease_expired":
            if recovered is None:
                assert after["state"] == "expired"
                assert after["fencing_token"] == before["fencing_token"] + 1
                assert after["lease_expires_at"] is None
                assert after["completed_json"] == before["completed_json"]
                assert after["remaining_json"] == before["remaining_json"]
                assert after["result_json"] == before["result_json"]
                assert "lease expired" in after["error"]
                recovered = after
            else:
                assert after == recovered
        else:
            assert after == before
        assert not db.conn().in_transaction


def test_worker_renews_before_each_boundary_and_persists_ordered_results(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001", "take-002"],
    )
    initial_deadline = datetime.fromisoformat(
        _operation_snapshot(operation_id)["lease_expires_at"]
    )
    first_now = initial_deadline - timedelta(minutes=2)
    monkeypatch.setattr(db, "now", lambda: first_now.isoformat(timespec="seconds"))

    first_ticket = authoring_operations.renew_operation_lease(claim)
    assert datetime.fromisoformat(first_ticket.lease_expires_at) == first_now + timedelta(minutes=10)
    assert not db.conn().in_transaction

    def fake_remote_call():
        assert not db.conn().in_transaction
        return {"choices": {"pose": "standing beside a tall window"}}

    first_result = fake_remote_call()
    first_view = _persist_response_for_state_test(
        claim,
        first_ticket,
        [{"target": "take-001", "result": first_result}],
    )
    assert first_view["state"] == "active"
    assert first_view["progress"] == {
        "requested": ["take-001", "take-002"],
        "completed": ["take-001"],
        "failed": None,
        "remaining": ["take-002"],
    }
    _assert_snapshot_results(first_view["result"]["items"], session_id, ["take-001"])

    second_now = first_now + timedelta(minutes=2)
    monkeypatch.setattr(db, "now", lambda: second_now.isoformat(timespec="seconds"))
    second_ticket = authoring_operations.renew_operation_lease(claim)
    assert datetime.fromisoformat(second_ticket.lease_expires_at) == second_now + timedelta(minutes=10)
    assert not db.conn().in_transaction
    second_result = fake_remote_call()
    final_view = _persist_response_for_state_test(
        claim,
        second_ticket,
        [{"target": "take-002", "result": second_result}],
    )
    assert final_view["state"] == "succeeded"
    assert final_view["lease_expires_at"] is None
    assert final_view["progress"]["completed"] == ["take-001", "take-002"]
    assert final_view["progress"]["remaining"] == []
    _assert_snapshot_results(final_view["result"]["items"], session_id, ["take-001", "take-002"])
    assert _operation_count(session_id) == 1
    assert not {"fencing_token", "request_digest"}.intersection(final_view)

    status = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    )
    assert status.status_code == 200, status.text
    assert status.json() == final_view
    resumed_terminal = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/resume",
        json={"expected_revision": claim.plan_revision},
    )
    assert resumed_terminal.status_code == 200, resumed_terminal.text
    assert resumed_terminal.json() == final_view


def test_prepare_result_cannot_advance_without_ready_snapshot(client, seeded, monkeypatch):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    before = _operation_snapshot(operation_id)
    with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
        _REAL_PERSIST_OPERATION_RESPONSE(
            claim, ticket,
            [{"target": "take-001", "result": {"pose": "invented pose"}}],
        )
    assert exc_info.value.code == "invalid_operation_result"
    assert _operation_snapshot(operation_id) == before
    assert db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND take_id = ?",
        session_id, "take-001",
    ) is None
    with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
        _REAL_PERSIST_OPERATION_RESPONSE(
            claim, ticket,
            [{"target": "take-001", "result": {"pose": "invented pose"}}],
            finalize_take=lambda target, result: {
                "id": 123, "session_id": session_id,
                "plan_revision": claim.plan_revision,
                "take_id": target, "status": "ready",
            },
        )
    assert exc_info.value.code == "invalid_operation_result"
    assert _operation_snapshot(operation_id) == before


def test_status_read_does_not_renew_or_rewrite_the_lease(client, seeded, monkeypatch):
    session_id, operation_id, _ = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    sentinel_lease = "2035-01-01T00:00:00+00:00"
    db.run(
        "UPDATE authoring_operation SET lease_expires_at = ?, updated_at = ? WHERE operation_id = ?",
        sentinel_lease,
        "before-status-read",
        operation_id,
    )
    before = _operation_snapshot(operation_id)

    status = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    )

    assert status.status_code == 200, status.text
    assert status.json()["lease_expires_at"] == sentinel_lease
    assert status.json()["updated_at"] == "before-status-read"
    assert _operation_snapshot(operation_id) == before


def test_cancel_without_owner_is_finalized_by_lazy_lease_recovery(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001", "take-002"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"

    before_bad_request = _operation_snapshot(operation_id)
    _error(client.post(f"{url}/cancel", json={"expected_revision": claim.plan_revision, "owner": "private"}), 422, "extra_field_forbidden")
    assert _operation_snapshot(operation_id) == before_bad_request

    cancelled = client.post(f"{url}/cancel", json={"expected_revision": claim.plan_revision})
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["state"] == "cancel_requested"
    assert cancelled.json()["progress"]["completed"] == []
    assert cancelled.json()["progress"]["remaining"] == ["take-001", "take-002"]
    assert cancelled.json()["can_cancel"] is False
    assert cancelled.json()["can_resume"] is False

    expired_at = datetime.fromisoformat(ticket.lease_expires_at) + timedelta(seconds=1)
    monkeypatch.setattr(db, "now", lambda: expired_at.isoformat(timespec="seconds"))
    recovered = client.get(url)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["state"] == "cancelled"
    assert recovered.json()["lease_expires_at"] is None
    assert recovered.json()["can_resume"] is True
    assert "preserved for resuming" in recovered.json()["error"]
    cancel_replay = client.post(
        f"{url}/cancel",
        json={"expected_revision": claim.plan_revision},
    )
    assert cancel_replay.status_code == 200, cancel_replay.text
    assert cancel_replay.json()["state"] == "cancelled"


def test_cancel_and_in_flight_response_race_has_one_serialized_winner(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    operation_url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    barrier = threading.Barrier(2)

    def request_cancel():
        barrier.wait(timeout=5)
        return client.post(
            f"{operation_url}/cancel",
            json={"expected_revision": claim.plan_revision},
        )

    def persist_response():
        barrier.wait(timeout=5)
        try:
            return _persist_response_for_state_test(
                claim,
                ticket,
                [{"target": "take-001", "result": {"pose": "standing near a window"}}],
            )
        except authoring_operations.AuthoringOperationError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        cancel_future = executor.submit(request_cancel)
        persist_future = executor.submit(persist_response)
        cancel_response = cancel_future.result(timeout=10)
        persist_result = persist_future.result(timeout=10)

    row = _operation_snapshot(operation_id)
    assert cancel_response.status_code in (200, 202), cancel_response.text
    if cancel_response.status_code == 202:
        assert cancel_response.json()["state"] == "cancel_requested"
        assert persist_result == "operation_not_active"
        assert row["state"] == "cancelled"
        assert row["completed_json"] == "[]"
        assert row["result_json"] is None
    else:
        assert cancel_response.json()["state"] == "succeeded"
        assert isinstance(persist_result, dict)
        assert persist_result["state"] == "succeeded"
        assert row["completed_json"] == '["take-001"]'
        assert json.loads(row["result_json"])["items"][0]["target"] == "take-001"


def test_expired_operation_resumes_with_new_fence_and_preserved_results(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001", "take-002"],
    )
    first_now = datetime.fromisoformat(_operation_snapshot(operation_id)["lease_expires_at"]) - timedelta(minutes=2)
    monkeypatch.setattr(db, "now", lambda: first_now.isoformat(timespec="seconds"))
    ticket = authoring_operations.renew_operation_lease(claim)
    first_result = {"choices": {"pose": "standing beside a tall window"}}
    partial = _persist_response_for_state_test(
        claim,
        ticket,
        [{"target": "take-001", "result": first_result}],
    )
    assert partial["state"] == "active"
    assert partial["progress"]["completed"] == ["take-001"]

    expired_at = datetime.fromisoformat(ticket.lease_expires_at) + timedelta(seconds=1)
    monkeypatch.setattr(db, "now", lambda: expired_at.isoformat(timespec="seconds"))
    operation_url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    expired = client.get(operation_url)
    assert expired.status_code == 200, expired.text
    assert expired.json()["state"] == "expired"
    assert expired.json()["progress"]["completed"] == ["take-001"]
    assert expired.json()["progress"]["remaining"] == ["take-002"]
    _assert_snapshot_results(expired.json()["result"]["items"], session_id, ["take-001"])
    assert expired.json()["can_resume"] is True
    expired_fence = _operation_snapshot(operation_id)["fencing_token"]

    resumed = client.post(
        f"{operation_url}/resume",
        json={"expected_revision": claim.plan_revision},
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["state"] == "active"
    assert resumed.json()["progress"]["completed"] == ["take-001"]
    assert resumed.json()["progress"]["remaining"] == ["take-002"]
    assert resumed.json()["result"]["items"] == expired.json()["result"]["items"]
    assert _operation_snapshot(operation_id)["fencing_token"] == expired_fence + 1
    assert datetime.fromisoformat(resumed.json()["lease_expires_at"]) == expired_at + timedelta(minutes=10)
    resumed_lease = _operation_snapshot(operation_id)["lease_expires_at"]
    active_replay = client.post(
        f"{operation_url}/resume",
        json={"expected_revision": claim.plan_revision},
    )
    assert active_replay.status_code == 200, active_replay.text
    assert active_replay.json() == resumed.json()
    assert _operation_snapshot(operation_id)["lease_expires_at"] == resumed_lease

    before_late_response = _operation_snapshot(operation_id)
    with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
        _persist_response_for_state_test(
            claim,
            ticket,
            [{"target": "take-002", "result": {"choices": {"pose": "stale result"}}}],
        )
    assert exc_info.value.code == "authoring_owner_stale"
    assert _operation_snapshot(operation_id) == before_late_response

    resumed_claim = authoring_operations.load_worker_claim(session_id, operation_id)
    resumed_ticket = authoring_operations.renew_operation_lease(resumed_claim)
    second_result = {"choices": {"pose": "standing beside a table"}}
    completed = _persist_response_for_state_test(
        resumed_claim,
        resumed_ticket,
        [{"target": "take-002", "result": second_result}],
    )
    assert completed["state"] == "succeeded"
    assert completed["progress"]["completed"] == ["take-001", "take-002"]
    _assert_snapshot_results(completed["result"]["items"], session_id, ["take-001", "take-002"])
    assert client.get(operation_url).json() == completed


def test_resume_retries_failed_item_before_remaining_without_repeating_completed_work(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch,
        take_ids=["take-001", "take-002", "take-003"],
    )
    first_ticket = authoring_operations.renew_operation_lease(claim)
    first_result = {"choices": {"pose": "sitting beside a lamp"}}
    _persist_response_for_state_test(
        claim,
        first_ticket,
        [{"target": "take-001", "result": first_result}],
    )
    failure_ticket = authoring_operations.renew_operation_lease(claim)
    failed = authoring_operations.fail_operation_item(
        claim,
        failure_ticket,
        "take-002",
    )
    assert failed["state"] == "failed"
    assert failed["progress"]["completed"] == ["take-001"]
    assert failed["progress"]["failed"]["take_id"] == "take-002"
    assert "could not complete this item" in failed["progress"]["failed"]["error"]
    assert failed["progress"]["remaining"] == ["take-003"]

    operation_url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    failed_fence = _operation_snapshot(operation_id)["fencing_token"]
    resumed = client.post(
        f"{operation_url}/resume",
        json={"expected_revision": claim.plan_revision},
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["progress"] == {
        "requested": ["take-001", "take-002", "take-003"],
        "completed": ["take-001"],
        "failed": None,
        "remaining": ["take-002", "take-003"],
    }
    _assert_snapshot_results(resumed.json()["result"]["items"], session_id, ["take-001"])
    assert _operation_snapshot(operation_id)["fencing_token"] == failed_fence + 1


def test_startup_expires_prior_active_owners_and_finalizes_cancel_requests(
    client, seeded, monkeypatch,
):
    first_session, first_id, first_claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001", "take-002"],
    )
    first_ticket = authoring_operations.renew_operation_lease(first_claim)
    first_result = {"choices": {"pose": "standing at an easel"}}
    _persist_response_for_state_test(
        first_claim,
        first_ticket,
        [{"target": "take-001", "result": first_result}],
    )

    second_session, second_id, second_claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001", "take-002"],
    )
    second_ticket = authoring_operations.renew_operation_lease(second_claim)
    second_result = {"choices": {"pose": "sitting on a bench"}}
    _persist_response_for_state_test(
        second_claim,
        second_ticket,
        [{"target": "take-001", "result": second_result}],
    )
    pending = client.post(
        f"/api/sessions/{second_session}/plan/authoring/operations/{second_id}/cancel",
        json={"expected_revision": second_claim.plan_revision},
    )
    assert pending.status_code == 202, pending.text
    assert pending.json()["progress"]["completed"] == ["take-001"]
    assert pending.json()["progress"]["remaining"] == ["take-002"]

    old_connection = db.conn()
    old_connection.close()
    db.connect(main.DATA_DIR / "idevgen.db")
    restart_time = datetime.fromisoformat(db.now()) + timedelta(minutes=1)

    recovered_count = authoring_operations.startup_recovery(
        planning_enabled=True,
        now=restart_time.isoformat(timespec="seconds"),
    )
    assert recovered_count == 2
    first = client.get(
        f"/api/sessions/{first_session}/plan/authoring/operations/{first_id}"
    ).json()
    second = client.get(
        f"/api/sessions/{second_session}/plan/authoring/operations/{second_id}"
    ).json()
    assert first["state"] == "expired"
    assert first["progress"]["completed"] == ["take-001"]
    assert first["progress"]["remaining"] == ["take-002"]
    _assert_snapshot_results(first["result"]["items"], first_session, ["take-001"])
    assert "application restarted" in first["error"]
    assert second["state"] == "cancelled"
    assert second["progress"]["completed"] == ["take-001"]
    assert second["progress"]["remaining"] == ["take-002"]
    _assert_snapshot_results(second["result"]["items"], second_session, ["take-001"])
    assert "preserved for resuming" in second["error"]


def test_feature_disable_cancels_before_renewal_or_late_response_persistence(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)

    with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
        _persist_response_for_state_test(
            claim,
            ticket,
            [{"target": "take-001", "result": {"pose": "late output"}}],
        )
    assert exc_info.value.code == "resource_planning_disabled"
    row = _operation_snapshot(operation_id)
    assert row["state"] == "cancelled"
    assert row["lease_expires_at"] is None
    assert row["completed_json"] == "[]"
    assert row["result_json"] is None
    assert "feature_disabled" in row["error"]

    resume = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}/resume",
        json={"expected_revision": claim.plan_revision},
    )
    _error(resume, 503, "resource_planning_disabled")
    assert _operation_snapshot(operation_id) == row


def test_plan_cas_cancels_old_owner_and_discards_its_late_output(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    current = client.get(f"/api/sessions/{session_id}/plan")
    assert current.status_code == 200, current.text
    saved = client.post(
        f"/api/sessions/{session_id}/plan",
        json={"plan": current.json()["plan"], "expected_revision": claim.plan_revision},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["plan_revision"] == claim.plan_revision + 1
    before_late_response = _operation_snapshot(operation_id)
    assert before_late_response["state"] == "cancelled"
    assert before_late_response["fencing_token"] == claim.fencing_token + 1
    assert "plan changed" in before_late_response["error"]

    with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
        _persist_response_for_state_test(
            claim,
            ticket,
            [{"target": "take-001", "result": {"pose": "stale output"}}],
        )
    assert exc_info.value.code == "authoring_owner_stale"
    assert _operation_snapshot(operation_id) == before_late_response


def test_operation_view_sanitizes_persisted_failure_diagnostics(client, seeded, monkeypatch):
    session_id, operation_id, _ = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    db.run(
        "UPDATE authoring_operation SET state='failed', lease_expires_at=NULL, "
        "failed_item='take-001', error=?, remaining_json='[]' WHERE operation_id=?",
        "credential-placeholder-value in fixture-input",
        operation_id,
    )
    status = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    )
    assert status.status_code == 200, status.text
    encoded = json.dumps(status.json())
    assert "credential-placeholder-value" not in encoded
    assert "fixture-input" not in encoded
    assert status.json()["progress"]["failed"]["error"] == (
        "The authoring assistant could not complete this item. "
        "Completed work was preserved for retry."
    )


def test_migrated_operation_without_input_digest_does_not_promise_resume(client, seeded, monkeypatch):
    session_id, operation_id, _ = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    db.run("DROP TRIGGER IF EXISTS authoring_operation_input_digest_immutable")
    db.run(
        "UPDATE authoring_operation SET input_digest='', state='expired', "
        "lease_expires_at=NULL, error=? WHERE operation_id=?",
        "The application restarted before this operation finished. "
        "Completed work was preserved for resuming.",
        operation_id,
    )

    status = client.get(
        f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    )

    assert status.status_code == 200, status.text
    assert status.json()["can_resume"] is False
    assert "preserved for resuming" not in status.json()["error"]
    assert status.json()["error"] == (
        "The operation's original inputs could not be verified after migration. "
        "Reload the plan and start again."
    )


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("late_fence", "authoring_owner_stale"),
        ("expired", "authoring_lease_expired"),
        ("stale_revision", "plan_revision_stale"),
        ("changed_request", "authoring_inputs_stale"),
    ],
)
def test_fenced_response_refuses_late_or_changed_claims_without_partial_writes(
    client, seeded, monkeypatch, failure, expected_code,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001", "take-002"],
    )
    fixed_now = datetime.fromisoformat(_operation_snapshot(operation_id)["lease_expires_at"]) - timedelta(minutes=1)
    monkeypatch.setattr(db, "now", lambda: fixed_now.isoformat(timespec="seconds"))
    ticket = authoring_operations.renew_operation_lease(claim)
    if failure == "late_fence":
        claim = replace(claim, fencing_token=claim.fencing_token + 1)
    elif failure == "expired":
        db.run(
            "UPDATE authoring_operation SET lease_expires_at = ? WHERE operation_id = ?",
            (fixed_now - timedelta(seconds=1)).isoformat(timespec="seconds"),
            operation_id,
        )
    elif failure == "stale_revision":
        db.run(
            "UPDATE session_plan SET plan_revision = plan_revision + 1 WHERE session_id = ?",
            session_id,
        )
    else:
        claim = replace(claim, request_digest="0" * 64)

    _assert_refused_without_writes(
        claim,
        ticket,
        operation_id,
        [{"target": "take-001", "result": {"pose": "invented pose"}}],
        expected_code,
    )


def test_fenced_response_rechecks_the_selected_resource_revision(
    client, seeded, monkeypatch,
):
    from backend import resource_preparation

    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    plan = json.loads(db.one(
        "SELECT plan_json FROM session_plan WHERE session_id = ?", session_id,
    )["plan_json"])
    selected = plan["selected_resources"][0]
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        selected["library_key"],
    )
    revision = resource_store.get_revision(
        library_id=library["id"],
        source_id=selected["source_id"],
        content_digest=selected["content_digest"],
    )
    assert revision is not None
    db.run("DELETE FROM asset_revision WHERE id = ?", revision["id"])

    prepare_take_inputs = resource_preparation.prepare_take_inputs

    def assert_transactional_resolution(*args, **kwargs):
        assert db.conn().in_transaction
        return prepare_take_inputs(*args, **kwargs)

    monkeypatch.setattr(
        resource_preparation,
        "prepare_take_inputs",
        assert_transactional_resolution,
    )

    _assert_refused_without_writes(
        claim,
        ticket,
        operation_id,
        [{"target": "take-001", "result": {"pose": "invented pose"}}],
        "authoring_inputs_stale",
    )


def test_fenced_response_rechecks_the_bound_workflow(
    client, seeded, monkeypatch,
):
    from backend import workflow_binding

    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    session = db.one("SELECT workflow_id FROM session WHERE id = ?", session_id)
    db.run("UPDATE workflow SET kind = kind || '-changed' WHERE id = ?", session["workflow_id"])

    validate_binding = workflow_binding.validate_workflow_binding_against_session

    def assert_transactional_binding(*args, **kwargs):
        assert db.conn().in_transaction
        return validate_binding(*args, **kwargs)

    monkeypatch.setattr(
        workflow_binding,
        "validate_workflow_binding_against_session",
        assert_transactional_binding,
    )

    _assert_refused_without_writes(
        claim,
        ticket,
        operation_id,
        [{"target": "take-001", "result": {"pose": "invented pose"}}],
        "authoring_inputs_stale",
    )


def test_shared_suggestion_response_rechecks_authorized_scene_summary(
    client, seeded, monkeypatch,
):
    from backend import resource_preparation, workflow_binding

    session_id, operation_id, claim = _start_shared_worker(client, seeded, monkeypatch)
    ticket = authoring_operations.renew_operation_lease(claim)
    plan = json.loads(db.one(
        "SELECT plan_json FROM session_plan WHERE session_id = ?", session_id,
    )["plan_json"])
    selected = plan["selected_resources"][0]
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        selected["library_key"],
    )
    revision = resource_store.get_revision(
        library_id=library["id"],
        source_id=selected["source_id"],
        content_digest=selected["content_digest"],
    )
    assert revision is not None
    db.run("DELETE FROM asset_revision WHERE id = ?", revision["id"])

    build_summary = resource_preparation.build_shared_state_summary
    validate_binding = workflow_binding.validate_workflow_binding_against_session

    def assert_transactional_summary(plan):
        assert db.conn().in_transaction
        return build_summary(plan)

    def assert_transactional_binding(*args, **kwargs):
        assert db.conn().in_transaction
        return validate_binding(*args, **kwargs)

    monkeypatch.setattr(resource_preparation, "build_shared_state_summary", assert_transactional_summary)
    monkeypatch.setattr(
        workflow_binding,
        "validate_workflow_binding_against_session",
        assert_transactional_binding,
    )

    _assert_refused_without_writes(
        claim,
        ticket,
        operation_id,
        [
            {"target": "look", "result": "soft makeup"},
            {"target": "initial_wardrobe", "result": "a blue jacket"},
        ],
        "authoring_inputs_stale",
    )


@pytest.mark.parametrize("kind", ["prepare_takes", "shared_suggestions"])
def test_fenced_response_rejects_translation_sidecar_drift_with_same_plan_and_digest(
    client, seeded, monkeypatch, kind,
):
    if kind == "prepare_takes":
        session_id, operation_id, claim = _start_worker(
            client, seeded, monkeypatch, take_ids=["take-001"],
        )
        items = [{"target": "take-001", "result": {"pose": "invented pose"}}]
    else:
        session_id, operation_id, claim = _start_shared_worker(client, seeded, monkeypatch)
        items = [
            {"target": "look", "result": "soft makeup"},
            {"target": "initial_wardrobe", "result": "a blue jacket"},
        ]

    ticket = authoring_operations.renew_operation_lease(claim)
    assert not db.conn().in_transaction
    old_revision, new_revision = _apply_scene_translation_change(client, session_id)
    before = _operation_snapshot(operation_id)

    assert old_revision["content_digest"] == new_revision["content_digest"]
    with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
        _persist_response_for_state_test(claim, ticket, items)
    assert exc_info.value.code == "authoring_inputs_stale"
    assert _operation_snapshot(operation_id) == before
    assert not db.conn().in_transaction

    with db.transaction():
        current_row = authoring_operations._get_operation(operation_id)
        assert current_row is not None
        current_fingerprint = authoring_operations._current_operation_inputs(current_row)[-1]
    assert current_fingerprint != ticket.input_fingerprint
    forged_ticket = replace(ticket, input_fingerprint=current_fingerprint)
    before_forged_persist = _operation_snapshot(operation_id)
    with pytest.raises(authoring_operations.AuthoringOperationError) as forged_info:
        _persist_response_for_state_test(claim, forged_ticket, items)
    assert forged_info.value.code == "invalid_request"
    assert _operation_snapshot(operation_id) == before_forged_persist
    assert not db.conn().in_transaction


def test_public_request_cannot_forge_an_operation_lease_ticket(client, seeded, monkeypatch):
    _configure_assistant(monkeypatch)
    session_id, revision = _create_guided_session(client, seeded)
    body = _start_body(revision=revision)
    body["lease_ticket"] = {"input_fingerprint": "0" * 64}

    response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=body,
    )

    _error(response, 422, "extra_field_forbidden")
    assert _operation_count(session_id) == 0


def test_modified_lease_ticket_signature_cannot_persist_a_response(client, seeded, monkeypatch):
    _, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    forged = replace(ticket, _signature="0" * 64)
    before = _operation_snapshot(operation_id)

    with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
        _persist_response_for_state_test(
            claim,
            forged,
            [{"target": "take-001", "result": {"pose": "invented pose"}}],
        )

    assert exc_info.value.code == "invalid_request"
    assert _operation_snapshot(operation_id) == before
    assert not db.conn().in_transaction


@pytest.mark.parametrize("field", ["claim", "lease_expires_at"])
def test_lease_ticket_signature_authenticates_claim_and_deadline(
    client, seeded, monkeypatch, field,
):
    _, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    if field == "claim":
        forged = replace(
            ticket,
            claim=replace(ticket.claim, fencing_token=ticket.claim.fencing_token + 1),
        )
    else:
        forged = replace(ticket, lease_expires_at="2035-01-01T00:00:00+00:00")
    before = _operation_snapshot(operation_id)

    with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
        _persist_response_for_state_test(
            claim,
            forged,
            [{"target": "take-001", "result": {"pose": "invented pose"}}],
        )

    assert exc_info.value.code == "invalid_request"
    assert _operation_snapshot(operation_id) == before
    assert not db.conn().in_transaction


def test_response_and_progress_roll_back_together_when_the_write_fails(
    client, seeded, monkeypatch,
):
    _, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    db.run(
        """CREATE TRIGGER reject_authoring_result
           BEFORE UPDATE OF result_json ON authoring_operation
           BEGIN SELECT RAISE(ABORT, 'test write rejection'); END"""
    )
    before = _operation_snapshot(operation_id)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="test write rejection"):
            _persist_response_for_state_test(
                claim,
                ticket,
                [{"target": "take-001", "result": {"pose": "invented pose"}}],
            )
    finally:
        db.run("DROP TRIGGER IF EXISTS reject_authoring_result")
    assert _operation_snapshot(operation_id) == before


def test_late_response_finalizes_a_cancelled_owner_without_waiting_for_lease_expiry(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001", "take-002"],
    )
    first_ticket = authoring_operations.renew_operation_lease(claim)
    first_result = {"pose": "standing beside a tall window"}
    _persist_response_for_state_test(
        claim,
        first_ticket,
        [{"target": "take-001", "result": first_result}],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    operation_url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    cancel = client.post(
        f"{operation_url}/cancel",
        json={"expected_revision": claim.plan_revision},
    )
    assert cancel.status_code == 202, cancel.text
    assert cancel.json()["progress"]["completed"] == ["take-001"]
    assert cancel.json()["progress"]["remaining"] == ["take-002"]
    before_late_response = _operation_snapshot(operation_id)

    with pytest.raises(authoring_operations.AuthoringOperationError):
        _persist_response_for_state_test(
            claim,
            ticket,
            [{"target": "take-002", "result": {"pose": "late output"}}],
        )

    after_late_response = _operation_snapshot(operation_id)
    assert after_late_response["completed_json"] == before_late_response["completed_json"]
    assert after_late_response["remaining_json"] == before_late_response["remaining_json"]
    assert after_late_response["result_json"] == before_late_response["result_json"]
    _assert_snapshot_results(
        json.loads(after_late_response["result_json"])["items"], session_id, ["take-001"],
    )
    assert after_late_response["state"] == "cancelled"
    assert after_late_response["lease_expires_at"] is None
    assert after_late_response["fencing_token"] == before_late_response["fencing_token"] + 1


def test_resume_refuses_same_revision_after_effective_translation_drift(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    initial_plan_revision = db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?",
        session_id,
    )["plan_revision"]
    initial_lease = datetime.fromisoformat(
        _operation_snapshot(operation_id)["lease_expires_at"]
    )
    monkeypatch.setattr(
        db,
        "now",
        lambda: (initial_lease + timedelta(seconds=1)).isoformat(timespec="seconds"),
    )
    operation_url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    expired = client.get(operation_url)
    assert expired.status_code == 200, expired.text
    assert expired.json()["state"] == "expired"

    old_revision, new_revision = _apply_scene_translation_change(client, session_id)
    current_plan_revision = db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?",
        session_id,
    )["plan_revision"]
    assert current_plan_revision == initial_plan_revision
    assert old_revision["content_digest"] == new_revision["content_digest"]
    assert old_revision["translation"] != new_revision["translation"]
    before_resume = _operation_snapshot(operation_id)

    resumed = client.post(
        f"{operation_url}/resume",
        json={"expected_revision": claim.plan_revision},
    )

    assert resumed.status_code == 409, resumed.text
    assert resumed.json()["detail"]["code"] == "authoring_inputs_stale"
    assert _operation_snapshot(operation_id) == before_resume


def test_feature_disable_inside_response_transaction_discards_late_output(
    client, seeded, monkeypatch,
):
    _, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)
    original_inputs = authoring_operations._current_operation_inputs

    def disable_after_inputs_are_checked(row):
        result = original_inputs(row)
        monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
        monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
        return result

    monkeypatch.setattr(
        authoring_operations,
        "_current_operation_inputs",
        disable_after_inputs_are_checked,
    )
    _persist_response_for_state_test(
        claim,
        ticket,
        [{"target": "take-001", "result": {"pose": "late output"}}],
    )

    row = _operation_snapshot(operation_id)
    assert not main.is_resource_planning_enabled()
    assert row["state"] == "cancelled"
    assert row["completed_json"] == "[]"
    assert row["result_json"] is None
    assert row["lease_expires_at"] is None


def test_effective_input_digest_survives_progress_failure_resume_and_cancel(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch,
        take_ids=["take-001", "take-002", "take-003"],
    )
    digest = _operation_snapshot(operation_id)["input_digest"]
    assert len(digest) == 64

    first_ticket = authoring_operations.renew_operation_lease(claim)
    _persist_response_for_state_test(
        claim,
        first_ticket,
        [{"target": "take-001", "result": {"pose": "standing near a window"}}],
    )
    assert _operation_snapshot(operation_id)["input_digest"] == digest

    failure_ticket = authoring_operations.renew_operation_lease(claim)
    authoring_operations.fail_operation_item(claim, failure_ticket, "take-002")
    assert _operation_snapshot(operation_id)["input_digest"] == digest

    operation_url = f"/api/sessions/{session_id}/plan/authoring/operations/{operation_id}"
    resumed = client.post(
        f"{operation_url}/resume",
        json={"expected_revision": claim.plan_revision},
    )
    assert resumed.status_code == 202, resumed.text
    assert _operation_snapshot(operation_id)["input_digest"] == digest

    resumed_claim = authoring_operations.load_worker_claim(session_id, operation_id)
    late_ticket = authoring_operations.renew_operation_lease(resumed_claim)
    cancel = client.post(
        f"{operation_url}/cancel",
        json={"expected_revision": claim.plan_revision},
    )
    assert cancel.status_code == 202, cancel.text
    assert _operation_snapshot(operation_id)["input_digest"] == digest

    with pytest.raises(authoring_operations.AuthoringOperationError):
        _persist_response_for_state_test(
            resumed_claim,
            late_ticket,
            [{"target": "take-002", "result": {"pose": "late output"}}],
        )
    after_cancel = _operation_snapshot(operation_id)
    assert after_cancel["state"] == "cancelled"
    assert after_cancel["input_digest"] == digest
    assert json.loads(after_cancel["completed_json"]) == ["take-001"]
    assert json.loads(after_cancel["remaining_json"]) == ["take-002", "take-003"]


def test_feature_disable_during_lease_renewal_prevents_remote_call_authority(
    client, seeded, monkeypatch,
):
    _, operation_id, claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    original_context = authoring_operations._current_operation_context

    def disable_after_context_validation(row):
        context = original_context(row)
        monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
        monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
        return context

    monkeypatch.setattr(
        authoring_operations,
        "_current_operation_context",
        disable_after_context_validation,
    )
    try:
        authoring_operations.renew_operation_lease(claim)
    except authoring_operations.AuthoringOperationError as exc:
        assert exc.code == "resource_planning_disabled"
    else:
        pytest.fail("lease renewal authorized another assistant call after feature disablement")

    row = _operation_snapshot(operation_id)
    assert not main.is_resource_planning_enabled()
    assert row["state"] == "cancelled"
    assert row["lease_expires_at"] is None
    assert row["completed_json"] == "[]"
    assert row["result_json"] is None
