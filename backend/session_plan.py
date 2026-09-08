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

  * A "constants are frozen after a generated take" guard. Once
    a session has at least one prepared_take in ``generated``
    status, a save that changes the look, the initial wardrobe,
    or the selected resources is refused with
    ``PlanConstantsFrozenAfterGenerated``. The refusal leaves the
    existing plan row, the existing prepared_take rows, and every
    linked shot byte-for-byte unchanged. Wardrobe changes and take
    reorders are not constant changes; they are still allowed and
    they still invalidate ungenerated rows.
"""
from __future__ import annotations

import json
from typing import Any

import db


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

    Resource-v1 binds the session's identity to its model and its
    look to the plan's ``look`` field. Once a prepared_take has
    been generated, those constants are history: a later save
    that rewrites ``look``, ``initial_wardrobe`` or
    ``selected_resources`` would silently pretend a finished
    photograph used a state it did not. The guard refuses the
    save before any write, leaves the existing plan row and every
    prepared_take row byte-for-byte unchanged, and asks the
    operator to start a new session for a new look.

    Wardrobe changes and take reorders are NOT constant changes:
    they remain legal after a generated take, and they still
    invalidate ungenerated prepared_take rows through the same
    pass the rest of the saves run.
    """


class PreparedTakeConflict(Exception):
    """A prepared-take snapshot would overwrite immutable history."""


class PreparedTakePersistenceError(Exception):
    """A prepared-take write failed and was rolled back."""


# -- Validation -------------------------------------------------------------


def validate_draft(plan: Any) -> dict:
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
        if scope not in VALID_WARDROBE_SCOPES:
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

    return {
        "version": MODE_RESOURCE_V1,
        "look": look,
        "initial_wardrobe": initial_wardrobe,
        "takes": list(takes),
        "selected_resources": normalized_selected,
        "wardrobe_changes": normalized_changes,
    }


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
    """Return True if the new plan changes the session's constants.

    The three fields the spec binds to the session's identity are
    ``look``, ``initial_wardrobe`` and ``selected_resources``. A
    change to any of them is a constant change. Other fields —
    ``wardrobe_changes`` and ``takes`` order or content — are
    explicit per-take decisions and are not constant changes; they
    remain editable after a generated take and they still
    invalidate ungenerated prepared_take rows.

    The function compares structurally. Two ``selected_resources``
    lists are equal when they carry the same triples in the same
    order, which is what the validator's normalization guarantees
    (the validator builds the list in the order the caller wrote
    it, so the order is the operator's). Reordering the
    ``selected_resources`` list is a constant change because the
    list is the operator's statement of "these resources bound
    the session's identity, in this order"; a save that reorders
    it is a different statement.
    """
    if old_plan.get("look", "") != new_plan.get("look", ""):
        return True
    if old_plan.get("initial_wardrobe", "") != new_plan.get(
        "initial_wardrobe", "",
    ):
        return True
    if old_plan.get("selected_resources", []) != new_plan.get(
        "selected_resources", [],
    ):
        return True
    return False


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


def invalidate_ungenerated_prepared_takes(
    session_id: int, kept_plan_revision: int,
) -> None:
    """Mark every ungenerated prepared_take row for the session as ``invalidated``.

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
            value, ensure_ascii=False, separators=(",", ":"),
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
    """Persist pending before lengthy work and return the durable row."""
    try:
        with db.transaction():
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
    ):
        raise
    except Exception as exc:
        raise PreparedTakePersistenceError(
            f"could not persist pending prepared take {take_id!r}: {exc}"
        ) from exc


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
    """Atomically transition one durable pending row to ready."""
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
            _validate_preparation_target(session_id, plan_revision, take_id)
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
                if current_snapshot == desired:
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
    for take_id in ordered_take_ids:
        row = current_by_take.get(take_id)
        if row is None:
            incomplete.append({"take_id": take_id, "status": "missing"})
            continue
        if row["status"] in (
            PREPARED_TAKE_STATUS_READY,
            PREPARED_TAKE_STATUS_GENERATED,
        ):
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
         compare the new plan's constants (``look``,
         ``initial_wardrobe``, ``selected_resources``) against the
         stored plan; a constant change while the session has a
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

    validated = validate_draft(plan)
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

    encoded = json.dumps(
        plan_with_conflicts, ensure_ascii=False, separators=(",", ":"),
    )
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
        # Step 4: constant-change guard. The comparison strips the
        # ``conflicts`` key the prior save may have written so a
        # re-save of the same draft (which is a legal CAS bump)
        # does not read as a constant change.
        if current is not None:
            try:
                old_plan = json.loads(current["plan_json"])
            except json.JSONDecodeError:
                old_plan = {}
            if not isinstance(old_plan, dict):
                old_plan = {}
            old_compare = {
                key: value for key, value in old_plan.items()
                if key != "conflicts"
            }
            new_compare = {
                key: value for key, value in validated.items()
                if key != "conflicts"
            }
            if _plan_constants_changed(old_compare, new_compare):
                if has_generated_take(session_id):
                    raise PlanConstantsFrozenAfterGenerated(
                        f"session {session_id} has at least one generated "
                        f"prepared_take; constants (look, initial_wardrobe, "
                        f"selected_resources) cannot be changed. Start a "
                        f"new session for a new look."
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
        invalidate_ungenerated_prepared_takes(session_id, new_revision)
    return {"plan_revision": new_revision, "conflicts": conflicts}
