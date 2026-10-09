from __future__ import annotations

import db
import main
import pytest
import session_plan
from backend import saved_looks
from test_task13_appearance_ownership import (
    _apply_profile,
    _profile,
    _publish,
    _saved_bracelet_look,
    _state,
    _view,
)
from test_task9_13_looks_demonstration import _prepare, _session


@pytest.fixture(autouse=True)
def _keep_profile_probes_off_remote_services(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)

    def unexpected(*_args, **_kwargs):
        pytest.fail("appearance ownership verification must not call remote services")

    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)
    monkeypatch.setattr(main.enhance, "run_structured", unexpected)
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", unexpected)


def test_profile_free_saved_look_replay_keeps_the_linked_profile_binding(
    client, seeded,
):
    session_id, initial = _session(client, seeded, count=1, scene="An empty studio.")
    saved = _saved_bracelet_look()
    published = _publish(saved, _profile(saved, bracelet_owner="permanent"))
    applied = _apply_profile(session_id, initial["plan_revision"], saved, published)

    _prepare(client, session_id, applied["plan_revision"], ["take-001"])
    approved = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": applied["plan_revision"]},
    )
    assert approved.status_code == 200, approved.text
    submitted = client.post(f"/api/sessions/{session_id}/plan/preparations/submit", json={
        "plan_revision": applied["plan_revision"], "take_id": "take-001",
    })
    assert submitted.status_code == 200, submitted.text

    before = _state(session_id)
    try:
        result = session_plan.apply_saved_look(
            session_id, applied["plan_revision"], saved["key"], saved["version"],
            {"look": "replace", "initial_wardrobe": "replace"},
            planning_enabled=True,
        )
    except session_plan.PlanConstantsFrozenAfterGenerated:
        assert _state(session_id) == before
        return

    current = _view(client, session_id)
    assert result["plan_revision"] == current["plan_revision"]
    evidence = session_plan._load_session_profile_application(
        session_id, current["plan_revision"], current["plan"],
    )
    assert evidence is not None
    assert evidence["profile"]["profile_digest"] == published["profile_digest"]


def test_profile_free_changed_appearance_drops_only_the_active_binding(client, seeded):
    session_id, initial = _session(client, seeded, count=1, scene="An empty studio.")
    saved = _saved_bracelet_look()
    published = _publish(saved, _profile(saved, bracelet_owner="garment"))
    applied = _apply_profile(session_id, initial["plan_revision"], saved, published)

    result = session_plan.apply_saved_look(
        session_id, applied["plan_revision"], saved["key"], saved["version"],
        {"look": "replace", "initial_wardrobe": "replace"},
        planning_enabled=True,
    )

    current = _view(client, session_id)
    assert result["plan_revision"] == applied["plan_revision"] + 1
    assert current["plan"]["look"] == saved["appearance"]
    assert current["plan"]["authoring"]["shared_state"]["look"] == {
        "origin": "saved_look", "evidence_id": None,
    }
    assert db.one(
        "SELECT profile_digest FROM session_profile_evidence "
        "WHERE session_id = ? AND plan_revision = ?",
        session_id, result["plan_revision"],
    ) is None
    assert session_plan._load_session_profile_application(
        session_id, result["plan_revision"], current["plan"],
    ) is None
    assert db.one(
        "SELECT profile_digest FROM session_profile_evidence "
        "WHERE session_id = ? AND plan_revision = ?",
        session_id, applied["plan_revision"],
    )["profile_digest"] == published["profile_digest"]


def test_reviewed_projection_rejects_unbounded_look_version_before_sqlite_binding(client):
    with pytest.raises(saved_looks.SavedLookError) as raised:
        saved_looks.get_reviewed_appearance_projection(
            "invented-look", 1 << 63, 1, "a" * 64, "b" * 64,
        )
    assert raised.value.status_code == 422


def test_manual_profile_application_stale_cas_and_tampered_digest_write_nothing(
    client, seeded,
):
    session_id, initial = _session(client, seeded, count=1, scene="An empty studio.")
    saved = _saved_bracelet_look()
    published = _publish(saved, _profile(saved, bracelet_owner="garment"))
    before = _state(session_id)

    with pytest.raises(session_plan.PlanRevisionStale):
        _apply_profile(session_id, initial["plan_revision"] - 1, saved, published)
    assert _state(session_id) == before

    with pytest.raises(saved_looks.SavedLookError) as raised:
        session_plan.apply_saved_look(
            session_id, initial["plan_revision"], saved["key"], saved["version"],
            {"look": "replace", "initial_wardrobe": "replace"},
            planning_enabled=True,
            profile_revision=published["profile_revision"],
            profile_digest="f" * 64,
        )
    assert raised.value.status_code == 409
    assert _state(session_id) == before
