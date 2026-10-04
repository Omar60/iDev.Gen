from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import json
import binascii
from pathlib import Path
import uuid

from PIL import Image
import pytest

import db
import main
from backend import photo_staging


def _image_bytes(image_format: str = "PNG", size: tuple[int, int] = (4, 3)) -> bytes:
    stream = BytesIO()
    Image.new("RGB", size, (23, 81, 144)).save(stream, format=image_format)
    return stream.getvalue()


def _jpeg_of_exact_size(size: int) -> bytes:
    image = _image_bytes("JPEG", (1, 1))
    remaining = size - len(image)
    assert remaining >= 4
    segments = bytearray()
    while remaining:
        segment_size = min(65_537, remaining)
        if 0 < remaining - segment_size < 4:
            segment_size -= 4 - (remaining - segment_size)
        payload_size = segment_size - 4
        segments.extend(b"\xff\xfe")
        segments.extend((payload_size + 2).to_bytes(2, "big"))
        segments.extend(b"x" * payload_size)
        remaining -= segment_size
    return image[:2] + bytes(segments) + image[2:]


@pytest.fixture(autouse=True)
def _clear_photo_stages(client):
    for row in db.q("SELECT photo_id, staged_path FROM look_photo_stage"):
        photo_staging._safe_unlink(row["photo_id"], row["staged_path"])
    db.run("DELETE FROM photo_look_proposal")
    db.run("DELETE FROM look_photo_stage")
    yield
    for row in db.q("SELECT photo_id, staged_path FROM look_photo_stage"):
        photo_staging._safe_unlink(row["photo_id"], row["staged_path"])
    db.run("DELETE FROM photo_look_proposal")
    db.run("DELETE FROM look_photo_stage")


def _upload(client, content: bytes, *, filename: str = "private-label.png", media_type: str = "image/png", headers=None):
    return client.post(
        "/api/looks/photo-stages",
        files={"file": (filename, content, media_type)},
        headers=headers,
    )


def test_actual_image_format_controls_staging_and_view_is_closed_and_path_free(client, monkeypatch):
    def fail_if_inference_runs(*_args, **_kwargs):
        pytest.fail("selecting a photo must not invoke inference")

    monkeypatch.setattr(main.enhance, "run", fail_if_inference_runs)
    photo = _image_bytes("PNG")
    response = _upload(client, photo, media_type="text/plain")

    assert response.status_code == 201, response.text
    view = response.json()
    assert set(view) == {
        "photo_id", "state", "created_at", "expires_at", "format", "media_type",
        "byte_count", "width", "height", "cleanup_warning", "saved_look",
    }
    assert view["state"] == "staged"
    assert view["format"] == "PNG"
    assert view["media_type"] == "image/png"
    assert view["byte_count"] == len(photo)
    assert (view["width"], view["height"]) == (4, 3)
    assert view["cleanup_warning"] is None
    assert view["saved_look"] is None
    assert "private-label.png" not in response.text
    row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", view["photo_id"])
    assert row["source_sha256"] == sha256(photo).hexdigest()
    assert row["staged_path"] not in response.text
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0

    preview = client.get(f"/api/looks/photo-stages/{view['photo_id']}/preview")
    assert preview.status_code == 200
    assert preview.content == photo
    assert preview.headers["content-type"] == "image/png"
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_supported_images_are_fully_decoded(image_format):
    pytest.importorskip("PIL.WebPImagePlugin") if image_format == "WEBP" else None
    verified = photo_staging.verify_photo(_image_bytes(image_format))
    assert verified["image_format"] == image_format
    assert verified["width"] == 4
    assert verified["height"] == 3
    assert verified["byte_count"] > 0
    assert len(verified["source_sha256"]) == 64


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_truncated_corrupt_and_appended_polyglot_inputs_are_rejected(image_format):
    pytest.importorskip("PIL.WebPImagePlugin") if image_format == "WEBP" else None
    valid = _image_bytes(image_format)
    candidates = [valid[:-1], valid + b"PK\x03\x04hidden-payload"]
    if image_format == "JPEG":
        candidates.append(valid + b"private-payload\xff\xd9")
    if image_format == "PNG":
        candidates.extend(valid[:-amount] for amount in range(1, 5))
    candidates.append(b"not an image")
    for content in candidates:
        with pytest.raises(photo_staging.PhotoStageError):
            photo_staging.verify_photo(content)


def test_png_iend_crc_is_verified():
    damaged = bytearray(_image_bytes("PNG"))
    damaged[-1] ^= 1

    with pytest.raises(photo_staging.PhotoStageError) as error:
        photo_staging.verify_photo(bytes(damaged))

    assert error.value.code == "invalid_image"


def test_invalid_upload_leaves_no_stage_row_or_private_file(client):
    root = photo_staging._stage_root()
    before = set(root.iterdir()) if root.exists() else set()

    response = _upload(client, _image_bytes("PNG") + b"appended-payload")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_image"
    assert db.one("SELECT COUNT(*) AS n FROM look_photo_stage")["n"] == 0
    assert (set(root.iterdir()) if root.exists() else set()) == before


def test_database_insert_failure_precedes_all_private_file_writes(client, monkeypatch):
    root = photo_staging._stage_root()
    before = set(root.iterdir()) if root.exists() else set()
    original_run = db.run

    def fail_stage_insert(sql, *args):
        if sql.lstrip().startswith("INSERT INTO look_photo_stage"):
            raise RuntimeError("simulated database insert failure")
        return original_run(sql, *args)

    with monkeypatch.context() as patch:
        patch.setattr(db, "run", fail_stage_insert)
        with pytest.raises(RuntimeError, match="simulated database insert failure"):
            photo_staging.create_stage(_image_bytes())

    assert db.one("SELECT COUNT(*) AS n FROM look_photo_stage")["n"] == 0
    assert (set(root.iterdir()) if root.exists() else set()) == before


def test_publication_sees_durable_owner_and_returns_only_after_stage_is_live(client, monkeypatch):
    original_replace = photo_staging.os.replace
    observed = []

    def assert_reserved_before_publish(source, target):
        row = db.one("SELECT state FROM look_photo_stage WHERE staged_path = ?", str(target))
        observed.append(row["state"] if row else None)
        return original_replace(source, target)

    monkeypatch.setattr(photo_staging.os, "replace", assert_reserved_before_publish)
    response = _upload(client, _image_bytes())

    assert response.status_code == 201, response.text
    photo_id = response.json()["photo_id"]
    assert observed == ["publishing"]
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "staged"
    assert photo_staging._stage_path(photo_id).is_file()


def test_ordinary_publication_failure_records_terminal_state_and_retryable_warning(client, monkeypatch):
    photo_id = "12345678123456781234567812345678"
    from uuid import UUID

    monkeypatch.setattr(photo_staging.uuid, "uuid4", lambda: UUID(hex=photo_id))
    original_unlink = photo_staging._safe_unlink

    def fail_replace(_source, _target):
        raise OSError("private publication detail")

    with monkeypatch.context() as patch:
        patch.setattr(photo_staging.os, "replace", fail_replace)
        patch.setattr(photo_staging, "_safe_unlink", lambda *_args: (False, "private cleanup detail"))
        with pytest.raises(photo_staging.PhotoStageError) as error:
            photo_staging.create_stage(_image_bytes())
        assert error.value.status_code == 500
        assert error.value.code == "photo_stage_unavailable"
        assert "private" not in error.value.message

        row = db.one("SELECT state, cleanup_state FROM look_photo_stage WHERE photo_id = ?", photo_id)
        assert row == {"state": "cancelled", "cleanup_state": "failed"}
        status = photo_staging.get_stage(photo_id)
        assert status["cleanup_warning"] == photo_staging._CLEANUP_WARNING
        assert "private" not in json.dumps(status)
        assert photo_staging._temporary_path(photo_id).is_file()

        patch.setattr(photo_staging, "_safe_unlink", original_unlink)
        recovered = photo_staging.get_stage(photo_id)
        assert recovered["cleanup_warning"] is None
        assert not photo_staging._temporary_path(photo_id).exists()


def test_upload_counts_actual_file_bytes_and_accepts_exact_ten_mib_with_multipart_overhead(client):
    exact = _jpeg_of_exact_size(photo_staging.MAX_PHOTO_BYTES)
    assert len(exact) == photo_staging.MAX_PHOTO_BYTES
    response = _upload(client, exact, filename="exact.jpg", media_type="application/octet-stream")

    assert response.status_code == 201, response.text
    assert response.json()["byte_count"] == photo_staging.MAX_PHOTO_BYTES
    assert photo_staging._stage_path(response.json()["photo_id"]).stat().st_size == photo_staging.MAX_PHOTO_BYTES

    too_large = _upload(client, exact + b"x", filename="large.jpg")
    assert too_large.status_code == 413, too_large.text
    assert too_large.json()["detail"]["code"] == "photo_too_large"
    assert db.one("SELECT COUNT(*) AS n FROM look_photo_stage")["n"] == 1


def test_upload_ignores_false_small_content_length_and_rejects_duplicate_file_parts(client):
    valid = _image_bytes("PNG")
    accepted = _upload(client, valid, headers={"content-length": "1"})
    assert accepted.status_code == 201, accepted.text

    duplicate = client.post(
        "/api/looks/photo-stages",
        files=[
            ("file", ("a.png", valid, "image/png")),
            ("file", ("b.png", valid, "image/png")),
        ],
    )
    assert duplicate.status_code == 422, duplicate.text
    assert duplicate.json()["detail"]["code"] == "invalid_multipart"
    assert db.one("SELECT COUNT(*) AS n FROM look_photo_stage")["n"] == 1


def test_dimension_limit_is_checked_before_decode():
    data = bytearray(_image_bytes("PNG"))
    data[16:20] = (5001).to_bytes(4, "big")
    data[20:24] = (5000).to_bytes(4, "big")
    data[29:33] = (binascii.crc32(data[12:29]) & 0xFFFFFFFF).to_bytes(4, "big")
    with pytest.raises(photo_staging.PhotoStageError) as error:
        photo_staging.verify_photo(bytes(data))
    assert error.value.code == "photo_dimensions_exceeded"


def test_save_is_atomic_manual_and_idempotent_then_cleans_bytes(client):
    uploaded = _upload(client, _image_bytes())
    photo_id = uploaded.json()["photo_id"]
    payload = {
        "name": "Blue layers",
        "appearance": "Short dark hair and soft makeup.",
        "garments": [{"wording": "a blue cotton shirt"}],
    }

    saved = client.post(f"/api/looks/photo-stages/{photo_id}/save", json=payload)
    assert saved.status_code == 200, saved.text
    result = saved.json()
    stage = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
    assert stage["state"] == "saved"
    assert stage["cleanup_state"] == "cleaned"
    assert stage["saved_look_key"] == result["key"]
    assert stage["saved_look_version"] == result["version"]
    assert not photo_staging._stage_path(photo_id).exists()
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1
    snapshot = json.loads(db.one(
        "SELECT snapshot_json FROM saved_look_version WHERE look_key = ?",
        result["key"],
    )["snapshot_json"])
    assert set(snapshot) == {"look_id", "version", "content_digest", "appearance", "outfit"}

    replay = client.post(f"/api/looks/photo-stages/{photo_id}/save", json=payload)
    assert replay.status_code == 200
    assert replay.json() == result
    changed = client.post(f"/api/looks/photo-stages/{photo_id}/save", json={**payload, "name": "Changed"})
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "idempotency_conflict"
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 1
    terminal = client.get(f"/api/looks/photo-stages/{photo_id}")
    assert terminal.status_code == 200
    assert terminal.json()["state"] == "saved"
    assert terminal.json()["saved_look"] == {"key": result["key"], "version": result["version"]}
    assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").status_code == 410


def test_cancel_is_post_commit_and_cleanup_failure_is_visible_and_retried(client, monkeypatch):
    uploaded = _upload(client, _image_bytes())
    photo_id = uploaded.json()["photo_id"]
    original_unlink = photo_staging._safe_unlink
    monkeypatch.setattr(photo_staging, "_safe_unlink", lambda *_args: (False, "private filesystem detail"))

    cancelled = client.post(f"/api/looks/photo-stages/{photo_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    assert cancelled.json()["cleanup_warning"] == photo_staging._CLEANUP_WARNING
    assert "private filesystem detail" not in cancelled.text
    assert photo_staging._stage_path(photo_id).exists()

    monkeypatch.setattr(photo_staging, "_safe_unlink", original_unlink)
    recovered = client.get(f"/api/looks/photo-stages/{photo_id}")
    assert recovered.status_code == 200
    assert recovered.json()["cleanup_warning"] is None
    assert not photo_staging._stage_path(photo_id).exists()


def test_cleanup_continues_to_final_file_when_temporary_disappears_during_unlink(client, monkeypatch):
    uploaded = _upload(client, _image_bytes())
    photo_id = uploaded.json()["photo_id"]
    target = photo_staging._stage_path(photo_id)
    temporary = photo_staging._temporary_path(photo_id)
    temporary.write_bytes(b"partial staged bytes")
    original_unlink = Path.unlink

    def remove_temp_then_raise(path, *args, **kwargs):
        if path == temporary:
            original_unlink(path, *args, **kwargs)
            raise FileNotFoundError
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", remove_temp_then_raise)
    cleaned, warning = photo_staging._safe_unlink(photo_id, str(target))

    assert cleaned is True
    assert warning == ""
    assert not temporary.exists()
    assert not target.exists()


def test_nested_rollback_preserves_photo_bytes_and_staged_state(client):
    uploaded = _upload(client, _image_bytes())
    photo_id = uploaded.json()["photo_id"]
    path = photo_staging._stage_path(photo_id)
    with pytest.raises(RuntimeError):
        with db.transaction():
            photo_staging.cancel_stage(photo_id)
            raise RuntimeError("rollback")

    row = db.one("SELECT state, cleanup_state FROM look_photo_stage WHERE photo_id = ?", photo_id)
    assert row == {"state": "staged", "cleanup_state": "none"}
    assert path.exists()


def test_stage_creation_inside_outer_transaction_is_rejected_before_writing(client):
    root = photo_staging._stage_root()
    before = set(root.iterdir()) if root.exists() else set()

    with db.transaction():
        with pytest.raises(photo_staging.PhotoStageError) as error:
            photo_staging.create_stage(_image_bytes())
        assert error.value.status_code == 409
        assert error.value.code == "photo_stage_transaction_active"

    assert db.one("SELECT COUNT(*) AS n FROM look_photo_stage")["n"] == 0
    assert (set(root.iterdir()) if root.exists() else set()) == before


def test_cancel_expiring_inside_transaction_commits_expiry_before_returning_410(client, monkeypatch):
    uploaded = _upload(client, _image_bytes())
    photo_id = uploaded.json()["photo_id"]
    path = photo_staging._stage_path(photo_id)
    before_expiry = photo_staging._now()
    expiry = before_expiry + timedelta(seconds=1)
    after_expiry = expiry + timedelta(seconds=1)
    db.run("UPDATE look_photo_stage SET expires_at = ? WHERE photo_id = ?", photo_staging._iso(expiry), photo_id)
    moments = iter([before_expiry, before_expiry, after_expiry, after_expiry])
    monkeypatch.setattr(photo_staging, "_now", lambda: next(moments, after_expiry))

    response = client.post(f"/api/looks/photo-stages/{photo_id}/cancel")

    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "photo_stage_expired"
    row = db.one("SELECT state, cleanup_state FROM look_photo_stage WHERE photo_id = ?", photo_id)
    assert row == {"state": "expired", "cleanup_state": "cleaned"}
    assert not path.exists()


def test_bounded_recovery_advances_past_persistently_failing_cleanup(client, monkeypatch):
    instant = photo_staging._now()
    timestamp = photo_staging._iso(instant)
    photo_ids = [uuid.uuid4().hex for _ in range(51)]
    with db.transaction():
        for photo_id in photo_ids:
            db.run(
                """INSERT INTO look_photo_stage
                   (photo_id, state, created_at, updated_at, expires_at, staged_path,
                    byte_count, image_format, media_type, width, height, source_sha256,
                    cleanup_state)
                   VALUES (?, 'cancelled', ?, ?, ?, ?, 1, 'PNG', 'image/png', 1, 1, ?, 'failed')""",
                photo_id, timestamp, timestamp, timestamp, str(photo_staging._stage_path(photo_id)), "a" * 64,
            )

    attempts = []

    def fail_cleanup(photo_id, _path):
        attempts.append(photo_id)
        return False, photo_staging._CLEANUP_WARNING

    monkeypatch.setattr(photo_staging, "_safe_unlink", fail_cleanup)
    monkeypatch.setattr(photo_staging, "_sweep_cursor", 0)
    photo_staging.startup_recovery(limit=50, now=instant)
    assert len(attempts) == 50

    photo_staging.startup_recovery(limit=1, now=instant)
    assert len(attempts) == 51
    assert set(attempts) == set(photo_ids)


def test_expiry_is_fixed_retains_a_tombstone_and_purges_after_an_extra_day(client):
    uploaded = _upload(client, _image_bytes())
    photo_id = uploaded.json()["photo_id"]
    view = uploaded.json()
    created = photo_staging._parse_iso(view["created_at"])
    expires = photo_staging._parse_iso(view["expires_at"])
    assert expires - created == photo_staging.PHOTO_LIFETIME

    expire_at = photo_staging._iso(photo_staging._now() - timedelta(hours=1))
    db.run("UPDATE look_photo_stage SET expires_at = ? WHERE photo_id = ?", expire_at, photo_id)
    expired = client.get(f"/api/looks/photo-stages/{photo_id}")
    assert expired.status_code == 200
    assert expired.json()["state"] == "expired"
    assert not photo_staging._stage_path(photo_id).exists()
    assert db.one("SELECT photo_id FROM look_photo_stage WHERE photo_id = ?", photo_id)
    assert client.post(f"/api/looks/photo-stages/{photo_id}/cancel").status_code == 410
    assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").status_code == 410

    result = photo_staging.startup_recovery(
        now=photo_staging._parse_iso(expire_at) + photo_staging.TOMBSTONE_LIFETIME
    )
    assert result["purged"] == 1
    assert client.get(f"/api/looks/photo-stages/{photo_id}").status_code == 404


def test_read_preview_and_cancel_remain_available_when_writes_are_disabled(client, monkeypatch):
    uploaded = _upload(client, _image_bytes())
    photo_id = uploaded.json()["photo_id"]
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)

    assert client.get(f"/api/looks/photo-stages/{photo_id}").status_code == 200
    assert client.get(f"/api/looks/photo-stages/{photo_id}/preview").status_code == 200
    denied_save = client.post(f"/api/looks/photo-stages/{photo_id}/save", json={"name": "Manual"})
    assert denied_save.status_code == 503
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "staged"
    cancelled = client.post(f"/api/looks/photo-stages/{photo_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    denied_upload = _upload(client, _image_bytes())
    assert denied_upload.status_code == 503


def test_preview_rejects_symlink_and_resolved_path_escape_without_filesystem_symlink_support(client, monkeypatch):
    uploaded = _upload(client, _image_bytes())
    photo_id = uploaded.json()["photo_id"]
    target = photo_staging._stage_path(photo_id)
    original_is_symlink = Path.is_symlink

    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "is_symlink",
            lambda path: True if path == target else original_is_symlink(path),
        )
        symlink_response = client.get(f"/api/looks/photo-stages/{photo_id}/preview")
    assert symlink_response.status_code == 404

    original_resolve = Path.resolve
    outside = Path("outside-photo-marker").resolve()

    def resolve_outside(path, *args, **kwargs):
        return outside if path == target else original_resolve(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", resolve_outside)
        escaped_response = client.get(f"/api/looks/photo-stages/{photo_id}/preview")
    assert escaped_response.status_code == 404
