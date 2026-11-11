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


def test_review_fixture_uses_real_read_contracts_and_owned_revision_control(client, monkeypatch):
    from starlette.routing import Route
    from scripts.e2e_harness.model_detail import ModelDetailFixture

    root = Path(main.DATA_DIR).parent
    fixture = SessionFlowFixture(main, root)
    baseline = fixture._counts()
    route = Route(FIXTURE_PATH, fixture.control, methods=["GET", "POST", "PATCH", "DELETE"])
    monkeypatch.setattr(main.app.router, "routes", [route, *main.app.router.routes])
    denials = []
    model_fixture = ModelDetailFixture(main, root)
    app = HarnessIsolationMiddleware(main.app, root, denials, model_fixture, fixture)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic-harness.invalid",
        ) as transport:
            for payload in ({"review": 1}, {"review": False}, {"review": True, "extra": True}):
                assert (await transport.post(FIXTURE_PATH, json=payload)).status_code == 403
                assert fixture._counts() == baseline
            created = await transport.post(FIXTURE_PATH, json={"review": True})
            assert created.status_code == 200
            first, second = created.json()["sessions"]
            assert [first["reviewed_revision"], second["reviewed_revision"]] == [None, None]
            assert first["plan"]["look"] != second["plan"]["look"]
            before = fixture.snapshot()
            assert before["counts"]["approvals"] == baseline["approvals"]
            sid = first["id"]
            path = f"/api/sessions/{sid}/plan"
            try:
                for endpoint in (f"{path}/review", f"{path}/takes/take-001/review"):
                    response = await transport.get(endpoint, params={"plan_revision": 1})
                    assert response.status_code == 200, response.text
                    body = response.json()
                    assert body["session_id"] == sid
                    assert body["plan_revision"] == 1
                    assert body["reviewed_revision"] is None
                    review = body["takes"][0] if "takes" in body else body
                    stored = next(row for row in before["prepared_takes"] if row["session_id"] == sid and row["take_id"] == "take-001")
                    assert review["snapshot"]["id"] == stored["id"]
                    assert review["snapshot"]["final_prompt"] == stored["final_prompt"]
                    assert first["plan"]["look"] in stored["final_prompt"]
                read = fixture.snapshot()
                for key in ("sessions", "counts", "plans", "prepared_takes", "shots", "authoring_operations"):
                    assert read[key] == before[key]
                for suffix in ("/review/approve", "/preparations/submit-selected", "/takes/take-003/prepare"):
                    assert (await transport.post(path + suffix, json={"plan_revision": 1})).status_code == 403
                assert (await transport.post(f"/api/sessions/{sid}/run", json={})).status_code == 403
                for payload in (
                    {"session_id": 99999999, "expected_revision": 1},
                    {"session_id": sid, "expected_revision": True},
                    {"session_id": sid, "expected_revision": 2},
                    {"session_id": sid, "expected_revision": 1, "extra": True},
                ):
                    assert (await transport.patch(FIXTURE_PATH, json=payload)).status_code == 403
                    assert fixture.snapshot()["prepared_takes"] == before["prepared_takes"]
                advanced = await transport.patch(FIXTURE_PATH, json={"session_id": sid, "expected_revision": 1})
                assert advanced.status_code == 200, advanced.text
                assert advanced.json()["plan_revision"] == 2
                assert advanced.json()["reviewed_revision"] is None
                assert fixture._session(second["id"]) == second
                assert (await transport.patch(FIXTURE_PATH, json={"session_id": sid, "expected_revision": 1})).status_code == 409
                assert (await transport.get(f"{path}/review?plan_revision=1")).status_code == 409
                current = await transport.get(f"{path}/review?plan_revision=2")
                assert current.status_code == 200, current.text
                assert current.json()["plan_revision"] == 2
                assert advanced.json()["plan"]["look"] in current.json()["takes"][0]["snapshot"]["final_prompt"]
                assert (await transport.get(f"{path}/review?plan_revision=3")).status_code == 403
                final = fixture.snapshot()
                assert final["revision_controls"] == [{"session_id": sid, "expected_revision": 1, "plan_revision": 2}]
                assert final["shots"] == []
                assert final["counts"]["approvals"] == baseline["approvals"]
                assert final["authoring_operations"] == []
                assert len(final["requests"]) == 4
                assert all(row["method"] == "GET" and row["payload"] is None for row in final["requests"])
                assert model_fixture.operations == []
            finally:
                removed = await transport.delete(FIXTURE_PATH)
                assert removed.status_code == 200
                assert removed.json()["counts"] == baseline
                assert not fixture.review_mode

    asyncio.run(exercise())


def test_approval_fixture_allows_only_owned_explicit_approval(client, monkeypatch):
    from starlette.routing import Route
    from scripts.e2e_harness.model_detail import ModelDetailFixture

    root = Path(main.DATA_DIR).parent
    fixture = SessionFlowFixture(main, root)
    baseline = fixture._counts()
    route = Route(FIXTURE_PATH, fixture.control, methods=["GET", "POST", "PATCH", "DELETE"])
    monkeypatch.setattr(main.app.router, "routes", [route, *main.app.router.routes])
    denials = []
    model_fixture = ModelDetailFixture(main, root)
    app = HarnessIsolationMiddleware(main.app, root, denials, model_fixture, fixture)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic-harness.invalid",
        ) as transport:
            for payload in (
                {"approval": 1}, {"approval": True, "extra": True},
                {"review": True, "approval": True},
            ):
                assert (await transport.post(FIXTURE_PATH, json=payload)).status_code == 403
                assert fixture._counts() == baseline

            created = await transport.post(FIXTURE_PATH, json={"approval": True})
            assert created.status_code == 200
            first, second = created.json()["sessions"]
            before = fixture.snapshot()
            assert [item["reviewed_revision"] for item in (first, second)] == [None, None]
            assert before["approval_rows"] == []
            assert before["counts"]["approvals"] == baseline["approvals"]
            sid = first["id"]
            path = f"/api/sessions/{sid}/plan"
            review = await transport.get(f"{path}/review?plan_revision=1")
            assert review.status_code == 200
            assert review.json()["reviewed_revision"] is None

            for target, payload in (
                (f"/api/sessions/99999999/plan/review/approve", {"plan_revision": 1}),
                (f"{path}/approve", {"plan_revision": 1}),
                (f"{path}/review/approve?plan_revision=1", {"plan_revision": 1}),
                (f"{path}/review/approve", {"plan_revision": True}),
                (f"{path}/review/approve", {"plan_revision": 1, "extra": True}),
                (f"{path}/preparations/submit-selected", {"plan_revision": 1, "take_ids": ["take-001"]}),
                (f"{path}/takes/take-003/prepare", {
                    "plan_revision": 1, "manual_completion": None, "adaptations": None,
                }),
                (f"/api/sessions/{sid}/run", {}),
            ):
                assert (await transport.post(target, json=payload)).status_code == 403

            approved = await transport.post(f"{path}/review/approve", json={"plan_revision": 1})
            assert approved.status_code == 200, approved.text
            assert approved.json()["session_id"] == sid
            assert approved.json()["plan_revision"] == 1
            assert approved.json()["approved"] is True
            duplicate = await transport.post(f"{path}/review/approve", json={"plan_revision": 1})
            assert duplicate.status_code == 200, duplicate.text
            assert fixture.snapshot()["approval_rows"] == [
                {"session_id": sid, "plan_revision": 1, "approved_at": duplicate.json()["approved_at"]},
            ]

            advanced = await transport.patch(FIXTURE_PATH, json={"session_id": sid, "expected_revision": 1})
            assert advanced.status_code == 200
            assert advanced.json()["plan_revision"] == 2
            assert fixture.snapshot()["approval_rows"] == []
            stale = await transport.post(f"{path}/review/approve", json={"plan_revision": 1})
            assert stale.status_code == 409
            current = await transport.post(f"{path}/review/approve", json={"plan_revision": 2})
            assert current.status_code == 200, current.text
            after = fixture.snapshot()
            assert after["approval_rows"] == [{
                "session_id": sid, "plan_revision": 2, "approved_at": current.json()["approved_at"],
            }]
            assert after["sessions"][1]["reviewed_revision"] is None
            assert after["counts"]["approvals"] == baseline["approvals"] + 1
            assert after["shots"] == []
            assert after["authoring_operations"] == []
            assert [
                (item["method"], item["path"], item["status"], item["payload"])
                for item in after["requests"] if item["method"] == "POST"
            ] == [
                ("POST", f"{path}/review/approve", 200, {"plan_revision": 1}),
                ("POST", f"{path}/review/approve", 200, {"plan_revision": 1}),
                ("POST", f"{path}/review/approve", 409, {"plan_revision": 1}),
                ("POST", f"{path}/review/approve", 200, {"plan_revision": 2}),
            ]
            assert [
                (item["method"], item["path"], item["query"], item["status"], item["payload"])
                for item in after["requests"] if item["method"] == "GET"
            ] == [("GET", f"{path}/review", "plan_revision=1", 200, None)]

    try:
        asyncio.run(exercise())
        assert len(denials) == 8
        assert model_fixture.operations == []
    finally:
        cleaned = asyncio.run(fixture.control(_control_request("DELETE")))
        result = json.loads(cleaned.body)
        assert result["cleaned"] is True
        assert result["counts"] == baseline
        assert not fixture.review_mode
        assert not fixture.approval_mode
