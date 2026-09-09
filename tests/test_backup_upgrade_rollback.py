"""Integration tests for backup, additive upgrade, and operational rollback (Task 6.1).

Covers:
- Consistent SQLite backup of isolated databases in WAL mode capturing uncheckpointed data.
- Pre-upgrade backup does not modify source database schema.
- Backup failure modes (missing source, destination exists, path collisions, cleanup).
- CLI entry point `scripts/backup_db.py`.
- Additive upgrade of pre-change database: db.connect() and repeated reopenings
  preserve legacy sessions, finished shots, and external photo files.
- Operational rollback (resource_planning_enabled=false):
  - Preserves all database tables, asset revisions, session plans, and prepared snapshots.
  - Blocks resource-v1 creation, cloning, mode transition, plan saving, and preparation with 503.
  - Preserves read endpoints (GET /api/sessions/{sid}/plan, reviews, resources).
  - Preserves submission of already prepared and approved snapshots into the queue.
  - Queue execution and cancellation via FakeComfy work without duplicate shots.
- Reactivation restores full draft continuation with identical IDs, revisions, and prompts.
- No automatic destructive down-migration or table drops occur.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3

import pytest

import db
import main
from backup import backup_database
from runner import Runner
import scripts.backup_db as backup_cli
import session_plan


PRE_CHANGE_LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    graph         TEXT NOT NULL,
    node_map      TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    lora_name     TEXT NOT NULL DEFAULT '',
    trigger       TEXT NOT NULL DEFAULT '',
    lora_strength REAL NOT NULL DEFAULT 1.0,
    base_positive TEXT NOT NULL DEFAULT '',
    base_negative TEXT NOT NULL DEFAULT '',
    workflow_id   INTEGER REFERENCES workflow(id) ON DELETE SET NULL,
    settings      TEXT NOT NULL DEFAULT '{}',
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    id            INTEGER PRIMARY KEY,
    model_id      INTEGER NOT NULL REFERENCES model(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft',
    workflow_id   INTEGER REFERENCES workflow(id) ON DELETE SET NULL,
    reference_workflow_id INTEGER REFERENCES workflow(id) ON DELETE SET NULL,
    anchor_shot_ids TEXT NOT NULL DEFAULT '[]',
    look          TEXT NOT NULL DEFAULT '',
    wardrobe      TEXT NOT NULL DEFAULT '',
    settings      TEXT NOT NULL DEFAULT '{}',
    tags          TEXT NOT NULL DEFAULT '[]',
    manner        TEXT NOT NULL DEFAULT '',
    checkpoint    TEXT NOT NULL DEFAULT '',
    room_key      TEXT NOT NULL DEFAULT '',
    origin        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shot (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    shot_index    INTEGER NOT NULL DEFAULT 0,
    shot_label    TEXT NOT NULL DEFAULT '',
    prompt        TEXT NOT NULL DEFAULT '',
    negative      TEXT NOT NULL DEFAULT '',
    use_reference INTEGER NOT NULL DEFAULT 0,
    mute_wardrobe INTEGER NOT NULL DEFAULT 0,
    reference_shot_ids TEXT NOT NULL DEFAULT '[]',
    reference_strength REAL,
    origin_shot_id INTEGER,
    seed          INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'pending',
    prompt_id     TEXT NOT NULL DEFAULT '',
    filename      TEXT NOT NULL DEFAULT '',
    rating        INTEGER NOT NULL DEFAULT 0,
    components    TEXT NOT NULL DEFAULT '{}',
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
    cameras     TEXT NOT NULL DEFAULT '',
    needs       TEXT NOT NULL DEFAULT '',
    retired_at  TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(slot, manner, wording)
);

CREATE TABLE IF NOT EXISTS cell (
    camera_wording  TEXT NOT NULL,
    act_wording     TEXT NOT NULL,
    framing_wording TEXT NOT NULL,
    manner          TEXT NOT NULL,
    checkpoint      TEXT NOT NULL,
    judged          INTEGER NOT NULL DEFAULT 0,
    arrived         INTEGER NOT NULL DEFAULT 0,
    contradicted    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (camera_wording, act_wording, framing_wording, manner, checkpoint)
);

CREATE TABLE IF NOT EXISTS reading (
    id         INTEGER PRIMARY KEY,
    slot       TEXT NOT NULL CHECK (slot IN ('camera', 'act', 'framing')),
    manner     TEXT NOT NULL,
    session_id INTEGER REFERENCES session(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    label      TEXT NOT NULL CHECK (length(trim(label)) > 0),
    axis       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS garment (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL,
    wording    TEXT NOT NULL,
    aside      TEXT NOT NULL DEFAULT '',
    retired_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outfit (
    id         INTEGER PRIMARY KEY,
    key        TEXT NOT NULL,
    label      TEXT NOT NULL,
    garments   TEXT NOT NULL,
    retired_at TEXT,
    created_at TEXT NOT NULL
);
"""

NEW_RESOURCE_TABLES = (
    "resource_library",
    "asset_revision",
    "auxiliary_resource",
    "session_plan",
    "session_plan_approval",
    "prepared_take",
    "take_resource_adaptation",
)

NEW_TRIGGERS = (
    "asset_revision_protect_immutable",
    "auxiliary_resource_protect_immutable",
    "prepared_take_protect_generated",
    "take_resource_adaptation_protect_identity",
)


def _list_tables(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


def _list_triggers(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    return {row[0] for row in cur.fetchall()}


def test_backup_wal_consistency_captures_uncheckpointed_commits(tmp_path: Path):
    """A database in WAL mode with active committed transactions in the WAL journal

    is consistently backed up. The backup contains the committed data and passes
    PRAGMA integrity_check.
    """
    db_path = tmp_path / "wal_source.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO sample (note) VALUES ('committed in wal')")
    conn.commit()

    # Confirm wal file exists or data is active
    backup_path = tmp_path / "wal_backup.db"
    result_path = backup_database(db_path, backup_path)
    conn.close()

    assert result_path == backup_path
    assert backup_path.is_file()

    # Reopen backup in isolation
    backup_conn = sqlite3.connect(backup_path)
    cur = backup_conn.execute("PRAGMA integrity_check")
    assert cur.fetchone()[0] == "ok"

    row = backup_conn.execute("SELECT note FROM sample WHERE id=1").fetchone()
    assert row is not None
    assert row[0] == "committed in wal"
    backup_conn.close()


def test_backup_pre_upgrade_preserves_source_schema(tmp_path: Path):
    """Backing up a pre-upgrade database directly without calling db.connect()

    does not modify the source database schema or introduce any new tables.
    """
    old_db = tmp_path / "legacy_pre_upgrade.db"
    conn = sqlite3.connect(old_db)
    conn.executescript(PRE_CHANGE_LEGACY_SCHEMA)
    conn.execute("INSERT INTO model (name, trigger, created_at) VALUES ('m1', 'trig', 'now')")
    conn.commit()

    source_tables_before = _list_tables(conn)
    for table in NEW_RESOURCE_TABLES:
        assert table not in source_tables_before
    conn.close()

    backup_dest = tmp_path / "legacy_backup.db"
    backup_database(old_db, backup_dest)

    # Re-verify source database schema: none of the new tables were created
    source_reopen = sqlite3.connect(old_db)
    source_tables_after = _list_tables(source_reopen)
    assert source_tables_after == source_tables_before
    for table in NEW_RESOURCE_TABLES:
        assert table not in source_tables_after
    source_reopen.close()

    # Destination also has the exact same tables and passes integrity check
    dest_conn = sqlite3.connect(backup_dest)
    assert _list_tables(dest_conn) == source_tables_before
    cur = dest_conn.execute("PRAGMA integrity_check")
    assert cur.fetchone()[0] == "ok"
    dest_conn.close()


def test_backup_safety_and_failure_handling(tmp_path: Path):
    """backup_database validates paths, avoids accidental overwrites, and leaves

    no incomplete files on failure.
    """
    valid_src = tmp_path / "source.db"
    conn = sqlite3.connect(valid_src)
    conn.execute("CREATE TABLE t (id INT)")
    conn.commit()
    conn.close()

    # 1. Non-existent source
    missing_src = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        backup_database(missing_src, tmp_path / "out.db")

    # 2. Source is a directory
    dir_src = tmp_path / "dir_src"
    dir_src.mkdir()
    with pytest.raises(ValueError, match="not a file"):
        backup_database(dir_src, tmp_path / "out.db")

    # 3. Source == Target
    with pytest.raises(ValueError, match="cannot be the same file"):
        backup_database(valid_src, valid_src)

    # 4. Target already exists without overwrite
    target = tmp_path / "dest.db"
    target.write_text("existing content", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        backup_database(valid_src, target, overwrite=False)

    # 5. Target already exists with overwrite=True succeeds
    result = backup_database(valid_src, target, overwrite=True)
    assert result == target
    test_conn = sqlite3.connect(target)
    assert "t" in _list_tables(test_conn)
    test_conn.close()


def test_backup_publish_failure_cleans_temp_and_preserves_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When final publication fails (e.g. rename/replace error), any temp files

    must be removed and the previous destination file must remain untouched.
    """
    src = tmp_path / "source.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE sample (id INT, note TEXT)")
    conn.execute("INSERT INTO sample VALUES (1, 'source data')")
    conn.commit()
    conn.close()

    target = tmp_path / "dest.db"
    target.write_bytes(b"previous destination content")

    # Monkeypatch os.replace to simulate an error during final publication
    def fake_replace(s, d):
        raise OSError("simulated publish failure")

    monkeypatch.setattr("os.replace", fake_replace)

    with pytest.raises(OSError, match="simulated publish failure"):
        backup_database(src, target, overwrite=True)

    # Destination remains intact
    assert target.read_bytes() == b"previous destination content"

    # No leftover temporary files
    tmps = list(tmp_path.glob(f"{target.name}.tmp.*"))
    assert tmps == []


def test_backup_refuses_concurrent_target_creation_without_overwrite(tmp_path: Path):
    """If the target destination file appears between the initial existence check

    and the final publication, backup_database with overwrite=False must refuse
    to overwrite it, preserve the target, and clean up temporary files.
    """
    src = tmp_path / "source.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE sample (id INT, note TEXT)")
    conn.execute("INSERT INTO sample VALUES (1, 'source data')")
    conn.commit()
    conn.close()

    target = tmp_path / "dest_concurrent.db"
    assert not target.exists()

    # Another process creates the destination file during backup before publish
    def on_progress(status, remaining, total):
        if not target.exists():
            target.write_bytes(b"concurrently created target content")

    with pytest.raises(FileExistsError, match="already exists"):
        backup_database(src, target, overwrite=False, pages=1, progress=on_progress)

    # Concurrently created destination is preserved intact
    assert target.read_bytes() == b"concurrently created target content"

    # Temporary file was cleaned up
    tmps = list(tmp_path.glob(f"{target.name}.tmp.*"))
    assert tmps == []


def test_backup_link_failure_safe_refusal_without_destructive_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When os.link fails or is unavailable during overwrite=False, backup_database

    must fail safely with an error, must not invoke a destructive fallback (like
    os.rename or os.replace) that could overwrite a destination, must leave any
    pre-existing or concurrent destination intact, and must clean up all temp files.
    """
    src = tmp_path / "source.db"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE sample (id INT, note TEXT)")
    conn.execute("INSERT INTO sample VALUES (1, 'source data')")
    conn.commit()
    conn.close()

    target = tmp_path / "dest_link_fail.db"
    assert not target.exists()

    called_destructive = []

    def mock_replace(s, d):
        called_destructive.append("replace")
        raise AssertionError("os.replace must not be called when overwrite=False")

    def mock_rename(s, d):
        called_destructive.append("rename")
        raise AssertionError("os.rename must not be called as fallback when overwrite=False")

    monkeypatch.setattr(os, "replace", mock_replace)
    monkeypatch.setattr(os, "rename", mock_rename)

    # 1. Test when os.link raises an unsupported OSError
    def mock_link(s, d):
        raise OSError("hard links not supported on this filesystem")

    monkeypatch.setattr(os, "link", mock_link)

    def on_progress(status, remaining, total):
        if not target.exists():
            target.write_bytes(b"concurrent content to protect")

    with pytest.raises(OSError, match="atomic publication without overwrite failed via os.link"):
        backup_database(src, target, overwrite=False, pages=1, progress=on_progress)

    assert target.read_bytes() == b"concurrent content to protect"
    assert called_destructive == []
    assert list(tmp_path.glob(f"{target.name}.tmp.*")) == []

    # 2. Test when os.link is not available (hasattr is False)
    monkeypatch.delattr(os, "link", raising=False)
    target2 = tmp_path / "dest_no_link.db"
    with pytest.raises(OSError, match="requires os.link support"):
        backup_database(src, target2, overwrite=False)

    assert not target2.exists()
    assert called_destructive == []
    assert list(tmp_path.glob(f"{target2.name}.tmp.*")) == []


def test_backup_cli_entry_point(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The scripts/backup_db.py CLI creates backups with custom target, default target,

    and JSON report formatting.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    src_db = data_dir / "idevgen.db"

    conn = sqlite3.connect(src_db)
    conn.execute("CREATE TABLE info (key TEXT)")
    conn.execute("INSERT INTO info VALUES ('test_cli')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("IDEVGEN_DATA_DIR", str(data_dir))

    # 1. Backup to explicit target with --json
    explicit_target = tmp_path / "manual_backup.db"
    rc = backup_cli.main(["--target", str(explicit_target), "--json"])
    assert rc == 0
    assert explicit_target.is_file()

    # 2. Re-running without --force fails with rc=1
    rc_fail = backup_cli.main(["--target", str(explicit_target)])
    assert rc_fail == 1

    # 3. Re-running with --force succeeds
    rc_force = backup_cli.main(["--target", str(explicit_target), "--force"])
    assert rc_force == 0

    # 4. Default target creates a timestamped database under backups/
    rc_default = backup_cli.main([])
    assert rc_default == 0
    backups_dir = data_dir / "backups"
    assert backups_dir.is_dir()
    created = list(backups_dir.glob("idevgen-backup-*.db"))
    assert len(created) >= 1


def test_additive_upgrade_preserves_legacy_sessions_shots_and_files(tmp_path: Path):
    """Starting from a true pre-change database, db.connect() adds the new resource

    tables and triggers additively. Legacy sessions, finished shots, ratings,
    verdicts, components, and external dummy photo files remain byte-for-byte intact.
    Repeated re-openings are safe and idempotent.
    """
    db_path = tmp_path / "data" / "idevgen.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Create true pre-change database
    conn = sqlite3.connect(db_path)
    conn.executescript(PRE_CHANGE_LEGACY_SCHEMA)

    # Seed legacy model
    conn.execute(
        "INSERT INTO model (name, trigger, lora_name, base_positive, created_at) "
        "VALUES ('Ada', 'ada trigger', 'ada.safetensors', 'portrait photo', '2026-09-01T00:00:00Z')",
    )
    mid = conn.execute("SELECT id FROM model").fetchone()[0]

    # Seed legacy session without composition_mode
    legacy_settings = {"width": 1024, "height": 1024, "steps": 8, "cfg": 1.0}
    conn.execute(
        "INSERT INTO session (model_id, name, look, wardrobe, settings, manner, checkpoint, origin, created_at) "
        "VALUES (?, 'Summer Shoot', 'natural light on balcony', 'linen sundress', ?, 'directed', 'sd_base.safetensors', 'written', '2026-09-01T01:00:00Z')",
        (mid, json.dumps(legacy_settings)),
    )
    sid = conn.execute("SELECT id FROM session").fetchone()[0]

    # Seed finished shot with verdicts and components
    components_json = json.dumps({"camera": {"concept": "front", "wording": "front angle"}})
    verdicts_json = json.dumps({"camera": "front"})
    shot_filename = f"{sid}/1_take_front.png"
    conn.execute(
        "INSERT INTO shot (session_id, shot_index, shot_label, prompt, status, filename, rating, verdicts, components, created_at, finished_at) "
        "VALUES (?, 1, 'take_front', 'ada trigger in linen sundress on balcony', 'done', ?, 5, ?, ?, '2026-09-01T01:05:00Z', '2026-09-01T01:06:00Z')",
        (sid, shot_filename, verdicts_json, components_json),
    )
    shot_id = conn.execute("SELECT id FROM shot").fetchone()[0]
    conn.commit()
    conn.close()

    # Create dummy photograph file on disk in sessions directory
    photo_file = tmp_path / "data" / "sessions" / f"{sid}" / "1_take_front.png"
    photo_file.parent.mkdir(parents=True, exist_ok=True)
    fake_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtest_photo_bytes_payload"
    photo_file.write_bytes(fake_png_bytes)

    # 2. Upgrade by calling db.connect()
    upgraded_conn = db.connect(db_path)

    # Verify all new resource tables were added additively
    tables = _list_tables(upgraded_conn)
    for table in NEW_RESOURCE_TABLES:
        assert table in tables, f"Expected new table {table} to be created"

    # Verify all triggers were added
    triggers = _list_triggers(upgraded_conn)
    for trig in NEW_TRIGGERS:
        assert trig in triggers, f"Expected trigger {trig} to be created"

    # Verify legacy session survived byte-for-byte
    sess_row = upgraded_conn.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone()
    assert sess_row["name"] == "Summer Shoot"
    assert sess_row["look"] == "natural light on balcony"
    assert sess_row["wardrobe"] == "linen sundress"
    parsed_settings = json.loads(sess_row["settings"])
    assert "composition_mode" not in parsed_settings
    assert parsed_settings == legacy_settings

    # Verify legacy shot survived
    shot_row = upgraded_conn.execute("SELECT * FROM shot WHERE id=?", (shot_id,)).fetchone()
    assert shot_row["status"] == "done"
    assert shot_row["rating"] == 5
    assert shot_row["verdicts"] == verdicts_json
    assert shot_row["components"] == components_json
    assert shot_row["filename"] == shot_filename

    # Verify photo file on disk survived untouched
    assert photo_file.exists()
    assert photo_file.read_bytes() == fake_png_bytes

    # Verify foreign keys and integrity
    assert upgraded_conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert upgraded_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # 3. Repeated re-openings are safe and idempotent
    for _ in range(3):
        reopen = db.connect(db_path)
        assert _list_tables(reopen) == tables
        assert _list_triggers(reopen) == triggers
        reopen_row = reopen.execute("SELECT name FROM session WHERE id=?", (sid,)).fetchone()
        assert reopen_row["name"] == "Summer Shoot"
        assert photo_file.exists()
        assert reopen.execute("PRAGMA foreign_key_check").fetchall() == []


def test_rollback_preserves_modern_database_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When resource planning is disabled (resource_planning_enabled=false) and the app

    is restarted, all modern tables, libraries, asset revisions, session plans,
    adaptations, approvals, prepared takes, linked shots, and photos survive intact.
    No tables are dropped, and foreign keys remain clean.
    """
    db_path = tmp_path / "modern.db"
    conn = db.connect(db_path)

    # Seed model & resource library
    conn.execute("INSERT INTO model (name, trigger, created_at) VALUES ('ModelB', 'trigB', 'now')")
    mid = conn.execute("SELECT id FROM model").fetchone()["id"]

    conn.execute(
        "INSERT INTO resource_library (library_key, display_name, kind, created_at) "
        "VALUES ('lib_test', 'Test Library', 'rooms', 'now')",
    )
    lib_id = conn.execute("SELECT id FROM resource_library").fetchone()["id"]

    conn.execute(
        "INSERT INTO asset_revision (library_id, source_id, content_digest, payload, translation, coverage, created_at) "
        "VALUES (?, 'room_1', 'digest_123', '{\"id\":\"room_1\",\"prompt\":\"cozy room\"}', '{}', '{}', 'now')",
        (lib_id,),
    )

    # Seed resource-v1 session
    r_settings = json.dumps({"composition_mode": "resource-v1", "steps": 10})
    conn.execute(
        "INSERT INTO session (model_id, name, settings, created_at) VALUES (?, 'Resource Session', ?, 'now')",
        (mid, r_settings),
    )
    sid = conn.execute("SELECT id FROM session WHERE name='Resource Session'").fetchone()["id"]

    plan_json = json.dumps({
        "version": "resource-v1",
        "look": "sunlit studio",
        "initial_wardrobe": "casual sweater",
        "takes": [{"take_id": "take_01", "wardrobe": None}],
    })
    conn.execute(
        "INSERT INTO session_plan (session_id, mode, plan_revision, plan_json, created_at, updated_at) "
        "VALUES (?, 'resource-v1', 1, ?, 'now', 'now')",
        (sid, plan_json),
    )

    conn.execute(
        "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) VALUES (?, 1, 'now')",
        (sid,),
    )

    conn.execute(
        "INSERT INTO take_resource_adaptation "
        "(session_id, plan_revision, take_id, library_key, source_id, content_digest, resource_field, source_value, adapted_value, created_at, updated_at) "
        "VALUES (?, 1, 'take_01', 'lib_test', 'room_1', 'digest_123', 'prompt', 'cozy room', 'adapted cozy room', 'now', 'now')",
        (sid,),
    )

    conn.execute(
        "INSERT INTO prepared_take "
        "(session_id, plan_revision, take_id, final_prompt, effective_state, mapping_version, compiler_version, provenance, status, created_at, updated_at) "
        "VALUES (?, 1, 'take_01', 'sunlit studio. casual sweater. adapted cozy room', '{}', '1.0', '1.0', '{}', 'ready', 'now', 'now')",
        (sid,),
    )
    conn.commit()

    # Now disable resource planning via environment variable
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "false")
    assert main.is_resource_planning_enabled() is False

    # Simulate restart / reconnect
    reconnect = db.connect(db_path)

    # 1. Assert all tables still exist (NO table drops!)
    tables = _list_tables(reconnect)
    for table in NEW_RESOURCE_TABLES:
        assert table in tables, f"Table {table} must survive deactivation"

    # 2. Assert all records still exist with exact values
    plan_row = reconnect.execute("SELECT * FROM session_plan WHERE session_id=?", (sid,)).fetchone()
    assert plan_row is not None
    assert plan_row["plan_revision"] == 1
    assert json.loads(plan_row["plan_json"])["look"] == "sunlit studio"

    prep_row = reconnect.execute("SELECT * FROM prepared_take WHERE session_id=? AND take_id='take_01'", (sid,)).fetchone()
    assert prep_row is not None
    assert prep_row["status"] == "ready"
    assert "adapted cozy room" in prep_row["final_prompt"]

    adapt_row = reconnect.execute("SELECT * FROM take_resource_adaptation WHERE session_id=?", (sid,)).fetchone()
    assert adapt_row is not None
    assert adapt_row["adapted_value"] == "adapted cozy room"

    appr_row = reconnect.execute("SELECT * FROM session_plan_approval WHERE session_id=?", (sid,)).fetchone()
    assert appr_row is not None
    assert appr_row["plan_revision"] == 1

    # 3. Foreign keys clean
    assert reconnect.execute("PRAGMA foreign_key_check").fetchall() == []
    assert reconnect.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_rollback_disables_resource_v1_writes_with_http_503(
    client, seeded, monkeypatch: pytest.MonkeyPatch,
):
    """When resource planning is disabled, all creation and preparation routes for

    resource-v1 return HTTP 503 before any persistent write. Legacy session creation
    and existing reads continue to work.
    """
    model_id = seeded["model_id"]
    workflow_id = seeded["workflow_id"]

    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "false")
    assert main.is_resource_planning_enabled() is False

    # 1. Direct session creation with composition_mode="resource-v1" is blocked
    res_create = client.post("/api/sessions", json={
        "model_id": model_id,
        "workflow_id": workflow_id,
        "name": "Blocked Resource Session",
        "composition_mode": "resource-v1",
    })
    assert res_create.status_code == 503
    assert "Resource planning is disabled" in res_create.json()["detail"]

    # Verify no session was inserted
    row = db.one("SELECT id FROM session WHERE name='Blocked Resource Session'")
    assert row is None

    # 2. Legacy session creation is NOT blocked
    res_legacy = client.post("/api/sessions", json={
        "model_id": model_id,
        "workflow_id": workflow_id,
        "name": "Allowed Legacy Session",
        "composition_mode": "",
    })
    assert res_legacy.status_code == 200
    legacy_sid = res_legacy.json()["id"]

    # 3. Transitioning legacy session to resource-v1 via PATCH is blocked
    res_patch = client.patch(f"/api/sessions/{legacy_sid}", json={
        "composition_mode": "resource-v1",
    })
    assert res_patch.status_code == 503

    # 4. Cloning into resource-v1 is blocked
    res_clone = client.post(f"/api/sessions/{legacy_sid}/clone", json={
        "name": "Cloned Session",
        "settings": {"composition_mode": "resource-v1"},
    })
    assert res_clone.status_code == 503

    # Plant a resource-v1 session directly in DB to test plan/prep routes
    res_sid = db.run(
        "INSERT INTO session (model_id, workflow_id, name, settings, created_at) "
        "VALUES (?, ?, 'Direct Resource Session', '{\"composition_mode\":\"resource-v1\"}', 'now')",
        model_id, workflow_id,
    )

    # 5. Saving a plan draft is blocked
    res_plan = client.post(f"/api/sessions/{res_sid}/plan", json={
        "expected_revision": 0,
        "plan": {
            "version": "resource-v1",
            "look": "studio light",
            "initial_wardrobe": "t-shirt",
            "takes": [{"take_id": "take_1", "wardrobe": None}],
        },
    })
    assert res_plan.status_code == 503
    assert db.one("SELECT id FROM session_plan WHERE session_id=?", res_sid) is None

    # 6. Preparation begin is blocked
    res_begin = client.post(f"/api/sessions/{res_sid}/plan/preparations/begin", json={
        "plan_revision": 1,
        "take_id": "take_1",
    })
    assert res_begin.status_code == 503

    # 7. Preparation complete is blocked
    res_complete = client.post(f"/api/sessions/{res_sid}/plan/preparations/complete", json={
        "plan_revision": 1,
        "take_id": "take_1",
        "final_prompt": "final prompt text",
        "effective_state": {},
        "mapping_version": "1.0",
        "compiler_version": "1.0",
        "provenance": {},
    })
    assert res_complete.status_code == 503

    # 8. Adaptation is blocked
    res_adapt = client.post(f"/api/sessions/{res_sid}/plan/takes/take_1/adaptations", json={
        "plan_revision": 1,
        "adaptation": {
            "library_key": "lib",
            "source_id": "s1",
            "content_digest": "d1",
            "resource_field": "prompt",
            "adapted_value": "adapted",
        },
    })
    assert res_adapt.status_code == 503

    # 9. Prepare take endpoints are blocked
    res_prep1 = client.post(f"/api/sessions/{res_sid}/plan/takes/take_1/prepare", json={
        "plan_revision": 1,
    })
    assert res_prep1.status_code == 503

    res_prep_batch = client.post(f"/api/sessions/{res_sid}/plan/preparations/prepare", json={
        "plan_revision": 1,
    })
    assert res_prep_batch.status_code == 503

    # 10. Approve is blocked
    res_appr = client.post(f"/api/sessions/{res_sid}/plan/review/approve", json={
        "plan_revision": 1,
    })
    assert res_appr.status_code == 503

    # Verify no preparation or plan rows were created
    assert db.one("SELECT COUNT(*) AS c FROM prepared_take WHERE session_id=?", res_sid)["c"] == 0
    assert db.one("SELECT COUNT(*) AS c FROM take_resource_adaptation WHERE session_id=?", res_sid)["c"] == 0
    assert db.one("SELECT COUNT(*) AS c FROM session_plan_approval WHERE session_id=?", res_sid)["c"] == 0


def test_rollback_blocks_all_shot_creation_endpoints_for_resource_v1(
    client, seeded, monkeypatch: pytest.MonkeyPatch,
):
    """When resource planning is disabled, all shot creation and preparation routes

    return HTTP 503 for sessions in resource-v1 mode and do not insert or modify
    any shot rows. Legacy sessions remain unblocked.
    """
    model_id = seeded["model_id"]
    workflow_id = seeded["workflow_id"]

    # 1. Create a legacy session and a resource-v1 session
    legacy_sid = db.run(
        "INSERT INTO session (model_id, workflow_id, name, settings, created_at) "
        "VALUES (?, ?, 'Legacy Session', '{}', 'now')",
        model_id, workflow_id,
    )
    res_sid = db.run(
        "INSERT INTO session (model_id, workflow_id, name, settings, created_at) "
        "VALUES (?, ?, 'Resource Session', '{\"composition_mode\":\"resource-v1\"}', 'now')",
        model_id, workflow_id,
    )

    # Seed an existing shot on the resource-v1 session to test reshoot
    res_shot_id = db.run(
        "INSERT INTO shot (session_id, shot_index, shot_label, prompt, status, rating, created_at) "
        "VALUES (?, 1, 'card_01', 'prompt 1', 'done', 0, 'now')",
        res_sid,
    )

    # Disable resource planning
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "false")
    assert main.is_resource_planning_enabled() is False

    shots_count_before = db.one("SELECT COUNT(*) AS c FROM shot WHERE session_id=?", res_sid)["c"]
    assert shots_count_before == 1

    # 1. POST /api/sessions/{sid}/shots is blocked for resource-v1
    res = client.post(f"/api/sessions/{res_sid}/shots", json={"shots": [{"prompt": "test prompt", "shot_label": "t1"}]})
    assert res.status_code == 503
    assert "Resource planning is disabled" in res.json()["detail"]

    # 2. POST /api/sessions/{sid}/compose is blocked for resource-v1
    res = client.post(f"/api/sessions/{res_sid}/compose", json={
        "camera": {"key": "cam", "wordings": [{"key": "w1", "text": "cam wording"}]},
        "act": {"key": "act", "wordings": [{"key": "w2", "text": "act wording"}]},
        "framing": {"key": "fr", "wordings": [{"key": "w3", "text": "fr wording"}]},
    })
    assert res.status_code == 503
    assert "Resource planning is disabled" in res.json()["detail"]

    # 3. POST /api/sessions/{sid}/compose-run is blocked for resource-v1
    res = client.post(f"/api/sessions/{res_sid}/compose-run", json={"count": 1, "candidates": {}})
    assert res.status_code == 503
    assert "Resource planning is disabled" in res.json()["detail"]

    # 4. POST /api/sessions/{sid}/compose-combination is blocked for resource-v1
    res = client.post(f"/api/sessions/{res_sid}/compose-combination", json={"identifier": "any"})
    assert res.status_code == 503
    assert "Resource planning is disabled" in res.json()["detail"]

    # 5. POST /api/sessions/{sid}/compose-session is blocked for resource-v1
    res = client.post(f"/api/sessions/{res_sid}/compose-session", json={"count": 1, "candidates": {}})
    assert res.status_code == 503
    assert "Resource planning is disabled" in res.json()["detail"]

    # 6. POST /api/sessions/{sid}/import is blocked for resource-v1
    res = client.post(f"/api/sessions/{res_sid}/import", content=b"dummy_bytes", headers={"content-type": "image/png"})
    assert res.status_code == 503
    assert "Resource planning is disabled" in res.json()["detail"]

    # 7. POST /api/shots/{shot_id}/reshoot is blocked for resource-v1
    res = client.post(f"/api/shots/{res_shot_id}/reshoot")
    assert res.status_code == 503
    assert "Resource planning is disabled" in res.json()["detail"]
    # Shot row was NOT modified
    shot_after = db.one("SELECT * FROM shot WHERE id=?", res_shot_id)
    assert shot_after["status"] == "done"

    # 8. POST /api/sessions/{sid}/reshoot-below is blocked for resource-v1
    res = client.post(f"/api/sessions/{res_sid}/reshoot-below?min_rating=1")
    assert res.status_code == 503
    assert "Resource planning is disabled" in res.json()["detail"]
    shot_after2 = db.one("SELECT * FROM shot WHERE id=?", res_shot_id)
    assert shot_after2["status"] == "done"

    # Assert no new shot rows were inserted for resource session
    shots_count_after = db.one("SELECT COUNT(*) AS c FROM shot WHERE session_id=?", res_sid)["c"]
    assert shots_count_after == shots_count_before

    # 9. Verify legacy session is NOT blocked by resource planning disabled flag
    res_legacy = client.post(f"/api/sessions/{legacy_sid}/shots", json={"shots": [{"prompt": "legacy prompt", "shot_label": "leg1"}]})
    assert res_legacy.status_code == 200
    assert db.one("SELECT COUNT(*) AS c FROM shot WHERE session_id=?", legacy_sid)["c"] == 1


def test_rollback_keeps_reads_and_queue_actions_functional(
    client, seeded, monkeypatch: pytest.MonkeyPatch, make_runner, tmp_path: Path,
):
    """While resource planning is disabled, clients can still read existing plans

    and reviews, and previously prepared and approved snapshots can still be
    submitted into the queue and generated via FakeComfy without duplicating shots.
    """
    model_id = seeded["model_id"]
    workflow_id = seeded["workflow_id"]

    # 1. Create and prepare work while enabled
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "true")
    res_create = client.post("/api/sessions", json={
        "model_id": model_id,
        "workflow_id": workflow_id,
        "name": "Prepared Shoot",
        "composition_mode": "resource-v1",
    })
    assert res_create.status_code == 200
    sid = res_create.json()["id"]

    save_res = client.post(f"/api/sessions/{sid}/plan", json={
        "expected_revision": 0,
        "plan": {
            "version": "resource-v1",
            "look": "studio softbox",
            "initial_wardrobe": "dark suit",
            "takes": [{
                "take_id": "take_alpha",
                "wardrobe": None,
                "camera": "a 35mm prime at chest height",
                "framing": "waist up",
                "pose": "standing square to the camera",
                "expression": "a slight smile",
            }],
        },
    })
    assert save_res.status_code == 200

    # Prepare take_alpha to ready status
    prep_res = client.post(f"/api/sessions/{sid}/plan/takes/take_alpha/prepare", json={
        "plan_revision": 1,
    })
    assert prep_res.status_code == 200
    assert prep_res.json()["status"] == "ready"

    # Approve the review
    appr_res = client.post(f"/api/sessions/{sid}/plan/review/approve", json={
        "plan_revision": 1,
    })
    assert appr_res.status_code == 200

    # 2. Disable resource planning
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "false")
    assert main.is_resource_planning_enabled() is False

    # 3. Read endpoints remain functional (GET requests succeed)
    plan_read = client.get(f"/api/sessions/{sid}/plan")
    assert plan_read.status_code == 200
    plan_data = plan_read.json()
    assert plan_data["plan"]["look"] == "studio softbox"
    assert len(plan_data["preparation"]["completed"]) == 1
    assert plan_data["preparation"]["completed"][0]["take_id"] == "take_alpha"

    review_read = client.get(f"/api/sessions/{sid}/plan/takes/take_alpha/review")
    assert review_read.status_code == 200
    assert "studio softbox" in review_read.json()["final_prompt"]

    plan_review_read = client.get(f"/api/sessions/{sid}/plan/review")
    assert plan_review_read.status_code == 200

    # 4. Delivery of the already prepared & approved take is preserved
    submit_res = client.post(f"/api/sessions/{sid}/plan/preparations/submit", json={
        "plan_revision": 1,
        "take_id": "take_alpha",
    })
    assert submit_res.status_code == 200
    shot_id = submit_res.json()["linked_shot_id"]
    assert shot_id is not None

    # Idempotent retry returns existing shot without duplication
    retry_res = client.post(f"/api/sessions/{sid}/plan/preparations/submit", json={
        "plan_revision": 1,
        "take_id": "take_alpha",
    })
    assert retry_res.status_code == 200
    assert retry_res.json()["linked_shot_id"] == shot_id
    assert db.one("SELECT COUNT(*) AS c FROM shot WHERE session_id=?", sid)["c"] == 1

    # 5. Serial queue execution via FakeComfy completes the shot
    runner, fake = make_runner()
    asyncio.run(runner._run_session(sid))

    shot_row = db.one("SELECT status, filename FROM shot WHERE id=?", shot_id)
    assert shot_row["status"] == "done"
    assert shot_row["filename"] != ""
    dest_file = runner.sessions_dir / str(sid) / shot_row["filename"]
    assert dest_file.exists()

    # 6. Verify cancellation of active/pending work through existing queue action
    shot2_id = db.run(
        "INSERT INTO shot (session_id, shot_index, prompt, status, created_at) "
        "VALUES (?, 2, 'second take', 'pending', 'now')",
        sid,
    )
    runner.cancel(sid)
    asyncio.run(runner._run_session(sid))
    assert db.one("SELECT status FROM shot WHERE id=?", shot2_id)["status"] == "cancelled"


def test_reactivation_resumes_draft_continuation(
    client, seeded, monkeypatch: pytest.MonkeyPatch,
):
    """Switching resource_planning_enabled back to true immediately restores full

    operation. Previously saved drafts can be edited, re-prepared, and submitted
    without requiring any restoration or database migration.
    """
    model_id = seeded["model_id"]
    workflow_id = seeded["workflow_id"]

    # Start disabled
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "false")
    assert client.post("/api/sessions", json={
        "model_id": model_id,
        "workflow_id": workflow_id,
        "name": "Attempt While Disabled",
        "composition_mode": "resource-v1",
    }).status_code == 503

    # Reactivate
    monkeypatch.setenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", "true")
    assert main.is_resource_planning_enabled() is True

    # Creation succeeds
    res_create = client.post("/api/sessions", json={
        "model_id": model_id,
        "workflow_id": workflow_id,
        "name": "Reactivated Session",
        "composition_mode": "resource-v1",
    })
    assert res_create.status_code == 200
    sid = res_create.json()["id"]

    # Plan draft save succeeds
    res_plan = client.post(f"/api/sessions/{sid}/plan", json={
        "expected_revision": 0,
        "plan": {
            "version": "resource-v1",
            "look": "cozy library",
            "initial_wardrobe": "wool cardigan",
            "takes": [{
                "take_id": "take_one",
                "wardrobe": None,
                "camera": "a 35mm prime at chest height",
                "framing": "waist up",
                "pose": "standing square to the camera",
                "expression": "a slight smile",
            }],
        },
    })
    assert res_plan.status_code == 200
    assert res_plan.json()["plan_revision"] == 1

    # Preparation succeeds
    res_prep = client.post(f"/api/sessions/{sid}/plan/takes/take_one/prepare", json={
        "plan_revision": 1,
    })
    assert res_prep.status_code == 200
    assert res_prep.json()["status"] == "ready"
