from pathlib import Path

import db


def test_the_origin_backfill_runs_on_a_database_that_predates_the_column(tmp_path):
    """The 3.6 back-fill, exercised on a database that does not
    have the column yet - the only shape where it runs.

    Every other test in this suite starts from a fresh database,
    where `SCHEMA` creates `session.origin` and `_migrate` skips
    the back-fill entirely. That is why the back-fill shipped
    with `conn.execute(sql, value, sid)` on the RAW sqlite3
    connection: `db.run` takes varargs, `sqlite3.Connection.execute`
    takes a params tuple, and the mistake is invisible until the
    branch actually runs. On a real upgrade it raised
    `TypeError: execute expected at most 2 arguments, got 3`
    inside `connect()`, so any existing database with at least
    one shot failed to open at all.

    The test drops the column from a fresh database, plants the
    four shapes the back-fill has to tell apart, and reopens: the
    written-only session reads `written`, the composed-only one
    `composed`, the session carrying both `mixed`, and the
    session with no shots keeps the empty default that means
    "draft".
    """
    p = Path(tmp_path) / "old.db"
    conn = db.connect(p)
    # Simulate a database written before 3.6: drop the column, then
    # plant three sessions (written-only, composed-only, mixed) and
    # one draft with no shots.
    conn.execute("ALTER TABLE session DROP COLUMN origin")
    conn.execute("INSERT INTO model (name, trigger, created_at) VALUES ('m','t','now')")
    mid = conn.execute("SELECT id FROM model").fetchone()["id"]
    sids = {}
    for name, comps in (("written", ["{}", "{}"]),
                        ("composed", ['{"camera": {"concept": "c", "wording": "c"}}']),
                        ("mixed", ["{}", '{"camera": {"concept": "c", "wording": "c"}}']),
                        ("draft", [])):
        cur = conn.execute(
            "INSERT INTO session (model_id, name, created_at) VALUES (?,?,'now')", (mid, name))
        sid = cur.lastrowid
        sids[name] = sid
        for c in comps:
            conn.execute(
                "INSERT INTO shot (session_id, prompt, components, created_at) VALUES (?,?,?,'now')",
                (sid, "line", c))
    conn.commit()
    conn.close()

    conn2 = db.connect(p)
    got = {n: conn2.execute("SELECT origin FROM session WHERE id=?", (s,)).fetchone()["origin"]
           for n, s in sids.items()}
    assert got == {"written": "written", "composed": "composed",
                   "mixed": "mixed", "draft": ""}, got


def test_reading_table_created_on_migration_of_older_database(tmp_path):
    """Task 1.1: opening a database created before the reading table
    finds the reading table created empty, with partial unique indexes,
    and sessions and shots untouched.
    """
    p = Path(tmp_path) / "pre_reading.db"
    conn = db.connect(p)
    # Plant a session and shot
    conn.execute("INSERT INTO model (name, trigger, created_at) VALUES ('m', 't', 'now')")
    mid = conn.execute("SELECT id FROM model").fetchone()["id"]
    conn.execute(
        "INSERT INTO session (model_id, name, manner, checkpoint, created_at) VALUES (?, 'sess', 'directed', 'ckpt', 'now')",
        (mid,),
    )
    sid = conn.execute("SELECT id FROM session").fetchone()["id"]
    conn.execute(
        "INSERT INTO shot (session_id, prompt, components, created_at) VALUES (?, 'a prompt', '{}', 'now')",
        (sid,),
    )
    # Simulate older DB without reading table
    conn.execute("DROP TABLE reading")
    conn.commit()
    conn.close()

    # Re-open through db.connect (which runs SCHEMA and _migrate)
    conn2 = db.connect(p)

    # reading table exists and is empty
    count = conn2.execute("SELECT COUNT(*) AS c FROM reading").fetchone()["c"]
    assert count == 0

    # Sessions and shots are intact
    sess = conn2.execute("SELECT name, manner, checkpoint FROM session WHERE id=?", (sid,)).fetchone()
    assert sess["name"] == "sess"
    assert sess["manner"] == "directed"
    assert sess["checkpoint"] == "ckpt"

    shot = conn2.execute("SELECT prompt FROM shot WHERE session_id=?", (sid,)).fetchone()
    assert shot["prompt"] == "a prompt"

    # Partial unique indexes exist
    indexes = {r["name"] for r in conn2.execute("PRAGMA index_list(reading)").fetchall()}
    assert "reading_base" in indexes
    assert "reading_session" in indexes

