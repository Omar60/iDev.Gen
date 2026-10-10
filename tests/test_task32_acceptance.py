"""Independent acceptance for atomic, populated Full Auto creation."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
import threading
import uuid

import pytest

import db
import main
import resource_store
from backend import guided_sessions, saved_looks, session_plan
import test_task31_acceptance as task31


_CREATION_TABLES = {
    "session",
    "session_plan",
    "session_profile_evidence",
    "session_profile_coverage_revision",
    "session_take_coverage_evidence",
    "full_auto_run_receipt",
    "guided_session_request",
    "session_plan_approval",
    "prepared_take",
    "authoring_operation",
}
_REQUIRED_WRITE_TABLES = {
    "session",
    "session_plan",
    "session_profile_evidence",
    "session_profile_coverage_revision",
    "session_take_coverage_evidence",
    "full_auto_run_receipt",
    "guided_session_request",
}


def _authorized_payload(base: dict, consent: dict, request_id: str | None = None) -> dict:
    payload = deepcopy(base)
    payload["request_id"] = request_id or str(uuid.uuid4())
    payload["full_auto"].update({
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
    })
    return payload


def _tables_to_snapshot(extra=()) -> list[str]:
    names = {
        row["name"]
        for row in db.q(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    return sorted((_CREATION_TABLES | {name for name in names if name.startswith("full_auto")} | set(extra)) & names)


def _row_snapshot(tables) -> dict[str, list[str]]:
    return {
        table: sorted(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True)
            for row in db.q(f'SELECT * FROM "{table}"')
        )
        for table in tables
    }


def _mutation_target(sql: str) -> str | None:
    normalized = " ".join(sql.split())
    match = re.match(
        r"(?i)^(?:insert(?:\s+or\s+\w+)?\s+into|replace\s+into|update|delete\s+from)\s+[`\"\[]?([a-z_][a-z0-9_]*)",
        normalized,
    )
    return None if match is None else match.group(1).lower()


def _assert_complete_creation(response, payload, consent, saved, published) -> None:
    assert response.status_code in {200, 201}, response.text
    result = response.json()
    session_id = result["session_id"]
    run_id = result.get("full_auto_run_id")
    assert isinstance(run_id, str) and run_id
    assert result["plan_revision"] == 1

    plan_row = db.one("SELECT * FROM session_plan WHERE session_id = ?", session_id)
    assert plan_row is not None
    assert plan_row["plan_revision"] == 1
    plan = json.loads(plan_row["plan_json"])
    assert plan == result["plan"]
    assert plan["authoring"]["look_snapshot"] == task31._expected_snapshot(saved)

    application = saved_looks.get_reviewed_appearance_projection(
        saved["key"], saved["version"], published["profile_revision"],
        saved["content_digest"], published["profile_digest"],
    )
    assert plan["look"] == application["appearance"]
    assert plan["initial_wardrobe"] == session_plan.compose_saved_look_wardrobe(saved["outfit"])
    assert plan["authoring"]["shared_state"] == {
        "look": {
            "origin": "user" if application["appearance"] != saved["appearance"] else "saved_look",
            "evidence_id": None,
        },
        "initial_wardrobe": {"origin": "saved_look", "evidence_id": None},
    }

    takes = plan["takes"]
    take_ids = [take["take_id"] for take in takes]
    progression = plan["authoring"]["wardrobe_progression"]
    stages = session_plan._saved_look_wardrobe_stages(saved["outfit"])
    assert len(stages) == len(saved_looks._automation_profile_coverage_stage_states(saved))
    assert progression == {
        "source_look_digest": saved["content_digest"],
        "start_take_id": take_ids[0],
        "end_take_id": take_ids[-1],
        "stage_indices": list(range(len(stages))),
        "applied_revision": 1,
    }

    expected_wardrobes = session_plan.derive_saved_look_wardrobe_progression(
        saved["outfit"], plan["initial_wardrobe"], len(takes),
        stage_indices=progression["stage_indices"],
        interval_start=0,
        interval_end=len(takes) - 1,
    )
    effective_wardrobes = session_plan.resolve_effective_wardrobes(plan)
    assert [effective_wardrobes[take_id] for take_id in take_ids] == expected_wardrobes
    expected_changes = []
    inherited = expected_wardrobes[0]
    for take, wardrobe in zip(takes[1:], expected_wardrobes[1:]):
        if wardrobe != inherited:
            expected_changes.append({
                "take_id": take["take_id"],
                "scope": "from_here",
                "wardrobe": wardrobe,
            })
            inherited = wardrobe
    assert plan["wardrobe_changes"] == expected_changes

    profile_row = db.one(
        "SELECT * FROM session_profile_evidence WHERE session_id = ? AND plan_revision = 1",
        session_id,
    )
    assert profile_row is not None
    projection_digest = hashlib.sha256(application["appearance"].encode("utf-8")).hexdigest()
    assert profile_row["look_key"] == saved["key"]
    assert profile_row["look_version"] == saved["version"]
    assert profile_row["look_content_digest"] == saved["content_digest"]
    assert profile_row["profile_revision"] == published["profile_revision"]
    assert profile_row["profile_digest"] == published["profile_digest"]
    assert profile_row["appearance_projection"] == application["appearance"]
    assert profile_row["projection_digest"] == projection_digest

    coverage_revision = db.one(
        "SELECT * FROM session_profile_coverage_revision WHERE session_id = ? AND plan_revision = 1",
        session_id,
    )
    assert coverage_revision is not None
    assert coverage_revision["evidence_version"] == 1
    assert json.loads(coverage_revision["derived_take_ids_json"]) == sorted(take_ids)
    coverage_rows = db.q(
        "SELECT * FROM session_take_coverage_evidence WHERE session_id = ? AND plan_revision = 1 ORDER BY take_id",
        session_id,
    )
    assert [row["take_id"] for row in coverage_rows] == sorted(take_ids)
    stage_count = len(saved_looks._automation_profile_coverage_stage_states(saved))
    coverage_by_take = {row["take_id"]: row for row in coverage_rows}
    for index, take in enumerate(takes):
        row = coverage_by_take[take["take_id"]]
        stage_index = 0 if len(takes) == 1 else (index * (stage_count - 1)) // (len(takes) - 1)
        expected_coverage = saved_looks.compile_automation_profile_coverage(
            saved["key"], saved["version"], saved["content_digest"],
            published["profile_revision"], published["profile_digest"], stage_index,
        )
        assert take["wardrobe_coverage"] == expected_coverage
        assert row["stage_index"] == stage_index
        assert row["look_content_digest"] == saved["content_digest"]
        assert row["profile_revision"] == published["profile_revision"]
        assert row["profile_digest"] == published["profile_digest"]
        assert row["text_digest"] == hashlib.sha256(expected_coverage.encode("utf-8")).hexdigest()

    receipt = db.one("SELECT * FROM full_auto_run_receipt WHERE run_id = ?", run_id)
    assert receipt is not None
    assert receipt["session_id"] == session_id
    assert receipt["scope_digest"] == consent["scope_digest"]
    assert receipt["scope_issued_at"] == consent["scope_issued_at"]
    assert receipt["plan_digest"] == resource_store.canonical_digest(plan)
    assert len(receipt["dependencies_digest"]) == 64
    authorization = json.loads(receipt["authorization_json"])
    assert authorization == {
        "authorization_version": 1,
        "generate_without_pauses": True,
        "profile_revision": published["profile_revision"],
        "profile_digest": published["profile_digest"],
        "progression": "all_stages_full_interval",
        "framing_intent": payload["full_auto"]["framing_intent"],
        "scope_digest": consent["scope_digest"],
        "scope_issued_at": consent["scope_issued_at"],
        "authorization_expires_at": consent["summary"]["authorization_expires_at"],
        "summary": consent["summary"],
    }

    stored_request = db.one(
        "SELECT request_digest, response_json, session_id FROM guided_session_request WHERE request_id = ?",
        payload["request_id"],
    )
    assert stored_request is not None
    assert stored_request["session_id"] == session_id
    assert stored_request["response_json"].encode("utf-8") == response.content
    assert db.one("SELECT 1 FROM session_plan_approval WHERE session_id = ?", session_id) is None
    assert db.one("SELECT 1 FROM prepared_take WHERE session_id = ?", session_id) is None
    assert db.one("SELECT 1 FROM authoring_operation WHERE session_id = ?", session_id) is None


def test_full_auto_creation_commits_complete_first_revision_from_shared_candidates(
    client, seeded, monkeypatch,
):
    task31._forbid_external_work(monkeypatch)
    task31._install_t2i_workflow(client, seeded)
    saved, published, base = task31._full_auto_fixture(seeded)
    consent = task31._preflight(client, base)

    saved_look_calls = []
    progression_calls = []
    original_saved_look_candidate = session_plan.build_saved_look_application_candidate
    original_progression_candidate = session_plan.build_full_auto_wardrobe_progression_candidate

    def build_saved_look(*args, **kwargs):
        saved_look_calls.append((args, kwargs))
        return original_saved_look_candidate(*args, **kwargs)

    def build_progression(plan):
        progression_calls.append(plan)
        return original_progression_candidate(plan)

    monkeypatch.setattr(session_plan, "build_saved_look_application_candidate", build_saved_look)
    monkeypatch.setattr(session_plan, "build_full_auto_wardrobe_progression_candidate", build_progression)
    response, payload = task31._create_from_preflight(client, base, consent)

    assert response.status_code == 201, response.text
    assert len(saved_look_calls) == 1
    assert len(progression_calls) == 1
    _assert_complete_creation(response, payload, consent, saved, published)


def test_each_post_write_failure_rolls_back_full_creation_and_same_request_retries_once(
    client, seeded, monkeypatch,
):
    task31._forbid_external_work(monkeypatch)
    task31._install_t2i_workflow(client, seeded)
    saved, published, base = task31._full_auto_fixture(seeded)
    consent = task31._preflight(client, base)

    captured_mutations = []
    original_run = db.run

    def capture(sql, *args):
        result = original_run(sql, *args)
        if _mutation_target(sql) is not None:
            captured_mutations.append(sql)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(db, "run", capture)
        probe_response, _probe_payload = task31._create_from_preflight(client, base, consent)
    assert probe_response.status_code == 201, probe_response.text
    targets = [_mutation_target(sql) for sql in captured_mutations]
    assert _REQUIRED_WRITE_TABLES <= set(targets)
    assert targets.count("session_take_coverage_evidence") == base["photo_count"]
    tables = _tables_to_snapshot(targets)

    for failure_index in range(1, len(captured_mutations) + 1):
        payload = _authorized_payload(base, consent)
        before = _row_snapshot(tables)
        seen = []
        mutation_index = 0

        def fail_after_write(sql, *args):
            nonlocal mutation_index
            result = original_run(sql, *args)
            if _mutation_target(sql) is not None:
                mutation_index += 1
                if mutation_index == failure_index:
                    seen.append(sql)
                    raise RuntimeError("injected post-write failure")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(db, "run", fail_after_write)
            try:
                failed = client.post("/api/sessions/guided", json=payload)
            except RuntimeError as exc:
                assert "injected post-write failure" in str(exc)
            else:
                assert failed.status_code == 500, failed.text
        assert len(seen) == 1
        assert mutation_index == failure_index
        assert _row_snapshot(tables) == before

        retry = client.post("/api/sessions/guided", json=payload)
        assert retry.status_code == 201, retry.text
        stored = db.one(
            "SELECT response_json, session_id FROM guided_session_request WHERE request_id = ?",
            payload["request_id"],
        )
        assert stored is not None
        assert stored["response_json"].encode("utf-8") == retry.content
        assert stored["session_id"] == retry.json()["session_id"]
        after_retry = _row_snapshot(tables)
        expected_increments = {table: 0 for table in tables}
        expected_increments.update({
            "session": 1,
            "session_plan": 1,
            "session_profile_evidence": 1,
            "session_profile_coverage_revision": 1,
            "session_take_coverage_evidence": base["photo_count"],
            "full_auto_run_receipt": 1,
                "full_auto_run": 1,
            "guided_session_request": 1,
        })
        assert {
            table: len(after_retry[table]) - len(before[table])
            for table in tables
        } == expected_increments
        _assert_complete_creation(retry, payload, consent, saved, published)

        replay = client.post("/api/sessions/guided", json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.content == retry.content
        assert _row_snapshot(tables) == after_retry


def test_identical_full_auto_race_returns_one_complete_stored_creation(
    client, seeded, monkeypatch,
):
    task31._forbid_external_work(monkeypatch)
    task31._install_t2i_workflow(client, seeded)
    saved, published, base = task31._full_auto_fixture(seeded)
    consent = task31._preflight(client, base)
    payload = _authorized_payload(base, consent)
    before = _row_snapshot(_tables_to_snapshot())
    barrier = threading.Barrier(2)

    def post_once():
        barrier.wait(timeout=10)
        return client.post("/api/sessions/guided", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = [future.result(timeout=20) for future in (
            pool.submit(post_once), pool.submit(post_once),
        )]

    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].content == responses[1].content
    result = responses[0].json()
    assert responses[1].json() == result
    assert isinstance(result["session_id"], int)
    assert isinstance(result["full_auto_run_id"], str) and result["full_auto_run_id"]
    _assert_complete_creation(responses[0], payload, consent, saved, published)

    after = _row_snapshot(_tables_to_snapshot())
    assert len(after["session"]) == len(before["session"]) + 1
    assert len(after["session_plan"]) == len(before["session_plan"]) + 1
    assert len(after["guided_session_request"]) == len(before["guided_session_request"]) + 1
    assert len(after["full_auto_run_receipt"]) == len(before["full_auto_run_receipt"]) + 1
    assert len(after["session_profile_evidence"]) == len(before["session_profile_evidence"]) + 1
    assert len(after["session_profile_coverage_revision"]) == len(before["session_profile_coverage_revision"]) + 1
    assert len(after["session_take_coverage_evidence"]) == (
        len(before["session_take_coverage_evidence"]) + base["photo_count"]
    )
