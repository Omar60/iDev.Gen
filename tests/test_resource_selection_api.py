"""HTTP boundary tests for resource import selections (Task 1.2).

The domain primitives live in ``backend.resource_selection``; Task 1.2
exposes them through six routes under
``/api/resources/import-selections``. These tests pin the public
contract of those routes:

  1. The exact surface (six routes, no more, no less).
  2. The status-code matrix for create, upload, remove, target,
     cancel, and read.
  3. The stable error envelope (``{"detail": {"code", "message",
     "current"}}``) and the absence of any private field at any
     depth of any response — success or failure.
  4. The idempotency guarantees: same ``request_id`` replays as the
     same selection; same ``upload_id`` + same bytes returns 200 with
     the existing view; same ``upload_id`` + different bytes is
     rejected with 409 and leaves the staged file untouched.
  5. Cross-selection isolation: ``file_id`` from a different
     selection returns 404 with no leakage of which selection it
     actually belongs to.
  6. Resource planning gate: writes are 503 when disabled, reads
     remain available.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import uuid
from pathlib import Path

import pytest

import db
import resource_import
from backend import resource_parser
from backend import resource_selection as rs
from backend import resource_service
from backend.main import (
    app,
    is_resource_planning_enabled,
    parse_and_validate_multipart_content_type,
)
from backend.resource_selection import MAX_SAFE_INTEGER


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _unique_request_id() -> str:
    """Return a unique valid UUID string for ``request_id``."""
    return str(uuid.uuid4())


def _unique(prefix: str) -> str:
    """Return a unique string for ``upload_id``."""
    return f"{prefix}_{uuid.uuid4().hex}"


@pytest.fixture
def fresh_db():
    """Wipe the resource_selection tables between tests.

    The conftest ``client`` fixture shares one SQLite database across
    tests. Each test creates its own selections, but a wipe keeps the
    surface narrow and avoids revision drift from the prior test.
    """
    with db.transaction():
        for table in (
            "resource_selection_file",
            "resource_selection",
        ):
            db.run(f"DELETE FROM {table}")
    yield


def _stable_detail(resp) -> dict:
    """Return the ``detail`` dict of a stable error envelope, or raise."""
    body = resp.json()
    if "detail" not in body:
        raise AssertionError(
            f"expected stable error envelope with 'detail' key, got {body!r}"
        )
    detail = body["detail"]
    if not isinstance(detail, dict):
        raise AssertionError(f"detail must be a dict, got {type(detail).__name__}")
    assert "code" in detail and isinstance(detail["code"], str)
    assert "message" in detail and isinstance(detail["message"], str)
    return detail


def _assert_view_is_public(view) -> None:
    """Recursively assert the view contains no private field at any depth."""
    rs.assert_selection_view_is_public(view)


# ---------------------------------------------------------------------------
# Test API: Create / Get Selection
# ---------------------------------------------------------------------------


def test_create_selection_returns_201_and_safe_view(client, fresh_db):
    body = {"request_id": _unique_request_id()}
    resp = client.post("/api/resources/import-selections", json=body)
    assert resp.status_code == 201, resp.text

    view = resp.json()
    assert view["state"] == "open"
    assert view["selection_revision"] == 0
    assert view["files"] == []
    assert view["preview"] is None
    assert view["commit_result"] is None
    assert set(view.keys()) == rs.SELECTION_VIEW_KEYS
    _assert_view_is_public(view)


def test_create_selection_replay_returns_200_with_same_id(client, fresh_db):
    request_id = _unique_request_id()
    first = client.post("/api/resources/import-selections", json={"request_id": request_id})
    assert first.status_code == 201
    first_view = first.json()

    second = client.post("/api/resources/import-selections", json={"request_id": request_id})
    assert second.status_code == 200
    second_view = second.json()

    # The replay must return the same selection_id and the same
    # expires_at (a fresh creation would mint a new selection_id and a
    # new 24-hour clock).
    assert first_view["selection_id"] == second_view["selection_id"]
    assert first_view["expires_at"] == second_view["expires_at"]
    _assert_view_is_public(second_view)


def test_create_selection_rejects_empty_request_id(client, fresh_db):
    resp = client.post("/api/resources/import-selections", json={"request_id": ""})
    # Pydantic v2 enforces min_length=1 -> 422 from FastAPI itself.
    assert resp.status_code == 422


def test_create_selection_rejects_whitespace_request_id(client, fresh_db):
    resp = client.post("/api/resources/import-selections", json={"request_id": "   "})
    # The route trims and rejects empty.
    assert resp.status_code == 422
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_request"
    assert "request_id" in detail["message"]


def test_create_selection_rejects_non_uuid_request_id(client, fresh_db):
    for bad in ("not-a-uuid", "create_1234", "12345", "g" * 36):
        resp = client.post("/api/resources/import-selections", json={"request_id": bad})
        assert resp.status_code == 422, f"expected 422 for {bad!r}, got {resp.status_code}"
        detail = _stable_detail(resp)
        assert detail["code"] == "invalid_request"
    count = db.one("SELECT COUNT(*) AS c FROM resource_selection")["c"]
    assert count == 0


def test_create_selection_rejects_wrong_type_and_missing_body(client, fresh_db):
    for bad_body in (
        {"request_id": 12345},
        {"request_id": True},
        {"request_id": False},
        {"request_id": None},
        {"request_id": ["550e8400-e29b-41d4-a716-446655440000"]},
        {},
    ):
        resp = client.post("/api/resources/import-selections", json=bad_body)
        assert resp.status_code == 422, f"expected 422 for {bad_body!r}"
        _stable_detail(resp)

    resp_empty = client.post(
        "/api/resources/import-selections",
        content=b"",
        headers={"Content-Type": "application/json"},
    )
    assert resp_empty.status_code == 422
    _stable_detail(resp_empty)

    count = db.one("SELECT COUNT(*) AS c FROM resource_selection")["c"]
    assert count == 0


def test_create_selection_rejects_extra_fields(client, fresh_db):
    body = {"request_id": _unique_request_id(), "extra_field": "forbidden"}
    resp = client.post("/api/resources/import-selections", json=body)
    assert resp.status_code == 422
    detail = _stable_detail(resp)
    assert detail["code"] == "extra_field_forbidden"
    count = db.one("SELECT COUNT(*) AS c FROM resource_selection")["c"]
    assert count == 0


def test_create_selection_canonicalizes_equivalent_uuid_spellings_on_replay(client, fresh_db):
    raw_uuid = str(uuid.uuid4())
    resp1 = client.post("/api/resources/import-selections", json={"request_id": raw_uuid})
    assert resp1.status_code == 201
    sid = resp1.json()["selection_id"]

    resp2 = client.post("/api/resources/import-selections", json={"request_id": raw_uuid.upper()})
    assert resp2.status_code == 200
    assert resp2.json()["selection_id"] == sid

    resp3 = client.post("/api/resources/import-selections", json={"request_id": f"{{{raw_uuid}}}"})
    assert resp3.status_code == 200
    assert resp3.json()["selection_id"] == sid

    count = db.one("SELECT COUNT(*) AS c FROM resource_selection")["c"]
    assert count == 1


def test_get_selection_returns_view(client, fresh_db):
    create = client.post(
        "/api/resources/import-selections", json={"request_id": _unique_request_id()}
    ).json()

    resp = client.get(f"/api/resources/import-selections/{create['selection_id']}")
    assert resp.status_code == 200
    view = resp.json()
    assert view["selection_id"] == create["selection_id"]
    assert set(view.keys()) == rs.SELECTION_VIEW_KEYS
    _assert_view_is_public(view)


def test_get_selection_unknown_returns_stable_404(client, fresh_db):
    resp = client.get("/api/resources/import-selections/sel_does_not_exist")
    assert resp.status_code == 404
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_not_found"
    assert "current" not in detail


# ---------------------------------------------------------------------------
# Test API: Upload File (Streaming + Idempotency)
# ---------------------------------------------------------------------------


def _create_open_selection(client) -> dict:
    resp = client.post(
        "/api/resources/import-selections", json={"request_id": _unique_request_id()}
    )
    assert resp.status_code in (200, 201)
    return resp.json()


def _upload_file(
    client,
    selection_id: str,
    *,
    upload_id: str,
    file_name: str,
    payload: bytes,
):
    return client.post(
        f"/api/resources/import-selections/{selection_id}/files",
        files={"file": (file_name, io.BytesIO(payload), "application/octet-stream")},
        data={"upload_id": upload_id},
    )


def test_upload_file_returns_201_and_safe_view_with_file(client, fresh_db):
    sel = _create_open_selection(client)
    payload = b'{"rooms":[{"id":"r1"}]}'
    resp = _upload_file(
        client, sel["selection_id"], upload_id=_unique("u1"), file_name="room.json", payload=payload
    )
    assert resp.status_code == 201, resp.text
    view = resp.json()
    assert len(view["files"]) == 1
    f_view = view["files"][0]
    assert f_view["file_name"] == "room.json"
    assert f_view["byte_count"] == len(payload)
    assert f_view["status"] == "staged"
    assert set(f_view.keys()) == rs.FILE_VIEW_KEYS
    assert view["selection_revision"] == 1
    _assert_view_is_public(view)


def test_upload_file_replay_same_bytes_returns_200_and_no_new_revision(client, fresh_db):
    sel = _create_open_selection(client)
    payload = b'{"replay":"same_bytes"}'
    upload_id = _unique("u-replay-same")

    first = _upload_file(
        client, sel["selection_id"], upload_id=upload_id, file_name="r.json", payload=payload
    )
    assert first.status_code == 201
    first_view = first.json()
    revision_after_first = first_view["selection_revision"]
    file_id = first_view["files"][0]["file_id"]

    second = _upload_file(
        client, sel["selection_id"], upload_id=upload_id, file_name="r.json", payload=payload
    )
    assert second.status_code == 200, second.text
    second_view = second.json()

    # Same selection, same revision, same file_id, same byte_count.
    assert second_view["selection_id"] == first_view["selection_id"]
    assert second_view["selection_revision"] == revision_after_first
    assert len(second_view["files"]) == 1
    assert second_view["files"][0]["file_id"] == file_id
    assert second_view["files"][0]["byte_count"] == len(payload)
    _assert_view_is_public(second_view)


def test_upload_file_replay_different_bytes_returns_409_and_leaves_staged_file_untouched(
    client, fresh_db
):
    sel = _create_open_selection(client)
    payload_a = b"AAAA"
    payload_b = b"BBBBBBBB"
    upload_id = _unique("u-replay-diff")

    first = _upload_file(
        client, sel["selection_id"], upload_id=upload_id, file_name="r.json", payload=payload_a
    )
    assert first.status_code == 201
    first_view = first.json()
    first_file = first_view["files"][0]
    revision_after_first = first_view["selection_revision"]

    second = _upload_file(
        client, sel["selection_id"], upload_id=upload_id, file_name="r.json", payload=payload_b
    )
    assert second.status_code == 409, second.text
    detail = _stable_detail(second)
    assert detail["code"] == "idempotency_conflict"

    # The current view in the error envelope must show the still-staged
    # file with the original bytes and revision.
    current = detail["current"]
    _assert_view_is_public(current)
    assert current["selection_id"] == sel["selection_id"]
    assert current["selection_revision"] == revision_after_first
    assert len(current["files"]) == 1
    assert current["files"][0]["file_id"] == first_file["file_id"]
    assert current["files"][0]["byte_count"] == len(payload_a)

    # The on-disk staged file must still hold the original bytes.
    raw = db.one(
        "SELECT staged_path, staged_sha256, byte_count FROM resource_selection_file WHERE file_id = ?",
        first_file["file_id"],
    )
    assert Path(raw["staged_path"]).read_bytes() == payload_a
    assert raw["byte_count"] == len(payload_a)


def test_upload_file_same_upload_id_different_file_name_returns_409(client, fresh_db):
    sel = _create_open_selection(client)
    payload = b"same"
    upload_id = _unique("u-replay-name")

    first = _upload_file(
        client, sel["selection_id"], upload_id=upload_id, file_name="a.json", payload=payload
    )
    assert first.status_code == 201

    second = _upload_file(
        client, sel["selection_id"], upload_id=upload_id, file_name="b.json", payload=payload
    )
    assert second.status_code == 409, second.text
    detail = _stable_detail(second)
    assert detail["code"] == "idempotency_conflict"
    assert "current" in detail
    _assert_view_is_public(detail["current"])


def test_upload_file_to_unknown_selection_returns_404(client, fresh_db):
    resp = _upload_file(
        client,
        "sel_does_not_exist",
        upload_id=_unique("u-404"),
        file_name="r.json",
        payload=b"x",
    )
    assert resp.status_code == 404
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_not_found"


def test_upload_file_to_expired_selection_returns_410(client, fresh_db):
    sel = _create_open_selection(client)
    # Force the selection past its expiry by rewriting the row.
    db.run(
        "UPDATE resource_selection SET expires_at = ? WHERE selection_id = ?",
        "2000-01-01T00:00:00+00:00",
        sel["selection_id"],
    )

    resp = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-exp"),
        file_name="r.json",
        payload=b"x",
    )
    assert resp.status_code == 410, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] in ("selection_expired", "selection_terminal")
    assert "current" in detail
    _assert_view_is_public(detail["current"])


def test_upload_file_with_empty_body_returns_422_and_cleans_reservation(client, fresh_db):
    sel = _create_open_selection(client)
    resp = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-empty"),
        file_name="empty.json",
        payload=b"",
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "empty_file"

    # The reservation must be rolled back: the selection has no files.
    view = client.get(f"/api/resources/import-selections/{sel['selection_id']}").json()
    assert view["files"] == []
    assert view["selection_revision"] == 0


def test_upload_duplicate_file_parts_rejected_with_stable_422(client, fresh_db):
    sel = _create_open_selection(client)
    url = f"/api/resources/import-selections/{sel['selection_id']}/files"
    files = [
        ("file", ("a.json", io.BytesIO(b"abc"), "application/octet-stream")),
        ("file", ("b.json", io.BytesIO(b"def"), "application/octet-stream")),
    ]
    data = {"upload_id": _unique("dup-file")}
    resp = client.post(url, files=files, data=data)
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_multipart"
    count = db.one("SELECT COUNT(*) AS c FROM resource_selection_file WHERE selection_id = ?", sel["selection_id"])["c"]
    assert count == 0


def test_upload_duplicate_upload_id_parts_rejected_with_stable_422(client, fresh_db):
    sel = _create_open_selection(client)
    url = f"/api/resources/import-selections/{sel['selection_id']}/files"
    files = {"file": ("a.json", io.BytesIO(b"abc"), "application/octet-stream")}
    data = [("upload_id", "u1"), ("upload_id", "u2")]
    resp = client.post(url, files=files, data=data)
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_multipart"
    count = db.one("SELECT COUNT(*) AS c FROM resource_selection_file WHERE selection_id = ?", sel["selection_id"])["c"]
    assert count == 0


def test_upload_missing_file_or_upload_id_rejected_with_stable_422(client, fresh_db):
    sel = _create_open_selection(client)
    url = f"/api/resources/import-selections/{sel['selection_id']}/files"

    resp1 = client.post(url, data={"upload_id": _unique("no-file")})
    assert resp1.status_code == 422
    assert _stable_detail(resp1)["code"] in ("missing_field", "invalid_multipart")

    resp2 = client.post(url, files={"file": ("a.json", io.BytesIO(b"abc"), "application/octet-stream")})
    assert resp2.status_code == 422
    assert _stable_detail(resp2)["code"] in ("missing_field", "invalid_multipart")

    resp3 = client.post(
        url,
        files={"file": ("a.json", io.BytesIO(b"abc"), "application/octet-stream")},
        data={"upload_id": "   "},
    )
    assert resp3.status_code == 422
    assert _stable_detail(resp3)["code"] in ("invalid_request", "invalid_multipart")

    resp4 = client.post(
        url,
        files={"file": ("a.json", io.BytesIO(b"abc"), "application/octet-stream")},
        data={"upload_id": _unique("extra"), "unexpected_key": "val"},
    )
    assert resp4.status_code == 422
    assert _stable_detail(resp4)["code"] == "invalid_multipart"

    count = db.one("SELECT COUNT(*) AS c FROM resource_selection_file WHERE selection_id = ?", sel["selection_id"])["c"]
    assert count == 0


def test_upload_malformed_multipart_returns_stable_422(client, fresh_db):
    sel = _create_open_selection(client)
    url = f"/api/resources/import-selections/{sel['selection_id']}/files"
    # Malformed part header causes Starlette/python-multipart to raise 400,
    # which our exception handler normalizes to stable 422 with code "invalid_multipart".
    malformed_body = b"--myboundary\r\nInvalidHeaderWithoutColon\r\n\r\n"
    resp = client.post(
        url,
        content=malformed_body,
        headers={"Content-Type": "multipart/form-data; boundary=myboundary"},
    )
    assert resp.status_code == 422, f"expected stable 422, got {resp.status_code}: {resp.text}"
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_multipart"
    count = db.one("SELECT COUNT(*) AS c FROM resource_selection_file WHERE selection_id = ?", sel["selection_id"])["c"]
    assert count == 0


# ---------------------------------------------------------------------------
# Test API: Remove File
# ---------------------------------------------------------------------------


def test_remove_file_returns_200_and_bumps_revision(client, fresh_db):
    sel = _create_open_selection(client)
    create_resp = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-rm"),
        file_name="r.json",
        payload=b"abc",
    )
    assert create_resp.status_code == 201
    file_id = create_resp.json()["files"][0]["file_id"]
    rev_after_upload = create_resp.json()["selection_revision"]

    resp = client.delete(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        params={"expected_revision": rev_after_upload},
    )
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert view["files"] == []
    assert view["selection_revision"] > rev_after_upload
    _assert_view_is_public(view)


def test_remove_file_with_stale_revision_returns_409(client, fresh_db):
    sel = _create_open_selection(client)
    create_resp = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-rm-stale"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = create_resp.json()["files"][0]["file_id"]

    # Bump revision via another upload so the first revision is stale.
    second = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-rm-stale-2"),
        file_name="r2.json",
        payload=b"def",
    )
    assert second.status_code == 201
    current_revision = second.json()["selection_revision"]

    resp = client.delete(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        params={"expected_revision": current_revision - 1},
    )
    assert resp.status_code == 409, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_revision_stale"
    _assert_view_is_public(detail["current"])


def test_remove_file_unknown_returns_404(client, fresh_db):
    sel = _create_open_selection(client)
    resp = client.delete(
        f"/api/resources/import-selections/{sel['selection_id']}/files/file_does_not_exist",
        params={"expected_revision": 0},
    )
    assert resp.status_code == 404
    detail = _stable_detail(resp)
    assert detail["code"] == "file_not_found"
    # The error must not leak which selection the file actually
    # belongs to (or whether it exists in any selection at all).
    assert "current" not in detail


def test_remove_file_cross_selection_returns_path_free_404(client, fresh_db):
    """A file_id from another selection must return 404 with no leakage."""
    sel_a = _create_open_selection(client)
    sel_b = _create_open_selection(client)

    # Upload one file into sel_a.
    a_resp = _upload_file(
        client,
        sel_a["selection_id"],
        upload_id=_unique("u-cross"),
        file_name="r.json",
        payload=b"data",
    )
    a_file_id = a_resp.json()["files"][0]["file_id"]

    # Attempt to remove that file using sel_b's URL.
    resp = client.delete(
        f"/api/resources/import-selections/{sel_b['selection_id']}/files/{a_file_id}",
        params={"expected_revision": 0},
    )
    assert resp.status_code == 404, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "file_not_found"
    # The error must not surface sel_a in any way.
    assert "current" not in detail
    assert sel_a["selection_id"] not in resp.text
    assert sel_b["selection_id"] not in resp.text


def test_remove_file_in_cancelled_selection_returns_410(client, fresh_db):
    sel = _create_open_selection(client)
    create_resp = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-rm-cancel"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = create_resp.json()["files"][0]["file_id"]
    rev_after_upload = create_resp.json()["selection_revision"]

    # Cancel the selection.
    cancel_resp = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/cancel",
        json={"expected_revision": rev_after_upload},
    )
    assert cancel_resp.status_code == 200

    resp = client.delete(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        params={"expected_revision": rev_after_upload + 1},
    )
    assert resp.status_code == 410, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_terminal"
    _assert_view_is_public(detail["current"])


# ---------------------------------------------------------------------------
# Test API: Patch File Target
# ---------------------------------------------------------------------------


def test_patch_file_target_returns_200_and_bumps_revision(client, fresh_db):
    sel = _create_open_selection(client)
    create_resp = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-patch"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = create_resp.json()["files"][0]["file_id"]
    rev = create_resp.json()["selection_revision"]

    resp = client.patch(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        json={
            "expected_revision": rev,
            "effective_library_key": "room_library_one",
            "effective_auxiliary_kind": "cut_map",
        },
    )
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert view["selection_revision"] > rev
    target = next(f for f in view["files"] if f["file_id"] == file_id)
    assert target["effective_library_key"] == "room_library_one"
    assert target["effective_auxiliary_kind"] == "cut_map"
    _assert_view_is_public(view)


def test_patch_file_target_unknown_returns_404(client, fresh_db):
    sel = _create_open_selection(client)
    resp = client.patch(
        f"/api/resources/import-selections/{sel['selection_id']}/files/file_does_not_exist",
        json={"expected_revision": 0, "effective_library_key": "lib_a"},
    )
    assert resp.status_code == 404
    detail = _stable_detail(resp)
    assert detail["code"] == "file_not_found"


def test_patch_file_target_stale_revision_returns_409(client, fresh_db):
    sel = _create_open_selection(client)
    create_resp = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-patch-stale"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = create_resp.json()["files"][0]["file_id"]
    stale_rev = create_resp.json()["selection_revision"]

    # Bump revision.
    _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-patch-stale-2"),
        file_name="r2.json",
        payload=b"def",
    )

    resp = client.patch(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        json={"expected_revision": stale_rev, "effective_library_key": "lib_x"},
    )
    assert resp.status_code == 409, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_revision_stale"
    _assert_view_is_public(detail["current"])


def test_patch_file_target_invalid_revision_returns_422(client, fresh_db):
    sel = _create_open_selection(client)
    create_resp = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-patch-bad-rev"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = create_resp.json()["files"][0]["file_id"]

    resp = client.patch(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        json={"expected_revision": -1},
    )
    # FastAPI/Pydantic enforces ge=0 -> 422.
    assert resp.status_code == 422


def test_patch_file_target_oversized_revision_returns_422(client, fresh_db):
    sel = _create_open_selection(client)
    create_resp = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-patch-overflow"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = create_resp.json()["files"][0]["file_id"]

    oversized = MAX_SAFE_INTEGER + 1
    resp = client.patch(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        json={
            "expected_revision": oversized,
            "effective_library_key": "lib_overflow",
        },
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_revision"
    assert "current" in detail
    _assert_view_is_public(detail["current"])


def test_patch_file_target_rejects_non_integer_revisions(client, fresh_db):
    sel = _create_open_selection(client)
    create_resp = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-patch-types"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = create_resp.json()["files"][0]["file_id"]
    initial_rev = create_resp.json()["selection_revision"]

    for bad_rev in (True, False, "0", "1", 0.0, 1.0, 1.5, None, -1, MAX_SAFE_INTEGER + 1):
        resp = client.patch(
            f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
            json={"expected_revision": bad_rev, "effective_library_key": "k"},
        )
        assert resp.status_code == 422, f"expected 422 for {bad_rev!r}, got {resp.status_code}"
        detail = _stable_detail(resp)
        assert detail["code"] == "invalid_revision"

    # Zero mutation: revision unchanged, target fields untouched
    view = client.get(f"/api/resources/import-selections/{sel['selection_id']}").json()
    assert view["selection_revision"] == initial_rev
    assert view["files"][0]["effective_library_key"] is None


# ---------------------------------------------------------------------------
# Test API: Cancel Selection
# ---------------------------------------------------------------------------


def test_cancel_selection_returns_200_and_transitions_state(client, fresh_db):
    sel = _create_open_selection(client)
    upload = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-cancel"),
        file_name="r.json",
        payload=b"abc",
    )
    rev_after_upload = upload.json()["selection_revision"]

    resp = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/cancel",
        json={"expected_revision": rev_after_upload},
    )
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert view["state"] == "cancelled"
    _assert_view_is_public(view)


def test_cancel_unknown_selection_returns_404(client, fresh_db):
    resp = client.post(
        "/api/resources/import-selections/sel_does_not_exist/cancel",
        json={"expected_revision": 0},
    )
    assert resp.status_code == 404
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_not_found"


def test_cancel_already_cancelled_returns_410(client, fresh_db):
    sel = _create_open_selection(client)
    first = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/cancel",
        json={"expected_revision": 0},
    )
    assert first.status_code == 200
    rev_after_cancel = first.json()["selection_revision"]

    second = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/cancel",
        json={"expected_revision": rev_after_cancel},
    )
    assert second.status_code == 410, second.text
    detail = _stable_detail(second)
    assert detail["code"] == "selection_terminal"
    _assert_view_is_public(detail["current"])


def test_cancel_committing_returns_409(client, fresh_db):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    # Simulate a live Task 1.3 commit claim: the row is in the
    # ``committing`` state AND has a future ``claim_lease_deadline`` so
    # ``recover_selection`` does not treat it as a stale claim and
    # transition the state back to ``open``. The route must refuse the
    # cancel and return 409 ``commit_active``.
    future_deadline = "2099-01-01T00:00:00+00:00"
    now_iso = rs._now_iso()
    prev_tok = "tok_simulated"
    man_dig = "a" * 64
    prev_rep = json.dumps({
        "version": 1,
        "phase": "preview",
        "summary": {
            "files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0,
            "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0,
            "updated": 0, "missing": 0,
        },
        "files": [],
        "missing_source_entries": [],
    })
    db.run(
        "UPDATE resource_selection SET state = 'committing', "
        "    preview_token = ?, preview_manifest_digest = ?, preview_committable = 1, "
        "    preview_report = ?, preview_created_at = ?, "
        "    claim_lease_deadline = ?, "
        "    claim_commit_token = 'ct_simulated', "
        "    claim_revision = 0, "
        "    claim_preview_token = ?, "
        "    claim_manifest_digest = ? "
        "WHERE selection_id = ?",
        prev_tok,
        man_dig,
        prev_rep,
        now_iso,
        future_deadline,
        prev_tok,
        man_dig,
        sid,
    )
    resp = client.post(
        f"/api/resources/import-selections/{sid}/cancel",
        json={"expected_revision": 0},
    )
    assert resp.status_code == 409, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "commit_active"
    _assert_view_is_public(detail["current"])


def test_cancel_with_stale_revision_returns_409(client, fresh_db):
    sel = _create_open_selection(client)
    upload = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("u-cancel-stale"),
        file_name="r.json",
        payload=b"abc",
    )
    rev_after_upload = upload.json()["selection_revision"]

    resp = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/cancel",
        json={"expected_revision": rev_after_upload - 1},
    )
    assert resp.status_code == 409, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_revision_stale"
    _assert_view_is_public(detail["current"])


def test_cancel_rejects_non_integer_revisions(client, fresh_db):
    sel = _create_open_selection(client)

    for bad_rev in (True, False, "0", "1", 0.0, 1.0, 1.5, None, -1, MAX_SAFE_INTEGER + 1):
        resp = client.post(
            f"/api/resources/import-selections/{sel['selection_id']}/cancel",
            json={"expected_revision": bad_rev},
        )
        assert resp.status_code == 422, f"expected 422 for {bad_rev!r}, got {resp.status_code}"
        detail = _stable_detail(resp)
        assert detail["code"] == "invalid_revision"

    # Selection remains open, zero mutation
    view = client.get(f"/api/resources/import-selections/{sel['selection_id']}").json()
    assert view["state"] == "open"
    assert view["selection_revision"] == 0


# ---------------------------------------------------------------------------
# Test API: Resource Planning Gate
# ---------------------------------------------------------------------------


def test_resource_planning_disabled_blocks_writes_with_503_and_keeps_reads(
    client, fresh_db, monkeypatch
):
    sel = _create_open_selection(client)
    # The conftest imports ``main`` (not ``backend.main``); FastAPI's
    # app was built on that namespace, so the route handler reads
    # ``is_resource_planning_enabled`` from ``main.__dict__``. The
    # patch must hit the same module the conftest loaded.
    import main as main_module

    monkeypatch.setattr(main_module, "is_resource_planning_enabled", lambda: False)

    # Reads still work.
    read = client.get(f"/api/resources/import-selections/{sel['selection_id']}")
    assert read.status_code == 200

    # Writes return 503 with the stable envelope.
    write = client.post(
        "/api/resources/import-selections", json={"request_id": _unique_request_id()}
    )
    assert write.status_code == 503, write.text
    detail = _stable_detail(write)
    assert detail["code"] == "resource_planning_disabled"

    upload = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("gate-up"),
        file_name="r.json",
        payload=b"x",
    )
    assert upload.status_code == 503
    detail = _stable_detail(upload)
    assert detail["code"] == "resource_planning_disabled"

    delete = client.delete(
        f"/api/resources/import-selections/{sel['selection_id']}/files/file_x",
        params={"expected_revision": 0},
    )
    assert delete.status_code == 503
    detail = _stable_detail(delete)
    assert detail["code"] == "resource_planning_disabled"

    patch = client.patch(
        f"/api/resources/import-selections/{sel['selection_id']}/files/file_x",
        json={"expected_revision": 0},
    )
    assert patch.status_code == 503
    detail = _stable_detail(patch)
    assert detail["code"] == "resource_planning_disabled"

    cancel = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/cancel",
        json={"expected_revision": 0},
    )
    assert cancel.status_code == 503
    detail = _stable_detail(cancel)
    assert detail["code"] == "resource_planning_disabled"


# ---------------------------------------------------------------------------
# Test API: Private-Field Absence (Sentinel)
# ---------------------------------------------------------------------------


def test_no_response_carries_private_field_at_any_depth(client, fresh_db):
    """No success or error response carries any private field at any depth.

    The check walks the full response tree and fails on any of the
    sentinel field names the design names as private. A new column
    added to ``resource_selection`` (or to ``resource_selection_file``)
    that lands on this list would fail this test, which is the
    structural guard against a public surface widening by accident.
    """
    # Drive every route through both happy and unhappy paths to collect
    # response bodies.
    payloads: list[tuple[str, int, object]] = []

    def _record(label: str, resp) -> None:
        try:
            payloads.append((label, resp.status_code, resp.json()))
        except json.JSONDecodeError:
            payloads.append((label, resp.status_code, resp.text))

    # Create selection.
    create_resp = client.post(
        "/api/resources/import-selections", json={"request_id": _unique_request_id()}
    )
    _record("create", create_resp)
    sel = create_resp.json()
    sid = sel["selection_id"]

    # Upload.
    upload_resp = _upload_file(
        client,
        sid,
        upload_id=_unique("sentinel-up"),
        file_name="r.json",
        payload=b'{"k":"v"}',
    )
    _record("upload", upload_resp)
    fid = upload_resp.json()["files"][0]["file_id"]

    # Read.
    read_resp = client.get(f"/api/resources/import-selections/{sid}")
    _record("read", read_resp)

    # Patch.
    patch_resp = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": 1, "effective_library_key": "lib_a"},
    )
    _record("patch", patch_resp)

    # Replay with different bytes (409).
    replay_diff_resp = _upload_file(
        client,
        sid,
        upload_id="sentinel-up",  # reuse same upload_id
        file_name="r.json",
        payload=b"different",
    )
    _record("replay_diff_bytes", replay_diff_resp)

    # Cross-selection 404.
    other_sel = client.post(
        "/api/resources/import-selections", json={"request_id": _unique_request_id()}
    ).json()
    cross_resp = client.delete(
        f"/api/resources/import-selections/{other_sel['selection_id']}/files/{fid}",
        params={"expected_revision": 0},
    )
    _record("cross_selection_404", cross_resp)

    # Unknown selection 404.
    unknown_resp = client.get("/api/resources/import-selections/sel_nope")
    _record("unknown_selection_404", unknown_resp)

    # Cancel.
    cancel_resp = client.post(
        f"/api/resources/import-selections/{sid}/cancel",
        json={"expected_revision": 2},
    )
    _record("cancel", cancel_resp)

    # Cancel-again 410.
    cancel_again_resp = client.post(
        f"/api/resources/import-selections/{sid}/cancel",
        json={"expected_revision": 3},
    )
    _record("cancel_again_410", cancel_again_resp)

    # Validate every captured payload carries no private field at any depth.
    for label, status, body in payloads:
        rs.assert_selection_view_is_public(body)


# ---------------------------------------------------------------------------
# Test API: Public Helper Contracts
# ---------------------------------------------------------------------------


def test_assert_selection_view_is_public_rejects_known_private_field():
    with pytest.raises(AssertionError, match="staged_path"):
        rs.assert_selection_view_is_public({"staged_path": "/secret/file.staged"})


def test_assert_selection_view_is_public_rejects_nested_private_field():
    leaky = {
        "selection_id": "sel_x",
        "files": [
            {
                "file_id": "file_x",
                "staged_mtime_ns": "1234567890",
            }
        ],
    }
    with pytest.raises(AssertionError, match="staged_mtime_ns"):
        rs.assert_selection_view_is_public(leaky)


def test_validate_public_revision_rejects_oversize():
    with pytest.raises(ValueError, match="0.."):
        rs.validate_public_revision(MAX_SAFE_INTEGER + 1)


def test_validate_public_revision_rejects_negative():
    with pytest.raises(ValueError):
        rs.validate_public_revision(-1)


def test_validate_public_revision_rejects_bool():
    # bool is an int subclass in Python; the helper must still reject it.
    with pytest.raises(ValueError):
        rs.validate_public_revision(True)


def test_validate_public_revision_accepts_string():
    assert rs.validate_public_revision("42") == 42


def test_validate_public_revision_accepts_max_safe_integer():
    assert rs.validate_public_revision(MAX_SAFE_INTEGER) == MAX_SAFE_INTEGER


# ---------------------------------------------------------------------------
# Repair Group Tests — Concurrency, Idempotency, Projection Safety, Cleanup
# ---------------------------------------------------------------------------


def test_create_selection_concurrent_same_request_id_converges(client, fresh_db):
    """Two concurrent creates with the same ``request_id`` converge.

    The disposition (``was_new``) is decided inside the domain layer's
    single transaction, so two concurrent calls cannot both mint a
    fresh selection. The HTTP responses agree on ``selection_id`` and
    ``expires_at`` regardless of the order they reach the client.
    """
    import threading

    request_id = _unique_request_id()
    barrier = threading.Barrier(2)
    results: list = [None, None]

    def worker(idx: int) -> None:
        barrier.wait()
        results[idx] = client.post(
            "/api/resources/import-selections", json={"request_id": request_id}
        )

    t1 = threading.Thread(target=worker, args=(0,))
    t2 = threading.Thread(target=worker, args=(1,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    codes = sorted(r.status_code for r in results)
    assert codes == [200, 201], f"expected one new and one replay, got {codes}"

    bodies = [r.json() for r in results]
    assert bodies[0]["selection_id"] == bodies[1]["selection_id"]
    assert bodies[0]["expires_at"] == bodies[1]["expires_at"]

    # Exactly one selection row must exist (no duplicate from the race).
    rows = db.q("SELECT selection_id FROM resource_selection WHERE request_id = ?", request_id)
    assert len(rows) == 1


def test_upload_replay_active_returns_409_without_streaming(client, fresh_db):
    """A second streaming call with the same upload_id gets a deterministic 409.

    The first call reserves a slot and stays in the ``reserved`` state
    until finalize. A second call arrives while the first is in
    flight: the route must NOT consume the second body, NOT mutate the
    first reservation, and NOT leak the internal ``status`` or
    ``staged_path`` of the in-flight slot.
    """
    sel = _create_open_selection(client)
    upload_id = _unique("replay-active")

    # First reservation: do not finalize yet, so the row stays 'reserved'.
    first = rs.reserve_file_slot(
        selection_id=sel["selection_id"],
        upload_id=upload_id,
        file_name="r.json",
    )
    assert first["status"] == "reserved"

    # Second call from the same upload_id: the route must refuse
    # without consuming the body.
    second_resp = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/files",
        files={"file": ("r.json", io.BytesIO(b"never streamed"), "application/octet-stream")},
        data={"upload_id": upload_id},
    )
    assert second_resp.status_code == 409, second_resp.text
    detail = _stable_detail(second_resp)
    assert detail["code"] == "idempotency_conflict"
    # The error envelope must NOT carry the in-flight selection view
    # (which would expose ``status`` and other internals).
    assert "current" not in detail, detail

    # Cleanup the first reservation so the test does not leak rows.
    rs.abort_file_reservation(sel["selection_id"], first["file_id"], reason="test cleanup")


def test_safe_projection_strips_oversized_mtime_ns_column(client, fresh_db):
    """A staged file row with ``staged_mtime_ns > MAX_SAFE_INTEGER`` must not leak.

    Asserts:
    1. The persisted staged_mtime_ns > MAX_SAFE_INTEGER remains byte-for-byte in DB.
    2. Absent from GET response.
    3. Absent from mutation success response (PATCH).
    4. Absent from relevant error responses (stale revision 409 containing 'current').
    """
    sel = _create_open_selection(client)
    upload = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("oversize-mtime"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = upload.json()["files"][0]["file_id"]
    oversized_val = MAX_SAFE_INTEGER + 100

    db.run(
        "UPDATE resource_selection_file SET staged_mtime_ns = ? WHERE file_id = ?",
        oversized_val,
        file_id,
    )

    # 1. Byte-for-byte unchanged in DB (stored as TEXT in SQLite)
    raw_db = db.one("SELECT staged_mtime_ns FROM resource_selection_file WHERE file_id = ?", file_id)
    assert raw_db["staged_mtime_ns"] == str(oversized_val)
    assert int(raw_db["staged_mtime_ns"]) == oversized_val

    # 2. Absent from GET
    get_resp = client.get(f"/api/resources/import-selections/{sel['selection_id']}")
    assert get_resp.status_code == 200
    assert "staged_mtime_ns" not in get_resp.text
    rs.assert_selection_view_is_public(get_resp.json())

    # 3. Absent from mutation success (PATCH)
    patch_resp = client.patch(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        json={"expected_revision": 1, "effective_library_key": "rooms"},
    )
    assert patch_resp.status_code == 200
    assert "staged_mtime_ns" not in patch_resp.text
    rs.assert_selection_view_is_public(patch_resp.json())

    # 4. Absent from error response returning current (stale revision 409)
    stale_resp = client.patch(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        json={"expected_revision": 0, "effective_library_key": "rooms"},
    )
    assert stale_resp.status_code == 409
    assert "staged_mtime_ns" not in stale_resp.text
    rs.assert_selection_view_is_public(stale_resp.json()["detail"]["current"])

    # Still byte-for-byte in DB
    raw_db_after = db.one("SELECT staged_mtime_ns FROM resource_selection_file WHERE file_id = ?", file_id)
    assert raw_db_after["staged_mtime_ns"] == str(oversized_val)
    assert int(raw_db_after["staged_mtime_ns"]) == oversized_val


def test_malformed_persisted_preview_report_fails_closed_with_500(client, fresh_db):
    """Unknown keys, physical paths, and private sentinels fail closed with 500 selection_state_invalid."""
    sel = _create_open_selection(client)
    leaky_report = {
        "summary": {"rooms": 5},
        "staged_path": "/secret/path",
        "source": "C:\\Windows\\System32\\cmd.exe",
        "nested": {"deep": {"claim_commit_token": "ct_secret"}},
    }
    db.run(
        "UPDATE resource_selection SET "
        "    preview_token = 'pt_test_token_1234567890abcdef', "
        "    preview_manifest_digest = 'a' * 64, "
        "    preview_committable = 1, "
        "    preview_report = ? "
        "WHERE selection_id = ?",
        json.dumps(leaky_report),
        sel["selection_id"],
    )

    resp = client.get(f"/api/resources/import-selections/{sel['selection_id']}")
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert detail["message"] == "Persisted selection state is invalid"
    assert "current" not in detail


def test_oversized_integer_in_persisted_preview_report_fails_closed(client, fresh_db):
    """An integer > MAX_SAFE_INTEGER inside persisted preview_report fails closed with 500."""
    sel = _create_open_selection(client)
    leaky_report = {
        "summary": {
            "inputs": MAX_SAFE_INTEGER + 1,
            "accepted": 42,
        },
        "accepted": MAX_SAFE_INTEGER + 1,
        "written": 10,
        "nested": {"big": MAX_SAFE_INTEGER + 999},
    }
    db.run(
        "UPDATE resource_selection SET "
        "    preview_token = 'pt_test_token_1234567890abcdef', "
        "    preview_manifest_digest = 'b' * 64, "
        "    preview_committable = 1, "
        "    preview_report = ? "
        "WHERE selection_id = ?",
        json.dumps(leaky_report),
        sel["selection_id"],
    )

    resp = client.get(f"/api/resources/import-selections/{sel['selection_id']}")
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert detail["message"] == "Persisted selection state is invalid"
    assert "current" not in detail


def test_patch_empty_body_returns_422_and_does_not_bump_revision(client, fresh_db):
    """An empty PATCH (only the revision) must be refused without bumping.

    The route enforces the no-op refusal BEFORE any DB write so the
    revision never advances on a body that asks for no change.
    """
    sel = _create_open_selection(client)
    upload = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("empty-patch"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = upload.json()["files"][0]["file_id"]
    rev_before = upload.json()["selection_revision"]

    resp = client.patch(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        json={"expected_revision": rev_before},
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_request"
    assert "at least one of" in detail["message"]

    refreshed = client.get(f"/api/resources/import-selections/{sel['selection_id']}").json()
    assert refreshed["selection_revision"] == rev_before


def test_patch_extra_field_returns_stable_422(client, fresh_db):
    """Extra fields in PATCH body are refused via ``extra="forbid"``."""
    sel = _create_open_selection(client)
    upload = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("extra-field"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = upload.json()["files"][0]["file_id"]
    rev_before = upload.json()["selection_revision"]

    resp = client.patch(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}",
        json={
            "expected_revision": rev_before,
            "effective_library_key": "lib_a",
            "staged_path": "/secret",  # unknown field
        },
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "extra_field_forbidden"


def test_delete_missing_expected_revision_returns_422(client, fresh_db):
    """A DELETE without ``expected_revision`` returns the stable envelope."""
    sel = _create_open_selection(client)
    upload = _upload_file(
        client,
        sel["selection_id"],
        upload_id=_unique("no-rev"),
        file_name="r.json",
        payload=b"abc",
    )
    file_id = upload.json()["files"][0]["file_id"]

    resp = client.delete(
        f"/api/resources/import-selections/{sel['selection_id']}/files/{file_id}"
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "missing_field"
    assert "expected_revision" in detail["message"]


def test_cancel_missing_body_returns_422(client, fresh_db):
    """A cancel without a body returns the stable envelope with the field name.

    Pydantic reports a missing body field with ``loc=('body', '<field>')``
    when the JSON body is present but empty, so the handler can echo
    the field name back. The route must surface ``expected_revision``
    in the message so the client knows which field is required.
    """
    sel = _create_open_selection(client)
    # Empty dict -> ``loc=('body', 'expected_revision')``.
    resp = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/cancel", json={}
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "missing_field"
    assert "expected_revision" in detail["message"]


def test_cancel_completely_missing_body_returns_422(client, fresh_db):
    """A cancel with NO body at all still returns the stable envelope."""
    sel = _create_open_selection(client)
    # No body, no JSON -> ``loc=('body',)`` and the field name is lost.
    resp = client.post(f"/api/resources/import-selections/{sel['selection_id']}/cancel")
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "missing_field"
    assert "body" in detail["message"]


def test_cancel_extra_field_returns_stable_422(client, fresh_db):
    """Extra fields in cancel body are refused via ``extra="forbid"``."""
    sel = _create_open_selection(client)
    resp = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/cancel",
        json={"expected_revision": 0, "staged_path": "/secret"},
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "extra_field_forbidden"


def test_failed_upload_reconciles_reservation_and_does_not_leak(client, fresh_db, monkeypatch):
    """A streaming failure aborts the reservation and reconciles counters.

    Simulates a chunk-write failure mid-stream and asserts the route
    returns the stable envelope, the per-file reservation is aborted,
    the aggregate byte counter does not leak the failed bytes, and
    the public view never exposes the staged path.
    """
    sel = _create_open_selection(client)
    upload_id = _unique("failed-upload")

    def _failing_stage_chunk(*args, **kwargs):
        # Mirror the byte accounting that ``stage_file_chunk`` does so
        # the route layer sees the same shape it would see from a real
        # I/O failure on chunk 2.
        raise OSError("simulated chunk write failure")

    monkeypatch.setattr(rs, "stage_file_chunk", _failing_stage_chunk)

    resp = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/files",
        files={"file": ("r.json", io.BytesIO(b"abcdef"), "application/octet-stream")},
        data={"upload_id": upload_id},
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "upload_failed"
    # The selection view MUST be present so the client can read the
    # current revision/aggregate counters; the projection must remain
    # safe regardless.
    assert "current" in detail
    rs.assert_selection_view_is_public(detail["current"])
    # The route must not echo the simulated exception class name.
    assert "OSError" not in resp.text

    # Counters must reconcile: the failed reservation does not leave
    # the selection with a phantom staged file.
    view = detail["current"]
    assert view["selection_revision"] == 0  # no successful finalize
    assert view["files"] == []

    raw = db.one(
        "SELECT COUNT(*) AS c FROM resource_selection_file "
        "WHERE selection_id = ? AND status = 'staged'",
        sel["selection_id"],
    )
    assert raw["c"] == 0


def test_concurrent_uploads_same_upload_id_converges_without_corruption(client, fresh_db, monkeypatch):
    """Two concurrent HTTP uploads with the same upload_id result in one 201 and one 409.

    Uses synchronization events to ensure upload 2 hits the active reservation
    while upload 1 is mid-stream. Proves no double append, corruption, second file,
    counter/revision drift, or internal-field leak.
    """
    import asyncio
    import threading
    from starlette.datastructures import UploadFile as StarletteUploadFile

    sel = _create_open_selection(client)
    upload_id = _unique("concurrent-up")

    upload_1_reserved = threading.Event()
    upload_2_done = threading.Event()

    orig_read = StarletteUploadFile.read

    async def cooperative_read(self, size=-1):
        chunk = await orig_read(self, size)
        if chunk:
            upload_1_reserved.set()
            # Yield cooperative control to the event loop so upload_2 runs
            while not upload_2_done.is_set():
                await asyncio.sleep(0.005)
        return chunk

    monkeypatch.setattr(StarletteUploadFile, "read", cooperative_read)

    results: list = [None, None]

    def upload_1():
        results[0] = client.post(
            f"/api/resources/import-selections/{sel['selection_id']}/files",
            files={"file": ("r.json", io.BytesIO(b"first_payload"), "application/octet-stream")},
            data={"upload_id": upload_id},
        )

    def upload_2():
        assert upload_1_reserved.wait(timeout=5.0)
        results[1] = client.post(
            f"/api/resources/import-selections/{sel['selection_id']}/files",
            files={"file": ("r.json", io.BytesIO(b"first_payload"), "application/octet-stream")},
            data={"upload_id": upload_id},
        )
        upload_2_done.set()

    t1 = threading.Thread(target=upload_1)
    t2 = threading.Thread(target=upload_2)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    statuses = [r.status_code for r in results]
    assert sorted(statuses) == [201, 409], f"expected [201, 409], got {statuses}"

    resp_409 = results[1] if results[1].status_code == 409 else results[0]
    detail = _stable_detail(resp_409)
    assert detail["code"] == "idempotency_conflict"
    assert "current" not in detail

    files = db.q(
        "SELECT * FROM resource_selection_file WHERE selection_id = ?",
        sel["selection_id"],
    )
    assert len(files) == 1
    assert files[0]["status"] == "staged"
    assert files[0]["byte_count"] == len(b"first_payload")

    sel_row = db.one(
        "SELECT selection_revision, total_reserved_bytes, active_file_count "
        "FROM resource_selection WHERE selection_id = ?",
        sel["selection_id"],
    )
    assert sel_row["selection_revision"] == 1
    assert sel_row["total_reserved_bytes"] == len(b"first_payload")
    assert sel_row["active_file_count"] == 1


def test_failed_upload_finalize_reconciles_reservation_and_does_not_leak(client, fresh_db, monkeypatch):
    """Injected finalize failure reconciles reservation, counters, and does not leak."""
    sel = _create_open_selection(client)
    upload_id = _unique("failed-finalize")

    def _failing_finalize(*args, **kwargs):
        raise OSError("simulated finalize disk failure")

    monkeypatch.setattr(rs, "finalize_staged_file", _failing_finalize)

    resp = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/files",
        files={"file": ("r.json", io.BytesIO(b"payload"), "application/octet-stream")},
        data={"upload_id": upload_id},
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "finalize_failed"
    assert "current" in detail
    rs.assert_selection_view_is_public(detail["current"])

    view = detail["current"]
    assert view["selection_revision"] == 0
    assert view["files"] == []
    raw = db.one(
        "SELECT COUNT(*) AS c FROM resource_selection_file "
        "WHERE selection_id = ? AND status = 'staged'",
        sel["selection_id"],
    )
    assert raw["c"] == 0


def test_failed_upload_close_raises_still_aborts_reservation(client, fresh_db, monkeypatch):
    """If file.close() raises during error handling, abort_file_reservation is still called."""
    from starlette.datastructures import UploadFile as StarletteUploadFile

    sel = _create_open_selection(client)
    upload_id = _unique("close-fails")

    def _failing_stage(*args, **kwargs):
        raise OSError("write failed")

    monkeypatch.setattr(rs, "stage_file_chunk", _failing_stage)

    orig_close = StarletteUploadFile.close

    async def _failing_close(self):
        await orig_close(self)
        raise RuntimeError("file.close exploded")

    monkeypatch.setattr(StarletteUploadFile, "close", _failing_close)

    resp = client.post(
        f"/api/resources/import-selections/{sel['selection_id']}/files",
        files={"file": ("r.json", io.BytesIO(b"payload"), "application/octet-stream")},
        data={"upload_id": upload_id},
    )
    assert resp.status_code == 422
    view = client.get(f"/api/resources/import-selections/{sel['selection_id']}").json()
    assert view["files"] == []


def test_upload_replay_bounded_completed_and_oversized(client, fresh_db):
    """Completed replay returns 200; oversized replay returns 409 without mutating disk."""
    sel = _create_open_selection(client)
    upload_id = _unique("bounded-replay")
    orig_bytes = b"safe_bytes"

    # Initial upload -> 201
    resp1 = _upload_file(
        client, sel["selection_id"], upload_id=upload_id, file_name="r.json", payload=orig_bytes
    )
    assert resp1.status_code == 201
    rev = resp1.json()["selection_revision"]

    # Exact replay -> 200
    resp2 = _upload_file(
        client, sel["selection_id"], upload_id=upload_id, file_name="r.json", payload=orig_bytes
    )
    assert resp2.status_code == 200
    assert resp2.json()["selection_revision"] == rev

    # Oversized replay -> 409 idempotency_conflict
    oversized_bytes = orig_bytes + b"_extra_attack_bytes"
    resp3 = _upload_file(
        client, sel["selection_id"], upload_id=upload_id, file_name="r.json", payload=oversized_bytes
    )
    assert resp3.status_code == 409
    detail = _stable_detail(resp3)
    assert detail["code"] == "idempotency_conflict"

    # Conflicting bytes (same length) -> 409
    conflicting_bytes = b"diff_bytes"
    assert len(conflicting_bytes) == len(orig_bytes)
    resp4 = _upload_file(
        client, sel["selection_id"], upload_id=upload_id, file_name="r.json", payload=conflicting_bytes
    )
    assert resp4.status_code == 409
    detail4 = _stable_detail(resp4)
    assert detail4["code"] == "idempotency_conflict"

    # Original file on disk is untouched
    file_id = resp1.json()["files"][0]["file_id"]
    row = db.one("SELECT staged_path, byte_count FROM resource_selection_file WHERE file_id = ?", file_id)
    assert Path(row["staged_path"]).read_bytes() == orig_bytes
    assert row["byte_count"] == len(orig_bytes)

def test_canonical_typed_preview_and_commit_report_roundtrip(client, fresh_db, tmp_path):
    """SelectionView preview report and commit result match resource_service.safe_report exactly."""
    sel = _create_open_selection(client)
    sid = sel["selection_id"]

    res_file = tmp_path / "resources.json"
    res_file.write_text(
        json.dumps([
            {
                "id": "scene_roundtrip",
                "label": "Roundtrip Scene",
                "scene_theme": "roundtrip",
                "tags": ["test"],
            }
        ]),
        encoding="utf-8",
    )

    preview = resource_service.preview_import([(str(res_file), "roundtrip_lib")])
    serialized_preview = resource_service.preview_to_dict(preview)
    commit_report = resource_service.commit_import(serialized_preview)

    expected_preview_safe = resource_service.safe_report(preview)
    expected_commit_safe = resource_service.safe_report(commit_report)

    valid_manifest_digest = hashlib.sha256(b"roundtrip_manifest").hexdigest().lower()

    now_iso = rs._now_iso()
    db.run(
        "UPDATE resource_selection SET "
        "    state = 'committed', "
        "    preview_token = 'pt_valid_token_roundtrip_123', "
        "    preview_manifest_digest = ?, "
        "    preview_committable = 1, "
        "    preview_report = ?, "
        "    preview_created_at = ?, "
        "    committed_revision = 0, "
        "    committed_preview_token = 'pt_valid_token_roundtrip_123', "
        "    committed_manifest_digest = ?, "
        "    commit_result = ?, "
        "    committed_at = ? "
        "WHERE selection_id = ?",
        valid_manifest_digest,
        json.dumps(expected_preview_safe),
        now_iso,
        valid_manifest_digest,
        json.dumps(expected_commit_safe),
        now_iso,
        sid,
    )

    resp = client.get(f"/api/resources/import-selections/{sid}")
    assert resp.status_code == 200
    view = resp.json()
    rs.assert_selection_view_is_public(view)

    # 1. Preview structure
    assert view["preview"]["preview_token"] == "pt_valid_token_roundtrip_123"
    assert view["preview"]["manifest_digest"] == valid_manifest_digest
    assert view["preview"]["committable"] is True
    assert view["preview"]["report"] == expected_preview_safe

    # 2. Commit result structure
    assert view["commit_result"] == expected_commit_safe

    # 3. Assert repair-2 speculative / legacy fields are NOT present
    for legacy_field in ("imported_libraries", "total_assets", "result", "committed", "ok_counter"):
        assert legacy_field not in view["commit_result"]


def test_persisted_preview_uppercase_hex_digest_fails_closed(client, fresh_db):
    """An uppercase hex digest in persisted preview fails closed with 500 selection_state_invalid."""
    sel = _create_open_selection(client)
    sid = sel["selection_id"]

    valid_preview_safe = {
        "version": 1,
        "phase": "preview",
        "summary": {
            "files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0,
            "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0
        },
        "files": [],
        "missing_source_entries": [],
    }

    db.run(
        "UPDATE resource_selection SET "
        "    preview_token = 'pt_valid_token_123', "
        "    preview_manifest_digest = 'ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890', "
        "    preview_committable = 1, "
        "    preview_report = ? "
        "WHERE selection_id = ?",
        json.dumps(valid_preview_safe),
        sid,
    )

    resp = client.get(f"/api/resources/import-selections/{sid}")
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert detail["message"] == "Persisted selection state is invalid"
    assert "current" not in detail


@pytest.mark.parametrize(
    ("col", "bad_val"),
    [
        ("preview_token", 12345),
        ("preview_token", ""),
        ("preview_token", "invalid\ntoken"),
        ("preview_manifest_digest", "short_digest"),
        ("preview_manifest_digest", "g" * 64),
        ("preview_manifest_digest", 12345),
        ("preview_committable", 2),
        ("preview_committable", -1),
        ("preview_committable", "1"),
        ("preview_report", "not json"),
        ("preview_report", json.dumps({"version": 2, "phase": "preview"})),
    ],
)
def test_malformed_persisted_preview_evidence_fails_closed(client, fresh_db, col, bad_val):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]

    valid_preview_safe = {
        "version": 1,
        "phase": "preview",
        "summary": {
            "files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0,
            "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0
        },
        "files": [],
        "missing_source_entries": [],
    }

    db.run(
        "UPDATE resource_selection SET "
        "    preview_token = 'pt_valid_token_1234567890', "
        "    preview_manifest_digest = 'a' * 64, "
        "    preview_committable = 1, "
        "    preview_report = ? "
        "WHERE selection_id = ?",
        json.dumps(valid_preview_safe),
        sid,
    )

    db.run(f"UPDATE resource_selection SET {col} = ? WHERE selection_id = ?", bad_val, sid)

    resp = client.get(f"/api/resources/import-selections/{sid}")
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert detail["message"] == "Persisted selection state is invalid"
    assert "current" not in detail


@pytest.mark.parametrize(
    "missing_col",
    ["preview_token", "preview_manifest_digest", "preview_committable", "preview_report"],
)
def test_incomplete_persisted_preview_evidence_fails_closed(client, fresh_db, missing_col):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]

    valid_preview_safe = {
        "version": 1,
        "phase": "preview",
        "summary": {
            "files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0,
            "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0
        },
        "files": [],
        "missing_source_entries": [],
    }

    db.run(
        "UPDATE resource_selection SET "
        "    preview_token = 'pt_valid_token_1234567890', "
        "    preview_manifest_digest = 'a' * 64, "
        "    preview_committable = 1, "
        "    preview_report = ? "
        "WHERE selection_id = ?",
        json.dumps(valid_preview_safe),
        sid,
    )

    db.run(f"UPDATE resource_selection SET {missing_col} = NULL WHERE selection_id = ?", sid)

    resp = client.get(f"/api/resources/import-selections/{sid}")
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert detail["message"] == "Persisted selection state is invalid"
    assert "current" not in detail


@pytest.mark.parametrize(
    ("col", "bad_val"),
    [
        ("byte_count", -1),
        ("byte_count", MAX_SAFE_INTEGER + 100),
        ("file_name", "../../etc/passwd"),
        ("file_name", "C:\\Windows\\system.ini"),
        ("matched_auxiliary_kinds", "invalid json"),
        ("matched_auxiliary_kinds", json.dumps({"not": "list"})),
        ("matched_auxiliary_kinds", json.dumps(["valid", "../invalid/path"])),
        ("matched_auxiliary_kinds", json.dumps([123])),
        ("status", "pending"),
    ],
)
def test_malformed_persisted_file_evidence_fails_closed(client, fresh_db, col, bad_val):
    sel = _create_open_selection(client)
    upload = _upload_file(client, sel["selection_id"], upload_id=_unique("bad-file"), file_name="ok.bin", payload=b"test")
    file_id = upload.json()["files"][0]["file_id"]

    db.conn().execute("PRAGMA ignore_check_constraints = ON")
    try:
        db.run(f"UPDATE resource_selection_file SET {col} = ? WHERE file_id = ?", bad_val, file_id)
        resp = client.get(f"/api/resources/import-selections/{sel['selection_id']}")
        assert resp.status_code == 500
        detail = _stable_detail(resp)
        assert detail["code"] == "selection_state_invalid"
        assert detail["message"] == "Persisted selection state is invalid"
        assert "current" not in detail
    finally:
        db.conn().execute("PRAGMA ignore_check_constraints = OFF")


@pytest.mark.parametrize(
    ("col", "bad_val"),
    [
        ("selection_revision", -1),
        ("selection_revision", MAX_SAFE_INTEGER + 1),
        ("state", "unknown_state"),
        ("expires_at", "not-a-valid-date"),
    ],
)
def test_malformed_persisted_selection_metadata_fails_closed(client, fresh_db, col, bad_val):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]

    db.conn().execute("PRAGMA ignore_check_constraints = ON")
    try:
        db.run(f"UPDATE resource_selection SET {col} = ? WHERE selection_id = ?", bad_val, sid)
        resp = client.get(f"/api/resources/import-selections/{sid}")
        assert resp.status_code == 500
        detail = _stable_detail(resp)
        assert detail["code"] == "selection_state_invalid"
        assert detail["message"] == "Persisted selection state is invalid"
        assert "current" not in detail
    finally:
        db.conn().execute("PRAGMA ignore_check_constraints = OFF")


@pytest.mark.parametrize(
    "bad_revision",
    ["1.0", "1e0", "true", "+1", "-1", " 1", "1 ", "", "abc", str(MAX_SAFE_INTEGER + 1)],
)
def test_delete_query_revision_grammar_and_range_validation(client, fresh_db, bad_revision):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("del-test"), file_name="r.json", payload=b"data")
    file_id = upload.json()["files"][0]["file_id"]
    assert upload.json()["selection_revision"] == 1

    resp = client.delete(
        f"/api/resources/import-selections/{sid}/files/{file_id}?expected_revision={bad_revision}"
    )
    assert resp.status_code == 422
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_revision"
    assert "current" in detail

    # Zero mutation: file must still exist, revision must still be 1
    view = client.get(f"/api/resources/import-selections/{sid}").json()
    assert view["selection_revision"] == 1
    assert len(view["files"]) == 1
    assert view["files"][0]["file_id"] == file_id


def test_delete_query_revision_accepts_valid_decimal_text(client, fresh_db):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("del-ok"), file_name="r.json", payload=b"data")
    file_id = upload.json()["files"][0]["file_id"]
    assert upload.json()["selection_revision"] == 1

    resp = client.delete(
        f"/api/resources/import-selections/{sid}/files/{file_id}?expected_revision=1"
    )
    assert resp.status_code == 200
    view = resp.json()
    assert view["selection_revision"] == 2
    assert view["files"] == []


def test_upload_multipart_casing_and_boundary_parsing(client, fresh_db):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]

    boundary = "----TestBoundary123456789"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="upload_id"\r\n\r\n'
        f"up_{uuid.uuid4().hex}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="test.json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f"sample content\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    # 1. Mixed casing
    resp_mixed = client.post(
        f"/api/resources/import-selections/{sid}/files",
        content=body,
        headers={"Content-Type": f"Multipart/Form-Data; boundary={boundary}"},
    )
    print("RESP_MIXED:", resp_mixed.json())
    assert resp_mixed.status_code == 201

    # 2. Uppercase casing
    body_upper = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="upload_id"\r\n\r\n'
        f"up_{uuid.uuid4().hex}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="test2.json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f"sample content 2\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    resp_upper = client.post(
        f"/api/resources/import-selections/{sid}/files",
        content=body_upper,
        headers={"Content-Type": f"MULTIPART/FORM-DATA; boundary={boundary}"},
    )
    assert resp_upper.status_code == 201

    # 3. Missing boundary parameter
    resp_noboundary = client.post(
        f"/api/resources/import-selections/{sid}/files",
        content=body,
        headers={"Content-Type": "multipart/form-data"},
    )
    assert resp_noboundary.status_code == 422
    assert _stable_detail(resp_noboundary)["code"] == "invalid_multipart"

    # 4. Wrong media type
    resp_json = client.post(
        f"/api/resources/import-selections/{sid}/files",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert resp_json.status_code == 422
    assert _stable_detail(resp_json)["code"] == "invalid_multipart"

    # 5. Malformed multipart body (truncated / invalid syntax)
    resp_corrupt = client.post(
        f"/api/resources/import-selections/{sid}/files",
        content=b"corrupted binary without boundaries",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert resp_corrupt.status_code == 422
    detail_corrupt = _stable_detail(resp_corrupt)
    assert detail_corrupt["code"] == "invalid_multipart"
    assert detail_corrupt["message"] == "Malformed multipart body"


@pytest.mark.parametrize(
    ("client_filename", "expected_display"),
    [
        ("../../secret/passwd.txt", "passwd.txt"),
        ("C:\\Windows\\System32\\calc.exe", "calc.exe"),
        ("\\\\server\\share\\document.pdf", "document.pdf"),
        ("file:///var/data/notes.txt", "notes.txt"),
        ("..", "upload.bin"),
        ("", "upload.bin"),
    ],
)
def test_upload_client_filename_path_sanitization(client, fresh_db, client_filename, expected_display):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload_id = _unique("path-sanitize")

    if client_filename == "":
        boundary = "----TestBoundaryEmptyName"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="upload_id"\r\n\r\n'
            f"{upload_id}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename=""\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
            f"content\r\n"
            f"--{boundary}--\r\n"
        ).encode("utf-8")
        resp = client.post(
            f"/api/resources/import-selections/{sid}/files",
            content=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
    else:
        resp = client.post(
            f"/api/resources/import-selections/{sid}/files",
            files={"file": (client_filename, io.BytesIO(b"content"), "application/octet-stream")},
            data={"upload_id": upload_id},
        )
    assert resp.status_code == 201
    view = resp.json()
    assert view["files"][0]["file_name"] == expected_display

    # Verify DB storage contains only the normalized display name
    row = db.one("SELECT file_name FROM resource_selection_file WHERE selection_id = ?", sid)
    assert row["file_name"] == expected_display

    # Verify raw path never leaks anywhere in the response text
    if "/" in client_filename or "\\" in client_filename:
        assert client_filename not in resp.text


def test_create_selection_forced_authoritative_view_failure_returns_500(client, fresh_db, monkeypatch):
    """When authoritative view cannot be read after insert, return 500, not synthetic 201."""
    request_id = _unique_request_id()

    def _fail_build(*args, **kwargs):
        return None

    monkeypatch.setattr(rs, "_project_selection_view", _fail_build)

    resp = client.post("/api/resources/import-selections", json={"request_id": request_id})
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert detail["message"] == "Persisted selection state is invalid"
    assert "current" not in detail


def test_concurrent_create_converges_to_single_durable_row(client, fresh_db):
    """Concurrent requests with same request_id yield 1 create (201) and replays (200)."""
    import threading

    request_id = _unique_request_id()
    results = [None] * 5
    barrier = threading.Barrier(5)

    def _do_create(idx):
        barrier.wait()
        results[idx] = client.post("/api/resources/import-selections", json={"request_id": request_id})

    threads = [threading.Thread(target=_do_create, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r is not None for r in results)
    status_codes = [r.status_code for r in results]
    assert 500 not in status_codes
    assert 404 not in status_codes
    assert status_codes.count(201) == 1
    assert status_codes.count(200) == 4

    selection_ids = {r.json()["selection_id"] for r in results}
    assert len(selection_ids) == 1
    sid = list(selection_ids)[0]

    # Verify all responses carry the exact same canonical SelectionView shape
    for r in results:
        data = r.json()
        assert data["selection_id"] == sid
        assert data["selection_revision"] == 0
        assert data["state"] == "open"
        assert data["files"] == []
        assert data["preview"] is None
        assert data["commit_result"] is None
        _assert_view_is_public(data)

    rows = db.q("SELECT COUNT(*) AS c FROM resource_selection WHERE request_id = ?", request_id)
    assert rows[0]["c"] == 1

    # Subsequent ordinary replay returns 200 with the exact same identity
    replay = client.post("/api/resources/import-selections", json={"request_id": request_id})
    assert replay.status_code == 200
    assert replay.json()["selection_id"] == sid


def test_concurrent_create_converges_repeatedly(client, fresh_db):
    """Repeat concurrent create across multiple isolated request IDs to guarantee no intermittent 500s."""
    import threading

    for _ in range(10):
        req_id = _unique_request_id()
        results = [None] * 5
        barrier = threading.Barrier(5)

        def _do_create(idx):
            barrier.wait()
            results[idx] = client.post("/api/resources/import-selections", json={"request_id": req_id})

        threads = [threading.Thread(target=_do_create, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        status_codes = [r.status_code for r in results]
        assert 500 not in status_codes, f"Encountered 500 in concurrent batch: {[(r.status_code, r.text) for r in results]}"
        assert 404 not in status_codes
        assert status_codes.count(201) == 1
        assert status_codes.count(200) == 4
        sids = {r.json()["selection_id"] for r in results}
        assert len(sids) == 1


def test_create_endpoint_does_not_call_post_decision_builder(client, fresh_db, monkeypatch):
    """Verify create_import_selection returns the domain view directly without post-decision builder calls."""
    req_id = _unique_request_id()

    builder_calls = []
    orig_build = rs.build_selection_view
    orig_recover = rs.recover_selection

    def traced_build(*args, **kwargs):
        builder_calls.append("build_selection_view")
        return orig_build(*args, **kwargs)

    def traced_recover(*args, **kwargs):
        builder_calls.append("recover_selection")
        return orig_recover(*args, **kwargs)

    monkeypatch.setattr(rs, "build_selection_view", traced_build)
    monkeypatch.setattr(rs, "recover_selection", traced_recover)

    resp = client.post("/api/resources/import-selections", json={"request_id": req_id})
    assert resp.status_code == 201
    assert builder_calls == []

    resp_replay = client.post("/api/resources/import-selections", json={"request_id": req_id})
    assert resp_replay.status_code == 200
    assert builder_calls == []


def test_create_replay_performs_canonical_recovery_for_expired_selection(client, fresh_db):
    """Replay of an expired selection performs record-local lazy recovery and returns state='expired'."""
    req_id = _unique_request_id()
    created = client.post("/api/resources/import-selections", json={"request_id": req_id})
    assert created.status_code == 201
    sid = created.json()["selection_id"]

    # Age the row past its 24h deadline
    db.run(
        "UPDATE resource_selection SET expires_at = ? WHERE selection_id = ?",
        "2000-01-01T00:00:00+00:00",
        sid,
    )

    replay = client.post("/api/resources/import-selections", json={"request_id": req_id})
    assert replay.status_code == 200
    view = replay.json()
    assert view["selection_id"] == sid
    assert view["state"] == "expired"
    _assert_view_is_public(view)


def test_selection_state_invalid_error_maps_to_stable_500(client, fresh_db, monkeypatch):
    """SelectionStateInvalidError maps to HTTP 500 with code 'selection_state_invalid' and no internal details."""
    req_id = _unique_request_id()

    def mock_create(*args, **kwargs):
        raise rs.SelectionStateInvalidError("Secret internal database state corruption details")

    monkeypatch.setattr(rs, "create_or_replay_selection", mock_create)

    resp = client.post("/api/resources/import-selections", json={"request_id": req_id})
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert detail["message"] == "Persisted selection state is invalid"
    assert "Secret internal database state corruption details" not in resp.text
    assert "current" not in detail


# ---------------------------------------------------------------------------
# Repair 4A: Pre-mutation target validation and presence/null tests
# ---------------------------------------------------------------------------

def _selection_db_snapshot(selection_id: str) -> dict:
    """Capture full selection and file state, excluding machine paths."""
    sel_row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", selection_id)
    file_rows = db.q("SELECT * FROM resource_selection_file WHERE selection_id = ? ORDER BY order_index, id", selection_id)
    safe_sel = dict(sel_row) if sel_row else None
    safe_files = []
    file_bytes = {}
    for f in file_rows:
        fd = dict(f)
        path_str = fd.get("staged_path") or ""
        p = Path(path_str) if path_str else None
        if p and p.exists():
            file_bytes[fd["file_id"]] = p.read_bytes()
        else:
            file_bytes[fd["file_id"]] = None
        fd["staged_path"] = "<staged_path>" if path_str else ""
        safe_files.append(fd)
    return {
        "selection": safe_sel,
        "files": safe_files,
        "file_bytes": file_bytes,
    }


@pytest.mark.parametrize(
    ("lib_key", "is_valid"),
    [
        ("a", True),
        ("k" * 128, True),
        ("k" * 129, False),
        ("", False),
        ("   ", False),
        ("  leading", False),
        ("trailing  ", False),
        (".", False),
        ("..", False),
        ("path/key", False),
        ("path\\key", False),
        ("\x00key", False),
        ("key\nwith_newline", False),
        ("key\twith_tab", False),
        ("key\x7fdel", False),
        ("MixedCase_Library-1.0", True),
    ],
)
def test_patch_target_library_key_boundaries(client, fresh_db, lib_key, is_valid):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("lib-bnd"), file_name="r.json", payload=b"payload")
    fid = upload.json()["files"][0]["file_id"]
    rev = upload.json()["selection_revision"]

    snap_before = _selection_db_snapshot(sid)

    resp = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": rev, "effective_library_key": lib_key},
    )

    if is_valid:
        assert resp.status_code == 200, resp.text
        view = resp.json()
        assert view["selection_revision"] == rev + 1
        target = next(f for f in view["files"] if f["file_id"] == fid)
        assert target["effective_library_key"] == lib_key
    else:
        assert resp.status_code == 422, resp.text
        detail = _stable_detail(resp)
        assert detail["code"] == "invalid_request"
        assert "current" in detail
        snap_after = _selection_db_snapshot(sid)
        assert snap_after == snap_before


@pytest.mark.parametrize("target_field", ["effective_library_key", "effective_auxiliary_kind"])
@pytest.mark.parametrize("bad_val", [True, False, 1, 1.0, [], {}])
def test_patch_target_strict_types_rejected(client, fresh_db, target_field, bad_val):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("strict-type"), file_name="r.json", payload=b"data")
    fid = upload.json()["files"][0]["file_id"]
    rev = upload.json()["selection_revision"]

    snap_before = _selection_db_snapshot(sid)

    resp = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": rev, target_field: bad_val},
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_request"
    assert "current" in detail
    snap_after = _selection_db_snapshot(sid)
    assert snap_after == snap_before


@pytest.mark.parametrize("kind", list(resource_parser.ALL_AUXILIARY_KINDS))
def test_patch_target_auxiliary_kind_valid_members(client, fresh_db, kind):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("aux-ok"), file_name="r.json", payload=b"data")
    fid = upload.json()["files"][0]["file_id"]
    rev = upload.json()["selection_revision"]

    resp = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": rev, "effective_auxiliary_kind": kind},
    )
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert view["selection_revision"] == rev + 1
    target = next(f for f in view["files"] if f["file_id"] == fid)
    assert target["effective_auxiliary_kind"] == kind


@pytest.mark.parametrize(
    "bad_kind",
    [
        "invented_kind",
        "CUT_MAP",
        "Cut_Map",
        "TRANSLATION_MAP",
        "Mined_Families",
        "",
        "   ",
        "cut/map",
        "cut\nmap",
        "cut\\map",
    ],
)
def test_patch_target_auxiliary_kind_invalid_rejected(client, fresh_db, bad_kind):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("aux-bad"), file_name="r.json", payload=b"data")
    fid = upload.json()["files"][0]["file_id"]
    rev = upload.json()["selection_revision"]

    snap_before = _selection_db_snapshot(sid)

    resp = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": rev, "effective_auxiliary_kind": bad_kind},
    )
    assert resp.status_code == 422, resp.text
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_request"
    assert "current" in detail
    snap_after = _selection_db_snapshot(sid)
    assert snap_after == snap_before


def test_patch_target_presence_and_explicit_null(client, fresh_db):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("pres-null"), file_name="r.json", payload=b"data")
    fid = upload.json()["files"][0]["file_id"]

    # Set initial target values
    resp_init = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": 1, "effective_library_key": "initial_lib", "effective_auxiliary_kind": "cut_map"},
    )
    assert resp_init.status_code == 200
    assert resp_init.json()["selection_revision"] == 2
    f_init = next(f for f in resp_init.json()["files"] if f["file_id"] == fid)
    assert f_init["effective_library_key"] == "initial_lib"
    assert f_init["effective_auxiliary_kind"] == "cut_map"

    # 1. Clear library key with explicit null, omit auxiliary kind
    resp_clear_lib = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": 2, "effective_library_key": None},
    )
    assert resp_clear_lib.status_code == 200
    assert resp_clear_lib.json()["selection_revision"] == 3
    f_c1 = next(f for f in resp_clear_lib.json()["files"] if f["file_id"] == fid)
    assert f_c1["effective_library_key"] is None
    assert f_c1["effective_auxiliary_kind"] == "cut_map"

    # 2. Clear auxiliary kind with explicit null, assign new library key
    resp_mixed = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": 3, "effective_library_key": "second_lib", "effective_auxiliary_kind": None},
    )
    assert resp_mixed.status_code == 200
    assert resp_mixed.json()["selection_revision"] == 4
    f_c2 = next(f for f in resp_mixed.json()["files"] if f["file_id"] == fid)
    assert f_c2["effective_library_key"] == "second_lib"
    assert f_c2["effective_auxiliary_kind"] is None

    # 3. Clear both with explicit null
    resp_clear_both = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": 4, "effective_library_key": None, "effective_auxiliary_kind": None},
    )
    assert resp_clear_both.status_code == 200
    assert resp_clear_both.json()["selection_revision"] == 5
    f_c3 = next(f for f in resp_clear_both.json()["files"] if f["file_id"] == fid)
    assert f_c3["effective_library_key"] is None
    assert f_c3["effective_auxiliary_kind"] is None

    # 4. Both target keys omitted -> 422, zero mutation
    snap_before_omitted = _selection_db_snapshot(sid)
    resp_omitted = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": 5},
    )
    assert resp_omitted.status_code == 422
    assert resp_omitted.json()["detail"]["code"] == "invalid_request"
    assert _selection_db_snapshot(sid) == snap_before_omitted

    # 5. One valid, one invalid -> atomic rejection, neither written
    snap_before_atomic = _selection_db_snapshot(sid)
    resp_atomic = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": 5, "effective_library_key": "good_lib", "effective_auxiliary_kind": "bad_kind"},
    )
    assert resp_atomic.status_code == 422
    assert _selection_db_snapshot(sid) == snap_before_atomic


def test_patch_target_invalidates_preview_and_increments_revision_once(client, fresh_db):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("prev-inval"), file_name="r.json", payload=b"data")
    fid = upload.json()["files"][0]["file_id"]
    rev_before = upload.json()["selection_revision"]

    # Bind active preview
    preview_saved = rs.save_preview(
        sid,
        expected_revision=rev_before,
        preview_token="prev_tok_12345",
        manifest_digest="a" * 64,
        committable=True,
        report={"version": 1, "phase": "preview", "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0}, "files": [], "missing_source_entries": []},
    )
    assert preview_saved is True

    # Check preview is active
    view_with_preview = client.get(f"/api/resources/import-selections/{sid}").json()
    assert view_with_preview["preview"] is not None
    assert view_with_preview["preview"]["preview_token"] == "prev_tok_12345"
    assert view_with_preview["preview"]["report"] == resource_service.validate_safe_report(
        view_with_preview["preview"]["report"], expected_phase="preview"
    )

    resp = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": rev_before, "effective_library_key": "new_target_lib"},
    )
    assert resp.status_code == 200
    view_after = resp.json()

    # Revision incremented exactly once
    assert view_after["selection_revision"] == rev_before + 1

    # Preview invalidated
    assert view_after["preview"] is None

    # In DB, all preview fields are NULL
    sel_row = db.one("SELECT preview_token, preview_manifest_digest, preview_committable, preview_report, preview_created_at FROM resource_selection WHERE selection_id = ?", sid)
    assert sel_row["preview_token"] is None
    assert sel_row["preview_manifest_digest"] is None
    assert sel_row["preview_committable"] is None
    assert sel_row["preview_report"] is None
    assert sel_row["preview_created_at"] is None


# ---------------------------------------------------------------------------
# Repair 4C: Strict Persisted State Integrity Probes
# ---------------------------------------------------------------------------

def _assert_selection_state_invalid_500(resp):
    assert resp.status_code == 500, f"Expected 500, got {resp.status_code}: {resp.text}"
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert detail["message"] == "Persisted selection state is invalid"
    assert "current" not in detail
    rs.assert_selection_view_is_public(detail)


def test_matched_auxiliary_kinds_sql_null_fails_closed(client, fresh_db, monkeypatch):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("null-aux"), file_name="f.json", payload=b"test")
    assert upload.status_code == 201

    orig_q = db.q
    def mock_q(sql, *args):
        rows = orig_q(sql, *args)
        if "FROM resource_selection_file" in sql:
            for r in rows:
                r["matched_auxiliary_kinds"] = None
        return rows
    monkeypatch.setattr(db, "q", mock_q)

    resp = client.get(f"/api/resources/import-selections/{sid}")
    _assert_selection_state_invalid_500(resp)


@pytest.mark.parametrize(
    "corrupt_kinds",
    [
        "",    # Empty text
        "not json",  # Malformed JSON
        "null",  # JSON null
        "123",  # JSON number
        '"cut_map"',  # JSON string
        "true",  # JSON bool
        '{"kind": "cut_map"}',  # JSON object
        json.dumps(["cut_map", "cut_map"]),  # Duplicate canonical member
        json.dumps(["translation_map", "mined_labels", "translation_map"]),  # Duplicate
        json.dumps(["invented_aux_kind"]),  # Invented kind
        json.dumps(["cut_map", "custom_kind"]),  # Unknown kind in list
        json.dumps(["Cut_Map"]),  # Wrong casing
        json.dumps(["TRANSLATION_MAP"]),  # Wrong casing
        json.dumps(["mined_Labels"]),  # Wrong casing
        json.dumps([123]),  # Integer member
        json.dumps([True]),  # Boolean member
        json.dumps([None]),  # Null member
        json.dumps([["cut_map"]]),  # Nested list
        json.dumps([{"kind": "cut_map"}]),  # Nested object
    ],
)
def test_matched_auxiliary_kinds_persisted_corruption_probes(client, fresh_db, corrupt_kinds):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("corrupt-aux"), file_name="f.json", payload=b"test")
    fid = upload.json()["files"][0]["file_id"]

    db.conn().execute("PRAGMA ignore_check_constraints = ON")
    try:
        db.run("UPDATE resource_selection_file SET matched_auxiliary_kinds = ? WHERE file_id = ?", corrupt_kinds, fid)
        resp = client.get(f"/api/resources/import-selections/{sid}")
        _assert_selection_state_invalid_500(resp)
    finally:
        db.conn().execute("PRAGMA ignore_check_constraints = OFF")


@pytest.mark.parametrize(
    ("valid_kinds_json", "expected_list"),
    [
        ("[]", []),
        (json.dumps(["translation_map"]), ["translation_map"]),
        (json.dumps(["cut_map"]), ["cut_map"]),
        (json.dumps(["mined_families"]), ["mined_families"]),
        (json.dumps(["mined_labels"]), ["mined_labels"]),
        (
            json.dumps(["mined_labels", "cut_map", "translation_map"]),
            ["mined_labels", "cut_map", "translation_map"],
        ),
    ],
)
def test_matched_auxiliary_kinds_valid_persisted_control(client, fresh_db, valid_kinds_json, expected_list):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    upload = _upload_file(client, sid, upload_id=_unique("valid-aux"), file_name="f.json", payload=b"test")
    fid = upload.json()["files"][0]["file_id"]

    db.run("UPDATE resource_selection_file SET matched_auxiliary_kinds = ? WHERE file_id = ?", valid_kinds_json, fid)
    resp = client.get(f"/api/resources/import-selections/{sid}")
    assert resp.status_code == 200
    view = resp.json()
    assert view["files"][0]["matched_auxiliary_kinds"] == expected_list


@pytest.mark.parametrize(
    "corrupt_committable",
    [
        1.5,
        2,
        -1,
        100,
        "true",
    ],
)
def test_preview_committable_persisted_corruption_probes(client, fresh_db, corrupt_committable):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_iso = rs._now_iso()
    rep = json.dumps({
        "version": 1, "phase": "preview",
        "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0},
        "files": [], "missing_source_entries": [],
    })

    db.conn().execute("PRAGMA ignore_check_constraints = ON")
    try:
        db.run(
            "UPDATE resource_selection SET "
            "    preview_token = 'p_tok_valid', "
            "    preview_manifest_digest = ?, "
            "    preview_committable = ?, "
            "    preview_report = ?, "
            "    preview_created_at = ? "
            "WHERE selection_id = ?",
            "a" * 64, corrupt_committable, rep, now_iso, sid,
        )
        resp = client.get(f"/api/resources/import-selections/{sid}")
        _assert_selection_state_invalid_500(resp)
    finally:
        db.conn().execute("PRAGMA ignore_check_constraints = OFF")


@pytest.mark.parametrize("corrupt_val", [True, False, 1.0, "1", "0"])
def test_preview_committable_normalized_storage_probes(client, fresh_db, monkeypatch, corrupt_val):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_iso = rs._now_iso()
    rep = json.dumps({
        "version": 1, "phase": "preview",
        "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0},
        "files": [], "missing_source_entries": [],
    })
    db.run(
        "UPDATE resource_selection SET "
        "    preview_token = 'p_tok_valid', "
        "    preview_manifest_digest = ?, "
        "    preview_committable = 1, "
        "    preview_report = ?, "
        "    preview_created_at = ? "
        "WHERE selection_id = ?",
        "a" * 64, rep, now_iso, sid,
    )

    orig_recover = rs.recover_selection
    def mock_recover(*args, **kwargs):
        res = orig_recover(*args, **kwargs)
        if res:
            res = dict(res)
            res["preview_committable"] = corrupt_val
        return res
    monkeypatch.setattr(rs, "recover_selection", mock_recover)

    resp = client.get(f"/api/resources/import-selections/{sid}")
    _assert_selection_state_invalid_500(resp)


@pytest.mark.parametrize("committable_int,expected_bool", [(0, False), (1, True)])
def test_preview_committable_valid_control(client, fresh_db, committable_int, expected_bool):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_iso = rs._now_iso()
    rep = json.dumps({
        "version": 1, "phase": "preview",
        "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0},
        "files": [], "missing_source_entries": [],
    })
    db.run(
        "UPDATE resource_selection SET "
        "    preview_token = 'p_tok_valid', "
        "    preview_manifest_digest = ?, "
        "    preview_committable = ?, "
        "    preview_report = ?, "
        "    preview_created_at = ? "
        "WHERE selection_id = ?",
        "a" * 64, committable_int, rep, now_iso, sid,
    )
    resp = client.get(f"/api/resources/import-selections/{sid}")
    assert resp.status_code == 200
    view = resp.json()
    assert view["preview"]["committable"] is expected_bool
    assert type(view["preview"]["committable"]) is bool


def test_preview_tuple_absence_control(client, fresh_db):
    """When all 5 preview fields are NULL, preview is legitimately null."""
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    resp = client.get(f"/api/resources/import-selections/{sid}")
    assert resp.status_code == 200
    assert resp.json()["preview"] is None


@pytest.mark.parametrize(
    "single_field",
    [
        "preview_token",
        "preview_manifest_digest",
        "preview_committable",
        "preview_report",
        "preview_created_at",
    ],
)
def test_preview_tuple_single_field_present_fails_closed(client, fresh_db, single_field):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    val_map = {
        "preview_token": "p_tok_single",
        "preview_manifest_digest": "b" * 64,
        "preview_committable": 1,
        "preview_report": json.dumps({"version": 1, "phase": "preview", "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0}, "files": [], "missing_source_entries": []}),
        "preview_created_at": rs._now_iso(),
    }
    db.run(f"UPDATE resource_selection SET {single_field} = ? WHERE selection_id = ?", val_map[single_field], sid)
    resp = client.get(f"/api/resources/import-selections/{sid}")
    _assert_selection_state_invalid_500(resp)


@pytest.mark.parametrize(
    "omitted_field",
    [
        "preview_token",
        "preview_manifest_digest",
        "preview_committable",
        "preview_report",
        "preview_created_at",
    ],
)
def test_preview_tuple_omitted_field_fails_closed(client, fresh_db, omitted_field):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_iso = rs._now_iso()
    rep = json.dumps({
        "version": 1, "phase": "preview",
        "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0},
        "files": [], "missing_source_entries": [],
    })
    db.run(
        "UPDATE resource_selection SET "
        "    preview_token = 'p_tok_valid', "
        "    preview_manifest_digest = ?, "
        "    preview_committable = 1, "
        "    preview_report = ?, "
        "    preview_created_at = ? "
        "WHERE selection_id = ?",
        "c" * 64, rep, now_iso, sid,
    )
    # Set one field to NULL
    db.run(f"UPDATE resource_selection SET {omitted_field} = NULL WHERE selection_id = ?", sid)
    resp = client.get(f"/api/resources/import-selections/{sid}")
    _assert_selection_state_invalid_500(resp)


@pytest.mark.parametrize(
    "empty_col",
    [
        "preview_token",
        "preview_manifest_digest",
        "preview_report",
        "preview_created_at",
    ],
)
def test_preview_tuple_empty_text_fails_closed(client, fresh_db, empty_col):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_iso = rs._now_iso()
    rep = json.dumps({
        "version": 1, "phase": "preview",
        "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0},
        "files": [], "missing_source_entries": [],
    })
    db.run(
        "UPDATE resource_selection SET "
        "    preview_token = 'p_tok_valid', "
        "    preview_manifest_digest = ?, "
        "    preview_committable = 1, "
        "    preview_report = ?, "
        "    preview_created_at = ? "
        "WHERE selection_id = ?",
        "d" * 64, rep, now_iso, sid,
    )
    db.run(f"UPDATE resource_selection SET {empty_col} = '' WHERE selection_id = ?", sid)
    resp = client.get(f"/api/resources/import-selections/{sid}")
    _assert_selection_state_invalid_500(resp)


@pytest.mark.parametrize(
    "bad_digest",
    [
        "a" * 63,  # Short
        "a" * 65,  # Long
        "A" * 64,  # Uppercase
        "g" * 64,  # Non-hex
        "invalid_hex_digest",
    ],
)
def test_preview_manifest_digest_corruption_probes(client, fresh_db, bad_digest):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_iso = rs._now_iso()
    rep = json.dumps({
        "version": 1, "phase": "preview",
        "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0},
        "files": [], "missing_source_entries": [],
    })
    db.conn().execute("PRAGMA ignore_check_constraints = ON")
    try:
        db.run(
            "UPDATE resource_selection SET "
            "    preview_token = 'p_tok_valid', "
            "    preview_manifest_digest = ?, "
            "    preview_committable = 1, "
            "    preview_report = ?, "
            "    preview_created_at = ? "
            "WHERE selection_id = ?",
            bad_digest, rep, now_iso, sid,
        )
        resp = client.get(f"/api/resources/import-selections/{sid}")
        _assert_selection_state_invalid_500(resp)
    finally:
        db.conn().execute("PRAGMA ignore_check_constraints = OFF")


@pytest.mark.parametrize(
    "bad_report",
    [
        "not json",
        json.dumps({"version": 1, "phase": "commit", "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0, "recorded": 0, "new_scene_revisions": 0, "unchanged_scene_revisions": 0, "updated_scene_revisions": 0, "new_auxiliary_revisions": 0, "unchanged_auxiliary_revisions": 0}, "files": [], "missing_source_entries": []}),  # wrong phase
        json.dumps({"version": 1, "phase": "preview", "summary": {"files": 10}, "files": []}),  # unreconciled
        json.dumps({"unstructured": "report"}),
    ],
)
def test_preview_report_corruption_probes(client, fresh_db, bad_report):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_iso = rs._now_iso()
    db.run(
        "UPDATE resource_selection SET "
        "    preview_token = 'p_tok_valid', "
        "    preview_manifest_digest = ?, "
        "    preview_committable = 1, "
        "    preview_report = ?, "
        "    preview_created_at = ? "
        "WHERE selection_id = ?",
        "e" * 64, bad_report, now_iso, sid,
    )
    resp = client.get(f"/api/resources/import-selections/{sid}")
    _assert_selection_state_invalid_500(resp)


@pytest.mark.parametrize(
    "incompatible_state",
    ["open", "committing", "cancelled", "expired"],
)
def test_commit_result_on_incompatible_state_fails_closed(client, fresh_db, incompatible_state):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    rep = json.dumps({
        "version": 1, "phase": "commit",
        "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0, "recorded": 0, "new_scene_revisions": 0, "unchanged_scene_revisions": 0, "updated_scene_revisions": 0, "new_auxiliary_revisions": 0, "unchanged_auxiliary_revisions": 0},
        "files": [], "missing_source_entries": [],
    })
    db.conn().execute("PRAGMA ignore_check_constraints = ON")
    try:
        db.run("UPDATE resource_selection SET state = ?, commit_result = ? WHERE selection_id = ?", incompatible_state, rep, sid)
        resp = client.get(f"/api/resources/import-selections/{sid}")
        _assert_selection_state_invalid_500(resp)
    finally:
        db.conn().execute("PRAGMA ignore_check_constraints = OFF")


@pytest.mark.parametrize(
    "bad_commit_result",
    [
        None,  # NULL commit_result on committed
        "",    # Empty string on committed
        "not json",
        json.dumps({"version": 1, "phase": "preview", "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0}, "files": [], "missing_source_entries": []}),  # wrong phase
        json.dumps({"unstructured": "ok"}),
    ],
)
def test_committed_state_corrupt_commit_result_fails_closed(client, fresh_db, bad_commit_result):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_iso = rs._now_iso()
    db.conn().execute("PRAGMA ignore_check_constraints = ON")
    try:
        db.run(
            "UPDATE resource_selection SET "
            "    state = 'committed', "
            "    committed_revision = 0, "
            "    committed_preview_token = 'p_tok', "
            "    committed_manifest_digest = ?, "
            "    commit_result = ?, "
            "    committed_at = ? "
            "WHERE selection_id = ?",
            "f" * 64, bad_commit_result, now_iso, sid,
        )
        resp = client.get(f"/api/resources/import-selections/{sid}")
        _assert_selection_state_invalid_500(resp)
    finally:
        db.conn().execute("PRAGMA ignore_check_constraints = OFF")


@pytest.mark.parametrize(
    "omitted_commit_field",
    [
        "committed_revision",
        "committed_preview_token",
        "committed_manifest_digest",
        "committed_at",
    ],
)
def test_committed_state_partial_evidence_fails_closed(client, fresh_db, omitted_commit_field):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_iso = rs._now_iso()
    rep = json.dumps({
        "version": 1, "phase": "commit",
        "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0, "recorded": 0, "new_scene_revisions": 0, "unchanged_scene_revisions": 0, "updated_scene_revisions": 0, "new_auxiliary_revisions": 0, "unchanged_auxiliary_revisions": 0},
        "files": [], "missing_source_entries": [],
    })
    db.run(
        "UPDATE resource_selection SET "
        "    state = 'committed', "
        "    committed_revision = 0, "
        "    committed_preview_token = 'p_tok', "
        "    committed_manifest_digest = ?, "
        "    commit_result = ?, "
        "    committed_at = ? "
        "WHERE selection_id = ?",
        "a" * 64, rep, now_iso, sid,
    )
    db.run(f"UPDATE resource_selection SET {omitted_commit_field} = NULL WHERE selection_id = ?", sid)
    resp = client.get(f"/api/resources/import-selections/{sid}")
    _assert_selection_state_invalid_500(resp)


@pytest.mark.parametrize(
    "incompatible_claim_state",
    ["open", "committed", "cancelled", "expired"],
)
def test_active_claim_on_incompatible_state_fails_closed(client, fresh_db, incompatible_claim_state):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    future_deadline = "2099-01-01T00:00:00+00:00"
    db.conn().execute("PRAGMA ignore_check_constraints = ON")
    try:
        db.run(
            "UPDATE resource_selection SET "
            "    state = ?, "
            "    claim_commit_token = 'c_tok', "
            "    claim_revision = 0, "
            "    claim_preview_token = 'p_tok', "
            "    claim_manifest_digest = ?, "
            "    claim_lease_deadline = ? "
            "WHERE selection_id = ?",
            incompatible_claim_state, "a" * 64, future_deadline, sid,
        )
        resp = client.get(f"/api/resources/import-selections/{sid}")
        _assert_selection_state_invalid_500(resp)
    finally:
        db.conn().execute("PRAGMA ignore_check_constraints = OFF")


@pytest.mark.parametrize(
    "omitted_claim_field",
    [
        "claim_commit_token",
        "claim_revision",
        "claim_preview_token",
        "claim_manifest_digest",
        "claim_lease_deadline",
    ],
)
def test_committing_state_partial_claim_fails_closed(client, fresh_db, omitted_claim_field):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    future_deadline = "2099-01-01T00:00:00+00:00"
    now_iso = rs._now_iso()
    rep = json.dumps({
        "version": 1, "phase": "preview",
        "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0},
        "files": [], "missing_source_entries": [],
    })
    db.run(
        "UPDATE resource_selection SET "
        "    state = 'committing', "
        "    preview_token = 'p_tok', "
        "    preview_manifest_digest = ?, "
        "    preview_committable = 1, "
        "    preview_report = ?, "
        "    preview_created_at = ?, "
        "    claim_commit_token = 'c_tok', "
        "    claim_revision = 0, "
        "    claim_preview_token = 'p_tok', "
        "    claim_manifest_digest = ?, "
        "    claim_lease_deadline = ? "
        "WHERE selection_id = ?",
        "b" * 64, rep, now_iso, "b" * 64, future_deadline, sid,
    )
    db.run(f"UPDATE resource_selection SET {omitted_claim_field} = NULL WHERE selection_id = ?", sid)
    resp = client.get(f"/api/resources/import-selections/{sid}")
    _assert_selection_state_invalid_500(resp)


def test_committing_state_claim_mismatch_with_preview_fails_closed(client, fresh_db):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    future_deadline = "2099-01-01T00:00:00+00:00"
    now_iso = rs._now_iso()
    rep = json.dumps({
        "version": 1, "phase": "preview",
        "summary": {"files": 0, "inputs": 0, "accepted": 0, "auxiliary": 0, "duplicates": 0, "unresolved": 0, "new": 0, "unchanged": 0, "updated": 0, "missing": 0},
        "files": [], "missing_source_entries": [],
    })
    db.run(
        "UPDATE resource_selection SET "
        "    state = 'committing', "
        "    preview_token = 'p_tok', "
        "    preview_manifest_digest = ?, "
        "    preview_committable = 1, "
        "    preview_report = ?, "
        "    preview_created_at = ?, "
        "    claim_commit_token = 'c_tok', "
        "    claim_revision = 0, "
        "    claim_preview_token = 'mismatched_tok', "
        "    claim_manifest_digest = ?, "
        "    claim_lease_deadline = ? "
        "WHERE selection_id = ?",
        "c" * 64, rep, now_iso, "c" * 64, future_deadline, sid,
    )
    resp = client.get(f"/api/resources/import-selections/{sid}")
    _assert_selection_state_invalid_500(resp)


def test_staged_file_order_and_non_staged_omission(client, fresh_db):
    """build_selection_view returns only staged files in order_index ASC order."""
    sel = _create_open_selection(client)
    sid = sel["selection_id"]

    # Upload two files
    up1 = _upload_file(client, sid, upload_id=_unique("file-1"), file_name="first.json", payload=b"a")
    fid1 = up1.json()["files"][0]["file_id"]

    up2 = _upload_file(client, sid, upload_id=_unique("file-2"), file_name="second.json", payload=b"b")
    fid2 = up2.json()["files"][1]["file_id"]

    # Add extra rows with non-staged statuses: reserved, removed, finalized, failed
    db.conn().execute("PRAGMA ignore_check_constraints = ON")
    try:
        now = rs._now_iso()
        for idx, (bad_fid, bad_status) in enumerate([
            ("f_reserved", "reserved"),
            ("f_removed", "removed"),
            ("f_finalized", "finalized"),
            ("f_failed", "failed"),
        ], start=10):
            db.run(
                "INSERT INTO resource_selection_file ("
                "    selection_id, file_id, upload_id, file_name, status, "
                "    byte_count, reserved_bytes, staged_path, order_index, "
                "    matched_auxiliary_kinds, created_at, updated_at"
                ") VALUES (?, ?, ?, 'hidden.json', ?, 1, 1, '/dummy/path', ?, '[]', ?, ?)",
                sid, bad_fid, f"up_{bad_fid}", bad_status, idx, now, now,
            )

        resp = client.get(f"/api/resources/import-selections/{sid}")
        assert resp.status_code == 200
        view = resp.json()
        assert len(view["files"]) == 2
        assert [f["file_id"] for f in view["files"]] == [fid1, fid2]
        for f in view["files"]:
            assert f["status"] == "staged"
    finally:
        db.conn().execute("PRAGMA ignore_check_constraints = OFF")


# ===========================================================================
# Repair 4D: Strict Multipart Contract and Authoritative Visibility Tests
# ===========================================================================

# ---------------------------------------------------------------------------
# 1. Strict Multipart / Content-Type Header Unit Probes
# ---------------------------------------------------------------------------

def test_parse_and_validate_multipart_content_type_valid_casings_and_aux_params():
    # Valid casings
    norm1, b1 = parse_and_validate_multipart_content_type(b"multipart/form-data; boundary=simple")
    assert b1 == "simple"
    assert norm1.startswith("multipart/form-data;")

    norm2, b2 = parse_and_validate_multipart_content_type(b'Multipart/Form-Data; boundary="quoted-bound"')
    assert b2 == "quoted-bound"

    norm3, b3 = parse_and_validate_multipart_content_type(b"MULTIPART/FORM-DATA; boundary=UPPER")
    assert b3 == "UPPER"

    # Auxiliary params before and after
    norm4, b4 = parse_and_validate_multipart_content_type(b"multipart/form-data; charset=utf-8; boundary=aux1")
    assert b4 == "aux1"
    assert "charset=utf-8" in norm4

    norm5, b5 = parse_and_validate_multipart_content_type(b"multipart/form-data; boundary=aux2; charset=utf-8")
    assert b5 == "aux2"
    assert "charset=utf-8" in norm5

    # Boundary with valid space in the middle
    norm6, b6 = parse_and_validate_multipart_content_type(b'multipart/form-data; boundary="boundary with spaces"')
    assert b6 == "boundary with spaces"

    # Boundary with length 70
    b70 = "a" * 70
    norm7, b7 = parse_and_validate_multipart_content_type(f'multipart/form-data; boundary="{b70}"'.encode("ascii"))
    assert b7 == b70

    # Boundary with punctuation characters from RFC bcharsnospace
    spec_b = "a'()+_,-./:=?"
    norm8, b8 = parse_and_validate_multipart_content_type(f'multipart/form-data; boundary="{spec_b}"'.encode("ascii"))
    assert b8 == spec_b


@pytest.mark.parametrize(
    "invalid_header",
    [
        b"application/json",
        b"multipart/mixed; boundary=abc",
        b"multipart/form-data",  # no boundary
        b"multipart/form-data; boundary=",  # empty unquoted
        b'multipart/form-data; boundary=""',  # empty quoted
        b'multipart/form-data; boundary="unterminated',
        b'multipart/form-data; boundary="dangling\\"',
        b'multipart/form-data; boundary="illegal\\b"',
        b"multipart/form-data; boundary=one; boundary=one",  # duplicate identical
        b"multipart/form-data; boundary=one; boundary=two",  # duplicate different
        b"multipart/form-data; boundary=one; BOUNDARY=one",  # duplicate mixed-case
        b"multipart/form-data; charset=utf-8; CHARSET=ascii; boundary=one",  # duplicate aux
        (b'multipart/form-data; boundary="' + b"a" * 71 + b'"'),  # length 71
        b'multipart/form-data; boundary="ends with space "',  # ends in space
        b'multipart/form-data; boundary="has\r\ncr"',  # CR/LF
        b'multipart/form-data; boundary="has\x00null"',  # control char
        b'multipart/form-data; boundary="has;semi"',  # semicolon in boundary
        b'multipart/form-data; boundary="has@at"',  # @ in boundary
        b'multipart/form-data; boundary="has<less"',  # < in boundary
        b'multipart/form-data; boundary="has[bracket"',  # [ in boundary
        b'multipart/form-data; boundary="has{brace"',  # { in boundary
        b'multipart/form-data; boundary="has\\\\backslash"',  # backslash in boundary
        b"multipart/form-data; boundary=one;",  # trailing semicolon
        b"multipart/form-data; =val",  # missing parameter name
        b'multipart/form-data; boundary="a"trailing',  # trailing chars after quote
    ],
)
def test_parse_and_validate_multipart_content_type_rejected_probes(invalid_header):
    with pytest.raises(ValueError):
        parse_and_validate_multipart_content_type(invalid_header)


# ---------------------------------------------------------------------------
# 2. Route-Level Multipart Invariant Probes (Timing, Non-consumption, Zero-mutation)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "headers",
    [
        # Zero Content-Type headers
        {"accept": "*/*"},
        # Two identical Content-Type headers
        [("content-type", "multipart/form-data; boundary=bound1"), ("content-type", "multipart/form-data; boundary=bound1")],
        # Two different Content-Type headers
        [("content-type", "multipart/form-data; boundary=bound1"), ("content-type", "multipart/form-data; boundary=bound2")],
        # Duplicate boundary parameter
        {"content-type": "multipart/form-data; boundary=b1; boundary=b2"},
        # Duplicate equal boundary parameter
        {"content-type": "multipart/form-data; boundary=b1; boundary=b1"},
        # Duplicate mixed-case boundary parameter
        {"content-type": "multipart/form-data; boundary=b1; BOUNDARY=b1"},
        # Duplicate auxiliary parameter
        {"content-type": "multipart/form-data; charset=utf-8; CHARSET=ascii; boundary=b1"},
        # Missing boundary parameter
        {"content-type": "multipart/form-data; charset=utf-8"},
        # Bare empty boundary
        {"content-type": "multipart/form-data; boundary="},
        # Quoted empty boundary
        {"content-type": 'multipart/form-data; boundary=""'},
        # Unterminated quote
        {"content-type": 'multipart/form-data; boundary="unterminated'},
        # Dangling escape
        {"content-type": 'multipart/form-data; boundary="dangling\\"'},
        # Illegal escape
        {"content-type": 'multipart/form-data; boundary="bad\\b"'},
        # Boundary length 71
        {"content-type": 'multipart/form-data; boundary="' + 'a' * 71 + '"'},
        # Boundary ending with space
        {"content-type": 'multipart/form-data; boundary="foo "'},
        # Boundary with illegal separator characters
        {"content-type": 'multipart/form-data; boundary="foo;bar"'},
        {"content-type": 'multipart/form-data; boundary="foo@bar"'},
        {"content-type": 'multipart/form-data; boundary="foo<bar"'},
        {"content-type": 'multipart/form-data; boundary="foo[bar"'},
        # Boundary with control characters
        {"content-type": 'multipart/form-data; boundary="foo\r\nbar"'},
        {"content-type": 'multipart/form-data; boundary="foo\x00bar"'},
        # Non-ASCII in boundary (pass raw bytes to bypass client string encoding)
        [(b"content-type", b'multipart/form-data; boundary="foo\xe9bar"')],
        # Wrong media type
        {"content-type": "application/json"},
        {"content-type": "multipart/mixed; boundary=abc"},
        # Trailing semicolon
        {"content-type": "multipart/form-data; boundary=one;"},
        # Missing parameter name
        {"content-type": "multipart/form-data; =val"},
        # Extra characters after closing quote
        {"content-type": 'multipart/form-data; boundary="a"trailing'},
    ],
)
def test_upload_route_invalid_multipart_headers_zero_mutation_and_no_body_consumption(
    client, fresh_db, monkeypatch, headers
):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]

    sel_before = dict(db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sid))
    files_before = [dict(r) for r in db.q("SELECT * FROM resource_selection_file WHERE selection_id = ?", sid)]

    consumed = []
    def tracking_body():
        consumed.append(True)
        yield b"--bound1\r\nContent-Disposition: form-data; name=\"upload_id\"\r\n\r\nu1\r\n--bound1--\r\n"

    reserve_called = []
    orig_reserve = rs.reserve_file_slot
    def mock_reserve(*args, **kwargs):
        reserve_called.append(True)
        return orig_reserve(*args, **kwargs)
    monkeypatch.setattr(rs, "reserve_file_slot", mock_reserve)

    build_called = []
    orig_build = rs.build_selection_view
    def mock_build(*args, **kwargs):
        build_called.append(True)
        return orig_build(*args, **kwargs)
    monkeypatch.setattr(rs, "build_selection_view", mock_build)

    recover_called = []
    orig_recover = rs.recover_selection
    def mock_recover(*args, **kwargs):
        recover_called.append(True)
        return orig_recover(*args, **kwargs)
    monkeypatch.setattr(rs, "recover_selection", mock_recover)

    resp = client.post(
        f"/api/resources/import-selections/{sid}/files",
        content=tracking_body(),
        headers=headers,
    )
    assert resp.status_code == 422
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_multipart"
    assert detail["message"] == "Content-Type must be multipart/form-data with a valid boundary"
    assert "current" not in detail
    assert len(consumed) == 0, "Request body stream was consumed!"
    assert len(reserve_called) == 0, "reserve_file_slot was invoked!"
    assert len(build_called) == 0, "build_selection_view was invoked!"
    assert len(recover_called) == 0, "recover_selection was invoked!"

    sel_after = dict(db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sid))
    files_after = [dict(r) for r in db.q("SELECT * FROM resource_selection_file WHERE selection_id = ?", sid)]
    assert sel_before == sel_after
    assert files_before == files_after


@pytest.mark.parametrize(
    "ct_header,boundary_val",
    [
        ("Multipart/Form-Data; boundary=test_bound_1", "test_bound_1"),
        ('MULTIPART/FORM-DATA; boundary="test_bound_2"', "test_bound_2"),
        ("multipart/form-data; charset=utf-8; boundary=test_bound_3", "test_bound_3"),
        ("multipart/form-data; boundary=test_bound_4; charset=utf-8", "test_bound_4"),
        ('multipart/form-data; boundary="' + 'a' * 70 + '"', "a" * 70),
        ("multipart/form-data; boundary=\"a'()+_,-./:=?\"", "a'()+_,-./:=?"),
    ],
)
def test_upload_route_valid_multipart_variations(client, fresh_db, ct_header, boundary_val):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]

    body = (
        f"--{boundary_val}\r\n"
        f'Content-Disposition: form-data; name="upload_id"\r\n\r\n'
        f"up_{boundary_val[:10]}\r\n"
        f"--{boundary_val}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="sample.json"\r\n'
        f"Content-Type: application/json\r\n\r\n"
        f'{{"ok": true}}\r\n'
        f"--{boundary_val}--\r\n"
    ).encode("latin1")

    resp = client.post(
        f"/api/resources/import-selections/{sid}/files",
        content=body,
        headers={"content-type": ct_header},
    )
    assert resp.status_code == 201
    view = resp.json()
    assert len(view["files"]) == 1
    assert view["files"][0]["file_name"] == "sample.json"


# ---------------------------------------------------------------------------
# 3. Post-Success Authoritative Visibility Probes (All 6 Operations)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("simulate_error", ["none", "exception"])
def test_authoritative_view_loss_on_create_fresh(client, fresh_db, monkeypatch, simulate_error):
    req_id = str(uuid.uuid4())

    def mock_project(sel, *args, **kwargs):
        if simulate_error == "none":
            return None
        raise rs.SelectionStateInvalidError("Simulated view failure")

    monkeypatch.setattr(rs, "_project_selection_view", mock_project)

    resp = client.post("/api/resources/import-selections", json={"request_id": req_id})
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert "current" not in detail
    row = db.one("SELECT * FROM resource_selection WHERE request_id = ?", req_id)
    assert row is not None
    assert row["state"] == "open"


def test_authoritative_view_loss_on_create_replay(client, fresh_db, monkeypatch):
    req_id = str(uuid.uuid4())
    created = client.post("/api/resources/import-selections", json={"request_id": req_id})
    assert created.status_code == 201

    def mock_project(sel, *args, **kwargs):
        return None

    monkeypatch.setattr(rs, "_project_selection_view", mock_project)

    resp = client.post("/api/resources/import-selections", json={"request_id": req_id})
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert "current" not in detail


@pytest.mark.parametrize("simulate_error", ["none", "exception"])
def test_authoritative_view_loss_on_fresh_upload(client, fresh_db, monkeypatch, simulate_error):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    orig_build = rs.build_selection_view

    call_count = 0
    def mock_build(selection_id, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return orig_build(selection_id, *args, **kwargs)
        if simulate_error == "none":
            return None
        raise rs.SelectionStateInvalidError("Simulated view failure")

    monkeypatch.setattr(rs, "build_selection_view", mock_build)

    resp = client.post(
        f"/api/resources/import-selections/{sid}/files",
        files={"file": ("test.json", b'{"key": 1}', "application/json")},
        data={"upload_id": _unique("fresh-loss")},
    )
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert "current" not in detail

    file_row = db.one("SELECT * FROM resource_selection_file WHERE selection_id = ?", sid)
    assert file_row is not None
    assert file_row["status"] == "staged"
    assert Path(file_row["staged_path"]).exists()
    sel_row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sid)
    assert sel_row["selection_revision"] == 1


def test_authoritative_view_loss_on_upload_replay(client, fresh_db, monkeypatch):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    up_id = _unique("replay-loss")
    up1 = _upload_file(client, sid, upload_id=up_id, file_name="rep.json", payload=b"abc")
    assert up1.status_code == 201

    orig_build = rs.build_selection_view
    call_count = 0
    def mock_build(selection_id, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return orig_build(selection_id, *args, **kwargs)
        return None

    monkeypatch.setattr(rs, "build_selection_view", mock_build)

    resp = client.post(
        f"/api/resources/import-selections/{sid}/files",
        files={"file": ("rep.json", b"abc", "application/json")},
        data={"upload_id": up_id},
    )
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert "current" not in detail

    files = db.q("SELECT * FROM resource_selection_file WHERE selection_id = ?", sid)
    assert len(files) == 1
    sel_row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sid)
    assert sel_row["selection_revision"] == 1


@pytest.mark.parametrize("simulate_error", ["none", "exception"])
def test_authoritative_view_loss_on_delete_file(client, fresh_db, monkeypatch, simulate_error):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    up = _upload_file(client, sid, upload_id=_unique("del-loss"), file_name="del.json", payload=b"abc")
    fid = up.json()["files"][0]["file_id"]

    orig_build = rs.build_selection_view
    call_count = 0
    def mock_build(selection_id, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return orig_build(selection_id, *args, **kwargs)
        if simulate_error == "none":
            return None
        raise rs.SelectionStateInvalidError("Simulated view failure")

    monkeypatch.setattr(rs, "build_selection_view", mock_build)

    resp = client.delete(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        params={"expected_revision": 1},
    )
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert "current" not in detail

    file_row = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row["status"] == "removed"
    sel_row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sid)
    assert sel_row["selection_revision"] == 2


@pytest.mark.parametrize("simulate_error", ["none", "exception"])
def test_authoritative_view_loss_on_patch_file(client, fresh_db, monkeypatch, simulate_error):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    up = _upload_file(client, sid, upload_id=_unique("patch-loss"), file_name="p.json", payload=b"abc")
    fid = up.json()["files"][0]["file_id"]

    orig_build = rs.build_selection_view
    call_count = 0
    def mock_build(selection_id, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return orig_build(selection_id, *args, **kwargs)
        if simulate_error == "none":
            return None
        raise rs.SelectionStateInvalidError("Simulated view failure")

    monkeypatch.setattr(rs, "build_selection_view", mock_build)

    resp = client.patch(
        f"/api/resources/import-selections/{sid}/files/{fid}",
        json={"expected_revision": 1, "effective_auxiliary_kind": "translation_map"},
    )
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert "current" not in detail

    file_row = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row["effective_auxiliary_kind"] == "translation_map"
    sel_row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sid)
    assert sel_row["selection_revision"] == 2


@pytest.mark.parametrize("simulate_error", ["none", "exception"])
def test_authoritative_view_loss_on_cancel(client, fresh_db, monkeypatch, simulate_error):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]

    orig_build = rs.build_selection_view
    call_count = 0
    def mock_build(selection_id, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return orig_build(selection_id, *args, **kwargs)
        if simulate_error == "none":
            return None
        raise rs.SelectionStateInvalidError("Simulated view failure")

    monkeypatch.setattr(rs, "build_selection_view", mock_build)

    resp = client.post(
        f"/api/resources/import-selections/{sid}/cancel",
        json={"expected_revision": 0},
    )
    assert resp.status_code == 500
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_state_invalid"
    assert "current" not in detail

    sel_row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sid)
    assert sel_row["state"] == "cancelled"


def test_authoritative_visibility_initial_not_found_controls(client, fresh_db):
    unknown_sid = "sel_nonexistent_0000000000000000"

    resp_get = client.get(f"/api/resources/import-selections/{unknown_sid}")
    assert resp_get.status_code == 404
    assert _stable_detail(resp_get)["code"] == "selection_not_found"

    resp_up = client.post(
        f"/api/resources/import-selections/{unknown_sid}/files",
        files={"file": ("f.json", b"{}", "application/json")},
        data={"upload_id": "u1"},
    )
    assert resp_up.status_code == 404
    assert _stable_detail(resp_up)["code"] == "selection_not_found"

    resp_del = client.delete(
        f"/api/resources/import-selections/{unknown_sid}/files/fid_1",
        params={"expected_revision": 0},
    )
    assert resp_del.status_code == 404
    assert _stable_detail(resp_del)["code"] == "selection_not_found"

    resp_patch = client.patch(
        f"/api/resources/import-selections/{unknown_sid}/files/fid_1",
        json={"expected_revision": 0, "effective_auxiliary_kind": "translation_map"},
    )
    assert resp_patch.status_code == 404
    assert _stable_detail(resp_patch)["code"] == "selection_not_found"

    resp_cancel = client.post(
        f"/api/resources/import-selections/{unknown_sid}/cancel",
        json={"expected_revision": 0},
    )
    assert resp_cancel.status_code == 404
    assert _stable_detail(resp_cancel)["code"] == "selection_not_found"


# ---------------------------------------------------------------------------
# 4. Repair 4D Fix 1: Early Multipart Validation Ahead of Selection Resolution
# ---------------------------------------------------------------------------
from datetime import datetime, timezone, timedelta
from starlette.requests import Request


def _setup_selection_lifecycle(client, variant: str) -> str:
    """Set up a selection in the specified lifecycle state and return selection_id."""
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_dt = datetime.now(timezone.utc)

    if variant == "open":
        pass
    elif variant == "just_before_expiry":
        future_iso = rs._format_iso(now_dt + timedelta(seconds=10))
        db.run("UPDATE resource_selection SET expires_at = ? WHERE selection_id = ?", future_iso, sid)
    elif variant == "already_expirable":
        past_iso = rs._format_iso(now_dt - timedelta(seconds=100))
        db.run("UPDATE resource_selection SET expires_at = ? WHERE selection_id = ?", past_iso, sid)
    elif variant == "cleanup_pending":
        db.run("UPDATE resource_selection SET cleanup_state = 'pending' WHERE selection_id = ?", sid)
    elif variant == "staged_file":
        resp = client.post(
            f"/api/resources/import-selections/{sid}/files",
            files={"file": ("staged.json", b'{"data": "replay_candidate"}', "application/json")},
            data={"upload_id": "u_staged_replay"},
        )
        assert resp.status_code == 201
    elif variant == "active_upload":
        rs.reserve_file_slot(selection_id=sid, upload_id="u_active_inprogress", file_name="active.json")
    else:
        raise ValueError(f"Unknown lifecycle variant: {variant}")
    return sid


_INVALID_MULTIPART_VARIANTS = [
    # 1. duplicate equal boundary
    ("dup_equal_boundary", {"content-type": "multipart/form-data; boundary=bound1; boundary=bound1"}),
    # 2. duplicate different boundary
    ("dup_diff_boundary", {"content-type": "multipart/form-data; boundary=bound1; boundary=bound2"}),
    # 3. mixed-case duplicate boundary name
    ("mixed_case_dup_boundary", {"content-type": "multipart/form-data; boundary=bound1; Boundary=bound1"}),
    # 4. missing boundary
    ("missing_boundary", {"content-type": "multipart/form-data"}),
    # 5. malformed header / quoting
    ("malformed_quoting", {"content-type": 'multipart/form-data; boundary="unclosed'}),
    # 6. duplicate raw Content-Type header
    ("dup_raw_ct_header", [(b"content-type", b"multipart/form-data; boundary=b1"), (b"content-type", b"multipart/form-data; boundary=b2")]),
]


@pytest.mark.parametrize("lifecycle_variant", [
    "open",
    "just_before_expiry",
    "already_expirable",
    "cleanup_pending",
    "staged_file",
    "active_upload",
])
@pytest.mark.parametrize("header_name,header_value", _INVALID_MULTIPART_VARIANTS)
def test_upload_route_invalid_multipart_lifecycle_matrix(
    client, fresh_db, monkeypatch, lifecycle_variant, header_name, header_value
):
    sid = _setup_selection_lifecycle(client, lifecycle_variant)

    sel_before = dict(db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sid))
    files_before = [dict(r) for r in db.q("SELECT * FROM resource_selection_file WHERE selection_id = ? ORDER BY id", sid)]

    staging_dir = rs.get_staging_root() / sid
    staged_bytes_before = {}
    if staging_dir.exists():
        for p in staging_dir.rglob("*"):
            if p.is_file():
                staged_bytes_before[str(p)] = p.read_bytes()

    consumed = []
    def tracking_body():
        consumed.append(True)
        yield b"--bound1\r\nContent-Disposition: form-data; name=\"upload_id\"\r\n\r\nu1\r\n--bound1--\r\n"

    build_called = []
    orig_build = rs.build_selection_view
    def mock_build(*args, **kwargs):
        build_called.append(True)
        return orig_build(*args, **kwargs)
    monkeypatch.setattr(rs, "build_selection_view", mock_build)

    recover_called = []
    orig_recover = rs.recover_selection
    def mock_recover(*args, **kwargs):
        recover_called.append(True)
        return orig_recover(*args, **kwargs)
    monkeypatch.setattr(rs, "recover_selection", mock_recover)

    reserve_called = []
    orig_reserve = rs.reserve_file_slot
    def mock_reserve(*args, **kwargs):
        reserve_called.append(True)
        return orig_reserve(*args, **kwargs)
    monkeypatch.setattr(rs, "reserve_file_slot", mock_reserve)

    tx_called = []
    orig_tx = db.transaction
    def mock_tx(*args, **kwargs):
        tx_called.append(True)
        return orig_tx(*args, **kwargs)
    monkeypatch.setattr(db, "transaction", mock_tx)

    form_called = []
    orig_form = Request.form
    async def mock_form(self, *args, **kwargs):
        form_called.append(True)
        return await orig_form(self, *args, **kwargs)
    monkeypatch.setattr(Request, "form", mock_form)

    resp = client.post(
        f"/api/resources/import-selections/{sid}/files",
        content=tracking_body(),
        headers=header_value,
    )
    assert resp.status_code == 422
    detail = _stable_detail(resp)
    assert detail["code"] == "invalid_multipart"
    assert detail["message"] == "Content-Type must be multipart/form-data with a valid boundary"
    assert "current" not in detail

    assert len(consumed) == 0, "Request body stream was consumed!"
    assert len(reserve_called) == 0, "reserve_file_slot was invoked!"
    assert len(build_called) == 0, "build_selection_view was invoked!"
    assert len(recover_called) == 0, "recover_selection was invoked!"
    assert len(tx_called) == 0, "db.transaction was entered!"
    assert len(form_called) == 0, "Request.form was invoked!"

    sel_after = dict(db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sid))
    files_after = [dict(r) for r in db.q("SELECT * FROM resource_selection_file WHERE selection_id = ? ORDER BY id", sid)]
    assert sel_before == sel_after
    assert sel_after["selection_revision"] == sel_before["selection_revision"]
    assert sel_after["state"] == sel_before["state"]
    assert sel_after["cleanup_state"] == sel_before["cleanup_state"]
    assert files_before == files_after

    staged_bytes_after = {}
    if staging_dir.exists():
        for p in staging_dir.rglob("*"):
            if p.is_file():
                staged_bytes_after[str(p)] = p.read_bytes()
    assert staged_bytes_before == staged_bytes_after


def test_upload_route_valid_multipart_on_expirable_selection_triggers_recovery(client, fresh_db):
    sel = _create_open_selection(client)
    sid = sel["selection_id"]
    now_dt = datetime.now(timezone.utc)
    past_iso = rs._format_iso(now_dt - timedelta(seconds=100))
    db.run("UPDATE resource_selection SET expires_at = ? WHERE selection_id = ?", past_iso, sid)

    resp = client.post(
        f"/api/resources/import-selections/{sid}/files",
        files={"file": ("valid.json", b'{"valid": true}', "application/json")},
        data={"upload_id": "u_valid_expirable"},
    )
    assert resp.status_code == 410
    detail = _stable_detail(resp)
    assert detail["code"] == "selection_terminal"
    assert "expired" in detail["message"].lower()

    row = db.one("SELECT * FROM resource_selection WHERE selection_id = ?", sid)
    assert row["state"] == "expired"
