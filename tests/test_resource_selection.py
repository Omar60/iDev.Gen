"""Tests for browser-selected resource import persistence foundation (Task 1.1).

Covers all 11 required invariants from simplify-resource-session-workflow Task 1.1:
1. Fresh and reopened databases gain additive tables, exact checks/uniqueness, and leave legacy rows intact.
2. Creation is open, fixed 24-hour deadline, access/upload does not extend it.
3. Incomplete/rejected uploads release slot/byte reservations, do not enter manifest or advance revision.
4. Complete staging preserves exact bytes, records exact SHA-256 and mtime_ns, advances revision, invalidates preview.
5. Per-file, file-count, and aggregate limits enforced against actual reserved bytes.
6. Deterministic two-worker race jointly exceeding 50 MiB admits capacity-compatible outcome and leaves exact counters.
7. Two simultaneous claim attempts produce exactly one owner; token-checked release; stale owner cannot release newer claim.
8. Startup and lazy recovery release stale claims, expire at boundary, preserve committed results, purge after tombstone window.
9. Cleanup failure leaves terminal state plus safe retryable warning; later sweep cleans file and clears warning.
10. Selection lifecycle operations never mutate seeded resource_library, asset_revision, or auxiliary_resource rows.
11. No new route exists on the app and existing path API remains untouched.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
from backend import resource_selection as rs
from backend.main import app


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


@pytest.fixture(autouse=True)
def reset_clock():
    rs.set_clock(None)
    yield
    rs.set_clock(None)


@pytest.fixture
def test_db(tmp_path):
    db_path = Path(tmp_path) / "idevgen_test.db"
    conn = db.connect(db_path)
    yield conn, db_path
    conn.close()


# ---------------------------------------------------------------------------
# Test 1: Schema, Additive Tables, Checks, Uniqueness, Legacy Row Integrity
# ---------------------------------------------------------------------------

def test_schema_tables_constraints_and_legacy_row_integrity(tmp_path):
    """1. Fresh and reopened databases gain additive tables and preserve legacy rows."""
    db_path = Path(tmp_path) / "legacy_check.db"
    conn = db.connect(db_path)

    # Seed model, session, shot, and resource store tables
    conn.execute("INSERT INTO model (name, trigger, created_at) VALUES ('hero_model', 'hero', '2026-09-01T00:00:00Z')")
    mid = conn.execute("SELECT id FROM model WHERE name='hero_model'").fetchone()["id"]

    sess_look = "A warm vintage living room with amber sunlight"
    conn.execute(
        "INSERT INTO session (model_id, name, look, wardrobe, created_at) VALUES (?, 'sess_alpha', ?, 'silk robe', '2026-09-01T00:00:00Z')",
        (mid, sess_look),
    )
    sid = conn.execute("SELECT id FROM session WHERE name='sess_alpha'").fetchone()["id"]

    shot_prompt = "reading a novel by the window"
    conn.execute(
        "INSERT INTO shot (session_id, prompt, created_at) VALUES (?, ?, '2026-09-01T00:00:00Z')",
        (sid, shot_prompt),
    )

    conn.execute(
        "INSERT INTO resource_library (library_key, display_name, kind, created_at) VALUES ('lib_one', 'Library One', 'rooms', '2026-09-01T00:00:00Z')"
    )
    lib_id = conn.execute("SELECT id FROM resource_library WHERE library_key='lib_one'").fetchone()["id"]

    conn.execute(
        "INSERT INTO asset_revision (library_id, source_id, content_digest, payload, created_at) VALUES (?, 'room_101', 'digest_abc', '{\"key\": \"val\"}', '2026-09-01T00:00:00Z')",
        (lib_id,),
    )
    conn.execute(
        "INSERT INTO auxiliary_resource (library_id, kind, content_digest, payload, created_at) VALUES (?, 'cut_map', 'digest_cut', '{\"cut\": 1}', '2026-09-01T00:00:00Z')",
        (lib_id,),
    )
    conn.commit()

    # Verify tables exist
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "resource_selection" in tables
    assert "resource_selection_file" in tables

    # Test SQL CHECK constraints on resource_selection
    # Invalid state
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resource_selection (selection_id, request_id, state, created_at, updated_at, expires_at) "
            "VALUES ('s_bad_state', 'r_bad_state', 'invalid_state', 'now', 'now', 'now')"
        )

    # Invalid revision (negative or > Number.MAX_SAFE_INTEGER)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resource_selection (selection_id, request_id, selection_revision, state, created_at, updated_at, expires_at) "
            "VALUES ('s_bad_rev', 'r_bad_rev', -1, 'open', 'now', 'now', 'now')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resource_selection (selection_id, request_id, selection_revision, state, created_at, updated_at, expires_at) "
            "VALUES ('s_bad_rev2', 'r_bad_rev2', 9007199254740992, 'open', 'now', 'now', 'now')"
        )

    # Total reserved bytes exceeding 50 MiB (52428800)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resource_selection (selection_id, request_id, total_reserved_bytes, state, created_at, updated_at, expires_at) "
            "VALUES ('s_bad_bytes', 'r_bad_bytes', 52428801, 'open', 'now', 'now', 'now')"
        )

    # Active file count exceeding 20
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resource_selection (selection_id, request_id, active_file_count, state, created_at, updated_at, expires_at) "
            "VALUES ('s_bad_cnt', 'r_bad_cnt', 21, 'open', 'now', 'now', 'now')"
        )

    # Valid insert into resource_selection
    conn.execute(
        "INSERT INTO resource_selection (selection_id, request_id, state, created_at, updated_at, expires_at) "
        "VALUES ('s_valid', 'r_valid', 'open', 'now', 'now', 'now')"
    )

    # File byte count exceeding 10 MiB (10485760)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resource_selection_file (selection_id, file_id, upload_id, file_name, byte_count, created_at, updated_at) "
            "VALUES ('s_valid', 'f_bad', 'u_bad', 'name', 10485761, 'now', 'now')"
        )

    # Duplicate upload_id in same selection
    conn.execute(
        "INSERT INTO resource_selection_file (selection_id, file_id, upload_id, file_name, byte_count, created_at, updated_at) "
        "VALUES ('s_valid', 'f1', 'u_dup', 'name1', 100, 'now', 'now')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO resource_selection_file (selection_id, file_id, upload_id, file_name, byte_count, created_at, updated_at) "
            "VALUES ('s_valid', 'f2', 'u_dup', 'name2', 100, 'now', 'now')"
        )

    conn.commit()
    conn.close()

    # Reopen database through db.connect to prove clean re-connect
    conn2 = db.connect(db_path)
    m = conn2.execute("SELECT name FROM model WHERE id=?", (mid,)).fetchone()
    assert m["name"] == "hero_model"

    s = conn2.execute("SELECT name, look, wardrobe FROM session WHERE id=?", (sid,)).fetchone()
    assert s["name"] == "sess_alpha"
    assert s["look"] == sess_look
    assert s["wardrobe"] == "silk robe"

    sh = conn2.execute("SELECT prompt FROM shot WHERE session_id=?", (sid,)).fetchone()
    assert sh["prompt"] == shot_prompt

    lib = conn2.execute("SELECT library_key, kind FROM resource_library WHERE id=?", (lib_id,)).fetchone()
    assert lib["library_key"] == "lib_one"
    assert lib["kind"] == "rooms"

    ast = conn2.execute("SELECT source_id, content_digest, payload FROM asset_revision WHERE library_id=?", (lib_id,)).fetchone()
    assert ast["source_id"] == "room_101"
    assert ast["content_digest"] == "digest_abc"
    assert ast["payload"] == '{"key": "val"}'

    aux = conn2.execute("SELECT kind, content_digest, payload FROM auxiliary_resource WHERE library_id=?", (lib_id,)).fetchone()
    assert aux["kind"] == "cut_map"
    assert aux["content_digest"] == "digest_cut"
    assert aux["payload"] == '{"cut": 1}'
    conn2.close()


# ---------------------------------------------------------------------------
# Test 2: Selection Creation, Fixed 24h Expiry, Activity Does Not Extend It
# ---------------------------------------------------------------------------

def test_selection_creation_fixed_expiry_and_access_invariance(test_db):
    """2. Creation is open, has fixed 24h deadline, and access/upload does not extend it."""
    t0 = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    t0_iso = _iso(t0)
    expected_expiry = _iso(t0 + timedelta(hours=24))

    sel = rs.create_selection("req-create-1", now_iso=t0_iso)
    assert sel["state"] == "open"
    assert sel["selection_revision"] == 0
    assert sel["created_at"] == t0_iso
    assert sel["expires_at"] == expected_expiry
    assert sel["active_file_count"] == 0
    assert sel["total_reserved_bytes"] == 0

    # Idempotent replay of same request_id returns existing record
    sel_replay = rs.create_selection("req-create-1", now_iso=_iso(t0 + timedelta(hours=1)))
    assert sel_replay["selection_id"] == sel["selection_id"]
    assert sel_replay["created_at"] == t0_iso
    assert sel_replay["expires_at"] == expected_expiry

    # Access at T0 + 12 hours
    t12_iso = _iso(t0 + timedelta(hours=12))
    f_res = rs.reserve_file_slot(sel["selection_id"], "up-1", "file1.json", now_iso=t12_iso)
    rs.stage_file_chunk(sel["selection_id"], f_res["file_id"], b"hello world", now_iso=t12_iso)
    rs.finalize_staged_file(sel["selection_id"], f_res["file_id"], now_iso=t12_iso)

    # Verify expires_at remains strictly unchanged
    updated_sel = rs.get_selection(sel["selection_id"], now_iso=t12_iso)
    assert updated_sel["expires_at"] == expected_expiry
    assert updated_sel["selection_revision"] == 1


# ---------------------------------------------------------------------------
# Test 3: Incomplete / Rejected Uploads Release Reservations
# ---------------------------------------------------------------------------

def test_incomplete_rejected_upload_releases_reservations_without_advancing_revision(test_db):
    """3. Incomplete/rejected uploads release slot/byte reservations and do not advance revision."""
    sel = rs.create_selection("req-incomplete-1")
    sid = sel["selection_id"]
    assert sel["selection_revision"] == 0

    # Reserve slot and stream 2000 bytes
    f = rs.reserve_file_slot(sid, "up-inc", "partial.json")
    fid = f["file_id"]
    chunk = b"X" * 2000
    rs.stage_file_chunk(sid, fid, chunk)

    mid_sel = rs.get_selection(sid)
    assert mid_sel["active_file_count"] == 1
    assert mid_sel["total_reserved_bytes"] == 2000
    assert mid_sel["selection_revision"] == 0

    # Manifest check: incomplete file MUST NOT enter manifest
    manifest = rs.get_selection_manifest(sid)
    assert len(manifest) == 0

    # Abort/reject upload
    rs.abort_file_reservation(sid, fid, reason="Client disconnected")

    after_sel = rs.get_selection(sid)
    assert after_sel["active_file_count"] == 0
    assert after_sel["total_reserved_bytes"] == 0
    assert after_sel["selection_revision"] == 0

    # Verify manifest is still empty
    assert len(rs.get_selection_manifest(sid)) == 0


# ---------------------------------------------------------------------------
# Test 4: Complete Staging Preserves Bytes, Evidence, Revision, Invalidation
# ---------------------------------------------------------------------------

def test_complete_staging_preserves_bytes_evidence_advances_revision_and_invalidates_preview(test_db):
    """4. Complete staging records exact SHA-256 and mtime_ns, bumps revision, and invalidates preview."""
    sel = rs.create_selection("req-stage-1")
    sid = sel["selection_id"]

    # Seed an existing preview
    saved = rs.save_preview(
        sid,
        expected_revision=0,
        preview_token="prev_tok_001",
        manifest_digest="man_dig_001",
        committable=True,
        report={"valid": True, "count": 0},
    )
    assert saved is True
    p_sel = rs.get_selection(sid)
    assert p_sel["preview_token"] == "prev_tok_001"
    assert p_sel["preview_manifest_digest"] == "man_dig_001"

    # Reserve slot, stream payload
    file_info = rs.reserve_file_slot(sid, "up-full-1", "rooms.json")
    fid = file_info["file_id"]

    payload = b'{"rooms": [{"key": "attic", "label": "Top Attic Room"}]}'
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    expected_size = len(payload)

    rs.stage_file_chunk(sid, fid, payload)
    finalized = rs.finalize_staged_file(sid, fid, declared_library="custom_rooms")

    # Assert exact byte count and digest
    assert finalized["status"] == "staged"
    assert finalized["byte_count"] == expected_size
    assert finalized["staged_size"] == expected_size
    assert finalized["staged_sha256"] == expected_sha256
    assert finalized["declared_library"] == "custom_rooms"
    assert finalized["order_index"] == 1

    # Exact nanosecond mtime preserved as decimal text
    assert finalized["staged_mtime_ns"].isdigit()
    assert finalized["fingerprint"] == f"{expected_sha256}:{expected_size}:{finalized['staged_mtime_ns']}"

    # Staged file content on disk is byte-for-byte identical
    staged_disk = Path(finalized["staged_path"]).read_bytes()
    assert staged_disk == payload

    # Revision bumped exactly once, and all preview fields invalidated atomically
    after_sel = rs.get_selection(sid)
    assert after_sel["selection_revision"] == 1
    assert after_sel["preview_token"] is None
    assert after_sel["preview_manifest_digest"] is None
    assert after_sel["preview_committable"] is None
    assert after_sel["preview_report"] is None
    assert after_sel["active_file_count"] == 1
    assert after_sel["total_reserved_bytes"] == expected_size

    # Manifest contains the file in stable order
    manifest = rs.get_selection_manifest(sid)
    assert len(manifest) == 1
    assert manifest[0]["file_id"] == fid
    assert manifest[0]["order_index"] == 1


# ---------------------------------------------------------------------------
# Test 5: Limits Enforced Against Actual Streamed Bytes
# ---------------------------------------------------------------------------

def test_limits_enforced_against_actual_reserved_bytes(test_db):
    """5. Per-file (10 MiB), file-count (20), and aggregate (50 MiB) limits enforced."""
    sel = rs.create_selection("req-limits-1")
    sid = sel["selection_id"]

    # 5.1 Per-file limit: 10 MiB (10,485,760 bytes)
    f1 = rs.reserve_file_slot(sid, "up-lim-1", "big1.bin")
    fid1 = f1["file_id"]

    # 10 MiB is allowed
    rs.stage_file_chunk(sid, fid1, b"A" * (10 * 1024 * 1024))
    # 1 more byte fails
    with pytest.raises(rs.FileByteLimitExceededError):
        rs.stage_file_chunk(sid, fid1, b"B")

    rs.finalize_staged_file(sid, fid1)

    # 5.2 File count limit: maximum 20 files
    sel_cnt = rs.create_selection("req-filecount-1")
    sid_cnt = sel_cnt["selection_id"]
    for i in range(20):
        f = rs.reserve_file_slot(sid_cnt, f"up-cnt-{i}", f"file_{i}.txt")
        rs.stage_file_chunk(sid_cnt, f["file_id"], b"ok")
        rs.finalize_staged_file(sid_cnt, f["file_id"])

    # 21st file slot reservation must fail
    with pytest.raises(rs.FileCountLimitExceededError):
        rs.reserve_file_slot(sid_cnt, "up-cnt-21", "file_21.txt")

    # 5.3 Aggregate limit: 50 MiB (52,428,800 bytes)
    sel_agg = rs.create_selection("req-agg-1")
    sid_agg = sel_agg["selection_id"]
    for i in range(5):
        f = rs.reserve_file_slot(sid_agg, f"up-agg-{i}", f"agg_{i}.bin")
        rs.stage_file_chunk(sid_agg, f["file_id"], b"K" * (10 * 1024 * 1024))
        rs.finalize_staged_file(sid_agg, f["file_id"])

    # Aggregate is now 50 MiB. Reserving 1 more slot and 1 byte fails
    f_extra = rs.reserve_file_slot(sid_agg, "up-agg-extra", "extra.bin")
    with pytest.raises(rs.AggregateByteLimitExceededError):
        rs.stage_file_chunk(sid_agg, f_extra["file_id"], b"X")


# ---------------------------------------------------------------------------
# Test 6: Deterministic Two-Worker Aggregate Capacity Race
# ---------------------------------------------------------------------------

def test_two_worker_aggregate_capacity_race(test_db):
    """6. Two-worker race exceeding 50 MiB admits capacity-compatible outcome and leaves exact counters."""
    sel = rs.create_selection("req-race-1")
    sid = sel["selection_id"]

    # Pre-stage 40 MiB (4 files x 10 MiB)
    for i in range(4):
        f = rs.reserve_file_slot(sid, f"race-base-{i}", f"base_{i}.bin")
        rs.stage_file_chunk(sid, f["file_id"], b"Z" * (10 * 1024 * 1024))
        rs.finalize_staged_file(sid, f["file_id"])

    base_sel = rs.get_selection(sid)
    assert base_sel["total_reserved_bytes"] == 40 * 1024 * 1024
    # Remaining capacity = 10 MiB

    # Worker 1 wants 8 MiB, Worker 2 wants 8 MiB.
    # 8 MiB fits individually (< 10 MiB), but together 16 MiB > 10 MiB remaining.
    f_w1 = rs.reserve_file_slot(sid, "race-w1", "w1.bin")
    f_w2 = rs.reserve_file_slot(sid, "race-w2", "w2.bin")

    barrier = threading.Barrier(2)
    results = {}

    def worker_task(worker_id: str, fid: str):
        barrier.wait()
        try:
            rs.stage_file_chunk(sid, fid, b"W" * (8 * 1024 * 1024))
            rs.finalize_staged_file(sid, fid)
            results[worker_id] = "success"
        except rs.AggregateByteLimitExceededError:
            rs.abort_file_reservation(sid, fid, reason="Capacity exceeded")
            results[worker_id] = "exceeded"

    t1 = threading.Thread(target=worker_task, args=("w1", f_w1["file_id"]))
    t2 = threading.Thread(target=worker_task, args=("w2", f_w2["file_id"]))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one succeeded and one failed
    outcomes = sorted(results.values())
    assert outcomes == ["exceeded", "success"], f"Race results: {results}"

    # Verify exact counters: 40 MiB + 8 MiB = 48 MiB
    final_sel = rs.get_selection(sid)
    assert final_sel["total_reserved_bytes"] == 48 * 1024 * 1024
    assert final_sel["active_file_count"] == 5  # 4 base + 1 winner


# ---------------------------------------------------------------------------
# Test 7: Commit Claims, Simultaneous Ownership, Token Check, Stale Owner
# ---------------------------------------------------------------------------

def test_commit_claims_simultaneous_ownership_and_release(test_db):
    """7. Simultaneous claims produce one owner, same-owner release is token-checked, stale owner cannot release."""
    sel = rs.create_selection("req-claim-1")
    sid = sel["selection_id"]

    # Stage a file and record a preview
    f = rs.reserve_file_slot(sid, "claim-up-1", "doc.json")
    rs.stage_file_chunk(sid, f["file_id"], b'{"items": []}')
    rs.finalize_staged_file(sid, f["file_id"])

    manifest = rs.get_selection_manifest(sid)
    m_digest = rs.compute_manifest_digest(manifest)
    rs.save_preview(sid, expected_revision=1, preview_token="tok_alpha", manifest_digest=m_digest, committable=True)

    # Concurrently race two claim attempts for the SAME tuple using a Barrier
    barrier = threading.Barrier(2)
    results = [None, None]

    def worker(idx):
        barrier.wait()
        results[idx] = rs.acquire_commit_claim(
            sid,
            expected_revision=1,
            preview_token="tok_alpha",
            manifest_digest=m_digest,
            lease_seconds=300,
        )

    t1 = threading.Thread(target=worker, args=(0,))
    t2 = threading.Thread(target=worker, args=(1,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    statuses = {results[0].status, results[1].status}
    assert statuses == {"acquired", "active_same_tuple"}

    owner = results[0] if results[0].status == "acquired" else results[1]
    assert owner.commit_token is not None
    token1 = owner.commit_token

    # Selection state is now committing
    curr_sel = rs.get_selection(sid, perform_recovery=False)
    assert curr_sel["state"] == "committing"
    assert curr_sel["claim_commit_token"] == token1

    # Claim 3 attempts with DIFFERENT tuple -> conflict
    claim3 = rs.acquire_commit_claim(
        sid,
        expected_revision=1,
        preview_token="tok_different",
        manifest_digest=m_digest,
    )
    assert claim3.status == "conflict"

    # Attempt to release with invalid/bogus token -> False
    assert rs.release_commit_claim(sid, "bogus_token") is False
    assert rs.get_selection(sid, perform_recovery=False)["state"] == "committing"

    # Valid release with token1 -> True, returns to open
    assert rs.release_commit_claim(sid, token1) is True
    reopened_sel = rs.get_selection(sid, perform_recovery=False)
    assert reopened_sel["state"] == "open"
    assert reopened_sel["claim_commit_token"] is None

    # New owner acquires claim with fresh token
    claim4 = rs.acquire_commit_claim(
        sid,
        expected_revision=1,
        preview_token="tok_alpha",
        manifest_digest=m_digest,
    )
    assert claim4.status == "acquired"
    token4 = claim4.commit_token
    assert token4 != token1

    # Stale token1 CANNOT release the new owner's claim!
    assert rs.release_commit_claim(sid, token1) is False
    assert rs.get_selection(sid, perform_recovery=False)["claim_commit_token"] == token4


# ---------------------------------------------------------------------------
# Test 8: Startup & Lazy Recovery, Fixed Expiry, Committed Terminal, Purge
# ---------------------------------------------------------------------------

def test_startup_and_lazy_recovery_lifecycle_and_purge(test_db):
    """8. Startup/lazy recovery releases stale claims, expires at boundary, preserves committed, purges after tombstone."""
    t0 = datetime(2026, 9, 12, 8, 0, 0, tzinfo=timezone.utc)
    t0_iso = _iso(t0)

    # 8.1 Prior process left an unfinished committing claim within lifetime (< 24h)
    sel1 = rs.create_selection("req-rec-1", now_iso=t0_iso)
    sid1 = sel1["selection_id"]
    f1 = rs.reserve_file_slot(sid1, "rec-up-1", "a.json", now_iso=t0_iso)
    rs.stage_file_chunk(sid1, f1["file_id"], b"data", now_iso=t0_iso)
    rs.finalize_staged_file(sid1, f1["file_id"], now_iso=t0_iso)
    m1 = rs.compute_manifest_digest(rs.get_selection_manifest(sid1))
    rs.save_preview(sid1, expected_revision=1, preview_token="p1", manifest_digest=m1, committable=True, now_iso=t0_iso)
    c1 = rs.acquire_commit_claim(sid1, 1, "p1", m1, lease_seconds=300, now_iso=t0_iso)
    assert c1.status == "acquired"

    # Startup recovery runs at T0 + 1 hour (< 24h)
    rec_summary = rs.startup_recovery(now_iso=_iso(t0 + timedelta(hours=1)))
    assert rec_summary["stale_claims_reopened"] >= 1
    sel1_recovered = rs.get_selection(sid1, perform_recovery=False)
    assert sel1_recovered["state"] == "open"
    assert sel1_recovered["claim_commit_token"] is None

    # 8.2 Committed result is terminal and NEVER rewritten by recovery
    c1_again = rs.acquire_commit_claim(sid1, 1, "p1", m1, now_iso=_iso(t0 + timedelta(hours=2)))
    assert c1_again.status == "acquired"
    res_recorded = rs.record_commit_result(
        sid1,
        c1_again.commit_token,
        {"imported_libraries": ["custom_rooms"], "total_assets": 1},
        now_iso=_iso(t0 + timedelta(hours=2)),
    )
    assert res_recorded is True

    committed_sel = rs.get_selection(sid1, perform_recovery=False)
    assert committed_sel["state"] == "committed"
    assert "imported_libraries" in committed_sel["commit_result"]

    # Recovery at T0 + 30 hours (> 24h) must preserve committed state
    rs.startup_recovery(now_iso=_iso(t0 + timedelta(hours=30)))
    committed_sel_after = rs.get_selection(sid1, perform_recovery=False)
    assert committed_sel_after["state"] == "committed"
    assert committed_sel_after["commit_result"] == committed_sel["commit_result"]

    # 8.3 Overdue open selection expires at exactly 24 hours
    sel2 = rs.create_selection("req-rec-2", now_iso=t0_iso)
    sid2 = sel2["selection_id"]

    # At T0 + 23h59m: still open
    s2_early = rs.get_selection(sid2, now_iso=_iso(t0 + timedelta(hours=23, minutes=59)))
    assert s2_early["state"] == "open"

    # At T0 + 24h01m: expired
    s2_late = rs.get_selection(sid2, now_iso=_iso(t0 + timedelta(hours=24, minutes=1)))
    assert s2_late["state"] == "expired"

    # 8.4 Tombstone window: readable until expires_at + 24h (T0 + 48h), then purged
    # At T0 + 36h (within tombstone window): readable
    s2_tombstone = rs.get_selection(sid2, now_iso=_iso(t0 + timedelta(hours=36)))
    assert s2_tombstone is not None
    assert s2_tombstone["state"] == "expired"

    # At T0 + 49h (> 48h): purged
    s2_purged = rs.get_selection(sid2, now_iso=_iso(t0 + timedelta(hours=49)))
    assert s2_purged is None


# ---------------------------------------------------------------------------
# Test 9: Cleanup Failure Leaves Safe Warning, Retry Clears Warning
# ---------------------------------------------------------------------------

def test_cleanup_failure_and_retry_warning_safety(test_db, monkeypatch):
    """9. Cleanup failure leaves terminal state plus safe warning; later sweep cleans file and clears warning."""
    sel = rs.create_selection("req-clean-1")
    sid = sel["selection_id"]

    f = rs.reserve_file_slot(sid, "clean-up-1", "temp.json")
    fid = f["file_id"]
    rs.stage_file_chunk(sid, fid, b"temporary bytes")
    finalized = rs.finalize_staged_file(sid, fid)
    staged_path = Path(finalized["staged_path"])
    assert staged_path.is_file()

    # Monkeypatch _safe_delete_file to simulate a filesystem lock / PermissionError
    orig_delete = rs._safe_delete_file

    def mock_fail_delete(path_str, staging_root=None):
        return False, "Temporary file cleanup failed: PermissionError. Retrying on next recovery sweep."

    monkeypatch.setattr(rs, "_safe_delete_file", mock_fail_delete)

    # Cancel selection while cleanup fails
    cancelled_sel = rs.cancel_selection(sid, expected_revision=1)
    assert cancelled_sel["state"] == "cancelled"
    assert cancelled_sel["cleanup_state"] == "failed"
    assert "PermissionError" in cancelled_sel["cleanup_warning"]

    # Invariant: Warning MUST NOT contain any private machine path
    assert str(staged_path) not in cancelled_sel["cleanup_warning"]
    assert "Users" not in cancelled_sel["cleanup_warning"]
    assert "data" not in cancelled_sel["cleanup_warning"]

    # File still exists on disk because delete failed
    assert staged_path.is_file()

    # Restore real deletion and run recovery sweep
    monkeypatch.setattr(rs, "_safe_delete_file", orig_delete)
    rec_result = rs.recover_selection(sid)

    assert rec_result["cleanup_state"] == "cleaned"
    assert rec_result["cleanup_warning"] == ""
    # File is now deleted
    assert not staged_path.is_file()


# ---------------------------------------------------------------------------
# Test 10: Resource Isolation Never Changes Seeded Resource Tables
# ---------------------------------------------------------------------------

def test_resource_isolation_never_mutates_seeded_resource_tables(test_db):
    """10. Selection lifecycle operations never modify resource_library, asset_revision, or auxiliary_resource."""
    conn, _ = test_db

    # Seed resource tables
    conn.execute("INSERT INTO resource_library (library_key, display_name, kind, created_at) VALUES ('lib_iso', 'Iso Lib', 'rooms', 'now')")
    lib_id = conn.execute("SELECT id FROM resource_library WHERE library_key='lib_iso'").fetchone()["id"]

    conn.execute(
        "INSERT INTO asset_revision (library_id, source_id, content_digest, payload, created_at) VALUES (?, 'iso_src', 'dig_iso', '{\"room\": \"main\"}', 'now')",
        (lib_id,),
    )
    conn.execute(
        "INSERT INTO auxiliary_resource (library_id, kind, content_digest, payload, created_at) VALUES (?, 'translation_map', 'dig_trans', '{\"map\": {}}', 'now')",
        (lib_id,),
    )
    conn.commit()

    def snapshot_resources():
        libs = [dict(r) for r in conn.execute("SELECT * FROM resource_library ORDER BY id").fetchall()]
        assets = [dict(r) for r in conn.execute("SELECT * FROM asset_revision ORDER BY id").fetchall()]
        auxes = [dict(r) for r in conn.execute("SELECT * FROM auxiliary_resource ORDER BY id").fetchall()]
        return libs, assets, auxes

    initial_libs, initial_assets, initial_auxes = snapshot_resources()

    # Perform diverse selection operations
    sel = rs.create_selection("req-iso-1")
    sid = sel["selection_id"]
    f1 = rs.reserve_file_slot(sid, "iso-up-1", "a.json")
    rs.stage_file_chunk(sid, f1["file_id"], b'{"test": 1}')
    rs.finalize_staged_file(sid, f1["file_id"])

    f2 = rs.reserve_file_slot(sid, "iso-up-2", "b.json")
    rs.stage_file_chunk(sid, f2["file_id"], b"junk")
    rs.abort_file_reservation(sid, f2["file_id"])

    m_dig = rs.compute_manifest_digest(rs.get_selection_manifest(sid))
    rs.save_preview(sid, expected_revision=1, preview_token="p_iso", manifest_digest=m_dig, committable=True)

    claim = rs.acquire_commit_claim(sid, 1, "p_iso", m_dig)
    assert claim.status == "acquired"
    rs.release_commit_claim(sid, claim.commit_token)

    rs.cancel_selection(sid, expected_revision=1)
    rs.startup_recovery()

    # Re-verify resource store snapshot
    final_libs, final_assets, final_auxes = snapshot_resources()
    assert final_libs == initial_libs
    assert final_assets == initial_assets
    assert final_auxes == initial_auxes


# ---------------------------------------------------------------------------
# Test 11: No New Route Exists and Path API/CLI Remains Untouched
# ---------------------------------------------------------------------------

def test_no_new_route_exists_and_existing_api_untouched():
    """11. No new route exists on FastAPI app and path API remains untouched."""
    routes = [r.path for r in app.routes]

    # Task 1.1 must NOT implement any HTTP routes for import selections
    for r in routes:
        assert not r.startswith("/api/resources/import-selections"), f"Unexpected route found: {r}"

    # Existing resource path endpoints must still exist
    assert "/api/resources/libraries" in routes
    assert "/api/resources/import/preview" in routes
    assert "/api/resources/import/commit" in routes


# ---------------------------------------------------------------------------
# Test 12: Outer Transaction Rollback Preserves Staged Bytes
# ---------------------------------------------------------------------------

def test_outer_transaction_rollback_preserves_staged_bytes(test_db):
    """Outer transaction rollback around cancel or commit persistence preserves staged bytes."""
    # 1. Test cancel rollback
    sel = rs.create_selection("req-tx-rollback-cancel")
    sid = sel["selection_id"]
    f = rs.reserve_file_slot(sid, "up-tx-1", "file.json")
    fid = f["file_id"]
    rs.stage_file_chunk(sid, fid, b'{"keep": "bytes"}')
    finalized = rs.finalize_staged_file(sid, fid)
    staged_path = Path(finalized["staged_path"])
    assert staged_path.is_file()

    # Outer transaction wraps cancel and deliberately rolls back
    try:
        with db.transaction():
            rs.cancel_selection(sid, expected_revision=1)
            raise RuntimeError("simulated cancel rollback")
    except RuntimeError:
        pass

    # Database state must be rolled back to open, and staged bytes must NOT be deleted
    reverted_sel = rs.get_selection(sid, perform_recovery=False)
    assert reverted_sel["state"] == "open"
    assert staged_path.is_file()
    assert staged_path.read_bytes() == b'{"keep": "bytes"}'

    # 2. Test commit-result rollback
    manifest = rs.get_selection_manifest(sid)
    m_digest = rs.compute_manifest_digest(manifest)
    rs.save_preview(sid, expected_revision=1, preview_token="p_tx", manifest_digest=m_digest, committable=True)
    claim = rs.acquire_commit_claim(sid, 1, "p_tx", m_digest)
    assert claim.status == "acquired"
    token = claim.commit_token

    try:
        with db.transaction():
            rs.record_commit_result(sid, token, {"result": "ok"})
            raise RuntimeError("simulated commit rollback")
    except RuntimeError:
        pass

    # Database state must be rolled back to committing, and staged bytes must still exist
    reverted_commit_sel = rs.get_selection(sid, perform_recovery=False)
    assert reverted_commit_sel["state"] == "committing"
    assert reverted_commit_sel["claim_commit_token"] == token
    assert staged_path.is_file()

    # When commit completes without rollback, bytes are cleaned
    res = rs.record_commit_result(sid, token, {"result": "ok"})
    assert res is True
    assert not staged_path.is_file()
    committed_sel = rs.get_selection(sid, perform_recovery=False)
    assert committed_sel["state"] == "committed"
    assert committed_sel["cleanup_state"] == "cleaned"


# ---------------------------------------------------------------------------
# Test 13: Finalization Rollback and Startup Recovery Leaves No Orphans
# ---------------------------------------------------------------------------

def test_finalization_rollback_and_startup_recovery_leaves_no_orphans(test_db):
    """Finalization rollback leaves recoverable recorded path; startup recovery leaves no orphans."""
    sel = rs.create_selection("req-fin-rollback")
    sid = sel["selection_id"]
    f = rs.reserve_file_slot(sid, "up-fin-1", "doc.json")
    fid = f["file_id"]
    rs.stage_file_chunk(sid, fid, b"data-for-finalization")

    # Staged file is at recorded path
    f_row = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    staged_path = Path(f_row["staged_path"])
    assert staged_path.is_file()

    # Simulate rollback during finalize_staged_file
    try:
        with db.transaction():
            rs.finalize_staged_file(sid, fid)
            raise RuntimeError("crash after finalization before commit")
    except RuntimeError:
        pass

    # Row is still reserved pointing to the recorded path
    rolled_row = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert rolled_row["status"] == "reserved"
    assert staged_path.is_file()

    # Startup recovery runs and must remove the bytes without leaving orphan files
    summary = rs.startup_recovery()
    assert summary["incomplete_uploads_discarded"] >= 1

    # Neither .staged nor any .part survives on disk
    assert not staged_path.is_file()
    assert not staged_path.with_suffix(".part").exists()
    assert db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid) is None


# ---------------------------------------------------------------------------
# Test 14: Failed Aborted-Upload Cleanup Retried and Warning Cleared
# ---------------------------------------------------------------------------

def test_aborted_upload_failed_cleanup_retried_and_warning_cleared(test_db, monkeypatch):
    """Failed aborted upload cleanup in open selection is retried and warning cleared after success."""
    sel = rs.create_selection("req-abort-retry")
    sid = sel["selection_id"]

    f = rs.reserve_file_slot(sid, "up-abort-1", "abort.json")
    fid = f["file_id"]
    rs.stage_file_chunk(sid, fid, b"bytes-to-abort")
    staged_path = Path(f["staged_path"])
    assert staged_path.is_file()

    orig_delete = rs._safe_delete_file

    def mock_fail_delete(path_str, staging_root=None):
        return False, "Temporary file cleanup failed: LockError. Retrying on next recovery sweep."

    monkeypatch.setattr(rs, "_safe_delete_file", mock_fail_delete)

    # Abort while deletion fails
    rs.abort_file_reservation(sid, fid)

    # In open selection: file row is 'failed', selection has cleanup_state='failed'
    sel_open = rs.get_selection(sid, perform_recovery=False)
    assert sel_open["state"] == "open"
    assert sel_open["cleanup_state"] == "failed"
    assert "LockError" in sel_open["cleanup_warning"]
    assert staged_path.is_file()

    # Reusing the upload ID while cleanup still fails is rejected and preserves path
    with pytest.raises(rs.ResourceSelectionError) as exc_info:
        rs.reserve_file_slot(sid, "up-abort-1", "abort.json")
    assert "cleanup failed" in str(exc_info.value)
    assert staged_path.is_file()

    # Restore real deletion
    monkeypatch.setattr(rs, "_safe_delete_file", orig_delete)

    # Run opportunistic sweep on the open selection
    sweep_res = rs.opportunistic_sweep()
    assert sweep_res["swept"] >= 1

    # File on disk is now removed
    assert not staged_path.is_file()

    # File row is deleted and parent warning is reconciled
    assert db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid) is None
    sel_reconciled = rs.get_selection(sid, perform_recovery=False)
    assert sel_reconciled["state"] == "open"
    assert sel_reconciled["cleanup_state"] == "none"
    assert sel_reconciled["cleanup_warning"] == ""


# ---------------------------------------------------------------------------
# Test 15: Startup Recovery Bounded and Sweep Non-Starving
# ---------------------------------------------------------------------------

def test_startup_recovery_bounded_and_sweep_non_starving(test_db):
    """Startup recovery respects limit and opportunistic sweep does not starve on clean tombstones."""
    t0 = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)
    t0_iso = _iso(t0)

    # Create 4 incomplete reserved uploads
    for i in range(4):
        s = rs.create_selection(f"req-bound-{i}", now_iso=t0_iso)
        f = rs.reserve_file_slot(s["selection_id"], f"up-bound-{i}", f"f{i}.json", now_iso=t0_iso)
        rs.stage_file_chunk(s["selection_id"], f["file_id"], b"chunk", now_iso=t0_iso)

    # Run bounded startup recovery with limit=2
    summary1 = rs.startup_recovery(limit=2, now_iso=t0_iso)
    assert summary1["incomplete_uploads_discarded"] == 2

    # Second pass cleans remaining
    summary2 = rs.startup_recovery(limit=2, now_iso=t0_iso)
    assert summary2["incomplete_uploads_discarded"] == 2

    # Cancel the 4 bound selections so they become cleaned tombstones
    for i in range(4):
        sid_i = db.one("SELECT selection_id FROM resource_selection WHERE request_id = ?", f"req-bound-{i}")["selection_id"]
        rs.cancel_selection(sid_i)

    # Test non-starvation: create 3 clean tombstones within their 24h retention window
    for i in range(3):
        ts_sel = rs.create_selection(f"req-tomb-{i}", now_iso=t0_iso)
        ts_sid = ts_sel["selection_id"]
        rs.cancel_selection(ts_sid)
        assert rs.get_selection(ts_sid, perform_recovery=False)["cleanup_state"] == "cleaned"

    # Create 1 overdue open selection
    overdue_sel = rs.create_selection("req-overdue-target", now_iso=t0_iso)
    overdue_sid = overdue_sel["selection_id"]

    # At T0 + 25h: the tombstones are within tombstone window (expires at T0+24h, retention until T0+48h)
    # The overdue selection expired at T0+24h
    now_test = _iso(t0 + timedelta(hours=25))

    # Opportunistic sweep with limit=2 MUST NOT starve on the 3 clean tombstones!
    # It must select and recover the overdue selection.
    rs.opportunistic_sweep(limit=2, now_iso=now_test)

    overdue_checked = rs.get_selection(overdue_sid, perform_recovery=False)
    assert overdue_checked["state"] == "expired"


# ---------------------------------------------------------------------------
# Test 16: Lazy Recovery Releases Expired Claim Within Lifetime
# ---------------------------------------------------------------------------

def test_lazy_recovery_releases_expired_claim_within_lifetime(test_db):
    """Lazy recovery releases an expired unfinished claim while selection lifetime remains valid."""
    t0 = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    t0_iso = _iso(t0)

    sel = rs.create_selection("req-lazy-lease", now_iso=t0_iso)
    sid = sel["selection_id"]

    f = rs.reserve_file_slot(sid, "up-lazy-1", "doc.json", now_iso=t0_iso)
    rs.stage_file_chunk(sid, f["file_id"], b'{"valid": true}', now_iso=t0_iso)
    rs.finalize_staged_file(sid, f["file_id"], now_iso=t0_iso)

    manifest = rs.get_selection_manifest(sid)
    m_digest = rs.compute_manifest_digest(manifest)
    rs.save_preview(sid, expected_revision=1, preview_token="p_lazy", manifest_digest=m_digest, committable=True, now_iso=t0_iso)

    # Claim acquired with 300s lease
    claim = rs.acquire_commit_claim(sid, 1, "p_lazy", m_digest, lease_seconds=300, now_iso=t0_iso)
    assert claim.status == "acquired"
    assert claim.commit_token is not None

    # At T0 + 350s (< 24h lifetime, > 300s claim lease):
    t_check = _iso(t0 + timedelta(seconds=350))
    recovered = rs.get_selection(sid, now_iso=t_check)

    # Selection must be returned to open with claim released, but files still staged
    assert recovered["state"] == "open"
    assert recovered["claim_commit_token"] == None
    manifest_after = rs.get_selection_manifest(sid)
    assert len(manifest_after) == 1
    assert manifest_after[0]["status"] == "staged"


# ---------------------------------------------------------------------------
# Test 17: Outer Rollback Around remove_staged_file Preserves Bytes and State
# ---------------------------------------------------------------------------

def test_remove_staged_file_outer_rollback_preserves_bytes_and_state(test_db):
    """17. Outer rollback around remove_staged_file preserves file bytes, row, counters, revision, preview."""
    sel = rs.create_selection("req-rm-rollback")
    sid = sel["selection_id"]
    f = rs.reserve_file_slot(sid, "up-rm-1", "remove_me.json")
    fid = f["file_id"]
    payload = b'{"keep": "exact_bytes_on_rollback"}'
    rs.stage_file_chunk(sid, fid, payload)
    rs.finalize_staged_file(sid, fid)

    manifest_before = rs.get_selection_manifest(sid)
    assert len(manifest_before) == 1
    staged_path = Path(manifest_before[0]["staged_path"])
    assert staged_path.is_file()

    m_digest = rs.compute_manifest_digest(manifest_before)
    rs.save_preview(sid, expected_revision=1, preview_token="p_prev_rm", manifest_digest=m_digest, committable=True)

    before_sel = rs.get_selection(sid, perform_recovery=False)
    assert before_sel["selection_revision"] == 1
    assert before_sel["active_file_count"] == 1
    assert before_sel["total_reserved_bytes"] == len(payload)
    assert before_sel["preview_token"] == "p_prev_rm"

    # Outer transaction wraps remove_staged_file and deliberately rolls back
    try:
        with db.transaction():
            rs.remove_staged_file(sid, fid, expected_revision=1)
            raise RuntimeError("deliberate rollback in remove")
    except RuntimeError:
        pass

    # Database row and file row must be restored, and exact staged bytes must remain
    after_sel = rs.get_selection(sid, perform_recovery=False)
    assert after_sel["state"] == "open"
    assert after_sel["selection_revision"] == 1
    assert after_sel["active_file_count"] == 1
    assert after_sel["total_reserved_bytes"] == len(payload)
    assert after_sel["preview_token"] == "p_prev_rm"
    assert after_sel["preview_manifest_digest"] == m_digest

    file_row = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row["status"] == "staged"
    assert file_row["byte_count"] == len(payload)
    assert file_row["cleanup_state"] == "none"

    assert staged_path.is_file()
    assert staged_path.read_bytes() == payload


# ---------------------------------------------------------------------------
# Test 18: Successful remove_staged_file Commits Before Deletion & Retryable
# ---------------------------------------------------------------------------

def test_remove_staged_file_commits_before_deletion_and_cleanup_failure_retryable(test_db, monkeypatch):
    """18. Successful remove_staged_file commits transition first; cleanup failure is recorded and retryable without restoring to valid manifest."""
    sel = rs.create_selection("req-rm-commit-first")
    sid = sel["selection_id"]
    f = rs.reserve_file_slot(sid, "up-rm-2", "remove_retry.json")
    fid = f["file_id"]
    payload = b'{"remove": "me"}'
    rs.stage_file_chunk(sid, fid, payload)
    rs.finalize_staged_file(sid, fid)

    manifest_before = rs.get_selection_manifest(sid)
    staged_path = Path(manifest_before[0]["staged_path"])
    assert staged_path.is_file()

    # Make _safe_delete_file fail during remove_staged_file
    orig_delete = rs._safe_delete_file
    monkeypatch.setattr(rs, "_safe_delete_file", lambda p: (False, "Simulated remove delete failure"))

    # Call remove_staged_file
    rs.remove_staged_file(sid, fid, expected_revision=1)

    # Manifest / revision transition is durably committed
    after_sel = rs.get_selection(sid, perform_recovery=False)
    assert after_sel["selection_revision"] == 2
    assert after_sel["active_file_count"] == 0
    assert after_sel["total_reserved_bytes"] == 0
    assert after_sel["cleanup_state"] == "failed"
    assert "Simulated remove delete failure" in after_sel["cleanup_warning"]

    # File is removed, not in valid manifest
    manifest_after = rs.get_selection_manifest(sid)
    assert len(manifest_after) == 0

    file_row = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row["status"] == "removed"
    assert file_row["cleanup_state"] == "failed"
    assert "Simulated remove delete failure" in file_row["cleanup_warning"]

    # Bytes still exist because deletion failed
    assert staged_path.is_file()

    # Restore real deletion and run recovery
    monkeypatch.setattr(rs, "_safe_delete_file", orig_delete)
    recovered_sel = rs.recover_selection(sid)

    # File is now deleted
    assert not staged_path.is_file()

    # File remains status='removed', NOT restored to valid manifest
    manifest_final = rs.get_selection_manifest(sid)
    assert len(manifest_final) == 0

    file_row_after = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row_after["status"] == "removed"
    assert file_row_after["cleanup_state"] == "cleaned"
    assert file_row_after["cleanup_warning"] == ""

    # Selection warning cleared
    assert recovered_sel["cleanup_state"] == "none"
    assert recovered_sel["cleanup_warning"] == ""


# ---------------------------------------------------------------------------
# Test 19: Expired remove and target-choice Never Produce Corrupted Open State
# ---------------------------------------------------------------------------

def test_expired_remove_and_target_choice_preserve_bytes_and_state_on_exception(test_db):
    """19. Expired remove and target-choice calls never produce open/staged DB state with missing bytes after expected exception."""
    t0 = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    t0_iso = _iso(t0)

    # 1. Test remove_staged_file on expired selection
    sel1 = rs.create_selection("req-exp-rm", now_iso=t0_iso)
    sid1 = sel1["selection_id"]
    f1 = rs.reserve_file_slot(sid1, "up-exp-1", "f1.json", now_iso=t0_iso)
    fid1 = f1["file_id"]
    payload1 = b'{"bytes": "one"}'
    rs.stage_file_chunk(sid1, fid1, payload1, now_iso=t0_iso)
    rs.finalize_staged_file(sid1, fid1, now_iso=t0_iso)
    staged_path1 = Path(rs.get_selection_manifest(sid1)[0]["staged_path"])
    assert staged_path1.is_file()

    expired_iso = _iso(t0 + timedelta(hours=25))

    with pytest.raises(rs.SelectionExpiredError):
        rs.remove_staged_file(sid1, fid1, expected_revision=1, now_iso=expired_iso)

    # Bytes MUST NOT be deleted
    assert staged_path1.is_file()
    assert staged_path1.read_bytes() == payload1

    # Database state remains open/staged with exact bytes present
    raw_sel1 = rs.get_selection(sid1, perform_recovery=False)
    assert raw_sel1["state"] == "open"
    assert raw_sel1["active_file_count"] == 1
    raw_f1 = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid1)
    assert raw_f1["status"] == "staged"

    # 2. Test update_file_targets on expired selection
    sel2 = rs.create_selection("req-exp-target", now_iso=t0_iso)
    sid2 = sel2["selection_id"]
    f2 = rs.reserve_file_slot(sid2, "up-exp-2", "f2.json", now_iso=t0_iso)
    fid2 = f2["file_id"]
    payload2 = b'{"bytes": "two"}'
    rs.stage_file_chunk(sid2, fid2, payload2, now_iso=t0_iso)
    rs.finalize_staged_file(sid2, fid2, now_iso=t0_iso)
    staged_path2 = Path(rs.get_selection_manifest(sid2)[0]["staged_path"])
    assert staged_path2.is_file()

    with pytest.raises(rs.SelectionExpiredError):
        rs.update_file_targets(sid2, fid2, expected_revision=1, effective_library_key="new_lib", now_iso=expired_iso)

    # Bytes MUST NOT be deleted
    assert staged_path2.is_file()
    assert staged_path2.read_bytes() == payload2

    raw_sel2 = rs.get_selection(sid2, perform_recovery=False)
    assert raw_sel2["state"] == "open"
    assert raw_sel2["active_file_count"] == 1
    raw_f2 = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid2)
    assert raw_f2["status"] == "staged"


# ---------------------------------------------------------------------------
# Test 20: Expired Claim Inside Outer Rollback Never Deletes Bytes
# ---------------------------------------------------------------------------

def test_expired_claim_in_outer_rollback_preserves_bytes(test_db):
    """20. Expired claim handling inside an outer rollback never deletes bytes while expiry is rolled back."""
    t0 = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    t0_iso = _iso(t0)

    sel = rs.create_selection("req-exp-claim-rb", now_iso=t0_iso)
    sid = sel["selection_id"]
    f = rs.reserve_file_slot(sid, "up-exp-claim-1", "doc.json", now_iso=t0_iso)
    fid = f["file_id"]
    payload = b'{"claim": "exact_bytes"}'
    rs.stage_file_chunk(sid, fid, payload, now_iso=t0_iso)
    rs.finalize_staged_file(sid, fid, now_iso=t0_iso)
    staged_path = Path(rs.get_selection_manifest(sid)[0]["staged_path"])
    assert staged_path.is_file()

    manifest = rs.get_selection_manifest(sid)
    m_digest = rs.compute_manifest_digest(manifest)
    rs.save_preview(sid, expected_revision=1, preview_token="p_exp_claim", manifest_digest=m_digest, committable=True, now_iso=t0_iso)

    expired_iso = _iso(t0 + timedelta(hours=25))

    # Outer transaction wraps acquire_commit_claim on expired selection, then rolls back
    try:
        with db.transaction():
            res = rs.acquire_commit_claim(
                sid,
                expected_revision=1,
                preview_token="p_exp_claim",
                manifest_digest=m_digest,
                now_iso=expired_iso,
            )
            assert res.status == "expired"
            raise RuntimeError("deliberate rollback after expired claim")
    except RuntimeError:
        pass

    # Database state rolled back to open / none
    raw_sel = rs.get_selection(sid, perform_recovery=False)
    assert raw_sel["state"] == "open"
    assert raw_sel["cleanup_state"] == "none"

    # Staged bytes MUST NOT be deleted
    assert staged_path.is_file()
    assert staged_path.read_bytes() == payload

    raw_f = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert raw_f["status"] == "staged"


# ---------------------------------------------------------------------------
# Test 21: Repaired Expiry Reaches Durable Expired and Cleans With Retry
# ---------------------------------------------------------------------------

def test_repaired_expiry_reaches_durable_expired_and_cleans_with_retry(test_db, monkeypatch):
    """21. Repaired expiry path reaches durable expired plus post-commit cleanup through recovery, with safe warning and retry."""
    t0 = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)
    t0_iso = _iso(t0)

    sel = rs.create_selection("req-exp-durable", now_iso=t0_iso)
    sid = sel["selection_id"]
    f = rs.reserve_file_slot(sid, "up-exp-dur-1", "data.json", now_iso=t0_iso)
    fid = f["file_id"]
    payload = b'{"durable": "data"}'
    rs.stage_file_chunk(sid, fid, payload, now_iso=t0_iso)
    rs.finalize_staged_file(sid, fid, now_iso=t0_iso)
    staged_path = Path(rs.get_selection_manifest(sid)[0]["staged_path"])
    assert staged_path.is_file()

    expired_iso = _iso(t0 + timedelta(hours=25))

    # 1. Attempt remove on expired selection -> raises SelectionExpiredError without deleting
    with pytest.raises(rs.SelectionExpiredError):
        rs.remove_staged_file(sid, fid, expected_revision=1, now_iso=expired_iso)
    assert staged_path.is_file()

    # 2. Simulate cleanup failure during recovery
    orig_delete = rs._safe_delete_file
    monkeypatch.setattr(rs, "_safe_delete_file", lambda p: (False, "Simulated expiry delete failure"))

    # Record-local recovery runs
    recovered = rs.recover_selection(sid, now_iso=expired_iso)
    assert recovered["state"] == "expired"
    assert recovered["cleanup_state"] == "failed"
    assert "Simulated expiry delete failure" in recovered["cleanup_warning"]
    assert staged_path.is_file()

    # 3. Restore real deletion, and retry via recovery
    monkeypatch.setattr(rs, "_safe_delete_file", orig_delete)
    recovered_retry = rs.recover_selection(sid, now_iso=expired_iso)
    assert recovered_retry["state"] == "expired"
    assert recovered_retry["cleanup_state"] == "cleaned"
    assert recovered_retry["cleanup_warning"] == ""
    assert not staged_path.is_file()


# ---------------------------------------------------------------------------
# Test 22: Pending Removal Crash Window - Startup Recovery Cleans Durable Bytes
# ---------------------------------------------------------------------------

def test_pending_removal_crash_window_startup_recovery_cleans_durable_bytes(test_db, monkeypatch):
    """22. Startup recovery cleans durable removed/pending files surviving a process crash after DB commit."""
    sel = rs.create_selection("req-crash-startup")
    sid = sel["selection_id"]
    f = rs.reserve_file_slot(sid, "up-crash-1", "crash1.json")
    fid = f["file_id"]
    payload = b'{"crash": "startup_recovery"}'
    rs.stage_file_chunk(sid, fid, payload)
    rs.finalize_staged_file(sid, fid)

    staged_path = Path(rs.get_selection_manifest(sid)[0]["staged_path"])
    assert staged_path.is_file()

    # Simulate process termination right after SQLite commit by capturing on_commit
    captured_callbacks = []
    orig_on_commit = db.on_commit
    monkeypatch.setattr(db, "on_commit", lambda cb: captured_callbacks.append(cb))

    rs.remove_staged_file(sid, fid, expected_revision=1)
    assert len(captured_callbacks) == 1

    # Restore on_commit without running the lost callback
    monkeypatch.setattr(db, "on_commit", orig_on_commit)

    # Assert durable state before recovery
    raw_sel = rs.get_selection(sid, perform_recovery=False)
    assert raw_sel["selection_revision"] == 2
    assert raw_sel["active_file_count"] == 0
    assert raw_sel["total_reserved_bytes"] == 0
    assert len(rs.get_selection_manifest(sid)) == 0

    file_row = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row["status"] == "removed"
    assert file_row["cleanup_state"] == "pending"

    assert staged_path.is_file()
    assert staged_path.read_bytes() == payload

    # Run startup recovery
    summary = rs.startup_recovery()
    assert summary["cleanup_retries"] >= 1

    # File bytes must be deleted
    assert not staged_path.is_file()

    # File remains status='removed', not restored to manifest
    assert len(rs.get_selection_manifest(sid)) == 0
    file_row_after = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row_after["status"] == "removed"
    assert file_row_after["cleanup_state"] == "cleaned"

    # Selection state preserved and warning clean
    after_sel = rs.get_selection(sid, perform_recovery=False)
    assert after_sel["selection_revision"] == 2
    assert after_sel["active_file_count"] == 0
    assert after_sel["cleanup_state"] == "none"
    assert after_sel["cleanup_warning"] == ""
# ---------------------------------------------------------------------------
# Test 23: Pending Removal Crash Window - Record-Local Recovery Cleans Bytes
# ---------------------------------------------------------------------------

def test_pending_removal_crash_window_record_local_recovery_cleans_durable_bytes(test_db, monkeypatch):
    """23. Record-local recovery cleans durable removed/pending files surviving a process crash after DB commit."""
    sel = rs.create_selection("req-crash-local")
    sid = sel["selection_id"]
    f = rs.reserve_file_slot(sid, "up-crash-2", "crash2.json")
    fid = f["file_id"]
    payload = b'{"crash": "record_local"}'
    rs.stage_file_chunk(sid, fid, payload)
    rs.finalize_staged_file(sid, fid)

    staged_path = Path(rs.get_selection_manifest(sid)[0]["staged_path"])
    assert staged_path.is_file()

    captured_callbacks = []
    orig_on_commit = db.on_commit
    monkeypatch.setattr(db, "on_commit", lambda cb: captured_callbacks.append(cb))

    rs.remove_staged_file(sid, fid, expected_revision=1)
    assert len(captured_callbacks) == 1

    monkeypatch.setattr(db, "on_commit", orig_on_commit)

    # Assert durable state before recovery
    raw_sel = rs.get_selection(sid, perform_recovery=False)
    assert raw_sel["selection_revision"] == 2
    assert raw_sel["active_file_count"] == 0
    assert len(rs.get_selection_manifest(sid)) == 0

    file_row = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row["status"] == "removed"
    assert file_row["cleanup_state"] == "pending"
    assert staged_path.is_file()

    # Run record-local recovery
    recovered_sel = rs.recover_selection(sid)

    # File bytes deleted and status cleaned
    assert not staged_path.is_file()
    assert len(rs.get_selection_manifest(sid)) == 0
    file_row_after = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row_after["status"] == "removed"
    assert file_row_after["cleanup_state"] == "cleaned"

    assert recovered_sel["selection_revision"] == 2
    assert recovered_sel["active_file_count"] == 0
    assert recovered_sel["cleanup_state"] == "none"
    assert recovered_sel["cleanup_warning"] == ""


# ---------------------------------------------------------------------------
# Test 24: Pending Removal Crash Window - Opportunistic Sweep Selects & Cleans
# ---------------------------------------------------------------------------

def test_pending_removal_crash_window_opportunistic_sweep_selects_and_cleans(test_db, monkeypatch):
    """24. Opportunistic sweep selects open parent with pending removed file and cleans it within configured bound."""
    sel = rs.create_selection("req-crash-sweep")
    sid = sel["selection_id"]
    f = rs.reserve_file_slot(sid, "up-crash-3", "crash3.json")
    fid = f["file_id"]
    payload = b'{"crash": "opportunistic_sweep"}'
    rs.stage_file_chunk(sid, fid, payload)
    rs.finalize_staged_file(sid, fid)

    staged_path = Path(rs.get_selection_manifest(sid)[0]["staged_path"])
    assert staged_path.is_file()

    captured_callbacks = []
    orig_on_commit = db.on_commit
    monkeypatch.setattr(db, "on_commit", lambda cb: captured_callbacks.append(cb))

    rs.remove_staged_file(sid, fid, expected_revision=1)
    assert len(captured_callbacks) == 1

    monkeypatch.setattr(db, "on_commit", orig_on_commit)

    # Assert durable state before sweep
    raw_sel = rs.get_selection(sid, perform_recovery=False)
    assert raw_sel["selection_revision"] == 2
    assert raw_sel["active_file_count"] == 0
    assert raw_sel["state"] == "open"
    assert raw_sel["cleanup_state"] == "none"

    file_row = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row["status"] == "removed"
    assert file_row["cleanup_state"] == "pending"
    assert staged_path.is_file()

    # Opportunistic sweep with limit=5 must select this open selection and clean it
    sweep_res = rs.opportunistic_sweep(limit=5)
    assert sweep_res["swept"] >= 1

    # Bytes deleted
    assert not staged_path.is_file()
    assert len(rs.get_selection_manifest(sid)) == 0

    file_row_after = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row_after["status"] == "removed"
    assert file_row_after["cleanup_state"] == "cleaned"

    after_sel = rs.get_selection(sid, perform_recovery=False)
    assert after_sel["selection_revision"] == 2
    assert after_sel["cleanup_state"] == "none"
    assert after_sel["cleanup_warning"] == ""


# ---------------------------------------------------------------------------
# Test 25: Pending Removal Cleanup Failure and Retry Preserves Invariants
# ---------------------------------------------------------------------------

def test_pending_removal_cleanup_failure_and_retry_preserves_manifest_and_revision(test_db, monkeypatch):
    """25. Simulated delete failure during pending removal recovery records warning, subsequent pass retries successfully without changing manifest or revision."""
    sel = rs.create_selection("req-crash-retry")
    sid = sel["selection_id"]
    f = rs.reserve_file_slot(sid, "up-crash-4", "crash4.json")
    fid = f["file_id"]
    payload = b'{"crash": "retry_failure"}'
    rs.stage_file_chunk(sid, fid, payload)
    rs.finalize_staged_file(sid, fid)

    staged_path = Path(rs.get_selection_manifest(sid)[0]["staged_path"])
    assert staged_path.is_file()

    captured_callbacks = []
    orig_on_commit = db.on_commit
    monkeypatch.setattr(db, "on_commit", lambda cb: captured_callbacks.append(cb))

    rs.remove_staged_file(sid, fid, expected_revision=1)
    monkeypatch.setattr(db, "on_commit", orig_on_commit)

    # 1. Simulate failure during first recovery pass
    orig_delete = rs._safe_delete_file
    monkeypatch.setattr(rs, "_safe_delete_file", lambda p: (False, "Simulated crash recovery failure"))

    recovered_fail = rs.recover_selection(sid)
    assert staged_path.is_file()

    file_row = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row["status"] == "removed"
    assert file_row["cleanup_state"] == "failed"
    assert "Simulated crash recovery failure" in file_row["cleanup_warning"]

    assert recovered_fail["selection_revision"] == 2
    assert recovered_fail["active_file_count"] == 0
    assert recovered_fail["cleanup_state"] == "failed"
    assert "Simulated crash recovery failure" in recovered_fail["cleanup_warning"]
    assert len(rs.get_selection_manifest(sid)) == 0

    # 2. Restore real deletion and run subsequent recovery pass
    monkeypatch.setattr(rs, "_safe_delete_file", orig_delete)

    # Opportunistic sweep (or recover_selection) can retry it
    sweep_res = rs.opportunistic_sweep(limit=5)
    assert sweep_res["swept"] >= 1

    assert not staged_path.is_file()
    assert len(rs.get_selection_manifest(sid)) == 0

    file_row_after = db.one("SELECT * FROM resource_selection_file WHERE file_id = ?", fid)
    assert file_row_after["status"] == "removed"
    assert file_row_after["cleanup_state"] == "cleaned"
    assert file_row_after["cleanup_warning"] == ""

    after_sel = rs.get_selection(sid, perform_recovery=False)
    assert after_sel["selection_revision"] == 2
    assert after_sel["active_file_count"] == 0
    assert after_sel["cleanup_state"] == "none"
    assert after_sel["cleanup_warning"] == ""
