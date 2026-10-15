from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import threading

import db
import main
import pytest
from backend import resource_planning, saved_looks
from test_task13_appearance_ownership import (
    _profile,
    _saved_bracelet_look,
    _session,
    _state,
    _view,
)
from test_task9_13_looks_demonstration import _prepare


@pytest.fixture(autouse=True)
def _manual_profile_only(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setattr(resource_planning, "is_enabled", lambda *_args: True)

    def unexpected(*_args, **_kwargs):
        pytest.fail("profile API must not call an assistant or generate images")

    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)
    monkeypatch.setattr(main.enhance, "run_structured", unexpected)
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", unexpected)


def _slash_look():
    base = _saved_bracelet_look()
    key = "fixtures/nested/profile-look"
    snapshot = {
        "look_id": key,
        "version": 1,
        "content_digest": base["content_digest"],
        "appearance": base["appearance"],
        "outfit": base["outfit"],
    }
    db.run(
        """INSERT INTO saved_look_version
           (look_key, version, name, snapshot_json, created_at)
           VALUES (?, 1, ?, ?, ?)""",
        key, "Slash-key profile fixture",
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), db.now(),
    )
    return saved_looks.get_version(key, 1)


def _publish(client, saved, profile, expected_revision=0):
    return client.post("/api/looks/automation-profiles", json={
        "look_key": saved["key"],
        "version": saved["version"],
        "content_digest": saved["content_digest"],
        "expected_profile_revision": expected_revision,
        "profile": profile,
    })


def _read(client, saved, published):
    return client.get(
        "/api/looks/automation-profiles",
        params={
            "look_key": saved["key"],
            "version": saved["version"],
            "content_digest": saved["content_digest"],
            "profile_revision": published["profile_revision"],
            "profile_digest": published["profile_digest"],
        },
    )


def _apply_body(saved, published, expected_revision):
    return {
        "expected_revision": expected_revision,
        "look_key": saved["key"],
        "version": saved["version"],
        "content_digest": saved["content_digest"],
        "profile_revision": published["profile_revision"],
        "profile_digest": published["profile_digest"],
        "decisions": {"look": "replace", "initial_wardrobe": "replace"},
    }


def test_exact_profile_http_read_and_revision_publish_accept_slash_look_keys(client):
    saved = _slash_look()
    first_profile = _profile(saved, bracelet_owner="garment", revision=1)
    first_response = _publish(client, saved, first_profile)
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()

    discovery = client.get(
        f"/api/looks/{saved['key']}/versions/1/automation-profiles",
        params={"content_digest": saved["content_digest"]},
    )
    assert discovery.status_code == 200, discovery.text
    assert discovery.json()["look"]["key"] == saved["key"]
    assert [item["profile_revision"] for item in discovery.json()["profiles"]] == [1]

    next_profile = _profile(saved, bracelet_owner="garment", revision=2)
    next_profile["inventory_complete"]["value"] = False
    second_response = _publish(client, saved, next_profile, expected_revision=1)
    assert second_response.status_code == 200, second_response.text
    assert second_response.json()["profile_revision"] == 2

    historical = _read(client, saved, first)
    assert historical.status_code == 200, historical.text
    assert historical.json() == first

    extra_query = client.get(
        "/api/looks/automation-profiles",
        params=[
            ("look_key", saved["key"]), ("version", "1"),
            ("content_digest", saved["content_digest"]),
            ("profile_revision", str(first["profile_revision"])),
            ("profile_digest", first["profile_digest"]), ("latest", "true"),
        ],
    )
    assert extra_query.status_code == 422
    assert extra_query.json()["detail"]["code"] == "invalid_request"

    missing = client.get(
        "/api/looks/automation-profiles",
        params={
            "look_key": saved["key"], "version": 1,
            "content_digest": saved["content_digest"],
            "profile_revision": 3, "profile_digest": "a" * 64,
        },
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "automation_profile_not_found"


def test_http_profile_revision_cas_has_one_concurrent_winner(client):
    saved = _saved_bracelet_look()
    barrier = threading.Barrier(2)

    def publish(identity):
        profile = _profile(saved, bracelet_owner="garment", revision=1)
        profile["authored_provenance"]["source_identity"] = identity
        barrier.wait(timeout=10)
        return _publish(client, saved, profile)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(publish, ("editor-alpha", "editor-beta")))

    assert sorted(response.status_code for response in responses) == [200, 409]
    stale = next(response for response in responses if response.status_code == 409)
    assert stale.json()["detail"]["code"] == "automation_profile_stale"
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_automation_profile "
        "WHERE look_key = ? AND look_version = ?",
        saved["key"], saved["version"],
    )["count"] == 1


def test_profile_api_rejects_open_or_unbounded_inputs_without_reflecting_source_data(
    client, monkeypatch,
):
    saved = _saved_bracelet_look()
    profile = _profile(saved, bracelet_owner="garment", revision=1)
    profile["authored_provenance"]["source_identity"] = "private/source-fixture"
    profile["authored_provenance"]["source_digest"] = "0" * 64
    before = db.one(
        "SELECT COUNT(*) AS count FROM saved_look_automation_profile",
    )["count"]

    rejected = _publish(client, saved, profile)
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "invalid_look"
    assert "private/source-fixture" not in rejected.text
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_automation_profile",
    )["count"] == before

    body = {
        "look_key": saved["key"], "version": saved["version"],
        "content_digest": saved["content_digest"],
        "expected_profile_revision": 0, "profile": profile,
        "unexpected": "private/source-fixture",
    }
    closed = client.post("/api/looks/automation-profiles", json=body)
    assert closed.status_code == 422
    assert closed.json()["detail"]["code"] == "extra_field_forbidden"
    assert "private/source-fixture" not in closed.text

    body.pop("unexpected")
    body["version"] = saved_looks._SQLITE_INTEGER_MAX + 1
    bounded = client.post("/api/looks/automation-profiles", json=body)
    assert bounded.status_code == 422
    assert "detail" in bounded.json()
    assert "private/source-fixture" not in bounded.text

    body["version"] = True
    strict = client.post("/api/looks/automation-profiles", json=body)
    assert strict.status_code == 422
    assert strict.json()["detail"]["code"] == "invalid_request"

    def unexpected_failure(*_args, **_kwargs):
        raise RuntimeError("private/source-fixture")

    monkeypatch.setattr(saved_looks, "publish_automation_profile", unexpected_failure)
    body["version"] = saved["version"]
    body["profile"] = _profile(saved, bracelet_owner="garment", revision=1)
    failed = client.post("/api/looks/automation-profiles", json=body)
    assert failed.status_code == 500
    assert failed.json()["detail"]["code"] == "internal_error"
    assert "private/source-fixture" not in failed.text


def test_version_scoped_discovery_and_reviewed_copy_preview_are_read_only(client):
    saved = _saved_bracelet_look()
    first = _publish(client, saved, _profile(saved, bracelet_owner="garment", revision=1))
    assert first.status_code == 200, first.text
    profile = first.json()

    discovery = client.get(
        f"/api/looks/{saved['key']}/versions/{saved['version']}/automation-profiles",
        params={"content_digest": saved["content_digest"]},
    )
    assert discovery.status_code == 200, discovery.text
    assert discovery.json()["look"] == {
        "key": saved["key"], "version": saved["version"],
        "content_digest": saved["content_digest"],
    }
    assert discovery.json()["latest_profile_revision"] == 1
    assert discovery.json()["profiles"][0]["profile_revision"] == 1
    assert discovery.json()["profiles"][0]["profile_digest"] == profile["profile_digest"]
    assert discovery.json()["profiles"][0]["readiness"]["manual_available"] is True
    assert discovery.json()["profiles"][0]["readiness"]["diagnostics"]

    before = db.q("SELECT * FROM saved_look_automation_profile ORDER BY rowid")
    preview = client.post("/api/looks/automation-profiles/copy-preview", json={
        "target_look_key": saved["key"],
        "target_version": saved["version"],
        "target_content_digest": saved["content_digest"],
        "expected_profile_revision": 1,
        "source_look_key": saved["key"],
        "source_version": saved["version"],
        "source_content_digest": saved["content_digest"],
        "source_profile_revision": profile["profile_revision"],
        "source_profile_digest": profile["profile_digest"],
        "selections": [{"garment_key": saved["outfit"]["garments"][0]["key"], "state": "worn"}],
    })
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert "selections" not in result
    key = saved["outfit"]["garments"][0]["key"]
    copied = result["garment_coverage"][key]["worn"]
    assert copied["regions"] == profile["garment_coverage"][key]["worn"]["regions"]
    assert copied["provenance"] == {
        "origin": "reviewed_structured",
        "source_identity": f"automation-profile:{profile['profile_digest']}",
        "source_digest": profile["profile_digest"],
        "accepted_revision": 2,
    }
    assert result["skipped"] == []
    assert db.q("SELECT * FROM saved_look_automation_profile ORDER BY rowid") == before

    mapped_saved = saved_looks.create({
        "name": "Reviewed moved-aside state",
        "appearance": saved["appearance"],
        "garments": [{"wording": "a brass bracelet", "aside": "bracelet moved aside"}],
    })
    mapped_key = mapped_saved["outfit"]["garments"][0]["key"]
    mapped_profile_input = _profile(mapped_saved, bracelet_owner="garment", revision=1)
    mapped_profile_input["garment_coverage"][mapped_key]["aside"] = {
        "regions": {region: "none" for region in ("chest", "hips", "legs", "feet")},
        "provenance": mapped_profile_input["garment_coverage"][mapped_key]["worn"]["provenance"],
    }
    mapped_profile_response = _publish(client, mapped_saved, mapped_profile_input)
    assert mapped_profile_response.status_code == 200, mapped_profile_response.text
    mapped_profile = mapped_profile_response.json()
    equivalent_state_preview = client.post("/api/looks/automation-profiles/copy-preview", json={
        "target_look_key": mapped_saved["key"],
        "target_version": mapped_saved["version"],
        "target_content_digest": mapped_saved["content_digest"],
        "expected_profile_revision": 0,
        "source_look_key": mapped_saved["key"],
        "source_version": mapped_saved["version"],
        "source_content_digest": mapped_saved["content_digest"],
        "source_profile_revision": mapped_profile["profile_revision"],
        "source_profile_digest": mapped_profile["profile_digest"],
        "selections": [{"garment_key": mapped_key, "source_state": "worn", "state": "aside"}],
    })
    assert equivalent_state_preview.status_code == 200, equivalent_state_preview.text
    mapped = equivalent_state_preview.json()
    assert mapped["selections"] == [{
        "garment_key": mapped_key, "source_state": "worn", "state": "aside",
    }]
    assert mapped["garment_coverage"][mapped_key]["aside"]["regions"] == mapped_profile["garment_coverage"][mapped_key]["worn"]["regions"]
    assert mapped["garment_coverage"][mapped_key]["aside"]["provenance"]["source_digest"] == mapped_profile["profile_digest"]

    mapped_next = saved_looks.create_version(mapped_saved["key"], mapped_saved["version"], {
        "name": "Same states in the next version",
        "appearance": mapped_saved["appearance"],
        "garments": [{
            "key": mapped_key, "wording": "a brass bracelet", "aside": "bracelet moved aside",
        }],
    })
    cross_version_mapping = client.post("/api/looks/automation-profiles/copy-preview", json={
        "target_look_key": mapped_next["key"],
        "target_version": mapped_next["version"],
        "target_content_digest": mapped_next["content_digest"],
        "expected_profile_revision": 0,
        "source_look_key": mapped_saved["key"],
        "source_version": mapped_saved["version"],
        "source_content_digest": mapped_saved["content_digest"],
        "source_profile_revision": mapped_profile["profile_revision"],
        "source_profile_digest": mapped_profile["profile_digest"],
        "selections": [{"garment_key": mapped_key, "source_state": "worn", "state": "aside"}],
    })
    assert cross_version_mapping.status_code == 200, cross_version_mapping.text
    assert cross_version_mapping.json()["garment_coverage"] == {}
    assert cross_version_mapping.json()["skipped"] == [{
        "garment_key": mapped_key, "source_state": "worn", "state": "aside", "code": "state_changed",
    }]

    changed_garments = [{
        "key": item["key"],
        "wording": "a silver bracelet",
        "aside": item["aside"],
    } for item in saved["outfit"]["garments"]]
    changed = saved_looks.create_version(saved["key"], saved["version"], {
        "name": "Changed garment state",
        "appearance": saved["appearance"],
        "garments": changed_garments,
    })
    changed_preview = client.post("/api/looks/automation-profiles/copy-preview", json={
        "target_look_key": changed["key"],
        "target_version": changed["version"],
        "target_content_digest": changed["content_digest"],
        "expected_profile_revision": 0,
        "source_look_key": saved["key"],
        "source_version": saved["version"],
        "source_content_digest": saved["content_digest"],
        "source_profile_revision": profile["profile_revision"],
        "source_profile_digest": profile["profile_digest"],
        "selections": [{"garment_key": key, "state": "worn"}],
    })
    assert changed_preview.status_code == 200, changed_preview.text
    assert changed_preview.json()["garment_coverage"] == {}
    assert changed_preview.json()["skipped"] == [
        {"garment_key": key, "state": "worn", "code": "state_changed"},
    ]

    cross_version_state_mapping = client.post("/api/looks/automation-profiles/copy-preview", json={
        "target_look_key": changed["key"],
        "target_version": changed["version"],
        "target_content_digest": changed["content_digest"],
        "expected_profile_revision": 0,
        "source_look_key": saved["key"],
        "source_version": saved["version"],
        "source_content_digest": saved["content_digest"],
        "source_profile_revision": profile["profile_revision"],
        "source_profile_digest": profile["profile_digest"],
        "selections": [{"garment_key": key, "source_state": "worn", "state": "aside"}],
    })
    assert cross_version_state_mapping.status_code == 200, cross_version_state_mapping.text
    assert cross_version_state_mapping.json()["garment_coverage"] == {}
    assert cross_version_state_mapping.json()["skipped"] == [
        {"garment_key": key, "source_state": "worn", "state": "aside", "code": "state_changed"},
    ]

    duplicate_query = client.get(
        f"/api/looks/{saved['key']}/versions/{saved['version']}/automation-profiles",
        params=[("content_digest", saved["content_digest"]), ("content_digest", saved["content_digest"])],
    )
    assert duplicate_query.status_code == 422
    assert duplicate_query.json()["detail"]["code"] == "invalid_request"


def test_profile_application_http_uses_exact_identity_and_preserves_rows_on_refusal(
    client, seeded,
):
    session_id, initial = _session(client, seeded, count=2, scene="A quiet studio.")
    saved = _saved_bracelet_look()
    profile_response = _publish(
        client, saved, _profile(saved, bracelet_owner="garment", revision=1),
    )
    assert profile_response.status_code == 200, profile_response.text
    profile = profile_response.json()

    before = _state(session_id)
    malformed = _apply_body(saved, profile, initial["plan_revision"])
    malformed["expected_revision"] = True
    invalid = client.post(
        f"/api/sessions/{session_id}/plan/apply-automation-profile",
        json=malformed,
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_revision"
    assert _state(session_id) == before

    applied = client.post(
        f"/api/sessions/{session_id}/plan/apply-automation-profile",
        json=_apply_body(saved, profile, initial["plan_revision"]),
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["plan_revision"] == initial["plan_revision"] + 1
    accepted = _view(client, session_id)
    assert accepted["plan"]["authoring"]["look_snapshot"]["content_digest"] == saved["content_digest"]
    assert accepted["plan"]["look"] == "A portrait in a quiet room."
    assert db.one(
        "SELECT profile_digest FROM session_profile_evidence "
        "WHERE session_id = ? AND plan_revision = ?",
        session_id, applied.json()["plan_revision"],
    )["profile_digest"] == profile["profile_digest"]

    before = _state(session_id)
    stale_revision = client.post(
        f"/api/sessions/{session_id}/plan/apply-automation-profile",
        json=_apply_body(saved, profile, initial["plan_revision"]),
    )
    assert stale_revision.status_code == 409
    assert stale_revision.json()["detail"]["code"] == "plan_revision_stale"
    assert _state(session_id) == before

    tampered_content = dict(_apply_body(saved, profile, applied.json()["plan_revision"]))
    tampered_content["content_digest"] = "0" * 64
    stale_look = client.post(
        f"/api/sessions/{session_id}/plan/apply-automation-profile",
        json=tampered_content,
    )
    assert stale_look.status_code == 409
    assert stale_look.json()["detail"]["code"] == "automation_profile_stale"
    assert _state(session_id) == before

    tampered_profile = dict(_apply_body(saved, profile, applied.json()["plan_revision"]))
    tampered_profile["profile_digest"] = "0" * 64
    stale_profile = client.post(
        f"/api/sessions/{session_id}/plan/apply-automation-profile",
        json=tampered_profile,
    )
    assert stale_profile.status_code == 409
    assert stale_profile.json()["detail"]["code"] == "automation_profile_stale"
    assert _state(session_id) == before


def test_plan_readback_exposes_only_exact_profile_identity_and_fails_closed(client, seeded):
    session_id, initial = _session(client, seeded, count=2, scene="A quiet studio.")
    legacy_readback = client.get(f"/api/sessions/{session_id}/plan")
    assert legacy_readback.status_code == 200, legacy_readback.text
    assert "profile_application" not in legacy_readback.json()
    saved = _saved_bracelet_look()
    first_response = _publish(
        client, saved, _profile(saved, bracelet_owner="garment", revision=1),
    )
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    applied = client.post(
        f"/api/sessions/{session_id}/plan/apply-automation-profile",
        json=_apply_body(saved, first, initial["plan_revision"]),
    )
    assert applied.status_code == 200, applied.text

    readback = client.get(f"/api/sessions/{session_id}/plan")
    assert readback.status_code == 200, readback.text
    assert readback.json()["profile_application"] == {
        "look_key": saved["key"],
        "version": saved["version"],
        "content_digest": saved["content_digest"],
        "profile_revision": first["profile_revision"],
        "profile_digest": first["profile_digest"],
    }

    second_profile = _profile(saved, bracelet_owner="garment", revision=2)
    second = _publish(client, saved, second_profile, expected_revision=1).json()
    session2, initial2 = _session(client, seeded, count=2, scene="A quiet studio.")
    applied2 = client.post(
        f"/api/sessions/{session2}/plan/apply-automation-profile",
        json=_apply_body(saved, second, initial2["plan_revision"]),
    )
    assert applied2.status_code == 200, applied2.text
    readback2 = client.get(f"/api/sessions/{session2}/plan").json()
    assert readback2["plan"]["look"] == readback.json()["plan"]["look"]
    assert readback2["profile_application"]["profile_revision"] == 2
    assert readback2["profile_application"]["profile_digest"] != first["profile_digest"]

    db.run("DROP TRIGGER session_profile_evidence_immutable")
    db.run(
        "UPDATE session_profile_evidence SET profile_digest = ? WHERE session_id = ? AND plan_revision = ?",
        "0" * 64, session2, applied2.json()["plan_revision"],
    )
    inconsistent = client.get(f"/api/sessions/{session2}/plan")
    assert inconsistent.status_code == 500
    assert inconsistent.json() == {
        "detail": {
            "code": "profile_application_invalid",
            "message": "Stored session profile identity is inconsistent; reload is unavailable.",
        },
    }

def test_linked_shot_freezes_profile_identity_through_http(client, seeded):
    session_id, initial = _session(client, seeded, count=1, scene="A quiet studio.")
    saved = _saved_bracelet_look()
    first_response = _publish(
        client, saved, _profile(saved, bracelet_owner="garment", revision=1),
    )
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    applied = client.post(
        f"/api/sessions/{session_id}/plan/apply-automation-profile",
        json=_apply_body(saved, first, initial["plan_revision"]),
    )
    assert applied.status_code == 200, applied.text

    _prepare(client, session_id, applied.json()["plan_revision"], ["take-001"])
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": applied.json()["plan_revision"]},
    )
    assert approved.status_code == 200, approved.text
    linked = client.post(f"/api/sessions/{session_id}/plan/preparations/submit", json={
        "plan_revision": applied.json()["plan_revision"], "take_id": "take-001",
    })
    assert linked.status_code == 200, linked.text

    second_profile = _profile(saved, bracelet_owner="garment", revision=2)
    second_profile["authored_provenance"]["source_identity"] = "profile-editor-revision-two"
    revised_response = _publish(client, saved, second_profile, expected_revision=1)
    assert revised_response.status_code == 200, revised_response.text
    before = _state(session_id)

    frozen = client.post(
        f"/api/sessions/{session_id}/plan/apply-automation-profile",
        json=_apply_body(saved, revised_response.json(), applied.json()["plan_revision"]),
    )
    assert frozen.status_code == 409
    assert frozen.json()["detail"]["code"] == "profile_frozen"
    assert _state(session_id) == before
