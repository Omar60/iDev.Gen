"""Independent persistence checks for combined saved-look/profile writes."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import sqlite3
from uuid import uuid4

from PIL import Image
import pytest

import db
import main
from backend import photo_staging, resource_store, saved_looks


_REGIONS = ("chest", "hips", "legs", "feet")
_GROUP_TABLES = (
    "garment", "outfit", "saved_look_version", "saved_look_automation_profile",
    "saved_look_photo_evidence", "saved_look_combined_save_receipt",
    "look_photo_stage", "photo_look_proposal", "photo_look_automation_proposal",
    "saved_look_archive",
)
_RECEIPT_TRIGGER = "saved_look_combined_save_receipt_immutable_update"


@pytest.fixture(autouse=True)
def _remove_test_photo_stages(client):
    before = {row["photo_id"] for row in db.q("SELECT photo_id FROM look_photo_stage")}
    yield
    for row in db.q("SELECT photo_id, staged_path FROM look_photo_stage"):
        if row["photo_id"] not in before:
            photo_staging._safe_unlink(row["photo_id"], row["staged_path"])
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", row["photo_id"])
            db.run("DELETE FROM look_photo_stage WHERE photo_id = ?", row["photo_id"])


def _enable_writes(monkeypatch):
    config = {**main.CONFIG, "resource_planning_enabled": True}
    monkeypatch.setattr(main, "CONFIG", config)
    return config


def _review_payload(
    *,
    request_id: str,
    name: str = "Independent review",
    appearance: str = "Short dark hair and a small beauty mark.",
    garments: list[dict] | None = None,
) -> dict:
    garments = garments or [{"key": None, "wording": "a blue coat", "aside": ""}]
    source = saved_looks.canonicalize_prospective_automation_source(appearance, garments)
    profile = {
        "schema_version": 1,
        "draft_content_digest": source["draft_content_digest"],
        "appearance_segments": [{
            "segment_id": "whole-appearance",
            "start": 0,
            "end": len(appearance),
            "text": appearance,
            "owner": "permanent",
        }],
        "garment_coverage": [{
            "garment_index": 0,
            "states": {"worn": {"regions": {region: "partial" for region in _REGIONS}}},
        }],
        "baseline_coverage": {"regions": {region: "none" for region in _REGIONS}},
        "inventory_complete": True,
    }
    return {
        "name": name,
        "appearance": appearance,
        "garments": garments,
        "request_id": request_id,
        "automation": {
            "profile": profile,
            "profile_review_confirmed": True,
            "inventory_complete_confirmed": True,
            "baseline_coverage_confirmed": True,
            "removal_order_confirmed": True,
        },
    }


def _persisted_group_state() -> dict:
    return {
        table: db.q(f"SELECT * FROM {table} ORDER BY rowid")
        for table in _GROUP_TABLES
    }


def _upload_photo(client) -> tuple[str, bytes]:
    stream = BytesIO()
    Image.new("RGB", (7, 5), (41, 79, 131)).save(stream, format="PNG")
    data = stream.getvalue()
    response = client.post(
        "/api/looks/photo-stages",
        files={"file": ("acceptance.png", data, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["photo_id"], data


def test_review_changes_with_same_look_text_conflict_without_persisted_delta(client, monkeypatch):
    _enable_writes(monkeypatch)
    request_id = str(uuid4())
    payload = _review_payload(request_id=request_id)
    first = client.post("/api/looks", json=payload)
    assert first.status_code == 200, first.text
    before = _persisted_group_state()

    changed_review = deepcopy(payload)
    changed_review["automation"]["profile"]["garment_coverage"][0]["states"]["worn"]["regions"]["chest"] = "full"
    conflict = client.post("/api/looks", json=changed_review)

    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert _persisted_group_state() == before


def test_committed_create_replay_does_not_depend_on_removed_source_catalogue_garment(
    client, monkeypatch,
):
    _enable_writes(monkeypatch)
    appearance = "Short dark hair and a small beauty mark."
    source = client.post("/api/looks", json={
        "name": "Source garment look",
        "appearance": appearance,
        "garments": [{"key": None, "wording": "a blue coat", "aside": ""}],
    })
    assert source.status_code == 200, source.text
    source_garment = source.json()["outfit"]["garments"][0]
    assert db.one("SELECT key FROM garment WHERE key = ?", source_garment["key"]) is not None

    request_id = str(uuid4())
    payload = _review_payload(
        request_id=request_id,
        name="Changed source wording",
        appearance=appearance,
        garments=[{
            "key": source_garment["key"],
            "wording": "a navy wool coat",
            "aside": "",
        }],
    )
    accepted = client.post("/api/looks", json=payload)
    assert accepted.status_code == 200, accepted.text
    expected = accepted.json()
    final_garment = expected["outfit"]["garments"][0]
    assert final_garment["key"] != source_garment["key"]
    assert final_garment["wording"] == "a navy wool coat"

    db.run("DELETE FROM garment WHERE key = ?", source_garment["key"])
    assert db.one("SELECT key FROM garment WHERE key = ?", source_garment["key"]) is None
    before_replay = _persisted_group_state()

    replay = client.post("/api/looks", json=payload)

    assert replay.status_code == 200, replay.text
    assert replay.json() == expected
    assert _persisted_group_state() == before_replay


def test_photo_profile_write_failure_rolls_back_pair_and_keeps_uploaded_bytes(
    client, monkeypatch,
):
    _enable_writes(monkeypatch)
    payload = _review_payload(request_id=str(uuid4()))
    photo_id, original_bytes = _upload_photo(client)
    stage = client.get(f"/api/looks/photo-stages/{photo_id}")
    assert stage.status_code == 200 and stage.json()["state"] == "staged"
    stage_path = photo_staging._stage_path(photo_id)
    assert stage_path.read_bytes() == original_bytes
    before = _persisted_group_state()

    original_run = db.run

    def fail_profile_insert(sql, *params):
        if "insert into saved_look_automation_profile" in " ".join(sql.lower().split()):
            raise sqlite3.IntegrityError("injected profile persistence failure")
        return original_run(sql, *params)

    cleanup_calls = []
    monkeypatch.setattr(db, "run", fail_profile_insert)
    monkeypatch.setattr(photo_staging, "_cleanup_after_commit", cleanup_calls.append)

    response = client.post(f"/api/looks/photo-stages/{photo_id}/save", json=payload)

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "look_write_conflict"
    assert db._tx_depth == 0 and db.conn().in_transaction is False
    assert _persisted_group_state() == before
    assert cleanup_calls == []
    assert stage_path.read_bytes() == original_bytes
    current = db.one("SELECT state, cleanup_state FROM look_photo_stage WHERE photo_id = ?", photo_id)
    assert current == {"state": "staged", "cleanup_state": "none"}


@pytest.mark.parametrize("corruption", [
    "extra_private_result",
    "broken_profile_binding",
    "request_identity_mismatch",
    "request_identity_content_mismatch",
    "extra_request_envelope",
])
def test_persisted_receipt_corruption_fails_closed_instead_of_replaying_json(
    client, monkeypatch, corruption,
):
    _enable_writes(monkeypatch)
    payload = _review_payload(request_id=str(uuid4()))
    saved = client.post("/api/looks", json=payload)
    assert saved.status_code == 200, saved.text
    expected = saved.json()
    row = db.one(
        "SELECT request_id, request_digest, request_json, result_json "
        "FROM saved_look_combined_save_receipt WHERE request_id = ?",
        payload["request_id"],
    )
    request_body = json.loads(row["request_json"])
    result_body = json.loads(row["result_json"])
    private_marker = "UNREVIEWED_PRIVATE_RECEIPT_MARKER"

    if corruption == "extra_private_result":
        result_body["private_extension"] = private_marker
    elif corruption == "broken_profile_binding":
        result_body["automation_profile"]["content_digest"] = "0" * 64
    elif corruption == "request_identity_mismatch":
        request_body["identity"]["request_id"] = str(uuid4())
    elif corruption == "request_identity_content_mismatch":
        request_body["identity"]["look"]["appearance"] += " Changed after commit."
    else:
        request_body["private_extension"] = private_marker
    request_json = json.dumps(request_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    request_digest = resource_store.canonical_digest(request_body)
    result_json = json.dumps(result_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    connection = db.conn()
    try:
        connection.execute(f"DROP TRIGGER IF EXISTS {_RECEIPT_TRIGGER}")
        connection.execute(
            "UPDATE saved_look_combined_save_receipt "
            "SET request_digest = ?, request_json = ?, result_json = ? WHERE request_id = ?",
            (request_digest, request_json, result_json, row["request_id"]),
        )
        connection.commit()

        replay = client.post("/api/looks", json=payload)

        assert replay.status_code == 500, replay.text
        assert replay.json()["detail"]["code"] == "combined_save_receipt_invalid"
        assert private_marker not in replay.text
        assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1
        assert db.one("SELECT COUNT(*) AS n FROM saved_look_automation_profile")["n"] == 1
        assert db.one("SELECT COUNT(*) AS n FROM saved_look_combined_save_receipt")["n"] == 1
    finally:
        connection.execute(f"DROP TRIGGER IF EXISTS {_RECEIPT_TRIGGER}")
        connection.execute(
            "UPDATE saved_look_combined_save_receipt "
            "SET request_digest = ?, request_json = ?, result_json = ? WHERE request_id = ?",
            (row["request_digest"], row["request_json"], row["result_json"], row["request_id"]),
        )
        connection.execute(
            f"""CREATE TRIGGER {_RECEIPT_TRIGGER}
                BEFORE UPDATE ON saved_look_combined_save_receipt
                BEGIN
                    SELECT RAISE(ABORT, 'combined look save receipts are immutable');
                END"""
        )
        connection.commit()

    restored_replay = client.post("/api/looks", json=payload)
    assert restored_replay.status_code == 200, restored_replay.text
    assert restored_replay.json() == expected
