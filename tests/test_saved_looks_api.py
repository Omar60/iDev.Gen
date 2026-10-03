from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest

import db
import main
from backend import resource_store
from backend.request_limits import DEFAULT_MAX_JSON_BODY_BYTES


def _look_path(look_key: str, version: int) -> str:
    return f"/api/looks/{look_key}/versions/{version}"


def _seed_legacy_outfit() -> None:
    now = db.now()
    db.run(
        "INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
        "legacy-top", "a blue cotton top", "", now,
    )
    db.run(
        "INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
        "legacy-layer", "a light cardigan", "", now,
    )
    db.run(
        "INSERT INTO outfit (key, label, garments, created_at) VALUES (?, ?, ?, ?)",
        "legacy-outfit", "Legacy outfit", "legacy-top,legacy-layer", now,
    )


def test_manual_create_generates_keys_and_keeps_one_piece_layers_and_accessories_ordered(client):
    response = client.post("/api/looks", json={
        "name": "Layered look",
        "appearance": "Soft makeup and short dark hair.",
        "garments": [
            {"wording": "a one-piece linen dress"},
            {"wording": "a sheer overshirt", "aside": "unbuttoned"},
            {"wording": "small silver earrings"},
        ],
    })

    assert response.status_code == 200, response.text
    look = response.json()
    assert look["version"] == 1
    assert look["key"].startswith("look-")
    assert look["content_digest"] == resource_store.canonical_digest({
        "appearance": look["appearance"], "outfit": look["outfit"],
    })
    outfit = look["outfit"]
    assert [garment["wording"] for garment in outfit["garments"]] == [
        "a one-piece linen dress", "a sheer overshirt", "small silver earrings",
    ]
    assert [garment["aside"] for garment in outfit["garments"]] == [
        "", "unbuttoned", "",
    ]
    assert all(garment["key"].startswith("garment-") for garment in outfit["garments"])
    assert outfit["outfit_key"].startswith("outfit-")
    assert client.get(_look_path(look["key"], 1)).json() == look

    stored = db.one(
        "SELECT snapshot_json FROM saved_look_version WHERE look_key = ? AND version = 1",
        look["key"],
    )
    snapshot = json.loads(stored["snapshot_json"])
    assert set(snapshot) == {"look_id", "version", "content_digest", "appearance", "outfit"}
    assert snapshot["look_id"] == look["key"]
    assert snapshot["outfit"] == outfit


def test_versions_preserve_legacy_rows_name_only_content_and_historical_reads(client):
    _seed_legacy_outfit()
    created = client.post("/api/looks", json={
        "name": "Blue layers",
        "appearance": "Short curls.",
        "outfit_key": "legacy-outfit",
    })
    assert created.status_code == 200, created.text
    v1 = created.json()
    assert v1["outfit"] == {
        "outfit_key": "legacy-outfit",
        "garments": [
            {"key": "legacy-top", "wording": "a blue cotton top", "aside": ""},
            {"key": "legacy-layer", "wording": "a light cardigan", "aside": ""},
        ],
    }

    renamed = client.post(f"/api/looks/{v1['key']}/versions", json={
        "expected_version": 1,
        "name": "Blue layers named",
    })
    assert renamed.status_code == 200, renamed.text
    v2 = renamed.json()
    assert v2["version"] == 2
    assert v2["appearance"] == v1["appearance"]
    assert v2["outfit"] == v1["outfit"]
    assert v2["content_digest"] == v1["content_digest"]

    edited = client.post(f"/api/looks/{v1['key']}/versions", json={
        "expected_version": 2,
        "name": "Fitted layers",
        "garments": [
            {"key": "legacy-top", "wording": "a fitted blue cotton top"},
            {"key": "legacy-layer"},
        ],
    })
    assert edited.status_code == 200, edited.text
    v3 = edited.json()
    assert v3["version"] == 3
    assert [item["wording"] for item in v3["outfit"]["garments"]] == [
        "a fitted blue cotton top", "a light cardigan",
    ]
    assert v3["outfit"]["garments"][0]["key"] != "legacy-top"
    assert v3["outfit"]["garments"][1]["key"] == "legacy-layer"
    assert v3["outfit"]["outfit_key"] != "legacy-outfit"
    assert client.get(_look_path(v1["key"], 1)).json() == v1
    assert client.get(_look_path(v1["key"], 2)).json() == v2

    reordered = client.post(f"/api/looks/{v1['key']}/versions", json={
        "expected_version": 3,
        "name": "Reordered layers",
        "garments": [
            {"key": v3["outfit"]["garments"][1]["key"]},
            {"key": v3["outfit"]["garments"][0]["key"]},
        ],
    })
    assert reordered.status_code == 200, reordered.text
    v4 = reordered.json()
    assert [item["key"] for item in v4["outfit"]["garments"]] == [
        v3["outfit"]["garments"][1]["key"],
        v3["outfit"]["garments"][0]["key"],
    ]
    assert v4["outfit"]["outfit_key"] != v3["outfit"]["outfit_key"]
    assert client.get("/api/looks").json() == [{
        "key": v1["key"], "version": 4, "name": "Reordered layers",
        "content_digest": v4["content_digest"],
    }]

    # Later catalogue drift cannot alter a persisted look's historical content.
    db.run("UPDATE garment SET wording = ? WHERE key = ?", "changed catalogue wording", "legacy-top")
    db.run("UPDATE outfit SET garments = ? WHERE key = ?", "legacy-layer,legacy-top", "legacy-outfit")
    assert client.get(_look_path(v1["key"], 1)).json() == v1

    with pytest.raises(sqlite3.IntegrityError):
        db.run(
            "UPDATE saved_look_version SET name = ? WHERE look_key = ? AND version = 1",
            "mutated", v1["key"],
        )


def test_appearance_only_and_blank_garments_are_null_outfits(client):
    created = client.post("/api/looks", json={
        "name": "Appearance only", "appearance": "Braided hair.",
    })
    assert created.status_code == 200, created.text
    v1 = created.json()
    assert v1["outfit"] is None

    blank = client.post(f"/api/looks/{v1['key']}/versions", json={
        "expected_version": 1, "name": "Still appearance only", "garments": [],
    })
    assert blank.status_code == 200, blank.text
    v2 = blank.json()
    assert v2["appearance"] == "Braided hair."
    assert v2["outfit"] is None
    assert v2["content_digest"] == resource_store.canonical_digest({
        "appearance": "Braided hair.", "outfit": None,
    })


def test_validation_rejects_unknown_and_ambiguous_content_before_writes(client):
    _seed_legacy_outfit()
    before = {
        table: db.one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
        for table in ("garment", "outfit", "saved_look_version")
    }

    ambiguous = client.post("/api/looks", json={
        "name": "Invalid", "outfit_key": "legacy-outfit", "garments": [],
    })
    assert ambiguous.status_code == 422

    missing_reference = client.post("/api/looks", json={
        "name": "Invalid",
        "garments": [
            {"wording": "a new garment"},
            {"key": "missing-garment"},
        ],
    })
    assert missing_reference.status_code == 422
    assert missing_reference.json()["detail"]["code"] == "invalid_look"

    forged = client.post("/api/looks", json={
        "name": "Invalid", "version": 1, "content_digest": "a" * 64,
        "provenance": {"source": "manual"},
    })
    assert forged.status_code == 422
    assert forged.json()["detail"]["code"] == "extra_field_forbidden"

    duplicate = client.post("/api/looks", json={
        "name": "Invalid", "garments": [{"key": "legacy-top"}, {"key": "legacy-top"}],
    })
    assert duplicate.status_code == 422
    assert {
        table: db.one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
        for table in before
    } == before

    valid = client.post("/api/looks", json={"name": "Valid"})
    assert valid.status_code == 200, valid.text
    created = valid.json()
    for expected_version in (True, 1.0, "1"):
        stale_shape = client.post(f"/api/looks/{created['key']}/versions", json={
            "expected_version": expected_version,
            "name": "Must not be accepted",
        })
        assert stale_shape.status_code == 422
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_version WHERE look_key = ?",
        created["key"],
    )["count"] == 1


def test_transaction_rolls_back_garment_and_outfit_when_look_insert_fails(client):
    db.conn().execute(
        """CREATE TRIGGER reject_saved_look_for_test
           BEFORE INSERT ON saved_look_version
           BEGIN SELECT RAISE(ABORT, 'private injected failure'); END"""
    )
    db.conn().commit()
    try:
        response = client.post("/api/looks", json={
            "name": "Must roll back",
            "garments": [{"wording": "a reversible test coat"}],
        })
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "look_write_conflict"
        assert "private injected failure" not in response.text
        for table in ("garment", "outfit", "saved_look_version"):
            assert db.one(f"SELECT COUNT(*) AS count FROM {table}")["count"] == 0
    finally:
        db.conn().execute("DROP TRIGGER IF EXISTS reject_saved_look_for_test")
        db.conn().commit()


def test_concurrent_stale_edits_have_one_winner_and_one_conflict(client):
    created = client.post("/api/looks", json={"name": "Concurrent"})
    assert created.status_code == 200, created.text
    look_key = created.json()["key"]
    barrier = threading.Barrier(2)

    def save(name: str):
        barrier.wait()
        return client.post(f"/api/looks/{look_key}/versions", json={
            "expected_version": 1, "name": name,
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, ("Concurrent A", "Concurrent B")))
    assert sorted(response.status_code for response in responses) == [200, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"]["code"] == "look_version_stale"
    assert [row["version"] for row in client.get("/api/looks").json()] == [2]
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_version WHERE look_key = ?",
        look_key,
    )["count"] == 2


def test_disabled_writes_keep_saved_look_reads_available(client, monkeypatch):
    created = client.post("/api/looks", json={"name": "Readable while disabled"})
    assert created.status_code == 200, created.text
    look = created.json()
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)

    assert client.get("/api/looks").status_code == 200
    assert client.get(_look_path(look["key"], 1)).status_code == 200
    for response in (
        client.post("/api/looks", json={"name": "Blocked"}),
        client.post(f"/api/looks/{look['key']}/versions", json={
            "expected_version": 1, "name": "Blocked",
        }),
    ):
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "resource_planning_disabled"
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_version WHERE look_key = ?",
        look["key"],
    )["count"] == 1


@pytest.mark.parametrize("path", ["/api/looks", "/api/looks/look-example/versions"])
def test_manual_write_routes_reject_oversized_bodies_before_endpoint_work(client, path):
    body = b"{" + b" " * DEFAULT_MAX_JSON_BODY_BYTES
    response = client.post(path, content=body, headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.json() == {"detail": "Request entity too large"}
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_version")["count"] == 0
    assert db.one("SELECT COUNT(*) AS count FROM garment")["count"] == 0
    assert db.one("SELECT COUNT(*) AS count FROM outfit")["count"] == 0
