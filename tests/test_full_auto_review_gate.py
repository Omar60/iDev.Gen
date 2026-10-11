"""Task 4.4 acceptance for Full Auto review and policy approval gates."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import db
import main
import test_full_auto_preparation_coordinator as preparation
from backend import resource_full_auto, resource_preparation, session_plan


@pytest.fixture(autouse=True)
def _configured_assistant(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "task44-test-provider")
    monkeypatch.setitem(main.CONFIG, "llm_key", "task44-test-secret")


def _prepare_full_auto(client, seeded, monkeypatch, *, photo_count=2, duplicate=False):
    session_id, run_id, revision, targets = preparation._create_run(
        client, seeded, photo_count=photo_count,
    )
    if duplicate:
        def duplicate_response(request, pending, calls, transaction_depths, fields_by_take, fail_take):
            assert request.url.path.endswith("/chat/completions")
            assert pending
            take_id = pending.pop(0)
            calls.append(take_id)
            transaction_depths.append(db._tx_depth)
            return json.dumps({field: f"same {field}" for field in fields_by_take[take_id]})

        monkeypatch.setattr(preparation, "_response_for_request", duplicate_response)
    owner = preparation._prepare_owned_batch(
        session_id, run_id, revision, targets, monkeypatch,
    )
    owner = resource_full_auto.renew_run_claim(owner)
    resource_full_auto.advance_run(owner, "active", phase="validate")
    return session_id, run_id, revision, targets, resource_full_auto.renew_run_claim(owner)


def test_full_auto_policy_approval_is_separate_and_submit_rechecks_linked_targets(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, targets, owner = _prepare_full_auto(
        client, seeded, monkeypatch, photo_count=4,
    )

    with pytest.raises(resource_full_auto.FullAutoRunError) as manual_error:
        session_plan.approve_plan_review(session_id, revision)
    assert manual_error.value.code == "full_auto_session_owned"
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?", session_id,
    ) is None

    review = client.get(f"/api/sessions/{session_id}/plan/review").json()
    assert review["full_auto_duplicate_review"]["state"] == "ready"
    view, owner = main._run_full_auto_review_and_approve(owner)
    assert view["state"] == "active"
    assert view["phase"] == "submit"
    assert owner is not None

    policy = db.one(
        "SELECT * FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    )
    assert policy is not None
    assert json.loads(policy["evidence_json"])["approval_kind"] == "policy"
    assert json.loads(policy["evidence_json"])["target_ids"] == targets
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?", session_id,
    ) is None
    assert session_plan.get_approved_plan_revision(session_id) is None

    owner = resource_full_auto.renew_run_claim(owner)
    first = session_plan.submit_prepared_take(
        session_id, revision, targets[0], _full_auto_claim=owner,
    )
    second = session_plan.submit_prepared_take(
        session_id, revision, targets[1], _full_auto_claim=owner,
    )
    assert first["shot_id"] != second["shot_id"]

    # A linked target remains in the aggregate gate for every new shot write.
    db.run(
        "UPDATE shot SET prompt = ? WHERE id = ?",
        "changed linked prompt", first["shot_id"],
    )
    with pytest.raises(resource_full_auto.FullAutoRunError) as linked_error:
        session_plan.submit_prepared_take(
            session_id, revision, targets[2], _full_auto_claim=owner,
        )
    assert linked_error.value.code == "linked_snapshot_invalid"
    assert len(db.q("SELECT id FROM shot WHERE session_id = ?", session_id)) == 2


def test_full_auto_duplicate_acceptance_binds_current_review_and_invalidates_on_edit(
    client, seeded, monkeypatch,
):
    session_id, run_id, revision, _targets, owner = _prepare_full_auto(
        client, seeded, monkeypatch, photo_count=4, duplicate=True,
    )
    validate_evidence = session_plan.validate_authoring_prepared_evidence

    def omit_frozen_duplicate_flags(*args, **kwargs):
        validated = validate_evidence(*args, **kwargs)
        evidence = dict(validated.authoring_evidence)
        evidence["duplicate_flags"] = {"status": "recorded", "flags": []}
        return SimpleNamespace(authoring_evidence=evidence)

    monkeypatch.setattr(
        session_plan, "validate_authoring_prepared_evidence", omit_frozen_duplicate_flags,
    )
    review = client.get(f"/api/sessions/{session_id}/plan/review").json()[
        "full_auto_duplicate_review"
    ]
    assert review["state"] == "duplicate_review_required"
    assert review["duplicate_acceptance_required"] is True
    assert len(review["duplicates"]) == 6

    view, next_owner = main._run_full_auto_review_and_approve(owner)
    assert view["state"] == "blocked"
    assert view["blocker_code"] == "duplicate_review_required"
    assert next_owner is None
    assert db.one(
        "SELECT run_id FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    ) is None
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?", session_id,
    ) is None
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []

    stale = client.post(
        f"/api/sessions/{session_id}/plan/review/duplicates/accept",
        json={"plan_revision": revision, "review_digest": "0" * 64},
    )
    assert stale.status_code == 409
    accepted = client.post(
        f"/api/sessions/{session_id}/plan/review/duplicates/accept",
        json={"plan_revision": revision, "review_digest": review["review_digest"]},
    )
    assert accepted.status_code == 200, accepted.text
    row = db.one(
        "SELECT plan_revision, target_digest, snapshot_digest, duplicate_set_digest, review_digest "
        "FROM full_auto_duplicate_review_acceptance WHERE run_id = ?",
        run_id,
    )
    assert row is not None
    assert row["plan_revision"] == revision
    assert row["target_digest"] == review["target_digest"]
    assert row["snapshot_digest"] == review["snapshot_digest"]
    assert row["duplicate_set_digest"] == review["duplicate_set_digest"]
    assert row["review_digest"] == review["review_digest"]

    db.run(
        "UPDATE prepared_take SET final_prompt = ? WHERE session_id = ? AND take_id = ?",
        "edited snapshot prompt", session_id, _targets[0],
    )
    changed = session_plan.get_full_auto_duplicate_review_state(session_id, revision)
    assert changed["duplicate_acceptance_current"] is False
    assert changed["state"] == "blocked"

    with db.transaction():
        session_plan.invalidate_plan_approval(session_id)
    assert db.one(
        "SELECT run_id FROM full_auto_duplicate_review_acceptance WHERE run_id = ?", run_id,
    ) is None


@pytest.mark.parametrize(
    ("review_field", "blocker"),
    [
        ("conflicts", "review_conflict"),
        ("stale_adaptations", "stale_adaptation"),
        ("unresolved_placeholders", "unresolved_placeholders"),
    ],
)
def test_full_auto_review_blockers_write_no_approval_or_shots(
    client, seeded, monkeypatch, review_field, blocker,
):
    session_id, run_id, _revision, _targets, owner = _prepare_full_auto(
        client, seeded, monkeypatch, photo_count=4,
    )
    build_review_state = resource_preparation.build_review_state

    def blocked_review(preparation_state, **kwargs):
        result = build_review_state(preparation_state, **kwargs)
        result[review_field] = [{"code": "current-review-blocker"}]
        return result

    monkeypatch.setattr(resource_preparation, "build_review_state", blocked_review)
    view, next_owner = main._run_full_auto_review_and_approve(owner)
    assert view["state"] == "blocked"
    assert view["blocker_code"] == blocker
    assert next_owner is None
    assert db.one(
        "SELECT run_id FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    ) is None
    assert db.one(
        "SELECT session_id FROM session_plan_approval WHERE session_id = ?", session_id,
    ) is None
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []


def test_full_auto_review_rejects_a_missing_target_snapshot_without_writes(
    client, seeded, monkeypatch,
):
    session_id, run_id, _revision, targets, owner = _prepare_full_auto(
        client, seeded, monkeypatch, photo_count=4,
    )
    db.run(
        "UPDATE prepared_take SET status = 'invalidated' WHERE session_id = ? AND take_id = ?",
        session_id, targets[-1],
    )
    view, next_owner = main._run_full_auto_review_and_approve(owner)
    assert view["state"] == "blocked"
    assert view["blocker_code"] == "full_auto_target_not_ready"
    assert next_owner is None
    assert db.one(
        "SELECT run_id FROM full_auto_plan_approval WHERE run_id = ?", run_id,
    ) is None
    assert db.q("SELECT id FROM shot WHERE session_id = ?", session_id) == []
