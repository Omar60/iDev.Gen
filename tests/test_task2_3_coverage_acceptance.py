"""Independent Task 2.3 acceptance probes for derived coverage evidence."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

import db
import main
import resource_preparation
import session_plan
from backend import authoring_operations, saved_looks
from test_authoring_operation_api import (
    _configure_assistant,
    _create_guided_session,
    _persist_response_for_state_test,
    _start_body,
)
from test_task13_appearance_ownership import (
    _apply_profile,
    _profile,
    _publish,
    _saved_bracelet_look,
)
from test_task9_13_looks_demonstration import (
    _apply_progression,
    _prepare,
    _save,
    _session,
    _view,
)


REGIONS = ("chest", "hips", "legs", "feet")
FULL_COVERAGE = (
    "The chest is fully covered. The hips are fully covered. "
    "The legs are fully covered. The feet are fully covered."
)


@pytest.fixture(autouse=True)
def _isolate_acceptance(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", lambda *_args: None)

    def unexpected(*_args, **_kwargs):
        pytest.fail("coverage acceptance must not call a provider or start generation")

    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)
    monkeypatch.setattr(main.enhance, "run_structured", unexpected)


def _ready_profile(saved):
    profile = _profile(saved, bracelet_owner="garment")
    provenance = profile["authored_provenance"]
    profile["baseline_coverage"] = {
        "regions": {region: "none" for region in REGIONS},
        "provenance": provenance,
    }
    for garment in saved["outfit"]["garments"]:
        entry = profile["garment_coverage"].setdefault(garment["key"], {
            "worn": {"regions": {}, "provenance": provenance},
        })
        entry["worn"]["regions"] = {
            region: "full" for region in REGIONS
        }
    profile["inventory_complete"]["value"] = True
    return profile


def _publish_revision(saved, profile, expected_revision=0):
    return saved_looks.publish_automation_profile(
        saved["key"], saved["version"], saved["content_digest"],
        expected_revision, profile,
    )


def _profiled_session(client, seeded, *, count=2, saved=None, profile=None, stage_indices=None):
    session_id, view = _session(client, seeded, count=count)
    saved = saved or _saved_bracelet_look()
    profile = profile or _ready_profile(saved)
    published = _publish_revision(saved, profile)
    applied = _apply_profile(session_id, view["plan_revision"], saved, published)
    view = _view(client, session_id)
    if stage_indices is not None:
        preview = client.post(
            f"/api/sessions/{session_id}/plan/wardrobe-progression/preview",
            json={
                "expected_revision": view["plan_revision"],
                "start_take_id": "take-001",
                "end_take_id": f"take-{count:03d}",
                "stage_indices": stage_indices,
                "event_policy": "replace",
            },
        )
        assert preview.status_code == 200, preview.text
        view = _apply_progression(client, session_id, preview.json())
    return session_id, saved, published, view, applied


def _persisted_state(session_id):
    return {
        table: db.q(
            f"SELECT * FROM {table} WHERE session_id = ? ORDER BY rowid",
            session_id,
        )
        for table in (
            "session_plan", "session_profile_evidence",
            "session_profile_coverage_revision",
            "session_take_coverage_evidence", "prepared_take",
            "take_resource_adaptation", "session_plan_approval",
            "authoring_operation", "shot",
        )
    }


def _as_dict(value):
    return json.loads(value) if isinstance(value, str) else value


def test_missing_derived_coverage_row_refuses_preparation_without_writes(client, seeded):
    session_id, _saved, _published, view, _applied = _profiled_session(
        client, seeded, count=2, stage_indices=[0, 1],
    )
    db.run(
        "DELETE FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, view["plan_revision"], "take-001",
    )
    before = _persisted_state(session_id)

    response = client.post(
        f"/api/sessions/{session_id}/plan/takes/take-001/prepare",
        json={"plan_revision": view["plan_revision"]},
    )

    assert 400 <= response.status_code < 500, response.text
    assert _persisted_state(session_id) == before


def test_extra_derived_coverage_row_refuses_preparation_without_writes(client, seeded):
    session_id, _saved, _published, view, _applied = _profiled_session(
        client, seeded, count=2, stage_indices=[0, 1],
    )
    row = db.one(
        "SELECT * FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = ?",
        session_id, view["plan_revision"], "take-001",
    )
    db.run(
        """INSERT INTO session_take_coverage_evidence (
               session_id, plan_revision, take_id, look_key, look_version,
               look_content_digest, profile_revision, profile_digest,
               ownership_digest, stage_index, stage_digest, compiler_version,
               text_digest, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        row["session_id"], row["plan_revision"], "take-999", row["look_key"],
        row["look_version"], row["look_content_digest"], row["profile_revision"],
        row["profile_digest"], row["ownership_digest"], row["stage_index"],
        row["stage_digest"], row["compiler_version"], row["text_digest"],
        row["created_at"],
    )
    before = _persisted_state(session_id)

    response = client.post(
        f"/api/sessions/{session_id}/plan/takes/take-001/prepare",
        json={"plan_revision": view["plan_revision"]},
    )

    assert 400 <= response.status_code < 500, response.text
    assert _persisted_state(session_id) == before


def test_forged_derived_take_contract_refuses_preparation_without_writes(client, seeded):
    session_id, _saved, _published, view, _applied = _profiled_session(
        client, seeded, count=2, stage_indices=[0, 1],
    )
    source_row = db.one(
        "SELECT * FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = 'take-001'",
        session_id, view["plan_revision"],
    )
    db.run(
        "DELETE FROM session_profile_coverage_revision "
        "WHERE session_id = ? AND plan_revision = ?",
        session_id, view["plan_revision"],
    )
    db.run(
        "INSERT INTO session_profile_coverage_revision "
        "(session_id, plan_revision, evidence_version, derived_take_ids_json, created_at) "
        "VALUES (?, ?, 1, ?, ?)",
        session_id, view["plan_revision"],
        '["take-001","take-002","take-forged"]', db.now(),
    )
    db.run(
        """INSERT INTO session_take_coverage_evidence (
               session_id, plan_revision, take_id, look_key, look_version,
               look_content_digest, profile_revision, profile_digest,
               ownership_digest, stage_index, stage_digest, compiler_version,
               text_digest, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        source_row["session_id"], source_row["plan_revision"], "take-forged",
        source_row["look_key"], source_row["look_version"], source_row["look_content_digest"],
        source_row["profile_revision"], source_row["profile_digest"],
        source_row["ownership_digest"], source_row["stage_index"], source_row["stage_digest"],
        source_row["compiler_version"], source_row["text_digest"], source_row["created_at"],
    )
    before = _persisted_state(session_id)

    response = client.post(
        f"/api/sessions/{session_id}/plan/takes/take-001/prepare",
        json={"plan_revision": view["plan_revision"]},
    )

    assert 400 <= response.status_code < 500, response.text
    assert "profile coverage rows do not match their revision evidence contract" in response.text
    assert _persisted_state(session_id) == before


def test_corrupt_prepared_coverage_identity_blocks_approval_without_writes(client, seeded):
    session_id, _saved, _published, view, _applied = _profiled_session(
        client, seeded, count=2, stage_indices=[0, 1],
    )
    prepared = _prepare(
        client, session_id, view["plan_revision"], ["take-001", "take-002"],
    )
    first = prepared[0]
    provenance = _as_dict(first["provenance"])
    provenance["profile_coverage_evidence"]["derived_coverage"]["stage_digest"] = "0" * 64
    db.run(
        "UPDATE prepared_take SET provenance = ? WHERE id = ?",
        json.dumps(provenance, ensure_ascii=False, separators=(",", ":")),
        first["id"],
    )
    before = _persisted_state(session_id)

    response = client.post(
        f"/api/sessions/{session_id}/plan/review/approve",
        json={"plan_revision": view["plan_revision"]},
    )

    assert 400 <= response.status_code < 500, response.text
    assert _persisted_state(session_id) == before


def test_ownership_digest_changes_when_projection_and_compiled_text_do_not(client, seeded):
    saved = _saved_bracelet_look()
    first_profile = _ready_profile(saved)
    first_published = _publish_revision(saved, first_profile)
    session_a, plan_a = _session(client, seeded, count=1)
    _apply_profile(session_a, plan_a["plan_revision"], saved, first_published)

    second_profile = copy.deepcopy(first_profile)
    appearance = saved["appearance"]
    permanent, accessory = second_profile["appearance_segments"]
    cut = appearance.index(" in ")
    second_profile["appearance_segments"] = [
        {
            **permanent,
            "segment_id": "appearance-left",
            "end": cut,
            "text": appearance[:cut],
        },
        {
            **permanent,
            "segment_id": "appearance-right",
            "start": cut,
            "text": appearance[cut:permanent["end"]],
        },
        accessory,
    ]
    def set_revision(value):
        if isinstance(value, dict):
            if "accepted_revision" in value:
                value["accepted_revision"] = 2
            for child in value.values():
                set_revision(child)
        elif isinstance(value, list):
            for child in value:
                set_revision(child)

    set_revision(second_profile)
    second_published = _publish_revision(saved, second_profile, expected_revision=1)
    session_b, plan_b = _session(client, seeded, count=1)
    _apply_profile(session_b, plan_b["plan_revision"], saved, second_published)

    look_a = _view(client, session_a)["plan"]["look"]
    look_b = _view(client, session_b)["plan"]["look"]
    row_a = db.one(
        "SELECT * FROM session_take_coverage_evidence WHERE session_id = ?",
        session_a,
    )
    row_b = db.one(
        "SELECT * FROM session_take_coverage_evidence WHERE session_id = ?",
        session_b,
    )
    assert look_a == look_b
    assert row_a["text_digest"] == row_b["text_digest"]
    assert row_a["profile_digest"] != row_b["profile_digest"]
    assert row_a["ownership_digest"] != row_b["ownership_digest"]


def test_stage_identity_is_bound_when_adjacent_stages_compile_to_same_text(client, seeded):
    saved = saved_looks.create({
        "name": "Invented layered coverage look",
        "appearance": "A portrait in a quiet room. She wears a brass bracelet.",
        "garments": [
            {"wording": "a brass bracelet", "aside": ""},
            {"wording": "a cotton bodysuit", "aside": ""},
        ],
    })
    profile = _ready_profile(saved)
    bracelet, bodysuit = saved["outfit"]["garments"]
    profile["garment_coverage"][bracelet["key"]]["worn"]["regions"] = {
        region: "none" for region in REGIONS
    }
    profile["garment_coverage"][bodysuit["key"]] = {
        "worn": {
            "regions": {region: "full" for region in REGIONS},
            "provenance": profile["authored_provenance"],
        },
    }
    profile["garment_coverage"][bodysuit["key"]]["worn"]["regions"] = {
        region: "full" for region in REGIONS
    }
    session_id, _saved, _published, view, _applied = _profiled_session(
        client, seeded, count=3, saved=saved, profile=profile,
        stage_indices=[0, 1, 2],
    )
    plan_takes = {take["take_id"]: take for take in view["plan"]["takes"]}
    rows = db.q(
        "SELECT * FROM session_take_coverage_evidence WHERE session_id = ? "
        "AND plan_revision = ? ORDER BY take_id",
        session_id, view["plan_revision"],
    )
    assert plan_takes["take-001"]["wardrobe_coverage"] == FULL_COVERAGE
    assert plan_takes["take-002"]["wardrobe_coverage"] == FULL_COVERAGE
    assert rows[0]["text_digest"] == rows[1]["text_digest"]
    assert rows[0]["stage_index"] == 0
    assert rows[1]["stage_index"] == 1
    assert rows[0]["stage_digest"] != rows[1]["stage_digest"]


def test_reorder_and_progression_event_change_refresh_stable_take_stage_evidence(client, seeded):
    saved = saved_looks.create({
        "name": "Invented reordered coverage look",
        "appearance": "A portrait in a quiet room. She wears a brass bracelet.",
        "garments": [
            {"wording": "a brass bracelet", "aside": ""},
            {"wording": "a cotton bodysuit", "aside": ""},
        ],
    })
    session_id, _saved, _published, view, _applied = _profiled_session(
        client, seeded, count=3, saved=saved,
        profile=_ready_profile(saved), stage_indices=[0, 1, 2],
    )
    initial_rows = db.q(
        "SELECT take_id, stage_index FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ?",
        session_id, view["plan_revision"],
    )
    assert {row["take_id"]: row["stage_index"] for row in initial_rows} == {
        "take-001": 0, "take-002": 1, "take-003": 2,
    }

    by_id = {take["take_id"]: take for take in view["plan"]["takes"]}
    view["plan"]["takes"] = [by_id[key] for key in ("take-001", "take-003", "take-002")]
    reordered = _save(client, session_id, view)
    reordered_rows = db.q(
        "SELECT take_id, stage_index FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ?",
        session_id, reordered["plan_revision"],
    )
    assert {row["take_id"]: row["stage_index"] for row in reordered_rows} == {
        "take-001": 0, "take-002": 1, "take-003": 2,
    }

    preview = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/preview",
        json={
            "expected_revision": reordered["plan_revision"],
            "start_take_id": "take-001",
            "end_take_id": "take-002",
            "stage_indices": [0, 1, 2],
            "event_policy": "replace",
        },
    )
    assert preview.status_code == 200, preview.text
    assert [entry["take_id"] for entry in preview.json()["reviewed_wardrobes"]] == [
        "take-001", "take-003", "take-002",
    ]
    revised = _apply_progression(client, session_id, preview.json())
    stage_rows = db.q(
        "SELECT take_id, stage_index, stage_digest, text_digest "
        "FROM session_take_coverage_evidence WHERE session_id = ? AND plan_revision = ? "
        "ORDER BY take_id",
        session_id, revised["plan_revision"],
    )
    assert {row["take_id"]: row["stage_index"] for row in stage_rows} == {
        "take-001": 0, "take-002": 2, "take-003": 1,
    }
    compiled = {
        index: saved_looks.compile_automation_profile_coverage(
            saved["key"], saved["version"], saved["content_digest"],
            _published["profile_revision"], _published["profile_digest"], index,
        )
        for index in (0, 1, 2)
    }
    for row in stage_rows:
        assert row["text_digest"] == hashlib.sha256(
            compiled[row["stage_index"]].encode("utf-8"),
        ).hexdigest()
    assert stage_rows[0]["stage_digest"] != stage_rows[1]["stage_digest"]


def test_new_profile_identity_invalidates_old_ready_copy_with_same_projection_text(client, seeded):
    saved = _saved_bracelet_look()
    profile = _ready_profile(saved)
    published = _publish_revision(saved, profile)
    session_id, view = _session(client, seeded, count=1)
    _apply_profile(session_id, view["plan_revision"], saved, published)
    view = _view(client, session_id)
    old_prepared = _prepare(client, session_id, view["plan_revision"], ["take-001"])[0]
    old_profile_evidence = db.one(
        "SELECT * FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = 'take-001'",
        session_id, view["plan_revision"],
    )

    revised_profile = copy.deepcopy(profile)
    permanent, accessory = revised_profile["appearance_segments"]
    appearance = saved["appearance"]
    cut = appearance.index(" in ")
    revised_profile["appearance_segments"] = [
        {
            **permanent,
            "segment_id": "appearance-left",
            "end": cut,
            "text": appearance[:cut],
        },
        {
            **permanent,
            "segment_id": "appearance-right",
            "start": cut,
            "text": appearance[cut:permanent["end"]],
        },
        accessory,
    ]

    def advance_accepted_revision(value):
        if isinstance(value, dict):
            if "accepted_revision" in value:
                value["accepted_revision"] = 2
            for child in value.values():
                advance_accepted_revision(child)
        elif isinstance(value, list):
            for child in value:
                advance_accepted_revision(child)

    advance_accepted_revision(revised_profile)
    revised_published = _publish_revision(
        saved, revised_profile, expected_revision=published["profile_revision"],
    )
    assert revised_published["profile_digest"] != published["profile_digest"]
    assert saved_looks.permanent_appearance_projection(revised_published) == \
        saved_looks.permanent_appearance_projection(published)
    assert saved_looks.compile_automation_profile_coverage(
        saved["key"], saved["version"], saved["content_digest"],
        revised_published["profile_revision"], revised_published["profile_digest"], 0,
    ) == saved_looks.compile_automation_profile_coverage(
        saved["key"], saved["version"], saved["content_digest"],
        published["profile_revision"], published["profile_digest"], 0,
    )

    applied = _apply_profile(
        session_id, view["plan_revision"], saved, revised_published,
    )
    current = _view(client, session_id)
    assert applied["plan_revision"] == view["plan_revision"] + 1
    assert current["plan"]["look"] == view["plan"]["look"]
    assert db.one("SELECT * FROM prepared_take WHERE id = ?", old_prepared["id"])["status"] == "invalidated"
    assert db.q(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ?",
        session_id, applied["plan_revision"],
    ) == []
    current_profile_evidence = db.one(
        "SELECT * FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = 'take-001'",
        session_id, applied["plan_revision"],
    )
    assert current_profile_evidence["profile_digest"] == revised_published["profile_digest"]
    assert current_profile_evidence["profile_digest"] != old_profile_evidence["profile_digest"]
    assert current_profile_evidence["text_digest"] == old_profile_evidence["text_digest"]


def test_copy_input_digest_binds_profile_ownership_stage_compiler_and_text(client, seeded):
    session_id, _saved, _published, view, _applied = _profiled_session(
        client, seeded, count=2, stage_indices=[0, 1],
    )
    row = _prepare(client, session_id, view["plan_revision"], ["take-001"])[0]
    provenance = _as_dict(row["provenance"])
    original = resource_preparation._copy_input_digest(row, provenance)

    mutations = (
        ("profile_digest", "f" * 64),
        ("ownership_digest", "e" * 64),
        ("coverage_text_digest", "d" * 64),
    )
    for key, value in mutations:
        changed = copy.deepcopy(provenance)
        changed["profile_coverage_evidence"][key] = value
        assert resource_preparation._copy_input_digest(row, changed) != original

    for key, value in (("stage_digest", "c" * 64), ("compiler_version", "compiler-v2")):
        changed = copy.deepcopy(provenance)
        changed["profile_coverage_evidence"]["derived_coverage"][key] = value
        assert resource_preparation._copy_input_digest(row, changed) != original


def test_profile_free_historical_snapshot_still_copies_forward(client, seeded):
    session_id, view = _session(client, seeded, count=2)
    source = _prepare(client, session_id, view["plan_revision"], ["take-001"])[0]
    source = db.one("SELECT * FROM prepared_take WHERE id = ?", source["id"])
    source_provenance = json.loads(source["provenance"])
    assert "profile_coverage_evidence" not in source_provenance
    assert db.q("SELECT * FROM session_profile_evidence WHERE session_id = ?", session_id) == []
    old_input_digest = resource_preparation._copy_input_digest(source, source_provenance)

    view["plan"]["takes"][1]["wardrobe_coverage"] = "Her feet are bare."
    updated = _save(client, session_id, view)
    copied = db.one(
        "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ?",
        session_id, updated["plan_revision"],
    )

    copied_provenance = json.loads(copied["provenance"])
    assert copied["take_id"] == "take-001"
    assert copied["final_prompt"] == source["final_prompt"]
    assert copied_provenance["copy_forward"]["source_prepared_id"] == source["id"]
    assert "profile_coverage_evidence" not in copied_provenance
    assert resource_preparation._copy_input_digest(copied, copied_provenance) == old_input_digest
    resource_preparation.validate_authoring_prepared_evidence(
        session_id, updated["plan_revision"], "take-001",
    )


def test_coverage_revocation_fences_active_automatic_authoring_worker(
    client, seeded, monkeypatch,
):
    _configure_assistant(monkeypatch)
    session_id, _ = _create_guided_session(
        client, seeded, mode="automatic", photo_count=2,
    )
    view = _view(client, session_id)
    saved = _saved_bracelet_look()
    published = _publish_revision(saved, _ready_profile(saved))
    _apply_profile(session_id, view["plan_revision"], saved, published)
    view = _view(client, session_id)
    preview = client.post(
        f"/api/sessions/{session_id}/plan/wardrobe-progression/preview",
        json={
            "expected_revision": view["plan_revision"],
            "start_take_id": "take-001",
            "end_take_id": "take-002",
            "stage_indices": [0, 1],
            "event_policy": "replace",
        },
    )
    assert preview.status_code == 200, preview.text
    view = _apply_progression(client, session_id, preview.json())
    started = client.post(
        f"/api/sessions/{session_id}/plan/authoring/operations",
        json=_start_body(revision=view["plan_revision"], take_ids=["take-001", "take-002"]),
    )
    assert started.status_code == 202, started.text
    claim = authoring_operations.load_worker_claim(
        session_id, started.json()["operation_id"],
    )
    ticket = authoring_operations.renew_operation_lease(claim)

    view["plan"]["takes"][0]["wardrobe_coverage"] = "Her chest is bare."
    updated = _save(client, session_id, view)
    assert db.q(
        "SELECT take_id FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = ? ORDER BY take_id",
        session_id, updated["plan_revision"],
    ) == [{"take_id": "take-002"}]
    operation = db.one(
        "SELECT state, fencing_token FROM authoring_operation WHERE operation_id = ?",
        claim.operation_id,
    )
    assert operation["state"] == "cancelled"
    assert operation["fencing_token"] == claim.fencing_token + 1
    before = _persisted_state(session_id)

    with pytest.raises(authoring_operations.AuthoringOperationError) as exc_info:
        _persist_response_for_state_test(
            claim, ticket, [{"target": "take-001", "result": {
                "camera": "50mm", "framing": "full body",
                "pose": "beside a marker", "expression": "calm",
            }}],
        )

    assert exc_info.value.code == "authoring_owner_stale"
    assert _persisted_state(session_id) == before
    assert updated["plan_revision"] == view["plan_revision"] + 1
