"""SQLite storage for iDev.Gen — models (characters), sessions, shots, workflows.

Single-user local app: one connection, WAL, check_same_thread off. No ORM on
purpose — four tables and hand-written SQL is less code than the mapping layer.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    graph         TEXT NOT NULL,           -- ComfyUI API-format JSON
    node_map      TEXT NOT NULL,           -- {"positive": "6.inputs.text", ...}
    -- What this graph is for: t2i|edit|angles|scene. Empty means untagged, which
    -- is every workflow imported before kinds existed: it stays offered everywhere.
    kind          TEXT NOT NULL DEFAULT '',
    is_template   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    lora_name     TEXT NOT NULL DEFAULT '',   -- as ComfyUI names it
    trigger       TEXT NOT NULL DEFAULT '',
    lora_strength REAL NOT NULL DEFAULT 1.0,
    base_positive TEXT NOT NULL DEFAULT '',
    base_negative TEXT NOT NULL DEFAULT '',
    workflow_id   INTEGER REFERENCES workflow(id) ON DELETE SET NULL,
    settings      TEXT NOT NULL DEFAULT '{}', -- default width/height/steps/cfg
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    id            INTEGER PRIMARY KEY,
    model_id      INTEGER NOT NULL REFERENCES model(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft',  -- draft|running|done|cancelled|failed
    workflow_id   INTEGER REFERENCES workflow(id) ON DELETE SET NULL,
    -- The graph that edits an existing photo instead of painting one from noise.
    -- Empty means the session is text-to-image only, which is every older session.
    reference_workflow_id INTEGER REFERENCES workflow(id) ON DELETE SET NULL,
    anchor_shot_ids TEXT NOT NULL DEFAULT '[]',   -- shot ids feeding reference/reference2/reference3
    look          TEXT NOT NULL DEFAULT '',       -- hair, makeup, place, light: constant for the shoot
    -- The garments. A *default*, not a constant: a take may carry its own, which
    -- is what lets one shoot walk from dressed to undressed without every take
    -- fighting a sentence that says the jacket is still on.
    wardrobe      TEXT NOT NULL DEFAULT '',
    settings      TEXT NOT NULL DEFAULT '{}',     -- resolved gen settings for the run
    -- Free-text tags the user puts on a session: trimmed, compared
    -- case-insensitively, never empty. Stored as JSON so the list route can read
    -- it whole and no second query is needed. No new table on purpose: four
    -- tables is what this app is shaped to, and a `session_tag` join is a column
    -- the whole point of NOT having.
    tags          TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shot (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    shot_index    INTEGER NOT NULL DEFAULT 0,
    shot_label    TEXT NOT NULL DEFAULT '',
    prompt        TEXT NOT NULL DEFAULT '',
    negative      TEXT NOT NULL DEFAULT '',
    use_reference INTEGER NOT NULL DEFAULT 0,     -- edit the session's anchor instead of painting from noise
    -- The anchors this shot actually ran against. The session's pick can change
    -- later, so "before vs after" has to compare with what was really used.
    reference_shot_ids TEXT NOT NULL DEFAULT '[]',
    -- NULL = follow the session. Not 0: zero is a real value for this dial, so it
    -- cannot double as "unset" the way an empty seed does.
    reference_strength REAL,
    -- The take this row is a copy of, across cloned sessions: the id of the shot
    -- in the ORIGINAL session, so every copy of one take carries the same value
    -- and NULL means "this is the original". It is what pairs two photos for the
    -- comparison. The seed cannot do that job — reshooting (↺) rolls a new one on
    -- purpose, and the pair has to survive exactly that.
    origin_shot_id INTEGER,
    seed          INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending', -- pending|running|done|failed|cancelled
    prompt_id     TEXT NOT NULL DEFAULT '',
    filename      TEXT NOT NULL DEFAULT '',        -- relative to the session folder
    rating        INTEGER NOT NULL DEFAULT 0,      -- 0-5
    -- The three drawn components (camera, act, framing) as (concept, wording)
    -- pairs, JSON-encoded. A written shot leaves this at the empty default
    -- '{}', which is the marker 3.6 uses to tell a composed session from a
    -- written one. A future task (6.2) reads the wording off the row to
    -- know which cell to count the photo toward — the prose does not
    -- survive the round-trip, and a column on `shot` is the only home
    -- this change gives it.
    components    TEXT NOT NULL DEFAULT '{}',
    rejected      INTEGER NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL DEFAULT ''
);

-- The cell is the unit of evidence: a (camera_wording, act_wording,
-- framing_wording, manner, checkpoint) tuple holding the counts that 2.2
-- turns into a verdict. NOT NULL on the five keys is what makes a write
-- missing one a hard rejection; PRIMARY KEY is the row identity, not a
-- separate rule. See task 2.1 of the prompt-component-matrix change: the
-- rejection lives in the schema, not in a Python if. The trio is the unit
-- because a photograph is camera × act × framing — the 9 per-family
-- observations in kinds.js:1962-1986 are (act, family) measurements, and
-- the 4-column key that recorded them under a single (concept, wording)
-- pair was the wrong shape.
CREATE TABLE IF NOT EXISTS cell (
    camera_wording  TEXT NOT NULL,
    act_wording     TEXT NOT NULL,
    framing_wording TEXT NOT NULL,
    manner          TEXT NOT NULL,
    checkpoint      TEXT NOT NULL,
    judged          INTEGER NOT NULL DEFAULT 0,
    arrived         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (camera_wording, act_wording, framing_wording, manner, checkpoint),
    -- NOT NULL alone would let '' through, and '' is this schema's idiom for
    -- "not set" (every other TEXT column here is DEFAULT ''). A cell with
    -- an empty wording is one the cell has nothing to index on. The literal
    -- 'none' is a fact of the measurement (scripts/shoot_arrangements.py:63-77
    -- has no framing, and the act-only and camera-only seeds did not name
    -- the other two slots) and is the only value the synthetic keys take.
    CHECK (camera_wording <> '' AND act_wording <> '' AND framing_wording <> ''
           AND manner <> '' AND checkpoint <> ''),
    -- 2.2 reads a verdict off these two. More arrivals than judgements is not a
    -- state it can answer, so it never gets stored.
    CHECK (judged >= 0 AND arrived BETWEEN 0 AND judged)
);

CREATE INDEX IF NOT EXISTS ix_shot_session ON shot(session_id);
CREATE INDEX IF NOT EXISTS ix_session_model ON session(model_id);
"""

_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cell_state(judged: int, arrived: int) -> str:
    """The state a cell's two counts imply.

    Per the component-matrix spec:
    - `verified`: at least 10 photographs judged AND at least 8 arrived
      for every 10 judged (the ratio reading).
    - `dead`: at least 10 photographs judged AND below the 8-in-10 ratio.
    - `unknown`: fewer than 10 photographs judged (whatever the ratio).

    The two readings ("at least 8 arrived" absolute, vs "below 8 of 10"
    ratio) are identical at the n=10 boundary the spec names, and they
    diverge above it: 8 of 20 is 40%, not verified. The strict drawer
    (design.md:218) draws only verified, so the ratio reading is what
    feeds strict. Integer math, no float: `arrived * 10 >= judged * 8`.

    `arrived > judged` is unreachable through the cell table: its CHECK
    rejects the write at insert time, so a state derived from such a count
    is not a state the table can hold. The function does not branch for it
    - that would be testing a case the schema already forbids, and a
    "let me also defensively check" branch is what silently swallows a
    future loosening of the CHECK.
    """
    if judged < 10:
        return "unknown"
    if arrived * 10 >= judged * 8:
        return "verified"
    return "dead"


# Verdicts this project already paid GPU time for, carried into the cell
# table with the sample size, manner and checkpoint they were actually
# taken at (task 2.3 of the prompt-component-matrix change). Each row is
# the trio that produced the photograph, with the literal wording `none`
# in a slot the measurement did not break out. Five traps the sources
# set that the seed deliberately does not fall into:
#
#   1. `side` (0/9, 0/8) lands unknown, not dead: 9 and 8 are below the
#      n=10 the spec sets as the minimum for any verdict at all, so the
#      zero ratio is irrelevant. design.md:332-334 was wrong about side
#      and is now corrected; the spec rules.
#   2. `astride` seeds per family, not as its 18/22 aggregate. The four
#      per-family measurements (6/6, 4/4, 6/4, 6/4) all have n<10 and
#      land unknown. Seeding the aggregate instead of the split is the
#      failure that makes `astride` look measured when it is not. The
#      family lives in the `camera_wording` slot because that is the
#      dimension the measurement varied (the camera was rotated through
#      its families; kinds.js:1962-1986 records the family level), and
#      `framing_wording` is the literal `none` because the fixed line
#      in scripts/shoot_arrangements.py:63-77 does not name one — the
#      framing was absent from the measurement, not lost.
#   3. `astride` on the Krea 2 mix (12/9) is one cell without per-family
#      breakdown, because kinds.js:2014 does not split that run by
#      family. Inventing a split is the failure 2.4 catches. The
#      `camera_wording` is also `none` because the source does not
#      name a camera for that run, and `framing_wording` is `none` for
#      the same reason as the per-family rows. Under the ratio reading,
#      this cell is dead (9*10=90 < 12*8=96, 75% below the 80%
#      threshold) — the ratio decision kills the control on Krea 2 mix,
#      and that is the cost.
#   4. The `behind` ACT is not seeded. kinds.js:2035-2058 kills it with
#      four anecdotes (sessions 155, 161, 267, 268), not with a
#      (judged, arrived) pair. The 0/6 in the source is the CAMERA
#      `behind-direct` under candid, and that is what is seeded — as a
#      camera cell, with `act_wording` and `framing_wording` set to
#      `none` because the source is a camera measurement that did not
#      name the act or the framing. Manufacturing an n for the act
#      would be inventing a number.
#   5. `back` and `side` are seeded as the arrangement wordings they
#      were shot on, with `camera_wording` and `framing_wording` set
#      to `none` because the source (kinds.js:2021, "back 0 of 12 on
#      finepornV4, 0 of 12 on the Krea 2 mix") reports the act ×
#      checkpoint without breaking out camera or framing. The wording
#      remains `back` and `side` even though the catalogue no longer
#      carries them (2.4) — the verdict is real, and a future reshape
#      can either re-introduce them in ARRANGEMENTS or pull the seeds
#      out.
#
# The third astride measurement from kinds.js:1968-1969 ("12 of 12
# in sessions 265 and 266 on a different fixed line") is also not
# seeded: the source does not name the checkpoint, and inventing one
# would be the failure 2.4 is set up to catch. Documented here so
# the omission is not silent.
EVIDENCE_SEED: list[tuple[str, str, str, str, str, int, int]] = [
    # (camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived)
    # astride, per family, finepornV4 — all unknown (n<10).
    # The camera dimension is the family (kinds.js:1967-1968); framing was absent.
    ("front",    "astride", "none", "directed", "finepornV4", 6, 6),
    ("overhead", "astride", "none", "directed", "finepornV4", 4, 4),
    ("mirror",   "astride", "none", "directed", "finepornV4", 6, 4),
    ("pov",      "astride", "none", "directed", "finepornV4", 6, 4),
    # astride on Krea 2 mix, no per-family or per-camera breakdown - DEAD.
    # kinds.js:2014 does not split this run by family or camera.
    ("none", "astride", "none", "directed", "Krea 2 mix", 12, 9),
    # reverse, per family, finepornV4 — all unknown (n<10).
    ("shoulder", "reverse", "none", "directed", "finepornV4", 3, 3),
    ("mirror",   "reverse", "none", "directed", "finepornV4", 3, 1),
    ("overhead", "reverse", "none", "directed", "finepornV4", 3, 1),
    # wall, per family, finepornV4 — all unknown (n<10).
    ("mirror",   "wall", "none", "directed", "finepornV4", 3, 3),
    ("shoulder", "wall", "none", "directed", "finepornV4", 3, 0),
    # back, per checkpoint - dead (12 judged, 0 arrived).
    # Source: kinds.js:2021 — act × checkpoint, no camera or framing breakdown.
    ("none", "back", "none", "directed", "finepornV4", 12, 0),
    ("none", "back", "none", "directed", "Krea 2 mix", 12, 0),
    # side, per checkpoint - UNKNOWN (9 and 8 judged, below 10) per spec rules.
    ("none", "side", "none", "directed", "finepornV4", 9, 0),
    ("none", "side", "none", "directed", "Krea 2 mix", 8, 0),
    # camera: behind-direct under candid, 0/6 - unknown (6 judged).
    # Source: kinds.js:2056-2058 — camera measurement, no act or framing named.
    ("behind-direct", "none", "none", "candid", "finepornV4", 6, 0),
]


def seed_evidence(rows: list[tuple[str, str, str, str, str, int, int]] | None = None) -> int:
    """Insert evidence rows into the cell table. Returns the count of new
    rows inserted.

    The INSERT uses `ON CONFLICT DO NOTHING`, which only swallows the
    PRIMARY KEY conflict (the "already seeded" case). It does NOT swallow
    CHECK or NOT NULL violations: a row with an empty `''` for any of the
    five key columns, or `arrived > judged`, raises `sqlite3.IntegrityError`.
    A bare `except IntegrityError: pass` would let those disappear as if
    they were PK conflicts — exactly the failure the CHECK is set up to
    make noisy, and the one the test
    `test_seed_evidence_does_not_swallow_check_violations` pins.

    Also called from `_migrate` on first connect (when the cell table is
    empty), so the verdicts already paid for are in any real DB without
    an explicit startup step.
    """
    if rows is None:
        rows = EVIDENCE_SEED
    before = one("SELECT COUNT(*) AS n FROM cell")["n"]
    for camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived in rows:
        run("INSERT INTO cell (camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived)
    return one("SELECT COUNT(*) AS n FROM cell")["n"] - before


def connect(path: Path) -> sqlite3.Connection:
    global _conn
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA)
    _migrate(_conn)
    _conn.commit()
    return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema.

    Only renames and added columns so far, so `ALTER TABLE` covers it and the
    rows survive: a session already shot is someone's afternoon of GPU time.
    """
    def columns(table: str) -> set[str]:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}

    # look_index/look_label -> shot_index/shot_label: a "look" is the wardrobe,
    # which is now a property of the session; the rows are its shots.
    shot_cols = columns("shot")
    for old, new in (("look_index", "shot_index"), ("look_label", "shot_label")):
        if old in shot_cols and new not in shot_cols:
            conn.execute(f"ALTER TABLE shot RENAME COLUMN {old} TO {new}")

    if "look" not in columns("session"):
        conn.execute("ALTER TABLE session ADD COLUMN look TEXT NOT NULL DEFAULT ''")

    # The garments, split off the look. Nothing is back-filled: an older session
    # keeps its whole look in `look` and shoots exactly as it always did, which is
    # right — its takes were written against that one sentence.
    if "wardrobe" not in columns("session"):
        conn.execute("ALTER TABLE session ADD COLUMN wardrobe TEXT NOT NULL DEFAULT ''")

    # Reference sessions: a second workflow that edits an anchor photo, the anchors
    # it edits, and the per-shot flag saying which takes go through it.
    session_cols = columns("session")
    if "reference_workflow_id" not in session_cols:
        # No REFERENCES clause here: SQLite only accepts one on ADD COLUMN when the
        # default is NULL, and spelling it out would need a full table rebuild for
        # a constraint the routes already enforce.
        conn.execute("ALTER TABLE session ADD COLUMN reference_workflow_id INTEGER")
    if "anchor_shot_ids" not in session_cols:
        conn.execute("ALTER TABLE session ADD COLUMN anchor_shot_ids TEXT NOT NULL DEFAULT '[]'")
    shot_cols = columns("shot")
    if "use_reference" not in shot_cols:
        conn.execute("ALTER TABLE shot ADD COLUMN use_reference INTEGER NOT NULL DEFAULT 0")
    if "reference_shot_ids" not in shot_cols:
        conn.execute("ALTER TABLE shot ADD COLUMN reference_shot_ids TEXT NOT NULL DEFAULT '[]'")
    if "reference_strength" not in shot_cols:
        conn.execute("ALTER TABLE shot ADD COLUMN reference_strength REAL")
    # Nothing is back-filled: the copies made before this column existed are
    # paired by their seed instead, which is what they were paired by all along.
    if "origin_shot_id" not in shot_cols:
        conn.execute("ALTER TABLE shot ADD COLUMN origin_shot_id INTEGER")

    # Session kinds: the tag that says which job a graph does, so picking a kind
    # picks the graph. Untagged is a valid state, not a migration to back-fill.
    if "kind" not in columns("workflow"):
        conn.execute("ALTER TABLE workflow ADD COLUMN kind TEXT NOT NULL DEFAULT ''")

    # Free-text tags on a session: a list the user builds, a column that didn't
    # exist before, default '[]' so a session with no tags reads as an empty list
    # and not a NULL the route has to remember to handle.
    if "tags" not in columns("session"):
        conn.execute("ALTER TABLE session ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")

    # The three drawn components (camera, act, framing) on a composed shot.
    # A written shot leaves the column at the empty default '{}', and 3.6 uses
    # that empty default to tell a composed session from a written one. The
    # pattern matches `tags` and `kind` above: a TEXT default that survives
    # the round-trip through the row, decoded with `db.jload` at read time.
    if "components" not in columns("shot"):
        conn.execute("ALTER TABLE shot ADD COLUMN components TEXT NOT NULL DEFAULT '{}'")

    # The verdicts already paid for. An empty cell table is a database that
    # has never carried the seed, not one someone emptied on purpose, so the
    # only safe move is to fill it. `seed_evidence` is idempotent (ON
    # CONFLICT DO NOTHING) so a re-run is a no-op and the guard is belt-
    # and-braces rather than load-bearing.
    #
    # The trio is the cell (design.md decision C, spec.md: "A cell is
    # identified by the trio, manner and checkpoint"). The earlier 4-column
    # shape (`concept, wording, manner, checkpoint`) recorded the 9
    # per-family verdicts of kinds.js:1962-1986 as a single concept with a
    # synthetic 'astride-front' wording — a 2-component observation stuffed
    # into a 1-component cell. The new shape is 5 columns: the trio plus
    # manner and checkpoint, with the literal `none` in any slot the
    # measurement did not break out.
    #
    # The migration is destructive: SQLite's `ALTER TABLE` cannot change a
    # PRIMARY KEY in place, and the (concept, wording) -> trio conversion is
    # rule-based (the family lives in `camera_wording` for the 9 family
    # rows, in `act_wording` for the act-only rows, the candid
    # behind-direct row is the only camera-only row) and is captured by
    # re-seeding from `EVIDENCE_SEED` rather than by a per-row translation.
    # 6.2 in this same change populates the cell table with human
    # judgements, so the destructive migration only runs when the old
    # table is empty or holds exactly the seed rows; any other content
    # means a human wrote rows the 4-column shape cannot represent and
    # the migration has to translate them by hand. A noisy failure is
    # information; a silent loss is not.
    cell_cols = columns("cell")
    if cell_cols and "concept" in cell_cols:
        n_rows = conn.execute("SELECT COUNT(*) FROM cell").fetchone()[0]
        if n_rows != 0 and n_rows != len(EVIDENCE_SEED):
            raise RuntimeError(
                f"cell table is in the old 4-column shape and holds "
                f"{n_rows} rows ({len(EVIDENCE_SEED)} would be the seed "
                f"alone). The trio migration is destructive and the rows "
                f"need to be translated by hand: the old "
                f"(concept, wording) shape does not map cleanly to the "
                f"trio. Drain or translate the table before re-running "
                f"the migration."
            )
        conn.execute("DROP TABLE cell")
        # Re-run the full SCHEMA: the cell part recreates the table in the
        # new shape; the other parts are no-ops on their IF NOT EXISTS
        # clauses. SCHEMA is the single source of truth for the cell
        # DDL — the previous hand copy here had already lost the CHECK
        # comments and was drifting.
        conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM cell").fetchone()[0] == 0:
        seed_evidence()


def conn() -> sqlite3.Connection:
    if _conn is None:
        raise RuntimeError("db.connect() not called")
    return _conn


def q(sql: str, *args) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, *args) -> dict | None:
    row = conn().execute(sql, args).fetchone()
    return dict(row) if row else None


def run(sql: str, *args) -> int:
    cur = conn().execute(sql, args)
    conn().commit()
    return cur.lastrowid


def jload(row: dict, *fields: str) -> dict:
    """Decode the JSON-as-TEXT columns of a row in place."""
    for f in fields:
        if isinstance(row.get(f), str):
            try:
                row[f] = json.loads(row[f])
            except json.JSONDecodeError:
                row[f] = {}
    return row
