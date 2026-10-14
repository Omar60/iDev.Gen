"""Read-only preflight for isolated Full Auto rollback."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile


_TERMINAL_RUNS = {"cancelled", "failed", "succeeded", "completed_with_errors"}
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


class Refusal(Exception):
    def __init__(self, code: str):
        self.code = code


class _PrivateInspectionConnection(sqlite3.Connection):
    def close(self) -> None:
        temporary = getattr(self, "_temporary_directory", None)
        self._temporary_directory = None
        try:
            super().close()
        finally:
            if temporary is not None:
                temporary.cleanup()


def _resolve(value: str | Path, root: Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def _same_path(left: Path, right: Path) -> bool:
    if os.path.normcase(str(left)) == os.path.normcase(str(right)):
        return True
    try:
        return left.samefile(right)
    except OSError:
        return False


def _contains(parent: Path, child: Path) -> bool:
    return child == parent or parent in child.parents


def _overlaps(left: Path, right: Path) -> bool:
    return _contains(left, right) or _contains(right, left)


def _has_sqlite_sidecar(path: Path) -> bool:
    return any(Path(str(path) + suffix).exists() for suffix in _SQLITE_SIDECARS)


def _file_uri(path: Path) -> str:
    return path.as_uri() + "?mode=ro"


def _open_database(
    path: Path,
    *,
    connection_factory: type[sqlite3.Connection] = sqlite3.Connection,
) -> tuple[set[str], sqlite3.Connection]:
    if not path.is_file():
        raise Refusal("database_missing")
    conn = None
    try:
        conn = sqlite3.connect(
            _file_uri(path), uri=True, timeout=5, factory=connection_factory,
        )
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise Refusal("database_invalid")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise Refusal("database_invalid")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        return tables, conn
    except Refusal:
        if conn is not None:
            conn.close()
        raise
    except (OSError, sqlite3.Error, ValueError):
        if conn is not None:
            conn.close()
        raise Refusal("database_invalid") from None


def _inspect_database(path: Path) -> tuple[set[str], sqlite3.Connection]:
    if not path.is_file():
        raise Refusal("database_missing")
    temporary = tempfile.TemporaryDirectory(prefix="idevgen-rollback-inspect-")
    connection = None
    try:
        private_copy = _snapshot_database(path, Path(temporary.name) / "idevgen.db")
        tables, connection = _open_database(
            private_copy,
            connection_factory=_PrivateInspectionConnection,
        )
        connection._temporary_directory = temporary
        temporary = None
        return tables, connection
    except Refusal:
        if connection is not None:
            connection.close()
        raise
    except Exception:
        if connection is not None:
            connection.close()
        raise Refusal("database_invalid") from None
    finally:
        if temporary is not None:
            temporary.cleanup()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_database(source: Path, destination: Path) -> Path:
    wal = Path(str(source) + "-wal")
    if Path(str(source) + "-journal").exists():
        raise Refusal("upgraded_database_not_stopped")
    try:
        before = (_sha256(source), _sha256(wal) if wal.exists() else None)
        shutil.copyfile(source, destination)
        if wal.exists():
            shutil.copyfile(wal, Path(str(destination) + "-wal"))
        copied = (_sha256(destination), _sha256(Path(str(destination) + "-wal")) if wal.exists() else None)
        after = (_sha256(source), _sha256(wal) if wal.exists() else None)
    except OSError:
        raise Refusal("upgraded_database_unreadable") from None
    if before != copied or copied != after:
        raise Refusal("upgraded_database_changed")
    return destination


def _file_identity(path: Path) -> tuple[int, int] | None:
    stat = path.stat(follow_symlinks=False)
    return (stat.st_dev, stat.st_ino) if stat.st_ino else None


def _is_path_alias(path: Path) -> bool:
    if path.is_symlink():
        return True
    junction_check = getattr(path, "is_junction", None)
    if junction_check is not None and junction_check():
        return True
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _tree_files(root: Path):
    def fail_on_walk_error(error: OSError) -> None:
        raise error

    for directory, names, files in os.walk(root, followlinks=False, onerror=fail_on_walk_error):
        base = Path(directory)
        for name in tuple(names):
            path = base / name
            if _is_path_alias(path):
                names.remove(name)
                yield path
        for name in files:
            yield base / name


def _refuse_upgraded_aliases(upgraded: Path, restored: Path) -> set[tuple[int, int]]:
    if _overlaps(upgraded, restored):
        raise Refusal("data_paths_overlap")
    upgraded_ids: set[tuple[int, int]] = set()
    try:
        for path in _tree_files(upgraded):
            if _is_path_alias(path):
                raise Refusal("data_paths_overlap")
            if path.is_file():
                identity = _file_identity(path)
                if identity is not None:
                    upgraded_ids.add(identity)
        for path in _tree_files(restored):
            if _is_path_alias(path):
                raise Refusal("data_paths_overlap")
            resolved = path.resolve()
            if _overlaps(upgraded, resolved):
                raise Refusal("data_paths_overlap")
            if path.is_file():
                identity = _file_identity(path)
                if identity is not None and identity in upgraded_ids:
                    raise Refusal("data_paths_overlap")
    except Refusal:
        raise
    except OSError:
        raise Refusal("filesystem_unreadable") from None
    return upgraded_ids


def _check_settled(conn: sqlite3.Connection) -> None:
    required = {
        "full_auto_run",
        "full_auto_execution_attempt",
        "full_auto_attempt_risk_acceptance",
        "full_auto_assistant_slot",
        "full_auto_assistant_reservation",
        "global_runner_claim",
        "authoring_operation",
        "session",
        "shot",
        "full_auto_run_receipt",
        "full_auto_run_authorization",
        "full_auto_runner_admission",
        "full_auto_submission_receipt",
    }
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if not required <= tables:
        raise Refusal("upgraded_schema_unrecognized")
    checks = (
        (
            "SELECT 1 FROM full_auto_run "
            "WHERE state NOT IN (?, ?, ?, ?) LIMIT 1",
            tuple(_TERMINAL_RUNS),
        ),
        (
            "SELECT 1 FROM full_auto_execution_attempt "
            "WHERE dispatch_state IN ('intent', 'outcome_unknown') "
            "OR completion_state IN ('pending', 'output_available') LIMIT 1",
            (),
        ),
        (
            "SELECT 1 FROM full_auto_assistant_slot "
            "WHERE state IN ('active', 'quarantined') LIMIT 1",
            (),
        ),
        (
            "SELECT 1 FROM full_auto_assistant_reservation "
            "WHERE settled_at IS NULL LIMIT 1",
            (),
        ),
        ("SELECT 1 FROM global_runner_claim WHERE owner_kind IS NOT NULL LIMIT 1", ()),
        (
            "SELECT 1 FROM authoring_operation "
            "WHERE state IN ('active', 'cancel_requested') LIMIT 1",
            (),
        ),
        ("SELECT 1 FROM session WHERE status = 'running' LIMIT 1", ()),
        ("SELECT 1 FROM shot WHERE status = 'running' LIMIT 1", ()),
        (
            """SELECT 1 FROM full_auto_runner_admission admission
               WHERE COALESCE(
                   (SELECT json_extract(grant_row.authorization_json, '$.summary.photo_count')
                    FROM full_auto_run_authorization grant_row
                    WHERE grant_row.run_id = admission.run_id
                    ORDER BY grant_row.authorization_sequence DESC LIMIT 1),
                   (SELECT json_extract(receipt.authorization_json, '$.summary.photo_count')
                    FROM full_auto_run_receipt receipt
                    WHERE receipt.run_id = admission.run_id),
                   -1
               ) != (
                   SELECT COUNT(*) FROM full_auto_submission_receipt submission
                   WHERE submission.run_id = admission.run_id
               )
               OR EXISTS (
                   SELECT 1 FROM full_auto_submission_receipt submission
                   LEFT JOIN shot ON shot.id = submission.shot_id
                   WHERE submission.run_id = admission.run_id
                     AND (shot.id IS NULL OR shot.session_id != admission.session_id
                          OR COALESCE(shot.status, '') NOT IN ('done', 'cancelled', 'failed'))
               )
               LIMIT 1""",
            (),
        ),
    )
    try:
        if any(conn.execute(sql, params).fetchone() is not None for sql, params in checks):
            raise Refusal("work_not_settled")
        failed_submissions = conn.execute(
            """SELECT admission.run_id, admission.session_id, shot.id AS shot_id
               FROM full_auto_runner_admission admission
               JOIN full_auto_submission_receipt submission
                 ON submission.run_id = admission.run_id
               JOIN shot ON shot.id = submission.shot_id
               WHERE shot.status = 'failed'"""
        ).fetchall()
        if failed_submissions:
            attempt_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(full_auto_execution_attempt)"
                )
            }
            required_attempt_columns = {
                "shot_id", "run_id", "session_id", "attempt_number",
                "dispatch_state", "completion_state",
            }
            if not required_attempt_columns <= attempt_columns:
                raise Refusal("work_not_settled")
            for submission in failed_submissions:
                attempt = conn.execute(
                    """SELECT dispatch_state, completion_state
                       FROM full_auto_execution_attempt
                       WHERE shot_id = ? AND run_id = ? AND session_id = ?
                       ORDER BY attempt_number DESC LIMIT 1""",
                    (submission[2], submission[0], submission[1]),
                ).fetchone()
                if (
                    attempt is None
                    or attempt[0] not in {"acknowledged", "rejected"}
                    or attempt[1] != "failed"
                ):
                    raise Refusal("work_not_settled")
    except Refusal:
        raise
    except sqlite3.Error:
        raise Refusal("upgraded_schema_unrecognized") from None


def check_rollback(
    *,
    backup: str,
    upgraded_data: str,
    restored_data: str,
    config: str,
    runtime_root: str,
) -> None:
    upgraded = _resolve(upgraded_data, Path.cwd())
    restored = _resolve(restored_data, Path.cwd())
    source_root = _resolve(runtime_root, Path.cwd())
    backup_path = _resolve(backup, Path.cwd())
    config_path = _resolve(config, source_root)
    upgraded_db = upgraded / "idevgen.db"
    restored_db = restored / "idevgen.db"

    if not upgraded.is_dir() or not restored.is_dir():
        raise Refusal("data_directory_missing")
    if not (source_root / "backend" / "main.py").is_file():
        raise Refusal("runtime_root_invalid")
    env_config = os.environ.get("IDEVGEN_CONFIG")
    effective_config = _resolve(env_config, source_root) if env_config else (source_root / "config.json").resolve()
    if not _same_path(effective_config, config_path):
        raise Refusal("launch_config_mismatch")
    if not config_path.is_file() or _overlaps(upgraded, config_path):
        raise Refusal("restored_config_invalid")
    upgraded_file_ids = _refuse_upgraded_aliases(upgraded, restored)
    try:
        config_identity = _file_identity(config_path)
    except OSError:
        raise Refusal("filesystem_unreadable") from None
    if config_identity is not None and config_identity in upgraded_file_ids:
        raise Refusal("restored_config_invalid")
    try:
        config_value = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config_value, dict):
            raise ValueError
        configured_dir = config_value.get("data_dir", "data")
        if not isinstance(configured_dir, str):
            raise ValueError
    except (OSError, ValueError, UnicodeError):
        raise Refusal("restored_config_invalid") from None

    env_data_dir = os.environ.get("IDEVGEN_DATA_DIR")
    effective_data = _resolve(env_data_dir or configured_dir, source_root)
    if not _same_path(effective_data, restored):
        raise Refusal("launch_data_mismatch")
    if _overlaps(restored, backup_path):
        raise Refusal("backup_inside_restored_data")
    if not backup_path.is_file() or _has_sqlite_sidecar(backup_path):
        raise Refusal("backup_missing_or_unstable")

    backup_tables, backup_conn = _inspect_database(backup_path)
    try:
        if not {"session", "shot"} <= backup_tables:
            raise Refusal("backup_invalid")
        if any(
            name.startswith("full_auto_")
            or name in {
                "saved_look_automation_profile",
                "session_profile_evidence",
                "session_profile_coverage_revision",
                "take_resource_coverage",
            }
            for name in backup_tables
        ):
            raise Refusal("backup_not_pre_upgrade")
    finally:
        backup_conn.close()

    if _has_sqlite_sidecar(restored_db):
        raise Refusal("restored_database_not_pristine")
    if not restored_db.is_file() or _same_path(backup_path, restored_db):
        raise Refusal("restored_database_missing")
    if _sha256(backup_path) != _sha256(restored_db):
        raise Refusal("restored_database_mismatch")
    restored_tables, restored_conn = _inspect_database(restored_db)
    restored_conn.close()
    if not {"session", "shot"} <= restored_tables:
        raise Refusal("restored_database_invalid")

    upgraded_tables, upgraded_conn = _inspect_database(upgraded_db)
    try:
        if "full_auto_run" not in upgraded_tables:
            raise Refusal("upgraded_schema_unrecognized")
        _check_settled(upgraded_conn)
    finally:
        upgraded_conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Full Auto rollback preflight.")
    parser.add_argument("--check", action="store_true", required=True, help="check rollback prerequisites only")
    parser.add_argument("--backup", required=True, help="verified pre-upgrade SQLite backup")
    parser.add_argument("--upgraded-data", required=True, help="retained upgraded data directory")
    parser.add_argument("--restored-data", required=True, help="separate restored data directory")
    parser.add_argument("--config", required=True, help="config file the older runtime will use")
    parser.add_argument("--runtime-root", required=True, help="older source root used to resolve relative paths")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        check_rollback(
            backup=args.backup,
            upgraded_data=args.upgraded_data,
            restored_data=args.restored_data,
            config=args.config,
            runtime_root=args.runtime_root,
        )
    except Refusal as exc:
        print(f"refused: {exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("refused: validation_failed", file=sys.stderr)
        return 1
    print("ready: backup verified; restored data isolated; upgraded work settled and retained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
