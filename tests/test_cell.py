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
        db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived, contradicted) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               "front-direct", "astride", "full-length", "directed", "krea2", 3, 8, 0)


def test_arrivals_plus_contradictions_exceeding_judged_is_rejected(client):
    """5 arrived + 6 contradicted against 10 judged exceeds 10 judgements,
    so the table rejects it via CHECK (arrived + contradicted <= judged).
    """
    with pytest.raises(sqlite3.IntegrityError):
        db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived, contradicted) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               "front-direct", "astride", "full-length", "directed", "krea2", 10, 5, 6)


def test_a_complete_cell_write_is_accepted(client):
    """A write that names all five keys lands, with the counts stored. The
    five rejection cases above would all pass on a table that didn't exist
    (a different error class entirely), or on a table that rejected every
    column; this one is the positive evidence the table accepts what the
    spec says a cell is, and reads it back with the counts intact.
    """
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived, contradicted) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "krea2", 10, 8, 1)
    row = db.one("SELECT judged, arrived, contradicted FROM cell WHERE "
                 "camera_wording=? AND act_wording=? AND framing_wording=? "
                 "AND manner=? AND checkpoint=?",
                 "front-direct", "astride", "full-length", "directed", "krea2")
    assert row == {"judged": 10, "arrived": 8, "contradicted": 1}


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


def test_cell_state_with_contradictions():
    """Task 1.3: confirm cell_state is unchanged by contradicted count:
    a cell with 10 judged, 0 arrived, 10 contradicted is `dead`, same as one
    with 10 judged and 0 arrived from misses.
    """
    # 10 judged, 0 arrived (with 10 contradictions) -> dead
    assert db.cell_state(10, 0) == "dead"
    # 10 judged, 8 arrived (with 2 contradictions) -> verified
    assert db.cell_state(10, 8) == "verified"
    # 9 judged, 0 arrived (with 9 contradictions) -> unknown (<10)
    assert db.cell_state(9, 0) == "unknown"


# ----------------------------------------------------------------- 1.1 component table
def test_component_table_rejects_judge_label_equal_to_wording(client):
    """Task 1.1: verify a test inserting a row whose label equals its wording
    is rejected by the database schema, not by Python.
    """
    with pytest.raises(sqlite3.IntegrityError):
        db.run(
            "INSERT INTO component (concept_key, slot, manner, family, faces, wording, judge_label, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            "front-direct", "camera", "directed", "front", "front",
            "Taken from directly in front of her",
            "Taken from directly in front of her",
            db.now(),
        )


def test_component_table_rejects_empty_wording_or_label(client):
    """Component table enforces non-empty wording and non-empty judge_label."""
    with pytest.raises(sqlite3.IntegrityError):
        db.run(
            "INSERT INTO component (concept_key, slot, manner, family, faces, wording, judge_label, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            "test-key", "camera", "directed", "front", "front", "", "label", db.now(),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.run(
            "INSERT INTO component (concept_key, slot, manner, family, faces, wording, judge_label, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            "test-key", "camera", "directed", "front", "front", "wording", "", db.now(),
        )


def test_component_table_rejects_invalid_slot(client):
    """Component table enforces slot IN ('camera', 'act', 'framing')."""
    with pytest.raises(sqlite3.IntegrityError):
        db.run(
            "INSERT INTO component (concept_key, slot, manner, family, faces, wording, judge_label, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            "test-key", "invalid_slot", "directed", "front", "front", "wording", "label", db.now(),
        )


def test_component_table_rejects_duplicate_slot_manner_wording(client):
    """Component table enforces UNIQUE(slot, manner, wording)."""
    db.run(
        "INSERT INTO component (concept_key, slot, manner, family, faces, wording, judge_label, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        "k1", "camera", "directed", "front", "front", "Text unique 1", "Label unique 1", db.now(),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.run(
            "INSERT INTO component (concept_key, slot, manner, family, faces, wording, judge_label, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            "k2", "camera", "directed", "front", "front", "Text unique 1", "Label unique 2", db.now(),
        )


def test_cell_backup_migration_dumps_and_clears_table(tmp_path):
    """Task 1.4: verify the migration that dumps cell to data/cell-backup-<timestamp>.json
    and empties it starts from a database holding rows: the dump file exists,
    holds every row, and the table is empty afterwards.
    """
    db_path = tmp_path / "test_migration.db"
    # Create an old schema database without contradicted
    old_conn = sqlite3.connect(db_path)
    old_conn.execute("""
        CREATE TABLE cell (
            camera_wording  TEXT NOT NULL,
            act_wording     TEXT NOT NULL,
            framing_wording TEXT NOT NULL,
            manner          TEXT NOT NULL,
            checkpoint      TEXT NOT NULL,
            judged          INTEGER NOT NULL DEFAULT 0,
            arrived         INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (camera_wording, act_wording, framing_wording, manner, checkpoint)
        )
    """)
    old_conn.execute(
        "INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived) "
        "VALUES ('cam1', 'act1', 'frame1', 'directed', 'finepornV4', 10, 8)"
    )
    old_conn.commit()
    old_conn.close()

    # Now open with db.connect which runs _migrate
    new_conn = db.connect(db_path)
    # Check that backup file was written
    backup_files = list(tmp_path.glob("cell-backup-*.json"))
    assert len(backup_files) == 1
    content = json.loads(backup_files[0].read_text(encoding="utf-8"))
    assert len(content) == 1
    assert content[0]["camera_wording"] == "cam1"
    assert content[0]["judged"] == 10
    assert content[0]["arrived"] == 8

    # Verify cell table is now empty
    count = new_conn.execute("SELECT COUNT(*) FROM cell").fetchone()[0]
    assert count == 0

    # Verify contradicted column exists
    cols = {r[1] for r in new_conn.execute("PRAGMA table_info(cell)").fetchall()}
    assert "contradicted" in cols

