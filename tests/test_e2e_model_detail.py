"""Safety checks for the narrowly owned ModelDetail write boundary."""
import asyncio
import json
from pathlib import Path

import httpx
import main
import pytest
from starlette.requests import Request

from scripts.e2e_harness.model_detail import ModelDetailFixture
from scripts.e2e_harness.server import HarnessIsolationMiddleware


def test_model_fixture_limits_real_writes_and_cleans_only_owned_models(client):
    unrelated = client.post("/api/models", json={"name": "Unrelated synthetic model"}).json()["id"]
    database = Path(main.db.conn().execute("PRAGMA database_list").fetchone()["file"])
    fixture = ModelDetailFixture(main, database.parent.parent)
    denials = []
    app = HarnessIsolationMiddleware(main.app, fixture.root, denials, model_fixture=fixture)

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic-harness.invalid") as transport:
            assert (await transport.patch(f"/api/models/{unrelated}", json={"name": "Forbidden"})).status_code == 403
            created = await fixture.control(Request({"type": "http", "method": "POST"}))
            a, b = json.loads(created.body)["models"]
            assert a["id"] != b["id"] and unrelated not in fixture.ids
            assert (await fixture.control(Request({"type": "http", "method": "POST"}))).status_code == 409
            for method, path in (
                ("PATCH", f"/api/models/{unrelated}"),
                ("PATCH", f"/api/models/{a['id']}?outside=1"),
                ("DELETE", f"/api/models/{a['id']}"),
                ("POST", "/api/models"),
                ("PATCH", "/api/config"),
                ("POST", "/api/sessions"),
                ("GET", "/api/loras/preview?name=../outside/sentinel.txt"),
            ):
                assert (await transport.request(method, path, json={"name": "Forbidden"})).status_code == 403
            payload = {**a, "name": "Explicit synthetic edit"}
            assert (await transport.patch(f"/api/models/{a['id']}", json=payload)).status_code == 200
            assert (await transport.get(f"/api/models/{a['id']}")).json() == payload
            assert (await transport.get(f"/api/models/{b['id']}")).json() == b
            assert fixture.operations == [{"method": "PATCH", "path": f"/api/models/{a['id']}", "payload": payload, "status": 200}]
            assert (await transport.patch(f"/api/models/{b['id']}", content=b"invalid JSON", headers={"content-type": "application/json"})).status_code == 422
            assert fixture.operations[-1]["status"] == 422
            assert (await transport.get(f"/api/models/{b['id']}")).json() == b
            await fixture.control(Request({"type": "http", "method": "DELETE"}))
            assert fixture.ids == []
            assert (await transport.get(f"/api/models/{a['id']}")).status_code == 403
        assert main.get_model(unrelated)["name"] == "Unrelated synthetic model"
        assert len(main.list_models()) == 1

    asyncio.run(check())


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_model_fixture_refuses_database_outside_its_root_before_any_control(client, tmp_path, method):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "idevgen.db").write_text("Synthetic outside-root sentinel\n", encoding="utf-8")
    fixture = ModelDetailFixture(main, tmp_path)
    before = main.list_models()
    with pytest.raises(RuntimeError, match="escaped"):
        asyncio.run(fixture.control(Request({"type": "http", "method": method})))
    assert main.list_models() == before
    assert fixture.ids == []
    assert (tmp_path / "data" / "idevgen.db").read_text(encoding="utf-8") == "Synthetic outside-root sentinel\n"
