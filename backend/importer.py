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

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from backend.asset_guard import guard_entries, guard_entry
from backend.extractor import (
    _extract_strings_from_value,
    _load_entries_from_file,
)
from backend.room_registry import (
    available_rooms,
    get_room_libraries_config,
    prose_names_piece,
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

# Where a source entry lists what is in the room. The source writes it as one
# comma-separated string in a `props` slot - "supply shelves, stacked linen
# packs, glove boxes, rolling cart" - and a list is accepted too, because a
# reader that only handles the shape in front of it is a reader that breaks on
# the next library.
PROP_FIELDS: tuple[str, ...] = ("props", "objects", "furniture")


# Words that put somebody OTHER than the subject in the frame.
#
# This is a one-character studio: every photograph is of her. So a word earns a
# place on this list only when it cannot be about her. Plurals qualify however
# they are gendered - she is one person, so `women` and `girls` are other
# people the same way `men` is - and so do the male terms and the role nouns,
# which name somebody doing a job in the scene while she is photographed.
#
# What is deliberately NOT here is the ambiguous female singular: `woman`,
# `girl`, `lady`, `she`, `her`. A room's own prose describes the place she is
# in and frequently describes her standing in it, so those words are as likely
# to be the subject as a second body, and a rule that is wrong half the time is
# worse for the operator than one that says nothing - it would mark most of the
# corpus and train them to turn the gate off.
#
# The bias that is left is deliberate and it points at over-marking. A missed
# room is silent: a crowd composes into a single-subject run and nobody learns
# until the frame comes back. A wrongly marked room is visible, names the word
# that did it, and the operator turns the second body on or picks another room.
# ponytail: a word list, not a parser. It cannot see "an empty hall with no
# people in it", which marks. If that shows up in the corpus, the upgrade is a
# negation check on the words immediately before the match, not a grammar.
OTHER_BODY_WORDS: tuple[str, ...] = (
    # plurals and collectives - she is one person
    "people", "persons", "crowd", "crowds", "others", "onlookers", "bystanders",
    "audience", "spectators", "guests", "customers", "patrons", "shoppers",
    "passengers", "students", "colleagues", "coworkers", "friends", "strangers",
    "couple", "couples", "men", "women", "boys", "girls", "ladies", "staff",
    # a second body that is not her
    "man", "him", "boyfriend", "husband", "gentleman", "guy", "guys",
    # somebody doing a job in the scene
    "bridesmaid", "bridesmaids", "nurse", "nurses", "doctor", "doctors",
    "waiter", "waitress", "bartender", "photographer", "assistant", "attendant",
    "guard", "guards", "officer", "receptionist", "stylist", "technician",
    "therapist", "masseur", "masseuse", "teacher", "cameraman",
)

_OTHER_BODY_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in OTHER_BODY_WORDS) + r")\b"
)


def derive_multi_body(prose: str) -> list[str]:
    """The words in a room's own text that put other people in the frame.

    Empty means the room is a single-subject place. Non-empty is the marking
    AND the reason: the gate that refuses this room in a run with no second
    body has to name the words responsible, or the operator is told no without
    being told what to do about it.

    One field, holding the words, rather than a boolean beside a list. The
    boolean is `bool(...)` of the list and cannot drift from it; stored
    separately they are two calculations of one fact, which is the bug this
    repo has now found several times.

    Computed here, at import, and never again. Recomputing at compose time
    would read a look the operator is expected to have edited, and would answer
    a question about words that may no longer be in the line.
    """
    found: list[str] = []
    for match in _OTHER_BODY_PATTERN.finditer((prose or "").lower()):
        word = match.group(1)
        if word not in found:
            found.append(word)
    return found


def derive_offers(entry: dict[str, Any], prose: str) -> list[str]:
    """The pieces this room offers: its source prop list, minus what the prose
    does not name.

    A prop the theme string never mentions is a piece no photograph can
    contain, so offering it would have the picker promise furniture and the act
    ask for it, and the render answer with neither. The intersection is the
    whole rule, and an empty result is the correct answer rather than a
    degraded one: a room that offers nothing licenses no act that names
    furniture, which is exactly what its prose supports.

    Read off the room's own English prose, never off a translation. A reworded
    translation must not be able to change whether a room offers its sofa.
    """
    pieces: list[str] = []
    for field in PROP_FIELDS:
        raw = entry.get(field)
        if isinstance(raw, str):
            pieces = [part.strip() for part in raw.split(",")]
        elif isinstance(raw, (list, tuple)):
            pieces = [str(part).strip() for part in raw]
        else:
            continue
        break

    kept: list[str] = []
    for piece in pieces:
        if piece and prose_names_piece(piece, prose) and piece not in kept:
            kept.append(piece)
    return kept


def is_guidance_field(name: str) -> bool:
    """Is this source field the entry author writing down how the shot works.

    `notes` and the four `*_anchor` fields are the only slots in the corpus
    that explain themselves - an `action_anchor` saying the subject is bending
    to restock rather than merely standing, a `notes` field forbidding crystal
    sparkle - and `anchors` is the same material grouped under one key by a
    library that writes it that way.

    It is guidance for whoever picks or edits the room and it never reaches a
    line: `compose_look` joins the manner's register to the room's place and
    reads nothing else, so keeping guidance out of a prompt is a property of
    where it is stored, not a filter somebody has to remember to apply.

    ponytail: a name rule, not a per-library map. A library that calls the
    same material something else stores it as an ordinary field and the picker
    does not show it; the upgrade is another name here, not a schema.
    """
    return name == "notes" or name == "anchors" or name.endswith("_anchor")


class TranslationMissingError(ValueError):
    """Raised when non-English strings in an upload are missing from the map."""

    def __init__(self, uncovered: list[dict[str, str]]) -> None:
        self.uncovered = [dict(item) for item in uncovered]
        first = self.uncovered[0] if self.uncovered else {}
        self.identifier = str(first.get("identifier") or "")
        self.field = str(first.get("field") or "")
        self.text = str(first.get("string") or "")

        if len(self.uncovered) <= 1:
            msg = (
                f"Missing translation for entry {self.identifier!r}, "
                f"field {self.field!r}: {self.text!r}"
            )
        else:
            lines = [
                f"Missing translation for {len(self.uncovered)} string(s) across upload:"
            ]
            lines.extend(
                f"  entry {item['identifier']!r}, field {item['field']!r}: "
                f"{item['string']!r}"
                for item in self.uncovered
            )
            msg = "\n".join(lines)
        super().__init__(msg)


def derive_room_key(identifier: str) -> str:
    """Derive an ASCII room key from the source's own identifier, and nothing else.

    The identifier is what the source called the entry, and it is the only
    input here: not the label, not the theme text, and not a translation of
    any of them. A translation is a thing a person corrects - that is what the
    map is for - and a key that moved when somebody fixed an English wording
    would take the room's verdict, its sample size and any session pointing at
    it with it.

    Normalisation is NFKD first, then the ASCII characters that survive.
    Dropping the non-ASCII bytes on their own is not a normalisation, it is a
    truncation that collides: `salon_01` and the same word with an acute
    accent on its o both reduce to `sal-n-01` under a bare character class,
    and two rooms sharing one key is one room. NFKD separates the accent from
    the letter it sits on, so the letter survives and only the mark is dropped.

    An identifier with no ASCII in it at all - a script NFKD does not decompose
    to Latin - has nothing left to normalise, and the old fallback named every
    one of them `room`. That is the same collision with a friendlier name, so
    those fall back to a digest of the identifier instead: unreadable, but
    stable across re-imports and distinct per entry, which is what a key is for.
    """
    decomposed = unicodedata.normalize("NFKD", str(identifier).strip().lower())
    # The combining marks NFKD split off are dropped rather than replaced: a
    # replacement puts a separator where the accent was and gives `salo-n-01`,
    # which is a third spelling rather than the `salon-01` the split was for.
    raw = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    normalized = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if normalized:
        return normalized
    digest = hashlib.sha1(str(identifier).encode("utf-8")).hexdigest()[:12]
    return f"room-{digest}"


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
) -> list[dict[str, str]]:
    """Find non-English strings in an entry not covered by the map.

    Returns list of dicts with 'identifier', 'field' and 'string'.
    Reuses the recursive walker from backend.extractor so nested dicts and
    lists are checked rather than silently escaping non-English text to disk.

    Every field is walked, the extractor's METADATA_FIELDS included. That list
    marks what is not a translation candidate for a coverage report; it is not
    what may reach a seed file. `identifier` is on it and is written to the row,
    so filtering by it puts the source's own script on disk as unicode escapes the
    repository's CJK rule cannot see - measured, not reasoned.
    """
    identifier = str(entry.get("identifier") or entry.get("id") or entry.get("key") or "")
    untranslated: list[dict[str, str]] = []

    for field in sorted(entry.keys()):
        extracted = _extract_strings_from_value(entry[field], field, identifier)
        for item in extracted:
            source_str = item["string"]
            map_entry = translation_map.get(source_str)
            trans = map_entry.get("translation") if isinstance(map_entry, dict) else None
            if not isinstance(trans, str) or not trans.strip() or contains_non_english(trans):
                untranslated.append({
                    "identifier": identifier,
                    "field": item["field"],
                    "string": source_str,
                })
    return untranslated


def _translate_value(
    val: Any,
    translation_map: dict[str, dict[str, Any]],
    identifier: str = "",
    field: str = "",
    translated: set[str] | None = None,
) -> Any:
    """Translate a value using the translation map, refusing what it does not cover.

    The map is the only source of translations, so a non-English string the map
    does not carry has nowhere to come from and this raises rather than handing
    the source's own script back to the caller. The old fallback returned such a
    string unchanged, which is only ever reached when `_find_untranslated_strings`
    missed it - and the two walks are separate recursions, so "missed it" is a
    live shape and not a hypothetical: `_extract_strings_from_value` does not
    descend a list inside a list, this function does, and a `notes` field shaped
    `[[text]]` imported clean and landed on disk as the \\uXXXX escapes
    `ensure_ascii=True` writes back out. Measured, both directions, on that
    fixture. That is 4.4's defect in a second place, and refusing here closes it
    for every shape rather than for the one shape found: the coverage walk stays
    the reporter that lists every uncovered string at once, and this stays the
    thing that makes a gap in it fail loudly instead of silently.

    Raising here is still before any write - rows are merged in memory and the
    seed files are written after the loop - so a destination stays byte-identical.
    A map entry that is present carries a non-empty English translation by
    construction, `validate_translation_map` refusing anything else on the way in,
    so presence is the whole check.
    """
    if isinstance(val, str):
        if not contains_non_english(val):
            return val
        if val in translation_map:
            if translated is not None:
                translated.add(field)
            return translation_map[val]["translation"]
        raise TranslationMissingError(
            [{"identifier": identifier, "field": field, "string": val}]
        )
    if isinstance(val, list):
        return [
            _translate_value(item, translation_map, identifier, field, translated)
            for item in val
        ]
    if isinstance(val, tuple):
        return tuple(
            _translate_value(item, translation_map, identifier, field, translated)
            for item in val
        )
    if isinstance(val, dict):
        return {
            k: _translate_value(
                v, translation_map, identifier,
                f"{field}.{k}" if field else str(k), translated,
            )
            for k, v in val.items()
        }
    return val


def _translate_field(
    val: Any,
    translation_map: dict[str, dict[str, Any]],
    identifier: str,
    field: str,
    authored: set[str],
) -> Any:
    """Translate one top-level row field and record whether the map wrote it.

    A field is AUTHORED when its words came out of the translation map, and it
    is SOURCE when the entry was already in English and `_translate_value`
    handed the string straight back. The same field is one or the other
    depending on the entry - `label` is a translation for a source that wrote
    it in its own script and the source's own words for one that did not - so
    which it is cannot be read off the field name, and a rule guessing from the
    name would be right for most of the corpus and quietly wrong for the rest.

    So it is recorded where it is known, at the substitution, rather than
    re-derived afterwards from the text. Re-deriving is the shape of bug this
    repo has now found several times: two calculations of one fact that agree
    until they do not.
    """
    seen: set[str] = set()
    out = _translate_value(val, translation_map, identifier, field, seen)
    if seen:
        authored.add(field)
    return out


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

    # 3b. Attribute the same guard decision to a destination, per entry. Every
    # entry in all_entries came from a declared_files member, and a file only
    # reaches declared_files by surviving declare_source_file - which refuses
    # any library with no destination before an entry is ever loaded. So the
    # library on every entry here resolves to a real destination, and this is
    # a direct read of data already on the entry, not an invented attribution.
    # `guard_entry` is called again rather than matched against
    # `accepted_entries` by identity, because `prune_options` returns a new
    # dict for an entry it pruned, which object identity would misreport.
    dest_accepted: dict[str, int] = {}
    dest_refused: dict[str, int] = {}
    for entry in all_entries:
        decl = declaration_for(str(entry.get("library") or ""))
        destinations = decl.get("destinations") if decl else ()
        if not destinations:
            continue
        refused = guard_entry(entry) is not None
        bucket = dest_refused if refused else dest_accepted
        for dest in destinations:
            bucket[dest] = bucket.get(dest, 0) + 1

    # 4. Filter empty header entries, then check translation coverage over
    # every entry that survives - the whole upload, before anything is written.
    # An entry with no theme text is a file-level header leftover: it is not
    # refused, it is simply never written, so a string on it cannot reach a
    # seed and must not take the upload down with it. Same reason a guarded
    # entry's strings are absent from the list.
    valid_accepted_entries: list[dict[str, Any]] = []
    skipped_empty_ids: list[str] = []
    all_untranslated: list[dict[str, str]] = []
    seen_untranslated: set[tuple[str, str, str]] = set()

    for entry in accepted_entries:
        identifier = str(entry.get("identifier") or entry.get("id") or entry.get("key") or "")

        theme = _extract_theme_text(entry)
        if not theme:
            skipped_empty_ids.append(identifier)
            continue

        for item in _find_untranslated_strings(entry, translation_map):
            key = (item["identifier"], item["field"], item["string"])
            if key not in seen_untranslated:
                seen_untranslated.add(key)
                all_untranslated.append(item)

        valid_accepted_entries.append(entry)

    if all_untranslated:
        raise TranslationMissingError(all_untranslated)

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
            # Which of this row's fields carry the map's words rather than the
            # source's own. Collected as the translations happen, not worked
            # out from the stored text afterwards.
            authored: set[str] = set()
            raw_label = str(entry.get("label") or entry.get("name") or identifier)
            label = _translate_field(raw_label, translation_map, identifier, "label", authored)
            # The room text goes through the map like every other string. It is
            # the reason this import exists, and storing it as the source wrote
            # it puts the source's own script in a seed file - invisibly, since
            # `ensure_ascii=True` writes it back out as \\u escapes that the
            # repository's CJK rule cannot see.
            theme_text = _translate_field(
                _extract_theme_text(entry), translation_map, identifier, "place", authored
            )

            # Build updated row
            new_row: dict[str, Any] = {
                "key": room_key,
                "label": label,
                # Every manner, and stored as an empty restriction rather than
                # as today's list of manners: the source says nothing about
                # manners, so the honest default is no restriction at all. A
                # frozen ["candid", "directed", "selfie"] would read the same
                # today and silently exclude every imported room from the
                # fourth manner the day one is written.
                "manners": [],
                # The place, and only the place. No register is written into an
                # imported room's text: the register belongs to the manner and
                # is joined on at compose time, so one place reads in whichever
                # voice the session is being shot in. Writing candid's capture
                # clause in here would make every one of these a candid room by
                # its first sentence, which is exactly what the split undid.
                "place": theme_text,
                "identifier": identifier,
                "source_library": entry.get("library"),
            }
            new_row["offers"] = derive_offers(entry, theme_text)
            # Read off the room's own text, at import, and stored. The gate
            # resolves against this and never re-reads the composed look.
            new_row["multi_body"] = derive_multi_body(theme_text)
            # The entry author's own reasons - why the room is shaped this way,
            # what breaks it - kept in ONE field rather than as loose top-level
            # keys. The picker has to show all of it, and a picker that has to
            # know which field names count as guidance is a second copy of
            # `is_guidance_field` living in the frontend, free to disagree with
            # this one. Always written, empty dict included, for the same
            # reason `authored` always is: a reader must not have to tell "this
            # room carries no guidance" from "this row predates the field".
            guidance: dict[str, Any] = {}
            for name in sorted(entry):
                if is_guidance_field(name):
                    guidance[name] = _translate_field(
                        entry[name], translation_map, identifier,
                        f"guidance.{name}", authored,
                    )
            new_row["guidance"] = guidance
            # Always written, even empty: a reader must not have to tell "this
            # row carries no translation" apart from "this row predates the
            # field", which is a guess it would get wrong in one direction.
            new_row["authored"] = sorted(authored)

            # Check if this row already existed
            existing_row = existing_by_id.get(identifier) or existing_by_id.get(room_key)
            if existing_row is not None:
                # What this project decided about the room, against what the
                # source owns. A manner restriction is somebody's judgement
                # about the place, written here by hand, so a re-import that
                # reset it to the import default would undo that judgement on
                # every run - the same way it would undo a verdict.
                for preserve_key in ("verdict", "sample_size", "manners", "manners_reason"):
                    if preserve_key in existing_row:
                        new_row[preserve_key] = existing_row[preserve_key]

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
            "accepted": dest_accepted.get(dest, 0),
            "refused": dest_refused.get(dest, 0),
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

    # A destination whose entries were all refused, or all skipped for an
    # empty theme, never gets an entries_by_dest key above, so it would
    # otherwise be missing from the report even though its refused count is
    # real. Nothing is written or registered for it - there is nothing to
    # write - so it is reported with zeros for the counts this run did not
    # produce, alongside the accepted/refused counts that are real.
    for dest in set(dest_accepted) | set(dest_refused):
        if dest not in dest_reports:
            dest_reports[dest] = {
                "accepted": dest_accepted.get(dest, 0),
                "refused": dest_refused.get(dest, 0),
                "written": 0,
                "created": 0,
                "created_keys": [],
                "updated": 0,
                "updated_keys": [],
                "unchanged": 0,
                "orphaned": 0,
                "orphaned_keys": [],
            }

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

    # A verdict this project measured against a room the import no longer
    # carries. Named here rather than acted on: the store is never written by
    # an import, in either direction, so an upstream that drops a room costs
    # nothing but a line in the report. Asked of the whole registry and not of
    # this run's destinations, because a verdict is orphaned by the rooms that
    # ARE here and not by the ones this run happened to touch.
    orphaned_verdicts = available_rooms(
        config=config, data_dir=target_data_dir)["orphaned_verdicts"]

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
        "orphaned_verdicts": orphaned_verdicts,
    }
