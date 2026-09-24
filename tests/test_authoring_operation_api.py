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
    assert view["can_cancel"] is False and view["can_resume"] is False
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
    assert status.json() == started.json()

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
        "SELECT state, fencing_token, lease_expires_at, completed_json, "
        "remaining_json, result_json, updated_at FROM authoring_operation "
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
    for action in (
        lambda: authoring_operations.renew_operation_lease(claim),
        lambda: authoring_operations.persist_operation_response(claim, ticket, items),
    ):
        with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
            action()
        assert exc_info.value.code == expected_code
        assert _operation_snapshot(operation_id) == before
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
