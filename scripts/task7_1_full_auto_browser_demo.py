"""Start an isolated browser demo for the Task 7.1 Full Auto journey."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen

import httpx
from PIL import Image
from PIL.PngImagePlugin import PngInfo


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
DEFAULT_PORT = 8786
ASSISTANT_URL = "http://synthetic-assistant.invalid/v1"
COMFY_URL = "http://synthetic-comfy.invalid"
REGIONS = ("chest", "hips", "legs", "feet")


def isolated_async_client_factory(handler, original_client=None):
    """Keep every backend HTTP client on one rejecting in-process transport."""
    original = original_client or httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def create_client(*args, **kwargs):
        if kwargs.get("transport") is not None:
            raise RuntimeError("The isolated Full Auto demo owns all outbound HTTP transports.")
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    return create_client


class SyntheticTransport:
    """OpenAI-compatible writer and ComfyUI HTTP protocol with synthetic output files."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.assistant_calls: list[dict] = []
        self.comfy_prompts: list[dict] = []
        self.histories: dict[str, dict] = {}
        self.output_names: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "http":
            raise RuntimeError("Synthetic providers accept only their local HTTP endpoints.")
        if request.url.host == "synthetic-assistant.invalid":
            return self._assistant(request)
        if request.url.host == "synthetic-comfy.invalid":
            return self._comfy(request)
        raise RuntimeError(f"Blocked unexpected outbound host: {request.url.host}")

    @staticmethod
    def _response(request: httpx.Request, status: int, payload: dict) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request)

    def _assistant(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/models"):
            return self._response(request, 200, {
                "data": [{"id": "task7-1-synthetic-assistant", "object": "model"}],
            })
        if request.method != "POST" or not request.url.path.endswith("/chat/completions"):
            return self._response(request, 404, {"error": {"message": "Unknown synthetic assistant route"}})

        body = json.loads(request.content or b"{}")
        user = next(
            (message["content"] for message in body.get("messages", []) if message.get("role") == "user"),
            "",
        )
        prefix, separator, serialized = user.partition("Context: ")
        if not separator or not prefix:
            raise RuntimeError("The synthetic assistant expected a server-owned take context.")
        context = json.loads(serialized)
        preparation = context.get("preparation")
        requested = preparation.get("requested_fields") if isinstance(preparation, dict) else None
        if not isinstance(requested, list) or not requested:
            raise RuntimeError("The synthetic assistant received no requested take fields.")
        take_id = context.get("take_id")
        if not isinstance(take_id, str) or not take_id:
            raise RuntimeError("The synthetic assistant received no stable take ID.")
        self.assistant_calls.append({"take_id": take_id, "model": body.get("model")})
        output = {
            field: f"Synthetic {field} choice {len(self.assistant_calls)}"
            for field in requested
        }
        return self._response(request, 200, {
            "id": f"task7-1-completion-{len(self.assistant_calls)}",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(output)},
                "finish_reason": "stop",
            }],
        })

    def _comfy(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/system_stats":
            return self._response(request, 200, {
                "system": {"comfyui_version": "task7-1-synthetic"},
                "devices": [{"name": "Synthetic CPU"}],
            })
        if request.method == "GET" and path == "/features":
            return self._response(request, 200, {"client_prompt_id": True})
        if request.method == "GET" and path == "/queue":
            return self._response(request, 200, {"queue_running": [], "queue_pending": []})
        if request.method == "GET" and path == "/history":
            return self._response(request, 200, self.histories)
        if request.method == "GET" and path.startswith("/history/"):
            prompt_id = path.rsplit("/", 1)[-1]
            record = self.histories.get(prompt_id)
            return self._response(request, 200, {prompt_id: record} if record else {})
        if request.method == "POST" and path == "/prompt":
            return self._queue_prompt(request)
        return self._response(request, 404, {"error": {"message": "Unknown synthetic ComfyUI route"}})

    def _queue_prompt(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        graph = body.get("prompt")
        prompt_id = body.get("prompt_id")
        if not isinstance(graph, dict) or not isinstance(prompt_id, str) or not prompt_id:
            raise RuntimeError("The synthetic ComfyUI requires a graph and verified prompt ID.")
        save_nodes = [
            (node_id, node) for node_id, node in graph.items()
            if isinstance(node, dict) and node.get("class_type") == "SaveImage"
        ]
        if len(save_nodes) != 1:
            raise RuntimeError("The synthetic ComfyUI expected one SaveImage node.")
        save_node_id, save_node = save_nodes[0]
        prefix = save_node["inputs"]["filename_prefix"]
        if not isinstance(prefix, str) or prefix in self.output_names:
            raise RuntimeError("The synthetic ComfyUI received an empty or reused filename prefix.")
        self.output_names.add(prefix)
        extra_data = dict(body.get("extra_data") or {})
        extra_data["client_id"] = body.get("client_id")
        filename = f"{prefix.rsplit('/', 1)[-1]}_00001_.png"
        subfolder = prefix.rpartition("/")[0]
        folder = self.output_dir.joinpath(*subfolder.split("/")) if subfolder else self.output_dir
        folder.mkdir(parents=True, exist_ok=True)
        metadata = PngInfo()
        metadata.add_text("prompt", json.dumps(graph))
        marker = extra_data.get("idevgen_attempt")
        if marker is not None:
            metadata.add_text("idevgen_attempt", json.dumps(marker))
        Image.new("RGB", (2, 2), (64, 96, 128)).save(folder / filename, pnginfo=metadata)

        self.comfy_prompts.append({"prompt_id": prompt_id, "filename_prefix": prefix, "graph": graph})
        prompt_record = [len(self.comfy_prompts), prompt_id, graph, extra_data, []]
        image = {"filename": filename, "subfolder": subfolder, "type": "output"}
        self.histories[prompt_id] = {
            "prompt": prompt_record,
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {save_node_id: {"images": [image]}},
        }
        return self._response(request, 200, {"prompt_id": prompt_id, "number": len(self.comfy_prompts)})


def _graph() -> dict:
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


def _profile(saved: dict) -> dict:
    appearance = saved["appearance"]
    split = appearance.index(" She wears")
    garment = saved["outfit"]["garments"][0]
    provenance = {
        "origin": "user",
        "source_identity": "task7-1-synthetic-profile-editor",
        "source_digest": saved["content_digest"],
        "accepted_revision": 1,
    }

    def uncovered_state() -> dict:
        return {
            "regions": {region: "none" for region in REGIONS},
            "provenance": provenance,
        }

    return {
        "schema_version": 1,
        "look": {
            "key": saved["key"], "version": saved["version"],
            "content_digest": saved["content_digest"],
        },
        "appearance_segments": [
            {
                "segment_id": "appearance-permanent",
                "start": 0, "end": split, "text": appearance[:split],
                "owner": "permanent", "provenance": provenance,
            },
            {
                "segment_id": "appearance-bracelet",
                "start": split, "end": len(appearance), "text": appearance[split:],
                "owner": f"garment:{garment['key']}", "provenance": provenance,
            },
        ],
        "garment_coverage": {garment["key"]: {
            "worn": uncovered_state(), "aside": uncovered_state(),
        }},
        "baseline_coverage": uncovered_state(),
        "inventory_complete": {"value": True, "provenance": provenance},
        "authored_provenance": provenance,
    }


def _seed(backend_main) -> dict:
    from fastapi.testclient import TestClient
    import resource_store
    from backend import saved_looks

    with TestClient(backend_main.app) as client:
        workflow_response = client.post("/api/workflows", json={
            "name": "Task 7.1 synthetic t2i workflow",
            "graph": _graph(), "kind": "t2i",
        })
        workflow_response.raise_for_status()
        workflow_id = workflow_response.json()["id"]
        model_response = client.post("/api/models", json={
            "name": "Task 7.1 synthetic character",
            "lora_name": "characters/ada.safetensors",
            "trigger": "invented portrait character",
            "base_positive": "portrait photograph",
            "base_negative": "blur",
            "workflow_id": workflow_id,
            "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
        })
        model_response.raise_for_status()

        library_key = "task7-1-synthetic-room"
        source_id = "scene-001"
        label = "Task 7.1 synthetic portrait room"
        theme = "Soft light enters an empty portrait room from a high window."
        library_id = resource_store.ensure_library(library_key, kind="rooms")
        revision_id = resource_store.record_revision(
            library_id, source_id,
            {"id": source_id, "label": label, "scene_theme": theme},
            translation={"label": label, "scene_theme": theme},
        )
        room_revision = resource_store.get_revision(revision_id=revision_id)
        assert room_revision is not None
        anchor = {
            "library_key": library_key,
            "source_id": source_id,
            "content_digest": room_revision["content_digest"],
        }

        saved = saved_looks.create({
            "name": "Task 7.1 progressive bracelet look",
            "appearance": "A portrait in a quiet room. She wears a brass bracelet.",
            "garments": [{
                "wording": "a brass bracelet",
                "aside": "a brass bracelet resting beside the wrist",
            }],
        })
        profile = saved_looks.publish_automation_profile(
            saved["key"], saved["version"], saved["content_digest"], 0, _profile(saved),
        )
        latest = saved_looks.create_version(saved["key"], saved["version"], {
            "name": saved["name"],
            "appearance": "A different portrait in a quiet room.",
            "garments": [{"wording": "a silver necklace", "aside": ""}],
        })

    return {
        "model_id": model_response.json()["id"],
        "workflow_id": workflow_id,
        "room": anchor,
        "saved": saved,
        "profile": profile,
        "latest": latest,
    }


def _arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help="loopback port for the disposable backend; exits if occupied",
    )
    return parser.parse_args(argv)


def _require_free_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise RuntimeError("Choose a TCP port from 1 through 65535.")
    with socket.socket() as probe:
        try:
            probe.bind((HOST, port))
        except OSError as exc:
            raise RuntimeError(
                f"Loopback port {port} is already in use. Leave that service alone, "
                "then rerun this demo with a different --port."
            ) from exc


def _write_config(path: Path, data_dir: Path, output_dir: Path) -> None:
    path.write_text(json.dumps({
        "comfy_url": COMFY_URL,
        "comfy_output_dir": str(output_dir),
        "lora_dir": "",
        "data_dir": str(data_dir),
        "resource_planning_enabled": True,
        "llm_url": ASSISTANT_URL,
        "llm_model": "task7-1-synthetic-assistant",
        "llm_vision_model": "",
        "llm_key": "",
        "checkpoints": {},
        "room_libraries": [],
    }, indent=2), encoding="utf-8")


def _build_frontend() -> None:
    command = ["npm", "--prefix", str(ROOT / "frontend"), "run", "build"]
    if os.name == "nt":
        # ``npm`` resolves to a PowerShell script on Windows, which Python's
        # CreateProcess cannot launch directly. Let cmd resolve npm.cmd.
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *command]
    result = subprocess.run(
        command,
        cwd=ROOT, check=False,
    )
    if result.returncode:
        raise RuntimeError("The fresh frontend build failed; the browser demo was not started.")


def _wait_for_backend(server, thread, base_url: str) -> None:
    for _ in range(200):
        if server.started:
            break
        if not thread.is_alive():
            raise RuntimeError("The isolated backend stopped before it became ready.")
        time.sleep(0.05)
    else:
        raise RuntimeError("The isolated backend did not become ready in time.")
    for _ in range(100):
        if not thread.is_alive():
            raise RuntimeError("The isolated backend stopped before health readback.")
        try:
            with urlopen(f"{base_url}/api/config", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("The isolated backend did not pass its health readback.")


def _print_protocol_summary(db, providers: SyntheticTransport) -> None:
    runs = db.q("SELECT run_id, session_id, state, phase FROM full_auto_run ORDER BY created_at")
    if not runs:
        print("Browser journey: no Full Auto run was created.", flush=True)
    for run in runs:
        run_id = run["run_id"]
        session_id = int(run["session_id"])
        prepared = db.one("SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", session_id)["n"]
        linked = db.one("SELECT COUNT(*) AS n FROM full_auto_submission_receipt WHERE run_id = ?", run_id)["n"]
        completed = db.one(
            "SELECT COUNT(*) AS n FROM full_auto_execution_attempt "
            "WHERE run_id = ? AND completion_state = 'completed'", run_id,
        )["n"]
        outputs = db.one(
            "SELECT COUNT(*) AS n FROM shot WHERE session_id = ? AND status = 'done'", session_id,
        )["n"]
        print(
            f"Run {run_id}: state={run['state']} phase={run['phase']} "
            f"prepared={prepared} linked={linked} completed_attempts={completed} outputs={outputs}",
            flush=True,
        )
    print(
        f"Synthetic provider calls: assistant={len(providers.assistant_calls)} "
        f"ComfyUI={len(providers.comfy_prompts)}; no external provider was contacted.",
        flush=True,
    )


def main_cli(argv=None) -> int:
    args = _arguments(argv)
    _require_free_port(args.port)
    _build_frontend()

    env_names = ("IDEVGEN_CONFIG", "IDEVGEN_DATA_DIR", "IDEVGEN_RESOURCE_PLANNING_ENABLED")
    previous_env = {name: os.environ.get(name) for name in env_names}
    temporary = tempfile.TemporaryDirectory(prefix="idevgen-task7-1-demo-")
    server = None
    server_thread = None
    backend_main = None
    database = None
    original_async_client = None
    providers = None
    try:
        root = Path(temporary.name)
        data_dir = root / "data"
        output_dir = root / "comfy-output"
        output_dir.mkdir()
        config_path = root / "config.json"
        _write_config(config_path, data_dir, output_dir)
        os.environ["IDEVGEN_CONFIG"] = str(config_path)
        os.environ["IDEVGEN_DATA_DIR"] = str(data_dir)
        os.environ.pop("IDEVGEN_RESOURCE_PLANNING_ENABLED", None)
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        if str(ROOT / "backend") not in sys.path:
            sys.path.insert(0, str(ROOT / "backend"))

        import db
        import main as backend_main
        import uvicorn

        database = db
        seeded = _seed(backend_main)
        db.conn().close()
        db._conn = None
        providers = SyntheticTransport(output_dir)
        original_async_client = httpx.AsyncClient
        httpx.AsyncClient = isolated_async_client_factory(providers, original_async_client)

        server = uvicorn.Server(uvicorn.Config(
            backend_main.app, host=HOST, port=args.port,
            log_level="warning", access_log=False,
        ))
        server_thread = threading.Thread(
            target=server.run, name="task7-1-full-auto-demo-backend", daemon=True,
        )
        server_thread.start()
        base_url = f"http://{HOST}:{args.port}"
        _wait_for_backend(server, server_thread, base_url)

        print("Task 7.1 isolated Full Auto browser journey is ready.", flush=True)
        print(f"Open {base_url}/#/resources in the browser.", flush=True)
        print("Choose 'Task 7.1 synthetic character' and 'Task 7.1 synthetic portrait room'.", flush=True)
        print(
            f"Enable Full Auto and select exact look version {seeded['saved']['version']} "
            f"with profile revision {seeded['profile']['profile_revision']}; "
            f"version {seeded['latest']['version']} is newer and has no profile.",
            flush=True,
        )
        print("Set the count to 40 and click 'Generate without pauses' once.", flush=True)
        print("The look has three stages: bracelet worn, bracelet aside, and bracelet removed.", flush=True)
        print("Synthetic assistant and ComfyUI requests use an in-process HTTPX transport; unexpected hosts fail closed.", flush=True)
        print("Press Ctrl+C to stop; temporary config, database and images are removed after backend shutdown.", flush=True)
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("Stopping the isolated Task 7.1 browser demo.", flush=True)
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
                httpx.AsyncClient = original_async_client
            try:
                if backend_stopped and database is not None:
                    if database._conn is not None:
                        _print_protocol_summary(database, providers)
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
        if backend_stopped:
            print("Temporary demo data removal completed.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main_cli())
    except RuntimeError as exc:
        raise SystemExit(str(exc))
