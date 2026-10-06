"""Run an isolated Task 10.4 browser demo with no assistant or text-only vision."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from urllib.request import urlopen

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.task10_3_browser_demo import SYNTHETIC_PNG, _FakeComfy, _graph


BACKEND_HOST = "127.0.0.1"
DEFAULT_PORT = 8784


def _arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("manual", "text-only"),
        default="manual",
        help="manual has no assistant; text-only configures text but no vision model",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="loopback port; the demo exits without binding if it is occupied",
    )
    return parser.parse_args(argv)


def _require_free_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise RuntimeError("Choose a TCP port from 1 through 65535.")
    with socket.socket() as probe:
        try:
            probe.bind((BACKEND_HOST, port))
        except OSError as exc:
            raise RuntimeError(
                f"Loopback port {port} is already in use. Leave that service alone, "
                "then retry this demo with a different --port."
            ) from exc


def _write_manual_source(path: Path) -> None:
    source = {
        "library": "task104_manual_rooms",
        "items": [{
            "id": "scene-001",
            "label": "Invented portrait studio",
            "scene_theme": "Soft daylight enters a quiet portrait studio.",
        }],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(source, indent=2), encoding="utf-8")


def _configuration(profile: str, data_dir: Path, output_dir: Path) -> dict:
    if profile == "manual":
        llm_url = ""
        llm_model = ""
    else:
        llm_url = "http://text-only-assistant.invalid/v1"
        llm_model = "task10-4-text-only"
    return {
        "comfy_url": f"http://{BACKEND_HOST}:1",
        "comfy_output_dir": str(output_dir),
        "lora_dir": "",
        "data_dir": str(data_dir),
        "resource_planning_enabled": True,
        "llm_url": llm_url,
        "llm_model": llm_model,
        "llm_vision_model": "",
        "llm_key": "",
        "checkpoints": {},
        "room_libraries": [],
    }


def _seed(backend_main) -> None:
    from fastapi.testclient import TestClient

    with TestClient(backend_main.app) as client:
        libraries = client.get("/api/resources/libraries")
        if libraries.status_code != 200 or libraries.json():
            raise RuntimeError("The isolated demo must start without imported resource rows.")
        workflow = client.post("/api/workflows", json={
            "name": "Synthetic Task 10.4 workflow",
            "graph": _graph(),
        })
        workflow.raise_for_status()
        model = client.post("/api/models", json={
            "name": "Invented portrait character",
            "lora_name": "characters/ada.safetensors",
            "trigger": "invented portrait character",
            "base_positive": "portrait photograph",
            "base_negative": "blur",
            "workflow_id": workflow.json()["id"],
            "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
        })
        model.raise_for_status()
        for path in ("/api/models", "/api/workflows"):
            response = client.get(path)
            if response.status_code != 200:
                raise RuntimeError(f"Synthetic demo seed readback failed for {path}.")


def main_cli(argv=None) -> int:
    args = _arguments(argv)
    _require_free_port(args.port)

    previous_env = {
        name: os.environ.get(name)
        for name in ("IDEVGEN_CONFIG", "IDEVGEN_DATA_DIR")
    }
    temporary = tempfile.TemporaryDirectory(prefix="idevgen-task10-4-demo-")
    server = None
    server_thread = None
    backend_main = None
    database = None
    original_async_client = None
    blocked_requests = []
    try:
        root = Path(temporary.name)
        data_dir = root / "data"
        output_dir = root / "comfy-output"
        output_dir.mkdir()
        config_path = root / "config.json"
        config_path.write_text(
            json.dumps(_configuration(args.profile, data_dir, output_dir), indent=2),
            encoding="utf-8",
        )
        source_path = root / "generated-files" / "invented_rooms.json"
        photo_path = root / "generated-files" / "invented_look.png"
        _write_manual_source(source_path)
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        photo_path.write_bytes(SYNTHETIC_PNG)

        os.environ["IDEVGEN_CONFIG"] = str(config_path)
        os.environ["IDEVGEN_DATA_DIR"] = str(data_dir)
        sys.path.insert(0, str(ROOT / "backend"))
        import db
        import main as backend_main
        import uvicorn
        from runner import Runner

        database = db

        def reject_assistant_request(request):
            blocked_requests.append(f"{request.method} {request.url.scheme}://{request.url.host}")
            raise RuntimeError("The isolated Task 10.4 demo blocked an assistant network request.")

        original_async_client = backend_main.enhance.httpx.AsyncClient

        class NoOutboundAsyncClient(original_async_client):
            def __init__(self, *client_args, **kwargs):
                if kwargs.get("transport") is not None:
                    raise RuntimeError("The isolated demo refuses external HTTP transports.")
                kwargs["transport"] = httpx.MockTransport(reject_assistant_request)
                super().__init__(*client_args, **kwargs)

        backend_main.enhance.httpx.AsyncClient = NoOutboundAsyncClient
        _seed(backend_main)
        db.conn().close()
        db._conn = None
        fake_comfy = _FakeComfy(output_dir)
        backend_main.comfy = fake_comfy
        backend_main.runner = Runner(fake_comfy, backend_main.SESSIONS_DIR, output_dir)

        server = uvicorn.Server(uvicorn.Config(
            backend_main.app,
            host=BACKEND_HOST,
            port=args.port,
            log_level="warning",
            access_log=False,
        ))
        server_thread = threading.Thread(
            target=server.run,
            name="task10-4-demo-backend",
            daemon=True,
        )
        server_thread.start()
        for _ in range(100):
            if server.started:
                break
            if not server_thread.is_alive():
                raise RuntimeError("The isolated backend stopped before it became ready.")
            time.sleep(0.05)
        else:
            raise RuntimeError("The isolated backend did not become ready in time.")

        url = f"http://{BACKEND_HOST}:{args.port}"
        for _ in range(100):
            if not server_thread.is_alive():
                raise RuntimeError("The isolated backend stopped before health readback.")
            try:
                with urlopen(f"{url}/api/config", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("The isolated backend did not pass its health readback.")

        print(f"Task 10.4 {args.profile} browser demo is ready at {url}.", flush=True)
        if args.profile == "manual":
            print(f"Open {url}/#/resources and select this generated source file: {source_path}", flush=True)
        else:
            print(f"Open {url}/#/looks and select this generated image file: {photo_path}", flush=True)
        print("Only temporary config, database, staged bytes, and synthetic output are used.", flush=True)
        print("Unexpected assistant requests are rejected by an in-process HTTPX transport.", flush=True)
        print("Press Ctrl+C to stop the demo; temporary data is removed after the backend stops.", flush=True)
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("Stopping the isolated Task 10.4 demo.", flush=True)
    finally:
        backend_stopped = server_thread is None
        try:
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=15)
                backend_stopped = not server_thread.is_alive()
        finally:
            if original_async_client is not None and backend_main is not None:
                backend_main.enhance.httpx.AsyncClient = original_async_client
            try:
                if backend_stopped and database is not None and database._conn is not None:
                    database.conn().close()
                    database._conn = None
            finally:
                if backend_stopped:
                    temporary.cleanup()
                else:
                    temporary._finalizer.detach()
                    print("Backend shutdown timed out; temporary data was preserved.", flush=True)
                for name, value in previous_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value

        print(f"Blocked assistant requests: {len(blocked_requests)}", flush=True)
    return 1 if blocked_requests else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main_cli())
    except RuntimeError as exc:
        raise SystemExit(str(exc))
