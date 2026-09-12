import sqlite3
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



def test_the_needs_backfill_reads_the_second_person_out_of_the_wording(tmp_path):
    """The `needs` back-fill, on a database written before an act could say what
    it needs — the only shape where it runs.

    Nobody types this column for the rows that already exist: the second person
    was always in the wording (`two people in frame`, `astride him`), and asking
    an operator to say it a second time is asking two spellings of one fact to
    disagree on day one. So the migration reads the text.

    The four shapes that matter: an act naming him, an act naming two people,
    an act that is pure geometry, and a CAMERA whose wording happens to say
    `he` — a camera is not staged and must come back empty whatever it says.
    """
    p = Path(tmp_path) / "old-acts.db"
    conn = db.connect(p)
    conn.execute("ALTER TABLE component DROP COLUMN needs")
    rows = [
        ("astride", "act", "She is astride him with her weight down on him, two people in frame.", "him"),
        ("joined", "act", "They are joined, two people in frame.", "him"),
        ("upright", "act", "One young woman stands upright and square to the camera.", ""),
        ("over-his-shoulder", "camera", "Taken over his shoulder as he stands behind her", ""),
    ]
    for key, slot, wording, _ in rows:
        conn.execute(
            "INSERT INTO component (concept_key, slot, manner, wording, judge_label, created_at) "
            "VALUES (?, ?, 'candid', ?, ?, 'now')", (key, slot, wording, f"label {key}"))
    conn.commit()
    conn.close()

    conn = db.connect(p)
    got = {r["concept_key"]: r["needs"]
           for r in conn.execute("SELECT concept_key, needs FROM component")}
    conn.close()
    assert got == {key: expected for key, _, _, expected in rows}, got


def test_a_session_written_before_the_room_key_reads_an_empty_key(tmp_path):
    """A database written before 7.1's column, reopened.

    The column records which room filled the session's look. Nothing is
    back-filled: by storage time the look is one block of text, and matching it
    against the catalogue to decide which room wrote it would invent provenance
    for a look somebody typed by hand. So the assertion is that an older
    session reads EMPTY - not that it reads something plausible.

    The look is asserted alongside, byte for byte, because the failure worth
    catching is not a missing column (the next query would raise) but a
    migration that adds the column by rebuilding the table and loses or
    rewrites the text on the way.
    """
    p = Path(tmp_path) / "old-rooms.db"
    look = "A bare ceiling bulb lights the room from overhead."
    conn = db.connect(p)
    conn.execute("ALTER TABLE session DROP COLUMN room_key")
    conn.execute("INSERT INTO model (name, trigger, created_at) VALUES ('m','t','now')")
    mid = conn.execute("SELECT id FROM model").fetchone()["id"]
    sid = conn.execute(
        "INSERT INTO session (model_id, name, look, created_at) VALUES (?,?,?,'now')",
        (mid, "before the column", look),
    ).lastrowid
    conn.commit()
    conn.close()

    conn = db.connect(p)
    row = conn.execute("SELECT room_key, look FROM session WHERE id=?", (sid,)).fetchone()
    conn.close()
    assert row["room_key"] == ""
    assert row["look"] == look

    # And the other half of "in SCHEMA and in _migrate together". Everything
    # above passes on a column that lives only in `_migrate`, because
    # `db.connect` runs both and an upgrade path that adds the column is
    # indistinguishable from a schema that declares it. `session.origin` is
    # already in the tree that way. So `SCHEMA` is asked on its own, with no
    # migration behind it.
    fresh = sqlite3.connect(":memory:")
    fresh.executescript(db.SCHEMA)
    cols = {r[1] for r in fresh.execute("PRAGMA table_info(session)")}
    fresh.close()
    assert "room_key" in cols, sorted(cols)


def test_resource_selection_tables_created_on_migration_of_older_database(tmp_path):
    """Task 1.1: opening an older database created before resource_selection
    finds resource_selection and resource_selection_file created empty,
    with existing models, sessions, and shots byte-for-byte intact.
    """
    p = Path(tmp_path) / "pre_resource_selection.db"
    conn = db.connect(p)
    # Seed model, session, shot
    conn.execute("INSERT INTO model (name, trigger, created_at) VALUES ('char_a', 'trigger_a', 'now')")
    mid = conn.execute("SELECT id FROM model").fetchone()["id"]
    sess_look = "A sunny loft room with soft morning light"
    conn.execute(
        "INSERT INTO session (model_id, name, look, wardrobe, created_at) VALUES (?, 'session_1', ?, 'linen dress', 'now')",
        (mid, sess_look),
    )
    sid = conn.execute("SELECT id FROM session").fetchone()["id"]
    shot_prompt = "sitting at the kitchen counter reading a book"
    conn.execute(
        "INSERT INTO shot (session_id, prompt, created_at) VALUES (?, ?, 'now')",
        (sid, shot_prompt),
    )
    # Simulate older DB without the new selection tables
    conn.execute("DROP TABLE IF EXISTS resource_selection_file")
    conn.execute("DROP TABLE IF EXISTS resource_selection")
    conn.commit()
    conn.close()

    # Re-open through db.connect (which executes SCHEMA and _migrate)
    conn2 = db.connect(p)
    tables = {r["name"] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "resource_selection" in tables
    assert "resource_selection_file" in tables

    # Tables start empty
    c_sel = conn2.execute("SELECT COUNT(*) AS c FROM resource_selection").fetchone()["c"]
    c_files = conn2.execute("SELECT COUNT(*) AS c FROM resource_selection_file").fetchone()["c"]
    assert c_sel == 0
    assert c_files == 0

    # Existing data is byte-for-byte intact
    s_row = conn2.execute("SELECT name, look, wardrobe FROM session WHERE id=?", (sid,)).fetchone()
    assert s_row["name"] == "session_1"
    assert s_row["look"] == sess_look
    assert s_row["wardrobe"] == "linen dress"

    shot_row = conn2.execute("SELECT prompt FROM shot WHERE session_id=?", (sid,)).fetchone()
    assert shot_row["prompt"] == shot_prompt

    conn2.close()

    # Verify SCHEMA alone declares the tables
    mem_conn = sqlite3.connect(":memory:")
    mem_conn.executescript(db.SCHEMA)
    mem_tables = {r[0] for r in mem_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    mem_conn.close()
    assert "resource_selection" in mem_tables
    assert "resource_selection_file" in mem_tables
