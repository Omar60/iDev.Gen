import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

import db


LEASE_START = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _create_session(conn, name="session"):
    model_id = conn.execute(
        "INSERT INTO model (name, created_at) VALUES (?, 'now')", (f"model-{name}",)
    ).lastrowid
    return conn.execute(
        "INSERT INTO session (model_id, name, created_at) VALUES (?, ?, 'now')",
        (model_id, name),
    ).lastrowid


def _insert_operation(
    conn,
    session_id,
    *,
    operation_id="00000000-0000-0000-0000-000000000001",
    request_id="00000000-0000-0000-0000-000000000101",
    kind="prepare_takes",
    state="active",
    plan_revision=1,
    fencing_token=1,
    request_digest="a" * 64,
    requested_json='["take-1", "take-2"]',
    completed_json="[]",
    failed_item=None,
    error=None,
    remaining_json='["take-1", "take-2"]',
    result_json=None,
):
    lease = (
        db.authoring_operation_lease_deadline(LEASE_START)
        if state in {"active", "cancel_requested"}
        else None
    )
    return conn.execute(
        """INSERT INTO authoring_operation (
               operation_id, request_id, session_id, plan_revision, kind,
               request_digest, state, fencing_token, lease_expires_at,
               requested_json, completed_json, failed_item, error,
               remaining_json, result_json, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', 'updated')""",
        (
            operation_id,
            request_id,
            session_id,
            plan_revision,
            kind,
            request_digest,
            state,
            fencing_token,
            lease,
            requested_json,
            completed_json,
            failed_item,
            error,
            remaining_json,
            result_json,
        ),
    )


def test_operation_schema_is_additive_and_lease_deadline_is_ten_minutes(tmp_path):
    start = LEASE_START
    deadline = datetime.fromisoformat(db.authoring_operation_lease_deadline(start))
    assert deadline - start == timedelta(minutes=10)

    path = tmp_path / "before_authoring_operations.db"
    conn = db.connect(path)
    session_id = _create_session(conn)
    conn.execute("DROP TABLE authoring_operation")
    conn.commit()
    conn.close()

    upgraded = db.connect(path)
    assert upgraded.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='authoring_operation'"
    ).fetchone()["name"] == "authoring_operation"
    assert upgraded.execute("SELECT id FROM session WHERE id=?", (session_id,)).fetchone()

    index_sql = upgraded.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' "
        "AND name='ix_authoring_operation_nonterminal_session'"
    ).fetchone()["sql"]
    assert "active" in index_sql and "cancel_requested" in index_sql
    assert upgraded.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' "
        "AND name='authoring_operation_fencing_monotonic'"
    ).fetchone()
    assert "input_digest" in {
        row["name"] for row in upgraded.execute("PRAGMA table_info(authoring_operation)")
    }
    upgraded.close()


def test_operation_input_digest_migrates_additively_and_is_immutable(tmp_path):
    path = tmp_path / "operation_input_digest_migration.db"
    legacy = db.connect(path)
    session_id = _create_session(legacy)
    legacy.execute("DROP TRIGGER IF EXISTS authoring_operation_input_digest_immutable")
    legacy.execute("ALTER TABLE authoring_operation DROP COLUMN input_digest")
    legacy.commit()
    legacy.close()

    upgraded = db.connect(path)
    columns = {
        row["name"] for row in upgraded.execute("PRAGMA table_info(authoring_operation)")
    }
    assert "input_digest" in columns
    trigger = upgraded.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' "
        "AND name='authoring_operation_input_digest_immutable'"
    ).fetchone()
    assert trigger
    _insert_operation(upgraded, session_id)
    with pytest.raises(sqlite3.IntegrityError, match="input digest is immutable"):
        upgraded.execute(
            "UPDATE authoring_operation SET input_digest='b' WHERE operation_id=?",
            ("00000000-0000-0000-0000-000000000001",),
        )
    upgraded.close()


def test_operation_constraints_and_nonterminal_exclusion(tmp_path):
    conn = db.connect(tmp_path / "constraints.db")
    session_id = _create_session(conn)
    _insert_operation(conn, session_id)

    invalid = (
        {"state": "running"},
        {"kind": "prepare"},
        {"plan_revision": 0},
        {"fencing_token": 0},
    )
    for index, values in enumerate(invalid, start=1):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_operation(
                conn,
                session_id,
                operation_id=f"00000000-0000-0000-0000-{index:012d}",
                request_id=f"00000000-0000-0000-0001-{index:012d}",
                **values,
            )

    with pytest.raises(sqlite3.IntegrityError):
        _insert_operation(conn, session_id, operation_id="")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_operation(conn, session_id, request_id="")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE authoring_operation SET lease_expires_at=NULL WHERE operation_id=?",
            ("00000000-0000-0000-0000-000000000001",),
        )

    with pytest.raises(sqlite3.IntegrityError, match="fencing token cannot decrease"):
        conn.execute(
            "UPDATE authoring_operation SET fencing_token=0 WHERE operation_id=?",
            ("00000000-0000-0000-0000-000000000001",),
        )
    conn.execute(
        "UPDATE authoring_operation SET fencing_token=2 WHERE operation_id=?",
        ("00000000-0000-0000-0000-000000000001",),
    )

    with pytest.raises(sqlite3.IntegrityError):
        _insert_operation(
            conn,
            session_id,
            operation_id="00000000-0000-0000-0000-000000000002",
            request_id="00000000-0000-0000-0000-000000000102",
            kind="shared_suggestions",
            state="cancel_requested",
        )

    # A terminal operation releases the session claim, while retaining its
    # request identity so it cannot be replaced by a new operation.
    conn.execute(
        "UPDATE authoring_operation SET state='succeeded', lease_expires_at=NULL "
        "WHERE operation_id=?",
        ("00000000-0000-0000-0000-000000000001",),
    )
    _insert_operation(
        conn,
        session_id,
        operation_id="00000000-0000-0000-0000-000000000003",
        request_id="00000000-0000-0000-0000-000000000103",
    )

    other_session = _create_session(conn, "other")
    _insert_operation(
        conn,
        other_session,
        operation_id="00000000-0000-0000-0000-000000000004",
        request_id="00000000-0000-0000-0000-000000000101",
    )
    conn.close()


def test_concurrent_nonterminal_claims_for_one_session_have_one_winner(tmp_path):
    path = tmp_path / "concurrent.db"
    conn = db.connect(path)
    session_id = _create_session(conn)
    conn.commit()
    conn.close()

    barrier = threading.Barrier(2)
    outcomes = []

    def claim(operation_id, request_id, state):
        connection = sqlite3.connect(path, timeout=10)
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            barrier.wait(timeout=5)
            try:
                _insert_operation(
                    connection,
                    session_id,
                    operation_id=operation_id,
                    request_id=request_id,
                    kind="shared_suggestions",
                    state=state,
                    requested_json="[]",
                    remaining_json="[]",
                )
                connection.commit()
                outcomes.append("inserted")
            except sqlite3.IntegrityError:
                connection.rollback()
                outcomes.append("conflict")
        finally:
            connection.close()

    first = threading.Thread(
        target=claim,
        args=("00000000-0000-0000-0000-000000000011", "00000000-0000-0000-0001-000000000111", "active"),
    )
    second = threading.Thread(
        target=claim,
        args=("00000000-0000-0000-0000-000000000012", "00000000-0000-0000-0001-000000000112", "cancel_requested"),
    )
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive() and not second.is_alive()
    assert sorted(outcomes) == ["conflict", "inserted"]
    check = db.connect(path)
    assert check.execute(
        "SELECT COUNT(*) AS count FROM authoring_operation WHERE session_id=? "
        "AND state IN ('active', 'cancel_requested')",
        (session_id,),
    ).fetchone()["count"] == 1
    check.close()


def test_terminal_operation_evidence_survives_reopen_and_remains_unique(tmp_path):
    path = tmp_path / "terminal.db"
    conn = db.connect(path)
    session_id = _create_session(conn)
    operation_id = "00000000-0000-0000-0000-000000000021"
    request_id = "00000000-0000-0000-0001-000000000121"
    _insert_operation(conn, session_id, operation_id=operation_id, request_id=request_id)
    completed = '[{"take_id":"take-1","prepared_take_id":17}]'
    remaining = '["take-3"]'
    result = '{"prepared":true}'
    conn.execute(
        """UPDATE authoring_operation
           SET state='failed', lease_expires_at=NULL, completed_json=?,
               failed_item='take-2', error='Preparation failed',
               remaining_json=?, result_json=?, updated_at='finished'
           WHERE operation_id=?""",
        (completed, remaining, result, operation_id),
    )
    conn.commit()
    conn.close()

    reopened = db.connect(path)
    row = reopened.execute(
        "SELECT * FROM authoring_operation WHERE operation_id=?", (operation_id,)
    ).fetchone()
    assert row["request_id"] == request_id
    assert row["session_id"] == session_id
    assert row["plan_revision"] == 1
    assert row["kind"] == "prepare_takes"
    assert row["request_digest"] == "a" * 64
    assert row["state"] == "failed"
    assert row["fencing_token"] == 1
    assert row["lease_expires_at"] is None
    assert row["requested_json"] == '["take-1", "take-2"]'
    assert row["completed_json"] == completed
    assert row["failed_item"] == "take-2"
    assert row["error"] == "Preparation failed"
    assert row["remaining_json"] == remaining
    assert row["result_json"] == result
    assert row["created_at"] == "created"
    assert row["updated_at"] == "finished"

    with pytest.raises(sqlite3.IntegrityError):
        _insert_operation(
            reopened,
            session_id,
            operation_id="00000000-0000-0000-0000-000000000022",
            request_id=request_id,
        )
    reopened.close()


def test_terminal_operation_identity_cannot_be_rewritten_or_reused(tmp_path):
    conn = db.connect(tmp_path / "immutable_identity.db")
    session_id = _create_session(conn)
    other_session_id = _create_session(conn, "other")
    operation_id = "00000000-0000-0000-0000-000000000031"
    request_id = "00000000-0000-0000-0001-000000000131"
    _insert_operation(conn, session_id, operation_id=operation_id, request_id=request_id)
    conn.execute(
        "UPDATE authoring_operation SET state='failed', lease_expires_at=NULL, "
        "updated_at='failed' WHERE operation_id=?",
        (operation_id,),
    )

    rewrites = (
        ("operation_id", "00000000-0000-0000-0000-000000000032"),
        ("request_id", "00000000-0000-0000-0001-000000000132"),
        ("session_id", other_session_id),
        ("plan_revision", 2),
        ("kind", "shared_suggestions"),
        ("request_digest", "b" * 64),
        ("requested_json", '["different-take"]'),
        ("created_at", "rewritten"),
    )
    for column, value in rewrites:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="identity and request inputs are immutable",
        ):
            conn.execute(
                f"UPDATE authoring_operation SET {column}=? WHERE operation_id=?",
                (value, operation_id),
            )

    row = conn.execute(
        "SELECT * FROM authoring_operation WHERE operation_id=?", (operation_id,)
    ).fetchone()
    assert row["operation_id"] == operation_id
    assert row["request_id"] == request_id
    assert row["session_id"] == session_id
    assert row["plan_revision"] == 1
    assert row["kind"] == "prepare_takes"
    assert row["request_digest"] == "a" * 64
    assert row["requested_json"] == '["take-1", "take-2"]'
    assert row["created_at"] == "created"
    assert row["state"] == "failed"

    with pytest.raises(sqlite3.IntegrityError):
        _insert_operation(
            conn,
            session_id,
            operation_id="00000000-0000-0000-0000-000000000033",
            request_id=request_id,
            request_digest="b" * 64,
            requested_json='["different-take"]',
        )
    conn.close()
