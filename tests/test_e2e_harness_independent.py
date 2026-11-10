"""Independent adversarial checks for the isolated E2E harness boundaries."""
from __future__ import annotations

import asyncio
import socket

import pytest

from scripts.e2e_harness.server import (
    BackendEgressGuard,
    HarnessIsolationMiddleware,
    owned_path,
    validate_owned_root,
)


def _restore_guard_targets(monkeypatch):
    for name in (
        "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo",
        "create_connection",
    ):
        monkeypatch.setattr(socket, name, getattr(socket, name))
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, getattr(socket.socket, name))
    if hasattr(socket.socket, "sendmsg"):
        monkeypatch.setattr(socket.socket, "sendmsg", socket.socket.sendmsg)


def test_backend_guard_refuses_unowned_loopback_socket_before_connect(monkeypatch):
    """Loopback is not an egress allowlist: unrelated local providers are out of scope."""
    _restore_guard_targets(monkeypatch)

    accepted = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.1)
    port = listener.getsockname()[1]

    guard = BackendEgressGuard()
    guard.install()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            with pytest.raises(PermissionError, match="socket connect"):
                client.connect(("127.0.0.1", port))
        with pytest.raises(TimeoutError):
            accepted.append(listener.accept())
        assert accepted == []
        assert guard.denials == [{"kind": "socket connect", "target": f"('127.0.0.1', {port})"}]
    finally:
        listener.close()


def test_backend_guard_refuses_unowned_udp_sendto_before_network(monkeypatch):
    _restore_guard_targets(monkeypatch)
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(0.1)
    target = receiver.getsockname()

    guard = BackendEgressGuard()
    guard.install()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            with pytest.raises(PermissionError, match="socket sendto"):
                sender.sendto(b"synthetic egress probe", target)
        with pytest.raises(TimeoutError):
            receiver.recvfrom(128)
        assert guard.denials == [{"kind": "socket sendto", "target": str(target)}]
    finally:
        receiver.close()


def test_harness_middleware_blocks_config_preview_and_path_escape_before_handlers(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    handler_calls = []
    denials = []

    async def endpoint(scope, receive, send):
        handler_calls.append((scope["method"], scope["path"], scope.get("query_string", b"")))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"synthetic"})

    app = HarnessIsolationMiddleware(endpoint, root, denials)

    async def request(method, path, query=b"", body=b""):
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query,
            "headers": [],
        }
        await app(scope, receive, send)
        status = next(message["status"] for message in sent if message["type"] == "http.response.start")
        response = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
        return status, response

    assert asyncio.run(request("GET", "/api/models"))[0] == 200
    status, body = asyncio.run(request(
        "PATCH", "/api/config", body=b'{"lora_dir":"../sibling/sentinel.txt"}',
    ))
    assert status == 403
    assert b"sentinel.txt" not in body
    assert asyncio.run(request(
        "GET", "/api/loras/preview", b"name=../../sibling/sentinel.txt",
    ))[0] == 403
    assert asyncio.run(request("GET", "/api/config", b"lora_dir=%2e%2e%2fsibling"))[0] == 403
    assert asyncio.run(request("GET", "/assets/../../sibling/sentinel.txt"))[0] == 403
    assert handler_calls == [("GET", "/api/models", b"")]
    assert denials == [
        {"method": "PATCH", "path": "/api/config"},
        {"method": "GET", "path": "/api/loras/preview"},
        {"method": "GET", "path": "/api/config"},
        {"method": "GET", "path": "/assets/../../sibling/sentinel.txt"},
    ]


def test_owned_path_rejects_absolute_and_parent_paths_without_touching_sibling(tmp_path):
    root = tmp_path / "owned"
    sibling = tmp_path / "sibling"
    root.mkdir()
    sibling.mkdir()
    sentinel = sibling / "sentinel.txt"
    sentinel.write_text("synthetic sentinel\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="relative"):
        owned_path(root, str(sentinel))
    with pytest.raises(RuntimeError, match="relative"):
        owned_path(root, "../sibling/sentinel.txt")
    assert sentinel.read_text(encoding="utf-8") == "synthetic sentinel\n"


def test_owned_root_and_paths_reject_synthetic_symlinks_when_supported(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "owned"
    root.mkdir()
    (root / ".idevgen-e2e-owner").write_text("synthetic-token\n", encoding="utf-8")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    sentinel = sibling / "sentinel.txt"
    sentinel.write_text("synthetic sentinel\n", encoding="utf-8")

    root_link = parent / "linked-root"
    target_link = root / "linked-sibling"
    try:
        root_link.symlink_to(root, target_is_directory=True)
        target_link.symlink_to(sibling, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        target_link.unlink(missing_ok=True)
        root_link.unlink(missing_ok=True)
        pytest.skip(f"Directory symlinks are unavailable in this environment: {error}")

    try:
        with pytest.raises(RuntimeError, match="symbolic link|reparse point"):
            validate_owned_root(root_link, parent, "synthetic-token")
        with pytest.raises(RuntimeError, match="symbolic link|reparse point"):
            owned_path(root, "linked-sibling/sentinel.txt")
        assert sentinel.read_text(encoding="utf-8") == "synthetic sentinel\n"
    finally:
        target_link.unlink(missing_ok=True)
        root_link.unlink(missing_ok=True)
