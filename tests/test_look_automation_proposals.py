from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest
import httpx

import db
import main
from backend import look_automation, saved_looks


@pytest.fixture(autouse=True)
def _clear_manual_proposals(client):
    db.run("DELETE FROM saved_look_automation_proposal")
    yield
    db.run("DELETE FROM saved_look_automation_proposal")


def _source():
    return {
        "appearance": "soft brown hair and earring",
        "garments": [
            {"key": None, "wording": "a white shirt", "aside": "the shirt folded over one arm"},
            {"key": "reused-garment", "wording": "blue jeans", "aside": ""},
        ],
    }


def _profile(source):
    canonical = saved_looks.canonicalize_prospective_automation_source(
        source["appearance"], source["garments"],
    )
    appearance = source["appearance"]
    regions = {name: "unknown" for name in ("chest", "hips", "legs", "feet")}
    coverage = []
    for index, garment in enumerate(source["garments"]):
        states = {"worn": {"regions": regions}}
        if garment["aside"]:
            states["aside"] = {"regions": regions}
        coverage.append({"garment_index": index, "states": states})
    return {
        "schema_version": 1,
        "draft_content_digest": canonical["draft_content_digest"],
        "appearance_segments": [
            {
                "segment_id": "segment-1",
                "start": 0,
                "end": len(appearance),
                "text": appearance,
                "owner": None,
            }
        ],
        "garment_coverage": coverage,
        "baseline_coverage": None,
        "inventory_complete": None,
    }


def _config(monkeypatch, **updates):
    config = {
        **main.CONFIG,
        "resource_planning_enabled": True,
        "llm_model": "invented-text-model",
        "llm_url": "https://assistant.example.invalid/v1",
        "llm_key": "invented-test-key",
        **updates,
    }
    monkeypatch.setattr(main, "CONFIG", config)
    return config


def _install_provider(monkeypatch, output, *, error=None, evidence=None):
    calls = []

    async def fake_run(config, prompt, request_evidence=None):
        calls.append((config, prompt))
        if error is not None:
            raise error
        if request_evidence is not None:
            request_evidence.update(evidence or {
                "messages": [
                    {"role": "system", "content": "Return one structured suggestion."},
                    {"role": "user", "content": "Exact source supplied by the caller."},
                ],
                "model": config["llm_model"],
                "parameters": {"response_format": {"type": "json_object"}},
            })
        return output

    monkeypatch.setattr(main.enhance, "run_structured", fake_run)
    return calls


def _post(client, source):
    return client.post("/api/looks/automation-proposals", json=source)


def test_manual_proposal_is_exact_transient_and_leaves_unknowns_unknown(client, monkeypatch):
    _config(monkeypatch)
    source = _source()
    calls = _install_provider(monkeypatch, _profile(source))

    response = _post(client, source)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["profile"]["baseline_coverage"] is None
    assert result["profile"]["inventory_complete"] is None
    assert [entry["garment_index"] for entry in result["profile"]["garment_coverage"]] == [0, 1]
    assert result["profile"]["garment_coverage"][0]["states"]["aside"]["regions"]["chest"] == "unknown"
    assert result["draft_content_digest"] == result["profile"]["draft_content_digest"]
    assert result["expires_at"]
    assert result["request_projection"]["model"] == "invented-text-model"
    assert set(result["request_projection"]) == {"model", "parameters", "messages_digest"}
    assert len(calls) == 1

    stored = look_automation.get_manual_proposal(result["proposal_id"], result["draft_content_digest"])
    created = datetime.fromisoformat(stored["created_at"])
    expires = datetime.fromisoformat(stored["expires_at"])
    assert expires - created == timedelta(hours=24)
    evidence = db.one(
        "SELECT request_projection_json FROM saved_look_automation_proposal WHERE proposal_id = ?",
        result["proposal_id"],
    )["request_projection_json"]
    assert "invented-test-key" not in evidence
    assert "https://assistant.example.invalid" not in evidence
    assert "Exact source supplied" not in evidence
    assert json.loads(evidence)["messages_digest"] == result["request_projection"]["messages_digest"]
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_profile")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM session")["n"] == 0


@pytest.mark.parametrize("appearance", ["soft brown hair and earring", "brown hair \U0001f33f"])
def test_manual_proposal_accepts_real_transport_evidence_with_embedded_draft_digest(client, monkeypatch, appearance):
    _config(monkeypatch, llm_url="https://api.minimax.io/v1", llm_model="MiniMax-M3.1-Flash-Preview")
    source = _source()
    source["appearance"] = appearance
    profile = _profile(source)
    requests = []
    original_client = main.enhance.httpx.AsyncClient

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(profile)}, "finish_reason": "stop"}],
        })

    def simulated_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(respond)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", simulated_client)
    response = _post(client, source)

    assert response.status_code == 200, response.text
    assert len(requests) == 1
    request = requests[0]
    assert profile["draft_content_digest"] in request["messages"][1]["content"]
    supplied_source = json.loads(request["messages"][1]["content"].split("Source:\n", 1)[1])
    assert supplied_source["appearance_length"] == len(appearance)
    assert supplied_source["draft_content_digest"] == profile["draft_content_digest"]
    assert request["reasoning_effort"] == "low"
    assert request["reasoning_split"] is True
    assert request["response_format"] == {"type": "json_object"}
    result = response.json()
    assert result["profile"] == profile
    assert result["request_projection"]["messages_digest"] == look_automation.resource_store.canonical_digest(
        request["messages"],
    )
    stored = look_automation.get_manual_proposal(result["proposal_id"], result["draft_content_digest"])
    assert stored["profile"] == profile
    assert stored["request_projection"] == result["request_projection"]
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_profile")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM session")["n"] == 0


@pytest.mark.parametrize("mutate", [
    lambda profile: profile.update(accepted=True),
    lambda profile: profile.update(draft_content_digest="0" * 64),
    lambda profile: profile["appearance_segments"][0].update(end=1),
    lambda profile: profile["appearance_segments"][0].update(text="altered source"),
    lambda profile: profile.update(inventory_complete=True),
    lambda profile: profile.update(baseline_coverage={"regions": {
        "chest": "none", "hips": "none", "legs": "none", "feet": "none",
    }}),
])
def test_malformed_or_self_accepting_manual_candidate_is_rejected(client, monkeypatch, mutate):
    _config(monkeypatch)
    source = _source()
    invalid = _profile(source)
    mutate(invalid)
    _install_provider(monkeypatch, invalid)

    response = _post(client, source)

    assert response.status_code == 502, response.text
    assert response.json()["detail"]["code"] == "automation_proposal_failed"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_proposal")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_profile")["n"] == 0


def test_manual_provider_failure_is_safe_and_allocates_nothing(client, monkeypatch):
    _config(monkeypatch)
    _install_provider(monkeypatch, None, error=RuntimeError("private provider detail"))

    response = _post(client, _source())

    assert response.status_code == 502
    assert response.json()["detail"]["message"] == "The assistant could not produce a usable automation proposal."
    assert "private provider detail" not in response.text
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_proposal")["n"] == 0


def test_disabled_feature_gate_refuses_before_provider_dispatch(client, monkeypatch):
    _config(monkeypatch, resource_planning_enabled=False)
    calls = _install_provider(monkeypatch, None)

    response = _post(client, _source())

    assert response.status_code == 503
    assert calls == []
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_proposal")["n"] == 0


@pytest.mark.parametrize("unsafe", [
    "Authorization: Bearer invented-test-key",
    "https://private.example.invalid/review",
    "0" * 64,
    "QUJD" * 24,
    "{digest}A",
])
def test_manual_proposal_rejects_unsafe_request_evidence(client, monkeypatch, unsafe):
    _config(monkeypatch)
    source = _source()
    profile = _profile(source)
    content = json.dumps(profile["draft_content_digest"]) + " " + unsafe.format(
        digest=profile["draft_content_digest"],
    )
    _install_provider(
        monkeypatch,
        profile,
        evidence={
            "messages": [
                {"role": "system", "content": "Return JSON."},
                {"role": "user", "content": content},
            ],
            "model": "invented-text-model",
            "parameters": {},
        },
    )

    response = _post(client, source)

    assert response.status_code == 502
    assert "invented-test-key" not in response.text
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_proposal")["n"] == 0


def test_expired_manual_proposal_is_deleted_and_cannot_be_reused(client, monkeypatch):
    _config(monkeypatch)
    source = _source()
    _install_provider(monkeypatch, _profile(source))
    result = _post(client, source).json()
    created = datetime.now(timezone.utc) - timedelta(hours=25)
    expires = created + timedelta(hours=24)
    db.run(
        "UPDATE saved_look_automation_proposal SET created_at = ?, expires_at = ? WHERE proposal_id = ?",
        created.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds"),
        result["proposal_id"],
    )

    with pytest.raises(saved_looks.SavedLookError) as expired:
        look_automation.get_manual_proposal(result["proposal_id"], result["draft_content_digest"])

    assert expired.value.status_code == 410
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_proposal")["n"] == 0


@pytest.mark.parametrize("column,value", [
    ("source_json", '{"appearance":"changed","garments":[],"draft_content_digest":"bad"}'),
    ("profile_json", '{"unexpected":true}'),
    ("request_projection_json", '{"model":"m","parameters":{},"messages_digest":"bad"}'),
])
def test_malformed_persisted_proposal_is_reported(client, monkeypatch, column, value):
    _config(monkeypatch)
    source = _source()
    _install_provider(monkeypatch, _profile(source))
    result = _post(client, source).json()
    assert column in {"source_json", "profile_json", "request_projection_json"}
    db.run(
        f"UPDATE saved_look_automation_proposal SET {column} = ? WHERE proposal_id = ?",
        value, result["proposal_id"],
    )

    with pytest.raises(saved_looks.SavedLookError) as invalid:
        look_automation.get_manual_proposal(result["proposal_id"], result["draft_content_digest"])

    assert invalid.value.status_code == 500
    assert invalid.value.code == "automation_proposal_invalid"
