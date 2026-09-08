"""Deterministic preparation of resource-v1 take inputs (tasks 4.1 and
4.2 of ``adopt-resource-session-planning``).

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

Task 4.2 layers the conflict / adaptation / placeholder handling
on top of task 4.1 without changing the deterministic shape of
the preparation. The new rules are:

  * A ``fused_scenes`` resource whose ``prompt`` prose mentions
    clothing tokens that are NOT in the take's effective wardrobe
    produces a visible structural conflict marker. The marker
    names the resource revision triple, the field, the source
    value verbatim, the effective wardrobe the take is supposed
    to wear, and a message that asks for human review. The
    detector is deliberately structural: a keyword/token match
    on a closed clothing-vocabulary set, no semantic reasoning.
    The detection rule NEVER claims to find every possible
    contradiction in free-form prose; the marker says so.

  * The original ``asset_revision.payload`` is NEVER rewritten,
    and the resource's original prose is NEVER silently
    substituted in the preparation output. When a conflict is
    detected, the preparation surfaces the original value AND
    the conflict marker side by side; the effective clause set
    is still built from the original until a reviewed
    adaptation is provided.

  * A user-provided ``adaptation`` is a reviewed, explicit
    override of a single resource field. It is stored
    SEPARATELY from the resource payload, is keyed by the
    exact immutable revision triple the resource carries, and
    is never allowed to rewrite look, identity, wardrobe, or
    any other fixed session state. An adaptation that points
    at a different revision triple than the resource the
    plan selected, that pretends to rewrite a forbidden
    field, or that still contains an unresolved template
    placeholder is refused with a readable message; the
    refusal names the offending key.

  * Unresolved template placeholders anywhere in the fused
    description OR the adaptation block ``finalization`` of
    the take. The check uses the same ``{name}`` syntax
    ``backend.importer.unresolved_placeholders`` already
    publishes, so the repository owns a single placeholder
    vocabulary. The check is field-specific and never
    rewrites the source.

  * A new ``review_state`` block on the preparation output
    lists the conflicts, the adaptations and the unresolved
    placeholders the take currently carries. The state is
    JSON-serialisable, has no wall-clock timestamps, and is
    the single source of truth a UI or a later task reads to
    decide whether the take is ready to leave the
    deterministic preparation layer. The layer does NOT
    mark a take ``ready`` and does NOT call
    ``session_plan.complete_preparation``: that is task 4.4
    and owns its own persistence.

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
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

import db
import importer
import resource_prompts
import resource_store
import session_plan
# Re-export the persistence-error class the existing
# ``session_plan`` API publishes, so a caller that reads
# ``resource_preparation.PrearedTakePersistenceError``
# sees the same type the save path raises. The class is
# the single source of truth for "a write to a take
# persistence table failed and was rolled back".
from session_plan import PreparedTakePersistenceError  # noqa: E402, F401


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


class AdaptationError(PreparationError):
    """A user-provided adaptation is not acceptable for the take.

    The refusal names the offending key (``library_key``,
    ``source_id``, ``content_digest``, ``resource_field``,
    ``adapted_value``, or any extra key outside the closed
    allowlist), the value that failed validation, and the
    rule the value violated. A refused adaptation does not
    reach the preparation output and does not reach the
    persistence layer (which task 4.4 owns).
    """


class PlaceholderUnresolvedError(PreparationError):
    """A template placeholder is still standing in the take's preparation.

    The check uses the same ``{name}`` syntax
    ``backend.importer.unresolved_placeholders`` publishes, so
    the repository owns a single placeholder vocabulary. The
    exception names the resource/field the standing
    placeholder is in, the placeholder name, and the field
    that carries it. The source value is NEVER modified by
    the check; the refusal is the surface that blocks
    finalization.
    """


# -- Task 4.2: review state vocabulary ------------------------------------


# The closed allowlist of names an adaptation dict may carry.
# Every other key is refused. The list is the single source of
# truth for what an adaptation IS; a future widening is a
# code change the tests will surface.
ADAPTATION_KEYS: frozenset[str] = frozenset({
    "library_key",
    "source_id",
    "content_digest",
    "resource_field",
    "adapted_value",
})

# The fields a fused scene's ``prompt`` (or any other
# ``descriptive_input``) is NEVER allowed to rewrite through an
# adaptation. The list is the closed surface that protects the
# session's fixed state: an adaptation is a per-take
# descriptive override, not a redefinition of identity, look,
# or wardrobe. The list is enforced by name; a numeric or
# nested-shape value is refused the same way.
ADAPTATION_FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "look",
    "initial_wardrobe",
    "wardrobe",
    "identity",
    "id",
    "library",
})

# The closed clothing-token vocabulary the structural conflict
# detector matches against fused_scenes prompts. The set is
# intentionally narrow and contains only garment names whose
# mention in a fused description is plausible AND whose absence
# from the effective wardrobe is a visible conflict the user
# can read. A token that is not in this set is invisible to the
# detector; the detector is structural, not semantic, and the
# marker says so. The set is the single source of truth a
# test, a UI message or a code review can read.
CLOTHING_TOKENS: frozenset[str] = frozenset({
    "shirt",
    "blouse",
    "t-shirt",
    "tshirt",
    "tee",
    "sweater",
    "pullover",
    "hoodie",
    "cardigan",
    "jacket",
    "coat",
    "blazer",
    "vest",
    "waistcoat",
    "trousers",
    "pants",
    "jeans",
    "denim",
    "shorts",
    "skirt",
    "dress",
    "gown",
    "shoes",
    "boots",
    "sneakers",
    "sandals",
    "heels",
    "socks",
    "stockings",
    "tights",
    "hat",
    "cap",
    "beanie",
    "scarf",
    "gloves",
    "belt",
    "tie",
    "bow",
})

# A precompiled word-boundary regex that matches any of the
# closed clothing tokens. The detector builds the pattern once
# at module load time and reuses it; the pattern is the
# structural match rule the rest of the module reads.
CLOTHING_TOKEN_PATTERN: re.Pattern = re.compile(
    r"\b(" + "|".join(sorted(CLOTHING_TOKENS)) + r")\b",
    flags=re.IGNORECASE,
)

# The single string the conflict marker carries in its ``kind``
# field. The value is the contract every UI message or review
# screen reads; a future task that wants to add a new conflict
# kind is a code change.
CONFLICT_KIND_FUSED_VS_EFFECTIVE_WARDROBE: str = (
    "fused_scene_prompt_vs_effective_wardrobe"
)


# -- Placeholder vocabulary -----------------------------------------------


# The placeholder syntax is owned by ``backend.importer`` and
# is exposed here by reference. The preparation layer never
# invents a parallel vocabulary; the contract a test asserts is
# the same contract the import path enforces on every accepted
# source row. Re-importing the symbol makes the dependency
# explicit and surfaces a future vocabulary change as a code
# change.
PLACEHOLDER_PATTERN: re.Pattern = importer.PLACEHOLDER_PATTERN


def _placeholder_names_in(value: Any) -> list[str]:
    """Return the placeholder names still standing in ``value``.

    The function delegates to ``importer.unresolved_placeholders``
    so the module owns a single placeholder vocabulary. A
    string is checked as-is; a mapping, a list or any other
    JSON-compatible value is serialised with sorted keys
    (the same convention ``importer`` uses) so a placeholder
    hidden in a nested field is also caught. The function
    returns the names in the order ``importer`` reports them,
    which is the first-occurrence order on the serialised
    form.
    """
    return list(importer.unresolved_placeholders(value))


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

    preparation = {
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
    # Task 4.2: the review state is computed from the
    # preparation the same way a UI or a future task 4.4
    # would compute it. The initial state has no
    # adaptations; conflicts and unresolved placeholders
    # are visible immediately so a reviewer can act on
    # them before persistence. The state is JSON-serialisable
    # and carries no wall-clock timestamps.
    preparation["review_state"] = build_review_state(preparation, adaptations=None)
    return preparation


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


# -- Task 4.2: conflict detection ----------------------------------------


def _clothing_tokens_in(text: str) -> list[str]:
    """Return the closed-vocabulary clothing tokens ``text`` mentions.

    The match is a structural word-boundary search against
    ``CLOTHING_TOKEN_PATTERN``; tokens are returned in the
    order they first appear in ``text`` and deduplicated by
    the canonical lower-cased name the vocabulary uses. A
    token not in the vocabulary is invisible to the detector
    (this is the structural limit the marker says is
    deliberate). The function is the single implementation
    the conflict detector and the adaptation validator use
    to read which clothing items a piece of prose mentions.
    """
    if not isinstance(text, str) or not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in CLOTHING_TOKEN_PATTERN.finditer(text):
        token = match.group(0).lower()
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _detect_fused_wardrobe_conflict(
    resource_entry: dict,
    effective_wardrobe: str,
) -> dict | None:
    """Return a conflict marker when a fused scene's prose contradicts the take's wardrobe.

    The detector reads the resource's ``descriptive_inputs``
    and looks at every value the contract classified as
    ``descriptive_input`` for the ``fused_scenes`` kind. The
    detector is structural: it tokenises the prose against
    ``CLOTHING_TOKEN_PATTERN`` and reports a conflict ONLY
    when the prose mentions a clothing token the effective
    wardrobe does not mention. A scene that mentions the
    same tokens as the wardrobe is compatible; a scene that
    adds a token is a visible conflict the user must
    resolve.

    The detector returns a single marker per fused resource
    per take. A resource whose prose mentions two
    incompatible tokens still produces one marker; the
    marker lists the union of the conflicting tokens so a
    reviewer can read the full picture in one place. The
    marker carries the immutable revision triple the
    resource was loaded by, the field name, the original
    source value verbatim, the effective wardrobe the take
    is supposed to wear, the conflicting token list, and a
    human-readable message that names the rule and the
    review it requires.

    A resource whose descriptive inputs do not mention any
    clothing token, or whose every mentioned token is also
    in the effective wardrobe, returns ``None``: there is
    no conflict to surface and the review state stays free
    of noise.
    """
    if not isinstance(resource_entry, dict):
        return None
    if resource_entry.get("kind") != resource_prompts.KIND_FUSED_SCENES:
        # The detector only speaks the fused_scenes contract.
        # Rooms and other scene kinds are not in scope: their
        # ``label``/``scene_theme`` prose is meant to feed the
        # prompt directly, and the clothing-vs-wardrobe
        # contradiction the spec calls out is a fused-scene
        # property.
        return None
    effective_tokens = set(_clothing_tokens_in(effective_wardrobe or ""))
    if not effective_tokens:
        # An empty effective wardrobe is the "no constant
        # yet" state: the detector has nothing to compare
        # against, and the session_plan detector has the
        # same quiet behaviour for that case. The structural
        # conflict that the spec calls out requires a
        # fixed wardrobe to compete with.
        return None
    descriptive = resource_entry.get("descriptive_inputs", {})
    if not isinstance(descriptive, dict):
        return None
    conflicting: list[str] = []
    original_value = ""
    field_name = ""
    for name in sorted(descriptive.keys()):
        value = descriptive[name]
        if not isinstance(value, str) or not value:
            continue
        tokens = _clothing_tokens_in(value)
        if not tokens:
            continue
        extra = [
            token for token in tokens if token not in effective_tokens
        ]
        if not extra:
            continue
        # A resource may carry several descriptive fields;
        # the marker reports the first one the detector
        # found, so a single resource produces a single
        # readable conflict. The original_value carries the
        # complete prose the user can read; the conflicting
        # tokens are reported in canonical order so two
        # runs produce the same list.
        if not original_value:
            original_value = value
            field_name = name
        for token in extra:
            if token not in conflicting:
                conflicting.append(token)
    if not conflicting:
        return None
    conflicting.sort()
    return {
        "kind": CONFLICT_KIND_FUSED_VS_EFFECTIVE_WARDROBE,
        "library_key": resource_entry.get("library_key", ""),
        "source_id": resource_entry.get("source_id", ""),
        "content_digest": resource_entry.get("content_digest", ""),
        "resource_field": field_name,
        "resource_value": original_value,
        "effective_wardrobe": effective_wardrobe,
        "conflicting_tokens": conflicting,
        "message": (
            f"selected fused scene "
            f"{resource_entry.get('library_key', '')}/"
            f"{resource_entry.get('source_id', '')} mentions "
            f"clothing tokens {conflicting!r} that are NOT in the "
            f"take's effective wardrobe; the source value is "
            f"preserved verbatim, the effective wardrobe wins, "
            f"a reviewed adaptation OR a different resource is "
            f"required before finalization; the detector is "
            f"structural and does NOT claim to find every "
            f"possible contradiction in free-form prose"
        ),
    }


def detect_take_conflicts(preparation: dict) -> list[dict]:
    """Return every visible conflict the take's preparation currently carries.

    The function is the single entry point a UI review
    screen or a future task 4.4 reads to surface the take's
    conflict state. It walks the resource inputs the
    preparation returns and runs the structural detector on
    every ``fused_scenes`` entry. The list is empty for a
    take that has no fused scene, for a take whose fused
    scene is compatible, or for a take whose effective
    wardrobe is empty (the "no constant yet" state).

    The function is pure: it does not consult a wall clock,
    does not read the database, and does not mutate the
    preparation. Two calls with the same preparation return
    the same list, byte-for-byte, in the same order.
    """
    if not isinstance(preparation, dict):
        raise PreparationArgumentError(
            f"preparation must be a dict, got "
            f"{type(preparation).__name__}"
        )
    effective_state = preparation.get("effective_state") or {}
    effective_wardrobe = (
        effective_state.get("wardrobe", "") if isinstance(effective_state, dict) else ""
    )
    conflicts: list[dict] = []
    for entry in preparation.get("resource_inputs", []):
        marker = _detect_fused_wardrobe_conflict(entry, effective_wardrobe)
        if marker is not None:
            conflicts.append(marker)
    return conflicts


# -- Task 4.2: adaptation validation -------------------------------------


def validate_adaptation(
    adaptation: Any,
    preparation: dict,
) -> dict:
    """Validate and normalize a user-provided adaptation for one resource field.

    An adaptation is a flat dict whose keys MUST be exactly
    the names in ``ADAPTATION_KEYS``. Every value is checked:

      * the three identity fields (``library_key``,
        ``source_id``, ``content_digest``) are non-empty
        strings that match a resource the preparation
        actually loaded. An adaptation that points at a
        different revision triple — including a re-imported
        revision of the same source entry — is refused
        because the adaptation must be tied to the exact
        revision the plan selected;

      * ``resource_field`` is a non-empty string that names a
        field the resource's payload actually carries and
        that the contract classifies as ``descriptive_input``
        for the resource's kind. An adaptation that tries to
        rewrite a forbidden field (``look``,
        ``initial_wardrobe``, ``wardrobe``, ``identity``,
        ``id``, ``library``) is refused by name. An
        adaptation that targets a ``writer_guidance``,
        ``selection_metadata`` or ``intentionally_unused``
        field is refused because the layer never copies
        those roles into the prompt and an adaptation that
        pretended to do so would silently change the rule;

      * ``adapted_value`` is a non-empty string. Any other
        shape (None, list, dict, number) is refused. The
        string MUST NOT contain an unresolved template
        placeholder; the check uses the same
        ``{name}`` syntax ``backend.importer`` already
        publishes, so the repository owns a single
        vocabulary. A placeholder found in
        ``adapted_value`` is the surface that blocks
        finalization, and the error names the field and
        the placeholder name;

      * any extra key outside ``ADAPTATION_KEYS`` is refused
        by name. The closed allowlist is what keeps the
        adaptation's contract surface auditable; a future
        widening is a code change the tests will surface.

    The function is pure: it returns a fresh dict in the
    canonical key order, with the same byte-for-byte values
    the caller supplied, when the input is valid. It does
    not write to the database, does not consult a wall
    clock, and does not change the preparation. The
    persistence of a validated adaptation is the
    responsibility of task 4.4 and the existing
    ``session_plan`` pipeline; the layer hands back the
    normalized dict a caller can store.
    """
    if not isinstance(preparation, dict):
        raise PreparationArgumentError(
            f"preparation must be a dict, got "
            f"{type(preparation).__name__}"
        )
    if not isinstance(adaptation, dict):
        raise AdaptationError(
            f"adaptation must be a dict, got {type(adaptation).__name__}"
        )
    extra_keys = sorted(
        key for key in adaptation.keys() if key not in ADAPTATION_KEYS
    )
    if extra_keys:
        raise AdaptationError(
            f"adaptation carries keys outside the closed allowlist: "
            f"{extra_keys!r}; allowed: {sorted(ADAPTATION_KEYS)}"
        )
    for required_key in (
        "library_key", "source_id", "content_digest",
        "resource_field", "adapted_value",
    ):
        if required_key not in adaptation:
            raise AdaptationError(
                f"adaptation is missing required key {required_key!r}; "
                f"required: {sorted(ADAPTATION_KEYS)}"
            )
        value = adaptation[required_key]
        if required_key == "adapted_value":
            if not isinstance(value, str) or not value:
                raise AdaptationError(
                    f"adaptation['adapted_value'] must be a non-empty "
                    f"string, got {type(value).__name__}"
                )
        else:
            if not isinstance(value, str) or not value:
                raise AdaptationError(
                    f"adaptation[{required_key!r}] must be a non-empty "
                    f"string, got {type(value).__name__}"
                )

    resource_field = adaptation["resource_field"]
    if resource_field in ADAPTATION_FORBIDDEN_FIELDS:
        raise AdaptationError(
            f"adaptation cannot rewrite the fixed field "
            f"{resource_field!r}; the session's look, identity "
            f"and wardrobe are authoritative and are not reachable "
            f"through an adaptation"
        )

    triple = {
        "library_key": adaptation["library_key"],
        "source_id": adaptation["source_id"],
        "content_digest": adaptation["content_digest"],
    }
    matched = None
    for entry in preparation.get("resource_inputs", []):
        if (
            entry.get("library_key") == triple["library_key"]
            and entry.get("source_id") == triple["source_id"]
            and entry.get("content_digest") == triple["content_digest"]
        ):
            matched = entry
            break
    if matched is None:
        raise AdaptationError(
            f"adaptation references revision triple "
            f"{triple['library_key']!r}/{triple['source_id']!r}/"
            f"{triple['content_digest']!r} but the take's "
            f"preparation did not load a resource with that "
            f"triple; the plan's selected revisions are the only "
            f"valid anchor for an adaptation"
        )

    payload = matched.get("descriptive_inputs", {})
    if not isinstance(payload, dict) or resource_field not in payload:
        raise AdaptationError(
            f"adaptation targets field {resource_field!r} which is "
            f"not a descriptive_input of "
            f"{triple['library_key']!r}/{triple['source_id']!r}; "
            f"an adaptation can only override a field the contract "
            f"classifies as descriptive_input for the resource's "
            f"kind"
        )
    source_value = payload[resource_field]
    if not isinstance(source_value, str):
        raise AdaptationError(
            f"adaptation targets field {resource_field!r} whose "
            f"source value is not a string; the layer only "
            f"accepts adaptations of string descriptive inputs"
        )

    adapted_value = adaptation["adapted_value"]
    standing = _placeholder_names_in(adapted_value)
    if standing:
        raise PlaceholderUnresolvedError(
            f"adaptation of {triple['library_key']!r}/"
            f"{triple['source_id']!r} field {resource_field!r} "
            f"still carries unresolved placeholders {standing!r}; "
            f"a placeholder must be filled before the adaptation "
            f"is finalizable"
        )

    return {
        "library_key": triple["library_key"],
        "source_id": triple["source_id"],
        "content_digest": triple["content_digest"],
        "resource_field": resource_field,
        "adapted_value": adapted_value,
        "source_value": source_value,
    }


# -- Task 4.2: placeholder check -----------------------------------------


def _find_unresolved_placeholders_in_source(
    preparation: dict,
) -> list[dict]:
    """Return the standing placeholders the resource descriptive inputs carry.

    The check walks the resource inputs the preparation
    returns and applies the same ``{name}`` syntax
    ``backend.importer`` already publishes. A standing
    placeholder anywhere in a resource's descriptive
    input is a preparation refusal, the surface that blocks
    finalization. The check NEVER rewrites the source
    value: a placeholder that stands in the source stays in
    the source, and the caller is the one that decides
    what to do about it (fill, adapt, or refuse).

    The returned list is the structured answer a UI
    review screen or a future task 4.4 reads. Each entry
    carries the immutable revision triple the placeholder
    is in, the field name, and the placeholder name. The
    list is sorted by ``(library_key, source_id,
    content_digest, resource_field, placeholder)`` so two
    runs produce the same order. ``in_adaptation`` is
    always ``False`` on the source-side findings; the
    adaptation-side findings
    ``_find_unresolved_placeholders_in_adaptations``
    returns carry ``in_adaptation = True``.
    """
    if not isinstance(preparation, dict):
        raise PreparationArgumentError(
            f"preparation must be a dict, got "
            f"{type(preparation).__name__}"
        )
    findings: list[dict] = []
    for entry in preparation.get("resource_inputs", []):
        if not isinstance(entry, dict):
            continue
        triple = (
            str(entry.get("library_key", "")),
            str(entry.get("source_id", "")),
            str(entry.get("content_digest", "")),
        )
        for field_name, value in (entry.get("descriptive_inputs") or {}).items():
            if not isinstance(value, str) or not value:
                continue
            standing = _placeholder_names_in(value)
            for name in standing:
                findings.append({
                    "library_key": triple[0],
                    "source_id": triple[1],
                    "content_digest": triple[2],
                    "resource_field": str(field_name),
                    "placeholder": name,
                    "in_adaptation": False,
                })
    findings.sort(key=lambda item: (
        item["library_key"], item["source_id"], item["content_digest"],
        item["resource_field"], item["placeholder"],
    ))
    return findings


def _find_unresolved_placeholders_in_adaptations(
    applicable_adaptations: list[dict],
) -> list[dict]:
    """Return the standing placeholders the applicable adaptations carry.

    The check walks the adapted values of the
    applicable adaptations the caller already filtered
    (the function never re-queries the database, never
    re-validates, and never inspects a row whose triple
    the current preparation does not select). Each
    finding carries the immutable revision triple, the
    field name, the placeholder name, and the
    ``in_adaptation = True`` marker that names the
    provenance. The list is sorted by the same
    five-tuple the source-side helper uses, so a
    combined call that simply concatenates the two
    results stays in canonical order.
    """
    findings: list[dict] = []
    for item in applicable_adaptations:
        if not isinstance(item, dict):
            continue
        value = item.get("adapted_value", "")
        if not isinstance(value, str) or not value:
            continue
        for name in _placeholder_names_in(value):
            findings.append({
                "library_key": str(item.get("library_key", "")),
                "source_id": str(item.get("source_id", "")),
                "content_digest": str(item.get("content_digest", "")),
                "resource_field": str(item.get("resource_field", "")),
                "placeholder": name,
                "in_adaptation": True,
            })
    findings.sort(key=lambda item: (
        item["library_key"], item["source_id"], item["content_digest"],
        item["resource_field"], item["placeholder"],
    ))
    return findings


def _resolve_applicable_adaptations(
    preparation: dict,
    adaptations: list[dict] | None,
) -> list[dict]:
    """Return the applicable adaptations the take will apply.

    When ``adaptations`` is a list, the function returns
    the validated list verbatim, in the caller's order.
    When ``adaptations`` is ``None``, the function reads
    the durable adaptations through
    ``load_take_adaptations`` and applies the same
    applicable filter ``build_review_state`` and the
    rest of the layer use, so a stored adaptation whose
    triple the current plan no longer selects is never
    surfaced as an applicable adaptation.

    The function is the single source of truth for the
    applicable adaptation list. ``build_review_state``,
    ``assert_no_unresolved_placeholders`` and
    ``assemble_adapted_clauses`` all use it; a future
    caller that wants a fourth view of the same take
    widens this function in one place.
    """
    if not isinstance(preparation, dict):
        return []
    if adaptations is None:
        if not all(
            key in preparation
            for key in ("session_id", "plan_revision", "take_id")
        ):
            return []
        persisted = load_take_adaptations(
            int(preparation["session_id"]),
            int(preparation["plan_revision"]),
            str(preparation["take_id"]),
        )
        return _applicable_adaptations(preparation, persisted)
    if not isinstance(adaptations, list):
        raise PreparationArgumentError(
            f"adaptations must be a list or None, got "
            f"{type(adaptations).__name__}"
        )
    validated: list[dict] = []
    for index, raw in enumerate(adaptations):
        try:
            validated.append(validate_adaptation(raw, preparation))
        except AdaptationError as exc:
            raise AdaptationError(
                f"adaptations[{index}] is not acceptable: {exc}"
            ) from exc
        except PlaceholderUnresolvedError as exc:
            raise PlaceholderUnresolvedError(
                f"adaptations[{index}] is not acceptable: {exc}"
            ) from exc
    return validated


def _collect_unresolved_placeholders(
    preparation: dict,
    *,
    adaptations: list[dict] | None = None,
) -> list[dict]:
    """Return every standing placeholder the take's review state must surface.

    The function is the single source of truth a UI
    review screen, ``assert_no_unresolved_placeholders``
    and the ``ready_for_finalization`` boolean the
    review state surfaces all read. The list is the
    UNION of two findings:

      * the source-side findings, computed from the
        resource descriptive inputs the preparation
        returns;
      * the adaptation-side findings, computed from
        the adapted values of the applicable
        adaptations the take will apply.

    A persisted adaptation that arrives by an
    adversarial route and still carries a ``{name}``
    placeholder is included in the adaptation-side
    findings with ``in_adaptation = True``, and the
    caller (the review state builder or the
    finalization boundary) sees a non-empty list and
    a ``False`` ready-for-finalization boolean. The
    source value is NEVER modified by the check; the
    function never overwrites the original.

    The two findings lists are sorted by the same
    five-tuple, so the union is in canonical order.
    The function does NOT consult a wall clock and
    does NOT mutate the preparation.
    """
    if not isinstance(preparation, dict):
        raise PreparationArgumentError(
            f"preparation must be a dict, got "
            f"{type(preparation).__name__}"
        )
    applicable = _resolve_applicable_adaptations(
        preparation, adaptations,
    )
    findings = _find_unresolved_placeholders_in_source(preparation)
    findings.extend(
        _find_unresolved_placeholders_in_adaptations(applicable)
    )
    findings.sort(key=lambda item: (
        item["library_key"], item["source_id"], item["content_digest"],
        item["resource_field"], item["placeholder"],
    ))
    return findings


def find_unresolved_placeholders_in_take(preparation: dict) -> list[dict]:
    """Return the source-side unresolved placeholders the take carries.

    The function is preserved for the tests that read
    only the resource-side placeholder surface (a
    future UI may want to display "placeholders in the
    source prose" separately from "placeholders in a
    reviewed adaptation"). The full review-state list
    is what ``build_review_state`` and
    ``assert_no_unresolved_placeholders`` read, and
    that list is the union of source-side and
    adaptation-side findings.
    """
    return _find_unresolved_placeholders_in_source(preparation)


def assert_no_unresolved_placeholders(
    preparation: dict,
    *,
    adaptations: list[dict] | None = None,
) -> None:
    """Raise ``PlaceholderUnresolvedError`` when the take carries any standing placeholder.

    The function is the single call a future task 4.4
    (or a UI review screen) makes to decide whether the
    take is finalizable. The check reads the resource
    descriptive inputs the preparation returns AND the
    persisted or supplied adaptations the take will
    apply, because an unresolved placeholder in an
    adapted value is the same block on finalization as
    a placeholder in the resource's original prose.

    The check is the same call
    ``_collect_unresolved_placeholders`` makes for the
    review state, so the review state and the
    finalization boundary cannot disagree on whether a
    placeholder stands. A persisted adaptation that
    arrived by an adversarial route and still carries
    a ``{name}`` placeholder is caught by both: the
    review state lists it under
    ``unresolved_placeholders`` and the
    finalization boundary raises this exception.

    The error names the resource, the field, the
    placeholder name, and (when the finding came from
    an adaptation) the provenance marker. The source
    value is NEVER modified by the check.
    """
    findings = _collect_unresolved_placeholders(
        preparation, adaptations=adaptations,
    )
    if not findings:
        return
    first = findings[0]
    suffix = " in an adaptation" if first.get("in_adaptation") else ""
    raise PlaceholderUnresolvedError(
        f"preparation for resource {first['library_key']!r}/"
        f"{first['source_id']!r} field {first['resource_field']!r}"
        f"{suffix} still carries unresolved placeholder "
        f"{{{first['placeholder']}}}; "
        f"a placeholder must be filled before the take is "
        f"finalizable; total standing placeholders: {len(findings)}"
    )


# -- Task 4.2: adaptation persistence ------------------------------------


# Single explicit string the trigger on
# ``take_resource_adaptation`` raises on. The value is
# documented here so a test that asserts the SQL-level
# guard can read it without re-deriving the message.
TAKE_RESOURCE_ADAPTATION_PROTECT_MESSAGE: str = (
    "take_resource_adaptation is immutable: session_id, "
    "plan_revision, take_id, library_key, source_id, "
    "content_digest, resource_field and created_at cannot "
    "be rewritten; only source_value, adapted_value and "
    "updated_at may change on re-review"
)


def _normalize_recorded_adaptation(
    session_id: int,
    plan_revision: int,
    take_id: str,
    validated: dict,
) -> dict:
    """Project a validated adaptation to the persisted-row shape.

    The persisted row carries the seven identity columns
    the ``take_resource_adaptation`` table pins through
    its UNIQUE constraint, the source value the
    adaptation was reviewed against, the adapted value
    the user approved, and the wall-clock timestamps the
    schema requires. The dict is JSON-serialisable and
    stable, and is the single source of truth a future
    task reads.
    """
    return {
        "session_id": int(session_id),
        "plan_revision": int(plan_revision),
        "take_id": str(take_id),
        "library_key": str(validated["library_key"]),
        "source_id": str(validated["source_id"]),
        "content_digest": str(validated["content_digest"]),
        "resource_field": str(validated["resource_field"]),
        "source_value": str(validated["source_value"]),
        "adapted_value": str(validated["adapted_value"]),
    }


def record_take_adaptation(
    session_id: int,
    plan_revision: int,
    take_id: str,
    adaptation: Any,
) -> dict:
    """Validate and persist a single reviewed adaptation for the take.

    The function is the single entry point a UI review
    screen or a future task 4.4 calls to make a
    reviewed adaptation durable. The validation runs
    the same rules ``validate_adaptation`` publishes
    (allowlist, no forbidden fields, no unresolved
    placeholders, triple bound to a resource the
    preparation actually loaded, non-empty string value)
    and refuses the input with the same exception
    ``validate_adaptation`` raises; the layer never
    silently stores an invalid review.

    The write runs inside the same ``db.transaction``
    block the rest of the project uses. The single
    INSERT (or UPDATE, when the exact conflict was
    reviewed before) is the only write; a failure of the
    SQL call raises ``PreparedTakePersistenceError`` and
    the transaction rolls back, so a transient
    persistence failure is never reported as "the
    adaptation was saved". After the INSERT, the function
    re-reads the row through the unique key and returns
    the persisted shape; a missing read after a successful
    INSERT is the boundary case the function turns into
    a ``PreparedTakePersistenceError``.

    The function does NOT mark a ``prepared_take`` row
    ``ready`` and does NOT generate a ``final_prompt``;
    that is task 4.4's responsibility. The
    ``session_id``/``plan_revision``/``take_id`` triple
    is what the future finalisation step joins to.

    A second review of the exact same conflict (same
    seven columns) replaces the previously-persisted
    ``updated_at`` and leaves every other column
    byte-for-byte unchanged, the same way
    ``asset_revision`` does for refreshed source content.
    The trigger the schema installs is the SQL-level
    guard that says "the seven identity columns and
    source_value/adapted_value/created_at are immutable,
    only updated_at may change"; a direct UPDATE that
    tries to rewrite one of the protected columns is
    refused at the schema level.
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
    if plan_revision <= 0:
        raise PreparationArgumentError(
            f"plan_revision must be > 0, got {plan_revision}"
        )
    if not isinstance(take_id, str) or not take_id:
        raise PreparationArgumentError(
            f"take_id must be a non-empty string, got {take_id!r}"
        )
    # The session, plan-revision and take_id must be a
    # valid target. The validation runs against the
    # plan the resource_preparation layer already
    # produced for the same triple, so an adaptation
    # cannot reach the persistence layer for a
    # session/plan/take that has not been prepared.
    _, plan = session_plan._load_current_resource_plan(  # noqa: SLF001
        session_id,
    )
    actual_revision, _ = session_plan._load_current_resource_plan(  # noqa: SLF001
        session_id,
    )
    if actual_revision != plan_revision:
        raise PreparationError(
            f"session {session_id} plan revision is "
            f"{actual_revision}, requested adaptation revision is "
            f"{plan_revision}"
        )
    if not any(
        isinstance(take, dict) and take.get("take_id") == take_id
        for take in plan.get("takes", [])
    ):
        raise PreparationError(
            f"take_id {take_id!r} is not present in plan revision "
            f"{plan_revision}"
        )

    # Build a synthetic preparation that carries the
    # minimum the validator needs (effective_state and
    # resource_inputs) without re-running the full
    # deterministic preparation. The validator only
    # looks at the resource_inputs, the effective_state
    # and the take_id; the rest is unused.
    resource_entries: list[dict] = []
    for sel in plan.get("selected_resources", []):
        try:
            revision = _load_resource_revision(
                library_key=str(sel["library_key"]),
                source_id=str(sel["source_id"]),
                content_digest=str(sel["content_digest"]),
            )
        except PreparationRevisionMissing:
            # A plan that names a missing revision is
            # refused at save time; the boundary case
            # here is reported as a persistence error so
            # the caller does not get a different
            # exception for the same problem.
            raise PreparationRevisionMissing(
                f"plan.selected_resources references missing "
                f"revision for take {take_id!r}"
            )
        resource_entries.append({
            "library_key": revision["library_key"],
            "source_id": revision["source_id"],
            "content_digest": revision["content_digest"],
            "kind": revision["kind"],
            "descriptive_inputs": (
                _classify_resource_fields(
                    revision["kind"], revision["payload"],
                )["descriptive_inputs"]
            ),
        })
    effective_take: dict | None = None
    for take in plan.get("takes", []):
        if isinstance(take, dict) and take.get("take_id") == take_id:
            effective_take = take
            break
    if effective_take is None:
        raise PreparationError(
            f"take_id {take_id!r} is not present in plan revision "
            f"{plan_revision}"
        )
    effective_state = _resolve_take_effective_state(plan, effective_take)
    synthetic_preparation = {
        "take_id": take_id,
        "session_id": session_id,
        "plan_revision": plan_revision,
        "effective_state": effective_state,
        "resource_inputs": resource_entries,
    }
    validated = validate_adaptation(adaptation, synthetic_preparation)
    record = _normalize_recorded_adaptation(
        session_id, plan_revision, take_id, validated,
    )

    now = db.now()
    try:
        with db.transaction():
            existing = db.one(
                "SELECT id, updated_at FROM take_resource_adaptation "
                "WHERE session_id = ? AND plan_revision = ? AND take_id = ? "
                "AND library_key = ? AND source_id = ? "
                "AND content_digest = ? AND resource_field = ?",
                record["session_id"], record["plan_revision"],
                record["take_id"], record["library_key"],
                record["source_id"], record["content_digest"],
                record["resource_field"],
            )
            if existing is None:
                db.run(
                    "INSERT INTO take_resource_adaptation "
                    "(session_id, plan_revision, take_id, library_key, "
                    "source_id, content_digest, resource_field, "
                    "source_value, adapted_value, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    record["session_id"], record["plan_revision"],
                    record["take_id"], record["library_key"],
                    record["source_id"], record["content_digest"],
                    record["resource_field"], record["source_value"],
                    record["adapted_value"], now, now,
                )
            else:
                # A re-review of the exact conflict updates
                # the source value, the adapted value, and
                # the timestamp. The trigger the schema
                # installs refuses a direct UPDATE of any
                # of the seven identity columns or
                # ``created_at``; the WHERE clause below
                # targets the immutable row the SELECT
                # found. ``updated_at`` is always mutable;
                # ``source_value`` and ``adapted_value`` are
                # re-review values (the new approval the
                # operator is recording), not part of the
                # identity.
                db.run(
                    "UPDATE take_resource_adaptation "
                    "SET source_value = ?, adapted_value = ?, "
                    "updated_at = ? "
                    "WHERE id = ? AND "
                    "session_id = ? AND plan_revision = ? AND take_id = ? "
                    "AND library_key = ? AND source_id = ? "
                    "AND content_digest = ? AND resource_field = ?",
                    record["source_value"], record["adapted_value"], now,
                    int(existing["id"]),
                    record["session_id"], record["plan_revision"],
                    record["take_id"], record["library_key"],
                    record["source_id"], record["content_digest"],
                    record["resource_field"],
                )
            row = db.one(
                "SELECT session_id, plan_revision, take_id, library_key, "
                "source_id, content_digest, resource_field, "
                "source_value, adapted_value, created_at, updated_at "
                "FROM take_resource_adaptation "
                "WHERE session_id = ? AND plan_revision = ? "
                "AND take_id = ? AND library_key = ? "
                "AND source_id = ? AND content_digest = ? "
                "AND resource_field = ?",
                record["session_id"], record["plan_revision"],
                record["take_id"], record["library_key"],
                record["source_id"], record["content_digest"],
                record["resource_field"],
            )
            if row is None:
                raise PreparedTakePersistenceError(
                    f"adaptation for "
                    f"{record['library_key']!r}/{record['source_id']!r}/"
                    f"{record['content_digest']!r} field "
                    f"{record['resource_field']!r} on take "
                    f"{record['take_id']!r} was not persisted"
                )
    except (
        AdaptationError,
        PlaceholderUnresolvedError,
        PreparationArgumentError,
        PreparationRevisionMissing,
        PreparationError,
    ):
        raise
    except Exception as exc:
        raise PreparedTakePersistenceError(
            f"could not persist adaptation for take "
            f"{record['take_id']!r} plan revision "
            f"{record['plan_revision']}: {exc}"
        ) from exc
    return {
        "session_id": int(row["session_id"]),
        "plan_revision": int(row["plan_revision"]),
        "take_id": str(row["take_id"]),
        "library_key": str(row["library_key"]),
        "source_id": str(row["source_id"]),
        "content_digest": str(row["content_digest"]),
        "resource_field": str(row["resource_field"]),
        "source_value": str(row["source_value"]),
        "adapted_value": str(row["adapted_value"]),
    }


def load_take_adaptations(
    session_id: int,
    plan_revision: int,
    take_id: str,
) -> list[dict]:
    """Return every persisted adaptation for the exact triple.

    The function is the single path the review state
    builder and the assembler use to read the durable
    reviewed adaptations a take carries. The query is
    indexed by the UNIQUE constraint the schema
    installs on the seven identity columns; the result
    is the full row set, sorted by the seven columns so
    two calls return the same list order.

    The function does NOT validate the persisted rows
    against the current preparation. A persisted
    adaptation whose triple the current plan no longer
    selects is simply ignored by the reviewer: the
    lookup is keyed by triple, the resource_inputs the
    preparation returns are the only triples a
    conflict can name, and a stored row whose triple is
    not in the preparation's resource_inputs is not
    applicable to the current take.
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
    rows = db.q(
        "SELECT session_id, plan_revision, take_id, library_key, "
        "source_id, content_digest, resource_field, "
        "source_value, adapted_value, created_at, updated_at "
        "FROM take_resource_adaptation "
        "WHERE session_id = ? AND plan_revision = ? AND take_id = ? "
        "ORDER BY library_key, source_id, content_digest, resource_field",
        int(session_id), int(plan_revision), str(take_id),
    )
    return [
        {
            "session_id": int(r["session_id"]),
            "plan_revision": int(r["plan_revision"]),
            "take_id": str(r["take_id"]),
            "library_key": str(r["library_key"]),
            "source_id": str(r["source_id"]),
            "content_digest": str(r["content_digest"]),
            "resource_field": str(r["resource_field"]),
            "source_value": str(r["source_value"]),
            "adapted_value": str(r["adapted_value"]),
        }
        for r in rows
    ]


def _applicable_adaptations(
    preparation: dict,
    persisted: list[dict],
) -> list[dict]:
    """Return the persisted adaptations that match the take's preparation.

    The match is the exact seven-column triple (the
    ``session_id``, ``plan_revision`` and ``take_id`` plus
    the resource triple and the field). A persisted row
    whose triple the current plan no longer selects is
    not returned. The function is the boundary between
    durable storage and the deterministic preparation
    output: a future finalisation step reads the
    applicable list, the assembler uses the same list.
    """
    if not isinstance(preparation, dict):
        return []
    prep_session = preparation.get("session_id", 0)
    prep_revision = preparation.get("plan_revision", 0)
    prep_take = preparation.get("take_id", "")
    resource_triples: set[tuple[str, str, str]] = set()
    for entry in preparation.get("resource_inputs", []):
        if not isinstance(entry, dict):
            continue
        resource_triples.add((
            str(entry.get("library_key", "")),
            str(entry.get("source_id", "")),
            str(entry.get("content_digest", "")),
        ))
    applicable: list[dict] = []
    for item in persisted:
        triple = (
            str(item.get("library_key", "")),
            str(item.get("source_id", "")),
            str(item.get("content_digest", "")),
        )
        if triple not in resource_triples:
            continue
        if (
            int(item.get("session_id", 0)) != int(prep_session)
            or int(item.get("plan_revision", 0)) != int(prep_revision)
            or str(item.get("take_id", "")) != str(prep_take)
        ):
            continue
        applicable.append(item)
    return applicable


# -- Task 4.2: review state assembly -------------------------------------


def build_review_state(
    preparation: dict,
    *,
    adaptations: list[dict] | None = None,
) -> dict:
    """Return the visible review state for the take's preparation.

    The state is the single source of truth a UI review
    screen, a future task 4.4 or a future HTTP handler
    reads to decide what the take still needs. The shape is
    stable and JSON-serialisable:

      * ``take_id`` and ``session_id`` — addressing keys
        the next layer echoes back;
      * ``effective_state`` — a copy of the preparation's
        effective state, with the authoritative
        ``look`` / ``initial_wardrobe`` / ``wardrobe`` the
        resolver computed;
      * ``selected_resource_revisions`` — the list of
        immutable revision triples the preparation loaded,
        with each resource's kind, so a reviewer can see
        exactly which revisions are bound to the take;
      * ``conflicts`` — the visible conflict markers
        ``detect_take_conflicts`` returned, in deterministic
        order. An empty list means "no conflict the
        structural detector can see";
      * ``adaptations`` — the validated adaptation list
        the function applies. When ``adaptations`` is
        ``None``, the function reads the durable
        adaptations the take has already persisted
        through ``record_take_adaptation`` and applies
        only the ones whose seven-column triple still
        matches the current preparation. When
        ``adaptations`` is a list, the function uses that
        list verbatim and never consults the database,
        so a caller that wants to preview a not-yet-
        persisted review can still build the state;
      * ``unresolved_placeholders`` — the standing
        placeholder list
        ``_collect_unresolved_placeholders`` returned,
        in canonical order. The list is the union of
        the source-side findings and the
        adaptation-side findings, so a persisted
        adaptation that arrived by an adversarial
        route and still carries a ``{name}``
        placeholder is visible here, the same way it
        is to ``assert_no_unresolved_placeholders``.
        An entry whose ``in_adaptation`` is True
        names the provenance; an entry whose
        ``in_adaptation`` is False (or missing) names
        a finding in the resource's original prose.
        An empty list means "no standing placeholder
        the detector can see";
      * ``ready_for_finalization`` — a single boolean
        that is True only when there are no conflicts AND
        no standing placeholders. The boolean is what a UI
        or a future task 4.4 reads as the gate; the
        function does NOT mark the take ``ready`` in the
        database, that is task 4.4's responsibility and
        uses ``session_plan.complete_preparation``.

    The function does NOT write to the database and does
    NOT consult a wall clock. Two calls with the same
    preparation, the same persisted adaptations, and the
    same explicit ``adaptations`` argument return the
    same dict, byte-for-byte.
    """
    if not isinstance(preparation, dict):
        raise PreparationArgumentError(
            f"preparation must be a dict, got "
            f"{type(preparation).__name__}"
        )
    validated_adaptations = _resolve_applicable_adaptations(
        preparation, adaptations,
    )
    conflicts = detect_take_conflicts(preparation)
    unresolved = _collect_unresolved_placeholders(
        preparation, adaptations=adaptations,
    )

    # A conflict is considered resolved when an adaptation
    # targets the same field of the same revision triple. The
    # match is exact: an adaptation that targets a different
    # field of the same triple does not resolve a conflict on
    # the original field, and a conflict on a different triple
    # is not resolved by an adaptation on a different triple.
    # The resolved set is the input to the boolean the
    # review state surfaces.
    resolved_triples: set[tuple[str, str, str, str]] = {
        (
            item["library_key"], item["source_id"],
            item["content_digest"], item["resource_field"],
        )
        for item in validated_adaptations
    }
    open_conflicts: list[dict] = []
    resolved_conflicts: list[dict] = []
    for marker in conflicts:
        key = (
            marker.get("library_key", ""),
            marker.get("source_id", ""),
            marker.get("content_digest", ""),
            marker.get("resource_field", ""),
        )
        if key in resolved_triples:
            resolved_conflicts.append(dict(marker))
        else:
            open_conflicts.append(dict(marker))

    selected_resource_revisions: list[dict] = []
    for entry in preparation.get("resource_inputs", []):
        if not isinstance(entry, dict):
            continue
        selected_resource_revisions.append({
            "library_key": entry.get("library_key", ""),
            "source_id": entry.get("source_id", ""),
            "content_digest": entry.get("content_digest", ""),
            "kind": entry.get("kind", ""),
        })
    selected_resource_revisions.sort(key=lambda item: (
        item["library_key"], item["source_id"], item["content_digest"],
    ))

    return {
        "take_id": preparation.get("take_id", ""),
        "session_id": preparation.get("session_id", 0),
        "plan_revision": preparation.get("plan_revision", 0),
        "composition_mode": preparation.get("composition_mode", ""),
        "effective_state": dict(preparation.get("effective_state") or {}),
        "selected_resource_revisions": selected_resource_revisions,
        "conflicts": open_conflicts,
        "resolved_conflicts": resolved_conflicts,
        "adaptations": validated_adaptations,
        "unresolved_placeholders": unresolved,
        "ready_for_finalization": (
            not open_conflicts and not unresolved
        ),
    }


# -- Task 4.2: adaptation-aware assembly ---------------------------------


def _adapted_value_for_field(
    entry: dict,
    field_name: str,
    adaptations: list[dict],
) -> str | None:
    """Return the adapted value for ``(entry, field_name)`` or None.

    The lookup matches the immutable revision triple the
    entry was loaded by AND the field name. A match returns
    the adapted value verbatim; a non-match returns ``None``
    so the caller falls back to the original value. The
    function is the only path the assembler uses to read
    the adapted value, so a future task that wants a
    different lookup rule (e.g. by resource id only) widens
    this function in one place.
    """
    triple = (
        entry.get("library_key", ""),
        entry.get("source_id", ""),
        entry.get("content_digest", ""),
    )
    for adaptation in adaptations:
        if (
            adaptation.get("library_key") == triple[0]
            and adaptation.get("source_id") == triple[1]
            and adaptation.get("content_digest") == triple[2]
            and adaptation.get("resource_field") == field_name
        ):
            value = adaptation.get("adapted_value")
            if isinstance(value, str):
                return value
            return None
    return None


def assemble_adapted_clauses(
    preparation: dict,
    *,
    adaptations: list[dict] | None = None,
) -> str:
    """Assemble the descriptive clause set the adaptations overwrite.

    The function is the INTERMEDIATE preparation output
    the take's review state reads, NOT a ``final_prompt``.
    It does NOT prepend a look, a wardrobe, a trigger or a
    base prompt, and it does NOT claim rendering parity.
    The function is a pure deterministic assembler; the
    exact final-prompt composition policy is task 4.4's
    responsibility, and persistence is the responsibility
    of tasks 4.2 / 4.3 / 4.4 (which use the existing
    ``session_plan`` pipeline).

    For every resource descriptive input the preparation
    returns, the assembler reads the adapted value when an
    adaptation exists for the exact (triple, field) pair,
    and falls back to the original value otherwise. A
    non-empty adapted value REPLACES the original in the
    effective clause set; the original is still preserved
    in the adaptation entry and in the resource entry the
    preparation returns, so a reviewer can read the full
    picture.

    The function refuses an invalid adaptation with the
    same exception the validator raises, so a review
    state that contains an invalid adaptation cannot
    reach the assembler silently. The function is pure
    and deterministic: two calls with the same
    preparation and the same adaptations return the same
    string.
    """
    if not isinstance(preparation, dict):
        raise PreparationArgumentError(
            f"preparation must be a dict, got "
            f"{type(preparation).__name__}"
        )
    validated_adaptations: list[dict] = []
    if adaptations is None:
        if all(
            key in preparation
            for key in ("session_id", "plan_revision", "take_id")
        ):
            persisted = load_take_adaptations(
                int(preparation["session_id"]),
                int(preparation["plan_revision"]),
                str(preparation["take_id"]),
            )
            validated_adaptations = _applicable_adaptations(
                preparation, persisted,
            )
    else:
        if not isinstance(adaptations, list):
            raise PreparationArgumentError(
                f"adaptations must be a list or None, got "
                f"{type(adaptations).__name__}"
            )
        for index, raw in enumerate(adaptations):
            try:
                validated = validate_adaptation(raw, preparation)
            except AdaptationError as exc:
                raise AdaptationError(
                    f"adaptations[{index}] is not acceptable: {exc}"
                ) from exc
            except PlaceholderUnresolvedError as exc:
                raise PlaceholderUnresolvedError(
                    f"adaptations[{index}] is not acceptable: {exc}"
                ) from exc
            validated_adaptations.append(validated)

    clauses: list[str] = []

    for name in sorted(TAKE_DESCRIPTIVE_CHOICES):
        value = preparation.get("effective_take_choices", {}).get(name)
        if isinstance(value, str) and value:
            clauses.append(value)

    for entry in preparation.get("resource_inputs", []):
        descriptive = entry.get("descriptive_inputs", {})
        for field_name in sorted(descriptive.keys()):
            original = descriptive[field_name]
            adapted = _adapted_value_for_field(entry, field_name, validated_adaptations)
            effective = adapted if isinstance(adapted, str) and adapted else original
            for text in _stringify_value(effective, field_name, entry):
                if text:
                    clauses.append(text)

    return ". ".join(clauses)
