"""API coverage for authoring operation start and read-only status."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
import sqlite3
import threading
import uuid

import db
import main
import resource_store
import pytest
from backend import authoring_operations


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


def _create_guided_session(client, seeded, *, mode="automatic", photo_count=4, look="", initial_wardrobe=""):
    response = client.post(
        "/api/sessions/guided",
        json={
            "request_id": str(uuid.uuid4()),
            "character_id": seeded["model_id"],
            "scene_anchor": _room_anchor(),
            "photo_count": photo_count,
            "mode": mode,
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


def _operation_snapshot(operation_id):
    row = db.one(
        "SELECT state, fencing_token, lease_expires_at, input_digest, completed_json, "
        "failed_item, error, remaining_json, result_json, updated_at FROM authoring_operation "
        "WHERE operation_id = ?",
        operation_id,
    )
    assert row is not None
    return row


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
        lambda: authoring_operations.persist_operation_response(claim, ticket, items),
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
    first_view = authoring_operations.persist_operation_response(
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
    assert first_view["result"] == {
        "items": [{"target": "take-001", "result": first_result}],
    }

    second_now = first_now + timedelta(minutes=2)
    monkeypatch.setattr(db, "now", lambda: second_now.isoformat(timespec="seconds"))
    second_ticket = authoring_operations.renew_operation_lease(claim)
    assert datetime.fromisoformat(second_ticket.lease_expires_at) == second_now + timedelta(minutes=10)
    assert not db.conn().in_transaction
    second_result = fake_remote_call()
    final_view = authoring_operations.persist_operation_response(
        claim,
        second_ticket,
        [{"target": "take-002", "result": second_result}],
    )
    assert final_view["state"] == "succeeded"
    assert final_view["lease_expires_at"] is None
    assert final_view["progress"]["completed"] == ["take-001", "take-002"]
    assert final_view["progress"]["remaining"] == []
    assert final_view["result"]["items"] == [
        {"target": "take-001", "result": first_result},
        {"target": "take-002", "result": second_result},
    ]
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
            return authoring_operations.persist_operation_response(
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
    partial = authoring_operations.persist_operation_response(
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
    assert expired.json()["result"]["items"] == [
        {"target": "take-001", "result": first_result},
    ]
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


def test_resume_retries_failed_item_before_remaining_without_repeating_completed_work(
    client, seeded, monkeypatch,
):
    session_id, operation_id, claim = _start_worker(
        client, seeded, monkeypatch,
        take_ids=["take-001", "take-002", "take-003"],
    )
    first_ticket = authoring_operations.renew_operation_lease(claim)
    first_result = {"choices": {"pose": "sitting beside a lamp"}}
    authoring_operations.persist_operation_response(
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
    assert resumed.json()["result"]["items"] == [
        {"target": "take-001", "result": first_result},
    ]
    assert _operation_snapshot(operation_id)["fencing_token"] == failed_fence + 1


def test_startup_expires_prior_active_owners_and_finalizes_cancel_requests(
    client, seeded, monkeypatch,
):
    first_session, first_id, _ = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001", "take-002"],
    )
    second_session, second_id, second_claim = _start_worker(
        client, seeded, monkeypatch, take_ids=["take-001"],
    )
    pending = client.post(
        f"/api/sessions/{second_session}/plan/authoring/operations/{second_id}/cancel",
        json={"expected_revision": second_claim.plan_revision},
    )
    assert pending.status_code == 202, pending.text

    recovered_count = authoring_operations.startup_recovery(
        planning_enabled=True,
        now=db.now(),
    )
    assert recovered_count == 2
    first = client.get(
        f"/api/sessions/{first_session}/plan/authoring/operations/{first_id}"
    ).json()
    second = client.get(
        f"/api/sessions/{second_session}/plan/authoring/operations/{second_id}"
    ).json()
    assert first["state"] == "expired"
    assert first["progress"]["remaining"] == ["take-001", "take-002"]
    assert "application restarted" in first["error"]
    assert second["state"] == "cancelled"
    assert second["progress"]["remaining"] == ["take-001"]
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
        authoring_operations.persist_operation_response(
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
        authoring_operations.persist_operation_response(
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
        authoring_operations.persist_operation_response(claim, ticket, items)
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
        authoring_operations.persist_operation_response(claim, forged_ticket, items)
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
        authoring_operations.persist_operation_response(
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
        authoring_operations.persist_operation_response(
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
            authoring_operations.persist_operation_response(
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
    authoring_operations.persist_operation_response(
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
        authoring_operations.persist_operation_response(
            claim,
            ticket,
            [{"target": "take-002", "result": {"pose": "late output"}}],
        )

    after_late_response = _operation_snapshot(operation_id)
    assert after_late_response["completed_json"] == before_late_response["completed_json"]
    assert after_late_response["remaining_json"] == before_late_response["remaining_json"]
    assert after_late_response["result_json"] == before_late_response["result_json"]
    assert json.loads(after_late_response["result_json"])["items"] == [
        {"target": "take-001", "result": first_result},
    ]
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
    authoring_operations.persist_operation_response(
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
    authoring_operations.persist_operation_response(
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
        authoring_operations.persist_operation_response(
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
