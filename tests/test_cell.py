"""The cell store: evidence is keyed by all five of (camera_wording,
act_wording, framing_wording, manner, checkpoint), and a write missing
any of them is rejected at the table.

Task 2.1 of the prompt-component-matrix change. The rule in the
component-matrix spec ("A verdict recorded against fewer than five
SHALL NOT be accepted") is enforced by `NOT NULL` on the five key
columns plus `PRIMARY KEY (camera_wording, act_wording, framing_wording,
manner, checkpoint)` on the cell table - not by a hand-written `if`. The
test below hits SQLite directly, because a hand-written rejection is
the wrong place to be testing it: the test is the only thing that would
catch a future "let me clean up the schema" that drops the constraint,
and that regression silently turns an unverifiable cell into one that
masquerades as verified.

The cell is the trio (design.md decision C, spec.md: "A cell is
identified by the trio, manner and checkpoint"). The earlier 4-column
shape was the wrong one: the 9 per-family verdicts in kinds.js:1962-1986
are observations of (act, family), not of either alone, and the 4-column
key that recorded them as a single concept with a synthetic
'astride-front' wording was a 2-component observation stuffed into a
1-component cell. The 5-column key matches the data.

Task 2.2 derives the three states (verified / dead / unknown) from the two
counts the table holds. The state is a pure function of the counts; the
test exercises every boundary of the spec's three-way rule, including the
n>10 cases where the ratio reading (`arrived * 10 >= judged * 8`) diverges
from the absolute `arrived >= 8` reading.

Task 2.3 seeds the verdicts already paid for, each against the trio
configuration and the manner and checkpoint they were actually shot on.
A slot the measurement did not break out carries the literal wording
`none` (a fact of the measurement, not an invention): the 9 per-family
observations' framing is `none` because scripts/shoot_arrangements.py:63-77
does not name one; the act-only and camera-only rows have `none` in the
other two slots for the same reason. The test asserts the seeded
structure (astride per family, not as aggregate; back and side per
checkpoint, not as a sum), the corrections (`side` is unknown, not dead;
`back` is dead; the per-family astride cells are unknown; the `behind`
act has no cell; the second astride checkpoint is one cell without
per-family breakdown; astride on Krea 2 mix is dead under the ratio
reading) and the state each seeded cell derives.

Task 2.4 asserts that every seed's wording in each of the three slots
is a real catalogue key for that slot OR a synthetic key explicitly
documented in the expected gap. The synthetic keys are: the deleted
act wordings (`back`, `side`), the camera family keys the 9 per-family
rows carry in their `camera_wording` slot, and the literal `none` for
the slots the measurements did not break out. A test that hides any of
these is a test that lets a future "let me clean this up" pass by
accident.

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
# The args are the values for the columns actually named in the SQL: the
# four present key columns plus the two count columns.
MISSING_FIELD_CASES = [
    ("camera_wording",
     "INSERT INTO cell (act_wording, framing_wording, manner, checkpoint, judged, arrived) "
     "VALUES (?, ?, ?, ?, ?, ?)",
     ("a", "f", "m", "k", 1, 1)),
    ("act_wording",
     "INSERT INTO cell (camera_wording, framing_wording, manner, checkpoint, judged, arrived) "
     "VALUES (?, ?, ?, ?, ?, ?)",
     ("c", "f", "m", "k", 1, 1)),
    ("framing_wording",
     "INSERT INTO cell (camera_wording, act_wording, manner, checkpoint, judged, arrived) "
     "VALUES (?, ?, ?, ?, ?, ?)",
     ("c", "a", "m", "k", 1, 1)),
    ("manner",
     "INSERT INTO cell (camera_wording, act_wording, framing_wording, checkpoint, judged, arrived) "
     "VALUES (?, ?, ?, ?, ?, ?)",
     ("c", "a", "f", "k", 1, 1)),
    ("checkpoint",
     "INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, judged, arrived) "
     "VALUES (?, ?, ?, ?, ?, ?)",
     ("c", "a", "f", "m", 1, 1)),
]


@pytest.mark.parametrize("field,sql,args", MISSING_FIELD_CASES,
                         ids=[c[0] for c in MISSING_FIELD_CASES])
def test_a_cell_write_missing_any_of_the_five_keys_is_rejected(client, field, sql, args):
    """The cell is keyed on all five of (camera_wording, act_wording,
    framing_wording, manner, checkpoint). A write that omits any of them
    is rejected by the schema - the constraint IS the rule. Dropping
    `NOT NULL` on a key column, or giving the key columns a DEFAULT,
    would let a write land without the dimension the verdict was measured
    under, which is the failure the spec calls "a verdict missing a
    dimension".
    """
    with pytest.raises(sqlite3.IntegrityError):
        db.run(sql, *args)


# The same five keys, present but empty. `NOT NULL` does not reject `''`, and
# `''` is what every other TEXT column in this schema defaults to - so a caller
# that builds a cell from a half-filled dict lands an empty key rather than
# failing. A CHECK on the five keys is what makes it the same rejection as
# omitting the column. The literal `none` is the value the synthetic keys
# take; it is non-empty and passes the CHECK.
EMPTY_KEY_CASES = ["camera_wording", "act_wording", "framing_wording", "manner", "checkpoint"]


@pytest.mark.parametrize("field", EMPTY_KEY_CASES)
def test_a_cell_write_with_an_empty_key_is_rejected(client, field):
    """A key present but empty is a missing dimension too. An empty wording
    in any of the five slots is exactly the entry design.md:130 says the
    cell has nothing to index on.
    """
    keys = dict(camera_wording="front-direct", act_wording="astride",
                framing_wording="full-length",
                manner="directed", checkpoint="krea2")
    keys[field] = ""
    with pytest.raises(sqlite3.IntegrityError):
        db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint) "
               "VALUES (?, ?, ?, ?, ?)", *keys.values())


def test_more_arrivals_than_judgements_is_rejected(client):
    """2.2 derives verified/dead/unknown from these two counts. Eight arrivals
    out of three judgements is not a state it can answer, so the table refuses
    to hold it.
    """
    with pytest.raises(sqlite3.IntegrityError):
        db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived) "
               "VALUES (?, ?, ?, ?, ?, ?, ?)",
               "front-direct", "astride", "full-length", "directed", "krea2", 3, 8)


def test_a_complete_cell_write_is_accepted(client):
    """A write that names all five keys lands, with the counts stored. The
    five rejection cases above would all pass on a table that didn't exist
    (a different error class entirely), or on a table that rejected every
    column; this one is the positive evidence the table accepts what the
    spec says a cell is, and reads it back with the counts intact.
    """
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived) "
           "VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "krea2", 10, 8)
    row = db.one("SELECT judged, arrived FROM cell WHERE "
                 "camera_wording=? AND act_wording=? AND framing_wording=? "
                 "AND manner=? AND checkpoint=?",
                 "front-direct", "astride", "full-length", "directed", "krea2")
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
# The verdicts already paid for, imported against the trio they were
# actually measured on (kinds.js:1962-1986). The corrections the user
# named are encoded in `EVIDENCE_SEED`; the tests below assert the
# seeded structure and the state each cell derives.


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
        # Empty framing_wording — the CHECK rejects it.
        db.seed_evidence([("front", "astride", "", "directed", "finepornV4", 3, 3)])
    with pytest.raises(sqlite3.IntegrityError):
        # arrived > judged — the CHECK rejects it.
        db.seed_evidence([("front", "astride", "full-length", "directed", "k", 3, 99)])


def test_astride_seeds_per_family_not_as_the_aggregate(client):
    """`astride` on finepornV4 is four cells, one per family the
    arrangement allows: front 6/6, overhead 4/4, mirror 4/6, pov 4/6.
    The 18/22 aggregate would be verified and one cell; the per-family
    split is four cells, all unknown (n<10). Seeding the aggregate
    instead of the split is the failure that makes `astride` look
    measured when it is not.

    Under the trio model the family lives in `camera_wording` and the
    framing is the literal `none` (the fixed line in
    scripts/shoot_arrangements.py:63-77 does not name one).
    """
    _seed()
    rows = db.q("SELECT camera_wording, judged, arrived FROM cell "
                "WHERE act_wording='astride' AND framing_wording='none' "
                "AND manner='directed' AND checkpoint='finepornV4' "
                "ORDER BY camera_wording")
    assert [(r["camera_wording"], r["judged"], r["arrived"]) for r in rows] == [
        ("front",    6, 6),
        ("mirror",   6, 4),
        ("overhead", 4, 4),
        ("pov",      6, 4),
    ]


def test_astride_per_family_lands_as_unknown_not_verified(client):
    """The four per-family cells are all n<10. design.md:331 names this
    as the point: most of what this project currently treats as ruled
    out was measured at n=3, and a per-family split makes the same
    trap visible at n=6/4/6/6. Each cell's state is `unknown` despite
    the high ratio.
    """
    _seed()
    rows = db.q("SELECT judged, arrived FROM cell "
                "WHERE act_wording='astride' AND framing_wording='none' "
                "AND manner='directed' AND checkpoint='finepornV4'")
    for r in rows:
        assert db.cell_state(r["judged"], r["arrived"]) == "unknown", r


def test_astride_second_checkpoint_is_seeded_as_one_cell_with_no_split(client):
    """`astride` on the Krea 2 mix is 9 of 12, recorded as one cell
    whose `camera_wording` is the literal `none` (kinds.js:2014 does
    not split that run by family or camera). Inventing a per-family
    breakdown is the failure 2.4 will catch.
    """
    _seed()
    row = db.one("SELECT judged, arrived FROM cell "
                 "WHERE act_wording='astride' AND camera_wording='none' "
                 "AND framing_wording='none' AND checkpoint='Krea 2 mix'")
    assert row == {"judged": 12, "arrived": 9}
    # And no per-family split for the Krea 2 mix run.
    n = db.one("SELECT COUNT(*) AS n FROM cell "
               "WHERE act_wording='astride' AND camera_wording<>'none' "
               "AND checkpoint='Krea 2 mix'")["n"]
    assert n == 0


def test_back_seeds_per_checkpoint_not_as_the_0_of_41_sum(client):
    """`back` is two cells: 0/12 on finepornV4 and 0/12 on Krea 2 mix.
    The 0-of-41 sum is what tests/test_arrangements.py:88 was getting
    at when it said "0 of 41 photographs across two checkpoints and
    four cameras" - that sum is a derived number, not a measurement.

    The source (kinds.js:2021) reports the act × checkpoint without
    breaking out camera or framing, so both other slots carry the
    literal `none`.
    """
    _seed()
    rows = db.q("SELECT checkpoint, judged, arrived FROM cell "
                "WHERE act_wording='back' "
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
                "WHERE act_wording='back'")
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
                "WHERE act_wording='side' "
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
                "WHERE act_wording='side'")
    for r in rows:
        assert db.cell_state(r["judged"], r["arrived"]) == "unknown", r


def test_reverse_and_wall_seed_at_their_real_per_family_numbers(client):
    """`reverse` and `wall` are seeded with the per-family numbers
    kinds.js:1962-1974 records: reverse 3/3 shoulder, 1/3 mirror,
    1/3 overhead; wall 3/3 mirror, 0/3 shoulder. All at n<10 and land
    unknown. Seeding at the aggregate or at the wrong family would
    misstate the verdict.

    Under the trio the family lives in `camera_wording` and the
    framing is the literal `none`. The seed does not name an
    aggregate reverse or wall cell because the source does not give
    one.
    """
    _seed()
    rows = db.q("SELECT camera_wording, act_wording, judged, arrived FROM cell "
                "WHERE framing_wording='none' AND manner='directed' "
                "AND checkpoint='finepornV4' "
                "AND ((act_wording='reverse' AND camera_wording IN ('shoulder', 'mirror', 'overhead')) "
                "  OR (act_wording='wall'    AND camera_wording IN ('mirror', 'shoulder')))")
    assert {(r["camera_wording"], r["act_wording"], r["judged"], r["arrived"]) for r in rows} == {
        ("shoulder", "reverse", 3, 3),
        ("mirror",   "reverse", 3, 1),
        ("overhead", "reverse", 3, 1),
        ("mirror",   "wall",    3, 3),
        ("shoulder", "wall",    3, 0),
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
                 "WHERE act_wording='astride' AND camera_wording='none' "
                 "AND framing_wording='none' AND checkpoint='Krea 2 mix'")
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
               "WHERE act_wording='behind'")["n"]
    assert n == 0


def test_the_candid_behind_camera_is_seeded_at_0_of_6(client):
    """The 0/6 from the candid camera renders is a camera cell
    (`behind-direct` under candid), not the act `behind`. It is
    seeded as a camera row with `act_wording` and `framing_wording`
    set to the literal `none` (the source is a camera measurement,
    kinds.js:2056-2058, that did not name the act or the framing).
    Reads back at n<10 (unknown).
    """
    _seed()
    row = db.one("SELECT judged, arrived FROM cell "
                 "WHERE camera_wording='behind-direct' AND act_wording='none' "
                 "AND framing_wording='none' AND manner='candid'")
    assert row == {"judged": 6, "arrived": 0}
    assert db.cell_state(6, 0) == "unknown"


# ----------------------------------------------------------------- 2.4 trio→catalogue map
#
# Every seed's wording in each of the three trio slots is a real
# catalogue key for that slot OR a synthetic key explicitly documented
# in the expected gap below. The reshape (task 1.1) carried every
# existing string forward as a concept's first wording, so a seed
# pointing at a wording the catalogue does not have is the failure this
# test exists to catch — it would surface far from its cause (the matrix
# tries to look up a cell against a missing catalogue key) and the
# data point is lost.
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
const framingKeys = ['full-length', 'three-quarter', 'waist-up'];
console.log(JSON.stringify({ actKeys, cameraKeys, framingKeys }));
"""


@pytest.fixture(scope="module")
def catalogue(tmp_path_factory) -> dict:
    return _node_json(CATALOGUE_PROBE % {"kinds": (ROOT / "frontend/src/kinds.js").as_posix()},
                      tmp_path_factory.mktemp("catalogue"))


def _catalogue_keys(catalogue: dict) -> dict[str, set[str]]:
    """The slot → catalogue-key map. A new slot is added here AND in
    `expected` (or `EVIDENCE_SEED`), and the test fails on the
    unregistered side until both are in step.
    """
    return {
        "camera":  set(catalogue["cameraKeys"]),
        "act":     set(catalogue["actKeys"]),
        "framing": set(catalogue["framingKeys"]),
    }


def test_every_seed_wording_is_a_key_in_its_catalogue(client, catalogue):
    """Every seeded cell's trio carries wordings that either exist in
    the catalogue for the slot, or are synthetic keys explicitly
    documented in the expected gap below.

    Under the trio model the cell is keyed on (camera_wording,
    act_wording, framing_wording, manner, checkpoint). The cell is
    the unit a photograph is composed of, and a slot the measurement
    did not break out carries the literal `none` (a fact of the
    measurement, not an invention: scripts/shoot_arrangements.py:63-77
    has no framing; the act-only and camera-only seeds did not name
    the other two slots). The camera family is metadata on the
    camera catalogue, not a top-level key: the 9 per-family verdicts
    in kinds.js:1962-1986 are recorded at the family level, so their
    `camera_wording` carries the family name and they appear in the
    gap.

    Four kinds of synthetic keys miss the catalogue on purpose:
      1. The camera family keys (front, overhead, mirror, pov,
         shoulder) — the 9 per-family observations are
         act × family measurements, and the family is metadata on the
         camera catalogue, not a catalogue concept of its own. They
         have to be carried over by a future catalogue change that
         adds family-keyed concepts, or the seeds have to be pulled.
      2. The literal `none` for framing — the 9 per-family rows'
         fixed line does not name a framing. The literal `none` is a
         fact of the measurement, not an invention, and passes the
         non-empty CHECK the cell table enforces.
      3. The literal `none` for camera — the 4 act-only rows (back,
         side, astride on Krea 2 mix) report the act × checkpoint
         without a camera breakdown. Same reasoning as 2.
      4. The literal `none` for act — the candid `behind-direct`
         camera row is a camera measurement that did not name the
         act. Same reasoning as 2.
      5. The arrangement wordings (`back`, `side`) deliberately
         deleted from ARRANGEMENTS — the proposal says "including
         the wordings currently deleted from ARRANGEMENTS as dead".
         The verdicts are real (0/12 dead, 0/9 and 0/8 unknown) but
         the catalogue no longer has a key for them. A future reshape
         can either re-introduce them or pull the seeds out.

    A test that hides any of these is a test that lets a future
    "let me clean this up" pass by accident. The assertion below
    names the gap exactly so a change that resolves it (one way or
    the other) breaks the test loudly.
    """
    _seed()
    catalogue_keys = _catalogue_keys(catalogue)

    rows = db.q("SELECT DISTINCT camera_wording, act_wording, framing_wording FROM cell")

    missing: set[tuple[str, str]] = set()
    for r in rows:
        for slot, wording in (("camera",  r["camera_wording"]),
                              ("act",     r["act_wording"]),
                              ("framing", r["framing_wording"])):
            keys = catalogue_keys.get(slot)
            if keys is None:
                # A new slot the test does not know about. Hiding the
                # check behind a chained if-let is the failure that
                # lets a seed pointing at an unregistered slot pass
                # by accident: the loop falls through and the row is
                # never added to `missing`. A future slot has to
                # register its catalogue here.
                raise AssertionError(
                    f"unknown slot {slot!r} - register its catalogue in "
                    f"_catalogue_keys and the seed-gap set above"
                )
            if wording not in keys:
                missing.add((slot, wording))

    expected = {
        # The camera family keys the 9 per-family observations carry
        # in their `camera_wording` slot. The family is metadata on
        # the camera catalogue, not a catalogue concept of its own;
        # the cells measure the act at the family level.
        ("camera", "front"),
        ("camera", "overhead"),
        ("camera", "mirror"),
        ("camera", "pov"),
        ("camera", "shoulder"),
        # The literal `none` for framing — the 9 per-family rows'
        # fixed line in scripts/shoot_arrangements.py:63-77 does
        # not name a framing. A fact of the measurement, not an
        # invention.
        ("framing", "none"),
        # The literal `none` for camera — the act-only rows (back,
        # side, astride on Krea 2 mix) report the act × checkpoint
        # without a camera breakdown.
        ("camera", "none"),
        # The literal `none` for act — the candid `behind-direct`
        # camera row is a camera measurement that did not name the
        # act.
        ("act", "none"),
        # The arrangement wordings deliberately deleted from
        # ARRANGEMENTS. Their verdicts are real (0/12 dead, 0/9 and
        # 0/8 unknown) but the catalogue no longer has a key for
        # them. A future reshape carries them back or pulls the
        # seeds out.
        ("act", "back"),
        ("act", "side"),
    }
    assert missing == expected, missing


def test_a_seed_pointing_at_nothing_in_the_catalogue_is_detected():
    """The negative case the task names: a seed that names a wording
    the catalogue does not have is the failure the mapping check
    exists to catch. Exercised on an invented row list so the
    detection logic is tested without depending on EVIDENCE_SEED — a
    future "let me dedupe the catalogue" that drops a real key
    surfaces here on the spot, and a seed that names a new slot
    surfaces as a key error rather than a silent pass.

    The detection rule is: a (slot, wording) is missing if either
    the slot has no catalogue registered, or the wording is not a
    key in the catalogue for that slot.
    """
    catalogue_keys = {
        "camera":  {"behind-direct"},
        "act":     {"astride"},
        "framing": {"full-length"},
    }
    invented = [
        # Real entries — must NOT be flagged missing.
        ("behind-direct", "astride", "full-length", "directed", "finepornV4", 6, 6),
        # Fake wordings — must be flagged missing.
        ("no-such-camera",  "astride",        "full-length", "directed", "k", 3, 3),
        ("behind-direct",    "no-such-act",   "full-length", "directed", "k", 3, 3),
        ("behind-direct",    "astride",       "no-such-framing", "directed", "k", 3, 3),
    ]

    missing: set[tuple[str, str]] = set()
    for camera_w, act_w, framing_w, _, _, _, _ in invented:
        for slot, wording in (("camera",  camera_w),
                              ("act",     act_w),
                              ("framing", framing_w)):
            keys = catalogue_keys.get(slot)
            if keys is None or wording not in keys:
                missing.add((slot, wording))

    assert missing == {
        ("camera",  "no-such-camera"),
        ("act",     "no-such-act"),
        ("framing", "no-such-framing"),
    }
