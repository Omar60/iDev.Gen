from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import base64
import json

from PIL import Image
import pytest

import db
import main
from backend import photo_staging
from backend import photo_extraction


def _image_bytes(color=(23, 81, 144)) -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 3), color).save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture(autouse=True)
def _clear_photo_data(client):
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


def _upload(client, data=None):
    return client.post(
        "/api/looks/photo-stages",
        files={"file": ("portrait.png", data or _image_bytes(), "image/png")},
    )


def _config(monkeypatch):
    config = {
        **main.CONFIG,
        "resource_planning_enabled": True,
        "llm_url": "https://vision.example.invalid/v1",
        "llm_model": "test-text-model",
        "llm_vision_model": "test-vision-model",
        "llm_key": "invented-key-for-test-only",
    }
    monkeypatch.setattr(main, "CONFIG", config)
    return config


def _proposal():
    return {
        "appearance": "short dark hair",
        "garments": ["a white shirt", "a blue cardigan"],
        "unresolved": [{"id": "u1", "detail": "A small detail is obscured."}],
    }


class _Response:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


def test_photo_extraction_redacts_actual_fallback_request_and_saves_reviewed_evidence(client, monkeypatch):
    config = _config(monkeypatch)
    seen = {"bodies": [], "headers": []}
    output = _proposal()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, json=None, headers=None):
            seen["bodies"].append(json)
            seen["headers"].append(headers)
            if "reasoning_effort" in json:
                return _Response(400, text="Unrecognized request argument: reasoning_effort")
            return _Response(200, {
                "choices": [{"message": {"content": json_module_dumps(output)}}],
            })

    monkeypatch.setattr(main.enhance.httpx, "AsyncClient", FakeClient)
    image = _image_bytes()
    upload = _upload(client, image)
    assert upload.status_code == 201
    photo_id = upload.json()["photo_id"]

    extracted = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert extracted.status_code == 200, extracted.text
    proposal = extracted.json()
    assert set(proposal) == {"proposal_id", "appearance", "garments", "unresolved"}
    assert proposal["appearance"] == output["appearance"]
    assert len(proposal["proposal_id"]) == 32
    assert ["reasoning_effort" in body for body in seen["bodies"]] == [True, False]
    assert seen["bodies"][0]["messages"] == seen["bodies"][1]["messages"]
    assert seen["headers"] == [{"Authorization": f"Bearer {config['llm_key']}"}] * 2
    transmitted = seen["bodies"][1]["messages"][1]["content"][1]["image_url"]["url"]
    assert transmitted == "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").content == image

    proposal_row = db.one("SELECT * FROM photo_look_proposal WHERE photo_id = ?", photo_id)
    request_projection = json.loads(proposal_row["request_projection_json"])
    image_part = request_projection["messages"][1]["content"][1]
    assert image_part == {
        "type": "image_url",
        "image_url": {
            "sha256": sha256(image).hexdigest(),
            "media_type": "image/png",
            "byte_count": len(image),
            "width": 4,
            "height": 3,
        },
    }
    assert request_projection["model"] == "test-vision-model"
    assert request_projection["parameters"] == {
        "temperature": 0.8,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    serialized_request = json.dumps(request_projection)
    assert "data:" not in serialized_request
    assert base64.b64encode(image).decode("ascii") not in serialized_request
    assert config["llm_url"] not in serialized_request
    assert config["llm_key"] not in serialized_request

    manual = client.post(f"/api/looks/photo-stages/{photo_id}/save", json={"name": "Manual"})
    assert manual.status_code == 409
    assert manual.json()["detail"]["code"] == "photo_extraction_review_required"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0

    review = {
        "proposal_id": proposal["proposal_id"],
        "name": "Blue layers",
        "appearance": "short dark hair",
        "garments": [
            {"wording": "a blue cardigan", "aside": ""},
                {"wording": "a white shirt"},
        ],
        "unresolved_decisions": [{"id": "u1", "action": "omit"}],
        "review_confirmed": True,
        "removal_order_confirmed": True,
    }
    saved = client.post(f"/api/looks/photo-stages/{photo_id}/save-extracted", json=review)
    assert saved.status_code == 200, saved.text
    look = saved.json()
    assert [item["wording"] for item in look["outfit"]["garments"]] == [
        "a blue cardigan", "a white shirt",
    ]
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "saved"
    assert not photo_staging._stage_path(photo_id).exists()

    replayed = client.post(f"/api/looks/photo-stages/{photo_id}/save-extracted", json=review)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == look
    conflict = client.post(
        f"/api/looks/photo-stages/{photo_id}/save-extracted",
        json={**review, "name": "Different reviewed name"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1

    evidence_response = client.get(
        f"/api/looks/{look['key']}/versions/{look['version']}/photo-evidence"
    )
    assert evidence_response.status_code == 200, evidence_response.text
    evidence = evidence_response.json()
    assert evidence["source"] == {
        "sha256": sha256(image).hexdigest(),
        "media_type": "image/png",
        "byte_count": len(image),
        "width": 4,
        "height": 3,
    }
    assert evidence["output"] == output
    assert evidence["corrections"]["garments"] == review["garments"]
    assert evidence["corrections"]["unresolved_decisions"] == review["unresolved_decisions"]
    serialized_evidence = json.dumps(evidence)
    for private_value in (
        "data:", base64.b64encode(image).decode("ascii"), config["llm_url"], config["llm_key"],
    ):
        assert private_value not in serialized_evidence

    exported = client.get(f"/api/looks/{look['key']}/versions/{look['version']}/export")
    assert exported.status_code == 200, exported.text
    assert exported.json()["provenance"] == {
        "source": "photo",
        "image_sha256": sha256(image).hexdigest(),
    }
    assert "request" not in exported.text and "corrections" not in exported.text

    expired = photo_staging._iso(photo_staging._now() - timedelta(hours=25))
    db.run("UPDATE look_photo_stage SET expires_at = ? WHERE photo_id = ?", expired, photo_id)
    photo_staging.startup_recovery(now=photo_staging._now())
    assert db.one("SELECT photo_id FROM look_photo_stage WHERE photo_id = ?", photo_id) is None
    assert client.get(
        f"/api/looks/{look['key']}/versions/{look['version']}/photo-evidence"
    ).status_code == 200


def json_module_dumps(value):
    return json.dumps(value, ensure_ascii=False)


def test_provider_errors_invalid_output_and_unsafe_echo_keep_photo_preview(client, monkeypatch):
    config = _config(monkeypatch)
    photo_id = _upload(client).json()["photo_id"]

    async def bad_json(_config, p, image, *, request_evidence):
        request_evidence.update({
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": [
                    {"type": "text", "text": p.instruction},
                    {"type": "image_url", "image_url": {"url": image}},
                ]},
            ],
            "model": config["llm_vision_model"],
            "parameters": {"temperature": 0.8, "stream": False},
        })
        return {**_proposal(), "removal_order": ["a white shirt"]}

    monkeypatch.setattr(main.enhance, "run_structured", bad_json)
    response = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "vision_request_failed"
    assert "removal_order" not in response.text
    assert db.one("SELECT state FROM photo_look_proposal WHERE photo_id = ?", photo_id)["state"] == "failed"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").status_code == 200

    async def echoed_uri(_config, p, image, *, request_evidence):
        request_evidence.update({
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": [
                    {"type": "text", "text": p.instruction},
                    {"type": "image_url", "image_url": {"url": image}},
                ]},
            ],
            "model": config["llm_vision_model"],
            "parameters": {"temperature": 0.8, "stream": False},
        })
        return {"appearance": image, "garments": [], "unresolved": []}

    monkeypatch.setattr(main.enhance, "run_structured", echoed_uri)
    response = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "vision_request_failed"
    assert "data:image" not in response.text
    assert db.one("SELECT state FROM photo_look_proposal WHERE photo_id = ?", photo_id)["state"] == "failed"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0

    private_echoes = (
        base64.b64encode(_image_bytes()).decode("ascii"),
        "vision.example.invalid/v1/chat/completions",
        "/v1/chat/completions",
        "X-API-Secret: hidden-test-value",
    )
    for private_value in private_echoes:
        async def echoed_private(_config, p, image, *, request_evidence):
            request_evidence.update({
                "messages": [
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": [
                        {"type": "text", "text": p.instruction},
                        {"type": "image_url", "image_url": {"url": image}},
                    ]},
                ],
                "model": config["llm_vision_model"],
                "parameters": {"temperature": 0.8, "stream": False},
            })
            return {"appearance": private_value, "garments": [], "unresolved": []}

        monkeypatch.setattr(main.enhance, "run_structured", echoed_private)
        response = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
        assert response.status_code == 502
        assert private_value not in response.text
        assert db.one("SELECT state FROM photo_look_proposal WHERE photo_id = ? ORDER BY generation DESC", photo_id)["state"] == "failed"
        assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0


def test_unavailable_vision_and_failed_provider_leave_manual_save_available(client, monkeypatch):
    config = _config(monkeypatch)
    config["llm_vision_model"] = ""
    called = []

    async def should_not_run(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("vision transport ran while unavailable")

    monkeypatch.setattr(main.enhance, "run_structured", should_not_run)
    photo_id = _upload(client).json()["photo_id"]
    unavailable = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "vision_unavailable"
    assert called == []
    assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").status_code == 200

    manual = client.post(f"/api/looks/photo-stages/{photo_id}/save", json={"name": "Manual notes"})
    assert manual.status_code == 200, manual.text
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == 0


@pytest.mark.parametrize("late_action, expected_status, expected_state", [
    ("cancel", 410, "cancelled"),
    ("expire", 410, "expired"),
    ("disable", 503, "staged"),
])
def test_late_cancel_expiry_and_feature_disable_discard_provider_output(
    client, monkeypatch, late_action, expected_status, expected_state,
):
    config = _config(monkeypatch)
    photo_id = _upload(client).json()["photo_id"]

    async def late_provider(_config, p, image, *, request_evidence):
        request_evidence.update({
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": [
                    {"type": "text", "text": p.instruction},
                    {"type": "image_url", "image_url": {"url": image}},
                ]},
            ],
            "model": config["llm_vision_model"],
            "parameters": {"temperature": 0.8, "stream": False},
        })
        if late_action == "cancel":
            photo_staging.cancel_stage(photo_id)
        elif late_action == "expire":
            db.run(
                "UPDATE look_photo_stage SET expires_at = ? WHERE photo_id = ?",
                photo_staging._iso(photo_staging._now() - timedelta(seconds=1)),
                photo_id,
            )
        else:
            config["resource_planning_enabled"] = False
        return {"appearance": "short dark hair", "garments": [], "unresolved": []}

    monkeypatch.setattr(main.enhance, "run_structured", late_provider)
    response = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert response.status_code == expected_status, response.text
    expected_code = {
        "cancel": "photo_stage_cancelled",
        "expire": "photo_stage_expired",
        "disable": "resource_planning_disabled",
    }[late_action]
    assert response.json()["detail"]["code"] == expected_code
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == expected_state
    if late_action == "disable":
        assert db.one("SELECT state FROM photo_look_proposal WHERE photo_id = ?", photo_id)["state"] == "failed"
    else:
        assert db.one("SELECT COUNT(*) AS n FROM photo_look_proposal")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    if late_action == "disable":
        assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").status_code == 200


def test_stage_tampering_is_detected_before_provider_call(client, monkeypatch):
    _config(monkeypatch)
    photo_id = _upload(client).json()["photo_id"]
    photo_staging._stage_path(photo_id).write_bytes(_image_bytes(color=(1, 2, 3)))
    called = []

    async def should_not_run(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("tampered bytes reached the provider")

    monkeypatch.setattr(main.enhance, "run_structured", should_not_run)
    response = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "photo_stage_changed"
    assert called == []
    assert db.one("SELECT COUNT(*) AS n FROM photo_look_proposal")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0


def test_newer_failed_attempt_still_fences_older_in_flight_proposal(client):
    photo_id = _upload(client).json()["photo_id"]
    _image_bytes, metadata = photo_staging.read_staged_photo(photo_id)
    older = photo_extraction._start_attempt(photo_id, "a" * 32, metadata)
    newer = photo_extraction._start_attempt(photo_id, "b" * 32, metadata)
    assert (older, newer) == (1, 2)

    photo_extraction._discard_attempt(photo_id, "b" * 32)
    assert db.one(
        "SELECT state FROM photo_look_proposal WHERE photo_id = ? AND proposal_id = ?",
        photo_id, "b" * 32,
    )["state"] == "failed"
    projection = {"messages": [], "model": "test-vision-model", "parameters": {}}
    with pytest.raises(photo_extraction.PhotoExtractionError) as error:
        photo_extraction._publish_attempt(
            photo_id,
            "a" * 32,
            older,
            metadata,
            projection,
            {"appearance": "", "garments": [], "unresolved": []},
            lambda: True,
        )
    assert error.value.code == "photo_proposal_stale"
    assert db.one(
        "SELECT state FROM photo_look_proposal WHERE photo_id = ? AND proposal_id = ?",
        photo_id, "a" * 32,
    )["state"] == "failed"
    next_attempt = photo_extraction._start_attempt(photo_id, "c" * 32, metadata)
    assert next_attempt == 3


def test_proposal_ids_are_stage_bound_and_stale_review_cannot_save(client, monkeypatch):
    config = _config(monkeypatch)

    async def fake_provider(_config, p, image, *, request_evidence):
        request_evidence.update({
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": [
                    {"type": "text", "text": p.instruction},
                    {"type": "image_url", "image_url": {"url": image}},
                ]},
            ],
            "model": config["llm_vision_model"],
            "parameters": {"temperature": 0.8, "stream": False},
        })
        return {"appearance": "short dark hair", "garments": ["a white shirt"], "unresolved": []}

    monkeypatch.setattr(main.enhance, "run_structured", fake_provider)
    first_photo = _upload(client).json()["photo_id"]
    first = client.post(f"/api/looks/photo-stages/{first_photo}/extract", json={}).json()
    second_photo = _upload(client).json()["photo_id"]
    second_proposal = client.post(f"/api/looks/photo-stages/{second_photo}/extract", json={}).json()
    review = {
        "proposal_id": first["proposal_id"],
        "name": "Review",
        "appearance": "short dark hair",
        "garments": [{"wording": "a white shirt", "aside": ""}],
        "unresolved_decisions": [],
        "review_confirmed": True,
        "removal_order_confirmed": True,
    }
    foreign = client.post(f"/api/looks/photo-stages/{second_photo}/save-extracted", json=review)
    assert foreign.status_code == 409
    assert foreign.json()["detail"]["code"] == "photo_proposal_stale"

    replacement = client.post(f"/api/looks/photo-stages/{first_photo}/extract", json={}).json()
    assert replacement["proposal_id"] != first["proposal_id"]
    stale = client.post(f"/api/looks/photo-stages/{first_photo}/save-extracted", json=review)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "photo_proposal_stale"

    forged = client.post(
        f"/api/looks/photo-stages/{first_photo}/save-extracted",
        json={**review, "proposal_id": replacement["proposal_id"], "request_evidence": {"data": "not trusted"}},
    )
    assert forged.status_code == 422
    assert "not trusted" not in forged.text
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == 0
    assert second_proposal["proposal_id"] != replacement["proposal_id"]


def test_review_rejects_sensitive_corrections_and_save_rolls_back_look_evidence_and_cleanup(
    client, monkeypatch,
):
    config = _config(monkeypatch)

    async def fake_provider(_config, p, image, *, request_evidence):
        request_evidence.update({
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": [
                    {"type": "text", "text": p.instruction},
                    {"type": "image_url", "image_url": {"url": image}},
                ]},
            ],
            "model": config["llm_vision_model"],
            "parameters": {"temperature": 0.8, "stream": False},
        })
        return _proposal()

    monkeypatch.setattr(main.enhance, "run_structured", fake_provider)
    photo_id = _upload(client).json()["photo_id"]
    proposal = client.post(f"/api/looks/photo-stages/{photo_id}/extract", json={}).json()
    review = {
        "proposal_id": proposal["proposal_id"],
        "name": "Review",
        "appearance": "short dark hair",
        "garments": [{"wording": "a white shirt", "aside": ""}],
        "unresolved_decisions": [{
            "id": "u1", "action": "correct", "correction": "Authorization: Bearer invented-key-for-test-only",
        }],
        "review_confirmed": True,
        "removal_order_confirmed": True,
    }
    unsafe = client.post(f"/api/looks/photo-stages/{photo_id}/save-extracted", json=review)
    assert unsafe.status_code == 422
    assert "invented-key" not in unsafe.text
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "staged"

    review["unresolved_decisions"] = [{"id": "u1", "action": "omit"}]
    original_save = main.saved_looks.save_photo_evidence

    def fail_evidence(*_args, **_kwargs):
        raise RuntimeError("private database failure detail")

    monkeypatch.setattr(main.saved_looks, "save_photo_evidence", fail_evidence)
    with pytest.raises(RuntimeError, match="private database failure detail"):
        client.post(f"/api/looks/photo-stages/{photo_id}/save-extracted", json=review)
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == 0
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "staged"
    assert photo_staging._stage_path(photo_id).is_file()
    assert db.one("SELECT state FROM photo_look_proposal WHERE photo_id = ?", photo_id)["state"] == "ready"

    monkeypatch.setattr(main.saved_looks, "save_photo_evidence", original_save)
    saved = client.post(f"/api/looks/photo-stages/{photo_id}/save-extracted", json=review)
    assert saved.status_code == 200, saved.text
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == 1
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "saved"
    assert not photo_staging._stage_path(photo_id).exists()
