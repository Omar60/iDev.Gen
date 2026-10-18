"""Independent Group 3 contract checks joining real frontend payload helpers to FastAPI."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

import db
import main
from backend import photo_staging, saved_looks


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
VITEST_CLI = FRONTEND / "node_modules" / "vitest" / "vitest.mjs"
_REGIONS = ("chest", "hips", "legs", "feet")
_PHOTO_TABLES = ("photo_look_proposal", "photo_look_automation_proposal", "look_photo_stage")


def _enabled_config(monkeypatch):
    config = {
        **main.CONFIG,
        "resource_planning_enabled": True,
        "llm_url": "https://assistant.example.invalid/v1",
        "llm_model": "invented-text-model",
        "llm_vision_model": "invented-vision-model",
        "llm_key": "invented-test-key",
    }
    monkeypatch.setattr(main, "CONFIG", config)
    return config


def _run_frontend_test(workdir: Path, input_path: Path, output_path: Path, mode: str, *, pattern: str | None = None):
    node = shutil.which("node")
    assert node, "Node.js is required by the frontend package declared in CI."
    assert VITEST_CLI.is_file(), "Install frontend dependencies before running the real frontend helper."
    env = os.environ.copy()
    env.update({
        "IDEVGEN_G3_INPUT_FILE": str(input_path),
        "IDEVGEN_G3_OUTPUT_FILE": str(output_path),
        "IDEVGEN_G3_MODE": mode,
        "TEMP": str(workdir),
        "TMP": str(workdir),
        "TMPDIR": str(workdir),
    })
    command = [
        node, str(VITEST_CLI), "run", "--pool=threads", "--maxWorkers=1",
    ]
    if pattern:
        command.extend(["--testNamePattern", pattern])
    command.append("src/views/Looks.transport.g3.acceptance.test.js")
    completed = subprocess.run(
        command,
        cwd=FRONTEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        "The real frontend helper did not produce a contract payload.\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return completed


def _run_frontend_helper(workdir: Path, value: dict) -> dict:
    input_path = workdir / "frontend-input.json"
    output_path = workdir / "frontend-output.json"
    input_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    _run_frontend_test(workdir, input_path, output_path, "profile")
    assert output_path.is_file(), "The frontend helper produced no output fixture."
    return json.loads(output_path.read_text(encoding="utf-8"))


def _automation(profile: dict, *, suggestion: dict | None = None) -> dict:
    result = {
        "profile": profile,
        "profile_review_confirmed": True,
        "inventory_complete_confirmed": True,
        "baseline_coverage_confirmed": True,
        "removal_order_confirmed": True,
    }
    if suggestion is not None:
        result["suggestion"] = suggestion
    return result


def _garments_from_outfit(outfit: dict) -> list[dict]:
    return [
        {"key": item["key"], "wording": item["wording"], "aside": item["aside"]}
        for item in outfit["garments"]
    ]


def _image_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (5, 4), (37, 83, 149)).save(stream, format="PNG")
    return stream.getvalue()


def _upload(client: TestClient) -> str:
    response = client.post(
        "/api/looks/photo-stages",
        files={"file": ("invented-photo.png", _image_bytes(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["photo_id"]


@pytest.fixture
def g3_temp_directory():
    path = Path(tempfile.mkdtemp(prefix=".tmp-g3-contract-", dir=ROOT)).resolve()
    try:
        yield path
    finally:
        if path.parent != ROOT.resolve() or not path.name.startswith(".tmp-g3-contract-"):
            raise AssertionError("Refusing to clean an unexpected temporary directory.")
        shutil.rmtree(path)


@pytest.fixture
def clean_g3_photo_rows(client):
    before = {row["photo_id"] for row in db.q("SELECT photo_id FROM look_photo_stage")}
    yield
    for row in db.q("SELECT photo_id, staged_path FROM look_photo_stage"):
        if row["photo_id"] not in before:
            photo_staging._safe_unlink(row["photo_id"], row["staged_path"])
            for table in _PHOTO_TABLES[:2]:
                db.run(f"DELETE FROM {table} WHERE photo_id = ?", row["photo_id"])
            db.run("DELETE FROM look_photo_stage WHERE photo_id = ?", row["photo_id"])


def test_frontend_profiles_execute_against_real_combined_save_routes(
    client, monkeypatch, g3_temp_directory, clean_g3_photo_rows,
):
    _enabled_config(monkeypatch)
    unicode_appearance = "Short dark hair e\u0301 ✨ with a faint beauty mark."
    manual_garments = [
        {"key": None, "wording": "a white shirt", "aside": "the shirt folded over one arm"},
        {"key": None, "wording": "blue jeans", "aside": ""},
    ]

    # Exercise the same profile builder consumed by Looks.jsx and pass its
    # exact output through the real FastAPI write route.
    helper = _run_frontend_helper(g3_temp_directory, {
        "appearance": unicode_appearance,
        "garments": manual_garments,
    })
    manual_body = {
        "name": "Frontend reviewed manual look",
        "appearance": unicode_appearance,
        "garments": manual_garments,
        "request_id": str(uuid4()),
        "automation": _automation(helper["profile"]),
    }
    before_refusal = {
        table: db.q(f"SELECT * FROM {table} ORDER BY rowid")
        for table in (
            "outfit", "saved_look_version", "saved_look_automation_profile",
            "saved_look_combined_save_receipt",
        )
    }
    refused_body = deepcopy(manual_body)
    refused_body["automation"]["profile_review_confirmed"] = False
    refused = client.post("/api/looks", json=refused_body)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "automation_review_required"
    assert {
        table: db.q(f"SELECT * FROM {table} ORDER BY rowid")
        for table in before_refusal
    } == before_refusal

    # A conflicting reviewed baseline is a known incomplete profile and must
    # refuse the pair atomically, without falling back to a look-only write.
    baseline_conflict = deepcopy(manual_body)
    baseline_conflict["request_id"] = str(uuid4())
    baseline_conflict["automation"]["profile"]["baseline_coverage"]["regions"]["chest"] = "partial"
    conflict_response = client.post("/api/looks", json=baseline_conflict)
    assert conflict_response.status_code == 422, conflict_response.text
    assert conflict_response.json()["detail"]["code"] == "automation_profile_not_ready"
    assert {
        table: db.q(f"SELECT * FROM {table} ORDER BY rowid")
        for table in before_refusal
    } == before_refusal

    # Discard the first response, as if it were lost after commit, then replay
    # the identical frontend-generated request and UUID.
    lost_response = client.post("/api/looks", json=manual_body)
    assert lost_response.status_code == 200, lost_response.text
    expected_manual = lost_response.json()
    replay_manual = client.post("/api/looks", json=manual_body)
    assert replay_manual.status_code == 200, replay_manual.text
    assert replay_manual.json() == expected_manual
    assert expected_manual["automation_profile"]["readiness"]["profile_ready"] is True
    identity = expected_manual["automation_profile"]
    persisted = client.get("/api/looks/automation-profiles", params={
        "look_key": identity["look_key"],
        "version": identity["version"],
        "content_digest": identity["content_digest"],
        "profile_revision": identity["profile_revision"],
        "profile_digest": identity["profile_digest"],
    })
    assert persisted.status_code == 200, persisted.text
    exact_profile = persisted.json()
    assert exact_profile["look"] == {
        "key": expected_manual["key"],
        "version": expected_manual["version"],
        "content_digest": expected_manual["content_digest"],
    }
    assert exact_profile["appearance_segments"] == [{
        **exact_profile["appearance_segments"][0],
        "start": 0,
        "end": len(unicode_appearance),
        "text": unicode_appearance,
        "owner": "permanent",
    }]
    assert set(exact_profile["garment_coverage"]) == {
        garment["key"] for garment in expected_manual["outfit"]["garments"]
    }
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_combined_save_receipt")["n"] == 1
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_combined_save_receipt")["n"] == 1

    # Outfit conversion uses the actual saved outfit rows and its API identity.
    base = client.post("/api/looks", json={
        "name": "Conversion source outfit",
        "appearance": unicode_appearance,
        "garments": manual_garments,
    })
    assert base.status_code == 200, base.text
    source = base.json()
    source_garments = _garments_from_outfit(source["outfit"])
    conversion_helper = _run_frontend_helper(g3_temp_directory, {
        "appearance": unicode_appearance,
        "garments": source_garments,
    })
    conversion_body = {
        "name": "Converted outfit look",
        "appearance": unicode_appearance,
        "outfit_key": source["outfit"]["outfit_key"],
        "request_id": str(uuid4()),
        "automation": _automation(conversion_helper["profile"]),
    }
    converted = client.post("/api/looks", json=conversion_body)
    assert converted.status_code == 200, converted.text
    converted_result = converted.json()
    assert converted_result["outfit"]["outfit_key"] == source["outfit"]["outfit_key"]
    assert converted_result["automation_profile"]["readiness"]["profile_ready"] is True
    converted_replay = client.post("/api/looks", json=conversion_body)
    assert converted_replay.status_code == 200, converted_replay.text
    assert converted_replay.json() == converted_result

    # New-version requests retain the selected immutable outfit source and its
    # exact expected version.
    version_helper = _run_frontend_helper(g3_temp_directory, {
        "appearance": "Short auburn hair ✨ with the same beauty mark.",
        "garments": _garments_from_outfit(converted_result["outfit"]),
    })
    version_body = {
        "name": "Reviewed second version",
        "appearance": "Short auburn hair ✨ with the same beauty mark.",
        "outfit_key": converted_result["outfit"]["outfit_key"],
        "expected_version": 1,
        "request_id": str(uuid4()),
        "automation": _automation(version_helper["profile"]),
    }
    version_path = f"/api/looks/{converted_result['key']}/versions"
    saved_version = client.post(version_path, json=version_body)
    assert saved_version.status_code == 200, saved_version.text
    version_result = saved_version.json()
    assert version_result["version"] == 2
    assert version_result["automation_profile"]["version"] == 2
    version_replay = client.post(version_path, json=version_body)
    assert version_replay.status_code == 200, version_replay.text
    assert version_replay.json() == version_result

    # Manual photo save uses an API-staged image while preserving the ordinary
    # manual review contract; no assistant answer is fabricated for this path.
    manual_photo_id = _upload(client)
    photo_manual_body = {
        "name": "Manual photo look",
        "appearance": unicode_appearance,
        "garments": manual_garments,
        "request_id": str(uuid4()),
        "automation": _automation(helper["profile"]),
    }
    photo_manual_path = f"/api/looks/photo-stages/{manual_photo_id}/save"
    saved_photo_manual = client.post(photo_manual_path, json=photo_manual_body)
    assert saved_photo_manual.status_code == 200, saved_photo_manual.text
    photo_manual_result = saved_photo_manual.json()
    assert photo_manual_result["automation_profile"]["readiness"]["profile_ready"] is True
    photo_manual_replay = client.post(photo_manual_path, json=photo_manual_body)
    assert photo_manual_replay.status_code == 200, photo_manual_replay.text
    assert photo_manual_replay.json() == photo_manual_result
    assert client.get(f"/api/looks/photo-stages/{manual_photo_id}").json()["state"] == "saved"

    # Photo extraction and its optional automation proposal come from the real
    # route using a deterministic fake provider. The final review is generated
    # by the frontend helper and refers to the actual API proposal identity.
    config = main.CONFIG
    ordinary_appearance = "Short dark hair ✨."
    photo_output = {
        "appearance": ordinary_appearance,
        "garments": ["a blue jacket", "silver earrings"],
        "unresolved": [{"id": "u1", "detail": "the exact shade of the hair"}],
        "automation": {
            "schema_version": 1,
            "appearance_segments": [{
                "segment_id": "photo-segment",
                "start": 0,
                "end": len(ordinary_appearance),
                "text": ordinary_appearance,
                "owner": "permanent",
            }],
            "garment_coverage": [
                {"garment_index": index, "states": {
                    "worn": {"regions": {region: "unknown" for region in _REGIONS}},
                }}
                for index in range(2)
            ],
            "baseline_coverage": None,
            "inventory_complete": None,
        },
    }

    async def fake_vision_provider(_config, _instruction, image_uri, request_evidence=None):
        if request_evidence is not None:
            request_evidence.update({
                "messages": [
                    {"role": "system", "content": "Return one reviewed look candidate."},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Describe visible look details."},
                        {"type": "image_url", "image_url": {"url": image_uri}},
                    ]},
                ],
                "model": config["llm_vision_model"],
                "parameters": {"response_format": {"type": "json_object"}},
            })
        return deepcopy(photo_output)

    monkeypatch.setattr(main.enhance, "run_structured", fake_vision_provider)
    extracted_photo_id = _upload(client)
    extraction = client.post(
        f"/api/looks/photo-stages/{extracted_photo_id}/extract",
        json={"include_automation": True},
    )
    assert extraction.status_code == 200, extraction.text
    proposal = extraction.json()
    assert proposal["unresolved"] == photo_output["unresolved"]
    assert proposal["automation"]["status"] == "ready"
    assert proposal["automation"]["proposal_id"] == proposal["proposal_id"]

    final_appearance = "Short auburn hair ✨."
    final_garments = [
        {"key": None, "wording": "silver earrings", "aside": ""},
        {"key": None, "wording": "a blue jacket", "aside": "the jacket folded over one arm"},
    ]
    photo_helper = _run_frontend_helper(g3_temp_directory, {
        "appearance": final_appearance,
        "garments": final_garments,
    })
    extracted_body = {
        "proposal_id": proposal["proposal_id"],
        "name": "Reviewed extracted photo look",
        "appearance": final_appearance,
        "garments": [
            {"wording": item["wording"], "aside": item["aside"]}
            for item in final_garments
        ],
        "unresolved_decisions": [{"id": "u1", "action": "correct", "correction": "auburn"}],
        "review_confirmed": True,
        "removal_order_confirmed": True,
        "request_id": str(uuid4()),
        "automation": _automation(photo_helper["profile"], suggestion={
            "proposal_id": proposal["automation"]["proposal_id"],
            "draft_content_digest": proposal["automation"]["draft_content_digest"],
        }),
    }
    extracted_path = f"/api/looks/photo-stages/{extracted_photo_id}/save-extracted"
    saved_extracted = client.post(extracted_path, json=extracted_body)
    assert saved_extracted.status_code == 200, saved_extracted.text
    extracted_result = saved_extracted.json()
    assert extracted_result["automation_profile"]["readiness"]["profile_ready"] is True
    assert client.get(f"/api/looks/photo-stages/{extracted_photo_id}").json()["state"] == "saved"
    extracted_replay = client.post(extracted_path, json=extracted_body)
    assert extracted_replay.status_code == 200, extracted_replay.text
    assert extracted_replay.json() == extracted_result

    # Mutating a successful request under its UUID is a known refusal and does
    # not create an additional version or paired profile.
    changed_body = deepcopy(manual_body)
    changed_body["name"] = "Changed after uncertain response"
    before_conflict = {
        table: db.q(f"SELECT * FROM {table} ORDER BY rowid")
        for table in ("saved_look_version", "saved_look_automation_profile", "saved_look_combined_save_receipt")
    }
    changed = client.post("/api/looks", json=changed_body)
    assert changed.status_code == 409, changed.text
    assert changed.json()["detail"]["code"] == "idempotency_conflict"
    assert {
        table: db.q(f"SELECT * FROM {table} ORDER BY rowid")
        for table in before_conflict
    } == before_conflict


def test_frontend_large_version_transport_is_accepted_by_real_api(client, monkeypatch, g3_temp_directory):
    _enabled_config(monkeypatch)
    created = client.post("/api/looks", json={
        "name": "Large version source",
        "appearance": "Short dark hair ✨.",
        "garments": [{"wording": "a white shirt", "aside": ""}],
    })
    assert created.status_code == 200, created.text
    source = created.json()
    maximum = saved_looks._SQLITE_INTEGER_MAX
    penultimate = maximum - 1
    snapshot = {
        "look_id": source["key"],
        "version": penultimate,
        "content_digest": source["content_digest"],
        "appearance": source["appearance"],
        "outfit": source["outfit"],
    }
    db.run(
        "INSERT INTO saved_look_version (look_key, version, name, snapshot_json, created_at) VALUES (?, ?, ?, ?, ?)",
        source["key"], penultimate, "Synthetic penultimate version",
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), db.now(),
    )
    garments = _garments_from_outfit(source["outfit"])
    profile = _run_frontend_helper(g3_temp_directory, {
        "appearance": source["appearance"],
        "garments": garments,
    })["profile"]
    body = {
        "name": "SQLite maximum version",
        "appearance": source["appearance"],
        "outfit_key": source["outfit"]["outfit_key"],
        "expected_version": str(penultimate),
        "request_id": str(uuid4()),
        "automation": _automation(profile),
    }
    identity = {
        "expectedVersion": str(penultimate),
        "path": f"/api/looks/{source['key']}/versions",
        "body": body,
    }

    # Ask the real api.postVersioned serializer to produce the wire body, then
    # submit those exact bytes to FastAPI. The JS stub captures transport only;
    # the asserted success response below is generated by the real API.
    input_path = g3_temp_directory / "transport-input.json"
    output_path = g3_temp_directory / "transport-output.json"
    input_path.write_text(json.dumps(identity), encoding="utf-8")

    # First capture exact request bytes without pretending a mocked response is
    # an API result; the captured payload is executed by FastAPI immediately.
    _run_frontend_test(g3_temp_directory, input_path, output_path, "transport", pattern="large expected version")
    captured = json.loads(output_path.read_text(encoding="utf-8"))["capturedBody"]
    assert json.loads(captured)["expected_version"] == int(penultimate)
    assert f'"expected_version":{penultimate}' in captured
    assert f'"expected_version":"{penultimate}"' not in captured

    actual = client.post(identity["path"], content=captured, headers={"Content-Type": "application/json"})
    assert actual.status_code == 200, actual.text
    assert actual.json()["version"] == maximum
    assert actual.json()["automation_profile"]["version"] == maximum
    api_response_path = g3_temp_directory / "api-response.json"
    api_response_path.write_text(actual.text, encoding="utf-8")

    # Re-enter the helper in response-parse mode: it parses the exact response
    # emitted by FastAPI, not a hand-authored version fixture.
    input_path.write_text(json.dumps({
        **identity,
        "apiResponsePath": str(api_response_path),
        "responseVersion": str(maximum),
    }), encoding="utf-8")
    _run_frontend_test(g3_temp_directory, input_path, output_path, "parse", pattern="large expected version")
    parsed = json.loads(output_path.read_text(encoding="utf-8"))
    assert parsed["parsedVersion"] == str(maximum)
