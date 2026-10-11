"""Independent acceptance checks for Full Auto submission recovery boundaries."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
import main
from backend import resource_full_auto, session_plan
from runner import Runner
import test_full_auto_preparation_coordinator as preparation


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "submission-independent-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "submission-independent-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)


def _reopen_database() -> None:
    path = db.conn().execute("PRAGMA database_list").fetchone()["file"]
    db.conn().commit()
    db.conn().close()
    db.connect(Path(path))


def _recover_owner(run_id: str, phase: str) -> resource_full_auto.RunClaim:
    db.run(
        "UPDATE full_auto_run SET lease_expires_at = ?, lease_started_at = ? WHERE run_id = ?",
        "2000-01-01T00:00:00+00:00", "1999-12-31T23:59:00+00:00", run_id,
    )
    reconciliation = resource_full_auto.claim_run(run_id)
    view, owner = resource_full_auto.reconcile_expired_lease(
        reconciliation,
        resource_full_auto.LeaseReconciliationEvidence("safe_to_continue", 0),
    )
    assert view["phase"] == phase
    assert owner is not None
    return owner


def _approved_run(client, seeded, monkeypatch, *, photo_count: int = 4):
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=photo_count,
    )
    preparation._install_provider(monkeypatch, session_id, revision, targets)
    prepared = asyncio.run(
        main._run_full_auto_preparation(resource_full_auto.claim_run(run_id)),
    )
    assert (prepared["state"], prepared["phase"]) == ("active", "validate")
    validation_owner = _recover_owner(run_id, "validate")
    reviewed, owner = main._run_full_auto_review_and_approve(validation_owner)
    assert (reviewed["state"], reviewed["phase"]) == ("active", "submit")
    assert owner is not None
    return session_id, run_id, revision, targets, owner


def _linked_rows(session_id: int, revision: int) -> list[dict]:
    return db.q(
        "SELECT p.take_id, p.final_prompt, p.linked_shot_id, s.prompt, s.id AS shot_id "
        "FROM prepared_take p LEFT JOIN shot s ON s.id = p.linked_shot_id "
        "WHERE p.session_id = ? AND p.plan_revision = ? ORDER BY p.rowid",
        session_id, revision,
    )


async def _start_and_wait(runner: Runner, session_id: int, claim=None) -> None:
    if claim is None:
        runner.start(session_id)
    else:
        runner.start(session_id, _full_auto_claim=claim)
    if runner._task is not None:
        await runner._task


@pytest.mark.parametrize("interleaving", ["feature_flag", "lease_expiry"])
def test_receipt_rechecks_authority_after_review_and_resumes_the_committed_link(
    client, seeded, monkeypatch, make_runner, interleaving,
):
    session_id, run_id, revision, targets, owner = _approved_run(
        client, seeded, monkeypatch,
    )
    original_review = session_plan._full_auto_duplicate_review_evidence
    original_receipt = resource_full_auto.record_submission_receipt
    original_now = db.now
    in_receipt = False
    injected = False

    def record_receipt(*args, **kwargs):
        nonlocal in_receipt
        in_receipt = True
        try:
            return original_receipt(*args, **kwargs)
        finally:
            in_receipt = False

    with monkeypatch.context() as patcher:
        def review(*args, **kwargs):
            nonlocal injected
            evidence = original_review(*args, **kwargs)
            if in_receipt and not injected:
                injected = True
                if interleaving == "feature_flag":
                    patcher.setitem(main.CONFIG, "resource_planning_enabled", False)
                else:
                    future = datetime.fromisoformat(
                        original_now().replace("Z", "+00:00"),
                    ) + timedelta(seconds=120)
                    patcher.setattr(
                        db, "now", lambda: future.isoformat(timespec="seconds"),
                    )
            return evidence

        patcher.setattr(session_plan, "_full_auto_duplicate_review_evidence", review)
        patcher.setattr(resource_full_auto, "record_submission_receipt", record_receipt)
        with pytest.raises(
            session_plan.ResourcePlanningDisabled
            if interleaving == "feature_flag"
            else resource_full_auto.FullAutoRunError,
        ) as failure:
            main._run_full_auto_submission(owner)

    if interleaving == "lease_expiry":
        assert failure.value.code == "run_lease_expired"
    assert injected is True
    linked_before_restart = _linked_rows(session_id, revision)
    assert [row["take_id"] for row in linked_before_restart] == targets
    assert linked_before_restart[0]["linked_shot_id"] is not None
    assert linked_before_restart[0]["prompt"] == linked_before_restart[0]["final_prompt"]
    assert all(row["linked_shot_id"] is None for row in linked_before_restart[1:])
    assert db.q(
        "SELECT * FROM full_auto_submission_receipt WHERE run_id = ?", run_id,
    ) == []
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []

    runner, fake = make_runner()
    status_before = db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"]
    with pytest.raises(resource_full_auto.FullAutoRunError):
        asyncio.run(_start_and_wait(runner, session_id, owner))
    assert runner._task is None
    assert fake.attempts == 0
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] == status_before

    _reopen_database()
    if interleaving == "feature_flag":
        main.CONFIG["resource_planning_enabled"] = True
    recovered_owner = _recover_owner(run_id, "submit")
    view, execute_owner = main._run_full_auto_submission(recovered_owner)
    assert (view["state"], view["phase"]) == ("active", "execute")
    assert execute_owner is not None

    linked_after_restart = _linked_rows(session_id, revision)
    assert [row["take_id"] for row in linked_after_restart] == targets
    assert all(row["linked_shot_id"] is not None for row in linked_after_restart)
    assert all(row["prompt"] == row["final_prompt"] for row in linked_after_restart)
    assert linked_after_restart[0]["shot_id"] == linked_before_restart[0]["shot_id"]
    assert len({row["shot_id"] for row in linked_after_restart}) == len(targets)
    assert [row["take_id"] for row in db.q(
        "SELECT take_id FROM full_auto_submission_receipt WHERE run_id = ? "
        "ORDER BY target_index", run_id,
    )] == targets

    asyncio.run(_start_and_wait(runner, session_id, execute_owner))
    assert fake.attempts == len(targets)
    assert db.q(
        "SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == [{"run_id": run_id}]
    shot_prompts = [row["prompt"] for row in db.q(
        "SELECT prompt FROM shot WHERE session_id = ? ORDER BY id", session_id,
    )]
    observed_prompts = [
        value
        for graph in fake.graphs
        for node in graph.values()
        for value in node.get("inputs", {}).values()
        if value in shot_prompts
    ]
    assert observed_prompts == shot_prompts
    assert all(row["status"] == "done" for row in db.q(
        "SELECT status FROM shot WHERE session_id = ? ORDER BY id", session_id,
    ))


def test_runner_refuses_a_valid_existing_link_that_precedes_target_order(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, revision, targets, owner = _approved_run(
        client, seeded, monkeypatch,
    )
    preexisting = session_plan.submit_prepared_take(
        session_id, revision, targets[1], _full_auto_claim=owner,
    )
    view, execute_owner = main._run_full_auto_submission(owner)
    assert (view["state"], view["phase"]) == ("active", "execute")
    assert execute_owner is not None
    rows = _linked_rows(session_id, revision)
    assert [row["take_id"] for row in rows] == targets
    assert rows[1]["shot_id"] == preexisting["shot_id"]
    assert rows[1]["shot_id"] < rows[0]["shot_id"]

    runner, fake = make_runner()
    status_before = db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"]
    with pytest.raises(resource_full_auto.FullAutoRunError) as refusal:
        asyncio.run(_start_and_wait(runner, session_id, execute_owner))
    assert refusal.value.code == "full_auto_execution_state_stale"
    assert runner._task is None
    assert fake.attempts == 0
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] == status_before


@pytest.mark.parametrize("interleaving", ["feature_flag", "lease_expiry"])
def test_runner_admission_rechecks_flag_and_lease_after_review(
    client, seeded, monkeypatch, make_runner, interleaving,
):
    session_id, run_id, _revision, _targets, submit_owner = _approved_run(
        client, seeded, monkeypatch,
    )
    view, execute_owner = main._run_full_auto_submission(submit_owner)
    assert view["phase"] == "execute"
    assert execute_owner is not None
    run_before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    original_review = session_plan._full_auto_duplicate_review_evidence
    original_now = db.now
    injected = False

    with monkeypatch.context() as patcher:
        def review(*args, **kwargs):
            nonlocal injected
            evidence = original_review(*args, **kwargs)
            if not injected:
                injected = True
                if interleaving == "feature_flag":
                    patcher.setitem(main.CONFIG, "resource_planning_enabled", False)
                else:
                    future = datetime.fromisoformat(
                        original_now().replace("Z", "+00:00"),
                    ) + timedelta(seconds=120)
                    patcher.setattr(
                        db, "now", lambda: future.isoformat(timespec="seconds"),
                    )
            return evidence

        patcher.setattr(session_plan, "_full_auto_duplicate_review_evidence", review)
        runner, fake = make_runner()
        with pytest.raises(
            session_plan.ResourcePlanningDisabled
            if interleaving == "feature_flag"
            else resource_full_auto.FullAutoRunError,
        ) as refusal:
            asyncio.run(_start_and_wait(runner, session_id, execute_owner))

    if interleaving == "lease_expiry":
        assert refusal.value.code == "run_lease_expired"
    assert injected is True
    assert runner._task is None
    assert fake.attempts == 0
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == run_before
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] != "running"


@pytest.mark.parametrize("interruption", ["cancel", "fence_takeover"])
def test_obsolete_execution_claim_cannot_admit_runner_after_cancel_or_takeover(
    client, seeded, monkeypatch, make_runner, interruption,
):
    session_id, run_id, _revision, _targets, submit_owner = _approved_run(
        client, seeded, monkeypatch,
    )
    view, old_owner = main._run_full_auto_submission(submit_owner)
    assert view["phase"] == "execute"
    if interruption == "cancel":
        run = db.one(
            "SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id,
        )
        cancelled = resource_full_auto.cancel_run(
            run_id, "independent-execution-cancel", run["revision"],
            run["plan_revision"], work_pending=True,
        )
        assert cancelled.view["state"] == "cancel_requested"
    else:
        _reopen_database()
        current_owner = _recover_owner(run_id, "execute")
        assert current_owner.fencing_token > old_owner.fencing_token

    run_before_refusal = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    runner, fake = make_runner()
    with pytest.raises(resource_full_auto.FullAutoRunError) as refusal:
        asyncio.run(_start_and_wait(runner, session_id, old_owner))
    assert refusal.value.code == "run_owner_stale"
    assert runner._task is None
    assert fake.attempts == 0
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == run_before_refusal
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] != "running"

    if interruption == "fence_takeover":
        asyncio.run(_start_and_wait(runner, session_id, current_owner))
        assert fake.attempts == len(_targets)


def test_runner_rejects_a_missing_prepared_shot_link_without_admission(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, revision, targets, owner = _approved_run(
        client, seeded, monkeypatch,
    )
    view, execute_owner = main._run_full_auto_submission(owner)
    assert view["phase"] == "execute"
    target = db.one(
        "SELECT id FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, revision, targets[0],
    )
    db.run("UPDATE prepared_take SET linked_shot_id = NULL WHERE id = ?", target["id"])

    runner, fake = make_runner()
    with pytest.raises(resource_full_auto.FullAutoRunError) as refusal:
        asyncio.run(_start_and_wait(runner, session_id, execute_owner))
    assert refusal.value.code == "linked_snapshot_invalid"
    assert runner._task is None
    assert fake.attempts == 0
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []


def test_manual_runner_still_works_after_full_auto_is_cancelled(
    client, seeded, monkeypatch, make_runner,
):
    session_id, run_id, revision, targets, _owner = _approved_run(
        client, seeded, monkeypatch,
    )
    run = db.one(
        "SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id,
    )
    cancelled = resource_full_auto.cancel_run(
        run_id, "independent-manual-transfer", run["revision"], run["plan_revision"],
        work_pending=False,
    )
    assert cancelled.view["state"] == "cancelled"
    session_plan.approve_plan_review(session_id, revision)
    manual = session_plan.submit_prepared_take(session_id, revision, targets[0])
    shot_id = manual["shot_id"]

    runner, fake = make_runner()
    asyncio.run(_start_and_wait(runner, session_id))
    assert fake.attempts == 1
    assert db.one("SELECT status FROM shot WHERE id = ?", shot_id)["status"] == "done"
    assert db.q(
        "SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) == []
