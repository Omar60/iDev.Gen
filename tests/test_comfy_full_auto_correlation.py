"""HTTP contracts for Full Auto's conservative Comfy identity path."""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from comfy import Comfy, ComfyError, execution_capability_evidence


def _mock_transport(monkeypatch, responder):
    real_async_client = httpx.AsyncClient

    class MockAsyncClient(real_async_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(responder)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)


def test_client_prompt_capability_requires_explicit_boolean_and_identity():
    stats = {"system": {"comfyui_version": "test-version", "argv": ["comfy"]}}
    advertised = execution_capability_evidence(
        "http://comfy.invalid", stats, {"client_prompt_id": True},
    )
    assert advertised["client_prompt_id"] is True
    assert advertised["advertised_client_prompt_id"] is True
    assert advertised["server_version"] == "test-version"

    for features in (
        {},
        {"client_prompt_id": 1},
        {"client_prompt_id": "true"},
    ):
        evidence = execution_capability_evidence("http://comfy.invalid", stats, features)
        assert evidence["client_prompt_id"] is False

    for version in ("unknown", " UNKNOWN "):
        unknown_version = execution_capability_evidence(
            "http://comfy.invalid",
            {"system": {"comfyui_version": version}},
            {"client_prompt_id": True},
        )
        assert unknown_version["advertised_client_prompt_id"] is True
        assert unknown_version["server_version"] is None
        assert unknown_version["client_prompt_id"] is False
    changed_identity = execution_capability_evidence(
        "http://comfy.invalid",
        {"system": {"comfyui_version": "different-version", "argv": ["comfy"]}},
        {"client_prompt_id": True},
    )
    assert changed_identity["server_identity_digest"] != advertised["server_identity_digest"]


def test_http_prompt_payload_and_queue_history_read_contract(monkeypatch):
    requests = []
    prompt_id = str(uuid.uuid4())

    def respond(request):
        requests.append(request)
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "test-server"}})
        if request.url.path == "/features":
            return httpx.Response(200, json={"client_prompt_id": True})
        if request.url.path == "/prompt" and request.method == "POST":
            body = json.loads(request.read())
            return httpx.Response(200, json={"prompt_id": body.get("prompt_id", prompt_id)})
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if request.url.path == "/history":
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})

    _mock_transport(monkeypatch, respond)
    comfy = Comfy("http://comfy.invalid/")
    graph = {
        "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "idevgen/1/1_ab"}},
    }
    metadata = {
        "schema": "idevgen-full-auto-attempt-v1",
        "client_id": "idevgen-test",
        "attempt_id": "a" * 32,
        "graph_digest": "b" * 64,
        "filename_prefix": "idevgen/1/1_ab",
        "client_prompt_id": prompt_id,
    }

    async def run():
        evidence = await comfy.execution_capabilities()
        assert evidence["client_prompt_id"] is True
        assert await comfy.queue_prompt(
            graph, "idevgen-test", client_prompt_id=prompt_id, attempt_metadata=metadata,
        ) == prompt_id
        assert await comfy.queue_state() == {"queue_running": [], "queue_pending": []}
        assert await comfy.history_entries() == {}

    asyncio.run(run())
    post_requests = [request for request in requests if request.url.path == "/prompt"]
    assert len(post_requests) == 1
    payload = json.loads(post_requests[0].read())
    assert payload["prompt_id"] == prompt_id
    assert payload["client_id"] == "idevgen-test"
    assert payload["extra_data"]["idevgen_attempt"] == metadata
    assert payload["extra_data"]["extra_pnginfo"]["idevgen_attempt"] == metadata


def test_http_unknown_version_omits_id_and_mismatched_ack_is_rejected(monkeypatch):
    bodies = []
    wrong_prompt_id = str(uuid.uuid4())

    def respond(request):
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"system": {"comfyui_version": "unknown"}})
        if request.url.path == "/features":
            return httpx.Response(200, json={"client_prompt_id": True})
        if request.url.path == "/prompt":
            bodies.append(json.loads(request.read()))
            return httpx.Response(200, json={"prompt_id": wrong_prompt_id})
        return httpx.Response(404, json={})

    _mock_transport(monkeypatch, respond)
    comfy = Comfy("http://comfy.invalid")
    graph = {"8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "prefix"}}}

    async def run():
        evidence = await comfy.execution_capabilities()
        assert evidence["client_prompt_id"] is False
        verified_prompt_id = str(uuid.uuid4()) if evidence["client_prompt_id"] else None
        assert await comfy.queue_prompt(
            graph, "idevgen-test", client_prompt_id=verified_prompt_id,
        ) == wrong_prompt_id
        with pytest.raises(ComfyError, match="different prompt ID") as mismatch:
            await comfy.queue_prompt(
                graph, "idevgen-test", client_prompt_id=str(uuid.uuid4()),
            )
        assert mismatch.value.status_code == 200

    asyncio.run(run())
    assert len(bodies) == 2
    assert "prompt_id" not in bodies[0]
    assert bodies[1]["prompt_id"] != wrong_prompt_id
