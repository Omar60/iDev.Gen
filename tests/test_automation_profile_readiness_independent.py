"""Independent adversarial acceptance checks for saved-look readiness and reuse."""
from __future__ import annotations

import pytest

import db
from backend import resource_planning, saved_looks, session_plan


def _open(path, monkeypatch):
    monkeypatch.setattr(resource_planning, "is_enabled", lambda: True)
    return db.connect(path)


def _saved_look(*, appearance="A model in a studio.", garments=True):
    payload = {"name": "Independent readiness fixture", "appearance": appearance}
    if garments:
        payload["garments"] = [
            {"wording": "a cotton shirt", "aside": "the shirt lifted"},
            {"wording": "dark trousers", "aside": "the trousers shifted"},
        ]
    return saved_looks.create(payload)


def _provenance(saved, revision=1):
    return {
        "origin": "user",
        "source_identity": "independent-profile-editor",
        "source_digest": saved["content_digest"],
        "accepted_revision": revision,
    }


def _state(saved, revision=1, values=None):
    values = values or {region: "none" for region in ("chest", "hips", "legs", "feet")}
    return {"regions": values, "provenance": _provenance(saved, revision)}


def _profile(saved, *, baseline="none", revision=1):
    appearance = saved["appearance"]
    garments = [] if saved["outfit"] is None else saved["outfit"]["garments"]
    coverage = {}
    for garment in garments:
        coverage[garment["key"]] = {"worn": _state(saved, revision)}
        if garment["aside"]:
            coverage[garment["key"]]["aside"] = _state(saved, revision)
    baseline_fact = None if baseline is None else _state(
        saved,
        revision,
        {"chest": baseline, "hips": "none", "legs": "none", "feet": "none"},
    )
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
        "garment_coverage": coverage,
        "baseline_coverage": baseline_fact,
        "inventory_complete": {
            "value": True,
            "provenance": _provenance(saved, revision),
        },
        "authored_provenance": _provenance(saved, revision),
    }


def _publish(saved, profile):
    return saved_looks.publish_automation_profile(
        saved["key"], saved["version"], saved["content_digest"], 0, profile,
    )


def _readiness(saved, profile):
    return saved_looks.get_automation_profile_readiness(
        saved["key"], saved["version"], saved["content_digest"],
        profile["profile_revision"], profile["profile_digest"],
    )


@pytest.mark.parametrize("baseline", ["full", "partial"])
def test_garment_free_final_stage_rejects_covering_baseline(
    tmp_path, monkeypatch, baseline,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    published = _publish(saved, _profile(saved, baseline=baseline))

    result = _readiness(saved, published)

    assert result["profile_ready"] is False
    assert result["manual_available"] is True
    assert any(
        item.get("field") == "baseline_coverage"
        and item.get("region") == "chest"
        and "conflict" in item["code"]
        for item in result["diagnostics"]
    )


@pytest.mark.parametrize("baseline", ["full", "partial"])
def test_appearance_only_look_can_have_known_covering_baseline(
    tmp_path, monkeypatch, baseline,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look(garments=False)
    published = _publish(saved, _profile(saved, baseline=baseline))

    result = _readiness(saved, published)

    assert result == {
        "profile_ready": True,
        "manual_available": True,
        "diagnostics": [],
    }


def test_garment_free_final_stage_conflicts_even_without_aside_wording(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = saved_looks.create({
        "name": "No garment-free stage",
        "appearance": "A model in a studio.",
        "garments": [{"wording": "a cotton shirt", "aside": ""}],
    })
    assert session_plan.derive_saved_look_wardrobe_progression(
        saved["outfit"], "", 2, stage_indices=[0, 1], interval_start=0, interval_end=1,
    ) == ["She wears a cotton shirt.", "She wears nothing at all."]
    published = _publish(saved, _profile(saved, baseline="full"))
    profiles_before = db.q("SELECT * FROM saved_look_automation_profile")
    versions_before = db.q("SELECT * FROM saved_look_version ORDER BY look_key, version")

    result = _readiness(saved, published)

    assert result["profile_ready"] is False
    assert result["manual_available"] is True
    assert any(
        item.get("field") == "baseline_coverage"
        and item.get("region") == "chest"
        and item["code"] == "baseline_progression_conflict"
        for item in result["diagnostics"]
    )
    assert db.q("SELECT * FROM saved_look_automation_profile") == profiles_before
    assert db.q("SELECT * FROM saved_look_version ORDER BY look_key, version") == versions_before


def test_aside_fact_copy_requires_unchanged_canonical_and_aside_wording(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    source_profile = _publish(saved, _profile(saved))
    final_garment = saved["outfit"]["garments"][-1]
    revised_garments = [
        {
            "key": garment["key"],
            "wording": "blue denim jeans" if garment["key"] == final_garment["key"]
            else garment["wording"],
            # The aside phrase stays identical while canonical garment wording changes.
            "aside": garment["aside"],
        }
        for garment in saved["outfit"]["garments"]
    ]
    target = saved_looks.create_version(
        saved["key"], saved["version"],
        {"name": "Changed canonical wording", "appearance": saved["appearance"],
         "garments": revised_garments},
    )
    profiles_before = db.q(
        "SELECT * FROM saved_look_automation_profile ORDER BY look_key, look_version, profile_revision",
    )
    versions_before = db.q(
        "SELECT look_key, version, snapshot_json FROM saved_look_version ORDER BY look_key, version",
    )

    copied = saved_looks.copy_reviewed_automation_profile_facts(
        target["key"], target["version"], source_profile["profile_digest"], 1,
        [{"garment_key": final_garment["key"], "state": "aside"}],
    )

    assert copied["garment_coverage"] == {}
    assert copied["skipped"] == [{
        "garment_key": final_garment["key"],
        "state": "aside",
        "code": "state_changed",
    }]
    assert db.q(
        "SELECT * FROM saved_look_automation_profile ORDER BY look_key, look_version, profile_revision",
    ) == profiles_before
    assert db.q(
        "SELECT look_key, version, snapshot_json FROM saved_look_version ORDER BY look_key, version",
    ) == versions_before


def test_corrupt_reviewed_source_fails_readiness_closed_without_writes(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    source = _publish(saved, _profile(saved))
    garment_key = saved["outfit"]["garments"][0]["key"]
    copied = saved_looks.copy_reviewed_automation_profile_facts(
        saved["key"], saved["version"], source["profile_digest"], 2,
        [{"garment_key": garment_key, "state": "worn"}],
    )
    target_profile = _profile(saved, revision=2)
    target_profile["garment_coverage"][garment_key]["worn"] = (
        copied["garment_coverage"][garment_key]["worn"]
    )
    target = saved_looks.publish_automation_profile(
        saved["key"], saved["version"], saved["content_digest"], 1, target_profile,
    )

    db.run("DROP TRIGGER saved_look_automation_profile_immutable")
    db.run(
        "UPDATE saved_look_automation_profile SET profile_json = '{}' "
        "WHERE profile_digest = ?",
        source["profile_digest"],
    )
    rows_before = db.q(
        "SELECT * FROM saved_look_automation_profile ORDER BY look_key, look_version, profile_revision",
    )

    result = _readiness(saved, target)

    assert result["profile_ready"] is False
    assert result["manual_available"] is True
    assert "coverage_provenance_unaccepted" in {
        item["code"] for item in result["diagnostics"]
    }
    assert db.q(
        "SELECT * FROM saved_look_automation_profile ORDER BY look_key, look_version, profile_revision",
    ) == rows_before
