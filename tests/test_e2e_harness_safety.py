import socket
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.e2e_harness.server import BackendEgressGuard, _bind_listener, owned_path, validate_owned_root


def test_e2e_owned_paths_reject_synthetic_outside_sentinel(tmp_path):
    root = tmp_path / "owned-root"
    root.mkdir()
    (root / ".idevgen-e2e-owner").write_text("synthetic-token\n", encoding="utf-8")
    outside = tmp_path / "outside-root"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("synthetic outside-root sentinel\n", encoding="utf-8")

    assert validate_owned_root(root, tmp_path, "synthetic-token") == root
    with pytest.raises(RuntimeError, match="escapes"):
        owned_path(root, "../outside-root/sentinel.txt")
    assert sentinel.read_text(encoding="utf-8") == "synthetic outside-root sentinel\n"


def test_e2e_socket_guard_blocks_raw_connections_including_loopback(monkeypatch):
    names = (
        "getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo",
        "create_connection",
    )
    original_functions = {name: getattr(socket, name) for name in names}
    method_names = ["connect", "connect_ex", "sendto"]
    if hasattr(socket.socket, "sendmsg"):
        method_names.append("sendmsg")
    original_methods = {name: getattr(socket.socket, name) for name in method_names}
    for name, original in original_functions.items():
        monkeypatch.setattr(socket, name, original)
    for name, original in original_methods.items():
        monkeypatch.setattr(socket.socket, name, original)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.05)
    guard = BackendEgressGuard()
    guard.install()
    try:
        with pytest.raises(PermissionError, match="DNS lookup"):
            socket.getaddrinfo("synthetic-comfy.invalid", 80)
        with pytest.raises(PermissionError, match="reverse DNS lookup"):
            socket.getnameinfo(("127.0.0.1", 80), 0)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            with pytest.raises(PermissionError, match="socket connect"):
                client.connect(("127.0.0.1", listener.getsockname()[1]))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            with pytest.raises(PermissionError, match="socket connect_ex"):
                client.connect_ex(listener.getsockname())
        with pytest.raises(PermissionError, match="socket create_connection"):
            socket.create_connection(listener.getsockname())
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            with pytest.raises(PermissionError, match="socket sendto"):
                client.sendto(b"synthetic probe", ("127.0.0.1", listener.getsockname()[1]))
            if hasattr(client, "sendmsg"):
                with pytest.raises(PermissionError, match="socket sendmsg"):
                    client.sendmsg([b"synthetic probe"], [], 0, ("127.0.0.1", listener.getsockname()[1]))
        with pytest.raises(TimeoutError):
            listener.accept()
    finally:
        listener.close()

    expected = ["DNS lookup", "reverse DNS lookup", "socket connect", "socket connect_ex", "socket create_connection", "socket sendto"]
    if "sendmsg" in method_names:
        expected.append("socket sendmsg")
    assert [item["kind"] for item in guard.denials] == expected


def test_e2e_occupied_port_bind_fails_without_contacting_listener():
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        with pytest.raises(RuntimeError, match="bind failed"):
            _bind_listener(occupied.getsockname()[1])
        occupied.settimeout(0.05)
        with pytest.raises(TimeoutError):
            occupied.accept()
    finally:
        occupied.close()


def test_e2e_process_refuses_occupied_port_without_contacting_listener(tmp_path):
    root = tmp_path / "synthetic-owned-root"
    root.mkdir()
    (root / ".idevgen-e2e-owner").write_text("synthetic-token\n", encoding="utf-8")
    occupied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts" / "e2e_harness" / "server.py"),
                "--root", str(root),
                "--root-token", "synthetic-token",
                "--parent", str(tmp_path),
                "--nonce", "synthetic-nonce",
                "--port", str(occupied.getsockname()[1]),
                "--workers", "1",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 1
        assert "loopback bind failed" in result.stderr
        occupied.settimeout(0.05)
        with pytest.raises(TimeoutError):
            occupied.accept()
    finally:
        occupied.close()
