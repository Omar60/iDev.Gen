"""Serve an isolated Group 5 browser demo against the real frontend and API."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4
from urllib.request import urlopen

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.task7_1_full_auto_browser_demo import (
    ASSISTANT_URL,
    COMFY_URL,
    SyntheticTransport,
    _graph,
    isolated_async_client_factory,
)


HOST = "127.0.0.1"
DEFAULT_PORT = 8795
TEXT_MODEL = "g5-synthetic-text"
VISION_MODEL = "g5-synthetic-vision"
REGIONS = ("chest", "hips", "legs", "feet")
APPEARANCE = "Short dark curls and warm brown eyes. She wears a brass bracelet."
GARMENTS = ["a blue jacket", "a cream blouse", "a pleated skirt", "brown sandals", "a brass bracelet"]


def _completion(request: httpx.Request, payload: dict, identity: str) -> httpx.Response:
    return httpx.Response(200, json={
        "id": identity,
        "object": "chat.completion",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": json.dumps(payload)},
            "finish_reason": "stop",
        }],
    }, request=request)


def _coverage(values: tuple[str, str, str, str]) -> dict:
    return {region: value for region, value in zip(REGIONS, values, strict=True)}


def _photo_candidate() -> dict:
    owned_clause = APPEARANCE.index("She wears")
    return {
        "appearance": APPEARANCE,
        "garments": GARMENTS,
        "unresolved": [],
        "automation": {
            "schema_version": 1,
            "appearance_segments": [
                {
                    "segment_id": "g5-permanent-appearance",
                    "start": 0,
                    "end": owned_clause,
                    "text": APPEARANCE[:owned_clause],
                    "owner": "permanent",
                },
                {
                    "segment_id": "g5-progressive-accessory",
                    "start": owned_clause,
                    "end": len(APPEARANCE),
                    "text": APPEARANCE[owned_clause:],
                    "owner": {"garment_index": 4},
                },
            ],
            "garment_coverage": [
                {"garment_index": 0, "states": {"worn": {"regions": _coverage(("full", "partial", "none", "none"))}}},
                {"garment_index": 1, "states": {"worn": {"regions": _coverage(("full", "partial", "none", "none"))}}},
                {"garment_index": 2, "states": {"worn": {"regions": _coverage(("none", "full", "partial", "none"))}}},
                {"garment_index": 3, "states": {"worn": {"regions": _coverage(("none", "none", "partial", "full"))}}},
                {"garment_index": 4, "states": {"worn": {"regions": _coverage(("none", "none", "none", "none"))}}},
            ],
            "baseline_coverage": None,
            "inventory_complete": None,
        },
    }


def _manual_candidate(source: dict) -> dict:
    appearance = source["appearance"]
    garments = source["garments"]
    coverage = []
    for index, garment in enumerate(garments):
        states = {"worn": {"regions": _coverage(("unknown",) * 4)}}
        if garment.get("aside"):
            states["aside"] = {"regions": _coverage(("unknown",) * 4)}
        coverage.append({"garment_index": index, "states": states})
    return {
        "schema_version": 1,
        "draft_content_digest": source["draft_content_digest"],
        "appearance_segments": ([{
            "segment_id": "g5-manual-appearance",
            "start": 0,
            "end": len(appearance),
            "text": appearance,
            "owner": "permanent",
        }] if appearance else []),
        "garment_coverage": coverage,
        "baseline_coverage": None,
        "inventory_complete": None,
    }


class Group5Transport(SyntheticTransport):
    """Deterministic assistant/ComfyUI boundary that rejects unknown hosts."""

    def __init__(self, output_dir: Path, mode: str):
        super().__init__(output_dir)
        self.mode = mode
        self.vision_calls = 0
        self.vision_companion_failure_seen = False
        self.manual_proposal_calls = 0
        self.manual_proposal_failure_seen = False
        self.comfy_request_bodies: list[bytes] = []

    def _queue_prompt(self, request: httpx.Request) -> httpx.Response:
        # Retain the exact HTTPX-serialized request bytes at the synthetic
        # provider boundary, then let the existing fake validate and execute
        # its normal ComfyUI protocol handling.
        self.comfy_request_bodies.append(bytes(request.content))
        return super()._queue_prompt(request)

    def _assistant(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/models"):
            return self._response(request, 200, {"data": [
                {"id": TEXT_MODEL, "object": "model"},
                {"id": VISION_MODEL, "object": "model"},
            ]})
        if request.method != "POST" or not request.url.path.endswith("/chat/completions"):
            return self._response(request, 404, {"error": {"message": "Unknown synthetic assistant route"}})
        body = json.loads(request.content or b"{}")
        model = body.get("model")
        messages = body.get("messages", [])
        user = next((message.get("content", "") for message in messages if message.get("role") == "user"), "")
        text = "\n".join(
            part.get("text", "") for part in user if isinstance(part, dict)
        ) if isinstance(user, list) else user

        if model == VISION_MODEL:
            self.vision_calls += 1
            companion_requested = "automation" in text.casefold()
            if self.mode == "photo" and companion_requested and not self.vision_companion_failure_seen:
                self.vision_companion_failure_seen = True
                return self._response(request, 503, {"error": {"message": "Synthetic first photo request failure"}})
            ordinary = {key: value for key, value in _photo_candidate().items() if key != "automation"}
            output = _photo_candidate() if companion_requested else ordinary
            return _completion(request, output, f"g5-vision-{self.vision_calls}")

        if model != TEXT_MODEL:
            raise RuntimeError(f"Blocked unexpected synthetic model: {model}")

        if "Context: " in text:
            self.assistant_calls.append({"model": model, "kind": "writer"})
            context = json.loads(text.partition("Context: ")[2])
            requested = context.get("preparation", {}).get("requested_fields")
            if not isinstance(requested, list) or not requested:
                raise RuntimeError("The synthetic writer received no requested fields.")
            output = {field: f"Synthetic {field} for {context['take_id']}" for field in requested}
            return _completion(request, output, f"g5-writer-{len(self.assistant_calls)}")

        if "Source:\n" in text:
            self.manual_proposal_calls += 1
            if self.mode == "photo" and not self.manual_proposal_failure_seen:
                self.manual_proposal_failure_seen = True
                return self._response(request, 503, {"error": {"message": "Synthetic first manual proposal failure"}})
            try:
                source = json.loads(text.partition("Source:\n")[2])
            except json.JSONDecodeError as exc:
                raise RuntimeError("The synthetic manual proposal source was not valid JSON.") from exc
            return _completion(request, _manual_candidate(source), f"g5-manual-{self.manual_proposal_calls}")

        raise RuntimeError("The synthetic assistant received an unrecognized request shape.")


def _arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--mode", choices=("photo", "manual-no-assistant"), default="photo",
        help="photo enables deterministic assistant fakes; manual-no-assistant leaves them unconfigured",
    )
    parser.add_argument(
        "--api-wire-audit", action="store_true",
        help="run an additional connected save/preflight/create flow to audit HTTP request bytes",
    )
    parser.add_argument(
        "--stop-after-api-wire-audit", action="store_true",
        help="stop the temporary backend automatically after the connected API audit settles",
    )
    return parser.parse_args(argv)


def _require_free_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise RuntimeError("Choose a TCP port from 1 through 65535.")
    with socket.socket() as probe:
        try:
            probe.bind((HOST, port))
        except OSError as exc:
            raise RuntimeError(f"Loopback port {port} is already in use; choose another --port.") from exc


def _build_frontend() -> None:
    command = ["npm", "--prefix", str(ROOT / "frontend"), "run", "build"]
    if os.name == "nt":
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", *command]
    if subprocess.run(command, cwd=ROOT, check=False).returncode:
        raise RuntimeError("The fresh frontend build failed; the browser demo was not started.")


def _seed(backend_main) -> dict:
    from fastapi.testclient import TestClient
    import resource_store

    with TestClient(backend_main.app) as client:
        workflow = client.post("/api/workflows", json={
            "name": "Group 5 synthetic t2i workflow",
            "graph": _graph(),
            "kind": "t2i",
        })
        workflow.raise_for_status()
        model = client.post("/api/models", json={
            "name": "Group 5 invented character",
            "lora_name": "characters/ada.safetensors",
            "trigger": "invented portrait character",
            "base_positive": "portrait photograph",
            "base_negative": "blur",
            "workflow_id": workflow.json()["id"],
            "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
        })
        model.raise_for_status()

    library_key = "g5_invented_studio"
    source_id = "invented-studio-01"
    label = "Invented portrait studio"
    description = "Soft daylight falls across an empty invented studio."
    library_id = resource_store.ensure_library(library_key, kind="rooms")
    revision_id = resource_store.record_revision(
        library_id, source_id,
        {"id": source_id, "label": label, "scene_theme": description},
        translation={"label": label, "scene_theme": description},
    )
    revision = resource_store.get_revision(revision_id=revision_id)
    if revision is None:
        raise RuntimeError("The isolated demo room could not be read back.")

    return {
        "model_id": model.json()["id"],
        "workflow_id": workflow.json()["id"],
        "room": {
            "library_key": library_key,
            "source_id": source_id,
            "content_digest": revision["content_digest"],
        },
    }


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


def _api_wire_audit(base_url: str, model_id: int, room: dict, database) -> dict:
    """Use real connected endpoints to create one isolated 13-take Full Auto run."""
    if not base_url.startswith(f"http://{HOST}:"):
        raise RuntimeError("The API wire audit must stay on loopback.")
    from backend import saved_looks

    appearance = "Short dark curls and warm brown eyes."
    garments = [
        {"key": None, "wording": "a blue jacket", "aside": ""},
        {"key": None, "wording": "a cream blouse", "aside": ""},
        {"key": None, "wording": "a pleated skirt", "aside": ""},
        {"key": None, "wording": "brown sandals", "aside": ""},
        {
            "key": None,
            "wording": "a brass bracelet",
            "aside": "the brass bracelet loosened but still on her wrist",
        },
    ]
    source_garments = [
        {"key": item["key"], "wording": item["wording"], "aside": item["aside"]}
        for item in garments
    ]
    source = saved_looks.canonicalize_prospective_automation_source(appearance, source_garments)
    states = [
        _coverage(("full", "partial", "none", "none")),
        _coverage(("full", "partial", "none", "none")),
        _coverage(("none", "full", "partial", "none")),
        _coverage(("none", "none", "partial", "full")),
        _coverage(("none", "none", "none", "none")),
    ]
    coverage = []
    for index, regions in enumerate(states):
        garment_states = {"worn": {"regions": regions}}
        if garments[index]["aside"]:
            garment_states["aside"] = {"regions": _coverage(("none", "none", "none", "none"))}
        coverage.append({"garment_index": index, "states": garment_states})
    profile = {
        "schema_version": 1,
        "draft_content_digest": source["draft_content_digest"],
        "appearance_segments": [{
            "segment_id": "g5-wire-permanent-appearance",
            "start": 0,
            "end": len(appearance),
            "text": appearance,
            "owner": "permanent",
        }],
        "garment_coverage": coverage,
        "baseline_coverage": {"regions": _coverage(("none", "none", "none", "none"))},
        "inventory_complete": True,
    }
    create_body = {
        "request_id": str(uuid4()),
        "name": "Group 5 isolated wire audit look",
        "appearance": appearance,
        "garments": garments,
        "automation": {
            "profile": profile,
            "profile_review_confirmed": True,
            "inventory_complete_confirmed": True,
            "baseline_coverage_confirmed": True,
            "removal_order_confirmed": True,
        },
    }
    with httpx.Client(timeout=30) as client:
        saved_response = client.post(f"{base_url}/api/looks", json=create_body)
        saved_response.raise_for_status()
        saved = saved_response.json()
        identity = saved["automation_profile"]
        base = {
            "character_id": model_id,
            "scene_anchor": room,
            "photo_count": 13,
            "saved_look": {
                "look_key": saved["key"],
                "version": saved["version"],
                "content_digest": saved["content_digest"],
            },
            "full_auto": {
                "authorization_version": 1,
                "generate_without_pauses": True,
                "profile_revision": identity["profile_revision"],
                "profile_digest": identity["profile_digest"],
                "progression": "all_stages_full_interval",
                "framing_intent": "vary",
            },
        }
        preflight_response = client.post(
            f"{base_url}/api/sessions/guided/full-auto/preflight", json=base,
        )
        preflight_response.raise_for_status()
        quote = preflight_response.json()
        create_body = dict(base)
        create_body["request_id"] = str(uuid4())
        create_body["full_auto"] = {
            **base["full_auto"],
            "scope_digest": quote["scope_digest"],
            "scope_issued_at": quote["scope_issued_at"],
        }
        created_response = client.post(f"{base_url}/api/sessions/guided", json=create_body)
        created_response.raise_for_status()
        created = created_response.json()
        session_id = int(created["session_id"])
        run_id = created["full_auto_run_id"]
        deadline = time.monotonic() + 120
        status = None
        while time.monotonic() < deadline:
            status_response = client.get(f"{base_url}/api/sessions/{session_id}/full-auto/{run_id}")
            status_response.raise_for_status()
            status = status_response.json()
            if status.get("state") in {"succeeded", "failed", "cancelled", "needs_attention"}:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("The isolated API wire-audit Full Auto run did not settle in time.")
        if status is None or status.get("state") != "succeeded":
            raise RuntimeError(f"The isolated API wire-audit Full Auto run did not succeed: {status!r}")
        authorization_receipts = database.one(
            "SELECT COUNT(*) AS n FROM full_auto_run_receipt WHERE run_id = ?", run_id,
        )["n"]
        print(
            "Connected API wire audit: "
            f"look_version={saved['version']} profile_revision={identity['profile_revision']} "
            f"session={session_id} run_state={status['state']} target_count=13 "
            f"authorization_receipt_count={authorization_receipts}.",
            flush=True,
        )
        return {
            "look_key": saved["key"],
            "session_id": session_id,
            "run_id": run_id,
            "authorization_receipt_count": authorization_receipts,
            "expected_target_count": 13,
        }


def _print_summary(
    database,
    providers: Group5Transport,
    mode: str,
    wire_audit: dict | None = None,
) -> list[str]:
    failures = []
    counts = {}
    for table in ("saved_look_version", "saved_look_automation_profile", "session", "full_auto_run", "prepared_take", "shot"):
        counts[table] = database.one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
    print(f"Final isolated database counts ({mode}): {json.dumps(counts, sort_keys=True)}", flush=True)
    identities = database.q(
        "SELECT look_key, look_version AS version, look_content_digest AS content_digest, "
        "profile_revision, profile_digest FROM saved_look_automation_profile "
        "ORDER BY look_key, look_version, profile_revision"
    )
    print(f"Saved profile identities: {json.dumps(identities, sort_keys=True)}", flush=True)
    if mode == "photo":
        print(
            "Synthetic boundary calls: "
            f"vision={providers.vision_calls}, manual_suggestions={providers.manual_proposal_calls}, "
            f"writer={len(providers.assistant_calls)}, ComfyUI={len(providers.comfy_prompts)}; "
            "unexpected outbound hosts are rejected.",
            flush=True,
        )
        for run in database.q("SELECT run_id, session_id, state, phase FROM full_auto_run ORDER BY created_at"):
            sid = int(run["session_id"])
            prepared_count = database.one(
                "SELECT COUNT(*) AS n FROM prepared_take WHERE session_id = ?", sid,
            )["n"]
            submitted = database.one(
                "SELECT COUNT(*) AS n FROM full_auto_submission_receipt WHERE run_id = ?", run["run_id"],
            )["n"]
            completed = database.one(
                "SELECT COUNT(*) AS n FROM full_auto_submission_receipt AS receipt "
                "JOIN shot ON shot.id = receipt.shot_id "
                "WHERE receipt.run_id = ? AND shot.status = 'done'", run["run_id"],
            )["n"]
            print(
                f"Full Auto {run['run_id']}: state={run['state']} phase={run['phase']} "
                f"prepared={prepared_count} submitted={submitted} completed={completed}",
                flush=True,
            )
            receipts = database.q(
                "SELECT receipt.target_index, receipt.take_id, receipt.prepared_take_id, "
                "receipt.shot_id, shot.prompt_id "
                "FROM full_auto_submission_receipt AS receipt "
                "JOIN shot ON shot.id = receipt.shot_id "
                "WHERE receipt.run_id = ? ORDER BY receipt.target_index",
                run["run_id"],
            )
            sent_by_prompt_id = {}
            for raw_body in providers.comfy_request_bodies:
                try:
                    body = json.loads(raw_body)
                    graph = body["prompt"]
                    positive = graph["3"]["inputs"]["text"]
                    sent_by_prompt_id[body["prompt_id"]] = positive.encode("utf-8")
                except (KeyError, TypeError, ValueError, UnicodeEncodeError):
                    continue
            comparisons = []
            hashes = []
            for receipt in receipts:
                prepared = database.one(
                    "SELECT final_prompt FROM prepared_take WHERE id = ?",
                    receipt["prepared_take_id"],
                )
                shot = database.one("SELECT prompt FROM shot WHERE id = ?", receipt["shot_id"])
                if prepared is None or shot is None:
                    comparisons.append(False)
                    hashes.append({"take_id": receipt["take_id"], "prepared_prompt_sha256": None})
                    continue
                prepared_bytes = prepared["final_prompt"].encode("utf-8")
                shot_bytes = shot["prompt"].encode("utf-8")
                sent_bytes = sent_by_prompt_id.get(receipt["prompt_id"])
                comparisons.append(
                    prepared_bytes == shot_bytes == sent_bytes if sent_bytes is not None else False
                )
                hashes.append({
                    "take_id": receipt["take_id"],
                    "prepared_prompt_sha256": hashlib.sha256(prepared_bytes).hexdigest(),
                })
            print(
                "Prepared/shot/HTTP-body prompt byte agreement: "
                f"receipts={len(receipts)} prepared_shot_http_equal={sum(comparisons)}/{len(receipts)} "
                f"all_equal={bool(comparisons) and all(comparisons)} prompt_sha256={json.dumps(hashes, sort_keys=True)}",
                flush=True,
            )
            if wire_audit is not None and run["run_id"] == wire_audit["run_id"]:
                expected = wire_audit["expected_target_count"]
                authorization_receipts = database.one(
                    "SELECT COUNT(*) AS n FROM full_auto_run_receipt WHERE run_id = ?",
                    run["run_id"],
                )["n"]
                target_indices = [int(receipt["target_index"]) for receipt in receipts]
                http_bodies = len(providers.comfy_request_bodies)
                check_results = {
                    "persisted run state": (run["state"] == "succeeded", "succeeded", run["state"]),
                    "prepared take count": (prepared_count == expected, expected, prepared_count),
                    "submission receipt count": (submitted == expected, expected, submitted),
                    "completed shot count": (completed == expected, expected, completed),
                    "joined receipt count": (len(receipts) == expected, expected, len(receipts)),
                    "target indices": (
                        sorted(target_indices) == list(range(expected)),
                        list(range(expected)),
                        sorted(target_indices),
                    ),
                    "captured HTTP body count": (http_bodies == expected, expected, http_bodies),
                    "prepared/shot/HTTP bytes": (
                        len(comparisons) == expected and all(comparisons),
                        f"{expected}/{expected} equal",
                        f"{sum(comparisons)}/{len(comparisons)} equal",
                    ),
                    "initial authorization receipt count": (
                        authorization_receipts == 1, 1, authorization_receipts,
                    ),
                    "reported authorization receipt count": (
                        wire_audit["authorization_receipt_count"] == authorization_receipts,
                        authorization_receipts,
                        wire_audit["authorization_receipt_count"],
                    ),
                }
                failures.extend(
                    f"{label}: expected {wanted}, observed {observed}"
                    for label, (passed, wanted, observed) in check_results.items()
                    if not passed
                )
                print(
                    "Wire-audit required checks: "
                    + json.dumps({label: result[0] for label, result in check_results.items()}, sort_keys=True),
                    flush=True,
                )
    if wire_audit is not None and not any(
        run["run_id"] == wire_audit["run_id"]
        for run in database.q("SELECT run_id FROM full_auto_run")
    ):
        failures.append("wire-audit Full Auto run was not persisted")
    return failures


def main_cli(argv=None) -> int:
    args = _arguments(argv)
    _require_free_port(args.port)
    _build_frontend()
    env_names = ("IDEVGEN_CONFIG", "IDEVGEN_DATA_DIR", "IDEVGEN_RESOURCE_PLANNING_ENABLED")
    previous_env = {name: os.environ.get(name) for name in env_names}
    temporary = tempfile.TemporaryDirectory(prefix="idevgen-g5-browser-")
    server = None
    server_thread = None
    original_async_client = None
    database = None
    providers = None
    backend_main = None
    wire_audit = None
    verification_failures = []
    try:
        root = Path(temporary.name)
        data_dir = root / "data"
        output_dir = root / "comfy-output"
        output_dir.mkdir()
        synthetic_photo = root / "invented-layered-outfit.png"
        Image.new("RGB", (24, 24), (102, 128, 154)).save(synthetic_photo, format="PNG")
        config_path = root / "config.json"
        with_assistant = args.mode == "photo"
        config_path.write_text(json.dumps({
            "comfy_url": COMFY_URL,
            "comfy_output_dir": str(output_dir),
            "lora_dir": "",
            "data_dir": str(data_dir),
            "resource_planning_enabled": True,
            "llm_url": ASSISTANT_URL if with_assistant else "",
            "llm_model": TEXT_MODEL if with_assistant else "",
            "llm_vision_model": VISION_MODEL if with_assistant else "",
            "llm_key": "",
            "checkpoints": {},
            "room_libraries": [],
        }, indent=2), encoding="utf-8")
        os.environ["IDEVGEN_CONFIG"] = str(config_path)
        os.environ["IDEVGEN_DATA_DIR"] = str(data_dir)
        os.environ.pop("IDEVGEN_RESOURCE_PLANNING_ENABLED", None)
        for path in (ROOT, ROOT / "backend"):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))

        import db
        import main as backend_main
        import runner as runner_module
        import uvicorn
        from comfy import Comfy
        from runner import Runner

        database = db
        seeded = _seed(backend_main)
        db.conn().close()
        db._conn = None
        providers = Group5Transport(output_dir, args.mode)
        original_async_client = httpx.AsyncClient
        httpx.AsyncClient = isolated_async_client_factory(providers, original_async_client)
        backend_main.CONFIG["comfy_url"] = COMFY_URL
        backend_main.CONFIG["comfy_output_dir"] = str(output_dir)
        backend_main.COMFY_OUTPUT = output_dir
        runner_module.POLL_SECONDS = 0.001
        comfy_client = Comfy(COMFY_URL)
        backend_main.comfy = comfy_client
        backend_main.runner = Runner(comfy_client, backend_main.SESSIONS_DIR, output_dir)

        server = uvicorn.Server(uvicorn.Config(
            backend_main.app, host=HOST, port=args.port,
            log_level="warning", access_log=False,
        ))
        server_thread = threading.Thread(target=server.run, name="group-5-browser-demo", daemon=True)
        server_thread.start()
        base_url = f"http://{HOST}:{args.port}"
        _wait_for_backend(server, server_thread, base_url)

        print("Group 5 isolated browser demo is ready.", flush=True)
        print(f"Open {base_url}/#/looks in the browser.", flush=True)
        print(f"Invented character: Group 5 invented character (id {seeded['model_id']}).", flush=True)
        print("Ready scene: Invented portrait studio.", flush=True)
        if args.api_wire_audit:
            if args.mode != "photo":
                raise RuntimeError("The API wire audit requires --mode photo so its fake assistant is configured.")
            wire_audit = _api_wire_audit(base_url, seeded["model_id"], seeded["room"], database)
            if args.stop_after_api_wire_audit:
                server.should_exit = True
        elif args.stop_after_api_wire_audit:
            raise RuntimeError("--stop-after-api-wire-audit requires --api-wire-audit.")
        if args.mode == "photo":
            print(f"Synthetic photo for the browser file chooser: {synthetic_photo}", flush=True)
            print("The first opt-in photo companion request fails; ordinary extraction retries and remains editable.", flush=True)
            print("Cancel that stage, upload the same generated image again, and the next photo proposal includes unaccepted profile suggestions.", flush=True)
            print("The fake writer and fake ComfyUI complete only after explicit Full Auto authorization.", flush=True)
        else:
            print("No assistant URL or model is configured. Complete and save a manual look; provider requests fail closed.", flush=True)
        print("Temporary config, SQLite data, photo staging, synthetic outputs and logs stay under a disposable temp directory.", flush=True)
        print("Press Ctrl+C to stop; sanitized persisted counts print after the backend stops.", flush=True)
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        print("Stopping the isolated Group 5 browser demo.", flush=True)
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
                    try:
                        database.conn()
                        verification_failures.extend(
                            _print_summary(database, providers, args.mode, wire_audit)
                        )
                    finally:
                        if database._conn is not None:
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
    if verification_failures:
        raise RuntimeError("The isolated API wire audit failed: " + "; ".join(verification_failures))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main_cli())
    except RuntimeError as exc:
        raise SystemExit(str(exc))
