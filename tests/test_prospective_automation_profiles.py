import copy

import pytest

import db
from backend import saved_looks


def _regions(value="unknown"):
    return {region: value for region in ("chest", "hips", "legs", "feet")}


def _draft(appearance, garments, *, owner="permanent", states=None):
    source = saved_looks.canonicalize_prospective_automation_source(appearance, garments)
    coverage = [
        {
            "garment_index": entry["garment_index"],
            "states": {
                state: {"regions": regions}
                for state, regions in entry["states"].items()
            },
        }
        for entry in (states or [])
    ]
    return {
        "schema_version": 1,
        "draft_content_digest": source["draft_content_digest"],
        "appearance_segments": ([{
            "segment_id": "appearance-1",
            "start": 0,
            "end": len(appearance),
            "text": appearance,
            "owner": owner,
        }] if appearance else []),
        "garment_coverage": coverage,
        "baseline_coverage": None,
        "inventory_complete": None,
    }


def test_prospective_digest_and_mapping_resolve_new_reused_and_ordered_states(client):
    existing = saved_looks.create({
        "name": "Existing source",
        "appearance": "A portrait.",
        "garments": [{"wording": "a blue cardigan", "aside": ""}],
    })
    reused_key = existing["outfit"]["garments"][0]["key"]
    appearance = "A blue cardigan over a gold chain."
    garments = [
        {"key": reused_key, "wording": "a blue cardigan", "aside": ""},
        {"key": None, "wording": "a gold chain", "aside": "the chain is unclasped"},
    ]
    draft = _draft(
        appearance,
        garments,
        owner={"garment_index": 1},
        states=[
            {"garment_index": 1, "states": {"worn": _regions("partial"), "aside": _regions("none")}},
            {"garment_index": 0, "states": {"worn": _regions("full")}},
        ],
    )
    canonical = saved_looks.canonicalize_prospective_automation_profile(
        draft, appearance, garments,
    )
    saved = saved_looks.create({
        "name": "Reviewed source",
        "appearance": appearance,
        "garments": garments,
    })

    mapped = saved_looks.map_prospective_automation_profile(
        canonical, appearance, garments, saved,
    )
    final_keys = [item["key"] for item in saved["outfit"]["garments"]]
    assert final_keys[0] == reused_key
    assert final_keys[1] != reused_key
    assert mapped["appearance_segments"][0]["owner"] == f"garment:{final_keys[1]}"
    assert list(mapped["garment_coverage"]) == [final_keys[0], final_keys[1]]
    assert mapped["garment_coverage"][final_keys[1]]["aside"]["regions"] == _regions("none")
    assert mapped["baseline_coverage"] is None
    assert mapped["inventory_complete"] is None
    assert mapped["draft_content_digest"] == canonical["draft_content_digest"]
    assert mapped["look"]["content_digest"] == saved["content_digest"]
    assert mapped["draft_content_digest"] != mapped["look"]["content_digest"]

    reordered = [garments[1], garments[0]]
    assert saved_looks.canonicalize_prospective_automation_source(
        appearance, reordered,
    )["draft_content_digest"] != canonical["draft_content_digest"]


def test_changed_reused_garment_definition_maps_to_its_new_snapshot_key(client):
    original = saved_looks.create({
        "name": "Old wording",
        "appearance": "A shirt.",
        "garments": [{"wording": "a shirt", "aside": ""}],
    })
    old_key = original["outfit"]["garments"][0]["key"]
    garments = [{"key": old_key, "wording": "a red silk shirt", "aside": ""}]
    appearance = "A red silk shirt."
    draft = _draft(appearance, garments, owner={"garment_index": 0})
    updated = saved_looks.create({
        "name": "New wording",
        "appearance": appearance,
        "garments": garments,
    })

    mapped = saved_looks.map_prospective_automation_profile(
        draft, appearance, garments, updated,
    )
    new_key = updated["outfit"]["garments"][0]["key"]
    assert new_key != old_key
    assert mapped["appearance_segments"][0]["owner"] == f"garment:{new_key}"


def test_mapping_uses_immutable_target_history_when_live_garment_row_drifts(client):
    appearance = "A saved blue coat."
    first = saved_looks.create({
        "name": "Original coat",
        "appearance": appearance,
        "garments": [{"wording": "a blue coat", "aside": ""}],
    })
    garment = first["outfit"]["garments"][0]
    garments = [{
        "key": garment["key"],
        "wording": garment["wording"],
        "aside": garment["aside"],
    }]
    profile = _draft(appearance, garments)
    second = saved_looks.create_version(first["key"], 1, {
        "name": "Reviewed coat",
        "appearance": appearance,
        "garments": copy.deepcopy(garments),
    })
    db.run("UPDATE garment SET wording = ? WHERE key = ?", "mutated live wording", garment["key"])

    mapped = saved_looks.map_prospective_automation_profile(
        profile, appearance, garments, second,
    )

    assert mapped["garment_coverage"] == {}
    assert mapped["look"]["content_digest"] == second["content_digest"]
    assert mapped["draft_content_digest"] == profile["draft_content_digest"]


@pytest.mark.parametrize("tamper", ["wrong_reused_key", "duplicate_final_key", "bad_digest"])
def test_mapping_rejects_forged_or_non_unique_final_snapshots(client, tamper):
    appearance = "Two identical coats."
    garments = [
        {"key": None, "wording": "a blue coat", "aside": ""},
        {"key": None, "wording": "a blue coat", "aside": ""},
    ]
    draft = _draft(appearance, garments)
    saved = saved_looks.create({
        "name": "Two coats",
        "appearance": appearance,
        "garments": garments,
    })
    corrupted = copy.deepcopy(saved)
    if tamper == "wrong_reused_key":
        original = saved_looks.create({
            "name": "Known coat",
            "appearance": "A coat.",
            "garments": [{"wording": "a coat", "aside": ""}],
        })["outfit"]["garments"][0]["key"]
        changed_source = [{"key": original, "wording": "a coat", "aside": ""}]
        changed_saved = saved_looks.create({
            "name": "Known coat unchanged",
            "appearance": "A coat.",
            "garments": changed_source,
        })
        changed_draft = _draft("A coat.", changed_source)
        corrupted = copy.deepcopy(changed_saved)
        corrupted["outfit"]["garments"][0]["key"] = "forged-reuse-key"
        corrupted["content_digest"] = saved_looks._portable_digest({
            "appearance": corrupted["appearance"], "outfit": corrupted["outfit"],
        })
        appearance, garments, draft = "A coat.", changed_source, changed_draft
    elif tamper == "duplicate_final_key":
        first_key = corrupted["outfit"]["garments"][0]["key"]
        corrupted["outfit"]["garments"][1]["key"] = first_key
        corrupted["content_digest"] = saved_looks._portable_digest({
            "appearance": corrupted["appearance"], "outfit": corrupted["outfit"],
        })
    else:
        corrupted["content_digest"] = "0" * 64

    with pytest.raises(saved_looks.SavedLookError):
        saved_looks.map_prospective_automation_profile(draft, appearance, garments, corrupted)


def test_prospective_profile_uses_unicode_scalar_offsets_without_normalizing(client):
    appearance = "A 👗e\u0301."
    garments = [{"key": None, "wording": "a dress", "aside": ""}]
    source = saved_looks.canonicalize_prospective_automation_source(appearance, garments)
    profile = {
        "schema_version": 1,
        "draft_content_digest": source["draft_content_digest"],
        "appearance_segments": [
            {"segment_id": "s1", "start": 0, "end": 2, "text": "A ", "owner": "permanent"},
            {"segment_id": "s2", "start": 2, "end": 3, "text": "👗", "owner": {"garment_index": 0}},
            {"segment_id": "s3", "start": 3, "end": 6, "text": "e\u0301.", "owner": None},
        ],
        "garment_coverage": [],
        "baseline_coverage": None,
        "inventory_complete": None,
    }

    canonical = saved_looks.canonicalize_prospective_automation_profile(
        profile, appearance, garments,
    )
    assert [item["text"] for item in canonical["appearance_segments"]] == ["A ", "👗", "e\u0301."]
    assert canonical["appearance_segments"][2]["owner"] is None
    assert "e\u0301" != "é"


@pytest.mark.parametrize("bad_profile", [
    lambda p: p["garment_coverage"].extend([
        {"garment_index": 0, "states": {"worn": _regions()}},
        {"garment_index": 0, "states": {"worn": _regions()}},
    ]),
    lambda p: p["garment_coverage"].append(
        {"garment_index": 2, "states": {"worn": _regions()}},
    ),
    lambda p: p["garment_coverage"].append(
        {"garment_index": True, "states": {"worn": _regions()}},
    ),
    lambda p: p["appearance_segments"][0].update(owner={"garment_index": 1}),
    lambda p: p["appearance_segments"][0].update(owner={"garment_index": True}),
])
def test_invalid_indices_are_rejected_without_creating_looks_or_profiles(client, bad_profile):
    appearance = "A jacket."
    garments = [{"key": None, "wording": "a jacket", "aside": ""}]
    profile = _draft(appearance, garments, owner={"garment_index": 0})
    bad_profile(profile)
    before = (
        db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"],
        db.one("SELECT COUNT(*) AS n FROM saved_look_automation_profile")["n"],
        db.one("SELECT COUNT(*) AS n FROM garment")["n"],
        db.one("SELECT COUNT(*) AS n FROM outfit")["n"],
    )

    with pytest.raises(saved_looks.SavedLookError) as raised:
        saved_looks.canonicalize_prospective_automation_profile(profile, appearance, garments)
    assert raised.value.code == "invalid_look"
    assert (
        db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"],
        db.one("SELECT COUNT(*) AS n FROM saved_look_automation_profile")["n"],
        db.one("SELECT COUNT(*) AS n FROM garment")["n"],
        db.one("SELECT COUNT(*) AS n FROM outfit")["n"],
    ) == before


def test_draft_digest_rejects_order_and_definition_changes(client):
    appearance = "A jacket and trousers."
    garments = [
        {"key": None, "wording": "a jacket", "aside": ""},
        {"key": None, "wording": "trousers", "aside": ""},
    ]
    profile = _draft(appearance, garments, owner={"garment_index": 0})
    changed = [garments[1], garments[0]]
    with pytest.raises(saved_looks.SavedLookError):
        saved_looks.canonicalize_prospective_automation_profile(profile, appearance, changed)

    changed_state = copy.deepcopy(garments)
    changed_state[0]["aside"] = "the jacket is open"
    with pytest.raises(saved_looks.SavedLookError):
        saved_looks.canonicalize_prospective_automation_profile(profile, appearance, changed_state)
