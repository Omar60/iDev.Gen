"""Independent adversarial probes for Task 10.1 operational disablement."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

import db
import main
from backend import resource_planning, resource_selection, resource_service, session_plan


def _portable_look() -> dict:
    return {
        "schema_version": 1,
        "look": {
            "key": "independent-portable",
            "version": 1,
            "name": "Independent look",
            "appearance": "short dark curls",
            "outfit": None,
        },
        "garments": [],
        "provenance": None,
    }


def _legacy_look() -> dict:
    return {
        "garments": [{"key": "independent-top", "wording": "a cotton top"}],
        "outfits": [],
    }


def _look_write_counts() -> dict[str, int]:
    tables = (
        "garment", "outfit", "saved_look_version", "saved_look_import_receipt",
        "saved_look_legacy_import_receipt",
    )
    return {
        table: int(db.one(f"SELECT COUNT(*) AS n FROM {table}")["n"])
        for table in tables
    }


def test_live_config_gate_cannot_be_overridden_by_planning_enabled_argument(client, monkeypatch):
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)

    assert resource_planning.is_enabled() is False
    before = db.conn().total_changes
    with pytest.raises(session_plan.ResourcePlanningDisabled):
        session_plan.refresh_resources(0, 0, planning_enabled=True)
    assert db.conn().total_changes == before


def test_legacy_selection_wrapper_is_gated_before_a_valid_direct_write(client, monkeypatch):
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "false")
    before = db.conn().total_changes

    request_id = str(uuid.uuid4())
    with pytest.raises(resource_selection.ResourcePlanningDisabledError):
        resource_selection.create_selection(request_id)

    assert db.conn().total_changes == before
    assert db.one(
        "SELECT COUNT(*) AS n FROM resource_selection WHERE request_id=?", request_id,
    )["n"] == 0


def test_cancel_while_disabled_cleans_an_existing_staged_file(client, monkeypatch):
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    selection = client.post(
        "/api/resources/import-selections", json={"request_id": str(uuid.uuid4())},
    )
    assert selection.status_code == 201, selection.text
    sid = selection.json()["selection_id"]
    upload = client.post(
        f"/api/resources/import-selections/{sid}/files",
        files={"file": ("invented.json", b'{"items":[]}', "application/json")},
        data={"upload_id": "independent-cancel-upload"},
    )
    assert upload.status_code == 201, upload.text
    revision = upload.json()["selection_revision"]
    staged = db.one(
        "SELECT staged_path FROM resource_selection_file WHERE selection_id=?", sid,
    )
    staged_path = Path(staged["staged_path"])
    assert staged_path.is_file()

    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
    response = client.post(
        f"/api/resources/import-selections/{sid}/cancel",
        json={"expected_revision": revision},
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "cancelled"
    assert client.get(f"/api/resources/import-selections/{sid}").json()["state"] == "cancelled"
    assert not staged_path.exists()
    stored = db.one(
        "SELECT cleanup_state FROM resource_selection_file WHERE selection_id=?", sid,
    )
    assert stored["cleanup_state"] == "cleaned"


@pytest.mark.parametrize("document", [_portable_look(), _legacy_look()], ids=["portable", "legacy"])
def test_disabled_new_look_preview_leaves_a_fresh_signing_key_absent(
    client, monkeypatch, tmp_path, document,
):
    key_path = tmp_path / ".resource-preview-key"
    monkeypatch.setattr(resource_service, "_database_directory", lambda: tmp_path)
    monkeypatch.setattr(main, "is_resource_planning_enabled", lambda: False)
    before = _look_write_counts()
    assert not key_path.exists()

    response = client.post("/api/looks/import/preview", json=document)

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "resource_planning_disabled"
    assert _look_write_counts() == before
    assert not key_path.exists()


def test_path_backed_resource_import_keeps_compatibility_behavior_when_disabled(
    client, monkeypatch, tmp_path,
):
    source = tmp_path / "invented-room.json"
    source.write_text(json.dumps([{
        "id": "independent_room",
        "label": "Invented room",
        "scene_theme": "An empty studio with soft daylight.",
    }]), encoding="utf-8")
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "false")

    preview = client.post("/api/resources/import/preview", json={
        "selections": [{"path": str(source), "library_key": "independent_legacy_library"}],
    })
    assert preview.status_code == 200, preview.text
    assert db.one(
        "SELECT id FROM resource_library WHERE library_key=?", "independent_legacy_library",
    ) is None

    committed = client.post("/api/resources/import/commit", json={
        "preview": preview.json()["preview"],
    })
    assert committed.status_code == 200, committed.text
    assert db.one(
        "SELECT id FROM resource_library WHERE library_key=?", "independent_legacy_library",
    ) is not None
    assert db.one(
        "SELECT id FROM asset_revision WHERE source_id=?", "independent_room",
    ) is not None


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/looks", {"name": "race look", "appearance": "short dark curls"}),
        (
            "/api/sessions/guided",
            {
                "request_id": str(uuid.uuid4()),
                "character_id": 999999,
                "scene_anchor": {
                    "library_key": "race_library",
                    "source_id": "race_room",
                    "content_digest": "a" * 64,
                },
                "photo_count": 1,
            },
        ),
        (
            "/api/resources/libraries/race-library/translations/preview",
            {"translation_map": {}},
        ),
        (
            "/api/looks/photo-stages/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/extract",
            {},
        ),
    ],
    ids=["saved-look", "guided-session", "translation", "photo-extraction"],
)
def test_live_disable_after_http_preflight_still_returns_503_without_writes(
    client, monkeypatch, path, payload,
):
    checks = []

    def enabled_then_disabled():
        checks.append(True)
        return len(checks) == 1

    monkeypatch.setattr(main, "is_resource_planning_enabled", enabled_then_disabled)
    before = db.conn().total_changes

    response = client.post(path, json=payload)

    assert response.status_code == 503, response.text
    assert len(checks) >= 2
    assert db.conn().total_changes == before
