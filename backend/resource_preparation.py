"""Deterministic preparation of resource-v1 take inputs (task 4.1 of
``adopt-resource-session-planning``).

The module is a small, dedicated backend layer that turns a session's
resource-v1 plan plus explicit take choices plus a thin manual
fallback into the structured input a future prompt-assembly step
(4.3) and the existing ``session_plan`` persistence (3.4) consume.
It is deliberately narrow:

  * It reads the validated plan, the effective wardrobe resolved by
    ``session_plan.resolve_effective_wardrobes``, the EXACT
    immutable revisions the plan selected through
    ``resource_store.get_revision``, and the explicit descriptive
    choices the take carries (``camera``, ``framing``, ``pose``,
    ``expression``). It does NOT re-derive any of those facts, does
    NOT consult network, GPU, ComfyUI or an assistant, and does NOT
    introduce a new source of truth for field roles or for
    revision identity. ``resource_prompts.PREPARATION_FIELD_MAPPING``
    is the single source of truth for which fields the contract
    allows into a prompt.

  * It produces a deterministic dict that names the take, the
    effective state, the per-resource descriptive inputs the
    contract classified as ``descriptive_input``, the writer
    guidance kept as bounded reference data, the take's own
    explicit choices, the manual completion the caller supplied
    (only for the four allowed take-choice keys, only for choices
    the take did NOT already establish), and the provenance the
    future snapshot will read. Two calls with the same arguments
    return the same dict, byte-for-byte, with no wall-clock
    timestamps and no nondeterministic ordering.

  * It refuses a resource that carries a field the contract does
    not name (the ``unmapped`` sentinel the preparation contract
    publishes) with a readable, field-specific message. The
    refusal happens BEFORE the structure is built, so a refused
    resource cannot reach the prompt silently. The same rule pins
    a selection-metadata value, a writer-guidance value and an
    intentionally-unused value to their non-prompt roles: the
    function never copies any of them into the prompt clause set.

  * It refuses to override fixed session state through the manual
    completion path. The manual completion is a fallback for
    take-level descriptive choices the take did not yet
    establish; the keys it accepts are an explicit, closed
    allowlist (``camera``, ``framing``, ``pose``, ``expression``).
    The fixed session state (look, initial wardrobe, effective
    wardrobe, identity, plan keys, snapshot fields) is
    authoritative and is never reachable through manual
    completion.

Three rules are pinned here and nowhere else:

  1. A resource is read by its EXACT
     ``(library_key, source_id, content_digest)`` triple, the
     triple the plan validated. A new immutable revision that
     appears after the plan was saved is NOT substituted. The
     function looks the revision up by the triple the plan
     carries, and an unknown triple is a refusal with the same
     message the planner would surface.

  2. ``descriptive_input`` is the only role the function copies
     into the resource descriptive bucket the future assembly
     step reads. Every other role
     (``identity``, ``selection_metadata``, ``writer_guidance``,
     ``auxiliary_data``, ``intentionally_unused``) stays in its
     own bucket the structure preserves for provenance, so a
     future reviewer can tell at a glance where each piece of
     information came from. An unknown / unmapped field is a
     refusal that names the field, the kind and the role the
     contract returned for it. A writer-guidance string that
     LOOKS like an instruction is still data: the structure
     keeps it under the ``writer_guidance`` bucket, the future
     prompt-assembly reads it from there, and the function never
     reinterprets it as a command.

  3. The function is pure: it does not write to the database, it
     does not start a session, it does not consult a wall clock,
     and it does not depend on a LLM, an assistant endpoint,
     ComfyUI, a GPU or a network. Task 4.1 produces the
     preparation; it does NOT finalise the take. Finalisation,
     conflict/adaptation handling, optional assistant synthesis,
     and the exact final-prompt snapshot semantics are tasks
     4.2 / 4.3 / 4.4, and this module does not own any of them.

The module does NOT:

  * invent a second interpretation of the weight adaptation the
    preparation contract pins (a numeric selection parameter is
    selection metadata, not a prompt emphasis);
  * split a fused scene's prose into camera, act or room
    clauses, rewrite it heuristically, claim rendering parity,
    or assert semantic contradiction detection;
  * re-implement wardrobe inheritance, ``this_take``,
    ``from_here``, plan-constant freezing, or prepared-take
    invalidation — every one of those has a single source of
    truth in ``session_plan``;
  * persist a final prompt, a snapshot row, or a ``ready``
    status. The function ``prepare_take_inputs`` is a pure
    deterministic builder; persistence belongs to tasks 4.2+;
  * depend on a translation service, an LLM, the network or a
    running ComfyUI.

The ``manual_completion`` argument is the path a user takes
when no assistant is available. It is a flat dict of explicit
take-level descriptive values, restricted to the four names
the OpenSpec names for take variation
(``camera``, ``framing``, ``pose``, ``expression``). Any other
key is refused with a readable message; an attempt to override
a choice the take already established is refused the same way.
The structure the function returns carries the manual completion
as a separate bucket so the future assembly step can tell which
descriptive input came from the take and which came from a
deliberate user decision. The two are joined into a single
deterministic descriptive clause set only at the explicit
``assemble_descriptive_clauses`` call.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

import db
import resource_prompts
import resource_store
import session_plan


# -- Versioning -------------------------------------------------------------


# A single explicit string the future snapshot/final-prompt step
# (task 4.4) reads as the preparation version. The version bumps
# when the structure of the preparation output changes in a way
# the snapshot row would care about. The value is the single
# source of truth a test, a UI message or a code review can read.
PREPARATION_VERSION: str = "preparation-v1"

# A single explicit string the snapshot row records as the
# mapping version. It is the version of
# ``resource_prompts.PREPARATION_FIELD_MAPPING`` the structure
# was built against. Bumping it is a contract change that
# requires the snapshot row to either be regenerated or be
# rejected by the snapshot's compatibility check. A future task
# 4.4 owns the compatibility logic; this module only writes
# the string.
MAPPING_VERSION: str = "resource-prompts-v1"

# A single explicit string the snapshot row records as the
# compiler version. Task 4.1 does not run a compiled binary; the
# version is the module identity, the same convention the rest
# of the project uses for non-compiled layers.
COMPILER_VERSION: str = "resource-preparation-v1"


# -- Take-level descriptive choices ----------------------------------------


# The four explicit take-level descriptive choices the OpenSpec
# names in the ``session-plan`` capability: a take SHALL
# expose its ``camera``, ``framing``, ``pose`` and
# ``expression`` choices. They are the descriptive inputs the
# take carries; the resource layer adds its own descriptive
# inputs on top of them, and the assembly step reads the two
# together.
#
# The same closed set is the allowlist for the manual
# completion path: ``manual_completion`` is a thin fallback
# for choices the take did not yet establish, and the keys it
# accepts are exactly the names in this tuple. Any other key
# is refused with a readable message. The closed allowlist is
# what keeps fixed session state authoritative: ``look``,
# ``wardrobe``, ``initial_wardrobe``, ``identity``,
# ``final_prompt``, ``take_id``, ``session_id``, ``provenance``
# and every snapshot / structural / resource key is unreachable
# through manual completion by construction.
TAKE_DESCRIPTIVE_CHOICES: frozenset[str] = frozenset({
    "camera", "framing", "pose", "expression",
})


# -- Errors -----------------------------------------------------------------


class PreparationError(ValueError):
    """A preparation refusal.

    Every refusal the layer raises is a subclass of this
    exception. The message is non-empty and names the resource
    the layer is refusing and the reason it is being refused;
    a future HTTP layer maps the exception to a 422 body the
    operator can read. A refused preparation never reaches the
    structure-returning code path, so the exception class
    documents the rule the layer enforced.
    """


class PreparationFieldError(PreparationError):
    """A field on a resource is not allowed into the prompt.

    The field is either unmapped (the contract does not name it
    for the resource's kind), or it carries a role that is
    reserved for a future synthesis step
    (``writer_guidance``), or it is a role mismatch on a scene
    kind (an auxiliary record key, a payload-only field, on a
    scene entry). The exception is the only path the layer
    uses to refuse, so a refused resource is never silently
    promoted to prompt content.
    """


class PreparationRevisionMissing(PreparationError):
    """The plan's selected revision triple is not in the store.

    The plan validation in ``session_plan`` already refuses an
    unknown triple at save time, so this exception is the
    boundary case: the revision was deleted between the save
    and the preparation. The message names the triple.
    """


class PreparationArgumentError(PreparationError):
    """The caller's arguments are not a valid preparation target.

    The layer does not raise on a missing session, on a session
    that is not in resource-v1 mode, on a missing plan, on a
    stale plan revision, or on a take_id the plan does not
    name — ``session_plan`` already does. The layer raises
    this exception only for arguments the function itself
    validates: a take choice of the wrong type, a manual
    completion key outside the closed allowlist, an attempt
    to override an existing take choice, and so on.
    """


# -- Manual completion validation -----------------------------------------


def _take_choices_from_take(take: dict) -> dict[str, str]:
    """Extract the take's own descriptive choices from ``take``.

    Only the four names in ``TAKE_DESCRIPTIVE_CHOICES`` are
    read. Any other field the take carries is ignored by this
    function: ``session_plan`` already preserves the take's
    full payload verbatim in the plan, and a future task can
    add new take fields without breaking this layer. Values
    must be non-empty strings; a value of any other shape is
    reported with a field-specific refusal so a caller knows
    which take field to fix.

    The function returns a fresh dict; mutating the result
    does not change the plan. The four keys are returned in
    canonical order so a downstream merge with the manual
    completion is deterministic.
    """
    out: dict[str, str] = {}
    for name in sorted(TAKE_DESCRIPTIVE_CHOICES):
        if name not in take:
            continue
        value = take[name]
        if not isinstance(value, str):
            raise PreparationArgumentError(
                f"take choice {name!r} must be a string, got "
                f"{type(value).__name__}"
            )
        if not value:
            raise PreparationArgumentError(
                f"take choice {name!r} must be a non-empty string"
            )
        out[name] = value
    return out


def _validate_manual_completion(
    value: Any,
    take_choices: Mapping[str, str],
) -> dict[str, str]:
    """Normalize and validate a ``manual_completion`` argument.

    The manual completion is a flat dict of explicit
    take-level descriptive values. The function enforces a
    closed allowlist of keys (the four names in
    ``TAKE_DESCRIPTIVE_CHOICES``): every key MUST be one of
    those four names, every value MUST be a non-empty string,
    and a key the take already establishes is refused so the
    fixed state is not silently overridden.

    The closed allowlist is what makes the manual completion
    a safe fallback: the caller can only fill take-level
    descriptive choices the take did not yet establish, and
    fixed session state (look, initial wardrobe, effective
    wardrobe, identity, plan keys, snapshot fields, resource
    metadata) is unreachable through this argument by
    construction. The error message names the offending key
    and the allowed set so the caller can correct the call.

    The function is exposed at module scope because a future
    task that builds a UI for manual completion wants the same
    validation as the preparation layer.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PreparationArgumentError(
            f"manual_completion must be a dict, got {type(value).__name__}"
        )
    out: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key:
            raise PreparationArgumentError(
                f"manual_completion key must be a non-empty string, "
                f"got {raw_key!r}"
            )
        if raw_key not in TAKE_DESCRIPTIVE_CHOICES:
            raise PreparationArgumentError(
                f"manual_completion key {raw_key!r} is not an "
                f"allowed take descriptive choice; allowed keys: "
                f"{sorted(TAKE_DESCRIPTIVE_CHOICES)}"
            )
        if not isinstance(raw_value, str) or not raw_value:
            raise PreparationArgumentError(
                f"manual_completion[{raw_key!r}] must be a non-empty "
                f"string, got {type(raw_value).__name__}"
            )
        if raw_key in take_choices:
            raise PreparationArgumentError(
                f"manual_completion cannot override an existing "
                f"take choice for {raw_key!r}; the take already "
                f"establishes this value"
            )
        out[raw_key] = raw_value
    return out


# -- Resource loading ------------------------------------------------------


def _load_resource_revision(
    library_key: str,
    source_id: str,
    content_digest: str,
) -> dict:
    """Return the immutable revision the plan pinned by its triple.

    The plan validates the triple at save time; the preparation
    layer looks it up by the same triple. A triple that the
    store cannot resolve is the boundary case the layer turns
    into a ``PreparationRevisionMissing`` exception. A
    successful lookup returns the decoded ``asset_revision``
    row, with the ``payload`` already deserialized to a Python
    value the rest of the layer can read.

    The function deliberately does NOT fall back to
    "the latest revision for the source entry" — the plan
    pinned the exact triple, and a later refresh that adds a
    new revision must NOT silently change which payload the
    preparation consumes.
    """
    if not isinstance(library_key, str) or not library_key:
        raise PreparationArgumentError(
            f"library_key must be a non-empty string, got {library_key!r}"
        )
    if not isinstance(source_id, str) or not source_id:
        raise PreparationArgumentError(
            f"source_id must be a non-empty string, got {source_id!r}"
        )
    if not isinstance(content_digest, str) or not content_digest:
        raise PreparationArgumentError(
            f"content_digest must be a non-empty string, "
            f"got {content_digest!r}"
        )
    library = db.one(
        "SELECT id, kind FROM resource_library WHERE library_key = ?",
        library_key,
    )
    if library is None:
        raise PreparationRevisionMissing(
            f"plan.selected_resources references unregistered library "
            f"library_key={library_key!r} source_id={source_id!r} "
            f"content_digest={content_digest!r}"
        )
    revision = resource_store.get_revision(
        library_id=int(library["id"]),
        source_id=source_id,
        content_digest=content_digest,
    )
    if revision is None:
        raise PreparationRevisionMissing(
            f"plan.selected_resources references missing revision "
            f"library_key={library_key!r} source_id={source_id!r} "
            f"content_digest={content_digest!r}: no immutable "
            f"asset_revision row matches this triple"
        )
    return {
        "library_key": library_key,
        "source_id": source_id,
        "content_digest": content_digest,
        "library_id": int(library["id"]),
        "kind": str(library["kind"]),
        "payload": revision["payload"],
        "revision_id": int(revision["id"]),
    }


# -- Field classification per resource ------------------------------------


def _classify_resource_fields(
    kind: str,
    payload: Any,
) -> dict[str, dict[str, Any]]:
    """Split a resource payload into the role buckets the layer exposes.

    The function is a thin wrapper over
    ``resource_prompts.classify_field`` that organises the
    payload by role. The buckets the layer exposes are:

      * ``identity`` — the values the contract classifies with
        the ``identity`` role (e.g. ``id``). Kept for
        provenance; never joined into the prompt.
      * ``selection_metadata`` — values the contract classifies
        with the ``selection_metadata`` role (e.g. ``weight``,
        ``library``). Kept for provenance; never joined into
        the prompt; the weight adaptation is the explicit
        contract on top of it.
      * ``descriptive_inputs`` — the values the contract
        classifies with the ``descriptive_input`` role (e.g.
        ``label``, ``scene_theme``, ``prompt`` for fused
        scenes). These are the only values the future prompt
        assembly is allowed to read.
      * ``writer_guidance`` — values the contract classifies
        with the ``writer_guidance`` role (the
        ``*_anchor`` suffix, the ``mood_*`` prefix, and the
        static mapping entries that name the role). Kept as
        bounded reference data; a string that looks like an
        instruction is still data.
      * ``intentionally_unused`` — values the contract
        classifies with the ``intentionally_unused`` role.
        Kept for provenance; never joined into the prompt.
      * ``auxiliary`` — values the contract classifies with
        the ``auxiliary_data`` role. For an auxiliary kind the
        bucket holds the auxiliary file's own record; for a
        scene kind an ``auxiliary_data`` field is a role
        mismatch and the function refuses it.

    A field whose name the contract does not name (the
    ``unmapped`` sentinel) raises
    ``PreparationFieldError`` with a message that names the
    field, the kind and the contract's reason. A scene entry
    that carries a field with the ``auxiliary_data`` role is
    also a role mismatch and is refused the same way.
    """
    if not isinstance(payload, dict):
        raise PreparationFieldError(
            f"resource payload must be a dict, got {type(payload).__name__}"
        )
    mapping = resource_prompts.mapping_for_kind(kind)
    identity: dict[str, Any] = {}
    selection_metadata: dict[str, Any] = {}
    descriptive_inputs: dict[str, Any] = {}
    writer_guidance: dict[str, Any] = {}
    intentionally_unused: dict[str, Any] = {}
    auxiliary: dict[str, Any] = {}

    is_auxiliary_kind = resource_prompts.is_auxiliary_kind(kind)

    for name, value in payload.items():
        info = resource_prompts.classify_field(kind, str(name))
        role = info.get("role")
        if role == "unmapped":
            # A field whose name the contract does not name for
            # the given kind. The contract says such a field
            # MUST NOT enter the prompt silently; the layer
            # turns that into a refusal. The reason the
            # contract returns is part of the message so a
            # reviewer can see which rule the field violated.
            raise PreparationFieldError(
                f"resource field {name!r} is not in the preparation "
                f"mapping for kind {kind!r}: {info.get('reason', '')}"
            )
        if role == resource_prompts.ROLE_AUXILIARY_DATA and not is_auxiliary_kind:
            # A scene entry carrying an auxiliary record key
            # (e.g. ``source``, ``translation``, ``fields``)
            # is the same kind of role mismatch the contract
            # reports through ``validate_resource_entry``.
            # The function refuses it; the layer does not
            # silently reduce the scene to an auxiliary file.
            raise PreparationFieldError(
                f"resource field {name!r} on a scene entry has the "
                f"auxiliary_data role; the kind is {kind!r}, not an "
                f"auxiliary schema"
            )
        if role == resource_prompts.ROLE_IDENTITY:
            identity[str(name)] = value
        elif role == resource_prompts.ROLE_SELECTION_METADATA:
            selection_metadata[str(name)] = value
        elif role == resource_prompts.ROLE_DESCRIPTIVE_INPUT:
            descriptive_inputs[str(name)] = value
        elif role == resource_prompts.ROLE_WRITER_GUIDANCE:
            writer_guidance[str(name)] = value
        elif role == resource_prompts.ROLE_INTENTIONALLY_UNUSED:
            intentionally_unused[str(name)] = value
        elif role == resource_prompts.ROLE_AUXILIARY_DATA:
            auxiliary[str(name)] = value
        else:
            # Defensive: ``classify_field`` either returns a
            # known role or the ``unmapped`` sentinel, so this
            # branch is unreachable for a well-formed contract.
            # The refusal keeps the surface closed on purpose:
            # a future widening of the contract must widen this
            # function too, and a closed surface is what
            # surfaces the widening as a code change.
            raise PreparationFieldError(
                f"resource field {name!r} has unrecognised role "
                f"{role!r} for kind {kind!r}"
            )

    return {
        "identity": identity,
        "selection_metadata": selection_metadata,
        "descriptive_inputs": descriptive_inputs,
        "writer_guidance": writer_guidance,
        "intentionally_unused": intentionally_unused,
        "auxiliary": auxiliary,
    }


# -- Per-resource preparation entry ---------------------------------------


def _prepare_resource(revision: dict) -> dict:
    """Build the per-resource preparation entry for the plan.

    The result is a dict the function composes with the rest
    of the plan's resources. Every bucket the function
    returns is the value the layer will surface to the future
    assembly step. The entry carries:

      * the immutable revision triple and the library's kind
        (for provenance);
      * the role-bucketed payload.

    The function does NOT join the descriptive inputs into
    prose; that is the future assembly step's job. The
    function's only job is to keep the role separation
    explicit so a future reviewer can read off which
    fields the contract approved for prompt use.
    """
    kind = revision["kind"]
    payload = revision["payload"]
    classified = _classify_resource_fields(kind, payload)
    return {
        "library_key": revision["library_key"],
        "source_id": revision["source_id"],
        "content_digest": revision["content_digest"],
        "kind": kind,
        "identity": classified["identity"],
        "selection_metadata": classified["selection_metadata"],
        "descriptive_inputs": classified["descriptive_inputs"],
        "writer_guidance": classified["writer_guidance"],
        "intentionally_unused": classified["intentionally_unused"],
        "auxiliary": classified["auxiliary"],
    }


# -- Effective state -------------------------------------------------------


def _resolve_take_effective_state(plan: dict, take: dict) -> dict:
    """Return the effective state the take inherits from the plan.

    The function is a thin wrapper over
    ``session_plan.resolve_effective_wardrobes`` that turns the
    per-take value into the structure the snapshot row will
    write into ``effective_state``. The structure is small and
    stable:

      * ``look`` — the plan's look as a string (empty when the
        plan did not set one);
      * ``initial_wardrobe`` — the plan's initial wardrobe;
      * ``wardrobe`` — the per-take effective wardrobe the
        resolver computed;
      * ``scope`` — the wardrobe-change scope the take
        carries, when one applies (``this_take`` or
        ``from_here``). Empty string when the take inherits
        the initial wardrobe with no change.

    The function does NOT re-derive the wardrobe: the
    resolver is the single source of truth and the layer
    reads its result verbatim. The identity, look and
    wardrobe are authoritative against any source
    suggestion; the structure the layer produces surfaces
    that fact explicitly.
    """
    effective_wardrobes = session_plan.resolve_effective_wardrobes(plan)
    take_id = take["take_id"]
    if take_id not in effective_wardrobes:
        # ``resolve_effective_wardrobes`` already returned a
        # value for every take in the plan; reaching this
        # branch means the plan changed between the validator
        # and the resolver, which is the boundary case the
        # layer turns into a refusal.
        raise PreparationError(
            f"plan does not contain a resolved effective wardrobe "
            f"for take_id {take_id!r}"
        )
    effective_wardrobe = effective_wardrobes[take_id]

    scope = ""
    for change in plan.get("wardrobe_changes", []):
        if change.get("take_id") == take_id:
            scope = change.get("scope", "")
            break

    return {
        "look": plan.get("look", ""),
        "initial_wardrobe": plan.get("initial_wardrobe", ""),
        "wardrobe": effective_wardrobe,
        "scope": scope,
    }


# -- The main entry point --------------------------------------------------


def prepare_take_inputs(
    session_id: int,
    plan_revision: int,
    take_id: str,
    *,
    manual_completion: Mapping[str, str] | None = None,
) -> dict:
    """Build the deterministic preparation structure for one take.

    The function reads the validated plan from
    ``session_plan``, the effective wardrobe the resolver
    computed, the EXACT immutable revisions the plan selected,
    and the take's own explicit descriptive choices. It
    refuses an unrecognised revision, a field that is not in
    the preparation mapping, a role mismatch on a scene
    kind, an invalid ``manual_completion`` value or a
    session that is not in resource-v1 mode.

    The returned dict carries the role-separated input the
    future assembly step reads, with no wall-clock
    timestamps, no random values and no global state. The
    same arguments produce the same dict, byte-for-byte, on
    every call. The structure is JSON-serialisable so a
    future snapshot row can store it under
    ``effective_state`` or ``provenance`` as a single value.

    The function does NOT write to the database, does NOT
    finalise the take, and does NOT mark a ``prepared_take``
    row as ``ready``. Task 4.1 produces the preparation;
    conflict handling, optional assistant synthesis, and
    the exact final-prompt snapshot are tasks 4.2 / 4.3 /
    4.4, and the persistence they own belongs to those tasks
    (and to the existing ``session_plan`` API they reuse).

    The output's top-level keys are:

      * ``take_id``, ``session_id``, ``plan_revision``,
        ``composition_mode`` — addressing keys the next task
        reads verbatim;
      * ``take_choices`` — the take's own descriptive choices
        the layer consumed, restricted to the four names in
        ``TAKE_DESCRIPTIVE_CHOICES``;
      * ``effective_take_choices`` — the merged view the
        assembly step reads: take choices take precedence
        over the manual completion, so an explicit take
        choice is the value the layer surfaces;
      * ``manual_completion`` — the manual fallback values
        that filled take choices the take did not yet
        establish, in the closed allowlist;
      * ``effective_state`` — the look, initial wardrobe,
        per-take effective wardrobe and wardrobe scope the
        resolver computed;
      * ``resource_inputs`` — per-resource role-bucketed
        payloads, with the immutable revision triple;
      * ``writer_guidance`` — the aggregated bounded
        reference data the future synthesis step (4.3) can
        read;
      * ``provenance`` — the preparation / mapping /
        compiler version triple, the module identity, the
        plan revision and the session id.
    """
    if not isinstance(session_id, int) or isinstance(session_id, bool):
        raise PreparationArgumentError(
            f"session_id must be an int, got {type(session_id).__name__}"
        )
    if not isinstance(plan_revision, int) or isinstance(plan_revision, bool):
        raise PreparationArgumentError(
            f"plan_revision must be an int, got "
            f"{type(plan_revision).__name__}"
        )
    if not isinstance(take_id, str) or not take_id:
        raise PreparationArgumentError(
            f"take_id must be a non-empty string, got {take_id!r}"
        )

    # The plan is read through the same path the routes use.
    # The function delegates the resource-mode / plan-revision
    # / take_id existence checks to ``session_plan`` so the
    # layer does not invent a second source of truth for any
    # of those facts.
    plan_revision_actual, plan = session_plan._load_current_resource_plan(  # noqa: SLF001
        session_id,
    )
    if plan_revision_actual != plan_revision:
        raise PreparationError(
            f"session {session_id} plan revision is "
            f"{plan_revision_actual}, requested preparation revision is "
            f"{plan_revision}"
        )
    take = None
    for candidate in plan.get("takes", []):
        if isinstance(candidate, dict) and candidate.get("take_id") == take_id:
            take = candidate
            break
    if take is None:
        raise PreparationError(
            f"take_id {take_id!r} is not present in plan revision "
            f"{plan_revision}"
        )

    # The take's own descriptive choices are read first; the
    # manual completion is validated against the choices the
    # take already established so a fixed state is never
    # overridden silently.
    take_choices = _take_choices_from_take(take)
    manual = _validate_manual_completion(manual_completion, take_choices)

    # The plan's selected resources are loaded by their exact
    # immutable triple. The order is the plan's order, which
    # the planner already normalised: the resolver and the
    # conflict detector both rely on that order, and the
    # assembly step reads resources in the order the
    # preparation returns.
    resource_entries: list[dict] = []
    for sel in plan.get("selected_resources", []):
        revision = _load_resource_revision(
            library_key=str(sel["library_key"]),
            source_id=str(sel["source_id"]),
            content_digest=str(sel["content_digest"]),
        )
        resource_entries.append(_prepare_resource(revision))

    effective_state = _resolve_take_effective_state(plan, take)

    # The merged view the assembly step reads: take choices
    # take precedence, the manual completion fills the
    # choices the take did not yet establish. The keys are
    # returned in canonical alphabetical order.
    effective_take_choices: dict[str, str] = {}
    for name in sorted(TAKE_DESCRIPTIVE_CHOICES):
        if name in take_choices:
            effective_take_choices[name] = take_choices[name]
        elif name in manual:
            effective_take_choices[name] = manual[name]

    provenance = {
        "preparation_version": PREPARATION_VERSION,
        "mapping_version": MAPPING_VERSION,
        "compiler_version": COMPILER_VERSION,
        "module": "backend.resource_preparation",
        "plan_revision": plan_revision,
        "session_id": session_id,
    }

    return {
        "take_id": take_id,
        "session_id": session_id,
        "plan_revision": plan_revision,
        "composition_mode": session_plan.MODE_RESOURCE_V1,
        "take_choices": take_choices,
        "effective_take_choices": effective_take_choices,
        "manual_completion": {"descriptive_inputs": dict(sorted(manual.items()))},
        "effective_state": effective_state,
        "resource_inputs": resource_entries,
        "writer_guidance": _collect_writer_guidance(resource_entries),
        "provenance": provenance,
    }


def _collect_writer_guidance(resource_entries: list[dict]) -> dict[str, Any]:
    """Aggregate the writer guidance every resource carries.

    The aggregated bucket is a single dict keyed by
    ``(library_key, source_id, field_name)`` so the future
    assembly step can read a specific piece of guidance by
    triple. The values are preserved verbatim; the function
    never interprets the prose. A string that LOOKS like an
    instruction is still data and lives under this key.
    """
    collected: dict[str, Any] = {}
    for entry in resource_entries:
        triple = (
            entry["library_key"], entry["source_id"], entry["content_digest"],
        )
        for field_name, value in entry.get("writer_guidance", {}).items():
            key = f"{triple[0]}|{triple[1]}|{triple[2]}|{field_name}"
            collected[key] = {
                "library_key": triple[0],
                "source_id": triple[1],
                "content_digest": triple[2],
                "field_name": field_name,
                "value": value,
            }
    return collected


# -- Intermediate descriptive clause assembly -----------------------------


def assemble_descriptive_clauses(preparation: dict) -> str:
    """Assemble the deterministic descriptive clause set for inspection.

    This is an INTERMEDIATE preparation output, NOT a
    ``final_prompt``. The function does NOT possess the
    semantics of a final prompt: it does not prepend a look,
    a wardrobe, a trigger or a base prompt, and it does not
    claim rendering parity with any compiled behaviour the
    source might have. The function is a pure deterministic
    assembler the next task (4.3) and the operator can read
    for inspection; the exact final-prompt composition
    policy is task 4.4's responsibility, and persistence is
    the responsibility of tasks 4.2 / 4.3 / 4.4 (which use
    the existing ``session_plan`` pipeline).

    The clauses joined are:

      * the effective take choices (camera, framing, pose,
        expression) in canonical order;
      * the resource descriptive inputs the preparation
        contract classified as ``descriptive_input``, in the
        plan's resource order, alphabetical field names;
      * the manual completion values (only the closed
        allowlist of take descriptive choices), in
        alphabetical order.

    Each clause is separated by a single full stop and a
    space. Empty strings and ``None`` are skipped. A fused
    scene's ``prompt`` field is preserved verbatim and
    joined without any decomposition: no split, no
    heuristic rephrase, no parity claim.

    Two calls with the same preparation return the same
    string. The function is pure: it does not consult
    network, GPU, ComfyUI or an assistant endpoint.
    """
    if not isinstance(preparation, dict):
        raise PreparationArgumentError(
            f"preparation must be a dict, got "
            f"{type(preparation).__name__}"
        )
    clauses: list[str] = []

    # Effective take choices come first: they are the take's
    # own explicit decisions, in the canonical order the
    # OpenSpec names. The effective_* view is what the
    # assembly step reads, and the take takes precedence
    # over the manual completion by construction (the
    # validation refuses the override case).
    for name in sorted(TAKE_DESCRIPTIVE_CHOICES):
        value = preparation.get("effective_take_choices", {}).get(name)
        if isinstance(value, str) and value:
            clauses.append(value)

    for entry in preparation.get("resource_inputs", []):
        descriptive = entry.get("descriptive_inputs", {})
        for field_name in sorted(descriptive.keys()):
            value = descriptive[field_name]
            for text in _stringify_value(value, field_name, entry):
                if text:
                    clauses.append(text)

    manual = preparation.get("manual_completion", {}).get(
        "descriptive_inputs", {},
    )
    for field_name in sorted(manual.keys()):
        # The manual completion values that already landed
        # in ``effective_take_choices`` are NOT re-emitted
        # here: a value the take established (or that the
        # manual completion filled) was already joined
        # above. A value the manual completion supplied for
        # a key the take did NOT establish is the same
        # value, so re-emitting it would duplicate. The
        # manual completion bucket is therefore silent for
        # the assembly step: the descriptive input is read
        # from ``effective_take_choices`` once. This keeps
        # the deterministic clause set free of duplicates
        # and makes the assembly step's contract identical
        # to a single read of the take + manual completion
        # merge.
        continue

    return ". ".join(clauses)


def _stringify_value(
    value: Any,
    field_name: str,
    entry: dict,
) -> list[str]:
    """Turn a descriptive value into the clause set the assembler joins.

    A string is a single clause. A list of strings is one
    clause per element. A list of non-strings, a dict, or any
    other shape is passed through the contract's role for
    ``field_name``: a field the contract classifies as a
    ``descriptive_input`` should already be a string or a
    list of strings in the source payload, so any other shape
    is reported as a preparation refusal with a message
    naming the field and the unexpected type.

    The function is exposed at module scope so a future
    helper (a UI preview, a test) can read the same
    behaviour.
    """
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                if item:
                    out.append(item)
                continue
            raise PreparationFieldError(
                f"resource field {field_name!r} on "
                f"{entry['library_key']!r}/{entry['source_id']!r} "
                f"carries a non-string item in its list value "
                f"({type(item).__name__}); descriptive_input is "
                f"a string or a list of strings"
            )
        return out
    if value is None:
        return []
    raise PreparationFieldError(
        f"resource field {field_name!r} on "
        f"{entry['library_key']!r}/{entry['source_id']!r} "
        f"carries a non-string value ({type(value).__name__}); "
        f"descriptive_input is a string or a list of strings"
    )


# -- Snapshot read ---------------------------------------------------------


def preparation_to_json(preparation: dict) -> str:
    """Encode a preparation structure as a deterministic JSON string.

    The encoding is JSON without ASCII escapes, with compact
    separators, and with the keys sorted. The byte-for-byte
    output of two calls with the same preparation is
    identical, which is what the test for "same inputs
    produce same result" pins. A future snapshot row can
    store the result under ``provenance`` without a separate
    canonicalisation step.
    """
    return json.dumps(
        preparation, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    )
