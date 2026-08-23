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
    rejected      INTEGER NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    finished_at   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_shot_session ON shot(session_id);
CREATE INDEX IF NOT EXISTS ix_session_model ON session(model_id);
"""

_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
