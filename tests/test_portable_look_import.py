from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
from urllib.parse import quote

import pytest

import db
import main
from backend import resource_store, saved_looks
from backend.request_limits import DEFAULT_MAX_JSON_BODY_BYTES


@pytest.fixture(autouse=True)
def _clear_import_receipts(client):
    db.run("DELETE FROM saved_look_import_receipt")
    yield
    db.run("DELETE FROM saved_look_import_receipt")


def _portable_look(**changes) -> dict:
    look = {
        "schema_version": 1,
        "look": {
            "key": "portable-look",
            "version": 3,
            "name": "Layered blue look",
            "appearance": "short dark curls",
            "outfit": {
                "key": "portable-outfit",
                "garment_keys": ["portable-top", "portable-layer"],
            },
        },
        "garments": [
            {"key": "portable-layer", "wording": "a light cardigan", "aside": "unbuttoned"},
            {"key": "portable-top", "wording": "a blue cotton top", "aside": ""},
        ],
        "provenance": {"source": "photo", "image_sha256": "c" * 64},
    }
    for path, value in changes.items():
        parent = look
        pieces = path.split(".")
        for piece in pieces[:-1]:
            parent = parent[piece]
        parent[pieces[-1]] = value
    return look


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


def test_disabled_new_look_import_blocks_portable_preview_before_parsing(client, monkeypatch):
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

    response = _preview(client, _portable_look())

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "resource_planning_disabled"
    assert _counts() == before


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
        key, version, name,
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
        db.now(),
    )


def test_empty_store_import_round_trips_closed_envelope_and_exact_retry(client):
    envelope = _portable_look()
    envelope["look"]["key"] = "portable/look\\identity"
    before = _counts()

    preview = _preview(client, envelope)
    assert preview.status_code == 200, preview.text
    reviewed = preview.json()
    assert reviewed["status"] == "ready"
    assert reviewed["destination"] == {"key": "portable/look\\identity", "version": 3}
    assert reviewed["mapping"] == {
        "garments": {"portable-top": "portable-top", "portable-layer": "portable-layer"},
        "outfits": {"portable-outfit": "portable-outfit"},
    }

    committed = _commit(client, envelope, reviewed)
    assert committed.status_code == 200, committed.text
    result = committed.json()
    assert result["local_origin"] == "import"
    assert result["portable_annotation"] == envelope["provenance"]
    assert result["no_op"] is False
    assert result["look"]["key"] == "portable/look\\identity"
    assert _counts() == {
        "garment": before["garment"] + 2,
        "outfit": before["outfit"] + 1,
        "saved_look_version": before["saved_look_version"] + 1,
        "saved_look_import_receipt": before["saved_look_import_receipt"] + 1,
    }

    export = client.get(
        f"/api/looks/{quote(result['look']['key'], safe='')}/versions/3/export"
    )
    assert export.status_code == 200, export.text
    assert saved_looks.portable_content_digest(export.json()) == saved_looks.portable_content_digest(envelope)
    assert export.json()["provenance"] == envelope["provenance"]

    retry_preview = _preview(client, envelope).json()
    assert retry_preview["status"] == "already_imported"
    assert retry_preview["destination"] == reviewed["destination"]
    assert _commit(client, envelope, retry_preview).json()["no_op"] is True
    assert _counts() == {
        "garment": before["garment"] + 2,
        "outfit": before["outfit"] + 1,
        "saved_look_version": before["saved_look_version"] + 1,
        "saved_look_import_receipt": before["saved_look_import_receipt"] + 1,
    }


def test_local_export_equality_is_a_no_op_and_higher_free_version_is_allowed(client):
    local = client.post("/api/looks", json={"name": "Local look", "appearance": "soft makeup"})
    assert local.status_code == 200, local.text
    saved = local.json()
    exported = client.get(f"/api/looks/{saved['key']}/versions/1/export").json()
    before = _counts()
    preview = _preview(client, exported)
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "already_equal"
    no_op = _commit(client, exported, preview.json())
    assert no_op.status_code == 200, no_op.text
    assert no_op.json()["no_op"] is True
    assert no_op.json()["local_origin"] == "existing"
    assert _counts() == before

    version_two = deepcopy(exported)
    version_two["look"]["version"] = 2
    version_two["look"]["name"] = "Local look v2"
    accepted = _preview(client, version_two)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "ready"
    created = _commit(client, version_two, accepted.json())
    assert created.status_code == 200, created.text
    assert created.json()["look"]["version"] == 2
    assert client.get(f"/api/looks/{saved['key']}/versions/1").json() == saved


def test_conflicts_require_exact_choice_and_older_unused_version_uses_latest_plus_one(client):
    _insert_look("conflict-look", 3, "Existing", "old appearance")
    envelope = _portable_look()
    envelope["look"].update(key="conflict-look", version=3)
    before = _counts()
    preview = _preview(client, envelope).json()
    assert preview["status"] == "choice_required"
    assert [item["choice"] for item in preview["choices"]] == ["new_version", "save_copy"]
    assert preview["choices"][0]["destination"] == {"key": "conflict-look", "version": 4}
    assert _commit(client, envelope, preview).status_code == 409
    assert _counts() == before

    created = _commit(client, envelope, preview, "new_version")
    assert created.status_code == 200, created.text
    assert created.json()["look"]["version"] == 4
    assert client.get("/api/looks/conflict-look/versions/3").json()["appearance"] == "old appearance"

    older = _portable_look()
    older["look"].update(key="conflict-look", version=2, name="Older source")
    older_preview = _preview(client, older).json()
    assert older_preview["status"] == "choice_required"
    assert older_preview["choices"][0]["destination"] == {"key": "conflict-look", "version": 5}


def test_save_copy_shows_catalogue_remapping_and_both_retry_paths_are_no_ops(client):
    now = db.now()
    db.run("INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
           "portable-top", "a red cotton top", "", now)
    db.run("INSERT INTO outfit (key, label, garments, created_at) VALUES (?, ?, ?, ?)",
           "portable-outfit", "Local outfit", "portable-top", now)
    _insert_look("portable-look", 3, "Occupied", "different look")
    envelope = _portable_look()
    before = _counts()

    preview_response = _preview(client, envelope)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["status"] == "choice_required"
    remaps = {(item["kind"], item["from"]) for item in preview["remapped"]}
    assert remaps == {("garment", "portable-top"), ("outfit", "portable-outfit")}
    assert preview["mapping"]["garments"]["portable-top"] != "portable-top"
    assert preview["mapping"]["outfits"]["portable-outfit"] != "portable-outfit"
    destination = preview["choices"][1]["destination"]

    saved = _commit(client, envelope, preview, "save_copy")
    assert saved.status_code == 200, saved.text
    result = saved.json()
    assert result["look"]["key"] == destination["key"]
    assert result["look"]["version"] == 1
    assert result["mapping"] == preview["mapping"]
    assert result["local_origin"] == "import"
    assert client.get("/api/looks/portable-look/versions/3").json()["appearance"] == "different look"

    receipt = db.one("SELECT * FROM saved_look_import_receipt")
    assert receipt["local_origin"] == "import"
    assert json.loads(receipt["portable_annotation_json"]) == envelope["provenance"]
    assert json.loads(receipt["garment_mapping_json"]) == preview["mapping"]["garments"]
    assert json.loads(receipt["outfit_mapping_json"]) == preview["mapping"]["outfits"]
    snapshot_row = db.one(
        "SELECT snapshot_json FROM saved_look_version WHERE look_key = ? AND version = 1",
        destination["key"],
    )
    assert set(json.loads(snapshot_row["snapshot_json"])) == {
        "look_id", "version", "content_digest", "appearance", "outfit",
    }
    counts_after_import = _counts()

    retry = _preview(client, envelope).json()
    assert retry["status"] == "already_imported"
    assert retry["destination"] == destination
    assert _commit(client, envelope, retry).json()["no_op"] is True
    assert _counts() == counts_after_import

    local_export = client.get(
        f"/api/looks/{quote(destination['key'], safe='')}/versions/1/export"
    ).json()
    local_retry = _preview(client, local_export).json()
    assert local_retry["status"] == "already_equal"
    assert _commit(client, local_export, local_retry).json()["no_op"] is True
    assert _counts() == counts_after_import
    assert local_export["provenance"] == envelope["provenance"]


def test_comma_garment_keys_are_remapped_before_csv_outfit_storage(client):
    envelope = _portable_look()
    envelope["look"]["outfit"]["garment_keys"][0] = "top,inner"
    envelope["garments"][1]["key"] = "top,inner"
    preview = _preview(client, envelope)
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["mapping"]["garments"]["top,inner"] != "top,inner"
    assert {item["reason"] for item in plan["remapped"]} == {"unrepresentable_key"}
    saved = _commit(client, envelope, plan)
    assert saved.status_code == 200, saved.text
    local_key = plan["mapping"]["garments"]["top,inner"]
    outfit_row = db.one("SELECT garments FROM outfit WHERE key = ?", "portable-outfit")
    assert outfit_row["garments"].split(",").count(local_key) == 1
    assert client.get("/api/looks/portable-look/versions/3/export").json()["look"]["outfit"]["garment_keys"] == [
        local_key, "portable-layer",
    ]


def test_prior_receipt_for_same_original_identity_keeps_different_content_conflicted(client):
    first = _portable_look()
    preview = _preview(client, first).json()
    assert _commit(client, first, preview).status_code == 200

    changed = deepcopy(first)
    changed["look"]["name"] = "Changed portable name"
    changed_preview = _preview(client, changed)
    assert changed_preview.status_code == 200, changed_preview.text
    assert changed_preview.json()["status"] == "choice_required"
    assert [item["choice"] for item in changed_preview.json()["choices"]] == ["new_version", "save_copy"]


def test_import_storage_rejects_schema_valid_integer_overflow_without_sqlite_error(client):
    too_large = _portable_look()
    too_large["look"]["version"] = 1 << 63
    assert saved_looks.canonicalize_portable_look(too_large)["look"]["version"] == 1 << 63

    before = _counts()
    rejected_preview = _preview(client, too_large)
    assert rejected_preview.status_code == 422
    assert rejected_preview.json()["detail"]["code"] == "invalid_look"
    assert _counts() == before

    reviewed = _preview(client, _portable_look()).json()
    rejected_commit = _commit(client, too_large, reviewed)
    assert rejected_commit.status_code == 422
    assert rejected_commit.json()["detail"]["code"] == "invalid_look"
    assert _counts() == before


def test_maximum_latest_version_keeps_save_copy_and_refuses_unavailable_new_version(client):
    maximum = (1 << 63) - 1
    _insert_look("full-version-range", maximum, "Existing", "different appearance")
    envelope = _portable_look()
    envelope["look"]["key"] = "full-version-range"
    envelope["look"]["version"] = maximum
    before = _counts()

    response = _preview(client, envelope)

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["status"] == "choice_required"
    assert [item["choice"] for item in plan["choices"]] == ["save_copy"]
    reviewed_copy = plan["choices"][0]["destination"]
    assert reviewed_copy["key"].startswith("look-copy-")
    assert reviewed_copy["version"] == 1

    unavailable = _commit(client, envelope, plan, "new_version")
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "look_import_choice_invalid"
    assert _counts() == before

    committed = _commit(client, envelope, plan, "save_copy")
    assert committed.status_code == 200, committed.text
    assert committed.json()["look"]["key"] == reviewed_copy["key"]
    assert committed.json()["look"]["version"] == reviewed_copy["version"]
    assert _counts() == {
        "garment": before["garment"] + 2,
        "outfit": before["outfit"] + 1,
        "saved_look_version": before["saved_look_version"] + 1,
        "saved_look_import_receipt": before["saved_look_import_receipt"] + 1,
    }

    replay = _commit(client, envelope, plan, "save_copy")
    assert replay.status_code == 200, replay.text
    assert replay.json()["no_op"] is True
    assert _counts() == {
        "garment": before["garment"] + 2,
        "outfit": before["outfit"] + 1,
        "saved_look_version": before["saved_look_version"] + 1,
        "saved_look_import_receipt": before["saved_look_import_receipt"] + 1,
    }


def test_store_drift_altered_content_and_altered_review_are_rejected_without_import_writes(client):
    envelope = _portable_look()
    preview = _preview(client, envelope).json()
    changed = deepcopy(envelope)
    changed["look"]["appearance"] = "edited after preview"
    before = _counts()
    mismatch = _commit(client, changed, preview)
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "look_import_preview_mismatch"
    assert _counts() == before

    altered_review = dict(preview)
    altered_review["review_digest"] = "0" * 64
    mismatch = _commit(client, envelope, altered_review)
    assert mismatch.status_code == 409
    assert _counts() == before

    db.run("INSERT INTO garment (key, wording, aside, created_at) VALUES (?, ?, ?, ?)",
           "unrelated-drift", "a green scarf", "", db.now())
    after_drift = _counts()
    stale = _commit(client, envelope, preview)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "look_import_preview_stale"
    assert _counts() == after_drift


def test_late_invalid_item_and_injected_receipt_failure_leave_no_partial_rows(client):
    envelope = _portable_look()
    envelope["garments"][-1]["aside"] = " invalid trailing aside "
    before = _counts()
    invalid = _preview(client, envelope)
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_look"
    assert _counts() == before

    valid = _portable_look()
    plan = _preview(client, valid).json()
    db.conn().execute(
        """CREATE TRIGGER reject_portable_receipt BEFORE INSERT ON saved_look_import_receipt
           BEGIN SELECT RAISE(ABORT, 'private injected failure'); END"""
    )
    db.conn().commit()
    try:
        failed = _commit(client, valid, plan)
        assert failed.status_code == 409
        assert "private injected failure" not in failed.text
        assert _counts() == before
    finally:
        db.conn().execute("DROP TRIGGER IF EXISTS reject_portable_receipt")
        db.conn().commit()


@pytest.mark.parametrize("field", ["name", "portable_annotation_json"])
def test_receipt_destination_digest_detects_altered_name_or_annotation(client, field):
    envelope = _portable_look()
    plan = _preview(client, envelope).json()
    result = _commit(client, envelope, plan)
    assert result.status_code == 200, result.text
    if field == "name":
        db.conn().execute("DROP TRIGGER saved_look_version_immutable")
        db.conn().execute(
            "UPDATE saved_look_version SET name = 'Tampered' WHERE look_key = ? AND version = ?",
            (envelope["look"]["key"], envelope["look"]["version"]),
        )
    else:
        db.conn().execute("DROP TRIGGER saved_look_import_receipt_immutable")
        db.conn().execute(
            "UPDATE saved_look_import_receipt SET portable_annotation_json = ?",
            (json.dumps({"source": "manual", "image_sha256": None}, separators=(",", ":")),),
        )
    db.conn().commit()
    replay = _preview(client, envelope)
    assert replay.status_code == 500
    assert replay.json()["detail"]["code"] == "look_import_receipt_invalid"


def test_missing_receipt_destination_is_diagnosed_instead_of_reallocated(client):
    envelope = _portable_look()
    plan = _preview(client, envelope).json()
    assert _commit(client, envelope, plan).status_code == 200
    db.run(
        "DELETE FROM saved_look_version WHERE look_key = ? AND version = ?",
        envelope["look"]["key"], envelope["look"]["version"],
    )
    retry = _preview(client, envelope)
    assert retry.status_code == 500
    assert retry.json()["detail"]["code"] == "look_import_receipt_invalid"
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_import_receipt")["count"] == 1


def test_concurrent_duplicate_and_different_content_imports_are_serialized(client):
    envelope = _portable_look()
    duplicate_previews = [_preview(client, envelope).json() for _ in range(2)]
    barrier = threading.Barrier(2)

    def commit_duplicate(preview):
        barrier.wait()
        return _commit(client, envelope, preview)

    with ThreadPoolExecutor(max_workers=2) as pool:
        duplicate_responses = list(pool.map(commit_duplicate, duplicate_previews))
    assert [response.status_code for response in duplicate_responses] == [200, 200]
    assert sorted(response.json()["no_op"] for response in duplicate_responses) == [False, True]
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_import_receipt")["count"] == 1

    other = _portable_look()
    other["look"]["key"] = "contested-look"
    other["look"]["version"] = 1
    changed = deepcopy(other)
    changed["look"]["appearance"] = "different concurrent content"
    plans = [_preview(client, other).json(), _preview(client, changed).json()]
    barrier = threading.Barrier(2)

    def commit_contender(args):
        candidate, preview = args
        barrier.wait()
        return _commit(client, candidate, preview)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(commit_contender, ((other, plans[0]), (changed, plans[1]))))
    assert sorted(response.status_code for response in responses) == [200, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["detail"]["code"] == "look_import_preview_stale"
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_version WHERE look_key = 'contested-look'")["count"] == 1
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_import_receipt WHERE original_look_key = 'contested-look'")["count"] == 1


@pytest.mark.parametrize("path", ["/api/looks/import/preview", "/api/looks/import/commit"])
def test_streamed_body_limit_rejects_missing_or_false_content_length_before_parsing(client, path):
    before = _counts()
    body = b" " * (DEFAULT_MAX_JSON_BODY_BYTES + 1)
    response = client.post(path, content=body, headers={
        "content-type": "application/json",
        "content-length": "1",
    })
    assert response.status_code == 413
    assert response.json() == {"detail": "Request entity too large"}
    assert _counts() == before


def test_commit_gate_and_raw_json_duplicate_keys_are_safe(client, monkeypatch):
    envelope = _portable_look()
    raw = json.dumps(envelope, separators=(",", ":")).replace(
        '"schema_version":1,', '"schema_version":1,"schema_version":1,', 1,
    )
    before = _counts()
    duplicate = client.post(
        "/api/looks/import/preview", content=raw,
        headers={"content-type": "application/json"},
    )
    assert duplicate.status_code == 422
    assert _counts() == before

    preview = _preview(client, envelope).json()
    commit_body = {
        "envelope": envelope,
        "preview_token": preview["preview_token"],
        "review_digest": preview["review_digest"],
        "choice": "import",
    }
    raw_commit = json.dumps(commit_body, separators=(",", ":")).replace(
        '"choice":"import"', '"choice":"import","choice":"save_copy"', 1,
    )
    duplicate_commit = client.post(
        "/api/looks/import/commit", content=raw_commit,
        headers={"content-type": "application/json"},
    )
    assert duplicate_commit.status_code == 422
    assert _counts() == before

    changed_token = dict(commit_body)
    changed_token["preview_token"] = (
        ("A" if preview["preview_token"][0] != "A" else "B")
        + preview["preview_token"][1:]
    )
    tampered = client.post("/api/looks/import/commit", json=changed_token)
    assert tampered.status_code == 409
    assert tampered.json()["detail"]["code"] == "look_import_preview_invalid"
    assert _counts() == before

    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    disabled = _commit(client, envelope, preview)
    assert disabled.status_code == 503
    assert disabled.json()["detail"]["code"] == "resource_planning_disabled"
    assert _counts() == before
