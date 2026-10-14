"""Safety checks for the read-only Full Auto rollback preflight."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

import full_auto_rollback
from backup import backup_database


@pytest.fixture
def rollback_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    runtime_root = tmp_path / "old-release"
    (runtime_root / "backend").mkdir(parents=True)
    (runtime_root / "backend" / "main.py").write_text("", encoding="utf-8")
    upgraded_data = tmp_path / "upgraded-data"
    upgraded_data.mkdir()
    (upgraded_data / "sessions").mkdir()
    (upgraded_data / "sessions" / "keep.png").write_bytes(b"synthetic image")
    upgraded_db = upgraded_data / "idevgen.db"
    with sqlite3.connect(upgraded_db) as conn:
        conn.executescript("""
            CREATE TABLE full_auto_run (state TEXT NOT NULL);
            CREATE TABLE full_auto_execution_attempt (
                attempt_id TEXT, run_id TEXT, session_id INTEGER, shot_id INTEGER,
                attempt_number INTEGER, dispatch_state TEXT, completion_state TEXT
            );
            CREATE TABLE full_auto_attempt_risk_acceptance (attempt_id TEXT);
            CREATE TABLE full_auto_assistant_slot (state TEXT);
            CREATE TABLE full_auto_assistant_reservation (settled_at TEXT);
            CREATE TABLE global_runner_claim (owner_kind TEXT);
            CREATE TABLE authoring_operation (state TEXT);
            CREATE TABLE session (id INTEGER PRIMARY KEY, status TEXT);
            CREATE TABLE shot (id INTEGER PRIMARY KEY, session_id INTEGER, status TEXT);
            CREATE TABLE full_auto_run_receipt (run_id TEXT, authorization_json TEXT);
            CREATE TABLE full_auto_run_authorization (
                run_id TEXT, authorization_sequence INTEGER, authorization_json TEXT
            );
            CREATE TABLE full_auto_runner_admission (run_id TEXT, session_id INTEGER);
            CREATE TABLE full_auto_submission_receipt (run_id TEXT, shot_id INTEGER);
            INSERT INTO global_runner_claim VALUES (NULL);
            INSERT INTO session VALUES (1, 'done');
        """)

    pre_upgrade = tmp_path / "pre-upgrade-source.db"
    with sqlite3.connect(pre_upgrade) as conn:
        conn.executescript("""
            CREATE TABLE session (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE shot (id INTEGER PRIMARY KEY, session_id INTEGER);
            INSERT INTO session (name) VALUES ('Synthetic legacy session');
        """)
    backup = tmp_path / "pre-upgrade-backup.db"
    backup_database(pre_upgrade, backup)

    restored_data = tmp_path / "restored-data"
    restored_data.mkdir()
    restored_db = restored_data / "idevgen.db"
    restored_db.write_bytes(backup.read_bytes())
    config = tmp_path / "restored-config.json"
    config.write_text(json.dumps({"data_dir": str(restored_data)}), encoding="utf-8")
    monkeypatch.setenv("IDEVGEN_CONFIG", str(config))
    monkeypatch.delenv("IDEVGEN_DATA_DIR", raising=False)
    return {
        "root": tmp_path,
        "runtime": runtime_root,
        "upgraded": upgraded_data,
        "restored": restored_data,
        "config": config,
        "backup": backup,
    }


def _args(paths: dict[str, Path], **overrides: Path) -> list[str]:
    values = {
        "backup": paths["backup"],
        "upgraded-data": paths["upgraded"],
        "restored-data": paths["restored"],
        "config": paths["config"],
        "runtime-root": paths["runtime"],
        **{key.replace("_", "-"): value for key, value in overrides.items()},
    }
    return [part for key, value in values.items() for part in (f"--{key}", str(value))] + ["--check"]


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def _make_directory_alias(alias: Path, target: Path) -> None:
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("directory symlink is unavailable")
        junction = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(alias), str(target)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if junction.returncode != 0:
            pytest.skip("directory junction is unavailable")
    assert alias.is_dir()


def _assert_refused_without_changes(
    paths: dict[str, Path], code: str, capsys: pytest.CaptureFixture[str],
    **overrides: Path,
) -> None:
    before = _snapshot(paths["root"])
    assert full_auto_rollback.main(_args(paths, **overrides)) == 1
    output = capsys.readouterr()
    assert output.err.strip() == f"refused: {code}"
    assert str(paths["root"]) not in output.err + output.out
    assert _snapshot(paths["root"]) == before


def test_read_only_preflight_accepts_a_consistent_isolated_restore(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    before = _snapshot(paths["root"])
    assert full_auto_rollback.main(_args(paths)) == 0
    assert capsys.readouterr().out.strip() == (
        "ready: backup verified; restored data isolated; upgraded work settled and retained"
    )
    assert _snapshot(paths["root"]) == before


def test_preflight_refuses_missing_invalid_and_upgraded_backups(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    _assert_refused_without_changes(paths, "backup_missing_or_unstable", capsys, backup=paths["root"] / "missing.db")

    paths["backup"].write_bytes(b"not a database")
    _assert_refused_without_changes(paths, "database_invalid", capsys)

    paths["backup"].unlink()
    with sqlite3.connect(paths["backup"]) as conn:
        conn.executescript("""
            CREATE TABLE session (id INTEGER PRIMARY KEY);
            CREATE TABLE shot (id INTEGER PRIMARY KEY);
            CREATE TABLE full_auto_run_receipt (run_id TEXT);
        """)
    paths["restored"].joinpath("idevgen.db").write_bytes(paths["backup"].read_bytes())
    _assert_refused_without_changes(paths, "backup_not_pre_upgrade", capsys)


@pytest.mark.parametrize("risk_accepted", [False, True])
def test_preflight_refuses_unresolved_and_risk_accepted_unknown_attempts(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str], risk_accepted: bool,
):
    paths = rollback_inputs
    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute(
            "INSERT INTO full_auto_execution_attempt (attempt_id, dispatch_state, completion_state) "
            "VALUES ('attempt', 'outcome_unknown', 'pending')"
        )
        if risk_accepted:
            conn.execute("INSERT INTO full_auto_attempt_risk_acceptance VALUES ('attempt')")
    _assert_refused_without_changes(paths, "work_not_settled", capsys)


def test_preflight_reads_committed_wal_work_without_touching_source_sidecars(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    connection = sqlite3.connect(paths["upgraded"] / "idevgen.db")
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        connection.execute(
            "INSERT INTO full_auto_execution_attempt (attempt_id, dispatch_state, completion_state) "
            "VALUES ('wal-attempt', 'intent', 'pending')"
        )
        connection.commit()
        before = _snapshot(paths["root"])
        assert full_auto_rollback.main(_args(paths)) == 1
        assert capsys.readouterr().err.strip() == "refused: work_not_settled"
        assert _snapshot(paths["root"]) == before
    finally:
        connection.close()


def test_preflight_refuses_live_run_and_runner_ownership(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute("UPDATE global_runner_claim SET owner_kind = 'manual'")
    _assert_refused_without_changes(paths, "work_not_settled", capsys)

    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute("UPDATE global_runner_claim SET owner_kind = NULL")
        conn.execute("INSERT INTO full_auto_assistant_slot VALUES ('active')")
    _assert_refused_without_changes(paths, "work_not_settled", capsys)

    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute("DELETE FROM full_auto_assistant_slot")
        conn.execute("INSERT INTO full_auto_run VALUES ('blocked')")
    _assert_refused_without_changes(paths, "work_not_settled", capsys)


def test_settled_failed_attempt_does_not_block_rollback(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute("INSERT INTO full_auto_run VALUES ('failed')")
        conn.execute(
            "INSERT INTO full_auto_execution_attempt (attempt_id, dispatch_state, completion_state) "
            "VALUES ('attempt', 'rejected', 'failed')"
        )
    assert full_auto_rollback.main(_args(paths)) == 0
    assert capsys.readouterr().err == ""


def test_orphan_running_shot_blocks_rollback_even_after_run_failure(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute("INSERT INTO full_auto_run VALUES ('failed')")
        conn.execute("INSERT INTO shot VALUES (1, 1, 'running')")
    _assert_refused_without_changes(paths, "work_not_settled", capsys)


def test_incomplete_admitted_photo_count_blocks_rollback(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute(
            "INSERT INTO full_auto_run_receipt VALUES ('run', ?)" ,
            (json.dumps({"summary": {"photo_count": 2}}),),
        )
        conn.execute("INSERT INTO full_auto_runner_admission VALUES ('run', 1)")
    _assert_refused_without_changes(paths, "work_not_settled", capsys)


def test_known_failed_admitted_shot_does_not_block_rollback(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute("INSERT INTO full_auto_run VALUES ('failed')")
        conn.execute(
            "INSERT INTO full_auto_run_receipt VALUES ('run', ?)",
            (json.dumps({"summary": {"photo_count": 1}}),),
        )
        conn.execute("INSERT INTO full_auto_runner_admission VALUES ('run', 1)")
        conn.execute("INSERT INTO shot VALUES (1, 1, 'failed')")
        conn.execute("INSERT INTO full_auto_submission_receipt VALUES ('run', 1)")
        conn.execute(
            """INSERT INTO full_auto_execution_attempt
               VALUES ('attempt', 'run', 1, 1, 1, 'rejected', 'failed')"""
        )
    assert full_auto_rollback.main(_args(paths)) == 0
    assert capsys.readouterr().err == ""


def test_preflight_refuses_same_nested_and_hard_linked_data(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs

    def point_config_at(data_dir: Path) -> None:
        paths["config"].write_text(json.dumps({"data_dir": str(data_dir)}), encoding="utf-8")
        os.environ.pop("IDEVGEN_DATA_DIR", None)

    point_config_at(paths["upgraded"])
    _assert_refused_without_changes(paths, "data_paths_overlap", capsys, restored_data=paths["upgraded"])

    nested = paths["upgraded"] / "restored"
    nested.mkdir()
    nested.joinpath("idevgen.db").write_bytes(paths["backup"].read_bytes())
    point_config_at(nested)
    _assert_refused_without_changes(paths, "data_paths_overlap", capsys, restored_data=nested)

    point_config_at(paths["restored"])
    restored_db = paths["restored"] / "idevgen.db"
    restored_db.unlink()
    os.link(paths["upgraded"] / "idevgen.db", restored_db)
    _assert_refused_without_changes(paths, "data_paths_overlap", capsys)


def test_preflight_refuses_a_restored_directory_alias(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    external = paths["root"] / "external-data"
    external.mkdir()
    os.link(paths["upgraded"] / "idevgen.db", external / "upgraded-db-link")
    _make_directory_alias(paths["restored"] / "aliased-data", external)
    _assert_refused_without_changes(paths, "data_paths_overlap", capsys)


def test_preflight_refuses_a_config_hard_link_to_upgraded_evidence(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    upgraded_config = paths["upgraded"] / "config.json"
    upgraded_config.write_text(json.dumps({"data_dir": str(paths["restored"])}), encoding="utf-8")
    linked_config = paths["root"] / "linked-config.json"
    os.link(upgraded_config, linked_config)
    os.environ["IDEVGEN_CONFIG"] = str(linked_config)
    _assert_refused_without_changes(paths, "restored_config_invalid", capsys, config=linked_config)


def test_preflight_refuses_a_mismatched_restore_and_wrong_launch_environment(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    with sqlite3.connect(paths["restored"] / "idevgen.db") as conn:
        conn.execute("INSERT INTO session (name) VALUES ('changed restore')")
    _assert_refused_without_changes(paths, "restored_database_mismatch", capsys)

    paths["restored"].joinpath("idevgen.db").write_bytes(paths["backup"].read_bytes())
    other_config = paths["root"] / "other-config.json"
    other_config.write_text(json.dumps({"data_dir": str(paths["upgraded"])}), encoding="utf-8")
    os.environ["IDEVGEN_CONFIG"] = str(other_config)
    _assert_refused_without_changes(paths, "launch_config_mismatch", capsys)


def test_preflight_resolves_relative_old_runtime_config_and_data_environment(
    rollback_inputs: dict[str, Path], capsys: pytest.CaptureFixture[str],
):
    paths = rollback_inputs
    relative_config = paths["runtime"] / "restored-config.json"
    relative_config.write_text(json.dumps({"data_dir": "unused"}), encoding="utf-8")
    os.environ["IDEVGEN_CONFIG"] = "restored-config.json"
    os.environ["IDEVGEN_DATA_DIR"] = "../restored-data"
    args = _args(
        paths,
        config=Path("restored-config.json"),
        restored_data=paths["restored"],
    )
    before = _snapshot(paths["root"])
    assert full_auto_rollback.main(args) == 0
    assert "ready:" in capsys.readouterr().out
    assert _snapshot(paths["root"]) == before

