"""Independent acceptance for Full Auto preparation orchestration."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
import json
import uuid

import httpx
import pytest

import db
import main
from backend import authoring_operations, resource_full_auto, resource_preparation, session_plan
import test_authoring_operation_api as manual_operations
import test_task31_acceptance as task31


def _configure_provider(monkeypatch) -> None:
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "invented-test-model")
    monkeypatch.setitem(main.CONFIG, "llm_key", "")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)


def _create_run(client, seeded, monkeypatch, *, photo_count: int) -> tuple[int, str, list[str]]:
    _configure_provider(monkeypatch)
    task31._install_t2i_workflow(client, seeded)
    _saved, _profile, body = task31._full_auto_fixture(
        seeded, photo_count=photo_count,
    )
    consent = task31._preflight(client, body)
    created, _payload = task31._create_from_preflight(client, body, consent)
    assert created.status_code == 201, created.text
    result = created.json()
    session_id = result["session_id"]
    plan = result["plan"]
    return session_id, result["full_auto_run_id"], [take["take_id"] for take in plan["takes"]]


class _StructuredAssistant:
    """Local HTTPX transport; production request construction and JSON parsing stay live."""

    def __init__(self, fail_at: str | None = None):
        self.fail_at = fail_at
        self.calls: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        user = next(message["content"] for message in body["messages"] if message["role"] == "user")
        prefix, separator, serialized = user.partition("Context: ")
        assert separator and prefix
        context = json.loads(serialized)
        take_id = context["take_id"]
        self.calls.append({
            "take_id": take_id,
            "transaction_open": db.conn().in_transaction,
            "body": body,
        })
        content = "not valid structured JSON" if take_id == self.fail_at else json.dumps({
            field: f"Invented {field} choice for {take_id}"
            for field in context["preparation"]["requested_fields"]
        })
        return httpx.Response(200, json={
            "choices": [{"message": {"content": content}}],
        }, request=request)


def _install_transport(
    monkeypatch,
    assistant: _StructuredAssistant,
    *,
    after_response=None,
) -> None:
    real_async_client = main.enhance.httpx.AsyncClient

    def respond(request):
        response = assistant(request)
        if after_response is not None:
            after_response(request, response)
        return response

    def local_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(respond)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", local_client)


def _start_owned_child(session_id, run_id, target_ids):
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "kind": "prepare_takes",
        "take_ids": target_ids,
    })
    child, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    return owner, child


def _drift_bound_workflow(session_id: int) -> None:
    session = db.one("SELECT model_id FROM session WHERE id = ?", session_id)
    model = db.one("SELECT workflow_id FROM model WHERE id = ?", session["model_id"])
    workflow = db.one("SELECT graph FROM workflow WHERE id = ?", model["workflow_id"])
    graph = json.loads(workflow["graph"])
    graph["independent-inflight-drift"] = {"class_type": "InventedDriftProbe"}
    db.run(
        "UPDATE workflow SET graph = ? WHERE id = ?",
        json.dumps(graph, ensure_ascii=False, separators=(",", ":")),
        model["workflow_id"],
    )


def _forbid_downstream_generation(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        pytest.fail("preparation must stop before approval, submission, or image generation")

    monkeypatch.setattr(main.session_plan, "approve_plan_review", unexpected)
    monkeypatch.setattr(main.session_plan, "submit_prepared_take", unexpected)
    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)


def _batch_targets(run_id: str) -> list[list[str]]:
    return [
        json.loads(row["target_ids_json"])
        for row in db.q(
            "SELECT target_ids_json FROM full_auto_prepare_child "
            "WHERE run_id = ? ORDER BY batch_index",
            run_id,
        )
    ]


def _prepared_rows(session_id: int, plan_revision: int = 1) -> list[dict]:
    return db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? ORDER BY take_id",
        session_id, plan_revision,
    )


def _assert_ready_snapshots(
    session_id: int,
    target_ids: list[str],
    *,
    allow_other_rows: bool = False,
) -> list[dict]:
    rows = _prepared_rows(session_id)
    if allow_other_rows:
        target_set = set(target_ids)
        rows = [row for row in rows if row["take_id"] in target_set]
    assert [row["take_id"] for row in rows] == target_ids
    for row in rows:
        assert row["status"] == session_plan.PREPARED_TAKE_STATUS_READY
        evidence = json.loads(row["provenance"])["authoring_evidence"]
        synthesis = evidence["writer_synthesis"]
        assert synthesis["kind"] == "assistant"
        assert synthesis["writer_input"]["assistant_request"]["model"] == "invented-test-model"
        resource_preparation.validate_authoring_prepared_evidence(
            session_id, row["plan_revision"], row["take_id"], row=row,
        )
    return rows


def _assert_no_submission(session_id: int) -> None:
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 0
    assert db.one(
        "SELECT COUNT(*) AS n FROM session_plan_approval WHERE session_id = ?", session_id,
    )["n"] == 0


def _resume_eligibility(plan_revision: int) -> resource_full_auto.ResumeEligibility:
    return resource_full_auto.ResumeEligibility(
        current_plan_revision=plan_revision,
        unresolved_work=0,
        authorization_valid=True,
        policy_supported=True,
        budget_available=True,
        session_exclusive=True,
        scope_matches=True,
        planning_enabled=True,
        next_state="active",
    )


def _prepare_owned_child_without_checkpoint(
    session_id: int,
    run_id: str,
    target_ids: list[str],
) -> tuple[resource_full_auto.RunClaim, dict]:
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "kind": "prepare_takes",
        "take_ids": target_ids,
    })
    child, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    owner = asyncio.run(main._run_prepare_takes_operation(
        session_id, child["operation_id"], _full_auto_claim=owner,
    )) or owner
    saved_child = authoring_operations.get_operation(
        session_id,
        child["operation_id"],
        planning_enabled=True,
        assistant_available=True,
    )
    assert saved_child["state"] == "succeeded"
    assert saved_child["progress"]["completed"] == target_ids
    assert resource_full_auto._read_prepare_progress(run_id)["completed_take_ids"] == []
    return owner, child


def test_forty_takes_chain_two_owned_children_through_the_real_writer_and_finalizer(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=40,
    )
    assistant = _StructuredAssistant()
    _install_transport(monkeypatch, assistant)
    _forbid_downstream_generation(monkeypatch)

    owner = resource_full_auto.claim_run(run_id)
    result = asyncio.run(main._run_full_auto_preparation(owner))

    expected_batches = [target_ids[:20], target_ids[20:]]
    assert result["state"] == "active"
    assert result["phase"] == "validate"
    assert _batch_targets(run_id) == expected_batches
    children = db.q(
        "SELECT child.target_ids_json, operation.* "
        "FROM full_auto_prepare_child AS child "
        "JOIN authoring_operation AS operation ON operation.operation_id = child.operation_id "
        "WHERE child.run_id = ? ORDER BY child.batch_index",
        run_id,
    )
    assert len(children) == 2
    for child in children:
        assert child["state"] == "succeeded"
        assert child["full_auto_run_id"] == owner.run_id
        assert child["full_auto_run_epoch"] == owner.epoch
        assert child["full_auto_run_fencing_token"] == owner.fencing_token
    progress = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    assert json.loads(progress["target_ids_json"]) == target_ids
    assert json.loads(progress["completed_ids_json"]) == target_ids
    _assert_ready_snapshots(session_id, target_ids)
    assert [call["take_id"] for call in assistant.calls] == target_ids
    assert all(call["transaction_open"] is False for call in assistant.calls)
    assert all(
        call["body"]["response_format"] == {"type": "json_object"}
        for call in assistant.calls
    )
    _assert_no_submission(session_id)


def test_five_hundred_take_checkpoint_and_first_child_are_not_truncated(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=500,
    )
    assert target_ids == [f"take-{index:03d}" for index in range(1, 501)]
    assistant = _StructuredAssistant(fail_at=target_ids[0])
    _install_transport(monkeypatch, assistant)
    _forbid_downstream_generation(monkeypatch)

    owner = resource_full_auto.claim_run(run_id)
    result = asyncio.run(main._run_full_auto_preparation(owner))

    progress = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    assert json.loads(progress["target_ids_json"]) == target_ids
    assert json.loads(progress["completed_ids_json"]) == []
    assert _batch_targets(run_id) == [target_ids[:20]]
    assert result["state"] == "blocked"
    operation = db.one(
        "SELECT operation.* FROM authoring_operation AS operation "
        "JOIN full_auto_prepare_child AS child ON child.operation_id = operation.operation_id "
        "WHERE child.run_id = ?",
        run_id,
    )
    assert operation["state"] == "failed"
    assert operation["failed_item"] == target_ids[0]
    assert json.loads(operation["requested_json"]) == target_ids[:20]
    assert [call["take_id"] for call in assistant.calls] == [target_ids[0]]
    assert assistant.calls[0]["transaction_open"] is False
    _assert_no_submission(session_id)


def test_take_twenty_three_failure_preserves_twenty_two_and_resume_reuses_them(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=40,
    )
    assistant = _StructuredAssistant(fail_at=target_ids[22])
    _install_transport(monkeypatch, assistant)
    _forbid_downstream_generation(monkeypatch)

    owner = resource_full_auto.claim_run(run_id)
    result = asyncio.run(main._run_full_auto_preparation(owner))

    assert result["state"] == "blocked"
    assert result["blocker_code"] == "preparation_failed"
    assert [call["take_id"] for call in assistant.calls] == target_ids[:23]
    progress = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    assert json.loads(progress["completed_ids_json"]) == target_ids[:22]
    assert json.loads(progress["target_ids_json"]) == target_ids
    child_rows = db.q(
        "SELECT operation.* FROM full_auto_prepare_child AS child "
        "JOIN authoring_operation AS operation ON operation.operation_id = child.operation_id "
        "WHERE child.run_id = ? ORDER BY child.batch_index",
        run_id,
    )
    assert [json.loads(row["requested_json"]) for row in child_rows] == [
        target_ids[:20], target_ids[20:],
    ]
    assert child_rows[0]["state"] == "succeeded"
    assert child_rows[1]["state"] == "failed"
    assert child_rows[1]["failed_item"] == target_ids[22]
    assert json.loads(child_rows[1]["completed_json"]) == target_ids[20:22]
    assert json.loads(child_rows[1]["remaining_json"]) == target_ids[23:]
    completed_before_resume = _assert_ready_snapshots(session_id, target_ids[:22])
    _assert_no_submission(session_id)

    saved_run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    resumed = resource_full_auto.resume_run(
        run_id,
        str(uuid.uuid4()),
        saved_run["revision"],
        saved_run["plan_revision"],
        eligibility=_resume_eligibility(saved_run["plan_revision"]),
    )
    assert resumed.claim is not None
    assistant.fail_at = None
    resumed_call_index = len(assistant.calls)
    result = asyncio.run(main._run_full_auto_preparation(resumed.claim))

    assert result["state"] == "active"
    assert result["phase"] == "validate"
    assert [call["take_id"] for call in assistant.calls[resumed_call_index:]] == target_ids[22:]
    assert _batch_targets(run_id)[-1] == target_ids[22:]
    assert _prepared_rows(session_id)[:22] == completed_before_resume
    _assert_ready_snapshots(session_id, target_ids)
    assert all(call["transaction_open"] is False for call in assistant.calls)
    _assert_no_submission(session_id)


def test_twenty_existing_owned_snapshots_start_at_remaining_batch_and_checkpoint_is_verified(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=40,
    )
    assistant = _StructuredAssistant()
    _install_transport(monkeypatch, assistant)
    _forbid_downstream_generation(monkeypatch)
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)

    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "kind": "prepare_takes",
        "take_ids": target_ids[:20],
    })
    child, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    operation = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )
    assert (operation["full_auto_run_id"], operation["full_auto_run_epoch"],
            operation["full_auto_run_fencing_token"]) == (
        owner.run_id, owner.epoch, owner.fencing_token,
    )

    manual_payload = {
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "kind": "prepare_takes",
        "take_ids": target_ids[:20],
    }
    manual_request = authoring_operations.normalize_start_request(manual_payload)
    operation_count = db.one(
        "SELECT COUNT(*) AS n FROM authoring_operation WHERE session_id = ?", session_id,
    )["n"]
    with pytest.raises(authoring_operations.AuthoringOperationError) as manual_error:
        authoring_operations.start_operation(
            session_id,
            manual_request,
            planning_enabled=True,
            assistant_available=True,
        )
    assert manual_error.value.code == "authoring_active"
    assert db.one(
        "SELECT COUNT(*) AS n FROM authoring_operation WHERE session_id = ?", session_id,
    )["n"] == operation_count

    with pytest.raises(authoring_operations.AuthoringOperationError) as missing_owner_error:
        authoring_operations.load_worker_claim(session_id, child["operation_id"])
    assert missing_owner_error.value.code == "full_auto_owner_required"

    forged_payload = {
        **manual_payload,
        "full_auto_claim": {
            "run_id": owner.run_id,
            "epoch": owner.epoch,
            "fencing_token": owner.fencing_token,
        },
    }
    route_before = db.one(
        "SELECT COUNT(*) AS n FROM authoring_operation WHERE session_id = ?", session_id,
    )["n"]
    forged_response = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=forged_payload,
    )
    assert forged_response.status_code == 422, forged_response.text
    assert db.one(
        "SELECT COUNT(*) AS n FROM authoring_operation WHERE session_id = ?", session_id,
    )["n"] == route_before

    forged_owner = replace(owner, fencing_token=owner.fencing_token + 1)
    before_forged_owner = _prepared_rows(session_id)
    with pytest.raises(resource_full_auto.FullAutoRunError) as forged_error:
        asyncio.run(main._run_full_auto_preparation(forged_owner))
    assert forged_error.value.code == "run_owner_stale"
    with pytest.raises(authoring_operations.AuthoringOperationError) as stale_worker_error:
        authoring_operations.load_worker_claim(
            session_id, child["operation_id"], _full_auto_claim=forged_owner,
        )
    assert stale_worker_error.value.code == "full_auto_owner_stale"
    assert _prepared_rows(session_id) == before_forged_owner
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_prepare_child WHERE run_id = ?", run_id,
    )["n"] == 1

    before_forged_checkpoint = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError):
        resource_full_auto.checkpoint_prepare_child(owner, child["operation_id"], target_ids[:20])
    assert db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    ) == before_forged_checkpoint

    owner = asyncio.run(main._run_prepare_takes_operation(
        session_id, child["operation_id"], _full_auto_claim=owner,
    )) or owner
    saved_child = authoring_operations.get_operation(
        session_id,
        child["operation_id"],
        planning_enabled=True,
        assistant_available=True,
    )
    assert saved_child["state"] == "succeeded"
    before_partial_checkpoint = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError):
        resource_full_auto.checkpoint_prepare_child(
            owner, child["operation_id"], target_ids[:19],
        )
    assert db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    ) == before_partial_checkpoint
    resource_full_auto.checkpoint_prepare_child(
        owner, child["operation_id"], saved_child["progress"]["completed"],
    )
    completed_before_next_batch = _prepared_rows(session_id)
    calls_before_next_batch = len(assistant.calls)

    result = asyncio.run(main._run_full_auto_preparation(owner))

    assert result["phase"] == "validate"
    assert _batch_targets(run_id) == [target_ids[:20], target_ids[20:]]
    assert [call["take_id"] for call in assistant.calls[calls_before_next_batch:]] == target_ids[20:]
    assert _prepared_rows(session_id)[:20] == completed_before_next_batch
    assert len(_prepared_rows(session_id)) == len(target_ids)
    assert _prepared_rows(session_id)[20:] == _assert_ready_snapshots(
        session_id, target_ids[20:], allow_other_rows=True,
    )
    assert all(call["transaction_open"] is False for call in assistant.calls)
    _assert_no_submission(session_id)


def test_generated_linked_snapshot_is_reused_without_reauthoring_or_resubmitting(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    assistant = _StructuredAssistant()
    _install_transport(monkeypatch, assistant)
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": 1,
        "kind": "prepare_takes",
        "take_ids": target_ids,
    })
    child, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    owner = asyncio.run(main._run_prepare_takes_operation(
        session_id, child["operation_id"], _full_auto_claim=owner,
    )) or owner
    saved_child = authoring_operations.get_operation(
        session_id,
        child["operation_id"],
        planning_enabled=True,
        assistant_available=True,
    )
    assert saved_child["state"] == "succeeded"
    assert saved_child["progress"]["completed"] == target_ids
    resource_full_auto.checkpoint_prepare_child(owner, child["operation_id"])

    review = session_plan.approve_plan_review(session_id, 1)
    assert review["approved"] is True
    submitted = session_plan.submit_prepared_take(session_id, 1, target_ids[0])
    assert type(submitted["shot_id"]) is int
    linked_before = _prepared_rows(session_id)
    assert [row["take_id"] for row in linked_before] == target_ids
    assert linked_before[0]["status"] == session_plan.PREPARED_TAKE_STATUS_GENERATED
    assert linked_before[0]["linked_shot_id"] == submitted["shot_id"]
    resource_preparation.validate_authoring_prepared_evidence(
        session_id, 1, target_ids[0], row=linked_before[0],
    )
    shots_before = db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id)
    approvals_before = db.q(
        "SELECT * FROM session_plan_approval WHERE session_id = ?", session_id,
    )
    children_before = db.q(
        "SELECT * FROM full_auto_prepare_child WHERE run_id = ? ORDER BY batch_index",
        run_id,
    )
    calls_before_coordinator = len(assistant.calls)
    _forbid_downstream_generation(monkeypatch)

    result = asyncio.run(main._run_full_auto_preparation(owner))

    assert result["state"] == "active"
    assert result["phase"] == "validate"
    assert len(assistant.calls) == calls_before_coordinator
    assert _prepared_rows(session_id) == linked_before
    assert db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id) == shots_before
    assert db.q(
        "SELECT * FROM session_plan_approval WHERE session_id = ?", session_id,
    ) == approvals_before
    assert db.q(
        "SELECT * FROM full_auto_prepare_child WHERE run_id = ? ORDER BY batch_index",
        run_id,
    ) == children_before


def test_prepared_snapshot_corrupted_after_validation_is_not_adopted_at_checkpoint_commit(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    assistant = _StructuredAssistant()
    _install_transport(monkeypatch, assistant)
    owner, child = _prepare_owned_child_without_checkpoint(
        session_id, run_id, target_ids,
    )
    original_rows = _prepared_rows(session_id)
    assert [row["take_id"] for row in original_rows] == target_ids
    assert all(row["status"] == session_plan.PREPARED_TAKE_STATUS_READY for row in original_rows)
    assert _batch_targets(run_id) == [target_ids]
    validations = set()
    interleaving = {"injected": False}
    real_validator = resource_preparation.validate_authoring_prepared_evidence
    real_transaction = db.transaction

    def record_validated_evidence(*args, **kwargs):
        result = real_validator(*args, **kwargs)
        validations.add(args[2])
        return result

    def corrupt_after_first_pass():
        if (
            not interleaving["injected"]
            and set(target_ids).issubset(validations)
            and not db.conn().in_transaction
        ):
            interleaving["injected"] = True
            row = next(item for item in original_rows if item["take_id"] == target_ids[0])
            db.run(
                "UPDATE prepared_take SET final_prompt = ? WHERE id = ?",
                row["final_prompt"] + "\ncorrupted after evidence validation",
                row["id"],
            )
        return real_transaction()

    monkeypatch.setattr(
        resource_preparation, "validate_authoring_prepared_evidence", record_validated_evidence,
    )
    monkeypatch.setattr(db, "transaction", corrupt_after_first_pass)
    with pytest.raises(resource_full_auto.FullAutoRunError) as stale_evidence:
        resource_full_auto.initialize_prepare_progress(owner)

    assert stale_evidence.value.code == "prepared_evidence_stale"
    assert interleaving["injected"] is True
    progress = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    assert json.loads(progress["completed_ids_json"]) == []
    assert db.one("SELECT phase FROM full_auto_run WHERE run_id = ?", run_id)["phase"] == "prepare"
    corrupted = session_plan._prepared_take_row(session_id, 1, target_ids[0])
    with pytest.raises(session_plan.AuthoringEvidenceInvalid):
        resource_preparation.validate_authoring_prepared_evidence(
            session_id, 1, target_ids[0], row=corrupted,
        )
    assert len(assistant.calls) == len(target_ids)
    _assert_no_submission(session_id)


def test_expired_lease_during_snapshot_validation_cannot_commit_progress(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    assistant = _StructuredAssistant()
    _install_transport(monkeypatch, assistant)
    owner, _child = _prepare_owned_child_without_checkpoint(
        session_id, run_id, target_ids,
    )
    original_rows = _prepared_rows(session_id)
    clock = {"now": owner.lease_started_at, "advanced": False}
    expired_time = (
        datetime.fromisoformat(owner.lease_expires_at) + timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    real_validator = resource_preparation.validate_authoring_prepared_evidence
    monkeypatch.setattr(db, "now", lambda: clock["now"])

    def expire_after_slow_validation(*args, **kwargs):
        result = real_validator(*args, **kwargs)
        if args[2] == target_ids[-1] and not clock["advanced"]:
            clock["now"] = expired_time
            clock["advanced"] = True
        return result

    monkeypatch.setattr(
        resource_preparation, "validate_authoring_prepared_evidence", expire_after_slow_validation,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as expired_owner:
        resource_full_auto.initialize_prepare_progress(owner)

    assert expired_owner.value.code == "run_lease_expired"
    assert clock["advanced"] is True
    progress = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    assert json.loads(progress["completed_ids_json"]) == []
    assert _prepared_rows(session_id) == original_rows
    assert db.one("SELECT phase FROM full_auto_run WHERE run_id = ?", run_id)["phase"] == "prepare"
    assert len(assistant.calls) == len(target_ids)
    _assert_no_submission(session_id)


@pytest.mark.parametrize("advance_point", ["transaction_entry", "progress_read"])
def test_checkpoint_rechecks_parent_lease_after_transaction_wait_and_before_write(
    client, seeded, monkeypatch, advance_point,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    assistant = _StructuredAssistant()
    _install_transport(monkeypatch, assistant)
    owner, child = _prepare_owned_child_without_checkpoint(
        session_id, run_id, target_ids,
    )
    owner = resource_full_auto.renew_run_claim(owner)
    before = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    clock = {"now": owner.lease_started_at, "advanced": False}
    expired_time = (
        datetime.fromisoformat(owner.lease_expires_at) + timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    monkeypatch.setattr(db, "now", lambda: clock["now"])

    if advance_point == "transaction_entry":
        real_transaction = db.transaction

        @contextmanager
        def expire_after_write_lock():
            with real_transaction():
                clock["now"] = expired_time
                clock["advanced"] = True
                yield

        monkeypatch.setattr(db, "transaction", expire_after_write_lock)
    else:
        real_read_progress = resource_full_auto._read_prepare_progress

        def expire_after_progress_read(requested_run_id):
            result = real_read_progress(requested_run_id)
            if db._tx_depth and not clock["advanced"]:
                clock["now"] = expired_time
                clock["advanced"] = True
            return result

        monkeypatch.setattr(
            resource_full_auto, "_read_prepare_progress", expire_after_progress_read,
        )

    with pytest.raises(resource_full_auto.FullAutoRunError) as expired_owner:
        resource_full_auto.checkpoint_prepare_child(owner, child["operation_id"])

    assert expired_owner.value.code == "run_lease_expired"
    assert clock["advanced"] is True
    assert db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    ) == before
    assert db.one("SELECT phase FROM full_auto_run WHERE run_id = ?", run_id)["phase"] == "prepare"
    assert len(assistant.calls) == len(target_ids)
    _assert_no_submission(session_id)


def test_checkpoint_rejects_child_snapshot_corruption_before_advancing_progress(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    assistant = _StructuredAssistant()
    _install_transport(monkeypatch, assistant)
    owner, child = _prepare_owned_child_without_checkpoint(
        session_id, run_id, target_ids,
    )
    owner = resource_full_auto.renew_run_claim(owner)
    rows_before = _prepared_rows(session_id)
    assert [row["take_id"] for row in rows_before] == target_ids
    before = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    target_row = rows_before[0]
    db.run(
        "UPDATE prepared_take SET final_prompt = ? WHERE id = ?",
        target_row["final_prompt"] + "\ncorrupted after child finalization",
        target_row["id"],
    )
    validation_depths: list[int] = []
    real_validator = resource_preparation.validate_authoring_prepared_evidence

    def record_checkpoint_validation(*args, **kwargs):
        validation_depths.append(db._tx_depth)
        return real_validator(*args, **kwargs)

    monkeypatch.setattr(
        resource_preparation, "validate_authoring_prepared_evidence", record_checkpoint_validation,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as stale_evidence:
        resource_full_auto.checkpoint_prepare_child(owner, child["operation_id"])

    assert stale_evidence.value.code == "prepared_evidence_stale"
    assert validation_depths and all(depth == 1 for depth in validation_depths)
    assert db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    ) == before
    assert db.one("SELECT phase FROM full_auto_run WHERE run_id = ?", run_id)["phase"] == "prepare"
    assert _prepared_rows(session_id) != rows_before
    assert authoring_operations._get_operation(child["operation_id"])["state"] == "succeeded"
    assert len(assistant.calls) == len(target_ids)
    _assert_no_submission(session_id)


def test_batch_preparation_matches_scalar_and_reloads_shared_dependencies_per_call(
    client, seeded, monkeypatch,
):
    session_id, _run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    shared_loaders = {
        "workflow": (resource_preparation.workflow_binding,
                     "validate_workflow_binding_against_session"),
        "plan": (session_plan, "_load_current_resource_plan"),
        "resources": (resource_preparation, "_prepare_selected_resource_inputs"),
        "wardrobes": (session_plan, "resolve_effective_wardrobes"),
        "profile": (session_plan, "_build_session_take_profile_coverage_context"),
        "model": (resource_preparation, "_load_model_for_session"),
    }
    counts = {name: 0 for name in shared_loaders}
    observed_models = []
    for name, (module, attribute) in shared_loaders.items():
        original = getattr(module, attribute)

        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            result = _original(*args, **kwargs)
            if _name == "model":
                observed_models.append(result)
            return result

        monkeypatch.setattr(module, attribute, counted)

    first_batch = resource_preparation.prepare_take_inputs_batch(
        session_id, 1, target_ids,
    )
    assert counts == {name: 1 for name in shared_loaders}
    assert first_batch == [
        resource_preparation.prepare_take_inputs(session_id, 1, take_id)
        for take_id in target_ids
    ]

    before_second_call = dict(counts)
    model_id = db.one("SELECT model_id FROM session WHERE id = ?", session_id)["model_id"]
    db.run(
        "UPDATE model SET trigger = ? WHERE id = ?",
        "fresh-batch-trigger",
        model_id,
    )
    second_batch = resource_preparation.prepare_take_inputs_batch(
        session_id, 1, target_ids,
    )
    assert {name: counts[name] - before_second_call[name] for name in counts} == {
        name: 1 for name in shared_loaders
    }
    assert observed_models[-1]["trigger"] == "fresh-batch-trigger"
    assert second_batch == [
        resource_preparation.prepare_take_inputs(session_id, 1, take_id)
        for take_id in target_ids
    ]


def test_operation_context_builds_one_shared_batch_for_its_requested_takes(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    owner, child = _start_owned_child(session_id, run_id, target_ids)
    assert owner.run_id == run_id
    row = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )
    shared_loaders = {
        "workflow": (resource_preparation.workflow_binding,
                     "validate_workflow_binding_against_session"),
        "resources": (resource_preparation, "_prepare_selected_resource_inputs"),
        "wardrobes": (session_plan, "resolve_effective_wardrobes"),
        "profile": (session_plan, "_build_session_take_profile_coverage_context"),
        "model": (resource_preparation, "_load_model_for_session"),
    }
    counts = {name: 0 for name in shared_loaders}
    for name, (module, attribute) in shared_loaders.items():
        original = getattr(module, attribute)

        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, attribute, counted)

    context = authoring_operations._current_operation_context(row)

    assert context[1] == target_ids
    assert counts == {name: 1 for name in shared_loaders}


def test_batch_preparation_rejects_stale_binding_before_resolving_batch_inputs(
    client, seeded, monkeypatch,
):
    session_id, _run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    original = resource_preparation.prepare_take_inputs_batch(
        session_id, 1, target_ids[:2],
    )
    session = db.one("SELECT model_id FROM session WHERE id = ?", session_id)
    model = db.one("SELECT workflow_id FROM model WHERE id = ?", session["model_id"])
    workflow = db.one("SELECT graph FROM workflow WHERE id = ?", model["workflow_id"])
    graph = json.loads(workflow["graph"])
    graph["independent-drift-probe"] = {"class_type": "InventedDriftProbe"}
    db.run(
        "UPDATE workflow SET graph = ? WHERE id = ?",
        json.dumps(graph, ensure_ascii=False, separators=(",", ":")),
        model["workflow_id"],
    )

    resolved_resources = 0
    real_resource_resolver = resource_preparation._prepare_selected_resource_inputs

    def count_resource_resolution(*args, **kwargs):
        nonlocal resolved_resources
        resolved_resources += 1
        return real_resource_resolver(*args, **kwargs)

    monkeypatch.setattr(
        resource_preparation, "_prepare_selected_resource_inputs", count_resource_resolution,
    )
    with pytest.raises(resource_preparation.workflow_binding.WorkflowChanged):
        resource_preparation.prepare_take_inputs_batch(session_id, 1, target_ids[:2])

    assert resolved_resources == 0
    assert original[0]["take_id"] == target_ids[0]
    assert db.one(
        "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", session_id,
    )["n"] == 0
    _assert_no_submission(session_id)


def test_owned_worker_refuses_binding_drift_before_assistant_dispatch(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    assistant = _StructuredAssistant()
    _install_transport(monkeypatch, assistant)
    owner, child = _start_owned_child(session_id, run_id, target_ids)
    _drift_bound_workflow(session_id)

    asyncio.run(main._run_prepare_takes_operation(
        session_id, child["operation_id"], _full_auto_claim=owner,
    ))

    operation = db.one(
        "SELECT state, completed_json, remaining_json FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )
    progress = resource_full_auto._read_prepare_progress(run_id)
    assert assistant.calls == []
    assert operation["state"] == "active"
    assert json.loads(operation["completed_json"]) == []
    assert json.loads(operation["remaining_json"]) == target_ids
    assert progress["completed_take_ids"] == []
    assert _prepared_rows(session_id) == []
    _assert_no_submission(session_id)


def test_owned_worker_rejects_assistant_response_after_inflight_binding_drift(
    client, seeded, monkeypatch,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    assistant = _StructuredAssistant()
    injected = {"done": False}

    def change_binding_after_response(_request, _response):
        if not injected["done"]:
            _drift_bound_workflow(session_id)
            injected["done"] = True

    _install_transport(monkeypatch, assistant, after_response=change_binding_after_response)
    owner, child = _start_owned_child(session_id, run_id, target_ids)

    asyncio.run(main._run_prepare_takes_operation(
        session_id, child["operation_id"], _full_auto_claim=owner,
    ))

    operation = db.one(
        "SELECT state, completed_json, remaining_json, result_json FROM authoring_operation "
        "WHERE operation_id = ?",
        child["operation_id"],
    )
    progress = resource_full_auto._read_prepare_progress(run_id)
    assert injected["done"] is True
    assert [call["take_id"] for call in assistant.calls] == [target_ids[0]]
    assert assistant.calls[0]["transaction_open"] is False
    assert operation["state"] == "active"
    assert json.loads(operation["completed_json"]) == []
    assert json.loads(operation["remaining_json"]) == target_ids
    assert operation["result_json"] in (None, "[]", "{\"items\":[]}")
    assert progress["completed_take_ids"] == []
    assert _prepared_rows(session_id) == []
    _assert_no_submission(session_id)


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    [
        ("signature", "invalid_request"),
        ("progress", "authoring_inputs_stale"),
        ("lease", "authoring_lease_expired"),
    ],
)
def test_after_io_lease_renewal_rejects_forged_stale_progress_and_expired_tickets(
    client, seeded, monkeypatch, tamper, expected_code,
):
    session_id, run_id, target_ids = _create_run(
        client, seeded, monkeypatch, photo_count=4,
    )
    owner, child = _start_owned_child(session_id, run_id, target_ids)
    claim = authoring_operations.load_worker_claim(
        session_id, child["operation_id"], _full_auto_claim=owner,
    )
    if tamper == "lease":
        def one_second_lease(started_at=None):
            start = started_at or datetime.now().astimezone()
            return (start + timedelta(seconds=1)).isoformat(timespec="seconds")

        monkeypatch.setattr(db, "authoring_operation_lease_deadline", one_second_lease)
    ticket = authoring_operations.renew_operation_lease(
        claim, planning_enabled=True, _full_auto_claim=owner,
    )
    before = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )

    candidate = ticket
    if tamper == "signature":
        candidate = replace(ticket, progress_fingerprint="0" * 64)
    elif tamper == "progress":
        db.run(
            "UPDATE authoring_operation SET completed_json = ?, remaining_json = ? "
            "WHERE operation_id = ?",
            json.dumps([target_ids[0]]),
            json.dumps(target_ids[1:]),
            child["operation_id"],
        )
        before = db.one(
            "SELECT * FROM authoring_operation WHERE operation_id = ?",
            child["operation_id"],
        )
    else:
        late = (
            datetime.fromisoformat(ticket.lease_expires_at.replace("Z", "+00:00"))
            + timedelta(seconds=1)
        ).isoformat(timespec="seconds")
        owner = resource_full_auto.renew_run_claim(owner, now_text=late)
        monkeypatch.setattr(db, "now", lambda: late)

    with pytest.raises(authoring_operations.AuthoringOperationError) as rejected:
        authoring_operations.renew_operation_lease_after_io(
            claim, candidate, planning_enabled=True, _full_auto_claim=owner,
        )

    assert rejected.value.code == expected_code
    after = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )
    assert after["state"] == "active"
    assert after["lease_expires_at"] == before["lease_expires_at"] == ticket.lease_expires_at
    assert after["completed_json"] == before["completed_json"]
    assert after["remaining_json"] == before["remaining_json"]
    if tamper == "progress":
        assert json.loads(after["completed_json"]) == target_ids[:1]
        assert json.loads(after["remaining_json"]) == target_ids[1:]
    else:
        assert json.loads(after["completed_json"]) == []
        assert json.loads(after["remaining_json"]) == target_ids
    assert db.one(
        "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", session_id,
    )["n"] == 0
    _assert_no_submission(session_id)


def test_manual_prepare_worker_keeps_ordinary_lease_path_without_full_auto_owner(
    client, seeded, monkeypatch,
):
    _configure_provider(monkeypatch)
    session_id, revision = manual_operations._create_guided_session(
        client, seeded, photo_count=4,
    )
    plan = session_plan.get_draft(session_id)["plan"]
    target_ids = [take["take_id"] for take in plan["takes"]]
    assistant = _StructuredAssistant()
    _install_transport(monkeypatch, assistant)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": target_ids,
    })
    child, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
    )
    assert created is True
    operation = db.one(
        "SELECT * FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )
    assert operation["full_auto_run_id"] is None

    claim = authoring_operations.load_worker_claim(session_id, child["operation_id"])
    ticket = authoring_operations.renew_operation_lease(claim, planning_enabled=True)
    with pytest.raises(authoring_operations.AuthoringOperationError) as no_auto_owner:
        authoring_operations.renew_operation_lease_after_io(
            claim, ticket, planning_enabled=True,
        )
    assert no_auto_owner.value.code == "full_auto_owner_required"

    asyncio.run(main._run_prepare_takes_operation(session_id, child["operation_id"]))

    saved = authoring_operations.get_operation(
        session_id,
        child["operation_id"],
        planning_enabled=True,
        assistant_available=True,
    )
    assert saved["state"] == "succeeded"
    assert saved["progress"]["completed"] == target_ids
    assert [call["take_id"] for call in assistant.calls] == target_ids
    rows = _prepared_rows(session_id, revision)
    assert [row["take_id"] for row in rows] == target_ids
    assert all(row["status"] == session_plan.PREPARED_TAKE_STATUS_READY for row in rows)
    assert db.one(
        "SELECT full_auto_run_id FROM authoring_operation WHERE operation_id = ?",
        child["operation_id"],
    )["full_auto_run_id"] is None
    _assert_no_submission(session_id)
