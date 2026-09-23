"""Server-side resolution and binding validation for authoring-v1 sessions.

Task 4.3 of ``simplify-resource-session-workflow``. The module exposes
the four operations the prepare / approve / submit paths and the guided
creation (4.4) call without going through ``main``:

  * ``resolve_effective_workflow`` returns the canonical binding a
    freshly-bound session will use. It validates the character
    (``model``) on both branches, never falls back from an explicit
    override, and runs the existing primary/reference compatibility
    rules on the resolved row before returning.
  * ``build_workflow_binding`` reads a single workflow row and returns
    the closed ``{workflow_id, kind, graph_digest, node_map_digest}``
    that the authoring schema validates on the wire. The digests are
    ``canonical_digest`` of the parsed graph / node_map — no double
    hash, no string hash, no silent empty dict from a malformed column.
  * ``validate_workflow_binding_against_session`` is the read-only
    drift guard. It refuses ``WorkflowChanged`` before the prepare /
    approve / submit / completion transaction runs, and refuses the
    same again inside the transactional completion boundary that
    Task 4.3 owns.
  * ``assert_workflow_compatible`` runs the primary/reference
    compatibility rules using the same predicates the live preflight
    uses, so the resolver and the run layer are not two contradictory
    policies. The check is conditional on the plan and the reference
    workflow id, matching the existing run preflight semantics.

The module is intentionally not coupled to ``fastapi``. ``main.py``
maps the exceptions below to the app's stable error envelope; nothing
here imports the HTTP app.

Module identity
---------------
The codebase is sometimes imported as ``backend.workflow_binding`` and
sometimes as ``workflow_binding``. To keep one class identity for
``isinstance`` and ``HTTPException`` mapping, this module registers
itself under both names when run as the top-level ``workflow_binding``.
``session_plan`` and ``resource_preparation`` follow the same pattern.
"""
from __future__ import annotations

import json
import sys
from typing import Any

import db
import resource_store
from comfy import REFERENCE_SLOTS

# Module-identity bootstrap: when this file is loaded as the top-level
# ``workflow_binding`` (because the runner or a test added the
# ``backend/`` directory to ``sys.path`` directly), also register it
# under ``backend.workflow_binding`` so that ``isinstance`` and
# ``HTTPException`` mappers in ``backend.main`` recognise the same
# class object. The reverse direction is handled when this file is
# imported via ``from backend import workflow_binding``: Python keeps a
# single module instance, so this guard is a no-op.
if __name__ == "workflow_binding" and "backend.workflow_binding" not in sys.modules:
    sys.modules["backend.workflow_binding"] = sys.modules[__name__]
elif __name__ == "backend.workflow_binding" and "workflow_binding" not in sys.modules:
    sys.modules["workflow_binding"] = sys.modules[__name__]


# -- Errors ------------------------------------------------------------------


class WorkflowRequired(Exception):
    """Neither the override nor the model's default assigned a workflow.

    A guided session without an effective workflow cannot run. The English
    message names both remedies so the operator does not have to guess
    which side of the boundary they are on. 4.4 maps this to ``422`` in
    its stable error envelope; this module does not assign an HTTP code.
    """

    def __init__(self, message: str) -> None:
        clean_msg = (
            message
            if message.startswith("workflow_required")
            else f"workflow_required: {message}"
        )
        super().__init__(clean_msg)
        self.code = "workflow_required"
        self.message = clean_msg


class WorkflowChanged(Exception):
    """The stored authoring binding no longer matches the live workflow row.

    The session was frozen against one workflow identity. The workflow
    row the session still points to is gone, has different JSON, has a
    different ``kind``, has a tampered session id, carries a binding the
    plan never froze, or is now too malformed to parse — the last case
    is the same drift, because a row the binding froze as one specific
    graph cannot match a row whose stored JSON is no longer the same
    graph. The validator runs before any new authoring write so a
    refused request leaves the plan, prepared_take, approval and shot
    rows byte-for-byte unchanged. The English message directs the
    operator to start a new session rather than try to repair the
    binding.
    """

    def __init__(self, message: str) -> None:
        clean_msg = (
            message
            if message.startswith("workflow_changed")
            else f"workflow_changed: {message}"
        )
        super().__init__(clean_msg)
        self.code = "workflow_changed"
        self.message = clean_msg


class WorkflowCompatibilityError(Exception):
    """The candidate workflow row is invalid for guided resolution.

    A workflow whose graph or map cannot be parsed or lacks the shape
    the run layer expects cannot back a freshly-bound session; the
    resolver refuses it before any session, plan or prepared_take row
    is written. The error is distinct from ``WorkflowRequired`` (no
    override and no default) and from ``WorkflowChanged`` (the
    binding already exists and drifted); this one fires on the
    resolve / ``assert_workflow_compatible`` path, before any plan is
    written.
    """

    def __init__(self, message: str) -> None:
        clean_msg = (
            message
            if message.startswith("workflow_compatibility_invalid")
            else f"workflow_compatibility_invalid: {message}"
        )
        super().__init__(clean_msg)
        self.code = "workflow_compatibility_invalid"
        self.message = clean_msg


class StoredPlanUnreadable(Exception):
    """The stored plan JSON cannot be decoded.

    A stored plan whose JSON-as-TEXT is not parseable, or whose
    top-level value is not an object, is not drift — it is unreadable
    data. The validator propagates this as itself so the caller can
    classify it as the existing ``PreparedTakePersistenceError``
    category instead of mis-routing it as ``workflow_changed``. The
    boundaries that already raise ``PreparedTakePersistenceError`` on
    this path (``_load_current_resource_plan``) keep that contract;
    the validator does not.
    """

    def __init__(self, message: str) -> None:
        clean_msg = (
            message
            if message.startswith("stored_plan_unreadable")
            else f"stored_plan_unreadable: {message}"
        )
        super().__init__(clean_msg)
        self.code = "stored_plan_unreadable"
        self.message = clean_msg


# -- Strict parsers ---------------------------------------------------------


def _parse_required_graph(graph_text: Any) -> dict:
    """Parse and validate the workflow's stored API graph.

    The parse is strict: a workflow row whose graph column is not
    parseable JSON, not an object, or whose nodes lack the API
    ``class_type`` shape produces a closed failure rather than the
    silent ``{}`` ``db.jload`` would have returned. An empty object is
    rejected because the run layer never starts a generation against a
    graph with no nodes — that would be the silent-drop the live
    preflight refuses.
    """
    if not isinstance(graph_text, str) or not graph_text.strip():
        raise WorkflowCompatibilityError(
            "workflow graph is missing or empty; open the workflow and re-save it"
        )
    try:
        parsed = json.loads(graph_text)
    except json.JSONDecodeError as exc:
        raise WorkflowCompatibilityError(
            f"workflow graph JSON is malformed: {exc.msg}; open the workflow "
            f"and re-save it"
        ) from exc
    if not isinstance(parsed, dict) or not parsed:
        raise WorkflowCompatibilityError(
            "workflow graph must be a non-empty API-format object"
        )
    for node_id, node in parsed.items():
        if not isinstance(node, dict) or "class_type" not in node:
            raise WorkflowCompatibilityError(
                f"workflow graph node {node_id!r} is not API format "
                f"(missing class_type); re-export the workflow from ComfyUI"
            )
    return parsed


def _parse_optional_node_map(node_map_text: Any) -> dict:
    """Parse the workflow's stored node map; allow empty ``{}`` as a valid map.

    The existing API accepts an empty ``node_map`` (``POST /api/workflows``
    defaults it to ``{}`` and a freshly-imported workflow may store an
    empty detected map). When no explicit mapped choices are required
    — text-to-image generation with no checkpoint, no LoRA, no sampler
    and no scheduler pick — the run layer never has anything to
    require the map to contain. Reject only the malformed cases:
    non-string text, unparseable JSON, or a non-object shape.
    """
    if node_map_text is None:
        return {}
    if not isinstance(node_map_text, str):
        raise WorkflowCompatibilityError(
            f"workflow node_map must be a JSON string, got {type(node_map_text).__name__}"
        )
    if not node_map_text.strip():
        # ``"{}"`` round-trips as empty, but a raw empty string is
        # ambiguous. Treat it as the explicit empty map so a row that
        # defaulted the column at creation does not trip the parser.
        return {}
    try:
        parsed = json.loads(node_map_text)
    except json.JSONDecodeError as exc:
        raise WorkflowCompatibilityError(
            f"workflow node_map JSON is malformed: {exc.msg}; remap the workflow"
        ) from exc
    if not isinstance(parsed, dict):
        raise WorkflowCompatibilityError(
            "workflow node_map must be a JSON object"
        )
    for slot, target in parsed.items():
        if not isinstance(slot, str) or not slot:
            raise WorkflowCompatibilityError(
                "workflow node_map keys must be non-empty strings"
            )
        if not isinstance(target, str) or not target:
            raise WorkflowCompatibilityError(
                f"workflow node_map[{slot!r}] must be a non-empty string"
            )
    return parsed


# -- Compatibility predicates (shared with the run preflight) ----------------


def first_unmapped_choice(node_map: dict, choices: tuple) -> tuple | None:
    """Return the first explicit choice that the graph would ignore."""
    return next(((slot, value, label) for slot, value, label in choices
                 if value and slot not in node_map), None)


def selected_workflow_choices(settings: dict, model: dict) -> tuple:
    """Explicit primary slots in run preflight order."""
    return (
        ("checkpoint", settings.get("checkpoint"), "base model"),
        ("lora_name", model["lora_name"], "LoRA"),
        ("sampler", settings.get("sampler"), "sampler"),
        ("scheduler", settings.get("scheduler"), "scheduler"),
    )


def effective_session_settings(model: dict, overrides: dict | None = None) -> dict:
    """Merge session defaults, model defaults and request settings in create order."""
    settings = {"width": 1024, "height": 1024, "steps": 8, "cfg": 1.0,
                "lora_strength": model["lora_strength"]}
    try:
        inherited = json.loads(model["settings"] or "{}")
    except (TypeError, ValueError) as exc:
        raise WorkflowCompatibilityError("model settings JSON is malformed") from exc
    if not isinstance(inherited, dict):
        inherited = {}
    settings.update({key: value for key, value in inherited.items()
                     if key != "composition_mode"})
    settings.update(overrides or {})
    return settings


def reference_compatibility_issue(
    node_map: dict, picks: list[list], anchors: list, will_shoot: bool,
) -> tuple | None:
    """Decide reference availability and exact photo/slot cardinality."""
    following = sum(not picked for picked in picks)
    if not anchors and following and not will_shoot:
        return ("anchor", following)
    if REFERENCE_SLOTS[0] not in node_map:
        return ("slot",)
    mapped = [slot for slot in REFERENCE_SLOTS if slot in node_map]
    for index, picked in enumerate(picks):
        if picked and len(picked) != len(mapped):
            return ("take_count", index, len(picked), len(mapped))
    if anchors and following and len(anchors) != len(mapped):
        return ("anchor_count", len(anchors), len(mapped))
    return None


def _is_reference_take_kind(take: Any) -> bool:
    """A take is a reference edit iff its ``reference`` flag is true."""
    if not isinstance(take, dict):
        return False
    return bool(take.get("reference", False))


def _take_uses_reference_slot(plan: dict) -> bool:
    """True iff any take in the plan asks for a reference edit."""
    if not isinstance(plan, dict):
        return False
    takes = plan.get("takes") or []
    if not isinstance(takes, list):
        return False
    for take in takes:
        if _is_reference_take_kind(take):
            return True
    return False


def _primary_workflow_row(primary_workflow_id: int) -> dict:
    """Read and shape-check the primary generation workflow row."""
    if (
        not isinstance(primary_workflow_id, int)
        or isinstance(primary_workflow_id, bool)
        or primary_workflow_id <= 0
    ):
        raise WorkflowCompatibilityError(
            f"primary workflow_id must be a positive integer, got "
            f"{primary_workflow_id!r}"
        )
    row = db.one(
        "SELECT id, graph, node_map FROM workflow WHERE id = ?",
        primary_workflow_id,
    )
    if row is None:
        raise WorkflowCompatibilityError(
            f"primary workflow {primary_workflow_id} does not exist"
        )
    graph = _parse_required_graph(row["graph"])
    node_map = _parse_optional_node_map(row["node_map"])
    return {"id": int(row["id"]), "graph": graph, "node_map": node_map}


def _reference_workflow_row(reference_workflow_id: int) -> dict:
    """Read and shape-check a reference workflow row.

    Reference rows are conditional on a reference take. Parsing is strict;
    slot, anchor and cardinality decisions belong to the shared predicate.
    """
    if (
        not isinstance(reference_workflow_id, int)
        or isinstance(reference_workflow_id, bool)
        or reference_workflow_id <= 0
    ):
        raise WorkflowCompatibilityError(
            f"reference_workflow_id must be a positive integer, got "
            f"{reference_workflow_id!r}"
        )
    row = db.one(
        "SELECT id, graph, node_map FROM workflow WHERE id = ?",
        reference_workflow_id,
    )
    if row is None:
        raise WorkflowCompatibilityError(
            f"reference workflow {reference_workflow_id} does not exist"
        )
    graph = _parse_required_graph(row["graph"])
    node_map = _parse_optional_node_map(row["node_map"])
    return {"id": int(row["id"]), "graph": graph, "node_map": node_map}


def assert_workflow_compatible(
    *,
    model_id: int,
    primary_workflow_id: int,
    reference_workflow_id: int | None,
    plan: dict | None,
    settings: dict | None = None,
    anchor_shot_ids: list | None = None,
) -> None:
    """Run the shared compatibility predicates the runner preflight enforces.

    The substance of ``_require_mapped_choices`` (primary graph / map
    shape) and ``_require_usable_reference`` (reference row presence,
    ``reference`` slot mapping, conditional anchor availability) is
    reused here as domain predicates rather than duplicated. The
    function is not a wrapper around the run preflight helpers; those
    raise ``HTTPException`` and are tied to pending ``shot`` rows, but
    the 4.3 boundary must work for a session that has no shots yet and
    must not import ``fastapi``.

    Reference rules apply only when a take in the plan actually wants a
    reference edit. An ordinary guided text-to-image creation has no
    reference take and never enters the reference branch.
    """
    model = db.one("SELECT * FROM model WHERE id = ?", model_id)
    if model is None:
        raise WorkflowCompatibilityError(f"model {model_id} does not exist")
    takes = (plan or {}).get("takes", [])
    will_shoot = plan is None or any(
        not _is_reference_take_kind(take) for take in takes
    )
    primary = _primary_workflow_row(primary_workflow_id)
    if will_shoot:
        overrides = settings if settings is not None else (plan or {}).get("settings")
        choices = selected_workflow_choices(
            effective_session_settings(model, overrides), model,
        )
        missing = first_unmapped_choice(primary["node_map"], choices)
        if missing:
            raise WorkflowCompatibilityError(
                f"primary workflow does not map the {missing[2]} slot; "
                f"{missing[1]!r} would be ignored"
            )
    if plan is not None and _take_uses_reference_slot(plan):
        if reference_workflow_id is None:
            raise WorkflowCompatibilityError(
                "plan includes a reference edit take, but no reference "
                "workflow is bound; assign a reference workflow"
            )
        reference = _reference_workflow_row(reference_workflow_id)
        picks = [take.get("reference_shot_ids") or [] for take in takes
                 if _is_reference_take_kind(take)]
        anchors = anchor_shot_ids if anchor_shot_ids is not None else (
            plan.get("anchor_shot_ids") or []
        )
        issue = reference_compatibility_issue(
            reference["node_map"], picks, anchors, will_shoot,
        )
        if issue:
            raise WorkflowCompatibilityError(
                f"reference workflow incompatible: {issue[0]} "
                f"(reference photos or slots must match available anchors)"
            )


# -- Resolver ----------------------------------------------------------------


def resolve_effective_workflow(
    model_id: int,
    override_workflow_id: int | None = None,
    *,
    reference_workflow_id: int | None = None,
    plan: dict | None = None,
    settings: dict | None = None,
    anchor_shot_ids: list | None = None,
) -> dict:
    """Pick the workflow row a freshly-bound session will run on.

    Resolution is server-side, ordered, and explicit:

      1. An ``override_workflow_id`` the caller passed wins.
      2. Otherwise the model's stored ``workflow_id`` is the default.
      3. Both missing raises ``WorkflowRequired`` with an actionable
    message.

    The character (model) is validated on both branches — a missing
    model fails closed, regardless of whether an override was passed,
    so the resolver cannot return a binding backed by a nonexistent
    row just because the caller also handed one in. A non-existent
    explicit override does NOT fall back to the model's default; the
    caller picked an id, the row is gone, and silently substituting
    the default is the bug the spec calls out.

    The resolver returns the closed canonical binding — the same
    shape the authoring schema validates on the wire — and runs
    ``assert_workflow_compatible`` on the resolved row before
    returning. A guided session created against an invalid primary or
    reference row is refused before any write happens.

    The ``reference_workflow_id`` and ``plan`` are optional and feed
    ``assert_workflow_compatible``. The resolver does not require a
    reference workflow unless the plan asks for a reference take,
    matching the run layer's conditional preflight.
    """
    if (
        model_id is None
        or not isinstance(model_id, int)
        or isinstance(model_id, bool)
        or model_id <= 0
    ):
        raise WorkflowCompatibilityError(
            f"resolve_effective_workflow requires a positive integer model_id, "
            f"got {model_id!r}"
        )

    # Model is validated on both branches. ``db.one`` returns None for
    # a missing row, and the model cannot be resolved any further when
    # the FK ON DELETE SET NULL has nulled it.
    model = db.one("SELECT id, workflow_id FROM model WHERE id = ?", model_id)
    if model is None:
        raise WorkflowCompatibilityError(
            f"model {model_id} does not exist; cannot resolve a workflow"
        )

    if override_workflow_id is not None:
        if (
            not isinstance(override_workflow_id, int)
            or isinstance(override_workflow_id, bool)
            or override_workflow_id <= 0
        ):
            raise WorkflowCompatibilityError(
                f"override workflow_id must be a positive integer, got "
                f"{override_workflow_id!r}"
            )
        row = db.one("SELECT id FROM workflow WHERE id = ?", override_workflow_id)
        if row is None:
            raise WorkflowCompatibilityError(
                f"override workflow_id {override_workflow_id} does not exist; "
                f"select an existing Advanced workflow or assign a default to the model"
            )
        primary_workflow_id = int(row["id"])
    else:
        default_id = model["workflow_id"]
        if default_id is None:
            raise WorkflowRequired(
                f"model {model_id} has no default workflow assigned; assign a "
                f"workflow to the character or select an Advanced workflow override"
            )
        row = db.one("SELECT id FROM workflow WHERE id = ?", int(default_id))
        if row is None:
            # The FK is ON DELETE SET NULL, but a row that survived a
            # future migration that drops the cascade still needs to
            # fail visibly. An unreachable default is a real resolver
            # failure, not a silent ``WorkflowRequired``.
            raise WorkflowCompatibilityError(
                f"model {model_id} default workflow {default_id} no longer "
                f"exists; reassign a workflow to the model or pick an Advanced "
                f"override"
            )
        primary_workflow_id = int(row["id"])

    # The compatibility predicates run on the resolved row before any
    # binding is returned, so the guided creation step never sees a
    # half-truth.
    assert_workflow_compatible(
        model_id=model_id,
        primary_workflow_id=primary_workflow_id,
        reference_workflow_id=reference_workflow_id,
        plan=plan,
        settings=settings,
        anchor_shot_ids=anchor_shot_ids,
    )
    return build_workflow_binding(primary_workflow_id)


# -- Binding ----------------------------------------------------------------


def build_workflow_binding(workflow_id: int) -> dict:
    """Read the workflow row and build the closed canonical binding.

    The binding's four keys are the exact ones the authoring schema
    (``REQUIRED_WORKFLOW_BINDING_KEYS``) validates on the wire:

      * ``workflow_id`` — the int id of the row, the only way to
        address it;
      * ``kind`` — the **stored** ``workflow.kind`` text, including
        ``""`` for the pre-kinds rows that pre-date the column's
        tagging era; never lowercased, never coerced, never
        fabricated;
      * ``graph_digest`` — ``canonical_digest`` of the parsed API
        graph;
      * ``node_map_digest`` — ``canonical_digest`` of the parsed node
        map.

    The two digests are computed independently. A workflow whose graph
    and map are byte-for-byte equal after a re-save keeps both
    digests; one that differs in any node or any slot sees a
    different digest on the side that moved. The digests are not
    double-hashed: ``canonical_digest`` already produces a 64-char
    lowercase hex SHA-256 over the canonicalised JSON-as-Python value,
    and the binding stores that digest verbatim.
    """
    if (
        not isinstance(workflow_id, int)
        or isinstance(workflow_id, bool)
        or workflow_id <= 0
    ):
        raise WorkflowCompatibilityError(
            f"workflow_id must be a positive integer, got {workflow_id!r}"
        )
    row = db.one(
        "SELECT id, kind, graph, node_map FROM workflow WHERE id = ?",
        workflow_id,
    )
    if row is None:
        raise WorkflowCompatibilityError(
            f"workflow {workflow_id} no longer exists; choose another"
        )
    graph = _parse_required_graph(row["graph"])
    node_map = _parse_optional_node_map(row["node_map"])
    kind = row["kind"]
    if not isinstance(kind, str):
        raise WorkflowCompatibilityError("workflow kind must be a string")
    return {
        "workflow_id": int(row["id"]),
        "kind": kind,
        "graph_digest": resource_store.canonical_digest(graph),
        "node_map_digest": resource_store.canonical_digest(node_map),
    }


# -- Drift validator --------------------------------------------------------


def _load_session_workflow_row(session_id: int) -> dict | None:
    """Read the workflow row the session currently points to.

    Returns ``None`` when the session has no primary workflow. The
    drift validator treats every ``None`` the same way: the stored
    binding cannot have come from a row that does not exist any more.
    """
    if (
        not isinstance(session_id, int)
        or isinstance(session_id, bool)
        or session_id <= 0
    ):
        raise WorkflowChanged(
            f"session_id must be a positive integer, got {session_id!r}"
        )
    session = db.one("SELECT workflow_id FROM session WHERE id = ?", session_id)
    if session is None:
        raise WorkflowChanged(f"session {session_id} no longer exists")
    wf_id = session["workflow_id"]
    if wf_id is None:
        return None
    return db.one(
        "SELECT id, kind, graph, node_map FROM workflow WHERE id = ?",
        int(wf_id),
    )


def _load_plan_binding(plan_row: dict) -> dict | None:
    """Extract the closed binding from a stored plan row.

    A plan with no ``authoring`` block is the pre-authoring-expert
    path: the validator returns ``None`` and skips every check, so
    legacy layers keep their old contract.

    A plan whose stored ``plan_json`` cannot be parsed is unreadable
    data, not drift. The validator raises ``StoredPlanUnreadable``
    instead of ``WorkflowChanged``, so the caller (and the existing
    ``PreparedTakePersistenceError`` category in
    ``_load_current_resource_plan``) classifies it as the existing
    persistence-error kind, not as a workflow-binding drift.

    A plan with an ``authoring`` block whose ``workflow_binding``
    shape is wrong (missing, non-object, wrong workflow_id kind,
    non-string kind, non-canonical digests) is drift, because the
    binding the plan froze cannot be the binding the validator is
    asked to validate.
    """
    if not isinstance(plan_row, dict):
        raise StoredPlanUnreadable("stored plan row is not an object")
    plan_json = plan_row.get("plan_json")
    if not isinstance(plan_json, str):
        raise StoredPlanUnreadable("stored plan_json is not a string")
    try:
        plan = json.loads(plan_json)
    except json.JSONDecodeError as exc:
        raise StoredPlanUnreadable(
            f"stored plan_json is malformed: {exc.msg}"
        ) from exc
    if not isinstance(plan, dict):
        raise StoredPlanUnreadable("stored plan_json is not an object")
    if "authoring" not in plan:
        return None
    auth = plan["authoring"]
    if not isinstance(auth, dict):
        raise WorkflowChanged("plan.authoring is not an object")
    binding = auth.get("workflow_binding")
    if not isinstance(binding, dict):
        raise WorkflowChanged(
            "plan.authoring.workflow_binding is missing or not an object"
        )
    expected = {"workflow_id", "kind", "graph_digest", "node_map_digest"}
    if set(binding.keys()) != expected:
        raise WorkflowChanged(
            "plan.authoring.workflow_binding does not contain exactly the four "
            "canonical keys"
        )
    if (
        not isinstance(binding["workflow_id"], int)
        or isinstance(binding["workflow_id"], bool)
        or binding["workflow_id"] <= 0
    ):
        raise WorkflowChanged(
            "plan.authoring.workflow_binding.workflow_id is not a positive integer"
        )
    if not isinstance(binding["kind"], str):
        raise WorkflowChanged(
            "plan.authoring.workflow_binding.kind is not a string"
        )
    for digest_field in ("graph_digest", "node_map_digest"):
        value = binding[digest_field]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise WorkflowChanged(
                f"plan.authoring.workflow_binding.{digest_field} is not a "
                f"64-character lowercase hex string"
            )
    return {
        "workflow_id": int(binding["workflow_id"]),
        "kind": str(binding["kind"]),
        "graph_digest": str(binding["graph_digest"]),
        "node_map_digest": str(binding["node_map_digest"]),
    }


def _parse_live_graph_for_drift(graph_text: Any) -> dict:
    """Parse the live row's graph with the strict parser.

    A graph that cannot be parsed at validation time is the same
    drift as a graph whose parsed content moved: the binding the plan
    froze was a digest of one specific graph, and the row the session
    points to no longer carries that graph. The validator raises
    ``WorkflowChanged`` rather than ``WorkflowCompatibilityError`` so
    the app maps the failure to 409 ``workflow_changed`` rather than
    to the resolution-time 422.
    """
    try:
        return _parse_required_graph(graph_text)
    except WorkflowCompatibilityError as exc:
        raise WorkflowChanged(
            f"session workflow graph could not be parsed after binding: "
            f"{exc.message}; start a new session to rebind"
        ) from exc


def _parse_live_node_map_for_drift(node_map_text: Any) -> dict:
    """Parse the live row's node map with the optional parser.

    Same drift-vs-compatibility classification as the graph: a live row
    whose stored map is now unreadable is the same drift, so the
    validator raises ``WorkflowChanged``.
    """
    try:
        return _parse_optional_node_map(node_map_text)
    except WorkflowCompatibilityError as exc:
        raise WorkflowChanged(
            f"session workflow node_map could not be parsed after binding: "
            f"{exc.message}; start a new session to rebind"
        ) from exc


def validate_workflow_binding_against_session(session_id: int) -> dict | None:
    """Read-only guard: the stored binding must still match the live row.

    Returns the live ``workflow_id`` when the plan's binding agrees
    with the workflow row the session currently points to. Returns
    ``None`` when the session has no ``authoring`` block, because the
    pre-authoring-expert path is exactly the one the spec keeps
    outside the binding contract. Raises ``WorkflowChanged`` for any
    drift: missing session, missing workflow row, malformed binding,
    kind drift, graph drift, node_map drift, or stored workflow id
    that disagrees with ``session.workflow_id``. Raises
    ``StoredPlanUnreadable`` for unreadable plan JSON, so the caller
    classifies it as the existing persistence error and not as drift.

    The function is read-only. It never writes and never reads
    ``model.workflow_id``: the whole point of freezing the binding is
    to survive a default swap on the character.
    """
    plan_row = db.one(
        "SELECT plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    if plan_row is None:
        # No saved plan: nothing to validate. The contract is "the
        # stored binding, when present, agrees with the live row"; an
        # absent binding has nothing to compare.
        return None
    binding = _load_plan_binding(plan_row)
    if binding is None:
        return None

    live_row = _load_session_workflow_row(session_id)
    if live_row is None:
        raise WorkflowChanged(
            f"session {session_id} workflow_binding is bound to workflow "
            f"{binding['workflow_id']}, but the session no longer points to "
            f"a primary workflow row; start a new session to rebind"
        )
    if int(binding["workflow_id"]) != int(live_row["id"]):
        raise WorkflowChanged(
            f"session {session_id} workflow_binding workflow_id is "
            f"{binding['workflow_id']}, but session.workflow_id points to "
            f"{int(live_row['id'])}; start a new session to rebind"
        )
    live_graph = _parse_live_graph_for_drift(live_row["graph"])
    live_node_map = _parse_live_node_map_for_drift(live_row["node_map"])
    live_kind = live_row["kind"]
    if not isinstance(live_kind, str):
        raise WorkflowChanged(
            f"session {session_id} workflow kind is not a string; "
            "start a new session to rebind"
        )
    live_graph_digest = resource_store.canonical_digest(live_graph)
    live_node_map_digest = resource_store.canonical_digest(live_node_map)
    if live_kind != binding["kind"]:
        raise WorkflowChanged(
            f"session {session_id} workflow {int(live_row['id'])} kind is "
            f"{live_kind!r}, but the plan binding froze "
            f"{binding['kind']!r}; start a new session to rebind"
        )
    if live_graph_digest != binding["graph_digest"]:
        raise WorkflowChanged(
            f"session {session_id} workflow {int(live_row['id'])} graph "
            f"has changed since the plan froze its binding; start a new "
            f"session to rebind"
        )
    if live_node_map_digest != binding["node_map_digest"]:
        raise WorkflowChanged(
            f"session {session_id} workflow {int(live_row['id'])} node_map "
            f"has changed since the plan froze its binding; start a new "
            f"session to rebind"
        )
    return {"workflow_id": int(live_row["id"])}
