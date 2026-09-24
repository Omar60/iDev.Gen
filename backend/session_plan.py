"""Session-plan persistence and validation for the resource-v1 composition
mode (task 3.1 of ``adopt-resource-session-planning``).

This module is the small additive surface the routes and the rest of the
app call against. Its contract is deliberately narrow:

  * A draft plan is identified by its session (one current draft per
    session). The plan is JSON in the ``session_plan`` table.

  * The save is a compare-and-swap on ``plan_revision``: the caller
    passes the revision it last read, the save refuses (with a
    ``PlanRevisionStale``) if the actual revision differs, and otherwise
    bumps the revision by one. The bump happens inside a SQLite
    transaction so a concurrent save either wins outright or is refused
    with no partial write.

  * The plan's shape is validated. Malformed plans, missing selected
    revisions and out-of-range scopes raise ``PlanValidationError`` with
    a readable message that names the offending field. The validation
    runs BEFORE the database is touched, so a refused save never leaves
    a half-written plan row behind.

  * A session is only allowed to save a plan when it is in
    ``resource-v1`` composition mode. Legacy sessions cannot reach
    ``save_draft`` because the route refuses the request first.

What this module does NOT do: it does not prepare prompts, queue
shots, or invent take fields beyond the stable take IDs. Those are
later tasks. 3.2 adds the effective-wardrobe resolver, which is a
pure function of a validated plan; legacy wardrobe composition and
legacy sessions remain untouched.

Task 3.3 adds three small pieces on top of the 3.1 surface:

  * A structural-conflict detector
    (``detect_resource_constant_conflicts``) that walks every
    selected resource's payload through
    ``resource_prompts.PREPARATION_FIELD_MAPPING`` and emits one
    neutral marker per non-empty ``descriptive_input`` field. The
    marker is a single neutral kind regardless of the field name
    or the value's content. The marker shows whichever of the
    plan's ``look`` and ``initial_wardrobe`` is set — both or
    one — and explicitly declares that human review is
    required; the detector does NOT decide by content whether a
    field competes with the look or with the initial_wardrobe.
    The plan keeps ONLY the immutable revision triple
    (``library_key``, ``source_id``, ``content_digest``); the
    payload itself lives in ``asset_revision.payload`` and is
    NOT carried in the plan, in any provenance field, or in the
    session row.

  * An explicit-invalidation pass that runs at the end of every
    successful ``save_draft``. On a successful save, every
    prepared_take row for the session whose status is ``pending``
    or ``ready`` AND whose ``plan_revision`` differs from the new
    plan revision is transitioned to ``invalidated`` — those are
    ungenerated work that was prepared under a now-stale plan
    revision. Rows in ``pending`` or ``ready`` that already sit at
    the new plan revision are preserved (a future task 3.4 may
    write such rows and the pass must not invalidate them).
    Rows already in ``generated`` or ``invalidated`` status are
    immutable history: the pass leaves them alone and never
    rewrites their prompt, provenance or linked shot.

  * A generated-continuity freeze. Once any take reaches generated
    status, a save that changes the look, initial wardrobe, selected
    resources, authoring scene anchor, variation policy, workflow
    binding, or complete saved-look snapshot is refused with
    ``PlanConstantsFrozenAfterGenerated``. The refusal leaves the
    plan, prepared_take rows, and linked shots unchanged. Brief edits
    and explicit scoped wardrobe changes remain allowed.
"""
from __future__ import annotations

import json
import sys
from typing import Any

import db

try:
    from backend import workflow_binding
except ImportError:
    import workflow_binding

if __name__ == "backend.session_plan" and "session_plan" not in sys.modules:
    sys.modules["session_plan"] = sys.modules[__name__]
elif __name__ == "session_plan" and "backend.session_plan" not in sys.modules:
    sys.modules["backend.session_plan"] = sys.modules[__name__]


# -- Constants --------------------------------------------------------------


# The single explicit composition mode this module serves today. A future
# task can add a sibling constant; the validation here refuses anything
# it does not recognise, so an unknown mode cannot sneak in through a
# stale call site.
MODE_RESOURCE_V1 = "resource-v1"

# The two wardrobe-change scopes the draft recognises. ``this_take`` is
# an isolated override; ``from_here`` is a persistent change that walks
# the ordered takes until superseded. Anything else is rejected.
WARDROBE_SCOPE_THIS_TAKE = "this_take"
WARDROBE_SCOPE_FROM_HERE = "from_here"
VALID_WARDROBE_SCOPES: frozenset[str] = frozenset(
    {WARDROBE_SCOPE_THIS_TAKE, WARDROBE_SCOPE_FROM_HERE}
)


AUTHORING_MODE_AUTOMATIC = "automatic"
AUTHORING_MODE_MANUAL = "manual"

PLAN_AUTHORING_KIND_PRE_AUTHORING_EXPERT = "pre_authoring_expert"
PLAN_AUTHORING_KIND_MANUAL = "manual"
PLAN_AUTHORING_KIND_AUTOMATIC = "automatic"

REQUIRED_AUTHORING_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "mode",
    "brief",
    "scene_anchor",
    "workflow_binding",
    "variation_policy",
    "shared_state",
    "evidence",
    "look_snapshot",
    "wardrobe_progression",
})
REQUIRED_SCENE_ANCHOR_KEYS: frozenset[str] = frozenset({
    "library_key",
    "source_id",
    "content_digest",
})
REQUIRED_WORKFLOW_BINDING_KEYS: frozenset[str] = frozenset({
    "workflow_id",
    "kind",
    "graph_digest",
    "node_map_digest",
})
REQUIRED_VARIATION_POLICY_KEYS: frozenset[str] = frozenset({
    "camera",
    "framing",
    "pose",
    "expression",
})
REQUIRED_SHARED_STATE_KEYS: frozenset[str] = frozenset({
    "look",
    "initial_wardrobe",
})
VALID_AUTHORING_ORIGINS: frozenset[str] = frozenset({
    "none",
    "user",
    "assistant",
    "assistant_edited",
    "saved_look",
})
REQUIRED_EVIDENCE_KEYS: frozenset[str] = frozenset({
    "id",
    "kind",
    "input",
    "output",
    "accepted",
})
REQUIRED_EVIDENCE_INPUT_KEYS: frozenset[str] = frozenset({
    "messages",
    "model",
    "parameters",
    "plan_revision",
})
REQUIRED_LOOK_SNAPSHOT_KEYS: frozenset[str] = frozenset({
    "look_id",
    "version",
    "content_digest",
    "appearance",
    "outfit",
})
REQUIRED_LOOK_SNAPSHOT_OUTFIT_KEYS: frozenset[str] = frozenset({
    "outfit_key",
    "garments",
})
REQUIRED_GARMENT_KEYS: frozenset[str] = frozenset({
    "key",
    "wording",
    "aside",
})
REQUIRED_WARDROBE_PROGRESSION_KEYS: frozenset[str] = frozenset({
    "source_look_digest",
    "start_take_id",
    "end_take_id",
    "stage_indices",
    "applied_revision",
})


# -- Errors -----------------------------------------------------------------


class PlanValidationError(ValueError):
    """The draft is malformed or references a missing revision.

    The message names the offending field so a caller (or a 422 body)
    can tell the operator exactly what to fix. Raised BEFORE the
    database is touched, so a refused save never leaves a half-written
    plan row behind.
    """


class PlanRevisionStale(Exception):
    """The save's expected revision does not match the current row.

    A browser that read the plan at revision N, sat idle while another
    tab saved revision N+1, and then tried to save its own copy at
    revision N is refused here. The current row is left intact.
    """


class SessionNotInResourceMode(Exception):
    """The session is not in resource-v1 mode and cannot hold a plan.

    Legacy sessions have no row in ``session_plan``; attempting to
    save or read a plan for one is a request-shape error, not a
    404. The route maps it to a 400 with the current mode in the
    message so the operator can see why the request was refused.
    """


class SessionNotFound(Exception):
    """No session row exists for the given id.

    Surfaced separately from the not-in-resource-mode error so a 404
    is mapped to a 404 and not a 400.
    """


class PlanConstantsFrozenAfterGenerated(Exception):
    """The save would change constants after a take is generated.

    Once a prepared_take has been generated, its continuity inputs
    are history: a later save that rewrites ``look``,
    ``initial_wardrobe``, ``selected_resources``, or an authoring
    plan's ``scene_anchor``, ``variation_policy``,
    ``workflow_binding`` or complete ``look_snapshot`` would
    describe a state the photograph did not use. The guard refuses
    the save before any write and preserves the plan, prepared_take
    rows, and linked shots.

    Brief edits, wardrobe changes and take reorders remain legal
    after a generated take. Explicit wardrobe changes still
    invalidate affected ungenerated prepared_take rows.
    """


class PlanOwnershipConflict(Exception):
    """A generic plan save attempted to create, delete, or mutate server-owned authoring state."""


class PreparedTakeConflict(Exception):
    """A prepared-take snapshot would overwrite immutable history."""


class PreparationAuthorityConflict(PreparedTakeConflict):
    """A preparation request violates the mode-specific authoring authority matrix."""


class PreparedTakePersistenceError(Exception):
    """A prepared-take write failed and was rolled back."""


class PlanReviewNotApproved(Exception):
    """The plan revision review has not been authoritatively approved for submission."""


class AuthoringEvidenceInvalid(PreparedTakeConflict):
    """Authoring prepared take evidence is missing, malformed, fabricated, or inconsistent."""

    def __init__(self, message: str) -> None:
        clean_msg = (
            message
            if message.startswith("authoring_evidence_invalid")
            else f"authoring_evidence_invalid: {message}"
        )
        super().__init__(clean_msg)
        self.code = "authoring_evidence_invalid"
        self.message = clean_msg


AUTHORING_EVIDENCE_PUBLIC_MESSAGE = (
    "authoring_evidence_invalid: Prepared authoring evidence is invalid."
)


class ValidatedAuthoringEvidence(dict):
    """Authoritative validated evidence for a prepared take."""

    def __init__(
        self,
        snapshot: Mapping[str, Any],
        *,
        authoring_evidence: dict | None = None,
        is_authoring: bool = True,
    ) -> None:
        super().__init__(snapshot)
        self.authoring_evidence = authoring_evidence
        self.is_authoring = is_authoring


def validate_authoring_prepared_evidence(
    session_id: int,
    plan_revision: int,
    take_id: str,
    row: Mapping[str, Any] | None = None,
) -> ValidatedAuthoringEvidence:
    """Validate server-owned authoring evidence for an authoring-v1 prepared take snapshot."""
    import resource_preparation
    return resource_preparation.validate_authoring_prepared_evidence(
        session_id, plan_revision, take_id, row=row,
    )


# -- Validation -------------------------------------------------------------


def compose_saved_look_wardrobe(outfit: dict) -> str:
    """Compose canonical fully-worn wardrobe string from look_snapshot outfit."""
    if not isinstance(outfit, dict):
        raise PlanValidationError(f"outfit must be a dict, got {type(outfit).__name__}")
    garments = outfit.get("garments")
    if not isinstance(garments, list) or len(garments) == 0:
        raise PlanValidationError("outfit.garments must be a non-empty list")
    wordings: list[str] = []
    for idx, g in enumerate(garments):
        if not isinstance(g, dict):
            raise PlanValidationError(f"garment[{idx}] must be a dict")
        w = g.get("wording")
        if not isinstance(w, str) or not w or w != w.strip():
            raise PlanValidationError(
                f"garment[{idx}].wording must be a non-empty string with no leading or trailing whitespace, got {w!r}"
            )
        wordings.append(w)
    if len(wordings) == 1:
        return f"She wears {wordings[0]}."
    all_but_last = ", ".join(wordings[:-1])
    return f"She wears {all_but_last}, and {wordings[-1]}."


def validate_authoring_count(value: Any) -> int:
    """Validate the count for guided allocation or authoring plan growth."""
    if type(value) is not int or not 1 <= value <= 500:
        raise PlanValidationError("authoring take count must be an integer from 1 to 500")
    return value


def validate_authoring_brief(value: Any) -> str:
    """Validate a present authoring brief before allocation or persistence."""
    if not isinstance(value, str):
        raise PlanValidationError(f"authoring.brief must be a string, got {type(value).__name__}")
    if len(value) > 2000:
        raise PlanValidationError(f"authoring.brief must be at most 2000 characters, got {len(value)}")
    return value


def validate_authoring_block(auth: Any, plan: dict, *, check_effective: bool = True) -> dict:
    """Validate closed authoring-v1 block and return a normalized copy."""
    if not isinstance(auth, dict) or not auth:
        raise PlanValidationError("plan authoring block must be a non-empty object")

    missing = REQUIRED_AUTHORING_KEYS - set(auth.keys())
    if missing:
        raise PlanValidationError(f"authoring is missing required keys: {sorted(missing)}")
    extra = set(auth.keys()) - REQUIRED_AUTHORING_KEYS
    if extra:
        raise PlanValidationError(f"authoring contains unknown keys: {sorted(extra)}")

    # 1. schema_version
    schema_ver = auth["schema_version"]
    if type(schema_ver) is not int or isinstance(schema_ver, bool) or schema_ver != 1:
        raise PlanValidationError(f"authoring.schema_version must be 1, got {schema_ver!r}")

    # 2. mode
    mode = auth["mode"]
    if not isinstance(mode, str) or mode not in (AUTHORING_MODE_AUTOMATIC, AUTHORING_MODE_MANUAL):
        raise PlanValidationError(
            f"authoring.mode must be {AUTHORING_MODE_AUTOMATIC!r} or {AUTHORING_MODE_MANUAL!r}, got {mode!r}"
        )

    # 3. brief
    brief = validate_authoring_brief(auth["brief"])

    # 4. scene_anchor
    anchor = auth["scene_anchor"]
    if not isinstance(anchor, dict):
        raise PlanValidationError(f"authoring.scene_anchor must be an object, got {type(anchor).__name__}")
    if set(anchor.keys()) != REQUIRED_SCENE_ANCHOR_KEYS:
        raise PlanValidationError(
            f"authoring.scene_anchor must contain exactly {sorted(REQUIRED_SCENE_ANCHOR_KEYS)}, got {sorted(anchor.keys())}"
        )
    for k in ("library_key", "source_id", "content_digest"):
        v = anchor[k]
        if not isinstance(v, str) or not v:
            raise PlanValidationError(f"authoring.scene_anchor.{k} must be a non-empty string, got {v!r}")
    cd = anchor["content_digest"]
    if len(cd) != 64 or not all(c in "0123456789abcdef" for c in cd):
        raise PlanValidationError(
            f"authoring.scene_anchor.content_digest must be 64 lowercase hex characters, got {cd!r}"
        )
    selected_triples = {
        (r["library_key"], r["source_id"], r["content_digest"])
        for r in plan.get("selected_resources", [])
    }
    if (anchor["library_key"], anchor["source_id"], anchor["content_digest"]) not in selected_triples:
        raise PlanValidationError(
            f"authoring.scene_anchor triple ({anchor['library_key']!r}, {anchor['source_id']!r}, "
            f"{anchor['content_digest']!r}) must appear in plan.selected_resources"
        )
    norm_anchor = {
        "library_key": anchor["library_key"],
        "source_id": anchor["source_id"],
        "content_digest": anchor["content_digest"],
    }

    # 5. workflow_binding
    wf = auth["workflow_binding"]
    if not isinstance(wf, dict):
        raise PlanValidationError(f"authoring.workflow_binding must be an object, got {type(wf).__name__}")
    if set(wf.keys()) != REQUIRED_WORKFLOW_BINDING_KEYS:
        raise PlanValidationError(
            f"authoring.workflow_binding must contain exactly {sorted(REQUIRED_WORKFLOW_BINDING_KEYS)}, got {sorted(wf.keys())}"
        )
    wf_id = wf["workflow_id"]
    if type(wf_id) is not int or isinstance(wf_id, bool) or wf_id <= 0:
        raise PlanValidationError(f"authoring.workflow_binding.workflow_id must be a positive integer, got {wf_id!r}")
    wf_kind = wf["kind"]
    # A stored kind is whatever ``workflow.kind`` says, including the empty
    # string that pre-dates the tagging era. The resolver (4.3) reads the
    # row's stored ``kind`` verbatim and the validator compares it back;
    # forbidding ``""`` here would reject every legacy untagged workflow a
    # session froze its plan against. ``kind`` is a label, not an enum.
    if not isinstance(wf_kind, str):
        raise PlanValidationError(f"authoring.workflow_binding.kind must be a string, got {wf_kind!r}")
    gd = wf["graph_digest"]
    if not isinstance(gd, str) or len(gd) != 64 or not all(c in "0123456789abcdef" for c in gd):
        raise PlanValidationError(
            f"authoring.workflow_binding.graph_digest must be 64 lowercase hex characters, got {gd!r}"
        )
    nmd = wf["node_map_digest"]
    if not isinstance(nmd, str) or len(nmd) != 64 or not all(c in "0123456789abcdef" for c in nmd):
        raise PlanValidationError(
            f"authoring.workflow_binding.node_map_digest must be 64 lowercase hex characters, got {nmd!r}"
        )
    norm_wf = {
        "workflow_id": wf_id,
        "kind": wf_kind,
        "graph_digest": gd,
        "node_map_digest": nmd,
    }

    # 6. variation_policy
    policy = auth["variation_policy"]
    if not isinstance(policy, dict):
        raise PlanValidationError(f"authoring.variation_policy must be an object, got {type(policy).__name__}")
    if set(policy.keys()) != REQUIRED_VARIATION_POLICY_KEYS:
        raise PlanValidationError(
            f"authoring.variation_policy must contain exactly {sorted(REQUIRED_VARIATION_POLICY_KEYS)}, got {sorted(policy.keys())}"
        )
    norm_policy: dict[str, dict] = {}
    for dim in ("camera", "framing", "pose", "expression"):
        dim_val = policy[dim]
        if not isinstance(dim_val, dict):
            raise PlanValidationError(f"authoring.variation_policy.{dim} must be an object, got {type(dim_val).__name__}")
        d_mode = dim_val.get("mode")
        if not isinstance(d_mode, str):
            raise PlanValidationError(f"authoring.variation_policy.{dim}.mode must be 'vary' or 'fixed', got {d_mode!r}")
        if d_mode == "vary":
            if set(dim_val.keys()) != {"mode"}:
                raise PlanValidationError(
                    f"authoring.variation_policy.{dim} with mode 'vary' must contain only 'mode', got {sorted(dim_val.keys())}"
                )
            norm_policy[dim] = {"mode": "vary"}
        elif d_mode == "fixed":
            if set(dim_val.keys()) != {"mode", "value", "value_origin"}:
                raise PlanValidationError(
                    f"authoring.variation_policy.{dim} with mode 'fixed' must contain exactly ['mode', 'value', 'value_origin'], got {sorted(dim_val.keys())}"
                )
            f_val = dim_val["value"]
            if not isinstance(f_val, str) or not f_val:
                raise PlanValidationError(f"authoring.variation_policy.{dim}.value must be a non-empty string, got {f_val!r}")
            f_origin = dim_val["value_origin"]
            if not isinstance(f_origin, str) or f_origin != "user":
                raise PlanValidationError(f"authoring.variation_policy.{dim}.value_origin must be 'user', got {f_origin!r}")
            norm_policy[dim] = {"mode": "fixed", "value": f_val, "value_origin": "user"}
        else:
            raise PlanValidationError(f"authoring.variation_policy.{dim}.mode must be 'vary' or 'fixed', got {d_mode!r}")

    # 7. evidence
    ev_list = auth["evidence"]
    if not isinstance(ev_list, list):
        raise PlanValidationError(f"authoring.evidence must be a list, got {type(ev_list).__name__}")
    seen_ev_ids: set[str] = set()
    norm_ev: list[dict] = []
    for idx, rec in enumerate(ev_list):
        if not isinstance(rec, dict):
            raise PlanValidationError(f"authoring.evidence[{idx}] must be an object, got {type(rec).__name__}")
        if set(rec.keys()) != REQUIRED_EVIDENCE_KEYS:
            raise PlanValidationError(
                f"authoring.evidence[{idx}] must contain exactly {sorted(REQUIRED_EVIDENCE_KEYS)}, got {sorted(rec.keys())}"
            )
        ev_id = rec["id"]
        if not isinstance(ev_id, str) or not ev_id:
            raise PlanValidationError(f"authoring.evidence[{idx}].id must be a non-empty string, got {ev_id!r}")
        if ev_id in seen_ev_ids:
            raise PlanValidationError(f"duplicate evidence id {ev_id!r}")
        seen_ev_ids.add(ev_id)
        if not isinstance(rec["kind"], str) or rec["kind"] != "shared_choices":
            raise PlanValidationError(f"authoring.evidence[{idx}].kind must be 'shared_choices', got {rec['kind']!r}")
        inp = rec["input"]
        if not isinstance(inp, dict) or set(inp.keys()) != REQUIRED_EVIDENCE_INPUT_KEYS:
            raise PlanValidationError(
                f"authoring.evidence[{idx}].input must contain exactly {sorted(REQUIRED_EVIDENCE_INPUT_KEYS)}"
            )
        msgs = inp["messages"]
        if not isinstance(msgs, list):
            raise PlanValidationError(f"authoring.evidence[{idx}].input.messages must be a list")
        norm_msgs: list[dict] = []
        for m_idx, m in enumerate(msgs):
            if not isinstance(m, dict) or set(m.keys()) != {"role", "content"}:
                raise PlanValidationError(
                    f"authoring.evidence[{idx}].input.messages[{m_idx}] must contain exactly ['content', 'role']"
                )
            if not isinstance(m["role"], str) or m["role"] not in ("system", "user", "assistant") or not isinstance(m["content"], str):
                raise PlanValidationError(
                    f"authoring.evidence[{idx}].input.messages[{m_idx}] role must be system|user|assistant and content must be string"
                )
            norm_msgs.append({"role": m["role"], "content": m["content"]})
        model = inp["model"]
        if not isinstance(model, str) or not model:
            raise PlanValidationError(f"authoring.evidence[{idx}].input.model must be a non-empty string")
        params = inp["parameters"]
        if not isinstance(params, dict):
            raise PlanValidationError(f"authoring.evidence[{idx}].input.parameters must be an object")
        plan_rev = inp["plan_revision"]
        if type(plan_rev) is not int or isinstance(plan_rev, bool) or plan_rev <= 0:
            raise PlanValidationError(
                f"authoring.evidence[{idx}].input.plan_revision must be a positive integer, got {plan_rev!r}"
            )
        outp = rec["output"]
        if not isinstance(outp, dict):
            raise PlanValidationError(f"authoring.evidence[{idx}].output must be an object")
        out_keys = set(outp.keys())
        if not out_keys or not out_keys.issubset({"look", "initial_wardrobe"}):
            raise PlanValidationError(
                f"authoring.evidence[{idx}].output keys must be a non-empty subset of ['look', 'initial_wardrobe'], got {sorted(out_keys)}"
            )
        norm_outp: dict[str, str] = {}
        for k in sorted(out_keys):
            v = outp[k]
            if not isinstance(v, str):
                raise PlanValidationError(f"authoring.evidence[{idx}].output.{k} must be a string")
            norm_outp[k] = v
        acc = rec["accepted"]
        if not isinstance(acc, dict):
            raise PlanValidationError(f"authoring.evidence[{idx}].accepted must be an object")
        if set(acc.keys()) != out_keys:
            raise PlanValidationError(
                f"authoring.evidence[{idx}].accepted keys must match output keys exactly, got {sorted(acc.keys())} != {sorted(out_keys)}"
            )
        norm_acc: dict[str, str] = {}
        for k in sorted(out_keys):
            v = acc[k]
            if not isinstance(v, str):
                raise PlanValidationError(f"authoring.evidence[{idx}].accepted.{k} must be a string")
            norm_acc[k] = v
        norm_ev.append({
            "id": ev_id,
            "kind": "shared_choices",
            "input": {
                "messages": norm_msgs,
                "model": model,
                "parameters": dict(params),
                "plan_revision": plan_rev,
            },
            "output": norm_outp,
            "accepted": norm_acc,
        })

    # 8. look_snapshot
    ls = auth["look_snapshot"]
    norm_ls = None
    if ls is not None:
        if not isinstance(ls, dict):
            raise PlanValidationError(f"authoring.look_snapshot must be null or an object, got {type(ls).__name__}")
        if set(ls.keys()) != REQUIRED_LOOK_SNAPSHOT_KEYS:
            raise PlanValidationError(
                f"authoring.look_snapshot must contain exactly {sorted(REQUIRED_LOOK_SNAPSHOT_KEYS)}, got {sorted(ls.keys())}"
            )
        look_id = ls["look_id"]
        if not isinstance(look_id, str) or not look_id:
            raise PlanValidationError("authoring.look_snapshot.look_id must be a non-empty string")
        ver = ls["version"]
        if type(ver) is not int or isinstance(ver, bool) or ver <= 0:
            raise PlanValidationError(f"authoring.look_snapshot.version must be a positive integer, got {ver!r}")
        app = ls["appearance"]
        if not isinstance(app, str):
            raise PlanValidationError("authoring.look_snapshot.appearance must be a string")
        outfit = ls["outfit"]
        norm_outfit = None
        if outfit is not None:
            if not isinstance(outfit, dict):
                raise PlanValidationError(f"authoring.look_snapshot.outfit must be null or an object, got {type(outfit).__name__}")
            if set(outfit.keys()) != REQUIRED_LOOK_SNAPSHOT_OUTFIT_KEYS:
                raise PlanValidationError(
                    f"authoring.look_snapshot.outfit must contain exactly {sorted(REQUIRED_LOOK_SNAPSHOT_OUTFIT_KEYS)}, got {sorted(outfit.keys())}"
                )
            ok = outfit["outfit_key"]
            if not isinstance(ok, str) or not ok:
                raise PlanValidationError("authoring.look_snapshot.outfit.outfit_key must be a non-empty string")
            garments = outfit["garments"]
            if not isinstance(garments, list) or len(garments) == 0:
                raise PlanValidationError("authoring.look_snapshot.outfit.garments must be a non-empty list")
            seen_g_keys: set[str] = set()
            norm_garments: list[dict] = []
            for g_idx, g in enumerate(garments):
                if not isinstance(g, dict) or set(g.keys()) != REQUIRED_GARMENT_KEYS:
                    raise PlanValidationError(
                        f"authoring.look_snapshot.outfit.garments[{g_idx}] must contain exactly {sorted(REQUIRED_GARMENT_KEYS)}"
                    )
                gk = g["key"]
                if not isinstance(gk, str) or not gk:
                    raise PlanValidationError(f"authoring.look_snapshot.outfit.garments[{g_idx}].key must be a non-empty string")
                if gk in seen_g_keys:
                    raise PlanValidationError(f"duplicate garment key {gk!r}")
                seen_g_keys.add(gk)
                gw = g["wording"]
                if not isinstance(gw, str) or not gw or gw != gw.strip():
                    raise PlanValidationError(
                        f"authoring.look_snapshot.outfit.garments[{g_idx}].wording must be a non-empty string with no leading or trailing whitespace, got {gw!r}"
                    )
                ga = g["aside"]
                if not isinstance(ga, str):
                    raise PlanValidationError(f"authoring.look_snapshot.outfit.garments[{g_idx}].aside must be a string")
                norm_garments.append({"key": gk, "wording": gw, "aside": ga})
            norm_outfit = {"outfit_key": ok, "garments": norm_garments}
        cd = ls["content_digest"]
        if not isinstance(cd, str) or len(cd) != 64 or not all(c in "0123456789abcdef" for c in cd):
            raise PlanValidationError("authoring.look_snapshot.content_digest must be 64 lowercase hex characters")
        import resource_store
        expected_digest = resource_store.canonical_digest({"appearance": app, "outfit": norm_outfit})
        if cd != expected_digest:
            raise PlanValidationError(
                f"authoring.look_snapshot.content_digest {cd!r} does not match computed digest {expected_digest!r}"
            )
        norm_ls = {
            "look_id": look_id,
            "version": ver,
            "content_digest": cd,
            "appearance": app,
            "outfit": norm_outfit,
        }

    # 9. wardrobe_progression
    wp = auth["wardrobe_progression"]
    norm_wp = None
    if wp is not None:
        if not isinstance(wp, dict):
            raise PlanValidationError(f"authoring.wardrobe_progression must be null or an object, got {type(wp).__name__}")
        if set(wp.keys()) != REQUIRED_WARDROBE_PROGRESSION_KEYS:
            raise PlanValidationError(
                f"authoring.wardrobe_progression must contain exactly {sorted(REQUIRED_WARDROBE_PROGRESSION_KEYS)}, got {sorted(wp.keys())}"
            )
        sld = wp["source_look_digest"]
        if not isinstance(sld, str) or len(sld) != 64 or not all(c in "0123456789abcdef" for c in sld):
            raise PlanValidationError("authoring.wardrobe_progression.source_look_digest must be 64 lowercase hex characters")
        stid = wp["start_take_id"]
        if not isinstance(stid, str) or not stid:
            raise PlanValidationError("authoring.wardrobe_progression.start_take_id must be a non-empty string")
        etid = wp["end_take_id"]
        if not isinstance(etid, str) or not etid:
            raise PlanValidationError("authoring.wardrobe_progression.end_take_id must be a non-empty string")
        stages = wp["stage_indices"]
        if not isinstance(stages, list) or len(stages) == 0:
            raise PlanValidationError("authoring.wardrobe_progression.stage_indices must be a non-empty list")
        for s_idx, s in enumerate(stages):
            if type(s) is not int or isinstance(s, bool) or s < 0:
                raise PlanValidationError(
                    f"authoring.wardrobe_progression.stage_indices[{s_idx}] must be a non-negative integer, got {s!r}"
                )
            if s_idx > 0 and s <= stages[s_idx - 1]:
                raise PlanValidationError(
                    f"authoring.wardrobe_progression.stage_indices must be strictly increasing, got {stages}"
                )
        app_rev = wp["applied_revision"]
        if type(app_rev) is not int or isinstance(app_rev, bool) or app_rev <= 0:
            raise PlanValidationError(
                f"authoring.wardrobe_progression.applied_revision must be a positive integer, got {app_rev!r}"
            )
        norm_wp = {
            "source_look_digest": sld,
            "start_take_id": stid,
            "end_take_id": etid,
            "stage_indices": list(stages),
            "applied_revision": app_rev,
        }

    # 10. shared_state
    ss = auth["shared_state"]
    if not isinstance(ss, dict):
        raise PlanValidationError(f"authoring.shared_state must be an object, got {type(ss).__name__}")
    if set(ss.keys()) != REQUIRED_SHARED_STATE_KEYS:
        raise PlanValidationError(
            f"authoring.shared_state must contain exactly {sorted(REQUIRED_SHARED_STATE_KEYS)}, got {sorted(ss.keys())}"
        )
    evidence_by_id = {rec["id"]: rec for rec in norm_ev}
    norm_ss: dict[str, dict] = {}
    for field in ("look", "initial_wardrobe"):
        meta = ss[field]
        if not isinstance(meta, dict) or set(meta.keys()) != {"origin", "evidence_id"}:
            raise PlanValidationError(f"authoring.shared_state.{field} must contain exactly ['evidence_id', 'origin']")
        origin = meta["origin"]
        if not isinstance(origin, str) or origin not in VALID_AUTHORING_ORIGINS:
            raise PlanValidationError(
                f"authoring.shared_state.{field}.origin must be one of {sorted(VALID_AUTHORING_ORIGINS)}, got {origin!r}"
            )
        ev_id = meta["evidence_id"]
        eff_val = plan.get(field, "")
        if origin == "none":
            if ev_id is not None:
                raise PlanValidationError(f"authoring.shared_state.{field}.evidence_id must be null for origin 'none'")
            if check_effective and eff_val != "":
                raise PlanValidationError(f"plan.{field} must be empty string for origin 'none', got {eff_val!r}")
        elif origin == "user":
            if ev_id is not None:
                raise PlanValidationError(f"authoring.shared_state.{field}.evidence_id must be null for origin 'user'")
        elif origin == "saved_look":
            if ev_id is not None:
                raise PlanValidationError(f"authoring.shared_state.{field}.evidence_id must be null for origin 'saved_look'")
            if norm_ls is None:
                raise PlanValidationError(f"authoring.shared_state.{field} with origin 'saved_look' requires a non-null look_snapshot")
            if field == "look":
                if check_effective and eff_val != norm_ls["appearance"]:
                    raise PlanValidationError(
                        f"plan.look must match look_snapshot.appearance for origin 'saved_look', got {eff_val!r} != {norm_ls['appearance']!r}"
                    )
            elif field == "initial_wardrobe":
                if norm_ls.get("outfit") is None:
                    raise PlanValidationError(
                        "authoring.shared_state.initial_wardrobe with origin 'saved_look' requires look_snapshot.outfit to be non-null"
                    )
                canonical_wardrobe = compose_saved_look_wardrobe(norm_ls["outfit"])
                if check_effective and eff_val != canonical_wardrobe:
                    raise PlanValidationError(
                        f"plan.initial_wardrobe must match canonical saved look wardrobe for origin 'saved_look', got {eff_val!r} != {canonical_wardrobe!r}"
                    )
        elif origin in ("assistant", "assistant_edited"):
            if not isinstance(ev_id, str) or not ev_id:
                raise PlanValidationError(
                    f"authoring.shared_state.{field}.evidence_id must be a non-empty string for origin {origin!r}"
                )
            if ev_id not in evidence_by_id:
                raise PlanValidationError(f"authoring.shared_state.{field} references non-existent evidence_id {ev_id!r}")
            ev_rec = evidence_by_id[ev_id]
            if field not in ev_rec["output"] or field not in ev_rec["accepted"]:
                raise PlanValidationError(f"evidence {ev_id!r} does not contain {field!r} in output/accepted")
            if check_effective and ev_rec["accepted"][field] != eff_val:
                raise PlanValidationError(
                    f"authoring.shared_state.{field} active evidence accepted value must match plan.{field}"
                )
        norm_ss[field] = {"origin": origin, "evidence_id": ev_id}

    return {
        "schema_version": 1,
        "mode": mode,
        "brief": brief,
        "scene_anchor": norm_anchor,
        "workflow_binding": norm_wf,
        "variation_policy": norm_policy,
        "shared_state": norm_ss,
        "evidence": norm_ev,
        "look_snapshot": norm_ls,
        "wardrobe_progression": norm_wp,
    }


def validate_draft(plan: Any, *, check_authoring_effective: bool = True) -> dict:
    """Validate a resource-v1 plan and return a normalized copy.

    The input shape and what this function checks:

      * top-level is a dict;
      * ``version`` is exactly ``"resource-v1"`` — anything else is
        rejected so an unknown or stale mode cannot sneak in;
      * ``look`` is a string (default ``""``);
      * ``initial_wardrobe`` is a string (default ``""``);
      * ``takes`` is a list of dicts, each with a unique non-empty
        ``take_id`` (the stable identifier the spec requires). Extra
        fields on a take are passed through verbatim, so a future task
        can add ``camera``/``pose``/``expression`` without breaking 3.1;
      * ``selected_resources`` is a list of dicts, each carrying exactly
        the three keys the spec names: ``library_key``,
        ``source_id`` and ``content_digest``. All three must be
        non-empty strings;
      * ``wardrobe_changes`` is a list of dicts, each with a
        ``take_id`` that matches one of the takes, a ``scope`` of
        ``"this_take"`` or ``"from_here"``, and a string
        ``wardrobe`` value.

    Returns a new dict with the validated structure. Selected resources
    and wardrobe changes are normalized to a stable key order so the
    round-trip through ``json.dumps`` is deterministic.
    """
    if not isinstance(plan, dict):
        raise PlanValidationError(
            f"plan must be a dict, got {type(plan).__name__}"
        )

    version = plan.get("version")
    if version != MODE_RESOURCE_V1:
        raise PlanValidationError(
            f"plan.version must be {MODE_RESOURCE_V1!r}, got {version!r}"
        )

    look = plan.get("look", "")
    if not isinstance(look, str):
        raise PlanValidationError(
            f"plan.look must be a string, got {type(look).__name__}"
        )

    initial_wardrobe = plan.get("initial_wardrobe", "")
    if not isinstance(initial_wardrobe, str):
        raise PlanValidationError(
            f"plan.initial_wardrobe must be a string, got "
            f"{type(initial_wardrobe).__name__}"
        )

    takes = plan.get("takes", [])
    if not isinstance(takes, list):
        raise PlanValidationError(
            f"plan.takes must be a list, got {type(takes).__name__}"
        )
    seen_take_ids: set[str] = set()
    for index, take in enumerate(takes):
        if not isinstance(take, dict):
            raise PlanValidationError(
                f"plan.takes[{index}] must be a dict, got "
                f"{type(take).__name__}"
            )
        take_id = take.get("take_id")
        if not isinstance(take_id, str) or not take_id:
            raise PlanValidationError(
                f"plan.takes[{index}].take_id must be a non-empty string, "
                f"got {take_id!r}"
            )
        if take_id in seen_take_ids:
            raise PlanValidationError(
                f"plan.takes contains duplicate take_id {take_id!r}"
            )
        seen_take_ids.add(take_id)

    selected = plan.get("selected_resources", [])
    if not isinstance(selected, list):
        raise PlanValidationError(
            f"plan.selected_resources must be a list, got "
            f"{type(selected).__name__}"
        )
    normalized_selected: list[dict] = []
    for index, sel in enumerate(selected):
        if not isinstance(sel, dict):
            raise PlanValidationError(
                f"plan.selected_resources[{index}] must be a dict, got "
                f"{type(sel).__name__}"
            )
        for key in ("library_key", "source_id", "content_digest"):
            value = sel.get(key)
            if not isinstance(value, str) or not value:
                raise PlanValidationError(
                    f"plan.selected_resources[{index}].{key} must be a "
                    f"non-empty string, got {value!r}"
                )
        normalized_selected.append({
            "library_key": sel["library_key"],
            "source_id": sel["source_id"],
            "content_digest": sel["content_digest"],
        })

    changes = plan.get("wardrobe_changes", [])
    if not isinstance(changes, list):
        raise PlanValidationError(
            f"plan.wardrobe_changes must be a list, got "
            f"{type(changes).__name__}"
        )
    # Two wardrobe-change events for the same stable take ID are
    # ambiguous: the resolver walks the ordered takes and looks up
    # ``wardrobe_changes`` by take_id, so two events for one take
    # would either silently override each other or be picked by
    # array order — the bug the spec calls out in
    # "Reject two wardrobe changes for the same take_id as
    # ambiguous during draft validation; do not rely on array
    # ordering to choose one." The set is built while iterating so
    # the first duplicate carries the offending index and the
    # take_id the operator can search the draft for.
    seen_change_take_ids: set[str] = set()
    normalized_changes: list[dict] = []
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            raise PlanValidationError(
                f"plan.wardrobe_changes[{index}] must be a dict, got "
                f"{type(change).__name__}"
            )
        change_take_id = change.get("take_id")
        if not isinstance(change_take_id, str) or not change_take_id:
            raise PlanValidationError(
                f"plan.wardrobe_changes[{index}].take_id must be a non-empty string, got {change_take_id!r}"
            )
        if change_take_id not in seen_take_ids:
            raise PlanValidationError(
                f"plan.wardrobe_changes[{index}].take_id "
                f"{change_take_id!r} does not match any take in plan.takes"
            )
        if change_take_id in seen_change_take_ids:
            raise PlanValidationError(
                f"plan.wardrobe_changes[{index}].take_id "
                f"{change_take_id!r} has more than one wardrobe-change "
                f"event; the resolver cannot pick one without an "
                f"ambiguous array-order tie-break. Remove the duplicate "
                f"event so each take has at most one wardrobe change."
            )
        seen_change_take_ids.add(change_take_id)
        scope = change.get("scope")
        if not isinstance(scope, str) or scope not in VALID_WARDROBE_SCOPES:
            raise PlanValidationError(
                f"plan.wardrobe_changes[{index}].scope must be one of "
                f"{sorted(VALID_WARDROBE_SCOPES)}, got {scope!r}"
            )
        wardrobe = change.get("wardrobe")
        if not isinstance(wardrobe, str):
            raise PlanValidationError(
                f"plan.wardrobe_changes[{index}].wardrobe must be a "
                f"string, got {type(wardrobe).__name__}"
            )
        normalized_changes.append({
            "take_id": change_take_id,
            "scope": scope,
            "wardrobe": wardrobe,
        })

    res = {
        "version": MODE_RESOURCE_V1,
        "look": look,
        "initial_wardrobe": initial_wardrobe,
        "takes": list(takes),
        "selected_resources": normalized_selected,
        "wardrobe_changes": normalized_changes,
    }
    if "authoring" in plan:
        res["authoring"] = validate_authoring_block(
            plan["authoring"], res, check_effective=check_authoring_effective,
        )
    return res


def validate_selected_resources(selected: list[dict]) -> None:
    """Every selected resource must exist as an immutable revision.

    The plan pins each selected resource to the exact
    ``(library_key, source_id, content_digest)`` triple it was chosen
    from. A change to the source creates a NEW immutable revision; the
    old one stays readable, so a draft that pointed at the old revision
    is still valid as long as that triple is in the table. A triple
    that was never recorded, or that names a library that was never
    registered, is refused — the alternative (silently substituting the
    latest revision) is the bug the spec names in the "selected
    resources identify one exact immutable revision" rule.

    Raises ``PlanValidationError`` naming the offending triple. Runs
    BEFORE the database is touched in the save path, so a refused save
    never leaves a half-written plan row behind.
    """
    for index, sel in enumerate(selected):
        library_key = sel["library_key"]
        source_id = sel["source_id"]
        content_digest = sel["content_digest"]
        library = db.one(
            "SELECT id FROM resource_library WHERE library_key = ?",
            library_key,
        )
        if library is None:
            raise PlanValidationError(
                f"plan.selected_resources[{index}] references unregistered "
                f"library_key={library_key!r} source_id={source_id!r} "
                f"content_digest={content_digest!r}"
            )
        revision = db.one(
            "SELECT id FROM asset_revision "
            "WHERE library_id = ? AND source_id = ? AND content_digest = ?",
            library["id"], source_id, content_digest,
        )
        if revision is None:
            raise PlanValidationError(
                f"plan.selected_resources[{index}] references missing "
                f"revision library_key={library_key!r} source_id={source_id!r} "
                f"content_digest={content_digest!r}: no immutable "
                f"asset_revision row matches this triple"
            )


# -- Effective-wardrobe resolution (task 3.2) ----------------------------


def resolve_effective_wardrobes(plan: Any) -> dict[str, str]:
    """Walk a validated plan and return each take's effective wardrobe.

    The resolver is a pure function of a resource-v1 plan. It is the
    small, deterministic core the spec names for task 3.2; the draft
    is data and the resolver turns that data into per-take state.

    Resolution rules, in this order:

      1. The inherited wardrobe starts as ``plan.initial_wardrobe``.
         With no ``wardrobe_changes`` at all, every take inherits
         this default.

      2. A change with ``scope == "this_take"`` applies to its named
         take only. The inherited wardrobe is NOT advanced; the
         following take inherits whatever the prior inherited state
         was. A one-take override is an isolated perturbation, never
         a step in a walk.

      3. A change with ``scope == "from_here"`` applies to its named
         take and every following take, until a later
         ``from_here`` supersedes it. The change is what the spec
         calls a "persistent" change, and the inherited wardrobe is
         updated in place as the walk crosses it. Two
         ``from_here`` events in the same plan are not
         contradictory: the later one wins from its take onward,
         which is the natural reading of "until another explicit
         change".

      4. The walk follows the CURRENT order of ``plan.takes``. The
         resolver does NOT sort, does NOT look at numeric suffixes,
         and does NOT remember old positions. A wardrobe change
         follows its stable ``take_id`` to wherever that take now
         sits. Removing a previously saved change and re-saving the
         draft through the existing CAS path leaves that take with
         whatever the prior inherited state now is, which is the
         "removing a change restores the inherited wardrobe" rule.

    The function is pure: it does no I/O, holds no state between
    calls, and returns a fresh dict each time. The caller is
    expected to pass a draft that has been read through
    ``get_draft``; this function runs ``validate_draft`` itself so
    a refused walk and a refused save share one error class. Two
    wardrobe changes for the same ``take_id`` are refused at the
    validation boundary so the resolver never has to pick one by
    array order.
    """
    validated = validate_draft(plan)
    initial = validated["initial_wardrobe"]
    takes = validated["takes"]
    changes_by_take: dict[str, dict] = {
        change["take_id"]: change for change in validated["wardrobe_changes"]
    }

    effective: dict[str, str] = {}
    inherited = initial
    for take in takes:
        take_id = take["take_id"]
        change = changes_by_take.get(take_id)
        if change is None:
            effective[take_id] = inherited
            continue
        scope = change["scope"]
        if scope == WARDROBE_SCOPE_THIS_TAKE:
            # One-take override: applies here, leaves the inherited
            # state untouched so the next take sees what came
            # before this one. This is the rule the spec calls out
            # in the "One-take override" scenario.
            effective[take_id] = change["wardrobe"]
        elif scope == WARDROBE_SCOPE_FROM_HERE:
            # Persistent change: applies here AND advances the
            # inherited state so every following take picks it up
            # until a later ``from_here`` supersedes it.
            inherited = change["wardrobe"]
            effective[take_id] = inherited
        else:
            # ``validate_draft`` already refused any other scope;
            # this branch is unreachable in a validated plan and
            # exists only to make the walk exhaustive for a reader
            # who reads the function without reading the
            # validation. The error names the scope so a future
            # caller that bypasses ``validate_draft`` gets a
            # readable message rather than a silent skip.
            raise PlanValidationError(
                f"resolve_effective_wardrobes encountered wardrobe_changes "
                f"with an unrecognised scope {scope!r} on take_id "
                f"{take_id!r}; validate_draft must be called first"
            )
    return effective


# -- 3.3: constant/look conflicts and explicit invalidation ---------------


# The four status values the ``prepared_take.status`` column accepts.
# The CHECK constraint in the schema is the source of truth, but a
# Python constant keeps the service-level reads in lockstep with the
# SQL CHECK so a future column widening does not silently add a fifth
# value here without also widening the constraint. The
# ``invalidate_ungenerated_prepared_takes`` pass owns the
# ``pending`` → ``invalidated`` and ``ready`` → ``invalidated``
# transitions directly inside its SQL pass; there is no Python-level
# set that aggregates them — the SQL pass is the single source of
# truth for the invalidation rule. On every successful save, only
# the prepared_take rows for the session whose status is ``pending``
# or ``ready`` AND whose ``plan_revision`` differs from the new
# plan revision are transitioned to ``invalidated``; rows in
# ``pending`` or ``ready`` that already sit at the new plan
# revision are preserved (a future task 3.4 may write such rows
# and the pass must not invalidate them). Rows in ``generated`` or
# ``invalidated`` are immutable history and the pass leaves them
# alone.
PREPARED_TAKE_STATUS_PENDING = "pending"
PREPARED_TAKE_STATUS_READY = "ready"
PREPARED_TAKE_STATUS_INVALIDATED = "invalidated"
PREPARED_TAKE_STATUS_GENERATED = "generated"


def detect_resource_constant_conflicts(plan: dict) -> list[dict]:
    """Emit a neutral structural marker for every non-empty ``descriptive_input``
    the selected resources carry, against the plan's fixed constants.

    The detector walks every selected resource's payload and reads
    ``resource_prompts.PREPARATION_FIELD_MAPPING[kind]`` to decide
    which field names the contract classifies as
    ``descriptive_input`` — the role whose values are eligible to
    land in a prompt. The contract is the single source of truth
    for the field set: a future widening of the vocabulary
    (``fused_scenes`` already has a wider set than ``rooms``) is
    picked up automatically. Fields with other roles
    (``identity``, ``selection_metadata``, ``writer_guidance``,
    ``intentionally_unused``) are not eligible to compete with
    the plan's constants because the preparation contract does
    not use them as prompt content, and the detector does not
    look at them. Auxiliary kinds (``translation_map``,
    ``cut_map``, ``mined_families``, ``mined_labels``) and any
    unknown kind are skipped silently because the preparation
    contract publishes no ``descriptive_input`` for them.

    The detector does NOT decide by content whether a field
    competes with the plan's ``look`` or with the plan's
    ``initial_wardrobe``. The marker is a single neutral kind —
    ``resource_descriptive_vs_plan_constants`` — regardless of
    the field name or the value's content. The plan's constants
    are what they are: a scene-theme that mentions "a linen
    curtain" and a uniform-fit field that mentions "a dark
    wool suit" are both descriptive inputs; the detector shows
    the value alongside whichever of the plan's
    ``look``/``initial_wardrobe`` is set, and asks for human
    review. The marker says so explicitly: human review is
    required, the detector does not classify the field by its
    content, and a future task decides which side the value
    helps (or whether the value is dropped).

    The value is preserved verbatim, in the type the resource
    carries. A string is preserved as a string; a JSON list
    (e.g. ``tags``) is preserved as a list. The detector does
    not coerce, summarize, or string-format the value: a
    preparation task downstream reads the marker and the asset
    revision's payload together, and a coerced value would
    lose the structure a list carries (an array of category
    tags is a different shape from a paragraph of prose that
    happens to mention those words).

    A marker is emitted ONLY when at least one of the plan's
    constants (``look`` or ``initial_wardrobe``) is set. An
    empty ``look`` and an empty ``initial_wardrobe`` is the
    "no constant yet" state, and a marker with neither side
    shown would not tell the operator anything. The detector
    is conservative: when nothing is fixed, the resource's
    descriptive input is recorded only in
    ``asset_revision.payload`` (the plan does NOT carry the
    payload, only the immutable revision reference) and the
    save proceeds without a marker.

    The marker shape:

      * ``kind`` — a single neutral value
        (``resource_descriptive_vs_plan_constants``)
        regardless of field name or content.
      * ``library_key`` / ``source_id`` / ``content_digest`` —
        the selected resource triple the marker names. The
        plan's reference to the resource is the triple; the
        payload itself lives in ``asset_revision`` and is
        looked up by the detector for this pass.
      * ``resource_field`` — the descriptive_input field name
        (from the preparation contract).
      * ``resource_value`` — the COMPLETE value the resource
        carries, preserved verbatim. A string is a string; a
        list is a list. The detector does NOT coerce, truncate,
        or summarize.
      * ``plan_look`` — the plan's ``look`` if set; omitted
        otherwise.
      * ``plan_initial_wardrobe`` — the plan's
        ``initial_wardrobe`` if set; omitted otherwise.
      * ``message`` — a human-readable sentence that names
        the field, both sides, and the human-review rule. The
        detector does not claim to classify the field by
        content; a future task or the operator decides.
    """
    # Import inside the function: resource_prompts is a sibling
    # and importing at module-load time would create a cycle if
    # resource_prompts ever imports session_plan. The import is
    # cheap (already-loaded module) and PREPARATION_FIELD_MAPPING
    # and ROLE_DESCRIPTIVE_INPUT are the source of truth for
    # which fields this detector looks at.
    import resource_prompts

    conflicts: list[dict] = []
    plan_look = plan.get("look", "") or ""
    plan_initial_wardrobe = plan.get("initial_wardrobe", "") or ""

    # The plan's reference to the resource is the immutable
    # revision triple; the payload is fetched from
    # ``asset_revision`` here and is NOT carried in the plan
    # JSON or in any provenance field. The detector's job is to
    # surface the structural fact that the resource carries a
    # descriptive input; the value itself stays in
    # ``asset_revision.payload`` for the future preparation
    # task to read.
    for sel in plan.get("selected_resources", []):
        library_key = sel["library_key"]
        source_id = sel["source_id"]
        content_digest = sel["content_digest"]
        library = db.one(
            "SELECT id, kind FROM resource_library WHERE library_key = ?",
            library_key,
        )
        if library is None:
            # ``validate_selected_resources`` already refused a
            # plan that names a missing library; a row that
            # somehow vanished between validation and this
            # pass is treated as "no marker" rather than as a
            # second refusal. The CAS check still guards the
            # write.
            continue
        kind = library["kind"]
        field_mapping = resource_prompts.PREPARATION_FIELD_MAPPING.get(kind)
        if field_mapping is None:
            continue
        revision = db.one(
            "SELECT payload FROM asset_revision "
            "WHERE library_id = ? AND source_id = ? AND content_digest = ?",
            library["id"], source_id, content_digest,
        )
        if revision is None:
            continue
        try:
            payload = json.loads(revision["payload"])
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        for field_name, mapping in field_mapping.items():
            if mapping.get("role") != resource_prompts.ROLE_DESCRIPTIVE_INPUT:
                continue
            if field_name not in payload:
                continue
            value = payload[field_name]
            # The value is preserved verbatim. A None, an empty
            # string, or an empty list is "no value" and
            # produces no marker; a non-empty string and a
            # non-empty list both produce one. A future
            # preparation task reads the value as the resource
            # wrote it.
            if value is None:
                continue
            if isinstance(value, str) and not value:
                continue
            if isinstance(value, list) and not value:
                continue
            # No fixed plan constants → no marker. The
            # "no constant yet" state has nothing to surface
            # against.
            if not plan_look and not plan_initial_wardrobe:
                continue
            marker: dict = {
                "kind": "resource_descriptive_vs_plan_constants",
                "library_key": library_key,
                "source_id": source_id,
                "content_digest": content_digest,
                "resource_field": field_name,
                "resource_value": value,
            }
            if plan_look:
                marker["plan_look"] = plan_look
            if plan_initial_wardrobe:
                marker["plan_initial_wardrobe"] = plan_initial_wardrobe
            sides: list[str] = []
            if plan_look:
                sides.append("look")
            if plan_initial_wardrobe:
                sides.append("initial_wardrobe")
            sides_text = " and ".join(sides)
            verb = "is" if len(sides) == 1 else "are"
            marker["message"] = (
                f"selected resource {library_key}/{source_id} carries a "
                f"descriptive input in field {field_name!r}; the "
                f"plan's {sides_text} {verb} the fixed constant(s); "
                f"both are recorded; human review is required; the "
                f"detector does NOT decide by content whether this "
                f"field competes with the look or with the "
                f"initial_wardrobe"
            )
            conflicts.append(marker)
    return conflicts


def _plan_constants_changed(old_plan: dict, new_plan: dict) -> bool:
    """Return whether look, initial wardrobe or selected resources changed."""
    return any(
        old_plan.get(field, default) != new_plan.get(field, default)
        for field, default in (
            ("look", ""),
            ("initial_wardrobe", ""),
            ("selected_resources", []),
        )
    )


def _plan_continuity_changed(old_plan: dict, new_plan: dict) -> bool:
    """Return whether a plan revision changes any frozen continuity input.

    Authoring plans also bind continuity to ``scene_anchor``,
    ``variation_policy``, ``workflow_binding`` and the complete
    ``look_snapshot``. Canonical JSON comparison ignores object key
    order and preserves array order and every nested value. Brief edits
    and explicit per-take decisions remain outside this freeze.
    """
    if _plan_constants_changed(old_plan, new_plan):
        return True
    old_authoring = old_plan.get("authoring")
    new_authoring = new_plan.get("authoring")
    if not isinstance(old_authoring, dict):
        old_authoring = {}
    if not isinstance(new_authoring, dict):
        new_authoring = {}
    return any(
        json.dumps(
            old_authoring.get(field), ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        ) != json.dumps(
            new_authoring.get(field), ensure_ascii=False,
            separators=(",", ":"), sort_keys=True,
        )
        for field in (
            "scene_anchor",
            "variation_policy",
            "workflow_binding",
            "look_snapshot",
        )
    )


def has_generated_take(session_id: int) -> bool:
    """Return True if the session has any prepared_take in ``generated`` status.

    A generated prepared_take is a row whose ``linked_shot_id``
    points at a queued or finished shot: that row, and the
    shot it points at, are history. The check is a single
    indexed SELECT on the (session_id, status) pair, which the
    schema's foreign-key index serves. A session that has never
    prepared a take reads as False, and a session whose only
    prepared takes are in ``pending`` or ``ready`` also reads as
    False — those are ungenerated work, not history.
    """
    row = db.one(
        "SELECT 1 AS x FROM prepared_take "
        "WHERE session_id = ? AND status = ? LIMIT 1",
        session_id, PREPARED_TAKE_STATUS_GENERATED,
    )
    return row is not None


def _take_id_set(plan: dict) -> set[str]:
    """Return the set of stable take IDs in ``plan``.

    A plan is a dict; ``takes`` is a list of dicts that may
    carry extra fields a future task adds. The function is
    purely defensive: a non-dict ``takes`` entry, a missing
    ``take_id`` or a non-string ``take_id`` is skipped so a
    malformed plan can never turn into a silent KeyError
    inside the invalidation pass.
    """
    out: set[str] = set()
    for take in plan.get("takes", []) or []:
        if not isinstance(take, dict):
            continue
        tid = take.get("take_id")
        if isinstance(tid, str) and tid:
            out.add(tid)
    return out


def _take_by_id(plan: dict) -> dict[str, dict]:
    """Index ``plan``'s takes by ``take_id`` and skip malformed entries.

    The invalidation pass needs the per-take content of both
    plans side by side. Returning a dict by ``take_id`` makes
    the comparison O(N) and keeps the caller from having to
    repeat the ``isinstance`` / non-string guards ``_take_id_set``
    already owns. A non-dict ``takes`` entry, a missing
    ``take_id`` or a non-string ``take_id`` is skipped for the
    same reason ``_take_id_set`` skips it: the input was
    validated upstream, but the helper is defensive in case a
    future caller hands it an unvalidated plan.
    """
    out: dict[str, dict] = {}
    for take in plan.get("takes", []) or []:
        if not isinstance(take, dict):
            continue
        tid = take.get("take_id")
        if not isinstance(tid, str) or not tid:
            continue
        out[tid] = take
    return out


def _take_content_signature(take: dict) -> str:
    """Return a deterministic, JSON-serialisable signature of a take.

    The signature covers every per-take field the take carries
    except ``take_id`` itself, which is the comparison key the
    invalidation pass matches on. The signature is built with
    ``json.dumps`` and ``sort_keys=True`` so dict ordering and
    list ordering that the user does not control do not
    surface as a difference. The comparison is intentionally
    conservative: any change in any field the take carries is
    a difference, so the invalidation pass can target the
    right rows.

    The function ignores per-plan structural fields the take
    does not carry (``look``, ``initial_wardrobe``,
    ``selected_resources``, ``wardrobe_changes``) because
    those live at the plan level, not on each take. The
    function also ignores a ``take_id`` key when it is present
    in the dict (the plan validator does not strip it), so
    two takes with the same per-take content but a different
    key order do not compare as different. The take must be a
    dict; any other shape falls back to an empty signature
    so a malformed input never raises inside the pass.
    """
    if not isinstance(take, dict):
        return ""
    payload = {
        key: value
        for key, value in take.items()
        if key != "take_id"
    }
    try:
        return json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return ""


def _compute_affected_take_ids_for_plan_change(
    old_plan: dict, new_plan: dict,
) -> set[str]:
    """Return the take IDs whose preparation inputs changed
    between ``old_plan`` and ``new_plan``, plus any take
    present in the old plan but absent from the new one.

    The function is the conservative invalidation rule the
    ``session-plan`` spec names for wardrobe / order / per-
    take edits: a take is in the result when ANY of the
    following holds between the two plans:

      * the take is in ``old_plan`` but missing from
        ``new_plan`` (the plan no longer carries it, so any
        prepared_take row the user landed for it has no
        current plan to live under);
      * the take's effective wardrobe under ``new_plan``
        differs from the one under ``old_plan`` (the
        ``from_here`` / ``this_take`` boundary changed);
      * the take's per-take content under ``new_plan``
        differs from the one under ``old_plan`` (a take-
        level field the preparation pipeline feeds into
        ``final_prompt`` was edited: ``camera``,
        ``framing``, ``pose``, ``expression``, or any
        future take-level field the OpenSpec adds).

    The third clause is the part the previous revision of
    this function missed: a take whose creative choices
    were edited but whose effective wardrobe stayed
    identical still needs its prior ``ready`` row marked
    ``invalidated`` because the new final_prompt will
    differ from the persisted one, and shipping a stale
    prompt as if it were current would let a finished
    photograph cite a state the photograph did not
    actually use. The function reads the take content
    from the validated plan the caller already passed
    through ``_validate_plan_payload``; a future take
    field added by the OpenSpec is covered by the same
    ``_take_content_signature`` comparison without any
    code change here.

    The function is intentionally generic. It does NOT
    inspect any wardrobe string, scope value or take
    count; it is a pure comparison of the per-take and
    effective-wardrobe projections of two plans. A
    future plan shape with a different wardrobe / scope /
    take grammar keeps the same contract: the resolver
    walks the new shape, the comparison surfaces what
    changed, the invalidation pass targets the right
    rows.

    Takes added by the new plan (present in ``new_plan``
    but absent from ``old_plan``) are NOT in the result:
    there is no old row to invalidate. The new revision
    has to land a fresh prepared_take row for them, which
    is the normal re-prepare path the recovery surface
    already offers.
    """
    old_effective = resolve_effective_wardrobes(old_plan)
    new_effective = resolve_effective_wardrobes(new_plan)
    old_by_id = _take_by_id(old_plan)
    new_by_id = _take_by_id(new_plan)
    affected: set[str] = set()
    for tid, old_take in old_by_id.items():
        new_take = new_by_id.get(tid)
        if new_take is None:
            affected.add(tid)
            continue
        if old_effective.get(tid, "") != new_effective.get(tid, ""):
            affected.add(tid)
            continue
        if (
            _take_content_signature(old_take)
            != _take_content_signature(new_take)
        ):
            affected.add(tid)
    return affected


def invalidate_ungenerated_prepared_takes(
    session_id: int, kept_plan_revision: int,
    *,
    affected_take_ids: set[str] | None = None,
    new_take_ids: set[str] | None = None,
) -> None:
    """Mark ungenerated prepared_take rows for the session as
    ``invalidated`` according to the invalidation policy.

    The pass is the single implementation called by
    ``save_draft``. It targets rows whose status is ``pending`` or
    ``ready`` — ungenerated work that was prepared under a now-
    stale plan revision. A row at the new plan revision is left
    alone (the kept revision is the one a future task will write
    fresh prepared_take rows under). Rows already in
    ``invalidated`` or ``generated`` status are history and the
    pass does not touch them; rewriting a generated row's status
    would silently pretend the linked shot was no longer linked,
    and rewriting a generated row's prompt or provenance would
    silently pretend a finished photograph used a state it did
    not.

    ``affected_take_ids`` and ``new_take_ids`` are the
    conservative-narrowing switches the ``session-plan`` spec
    asks for. When both are ``None`` (the default), the pass
    invalidates every ungenerated row at a non-current revision
    — the strict policy for a save that changes the established
    constants (``look``, ``initial_wardrobe`` or
    ``selected_resources``). When at least one is provided, the
    pass invalidates only the rows whose ``take_id`` is in
    ``affected_take_ids`` OR is not in ``new_take_ids`` — the
    conservative policy for a save that only edits
    ``wardrobe_changes`` or the take order, where a take whose
    effective state is byte-for-byte identical in the new plan
    keeps its prior ``ready`` row untouched, and any row whose
    take_id is not in the new plan (a take removed by the edit, or
    a synthetic orphan row) is invalidated because it has no
    current plan to land under. The empty sets are a legal
    argument: a save whose diff lands an empty affected set
    invalidates no rows.

    The function returns nothing on purpose: a row-count
    derivation is not part of the contract, the tests assert the
    row state directly, and any "how many rows were invalidated
    on this call" derivation can be done by the caller with a
    deterministic SELECT outside the transaction. A return value
    that was tied to a SELECT inside the same transaction would
    either be racy (counting the rows the UPDATE just changed on
    a future call) or rely on a side channel (the ``updated_at``
    timestamp) that is not part of the row's identity.

    The pass is safe to run when no rows match: a session with
    no prepared_take rows is a legal state, and the UPDATE
    succeeds without raising. The caller (``save_draft``) wraps
    the call in the same transaction as the plan write so a
    refused save is the only path that could leave the table in
    an inconsistent state — and a refused save never reaches
    this function.
    """
    now = db.now()
    if affected_take_ids is None and new_take_ids is None:
        db.run(
            "UPDATE prepared_take "
            "SET status = ?, updated_at = ? "
            "WHERE session_id = ? "
            "AND status IN (?, ?) "
            "AND plan_revision != ?",
            PREPARED_TAKE_STATUS_INVALIDATED, now, session_id,
            PREPARED_TAKE_STATUS_PENDING, PREPARED_TAKE_STATUS_READY,
            kept_plan_revision,
        )
        return
    affected_take_ids = affected_take_ids or set()
    new_take_ids = new_take_ids or set()
    if not affected_take_ids and not new_take_ids:
        return
    parts: list[str] = []
    params: list[Any] = [
        PREPARED_TAKE_STATUS_INVALIDATED, now, session_id,
        PREPARED_TAKE_STATUS_PENDING, PREPARED_TAKE_STATUS_READY,
        kept_plan_revision,
    ]
    if affected_take_ids:
        placeholders = ", ".join("?" for _ in affected_take_ids)
        parts.append(f"take_id IN ({placeholders})")
        params.extend(sorted(affected_take_ids))
    if new_take_ids:
        placeholders = ", ".join("?" for _ in new_take_ids)
        parts.append(f"take_id NOT IN ({placeholders})")
        params.extend(sorted(new_take_ids))
    where_extra = " OR ".join(parts)
    db.run(
        "UPDATE prepared_take "
        "SET status = ?, updated_at = ? "
        "WHERE session_id = ? "
        "AND status IN (?, ?) "
        "AND plan_revision != ? "
        f"AND ({where_extra})",
        *params,
    )


# -- Persistence ------------------------------------------------------------


def current_revision(session_id: int) -> int:
    """Return the current ``plan_revision`` for the session, or 0.

    A session with no row in ``session_plan`` reads as 0, which is the
    revision a first save is expected to pass. The function is a thin
    read; it does not raise on a missing session, so a caller that
    wants to distinguish "no plan" from "no session" should check the
    session row separately.
    """
    row = db.one(
        "SELECT plan_revision FROM session_plan WHERE session_id = ?",
        session_id,
    )
    if row is None:
        return 0
    return int(row["plan_revision"])


def read_composition_mode(settings: Any) -> str:
    """Return the session's ``composition_mode`` from its ``settings`` JSON.

    The mode is stored inside the session's ``settings`` column, NOT
    on a dedicated row, so the value lives wherever the rest of the
    session's free-form settings live. A missing key reads as the
    empty string — the legacy default — and any value other than
    ``""`` or ``MODE_RESOURCE_V1`` is treated as a malformed session
    and the empty string is returned so a downstream caller falls
    through to the legacy path. Storing it in ``settings`` is what
    keeps the design's "absent means legacy" rule on the same
    JSON-as-TEXT idiom the rest of the column already follows.
    """
    if not isinstance(settings, str) or not settings:
        return ""
    try:
        decoded = json.loads(settings)
    except json.JSONDecodeError:
        return ""
    if not isinstance(decoded, dict):
        return ""
    value = decoded.get("composition_mode", "")
    if not isinstance(value, str):
        return ""
    if value not in ("", MODE_RESOURCE_V1):
        return ""
    return value


def get_draft(session_id: int) -> dict | None:
    """Return the current draft, or ``None`` if no plan exists.

    The returned dict has two keys: ``plan_revision`` (the integer the
    next save must echo back as ``expected_revision``) and ``plan``
    (the validated JSON decoded back into Python). Returns ``None``
    when the session has no plan row — the route maps that to a 404.
    """
    row = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    if row is None:
        return None
    return {
        "plan_revision": int(row["plan_revision"]),
        "plan": json.loads(row["plan_json"]),
    }


def _safe_validate_workflow_binding(session_id: int) -> dict | None:
    """Run the validator, mapping ``StoredPlanUnreadable`` to the persistence category.

    The validator raises ``StoredPlanUnreadable`` when the stored
    ``plan_json`` cannot be decoded at all — the same condition
    ``_load_current_resource_plan`` already classifies as
    ``PreparedTakePersistenceError``. The persistence-error category
    is the existing, app-wide one for unreadable stored plans; turning
    unreadable data into ``WorkflowChanged`` would have introduced a
    second, misleading classification. This helper centralises the
    conversion so every domain path that gates on the validator
    surfaces the same error kind for the same root cause.
    """
    try:
        return workflow_binding.validate_workflow_binding_against_session(session_id)
    except workflow_binding.StoredPlanUnreadable as exc:
        raise PreparedTakePersistenceError(exc.message) from exc


def _load_current_resource_plan(session_id: int) -> tuple[int, dict]:
    """Return the current resource-v1 plan after validating the session mode."""
    session = db.one(
        "SELECT id, settings FROM session WHERE id = ?",
        session_id,
    )
    if session is None:
        raise SessionNotFound(f"session {session_id} not found")
    mode = read_composition_mode(session["settings"])
    if mode != MODE_RESOURCE_V1:
        raise SessionNotInResourceMode(
            f"session {session_id} composition_mode is {mode!r}, "
            f"expected {MODE_RESOURCE_V1!r}"
        )
    row = db.one(
        "SELECT plan_revision, plan_json FROM session_plan WHERE session_id = ?",
        session_id,
    )
    if row is None:
        raise PlanRevisionStale(
            f"session {session_id} has no saved plan revision to prepare"
        )
    try:
        plan = json.loads(row["plan_json"])
    except json.JSONDecodeError as exc:
        raise PreparedTakePersistenceError(
            f"session {session_id} stored plan could not be read"
        ) from exc
    if not isinstance(plan, dict):
        raise PreparedTakePersistenceError(
            f"session {session_id} stored plan is not an object"
        )
    return int(row["plan_revision"]), plan


def _validate_preparation_target(
    session_id: int,
    plan_revision: int,
    take_id: str,
) -> dict:
    """Validate the exact session, revision and take preparation key."""
    actual_revision, plan = _load_current_resource_plan(session_id)
    if actual_revision != plan_revision:
        raise PlanRevisionStale(
            f"session {session_id} plan revision is {actual_revision}, "
            f"requested preparation revision is {plan_revision}"
        )
    if not isinstance(take_id, str) or not take_id:
        raise PlanValidationError("take_id must be a non-empty string")
    take_ids = {
        take.get("take_id")
        for take in plan.get("takes", [])
        if isinstance(take, dict)
    }
    if take_id not in take_ids:
        raise PlanValidationError(
            f"take_id {take_id!r} is not present in plan revision {plan_revision}"
        )
    return plan


def classify_plan_authoring(plan: dict) -> str:
    """Classify plan authoring authority into pre-authoring expert, manual, or automatic.

    Fails closed with PlanValidationError if an authoring block is present but malformed
    (e.g. null, empty, non-dict, schema_version != 1, boolean version, or missing/unknown mode).
    """
    if not isinstance(plan, dict):
        raise PlanValidationError("plan must be an object")
    if "authoring" not in plan:
        return PLAN_AUTHORING_KIND_PRE_AUTHORING_EXPERT
    auth = plan["authoring"]
    if not isinstance(auth, dict) or not auth:
        raise PlanValidationError("plan authoring block must be a non-empty object")
    schema_ver = auth.get("schema_version")
    if type(schema_ver) is not int or schema_ver != 1:
        raise PlanValidationError(
            f"plan authoring schema_version must be 1, got {schema_ver!r}"
        )
    mode = auth.get("mode")
    if mode == AUTHORING_MODE_MANUAL:
        return PLAN_AUTHORING_KIND_MANUAL
    if mode == AUTHORING_MODE_AUTOMATIC:
        return PLAN_AUTHORING_KIND_AUTOMATIC
    raise PlanValidationError(
        f"plan authoring mode must be {AUTHORING_MODE_AUTOMATIC!r} or {AUTHORING_MODE_MANUAL!r}, got {mode!r}"
    )


def assert_raw_preparation_allowed(plan: dict) -> None:
    """Enforce that public raw begin/complete is only available for pre-authoring plans."""
    kind = classify_plan_authoring(plan)
    if kind != PLAN_AUTHORING_KIND_PRE_AUTHORING_EXPERT:
        raise PreparationAuthorityConflict(
            f"server_owned_preparation_required: plan authoring mode {kind!r} "
            f"does not allow public raw preparation; raw begin/complete is restricted "
            f"to pre-authoring expert plans"
        )


def assert_direct_preparation_allowed(plan: dict) -> None:
    """Enforce that direct preparation routes are not accessible for automatic authoring."""
    kind = classify_plan_authoring(plan)
    if kind == PLAN_AUTHORING_KIND_AUTOMATIC:
        raise PreparationAuthorityConflict(
            "automatic_requires_operation: plan authoring mode 'automatic' "
            "does not allow direct preparation; automatic ready state requires "
            "a fenced prepare_takes operation"
        )


def _decode_prepared_take(row: dict) -> dict:
    """Decode the JSON snapshot columns without rewriting their row."""
    decoded = dict(row)
    try:
        decoded["effective_state"] = json.loads(decoded["effective_state"])
        decoded["provenance"] = json.loads(decoded["provenance"])
    except json.JSONDecodeError as exc:
        raise PreparedTakePersistenceError(
            f"prepared take {row.get('id')} contains unreadable snapshot JSON"
        ) from exc
    return decoded


def _prepared_take_row(
    session_id: int,
    plan_revision: int,
    take_id: str,
) -> dict | None:
    return db.one(
        "SELECT id, session_id, plan_revision, take_id, final_prompt, "
        "effective_state, mapping_version, compiler_version, provenance, "
        "status, linked_shot_id, created_at, updated_at "
        "FROM prepared_take WHERE session_id = ? AND plan_revision = ? "
        "AND take_id = ?",
        session_id, plan_revision, take_id,
    )


def _encode_snapshot_json(value: Any, field_name: str) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(
            f"{field_name} must be JSON serializable"
        ) from exc


def begin_preparation(
    session_id: int,
    plan_revision: int,
    take_id: str,
) -> dict:
    """Persist pending before lengthy work and return the durable row.

    Task 4.3: the read-only workflow-binding validator runs BEFORE the
    transaction so a drifted binding cannot leave a pending row behind.
    The pre-check rejects the begin with ``WorkflowChanged``; the
    transaction is never entered, the existing pending / ready rows are
    not touched.
    """
    _safe_validate_workflow_binding(session_id)
    try:
        with db.transaction():
            _safe_validate_workflow_binding(session_id)
            _validate_preparation_target(session_id, plan_revision, take_id)
            existing = _prepared_take_row(session_id, plan_revision, take_id)
            if existing is not None:
                if existing["status"] == PREPARED_TAKE_STATUS_INVALIDATED:
                    raise PreparedTakeConflict(
                        f"prepared take {take_id!r} at plan revision "
                        f"{plan_revision} is invalidated history"
                    )
                return _decode_prepared_take(existing)
            now = db.now()
            db.run(
                "INSERT INTO prepared_take "
                "(session_id, plan_revision, take_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                session_id, plan_revision, take_id,
                PREPARED_TAKE_STATUS_PENDING, now, now,
            )
            row = _prepared_take_row(session_id, plan_revision, take_id)
            if row is None:
                raise PreparedTakePersistenceError(
                    f"pending prepared take {take_id!r} was not persisted"
                )
            return _decode_prepared_take(row)
    except (
        SessionNotFound,
        SessionNotInResourceMode,
        PlanRevisionStale,
        PlanValidationError,
        PreparedTakeConflict,
        PreparedTakePersistenceError,
        workflow_binding.WorkflowChanged,
    ):
        raise
    except Exception as exc:
        raise PreparedTakePersistenceError(
            f"could not persist pending prepared take {take_id!r}: {exc}"
        ) from exc


def _snapshots_equal(current: tuple, desired: tuple) -> bool:
    """Compare snapshot tuples with semantic JSON equality for JSON columns."""
    if current == desired:
        return True
    if len(current) != len(desired):
        return False
    if current[0] != desired[0] or current[2] != desired[2] or current[3] != desired[3]:
        return False
    try:
        cur_state = json.loads(current[1]) if isinstance(current[1], str) else current[1]
        des_state = json.loads(desired[1]) if isinstance(desired[1], str) else desired[1]
        cur_prov = json.loads(current[4]) if isinstance(current[4], str) else current[4]
        des_prov = json.loads(desired[4]) if isinstance(desired[4], str) else desired[4]
        return cur_state == des_state and cur_prov == des_prov
    except Exception:
        return False


def complete_preparation(
    session_id: int,
    plan_revision: int,
    take_id: str,
    *,
    final_prompt: str,
    effective_state: Any,
    mapping_version: str,
    compiler_version: str,
    provenance: Any,
) -> dict:
    """Complete a raw historical snapshot; authoring-v1 requires sealed persistence."""
    plan = _validate_preparation_target(session_id, plan_revision, take_id)
    if classify_plan_authoring(plan) != PLAN_AUTHORING_KIND_PRE_AUTHORING_EXPERT:
        raise AuthoringEvidenceInvalid(
            "raw complete_preparation cannot create an authoring-v1 snapshot"
        )
    if not isinstance(final_prompt, str):
        raise PlanValidationError("final_prompt must be a string")
    if not final_prompt.strip():
        raise PlanValidationError("final_prompt must be a non-empty string")
    if not isinstance(mapping_version, str):
        raise PlanValidationError("mapping_version must be a string")
    if not mapping_version.strip():
        raise PlanValidationError("mapping_version must be a non-empty string")
    if not isinstance(compiler_version, str):
        raise PlanValidationError("compiler_version must be a string")
    if not compiler_version.strip():
        raise PlanValidationError("compiler_version must be a non-empty string")
    encoded_state = _encode_snapshot_json(effective_state, "effective_state")
    encoded_provenance = _encode_snapshot_json(provenance, "provenance")
    desired = (
        final_prompt,
        encoded_state,
        mapping_version,
        compiler_version,
        encoded_provenance,
    )
    try:
        with db.transaction():
            # Task 4.3 re-check: the binding validator runs inside the
            # same transactional write boundary that persists the
            # ready snapshot. A drift that lands between an external
            # preflight and this completion is refused and rolled back.
            _safe_validate_workflow_binding(session_id)
            plan = _validate_preparation_target(session_id, plan_revision, take_id)
            if classify_plan_authoring(plan) != PLAN_AUTHORING_KIND_PRE_AUTHORING_EXPERT:
                raise AuthoringEvidenceInvalid(
                    "raw complete_preparation cannot create an authoring-v1 snapshot"
                )
            existing = _prepared_take_row(session_id, plan_revision, take_id)
            if existing is None:
                raise PreparedTakeConflict(
                    f"prepared take {take_id!r} has no pending row; begin it first"
                )
            current_snapshot = (
                existing["final_prompt"],
                existing["effective_state"],
                existing["mapping_version"],
                existing["compiler_version"],
                existing["provenance"],
            )
            if existing["status"] != PREPARED_TAKE_STATUS_PENDING:
                if _snapshots_equal(current_snapshot, desired):
                    return _decode_prepared_take(existing)
                raise PreparedTakeConflict(
                    f"prepared take {take_id!r} at plan revision {plan_revision} "
                    f"is immutable {existing['status']} history and differs from "
                    f"the requested snapshot"
                )
            now = db.now()
            db.run(
                "UPDATE prepared_take SET final_prompt = ?, effective_state = ?, "
                "mapping_version = ?, compiler_version = ?, provenance = ?, "
                "status = ?, updated_at = ? WHERE id = ? AND status = ?",
                final_prompt, encoded_state, mapping_version, compiler_version,
                encoded_provenance, PREPARED_TAKE_STATUS_READY, now,
                existing["id"], PREPARED_TAKE_STATUS_PENDING,
            )
            row = _prepared_take_row(session_id, plan_revision, take_id)
            if row is None or row["status"] != PREPARED_TAKE_STATUS_READY:
                raise PreparedTakePersistenceError(
                    f"prepared take {take_id!r} did not reach ready state"
                )
            return _decode_prepared_take(row)
    except (
        SessionNotFound,
        SessionNotInResourceMode,
        PlanRevisionStale,
        PlanValidationError,
        PreparedTakeConflict,
        PreparedTakePersistenceError,
        workflow_binding.WorkflowChanged,
    ):
        raise
    except Exception as exc:
        raise PreparedTakePersistenceError(
            f"could not persist ready prepared take {take_id!r}: {exc}"
        ) from exc


def complete_authoring_preparation(result: Any) -> dict:
    """Persist only an opaque snapshot produced by the authoring server builder."""
    import resource_preparation

    if not resource_preparation._is_sealed_authoring_result(result):
        raise AuthoringEvidenceInvalid(
            "authoring persistence requires a sealed server-built result"
        )

    session_id = result.session_id
    plan_revision = result.plan_revision
    take_id = result.take_id
    encoded_state = _encode_snapshot_json(result.effective_state, "effective_state")
    encoded_provenance = _encode_snapshot_json(result.provenance, "provenance")
    desired = (
        result.final_prompt,
        encoded_state,
        result.mapping_version,
        result.compiler_version,
        encoded_provenance,
    )
    try:
        with db.transaction():
            # Task 4.3 re-check: the binding validator runs inside the
            # same transactional write boundary that seals the authoring
            # snapshot. A drift that lands between the early preflight
            # and this sealed completion is refused and rolled back; no
            # ``prepared_take`` status update, no ``linked_shot_id``
            # change, and no ``shot`` row survives.
            _safe_validate_workflow_binding(session_id)
            plan = _validate_preparation_target(session_id, plan_revision, take_id)
            if classify_plan_authoring(plan) != PLAN_AUTHORING_KIND_MANUAL:
                raise AuthoringEvidenceInvalid(
                    "sealed manual persistence requires manual authoring-v1"
                )
            existing = _prepared_take_row(session_id, plan_revision, take_id)
            if existing is None:
                raise PreparedTakeConflict(
                    f"prepared take {take_id!r} has no pending row; begin it first"
                )
            current_snapshot = (
                existing["final_prompt"],
                existing["effective_state"],
                existing["mapping_version"],
                existing["compiler_version"],
                existing["provenance"],
            )
            if existing["status"] != PREPARED_TAKE_STATUS_PENDING:
                if _snapshots_equal(current_snapshot, desired):
                    validate_authoring_prepared_evidence(
                        session_id, plan_revision, take_id, row=existing,
                    )
                    return _decode_prepared_take(existing)
                raise PreparedTakeConflict(
                    f"prepared take {take_id!r} at plan revision {plan_revision} "
                    f"is immutable {existing['status']} history and differs from "
                    "the requested snapshot"
                )
            candidate_row = {
                "id": existing["id"],
                "session_id": session_id,
                "plan_revision": plan_revision,
                "take_id": take_id,
                "final_prompt": result.final_prompt,
                "effective_state": encoded_state,
                "mapping_version": result.mapping_version,
                "compiler_version": result.compiler_version,
                "provenance": encoded_provenance,
                "status": PREPARED_TAKE_STATUS_READY,
            }
            validate_authoring_prepared_evidence(
                session_id, plan_revision, take_id, row=candidate_row,
            )
            now = db.now()
            db.run(
                "UPDATE prepared_take SET final_prompt = ?, effective_state = ?, "
                "mapping_version = ?, compiler_version = ?, provenance = ?, "
                "status = ?, updated_at = ? WHERE id = ? AND status = ?",
                result.final_prompt,
                encoded_state,
                result.mapping_version,
                result.compiler_version,
                encoded_provenance,
                PREPARED_TAKE_STATUS_READY,
                now,
                existing["id"],
                PREPARED_TAKE_STATUS_PENDING,
            )
            row = _prepared_take_row(session_id, plan_revision, take_id)
            if row is None or row["status"] != PREPARED_TAKE_STATUS_READY:
                raise PreparedTakePersistenceError(
                    f"prepared take {take_id!r} did not reach ready state"
                )
            return _decode_prepared_take(row)
    except (
        SessionNotFound,
        SessionNotInResourceMode,
        PlanRevisionStale,
        PlanValidationError,
        PreparedTakeConflict,
        PreparedTakePersistenceError,
        workflow_binding.WorkflowChanged,
    ):
        raise
    except Exception as exc:
        raise PreparedTakePersistenceError(
            f"could not persist ready prepared take {take_id!r}: {exc}"
        ) from exc


def recover_preparation(session_id: int) -> dict:
    """Return completed, resumable and historical preparation for the plan."""
    plan_revision, plan = _load_current_resource_plan(session_id)
    ordered_take_ids = [
        take["take_id"]
        for take in plan.get("takes", [])
        if isinstance(take, dict) and isinstance(take.get("take_id"), str)
    ]
    active_ids = set(ordered_take_ids)
    rows = db.q(
        "SELECT id, session_id, plan_revision, take_id, final_prompt, "
        "effective_state, mapping_version, compiler_version, provenance, "
        "status, linked_shot_id, created_at, updated_at "
        "FROM prepared_take WHERE session_id = ? ORDER BY id",
        session_id,
    )
    current_by_take: dict[str, dict] = {}
    history: list[dict] = []
    for row in rows:
        is_current_active = (
            int(row["plan_revision"]) == plan_revision
            and row["take_id"] in active_ids
            and row["status"] != PREPARED_TAKE_STATUS_INVALIDATED
        )
        if is_current_active:
            current_by_take[row["take_id"]] = row
        else:
            history.append(_decode_prepared_take(row))

    completed: list[dict] = []
    incomplete: list[dict] = []
    plan_kind = classify_plan_authoring(plan)
    for take_id in ordered_take_ids:
        row = current_by_take.get(take_id)
        if row is None:
            incomplete.append({"take_id": take_id, "status": "missing"})
            continue
        if row["status"] == PREPARED_TAKE_STATUS_GENERATED:
            completed.append(_decode_prepared_take(row))
        elif row["status"] == PREPARED_TAKE_STATUS_READY:
            if plan_kind in (PLAN_AUTHORING_KIND_MANUAL, PLAN_AUTHORING_KIND_AUTOMATIC):
                try:
                    val = validate_authoring_prepared_evidence(
                        session_id, plan_revision, take_id, row=row,
                    )
                    completed.append(dict(val))
                except AuthoringEvidenceInvalid:
                    incomplete.append({
                        "take_id": take_id,
                        "status": "invalid_evidence",
                        "diagnostic": "authoring_evidence_invalid",
                    })
            else:
                completed.append(_decode_prepared_take(row))
        elif row["status"] == PREPARED_TAKE_STATUS_PENDING:
            incomplete.append({"take_id": take_id, "status": "pending"})
        else:
            history.append(_decode_prepared_take(row))
    return {
        "plan_revision": plan_revision,
        "completed": completed,
        "incomplete": incomplete,
        "history": history,
    }


def record_writer_synthesis(
    session_id: int,
    plan_revision: int,
    take_id: str,
    *,
    effective_state: Any,
    mapping_version: str,
    compiler_version: str,
    provenance: Any,
) -> dict:
    """Persist the writer synthesis block on a pending prepared_take row.

    Writer data is resumable preparation state, not a final prompt. The
    atomic update keeps the row pending; ``complete_preparation`` is the
    only transition to ready.

    The write runs inside the same ``db.transaction``
    block the rest of the pipeline uses. The single
    UPDATE targets the exact pending row the
    ``begin_preparation`` step created and the future
    ``complete_preparation`` step will overwrite; the
    WHERE clause pins the status so a second review
    that lands on a row already in ``ready`` /
    ``generated`` / ``invalidated`` status is refused
    with the same ``PreparedTakeConflict`` the
    existing pipeline raises. A row that does not
    exist at all is also refused: the
    ``begin_preparation`` call that task 4.3 runs
    before this one is the only path that creates the
    pending row the function expects to find, and a
    missing row is the boundary case the function
    turns into ``PreparedTakePersistenceError``.

    The function returns the persisted row the same
    way ``complete_preparation`` does, with the
    ``writer_synthesis`` block intact inside the
    ``provenance`` JSON column. A future
    ``load_writer_synthesis`` call reads the same
    block back, byte-for-byte, the same way the
    orchestrator task 4.3 owns reads it.
    """
    if not isinstance(mapping_version, str):
        raise PlanValidationError("mapping_version must be a string")
    if not mapping_version.strip():
        raise PlanValidationError("mapping_version must be a non-empty string")
    if not isinstance(compiler_version, str):
        raise PlanValidationError("compiler_version must be a string")
    if not compiler_version.strip():
        raise PlanValidationError("compiler_version must be a non-empty string")
    encoded_state = _encode_snapshot_json(effective_state, "effective_state")
    encoded_provenance = _encode_snapshot_json(provenance, "provenance")
    try:
        with db.transaction():
            _validate_preparation_target(session_id, plan_revision, take_id)
            existing = _prepared_take_row(session_id, plan_revision, take_id)
            if existing is None:
                raise PreparedTakeConflict(
                    f"prepared take {take_id!r} at plan revision "
                    f"{plan_revision} has no pending row to attach the "
                    f"writer synthesis to; begin_preparation must run "
                    f"first"
                )
            if existing["status"] != PREPARED_TAKE_STATUS_PENDING:
                raise PreparedTakeConflict(
                    f"prepared take {take_id!r} at plan revision "
                    f"{plan_revision} is in {existing['status']!r} status; "
                    f"writer synthesis is only recorded on pending rows, "
                    f"and a row that is already ready, generated or "
                    f"invalidated is history"
                )
            now = db.now()
            db.run(
                "UPDATE prepared_take SET effective_state = ?, "
                "mapping_version = ?, compiler_version = ?, "
                "provenance = ?, updated_at = ? "
                "WHERE id = ? AND status = ?",
                encoded_state, mapping_version, compiler_version,
                encoded_provenance, now,
                existing["id"], PREPARED_TAKE_STATUS_PENDING,
            )
            row = _prepared_take_row(session_id, plan_revision, take_id)
            if row is None or row["status"] != PREPARED_TAKE_STATUS_PENDING:
                raise PreparedTakePersistenceError(
                    f"prepared take {take_id!r} did not remain pending "
                    f"after the writer synthesis write"
                )
            return _decode_prepared_take(row)
    except (
        SessionNotFound,
        SessionNotInResourceMode,
        PlanRevisionStale,
        PlanValidationError,
        PreparedTakeConflict,
        PreparedTakePersistenceError,
        workflow_binding.WorkflowChanged,
    ):
        raise
    except Exception as exc:
        raise PreparedTakePersistenceError(
            f"could not persist writer synthesis for prepared take "
            f"{take_id!r}: {exc}"
        ) from exc
def save_draft(session_id: int, plan: Any, expected_revision: int) -> dict:
    """Save a draft plan with a compare-and-swap on the revision.

    The save runs five steps, in this order:

      1. Confirm the session exists and is in ``resource-v1`` mode. A
         missing session raises ``SessionNotFound``; a session in any
         other mode raises ``SessionNotInResourceMode``. The mode is
         read from the session's ``settings`` JSON, not from a
         dedicated column, so an absent or malformed key reads as
         legacy.

      2. Validate the plan. A malformed plan or one whose selected
         resources are missing from the immutable revision table raises
         ``PlanValidationError``. Validation runs BEFORE the database
         is touched.

      3. Compute structural conflicts between the plan's look and the
         selected resources. The detector runs over the validated plan
         and returns a list of markers naming each resource that
         carries descriptive fields competing with the plan's look.
         The plan's look is the authoritative constant; the resource
         is recorded in provenance; neither is silently overwritten.

      4. Inside a SQLite transaction (``BEGIN IMMEDIATE``), read the
         current revision and compare it to ``expected_revision``. A
         mismatch raises ``PlanRevisionStale`` and the transaction
         rolls back without writing anything. With the read in hand,
         compare the new plan's continuity inputs against the stored
         plan; a continuity change while the session has a
         prepared_take in ``generated`` status raises
         ``PlanConstantsFrozenAfterGenerated`` and the transaction
         rolls back without writing anything.

      5. INSERT a new row (when the session had no plan) or UPDATE
         the existing one with ``plan_revision + 1``. The conflicts
         are written into the plan JSON under a ``conflicts`` key
         so a later GET returns them alongside the draft. In the
         same transaction, every prepared_take row for the session
         whose status is ``pending`` or ``ready`` and whose
         ``plan_revision`` differs from the new revision is moved
         to ``invalidated``. Rows already in ``generated`` or
         ``invalidated`` are immutable history and are not
         touched. The transaction commits on exit.

    Returns a dict with two keys:

      * ``plan_revision`` — the new integer revision the caller
        must echo on the next save;
      * ``conflicts`` — the list of conflict markers computed in
        step 3. An empty list is a normal answer: a plan with no
        selected resources, or with only auxiliary resources,
        produces no conflicts.

    The CAS check plus the transaction wrap together are what pin
    "a stale save must not overwrite a newer draft" to the schema:
    there is no path in this function that bumps a revision by
    more than one, and there is no path that writes without first
    comparing. The constant-change guard is what pins "a finished
    photograph's identity is history": a refused save leaves the
    plan row, every prepared_take row, and every linked shot
    byte-for-byte unchanged.
    """
    session = db.one(
        "SELECT id, settings FROM session WHERE id = ?",
        session_id,
    )
    if session is None:
        raise SessionNotFound(f"session {session_id} not found")
    mode = read_composition_mode(session["settings"])
    if mode != MODE_RESOURCE_V1:
        raise SessionNotInResourceMode(
            f"session {session_id} composition_mode is {mode!r}, "
            f"expected {MODE_RESOURCE_V1!r}"
        )

    # The candidate may echo provenance for an effective value edited in the UI.
    # Compare effective values with the persisted winner before checking that relation.
    validated = validate_draft(plan, check_authoring_effective=False)
    validate_selected_resources(validated["selected_resources"])

    # Step 3: structural conflicts. The detector reads each
    # selected asset_revision out of the database, so it runs
    # BEFORE the write transaction (no point taking the write
    # lock for nothing) but AFTER the database-shape validation.
    # The list is empty when the plan has no selected resources
    # or when every selected resource is auxiliary.
    conflicts = detect_resource_constant_conflicts(validated)
    plan_with_conflicts = dict(validated)
    plan_with_conflicts["conflicts"] = conflicts

    now = db.now()

    with db.transaction():
        current = db.one(
            "SELECT plan_revision, plan_json FROM session_plan "
            "WHERE session_id = ?",
            session_id,
        )
        actual = 0 if current is None else int(current["plan_revision"])
        if actual != expected_revision:
            raise PlanRevisionStale(
                f"session {session_id} plan revision is {actual}, "
                f"expected {expected_revision}; refusing to overwrite "
                f"newer draft"
            )

        old_plan: dict = {}
        if current is not None:
            try:
                old_plan = json.loads(current["plan_json"])
            except json.JSONDecodeError:
                old_plan = {}
            if not isinstance(old_plan, dict):
                old_plan = {}

        has_old_auth = "authoring" in old_plan
        has_new_auth = "authoring" in validated

        if not has_old_auth and has_new_auth:
            raise PlanOwnershipConflict(
                "generic save cannot create authoring on a plan without authoring"
            )

        if has_old_auth and not has_new_auth:
            raise PlanOwnershipConflict(
                "generic save cannot delete authoring from a plan with authoring"
            )

        if has_old_auth and has_new_auth:
            old_auth = old_plan["authoring"]
            new_auth = validated["authoring"]

            # 1. workflow_binding
            if new_auth["workflow_binding"] != old_auth["workflow_binding"]:
                raise PlanOwnershipConflict(
                    "workflow_binding is server-owned and cannot be modified through generic plan save"
                )
            # 2. evidence
            if new_auth["evidence"] != old_auth["evidence"]:
                raise PlanOwnershipConflict(
                    "evidence is server-owned and cannot be modified through generic plan save"
                )
            # 3. look_snapshot
            if new_auth["look_snapshot"] != old_auth["look_snapshot"]:
                raise PlanOwnershipConflict(
                    "look_snapshot is server-owned and cannot be modified through generic plan save"
                )
            # 4. wardrobe_progression
            if new_auth["wardrobe_progression"] != old_auth["wardrobe_progression"]:
                raise PlanOwnershipConflict(
                    "wardrobe_progression is server-owned and cannot be modified through generic plan save"
                )

            # Reconcile shared_state per effective field independently
            reconciled_shared_state = {}
            for field in ("look", "initial_wardrobe"):
                old_val = old_plan.get(field, "")
                new_val = validated.get(field, "")
                old_meta = old_auth["shared_state"][field]
                new_meta = new_auth["shared_state"][field]

                if new_val == old_val:
                    if new_meta != old_meta:
                        raise PlanOwnershipConflict(
                            f"shared_state.{field} metadata cannot be modified when {field} is unchanged"
                        )
                    reconciled_shared_state[field] = dict(old_meta)
                else:
                    reconciled_shared_state[field] = {"origin": "user", "evidence_id": None}

            reconciled_auth = dict(new_auth)
            reconciled_auth["shared_state"] = reconciled_shared_state
            reconciled_auth["evidence"] = list(old_auth["evidence"])
            reconciled_auth = validate_authoring_block(reconciled_auth, validated)
            validated["authoring"] = reconciled_auth
            plan_with_conflicts["authoring"] = reconciled_auth

            if len(validated["takes"]) > len(old_plan["takes"]):
                validate_authoring_count(len(validated["takes"]))

        encoded = json.dumps(
            plan_with_conflicts, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )

        # Step 4: continuity guard. The comparison strips the
        # ``conflicts`` key the prior save may have written so a
        # re-save of the same draft (which is a legal CAS bump)
        # does not read as a continuity change.
        continuity_changed = False
        constants_changed = False
        if current is not None:
            old_compare = {
                key: value for key, value in old_plan.items()
                if key != "conflicts"
            }
            new_compare = {
                key: value for key, value in validated.items()
                if key != "conflicts"
            }
            continuity_changed = _plan_continuity_changed(
                old_compare, new_compare,
            )
            constants_changed = _plan_constants_changed(
                old_compare, new_compare,
            )
            if continuity_changed and has_generated_take(session_id):
                raise PlanConstantsFrozenAfterGenerated(
                    f"session {session_id} has at least one generated "
                    "prepared_take; continuity fields (look, "
                    "initial_wardrobe, selected_resources, "
                    "authoring.scene_anchor, authoring.variation_policy, "
                    "authoring.workflow_binding, authoring.look_snapshot) "
                    "cannot be changed. Start a new session to change them."
                )
        new_revision = actual + 1
        if actual == 0:
            db.run(
                "INSERT INTO session_plan "
                "(session_id, mode, plan_revision, plan_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                session_id, MODE_RESOURCE_V1, new_revision, encoded, now, now,
            )
        else:
            db.run(
                "UPDATE session_plan SET plan_revision = ?, plan_json = ?, "
                "updated_at = ? WHERE session_id = ?",
                new_revision, encoded, now, session_id,
            )
        # Step 5: explicit invalidation. The pass is the single
        # implementation ``invalidate_ungenerated_prepared_takes``
        # owns; calling it from here keeps the invalidation SQL
        # in one place and the test for "ungenerated rows were
        # invalidated" reads against the same function the
        # production save calls. Generated and already-
        # invalidated rows are history; the WHERE clause in the
        # pass leaves them alone. Running the pass inside the
        # same transaction as the plan write means a refused
        # save is the only path that could leave the table in
        # an inconsistent state, and a refused save never
        # reaches this call.
        #
        # The boundary the ``session-plan`` spec asks for lives
        # here. A save that changes the established constants
        # (look, initial_wardrobe or selected_resources) keeps the
        # strict policy: every ungenerated row at an older revision
        # is invalidated. A save that only edits
        # ``wardrobe_changes`` or the take order narrows the
        # pass to the take IDs whose effective wardrobe actually
        # changed between revisions, plus any take removed from
        # the new plan. A take whose effective state and prompt
        # remain byte-for-byte identical in the new plan keeps
        # its prior ``ready`` row untouched, which is the
        # "reuse the prior preparation" rule the 6.2 acceptance
        # demands. The two policies live side by side so a
        # continuity change after a generated take still raises
        # ``PlanConstantsFrozenAfterGenerated`` above without
        # reaching this pass.
        if current is None:
            affected_for_invalidation: set[str] | None = None
            new_take_ids_for_invalidation: set[str] | None = None
        else:
            if constants_changed:
                affected_for_invalidation = None
                new_take_ids_for_invalidation = None
            else:
                affected_for_invalidation = (
                    _compute_affected_take_ids_for_plan_change(
                        old_plan, validated,
                    )
                )
                new_take_ids_for_invalidation = _take_id_set(validated)
        invalidate_ungenerated_prepared_takes(
            session_id, new_revision,
            affected_take_ids=affected_for_invalidation,
            new_take_ids=new_take_ids_for_invalidation,
        )
        invalidate_plan_approval(session_id)
    return {"plan_revision": new_revision, "conflicts": conflicts}


def _update_session_origin(session_id: int, kind: str) -> None:
    """Stamp session.origin for the path that just wrote a shot."""
    session = db.one("SELECT origin FROM session WHERE id = ?", session_id)
    if not session:
        return
    current = session.get("origin") or ""
    if not current:
        db.run("UPDATE session SET origin = ? WHERE id = ?", kind, session_id)
    elif current != kind and current != "mixed":
        db.run("UPDATE session SET origin = 'mixed' WHERE id = ?", session_id)


def _validate_take_submission_fields(take_def: dict, take_id: str) -> dict:
    """Validate all take fields consumed by submission against ShotIn semantics.

    Compatible with ShotIn:
      - label: str (default take_id)
      - negative: str | None (default session/model negative)
      - reference: bool (strictly boolean, default False)
      - reference_strength: float | None (strictly float or int, not bool)
      - seed: int (strictly int, not bool, default 0)

    Raises PlanValidationError (HTTP 422) if any field has an incompatible type.
    """
    label = take_def.get("label")
    if label is not None and not isinstance(label, str):
        raise PlanValidationError(
            f"take {take_id!r} field 'label' must be a string, got {type(label).__name__}"
        )

    negative = take_def.get("negative")
    if negative is not None and not isinstance(negative, str):
        raise PlanValidationError(
            f"take {take_id!r} field 'negative' must be a string, got {type(negative).__name__}"
        )

    reference = take_def.get("reference")
    if reference is not None:
        if not isinstance(reference, bool):
            raise PlanValidationError(
                f"take {take_id!r} field 'reference' must be a boolean, got {type(reference).__name__}"
            )
    else:
        reference = False

    reference_strength = take_def.get("reference_strength")
    if reference_strength is not None:
        if isinstance(reference_strength, bool) or not isinstance(reference_strength, (int, float)):
            raise PlanValidationError(
                f"take {take_id!r} field 'reference_strength' must be a float or None, got {type(reference_strength).__name__}"
            )
        reference_strength = float(reference_strength)

    seed = take_def.get("seed")
    if seed is not None:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise PlanValidationError(
                f"take {take_id!r} field 'seed' must be an integer, got {type(seed).__name__}"
            )
    else:
        seed = 0

    return {
        "label": label if label is not None else take_id,
        "negative": negative,
        "reference": reference,
        "reference_strength": reference_strength,
        "seed": seed,
    }


def submit_prepared_take(
    session_id: int,
    plan_revision: int,
    take_id: str,
) -> dict:
    """Submit a finalized prepared take to shot creation and the serial queue.

    Task 4.5 of ``adopt-resource-session-planning``.

    Connects a finalized, ready prepared take to existing shot creation and
    the serial queue. Preserves uniqueness per preparation revision:
    submitting the same prepared revision multiple times (including concurrent
    calls or HTTP retries) produces exactly one shot row and returns the
    already linked snapshot.

    The final prompt stored in ``prepared_take.final_prompt`` is sent directly
    to the shot row without re-composing trigger, base prompt or look.
    """
    if not isinstance(plan_revision, int):
        raise PlanValidationError(
            f"plan_revision must be an integer, got {type(plan_revision).__name__}"
        )
    if plan_revision <= 0:
        raise PlanValidationError("plan_revision must be greater than zero")
    if not isinstance(take_id, str) or not take_id.strip():
        raise PlanValidationError("take_id must be a non-empty string")

    # Task 4.3: the workflow binding the plan froze must still match the
    # live workflow row. Runs BEFORE the submission transaction so a drifted
    # binding cannot leave a freshly inserted ``shot`` row behind. The
    # idempotent generated-retry branch is covered by the same pre-check:
    # re-submitting an already-generated take is a no-op, but the plan's
    # identity still has to be intact for the answer to be authoritative.
    _safe_validate_workflow_binding(session_id)

    try:
        with db.transaction():
            # Task 4.3 re-check: validate again inside the same
            # transactional write boundary to catch drift between the
            # preflight and the submission write. The validator is
            # read-only; a refusal rolls the transaction back without
            # touching the shot or prepared_take row. The idempotent
            # ``status == GENERATED`` short-circuit above is also
            # gated by the in-transaction re-check, so a drift that
            # lands between the preflight and the read of the existing
            # row cannot make the helper return a stale shot id.
            _safe_validate_workflow_binding(session_id)
            session = db.one("SELECT id, settings, model_id FROM session WHERE id = ?", session_id)
            if session is None:
                raise SessionNotFound(f"session {session_id} not found")
            mode = read_composition_mode(session["settings"])
            if mode != MODE_RESOURCE_V1:
                raise SessionNotInResourceMode(
                    f"session {session_id} composition_mode is {mode!r}, "
                    f"expected {MODE_RESOURCE_V1!r}"
                )

            current_rev, plan = _load_current_resource_plan(session_id)
            if current_rev != plan_revision:
                raise PlanRevisionStale(
                    f"session {session_id} plan revision is {current_rev}, "
                    f"requested submission revision is {plan_revision}"
                )
            if not is_plan_review_approved(session_id, plan_revision):
                raise PlanReviewNotApproved(
                    f"session {session_id} plan revision {plan_revision} has not been approved for submission"
                )
            take_ids = {
                t.get("take_id")
                for t in plan.get("takes", [])
                if isinstance(t, dict)
            }
            if take_id not in take_ids:
                raise PlanValidationError(
                    f"take_id {take_id!r} is not present in plan revision {plan_revision}"
                )
            take_def = next(
                (t for t in plan.get("takes", []) if isinstance(t, dict) and t.get("take_id") == take_id),
                None,
            )
            if take_def is None:
                raise PlanValidationError(
                    f"take_id {take_id!r} is not present in plan revision {plan_revision}"
                )
            validated_fields = _validate_take_submission_fields(take_def, take_id)

            existing = _prepared_take_row(session_id, plan_revision, take_id)
            if existing is None:
                raise PreparedTakeConflict(
                    f"prepared take {take_id!r} at plan revision {plan_revision} "
                    f"has no prepared row; finalize it first"
                )

            # Idempotent retry: return existing shot if already generated
            if existing["status"] == PREPARED_TAKE_STATUS_GENERATED:
                linked_id = existing["linked_shot_id"]
                if linked_id is not None:
                    shot = db.one("SELECT id FROM shot WHERE id = ?", linked_id)
                    if shot is not None:
                        decoded = _decode_prepared_take(existing)
                        decoded["shot_id"] = linked_id
                        return decoded
                raise PreparedTakePersistenceError(
                    f"prepared take {take_id!r} is marked generated but linked shot {linked_id} is missing"
                )

            if existing["status"] == PREPARED_TAKE_STATUS_INVALIDATED:
                raise PreparedTakeConflict(
                    f"prepared take {take_id!r} at plan revision {plan_revision} "
                    f"is invalidated history"
                )

            if existing["status"] != PREPARED_TAKE_STATUS_READY:
                raise PreparedTakeConflict(
                    f"prepared take {take_id!r} at plan revision {plan_revision} "
                    f"is in {existing['status']!r} status and not ready for submission"
                )

            plan_kind = classify_plan_authoring(plan)
            if plan_kind in (PLAN_AUTHORING_KIND_MANUAL, PLAN_AUTHORING_KIND_AUTOMATIC):
                validate_authoring_prepared_evidence(
                    session_id, plan_revision, take_id, row=existing,
                )

            final_prompt = existing["final_prompt"]
            if not isinstance(final_prompt, str) or not final_prompt.strip():
                raise PlanValidationError(
                    f"prepared take {take_id!r} has invalid or empty final_prompt"
                )

            model = db.one("SELECT base_negative FROM model WHERE id = ?", session["model_id"])
            base_negative = model["base_negative"] if model else ""

            shot_index = db.one(
                "SELECT COALESCE(MAX(shot_index), -1) AS m FROM shot WHERE session_id = ?",
                session_id,
            )["m"] + 1

            shot_label = validated_fields["label"]
            negative = validated_fields["negative"] if validated_fields["negative"] is not None else base_negative
            use_ref = 1 if validated_fields["reference"] else 0
            ref_strength = validated_fields["reference_strength"]
            seed = validated_fields["seed"]

            now = db.now()
            shot_id = db.run(
                "INSERT INTO shot (session_id, shot_index, shot_label, prompt, negative, "
                "use_reference, reference_strength, seed, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                session_id,
                shot_index,
                shot_label,
                final_prompt,
                negative,
                use_ref,
                ref_strength,
                seed,
                now,
            )

            db.run(
                "UPDATE prepared_take SET status = ?, linked_shot_id = ?, updated_at = ? "
                "WHERE id = ? AND status = ? AND linked_shot_id IS NULL",
                PREPARED_TAKE_STATUS_GENERATED,
                shot_id,
                now,
                existing["id"],
                PREPARED_TAKE_STATUS_READY,
            )

            updated = _prepared_take_row(session_id, plan_revision, take_id)
            if (
                updated is None
                or updated["status"] != PREPARED_TAKE_STATUS_GENERATED
                or updated["linked_shot_id"] != shot_id
            ):
                raise PreparedTakePersistenceError(
                    f"could not link prepared take {take_id!r} to shot {shot_id}"
                )

            session_row = db.one("SELECT status FROM session WHERE id = ?", session_id)
            if session_row and session_row["status"] in ("done", "cancelled", "failed"):
                db.run("UPDATE session SET status = 'draft' WHERE id = ?", session_id)
            _update_session_origin(session_id, "written")

            decoded = _decode_prepared_take(updated)
            decoded["shot_id"] = shot_id
            return decoded
    except (
        SessionNotFound,
        SessionNotInResourceMode,
        PlanRevisionStale,
        PlanValidationError,
        PreparedTakeConflict,
        PreparedTakePersistenceError,
        PlanReviewNotApproved,
        workflow_binding.WorkflowChanged,
    ):
        raise
    except Exception as exc:
        raise PreparedTakePersistenceError(
            f"could not submit prepared take {take_id!r}: {exc}"
        ) from exc


def is_plan_review_approved(session_id: int, plan_revision: int) -> bool:
    """Check if the specified plan revision has an authoritative approved review."""
    row = db.one(
        "SELECT plan_revision FROM session_plan_approval WHERE session_id = ?",
        session_id,
    )
    if row is None:
        return False
    return int(row["plan_revision"]) == int(plan_revision)


def get_approved_plan_revision(session_id: int) -> int | None:
    """Return the approved plan revision for session_id if still current."""
    row = db.one(
        "SELECT plan_revision FROM session_plan_approval WHERE session_id = ?",
        session_id,
    )
    if row is None:
        return None
    return int(row["plan_revision"])


def approve_plan_review(session_id: int, plan_revision: int) -> dict:
    """Authoritatively record approval of the plan review for session_id and plan_revision.

    Validates that:
    1. Session exists and is in resource-v1 mode.
    2. Current plan revision matches plan_revision (CAS check).
    3. Task 4.3: the workflow binding the plan froze still matches the live
       workflow row the session points to. The validator runs BEFORE the
       approval transaction so a drift cannot leave an approval row behind.
    """
    if not isinstance(plan_revision, int) or plan_revision <= 0:
        raise PlanValidationError("plan_revision must be a positive integer")

    _safe_validate_workflow_binding(session_id)

    with db.transaction():
        # Task 4.3 re-check: validate again inside the same
        # transactional write boundary to catch drift between the
        # preflight and the approval write. The validator is
        # read-only; a refusal rolls the transaction back without
        # touching the approval row.
        _safe_validate_workflow_binding(session_id)
        session = db.one("SELECT id, settings FROM session WHERE id = ?", session_id)
        if session is None:
            raise SessionNotFound(f"session {session_id} not found")
        mode = read_composition_mode(session["settings"])
        if mode != MODE_RESOURCE_V1:
            raise SessionNotInResourceMode(
                f"session {session_id} composition_mode is {mode!r}, expected {MODE_RESOURCE_V1!r}"
            )
        current_rev, plan = _load_current_resource_plan(session_id)
        if current_rev != plan_revision:
            raise PlanRevisionStale(
                f"session {session_id} plan revision is {current_rev}, requested {plan_revision}"
            )
        plan_kind = classify_plan_authoring(plan)
        if plan_kind in (PLAN_AUTHORING_KIND_MANUAL, PLAN_AUTHORING_KIND_AUTOMATIC):
            ready_rows = db.q(
                "SELECT * FROM prepared_take WHERE session_id = ? AND plan_revision = ? AND status = ? AND linked_shot_id IS NULL",
                session_id,
                plan_revision,
                PREPARED_TAKE_STATUS_READY,
            )
            for r in ready_rows:
                validate_authoring_prepared_evidence(
                    session_id, plan_revision, str(r["take_id"]), row=r,
                )
        now = db.now()
        db.run(
            "INSERT INTO session_plan_approval (session_id, plan_revision, approved_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "plan_revision = excluded.plan_revision, approved_at = excluded.approved_at",
            session_id, plan_revision, now,
        )
        return {
            "session_id": session_id,
            "plan_revision": plan_revision,
            "approved": True,
            "approved_at": now,
        }


def invalidate_plan_approval(session_id: int) -> None:
    """Invalidate any existing plan review approval for session_id."""
    db.run("DELETE FROM session_plan_approval WHERE session_id = ?", session_id)
