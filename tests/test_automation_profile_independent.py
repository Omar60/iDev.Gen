"""Adversarial input checks for exact saved-look profile APIs."""
from __future__ import annotations

import json
import sqlite3
from copy import deepcopy

import pytest

import db
from backend import resource_planning, resource_store, saved_looks


def _profile(saved: dict) -> dict:
    provenance = {
        "origin": "user",
        "source_identity": "profile-editor",
        "source_digest": saved["content_digest"],
        "accepted_revision": 1,
    }
    appearance = saved["appearance"]
    return {
        "schema_version": 1,
        "look": {
            "key": saved["key"],
            "version": saved["version"],
            "content_digest": saved["content_digest"],
        },
        "appearance_segments": [{
            "segment_id": "appearance-1",
            "start": 0,
            "end": len(appearance),
            "text": appearance,
            "owner": "permanent",
            "provenance": provenance,
        }],
        "garment_coverage": {},
        "baseline_coverage": None,
        "inventory_complete": {"value": None, "provenance": provenance},
        "authored_provenance": provenance,
    }


def test_pre_profile_saved_look_database_upgrades_additively(tmp_path, monkeypatch):
    path = tmp_path / "legacy-profiles.db"
    appearance = "Legacy saved appearance."
    snapshot = {
        "look_id": "legacy-look",
        "version": 1,
        "content_digest": resource_store.canonical_digest({
            "appearance": appearance, "outfit": None,
        }),
        "appearance": appearance,
        "outfit": None,
    }
    legacy = sqlite3.connect(path)
    legacy.execute(
        """CREATE TABLE saved_look_version (
             id INTEGER PRIMARY KEY,
             look_key TEXT NOT NULL,
             version INTEGER NOT NULL CHECK (version > 0),
             name TEXT NOT NULL,
             snapshot_json TEXT NOT NULL,
             created_at TEXT NOT NULL,
             UNIQUE (look_key, version)
           )"""
    )
    legacy.execute(
        """CREATE TRIGGER saved_look_version_immutable
           BEFORE UPDATE ON saved_look_version
           BEGIN
             SELECT RAISE(ABORT, 'saved look versions are immutable');
           END"""
    )
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    legacy.execute(
        "INSERT INTO saved_look_version VALUES (1, ?, 1, ?, ?, ?)",
        ("legacy-look", "Legacy look", snapshot_json, "2026-01-01T00:00:00+00:00"),
    )
    legacy.commit()
    legacy_columns = legacy.execute("PRAGMA table_info(saved_look_version)").fetchall()
    legacy_row = legacy.execute(
        "SELECT * FROM saved_look_version WHERE look_key = ? AND version = 1",
        ("legacy-look",),
    ).fetchone()
    legacy.close()

    monkeypatch.setattr(resource_planning, "is_enabled", lambda: True)
    upgraded = db.connect(path)

    assert [tuple(row) for row in upgraded.execute(
        "PRAGMA table_info(saved_look_version)",
    ).fetchall()] == legacy_columns
    assert tuple(upgraded.execute(
        "SELECT * FROM saved_look_version WHERE look_key = ? AND version = 1",
        ("legacy-look",),
    ).fetchone()) == legacy_row
    saved = saved_looks.get_version("legacy-look", 1)
    assert saved == {
        "key": "legacy-look",
        "version": 1,
        "name": "Legacy look",
        "content_digest": snapshot["content_digest"],
        "appearance": appearance,
        "outfit": None,
    }
    before_export = saved_looks.export_version("legacy-look", 1)
    published = saved_looks.publish_automation_profile(
        "legacy-look", 1, snapshot["content_digest"], 0, _profile(saved),
    )
    assert published["profile_revision"] == 1
    assert saved_looks.export_version("legacy-look", 1) == before_export
    assert set(before_export) == {"schema_version", "look", "garments", "provenance"}
    assert upgraded.execute(
        "SELECT COUNT(*) FROM saved_look_automation_profile",
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "oversized",
    [lambda: 1 << 63, lambda: 10**5000],
    ids=["sqlite-integer-overflow", "very-large-integer"],
)
def test_profile_identity_integer_overflow_is_a_safe_refusal_without_writes(
    tmp_path, monkeypatch, oversized,
):
    monkeypatch.setattr(resource_planning, "is_enabled", lambda: True)
    db.connect(tmp_path / "profiles.db")
    saved = saved_looks.create({"name": "Overflow fixture", "appearance": "Simple look."})
    profile = _profile(saved)
    large = oversized()

    calls = (
        lambda: saved_looks.publish_automation_profile(
            saved["key"], large, saved["content_digest"], 0, profile,
        ),
        lambda: saved_looks.get_automation_profile(
            saved["key"], large, 1, saved["content_digest"], "0" * 64,
        ),
        lambda: saved_looks.get_automation_profile(
            saved["key"], saved["version"], large, saved["content_digest"], "0" * 64,
        ),
        lambda: saved_looks.get_latest_automation_profile(saved["key"], large),
    )
    for call in calls:
        with pytest.raises(saved_looks.SavedLookError) as raised:
            call()
        assert raised.value.status_code == 422

    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_automation_profile",
    )["count"] == 0


def test_portable_v1_round_trip_does_not_transfer_automation_profiles(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(resource_planning, "is_enabled", lambda: True)
    db.connect(tmp_path / "profiles.db")
    saved = saved_looks.create({"name": "Portable source", "appearance": "Simple look."})
    profile = _profile(saved)
    published = saved_looks.publish_automation_profile(
        saved["key"], saved["version"], saved["content_digest"], 0, profile,
    )
    source_envelope = saved_looks.export_version(saved["key"], saved["version"])
    source_digest = saved_looks.portable_content_digest(source_envelope)
    envelope = deepcopy(source_envelope)
    assert "automation_profile" not in envelope

    envelope["look"]["key"] = "portable-copy"
    preview = saved_looks.preview_portable_import(envelope)
    assert preview["status"] == "ready"
    imported = saved_looks.commit_portable_import(
        envelope, preview["preview_token"], preview["review_digest"], "import",
    )

    assert imported["look"]["key"] == "portable-copy"
    assert saved_looks.export_version(saved["key"], saved["version"]) == source_envelope
    assert saved_looks.portable_content_digest(source_envelope) == source_digest
    assert saved_looks.get_automation_profile(
        saved["key"], saved["version"], published["profile_revision"],
        saved["content_digest"], published["profile_digest"],
    ) == published
    assert saved_looks.get_latest_automation_profile("portable-copy", 1) is None
    assert saved_looks.portable_content_digest(
        saved_looks.export_version("portable-copy", 1),
    ) == saved_looks.portable_content_digest(envelope)
