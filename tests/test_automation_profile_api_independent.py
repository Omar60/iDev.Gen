from __future__ import annotations

import db
import main
import pytest
from backend import resource_planning, resource_store, saved_looks
from test_task13_appearance_ownership import (
    _profile,
    _saved_bracelet_look,
    _session,
)
from test_task9_13_looks_demonstration import _prepare


def _publish(client, saved, profile, expected_revision=0, *, content_digest=None):
    return client.post("/api/looks/automation-profiles", json={
        "look_key": saved["key"],
        "version": saved["version"],
        "content_digest": content_digest or saved["content_digest"],
        "expected_profile_revision": expected_revision,
        "profile": profile,
    })


def _read_params(saved, published, *, content_digest=None):
    return {
        "look_key": saved["key"],
        "version": saved["version"],
        "content_digest": content_digest or saved["content_digest"],
        "profile_revision": published["profile_revision"],
        "profile_digest": published["profile_digest"],
    }


def _apply_profile(client, session_id, saved, published, expected_revision):
    return client.post(
        f"/api/sessions/{session_id}/plan/apply-automation-profile",
        json={
            "expected_revision": expected_revision,
            "look_key": saved["key"],
            "version": saved["version"],
            "content_digest": saved["content_digest"],
            "profile_revision": published["profile_revision"],
            "profile_digest": published["profile_digest"],
            "decisions": {"look": "replace", "initial_wardrobe": "replace"},
        },
    )


def test_portable_export_digest_is_not_accepted_as_snapshot_identity(client):
    saved = _saved_bracelet_look()
    exported = client.get(
        f"/api/looks/{saved['key']}/versions/{saved['version']}/export",
    )
    assert exported.status_code == 200, exported.text
    portable_digest = resource_store.canonical_digest(exported.json())
    assert portable_digest != saved["content_digest"]

    profile = _profile(saved, bracelet_owner="garment", revision=1)
    before = db.q(
        "SELECT * FROM saved_look_automation_profile ORDER BY rowid",
    )
    refused = _publish(
        client, saved, profile, content_digest=portable_digest,
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "automation_profile_stale"
    assert db.q(
        "SELECT * FROM saved_look_automation_profile ORDER BY rowid",
    ) == before

    published_response = _publish(client, saved, profile)
    assert published_response.status_code == 200, published_response.text
    published = published_response.json()
    wrong_digest_read = client.get(
        "/api/looks/automation-profiles",
        params=_read_params(
            saved, published, content_digest=portable_digest,
        ),
    )
    assert wrong_digest_read.status_code == 409, wrong_digest_read.text
    assert wrong_digest_read.json()["detail"]["code"] == "automation_profile_stale"


def test_profile_read_rejects_duplicate_and_sqlite_overflow_query_values(client):
    saved = _saved_bracelet_look()
    profile = _profile(saved, bracelet_owner="garment", revision=1)
    published_response = _publish(client, saved, profile)
    assert published_response.status_code == 200, published_response.text
    published = published_response.json()
    params = _read_params(saved, published)

    duplicate = client.get(
        "/api/looks/automation-profiles",
        params=[
            ("look_key", params["look_key"]),
            ("version", str(params["version"])),
            ("content_digest", params["content_digest"]),
            ("profile_revision", str(params["profile_revision"])),
            ("profile_digest", params["profile_digest"]),
            ("version", str(params["version"])),
        ],
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["code"] == "invalid_request"
    assert set(duplicate.json()["detail"]) == {"code", "message"}

    overflow = dict(params, version=str(saved_looks._SQLITE_INTEGER_MAX + 1))
    too_large = client.get("/api/looks/automation-profiles", params=overflow)
    assert too_large.status_code == 422
    assert too_large.json()["detail"]["code"] == "invalid_request"
    assert set(too_large.json()["detail"]) == {"code", "message"}


def test_profile_application_through_http_invalidates_existing_preparation(
    client, seeded, monkeypatch,
):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.setattr(resource_planning, "is_enabled", lambda *_args: True)
    session_id, initial = _session(client, seeded, count=2, scene="A quiet studio.")
    prepared = _prepare(
        client, session_id, initial["plan_revision"], ["take-001"],
    )[0]
    saved = _saved_bracelet_look()
    published_response = _publish(
        client, saved, _profile(saved, bracelet_owner="garment", revision=1),
    )
    assert published_response.status_code == 200, published_response.text
    published = published_response.json()

    applied = _apply_profile(
        client, session_id, saved, published, initial["plan_revision"],
    )
    assert applied.status_code == 200, applied.text
    revision = applied.json()["plan_revision"]
    assert revision == initial["plan_revision"] + 1
    assert db.one(
        "SELECT status, final_prompt, linked_shot_id FROM prepared_take WHERE id = ?",
        prepared["id"],
    ) == {
        "status": "invalidated",
        "final_prompt": prepared["final_prompt"],
        "linked_shot_id": None,
    }
    evidence = db.one(
        "SELECT * FROM session_profile_evidence WHERE session_id = ? AND plan_revision = ?",
        session_id, revision,
    )
    assert evidence["look_key"] == saved["key"]
    assert evidence["look_version"] == saved["version"]
    assert evidence["look_content_digest"] == saved["content_digest"]
    assert evidence["profile_revision"] == published["profile_revision"]
    assert evidence["profile_digest"] == published["profile_digest"]


def test_profile_free_manual_application_remains_explicit_and_portable_v1_stays_closed(
    client, seeded, monkeypatch,
):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.setattr(resource_planning, "is_enabled", lambda *_args: True)
    session_id, initial = _session(client, seeded, count=1, scene="A quiet studio.")
    saved = _saved_bracelet_look()
    published_response = _publish(
        client, saved, _profile(saved, bracelet_owner="garment", revision=1),
    )
    assert published_response.status_code == 200, published_response.text

    manual = client.post(
        f"/api/sessions/{session_id}/plan/apply-look",
        json={
            "expected_revision": initial["plan_revision"],
            "look_key": saved["key"],
            "version": saved["version"],
            "decisions": {"look": "replace", "initial_wardrobe": "replace"},
        },
    )
    assert manual.status_code == 200, manual.text
    plan = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]
    assert plan["authoring"]["look_snapshot"]["content_digest"] == saved["content_digest"]
    assert plan["look"] == saved["appearance"]
    assert db.one(
        "SELECT * FROM session_profile_evidence WHERE session_id = ?",
        session_id,
    ) is None

    exported = client.get(
        f"/api/looks/{saved['key']}/versions/{saved['version']}/export",
    )
    assert exported.status_code == 200, exported.text
    assert set(exported.json()) == {"schema_version", "look", "garments", "provenance"}
    assert "automation_profile" not in exported.json()


@pytest.mark.parametrize(
    "sid",
    ["not-a-session-id", "-1", str(saved_looks._SQLITE_INTEGER_MAX + 1)],
)
def test_malformed_session_path_still_has_stable_profile_error_envelope(client, sid):
    response = client.post(
        f"/api/sessions/{sid}/plan/apply-automation-profile",
        json={},
    )
    assert response.status_code == 422
    assert isinstance(response.json().get("detail"), dict)
    assert response.json()["detail"]["code"] == "invalid_parameter"
    assert set(response.json()["detail"]) == {"code", "message"}


def test_invalid_decision_value_does_not_reflect_client_supplied_mapping_key(client):
    private_source = "private/source-fixture"
    response = client.post(
        "/api/sessions/1/plan/apply-automation-profile",
        json={
            "expected_revision": 1,
            "look_key": "fixture-look",
            "version": 1,
            "content_digest": "a" * 64,
            "profile_revision": 1,
            "profile_digest": "b" * 64,
            "decisions": {private_source: "invalid-choice"},
        },
    )
    assert response.status_code == 422
    assert set(response.json()["detail"]) == {"code", "message"}
    assert private_source not in response.text
