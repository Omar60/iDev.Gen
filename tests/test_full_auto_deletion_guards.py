"""Transactional deletion and stale-worker guards for Full Auto runs."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace

import db
import main
import pytest
from backend import authoring_operations, resource_full_auto, resource_store
from test_full_auto_preparation_coordinator import _create_run
from test_task31_acceptance import (
    _create_from_preflight,
    _full_auto_fixture,
    _install_t2i_workflow,
    _preflight,
)


def _create_active_run(client, seeded, monkeypatch, *, photo_count=4):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "deletion-guard-test")
    monkeypatch.setitem(main.CONFIG, "llm_key", "deletion-guard-test-key")
    return _create_run(client, seeded, photo_count=photo_count)


def _bound_ids(run_id: str) -> dict:
    receipt = db.one(
        "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?",
        run_id,
    )
    authorization = json.loads(receipt["authorization_json"])
    summary = authorization["summary"]
    return {
        "model_id": summary["model_binding"]["model_id"],
        "workflow_id": summary["workflow"]["workflow_id"],
        "look": (summary["saved_look"]["look_key"], summary["saved_look"]["version"]),
        "profile": (
            summary["saved_look"]["look_key"], summary["saved_look"]["version"],
            authorization["profile_revision"], authorization["profile_digest"],
        ),
    }


def _full_auto_rows(session_id: int, run_id: str) -> dict:
    return {
        "session": db.one("SELECT * FROM session WHERE id = ?", session_id),
        "shots": db.q("SELECT * FROM shot WHERE session_id = ? ORDER BY id", session_id),
        "model": db.one(
            "SELECT * FROM model WHERE id = (SELECT model_id FROM session WHERE id = ?)",
            session_id,
        ),
        "workflow": db.one(
            "SELECT * FROM workflow WHERE id = ?", _bound_ids(run_id)["workflow_id"],
        ),
        "plan": db.one("SELECT * FROM session_plan WHERE session_id = ?", session_id),
        "profile_evidence": db.q(
            "SELECT * FROM session_profile_evidence WHERE session_id = ? ORDER BY plan_revision",
            session_id,
        ),
        "run": db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id),
        "receipt": db.one("SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id),
        "renewals": db.q(
            "SELECT * FROM full_auto_run_authorization WHERE run_id = ? ORDER BY authorization_sequence",
            run_id,
        ),
    }


def _assert_full_auto_in_use(response, session_id: int) -> None:
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "full_auto_in_use"
    assert detail["affected_sessions"] == [session_id]
    assert "Cancel" in detail["message"] and "settle" in detail["message"]


def _cancel_without_work(session_id: int, run_id: str) -> dict:
    row = db.one("SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id)
    return resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), row["revision"], row["plan_revision"],
        work_pending=False, expected_session_id=session_id,
    ).view


def test_api_deletion_refusal_preserves_rows_and_session_files(client, seeded, monkeypatch):
    session_id, run_id, _revision, _targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    ids = _bound_ids(run_id)
    folder = Path(main.SESSIONS_DIR) / str(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    photo = folder / "preserved.png"
    photo.write_bytes(b"synthetic session evidence")
    before = _full_auto_rows(session_id, run_id)

    _assert_full_auto_in_use(client.delete(f"/api/sessions/{session_id}"), session_id)
    _assert_full_auto_in_use(client.delete(f"/api/models/{ids['model_id']}"), session_id)
    _assert_full_auto_in_use(client.delete(f"/api/workflows/{ids['workflow_id']}"), session_id)

    assert _full_auto_rows(session_id, run_id) == before
    assert photo.read_bytes() == b"synthetic session evidence"


def test_shot_api_and_raw_sql_deletes_are_guarded_before_file_removal(
    client, seeded, monkeypatch,
):
    session_id, run_id, plan_revision, targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    now = db.now()
    shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, shot_label, status, created_at)
           VALUES (?, 0, 'pending target', 'running', ?)""",
        session_id, now,
    )
    db.run(
        """INSERT INTO prepared_take
           (session_id, plan_revision, take_id, status, linked_shot_id, created_at, updated_at)
           VALUES (?, ?, ?, 'ready', ?, ?, ?)""",
        session_id, plan_revision, targets[0], shot_id, now, now,
    )
    image = Path(main.SESSIONS_DIR) / str(session_id) / f"{shot_id:05d}_pending-target.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"synthetic rendered image")
    before = _full_auto_rows(session_id, run_id)

    _assert_full_auto_in_use(client.delete(f"/api/shots/{shot_id}"), session_id)
    assert image.read_bytes() == b"synthetic rendered image"
    assert _full_auto_rows(session_id, run_id) == before

    ids = _bound_ids(run_id)
    statements = [
        ("DELETE FROM session WHERE id = ?", (session_id,)),
        ("DELETE FROM shot WHERE id = ?", (shot_id,)),
        ("DELETE FROM model WHERE id = ?", (ids["model_id"],)),
        ("DELETE FROM workflow WHERE id = ?", (ids["workflow_id"],)),
        (
            "DELETE FROM saved_look_version WHERE look_key = ? AND version = ?",
            ids["look"],
        ),
        (
            "DELETE FROM saved_look_automation_profile "
            "WHERE look_key = ? AND look_version = ? AND profile_revision = ?",
            ids["profile"][:3],
        ),
    ]
    for sql, params in statements:
        with pytest.raises(sqlite3.IntegrityError, match="full_auto_in_use"):
            db.run(sql, *params)
    assert _full_auto_rows(session_id, run_id) == before
    assert image.exists()


def test_renewed_grant_protects_its_exact_model_dependency(client, seeded, monkeypatch):
    session_id, run_id, _revision, _targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    original = resource_full_auto.current_authorization(run_id)
    other_model = client.post("/api/models", json={
        "name": "renewed grant model",
        "lora_name": "renewed.safetensors",
        "trigger": "renewed",
        "lora_strength": 1.0,
        "base_positive": "",
        "base_negative": "",
        "workflow_id": _bound_ids(run_id)["workflow_id"],
        "settings": {},
        "notes": "",
    }).json()["id"]
    authorization = json.loads(json.dumps(original["authorization"]))
    authorization["summary"]["model_binding"]["model_id"] = other_model
    scope_digest = resource_store.canonical_digest(
        resource_full_auto._project_consent_summary(authorization["summary"]),
    )
    authorization["scope_digest"] = scope_digest
    now = db.now()
    run = db.one("SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id)
    db.run(
        """INSERT INTO full_auto_run_authorization
           (run_id, authorization_sequence, previous_authorization_digest,
            comparison_digest, authorization_json, scope_digest, dependencies_digest,
            plan_digest, scope_issued_at, authorization_expires_at, run_revision,
            plan_revision, created_at)
           VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        run_id, original["authorization_digest"], "a" * 64,
        json.dumps(authorization, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        scope_digest, original["dependencies_digest"], original["plan_digest"],
        original["scope_issued_at"], original["authorization_expires_at"],
        run["revision"], run["plan_revision"], now,
    )
    assert resource_full_auto.current_authorization(run_id)["sequence"] == 1

    _assert_full_auto_in_use(client.delete(f"/api/models/{other_model}"), session_id)
    assert db.one("SELECT id FROM model WHERE id = ?", other_model) is not None


def test_settled_run_allows_library_removal_and_keeps_session_snapshot(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, _targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    ids = _bound_ids(run_id)
    original_plan = db.one(
        "SELECT plan_json FROM session_plan WHERE session_id = ?", session_id,
    )["plan_json"]
    assert _cancel_without_work(session_id, run_id)["state"] == "cancelled"

    removed_look = db.run(
        "DELETE FROM saved_look_version WHERE look_key = ? AND version = ?",
        *ids["look"],
    )
    assert removed_look
    assert db.one("SELECT id FROM session WHERE id = ?", session_id) is not None
    assert db.one(
        "SELECT plan_json FROM session_plan WHERE session_id = ?", session_id,
    )["plan_json"] == original_plan
    assert db.one(
        "SELECT 1 FROM session_profile_evidence WHERE session_id = ?", session_id,
    ) is not None
    # Historical profile evidence is retained by its FK, so the live library
    # profile cannot be removed while that evidence exists.
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.run(
            """DELETE FROM saved_look_automation_profile
               WHERE look_key = ? AND look_version = ? AND profile_revision = ?""",
            *ids["profile"][:3],
        )


def test_delete_race_orders_with_create_and_cancel_are_serializable(
    client, seeded, monkeypatch,
):
    _install_t2i_workflow(client, seeded)
    _saved, _published, base = _full_auto_fixture(seeded, photo_count=4)
    consent = _preflight(client, base)
    created, _payload = _create_from_preflight(client, base, consent)
    assert created.status_code == 201, created.text
    session_id = created.json()["session_id"]
    run_id = created.json()["full_auto_run_id"]
    model_id = _bound_ids(run_id)["model_id"]

    # Creation commits first: the following cascading model delete is refused.
    _assert_full_auto_in_use(client.delete(f"/api/models/{model_id}"), session_id)
    assert _cancel_without_work(session_id, run_id)["state"] == "cancelled"
    assert client.delete(f"/api/sessions/{session_id}").status_code == 200
    assert client.delete(f"/api/models/{model_id}").status_code == 200

    # Deletion commits first: the already-quoted creation refuses without allocating.
    refused, _payload = _create_from_preflight(client, base, consent)
    assert refused.status_code in {404, 409, 422}, refused.text
    assert db.one("SELECT COUNT(*) AS count FROM session")["count"] == 0


def test_cancel_and_delete_orders_wait_for_verified_settlement(client, seeded, monkeypatch):
    session_id, run_id, _revision, _targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, shot_label, status, created_at)
           VALUES (?, 0, 'unsettled', 'pending', ?)""",
        session_id, db.now(),
    )
    run = db.one("SELECT revision, plan_revision FROM full_auto_run WHERE run_id = ?", run_id)
    cancelled = resource_full_auto.cancel_run(
        run_id, str(uuid.uuid4()), run["revision"], run["plan_revision"],
        work_pending=None, derive_work_pending=True, expected_session_id=session_id,
    )
    assert cancelled.view["state"] == "cancel_requested"
    _assert_full_auto_in_use(client.delete(f"/api/sessions/{session_id}"), session_id)

    db.run("UPDATE shot SET status = 'cancelled' WHERE id = ?", shot_id)
    db.run("UPDATE session SET status = 'cancelled' WHERE id = ?", session_id)
    claim = resource_full_auto.claim_run(run_id)
    now = db.now()
    settled = resource_full_auto.reconcile_cancellation(
        claim,
        resource_full_auto.CancellationEvidence("settled", now, 0),
        now_text=now,
    )
    assert settled["state"] == "cancelled"
    assert client.delete(f"/api/sessions/{session_id}").status_code == 200

    # Reverse order on another run: deletion refusal linearizes before Cancel;
    # once Cancel proves no work was admitted, the delete can proceed.
    session_id, run_id, _revision, _targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    _assert_full_auto_in_use(client.delete(f"/api/sessions/{session_id}"), session_id)
    assert _cancel_without_work(session_id, run_id)["state"] == "cancelled"
    assert client.delete(f"/api/sessions/{session_id}").status_code == 200


def test_missing_exact_dependency_durably_blocks_claim_and_resume_refuses_without_writes(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, _targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    workflow_id = _bound_ids(run_id)["workflow_id"]
    with db.transaction():
        db.run("DROP TRIGGER full_auto_protect_workflow_delete")
        db.run("DELETE FROM workflow WHERE id = ?", workflow_id)
    db.conn().executescript(db.SCHEMA)

    before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    current = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert current.status_code == 200, current.text
    assert current.json()["blocker_code"] == "dependency_missing"
    with pytest.raises(resource_full_auto.FullAutoRunError) as claim_error:
        resource_full_auto.claim_run(run_id)
    assert claim_error.value.code == "dependency_missing"
    blocked = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert blocked["state"] == "blocked"
    assert blocked["blocker_code"] == "dependency_missing"
    assert blocked["revision"] == before["revision"] + 1
    assert blocked["fencing_token"] == before["fencing_token"] + 1
    assert blocked["lease_expires_at"] is None
    assert blocked["lease_started_at"] is None

    before = blocked
    current = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}")
    assert current.status_code == 200, current.text
    assert current.json()["state"] == "blocked"
    assert current.json()["blocker_code"] == "dependency_missing"
    assert current.json()["can_resume"] is False
    with pytest.raises(resource_full_auto.FullAutoRunError) as resume_error:
        resource_full_auto.resume_run(
            run_id, str(uuid.uuid4()), before["revision"], before["plan_revision"],
            eligibility=None, expected_session_id=session_id,
        )
    assert resume_error.value.code == "dependency_missing"
    assert db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id) == before


def test_missing_dependency_durably_blocks_safe_lease_recovery(
    client, seeded, monkeypatch,
):
    _session_id, run_id, _revision, _targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    first = resource_full_auto.claim_run(run_id)
    expired = resource_full_auto.claim_run(run_id, now_text=first.lease_expires_at)
    assert expired.requires_reconciliation is True

    workflow_id = _bound_ids(run_id)["workflow_id"]
    with db.transaction():
        db.run("DROP TRIGGER full_auto_protect_workflow_delete")
        db.run("DELETE FROM workflow WHERE id = ?", workflow_id)
    db.conn().executescript(db.SCHEMA)

    before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    view, next_claim = resource_full_auto.reconcile_expired_lease(
        expired,
        resource_full_auto.LeaseReconciliationEvidence(
            "safe_to_continue", unresolved_work=0,
        ),
        now_text=expired.lease_started_at,
    )
    assert next_claim is None
    assert view["state"] == "blocked"
    assert view["blocker_code"] == "dependency_missing"
    blocked = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert blocked["state"] == "blocked"
    assert blocked["blocker_code"] == "dependency_missing"
    assert blocked["revision"] == before["revision"] + 1
    assert blocked["fencing_token"] == before["fencing_token"] + 1
    assert blocked["lease_expires_at"] is None
    assert blocked["lease_started_at"] is None


def test_missing_bound_room_revision_blocks_without_using_a_later_revision(
    client, seeded, monkeypatch,
):
    _session_id, run_id, _revision, _targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    grant = resource_full_auto.current_authorization(run_id)
    room = grant["authorization"]["summary"]["room"]
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        room["library_key"],
    )
    bound_revision = resource_store.get_revision(
        library_id=library["id"],
        source_id=room["source_id"],
        content_digest=room["content_digest"],
    )
    assert bound_revision is not None
    resource_store.record_revision(
        library["id"], room["source_id"],
        {**bound_revision["payload"], "later_revision": True},
        translation=bound_revision["translation"],
        coverage=bound_revision["coverage"],
    )

    connection = db.conn()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with db.transaction():
            db.run(
                """DELETE FROM asset_revision
                   WHERE library_id = ? AND source_id = ? AND content_digest = ?""",
                library["id"], room["source_id"], room["content_digest"],
            )
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

    before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.claim_run(run_id)
    assert error.value.code == "dependency_missing"
    blocked = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert blocked["state"] == "blocked"
    assert blocked["blocker_code"] == "dependency_missing"
    assert blocked["revision"] == before["revision"] + 1
    assert blocked["fencing_token"] == before["fencing_token"] + 1
    assert blocked["lease_expires_at"] is None


def test_bound_room_translation_drift_remains_distinct_from_missing_dependency(
    client, seeded, monkeypatch,
):
    _session_id, run_id, _revision, _targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    grant = resource_full_auto.current_authorization(run_id)
    room = grant["authorization"]["summary"]["room"]
    library = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        room["library_key"],
    )
    revision = resource_store.get_revision(
        library_id=library["id"],
        source_id=room["source_id"],
        content_digest=room["content_digest"],
    )
    changed_translation = dict(revision["translation"])
    changed_translation["scene_theme"] = "Changed after authorization."
    resource_store.update_translation(revision["id"], changed_translation)

    before = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    with pytest.raises(resource_full_auto.FullAutoRunError) as error:
        resource_full_auto.claim_run(run_id)
    assert error.value.code == "dependency_changed"
    blocked = db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)
    assert blocked["state"] == "blocked"
    assert blocked["blocker_code"] == "dependency_changed"
    assert blocked["revision"] == before["revision"] + 1
    assert blocked["fencing_token"] == before["fencing_token"] + 1


def test_stale_coordinator_child_and_runner_cannot_touch_reused_session_or_folder(
    client, seeded, monkeypatch, make_runner, comfy_output, tmp_path,
):
    session_id, run_id, revision, targets = _create_active_run(
        client, seeded, monkeypatch,
    )
    run_claim = resource_full_auto.claim_run(run_id)
    resource_full_auto.initialize_prepare_progress(run_claim)
    request = authoring_operations.normalize_start_request({
        "request_id": str(uuid.uuid4()),
        "expected_revision": revision,
        "kind": "prepare_takes",
        "take_ids": targets,
    })
    operation, created = authoring_operations.start_operation(
        session_id, request, planning_enabled=True, assistant_available=True,
        _full_auto_claim=run_claim,
    )
    assert created is True
    operation_claim = authoring_operations.load_worker_claim(
        session_id, operation["operation_id"], planning_enabled=True,
        _full_auto_claim=run_claim,
    )
    ticket = authoring_operations.renew_operation_lease(
        operation_claim, planning_enabled=True, _full_auto_claim=run_claim,
    )
    old_shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, shot_label, status, created_at)
           VALUES (?, 0, 'old target', 'running', ?)""",
        session_id, db.now(),
    )

    with db.transaction():
        db.run("DROP TRIGGER full_auto_protect_session_delete")
        db.run("DROP TRIGGER full_auto_protect_shot_delete")
        db.run("DELETE FROM session WHERE id = ?", session_id)
    db.conn().executescript(db.SCHEMA)

    reused_id = db.run(
        """INSERT INTO session (model_id, name, settings, created_at)
           VALUES (?, 'reused session', '{}', ?)""",
        seeded["model_id"], db.now(),
    )
    assert reused_id == session_id
    reused_shot_id = db.run(
        """INSERT INTO shot (session_id, shot_index, shot_label, status, created_at)
           VALUES (?, 0, 'new target', 'running', ?)""",
        reused_id, db.now(),
    )
    assert reused_shot_id == old_shot_id

    runner, _fake = make_runner()
    session_folder = runner.sessions_dir / str(reused_id)
    session_folder.mkdir(parents=True, exist_ok=True)
    marker = session_folder / "belongs-to-new-session.txt"
    marker.write_text("preserve", encoding="utf-8")
    output = comfy_output / "old-result.png"
    output.write_bytes(b"old rendered result")
    heartbeat = SimpleNamespace(check=lambda: run_claim)
    before = db.one("SELECT * FROM shot WHERE id = ?", reused_shot_id)

    with pytest.raises(resource_full_auto.FullAutoRunError):
        runner._owned_update(
            reused_id, heartbeat, "UPDATE shot SET rating = 5 WHERE id = ?", reused_shot_id,
        )
    with pytest.raises(authoring_operations.AuthoringOperationError):
        authoring_operations.persist_operation_response(
            operation_claim, ticket, [], planning_enabled=True,
            _full_auto_claim=run_claim,
        )
    with pytest.raises(resource_full_auto.FullAutoRunError):
        resource_full_auto.advance_run(run_claim, "blocked", blocker_code="comfy_unavailable")
    with pytest.raises(resource_full_auto.FullAutoRunError):
        asyncio.run(runner._collect(
            reused_id,
            {"id": old_shot_id, "shot_label": "old target"},
            {"filename": output.name, "subfolder": "", "type": "output"},
            heartbeat,
        ))

    assert db.one("SELECT * FROM shot WHERE id = ?", reused_shot_id) == before
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert list(session_folder.iterdir()) == [marker]
    assert output.read_bytes() == b"old rendered result"
