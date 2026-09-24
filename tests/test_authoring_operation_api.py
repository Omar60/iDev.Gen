"""API coverage for authoring operation start and read-only status."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import uuid

import db
import main
import resource_store


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
