from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import sqlite3
import threading
from urllib.parse import quote

import pytest

import db
import main
from backend import resource_store, saved_looks
from backend.request_limits import DEFAULT_MAX_JSON_BODY_BYTES


def _look_path(look_key: str, version: int) -> str:
    return f"/api/looks/{quote(look_key, safe='')}/versions/{version}"


def _look_export_path(look_key: str, version: int) -> str:
    return f"{_look_path(look_key, version)}/export"


def _insert_historical_look(look_key: str) -> dict:
    appearance = "Historical appearance."
    outfit = None
    snapshot = {
        "look_id": look_key,
        "version": 1,
        "content_digest": resource_store.canonical_digest({
            "appearance": appearance, "outfit": outfit,
        }),
        "appearance": appearance,
        "outfit": outfit,
    }
    db.run(
        """INSERT INTO saved_look_version
           (look_key, version, name, snapshot_json, created_at)
           VALUES (?, 1, ?, ?, ?)""",
        look_key,
        "Historical look",
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        db.now(),
    )
    return snapshot


def _portable_look() -> dict:
    return {
        "schema_version": 1,
        "look": {
            "key": "portable-look",
            "version": 3,
            "name": "Layered blue look",
            "appearance": "  arbitrary appearance prose\n",
            "outfit": {
                "key": "portable-outfit",
                "garment_keys": ["portable-top", "portable-layer"],
            },
        },
        "garments": [
            {"key": "portable-layer", "wording": "a light cardigan", "aside": "unbuttoned"},
            {"key": "portable-top", "wording": "a blue cotton top", "aside": ""},
        ],
        "provenance": None,
    }


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


def _portable_counts() -> dict[str, int]:
    return {
        table: db.one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
        for table in ("garment", "outfit", "saved_look_version")
    }


def _assert_portable_invalid(value: object) -> None:
    with pytest.raises(saved_looks.SavedLookError) as error:
        saved_looks.canonicalize_portable_look(value)
    assert error.value.status_code == 422
    assert error.value.code == "invalid_look"
    assert error.value.message == "Look data is invalid."


def test_portable_parser_rejects_duplicate_keys_and_non_json_values(client):
    raw = json.dumps(_portable_look(), separators=(",", ":"))
    duplicates = (
        raw.replace('"schema_version":1,', '"schema_version":1,"schema_version":1,', 1),
        raw.replace('"version":3,', '"version":3,"version":3,', 1),
    )
    invalid_values = (
        raw.replace('"version":3,', '"version":NaN,', 1),
        raw.replace('"version":3,', '"version":Infinity,', 1),
    )
    before = _portable_counts()

    for invalid_json in (*duplicates, *invalid_values):
        with pytest.raises(saved_looks.SavedLookError) as error:
            saved_looks.parse_portable_look_json(invalid_json)
        assert error.value.code == "invalid_look"
    assert _portable_counts() == before


def test_portable_parser_bounds_deep_json_and_large_integer_errors(client):
    deeply_nested = "[" * 1200 + "0" + "]" * 1200
    too_large_integer = json.dumps(_portable_look(), separators=(",", ":")).replace(
        '"version":3,', '"version":' + ("9" * 5000) + ',', 1,
    )
    for raw in (deeply_nested, too_large_integer):
        with pytest.raises(saved_looks.SavedLookError) as error:
            saved_looks.parse_portable_look_json(raw)
        assert error.value.code == "invalid_look"
        assert error.value.message == "Look data is invalid."


def test_portable_envelope_rejects_unknown_fields_types_and_incomplete_sets(client):
    invalid = []

    candidate = _portable_look()
    candidate["unexpected"] = "must not be discarded"
    invalid.append(candidate)

    candidate = _portable_look()
    candidate["look"]["unexpected"] = "must not be discarded"
    invalid.append(candidate)

    candidate = _portable_look()
    candidate["garments"][0]["unexpected"] = "must not be discarded"
    invalid.append(candidate)

    for bad_version in (True, 1.0, "1"):
        candidate = _portable_look()
        candidate["look"]["version"] = bad_version
        invalid.append(candidate)

    for bad_schema in (True, 1.0, "1", 2):
        candidate = _portable_look()
        candidate["schema_version"] = bad_schema
        invalid.append(candidate)

    candidate = _portable_look()
    candidate["look"]["outfit"]["garment_keys"] = ["portable-top", "portable-top"]
    invalid.append(candidate)

    candidate = _portable_look()
    candidate["garments"].append(copy.deepcopy(candidate["garments"][0]))
    invalid.append(candidate)

    candidate = _portable_look()
    candidate["garments"] = candidate["garments"][:1]
    invalid.append(candidate)

    candidate = _portable_look()
    candidate["garments"].append({"key": "extra", "wording": "an extra item", "aside": ""})
    invalid.append(candidate)

    candidate = _portable_look()
    candidate["look"]["outfit"] = None
    invalid.append(candidate)

    before = _portable_counts()
    for envelope in invalid:
        _assert_portable_invalid(envelope)
    assert _portable_counts() == before


def test_portable_order_digest_provenance_and_complete_snapshot_are_distinct(client):
    input_envelope = _portable_look()
    canonical = saved_looks.parse_portable_look_json(json.dumps(input_envelope))
    assert [item["key"] for item in canonical["garments"]] == [
        "portable-top", "portable-layer",
    ]
    assert canonical["look"]["appearance"] == "  arbitrary appearance prose\n"

    reordered_input = copy.deepcopy(input_envelope)
    reordered_input["garments"].reverse()
    assert saved_looks.portable_content_digest(reordered_input) == saved_looks.portable_content_digest(canonical)
    assert saved_looks.portable_content_digest(canonical) == resource_store.canonical_digest(canonical)

    manual = copy.deepcopy(canonical)
    manual["provenance"] = {"source": "manual", "image_sha256": None}
    photo = copy.deepcopy(canonical)
    photo["provenance"] = {"source": "photo", "image_sha256": "a" * 64}
    assert saved_looks.portable_content_digest(manual) != saved_looks.portable_content_digest(photo)
    assert saved_looks.portable_content_digest(canonical) != saved_looks.portable_content_digest(manual)

    for source in ("manual", "assistant", "import"):
        candidate = copy.deepcopy(canonical)
        candidate["provenance"] = {"source": source, "image_sha256": None}
        assert saved_looks.canonicalize_portable_look(candidate)["provenance"]["source"] == source
    assert saved_looks.canonicalize_portable_look(photo)["provenance"] == photo["provenance"]

    snapshot = saved_looks.portable_look_to_snapshot(manual)
    assert snapshot == {
        "look_id": "portable-look",
        "version": 3,
        "content_digest": resource_store.canonical_digest({
            "appearance": canonical["look"]["appearance"],
            "outfit": {
                "outfit_key": "portable-outfit",
                "garments": canonical["garments"],
            },
        }),
        "appearance": canonical["look"]["appearance"],
        "outfit": {
            "outfit_key": "portable-outfit",
            "garments": canonical["garments"],
        },
    }
    photo_snapshot = saved_looks.portable_look_to_snapshot(photo)
    assert photo_snapshot["content_digest"] == snapshot["content_digest"]


def test_portable_canonicalization_maps_bad_unicode_to_safe_error(client):
    envelope = _portable_look()
    envelope["look"]["appearance"] = "unpaired: \ud800"
    _assert_portable_invalid(envelope)
    with pytest.raises(saved_looks.SavedLookError) as parse_error:
        saved_looks.parse_portable_look_json(json.dumps(envelope))
    assert parse_error.value.code == "invalid_look"
    with pytest.raises(saved_looks.SavedLookError) as error:
        saved_looks.portable_look_to_snapshot(envelope)
    assert error.value.code == "invalid_look"


def test_portable_provenance_is_closed_untrusted_annotation(client):
    invalid_provenance = (
        {"source": "other", "image_sha256": None},
        {"source": "manual", "image_sha256": "a" * 64},
        {"source": "photo", "image_sha256": "A" * 64},
        {"source": "photo", "image_sha256": "a" * 63},
        {"source": "assistant", "image_sha256": None, "approved": True},
    )
    for provenance in invalid_provenance:
        candidate = _portable_look()
        candidate["provenance"] = provenance
        _assert_portable_invalid(candidate)

    accepted = _portable_look()
    accepted["provenance"] = {"source": "assistant", "image_sha256": None}
    canonical = saved_looks.canonicalize_portable_look(accepted)
    assert canonical["provenance"] == accepted["provenance"]
    assert set(canonical) == {"schema_version", "look", "garments", "provenance"}


def test_version_export_is_canonical_private_data_free_and_catalogue_independent(client):
    created = client.post("/api/looks", json={
        "name": "Exportable look",
        "appearance": "Short dark curls.",
        "garments": [
            {"wording": "a blue cotton top"},
            {"wording": "a light cardigan", "aside": "unbuttoned"},
        ],
    })
    assert created.status_code == 200, created.text
    saved = created.json()
    before = _portable_counts()

    response = client.get(_look_export_path(saved["key"], saved["version"]))
    assert response.status_code == 200, response.text
    exported = response.json()
    assert set(exported) == {"schema_version", "look", "garments", "provenance"}
    assert set(exported["look"]) == {"key", "version", "name", "appearance", "outfit"}
    assert set(exported["look"]["outfit"]) == {"key", "garment_keys"}
    assert exported["schema_version"] == 1
    assert exported["look"]["key"] == saved["key"]
    assert exported["look"]["version"] == saved["version"]
    assert exported["look"]["outfit"]["key"] == saved["outfit"]["outfit_key"]
    assert [garment["key"] for garment in exported["garments"]] == exported["look"]["outfit"]["garment_keys"]
    assert exported["provenance"] is None
    encoded = response.text
    assert all(secret not in encoded for secret in (
        "private-path", "data:image/", "api_key", "session_id", "assistant_request",
    ))
    assert saved_looks.portable_content_digest(exported) == saved_looks.portable_content_digest(
        saved_looks.parse_portable_look_json(response.content)
    )

    for garment in saved["outfit"]["garments"]:
        db.run("UPDATE garment SET wording = ? WHERE key = ?", "catalogue drift", garment["key"])
    db.run("UPDATE outfit SET garments = ? WHERE key = ?", "changed-catalogue-order", saved["outfit"]["outfit_key"])
    assert client.get(_look_export_path(saved["key"], saved["version"])).json() == exported
    assert _portable_counts() == before


def test_version_export_is_readable_while_writes_are_disabled(client, monkeypatch):
    created = client.post("/api/looks", json={"name": "Readable export"})
    assert created.status_code == 200, created.text
    saved = created.json()
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)

    response = client.get(_look_export_path(saved["key"], saved["version"]))
    assert response.status_code == 200, response.text
    assert response.json()["look"]["outfit"] is None
    assert response.json()["garments"] == []
    assert response.json()["provenance"] is None


def test_version_export_returns_the_saved_look_not_found_error(client):
    response = client.get(_look_export_path("missing-look", 1))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "look_not_found"


def test_version_export_matches_keys_with_slashes_and_preserves_older_routes(client):
    keys = ("ordinary-look", "historical/look-key", "historical\\look-key")
    for look_key in keys:
        _insert_historical_look(look_key)

    for look_key in keys:
        response = client.get(_look_export_path(look_key, 1))
        assert response.status_code == 200, response.text
        assert response.json()["look"]["key"] == look_key
        assert response.json()["look"]["version"] == 1

    ordinary_detail = client.get(_look_path(keys[0], 1))
    assert ordinary_detail.status_code == 200, ordinary_detail.text
    slash_detail = client.get(_look_path(keys[1], 1))
    assert slash_detail.status_code == 404
    assert client.get(_look_export_path("missing-look", 7)).status_code == 404

    manual = client.post("/api/looks", json={"name": "Manual route remains available"})
    assert manual.status_code == 200, manual.text
    assert client.get(_look_path(manual.json()["key"], 1)).status_code == 200
