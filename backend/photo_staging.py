"""Private staging and lifecycle for user-selected saved-look photos."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import binascii
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import threading
import uuid

from fastapi import Request
from PIL import Image, UnidentifiedImageError
from python_multipart import MultipartParser
from starlette.formparsers import parse_options_header

import db

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTO_PIXELS = 25_000_000
MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_MULTIPART_BYTES = MAX_PHOTO_BYTES + MULTIPART_OVERHEAD_BYTES
RECOVERY_BATCH = 50
OPPORTUNISTIC_BATCH = 5
PHOTO_LIFETIME = timedelta(hours=24)
TOMBSTONE_LIFETIME = timedelta(hours=24)
_PHOTO_ID = re.compile(r"^[0-9a-f]{32}$")
_TERMINAL_STATES = ("saved", "cancelled", "expired")
_FORMAT_TO_MEDIA = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_CLEANUP_WARNING = (
    "The staged photo could not be removed yet; cleanup will retry when this stage is accessed."
)
_sweep_cursor = 0
_sweep_lock = threading.Lock()


class PhotoStageError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _stage_root() -> Path:
    row = db.one("PRAGMA database_list")
    database_file = row.get("file") if row else None
    if database_file and database_file != ":memory:":
        parent = Path(database_file).resolve().parent
    else:
        parent = Path(os.environ.get("IDEVGEN_DATA_DIR") or "data").resolve()
    return (parent / "staging" / "look-photos").resolve()


def _stage_path(photo_id: str) -> Path:
    if not _PHOTO_ID.fullmatch(photo_id):
        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
    root = _stage_root()
    path = root / f"{photo_id}.image"
    if path.parent.resolve() != root:
        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
    return path


def _temporary_path(photo_id: str) -> Path:
    if not _PHOTO_ID.fullmatch(photo_id):
        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
    return _stage_root() / f".{photo_id}.tmp"


def _jpeg_ends_at_eof(data: bytes) -> bool:
    """Walk JPEG markers and entropy escapes to the first real EOI marker."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return False
    offset = 2
    in_scan = False
    while offset < len(data):
        if in_scan:
            marker_start = data.find(b"\xff", offset)
            if marker_start < 0:
                return False
            offset = marker_start + 1
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                return False
            marker = data[offset]
            offset += 1
            if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                continue
            in_scan = False
        else:
            if data[offset] != 0xFF:
                return False
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                return False
            marker = data[offset]
            offset += 1

        if marker == 0xD9:
            return offset == len(data)
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7:
            return False
        if marker == 0x01:  # TEM is the only other stand-alone marker.
            continue
        if offset + 2 > len(data):
            return False
        segment_length = int.from_bytes(data[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            return False
        offset += segment_length
        if marker == 0xDA:
            in_scan = True
    return False


def _container_ends_at_eof(data: bytes, image_format: str) -> bool:
    if image_format == "JPEG":
        return _jpeg_ends_at_eof(data)
    if image_format == "WEBP":
        return (
            len(data) >= 12
            and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
            and int.from_bytes(data[4:8], "little") + 8 == len(data)
        )
    if image_format == "PNG":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        offset = 8
        while offset + 12 <= len(data):
            size = int.from_bytes(data[offset:offset + 4], "big")
            chunk_type = data[offset + 4:offset + 8]
            end = offset + 12 + size
            if end > len(data):
                return False
            crc = int.from_bytes(data[end - 4:end], "big")
            if (binascii.crc32(data[offset + 4:end - 4]) & 0xFFFFFFFF) != crc:
                return False
            if chunk_type == b"IEND":
                return size == 0 and end == len(data)
            offset = end
    return False


def verify_photo(data: bytes) -> dict:
    """Verify a supported image, exact container end, dimensions and full decode."""
    if not isinstance(data, bytes) or not data:
        raise PhotoStageError(422, "invalid_image", "Choose a valid JPEG, PNG, or WebP image.")
    if len(data) > MAX_PHOTO_BYTES:
        raise PhotoStageError(413, "photo_too_large", "The image exceeds the 10 MiB file limit.")
    try:
        with Image.open(BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as image:
            image_format = image.format
            if image_format not in _FORMAT_TO_MEDIA or not _container_ends_at_eof(data, image_format):
                raise PhotoStageError(422, "invalid_image", "Choose a complete JPEG, PNG, or WebP image.")
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_PHOTO_PIXELS:
                raise PhotoStageError(422, "photo_dimensions_exceeded", "The image exceeds the 25-megapixel limit.")
            if getattr(image, "n_frames", 1) != 1:
                raise PhotoStageError(422, "multiple_frames_unsupported", "Choose a single-frame image.")
            image.verify()
        with Image.open(BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as decoded:
            if decoded.format != image_format or decoded.size != (width, height):
                raise PhotoStageError(422, "invalid_image", "Choose a valid JPEG, PNG, or WebP image.")
            decoded.load()
    except PhotoStageError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError):
        raise PhotoStageError(422, "invalid_image", "Choose a complete JPEG, PNG, or WebP image.") from None
    return {
        "image_format": image_format,
        "media_type": _FORMAT_TO_MEDIA[image_format],
        "width": width,
        "height": height,
        "byte_count": len(data),
        "source_sha256": sha256(data).hexdigest(),
    }


def read_staged_photo(photo_id: str) -> tuple[bytes, dict]:
    """Read and re-verify the bounded bytes that will be sent to vision."""
    row = _recover_one(photo_id)
    opportunistic_sweep(exclude=photo_id)
    row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id) if row else None
    if row is None or row["state"] == "publishing":
        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
    if row["state"] == "expired":
        raise PhotoStageError(410, "photo_stage_expired", "This photo stage has expired.")
    if row["state"] == "cancelled":
        raise PhotoStageError(410, "photo_stage_cancelled", "This photo stage was cancelled.")
    if row["state"] == "saved":
        raise PhotoStageError(409, "photo_stage_saved", "This photo stage has already been saved.")

    path = _stage_path(photo_id)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_PHOTO_BYTES:
            raise OSError("staged file is not a bounded regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or opened.st_size > MAX_PHOTO_BYTES:
                raise OSError("staged file is not a bounded regular file")
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise OSError("staged file changed while opening")
            data = stream.read(MAX_PHOTO_BYTES + 1)
            if len(data) > MAX_PHOTO_BYTES or len(data) != opened.st_size:
                raise OSError("staged file size changed")
        after = path.lstat()
        if not stat.S_ISREG(after.st_mode) or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino):
            raise OSError("staged file changed while reading")
        metadata = verify_photo(data)
    except PhotoStageError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise PhotoStageError(409, "photo_stage_changed", "The staged photo changed; upload it again.") from None

    expected = {
        "byte_count": row["byte_count"],
        "image_format": row["image_format"],
        "media_type": row["media_type"],
        "width": row["width"],
        "height": row["height"],
        "source_sha256": row["source_sha256"],
    }
    if metadata != expected:
        raise PhotoStageError(409, "photo_stage_changed", "The staged photo changed; upload it again.")
    return data, {
        "sha256": metadata["source_sha256"],
        "media_type": metadata["media_type"],
        "byte_count": metadata["byte_count"],
        "width": metadata["width"],
        "height": metadata["height"],
    }


async def read_multipart_photo(request: Request, boundary: str) -> bytes:
    """Read exactly one file part with independent file and framing limits."""
    lengths = request.headers.getlist("content-length")
    if len(lengths) == 1 and lengths[0].strip().isdigit() and int(lengths[0].strip()) > MAX_MULTIPART_BYTES:
        raise PhotoStageError(413, "photo_upload_too_large", "The photo upload exceeds the request limit.")

    state: dict[str, object] = {
        "parts": 0,
        "headers": {},
        "header_name": bytearray(),
        "header_value": bytearray(),
        "is_file": False,
        "ended": False,
    }
    file_data = bytearray()

    def part_begin() -> None:
        state["parts"] = int(state["parts"]) + 1
        if state["parts"] > 1:
            raise ValueError("multiple multipart parts")
        state["headers"] = {}
        state["is_file"] = False

    def header_field(data: bytes, start: int, end: int) -> None:
        state["header_name"].extend(data[start:end])

    def header_value(data: bytes, start: int, end: int) -> None:
        state["header_value"].extend(data[start:end])

    def header_end() -> None:
        headers = state["headers"]
        name = bytes(state["header_name"]).strip().lower()
        value = bytes(state["header_value"]).strip()
        if not name or name in headers:
            raise ValueError("duplicate or empty multipart header")
        headers[name] = value
        state["header_name"] = bytearray()
        state["header_value"] = bytearray()

    def headers_finished() -> None:
        headers = state["headers"]
        content_disposition = headers.get(b"content-disposition")
        if content_disposition is None:
            raise ValueError("missing content disposition")
        disposition, options = parse_options_header(content_disposition)
        if disposition != b"form-data" or options.get(b"name") != b"file" or b"filename" not in options:
            raise ValueError("expected one named file part")
        if b"transfer-encoding" in headers:
            raise ValueError("unexpected transfer encoding")
        state["is_file"] = True

    def part_data(data: bytes, start: int, end: int) -> None:
        if not state["is_file"]:
            raise ValueError("unexpected form field")
        if len(file_data) + end - start > MAX_PHOTO_BYTES:
            raise PhotoStageError(413, "photo_too_large", "The image exceeds the 10 MiB file limit.")
        file_data.extend(data[start:end])

    def part_end() -> None:
        state["is_file"] = False

    def parser_end() -> None:
        state["ended"] = True

    callbacks = {
        "on_part_begin": part_begin,
        "on_header_field": header_field,
        "on_header_value": header_value,
        "on_header_end": header_end,
        "on_headers_finished": headers_finished,
        "on_part_data": part_data,
        "on_part_end": part_end,
        "on_end": parser_end,
    }
    try:
        parser = MultipartParser(
            boundary.encode("latin1"),
            callbacks,
            max_size=MAX_MULTIPART_BYTES,
            max_header_count=8,
            max_header_size=8192,
        )
        body_bytes = 0
        async for chunk in request.stream():
            body_bytes += len(chunk)
            if body_bytes > MAX_MULTIPART_BYTES:
                raise PhotoStageError(413, "photo_upload_too_large", "The photo upload exceeds the request limit.")
            parser.write(chunk)
        parser.finalize()
    except PhotoStageError:
        raise
    except Exception:
        raise PhotoStageError(422, "invalid_multipart", "Upload one photo as a multipart file named 'file'.") from None
    if state["parts"] != 1 or not state["ended"] or not file_data:
        raise PhotoStageError(422, "invalid_multipart", "Upload exactly one non-empty multipart file named 'file'.")
    return bytes(file_data)


def _safe_unlink(photo_id: str, stored_path: str) -> tuple[bool, str]:
    try:
        root = _stage_root()
        target = _stage_path(photo_id)
        if Path(stored_path) != target or target.parent.resolve() != root:
            return False, _CLEANUP_WARNING
        for owned_path in (_temporary_path(photo_id), target):
            try:
                resolved = owned_path.resolve(strict=False)
                if (
                    owned_path.parent.resolve() != root
                    or owned_path.is_symlink()
                    or resolved != owned_path
                    or resolved.parent != root
                ):
                    return False, _CLEANUP_WARNING
                if owned_path.exists():
                    if not owned_path.is_file():
                        return False, _CLEANUP_WARNING
                    owned_path.unlink()
            except FileNotFoundError:
                # Continue to the other owned name; a concurrent removal of
                # the temporary must not skip checking the published file.
                continue
        return True, ""
    except FileNotFoundError:
        return True, ""
    except (OSError, PhotoStageError, RuntimeError):
        return False, _CLEANUP_WARNING


def _cleanup_after_commit(photo_id: str) -> None:
    try:
        _attempt_cleanup(photo_id)
    except Exception:
        # The durable row remains pending for lazy or startup recovery.
        return


def _schedule_cleanup(photo_id: str) -> None:
    db.on_commit(lambda: _cleanup_after_commit(photo_id))


def _attempt_cleanup(photo_id: str) -> None:
    row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
    if row is None or row["state"] not in _TERMINAL_STATES or row["cleanup_state"] not in ("pending", "failed"):
        return
    ok, warning = _safe_unlink(photo_id, row["staged_path"])
    with db.transaction():
        current = db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)
        if current is None or current["state"] not in _TERMINAL_STATES:
            return
        db.run(
            "UPDATE look_photo_stage SET cleanup_state = ?, cleanup_warning = ?, updated_at = ? WHERE photo_id = ?",
            "cleaned" if ok else "failed",
            "" if ok else warning,
            _iso(_now()),
            photo_id,
        )


def _maybe_purge(row: dict, now: datetime) -> bool:
    if row["state"] not in _TERMINAL_STATES or row["cleanup_state"] != "cleaned":
        return False
    if now < _parse_iso(row["expires_at"]) + TOMBSTONE_LIFETIME:
        return False
    with db.transaction():
        current = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", row["photo_id"])
        if (
            current is None
            or current["state"] not in _TERMINAL_STATES
            or current["cleanup_state"] != "cleaned"
            or now < _parse_iso(current["expires_at"]) + TOMBSTONE_LIFETIME
        ):
            return False
        db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", row["photo_id"])
        db.run("DELETE FROM look_photo_stage WHERE photo_id = ?", row["photo_id"])
    return True


def _recover_one(photo_id: str, now: datetime | None = None) -> dict | None:
    instant = now or _now()
    with db.transaction():
        row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
        if row is None:
            return None
        if row["state"] in ("staged", "publishing") and instant >= _parse_iso(row["expires_at"]):
            db.run(
                "UPDATE look_photo_stage SET state = 'expired', cleanup_state = 'pending', cleanup_warning = '', updated_at = ? WHERE photo_id = ? AND state IN ('staged', 'publishing')",
                _iso(instant), photo_id,
            )
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", photo_id)
        elif row["state"] in _TERMINAL_STATES:
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", photo_id)
        row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
        if row is None:
            return None
        if row["state"] in _TERMINAL_STATES and row["cleanup_state"] in ("pending", "failed"):
            _schedule_cleanup(photo_id)
        if _maybe_purge(row, instant):
            return None
    # Cleanup hooks run after the transaction releases its lock. Re-read under
    # the same database lock so concurrent stage requests never share an
    # unsynchronized SQLite cursor, and callers see the cleanup result.
    with db.transaction():
        return db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)


def _pending_ids(limit: int, now: datetime, exclude: str | None = None) -> list[str]:
    global _sweep_cursor
    if limit <= 0:
        return []
    with _sweep_lock:
        retention_cutoff = _iso(now - TOMBSTONE_LIFETIME)
        condition = "((state IN ('staged','publishing') AND expires_at <= ?) OR (state IN ('saved','cancelled','expired') AND cleanup_state IN ('pending','failed')) OR (state IN ('saved','cancelled','expired') AND cleanup_state = 'cleaned' AND expires_at <= ?))"
        params: list[object] = [_iso(now), retention_cutoff]
        if exclude is not None:
            condition += " AND photo_id <> ?"
            params.append(exclude)
        rows = db.q(
            f"SELECT id, photo_id FROM look_photo_stage WHERE {condition} AND id > ? ORDER BY id LIMIT ?",
            *params, _sweep_cursor, limit,
        )
        if len(rows) < limit:
            rows.extend(db.q(
                f"SELECT id, photo_id FROM look_photo_stage WHERE {condition} AND id <= ? ORDER BY id LIMIT ?",
                *params, _sweep_cursor, limit - len(rows),
            ))
        if rows:
            _sweep_cursor = rows[-1]["id"]
        return [row["photo_id"] for row in rows]


def startup_recovery(limit: int = RECOVERY_BATCH, now: datetime | None = None) -> dict:
    instant = now or _now()
    summary = {"expired": 0, "cleanup_retried": 0, "purged": 0}
    ids = _pending_ids(max(0, limit), instant)
    for photo_id in ids:
        before = db.one("SELECT state, cleanup_state FROM look_photo_stage WHERE photo_id = ?", photo_id)
        after = _recover_one(photo_id, instant)
        if before and before["state"] in ("staged", "publishing") and after and after["state"] == "expired":
            summary["expired"] += 1
        if before and before["cleanup_state"] in ("pending", "failed"):
            summary["cleanup_retried"] += 1
        if after is None:
            summary["purged"] += 1
    return summary


def opportunistic_sweep(limit: int = OPPORTUNISTIC_BATCH, now: datetime | None = None, exclude: str | None = None) -> dict:
    instant = now or _now()
    summary = {"expired": 0, "cleanup_retried": 0, "purged": 0}
    for photo_id in _pending_ids(max(0, limit), instant, exclude):
        before = db.one("SELECT state, cleanup_state FROM look_photo_stage WHERE photo_id = ?", photo_id)
        after = _recover_one(photo_id, instant)
        if before and before["state"] in ("staged", "publishing") and after and after["state"] == "expired":
            summary["expired"] += 1
        if before and before["cleanup_state"] in ("pending", "failed"):
            summary["cleanup_retried"] += 1
        if after is None:
            summary["purged"] += 1
    return summary


def create_stage(data: bytes) -> dict:
    with db._tx_lock:
        if getattr(db, "_tx_depth", 0):
            raise PhotoStageError(
                409,
                "photo_stage_transaction_active",
                "A photo stage cannot be created inside another write transaction.",
            )
    image = verify_photo(data)
    photo_id = uuid.uuid4().hex
    target = _stage_path(photo_id)
    temporary = _temporary_path(photo_id)
    created = _now()
    expires = created + PHOTO_LIFETIME

    # A durable owner exists before any byte reaches disk. If this process is
    # lost during publication, expiry recovery knows both owned filenames.
    with db.transaction():
        db.run(
            """INSERT INTO look_photo_stage
               (photo_id, state, created_at, updated_at, expires_at, staged_path,
                byte_count, image_format, media_type, width, height, source_sha256)
               VALUES (?, 'publishing', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            photo_id, _iso(created), _iso(created), _iso(expires), str(target),
            image["byte_count"], image["image_format"], image["media_type"],
            image["width"], image["height"], image["source_sha256"],
        )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        expired = False
        with db.transaction():
            current = db.one("SELECT state, expires_at FROM look_photo_stage WHERE photo_id = ?", photo_id)
            if current is None:
                raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
            if current["state"] == "publishing" and _now() < _parse_iso(current["expires_at"]):
                db.run(
                    "UPDATE look_photo_stage SET state = 'staged', updated_at = ? WHERE photo_id = ? AND state = 'publishing'",
                    _iso(_now()), photo_id,
                )
            else:
                if current["state"] == "publishing":
                    db.run(
                        "UPDATE look_photo_stage SET state = 'expired', cleanup_state = 'pending', cleanup_warning = '', updated_at = ? WHERE photo_id = ?",
                        _iso(_now()), photo_id,
                    )
                else:
                    db.run(
                        "UPDATE look_photo_stage SET cleanup_state = 'pending', cleanup_warning = '', updated_at = ? WHERE photo_id = ?",
                        _iso(_now()), photo_id,
                    )
                _schedule_cleanup(photo_id)
                expired = True
        if expired:
            raise PhotoStageError(410, "photo_stage_expired", "This photo stage has expired.")
    except PhotoStageError:
        raise
    except Exception:
        try:
            _fail_publication(photo_id)
        except Exception:
            # The durable publishing row remains available to expiry recovery.
            pass
        raise PhotoStageError(500, "photo_stage_unavailable", "The photo could not be staged.") from None
    return get_stage(photo_id)


def _fail_publication(photo_id: str) -> None:
    with db.transaction():
        row = db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)
        if row is None:
            return
        if row["state"] == "publishing":
            db.run(
                "UPDATE look_photo_stage SET state = 'cancelled', cleanup_state = 'pending', cleanup_warning = '', updated_at = ? WHERE photo_id = ?",
                _iso(_now()), photo_id,
            )
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", photo_id)
            _schedule_cleanup(photo_id)
        elif row["state"] in _TERMINAL_STATES:
            db.run(
                "UPDATE look_photo_stage SET cleanup_state = 'pending', cleanup_warning = '', updated_at = ? WHERE photo_id = ?",
                _iso(_now()), photo_id,
            )
            _schedule_cleanup(photo_id)


def _view(row: dict) -> dict:
    return {
        "photo_id": row["photo_id"],
        "state": row["state"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "format": row["image_format"],
        "media_type": row["media_type"],
        "byte_count": row["byte_count"],
        "width": row["width"],
        "height": row["height"],
        "cleanup_warning": _CLEANUP_WARNING if row["cleanup_state"] == "failed" else None,
        "saved_look": (
            {"key": row["saved_look_key"], "version": row["saved_look_version"]}
            if row["state"] == "saved" else None
        ),
    }


def get_stage(photo_id: str) -> dict | None:
    row = _recover_one(photo_id)
    opportunistic_sweep(exclude=photo_id)
    row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id) if row else None
    return None if row is None or row["state"] == "publishing" else _view(row)


def preview_path(photo_id: str) -> tuple[Path, str]:
    row = _recover_one(photo_id)
    opportunistic_sweep(exclude=photo_id)
    row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id) if row else None
    if row is None:
        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
    if row["state"] == "publishing":
        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
    if row["state"] != "staged":
        raise PhotoStageError(410, "photo_stage_expired", "This photo is no longer available for preview.")
    path = _stage_path(photo_id)
    root = _stage_root()
    try:
        resolved = path.resolve(strict=True)
        if (
            Path(row["staged_path"]) != path
            or path.is_symlink()
            or resolved != path
            or resolved.parent != root
            or not resolved.is_file()
        ):
            raise PhotoStageError(404, "photo_preview_unavailable", "The staged photo preview is unavailable.")
    except (OSError, RuntimeError):
        raise PhotoStageError(404, "photo_preview_unavailable", "The staged photo preview is unavailable.")
    return path, row["media_type"]


def cancel_stage(photo_id: str) -> dict:
    row = _recover_one(photo_id)
    opportunistic_sweep(exclude=photo_id)
    if row is None:
        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
    if row["state"] == "expired":
        raise PhotoStageError(410, "photo_stage_expired", "This photo stage has expired.")
    if row["state"] == "saved":
        raise PhotoStageError(409, "photo_stage_saved", "A saved photo stage cannot be cancelled.")
    if row["state"] == "publishing":
        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
    if row["state"] == "cancelled":
        return _view(row)
    expired_during_cancel = False
    with db.transaction():
        current = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
        if current is None:
            raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
        now = _now()
        if current["state"] == "staged" and now < _parse_iso(current["expires_at"]):
            db.run(
                "UPDATE look_photo_stage SET state = 'cancelled', cleanup_state = 'pending', cleanup_warning = '', updated_at = ? WHERE photo_id = ?",
                _iso(now), photo_id,
            )
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", photo_id)
            _schedule_cleanup(photo_id)
        elif current["state"] in ("staged", "publishing") and now >= _parse_iso(current["expires_at"]):
            db.run(
                "UPDATE look_photo_stage SET state = 'expired', cleanup_state = 'pending', cleanup_warning = '', updated_at = ? WHERE photo_id = ?",
                _iso(now), photo_id,
            )
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", photo_id)
            _schedule_cleanup(photo_id)
            expired_during_cancel = True
        elif current["state"] == "publishing":
            raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
        elif current["state"] == "expired":
            raise PhotoStageError(410, "photo_stage_expired", "This photo stage has expired.")
        elif current["state"] == "saved":
            raise PhotoStageError(409, "photo_stage_saved", "A saved photo stage cannot be cancelled.")
    if expired_during_cancel:
        raise PhotoStageError(410, "photo_stage_expired", "This photo stage has expired.")
    latest = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
    return _view(latest) if latest else {"photo_id": photo_id, "state": "cancelled"}


def _payload_digest(payload: dict) -> str:
    try:
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise PhotoStageError(422, "invalid_look", "The reviewed look is invalid.") from None
    return sha256(encoded.encode("ascii")).hexdigest()


def replay_saved_look(photo_id: str, digest_payload: dict) -> dict | None:
    """Return an exact successful save retry without reopening the cleaned image."""
    digest = _payload_digest(digest_payload)
    row = _recover_one(photo_id)
    opportunistic_sweep(exclude=photo_id)
    if row is None or row["state"] != "saved":
        return None
    if row["save_payload_digest"] != digest:
        raise PhotoStageError(
            409,
            "idempotency_conflict",
            "This photo stage was already saved with different content.",
        )
    return json.loads(row["saved_result_json"])


def save_look(
    photo_id: str,
    payload: dict,
    create_look,
    *,
    expected_proposal_id: str | None = None,
    digest_payload: dict | None = None,
) -> dict:
    """Create a reviewed look and terminal stage receipt in one transaction."""
    digest = _payload_digest(payload if digest_payload is None else digest_payload)
    row = _recover_one(photo_id)
    opportunistic_sweep(exclude=photo_id)
    if row is None:
        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
    if row["state"] == "saved":
        if row["save_payload_digest"] != digest:
            raise PhotoStageError(409, "idempotency_conflict", "This photo stage was already saved with different content.")
        return json.loads(row["saved_result_json"])
    if row["state"] == "expired":
        raise PhotoStageError(410, "photo_stage_expired", "This photo stage has expired.")
    if row["state"] == "cancelled":
        raise PhotoStageError(410, "photo_stage_cancelled", "This photo stage was cancelled.")
    if row["state"] == "publishing":
        raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")

    expired_during_save = False
    result = None
    with db.transaction():
        current = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
        if current is None:
            raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
        if current["state"] == "saved":
            if current["save_payload_digest"] != digest:
                raise PhotoStageError(409, "idempotency_conflict", "This photo stage was already saved with different content.")
            return json.loads(current["saved_result_json"])
        now = _now()
        if current["state"] in ("staged", "publishing") and now >= _parse_iso(current["expires_at"]):
            db.run(
                "UPDATE look_photo_stage SET state = 'expired', cleanup_state = 'pending', cleanup_warning = '', updated_at = ? WHERE photo_id = ?",
                _iso(now), photo_id,
            )
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", photo_id)
            _schedule_cleanup(photo_id)
            expired_during_save = True
        elif current["state"] == "publishing":
            raise PhotoStageError(404, "photo_stage_not_found", "Photo stage was not found.")
        elif current["state"] != "staged":
            raise PhotoStageError(410, "photo_stage_expired", "This photo stage has expired.")
        else:
            proposals = db.q(
                "SELECT proposal_id, state FROM photo_look_proposal WHERE photo_id = ?",
                photo_id,
            )
            if expected_proposal_id is None:
                if any(item["state"] == "ready" for item in proposals):
                    raise PhotoStageError(
                        409,
                        "photo_extraction_review_required",
                        "Review and save the extraction proposal before saving this photo stage.",
                    )
            elif not any(
                item["proposal_id"] == expected_proposal_id and item["state"] == "ready"
                for item in proposals
            ):
                raise PhotoStageError(
                    409,
                    "photo_proposal_stale",
                    "The extraction proposal is stale or does not belong to this photo stage.",
                )
            result = create_look(payload)
            db.run(
                """UPDATE look_photo_stage
                   SET state = 'saved', cleanup_state = 'pending', cleanup_warning = '',
                       save_payload_digest = ?, saved_look_key = ?, saved_look_version = ?,
                       saved_result_json = ?, updated_at = ?
                   WHERE photo_id = ? AND state = 'staged'""",
                digest, result["key"], result["version"],
                json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                _iso(_now()), photo_id,
            )
            db.run("DELETE FROM photo_look_proposal WHERE photo_id = ?", photo_id)
            _schedule_cleanup(photo_id)
    if expired_during_save:
        raise PhotoStageError(410, "photo_stage_expired", "This photo stage has expired.")
    return result
