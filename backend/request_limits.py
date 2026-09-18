"""Reusable actual-streamed-body limit boundary for request content.

Enforces an exact streamed byte limit on incoming HTTP requests before FastAPI
JSON/Pydantic deserialization, multipart form parsing, or endpoint writes occur.
A bounded pre-read of the raw ASGI stream verifies the entire request body
before handing the request to downstream parsers via an exact in-memory replay.

Declared Content-Length headers may be used for early rejection when reliably
oversized, but are never authoritative for acceptance. Missing, empty, malformed,
or false-small Content-Length declarations cannot bypass the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Mapping, Sequence
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.types import Message, Receive, Scope, Send

# Normative 10 MiB boundary for content-bearing JSON requests (10 * 1024 * 1024)
DEFAULT_MAX_JSON_BODY_BYTES: int = 10 * 1024 * 1024
REQUEST_BODY_TOO_LARGE_DETAIL: str = "Request entity too large"


class PreReadState(Enum):
    COMPLETE = auto()
    TOO_LARGE = auto()
    DISCONNECTED = auto()


@dataclass(frozen=True, slots=True)
class PreReadResult:
    state: PreReadState
    replay_receive: Receive | None = None


class _ReplayState(Enum):
    PENDING = auto()
    TERMINAL_PENDING = auto()
    TERMINAL_DELIVERED = auto()


class _DisconnectedResponse(Response):
    """Satisfy the route-handler contract without emitting an ASGI response."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        return


def check_content_length_oversized(
    headers: Headers | Mapping[str, str] | Sequence[tuple[bytes, bytes]] | None,
    max_bytes: int = DEFAULT_MAX_JSON_BODY_BYTES,
) -> bool:
    """Return True if Content-Length is an unequivocal decimal strictly exceeding max_bytes.

    Fast-path early rejection rule:
    Only triggers if there is EXACTLY ONE unambiguous, valid Content-Length declaration
    strictly greater than max_bytes. If the header is missing, empty, duplicated,
    comma-separated, negative, or non-numeric, returns False so that actual stream byte
    counting remains the sole authority.
    """
    if headers is None:
        return False
    if isinstance(headers, Headers):
        h = headers
    elif isinstance(headers, (list, tuple)):
        raw_headers = [
            (
                k.lower() if isinstance(k, bytes) else k.encode("latin-1").lower(),
                v if isinstance(v, bytes) else v.encode("latin-1"),
            )
            for k, v in headers
        ]
        h = Headers(raw=raw_headers)
    elif isinstance(headers, Mapping):
        h = Headers(headers=headers)
    else:
        return False

    raw_list = h.getlist("content-length")
    # Must have exactly one Content-Length declaration
    if len(raw_list) != 1:
        return False

    raw_val = raw_list[0]
    # Comma-separated declarations are ambiguous; defer to actual byte stream
    if "," in raw_val:
        return False

    val_str = raw_val.strip()
    if not val_str or not val_str.isdigit():
        return False

    try:
        val = int(val_str)
        return val > max_bytes
    except ValueError:
        return False


def create_payload_too_large_response(
    detail: str = REQUEST_BODY_TOO_LARGE_DETAIL,
) -> JSONResponse:
    """Construct a path-free, credential-free, parser-free HTTP 413 response."""
    return JSONResponse(status_code=413, content={"detail": detail})


async def pre_read_and_bound_body(
    receive: Receive,
    max_bytes: int = DEFAULT_MAX_JSON_BODY_BYTES,
) -> PreReadResult:
    """Pre-read and enforce memory/byte limits on the raw ASGI stream.

    Reads chunks sequentially from `receive` up to `max_bytes`:
    Returns an explicit complete, too-large, or disconnected result. A complete
    result includes a replay receive that never reads the network again after
    pre-read has observed the terminal request message.
    """
    captured: list[Message] = []
    total_bytes = 0

    while True:
        message = await receive()
        msg_type = message.get("type")
        if msg_type == "http.request":
            body = message.get("body", b"")
            chunk_len = len(body)
            if total_bytes + chunk_len > max_bytes:
                # Discard buffered chunks immediately to bound memory
                captured.clear()
                return PreReadResult(PreReadState.TOO_LARGE)
            total_bytes += chunk_len
            captured.append(message)
            if not message.get("more_body", False):
                break
        elif msg_type == "http.disconnect":
            captured.clear()
            return PreReadResult(PreReadState.DISCONNECTED)
        else:
            captured.append(message)

    msg_index = 0
    replay_state = (
        _ReplayState.TERMINAL_PENDING
        if len(captured) == 1
        else _ReplayState.PENDING
    )

    async def replay_receive() -> Message:
        nonlocal msg_index, replay_state
        if replay_state is _ReplayState.TERMINAL_DELIVERED:
            return {"type": "http.request", "body": b"", "more_body": False}

        msg = captured[msg_index]
        msg_index += 1
        if not msg.get("more_body", False):
            replay_state = _ReplayState.TERMINAL_DELIVERED
        elif msg_index == len(captured) - 1:
            replay_state = _ReplayState.TERMINAL_PENDING
        return msg

    return PreReadResult(PreReadState.COMPLETE, replay_receive)


class RequestLimitRoute(APIRoute):
    """FastAPI APIRoute subclass enforcing an actual-streamed body limit before parsing.

    Pre-reads and verifies total request body bytes from the raw ASGI stream
    before passing the request to FastAPI's dependency solver, Pydantic validators,
    or multipart form parsers.
    """

    max_body_bytes: int = DEFAULT_MAX_JSON_BODY_BYTES

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()
        max_bytes = self.max_body_bytes

        async def custom_route_handler(request: Request) -> Response:
            # 1. Early Content-Length check (fast path)
            if check_content_length_oversized(request.headers, max_bytes):
                return create_payload_too_large_response()

            # 2. Bounded pre-read of actual stream bytes before downstream parsing
            pre_read = await pre_read_and_bound_body(
                request._receive, max_bytes=max_bytes
            )
            if pre_read.state is PreReadState.TOO_LARGE:
                return create_payload_too_large_response()
            if pre_read.state is PreReadState.DISCONNECTED:
                return _DisconnectedResponse()

            # 3. Stream verified within limit; supply replay receive to downstream
            assert pre_read.replay_receive is not None
            request._receive = pre_read.replay_receive
            return await original_handler(request)

        return custom_route_handler


def create_request_limit_route(
    max_body_bytes: int = DEFAULT_MAX_JSON_BODY_BYTES,
) -> type[RequestLimitRoute]:
    """Return a RequestLimitRoute subclass configured with the specified byte limit."""
    class ConfiguredRequestLimitRoute(RequestLimitRoute):
        pass

    ConfiguredRequestLimitRoute.max_body_bytes = max_body_bytes
    return ConfiguredRequestLimitRoute
