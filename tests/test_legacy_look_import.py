from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

import db
import main
from backend import saved_looks
from backend.request_limits import DEFAULT_MAX_JSON_BODY_BYTES


@pytest.fixture(autouse=True)
def _clear_legacy_import_receipts(client):
    db.run("DELETE FROM saved_look_legacy_import_receipt")
    yield
    db.run("DELETE FROM saved_look_legacy_import_receipt")


def _wardrobe(**changes) -> dict:
    value = {
        "garments": [
            {"key": "legacy-top", "wording": "a blue cotton top"},
            {"key": "legacy-layer", "wording": "a light cardigan", "aside": "unbuttoned"},
            {"key": "legacy-spare", "wording": "a folded scarf"},
        ],
        "outfits": [
            {"key": "legacy-layers", "garments": ["legacy-top", "legacy-layer"]},
            {"key": "legacy-reversed", "label": "Reverse layers", "garments": "legacy-layer,legacy-top"},
        ],
    }
    value.update(changes)
    return value


def _counts() -> dict[str, int]:
    return {
        table: db.one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
        for table in (
            "garment", "outfit", "saved_look_version", "saved_look_import_receipt",
            "saved_look_legacy_import_receipt",
        )
    }


def _preview(client, value: dict):
    return client.post("/api/looks/import/preview", json=value)


def _commit(client, value: dict, preview: dict, choice: str = "import"):
    return client.post("/api/looks/import/commit", json={
        "envelope": value,
        "preview_token": preview["preview_token"],
        "review_digest": preview["review_digest"],
        "choice": choice,
    })


def test_disabled_new_look_import_blocks_legacy_format_before_parsing(client, monkeypatch):
    monkeypatch.setattr(main, "is_resource_planning_enabled", lambda: False)
    monkeypatch.setattr(
        saved_looks,
        "parse_look_import_preview_json",
        lambda *_: pytest.fail("disabled import reached JSON parsing"),
    )
    monkeypatch.setattr(
        saved_looks,
        "_portable_preview_key",
        lambda **_: pytest.fail("disabled import created preview signing material"),
    )
    before = _counts()

    response = _preview(client, _wardrobe())

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "resource_planning_disabled"
    assert _counts() == before


def _send_asgi_post(path: str, body: bytes, *, content_length: bytes | None) -> tuple[int, bytes]:
    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", content_length))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
        "state": {},
    }
    sent = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(main.app(scope, receive, send))
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return start["status"], response_body


def test_legacy_import_reviews_full_order_and_explicitly_converts_to_named_look(client):
    value = _wardrobe()
    before = _counts()

    preview_response = _preview(client, value)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["status"] == "ready"
    assert preview["choices"] == ["import"]
    assert preview["mapping"] == {
        "garments": {
            "legacy-layer": "legacy-layer",
            "legacy-spare": "legacy-spare",
            "legacy-top": "legacy-top",
        },
        "outfits": {
            "legacy-layers": "legacy-layers",
            "legacy-reversed": "legacy-reversed",
        },
    }
    assert preview["resolved_garments"][0] == {
        "source_key": "legacy-layer",
        "key": "legacy-layer",
        "wording": "a light cardigan",
        "aside": "unbuttoned",
    }
    assert preview["resolved_outfits"][0] == {
        "source_key": "legacy-layers",
        "key": "legacy-layers",
        "label": "legacy-layers",
        "garments": ["legacy-top", "legacy-layer"],
    }

    committed_response = _commit(client, value, preview)
    assert committed_response.status_code == 200, committed_response.text
    committed = committed_response.json()
    assert committed["no_op"] is False
    assert committed["outfits"][0]["garments"] == ["legacy-top", "legacy-layer"]
    assert db.one("SELECT wording, aside FROM garment WHERE key='legacy-top'") == {
        "wording": "a blue cotton top", "aside": "",
    }
    assert db.one("SELECT label, garments FROM outfit WHERE key='legacy-reversed'") == {
        "label": "Reverse layers", "garments": "legacy-layer,legacy-top",
    }
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_version")["count"] == 0
    assert _counts() == {
        **before,
        "garment": before["garment"] + 3,
        "outfit": before["outfit"] + 2,
        "saved_look_legacy_import_receipt": before["saved_look_legacy_import_receipt"] + 1,
    }

    named = client.post("/api/looks", json={"name": "Blue layers", "outfit_key": "legacy-layers"})
    assert named.status_code == 200, named.text
    assert named.json()["name"] == "Blue layers"
    assert named.json()["outfit"]["outfit_key"] == "legacy-layers"


def test_legacy_import_resolves_catalogue_references_and_refuses_unknown_before_writes(client):
    db.run(
        "INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
        "catalogue-shirt", "a soft linen shirt", "", db.now(),
    )
    value = {"outfits": [{"key": "catalogue-outfit", "garments": " catalogue-shirt "}]}
    preview = _preview(client, value)
    assert preview.status_code == 200, preview.text
    assert preview.json()["resolved_outfits"][0]["garments"] == ["catalogue-shirt"]
    committed = _commit(client, value, preview.json())
    assert committed.status_code == 200, committed.text
    assert db.one("SELECT COUNT(*) AS count FROM garment WHERE key='catalogue-shirt'")["count"] == 1

    before = _counts()
    unknown = {
        "garments": [{"key": "valid-first", "wording": "a valid garment"}],
        "outfits": [{"key": "invalid-last", "garments": ["not-in-store"]}],
    }
    refused = _preview(client, unknown)
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "look_import_unknown_garment"
    assert _counts() == before


def test_legacy_garments_only_input_is_accepted_without_creating_an_outfit_or_look(client):
    value = {"garments": [{"key": "garments-only", "wording": "a green scarf"}]}
    preview = _preview(client, value)
    assert preview.status_code == 200, preview.text
    assert preview.json()["resolved_outfits"] == []
    committed = _commit(client, value, preview.json())
    assert committed.status_code == 200, committed.text
    assert db.one("SELECT wording FROM garment WHERE key='garments-only'")["wording"] == "a green scarf"
    assert db.one("SELECT COUNT(*) AS count FROM outfit")["count"] == 0
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_version")["count"] == 0


def test_exact_existing_legacy_content_is_a_catalogue_no_op(client):
    db.run(
        "INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
        "existing-shirt", "a white linen shirt", "", db.now(),
    )
    db.run(
        "INSERT INTO outfit (key, label, garments, created_at) VALUES (?, ?, ?, ?)",
        "existing-outfit", "White layers", "existing-shirt", db.now(),
    )
    value = {
        "garments": [{"key": "existing-shirt", "wording": "a white linen shirt"}],
        "outfits": [{"key": "existing-outfit", "label": "White layers", "garments": ["existing-shirt"]}],
    }
    before = _counts()
    preview = _preview(client, value)
    assert preview.status_code == 200, preview.text
    assert preview.json()["mode"] == "already_equal"
    committed = _commit(client, value, preview.json())
    assert committed.status_code == 200, committed.text
    assert committed.json()["no_op"] is True
    assert _counts() == before


def test_legacy_conflict_requires_save_copy_and_receipt_makes_replay_a_no_op(client):
    db.run(
        "INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
        "shared-key", "the original wording", "", db.now(),
    )
    db.run(
        "INSERT INTO outfit (key, label, garments, created_at) VALUES (?, ?, ?, ?)",
        "shared-outfit", "Original", "shared-key", db.now(),
    )
    value = {
        "garments": [{"key": "shared-key", "wording": "the changed wording"}],
        "outfits": [{"key": "shared-outfit", "label": "Changed", "garments": ["shared-key"]}],
    }
    preview_response = _preview(client, value)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["status"] == "choice_required"
    assert preview["choices"] == ["save_copy"]
    assert [item["from"] for item in preview["remapped"]] == ["shared-key", "shared-outfit"]
    before = _counts()

    refused = _commit(client, value, preview, "import")
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "look_import_choice_invalid"
    assert _counts() == before

    copied_response = _commit(client, value, preview, "save_copy")
    assert copied_response.status_code == 200, copied_response.text
    copied = copied_response.json()
    assert copied["no_op"] is False
    assert db.one("SELECT wording FROM garment WHERE key='shared-key'")["wording"] == "the original wording"
    copied_garment = copied["mapping"]["garments"]["shared-key"]
    copied_outfit = copied["mapping"]["outfits"]["shared-outfit"]
    assert copied_garment != "shared-key"
    assert copied_outfit != "shared-outfit"
    assert db.one("SELECT garments FROM outfit WHERE key=?", copied_outfit)["garments"] == copied_garment

    replay_preview_response = _preview(client, value)
    assert replay_preview_response.status_code == 200, replay_preview_response.text
    replay_preview = replay_preview_response.json()
    assert replay_preview["mode"] == "receipt_replay"
    assert replay_preview["mapping"] == copied["mapping"]
    replay = _commit(client, value, replay_preview)
    assert replay.status_code == 200, replay.text
    assert replay.json()["no_op"] is True
    assert _counts() == {**before, "garment": before["garment"] + 1, "outfit": before["outfit"] + 1,
                         "saved_look_legacy_import_receipt": before["saved_look_legacy_import_receipt"] + 1}


def test_identical_concurrent_legacy_commits_serialize_to_one_import(client):
    value = _wardrobe(outfits=[{"key": "one-outfit", "garments": "legacy-top,legacy-layer"}])
    document = saved_looks.canonicalize_legacy_wardrobe(value)
    preview = saved_looks.preview_legacy_import(document)
    barrier = threading.Barrier(2)

    def commit():
        barrier.wait()
        return saved_looks.commit_legacy_import(
            document, preview["preview_token"], preview["review_digest"], "import",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: commit(), range(2)))
    assert sorted(result["no_op"] for result in results) == [False, True]
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_legacy_import_receipt")["count"] == 1
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_version")["count"] == 0


@pytest.mark.parametrize("content_length", [None, b"1"])
@pytest.mark.parametrize("path", ["/api/looks/import/preview", "/api/looks/import/commit"])
def test_legacy_routes_count_actual_body_bytes_with_missing_or_false_length(client, path, content_length):
    before = _counts()
    status, response_body = _send_asgi_post(
        path, b" " * (DEFAULT_MAX_JSON_BODY_BYTES + 1), content_length=content_length,
    )
    assert status == 413
    assert json.loads(response_body) == {"detail": "Request entity too large"}
    assert _counts() == before


@pytest.mark.parametrize("value", [
    {"garments": None},
    {"outfits": []},
    {"garments": [{"key": "shirt", "wording": "top", "unknown": True}]},
    {"outfits": [{"key": "layers", "garments": ["shirt", "shirt"]}]},
])
def test_legacy_parser_rejects_null_unknown_and_duplicate_references(value):
    with pytest.raises(saved_looks.SavedLookError):
        saved_looks.canonicalize_legacy_wardrobe(value)


def test_legacy_json_parser_rejects_duplicate_object_keys():
    raw = b'{"garments":[],"garments":[{"key":"shirt","wording":"a shirt"}]}'
    with pytest.raises(saved_looks.SavedLookError):
        saved_looks.parse_look_import_preview_json(raw)
