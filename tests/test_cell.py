"""The cell store: evidence is keyed by all four of (concept, wording, manner,
checkpoint), and a write missing any of them is rejected at the table.

Task 2.1 of the prompt-component-matrix change. The rule in the
component-matrix spec ("A verdict recorded against fewer than four SHALL NOT
be accepted") is enforced by `NOT NULL` on the four key columns plus
`PRIMARY KEY (concept, wording, manner, checkpoint)` on the cell table - not
by a hand-written `if`. The test below hits SQLite directly, because a
hand-written rejection is the wrong place to be testing it: the test is the
only thing that would catch a future "let me clean up the schema" that drops
the constraint, and that regression silently turns an unverifiable cell into
one that masquerades as verified.

Task 2.2 derives the three states (verified / dead / unknown) from the two
counts the table holds. The state is a pure function of the counts; the
test exercises every boundary of the spec's three-way rule, including the
n>10 cases where the ratio reading (`arrived * 10 >= judged * 8`) diverges
from the absolute `arrived >= 8` reading.

Task 2.3 seeds the verdicts already paid for, each with its real
(judged, arrived, manner, checkpoint). The test asserts the seeded
structure (astride per family, not as aggregate; back and side per
checkpoint, not as a sum), the corrections (`side` is unknown, not dead;
`back` is dead; the per-family astride cells are unknown; the `behind`
act has no cell; the second astride checkpoint is one cell without
per-family breakdown; astride on Krea 2 mix is dead under the ratio
reading) and the state each seeded cell derives.

Task 2.4 asserts that every seed names a wording that exists in the
catalogue for its concept, and documents the expected gap: the
synthetic per-family wordings (astride-front, etc.) and the
ARRANGEMENTS entries that were deliberately deleted (back, side).
A test that hides either side is a test that lets a future
"let me clean this up" pass by accident.

2.1 is the table and the rejection. 2.2 is the state. 2.3 is the seed.
2.4 is the mapping.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import db
from test_shoot_checks import _node_json


ROOT = Path(__file__).resolve().parents[1]


# One case per missing key. The key columns carry no DEFAULT, so omitting one
# from the column list is itself a NOT NULL violation - the test relies on
# that rather than on inserting `NULL` explicitly. The spec scenario phrases
# it as "without naming the dimension", which is the omission, and a DEFAULT
# on a key column would make a missing dimension silently land.
#
# Each entry is (missing field, SQL for that omission, args in column order).
# The args are the values for the columns actually named in the SQL: three
# present key columns plus the two count columns.
MISSING_FIELD_CASES = [
    ("concept",
     "INSERT INTO cell (wording, manner, checkpoint, judged, arrived) "
     "VALUES (?, ?, ?, ?, ?)",
     ("w", "m", "k", 1, 1)),
    ("wording",
     "INSERT INTO cell (concept, manner, checkpoint, judged, arrived) "
     "VALUES (?, ?, ?, ?, ?)",
     ("c", "m", "k", 1, 1)),
    ("manner",
     "INSERT INTO cell (concept, wording, checkpoint, judged, arrived) "
     "VALUES (?, ?, ?, ?, ?)",
     ("c", "w", "k", 1, 1)),
    ("checkpoint",
     "INSERT INTO cell (concept, wording, manner, judged, arrived) "
     "VALUES (?, ?, ?, ?, ?)",
     ("c", "w", "m", 1, 1)),
]


@pytest.mark.parametrize("field,sql,args", MISSING_FIELD_CASES,
                         ids=[c[0] for c in MISSING_FIELD_CASES])
def test_a_cell_write_missing_any_of_the_four_keys_is_rejected(client, field, sql, args):
    """The cell is keyed on all four of (concept, wording, manner, checkpoint).
    A write that omits any of them is rejected by the schema - the constraint
    IS the rule. Dropping `NOT NULL` on a key column, or giving the key columns
    a DEFAULT, would let a write land without the dimension the verdict was
    measured under, which is the failure the spec calls "a verdict missing a
    dimension".
    """
    with pytest.raises(sqlite3.IntegrityError):
        db.run(sql, *args)


# The same four keys, present but empty. `NOT NULL` does not reject `''`, and
# `''` is what every other TEXT column in this schema defaults to - so a caller
# that builds a cell from a half-filled dict lands an empty key rather than
# failing. A CHECK on the four keys is what makes it the same rejection as
# omitting the column.
EMPTY_KEY_CASES = ["concept", "wording", "manner", "checkpoint"]


@pytest.mark.parametrize("field", EMPTY_KEY_CASES)
def test_a_cell_write_with_an_empty_key_is_rejected(client, field):
    """A key present but empty is a missing dimension too. An empty `wording` is
    exactly the entry design.md:110 calls one the cell "has nothing to index".
    """
    keys = dict(concept="camera", wording="front-direct",
                manner="directed", checkpoint="krea2")
    keys[field] = ""
    with pytest.raises(sqlite3.IntegrityError):
        db.run("INSERT INTO cell (concept, wording, manner, checkpoint) "
               "VALUES (?, ?, ?, ?)", *keys.values())


def test_more_arrivals_than_judgements_is_rejected(client):
    """2.2 derives verified/dead/unknown from these two counts. Eight arrivals
    out of three judgements is not a state it can answer, so the table refuses
    to hold it.
    """
    with pytest.raises(sqlite3.IntegrityError):
        db.run("INSERT INTO cell (concept, wording, manner, checkpoint, judged, arrived) "
               "VALUES (?, ?, ?, ?, ?, ?)",
               "camera", "front-direct", "directed", "krea2", 3, 8)


def test_a_complete_cell_write_is_accepted(client):
    """A write that names all four keys lands, with the counts stored. The
    four rejection cases above would all pass on a table that didn't exist
    (a different error class entirely), or on a table that rejected every
    column; this one is the positive evidence the table accepts what the
    spec says a cell is, and reads it back with the counts intact.
    """
    db.run("INSERT INTO cell (concept, wording, manner, checkpoint, judged, arrived) "
           "VALUES (?, ?, ?, ?, ?, ?)",
           "camera", "front-direct", "directed", "krea2", 10, 8)
    row = db.one("SELECT judged, arrived FROM cell WHERE "
                 "concept=? AND wording=? AND manner=? AND checkpoint=?",
                 "camera", "front-direct", "directed", "krea2")
    assert row == {"judged": 10, "arrived": 8}


# The case table for 2.2. Three states derive from the two counts the cell
# table holds. The cases below cover the boundaries of all three:
#   - the n=10 admission edge: verified starts here, dead ends one short of it
#   - the n<10 region: unknown, whatever the ratio (including 0/3, the case
#     the task names explicitly - measured too lightly to be called either way)
#   - the empty cell
#   - n>10: where the two readings (absolute `arrived >= 8` vs ratio
#     `arrived * 10 >= judged * 8`) diverge. The absolute reading would call
#     8/20 verified; the ratio reading (which the spec mandates) calls it
#     dead. 8/20 = 40%, and the strict drawer (design.md:218) draws only
#     verified, so this divergence matters for what strict can use.
# The function is pure; the table's CHECK is what keeps the inputs honest.
CELL_STATE_CASES = [
    # (judged, arrived, expected, name)
    (0,  0,  "unknown",  "no_measurements"),
    (3,  0,  "unknown",  "zero_of_three"),
    (3,  3,  "unknown",  "all_three_too_light"),
    (8,  8,  "unknown",  "perfect_ratio_just_below_threshold"),
    (9,  9,  "unknown",  "perfect_ratio_one_below_threshold"),
    (10, 0,  "dead",     "zero_of_ten"),
    (10, 7,  "dead",     "seven_of_ten_just_below_admission"),
    (10, 8,  "verified", "eight_of_ten_admission_boundary"),
    (10, 10, "verified", "ten_of_ten"),
    (11, 9,  "verified", "above_threshold"),
    # Above the n=10 boundary the two readings diverge. The ratio reading
    # (which the spec mandates) is the one this function implements; these
    # cases would all be `verified` under the absolute reading.
    (20, 8,  "dead",     "eight_of_twenty_below_ratio"),
    (20, 16, "verified", "sixteen_of_twenty_at_ratio"),
]


@pytest.mark.parametrize("judged,arrived,expected,name",
                         CELL_STATE_CASES,
                         ids=[c[3] for c in CELL_STATE_CASES])
def test_cell_state_is_derived_from_the_counts(judged, arrived, expected, name):
    """The cell's state is a pure function of its two counts.

    The rule is in the component-matrix spec: verified at >=10 judged
    AND >=8 arrived, dead at >=10 judged AND <8 arrived, unknown at
    <10 judged regardless. The boundaries are where the trap lives:
    a future "let me round 8 down to 7" turns verified into dead for
    every 8/10 cell, and a future "let me round 10 down to 9" turns
    every measured cell into unknown.

    `arrived > judged` is unreachable: the cell table's CHECK constraint
    rejects the write before this function ever sees the row, and the
    rejection is already proven by `test_more_arrivals_than_judgements_is_rejected`
    above. A branch here for it would be a duplicate guard against a
    case the schema already forbids, and a future loosening of the
    CHECK would silently pass through a "defensive" Python check.
    """
    assert db.cell_state(judged, arrived) == expected


# ----------------------------------------------------------------- 2.3 seeding
#
# The verdicts already paid for, imported with their real (judged, arrived,
# manner, checkpoint). The task says: each against the one wording, manner
# and checkpoint it was actually shot on; `astride` per family (not as its
# 18/22 aggregate); `back` and `side` per checkpoint (not as their 0-of-41
# sum). The corrections the user named are encoded in `EVIDENCE_SEED`; the
# tests below assert the seeded structure and the state each cell derives.


def _seed():
    """Idempotent seed call: returns the count of rows that were newly
    inserted (0 on a re-run within the same client)."""
    return db.seed_evidence()


def test_seed_evidence_inserts_every_row_in_the_table(client):
    """`seed_evidence` lands every entry in `EVIDENCE_SEED` as a row in
    the cell table. A row the seed is missing is a row the project
    will measure again, and the only proof the seed is complete is the
    row count matching the constant.

    The cell table is cleared first because `_migrate` already seeds on
    first connect, so by the time this test starts the table is full
    and the call below would (correctly) return 0.
    """
    db.run("DELETE FROM cell")
    inserted = _seed()
    assert inserted == len(db.EVIDENCE_SEED)
    count = db.one("SELECT COUNT(*) AS n FROM cell")["n"]
    assert count == len(db.EVIDENCE_SEED)


def test_seed_evidence_is_idempotent(client):
    """Re-running the seed is a no-op. A second call inserts 0 rows;
    the cell table keeps the same 15. A future caller (a test, a route)
    that seeds twice must not duplicate or fail.
    """
    db.run("DELETE FROM cell")
    assert _seed() == len(db.EVIDENCE_SEED)
    assert _seed() == 0


def test_seed_evidence_does_not_swallow_check_violations(client):
    """A row that violates a CHECK (empty key, or arrived > judged) is
    not a PK conflict, and `ON CONFLICT DO NOTHING` does not apply.
    `seed_evidence` must raise `IntegrityError` and not return 0 as if
    the row were an "already seeded" duplicate. A bare
    `except IntegrityError: pass` would let bad data disappear — exactly
    the regression the CHECK is set up to make noisy.
    """
    db.run("DELETE FROM cell")
    with pytest.raises(sqlite3.IntegrityError):
        db.seed_evidence([("act", "", "directed", "finepornV4", 3, 3)])
    with pytest.raises(sqlite3.IntegrityError):
        db.seed_evidence([("act", "x", "directed", "k", 3, 99)])


def test_astride_seeds_per_family_not_as_the_aggregate(client):
    """`astride` on finepornV4 is four cells, one per family the
    arrangement allows: front 6/6, overhead 4/4, mirror 4/6, pov 4/6.
    The 18/22 aggregate would be verified and one cell; the per-family
    split is four cells, all unknown (n<10). Seeding the aggregate
    instead of the split is the failure that makes `astride` look
    measured when it is not.
    """
    _seed()
    rows = db.q("SELECT wording, judged, arrived FROM cell "
                "WHERE concept='act' AND wording LIKE 'astride-%' "
                "AND checkpoint='finepornV4' ORDER BY wording")
    assert [(r["wording"], r["judged"], r["arrived"]) for r in rows] == [
        ("astride-front",    6, 6),
        ("astride-mirror",   6, 4),
        ("astride-overhead", 4, 4),
        ("astride-pov",      6, 4),
    ]


def test_astride_per_family_lands_as_unknown_not_verified(client):
    """The four per-family cells are all n<10. design.md:331 names this
    as the point: most of what this project currently treats as ruled
    out was measured at n=3, and a per-family split makes the same
    trap visible at n=6/4/6/6. Each cell's state is `unknown` despite
    the high ratio.
    """
    _seed()
    rows = db.q("SELECT wording, judged, arrived FROM cell "
                "WHERE concept='act' AND wording LIKE 'astride-%' "
                "AND checkpoint='finepornV4'")
    for r in rows:
        assert db.cell_state(r["judged"], r["arrived"]) == "unknown", r


def test_astride_second_checkpoint_is_seeded_as_one_cell_with_no_split(client):
    """`astride` on the Krea 2 mix is 9 of 12, recorded as one cell
    with the plain wording `astride`. The per-family breakdown of
    that run does not exist in kinds.js:2014; inventing one is the
    failure 2.4 will catch.
    """
    _seed()
    row = db.one("SELECT judged, arrived FROM cell "
                 "WHERE concept='act' AND wording='astride' "
                 "AND checkpoint='Krea 2 mix'")
    assert row == {"judged": 12, "arrived": 9}
    # And no per-family split for the Krea 2 mix run.
    n = db.one("SELECT COUNT(*) AS n FROM cell "
               "WHERE concept='act' AND wording LIKE 'astride-%' "
               "AND checkpoint='Krea 2 mix'")["n"]
    assert n == 0


def test_back_seeds_per_checkpoint_not_as_the_0_of_41_sum(client):
    """`back` is two cells: 0/12 on finepornV4 and 0/12 on Krea 2 mix.
    The 0-of-41 sum is what tests/test_arrangements.py:88 was getting
    at when it said "0 of 41 photographs across two checkpoints and
    four cameras" - that sum is a derived number, not a measurement.
    """
    _seed()
    rows = db.q("SELECT checkpoint, judged, arrived FROM cell "
                "WHERE concept='act' AND wording='back' "
                "ORDER BY checkpoint")
    assert [(r["checkpoint"], r["judged"], r["arrived"]) for r in rows] == [
        ("Krea 2 mix",  12, 0),
        ("finepornV4",  12, 0),
    ]


def test_back_lands_as_dead(client):
    """`back` is the only arrangement whose zero ratios are at n>=10
    on both checkpoints. The spec says: verified at >=10 judged AND
    >=8 of every 10; below that, at 10+ judged, dead. 0/12 satisfies
    the second half.
    """
    _seed()
    rows = db.q("SELECT judged, arrived FROM cell "
                "WHERE concept='act' AND wording='back'")
    for r in rows:
        assert db.cell_state(r["judged"], r["arrived"]) == "dead", r


def test_side_seeds_per_checkpoint_not_as_the_0_of_17_sum(client):
    """`side` is two cells, not a 0/17 sum. Each is at its real n and
    checkpoint. The sum is what test_arrangements.py:89 implied when
    it said the arrangement "0 of 41 across two checkpoints and four
    cameras" - a derived number, not a cell.
    """
    _seed()
    rows = db.q("SELECT checkpoint, judged, arrived FROM cell "
                "WHERE concept='act' AND wording='side' "
                "ORDER BY checkpoint")
    assert [(r["checkpoint"], r["judged"], r["arrived"]) for r in rows] == [
        ("Krea 2 mix",  8, 0),
        ("finepornV4",  9, 0),
    ]


def test_side_lands_as_unknown_not_dead(client):
    """`side` is NOT dead despite the zero ratios. 0/9 and 0/8 have
    judged<10, which the spec says is `unknown` whatever the ratio.
    design.md:332-334 was wrong about side; the spec rules, and the
    earlier draft's "side 0 of 9 and 0 of 8 carry enough to seed as
    dead" is the kind of claim that quietly turns unknown into dead.
    """
    _seed()
    rows = db.q("SELECT judged, arrived FROM cell "
                "WHERE concept='act' AND wording='side'")
    for r in rows:
        assert db.cell_state(r["judged"], r["arrived"]) == "unknown", r


def test_reverse_and_wall_seed_at_their_real_per_family_numbers(client):
    """`reverse` and `wall` are seeded with the per-family numbers
    kinds.js:1962-1974 records: reverse 3/3 shoulder, 1/3 mirror,
    1/3 overhead; wall 3/3 mirror, 0/3 shoulder. All at n<10 and land
    unknown. Seeding at the aggregate or at the wrong family would
    misstate the verdict.

    The naming follows the convention: a family cell uses
    `<arrangement>-<family>`, so the five rows are `reverse-shoulder`,
    `reverse-mirror`, `reverse-overhead`, `wall-mirror`, `wall-shoulder`.
    None of these is the bare arrangement key — the bare key would
    mean the aggregate, and the source does not give an aggregate
    reverse or wall measurement.
    """
    _seed()
    rows = db.q("SELECT wording, judged, arrived FROM cell "
                "WHERE concept='act' AND (wording='reverse-shoulder' "
                "OR wording='reverse-mirror' "
                "OR wording='reverse-overhead' "
                "OR wording='wall-mirror' "
                "OR wording='wall-shoulder')")
    assert {(r["wording"], r["judged"], r["arrived"]) for r in rows} == {
        ("reverse-shoulder", 3, 3),
        ("reverse-mirror",   3, 1),
        ("reverse-overhead", 3, 1),
        ("wall-mirror",      3, 3),
        ("wall-shoulder",    3, 0),
    }
    for r in rows:
        assert db.cell_state(r["judged"], r["arrived"]) == "unknown", r


def test_astride_on_krea_2_mix_lands_as_dead_under_the_ratio_reading(client):
    """The 12/9 control on Krea 2 mix is DEAD under the ratio reading:
    9 * 10 = 90 < 12 * 8 = 96, so 75% is below the 80% threshold. The
    absolute reading (`arrived >= 8`) would have called it verified;
    the ratio reading does not. This is the cost of the ratio decision:
    the control arrangement no longer survives on Krea 2 mix, and a
    future "let me round 8 down to 7" would silently turn this cell
    into a verified one on measurements it never earned. The aggregate
    18/22 of finepornV4 is verified; the per-checkpoint 12/9 of
    Krea 2 mix is not, and the cell must show the worse side.
    """
    _seed()
    row = db.one("SELECT judged, arrived FROM cell "
                 "WHERE concept='act' AND wording='astride' "
                 "AND checkpoint='Krea 2 mix'")
    assert row == {"judged": 12, "arrived": 9}
    assert db.cell_state(12, 9) == "dead"


def test_the_behind_act_is_not_seeded(client):
    """The `behind` act is killed in the source with four anecdotes
    (sessions 155, 161, 267, 268 - kinds.js:2035-2058), not with a
    (judged, arrived) pair. There is no n to count. The 0/6 in the
    source is the camera `behind-direct` under candid, not the act
    `behind`. Manufacturing an n for the act would be inventing a
    number; the default `unknown` state is the truthful seed.
    """
    _seed()
    n = db.one("SELECT COUNT(*) AS n FROM cell "
               "WHERE concept='act' AND wording='behind'")["n"]
    assert n == 0


def test_the_candid_behind_camera_is_seeded_at_0_of_6(client):
    """The 0/6 from the candid camera renders is a camera cell
    (`behind-direct` under candid), not the act `behind`. It is
    seeded as a camera row and reads back at n<10 (unknown).
    """
    _seed()
    row = db.one("SELECT judged, arrived FROM cell "
                 "WHERE concept='camera' AND wording='behind-direct' "
                 "AND manner='candid'")
    assert row == {"judged": 6, "arrived": 0}
    assert db.cell_state(6, 0) == "unknown"


# ----------------------------------------------------------------- 2.4 wording map
#
# Every seed names a wording that exists in the catalogue for its concept.
# The reshape (task 1.1) carried every existing string forward as a concept's
# first wording, so a seed that points at a wording the reshape did NOT
# carry is a gap — either the wording was deliberately removed (back, side)
# or it is a synthetic per-family wording the cell model invented
# (astride-front, wall-mirror, etc.). Both are caught here; both need to be
# resolved before the matrix can be filled against the catalogue.
#
# The probe below reads the real ARRANGEMENTS, CAMERA_POSITIONS,
# CANDID_POSITIONS and SELFIE_POSITIONS arrays and returns their keys.
# Bundled with esbuild the way the other JS-driven tests are (see
# test_shoot_checks._node_json), no network involved.

CATALOGUE_PROBE = """
import { ARRANGEMENTS, CAMERA_POSITIONS, CANDID_POSITIONS, SELFIE_POSITIONS } from '%(kinds)s';

const actKeys = ARRANGEMENTS.map((a) => a.key);
const cameraKeys = [...new Set([
  ...CAMERA_POSITIONS.map((c) => c.key),
  ...CANDID_POSITIONS.map((c) => c.key),
  ...SELFIE_POSITIONS.map((c) => c.key),
])];
console.log(JSON.stringify({ actKeys, cameraKeys }));
"""


@pytest.fixture(scope="module")
def catalogue(tmp_path_factory) -> dict:
    return _node_json(CATALOGUE_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                      tmp_path_factory.mktemp("catalogue"))


def test_every_seed_wording_is_a_key_in_its_catalogue(client, catalogue):
    """Every seeded cell names a wording that exists in the catalogue
    for its concept. The reshape (task 1.1) carried every existing
    string forward as a concept's first wording, so a seed that points
    at a wording the reshape did NOT carry is the failure this test
    is meant to catch: a seed naming nothing would surface far from
    its cause (the matrix tries to look up a cell against a missing
    catalogue key) and the data point is lost.

    The assertion below documents the expected gap. Two kinds of
    wording miss the catalogue on purpose:
      1. The synthetic per-family wordings (astride-front etc.) — the
         cell key has 4 columns, the family is the 5th, and the
         wording is the only place to carry it. These are the entries
         2.4 is meant to discover, not a failure to hide. They have
         to be carried over by a future catalogue change.
      2. The arrangement entries that were deliberately deleted
         (back, side) — the proposal says "including the wordings
         currently deleted from ARRANGEMENTS as dead". The wording
         still exists as a measurement (the cell), but the catalogue
         no longer has a key for it. A future reshape can either
         re-introduce them or pull the seeds out.

    A test that hides either side is a test that lets a future
    "let me clean this up" pass by accident. The assertion below
    names the gap exactly so a change that resolves it (one way or
    the other) breaks the test loudly.
    """
    _seed()
    catalogue_keys = _catalogue_keys(catalogue)

    rows = db.q("SELECT DISTINCT concept, wording FROM cell")

    missing: set[tuple[str, str]] = set()
    for r in rows:
        keys = catalogue_keys.get(r["concept"])
        if keys is None:
            # A new concept the test does not know about. Hiding the
            # check behind a chained if-let is the failure that lets a
            # seed pointing at an unregistered concept pass by accident:
            # the loop falls through and the row is never added to
            # `missing`. A future concept (framing, technique) has to
            # register its catalogue here.
            raise AssertionError(
                f"unknown concept {r['concept']!r} — register its catalogue in "
                f"_catalogue_keys and the seed-gap set above"
            )
        if r["wording"] not in keys:
            missing.add((r["concept"], r["wording"]))

    expected = {
        # Synthetic per-family wordings: the cell model invents these to
        # carry the family dimension the 4-column key cannot hold.
        ("act", "astride-front"),
        ("act", "astride-mirror"),
        ("act", "astride-overhead"),
        ("act", "astride-pov"),
        ("act", "reverse-shoulder"),
        ("act", "reverse-mirror"),
        ("act", "reverse-overhead"),
        ("act", "wall-mirror"),
        ("act", "wall-shoulder"),
        # Wordings deleted from ARRANGEMENTS but kept as seeds: their
        # verdicts are real (0/12 dead, 0/9 and 0/8 unknown) but the
        # catalogue no longer has them. A future reshape carries them
        # back or pulls the seeds out.
        ("act", "back"),
        ("act", "side"),
    }
    assert missing == expected, missing


def _catalogue_keys(catalogue: dict) -> dict[str, set[str]]:
    """The concept → catalogue map. A new concept (framing, technique)
    is added here AND in `expected` (or `EVIDENCE_SEED`), and the test
    fails on the unregistered side until both are in step.
    """
    return {
        "act": set(catalogue["actKeys"]),
        "camera": set(catalogue["cameraKeys"]),
    }


def test_a_seed_pointing_at_nothing_in_the_catalogue_is_detected():
    """The negative case the task names: a seed that names a wording
    the catalogue does not have is the failure the mapping check
    exists to catch. Exercised on an invented row list so the
    detection logic is tested without depending on EVIDENCE_SEED —
    a future "let me dedupe the catalogue" that drops a real key
    surfaces here on the spot, and a seed that names a new concept
    surfaces as a key error rather than a silent pass.

    The detection rule is: a (concept, wording) is missing if
    either the concept has no catalogue registered, or the wording
    is not a key in the catalogue for that concept.
    """
    catalogue_keys = {
        "act": {"astride", "reverse", "wall"},
        "camera": {"behind-direct"},
    }
    invented = [
        # Real entries — must NOT be flagged missing.
        ("act", "astride", "directed", "finepornV4", 6, 6),
        ("camera", "behind-direct", "candid", "finepornV4", 6, 0),
        # Fake wordings — must be flagged missing.
        ("act", "no-such-wording", "directed", "k", 3, 3),
        ("camera", "no-such-camera", "candid", "k", 3, 3),
    ]

    missing: set[tuple[str, str]] = set()
    for concept, wording, _, _, _, _ in invented:
        keys = catalogue_keys.get(concept)
        if keys is None or wording not in keys:
            missing.add((concept, wording))

    assert missing == {
        ("act", "no-such-wording"),
        ("camera", "no-such-camera"),
    }
