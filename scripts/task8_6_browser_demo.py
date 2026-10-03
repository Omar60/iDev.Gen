"""Run a disposable backend for the Task 8.6 browser walkthrough.

The demo seeds invented resources in a temporary SQLite database and serves a
deterministic OpenAI-compatible fake assistant on loopback. It refuses to start
if the frontend's configured backend port is already in use.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8777


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


class _AssistantHandler(BaseHTTPRequestHandler):
    calls = 0
    lock = threading.Lock()

    def log_message(self, _format, *_args):
        return

    def _send(self, status, payload):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/v1/models":
            self._send(200, {"data": [{"id": "task86-synthetic-assistant", "object": "model"}]})
            return
        self._send(404, {"error": {"message": "Unknown synthetic assistant route"}})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self._send(404, {"error": {"message": "Unknown synthetic assistant route"}})
            return
        length = int(self.headers.get("Content-Length", "0"))
        json.loads(self.rfile.read(length) or b"{}")
        with self.lock:
            type(self).calls += 1
            call_number = type(self).calls
        time.sleep(2.0 if call_number == 1 else 0.8)
        if call_number == 2:
            self._send(503, {"error": {"message": "Synthetic partial failure; resume is available"}})
            return
        content = json.dumps({
            "camera": "50mm eye-level",
            "framing": "medium portrait",
            "pose": "standing beside a studio window",
            "expression": "calm gaze",
        })
        self._send(200, {
            "id": f"task86-completion-{call_number}",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        })


def _require_free_backend_port(host=BACKEND_HOST, port=BACKEND_PORT):
    with socket.socket() as probe:
        try:
            probe.bind((host, port))
        except OSError as exc:
            raise RuntimeError(
                f"Backend port {port} is already in use. Leave that service alone, "
                "then rerun this demo after it has stopped."
            ) from exc


def _seed(main):
    from fastapi.testclient import TestClient
    import resource_store

    with TestClient(main.app) as client:
        library_id = resource_store.ensure_library("task86_synthetic_rooms", kind="rooms")
        revision_id = resource_store.record_revision(
            library_id,
            "studio-001",
            {"id": "studio-001", "label": "Invented studio", "scene_theme": "Soft daylight in a quiet studio."},
            translation={"label": "Invented studio", "scene_theme": "Soft daylight in a quiet studio."},
        )
        revision = resource_store.get_revision(revision_id=revision_id)
        anchor = {
            "library_key": "task86_synthetic_rooms",
            "source_id": "studio-001",
            "content_digest": revision["content_digest"],
        }
        workflow_response = client.post("/api/workflows", json={
            "name": "Synthetic task 8.6 workflow", "graph": _graph(),
        })
        workflow_response.raise_for_status()
        model_response = client.post("/api/models", json={
            "name": "Invented Ada model",
            "lora_name": "characters/ada.safetensors",
            "trigger": "4da woman",
            "base_positive": "portrait photograph",
            "base_negative": "blur",
            "workflow_id": workflow_response.json()["id"],
            "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
        })
        model_response.raise_for_status()
        library_response = client.get("/api/resources/libraries")
        model_list_response = client.get("/api/models")
        workflow_list_response = client.get("/api/workflows")
        if not all(response.status_code == 200 for response in (
            library_response, model_list_response, workflow_list_response,
        )):
            raise RuntimeError("The synthetic browser demo seed did not pass API readback.")
    return anchor


def main_cli():
    _require_free_backend_port()
    previous_env = {name: os.environ.get(name) for name in ("IDEVGEN_CONFIG", "IDEVGEN_DATA_DIR")}
    assistant = ThreadingHTTPServer((BACKEND_HOST, 0), _AssistantHandler)
    assistant_thread = threading.Thread(target=assistant.serve_forever, daemon=True)
    assistant_thread.start()
    server = None
    server_thread = None
    temporary = tempfile.TemporaryDirectory(prefix="idevgen-task8-6-demo-")
    try:
        root = Path(temporary.name)
        data_dir = root / "data"
        config_path = root / "config.json"
        config_path.write_text(json.dumps({
            "comfy_url": f"http://{BACKEND_HOST}:1",
            "comfy_output_dir": "",
            "lora_dir": "",
            "data_dir": "isolated-demo-data",
            "resource_planning_enabled": True,
            "llm_url": f"http://{BACKEND_HOST}:{assistant.server_port}/v1",
            "llm_model": "task86-synthetic-assistant",
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

        _seed(backend_main)
        db.conn().close()
        db._conn = None

        server = uvicorn.Server(uvicorn.Config(
            backend_main.app, host=BACKEND_HOST, port=BACKEND_PORT,
            log_level="warning", access_log=False,
        ))
        server_thread = threading.Thread(target=server.run, name="task8-6-demo-backend", daemon=True)
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

        print("Task 8.6 isolated browser demo is ready.")
        print("The backend serves the built frontend; build it first with: npm --prefix frontend run build")
        print("Open http://127.0.0.1:8777/#/resources and follow docs/task-8-6-browser-walkthrough.md.")
        print("Press Ctrl+C here to stop only this demo backend and assistant; temporary files are removed after shutdown.")
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("Stopping the isolated Task 8.6 demo.")
    finally:
        backend_stopped = server_thread is None
        try:
            if server is not None:
                server.should_exit = True
            if server_thread is not None:
                server_thread.join(timeout=15)
                backend_stopped = not server_thread.is_alive()
        finally:
            try:
                assistant.shutdown()
            finally:
                try:
                    assistant.server_close()
                    assistant_thread.join(timeout=2)
                finally:
                    try:
                        if backend_stopped and "db" in locals() and db._conn is not None:
                            db.conn().close()
                            db._conn = None
                    finally:
                        try:
                            if backend_stopped:
                                temporary.cleanup()
                            else:
                                temporary._finalizer.detach()
                                print("Backend shutdown timed out; temporary data was preserved.")
                        finally:
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
