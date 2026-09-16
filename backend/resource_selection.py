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
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import db
import resource_import
import resource_parser
import resource_service
import sys

if __name__ == "backend.resource_selection" and "resource_selection" not in sys.modules:
    sys.modules["resource_selection"] = sys.modules[__name__]
elif __name__ == "resource_selection" and "backend.resource_selection" not in sys.modules:
    sys.modules["backend.resource_selection"] = sys.modules[__name__]


# Sentinel to distinguish an omitted target field from an explicit null assignment.
TARGET_OMITTED: object = object()


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

# Number.MAX_SAFE_INTEGER. The schema CHECK on ``selection_revision``
# already pins the value to this range, so a revision read out of the
# database never exceeds it; the constant is the single source of truth
# for any guard that validates a public numeric before serializing.
MAX_SAFE_INTEGER: int = 9007199254740991


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ResourceSelectionError(Exception):
    """Base exception for resource selection domain errors."""


class InvalidTargetError(ResourceSelectionError, ValueError):
    """Raised when an effective target assignment is invalid."""


class SelectionStateInvalidError(ResourceSelectionError):
    """Raised when persisted selection or file state is invalid or inconsistent."""


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


class CommitConflictError(ResourceSelectionError):
    """Raised when a conflicting commit claim is active."""


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


# Test hooks for deterministic race / failure simulation
_commit_failure_injector: Callable[[int], None] | None = None
_commit_before_tx_hook: Callable[[], None] | None = None
_commit_after_tx_hook: Callable[[], None] | None = None


def set_commit_failure_injector(injector: Callable[[int], None] | None) -> None:
    """Inject a failure callback during canonical commit persistence for testing."""
    global _commit_failure_injector
    _commit_failure_injector = injector


def set_commit_before_tx_hook(hook: Callable[[], None] | None) -> None:
    """Inject a callback invoked before entering the commit transaction for testing."""
    global _commit_before_tx_hook
    _commit_before_tx_hook = hook


def set_commit_after_tx_hook(hook: Callable[[], None] | None) -> None:
    """Inject a callback invoked immediately after the commit transaction commits for testing."""
    global _commit_after_tx_hook
    _commit_after_tx_hook = hook


_commit_before_record_result_hook: Callable[[], None] | None = None


def set_commit_before_record_result_hook(hook: Callable[[], None] | None) -> None:
    """Inject a callback invoked after resources/coverage but before record_commit_result for testing."""
    global _commit_before_record_result_hook
    _commit_before_record_result_hook = hook


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
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception as exc:
        raise SelectionStateInvalidError(f"Invalid ISO timestamp: {s}") from exc


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
    """Create a new import selection or replay existing request_id.

    Kept for backwards compatibility with Task 1.1 callers and tests;
    new callers SHOULD use :func:`create_or_replay_selection` which
    returns a deterministic ``was_new`` disposition and authoritative view.
    """
    view, _was_new = create_or_replay_selection(
        request_id=request_id,
        selection_id=selection_id,
        now_iso=now_iso,
    )
    row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", view["selection_id"])
    if not row:
        raise SelectionStateInvalidError("Selection unexpectedly missing after create or replay")
    return dict(row)


def normalize_client_uuid(value: Any) -> str:
    """Validate and canonicalize a client UUID string into lowercase hyphenated UUID format."""
    if not isinstance(value, str) or isinstance(value, bool):
        raise ValueError("UUID must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("UUID cannot be empty")
    try:
        parsed = uuid.UUID(cleaned)
    except Exception as exc:
        raise ValueError("Invalid UUID") from exc
    return str(parsed).lower()


def normalize_display_filename(name: Any) -> str:
    """Normalize client-provided filename to a safe final display component."""
    if not isinstance(name, str) or isinstance(name, bool):
        return "upload.bin"
    sanitized = "".join(c for c in name if ord(c) >= 32 and ord(c) != 127).strip()
    if not sanitized:
        return "upload.bin"
    if sanitized.lower().startswith("file:"):
        sanitized = sanitized[5:].lstrip("/")
    sanitized = sanitized.replace("\\", "/")
    parts = [p.strip() for p in sanitized.split("/") if p.strip()]
    candidate = parts[-1] if parts else ""
    if ":" in candidate:
        candidate = candidate.split(":")[-1].strip()
    if not candidate or not candidate.strip("."):
        return "upload.bin"
    if not _is_safe_display_filename(candidate):
        return "upload.bin"
    return candidate


def validate_json_revision(value: Any) -> int:
    """Validate a JSON revision value.

    Accepts only a Python int that is not bool and lies in 0..MAX_SAFE_INTEGER.
    Rejects strings, booleans, integral/fractional floats, null, and all other types.
    """
    if isinstance(value, bool) or not isinstance(value, int) or isinstance(value, float):
        raise ValueError("expected_revision must be an integer")
    if value < 0 or value > MAX_SAFE_INTEGER:
        raise ValueError(f"expected_revision must be in 0..{MAX_SAFE_INTEGER}")
    return value


def validate_query_revision(value: Any) -> int:
    """Validate a query parameter revision value.

    Accepts only an ASCII decimal integer representation in 0..MAX_SAFE_INTEGER.
    Rejects decimal points, exponent notation, signs, boolean words, whitespace-only
    input, and non-decimal forms.
    """
    if not isinstance(value, str) or isinstance(value, bool):
        raise ValueError("expected_revision must be an integer")
    if not value or not value.isascii() or not value.isdigit():
        raise ValueError("expected_revision must be an integer")
    try:
        candidate = int(value, 10)
    except ValueError as exc:
        raise ValueError("expected_revision must be an integer") from exc
    if candidate < 0 or candidate > MAX_SAFE_INTEGER:
        raise ValueError(f"expected_revision must be in 0..{MAX_SAFE_INTEGER}")
    return candidate


def validate_target_library_key(key: Any) -> str:
    """Validate effective_library_key according to OpenSpec identity grammar.

    - 1 to 128 characters
    - no leading or trailing whitespace
    - no ASCII control characters (0x00-0x1F, 0x7F)
    - no slash or backslash
    - not '.' or '..'
    - exact type str (no boolean, numeric coercion, or str subclasses)
    """
    if type(key) is not str:
        raise InvalidTargetError("effective_library_key must be a string")
    if not (1 <= len(key) <= 128):
        raise InvalidTargetError("effective_library_key must be between 1 and 128 characters")
    if key != key.strip():
        raise InvalidTargetError("effective_library_key cannot contain leading or trailing whitespace")
    if key in (".", ".."):
        raise InvalidTargetError("effective_library_key cannot be '.' or '..'")
    if "/" in key or "\\" in key:
        raise InvalidTargetError("effective_library_key cannot contain '/' or '\\'")
    for ch in key:
        code = ord(ch)
        if (0 <= code <= 31) or code == 127:
            raise InvalidTargetError("effective_library_key cannot contain ASCII control characters")
    return key


def validate_target_auxiliary_kind(kind: Any) -> str:
    """Validate effective_auxiliary_kind against canonical allowlist.

    - exact member of resource_parser.ALL_AUXILIARY_KINDS
    - case-sensitive
    - exact type str (no coercion, no str subclasses)
    """
    if type(kind) is not str:
        raise InvalidTargetError("effective_auxiliary_kind must be a string")
    if kind not in resource_parser.ALL_AUXILIARY_KINDS:
        raise InvalidTargetError(
            f"effective_auxiliary_kind must be one of {list(resource_parser.ALL_AUXILIARY_KINDS)}"
        )
    return kind


def create_or_replay_selection(
    request_id: str,
    selection_id: str | None = None,
    now_iso: str | None = None,
) -> tuple[dict, bool]:
    """Create or replay a selection in a single atomic transaction.

    Returns ``(selection_view, was_new)``. ``was_new`` is ``True`` only
    when this call inserted a new ``resource_selection`` row. A replay
    of the same ``request_id`` returns ``was_new=False`` even under
    concurrent load: the SELECT-then-INSERT is wrapped in a single
    transaction with an explicit check, and the unique constraint on
    ``request_id`` is the second line of defense. The return value is
    the authoritative, canonical ``SelectionView`` projected directly
    from durable state.
    """
    if not request_id or not str(request_id).strip():
        raise ResourceSelectionError("request_id cannot be empty")

    try:
        canonical_request_id = normalize_client_uuid(request_id)
    except ValueError:
        canonical_request_id = str(request_id).strip()

    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)
    expires_at = _format_iso(now_dt + timedelta(seconds=SELECTION_LIFETIME_SECONDS))

    with db._tx_lock:
        with db.transaction():
            existing = db.one(
                "SELECT * FROM resource_selection WHERE request_id = ?",
                canonical_request_id,
            )
            if existing:
                recovered_sel = _recover_selection_in_tx(existing["selection_id"], now_dt=now_dt, now_iso=now)
                if recovered_sel is None:
                    raise SelectionStateInvalidError("Selection was purged or state is invalid")
                target_sid = existing["selection_id"]
                was_new = False
            else:
                sel_id = selection_id or f"sel_{secrets.token_hex(16)}"
                try:
                    db.conn().execute(
                        "INSERT INTO resource_selection ("
                        "    selection_id, request_id, selection_revision, state, "
                        "    created_at, updated_at, expires_at, "
                        "    total_reserved_bytes, active_file_count, "
                        "    cleanup_state, cleanup_warning"
                        ") VALUES (?, ?, 0, 'open', ?, ?, ?, 0, 0, 'none', '')",
                        (sel_id, canonical_request_id, now, now, expires_at),
                    )
                    target_sid = sel_id
                    was_new = True
                except sqlite3.IntegrityError:
                    # A concurrent request inserted the same request_id between
                    # our SELECT and INSERT. Re-read and treat as replay.
                    existing = db.one(
                        "SELECT * FROM resource_selection WHERE request_id = ?",
                        canonical_request_id,
                    )
                    if existing is None:
                        raise
                    recovered_sel = _recover_selection_in_tx(existing["selection_id"], now_dt=now_dt, now_iso=now)
                    if recovered_sel is None:
                        raise SelectionStateInvalidError("Selection was purged or state is invalid")
                    target_sid = existing["selection_id"]
                    was_new = False

        target_row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", target_sid)
        if not target_row:
            raise SelectionStateInvalidError("Selection unexpectedly missing after commit")
        target_sel = dict(target_row)

        view = _project_selection_view(target_sel)
        if view is None:
            raise SelectionStateInvalidError("Selection view is None")

    return view, was_new


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


def compute_manifest_digest(
    files: list[dict],
    selection_revision: int | None = None,
) -> str:
    """Compute deterministic SHA-256 digest of ordered manifest entries and optional revision."""
    entries = []
    for f in files:
        entries.append({
            "file_id": f["file_id"],
            "file_name": f["file_name"],
            "order_index": f.get("order_index", 0),
            "byte_count": f["byte_count"],
            "staged_size": f.get("staged_size", f["byte_count"]),
            "staged_mtime_ns": str(f.get("staged_mtime_ns", "")),
            "staged_sha256": f.get("staged_sha256", f.get("sha256", "")),
            "fingerprint": f.get("fingerprint", ""),
            "declared_library": f.get("declared_library"),
            "effective_library_key": f.get("effective_library_key"),
            "effective_auxiliary_kind": f.get("effective_auxiliary_kind"),
        })
    payload: dict[str, Any] = {"files": entries}
    if selection_revision is not None:
        payload["selection_revision"] = selection_revision
    canonical_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
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
    """Atomically reserve one file slot before streaming bytes.

    The returned dict carries a ``_disposition`` key the public route
    switches on to decide between a fresh upload, a completed same-ID
    replay, an active same-ID upload (still being streamed), or a
    cleanup retry. The disposition is set inside the same transaction
    that performs the reservation, so concurrent calls with the same
    ``upload_id`` cannot race past it.
    """
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
                raise IdempotencyConflictError(
                    f"upload_id {upload_id} already exists with different file_name"
                )
            if existing_file["status"] == "reserved":
                # Same upload_id is still streaming. The public route
                # MUST refuse the second stream with a stable, non-
                # mutating 409 idempotency_conflict. The disposition is
                # the single switch the route reads.
                row = dict(existing_file)
                row["_disposition"] = "active"
                return row
            if existing_file["status"] == "staged":
                # The file is already complete. The route reads the
                # stored digest and compares the incoming body.
                row = dict(existing_file)
                row["_disposition"] = "completed"
                return row
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
        row = dict(db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid))
        row["_disposition"] = "fresh"
        return row


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
    effective_library_key: Any = TARGET_OMITTED,
    effective_auxiliary_kind: Any = TARGET_OMITTED,
    now_iso: str | None = None,
) -> dict:
    """Update effective target fields, bump revision, and invalidate preview.

    Validates targets and expected_revision before entering the transaction.
    If validation fails, zero mutation occurs.
    """
    try:
        expected_revision = validate_json_revision(expected_revision)
    except ValueError as exc:
        raise InvalidTargetError(str(exc)) from exc

    if effective_library_key is TARGET_OMITTED and effective_auxiliary_kind is TARGET_OMITTED:
        raise InvalidTargetError(
            "PATCH body must include at least one of 'effective_library_key' or 'effective_auxiliary_kind'"
        )

    if effective_library_key is not TARGET_OMITTED and effective_library_key is not None:
        validate_target_library_key(effective_library_key)

    if effective_auxiliary_kind is not TARGET_OMITTED and effective_auxiliary_kind is not None:
        validate_target_auxiliary_kind(effective_auxiliary_kind)

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

        set_clauses = ["updated_at = ?"]
        params: list[Any] = [now]
        if effective_library_key is not TARGET_OMITTED:
            set_clauses.append("effective_library_key = ?")
            params.append(effective_library_key)
        if effective_auxiliary_kind is not TARGET_OMITTED:
            set_clauses.append("effective_auxiliary_kind = ?")
            params.append(effective_auxiliary_kind)
        params.append(file_row["id"])

        sql = f"UPDATE resource_selection_file SET {', '.join(set_clauses)} WHERE id = ?"
        db.conn().execute(sql, params)
        _bump_revision_and_invalidate_preview(selection_id, now_iso=now)
        return dict(db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id))


def save_preview(
    selection_id: str,
    expected_revision: int,
    preview_token: str,
    manifest_digest: str,
    committable: bool,
    report: dict | str,
    now_iso: str | None = None,
) -> bool:
    """Bind preview token, manifest digest, committable status, and report to an open selection."""
    if type(selection_id) is not str or not selection_id or not _is_safe_public_string(selection_id):
        raise ValueError("selection_id is invalid")
    validate_json_revision(expected_revision)
    if type(preview_token) is not str or not preview_token or not _is_safe_public_string(preview_token):
        raise ValueError("preview_token is invalid")
    if (
        type(manifest_digest) is not str
        or len(manifest_digest) != 64
        or not all(c in "0123456789abcdef" for c in manifest_digest)
    ):
        raise ValueError("manifest_digest must be 64 lowercase hex characters")
    if type(committable) is not bool:
        raise TypeError("committable must be a boolean")
    if report is None or report == "":
        raise ValueError("report cannot be null or empty")
    if type(report) is not str and type(report) is not dict:
        raise TypeError("report must be a dict or JSON string")

    validated_report = resource_service.validate_safe_report(report, expected_phase="preview")
    report_json = json.dumps(validated_report)

    if now_iso is not None:
        _parse_iso(now_iso)
    now = _now_iso(now_iso)

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
    if type(selection_id) is not str or not selection_id or not _is_safe_public_string(selection_id):
        raise ValueError("selection_id is invalid")
    if type(commit_token) is not str or not commit_token or not _is_safe_public_string(commit_token):
        raise ValueError("commit_token is invalid")
    if result is None or result == "":
        raise ValueError("commit result cannot be null or empty")
    if type(result) is not str and type(result) is not dict:
        raise TypeError("commit result must be a dict or JSON string")

    validated_result = resource_service.validate_safe_report(result, expected_phase="commit")
    result_json = json.dumps(validated_result)

    if now_iso is not None:
        _parse_iso(now_iso)
    now = _now_iso(now_iso)

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
    """Clean all staged files and any associated preview attestation for a given selection."""
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

    # Clean browser attestation if selection is in a terminal state
    sel = db.one(
        "SELECT state, preview_token, committed_preview_token FROM resource_selection WHERE selection_id = ?",
        selection_id,
    )
    if sel and sel["state"] in ("committed", "cancelled", "expired"):
        tok = sel.get("committed_preview_token") or sel.get("preview_token")
        if tok:
            tok_ok, tok_warn = resource_service.delete_attestation(tok)
            if not tok_ok:
                all_ok = False
                if not last_warning:
                    last_warning = tok_warn

    return all_ok, last_warning


def _recover_selection_in_tx(
    selection_id: str,
    now_dt: datetime,
    now_iso: str,
) -> dict | None:
    """Record-local lazy recovery logic executed within an existing transaction."""
    now = now_iso
    sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
    if not sel:
        return None

    # 1. Recover stale claim lease
    if sel["state"] == "committing":
        claim_tok = sel.get("claim_commit_token")
        claim_rev = sel.get("claim_revision")
        claim_prev_tok = sel.get("claim_preview_token")
        claim_prev_dig = sel.get("claim_manifest_digest")
        deadline_str = sel.get("claim_lease_deadline")

        claim_tuple = (claim_tok, claim_rev, claim_prev_tok, claim_prev_dig, deadline_str)
        if not any(x is None for x in claim_tuple):
            try:
                deadline_dt = _parse_iso(deadline_str)
            except Exception:
                deadline_dt = None
            if deadline_dt is not None and now_dt >= deadline_dt:
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


def recover_selection(selection_id: str, now_iso: str | None = None) -> dict | None:
    """Record-local lazy recovery for a single selection."""
    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)

    with db.transaction():
        res = _recover_selection_in_tx(selection_id, now_dt=now_dt, now_iso=now)
        if res is None:
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


# ---------------------------------------------------------------------------
# Public SelectionView Projection (Task 1.2)
# ---------------------------------------------------------------------------
#
# The selection row in ``resource_selection`` and the per-file rows in
# ``resource_selection_file`` carry internal evidence that must NEVER reach
# the public boundary: physical staging paths, SQLite surrogate IDs,
# ``staged_mtime_ns``, fingerprint blobs, claim tokens, cleanup internals,
# reservation counters, staged bytes, raw SHA-256 evidence fields, etc.
# The schema's CHECK constraints already gate the unsafe integers to
# ``0..MAX_SAFE_INTEGER`` (and the per-file/aggregate byte caps), but the
# *fields themselves* would still travel if the route returned the raw
# row dict. The projection below is the only public shape: every route
# under ``/api/resources/import-selections`` serializes through
# ``build_selection_view`` and nothing else.
#
# The view is intentionally narrow. It does NOT expose:
#   * any surrogate primary key;
#   * any path under ``staged_path`` or its parent directories;
#   * any field that names ``mtime_ns``, ``fingerprint``, ``sha256``,
#     ``claim_*``, ``*_lease_deadline``, ``cleanup_*``, ``reserved_*``,
#     ``purged_*``, ``byte_count`` of the internal reservation counter
#     (only the public ``byte_count`` of the finalized file), or any
#     preview/claim/attestation secret.
#
# The fields exposed are exactly the contract the OpenSpec design
# approved: ``selection_id``, ``selection_revision``, ``state``,
# ``expires_at``, ordered ``files`` (with ``file_id``, ``file_name``,
# ``byte_count``, ``declared_library``, ``effective_library_key``,
# ``matched_auxiliary_kinds``, ``effective_auxiliary_kind``, ``status``),
# nullable ``preview`` (opaque token, lowercase-hex digest, committable
# boolean, readable report projection), and nullable ``commit_result``
# (the safe canonical import result only).

# The literal set of keys the public view is allowed to carry, used by
# the public serializer and asserted by the API tests so a new column
# added to ``resource_selection`` (or to ``resource_selection_file``)
# cannot silently widen the public surface.
SELECTION_VIEW_KEYS: frozenset[str] = frozenset({
    "selection_id",
    "selection_revision",
    "state",
    "expires_at",
    "files",
    "preview",
    "commit_result",
})

FILE_VIEW_KEYS: frozenset[str] = frozenset({
    "file_id",
    "file_name",
    "byte_count",
    "declared_library",
    "effective_library_key",
    "matched_auxiliary_kinds",
    "effective_auxiliary_kind",
    "status",
})

PREVIEW_VIEW_KEYS: frozenset[str] = frozenset({
    "preview_token",
    "manifest_digest",
    "committable",
    "report",
})

# Internal/private field names that must NEVER appear at any depth in a
# public success or error response. The list is deliberately broad: a
# field added to the row by a future task that lands on this list would
# fail the public projection test, which is the structural guard the
# design relies on (a missing sentinel on a new column is how a private
# path would leak).
PRIVATE_SELECTION_FIELDS: frozenset[str] = frozenset({
    "id",
    "staged_path",
    "staged_size",
    "staged_mtime_ns",
    "staged_sha256",
    "fingerprint",
    "reserved_bytes",
    "claim_commit_token",
    "claim_revision",
    "claim_preview_token",
    "claim_manifest_digest",
    "claim_lease_deadline",
    "committed_revision",
    "committed_preview_token",
    "committed_manifest_digest",
    "committed_at",
    "cleanup_state",
    "cleanup_warning",
    "purged_at",
    "created_at",
    "updated_at",
    "total_reserved_bytes",
    "active_file_count",
    "request_id",
})


# Names that are explicitly public on the SelectionView. They MUST stay
# allowed by ``_is_private_field_name`` even if a future suffix matches.
PUBLIC_FIELD_ALLOWLIST: frozenset[str] = frozenset({
    "selection_id",
    "selection_revision",
    "state",
    "expires_at",
    "files",
    "preview",
    "commit_result",
    "file_id",
    "file_name",
    "byte_count",
    "declared_library",
    "effective_library_key",
    "matched_auxiliary_kinds",
    "effective_auxiliary_kind",
    "status",
    "preview_token",
    "manifest_digest",
    "committable",
    "report",
})


def assert_selection_view_is_public(value: Any, _seen: set | None = None) -> None:
    """Recursively assert that ``value`` carries no private field at any depth."""
    if _seen is None:
        _seen = set()
    if isinstance(value, dict):
        key = id(value)
        if key in _seen:
            return
        _seen.add(key)
        for k, v in value.items():
            if isinstance(k, str) and _is_private_field_name(k):
                raise AssertionError(
                    f"private field {k!r} leaked into public selection view"
                )
            assert_selection_view_is_public(v, _seen)
    elif isinstance(value, list):
        key = id(value)
        if key in _seen:
            return
        _seen.add(key)
        for item in value:
            assert_selection_view_is_public(item, _seen)


def _is_private_field_name(name: str) -> bool:
    """Return ``True`` when ``name`` is a private field name the public view must not carry."""
    if name in PUBLIC_FIELD_ALLOWLIST:
        return False
    if name in PRIVATE_SELECTION_FIELDS:
        return True
    suffixes = (
        "_mtime_ns",
        "_sha256",
        "_fingerprint",
        "_lease_deadline",
        "_attestation",
        "_attestation_token",
        "_path",
        "_secret",
    )
    return any(name.endswith(s) for s in suffixes)


def _is_safe_display_filename(s: str) -> bool:
    if not isinstance(s, str) or isinstance(s, bool):
        return False
    if not s or not s.strip():
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in s):
        return False
    if "/" in s or "\\" in s:
        return False
    if "file:" in s.lower():
        return False
    if re.search(r"^[a-zA-Z]:", s):
        return False
    if not s.strip("."):
        return False
    if _is_private_field_name(s) or "claim_" in s or "staged_" in s or "attestation" in s.lower():
        return False
    return True


def _is_safe_public_string(s: str) -> bool:
    if not isinstance(s, str) or isinstance(s, bool):
        return False
    if not s or not s.strip():
        return False
    if any(ord(c) < 32 or ord(c) == 127 for c in s):
        return False
    s_clean = s.strip()
    if "/" in s_clean or "\\" in s_clean:
        return False
    if "file:" in s_clean.lower():
        return False
    if re.search(r"[a-zA-Z]:[/\\]", s_clean):
        return False
    if s_clean.startswith((".", "..")):
        return False
    if _is_private_field_name(s_clean):
        return False
    if any(s_clean.endswith(suf) for suf in (
        "_mtime_ns", "_sha256", "_fingerprint", "_lease_deadline",
        "_attestation", "_attestation_token", "_path", "_secret"
    )):
        return False
    if "claim_commit_token" in s_clean or "staged_path" in s_clean or "attestation" in s_clean.lower():
        return False
    return True


def _validate_matched_auxiliary_kinds(value: Any) -> list[str]:
    if type(value) is not str or not value:
        raise SelectionStateInvalidError("matched_auxiliary_kinds must be a non-empty JSON string")
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SelectionStateInvalidError("matched_auxiliary_kinds contains malformed JSON") from exc
    if type(decoded) is not list:
        raise SelectionStateInvalidError("matched_auxiliary_kinds must be a JSON array")
    out: list[str] = []
    seen: set[str] = set()
    for item in decoded:
        if type(item) is not str:
            raise SelectionStateInvalidError("matched_auxiliary_kinds member must be a string")
        if item not in resource_parser.ALL_AUXILIARY_KINDS:
            raise SelectionStateInvalidError(f"matched_auxiliary_kinds member {item!r} is not an allowed auxiliary kind")
        if item in seen:
            raise SelectionStateInvalidError(f"matched_auxiliary_kinds contains duplicate member {item!r}")
        seen.add(item)
        out.append(item)
    return out


def _build_file_view(file_row: dict) -> dict:
    file_id = file_row.get("file_id")
    if type(file_id) is not str or not file_id:
        raise SelectionStateInvalidError("file_id must be a non-empty string")
    if not _is_safe_public_string(file_id):
        raise SelectionStateInvalidError("file_id contains unsafe characters")

    raw_file_name = file_row.get("file_name")
    if type(raw_file_name) is not str or not raw_file_name:
        raise SelectionStateInvalidError("file_name must be a non-empty string")
    if not _is_safe_display_filename(raw_file_name):
        raise SelectionStateInvalidError("file_name is not a normalized display filename")

    byte_count = file_row.get("byte_count")
    if type(byte_count) is not int or byte_count < 0 or byte_count > MAX_SAFE_INTEGER or byte_count > MAX_BYTES_PER_FILE:
        raise SelectionStateInvalidError("byte_count must be a safe integer in bounds")

    declared_lib = file_row.get("declared_library")
    if declared_lib is not None:
        if type(declared_lib) is not str or not _is_safe_public_string(declared_lib):
            raise SelectionStateInvalidError("declared_library contains unsafe characters")

    effective_lib = file_row.get("effective_library_key")
    if effective_lib is not None:
        if type(effective_lib) is not str or not _is_safe_public_string(effective_lib):
            raise SelectionStateInvalidError("effective_library_key contains unsafe characters")

    matched_kinds = _validate_matched_auxiliary_kinds(file_row.get("matched_auxiliary_kinds"))

    effective_aux = file_row.get("effective_auxiliary_kind")
    if effective_aux is not None:
        if type(effective_aux) is not str or not _is_safe_public_string(effective_aux):
            raise SelectionStateInvalidError("effective_auxiliary_kind contains unsafe characters")

    status = file_row.get("status")
    if status != "staged":
        raise SelectionStateInvalidError("file status must be 'staged'")

    return {
        "file_id": file_id,
        "file_name": raw_file_name,
        "byte_count": byte_count,
        "declared_library": declared_lib,
        "effective_library_key": effective_lib,
        "matched_auxiliary_kinds": matched_kinds,
        "effective_auxiliary_kind": effective_aux,
        "status": status,
    }


def _build_preview_view(sel_row: dict) -> dict | None:
    token = sel_row.get("preview_token")
    digest = sel_row.get("preview_manifest_digest")
    committable_raw = sel_row.get("preview_committable")
    report_raw = sel_row.get("preview_report")
    created_at = sel_row.get("preview_created_at")

    if (
        token is None
        and digest is None
        and committable_raw is None
        and report_raw is None
        and created_at is None
    ):
        return None

    if (
        token is None
        or digest is None
        or committable_raw is None
        or report_raw is None
        or created_at is None
    ):
        raise SelectionStateInvalidError("Incomplete persisted preview evidence")

    if type(token) is not str or not token:
        raise SelectionStateInvalidError("Persisted preview_token is invalid")
    if not _is_safe_public_string(token):
        raise SelectionStateInvalidError("Persisted preview_token contains unsafe characters")

    if (
        type(digest) is not str
        or len(digest) != 64
        or not all(c in "0123456789abcdef" for c in digest)
    ):
        raise SelectionStateInvalidError("Persisted preview_manifest_digest must be canonical lowercase hex")

    if type(committable_raw) is not int or committable_raw not in (0, 1):
        raise SelectionStateInvalidError("Persisted preview_committable must be exact integer 0 or 1")
    committable = (committable_raw == 1)

    if type(report_raw) is str:
        if not report_raw:
            raise SelectionStateInvalidError("Persisted preview_report is empty")
    elif type(report_raw) is not dict:
        raise SelectionStateInvalidError("Persisted preview_report must be JSON string or dict")

    try:
        report = resource_service.validate_safe_report(report_raw, expected_phase="preview")
    except Exception as exc:
        raise SelectionStateInvalidError("Persisted preview_report is invalid") from exc

    if type(created_at) is not str or not created_at:
        raise SelectionStateInvalidError("Persisted preview_created_at is invalid")
    try:
        _parse_iso(created_at)
    except Exception as exc:
        raise SelectionStateInvalidError("Persisted preview_created_at is invalid timestamp") from exc

    return {
        "preview_token": token,
        "manifest_digest": digest,
        "committable": committable,
        "report": report,
    }


def _validate_selection_state_matrix(
    sel_row: dict,
    preview: dict | None,
    revision: int,
    state: str,
) -> dict | None:
    """Validate claim and commit evidence tuples against the selection state.

    Returns the canonical commit_result dictionary if state is 'committed',
    or None if state is not 'committed'. Fails closed with
    SelectionStateInvalidError on any partial, contradictory, or malformed state.
    """
    committed_rev = sel_row.get("committed_revision")
    committed_tok = sel_row.get("committed_preview_token")
    committed_dig = sel_row.get("committed_manifest_digest")
    commit_res_raw = sel_row.get("commit_result")
    committed_at = sel_row.get("committed_at")

    claim_tok = sel_row.get("claim_commit_token")
    claim_rev = sel_row.get("claim_revision")
    claim_prev_tok = sel_row.get("claim_preview_token")
    claim_prev_dig = sel_row.get("claim_manifest_digest")
    claim_deadline = sel_row.get("claim_lease_deadline")

    commit_tuple = (committed_rev, committed_tok, committed_dig, commit_res_raw, committed_at)
    claim_tuple = (claim_tok, claim_rev, claim_prev_tok, claim_prev_dig, claim_deadline)

    if state == "committed":
        if any(x is None for x in commit_tuple):
            raise SelectionStateInvalidError("Committed selection has incomplete commit evidence")

        if type(committed_rev) is not int or committed_rev < 0 or committed_rev > MAX_SAFE_INTEGER:
            raise SelectionStateInvalidError("committed_revision must be a safe integer")
        if committed_rev != revision:
            raise SelectionStateInvalidError("committed_revision does not match selection_revision")

        if type(committed_tok) is not str or not committed_tok or not _is_safe_public_string(committed_tok):
            raise SelectionStateInvalidError("committed_preview_token is invalid")

        if (
            type(committed_dig) is not str
            or len(committed_dig) != 64
            or not all(c in "0123456789abcdef" for c in committed_dig)
        ):
            raise SelectionStateInvalidError("committed_manifest_digest is invalid")

        if preview is not None:
            if (
                committed_tok != preview["preview_token"]
                or committed_dig != preview["manifest_digest"]
            ):
                raise SelectionStateInvalidError(
                    "committed preview token or digest does not match retained preview"
                )

        if commit_res_raw == "":
            raise SelectionStateInvalidError("commit_result cannot be empty")
        if type(commit_res_raw) is not str and type(commit_res_raw) is not dict:
            raise SelectionStateInvalidError("commit_result must be JSON string or dict")

        try:
            validated_commit = resource_service.validate_safe_report(
                commit_res_raw, expected_phase="commit"
            )
        except Exception as exc:
            raise SelectionStateInvalidError("commit_result is invalid") from exc

        if type(committed_at) is not str or not committed_at:
            raise SelectionStateInvalidError("committed_at is invalid")
        try:
            _parse_iso(committed_at)
        except Exception as exc:
            raise SelectionStateInvalidError("committed_at is invalid timestamp") from exc

        if any(x is not None for x in claim_tuple):
            raise SelectionStateInvalidError("Committed selection has active claim fields")

        return validated_commit

    else:
        if any(x is not None for x in commit_tuple):
            raise SelectionStateInvalidError(
                f"Selection in state {state!r} has unexpected commit evidence"
            )

        if state == "committing":
            if any(x is None for x in claim_tuple):
                raise SelectionStateInvalidError("Committing selection has incomplete claim evidence")

            if type(claim_tok) is not str or not claim_tok or not _is_safe_public_string(claim_tok):
                raise SelectionStateInvalidError("claim_commit_token is invalid")

            if type(claim_rev) is not int or claim_rev < 0 or claim_rev > MAX_SAFE_INTEGER:
                raise SelectionStateInvalidError("claim_revision must be a safe integer")
            if claim_rev != revision:
                raise SelectionStateInvalidError("claim_revision does not match selection_revision")

            if (
                type(claim_prev_tok) is not str
                or not claim_prev_tok
                or not _is_safe_public_string(claim_prev_tok)
            ):
                raise SelectionStateInvalidError("claim_preview_token is invalid")

            if (
                type(claim_prev_dig) is not str
                or len(claim_prev_dig) != 64
                or not all(c in "0123456789abcdef" for c in claim_prev_dig)
            ):
                raise SelectionStateInvalidError("claim_manifest_digest is invalid")

            if type(claim_deadline) is not str or not claim_deadline:
                raise SelectionStateInvalidError("claim_lease_deadline is invalid")
            try:
                _parse_iso(claim_deadline)
            except Exception as exc:
                raise SelectionStateInvalidError("claim_lease_deadline is invalid timestamp") from exc

            if preview is None:
                raise SelectionStateInvalidError("Committing selection lacks retained preview evidence")
            if (
                claim_prev_tok != preview["preview_token"]
                or claim_prev_dig != preview["manifest_digest"]
            ):
                raise SelectionStateInvalidError(
                    "claim preview token or digest does not match retained preview"
                )

        else:
            if any(x is not None for x in claim_tuple):
                raise SelectionStateInvalidError(
                    f"Selection in state {state!r} has unexpected active claim fields"
                )

        return None


def _project_selection_view(sel: dict) -> dict:
    """Pure canonical projector and validator of a persisted selection row to SelectionView."""
    sid = sel.get("selection_id")
    if type(sid) is not str or not sid or not _is_safe_public_string(sid):
        raise SelectionStateInvalidError("selection_id is invalid")

    revision = sel.get("selection_revision")
    if type(revision) is not int or revision < 0 or revision > MAX_SAFE_INTEGER:
        raise SelectionStateInvalidError("selection_revision must be safe integer")

    state = sel.get("state")
    if type(state) is not str or state not in PUBLIC_SELECTION_STATES:
        raise SelectionStateInvalidError("state is invalid")

    expires_at = sel.get("expires_at")
    if type(expires_at) is not str or not expires_at:
        raise SelectionStateInvalidError("expires_at is invalid")
    try:
        _parse_iso(expires_at)
    except Exception as exc:
        raise SelectionStateInvalidError("expires_at is invalid ISO format") from exc

    file_rows = db.q(
        "SELECT file_id, file_name, byte_count, declared_library, "
        "       effective_library_key, matched_auxiliary_kinds, "
        "       effective_auxiliary_kind, status "
        "FROM resource_selection_file "
        "WHERE selection_id = ? "
        "ORDER BY order_index ASC, id ASC",
        sid,
    )

    files = []
    for row in file_rows:
        st = row.get("status")
        if type(st) is not str or st not in ("reserved", "staged", "removed", "finalized", "failed"):
            raise SelectionStateInvalidError("file status is invalid")
        if st == "staged":
            files.append(_build_file_view(row))
    preview = _build_preview_view(sel)
    commit_result = _validate_selection_state_matrix(sel, preview, revision, state)

    return {
        "selection_id": sid,
        "selection_revision": revision,
        "state": state,
        "expires_at": expires_at,
        "files": files,
        "preview": preview,
        "commit_result": commit_result,
    }


def build_selection_view(
    selection_id: str,
    now_iso: str | None = None,
) -> dict | None:
    """Return the public, safe projection of one selection, or ``None``.

    The function runs record-local lazy recovery first (so a read after
    expiry sees the expired state, not the stale-open row), then
    serializes through the validated public shape. Fails closed with
    ``SelectionStateInvalidError`` on any inconsistent or malformed
    persisted evidence.
    """
    sel = recover_selection(selection_id, now_iso=now_iso)
    if sel is None:
        return None
    return _project_selection_view(sel)


def validate_public_revision(value: Any) -> int:
    """Validate a revision value (compatibility wrapper)."""
    if isinstance(value, str):
        return validate_query_revision(value)
    return validate_json_revision(value)


def preview_selection(
    selection_id: str,
    expected_revision: int,
    now_iso: str | None = None,
) -> dict:
    """Run canonical preview for the ordered staged manifest of an open selection.

    Validates exact expected_revision, requires open state and resolved effective targets,
    runs canonical preview_import, computes committable status, saves preview evidence,
    and returns the authoritative updated SelectionView.
    """
    if type(selection_id) is not str or not selection_id or not _is_safe_public_string(selection_id):
        raise ValueError("selection_id is invalid")
    validate_json_revision(expected_revision)

    sel = recover_selection(selection_id, now_iso=now_iso)
    if not sel:
        raise SelectionNotFoundError(f"Selection {selection_id} not found")

    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)
    if now_dt >= _parse_iso(sel["expires_at"]):
        raise SelectionExpiredError("Selection has expired")

    if sel["state"] == "committing":
        raise CommitActiveError("Cannot preview while commit is active")
    if sel["state"] in ("committed", "cancelled", "expired"):
        if sel["state"] == "expired":
            raise SelectionExpiredError("Selection has expired")
        if sel["state"] == "cancelled":
            raise SelectionCancelledError("Selection is cancelled")
        raise SelectionStateError(f"Selection is already {sel['state']}")
    if sel["state"] != "open":
        raise SelectionNotOpenError(f"Selection is {sel['state']}")

    if sel["selection_revision"] != expected_revision:
        raise StaleRevisionError(
            f"Revision {expected_revision} is stale, current is {sel['selection_revision']}"
        )

    manifest = get_selection_manifest(selection_id)
    if not manifest:
        raise InvalidTargetError("Selection contains no staged files to preview")

    selections: list[tuple[str | Path, str]] = []
    for f in manifest:
        lib_key = f.get("effective_library_key")
        if not lib_key:
            raise InvalidTargetError(f"File {f['file_id']} has no effective library target")
        validate_target_library_key(lib_key)
        aux_kind = f.get("effective_auxiliary_kind")
        if aux_kind is not None:
            validate_target_auxiliary_kind(aux_kind)
        selections.append((f["staged_path"], lib_key))

    preview_rep = resource_service.preview_import(selections)
    manifest_digest = compute_manifest_digest(manifest, selection_revision=expected_revision)
    preview_token = resource_service.create_browser_attestation(
        preview=preview_rep,
        selection_id=selection_id,
        selection_revision=expected_revision,
        manifest_digest=manifest_digest,
    )
    safe_rep = resource_service.safe_report(preview_rep)
    committable = (
        (preview_rep.total_accepted + preview_rep.total_auxiliary > 0)
        and (preview_rep.total_unresolved == 0)
    )

    saved = save_preview(
        selection_id=selection_id,
        expected_revision=expected_revision,
        preview_token=preview_token,
        manifest_digest=manifest_digest,
        committable=committable,
        report=safe_rep,
        now_iso=now_iso,
    )
    if not saved:
        raise StaleRevisionError("Selection was modified concurrently during preview")

    view = build_selection_view(selection_id, now_iso=now_iso)
    if view is None:
        raise SelectionStateInvalidError("Selection disappeared after preview save")
    return view


def commit_selection(
    selection_id: str,
    expected_revision: int,
    preview_token: str,
    now_iso: str | None = None,
    failure_injector: Callable[[int], None] | None = None,
) -> tuple[str, dict]:
    """Atomically commit an exact previewed selection.

    Returns (disposition, view), where disposition is one of:
      - "committed": genuinely new successful commit (HTTP 200)
      - "replay": idempotent replay of already committed tuple (HTTP 200)
      - "active_same_tuple": another owner is actively committing (HTTP 202)
    """
    if type(selection_id) is not str or not selection_id or not _is_safe_public_string(selection_id):
        raise ValueError("selection_id is invalid")
    validate_json_revision(expected_revision)
    if type(preview_token) is not str or not preview_token or not _is_safe_public_string(preview_token):
        raise ValueError("preview_token is invalid")

    sel = recover_selection(selection_id, now_iso=now_iso)
    if not sel:
        raise SelectionNotFoundError(f"Selection {selection_id} not found")

    now = _now_iso(now_iso)
    now_dt = _parse_iso(now)
    if now_dt >= _parse_iso(sel["expires_at"]):
        raise SelectionExpiredError("Selection has expired")

    # 1. Replay check for committed selection
    if sel["state"] == "committed":
        if (
            sel.get("committed_revision") == expected_revision
            and sel.get("committed_preview_token") == preview_token
        ):
            durable_view = build_selection_view(selection_id, now_iso=now_iso)
            if durable_view is None:
                raise SelectionStateInvalidError("Selection view missing for committed selection")
            return "replay", durable_view
        raise SelectionStateError("Selection is already committed")

    if sel["state"] == "cancelled":
        raise SelectionCancelledError("Selection is cancelled")
    if sel["state"] == "expired":
        raise SelectionExpiredError("Selection has expired")

    # 2. Check committing / open state
    manifest = get_selection_manifest(selection_id)
    computed_manifest_digest = (
        compute_manifest_digest(manifest, selection_revision=expected_revision)
        if manifest
        else ""
    )

    if sel["state"] == "open":
        if sel["selection_revision"] != expected_revision:
            raise StaleRevisionError(
                f"Revision {expected_revision} is stale, current is {sel['selection_revision']}"
            )

        if not sel.get("preview_token") or sel["preview_token"] != preview_token:
            raise PreviewMismatchError("Preview token does not match current preview")

        if sel.get("preview_committable") != 1:
            raise InvalidTargetError("Selection preview is not committable")

        if not manifest:
            raise InvalidTargetError("Selection contains no staged files to commit")

        if computed_manifest_digest != sel.get("preview_manifest_digest"):
            raise PreviewMismatchError("Manifest digest does not match preview")

    # Acquire commit claim atomically
    claim_res = acquire_commit_claim(
        selection_id=selection_id,
        expected_revision=expected_revision,
        preview_token=preview_token,
        manifest_digest=computed_manifest_digest,
        now_iso=now_iso,
    )
    if claim_res.status == "active_same_tuple":
        committing_view = build_selection_view(selection_id, now_iso=now_iso)
        if committing_view is None:
            raise SelectionStateInvalidError("Selection missing during committing state")
        return "active_same_tuple", committing_view
    elif claim_res.status == "conflict":
        raise CommitConflictError("Conflicting active commit claim in progress")
    elif claim_res.status == "stale_revision":
        raise StaleRevisionError(claim_res.detail)
    elif claim_res.status == "preview_mismatch":
        raise PreviewMismatchError(claim_res.detail)
    elif claim_res.status == "already_committed":
        durable_view = build_selection_view(selection_id, now_iso=now_iso)
        if durable_view is None:
            raise SelectionStateInvalidError("Selection view missing for committed selection")
        return "replay", durable_view
    elif claim_res.status == "expired":
        raise SelectionExpiredError("Selection has expired")
    elif claim_res.status == "terminal":
        raise SelectionStateError(claim_res.detail)
    elif claim_res.status != "acquired":
        raise SelectionStateError(f"Unexpected claim status: {claim_res.status}")

    commit_token = claim_res.commit_token
    assert commit_token is not None

    # Re-fetch selection to have latest preview_report
    sel = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)

    # Revalidate disk files and canonical report integrity while holding claim:
    manifest = get_selection_manifest(selection_id)
    selections: list[tuple[str | Path, str]] = []
    for f in manifest:
        staged_p = Path(f["staged_path"])
        if not staged_p.is_file():
            release_commit_claim(selection_id, commit_token, now_iso=now_iso)
            raise PreviewMismatchError("Staged file missing on disk; fresh preview required")
        stat_res = staged_p.stat()
        staged_size = f.get("staged_size", f["byte_count"])
        if stat_res.st_size != staged_size or stat_res.st_size != f["byte_count"]:
            release_commit_claim(selection_id, commit_token, now_iso=now_iso)
            raise PreviewMismatchError("Staged file size changed after preview; fresh preview required")
        if str(stat_res.st_mtime_ns) != str(f.get("staged_mtime_ns", "")):
            release_commit_claim(selection_id, commit_token, now_iso=now_iso)
            raise PreviewMismatchError("Staged file mtime changed after preview; fresh preview required")
        hasher = hashlib.sha256()
        with open(staged_p, "rb") as fp:
            while chunk := fp.read(65536):
                hasher.update(chunk)
        disk_sha = hasher.hexdigest()
        if disk_sha != f["staged_sha256"]:
            release_commit_claim(selection_id, commit_token, now_iso=now_iso)
            raise PreviewMismatchError("Staged file content changed after preview; fresh preview required")
        expected_fp = f"{disk_sha}:{stat_res.st_size}:{stat_res.st_mtime_ns}"
        if f.get("fingerprint") and f["fingerprint"] != expected_fp:
            release_commit_claim(selection_id, commit_token, now_iso=now_iso)
            raise PreviewMismatchError("Staged file fingerprint mismatch; fresh preview required")
        lib_key = f.get("effective_library_key")
        if not lib_key:
            release_commit_claim(selection_id, commit_token, now_iso=now_iso)
            raise InvalidTargetError("Selection file lacks effective library target")
        selections.append((staged_p, lib_key))

    # Rebuild preview report deterministically and verify attestation
    try:
        preview_rep = resource_service.preview_import(selections)
        rebuilt_safe = resource_service.safe_report(preview_rep)
        persisted_report_raw = sel.get("preview_report") if sel else None
        if persisted_report_raw is None:
            raise PreviewMismatchError("Persisted preview report is missing")
        persisted_safe = (
            json.loads(persisted_report_raw)
            if isinstance(persisted_report_raw, str)
            else persisted_report_raw
        )
        if rebuilt_safe != persisted_safe:
            raise PreviewMismatchError(
                "Canonical report outcomes do not match preview; a fresh preview is required"
            )
        preview_body = resource_service._preview_body(preview_rep)
        resource_service.verify_browser_attestation(
            body=preview_body,
            token=preview_token,
            selection_id=selection_id,
            selection_revision=expected_revision,
            manifest_digest=computed_manifest_digest,
        )
    except BaseException as exc:
        release_commit_claim(selection_id, commit_token, now_iso=now_iso)
        if isinstance(exc, ResourceSelectionError):
            raise
        raise PreviewMismatchError("Canonical preview revalidation failed") from exc

    if _commit_before_tx_hook is not None:
        try:
            _commit_before_tx_hook()
        except BaseException:
            release_commit_claim(selection_id, commit_token, now_iso=now_iso)
            raise

    # Verify claim ownership before entering transaction
    sel_check = db.one(
        "SELECT claim_commit_token, claim_revision, claim_preview_token, claim_manifest_digest, state FROM resource_selection WHERE selection_id = ?",
        selection_id,
    )
    if (
        not sel_check
        or sel_check["state"] != "committing"
        or sel_check.get("claim_commit_token") != commit_token
        or sel_check.get("claim_revision") != expected_revision
        or sel_check.get("claim_preview_token") != preview_token
        or sel_check.get("claim_manifest_digest") != computed_manifest_digest
    ):
        raise CommitConflictError("Commit claim ownership lost")

    # Execute atomic SQLite transaction: commit_selection_import + coverage + record_commit_result
    try:
        with db.transaction():
            # Verify claim ownership inside transaction before resource writes
            sel_in_tx = db.one(
                "SELECT claim_commit_token, claim_revision, claim_preview_token, claim_manifest_digest, state FROM resource_selection WHERE selection_id = ?",
                selection_id,
            )
            if (
                not sel_in_tx
                or sel_in_tx["state"] != "committing"
                or sel_in_tx.get("claim_commit_token") != commit_token
                or sel_in_tx.get("claim_revision") != expected_revision
                or sel_in_tx.get("claim_preview_token") != preview_token
                or sel_in_tx.get("claim_manifest_digest") != computed_manifest_digest
            ):
                raise CommitConflictError("Commit claim ownership lost")

            commit_report = resource_service.commit_selection_import(
                preview=preview_rep,
                preview_token=preview_token,
                selection_id=selection_id,
                selection_revision=expected_revision,
                manifest_digest=computed_manifest_digest,
                failure_injector=failure_injector or _commit_failure_injector,
            )
            commit_safe = resource_service.safe_report(commit_report)
            commit_safe = resource_service.validate_safe_report(commit_safe, expected_phase="commit")

            if _commit_before_record_result_hook is not None:
                _commit_before_record_result_hook()

            ok = record_commit_result(
                selection_id=selection_id,
                commit_token=commit_token,
                result=commit_safe,
                now_iso=now_iso,
            )
            if not ok:
                raise CommitConflictError("Commit claim ownership lost")
    except ValueError as exc:
        release_commit_claim(selection_id, commit_token, now_iso=now_iso)
        raise PreviewMismatchError("Attestation validation failed") from exc
    except resource_import.StaleFingerprintError as exc:
        release_commit_claim(selection_id, commit_token, now_iso=now_iso)
        raise PreviewMismatchError("Staged content changed during commit") from exc
    except CommitConflictError:
        # Defect 3: Owner whose fence changed must NOT release the new owner's claim!
        raise
    except BaseException:
        release_commit_claim(selection_id, commit_token, now_iso=now_iso)
        raise

    if _commit_after_tx_hook is not None:
        _commit_after_tx_hook()

    final_view = build_selection_view(selection_id, now_iso=now_iso)
    if final_view is None:
        raise SelectionStateInvalidError("Selection disappeared after commit")
    return "committed", final_view
