"""Run the isolated Task 10.3 automatic journey in the built frontend."""
from __future__ import annotations

import base64
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
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8777
SYNTHETIC_ASSISTANT_URL = "http://synthetic-assistant.invalid/v1"
SYNTHETIC_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGN4c2cfAAU+AocM07TXAAAAAElFTkSuQmCC"
)


def _graph():
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "invented-base.safetensors"}},
        "2": {"class_type": "LoraLoader", "inputs": {
            "lora_name": "characters/ada.safetensors", "strength_model": 0.7,
            "strength_clip": 1.0, "model": ["1", 0], "clip": ["1", 1],
        }},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {
            "seed": 1, "steps": 8, "cfg": 1.0, "sampler_name": "euler",
            "scheduler": "normal", "denoise": 1.0, "model": ["2", 0],
            "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0],
        }},
        "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "demo", "images": ["6", 0]}},
    }


class _SyntheticAssistant:
    """Deterministic OpenAI-compatible responses for the configured demo model."""

    def __init__(self):
        self.calls = {"translation": 0, "shared": 0, "writer": 0, "interrupted": False}

    def _response(self, status, payload):
        return httpx.Response(status, json=payload)

    def __call__(self, request):
        if request.url.scheme != "http" or request.url.host != "synthetic-assistant.invalid":
            raise RuntimeError("The synthetic assistant transport refused an unexpected endpoint.")
        if request.method == "GET" and request.url.path == "/v1/models":
            return self._response(200, {"data": [{"id": "task10-3-synthetic-assistant", "object": "model"}]})
        if request.method != "POST" or request.url.path != "/v1/chat/completions":
            return self._response(404, {"error": {"message": "Unknown synthetic assistant route"}})

        body = json.loads(request.content or b"{}")
        user = next(
            (message["content"] for message in body.get("messages", []) if message.get("role") == "user"),
            "",
        )
        if "Entries:\n" in user:
            entries = json.loads(user.partition("Entries:\n")[2])
            self.calls["translation"] += 1
            output = {
                entry["key"]: (
                    "Invented portrait studio"
                    if entry["field"] == "label"
                    else "Soft daylight fills an empty portrait studio."
                )
                for entry in entries
            }
        elif "Requested fields, in order:" in user:
            marker = "Requested fields, in order:"
            fields = json.loads(user.partition(marker)[2].split("\n", 1)[0].strip())
            self.calls["shared"] += 1
            output = {
                field: (
                    "A calm portrait with soft side light."
                    if field == "look"
                    else "A cream cotton shirt and dark trousers."
                )
                for field in fields
            }
        else:
            if "Context: " not in user:
                raise RuntimeError("The synthetic take request omitted its server-owned context.")
            context = json.loads(user.partition("Context: ")[2])
            requested = context["preparation"]["requested_fields"]
            self.calls["writer"] += 1
            if context["take_id"] == "take-004" and not self.calls["interrupted"]:
                self.calls["interrupted"] = True
                return self._response(503, {"error": {"message": "Synthetic interruption after three saved takes"}})
            output = {
                field: f"Synthetic {field}, variation {self.calls['writer']}"
                for field in requested
            }

        return self._response(200, {
            "id": f"task10-3-completion-{self.calls['writer'] + self.calls['shared'] + self.calls['translation']}",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(output)},
                "finish_reason": "stop",
            }],
        })


def _synthetic_async_client(httpx_module=httpx):
    """Build an AsyncClient that keeps this fake provider in-process."""
    assistant = _SyntheticAssistant()
    original = httpx_module.AsyncClient

    class SyntheticAsyncClient(original):
        def __init__(self, *args, **kwargs):
            if kwargs.get("transport") is not None:
                raise RuntimeError("The synthetic assistant client rejects external transports.")
            super().__init__(*args, transport=httpx_module.MockTransport(assistant), **kwargs)

    return assistant.calls, SyntheticAsyncClient


class _FakeComfy:
    """Local runner double that writes tiny synthetic PNGs to temporary storage."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.url = "http://127.0.0.1:1"
        self.graphs = {}
        self.attempts = 0

    async def stats(self):
        return {"system": {"comfyui_version": "synthetic"}, "devices": [{"name": "Synthetic device"}]}

    async def queue_prompt(self, graph: dict, _client_id: str) -> str:
        self.attempts += 1
        prompt_id = f"task103-prompt-{self.attempts}"
        self.graphs[prompt_id] = graph
        return prompt_id

    async def history(self, prompt_id: str) -> dict:
        prefix = self.graphs[prompt_id]["8"]["inputs"]["filename_prefix"]
        subfolder, _, stem = prefix.rpartition("/")
        filename = f"{stem}_00001_.png"
        folder = self.output_dir / subfolder
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(SYNTHETIC_PNG)
        return {
            "status": {"completed": True},
            "outputs": {"8": {"images": [{
                "filename": filename, "subfolder": subfolder, "type": "output",
            }]}},
        }

    async def interrupt(self):
        return None


def _require_free_backend_port():
    with socket.socket() as probe:
        try:
            probe.bind((BACKEND_HOST, BACKEND_PORT))
        except OSError as exc:
            raise RuntimeError(
                f"Backend port {BACKEND_PORT} is already in use. Leave that service alone, "
                "then rerun this demo after it has stopped."
            ) from exc


def _write_generated_source(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "library": "task103_synthetic_rooms",
        "items": [{
            "id": "scene-001",
            "label": "\u67b6\u7a7a\u306e\u80d6\u50cf\u30b9\u30bf\u30b8\u30aa",
            "scene_theme": "\u67d4\u3089\u304b\u306a\u5149\u304c\u9ad8\u3044\u7a93\u304b\u3089\u9759\u304b\u306a\u30b9\u30bf\u30b8\u30aa\u306b\u5dee\u3057\u8fbc\u3080\u3002",
        }],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _seed(main):
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        libraries = client.get("/api/resources/libraries")
        if libraries.status_code != 200 or libraries.json():
            raise RuntimeError("The isolated demo must start without imported resource rows.")
        workflow = client.post("/api/workflows", json={
            "name": "Synthetic Task 10.3 workflow", "graph": _graph(),
        })
        workflow.raise_for_status()
        model = client.post("/api/models", json={
            "name": "Invented Ada model",
            "lora_name": "characters/ada.safetensors",
            "trigger": "4da woman",
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


def main_cli():
    _require_free_backend_port()
    previous_env = {name: os.environ.get(name) for name in ("IDEVGEN_CONFIG", "IDEVGEN_DATA_DIR")}
    server = None
    server_thread = None
    original_async_client = None
    backend_main = None
    temporary = tempfile.TemporaryDirectory(prefix="idevgen-task10-3-demo-")
    try:
        root = Path(temporary.name)
        data_dir = root / "data"
        output_dir = root / "comfy-output"
        output_dir.mkdir()
        source_file = root / "generated-files" / "invented_rooms.json"
        _write_generated_source(source_file)
        config_path = root / "config.json"
        config_path.write_text(json.dumps({
            "comfy_url": f"http://{BACKEND_HOST}:1",
            "comfy_output_dir": str(output_dir),
            "lora_dir": "",
            "data_dir": str(data_dir),
            "resource_planning_enabled": True,
            "llm_url": SYNTHETIC_ASSISTANT_URL,
            "llm_model": "task10-3-synthetic-assistant",
            "llm_vision_model": "",
            "llm_key": "",
            "checkpoints": {},
            "room_libraries": [],
        }, indent=2), encoding="utf-8")
        os.environ["IDEVGEN_CONFIG"] = str(config_path)
        os.environ["IDEVGEN_DATA_DIR"] = str(data_dir)
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / "backend"))
        import db
        import main as backend_main
        import uvicorn
        from runner import Runner

        _, synthetic_client = _synthetic_async_client(backend_main.enhance.httpx)
        original_async_client = backend_main.enhance.httpx.AsyncClient
        backend_main.enhance.httpx.AsyncClient = synthetic_client
        _seed(backend_main)
        db.conn().close()
        db._conn = None
        fake_comfy = _FakeComfy(output_dir)
        backend_main.comfy = fake_comfy
        backend_main.runner = Runner(fake_comfy, backend_main.SESSIONS_DIR, output_dir)

        server = uvicorn.Server(uvicorn.Config(
            backend_main.app, host=BACKEND_HOST, port=BACKEND_PORT,
            log_level="warning", access_log=False,
        ))
        server_thread = threading.Thread(target=server.run, name="task10-3-demo-backend", daemon=True)
        server_thread.start()
        for _ in range(100):
            if server.started:
                break
            if not server_thread.is_alive():
                raise RuntimeError("The isolated backend stopped before it became ready.")
            time.sleep(0.05)
        else:
            raise RuntimeError("The isolated backend did not become ready in time.")
        for _ in range(100):
            if not server_thread.is_alive():
                raise RuntimeError("The isolated backend stopped before health readback.")
            try:
                with urlopen(f"http://{BACKEND_HOST}:{BACKEND_PORT}/api/config", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("The isolated backend did not pass its health readback.")

        print("Task 10.3 isolated browser demo is ready.", flush=True)
        print("Open http://127.0.0.1:8777/#/resources and follow docs/task-10-3-browser-walkthrough.md.", flush=True)
        print(f"In the native file picker, select the generated source file: {source_file}", flush=True)
        print("Preparation saves takes 001-003, fails one in-process assistant request for take-004, then Resume completes the remaining takes.", flush=True)
        print("Press Ctrl+C to stop this demo. Its config, database, upload staging and synthetic photos are temporary.", flush=True)
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("Stopping the isolated Task 10.3 demo.")
    finally:
        backend_stopped = server_thread is None
        try:
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=15)
                backend_stopped = not server_thread.is_alive()
        finally:
            if original_async_client is not None:
                backend_main.enhance.httpx.AsyncClient = original_async_client
            try:
                if backend_stopped and "db" in locals() and db._conn is not None:
                    db.conn().close()
                    db._conn = None
            finally:
                if backend_stopped:
                    temporary.cleanup()
                else:
                    temporary._finalizer.detach()
                    print("Backend shutdown timed out; temporary data was preserved.")
                for name, value in previous_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
        if backend_stopped:
            print("Temporary demo data removal completed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main_cli())
    except RuntimeError as exc:
        raise SystemExit(str(exc))
