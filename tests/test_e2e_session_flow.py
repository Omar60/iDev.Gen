"""Safety checks for the isolated two-session browser E2E fixture."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import main
import pytest
from starlette.requests import Request

from scripts.e2e_harness.server import HarnessIsolationMiddleware
from scripts.e2e_harness.session_flow import FIXTURE_PATH, SessionFlowFixture


def _control_request(method: str) -> Request:
    return Request({
        "type": "http", "method": method, "path": FIXTURE_PATH,
        "headers": [], "query_string": b"",
    })


def test_session_flow_fixture_scopes_writes_audits_real_rows_and_restores_counts(client):
    root = Path(main.DATA_DIR).parent
    fixture = SessionFlowFixture(main, root)
    baseline = fixture._counts()
    created = asyncio.run(fixture.control(_control_request("POST")))
    assert created.status_code == 200
    manifest = json.loads(created.body)
    assert [item["plan_revision"] for item in manifest["sessions"]] == [1, 1]
    assert [item["reviewed_revision"] for item in manifest["sessions"]] == [1, 1]
    assert [item["ready_take_ids"] for item in manifest["sessions"]] == [
        ["take-001", "take-002"], ["take-001", "take-002"],
    ]
    assert [item["incomplete_take_ids"] for item in manifest["sessions"]] == [
        ["take-003"], ["take-003"],
    ]
    assert manifest["baseline_counts"] == baseline
    assert manifest["counts"] == {
        **baseline,
        "models": baseline["models"] + 1,
        "sessions": baseline["sessions"] + 2,
        "workflows": baseline["workflows"] + 1,
        "plans": baseline["plans"] + 2,
        "approvals": baseline["approvals"] + 2,
        "prepared_takes": baseline["prepared_takes"] + 4,
    }

    denials = []
    app = HarnessIsolationMiddleware(
        main.app, root, denials, session_flow_fixture=fixture,
    )
    first, second = manifest["sessions"]
    try:
        async def exercise():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://synthetic-harness.invalid",
            ) as transport:
                assert (await transport.get(f"/api/sessions/{first['id']}")).status_code == 200
                assert (await transport.get(f"/api/sessions/{first['id']}/plan")).status_code == 200
                assert (await transport.get(
                    f"/api/sessions/{first['id']}/plan/review?plan_revision=1",
                )).status_code == 200
                assert (await transport.get(
                    f"/api/sessions/{first['id']}/plan/takes/take-001/review?plan_revision=1",
                )).status_code == 200

                for method, path, payload in (
                    ("GET", "/api/sessions/99999999", None),
                    ("GET", f"/api/sessions/{first['id']}/plan?plan_revision=2", None),
                    ("POST", f"/api/sessions/{first['id']}/plan/review/approve", {"plan_revision": 1}),
                    ("POST", f"/api/sessions/{first['id']}/plan/approve", {"plan_revision": 1}),
                    ("POST", f"/api/sessions/{first['id']}/run", {}),
                    ("POST", f"/api/sessions/{second['id']}/plan/takes/take-003/prepare", {
                        "plan_revision": 2, "manual_completion": None, "adaptations": None,
                    }),
                ):
                    response = await transport.request(method, path, json=payload)
                    assert response.status_code == 403

                prepared = await transport.post(
                    f"/api/sessions/{first['id']}/plan/takes/take-003/prepare",
                    json={"plan_revision": 1, "manual_completion": None, "adaptations": None},
                )
                assert prepared.status_code == 200, prepared.text
                assert prepared.json()["status"] == "ready"

                submitted = await transport.post(
                    f"/api/sessions/{first['id']}/plan/preparations/submit-selected",
                    json={"plan_revision": 1, "take_ids": ["take-001"]},
                )
                assert submitted.status_code == 200, submitted.text
                assert len(submitted.json()["submitted"]) == 1

        asyncio.run(exercise())
        snapshot = fixture.snapshot()
        assert len(snapshot["plans"]) == 2
        assert len(snapshot["prepared_takes"]) == 5
        generated = next(
            row for row in snapshot["prepared_takes"]
            if row["session_id"] == first["id"] and row["take_id"] == "take-001"
        )
        assert generated["status"] == "generated"
        assert generated["linked_shot_id"] == snapshot["shots"][0]["id"]
        assert len(snapshot["shots"]) == 1
        shot = snapshot["shots"][0]
        assert shot["id"] == generated["linked_shot_id"]
        assert shot["session_id"] == first["id"]
        assert shot["status"] == "pending"
        assert shot["prompt"]
        assert [request["method"] for request in snapshot["requests"] if request["method"] == "POST"] == [
            "POST", "POST",
        ]
        assert [request["path"] for request in snapshot["requests"] if request["method"] == "POST"] == [
            f"/api/sessions/{first['id']}/plan/takes/take-003/prepare",
            f"/api/sessions/{first['id']}/plan/preparations/submit-selected",
        ]
        assert len(denials) == 6
        assert snapshot["authoring_operations"] == []
    finally:
        cleaned = asyncio.run(fixture.control(_control_request("DELETE")))
        result = json.loads(cleaned.body)
        assert result["cleaned"] is True
        assert result["counts"] == baseline


def test_session_flow_cleanup_surfaces_owned_session_folder_warning(client, monkeypatch):
    fixture = SessionFlowFixture(main, Path(main.DATA_DIR).parent)
    baseline = fixture._counts()
    created = asyncio.run(fixture.control(_control_request("POST")))
    assert created.status_code == 200
    owned_ids = list(fixture.session_ids)
    warned = []

    def warn_for_owned_session(session_id):
        warned.append(session_id)
        return {"ok": True, "warning": "Synthetic session-folder cleanup warning."}

    original_delete = main.delete_session
    monkeypatch.setattr(main, "delete_session", warn_for_owned_session)
    with pytest.raises(RuntimeError, match="could not remove the folder"):
        fixture._cleanup()
    assert warned == [owned_ids[-1]]
    assert fixture.session_ids == owned_ids

    monkeypatch.setattr(main, "delete_session", original_delete)
    cleaned = fixture._cleanup()
    assert cleaned == baseline
