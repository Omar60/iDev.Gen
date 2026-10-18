"""Connected G5 acceptance from integrated look saving through FakeComfy."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from io import BytesIO
from uuid import uuid4

from PIL import Image
import pytest

import db
import main
import session_plan
from backend import resource_full_auto, saved_looks
from test_full_auto_preparation_coordinator import _install_provider
from test_task31_acceptance import (
    _create_from_preflight,
    _full_auto_base,
    _install_t2i_workflow,
    _preflight,
    _room_anchor,
)


REGIONS = ("chest", "hips", "legs", "feet")
_ORIGINAL_RUN_STRUCTURED = main.enhance.run_structured
APPEARANCE = "Short curls and studio makeup. She wears a silver ankle chain."
ACCESSORY_CLAUSE = "She wears a silver ankle chain."
GARMENTS = [
    {"key": None, "wording": "a navy jacket", "aside": ""},
    {"key": None, "wording": "a cream blouse", "aside": ""},
    {"key": None, "wording": "a woven wrap skirt", "aside": ""},
    {"key": None, "wording": "leather sandals", "aside": ""},
    {
        "key": None,
        "wording": "a silver ankle chain",
        "aside": "the silver ankle chain loosened, still draped around her ankle",
    },
]
PERMANENT_APPEARANCE = APPEARANCE[: APPEARANCE.index(ACCESSORY_CLAUSE)]
_OUTPUT_TABLES = (
    "session", "session_plan", "session_profile_evidence",
    "session_profile_coverage_revision", "session_take_coverage_evidence",
    "prepared_take", "full_auto_run_receipt", "full_auto_run",
    "guided_session_request", "shot", "full_auto_submission_receipt",
    "full_auto_execution_attempt",
)


@pytest.fixture(autouse=True)
def _enable_isolated_resource_flows(monkeypatch):
    monkeypatch.setitem(main.CONFIG, "resource_planning_enabled", True)
    monkeypatch.setitem(main.CONFIG, "llm_url", "https://assistant.example.invalid/v1")
    monkeypatch.setitem(main.CONFIG, "llm_model", "invented-g5-text-model")
    monkeypatch.setitem(main.CONFIG, "llm_vision_model", "invented-g5-vision-model")
    monkeypatch.setitem(main.CONFIG, "llm_key", "invented-g5-test-key")
    monkeypatch.delenv("IDEVGEN_RESOURCE_PLANNING_ENABLED", raising=False)


def _source_garments() -> list[dict]:
    return [
        {"key": item["key"], "wording": item["wording"], "aside": item["aside"]}
        for item in GARMENTS
    ]


def _reviewed_profile(*, baseline: str = "none") -> dict:
    source = saved_looks.canonicalize_prospective_automation_source(
        APPEARANCE, _source_garments(),
    )
    split = APPEARANCE.index(ACCESSORY_CLAUSE)
    segments = [
        {
            "segment_id": "permanent-appearance",
            "start": 0,
            "end": split,
            "text": APPEARANCE[:split],
            "owner": "permanent",
        },
        {
            "segment_id": "progressive-accessory-appearance",
            "start": split,
            "end": len(APPEARANCE),
            "text": APPEARANCE[split:],
            "owner": {"garment_index": 4},
        },
    ]
    region_states = [
        {"chest": "full", "hips": "none", "legs": "none", "feet": "none"},
        {"chest": "full", "hips": "none", "legs": "none", "feet": "none"},
        {"chest": "none", "hips": "full", "legs": "full", "feet": "none"},
        {"chest": "none", "hips": "none", "legs": "none", "feet": "full"},
        {"chest": "none", "hips": "none", "legs": "partial", "feet": "partial"},
    ]
    coverage = []
    for index, regions in enumerate(region_states):
        states = {"worn": {"regions": regions}}
        if GARMENTS[index]["aside"]:
            states["aside"] = {"regions": dict(regions)}
        coverage.append({"garment_index": index, "states": states})
    return {
        "schema_version": 1,
        "draft_content_digest": source["draft_content_digest"],
        "appearance_segments": segments,
        "garment_coverage": coverage,
        "baseline_coverage": {"regions": {region: baseline for region in REGIONS}},
        "inventory_complete": True,
    }


def _automation_companion(*, suggestion: dict | None = None, baseline: str = "none") -> dict:
    companion = {
        "profile": _reviewed_profile(baseline=baseline),
        "profile_review_confirmed": True,
        "inventory_complete_confirmed": True,
        "baseline_coverage_confirmed": True,
        "removal_order_confirmed": True,
    }
    if suggestion is not None:
        companion["suggestion"] = suggestion
    return companion


def _manual_save_body(*, request_id: str | None = None, baseline: str = "none") -> dict:
    return {
        "name": f"G5 layered look {uuid4().hex[:8]}",
        "appearance": APPEARANCE,
        "garments": deepcopy(GARMENTS),
        "request_id": request_id or str(uuid4()),
        "automation": _automation_companion(baseline=baseline),
    }


def _photo_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (12, 9), (71, 94, 122)).save(stream, format="PNG")
    return stream.getvalue()


def _save_reviewed_photo(client, monkeypatch) -> tuple[dict, dict]:
    candidate_appearance = APPEARANCE
    candidate = {
        "appearance": candidate_appearance,
        "garments": [item["wording"] for item in GARMENTS],
        "unresolved": [],
        "automation": {
            "schema_version": 1,
            "appearance_segments": [{
                "segment_id": "photo-appearance",
                "start": 0,
                "end": len(candidate_appearance),
                "text": candidate_appearance,
                "owner": None,
            }],
            "garment_coverage": [
                {"garment_index": index, "states": {
                    "worn": {"regions": {region: "unknown" for region in REGIONS}},
                }}
                for index in range(len(GARMENTS))
            ],
            "baseline_coverage": None,
            "inventory_complete": None,
        },
    }

    async def fake_photo_provider(_config, _prompt, image_uri, request_evidence=None):
        assert image_uri.startswith("data:image/png;base64,")
        assert not db.conn().in_transaction
        if request_evidence is not None:
            request_evidence.update({
                "messages": [
                    {"role": "system", "content": "Return one reviewed look candidate."},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Describe visible look details."},
                        {"type": "image_url", "image_url": {"url": image_uri}},
                    ]},
                ],
                "model": main.CONFIG["llm_vision_model"],
                "parameters": {"response_format": {"type": "json_object"}},
            })
        return deepcopy(candidate)

    monkeypatch.setattr(main.enhance, "run_structured", fake_photo_provider)
    upload = client.post(
        "/api/looks/photo-stages",
        files={"file": ("invented-g5-photo.png", _photo_bytes(), "image/png")},
    )
    assert upload.status_code == 201, upload.text
    photo_id = upload.json()["photo_id"]
    extracted = client.post(
        f"/api/looks/photo-stages/{photo_id}/extract",
        json={"include_automation": True},
    )
    assert extracted.status_code == 200, extracted.text
    proposal = extracted.json()
    assert proposal["appearance"] == candidate_appearance
    assert len(proposal["garments"]) == len(GARMENTS)
    automation_candidate = proposal["automation"]
    assert automation_candidate["status"] == "ready"

    # The provider leaves reusable facts unknown; the reviewed values below
    # complete ownership, coverage, baseline, aside wording, and order by hand.
    request_id = str(uuid4())
    final_garments = [
        {"wording": item["wording"], "aside": item["aside"]}
        for item in GARMENTS
    ]
    suggestion = {
        "proposal_id": automation_candidate["proposal_id"],
        "draft_content_digest": automation_candidate["draft_content_digest"],
    }
    review = {
        "proposal_id": proposal["proposal_id"],
        "name": f"G5 reviewed photo look {uuid4().hex[:8]}",
        "appearance": APPEARANCE,
        "garments": final_garments,
        "unresolved_decisions": [],
        "review_confirmed": True,
        "removal_order_confirmed": True,
        "request_id": request_id,
        "automation": _automation_companion(suggestion=suggestion),
    }
    saved = client.post(
        f"/api/looks/photo-stages/{photo_id}/save-extracted", json=review,
    )
    assert saved.status_code == 200, saved.text
    result = saved.json()
    repeated = client.post(
        f"/api/looks/photo-stages/{photo_id}/save-extracted", json=review,
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == result
    assert db.one(
        "SELECT COUNT(*) AS n FROM saved_look_combined_save_receipt WHERE request_id = ?",
        request_id,
    )["n"] == 1
    monkeypatch.setattr(main.enhance, "run_structured", _ORIGINAL_RUN_STRUCTURED)
    return result, {"photo_id": photo_id, "proposal": proposal}


def _materialized_counts() -> dict[str, int]:
    return {
        table: int(db.one(f"SELECT COUNT(*) AS n FROM {table}")["n"])
        for table in _OUTPUT_TABLES
    }


def _assert_profile_bound_to_saved_result(saved: dict) -> dict:
    identity = saved["automation_profile"]
    assert identity["look_key"] == saved["key"]
    assert identity["version"] == saved["version"] == 1
    assert identity["content_digest"] == saved["content_digest"]
    assert identity["profile_revision"] == 1
    assert identity["readiness"]["profile_ready"] is True
    profile = saved_looks.get_automation_profile(
        saved["key"], saved["version"], 1, saved["content_digest"],
        identity["profile_digest"],
    )
    assert profile is not None
    assert profile["look"] == {
        "key": saved["key"],
        "version": saved["version"],
        "content_digest": saved["content_digest"],
    }
    assert profile["authored_provenance"]["source_identity"] == "looks-integrated-review"
    assert "silver ankle chain" not in "".join(
        segment["text"] for segment in profile["appearance_segments"]
        if segment["owner"] == "permanent"
    ).lower()
    accessory_key = saved["outfit"]["garments"][4]["key"]
    accessory_segments = [
        segment for segment in profile["appearance_segments"]
        if segment["owner"] == f"garment:{accessory_key}"
    ]
    assert [segment["text"] for segment in accessory_segments] == [ACCESSORY_CLAUSE]
    assert set(profile["garment_coverage"]) == {item["key"] for item in saved["outfit"]["garments"]}
    assert [item["wording"] for item in saved["outfit"]["garments"]] == [
        item["wording"] for item in GARMENTS
    ]
    return profile


def _run_full_auto(client, seeded, saved, photo_count, monkeypatch, make_runner):
    _install_t2i_workflow(client, seeded)
    anchor = _room_anchor()
    base = _full_auto_base(
        seeded, anchor, saved, saved["automation_profile"], photo_count=photo_count,
    )
    canonical = session_plan._saved_look_wardrobe_stages(saved["outfit"])
    expected_stage_count = len(GARMENTS) + 2
    assert len(canonical) == expected_stage_count == 7
    before_preflight = _materialized_counts()
    too_short = _full_auto_base(
        seeded, anchor, saved, saved["automation_profile"],
        photo_count=expected_stage_count - 1,
    )
    refused = client.post("/api/sessions/guided/full-auto/preflight", json=too_short)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "insufficient_stage_count"
    assert refused.json()["detail"]["current"]["required_count"] == expected_stage_count
    assert _materialized_counts() == before_preflight
    quote = _preflight(client, base)
    assert _materialized_counts() == before_preflight
    assert quote["summary"]["saved_look"] == base["saved_look"]
    assert quote["summary"]["automation_profile"] == {
        "revision": saved["automation_profile"]["profile_revision"],
        "digest": saved["automation_profile"]["profile_digest"],
        "ready": True,
    }
    created, request = _create_from_preflight(client, base, quote)
    assert created.status_code == 201, created.text
    response = created.json()
    session_id = response["session_id"]
    run_id = response["full_auto_run_id"]
    assert response["plan"]["authoring"]["look_snapshot"]["look_id"] == saved["key"]
    assert response["plan"]["authoring"]["look_snapshot"]["version"] == saved["version"]
    assert response["plan"]["authoring"]["look_snapshot"]["content_digest"] == saved["content_digest"]
    assert db.one(
        "SELECT COUNT(*) AS n FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    )["n"] == 1
    authorization = json.loads(db.one(
        "SELECT authorization_json FROM full_auto_run_receipt WHERE run_id = ?", run_id,
    )["authorization_json"])
    assert authorization["generate_without_pauses"] is True

    replay = client.post("/api/sessions/guided", json=request)
    assert replay.status_code == 200, replay.text
    assert replay.json() == response
    assert db.one("SELECT COUNT(*) AS n FROM full_auto_run WHERE session_id = ?", session_id)["n"] == 1

    plan_row = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?", session_id,
    )
    revision = int(plan_row["plan_revision"])
    plan = json.loads(plan_row["plan_json"])
    take_ids = [take["take_id"] for take in plan["takes"]]
    assert canonical[-1] == "She wears nothing at all."
    assert GARMENTS[-1]["aside"] in canonical[-2]
    assert canonical[-2] != canonical[-1]
    for stage_index, stage in enumerate(canonical[:-2]):
        for garment_index, item in enumerate(GARMENTS):
            if garment_index >= stage_index:
                assert item["wording"] in stage
            else:
                assert item["wording"] not in stage
    assert "a navy jacket" in canonical[0] and "a cream blouse" in canonical[0]
    assert "a navy jacket" not in canonical[1] and "a cream blouse" in canonical[1]
    assert "a cream blouse" not in canonical[2]
    assert ACCESSORY_CLAUSE not in plan["look"]
    assert plan["look"].strip() == PERMANENT_APPEARANCE.strip()

    stage_indices = [
        (index * (len(canonical) - 1)) // (photo_count - 1)
        for index in range(photo_count)
    ]
    if photo_count == expected_stage_count:
        assert stage_indices == list(range(expected_stage_count))
    assert stage_indices[0] == 0 and stage_indices[-1] == expected_stage_count - 1
    assert set(stage_indices) == set(range(expected_stage_count))
    effective_wardrobes = session_plan.resolve_effective_wardrobes(plan)
    assert list(effective_wardrobes) == take_ids
    assert list(effective_wardrobes.values()) == [canonical[index] for index in stage_indices]
    coverage_by_stage = {
        item["stage_index"]: item["coverage"]
        for item in quote["summary"]["progression"]["stages"]
    }
    assert [take["wardrobe_coverage"] for take in plan["takes"]] == [
        coverage_by_stage[index] for index in stage_indices
    ]
    assert "uncovered" in coverage_by_stage[expected_stage_count - 1]

    provider = _install_provider(monkeypatch, session_id, revision, take_ids)
    prepared, owner = asyncio.run(main._run_full_auto_preparation(
        resource_full_auto.claim_run(run_id), _return_claim=True,
    ))
    assert (prepared["state"], prepared["phase"]) == ("active", "validate")
    assert owner is not None
    assert provider["calls"] == provider["dispatch_order"]
    assert not provider["pending"]
    assert provider["transaction_depths"] == [0] * len(provider["calls"])

    reviewed, owner = main._run_full_auto_review_and_approve(owner)
    assert (reviewed["state"], reviewed["phase"]) == ("active", "submit")
    assert owner is not None
    submitted, owner = main._run_full_auto_submission(owner)
    assert (submitted["state"], submitted["phase"]) == ("active", "execute")

    runner, fake_comfy = make_runner()

    async def execute_fake_comfy():
        runner.start(session_id, _full_auto_claim=owner)
        if runner._task is not None:
            await runner._task

    asyncio.run(execute_fake_comfy())
    assert fake_comfy.attempts == photo_count
    assert len(fake_comfy.graphs) == photo_count

    rows = db.q(
        "SELECT receipt.target_index, receipt.take_id, prepared.final_prompt, "
        "prepared.effective_state, shot.prompt, shot.status "
        "FROM full_auto_submission_receipt receipt "
        "JOIN prepared_take prepared ON prepared.id = receipt.prepared_take_id "
        "JOIN shot ON shot.id = receipt.shot_id "
        "WHERE receipt.run_id = ? ORDER BY receipt.target_index",
        run_id,
    )
    assert len(rows) == photo_count
    assert [row["take_id"] for row in rows] == take_ids
    assert all(row["status"] == "done" for row in rows)
    assert all(row["final_prompt"] == row["prompt"] for row in rows)
    for index, row in enumerate(rows):
        stage_index = stage_indices[index]
        wardrobe = canonical[stage_index]
        prompt = row["final_prompt"]
        effective_state = json.loads(row["effective_state"])
        assert effective_state["wardrobe"] == wardrobe
        assert wardrobe in prompt
        assert PERMANENT_APPEARANCE.strip() in prompt
        if stage_index == expected_stage_count - 1:
            assert all(item["wording"].lower() not in prompt.lower() for item in GARMENTS)
            assert all(item["aside"].lower() not in prompt.lower() for item in GARMENTS if item["aside"])
            assert "silver ankle chain" not in prompt.lower()
        elif stage_index == expected_stage_count - 2:
            assert GARMENTS[-1]["aside"] in prompt
        else:
            for garment_index, item in enumerate(GARMENTS):
                assert (item["wording"].lower() in prompt.lower()) is (garment_index >= stage_index)

    workflow = db.one("SELECT node_map FROM workflow WHERE id = ?", seeded["workflow_id"])
    positive_path = json.loads(workflow["node_map"])["positive"]
    node_id, _, input_name = positive_path.partition(".")
    input_name = input_name.removeprefix("inputs.")
    submitted_prompt_bytes = [
        graph[node_id]["inputs"][input_name] for graph in fake_comfy.graphs
    ]
    assert submitted_prompt_bytes == [row["final_prompt"] for row in rows]
    attempts = db.q(
        "SELECT attempt_id, filename_prefix, dispatch_state, completion_state "
        "FROM full_auto_execution_attempt WHERE run_id = ? ORDER BY shot_id", run_id,
    )
    assert len(attempts) == photo_count
    assert len({row["attempt_id"] for row in attempts}) == photo_count
    assert len({row["filename_prefix"] for row in attempts}) == photo_count
    assert all(
        row["dispatch_state"] == "acknowledged" and row["completion_state"] == "completed"
        for row in attempts
    )
    counts_before_replay = _materialized_counts()
    replay_after_execution = client.post("/api/sessions/guided", json=request)
    assert replay_after_execution.status_code == 200, replay_after_execution.text
    assert replay_after_execution.json() == response
    assert _materialized_counts() == counts_before_replay
    assert fake_comfy.attempts == photo_count
    return {
        "saved": saved,
        "quote": quote,
        "created": response,
        "canonical": canonical,
        "stage_indices": stage_indices,
        "prompts": [row["final_prompt"] for row in rows],
        "photo_count": photo_count,
    }


@pytest.mark.parametrize(
    ("source", "photo_count"),
    [("manual", 7), ("reviewed-photo", 13)],
    ids=("manual-exact-stage-count", "reviewed-photo-longer-interval"),
)
def test_integrated_look_profile_reaches_fake_comfy_with_exact_removal_arc(
    client, seeded, monkeypatch, make_runner, source, photo_count,
):
    if source == "manual":
        body = _manual_save_body()

        async def no_assistant_for_manual_save(*_args, **_kwargs):
            pytest.fail("A manually authored review must save without assistant assistance.")

        with monkeypatch.context() as manual_save_only:
            manual_save_only.setattr(main.enhance, "run_structured", no_assistant_for_manual_save)
            response = client.post("/api/looks", json=body)
        assert response.status_code == 200, response.text
        saved = response.json()
        replay = client.post("/api/looks", json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json() == saved
        assert db.one("SELECT COUNT(*) AS n FROM saved_look_version WHERE look_key = ?", saved["key"])["n"] == 1
        assert db.one(
            "SELECT COUNT(*) AS n FROM saved_look_automation_profile WHERE look_key = ?", saved["key"],
        )["n"] == 1
        assert db.one("SELECT COUNT(*) AS n FROM session")["n"] == 0
        assert db.one("SELECT COUNT(*) AS n FROM shot")["n"] == 0
    else:
        saved, _photo = _save_reviewed_photo(client, monkeypatch)

    _assert_profile_bound_to_saved_result(saved)
    result = _run_full_auto(client, seeded, saved, photo_count, monkeypatch, make_runner)
    assert result["saved"]["key"] == saved["key"]
    assert result["saved"]["content_digest"] == saved["content_digest"]
    assert len(result["canonical"]) == 7
    assert len(result["prompts"]) == photo_count


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [("unknown-coverage", "automation_profile_not_ready"),
     ("conflicting-baseline", "automation_profile_not_ready")],
)
def test_incomplete_integrated_review_is_a_zero_write_refusal(
    client, monkeypatch, case, expected_code,
):
    body = _manual_save_body()
    if case == "unknown-coverage":
        body["automation"]["profile"]["garment_coverage"][0]["states"]["worn"]["regions"]["chest"] = "unknown"
    else:
        body["automation"]["profile"]["baseline_coverage"]["regions"]["chest"] = "partial"
    before = {
        table: db.q(f"SELECT * FROM {table} ORDER BY rowid")
        for table in (
            "garment", "outfit", "saved_look_version", "saved_look_automation_profile",
            "saved_look_photo_evidence", "saved_look_automation_proposal",
            "saved_look_combined_save_receipt", "photo_look_proposal",
            "photo_look_automation_proposal", "look_photo_stage",
        )
    }

    refused = client.post("/api/looks", json=body)

    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == expected_code
    after = {
        table: db.q(f"SELECT * FROM {table} ORDER BY rowid")
        for table in before
    }
    assert after == before
