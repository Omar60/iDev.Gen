from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from urllib.parse import quote

import pytest

import db
from backend import saved_looks


def _portable_look() -> dict:
    return {
        "schema_version": 1,
        "look": {
            "key": "portable/look\\identity",
            "version": 4,
            "name": "Portable layers",
            "appearance": "  appearance prose remains exact\n",
            "outfit": {
                "key": "portable/outfit",
                "garment_keys": ["garment-top", "garment-layer"],
            },
        },
        "garments": [
            {"key": "garment-layer", "wording": "a light cardigan", "aside": "unbuttoned"},
            {"key": "garment-top", "wording": "a blue cotton top", "aside": ""},
        ],
        "provenance": {"source": "photo", "image_sha256": "b" * 64},
    }


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _row_counts() -> dict[str, int]:
    return {
        table: db.one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
        for table in ("garment", "outfit", "saved_look_version")
    }


def test_public_portable_parser_fails_closed_on_raw_json_edges_without_writes(client):
    raw = json.dumps(_portable_look(), separators=(",", ":"))
    invalid_raw = [
        raw.replace('"schema_version":1,', '"schema_version":1,"schema_version":1,', 1),
        raw.replace('"version":4,', '"version":NaN,', 1),
        raw.replace('"version":4,', '"version":Infinity,', 1),
        raw.replace('"version":4,', '"version":-Infinity,', 1),
        raw.replace('"version":4,', '"version":1e999,', 1),
        "[" * 1200 + "0" + "]" * 1200,
        raw.replace('"version":4,', '"version":' + "9" * 5000 + ",", 1),
        b"\xff",
    ]
    unpaired = _portable_look()
    unpaired["look"]["appearance"] = "bad surrogate: \ud800"
    invalid_raw.append(json.dumps(unpaired, ensure_ascii=True))
    for field, value in (
        ("photo_bytes", "data:image/png;base64,ZmFrZQ=="),
        ("private_path", "synthetic/private/photo.png"),
        ("session_data", {"session_id": "synthetic-session"}),
        ("credentials", {"token": "test-placeholder"}),
    ):
        private_content = _portable_look()
        private_content[field] = value
        invalid_raw.append(json.dumps(private_content, separators=(",", ":")))
    invalid_raw.append(raw.replace(
        '"garment_keys":["garment-top","garment-layer"]',
        '"garment_keys":["garment-top","garment-layer"],"garment_keys":["garment-top","garment-layer"]',
        1,
    ))

    before = _row_counts()
    for payload in invalid_raw:
        with pytest.raises(saved_looks.SavedLookError) as error:
            saved_looks.parse_portable_look_json(payload)
        assert (error.value.status_code, error.value.code, error.value.message) == (
            422, "invalid_look", "Look data is invalid.",
        )

    late_invalid = _portable_look()
    late_invalid["garments"][-1]["aside"] = " padded aside "
    for operation in (
        saved_looks.canonicalize_portable_look,
        saved_looks.portable_content_digest,
        saved_looks.portable_look_to_snapshot,
    ):
        with pytest.raises(saved_looks.SavedLookError) as error:
            operation(late_invalid)
        assert error.value.code == "invalid_look"
    assert _row_counts() == before


def test_complete_portable_digest_and_snapshot_digest_follow_different_contracts(client):
    envelope = _portable_look()
    canonical = saved_looks.canonicalize_portable_look(envelope)
    expected = deepcopy(envelope)
    expected["garments"] = [
        {"key": "garment-top", "wording": "a blue cotton top", "aside": ""},
        {"key": "garment-layer", "wording": "a light cardigan", "aside": "unbuttoned"},
    ]
    assert canonical == expected
    assert saved_looks.portable_content_digest(envelope) == _sha256_json(expected)

    snapshot = saved_looks.portable_look_to_snapshot(envelope)
    expected_outfit = {
        "outfit_key": "portable/outfit",
        "garments": expected["garments"],
    }
    assert snapshot == {
        "look_id": "portable/look\\identity",
        "version": 4,
        "content_digest": _sha256_json({
            "appearance": "  appearance prose remains exact\n",
            "outfit": expected_outfit,
        }),
        "appearance": "  appearance prose remains exact\n",
        "outfit": expected_outfit,
    }

    for change in (
        lambda item: item["look"].update(name="Renamed"),
        lambda item: item["look"].update(key="another/look\\identity"),
        lambda item: item["look"].update(version=5),
        lambda item: item.update(provenance={"source": "manual", "image_sha256": None}),
    ):
        changed = deepcopy(envelope)
        change(changed)
        assert saved_looks.portable_content_digest(changed) != _sha256_json(expected)
        assert saved_looks.portable_look_to_snapshot(changed)["content_digest"] == snapshot["content_digest"]

    changed_appearance = deepcopy(envelope)
    changed_appearance["look"]["appearance"] = "different appearance"
    assert saved_looks.portable_look_to_snapshot(changed_appearance)["content_digest"] != snapshot["content_digest"]


@pytest.mark.parametrize("look_key", ["historical/look-key", "historical\\look-key"])
def test_export_route_round_trips_every_schema_legal_path_character(client, look_key):
    portable = _portable_look()
    portable["look"]["key"] = look_key
    assert saved_looks.canonicalize_portable_look(portable)["look"]["key"] == look_key

    outfit = None
    snapshot = {
        "look_id": look_key,
        "version": 1,
        "content_digest": _sha256_json({"appearance": "stable appearance", "outfit": outfit}),
        "appearance": "stable appearance",
        "outfit": outfit,
    }
    db.run(
        """INSERT INTO saved_look_version
           (look_key, version, name, snapshot_json, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        look_key, 1, "Historical look",
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), db.now(),
    )

    encoded_key = quote(look_key, safe="")
    response = client.get(f"/api/looks/{encoded_key}/versions/1/export")
    assert response.status_code == 200, response.text
    exported = response.json()
    assert exported["look"]["key"] == look_key
    assert set(exported) == {"schema_version", "look", "garments", "provenance"}
    assert exported["provenance"] is None
    assert saved_looks.portable_content_digest(exported) == saved_looks.portable_content_digest(
        saved_looks.parse_portable_look_json(response.content)
    )
