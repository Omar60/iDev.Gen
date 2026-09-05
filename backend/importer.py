"""Asset importer for external source libraries.

Implements the single asset import pipeline with:
- Refusal first: source manifest declaration and asset guard filtering.
- Translation lookup: strictly reads from the translation map, stops if missing.
- Merge: updates text and derived fields while preserving verdicts and sample sizes.
- Report: counts accepted, refused, created, updated, unchanged, orphaned, and
  names refused entries by identifier only (never entry text).
- Registry synchronization: writes seed and registers library in config atomically
  so verify_registry_disk_agreement holds.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.asset_guard import guard_entries
from backend.extractor import _load_entries_from_file
from backend.room_registry import (
    get_room_libraries_config,
    resolve_data_dir,
    verify_registry_disk_agreement,
)
from backend.source_manifest import (
    SourceRefused,
    declaration_for,
    declare_source_file,
)
from backend.translation_map import (
    contains_non_english,
    load_translation_map,
    resolve_translation_map_path,
)

THEME_FIELDS: tuple[str, ...] = (
    "theme_text",
    "scene_theme",
    "theme",
    "text",
    "prompt",
)


class TranslationMissingError(ValueError):
    """Raised when a non-English string is missing from the translation map."""

    def __init__(self, identifier: str, field: str, text: str) -> None:
        super().__init__(
            f"Missing translation for entry {identifier!r}, field {field!r}: {text!r}"
        )
        self.identifier = identifier
        self.field = field
        self.text = text


def derive_room_key(identifier: str) -> str:
    """Derive an ASCII normalized room key from source identifier."""
    raw = str(identifier).strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return normalized or "room"


def _extract_theme_text(entry: dict[str, Any]) -> str:
    """Extract theme or scene prose from an entry, or empty string if absent.

    Returned byte for byte as the source wrote it. A field is chosen by whether
    it holds anything other than whitespace, and the value handed back is never
    trimmed: the room text is stored character for character, and a `strip()`
    on the way in is a silent edit to the wording this import exists to adopt.
    """
    for field in THEME_FIELDS:
        val = entry.get(field)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _find_untranslated_strings(
    entry: dict[str, Any],
    translation_map: dict[str, dict[str, Any]],
) -> list[tuple[str, str]]:
    """Find non-English strings in an entry not covered by the map.

    Returns list of (field_name, source_string).
    """
    untranslated: list[tuple[str, str]] = []
    identifier = str(entry.get("identifier") or entry.get("id") or entry.get("key") or "")

    for field, val in entry.items():
        if isinstance(val, str):
            if contains_non_english(val):
                map_entry = translation_map.get(val)
                trans = map_entry.get("translation") if isinstance(map_entry, dict) else None
                if not isinstance(trans, str) or not trans.strip() or contains_non_english(trans):
                    untranslated.append((field, val))
        elif isinstance(val, (list, tuple)):
            for idx, item in enumerate(val):
                if isinstance(item, str) and contains_non_english(item):
                    map_entry = translation_map.get(item)
                    trans = map_entry.get("translation") if isinstance(map_entry, dict) else None
                    if not isinstance(trans, str) or not trans.strip() or contains_non_english(trans):
                        untranslated.append((f"{field}[{idx}]", item))
    return untranslated


def _translate_value(
    val: Any,
    translation_map: dict[str, dict[str, Any]],
) -> Any:
    """Translate a value using the translation map if it contains non-English text."""
    if isinstance(val, str):
        if contains_non_english(val) and val in translation_map:
            return translation_map[val]["translation"]
        return val
    if isinstance(val, list):
        return [_translate_value(item, translation_map) for item in val]
    if isinstance(val, tuple):
        return tuple(_translate_value(item, translation_map) for item in val)
    if isinstance(val, dict):
        return {k: _translate_value(v, translation_map) for k, v in val.items()}
    return val


def import_source(
    source_dir: Path | str,
    map_path: Path | str,
    data_dir: Path | str | None = None,
    *,
    config: dict,
) -> dict[str, Any]:
    """Import an asset source directory into seed files.

    Steps:
    1. Validate source_dir and map_path (both required, no defaults).
    2. Load translation map (resolve beside source_dir if relative).
    3. Manifest check: a file whose library is undeclared or reaches no
       destination is refused, named in the report and never read.
    4. Guard check: filter entries through guard_entries before translation or write.
    5. Check translation map coverage: stop before write if any non-English string is missing.
    6. Validate everything then write: merge rows in memory, update seed files,
       and register each destination in config['room_libraries'].

    `config` is required and keyed only, with no default. Writing a seed file
    and registering its library are one operation from outside or
    `room_registry.verify_registry_disk_agreement` refuses the pair: writing
    the seed alone fails direction 2, registering alone fails direction 1. A
    caller that could omit the registry half is a caller that leaves the app in
    the state that check exists to name.
    """
    if source_dir is None or not str(source_dir).strip():
        raise ValueError("source_dir is required and cannot be empty")
    if map_path is None or not str(map_path).strip():
        raise ValueError("map_path is required and cannot be empty")

    source_path = Path(source_dir)
    if not source_path.is_dir():
        raise NotADirectoryError(f"source_dir must be an existing directory, got {source_path}")

    # Resolve and load translation map
    map_p = Path(map_path)
    if not map_p.is_absolute():
        resolved_map_path = resolve_translation_map_path(source_path, map_p)
    else:
        resolved_map_path = map_p
    translation_map = load_translation_map(resolved_map_path)

    # Discover source JSON files, skipping the translation map itself
    all_json_files = sorted(p for p in source_path.rglob("*.json") if p.is_file())
    source_files: list[Path] = []
    for p in all_json_files:
        try:
            if p.resolve() == resolved_map_path.resolve():
                continue
        except OSError:
            pass
        source_files.append(p)

    target_data_dir = resolve_data_dir(data_dir=data_dir, config=config)
    target_data_dir.mkdir(parents=True, exist_ok=True)

    # 1. Manifest refusal check on all files before loading entries. A refused
    # file is refused on its own and the run carries on. The corpus is handed
    # over whole - nine libraries, three of which reach no destination - so a
    # refusal that aborted the run would be a corpus that can never be
    # imported, and the one file nobody declared would take the other eight
    # down with it. Nothing inside a refused file is read, so neither a row nor
    # the report can carry its prose: the report names its library and the
    # reason, which are the file's name and a constant from the manifest.
    declared_files: list[Path] = []
    refused_libraries: list[dict[str, str]] = []
    for sf in source_files:
        try:
            declare_source_file(sf)
        except SourceRefused as refusal:
            refused_libraries.append(
                {"library": refusal.library, "reason": refusal.reason}
            )
            continue
        declared_files.append(sf)

    # 2. Collect entries from declared files
    all_entries: list[dict[str, Any]] = []
    for sf in declared_files:
        entries = _load_entries_from_file(sf)
        all_entries.extend(entries)

    # 3. Guard entries: filter out refused entries
    accepted_entries, guard_report = guard_entries(all_entries)

    # 4. Filter empty header entries and check translation coverage
    valid_accepted_entries: list[dict[str, Any]] = []
    skipped_empty_ids: list[str] = []

    for entry in accepted_entries:
        identifier = str(entry.get("identifier") or entry.get("id") or entry.get("key") or "")
        theme = _extract_theme_text(entry)
        if not theme:
            skipped_empty_ids.append(identifier)
            continue

        # Check for untranslated strings
        untranslated = _find_untranslated_strings(entry, translation_map)
        if untranslated:
            field_name, bad_str = untranslated[0]
            raise TranslationMissingError(identifier, field_name, bad_str)

        valid_accepted_entries.append(entry)

    # 5. Group entries by destination file
    entries_by_dest: dict[str, list[dict[str, Any]]] = {}
    library_by_dest: dict[str, str] = {}

    for entry in valid_accepted_entries:
        lib_name = str(entry.get("library") or "")
        decl = declaration_for(lib_name)
        if decl and decl.get("destinations"):
            for dest in decl["destinations"]:
                entries_by_dest.setdefault(dest, []).append(entry)
                library_by_dest[dest] = lib_name

    # 6. Build and merge rows per destination (in memory first)
    destination_results: dict[str, list[dict[str, Any]]] = {}
    dest_reports: dict[str, dict[str, Any]] = {}

    total_created = 0
    total_updated = 0
    total_unchanged = 0
    total_orphaned = 0
    total_written = 0

    for dest, incoming_entries in entries_by_dest.items():
        dest_path = target_data_dir / dest
        existing_rows: list[dict[str, Any]] = []
        if dest_path.is_file():
            try:
                existing_rows = json.loads(dest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                existing_rows = []

        existing_by_id: dict[str, dict[str, Any]] = {}
        for r in existing_rows:
            row_id = str(r.get("identifier") or r.get("key") or "")
            if row_id:
                existing_by_id[row_id] = r

        created_keys: list[str] = []
        updated_keys: list[str] = []
        unchanged_keys: list[str] = []
        seen_incoming_ids: set[str] = set()
        merged_rows: list[dict[str, Any]] = []

        for entry in incoming_entries:
            identifier = str(entry.get("identifier") or entry.get("id") or entry.get("key") or "")
            seen_incoming_ids.add(identifier)

            room_key = derive_room_key(identifier)
            raw_label = str(entry.get("label") or entry.get("name") or identifier)
            label = _translate_value(raw_label, translation_map)
            # The room text goes through the map like every other string. It is
            # the reason this import exists, and storing it as the source wrote
            # it puts the source's own script in a seed file - invisibly, since
            # `ensure_ascii=True` writes it back out as \\u escapes that the
            # repository's CJK rule cannot see.
            theme_text = _translate_value(_extract_theme_text(entry), translation_map)

            # Build updated row
            new_row: dict[str, Any] = {
                "key": room_key,
                "label": label,
                "manner": "candid",
                "look": theme_text,
                "identifier": identifier,
                "source_library": entry.get("library"),
            }
            if "offers" in entry:
                new_row["offers"] = entry["offers"]
            if "notes" in entry:
                new_row["notes"] = _translate_value(entry["notes"], translation_map)

            # Check if this row already existed
            existing_row = existing_by_id.get(identifier) or existing_by_id.get(room_key)
            if existing_row is not None:
                # Preserve existing verdict and sample size
                for preserve_key in ("verdict", "sample_size", "manner"):
                    if preserve_key in existing_row and preserve_key not in new_row:
                        new_row[preserve_key] = existing_row[preserve_key]
                    elif preserve_key in existing_row and preserve_key == "manner":
                        new_row["manner"] = existing_row["manner"]

                if new_row == existing_row:
                    unchanged_keys.append(room_key)
                else:
                    updated_keys.append(room_key)
            else:
                new_row.setdefault("verdict", "unverified")
                created_keys.append(room_key)

            merged_rows.append(new_row)

        # Retain orphaned rows (rows that existed on disk but disappeared upstream)
        orphaned_keys: list[str] = []
        for existing_id, existing_row in existing_by_id.items():
            row_key = existing_row.get("key") or existing_id
            if existing_id not in seen_incoming_ids and row_key not in seen_incoming_ids:
                orphaned_keys.append(row_key)
                merged_rows.append(existing_row)

        destination_results[dest] = merged_rows
        dest_reports[dest] = {
            "written": len(merged_rows),
            "created": len(created_keys),
            "created_keys": created_keys,
            "updated": len(updated_keys),
            "updated_keys": updated_keys,
            "unchanged": len(unchanged_keys),
            "orphaned": len(orphaned_keys),
            "orphaned_keys": orphaned_keys,
        }

        total_created += len(created_keys)
        total_updated += len(updated_keys)
        total_unchanged += len(unchanged_keys)
        total_orphaned += len(orphaned_keys)
        total_written += len(merged_rows)

    # 7. Write seed files and synchronize registry config atomically
    for dest, rows in destination_results.items():
        dest_path = target_data_dir / dest
        formatted = json.dumps(rows, ensure_ascii=True, indent=2) + "\n"
        dest_path.write_text(formatted, encoding="utf-8")

        # Register the library in the same operation that writes its seed.
        lib_name = library_by_dest.get(dest, Path(dest).stem.replace("-rooms-seed", ""))
        current_libs = list(get_room_libraries_config(config))
        existing_seed_files = {lib.get("seed_file") or lib.get("seed") for lib in current_libs}
        if dest not in existing_seed_files:
            current_libs.append({
                "name": lib_name,
                "seed_file": dest,
                "enabled": True,
                "weight": 1.0,
            })
        config["room_libraries"] = current_libs

    verify_registry_disk_agreement(config=config, data_dir=target_data_dir)

    return {
        "accepted": guard_report["accepted"],
        "refused": guard_report["refused"],
        "written": total_written,
        "created": total_created,
        "updated": total_updated,
        "unchanged": total_unchanged,
        "orphaned": total_orphaned,
        "skipped_empty": len(skipped_empty_ids),
        "skipped_empty_identifiers": skipped_empty_ids,
        "refused_files": len(refused_libraries),
        "refused_libraries": refused_libraries,
        "refused_identifiers": guard_report["refused_identifiers"],
        "by_signal": guard_report["by_signal"],
        "by_library": guard_report["by_library"],
        "destinations": dest_reports,
    }
