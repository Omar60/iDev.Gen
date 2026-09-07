"""Coverage ledger for operator-selected source libraries and auxiliary maps.

Every discovered source file is recorded with structural information only:
the file identity (stem + content digest + disambiguator), the top-level
shape, every entry shape the walk reached, the field names and their
structural types, a provisional field role where readable evidence
supports it, and the consumer evidence that names a `module.function`
rather than an absolute path.

No source prose reaches the ledger. A field name and a structural type
are not prose, and a content digest is not text. The ledger is the
inventory 1.1 builds; the final mapping of source fields onto the
preparation vocabulary is owned by task 1.2 and the ledger's roles are
deliberately provisional.

The walk is whole-file. A field first appearing in the ninth entry is
recorded; a field only ever seen in the hundredth is recorded; the cap
the old draft had was the bug 1.1 review named. Field observations are
deduplicated by `(name, structural_type)`, so a field appearing in a
hundred entries counts once.

Auxiliary identity is by name or by an explicit operator-supplied
auxiliary path. The shape inference the old draft carried is gone: a
file shaped like a translation map but with an unrecognised stem is
not auxiliary, and is not guessed into one.

File IDs are content-derived and disambiguated. Two files with the
same stem and identical bytes receive distinct file IDs (a positional
suffix is appended to the second and later occurrences), so a coverage
report never collapses a copy of an operator's corpus into one row.

Nothing about a real operator's corpus is hardcoded. The ledger is
built around paths the operator passes; the tests run on invented
fixtures; the imports stay below the level the no-personal-data guard
already polices.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.cut_map import CUT_SLOTS
from backend.source_manifest import (
    REASON_UNDECLARED,
    SOURCE_LIBRARIES,
    declaration_for,
)


# -- Status values for a file in the ledger ---------------------------------

STATUS_KNOWN_SHAPE: str = "known_shape"
STATUS_AUXILIARY: str = "auxiliary"
STATUS_MALFORMED: str = "malformed"
STATUS_UNDECLARED: str = "undeclared"
STATUS_NOT_ADOPTED: str = "not_adopted"
STATUS_UNKNOWN_SHAPE: str = "unknown_shape"
STATUS_PENDING_MAPPING: str = "pending_mapping"
STATUS_REFUSED: str = "refused"

ALL_STATUSES: tuple[str, ...] = (
    STATUS_KNOWN_SHAPE,
    STATUS_AUXILIARY,
    STATUS_MALFORMED,
    STATUS_UNDECLARED,
    STATUS_NOT_ADOPTED,
    STATUS_UNKNOWN_SHAPE,
    STATUS_PENDING_MAPPING,
    STATUS_REFUSED,
)

STATUSES_REQUIRING_REASON: frozenset[str] = frozenset(
    s for s in ALL_STATUSES
    if s not in (STATUS_KNOWN_SHAPE, STATUS_AUXILIARY)
)


# -- Field role constants. Provisional; 1.2 owns the final mapping. ---------

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

TYPE_STRING: str = "string"
TYPE_NUMBER: str = "number"
TYPE_BOOLEAN: str = "boolean"
TYPE_NULL: str = "null"
TYPE_LIST: str = "list"
TYPE_DICT: str = "dict"

ALL_TYPES: tuple[str, ...] = (
    TYPE_STRING, TYPE_NUMBER, TYPE_BOOLEAN, TYPE_NULL, TYPE_LIST, TYPE_DICT,
)

SHAPE_OBJECT: str = "object"
SHAPE_ARRAY: str = "array"
SHAPE_SCALAR: str = "scalar"
SHAPE_MALFORMED: str = "malformed"
SHAPE_EMPTY: str = "empty"


# -- Auxiliary-map identity. By name only; no shape inference. ------------

# Each entry: the safe auxiliary `kind`, the on-disk stems it matches,
# and the structural rule its body must satisfy when one is supplied.
# A file whose stem is not on the list and which the operator did not
# select as auxiliary is NOT an auxiliary map.
AUXILIARY_MAPS: tuple[dict[str, Any], ...] = (
    {
        "kind": "translation_map",
        "stems": ("translation_map",),
        "shape_test": "translation_map_shape",
        "description": "translation map keyed by source string",
    },
    {
        "kind": "cut_map",
        # The cut map's stem in the actual miner is `cuts`; `cut_map` is
        # accepted too so a renamed copy of an operator's curated file
        # is not silently reclassified.
        "stems": ("cuts", "cut_map"),
        "shape_test": "cut_map_shape",
        "description": "cut map keyed by source identifier",
    },
    {
        "kind": "mined_families",
        "stems": ("mined_families",),
        "shape_test": "mined_families_shape",
        "description": "family declaration keyed by source identifier",
    },
    {
        "kind": "mined_labels",
        "stems": ("mined_judge_labels", "mined_labels"),
        "shape_test": "mined_labels_shape",
        "description": "judge labels keyed by row key",
    },
)


# -- Provisional field role table --------------------------------------------

PROVISIONAL_FIELD_ROLES: dict[str, dict[str, Any]] = {
    "identifier": {
        "role": ROLE_IDENTIFIER,
        "evidence": (
            "asset_guard.guard_entry",
            "extractor.identifier_for",
            "importer.derive_room_key",
            "mining.identifier_for",
        ),
    },
    "id": {
        "role": ROLE_IDENTIFIER,
        "evidence": (
            "asset_guard.guard_entry",
            "extractor.identifier_for",
        ),
    },
    "key": {
        "role": ROLE_IDENTIFIER,
        "evidence": (
            "asset_guard.guard_entry",
            "extractor.identifier_for",
            "mining.identifier_for",
            "mining.validate_combination",
        ),
    },
    "library": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": (
            "asset_guard.guard_entry",
            "source_manifest.declaration_for",
        ),
    },
    "source_library": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("importer.import_source", "extractor._load_entries_from_file"),
    },
    "lib": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("asset_guard.guard_entry",),
    },
    "weight": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("importer.import_source", "importer.outlying_weights"),
    },
    "kind": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("asset_guard.guard_entry", "mining.family_for"),
    },
    "source_family": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("mining.family_for", "mining.normalise_family"),
    },
    "family": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("mining.family_for",),
    },
    "category": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("mining.family_for",),
    },
    "profile_key": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("asset_guard.guard_entry",),
    },
    "profile": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("asset_guard.guard_entry",),
    },
    "body_profile": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("asset_guard.guard_entry",),
    },
    "tags": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("asset_guard.guard_entry", "importer.derive_tags"),
    },
    "tag": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("asset_guard.guard_entry", "importer.derive_tags"),
    },
    "props": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("importer.derive_offers",),
    },
    "objects": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("importer.derive_offers",),
    },
    "furniture": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("importer.derive_offers",),
    },
    "label": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("importer._translate_field", "extractor._collect_entries"),
    },
    "name": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("extractor._collect_entries",),
    },
    "title": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("extractor._collect_entries",),
    },
    "display_name": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("extractor._collect_entries",),
    },
    "theme_text": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("importer._extract_theme_text", "asset_guard.guard_entry"),
    },
    "theme": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("importer._extract_theme_text", "asset_guard.guard_entry"),
    },
    "scene_theme": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("importer._extract_theme_text", "asset_guard.guard_entry"),
    },
    "text": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("importer._extract_theme_text", "asset_guard.guard_entry"),
    },
    "description": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("extractor._collect_entries", "asset_guard.guard_entry"),
    },
    "prompt": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("mining.split_fused_entry", "asset_guard.guard_entry"),
    },
    "notes": {
        "role": ROLE_WRITER_GUIDANCE,
        "evidence": ("importer.is_guidance_field", "asset_guard.guard_entry"),
    },
    "anchors": {
        "role": ROLE_WRITER_GUIDANCE,
        "evidence": ("importer.is_guidance_field",),
    },
    "mood": {
        "role": ROLE_WRITER_GUIDANCE,
        "evidence": ("importer.is_guidance_field",),
    },
    "uniform_fit": {
        "role": ROLE_DESCRIPTIVE_INPUT,
        "evidence": ("asset_guard.prune_options",),
    },
    "enabled": {
        "role": ROLE_SELECTION_METADATA,
        "evidence": ("room_registry.normalize_library_entry",),
    },
}

_ANCHOR_SUFFIX_ROLE: dict[str, Any] = {
    "role": ROLE_WRITER_GUIDANCE,
    "evidence": ("importer.is_guidance_field",),
}

_MOOD_PREFIX_ROLE: dict[str, Any] = {
    "role": ROLE_WRITER_GUIDANCE,
    "evidence": ("importer.is_guidance_field",),
}


# -- The ledger data classes -----------------------------------------------


def _digest_bytes(payload: bytes) -> str:
    """The sha256 of `payload` as a hex digest.

    A digest is not prose and is not an absolute path. It is the stable
    identity the ledger uses to tell two files sharing a stem apart, and
    to tell a re-import of the same file from a changed one.
    """
    return hashlib.sha256(payload).hexdigest()


def _short_digest(digest: str, length: int = 8) -> str:
    """The prefix of `digest` that participates in the file_id.

    Eight hex chars carry 32 bits of entropy, enough to disambiguate
    the operator's corpus without lengthening the file_id past the
    disambiguator's reach.
    """
    return digest[:length] if digest else ""


@dataclass(frozen=True)
class FieldObservation:
    """One field, as it appeared in a discovered file.

    `name` is the field's own name. `structural_type` is one of the
    `TYPE_*` constants and is read off the value's Python type, never
    the value itself. `role` is the provisional role from
    `PROVISIONAL_FIELD_ROLES`, or `ROLE_UNUSED` for a field the
    inventory has no evidence for. `consumer_evidence` is the tuple of
    safe `module.function` references that read this field. `notes` is
    a structural note for cases the field name alone cannot describe -
    e.g. `_anchor` suffix and `mood_` prefix.
    """

    name: str
    structural_type: str
    role: str
    consumer_evidence: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True)
class EntryShape:
    """A distinct entry shape the walk saw in the file.

    `path` is the structural locator (`root[0]`, `items[2]`, `props`)
    of the first entry with this shape; later entries of the same
    shape do not contribute a new `EntryShape`. `top_level_keys` is
    the sorted list of field names observed at the entry's top level.
    No values reach the ledger; if a value's type matters, it is in
    `field_types` keyed by the field name.
    """

    path: str
    top_level_keys: tuple[str, ...]
    field_types: dict[str, str]


@dataclass(frozen=True)
class FileEntry:
    """One file in the coverage ledger.

    `file_id` is a safe identifier - the file stem plus a content-digest
    prefix plus, when needed, a positional disambiguator - that does
    not include the absolute path. `top_level_shape` is one of
    `SHAPE_*`. `declared_kind` is the manifest's kind for the file's
    stem, or `""` for an undeclared file. `status` is one of the
    `STATUS_*` constants. `reason` is non-empty whenever `status` is in
    `STATUSES_REQUIRING_REASON`. `auxiliary_kind` is the kind from
    `AUXILIARY_MAPS` for an auxiliary file, or `""` for a scene.
    `entry_count` is the number of distinct entry shapes the walk
    reached. `entry_shapes` is the deduplicated set of those shapes.
    `field_observations` is the deduplicated set of fields the
    inventory saw across the file, with their structural type, role
    and consumer evidence. `structural_notes` describes patterns the
    walk detected that are not standard entry lists - keyed record
    collections, scalar keyed collections, list-of-scalars containers
    - so a coverage report is not silently reduced when a file's
    shape does not match the manifest's standard layout.
    """

    file_id: str
    file_stem: str
    content_digest: str
    top_level_shape: str
    declared_kind: str
    status: str
    reason: str
    auxiliary: bool
    auxiliary_kind: str
    entry_count: int
    entry_shapes: tuple[EntryShape, ...]
    field_observations: tuple[FieldObservation, ...]
    structural_notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "file_stem": self.file_stem,
            "content_digest": self.content_digest,
            "top_level_shape": self.top_level_shape,
            "declared_kind": self.declared_kind,
            "status": self.status,
            "reason": self.reason,
            "auxiliary": self.auxiliary,
            "auxiliary_kind": self.auxiliary_kind,
            "entry_count": self.entry_count,
            "entry_shapes": [asdict(s) for s in self.entry_shapes],
            "field_observations": [asdict(f) for f in self.field_observations],
            "structural_notes": list(self.structural_notes),
        }


@dataclass(frozen=True)
class CoverageLedger:
    """The whole coverage report.

    Built by `inventory_source_dir`. Written with `save_ledger` to an
    untracked location beside the imported seed files; never to a tracked
    path, because the ledger is a record of operator-corpus coverage and
    has no business in the public repo.
    """

    entries: tuple[FileEntry, ...]
    source_dir: str  # a label, not an absolute path: only the directory name
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_dir": self.source_dir,
            "generated_at": self.generated_at,
            "entries": [e.to_dict() for e in self.entries],
            "totals": _totals(self.entries),
        }

    def by_status(self) -> dict[str, int]:
        return _totals(self.entries)

    def file_ids(self) -> tuple[str, ...]:
        return tuple(e.file_id for e in self.entries)


def _totals(entries: Iterable[FileEntry]) -> dict[str, int]:
    counts: dict[str, int] = {status: 0 for status in ALL_STATUSES}
    for entry in entries:
        counts[entry.status] = counts.get(entry.status, 0) + 1
    counts["__total__"] = sum(counts.values())
    return counts


# -- Helpers used by the inventory ------------------------------------------


def _structural_type(value: Any) -> str:
    """The structural type of a value, with no prose retained."""
    if isinstance(value, bool):
        return TYPE_BOOLEAN
    if isinstance(value, (int, float)):
        return TYPE_NUMBER
    if isinstance(value, str):
        return TYPE_STRING
    if value is None:
        return TYPE_NULL
    if isinstance(value, list):
        return TYPE_LIST
    if isinstance(value, dict):
        return TYPE_DICT
    return TYPE_STRING


def _top_level_shape(data: Any) -> str:
    """The shape of the file as a whole, not its content."""
    if data is None:
        return SHAPE_EMPTY
    if isinstance(data, dict):
        return SHAPE_OBJECT
    if isinstance(data, list):
        return SHAPE_ARRAY
    return SHAPE_SCALAR


def _entry_marker_present(node: dict[str, Any]) -> bool:
    """True when a dict carries any of the extractor's entry-marker keys."""
    from backend.extractor import ENTRY_MARKER_KEYS
    return any(k in node for k in ENTRY_MARKER_KEYS)


def _walk_entries(
    data: Any,
) -> tuple[list[EntryShape], int, list[tuple[str, str]]]:
    """Walk a parsed JSON value, recording every entry shape it contains.

    No cap. A field first appearing in entry nine is recorded; a shape
    first appearing in entry ninety-nine is recorded. The function
    descends into containers the extractor's `_collect_entries` does
    not recognise as entries (dicts without marker keys are walked
    key by key; lists of mixed types are walked index by index).

    Two entries of the same shape produce one `EntryShape` and count
    toward `entry_count` only once. The walk is structural and never
    retains values; field types come from `type(value)`, never the
    value itself.

    Three structural patterns get special handling so a coverage
    report is not silently reduced to an empty observation when the
    source's own layout does not use entry-marker keys:

    1. **Keyed record collection.** A dict whose values are all dicts
       is treated as a collection of records keyed by an identity
       string. The OUTER keys are not emitted anywhere — they are the
       part of the data most likely to carry personal or profile
       identifiers. The INNER schema (the keys every value shares) is
       recorded as one `EntryShape`, with the path using the
       placeholder `<id-key>` instead of any real key.

    2. **Scalar keyed collection.** A dict whose values are all
       scalars is recorded as one empty `EntryShape` with a
       structural note, again with the placeholder `<id-key>`.

    3. **Mixed / unknown container.** The previous behaviour: descend
       key by key or index by index, recording entries where marker
       keys are present.

    Returns a 3-tuple: `(entry_shapes, visited, structural_notes)`
    where each note is `(path, pattern)` for the patterns the walk
    detected.
    """
    shapes_by_key: dict[
        tuple[tuple[str, ...], tuple[tuple[str, str], ...]], EntryShape
    ] = {}
    visited = 0
    notes: list[tuple[str, str]] = []
    queue: list[tuple[str, Any]] = [("root", data)]
    while queue:
        path, node = queue.pop(0)
        if isinstance(node, dict):
            if _entry_marker_present(node):
                shape = _entry_shape_from_dict(path, node)
                _record_shape(shapes_by_key, shape)
                visited += 1
                continue
            shape = _keyed_record_collection_shape(path, node)
            if shape is not None:
                _record_shape(shapes_by_key, shape)
                notes.append((path, "keyed_record_collection"))
                visited += 1
                continue
            shape = _list_keyed_collection_shape(path, node)
            if shape is not None:
                _record_shape(shapes_by_key, shape)
                notes.append((path, "list_keyed_collection"))
                visited += 1
                continue
            if _is_scalar_keyed_collection(node):
                shape = EntryShape(
                    path=f"{path}.<id-key>",
                    top_level_keys=(),
                    field_types={},
                )
                _record_shape(shapes_by_key, shape)
                notes.append((path, "scalar_keyed_collection"))
                continue
            # Mixed / unknown container: descend.
            for key in sorted(node.keys()):
                queue.append((f"{path}.{key}" if path != "root" else key, node[key]))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                queue.append((f"{path}[{index}]", item))
    return list(shapes_by_key.values()), visited, notes


def _entry_shape_from_dict(path: str, node: dict[str, Any]) -> EntryShape:
    """Build an `EntryShape` from a dict the walk treats as an entry."""
    keys = tuple(sorted(node.keys()))
    field_types = tuple(sorted(
        (k, _structural_type(node[k])) for k in keys
    ))
    return EntryShape(
        path=path,
        top_level_keys=keys,
        field_types=dict(field_types),
    )


def _keyed_record_collection_shape(
    path: str, node: dict[str, Any]
) -> EntryShape | None:
    """Build the `EntryShape` for a dict-of-records container, or None.

    A dict is a keyed record collection when:
    - it has at least one entry;
    - every value is a dict;
    - the values' key sets share at least one key (so a non-empty
      inner schema exists).

    Returns the `EntryShape` whose `top_level_keys` is the SHARED key
    set and whose `path` is `<path>.<id-key>`, with the placeholder
    standing in for the real outer key. Returns `None` when the
    container has no shared schema (e.g. one of the values is an
    empty dict, or the values' key sets are disjoint) — the caller
    then falls through to a descent so the walk does not silently
    lose information.

    The path uses the placeholder `<id-key>` because the real outer
    keys are the part of the source most likely to carry personal
    or profile identifiers, and the requirement is to never emit
    them.
    """
    if not node:
        return None
    value_key_sets = [set(v.keys()) for v in node.values() if isinstance(v, dict)]
    if not value_key_sets:
        return None
    if not all(isinstance(v, dict) for v in node.values()):
        return None
    shared = value_key_sets[0]
    for ks in value_key_sets[1:]:
        shared = shared & ks
    if not shared:
        return None
    first = next(iter(node.values()))
    keys = tuple(sorted(shared))
    field_types = tuple(
        sorted((k, _structural_type(first[k])) for k in keys)
    )
    return EntryShape(
        path=f"{path}.<id-key>",
        top_level_keys=keys,
        field_types=dict(field_types),
    )


def _record_shape(
    shapes_by_key: dict[
        tuple[tuple[str, ...], tuple[tuple[str, str], ...]], EntryShape
    ],
    shape: EntryShape,
) -> None:
    """Insert `shape` into `shapes_by_key` if not already present."""
    key = (shape.top_level_keys, tuple(sorted(shape.field_types.items())))
    if key not in shapes_by_key:
        shapes_by_key[key] = shape


def _is_scalar_keyed_collection(node: dict[str, Any]) -> bool:
    """True when `node` is a dict whose values are all scalars.

    The OUTER keys are identity-like (e.g. profile names, personal
    names) and are never emitted. The inner values are scalars, so
    the ledger records the container as an empty `EntryShape` with
    a structural note rather than guessing a schema.

    A small fixed-schema dict (two or three keys, all scalars) is
    not a keyed collection; it is a config record, and the walk
    descends to record its fields. The three-key floor matches
    the keyed-record and list-keyed thresholds: a single
    threshold means one rule across all three patterns.
    """
    if len(node) < 3:
        return False
    for v in node.values():
        if isinstance(v, (str, int, float, bool)) or v is None:
            continue
        return False
    return True


def _list_keyed_collection_shape(
    path: str, node: dict[str, Any]
) -> EntryShape | None:
    """Build the `EntryShape` for a dict whose values are all lists.

    A dict whose values are uniformly lists is treated as a keyed
    list collection: each outer key is identity-like and is never
    emitted. The recorded schema is a single synthetic field
    `value` whose structural type is the shared element type
    (`string` for `[str, ...]`, `dict` for `[record, ...]`, etc.).

    Returns `None` when:
    - `node` has fewer than three keys (a small fixed-schema dict
      like `{"required": [...], "optional": [...]}` is a config
      record, not a keyed collection);
    - the values are not all lists;
    - the values are empty lists with no element type to report.

    The two-key floor is what separates the operator's
    `entry_schema` (a config-style record the manifest's source
    libraries actually use to declare their shape) from the
    operator's `celebrities.z` (a keyed collection of identity
    strings, which the structural coverage report must name
    without exposing the keys).
    """
    if len(node) < 3:
        return None
    if not all(isinstance(v, list) for v in node.values()):
        return None
    first = next(iter(node.values()))
    if not first:
        element_type = TYPE_STRING
    else:
        element_type = _structural_type(first[0])
    return EntryShape(
        path=f"{path}.<id-key>",
        top_level_keys=("value",),
        field_types={"value": element_type},
    )


def _format_structural_notes(
    notes: list[tuple[str, str]],
) -> tuple[str, ...]:
    """Format a list of (path, pattern) into a sorted, deduped tuple of strings."""
    seen: set[str] = set()
    out: list[str] = []
    for path, pattern in sorted(notes):
        text = f"{pattern}:{path}"
        if text not in seen:
            seen.add(text)
            out.append(text)
    return tuple(out)


def _field_observations(shapes: Iterable[EntryShape]) -> tuple[FieldObservation, ...]:
    """The deduplicated set of field observations across `shapes`.

    A field is recorded once per `(name, structural_type)` pair. A
    field observed as both `string` and `list` across the file keeps
    both observations: the source is genuinely polymorphic, and the
    ledger records what was seen.
    """
    by_pair: dict[tuple[str, str], FieldObservation] = {}
    for shape in shapes:
        for field_name, field_type in shape.field_types.items():
            role_info = _role_for_field(field_name)
            obs = FieldObservation(
                name=field_name,
                structural_type=field_type,
                role=role_info["role"],
                consumer_evidence=tuple(role_info["evidence"]),
                notes=str(role_info.get("notes", "")),
            )
            by_pair[(field_name, field_type)] = obs
    return tuple(by_pair[k] for k in sorted(by_pair))


def _role_for_field(name: str) -> dict[str, Any]:
    """The provisional role for `name`, with consumer evidence."""
    if name in PROVISIONAL_FIELD_ROLES:
        info = dict(PROVISIONAL_FIELD_ROLES[name])
        info.setdefault("notes", "table match")
        return info
    if name.endswith("_anchor"):
        return {**_ANCHOR_SUFFIX_ROLE, "notes": "name suffix rule"}
    if name.startswith("mood_"):
        return {**_MOOD_PREFIX_ROLE, "notes": "name prefix rule"}
    return {"role": ROLE_UNUSED, "evidence": (), "notes": "no consumer evidence"}


# -- Auxiliary classification: by name or by operator selection only --------


def _auxiliary_kind_for_stem(stem: str) -> dict[str, Any] | None:
    """The auxiliary-map spec for `stem`, by stem only.

    No shape inference. A file whose stem is not on the list and
    which the operator did not explicitly select as auxiliary is not
    an auxiliary map. The review named this: shape inference is a
    guess, and a guess in the auxiliary path is the wrong direction
    because it would silently reclassify operator material.
    """
    lowered = stem.lower()
    for spec in AUXILIARY_MAPS:
        for allowed in spec["stems"]:
            if lowered == allowed:
                return spec
    return None


def _auxiliary_spec_for_kind(kind: str) -> dict[str, Any] | None:
    """The auxiliary-map spec for an operator-supplied `kind`."""
    for spec in AUXILIARY_MAPS:
        if spec["kind"] == kind:
            return spec
    return None


def _is_translation_map_shape(data: Any) -> bool:
    """True when `data` is shaped like the translation map.

    The translation map is a dict whose values are themselves dicts
    carrying `source`, `translation` and `fields` keys. Used only as
    a structural check AFTER a file is named as a translation map
    (by stem or by operator selection); never as a way to GUESS
    a file's identity.
    """
    if not isinstance(data, dict) or not data:
        return False
    for value in data.values():
        if not isinstance(value, dict):
            return False
        if {"source", "translation", "fields"} - value.keys():
            return False
    return True


def _is_cut_map_shape(data: Any) -> bool:
    """True when `data` is shaped like the cut map.

    The cut map is a dict whose values are dicts whose keys are all
    from `CUT_SLOTS` (or absent). The slot values are non-empty
    strings when present.
    """
    if not isinstance(data, dict) or not data:
        return False
    for value in data.values():
        if not isinstance(value, dict):
            return False
        for slot_key, slot_value in value.items():
            if slot_key not in CUT_SLOTS:
                return False
            if slot_value is not None and not (
                isinstance(slot_value, str) and slot_value
            ):
                return False
    return True


def _is_mined_families_shape(data: Any) -> bool:
    """True when `data` is shaped like the family declaration."""
    if not isinstance(data, dict) or not data:
        return False
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            return False
        if not isinstance(value, str) or not value:
            return False
    return True


def _is_mined_labels_shape(data: Any) -> bool:
    """True when `data` is shaped like the judge labels."""
    return _is_mined_families_shape(data)


def _auxiliary_shape_matches(rule: str, data: Any) -> bool:
    """The auxiliary structural rule for `data`."""
    if rule == "translation_map_shape":
        return _is_translation_map_shape(data)
    if rule == "cut_map_shape":
        return _is_cut_map_shape(data)
    if rule == "mined_families_shape":
        return _is_mined_families_shape(data)
    if rule == "mined_labels_shape":
        return _is_mined_labels_shape(data)
    return False


# -- Single-file inventory --------------------------------------------------


def _empty_entry(stem: str, reason: str, *, declared_kind: str = "",
                 content_digest: str = "") -> FileEntry:
    """An empty-observation entry, used for the malformed and unreadable paths."""
    file_id = f"{stem}-{_short_digest(content_digest)}" if content_digest else f"{stem}-missing"
    return FileEntry(
        file_id=file_id,
        file_stem=stem,
        content_digest=content_digest,
        top_level_shape=SHAPE_MALFORMED,
        declared_kind=declared_kind,
        status=STATUS_MALFORMED,
        reason=reason,
        auxiliary=False,
        auxiliary_kind="",
        entry_count=0,
        entry_shapes=(),
        field_observations=(),
    )


def inventory_file(
    file_path: Path | str,
    *,
    aux_kind: str = "",
) -> FileEntry:
    """Inventory a single file and return its `FileEntry`.

    `aux_kind`, when non-empty, marks this file as operator-selected
    auxiliary and overrides stem-based classification. The kind must
    be one of the documented auxiliary kinds (translation_map,
    cut_map, mined_families, mined_labels). A kind outside that set
    is reported as malformed: an operator selection that names a kind
    the inventory has no rule for is named, not silently mapped to
    a scene.

    The walk is whole-file: a field first appearing in entry nine is
    recorded, and the field-observation set is the deduplicated union
    across every entry the walk reached.

    A file the inventory cannot read - a directory passed where a
    file is wanted, an unreadable file, an invalid JSON document - is
    reported as `malformed` with a reason that names the failure.
    The ledger never silently drops a file: the inventory either
    records what it saw, or it reports why it could not.
    """
    if not isinstance(file_path, Path):
        file_path = Path(file_path)
    stem = file_path.stem.strip().lower()
    if not stem:
        return _empty_entry("", "file has no usable stem")

    if not file_path.is_file():
        return _empty_entry(
            stem,
            f"path is not a regular file: {file_path.name}",
            declared_kind=_declared_kind(stem),
        )

    try:
        payload = file_path.read_bytes()
    except OSError as exc:
        return _empty_entry(
            stem,
            f"file could not be read: {exc.__class__.__name__}",
            declared_kind=_declared_kind(stem),
        )

    digest = _digest_bytes(payload)
    file_id = f"{stem}-{_short_digest(digest)}"

    if not payload.strip():
        return FileEntry(
            file_id=file_id,
            file_stem=stem,
            content_digest=digest,
            top_level_shape=SHAPE_EMPTY,
            declared_kind=_declared_kind(stem),
            status=STATUS_PENDING_MAPPING,
            reason="file is empty: nothing to inventory",
            auxiliary=False,
            auxiliary_kind="",
            entry_count=0,
            entry_shapes=(),
            field_observations=(),
        )

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return FileEntry(
            file_id=file_id,
            file_stem=stem,
            content_digest=digest,
            top_level_shape=SHAPE_MALFORMED,
            declared_kind=_declared_kind(stem),
            status=STATUS_MALFORMED,
            reason=f"invalid JSON or encoding: {exc.__class__.__name__}",
            auxiliary=False,
            auxiliary_kind="",
            entry_count=0,
            entry_shapes=(),
            field_observations=(),
        )

    return _build_file_entry(
        stem=stem, file_id=file_id, digest=digest, data=data, aux_kind=aux_kind,
    )


def _declared_kind(stem: str) -> str:
    """The manifest kind for `stem`, or `""` when undeclared."""
    declaration = declaration_for(stem)
    if declaration is None:
        return ""
    return str(declaration.get("kind", ""))


def _build_file_entry(
    *,
    stem: str,
    file_id: str,
    digest: str,
    data: Any,
    aux_kind: str,
) -> FileEntry:
    """Assemble the `FileEntry` for a parsed JSON value.

    The reasoning order:
    1. operator-selected auxiliary (by `aux_kind`) wins over everything;
    2. stem-based auxiliary classification for the curated maps;
    3. declared library classification;
    4. undeclared / not adopted / unknown / pending for the rest.

    Every non-OK status gets a non-empty `reason`; KNOWN_SHAPE and
    AUXILIARY do not need one, but the field is always present (empty
    string for OK), so readers can index it without a guard.
    """
    shape = _top_level_shape(data)

    # 1. Operator selection: only the named auxiliary kinds count. An
    # operator pass that names a kind outside the documented set is
    # reported as malformed, not silently mapped to a scene.
    if aux_kind:
        spec = _auxiliary_spec_for_kind(aux_kind)
        if spec is None:
            return FileEntry(
                file_id=file_id,
                file_stem=stem,
                content_digest=digest,
                top_level_shape=shape,
                declared_kind="",
                status=STATUS_MALFORMED,
                reason=(
                    f"operator-selected auxiliary kind {aux_kind!r} is not in "
                    f"the documented set; named, not guessed"
                ),
                auxiliary=True,
                auxiliary_kind=aux_kind,
                entry_count=0,
                entry_shapes=(),
                field_observations=(),
            )
        return _auxiliary_entry(
            stem=stem, file_id=file_id, digest=digest, data=data, spec=spec
        )

    # 2. Stem-based auxiliary classification. By name only.
    aux_spec = _auxiliary_kind_for_stem(stem)
    if aux_spec is not None:
        return _auxiliary_entry(
            stem=stem, file_id=file_id, digest=digest, data=data, spec=aux_spec
        )

    # 3. Declared library classification.
    declaration = declaration_for(stem)
    if declaration is None:
        # Even an undeclared file gets a structural schema recorded
        # so the operator can see what the source actually contains
        # before they adopt it. An empty observation here would be
        # a silent reduction of coverage.
        shapes, visited, notes = _walk_entries(data)
        observations = _field_observations(shapes)
        return FileEntry(
            file_id=file_id,
            file_stem=stem,
            content_digest=digest,
            top_level_shape=shape,
            declared_kind="",
            status=STATUS_UNDECLARED,
            reason=(
                f"file stem {stem!r} has no declaration in the source manifest"
            ),
            auxiliary=False,
            auxiliary_kind="",
            entry_count=visited,
            entry_shapes=shapes,
            field_observations=observations,
            structural_notes=_format_structural_notes(notes),
        )

    declared_kind = str(declaration.get("kind", ""))
    if not declaration.get("destinations"):
        # Even files this project does not adopt get a structural
        # schema recorded. The operator needs to see what the file
        # actually contains before they decide to adopt it; an empty
        # observation here would be a silent reduction of coverage.
        shapes, visited, notes = _walk_entries(data)
        observations = _field_observations(shapes)
        return FileEntry(
            file_id=file_id,
            file_stem=stem,
            content_digest=digest,
            top_level_shape=shape,
            declared_kind=declared_kind,
            status=STATUS_NOT_ADOPTED,
            reason=str(declaration.get("reason", "library reaches no destination")),
            auxiliary=False,
            auxiliary_kind="",
            entry_count=visited,
            entry_shapes=shapes,
            field_observations=observations,
            structural_notes=_format_structural_notes(notes),
        )

    if shape not in (SHAPE_OBJECT, SHAPE_ARRAY, SHAPE_EMPTY):
        return FileEntry(
            file_id=file_id,
            file_stem=stem,
            content_digest=digest,
            top_level_shape=shape,
            declared_kind=declared_kind,
            status=STATUS_UNKNOWN_SHAPE,
            reason=(
                f"top-level shape {shape!r} is not a recognised source shape"
            ),
            auxiliary=False,
            auxiliary_kind="",
            entry_count=0,
            entry_shapes=(),
            field_observations=(),
        )

    if shape == SHAPE_EMPTY:
        return FileEntry(
            file_id=file_id,
            file_stem=stem,
            content_digest=digest,
            top_level_shape=shape,
            declared_kind=declared_kind,
            status=STATUS_PENDING_MAPPING,
            reason="file is empty: nothing to inventory",
            auxiliary=False,
            auxiliary_kind="",
            entry_count=0,
            entry_shapes=(),
            field_observations=(),
        )

    # Whole-file walk. No cap. Every distinct entry shape is recorded.
    shapes, visited, notes = _walk_entries(data)
    observations = _field_observations(shapes)

    return FileEntry(
        file_id=file_id,
        file_stem=stem,
        content_digest=digest,
        top_level_shape=shape,
        declared_kind=declared_kind,
        status=STATUS_KNOWN_SHAPE,
        reason="",
        auxiliary=False,
        auxiliary_kind="",
        entry_count=visited,
        entry_shapes=shapes,
        field_observations=observations,
        structural_notes=_format_structural_notes(notes),
    )


def _auxiliary_entry(
    *,
    stem: str,
    file_id: str,
    digest: str,
    data: Any,
    spec: dict[str, Any],
) -> FileEntry:
    """Inventory an auxiliary map.

    Auxiliary maps are never scenes, and they are never refused: the
    inventory records them with their auxiliary kind and a shape
    derived from the kind's structural rule. A auxiliary file whose
    shape does not match its declared structural rule is reported as
    `malformed` with a reason, so a translation map carrying the wrong
    key set is named rather than guessed into a scene.
    """
    aux_kind = str(spec.get("kind", ""))
    shape = _top_level_shape(data)
    structural_rule = str(spec.get("shape_test", ""))
    if not _auxiliary_shape_matches(structural_rule, data):
        return FileEntry(
            file_id=file_id,
            file_stem=stem,
            content_digest=digest,
            top_level_shape=shape,
            declared_kind="",
            status=STATUS_MALFORMED,
            reason=(
                f"auxiliary map kind {aux_kind!r} expected shape {structural_rule!r}, "
                f"got {shape!r}"
            ),
            auxiliary=True,
            auxiliary_kind=aux_kind,
            entry_count=0,
            entry_shapes=(),
            field_observations=(),
        )

    shapes, visited, notes = _walk_entries(data)
    observations = _field_observations(shapes)
    return FileEntry(
        file_id=file_id,
        file_stem=stem,
        content_digest=digest,
        top_level_shape=shape,
        declared_kind="",
        status=STATUS_AUXILIARY,
        reason="",
        auxiliary=True,
        auxiliary_kind=aux_kind,
        entry_count=visited,
        entry_shapes=shapes,
        field_observations=observations,
        structural_notes=_format_structural_notes(notes),
    )


# -- Whole-directory inventory --------------------------------------------


def _safe_resolve(path: Path) -> Path:
    """Resolve a path to its canonical absolute form.

    The result is what the OS sees as "the same file", regardless of
    whether the caller passed a relative or an absolute path, with
    redundant separators, with `..` segments, or under a different
    working directory. Two paths that resolve to the same value are
    the same physical file from the inventory's point of view.

    A path that does not exist on disk is still resolvable: the
    canonical form is computed lexically against the current working
    directory, with symlinks left alone (Python's `resolve()` does not
    require the target to exist when `strict=False`, the default).
    This matters because a malformed or missing auxiliary file still
    needs a stable identity for the dedup set; refusing to resolve
    a non-existing path would split the same logical reference into
    two entries by absolute-vs-relative spelling.
    """
    try:
        return path.resolve()
    except (OSError, ValueError):
        # `resolve()` can raise on some platforms when the path is
        # unresolvable for a reason other than "does not exist".
        # `absolute()` covers the relative-path case; it does not
        # collapse `..` segments, but it makes the path canonical
        # against the current working directory, which is enough for
        # dedup purposes.
        return path.absolute()


def _disambiguate_file_ids(
    entries: list[FileEntry],
) -> list[FileEntry]:
    """Make every file_id unique across the entries.

    Two entries collide on identity when they share `file_stem` and
    `content_digest`. The first-seen entry keeps its content-derived
    `file_id`; the second and later occurrences receive a positional
    suffix (`-2`, `-3`, ...). The disambiguator is content-stable
    (a second pass with the same files produces the same ids) and
    positional (a different walk order produces different suffixes),
    so the operator can re-run the inventory and find the same file
    under the same id, while a copy of the same file is named
    distinctly the first time.
    """
    seen: dict[tuple[str, str], int] = {}
    out: list[FileEntry] = []
    for entry in entries:
        key = (entry.file_stem, entry.content_digest)
        count = seen.get(key, 0) + 1
        seen[key] = count
        if count == 1:
            out.append(entry)
            continue
        new_id = f"{entry.file_id}-{count}"
        out.append(replace(entry, file_id=new_id))
    return out


def _dedupe_selections(
    selections: list[tuple[Path, str]],
) -> list[tuple[Path, str]]:
    """Drop duplicate (path, aux_kind) selections by canonical path.

    The same physical file referenced from `source_dir` and again
    from `aux_paths` gets one entry. The first selection wins; later
    duplicates are dropped, so the operator cannot accidentally
    double-count by naming a file in both lists, or by writing the
    same path with different relative/absolute spellings.

    `selections` is expected to already be in canonical form
    (every `Path` already resolved by `_safe_resolve`); the dedup set
    is a set of `Path` values, which compares by string equality
    after `resolve()`. The test
    `test_relative_source_dir_with_absolute_aux_path_dedupes` and its
    mirror prove the two spellings collapse into one entry.
    """
    seen: set[Path] = set()
    out: list[tuple[Path, str]] = []
    for path, aux_kind in selections:
        if path in seen:
            continue
        seen.add(path)
        out.append((path, aux_kind))
    return out


def inventory_source_dir(
    source_dir: Path | str,
    *,
    aux_paths: tuple[Path | str, ...] = (),
    aux_kinds: Mapping[Path | str, str] | None = None,
) -> CoverageLedger:
    """Inventory every JSON file in `source_dir` and `aux_paths`.

    `source_dir` is the operator-selected source directory: every
    `*.json` under it is walked and inventoried. `aux_paths` are
    additional files the operator names explicitly, each receiving one
    `FileEntry` regardless of whether they sit beside the source
    material or somewhere else entirely. `aux_kinds` maps an aux path
    to its auxiliary kind (`translation_map`, `cut_map`,
    `mined_families`, `mined_labels`); the path is then classified
    as auxiliary by operator selection, not by shape guess.

    Paths are canonicalised before any comparison: a file referenced
    from `source_dir` as a relative path and again from `aux_paths`
    as an absolute path resolves to the same canonical form, and the
    file is inventoried exactly once. Symlinks are resolved; `..`
    segments are collapsed; the current working directory is fixed
    at the start of the call so a later `chdir` cannot change the
    answer.

    The same file named twice (e.g. in both lists) gets one entry.
    File IDs are content-derived, then disambiguated: two files
    sharing a stem and identical bytes get distinct ids so a copy of
    an operator's corpus never collapses into one row.

    A non-directory `source_dir` raises `NotADirectoryError` so a
    caller passing the wrong path learns at the call, not at the
    first file's read.
    """
    if source_dir is None or not str(source_dir).strip():
        raise ValueError("source_dir is required and cannot be empty")
    target_src = Path(source_dir)
    if not target_src.is_dir():
        raise NotADirectoryError(
            f"source_dir must be an existing directory, got {target_src}"
        )
    target = _safe_resolve(target_src)

    aux_kinds_map: dict[Path, str] = {}
    if aux_kinds:
        for key, value in aux_kinds.items():
            aux_kinds_map[_safe_resolve(Path(key))] = str(value)

    selections: list[tuple[Path, str]] = []
    for p in sorted(target.rglob("*.json")):
        if p.is_file():
            selections.append((_safe_resolve(p), ""))
    for ap in aux_paths:
        p = _safe_resolve(Path(ap))
        kind = aux_kinds_map.get(p, "")
        selections.append((p, kind))

    selections = _dedupe_selections(selections)
    entries = [inventory_file(p, aux_kind=aux_kind) for p, aux_kind in selections]
    entries = _disambiguate_file_ids(entries)

    return CoverageLedger(
        entries=tuple(entries),
        source_dir=target.name,
        generated_at="",
    )


def save_ledger(ledger: CoverageLedger, path: Path | str) -> Path:
    """Write `ledger` to `path` as JSON and return the path written.

    The destination is the caller's responsibility: a tracked path is
    a publication of an operator's coverage report, and that is not
    something this code does on its own. The tests use `tmp_path`; an
    operator run uses an untracked location beside the data directory.
    """
    target = Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    serialised = json.dumps(ledger.to_dict(), ensure_ascii=True, indent=2) + "\n"
    target.write_text(serialised, encoding="utf-8")
    return target


# A small, public bridge to the translation-map's own shape test, so the
# ledger and the importer never disagree on what a translation map is.
def _translation_map_shape(data: Any) -> bool:
    return _is_translation_map_shape(data)


__all__ = [
    "ALL_ROLES",
    "ALL_STATUSES",
    "ALL_TYPES",
    "AUXILIARY_MAPS",
    "CoverageLedger",
    "EntryShape",
    "FieldObservation",
    "FileEntry",
    "PROVISIONAL_FIELD_ROLES",
    "ROLE_AUXILIARY_DATA",
    "ROLE_DESCRIPTIVE_INPUT",
    "ROLE_IDENTIFIER",
    "ROLE_SELECTION_METADATA",
    "ROLE_UNUSED",
    "ROLE_WRITER_GUIDANCE",
    "SHAPE_ARRAY",
    "SHAPE_EMPTY",
    "SHAPE_MALFORMED",
    "SHAPE_OBJECT",
    "SHAPE_SCALAR",
    "STATUS_AUXILIARY",
    "STATUS_KNOWN_SHAPE",
    "STATUS_MALFORMED",
    "STATUS_NOT_ADOPTED",
    "STATUS_PENDING_MAPPING",
    "STATUS_REFUSED",
    "STATUS_UNDECLARED",
    "STATUS_UNKNOWN_SHAPE",
    "STATUSES_REQUIRING_REASON",
    "TYPE_BOOLEAN",
    "TYPE_DICT",
    "TYPE_LIST",
    "TYPE_NULL",
    "TYPE_NUMBER",
    "TYPE_STRING",
    "inventory_file",
    "inventory_source_dir",
    "save_ledger",
]
