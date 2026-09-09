"""Consistent SQLite database backup for iDev.Gen.

Uses standard library sqlite3.Connection.backup() to create an atomic,
consistent snapshot of an SQLite database, capturing all committed transactions
including those residing in WAL journals.

Does NOT call db.connect() so pre-upgrade source schemas remain unmodified.
Preserves SQLite database tables, rows, settings, and references; does NOT
copy external photograph files on disk (data/sessions/...).
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Callable
from uuid import uuid4


def backup_database(
    source_path: str | Path,
    target_path: str | Path,
    *,
    overwrite: bool = False,
    pages: int = -1,
    progress: Callable[[sqlite3.Connection, int, int], None] | None = None,
) -> Path:
    """Create a consistent SQLite backup from source_path to target_path.

    Parameters:
        source_path: Path to the existing SQLite database.
        target_path: Destination path for the backup.
        overwrite: When False (default), raises FileExistsError if target exists.
        pages: Number of pages to copy per step (-1 copies all in one step).
        progress: Optional callback invoked by sqlite3 during backup steps.

    Returns:
        The resolved Path of the completed backup.

    Raises:
        FileNotFoundError: If source_path does not exist.
        ValueError: If source_path is not a file, or source and target are identical.
        FileExistsError: If target_path exists and overwrite is False.
        RuntimeError: If SQLite integrity check on the backup fails.
        sqlite3.Error: If a database error occurs during backup.
    """
    src = Path(source_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"source database does not exist: {src}")
    if not src.is_file():
        raise ValueError(f"source database path is not a file: {src}")

    dst = Path(target_path).resolve()
    if src == dst:
        raise ValueError(f"destination cannot be the same file as source: {dst}")

    if dst.exists() and not overwrite:
        raise FileExistsError(f"destination backup file already exists: {dst}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    temp_target = dst.with_name(f"{dst.name}.tmp.{os.getpid()}.{uuid4().hex[:8]}")

    src_conn: sqlite3.Connection | None = None
    dst_conn: sqlite3.Connection | None = None
    try:
        # Open source directly without running schema creation or migrations.
        src_conn = sqlite3.connect(src)
        dst_conn = sqlite3.connect(temp_target)

        src_conn.backup(dst_conn, pages=pages, progress=progress)

        # Flush and verify integrity of the completed backup before presenting it.
        dst_conn.commit()
        cur = dst_conn.execute("PRAGMA integrity_check")
        row = cur.fetchone()
        status = row[0] if row else ""
        if status != "ok":
            raise RuntimeError(f"backup integrity check failed: {status}")

        # Close connections before file operations (critical on Windows where locks prevent rename/replace).
        dst_conn.close()
        dst_conn = None
        src_conn.close()
        src_conn = None

        # Final publish phase
        if not overwrite:
            if not hasattr(os, "link"):
                raise OSError("atomic publication without overwrite requires os.link support")
            try:
                os.link(temp_target, dst)
            except FileExistsError as exc:
                raise FileExistsError(f"destination backup file already exists: {dst}") from exc
            except (AttributeError, NotImplementedError, OSError) as exc:
                raise OSError(f"atomic publication without overwrite failed via os.link: {exc}") from exc
            temp_target.unlink()
        else:
            os.replace(temp_target, dst)

        return dst

    except Exception:
        if dst_conn is not None:
            try:
                dst_conn.close()
            except Exception:
                pass
            dst_conn = None
        if src_conn is not None:
            try:
                src_conn.close()
            except Exception:
                pass
            src_conn = None
        if temp_target.exists():
            try:
                temp_target.unlink()
            except Exception:
                pass
        raise
    finally:
        if dst_conn is not None:
            try:
                dst_conn.close()
            except Exception:
                pass
        if src_conn is not None:
            try:
                src_conn.close()
            except Exception:
                pass
