"""Task 4.5 acceptance for replayable ordered submission and runner admission."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
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
    monkeypatch.setitem(main.CONFIG, "llm_model", "task45-test-provider")
    monkeypatch.setitem(main.CONFIG, "llm_key", "task45-test-secret")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)


def _approve_and_get_owner(client, seeded, monkeypatch, *, photo_count=4):
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=photo_count,
    )
    preparation._install_provider(monkeypatch, session_id, revision, targets)
    prepared_view = asyncio.run(
        main._run_full_auto_preparation(resource_full_auto.claim_run(run_id)),
    )
    assert (prepared_view["state"], prepared_view["phase"]) == ("active", "validate")
    owner = _resume_validate_owner(run_id)
    view, owner = main._run_full_auto_review_and_approve(owner)
    assert view["state"] == "active"
    assert view["phase"] == "submit"
    assert owner is not None
    return session_id, run_id, revision, targets, owner


def _prepare_full_auto(client, seeded, monkeypatch, *, photo_count=4):
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=photo_count,
    )
    preparation._install_provider(monkeypatch, session_id, revision, targets)
    prepared_view = asyncio.run(
        main._run_full_auto_preparation(resource_full_auto.claim_run(run_id)),
    )
    assert (prepared_view["state"], prepared_view["phase"]) == ("active", "validate")
    return session_id, run_id, revision, targets, _resume_validate_owner(run_id)


def _reopen_database() -> None:
    path = Path(db.conn().execute("PRAGMA database_list").fetchone()["file"])
    db.conn().commit()
    db.conn().close()
    db.connect(path)


def _resume_expired_owner(run_id: str) -> resource_full_auto.RunClaim:
    db.run(
        "UPDATE full_auto_run SET lease_expires_at = ?, lease_started_at = ? WHERE run_id = ?",
        "2000-01-01T00:00:00+00:00", "1999-12-31T23:59:00+00:00", run_id,
    )
    reconciliation = resource_full_auto.claim_run(run_id)
    view, owner = resource_full_auto.reconcile_expired_lease(
        reconciliation,
        resource_full_auto.LeaseReconciliationEvidence("safe_to_continue", 0),
    )
    assert view["phase"] == "submit"
    assert owner is not None
    return owner


def _resume_validate_owner(run_id: str) -> resource_full_auto.RunClaim:
    db.run(
        "UPDATE full_auto_run SET lease_expires_at = ?, lease_started_at = ? WHERE run_id = ?",
        "2000-01-01T00:00:00+00:00", "1999-12-31T23:59:00+00:00", run_id,
    )
    reconciliation = resource_full_auto.claim_run(run_id)
    view, owner = resource_full_auto.reconcile_expired_lease(
        reconciliation,
        resource_full_auto.LeaseReconciliationEvidence("safe_to_continue", 0),
    )
    assert view["phase"] == "validate"
    assert owner is not None
    return owner


def _phase_owner(owner, phase: str) -> resource_full_auto.RunClaim:
    owner = resource_full_auto.renew_run_claim(owner)
    resource_full_auto.advance_run(owner, "active", phase=phase)
    return resource_full_auto.renew_run_claim(owner)


def test_approval_recovers_from_approve_phase_and_reuses_persisted_policy_receipt(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, _targets, owner = _prepare_full_auto(
        client, seeded, monkeypatch, photo_count=4,
    )
    owner = _phase_owner(owner, "approve")

    view, submit_owner = main._run_full_auto_review_and_approve(owner)
    assert (view["state"], view["phase"]) == ("active", "submit")
    assert submit_owner is not None
    assert db.one(
        "SELECT plan_revision FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    )["plan_revision"] == revision
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?", session_id,
    ) is None

    # Recreate an interruption after the durable policy approval but before the
    # phase transition. Recovery must validate and reuse the row, not overwrite it.
    session_id2, run_id2, revision2, _targets2, owner2 = _prepare_full_auto(
        client, seeded, monkeypatch, photo_count=4,
    )
    owner2 = _phase_owner(owner2, "approve")
    session_plan.approve_plan_review(
        session_id2, revision2, _full_auto_claim=owner2,
    )
    approved_at = db.one(
        "SELECT approved_at FROM full_auto_plan_approval WHERE run_id = ?", run_id2,
    )["approved_at"]
    _reopen_database()

    view2, submit_owner2 = main._run_full_auto_review_and_approve(owner2)
    assert (view2["state"], view2["phase"]) == ("active", "submit")
    assert submit_owner2 is not None
    assert db.one(
        "SELECT approved_at FROM full_auto_plan_approval WHERE run_id = ?", run_id2,
    )["approved_at"] == approved_at
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?", session_id2,
    ) is None


@pytest.mark.parametrize("receipt_cut", ["before", "after"])
@pytest.mark.parametrize("target_index", [0, 1, 2, 3])
def test_submission_replays_every_link_or_receipt_cut_after_database_reopen(
    client, seeded, monkeypatch, receipt_cut, target_index,
):
    session_id, run_id, revision, targets, owner = _approve_and_get_owner(
        client, seeded, monkeypatch, photo_count=4,
    )
    frozen_prompts = {
        row["take_id"]: row["final_prompt"]
        for row in db.q(
            "SELECT take_id, final_prompt FROM prepared_take "
            "WHERE session_id = ? AND plan_revision = ? ORDER BY id",
            session_id, revision,
        )
    }
    original_record = resource_full_auto.record_submission_receipt

    def interrupt_at_receipt(claim, index, take_id, shot_id, **kwargs):
        if index == target_index and receipt_cut == "before":
            raise RuntimeError("injected cut before submission receipt")
        receipt = original_record(claim, index, take_id, shot_id, **kwargs)
        if index == target_index and receipt_cut == "after":
            raise RuntimeError("injected lost response after submission receipt")
        return receipt

    with monkeypatch.context() as patcher:
        patcher.setattr(resource_full_auto, "record_submission_receipt", interrupt_at_receipt)
        with pytest.raises(RuntimeError, match="injected"):
            main._run_full_auto_submission(owner)

    linked = db.q(
        "SELECT p.take_id, p.final_prompt, p.linked_shot_id, s.prompt "
        "FROM prepared_take p LEFT JOIN shot s ON s.id = p.linked_shot_id "
        "WHERE p.session_id = ? AND p.plan_revision = ? ORDER BY p.rowid",
        session_id, revision,
    )
    assert [row["take_id"] for row in linked] == targets
    assert all(row["linked_shot_id"] is not None for row in linked[:target_index + 1])
    assert all(row["prompt"] == frozen_prompts[row["take_id"]] for row in linked[:target_index + 1])
    assert len(db.q(
        "SELECT * FROM full_auto_submission_receipt WHERE run_id = ?", run_id,
    )) == target_index + (receipt_cut == "after")
    assert db.one("SELECT phase FROM full_auto_run WHERE run_id = ?", run_id)["phase"] == "submit"

    _reopen_database()
    recovered_owner = _resume_expired_owner(run_id)
    view, execute_owner = main._run_full_auto_submission(recovered_owner)
    assert (view["state"], view["phase"]) == ("active", "execute")
    assert execute_owner is not None

    linked_after_replay = db.q(
        "SELECT p.take_id, p.final_prompt, s.prompt, s.id AS shot_id "
        "FROM prepared_take p JOIN shot s ON s.id = p.linked_shot_id "
        "WHERE p.session_id = ? AND p.plan_revision = ? "
        "ORDER BY s.shot_index, s.id",
        session_id, revision,
    )
    assert [row["take_id"] for row in linked_after_replay] == targets
    assert len({row["shot_id"] for row in linked_after_replay}) == len(targets)
    assert all(row["prompt"] == row["final_prompt"] == frozen_prompts[row["take_id"]]
               for row in linked_after_replay)
    receipts = db.q(
        "SELECT target_index, take_id, shot_id FROM full_auto_submission_receipt "
        "WHERE run_id = ? ORDER BY target_index", run_id,
    )
    assert [row["target_index"] for row in receipts] == list(range(len(targets)))
    assert [row["take_id"] for row in receipts] == targets
    assert len(db.q("SELECT id FROM shot WHERE session_id = ?", session_id)) == len(targets)


@pytest.mark.parametrize("corruption", ["missing_receipt", "linked_prompt"])
def test_runner_admission_rejects_incomplete_or_corrupt_links_without_starting(
    client, seeded, monkeypatch, tmp_path, corruption,
):
    session_id, run_id, _revision, _targets, owner = _approve_and_get_owner(
        client, seeded, monkeypatch,
    )
    view, execute_owner = main._run_full_auto_submission(owner)
    assert view["phase"] == "execute"
    if corruption == "missing_receipt":
        db.run(
            "DELETE FROM full_auto_submission_receipt WHERE run_id = ? "
            "AND target_index = (SELECT MAX(target_index) FROM full_auto_submission_receipt WHERE run_id = ?)",
            run_id, run_id,
        )
    elif corruption == "linked_prompt":
        shot_id = db.one(
            "SELECT shot_id FROM full_auto_submission_receipt WHERE run_id = ? ORDER BY target_index LIMIT 1",
            run_id,
        )["shot_id"]
        db.run("UPDATE shot SET prompt = ? WHERE id = ?", "corrupt linked prompt", shot_id)

    original_session_status = db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"]
    runner = Runner(None, tmp_path / "sessions", tmp_path / "output")
    started: list[int] = []

    async def record_start(sid: int) -> None:
        started.append(sid)

    runner._run_session = record_start
    with pytest.raises(resource_full_auto.FullAutoRunError):
        asyncio.run(_start_runner(runner, session_id, execute_owner))
    assert started == []
    assert db.one(
        "SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) is None
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id,
    )["status"] == original_session_status


def test_runner_admission_rejects_receipts_whose_ids_run_out_of_target_order(
    client, seeded, monkeypatch, tmp_path,
):
    session_id, run_id, revision, _targets, owner = _approve_and_get_owner(
        client, seeded, monkeypatch,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)
    receipts = db.q(
        "SELECT * FROM full_auto_submission_receipt WHERE run_id = ? ORDER BY target_index",
        run_id,
    )
    assert len(receipts) == 4
    first, second = receipts[:2]
    with db.transaction():
        # Model two valid target links created in the wrong order. The runner
        # consumes shots by id, so all linked evidence can be valid while the
        # execution sequence still differs from the saved target sequence.
        db.run(
            "UPDATE prepared_take SET linked_shot_id = NULL "
            "WHERE id IN (?, ?)", first["prepared_take_id"], second["prepared_take_id"],
        )
        db.run(
            "UPDATE prepared_take SET linked_shot_id = CASE id "
            "WHEN ? THEN ? WHEN ? THEN ? END WHERE id IN (?, ?)",
            first["prepared_take_id"], second["shot_id"],
            second["prepared_take_id"], first["shot_id"],
            first["prepared_take_id"], second["prepared_take_id"],
        )
        first_prompt = db.one(
            "SELECT final_prompt FROM prepared_take WHERE id = ?", first["prepared_take_id"],
        )["final_prompt"]
        second_prompt = db.one(
            "SELECT final_prompt FROM prepared_take WHERE id = ?", second["prepared_take_id"],
        )["final_prompt"]
        db.run("UPDATE shot SET prompt = ? WHERE id = ?", second_prompt, first["shot_id"])
        db.run("UPDATE shot SET prompt = ? WHERE id = ?", first_prompt, second["shot_id"])
        db.run("DELETE FROM full_auto_submission_receipt WHERE run_id = ?", run_id)
        for receipt in receipts:
            db.run(
                "INSERT INTO full_auto_submission_receipt "
                "(run_id, target_index, take_id, prepared_take_id, shot_id, snapshot_digest, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                receipt["run_id"], receipt["target_index"], receipt["take_id"],
                receipt["prepared_take_id"],
                second["shot_id"] if receipt["target_index"] == first["target_index"]
                else first["shot_id"] if receipt["target_index"] == second["target_index"]
                else receipt["shot_id"],
                receipt["snapshot_digest"], receipt["created_at"],
            )

    runner = Runner(None, tmp_path / "sessions", tmp_path / "output")
    started: list[int] = []

    async def record_start(sid: int) -> None:
        started.append(sid)

    runner._run_session = record_start
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        asyncio.run(_start_runner(runner, session_id, execute_owner))
    assert error.value.code == "full_auto_execution_state_stale"
    assert started == []
    assert db.one(
        "SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    ) is None
    assert db.one("SELECT plan_revision FROM full_auto_run WHERE run_id = ?", run_id)["plan_revision"] == revision


async def _start_runner(runner: Runner, session_id: int, claim) -> None:
    runner.start(session_id, _full_auto_claim=claim)
    if runner._task is not None:
        await runner._task


def test_runner_admits_all_linked_targets_once_and_fails_closed_after_schedule_cut(
    client, seeded, monkeypatch, tmp_path,
):
    session_id, run_id, _revision, _targets, owner = _approve_and_get_owner(
        client, seeded, monkeypatch,
    )
    _view, execute_owner = main._run_full_auto_submission(owner)

    def assert_forwarded_claim(claim, expected) -> None:
        assert claim is not None
        assert (claim.run_id, claim.session_id, claim.epoch, claim.fencing_token) == (
            expected.run_id, expected.session_id, expected.epoch, expected.fencing_token,
        )

    runner = Runner(None, tmp_path / "sessions", tmp_path / "output")
    started: list[int] = []

    async def record_start(sid: int, _full_auto_claim=None) -> None:
        assert_forwarded_claim(_full_auto_claim, execute_owner)
        started.append(sid)

    runner._run_session = record_start

    async def start_once_and_replay() -> None:
        runner.start(session_id, _full_auto_claim=execute_owner)
        await runner._task
        with pytest.raises(resource_full_auto.FullAutoRunError) as error:
            runner.start(session_id, _full_auto_claim=execute_owner)
        assert error.value.code == "full_auto_runner_already_admitted"

    asyncio.run(start_once_and_replay())
    assert started == [session_id]
    assert db.one(
        "SELECT session_id FROM full_auto_runner_admission WHERE run_id = ?", run_id,
    )["session_id"] == session_id

    # A committed admission followed by a scheduler exception remains durable;
    # retry cannot mistake the cut for proof that no runner was started.
    session_id2, run_id2, _revision2, _targets2, owner2 = _approve_and_get_owner(
        client, seeded, monkeypatch,
    )
    _view2, execute_owner2 = main._run_full_auto_submission(owner2)
    runner2 = Runner(None, tmp_path / "sessions-2", tmp_path / "output-2")
    second_started: list[int] = []

    def record_second_start(sid: int, _full_auto_claim=None):
        assert_forwarded_claim(_full_auto_claim, execute_owner2)

        async def record() -> None:
            second_started.append(sid)

        return record()

    runner2._run_session = record_second_start

    def fail_schedule(coro):
        coro.close()
        raise RuntimeError("injected cut before runner scheduling")

    async def cut_after_admission() -> None:
        with monkeypatch.context() as patcher:
            patcher.setattr(asyncio, "create_task", fail_schedule)
            with pytest.raises(RuntimeError, match="injected cut"):
                runner2.start(session_id2, _full_auto_claim=execute_owner2)

    asyncio.run(cut_after_admission())
    assert db.one(
        "SELECT status FROM session WHERE id = ?", session_id2,
    )["status"] == "running"
    assert db.one(
        "SELECT run_id FROM full_auto_runner_admission WHERE run_id = ?", run_id2,
    ) is not None
    _reopen_database()

    async def refuse_unknown_replay() -> None:
        with pytest.raises(resource_full_auto.FullAutoRunError) as error:
            runner2.start(session_id2, _full_auto_claim=execute_owner2)
        assert error.value.code == "full_auto_runner_already_admitted"

    asyncio.run(refuse_unknown_replay())
    assert second_started == []


def test_disabled_or_stale_submission_owner_writes_no_shots_or_receipts(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, _targets, owner = _approve_and_get_owner(
        client, seeded, monkeypatch,
    )
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    with pytest.raises(session_plan.ResourcePlanningDisabled):
        main._run_full_auto_submission(owner)
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []
    assert db.q("SELECT * FROM full_auto_submission_receipt WHERE run_id = ?", run_id) == []
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    renewed_at = (
        datetime.fromisoformat(owner.lease_started_at) + timedelta(seconds=2)
    ).isoformat()
    resource_full_auto.renew_run_claim(owner, now_text=renewed_at)
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        main._run_full_auto_submission(owner)
    assert error.value.code == "run_owner_stale"
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []
    assert db.q("SELECT * FROM full_auto_submission_receipt WHERE run_id = ?", run_id) == []


def test_cancelled_or_drifted_submission_owner_writes_no_shots_or_receipts(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, _targets, owner = _approve_and_get_owner(
        client, seeded, monkeypatch,
    )
    run = db.one("SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id)
    resource_full_auto.cancel_run(
        run_id, "task45-cancel-before-submit", int(run["revision"]),
        int(run["plan_revision"]), work_pending=False,
    )
    cancelled_snapshot = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    with pytest.raises(resource_full_auto.FullAutoRunError):
        main._run_full_auto_submission(owner)
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == cancelled_snapshot
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []
    assert db.q("SELECT * FROM full_auto_submission_receipt WHERE run_id = ?", run_id) == []

    session_id2, run_id2, revision2, targets2, owner2 = _approve_and_get_owner(
        client, seeded, monkeypatch,
    )
    db.run(
        "UPDATE prepared_take SET final_prompt = ? "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        "drifted frozen prompt", session_id2, revision2, targets2[0],
    )
    with pytest.raises(resource_full_auto.FullAutoRunError):
        main._run_full_auto_submission(owner2)
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id2) == []
    assert db.q("SELECT * FROM full_auto_submission_receipt WHERE run_id = ?", run_id2) == []


def test_receipt_and_runner_admission_recheck_feature_flag_after_review(
    client, seeded, monkeypatch, tmp_path,
):
    session_id, run_id, revision, targets, owner = _approve_and_get_owner(
        client, seeded, monkeypatch,
    )
    submitted = session_plan.submit_prepared_take(
        session_id, revision, targets[0], _full_auto_claim=owner,
    )
    original_review = session_plan._full_auto_duplicate_review_evidence

    def disable_after_review(*args, **kwargs):
        review = original_review(*args, **kwargs)
        monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
        return review

    with monkeypatch.context() as patcher:
        patcher.setattr(session_plan, "_full_auto_duplicate_review_evidence", disable_after_review)
        with pytest.raises(session_plan.ResourcePlanningDisabled):
            resource_full_auto.record_submission_receipt(
                owner, 0, targets[0], submitted["shot_id"],
            )
    assert db.q("SELECT * FROM full_auto_submission_receipt WHERE run_id = ?", run_id) == []

    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    session_id2, run_id2, _revision2, _targets2, owner2 = _approve_and_get_owner(
        client, seeded, monkeypatch,
    )
    _view, execute_owner = main._run_full_auto_submission(owner2)
    runner = Runner(None, tmp_path / "sessions", tmp_path / "output")
    started: list[int] = []

    async def record_start(sid: int) -> None:
        started.append(sid)

    runner._run_session = record_start
    status_before = db.one("SELECT status FROM session WHERE id = ?", session_id2)["status"]
    with monkeypatch.context() as patcher:
        patcher.setattr(session_plan, "_full_auto_duplicate_review_evidence", disable_after_review)
        with pytest.raises(session_plan.ResourcePlanningDisabled):
            asyncio.run(_start_runner(runner, session_id2, execute_owner))
    assert started == []
    assert db.one("SELECT status FROM session WHERE id = ?", session_id2)["status"] == status_before
    assert db.q("SELECT * FROM full_auto_runner_admission WHERE run_id = ?", run_id2) == []


def test_disabled_approval_recovery_does_not_advance_or_write_policy_approval(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, _targets, owner = _prepare_full_auto(
        client, seeded, monkeypatch, photo_count=4,
    )
    owner = _phase_owner(owner, "approve")
    run_before = db.one("SELECT revision, phase FROM full_auto_run WHERE run_id = ?", run_id)
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    with pytest.raises(session_plan.ResourcePlanningDisabled):
        main._run_full_auto_review_and_approve(owner)
    assert db.one("SELECT revision, phase FROM full_auto_run WHERE run_id = ?", run_id) == run_before
    assert db.one(
        "SELECT run_id FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    ) is None
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []
