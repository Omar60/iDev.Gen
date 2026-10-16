"""Disposable backend worker for Task 7.2 process-recovery tests."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import httpx


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from scripts.task7_1_full_auto_browser_demo import (  # noqa: E402
    ASSISTANT_URL,
    COMFY_URL,
    SyntheticTransport,
    isolated_async_client_factory,
)


class ProcessPersistentTransport(SyntheticTransport):
    """Persist accepted synthetic ComfyUI jobs outside the backend process."""

    def __init__(self, output_dir: Path, ledger_path: Path, accepted_path: Path,
                 release_path: Path, *, hold_first_prompt: bool):
        super().__init__(output_dir)
        self.ledger_path = ledger_path
        self.accepted_path = accepted_path
        self.release_path = release_path
        self.hold_first_prompt = hold_first_prompt
        self.prompt_post_count = 0
        self._restore_remote_history()

    async def __call__(self, request: httpx.Request):
        if request.url.host == "synthetic-comfy.invalid" and request.method == "POST" and request.url.path == "/prompt":
            body = json.loads(request.content or b"{}")
            prompt_id = body.get("prompt_id")
            if not isinstance(prompt_id, str) or not prompt_id:
                raise RuntimeError("The synthetic ComfyUI requires a caller prompt ID.")
            existing = self.histories.get(prompt_id)
            if existing is None:
                response = super().__call__(request)
                record = self.histories[prompt_id]
                image = next(
                    image for output in record.get("outputs", {}).values()
                    for image in output.get("images", [])
                )
                relative_output = Path(image["subfolder"]) / image["filename"]
                output_path = self.output_dir / relative_output
                output_digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
                durable_record = {
                    "kind": "comfy_prompt",
                    "prompt_id": prompt_id,
                    "request": body,
                    "response": response.json(),
                    "history": record,
                    "output": str(relative_output).replace("\\", "/"),
                    "output_sha256": output_digest,
                    "request_sha256": hashlib.sha256(bytes(request.content or b"")).hexdigest(),
                }
            else:
                # This fake remote accepts duplicate caller IDs so a replay is
                # observable in the durable POST ledger instead of being hidden
                # behind an idempotency guarantee the real protocol lacks.
                response = self._response(request, 200, {
                    "prompt_id": prompt_id,
                    "number": len(self.comfy_prompts) + self.prompt_post_count + 1,
                })
                durable_record = {
                    "kind": "comfy_prompt",
                    "prompt_id": prompt_id,
                    "request": body,
                    "response": response.json(),
                    "duplicate_caller_id": True,
                    "request_sha256": hashlib.sha256(bytes(request.content or b"")).hexdigest(),
                }
            self.prompt_post_count += 1
            durable_record["sequence"] = self.prompt_post_count
            self._append_remote_record(durable_record)
            if self.prompt_post_count == 1:
                self.accepted_path.parent.mkdir(parents=True, exist_ok=True)
                with self.accepted_path.open("w", encoding="utf-8") as marker:
                    marker.write(json.dumps(durable_record, sort_keys=True))
                    marker.flush()
                    os.fsync(marker.fileno())
                if self.hold_first_prompt:
                    await asyncio.to_thread(self._wait_for_release)
            return response
        return super().__call__(request)

    def _append_remote_record(self, entry: dict) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8", newline="\n") as ledger:
            ledger.write(json.dumps(entry, sort_keys=True) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())

    def _restore_remote_history(self) -> None:
        if not self.ledger_path.exists():
            return
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            self.prompt_post_count += 1
            if entry.get("kind") != "comfy_prompt" or entry.get("duplicate_caller_id"):
                continue
            relative_output = Path(entry["output"])
            if relative_output.is_absolute() or ".." in relative_output.parts:
                raise RuntimeError("The persisted synthetic output path is unsafe.")
            output_path = self.output_dir / relative_output
            if not output_path.is_file() or hashlib.sha256(output_path.read_bytes()).hexdigest() != entry["output_sha256"]:
                raise RuntimeError("The persisted remote output is missing or changed after backend restart.")
            request = entry["request"]
            prompt_id = entry["prompt_id"]
            graph = request["prompt"]
            save_node = next(
                node for node in graph.values()
                if isinstance(node, dict) and node.get("class_type") == "SaveImage"
            )
            prefix = save_node["inputs"]["filename_prefix"]
            self.output_names.add(prefix)
            self.comfy_prompts.append({
                "prompt_id": prompt_id,
                "filename_prefix": prefix,
                "graph": graph,
            })
            self.histories[prompt_id] = entry["history"]

    def _wait_for_release(self) -> None:
        while not self.release_path.exists():
            time.sleep(0.02)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--accepted-marker", required=True, type=Path)
    parser.add_argument("--release-marker", required=True, type=Path)
    parser.add_argument("--hold-first-prompt", action="store_true",
                        help="Pause after the first ComfyUI job and output are durably accepted.")
    return parser.parse_args(argv)


def main_cli(argv=None) -> int:
    args = _parse_args(argv)
    providers = ProcessPersistentTransport(
        args.output_dir,
        args.ledger,
        args.accepted_marker,
        args.release_marker,
        hold_first_prompt=args.hold_first_prompt,
    )
    original_async_client = httpx.AsyncClient
    httpx.AsyncClient = isolated_async_client_factory(providers, original_async_client)
    import main as backend_main
    import uvicorn

    uvicorn.run(
        backend_main.app,
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
