"""Independent black-box checks for Full Auto rollback isolation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from backup import backup_database
import full_auto_rollback


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rollback_case(tmp_path: Path) -> dict[str, Path]:
    runtime = tmp_path / "old-runtime"
    (runtime / "backend").mkdir(parents=True)
    (runtime / "backend" / "main.py").write_text("", encoding="utf-8")

    upgraded = tmp_path / "upgraded-data"
    (upgraded / "sessions").mkdir(parents=True)
    (upgraded / "sessions" / "retained.png").write_bytes(b"synthetic image")
    with sqlite3.connect(upgraded / "idevgen.db") as conn:
        conn.executescript("""
            CREATE TABLE full_auto_run (run_id TEXT, session_id INTEGER, state TEXT);
            CREATE TABLE full_auto_execution_attempt (
                dispatch_state TEXT, completion_state TEXT
            );
            CREATE TABLE full_auto_attempt_risk_acceptance (attempt_id TEXT);
            CREATE TABLE full_auto_assistant_slot (state TEXT);
            CREATE TABLE full_auto_assistant_reservation (settled_at TEXT);
            CREATE TABLE global_runner_claim (owner_kind TEXT);
            CREATE TABLE authoring_operation (state TEXT);
            CREATE TABLE session (id INTEGER PRIMARY KEY, status TEXT);
            CREATE TABLE shot (id INTEGER PRIMARY KEY, session_id INTEGER, status TEXT);
            CREATE TABLE full_auto_runner_admission (run_id TEXT, session_id INTEGER);
            CREATE TABLE full_auto_run_receipt (
                run_id TEXT, session_id INTEGER, authorization_json TEXT
            );
            CREATE TABLE full_auto_run_authorization (
                run_id TEXT, authorization_sequence INTEGER, authorization_json TEXT
            );
            CREATE TABLE full_auto_submission_receipt (run_id TEXT, shot_id INTEGER);
            INSERT INTO global_runner_claim VALUES (NULL);
            INSERT INTO session VALUES (1, 'done');
        """)

    # This schema is intentionally only plausible-looking: it must not stand in
    # for evidence that the backup came from the recorded older application.
    legacy_source = tmp_path / "legacy-source.db"
    with sqlite3.connect(legacy_source) as conn:
        conn.executescript("""
            CREATE TABLE session (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE shot (id INTEGER PRIMARY KEY, session_id INTEGER);
            INSERT INTO session (name) VALUES ('Synthetic legacy session');
        """)
    backup = tmp_path / "pre-upgrade-backup.db"
    backup_database(legacy_source, backup)

    restored = tmp_path / "restored-data"
    restored.mkdir()
    shutil.copyfile(backup, restored / "idevgen.db")
    config = tmp_path / "restored-config.json"
    config.write_text(json.dumps({"data_dir": str(restored)}), encoding="utf-8")

    return {
        "root": tmp_path,
        "runtime": runtime,
        "upgraded": upgraded,
        "restored": restored,
        "config": config,
        "backup": backup,
    }


def _snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def _run_check(paths: dict[str, Path], **overrides: Path) -> subprocess.CompletedProcess[str]:
    arguments = {
        "backup": paths["backup"],
        "upgraded-data": paths["upgraded"],
        "restored-data": paths["restored"],
        "config": paths["config"],
        "runtime-root": paths["runtime"],
        **{name.replace("_", "-"): value for name, value in overrides.items()},
    }
    env = os.environ.copy()
    env["IDEVGEN_CONFIG"] = str(paths["config"])
    env.pop("IDEVGEN_DATA_DIR", None)
    return subprocess.run(
        [
            sys.executable, "-m", "backend.full_auto_rollback",
            *[part for name, value in arguments.items() for part in (f"--{name}", str(value))],
            "--check",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _assert_refused_unchanged(paths: dict[str, Path], **overrides: Path) -> None:
    before = _snapshot(paths["root"])
    result = _run_check(paths, **overrides)
    assert result.returncode != 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr.startswith("refused: "), result.stderr
    assert str(paths["root"]) not in result.stdout + result.stderr
    assert _snapshot(paths["root"]) == before


def _make_directory_alias(alias: Path, target: Path) -> None:
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if os.name != "nt":
            pytest.skip(f"directory symlink is unavailable: {exc}")
        junction = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(alias), str(target)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if junction.returncode != 0:
            pytest.skip(f"directory junction is unavailable: {junction.stderr or junction.stdout}")
    assert alias.is_dir()


@pytest.mark.parametrize(
    ("dispatch_state", "completion_state", "risk_accepted"),
    [
        ("intent", "pending", False),
        ("acknowledged", "pending", False),
        ("outcome_unknown", "pending", False),
        ("outcome_unknown", "pending", True),
    ],
)
def test_cli_refuses_every_unsettled_attempt(
    rollback_case: dict[str, Path],
    dispatch_state: str,
    completion_state: str,
    risk_accepted: bool,
):
    paths = rollback_case
    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute(
            "INSERT INTO full_auto_execution_attempt VALUES (?, ?)",
            (dispatch_state, completion_state),
        )
        if risk_accepted:
            conn.execute("INSERT INTO full_auto_attempt_risk_acceptance VALUES ('attempt')")
    _assert_refused_unchanged(paths)


def test_cli_refuses_pending_cancellation_even_without_a_live_attempt(
    rollback_case: dict[str, Path],
):
    paths = rollback_case
    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute("INSERT INTO full_auto_run VALUES ('run', 1, 'cancel_requested')")
    _assert_refused_unchanged(paths)


def test_cli_refuses_a_run_with_an_orphan_running_shot(
    rollback_case: dict[str, Path],
):
    paths = rollback_case
    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute("INSERT INTO full_auto_run VALUES ('run', 1, 'failed')")
        conn.execute("INSERT INTO shot VALUES (1, 1, 'running')")
    _assert_refused_unchanged(paths)


def test_cli_refuses_an_admitted_run_with_missing_submission_receipts(
    rollback_case: dict[str, Path],
):
    paths = rollback_case
    with sqlite3.connect(paths["upgraded"] / "idevgen.db") as conn:
        conn.execute("INSERT INTO full_auto_run VALUES ('run', 1, 'failed')")
        conn.execute("INSERT INTO full_auto_run_receipt VALUES ('run', 1, '{\"summary\":{\"photo_count\":2}}')")
        conn.execute("INSERT INTO full_auto_runner_admission VALUES ('run', 1)")
    _assert_refused_unchanged(paths)


def test_cli_refuses_a_restored_symlink_tree_hiding_an_upgraded_hardlink(
    rollback_case: dict[str, Path],
):
    paths = rollback_case
    external = paths["root"] / "external-alias-target"
    external.mkdir()
    os.link(paths["upgraded"] / "idevgen.db", external / "hidden-upgraded-db-link")
    alias = paths["restored"] / "external-data"
    _make_directory_alias(alias, external)
    _assert_refused_unchanged(paths)


def test_cli_refuses_a_config_hardlink_to_upgraded_evidence(
    rollback_case: dict[str, Path],
):
    paths = rollback_case
    upgraded_config = paths["upgraded"] / "config.json"
    upgraded_config.write_text(json.dumps({"data_dir": str(paths["restored"])}), encoding="utf-8")
    linked_config = paths["root"] / "external-linked-config.json"
    os.link(upgraded_config, linked_config)
    paths["config"] = linked_config
    _assert_refused_unchanged(paths, config=linked_config)


def test_cli_resolves_config_through_an_alias_into_upgraded_data(
    rollback_case: dict[str, Path],
):
    paths = rollback_case
    upgraded_config = paths["upgraded"] / "config.json"
    upgraded_config.write_text(json.dumps({"data_dir": str(paths["restored"])}), encoding="utf-8")
    alias = paths["root"] / "upgraded-through-alias"
    _make_directory_alias(alias, paths["upgraded"])
    paths["config"] = alias / "config.json"
    _assert_refused_unchanged(paths, config=paths["config"])


def test_cli_does_not_treat_sqlite_user_version_as_a_downgrade_guard(
    rollback_case: dict[str, Path],
):
    paths = rollback_case
    with sqlite3.connect(paths["backup"]) as conn:
        conn.execute("PRAGMA user_version = 777")
    shutil.copyfile(paths["backup"], paths["restored"] / "idevgen.db")
    before = _snapshot(paths["root"])
    result = _run_check(paths)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("ready: ")
    assert result.stderr == ""
    assert _snapshot(paths["root"]) == before


def test_preflight_observes_no_transient_sidecars_on_wal_backup_or_restore(
    rollback_case: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
):
    paths = rollback_case
    wal_source = paths["root"] / "wal-pre-upgrade-source.db"
    with sqlite3.connect(wal_source) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        conn.executescript("""
            CREATE TABLE session (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE shot (id INTEGER PRIMARY KEY, session_id INTEGER);
            INSERT INTO session (name) VALUES ('Synthetic WAL session');
        """)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    backup = paths["backup"]
    restored_db = paths["restored"] / "idevgen.db"
    shutil.copyfile(wal_source, backup)
    shutil.copyfile(wal_source, restored_db)

    def sidecars(database: Path) -> set[str]:
        return {
            suffix for suffix in ("-wal", "-shm", "-journal")
            if Path(str(database) + suffix).exists()
        }

    assert sidecars(backup) == set()
    assert sidecars(restored_db) == set()
    monkeypatch.setenv("IDEVGEN_CONFIG", str(paths["config"]))
    monkeypatch.delenv("IDEVGEN_DATA_DIR", raising=False)
    originals = (backup, restored_db, paths["upgraded"] / "idevgen.db")
    observed_while_open: list[tuple[Path, dict[Path, set[str]]]] = []
    inspect_database = full_auto_rollback._inspect_database

    def inspect_and_observe(database: Path):
        tables, connection = inspect_database(database)
        observed_while_open.append(
            (database.resolve(), {original: sidecars(original) for original in originals})
        )
        return tables, connection

    monkeypatch.setattr(full_auto_rollback, "_inspect_database", inspect_and_observe)
    args = [
        "--check",
        "--backup", str(backup),
        "--upgraded-data", str(paths["upgraded"]),
        "--restored-data", str(paths["restored"]),
        "--config", str(paths["config"]),
        "--runtime-root", str(paths["runtime"]),
    ]
    result = full_auto_rollback.main(args)

    assert result == 0
    # The pre-upgrade backup and restored DB must both be inspected. The
    # implementation may validate private copies, so observe the actual
    # source files after every inspection returns with its connection still
    # open instead of requiring source paths as inputs.
    assert len(observed_while_open) >= 2
    assert all(
        sidecars == {original: set() for original in originals}
        for _, sidecars in observed_while_open
    ), observed_while_open
    assert all(sidecars(original) == set() for original in originals)


def test_cli_uses_the_effective_data_environment_and_preserves_settled_data(
    rollback_case: dict[str, Path],
):
    paths = rollback_case
    before = _snapshot(paths["root"])
    env = os.environ.copy()
    env["IDEVGEN_CONFIG"] = str(paths["config"])
    env["IDEVGEN_DATA_DIR"] = str(paths["upgraded"])
    args = [
        "--backup", str(paths["backup"]),
        "--upgraded-data", str(paths["upgraded"]),
        "--restored-data", str(paths["restored"]),
        "--config", str(paths["config"]),
        "--runtime-root", str(paths["runtime"]),
        "--check",
    ]
    result = subprocess.run(
        [sys.executable, "-m", "backend.full_auto_rollback", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert result.stderr == "refused: launch_data_mismatch\n"
    assert _snapshot(paths["root"]) == before
