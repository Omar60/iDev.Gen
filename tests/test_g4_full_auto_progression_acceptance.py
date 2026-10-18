"""Independent G4 API acceptance for canonical Full Auto stage materialization."""
from __future__ import annotations

import json
import uuid

import pytest

import db
import main
import resource_store
import session_plan
from test_automation_profile_readiness_independent import _profile, _publish


REGIONS = ("chest", "hips", "legs", "feet")
CREATION_TABLES = (
    "session",
    "session_plan",
    "session_profile_evidence",
    "session_profile_coverage_revision",
    "session_take_coverage_evidence",
    "prepared_take",
    "full_auto_run_receipt",
    "full_auto_run",
    "guided_session_request",
    "shot",
)
GARMENTS = [
    {"wording": "a blue coat", "aside": "the blue coat, folded over one arm"},
    {"wording": "a pleated skirt", "aside": "the pleated skirt, set aside"},
    {"wording": "a brass bracelet", "aside": "the brass bracelet, resting on a table"},
]


def _room_anchor() -> dict[str, str]:
    library_key = f"g4-room-{uuid.uuid4().hex}"
    source_id = "invented-studio"
    label = "Invented Studio"
    theme = "Soft daylight falls across an empty studio."
    library_id = resource_store.ensure_library(library_key, kind="rooms")
    revision_id = resource_store.record_revision(
        library_id,
        source_id,
        {"id": source_id, "label": label, "scene_theme": theme},
        translation={"label": label, "scene_theme": theme},
    )
    revision = resource_store.get_revision(revision_id=revision_id)
    assert revision is not None
    return {
        "library_key": library_key,
        "source_id": source_id,
        "content_digest": revision["content_digest"],
    }


def _saved_look(
    client, *, outfit: bool = True, progressive_bracelet: bool = False,
) -> tuple[dict, dict]:
    appearance = "Short curls and soft studio makeup."
    if progressive_bracelet:
        appearance += " She wears a brass bracelet."
    payload = {
        "name": f"G4 progression {uuid.uuid4().hex[:8]}",
        "appearance": appearance,
    }
    if outfit:
        payload["garments"] = GARMENTS
    response = client.post("/api/looks", json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    profile = _profile(saved)
    for states in profile["garment_coverage"].values():
        for fact in states.values():
            fact["regions"] = {region: "full" for region in REGIONS}
    if progressive_bracelet:
        progressive_clause = "She wears a brass bracelet."
        start = appearance.index(progressive_clause)
        permanent_source = profile["appearance_segments"][0]["provenance"]
        bracelet_key = saved["outfit"]["garments"][-1]["key"]
        profile["appearance_segments"] = [
            {
                "segment_id": "permanent-appearance",
                "start": 0,
                "end": start,
                "text": appearance[:start],
                "owner": "permanent",
                "provenance": dict(permanent_source),
            },
            {
                "segment_id": "progressive-bracelet-appearance",
                "start": start,
                "end": len(appearance),
                "text": appearance[start:],
                "owner": f"garment:{bracelet_key}",
                "provenance": dict(permanent_source),
            },
        ]
    published = _publish(saved, profile)
    return saved, published


def _configure_full_auto_workflow(client, seeded) -> None:
    workflow = db.one("SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"])
    response = client.post("/api/workflows", json={
        "name": f"g4-t2i-{uuid.uuid4().hex}",
        "graph": json.loads(workflow["graph"]),
        "kind": "t2i",
    })
    assert response.status_code == 200, response.text
    db.run(
        "UPDATE model SET workflow_id = ? WHERE id = ?",
        response.json()["id"], seeded["model_id"],
    )


def _full_auto_body(seeded, anchor, saved, published, photo_count: int) -> dict:
    return {
        "character_id": seeded["model_id"],
        "scene_anchor": anchor,
        "photo_count": photo_count,
        "saved_look": {
            "look_key": saved["key"],
            "version": saved["version"],
            "content_digest": saved["content_digest"],
        },
        "full_auto": {
            "authorization_version": 1,
            "generate_without_pauses": True,
            "profile_revision": published["profile_revision"],
            "profile_digest": published["profile_digest"],
            "progression": "all_stages_full_interval",
            "framing_intent": "vary",
        },
    }


def _creation_snapshot() -> dict[str, list[dict]]:
    return {table: db.q(f"SELECT * FROM {table} ORDER BY rowid") for table in CREATION_TABLES}


def _forbid_generation(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        pytest.fail("G4 API acceptance must not call a provider or start image generation")

    monkeypatch.setattr(main.enhance, "_request_completion", unexpected)
    monkeypatch.setattr(main.enhance, "run_structured", unexpected)
    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)


def _quote_and_create(client, body: dict) -> tuple[dict, dict]:
    quote_response = client.post(
        "/api/sessions/guided/full-auto/preflight", json=body,
    )
    assert quote_response.status_code == 200, quote_response.text
    quote = quote_response.json()
    request = {
        **body,
        "request_id": str(uuid.uuid4()),
        "full_auto": {
            **body["full_auto"],
            "scope_digest": quote["scope_digest"],
            "scope_issued_at": quote["scope_issued_at"],
        },
    }
    created = client.post("/api/sessions/guided", json=request)
    assert created.status_code == 201, created.text
    return quote, created.json()


@pytest.mark.parametrize("photo_count,expected_stage_counts", [(5, [1, 1, 1, 1, 1]), (8, [2, 2, 2, 1, 1])])
def test_exact_and_longer_full_auto_intervals_materialize_every_canonical_stage(
    client, seeded, monkeypatch, photo_count, expected_stage_counts,
):
    _forbid_generation(monkeypatch)
    _configure_full_auto_workflow(client, seeded)
    saved, published = _saved_look(client, progressive_bracelet=True)
    anchor = _room_anchor()
    body = _full_auto_body(seeded, anchor, saved, published, photo_count)

    quote, created = _quote_and_create(client, body)

    canonical = session_plan._saved_look_wardrobe_stages(saved["outfit"])
    assert canonical == [
        "She wears a blue coat, a pleated skirt, and a brass bracelet.",
        "She wears a pleated skirt, and a brass bracelet.",
        "She wears a brass bracelet.",
        "She wears the brass bracelet, resting on a table.",
        "She wears nothing at all.",
    ]
    assert len(canonical) == 5
    summary = quote["summary"]
    assert summary["saved_look"] == body["saved_look"]
    assert summary["automation_profile"] == {
        "revision": published["profile_revision"],
        "digest": published["profile_digest"],
        "ready": True,
    }
    stages = summary["progression"]["stages"]
    assert [stage["stage_index"] for stage in stages] == list(range(len(canonical)))
    assert [stage["take_count"] for stage in stages] == expected_stage_counts

    plan = created["plan"]
    assert plan["look"] == "Short curls and soft studio makeup. "
    assert "a brass bracelet" not in plan["look"]
    assert plan["authoring"]["look_snapshot"]["look_id"] == saved["key"]
    assert plan["authoring"]["look_snapshot"]["version"] == saved["version"]
    assert plan["authoring"]["look_snapshot"]["content_digest"] == saved["content_digest"]
    assert plan["authoring"]["look_snapshot"]["appearance"] == saved["appearance"]
    assert "a brass bracelet" in plan["authoring"]["look_snapshot"]["appearance"]
    assert plan["authoring"]["look_snapshot"]["outfit"] == saved["outfit"]
    assert plan["initial_wardrobe"] == canonical[0]
    assert plan["authoring"]["wardrobe_progression"]["stage_indices"] == list(range(5))

    expected_indices = [
        (take_index * (len(canonical) - 1)) // (photo_count - 1)
        for take_index in range(photo_count)
    ]
    assert list(session_plan.resolve_effective_wardrobes(plan).values()) == [
        canonical[index] for index in expected_indices
    ]
    assert [canonical[index] for index in expected_indices][-1] == "She wears nothing at all."
    assert [canonical[index] for index in expected_indices].index(
        "She wears the brass bracelet, resting on a table."
    ) < expected_indices.index(4)

    coverage_by_stage = {stage["stage_index"]: stage["coverage"] for stage in stages}
    assert [take["wardrobe_coverage"] for take in plan["takes"]] == [
        coverage_by_stage[index] for index in expected_indices
    ]
    assert stages[0]["coverage"] == "The chest is fully covered. The hips are fully covered. The legs are fully covered. The feet are fully covered."
    assert stages[-1]["coverage"] == "The chest is uncovered. The hips are uncovered. The legs are uncovered. The feet are uncovered."

    sid = created["session_id"]
    profile_evidence = db.one(
        "SELECT * FROM session_profile_evidence WHERE session_id = ? AND plan_revision = 1",
        sid,
    )
    assert profile_evidence["look_key"] == saved["key"]
    assert profile_evidence["look_version"] == saved["version"]
    assert profile_evidence["look_content_digest"] == saved["content_digest"]
    assert profile_evidence["profile_revision"] == published["profile_revision"]
    assert profile_evidence["profile_digest"] == published["profile_digest"]
    evidence_rows = db.q(
        "SELECT take_id, stage_index FROM session_take_coverage_evidence "
        "WHERE session_id = ? AND plan_revision = 1 ORDER BY take_id",
        sid,
    )
    assert [row["stage_index"] for row in evidence_rows] == expected_indices
    assert db.q("SELECT * FROM shot WHERE session_id = ?", sid) == []
    assert db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid) == []


def test_insufficient_full_auto_preflight_returns_minimum_without_allocating_work(
    client, seeded, monkeypatch,
):
    _forbid_generation(monkeypatch)
    _configure_full_auto_workflow(client, seeded)
    saved, published = _saved_look(client)
    anchor = _room_anchor()
    before = _creation_snapshot()

    response = client.post(
        "/api/sessions/guided/full-auto/preflight",
        json=_full_auto_body(seeded, anchor, saved, published, 4),
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "insufficient_stage_count"
    assert detail["current"] == {
        "required_count": 5,
        "available_count": 4,
        "manual_available": True,
        "remedy": "increase_photo_count_or_use_manual_flow",
    }
    assert _creation_snapshot() == before


def test_appearance_only_full_auto_keeps_one_constant_wardrobe_stage(client, seeded, monkeypatch):
    _forbid_generation(monkeypatch)
    _configure_full_auto_workflow(client, seeded)
    saved, published = _saved_look(client, outfit=False)
    anchor = _room_anchor()
    body = _full_auto_body(seeded, anchor, saved, published, 1)

    quote, created = _quote_and_create(client, body)

    assert quote["summary"]["progression"]["stages"] == [{
        "stage_index": 0,
        "first_take_id": "take-001",
        "last_take_id": "take-001",
        "take_count": 1,
        "coverage": "The chest is uncovered. The hips are uncovered. The legs are uncovered. The feet are uncovered.",
    }]
    plan = created["plan"]
    assert plan["initial_wardrobe"] == ""
    assert plan["wardrobe_changes"] == []
    assert plan["authoring"]["wardrobe_progression"] is None
    assert list(session_plan.resolve_effective_wardrobes(plan).values()) == [""]
    assert [take["wardrobe_coverage"] for take in plan["takes"]] == [
        quote["summary"]["progression"]["stages"][0]["coverage"],
    ]
