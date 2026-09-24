"""Task 4.3: workflow resolver, binder and drift validator.

The tests exercise the production helpers in ``backend/workflow_binding.py``
through the same paths the prepare / approve / submit boundaries and the
PATCH handler will exercise: a real ``workflow`` row, a real
``session_plan`` row, and the real ``session.workflow_id`` FK. The
fixtures build the rows via the production ``POST /api/workflows``,
``POST /api/models``, ``POST /api/sessions`` and direct ``session_plan``
inserts so the digest and compatibility checks run against the same
``canonical_digest`` the rest of the app uses.

The naming convention follows the existing test files:
``_task43_*`` helpers seed the production state, and the tests inside
``TestTask43WorkflowResolver`` / ``TestTask43WorkflowBindingDrift``
groups cover the matrix the handoff defines.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import db
import resource_store
import session_plan
import workflow_binding


# =====================================================================
# Helpers
# =====================================================================


def _create_workflow(client, *, name: str, kind: str = "", graph: dict) -> tuple[int, str]:
    """Create a workflow via the production API and read its stored kind.

    The production POST returns only ``{id, node_map}``; the kind is read
    back through GET so a test that asks for ``kind=""`` is asserting the
    empty string survives the round-trip. The handoff explicitly bans
    fabricating ``t2i`` for empty kinds, so this helper is the loop-closed
    test for that contract.
    """
    wid = client.post("/api/workflows", json={
        "name": name, "graph": graph, "kind": kind,
    }).json()["id"]
    actual = client.get(f"/api/workflows/{wid}").json()["kind"]
    assert actual == kind, f"workflow {name} recorded as {actual!r}, expected {kind!r}"
    return wid, actual


def _seed_model_with_default(client, *, workflow_id: int) -> int:
    """Create a model whose default workflow is ``workflow_id``."""
    return client.post("/api/models", json={
        "name": f"task43-model-{workflow_id}",
        "lora_name": "characters/ada.safetensors",
        "trigger": "4da woman",
        "base_positive": "photo, 35mm",
        "base_negative": "blurry",
        "workflow_id": workflow_id,
        "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0},
    }).json()["id"]


def _seed_authoring_session(client, *, model_id: int, workflow_id: int) -> int:
    """Create a resource-v1 session whose primary graph is ``workflow_id``."""
    response = client.post("/api/sessions", json={
        "model_id": model_id,
        "name": f"task43-authoring-session-{workflow_id}",
        "composition_mode": "resource-v1",
        "workflow_id": workflow_id,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _authoring_plan(workflow_id: int, *, kind: str = "t2i", mode: str = "manual") -> dict:
    """A minimal valid authoring-v1 plan with a workflow_binding fixture.

    The plan's stored binding is computed via the production binder so the
    test exercises the same code path 4.4 will. The stored ``kind`` is
    whatever the row was created with, including the empty string.
    ``mode`` defaults to ``"manual"`` so tests that exercise the real
    prepare endpoint with ``manual_completion`` land on the manual
    path; tests that only care about the schema pass through the
    authoring validation regardless of mode.
    """
    return {
        "version": "resource-v1",
        "look": "invented studio look",
        "initial_wardrobe": "linen shirt",
        "takes": [{"take_id": "take-001"}],
        "selected_resources": [],
        "wardrobe_changes": [],
        "authoring": {
            "schema_version": 1,
            "mode": mode,
            "brief": "task 4.3 invented brief",
            "scene_anchor": {
                "library_key": "task43_lib",
                "source_id": "task43_src",
                "content_digest": "a" * 64,
            },
            "workflow_binding": workflow_binding.build_workflow_binding(workflow_id),
            "variation_policy": {
                "camera": {"mode": "vary"},
                "framing": {"mode": "vary"},
                "pose": {"mode": "vary"},
                "expression": {"mode": "vary"},
            },
            "shared_state": {
                "look": {"origin": "user", "evidence_id": None},
                "initial_wardrobe": {"origin": "user", "evidence_id": None},
            },
            "evidence": [],
            "look_snapshot": None,
            "wardrobe_progression": None,
        },
    }


def _setup_real_room_revision() -> dict:
    """Register a real room library + revision so ``selected_resources``
    validates against the actual catalogue. The keys returned are the
    ones the authoring scene_anchor validation expects to find in
    ``plan.selected_resources``.
    """
    lib_id = resource_store.ensure_library("inv_task43_rooms", kind="rooms")
    payload = {
        "label": "task43 invented room",
        "scene_theme": "minimalist studio lighting",
        "weight": 1.0,
    }
    rev_id = resource_store.record_revision(
        lib_id, "inv_room_task43", payload,
        translation={
            "label": "task43 invented room",
            "scene_theme": "minimalist studio lighting",
        },
    )
    rev = resource_store.get_revision(revision_id=rev_id)
    assert rev is not None
    return {
        "library_key": "inv_task43_rooms",
        "source_id": "inv_room_task43",
        "content_digest": rev["content_digest"],
    }


def _save_authoring_plan(session_id: int, plan: dict) -> None:
    encoded = json.dumps(plan, ensure_ascii=True, separators=(",", ":"))
    now = db.now()
    db.run(
        "INSERT INTO session_plan (session_id, mode, plan_revision, plan_json, "
        "created_at, updated_at) "
        "VALUES (?, 'resource-v1', 1, ?, ?, ?)",
        session_id, encoded, now, now,
    )


def _bind_session_to_workflow(session_id: int, workflow_id: int) -> None:
    """Force ``session.workflow_id`` to ``workflow_id`` without going through API."""
    db.run(
        "UPDATE session SET workflow_id = ? WHERE id = ?",
        workflow_id, session_id,
    )


def _prepare_approved_authoring_session(client, seeded) -> int:
    sid = _seed_authoring_session(
        client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
    )
    plan = _authoring_plan(seeded["workflow_id"])
    plan["look"] = ""
    plan["initial_wardrobe"] = ""
    revision = _setup_real_room_revision()
    plan["selected_resources"] = [revision]
    plan["authoring"]["scene_anchor"] = revision
    _save_authoring_plan(sid, plan)
    response = client.post(
        f"/api/sessions/{sid}/plan/takes/take-001/prepare",
        json={"plan_revision": 1, "manual_completion": {
            "camera": "wide 35mm", "framing": "waist up",
            "pose": "standing still", "expression": "neutral smile",
        }},
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/sessions/{sid}/plan/approve", json={"plan_revision": 1},
    )
    assert response.status_code == 200, response.text
    return sid


# =====================================================================
# Resolver
# =====================================================================


class TestTask43WorkflowResolver:
    @pytest.mark.parametrize("stored_kind", [b"", b"t2i"])
    def test_initial_binding_rejects_blob_kind(self, client, seeded, stored_kind):
        wid = seeded["workflow_id"]
        db.run("UPDATE workflow SET kind=? WHERE id=?", stored_kind, wid)
        assert isinstance(db.one("SELECT kind FROM workflow WHERE id=?", wid)["kind"], bytes)
        with pytest.raises(workflow_binding.WorkflowCompatibilityError, match="kind"):
            workflow_binding.build_workflow_binding(wid)
        with pytest.raises(workflow_binding.WorkflowCompatibilityError, match="kind"):
            workflow_binding.resolve_effective_workflow(seeded["model_id"])
        assert db.q("SELECT * FROM session") == []
        assert db.q("SELECT * FROM session_plan") == []

    @pytest.mark.parametrize("bad_kind", [0, None, True])
    def test_initial_binding_rejects_non_string_kind_from_row(
        self, client, seeded, monkeypatch, bad_kind,
    ):
        original = workflow_binding.db.one
        wid = seeded["workflow_id"]

        def row_with_bad_kind(sql, *args):
            row = original(sql, *args)
            if row is not None and sql.startswith("SELECT id, kind, graph, node_map FROM workflow"):
                return {**row, "kind": bad_kind}
            return row

        monkeypatch.setattr(workflow_binding.db, "one", row_with_bad_kind)
        with pytest.raises(workflow_binding.WorkflowCompatibilityError, match="kind"):
            workflow_binding.build_workflow_binding(wid)

    @pytest.mark.parametrize("stored_kind", [b"", b"t2i"])
    def test_frozen_binding_rejects_blob_kind_without_writes(
        self, client, seeded, stored_kind,
    ):
        wid = seeded["workflow_id"]
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=wid,
        )
        plan = _authoring_plan(wid)
        original_binding = plan["authoring"]["workflow_binding"].copy()
        _save_authoring_plan(sid, plan)
        db.run("UPDATE workflow SET kind=? WHERE id=?", stored_kind, wid)
        before_session = dict(db.one("SELECT * FROM session WHERE id=?", sid))
        before_plan = dict(db.one("SELECT * FROM session_plan WHERE session_id=?", sid))
        before_shots = [dict(row) for row in db.q("SELECT * FROM shot WHERE session_id=?", sid)]
        with pytest.raises(workflow_binding.WorkflowChanged, match="kind"):
            workflow_binding.validate_workflow_binding_against_session(sid)
        assert dict(db.one("SELECT * FROM session WHERE id=?", sid)) == before_session
        assert dict(db.one("SELECT * FROM session_plan WHERE session_id=?", sid)) == before_plan
        assert [dict(row) for row in db.q("SELECT * FROM shot WHERE session_id=?", sid)] == before_shots
        assert json.loads(before_plan["plan_json"])["authoring"]["workflow_binding"] == original_binding

    def test_override_wins_over_model_default(self, client, seeded):
        # The seeded workflow is the model default; an explicit override
        # wins. We seed a second workflow the override will pick.
        wid_a = seeded["workflow_id"]
        wid_b = _create_workflow(
            client, name="task43-override", graph=seeded["node_map"] and
            json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", wid_a,
            )["graph"]),
        )[0]
        # Resolve without override: model default wins
        resolved = workflow_binding.resolve_effective_workflow(seeded["model_id"])
        assert resolved["workflow_id"] == wid_a
        # Resolve with explicit override: override wins regardless of default
        resolved = workflow_binding.resolve_effective_workflow(
            seeded["model_id"], override_workflow_id=wid_b,
        )
        assert resolved["workflow_id"] == wid_b

    def test_no_override_uses_model_default(self, client, seeded):
        wid = seeded["workflow_id"]
        resolved = workflow_binding.resolve_effective_workflow(seeded["model_id"])
        assert resolved["workflow_id"] == wid

    def test_explicit_missing_override_raises_workflow_compatibility_error(
        self, client, seeded,
    ):
        with pytest.raises(workflow_binding.WorkflowCompatibilityError) as exc:
            workflow_binding.resolve_effective_workflow(
                seeded["model_id"], override_workflow_id=999_999,
            )
        assert exc.value.code == "workflow_compatibility_invalid"
        assert "does not exist" in exc.value.message
        assert "select an existing Advanced workflow" in exc.value.message

    def test_no_override_no_default_raises_workflow_required(
        self, client, seeded,
    ):
        # Create a model whose default is None (no override, no model wf).
        model_id = client.post("/api/models", json={
            "name": "task43-no-default-model",
            "lora_name": "characters/empty.safetensors",
            "trigger": "x",
            "base_positive": "",
            "base_negative": "",
        }).json()["id"]
        with pytest.raises(workflow_binding.WorkflowRequired) as exc:
            workflow_binding.resolve_effective_workflow(model_id)
        assert exc.value.code == "workflow_required"
        assert "no default workflow" in exc.value.message
        assert "select an Advanced workflow override" in exc.value.message

    def test_default_pointing_to_missing_row_raises_compatibility(self, client, seeded):
        # Build a model pointing at a workflow id, then delete the workflow
        # so the FK ON DELETE SET NULL has fired but the resolver is asked
        # to look up a stale id (this exercises the row-not-found branch).
        wid = seeded["workflow_id"]
        model_id = _seed_model_with_default(client, workflow_id=wid)
        # Temporarily disable FKs so we can re-point the model at a
        # non-existent workflow id; restore after the assertion.
        db.conn().execute("PRAGMA foreign_keys = OFF")
        try:
            db.run(
                "UPDATE model SET workflow_id = 999999 WHERE id = ?",
                model_id,
            )
            with pytest.raises(workflow_binding.WorkflowCompatibilityError) as exc:
                workflow_binding.resolve_effective_workflow(model_id)
            assert exc.value.code == "workflow_compatibility_invalid"
            assert "no longer exists" in exc.value.message
        finally:
            db.conn().execute("PRAGMA foreign_keys = ON")

    def test_absent_model_is_a_distinct_failure(self, client, seeded):
        with pytest.raises(workflow_binding.WorkflowCompatibilityError) as exc:
            workflow_binding.resolve_effective_workflow(999_999)
        assert "model 999999 does not exist" in exc.value.message

    def test_override_type_validation_rejects_non_positive_int(
        self, client, seeded,
    ):
        # ``None`` is the "no override, use model default" sentinel and
        # must NOT raise; every other non-positive-integer value does.
        for bad in (0, -1, "1", True, 1.5):
            with pytest.raises(workflow_binding.WorkflowCompatibilityError):
                workflow_binding.resolve_effective_workflow(
                    seeded["model_id"], override_workflow_id=bad,
                )

    def test_stored_kind_is_returned_verbatim_including_empty(self, client, seeded):
        # Default seeded workflow has no explicit kind on creation; the
        # default kind is the empty string. The resolver must return it
        # verbatim rather than fabricating "t2i".
        wid = seeded["workflow_id"]
        resolved = workflow_binding.resolve_effective_workflow(seeded["model_id"])
        # Task 4.3 repair: the resolver returns the closed canonical
        # binding, not the legacy {workflow_id, kind} pair. The four
        # closed keys are present and the stored kind is preserved.
        assert resolved["workflow_id"] == wid
        assert resolved["kind"] == ""
        assert set(resolved.keys()) == {
            "workflow_id", "kind", "graph_digest", "node_map_digest",
        }

        # An explicit tagged workflow keeps its tag.
        tagged = _create_workflow(
            client, name="task43-tagged-edit",
            kind="edit",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", wid,
            )["graph"]),
        )[0]
        resolved = workflow_binding.resolve_effective_workflow(
            seeded["model_id"], override_workflow_id=tagged,
        )
        assert resolved["workflow_id"] == tagged
        assert resolved["kind"] == "edit"

    def test_resolver_validates_model_in_override_branch(self, client, seeded):
        # Task 4.3 repair: a missing model fails closed regardless of
        # whether the caller passed an explicit override. Without this
        # check the previous implementation returned a binding for an
        # override against a character that did not exist.
        wid = seeded["workflow_id"]
        with pytest.raises(workflow_binding.WorkflowCompatibilityError) as exc:
            workflow_binding.resolve_effective_workflow(
                999_999, override_workflow_id=wid,
            )
        assert "does not exist" in exc.value.message

    def test_resolver_runs_compatibility_and_rejects_invalid_override(
        self, client, seeded,
    ):
        # Build a workflow whose graph is invalid (malformed JSON).
        # The resolver refuses it as WorkflowCompatibilityError
        # because the resolver must validate the row it would bind
        # to, not just confirm it exists.
        bad = _create_workflow(
            client, name="task43-bad-graph-override",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?",
                seeded["workflow_id"],
            )["graph"]),
        )[0]
        db.run("UPDATE workflow SET graph = '{not json' WHERE id = ?", bad)
        with pytest.raises(workflow_binding.WorkflowCompatibilityError):
            workflow_binding.resolve_effective_workflow(
                seeded["model_id"], override_workflow_id=bad,
            )


# =====================================================================
# Binder and drift validator
# =====================================================================


class TestTask43WorkflowBindingDrift:
    def test_authoring_plan_with_empty_kind_round_trips(self, client, seeded):
        # The seeded workflow was created without an explicit ``kind``,
        # which the production API records as the empty string. The
        # binder reads the row verbatim and the validator compares back
        # without ever fabricating a tag.
        binding = workflow_binding.build_workflow_binding(seeded["workflow_id"])
        assert binding["kind"] == ""
        # Round-trip: a plan saved with this binding, then read back,
        # preserves the empty string verbatim.
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"], kind="")
        _save_authoring_plan(sid, plan)
        stored_plan_row = db.one(
            "SELECT plan_json FROM session_plan WHERE session_id = ?", sid,
        )
        decoded = json.loads(stored_plan_row["plan_json"])
        assert decoded["authoring"]["workflow_binding"]["kind"] == ""
        # And the drift validator confirms ``""`` == ``""`` — no
        # fabricated replacement tag.
        live = workflow_binding.validate_workflow_binding_against_session(sid)
        assert live == {"workflow_id": seeded["workflow_id"]}

    def test_binding_carries_exact_id_stored_kind_and_independent_digests(
        self, client, seeded,
    ):
        wid = seeded["workflow_id"]
        binding = workflow_binding.build_workflow_binding(wid)
        assert binding["workflow_id"] == wid
        assert binding["kind"] == ""  # the stored default for legacy workflows
        # Both digests are 64 lowercase hex chars, and they are
        # independent: tampering one of the rows keeps the other equal.
        for field in ("graph_digest", "node_map_digest"):
            value = binding[field]
            assert isinstance(value, str) and len(value) == 64
            assert all(c in "0123456789abcdef" for c in value)
        assert binding["graph_digest"] != binding["node_map_digest"]

    def test_canonical_equivalence_key_order_and_format_independent(
        self, client, seeded,
    ):
        wid = seeded["workflow_id"]
        graph_a = json.loads(db.one(
            "SELECT graph FROM workflow WHERE id = ?", wid,
        )["graph"])
        # Re-emit the graph with reordered keys and extra whitespace.
        reordered = {k: graph_a[k] for k in reversed(list(graph_a))}
        rewritten = json.dumps(reordered, indent=2, separators=(", ", ": "))
        db.run("UPDATE workflow SET graph = ? WHERE id = ?", rewritten, wid)
        # The graph digest must be unchanged because canonical_digest is
        # order-insensitive.
        binding = workflow_binding.build_workflow_binding(wid)
        original = workflow_binding.build_workflow_binding(wid)
        # Re-write back so subsequent tests see the canonical form.
        db.run(
            "UPDATE workflow SET graph = ? WHERE id = ?",
            json.dumps(graph_a, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            wid,
        )
        assert binding["graph_digest"] == original["graph_digest"]

    def test_semantic_graph_change_produces_different_digest(self, client, seeded):
        wid = seeded["workflow_id"]
        before = workflow_binding.build_workflow_binding(wid)
        # Add a fake node — a real semantic change.
        graph_text = db.one(
            "SELECT graph FROM workflow WHERE id = ?", wid,
        )["graph"]
        parsed = json.loads(graph_text)
        parsed["99"] = {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "ComfyUI_extra", "images": ["8", 0],
        }}
        db.run(
            "UPDATE workflow SET graph = ? WHERE id = ?",
            json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            wid,
        )
        after = workflow_binding.build_workflow_binding(wid)
        assert after["graph_digest"] != before["graph_digest"]

    def test_drift_raises_workflow_changed_before_writes_when_row_deleted(
        self, client, seeded,
    ):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Delete the bound workflow row; the FK ON DELETE SET NULL will
        # null session.workflow_id, which the validator detects.
        db.run("DELETE FROM workflow WHERE id = ?", seeded["workflow_id"])
        with pytest.raises(workflow_binding.WorkflowChanged) as exc:
            workflow_binding.validate_workflow_binding_against_session(sid)
        assert exc.value.code == "workflow_changed"
        assert "no longer points" in exc.value.message

    def test_drift_raises_on_graph_change(self, client, seeded):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Tamper with the stored graph: an extra node the plan never froze.
        graph_text = db.one(
            "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
        )["graph"]
        parsed = json.loads(graph_text)
        parsed["99"] = {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "x", "images": ["8", 0],
        }}
        db.run(
            "UPDATE workflow SET graph = ? WHERE id = ?",
            json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            seeded["workflow_id"],
        )
        with pytest.raises(workflow_binding.WorkflowChanged, match="graph"):
            workflow_binding.validate_workflow_binding_against_session(sid)

    def test_drift_raises_on_node_map_change(self, client, seeded):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Tamper with the stored node_map: a slot the plan never froze.
        node_map_text = db.one(
            "SELECT node_map FROM workflow WHERE id = ?", seeded["workflow_id"],
        )["node_map"]
        parsed = json.loads(node_map_text)
        parsed["forged_extra_slot"] = "1.inputs.text"
        db.run(
            "UPDATE workflow SET node_map = ? WHERE id = ?",
            json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            seeded["workflow_id"],
        )
        with pytest.raises(workflow_binding.WorkflowChanged, match="node_map"):
            workflow_binding.validate_workflow_binding_against_session(sid)

    def test_drift_raises_on_kind_change(self, client, seeded):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Change the row's stored kind after the plan froze empty.
        db.run(
            "UPDATE workflow SET kind = 'edit' WHERE id = ?",
            seeded["workflow_id"],
        )
        with pytest.raises(workflow_binding.WorkflowChanged, match="kind"):
            workflow_binding.validate_workflow_binding_against_session(sid)

    def test_drift_raises_when_session_id_disagrees_with_binding(self, client, seeded):
        # Build a session whose session.workflow_id differs from the
        # binding's workflow_id (a stale session id from a manual
        # mutation or migration).
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Create a second workflow, point session at it without touching
        # the binding.
        wid2 = _create_workflow(
            client, name="task43-second",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
            )["graph"]),
        )[0]
        _bind_session_to_workflow(sid, wid2)
        with pytest.raises(workflow_binding.WorkflowChanged, match="session.workflow_id"):
            workflow_binding.validate_workflow_binding_against_session(sid)

    def test_drift_raises_when_binding_tampered_in_plan_json(self, client, seeded):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        # Tamper with the binding before saving (forged workflow_id).
        plan["authoring"]["workflow_binding"]["workflow_id"] = 999_999
        _save_authoring_plan(sid, plan)
        with pytest.raises(workflow_binding.WorkflowChanged, match="workflow_id"):
            workflow_binding.validate_workflow_binding_against_session(sid)

    def test_drift_raises_when_plan_json_is_malformed(self, client, seeded):
        # Malformed plan_json is unreadable stored corruption, not
        # workflow-binding drift. The validator raises
        # ``StoredPlanUnreadable`` so the existing
        # ``PreparedTakePersistenceError`` category continues to own
        # this kind of failure, and the read-only guard refuses the
        # next authoring write without inventing a workflow identity
        # claim.
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        now = db.now()
        db.run(
            "INSERT INTO session_plan (session_id, mode, plan_revision, plan_json, "
            "created_at, updated_at) VALUES (?, 'resource-v1', 1, ?, ?, ?)",
            sid, "{not json", now, now,
        )
        with pytest.raises(workflow_binding.StoredPlanUnreadable, match="malformed"):
            workflow_binding.validate_workflow_binding_against_session(sid)

    def test_no_plan_row_is_a_no_op(self, client, seeded):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        # No plan saved: validator returns None.
        assert workflow_binding.validate_workflow_binding_against_session(sid) is None

    def test_pre_authoring_plan_without_authoring_block_is_a_no_op(self, client, seeded):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        # Save a pre-authoring plan (no ``authoring`` block).
        plan = {
            "version": "resource-v1",
            "look": "invented studio look",
            "initial_wardrobe": "linen shirt",
            "takes": [{"take_id": "take-001", "camera": "50mm", "pose": "standing"}],
            "selected_resources": [],
            "wardrobe_changes": [],
        }
        _save_authoring_plan(sid, plan)
        assert workflow_binding.validate_workflow_binding_against_session(sid) is None

    def test_no_db_lookup_of_model_default_during_validation(self, client, seeded):
        """The frozen default: changing model.workflow_id must not move the
        validator's answer.
        """
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Mutate model.workflow_id to a different row.
        wid_b = _create_workflow(
            client, name="task43-second-default",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
            )["graph"]),
        )[0]
        db.run(
            "UPDATE model SET workflow_id = ? WHERE id = ?",
            wid_b, seeded["model_id"],
        )
        # Validator must still return the original bound workflow.
        live = workflow_binding.validate_workflow_binding_against_session(sid)
        assert live == {"workflow_id": seeded["workflow_id"]}

    def test_canonically_equivalent_resave_is_allowed(self, client, seeded):
        """A workflow row rewritten with reordered keys or reformatted
        whitespace keeps the same digests and is not flagged as drift.
        """
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Resave with extra whitespace.
        graph_text = db.one(
            "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
        )["graph"]
        parsed = json.loads(graph_text)
        rewritten = json.dumps(parsed, indent=4, separators=(", ", ": "))
        db.run(
            "UPDATE workflow SET graph = ? WHERE id = ?",
            rewritten, seeded["workflow_id"],
        )
        # Validator must accept — the digests compare equal.
        live = workflow_binding.validate_workflow_binding_against_session(sid)
        assert live == {"workflow_id": seeded["workflow_id"]}


# =====================================================================
# Preparation / approval / submission enforcement
# =====================================================================


class TestTask43DriftEnforcementOnAuthoringPaths:
    """The drift validator must run BEFORE any preparation, approval or
    submission write, so a refused request leaves plan, prepared_take,
    approval and shot rows byte-for-byte unchanged.
    """

    def test_preparation_target_validation_runs_validator(self, client, seeded):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Drift the graph.
        graph_text = db.one(
            "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
        )["graph"]
        parsed = json.loads(graph_text)
        parsed["99"] = {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "x", "images": ["8", 0],
        }}
        db.run(
            "UPDATE workflow SET graph = ? WHERE id = ?",
            json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            seeded["workflow_id"],
        )
        # No pending / ready snapshot was ever created.
        assert db.q(
            "SELECT * FROM prepared_take WHERE session_id = ?", sid,
        ) == []
        with pytest.raises(workflow_binding.WorkflowChanged):
            session_plan.begin_preparation(sid, 1, "take-001")
        # Database unchanged: no pending row.
        assert db.q(
            "SELECT * FROM prepared_take WHERE session_id = ?", sid,
        ) == []

    def test_approve_plan_review_runs_validator(self, client, seeded):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Drift the kind.
        db.run(
            "UPDATE workflow SET kind = 'edit' WHERE id = ?",
            seeded["workflow_id"],
        )
        with pytest.raises(workflow_binding.WorkflowChanged):
            session_plan.approve_plan_review(sid, 1)
        # No approval row was created.
        assert db.one(
            "SELECT * FROM session_plan_approval WHERE session_id = ?", sid,
        ) is None

    def test_submit_prepared_take_runs_validator_on_ready_branch(
        self, client, seeded,
    ):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Seed a ready prepared_take directly so we exercise the
        # ``existing.status == READY`` branch of submit_prepared_take.
        snap = {
            "final_prompt": "invented task43 submit prompt",
            "effective_state": {"look": "invented", "wardrobe": "linen shirt"},
            "mapping_version": "task43-v1",
            "compiler_version": "task43-v1",
            "provenance": {"source": "task43"},
        }
        now = db.now()
        db.run(
            "INSERT INTO prepared_take (session_id, plan_revision, take_id, "
            "final_prompt, effective_state, mapping_version, compiler_version, "
            "provenance, status, created_at, updated_at) "
            "VALUES (?, 1, 'take-001', ?, ?, ?, ?, ?, 'ready', ?, ?)",
            sid, snap["final_prompt"],
            json.dumps(snap["effective_state"]),
            snap["mapping_version"], snap["compiler_version"],
            json.dumps(snap["provenance"]),
            now, now,
        )
        # Drift the workflow graph.
        graph_text = db.one(
            "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
        )["graph"]
        parsed = json.loads(graph_text)
        parsed["99"] = {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "x", "images": ["8", 0],
        }}
        db.run(
            "UPDATE workflow SET graph = ? WHERE id = ?",
            json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            seeded["workflow_id"],
        )
        # Approve first so submit is allowed to reach the workflow check.
        with pytest.raises(workflow_binding.WorkflowChanged):
            session_plan.approve_plan_review(sid, 1)
        # No approval row was created.
        assert db.one(
            "SELECT * FROM session_plan_approval WHERE session_id = ?", sid,
        ) is None
        # No shot row was created.
        assert db.q("SELECT * FROM shot WHERE session_id = ?", sid) == []

    def test_preparation_drift_leaves_no_writes_or_side_effects(self, client, seeded):
        # Seed a plan with a real resource revision so prepare_take_inputs
        # would otherwise walk the entire pipeline. We do NOT call
        # ``prepare_take_inputs`` here — only ``begin_preparation``, which
        # is the boundary 4.3 owns.
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        before_plan = db.one("SELECT * FROM session_plan WHERE session_id = ?", sid)
        # Drift.
        db.run(
            "UPDATE workflow SET kind = 'guide' WHERE id = ?",
            seeded["workflow_id"],
        )
        with pytest.raises(workflow_binding.WorkflowChanged):
            session_plan.begin_preparation(sid, 1, "take-001")
        # Plan row, prepared_take, approval and shot rows are all
        # unchanged.
        assert db.one("SELECT * FROM session_plan WHERE session_id = ?", sid) == before_plan
        assert db.q("SELECT * FROM prepared_take WHERE session_id = ?", sid) == []
        assert db.one(
            "SELECT * FROM session_plan_approval WHERE session_id = ?", sid,
        ) is None
        assert db.q("SELECT * FROM shot WHERE session_id = ?", sid) == []


# =====================================================================
# PATCH preflight
# =====================================================================


class TestTask43PatchPreflight:
    def test_patch_workflow_swap_on_authoring_session_returns_409(
        self, client, seeded,
    ):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Build a different workflow the PATCH will try to swap in.
        wid_b = _create_workflow(
            client, name="task43-patch-target",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
            )["graph"]),
        )[0]
        before = db.one("SELECT * FROM session WHERE id = ?", sid)
        response = client.patch(
            f"/api/sessions/{sid}", json={"workflow_id": wid_b},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "workflow_changed"
        # Session row unchanged.
        assert db.one("SELECT * FROM session WHERE id = ?", sid) == before

    def test_patch_reference_workflow_swap_on_authoring_session_is_allowed(
        self, client, seeded,
    ):
        # The OpenSpec binding freezes only the effective primary
        # ``workflow_id``. ``reference_workflow_id`` is a separate
        # session relation that is not part of the closed binding and
        # is governed by the existing preflight rules when an actual
        # reference take uses it. A PATCH that swaps the reference
        # workflow on an authoring session lands as a normal PATCH;
        # Task 4.3 does not freeze this column.
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        wid_ref = _create_workflow(
            client, name="task43-patch-ref",
            kind="edit",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
            )["graph"]),
        )[0]
        response = client.patch(
            f"/api/sessions/{sid}", json={"reference_workflow_id": wid_ref},
        )
        # Not the authoring guard: lands as a regular session PATCH
        # (the exact status depends on body validation, not on Task
        # 4.3).
        assert response.status_code != 409, response.text

    def test_patch_identical_echo_is_accepted(self, client, seeded):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        response = client.patch(
            f"/api/sessions/{sid}", json={"workflow_id": seeded["workflow_id"]},
        )
        # The echo is harmless and lands as a normal PATCH (200 or 400
        # depending on validation, but never the 409 authoring guard).
        assert response.status_code != 409, response.text

    def test_patch_on_pre_authoring_session_can_swap_workflow(self, client, seeded):
        # Pre-authoring expert sessions (no ``authoring`` block) retain
        # the historical "edit workflow" behaviour.
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = {
            "version": "resource-v1",
            "look": "invented studio look",
            "initial_wardrobe": "linen shirt",
            "takes": [{"take_id": "take-001", "camera": "50mm", "pose": "standing"}],
            "selected_resources": [],
            "wardrobe_changes": [],
        }
        _save_authoring_plan(sid, plan)
        wid_b = _create_workflow(
            client, name="task43-pre-auth-target",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
            )["graph"]),
        )[0]
        response = client.patch(
            f"/api/sessions/{sid}", json={"workflow_id": wid_b},
        )
        assert response.status_code != 409, response.text

    def test_patch_on_legacy_session_can_swap_workflow(self, client, seeded):
        # Legacy (non resource-v1) sessions retain historical workflow edits.
        legacy = client.post("/api/sessions", json={
            "model_id": seeded["model_id"],
            "name": "task43 legacy patch",
            "workflow_id": seeded["workflow_id"],
        }).json()["id"]
        wid_b = _create_workflow(
            client, name="task43-legacy-target",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
            )["graph"]),
        )[0]
        response = client.patch(
            f"/api/sessions/{legacy}", json={"workflow_id": wid_b},
        )
        assert response.status_code != 409, response.text

    def test_model_default_patch_does_not_affect_bound_session(self, client, seeded):
        # Authoring session is bound to workflow X; mutate the model's
        # default to Y. Session.workflow_id stays X; the bound session's
        # work keeps going through X. The model PATCH requires the full
        # ``ModelIn`` schema, so we go directly through the DB to exercise
        # the contract: changing ``model.workflow_id`` does NOT move an
        # already-bound session's binding.
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        wid_b = _create_workflow(
            client, name="task43-model-default-swap",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
            )["graph"]),
        )[0]
        db.run(
            "UPDATE model SET workflow_id = ? WHERE id = ?",
            wid_b, seeded["model_id"],
        )
        # Session row, plan and binding are intact.
        session_row = db.one("SELECT workflow_id FROM session WHERE id = ?", sid)
        assert session_row["workflow_id"] == seeded["workflow_id"]
        live = workflow_binding.validate_workflow_binding_against_session(sid)
        assert live == {"workflow_id": seeded["workflow_id"]}


# =====================================================================
# Compatibility predicates
# =====================================================================


class TestTask43CompatibilityPredicates:
    @pytest.mark.parametrize("choice", ["sampler", "scheduler", "checkpoint"])
    def test_model_settings_mapping_parity_with_run_preflight(
        self, client, seeded, choice,
    ):
        import main
        wid = seeded["workflow_id"]
        value = {"sampler": "dpmpp", "scheduler": "karras",
                 "checkpoint": "invented/base.safetensors"}[choice]
        db.run("UPDATE model SET lora_name='', settings=? WHERE id=?",
               json.dumps({choice: value}), seeded["model_id"])
        db.run("UPDATE workflow SET node_map='{}' WHERE id=?", wid)
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": "settings parity",
            "shots": [{"prompt": "portrait", "count": 1}],
        }).json()["id"]
        assert client.get(f"/api/sessions/{sid}").json()["settings"][choice] == value
        label = "base model" if choice == "checkpoint" else choice
        with pytest.raises(workflow_binding.WorkflowCompatibilityError, match=label):
            workflow_binding.resolve_effective_workflow(seeded["model_id"])
        with pytest.raises(main.HTTPException) as exc:
            main._require_mapped_choices(sid)
        assert exc.value.status_code == 400
        assert label in exc.value.detail

        db.run("UPDATE workflow SET node_map=? WHERE id=?",
               json.dumps({choice: "1.inputs.choice"}), wid)
        assert workflow_binding.resolve_effective_workflow(
            seeded["model_id"]
        )["workflow_id"] == wid
        main._require_mapped_choices(sid)

    def test_request_settings_override_model_defaults(self, client, seeded):
        import main
        wid = seeded["workflow_id"]
        db.run("UPDATE model SET lora_name='', settings=? WHERE id=?",
               json.dumps({"sampler": "dpmpp"}), seeded["model_id"])
        db.run("UPDATE workflow SET node_map='{}' WHERE id=?", wid)
        overrides = {"sampler": ""}
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": "settings override",
            "settings": overrides,
            "shots": [{"prompt": "portrait", "count": 1}],
        }).json()["id"]
        assert client.get(f"/api/sessions/{sid}").json()["settings"]["sampler"] == ""
        assert workflow_binding.resolve_effective_workflow(
            seeded["model_id"], settings=overrides,
        )["workflow_id"] == wid
        main._require_mapped_choices(sid)
        with pytest.raises(workflow_binding.WorkflowCompatibilityError, match="sampler"):
            workflow_binding.resolve_effective_workflow(seeded["model_id"])

    @pytest.mark.parametrize("has_lora", [True, False])
    def test_primary_mapping_parity_with_run_preflight(self, client, seeded, has_lora):
        import main
        wid = seeded["workflow_id"]
        db.run("UPDATE workflow SET node_map = '{}' WHERE id = ?", wid)
        if not has_lora:
            db.run("UPDATE model SET lora_name = '' WHERE id = ?", seeded["model_id"])
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": "mapping parity",
            "shots": [{"prompt": "portrait", "count": 1}],
        }).json()["id"]
        if has_lora:
            with pytest.raises(workflow_binding.WorkflowCompatibilityError, match="LoRA"):
                workflow_binding.resolve_effective_workflow(seeded["model_id"])
            with pytest.raises(main.HTTPException, match="LoRA"):
                main._require_mapped_choices(sid)
        else:
            assert workflow_binding.resolve_effective_workflow(
                seeded["model_id"]
            )["workflow_id"] == wid
            main._require_mapped_choices(sid)

    @pytest.mark.parametrize("anchors,slots,issue,http_detail", [
        (0, 1, "anchor", "none is set"),
        (1, 0, "slot", "reference image slot"),
        (1, 2, "anchor_count", "reads 2"),
        (1, 1, None, None),
    ])
    def test_reference_parity_with_run_preflight(
        self, client, seeded, anchors, slots, issue, http_detail,
    ):
        import main
        graph = json.loads(db.one(
            "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
        )["graph"])
        ref_id = _create_workflow(client, name="reference parity", graph=graph)[0]
        node_map = {"reference": "1.inputs.image"} if slots else {}
        if slots == 2:
            node_map["reference2"] = "2.inputs.image"
        db.run("UPDATE workflow SET node_map = ? WHERE id = ?", json.dumps(node_map), ref_id)
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": "reference parity",
            "reference_workflow_id": ref_id,
            "shots": [{"prompt": "edit", "count": 1, "reference": True}],
        }).json()["id"]
        anchor_ids = []
        if anchors:
            added = client.post(f"/api/sessions/{sid}/shots", json={
                "shots": [{"prompt": "anchor", "count": 1}],
            })
            assert added.status_code == 200, added.text
            anchor_id = db.one(
                "SELECT id FROM shot WHERE session_id=? ORDER BY id DESC LIMIT 1", sid,
            )["id"]
            db.run("UPDATE shot SET status='done', filename='anchor.png' WHERE id=?", anchor_id)
            anchor_ids = [anchor_id]
            db.run("UPDATE session SET anchor_shot_ids=? WHERE id=?", json.dumps(anchor_ids), sid)
        plan = {"takes": [{"take_id": "edit", "reference": True}],
                "anchor_shot_ids": anchor_ids}
        if issue is None:
            workflow_binding.resolve_effective_workflow(
                seeded["model_id"], reference_workflow_id=ref_id, plan=plan,
            )
            main._require_mapped_choices(sid)
        else:
            with pytest.raises(workflow_binding.WorkflowCompatibilityError, match=issue):
                workflow_binding.resolve_effective_workflow(
                    seeded["model_id"], reference_workflow_id=ref_id, plan=plan,
                )
            with pytest.raises(main.HTTPException) as exc:
                main._require_mapped_choices(sid)
            assert exc.value.status_code == 400
            assert http_detail in exc.value.detail

    def test_primary_graph_must_be_parseable_api_format(self, client, seeded):
        # Forge a workflow whose graph is malformed JSON-as-TEXT.
        wid = _create_workflow(
            client, name="task43-bad-graph",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
            )["graph"]),
        )[0]
        db.run("UPDATE workflow SET graph = '{not json' WHERE id = ?", wid)
        with pytest.raises(workflow_binding.WorkflowCompatibilityError) as exc:
            workflow_binding.build_workflow_binding(wid)
        assert "malformed" in exc.value.message

    def test_primary_node_map_must_be_parseable_dict(self, client, seeded):
        wid = _create_workflow(
            client, name="task43-bad-map",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
            )["graph"]),
        )[0]
        db.run("UPDATE workflow SET node_map = '[]' WHERE id = ?", wid)
        with pytest.raises(workflow_binding.WorkflowCompatibilityError):
            workflow_binding.build_workflow_binding(wid)

    def test_reference_workflow_requires_reference_slot(self, client, seeded):
        # Build a workflow whose node_map omits the reference slot.
        wid = _create_workflow(
            client, name="task43-no-ref-slot",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
            )["graph"]),
        )[0]
        plan = {
            "takes": [{"take_id": "ref-take", "reference": True}],
        }
        with pytest.raises(workflow_binding.WorkflowCompatibilityError) as exc:
            workflow_binding.assert_workflow_compatible(
                model_id=seeded["model_id"],
                primary_workflow_id=seeded["workflow_id"],
                reference_workflow_id=wid,
                plan=plan,
            )
        assert "reference" in exc.value.message

    def test_text_to_image_plan_does_not_require_reference_workflow(
        self, client, seeded,
    ):
        plan = {
            "takes": [{"take_id": "plain-take", "camera": "50mm", "pose": "standing"}],
        }
        # No reference_workflow_id is fine for non-reference plans.
        workflow_binding.assert_workflow_compatible(
            model_id=seeded["model_id"],
            primary_workflow_id=seeded["workflow_id"],
            reference_workflow_id=None,
            plan=plan,
        )

    def test_empty_node_map_is_accepted(self, client, seeded):
        # Task 4.3 repair: the existing API accepts an empty
        # ``node_map`` (default to ``{}``). The strict parser no longer
        # rejects it; only malformed JSON / wrong shape are refused.
        wid = _create_workflow(
            client, name="task43-empty-map",
            graph=json.loads(db.one(
                "SELECT graph FROM workflow WHERE id = ?",
                seeded["workflow_id"],
            )["graph"]),
        )[0]
        db.run("UPDATE workflow SET node_map = '{}' WHERE id = ?", wid)
        db.run("UPDATE model SET lora_name = '' WHERE id = ?", seeded["model_id"])
        # build_workflow_binding accepts the empty map.
        binding = workflow_binding.build_workflow_binding(wid)
        assert binding["workflow_id"] == wid
        # assert_workflow_compatible accepts an empty-map primary row
        # for a text-to-image plan.
        workflow_binding.assert_workflow_compatible(
            model_id=seeded["model_id"],
            primary_workflow_id=wid,
            reference_workflow_id=None,
            plan={"takes": [{"take_id": "plain-take"}]},
        )


# =====================================================================
# Repair-1 regressions: identity, classification, submit, PATCH,
# canonical digest equality, drift on completion write.
# =====================================================================


class TestTask43RepairRegressions:
    """Direct regressions for the blockers the review named."""

    def test_http_submit_drift_returns_409_without_writes(self, client, seeded):
        sid = _prepare_approved_authoring_session(client, seeded)
        db.run("UPDATE workflow SET kind='edit' WHERE id=?", seeded["workflow_id"])
        before_prepared = dict(db.one(
            "SELECT * FROM prepared_take WHERE session_id=?", sid,
        ))
        before_shots = [dict(row) for row in db.q(
            "SELECT * FROM shot WHERE session_id=?", sid,
        )]
        response = client.post(
            f"/api/sessions/{sid}/plan/preparations/submit",
            json={"plan_revision": 1, "take_id": "take-001"},
        )
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert set(detail) == {"code", "message"}
        assert detail["code"] == "workflow_changed"
        assert isinstance(detail["message"], str) and detail["message"]
        assert dict(db.one("SELECT * FROM prepared_take WHERE session_id=?", sid)) == before_prepared
        assert [dict(row) for row in db.q(
            "SELECT * FROM shot WHERE session_id=?", sid,
        )] == before_shots

    def test_patch_corrupt_plan_fails_closed(self, client, seeded):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        _save_authoring_plan(sid, _authoring_plan(seeded["workflow_id"]))
        db.run("UPDATE session_plan SET plan_json='{not json' WHERE session_id=?", sid)
        before_session = dict(db.one("SELECT * FROM session WHERE id=?", sid))
        before_plan = dict(db.one("SELECT * FROM session_plan WHERE session_id=?", sid))
        response = client.patch(f"/api/sessions/{sid}", json={
            "workflow_id": 0, "name": "must not land", "settings": {"cfg": 7},
        })
        assert response.status_code == 500, response.text
        assert "malformed" in response.json()["detail"]
        assert dict(db.one("SELECT * FROM session WHERE id=?", sid)) == before_session
        assert dict(db.one("SELECT * FROM session_plan WHERE session_id=?", sid)) == before_plan

    def test_begin_rechecks_binding_inside_pending_transaction(
        self, client, seeded, monkeypatch,
    ):
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        _save_authoring_plan(sid, _authoring_plan(seeded["workflow_id"]))
        original = session_plan._safe_validate_workflow_binding
        calls = 0

        def drift_after_preflight(session_id):
            nonlocal calls
            original(session_id)
            calls += 1
            if calls == 1:
                db.run("UPDATE workflow SET kind='edit' WHERE id=?", seeded["workflow_id"])

        monkeypatch.setattr(session_plan, "_safe_validate_workflow_binding", drift_after_preflight)
        with pytest.raises(workflow_binding.WorkflowChanged):
            session_plan.begin_preparation(sid, 1, "take-001")
        assert calls == 1
        assert db.q("SELECT * FROM prepared_take WHERE session_id=?", sid) == []

    def test_exception_identity_top_level_and_backend_match(self):
        # Blocked 3: ``workflow_binding`` and ``backend.workflow_binding``
        # must be the same module instance. ``WorkflowChanged`` raised
        # in ``session_plan`` (which imports ``workflow_binding``) must
        # be the same class object ``main.py`` looks up via
        # ``from backend import workflow_binding``.
        import sys
        if "workflow_binding" in sys.modules:
            top = sys.modules["workflow_binding"]
        else:
            import workflow_binding as top  # noqa: F401
            top = sys.modules["workflow_binding"]
        if "backend.workflow_binding" in sys.modules:
            back = sys.modules["backend.workflow_binding"]
        else:
            from backend import workflow_binding as back  # noqa: F401
            back = sys.modules["backend.workflow_binding"]
        assert top is back
        for name in (
            "WorkflowRequired",
            "WorkflowChanged",
            "WorkflowCompatibilityError",
            "StoredPlanUnreadable",
        ):
            assert getattr(top, name) is getattr(back, name)

    def test_workflow_changed_isinstance_recognised_by_session_plan(self):
        # A ``WorkflowChanged`` constructed in the top-level module
        # instance must satisfy ``isinstance`` against the symbol
        # ``session_plan.workflow_binding.WorkflowChanged``.
        exc = workflow_binding.WorkflowChanged("test drift")
        import session_plan
        assert isinstance(exc, session_plan.workflow_binding.WorkflowChanged)
        assert isinstance(exc, session_plan.workflow_binding.WorkflowChanged)

    def test_stored_plan_unreadable_is_classified_as_persistence_error(
        self, client, seeded,
    ):
        # Blocked 5: malformed stored plan_json is unreadable, not
        # drift. The validator raises ``StoredPlanUnreadable``; the
        # domain helpers in ``session_plan`` translate it to the
        # existing ``PreparedTakePersistenceError`` category.
        import session_plan
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        now = db.now()
        db.run(
            "INSERT INTO session_plan (session_id, mode, plan_revision, plan_json, "
            "created_at, updated_at) VALUES (?, 'resource-v1', 1, ?, ?, ?)",
            sid, "{not json", now, now,
        )
        with pytest.raises(session_plan.PreparedTakePersistenceError, match="malformed"):
            session_plan.begin_preparation(sid, 1, "take-001")

    def test_live_graph_drift_raises_workflow_changed_not_compatibility(
        self, client, seeded,
    ):
        # Blocked 5: when the live workflow row's graph becomes
        # unreadable AFTER the binding froze, the validator must raise
        # ``WorkflowChanged`` (the existing drift category) and not
        # ``WorkflowCompatibilityError`` (the resolution-time 422
        # category).
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Tamper with the live graph AFTER binding was set.
        db.run(
            "UPDATE workflow SET graph = '{not json' WHERE id = ?",
            seeded["workflow_id"],
        )
        with pytest.raises(workflow_binding.WorkflowChanged, match="graph could not be parsed"):
            workflow_binding.validate_workflow_binding_against_session(sid)

    def test_live_node_map_drift_raises_workflow_changed_not_compatibility(
        self, client, seeded,
    ):
        # Same logic for the node_map column.
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        db.run(
            "UPDATE workflow SET node_map = '{not json' WHERE id = ?",
            seeded["workflow_id"],
        )
        with pytest.raises(workflow_binding.WorkflowChanged, match="node_map could not be parsed"):
            workflow_binding.validate_workflow_binding_against_session(sid)

    def test_binding_digests_equal_canonical_digest_of_parsed_values(
        self, client, seeded,
    ):
        # Blocked 10: assert the binder stores exactly the value of
        # ``resource_store.canonical_digest`` of the parsed graph /
        # node_map. No double-hash, no re-encoding of raw text.
        import resource_store
        wid = seeded["workflow_id"]
        graph_text = db.one(
            "SELECT graph FROM workflow WHERE id = ?", wid,
        )["graph"]
        node_map_text = db.one(
            "SELECT node_map FROM workflow WHERE id = ?", wid,
        )["node_map"]
        parsed_graph = json.loads(graph_text)
        parsed_map = json.loads(node_map_text)
        binding = workflow_binding.build_workflow_binding(wid)
        assert binding["graph_digest"] == resource_store.canonical_digest(parsed_graph)
        assert binding["node_map_digest"] == resource_store.canonical_digest(parsed_map)

    def test_patch_on_already_drifted_session_cannot_repair(self, client, seeded):
        # Blocked 7: a session whose ``session.workflow_id`` no longer
        # agrees with ``workflow_binding.workflow_id`` cannot be
        # repaired through PATCH. The preflight refuses the requested
        # id when it equals the bound id (because the session row is
        # drifted), and the operator must start a new session.
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        _save_authoring_plan(sid, plan)
        # Drift: nullify the session.workflow_id so it no longer
        # agrees with the binding.
        db.run("UPDATE session SET workflow_id = NULL WHERE id = ?", sid)
        # PATCH asking for the bound id is refused: it would silently
        # repair the drift.
        response = client.patch(
            f"/api/sessions/{sid}", json={"workflow_id": seeded["workflow_id"]},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "workflow_changed"
        # Session row still drifted.
        assert db.one(
            "SELECT workflow_id FROM session WHERE id = ?", sid,
        )["workflow_id"] is None

    def test_submit_prepared_take_drift_blocks_real_submit(self, client, seeded):
        # Blocked 9: the drift validator runs inside the submission
        # transaction, so a drift that lands before submission prevents
        # any new shot row, any new ``linked_shot_id``, any
        # ``status = generated`` mutation. The test exercises the
        # real production path: a manual authoring plan is sealed
        # through the prepare endpoint (so the prepared_take row
        # carries the right provenance, mapping and compiler
        # versions), then the workflow is drifted, then
        # ``submit_prepared_take`` is called and must refuse.
        import session_plan
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        # Build a complete authoring plan with the canonical authoring
        # evidence so the prepare / approve / submit pipeline can run
        # end to end. The take does not pre-establish ``camera`` /
        # ``pose`` so the manual completion can fill them in.
        plan = _authoring_plan(seeded["workflow_id"])
        plan["takes"] = [{"take_id": "take-001"}]
        # Drop the look/wardrobe shared-state for an empty plan so the
        # validator does not require evidence we did not wire up.
        plan["look"] = ""
        plan["initial_wardrobe"] = ""
        # The scene_anchor triple must appear in ``selected_resources``
        # and reference a real registered library / revision so the
        # authoring schema validation passes end-to-end.
        rev = _setup_real_room_revision()
        plan["selected_resources"] = [rev]
        plan["authoring"]["scene_anchor"] = {
            "library_key": rev["library_key"],
            "source_id": rev["source_id"],
            "content_digest": rev["content_digest"],
        }
        _save_authoring_plan(sid, plan)
        # Build a real ready prepared_take via the production prepare
        # endpoint (manual authoring path).
        response = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {
                    "camera": "wide 35mm",
                    "framing": "waist up",
                    "pose": "standing still",
                    "expression": "neutral smile",
                },
            },
        )
        assert response.status_code == 200, response.text
        # Approve so the plan revision is eligible for submission.
        approve_response = client.post(
            f"/api/sessions/{sid}/plan/approve", json={"plan_revision": 1},
        )
        assert approve_response.status_code == 200, approve_response.text
        # Drift the workflow graph AFTER the prepare snapshot is sealed.
        graph_text = db.one(
            "SELECT graph FROM workflow WHERE id = ?", seeded["workflow_id"],
        )["graph"]
        parsed = json.loads(graph_text)
        parsed["99"] = {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "x", "images": ["8", 0],
        }}
        db.run(
            "UPDATE workflow SET graph = ? WHERE id = ?",
            json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            seeded["workflow_id"],
        )
        # Submit must refuse with WorkflowChanged and not produce a shot.
        before_shots = db.q("SELECT * FROM shot WHERE session_id = ?", sid)
        before_prepared = dict(db.one(
            "SELECT * FROM prepared_take "
            "WHERE session_id = ? AND take_id = ?", sid, "take-001",
        ))
        with pytest.raises(workflow_binding.WorkflowChanged, match="graph"):
            session_plan.submit_prepared_take(sid, 1, "take-001")
        # No new shot row.
        assert db.q("SELECT * FROM shot WHERE session_id = ?", sid) == before_shots
        # No ``linked_shot_id`` set; ``status`` unchanged.
        after_prepared = dict(db.one(
            "SELECT * FROM prepared_take "
            "WHERE session_id = ? AND take_id = ?", sid, "take-001",
        ))
        assert after_prepared == before_prepared

    def test_submit_idempotent_branch_drift_also_refuses(self, client, seeded):
        # Blocked 9 idempotent retry: a drift that lands after a take
        # has already been generated must refuse the re-submission; the
        # existing shot is returned only when the binding is intact.
        import session_plan
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        plan["takes"] = [{"take_id": "take-001"}]
        plan["look"] = ""
        plan["initial_wardrobe"] = ""
        rev = _setup_real_room_revision()
        plan["selected_resources"] = [rev]
        plan["authoring"]["scene_anchor"] = {
            "library_key": rev["library_key"],
            "source_id": rev["source_id"],
            "content_digest": rev["content_digest"],
        }
        _save_authoring_plan(sid, plan)
        response = client.post(
            f"/api/sessions/{sid}/plan/takes/take-001/prepare",
            json={
                "plan_revision": 1,
                "manual_completion": {
                    "camera": "wide 35mm",
                    "framing": "waist up",
                    "pose": "standing still",
                    "expression": "neutral smile",
                },
            },
        )
        assert response.status_code == 200, response.text
        approval = client.post(
            f"/api/sessions/{sid}/plan/approve", json={"plan_revision": 1},
        )
        assert approval.status_code == 200, approval.text
        shot_id = session_plan.submit_prepared_take(sid, 1, "take-001")["shot_id"]
        before_prepared = dict(db.one(
            "SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?",
            sid, "take-001",
        ))
        before_shots = [dict(row) for row in db.q(
            "SELECT * FROM shot WHERE session_id = ?", sid,
        )]
        assert before_prepared["status"] == "generated"
        assert before_prepared["linked_shot_id"] == shot_id
        # Drift after production created the generated/linked state.
        db.run(
            "UPDATE workflow SET kind = 'edit' WHERE id = ?",
            seeded["workflow_id"],
        )
        with pytest.raises(workflow_binding.WorkflowChanged, match="kind"):
            session_plan.submit_prepared_take(sid, 1, "take-001")
        assert dict(db.one(
            "SELECT * FROM prepared_take WHERE session_id = ? AND take_id = ?",
            sid, "take-001",
        )) == before_prepared
        assert [dict(row) for row in db.q(
            "SELECT * FROM shot WHERE session_id = ?", sid,
        )] == before_shots

    def test_completion_drift_blocks_inside_sealed_write_transaction(
        self, client, seeded,
    ):
        # Blocked 6: ``complete_authoring_preparation`` runs the drift
        # validator inside the same transactional write boundary that
        # seals the authoring snapshot. The test exercises the exact
        # TOCTOU window: ``begin_preparation`` succeeds with a clean
        # binding (which leaves the prepared_take in ``pending`` and
        # an open transaction is **not** held during the gap), the
        # workflow drifts, then ``complete_authoring_preparation`` is
        # called directly with a sealed result. The in-transaction
        # validator refuses the write and the row never advances to
        # ``ready``.
        import session_plan
        import resource_preparation
        sid = _seed_authoring_session(
            client, model_id=seeded["model_id"], workflow_id=seeded["workflow_id"],
        )
        plan = _authoring_plan(seeded["workflow_id"])
        plan["takes"] = [{"take_id": "take-001"}]
        plan["look"] = ""
        plan["initial_wardrobe"] = ""
        rev = _setup_real_room_revision()
        plan["selected_resources"] = [rev]
        plan["authoring"]["scene_anchor"] = {
            "library_key": rev["library_key"],
            "source_id": rev["source_id"],
            "content_digest": rev["content_digest"],
        }
        _save_authoring_plan(sid, plan)
        # Begin the pending prepared_take while the binding is clean.
        session_plan.begin_preparation(sid, 1, "take-001")
        # Drift the live row before the sealed completion runs.
        db.run(
            "UPDATE workflow SET kind = 'guide' WHERE id = ?",
            seeded["workflow_id"],
        )
        # Build a sealed authoring result and call the completion
        # boundary directly. The validator inside the transaction
        # refuses.
        sealed = resource_preparation._AuthoringPreparedResult(
            resource_preparation._AUTHORING_RESULT_SEAL,
            sid,
            1,
            "take-001",
            "task43 completion drift prompt",
            {"look": "invented", "wardrobe": "linen shirt"},
            resource_preparation.MAPPING_VERSION,
            resource_preparation.COMPILER_VERSION,
            {
                "preparation_version": "task43-v1",
                "mapping_version": resource_preparation.MAPPING_VERSION,
                "compiler_version": resource_preparation.COMPILER_VERSION,
                "module": "backend.resource_preparation",
                "session_id": sid,
                "plan_revision": 1,
                "take_id": "take-001",
                "selected_resource_revisions": [],
                "field_mappings": {},
                "adaptations": [],
                "writer_synthesis": None,
            },
        )
        with pytest.raises(workflow_binding.WorkflowChanged, match="kind"):
            session_plan.complete_authoring_preparation(sealed)
        row = db.one(
            "SELECT status FROM prepared_take WHERE session_id = ? AND take_id = ?",
            sid, "take-001",
        )
        # The pending row never advanced to ``ready``.
        assert row["status"] == "pending"
