"""Atomic paired look/profile save, evidence, and replay acceptance."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
import sqlite3
import threading
from uuid import uuid4

from PIL import Image
import pytest

import db
import main
from backend import photo_staging, resource_store, saved_looks
from backend import combined_look_save
from test_saved_look_application_acceptance import (
    _apply as _apply_saved_look,
    _expected_snapshot,
    _session_with_authoring_plan,
)


_REGIONS = ("chest", "hips", "legs", "feet")
_TABLES = (
    "garment", "outfit", "saved_look_version", "saved_look_automation_profile",
    "saved_look_photo_evidence", "saved_look_combined_save_receipt",
    "look_photo_stage", "photo_look_proposal", "photo_look_automation_proposal",
)


@pytest.fixture(autouse=True)
def _clean_photo_rows(client):
    before = {row["photo_id"] for row in db.q("SELECT photo_id FROM look_photo_stage")}
    yield
    for row in db.q("SELECT photo_id, staged_path FROM look_photo_stage"):
        if row["photo_id"] not in before:
            photo_staging._safe_unlink(row["photo_id"], row["staged_path"])
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", row["photo_id"])
            db.run("DELETE FROM look_photo_stage WHERE photo_id = ?", row["photo_id"])


def _configure(monkeypatch, *, enabled=True):
    config = {
        **main.CONFIG,
        "resource_planning_enabled": enabled,
        "llm_url": "https://assistant.example.invalid/v1",
        "llm_model": "invented-text-model",
        "llm_vision_model": "invented-vision-model",
        "llm_key": "invented-test-key",
    }
    monkeypatch.setattr(main, "CONFIG", config)
    return config


def _garment_source(look: dict) -> list[dict]:
    return [
        {"key": item.get("key"), "wording": item["wording"], "aside": item["aside"]}
        for item in look.get("garments", [])
    ]


def _profile(appearance: str, garments: list[dict]) -> dict:
    source = saved_looks.canonicalize_prospective_automation_source(appearance, garments)
    segments = []
    if appearance:
        segments.append({
            "segment_id": "appearance-1",
            "start": 0,
            "end": len(appearance),
            "text": appearance,
            "owner": "permanent",
        })
    coverage = []
    for index, garment in enumerate(garments):
        states = {"worn": {"regions": {region: "partial" for region in _REGIONS}}}
        if garment.get("aside"):
            states["aside"] = {"regions": {region: "partial" for region in _REGIONS}}
        coverage.append({"garment_index": index, "states": states})
    return {
        "schema_version": 1,
        "draft_content_digest": source["draft_content_digest"],
        "appearance_segments": segments,
        "garment_coverage": coverage,
        "baseline_coverage": {"regions": {region: "none" for region in _REGIONS}},
        "inventory_complete": True,
    }


def _companion(appearance: str, garments: list[dict], *, suggestion=None) -> dict:
    return {
        "profile": _profile(appearance, garments),
        "profile_review_confirmed": True,
        "inventory_complete_confirmed": True,
        "baseline_coverage_confirmed": True,
        "removal_order_confirmed": True,
        **({"suggestion": suggestion} if suggestion is not None else {}),
    }


def _look_payload(
    *, name="Reviewed look", appearance="Short dark hair and a faint beauty mark.",
    garments=None, request_id=None, suggestion=None,
):
    garments = garments if garments is not None else [
        {"key": None, "wording": "a white shirt", "aside": "the shirt folded over one arm"},
        {"key": None, "wording": "blue jeans", "aside": ""},
    ]
    source = _garment_source({"garments": garments})
    result = {"name": name, "appearance": appearance, "garments": garments}
    if request_id is not None:
        result["request_id"] = request_id
        result["automation"] = _companion(appearance, source, suggestion=suggestion)
    return result


def _post(client, payload):
    return client.post("/api/looks", json=payload)


def _detail_code(response):
    return response.json()["detail"]["code"]


def _snapshot():
    return {
        table: db.q(f"SELECT * FROM {table} ORDER BY rowid")
        for table in _TABLES
    }


def _inject_write_failure(monkeypatch, needle: str):
    original = db.run
    normalized_needle = " ".join(needle.lower().split())

    def failing_run(sql, *params):
        if normalized_needle in " ".join(sql.lower().split()):
            raise sqlite3.IntegrityError("injected combined-save write failure")
        return original(sql, *params)

    monkeypatch.setattr(db, "run", failing_run)


def _image_bytes():
    stream = BytesIO()
    Image.new("RGB", (5, 4), (37, 83, 149)).save(stream, format="PNG")
    return stream.getvalue()


def _upload(client, content=None):
    response = client.post(
        "/api/looks/photo-stages",
        files={"file": ("invented-photo.png", content or _image_bytes(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["photo_id"]


def _photo_review(*, request_id: str, automation: dict) -> dict:
    return {
        "proposal_id": automation["suggestion"]["proposal_id"],
        "name": "Reviewed photo look",
        "appearance": "Short dark hair and a faint beauty mark.",
        "garments": [
            {"wording": "a white shirt", "aside": "the shirt folded over one arm"},
            {"wording": "blue jeans", "aside": ""},
        ],
        "unresolved_decisions": [],
        "review_confirmed": True,
        "removal_order_confirmed": True,
        "request_id": request_id,
        "automation": automation,
    }


def _install_photo_provider(monkeypatch, config):
    ordinary = {
        "appearance": "short dark hair",
        "garments": ["a white shirt"],
        "unresolved": [],
    }
    regions = {region: "unknown" for region in _REGIONS}
    output = {
        **ordinary,
        "automation": {
            "schema_version": 1,
            "appearance_segments": [{
                "segment_id": "appearance-1", "start": 0,
                "end": len(ordinary["appearance"]), "text": ordinary["appearance"],
                "owner": None,
            }],
            "garment_coverage": [{"garment_index": 0, "states": {
                "worn": {"regions": regions},
            }}],
            "baseline_coverage": None,
            "inventory_complete": None,
        },
    }

    async def fake_provider(_config, _prompt, image_uri, request_evidence=None):
        assert not db.conn().in_transaction
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
        return deepcopy(output)

    monkeypatch.setattr(main.enhance, "run_structured", fake_provider)
    return ordinary


def _extracted_review(client, monkeypatch, *, request_id=None):
    config = _configure(monkeypatch)
    ordinary = _install_photo_provider(monkeypatch, config)
    photo_id = _upload(client)
    extracted = client.post(
        f"/api/looks/photo-stages/{photo_id}/extract",
        json={"include_automation": True},
    )
    assert extracted.status_code == 200, extracted.text
    automation = extracted.json()["automation"]
    assert automation["status"] == "ready"
    final_appearance = "Short dark hair and a faint beauty mark."
    final_garments = [
        {"key": None, "wording": "a white shirt", "aside": "the shirt folded over one arm"},
        {"key": None, "wording": "blue jeans", "aside": ""},
    ]
    companion = _companion(
        final_appearance,
        _garment_source({"garments": final_garments}),
        suggestion={
            "proposal_id": automation["proposal_id"],
            "draft_content_digest": automation["draft_content_digest"],
        },
    )
    return photo_id, ordinary, automation, _photo_review(
        request_id=request_id or str(uuid4()), automation=companion,
    )


def test_combined_create_maps_final_identity_and_builds_server_user_provenance(client, monkeypatch):
    _configure(monkeypatch)
    request_id = str(uuid4())
    payload = _look_payload(request_id=request_id)

    response = _post(client, payload)

    assert response.status_code == 200, response.text
    result = response.json()
    identity = result["automation_profile"]
    assert identity == {
        "look_key": result["key"],
        "version": 1,
        "content_digest": result["content_digest"],
        "profile_revision": 1,
        "profile_digest": identity["profile_digest"],
        "readiness": identity["readiness"],
    }
    assert identity["readiness"]["profile_ready"] is True
    profile = saved_looks.get_automation_profile(
        result["key"], 1, 1, result["content_digest"], identity["profile_digest"],
    )
    accepted = profile["authored_provenance"]
    assert accepted == {
        "origin": "user",
        "source_identity": "looks-integrated-review",
        "source_digest": result["content_digest"],
        "accepted_revision": 1,
    }
    assert all(segment["provenance"] == accepted for segment in profile["appearance_segments"])
    assert set(profile["garment_coverage"]) == {
        item["key"] for item in result["outfit"]["garments"]
    }
    assert all(
        fact["provenance"] == accepted
        for states in profile["garment_coverage"].values()
        for fact in states.values()
    )
    receipt = db.one(
        "SELECT * FROM saved_look_combined_save_receipt WHERE request_id = ?", request_id,
    )
    assert receipt is not None
    assert json.loads(receipt["result_json"]) == result
    assert json.loads(receipt["request_json"])["identity"]["request_id"] == request_id


@pytest.mark.parametrize("missing", ["request_id", "automation"])
def test_combined_companion_pair_rejection_never_falls_back_to_look_only(client, monkeypatch, missing):
    _configure(monkeypatch)
    payload = _look_payload(request_id=str(uuid4()))
    payload.pop(missing)

    response = _post(client, payload)

    assert response.status_code == 422
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_combined_save_receipt")["n"] == 0


@pytest.mark.parametrize("confirmation", [
    "profile_review_confirmed", "inventory_complete_confirmed",
    "baseline_coverage_confirmed", "removal_order_confirmed",
])
def test_each_integrated_confirmation_is_required_before_any_write(client, monkeypatch, confirmation):
    _configure(monkeypatch)
    payload = _look_payload(request_id=str(uuid4()))
    payload["automation"][confirmation] = False

    response = _post(client, payload)

    assert response.status_code == 422
    assert _detail_code(response) == "automation_review_required"
    assert _snapshot()["saved_look_version"] == []
    assert _snapshot()["saved_look_combined_save_receipt"] == []


def test_incomplete_profile_is_not_saved_or_advertised_as_full_auto_ready(client, monkeypatch):
    _configure(monkeypatch)
    payload = _look_payload(request_id=str(uuid4()))
    payload["automation"]["profile"]["garment_coverage"][0]["states"]["worn"]["regions"]["chest"] = "unknown"
    baseline = _snapshot()

    response = _post(client, payload)

    assert response.status_code == 422
    assert _detail_code(response) == "automation_profile_not_ready"
    assert _snapshot() == baseline


@pytest.mark.parametrize("needle", [
    "INSERT INTO garment", "INSERT INTO outfit", "INSERT INTO saved_look_version",
    "INSERT INTO saved_look_automation_profile", "INSERT INTO saved_look_combined_save_receipt",
])
def test_every_principal_write_failure_rolls_back_all_persisted_rows(
    client, monkeypatch, needle,
):
    _configure(monkeypatch)
    baseline = _snapshot()
    _inject_write_failure(monkeypatch, needle)

    response = _post(client, _look_payload(request_id=str(uuid4())))

    assert response.status_code == 409, response.text
    assert _detail_code(response) == "look_write_conflict"
    assert _snapshot() == baseline


def test_identical_retry_after_discarded_response_and_real_concurrent_requests_converge(
    client, monkeypatch,
):
    _configure(monkeypatch)
    request_id = str(uuid4())
    payload = _look_payload(request_id=request_id)
    first = _post(client, payload)
    assert first.status_code == 200, first.text
    expected = first.json()
    # Discard the first result, modeling a client that lost the response after commit.
    replay = _post(client, payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == expected
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1

    racing_id = str(uuid4())
    racing_payload = _look_payload(name="Concurrent reviewed look", request_id=racing_id)
    start = threading.Barrier(2)

    def submit():
        start.wait(timeout=10)
        return _post(client, racing_payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result(timeout=20) for future in [pool.submit(submit), pool.submit(submit)]]
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 2
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_combined_save_receipt")["n"] == 2


def test_committed_create_replay_uses_resolved_source_after_catalogue_garment_removal(
    client, monkeypatch,
):
    _configure(monkeypatch)
    source_response = _post(client, {
        "name": "Source look",
        "appearance": "Short dark hair.",
        "garments": [{"wording": "a gray wool coat", "aside": ""}],
    })
    assert source_response.status_code == 200, source_response.text
    source_key = source_response.json()["outfit"]["garments"][0]["key"]
    request_id = str(uuid4())
    payload = _look_payload(
        name="Reviewed changed coat",
        appearance="Short dark hair.",
        garments=[{
            "key": source_key,
            "wording": "a gray wool coat with silver buttons",
            "aside": "",
        }],
        request_id=request_id,
    )

    first = _post(client, payload)

    assert first.status_code == 200, first.text
    expected = first.json()
    final_key = expected["outfit"]["garments"][0]["key"]
    assert final_key != source_key
    db.run("DELETE FROM garment WHERE key = ?", source_key)
    baseline = _snapshot()

    replay = _post(client, payload)

    assert replay.status_code == 200, replay.text
    assert replay.json() == expected
    assert _snapshot() == baseline

    new_request = deepcopy(payload)
    new_request["request_id"] = str(uuid4())
    refused = _post(client, new_request)

    assert refused.status_code == 422
    assert _detail_code(refused) == "invalid_look"
    assert _snapshot() == baseline


def test_combined_new_version_rejects_stale_expected_version_without_partial_rows(client, monkeypatch):
    _configure(monkeypatch)
    first = _post(client, _look_payload(request_id=str(uuid4())))
    assert first.status_code == 200, first.text
    saved = first.json()
    garments = [
        {"key": item["key"], "wording": item["wording"], "aside": item["aside"]}
        for item in saved["outfit"]["garments"]
    ]
    first_version = client.post(f"/api/looks/{saved['key']}/versions", json={
        "expected_version": 1,
        "name": "Accepted version two",
        "appearance": saved["appearance"],
        "request_id": str(uuid4()),
        "automation": _companion(saved["appearance"], garments),
    })
    assert first_version.status_code == 200, first_version.text
    baseline = _snapshot()
    stale = client.post(f"/api/looks/{saved['key']}/versions", json={
        "expected_version": 1,
        "name": "Stale competing version",
        "appearance": saved["appearance"],
        "request_id": str(uuid4()),
        "automation": _companion(saved["appearance"], garments),
    })
    assert stale.status_code == 409
    assert _detail_code(stale) == "look_version_stale"
    assert _snapshot() == baseline


def test_changed_content_or_garment_order_with_same_request_id_conflicts(client, monkeypatch):
    _configure(monkeypatch)
    request_id = str(uuid4())
    first_payload = _look_payload(request_id=request_id)
    first = _post(client, first_payload)
    assert first.status_code == 200, first.text
    changed = deepcopy(first_payload)
    changed["garments"].reverse()
    source = _garment_source({"garments": changed["garments"]})
    changed["automation"] = _companion(changed["appearance"], source)

    conflict = _post(client, changed)

    assert conflict.status_code == 409
    assert _detail_code(conflict) == "idempotency_conflict"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1


def test_equivalent_transport_defaults_replay_the_same_combined_result(client, monkeypatch):
    _configure(monkeypatch)
    request_id = str(uuid4())
    payload = _look_payload(
        appearance="",
        garments=[{"key": None, "wording": "a white shirt", "aside": ""}],
        request_id=request_id,
    )
    del payload["appearance"]
    del payload["garments"][0]["aside"]
    first = _post(client, payload)
    assert first.status_code == 200, first.text

    equivalent = deepcopy(payload)
    equivalent["appearance"] = ""
    equivalent["garments"][0].pop("key", None)
    equivalent["garments"][0]["aside"] = ""
    equivalent["automation"]["suggestion"] = None
    replay = _post(client, equivalent)

    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1


@pytest.mark.parametrize("change", ["name", "content", "source"])
def test_changed_name_content_or_source_with_same_request_id_conflicts(client, monkeypatch, change):
    _configure(monkeypatch)
    request_id = str(uuid4())
    first_payload = _look_payload(request_id=request_id)
    first = _post(client, first_payload)
    assert first.status_code == 200, first.text
    changed = deepcopy(first_payload)
    if change == "name":
        changed["name"] = "Different reviewed name"
    elif change == "content":
        changed["appearance"] += " A small mole."
        changed["automation"] = _companion(
            changed["appearance"], _garment_source(changed),
        )
    else:
        changed["outfit_key"] = "outfit_0123456789abcdef0123456789abcdef"
        del changed["garments"]

    conflict = _post(client, changed)

    assert conflict.status_code == 409
    assert _detail_code(conflict) == "idempotency_conflict"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1


def test_changed_photo_source_with_same_request_id_conflicts_before_stage_read(client, monkeypatch):
    _configure(monkeypatch)
    payload = _look_payload(request_id=str(uuid4()))
    first = _post(client, payload)
    assert first.status_code == 200, first.text
    photo_id = _upload(client)

    conflict = client.post(f"/api/looks/photo-stages/{photo_id}/save", json=payload)

    assert conflict.status_code == 409
    assert _detail_code(conflict) == "idempotency_conflict"
    assert client.get(f"/api/looks/photo-stages/{photo_id}").json()["state"] == "staged"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1


def test_changed_expected_version_with_same_request_id_conflicts(client, monkeypatch):
    _configure(monkeypatch)
    initial = client.post("/api/looks", json={
        "name": "Original look",
        "appearance": "Short dark hair and a faint beauty mark.",
        "garments": [
            {"wording": "a white shirt", "aside": "the shirt folded over one arm"},
            {"wording": "blue jeans", "aside": ""},
        ],
    })
    assert initial.status_code == 200, initial.text
    saved = initial.json()
    garments = [
        {"key": item["key"], "wording": item["wording"], "aside": item["aside"]}
        for item in saved["outfit"]["garments"]
    ]
    payload = {
        "name": "Reviewed next version",
        "appearance": saved["appearance"],
        "garments": garments,
        "request_id": str(uuid4()),
        "automation": _companion(saved["appearance"], garments),
    }
    first = client.post(f"/api/looks/{saved['key']}/versions", json={
        **payload, "expected_version": 1,
    })
    assert first.status_code == 200, first.text
    conflict = client.post(f"/api/looks/{saved['key']}/versions", json={
        **payload, "expected_version": 2,
    })

    assert conflict.status_code == 409
    assert _detail_code(conflict) == "idempotency_conflict"
    assert db.one(
        "SELECT COUNT(*) AS n FROM saved_look_version WHERE look_key = ?",
        saved["key"],
    )["n"] == 2


def test_receipt_replay_rejects_extra_persisted_response_or_evidence_fields(client, monkeypatch):
    _configure(monkeypatch)
    response = _post(client, _look_payload(request_id=str(uuid4())))
    assert response.status_code == 200, response.text
    row = db.one("SELECT * FROM saved_look_combined_save_receipt")
    request_id = row["request_id"]
    request_body = json.loads(row["request_json"])
    input_digest = row["input_digest"]

    tampered_result = deepcopy(row)
    result = json.loads(tampered_result["result_json"])
    result["unreviewed_private_field"] = "unexpected"
    tampered_result["result_json"] = json.dumps(result)
    with pytest.raises(saved_looks.SavedLookError) as invalid_result:
        combined_look_save._read_receipt(tampered_result, request_id, input_digest)
    assert invalid_result.value.code == "combined_save_receipt_invalid"

    tampered_evidence = deepcopy(row)
    request_body["unreviewed_private_field"] = "unexpected"
    tampered_evidence["request_json"] = json.dumps(request_body)
    with pytest.raises(saved_looks.SavedLookError) as invalid_evidence:
        combined_look_save._read_receipt(tampered_evidence, request_id, input_digest)
    assert invalid_evidence.value.code == "combined_save_receipt_invalid"


def test_receipt_replay_rejects_recalculated_identity_with_different_submitted_look(client, monkeypatch):
    _configure(monkeypatch)
    request_id = str(uuid4())
    response = _post(client, _look_payload(request_id=request_id))
    assert response.status_code == 200, response.text
    row = db.one(
        "SELECT * FROM saved_look_combined_save_receipt WHERE request_id = ?",
        request_id,
    )
    tampered = deepcopy(row)
    request = json.loads(tampered["request_json"])
    request["identity"]["look"]["appearance"] += " Changed after commit."
    tampered["request_json"] = json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    tampered["request_digest"] = resource_store.canonical_digest(request)

    with pytest.raises(saved_looks.SavedLookError) as invalid:
        combined_look_save._read_receipt(tampered, request_id, row["input_digest"])

    assert invalid.value.code == "combined_save_receipt_invalid"


def test_receipt_replay_preserves_original_profile_order_for_input_digest(client, monkeypatch):
    _configure(monkeypatch)
    payload = _look_payload(request_id=str(uuid4()))
    payload["automation"]["profile"]["garment_coverage"].reverse()

    first = _post(client, payload)

    assert first.status_code == 200, first.text
    replay = _post(client, payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()


def test_manual_suggestion_identity_survives_renewed_final_review(client, monkeypatch):
    config = _configure(monkeypatch)
    original_appearance = "short dark hair"
    original_garments = [{"key": None, "wording": "a white shirt", "aside": ""}]
    original_candidate = _profile(original_appearance, original_garments)
    original_candidate["appearance_segments"][0]["owner"] = None
    original_candidate["garment_coverage"][0]["states"]["worn"]["regions"] = {
        region: "unknown" for region in _REGIONS
    }
    original_candidate["baseline_coverage"] = None
    original_candidate["inventory_complete"] = None

    async def fake_provider(_config, _prompt, request_evidence=None):
        if request_evidence is not None:
            request_evidence.update({
                "messages": [
                    {"role": "system", "content": "Return structured suggestions."},
                    {"role": "user", "content": "Exact original look draft."},
                ],
                "model": config["llm_model"],
                "parameters": {},
            })
        return deepcopy(original_candidate)

    monkeypatch.setattr(main.enhance, "run_structured", fake_provider)
    proposed = client.post("/api/looks/automation-proposals", json={
        "appearance": original_appearance,
        "garments": original_garments,
    })
    assert proposed.status_code == 200, proposed.text
    suggestion = {
        "proposal_id": proposed.json()["proposal_id"],
        "draft_content_digest": proposed.json()["draft_content_digest"],
    }
    final_appearance = "Short dark hair with a faint beauty mark."
    final_garments = [
        {"key": None, "wording": "a white shirt", "aside": ""},
        {"key": None, "wording": "a navy cardigan", "aside": "open"},
    ]
    request_id = str(uuid4())
    accepted = _post(client, _look_payload(
        name="Corrected after suggestion", appearance=final_appearance,
        garments=final_garments, request_id=request_id, suggestion=suggestion,
    ))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["automation_profile"]["readiness"]["profile_ready"] is True
    evidence = json.loads(db.one(
        "SELECT request_json FROM saved_look_combined_save_receipt WHERE request_id = ?",
        request_id,
    )["request_json"])
    assert evidence["suggestion_evidence"]["draft_content_digest"] == suggestion["draft_content_digest"]
    assert evidence["identity"]["automation"]["suggestion"] == suggestion
    assert evidence["identity"]["resolved_source"]["draft_content_digest"] != suggestion["draft_content_digest"]


def test_stale_suggestion_digest_and_stale_final_draft_are_zero_write_refusals(client, monkeypatch):
    config = _configure(monkeypatch)
    appearance = "short dark hair"
    garments = [{"key": None, "wording": "a white shirt", "aside": ""}]
    candidate = _profile(appearance, garments)
    candidate["appearance_segments"][0]["owner"] = None
    candidate["garment_coverage"][0]["states"]["worn"]["regions"] = {
        region: "unknown" for region in _REGIONS
    }
    candidate["baseline_coverage"] = None
    candidate["inventory_complete"] = None

    async def fake_provider(_config, _prompt, request_evidence=None):
        if request_evidence is not None:
            request_evidence.update({
                "messages": [
                    {"role": "system", "content": "Return structured suggestions."},
                    {"role": "user", "content": "Exact source draft."},
                ],
                "model": config["llm_model"],
                "parameters": {},
            })
        return deepcopy(candidate)

    monkeypatch.setattr(main.enhance, "run_structured", fake_provider)
    proposed = client.post("/api/looks/automation-proposals", json={
        "appearance": appearance, "garments": garments,
    })
    assert proposed.status_code == 200, proposed.text
    suggestion = {
        "proposal_id": proposed.json()["proposal_id"],
        "draft_content_digest": "0" * 64,
    }
    payload = _look_payload(
        appearance="Corrected final appearance.", garments=garments,
        request_id=str(uuid4()), suggestion=suggestion,
    )
    before = _snapshot()

    stale_proposal = _post(client, payload)

    assert stale_proposal.status_code == 409
    assert _detail_code(stale_proposal) == "automation_proposal_stale"
    assert _snapshot() == before

    current_suggestion = {
        "proposal_id": proposed.json()["proposal_id"],
        "draft_content_digest": proposed.json()["draft_content_digest"],
    }
    current = _look_payload(
        appearance="Corrected final appearance.", garments=garments,
        request_id=str(uuid4()), suggestion=current_suggestion,
    )
    current["automation"]["profile"]["draft_content_digest"] = "f" * 64
    stale_draft = _post(client, current)
    assert stale_draft.status_code == 422
    assert _detail_code(stale_draft) == "invalid_look"
    assert _snapshot() == before


def test_photo_combined_save_cleans_only_after_outer_commit_and_replays_without_bytes(
    client, monkeypatch,
):
    photo_id, _ordinary, _suggestion, review = _extracted_review(client, monkeypatch)
    seen = []
    original_cleanup = photo_staging._cleanup_after_commit

    def inspect_commit(photo_stage_id):
        assert photo_stage_id == photo_id
        assert db._tx_depth == 0
        assert db.conn().in_transaction is False
        assert db.one(
            "SELECT COUNT(*) AS n FROM saved_look_combined_save_receipt WHERE request_id = ?",
            review["request_id"],
        )["n"] == 1
        assert db.one("SELECT COUNT(*) AS n FROM saved_look_photo_evidence")["n"] == 1
        seen.append(True)
        original_cleanup(photo_stage_id)

    monkeypatch.setattr(photo_staging, "_cleanup_after_commit", inspect_commit)
    response = client.post(f"/api/looks/photo-stages/{photo_id}/save-extracted", json=review)

    assert response.status_code == 200, response.text
    result = response.json()
    assert seen == [True]
    stage = db.one("SELECT staged_path, state, cleanup_state FROM look_photo_stage WHERE photo_id = ?", photo_id)
    assert stage["state"] == "saved"
    assert stage["cleanup_state"] == "cleaned"
    assert not photo_staging._stage_path(photo_id).exists()
    replay = client.post(f"/api/looks/photo-stages/{photo_id}/save-extracted", json=review)
    assert replay.status_code == 200, replay.text
    assert replay.json() == result
    receipt_json = db.one(
        "SELECT request_json FROM saved_look_combined_save_receipt WHERE request_id = ?",
        review["request_id"],
    )["request_json"]
    for private_value in ("data:", "https://assistant.example.invalid", "invented-test-key"):
        assert private_value not in receipt_json
    evidence = client.get(
        f"/api/looks/{result['key']}/versions/{result['version']}/photo-evidence",
    )
    assert evidence.status_code == 200
    assert evidence.json()["source"]["sha256"]
    assert evidence.json()["corrections"]["appearance"] == review["appearance"]
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1


@pytest.mark.parametrize("needle", [
    "INSERT INTO saved_look_photo_evidence",
    "UPDATE look_photo_stage SET state = 'saved'",
    "INSERT INTO saved_look_combined_save_receipt",
])
def test_photo_evidence_stage_and_receipt_write_failures_leave_no_partial_pair(
    client, monkeypatch, needle,
):
    photo_id, _ordinary, _suggestion, review = _extracted_review(client, monkeypatch)
    stage_path = photo_staging._stage_path(photo_id)
    baseline = _snapshot()
    assert stage_path.exists()
    _inject_write_failure(monkeypatch, needle)

    response = client.post(f"/api/looks/photo-stages/{photo_id}/save-extracted", json=review)

    assert response.status_code == 409, response.text
    assert _snapshot() == baseline
    assert stage_path.exists()
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "staged"


def test_cleanup_failure_is_committed_and_visible_while_replay_response_stays_exact(
    client, monkeypatch,
):
    photo_id = _upload(client)
    review = _look_payload(request_id=str(uuid4()), garments=[
        {"key": None, "wording": "a white shirt", "aside": ""},
    ])
    monkeypatch.setattr(
        photo_staging, "_safe_unlink",
        lambda *_args: (False, photo_staging._CLEANUP_WARNING),
    )

    response = client.post(f"/api/looks/photo-stages/{photo_id}/save", json=review)

    assert response.status_code == 200, response.text
    result = response.json()
    status = client.get(f"/api/looks/photo-stages/{photo_id}")
    assert status.status_code == 200
    assert status.json()["state"] == "saved"
    assert status.json()["cleanup_warning"] == photo_staging._CLEANUP_WARNING
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1
    replay = client.post(f"/api/looks/photo-stages/{photo_id}/save", json=review)
    assert replay.status_code == 200, replay.text
    assert replay.json() == result


def test_new_version_gets_revision_one_and_does_not_mutate_prior_profile_or_session_snapshot(
    client, monkeypatch, seeded,
):
    _configure(monkeypatch)
    first_payload = _look_payload(name="Version one", request_id=str(uuid4()))
    first_response = _post(client, first_payload)
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    session_id, _plan = _session_with_authoring_plan(
        client, seeded, "Historical combined look snapshot",
    )
    applied = _apply_saved_look(client, session_id, first, revision=1)
    assert applied.status_code == 200, applied.text
    session_plan_before = client.get(f"/api/sessions/{session_id}/plan").json()["plan"]
    assert session_plan_before["authoring"]["look_snapshot"] == _expected_snapshot(first)

    prior_profile = saved_looks.get_automation_profile(
        first["key"], 1, 1, first["content_digest"],
        first["automation_profile"]["profile_digest"],
    )
    source_garments = [
        {"key": item["key"], "wording": item["wording"], "aside": item["aside"]}
        for item in first["outfit"]["garments"]
    ]
    new_name = "Version two"
    version_payload = {
        "expected_version": 1,
        "name": new_name,
        "appearance": first["appearance"],
        "request_id": str(uuid4()),
        "automation": _companion(first["appearance"], source_garments),
    }
    edited = client.post(f"/api/looks/{first['key']}/versions", json=version_payload)
    assert edited.status_code == 200, edited.text
    second = edited.json()
    assert second["version"] == 2
    assert second["automation_profile"]["profile_revision"] == 1
    assert second["automation_profile"]["look_key"] == first["key"]
    assert second["automation_profile"]["content_digest"] == second["content_digest"]
    assert saved_looks.get_automation_profile(
        first["key"], 1, 1, first["content_digest"],
        first["automation_profile"]["profile_digest"],
    ) == prior_profile
    assert saved_looks.get_latest_automation_profile(first["key"], 2)["profile_revision"] == 1
    assert client.get(f"/api/sessions/{session_id}/plan").json()["plan"] == session_plan_before


def test_archive_feature_gate_old_look_only_shape_and_portable_v1_are_preserved(
    client, monkeypatch,
):
    _configure(monkeypatch)
    old = client.post("/api/looks", json={"name": "Ordinary API look", "appearance": "A portrait."})
    assert old.status_code == 200, old.text
    assert set(old.json()) == {"key", "version", "name", "content_digest", "appearance", "outfit"}
    legacy = old.json()
    exported = client.get(f"/api/looks/{legacy['key']}/versions/1/export")
    assert exported.status_code == 200, exported.text
    assert set(exported.json()) == {"schema_version", "look", "garments", "provenance"}
    assert "automation" not in exported.text and "profile_revision" not in exported.text
    ordinary_version = client.post(f"/api/looks/{legacy['key']}/versions", json={
        "expected_version": 1,
        "name": "Ordinary version two",
        "appearance": "A later portrait.",
    })
    assert ordinary_version.status_code == 200, ordinary_version.text
    assert set(ordinary_version.json()) == {
        "key", "version", "name", "content_digest", "appearance", "outfit",
    }

    combined_request_id = str(uuid4())
    combined = _post(client, _look_payload(request_id=combined_request_id))
    assert combined.status_code == 200, combined.text
    result = combined.json()
    archived = client.delete(f"/api/looks/{result['key']}")
    assert archived.status_code == 200, archived.text
    replay = _post(client, _look_payload(request_id=combined_request_id))
    assert replay.status_code == 200
    assert replay.json() == result
    new_version = client.post(f"/api/looks/{result['key']}/versions", json={
        "expected_version": 1,
        "name": "Blocked archived edit",
        "request_id": str(uuid4()),
        "appearance": result["appearance"],
        "garments": result["outfit"]["garments"],
        "automation": _companion(result["appearance"], [
            {"key": g["key"], "wording": g["wording"], "aside": g["aside"]}
            for g in result["outfit"]["garments"]
        ]),
    })
    assert new_version.status_code == 409
    assert _detail_code(new_version) == "look_archived"

    disabled = _configure(monkeypatch, enabled=False)
    assert disabled["resource_planning_enabled"] is False
    blocked = _post(client, _look_payload(request_id=str(uuid4())))
    assert blocked.status_code == 503
    assert _detail_code(blocked) == "resource_planning_disabled"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_combined_save_receipt")["n"] == 1


def test_feature_gate_refuses_even_a_combined_receipt_replay_without_new_writes(client, monkeypatch):
    _configure(monkeypatch)
    request_id = str(uuid4())
    payload = _look_payload(request_id=request_id)
    accepted = _post(client, payload)
    assert accepted.status_code == 200, accepted.text
    before = _snapshot()
    _configure(monkeypatch, enabled=False)

    replay = _post(client, payload)

    assert replay.status_code == 503
    assert _detail_code(replay) == "resource_planning_disabled"
    assert _snapshot() == before


def test_feature_gate_rechecked_before_receipt_persist_and_rolls_back_the_pair(client, monkeypatch):
    config = _configure(monkeypatch)
    original_publish = saved_looks.publish_automation_profile
    baseline = _snapshot()

    def publish_then_disable(*args, **kwargs):
        result = original_publish(*args, **kwargs)
        config["resource_planning_enabled"] = False
        return result

    monkeypatch.setattr(saved_looks, "publish_automation_profile", publish_then_disable)
    response = _post(client, _look_payload(request_id=str(uuid4())))
    assert response.status_code == 503
    assert _detail_code(response) == "resource_planning_disabled"
    assert _snapshot() == baseline


def test_combined_new_version_uses_history_after_catalogue_rows_drift_and_appearance_only_is_ready(
    client, monkeypatch,
):
    _configure(monkeypatch)
    first = _post(client, _look_payload(request_id=str(uuid4())))
    assert first.status_code == 200, first.text
    saved = first.json()
    outfit_key = saved["outfit"]["outfit_key"]
    garment_keys = [item["key"] for item in saved["outfit"]["garments"]]
    # The immutable version remains the source of truth when live catalogue
    # definitions are edited or removed later.
    db.run("UPDATE garment SET wording = 'a changed catalogue label' WHERE key = ?", garment_keys[0])
    db.run("DELETE FROM garment WHERE key = ?", garment_keys[1])
    db.run("DELETE FROM outfit WHERE key = ?", outfit_key)
    source = [
        {"key": item["key"], "wording": item["wording"], "aside": item["aside"]}
        for item in saved["outfit"]["garments"]
    ]
    revised = client.post(f"/api/looks/{saved['key']}/versions", json={
        "expected_version": 1,
        "name": "Historical catalogue snapshot",
        "appearance": saved["appearance"],
        "outfit_key": outfit_key,
        "request_id": str(uuid4()),
        "automation": _companion(saved["appearance"], source),
    })
    assert revised.status_code == 200, revised.text
    assert revised.json()["outfit"] == saved["outfit"]
    assert revised.json()["automation_profile"]["profile_revision"] == 1

    appearance_only = _post(client, _look_payload(
        name="Appearance only", appearance="A quiet studio portrait.",
        garments=[], request_id=str(uuid4()),
    ))
    assert appearance_only.status_code == 200, appearance_only.text
    assert appearance_only.json()["outfit"] is None
    assert appearance_only.json()["automation_profile"]["readiness"]["profile_ready"] is True


def test_exact_sqlite_integer_boundary_accepts_max_and_rejects_successor_before_writes(
    client, monkeypatch,
):
    _configure(monkeypatch)
    initial = _post(client, _look_payload(name="Integer boundary", request_id=str(uuid4())))
    assert initial.status_code == 200, initial.text
    first = initial.json()
    maximum = saved_looks._SQLITE_INTEGER_MAX
    snapshot = {
        "look_id": first["key"],
        "version": maximum - 1,
        "content_digest": first["content_digest"],
        "appearance": first["appearance"],
        "outfit": first["outfit"],
    }
    db.run(
        "INSERT INTO saved_look_version (look_key, version, name, snapshot_json, created_at) VALUES (?, ?, ?, ?, ?)",
        first["key"], maximum - 1, "Synthetic penultimate version",
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), db.now(),
    )
    source_garments = [
        {"key": garment["key"], "wording": garment["wording"], "aside": garment["aside"]}
        for garment in first["outfit"]["garments"]
    ]
    at_max = client.post(f"/api/looks/{first['key']}/versions", json={
        "expected_version": maximum - 1,
        "name": "SQLite maximum",
        "appearance": first["appearance"],
        "request_id": str(uuid4()),
        "automation": _companion(first["appearance"], source_garments),
    })
    assert at_max.status_code == 200, at_max.text
    assert at_max.json()["version"] == maximum
    assert client.get(f"/api/looks/{first['key']}/versions/{maximum}").status_code == 200
    before_successor = _snapshot()
    successor = client.post(f"/api/looks/{first['key']}/versions", json={
        "expected_version": maximum,
        "name": "Overflow is refused",
        "appearance": first["appearance"],
        "request_id": str(uuid4()),
        "automation": _companion(first["appearance"], source_garments),
    })
    assert successor.status_code == 409
    assert _detail_code(successor) == "look_version_limit"
    assert _snapshot() == before_successor
    too_large = client.post(f"/api/looks/{first['key']}/versions", json={
        "expected_version": maximum + 1,
        "name": "Out of SQLite range",
        "request_id": str(uuid4()),
        "appearance": first["appearance"],
        "automation": _companion(first["appearance"], source_garments),
    })
    assert too_large.status_code == 422
    assert _snapshot() == before_successor
