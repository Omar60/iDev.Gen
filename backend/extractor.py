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


def _load_entries_from_file(json_file: Path) -> list[dict[str, Any]]:
    """Load asset entries from a JSON file in a source directory."""
    raw_text = json_file.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {json_file}: {exc}") from exc

    file_library = json_file.stem
    entries: list[dict[str, Any]] = []

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                entry = dict(item)
                entry.setdefault("library", file_library)
                entries.append(entry)
    elif isinstance(data, dict):
        if data and all(isinstance(v, dict) for v in data.values()):
            for key in sorted(data.keys()):
                val = data[key]
                entry = dict(val)
                if "identifier" not in entry and "id" not in entry and "key" not in entry:
                    entry["identifier"] = key
                entry.setdefault("library", file_library)
                entries.append(entry)
        else:
            found_list = False
            for key in sorted(data.keys()):
                v = data[key]
                if isinstance(v, list) and any(isinstance(x, dict) for x in v):
                    found_list = True
                    for item in v:
                        if isinstance(item, dict):
                            entry = dict(item)
                            entry.setdefault("library", file_library)
                            entries.append(entry)
            if not found_list:
                entry = dict(data)
                entry.setdefault("library", file_library)
                entries.append(entry)

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
