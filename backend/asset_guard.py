"""Refusal markers for asset imports.

Every marker matching non-English text is written using escape sequences so that
no tracked file carries a non-English glyph. All strings here are pure ASCII.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# Terms that mark an entry as school-set, in English.
# Removed: campus, dormitory, playground, gymnasium, academy (not minors-only).
# Removed: locker room and student, for the same reason, measured. A hospital
# staff locker room, an office one and a bathroom-mirror room were refused for
# a room every workplace has, and bare "student" refused a nurse and three
# petplay scenes. The school-set cases they were carrying are still refused by
# a narrower marker: "school", "high school", "middle school", "schoolgirl",
# "classroom", and their Chinese counterparts.
# Not added back as narrower markers: "private lesson", "tutoring". The one
# entry they would refuse is a home tutoring room naming no school and no
# age, and the operator accepted it after reading it. Three accepted entries
# in the source name a lesson or a student at all, and that is the only one
# of them that is about a lesson.
SCHOOL_MARKERS_EN: tuple[str, ...] = (
    "school",
    "schoolgirl",
    "schoolboy",
    "school uniform",
    "classroom",
    "classmate",
    "high school",
    "middle school",
    "junior high",
    "elementary school",
    "kindergarten",
    "blackboard",
    "chalkboard",
    "school desk",
    "sailor suit",
    "sailor uniform",
    "homework",
    "jc",
    "jk",
)

# Terms that mark an entry as school-set, in Simplified Chinese (escaped).
# Removed: \u5bbf\u820d (dormitory) and \u64cd\u573a (playground / sports field).
# Removed: \u5b66\u751f (student) with the English "student", and for the
# same reason. The age-bearing compounds stay markers in their own right -
# \u4e2d\u5b66\u751f, \u5c0f\u5b66\u751f, \u9ad8\u4e2d\u751f, \u521d\u4e2d\u751f, \u5973\u5b66\u751f - and each is matched as a
# substring, so nothing that names a school-age student stops being refused.
SCHOOL_MARKERS_ZH: tuple[str, ...] = (
    "\u5b66\u6821",  # school
    "\u6559\u5ba4",  # classroom
    "\u6821\u670d",  # school uniform
    # Retained because U+6821 explicitly denotes school, unlike their English counterparts.
    "\u6821\u56ed",  # campus
    "\u4e2d\u5b66",  # middle / high school
    "\u9ad8\u4e2d",  # high school
    "\u521d\u4e2d",  # junior high school
    "\u5c0f\u5b66",  # elementary school
    "\u5e7c\u513f\u56ed",  # kindergarten
    "\u5973\u9ad8\u4e2d\u751f",  # female high school student
    "\u5973\u521d\u4e2d\u751f",  # female junior high student
    "\u5973\u4e2d\u5b66\u751f",  # female secondary student
    "\u5973\u5b66\u751f",  # female student
    "\u9ad8\u4e2d\u751f",  # high school student
    "\u521d\u4e2d\u751f",  # junior high student
    "\u4e2d\u5b66\u751f",  # middle school student
    "\u5c0f\u5b66\u751f",  # elementary school student
    "\u5973\u5c0f\u5b66\u751f",  # female elementary student
    "\u8bfe\u684c",  # school desk
    "\u9ed1\u677f",  # blackboard
    "\u8bb2\u53f0",  # podium / teacher platform
    "\u540c\u5b66",  # classmate
    "\u6c34\u624b\u670d",  # sailor uniform
    "\u8bfe\u5ba4",  # classroom
    "\u653e\u5b66",  # after school
    "\u4e0a\u5b66",  # attend school
    "\u6821\u820d",  # school building
    "\u6821\u95e8",  # school gate
    "\u4e66\u5305",  # schoolbag
    "jk\u5236\u670d",  # jk uniform
    "jc\u5236\u670d",  # jc uniform
)

# Combined school markers across languages.
SCHOOL_MARKERS: tuple[str, ...] = SCHOOL_MARKERS_EN + SCHOOL_MARKERS_ZH

# Terms that represent adult university material accepted by the project.
ALLOW_LIST_EN: tuple[str, ...] = (
    "college student",
    "undergraduate student",
    "university student",
    "graduate student",
    "postgraduate",
)

# Escaped Chinese terms for adult university material accepted by the project.
ALLOW_LIST_ZH: tuple[str, ...] = (
    "\u5927\u5b66\u751f",  # university student
    "\u5927\u5b66",  # university
    "\u7814\u7a76\u751f",  # graduate student
)

# Combined allow-list terms.
ALLOW_LIST: tuple[str, ...] = ALLOW_LIST_EN + ALLOW_LIST_ZH

# Minor-coded body-profile keys, in English / alphanumeric.
# Removed "jc-" and "jk-" as redundant after token splitting.
MINOR_PROFILE_KEYS_EN: tuple[str, ...] = (
    "jc",
    "jk",
    "schoolgirl",
    "middle-school",
    "high-school",
    "junior-high",
    "elementary-school",
    "underage",
    "minor",
    "child",
    "teen",
    "teenager",
    "loli",
    "lolita",
)

# Minor-coded body-profile keys, in Simplified Chinese (escaped).
MINOR_PROFILE_KEYS_ZH: tuple[str, ...] = (
    "\u521d\u4e2d\u751f",  # junior high student
    "\u9ad8\u4e2d\u751f",  # high school student
    "\u4e2d\u5b66\u751f",  # middle school student
    "\u5c0f\u5b66\u751f",  # elementary school student
    "\u5973\u521d\u4e2d\u751f",  # female junior high student
    "\u5973\u9ad8\u4e2d\u751f",  # female high school student
    "\u5973\u4e2d\u5b66\u751f",  # female secondary student
    "\u5973\u5c0f\u5b66\u751f",  # female elementary student
    "\u5e7c\u5973",  # young girl
    "\u841d\u8389",  # loli
    "\u521d\u4e2d",  # junior high
    "\u9ad8\u4e2d",  # high school
)

# Combined minor-coded body-profile keys across languages.
MINOR_PROFILE_KEYS: tuple[str, ...] = MINOR_PROFILE_KEYS_EN + MINOR_PROFILE_KEYS_ZH

# Deny-list of refused source-library names. The operator fills this.
REFUSED_LIBRARIES: tuple[str, ...] = ()

# Source libraries this project does not adopt. This is not the deny-list and
# does not mean the material is forbidden - it means it is not what this app
# is for, so its entries reach no destination and none of its strings are ever
# translated.
#
# `amateurs` and `celebrities` are body and identity profiles. A written body
# beats the LoRA, so importing them is a way to stop photographing the
# character this app exists to photograph. `celebrities` is also 256 real
# people's names: there is no English for one, only a transliteration, and the
# result is still the person.
#
# There is no caller argument that adds to or removes from this, for the same
# reason the deny-list has none.
NOT_ADOPTED_LIBRARIES: tuple[str, ...] = ("amateurs", "celebrities")

# Refusal signal constants.
SIGNAL_LIBRARY: str = "library"
SIGNAL_IDENTIFIER: str = "identifier"
SIGNAL_TAGS: str = "tags"
SIGNAL_THEME_TEXT: str = "theme_text"
SIGNAL_MINOR_PROFILE_KEY: str = "minor_profile_key"
SIGNAL_NOT_ADOPTED: str = "not_adopted"

ALL_SIGNALS: tuple[str, ...] = (
    SIGNAL_LIBRARY,
    SIGNAL_NOT_ADOPTED,
    SIGNAL_MINOR_PROFILE_KEY,
    SIGNAL_IDENTIFIER,
    SIGNAL_TAGS,
    SIGNAL_THEME_TEXT,
)

# Every field an entry may carry prose in. All of them are checked.
TEXT_FIELDS: tuple[str, ...] = (
    "theme_text",
    "theme",
    "label",
    "text",
    "description",
    "notes",
    "prompt",
)

# Fields holding a list of interchangeable options rather than a description
# of the entry itself. A school-coded garment among a room's wardrobe options
# does not make the room school-set: the option is dropped and the entry
# stands. Only a list is treated this way. Written as a string the field is
# describing the entry, not offering choices, and refuses it like any prose.
OPTION_FIELDS: tuple[str, ...] = ("uniform_fit",)

# Every entry field name accepted as a keyword argument. A caller cannot pass
# bypass flags like allow_school=True expecting them to take effect.
ENTRY_FIELD_NAMES: frozenset[str] = frozenset(
    (
        "identifier",
        "id",
        "key",
        "library",
        "source_library",
        "lib",
        "profile_key",
        "profile",
        "body_profile",
        "kind",
        "tags",
        "tag",
    )
    + TEXT_FIELDS
)


def _mask_allow_list(text: str) -> str:
    """Mask allow-list phrases with spaces so nested school markers do not match."""
    # Mask Chinese allow-list terms (longest first)
    for phrase in sorted(ALLOW_LIST_ZH, key=len, reverse=True):
        if phrase in text:
            text = text.replace(phrase, " " * len(phrase))
    # Mask English allow-list terms on word boundaries (longest first)
    for phrase in sorted(ALLOW_LIST_EN, key=len, reverse=True):
        words = phrase.split()
        pattern = (
            r"(?<![a-zA-Z0-9])"
            + r"\s+".join(re.escape(w) for w in words)
            + r"s?(?![a-zA-Z0-9])"
        )
        text = re.sub(pattern, lambda m: " " * len(m.group(0)), text, flags=re.IGNORECASE)
    return text


def _match_tokens(text: str, markers: tuple[str, ...]) -> bool:
    """Match English markers on whole words/tokens after splitting on non-alphanumerics."""
    tokens = [t for t in re.split(r"[^a-zA-Z0-9]+", text.lower()) if t]
    if not tokens:
        return False
    for marker in markers:
        m_tokens = [t for t in re.split(r"[^a-zA-Z0-9]+", marker.lower()) if t]
        if not m_tokens:
            continue
        m_len = len(m_tokens)
        if any(tokens[i:i + m_len] == m_tokens for i in range(len(tokens) - m_len + 1)):
            return True
    return False


def _matches_school_markers(text: str) -> bool:
    """Check if text matches any school marker, respecting allow-list."""
    if not text:
        return False
    masked = _mask_allow_list(text)
    masked_lower = masked.lower()
    if any(m.lower() in masked_lower for m in SCHOOL_MARKERS_ZH):
        return True
    return _match_tokens(masked, SCHOOL_MARKERS_EN)


def _matches_profile_keys(key: str) -> bool:
    """Check if profile key matches any minor-coded profile key."""
    if not key:
        return False
    key_lower = key.lower()
    if any(m.lower() in key_lower for m in MINOR_PROFILE_KEYS_ZH):
        return True
    return _match_tokens(key, MINOR_PROFILE_KEYS_EN)


def guard_entry(
    entry: dict[str, Any] | None = None,
    *,
    refused_libraries: tuple[str, ...] | set[str] | list[str] = (),
    **kwargs: Any,
) -> tuple[str, str] | None:
    """Determine whether an asset import entry is refused and on which signal.

    Returns a tuple of (signal_name, identifier) if refused, or None if accepted.
    Never returns, embeds, or carries the entry's text.
    """
    # A caller argument is a person asking for the bypass, so unknown keyword
    # arguments raise rather than being silently ignored. By contrast, a bypass-
    # shaped key on the entry dict does not raise -- the entry is source material
    # this project did not write, and a single source file carrying
    # allow_school: true must not abort an import of five hundred rooms.
    unknown_kwargs = set(kwargs) - ENTRY_FIELD_NAMES
    if unknown_kwargs:
        unexpected = sorted(unknown_kwargs)[0]
        raise TypeError(f"guard_entry() got an unexpected keyword argument '{unexpected}'")

    data: dict[str, Any] = {}
    if entry is not None:
        data.update(entry)
    data.update(kwargs)

    # The entry is source material and never sets the deny-list. A caller may
    # add libraries for a test; it can never remove one.
    data.pop("refused_libraries", None)
    refused_libs = REFUSED_LIBRARIES + tuple(refused_libraries)

    identifier = str(data.get("identifier") or data.get("id") or data.get("key") or "")

    # Signal 1: source library
    library = str(data.get("library") or data.get("source_library") or data.get("lib") or "")
    if library:
        refused_set = {lib.lower() for lib in refused_libs}
        if library.lower() in refused_set:
            return (SIGNAL_LIBRARY, identifier)

        # Checked after the deny-list, so a library on both is reported as the
        # deny-list refusal: "we may not" outranks "we do not want to".
        if library.lower() in {lib.lower() for lib in NOT_ADOPTED_LIBRARIES}:
            return (SIGNAL_NOT_ADOPTED, identifier)

    # Signal 2: minor-coded profile key
    profile_key = str(
        data.get("profile_key")
        or data.get("profile")
        or data.get("body_profile")
        or ""
    )
    if not profile_key and data.get("kind") in ("profile", "body_profile"):
        profile_key = identifier
    if profile_key and _matches_profile_keys(profile_key):
        return (SIGNAL_MINOR_PROFILE_KEY, identifier)

    # Signal 3: identifier
    if identifier and _matches_school_markers(identifier):
        return (SIGNAL_IDENTIFIER, identifier)

    # Signal 4: tags
    tags_val = data.get("tags") if "tags" in data else data.get("tag")
    if tags_val is not None:
        if isinstance(tags_val, str):
            if _matches_school_markers(tags_val):
                return (SIGNAL_TAGS, identifier)
        elif hasattr(tags_val, "__iter__"):
            for tag in tags_val:
                if isinstance(tag, str) and _matches_school_markers(tag):
                    return (SIGNAL_TAGS, identifier)

    # Signal 5: theme text and label. Every string an entry carries is checked,
    # in any field and inside any list, not only the fields named in
    # TEXT_FIELDS. The source libraries keep their prose in fields this project
    # did not name - `scene_theme`, `keywords`, `uniform_fit`, `prop_hint`,
    # `pose_hint` - and a fixed list of field names is a list that drifts from
    # whatever the next library calls its prose. Reading only the first field
    # present would accept a school-set label whenever a harmless theme sits in
    # front of it, so all of them are read.
    checked_first = [f for f in TEXT_FIELDS if f in data]
    rest = [k for k in sorted(data.keys()) if k not in TEXT_FIELDS]
    for field in checked_first + rest:
        value = data.get(field)
        if isinstance(value, str):
            if _matches_school_markers(value):
                return (SIGNAL_THEME_TEXT, identifier)
        elif field in OPTION_FIELDS and isinstance(value, (list, tuple)):
            continue  # pruned item by item by prune_options, not refused whole
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and _matches_school_markers(item):
                    return (SIGNAL_THEME_TEXT, identifier)

    return None


def prune_options(entry: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Drop school-coded items from an entry's option fields.

    Returns the entry and the names of the fields an item was dropped from.
    The entry is copied, never edited in place: the caller's source data is
    read-only material. Carries no dropped text back to the caller.
    """
    touched: list[str] = []
    keeps: dict[str, list[Any]] = {}
    for field in OPTION_FIELDS:
        value = entry.get(field)
        if not isinstance(value, (list, tuple)):
            continue
        kept = [
            item for item in value
            if not (isinstance(item, str) and _matches_school_markers(item))
        ]
        if len(kept) != len(value):
            keeps[field] = kept
            touched.append(field)
    if not touched:
        # Nothing to drop: the caller's own entry object is handed back, so an
        # untouched entry stays the same object it went in as.
        return entry, []
    pruned = dict(entry)
    pruned.update(keeps)
    return pruned, touched


def guard_entries(
    entries: Iterable[dict[str, Any]],
    *,
    refused_libraries: tuple[str, ...] | set[str] | list[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Filter an iterable of asset entries and report counts and refused identifiers.

    Returns a tuple of (accepted_entries, report_dict).
    The report contains:
    - "accepted": count of accepted entries
    - "refused": count of refused entries
    - "by_signal": count per refusal signal (keyed by SIGNAL_* constants)
    - "by_library": count per refused source library
    - "refused_identifiers": list of refused identifiers in input order
    - "pruned_options": count of accepted entries an option was dropped from
    - "pruned_option_identifiers": their identifiers, in input order

    Never prints, logs, or includes any entry text in the report.
    """
    refused_libs = REFUSED_LIBRARIES + tuple(refused_libraries)
    seen_libs: set[str] = set()
    unique_refused_libs: list[str] = []
    for lib in refused_libs:
        if lib not in seen_libs:
            seen_libs.add(lib)
            unique_refused_libs.append(lib)

    by_library: dict[str, int] = {lib: 0 for lib in unique_refused_libs}
    lib_lookup: dict[str, str] = {lib.lower(): lib for lib in unique_refused_libs}

    by_signal: dict[str, int] = {sig: 0 for sig in ALL_SIGNALS}

    accepted: list[dict[str, Any]] = []
    refused_identifiers: list[str] = []
    pruned_identifiers: list[str] = []

    for entry in entries:
        decision = guard_entry(entry, refused_libraries=refused_libraries)
        if decision is None:
            kept, touched = prune_options(entry)
            accepted.append(kept)
            if touched:
                identifier = str(
                    entry.get("identifier")
                    or entry.get("id")
                    or entry.get("key")
                    or ""
                )
                pruned_identifiers.append(identifier)
        else:
            signal, identifier = decision
            refused_identifiers.append(identifier)
            by_signal[signal] = by_signal.get(signal, 0) + 1
            if signal == SIGNAL_LIBRARY:
                raw_lib = str(
                    entry.get("library")
                    or entry.get("source_library")
                    or entry.get("lib")
                    or ""
                )
                canonical_lib = lib_lookup.get(raw_lib.lower(), raw_lib)
                by_library[canonical_lib] = by_library.get(canonical_lib, 0) + 1

    report: dict[str, Any] = {
        "accepted": len(accepted),
        "refused": len(refused_identifiers),
        "by_signal": by_signal,
        "by_library": by_library,
        "refused_identifiers": refused_identifiers,
        "pruned_options": len(pruned_identifiers),
        "pruned_option_identifiers": pruned_identifiers,
    }
    return accepted, report

