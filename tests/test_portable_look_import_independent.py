from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib
import json
from urllib.parse import quote

import pytest

import db
import main
from backend import resource_store, saved_looks
from backend.request_limits import DEFAULT_MAX_JSON_BODY_BYTES


@pytest.fixture(autouse=True)
def _clear_portable_receipts(client):
    db.run("DELETE FROM saved_look_import_receipt")
    yield
    db.run("DELETE FROM saved_look_import_receipt")


def _envelope(
    *,
    look_key: str = "source-look",
    version: int = 4,
    outfit_key: str | None = "source-outfit",
    garment_keys: tuple[str, ...] = ("source-top", "source-layer"),
    name: str = "Portable layers",
    appearance: str = "short dark curls",
    provenance: dict | None = None,
) -> dict:
    if outfit_key is None:
        keys: list[str] = []
        garments: list[dict] = []
        outfit = None
    else:
        keys = list(garment_keys)
        garments = [
            {"key": keys[0], "wording": "a blue cotton top", "aside": ""},
            {"key": keys[1], "wording": "a light cardigan", "aside": "unbuttoned"},
        ]
        outfit = {"key": outfit_key, "garment_keys": keys}
    return {
        "schema_version": 1,
        "look": {
            "key": look_key,
            "version": version,
            "name": name,
            "appearance": appearance,
            "outfit": outfit,
        },
        "garments": garments,
        "provenance": provenance or {"source": "photo", "image_sha256": "d" * 64},
    }


def _counts() -> dict[str, int]:
    return {
        table: db.one(f"SELECT COUNT(*) AS count FROM {table}")["count"]
        for table in ("garment", "outfit", "saved_look_version", "saved_look_import_receipt")
    }


def _preview(client, envelope: dict):
    return client.post("/api/looks/import/preview", json=envelope)


def _commit(client, envelope: dict, preview: dict, choice: str = "import"):
    return client.post("/api/looks/import/commit", json={
        "envelope": envelope,
        "preview_token": preview["preview_token"],
        "review_digest": preview["review_digest"],
        "choice": choice,
    })


def _insert_look(key: str, version: int, name: str, appearance: str) -> None:
    snapshot = {
        "look_id": key,
        "version": version,
        "content_digest": resource_store.canonical_digest({"appearance": appearance, "outfit": None}),
        "appearance": appearance,
        "outfit": None,
    }
    db.run(
        """INSERT INTO saved_look_version
           (look_key, version, name, snapshot_json, created_at) VALUES (?, ?, ?, ?, ?)""",
        key, version, name, json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), db.now(),
    )


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
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return status, response_body


def test_remapped_receipt_preserves_schema_legal_path_keys_and_comma_garment_identity(client):
    envelope = _envelope(
        look_key="portable/look\\identity",
        outfit_key="portable/outfit\\identity",
        garment_keys=("top,inner", "source-layer"),
    )
    before = _counts()
    preview_response = _preview(client, envelope)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    mapping = preview["mapping"]
    local_comma_key = mapping["garments"]["top,inner"]
    assert local_comma_key != "top,inner"
    assert mapping["outfits"] == {"portable/outfit\\identity": "portable/outfit\\identity"}

    committed = _commit(client, envelope, preview)
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert result["local_origin"] == "import"
    assert result["portable_annotation"] == envelope["provenance"]
    assert result["no_op"] is False
    assert result["mapping"] == mapping
    assert result["look"]["key"] == envelope["look"]["key"]
    assert result["look"]["outfit"]["outfit_key"] == envelope["look"]["outfit"]["key"]

    stored_order = db.one(
        "SELECT garments FROM outfit WHERE key = ?", envelope["look"]["outfit"]["key"],
    )["garments"].split(",")
    assert stored_order == [local_comma_key, "source-layer"]
    assert "top,inner" not in stored_order

    receipt = db.one("SELECT * FROM saved_look_import_receipt")
    assert receipt["original_look_key"] == envelope["look"]["key"]
    assert receipt["original_version"] == envelope["look"]["version"]
    assert receipt["portable_content_digest"] == saved_looks.portable_content_digest(envelope)
    assert json.loads(receipt["garment_mapping_json"]) == mapping["garments"]
    assert json.loads(receipt["outfit_mapping_json"]) == mapping["outfits"]
    assert receipt["destination_look_key"] == result["look"]["key"]
    assert receipt["destination_version"] == result["look"]["version"]
    assert result["look"]["content_digest"] != receipt["portable_content_digest"]

    exported = client.get(
        f"/api/looks/{quote(result['look']['key'], safe='')}/versions/{result['look']['version']}/export"
    )
    assert exported.status_code == 200, exported.text
    exported_envelope = exported.json()
    assert exported_envelope["look"]["key"] == envelope["look"]["key"]
    assert exported_envelope["look"]["outfit"]["key"] == envelope["look"]["outfit"]["key"]
    assert exported_envelope["look"]["outfit"]["garment_keys"] == [local_comma_key, "source-layer"]
    assert exported_envelope["provenance"] == envelope["provenance"]

    after_import = _counts()
    replay_preview = _preview(client, envelope).json()
    replay = _commit(client, envelope, replay_preview)
    assert replay.status_code == 200, replay.text
    assert replay.json()["no_op"] is True
    assert replay.json()["look"] == result["look"]
    assert _counts() == after_import
    assert after_import == {
        "garment": before["garment"] + 2,
        "outfit": before["outfit"] + 1,
        "saved_look_version": before["saved_look_version"] + 1,
        "saved_look_import_receipt": before["saved_look_import_receipt"] + 1,
    }


def test_save_copy_and_new_version_choices_keep_their_receipt_destinations_on_retry(client):
    _insert_look("choice-source", 3, "Existing", "old appearance")
    original = _envelope(look_key="choice-source", version=3)
    preview = _preview(client, original).json()
    assert preview["status"] == "choice_required"
    saved_copy = _commit(client, original, preview, "save_copy")
    assert saved_copy.status_code == 200, saved_copy.text
    copy_destination = {
        "key": saved_copy.json()["look"]["key"],
        "version": saved_copy.json()["look"]["version"],
    }
    assert copy_destination == preview["choices"][1]["destination"]

    changed = deepcopy(original)
    changed["look"]["appearance"] = "changed appearance"
    changed_preview_response = _preview(client, changed)
    assert changed_preview_response.status_code == 200, changed_preview_response.text
    changed_preview = changed_preview_response.json()
    assert changed_preview["status"] == "choice_required"
    assert [item["choice"] for item in changed_preview["choices"]] == ["new_version", "save_copy"]
    assert changed_preview["choices"][0]["destination"] == {"key": "choice-source", "version": 4}

    before_new_version = _counts()
    new_version = _commit(client, changed, changed_preview, "new_version")
    assert new_version.status_code == 200, new_version.text
    assert new_version.json()["look"]["version"] == 4
    after_new_version = _counts()

    exact_retry = _commit(client, changed, changed_preview, "new_version")
    assert exact_retry.status_code == 200, exact_retry.text
    assert exact_retry.json()["no_op"] is True
    assert exact_retry.json()["look"] == new_version.json()["look"]
    assert _counts() == after_new_version

    wrong_choice_retry = _commit(client, changed, changed_preview, "save_copy")
    assert wrong_choice_retry.status_code == 409
    assert wrong_choice_retry.json()["detail"]["code"] == "look_import_choice_invalid"
    assert _counts() == after_new_version

    source_retry = _preview(client, original).json()
    assert source_retry["status"] == "already_imported"
    assert source_retry["destination"] == copy_destination
    replay = _commit(client, original, source_retry)
    assert replay.status_code == 200, replay.text
    assert replay.json()["no_op"] is True
    assert replay.json()["look"]["key"] == copy_destination["key"]
    assert _counts() == after_new_version
    assert before_new_version["saved_look_import_receipt"] + 1 == after_new_version["saved_look_import_receipt"]


@pytest.mark.parametrize("field", ["garment_mapping_json", "outfit_mapping_json", "destination_digest"])
def test_receipt_detects_corrupted_mapping_or_destination_digest_without_reallocation(client, field):
    envelope = _envelope()
    preview = _preview(client, envelope).json()
    committed = _commit(client, envelope, preview)
    assert committed.status_code == 200, committed.text
    before_replay = _counts()

    db.conn().execute("DROP TRIGGER saved_look_import_receipt_immutable")
    replacement = "{}" if field.endswith("mapping_json") else "f" * 64
    db.conn().execute(f"UPDATE saved_look_import_receipt SET {field} = ?", (replacement,))
    db.conn().commit()

    replay = _preview(client, envelope)
    assert replay.status_code == 500
    assert replay.json()["detail"]["code"] == "look_import_receipt_invalid"
    assert _counts() == before_replay
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_import_receipt")["count"] == 1


def test_schema_valid_version_above_sqlite_integer_range_returns_safe_error_without_writes(client):
    envelope = _envelope(look_key="large-version", version=2**63)
    raw = json.dumps(envelope, separators=(",", ":"))
    assert saved_looks.parse_portable_look_json(raw)["look"]["version"] == 2**63
    before = _counts()
    transport = client._transport
    old_raise = transport.raise_server_exceptions
    transport.raise_server_exceptions = False
    try:
        preview_response = _preview(client, envelope)
        response = (
            _commit(client, envelope, preview_response.json())
            if preview_response.status_code == 200 else preview_response
        )
    finally:
        transport.raise_server_exceptions = old_raise
    assert _counts() == before
    assert response.status_code in {400, 409, 422}, response.text


def test_sqlite_max_declared_version_imports_and_replays_without_writes(client):
    maximum = (1 << 63) - 1
    envelope = _envelope(
        look_key="maximum-declared-version",
        version=maximum,
        outfit_key=None,
        name="Maximum version",
        appearance="stable appearance",
    )
    before = _counts()

    preview = _preview(client, envelope)
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["status"] == "ready"
    assert plan["destination"] == {"key": "maximum-declared-version", "version": maximum}
    created = _commit(client, envelope, plan)
    assert created.status_code == 200, created.text
    assert created.json()["look"]["version"] == maximum
    after_import = _counts()

    replay_plan = _preview(client, envelope)
    assert replay_plan.status_code == 200, replay_plan.text
    assert replay_plan.json()["status"] == "already_imported"
    replay = _commit(client, envelope, replay_plan.json())
    assert replay.status_code == 200, replay.text
    assert replay.json()["no_op"] is True
    assert _counts() == after_import
    assert after_import["saved_look_version"] == before["saved_look_version"] + 1
    assert after_import["saved_look_import_receipt"] == before["saved_look_import_receipt"] + 1


def test_sqlite_max_latest_version_still_allows_safe_save_copy(client):
    maximum = (1 << 63) - 1
    _insert_look("full-version-range", maximum, "Existing", "different appearance")
    envelope = _envelope(
        look_key="full-version-range",
        version=maximum,
        outfit_key=None,
        name="Portable copy",
        appearance="portable appearance",
    )
    preview = _preview(client, envelope)
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["status"] == "choice_required"
    assert [item["choice"] for item in plan["choices"]] == ["save_copy"]
    assert plan["choices"][0]["destination"]["version"] == 1
    assert plan["choices"][0]["destination"]["key"] != "full-version-range"

    before_commit = _counts()
    unsupported = _commit(client, envelope, plan, "new_version")
    assert unsupported.status_code == 409
    assert unsupported.json()["detail"]["code"] == "look_import_choice_invalid"
    assert _counts() == before_commit

    saved = _commit(client, envelope, plan, "save_copy")
    assert saved.status_code == 200, saved.text
    assert saved.json()["look"]["key"] == plan["choices"][0]["destination"]["key"]
    assert saved.json()["look"]["version"] == 1


def test_local_equal_row_cannot_replace_a_reviewed_save_copy_destination_after_store_change(client):
    envelope = _envelope(
        look_key="race-source",
        version=1,
        outfit_key=None,
        name="Reviewed copy",
        appearance="reviewed appearance",
    )
    envelope["provenance"] = None
    _insert_look("race-source", 1, "Existing", "different appearance")
    preview = _preview(client, envelope).json()
    assert preview["status"] == "choice_required"
    reviewed_destination = preview["choices"][1]["destination"]

    # Replace the occupied identity between preview and commit to model a concurrent
    # store change that now looks locally equal to the submitted envelope.
    db.run("DELETE FROM saved_look_version WHERE look_key = ? AND version = ?", "race-source", 1)
    snapshot = {
        "look_id": "race-source",
        "version": 1,
        "content_digest": resource_store.canonical_digest({
            "appearance": envelope["look"]["appearance"], "outfit": None,
        }),
        "appearance": envelope["look"]["appearance"],
        "outfit": None,
    }
    db.run(
        """INSERT INTO saved_look_version
           (look_key, version, name, snapshot_json, created_at) VALUES (?, ?, ?, ?, ?)""",
        "race-source", 1, envelope["look"]["name"],
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), db.now(),
    )
    before_commit = _counts()

    response = _commit(client, envelope, preview, "save_copy")
    assert _counts() == before_commit
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] in {
        "look_import_preview_stale", "look_import_choice_invalid",
    }
    assert reviewed_destination["key"].startswith("look-copy-")


def test_latest_version_change_after_review_requires_a_fresh_preview(client):
    _insert_look("latest-race", 1, "Existing", "old appearance")
    envelope = _envelope(look_key="latest-race", version=1)
    preview = _preview(client, envelope).json()
    assert preview["status"] == "choice_required"

    concurrent = client.post("/api/looks/latest-race/versions", json={
        "expected_version": 1,
        "name": "Concurrent version",
        "appearance": "concurrent appearance",
    })
    assert concurrent.status_code == 200, concurrent.text
    before_stale_commit = _counts()

    stale = _commit(client, envelope, preview, "new_version")
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "look_import_preview_stale"
    assert _counts() == before_stale_commit


def test_raw_http_rejects_boolean_version_and_unknown_helper_choice_without_writes(client):
    envelope = _envelope()
    raw_preview = json.dumps(envelope, separators=(",", ":")).replace(
        '"version":4,', '"version":true,', 1,
    )
    before = _counts()
    invalid_preview = client.post(
        "/api/looks/import/preview", content=raw_preview,
        headers={"content-type": "application/json"},
    )
    assert invalid_preview.status_code == 422
    assert _counts() == before

    valid_preview = _preview(client, envelope).json()
    raw_commit_body = json.dumps({
        "envelope": envelope,
        "preview_token": valid_preview["preview_token"],
        "review_digest": valid_preview["review_digest"],
        "choice": "import",
    }, separators=(",", ":")).replace('"version":4,', '"version":true,', 1)
    invalid_commit = client.post(
        "/api/looks/import/commit", content=raw_commit_body,
        headers={"content-type": "application/json"},
    )
    assert invalid_commit.status_code == 422
    assert _counts() == before

    with pytest.raises(saved_looks.SavedLookError) as invalid_choice:
        saved_looks.commit_portable_import(
            envelope,
            valid_preview["preview_token"],
            valid_preview["review_digest"],
            "unreviewed-choice",
        )
    assert invalid_choice.value.status_code == 409
    assert invalid_choice.value.code == "look_import_choice_invalid"
    assert _counts() == before


@pytest.mark.parametrize("path", ["/api/looks/import/preview", "/api/looks/import/commit"])
def test_missing_content_length_is_counted_before_any_portable_json_parser(client, monkeypatch, path):
    body = b" " * (DEFAULT_MAX_JSON_BODY_BYTES + 1)
    before = _counts()
    monkeypatch.setattr(saved_looks, "parse_portable_look_json", lambda *_: pytest.fail("preview parser ran"))
    monkeypatch.setattr(saved_looks, "parse_portable_look_commit_json", lambda *_: pytest.fail("commit parser ran"))

    status, raw_response = _send_asgi_post(path, body, content_length=None)
    assert status == 413
    assert json.loads(raw_response) == {"detail": "Request entity too large"}
    assert _counts() == before


def test_disabled_commit_gate_skips_import_parsing_and_persistence(client, monkeypatch):
    envelope = _envelope()
    preview = _preview(client, envelope).json()
    before = _counts()
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    monkeypatch.setattr(saved_looks, "parse_portable_look_commit_json", lambda *_: pytest.fail("commit parser ran"))
    monkeypatch.setattr(saved_looks, "commit_portable_import", lambda *_: pytest.fail("commit service ran"))

    response = _commit(client, envelope, preview)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "resource_planning_disabled"
    assert _counts() == before


def test_preview_token_survives_module_reload_while_unexpired(client):
    envelope = _envelope(look_key="restart-token")
    preview = _preview(client, envelope).json()
    error_type = saved_looks.SavedLookError
    importlib.reload(saved_looks)
    # Keep the application exception handler's registered class identity stable
    # while reloading the service module as a process-restart proxy.
    saved_looks.SavedLookError = error_type

    committed = _commit(client, envelope, preview)
    assert committed.status_code == 200, committed.text
    assert committed.json()["no_op"] is False
