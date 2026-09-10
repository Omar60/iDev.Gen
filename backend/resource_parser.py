"""Shared resource parser for the resource-session-planning work (task 2.2).

The parser turns declared source data into explicit, inspectable parse
results BEFORE any database write, preview/commit fingerprint work, or
import-commit operation. Those downstream steps belong to tasks 2.3
through 2.5; this module's job is to surface every input the caller
handed it as one of: accepted, auxiliary, malformed, unsupported,
ambiguous, or missing. Nothing is silently dropped and nothing is
re-encoded.

Three rules are pinned here and nowhere else:

  1. The complete original accepted object survives the parse. The
     `AcceptedEntry.original` field is the exact dict the parser was
     handed, with no key reordering, no list reversal, no float
     normalisation, no whitespace stripping, and no field removal.
     Familiar fields are classified by their role; unfamiliar fields
     are reported as retained-but-unused in `unknown_fields` so a
     later persistence step can keep them on the asset_revision
     payload column. The original is what `resource_store.record_revision`
     will write.

  2. Auxiliary resources are detected structurally, not by guessing.
     A top-level dict whose values share a known auxiliary shape
     (translation_map, cut_map, mined_families, mined_labels) is
     classified as auxiliary and kept intact under `AuxiliaryResource.payload`.
     A top-level dict that does NOT match any known shape and does
     NOT look like a scene record is `unsupported` with a reason,
     never silently re-interpreted as a scene.

  3. Every input is accounted for. The sum
     ``len(accepted) + len(auxiliary) + len(malformed) +
     len(unsupported) + len(ambiguous_identifier) +
     len(missing_identifier)`` equals the number of input items the
     parser was handed. The helper ``ParseResult.every_input_accounted_for``
     pins this property so a test can prove it on any non-empty
     input.

This module does NOT:

- touch the database (no INSERT, no SELECT, no transaction);
- call `asset_guard` for content-based filtering, or otherwise apply
  any accept/refuse decision based on the words an entry contains;
- compute content digests, bind to source fingerprints, or perform
  any preview/commit operation;
- read files, walk directories, open HTTP routes, or call ComfyUI;
- decide readiness, prompt preparation, or session plans.

Those are the next tasks. The parser is one bounded layer between
declared source data and a database write, with the property that
every input reaches a bucket and every accepted input keeps its
whole payload.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


# -- Vocabulary re-used from the broader resource work. The parser
#    does not import ``backend.resource_ledger`` or
#    ``backend.resource_prompts`` to keep this layer independent: the
#    parser is a precondition for ledger state, and a private fork of
#    the vocabulary would defeat the "single source of truth" rule
#    those modules pin in their own docstrings. The string values are
#    the same ones those modules publish, so a caller that already
#    imports them can compare role / kind strings directly.


ROLE_IDENTIFIER: str = "identifier"
ROLE_SELECTION_METADATA: str = "selection_metadata"
ROLE_DESCRIPTIVE_INPUT: str = "descriptive_input"
ROLE_WRITER_GUIDANCE: str = "writer_guidance"
ROLE_AUXILIARY_DATA: str = "auxiliary_data"
ROLE_UNUSED: str = "unused"

ALL_ROLES: tuple[str, ...] = (
    ROLE_IDENTIFIER,
    ROLE_SELECTION_METADATA,
    ROLE_DESCRIPTIVE_INPUT,
    ROLE_WRITER_GUIDANCE,
    ROLE_AUXILIARY_DATA,
    ROLE_UNUSED,
)


# The two scene kinds and the four auxiliary kinds the OpenSpec
# declares for the resource path. A kind outside this closed set is
# not preparable: an undeclared or unknown-shape input lands in
# ``unsupported`` with a reason.
KIND_ROOMS: str = "rooms"
KIND_FUSED_SCENES: str = "fused_scenes"

KIND_TRANSLATION_MAP: str = "translation_map"
KIND_CUT_MAP: str = "cut_map"
KIND_MINED_FAMILIES: str = "mined_families"
KIND_MINED_LABELS: str = "mined_labels"

ALL_AUXILIARY_KINDS: tuple[str, ...] = (
    KIND_TRANSLATION_MAP,
    KIND_CUT_MAP,
    KIND_MINED_FAMILIES,
    KIND_MINED_LABELS,
)


# The three names an entry may carry its own identifier in. The first
# non-empty one wins; two or more non-empty is reported as ambiguous.
IDENTIFIER_FIELDS: tuple[str, ...] = ("id", "identifier", "key")


# The label / theme / list field names the parser uses to decide a
# dict is a scene record. The fused_scene detection is independent:
# it looks for a single ``prompt`` field and an identifier, with no
# other descriptive content.
LABEL_FIELDS: tuple[str, ...] = (
    "label", "name", "title", "display_name",
)
THEME_FIELDS: tuple[str, ...] = (
    "scene_theme", "theme", "theme_text", "text", "description",
)
LIST_FIELDS: tuple[str, ...] = (
    "tags", "tag", "props", "objects", "furniture",
)


# Selection metadata: fields the parser recognises as configuration
# rather than content. None of these are entered into a prompt; the
# parser only classifies them so a caller can reason about which
# fields of an entry are selection-shaped and which are content-shaped.
SELECTION_METADATA_FIELDS: frozenset[str] = frozenset((
    "library", "lib", "source_library", "weight", "kind",
    "category", "family", "source_family", "profile_key",
    "profile", "body_profile", "enabled", "version",
))


def _is_writer_guidance(name: str) -> bool:
    """The structural rule the parser uses to recognise writer guidance.

    A field is writer guidance if its name is ``notes``, ``anchors``,
    ``mood``, starts with ``mood_`` or ends with ``_anchor``. A field
    the rule does not name is not writer guidance even if it would
    later be classified as such by a per-library extension the parser
    does not have.
    """
    return (
        name == "notes"
        or name == "anchors"
        or name == "mood"
        or name.startswith("mood_")
        or name.endswith("_anchor")
    )


# -- Auxiliary structural shape tests. Each is a pure function on a
#    top-level dict, returning True when every value matches the
#    named shape. A dict that does not match any of these is not an
#    auxiliary; a dict that matches more than one is not guessable
#    and is reported as ambiguous (the first match wins, and the
#    other is logged as a "shadow match" on the auxiliary record so
#    a reviewer can see why a different shape was rejected).

_CUT_SLOTS: frozenset[str] = frozenset(("camera", "act", "room"))


def _is_translation_map_shape(data: Any) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    for value in data.values():
        if not isinstance(value, dict):
            return False
        if {"source", "translation", "fields"} - value.keys():
            return False
    return True


def _is_cut_map_shape(data: Any) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    for value in data.values():
        if not isinstance(value, dict):
            return False
        for slot_key, slot_value in value.items():
            if slot_key not in _CUT_SLOTS:
                return False
            if slot_value is not None and not (
                isinstance(slot_value, str) and slot_value
            ):
                return False
    return True


def _is_mined_families_shape(data: Any) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            return False
        if not isinstance(value, str) or not value:
            return False
    return True


def _is_mined_labels_shape(data: Any) -> bool:
    # Same shape as mined_families; the distinction is by stem in the
    # inventory, not by the body. Kept as a separate function so a
    # future divergence (labels gaining structure that families do
    # not) does not have to be reasoned about through one shared
    # helper.
    return _is_mined_families_shape(data)


def _looks_like_scene_record(data: Any) -> bool:
    """True when a dict has the structural shape of a scene record.

    A scene record carries AT LEAST ONE of:

    - an identifier field (``id``, ``identifier`` or ``key``);
    - a label field (``label``, ``name``, ``title``, ``display_name``);
    - a theme / prose field (``scene_theme``, ``theme``,
      ``theme_text``, ``text``, ``description``);
    - a ``prompt`` field (fused-scene descriptive prose);
    - a list field (``tags``, ``tag``, ``props``, ``objects``,
      ``furniture``).

    The identifier rule is "the field is present", not "the field
    is non-empty": a record that calls itself ``id`` but has a
    whitespace value is still a record-shaped thing, not an
    auxiliary map, and the parser reports the missing identifier
    explicitly. This is the only way a dict that contains an
    empty or whitespace ``id`` field does not silently fall
    through to the ``mined_families`` shape test, which would
    otherwise match any dict whose values are all non-empty
    strings.

    The list-field rule is "the field key is present, even with a
    null value": a record that names a ``tags`` field is a scene
    record, not a key-value auxiliary. The ``mined_families``
    shape test requires every value to be a non-empty string, so
    the presence of a list-shaped field is the structural
    witness that breaks the match.
    """
    if not isinstance(data, dict):
        return False
    for f in IDENTIFIER_FIELDS:
        if f in data:
            return True
    for f in LABEL_FIELDS:
        if isinstance(data.get(f), str) and data[f].strip():
            return True
    for f in THEME_FIELDS:
        if isinstance(data.get(f), str) and data[f].strip():
            return True
    if isinstance(data.get("prompt"), str) and data["prompt"].strip():
        return True
    for f in LIST_FIELDS:
        if f in data and data[f] is not None:
            return True
    return False


def _match_auxiliary_shape(data: Any) -> str | None:
    """The auxiliary kind a top-level dict matches, or None.

    Order matters. ``translation_map`` and ``cut_map`` are
    distinguishable: a translation map value carries three named
    keys, a cut map value carries a subset of three slot names. A
    dict that matches BOTH is not a single thing; the parser picks
    the first match and records the other in the
    ``AuxiliaryResource`` so a reviewer can see what was rejected.

    A dict that matches NEITHER is not auxiliary and falls through
    to the scene-record detection.
    """
    if not isinstance(data, dict) or not data:
        return None
    if "items" in data or "library" in data:
        return None
    if _is_translation_map_shape(data):
        return KIND_TRANSLATION_MAP
    if _is_cut_map_shape(data):
        return KIND_CUT_MAP
    if _is_mined_families_shape(data):
        return KIND_MINED_FAMILIES
    if _is_mined_labels_shape(data):
        return KIND_MINED_LABELS
    return None


def _auxiliary_shadow_matches(data: Any) -> list[str]:
    """The auxiliary kinds a dict ALSO matches, beyond the first one.

    Used to surface a "would also have matched X" note on the
    ``AuxiliaryResource`` so a reviewer can see whether two shape
    tests could have produced different results. The first match
    wins; the rest are diagnostic, not the basis for classification.
    """
    if not isinstance(data, dict) or not data:
        return []
    if "items" in data or "library" in data:
        return []
    matched: list[str] = []
    for name, test in (
        (KIND_TRANSLATION_MAP, _is_translation_map_shape),
        (KIND_CUT_MAP, _is_cut_map_shape),
        (KIND_MINED_FAMILIES, _is_mined_families_shape),
        (KIND_MINED_LABELS, _is_mined_labels_shape),
    ):
        if test(data):
            matched.append(name)
    return matched


# -- Field classification. Each top-level key on an accepted entry
#    receives a role. Known fields map to their role by the rules
#    below; everything else is ROLE_UNUSED with a non-empty reason.


def _classify_field(name: str, kind: str) -> FieldClassification:
    """The role and (if unused) reason for a single top-level field.

    The role is taken from the per-kind contract below. The unused
    reason is a non-empty string when the role is ROLE_UNUSED and
    None otherwise; tests assert that property so a regression that
    drops the reason is visible.
    """
    if name in IDENTIFIER_FIELDS:
        return FieldClassification(name=name, role=ROLE_IDENTIFIER)
    if name in SELECTION_METADATA_FIELDS:
        return FieldClassification(name=name, role=ROLE_SELECTION_METADATA)
    if _is_writer_guidance(name):
        return FieldClassification(name=name, role=ROLE_WRITER_GUIDANCE)
    # Per-kind descriptive input. A field the per-kind contract names
    # is descriptive input; a field neither contract names is unused.
    descriptive_names = _DESCRIPTIVE_FIELDS_BY_KIND.get(kind, frozenset())
    if name in descriptive_names:
        return FieldClassification(name=name, role=ROLE_DESCRIPTIVE_INPUT)
    # Auxiliary data fields. A field the parser knows belongs to an
    # auxiliary kind is classified as auxiliary data on a scene
    # record too, so a future move from "auxiliary only" to "carry
    # the auxiliary record alongside" is a classification change, not
    # a field-removal.
    if name in _AUXILIARY_DATA_FIELDS:
        return FieldClassification(
            name=name, role=ROLE_AUXILIARY_DATA,
        )
    # Unknown / unused field. The reason names the field and the
    # role it was checked against, so a test can read the reason
    # off a FieldClassification without having to look elsewhere.
    return FieldClassification(
        name=name,
        role=ROLE_UNUSED,
        unused_reason=(
            f"field {name!r} is not named by the {kind} preparation "
            f"contract; the original value is retained on the "
            f"accepted entry but no consumer reads it"
        ),
    )


# The descriptive-input field names, by kind. Both kinds share the
# common ones; fused_scenes adds 'prompt' which is its single
# descriptive field.
_DESCRIPTIVE_FIELDS_BY_KIND: dict[str, frozenset[str]] = {
    KIND_ROOMS: frozenset((
        "label", "name", "title", "display_name",
        "scene_theme", "theme", "theme_text", "text", "description",
        # A rooms entry can carry a `prompt` field too, and the
        # parser does not strip it: descriptive prose is
        # descriptive prose regardless of which field name the
        # source used. A rooms record whose descriptive content
        # is a `prompt` is the same kind of input as one whose
        # descriptive content is a `scene_theme`, and a
        # preparation that reads either treats them the same.
        "prompt",
        "tags", "tag", "props", "objects", "furniture", "uniform_fit",
    )),
    KIND_FUSED_SCENES: frozenset((
        # A fused scene's only descriptive content is its prompt
        # string. The label fields are accepted too because the
        # source writes them; the parser does not strip them.
        "label", "name", "title", "display_name",
        "prompt",
    )),
}


# Auxiliary data fields: a field whose name comes from an auxiliary
# kind. Recognised on a scene record so a record that carries
# auxiliary metadata (for example a translation map alongside a
# scene) is classified as carrying auxiliary data, not as having
# an unknown field.
_AUXILIARY_DATA_FIELDS: frozenset[str] = frozenset((
    # translation_map values carry these as a sibling of the source
    # string and translation. A scene entry that already carries
    # them is unusual but the parser does not drop them.
    "source", "translation", "fields",
    # cut_map slot names.
    "camera", "act", "room",
))


# -- Result types ----------------------------------------------------------


@dataclass
class FieldClassification:
    """One field of an accepted entry, with its role.

    The full value is NOT carried here. ``AcceptedEntry.original``
    holds the value, untouched, for the persistence step.
    """

    name: str
    role: str
    unused_reason: str | None = None


@dataclass
class AcceptedEntry:
    """An entry the parser accepts, with the full original preserved.

    The kind is the OpenSpec-declared category the parser classified
    the entry under. The original dict is the input the parser was
    given for this entry, byte-for-byte. A later persistence step
    (task 2.3) uses ``original`` as the asset_revision.payload.

    The parser never re-encodes ``original``; passing it through
    ``json.dumps(ensure_ascii=False)`` then back through
    ``json.loads`` round-trips a value that compares equal to the
    input, which is the same property the ``resource_store`` tests
    pin for the persistence layer.
    """

    kind: str
    source_id: str
    original: dict
    field_classifications: list[FieldClassification] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)


@dataclass
class AuxiliaryResource:
    """An auxiliary map recognised structurally, kept intact.

    The payload is the top-level dict the parser was handed, with
    no re-encoding. A later persistence step writes it into the
    auxiliary store the OpenSpec names (separate from the
    asset_revision table, because auxiliary resources are not
    prompt inputs).

    The ``shadow_matches`` list names the auxiliary kinds the body
    ALSO matched structurally beyond the one assigned. The first
    match wins; the rest are diagnostic, so a reviewer can see why
    a future divergence between two shape tests would have to be
    resolved deliberately rather than by silent reclassification.
    """

    kind: str
    payload: dict
    entry_count: int
    shadow_matches: list[str] = field(default_factory=list)


@dataclass
class MalformedInput:
    """An input that could not be parsed safely.

    The index is the position of the input in the original
    sequence (a list of items), or 0 for a single top-level
    payload. The reason names the structural problem in plain
    English so a reviewer reading the import report can see what
    failed without re-running the parse.
    """

    index: int
    reason: str
    received_type: str


@dataclass
class UnsupportedShape:
    """An input that looks structured but is not a scene, fused
    scene or auxiliary.

    The reason names the structural test that failed: ``no
    identifier and no scene content``, ``identifier with no
    descriptor``, and so on. The parser never guesses; a dict
    that is one of these stays one of these, with a reason.
    """

    index: int
    reason: str
    received_type: str


@dataclass
class AmbiguousIdentifier:
    """An input whose identifier is split across multiple fields.

    Two or more of ``id``, ``identifier``, ``key`` are non-empty.
    The parser refuses to guess which one is the source identity;
    the caller reads the named fields and resolves the ambiguity.
    The input is NOT classified as accepted: a missing decision
    is the right answer here, and adding it to ``accepted`` would
    be a guess.
    """

    index: int
    identifier_fields: list[str]
    reason: str


@dataclass
class MissingIdentifier:
    """An input that has no identifier at all.

    The expected_kind is the kind the entry would have been
    classified under had an identifier been present; empty means
    even the kind was undecidable. A scene-shaped entry with no
    identifier is a known shape the persistence step cannot write
    without a key, and reporting it explicitly is the whole point
    of this bucket.
    """

    index: int
    expected_kind: str
    reason: str


@dataclass
class ParseResult:
    """The full result of parsing one source payload.

    Every input the parser was handed is represented in exactly
    one of: accepted, auxiliary, malformed, unsupported,
    ambiguous_identifier, missing_identifier. The helper
    ``every_input_accounted_for(input_size)`` proves this on any
    non-empty input.
    """

    accepted: list[AcceptedEntry] = field(default_factory=list)
    auxiliary: list[AuxiliaryResource] = field(default_factory=list)
    malformed: list[MalformedInput] = field(default_factory=list)
    unsupported: list[UnsupportedShape] = field(default_factory=list)
    ambiguous_identifier: list[AmbiguousIdentifier] = field(default_factory=list)
    missing_identifier: list[MissingIdentifier] = field(default_factory=list)

    def total(self) -> int:
        return (
            len(self.accepted)
            + len(self.auxiliary)
            + len(self.malformed)
            + len(self.unsupported)
            + len(self.ambiguous_identifier)
            + len(self.missing_identifier)
        )

    def every_input_accounted_for(self, input_size: int) -> bool:
        """True when every input reached exactly one bucket.

        The input_size is the number of items the parser was
        handed: 1 for a single dict / scalar at the top, the
        length of the list for a list. The parser guarantees the
        sum of bucket sizes equals input_size for any well-formed
        call, and the helper makes that guarantee assertable.
        """
        if input_size < 0:
            return False
        return self.total() == input_size


# -- The parser -----------------------------------------------------------


def _identifier_field_value(entry: dict) -> tuple[str | None, list[str]]:
    """The non-empty identifier value and the list of fields that carry one.

    Two or more non-empty values is the ambiguous-identifier case;
    the parser reports it rather than picking one. The check is
    against a non-empty trimmed string, so an ``"id": "  "`` entry
    is not "having" an identifier and is reported under
    missing_identifier.
    """
    present: list[str] = []
    value: str | None = None
    for field_name in IDENTIFIER_FIELDS:
        raw = entry.get(field_name)
        if isinstance(raw, str) and raw.strip():
            present.append(field_name)
            if value is None:
                value = raw
    return value, present


def _classify_scene_kind(entry: dict) -> str | None:
    """The kind of scene record a dict looks like, or None.

    A dict is a ``fused_scenes`` record when it carries a
    non-empty string ``prompt`` and no other descriptive content
    (no label / theme / list field). It is a ``rooms`` record when
    it carries a label, a theme/prose field, or a list of
    props/tags. A dict that has only metadata (no identifier
    field test happens here, only structural content test) is
    None; the caller reports that as ``unsupported``.
    """
    has_prompt = isinstance(entry.get("prompt"), str) and entry["prompt"].strip()
    has_label = any(
        isinstance(entry.get(f), str) and entry[f].strip()
        for f in LABEL_FIELDS
    )
    has_theme = any(
        isinstance(entry.get(f), str) and entry[f].strip()
        for f in THEME_FIELDS
    )
    has_list = any(f in entry and entry[f] is not None for f in LIST_FIELDS)

    if has_prompt and not has_label and not has_theme and not has_list:
        return KIND_FUSED_SCENES
    if has_label or has_theme or has_list:
        return KIND_ROOMS
    return None


def _parse_entry(item: Any, index: int, result: ParseResult) -> None:
    """Parse a single top-level item, appending the result to ``result``.

    The item is expected to be a dict by the time the caller reaches
    here; the caller is responsible for routing non-dict items to
    ``MalformedInput`` first.
    """
    if not isinstance(item, dict):
        result.malformed.append(MalformedInput(
            index=index,
            reason=(
                f"list item is {type(item).__name__}, expected a dict"
            ),
            received_type=type(item).__name__,
        ))
        return

    # The "library" key on a source entry is set by the file-level
    # extraction (see backend/extractor.py); it is metadata, not
    # scene content. We keep it on the original and classify it
    # under selection_metadata so a later preparation step can read
    # it without re-walking the entry.

    source_id, present_identifier_fields = _identifier_field_value(item)

    # Ambiguous identifier: two or more non-empty identifier fields.
    if len(present_identifier_fields) > 1:
        result.ambiguous_identifier.append(AmbiguousIdentifier(
            index=index,
            identifier_fields=list(present_identifier_fields),
            reason=(
                f"entry has multiple non-empty identifier fields: "
                f"{', '.join(present_identifier_fields)}; the parser "
                f"does not guess which one is the source identity"
            ),
        ))
        return

    # No identifier at all: report as missing after determining the
    # expected kind from the structural content, so the import
    # report names what kind the entry was *going* to be.
    if source_id is None:
        kind = _classify_scene_kind(item)
        if kind is None:
            result.unsupported.append(UnsupportedShape(
                index=index,
                reason=(
                    "dict has no identifier and no scene content "
                    "(no label, no theme/prose field, no list of "
                    "props/tags); not a scene, fused scene or "
                    "auxiliary"
                ),
                received_type="dict",
            ))
        else:
            result.missing_identifier.append(MissingIdentifier(
                index=index,
                expected_kind=kind,
                reason=(
                    f"entry looks like a {kind} but has no "
                    f"identifier (expected one of "
                    f"{', '.join(IDENTIFIER_FIELDS)})"
                ),
            ))
        return

    # The structural kind test: is this a scene / fused scene?
    kind = _classify_scene_kind(item)
    if kind is None:
        result.unsupported.append(UnsupportedShape(
            index=index,
            reason=(
                "dict has an identifier but no scene content "
                "(no label, no theme/prose field, no list of "
                "props/tags, no 'prompt' prose); not a scene or "
                "fused scene"
            ),
            received_type="dict",
        ))
        return

    # Classify every top-level field on the entry. The classification
    # is exhaustive: every key in the entry produces one
    # ``FieldClassification``. Unknown keys land in ROLE_UNUSED with
    # a non-empty reason; their values stay on the original.
    classifications: list[FieldClassification] = []
    unknown: list[str] = []
    for key in item.keys():
        fc = _classify_field(str(key), kind)
        classifications.append(fc)
        if fc.role == ROLE_UNUSED:
            unknown.append(fc.name)

    result.accepted.append(AcceptedEntry(
        kind=kind,
        source_id=source_id,
        original=item,
        field_classifications=classifications,
        unknown_fields=unknown,
    ))



_ENTRY_CONTENT_MARKERS: tuple[str, ...] = (
    *IDENTIFIER_FIELDS,
    "prompt",
    "scene_theme",
    "theme",
    "theme_text",
    "text",
    "label",
    "display_name",
    "props",
    "objects",
    "furniture",
    "tags",
    "tag",
    "uniform_fit",
    "mood_light",
    "action_anchor",
    "anchors",
)


def is_source_envelope(data: Any) -> bool:
    """True when `data` is a source collection envelope {library: ..., items: [...]}.

    A source envelope carries top-level collection metadata ('library') and an
    'items' list holding individual entries. It is neither an individual scene
    record nor an auxiliary resource map.
    """
    if not isinstance(data, dict):
        return False
    if "items" not in data or not isinstance(data["items"], list):
        return False
    if "library" not in data:
        return False
    if any(k in data for k in _ENTRY_CONTENT_MARKERS):
        return False
    return True


def normalize_source_payload(data: Any) -> Any:
    """Normalize a source payload if it is an envelope, otherwise return as-is.

    When `data` is a valid source envelope ({library: ..., items: [...]}),
    returns the underlying `items` list. Otherwise returns `data` unchanged.
    """
    if is_source_envelope(data):
        return data["items"]
    return data


def parse_source_payload(data: Any) -> ParseResult:
    """Parse one source payload into a structured result.

    The input contract — the same one ``input_size`` reports — is:

    - a source envelope dict: a dict carrying file-level 'library'
      metadata and an 'items' list. Each element of 'items' is parsed
      as an individual entry. An empty 'items' list accounts for 0
      inputs and produces 0 bucket entries.
    - a list of N items: every list element is one input. The
      function produces exactly N bucket entries (one per item),
      where each bucket entry is one of accepted / auxiliary /
      malformed / unsupported / ambiguous_identifier /
      missing_identifier. An empty list is zero inputs, zero
      buckets, which is the correct answer.
    - any non-list value, including ``None``, ``{}``, a single
      dict, a string, an int or a bool: exactly one input. The
      function produces exactly one bucket entry for it. A
      single dict is parsed as a scene record (and routed to
      accepted / missing_identifier / ambiguous_identifier /
      unsupported as the structural tests decide) or as an
      auxiliary map. A scalar or ``None`` at the top is reported
      as a single malformed input. An empty dict ``{}`` is
      reported as a single unsupported input (it has neither an
      identifier nor any scene content, so the scene-detection
      test fails and the auxiliary shape test also fails).

    The function never raises on shape errors. The only raises are
    programming errors (e.g. the caller passes an exotic object
    type the function does not know how to handle) and the
    function's own type-hint contract. The contract is that for
    any input the caller hands in, the result's
    ``total() == input_size(data)``: nothing is silently dropped,
    nothing is double-counted.
    """
    data = normalize_source_payload(data)
    result = ParseResult()

    if isinstance(data, dict):
        # Scene detection comes first: a dict with an identifier
        # field, a label / theme / prompt / list field is a scene
        # record, NOT an auxiliary. The auxiliary check is the
        # fallback for the case where the dict has none of those
        # markers and the body looks like a key-value map.
        if _looks_like_scene_record(data):
            _parse_entry(copy.deepcopy(data), 0, result)
            return result
        # Auxiliary detection as a fallback. A dict whose values
        # all share a known auxiliary shape is classified as
        # auxiliary; the body is preserved under
        # ``AuxiliaryResource.payload``.
        aux_kind = _match_auxiliary_shape(data)
        if aux_kind is not None:
            matches = _auxiliary_shadow_matches(data)
            result.auxiliary.append(AuxiliaryResource(
                kind=aux_kind,
                payload=copy.deepcopy(data),
                entry_count=len(data),
                shadow_matches=[m for m in matches if m != aux_kind],
            ))
            return result
        # Neither scene nor auxiliary: route to scene parsing for
        # proper reporting (unsupported, missing_identifier).
        _parse_entry(copy.deepcopy(data), 0, result)
        return result

    if isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, dict):
                if _looks_like_scene_record(item):
                    _parse_entry(copy.deepcopy(item), i, result)
                    continue
                aux_kind = _match_auxiliary_shape(item)
                if aux_kind is not None:
                    matches = _auxiliary_shadow_matches(item)
                    result.auxiliary.append(AuxiliaryResource(
                        kind=aux_kind,
                        payload=copy.deepcopy(item),
                        entry_count=len(item),
                        shadow_matches=[m for m in matches if m != aux_kind],
                    ))
                    continue
                # Neither scene nor auxiliary: route to scene
                # parsing for proper reporting.
                _parse_entry(copy.deepcopy(item), i, result)
            else:
                result.malformed.append(MalformedInput(
                    index=i,
                    reason=(
                        f"list item is {type(item).__name__}, "
                        f"expected a dict"
                    ),
                    received_type=type(item).__name__,
                ))
        return result

    # Scalar at the top: one malformed input. ``None`` lands here
    # too; it is a single top-level item that is not a dict or list.
    result.malformed.append(MalformedInput(
        index=0,
        reason=(
            f"top-level value is {type(data).__name__}, "
            f"expected a dict or list"
        ),
        received_type=type(data).__name__,
    ))
    return result


def input_size(data: Any) -> int:
    """The number of inputs ``parse_source_payload(data)`` accounts for.

    The contract is the one ``parse_source_payload`` implements:

    - a source envelope dict {library: ..., items: [...]}: returns the
      length of the 'items' list (0 for an empty envelope).
    - a list of N items: ``input_size`` returns ``N``; the
      parser produces exactly N bucket entries (one per list
      item). An empty list is 0.
    - any non-list value, including ``None``, ``{}``, a single
      dict, a string, an int or a bool: ``input_size`` returns
      1; the parser produces exactly one bucket entry for it.

    This is the number a caller passes to
    ``ParseResult.every_input_accounted_for`` to assert the
    every-input-accounted-for guarantee on a specific input.
    The guarantee is that
    ``parse_source_payload(data).total() == input_size(data)``
    for any value the parser is asked to parse.
    """
    if is_source_envelope(data):
        return len(data["items"])
    if isinstance(data, list):
        return len(data)
    return 1


__all__ = (
    "ROLE_IDENTIFIER", "ROLE_SELECTION_METADATA", "ROLE_DESCRIPTIVE_INPUT",
    "ROLE_WRITER_GUIDANCE", "ROLE_AUXILIARY_DATA", "ROLE_UNUSED",
    "ALL_ROLES",
    "KIND_ROOMS", "KIND_FUSED_SCENES",
    "KIND_TRANSLATION_MAP", "KIND_CUT_MAP", "KIND_MINED_FAMILIES",
    "KIND_MINED_LABELS", "ALL_AUXILIARY_KINDS",
    "IDENTIFIER_FIELDS", "LABEL_FIELDS", "THEME_FIELDS", "LIST_FIELDS",
    "SELECTION_METADATA_FIELDS",
    "FieldClassification", "AcceptedEntry", "AuxiliaryResource",
    "MalformedInput", "UnsupportedShape", "AmbiguousIdentifier",
    "MissingIdentifier", "ParseResult",
    "is_source_envelope", "normalize_source_payload",
    "parse_source_payload", "input_size",
)
