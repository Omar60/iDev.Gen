"""Mine the fused source library into rows this project can measure.

A `perspective_scenes` entry names a camera position, an act and a room in one
string. Stored whole it is a room seed row carrying a camera position - a room
that overrules the line's camera - so it is cut into one row per part, and every
boundary is read from the curated cut map. Nothing here parses prose.

Three curated files sit beside the source material, none of them tracked:

  * the cut map, one entry per source identifier naming the substrings
  * the family declaration, because the corpus names the ROOM it is set in
    where this project needs the family whose camera it was written for
  * the judge labels, the sentence a blind judge is shown for each mined row

What the run does, in order, and it writes nothing until every check has passed:

  1. declares the file against the manifest as fused material,
  2. filters the entries through the asset guard,
  3. translates each entry's label through the translation map,
  4. resolves placeholders and REPORTS any entry still holding one,
  5. cuts each entry, deriving the manner from its family and the second-body
     requirement from the act's own wording,
  6. records the combination each entry was split into,
  7. writes the room rows to the seed the manifest declares and registers it,
  8. posts the camera and act rows to the catalogue's own import route.

Usage:
  python scripts/mine_perspective_scenes.py --source DIR --map PATH
      [--cuts PATH] [--families PATH] [--labels PATH]
      [--base URL] [--config PATH] [--data-dir DIR] [--dry-run]

Every path is the operator's to type: the source material is machine-local and
there is no sensible default to guess, which is the rule the room import already
keeps.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.asset_guard import guard_entries
from backend.cut_map import cut_for, load_cut_map, missing_cuts
from backend.importer import (
    _translate_field,
    derive_multi_body,
    derive_offers,
    derive_tags,
    resolve_placeholders,
    unresolved_placeholders,
)
from backend.mining import (
    MINED_FAMILIES_FILE,
    MINED_LABELS_FILE,
    component_rows,
    identifier_for,
    load_mined_families,
    load_mined_labels,
    record_combinations,
    save_mined_combinations,
    split_fused_entry,
)
from backend.room_registry import (
    get_room_libraries_config,
    resolve_data_dir,
    verify_registry_disk_agreement,
)
from backend.source_manifest import KIND_FUSED_SCENES, declare_source_file
from backend.translation_map import load_translation_map

LIBRARY_FILE = "perspective_scenes.json"
CUT_MAP_FILE = "perspective-cuts.json"


def _entries(path: Path) -> list[dict[str, Any]]:
    """The library's entries, whatever shape the file wraps them in."""
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"{path.name} holds no list of entries")
    return [dict(item, library=data.get("library") if isinstance(data, dict) else None)
            for item in items if isinstance(item, dict)]


def _room_row(entry: dict[str, Any], row: dict[str, str],
              translation_map: dict) -> dict[str, Any]:
    """One room seed row, built from the ROOM cut and not from the whole entry.

    The derivations are the room importer's own functions rather than second
    spellings of them: `offers`, `tags` and `multi_body` are read here exactly
    as they are read for every other library, and they are read off the CUT,
    because that is the text this room will actually compose into a look.

    Keyed by the key the SPLIT derived and NOT by `derive_room_key`. The
    combination records the keys of the rows the entry was cut into, and a room
    stored under a second key of its own is a room the combination can never
    resolve - the compose route matches the session's room key against the one
    the record names, and the two would never be the same string.
    """
    place = row["wording"]
    identifier = identifier_for(entry)
    return {
        "key": row["key"],
        "label": _translate_field(
            entry.get("label", ""), translation_map, identifier, "label", set()
        ),
        # No restriction, the same default the room import writes: the source
        # says nothing about manners.
        "manners": [],
        "place": place,
        "identifier": identifier,
        "source_library": entry.get("library"),
        "weight": _weight(entry),
        "offers": derive_offers(entry, place),
        "tags": derive_tags(entry),
        "multi_body": derive_multi_body(place),
        "guidance": {},
    }


def _weight(entry: dict[str, Any]) -> float:
    try:
        return float(entry.get("weight", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _catalogue_keys(base: str) -> dict[tuple[str, str, str], str]:
    """Every stored component key, by the slot, manner and wording it holds.

    Asked of the app rather than of the database file: the app is the thing
    that just wrote these rows, and a second reader would be a second answer to
    what the catalogue carries.
    """
    with urllib.request.urlopen(base.rstrip("/") + "/api/components?all=true",
                                timeout=60) as response:
        stored = json.loads(response.read().decode("utf-8"))
    return {(c["slot"], c["manner"], c["wording"].strip()): c["concept_key"]
            for c in stored}


def _repoint(rows: list[dict[str, str]], stored: dict[tuple[str, str, str], str]) -> int:
    """Point each component row at the key the catalogue holds its wording under.

    Rooms are left alone: they are not components, nothing dedupes them, and
    each one is written under the key its own split derived.
    """
    moved = 0
    for row in rows:
        if row["slot"] == "room":
            continue
        key = stored.get((row["slot"], row["manner"], row["wording"].strip()))
        if key and key != row["key"]:
            row["key"] = key
            moved += 1
    return moved


def _post(base: str, path: str, payload: Any) -> dict:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="directory holding the source library")
    parser.add_argument("--map", required=True, help="the translation map")
    parser.add_argument("--cuts", default="", help=f"the cut map (default: <source>/{CUT_MAP_FILE})")
    parser.add_argument("--families", default="", help=f"the family declaration (default: <source>/{MINED_FAMILIES_FILE})")
    parser.add_argument("--labels", default="", help=f"the judge labels (default: <source>/{MINED_LABELS_FILE})")
    parser.add_argument("--base", default="http://127.0.0.1:8777", help="the running app")
    parser.add_argument("--config", default="", help="config.json to register the room library in")
    parser.add_argument("--data-dir", default="", help="where the seed files live")
    parser.add_argument("--dry-run", action="store_true", help="report and write nothing")
    args = parser.parse_args(argv)

    source = Path(args.source)
    library = source / LIBRARY_FILE
    if not library.is_file():
        print(f"no {LIBRARY_FILE} in {source}")
        return 1

    # The manifest decides whether this file is read at all, and this caller
    # says what it can write. A room importer asking for the same file is
    # refused: only the room PART of a cut entry belongs in a room seed.
    declared = declare_source_file(library, writes=KIND_FUSED_SCENES)
    destination = declared["destinations"][0]

    translation_map = load_translation_map(Path(args.map))
    cut_map = load_cut_map(Path(args.cuts) if args.cuts else source / CUT_MAP_FILE)
    families = load_mined_families(
        Path(args.families) if args.families else source / MINED_FAMILIES_FILE
    )
    labels = load_mined_labels(
        Path(args.labels) if args.labels else source / MINED_LABELS_FILE
    )

    entries = _entries(library)
    accepted, report = guard_entries(entries)
    print(f"{len(entries)} entries, {report['accepted']} accepted, "
          f"{report['refused']} refused by the guard")
    for signal, count in sorted(report["by_signal"].items()):
        if count:
            print(f"  {count} on {signal}: {report['refused_identifiers']}")

    short = missing_cuts(cut_map, [identifier_for(e) for e in accepted])
    if short:
        print(f"no cut written for {len(short)} entries: {short}")
        return 1
    undeclared = sorted(identifier_for(e) for e in accepted if identifier_for(e) not in families)
    if undeclared:
        print(f"no family declared for {len(undeclared)} entries: {undeclared}")
        return 1

    rows: list[dict[str, str]] = []
    room_rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for entry in accepted:
        identifier = identifier_for(entry)
        cut = cut_for(cut_map, identifier)
        # After translation, and reported rather than deleted: a hole quietly
        # dropped is a sentence with a piece missing that still reads as English
        # and still enters the catalogue.
        resolved = {slot: resolve_placeholders(text, entry) for slot, text in cut.items()}
        holes = sorted({h for text in resolved.values() for h in unresolved_placeholders(text)})
        if holes:
            skipped.append(f"{identifier} ({', '.join(holes)})")
            continue
        split = split_fused_entry(entry, resolved, identifier=identifier,
                                  source_family=families[identifier])
        rows.extend(split)
        for row in split:
            if row["slot"] == "room":
                room_rows.append(_room_row(entry, row, translation_map))

    if skipped:
        print(f"{len(skipped)} entries skipped, a placeholder nothing filled: {skipped}")

    components = component_rows(rows, labels)
    print(f"{len(rows)} rows: {len(components)} components, {len(room_rows)} rooms, "
          f"{len(set(row['source_identifier'] for row in rows))} combinations")
    if args.dry_run:
        print("dry run: nothing written")
        return 0

    config_path = Path(args.config) if args.config else ROOT / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    data_dir = resolve_data_dir(data_dir=args.data_dir or None, config=config)
    data_dir.mkdir(parents=True, exist_ok=True)

    # The seed and its registry entry are one operation, the rule
    # `verify_registry_disk_agreement` refuses the pair for: a seed on disk
    # whose library was never registered fails direction 2, and a registry
    # entry naming a file nobody wrote fails direction 1.
    (data_dir / destination).write_text(
        json.dumps(room_rows, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    libraries = list(get_room_libraries_config(config))
    if destination not in {lib.get("seed_file") for lib in libraries}:
        libraries.append({"name": declared["library"], "seed_file": destination,
                          "enabled": True, "weight": 1.0})
    config["room_libraries"] = libraries
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    verify_registry_disk_agreement(config=config, data_dir=data_dir)
    print(f"wrote {len(room_rows)} rooms to {destination} and registered the library")

    try:
        report = _post(args.base, "/api/components/import", components)
    except urllib.error.URLError as exc:
        print(f"the app at {args.base} did not answer: {exc}. The rooms are "
              f"written; re-run to import the components and record the "
              f"combinations.")
        return 1
    print(f"components: {report['added']} added, {report['skipped']} skipped")
    for duplicate in report.get("duplicates", []):
        print(f"  duplicate on {duplicate['matched_on']}: {duplicate['concept_key']} "
              f"is already {duplicate['existing_key']}")

    # The combination is recorded AFTER the import and against what the
    # catalogue actually carries. A mined clause that duplicates a row already
    # there creates no second row (8.15), so the key this entry's split derived
    # may name nothing - and a combination naming a row nobody wrote is broken
    # on the day it is recorded. Two entries whose camera is the same sentence
    # are pointed at the one row that holds it, which is what the duplicate
    # report says the operator's next move is.
    moved = _repoint(rows, _catalogue_keys(args.base))
    combinations = record_combinations(rows)
    save_mined_combinations(combinations, data_dir=data_dir, config=config)
    print(f"recorded {len(combinations)} combinations, {moved} slots pointed at "
          f"the row the catalogue already carried")
    print("The app reads its config at startup: restart it to see the new room library.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
