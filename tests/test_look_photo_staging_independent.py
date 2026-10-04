"""Independent black-box acceptance tests for private saved-look photo staging."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import re
import threading

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from starlette.requests import Request

import db
import main
from backend import photo_staging, saved_looks


UPLOAD_URL = "/api/looks/photo-stages"
BOUNDARY = "independent-look-photo-boundary"
PRIVATE_FILENAME = "selected-upload-name-marker.jpg"
PRIVATE_SENTINEL = "private-cleanup-detail-marker"


def _image_bytes(
    image_format: str = "JPEG",
    size: tuple[int, int] = (24, 18),
    *,
    progressive: bool = False,
    exif: Image.Exif | None = None,
    animated: bool = False,
) -> bytes:
    image = Image.new("RGB", size, (31, 91, 147))
    output = BytesIO()
    options: dict[str, object] = {}
    if image_format == "JPEG":
        options["quality"] = 88
        options["progressive"] = progressive
        if exif is not None:
            options["exif"] = exif
    elif image_format == "WEBP":
        options["quality"] = 88
        if animated:
            options.update(save_all=True, append_images=[Image.new("RGB", size, (170, 30, 60))], duration=100, loop=0)
    image.save(output, format=image_format, **options)
    return output.getvalue()


def _multipart_body(
    parts: list[tuple[str, str, str, bytes]], *, boundary: str = BOUNDARY,
) -> bytes:
    chunks: list[bytes] = []
    for name, filename, content_type, value in parts:
        chunks.extend((
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("ascii"),
            f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
            value,
            b"\r\n",
        ))
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks)


def _upload(
    client: TestClient,
    data: bytes,
    *,
    filename: str = PRIVATE_FILENAME,
    content_type: str = "application/octet-stream",
    content_length: str | None = None,
):
    body = _multipart_body([("file", filename, content_type, data)])
    headers = {"content-type": f"multipart/form-data; boundary={BOUNDARY}"}
    if content_length is not None:
        headers["content-length"] = content_length
    return client.post(UPLOAD_URL, content=body, headers=headers)


@pytest.fixture
def photo_api(client):
    """Track only this module's stages and remove its temporary records/files."""
    photo_ids: list[str] = []
    baseline = {
        row["photo_id"] for row in db.q("SELECT photo_id FROM look_photo_stage")
    }
    yield client, photo_ids
    created = {
        row["photo_id"] for row in db.q("SELECT photo_id FROM look_photo_stage")
    } - baseline
    for photo_id in set(photo_ids) | created:
        row = db.one("SELECT staged_path FROM look_photo_stage WHERE photo_id = ?", photo_id)
        path = Path(row["staged_path"]) if row else photo_staging._stage_path(photo_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        db.run("DELETE FROM look_photo_stage WHERE photo_id = ?", photo_id)


def _stage(photo_api, data: bytes | None = None) -> tuple[TestClient, list[str], dict]:
    client, photo_ids = photo_api
    response = _upload(client, data or _image_bytes())
    assert response.status_code == 201, response.text
    view = response.json()
    photo_ids.append(view["photo_id"])
    return client, photo_ids, view


def _stage_path(photo_id: str) -> Path:
    return photo_staging._stage_path(photo_id)


def _look_count() -> int:
    return db.one("SELECT COUNT(*) AS c FROM saved_look_version")["c"]


def _error_code(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def _jpeg_exact_size(target_bytes: int) -> bytes:
    """Add valid JPEG COM segments until the complete file has an exact size."""
    base = _image_bytes("JPEG")
    remaining = target_bytes - len(base)
    assert remaining >= 4
    segments: list[bytes] = []
    while remaining:
        total = min(remaining, 65_537)
        tail = remaining - total
        if 0 < tail < 4:
            total -= 4 - tail
            tail = 4
        payload_size = total - 4
        segment = b"\xff\xfe" + (payload_size + 2).to_bytes(2, "big") + (b"x" * payload_size)
        assert len(segment) == total
        segments.append(segment)
        remaining = tail
    result = base[:-2] + b"".join(segments) + base[-2:]
    assert len(result) == target_bytes
    return result


@pytest.mark.parametrize(
    ("image_format", "media_type"),
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
)
def test_upload_stages_one_real_format_with_opaque_safe_view_and_actual_preview(
    photo_api, image_format, media_type, monkeypatch,
):
    client, photo_ids = photo_api
    source = _image_bytes(image_format)
    before = _look_count()
    monkeypatch.setattr(
        main.enhance, "run",
        lambda *_args, **_kwargs: pytest.fail("selection must not invoke inference"),
    )
    monkeypatch.setattr(
        saved_looks, "create",
        lambda *_args, **_kwargs: pytest.fail("selection must not save a look"),
    )

    response = _upload(client, source, filename=PRIVATE_FILENAME, content_type="text/plain")

    assert response.status_code == 201, response.text
    view = response.json()
    photo_id = view["photo_id"]
    photo_ids.append(photo_id)
    assert re.fullmatch(r"[0-9a-f]{32}", photo_id)
    assert set(view) == {
        "photo_id", "state", "created_at", "expires_at", "format", "media_type",
        "byte_count", "width", "height", "cleanup_warning", "saved_look",
    }
    assert view["state"] == "staged"
    assert view["format"] == image_format
    assert view["media_type"] == media_type
    assert view["byte_count"] == len(source)
    assert (view["width"], view["height"]) == (24, 18)
    assert view["cleanup_warning"] is None
    assert view["saved_look"] is None
    assert PRIVATE_FILENAME not in json.dumps(view)
    assert str(_stage_path(photo_id)) not in json.dumps(view)
    assert _look_count() == before

    preview = client.get(f"{UPLOAD_URL}/{photo_id}/preview")
    assert preview.status_code == 200
    assert preview.content == source
    assert preview.headers["content-type"].split(";", 1)[0] == media_type
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.headers["cache-control"] == "no-store"


def test_jpeg_progressive_and_exif_marker_bytes_are_valid_container_content(photo_api):
    client, photo_ids = photo_api
    exif = Image.Exif()
    exif[0x9286] = b"ASCII\x00\x00\x00metadata-with-\xff\xd9-inside"
    progressive = _image_bytes("JPEG", progressive=True, exif=exif)
    app1 = progressive.find(b"\xff\xe1")
    segment_length = int.from_bytes(progressive[app1 + 2:app1 + 4], "big")
    assert b"\xff\xd9" in progressive[app1:app1 + 2 + segment_length]

    response = _upload(client, progressive, filename="picture.webp", content_type="image/webp")

    assert response.status_code == 201, response.text
    photo_ids.append(response.json()["photo_id"])
    assert response.json()["format"] == "JPEG"
    assert client.get(f"{UPLOAD_URL}/{response.json()['photo_id']}/preview").content == progressive


@pytest.mark.parametrize("image_format", ["JPEG", "PNG", "WEBP"])
def test_truncated_or_appended_polyglot_container_is_refused_before_staging(photo_api, image_format):
    client, _ = photo_api
    valid = _image_bytes(image_format)
    count_before = db.one("SELECT COUNT(*) AS c FROM look_photo_stage")["c"]

    for malformed in (valid[:-1], valid + b"untrusted-trailing-payload"):
        response = _upload(client, malformed)
        assert response.status_code == 422, response.text
        assert _error_code(response) == "invalid_image"
    assert db.one("SELECT COUNT(*) AS c FROM look_photo_stage")["c"] == count_before


@pytest.mark.parametrize("missing_crc_bytes", [1, 2, 3, 4])
def test_png_iend_crc_cannot_be_truncated_or_corrupted(photo_api, missing_crc_bytes):
    client, _ = photo_api
    png = _image_bytes("PNG")
    truncated = _upload(client, png[:-missing_crc_bytes])
    assert truncated.status_code == 422, truncated.text

    corrupted_crc = png[:-1] + bytes([png[-1] ^ 0x01])
    corrupted = _upload(client, corrupted_crc)
    assert corrupted.status_code == 422, corrupted.text
    assert db.one("SELECT COUNT(*) AS c FROM look_photo_stage")["c"] == 0


def test_webp_riff_length_and_single_frame_contract_are_enforced(photo_api):
    client, _ = photo_api
    valid = _image_bytes("WEBP")
    wrong_length = bytearray(valid)
    wrong_length[4:8] = (int.from_bytes(wrong_length[4:8], "little") + 1).to_bytes(4, "little")
    assert _upload(client, bytes(wrong_length)).status_code == 422

    animated = _image_bytes("WEBP", animated=True)
    response = _upload(client, animated)
    assert response.status_code == 422, response.text
    assert _error_code(response) == "multiple_frames_unsupported"
    assert db.one("SELECT COUNT(*) AS c FROM look_photo_stage")["c"] == 0


def test_unsupported_image_and_multiple_multipart_parts_are_refused(photo_api):
    client, _ = photo_api
    gif_buffer = BytesIO()
    Image.new("RGB", (8, 8), (3, 4, 5)).save(gif_buffer, format="GIF")
    assert _upload(client, gif_buffer.getvalue(), filename="valid.jpg", content_type="image/jpeg").status_code == 422

    file_bytes = _image_bytes()
    two_files = _multipart_body([
        ("file", "first.jpg", "image/jpeg", file_bytes),
        ("file", "second.jpg", "image/jpeg", file_bytes),
    ])
    extra_field = _multipart_body([
        ("file", "first.jpg", "image/jpeg", file_bytes),
        ("caption", "caption.txt", "text/plain", b"unused"),
    ])
    headers = {"content-type": f"multipart/form-data; boundary={BOUNDARY}"}
    assert client.post(UPLOAD_URL, content=two_files, headers=headers).status_code == 422
    assert client.post(UPLOAD_URL, content=extra_field, headers=headers).status_code == 422
    assert db.one("SELECT COUNT(*) AS c FROM look_photo_stage")["c"] == 0


def test_exact_file_byte_limit_excludes_multipart_framing_and_one_byte_over_is_rejected(photo_api):
    client, photo_ids = photo_api
    exact = _jpeg_exact_size(photo_staging.MAX_PHOTO_BYTES)
    body = _multipart_body([("file", "large.jpg", "image/jpeg", exact)])
    assert len(body) > photo_staging.MAX_PHOTO_BYTES
    assert len(body) <= photo_staging.MAX_MULTIPART_BYTES

    response = client.post(
        UPLOAD_URL,
        content=body,
        headers={"content-type": f"multipart/form-data; boundary={BOUNDARY}"},
    )
    assert response.status_code == 201, response.text
    photo_ids.append(response.json()["photo_id"])
    assert response.json()["byte_count"] == photo_staging.MAX_PHOTO_BYTES

    over = _jpeg_exact_size(photo_staging.MAX_PHOTO_BYTES + 1)
    too_large = _upload(client, over)
    assert too_large.status_code == 413, too_large.text
    assert _error_code(too_large) == "photo_too_large"


def _read_streamed_multipart(data: bytes, *, content_length: str | None) -> bytes:
    body = _multipart_body([("file", "exact.jpg", "image/jpeg", data)])
    headers = [(b"content-type", f"multipart/form-data; boundary={BOUNDARY}".encode("ascii"))]
    if content_length is not None:
        headers.append((b"content-length", content_length.encode("ascii")))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": UPLOAD_URL,
        "raw_path": UPLOAD_URL.encode("ascii"), "query_string": b"", "headers": headers,
        "client": ("testclient", 50000), "server": ("testserver", 80),
    }
    chunks = [body[index:index + 64 * 1024] for index in range(0, len(body), 64 * 1024)]
    index = 0

    async def receive():
        nonlocal index
        chunk = chunks[index]
        more = index < len(chunks) - 1
        index += 1
        return {"type": "http.request", "body": chunk, "more_body": more}

    return asyncio.run(photo_staging.read_multipart_photo(Request(scope, receive), BOUNDARY))


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_actual_stream_limit_accepts_exact_file_with_missing_or_false_small_length(declared_length):
    exact = _jpeg_exact_size(photo_staging.MAX_PHOTO_BYTES)
    parsed = _read_streamed_multipart(exact, content_length=declared_length)
    assert parsed == exact


@pytest.mark.parametrize(("width", "height", "accepted"), [(5000, 5000, True), (5000, 5001, False)])
def test_pixel_limit_accepts_exact_25_megapixels_and_rejects_one_row_more(
    photo_api, width, height, accepted,
):
    client, photo_ids = photo_api
    image = Image.new("1", (width, height), 0)
    output = BytesIO()
    image.save(output, format="PNG")
    data = output.getvalue()

    response = _upload(client, data, filename="dimensions.png", content_type="image/png")

    if accepted:
        assert response.status_code == 201, response.text
        photo_ids.append(response.json()["photo_id"])
        assert response.json()["width"] * response.json()["height"] == photo_staging.MAX_PHOTO_PIXELS
    else:
        assert response.status_code == 422, response.text
        assert _error_code(response) == "photo_dimensions_exceeded"


def test_fixed_expiry_lazy_recovery_terminal_tombstone_and_expired_mutation_statuses(photo_api, monkeypatch):
    client, photo_ids = photo_api
    fixed = datetime(2035, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
    monkeypatch.setattr(photo_staging, "_now", lambda: fixed)
    client, photo_ids, view = _stage(photo_api)
    photo_id = view["photo_id"]
    staged_file = _stage_path(photo_id)
    created = datetime.fromisoformat(view["created_at"])
    expires = datetime.fromisoformat(view["expires_at"])
    assert expires - created == timedelta(hours=24)

    monkeypatch.setattr(photo_staging, "_now", lambda: fixed + timedelta(hours=20))
    first_read = client.get(f"{UPLOAD_URL}/{photo_id}")
    assert first_read.status_code == 200
    assert first_read.json()["expires_at"] == view["expires_at"]
    assert client.get(f"{UPLOAD_URL}/{photo_id}/preview").status_code == 200
    assert client.get(f"{UPLOAD_URL}/{photo_id}").json()["expires_at"] == view["expires_at"]

    expired_at = expires + timedelta(seconds=1)
    monkeypatch.setattr(photo_staging, "_now", lambda: expired_at)
    expired = client.get(f"{UPLOAD_URL}/{photo_id}")
    assert expired.status_code == 200
    assert expired.json()["state"] == "expired"
    assert not staged_file.exists()
    assert client.get(f"{UPLOAD_URL}/{photo_id}/preview").status_code == 410
    assert client.post(f"{UPLOAD_URL}/{photo_id}/cancel").status_code == 410
    save = client.post(f"{UPLOAD_URL}/{photo_id}/save", json={"name": "Expired look"})
    assert save.status_code == 410, save.text

    just_before_purge = expires + photo_staging.TOMBSTONE_LIFETIME - timedelta(seconds=1)
    monkeypatch.setattr(photo_staging, "_now", lambda: just_before_purge)
    assert client.get(f"{UPLOAD_URL}/{photo_id}").status_code == 200
    at_purge_boundary = expires + photo_staging.TOMBSTONE_LIFETIME
    monkeypatch.setattr(photo_staging, "_now", lambda: at_purge_boundary)
    assert client.get(f"{UPLOAD_URL}/{photo_id}").status_code == 404


def test_startup_recovery_is_bounded_and_locked_early_cleanup_does_not_starve_later_stage(photo_api, monkeypatch):
    client, photo_ids = photo_api
    fixed = datetime(2036, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(photo_staging, "_now", lambda: fixed)
    monkeypatch.setattr(photo_staging, "_sweep_cursor", 0)
    expired_ids = []
    for _ in range(3):
        stage = photo_staging.create_stage(_image_bytes())
        photo_ids.append(stage["photo_id"])
        expired_ids.append(stage["photo_id"])
    past = (fixed - timedelta(seconds=1)).isoformat(timespec="seconds")
    for photo_id in expired_ids:
        db.run("UPDATE look_photo_stage SET expires_at = ? WHERE photo_id = ?", past, photo_id)

    original_unlink = photo_staging._safe_unlink

    def fail_first(photo_id, stored_path):
        if photo_id == expired_ids[0]:
            return False, PRIVATE_SENTINEL
        return original_unlink(photo_id, stored_path)

    monkeypatch.setattr(photo_staging, "_safe_unlink", fail_first)
    first = photo_staging.startup_recovery(limit=1, now=fixed)
    assert first["expired"] == 1
    first_row = db.one("SELECT state, cleanup_state FROM look_photo_stage WHERE photo_id = ?", expired_ids[0])
    assert first_row == {"state": "expired", "cleanup_state": "failed"}

    second = photo_staging.startup_recovery(limit=1, now=fixed + timedelta(seconds=1))
    assert second["expired"] == 1
    second_row = db.one("SELECT state, cleanup_state FROM look_photo_stage WHERE photo_id = ?", expired_ids[1])
    assert second_row == {"state": "expired", "cleanup_state": "cleaned"}
    public = client.get(f"{UPLOAD_URL}/{expired_ids[0]}")
    assert public.status_code == 200
    warning = public.json()["cleanup_warning"]
    assert warning
    assert PRIVATE_SENTINEL not in public.text
    assert str(_stage_path(expired_ids[0])) not in public.text


@pytest.mark.parametrize("action", ["save", "cancel"])
def test_save_and_cancel_cleanup_wait_for_outermost_commit_and_survive_rollback(photo_api, action):
    client, photo_ids, view = _stage(photo_api)
    photo_id = view["photo_id"]
    staged_file = _stage_path(photo_id)
    before_looks = _look_count()
    payload = {"name": "Nested transaction look", "appearance": "Short dark curls."}

    try:
        with db.transaction():
            if action == "save":
                photo_staging.save_look(photo_id, payload, saved_looks.create)
            else:
                photo_staging.cancel_stage(photo_id)
            assert staged_file.is_file()
            assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == (
                "saved" if action == "save" else "cancelled"
            )
            raise RuntimeError("outer rollback sentinel")
    except RuntimeError as exc:
        assert str(exc) == "outer rollback sentinel"

    assert staged_file.is_file()
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "staged"
    assert _look_count() == before_looks

    with db.transaction():
        if action == "save":
            result = photo_staging.save_look(photo_id, payload, saved_looks.create)
        else:
            result = photo_staging.cancel_stage(photo_id)
        assert staged_file.is_file()
        if action == "save":
            assert result["key"]
        else:
            assert result["state"] == "cancelled"

    assert not staged_file.exists()
    if action == "save":
        assert _look_count() == before_looks + 1
        assert client.get(f"{UPLOAD_URL}/{photo_id}").json()["state"] == "saved"
    else:
        assert _look_count() == before_looks
        assert client.get(f"{UPLOAD_URL}/{photo_id}").json()["state"] == "cancelled"


@pytest.mark.parametrize("action", ["save", "cancel"])
def test_lost_postcommit_callback_is_recovered_on_later_stage_access(photo_api, monkeypatch, action):
    client, photo_ids, view = _stage(photo_api)
    photo_id = view["photo_id"]
    staged_file = _stage_path(photo_id)
    before_looks = _look_count()
    captured = []
    original_on_commit = db.on_commit
    monkeypatch.setattr(db, "on_commit", lambda callback: captured.append(callback))
    payload = {"name": "Callback recovery look", "appearance": "A side braid."}

    if action == "save":
        result = photo_staging.save_look(photo_id, payload, saved_looks.create)
        assert result["key"]
    else:
        result = photo_staging.cancel_stage(photo_id)
        assert result["state"] == "cancelled"
    assert len(captured) == 1
    assert staged_file.is_file()
    row = db.one("SELECT state, cleanup_state FROM look_photo_stage WHERE photo_id = ?", photo_id)
    assert row == {"state": "saved" if action == "save" else "cancelled", "cleanup_state": "pending"}

    monkeypatch.setattr(db, "on_commit", original_on_commit)
    recovered = client.get(f"{UPLOAD_URL}/{photo_id}")
    assert recovered.status_code == 200
    assert recovered.json()["state"] == ("saved" if action == "save" else "cancelled")
    assert recovered.json()["cleanup_warning"] is None
    assert not staged_file.exists()
    if action == "save":
        replay = client.post(f"{UPLOAD_URL}/{photo_id}/save", json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json() == result
        conflict = client.post(f"{UPLOAD_URL}/{photo_id}/save", json={**payload, "name": "Different look"})
        assert conflict.status_code == 409
        assert _error_code(conflict) == "idempotency_conflict"
        assert _look_count() == before_looks + 1
    else:
        assert _look_count() == before_looks


def test_concurrent_same_payload_saves_replay_one_persisted_look(photo_api):
    client, photo_ids, view = _stage(photo_api)
    photo_id = view["photo_id"]
    before_looks = _look_count()
    payload = {"name": "Concurrent saved look", "appearance": "A neat bob."}
    barrier = threading.Barrier(3)

    def save_once():
        worker_client = TestClient(main.app)
        barrier.wait(timeout=5)
        try:
            return worker_client.post(f"{UPLOAD_URL}/{photo_id}/save", json=payload)
        finally:
            worker_client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save_once)
        second = pool.submit(save_once)
        barrier.wait(timeout=5)
        responses = [first.result(timeout=10), second.result(timeout=10)]

    assert [response.status_code for response in responses] == [200, 200], [
        (response.status_code, response.text) for response in responses
    ]
    assert responses[0].json() == responses[1].json()
    assert _look_count() == before_looks + 1
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "saved"


def test_cancel_cleanup_warning_is_sanitized_and_retryable(photo_api, monkeypatch):
    client, photo_ids, view = _stage(photo_api)
    photo_id = view["photo_id"]
    staged_file = _stage_path(photo_id)
    real_unlink = photo_staging._safe_unlink
    monkeypatch.setattr(
        photo_staging, "_safe_unlink",
        lambda _photo_id, _stored: (False, f"{PRIVATE_SENTINEL}: {staged_file}"),
    )

    cancelled = client.post(f"{UPLOAD_URL}/{photo_id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    warning = cancelled.json()["cleanup_warning"]
    assert warning
    assert warning == client.get(f"{UPLOAD_URL}/{photo_id}").json()["cleanup_warning"]
    assert PRIVATE_SENTINEL not in warning
    assert str(staged_file) not in warning
    assert staged_file.is_file()

    monkeypatch.setattr(photo_staging, "_safe_unlink", real_unlink)
    retry = client.get(f"{UPLOAD_URL}/{photo_id}")
    assert retry.status_code == 200
    assert retry.json()["cleanup_warning"] is None
    assert retry.json()["state"] == "cancelled"
    assert not staged_file.exists()


@pytest.mark.parametrize("action", ["save", "cancel"])
def test_expiry_crossing_inside_cancel_or_save_does_not_return_a_live_stage(photo_api, monkeypatch, action):
    client, photo_ids, view = _stage(photo_api)
    photo_id = view["photo_id"]
    expires = datetime.fromisoformat(view["expires_at"])
    before = expires - timedelta(seconds=1)
    after = expires + timedelta(seconds=1)
    moments = iter((before, after, after, after, after))
    monkeypatch.setattr(photo_staging, "_now", lambda: next(moments, after))

    if action == "cancel":
        response = client.post(f"{UPLOAD_URL}/{photo_id}/cancel")
    else:
        response = client.post(
            f"{UPLOAD_URL}/{photo_id}/save",
            json={"name": "Crossed expiry"},
        )

    assert response.status_code == 410, response.text
    stage = db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)
    assert stage["state"] == "expired"


def test_stage_creation_is_refused_inside_outer_transaction_without_orphan_file(photo_api):
    _, _photo_ids = photo_api
    root = photo_staging._stage_root()
    before_files = {path.name for path in root.glob("*")} if root.exists() else set()
    before_rows = db.one("SELECT COUNT(*) AS c FROM look_photo_stage")["c"]

    with db.transaction():
        with pytest.raises(photo_staging.PhotoStageError) as error:
            photo_staging.create_stage(_image_bytes())
        assert error.value.status_code == 409
        assert error.value.code == "photo_stage_transaction_active"
        after_files = {path.name for path in root.glob("*")} if root.exists() else set()
        assert after_files == before_files
        assert db.one("SELECT COUNT(*) AS c FROM look_photo_stage")["c"] == before_rows

    assert db.one("SELECT COUNT(*) AS c FROM look_photo_stage")["c"] == before_rows


def test_process_loss_after_photo_publication_is_recovered_after_fixed_expiry(photo_api, monkeypatch):
    _, photo_ids = photo_api
    fixed = datetime(2037, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(photo_staging, "_now", lambda: fixed)
    photo_id = "12345678123456781234567812345678"
    photo_ids.append(photo_id)
    generated = iter((photo_id, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"))
    from uuid import UUID
    monkeypatch.setattr(photo_staging.uuid, "uuid4", lambda: UUID(hex=next(generated)))
    actual_replace = photo_staging.os.replace

    def publish_then_terminate(source, target):
        actual_replace(source, target)
        raise SystemExit("simulated process loss after final-file publication")

    monkeypatch.setattr(photo_staging.os, "replace", publish_then_terminate)
    with pytest.raises(SystemExit, match="simulated process loss"):
        photo_staging.create_stage(_image_bytes())

    target = _stage_path(photo_id)
    assert target.is_file()

    photo_staging.startup_recovery(now=fixed + timedelta(hours=25))

    assert not target.exists()


def test_process_loss_during_temporary_write_is_recovered_after_fixed_expiry(photo_api, monkeypatch):
    _, photo_ids = photo_api
    fixed = datetime(2037, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(photo_staging, "_now", lambda: fixed)
    photo_id = "22345678123456781234567812345678"
    photo_ids.append(photo_id)
    from uuid import UUID

    actual_uuid4 = photo_staging.uuid.uuid4
    first_uuid = True

    def stable_stage_id():
        nonlocal first_uuid
        if first_uuid:
            first_uuid = False
            return UUID(hex=photo_id)
        return actual_uuid4()

    monkeypatch.setattr(photo_staging.uuid, "uuid4", stable_stage_id)
    root = photo_staging._stage_root()
    before = set(root.iterdir()) if root.exists() else set()
    actual_open = Path.open

    class PartialWriteThenTerminate:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, value):
            self.stream.write(value[: max(1, len(value) // 2)])
            raise SystemExit("simulated process loss during temporary write")

        def __getattr__(self, name):
            return getattr(self.stream, name)

    def partial_open(path, *args, **kwargs):
        stream = actual_open(path, *args, **kwargs)
        if path.parent == root and path.name.endswith(".tmp"):
            return PartialWriteThenTerminate(stream)
        return stream

    monkeypatch.setattr(Path, "open", partial_open)
    payload = _image_bytes()
    with pytest.raises(SystemExit, match="during temporary write"):
        photo_staging.create_stage(payload)

    row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
    assert row is not None, "the recovery owner row must predate temporary-file publication"
    artifacts = {path for path in root.iterdir() if path not in before and path.is_file()}
    assert artifacts, "the injected interruption must leave a partial owned staging artifact"
    assert any(0 < path.stat().st_size < len(payload) for path in artifacts)

    photo_staging.startup_recovery(now=fixed + timedelta(hours=25))

    assert all(not path.exists() for path in artifacts)
    assert not _stage_path(photo_id).exists()
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "expired"


def test_process_loss_before_rename_is_recovered_after_fixed_expiry(photo_api, monkeypatch):
    _, photo_ids = photo_api
    fixed = datetime(2037, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(photo_staging, "_now", lambda: fixed)
    photo_id = "32345678123456781234567812345678"
    photo_ids.append(photo_id)
    from uuid import UUID

    actual_uuid4 = photo_staging.uuid.uuid4
    first_uuid = True

    def stable_stage_id():
        nonlocal first_uuid
        if first_uuid:
            first_uuid = False
            return UUID(hex=photo_id)
        return actual_uuid4()

    monkeypatch.setattr(photo_staging.uuid, "uuid4", stable_stage_id)
    root = photo_staging._stage_root()
    before = set(root.iterdir()) if root.exists() else set()

    def terminate_before_rename(_source, _target):
        raise SystemExit("simulated process loss before final-file publication")

    monkeypatch.setattr(photo_staging.os, "replace", terminate_before_rename)
    with pytest.raises(SystemExit, match="before final-file publication"):
        photo_staging.create_stage(_image_bytes())

    row = db.one("SELECT * FROM look_photo_stage WHERE photo_id = ?", photo_id)
    assert row is not None, "the recovery owner row must predate final-file publication"
    artifacts = {path for path in root.iterdir() if path not in before and path.is_file()}
    assert artifacts
    assert not _stage_path(photo_id).exists()

    photo_staging.startup_recovery(now=fixed + timedelta(hours=25))

    assert all(not path.exists() for path in artifacts)
    assert db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)["state"] == "expired"


def test_publishing_row_is_not_visible_or_previewable_before_final_rename(photo_api, monkeypatch):
    client, photo_ids = photo_api
    photo_id = "42345678123456781234567812345678"
    photo_ids.append(photo_id)
    from uuid import UUID

    actual_uuid4 = photo_staging.uuid.uuid4
    first_uuid = True

    def stable_stage_id():
        nonlocal first_uuid
        if first_uuid:
            first_uuid = False
            return UUID(hex=photo_id)
        return actual_uuid4()

    monkeypatch.setattr(photo_staging.uuid, "uuid4", stable_stage_id)
    actual_replace = photo_staging.os.replace
    rename_entered = threading.Event()
    allow_rename = threading.Event()

    def paused_rename(source, target):
        rename_entered.set()
        if not allow_rename.wait(timeout=10):
            raise TimeoutError("test did not release the final rename")
        actual_replace(source, target)

    monkeypatch.setattr(photo_staging.os, "replace", paused_rename)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(photo_staging.create_stage, _image_bytes())
        try:
            assert rename_entered.wait(timeout=10), "publication did not reach the controlled rename boundary"
            row = db.one("SELECT state FROM look_photo_stage WHERE photo_id = ?", photo_id)
            assert row == {"state": "publishing"}
            assert not _stage_path(photo_id).exists()
            assert client.get(f"{UPLOAD_URL}/{photo_id}").status_code == 404
            assert client.get(f"{UPLOAD_URL}/{photo_id}/preview").status_code == 404
        finally:
            allow_rename.set()
        created = future.result(timeout=10)

    assert created["state"] == "staged"
    assert client.get(f"{UPLOAD_URL}/{photo_id}").status_code == 200
    assert client.get(f"{UPLOAD_URL}/{photo_id}/preview").status_code == 200


def test_missing_temporary_path_race_does_not_skip_final_file_cleanup(photo_api, monkeypatch):
    client, _photo_ids, view = _stage(photo_api)
    photo_id = view["photo_id"]
    staged = _stage_path(photo_id)
    temporary = photo_staging._temporary_path(photo_id)
    temporary.write_bytes(b"owned-temporary-marker")
    actual_unlink = Path.unlink
    simulated_race = False

    def disappear_during_temporary_unlink(path, *args, **kwargs):
        nonlocal simulated_race
        if path == temporary and not simulated_race:
            simulated_race = True
            actual_unlink(path, *args, **kwargs)
            raise FileNotFoundError("temporary disappeared after the existence check")
        return actual_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", disappear_during_temporary_unlink)

    response = client.post(f"{UPLOAD_URL}/{photo_id}/cancel")

    assert response.status_code == 200, response.text
    assert simulated_race
    assert not temporary.exists()
    assert not staged.exists(), "cleanup must continue to the final file after one owned path vanished"
    assert db.one("SELECT cleanup_state FROM look_photo_stage WHERE photo_id = ?", photo_id)["cleanup_state"] == "cleaned"


def test_preview_refuses_a_staged_path_replaced_with_external_symlink(photo_api, tmp_path):
    client, photo_ids, view = _stage(photo_api)
    photo_id = view["photo_id"]
    staged = _stage_path(photo_id)
    outside = tmp_path / "outside-image-marker.bin"
    outside.write_bytes(b"outside-private-content")
    staged.unlink()
    try:
        staged.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"filesystem symlinks unavailable: {type(exc).__name__}")

    response = client.get(f"{UPLOAD_URL}/{photo_id}/preview")

    assert response.status_code == 404, response.text
    assert b"outside-private-content" not in response.content


def test_preview_refuses_symlink_even_when_native_symlinks_are_unavailable(photo_api, monkeypatch):
    client, _photo_ids, view = _stage(photo_api)
    staged = _stage_path(view["photo_id"])
    actual_is_symlink = Path.is_symlink

    def report_stage_as_symlink(path):
        if path == staged:
            return True
        return actual_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", report_stage_as_symlink)

    response = client.get(f"{UPLOAD_URL}/{view['photo_id']}/preview")

    assert response.status_code == 404, response.text
    assert b"outside-private-content" not in response.content


def test_preview_refuses_a_resolved_path_outside_the_staging_root(photo_api, monkeypatch, tmp_path):
    client, _photo_ids, view = _stage(photo_api)
    staged = _stage_path(view["photo_id"])
    outside = tmp_path / "outside-private-content.bin"
    outside.write_bytes(b"outside-private-content")
    actual_resolve = Path.resolve

    def resolve_stage_outside(path, *args, **kwargs):
        if path == staged:
            return outside
        return actual_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_stage_outside)

    response = client.get(f"{UPLOAD_URL}/{view['photo_id']}/preview")

    assert response.status_code == 404, response.text
    assert b"outside-private-content" not in response.content


def test_disabled_writes_keep_stage_reads_preview_and_cancel_available(photo_api, monkeypatch):
    client, photo_ids, view = _stage(photo_api)
    photo_id = view["photo_id"]
    source = client.get(f"{UPLOAD_URL}/{photo_id}/preview").content
    monkeypatch.setattr(main, "is_resource_planning_enabled", lambda: False)

    refused_upload = _upload(client, _image_bytes())
    refused_save = client.post(f"{UPLOAD_URL}/{photo_id}/save", json={"name": "Disabled save"})
    assert refused_upload.status_code == 503
    assert refused_save.status_code == 503
    assert _error_code(refused_upload) == "resource_planning_disabled"
    assert _error_code(refused_save) == "resource_planning_disabled"
    assert client.get(f"{UPLOAD_URL}/{photo_id}").status_code == 200
    preview = client.get(f"{UPLOAD_URL}/{photo_id}/preview")
    assert preview.status_code == 200
    assert preview.content == source
    cancelled = client.post(f"{UPLOAD_URL}/{photo_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
