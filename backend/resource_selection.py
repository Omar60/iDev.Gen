"""Domain and persistence layer for browser-selected resource imports.

Implements OpenSpec Task 1.1 for change simplify-resource-session-workflow:
- Server-owned selection and ordered file-manifest persistence.
- Exact public selection states: open, committing, committed, cancelled, expired.
- Fixed 24-hour selection lifetime and 24-hour post-expiry tombstone window.
- Atomic file-slot and streamed-byte reservations (20 files, 10 MiB per file, 50 MiB aggregate).
- Server-only staging paths, nanosecond mtime decimal text, SHA-256 and fingerprints.
- Revision mutation and preview invalidation primitives.
- Atomic commit claims with single owner and lease deadlines.
- Startup and lazy recovery sweeps.
- Path-safe, retryable cleanup warnings without disclosing private server paths.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import db


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILES_PER_SELECTION: int = 20
MAX_BYTES_PER_FILE: int = 10 * 1024 * 1024       # 10 MiB = 10,485,760 bytes
MAX_BYTES_PER_SELECTION: int = 50 * 1024 * 1024  # 50 MiB = 52,428,800 bytes
SELECTION_LIFETIME_SECONDS: int = 24 * 3600      # 24 hours
TOMBSTONE_RETENTION_SECONDS: int = 24 * 3600     # 24 hours after expires_at
DEFAULT_COMMIT_LEASE_SECONDS: int = 300          # 5 minutes internal claim lease

PUBLIC_SELECTION_STATES: tuple[str, ...] = (
    "open",
    "committing",
    "committed",
    "cancelled",
    "expired",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ResourceSelectionError(Exception):
    """Base exception for resource selection domain errors."""


class SelectionNotFoundError(ResourceSelectionError):
    """Raised when a selection ID does not exist."""


class SelectionStateError(ResourceSelectionError):
    """Raised when an operation is invalid for the selection's current state."""


class SelectionExpiredError(SelectionStateError):
    """Raised when an operation targets an expired selection."""


class SelectionCancelledError(SelectionStateError):
    """Raised when an operation targets a cancelled selection."""


class SelectionNotOpenError(SelectionStateError):
    """Raised when an operation requires an open selection."""


class CommitActiveError(SelectionStateError):
    """Raised when attempting to cancel or mutate a selection with an active commit."""


class IdempotencyConflictError(ResourceSelectionError):
    """Raised when an ID is reused with conflicting metadata or content."""


class StaleRevisionError(ResourceSelectionError):
    """Raised when a mutation provides an outdated selection revision."""


class PreviewMismatchError(ResourceSelectionError):
    """Raised when commit claim token/digest does not match current preview."""


class FileCountLimitExceededError(ResourceSelectionError):
    """Raised when selection exceeds the 20 file reservation limit."""


class FileByteLimitExceededError(ResourceSelectionError):
    """Raised when a file exceeds the 10 MiB limit."""


class AggregateByteLimitExceededError(ResourceSelectionError):
    """Raised when selection aggregate streamed bytes exceed 50 MiB."""


class FileNotFoundInSelectionError(ResourceSelectionError):
    """Raised when a file does not exist or does not belong to the selection."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CommitClaimResult:
    """Result of an atomic commit claim acquisition attempt."""

    status: str  # "acquired", "active_same_tuple", "conflict", "stale_revision", "preview_mismatch", "already_committed", "terminal", "expired"
    commit_token: str | None = None
    lease_deadline: str | None = None
    detail: str = ""
    commit_result: str | None = None


# ---------------------------------------------------------------------------
# Time helpers & injectable clock
# ---------------------------------------------------------------------------

_clock_fn: Callable[[], datetime] | None = None


def set_clock(clock_fn: Callable[[], datetime] | None) -> None:
    """Inject a custom UTC clock function for testing."""
    global _clock_fn
    _clock_fn = clock_fn


def _now_dt() -> datetime:
    if _clock_fn is not None:
        dt = _clock_fn()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return datetime.now(timezone.utc)


def _format_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


def _now_iso(override: str | None = None) -> str:
    if override:
        return override
    return _format_iso(_now_dt())


def _parse_iso(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Staging paths and safe file deletion
# ---------------------------------------------------------------------------

def _database_directory() -> Path:
    row = db.one("PRAGMA database_list")
    database_file = row.get("file") if row else None
    if database_file and database_file != ":memory:":
        return Path(database_file).resolve().parent
    configured = os.environ.get("IDEVGEN_DATA_DIR")
    return Path(configured or "data").resolve()


def get_staging_root() -> Path:
    """Private staging directory beside the active database."""
    return (_database_directory() / "staging" / "selections").resolve()


def _safe_delete_file(path_str: str, staging_root: Path | None = None) -> tuple[bool, str]:
    """Safely delete a staged file.

    Verifies the file is strictly contained within staging_root, is a regular
    file, and unlinks it. Returns (success, safe_warning).
    Never includes private physical paths in the warning message.
    """
    if not path_str:
        return True, ""
    root = (staging_root or get_staging_root()).resolve()
    target = Path(path_str).resolve()

    try:
        target.relative_to(root)
    except ValueError:
        return False, "Target path is outside staging root"

    if target == root:
        return False, "Target path cannot be the staging root"

    if target.is_dir():
        return False, "Target path is a directory, not a staged file"

    candidates = [target]
    if target.suffix == ".part":
        candidates.append(target.with_suffix(".staged"))
    elif target.suffix == ".staged":
        candidates.append(target.with_suffix(".part"))

    errors: list[Exception] = []
    for cand in candidates:
        try:
            cand.unlink(missing_ok=True)
        except OSError as e:
            errors.append(e)

    # Verify no candidate survives on disk
    survived = [c for c in candidates if c.exists()]
    if survived or errors:
        err_name = type(errors[0]).__name__ if errors else "FileSurvives"
        return False, f"Temporary file cleanup failed: {err_name}. Retrying on next recovery sweep."

    # Bounded cleanup: remove parent selection directory if empty
    parent = target.parent
    if parent != root and parent.is_dir():
        try:
            parent.rmdir()
        except OSError:
            pass
    return True, ""


def _reconcile_selection_warning(selection_id: str, now: str) -> None:
    """Reconcile selection cleanup_state and cleanup_warning after file cleanup."""
    sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
    if not sel:
        return
    failed_count = db.one(
        "SELECT COUNT(*) as c FROM resource_selection_file "
        "WHERE selection_id = ? AND (cleanup_state = 'failed' OR status = 'failed')",
        selection_id,
    )["c"]
    if failed_count == 0:
        new_cleanup_st = "cleaned" if sel["state"] in ("cancelled", "expired", "committed") else "none"
        db.conn().execute(
            "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = '', updated_at = ? WHERE selection_id = ?",
            (new_cleanup_st, now, selection_id),
        )
    else:
        f_row = db.one(
            "SELECT cleanup_warning FROM resource_selection_file "
            "WHERE selection_id = ? AND cleanup_warning != '' "
            "ORDER BY id DESC",
            selection_id,
        )
        warning = f_row["cleanup_warning"] if f_row else "Cleanup failed"
        db.conn().execute(
            "UPDATE resource_selection SET cleanup_state = 'failed', cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
            (warning, now, selection_id),
        )


# ---------------------------------------------------------------------------
# Selection Creation and Queries
# ---------------------------------------------------------------------------

def create_selection(
    request_id: str,
    selection_id: str | None = None,
    now_iso: str | None = None,
) -> dict:
    """Create a new import selection or replay existing request_id."""
    if not request_id or not request_id.strip():
        raise ResourceSelectionError("request_id cannot be empty")

    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)
    expires_at = _format_iso(now_dt + timedelta(seconds=SELECTION_LIFETIME_SECONDS))

    with db.transaction():
        existing = db.one("SELECT * FROM resource_selection WHERE request_id = ?", request_id)
        if existing:
            return dict(existing)

        sel_id = selection_id or f"sel_{secrets.token_hex(16)}"
        db.conn().execute(
            "INSERT INTO resource_selection ("
            "    selection_id, request_id, selection_revision, state, "
            "    created_at, updated_at, expires_at, "
            "    total_reserved_bytes, active_file_count, "
            "    cleanup_state, cleanup_warning"
            ") VALUES (?, ?, 0, 'open', ?, ?, ?, 0, 0, 'none', '')",
            (sel_id, request_id, now, now, expires_at),
        )
        return dict(db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sel_id))


def get_selection(
    selection_id: str,
    perform_recovery: bool = True,
    now_iso: str | None = None,
) -> dict | None:
    """Fetch selection record, optionally performing lazy recovery."""
    if perform_recovery:
        return recover_selection(selection_id, now_iso=now_iso)
    row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
    return dict(row) if row else None


def get_selection_manifest(selection_id: str) -> list[dict]:
    """Return ordered list of staged files in the selection manifest."""
    rows = db.q(
        "SELECT * FROM resource_selection_file "
        "WHERE selection_id = ? AND status = 'staged' "
        "ORDER BY order_index ASC, id ASC",
        selection_id,
    )
    return [db.jload(dict(r), "matched_auxiliary_kinds") for r in rows]


def compute_manifest_digest(files: list[dict]) -> str:
    """Compute deterministic SHA-256 digest of ordered manifest entries."""
    entries = []
    for f in files:
        entries.append({
            "file_id": f["file_id"],
            "file_name": f["file_name"],
            "byte_count": f["byte_count"],
            "sha256": f["staged_sha256"],
            "declared_library": f.get("declared_library"),
            "effective_library_key": f.get("effective_library_key"),
            "effective_auxiliary_kind": f.get("effective_auxiliary_kind"),
        })
    canonical_bytes = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()


# ---------------------------------------------------------------------------
# File Slot and Byte Reservations
# ---------------------------------------------------------------------------

def reserve_file_slot(
    selection_id: str,
    upload_id: str,
    file_name: str,
    file_id: str | None = None,
    now_iso: str | None = None,
) -> dict:
    """Atomically reserve one file slot before streaming bytes."""
    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)

    with db.transaction():
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if not sel:
            raise SelectionNotFoundError(f"Selection {selection_id} not found")

        if now_dt >= _parse_iso(sel["expires_at"]):
            raise SelectionExpiredError(f"Selection {selection_id} has expired")

        if sel["state"] != "open":
            raise SelectionNotOpenError(f"Cannot upload to selection in state: {sel['state']}")

        # Idempotency check on (selection_id, upload_id)
        existing_file = db.one(
            "SELECT * FROM resource_selection_file WHERE selection_id = ? AND upload_id = ?",
            selection_id,
            upload_id,
        )
        if existing_file:
            if existing_file["file_name"] != file_name:
                raise IdempotencyConflictError(f"upload_id {upload_id} already exists with different file_name")
            if existing_file["status"] in ("reserved", "staged"):
                return dict(existing_file)
            if existing_file["status"] == "failed":
                ok, warning = _safe_delete_file(existing_file["staged_path"])
                if not ok:
                    raise ResourceSelectionError(
                        f"Cannot reuse upload_id {upload_id}: prior file cleanup failed: {warning}"
                    )
                db.conn().execute("DELETE FROM resource_selection_file WHERE id = ?", (existing_file["id"],))
                _reconcile_selection_warning(selection_id, now)

        # Check 20-file limit
        active_count = db.one(
            "SELECT COUNT(*) as c FROM resource_selection_file WHERE selection_id = ? AND status IN ('reserved', 'staged')",
            selection_id,
        )["c"]
        if active_count >= MAX_FILES_PER_SELECTION:
            raise FileCountLimitExceededError(f"Selection exceeds {MAX_FILES_PER_SELECTION} file limit")

        cur = db.conn().execute(
            "UPDATE resource_selection "
            "SET active_file_count = active_file_count + 1, updated_at = ? "
            "WHERE selection_id = ? AND state = 'open' AND active_file_count < ?",
            (now, selection_id, MAX_FILES_PER_SELECTION),
        )
        if cur.rowcount == 0:
            raise FileCountLimitExceededError(f"Selection exceeds {MAX_FILES_PER_SELECTION} file limit")

        fid = file_id or f"file_{secrets.token_hex(16)}"
        sel_dir = get_staging_root() / selection_id
        sel_dir.mkdir(parents=True, exist_ok=True)
        staged_path = sel_dir / f"{fid}.staged"

        db.conn().execute(
            "INSERT INTO resource_selection_file ("
            "    selection_id, file_id, upload_id, file_name, status, "
            "    byte_count, reserved_bytes, staged_path, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, 'reserved', 0, 0, ?, ?, ?)",
            (selection_id, fid, upload_id, file_name, str(staged_path), now, now),
        )
        return dict(db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid))


def reserve_file_bytes(
    selection_id: str,
    file_id: str,
    chunk_size: int,
    now_iso: str | None = None,
) -> None:
    """Atomically reserve streamed bytes against file and selection limits."""
    if chunk_size < 0:
        raise ResourceSelectionError("chunk_size cannot be negative")
    if chunk_size == 0:
        return

    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)

    with db.transaction():
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if not sel:
            raise SelectionNotFoundError(f"Selection {selection_id} not found")

        if now_dt >= _parse_iso(sel["expires_at"]):
            raise SelectionExpiredError("Selection has expired")

        if sel["state"] != "open":
            raise SelectionNotOpenError(f"Selection is {sel['state']}")

        file_row = db.one(
            "SELECT * FROM resource_selection_file WHERE selection_id = ? AND file_id = ?",
            selection_id,
            file_id,
        )
        if not file_row or file_row["status"] != "reserved":
            raise FileNotFoundInSelectionError(f"File {file_id} not reserved in selection {selection_id}")

        if file_row["reserved_bytes"] + chunk_size > MAX_BYTES_PER_FILE:
            raise FileByteLimitExceededError(f"File exceeds {MAX_BYTES_PER_FILE} bytes limit (10 MiB)")

        if sel["total_reserved_bytes"] + chunk_size > MAX_BYTES_PER_SELECTION:
            raise AggregateByteLimitExceededError(f"Selection exceeds {MAX_BYTES_PER_SELECTION} bytes aggregate limit (50 MiB)")

        cur_sel = db.conn().execute(
            "UPDATE resource_selection "
            "SET total_reserved_bytes = total_reserved_bytes + ?, updated_at = ? "
            "WHERE selection_id = ? AND state = 'open' AND total_reserved_bytes + ? <= ?",
            (chunk_size, now, selection_id, chunk_size, MAX_BYTES_PER_SELECTION),
        )
        if cur_sel.rowcount == 0:
            raise AggregateByteLimitExceededError("Selection exceeds 50 MiB aggregate limit")

        cur_file = db.conn().execute(
            "UPDATE resource_selection_file "
            "SET reserved_bytes = reserved_bytes + ?, updated_at = ? "
            "WHERE file_id = ? AND selection_id = ? AND status = 'reserved' AND reserved_bytes + ? <= ?",
            (chunk_size, now, file_id, selection_id, chunk_size, MAX_BYTES_PER_FILE),
        )
        if cur_file.rowcount == 0:
            raise FileByteLimitExceededError("File exceeds 10 MiB limit")


def stage_file_chunk(
    selection_id: str,
    file_id: str,
    chunk: bytes,
    now_iso: str | None = None,
) -> None:
    """Reserve and append a byte chunk to the staged file."""
    reserve_file_bytes(selection_id, file_id, len(chunk), now_iso=now_iso)

    file_row = db.one("SELECT staged_path FROM resource_selection_file WHERE file_id = ?", file_id)
    if not file_row:
        raise FileNotFoundInSelectionError(f"File {file_id} not found")

    part_path = Path(file_row["staged_path"])
    try:
        with open(part_path, "ab") as f:
            f.write(chunk)
    except Exception:
        abort_file_reservation(selection_id, file_id, reason="Chunk write error", now_iso=now_iso)
        raise


def finalize_staged_file(
    selection_id: str,
    file_id: str,
    declared_library: str | None = None,
    now_iso: str | None = None,
) -> dict:
    """Finalize an uploaded file, record fingerprint, bump revision, and invalidate preview."""
    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)

    with db.transaction():
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if not sel:
            raise SelectionNotFoundError(f"Selection {selection_id} not found")

        if now_dt >= _parse_iso(sel["expires_at"]):
            raise SelectionExpiredError("Selection has expired")

        if sel["state"] != "open":
            raise SelectionNotOpenError(f"Selection is {sel['state']}")

        file_row = db.one(
            "SELECT * FROM resource_selection_file WHERE selection_id = ? AND file_id = ?",
            selection_id,
            file_id,
        )
        if not file_row or file_row["status"] != "reserved":
            raise FileNotFoundInSelectionError(f"File {file_id} not in reserved status")

        staged_path = Path(file_row["staged_path"])
        if not staged_path.is_file():
            raise ResourceSelectionError(f"Staged file {file_id} missing on disk")

        stat_res = staged_path.stat()
        size = stat_res.st_size
        if size != file_row["reserved_bytes"]:
            raise FileByteLimitExceededError("Streamed byte size does not match reservation")

        hasher = hashlib.sha256()
        with open(staged_path, "rb") as f:
            while block := f.read(65536):
                hasher.update(block)
        content_sha256 = hasher.hexdigest()

        mtime_ns = str(stat_res.st_mtime_ns)
        fingerprint = f"{content_sha256}:{size}:{mtime_ns}"

        max_order = db.one(
            "SELECT COALESCE(MAX(order_index), 0) as m FROM resource_selection_file "
            "WHERE selection_id = ? AND status = 'staged'",
            selection_id,
        )["m"]
        next_order = max_order + 1

        db.conn().execute(
            "UPDATE resource_selection_file "
            "SET status = 'staged', "
            "    order_index = ?, "
            "    byte_count = ?, "
            "    staged_path = ?, "
            "    staged_size = ?, "
            "    staged_mtime_ns = ?, "
            "    staged_sha256 = ?, "
            "    fingerprint = ?, "
            "    declared_library = ?, "
            "    updated_at = ? "
            "WHERE id = ?",
            (
                next_order,
                size,
                str(staged_path),
                size,
                mtime_ns,
                content_sha256,
                fingerprint,
                declared_library,
                now,
                file_row["id"],
            ),
        )

        # Invalidate preview and bump revision in the same transaction
        db.conn().execute(
            "UPDATE resource_selection "
            "SET selection_revision = selection_revision + 1, "
            "    preview_token = NULL, "
            "    preview_manifest_digest = NULL, "
            "    preview_committable = NULL, "
            "    preview_report = NULL, "
            "    preview_created_at = NULL, "
            "    updated_at = ? "
            "WHERE selection_id = ? AND state = 'open'",
            (now, selection_id),
        )

        return dict(db.one("SELECT * FROM resource_selection_file WHERE id = ?", file_row["id"]))


def abort_file_reservation(
    selection_id: str,
    file_id: str,
    reason: str = "Upload aborted",
    now_iso: str | None = None,
) -> None:
    """Abort an in-progress file reservation, release counts/bytes, and remove partial file."""
    now = _now_iso(now_iso)

    with db.transaction():
        file_row = db.one(
            "SELECT * FROM resource_selection_file WHERE selection_id = ? AND file_id = ?",
            selection_id,
            file_id,
        )
        if not file_row or file_row["status"] != "reserved":
            return

        db.conn().execute(
            "UPDATE resource_selection "
            "SET total_reserved_bytes = max(0, total_reserved_bytes - ?), "
            "    active_file_count = max(0, active_file_count - 1), "
            "    updated_at = ? "
            "WHERE selection_id = ?",
            (file_row["reserved_bytes"], now, selection_id),
        )

        db.conn().execute(
            "UPDATE resource_selection_file "
            "SET reserved_bytes = 0, byte_count = 0, cleanup_state = 'pending', updated_at = ? "
            "WHERE id = ?",
            (now, file_row["id"]),
        )

        def _do_abort_cleanup():
            ok, warning = _safe_delete_file(file_row["staged_path"])
            with db.transaction():
                if ok:
                    db.conn().execute("DELETE FROM resource_selection_file WHERE id = ?", (file_row["id"],))
                    _reconcile_selection_warning(selection_id, _now_iso())
                else:
                    db.conn().execute(
                        "UPDATE resource_selection_file "
                        "SET status = 'failed', reserved_bytes = 0, byte_count = 0, "
                        "    cleanup_state = 'failed', cleanup_warning = ?, updated_at = ? "
                        "WHERE id = ?",
                        (warning, _now_iso(), file_row["id"]),
                    )
                    db.conn().execute(
                        "UPDATE resource_selection SET cleanup_state = 'failed', cleanup_warning = ? WHERE selection_id = ?",
                        (warning, selection_id),
                    )

        db.on_commit(_do_abort_cleanup)


# ---------------------------------------------------------------------------
# Revision & Preview Invalidation Primitives
# ---------------------------------------------------------------------------

def _bump_revision_and_invalidate_preview(selection_id: str, now_iso: str | None = None) -> int:
    now = _now_iso(now_iso)
    cur = db.conn().execute(
        "UPDATE resource_selection "
        "SET selection_revision = selection_revision + 1, "
        "    preview_token = NULL, "
        "    preview_manifest_digest = NULL, "
        "    preview_committable = NULL, "
        "    preview_report = NULL, "
        "    preview_created_at = NULL, "
        "    updated_at = ? "
        "WHERE selection_id = ? AND state = 'open'",
        (now, selection_id),
    )
    if cur.rowcount == 0:
        raise SelectionNotOpenError("Cannot bump revision on non-open selection")
    row = db.one("SELECT selection_revision FROM resource_selection WHERE selection_id = ?", selection_id)
    return row["selection_revision"]


def remove_staged_file(
    selection_id: str,
    file_id: str,
    expected_revision: int | None = None,
    now_iso: str | None = None,
) -> dict:
    """Remove a staged file, decrement counters, bump revision, and invalidate preview."""
    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)

    with db.transaction():
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if not sel:
            raise SelectionNotFoundError(f"Selection {selection_id} not found")

        if sel["state"] != "open":
            raise SelectionNotOpenError(f"Selection is {sel['state']}")

        if now_dt >= _parse_iso(sel["expires_at"]):
            raise SelectionExpiredError("Selection has expired")

        if expected_revision is not None and sel["selection_revision"] != expected_revision:
            raise StaleRevisionError(f"Revision {expected_revision} is stale, current is {sel['selection_revision']}")

        file_row = db.one(
            "SELECT * FROM resource_selection_file WHERE selection_id = ? AND file_id = ?",
            selection_id,
            file_id,
        )
        if not file_row or file_row["status"] != "staged":
            raise FileNotFoundInSelectionError(f"File {file_id} not found or not staged")

        db.conn().execute(
            "UPDATE resource_selection "
            "SET total_reserved_bytes = max(0, total_reserved_bytes - ?), "
            "    active_file_count = max(0, active_file_count - 1), "
            "    selection_revision = selection_revision + 1, "
            "    preview_token = NULL, "
            "    preview_manifest_digest = NULL, "
            "    preview_committable = NULL, "
            "    preview_report = NULL, "
            "    preview_created_at = NULL, "
            "    updated_at = ? "
            "WHERE selection_id = ?",
            (file_row["byte_count"], now, selection_id),
        )

        db.conn().execute(
            "UPDATE resource_selection_file "
            "SET status = 'removed', byte_count = 0, reserved_bytes = 0, "
            "    cleanup_state = 'pending', updated_at = ? "
            "WHERE id = ?",
            (now, file_row["id"]),
        )

        def _do_remove_cleanup():
            ok, warning = _safe_delete_file(file_row["staged_path"])
            with db.transaction():
                if ok:
                    db.conn().execute(
                        "UPDATE resource_selection_file "
                        "SET cleanup_state = 'cleaned', cleanup_warning = '', updated_at = ? "
                        "WHERE id = ?",
                        (_now_iso(), file_row["id"]),
                    )
                else:
                    db.conn().execute(
                        "UPDATE resource_selection_file "
                        "SET cleanup_state = 'failed', cleanup_warning = ?, updated_at = ? "
                        "WHERE id = ?",
                        (warning, _now_iso(), file_row["id"]),
                    )
                _reconcile_selection_warning(selection_id, _now_iso())

        db.on_commit(_do_remove_cleanup)

    return dict(db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id))


def update_file_targets(
    selection_id: str,
    file_id: str,
    expected_revision: int,
    effective_library_key: str | None = None,
    effective_auxiliary_kind: str | None = None,
    now_iso: str | None = None,
) -> dict:
    """Update effective target fields, bump revision, and invalidate preview."""
    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)

    with db.transaction():
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if not sel:
            raise SelectionNotFoundError(f"Selection {selection_id} not found")

        if sel["state"] != "open":
            raise SelectionNotOpenError(f"Selection is {sel['state']}")

        if now_dt >= _parse_iso(sel["expires_at"]):
            raise SelectionExpiredError("Selection has expired")

        if sel["selection_revision"] != expected_revision:
            raise StaleRevisionError(f"Revision {expected_revision} is stale, current is {sel['selection_revision']}")

        file_row = db.one(
            "SELECT * FROM resource_selection_file WHERE selection_id = ? AND file_id = ?",
            selection_id,
            file_id,
        )
        if not file_row or file_row["status"] != "staged":
            raise FileNotFoundInSelectionError(f"File {file_id} not found or not staged")

        db.conn().execute(
            "UPDATE resource_selection_file "
            "SET effective_library_key = COALESCE(?, effective_library_key), "
            "    effective_auxiliary_kind = COALESCE(?, effective_auxiliary_kind), "
            "    updated_at = ? "
            "WHERE id = ?",
            (effective_library_key, effective_auxiliary_kind, now, file_row["id"]),
        )
        _bump_revision_and_invalidate_preview(selection_id, now_iso=now)
        return dict(db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id))


def save_preview(
    selection_id: str,
    expected_revision: int,
    preview_token: str,
    manifest_digest: str,
    committable: bool,
    report: dict | None = None,
    now_iso: str | None = None,
) -> bool:
    """Bind preview token, manifest digest, committable status, and report to an open selection."""
    now = _now_iso(now_iso)
    report_json = json.dumps(report) if report is not None else None

    with db.transaction():
        cur = db.conn().execute(
            "UPDATE resource_selection "
            "SET preview_token = ?, "
            "    preview_manifest_digest = ?, "
            "    preview_committable = ?, "
            "    preview_report = ?, "
            "    preview_created_at = ?, "
            "    updated_at = ? "
            "WHERE selection_id = ? AND state = 'open' AND selection_revision = ?",
            (
                preview_token,
                manifest_digest,
                1 if committable else 0,
                report_json,
                now,
                now,
                selection_id,
                expected_revision,
            ),
        )
        return cur.rowcount == 1


# ---------------------------------------------------------------------------
# Commit Claim Foundation
# ---------------------------------------------------------------------------

def acquire_commit_claim(
    selection_id: str,
    expected_revision: int,
    preview_token: str,
    manifest_digest: str,
    lease_seconds: int = DEFAULT_COMMIT_LEASE_SECONDS,
    now_iso: str | None = None,
) -> CommitClaimResult:
    """Atomically acquire the single commit claim on the selection."""
    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)

    with db.transaction():
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if not sel:
            raise SelectionNotFoundError(f"Selection {selection_id} not found")

        if now_dt >= _parse_iso(sel["expires_at"]):
            if sel["state"] not in ("committed", "cancelled", "expired"):
                db.conn().execute(
                    "UPDATE resource_selection "
                    "SET state = 'expired', "
                    "    claim_commit_token = NULL, "
                    "    claim_revision = NULL, "
                    "    claim_preview_token = NULL, "
                    "    claim_manifest_digest = NULL, "
                    "    claim_lease_deadline = NULL, "
                    "    cleanup_state = 'pending', "
                    "    updated_at = ? "
                    "WHERE selection_id = ?",
                    (now, selection_id),
                )
                def _do_acquire_expiry_cleanup():
                    all_ok, warning = _clean_selection_files(selection_id)
                    cleanup_st = "cleaned" if all_ok else "failed"
                    with db.transaction():
                        db.conn().execute(
                            "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
                            (cleanup_st, warning, _now_iso(), selection_id),
                        )
                db.on_commit(_do_acquire_expiry_cleanup)
            return CommitClaimResult(status="expired", detail="Selection has expired")

        if sel["state"] == "committed":
            return CommitClaimResult(
                status="already_committed",
                commit_result=sel.get("commit_result"),
                detail="Selection is already committed",
            )

        if sel["state"] in ("cancelled", "expired"):
            return CommitClaimResult(status="terminal", detail=f"Selection is {sel['state']}")

        if sel["state"] == "committing":
            deadline_str = sel.get("claim_lease_deadline")
            deadline_dt = _parse_iso(deadline_str) if deadline_str else now_dt
            if now_dt >= deadline_dt:
                # Expired lease recovered back to open
                db.conn().execute(
                    "UPDATE resource_selection "
                    "SET state = 'open', "
                    "    claim_commit_token = NULL, "
                    "    claim_revision = NULL, "
                    "    claim_preview_token = NULL, "
                    "    claim_manifest_digest = NULL, "
                    "    claim_lease_deadline = NULL, "
                    "    updated_at = ? "
                    "WHERE selection_id = ?",
                    (now, selection_id),
                )
                sel["state"] = "open"
            else:
                is_same = (
                    sel.get("claim_revision") == expected_revision
                    and sel.get("claim_preview_token") == preview_token
                    and sel.get("claim_manifest_digest") == manifest_digest
                )
                if is_same:
                    return CommitClaimResult(
                        status="active_same_tuple",
                        lease_deadline=sel.get("claim_lease_deadline"),
                        detail="Another worker is actively committing the same tuple",
                    )
                return CommitClaimResult(
                    status="conflict",
                    detail="Conflicting active commit claim in progress",
                )

        if sel["selection_revision"] != expected_revision:
            return CommitClaimResult(
                status="stale_revision",
                detail=f"Revision {expected_revision} is stale, current is {sel['selection_revision']}",
            )

        if (
            not sel.get("preview_token")
            or sel["preview_token"] != preview_token
            or sel.get("preview_manifest_digest") != manifest_digest
        ):
            return CommitClaimResult(status="preview_mismatch", detail="Preview token or manifest digest mismatch")

        commit_token = secrets.token_hex(16)
        lease_deadline = _format_iso(now_dt + timedelta(seconds=lease_seconds))

        cur = db.conn().execute(
            "UPDATE resource_selection "
            "SET state = 'committing', "
            "    claim_commit_token = ?, "
            "    claim_revision = ?, "
            "    claim_preview_token = ?, "
            "    claim_manifest_digest = ?, "
            "    claim_lease_deadline = ?, "
            "    updated_at = ? "
            "WHERE selection_id = ? "
            "  AND state = 'open' "
            "  AND selection_revision = ? "
            "  AND preview_token = ? "
            "  AND preview_manifest_digest = ?",
            (
                commit_token,
                expected_revision,
                preview_token,
                manifest_digest,
                lease_deadline,
                now,
                selection_id,
                expected_revision,
                preview_token,
                manifest_digest,
            ),
        )
        if cur.rowcount == 1:
            return CommitClaimResult(
                status="acquired",
                commit_token=commit_token,
                lease_deadline=lease_deadline,
            )
        return CommitClaimResult(status="conflict", detail="Failed to acquire claim due to concurrent state change")


def release_commit_claim(
    selection_id: str,
    commit_token: str,
    now_iso: str | None = None,
) -> bool:
    """Release a commit claim back to open if the token matches (or expired if past lifetime)."""
    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)

    with db.transaction():
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if not sel or sel["state"] != "committing" or sel.get("claim_commit_token") != commit_token:
            return False

        is_expired = now_dt >= _parse_iso(sel["expires_at"])
        new_state = "expired" if is_expired else "open"
        new_cleanup = "pending" if is_expired else sel["cleanup_state"]

        cur = db.conn().execute(
            "UPDATE resource_selection "
            "SET state = ?, "
            "    claim_commit_token = NULL, "
            "    claim_revision = NULL, "
            "    claim_preview_token = NULL, "
            "    claim_manifest_digest = NULL, "
            "    claim_lease_deadline = NULL, "
            "    cleanup_state = ?, "
            "    updated_at = ? "
            "WHERE selection_id = ? AND state = 'committing' AND claim_commit_token = ?",
            (new_state, new_cleanup, now, selection_id, commit_token),
        )
        if cur.rowcount == 1 and is_expired:
            def _do_release_cleanup():
                all_ok, warning = _clean_selection_files(selection_id)
                cleanup_st = "cleaned" if all_ok else "failed"
                with db.transaction():
                    db.conn().execute(
                        "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
                        (cleanup_st, warning, _now_iso(), selection_id),
                    )
            db.on_commit(_do_release_cleanup)
        return cur.rowcount == 1


def record_commit_result(
    selection_id: str,
    commit_token: str,
    result: dict | str,
    now_iso: str | None = None,
) -> bool:
    """Record final committed result atomically, mark terminal committed, and clean staged files."""
    now = _now_iso(now_iso)
    result_json = result if isinstance(result, str) else json.dumps(result)

    with db.transaction():
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if not sel or sel["state"] != "committing" or sel.get("claim_commit_token") != commit_token:
            return False

        cur = db.conn().execute(
            "UPDATE resource_selection "
            "SET state = 'committed', "
            "    commit_result = ?, "
            "    committed_revision = claim_revision, "
            "    committed_preview_token = claim_preview_token, "
            "    committed_manifest_digest = claim_manifest_digest, "
            "    committed_at = ?, "
            "    claim_commit_token = NULL, "
            "    claim_revision = NULL, "
            "    claim_preview_token = NULL, "
            "    claim_manifest_digest = NULL, "
            "    claim_lease_deadline = NULL, "
            "    cleanup_state = 'pending', "
            "    updated_at = ? "
            "WHERE selection_id = ? AND state = 'committing' AND claim_commit_token = ?",
            (result_json, now, now, selection_id, commit_token),
        )
        if cur.rowcount != 1:
            return False

        def _do_commit_cleanup():
            all_ok, warning = _clean_selection_files(selection_id)
            cleanup_st = "cleaned" if all_ok else "failed"
            with db.transaction():
                db.conn().execute(
                    "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
                    (cleanup_st, warning, _now_iso(), selection_id),
                )

        db.on_commit(_do_commit_cleanup)
        return True


def cancel_selection(
    selection_id: str,
    expected_revision: int | None = None,
    now_iso: str | None = None,
) -> dict:
    """Cancel an open selection, mark cancelled, and clean staged files."""
    now = _now_iso(now_iso)

    with db.transaction():
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if not sel:
            raise SelectionNotFoundError(f"Selection {selection_id} not found")

        if sel["state"] == "committing":
            raise CommitActiveError("Cannot cancel while commit is active")

        if sel["state"] in ("committed", "cancelled", "expired"):
            raise SelectionStateError(f"Selection is already {sel['state']}")

        if expected_revision is not None and sel["selection_revision"] != expected_revision:
            raise StaleRevisionError(f"Revision {expected_revision} is stale, current is {sel['selection_revision']}")

        db.conn().execute(
            "UPDATE resource_selection "
            "SET state = 'cancelled', "
            "    claim_commit_token = NULL, "
            "    claim_revision = NULL, "
            "    claim_preview_token = NULL, "
            "    claim_manifest_digest = NULL, "
            "    claim_lease_deadline = NULL, "
            "    cleanup_state = 'pending', "
            "    updated_at = ? "
            "WHERE selection_id = ?",
            (now, selection_id),
        )

        def _do_cancel_cleanup():
            all_ok, warning = _clean_selection_files(selection_id)
            cleanup_st = "cleaned" if all_ok else "failed"
            with db.transaction():
                db.conn().execute(
                    "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
                    (cleanup_st, warning, _now_iso(), selection_id),
                )

        db.on_commit(_do_cancel_cleanup)

    return dict(db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id))


# ---------------------------------------------------------------------------
# Recovery and Cleanup Sweeps
# ---------------------------------------------------------------------------

def _clean_selection_files(selection_id: str) -> tuple[bool, str]:
    """Clean all staged files for a given selection."""
    files = db.q("SELECT * FROM resource_selection_file WHERE selection_id = ?", selection_id)
    all_ok = True
    last_warning = ""
    now = _now_iso()

    for f in files:
        if f.get("staged_path"):
            ok, warning = _safe_delete_file(f["staged_path"])
            with db.transaction():
                if ok:
                    db.conn().execute(
                        "UPDATE resource_selection_file "
                        "SET cleanup_state = 'cleaned', cleanup_warning = '', updated_at = ? "
                        "WHERE id = ?",
                        (now, f["id"]),
                    )
                else:
                    all_ok = False
                    last_warning = warning
                    db.conn().execute(
                        "UPDATE resource_selection_file "
                        "SET cleanup_state = 'failed', cleanup_warning = ?, updated_at = ? "
                        "WHERE id = ?",
                        (warning, now, f["id"]),
                    )
    return all_ok, last_warning


def recover_selection(selection_id: str, now_iso: str | None = None) -> dict | None:
    """Record-local lazy recovery for a single selection."""
    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)

    with db.transaction():
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if not sel:
            return None

        # 1. Recover stale claim lease
        if sel["state"] == "committing":
            deadline_str = sel.get("claim_lease_deadline")
            deadline_dt = _parse_iso(deadline_str) if deadline_str else now_dt
            if now_dt >= deadline_dt:
                if now_dt < _parse_iso(sel["expires_at"]):
                    db.conn().execute(
                        "UPDATE resource_selection "
                        "SET state = 'open', "
                        "    claim_commit_token = NULL, "
                        "    claim_revision = NULL, "
                        "    claim_preview_token = NULL, "
                        "    claim_manifest_digest = NULL, "
                        "    claim_lease_deadline = NULL, "
                        "    updated_at = ? "
                        "WHERE selection_id = ?",
                        (now, selection_id),
                    )
                else:
                    db.conn().execute(
                        "UPDATE resource_selection "
                        "SET state = 'expired', "
                        "    claim_commit_token = NULL, "
                        "    claim_revision = NULL, "
                        "    claim_preview_token = NULL, "
                        "    claim_manifest_digest = NULL, "
                        "    claim_lease_deadline = NULL, "
                        "    cleanup_state = 'pending', "
                        "    updated_at = ? "
                        "WHERE selection_id = ?",
                        (now, selection_id),
                    )
                    def _do_recover_stale_claim_cleanup():
                        all_ok, warning = _clean_selection_files(selection_id)
                        cleanup_st = "cleaned" if all_ok else "failed"
                        with db.transaction():
                            db.conn().execute(
                                "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
                                (cleanup_st, warning, _now_iso(), selection_id),
                            )
                    db.on_commit(_do_recover_stale_claim_cleanup)

        # 2. Check 24-hour fixed expiry on open selections
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if sel and sel["state"] == "open" and now_dt >= _parse_iso(sel["expires_at"]):
            db.conn().execute(
                "UPDATE resource_selection SET state = 'expired', cleanup_state = 'pending', updated_at = ? WHERE selection_id = ?",
                (now, selection_id),
            )
            def _do_recover_expiry_cleanup():
                all_ok, warning = _clean_selection_files(selection_id)
                cleanup_st = "cleaned" if all_ok else "failed"
                with db.transaction():
                    db.conn().execute(
                        "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
                        (cleanup_st, warning, _now_iso(), selection_id),
                    )
            db.on_commit(_do_recover_expiry_cleanup)

        # 3. Clean pending/failed files in open selection (aborted uploads or removed files)
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if sel and sel["state"] == "open":
            failed_files = db.q(
                "SELECT * FROM resource_selection_file "
                "WHERE selection_id = ? AND (cleanup_state IN ('pending', 'failed') OR status = 'failed')",
                selection_id,
            )
            if failed_files:
                def _do_recover_failed_files():
                    for ff in failed_files:
                        ok, warning = _safe_delete_file(ff["staged_path"])
                        with db.transaction():
                            if ok:
                                if ff["status"] == "removed":
                                    db.conn().execute(
                                        "UPDATE resource_selection_file SET cleanup_state = 'cleaned', cleanup_warning = '', updated_at = ? WHERE id = ?",
                                        (_now_iso(), ff["id"]),
                                    )
                                else:
                                    db.conn().execute("DELETE FROM resource_selection_file WHERE id = ?", (ff["id"],))
                            else:
                                db.conn().execute(
                                    "UPDATE resource_selection_file SET cleanup_state = 'failed', cleanup_warning = ?, updated_at = ? WHERE id = ?",
                                    (warning, _now_iso(), ff["id"]),
                                )
                    with db.transaction():
                        _reconcile_selection_warning(selection_id, _now_iso())
                db.on_commit(_do_recover_failed_files)

        # 4. Retry cleanup if terminal and uncleaned
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if sel and sel["state"] in ("cancelled", "expired", "committed") and sel["cleanup_state"] != "cleaned":
            def _do_recover_terminal_cleanup():
                all_ok, warning = _clean_selection_files(selection_id)
                cleanup_st = "cleaned" if all_ok else "failed"
                with db.transaction():
                    db.conn().execute(
                        "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
                        (cleanup_st, warning, _now_iso(), selection_id),
                    )
            db.on_commit(_do_recover_terminal_cleanup)

        # 5. Check purge window: 24 hours after expires_at
        sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
        if sel and sel["state"] in ("cancelled", "expired") and sel["cleanup_state"] == "cleaned":
            purge_boundary = _parse_iso(sel["expires_at"]) + timedelta(seconds=TOMBSTONE_RETENTION_SECONDS)
            if now_dt >= purge_boundary:
                db.conn().execute("DELETE FROM resource_selection WHERE selection_id = ?", (selection_id,))
                return None

    final_row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
    return dict(final_row) if final_row else None


def startup_recovery(limit: int = 50, now_iso: str | None = None) -> dict:
    """Bounded startup sweep invoked on application boot."""
    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)
    purge_cutoff = _format_iso(now_dt - timedelta(seconds=TOMBSTONE_RETENTION_SECONDS))
    summary = {
        "incomplete_uploads_discarded": 0,
        "stale_claims_reopened": 0,
        "stale_claims_expired": 0,
        "overdue_selections_expired": 0,
        "cleanup_retries": 0,
        "purged_selections": 0,
    }

    # 1. Discard incomplete upload reservations (status == 'reserved')
    reserved_files = db.q(
        "SELECT * FROM resource_selection_file WHERE status = 'reserved' LIMIT ?",
        limit,
    )
    if reserved_files:
        with db.transaction():
            for rf in reserved_files:
                summary["incomplete_uploads_discarded"] += 1
                db.conn().execute(
                    "UPDATE resource_selection "
                    "SET total_reserved_bytes = max(0, total_reserved_bytes - ?), "
                    "    active_file_count = max(0, active_file_count - 1), "
                    "    updated_at = ? "
                    "WHERE selection_id = ?",
                    (rf["reserved_bytes"], now, rf["selection_id"]),
                )
                db.conn().execute(
                    "UPDATE resource_selection_file SET cleanup_state = 'pending', updated_at = ? WHERE id = ?",
                    (now, rf["id"]),
                )
        for rf in reserved_files:
            ok, warning = _safe_delete_file(rf["staged_path"])
            with db.transaction():
                if ok:
                    db.conn().execute("DELETE FROM resource_selection_file WHERE id = ?", (rf["id"],))
                    _reconcile_selection_warning(rf["selection_id"], now)
                else:
                    db.conn().execute(
                        "UPDATE resource_selection_file "
                        "SET status = 'failed', reserved_bytes = 0, byte_count = 0, "
                        "    cleanup_state = 'failed', cleanup_warning = ?, updated_at = ? "
                        "WHERE id = ?",
                        (warning, now, rf["id"]),
                    )
                    db.conn().execute(
                        "UPDATE resource_selection SET cleanup_state = 'failed', cleanup_warning = ? WHERE selection_id = ?",
                        (warning, rf["selection_id"]),
                    )

    # 1.5. Clean pending/failed files in open selections (aborted uploads or removed files)
    failed_upload_files = db.q(
        "SELECT rf.* FROM resource_selection_file rf "
        "JOIN resource_selection rs ON rs.selection_id = rf.selection_id "
        "WHERE rs.state = 'open' AND (rf.status = 'failed' OR rf.cleanup_state IN ('pending', 'failed')) "
        "LIMIT ?",
        limit,
    )
    for ff in failed_upload_files:
        ok, warning = _safe_delete_file(ff["staged_path"])
        with db.transaction():
            if ok:
                if ff["status"] == "removed":
                    db.conn().execute(
                        "UPDATE resource_selection_file SET cleanup_state = 'cleaned', cleanup_warning = '', updated_at = ? WHERE id = ?",
                        (now, ff["id"]),
                    )
                else:
                    db.conn().execute("DELETE FROM resource_selection_file WHERE id = ?", (ff["id"],))
                _reconcile_selection_warning(ff["selection_id"], now)
                summary["cleanup_retries"] += 1
            else:
                db.conn().execute(
                    "UPDATE resource_selection_file "
                    "SET cleanup_state = 'failed', cleanup_warning = ?, updated_at = ? "
                    "WHERE id = ?",
                    (warning, now, ff["id"]),
                )
                _reconcile_selection_warning(ff["selection_id"], now)

    # 2. Prior process committing claims are stale
    committing = db.q(
        "SELECT * FROM resource_selection WHERE state = 'committing' LIMIT ?",
        limit,
    )
    stale_to_expire: list[str] = []
    if committing:
        with db.transaction():
            for sel in committing:
                if now_dt < _parse_iso(sel["expires_at"]):
                    db.conn().execute(
                        "UPDATE resource_selection "
                        "SET state = 'open', "
                        "    claim_commit_token = NULL, "
                        "    claim_revision = NULL, "
                        "    claim_preview_token = NULL, "
                        "    claim_manifest_digest = NULL, "
                        "    claim_lease_deadline = NULL, "
                        "    updated_at = ? "
                        "WHERE selection_id = ?",
                        (now, sel["selection_id"]),
                    )
                    summary["stale_claims_reopened"] += 1
                else:
                    db.conn().execute(
                        "UPDATE resource_selection "
                        "SET state = 'expired', "
                        "    claim_commit_token = NULL, "
                        "    claim_revision = NULL, "
                        "    claim_preview_token = NULL, "
                        "    claim_manifest_digest = NULL, "
                        "    claim_lease_deadline = NULL, "
                        "    cleanup_state = 'pending', "
                        "    updated_at = ? "
                        "WHERE selection_id = ?",
                        (now, sel["selection_id"]),
                    )
                    stale_to_expire.append(sel["selection_id"])
                    summary["stale_claims_expired"] += 1
        for sid in stale_to_expire:
            all_ok, warning = _clean_selection_files(sid)
            cleanup_st = "cleaned" if all_ok else "failed"
            with db.transaction():
                db.conn().execute(
                    "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
                    (cleanup_st, warning, now, sid),
                )

    # 3. Overdue open selections
    overdue_open = db.q(
        "SELECT * FROM resource_selection WHERE state = 'open' AND expires_at <= ? LIMIT ?",
        now,
        limit,
    )
    if overdue_open:
        with db.transaction():
            for sel in overdue_open:
                db.conn().execute(
                    "UPDATE resource_selection SET state = 'expired', cleanup_state = 'pending', updated_at = ? WHERE selection_id = ?",
                    (now, sel["selection_id"]),
                )
                summary["overdue_selections_expired"] += 1
        for sel in overdue_open:
            all_ok, warning = _clean_selection_files(sel["selection_id"])
            cleanup_st = "cleaned" if all_ok else "failed"
            with db.transaction():
                db.conn().execute(
                    "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
                    (cleanup_st, warning, now, sel["selection_id"]),
                )

    # 4. Retry uncleaned terminal selections
    uncleaned = db.q(
        "SELECT * FROM resource_selection "
        "WHERE state IN ('cancelled', 'expired', 'committed') "
        "  AND cleanup_state IN ('none', 'pending', 'failed') "
        "LIMIT ?",
        limit,
    )
    for sel in uncleaned:
        all_ok, warning = _clean_selection_files(sel["selection_id"])
        cleanup_st = "cleaned" if all_ok else "failed"
        with db.transaction():
            db.conn().execute(
                "UPDATE resource_selection SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE selection_id = ?",
                (cleanup_st, warning, now, sel["selection_id"]),
            )
        summary["cleanup_retries"] += 1

    # 5. Purge eligible selections past tombstone window
    terminal_purged = db.q(
        "SELECT * FROM resource_selection "
        "WHERE state IN ('cancelled', 'expired') "
        "  AND cleanup_state = 'cleaned' "
        "  AND expires_at <= ? "
        "LIMIT ?",
        purge_cutoff,
        limit,
    )
    if terminal_purged:
        with db.transaction():
            for sel in terminal_purged:
                db.conn().execute("DELETE FROM resource_selection WHERE selection_id = ?", (sel["selection_id"],))
                summary["purged_selections"] += 1

    # 6. Reconcile counters for remaining open selections
    open_selections = db.q("SELECT selection_id FROM resource_selection WHERE state = 'open' LIMIT ?", limit)
    with db.transaction():
        for o_sel in open_selections:
            sid = o_sel["selection_id"]
            stats = db.one(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(reserved_bytes), 0) as tot "
                "FROM resource_selection_file "
                "WHERE selection_id = ? AND status IN ('reserved', 'staged')",
                sid,
            )
            db.conn().execute(
                "UPDATE resource_selection "
                "SET active_file_count = ?, total_reserved_bytes = ? "
                "WHERE selection_id = ?",
                (stats["cnt"], stats["tot"], sid),
            )

    return summary


def opportunistic_sweep(limit: int = 10, now_iso: str | None = None) -> dict:
    """Bounded opportunistic sweep for status/mutation access."""
    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)
    purge_cutoff = _format_iso(now_dt - timedelta(seconds=TOMBSTONE_RETENTION_SECONDS))

    rows = db.q(
        "SELECT selection_id FROM resource_selection "
        "WHERE (state = 'open' AND ("
        "         expires_at <= ? "
        "         OR cleanup_state = 'failed' "
        "         OR EXISTS ("
        "             SELECT 1 FROM resource_selection_file rf "
        "             WHERE rf.selection_id = resource_selection.selection_id "
        "               AND (rf.status = 'failed' OR rf.cleanup_state IN ('pending', 'failed'))"
        "         )"
        "      )) "
        "   OR (state = 'committing' AND claim_lease_deadline <= ?) "
        "   OR (state IN ('cancelled', 'expired', 'committed') AND cleanup_state != 'cleaned') "
        "   OR (state IN ('cancelled', 'expired') AND cleanup_state = 'cleaned' AND expires_at <= ?) "
        "ORDER BY "
        "  CASE "
        "    WHEN cleanup_state = 'failed' "
        "      OR EXISTS ("
        "          SELECT 1 FROM resource_selection_file rf "
        "          WHERE rf.selection_id = resource_selection.selection_id "
        "            AND (rf.status = 'failed' OR rf.cleanup_state IN ('pending', 'failed'))"
        "      ) THEN 1 "
        "    WHEN state = 'committing' AND claim_lease_deadline <= ? THEN 2 "
        "    WHEN state = 'open' AND expires_at <= ? THEN 3 "
        "    WHEN state IN ('cancelled', 'expired', 'committed') AND cleanup_state != 'cleaned' THEN 4 "
        "    ELSE 5 "
        "  END ASC, "
        "  expires_at ASC "
        "LIMIT ?",
        now,
        now,
        purge_cutoff,
        now,
        now,
        limit,
    )
    recovered = 0
    for r in rows:
        recover_selection(r["selection_id"], now_iso=now)
        recovered += 1
    return {"swept": recovered}
