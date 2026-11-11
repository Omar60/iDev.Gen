"""Run an isolated synthetic FastAPI process for Playwright E2E tests."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from runpy import run_path
import secrets
import signal
import socket
import stat
import sys
import threading
import time
from urllib.parse import parse_qs

import httpx
from fastapi.responses import JSONResponse


HOST = "127.0.0.1"
OWNER_FILE = ".idevgen-e2e-owner"
IDENTITY_PATH = "/__e2e/identity"
REDIRECT_PATH = "/__e2e/redirect"
SAFE_API_PATHS = {
    "/api/components",
    "/api/config",
    "/api/comfy/loras",
    "/api/comfy/models",
    "/api/comfy/status",
    "/api/models",
    "/api/sessions",
    "/api/wardrobe",
    "/api/workflows",
}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_reparse_point(path: Path) -> bool:
    value = path.lstat()
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def validate_owned_root(root: Path, parent: Path, token: str) -> Path:
    parent = parent.resolve(strict=True)
    supplied_root = root.absolute()
    if _is_reparse_point(supplied_root):
        raise RuntimeError("E2E root cannot be a symbolic link or reparse point.")
    root = supplied_root.resolve(strict=True)
    if root.parent != parent or supplied_root.parent.resolve(strict=True) != parent:
        raise RuntimeError("E2E root must be an unlinked direct child of its owned temporary parent.")
    marker = root / OWNER_FILE
    try:
        marker_stat = marker.lstat()
        marker_valid = not _is_reparse_point(marker) and stat.S_ISREG(marker_stat.st_mode)
        marker_matches = marker_valid and marker.read_text(encoding="utf-8") == f"{token}\n"
    except OSError:
        marker_matches = False
    if not marker_matches:
        raise RuntimeError("E2E root owner marker is missing or mismatched.")
    return root


def owned_path(root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute() or not relative:
        raise RuntimeError("E2E path must stay relative to the owned temporary root.")
    if ".." in relative_path.parts:
        raise RuntimeError("E2E path must stay relative to the owned temporary root; it escapes the owned root.")
    root = root.resolve(strict=True)
    candidate = root / relative_path
    current = root
    for part in relative_path.parts:
        current /= part
        if current.exists() or current.is_symlink():
            if _is_reparse_point(current):
                raise RuntimeError("E2E path crosses a symbolic link or reparse point.")
        else:
            break
    if not candidate.resolve(strict=False).is_relative_to(root):
        raise RuntimeError("E2E path escapes the owned temporary root.")
    return candidate


def _path_state(root: Path, candidate: Path) -> bool:
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        return True
    except (OSError, ValueError):
        return False


class BackendEgressGuard:
    """Refuse raw application network operations before they can reach the OS."""

    def __init__(self):
        self.denials: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self._originals = {}

    def _deny(self, kind: str, target: object) -> PermissionError:
        rendered = str(target)[:160]
        with self._lock:
            self.denials.append({"kind": kind, "target": rendered})
        return PermissionError(f"E2E network policy blocked {kind}: {rendered}")

    def _getaddrinfo(self, host, port, *args, **kwargs):
        raise self._deny("DNS lookup", host)

    def _gethostbyname(self, host):
        raise self._deny("DNS lookup", host)

    def _gethostbyname_ex(self, host):
        raise self._deny("DNS lookup", host)

    def _gethostbyaddr(self, host):
        raise self._deny("reverse DNS lookup", host)

    def _getnameinfo(self, address, flags):
        raise self._deny("reverse DNS lookup", address)

    def _connect(self, sock, address):
        raise self._deny("socket connect", address)

    def _connect_ex(self, sock, address):
        raise self._deny("socket connect_ex", address)

    def _create_connection(self, address, *args, **kwargs):
        raise self._deny("socket create_connection", address)

    def _sendto(self, sock, data, *args, **kwargs):
        raise self._deny("socket sendto", args[-1] if args else kwargs.get("address", "unknown address"))

    def _sendmsg(self, sock, buffers, *args, **kwargs):
        raise self._deny("socket sendmsg", args[-1] if args else kwargs.get("address", "unknown address"))

    def install(self):
        self._originals = {
            "getaddrinfo": socket.getaddrinfo,
            "gethostbyname": socket.gethostbyname,
            "gethostbyname_ex": socket.gethostbyname_ex,
            "gethostbyaddr": socket.gethostbyaddr,
            "getnameinfo": socket.getnameinfo,
            "connect": socket.socket.connect,
            "connect_ex": socket.socket.connect_ex,
            "create_connection": socket.create_connection,
        }
        socket.getaddrinfo = self._getaddrinfo
        socket.gethostbyname = self._gethostbyname
        socket.gethostbyname_ex = self._gethostbyname_ex
        socket.gethostbyaddr = self._gethostbyaddr
        socket.getnameinfo = self._getnameinfo
        socket.socket.connect = lambda sock, address: self._connect(sock, address)
        socket.socket.connect_ex = lambda sock, address: self._connect_ex(sock, address)
        socket.create_connection = self._create_connection
        self._originals["sendto"] = socket.socket.sendto
        socket.socket.sendto = lambda sock, data, *args, **kwargs: self._sendto(sock, data, *args, **kwargs)
        if hasattr(socket.socket, "sendmsg"):
            self._originals["sendmsg"] = socket.socket.sendmsg
            socket.socket.sendmsg = lambda sock, buffers, *args, **kwargs: self._sendmsg(sock, buffers, *args, **kwargs)


class SyntheticTransport:
    """HTTPX provider double; every requested host must be synthetic."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.requests: list[dict[str, str]] = []
        self.delegate = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"method": request.method, "host": request.url.host, "path": request.url.path})
        if request.url.host not in {"synthetic-assistant.invalid", "synthetic-comfy.invalid"}:
            raise RuntimeError(f"E2E HTTPX transport blocked unexpected host: {request.url.host}")
        return self.delegate(request)


def isolated_sync_client_factory(handler, original):
    def create_client(*args, **kwargs):
        if kwargs.get("transport") is not None:
            raise RuntimeError("The E2E harness owns all outbound HTTPX transports.")
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)
    return create_client


class HarnessIsolationMiddleware:
    def __init__(self, app, root: Path, denials: list[dict[str, str]], model_fixture=None, session_flow_fixture=None):
        self.app = app
        self.root = root
        self.denials = denials
        self.model_fixture = model_fixture
        self.session_flow_fixture = session_flow_fixture

    @staticmethod
    def _query_allowed(path: str, query: bytes) -> bool:
        values = parse_qs(query.decode("ascii", errors="ignore"), keep_blank_values=True)
        if path == "/api/components":
            return not values or values == {"all": ["1"]}
        return not values

    @staticmethod
    def _redirect_target(query: bytes) -> str | None:
        values = parse_qs(query.decode("ascii", errors="ignore"), keep_blank_values=True)
        if set(values) != {"port"} or len(values["port"]) != 1:
            return None
        try:
            port = int(values["port"][0])
        except ValueError:
            return None
        return f"http://127.0.0.1:{port}/e2e-redirect-probe" if 1 <= port <= 65535 else None

    @staticmethod
    def _safe_static_path(path: str) -> bool:
        if path in {"/", "/index.html"}:
            return True
        if not path.startswith("/assets/") or "\\" in path:
            return False
        return all(part not in {".", ".."} for part in Path(path).parts)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        query = scope.get("query_string", b"")
        allowed = (
            self.model_fixture is not None and self.model_fixture.allows(method, path, query)
        ) or (
            self.session_flow_fixture is not None and self.session_flow_fixture.allows(method, path, query)
        ) or (
            path == IDENTITY_PATH and method == "GET" and not query
        ) or (
            path == REDIRECT_PATH and method == "GET" and self._redirect_target(query) is not None
        ) or (
            method in SAFE_METHODS
            and path in SAFE_API_PATHS
            and self._query_allowed(path, query)
        ) or (
            method in SAFE_METHODS and self._safe_static_path(path) and not query
        )
        if allowed:
            if method == "PATCH" and self.model_fixture is not None and self.model_fixture.allows(method, path, query):
                await self.model_fixture.record_patch(self.app, scope, receive, send)
                return
            if self.session_flow_fixture is not None and self.session_flow_fixture.is_runtime_path(method, path):
                await self.session_flow_fixture.record_request(self.app, scope, receive, send, self.denials)
                return
            if path == REDIRECT_PATH:
                response = JSONResponse({}, status_code=302, headers={"Location": self._redirect_target(query)})
                await response(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        self.denials.append({"method": method, "path": path})
        response = JSONResponse({"detail": "Blocked by the isolated E2E harness."}, status_code=403)
        await response(scope, receive, send)


def _bind_listener(port: int) -> socket.socket:
    if not 0 <= port <= 65535:
        raise RuntimeError("Choose TCP port 0 or a port from 1 through 65535.")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((HOST, port))
        listener.listen(128)
        return listener
    except OSError as exc:
        listener.close()
        raise RuntimeError("E2E loopback bind failed; the requested socket was not probed or reused.") from exc


def _write_synthetic_seed(root: Path) -> Path:
    config_path = owned_path(root, "config.json")
    data_dir = owned_path(root, "data")
    output_dir = owned_path(root, "output")
    lora_dir = owned_path(root, "lora")
    expected_config = Path(os.environ.get("IDEVGEN_CONFIG", "")).resolve(strict=True)
    expected_data = Path(os.environ.get("IDEVGEN_DATA_DIR", "")).resolve(strict=True)
    if expected_config != config_path.resolve(strict=True) or expected_data != data_dir.resolve(strict=True):
        raise RuntimeError("E2E config and data overrides must point into the parent-owned root.")
    for name in ("data", "data/sessions", "data/staging", "lora", "output"):
        directory = owned_path(root, name)
        if not directory.is_dir():
            raise RuntimeError(f"Synthetic E2E directory is missing: {name}")

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    backend_path = Path(__file__).resolve().parents[2] / "backend"
    repository = backend_path.parent
    if str(repository) not in sys.path:
        sys.path.insert(0, str(repository))
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))

    import db

    connection = db.connect(data_dir / "idevgen.db")
    created = datetime.now(timezone.utc).isoformat()
    model_id = db.run("INSERT INTO model (name, created_at) VALUES (?,?)", "E2E Synthetic Character", created)
    session_id = db.run(
        "INSERT INTO session (model_id, name, created_at) VALUES (?,?,?)",
        model_id, "E2E Synthetic Session", created,
    )
    session_dir = owned_path(root, f"data/sessions/{session_id}")
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "seed.marker").write_text("Synthetic session file.\n", encoding="utf-8")
    connection.close()
    db._conn = None
    return data_dir


def _identity(root: Path, token: str, nonce: str, port: int, backend_main, egress: BackendEgressGuard, providers: SyntheticTransport, request_denials: list[dict[str, str]]) -> dict:
    database_file = Path(backend_main.db.conn().execute("PRAGMA database_list").fetchone()["file"]).resolve(strict=True)
    file_paths = {
        "config": _path_state(root, backend_main.CONFIG_PATH),
        "data": _path_state(root, backend_main.DATA_DIR),
        "sessions": _path_state(root, backend_main.SESSIONS_DIR),
        "lora": _path_state(root, backend_main.LORA_DIR),
        "output": _path_state(root, backend_main.COMFY_OUTPUT),
        "staging": _path_state(root, backend_main.DATA_DIR / "staging"),
        "session_seed": _path_state(root, backend_main.SESSIONS_DIR / "1" / "seed.marker"),
    }
    return {
        "server_pid": os.getpid(),
        "port": port,
        "server_nonce": nonce,
        "root_id": token,
        "database_path_owned": _path_state(root, database_file) and database_file.name == "idevgen.db",
        "seeded_models": int(backend_main.db.one("SELECT COUNT(*) AS n FROM model")["n"]),
        "seeded_sessions": int(backend_main.db.one("SELECT COUNT(*) AS n FROM session")["n"]),
        "file_paths_owned": file_paths,
        "backend_socket_denials": list(egress.denials),
        "synthetic_httpx_requests": list(providers.requests),
        "request_denials": list(request_denials),
    }


def _arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--root-token", required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    return parser.parse_args(argv)


def owned_event_loop():
    # This single-worker harness uses sockets and a blocking stdin thread, not
    # asyncio pipes/subprocesses. Keep Windows Proactor shutdown out of this path.
    return asyncio.SelectorEventLoop() if os.name == "nt" else asyncio.new_event_loop()


async def run_owned_server(server, listener, stop_requested, on_ready, *, startup_timeout=60, shutdown_timeout=10):
    task = asyncio.create_task(server.serve(sockets=[listener]))
    stop_task = asyncio.create_task(stop_requested.wait())
    deadline = time.monotonic() + startup_timeout
    try:
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("E2E FastAPI exited before lifespan startup completed.")
            if stop_requested.is_set():
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("E2E FastAPI lifespan startup timed out.")
            await asyncio.sleep(0.05)
        if server.started:
            on_ready()
        done, _ = await asyncio.wait({task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if task in done and not stop_requested.is_set():
            await task
            raise RuntimeError("E2E FastAPI exited before an owned stop request.")
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=shutdown_timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("E2E FastAPI shutdown deadline exceeded; clean=false.") from exc
        print("E2E_SERVER_STOPPED clean=true", flush=True)
        return 0
    finally:
        server.should_exit = True
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        if not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=1)
        if task.done() and not task.cancelled():
            task.exception()


def main(argv=None) -> int:
    args = _arguments(argv)
    if args.workers != 1:
        raise RuntimeError("The E2E harness requires exactly one backend worker.")
    listener = _bind_listener(args.port)
    root = validate_owned_root(Path(args.root), Path(args.parent), args.root_token)
    os.environ["IDEVGEN_CONFIG"] = str(owned_path(root, "config.json"))
    os.environ["IDEVGEN_DATA_DIR"] = str(owned_path(root, "data"))
    os.environ["IDEVGEN_E2E_ROOT"] = str(root)
    os.environ["IDEVGEN_RESOURCE_PLANNING_ENABLED"] = "true"
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)
    os.environ.pop("ALL_PROXY", None)
    os.environ.pop("http_proxy", None)
    os.environ.pop("https_proxy", None)
    os.environ.pop("all_proxy", None)

    # Construct internal loop sockets before installing the egress guard.
    event_loop = owned_event_loop()
    asyncio.set_event_loop(event_loop)

    egress = BackendEgressGuard()
    egress.install()
    providers = SyntheticTransport(owned_path(root, "output"))
    _write_synthetic_seed(root)
    demo = run_path(Path(__file__).resolve().parents[1] / "task7_1_full_auto_browser_demo.py")
    providers.delegate = demo["SyntheticTransport"](providers.output_dir)

    original_async_client = httpx.AsyncClient
    original_sync_client = httpx.Client
    httpx.AsyncClient = demo["isolated_async_client_factory"](providers, original_async_client)
    httpx.Client = isolated_sync_client_factory(providers, original_sync_client)

    import main as backend_main

    if Path(backend_main.CONFIG_PATH).resolve(strict=True) != owned_path(root, "config.json").resolve(strict=True):
        raise RuntimeError("Backend config import escaped the owned E2E root.")
    expected_config_paths = (
        backend_main.DATA_DIR,
        backend_main.SESSIONS_DIR,
        backend_main.LORA_DIR,
        backend_main.COMFY_OUTPUT,
    )
    if any(not _path_state(root, path) for path in expected_config_paths):
        raise RuntimeError("A backend data, session, LoRA, or output path escaped the owned E2E root.")

    request_denials: list[dict[str, str]] = []
    from scripts.e2e_harness.model_detail import FIXTURE_PATH, ModelDetailFixture
    from scripts.e2e_harness.session_flow import FIXTURE_PATH as SESSION_FLOW_PATH, SessionFlowFixture

    model_fixture = ModelDetailFixture(backend_main, root)
    session_flow_fixture = SessionFlowFixture(backend_main, root)
    backend_main.app.add_middleware(
        HarnessIsolationMiddleware, root=root, denials=request_denials,
        model_fixture=model_fixture, session_flow_fixture=session_flow_fixture,
    )
    identity = lambda: _identity(root, args.root_token, args.nonce, listener.getsockname()[1], backend_main, egress, providers, request_denials)

    async def read_identity(request):
        result = identity()
        if not all(result["file_paths_owned"].values()) or not result["database_path_owned"]:
            return JSONResponse({"detail": "E2E synthetic file path escaped its owned root."}, status_code=500)
        return JSONResponse(result)

    from starlette.routing import Route
    backend_main.app.router.routes.insert(0, Route(FIXTURE_PATH, model_fixture.control, methods=["GET", "POST", "DELETE"]))
    backend_main.app.router.routes.insert(0, Route(SESSION_FLOW_PATH, session_flow_fixture.control, methods=["GET", "POST", "PATCH", "DELETE"]))
    backend_main.app.router.routes.insert(0, Route(IDENTITY_PATH, read_identity, methods=["GET"]))

    original_lifespan = backend_main.app.router.lifespan_context

    @asynccontextmanager
    async def closing_lifespan(app):
        async with original_lifespan(app):
            yield
            connection = backend_main.db._conn
            if connection is not None:
                connection.close()
                backend_main.db._conn = None

    backend_main.app.router.lifespan_context = closing_lifespan

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        backend_main.app,
        host=HOST,
        port=listener.getsockname()[1],
        workers=1,
        reload=False,
        log_level="warning",
        access_log=False,
    ))
    stop_requested = asyncio.Event()

    def request_stop():
        event_loop.call_soon_threadsafe(stop_requested.set)

    def watch_parent():
        try:
            for line in sys.stdin:
                if line.strip() == "stop":
                    request_stop()
                    return
        finally:
            request_stop()

    control_thread = threading.Thread(target=watch_parent, name="idevgen-e2e-control", daemon=True)
    control_thread.start()

    old_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda _signum, _frame: request_stop())

    def ready():
        port = listener.getsockname()[1]
        print(
            f"E2E_SERVER_READY base_url=http://{HOST}:{port} pid={os.getpid()} parent_pid={os.getppid()} "
            f"port={port} nonce={args.nonce} root_id={args.root_token}",
            flush=True,
        )

    try:
        return event_loop.run_until_complete(run_owned_server(server, listener, stop_requested, ready))
    finally:
        server.should_exit = True
        if listener.fileno() >= 0:
            listener.close()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        asyncio.set_event_loop(None)
        event_loop.close()
        httpx.AsyncClient = original_async_client
        httpx.Client = original_sync_client


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"E2E_SERVER_ERROR {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
