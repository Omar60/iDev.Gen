"""Owned model fixtures and real PATCH evidence for browser identity tests."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.responses import JSONResponse


FIXTURE_PATH = "/__e2e/model-detail"


class ModelDetailFixture:
    def __init__(self, backend, root: Path):
        self.backend = backend
        self.root = root
        self.ids: list[int] = []
        self.operations: list[dict] = []

    def allows(self, method: str, path: str, query: bytes) -> bool:
        if query:
            return False
        if path == FIXTURE_PATH:
            return method in {"GET", "POST", "DELETE"}
        if self.ids and path == "/api/rooms":
            return method == "GET"
        return method in {"GET", "PATCH"} and path in {f"/api/models/{mid}" for mid in self.ids}

    async def control(self, request):
        database = Path(self.backend.db.conn().execute("PRAGMA database_list").fetchone()["file"]).resolve(strict=True)
        if database != (self.root / "data" / "idevgen.db").resolve(strict=True):
            raise RuntimeError("Model fixture database escaped the owned E2E root.")
        if request.method == "POST":
            if self.ids:
                return JSONResponse({"detail": "Model fixture is already active."}, status_code=409)
            self.operations.clear()
            for letter in ("A", "B"):
                model = self.backend.ModelIn(
                    name=f"E2E Model {letter}", trigger=f"synthetic_{letter.lower()}",
                    base_positive=f"Synthetic {letter} base prompt", settings={"steps": 8},
                )
                self.ids.append(self.backend.create_model(model)["id"])
        elif request.method == "DELETE":
            for mid in self.ids:
                self.backend.delete_model(mid)
            self.ids.clear()
        return JSONResponse({
            "models": [self.backend.get_model(mid) for mid in self.ids],
            "operations": self.operations,
        })

    async def record_patch(self, app, scope, receive, send):
        body = bytearray()
        status = None

        async def read():
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
            return message

        async def write(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                try:
                    payload = json.loads(body)
                except ValueError:
                    payload = None
                self.operations.append({
                    "method": "PATCH", "path": scope["path"],
                    "payload": payload, "status": status,
                })
            await send(message)

        await app(scope, read, write)
