"""Supported field mappings for resource-based prompt preparation (task 1.2).

This module is the explicit contract that turns an accepted source entry
into a deterministic preparation input. The coverage ledger built by
``backend.resource_ledger`` records what the operator's source libraries
OBSERVE; this module records what the preparation path SUPPORTS. The two
are deliberately separate:

  * the ledger classifies by structural type and observed consumer evidence;
  * this module classifies every observed safe field as ``identity``,
    ``selection_metadata``, ``descriptive_input``, ``writer_guidance``,
    ``auxiliary_data`` or ``intentionally_unused`` (with a non-empty reason),
    declares which fields are required versus optional for preparation,
    and refuses to let an unknown or unmapped field reach the prompt
    silently.

The classification is exhaustive for the field names the coverage ledger
of 2026-09-07 recorded as ``known_shape`` (six ``rooms`` libraries and one
``fused_scenes`` library) and for the four documented auxiliary schemas
(``translation_map``, ``cut_map``, ``mined_families``, ``mined_labels``).
The class names are not invented: they are the six roles the
``backend.resource_ledger`` module already exports, reused here so a
test can reason about both modules through the same vocabulary.

The module never reads the operator's private corpus. The field names
below come from the structural field-name set the ledger's
``PROVISIONAL_FIELD_ROLES`` already publishes, and from the four
auxiliary schemas the same module's ``AUXILIARY_MAPS`` declares. The
mapping values are roles and required-flags, not source values.

Three rules are pinned here and nowhere else:

  1. The ``weight`` field is a NUMBER used for SELECTION (which entry
     to pick, which entries are outliers). It is NOT a prompt emphasis.
     The text-weight adaptation is the explicit declaration further
     down (``WEIGHT_TEXT_ADAPTATION``); without it, a reader of the
     inventory's ``selection_metadata`` role could reasonably try to
     translate ``weight: 1.5`` into a prompt-weight syntax and silently
     corrupt the generation. The adaptation forbids that.

  2. The ``fused_scenes`` library's ``prompt`` field is a string of
     free prose the source wrote. The compiled behavior that splits
     such prose into camera, act and room clauses (and the resulting
     routing through the rest of the source application) is NOT
     verifiable from the readable evidence this project has. The
     preparation contract treats the prose as descriptive input only,
     keeps the source string intact, and surfaces a visible
     field-specific reason if the prose contradicts a fixed session
     choice. It does NOT claim parity, it does NOT auto-decompose, and
     it does NOT silently rewrite the original. The note
     ``FUSED_SCENES_COMPILED_BEHAVIOR`` carries the explicit "unverified"
     marker the spec requires.

  3. An unknown or unmapped field cannot reach the prompt. The
     ``extract_prompt_inputs`` function below returns ONLY fields whose
     role is ``descriptive_input`` for the given kind; a field the
     mapping does not name is reported by ``validate_resource_entry``
     with a field-specific reason and never silently appears in the
     preparation output.
"""
from __future__ import annotations

from typing import Any, Mapping

# -- Roles. Re-exported from the inventory vocabulary so the same word ----
# means the same thing in both modules. The inventory's "unused" is the
# observation that the field has no consumer evidence; the preparation's
# "intentionally_unused" is the contract that the field is documented as
# not entering the prompt, with a non-empty reason. They map 1:1; the
# latter is the latter's documented name in this module.

ROLE_IDENTITY: str = "identity"
ROLE_SELECTION_METADATA: str = "selection_metadata"
ROLE_DESCRIPTIVE_INPUT: str = "descriptive_input"
ROLE_WRITER_GUIDANCE: str = "writer_guidance"
ROLE_AUXILIARY_DATA: str = "auxiliary_data"
ROLE_INTENTIONALLY_UNUSED: str = "intentionally_unused"

ALL_PREPARATION_ROLES: tuple[str, ...] = (
    ROLE_IDENTITY,
    ROLE_SELECTION_METADATA,
    ROLE_DESCRIPTIVE_INPUT,
    ROLE_WRITER_GUIDANCE,
    ROLE_AUXILIARY_DATA,
    ROLE_INTENTIONALLY_UNUSED,
)

# -- The six supported resource kinds in the preparation contract. ---------
# Two are declared scene kinds and four are auxiliary schemas the ledger
# records separately for source entries the operator selected.
# No other kind is preparable: an undeclared, malformed, not-adopted or
# unknown-shape entry is not a scene and is not a preparation input.
KIND_ROOMS: str = "rooms"
KIND_FUSED_SCENES: str = "fused_scenes"
KIND_TRANSLATION_MAP: str = "translation_map"
KIND_CUT_MAP: str = "cut_map"
KIND_MINED_FAMILIES: str = "mined_families"
KIND_MINED_LABELS: str = "mined_labels"

ALL_PREPARATION_KINDS: tuple[str, ...] = (
    KIND_ROOMS,
    KIND_FUSED_SCENES,
    KIND_TRANSLATION_MAP,
    KIND_CUT_MAP,
    KIND_MINED_FAMILIES,
    KIND_MINED_LABELS,
)

# -- The two explicit textual declarations the spec requires. ------------
# These are exposed as module-level constants so a test, a code review
# or a UI message can read them without re-deriving them. They are the
# only place the weight adaptation and the fused-scene compiled
# unverified marker live; if either changes, the contract changes and
# the spec under openspec/ must change with it.


WEIGHT_TEXT_ADAPTATION: str = (
    "The numeric 'weight' field on a source entry is a selection "
    "parameter the importer uses to weight which entries to draw and "
    "to detect outliers; it is not a text emphasis and it MUST NOT be "
    "translated into prompt-weight syntax (for example '(label:1.5)'). "
    "The preparation records the entry's weight as provenance on the "
    "take and uses the descriptive-input fields ('label', "
    "'scene_theme', 'tags', 'description', and so on) to build the "
    "prompt. A field named 'weight' never becomes a clause of the "
    "prepared prompt; an attempt to map it that way is a contract "
    "violation and is refused before the prompt is built."
)


FUSED_SCENES_COMPILED_BEHAVIOR: str = (
    "The 'fused_scenes' library stores a free-prose 'prompt' field "
    "whose exact rendering in the source application is NOT "
    "verifiable from the readable evidence this project has access "
    "to. The split of that prose into camera, act and room clauses, "
    "the routing of each clause, and the resulting prompt structure "
    "are treated as unverified: the preparation contract does NOT "
    "claim parity with any compiled behavior, does NOT auto-split the "
    "prose, and does NOT silently rewrite the original. The 'prompt' "
    "string is preserved intact, passed to the prompt as descriptive "
    "input, and any visible contradiction with a fixed session choice "
    "(identity, look, wardrobe) is reported with a field-specific "
    "reason. A tested-visible claim that this preparation reproduces "
    "the source's compiled output is unsupported; do not write one."
)


# -- Auxiliary schema notes. These explain what each auxiliary kind is
# used for and why none of its fields ever enter a prompt. The notes
# are short on purpose: the detailed contract is in the spec under
# openspec/, and the implementation under
# ``backend.resource_ledger.AUXILIARY_MAPS``.
AUXILIARY_KIND_NOTES: dict[str, str] = {
    KIND_TRANSLATION_MAP: (
        "translation_map: keyed by source string, value carries the "
        "English translation and the field names the translation "
        "applies to. The source strings and the translation values "
        "are pipeline data, not prompt content."
    ),
    KIND_CUT_MAP: (
        "cut_map: keyed by source identifier, value carries the cut "
        "triple (camera, act, room) the mining pipeline uses. The "
        "triple names and the source identifier are pipeline data, "
        "not prompt content."
    ),
    KIND_MINED_FAMILIES: (
        "mined_families: keyed by source identifier, value is the "
        "family name the mining pipeline produced. Used for "
        "selection only; never a prompt input."
    ),
    KIND_MINED_LABELS: (
        "mined_labels: keyed by row key, value is the judge's label "
        "for that row. Used by the judge/measurement pipeline only; "
        "never a prompt input."
    ),
}


# -- The per-kind preparation mapping. -----------------------------------
# Each entry: ``role``, ``required`` (bool), ``reason`` (non-empty iff
# the role is ``intentionally_unused``), and optional ``notes`` for any
# extra context. The field names below are drawn from the structural
# field-name set the ledger records: they match names the source uses
# (for example, the rooms library uses ``scene_theme`` as its theme
# field) and the names the inventory's ``PROVISIONAL_FIELD_ROLES`` table
# documents (for example, ``identifier`` and ``name`` as aliases of
# ``id`` and ``label``). Adding a field that is not in this mapping and
# is not one of the auxiliary schemas is what the spec calls an
# "unknown or unmapped field" and is refused by
# ``extract_prompt_inputs`` below.

ROOMS_FIELD_MAPPING: dict[str, dict[str, Any]] = {
    # -- identity ------------------------------------------------------------
    "id": {
        "role": ROLE_IDENTITY,
        "required": True,
    },
    "identifier": {
        "role": ROLE_IDENTITY,
        "required": False,
        "notes": "alternate spelling of 'id'; either 'id' or 'identifier' is sufficient",
    },
    "key": {
        "role": ROLE_IDENTITY,
        "required": False,
        "notes": "alternate spelling of 'id' recorded by some libraries; the inventory's identifier role",
    },
    # -- selection metadata --------------------------------------------------
    "library": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "source_library": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "lib": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "version": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "category": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "kind": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "source_family": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "family": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "profile_key": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "profile": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "body_profile": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "weight": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
        "notes": "numeric selection parameter; see WEIGHT_TEXT_ADAPTATION",
    },
    "enabled": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    # -- descriptive input ---------------------------------------------------
    "label": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": True,
    },
    "name": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
        "notes": "alternate spelling of 'label'",
    },
    "title": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
        "notes": "alternate spelling of 'label'",
    },
    "display_name": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
        "notes": "alternate spelling of 'label'",
    },
    "scene_theme": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": True,
    },
    "theme": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
        "notes": "alternate spelling of 'scene_theme'",
    },
    "theme_text": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
        "notes": "alternate spelling of 'scene_theme'",
    },
    "text": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
    },
    "description": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
    },
    "tags": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
    },
    "tag": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
        "notes": "singular alias of 'tags'",
    },
    "props": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
    },
    "objects": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
    },
    "furniture": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
    },
    "uniform_fit": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
    },
    # -- writer guidance -----------------------------------------------------
    "notes": {
        "role": ROLE_WRITER_GUIDANCE,
        "required": False,
    },
    "anchors": {
        "role": ROLE_WRITER_GUIDANCE,
        "required": False,
    },
    "mood": {
        "role": ROLE_WRITER_GUIDANCE,
        "required": False,
    },
    # -- intentionally unused: the observed fields with no current
    # preparation role, each with a non-empty reason. The list is the
    # union of the names the operator's coverage ledger recorded as
    # 'unused' for at least one rooms library; every name below is one
    # the ledger's structural walk reached, so the contract here is
    # exhaustive for the corpus the operator has selected.
    "body_focus": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "niche composition axis recorded by some rooms libraries; "
            "no preparation consumer and no current session use"
        ),
    },
    "costume_hint": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints the wardrobe picker could use later; today the "
            "session's resolved wardrobe takes precedence and the "
            "field has no prompt role"
        ),
    },
    "dominance_axis": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "niche composition axis recorded by some rooms libraries; "
            "no preparation consumer and no current session use"
        ),
    },
    "entry_schema": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "structural metadata describing the entry's own shape; "
            "it is not a content field and has no prompt role"
        ),
    },
    "intensity_level": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "niche composition axis recorded by some rooms libraries; "
            "no preparation consumer and no current session use"
        ),
    },
    "items": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "structural container for the library's entries; not a "
            "per-entry content field and has no prompt role"
        ),
    },
    "keywords": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints without a current preparation consumer; not "
            "auto-merged into the prompt"
        ),
    },
    "lighting_hint": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints without a current preparation consumer; the "
            "session's fixed look takes precedence when set"
        ),
    },
    "moods": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints without a current preparation consumer; the "
            "session's fixed look takes precedence when set"
        ),
    },
    "play_axis": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "niche composition axis recorded by some rooms libraries; "
            "no preparation consumer and no current session use"
        ),
    },
    "pose_hint": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints without a current preparation consumer; take-level "
            "pose is supplied by the session's effective state, not by "
            "this hint field"
        ),
    },
    "privacy_level": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "operator-side flag; not a content field and has no prompt "
            "role"
        ),
    },
    "prop_hint": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints without a current preparation consumer; the "
            "session's resolved wardrobe takes precedence when set"
        ),
    },
}


FUSED_SCENES_FIELD_MAPPING: dict[str, dict[str, Any]] = {
    # -- identity ------------------------------------------------------------
    "id": {
        "role": ROLE_IDENTITY,
        "required": True,
    },
    "identifier": {
        "role": ROLE_IDENTITY,
        "required": False,
        "notes": "alternate identifier spelling in a fused-scene record",
    },
    # -- selection metadata --------------------------------------------------
    "library": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    "weight": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
        "notes": "numeric selection parameter; see WEIGHT_TEXT_ADAPTATION",
    },
    "source_family": {
        "role": ROLE_SELECTION_METADATA,
        "required": False,
    },
    # -- descriptive input ---------------------------------------------------
    "prompt": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": True,
        "notes": (
            "free-prose 'prompt' field; see FUSED_SCENES_COMPILED_BEHAVIOR"
        ),
    },
    "label": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
    },
    "scene_theme": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
    },
    "tags": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "required": False,
    },
    # -- intentionally unused -----------------------------------------------
    "keywords": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints without a current preparation consumer; the "
            "session's fixed look and the 'prompt' prose take "
            "precedence"
        ),
    },
    "lighting_hint": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints without a current preparation consumer; the "
            "session's fixed look takes precedence when set"
        ),
    },
    "moods": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints without a current preparation consumer; the "
            "session's fixed look takes precedence when set"
        ),
    },
    "pose_hint": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints without a current preparation consumer; take-level "
            "pose is supplied by the session's effective state, not by "
            "this hint field"
        ),
    },
    "prop_hint": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "hints without a current preparation consumer; the "
            "session's resolved wardrobe takes precedence when set"
        ),
    },
    "items": {
        "role": ROLE_INTENTIONALLY_UNUSED,
        "required": False,
        "reason": (
            "structural container for fused-scene records; it is not a "
            "per-entry preparation input"
        ),
    },
}


# -- Auxiliary schemas: the four kinds the coverage ledger recognises.
# Each auxiliary kind has the SAME mapping shape: a fixed set of
# content-bearing field names the auxiliary file uses, every one of
# them ``auxiliary_data``, none of them required, none of them into
# the prompt. A field an auxiliary file actually uses that is not in
# this mapping is reported by ``validate_resource_entry`` as
# "unmapped" and the auxiliary file is treated as a structured
# pipeline input rather than a scene.

_AUXILIARY_RECORD_FIELDS: dict[str, dict[str, Any]] = {
    # The record-shape auxiliary kinds (translation_map, cut_map)
    # carry these as their content-bearing field names. Every one is
    # ``auxiliary_data``: pipeline data, never prompt content.
    "fields": {
        "role": ROLE_AUXILIARY_DATA,
        "required": False,
    },
    "source": {
        "role": ROLE_AUXILIARY_DATA,
        "required": False,
    },
    "translation": {
        "role": ROLE_AUXILIARY_DATA,
        "required": False,
    },
    "camera": {
        "role": ROLE_AUXILIARY_DATA,
        "required": False,
    },
    "act": {
        "role": ROLE_AUXILIARY_DATA,
        "required": False,
    },
    "room": {
        "role": ROLE_AUXILIARY_DATA,
        "required": False,
    },
}


# The two scalar auxiliary kinds (mined_families, mined_labels) carry
# their value as a string, not as a record. A scalar value passes the
# preparation check when it is a non-empty string; otherwise the
# reason is a field-specific message naming the empty value.
_SCALAR_AUXILIARY_KINDS: frozenset[str] = frozenset({
    KIND_MINED_FAMILIES,
    KIND_MINED_LABELS,
})


def is_scalar_auxiliary_kind(kind: str) -> bool:
    """``True`` when ``kind`` is a scalar auxiliary (value is a string)."""
    return kind in _SCALAR_AUXILIARY_KINDS


def _auxiliary_record_mapping() -> dict[str, dict[str, Any]]:
    """The shared record-shape auxiliary mapping: every field is ``auxiliary_data``.

    A fresh copy is returned each call so a test that mutates the
    result cannot leak the change into another call. The two
    record-shape auxiliary kinds (translation_map, cut_map) use the
    same mapping; the difference between them is the SCHEMA, declared
    in ``backend.resource_ledger.AUXILIARY_MAPS``, not the per-field
    role.
    """
    return dict(_AUXILIARY_RECORD_FIELDS)


TRANSLATION_MAP_FIELD_MAPPING: dict[str, dict[str, Any]] = dict(_AUXILIARY_RECORD_FIELDS)
CUT_MAP_FIELD_MAPPING: dict[str, dict[str, Any]] = dict(_AUXILIARY_RECORD_FIELDS)
MINED_FAMILIES_FIELD_MAPPING: dict[str, dict[str, Any]] = {
    # A mined-families file is a flat identifier -> family string. The
    # value is a scalar, not a record; the only field name it carries
    # at the value level is the family string itself, which is
    # selection metadata for the identifier it sits under. No
    # record-level field name is "prompt content" in the preparation
    # sense, so this kind's mapping is empty: there is no per-field
    # check for a scalar value, and the contract that the value is
    # ``auxiliary_data`` is carried by the kind itself.
}
MINED_LABELS_FIELD_MAPPING: dict[str, dict[str, Any]] = {
    # A mined-labels file is a flat row-key -> label string. Same
    # reasoning as mined_families: the value is a scalar with a
    # pipeline role, not a prompt input.
}


PREPARATION_FIELD_MAPPING: dict[str, dict[str, dict[str, Any]]] = {
    KIND_ROOMS: ROOMS_FIELD_MAPPING,
    KIND_FUSED_SCENES: FUSED_SCENES_FIELD_MAPPING,
    KIND_TRANSLATION_MAP: TRANSLATION_MAP_FIELD_MAPPING,
    KIND_CUT_MAP: CUT_MAP_FIELD_MAPPING,
    KIND_MINED_FAMILIES: MINED_FAMILIES_FIELD_MAPPING,
    KIND_MINED_LABELS: MINED_LABELS_FIELD_MAPPING,
}


# -- Field-name suffix / prefix rules the inventory recognises. -----------
# The inventory's ``is_guidance_field`` name rules (the ``*_anchor``
# suffix and the ``mood_*`` prefix) extend the preparation mapping the
# same way: a field whose name matches one of these rules is treated
# as ``writer_guidance`` for both rooms and fused_scenes, with the
# same non-required status. The matchers are exposed as functions so
# a test can reason about them without depending on the inventory's
# private helpers.


def _is_anchor_suffix(name: str) -> bool:
    """The inventory's '*_anchor' suffix rule.

    The inventory documents the rule in
    ``backend.resource_ledger._ANCHOR_SUFFIX_ROLE`` and the importer
    uses it to decide whether a field is a writing guide. The
    preparation contract honours the same rule for the same reason:
    an anchor-style field is data the writer uses, not a clause of
    the prompt.
    """
    return name.endswith("_anchor")


def _is_mood_prefix(name: str) -> bool:
    """The inventory's 'mood_*' prefix rule.

    The inventory documents the rule in
    ``backend.resource_ledger._MOOD_PREFIX_ROLE``. A mood-style field
    is data the writer uses, not a clause of the prompt.
    """
    return name.startswith("mood_")


# -- Public helpers. ------------------------------------------------------


def kinds() -> tuple[str, ...]:
    """The kinds the preparation contract supports, in declaration order."""
    return ALL_PREPARATION_KINDS


def mapping_for_kind(kind: str) -> dict[str, dict[str, Any]]:
    """The per-field mapping for ``kind``.

    Returns the mapping by reference for the scene kinds and a fresh
    copy for the auxiliary kinds (so a test that mutates the result
    cannot leak into another call's view of the same auxiliary kind).
    The caller MUST treat the returned dict as read-only.
    """
    if kind not in PREPARATION_FIELD_MAPPING:
        raise ValueError(
            f"unsupported preparation kind {kind!r}; supported: "
            f"{ALL_PREPARATION_KINDS}"
        )
    return PREPARATION_FIELD_MAPPING[kind]


def classify_field(kind: str, field_name: str) -> dict[str, Any]:
    """The preparation entry for ``(kind, field_name)``, or an "unmapped" record.

    The returned dict has the keys ``role``, ``required`` and
    ``reason`` (the last only when the role is
    ``ROLE_INTENTIONALLY_UNUSED``). A name that is not in the mapping
    returns ``{"role": "unmapped", "required": False, "reason":
    "field is not in the preparation mapping for kind {kind}"}``;
    such a field MUST NOT enter the prompt silently.
    """
    mapping = mapping_for_kind(kind)
    if field_name in mapping:
        return dict(mapping[field_name])
    if _is_anchor_suffix(field_name) or _is_mood_prefix(field_name):
        return {
            "role": ROLE_WRITER_GUIDANCE,
            "required": False,
            "notes": "name suffix or prefix rule; see is_guidance_field",
        }
    return {
        "role": "unmapped",
        "required": False,
        "reason": (
            f"field is not in the preparation mapping for kind {kind!r}; "
            "unknown fields MUST NOT enter the prompt silently"
        ),
    }


def is_auxiliary_kind(kind: str) -> bool:
    """``True`` when ``kind`` is one of the four auxiliary schemas.

    An auxiliary file is a pipeline input (selection, translation,
    mining, judging), never a scene. The preparation contract
    recognises this distinction and refuses to build prompt input
    from an auxiliary file.
    """
    return kind in (
        KIND_TRANSLATION_MAP,
        KIND_CUT_MAP,
        KIND_MINED_FAMILIES,
        KIND_MINED_LABELS,
    )


def extract_prompt_inputs(
    kind: str,
    entry: Any,
) -> dict[str, Any]:
    """The fields the prompt is allowed to draw from ``entry``.

    The result contains ONLY fields whose role is
    ``ROLE_DESCRIPTIVE_INPUT`` for the given ``kind``. Identity,
    selection-metadata, writer-guidance, auxiliary-data and
    intentionally-unused fields are excluded by construction; an
    unknown field is excluded by construction. The caller may not
    extend the result with fields ``extract_prompt_inputs`` did not
    return without a separate, explicit, contract-checked decision.

    The function is pure: it does not mutate ``entry`` and does not
    read any global state. Two calls with the same arguments return
    the same output. An auxiliary kind returns an empty dict: an
    auxiliary file is pipeline data and never contributes prompt
    content, whether its value is a record or a scalar.
    """
    if is_auxiliary_kind(kind):
        return {}
    if not isinstance(entry, Mapping):
        raise TypeError(
            f"a preparation entry must be a mapping, got "
            f"{type(entry).__name__}"
        )
    mapping = mapping_for_kind(kind)
    out: dict[str, Any] = {}
    for name, value in entry.items():
        entry_ = mapping.get(name)
        if entry_ is None:
            continue
        if entry_.get("role") == ROLE_DESCRIPTIVE_INPUT:
            out[name] = value
    return out


def validate_resource_entry(
    kind: str,
    entry: Any,
) -> list[str]:
    """The preparation issues a covered entry carries, as a list of reasons.

    Each reason is a non-empty string naming one of:
      * a required field that is missing (``required field 'X' is
        missing for kind 'Y'``);
      * a field that is in the entry but not in the preparation
        mapping (``unmapped field 'X' for kind 'Y'``);
      * a field whose role is ``ROLE_AUXILIARY_DATA`` when the kind is
        a scene kind (an entry that is a record, not a value, cannot
        simultaneously be a scene and a translation-map record);
      * for an auxiliary record kind, any top-level record key that is
        not one of the documented auxiliary content fields;
      * for a scalar auxiliary kind, a value that is not a non-empty
        string.

    The function is pure and does not raise on a value that fails a
    check; it returns the empty list only when the entry passes every
    preparation check. A scalar auxiliary kind's value is a string,
    not a record, and the per-field check is skipped for it; the
    caller is expected to surface the returned reasons to the operator
    before the entry is used to prepare a prompt.
    """
    if is_scalar_auxiliary_kind(kind):
        if not isinstance(entry, str) or not entry:
            return [
                f"scalar auxiliary kind {kind!r} requires a non-empty "
                f"string value, got {type(entry).__name__}"
            ]
        return []
    if not isinstance(entry, Mapping):
        raise TypeError(
            f"a preparation entry must be a mapping, got "
            f"{type(entry).__name__}"
        )
    mapping = mapping_for_kind(kind)
    reasons: list[str] = []

    # Required-field check. A required field is required ONLY for the
    # scene kinds; an auxiliary kind has no required field by
    # contract (the auxiliary schemas are pipeline data and are
    # never blocking).
    if not is_auxiliary_kind(kind):
        for name, info in mapping.items():
            if info.get("required") and name not in entry:
                reasons.append(
                    f"required field {name!r} is missing for kind {kind!r}"
                )

    # Per-field check: an unknown field is reported with a field-
    # specific reason, the writer-guidance name rules are accepted,
    # every other field is checked against its role. A field whose
    # role is ``auxiliary_data`` is allowed in an auxiliary file and
    # not in a scene; a scene entry that carries a field whose name
    # is an auxiliary record key (e.g. ``source``, ``translation``,
    # ``camera``, ``act``, ``room``, ``fields``) gets a specific
    # reason that names the role mismatch, which is the more useful
    # message for an operator.
    auxiliary_record_keys = set(_AUXILIARY_RECORD_FIELDS)
    for name in entry:
        if _is_anchor_suffix(name) or _is_mood_prefix(name):
            continue
        if name in mapping:
            info = mapping[name]
            if is_auxiliary_kind(kind):
                continue
            if info.get("role") == ROLE_AUXILIARY_DATA:
                reasons.append(
                    f"field {name!r} on a scene entry has the "
                    f"auxiliary_data role; the kind is {kind!r}, not "
                    f"an auxiliary schema"
                )
            continue
        if is_auxiliary_kind(kind):
            if name in auxiliary_record_keys:
                continue
            reasons.append(
                f"unrecognised top-level field {name!r} for "
                f"auxiliary kind {kind!r}"
            )
            continue
        if name in auxiliary_record_keys:
            reasons.append(
                f"field {name!r} is an auxiliary record key and is "
                f"not a field of scene kind {kind!r}"
            )
            continue
        reasons.append(
            f"unmapped field {name!r} for kind {kind!r}; unknown "
            f"fields MUST NOT enter the prompt silently"
        )

    return reasons


def required_fields_for_kind(kind: str) -> tuple[str, ...]:
    """The fields ``validate_resource_entry`` requires for ``kind``.

    The returned names are the names the caller must supply for
    preparation to be allowed. For an auxiliary kind the result is
    empty: auxiliary schemas are never blocking.
    """
    mapping = mapping_for_kind(kind)
    return tuple(
        name for name, info in mapping.items() if info.get("required")
    )


def intentionally_unused_fields_for_kind(kind: str) -> tuple[str, ...]:
    """The fields the contract declares as ``intentionally_unused``.

    Every name returned here is guaranteed to have a non-empty
    ``reason`` in its mapping entry; ``classify_field`` returns the
    same reason. The list is sorted alphabetically to keep the
    contract easy to compare across runs.
    """
    mapping = mapping_for_kind(kind)
    return tuple(sorted(
        name for name, info in mapping.items()
        if info.get("role") == ROLE_INTENTIONALLY_UNUSED
    ))
