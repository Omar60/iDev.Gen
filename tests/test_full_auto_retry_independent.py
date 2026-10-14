from __future__ import annotations

import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
import time
import uuid

import pytest

import db
import main
from backend import resource_full_auto
import test_full_auto_attempt_journal_independent as journal


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "full-auto-retry-independent-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "full-auto-retry-independent-test-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)


def _unknown_attempt(client, seeded, monkeypatch, make_runner):
    session_id, run_id, _revision, _targets, owner, runner, fake = journal._execution(
        client, seeded, monkeypatch, make_runner,
    )
    monkeypatch.setattr(main, "runner", runner)
    fake.advertise_client_prompt_id = True
    original_queue_prompt = fake.queue_prompt
    captured: dict[str, list] = {}

    async def accept_then_lose_response(graph, client_id, **kwargs):
        prompt_id = await original_queue_prompt(graph, client_id, **kwargs)
        captured["entry"] = copy.deepcopy(fake.queue_records[-1])
        fake.queue_records.clear()
        return_value = prompt_id
        raise TimeoutError(f"queue acknowledgement for {return_value} was lost")

    fake.queue_prompt = accept_then_lose_response

    async def run_once():
        assert runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(run_once())
    fake.queue_prompt = original_queue_prompt
    attempt = db.one(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY attempt_number LIMIT 1",
        run_id,
    )
    assert attempt["dispatch_state"] == "outcome_unknown"
    assert attempt["completion_state"] == "pending"
    assert attempt["client_prompt_id"] == captured["entry"][1]
    assert fake.queue_records == []
    db.run(
        "UPDATE global_runner_claim SET lease_expires_at = ? WHERE singleton = 1",
        "2000-01-01T00:00:00+00:00",
    )
    return session_id, run_id, runner, fake, attempt, captured["entry"]


def _state_snapshot(run_id: str, session_id: int, attempt_id: str, shot_id: int) -> dict:
    return {
        "run": db.one(
            "SELECT state, revision, epoch, fencing_token, cancel_intent "
            "FROM full_auto_run WHERE run_id = ?", run_id,
        ),
        "shot": db.one(
            "SELECT status, prompt_id, filename, error FROM shot WHERE id = ? AND session_id = ?",
            shot_id, session_id,
        ),
        "attempts": db.q(
            "SELECT attempt_id, attempt_number, dispatch_state, completion_state "
            "FROM full_auto_execution_attempt WHERE run_id = ? AND shot_id = ? ORDER BY attempt_number",
            run_id, shot_id,
        ),
        "risk": db.q(
            "SELECT * FROM full_auto_attempt_risk_acceptance WHERE run_id = ? ORDER BY attempt_id",
            run_id,
        ),
        "retry": db.q(
            "SELECT * FROM full_auto_attempt_retry_authorization WHERE run_id = ? ORDER BY attempt_id",
            run_id,
        ),
        "actions": db.q(
            "SELECT request_id, request_digest, action FROM full_auto_run_action_receipt "
            "WHERE run_id = ? ORDER BY request_id", run_id,
        ),
    }


def _remove_risk_receipt_for_fixture(attempt_id: str) -> None:
    """Remove test-only evidence even if the implementation guards DELETE."""
    triggers = db.q(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
        "AND tbl_name = 'full_auto_attempt_risk_acceptance' ORDER BY name",
    )
    for trigger in triggers:
        db.run(f'DROP TRIGGER "{trigger["name"]}"')
    db.run(
        "DELETE FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
        attempt_id,
    )
    for trigger in triggers:
        if trigger["sql"]:
            db.run(trigger["sql"])


def _remove_retry_receipt_for_fixture(attempt_id: str) -> None:
    triggers = db.q(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
        "AND tbl_name = 'full_auto_attempt_retry_authorization' ORDER BY name",
    )
    for trigger in triggers:
        db.run(f'DROP TRIGGER "{trigger["name"]}"')
    db.run(
        "DELETE FROM full_auto_attempt_retry_authorization WHERE attempt_id = ?",
        attempt_id,
    )
    for trigger in triggers:
        if trigger["sql"]:
            db.run(trigger["sql"])


def _failed_execution(client, seeded, monkeypatch, make_runner, *, fail_first=0):
    session_id, run_id, _revision, _targets, owner, runner, fake = journal._execution(
        client, seeded, monkeypatch, make_runner,
        provider_options={"fail_queue": not bool(fail_first), "fail_first": fail_first},
    )
    monkeypatch.setattr(main, "runner", runner)

    async def run_once():
        assert runner.start(session_id, _full_auto_claim=owner)
        await runner._task

    asyncio.run(run_once())
    return session_id, run_id, runner, fake


@pytest.mark.parametrize("corruption", ["missing_marker", "conflicting_marker"])
def test_same_client_prompt_id_with_corrupt_queue_identity_is_not_absence_evidence(
    corruption, client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _runner, fake, attempt, accepted_entry = _unknown_attempt(
        client, seeded, monkeypatch, make_runner,
    )
    quote = journal._resume_quote(client, session_id, run_id)
    body = journal._resume_retry_body(session_id, run_id, quote, accept_unknown=True)
    entry = copy.deepcopy(accepted_entry)
    entry[2]["8"]["inputs"]["filename_prefix"] += "-changed"
    if corruption == "missing_marker":
        entry[3].pop("idevgen_attempt", None)
        entry[3].get("extra_pnginfo", {}).pop("idevgen_attempt", None)
    else:
        conflicting = copy.deepcopy(entry[3]["idevgen_attempt"])
        conflicting["attempt_id"] = uuid.uuid4().hex
        entry[3]["idevgen_attempt"] = conflicting
        entry[3]["extra_pnginfo"]["idevgen_attempt"] = conflicting
    fake.queue_records.append(entry)
    before = _state_snapshot(run_id, session_id, attempt["attempt_id"], attempt["shot_id"])

    try:
        response = client.post(
            f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
        )
    finally:
        if db.one(
            "SELECT 1 FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
            attempt["attempt_id"],
        ) is not None:
            _remove_risk_receipt_for_fixture(attempt["attempt_id"])

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] in {
        "unknown_outcome_evidence_invalid", "unknown_outcome_still_active",
    }
    assert _state_snapshot(run_id, session_id, attempt["attempt_id"], attempt["shot_id"]) == before
    assert db.one(
        "SELECT status FROM shot WHERE id = ?", attempt["shot_id"],
    )["status"] == "running"


def test_resume_run_rejects_forged_fresh_unknown_queue_evidence_without_writes(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _runner, _fake, attempt, _entry = _unknown_attempt(
        client, seeded, monkeypatch, make_runner,
    )
    quote = journal._resume_quote(client, session_id, run_id)
    body = journal._resume_retry_body(session_id, run_id, quote, accept_unknown=True)
    forged = resource_full_auto.UnknownRetryEvidence(
        attempt_id=attempt["attempt_id"],
        queue_snapshot_digest="a" * 64,
        capability_evidence_digest="b" * 64,
        observed_at=db.now(),
    )
    before = _state_snapshot(run_id, session_id, attempt["attempt_id"], attempt["shot_id"])
    receipt = None

    try:
        with pytest.raises(resource_full_auto.FullAutoRunError) as refused:
            resource_full_auto.resume_run(
                run_id,
                body["request_id"],
                body["expected_run_revision"],
                body["expected_plan_revision"],
                eligibility=None,
                scope_digest=body["scope_digest"],
                comparison_issued_at=body["comparison_issued_at"],
                accept_unknown_outcome_risk=True,
                unknown_retry_evidence=[forged],
                config=lambda: main.CONFIG,
                defer_claim=True,
                expected_session_id=session_id,
            )
        assert refused.value.code in {
            "unknown_outcome_evidence_required", "unknown_outcome_evidence_invalid",
            "unknown_outcome_evidence_untrusted", "unknown_outcome_proof_required",
        }
    finally:
        receipt = db.one(
            "SELECT * FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
            attempt["attempt_id"],
        )
        if receipt is not None:
            _remove_risk_receipt_for_fixture(attempt["attempt_id"])

    assert _state_snapshot(run_id, session_id, attempt["attempt_id"], attempt["shot_id"]) == before


def test_unknown_queue_proof_expiring_inside_control_transaction_is_rejected_without_writes(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _runner, fake, attempt, _entry = _unknown_attempt(
        client, seeded, monkeypatch, make_runner,
    )
    quote = journal._resume_quote(client, session_id, run_id)
    body = journal._resume_retry_body(session_id, run_id, quote, accept_unknown=True)
    original_now = db.now
    monkeypatch.setattr(
        db, "now",
        lambda: datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    )
    monkeypatch.setattr(resource_full_auto, "RUN_LEASE", timedelta(milliseconds=35))
    original_cas = resource_full_auto._check_control_cas

    def delay_after_lock(*args, **kwargs):
        # resume_run calls this after opening its write transaction; the proof
        # is deliberately older than its freshness window at the write boundary.
        original_cas(*args, **kwargs)
        time.sleep(0.08)

    monkeypatch.setattr(resource_full_auto, "_check_control_cas", delay_after_lock)
    before = _state_snapshot(run_id, session_id, attempt["attempt_id"], attempt["shot_id"])
    try:
        response = client.post(
            f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
        )
    finally:
        receipt = db.one(
            "SELECT 1 FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
            attempt["attempt_id"],
        )
        if receipt is not None:
            _remove_risk_receipt_for_fixture(attempt["attempt_id"])
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "unknown_outcome_evidence_stale"
    assert _state_snapshot(run_id, session_id, attempt["attempt_id"], attempt["shot_id"]) == before
    monkeypatch.setattr(db, "now", original_now)


def test_failed_retry_rechecks_quote_after_action_receipt_before_grant(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _runner, _fake = _failed_execution(
        client, seeded, monkeypatch, make_runner,
    )
    quote = journal._resume_quote(client, session_id, run_id)
    body = journal._resume_retry_body(session_id, run_id, quote, retry_failed=True)
    issued = datetime.fromisoformat(quote["comparison_issued_at"])
    clock = {"now": issued + timedelta(seconds=299)}
    monkeypatch.setattr(
        db, "now", lambda: clock["now"].isoformat(timespec="seconds"),
    )
    original_store_action = resource_full_auto._store_action

    def delay_after_action_receipt(*args, **kwargs):
        result = original_store_action(*args, **kwargs)
        # The quoted comparison is still valid at every pre-write check, then
        # expires after the action receipt is serialized and before grants are
        # inserted. A final write-boundary clock read must reject and roll back.
        clock["now"] = issued + timedelta(seconds=301)
        return result

    monkeypatch.setattr(resource_full_auto, "_store_action", delay_after_action_receipt)
    monkeypatch.setattr(main, "_schedule_full_auto_run", lambda *_args, **_kwargs: None)
    attempt = db.one(
        "SELECT attempt_id, shot_id FROM full_auto_execution_attempt "
        "WHERE run_id = ? ORDER BY attempt_number LIMIT 1", run_id,
    )
    before = _state_snapshot(run_id, session_id, attempt["attempt_id"], attempt["shot_id"])

    response = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )

    after = _state_snapshot(run_id, session_id, attempt["attempt_id"], attempt["shot_id"])
    assert response.status_code == 409 and after == before, (
        f"expired known-retry quote status={response.status_code}, "
        f"error={response.text}, before={before}, after={after}"
    )
    assert response.json()["detail"]["code"] == "resume_comparison_stale"


@pytest.mark.parametrize("tamper", ["cross_request", "sealed_digest"])
def test_unknown_queue_evidence_is_bound_to_request_and_seal_without_writes(
    tamper, client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, runner, _fake, attempt, _entry = _unknown_attempt(
        client, seeded, monkeypatch, make_runner,
    )
    quote = journal._resume_quote(client, session_id, run_id)
    body = journal._resume_retry_body(session_id, run_id, quote, accept_unknown=True)
    evidence = asyncio.run(runner.check_unknown_retry_queue(
        session_id, run_id, [attempt["attempt_id"]],
        request_id=body["request_id"],
        expected_run_revision=body["expected_run_revision"],
        expected_plan_revision=body["expected_plan_revision"],
        scope_digest=body["scope_digest"],
        comparison_issued_at=body["comparison_issued_at"],
    ))
    request_id = body["request_id"]
    if tamper == "cross_request":
        request_id = str(uuid.uuid4())
    else:
        evidence = [resource_full_auto.UnknownRetryEvidence(**{
            **evidence[0].__dict__,
            "queue_snapshot_digest": "c" * 64,
        })]
    before = _state_snapshot(run_id, session_id, attempt["attempt_id"], attempt["shot_id"])

    try:
        with pytest.raises(resource_full_auto.FullAutoRunError) as refused:
            resource_full_auto.resume_run(
                run_id,
                request_id,
                body["expected_run_revision"],
                body["expected_plan_revision"],
                eligibility=None,
                scope_digest=body["scope_digest"],
                comparison_issued_at=body["comparison_issued_at"],
                accept_unknown_outcome_risk=True,
                unknown_retry_evidence=evidence,
                config=lambda: main.CONFIG,
                defer_claim=True,
                expected_session_id=session_id,
            )
        assert refused.value.code in {
            "unknown_outcome_evidence_invalid", "unknown_outcome_evidence_untrusted",
        }
    finally:
        receipt = db.one(
            "SELECT 1 FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
            attempt["attempt_id"],
        )
        if receipt is not None:
            _remove_risk_receipt_for_fixture(attempt["attempt_id"])

    assert _state_snapshot(run_id, session_id, attempt["attempt_id"], attempt["shot_id"]) == before


def test_risk_receipt_cannot_be_deleted_to_release_completed_run_deletion_guard(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, runner, fake, attempt, _entry = _unknown_attempt(
        client, seeded, monkeypatch, make_runner,
    )
    quote = journal._resume_quote(client, session_id, run_id)
    body = journal._resume_retry_body(session_id, run_id, quote, accept_unknown=True)
    fake.queue_records.clear()
    accepted = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    assert accepted.status_code == 202, accepted.text
    asyncio.run(journal._run_retry_attempt(runner, session_id, run_id))
    assert client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()["state"] == "succeeded"
    receipt = db.one(
        "SELECT * FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
        attempt["attempt_id"],
    )
    assert receipt is not None
    assert db.one(
        "SELECT 1 FROM full_auto_deletion_protected_run WHERE run_id = ?", run_id,
    ) is not None
    assert resource_full_auto._run_deletion_protected(resource_full_auto._run_row(run_id)) is True
    try:
        try:
            db.run(
                "DELETE FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
                attempt["attempt_id"],
            )
        except sqlite3.IntegrityError as exc:
            assert any(word in str(exc).lower() for word in ("immutable", "full_auto_in_use", "risk"))
        else:
            assert db.one(
                "SELECT 1 FROM full_auto_deletion_protected_run WHERE run_id = ?", run_id,
            ) is None
            pytest.fail("Deleting risk evidence removed the completed run's deletion guard")
        assert db.one(
            "SELECT * FROM full_auto_attempt_risk_acceptance WHERE attempt_id = ?",
            attempt["attempt_id"],
        ) == receipt
        assert db.one(
            "SELECT 1 FROM full_auto_deletion_protected_run WHERE run_id = ?", run_id,
        ) is not None
    finally:
        _remove_risk_receipt_for_fixture(attempt["attempt_id"])


def test_risk_receipt_must_bind_the_authoritative_attempt_run_session_and_shot(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _runner, _fake, attempt, _entry = _unknown_attempt(
        client, seeded, monkeypatch, make_runner,
    )
    other_shot = db.one(
        "SELECT shot_id FROM full_auto_submission_receipt WHERE run_id = ? "
        "AND shot_id != ? ORDER BY target_index LIMIT 1",
        run_id, attempt["shot_id"],
    )["shot_id"]
    timestamp = db.now()
    try:
        try:
            db.run(
                """INSERT INTO full_auto_attempt_risk_acceptance (
                       attempt_id, run_id, session_id, shot_id, request_id, run_epoch,
                       queue_snapshot_digest, capability_evidence_digest, observed_at, accepted_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                attempt["attempt_id"], run_id, session_id, other_shot,
                f"unrelated-{uuid.uuid4().hex}", attempt["run_epoch"],
                "a" * 64, "b" * 64, timestamp, timestamp,
            )
        except sqlite3.IntegrityError:
            pass
        else:
            assert resource_full_auto._retry_candidates(session_id, run_id)[
                "unknown_attempts"
            ] == []
            pytest.fail("A mismatched risk receipt hid the real unknown attempt")
    finally:
        _remove_risk_receipt_for_fixture(attempt["attempt_id"])
    candidates = resource_full_auto._retry_candidates(session_id, run_id)
    assert candidates["unknown_attempts"] == [{
        "shot_id": attempt["shot_id"], "attempt_id": attempt["attempt_id"],
    }]


def test_retry_grant_must_bind_the_authoritative_attempt_run_session_and_shot(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _runner, _fake = _failed_execution(
        client, seeded, monkeypatch, make_runner,
    )
    attempt = db.one(
        "SELECT * FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY attempt_number LIMIT 1",
        run_id,
    )
    other_shot = db.one(
        "SELECT shot_id FROM full_auto_submission_receipt WHERE run_id = ? "
        "AND shot_id != ? ORDER BY target_index LIMIT 1",
        run_id, attempt["shot_id"],
    )["shot_id"]
    timestamp = db.now()
    try:
        try:
            db.run(
                """INSERT INTO full_auto_attempt_retry_authorization (
                       attempt_id, run_id, session_id, shot_id, request_id,
                       run_epoch, authorized_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                attempt["attempt_id"], run_id, session_id, other_shot,
                f"unrelated-{uuid.uuid4().hex}", attempt["run_epoch"], timestamp,
            )
        except sqlite3.IntegrityError:
            pass
        else:
            candidates = resource_full_auto._retry_candidates(session_id, run_id)[
                "failed_shot_ids"
            ]
            assert attempt["shot_id"] not in candidates
            pytest.fail("A mismatched retry grant was accepted and hid its failed attempt")
    finally:
        _remove_retry_receipt_for_fixture(attempt["attempt_id"])
    candidates = resource_full_auto._retry_candidates(session_id, run_id)
    assert candidates["failed_shot_ids"] == [
        item["shot_id"] for item in db.q(
            "SELECT shot_id FROM full_auto_execution_attempt WHERE run_id = ? "
            "AND completion_state = 'failed' ORDER BY shot_id",
            run_id,
        )
    ]


def test_candidate_change_invalidates_the_displayed_retry_quote_without_writes(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _runner, _fake = _failed_execution(
        client, seeded, monkeypatch, make_runner,
    )
    quote = journal._resume_quote(client, session_id, run_id)
    body = journal._resume_retry_body(session_id, run_id, quote, retry_failed=True)
    shot_id = quote["image_retry_allowance"]["retry_failed_shot_ids"][0]
    db.run("UPDATE shot SET status = 'done' WHERE id = ?", shot_id)
    before = {
        "run": db.one(
            "SELECT state, revision, epoch, fencing_token FROM full_auto_run WHERE run_id = ?",
            run_id,
        ),
        "attempt_count": db.one(
            "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
        )["n"],
        "retry_receipts": db.q(
            "SELECT * FROM full_auto_attempt_retry_authorization WHERE run_id = ?", run_id,
        ),
        "actions": db.q(
            "SELECT request_id FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
        ),
    }

    response = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] in {
        "resume_comparison_stale", "retry_targets_changed",
    }
    assert db.one(
        "SELECT status FROM shot WHERE id = ?", shot_id,
    )["status"] == "done"
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )["n"] == before["attempt_count"]
    assert db.q(
        "SELECT * FROM full_auto_attempt_retry_authorization WHERE run_id = ?", run_id,
    ) == before["retry_receipts"] == []
    assert db.q(
        "SELECT request_id FROM full_auto_run_action_receipt WHERE run_id = ?", run_id,
    ) == before["actions"]
    assert db.one(
        "SELECT state, revision, epoch, fencing_token FROM full_auto_run WHERE run_id = ?",
        run_id,
    ) == before["run"]


def test_concurrent_retries_using_one_quote_create_only_one_grant_set(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _runner, _fake = _failed_execution(
        client, seeded, monkeypatch, make_runner,
    )
    quote = journal._resume_quote(client, session_id, run_id)
    bodies = [
        journal._resume_retry_body(session_id, run_id, quote, retry_failed=True)
        for _ in range(2)
    ]

    def post(body):
        return client.post(
            f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(post, bodies))

    assert sorted(response.status_code for response in responses) == [202, 409]
    refused = next(response for response in responses if response.status_code == 409)
    assert refused.json()["detail"]["code"] in {
        "run_revision_stale", "resume_comparison_stale",
    }
    grants = db.q(
        "SELECT * FROM full_auto_attempt_retry_authorization WHERE run_id = ? ORDER BY shot_id",
        run_id,
    )
    expected = db.q(
        "SELECT attempt_id, shot_id FROM full_auto_execution_attempt "
        "WHERE run_id = ? AND completion_state = 'failed' ORDER BY shot_id",
        run_id,
    )
    assert len(grants) == len(expected) == 4
    assert [(row["attempt_id"], row["shot_id"]) for row in grants] == [
        (row["attempt_id"], row["shot_id"]) for row in expected
    ]
    assert len({row["request_id"] for row in grants}) == 1
    assert len({row["run_epoch"] for row in grants}) == 1
    assert all(
        db.one("SELECT status FROM shot WHERE id = ?", row["shot_id"])["status"] == "pending"
        for row in grants
    )


def test_resume_without_retry_consent_never_requeues_a_failed_attempt(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, _runner, _fake = _failed_execution(
        client, seeded, monkeypatch, make_runner, fail_first=1,
    )
    attempt_count = db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )["n"]
    failed = db.one(
        "SELECT shot_id FROM full_auto_execution_attempt WHERE run_id = ? "
        "AND completion_state = 'failed' LIMIT 1", run_id,
    )
    assert failed is not None
    run = resource_full_auto.get_run(run_id)
    plan_revision = db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?", session_id,
    )["plan_revision"]
    response = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume",
        json={
            "request_id": str(uuid.uuid4()),
            "expected_run_revision": run["revision"],
            "expected_plan_revision": plan_revision,
        },
    )
    assert response.status_code in {202, 409}, response.text
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_execution_attempt WHERE run_id = ?", run_id,
    )["n"] == attempt_count
    assert db.one(
        "SELECT status FROM shot WHERE id = ?", failed["shot_id"],
    )["status"] == "failed"
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_attempt_retry_authorization WHERE run_id = ?", run_id,
    )["n"] == 0
