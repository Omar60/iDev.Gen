"""Refusal markers for asset imports.

Every marker matching non-English text is written using escape sequences so that
no tracked file carries a non-English glyph. All strings here are pure ASCII.
"""
from __future__ import annotations

# Terms that mark an entry as school-set, in English.
SCHOOL_MARKERS_EN: tuple[str, ...] = (
    "school",
    "schoolgirl",
    "schoolboy",
    "school uniform",
    "classroom",
    "classmate",
    "student",
    "campus",
    "high school",
    "middle school",
    "junior high",
    "elementary school",
    "kindergarten",
    "academy",
    "locker room",
    "blackboard",
    "chalkboard",
    "school desk",
    "gymnasium",
    "playground",
    "dormitory",
    "sailor suit",
    "sailor uniform",
    "homework",
    "jc",
    "jk",
)

# Terms that mark an entry as school-set, in Simplified Chinese (escaped).
SCHOOL_MARKERS_ZH: tuple[str, ...] = (
    "\u5b66\u6821",  # school
    "\u6559\u5ba4",  # classroom
    "\u5b66\u751f",  # student
    "\u6821\u670d",  # school uniform
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
    "\u64cd\u573a",  # playground / sports field
    "\u5bbf\u820d",  # dormitory
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
SCHOOL_MARKERS_SET: frozenset[str] = frozenset(SCHOOL_MARKERS)

# Minor-coded body-profile keys, in English / alphanumeric.
MINOR_PROFILE_KEYS_EN: tuple[str, ...] = (
    "jc",
    "jk",
    "jc-",
    "jk-",
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
MINOR_PROFILE_KEYS_SET: frozenset[str] = frozenset(MINOR_PROFILE_KEYS)

# Task 1.2 fills the refused source-library names.
REFUSED_LIBRARIES: tuple[str, ...] = ()
REFUSED_LIBRARY_NAMES: tuple[str, ...] = REFUSED_LIBRARIES
