"""Persistence and closed validation for immutable saved-look profiles."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
import shutil
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

import db
from backend import resource_planning, resource_store, saved_looks


def _open(path, monkeypatch):
    monkeypatch.setattr(resource_planning, "is_enabled", lambda: True)
    return db.connect(path)


def _saved_look():
    return saved_looks.create({
        "name": "Layered profile fixture",
        "appearance": "A 😀 model with e\u0301.",
        "garments": [{"wording": "a brass bracelet", "aside": ""}],
    })


def _provenance(saved, revision=1, identity="profile-editor"):
    return {
        "origin": "user",
        "source_identity": identity,
        "source_digest": saved["content_digest"],
        "accepted_revision": revision,
    }


def _state(saved, revision=1, value="unknown"):
    return {
        "regions": {region: value for region in ("chest", "hips", "legs", "feet")},
        "provenance": _provenance(saved, revision),
    }


def _profile(saved, revision=1):
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
            "provenance": _provenance(saved, revision),
        }],
        "garment_coverage": {
            saved["outfit"]["garments"][0]["key"]: {
                "worn": _state(saved, revision),
            },
        },
        "baseline_coverage": None,
        "inventory_complete": {
            "value": None,
            "provenance": _provenance(saved, revision),
        },
        "authored_provenance": _provenance(saved, revision),
    }


def _publish(saved, profile, expected_revision=0):
    return saved_looks.publish_automation_profile(
        saved["key"], saved["version"], saved["content_digest"],
        expected_revision, profile,
    )


def test_profile_schema_is_additive_and_recreated_on_reopen(tmp_path, monkeypatch):
    path = tmp_path / "profiles.db"
    conn = _open(path, monkeypatch)
    saved = _saved_look()
    before = saved_looks.get_version(saved["key"], saved["version"])
    conn.execute("DROP TABLE saved_look_automation_profile")
    conn.commit()
    conn.close()

    reopened = db.connect(path)
    tables = {row["name"] for row in reopened.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'",
    )}
    assert "saved_look_automation_profile" in tables
    assert reopened.execute(
        "SELECT COUNT(*) FROM saved_look_automation_profile",
    ).fetchone()[0] == 0
    assert saved_looks.get_version(saved["key"], saved["version"]) == before


def test_archived_look_retains_exact_profile_but_refuses_new_profile_revisions(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    published = _publish(saved, _profile(saved))

    saved_looks.archive(saved["key"])

    assert saved_looks.get_automation_profile(
        saved["key"], saved["version"], published["profile_revision"],
        saved["content_digest"], published["profile_digest"],
    ) == published
    with pytest.raises(saved_looks.SavedLookError) as error:
        _publish(saved, _profile(saved, revision=2), expected_revision=1)
    assert error.value.status_code == 409
    assert error.value.code == "look_archived"
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_automation_profile WHERE look_key = ?",
        saved["key"],
    )["count"] == 1


def test_profile_revisions_are_immutable_digest_bound_and_exactly_readable(
    tmp_path, monkeypatch,
):
    conn = _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    first_input = _profile(saved)
    first = _publish(saved, first_input)
    assert first["profile_digest"] == resource_store.canonical_digest(first_input)
    stored_first = db.one(
        """SELECT * FROM saved_look_automation_profile
           WHERE look_key = ? AND look_version = ? AND profile_revision = 1""",
        saved["key"], saved["version"],
    )

    second_input = _profile(saved, revision=2)
    second_input["inventory_complete"]["value"] = False
    second = _publish(saved, second_input, expected_revision=1)
    assert second["profile_revision"] == 2
    assert saved_looks.get_automation_profile(
        saved["key"], saved["version"], 1, saved["content_digest"],
        first["profile_digest"],
    ) == first
    assert saved_looks.get_latest_automation_profile(saved["key"], saved["version"]) == second
    assert db.one(
        "SELECT profile_json FROM saved_look_automation_profile "
        "WHERE look_key = ? AND look_version = ? AND profile_revision = 1",
        saved["key"], saved["version"],
    )["profile_json"] == stored_first["profile_json"]

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE saved_look_automation_profile SET profile_json = '{}' "
            "WHERE look_key = ? AND look_version = ? AND profile_revision = 1",
            (saved["key"], saved["version"]),
        )


def test_corrupt_profile_storage_is_refused_on_exact_read(tmp_path, monkeypatch):
    conn = _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    published = _publish(saved, _profile(saved))
    corrupted = _profile(saved)
    corrupted["garment_coverage"][saved["outfit"]["garments"][0]["key"]][
        "worn"
    ]["regions"]["chest"] = "full"
    conn.execute("DROP TRIGGER saved_look_automation_profile_immutable")
    conn.execute(
        "UPDATE saved_look_automation_profile SET profile_json = ? "
        "WHERE look_key = ? AND look_version = ? AND profile_revision = 1",
        (
            json.dumps(corrupted, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            saved["key"], saved["version"],
        ),
    )
    conn.commit()
    conn.executescript(
        """CREATE TRIGGER saved_look_automation_profile_immutable
           BEFORE UPDATE ON saved_look_automation_profile
           BEGIN
               SELECT RAISE(ABORT, 'saved look automation profiles are immutable');
           END;""",
    )

    with pytest.raises(saved_looks.SavedLookError) as raised:
        saved_looks.get_automation_profile(
            saved["key"], saved["version"], 1, saved["content_digest"],
            published["profile_digest"],
        )
    assert raised.value.status_code == 500


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(unexpected=True),
    lambda p: p.update(schema_version=True),
    lambda p: p["look"].update(unexpected=True),
    lambda p: p["appearance_segments"][0].update(unexpected=True),
    lambda p: p["appearance_segments"][0].update(start=True),
    lambda p: p["appearance_segments"][0].update(text="fabricated"),
    lambda p: p["appearance_segments"][0]["provenance"].update(unexpected=True),
    lambda p: p["garment_coverage"].update(missing_garment={"worn": {}}),
    lambda p: p["garment_coverage"][next(iter(p["garment_coverage"]))]["worn"].update(
        extra_state={},
    ),
    lambda p: p["garment_coverage"][next(iter(p["garment_coverage"]))]["worn"][
        "regions"
    ].update(extra_region="unknown"),
    lambda p: p["garment_coverage"][next(iter(p["garment_coverage"]))]["worn"][
        "regions"
    ].update(chest="unsupported"),
    lambda p: p.update(baseline_coverage={"unexpected": True}),
    lambda p: p["inventory_complete"].update(value=1),
    lambda p: p["authored_provenance"].update(accepted_revision=True),
    lambda p: p["authored_provenance"].update(origin=[]),
    lambda p: p["garment_coverage"][next(iter(p["garment_coverage"]))]["worn"][
        "regions"
    ].update(chest=[]),
], ids=[
    "top-level-key", "strict-schema-integer", "look-key", "segment-key",
    "scalar-offset", "exact-source-text", "provenance-key", "unknown-garment",
    "state-key", "region-key", "region-value", "baseline-shape",
    "strict-inventory-boolean", "strict-accepted-revision", "strict-provenance-origin",
    "strict-region-type",
])
def test_closed_profile_rejects_unknown_keys_and_invalid_nested_values_without_writes(
    tmp_path, monkeypatch, mutate,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    candidate = copy.deepcopy(_profile(saved))
    mutate(candidate)
    with pytest.raises(saved_looks.SavedLookError) as raised:
        _publish(saved, candidate)
    assert raised.value.status_code == 422
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_automation_profile")["count"] == 0


def test_stale_look_digests_versions_and_portable_digest_write_nothing(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    portable_digest = saved_looks.portable_content_digest(
        saved_looks.export_version(saved["key"], saved["version"]),
    )
    for digest, version in (
        ("0" * 64, saved["version"]),
        (portable_digest, saved["version"]),
        (saved["content_digest"], saved["version"] + 1),
    ):
        with pytest.raises(saved_looks.SavedLookError):
            saved_looks.publish_automation_profile(
                saved["key"], version, digest, 0, _profile(saved),
            )
        assert db.one(
            "SELECT COUNT(*) AS count FROM saved_look_automation_profile",
        )["count"] == 0


@pytest.mark.parametrize("partition", ["gap", "overlap", "omission"])
def test_appearance_segments_must_partition_the_exact_source(
    tmp_path, monkeypatch, partition,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    profile = _profile(saved)
    appearance = saved["appearance"]
    split = max(1, len(appearance) // 2)
    if partition == "gap":
        profile["appearance_segments"] = [{
            **profile["appearance_segments"][0],
            "start": 1,
            "text": appearance[1:],
        }]
    elif partition == "overlap":
        profile["appearance_segments"] = [
            {"segment_id": "first", "start": 0, "end": split,
             "text": appearance[:split], "owner": "permanent",
             "provenance": _provenance(saved)},
            {"segment_id": "second", "start": split - 1, "end": len(appearance),
             "text": appearance[split - 1:], "owner": "permanent",
             "provenance": _provenance(saved)},
        ]
    else:
        profile["appearance_segments"] = [{
            **profile["appearance_segments"][0],
            "end": len(appearance) - 1,
            "text": appearance[:-1],
        }]
    with pytest.raises(saved_looks.SavedLookError):
        _publish(saved, profile)
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_automation_profile",
    )["count"] == 0


def test_appearance_spans_use_unicode_scalar_indices(tmp_path, monkeypatch):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    profile = _profile(saved)
    profile["appearance_segments"] = [
        {
            "segment_id": f"scalar-{index}",
            "start": index,
            "end": index + 1,
            "text": character,
            "owner": "permanent",
            "provenance": _provenance(saved),
        }
        for index, character in enumerate(saved["appearance"])
    ]

    published = _publish(saved, profile)
    segments = published["appearance_segments"]
    emoji_index = saved["appearance"].index("😀")
    combining_index = saved["appearance"].index("\u0301")
    assert segments[emoji_index]["text"] == "😀"
    assert segments[emoji_index]["end"] == emoji_index + 1
    assert segments[combining_index]["text"] == "\u0301"
    assert segments[combining_index]["end"] == combining_index + 1

    node = shutil.which("node")
    if not node:
        pytest.skip("Python/JavaScript scalar-span parity requires Node.js")
    script = """
import { scalarSpanFromUtf16Selection } from './frontend/src/appearanceOwnership.js';
const appearance = process.argv[1];
const spans = [];
let offset = 0;
for (const character of appearance) {
  const end = offset + character.length;
  spans.push(scalarSpanFromUtf16Selection(appearance, offset, end));
  offset = end;
}
process.stdout.write(JSON.stringify(spans));
"""
    js = subprocess.run(
        [node, "--input-type=module", "--eval", script, saved["appearance"]],
        capture_output=True, text=True, encoding="utf-8", check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert json.loads(js.stdout) == [
        {"start": segment["start"], "end": segment["end"], "text": segment["text"]}
        for segment in segments
    ]


def test_unpaired_surrogate_in_profile_source_is_rejected_without_writes(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    corrupted = {**saved, "appearance": "\ud800"}
    monkeypatch.setattr(saved_looks, "get_version", lambda *_: corrupted)

    with pytest.raises(saved_looks.SavedLookError) as raised:
        _publish(saved, _profile(saved))
    assert raised.value.status_code == 422
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_automation_profile",
    )["count"] == 0


def test_all_appearance_permanent_shortcut_projects_the_exact_source(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    published = _publish(saved, _profile(saved))
    projection = saved_looks.get_reviewed_appearance_projection(
        saved["key"], saved["version"], published["profile_revision"],
        saved["content_digest"], published["profile_digest"],
    )
    assert projection["appearance"] == saved["appearance"]
    assert projection["saved_look"]["appearance"] == saved["appearance"]


def test_explicit_no_coverage_assertion_is_preserved_for_only_its_selected_state(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    profile = _profile(saved)
    garment_key = saved["outfit"]["garments"][0]["key"]
    profile["garment_coverage"][garment_key]["worn"]["regions"] = {
        region: "none" for region in ("chest", "hips", "legs", "feet")
    }
    published = _publish(saved, profile)

    stored = saved_looks.get_automation_profile(
        saved["key"], saved["version"], published["profile_revision"],
        saved["content_digest"], published["profile_digest"],
    )
    assert stored["garment_coverage"][garment_key]["worn"]["regions"] == {
        region: "none" for region in ("chest", "hips", "legs", "feet")
    }
    assert stored["garment_coverage"][garment_key]["worn"]["provenance"] == _provenance(saved)
    assert stored["garment_coverage"][garment_key].get("aside") is None


def test_profile_readiness_blocks_exact_permanent_removed_garment_clause(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = saved_looks.create({
        "name": "Invented bracelet ownership fixture",
        "appearance": "A portrait includes a brass bracelet.",
        "garments": [{"wording": "a brass bracelet", "aside": ""}],
    })
    profile = _profile(saved)
    profile["appearance_segments"][0].update(
        end=len(saved["appearance"]),
        text=saved["appearance"],
        owner="permanent",
    )
    published = _publish(saved, profile)
    readiness = saved_looks.get_automation_profile_readiness(
        saved["key"], saved["version"], saved["content_digest"],
        published["profile_revision"], published["profile_digest"],
    )
    assert {
        "code": "appearance_progression_conflict",
        "appearance_segment_ids": ["appearance-1"],
        "garment_key": saved["outfit"]["garments"][0]["key"],
    } in readiness["diagnostics"]


def test_concurrent_profile_edits_have_one_cas_winner(tmp_path, monkeypatch):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    barrier = threading.Barrier(2)

    def publish(identity):
        profile = _profile(saved)
        profile["authored_provenance"]["source_identity"] = identity
        barrier.wait(timeout=5)
        try:
            return _publish(saved, profile)
        except saved_looks.SavedLookError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ("editor-a", "editor-b")))
    assert sum(isinstance(result, dict) for result in results) == 1
    failures = [result for result in results if isinstance(result, saved_looks.SavedLookError)]
    assert len(failures) == 1
    assert failures[0].status_code == 409
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_automation_profile",
    )["count"] == 1


def test_profiles_leave_look_snapshots_and_portable_v1_unchanged(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    snapshot = saved_looks.get_version(saved["key"], saved["version"])
    exported = saved_looks.export_version(saved["key"], saved["version"])
    portable_digest = saved_looks.portable_content_digest(exported)
    _publish(saved, _profile(saved))

    assert saved_looks.get_version(saved["key"], saved["version"]) == snapshot
    assert saved_looks.export_version(saved["key"], saved["version"]) == exported
    assert set(exported) == {"schema_version", "look", "garments", "provenance"}
    assert saved_looks.portable_content_digest(exported) == portable_digest
    assert saved_looks.canonicalize_portable_look(exported) == exported
    portable_snapshot = saved_looks.portable_look_to_snapshot(exported)
    assert portable_snapshot == {
        "look_id": snapshot["key"],
        "version": snapshot["version"],
        "content_digest": snapshot["content_digest"],
        "appearance": snapshot["appearance"],
        "outfit": snapshot["outfit"],
    }


def test_feature_flag_blocks_profile_writes_but_keeps_exact_reads(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    published = _publish(saved, _profile(saved))
    monkeypatch.setattr(resource_planning, "is_enabled", lambda: False)

    with pytest.raises(saved_looks.SavedLookError) as raised:
        _publish(saved, _profile(saved, revision=2), expected_revision=1)
    assert raised.value.status_code == 503
    assert saved_looks.get_automation_profile(
        saved["key"], saved["version"], 1, saved["content_digest"],
        published["profile_digest"],
    ) == published
    assert db.one(
        "SELECT COUNT(*) AS count FROM saved_look_automation_profile",
    )["count"] == 1
