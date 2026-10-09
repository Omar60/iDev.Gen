"""Readiness and explicit reuse of accepted saved-look profile facts."""
from __future__ import annotations

from copy import deepcopy

import pytest

import db
from backend import enhance, resource_planning, resource_store, saved_looks, session_plan


def _open(path, monkeypatch):
    monkeypatch.setattr(resource_planning, "is_enabled", lambda: True)
    return db.connect(path)


def _saved_look(*, appearance="A model in a studio."):
    return saved_looks.create({
        "name": "Readiness fixture",
        "appearance": appearance,
        "garments": [
            {"wording": "a cotton shirt", "aside": "the shirt lifted"},
            {"wording": "dark trousers", "aside": "the trousers shifted"},
        ],
    })


def _provenance(saved, revision=1, identity="profile-editor"):
    return {
        "origin": "user",
        "source_identity": identity,
        "source_digest": saved["content_digest"],
        "accepted_revision": revision,
    }


def _state(saved, revision=1, value="none"):
    return {
        "regions": {region: value for region in ("chest", "hips", "legs", "feet")},
        "provenance": _provenance(saved, revision),
    }


def _state_for_regions(saved, regions, revision=1):
    return {
        "regions": {
            region: regions.get(region, "none")
            for region in ("chest", "hips", "legs", "feet")
        },
        "provenance": _provenance(saved, revision),
    }


def _profile(saved, revision=1):
    appearance = saved["appearance"]
    garments = [] if saved["outfit"] is None else saved["outfit"]["garments"]
    coverage = {}
    for garment in garments:
        coverage[garment["key"]] = {"worn": _state(saved, revision)}
        if garment["aside"]:
            coverage[garment["key"]]["aside"] = _state(saved, revision)
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
        "baseline_coverage": _state(saved, revision),
        "inventory_complete": {
            "value": True,
            "provenance": _provenance(saved, revision),
        },
        "authored_provenance": _provenance(saved, revision),
    }


def _publish(saved, profile, expected_revision=0):
    return saved_looks.publish_automation_profile(
        saved["key"], saved["version"], saved["content_digest"],
        expected_revision, profile,
    )


def _readiness(saved, published):
    return saved_looks.get_automation_profile_readiness(
        saved["key"], saved["version"], saved["content_digest"],
        published["profile_revision"], published["profile_digest"],
    )


def _compile_coverage(saved, published, stage_index):
    return saved_looks.compile_automation_profile_coverage(
        saved["key"], saved["version"], saved["content_digest"],
        published["profile_revision"], published["profile_digest"], stage_index,
    )


def test_coverage_compiler_uses_layer_precedence_and_exact_v1_text(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    profile = _profile(saved)
    shirt, trousers = saved["outfit"]["garments"]
    profile["garment_coverage"][shirt["key"]]["worn"] = _state_for_regions(
        saved, {"chest": "partial", "hips": "partial"},
    )
    profile["garment_coverage"][trousers["key"]]["worn"] = _state_for_regions(
        saved, {"chest": "partial", "hips": "full", "legs": "partial"},
    )
    published = _publish(saved, profile)

    def unexpected(*_args, **_kwargs):
        pytest.fail("coverage compilation must not call an assistant")

    monkeypatch.setattr(enhance, "run", unexpected)
    monkeypatch.setattr(enhance, "run_structured", unexpected)
    coverage = _compile_coverage(saved, published, 0)

    assert saved_looks.AUTOMATION_COVERAGE_COMPILER_VERSION == "compiler-v1"
    assert coverage == (
        "The chest is partially covered. The hips are fully covered. "
        "The legs are partially covered. The feet are uncovered."
    )
    assert len(coverage) <= 2000
    assert not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in coverage)
    normalized = session_plan.validate_draft({
        "version": "resource-v1",
        "takes": [{"take_id": "take-001", "wardrobe_coverage": coverage}],
    })
    assert normalized["takes"][0]["wardrobe_coverage"] == coverage


def test_coverage_compiler_uses_authored_aside_and_baseline_stages(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = saved_looks.create({
        "name": "Single garment stages",
        "appearance": "A model in a studio.",
        "garments": [{"wording": "a coat", "aside": "the coat set aside"}],
    })
    profile = _profile(saved)
    garment = saved["outfit"]["garments"][0]
    profile["garment_coverage"][garment["key"]]["worn"] = _state_for_regions(
        saved, {"chest": "full"},
    )
    profile["garment_coverage"][garment["key"]]["aside"] = _state_for_regions(
        saved, {"chest": "partial"},
    )
    published = _publish(saved, profile)

    assert "The chest is fully covered." in _compile_coverage(saved, published, 0)
    assert "The chest is partially covered." in _compile_coverage(saved, published, 1)
    assert "The chest is uncovered." in _compile_coverage(saved, published, 2)


def test_coverage_compiler_tracks_each_multi_garment_stage_without_mutating_evidence(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    profile = _profile(saved)
    shirt, trousers = saved["outfit"]["garments"]
    profile["garment_coverage"][shirt["key"]]["worn"] = _state_for_regions(
        saved, {"chest": "full"},
    )
    profile["garment_coverage"][trousers["key"]]["worn"] = _state_for_regions(
        saved, {"chest": "partial", "hips": "full", "legs": "partial"},
    )
    profile["garment_coverage"][trousers["key"]]["aside"] = _state_for_regions(
        saved, {"chest": "partial", "legs": "partial"},
    )
    expected_input = deepcopy(profile)
    published = _publish(saved, profile)
    assert profile == expected_input

    look_before = saved_looks.get_version(saved["key"], saved["version"])
    profile_before = saved_looks.get_automation_profile(
        saved["key"], saved["version"], published["profile_revision"],
        saved["content_digest"], published["profile_digest"],
    )
    row_before = dict(db.one(
        """SELECT * FROM saved_look_automation_profile
           WHERE look_key = ? AND look_version = ? AND profile_revision = ?""",
        saved["key"], saved["version"], published["profile_revision"],
    ))

    assert [
        _compile_coverage(saved, published, stage_index)
        for stage_index in range(4)
    ] == [
        "The chest is fully covered. The hips are fully covered. "
        "The legs are partially covered. The feet are uncovered.",
        "The chest is partially covered. The hips are fully covered. "
        "The legs are partially covered. The feet are uncovered.",
        "The chest is partially covered. The hips are uncovered. "
        "The legs are partially covered. The feet are uncovered.",
        "The chest is uncovered. The hips are uncovered. "
        "The legs are uncovered. The feet are uncovered.",
    ]
    assert profile == expected_input
    assert saved_looks.get_version(saved["key"], saved["version"]) == look_before
    assert saved_looks.get_automation_profile(
        saved["key"], saved["version"], published["profile_revision"],
        saved["content_digest"], published["profile_digest"],
    ) == profile_before
    assert dict(db.one(
        """SELECT * FROM saved_look_automation_profile
           WHERE look_key = ? AND look_version = ? AND profile_revision = ?""",
        saved["key"], saved["version"], published["profile_revision"],
    )) == row_before


@pytest.mark.parametrize(
    ("missing_fact", "stage_index"),
    [("final_aside", 2), ("baseline", 3)],
)
def test_coverage_compiler_refuses_missing_stage_facts_without_writes(
    tmp_path, monkeypatch, missing_fact, stage_index,
):
    _open(tmp_path / f"missing-{missing_fact}.db", monkeypatch)
    saved = _saved_look()
    profile = _profile(saved)
    if missing_fact == "final_aside":
        final_key = saved["outfit"]["garments"][-1]["key"]
        profile["garment_coverage"][final_key].pop("aside")
    else:
        profile["baseline_coverage"] = None
    expected_input = deepcopy(profile)
    published = _publish(saved, profile)
    row_before = dict(db.one(
        """SELECT * FROM saved_look_automation_profile
           WHERE look_key = ? AND look_version = ? AND profile_revision = ?""",
        saved["key"], saved["version"], published["profile_revision"],
    ))

    with pytest.raises(saved_looks.SavedLookError) as raised:
        _compile_coverage(saved, published, stage_index)

    assert raised.value.code == "coverage_unknown"
    assert profile == expected_input
    assert dict(db.one(
        """SELECT * FROM saved_look_automation_profile
           WHERE look_key = ? AND look_version = ? AND profile_revision = ?""",
        saved["key"], saved["version"], published["profile_revision"],
    )) == row_before


def test_coverage_compiler_refuses_a_fact_that_is_not_accepted(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    published = _publish(saved, _profile(saved))

    monkeypatch.setattr(
        saved_looks, "_profile_fact_provenance_accepted", lambda *_args, **_kwargs: False,
    )
    with pytest.raises(saved_looks.SavedLookError) as raised:
        _compile_coverage(saved, published, 0)

    assert raised.value.code == "automation_profile_not_ready"


def test_coverage_compiler_uses_appearance_only_baseline(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = saved_looks.create({
        "name": "Appearance-only baseline",
        "appearance": "A model in a studio.",
    })
    profile = _profile(saved)
    profile["baseline_coverage"] = _state_for_regions(
        saved, {"chest": "partial", "hips": "full"},
    )
    published = _publish(saved, profile)

    assert _compile_coverage(saved, published, 0) == (
        "The chest is partially covered. The hips are fully covered. "
        "The legs are uncovered. The feet are uncovered."
    )


def test_coverage_compiler_does_not_infer_body_coverage_from_accessories(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = saved_looks.create({
        "name": "Accessory stages",
        "appearance": "A model in a studio.",
        "garments": [{"wording": "a beaded bracelet", "aside": "the bracelet removed"}],
    })
    published = _publish(saved, _profile(saved))

    coverage = _compile_coverage(saved, published, 0)
    assert coverage == (
        "The chest is uncovered. The hips are uncovered. "
        "The legs are uncovered. The feet are uncovered."
    )
    assert "bracelet" not in coverage


def test_coverage_compiler_refuses_unknown_even_when_a_layer_is_full(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    profile = _profile(saved)
    shirt, trousers = saved["outfit"]["garments"]
    profile["garment_coverage"][shirt["key"]]["worn"] = _state_for_regions(
        saved, {"chest": "full"},
    )
    profile["garment_coverage"][trousers["key"]]["worn"] = _state_for_regions(
        saved, {"chest": "unknown"},
    )
    published = _publish(saved, profile)

    with pytest.raises(saved_looks.SavedLookError) as raised:
        _compile_coverage(saved, published, 0)
    assert raised.value.code == "coverage_unknown"
    assert saved["key"] not in raised.value.message


@pytest.mark.parametrize(
    ("inventory", "error_code"),
    [(None, "inventory_unconfirmed"), (False, "inventory_incomplete")],
)
def test_coverage_compiler_requires_confirmed_complete_inventory(
    tmp_path, monkeypatch, inventory, error_code,
):
    _open(tmp_path / f"profiles-{inventory}.db", monkeypatch)
    saved = _saved_look()
    profile = _profile(saved)
    profile["inventory_complete"]["value"] = inventory
    published = _publish(saved, profile)

    with pytest.raises(saved_looks.SavedLookError) as raised:
        _compile_coverage(saved, published, 0)
    assert raised.value.code == error_code


def test_coverage_compiler_requires_exact_profile_and_materialized_stage(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    published = _publish(saved, _profile(saved))

    with pytest.raises(saved_looks.SavedLookError) as raised:
        saved_looks.compile_automation_profile_coverage(
            saved["key"], saved["version"], saved["content_digest"],
            published["profile_revision"], "0" * 64, 0,
        )
    assert raised.value.code == "automation_profile_stale"

    with pytest.raises(saved_looks.SavedLookError) as raised:
        _compile_coverage(saved, published, True)
    assert raised.value.code == "coverage_stage_invalid"


def test_missing_profile_never_falls_back_to_latest_and_manual_stays_available(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()

    missing = saved_looks.get_automation_profile_readiness(
        saved["key"], saved["version"], saved["content_digest"],
    )
    assert missing == {
        "profile_ready": False,
        "manual_available": True,
        "diagnostics": [{"code": "automation_profile_missing"}],
    }

    published = _publish(saved, _profile(saved))
    unselected = saved_looks.get_automation_profile_readiness(
        saved["key"], saved["version"], saved["content_digest"],
    )
    assert unselected["profile_ready"] is False
    assert unselected["manual_available"] is True
    assert unselected["diagnostics"] == [{"code": "automation_profile_identity_required"}]
    assert _readiness(saved, published)["profile_ready"] is True


def test_readiness_preserves_exact_stale_and_missing_profile_refusals(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    published = _publish(saved, _profile(saved))

    with pytest.raises(saved_looks.SavedLookError) as raised:
        saved_looks.get_automation_profile_readiness(
            saved["key"], saved["version"], "0" * 64,
            published["profile_revision"], published["profile_digest"],
        )
    assert raised.value.status_code == 409
    assert raised.value.code == "automation_profile_stale"

    # A requested absent revision cannot silently resolve to the stored revision.
    with pytest.raises(saved_looks.SavedLookError) as raised:
        saved_looks.get_automation_profile_readiness(
            saved["key"], saved["version"], saved["content_digest"],
            published["profile_revision"] + 1, published["profile_digest"],
        )
    assert raised.value.status_code == 404
    assert raised.value.code == "automation_profile_not_found"

    with pytest.raises(saved_looks.SavedLookError) as raised:
        saved_looks.get_automation_profile_readiness(
            saved["key"], saved["version"], saved["content_digest"],
            published["profile_revision"], "0" * 64,
        )
    assert raised.value.status_code == 409
    assert raised.value.code == "automation_profile_stale"


def test_readiness_requires_known_accepted_worn_final_aside_baseline_and_inventory(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    profile = _profile(saved)
    first = saved["outfit"]["garments"][0]

    # The non-final aside is not a selected stage; its missing facts do not block.
    profile["garment_coverage"][first["key"]].pop("aside")
    complete = _publish(saved, profile)
    assert _readiness(saved, complete)["profile_ready"] is True

    incomplete_saved = _saved_look(appearance="A second model in a studio.")
    incomplete = _profile(incomplete_saved)
    incomplete["garment_coverage"].pop(incomplete_saved["outfit"]["garments"][0]["key"])
    incomplete["garment_coverage"][incomplete_saved["outfit"]["garments"][-1]["key"]].pop("aside")
    incomplete["baseline_coverage"]["regions"]["feet"] = "unknown"
    incomplete["baseline_coverage"]["regions"]["chest"] = "full"
    incomplete["inventory_complete"]["value"] = None
    published = _publish(incomplete_saved, incomplete)
    result = _readiness(incomplete_saved, published)

    assert result["profile_ready"] is False
    assert result["manual_available"] is True
    assert {item["code"] for item in result["diagnostics"]} >= {
        "coverage_state_missing", "coverage_unknown",
        "inventory_unconfirmed", "baseline_progression_conflict",
    }
    assert any(
        item.get("garment_key") == incomplete_saved["outfit"]["garments"][-1]["key"]
        and item.get("state") == "aside"
        for item in result["diagnostics"]
    )

    incomplete_again = _profile(incomplete_saved, revision=2)
    incomplete_again["inventory_complete"]["value"] = False
    published_again = _publish(incomplete_saved, incomplete_again, expected_revision=1)
    assert "inventory_incomplete" in {
        item["code"] for item in _readiness(incomplete_saved, published_again)["diagnostics"]
    }


@pytest.mark.parametrize(
    ("baseline", "ready", "expected_code"),
    [
        (None, False, "baseline_missing"),
        ("unknown", False, "coverage_unknown"),
        ("none", True, None),
        ("partial", True, None),
        ("full", True, None),
    ],
)
def test_appearance_only_baseline_none_is_distinct_from_missing_or_unknown(
    tmp_path, monkeypatch, baseline, ready, expected_code,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = saved_looks.create({"name": "Appearance only", "appearance": "A portrait subject."})
    profile = _profile(saved)
    profile["baseline_coverage"] = None if baseline is None else _state(
        saved, value=baseline,
    )
    published = _publish(saved, profile)

    result = _readiness(saved, published)
    assert result["profile_ready"] is ready
    assert result["manual_available"] is True
    if expected_code:
        assert expected_code in {item["code"] for item in result["diagnostics"]}
    else:
        assert result["diagnostics"] == []


def test_invalid_garment_and_arbitrary_reviewed_label_cannot_become_ready(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    invalid_key = _profile(saved)
    invalid_key["garment_coverage"]["missing-garment"] = {
        "worn": _state(saved),
    }
    with pytest.raises(saved_looks.SavedLookError) as raised:
        _publish(saved, invalid_key)
    assert raised.value.status_code == 422

    wrong_user_binding = _profile(saved)
    wrong_user_binding["baseline_coverage"]["provenance"]["source_digest"] = "b" * 64
    with pytest.raises(saved_looks.SavedLookError) as raised:
        _publish(saved, wrong_user_binding)
    assert raised.value.status_code == 422

    fake_source = _profile(saved)
    fake_source["garment_coverage"][saved["outfit"]["garments"][0]["key"]][
        "worn"
    ]["provenance"] = {
        "origin": "reviewed_structured",
        "source_identity": "assistant-proposal",
        "source_digest": "a" * 64,
        "accepted_revision": 1,
    }
    with pytest.raises(saved_looks.SavedLookError) as raised:
        _publish(saved, fake_source)
    assert raised.value.status_code == 422
    assert db.one("SELECT COUNT(*) AS count FROM saved_look_automation_profile")["count"] == 0


def test_reviewed_copy_resolves_exact_source_and_invalidates_changed_states_and_spans(
    tmp_path, monkeypatch,
):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    source_profile = _publish(saved, _profile(saved))
    garments = saved["outfit"]["garments"]
    selected = [
        {"garment_key": garments[0]["key"], "state": "worn"},
        {"garment_key": garments[1]["key"], "state": "worn"},
        {"garment_key": garments[1]["key"], "state": "aside"},
    ]

    same_facts = saved_looks.create_version(
        saved["key"], 1, {"name": "Renamed look", "appearance": saved["appearance"]},
    )
    copied = saved_looks.copy_reviewed_automation_profile_facts(
        same_facts["key"], same_facts["version"], source_profile["profile_digest"],
        1, selected, copy_baseline=True,
    )
    assert set(copied["garment_coverage"]) == {garments[0]["key"], garments[1]["key"]}
    assert copied["baseline_coverage"]["regions"] == source_profile["baseline_coverage"]["regions"]
    assert copied["garment_coverage"][garments[0]["key"]]["worn"]["provenance"] == {
        "origin": "reviewed_structured",
        "source_identity": f"automation-profile:{source_profile['profile_digest']}",
        "source_digest": source_profile["profile_digest"],
        "accepted_revision": 1,
    }
    assert "appearance_segments" not in copied

    candidate = _profile(same_facts)
    candidate["garment_coverage"] = copied["garment_coverage"]
    candidate["baseline_coverage"] = copied["baseline_coverage"]
    reviewed = _publish(same_facts, candidate)
    assert _readiness(same_facts, reviewed)["profile_ready"] is True

    # Keep the stable key and aside wording while changing the canonical wording.
    changed_wording = deepcopy(same_facts)
    changed_wording["outfit"]["garments"][0]["wording"] = "a silk shirt"
    changed_wording["content_digest"] = resource_store.canonical_digest({
        "appearance": changed_wording["appearance"],
        "outfit": changed_wording["outfit"],
    })
    original_get_version = saved_looks.get_version
    monkeypatch.setattr(
        saved_looks,
        "get_version",
        lambda key, version: (
            changed_wording
            if (key, version) == (same_facts["key"], same_facts["version"])
            else original_get_version(key, version)
        ),
    )
    changed_canonical = saved_looks.copy_reviewed_automation_profile_facts(
        same_facts["key"], same_facts["version"], source_profile["profile_digest"],
        2, [{"garment_key": garments[0]["key"], "state": "aside"}],
    )
    assert changed_canonical["garment_coverage"] == {}
    assert changed_canonical["skipped"] == [
        {"garment_key": garments[0]["key"], "state": "aside", "code": "state_changed"},
    ]
    monkeypatch.setattr(saved_looks, "get_version", original_get_version)

    changed_appearance = saved_looks.create_version(
        same_facts["key"], 2,
        {"name": "Changed appearance", "appearance": "A visibly different subject."},
    )
    changed = saved_looks.copy_reviewed_automation_profile_facts(
        changed_appearance["key"], changed_appearance["version"],
        source_profile["profile_digest"], 1, selected, copy_baseline=True,
    )
    assert changed["garment_coverage"]
    assert changed["baseline_coverage"] is None
    assert {item["code"] for item in changed["skipped"]} == {"state_changed"}
    assert "appearance_segments" not in changed

    changed_garments = [
        {"key": item["key"], "wording": item["wording"], "aside": item["aside"]}
        for item in changed_appearance["outfit"]["garments"]
    ]
    changed_garments[-1]["aside"] = "the trousers moved aside"
    changed_state = saved_looks.create_version(
        changed_appearance["key"], 3,
        {"name": "Changed aside state", "appearance": changed_appearance["appearance"],
         "garments": changed_garments},
    )
    changed_key = changed_state["outfit"]["garments"][-1]["key"]
    invalidated = saved_looks.copy_reviewed_automation_profile_facts(
        changed_state["key"], changed_state["version"],
        source_profile["profile_digest"], 1,
        [{"garment_key": changed_key, "state": "aside"}],
    )
    assert invalidated["garment_coverage"] == {}
    assert invalidated["skipped"] == [
        {"garment_key": changed_key, "state": "aside", "code": "state_changed"},
    ]


def test_reviewed_copy_never_promotes_unknown_region_facts(tmp_path, monkeypatch):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    source = _profile(saved)
    key = saved["outfit"]["garments"][0]["key"]
    source["garment_coverage"][key]["worn"]["regions"]["chest"] = "unknown"
    published = _publish(saved, source)
    target = saved_looks.create_version(
        saved["key"], 1, {"name": "Same facts, next version", "appearance": saved["appearance"]},
    )

    copied = saved_looks.copy_reviewed_automation_profile_facts(
        target["key"], target["version"], published["profile_digest"], 1,
        [{"garment_key": key, "state": "worn"}],
    )
    assert copied["garment_coverage"][key]["worn"]["regions"]["chest"] == "unknown"

    candidate = _profile(target)
    candidate["garment_coverage"][key]["worn"] = copied["garment_coverage"][key]["worn"]
    reviewed = _publish(target, candidate)
    result = _readiness(target, reviewed)
    assert result["profile_ready"] is False
    assert any(
        item.get("garment_key") == key
        and item.get("state") == "worn"
        and item.get("region") == "chest"
        and item["code"] == "coverage_unknown"
        for item in result["diagnostics"]
    )


def test_reviewed_source_chains_are_accepted_but_bounded(tmp_path, monkeypatch):
    _open(tmp_path / "profiles.db", monkeypatch)
    saved = _saved_look()
    first = _publish(saved, _profile(saved))
    garment_key = saved["outfit"]["garments"][0]["key"]

    second_copy = saved_looks.copy_reviewed_automation_profile_facts(
        saved["key"], saved["version"], first["profile_digest"], 2,
        [{"garment_key": garment_key, "state": "worn"}],
    )
    second_profile = _profile(saved, revision=2)
    second_profile["garment_coverage"][garment_key]["worn"] = (
        second_copy["garment_coverage"][garment_key]["worn"]
    )
    second = _publish(saved, second_profile, expected_revision=1)

    third_copy = saved_looks.copy_reviewed_automation_profile_facts(
        saved["key"], saved["version"], second["profile_digest"], 3,
        [{"garment_key": garment_key, "state": "worn"}],
    )
    third_profile = _profile(saved, revision=3)
    third_profile["garment_coverage"][garment_key]["worn"] = (
        third_copy["garment_coverage"][garment_key]["worn"]
    )
    third = _publish(saved, third_profile, expected_revision=2)
    assert _readiness(saved, third)["profile_ready"] is True

    monkeypatch.setattr(saved_looks, "_AUTOMATION_PROFILE_SOURCE_MAX_DEPTH", 1)
    bounded = _readiness(saved, third)
    assert bounded["profile_ready"] is False
    assert any(
        item["code"] == "coverage_provenance_unaccepted"
        and item.get("garment_key") == garment_key
        and item.get("state") == "worn"
        for item in bounded["diagnostics"]
    )
