"""Create a consistent SQLite database backup.

Captures all committed data, including WAL journal state, using standard
library sqlite3.Connection.backup().

Does NOT call db.connect() so pre-upgrade source schemas remain unmodified.
Preserves SQLite database tables, rows, settings, and references; does NOT
copy external photograph files on disk (data/sessions/...).

Examples:
    python scripts/backup_db.py
    python scripts/backup_db.py --target data/backups/manual.db
    python scripts/backup_db.py --source data/idevgen.db --target data/backups/backup.db --force
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
for path in (ROOT, BACKEND):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backup import backup_database  # noqa: E402


def _resolve_data_dir(config_path: str | None, override: str | None) -> Path:
    if override:
        value = Path(override)
    else:
        configured = os.environ.get("IDEVGEN_DATA_DIR")
        if configured:
            value = Path(configured)
        else:
            path = Path(config_path or os.environ.get("IDEVGEN_CONFIG") or ROOT / "config.json")
            config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            value = Path(config.get("data_dir") or "data")
    return value if value.is_absolute() else ROOT / value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a consistent SQLite database backup capturing WAL state.\n"
            "Preserves database records and references; does not copy image files."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", type=str, default=None,
                        help="path to config.json (defaults to IDEVGEN_CONFIG or ROOT/config.json)")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="data directory override (defaults to config or data)")
    parser.add_argument("--source", type=Path, default=None,
                        help="source database path (defaults to <data_dir>/idevgen.db)")
    parser.add_argument("-o", "--target", type=Path, default=None,
                        help="destination backup path (defaults to <data_dir>/backups/idevgen-backup-<timestamp>.db)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="overwrite target backup file if it already exists")
    parser.add_argument("--json", action="store_true",
                        help="format status output as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    data_dir = _resolve_data_dir(args.config, args.data_dir)
    source_path = args.source or (data_dir / "idevgen.db")

    if args.target:
        target_path = args.target if args.target.is_absolute() else (ROOT / args.target)
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target_path = data_dir / "backups" / f"idevgen-backup-{timestamp}.db"

    try:
        completed = backup_database(source_path, target_path, overwrite=args.force)
    except (FileNotFoundError, ValueError, FileExistsError, RuntimeError, OSError) as exc:
        print(f"error: database backup failed: {exc}", file=sys.stderr)
        return 1

    report = {
        "status": "ok",
        "source": str(source_path.resolve()),
        "target": str(completed.resolve()),
        "size_bytes": completed.stat().st_size,
        "note": "SQLite database backup completed. Photo files on disk under sessions/ are not included.",
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Database backed up successfully to: {completed}")
        print("Note: Photograph files on disk outside the SQLite database are not included.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
