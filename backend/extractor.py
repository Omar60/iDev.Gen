"""Extractor for non-English strings in asset source directories.

Lists every non-English string in the accepted entries of a source directory
given as a required argument, running the guard first so refused entries
are never listed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.asset_guard import guard_entries
from backend.translation_map import contains_non_english

# Metadata fields on asset entries that identify or configure the entry
# rather than containing authored text or translation candidates.
METADATA_FIELDS: frozenset[str] = frozenset(
    (
        "identifier",
        "id",
        "key",
        "library",
        "source_library",
        "lib",
        "kind",
        "weight",
        "enabled",
    )
)


# Keys whose presence marks a dict as an asset entry rather than a container
# holding entries. A container is descended into so that every entry inside it
# reaches the guard on its own. Collapsing a file into a single entry is what
# lets a refused entry ride through as a nested field of an accepted one:
# amateurs.json carries its minor-coded profiles under a "profiles" key, and
# read as one entry the guard never sees a single profile key.
ENTRY_MARKER_KEYS: frozenset[str] = frozenset(
    (
        "identifier",
        "id",
        "key",
        "label",
        "name",
        "display_name",
        "title",
        "scene_theme",
        "theme",
        "theme_text",
        "text",
        "description",
    )
)

# The keys an entry may carry its own identifier in.
IDENTIFIER_KEYS: tuple[str, ...] = ("identifier", "id", "key")


def _extract_strings_from_value(
    val: Any,
    field_name: str,
    identifier: str,
) -> list[dict[str, str]]:
    """Recursively extract non-English strings from a field value."""
    results: list[dict[str, str]] = []
    if isinstance(val, str):
        if contains_non_english(val):
            results.append({
                "identifier": identifier,
                "field": field_name,
                "string": val,
            })
    elif isinstance(val, (list, tuple, set)):
        for item in val:
            if isinstance(item, str):
                if contains_non_english(item):
                    results.append({
                        "identifier": identifier,
                        "field": field_name,
                        "string": item,
                    })
            elif isinstance(item, dict):
                for sub_key in sorted(item.keys()):
                    sub_val = item[sub_key]
                    sub_field = f"{field_name}.{sub_key}" if field_name else sub_key
                    results.extend(_extract_strings_from_value(sub_val, sub_field, identifier))
    elif isinstance(val, dict):
        for sub_key in sorted(val.keys()):
            sub_val = val[sub_key]
            sub_field = f"{field_name}.{sub_key}" if field_name else sub_key
            results.extend(_extract_strings_from_value(sub_val, sub_field, identifier))
    return results


def _holds_a_collection(node: dict[str, Any]) -> bool:
    """True when a dict carries a list of dicts or a dict of dicts."""
    for val in node.values():
        if isinstance(val, list) and any(isinstance(x, dict) for x in val):
            return True
        if isinstance(val, dict) and val and all(isinstance(x, dict) for x in val.values()):
            return True
    return False


def _collect_entries(
    node: Any,
    path: list[str],
    entries: list[dict[str, Any]],
    is_root: bool = False,
) -> None:
    """Split a JSON node into entries, descending containers until an entry.

    A dict carrying one of ENTRY_MARKER_KEYS is an entry and is taken whole:
    its own nested dicts and lists stay fields on it, so a field path like
    'anchors.camera' survives. A dict carrying none of them is a container and
    is descended into, one entry per key, so each entry is guarded on its own.
    Scalar values sitting on a container become entries of their own rather
    than being dropped, one per string, so the guard reads each on its own.

    A file's root dict is never an entry when it holds a collection: the
    libraries here write a file-level 'description' and 'library' beside the
    'items' list, and the marker rule alone would read the whole file - items
    and all - as one entry, which is the bypass this function exists to close.
    """
    if isinstance(node, dict):
        if is_root and _holds_a_collection(node):
            pass
        elif any(k in node for k in ENTRY_MARKER_KEYS):
            entry = dict(node)
            if not any(k in entry for k in IDENTIFIER_KEYS):
                entry["identifier"] = path[-1] if path else ""
            entries.append(entry)
            return
        leftovers: dict[str, Any] = {}
        for key in sorted(node.keys()):
            val = node[key]
            if isinstance(val, (dict, list)):
                _collect_entries(val, path + [key], entries)
            else:
                leftovers[key] = val
        if leftovers:
            leftovers["identifier"] = path[-1] if path else ""
            entries.append(leftovers)
        return

    if isinstance(node, list):
        own_name = path[-1] if path else "values"
        for index, item in enumerate(node):
            if isinstance(item, (dict, list)):
                _collect_entries(item, path, entries)
            else:
                # One entry per item, not one entry per list: a pool of role
                # names holds refused roles beside accepted ones, and a single
                # entry carrying the whole list is refused or accepted whole.
                entries.append({
                    "identifier": f"{own_name}[{index}]",
                    own_name: item,
                })
        return

    raise ValueError(
        f"Unsupported JSON node at {'.'.join(path) or 'root'}: "
        f"{type(node).__name__}"
    )


def _is_translation_map(data: Any) -> bool:
    """True when `data` is a translation map rather than source material.

    `resolve_translation_map_path` puts the map inside the source directory on
    purpose, and this walk is an `rglob("*.json")` over that directory, so the
    map is handed back to the extractor as source material unless it is named
    here. Read as source it yields one row per entry from the map's own
    `source` key, with an empty identifier and nothing for the guard to read -
    strings that cover themselves and would report a corpus clean because the
    answer was already in the file.

    Recognised by the shape `save_translation_map` writes and nothing else
    does: every value carrying all three of source, translation and fields.
    """
    if not isinstance(data, dict) or not data:
        return False
    return all(
        isinstance(v, dict) and {"source", "translation", "fields"} <= v.keys()
        for v in data.values()
    )


def _load_entries_from_file(json_file: Path) -> list[dict[str, Any]]:
    """Load asset entries from a JSON file in a source directory.

    A file whose shape cannot be split into entries raises rather than being
    read as one entry: accept-as-one is a guard bypass, since the guard reads
    an entry's own identifier and text and cannot see either through a
    wrapper it was never handed.
    """
    raw_text = json_file.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {json_file}: {exc}") from exc

    if _is_translation_map(data):
        return []

    if not isinstance(data, (dict, list)):
        raise ValueError(
            f"Unsupported JSON shape in {json_file}: {type(data).__name__}"
        )

    file_library = json_file.stem
    if isinstance(data, dict) and isinstance(data.get("library"), str):
        file_library = data["library"]

    entries: list[dict[str, Any]] = []
    _collect_entries(data, [json_file.stem], entries, is_root=True)
    for entry in entries:
        entry.setdefault("library", file_library)
    return entries


def extract_non_english_strings(
    source_dir: Path | str,
    *,
    refused_libraries: tuple[str, ...] | set[str] | list[str] = (),
) -> list[dict[str, str]]:
    """List every non-English string in accepted entries of source_dir.

    source_dir is a required argument with no default. Refused entries are
    filtered out by running the guard first so their strings are never listed.

    Each reported string is returned as a dict with:
    - 'identifier': the entry identifier
    - 'field': the field the string came from
    - 'string': the non-English string
    """
    if source_dir is None or not str(source_dir).strip():
        raise ValueError("source_dir is required and cannot be empty")

    source_path = Path(source_dir)
    if not source_path.is_dir():
        raise NotADirectoryError(f"source_dir must be an existing directory, got {source_path}")

    json_files = sorted(p for p in source_path.rglob("*.json") if p.is_file())
    all_entries: list[dict[str, Any]] = []
    for json_file in json_files:
        all_entries.extend(_load_entries_from_file(json_file))

    accepted_entries, _ = guard_entries(
        all_entries,
        refused_libraries=refused_libraries,
    )

    seen: set[tuple[str, str, str]] = set()
    results: list[dict[str, str]] = []

    for entry in accepted_entries:
        identifier = str(entry.get("identifier") or entry.get("id") or entry.get("key") or "")
        fields = sorted(k for k in entry.keys() if k not in METADATA_FIELDS)
        for field in fields:
            extracted = _extract_strings_from_value(entry[field], field, identifier)
            for item in extracted:
                dedup_key = (item["identifier"], item["field"], item["string"])
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    results.append(item)

    return results


def find_uncovered_strings(
    source_dir: Path | str,
    translation_map: dict[str, Any] | Path | str,
    fields: tuple[str, ...] | set[str] | list[str] | frozenset[str] | None = None,
    *,
    refused_libraries: tuple[str, ...] | set[str] | list[str] = (),
) -> list[dict[str, str]]:
    """Report non-English strings of a named field set not covered by translation_map.

    Given a source directory and a loaded map (or path to a map file), reports
    which strings of a named field set are not covered by the map. It runs the
    guard by going through the extractor, so a refused entry's strings are never
    reported as uncovered and never reach the map.

    Returns a list of dicts for each uncovered string, containing:
    - 'identifier': the entry identifier
    - 'field': the field name
    - 'string': the uncovered source string
    """
    if source_dir is None or not str(source_dir).strip():
        raise ValueError("source_dir is required and cannot be empty")

    if isinstance(translation_map, (str, Path)):
        from backend.translation_map import load_translation_map
        loaded_map = load_translation_map(translation_map)
    elif isinstance(translation_map, dict):
        loaded_map = translation_map
    else:
        raise TypeError(
            f"translation_map must be a dict or a path, got {type(translation_map).__name__}"
        )

    # A bare string is refused the way validate_translation_entry refuses one for
    # 'fields': "notes" and ["notes"] reaching the same call is one field
    # written two ways. None means every field, which is what 2.5 asks for.
    if fields is None:
        field_set = None
    elif isinstance(fields, str) or not hasattr(fields, "__iter__"):
        raise TypeError(
            f"fields must be a collection of strings or None, got {type(fields).__name__}"
        )
    else:
        field_set = set(fields)

    extracted = extract_non_english_strings(
        source_dir,
        refused_libraries=refused_libraries,
    )

    uncovered: list[dict[str, str]] = []
    for item in extracted:
        if field_set is not None and item["field"] not in field_set:
            continue
        source_str = item["string"]
        # An entry is a dict by construction: validate_translation_entry is the
        # only way one is written, and load_translation_map runs it on the way in.
        map_entry = loaded_map.get(source_str)
        trans = map_entry.get("translation") if isinstance(map_entry, dict) else None
        if not isinstance(trans, str) or not trans.strip() or contains_non_english(trans):
            uncovered.append(dict(item))

    return uncovered
