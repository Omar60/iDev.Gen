from __future__ import annotations

import copy

import pytest

import db
import main
from backend import photo_staging, saved_looks
from test_photo_extraction import (
    _automation_candidate as _photo_candidate,
    _config as _photo_config,
    _proposal as _photo_proposal,
    _upload as _upload_photo,
)


@pytest.fixture(autouse=True)
def _cleanup_group1_photo_stages(client):
    baseline = {
        row["photo_id"] for row in db.q("SELECT photo_id FROM look_photo_stage")
    }
    yield
    created = [
        row for row in db.q("SELECT photo_id, staged_path FROM look_photo_stage")
        if row["photo_id"] not in baseline
    ]
    for row in created:
        photo_id = row["photo_id"]
        photo_staging._safe_unlink(photo_id, row["staged_path"])
        db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", photo_id)
        db.run("DELETE FROM look_photo_stage WHERE photo_id = ?", photo_id)


def _profile(appearance, garments):
    source = saved_looks.canonicalize_prospective_automation_source(
        appearance, garments,
    )
    segments = []
    if appearance:
        segments.append({
            "segment_id": "segment-1",
            "start": 0,
            "end": len(appearance),
            "text": appearance,
            "owner": None,
        })
    return {
        "schema_version": 1,
        "draft_content_digest": source["draft_content_digest"],
        "appearance_segments": segments,
        "garment_coverage": [],
        "baseline_coverage": None,
        "inventory_complete": None,
    }


def test_mapping_reuses_immutable_historical_garment_snapshot(client):
    appearance = "A saved blue coat."
    first = saved_looks.create({
        "name": "First historical look",
        "appearance": appearance,
        "garments": [{"wording": "a blue coat", "aside": ""}],
    })
    garment = first["outfit"]["garments"][0]
    draft_garments = [{
        "key": garment["key"],
        "wording": garment["wording"],
        "aside": garment["aside"],
    }]
    profile = _profile(appearance, draft_garments)
    second = saved_looks.create_version(first["key"], 1, {
        "name": "Second historical look",
        "appearance": appearance,
        "garments": copy.deepcopy(draft_garments),
    })

    db.run("DELETE FROM garment WHERE key = ?", garment["key"])

    mapped = saved_looks.map_prospective_automation_profile(
        profile, appearance, draft_garments, second,
    )

    assert mapped["look"]["content_digest"] == second["content_digest"]
    assert mapped["garment_coverage"] == {}
    assert mapped["draft_content_digest"] == profile["draft_content_digest"]


def test_manual_proposal_rejects_private_endpoint_in_model_metadata(client, monkeypatch):
    private_endpoint = "https://private-assistant.invalid/internal/v1"
    config = {
        **main.CONFIG,
        "resource_planning_enabled": True,
        "llm_url": "https://assistant.example.invalid/v1",
        "llm_model": private_endpoint,
        "llm_key": "invented-test-key",
    }
    monkeypatch.setattr(main, "CONFIG", config)
    appearance = "A portrait with short hair."
    garments = []
    candidate = _profile(appearance, garments)

    async def fake_provider(_config, _prompt, request_evidence=None):
        if request_evidence is not None:
            request_evidence.update({
                "messages": [
                    {"role": "system", "content": "Return one structured suggestion."},
                    {"role": "user", "content": "Exact source supplied by the caller."},
                ],
                "model": private_endpoint,
                "parameters": {},
            })
        return candidate

    monkeypatch.setattr(main.enhance, "run_structured", fake_provider)
    response = client.post("/api/looks/automation-proposals", json={
        "appearance": appearance,
        "garments": garments,
    })

    assert private_endpoint not in response.text
    assert response.status_code == 502
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_proposal")["n"] == 0


def test_manual_provider_runs_outside_transaction_and_gate_is_rechecked(client, monkeypatch):
    config = {
        **main.CONFIG,
        "resource_planning_enabled": True,
        "llm_url": "https://assistant.example.invalid/v1",
        "llm_model": "invented-text-model",
        "llm_key": "invented-test-key",
    }
    monkeypatch.setattr(main, "CONFIG", config)
    appearance = "A portrait with short hair."
    garments = []
    candidate = _profile(appearance, garments)

    async def fake_provider(_config, _prompt, request_evidence=None):
        assert not db.conn().in_transaction
        if request_evidence is not None:
            request_evidence.update({
                "messages": [
                    {"role": "system", "content": "Return one structured suggestion."},
                    {"role": "user", "content": "Exact source supplied by the caller."},
                ],
                "model": config["llm_model"],
                "parameters": {},
            })
        config["resource_planning_enabled"] = False
        return candidate

    monkeypatch.setattr(main.enhance, "run_structured", fake_provider)
    response = client.post("/api/looks/automation-proposals", json={
        "appearance": appearance,
        "garments": garments,
    })

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "resource_planning_disabled"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_proposal")["n"] == 0


def test_photo_provider_runs_outside_transaction_and_gate_is_rechecked(client, monkeypatch):
    config = _photo_config(monkeypatch)
    ordinary = _photo_proposal()
    output = {**ordinary, "automation": _photo_candidate(ordinary)}
    photo_id = _upload_photo(client).json()["photo_id"]

    async def fake_provider(_config, _prompt, image_uri, *, request_evidence=None):
        assert not db.conn().in_transaction
        if request_evidence is not None:
            request_evidence.update({
                "messages": [
                    {"role": "system", "content": "Return an ordinary proposal."},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Visible look description."},
                        {"type": "image_url", "image_url": {"url": image_uri}},
                    ]},
                ],
                "model": config["llm_vision_model"],
                "parameters": {"response_format": {"type": "json_object"}},
            })
        config["resource_planning_enabled"] = False
        return output

    monkeypatch.setattr(main.enhance, "run_structured", fake_provider)
    response = client.post(
        f"/api/looks/photo-stages/{photo_id}/extract",
        json={"include_automation": True},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "resource_planning_disabled"
    assert db.one(
        "SELECT state FROM photo_look_proposal WHERE photo_id = ?", photo_id,
    )["state"] == "failed"
    assert db.one("SELECT COUNT(*) AS n FROM photo_look_automation_proposal")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
