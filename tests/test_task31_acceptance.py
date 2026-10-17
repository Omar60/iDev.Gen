"""Independent acceptance for guided saved-look and Full Auto creation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import threading
import uuid

import pytest

import db
import main
import resource_store
from backend import guided_sessions, saved_looks, workflow_binding
from test_automation_profile_readiness_independent import (
    _profile as _ready_profile,
    _publish as _publish_profile,
    _saved_look as _make_look,
)
from test_task13_appearance_ownership import (
    _profile as _ownership_profile,
    _saved_bracelet_look,
)
from test_saved_look_application_acceptance import _expected_snapshot


PREFLIGHT_PATH = "/api/sessions/guided/full-auto/preflight"
ALL_VARY = {
    field: {"mode": "vary"}
    for field in ("camera", "framing", "pose", "expression")
}
REGIONS = ("chest", "hips", "legs", "feet")
_ALLOCATION_TABLES = {
    "session",
    "session_plan",
    "guided_session_request",
    "session_profile_evidence",
    "session_profile_coverage_revision",
    "session_take_coverage_evidence",
}


@pytest.fixture(autouse=True)
def _configured_fake_provider(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "task31-test-provider")
    monkeypatch.setitem(main.CONFIG, "llm_key", "task31-test-secret")


def _allocation_counts() -> dict[str, int]:
    names = {
        row["name"]
        for row in db.q(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    relevant = _ALLOCATION_TABLES | {name for name in names if "full_auto" in name}
    return {
        name: int(db.one(f"SELECT COUNT(*) AS n FROM {name}")["n"])
        for name in sorted(names & relevant)
    }


def _room_anchor() -> dict[str, str]:
    library_key = f"task31-room-{uuid.uuid4().hex}"
    source_id = "scene-001"
    label = "invented studio"
    theme = "Soft light enters an empty studio from a high window."
    library_id = resource_store.ensure_library(library_key, kind="rooms")
    revision_id = resource_store.record_revision(
        library_id,
        source_id,
        {"id": source_id, "label": label, "scene_theme": theme},
        translation={"label": label, "scene_theme": theme},
    )
    revision = resource_store.get_revision(revision_id=revision_id)
    assert revision is not None
    return {
        "library_key": library_key,
        "source_id": source_id,
        "content_digest": revision["content_digest"],
    }


def _saved_look_wire(saved: dict, *, digest: str | None = None) -> dict:
    return {
        "look_key": saved["key"],
        "version": saved["version"],
        "content_digest": digest or saved["content_digest"],
    }


def _full_auto_base(
    seeded,
    anchor: dict[str, str],
    saved: dict,
    published: dict,
    *,
    photo_count: int = 8,
    framing_intent: str = "vary",
) -> dict:
    return {
        "character_id": seeded["model_id"],
        "scene_anchor": anchor,
        "photo_count": photo_count,
        "saved_look": _saved_look_wire(saved),
        "full_auto": {
            "authorization_version": 1,
            "generate_without_pauses": True,
            "profile_revision": published["profile_revision"],
            "profile_digest": published["profile_digest"],
            "progression": "all_stages_full_interval",
            "framing_intent": framing_intent,
        },
    }


def _full_auto_fixture(
    seeded,
    *,
    garments: bool = True,
    anchor: dict[str, str] | None = None,
    photo_count: int = 8,
):
    saved = _make_look(garments=garments)
    published = _publish_profile(saved, _ready_profile(saved))
    return (
        saved,
        published,
        _full_auto_base(
            seeded, anchor or _room_anchor(), saved, published,
            photo_count=photo_count,
        ),
    )


def _create_from_preflight(client, base: dict, consent: dict, *, request_id=None):
    payload = deepcopy(base)
    payload["request_id"] = request_id or str(uuid.uuid4())
    payload["full_auto"]["scope_digest"] = consent["scope_digest"]
    payload["full_auto"]["scope_issued_at"] = consent["scope_issued_at"]
    return client.post("/api/sessions/guided", json=payload), payload


def _preflight(client, base: dict):
    response = client.post(PREFLIGHT_PATH, json=base)
    assert response.status_code == 200, response.text
    result = response.json()
    assert isinstance(result.get("scope_digest"), str)
    assert len(result["scope_digest"]) == 64
    assert isinstance(result.get("scope_issued_at"), str)
    issued_at = datetime.fromisoformat(result["scope_issued_at"].replace("Z", "+00:00"))
    assert issued_at.tzinfo is not None
    return result


def _assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    detail = response.json().get("detail")
    assert isinstance(detail, dict), response.text
    assert detail.get("code") == code
    assert isinstance(detail.get("message"), str) and detail["message"]
    return detail


def _install_t2i_workflow(client, seeded) -> int:
    existing = db.one("SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"])
    assert existing is not None
    graph = json.loads(existing["graph"])
    created = client.post("/api/workflows", json={
        "name": f"task31-t2i-{uuid.uuid4().hex}",
        "graph": graph,
        "kind": "t2i",
    })
    assert created.status_code == 200, created.text
    workflow_id = created.json()["id"]
    db.run("UPDATE model SET workflow_id = ? WHERE id = ?", workflow_id, seeded["model_id"])
    return workflow_id


def _forbid_external_work(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("Task 3.1 creation/preflight must not call an assistant or run generation")

    monkeypatch.setattr(main.enhance, "_request_completion", unexpected)
    monkeypatch.setattr(main.enhance, "run_structured", unexpected)
    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)


def _private_provider_url(user: str, password: str, host: str, path: str, token: str) -> str:
    return "https://" + user + ":" + password + "@" + host + "/" + path + "?token=" + token


def _contains_key_value(value, key: str, expected) -> bool:
    if isinstance(value, dict):
        return (key in value and value[key] == expected) or any(
            _contains_key_value(child, key, expected) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key_value(child, key, expected) for child in value)
    return False


def _assert_refreshed_scope(response, previous_scope: dict) -> dict:
    detail = _assert_error(response, 409, "authorization_scope_changed")
    refreshed = detail.get("current")
    assert isinstance(refreshed, dict) and isinstance(refreshed.get("summary"), dict)
    assert refreshed["summary"] != previous_scope["summary"]
    assert refreshed["scope_digest"] == resource_store.canonical_digest(refreshed["summary"])
    assert refreshed["scope_digest"] != previous_scope["scope_digest"]
    return detail


def _assert_no_secret_echo(response, *secrets: str):
    body = response.text.lower()
    for secret in secrets:
        assert secret.lower() not in body


def test_omitted_full_auto_fields_preserve_legacy_digest_and_response_replay(
    client, seeded,
):
    request_id = str(uuid.uuid4())
    anchor = _room_anchor()
    old_payload = {
        "request_id": request_id,
        "character_id": seeded["model_id"],
        "scene_anchor": anchor,
        "photo_count": 2,
    }
    normalized = guided_sessions.normalize_request(old_payload)
    legacy_body = {
        "character_id": seeded["model_id"],
        "workflow_id": None,
        "scene_anchor": anchor,
        "photo_count": 2,
        "brief": "",
        "mode": "automatic",
        "variation_policy": ALL_VARY,
        "look": "",
        "initial_wardrobe": "",
    }
    assert normalized.body == legacy_body
    assert normalized.body_digest == resource_store.canonical_digest(legacy_body)
    assert "saved_look" not in normalized.body
    assert "full_auto" not in normalized.body

    first = client.post("/api/sessions/guided", json=old_payload)
    assert first.status_code == 201, first.text
    replay = client.post("/api/sessions/guided", json=old_payload)
    assert replay.status_code == 200, replay.text
    assert replay.content == first.content


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body["saved_look"].update(extra="forbidden"),
        lambda body: body["saved_look"].update(version=True),
        lambda body: body["saved_look"].update(version=2**63),
        lambda body: body["saved_look"].update(content_digest="A" * 64),
        lambda body: body["full_auto"].update(extra="forbidden"),
        lambda body: body["full_auto"].update(authorization_version=True),
        lambda body: body["full_auto"].update(generate_without_pauses=1),
        lambda body: body["full_auto"].update(profile_revision="1"),
        lambda body: body["full_auto"].update(profile_revision=2**63),
        lambda body: body["full_auto"].update(profile_digest="not-a-digest"),
        lambda body: body.update(character_id=2**63),
        lambda body: body.update(workflow_id=2**63),
        lambda body: body.update(photo_count=2**63),
        lambda body: body.update(photo_count=0),
        lambda body: body.update(photo_count=501),
    ],
    ids=[
        "saved-look-extra", "saved-look-bool-version", "sqlite-version-overflow",
        "noncanonical-saved-look-digest", "full-auto-extra", "bool-auth-version",
        "integer-is-not-boolean", "string-profile-revision", "sqlite-profile-overflow",
        "invalid-profile-digest", "sqlite-character-overflow", "sqlite-workflow-overflow",
        "photo-count-overflow", "count-below-one", "count-above-five-hundred",
    ],
)
def test_preflight_rejects_coercions_and_sqlite_overflow_without_allocation(
    client, seeded, monkeypatch, mutation,
):
    _forbid_external_work(monkeypatch)
    _install_t2i_workflow(client, seeded)
    _saved, _published, base = _full_auto_fixture(seeded)
    mutation(base)
    before = _allocation_counts()

    response = client.post(PREFLIGHT_PATH, json=base)

    assert response.status_code == 422, response.text
    detail = response.json().get("detail")
    assert isinstance(detail, dict) and detail.get("code")
    assert _allocation_counts() == before


def test_manual_saved_look_creation_uses_exact_snapshot_without_profile_or_run(
    client, seeded, monkeypatch,
):
    _forbid_external_work(monkeypatch)
    saved = _make_look()
    latest = client.post(
        f"/api/looks/{saved['key']}/versions",
        json={
            "expected_version": saved["version"],
            "name": "Newer look version after selection",
            "appearance": "A changed appearance in the latest version.",
        },
    )
    assert latest.status_code == 200, latest.text
    assert latest.json()["version"] == saved["version"] + 1
    anchor = _room_anchor()
    payload = {
        "request_id": str(uuid.uuid4()),
        "character_id": seeded["model_id"],
        "scene_anchor": anchor,
        "photo_count": 2,
        "mode": "manual",
        "saved_look": _saved_look_wire(saved),
    }

    response = client.post("/api/sessions/guided", json=payload)

    assert response.status_code == 201, response.text
    result = response.json()
    assert "full_auto_run_id" not in result
    plan = result["plan"]
    assert plan["authoring"]["look_snapshot"] == _expected_snapshot(saved)
    assert plan["look"] == saved["appearance"]
    assert plan["authoring"]["wardrobe_progression"] is None
    assert plan["wardrobe_changes"] == []
    assert db.one(
        "SELECT 1 FROM session_profile_evidence WHERE session_id = ?",
        result["session_id"],
    ) is None
    assert not any(
        name.startswith("full_auto") and count
        for name, count in _allocation_counts().items()
    )


def test_guided_creation_rejects_an_archived_saved_look_without_allocating(client, seeded, monkeypatch):
    _forbid_external_work(monkeypatch)
    saved = _make_look()
    archived = client.delete(f"/api/looks/{saved['key']}")
    assert archived.status_code == 200, archived.text
    payload = {
        "request_id": str(uuid.uuid4()),
        "character_id": seeded["model_id"],
        "scene_anchor": _room_anchor(),
        "photo_count": 2,
        "mode": "manual",
        "saved_look": _saved_look_wire(saved),
    }
    before = _allocation_counts()

    response = client.post("/api/sessions/guided", json=payload)

    _assert_error(response, 409, "look_archived")
    assert _allocation_counts() == before


@pytest.mark.parametrize("photo_count", [1, 500])
def test_full_auto_accepts_count_endpoints_with_single_appearance_stage(
    client, seeded, monkeypatch, photo_count,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    saved, published, base = _full_auto_fixture(
        seeded, garments=False, photo_count=photo_count,
    )
    before = _allocation_counts()

    consent = _preflight(client, base)

    assert _allocation_counts() == before
    created, _payload = _create_from_preflight(client, base, consent)
    assert created.status_code == 201, created.text
    result = created.json()
    assert "full_auto_run_id" in result
    assert len(result["plan"]["takes"]) == photo_count
    assert result["plan"]["authoring"]["look_snapshot"] == _expected_snapshot(saved)
    assert result["plan"]["wardrobe_changes"] == []
    assert db.one(
        "SELECT COUNT(*) AS n FROM session_take_coverage_evidence WHERE session_id = ?",
        result["session_id"],
    )["n"] == photo_count
    assert db.one(
        "SELECT profile_revision, profile_digest, look_content_digest FROM session_profile_evidence "
        "WHERE session_id = ? AND plan_revision = 1",
        result["session_id"],
    ) == {
        "profile_revision": published["profile_revision"],
        "profile_digest": published["profile_digest"],
        "look_content_digest": saved["content_digest"],
    }


def test_preflight_and_create_reject_portable_digest_and_too_few_stages_without_writes(
    client, seeded, monkeypatch,
):
    _forbid_external_work(monkeypatch)
    _install_t2i_workflow(client, seeded)
    saved, _published, base = _full_auto_fixture(seeded, photo_count=1)
    exported = saved_looks.export_version(saved["key"], saved["version"])
    portable = saved_looks.portable_content_digest(exported)
    assert portable != saved["content_digest"]

    before = _allocation_counts()
    portable_payload = deepcopy(base)
    portable_payload["saved_look"]["content_digest"] = portable
    portable_response = client.post(PREFLIGHT_PATH, json=portable_payload)
    _assert_error(portable_response, 409, "saved_look_changed")
    assert _allocation_counts() == before

    manual_payload = {
        "request_id": str(uuid.uuid4()),
        "character_id": seeded["model_id"],
        "scene_anchor": base["scene_anchor"],
        "photo_count": 2,
        "mode": "manual",
        "saved_look": _saved_look_wire(saved, digest=portable),
    }
    manual_refusal = client.post("/api/sessions/guided", json=manual_payload)
    _assert_error(manual_refusal, 409, "saved_look_changed")
    assert _allocation_counts() == before

    too_few = client.post(PREFLIGHT_PATH, json=base)
    count_detail = _assert_error(too_few, 422, "insufficient_stage_count")
    stage_count = len(saved_looks._automation_profile_coverage_stage_states(saved))
    assert _contains_key_value(count_detail, "required_count", stage_count)
    count_message = json.dumps(count_detail).lower()
    assert "manual" in count_message or "progression" in count_message
    assert _allocation_counts() == before


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("automation_profile_not_ready", 422, "automation_profile_not_ready"),
        ("coverage_unknown", 422, "coverage_unknown"),
        ("inventory_unconfirmed", 422, "inventory_unconfirmed"),
        ("appearance_progression_conflict", 422, "appearance_progression_conflict"),
        ("baseline_progression_conflict", 422, "baseline_progression_conflict"),
        ("full_auto_workflow_unsupported", 422, "full_auto_workflow_unsupported"),
        ("framing_conflict", 422, "framing_conflict"),
        ("saved_look_changed", 409, "saved_look_changed"),
        ("automation_profile_changed", 409, "automation_profile_changed"),
    ],
)
def test_decision14_preflight_errors_are_stable_safe_and_zero_write(
    client, seeded, monkeypatch, case, status, code,
):
    _forbid_external_work(monkeypatch)
    workflow_id = _install_t2i_workflow(client, seeded)
    anchor = _room_anchor()
    if case == "appearance_progression_conflict":
        saved = _saved_bracelet_look()
        profile = _ownership_profile(saved, bracelet_owner="permanent")
        provenance = profile["authored_provenance"]
        profile["baseline_coverage"] = {
            "regions": {region: "none" for region in REGIONS},
            "provenance": provenance,
        }
        profile["inventory_complete"]["value"] = True
        for garment in profile["garment_coverage"].values():
            for state in garment.values():
                state["regions"] = {region: "none" for region in REGIONS}
        published = _publish_profile(saved, profile)
    else:
        saved = _make_look()
        profile = _ready_profile(saved, baseline="full" if case == "baseline_progression_conflict" else "none")
        if case == "coverage_unknown":
            final_key = saved["outfit"]["garments"][-1]["key"]
            profile["garment_coverage"][final_key]["aside"]["regions"]["feet"] = "unknown"
        elif case == "inventory_unconfirmed":
            profile["inventory_complete"]["value"] = None
        published = None if case == "automation_profile_not_ready" else _publish_profile(saved, profile)

    if published is None:
        published = {
            "profile_revision": 1,
            "profile_digest": "a" * 64,
        }
    base = _full_auto_base(seeded, anchor, saved, published)
    if case == "full_auto_workflow_unsupported":
        created = client.post("/api/workflows", json={
            "name": f"task31-guide-{uuid.uuid4().hex}",
            "graph": json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", workflow_id,
            )["graph"]),
            "kind": "guide",
        })
        assert created.status_code == 200, created.text
        base["workflow_id"] = created.json()["id"]
    elif case == "framing_conflict":
        base["full_auto"]["framing_intent"] = "full_body"
        base["variation_policy"] = deepcopy(ALL_VARY)
        base["variation_policy"]["framing"] = {
            "mode": "fixed", "value": "tight portrait crop", "value_origin": "user",
        }
    elif case == "saved_look_changed":
        portable = saved_looks.portable_content_digest(
            saved_looks.export_version(saved["key"], saved["version"]),
        )
        base["saved_look"]["content_digest"] = portable
    elif case == "automation_profile_changed":
        base["full_auto"]["profile_digest"] = "0" * 64

    monkeypatch.setitem(main.CONFIG, "llm_url", _private_provider_url(
        "hidden-user", "secret-marker", "internal.example", "private-url-path-marker", "url-marker",
    ))
    monkeypatch.setitem(main.CONFIG, "llm_key", "api-key-marker")
    before = _allocation_counts()

    response = client.post(PREFLIGHT_PATH, json=base)

    detail = _assert_error(response, status, code)
    assert "token" not in detail["message"].lower()
    if case in {
        "automation_profile_not_ready", "coverage_unknown", "inventory_unconfirmed",
        "appearance_progression_conflict", "baseline_progression_conflict",
    }:
        safe_detail = json.dumps(detail, ensure_ascii=False).lower()
        assert "looks" in safe_detail
        affected_fields = {
            "automation_profile_not_ready": ("profile",),
            "coverage_unknown": ("aside", "feet"),
            "inventory_unconfirmed": ("inventory",),
            "appearance_progression_conflict": ("appearance", "garment"),
            "baseline_progression_conflict": ("baseline",),
        }
        for marker in affected_fields[case]:
            assert marker in safe_detail
        if case == "coverage_unknown":
            assert _contains_key_value(detail, "state", "aside")
            assert _contains_key_value(detail, "region", "feet")
    _assert_no_secret_echo(
        response, "secret-marker", "internal.example", "url-marker", "api-key-marker",
        "private-url-path-marker",
    )
    assert _allocation_counts() == before


def test_valid_preflight_and_create_are_read_only_until_atomic_commit_and_secret_safe(
    client, seeded, monkeypatch,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    monkeypatch.setitem(main.CONFIG, "llm_url", _private_provider_url(
        "private-user", "credential-marker", "private.example", "private-url-path-marker", "url-marker",
    ))
    monkeypatch.setitem(main.CONFIG, "llm_model", "invented-provider-model")
    monkeypatch.setitem(main.CONFIG, "llm_key", "api-key-marker")
    saved, published, base = _full_auto_fixture(seeded)
    before = _allocation_counts()

    consent_response = client.post(PREFLIGHT_PATH, json=base)

    assert consent_response.status_code == 200, consent_response.text
    consent = consent_response.json()
    assert isinstance(consent.get("scope_digest"), str) and len(consent["scope_digest"]) == 64
    assert isinstance(consent.get("scope_issued_at"), str)
    issued = datetime.fromisoformat(consent["scope_issued_at"].replace("Z", "+00:00"))
    assert issued.tzinfo is not None
    summary = consent.get("summary")
    assert isinstance(summary, dict)
    assert {
        "saved_look", "automation_profile", "room", "character_id", "workflow",
        "provider", "canvas", "framing_intent", "effective_framing",
        "variation_policy", "photo_count", "progression",
        "coverage_compiler_version", "policy_version", "limits",
        "scope_issued_at", "authorization_expires_at",
    } <= set(summary)
    assert summary["saved_look"] == {
        "look_key": saved["key"],
        "version": saved["version"],
        "content_digest": saved["content_digest"],
    }
    assert summary["automation_profile"] == {
        "revision": published["profile_revision"],
        "digest": published["profile_digest"],
        "ready": True,
    }
    assert summary["policy_version"] == "resource-full-auto-v1"
    assert summary["scope_issued_at"] == consent["scope_issued_at"]
    assert summary["authorization_expires_at"] == (
        issued + timedelta(days=7)
    ).isoformat(timespec="seconds")
    summary_json = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    assert base["scene_anchor"]["content_digest"] in summary_json
    _assert_no_secret_echo(
        consent_response,
        "private-user", "credential-marker", "private.example", "url-marker", "api-key-marker",
        "private-url-path-marker",
    )
    assert _allocation_counts() == before

    created, payload = _create_from_preflight(client, base, consent)

    assert created.status_code == 201, created.text
    result = created.json()
    assert result["plan_revision"] == 1
    assert isinstance(result.get("full_auto_run_id"), str) and result["full_auto_run_id"]
    plan = result["plan"]
    assert plan["authoring"]["look_snapshot"] == _expected_snapshot(saved)
    assert plan["authoring"]["wardrobe_progression"] is not None
    assert all(take.get("wardrobe_coverage") for take in plan["takes"])
    assert plan["wardrobe_changes"]
    assert db.one(
        "SELECT response_json, request_digest FROM guided_session_request WHERE request_id = ?",
        payload["request_id"],
    )["response_json"].encode("utf-8") == created.content
    receipt = db.one(
        "SELECT * FROM full_auto_run_receipt WHERE run_id = ?",
        result["full_auto_run_id"],
    )
    assert receipt["session_id"] == result["session_id"]
    assert receipt["scope_digest"] == consent["scope_digest"]
    assert receipt["scope_issued_at"] == consent["scope_issued_at"]
    assert receipt["authorization_expires_at"] == summary["authorization_expires_at"]
    assert receipt["plan_digest"] == resource_store.canonical_digest(plan)
    authorization = json.loads(receipt["authorization_json"])
    assert authorization["summary"] == summary
    assert authorization["scope_digest"] == consent["scope_digest"]
    assert _allocation_counts()["session"] == before["session"] + 1
    assert _allocation_counts()["session_plan"] == before["session_plan"] + 1
    assert _allocation_counts()["full_auto_run_receipt"] == before.get("full_auto_run_receipt", 0) + 1
    _assert_no_secret_echo(
        created,
        "private-user", "credential-marker", "private.example", "url-marker", "api-key-marker",
        "private-url-path-marker",
    )


def test_consent_summary_discloses_bound_translation_and_model_inputs(
    client, seeded, monkeypatch,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    saved, published, base = _full_auto_fixture(seeded)

    response = client.post(PREFLIGHT_PATH, json=base)

    assert response.status_code == 200, response.text
    summary = response.json()["summary"]
    summary_json = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    library_id = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        base["scene_anchor"]["library_key"],
    )["id"]
    room_revision = resource_store.get_revision(
        library_id=library_id,
        source_id=base["scene_anchor"]["source_id"],
        content_digest=base["scene_anchor"]["content_digest"],
    )
    model = db.one("SELECT * FROM model WHERE id = ?", seeded["model_id"])
    settings = json.loads(model["settings"])
    effective_settings = workflow_binding.effective_session_settings(model)
    model_binding = summary.get("model_binding")
    assert isinstance(model_binding, dict)
    assert model_binding["model_id"] == model["id"]
    assert model_binding["name"] == model["name"]
    assert model_binding["trigger_digest"] == resource_store.canonical_digest(model["trigger"])
    assert model_binding["base_positive_digest"] == resource_store.canonical_digest(model["base_positive"])
    assert model_binding["base_negative_digest"] == resource_store.canonical_digest(model["base_negative"])
    assert model_binding["lora_selection_digest"] == resource_store.canonical_digest({
        "name": model["lora_name"], "strength": model["lora_strength"],
    })
    assert model_binding["effective_settings_digest"] == resource_store.canonical_digest(effective_settings)
    assert summary["room"]["translation_digest"] == resource_store.canonical_digest(room_revision["translation"])
    assert len(summary["dependencies_digest"]) == 64
    assert len(summary["request_digest"]) == 64
    for field in ("width", "height", "steps", "cfg"):
        assert _contains_key_value(summary, field, settings[field])


def test_scope_digest_is_canonical_resolved_summary_digest(client, seeded, monkeypatch):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    _saved, _published, base = _full_auto_fixture(seeded)

    response = client.post(PREFLIGHT_PATH, json=base)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["scope_digest"] == resource_store.canonical_digest(result["summary"])


@pytest.mark.parametrize(
    ("age_seconds", "future_seconds"),
    [(301, 0), (0, 1)],
)
def test_creation_rejects_expired_or_future_scope_without_allocation(
    client, seeded, monkeypatch, age_seconds, future_seconds,
):
    _forbid_external_work(monkeypatch)
    _install_t2i_workflow(client, seeded)
    real_datetime = guided_sessions.datetime
    instant = real_datetime.now(timezone.utc).replace(microsecond=0)

    class FrozenDateTime(real_datetime):
        current = instant

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(guided_sessions, "datetime", FrozenDateTime)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    if age_seconds:
        FrozenDateTime.current = instant + timedelta(seconds=age_seconds)
    else:
        consent["scope_issued_at"] = (instant + timedelta(seconds=future_seconds)).isoformat(timespec="seconds")
    before = _allocation_counts()

    created, _payload = _create_from_preflight(client, base, consent)

    if age_seconds:
        _assert_refreshed_scope(created, consent)
    else:
        detail = _assert_error(created, 409, "authorization_scope_changed")
        refreshed = detail.get("current")
        assert isinstance(refreshed, dict) and isinstance(refreshed.get("summary"), dict)
        assert refreshed["scope_issued_at"] == instant.isoformat(timespec="seconds")
        assert refreshed["scope_digest"] == resource_store.canonical_digest(refreshed["summary"])
    assert _allocation_counts() == before


def test_scope_rejects_changed_model_settings(client, seeded, monkeypatch):
    _forbid_external_work(monkeypatch)
    _install_t2i_workflow(client, seeded)
    _saved, _published, base = _full_auto_fixture(seeded)
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://provider-a.example/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "invented-model-a")
    consent = _preflight(client, base)
    settings = json.loads(db.one(
        "SELECT settings FROM model WHERE id = ?", seeded["model_id"],
    )["settings"])
    settings["width"] += 8
    db.run(
        "UPDATE model SET settings = ? WHERE id = ?",
        json.dumps(settings, sort_keys=True), seeded["model_id"],
    )
    before = _allocation_counts()

    created, _payload = _create_from_preflight(client, base, consent)

    _assert_refreshed_scope(created, consent)
    assert _allocation_counts() == before


def test_scope_rejects_provider_identity_change_independently(
    client, seeded, monkeypatch,
):
    _forbid_external_work(monkeypatch)
    _install_t2i_workflow(client, seeded)
    _saved, _published, base = _full_auto_fixture(seeded)
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://provider-a.example/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "invented-model-a")
    consent = _preflight(client, base)
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://provider-b.example/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "invented-model-b")
    before = _allocation_counts()

    created, _payload = _create_from_preflight(client, base, consent)

    _assert_refreshed_scope(created, consent)
    assert _allocation_counts() == before


def test_scope_rejects_changed_room_translation_with_refreshed_summary(
    client, seeded, monkeypatch,
):
    _forbid_external_work(monkeypatch)
    _install_t2i_workflow(client, seeded)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    library_id = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        base["scene_anchor"]["library_key"],
    )["id"]
    revision = resource_store.get_revision(
        library_id=library_id,
        source_id=base["scene_anchor"]["source_id"],
        content_digest=base["scene_anchor"]["content_digest"],
    )
    translation = dict(revision["translation"])
    translation["scene_theme"] += " A changed reviewed translation."
    db.run(
        "UPDATE asset_revision SET translation = ? WHERE library_id = ? AND source_id = ? AND content_digest = ?",
        json.dumps(translation, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        library_id,
        base["scene_anchor"]["source_id"],
        base["scene_anchor"]["content_digest"],
    )
    before = _allocation_counts()

    created, _payload = _create_from_preflight(client, base, consent)

    _assert_refreshed_scope(created, consent)
    assert _allocation_counts() == before


def test_scope_rejects_workflow_graph_change_independently(client, seeded, monkeypatch):
    _forbid_external_work(monkeypatch)
    workflow_id = _install_t2i_workflow(client, seeded)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    graph = json.loads(db.one(
        "SELECT graph FROM workflow WHERE id = ?", workflow_id,
    )["graph"])
    graph["8"]["inputs"]["filename_prefix"] = "changed-after-consent"
    db.run(
        "UPDATE workflow SET graph = ? WHERE id = ?",
        json.dumps(graph, sort_keys=True), workflow_id,
    )
    before = _allocation_counts()

    created, _payload = _create_from_preflight(client, base, consent)

    _assert_refreshed_scope(created, consent)
    assert _allocation_counts() == before


def test_full_auto_keeps_exact_selected_look_when_a_newer_version_appears(
    client, seeded, monkeypatch,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    newer = client.post(
        f"/api/looks/{saved['key']}/versions",
        json={
            "expected_version": saved["version"],
            "name": "A later exact look version",
            "appearance": "A different later appearance.",
        },
    )
    assert newer.status_code == 200, newer.text
    assert newer.json()["version"] == saved["version"] + 1
    before = _allocation_counts()

    created, _payload = _create_from_preflight(client, base, consent)

    assert created.status_code == 201, created.text
    assert created.json()["plan"]["authoring"]["look_snapshot"] == _expected_snapshot(saved)
    assert created.json()["plan"]["look"] == saved["appearance"]
    assert _allocation_counts()["session"] == before["session"] + 1


def test_full_auto_invalid_objects_and_manual_override_conflict_leave_no_allocation(
    client, seeded, monkeypatch,
):
    _forbid_external_work(monkeypatch)
    _install_t2i_workflow(client, seeded)
    saved, published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    before = _allocation_counts()

    invalid = deepcopy(base)
    invalid["request_id"] = str(uuid.uuid4())
    invalid["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })
    invalid["mode"] = "manual"
    response = client.post("/api/sessions/guided", json=invalid)
    assert response.status_code == 422, response.text
    assert _allocation_counts() == before

    manual_conflict = {
        "request_id": str(uuid.uuid4()),
        "character_id": seeded["model_id"],
        "scene_anchor": base["scene_anchor"],
        "photo_count": 2,
        "mode": "manual",
        "saved_look": _saved_look_wire(saved),
        "initial_wardrobe": "A competing manually supplied wardrobe.",
    }
    response = client.post("/api/sessions/guided", json=manual_conflict)
    assert response.status_code == 422, response.text
    assert _allocation_counts() == before

    invalid = deepcopy(base)
    invalid["request_id"] = str(uuid.uuid4())
    invalid["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })
    invalid["look"] = "Competing free text."
    response = client.post("/api/sessions/guided", json=invalid)
    assert response.status_code == 422, response.text
    assert _allocation_counts() == before


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body["full_auto"].update(scope_digest="A" * 64),
        lambda body: body["full_auto"].update(scope_issued_at=1),
    ],
    ids=["noncanonical-scope-digest", "nonstring-issued-at"],
)
def test_creation_rejects_malformed_scope_fields_without_allocation(
    client, seeded, monkeypatch, mutation,
):
    _forbid_external_work(monkeypatch)
    _install_t2i_workflow(client, seeded)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    payload = deepcopy(base)
    payload["request_id"] = str(uuid.uuid4())
    payload["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })
    mutation(payload)
    before = _allocation_counts()

    response = client.post("/api/sessions/guided", json=payload)

    assert response.status_code == 422, response.text
    detail = response.json().get("detail")
    assert isinstance(detail, dict) and detail.get("code")
    assert _allocation_counts() == before


@pytest.mark.parametrize(
    ("field", "value"),
    [("saved_look", None), ("full_auto", None)],
    ids=["explicit-null-saved-look", "explicit-null-full-auto"],
)
def test_explicit_null_new_objects_are_not_treated_as_omitted(
    client, seeded, monkeypatch, field, value,
):
    _forbid_external_work(monkeypatch)
    payload = {
        "request_id": str(uuid.uuid4()),
        "character_id": seeded["model_id"],
        "scene_anchor": _room_anchor(),
        "photo_count": 2,
    }
    payload[field] = value
    before = _allocation_counts()

    response = client.post("/api/sessions/guided", json=payload)

    assert response.status_code == 422, response.text
    detail = response.json().get("detail")
    assert isinstance(detail, dict) and detail.get("code")
    assert _allocation_counts() == before


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body["saved_look"].update(version=True),
        lambda body: body["full_auto"].update(extra="forbidden"),
        lambda body: body["full_auto"].update(authorization_version=True),
        lambda body: body["full_auto"].update(generate_without_pauses=1),
        lambda body: body["full_auto"].update(profile_revision=2**63),
        lambda body: body["full_auto"].update(profile_digest="A" * 64),
        lambda body: body["full_auto"].update(scope_digest="A" * 64),
        lambda body: body["full_auto"].update(scope_issued_at=1),
        lambda body: body.update(character_id=2**63),
        lambda body: body.update(workflow_id=2**63),
    ],
    ids=[
        "saved-look-bool-version", "unknown-full-auto-field", "bool-auth-version",
        "integer-is-not-boolean", "sqlite-profile-overflow", "uppercase-profile-digest",
        "uppercase-scope-digest", "numeric-issued-at", "sqlite-character-overflow",
        "sqlite-workflow-overflow",
    ],
)
def test_creation_rejects_strict_full_auto_shapes_before_writes(
    client, seeded, monkeypatch, mutation,
):
    _forbid_external_work(monkeypatch)
    _install_t2i_workflow(client, seeded)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    payload = deepcopy(base)
    payload["request_id"] = str(uuid.uuid4())
    payload["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })
    mutation(payload)
    before = _allocation_counts()

    response = client.post("/api/sessions/guided", json=payload)

    assert response.status_code == 422, response.text
    detail = response.json().get("detail")
    assert isinstance(detail, dict) and detail.get("code")
    assert _allocation_counts() == before


def test_atomic_final_receipt_failure_rolls_back_every_creation_row(
    client, seeded, monkeypatch,
):
    _forbid_external_work(monkeypatch)
    _install_t2i_workflow(client, seeded)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    payload = deepcopy(base)
    payload["request_id"] = str(uuid.uuid4())
    payload["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })
    before = _allocation_counts()
    original_run = db.run
    receipt_write_reached = False

    def fail_receipt(sql, *args):
        nonlocal receipt_write_reached
        if "INSERT INTO guided_session_request" in sql:
            receipt_write_reached = True
            raise RuntimeError("synthetic final receipt write failure")
        return original_run(sql, *args)

    monkeypatch.setattr(db, "run", fail_receipt)
    try:
        response = client.post("/api/sessions/guided", json=payload)
    except RuntimeError as exc:
        assert "synthetic final receipt" in str(exc)
    else:
        assert response.status_code == 500, response.text
    finally:
        monkeypatch.setattr(db, "run", original_run)
    assert receipt_write_reached
    assert _allocation_counts() == before


def test_concurrent_same_request_id_replays_one_atomic_full_auto_response(
    client, seeded, monkeypatch,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    payload = deepcopy(base)
    payload["request_id"] = str(uuid.uuid4())
    payload["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })
    barrier = threading.Barrier(2)

    def post_once():
        barrier.wait(timeout=10)
        return client.post("/api/sessions/guided", json=payload)

    before = _allocation_counts()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result(timeout=20) for future in [
            pool.submit(post_once), pool.submit(post_once),
        ]]

    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].content == responses[1].content
    response_json = responses[0].json()
    assert response_json["full_auto_run_id"]
    assert _allocation_counts()["session"] == before["session"] + 1
    assert _allocation_counts()["session_plan"] == before["session_plan"] + 1
    changed_scope = deepcopy(payload)
    changed_scope["full_auto"]["scope_digest"] = "0" * 64
    conflict = client.post("/api/sessions/guided", json=changed_scope)
    _assert_error(conflict, 409, "idempotency_conflict")
    assert _allocation_counts()["session"] == before["session"] + 1


def test_identical_receipt_replays_after_scope_expiry_and_dependency_drift(
    client, seeded, monkeypatch,
):
    _install_t2i_workflow(client, seeded)
    _forbid_external_work(monkeypatch)
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://provider-a.example/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "invented-model-a")
    real_datetime = guided_sessions.datetime
    instant = real_datetime.now(timezone.utc).replace(microsecond=0)

    class FrozenDateTime(real_datetime):
        current = instant

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(guided_sessions, "datetime", FrozenDateTime)
    _saved, _published, base = _full_auto_fixture(seeded)
    consent = _preflight(client, base)
    created, payload = _create_from_preflight(client, base, consent)
    assert created.status_code == 201, created.text
    stored_bytes = created.content

    settings = json.loads(db.one(
        "SELECT settings FROM model WHERE id = ?", seeded["model_id"],
    )["settings"])
    settings["width"] += 8
    db.run(
        "UPDATE model SET settings = ? WHERE id = ?",
        json.dumps(settings, sort_keys=True), seeded["model_id"],
    )
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://provider-b.example/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "invented-model-b")
    library_id = db.one(
        "SELECT id FROM resource_library WHERE library_key = ?",
        base["scene_anchor"]["library_key"],
    )["id"]
    db.run(
        "DELETE FROM asset_revision WHERE library_id = ? AND source_id = ? AND content_digest = ?",
        library_id,
        base["scene_anchor"]["source_id"],
        base["scene_anchor"]["content_digest"],
    )
    assert db.one(
        "SELECT 1 FROM asset_revision WHERE library_id = ? AND source_id = ? AND content_digest = ?",
        library_id,
        base["scene_anchor"]["source_id"],
        base["scene_anchor"]["content_digest"],
    ) is None
    FrozenDateTime.current = instant + timedelta(minutes=6)
    before_replay = _allocation_counts()

    replay = client.post("/api/sessions/guided", json=payload)

    assert replay.status_code == 200, replay.text
    assert replay.content == stored_bytes
    assert _allocation_counts() == before_replay
