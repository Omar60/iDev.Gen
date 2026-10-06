"""Opt-in checks against pinned historical source revisions.

Run with ``python -m pytest tests/historical_rollback.py -q``. These checks
need the local Git objects for both revisions, so the default suite does not
collect this module in shallow or source-only checkouts.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import textwrap

import pytest

import db
import main
from backup import backup_database
from test_authoring_operation_api import _create_guided_session


REPO_ROOT = Path(__file__).resolve().parents[1]
ROLLBACK_COMPATIBLE_SOURCE_REVISION = "45f3005188d59d0c508df55479d19156834627b4"
PRE_FLAG_SOURCE_REVISION = "89ef945c380a76ec85045a49f90671b8d7763366"


def _extract_git_revision(revision: str, destination: Path) -> Path:
    """Extract a local historical source tree without changing Git state."""
    destination.mkdir(parents=True)
    result = subprocess.run(
        ["git", "archive", "--format=tar", revision],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            assert not path.is_absolute() and ".." not in path.parts
            assert not member.issym() and not member.islnk()
        archive.extractall(destination)
    return destination


def _run_source_app(
    source_root: Path,
    data_dir: Path,
    config_path: Path,
    script: str,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("IDEVGEN_RESOURCE_PLANNING_ENABLED", None)
    env["IDEVGEN_CONFIG"] = str(config_path)
    env["IDEVGEN_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = os.pathsep.join((str(source_root / "backend"), str(source_root)))
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=source_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _rollback_rows(db_path: Path, session_id: int, plan_revision: int, take_id: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    plan = conn.execute(
        "SELECT * FROM session_plan WHERE session_id = ?", (session_id,),
    ).fetchone()
    snapshot = conn.execute(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        (session_id, plan_revision, take_id),
    ).fetchone()
    approval = conn.execute(
        "SELECT * FROM session_plan_approval WHERE session_id = ?", (session_id,),
    ).fetchone()
    assert plan is not None and snapshot is not None and approval is not None
    state = {
        "plan_row": dict(plan),
        "snapshot_row": dict(snapshot),
        "approval_row": dict(approval),
        "foreign_key_check": [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")],
        "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
    }
    conn.close()
    return state


def test_local_rollback_compatible_source_preserves_authoring_when_disabled(
    tmp_path: Path, client, seeded,
):
    """The pinned older source drops authoring in normalization but its live gate blocks saves."""
    session_id, revision = _create_guided_session(client, seeded, photo_count=1)
    plan_response = client.get(f"/api/sessions/{session_id}/plan")
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()["plan"]
    assert plan["authoring"]["schema_version"] == 1
    assert main.session_plan.validate_draft(plan)["authoring"] == plan["authoring"]
    take_id = plan["takes"][0]["take_id"]

    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": revision},
    )
    assert approved.status_code == 200, approved.text
    db.run(
        "INSERT INTO prepared_take "
        "(session_id, plan_revision, take_id, final_prompt, effective_state, "
        "mapping_version, compiler_version, provenance, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)",
        session_id,
        revision,
        take_id,
        "invented prepared prompt from the approved authoring plan",
        json.dumps({"camera": "invented eye-level camera", "framing": "waist up"}),
        "rollback-test-map-v1",
        "rollback-test-compiler-v1",
        json.dumps({"fixture": "representative ready snapshot"}),
        db.now(),
        db.now(),
    )
    expected = {
        "plan_row": dict(db.one("SELECT * FROM session_plan WHERE session_id = ?", session_id)),
        "snapshot_row": dict(db.one(
            "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
            session_id, revision, take_id,
        )),
        "approval_row": dict(db.one(
            "SELECT * FROM session_plan_approval WHERE session_id = ?", session_id,
        )),
    }
    assert expected["snapshot_row"]["status"] == "ready"
    assert expected["approval_row"]["plan_revision"] == revision

    source_root = _extract_git_revision(
        ROLLBACK_COMPATIBLE_SOURCE_REVISION,
        tmp_path / "rollback-compatible-source",
    )
    upgraded_backup = tmp_path / "verified-upgraded-backup.db"
    backup_database(main.DATA_DIR / "idevgen.db", upgraded_backup)
    backup_conn = sqlite3.connect(upgraded_backup)
    assert backup_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    backup_conn.close()

    historical_check = textwrap.dedent("""
        import json
        import os
        from fastapi.testclient import TestClient
        from backend import main, session_plan

        session_id = int(os.environ["TEST_SESSION_ID"])
        revision = int(os.environ["TEST_PLAN_REVISION"])
        incoming_plan = json.loads(os.environ["TEST_PLAN"])
        assert incoming_plan["authoring"]["schema_version"] == 1

        normalized = session_plan.validate_draft(incoming_plan)
        assert normalized["version"] == "resource-v1"
        assert "authoring" not in normalized

        calls = []
        validate_draft = session_plan.validate_draft
        def record_normalizer_call(*args, **kwargs):
            calls.append(True)
            return validate_draft(*args, **kwargs)
        session_plan.validate_draft = record_normalizer_call

        with TestClient(main.app) as client:
            assert main.is_resource_planning_enabled() is False
            config = client.get("/api/config")
            assert config.status_code == 200, config.text
            assert config.json()["resource_planning_enabled"] is bool(
                int(os.environ["TEST_PERSISTED_FLAG"])
            )
            inspectable = client.get(f"/api/sessions/{session_id}/plan")
            assert inspectable.status_code == 200, inspectable.text
            assert inspectable.json()["plan"] == incoming_plan
            assert inspectable.json()["plan"]["authoring"] == incoming_plan["authoring"]
            refused = client.post(
                f"/api/sessions/{session_id}/plan",
                json={"expected_revision": revision, "plan": incoming_plan},
            )
            assert refused.status_code == 503, refused.text
            assert calls == []
    """)

    for name, persisted_flag, env_override in (
        ("persisted-disabled", False, None),
        ("environment-disabled", True, "0"),
    ):
        data_dir = tmp_path / name
        data_dir.mkdir()
        shutil.copyfile(upgraded_backup, data_dir / "idevgen.db")
        config_path = tmp_path / f"{name}-config.json"
        config_path.write_text(json.dumps({
            "comfy_url": "http://127.0.0.1:8188",
            "data_dir": str(data_dir),
            "resource_planning_enabled": persisted_flag,
        }), encoding="utf-8")
        extra_env = {
            "TEST_SESSION_ID": str(session_id),
            "TEST_PLAN_REVISION": str(revision),
            "TEST_PLAN": json.dumps(plan, separators=(",", ":")),
            "TEST_PERSISTED_FLAG": "1" if persisted_flag else "0",
        }
        if env_override is not None:
            extra_env["IDEVGEN_RESOURCE_PLANNING_ENABLED"] = env_override
        completed = _run_source_app(
            source_root,
            data_dir,
            config_path,
            historical_check,
            extra_env=extra_env,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        after = _rollback_rows(data_dir / "idevgen.db", session_id, revision, take_id)
        assert after["plan_row"] == expected["plan_row"]
        assert after["snapshot_row"] == expected["snapshot_row"]
        assert after["approval_row"] == expected["approval_row"]
        assert after["foreign_key_check"] == []
        assert after["integrity_check"] == "ok"


def test_local_pre_flag_source_starts_from_verified_pre_upgrade_backup(tmp_path: Path):
    """The pinned pre-flag source is opened only on a restored legacy backup."""
    source_root = _extract_git_revision(
        PRE_FLAG_SOURCE_REVISION,
        tmp_path / "pre-flag-source",
    )
    source_data = tmp_path / "pre-upgrade-data"
    source_data.mkdir()
    source_config = tmp_path / "pre-upgrade-config.json"
    source_config.write_text(json.dumps({
        "comfy_url": "http://127.0.0.1:8188",
        "data_dir": str(source_data),
        "resource_planning_enabled": False,
    }), encoding="utf-8")
    create_legacy = textwrap.dedent("""
        from fastapi.testclient import TestClient
        from backend import main

        with TestClient(main.app) as client:
            model_id = main.db.run(
                "INSERT INTO model (name, trigger, created_at) VALUES (?, ?, ?)",
                "Rollback model", "invented trigger", "now",
            )
            main.db.run(
                "INSERT INTO session (model_id, name, created_at) VALUES (?, ?, ?)",
                model_id, "Pre-upgrade session", "now",
            )
    """)
    created = _run_source_app(source_root, source_data, source_config, create_legacy)
    assert created.returncode == 0, created.stdout + created.stderr

    verified_pre_upgrade_backup = tmp_path / "verified-pre-upgrade.db"
    backup_database(source_data / "idevgen.db", verified_pre_upgrade_backup)
    backup_conn = sqlite3.connect(verified_pre_upgrade_backup)
    assert backup_conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    tables = {row[0] for row in backup_conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'",
    )}
    assert "authoring_operation" not in tables
    backup_conn.close()

    restored_data = tmp_path / "restored-pre-upgrade-data"
    restored_data.mkdir()
    shutil.copyfile(verified_pre_upgrade_backup, restored_data / "idevgen.db")
    restored_config = tmp_path / "restored-pre-upgrade-config.json"
    restored_config.write_text(json.dumps({
        "comfy_url": "http://127.0.0.1:8188",
        "data_dir": str(restored_data),
        "resource_planning_enabled": False,
    }), encoding="utf-8")
    read_restored = textwrap.dedent("""
        from fastapi.testclient import TestClient
        from backend import main

        assert not hasattr(main, "is_resource_planning_enabled")
        with TestClient(main.app) as client:
            response = client.get("/api/sessions")
            assert response.status_code == 200, response.text
            row = main.db.one("SELECT name FROM session WHERE name = ?", "Pre-upgrade session")
            assert row["name"] == "Pre-upgrade session"
    """)
    opened = _run_source_app(source_root, restored_data, restored_config, read_restored)
    assert opened.returncode == 0, opened.stdout + opened.stderr

    reopened = sqlite3.connect(restored_data / "idevgen.db")
    assert reopened.execute(
        "SELECT name FROM session WHERE name = ?", ("Pre-upgrade session",),
    ).fetchone()[0] == "Pre-upgrade session"
    assert reopened.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    reopened.close()
