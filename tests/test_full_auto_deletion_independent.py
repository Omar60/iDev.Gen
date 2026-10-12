"""Independent deletion-boundary acceptance for Full Auto task 4.10."""
from __future__ import annotations

import json
import sqlite3
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

import db
import main
import runner as runner_module
from backend import guided_sessions, resource_full_auto
from test_guided_session_creation import (
    _forbid_full_auto_side_effects,
    _full_auto_payload,
    _room_anchor,
)
import test_full_auto_preparation_coordinator as preparation
import test_full_auto_submission_independent as submission
from test_full_auto_run_independent import _seed_run
import test_full_auto_control_api as control
from runner import Runner


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "deletion-independent-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "deletion-independent-test-key")
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)


def _all_rows() -> dict[str, list[str]]:
    tables = [row["name"] for row in db.q(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {
        table: sorted(
            json.dumps(dict(row), sort_keys=True, ensure_ascii=False, default=repr)
            for row in db.q(f' SELECT * FROM "{table}"')
        )
        for table in tables
    }


def _files() -> dict[str, bytes]:
    root = main.SESSIONS_DIR
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    } if root.exists() else {}


def _linked_active_run(client, seeded, monkeypatch):
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    preparation._install_provider(monkeypatch, session_id, revision, targets)
    prepared = asyncio.run(
        main._run_full_auto_preparation(resource_full_auto.claim_run(run_id)),
    )
    assert (prepared["state"], prepared["phase"]) == ("active", "validate"), prepared
    validation_owner = submission._recover_owner(run_id, "validate")
    reviewed, submit_owner = main._run_full_auto_review_and_approve(validation_owner)
    assert (reviewed["state"], reviewed["phase"]) == ("active", "submit"), reviewed
    view, execute_owner = main._run_full_auto_submission(submit_owner)
    assert (view["state"], view["phase"]) == ("active", "execute")
    shots = db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id)
    assert len(shots) == len(targets) == 4
    return session_id, run_id, revision, execute_owner, shots


def _assert_route_refusal(response, session_id: int) -> None:
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "full_auto_in_use"
    public = json.dumps(detail, ensure_ascii=False).lower()
    assert str(session_id) in public
    assert "cancel" in public and "settle" in public


def _assert_direct_delete_refusal(error: Exception, session_id: int) -> None:
    if isinstance(error, HTTPException):
        assert error.status_code == 409
        code = error.detail["code"]
        message = json.dumps(error.detail, ensure_ascii=False).lower()
    else:
        assert isinstance(error, resource_full_auto.FullAutoRunError)
        assert error.status_code == 409
        code = error.code
        message = error.message.lower()
    assert code == "full_auto_in_use"
    assert str(session_id) in message
    assert "cancel" in message and "settle" in message


def _assert_sql_refusal(sql: str, *args) -> None:
    with pytest.raises(sqlite3.IntegrityError) as error:
        db.run(sql, *args)
    assert "full_auto_in_use" in str(error.value)


def _remove_delete_guards(table: str) -> None:
    for row in db.q(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
        table,
    ):
        name = row["name"].replace('"', '""')
        db.conn().execute(f'DROP TRIGGER "{name}"')
    db.conn().commit()


def _assert_dependency_missing_blocks_claim(session_id: int, run_id: str) -> None:
    before = db.one(
        "SELECT revision, fencing_token FROM full_auto_run WHERE run_id = ?",
        run_id,
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.claim_run(run_id)
    assert error.value.code == "dependency_missing"
    row = db.one(
        "SELECT state, blocker_code, revision, fencing_token, lease_expires_at, "
        "lease_started_at FROM full_auto_run WHERE run_id = ?",
        run_id,
    )
    assert (row["state"], row["blocker_code"]) == ("blocked", "dependency_missing")
    assert row["revision"] > before["revision"]
    assert row["fencing_token"] > before["fencing_token"]
    assert row["lease_expires_at"] is None and row["lease_started_at"] is None
    view = resource_full_auto.public_run_status(session_id, run_id)
    assert (view["state"], view["blocker_code"]) == ("blocked", "dependency_missing")


def _reuse_session_id(session_id: int, model_id: int, shot_id: int) -> None:
    """Simulate an out-of-band delete/recreate while a worker still holds its claim."""
    for row in db.q(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
        "AND name LIKE 'full_auto_protect_%'"
    ):
        name = row["name"].replace('"', '""')
        db.conn().execute(f'DROP TRIGGER "{name}"')
    db.conn().commit()
    db.conn().execute("DELETE FROM session WHERE id = ?", (session_id,))
    db.conn().commit()
    db.conn().execute(
        "INSERT INTO session (id, model_id, name, created_at) VALUES (?, ?, ?, ?)",
        (session_id, model_id, "replacement-session", db.now()),
    )
    db.conn().execute(
        "INSERT INTO shot (id, session_id, shot_label, status, created_at) "
        "VALUES (?, ?, ?, 'running', ?)",
        (shot_id, session_id, "replacement-shot", db.now()),
    )
    db.conn().commit()
    db.conn().execute("PRAGMA foreign_keys = ON")


def test_active_full_auto_refuses_route_and_persistence_deletes_without_side_effects(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, _owner, shots = _linked_active_run(
        client, seeded, monkeypatch,
    )
    session = db.one("SELECT * FROM session WHERE id = ?", session_id)
    model = db.one("SELECT * FROM model WHERE id = ?", session["model_id"])
    workflow_id = session["workflow_id"] or model["workflow_id"]
    profile = db.one(
        "SELECT look_key, look_version, profile_revision FROM session_profile_evidence "
        "WHERE session_id = ? ORDER BY plan_revision DESC LIMIT 1",
        session_id,
    )
    assert workflow_id is not None and profile is not None

    folder = main.SESSIONS_DIR / str(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / "protected.png"
    image.write_bytes(b"synthetic linked image")
    db.run("UPDATE shot SET filename = ? WHERE id = ?", image.name, shots[0]["id"])

    for path in (
        f"/api/sessions/{session_id}",
        f"/api/shots/{shots[0]['id']}",
        f"/api/models/{model['id']}",
        f"/api/workflows/{workflow_id}",
    ):
        before_rows, before_files = _all_rows(), _files()
        response = client.delete(path)
        _assert_route_refusal(response, session_id)
        assert _all_rows() == before_rows
        assert _files() == before_files

    direct_deletes = (
        ("DELETE FROM session WHERE id = ?", (session_id,)),
        ("DELETE FROM shot WHERE id = ?", (shots[0]["id"],)),
        ("DELETE FROM model WHERE id = ?", (model["id"],)),
        ("DELETE FROM workflow WHERE id = ?", (workflow_id,)),
        (
            "DELETE FROM saved_look_version WHERE look_key = ? AND version = ?",
            (profile["look_key"], profile["look_version"]),
        ),
        (
            "DELETE FROM saved_look_automation_profile "
            "WHERE look_key = ? AND look_version = ? AND profile_revision = ?",
            (profile["look_key"], profile["look_version"], profile["profile_revision"]),
        ),
    )
    for sql, args in direct_deletes:
        before_rows, before_files = _all_rows(), _files()
        _assert_sql_refusal(sql, *args)
        assert _all_rows() == before_rows
        assert _files() == before_files

    assert db.one("SELECT run_id FROM full_auto_run WHERE run_id = ?", run_id)


def test_unrelated_and_unlinked_rows_remain_deletable_during_full_auto(
    client, seeded, monkeypatch,
):
    session_id, _run_id, _revision, _owner, _shots = _linked_active_run(
        client, seeded, monkeypatch,
    )
    workflow = db.one("SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"])
    unrelated_workflow = client.post(
        "/api/workflows", json={"name": "unrelated-delete", "graph": json.loads(workflow["graph"])}
    ).json()["id"]
    unrelated_model = client.post(
        "/api/models", json={"name": "unrelated-delete", "settings": {}}
    ).json()["id"]
    unlinked_shot = db.run(
        "INSERT INTO shot (session_id, shot_label, status, created_at) VALUES (?, ?, 'pending', ?)",
        session_id, "not-in-run-receipts", db.now(),
    )

    assert client.delete(f"/api/shots/{unlinked_shot}").status_code == 200
    assert client.delete(f"/api/models/{unrelated_model}").status_code == 200
    assert client.delete(f"/api/workflows/{unrelated_workflow}").status_code == 200
    assert db.one("SELECT id FROM session WHERE id = ?", session_id)


def test_unsettled_admitted_shot_keeps_deletion_guard_after_run_terminal_state(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, owner, shots = _linked_active_run(
        client, seeded, monkeypatch,
    )
    resource_full_auto.admit_runner_start(session_id, owner)
    db.run("UPDATE shot SET status = 'running' WHERE id = ?", shots[0]["id"])
    db.run(
        "UPDATE full_auto_run SET state = 'failed', revision = revision + 1, "
        "fencing_token = fencing_token + 1, lease_expires_at = NULL, "
        "lease_started_at = NULL, updated_at = ? WHERE run_id = ?",
        db.now(), run_id,
    )
    folder = main.SESSIONS_DIR / str(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / "possibly-in-flight.png"
    image.write_bytes(b"uncertain output")
    db.run("UPDATE shot SET filename = ? WHERE id = ?", image.name, shots[0]["id"])

    before_rows, before_files = _all_rows(), _files()
    _assert_route_refusal(client.delete(f"/api/sessions/{session_id}"), session_id)
    _assert_route_refusal(client.delete(f"/api/shots/{shots[0]['id']}"), session_id)
    _assert_sql_refusal("DELETE FROM session WHERE id = ?", session_id)
    _assert_sql_refusal("DELETE FROM shot WHERE id = ?", shots[0]["id"])
    assert _all_rows() == before_rows
    assert _files() == before_files


def test_stale_owner_is_rechecked_after_locked_move_before_reused_folder_adoption(
    client, seeded, monkeypatch, comfy_output,
):
    session_id, run_id, _revision, owner, shots = _linked_active_run(
        client, seeded, monkeypatch,
    )
    resource_full_auto.admit_runner_start(session_id, owner)
    shot = shots[0]
    db.run("UPDATE shot SET status = 'running' WHERE id = ?", shot["id"])

    output_folder = comfy_output / "idevgen" / "old-run"
    output_folder.mkdir(parents=True)
    source = output_folder / "late.png"
    source.write_bytes(b"late Full Auto output")
    session_folder = main.SESSIONS_DIR / str(session_id)
    session_folder.mkdir(parents=True, exist_ok=True)
    destination = session_folder / (
        f"{shot['id']:05d}_{runner_module.slug(shot['shot_label'])}.png"
    )
    attempts = []
    first_move = True
    original_move = runner_module.shutil.move

    class Heartbeat:
        def check(self):
            return owner

    def lock_first_move(source_path, destination_path):
        nonlocal first_move
        attempts.append((source_path, destination_path))
        if first_move:
            first_move = False
            raise PermissionError("synthetic file lock")
        return original_move(source_path, destination_path)

    async def replace_reused_folder(_delay):
        model_id = db.one("SELECT model_id FROM session WHERE id = ?", session_id)["model_id"]
        for path in session_folder.iterdir():
            if path.is_dir():
                runner_module.shutil.rmtree(path)
            else:
                path.unlink()
        session_folder.rmdir()
        _reuse_session_id(session_id, model_id, int(shot["id"]))
        session_folder.mkdir(parents=True)
        (session_folder / "new-session-marker.txt").write_bytes(b"replacement session")

    monkeypatch.setattr(runner_module.shutil, "move", lock_first_move)
    monkeypatch.setattr(
        runner_module, "asyncio", SimpleNamespace(sleep=replace_reused_folder),
    )
    runner = Runner(object(), main.SESSIONS_DIR, comfy_output)

    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        asyncio.run(runner._collect(
            session_id, shot, {"subfolder": "idevgen/old-run", "filename": source.name},
            Heartbeat(),
        ))

    assert error.value.code == "run_owner_stale"
    assert len(attempts) == 1
    assert source.read_bytes() == b"late Full Auto output"
    assert not destination.exists()
    assert (session_folder / "new-session-marker.txt").read_bytes() == b"replacement session"
    assert db.one("SELECT name FROM session WHERE id = ?", session_id)["name"] == "replacement-session"
    assert db.one("SELECT shot_label FROM shot WHERE id = ?", shot["id"])["shot_label"] == "replacement-shot"


def test_terminal_session_snapshot_survives_safe_saved_look_version_removal(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, _targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    profile = db.one(
        "SELECT look_key, look_version, profile_revision FROM session_profile_evidence "
        "WHERE session_id = ? ORDER BY plan_revision DESC LIMIT 1",
        session_id,
    )
    plan_before = db.one("SELECT * FROM session_plan WHERE session_id = ?", session_id)
    row = db.one("SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, f"safe-delete-{uuid.uuid4().hex}", row["revision"], row["plan_revision"],
        work_pending=False,
    )
    assert cancelled.view["state"] == "cancelled"
    assert db.run(
        "DELETE FROM saved_look_version WHERE look_key = ? AND version = ?",
        profile["look_key"], profile["look_version"],
    )
    assert db.one("SELECT * FROM session_plan WHERE session_id = ?", session_id) == plan_before
    assert db.one(
        "SELECT * FROM session_profile_evidence WHERE session_id = ?",
        session_id,
    ) is not None


def test_renewed_authorization_keeps_model_dependency_guarded(client, seeded, monkeypatch):
    session_id, run_id = control._create_full_auto(client, seeded, monkeypatch)
    control._cancel_run(client, session_id, run_id)
    plan_revision = control._edit_brief(session_id, "A renewed but still bound run.")
    path = f"/api/sessions/{session_id}/full-auto/{run_id}"
    comparison = client.get(f"{path}/resume-preflight")
    assert comparison.status_code == 200, comparison.text
    fresh = comparison.json()
    body = control._control_body(run_id, plan_revision)
    body.update({
        "scope_digest": fresh["scope_digest"],
        "comparison_issued_at": fresh["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    resumed = client.post(f"{path}/resume", json=body)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["state"] == "waiting_authoring"
    grants = db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    )
    assert len(grants) == 1

    before_rows, before_files = _all_rows(), _files()
    model_id = db.one("SELECT model_id FROM session WHERE id = ?", session_id)["model_id"]
    _assert_route_refusal(client.delete(f"/api/models/{model_id}"), session_id)
    assert _all_rows() == before_rows
    assert _files() == before_files
    assert db.q(
        "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
        run_id,
    ) == grants


def test_out_of_band_look_loss_blocks_full_auto_without_dispatch(client, seeded, monkeypatch):
    session_id, run_id, revision, _targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    profile = db.one(
        "SELECT look_key, look_version FROM session_profile_evidence "
        "WHERE session_id = ? ORDER BY plan_revision DESC LIMIT 1",
        session_id,
    )
    assert profile is not None
    _remove_delete_guards("saved_look_version")
    db.conn().execute("PRAGMA foreign_keys = OFF")
    db.conn().execute(
        "DELETE FROM saved_look_version WHERE look_key = ? AND version = ?",
        (profile["look_key"], profile["look_version"]),
    )
    db.conn().commit()
    db.conn().execute("PRAGMA foreign_keys = ON")

    monkeypatch.setattr(
        main.enhance, "run_structured",
        lambda *_args, **_kwargs: pytest.fail("dependency loss must be checked before assistant dispatch"),
    )
    monkeypatch.setattr(
        main.runner, "start",
        lambda *_args, **_kwargs: pytest.fail("dependency loss must prevent runner dispatch"),
    )
    _assert_dependency_missing_blocks_claim(session_id, run_id)
    assert db.one("SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", session_id)["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 0
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_runner_admission WHERE run_id = ?",
        run_id,
    )["n"] == 0


def test_out_of_band_room_revision_loss_blocks_full_auto_before_claim(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, _targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    authorization = json.loads(db.one(
        "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?",
        run_id,
    )["authorization_json"])
    room = authorization["summary"]["room"]
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        room["library_key"],
    )
    assert library is not None
    db.conn().execute("PRAGMA foreign_keys = OFF")
    deleted = db.conn().execute(
        "DELETE FROM asset_revision WHERE library_id = ? AND source_id = ? "
        "AND content_digest = ?",
        (library["id"], room["source_id"], room["content_digest"]),
    )
    db.conn().commit()
    db.conn().execute("PRAGMA foreign_keys = ON")
    assert deleted.rowcount == 1

    _assert_dependency_missing_blocks_claim(session_id, run_id)
    assert db.one("SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", session_id)["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id = ?", session_id)["n"] == 0
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_runner_admission WHERE run_id = ?",
        run_id,
    )["n"] == 0


def test_resume_with_missing_dependency_keeps_failed_run_stopped(client, seeded, monkeypatch):
    session_id, run_id, _revision, _targets = preparation._create_run(
        client, seeded, photo_count=4,
    )
    workflow_id = resource_full_auto.current_authorization(run_id)["authorization"][
        "summary"
    ]["workflow"]["workflow_id"]
    _remove_delete_guards("workflow")
    db.run("DELETE FROM workflow WHERE id = ?", workflow_id)
    db.run(
        "UPDATE full_auto_run SET state = 'failed', revision = revision + 1, "
        "fencing_token = fencing_token + 1, lease_expires_at = NULL, "
        "lease_started_at = NULL, updated_at = ? WHERE run_id = ?",
        db.now(), run_id,
    )
    before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    before_rows, before_files = _all_rows(), _files()
    status = resource_full_auto.public_run_status(session_id, run_id)
    assert status["state"] == "failed"
    assert status["blocker_code"] == "dependency_missing"
    assert status["can_resume"] is False

    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.resume_run(
            run_id, f"missing-dependency-resume-{uuid.uuid4().hex}",
            before["revision"], before["plan_revision"], eligibility=None,
            expected_session_id=session_id,
        )
    assert error.value.code == "dependency_missing"
    after = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert after == before
    assert _all_rows() == before_rows
    assert _files() == before_files


def test_guided_creation_and_model_deletion_have_one_transactional_winner(
    client, seeded, monkeypatch,
):
    _forbid_full_auto_side_effects(monkeypatch)
    payload, _saved, _published = _full_auto_payload(
        client, seeded, _room_anchor(),
    )
    request = guided_sessions.normalize_request(payload)
    request_id = payload["request_id"]
    before_sessions = db.one("SELECT COUNT(*) AS n FROM session")["n"]
    start = Barrier(2)

    def create():
        start.wait()
        try:
            return guided_sessions.create_or_replay(request, config=lambda: main.CONFIG)
        except Exception as exc:  # the losing operation must leave no partial session
            return exc

    def delete_model():
        start.wait()
        try:
            return main.delete_model(seeded["model_id"])
        except (HTTPException, resource_full_auto.FullAutoRunError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        creation = pool.submit(create)
        deletion = pool.submit(delete_model)
        create_result, delete_result = creation.result(), deletion.result()

    if isinstance(create_result, tuple):
        response_json, was_new = create_result
        assert was_new is True
        created = json.loads(response_json)
        assert db.one(
            "SELECT run_id FROM full_auto_run WHERE session_id = ?",
            created["session_id"],
        ) is not None
        _assert_direct_delete_refusal(delete_result, created["session_id"])
    else:
        assert isinstance(create_result, guided_sessions.GuidedSessionError)
        assert isinstance(delete_result, dict) and delete_result["ok"] is True
        assert db.one("SELECT id FROM model WHERE id = ?", seeded["model_id"]) is None
        assert db.one("SELECT COUNT(*) AS n FROM session")["n"] == before_sessions
        assert db.one(
            "SELECT request_id FROM guided_session_request WHERE request_id = ?",
            request_id,
        ) is None
        assert db.one("SELECT COUNT(*) AS n FROM full_auto_run_receipt")["n"] == 0


@pytest.mark.parametrize("first", ["delete", "cancel"])
def test_cancel_and_session_delete_are_safe_in_both_serial_orders(client, first):
    run_id, session_id = _seed_run(f"cancel-delete-{first}")
    folder = main.SESSIONS_DIR / str(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / "retained-until-safe.txt"
    marker.write_text("synthetic session file", encoding="utf-8")
    run = db.one("SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id)

    def cancel():
        return resource_full_auto.cancel_run(
            run_id, f"cancel-delete-{uuid.uuid4().hex}",
            run["revision"], run["plan_revision"], work_pending=False,
        )

    def delete():
        try:
            return main.delete_session(session_id)
        except (HTTPException, resource_full_auto.FullAutoRunError) as exc:
            return exc

    if first == "delete":
        refused = delete()
        _assert_direct_delete_refusal(refused, session_id)
        assert marker.read_text(encoding="utf-8") == "synthetic session file"
        assert db.one("SELECT id FROM session WHERE id = ?", session_id)
        assert cancel().view["state"] == "cancelled"
        assert db.one("SELECT id FROM session WHERE id = ?", session_id)
        assert marker.exists()
    else:
        assert cancel().view["state"] == "cancelled"
        assert delete() == {"ok": True}
        assert db.one("SELECT id FROM session WHERE id = ?", session_id) is None
        assert not folder.exists()


def test_concurrent_cancel_and_session_delete_are_serializable(client):
    run_id, session_id = _seed_run("concurrent-cancel-delete")
    run = db.one("SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id)
    start = Barrier(2)

    def cancel():
        start.wait()
        return resource_full_auto.cancel_run(
            run_id, f"concurrent-cancel-{uuid.uuid4().hex}",
            run["revision"], run["plan_revision"], work_pending=False,
        )

    def delete():
        start.wait()
        try:
            return main.delete_session(session_id)
        except (HTTPException, resource_full_auto.FullAutoRunError) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancel_future = pool.submit(cancel)
        delete_future = pool.submit(delete)
        cancelled, deleted = cancel_future.result(), delete_future.result()

    assert cancelled.view["state"] == "cancelled"
    if isinstance(deleted, HTTPException):
        _assert_direct_delete_refusal(deleted, session_id)
        assert db.one("SELECT id FROM session WHERE id = ?", session_id)
    elif isinstance(deleted, resource_full_auto.FullAutoRunError):
        _assert_direct_delete_refusal(deleted, session_id)
        assert db.one("SELECT id FROM session WHERE id = ?", session_id)
    else:
        assert deleted == {"ok": True}
        assert db.one("SELECT id FROM session WHERE id = ?", session_id) is None
