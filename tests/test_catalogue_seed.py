"""Tests for data/catalogue-seed.json contents, judge labels, and faces."""
from __future__ import annotations

import json
import re
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
NEEDS = ("", "him", "access", "furniture")


def test_every_candid_act_declares_what_it_needs_and_says_it_in_the_wording():
    """`needs` and the wording are two spellings of one fact, and the day they
    disagree the draw deals a photograph the line cannot render.

    So: an act that needs him has a second person IN the wording (this sampler
    renders one body unless the line plainly says two — measured, thirty frames),
    and an act that needs nothing has none. `access` is not asserted against the
    text: an act needing her bare is one whose ANATOMY is the subject, and it
    names her body rather than the absence of clothes.

    The token was `nude` until it was measured against the shoot it describes: a
    toy or a hand needs ACCESS, not an undressed woman, and a garment pulled
    aside gives access while she is still wearing it.
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


# ------------------------------------------------------- candid's rooms
#
# `data/candid-rooms-seed.json` is not a catalogue: a room is a starting text for
# `session.look`, and the picker in ModelDetail fills the textarea from it. It
# lives in a seed file rather than in `component` because that table's `slot` is
# the closed vocabulary of the trio, and a fourth slot would reach the draw.
ROOMS = ROOT / "data" / "candid-rooms-seed.json"
# The register is the manner's and the place is the room's, so the look only
# exists once the two are joined. `frontend/src/rooms.js` does the join for the
# picker; this is the same join, and the tests below assert against its result
# rather than against a stored row, because a stored row is half a look.
# The register is the manner's and the place is the room's, so a look only
# exists once the two are joined - the same join the picker does. Read from the
# repo's own data dir explicitly: `conftest` points `IDEVGEN_DATA_DIR` at a tmp
# directory for the whole suite, and these tests are about the shipped seeds.
from backend.importer import derive_multi_body  # noqa: E402
from backend.db import cell_state  # noqa: E402
from backend.room_registry import (  # noqa: E402
    VERDICT_WORDS,
    compose_look,
    load_manner_registers,
    prose_names_piece,
)

REGISTERS = load_manner_registers(data_dir=ROOT / "data")


def composed_look(manner, place):
    return compose_look(manner, place, REGISTERS)


def test_every_room_names_the_furniture_it_offers():
    """`offers` is a data field and the place is prose, and the day they disagree
    the picker promises a piece no photograph can contain.

    It caught one: `shower` offered a bench its sentence never mentioned, from
    the outside model that wrote it. The comparison itself is
    `room_registry.prose_names_piece`, shared with the importer's derivation and
    with the import suite, because three copies of one rule is how the room that
    offers a piece nobody can photograph gets through the one copy that is wrong.

    An imported room may offer nothing - that is the honest answer where its
    prose names none of its source's props. A room THIS project wrote may not:
    each of the nine was written to offer a piece, and one that stopped would be
    a room that quietly stopped licensing every act that needs furniture.
    """
    rooms = json.loads(ROOMS.read_text(encoding="utf-8"))
    assert len(rooms) >= 9
    for room in rooms:
        assert room["offers"], f"{room['key']}: a room this project wrote offers nothing"
        for piece in room["offers"]:
            assert prose_names_piece(piece, room["place"]), (
                f"{room['key']}: offers {piece!r}, which its place never names")


def test_every_composed_room_carries_the_constant_half_of_the_look():
    """The capture clause and the hair are what make twenty frames one shoot, so
    a look that drops them is not a look, it is half of one. Sessions 370 and 371
    spliced every candidate behind exactly this text.

    The invariant is unchanged and WHERE it is true has moved. A room no longer
    stores a look: it stores a place, and the register belongs to the manner, so
    none of these four assertions is true of a stored row and all four are true
    of what the picker composes. Asserting them on the row would be asserting
    that the split never happened.
    """
    rooms = json.loads(ROOMS.read_text(encoding="utf-8"))
    keys = [r["key"] for r in rooms]
    assert len(keys) == len(set(keys)), keys
    for room in rooms:
        # A permission, and these nine restrict nothing: the register left the
        # text in the split, so the bedroom is a bedroom and a directed session
        # can be shot in it. Empty is every manner, including one nobody has
        # written yet.
        assert room["manners"] == [], room["key"]
        look = composed_look("candid", room["place"])
        assert look.startswith("Small sensor,"), room["key"]
        assert "She wears her hair loose" in look, room["key"]
        # the room half sits behind the constant half, and both are present
        assert 60 <= len(look.split()) <= 110, (room["key"], len(look.split()))


def test_composing_the_nine_rooms_yields_the_text_they_yielded_before_the_split():
    """5.2: the split is a refactor of storage, not a rewrite of the looks.

    Every one of these ten texts was rendered - the nine candid rooms in
    sessions 370 and 371, the studio in 381 - so a byte that moved is a
    measurement that no longer describes what the app produces. The expected
    strings are the pre-split file, copied in whole rather than rebuilt from
    the halves, because rebuilding them from the halves is the thing under
    test asserting itself.
    """
    before = json.loads((ROOT / "tests" / "rooms-before-the-split.json").read_text(encoding="utf-8"))
    # The manner each was measured under is the file it is in, not a field on
    # the row: `manner` became `manners` in 6.7 and says what a room is ALLOWED
    # in, which is a different question and no longer answers this one.
    rooms = [("candid", r) for r in json.loads(ROOMS.read_text(encoding="utf-8"))]
    rooms += [("directed", r) for r in json.loads(DIRECTED_LOOKS.read_text(encoding="utf-8"))]
    assert len(rooms) == len(before) == 10
    for manner, room in rooms:
        assert composed_look(manner, room["place"]) == before[room["key"]], room["key"]


def test_every_room_stores_the_marking_its_own_prose_earns():
    """A room's multi-body marking is computed at import and read at compose,
    so nothing recomputes it in between - which is exactly why it can go stale.

    Editing one of these nine by hand to add a second person into the place is
    a one-line change that would leave `multi_body` empty and the room composing
    into a single-subject run with somebody else in the frame. This is the only
    thing that would notice.
    """
    rooms = json.loads(ROOMS.read_text(encoding="utf-8"))
    rooms += json.loads(DIRECTED_LOOKS.read_text(encoding="utf-8"))
    for room in rooms:
        assert "multi_body" in room, room["key"]
        assert room["multi_body"] == derive_multi_body(room["place"]), room["key"]


def test_the_rooms_seed_is_tracked_by_git():
    """`data/*` is ignored wholesale and every seed has to un-ignore its own path.

    This one matters more than the others: `ModelDetail.jsx` imports it at build
    time, so an untracked rooms seed is a fresh clone whose FRONTEND DOES NOT
    BUILD — a strictly worse version of the failure
    `test_the_seed_file_is_tracked_by_git` was written for.
    """
    out = subprocess.run(["git", "ls-files", "--", "data/candid-rooms-seed.json"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git repository")
    assert out.stdout.strip() == "data/candid-rooms-seed.json", (
        "data/candid-rooms-seed.json is not tracked; .gitignore has to un-ignore "
        "it and somebody has to `git add` it")


# ------------------------------------------------------- directed's look
#
# `data/directed-looks-seed.json` is the same kind of file as the rooms above and
# for the same reason, but it could not be a row IN that file: the tests there
# assert `manner == "candid"` and candid's amateur-technique opening, which is
# exactly what directed's look must not carry.
DIRECTED_LOOKS = ROOT / "data" / "directed-looks-seed.json"


def test_directed_looks_are_directed_and_not_candid_in_disguise():
    """The whole point of the file is that directed's look is written in
    directed's voice. A row that opens with candid's capture clause would put an
    amateur register on a directed shoot, which is the mistake the file exists to
    prevent -- and it would do it silently, because the picker only shows the
    label.

    The hair sentence is the one thing both manners share: session 381 measured
    it as what makes the ten frames of a session agree on her hair.
    """
    looks = json.loads(DIRECTED_LOOKS.read_text(encoding="utf-8"))
    assert looks
    keys = [r["key"] for r in looks]
    assert len(keys) == len(set(keys)), keys
    for row in looks:
        # The one restriction anybody has written, and the reason is stored
        # with it: the place is a photographic set-up, so it makes no sense
        # under a manner where nobody is photographing her.
        assert row["manners"] == ["directed"], row["key"]
        assert row["manners_reason"].strip(), row["key"]
        look = composed_look("directed", row["place"])
        assert "She wears her hair loose" in look, row["key"]
        for candid_only in ("small sensor", "sensor noise", "washed-out",
                            "no studio lighting"):
            assert candid_only not in look.lower(), (row["key"], candid_only)
        assert 60 <= len(look.split()) <= 110, (row["key"], len(look.split()))


def test_the_directed_looks_seed_is_tracked_by_git():
    """`ModelDetail.jsx` imports it at build time, so an untracked seed is a
    fresh clone whose frontend does not build. Same failure the rooms seed has
    its own test for.
    """
    out = subprocess.run(["git", "ls-files", "--", "data/directed-looks-seed.json"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git repository")
    assert out.stdout.strip() == "data/directed-looks-seed.json", (
        "data/directed-looks-seed.json is not tracked; .gitignore has to un-ignore "
        "it and somebody has to `git add` it")


# ------------------------------------------------------- reading axes
#
# A judging pass offers one menu and asks for one answer, so the readings on
# that menu have to be mutually exclusive. Directed's camera vocabulary was not:
# it held where the camera stood AND how high it was, both true of every
# photograph, and session 382 measured what that costs -- a camera side-on in 10
# of 10 came back `hip-level` 6 and `side-level` 1. `axis` is what splits one
# slot into the questions it actually asks.
READINGS = ROOT / "data" / "readings-seed.json"


def test_a_slot_either_asks_one_question_or_names_every_axis():
    """A half-tagged vocabulary is the worst of both: the pass filters to an
    axis and silently drops the readings nobody labelled, so the menu loses
    answers that are true and the deck records them as misses.

    So within one (slot, manner) either NO reading carries an axis -- the whole
    vocabulary is one question, which is what candid's cameras and every act and
    framing vocabulary are -- or EVERY one does.
    """
    rows = json.loads(READINGS.read_text(encoding="utf-8"))
    by_slot: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by_slot.setdefault((r["slot"], r["manner"]), []).append(r)
    for key, group in sorted(by_slot.items()):
        tagged = [r for r in group if r.get("axis")]
        assert len(tagged) in (0, len(group)), (
            f"{key}: {len(tagged)} of {len(group)} readings carry an axis; "
            "tag all of them or none")


# Directed's cameras were described twice, once per seed file: `catalogue-seed.json`
# holds the nine furniture-free forms as sentences and `directed-cameras-seed.json`
# holds the terms, and four cameras carried a different `family` in each -- so
# both families needed a reading and both readings said the same thing. Two
# identical sentences on one menu is a coin toss that `arrived` then scores.
#
# Merged on 2026-09-03 onto the sentence vocabulary, which is the one the
# written path plans with and the one the arrangement rows name:
# `side-level` -> `side`, `over-shoulder` -> `shoulder`, `rear` -> `behind`,
# `ground-level` -> `floor`. `scripts/merge_camera_families.py` moved the store,
# the arrangement lists and the verdicts already recorded under the old spelling
# together, which is what the first, reverted attempt did not do.


# A `family` that is not a question the judge of that slot asks. Every one of
# these is filed under `camera` for `directed` and none of them is a camera
# POSITION: `full` and `medium` are crops (`full-body` is "head to feet",
# `medium-shot` is "waist up"), and `composition`, `geometry`, `lens` and
# `register` are properties of the picture (a vanishing point, a fisheye, a ring
# light, "shot on a Canon EOS"). Writing a reading for one would put two
# questions on one menu, which is the failure that killed two questions in a day
# and the reason `axis` exists at all.
#
# It is an explicit list and not a rule, because "is this a camera position" is
# not something a predicate can see. Splitting directed's camera slot by axis is
# its own change; when it lands, these names leave this list rather than gaining
# a reading.
# Families a seed ships that a judge cannot be asked about, exempted from the
# check below. All four are mined CANDIDATES - properties of the picture, not
# positions of the camera - and `camera-candidates-seed.json` is not imported by
# any route: `/api/components/import` with no body reads `catalogue-seed.json`.
#
# `full` and `medium` were here too, and they were something else: crop terms
# filed in the camera slot, with `full body` and `waist-up` duplicating the
# framing slot's own wordings exactly. They are gone from the seed rather than
# exempted here - the framing catalogue carries those crops under readings that
# CAN be judged.
NOT_A_QUESTION = {
    ("camera", "directed", fam)
    for fam in ("composition", "geometry", "lens", "register")
}


def test_every_family_a_seed_ships_can_be_judged():
    """A component whose family has no reading cannot be judged at all.

    `judge-pass` refuses the whole slot naming the family -- "no reading for
    family/families ontop in act catalogue for manner 'directed'" -- so ONE
    uncovered family takes the entire slot of that manner out of the app. It
    took directed's act slot out for good: the vocabulary shipped five solo
    readings written for an act catalogue that no longer ships, the three
    two-person acts that DO ship carried none, and directed is the default
    manner. A fresh clone could not judge an act.

    Nothing caught it because the existing agreement test compares the component
    seeds against EACH OTHER and never crosses into the readings file. This one
    crosses.
    """
    readings = json.loads(READINGS.read_text(encoding="utf-8"))
    have = {(r["slot"], r["manner"], r["key"]) for r in readings}

    missing = []
    shipped = set()
    for name in sorted(p.name for p in (ROOT / "data").glob("*seed*.json")):
        path = ROOT / "data" / name
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("slot"):
                continue
            family = (item.get("family") or "").strip()
            if not family:
                continue                      # a row with no family reduces to nothing
            key = (item["slot"], item["manner"], family)
            shipped.add(key)
            if key not in have and key not in NOT_A_QUESTION:
                missing.append(f"{name}: {item['manner']}/{item['slot']} family {family!r}")

    assert not missing, (
        "these families ship and cannot be judged -- judge-pass refuses the "
        "whole slot for the manner:\n  " + "\n  ".join(sorted(set(missing))))

    # An exemption for a family no seed ships any more is an exemption that
    # cannot fail, and it is the door the next crop term walks in through: two
    # of these covered `full` and `medium` for as long as the camera slot
    # carried crops, so re-adding one would have kept this test green.
    stale = sorted(k for k in NOT_A_QUESTION if k not in shipped)
    assert not stale, f"NOT_A_QUESTION exempts families no seed ships: {stale}"


def test_no_two_readings_are_the_same_sentence_on_the_same_menu():
    """A menu that offers one sentence twice records a correct reading under
    whichever spelling the judge happened to land on, and the other copy scores
    it a miss.
    """
    rows = json.loads(READINGS.read_text(encoding="utf-8"))
    seen: dict[tuple[str, str, str, str], str] = {}
    for r in rows:
        k = (r["slot"], r["manner"], r.get("axis", ""), r["label"].strip().lower())
        assert k not in seen, (
            f"{r['slot']}/{r['manner']}: {r['key']!r} and {seen[k]!r} are the same "
            "sentence on the same menu")
        seen[k] = r["key"]


# 6.5: the verdict store. A measurement is this project's own work; the text it
# was taken against may be an import that never reaches git. Stored together,
# every measurement leaves with the licensing decision - so they are stored
# apart, and the rule that keeps the tracked half committable is that it
# reproduces none of the prose it was measured against.
VERDICTS = ROOT / "data" / "room-verdicts-seed.json"

# Six consecutive words is prose. Shorter runs collide honestly - "on the bed",
# "in the kitchen" - and a note saying which run measured a room is allowed to
# name what is in it. What may not appear is the room's own sentences.
PROSE_RUN = 6


def _runs(text, n=PROSE_RUN):
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def verdict_store_prose(store, places):
    """(key, manner, field) for every stored value reproducing a room's prose."""
    known = set()
    for place in places:
        known |= _runs(place)
    offenders = []
    for key, per_manner in (store or {}).items():
        for manner, record in (per_manner or {}).items():
            for field, value in (record or {}).items():
                if isinstance(value, str) and _runs(value) & known:
                    offenders.append((key, manner, field))
    return sorted(offenders)


def test_the_verdict_vocabulary_is_the_catalogue_s_own_three_words():
    """6.9: a room measured at ten frames is the same kind of measurement as a
    cell measured at ten, so it is read in the same three words.

    Bound to `cell_state` rather than written out beside it: two lists of the
    same three strings in two files drift the first time somebody adds a
    fourth, and the drift is silent - the picker keeps rendering, showing a
    word no rule can produce.
    """
    produced = {cell_state(judged, arrived)
                for judged in (0, 1, 9, 10, 20)
                for arrived in range(0, judged + 1)}
    assert produced == set(VERDICT_WORDS)
    # "unverified" is the word the tasks use in prose and it is NOT a stored
    # verdict: a second vocabulary for one question means the picker has to
    # know which of the two it is reading.
    assert "unverified" not in VERDICT_WORDS


def test_the_ten_shipped_rooms_carry_a_converted_verdict_and_keep_their_sentence():
    """6.10: the nine rooms and the studio carried free text where the
    catalogue carries a vocabulary. The conversion is honest or it is nothing.

    "built 1/1 in session 370" is one photograph, and this repo's own judging
    protocol puts the verified bar at ten because below it the reading sits
    inside the judge's noise. So none of the nine is verified, and the sample
    size is the one the sentence states and no higher - which for the oldest
    room, "in use since session 351", is none at all.

    The studio is the one that converts: session 381 shot ten seeds and the
    sentence says the softbox, the paper roll and the reflector are all built.
    Ten judged, ten arrived, under directed - the manner it was shot in and the
    only one it is allowed in.

    The prose is not deleted, because the vocabulary cannot say which run
    measured what: it moves to the note beside the verdict.
    """
    store = json.loads(VERDICTS.read_text(encoding="utf-8"))
    rooms = json.loads(ROOMS.read_text(encoding="utf-8"))
    looks = json.loads(DIRECTED_LOOKS.read_text(encoding="utf-8"))

    for room in rooms:
        record = store[room["key"]]["candid"]
        assert record["verdict"] == "unknown", room["key"]
        assert record["sample_size"] <= 1, room["key"]
        assert record["note"].strip(), room["key"]
        assert "session 3" in record["note"], room["key"]

    studio = store["studio-softbox"]["directed"]
    assert studio["verdict"] == "verified" and studio["sample_size"] == 10
    assert "session 381" in studio["note"]
    assert "candid" not in store["studio-softbox"], (
        "it was shot under directed, and allowed nowhere else")

    # And the row it came off carries none of it. Two homes for one fact is
    # what the split undid; a row that still answered "verdict" would answer it
    # with whatever it was carrying the day the store was written.
    for row in rooms + looks:
        assert "verdict" not in row, row["key"]
        assert "sample_size" not in row, row["key"]
        assert row["key"] in store, row["key"]


def test_the_verdict_store_holds_measurements_and_no_room_text():
    """Keys, manners, verdicts and sample sizes. Not a sentence of a room.

    The store is tracked and the imported rooms are not, which is the whole
    reason the two are separate files - and it is also how source prose gets
    committed by accident, one note at a time. So the shape is closed and the
    prose rule is asserted rather than trusted: a value carrying six
    consecutive words of any room's place is the room's sentence copied, under
    whatever field name.

    The detector is exercised on invented rooms in both directions, because the
    real store starts empty and a rule that only ever reads an empty file is a
    rule nobody has run.
    """
    store = json.loads(VERDICTS.read_text(encoding="utf-8"))
    assert isinstance(store, dict)

    rooms = json.loads(ROOMS.read_text(encoding="utf-8"))
    places = [r["place"] for r in rooms]

    for key, per_manner in store.items():
        assert re.fullmatch(r"[a-z0-9-]+", key), key
        assert isinstance(per_manner, dict), key
        for manner, record in per_manner.items():
            assert manner and isinstance(manner, str), key
            assert set(record) <= {"verdict", "sample_size", "note"}, (key, manner)
            # 6.9: one of the catalogue's own words, and a count with it. A
            # verdict with no sample size is the free text this store replaced -
            # "verified" says nothing until it says out of how many.
            assert record["verdict"] in VERDICT_WORDS, (key, manner, record["verdict"])
            assert isinstance(record["sample_size"], int), (key, manner)
            assert record["sample_size"] >= 0, (key, manner)
            # And the word agrees with the count by the catalogue's own rule,
            # which is the only thing that keeps a room's verdict comparable to
            # a cell's. A stored word the counts cannot produce is a reading
            # somebody typed.
            assert record["verdict"] == cell_state(
                record["sample_size"],
                record["sample_size"] if record["verdict"] == "verified" else 0,
            ), (key, manner, record)

    assert verdict_store_prose(store, places) == []

    # The rule, run on something. A note naming the run is fine; the room's own
    # sentence under any field name is not.
    planted = places[0]
    assert verdict_store_prose(
        {"bedroom-night": {"candid": {"note": "in use since session 351"}}}, places) == []
    assert verdict_store_prose(
        {"bedroom-night": {"candid": {"note": planted}}}, places) == [
        ("bedroom-night", "candid", "note")]
    assert verdict_store_prose(
        {"bedroom-night": {"candid": {"verdict": planted[:120]}}}, places) == [
        ("bedroom-night", "candid", "verdict")]
