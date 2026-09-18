"""Tests for backend.request_limits shared actual-streamed-body boundary.

Verifies FastAPI route-level boundaries enforcing actual-streamed byte limits
before JSON/Pydantic deserialization, multipart form parsing, or route writes occur.
"""
from __future__ import annotations

import json
from typing import AsyncGenerator
import pytest
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.routing import APIRouter
from pydantic import BaseModel, Field, field_validator
from starlette.datastructures import Headers
from starlette.formparsers import MultiPartParser
from starlette.testclient import TestClient
from starlette.types import Message, Scope, Send

from backend.request_limits import (
    DEFAULT_MAX_JSON_BODY_BYTES,
    PreReadState,
    REQUEST_BODY_TOO_LARGE_DETAIL,
    check_content_length_oversized,
    create_request_limit_route,
    pre_read_and_bound_body,
    RequestLimitRoute,
)


class InstrumentedPayload(BaseModel):
    items: list[str] = Field(default_factory=list)

    @field_validator("items")
    @classmethod
    def track_validation(cls, v: list[str]) -> list[str]:
        InstrumentedPayload.validation_call_count += 1
        return v


InstrumentedPayload.validation_call_count = 0


@pytest.fixture
def multipart_parser_counter(monkeypatch):
    """Instrument Starlette MultiPartParser.parse to record invocation count."""
    calls = []
    orig_parse = MultiPartParser.parse

    async def tracking_parse(self):
        calls.append(True)
        return await orig_parse(self)

    monkeypatch.setattr(MultiPartParser, "parse", tracking_parse)
    return calls


def build_deterministic_multipart_body(
    target_total_bytes: int,
    boundary: str = "deterministic_boundary_12345",
) -> tuple[bytes, str]:
    """Construct a deterministic multipart request body of exact total bytes."""
    prefix = (
        b"--" + boundary.encode("latin-1") + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="sample.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\n"
    )
    suffix = b"\r\n--" + boundary.encode("latin-1") + b"--\r\n"
    framing_len = len(prefix) + len(suffix)
    assert target_total_bytes >= framing_len, (
        f"Target bytes {target_total_bytes} must be >= framing {framing_len}"
    )
    file_len = target_total_bytes - framing_len
    file_content = b"a" * file_len
    full_body = prefix + file_content + suffix
    assert len(full_body) == target_total_bytes
    content_type = f"multipart/form-data; boundary={boundary}"
    return full_body, content_type


# ---------------------------------------------------------------------------
# Header & Fast-Path Unit Tests
# ---------------------------------------------------------------------------

def test_check_content_length_oversized_exhaustive():
    """Verify fast-path Content-Length rejection rule for all ambiguous and valid cases."""
    max_bytes = 1000

    # No header -> defer
    assert check_content_length_oversized(None, max_bytes) is False
    assert check_content_length_oversized({}, max_bytes) is False
    assert check_content_length_oversized(Headers({}), max_bytes) is False

    # Exact boundary -> no early reject (defer)
    assert check_content_length_oversized({"content-length": "1000"}, max_bytes) is False
    assert check_content_length_oversized([(b"content-length", b"1000")], max_bytes) is False

    # Boundary + 1 -> early reject
    assert check_content_length_oversized({"content-length": "1001"}, max_bytes) is True
    assert check_content_length_oversized([(b"content-length", b"1001")], max_bytes) is True
    assert check_content_length_oversized([(b"Content-Length", b"2000")], max_bytes) is True

    # Empty string -> defer
    assert check_content_length_oversized({"content-length": ""}, max_bytes) is False
    assert check_content_length_oversized([(b"content-length", b"")], max_bytes) is False

    # Invalid string -> defer
    assert check_content_length_oversized({"content-length": "invalid"}, max_bytes) is False

    # Negative -> defer
    assert check_content_length_oversized({"content-length": "-1"}, max_bytes) is False

    # Two Content-Length headers even if one is empty -> defer (ambiguous)
    assert check_content_length_oversized(
        [(b"content-length", b"2000"), (b"content-length", b"")], max_bytes
    ) is False

    # Two identical Content-Length headers -> defer (ambiguous)
    assert check_content_length_oversized(
        [(b"content-length", b"2000"), (b"content-length", b"2000")], max_bytes
    ) is False

    # Two conflicting Content-Length headers -> defer (ambiguous)
    assert check_content_length_oversized(
        [(b"content-length", b"2000"), (b"content-length", b"500")], max_bytes
    ) is False

    # Comma-separated Content-Length -> defer (ambiguous)
    assert check_content_length_oversized({"content-length": "500, 2000"}, max_bytes) is False
    assert check_content_length_oversized({"content-length": "2000, 2000"}, max_bytes) is False


@pytest.mark.anyio
async def test_early_content_length_rejection_no_receive():
    """Verify single unequivocal oversized Content-Length rejects before calling receive."""
    receive_called = False

    async def spy_receive() -> Message:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"x" * 100, "more_body": False}

    app = FastAPI()
    router = APIRouter(route_class=create_request_limit_route(max_body_bytes=50))

    @router.post("/api/fast-path")
    async def fast_path_endpoint(request: Request):
        return {"status": "ok"}

    app.include_router(router)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/fast-path",
        "raw_path": b"/api/fast-path",
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "headers": [
            (b"content-length", b"200"),
            (b"content-type", b"application/octet-stream"),
        ],
    }
    sent_messages = []

    async def fake_send(message: Message) -> None:
        sent_messages.append(message)

    await app(scope, spy_receive, fake_send)

    assert receive_called is False
    response_start = next(
        (m for m in sent_messages if m.get("type") == "http.response.start"), None
    )
    assert response_start is not None
    assert response_start.get("status") == 413


# ---------------------------------------------------------------------------
# Real JSON and Pydantic Tests
# ---------------------------------------------------------------------------

@pytest.fixture
def json_app():
    """Build a FastAPI test app with a RequestLimitRoute JSON endpoint."""
    app = FastAPI()
    router = APIRouter(route_class=RequestLimitRoute)
    sentinel = {"calls": 0, "payloads": []}

    @router.post("/api/json-limit")
    def guarded_json(payload: InstrumentedPayload):
        sentinel["calls"] += 1
        sentinel["payloads"].append(payload.model_dump())
        return {"status": "ok", "count": len(payload.items)}

    app.include_router(router)
    return app, sentinel


def test_json_exact_10_mib_single_chunk_accepted():
    """Verify exactly 10,485,760 bytes of raw JSON body in a single chunk are accepted."""
    app = FastAPI()
    router = APIRouter(route_class=RequestLimitRoute)
    called = False

    @router.post("/api/raw-check")
    async def raw_check(request: Request):
        nonlocal called
        body = await request.body()
        called = True
        return {"len": len(body)}

    app.include_router(router)
    client = TestClient(app)

    exact_body = b"x" * DEFAULT_MAX_JSON_BODY_BYTES
    res = client.post(
        "/api/raw-check",
        content=exact_body,
        headers={"content-type": "application/octet-stream"},
    )
    assert res.status_code == 200
    assert res.json() == {"len": DEFAULT_MAX_JSON_BODY_BYTES}
    assert called is True


def test_json_exact_10_mib_multi_chunk_accepted():
    """Verify exactly 10,485,760 bytes streamed across multiple chunks are accepted."""
    app = FastAPI()
    router = APIRouter(route_class=RequestLimitRoute)
    called = False

    @router.post("/api/raw-check")
    async def raw_check(request: Request):
        nonlocal called
        body = await request.body()
        called = True
        return {"len": len(body)}

    app.include_router(router)
    client = TestClient(app)

    chunk_size = 1024 * 1024
    chunks = [b"k" * chunk_size for _ in range(10)]
    assert sum(len(c) for c in chunks) == DEFAULT_MAX_JSON_BODY_BYTES

    res = client.post(
        "/api/raw-check",
        content=(c for c in chunks),
        headers={"content-type": "application/octet-stream"},
    )
    assert res.status_code == 200
    assert res.json() == {"len": DEFAULT_MAX_JSON_BODY_BYTES}
    assert called is True


def test_json_limit_plus_one_single_chunk_rejected():
    """Verify 10,485,761 bytes in a single chunk returns HTTP 413."""
    app = FastAPI()
    router = APIRouter(route_class=RequestLimitRoute)
    called = False

    @router.post("/api/raw-check")
    async def raw_check(request: Request):
        nonlocal called
        called = True
        return {"ok": True}

    app.include_router(router)
    client = TestClient(app)

    oversized = b"x" * (DEFAULT_MAX_JSON_BODY_BYTES + 1)
    res = client.post(
        "/api/raw-check",
        content=oversized,
        headers={"content-type": "application/octet-stream"},
    )
    assert res.status_code == 413
    assert res.json() == {"detail": REQUEST_BODY_TOO_LARGE_DETAIL}
    assert called is False


def test_json_limit_plus_one_multi_chunk_crossing_rejected():
    """Verify stream crossing the limit on the final chunk returns HTTP 413."""
    app = FastAPI()
    router = APIRouter(route_class=RequestLimitRoute)
    called = False

    @router.post("/api/raw-check")
    async def raw_check(request: Request):
        nonlocal called
        called = True
        return {"ok": True}

    app.include_router(router)
    client = TestClient(app)

    chunks = [b"a" * DEFAULT_MAX_JSON_BODY_BYTES, b"b"]
    res = client.post(
        "/api/raw-check",
        content=(c for c in chunks),
        headers={"content-type": "application/octet-stream"},
    )
    assert res.status_code == 413
    assert res.json() == {"detail": REQUEST_BODY_TOO_LARGE_DETAIL}
    assert called is False


def test_json_missing_content_length_oversized_rejected():
    """Verify oversized chunked stream lacking Content-Length returns 413."""
    app = FastAPI()
    router = APIRouter(route_class=RequestLimitRoute)
    called = False

    @router.post("/api/raw-check")
    async def raw_check(request: Request):
        nonlocal called
        called = True
        return {"ok": True}

    app.include_router(router)
    client = TestClient(app)

    chunk_size = 2 * 1024 * 1024
    chunks = [b"c" * chunk_size for _ in range(6)]  # 12 MiB total
    res = client.post(
        "/api/raw-check",
        content=(c for c in chunks),
        headers={"content-type": "application/octet-stream"},
    )
    assert res.status_code == 413
    assert res.json() == {"detail": REQUEST_BODY_TOO_LARGE_DETAIL}
    assert called is False


def test_json_false_small_content_length_oversized_rejected():
    """Verify false-small Content-Length cannot bypass actual stream byte limits."""
    app = FastAPI()
    router = APIRouter(route_class=RequestLimitRoute)
    called = False

    @router.post("/api/raw-check")
    async def raw_check(request: Request):
        nonlocal called
        called = True
        return {"ok": True}

    app.include_router(router)
    client = TestClient(app)

    chunk_size = 2 * 1024 * 1024
    chunks = [b"d" * chunk_size for _ in range(6)]  # 12 MiB total
    res = client.post(
        "/api/raw-check",
        content=(c for c in chunks),
        headers={
            "content-length": "64",
            "content-type": "application/octet-stream",
        },
    )
    assert res.status_code == 413
    assert res.json() == {"detail": REQUEST_BODY_TOO_LARGE_DETAIL}
    assert called is False


def test_pydantic_validation_and_writes_skipped_on_oversized():
    """Verify Pydantic validators and endpoint writes never run on oversized JSON."""
    InstrumentedPayload.validation_call_count = 0
    route_write_count = 0

    app = FastAPI()
    router = APIRouter(route_class=create_request_limit_route(max_body_bytes=200))

    @router.post("/api/pydantic-guarded")
    def guarded_endpoint(payload: InstrumentedPayload):
        nonlocal route_write_count
        route_write_count += 1
        return {"count": len(payload.items)}

    app.include_router(router)
    client = TestClient(app)

    # 1. Under-limit valid payload: validator runs once, endpoint writes once
    valid_payload = json.dumps({"items": ["alpha", "beta"]}).encode("utf-8")
    res_valid = client.post(
        "/api/pydantic-guarded",
        content=valid_payload,
        headers={"content-type": "application/json"},
    )
    assert res_valid.status_code == 200
    assert InstrumentedPayload.validation_call_count == 1
    assert route_write_count == 1

    # 2. Oversized syntactically valid JSON: validator must NOT run, endpoint must NOT run
    large_items = ["item_" + ("x" * 50) for _ in range(10)]
    oversized_payload = json.dumps({"items": large_items}).encode("utf-8")
    assert len(oversized_payload) > 200

    res_over = client.post(
        "/api/pydantic-guarded",
        content=oversized_payload,
        headers={"content-type": "application/json"},
    )
    assert res_over.status_code == 413
    assert res_over.json() == {"detail": REQUEST_BODY_TOO_LARGE_DETAIL}
    assert InstrumentedPayload.validation_call_count == 1
    assert route_write_count == 1

    # 3. Oversized chunked stream lacking Content-Length
    res_chunked = client.post(
        "/api/pydantic-guarded",
        content=(chunk for chunk in [oversized_payload[:100], oversized_payload[100:]]),
        headers={"content-type": "application/json"},
    )
    assert res_chunked.status_code == 413
    assert InstrumentedPayload.validation_call_count == 1
    assert route_write_count == 1


# ---------------------------------------------------------------------------
# Real Multipart Tests (UploadFile = File(...))
# ---------------------------------------------------------------------------

@pytest.fixture
def multipart_app():
    """Build a FastAPI test app with a RequestLimitRoute multipart endpoint."""
    app = FastAPI()
    router = APIRouter(route_class=create_request_limit_route(max_body_bytes=DEFAULT_MAX_JSON_BODY_BYTES))
    sentinel = {"calls": 0, "filenames": [], "sizes": []}

    @router.post("/api/multipart-limit")
    async def guarded_upload(file: UploadFile = File(...)):
        sentinel["calls"] += 1
        content = await file.read()
        sentinel["filenames"].append(file.filename)
        sentinel["sizes"].append(len(content))
        return {"filename": file.filename, "size": len(content)}

    app.include_router(router)
    return app, sentinel


def test_multipart_valid_under_limit_accepted(multipart_app, multipart_parser_counter):
    """Case A: Valid multipart under limit -> parser executes, endpoint executes."""
    app, sentinel = multipart_app
    client = TestClient(app)

    body, ct = build_deterministic_multipart_body(1000)
    res = client.post("/api/multipart-limit", content=body, headers={"content-type": ct})

    assert res.status_code == 200
    assert sentinel["calls"] == 1
    assert sentinel["filenames"] == ["sample.txt"]
    assert len(multipart_parser_counter) == 1


def test_multipart_exact_boundary_accepted(multipart_app, multipart_parser_counter):
    """Case B: Multipart body exact boundary (10,485,760 bytes) -> accepted, parser executes."""
    app, sentinel = multipart_app
    client = TestClient(app)

    body, ct = build_deterministic_multipart_body(DEFAULT_MAX_JSON_BODY_BYTES)
    assert len(body) == DEFAULT_MAX_JSON_BODY_BYTES

    res = client.post("/api/multipart-limit", content=body, headers={"content-type": ct})

    assert res.status_code == 200
    assert sentinel["calls"] == 1
    assert len(multipart_parser_counter) == 1


def test_multipart_limit_plus_one_rejected_before_parse(
    multipart_app, multipart_parser_counter
):
    """Case C: Multipart body limit+1 (10,485,761 bytes) -> 413, parser=0, endpoint=0, writes=0."""
    app, sentinel = multipart_app
    client = TestClient(app)

    body, ct = build_deterministic_multipart_body(DEFAULT_MAX_JSON_BODY_BYTES + 1)
    assert len(body) == DEFAULT_MAX_JSON_BODY_BYTES + 1

    res = client.post("/api/multipart-limit", content=body, headers={"content-type": ct})

    assert res.status_code == 413
    assert res.json() == {"detail": REQUEST_BODY_TOO_LARGE_DETAIL}
    assert len(multipart_parser_counter) == 0
    assert sentinel["calls"] == 0


def test_multipart_oversized_missing_content_length_rejected_before_parse(
    multipart_app, multipart_parser_counter
):
    """Case D: Multipart oversized stream without Content-Length -> 413, parser=0, endpoint=0."""
    app, sentinel = multipart_app
    client = TestClient(app)

    body, ct = build_deterministic_multipart_body(DEFAULT_MAX_JSON_BODY_BYTES + 1024)
    chunk_size = 1024 * 1024
    chunks = [body[i:i + chunk_size] for i in range(0, len(body), chunk_size)]

    res = client.post(
        "/api/multipart-limit",
        content=(c for c in chunks),
        headers={"content-type": ct},
    )

    assert res.status_code == 413
    assert res.json() == {"detail": REQUEST_BODY_TOO_LARGE_DETAIL}
    assert len(multipart_parser_counter) == 0
    assert sentinel["calls"] == 0


def test_multipart_oversized_false_small_content_length_rejected_before_parse(
    multipart_app, multipart_parser_counter
):
    """Case E: Multipart oversized with false-small Content-Length -> 413, parser=0, endpoint=0."""
    app, sentinel = multipart_app
    client = TestClient(app)

    body, ct = build_deterministic_multipart_body(DEFAULT_MAX_JSON_BODY_BYTES + 500)
    chunk_size = 1024 * 1024
    chunks = [body[i:i + chunk_size] for i in range(0, len(body), chunk_size)]

    res = client.post(
        "/api/multipart-limit",
        content=(c for c in chunks),
        headers={
            "content-length": "64",
            "content-type": ct,
        },
    )

    assert res.status_code == 413
    assert res.json() == {"detail": REQUEST_BODY_TOO_LARGE_DETAIL}
    assert len(multipart_parser_counter) == 0
    assert sentinel["calls"] == 0


# ---------------------------------------------------------------------------
# Additional Edge Cases, Privacy, Double-Response & Replay
# ---------------------------------------------------------------------------

def test_ambiguous_content_length_headers_defers_to_streaming():
    """Verify ambiguous Content-Length headers defer to actual byte stream counting."""
    app = FastAPI()
    router = APIRouter(route_class=create_request_limit_route(max_body_bytes=100))
    endpoint_called = False

    @router.post("/api/ambiguous-cl")
    async def ambiguous_cl(request: Request):
        nonlocal endpoint_called
        body = await request.body()
        endpoint_called = True
        return {"len": len(body)}

    app.include_router(router)
    client = TestClient(app)

    # 1. Multiple headers where one claims oversized and one is empty:
    # Under-limit payload must defer and pass
    res_under = client.post(
        "/api/ambiguous-cl",
        content=b"small",
        headers={"content-length": "1000, ", "content-type": "text/plain"},
    )
    assert res_under.status_code == 200
    assert res_under.json() == {"len": 5}
    assert endpoint_called is True

    # 2. Multiple headers where one claims oversized and one is empty:
    # Oversized payload must defer and be rejected by actual stream counter
    endpoint_called = False
    res_over = client.post(
        "/api/ambiguous-cl",
        content=b"x" * 150,
        headers={"content-length": "1000, ", "content-type": "text/plain"},
    )
    assert res_over.status_code == 413
    assert endpoint_called is False


def test_empty_body_accepted_by_boundary():
    """Verify a 0-byte request body is accepted by the boundary and passed to downstream."""
    app = FastAPI()
    router = APIRouter(route_class=RequestLimitRoute)
    called = False

    @router.post("/api/empty-check")
    async def empty_check(request: Request):
        nonlocal called
        body = await request.body()
        called = True
        return {"len": len(body)}

    app.include_router(router)
    client = TestClient(app)

    res = client.post(
        "/api/empty-check",
        content=b"",
        headers={"content-type": "application/octet-stream"},
    )
    assert res.status_code == 200
    assert res.json() == {"len": 0}
    assert called is True


def test_correct_declared_content_length_preserves_body_bytes(json_app):
    """Verify valid under-limit payload is delivered with exact byte fidelity."""
    app, sentinel = json_app
    client = TestClient(app)

    payload_data = {"items": ["item1", "item2", "item3"]}
    payload_bytes = json.dumps(payload_data).encode("utf-8")

    res = client.post(
        "/api/json-limit",
        content=payload_bytes,
        headers={
            "content-length": str(len(payload_bytes)),
            "content-type": "application/json",
        },
    )
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "count": 3}
    assert sentinel["calls"] == 1
    assert sentinel["payloads"] == [payload_data]


def test_safe_413_response_does_not_leak_secrets_paths_or_payload():
    """Verify 413 response does not disclose payload data, paths, secrets, or parser text."""
    app = FastAPI()
    router = APIRouter(route_class=create_request_limit_route(max_body_bytes=100))

    @router.post("/api/confidential")
    def confidential_endpoint(payload: InstrumentedPayload):
        return {"ok": True}

    app.include_router(router)
    client = TestClient(app)

    secret_token = "SECRET_TOKEN_KEY_98765_ABCD"
    private_path = "system/secret/keys/db_password.txt"
    raw_payload_str = json.dumps({
        "secret": secret_token,
        "path": private_path,
        "padding": "x" * 200,
    })

    res = client.post(
        "/api/confidential",
        content=raw_payload_str.encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    assert res.status_code == 413
    assert res.json() == {"detail": REQUEST_BODY_TOO_LARGE_DETAIL}
    assert secret_token not in res.text
    assert secret_token not in str(res.headers)
    assert private_path not in res.text
    assert "padding" not in res.text


def test_double_response_impossible_downstream_never_executed():
    """Regression test: downstream attempting response before read is never run on oversized."""
    app = FastAPI()
    router = APIRouter(route_class=create_request_limit_route(max_body_bytes=50))
    downstream_entered = False

    @router.post("/api/prevent-double-response")
    async def rogue_endpoint(request: Request):
        nonlocal downstream_entered
        downstream_entered = True
        body = await request.body()
        return {"len": len(body)}

    app.include_router(router)
    client = TestClient(app)

    # Oversized payload
    res = client.post("/api/prevent-double-response", content=b"x" * 100)
    assert res.status_code == 413
    assert res.json() == {"detail": REQUEST_BODY_TOO_LARGE_DETAIL}
    assert downstream_entered is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "messages, expected_body",
    [
        ([{"type": "http.request", "body": b"single", "more_body": False}], b"single"),
        (
            [
                {"type": "http.request", "body": b"multi-", "more_body": True},
                {"type": "http.request", "body": b"chunk", "more_body": False},
            ],
            b"multi-chunk",
        ),
        ([{"type": "http.request", "body": b"", "more_body": False}], b""),
    ],
)
async def test_replay_preserves_bytes_and_never_rereads_after_terminal(
    messages, expected_body
):
    """Single, multi-chunk, and empty bodies replay exactly without network rereads."""
    original_calls = 0

    async def original_receive() -> Message:
        nonlocal original_calls
        message = messages[original_calls]
        original_calls += 1
        return message

    result = await pre_read_and_bound_body(original_receive, max_bytes=100)
    assert result.state is PreReadState.COMPLETE
    assert result.replay_receive is not None
    calls_after_pre_read = original_calls

    replayed = []
    while True:
        message = await result.replay_receive()
        replayed.append(message.get("body", b""))
        if not message.get("more_body", False):
            break

    extra = await result.replay_receive()
    assert b"".join(replayed) == expected_body
    assert extra == {"type": "http.request", "body": b"", "more_body": False}
    assert original_calls == calls_after_pre_read == len(messages)


@pytest.mark.anyio
async def test_replay_terminal_is_stable_across_multiple_extra_calls():
    """Repeated receive calls after terminal remain non-blocking and network-free."""
    original_calls = 0

    async def original_receive() -> Message:
        nonlocal original_calls
        original_calls += 1
        return {"type": "http.request", "body": b"body", "more_body": False}

    result = await pre_read_and_bound_body(original_receive, max_bytes=100)
    assert result.replay_receive is not None
    assert await result.replay_receive() == {
        "type": "http.request",
        "body": b"body",
        "more_body": False,
    }
    terminal = {"type": "http.request", "body": b"", "more_body": False}
    assert [await result.replay_receive() for _ in range(3)] == [terminal] * 3
    assert original_calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "messages",
    [
        [{"type": "http.disconnect"}],
        [
            {"type": "http.request", "body": b"partial", "more_body": True},
            {"type": "http.disconnect"},
        ],
    ],
)
async def test_pre_read_disconnect_before_terminal_has_no_replay(messages):
    """A disconnect before terminal makes the partial request unusable."""
    original_calls = 0

    async def original_receive() -> Message:
        nonlocal original_calls
        message = messages[original_calls]
        original_calls += 1
        return message

    result = await pre_read_and_bound_body(original_receive, max_bytes=100)
    assert result.state is PreReadState.DISCONNECTED
    assert result.replay_receive is None
    assert original_calls == len(messages)


@pytest.mark.anyio
async def test_incomplete_disconnect_aborts_without_downstream_or_response(
    multipart_parser_counter,
):
    """At route level, incomplete disconnect emits nothing and skips all downstream work."""
    InstrumentedPayload.validation_call_count = 0
    endpoint_calls = 0
    writes = 0
    app = FastAPI()
    router = APIRouter(route_class=create_request_limit_route(max_body_bytes=100))

    @router.post("/api/disconnect")
    async def disconnected_endpoint(payload: InstrumentedPayload):
        nonlocal endpoint_calls, writes
        endpoint_calls += 1
        writes += 1
        return {"ok": True}

    app.include_router(router)
    messages = [
        {"type": "http.request", "body": b"partial", "more_body": True},
        {"type": "http.disconnect"},
    ]
    original_calls = 0
    sent_messages = []

    async def original_receive() -> Message:
        nonlocal original_calls
        message = messages[original_calls]
        original_calls += 1
        return message

    async def fake_send(message: Message) -> None:
        sent_messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/disconnect",
        "raw_path": b"/api/disconnect",
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "headers": [(b"content-type", b"application/json")],
    }
    await app(scope, original_receive, fake_send)

    assert original_calls == 2
    assert endpoint_calls == 0
    assert len(multipart_parser_counter) == 0
    assert InstrumentedPayload.validation_call_count == 0
    assert writes == 0
    assert sent_messages == []


def test_unrelated_routes_unaffected():
    """Verify routes not opting into RequestLimitRoute remain unrestricted."""
    app = FastAPI()

    @app.post("/api/unrestricted-legacy")
    async def unrestricted(request: Request):
        body = await request.body()
        return {"len": len(body)}

    client = TestClient(app)

    fifteen_mib = b"z" * (15 * 1024 * 1024)
    res = client.post(
        "/api/unrestricted-legacy",
        content=fifteen_mib,
        headers={"content-type": "application/octet-stream"},
    )
    assert res.status_code == 200
    assert res.json() == {"len": 15 * 1024 * 1024}
