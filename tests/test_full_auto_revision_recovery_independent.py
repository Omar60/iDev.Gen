"""Independent service-boundary probes for Full Auto revision recovery."""
from __future__ import annotations

import time
from datetime import timedelta

import pytest

import db
import main
import resource_store
from backend import resource_full_auto
from test_full_auto_conflict_recovery_journeys import (
    ControlledTransport,
    _all_rows,
    _connect,
    _control_body,
    _create_look,
    _payload,
    _publish,
    _ready_profile,
    _room_source,
    _start,
    _wait_for_state,
)
import test_task31_acceptance as task31


def test_changed_scope_resume_without_consent_has_no_persistent_effect(
    client, seeded, monkeypatch, tmp_path,
):
    providers = ControlledTransport(tmp_path / "provider-output", pause_call=1)
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, revision_id = _room_source()
    saved = _create_look(
        client, appearance="A quiet portrait in an invented studio.", wording=None,
    )
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=2),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]
    assert providers.entered.wait(timeout=10), "the first assistant response was not held"

    resource_store.update_translation(revision_id, {
        "label": "invented studio", "scene_theme": "A changed quiet studio.",
    })
    providers.release.set()
    stopped = _wait_for_state(client, session_id, run_id, {"blocked"})
    assert stopped["blocker"]["code"] == "dependency_changed"

    comparison_response = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert comparison_response.status_code == 200, comparison_response.text
    comparison = comparison_response.json()
    assert comparison["changed"] is True

    before = _all_rows()
    body = _control_body(stopped)
    body.update({
        "scope_digest": comparison["scope_digest"],
        "comparison_issued_at": comparison["comparison_issued_at"],
        "accept_changed_scope": False,
    })
    refused = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )

    task31._assert_error(refused, 409, "authorization_scope_changed")
    assert _all_rows() == before
    assert len(providers.assistant_calls) == 1
    assert providers.comfy_prompts == []


def test_expired_real_claim_rejects_old_owner_write_and_late_response(
    client, seeded, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        resource_full_auto, "RUNNER_HEARTBEAT_INTERVAL", timedelta(hours=1),
    )
    providers = ControlledTransport(tmp_path / "provider-output", pause_call=1)
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, _revision_id = _room_source()
    saved = _create_look(
        client, appearance="A quiet portrait in an invented studio.", wording=None,
    )
    published = _publish(client, saved, _ready_profile(saved), 0)

    observed_claims = []
    real_claim_run = resource_full_auto.claim_run

    def observe_real_claim(run_id, **kwargs):
        claim = real_claim_run(run_id, **kwargs)
        if claim is not None:
            observed_claims.append(claim)
        return claim

    # Observe the actual worker claim without replacing its ownership checks.
    monkeypatch.setattr(resource_full_auto, "claim_run", observe_real_claim)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=2),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]
    try:
        assert providers.entered.wait(timeout=10), "the first assistant response was not held"
        old_owner = next(claim for claim in observed_claims if claim.run_id == run_id)
        db.run(
            "UPDATE full_auto_run SET lease_expires_at = ? WHERE run_id = ?",
            "2000-01-01T00:00:00+00:00", run_id,
        )
        replacement = real_claim_run(run_id)
        assert replacement.requires_reconciliation is True
        assert replacement.epoch == old_owner.epoch
        assert replacement.fencing_token == old_owner.fencing_token + 1

        before_stale_write = _all_rows()
        with pytest.raises(resource_full_auto.FullAutoRunError) as stale:
            resource_full_auto.advance_run(
                old_owner, "blocked", blocker_code="assistant_refused",
            )
        assert stale.value.code == "run_owner_stale"
        assert _all_rows() == before_stale_write

        run_before_late_response = dict(db.one(
            "SELECT * FROM full_auto_run WHERE run_id = ?", run_id,
        ))
        historical_before_late_response = {
            table: [dict(row) for row in db.q(query, run_id)]
            for table, query in {
                "prepared": "SELECT * FROM prepared_take WHERE session_id = "
                           "(SELECT session_id FROM full_auto_run WHERE run_id = ?)",
                "shots": "SELECT * FROM shot WHERE session_id = "
                         "(SELECT session_id FROM full_auto_run WHERE run_id = ?)",
                "children": "SELECT * FROM full_auto_prepare_child WHERE run_id = ?",
                "operations": "SELECT * FROM authoring_operation WHERE full_auto_run_id = ?",
                "receipts": "SELECT * FROM full_auto_submission_receipt WHERE run_id = ?",
            }.items()
        }
    finally:
        providers.release.set()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        reservation = db.one(
            "SELECT settled_at FROM full_auto_assistant_reservation "
            "WHERE run_id = ? ORDER BY reservation_id DESC LIMIT 1", run_id,
        )
        if reservation is not None and reservation["settled_at"] is not None:
            break
        time.sleep(0.01)
    else:
        pytest.fail("The held old-owner assistant dispatch did not settle.")

    assert dict(db.one("SELECT * FROM full_auto_run WHERE run_id = ?", run_id)) == run_before_late_response
    for table, query in {
        "prepared": "SELECT * FROM prepared_take WHERE session_id = "
                    "(SELECT session_id FROM full_auto_run WHERE run_id = ?)",
        "shots": "SELECT * FROM shot WHERE session_id = "
                 "(SELECT session_id FROM full_auto_run WHERE run_id = ?)",
        "children": "SELECT * FROM full_auto_prepare_child WHERE run_id = ?",
        "operations": "SELECT * FROM authoring_operation WHERE full_auto_run_id = ?",
        "receipts": "SELECT * FROM full_auto_submission_receipt WHERE run_id = ?",
    }.items():
        assert [dict(row) for row in db.q(query, run_id)] == historical_before_late_response[table]
    assert providers.assistant_reached == 1
    assert len(providers.assistant_calls) == 1
    assert providers.comfy_prompts == []


def test_feature_disablement_during_prefix_validation_rolls_back_resume_rebase(
    client, seeded, monkeypatch, tmp_path,
):
    providers = ControlledTransport(tmp_path / "provider-output", pause_call=2)
    _connect(client, seeded, monkeypatch, tmp_path, providers)
    anchor, revision_id = _room_source()
    saved = _create_look(
        client, appearance="A quiet portrait in an invented studio.", wording=None,
    )
    published = _publish(client, saved, _ready_profile(saved), 0)
    created, _request = _start(
        client, _payload(seeded, anchor, saved, published, photo_count=3),
    )
    session_id, run_id = created["session_id"], created["full_auto_run_id"]
    assert providers.entered.wait(timeout=10), "the second assistant response was not held"

    resource_store.update_translation(revision_id, {
        "label": "invented studio", "scene_theme": "A changed quiet studio.",
    })
    providers.release.set()
    stopped = _wait_for_state(client, session_id, run_id, {"blocked"})
    assert stopped["blocker"]["code"] == "dependency_changed"

    current = client.get(f"/api/sessions/{session_id}/full-auto/{run_id}").json()
    cancelled = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/cancel",
        json=_control_body(current),
    )
    assert cancelled.status_code in {200, 202}, cancelled.text
    cancelled_status = _wait_for_state(client, session_id, run_id, {"cancelled"})
    refreshed = client.post(
        f"/api/sessions/{session_id}/plan/refresh-resources",
        json={"expected_revision": 1},
    )
    assert refreshed.status_code == 200, refreshed.text
    comparison_response = client.get(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume-preflight",
    )
    assert comparison_response.status_code == 200, comparison_response.text
    comparison = comparison_response.json()
    assert comparison["changed"] is True

    original_verify = resource_full_auto._verified_prepared_prefix

    def disable_feature_after_validation(*args, **kwargs):
        result = original_verify(*args, **kwargs)
        monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", False)
        return result

    monkeypatch.setattr(
        resource_full_auto, "_verified_prepared_prefix", disable_feature_after_validation,
    )
    # Keep the real HTTP control transaction isolated from the later worker
    # dispatch; the assertion concerns the atomic Resume boundary itself.
    monkeypatch.setattr(main, "_schedule_full_auto_run", lambda *_args, **_kwargs: None)
    before = _all_rows()
    body = _control_body(cancelled_status)
    body.update({
        "expected_plan_revision": refreshed.json()["plan_revision"],
        "scope_digest": comparison["scope_digest"],
        "comparison_issued_at": comparison["comparison_issued_at"],
        "accept_changed_scope": True,
    })
    response = client.post(
        f"/api/sessions/{session_id}/full-auto/{run_id}/resume", json=body,
    )
    after = _all_rows()
    changed_tables = sorted(table for table in before if before[table] != after[table])
    assert (
        response.status_code == 503
        and response.json().get("detail", {}).get("code") == "resource_planning_disabled"
        and after == before
    ), (
        "Resume must recheck feature availability after prepared-prefix validation "
        "and roll back the authorization, checkpoint, run, and action rows; "
        f"got HTTP {response.status_code}, response={response.text}, "
        f"changed_tables={changed_tables}"
    )
    assert providers.comfy_prompts == []
