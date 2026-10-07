from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import base64
import json

import httpx
from PIL import Image
import pytest

import db
import main
from backend import photo_extraction, photo_staging


def _image_bytes(color=(17, 83, 149)) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (5, 4), color).save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture(autouse=True)
def _clear_photo_rows(client):
    for row in db.q("SELECT photo_id, staged_path FROM look_photo_stage"):
        photo_staging._safe_unlink(row["photo_id"], row["staged_path"])
    db.run("DELETE FROM photo_look_proposal")
    db.run("DELETE FROM look_photo_stage")
    db.run("DELETE FROM saved_look_photo_evidence")
    yield
    for row in db.q("SELECT photo_id, staged_path FROM look_photo_stage"):
        photo_staging._safe_unlink(row["photo_id"], row["staged_path"])
    db.run("DELETE FROM photo_look_proposal")
    db.run("DELETE FROM look_photo_stage")
    db.run("DELETE FROM saved_look_photo_evidence")


def _upload(client, content: bytes | None = None) -> str:
    response = client.post(
        "/api/looks/photo-stages",
        files={"file": ("invented-photo.png", content or _image_bytes(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["photo_id"]


def _config(monkeypatch) -> dict:
    config = {
        **main.CONFIG,
        "resource_planning_enabled": True,
        "llm_url": "https://vision.example.invalid/v1",
        "llm_model": "invented-text-model",
        "llm_vision_model": "invented-vision-model",
        "llm_key": "acceptance-secret-key-73",
    }
    monkeypatch.setattr(main, "CONFIG", config)
    return config


def _output() -> dict:
    return {
        "appearance": "short dark hair",
        "garments": ["a white shirt", "a blue cardigan"],
        "unresolved": [{"id": "u1", "detail": "A small detail is obscured."}],
    }


def _install_fake_provider(monkeypatch, config: dict, result: dict):
    async def fake_provider(_config, prompt, image, *, request_evidence):
        request_evidence.update({
            "messages": [
                {"role": "system", "content": "Use only visible evidence."},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt.instruction},
                    {"type": "image_url", "image_url": {"url": image}},
                ]},
            ],
            "model": config["llm_vision_model"],
            "parameters": {
                "temperature": 0.8,
                "stream": False,
                "response_format": {"type": "json_object"},
            },
        })
        return deepcopy(result)

    monkeypatch.setattr(main.enhance, "run_structured", fake_provider)
    return fake_provider


def _review(proposal_id: str) -> dict:
    return {
        "proposal_id": proposal_id,
        "name": "Reviewed blue layers",
        "appearance": "short dark hair with a faint beauty mark",
        "garments": [
            {"wording": "a blue cardigan", "aside": "unbuttoned"},
            {"wording": "a white shirt", "aside": ""},
        ],
        "unresolved_decisions": [
            {"id": "u1", "action": "correct", "correction": "faint beauty mark"},
        ],
        "review_confirmed": True,
        "removal_order_confirmed": True,
    }


@pytest.mark.parametrize("minimax", [False, True])
def test_http_wire_projection_is_exact_redacted_and_survives_stage_purge(client, monkeypatch, minimax):
    config = _config(monkeypatch)
    if minimax:
        config.update(llm_url="https://api.minimax.io/v1", llm_vision_model="MiniMax-M3.1-Flash-Preview")
    actual_async_client = httpx.AsyncClient
    wire_requests: list[dict] = []
    output = _output()

    async def provider(request: httpx.Request) -> httpx.Response:
        assert db._tx_depth == 0
        assert db.conn().in_transaction is False
        body = json.loads(request.content)
        wire_requests.append({
            "url": str(request.url),
            "headers": dict(request.headers),
            "body": body,
        })
        if not minimax and "reasoning_effort" in body:
            return httpx.Response(
                400,
                text="Unsupported reasoning_effort",
                request=request,
            )
        if minimax:
            assert body["reasoning_split"] is True
            assert body["reasoning_effort"] == "low"
        return httpx.Response(
            200,
            json={"choices": [{"message": {
                "content": json.dumps(output), "reasoning_content": "Separate provider reasoning.",
            }}]},
            request=request,
        )

    transport = httpx.MockTransport(provider)

    def with_fake_transport(*args, **kwargs):
        return actual_async_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", with_fake_transport)
    image = _image_bytes()
    photo_id = _upload(client, image)

    response = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})

    assert response.status_code == 200, response.text
    proposal = response.json()
    assert set(proposal) == {"proposal_id", "appearance", "garments", "unresolved"}
    assert proposal["appearance"] == output["appearance"]
    assert len(wire_requests) == (1 if minimax else 2)
    assert "reasoning_effort" in wire_requests[0]["body"]
    successful_request = wire_requests[-1]
    body = successful_request["body"]
    if not minimax:
        assert "reasoning_effort" not in body
    assert successful_request["url"] == f"{config['llm_url']}/chat/completions"
    assert body["model"] == config["llm_vision_model"]
    assert successful_request["headers"]["authorization"] == f"Bearer {config['llm_key']}"
    assert body["messages"][1]["content"][1]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    )
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").content == image

    transient = db.one(
        "SELECT request_projection_json, output_json FROM photo_look_proposal WHERE photo_id = ? AND state = 'ready'",
        photo_id,
    )
    projection = json.loads(transient["request_projection_json"])
    expected_messages = deepcopy(body["messages"])
    image_part = expected_messages[1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    image_part["image_url"] = {
        "sha256": sha256(image).hexdigest(),
        "media_type": "image/png",
        "byte_count": len(image),
        "width": 5,
        "height": 4,
    }
    assert projection == {
        "messages": expected_messages,
        "model": body["model"],
        "parameters": {
            key: body[key]
            for key in ("temperature", "stream", "response_format", "reasoning_effort", "reasoning_split")
            if key in body
        },
    }
    assert json.loads(transient["output_json"]) == output

    review = _review(proposal["proposal_id"])
    saved_response = client.post(
        f"/api/looks/photo-stages/{photo_id}/save-extracted",
        json=review,
    )
    assert saved_response.status_code == 200, saved_response.text
    saved = saved_response.json()
    assert saved["appearance"] == review["appearance"]
    assert [item["wording"] for item in saved["outfit"]["garments"]] == [
        "a blue cardigan", "a white shirt",
    ]
    assert not photo_staging._stage_path(photo_id).exists()

    evidence_response = client.get(
        f"/api/looks/{saved['key']}/versions/{saved['version']}/photo-evidence"
    )
    assert evidence_response.status_code == 200, evidence_response.text
    evidence = evidence_response.json()
    assert evidence["request"] == projection
    assert evidence["output"] == output
    assert evidence["corrections"]["appearance"] == review["appearance"]
    assert evidence["corrections"]["garments"] == review["garments"]
    assert evidence["corrections"]["unresolved_decisions"] == review["unresolved_decisions"]

    export = client.get(f"/api/looks/{saved['key']}/versions/{saved['version']}/export")
    assert export.status_code == 200, export.text
    assert export.json()["provenance"] == {
        "source": "photo",
        "image_sha256": sha256(image).hexdigest(),
    }
    assert "request" not in export.text
    assert "corrections" not in export.text

    expired = photo_staging._iso(photo_staging._now() - timedelta(hours=25))
    db.run("UPDATE look_photo_stage SET expires_at = ? WHERE photo_id = ?", expired, photo_id)
    photo_staging.startup_recovery(now=photo_staging._now())
    assert db.one("SELECT photo_id FROM look_photo_stage WHERE photo_id = ?", photo_id) is None
    assert client.get(
        f"/api/looks/{saved['key']}/versions/{saved['version']}/photo-evidence"
    ).json() == evidence

    persisted = "\n".join(db.conn().iterdump())
    persisted_bytes = persisted.encode("utf-8")
    for private_value in (
        "data:image/png;base64,",
        base64.b64encode(image).decode("ascii"),
        image.hex(),
        config["llm_url"],
        "/v1/chat/completions",
        config["llm_key"],
        "Authorization",
        f"Bearer {config['llm_key']}",
    ):
        assert private_value not in persisted
    assert image not in persisted_bytes


@pytest.mark.parametrize("forbidden_key", [
    "person_identity", "background", "pose", "camera", "unseen_garments", "removal_order",
])
def test_provider_output_rejects_non_look_fields_without_publishing(client, monkeypatch, forbidden_key):
    config = _config(monkeypatch)
    result = {**_output(), forbidden_key: "must never become accepted look content"}
    _install_fake_provider(monkeypatch, config, result)
    photo_id = _upload(client)

    response = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})

    assert response.status_code == 502, response.text
    assert response.json()["detail"]["code"] == "vision_request_failed"
    assert "must never become accepted" not in response.text
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == 0
    assert db.one(
        "SELECT state FROM photo_look_proposal WHERE photo_id = ?", photo_id,
    )["state"] == "failed"
    assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").status_code == 200


def test_unresolved_decisions_and_user_confirmed_order_are_required(client, monkeypatch):
    config = _config(monkeypatch)
    _install_fake_provider(monkeypatch, config, _output())
    photo_id = _upload(client)
    extracted = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert extracted.status_code == 200, extracted.text
    proposal_id = extracted.json()["proposal_id"]
    base = _review(proposal_id)
    invalid_reviews = [
        {**base, "removal_order_confirmed": False},
        {**base, "unresolved_decisions": []},
        {**base, "unresolved_decisions": [base["unresolved_decisions"][0]] * 2},
        {**base, "unresolved_decisions": [{"id": "u9", "action": "omit"}]},
        {**base, "unresolved_decisions": [{"id": "u1", "action": "correct", "correction": "not in reviewed text"}]},
    ]

    for invalid in invalid_reviews:
        response = client.post(
            f"/api/looks/photo-stages/{photo_id}/save-extracted",
            json=invalid,
        )
        assert response.status_code == 422, response.text
        assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
        assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == 0
        assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "staged"
        assert photo_staging._stage_path(photo_id).is_file()

    accepted = client.post(
        f"/api/looks/photo-stages/{photo_id}/save-extracted",
        json=base,
    )
    assert accepted.status_code == 200, accepted.text
    saved = accepted.json()
    assert [item["wording"] for item in saved["outfit"]["garments"]] == [
        "a blue cardigan", "a white shirt",
    ]
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "saved"


@pytest.mark.parametrize("private_value_kind", ["data_uri", "base64", "endpoint", "key", "authorization"])
def test_provider_and_user_text_cannot_persist_photo_or_secret_markers(
    client, monkeypatch, private_value_kind,
):
    config = _config(monkeypatch)
    image = _image_bytes()
    private_values = {
        "data_uri": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        "base64": base64.b64encode(image).decode("ascii"),
        "endpoint": config["llm_url"] + "/chat/completions",
        "key": config["llm_key"],
        "authorization": "Authorization: Bearer " + config["llm_key"],
    }
    private_value = private_values[private_value_kind]
    photo_id = _upload(client, image)
    _install_fake_provider(
        monkeypatch,
        config,
        {"appearance": private_value, "garments": [], "unresolved": []},
    )

    extracted = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert extracted.status_code == 502
    assert extracted.json()["detail"]["code"] == "vision_request_failed"
    assert private_value not in extracted.text
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == 0

    _install_fake_provider(monkeypatch, config, _output())
    extracted = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert extracted.status_code == 200, extracted.text
    review = _review(extracted.json()["proposal_id"])
    review["appearance"] = "short dark hair " + private_value
    review["unresolved_decisions"] = [
        {"id": "u1", "action": "correct", "correction": private_value},
    ]
    rejected = client.post(
        f"/api/looks/photo-stages/{photo_id}/save-extracted",
        json=review,
    )
    assert rejected.status_code == 422
    assert private_value not in rejected.text
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == 0
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "staged"
