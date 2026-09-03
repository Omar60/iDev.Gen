"""Tests for data/catalogue-seed.json contents, judge labels, and faces."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest
import db


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "catalogue-seed.json"


def test_seed_file_exists_and_is_valid_json():
    """Task 3.1: data/catalogue-seed.json exists and parses as JSON array."""
    assert SEED_PATH.exists()
    items = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    assert isinstance(items, list)
    assert len(items) > 0


def test_the_seed_file_is_tracked_by_git():
    """The suite reads this file in `conftest.seeded`, and `data/` is otherwise
    ignored wholesale. Left untracked, every gate passes on the machine that
    generated it and six tests fail on a fresh clone — which is exactly how it
    shipped. The `.gitignore` un-ignores this one path; this asserts somebody
    also added it.
    """
    out = subprocess.run(["git", "ls-files", "--", "data/catalogue-seed.json"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git repository")
    assert out.stdout.strip() == "data/catalogue-seed.json", (
        "data/catalogue-seed.json is not tracked; run "
        "`git add data/catalogue-seed.json`"
    )


def test_seed_judge_labels_unique_and_not_equal_to_wording():
    """Task 3.2: no label equals its wording and no two labels in one slot are identical."""
    items = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    slot_labels: dict[str, set[str]] = {}

    for item in items:
        slot = item["slot"]
        wording = item["wording"]
        label = item.get("judge_label", "")

        assert label != "", f"missing judge_label on {item}"
        assert label != wording, f"judge_label equals wording on {item}"

        # Uniqueness within slot across distinct concept_keys
        # (the same concept_key across manners has the same judge_label)
        slot_labels.setdefault(slot, set())
        # Check that within a slot, different concept keys do not share identical judge labels
        # Let's map (slot, judge_label) -> concept_key
        slot_labels[slot].add((item["concept_key"], label))

    # For each slot, verify all distinct concept keys have distinct judge labels
    for slot, pairs in slot_labels.items():
        keys = [k for k, l in pairs]
        labels = [l for k, l in pairs]
        assert len(keys) == len(set(keys))
        assert len(labels) == len(set(labels)), f"duplicate labels in slot {slot}: {labels}"


def test_seed_camera_faces_rules():
    """Task 3.3: assert shoulder and behind families are 'back', mirror and pov families are empty,
    and no camera row is left unset (faces is one of 'front', 'side', 'back', '').
    """
    items = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    allowed_faces = {"front", "side", "back", ""}

    for item in items:
        assert "faces" in item, f"missing 'faces' on {item}"
        faces = item["faces"]
        assert faces in allowed_faces, f"invalid faces {faces!r} on {item}"

        if item["slot"] == "camera":
            family = item.get("family", "")
            if family in ("shoulder", "behind"):
                assert faces == "back", f"expected faces='back' for family {family} on {item}"
            elif family in ("mirror", "pov", "overhead"):
                assert faces == "", f"expected faces='' for family {family} on {item}"
            elif family in ("front", "floor"):
                assert faces == "front", f"expected faces='front' for family {family} on {item}"
            elif family == "side":
                assert faces == "side", f"expected faces='side' for family {family} on {item}"


# ---------------------------------------------------------------- solo acts
#
# `data/solo-acts-seed.json` is the act list candid and selfie did not have.
# Both manners held three acts each and all six were the same explicit
# arrangement with a second person, so a composed shoot could only ever be
# photographed at the END of its arc: photograph 1 dealt a dressed wardrobe
# state and `She is astride him` is a line arguing with itself.
#
# These eight forms are the other end. They are stage-NEUTRAL on purpose —
# geometry and nothing else — which is what lets one row be photographed dressed,
# half-dressed and undressed without a stage tag on the row. The wardrobe states
# say what is on her; the act says what her body is doing; neither answers the
# other's question.
SOLO_PATH = ROOT / "data" / "solo-acts-seed.json"

# What a stage-neutral act must never contain. A garment word pins the row to one
# state of the arc, and a second person pins it to the end of one.
GARMENTS = ("dress", "skirt", "jumper", "shirt", "top", "bra", "knickers",
            "panties", "stockings", "tights", "jeans", "trousers", "shoes",
            "boots", "coat", "jacket", "naked", "nude", "undressed", "bare")
SECOND_PERSON = (" him", " his ", " he ", " man", "two people", "both of them")


def test_the_solo_seed_is_valid_json_and_tracked():
    assert SOLO_PATH.exists()
    items = json.loads(SOLO_PATH.read_text(encoding="utf-8"))
    assert isinstance(items, list) and items
    out = subprocess.run(["git", "ls-files", "--", "data/solo-acts-seed.json"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git repository")
    assert out.stdout.strip() == "data/solo-acts-seed.json", (
        "data/solo-acts-seed.json is not tracked; `data/*` is ignored wholesale "
        "and .gitignore has to un-ignore this path too")


def test_every_solo_act_is_stage_neutral():
    """The property the rows exist for: an act that names a garment or a second
    person cannot be walked through an arc.

    A garment in the act contradicts the wardrobe state dealt to the same
    photograph — two texts describing the same clothes, and this sampler renders
    a contradiction as neither. A second person makes every photograph the end of
    the shoot, which is the hole these rows fill.
    """
    for item in json.loads(SOLO_PATH.read_text(encoding="utf-8")):
        line = item["wording"].lower()
        assert item["slot"] == "act"
        assert line.startswith("she "), (
            f"the person is not the subject of the main verb: {line!r}")
        for word in GARMENTS:
            assert word not in line, f"{item['concept_key']} names a garment ({word}): {line!r}"
        for word in SECOND_PERSON:
            assert word not in f" {line} ", (
                f"{item['concept_key']} names a second person ({word.strip()}): {line!r}")


def test_the_solo_acts_leave_the_tight_framings_drawable():
    """The frame reaches the lowest part of her the line names, so an act list
    whose every row names her feet is an act list that can only be photographed
    full-length — and candid's framing catalogue carries a headshot, a close-up
    and a waist-up that would then never draw (`backend/crop.py`).

    So the eight forms span the ladder on purpose. This asserts the half that is
    easy to lose when a row is reworded: at least three of them name nothing
    below her chest.
    """
    import crop
    by_key = {}
    for item in json.loads(SOLO_PATH.read_text(encoding="utf-8")):
        by_key[item["concept_key"]] = crop.lowest_named(item["wording"])
    high = [k for k, rung in by_key.items() if rung is not None and rung <= crop.CHEST]
    assert len(high) >= 3, f"only {len(high)} of {len(by_key)} acts stay above the waist: {by_key}"
    # And the other end: an arc of eight identical rungs is one photograph shot
    # eight times as far as the crop law is concerned.
    assert len(set(by_key.values())) >= 3, f"the acts sit on {set(by_key.values())}"


def test_the_solo_acts_import_and_are_offered_for_their_manner(client):
    """Imported through the same endpoint the rest of the catalogue goes through,
    and offered by the API for the manner they were written for. The import is
    idempotent on (slot, manner, concept_key or wording), so the second call adds
    nothing — which is what makes re-importing a seed safe.
    """
    items = json.loads(SOLO_PATH.read_text(encoding="utf-8"))
    first = client.post("/api/components/import", json=items).json()
    assert first["added"] == len(items), first
    again = client.post("/api/components/import", json=items).json()
    assert again["added"] == 0 and again["skipped"] == len(items), again

    for manner in ("candid", "selfie"):
        acts = [c for c in client.get("/api/components").json()
                if c["slot"] == "act" and c["manner"] == manner]
        keys = {a["concept_key"] for a in acts}
        assert {"all-fours", "kneeling-heels", "standing-hip"} <= keys, keys


# ------------------------------------------------- the phone is in her hand
#
# The composer draws a camera from a FAMILY (`_without_camera_mismatch`), so a
# family is only as fine as the constraint it has to carry. `front` used to hold
# both `Taken from directly in front of her` and `Phone held out at arm's length
# in front of her face` — one hands-free and one that costs her a hand — and an
# act with both hands flat on the floor could ask for `front` and be dealt the
# phone. The split is what lets an act say "from the front, but not with the
# phone in her hand".
CAMERA_SEEDS = ("candid-cameras-seed.json", "selfie-cameras-seed.json")


def _needs_a_hand(wording: str) -> bool:
    """Whether this camera costs her a hand to take. The catalogue says it in
    plain words — `in her right hand`, `in her own outstretched hand`, `at arm's
    length` — and a phone `propped on a high shelf` costs her nothing."""
    line = wording.lower()
    return "hand" in line or "arm's length" in line


def test_no_camera_family_mixes_a_held_phone_with_a_hands_free_view():
    """Every family is uniform in what it costs her, or the constraint an act
    writes down cannot be honoured: the composer picks any member of the family
    the act named, and one member of `front` needs a hand she does not have.
    """
    for name in CAMERA_SEEDS:
        items = json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
        by_family: dict[str, set[bool]] = {}
        for item in items:
            if item["slot"] != "camera":
                continue
            by_family.setdefault(item["family"], set()).add(_needs_a_hand(item["wording"]))
        for family, costs in by_family.items():
            assert len(costs) == 1, (
                f"{name}: family {family!r} holds both a camera she has to hold and one "
                f"she does not — an act cannot ask for one without risking the other")
        # And the split actually happened: the arm's-length phone is not in
        # `front` any more, which is the whole point of the family.
        arm = [i for i in items if i["concept_key"] == "front-arm-length"]
        assert arm and arm[0]["family"] != "front", f"{name}: {arm}"


# ------------------------------------------------------ the seeds must agree
#
# The catalogue ships as several files and the import walks them all, skipping a
# row whose (slot, manner, concept_key) already exists. So when two files
# disagree about a row, whichever imports FIRST wins and the other is silently
# dropped — on a fresh database, and only there. That is how `front-arm-length`
# came to sit in family `arm` in the live store and `front` in
# `catalogue-seed.json` for a whole day: the split was applied to the store and
# to `candid-cameras-seed.json`, the older file was never touched, and every
# check passed because no check read two files at once.
SEED_FILES = ("catalogue-seed.json", "crop-seed.json", "candid-acts-seed.json",
              "candid-cameras-seed.json", "candid-selfie-acts-seed.json",
              "selfie-cameras-seed.json", "solo-acts-seed.json",
              "feet-act-seed.json", "head-act-seed.json", "upright-act-seed.json")


def test_the_seed_files_agree_about_every_row_they_share():
    """One (slot, manner, concept_key) is one component, whichever file it is in.

    Compared on the three fields the import writes and a judging pass reads back:
    `family` is the reading key a verdict reduces to, `wording` is the line that
    gets queued, and `judge_label` is the question. A disagreement on any of
    them means a fresh clone measures something the store never measured.
    """
    seen: dict[tuple[str, str, str], tuple[str, dict]] = {}
    for name in SEED_FILES:
        path = ROOT / "data" / name
        if not path.exists():
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            key = (item["slot"], item["manner"], item["concept_key"])
            fields = {f: item.get(f, "") for f in ("family", "wording", "judge_label")}
            if key in seen:
                first_name, first_fields = seen[key]
                assert fields == first_fields, (
                    f"{key} disagrees between {first_name} and {name}: "
                    f"{first_fields} vs {fields}")
            else:
                seen[key] = (name, fields)


def test_no_two_framing_families_in_one_manner_describe_the_same_crop():
    """A judge cannot separate two families whose pictures look alike, so a
    framing pass over them is a coin flip recorded as a measurement.

    `framing` ("a three-quarter photograph from the knees up") and
    `crop-knee-up` ("knee-up") were two FAMILIES with one visual outcome for
    candid and selfie, which is why candid's framing slot went unjudged. They
    are one family with two wordings now — the shape directed already had for
    `mid-shot-edges` and `crop-knee-up` — and the cell keys on the wording, so
    both stay separately measurable.
    """
    rows = []
    for name in SEED_FILES:
        path = ROOT / "data" / name
        if path.exists():
            rows += [i for i in json.loads(path.read_text(encoding="utf-8"))
                     if i["slot"] == "framing"]
    knees = {(i["manner"], i["family"]) for i in rows
             if "knee" in i["judge_label"].lower()}
    by_manner: dict[str, set[str]] = {}
    for manner, family in knees:
        by_manner.setdefault(manner, set()).add(family)
    for manner, families in by_manner.items():
        assert len(families) == 1, (
            f"{manner}: the knees-up crop is spread over families {sorted(families)}; "
            f"a judge offered both cannot tell them apart")


# ------------------------------------------------- the rest of candid's acts
#
# `data/candid-acts-seed.json` is what candid was missing: five families held one
# member each (so the spreader had nothing to alternate with), no act was the
# undressing itself, the explicit solo acts did not exist at all, and the three
# acts with a second person were all the same penetration.
CANDID_ACTS = ROOT / "data" / "candid-acts-seed.json"
NEEDS = ("", "him", "nude")


def test_every_candid_act_declares_what_it_needs_and_says_it_in_the_wording():
    """`needs` and the wording are two spellings of one fact, and the day they
    disagree the draw deals a photograph the line cannot render.

    So: an act that needs him has a second person IN the wording (this sampler
    renders one body unless the line plainly says two — measured, thirty frames),
    and an act that needs nothing has none. `nude` is not asserted against the
    text: an act needing her bare is one whose ANATOMY is the subject, and it
    names her body rather than the absence of clothes.
    """
    items = json.loads(CANDID_ACTS.read_text(encoding="utf-8"))
    assert items
    for item in items:
        line = item["wording"].lower()
        assert item["slot"] == "act" and item["manner"] == "candid"
        assert item["needs"] in NEEDS, item
        assert line.startswith("she "), f"the person is not the subject: {line!r}"
        assert item["judge_label"] and item["judge_label"] != item["wording"], item
        second = ("two people in frame" in line)
        assert second == (item["needs"] == "him"), (
            f"{item['concept_key']}: needs={item['needs']!r} but "
            f"{'names' if second else 'does not name'} a second person")


def test_the_new_candid_acts_fill_the_families_that_had_one_member():
    """The point of the batch, asserted as the shape it was written for: the
    spreader never opens two consecutive photographs on the same family, and a
    family with a single act has nothing to alternate with. Sitting, kneeling and
    all-fours each held exactly one before this file.
    """
    import collections
    fam = collections.Counter(i["family"] for i in json.loads(CANDID_ACTS.read_text(encoding="utf-8")))
    for family in ("sitting", "kneeling", "all-fours"):
        assert fam[family] >= 2, f"{family} still has {fam[family]} in the new file"
