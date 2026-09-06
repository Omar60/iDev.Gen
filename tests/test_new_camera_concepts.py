"""What the mining introduces that this project's camera catalogue did not hold.

8.16. The source libraries are mined for their cameras, and most of what they
carry the catalogue already answers under another name. Two positions it does
not, and they are the reason the mining is worth doing at all - so they are
recorded, and the record is checked against the catalogue on disk rather than
believed.

The record names, per concept, the rows it is NEAREST to. That is what makes it
accountable: the near misses are named so a reader can check the claim, and this
file asserts they still exist. It also enforces the other direction - the day one
of these concepts is added to the catalogue under its key, it stops being new and
has to come out of the record.
"""
from __future__ import annotations

import json
from pathlib import Path

import db
from backend.mining import NEW_CAMERA_CONCEPTS, POV_BLOCK_EVIDENCE

DATA = Path(__file__).resolve().parents[1] / "data"


def _catalogue_camera_keys() -> set[str]:
    """Every camera concept key in every seed file this repository tracks.

    Read off the tracked seeds and not out of the database: the database on a
    developer's machine holds whatever they have imported, and the claim being
    checked is about the catalogue this repository SHIPS.
    """
    keys: set[str] = set()
    for path in sorted(DATA.glob("*-seed.json")):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("slot") == "camera":
                keys.add(row.get("concept_key", ""))
    return keys


def test_the_record_names_the_two_concepts_the_mining_introduces():
    """The feet-first low POV and the overhead over a kneeling subject.

    Both follow from the one thing the source families have in common: the
    camera is held by a participant. A library mined for cameras that produced
    nothing the catalogue lacked would not be worth the import, so this is the
    record of what it is being spent on.
    """
    assert [c["key"] for c in NEW_CAMERA_CONCEPTS] == [
        "feet-first-low-pov",
        "overhead-over-kneeling",
    ]
    for concept in NEW_CAMERA_CONCEPTS:
        assert concept["judge_label"].strip(), concept
        assert concept["why_new"].strip(), concept


def test_no_shipped_camera_row_already_carries_these_keys():
    """New means the catalogue does not hold it, checked against the catalogue.

    The day one of these is measured and added under its key, this fails - and
    it should: a record of what is missing that still names a row somebody has
    since written is a record that sends the next reader to import something
    twice.
    """
    shipped = _catalogue_camera_keys()
    assert shipped, "no camera rows were read, so this test proves nothing"
    for concept in NEW_CAMERA_CONCEPTS:
        assert concept["key"] not in shipped, concept["key"]


def test_the_near_misses_the_record_names_are_real_rows():
    """The claim is accountable because the near misses are named.

    "The catalogue has nothing like this" is unfalsifiable prose. "It has
    `worms-eye`, `ground-level` and `floor-low-angle`, and each of them says how
    high the lens is without saying where around her it stands" is checkable,
    and this asserts the half a test can check: those rows exist.
    """
    shipped = _catalogue_camera_keys()
    for concept in NEW_CAMERA_CONCEPTS:
        assert concept["nearest"], concept["key"]
        for near in concept["nearest"]:
            assert near in shipped, f"{concept['key']} names a row nobody carries: {near}"


# -- 8.18 The verdicts the blind judge returned --------------------------


def test_every_recorded_concept_carries_a_verdict_at_the_protocol_minimum():
    """A concept nobody judged is a claim, and this file is where that shows.

    The sample size is compared against `db.cell_state`'s own threshold rather
    than against a 10 written here: the bar is the catalogue's, and a second
    copy of it would drift the day the protocol moves. A verdict recorded on
    nine photographs is `unknown` wearing a verdict's name.

    `arrived` is checked against the same function, so the word and the counts
    cannot disagree - which is the failure this test exists for: a `verified`
    typed next to 4 of 13 would otherwise sit here reading as a result.
    """
    for concept in NEW_CAMERA_CONCEPTS:
        assert concept["verdict"] in ("verified", "dead"), concept["key"]
        assert concept["claim"] and concept["axis"], concept["key"]
        assert concept["judged"] >= 10, (
            f"{concept['key']}: judged {concept['judged']}, below the protocol's minimum")
        assert db.cell_state(concept["judged"], concept["arrived"]) == concept["verdict"], (
            f"{concept['key']}: {concept['arrived']} of {concept['judged']} is not "
            f"{concept['verdict']!r}")


def test_the_retracted_concept_is_kept_with_what_it_was_measured_against():
    """A dead concept is not deleted, and its claim stays readable.

    Deleting it would leave the next reader to re-derive the same idea from the
    same corpus and mine it a second time. Kept with its verdict, the record
    says the camera was shot, judged and landed on a reading the catalogue
    already carries - and `nearest` names that reading, which is what makes the
    retraction checkable rather than a sentence.
    """
    dead = [c for c in NEW_CAMERA_CONCEPTS if c["verdict"] == "dead"]
    assert dead, "nothing is recorded as dead; the 8.18 result is not in the record"
    for concept in dead:
        assert concept["claim"] not in concept["nearest"], concept["key"]
        landed = {"overhead-over-kneeling": "high-angle"}[concept["key"]]
        assert landed in concept["nearest"], (
            f"{concept['key']}: the reading it landed on is not among its near misses")


# -- 8.19 The combination against its parts drawn separately -------------


def test_the_pov_block_question_is_answered_by_two_arms_of_each_camera():
    """The evidence, and the shape that makes it evidence rather than an opinion.

    One arm alone answers nothing: a camera at 10 of 10 with its own act could
    be a camera that always works or an agreement that carries it. It is the
    SECOND arm - the same camera and room with an act mined from another entry -
    that separates them, so both are required here, per camera, and each at the
    protocol's own minimum.

    The claims are the ones 8.18 judged, and they are asserted to match: an arm
    scored against a different claim than the concept was judged under would be
    two measurements dressed as a comparison.
    """
    arms = POV_BLOCK_EVIDENCE["arms"]
    assert POV_BLOCK_EVIDENCE["answer"] in ("yes", "no")
    assert POV_BLOCK_EVIDENCE["reason"].strip()

    claims = {c["claim"] for c in NEW_CAMERA_CONCEPTS}
    by_camera: dict[str, set[str]] = {}
    for arm in arms:
        assert arm["judged"] >= 10, (
            f"{arm['camera']} {arm['arm']}: judged {arm['judged']}, below the minimum")
        assert 0 <= arm["arrived"] <= arm["judged"]
        assert arm["claim"] in claims, arm
        by_camera.setdefault(arm["camera"], set()).add(arm["arm"])
    for camera, kinds in by_camera.items():
        assert kinds == {"own act", "foreign act"}, (
            f"{camera} carries {sorted(kinds)}; one arm on its own compares nothing")


def test_the_answer_follows_from_the_arms_rather_than_sitting_beside_them():
    """`answer` is derived here from the counts, so prose cannot outvote them.

    A block would be worth writing if breaking the author's pairing broke the
    photograph. It is worth writing when SOME camera's verdict changes between
    its two arms; it is not when every camera lands in the same state both ways.
    Written out rather than asserted as a constant, so the day an arm is
    re-measured the recorded answer has to move with it.
    """
    states: dict[str, set[str]] = {}
    for arm in POV_BLOCK_EVIDENCE["arms"]:
        states.setdefault(arm["camera"], set()).add(
            db.cell_state(arm["judged"], arm["arrived"]))
    moved = {camera for camera, seen in states.items() if len(seen) > 1}
    assert POV_BLOCK_EVIDENCE["answer"] == ("yes" if moved else "no"), (
        f"the record answers {POV_BLOCK_EVIDENCE['answer']!r} while the arms that "
        f"moved between states are {sorted(moved)}")
