"""Owned two-session resource-v1 data and request audit for browser E2E."""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.responses import JSONResponse


FIXTURE_PATH = "/__e2e/session-flow"
TAKE_IDS = ("take-001", "take-002", "take-003")
READY_TAKE_IDS = TAKE_IDS[:2]
INCOMPLETE_TAKE_IDS = TAKE_IDS[2:]
_SESSION_PATH = re.compile(r"^/api/sessions/(\d+)(.*)$")
_TAKE_REVIEW_PATH = re.compile(r"^/api/sessions/(\d+)/plan/takes/([A-Za-z0-9_-]+)/review$")
_TAKE_PREPARE_PATH = re.compile(r"^/api/sessions/(\d+)/plan/takes/([A-Za-z0-9_-]+)/prepare$")


_GRAPH = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "synthetic.safetensors"}},
    "2": {"class_type": "LoraLoader", "inputs": {
        "lora_name": "characters/e2e.synthetic.safetensors", "strength_model": 0.5,
        "strength_clip": 1.0, "model": ["1", 0], "clip": ["1", 1],
    }},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 1]}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 1]}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
    "6": {"class_type": "KSampler", "inputs": {
        "seed": 1, "steps": 8, "cfg": 1.0, "sampler_name": "euler", "scheduler": "normal",
        "denoise": 1.0, "model": ["2", 0], "positive": ["3", 0],
        "negative": ["4", 0], "latent_image": ["5", 0],
    }},
    "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "E2E", "images": ["6", 0]}},
}


def _plan() -> dict:
    return {
        "version": "resource-v1",
        "look": "A quiet synthetic studio with soft side light.",
        "initial_wardrobe": "a plain charcoal shirt and dark trousers",
        "takes": [
            {
                "take_id": take_id,
                "label": f"Synthetic take {index:03d}",
                "camera": "an eye-level synthetic 50mm camera",
                "framing": "a waist-up synthetic frame",
                "pose": "standing in a relaxed synthetic pose",
                "expression": "a calm synthetic expression",
            }
            for index, take_id in enumerate(TAKE_IDS, 1)
        ],
        "selected_resources": [],
        "wardrobe_changes": [],
    }


class SessionFlowFixture:
    """Create and expose only the synthetic sessions owned by this fixture."""

    def __init__(self, backend, root: Path):
        self.backend = backend
        self.root = root.resolve(strict=True)
        self.session_ids: list[int] = []
        self.model_id: int | None = None
        self.workflow_id: int | None = None
        self.requests: list[dict] = []
        self.baseline_counts: dict[str, int] | None = None
        self.review_mode = False
        self.revision_controls: list[dict] = []

    def _database(self) -> Path:
        row = self.backend.db.conn().execute("PRAGMA database_list").fetchone()
        database = Path(row["file"]).resolve(strict=True)
        if database != (self.root / "data" / "idevgen.db").resolve(strict=True):
            raise RuntimeError("Session flow fixture database escaped its owned E2E root.")
        return database

    def _counts(self) -> dict[str, int]:
        self._database()
        tables = {
            "models": "model",
            "sessions": "session",
            "workflows": "workflow",
            "plans": "session_plan",
            "approvals": "session_plan_approval",
            "prepared_takes": "prepared_take",
            "shots": "shot",
            "authoring_operations": "authoring_operation",
        }
        return {
            name: int(self.backend.db.one(f"SELECT COUNT(*) AS n FROM {table}")["n"])
            for name, table in tables.items()
        }

    def _session_folder(self, session_id: int) -> Path:
        sessions_root = Path(self.backend.SESSIONS_DIR).resolve(strict=True)
        folder = sessions_root / str(session_id)
        if not sessions_root.is_relative_to(self.root) or not folder.resolve(strict=False).is_relative_to(self.root):
            raise RuntimeError(f"Session flow cleanup path for owned session {session_id} escaped its E2E root.")
        return folder

    def _create(self) -> None:
        backend = self.backend
        self.workflow_id = backend.create_workflow(backend.WorkflowIn(
            name="E2E Session Flow Workflow", graph=_GRAPH,
        ))["id"]
        self.model_id = backend.create_model(backend.ModelIn(
            name="E2E Session Flow Character",
            lora_name="characters/e2e.synthetic.safetensors",
            trigger="synthetic character",
            base_positive="synthetic photo",
            base_negative="blurry",
            workflow_id=self.workflow_id,
            settings={"width": 512, "height": 512, "steps": 8, "cfg": 1.0},
        ))["id"]

        for name in ("E2E Session A", "E2E Session B"):
            session_id = backend.create_session(backend.SessionIn(
                model_id=self.model_id,
                name=name,
                composition_mode=backend.session_plan.MODE_RESOURCE_V1,
            ))["id"]
            self.session_ids.append(int(session_id))
            plan = _plan()
            if self.review_mode:
                plan["look"] = f"Synthetic Review {name}, revision 1."
            saved = backend.save_plan_draft(
                session_id,
                backend.PlanDraftIn(plan=plan, expected_revision=0),
            )
            if saved["plan_revision"] != 1:
                raise RuntimeError("Session flow fixture plan did not start at revision 1.")
            for take_id in READY_TAKE_IDS:
                prepared = backend.prepare_take_endpoint(
                    session_id,
                    take_id,
                    backend.TakePrepareIn(
                        plan_revision=1, manual_completion=None, adaptations=None,
                    ),
                )
                if prepared["status"] != "ready":
                    raise RuntimeError(f"Session flow fixture take {take_id} did not become ready.")
            if not self.review_mode:
                approval = backend.session_plan.approve_plan_review(session_id, 1)
                if approval["approved"] is not True:
                    raise RuntimeError("Session flow fixture plan review was not approved.")

    def _cleanup(self) -> dict[str, int]:
        backend = self.backend
        for session_id in reversed(self.session_ids):
            folder = self._session_folder(session_id)
            result = backend.delete_session(session_id)
            if not isinstance(result, dict) or result.get("warning") or folder.exists() or folder.is_symlink():
                raise RuntimeError(
                    f"Session flow cleanup could not remove the folder for owned session {session_id}."
                )
        if self.model_id is not None:
            backend.delete_model(self.model_id)
        if self.workflow_id is not None:
            backend.delete_workflow(self.workflow_id)
        counts = self._counts()
        if self.baseline_counts is not None and counts != self.baseline_counts:
            raise RuntimeError(
                f"Session flow cleanup did not restore its database counts: {counts!r}."
            )
        self.session_ids.clear()
        self.model_id = None
        self.workflow_id = None
        self.requests.clear()
        self.baseline_counts = None
        self.review_mode = False
        self.revision_controls.clear()
        return counts

    def _session(self, session_id: int) -> dict:
        backend = self.backend
        current = backend.get_session(session_id)
        plan = backend.get_plan_draft(session_id)
        ready = [item["take_id"] for item in plan["preparation"]["completed"]]
        incomplete = [item["take_id"] for item in plan["preparation"]["incomplete"]]
        return {
            "id": session_id,
            "session": current,
            "plan_revision": int(plan["plan_revision"]),
            "reviewed_revision": backend.session_plan.get_approved_plan_revision(session_id),
            "plan": plan["plan"],
            "ready_take_ids": ready,
            "incomplete_take_ids": incomplete,
        }

    def snapshot(self) -> dict:
        self._database()
        ids = list(self.session_ids)
        if ids:
            placeholders = ",".join("?" for _ in ids)
            params = tuple(ids)
            plans = self.backend.db.q(
                "SELECT * FROM session_plan "
                f"WHERE session_id IN ({placeholders}) ORDER BY session_id",
                *params,
            )
            prepared = self.backend.db.q(
                "SELECT * FROM prepared_take "
                f"WHERE session_id IN ({placeholders}) ORDER BY session_id, take_id",
                *params,
            )
            shots = self.backend.db.q(
                "SELECT id, session_id, status, prompt FROM shot "
                f"WHERE session_id IN ({placeholders}) ORDER BY session_id, id",
                *params,
            )
            operations = self.backend.db.q(
                "SELECT operation_id, session_id, kind, state FROM authoring_operation "
                f"WHERE session_id IN ({placeholders}) ORDER BY session_id, operation_id",
                *params,
            )
        else:
            plans, prepared, shots, operations = [], [], [], []
        return {
            "sessions": [self._session(session_id) for session_id in ids],
            "baseline_counts": self.baseline_counts,
            "counts": self._counts(),
            "requests": list(self.requests),
            "plans": plans,
            "prepared_takes": prepared,
            "shots": shots,
            "authoring_operations": operations,
            "revision_controls": list(self.revision_controls),
        }

    def allows(self, method: str, path: str, query: bytes) -> bool:
        method = method.upper()
        if path == FIXTURE_PATH:
            return (method in {"GET", "POST", "DELETE"} or (
                method == "PATCH" and self.review_mode and bool(self.session_ids)
            )) and not query
        match = _SESSION_PATH.fullmatch(path)
        if match is None or int(match.group(1)) not in self.session_ids:
            return False
        session_id, suffix = match.groups()
        if method == "GET":
            if suffix in {"", "/plan", "/full-auto"}:
                return not query
            if suffix == "/plan/review":
                return query == b"plan_revision=1" or (
                    self.review_mode and query == b"plan_revision=2"
                )
            review = _TAKE_REVIEW_PATH.fullmatch(path)
            if review and int(review.group(1)) == int(session_id):
                return review.group(2) in TAKE_IDS and (query == b"plan_revision=1" or (
                    self.review_mode and query == b"plan_revision=2"
                ))
            return False
        if method == "POST":
            if self.review_mode:
                return False
            if query:
                return False
            prepare = _TAKE_PREPARE_PATH.fullmatch(path)
            if prepare and int(prepare.group(1)) == int(session_id):
                return prepare.group(2) in TAKE_IDS
            return suffix == "/plan/preparations/submit-selected"
        return False

    def is_runtime_path(self, method: str, path: str) -> bool:
        return path != FIXTURE_PATH and self.allows(method, path, b"") or (
            path != FIXTURE_PATH and self.allows(method, path, b"plan_revision=1")
        )

    def body_allowed(self, method: str, path: str, body: bytes) -> bool:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        prepare = _TAKE_PREPARE_PATH.fullmatch(path)
        if method.upper() == "POST" and prepare:
            return payload == {
                "plan_revision": 1,
                "manual_completion": None,
                "adaptations": None,
            }
        if method.upper() == "POST" and path.endswith("/plan/preparations/submit-selected"):
            match = _SESSION_PATH.fullmatch(path)
            if match is None:
                return False
            session_id = int(match.group(1))
            own_takes = set(TAKE_IDS) if session_id in self.session_ids else set()
            targets = payload.get("take_ids") if isinstance(payload, dict) else None
            return (
                isinstance(payload, dict)
                and set(payload) == {"plan_revision", "take_ids"}
                and payload.get("plan_revision") == 1
                and isinstance(targets, list)
                and bool(targets)
                and all(isinstance(item, str) and item in own_takes for item in targets)
                and len(targets) == len(set(targets))
            )
        return True

    async def record_request(self, app, scope, receive, send, denials: list[dict[str, str]]):
        method = scope["method"].upper()
        path = scope["path"]
        body = bytearray()
        messages = []
        if method == "POST":
            while True:
                message = await receive()
                messages.append(message)
                if message["type"] != "http.request":
                    break
                body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            if not self.body_allowed(method, path, bytes(body)):
                denials.append({"method": method, "path": path})
                response = JSONResponse({"detail": "Blocked by the isolated E2E harness."}, status_code=403)
                await response(scope, receive, send)
                return
        cursor = 0

        async def replay():
            nonlocal cursor
            if cursor < len(messages):
                message = messages[cursor]
                cursor += 1
                return message
            return await receive()

        status = None
        captured = False

        async def record(message):
            nonlocal status, captured
            if message["type"] == "http.response.start":
                status = message["status"]
            if message["type"] == "http.response.body" and not message.get("more_body", False) and not captured:
                payload = None
                if method == "POST":
                    try:
                        payload = json.loads(body)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        pass
                self.requests.append({
                    "method": method,
                    "path": path,
                    "query": scope.get("query_string", b"").decode("ascii", errors="replace"),
                    "status": status,
                    "payload": payload,
                })
                captured = True
            await send(message)

        await app(scope, replay, record)

    async def control(self, request):
        self._database()
        if request.method == "PATCH":
            try:
                payload = await request.json()
            except (ValueError, UnicodeDecodeError):
                return JSONResponse({"detail": "Invalid owned Review revision control."}, status_code=403)
            if (not self.review_mode or not isinstance(payload, dict)
                    or set(payload) != {"session_id", "expected_revision"}
                    or type(payload["session_id"]) is not int
                    or payload["session_id"] not in self.session_ids
                    or type(payload["expected_revision"]) is not int
                    or payload["expected_revision"] != 1):
                return JSONResponse({"detail": "Invalid owned Review revision control."}, status_code=403)
            sid = payload["session_id"]
            current = self.backend.get_plan_draft(sid)
            if current["plan_revision"] != 1:
                return JSONResponse({"detail": "Review fixture revision already advanced."}, status_code=409)
            plan = current["plan"]
            plan["look"] = f"Synthetic Review session {sid}, revision 2."
            self.backend.save_plan_draft(sid, self.backend.PlanDraftIn(plan=plan, expected_revision=1))
            for take_id in READY_TAKE_IDS:
                self.backend.prepare_take_endpoint(sid, take_id, self.backend.TakePrepareIn(
                    plan_revision=2, manual_completion=None, adaptations=None,
                ))
            self.revision_controls.append({**payload, "plan_revision": 2})
            return JSONResponse(self._session(sid))
        if request.method == "POST":
            if self.session_ids or self.baseline_counts is not None:
                return JSONResponse({"detail": "Session flow fixture is already active."}, status_code=409)
            try:
                payload = await request.json() if request.headers.get("content-type") == "application/json" else {}
            except (ValueError, UnicodeDecodeError):
                return JSONResponse({"detail": "Invalid session fixture mode."}, status_code=403)
            if not isinstance(payload, dict) or (payload and (
                set(payload) != {"review"} or payload["review"] is not True
            )):
                return JSONResponse({"detail": "Invalid session fixture mode."}, status_code=403)
            self.baseline_counts = self._counts()
            self.review_mode = payload == {"review": True}
            self.requests.clear()
            try:
                self._create()
            except Exception:
                self._cleanup()
                raise
            return JSONResponse(self.snapshot())
        if request.method == "DELETE":
            if self.baseline_counts is None:
                return JSONResponse({"cleaned": True, "counts": self._counts()})
            baseline = dict(self.baseline_counts)
            counts = self._cleanup()
            return JSONResponse({"cleaned": counts == baseline, "baseline_counts": baseline, "counts": counts})
        return JSONResponse(self.snapshot())
