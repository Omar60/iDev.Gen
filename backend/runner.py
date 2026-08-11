"""Serial session runner: one shot at a time through ComfyUI's queue.

Serial on purpose — one GPU, and a session is a photo shoot, not a benchmark:
shots land in order so the gallery fills top to bottom. Each shot is committed to
the DB *before* being queued, so a crash leaves a visible failed row, never an
orphan job in ComfyUI.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import shutil
import uuid
from contextlib import suppress
from pathlib import Path

import db
from comfy import Comfy, apply_map, output_images

log = logging.getLogger("idevgen.runner")

CLIENT_ID = f"idevgen-{uuid.uuid4().hex[:8]}"
POLL_SECONDS = 1.5
SHOT_TIMEOUT = 900  # 15 min per image; a hung job must not wedge the session
# Retries for a locked output file. Delays escalate (0.4, 0.8, 1.2 …), so six
# attempts wait ~8s in total before the shot is called failed.
MOVE_ATTEMPTS = 6
MOVE_RETRY_DELAY = 0.4


class Runner:
    def __init__(self, comfy: Comfy, sessions_dir: Path, comfy_output_dir: Path):
        self.comfy = comfy
        self.sessions_dir = sessions_dir
        self.comfy_output_dir = comfy_output_dir
        self._task: asyncio.Task | None = None
        self._cancel: set[int] = set()

    # -- control ----------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, session_id: int) -> None:
        if self.busy:
            raise RuntimeError("A session is already running")
        self._cancel.discard(session_id)
        db.run("UPDATE session SET status='running' WHERE id=?", session_id)
        self._task = asyncio.create_task(self._run_session(session_id))

    def cancel(self, session_id: int) -> None:
        self._cancel.add(session_id)

    def current_session(self) -> int | None:
        row = db.one("SELECT id FROM session WHERE status='running' LIMIT 1")
        return row["id"] if row else None

    # -- loop -------------------------------------------------------------

    async def _run_session(self, session_id: int) -> None:
        try:
            while True:
                shot = db.one(
                    "SELECT * FROM shot WHERE session_id=? AND status='pending' ORDER BY id LIMIT 1",
                    session_id,
                )
                if shot is None:
                    break
                if session_id in self._cancel:
                    db.run(
                        "UPDATE shot SET status='cancelled' WHERE session_id=? AND status='pending'",
                        session_id,
                    )
                    break
                await self._run_shot(session_id, shot)
        except Exception as exc:  # noqa: BLE001 - the loop must never die silently
            log.exception("session %s crashed", session_id)
            db.run("UPDATE session SET status='failed' WHERE id=?", session_id)
            db.run(
                "UPDATE shot SET status='failed', error=? WHERE session_id=? AND status IN ('pending','running')",
                str(exc)[:500], session_id,
            )
            return
        cancelled = session_id in self._cancel
        self._cancel.discard(session_id)
        db.run("UPDATE session SET status=? WHERE id=?", self._final_status(session_id, cancelled), session_id)

    @staticmethod
    def _final_status(session_id: int, cancelled: bool) -> str:
        """`done` means the queue emptied, not that anything came out of it — so a
        session where every shot failed is `failed`, and one with a single keeper
        among failures is still `done`, because it produced photos."""
        if cancelled:
            return "cancelled"
        counts = db.one(
            """SELECT COALESCE(SUM(status='done'), 0) AS done,
                      COALESCE(SUM(status='failed'), 0) AS failed
               FROM shot WHERE session_id=?""", session_id)
        return "failed" if counts["failed"] and not counts["done"] else "done"

    async def _run_shot(self, session_id: int, shot: dict) -> None:
        session = db.jload(db.one("SELECT * FROM session WHERE id=?", session_id), "settings")
        model = db.one("SELECT * FROM model WHERE id=?", session["model_id"])
        wf_id = session["workflow_id"] or model["workflow_id"]
        wf = db.jload(db.one("SELECT * FROM workflow WHERE id=?", wf_id), "graph", "node_map") if wf_id else None
        if not wf:
            raise RuntimeError("The session has no workflow assigned")

        s = session["settings"]
        seed = shot["seed"] or random.randint(1, 2**31 - 1)
        # The random suffix is what stops ComfyUI from serving a cached SaveImage:
        # an identical graph returns `execution_cached` with the previous run's
        # filename and writes NO file. Reachable for real after wiping data/ (shot
        # ids restart at 1) while ComfyUI still holds its cache. Only the SaveImage
        # node is invalidated; the sampler above it still reuses its cache.
        prefix = f"idevgen/{session_id}/{shot['id']}_{uuid.uuid4().hex[:6]}"
        values = {
            "positive": shot["prompt"],
            "negative": shot["negative"],
            "seed": seed,
            "steps": s.get("steps"),
            "cfg": s.get("cfg"),
            "width": s.get("width"),
            "height": s.get("height"),
            # Empty means "whatever the workflow already loads" — same rule as
            # every other slot, so one workflow per family is enough.
            "checkpoint": s.get("checkpoint") or None,
            "lora_name": model["lora_name"] or None,
            "lora_strength": s.get("lora_strength", model["lora_strength"]),
            "filename_prefix": prefix,
        }
        graph = apply_map(wf["graph"], wf["node_map"], values)

        db.run("UPDATE shot SET status='running', seed=? WHERE id=?", seed, shot["id"])
        try:
            prompt_id = await self.comfy.queue_prompt(graph, CLIENT_ID)
        except Exception as exc:  # noqa: BLE001 - a rejected graph fails one shot, not the run
            db.run("UPDATE shot SET status='failed', error=?, finished_at=? WHERE id=?",
                   str(exc)[:500], db.now(), shot["id"])
            return
        db.run("UPDATE shot SET prompt_id=? WHERE id=?", prompt_id, shot["id"])

        history = await self._await_history(prompt_id, session_id)
        if history is None:
            await self.comfy.interrupt()
            db.run("UPDATE shot SET status='cancelled', finished_at=? WHERE id=?", db.now(), shot["id"])
            return

        images = output_images(history)
        if not images:
            msg = _history_error(history) or "ComfyUI returned no image"
            db.run("UPDATE shot SET status='failed', error=?, finished_at=? WHERE id=?",
                   msg[:500], db.now(), shot["id"])
            return

        try:
            filename = await self._collect(session_id, shot, images[0])
        except OSError as exc:  # disk full, misconfigured path, file already moved
            db.run("UPDATE shot SET status='failed', error=?, finished_at=? WHERE id=?",
                   str(exc)[:500], db.now(), shot["id"])
            return
        db.run("UPDATE shot SET status='done', filename=?, finished_at=? WHERE id=?",
               filename, db.now(), shot["id"])

    async def _await_history(self, prompt_id: str, session_id: int) -> dict | None:
        """Poll until the job leaves ComfyUI's queue. None = cancelled/timeout."""
        waited = 0.0
        while waited < SHOT_TIMEOUT:
            if session_id in self._cancel:
                return None
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS
            try:
                history = await self.comfy.history(prompt_id)
            except Exception:  # ComfyUI restarting / transient — keep polling
                continue
            if history and history.get("status", {}).get("completed") is not None:
                return history
            if history and history.get("outputs"):
                return history
        raise TimeoutError(f"The shot ran past {SHOT_TIMEOUT}s without finishing")

    async def _collect(self, session_id: int, shot: dict, image: dict) -> str:
        """Move the rendered file out of ComfyUI's output into the session folder.

        The move is retried because on Windows the PNG is briefly locked by
        whoever still holds it the instant ComfyUI reports it done — ComfyUI
        itself closing the handle, an antivirus scanning a freshly written file,
        a thumbnailer. That is a sharing violation (WinError 32), it clears in
        well under a second, and failing the shot over it wastes a real
        generation.
        """
        src = self.comfy_output_dir / (image.get("subfolder") or "") / image["filename"]
        dest_dir = self.sessions_dir / str(session_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = f"{shot['id']:05d}_{_slug(shot['shot_label'])}{Path(image['filename']).suffix}"
        dest = dest_dir / name

        for attempt in range(1, MOVE_ATTEMPTS + 1):
            try:
                shutil.move(str(src), str(dest))
                break
            except FileNotFoundError as exc:
                raise FileNotFoundError(f"Generated file not found: {src}") from exc
            except PermissionError:
                if attempt == MOVE_ATTEMPTS:
                    raise
                log.info("shot %s: file locked, retry %s/%s", shot["id"], attempt, MOVE_ATTEMPTS)
                await asyncio.sleep(MOVE_RETRY_DELAY * attempt)

        self._prune_empty(src.parent)
        return name

    def _prune_empty(self, folder: Path) -> None:
        """Drop idevgen/<session>/ and its parent once empty: moving a file means
        leaving no trace in ComfyUI's output. Never climbs above that output."""
        root = self.comfy_output_dir.resolve()
        current = folder.resolve()
        while current != root and root in current.parents:
            with suppress(OSError):   # OSError = not empty; stop there
                current.rmdir()
            if current.exists():
                return
            current = current.parent


def _history_error(history: dict) -> str:
    """The one line worth showing on the failed shot.

    ComfyUI's error message is a dict with a traceback, the whole prompt and the
    node's inputs in it. Dumped raw it fills the card with JSON and buries the
    sentence that says what broke, so only that sentence is kept.
    """
    for kind, payload in history.get("status", {}).get("messages", []) or []:
        if "error" not in kind:
            continue
        if not isinstance(payload, dict):
            return str(payload)[:400]
        message = (payload.get("exception_message") or "").strip()
        if not message:
            return str(payload)[:400]
        node = payload.get("node_type") or payload.get("node_id") or ""
        kind_of = payload.get("exception_type", "")
        head = " · ".join(p for p in (node, kind_of) if p)
        return (f"{head}: {message}" if head else message)[:400]
    return ""


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "shot").strip()).strip("-").lower()
    return (slug or "shot")[:40]
