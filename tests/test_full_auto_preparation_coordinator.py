"""Task 4.2 acceptance for the internal Full Auto preparation coordinator."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
import uuid

import httpx
import pytest

import db
import main
from backend import (
    authoring_operations,
    resource_full_auto,
    resource_preparation,
    session_plan,
)
from test_task31_acceptance import (
    _create_from_preflight,
    _full_auto_fixture,
    _install_t2i_workflow,
    _preflight,
)

_ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "task42-test-provider")
    monkeypatch.setitem(main.CONFIG, "llm_key", "task42-test-secret")


def _create_run(client, seeded, *, photo_count: int):
    _install_t2i_workflow(client, seeded)
    _saved, _published, base = _full_auto_fixture(
        seeded, photo_count=photo_count,
    )
    consent = _preflight(client, base)
    created, _payload = _create_from_preflight(client, base, consent)
    assert created.status_code == 201, created.text
    result = created.json()
    row = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        result["session_id"],
    )
    plan = json.loads(row["plan_json"])
    targets = [take["take_id"] for take in plan["takes"]]
    assert len(targets) == photo_count
    return result["session_id"], result["full_auto_run_id"], int(row["plan_revision"]), targets


def _response_for_request(request, pending, calls, transaction_depths, fields_by_take, fail_take):
    assert request.url.path.endswith("/chat/completions")
    assert pending, "the assistant received more requests than the plan has unlocked takes"
    take_id = pending.pop(0)
    calls.append(take_id)
    transaction_depths.append(db._tx_depth)
    if take_id == fail_take:
        return "not valid JSON"
    values = {
        field: f"{field} for {take_id}"
        for field in fields_by_take[take_id]
    }
    return json.dumps(values)


def _install_provider(monkeypatch, session_id, plan_revision, targets, *, fail_take=None):
    fields_by_take = {}
    for start in range(0, len(targets), 20):
        batch_targets = targets[start:start + 20]
        batch_inputs = resource_preparation.prepare_take_inputs_batch(
            session_id, plan_revision, batch_targets,
        )
        fields_by_take.update({
            take_id: resource_preparation.compute_unlocked_fields(batch_inputs[index])
            for index, take_id in enumerate(batch_targets)
        })
    dispatch_order = [take_id for take_id in targets if fields_by_take[take_id]]
    pending = list(dispatch_order)
    calls: list[str] = []
    transaction_depths: list[int] = []

    def respond(request):
        content = _response_for_request(
            request, pending, calls, transaction_depths, fields_by_take, fail_take,
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
        )

    transport = httpx.MockTransport(respond)
    class MockAsyncClient(_ORIGINAL_ASYNC_CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", MockAsyncClient)
    return {
        "calls": calls,
        "dispatch_order": dispatch_order,
        "pending": pending,
        "transaction_depths": transaction_depths,
    }


def _prepared_rows(session_id, plan_revision):
    return db.q(
        "SELECT id, take_id, status FROM prepared_take "
        "WHERE session_id = ? AND plan_revision = ? ORDER BY id",
        session_id, plan_revision,
    )


def _prepare_owned_batch(session_id, run_id, revision, targets, monkeypatch):
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    provider = _install_provider(monkeypatch, session_id, revision, targets)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": targets,
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
    assert authoring_operations.get_operation(
        session_id,
        child["operation_id"],
        planning_enabled=True,
        assistant_available=True,
    )["state"] == "succeeded"
    assert provider["calls"] == provider["dispatch_order"]
    return owner


@pytest.mark.parametrize(
    "photo_count",
    [
        pytest.param(40, id="real-40"),
        pytest.param(
            500,
            id="real-500-synthesis-benchmark",
            marks=pytest.mark.skipif(
                os.environ.get("IDEVGEN_FULL_AUTO_500_SYNTHESIS") != "1",
                reason=(
                    "The real 500-take synthesis benchmark is opt-in. Set "
                    "IDEVGEN_FULL_AUTO_500_SYNTHESIS=1 and run "
                    "python -m pytest tests/test_full_auto_preparation_coordinator.py "
                    "-k chains_bounded_children."
                ),
            ),
        ),
    ],
)
def test_full_auto_chains_bounded_children_through_all_targets_and_stops_at_validate(
    client, seeded, monkeypatch, photo_count,
):
    """The real 500-take synthesis case is an explicit opt-in benchmark."""
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=photo_count,
    )
    provider = _install_provider(monkeypatch, session_id, revision, targets)
    original_get_operation = authoring_operations.get_operation
    unowned_gets = []
    owned_gets = []

    def track_internal_operation_get(session_id, operation_id, **kwargs):
        claim = kwargs.get("_full_auto_claim")
        (owned_gets if claim is not None else unowned_gets).append(operation_id)
        return original_get_operation(session_id, operation_id, **kwargs)

    monkeypatch.setattr(
        authoring_operations, "get_operation", track_internal_operation_get,
    )

    result = asyncio.run(
        main._run_full_auto_preparation(resource_full_auto.claim_run(run_id)),
    )

    assert result["state"] == "active"
    assert result["phase"] == "validate"
    assert owned_gets
    assert not unowned_gets
    assert provider["calls"] == provider["dispatch_order"]
    assert not provider["pending"]
    assert provider["transaction_depths"] == [0] * len(provider["calls"])
    rows = _prepared_rows(session_id, revision)
    assert [row["take_id"] for row in rows] == targets
    assert all(row["status"] == "ready" for row in rows)
    assert db.one("SELECT 1 FROM shot WHERE session_id = ?", session_id) is None
    progress = resource_full_auto._read_prepare_progress(run_id)
    assert progress["completed_take_ids"] == targets
    assert len(progress["children"]) == (photo_count + 19) // 20
    for child in progress["children"]:
        assert 1 <= len(child["target_ids"]) <= 20
        operation = db.one(
            "SELECT full_auto_run_id, requested_json FROM authoring_operation WHERE operation_id = ?",
            child["operation_id"],
        )
        assert operation["full_auto_run_id"] == run_id
        assert len(json.loads(operation["requested_json"])) <= 20


def test_full_auto_500_traverses_twenty_five_owned_children_without_truncation(
    client, seeded, monkeypatch,
):
    """Exercise the real 500-target coordinator with an explicitly synthetic child producer."""
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=500,
    )
    assert not _prepared_rows(session_id, revision)

    synthetic_marker = "test_only_full_auto_500_orchestration"
    synthetic_operation_by_take: dict[str, str] = {}
    synthetic_snapshot_id_by_take: dict[str, int] = {}
    produced_children: list[tuple[str, list[str]]] = []
    observed_owner_gets: list[tuple[str, str, int, int]] = []
    checkpoint_prefixes: list[list[str]] = []
    renewed_claims: list[tuple[str, int, int]] = []
    validated_synthetic_takes: set[str] = set()

    parent_claim = resource_full_auto.claim_run(run_id)
    expected_owner = (parent_claim.run_id, parent_claim.epoch, parent_claim.fencing_token)

    def track_run_renewal(claim, *args, **kwargs):
        renewed_claims.append((claim.run_id, claim.epoch, claim.fencing_token))
        return original_renew_run_claim(claim, *args, **kwargs)

    original_renew_run_claim = resource_full_auto.renew_run_claim
    monkeypatch.setattr(resource_full_auto, "renew_run_claim", track_run_renewal)

    original_get_operation = authoring_operations.get_operation

    def track_owned_get(operation_session_id, operation_id, **kwargs):
        claim = kwargs.get("_full_auto_claim")
        assert claim is not None, "The coordinator must read each child with its owner claim."
        observed_owner_gets.append(
            (operation_id, claim.run_id, claim.epoch, claim.fencing_token),
        )
        return original_get_operation(operation_session_id, operation_id, **kwargs)

    monkeypatch.setattr(authoring_operations, "get_operation", track_owned_get)

    original_checkpoint = resource_full_auto.checkpoint_prepare_child

    def track_checkpoint(claim, operation_id, *args, **kwargs):
        result = original_checkpoint(claim, operation_id, *args, **kwargs)
        checkpoint_prefixes.append(list(result["completed_take_ids"]))
        return result

    monkeypatch.setattr(resource_full_auto, "checkpoint_prepare_child", track_checkpoint)

    def accept_only_synthetic_snapshot(session_id_arg, plan_revision_arg, take_id, row=None, **kwargs):
        snapshot = row or session_plan._prepared_take_row(
            session_id_arg, plan_revision_arg, take_id,
        )
        assert snapshot is not None, "A checkpoint may validate only a persisted synthetic snapshot."
        assert snapshot["session_id"] == session_id
        assert snapshot["plan_revision"] == revision
        assert snapshot["take_id"] == take_id
        assert snapshot["status"] == "ready"
        assert snapshot["linked_shot_id"] is None
        provenance = snapshot["provenance"]
        if isinstance(provenance, str):
            provenance = json.loads(provenance)
        assert isinstance(provenance, dict)
        marker = provenance.get(synthetic_marker)
        assert isinstance(marker, dict), "The orchestration test must reject unmarked evidence."
        operation_id = synthetic_operation_by_take.get(take_id)
        assert operation_id is not None
        assert marker == {"operation_id": operation_id}
        assert snapshot["id"] == synthetic_snapshot_id_by_take[take_id]
        operation = db.one(
            "SELECT session_id, plan_revision, kind, full_auto_run_id, "
            "full_auto_run_epoch, full_auto_run_fencing_token "
            "FROM authoring_operation WHERE operation_id = ?",
            operation_id,
        )
        assert operation is not None
        assert operation["session_id"] == session_id
        assert operation["plan_revision"] == revision
        assert operation["kind"] == "prepare_takes"
        assert (
            operation["full_auto_run_id"],
            operation["full_auto_run_epoch"],
            operation["full_auto_run_fencing_token"],
        ) == expected_owner
        assert db._tx_depth > 0, "The real checkpoint must validate evidence in its write transaction."
        validated_synthetic_takes.add(take_id)
        return None

    # The synthetic rows below are not attestations. The wrapper is local to
    # this orchestration test and refuses every row without the exact marker.
    monkeypatch.setattr(
        resource_preparation,
        "validate_authoring_prepared_evidence",
        accept_only_synthetic_snapshot,
    )

    async def produce_synthetic_child(
        session_id_arg, operation_id, *, _full_auto_claim=None, **kwargs,
    ):
        assert db._tx_depth == 0, "No transaction may span the substituted child-production boundary."
        assert _full_auto_claim is not None
        owner = resource_full_auto.require_prepare_claim(
            _full_auto_claim,
            session_id=session_id_arg,
            now_text=db.now(),
        )
        operation = db.one(
            "SELECT operation_id, session_id, plan_revision, kind, state, requested_json, "
            "full_auto_run_id, full_auto_run_epoch, full_auto_run_fencing_token "
            "FROM authoring_operation WHERE operation_id = ?",
            operation_id,
        )
        assert operation is not None and operation["state"] == "active"
        assert operation["session_id"] == session_id
        assert operation["plan_revision"] == revision
        assert operation["kind"] == "prepare_takes"
        assert (
            operation["full_auto_run_id"],
            operation["full_auto_run_epoch"],
            operation["full_auto_run_fencing_token"],
        ) == expected_owner
        assert owner["run_id"] == run_id

        batch = json.loads(operation["requested_json"])
        batch_index = len(produced_children)
        expected_batch = targets[batch_index * 20:(batch_index + 1) * 20]
        assert batch == expected_batch
        assert len(batch) == 20
        produced_children.append((operation_id, batch))

        with db.transaction():
            resource_full_auto.require_prepare_claim(
                _full_auto_claim,
                session_id=session_id_arg,
                now_text=db.now(),
            )
            for take_id in batch:
                provenance = json.dumps(
                    {synthetic_marker: {"operation_id": operation_id}},
                    separators=(",", ":"),
                )
                snapshot_id = db.run(
                    "INSERT INTO prepared_take "
                    "(session_id, plan_revision, take_id, status, provenance, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'ready', ?, ?, ?)",
                    session_id,
                    revision,
                    take_id,
                    provenance,
                    db.now(),
                    db.now(),
                )
                synthetic_operation_by_take[take_id] = operation_id
                synthetic_snapshot_id_by_take[take_id] = int(snapshot_id)

            result_items = [
                {
                    "target": take_id,
                    "result": {
                        "prepared_take_id": synthetic_snapshot_id_by_take[take_id],
                        "take_id": take_id,
                        "status": "ready",
                        "test_only_synthetic": True,
                    },
                }
                for take_id in batch
            ]
            db.run(
                "UPDATE authoring_operation SET state = 'succeeded', lease_expires_at = NULL, "
                "completed_json = ?, remaining_json = '[]', result_json = ?, updated_at = ? "
                "WHERE operation_id = ? AND state = 'active' "
                "AND full_auto_run_id = ? AND full_auto_run_epoch = ? "
                "AND full_auto_run_fencing_token = ?",
                json.dumps(batch, separators=(",", ":")),
                json.dumps({"items": result_items}, separators=(",", ":")),
                db.now(),
                operation_id,
                _full_auto_claim.run_id,
                _full_auto_claim.epoch,
                _full_auto_claim.fencing_token,
            )
            saved = db.one(
                "SELECT state, completed_json, remaining_json FROM authoring_operation "
                "WHERE operation_id = ?",
                operation_id,
            )
            assert saved is not None and saved["state"] == "succeeded"
            assert json.loads(saved["completed_json"]) == batch
            assert json.loads(saved["remaining_json"]) == []
        return _full_auto_claim

    monkeypatch.setattr(main, "_run_prepare_takes_operation", produce_synthetic_child)

    provider_attempts = []

    class ForbiddenAssistantClient:
        def __init__(self, *args, **kwargs):
            provider_attempts.append((args, kwargs))
            raise AssertionError("The 500-target orchestration contract must not call a provider.")

    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", ForbiddenAssistantClient)
    runner_attempts = []

    def forbidden_runner_start(*args, **kwargs):
        runner_attempts.append((args, kwargs))
        raise AssertionError("Full Auto preparation must stop before starting the runner.")

    monkeypatch.setattr(main.runner, "start", forbidden_runner_start)

    result = asyncio.run(main._run_full_auto_preparation(parent_claim))

    assert result["state"] == "active"
    assert result["phase"] == "validate"
    assert provider_attempts == []
    assert runner_attempts == []
    assert len(renewed_claims) == 51
    assert all(claim_tuple == expected_owner for claim_tuple in renewed_claims)
    assert len(produced_children) == 25
    operation_ids = [operation_id for operation_id, _batch in produced_children]
    assert len(set(operation_ids)) == 25
    assert [operation_id for operation_id, *_owner in observed_owner_gets] == operation_ids
    assert all(
        (claim_run_id, claim_epoch, claim_fence) == expected_owner
        for _operation_id, claim_run_id, claim_epoch, claim_fence in observed_owner_gets
    )
    assert checkpoint_prefixes == [targets[:end] for end in range(20, 501, 20)]

    rows = _prepared_rows(session_id, revision)
    assert [row["take_id"] for row in rows] == targets
    assert all(row["status"] == "ready" for row in rows)
    assert db.one("SELECT 1 FROM shot WHERE session_id = ?", session_id) is None
    assert validated_synthetic_takes == set(targets)

    progress = resource_full_auto._read_prepare_progress(run_id)
    assert progress["target_ids"] == targets
    assert progress["completed_take_ids"] == targets
    assert progress["remaining_take_ids"] == []
    assert len(progress["children"]) == 25
    assert [child["batch_index"] for child in progress["children"]] == list(range(25))
    assert [child["operation_id"] for child in progress["children"]] == operation_ids
    assert [child["target_ids"] for child in progress["children"]] == [
        targets[start:start + 20] for start in range(0, 500, 20)
    ]

    for child in progress["children"]:
        operation = db.one(
            "SELECT operation_id, session_id, plan_revision, kind, state, requested_json, "
            "completed_json, remaining_json, full_auto_run_id, full_auto_run_epoch, "
            "full_auto_run_fencing_token FROM authoring_operation WHERE operation_id = ?",
            child["operation_id"],
        )
        expected_targets = child["target_ids"]
        assert operation is not None
        assert operation["session_id"] == session_id
        assert operation["plan_revision"] == revision
        assert operation["kind"] == "prepare_takes"
        assert operation["state"] == "succeeded"
        assert json.loads(operation["requested_json"]) == expected_targets
        assert json.loads(operation["completed_json"]) == expected_targets
        assert json.loads(operation["remaining_json"]) == []
        assert (
            operation["full_auto_run_id"],
            operation["full_auto_run_epoch"],
            operation["full_auto_run_fencing_token"],
        ) == expected_owner


def test_failed_twenty_third_take_preserves_twenty_two_and_resume_reuses_them(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=40,
    )
    unlocked_targets = [
        take_id for take_id in targets
        if resource_preparation.compute_unlocked_fields(
            resource_preparation.prepare_take_inputs(session_id, revision, take_id),
        )
    ]
    assert targets[22] in unlocked_targets
    first_provider = _install_provider(
        monkeypatch, session_id, revision, targets, fail_take=targets[22],
    )

    blocked = asyncio.run(
        main._run_full_auto_preparation(resource_full_auto.claim_run(run_id)),
    )

    assert blocked["state"] == "blocked"
    assert blocked["phase"] == "prepare"
    assert blocked["blocker_code"] == "preparation_failed"
    assert first_provider["calls"] == targets[:23]
    assert first_provider["transaction_depths"] == [0] * 23
    rows = _prepared_rows(session_id, revision)
    assert [row["take_id"] for row in rows] == targets[:22]
    assert all(row["status"] == "ready" for row in rows)
    assert db.one("SELECT 1 FROM shot WHERE session_id = ?", session_id) is None
    progress = resource_full_auto._read_prepare_progress(run_id)
    assert progress["completed_take_ids"] == targets[:22]

    resumed = resource_full_auto.resume_run(
        run_id,
        str(uuid.uuid4()),
        blocked["revision"],
        revision,
        eligibility=resource_full_auto.ResumeEligibility(
            current_plan_revision=revision,
            unresolved_work=0,
            authorization_valid=True,
            policy_supported=True,
            budget_available=True,
            session_exclusive=True,
            scope_matches=True,
            planning_enabled=True,
            next_state="active",
        ),
    )
    assert resumed.claim is not None
    old_snapshot_ids = {row["take_id"]: row["id"] for row in rows}
    second_provider = _install_provider(
        monkeypatch, session_id, revision, targets[22:],
    )

    validated = asyncio.run(main._run_full_auto_preparation(resumed.claim))

    expected_calls = [
        take_id for take_id in targets[22:]
        if resource_preparation.compute_unlocked_fields(
            resource_preparation.prepare_take_inputs(session_id, revision, take_id),
        )
    ]
    assert validated["state"] == "active", (
        validated,
        db.q(
            "SELECT state, failed_item, error, completed_json, remaining_json "
            "FROM authoring_operation WHERE session_id = ? ORDER BY created_at",
            session_id,
        ),
        second_provider["calls"],
    )
    assert validated["phase"] == "validate"
    assert second_provider["calls"] == expected_calls
    assert not set(second_provider["calls"]) & set(targets[:22])
    final_rows = _prepared_rows(session_id, revision)
    assert [row["take_id"] for row in final_rows] == targets
    assert all(row["status"] == "ready" for row in final_rows)
    assert all(old_snapshot_ids[row["take_id"]] == row["id"] for row in final_rows[:22])
    assert db.one("SELECT 1 FROM shot WHERE session_id = ?", session_id) is None
    assert resource_full_auto._read_prepare_progress(run_id)["completed_take_ids"] == targets


def test_valid_prepared_prefix_is_check_reported_before_the_next_child_is_created(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=40,
    )
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    first_provider = _install_provider(
        monkeypatch, session_id, revision, targets[:20],
    )
    first_request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": targets[:20],
    })
    first_child, created = authoring_operations.start_operation(
        session_id,
        first_request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    owner = asyncio.run(main._run_prepare_takes_operation(
        session_id, first_child["operation_id"], _full_auto_claim=owner,
    )) or owner
    assert first_provider["calls"] == first_provider["dispatch_order"]
    original_ids = {
        row["take_id"]: row["id"]
        for row in _prepared_rows(session_id, revision)
    }
    assert list(original_ids) == targets[:20]

    second_provider = _install_provider(
        monkeypatch, session_id, revision, targets[20:],
    )
    validated = asyncio.run(main._run_full_auto_preparation(owner))

    assert validated["state"] == "active"
    assert validated["phase"] == "validate"
    assert second_provider["calls"] == second_provider["dispatch_order"]
    assert not set(second_provider["calls"]) & set(targets[:20])
    children = resource_full_auto._read_prepare_progress(run_id)["children"]
    assert len(children) == 2
    remaining_operation = db.one(
        "SELECT requested_json FROM authoring_operation WHERE operation_id = ?",
        children[1]["operation_id"],
    )
    assert json.loads(remaining_operation["requested_json"]) == targets[20:]
    final_rows = _prepared_rows(session_id, revision)
    assert [row["take_id"] for row in final_rows] == targets
    assert all(original_ids[row["take_id"]] == row["id"] for row in final_rows[:20])


def test_prepared_prefix_is_revalidated_inside_the_checkpoint_transaction(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=4,
    )
    owner = _prepare_owned_batch(
        session_id, run_id, revision, targets, monkeypatch,
    )
    before = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    assert json.loads(before["completed_ids_json"]) == []

    original_validate = resource_preparation.validate_authoring_prepared_evidence
    validation_depths = []
    mutated = False

    def mutate_after_read_validation(*args, **kwargs):
        nonlocal mutated
        validation_depths.append(db._tx_depth)
        result = original_validate(*args, **kwargs)
        if not mutated and db._tx_depth == 0:
            snapshot = kwargs["row"]
            db.run(
                "UPDATE prepared_take SET final_prompt = final_prompt || ? WHERE id = ?",
                " changed during checkpoint validation", snapshot["id"],
            )
            mutated = True
        return result

    monkeypatch.setattr(
        resource_preparation,
        "validate_authoring_prepared_evidence",
        mutate_after_read_validation,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as stale:
        resource_full_auto.initialize_prepare_progress(owner)

    assert stale.value.code == "prepared_evidence_stale"
    assert mutated is True
    assert 0 in validation_depths
    assert 1 in validation_depths
    assert db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    ) == before


def test_prepared_prefix_dependency_is_revalidated_inside_the_checkpoint_transaction(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=4,
    )
    owner = _prepare_owned_batch(
        session_id, run_id, revision, targets, monkeypatch,
    )
    before = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    original_validate = resource_preparation.validate_authoring_prepared_evidence
    validation_depths = []
    mutated = False

    def mutate_operation_result_after_read_validation(*args, **kwargs):
        nonlocal mutated
        validation_depths.append(db._tx_depth)
        result = original_validate(*args, **kwargs)
        if not mutated and db._tx_depth == 0:
            provenance = json.loads(kwargs["row"]["provenance"])
            operation_id = provenance["authoring_evidence"]["operation_id"]
            db.run(
                "UPDATE authoring_operation SET result_json = ? WHERE operation_id = ?",
                json.dumps({"items": []}), operation_id,
            )
            mutated = True
        return result

    monkeypatch.setattr(
        resource_preparation,
        "validate_authoring_prepared_evidence",
        mutate_operation_result_after_read_validation,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as stale:
        resource_full_auto.initialize_prepare_progress(owner)

    assert stale.value.code == "prepared_evidence_stale"
    assert mutated is True
    assert 0 in validation_depths
    assert 1 in validation_depths
    assert db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    ) == before


def test_child_checkpoint_rejects_snapshot_corruption_before_advancing_progress(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=4,
    )
    owner = _prepare_owned_batch(
        session_id, run_id, revision, targets, monkeypatch,
    )
    child = resource_full_auto._read_prepare_progress(run_id)["children"][0]
    before = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    snapshot = session_plan._prepared_take_row(session_id, revision, targets[0])
    assert snapshot is not None
    db.run(
        "UPDATE prepared_take SET final_prompt = final_prompt || ? WHERE id = ?",
        " changed after child finalization", snapshot["id"],
    )

    with pytest.raises(resource_full_auto.FullAutoRunError) as stale:
        resource_full_auto.checkpoint_prepare_child(owner, child["operation_id"])

    assert stale.value.code == "prepared_evidence_stale"
    assert db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    ) == before


def test_child_checkpoint_rechecks_lease_after_snapshot_validation(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=4,
    )
    owner = _prepare_owned_batch(
        session_id, run_id, revision, targets, monkeypatch,
    )
    child = resource_full_auto._read_prepare_progress(run_id)["children"][0]
    before = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    clock = [owner.lease_started_at]
    monkeypatch.setattr(resource_full_auto.db, "now", lambda: clock[0])
    original_validate = resource_preparation.validate_authoring_prepared_evidence
    validation_depths = []

    def expire_after_snapshot_validation(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        validation_depths.append(db._tx_depth)
        if db._tx_depth == 1:
            clock[0] = owner.lease_expires_at
        return result

    monkeypatch.setattr(
        resource_preparation,
        "validate_authoring_prepared_evidence",
        expire_after_snapshot_validation,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as expired:
        resource_full_auto.checkpoint_prepare_child(owner, child["operation_id"])

    assert expired.value.code == "run_lease_expired"
    assert 1 in validation_depths
    assert db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    ) == before


def test_child_registration_rechecks_lease_immediately_before_insert(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=4,
    )
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": targets,
    })
    original_register = resource_full_auto.register_prepare_child
    monkeypatch.setattr(
        resource_full_auto,
        "register_prepare_child",
        lambda claim, operation_id, target_ids: resource_full_auto._read_prepare_progress(claim.run_id),
    )
    child, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    monkeypatch.setattr(resource_full_auto, "register_prepare_child", original_register)

    clock = [owner.lease_started_at]
    monkeypatch.setattr(resource_full_auto.db, "now", lambda: clock[0])
    original_one = resource_full_auto.db.one

    def expire_after_operation_read(sql, *args):
        result = original_one(sql, *args)
        if "FROM authoring_operation" in sql:
            clock[0] = owner.lease_expires_at
        return result

    monkeypatch.setattr(resource_full_auto.db, "one", expire_after_operation_read)
    with pytest.raises(resource_full_auto.FullAutoRunError) as expired:
        resource_full_auto.register_prepare_child(owner, child["operation_id"], targets)

    assert expired.value.code == "run_lease_expired"
    assert db.one(
        "SELECT 1 FROM full_auto_prepare_child WHERE run_id = ?", run_id,
    ) is None


def test_prepare_lease_is_checked_again_after_prefix_validation(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=4,
    )
    owner = _prepare_owned_batch(
        session_id, run_id, revision, targets, monkeypatch,
    )
    before = db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    )
    clock = [owner.lease_started_at]
    monkeypatch.setattr(resource_full_auto.db, "now", lambda: clock[0])
    original_validate = resource_preparation.validate_authoring_prepared_evidence
    validation_depths = []

    def expire_lease_during_validation(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        validation_depths.append(db._tx_depth)
        if db._tx_depth == 1:
            clock[0] = owner.lease_expires_at
        return result

    monkeypatch.setattr(
        resource_preparation,
        "validate_authoring_prepared_evidence",
        expire_lease_during_validation,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as expired:
        resource_full_auto.initialize_prepare_progress(owner)

    assert expired.value.code == "run_lease_expired"
    assert 0 in validation_depths
    assert 1 in validation_depths
    assert db.one(
        "SELECT * FROM full_auto_prepare_progress WHERE run_id = ?", run_id,
    ) == before


def test_owned_authoring_worker_requires_a_live_parent_capability(client, seeded):
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=4,
    )
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": targets,
    })
    child, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    with pytest.raises(authoring_operations.AuthoringOperationError) as missing:
        authoring_operations.load_worker_claim(session_id, child["operation_id"])
    assert missing.value.code == "full_auto_owner_required"

    forged = replace(owner, fencing_token=owner.fencing_token + 1)
    with pytest.raises(authoring_operations.AuthoringOperationError) as invalid:
        authoring_operations.load_worker_claim(
            session_id, child["operation_id"], _full_auto_claim=forged,
        )
    assert invalid.value.code == "full_auto_owner_stale"


def test_owned_operation_get_rejects_an_expired_parent_claim_without_writes(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets = _create_run(
        client, seeded, photo_count=4,
    )
    owner = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(owner)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": targets,
    })
    child, created = authoring_operations.start_operation(
        session_id,
        request,
        planning_enabled=True,
        assistant_available=True,
        _full_auto_claim=owner,
    )
    assert created is True
    before = authoring_operations._get_operation(child["operation_id"])

    monkeypatch.setattr(authoring_operations.db, "now", lambda: owner.lease_expires_at)
    with pytest.raises(authoring_operations.AuthoringOperationError) as expired:
        authoring_operations.get_operation(
            session_id,
            child["operation_id"],
            planning_enabled=True,
            assistant_available=True,
            _full_auto_claim=owner,
        )

    assert expired.value.code == "full_auto_owner_stale"
    assert authoring_operations._get_operation(child["operation_id"]) == before
