"""Run an isolated Task 10.5 compatibility demo for the rendered browser UI."""
from __future__ import annotations

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

from scripts.task10_3_browser_demo import _FakeComfy, _graph


BACKEND_HOST = "127.0.0.1"
DEFAULT_PORT = 8785


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


def _configuration(data_dir: Path, output_dir: Path) -> dict:
    return {
        "comfy_url": f"http://{BACKEND_HOST}:1",
        "comfy_output_dir": str(output_dir),
        "lora_dir": "",
        "data_dir": str(data_dir),
        "resource_planning_enabled": True,
        "llm_url": "",
        "llm_model": "",
        "llm_vision_model": "",
        "llm_key": "",
        "checkpoints": {},
        "room_libraries": [],
    }


def _write_sources(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    shared = {
        "id": "portrait-room-001",
        "label": "Invented portrait room",
        "scene_theme": "Soft daylight falls across a quiet portrait studio.",
    }
    sources = {
        "historical": {
            "library": "task105_historical_rooms",
            "items": [shared],
        },
        "rekeyed": {
            "library": "task105_new_declared_key",
            "items": [shared],
        },
        "updated": {
            "library": "task105_historical_rooms",
            "items": [{
                **shared,
                "label": "Updated portrait room",
                "scene_theme": "Warm afternoon light falls across the portrait studio.",
            }],
        },
        "items_only": {
            "items": [
                {"id": "items-only-001", "label": "Invented window light", "scene_theme": "Cool morning light at a studio window."},
                {"id": "items-only-002", "label": "Invented warm backdrop", "scene_theme": "A warm paper backdrop in a small portrait studio."},
            ],
        },
        "duplicates_a": [
            {"id": "unique-a", "label": "Unique A", "scene_theme": "A small studio with pale curtains."},
            {"id": "duplicate-same", "label": "Shared wording", "scene_theme": "A bright studio."},
            {"id": "duplicate-different", "label": "Original wording", "scene_theme": "A blue backdrop."},
        ],
        "duplicates_b": [
            {"id": "unique-b", "label": "Unique B", "scene_theme": "A studio with warm paper."},
            {"id": "duplicate-same", "label": "Shared wording", "scene_theme": "A bright studio."},
            {"id": "duplicate-different", "label": "Changed wording", "scene_theme": "A green backdrop."},
        ],
    }
    names = {
        "historical": "historical_rooms.json",
        "rekeyed": "different_declared_key.json",
        "updated": "updated_historical_room.json",
        "items_only": "items_only_rooms.json",
        "duplicates_a": "same_library_a.json",
        "duplicates_b": "same_library_b.json",
    }
    paths = {}
    for key, source in sources.items():
        path = folder / names[key]
        path.write_text(json.dumps(source, indent=2), encoding="utf-8")
        paths[key] = path
    return paths


def _seed(backend_main) -> None:
    from fastapi.testclient import TestClient
    from backend import resource_store

    with TestClient(backend_main.app) as client:
        libraries = client.get("/api/resources/libraries")
        if libraries.status_code != 200 or libraries.json():
            raise RuntimeError("The isolated demo must start without imported resource rows.")
        workflow = client.post("/api/workflows", json={
            "name": "Synthetic Task 10.5 workflow",
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

        library_key = "task105_fused_scenes"
        source_id = "fused-scene-001"
        prompt = "A woman wearing a red dress stands beside a tall studio window."
        library_id = resource_store.ensure_library(
            library_key,
            display_name="Task 10.5 fused demo",
            kind="fused_scenes",
        )
        revision_id = resource_store.record_revision(
            library_id,
            source_id,
            {"id": source_id, "prompt": prompt},
            translation={"prompt": prompt},
        )
        library = client.get(f"/api/resources/libraries/{library_key}")
        if (
            library.status_code != 200
            or library.json()["revisions"][0]["readiness"]["status"] != "ready"
            or not revision_id
        ):
            raise RuntimeError("Synthetic fused resource did not become ready for the advanced editor.")


def main_cli(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="loopback port; the demo exits without binding if it is occupied",
    )
    args = parser.parse_args(argv)
    _require_free_port(args.port)

    previous_env = {
        name: os.environ.get(name)
        for name in ("IDEVGEN_CONFIG", "IDEVGEN_DATA_DIR")
    }
    temporary = tempfile.TemporaryDirectory(prefix="idevgen-task10-5-demo-")
    server = None
    server_thread = None
    backend_main = None
    database = None
    original_async_client = None
    blocked_requests: list[str] = []
    try:
        root = Path(temporary.name)
        data_dir = root / "data"
        output_dir = root / "comfy-output"
        output_dir.mkdir()
        config_path = root / "config.json"
        config_path.write_text(
            json.dumps(_configuration(data_dir, output_dir), indent=2),
            encoding="utf-8",
        )
        source_paths = _write_sources(root / "generated-files")

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
            raise RuntimeError("The isolated Task 10.5 demo blocked an assistant network request.")

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
            name="task10-5-demo-backend",
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

        print(f"Task 10.5 browser demo is ready at {url}#/resources.", flush=True)
        print("Select the Invented portrait character before using the fused advanced editor.", flush=True)
        for key, path in source_paths.items():
            print(f"{key}: {path}", flush=True)
        print("Only temporary configuration, SQLite data, staging, and synthetic outputs are used.", flush=True)
        print("Assistant requests are rejected; FakeComfy is installed and no generation is part of the walkthrough.", flush=True)
        print("Press Ctrl+C to stop the demo; temporary data is removed after the backend stops.", flush=True)
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("Stopping the isolated Task 10.5 demo.", flush=True)
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
