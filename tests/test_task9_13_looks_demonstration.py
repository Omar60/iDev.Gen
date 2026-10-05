"""Task 9.13: real HTTP journeys from reusable look to linked shot history."""
from __future__ import annotations

import copy
from uuid import uuid4

import pytest

import db
import main
from backend import resource_store
from test_saved_look_application_acceptance import _apply, _create_look


APPEARANCE = "Short curls and soft studio makeup."
GARMENTS = [
    {"wording": "a wool jacket", "aside": ""},
    {"wording": "a pleated skirt", "aside": "the pleated skirt, moved aside"},
]
FULL = "She wears a wool jacket, and a pleated skirt."
PARTIAL = "She wears a pleated skirt."
BARE = "She wears nothing at all."


@pytest.fixture(autouse=True)
def _explicit_generation_only(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)

    def unexpected(*_args, **_kwargs):
        pytest.fail("look authoring must not call an assistant or start generation")

    monkeypatch.setattr(main.runner, "start", unexpected)
    monkeypatch.setattr(main.comfy, "queue_prompt", unexpected)
    monkeypatch.setattr(main.enhance, "run_structured", unexpected)
    monkeypatch.setattr(main, "_schedule_shared_suggestion_operation", unexpected)


def _view(client, sid):
    response = client.get(f"/api/sessions/{sid}/plan")
    assert response.status_code == 200, response.text
    return response.json()


def _save(client, sid, view):
    response = client.post(f"/api/sessions/{sid}/plan", json={
        "expected_revision": view["plan_revision"], "plan": view["plan"],
    })
    assert response.status_code == 200, response.text
    return _view(client, sid)


def _session(client, seeded, count=5, *, scene="Soft window light falls across an empty studio."):
    library = f"task-9-13-rooms-{uuid4().hex}"
    library_id = resource_store.ensure_library(library, kind="rooms")
    rid = resource_store.record_revision(
        library_id, "scene-01", {"id": "scene-01", "label": "invented scene", "scene_theme": scene},
        translation={"label": "invented scene", "scene_theme": scene},
    )
    response = client.post("/api/sessions/guided", json={
        "request_id": str(uuid4()), "character_id": seeded["model_id"],
        "scene_anchor": {"library_key": library, "source_id": "scene-01",
                         "content_digest": resource_store.get_revision(revision_id=rid)["content_digest"]},
        "mode": "manual", "photo_count": count,
    })
    assert response.status_code == 201, response.text
    sid = response.json()["session_id"]
    view = _view(client, sid)
    for index, take in enumerate(view["plan"]["takes"], 1):
        take.update(camera="50mm", framing="three-quarter", expression="a quiet smile",
                    pose=f"standing beside studio marker {index}")
    return sid, _save(client, sid, view)


def _state(sid):
    """Compare complete persisted rows, including history and approval, on refusal."""
    return {
        table: db.q(f"SELECT * FROM {table} WHERE session_id = ? ORDER BY rowid", sid)
        for table in (
            "session_plan", "prepared_take", "take_resource_adaptation",
            "session_plan_approval", "authoring_operation", "shot",
        )
    }


def _preview(client, sid, revision, *, end="take-005"):
    return client.post(f"/api/sessions/{sid}/plan/wardrobe-progression/preview", json={
        "expected_revision": revision, "start_take_id": "take-001", "end_take_id": end,
        "stage_indices": [0, 1, 3], "event_policy": "replace",
    })


def _apply_progression(client, sid, preview):
    response = client.post(f"/api/sessions/{sid}/plan/wardrobe-progression/apply", json={
        "expected_revision": preview["expected_revision"],
        "preview_token": preview["preview_token"], "review_digest": preview["review_digest"],
        "reviewed_wardrobes": preview["reviewed_wardrobes"],
    })
    assert response.status_code == 200, response.text
    return _view(client, sid)


def _prepare(client, sid, revision, take_ids):
    response = client.post(f"/api/sessions/{sid}/plan/preparations/prepare", json={
        "plan_revision": revision, "take_ids": take_ids,
    })
    assert response.status_code == 200, response.text
    return response.json()["prepared"]


def _assert_prompts(prepared, wardrobes, *, trigger="4da woman"):
    assert [row["take_id"] for row in prepared] == list(wardrobes)
    for row in prepared:
        prompt = row["final_prompt"]
        wardrobe = wardrobes[row["take_id"]]
        assert trigger in prompt
        assert APPEARANCE in prompt
        assert wardrobe in prompt
        for garment in GARMENTS:
            if garment["wording"] not in wardrobe:
                assert garment["wording"] not in prompt


def test_reused_look_progression_overrides_reorder_repeatable_prompts_and_linked_freeze(
    client, seeded,
):
    look = _create_look(client, "Reusable studio layers", APPEARANCE, GARMENTS)
    second_model = client.post("/api/models", json={
        "name": "invented second character", "lora_name": "characters/bea.safetensors",
        "trigger": "b3a woman", "base_positive": "studio photograph",
        "workflow_id": seeded["workflow_id"],
    })
    assert second_model.status_code == 200, second_model.text
    first, first_view = _session(client, seeded)
    second_scene = "Warm lamps light an empty brick workshop."
    second, second_view = _session(client, {**seeded, "model_id": second_model.json()["id"]}, scene=second_scene)
    original_anchors = [view["plan"]["authoring"]["scene_anchor"] for view in (first_view, second_view)]
    assert original_anchors[0] != original_anchors[1]

    for sid, view in ((first, first_view), (second, second_view)):
        applied = _apply(client, sid, look, view["plan_revision"])
        assert applied.status_code == 200, applied.text
    first_view, second_view = _view(client, first), _view(client, second)
    snapshot = copy.deepcopy(first_view["plan"]["authoring"]["look_snapshot"])
    assert second_view["plan"]["authoring"]["look_snapshot"] == snapshot
    for sid, view, anchor, model_id in (
        (first, first_view, original_anchors[0], seeded["model_id"]),
        (second, second_view, original_anchors[1], second_model.json()["id"]),
    ):
        assert db.one("SELECT model_id FROM session WHERE id = ?", sid)["model_id"] == model_id
        assert view["plan"]["authoring"]["scene_anchor"] == anchor
        assert view["plan"]["wardrobe_changes"] == []
        assert view["plan"]["authoring"]["wardrobe_progression"] is None
        assert view["plan"]["initial_wardrobe"] == FULL

    second_ids = [take["take_id"] for take in second_view["plan"]["takes"]]
    second_prepared = _prepare(client, second, second_view["plan_revision"], second_ids)
    _assert_prompts(second_prepared, dict.fromkeys(second_ids, FULL), trigger="b3a woman")
    assert all(second_scene in row["final_prompt"] for row in second_prepared)
    changed = client.post(f"/api/looks/{look['key']}/versions", json={
        "expected_version": look["version"], "name": "Later library clothing",
        "appearance": "A later appearance that must not replace the selected curls.",
        "garments": [{"wording": "a silver vest", "aside": ""}],
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == look["version"] + 1
    assert client.get(f"/api/looks/{look['key']}/versions/{look['version']}").json() == look
    for sid in (first, second):
        assert _view(client, sid)["plan"]["authoring"]["look_snapshot"] == snapshot
    assert _prepare(client, second, second_view["plan_revision"], second_ids) == second_prepared

    before = _state(first)
    too_short = _preview(client, first, first_view["plan_revision"], end="take-002")
    assert too_short.status_code == 422, too_short.text
    assert _state(first) == before
    preview_response = _preview(client, first, first_view["plan_revision"])
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["reviewed_wardrobes"] == [
        {"take_id": f"take-{index:03d}", "wardrobe": wardrobe}
        for index, wardrobe in enumerate((FULL, FULL, PARTIAL, PARTIAL, BARE), 1)
    ]
    assert preview["wardrobe_changes"] == [
        {"take_id": "take-003", "scope": "from_here", "wardrobe": PARTIAL},
        {"take_id": "take-005", "scope": "from_here", "wardrobe": BARE},
    ]
    assert _state(first) == before
    first_view = _apply_progression(client, first, preview)
    ids = [take["take_id"] for take in first_view["plan"]["takes"]]
    prepared = _prepare(client, first, first_view["plan_revision"], ids)
    _assert_prompts(prepared, dict(zip(ids, (FULL, FULL, PARTIAL, PARTIAL, BARE), strict=True)))
    assert all("Soft window light falls across an empty studio." in row["final_prompt"] for row in prepared)
    assert _prepare(client, first, first_view["plan_revision"], ids) == prepared

    local = "She wears a silk scarf."
    persistent = "She wears black shoes."
    first_view["plan"]["wardrobe_changes"].extend([
        {"take_id": "take-002", "scope": "this_take", "wardrobe": local},
        {"take_id": "take-004", "scope": "from_here", "wardrobe": persistent},
    ])
    first_view = _save(client, first, first_view)
    overrides = dict(zip(ids, (FULL, local, PARTIAL, persistent, BARE), strict=True))
    _assert_prompts(_prepare(client, first, first_view["plan_revision"], ids), overrides)
    replacement = _preview(client, first, first_view["plan_revision"])
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["reviewed_wardrobes"][1] == {"take_id": "take-002", "wardrobe": local}
    first_view = _apply_progression(client, first, replacement.json())
    assert {"take_id": "take-002", "scope": "this_take", "wardrobe": local} in first_view["plan"]["wardrobe_changes"]
    assert not any(event["take_id"] == "take-004" for event in first_view["plan"]["wardrobe_changes"])
    first_view["plan"]["wardrobe_changes"].append(
        {"take_id": "take-004", "scope": "from_here", "wardrobe": persistent},
    )
    first_view = _save(client, first, first_view)
    events = copy.deepcopy(first_view["plan"]["wardrobe_changes"])
    evidence = copy.deepcopy(first_view["plan"]["authoring"]["wardrobe_progression"])
    first_view["plan"]["takes"] = [first_view["plan"]["takes"][i] for i in (0, 4, 1, 2, 3)]
    first_view = _save(client, first, first_view)
    assert first_view["plan"]["wardrobe_changes"] == events
    assert first_view["plan"]["authoring"]["wardrobe_progression"] == evidence
    reordered = dict(zip(("take-001", "take-005", "take-002", "take-003", "take-004"),
                         (FULL, BARE, local, PARTIAL, persistent), strict=True))
    prepared = _prepare(client, first, first_view["plan_revision"], list(reordered))
    _assert_prompts(prepared, reordered)
    assert _prepare(client, first, first_view["plan_revision"], list(reordered)) == prepared
    assert _state(first)["shot"] == _state(second)["shot"] == []
    assert _state(first)["authoring_operation"] == _state(second)["authoring_operation"] == []

    revision = first_view["plan_revision"]
    refused = client.post(f"/api/sessions/{first}/plan/preparations/submit", json={
        "plan_revision": revision, "take_id": "take-001",
    })
    assert refused.status_code == 409, refused.text
    assert _state(first)["shot"] == []
    approved = client.post(f"/api/sessions/{first}/plan/review/approve", json={"plan_revision": revision})
    assert approved.status_code == 200, approved.text
    assert _state(first)["shot"] == []
    submitted = client.post(f"/api/sessions/{first}/plan/preparations/submit", json={
        "plan_revision": revision, "take_id": "take-001",
    })
    assert submitted.status_code == 200, submitted.text
    shot_id = submitted.json()["shot_id"]
    linked = client.get(f"/api/sessions/{first}/plan/takes/take-001/review").json()["snapshot"]
    assert linked["status"] == "generated"
    assert linked["linked_shot_id"] == shot_id
    shot = db.one("SELECT * FROM shot WHERE id = ?", shot_id)
    assert shot["status"] == "pending"
    assert shot["prompt"] == linked["final_prompt"] == prepared[0]["final_prompt"]

    # Valid server-created alternatives exercise the entire snapshot freeze,
    # even with Keep preserving both effective shared strings.
    alternatives = [changed.json()]
    for name, body in (
        ("same content and another identity", {"outfit_key": look["outfit"]["outfit_key"]}),
        ("appearance only", {}),
        ("same wording and new outfit identities", {"garments": GARMENTS}),
        ("different garment order", {"garments": list(reversed(GARMENTS))}),
        ("different aside", {"garments": [GARMENTS[0], {**GARMENTS[1], "aside": "folded aside"}]}),
        ("different wording", {"garments": [GARMENTS[0], {**GARMENTS[1], "wording": "a linen skirt"}]}),
    ):
        response = client.post("/api/looks", json={"name": name, "appearance": APPEARANCE, **body})
        assert response.status_code == 200, response.text
        alternatives.append(response.json())
    renamed = client.post(f"/api/looks/{look['key']}/versions", json={
        "expected_version": changed.json()["version"], "name": "Same selected content, later version",
        "appearance": APPEARANCE, "outfit_key": look["outfit"]["outfit_key"],
    })
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["content_digest"] == look["content_digest"]
    alternatives.append(renamed.json())
    frozen = _state(first)
    for alternative in alternatives:
        decisions = {"look": "keep", "initial_wardrobe": "keep"}
        refused = _apply(client, first, alternative, revision, decisions=decisions)
        assert refused.status_code == 409, refused.text
        assert "continuity fields" in refused.text
        assert _state(first) == frozen

    # Canonical object-key echoes and a future scoped edit remain permitted.
    echo = _view(client, first)
    echo["plan"]["authoring"]["look_snapshot"] = dict(reversed(list(snapshot.items())))
    echo["plan"]["wardrobe_changes"][-1]["wardrobe"] = "She wears canvas shoes."
    _save(client, first, echo)
    assert _view(client, first)["plan"]["authoring"]["look_snapshot"] == snapshot
    assert db.one("SELECT * FROM shot WHERE id = ?", shot_id) == shot
    historical_row = next(row for row in frozen["prepared_take"] if row["id"] == linked["id"])
    assert db.one("SELECT * FROM prepared_take WHERE id = ?", linked["id"]) == historical_row


def test_source_clothing_requires_separate_reviewed_adaptation_before_preparation(client, seeded):
    look = _create_look(client, "Conflict review layers", APPEARANCE, GARMENTS)
    sid, view = _session(client, seeded)
    applied = _apply(client, sid, look, view["plan_revision"])
    assert applied.status_code == 200, applied.text
    view = _view(client, sid)
    preview = _preview(client, sid, view["plan_revision"])
    assert preview.status_code == 200, preview.text
    view = _apply_progression(client, sid, preview.json())

    library = f"task-9-13-fused-{uuid4().hex}"
    source = "She stands beside the studio window wearing a wool jacket and a pleated skirt."
    library_id = resource_store.ensure_library(library, kind="fused_scenes")
    rid = resource_store.record_revision(library_id, "scene-01", {"id": "scene-01", "prompt": source},
                                         translation={"prompt": source})
    original_revision = resource_store.get_revision(revision_id=rid)
    triple = {"library_key": library, "source_id": "scene-01", "content_digest": original_revision["content_digest"]}
    view["plan"]["selected_resources"].append(triple)
    view = _save(client, sid, view)
    revision = view["plan_revision"]
    review = client.get(f"/api/sessions/{sid}/plan/takes/take-003/review")
    assert review.status_code == 200, review.text
    marker = review.json()["conflicts"][0]
    assert marker["resource_value"] == source
    assert marker["effective_wardrobe"] == PARTIAL
    assert "jacket" in marker["conflicting_tokens"]
    assert "structural" in marker["message"]
    before = _state(sid)
    refused = client.post(f"/api/sessions/{sid}/plan/takes/take-003/prepare", json={"plan_revision": revision})
    assert refused.status_code == 422, refused.text
    assert _state(sid) == before

    adapted = "She stands beside the studio window wearing a pleated skirt."
    saved = client.post(f"/api/sessions/{sid}/plan/takes/take-003/adaptations", json={
        "plan_revision": revision, "adaptation": {**triple, "resource_field": "prompt", "adapted_value": adapted},
    })
    assert saved.status_code == 200, saved.text
    review = client.get(f"/api/sessions/{sid}/plan/takes/take-003/review").json()
    assert review["conflicts"] == []
    assert len(review["resolved_conflicts"]) == 1
    assert review["adaptations"][0]["source_value"] == source
    assert review["adaptations"][0]["adapted_value"] == adapted
    prepared = _prepare(client, sid, revision, ["take-003"])
    _assert_prompts(prepared, {"take-003": PARTIAL})
    assert adapted in prepared[0]["final_prompt"]
    assert _prepare(client, sid, revision, ["take-003"]) == prepared
    assert resource_store.get_revision(revision_id=rid) == original_revision
    persisted = _state(sid)
    assert len(persisted["take_resource_adaptation"]) == 1
    assert persisted["shot"] == persisted["authoring_operation"] == []
