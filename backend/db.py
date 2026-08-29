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
    -- The session's manner and checkpoint: the two non-trio dimensions the
    -- cell table is keyed on (design.md decision C). Manner is the camera
    -- list the session draws from (directed/candid/selfie, matching
    -- POSITIONS in kinds.js); checkpoint is the base model the workflow
    -- loads. Strict mode (3.2) checks the cell for (trio, manner,
    -- checkpoint) and refuses a draw whose cell is not verified, so a
    -- session without these is refused on a strict compose — a free
    -- compose (the 3.1 path) is unaffected. Empty default = an older
    -- session that predates 3.2, kept unverified rather than guessed.
    manner        TEXT NOT NULL DEFAULT '',
    checkpoint    TEXT NOT NULL DEFAULT '',
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
    -- The judging screen's verdict per slot. JSON of
    -- {camera: "wording" | "" | null, act: ..., framing: ...}: a non-null
    -- value means the judge answered that slot (a catalogue key is a
    -- match, "" is "none or cannot tell" per the spec), null means the
    -- question was not asked on this pass. The empty default '' means
    -- the shot has not been judged — 6.2's idempotence marker. A
    -- re-judge on a non-empty value is refused at 409 rather than
    -- silently double-counted (the cell's CHECK would surface a
    -- double-increment as IntegrityError, but the column check is the
    -- upstream gate the user sees).
    verdicts      TEXT NOT NULL DEFAULT '',
    rejected      INTEGER NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS component (
    id          INTEGER PRIMARY KEY,
    concept_key TEXT NOT NULL,
    slot        TEXT NOT NULL,
    manner      TEXT NOT NULL,
    family      TEXT NOT NULL DEFAULT '',
    faces       TEXT NOT NULL DEFAULT '',
    wording     TEXT NOT NULL,
    judge_label TEXT NOT NULL,
    -- An `act` component's compatible camera FAMILIES, comma-separated,
    -- strongest first. Empty for the other slots and for an act nobody has
    -- measured yet. This is the list `fitCameras` walks: an arrangement handed
    -- a camera that cannot see it renders as a different arrangement, measured
    -- session 267, so the planted photographs take their camera from here.
    --
    -- It is a column and not an if-chain in the frontend keyed on `family`,
    -- which is what it was first: that spelling gave every act added through
    -- the catalogue screen an empty list, so the one thing the screen exists
    -- for produced acts the camera plan silently ignored.
    cameras     TEXT NOT NULL DEFAULT '',
    retired_at  TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(slot, manner, wording),
    CHECK (slot IN ('camera', 'act', 'framing')
           AND wording <> ''
           AND judge_label <> ''
           AND judge_label <> wording)
);

CREATE INDEX IF NOT EXISTS ix_component_slot_manner ON component(slot, manner);

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
    contradicted    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (camera_wording, act_wording, framing_wording, manner, checkpoint),
    -- NOT NULL alone would let '' through, and '' is this schema's idiom for
    -- "not set" (every other TEXT column here is DEFAULT ''). A cell with
    -- an empty wording is one the cell has nothing to index on. The literal
    -- 'none' is a fact of the measurement (scripts/shoot_arrangements.py:63-77
    -- has no framing, and the act-only and camera-only seeds did not name
    -- the other two slots) and is the only value the synthetic keys take.
    CHECK (camera_wording <> '' AND act_wording <> '' AND framing_wording <> ''
           AND manner <> '' AND checkpoint <> ''),
    -- 2.2 reads a verdict off these. More arrivals + contradictions than judgements is not a
    -- state it can answer, so it never gets stored.
    CHECK (judged >= 0 AND arrived >= 0 AND contradicted >= 0
           AND arrived + contradicted <= judged)
);

CREATE TABLE IF NOT EXISTS reading (
    id         INTEGER PRIMARY KEY,
    slot       TEXT NOT NULL CHECK (slot IN ('camera', 'act', 'framing')),
    manner     TEXT NOT NULL,
    session_id INTEGER REFERENCES session(id) ON DELETE CASCADE,  -- NULL = base
    key        TEXT NOT NULL,
    label      TEXT NOT NULL CHECK (length(trim(label)) > 0),
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS reading_base ON reading (slot, manner, key)
    WHERE session_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS reading_session ON reading (slot, manner, session_id, key)
    WHERE session_id IS NOT NULL;

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


def connect(path: Path) -> sqlite3.Connection:
    global _conn
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA foreign_keys=ON")
    _conn.executescript(SCHEMA)
    _migrate(_conn, path.parent)
    _conn.commit()
    return _conn


def _migrate(conn: sqlite3.Connection, db_dir: Path | None = None) -> None:
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

    # The judging screen's verdicts per slot. Same idiom as `components`:
    # a TEXT JSON column, the empty default '' means "not yet judged",
    # 6.2 reads it to enforce idempotence (a non-empty value means a
    # judge already answered, the second call is a 409) and to compute
    # the per-slot (judged, arrived) delta the cell update carries.
    # A separate `shot_verdict` table would be one column's worth of
    # data, and the row already carries the matching input (the trio
    # in `components`) — the verdicts live next to what they answer.
    if "verdicts" not in columns("shot"):
        conn.execute("ALTER TABLE shot ADD COLUMN verdicts TEXT NOT NULL DEFAULT ''")

    # The session's manner and checkpoint: the two non-trio dimensions the
    # cell table is keyed on. Strict mode (3.2) reads them off the row to
    # check the cell for (trio, manner, checkpoint). Empty default for
    # older sessions, which is the right migration answer for "we don't
    # know what manner or checkpoint this session was shot under" - the
    # alternative (guessing from the model or workflow) is the failure
    # mode this default avoids. The default also keeps the column CHECK
    # honest: a session that never set manner or checkpoint reads as
    # 'unknown' and a strict compose on it fails loudly.
    session_cols = columns("session")
    if "manner" not in session_cols:
        conn.execute("ALTER TABLE session ADD COLUMN manner TEXT NOT NULL DEFAULT ''")
    if "checkpoint" not in session_cols:
        conn.execute("ALTER TABLE session ADD COLUMN checkpoint TEXT NOT NULL DEFAULT ''")

    # The session's origin: written, composed, or mixed. 3.6's spec
    # scenario "a later comparison can tell which produced which
    # photographs" needs this recorded on the session, not derived
    # from its shots: a draft with zero shots has no answer to
    # derive, and a session that carries both kinds of rows (3.4
    # contemplates this) is information a per-shot scan would
    # collapse. The empty default is the same idiom as manner and
    # checkpoint: a brand-new session has no shots yet, and the
    # first shot's write is what stamps the column. Older sessions
    # are back-filled from the shot table below - unlike manner
    # and checkpoint, the shot table IS a source of truth here
    # (3.1 already wrote components to every composed shot), so
    # the derivation is not a guess. A session with at least one
    # shot gets 'written', 'composed', or 'mixed' from the
    # components JSONs on its rows; a session with zero shots
    # keeps the empty default, which the routes read as
    # "draft, no shots yet".
    if "origin" not in session_cols:
        conn.execute("ALTER TABLE session ADD COLUMN origin TEXT NOT NULL DEFAULT ''")
        # Back-fill: read every shot's components once, bucket per
        # session in Python, write the bucket value. The JSON
        # column needs jload, and a five-line Python scan is
        # clearer than a SQL CASE that has to inspect JSON-as-
        # TEXT. Re-runs of `_migrate` skip the back-fill because
        # the column check above fails the second time around.
        per_session: dict[int, set[str]] = {}
        for row in conn.execute("SELECT session_id, components FROM shot").fetchall():
            per_session.setdefault(row["session_id"], set()).add(row["components"] or "")
        for sid, comp_set in per_session.items():
            has_written = "{}" in comp_set
            has_composed = any(c != "{}" for c in comp_set)
            if has_written and has_composed:
                value = "mixed"
            elif has_composed:
                value = "composed"
            else:
                value = "written"
            # Params as a tuple: this is the raw sqlite3 connection, not
            # `db.run`, and sqlite3.Connection.execute takes (sql, params).
            conn.execute("UPDATE session SET origin=? WHERE id=?", (value, sid))

    # A component store written before the act's camera families became a
    # column: add it empty. An act with no list is one no camera plan will
    # move, which is the same thing the frontend's if-chain did for every act
    # it did not recognise — so an un-backfilled row is no worse off than it
    # was, and the seed carries the measured lists for the three that have one.
    component_cols = columns("component")
    if component_cols and "cameras" not in component_cols:
        conn.execute("ALTER TABLE component ADD COLUMN cameras TEXT NOT NULL DEFAULT ''")

    # Cell table migration: add contradicted column if not present.
    # If an older database holds rows, dump them to cell-backup-<timestamp>.json
    # before recreating the table in the new shape. The cell table starts empty.
    cell_cols = columns("cell")
    if cell_cols and "contradicted" not in cell_cols:
        existing_rows = [dict(r) for r in conn.execute("SELECT * FROM cell").fetchall()]
        if existing_rows:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            target_dir = db_dir if db_dir is not None else Path("data")
            target_dir.mkdir(parents=True, exist_ok=True)
            backup_file = target_dir / f"cell-backup-{ts}.json"
            backup_file.write_text(json.dumps(existing_rows, indent=2), encoding="utf-8")
        conn.execute("DROP TABLE cell")
        conn.executescript(SCHEMA)

    # Reading table: the vocabulary a judging pass offers. Created on migration
    # if not present; starts empty.
    reading_cols = columns("reading")
    if not reading_cols:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS reading (
            id         INTEGER PRIMARY KEY,
            slot       TEXT NOT NULL CHECK (slot IN ('camera', 'act', 'framing')),
            manner     TEXT NOT NULL,
            session_id INTEGER REFERENCES session(id) ON DELETE CASCADE,
            key        TEXT NOT NULL,
            label      TEXT NOT NULL CHECK (length(trim(label)) > 0),
            created_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS reading_base ON reading (slot, manner, key)
            WHERE session_id IS NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS reading_session ON reading (slot, manner, session_id, key)
            WHERE session_id IS NOT NULL;
        """)


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
