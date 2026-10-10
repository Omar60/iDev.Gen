"""Independent persistence checks for the Full Auto run state machine."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading
import uuid

import pytest

import db
import main
from backend import guided_sessions, resource_full_auto
from test_guided_session_creation import (
    _forbid_full_auto_side_effects,
    _full_auto_payload,
    _room_anchor,
)


START = "2026-10-01T12:00:00+00:00"
PLAN_JSON = '{"takes":[]}'
AUTHORIZATION_EXPIRY = "2026-10-08T12:00:00+00:00"


def _authorization_json(expires_at: str = AUTHORIZATION_EXPIRY) -> str:
    return json.dumps({
        "authorization_version": 1,
        "generate_without_pauses": True,
        "profile_revision": 1,
        "profile_digest": "d" * 64,
        "progression": "all_stages_full_interval",
        "framing_intent": "vary",
        "scope_digest": "a" * 64,
        "scope_issued_at": START,
        "authorization_expires_at": expires_at,
        "summary": {"policy_version": "resource-full-auto-v1"},
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _all_rows() -> dict[str, list[str]]:
    tables = [
        row["name"] for row in db.q(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    snapshot = {}
    for table in tables:
        quoted_table = '"' + table.replace('"', '""') + '"'
        snapshot[table] = sorted(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=repr)
            for row in db.q(f"SELECT * FROM {quoted_table}")
        )
    return snapshot


def _seed_session(session_name: str) -> tuple[int, int]:
    model_id = db.run(
        "INSERT INTO model (name, created_at) VALUES (?, ?)",
        f"model-{session_name}", START,
    )
    session_id = db.run(
        "INSERT INTO session (model_id, name, created_at) VALUES (?, ?, ?)",
        model_id, session_name, START,
    )
    db.run(
        "INSERT INTO session_plan "
        "(session_id, mode, plan_revision, plan_json, created_at, updated_at) "
        "VALUES (?, 'resource-v1', 1, ?, ?, ?)",
        session_id, PLAN_JSON, START, START,
    )
    return int(model_id), int(session_id)


def _seed_run(
    session_name: str = "independent-run",
    *,
    authorization_expires_at: str = AUTHORIZATION_EXPIRY,
) -> tuple[str, int]:
    _model_id, session_id = _seed_session(session_name)
    run_id = f"run-{uuid.uuid4().hex}"
    with db.transaction():
        db.run(
            """INSERT INTO full_auto_run_receipt (
                   run_id, session_id, authorization_json, scope_digest,
                   dependencies_digest, plan_digest, scope_issued_at,
                   authorization_expires_at, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            run_id, session_id, _authorization_json(authorization_expires_at),
            "a" * 64, "b" * 64, "c" * 64,
            START, authorization_expires_at, START,
        )
        resource_full_auto.create_run_in_transaction(
            run_id, session_id, created_at=START,
        )
    return run_id, session_id


def _resume_checks(**overrides) -> resource_full_auto.ResumeEligibility:
    values = {
        "current_plan_revision": 1,
        "unresolved_work": 0,
        "authorization_valid": True,
        "policy_supported": True,
        "budget_available": True,
        "session_exclusive": True,
        "scope_matches": True,
        "planning_enabled": True,
        "next_state": "active",
    }
    values.update(overrides)
    return resource_full_auto.ResumeEligibility(**values)


def _control(run_id: str, action: str, request_id: str, *, pending=None):
    row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    if action == "cancel":
        return resource_full_auto.cancel_run(
            run_id, request_id, int(row["revision"]), int(row["plan_revision"]),
            work_pending=pending, now_text=START,
        )
    return resource_full_auto.resume_run(
        run_id, request_id, int(row["revision"]), int(row["plan_revision"]),
        eligibility=_resume_checks(), now_text=START,
    )


def test_guided_initial_run_is_atomic_and_replay_is_read_only(
    client, seeded, monkeypatch,
):
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    request = guided_sessions.normalize_request(payload)

    response_json, was_new = guided_sessions.create_or_replay(
        request, config=lambda: main.CONFIG,
    )

    assert was_new is True
    response = json.loads(response_json)
    run_id = response["full_auto_run_id"]
    session_id = response["session_id"]
    receipt = db.one("SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id)
    run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert receipt["session_id"] == session_id
    assert run["session_id"] == session_id
    assert (run["state"], run["phase"], run["revision"], run["epoch"]) == (
        "active", "prepare", 1, 1,
    )
    assert run["plan_revision"] == 1
    assert type(run["fencing_token"]) is int and run["fencing_token"] >= 0
    assert run["lease_expires_at"] is None
    assert run["cancel_intent"] == 0
    assert db.q("SELECT * FROM full_auto_run_action_receipt WHERE run_id = ?", run_id) == []
    assert resource_full_auto.get_run_for_session(session_id)["run_id"] == run_id

    committed = _all_rows()
    replay_json, was_new = guided_sessions.create_or_replay(
        request, config=lambda: main.CONFIG,
    )
    assert was_new is False
    assert replay_json == response_json
    assert _all_rows() == committed

    failed_payload = {**payload, "request_id": str(uuid.uuid4())}
    failed_request = guided_sessions.normalize_request(failed_payload)
    before_failure = _all_rows()
    original_run = db.run
    saw_run_insert = False

    def fail_after_run_insert(sql, *args):
        nonlocal saw_run_insert
        result = original_run(sql, *args)
        if " ".join(sql.split()).upper().startswith("INSERT INTO FULL_AUTO_RUN "):
            saw_run_insert = True
            raise sqlite3.OperationalError("injected failure after persisted run state")
        return result

    monkeypatch.setattr(db, "run", fail_after_run_insert)
    with pytest.raises(sqlite3.OperationalError, match="injected failure after persisted run state"):
        guided_sessions.create_or_replay(failed_request, config=lambda: main.CONFIG)
    assert saw_run_insert is True
    assert _all_rows() == before_failure


def test_guided_created_authorization_can_claim_and_resume_from_its_persisted_receipt(
    client, seeded, monkeypatch,
):
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    request = guided_sessions.normalize_request(payload)
    response_json, was_new = guided_sessions.create_or_replay(
        request, config=lambda: main.CONFIG,
    )
    assert was_new is True
    created = json.loads(response_json)
    run_id = created["full_auto_run_id"]
    receipt = db.one("SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id)
    authorization = json.loads(receipt["authorization_json"])
    assert authorization["authorization_version"] == 1
    assert authorization["summary"]["policy_version"] == "resource-full-auto-v1"
    assert authorization["authorization_expires_at"] == receipt["authorization_expires_at"]

    owner = resource_full_auto.claim_run(
        run_id, now_text=receipt["scope_issued_at"],
    )
    claimed_row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, "guided-created-cancel", claimed_row["revision"], 1,
        work_pending=False, now_text=receipt["scope_issued_at"],
    )
    assert cancelled.view["state"] == "cancelled"
    assert owner.epoch == 1

    resumed = resource_full_auto.resume_run(
        run_id, "guided-created-resume", cancelled.view["revision"], 1,
        eligibility=_resume_checks(), now_text=receipt["scope_issued_at"],
    )
    assert resumed.view["state"] == "active"
    assert resumed.view["epoch"] == 2
    assert resumed.claim is not None


def test_run_creation_rejects_receipt_session_mismatch_at_service_and_sql_boundaries(client):
    _run_one, session_one = _seed_run("mismatch-one")
    _model_two, session_two = _seed_session("mismatch-two")
    run_two = f"run-{uuid.uuid4().hex}"
    db.run(
        """INSERT INTO full_auto_run_receipt (
               run_id, session_id, authorization_json, scope_digest,
               dependencies_digest, plan_digest, scope_issued_at,
               authorization_expires_at, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        run_two, session_two, _authorization_json(), "a" * 64, "b" * 64, "c" * 64,
        START, AUTHORIZATION_EXPIRY, START,
    )

    before_service = _all_rows()
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        with db.transaction():
            resource_full_auto.create_run_in_transaction(
                run_two, session_one, created_at=START,
            )
    assert error.value.code == "run_receipt_missing"
    assert _all_rows() == before_service

    _control(_run_one, "cancel", "retire-session-one", pending=False)
    before_sql = _all_rows()
    row = (
        run_two, session_one, "active", "prepare", 1, 1, 1, 1,
        None, 0, None, None, "{}", START, START,
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.conn().execute(
            """INSERT INTO full_auto_run (
                   run_id, session_id, state, phase, revision, epoch, plan_revision,
                   fencing_token, lease_expires_at, cancel_intent, blocker_code,
                   resume_phase, evidence_json, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
    assert _all_rows() == before_sql


def test_backfilled_run_with_a_changed_plan_cannot_be_claimed(tmp_path):
    path = tmp_path / "legacy-run.sqlite3"
    db.connect(path)
    _model_id, session_id = _seed_session("legacy-edited-plan")
    run_id = f"legacy-{uuid.uuid4().hex}"
    db.run(
        """INSERT INTO full_auto_run_receipt (
               run_id, session_id, authorization_json, scope_digest,
               dependencies_digest, plan_digest, scope_issued_at,
               authorization_expires_at, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        run_id, session_id, _authorization_json(), "a" * 64, "b" * 64, "c" * 64,
        START, AUTHORIZATION_EXPIRY, START,
    )
    db.run(
        "UPDATE session_plan SET plan_revision = 2, updated_at = ? WHERE session_id = ?",
        "2026-10-01T12:00:01+00:00", session_id,
    )
    db.conn().execute("DROP TABLE full_auto_run_action_receipt")
    db.conn().execute("DROP TABLE full_auto_run")
    db.conn().commit()
    db.conn().close()

    db.connect(path)
    run = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert run["plan_revision"] == 1
    assert db.one("SELECT plan_revision FROM session_plan WHERE session_id = ?", session_id)[
        "plan_revision"
    ] == 2

    with pytest.raises(resource_full_auto.FullAutoRunError):
        resource_full_auto.claim_run(run_id, now_text=START)
    recovered = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert recovered["lease_expires_at"] is None
    assert recovered["state"] == "blocked"
    assert recovered["blocker_code"] == "plan_revision_stale"


def test_owned_phase_write_checks_the_persisted_plan_revision(client):
    run_id, session_id = _seed_run("persisted-plan-boundary")
    owner = resource_full_auto.claim_run(run_id, now_text=START)
    db.run(
        "UPDATE session_plan SET plan_revision = 2, updated_at = ? WHERE session_id = ?",
        "2026-10-01T12:00:01+00:00", session_id,
    )
    before_phase = _all_rows()
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.advance_run(
            owner, "active", phase="validate", now_text="2026-10-01T12:00:02+00:00",
        )
    assert error.value.code == "plan_revision_stale"
    assert _all_rows() == before_phase


def test_resume_checks_the_persisted_plan_even_when_backend_evidence_claims_it_is_current(
    client,
):
    run_id, session_id = _seed_run("resume-plan-boundary")
    cancelled = _control(run_id, "cancel", "cancel-plan-boundary", pending=False)
    db.run(
        "UPDATE session_plan SET plan_revision = 2, updated_at = ? WHERE session_id = ?",
        "2026-10-01T12:00:01+00:00", session_id,
    )
    before_resume = _all_rows()
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.resume_run(
            run_id, "resume-stale-plan", cancelled.view["revision"], 1,
            eligibility=_resume_checks(current_plan_revision=1),
            now_text="2026-10-01T12:00:02+00:00",
        )
    assert error.value.code == "plan_revision_stale"
    assert _all_rows() == before_resume


def test_resume_checks_the_saved_authorization_deadline_even_when_backend_evidence_says_valid(
    client,
):
    run_id, _session_id = _seed_run("resume-expired-authorization")
    cancelled = _control(run_id, "cancel", "cancel-expiry-boundary", pending=False)
    before_expired_resume = _all_rows()
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.resume_run(
            run_id, "resume-after-expiry", cancelled.view["revision"], 1,
            eligibility=_resume_checks(authorization_valid=True),
            now_text="2026-10-09T12:00:00+00:00",
        )
    assert error.value.code == "authorization_expired"
    assert _all_rows() == before_expired_resume


def test_competing_claims_and_expired_owner_writes_are_fenced(client):
    run_id, _session_id = _seed_run("claim-race")
    barrier = threading.Barrier(2)

    def claim_once():
        barrier.wait(timeout=5)
        try:
            return resource_full_auto.claim_run(run_id, now_text=START)
        except resource_full_auto.FullAutoRunError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=10)
            for future in (pool.submit(claim_once), pool.submit(claim_once))
        ]
    owners = [value for value in results if isinstance(value, resource_full_auto.RunClaim)]
    assert len(owners) == 1
    assert results.count("run_already_claimed") == 1

    old_owner = owners[0]
    takeover_time = "2026-10-01T12:01:01+00:00"
    new_owner = resource_full_auto.claim_run(run_id, now_text=takeover_time)
    assert new_owner.fencing_token == old_owner.fencing_token + 1
    before_stale_write = _all_rows()
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.advance_run(
            old_owner, "active", phase="validate", now_text=takeover_time,
        )
    assert error.value.code == "run_owner_stale"
    assert _all_rows() == before_stale_write


@pytest.mark.parametrize(
    ("source_state", "event"),
    [
        ("waiting_authoring", "block"),
        ("waiting_runner", "block"),
        ("waiting_authoring", "internal_failure"),
        ("waiting_runner", "internal_failure"),
        ("waiting_authoring", "cancel_pending_then_settle"),
        ("waiting_runner", "cancel_pending_then_settle"),
        ("waiting_authoring", "cancel_settled"),
        ("waiting_runner", "cancel_settled"),
        ("waiting_authoring", "cross_wait"),
        ("waiting_runner", "cross_wait"),
    ],
)
def test_each_waiting_state_supports_only_its_decision9_events(client, source_state, event):
    run_id, _session_id = _seed_run(f"{source_state}-{event}")
    first_owner = resource_full_auto.claim_run(run_id, now_text=START)
    waiting = resource_full_auto.advance_run(first_owner, source_state, now_text=START)
    assert waiting["state"] == source_state
    owner = resource_full_auto.claim_run(run_id, now_text=START)
    row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)

    if event == "block":
        result = resource_full_auto.advance_run(
            owner, "blocked", blocker_code="dependency_changed", now_text=START,
        )
        assert result["state"] == "blocked"
        assert result["blocker_code"] == "dependency_changed"
    elif event == "internal_failure":
        result = resource_full_auto.complete_run(
            owner,
            resource_full_auto.CompletionEvidence("internal_failure", unresolved_work=0),
            now_text=START,
        )
        assert result["state"] == "failed"
    elif event == "cancel_pending_then_settle":
        cancelled = resource_full_auto.cancel_run(
            run_id, f"{source_state}-pending", row["revision"], row["plan_revision"],
            work_pending=True, now_text=START,
        )
        assert cancelled.view["state"] == "cancel_requested"
        reconcile_owner = resource_full_auto.claim_run(
            run_id, now_text="2026-10-01T12:00:01+00:00",
        )
        result = resource_full_auto.reconcile_cancellation(
            reconcile_owner,
            resource_full_auto.CancellationEvidence(
                "settled", "2026-10-01T12:00:02+00:00", 0,
            ),
            now_text="2026-10-01T12:00:02+00:00",
        )
        assert result["state"] == "cancelled"
        assert result["cancel_intent"] is True
    elif event == "cancel_settled":
        result = resource_full_auto.cancel_run(
            run_id, f"{source_state}-settled", row["revision"], row["plan_revision"],
            work_pending=False, now_text=START,
        )
        assert result.view["state"] == "cancelled"
    else:
        other_wait = "waiting_runner" if source_state == "waiting_authoring" else "waiting_authoring"
        before_refusal = _all_rows()
        with pytest.raises(resource_full_auto.FullAutoRunError) as error:
            resource_full_auto.advance_run(owner, other_wait, now_text=START)
        assert error.value.code == "run_transition_refused"
        assert _all_rows() == before_refusal


@pytest.mark.parametrize(
    "event",
    ["cancel_pending", "cancel_settled", "resume_waiting_authoring", "resume_waiting_runner"],
)
def test_blocked_without_cancel_intent_can_cancel_or_resume_to_required_wait(event, client):
    run_id, _session_id = _seed_run(f"blocked-{event}")
    owner = resource_full_auto.claim_run(run_id, now_text=START)
    blocked = resource_full_auto.advance_run(
        owner, "blocked", blocker_code="correctable_input", now_text=START,
    )
    assert blocked["cancel_intent"] is False

    if event.startswith("cancel"):
        row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
        result = resource_full_auto.cancel_run(
            run_id, event, row["revision"], row["plan_revision"],
            work_pending=event == "cancel_pending", now_text=START,
        )
        assert result.view["state"] == (
            "cancel_requested" if event == "cancel_pending" else "cancelled"
        )
    else:
        target = event.removeprefix("resume_")
        result = resource_full_auto.resume_run(
            run_id, event, blocked["revision"], 1,
            eligibility=_resume_checks(next_state=target), now_text=START,
        )
        assert result.view["state"] == target
        assert result.view["epoch"] == blocked["epoch"]
        assert result.claim is not None


@pytest.mark.parametrize("terminal_state", ["cancelled", "failed", "completed_with_errors"])
@pytest.mark.parametrize("resume_state", ["active", "waiting_authoring", "waiting_runner"])
def test_each_resumable_terminal_state_opens_a_new_epoch_to_each_required_state(
    terminal_state, resume_state, client,
):
    run_id, _session_id = _seed_run(f"{terminal_state}-{resume_state}")
    if terminal_state == "cancelled":
        stopped = resource_full_auto.cancel_run(
            run_id, "initial-cancel", 1, 1, work_pending=False, now_text=START,
        ).view
    else:
        owner = resource_full_auto.claim_run(run_id, now_text=START)
        for phase in ("validate", "approve", "submit", "execute"):
            resource_full_auto.advance_run(owner, "active", phase=phase, now_text=START)
        outcome = "all_shots_failed" if terminal_state == "failed" else "mixed"
        stopped = resource_full_auto.complete_run(
            owner,
            resource_full_auto.CompletionEvidence(outcome, unresolved_work=0),
            now_text=START,
        )
    assert stopped["state"] == terminal_state

    resumed = resource_full_auto.resume_run(
        run_id, f"resume-{terminal_state}-{resume_state}", stopped["revision"], 1,
        eligibility=_resume_checks(next_state=resume_state), now_text=START,
    )
    assert resumed.view["state"] == resume_state
    assert resumed.view["run_id"] == run_id
    assert resumed.view["epoch"] == stopped["epoch"] + 1
    assert resumed.claim is not None


@pytest.mark.parametrize("saved_state", ["active", "waiting_authoring", "waiting_runner"])
def test_expired_leases_recover_each_nonterminal_state_and_saved_phase(client, saved_state):
    run_id, _session_id = _seed_run(f"recover-{saved_state}")
    owner = resource_full_auto.claim_run(run_id, now_text=START)
    if saved_state != "active":
        resource_full_auto.advance_run(owner, saved_state, now_text=START)
        owner = resource_full_auto.claim_run(run_id, now_text=START)
    prior = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    takeover_at = "2026-10-01T12:01:01+00:00"
    recovery = resource_full_auto.claim_run(run_id, now_text=takeover_at)
    assert recovery.requires_reconciliation is True
    result, owner = resource_full_auto.reconcile_expired_lease(
        recovery,
        resource_full_auto.LeaseReconciliationEvidence("safe_to_continue", unresolved_work=0),
        now_text=takeover_at,
    )
    assert result["state"] == saved_state
    assert result["phase"] == prior["phase"]
    assert result["epoch"] == prior["epoch"]
    assert owner is not None and owner.requires_reconciliation is False


def test_expired_cancel_lease_never_recovers_as_ordinary_work_or_unblocks(client):
    run_id, _session_id = _seed_run("recover-cancellation")
    cancel = resource_full_auto.cancel_run(
        run_id, "cancel-before-expiry", 1, 1, work_pending=True, now_text=START,
    )
    first_owner = resource_full_auto.claim_run(
        run_id, now_text="2026-10-01T12:00:01+00:00",
    )
    takeover_at = "2026-10-01T12:01:02+00:00"
    recovery = resource_full_auto.claim_run(run_id, now_text=takeover_at)
    assert recovery.requires_reconciliation is True
    before_refusal = _all_rows()
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.reconcile_expired_lease(
            recovery,
            resource_full_auto.LeaseReconciliationEvidence("safe_to_continue", unresolved_work=0),
            now_text=takeover_at,
        )
    assert error.value.code == "cancel_reconciliation_required"
    assert _all_rows() == before_refusal

    settled = resource_full_auto.reconcile_cancellation(
        recovery,
        resource_full_auto.CancellationEvidence("settled", takeover_at, 0),
        now_text=takeover_at,
    )
    assert settled["state"] == "cancelled"
    assert settled["cancel_intent"] is True
    assert first_owner.requires_reconciliation is True
    assert cancel.view["state"] == "cancel_requested"


def test_nonterminal_session_index_rejects_a_second_persisted_run_row(client):
    run_id, session_id = _seed_run("one-nonterminal-session")
    before = _all_rows()
    db.conn().execute("PRAGMA foreign_keys = OFF")
    try:
        with db.transaction():
            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed: full_auto_run.session_id"):
                db.conn().execute(
                    """INSERT INTO full_auto_run (
                           run_id, session_id, state, phase, revision, epoch, plan_revision,
                           fencing_token, lease_expires_at, lease_started_at, cancel_intent,
                           cancel_intent_at, blocker_code, resume_phase, evidence_json,
                           created_at, updated_at
                       ) VALUES (?, ?, 'active', 'prepare', 1, 1, 1, 1, NULL, NULL, 0,
                                 NULL, NULL, NULL, '{}', ?, ?)""",
                    (f"second-{run_id}", session_id, START, START),
                )
    finally:
        db.conn().execute("PRAGMA foreign_keys = ON")
    assert _all_rows() == before


@pytest.mark.parametrize("target", ["succeeded", "completed_with_errors", "failed", "cancelled", "cancel_requested"])
def test_all_disallowed_direct_state_transitions_are_zero_write(target, client):
    run_id, _session_id = _seed_run(f"direct-refusal-{target}")
    owner = resource_full_auto.claim_run(run_id, now_text=START)
    before = _all_rows()
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.advance_run(owner, target, now_text=START)
    assert error.value.code == "run_transition_refused"
    assert _all_rows() == before


@pytest.mark.parametrize(
    ("authority_issue", "expires_at", "drift_plan"),
    [
        ("plan_revision_stale", AUTHORIZATION_EXPIRY, True),
        ("authorization_expired", "2026-10-01T12:00:30+00:00", False),
    ],
)
def test_valid_owner_can_release_to_blocked_after_authority_drift(
    client, authority_issue, expires_at, drift_plan,
):
    run_id, session_id = _seed_run(
        f"block-after-{authority_issue}", authorization_expires_at=expires_at,
    )
    owner = resource_full_auto.claim_run(run_id, now_text=START)
    block_time = "2026-10-01T12:00:31+00:00"
    if drift_plan:
        db.run(
            "UPDATE session_plan SET plan_revision = 2, updated_at = ? WHERE session_id = ?",
            "2026-10-01T12:00:01+00:00", session_id,
        )
    before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)

    blocked = resource_full_auto.advance_run(
        owner, "blocked", blocker_code=authority_issue, now_text=block_time,
    )

    assert blocked["state"] == "blocked"
    assert blocked["blocker_code"] == authority_issue
    after = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert after["revision"] == before["revision"] + 1
    assert after["fencing_token"] == before["fencing_token"] + 1
    assert after["lease_expires_at"] is None
    assert after["lease_started_at"] is None


def test_cancel_intent_stays_in_read_only_reconciliation_and_old_cancel_replay_is_historical(
    client,
):
    run_id, session_id = _seed_run("cancel-reconcile")
    shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, status, filename, created_at)
           VALUES (?, 0, 'done', 'retained/output.png', ?)""",
        session_id, START,
    )
    shot_before = db.one("SELECT * FROM shot WHERE id = ?", shot_id)
    cancelled_request = _control(run_id, "cancel", "cancel-epoch-one", pending=True)
    assert cancelled_request.view["state"] == "cancel_requested"
    initial_cancel_view = cancelled_request.view

    owner = resource_full_auto.claim_run(run_id, now_text="2026-10-01T12:00:01+00:00")
    blocked = resource_full_auto.reconcile_cancellation(
        owner,
        resource_full_auto.CancellationEvidence(
            "unknown", "2026-10-01T12:00:02+00:00", 1,
            blocker_code="comfy_unavailable",
            attempt_ids=("attempt-a",), shot_ids=("shot-a",),
        ),
        now_text="2026-10-01T12:00:02+00:00",
    )
    assert blocked["state"] == "blocked"
    assert blocked["cancel_intent"] is True
    row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert row["lease_expires_at"] is None
    evidence = json.loads(row["evidence_json"])
    assert evidence["attempt_ids"] == ["attempt-a"]
    assert evidence["shot_ids"] == ["shot-a"]

    # A new Cancel request while blocked with intent records no new remote work.
    prior_state = dict(row)
    repeated = resource_full_auto.cancel_run(
        run_id, "cancel-again", int(row["revision"]), int(row["plan_revision"]),
        work_pending=None, now_text="2026-10-01T12:00:03+00:00",
    )
    assert repeated.view["state"] == "blocked"
    assert dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)) == prior_state

    retry = resource_full_auto.resume_run(
        run_id, "reconcile-unknown", int(repeated.view["revision"]), 1,
        eligibility=_resume_checks(), now_text="2026-10-01T12:00:04+00:00",
    )
    assert retry.view["state"] == "blocked"
    assert retry.claim is not None and retry.claim.requires_reconciliation is True
    before_refusal = _all_rows()
    with pytest.raises(resource_full_auto.FullAutoRunError):
        resource_full_auto.advance_run(
            retry.claim, "active", now_text="2026-10-01T12:00:04+00:00",
        )
    with pytest.raises(resource_full_auto.FullAutoRunError):
        resource_full_auto.complete_run(
            retry.claim,
            resource_full_auto.CompletionEvidence("all_succeeded", unresolved_work=1),
            now_text="2026-10-01T12:00:04+00:00",
        )
    assert _all_rows() == before_refusal

    blocked_again = resource_full_auto.reconcile_cancellation(
        retry.claim,
        resource_full_auto.CancellationEvidence(
            "unknown", "2026-10-01T12:00:05+00:00", 1,
            blocker_code="execution_outcome_unknown",
        ),
        now_text="2026-10-01T12:00:05+00:00",
    )
    assert blocked_again["state"] == "blocked"
    retry = resource_full_auto.resume_run(
        run_id, "reconcile-running", blocked_again["revision"], 1,
        eligibility=None, now_text="2026-10-01T12:00:06+00:00",
    )
    running = resource_full_auto.reconcile_cancellation(
        retry.claim,
        resource_full_auto.CancellationEvidence(
            "running", "2026-10-01T12:00:07+00:00", 1,
        ),
        now_text="2026-10-01T12:00:07+00:00",
    )
    assert running["state"] == "cancel_requested"
    settled = resource_full_auto.reconcile_cancellation(
        retry.claim,
        resource_full_auto.CancellationEvidence(
            "settled", "2026-10-01T12:00:08+00:00", 0,
        ),
        now_text="2026-10-01T12:00:08+00:00",
    )
    assert settled["state"] == "cancelled"
    assert db.one("SELECT * FROM shot WHERE id = ?", shot_id) == shot_before

    epoch_two = resource_full_auto.resume_run(
        run_id, "resume-epoch-two", settled["revision"], 1,
        eligibility=_resume_checks(), now_text="2026-10-01T12:00:09+00:00",
    )
    assert epoch_two.view["run_id"] == run_id
    assert epoch_two.view["session_id"] == session_id
    assert epoch_two.view["epoch"] == settled["epoch"] + 1
    after_new_epoch = _all_rows()
    replay = resource_full_auto.cancel_run(
        run_id, "cancel-epoch-one", 1, 1, work_pending=None,
        now_text="2026-10-01T12:00:10+00:00",
    )
    assert replay.replayed is True
    assert replay.view == initial_cancel_view
    assert _all_rows() == after_new_epoch


def test_cancellation_committed_before_settlement_fences_stale_completion(
    client, monkeypatch,
):
    run_id, session_id = _seed_run("cancel-settlement-race")
    shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, status, filename, created_at)
           VALUES (?, 0, 'done', 'retained/finished.png', ?)""",
        session_id, START,
    )
    claim = resource_full_auto.claim_run(run_id, now_text=START)
    for phase in ("validate", "approve", "submit", "execute"):
        resource_full_auto.advance_run(claim, "active", phase=phase, now_text=START)
    shot_before = db.one("SELECT * FROM shot WHERE id = ?", shot_id)

    completion_waiting = threading.Event()
    release_completion = threading.Event()
    worker_role = threading.local()
    real_transaction = db.transaction

    @contextmanager
    def gate_completion_transaction():
        if getattr(worker_role, "is_completion", False):
            completion_waiting.set()
            assert release_completion.wait(timeout=5), "completion transaction was not released"
        with real_transaction():
            yield

    monkeypatch.setattr(db, "transaction", gate_completion_transaction)

    def settle():
        worker_role.is_completion = True
        return resource_full_auto.complete_run(
            claim,
            resource_full_auto.CompletionEvidence("all_succeeded", unresolved_work=0),
            now_text="2026-10-01T12:00:01+00:00",
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending_completion = pool.submit(settle)
        assert completion_waiting.wait(timeout=5)
        cancel = resource_full_auto.cancel_run(
            run_id, "cancel-during-settlement", 6, 1,
            work_pending=True, now_text="2026-10-01T12:00:01+00:00",
        )
        release_completion.set()
        with pytest.raises(resource_full_auto.FullAutoRunError) as error:
            pending_completion.result(timeout=10)

    assert error.value.code == "run_owner_stale"
    assert cancel.view["state"] == "cancel_requested"
    assert cancel.view["cancel_intent"] is True
    row = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert row["state"] == "cancel_requested"
    assert row["lease_expires_at"] is None
    assert db.one("SELECT * FROM shot WHERE id = ?", shot_id) == shot_before


def test_cancellation_rejects_settlement_observed_before_the_current_epoch_intent(client):
    run_id, _session_id = _seed_run("stale-cancel-observation")
    first_cancel = resource_full_auto.cancel_run(
        run_id, "first-cancel", 1, 1, work_pending=True,
        now_text="2026-10-01T12:00:01+00:00",
    )
    first_owner = resource_full_auto.claim_run(
        run_id, now_text="2026-10-01T12:00:02+00:00",
    )
    settled = resource_full_auto.reconcile_cancellation(
        first_owner,
        resource_full_auto.CancellationEvidence(
            "settled", "2026-10-01T12:00:03+00:00", 0,
        ),
        now_text="2026-10-01T12:00:03+00:00",
    )
    assert settled["state"] == "cancelled"

    epoch_two = resource_full_auto.resume_run(
        run_id, "resume-second-epoch", settled["revision"], 1,
        eligibility=_resume_checks(), now_text="2026-10-01T12:00:04+00:00",
    )
    next_cancel = resource_full_auto.cancel_run(
        run_id, "second-cancel", epoch_two.view["revision"], 1,
        work_pending=True, now_text="2026-10-01T12:00:05+00:00",
    )
    second_owner = resource_full_auto.claim_run(
        run_id, now_text="2026-10-01T12:00:06+00:00",
    )
    before_stale_observation = _all_rows()

    with pytest.raises(resource_full_auto.FullAutoRunError):
        resource_full_auto.reconcile_cancellation(
            second_owner,
            resource_full_auto.CancellationEvidence(
                "settled", "2026-10-01T12:00:03+00:00", 0,
            ),
            now_text="2026-10-01T12:00:07+00:00",
        )

    assert _all_rows() == before_stale_observation
    current = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert current["epoch"] == 2
    assert current["state"] == "cancel_requested"
    assert current["cancel_intent"] == 1
    assert current["revision"] == next_cancel.view["revision"] + 1


def test_cancel_reconciliation_remains_available_after_plan_drift_and_authorization_expiry(
    client,
):
    run_id, session_id = _seed_run("expired-cancel-reconciliation")
    expired_now = "2026-10-09T12:00:00+00:00"
    cancel = resource_full_auto.cancel_run(
        run_id, "cancel-after-expiry", 1, 1, work_pending=True,
        now_text=expired_now,
    )
    claim = resource_full_auto.claim_run(
        run_id, now_text="2026-10-09T12:00:01+00:00",
    )
    blocked = resource_full_auto.reconcile_cancellation(
        claim,
        resource_full_auto.CancellationEvidence(
            "unknown", "2026-10-09T12:00:02+00:00", 1,
            blocker_code="execution_outcome_unknown",
        ),
        now_text="2026-10-09T12:00:02+00:00",
    )
    assert blocked["state"] == "blocked"

    db.run(
        "UPDATE session_plan SET plan_revision = 2, updated_at = ? WHERE session_id = ?",
        "2026-10-09T12:00:03+00:00", session_id,
    )
    reconciliation = resource_full_auto.resume_run(
        run_id, "reconcile-after-expiry", blocked["revision"], 1,
        eligibility=None, now_text="2026-10-09T12:00:04+00:00",
    )
    assert reconciliation.view["state"] == "blocked"
    assert reconciliation.claim is not None
    assert reconciliation.claim.requires_reconciliation is True
    settled = resource_full_auto.reconcile_cancellation(
        reconciliation.claim,
        resource_full_auto.CancellationEvidence(
            "settled", "2026-10-09T12:00:05+00:00", 0,
        ),
        now_text="2026-10-09T12:00:05+00:00",
    )
    assert settled["state"] == "cancelled"
    assert settled["cancel_intent"] is True
