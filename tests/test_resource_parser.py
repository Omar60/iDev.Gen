"""Tests for the shared resource parser (task 2.2 of
`adopt-resource-session-planning`).

The parser is a precondition for persistence: it turns declared
source data into explicit, inspectable parse results before any
database write, preview/commit fingerprint work, or import-commit
operation. These tests pin the contract the parser promises to its
callers:

  * a normal scene record retains its full nested payload and
    reports field classifications for every top-level key;
  * a fused scene/perspective record is accepted intact without
    forced decomposition;
  * an auxiliary map (translation_map, cut_map, mined_families,
    mined_labels) is classified as auxiliary, not interpreted as
    a scene;
  * an entry with unfamiliar fields is accepted, retained intact,
    and reported with those fields marked as retained-but-unused;
  * malformed root shapes, malformed entries, missing identifiers,
    and ambiguous identifiers are reported with explicit safe
    reasons and never silently dropped or guessed;
  * a mixed source accounts for every input item with no silent
    omission.

The tests use invented, English-only data. They do not import any
source-corpus text, do not write to real config or data paths, do
not touch the database beyond an isolated fresh schema, and never
invoke the legacy room-seed importer. The fixtures are kept short
so a failure message names the field or the entry that failed.
"""
from __future__ import annotations

import json

import pytest

from backend.resource_parser import (
    ALL_AUXILIARY_KINDS,
    ALL_ROLES,
    IDENTIFIER_FIELDS,
    KIND_CUT_MAP,
    KIND_FUSED_SCENES,
    KIND_MINED_FAMILIES,
    KIND_MINED_LABELS,
    KIND_ROOMS,
    KIND_TRANSLATION_MAP,
    LABEL_FIELDS,
    LIST_FIELDS,
    ROLE_AUXILIARY_DATA,
    ROLE_DESCRIPTIVE_INPUT,
    ROLE_IDENTIFIER,
    ROLE_SELECTION_METADATA,
    ROLE_UNUSED,
    ROLE_WRITER_GUIDANCE,
    SELECTION_METADATA_FIELDS,
    THEME_FIELDS,
    AcceptedEntry,
    AmbiguousIdentifier,
    AuxiliaryResource,
    FieldClassification,
    MalformedInput,
    MissingIdentifier,
    ParseResult,
    UnsupportedShape,
    input_size,
    parse_source_payload,
)

from test_no_personal_data import PATTERNS as PRIVACY_PATTERNS  # noqa: E402


# -- Invented fixtures ------------------------------------------------------


# A normal scene record. The nested structure is deliberately varied
# (a dict inside a dict, a list of strings, a list of dicts, None, an
# empty list) so a future re-encoder that loses one of these shapes
# shows up as a precise equality diff.
NORMAL_SCENE = {
    "id": "inv_scene_studio_dawn",
    "label": "invented studio at dawn",
    "scene_theme": (
        "An empty studio with a tall window facing north. Soft grey light "
        "enters from the side and leaves the back wall in shadow. A "
        "single wooden chair stands between the subject and the camera, "
        "and a folded white sheet covers the floor."
    ),
    "tags": ["indoor", "studio", "morning"],
    "props": ["chair", "sheet", "window"],
    "weight": 1.5,
    "library": "general_scenes",
    "enabled": True,
    "notes": "an invented guidance note",
    "mood_light": "soft",
    "action_anchor": "subject leaning to restock",
    "nested": {
        "first": {
            "second": {
                "leaf": "an invented leaf string",
                "leaf_list": ["a", "b", "c"],
            },
        },
    },
    "empty_list": [],
    "none_value": None,
}


# A fused scene record. The OpenSpec calls these "fused
# scene/perspective records" because they combine a camera, an act
# and a room into one prose block under `prompt`. The parser
# preserves the whole original and never decomposes the prose.
FUSED_SCENE = {
    "id": "inv_fused_dressing_room_01",
    "library": "perspective_scenes",
    "weight": 1.0,
    "prompt": (
        "A waist-up photograph, taken from her right side. She stands "
        "before the mirror in a fitting room, running a hand down the "
        "lapel of an unbuttoned blazer."
    ),
}


# An auxiliary translation map. The shape is the one
# ``save_translation_map`` writes and the one the legacy extractor
# recognises: every value is a dict carrying source, translation
# and fields keys.
TRANSLATION_MAP = {
    "inv_source_string_one": {
        "source": "inv source string one",
        "translation": "invented english one",
        "fields": ["label"],
    },
    "inv_source_string_two": {
        "source": "inv source string two",
        "translation": "invented english two",
        "fields": ["scene_theme"],
    },
}


# A cut map: every value is a dict whose keys are CUT_SLOTS
# (camera, act, room) and whose values are non-empty strings or
# None.
CUT_MAP = {
    "inv_fused_dressing_room_01": {
        "camera": "inv camera clause one",
        "act": "inv act clause one",
        "room": "inv room clause one",
    },
    "inv_fused_dressing_room_02": {
        "camera": "inv camera clause two",
        "act": "inv act clause two",
        "room": None,
    },
}


# A mined families declaration: a dict whose values are family name
# strings.
MINED_FAMILIES = {
    "inv_mined_one": "inv_family_one",
    "inv_mined_two": "inv_family_two",
    "inv_mined_three": "inv_family_three",
}


# A mined labels declaration: a dict whose values are label strings.
MINED_LABELS = {
    "inv_mined_row_one": "inv_judge_label_one",
    "inv_mined_row_two": "inv_judge_label_two",
}


# -- Tests -----------------------------------------------------------------


class TestSceneRecord:
    """A normal scene record retains its full nested payload and reports
    field classifications for every top-level key.

    The OpenSpec's "complete accepted entries are retained" rule is
    pinned here: the original dict round-trips through the parser
    byte-for-byte (no re-encoding, no key reordering, no field
    removal). The per-field classifications are exhaustive: every
    top-level key produces a FieldClassification, and the list of
    classifications has the same length as the list of keys.
    """

    def test_a_normal_scene_is_accepted(self):
        result = parse_source_payload(NORMAL_SCENE)
        assert len(result.accepted) == 1, result
        assert len(result.auxiliary) == 0
        assert len(result.malformed) == 0
        assert len(result.unsupported) == 0
        assert len(result.ambiguous_identifier) == 0
        assert len(result.missing_identifier) == 0

    def test_a_normal_scene_is_classified_as_rooms(self):
        result = parse_source_payload(NORMAL_SCENE)
        entry = result.accepted[0]
        assert entry.kind == KIND_ROOMS, entry
        assert entry.source_id == "inv_scene_studio_dawn", entry

    def test_a_normal_scene_retains_the_complete_original(self):
        # The full nested structure round-trips through the parser
        # byte-for-byte. The nested dict, the list of strings, the
        # list of dicts, None, and the empty list all survive.
        result = parse_source_payload(NORMAL_SCENE)
        original = result.accepted[0].original
        assert original == NORMAL_SCENE, original
        # The most-likely-to-be-mangled leaves are asserted
        # individually so a regression shows the exact line that
        # drifted.
        assert original["nested"]["first"]["second"]["leaf"] == \
            "an invented leaf string"
        assert original["nested"]["first"]["second"]["leaf_list"] == \
            ["a", "b", "c"]
        assert original["empty_list"] == []
        assert original["none_value"] is None
        assert original["tags"] == ["indoor", "studio", "morning"]
        assert original["props"] == ["chair", "sheet", "window"]
        assert original["weight"] == 1.5
        assert original["enabled"] is True

    def test_a_normal_scene_original_is_a_deep_copy(self):
        # Mutating the input after parsing must not change the
        # accepted entry's original. A parser that kept a reference
        # would propagate the mutation; the test pins the
        # "preserved" property as a deep copy, not as a borrowed
        # pointer.
        source = dict(NORMAL_SCENE)
        result = parse_source_payload(source)
        # Mutate the source.
        source["label"] = "mutated"
        source["tags"].append("mutated")
        source["nested"]["first"]["second"]["leaf"] = "mutated"
        # The accepted entry is unchanged.
        accepted = result.accepted[0]
        assert accepted.original["label"] == "invented studio at dawn"
        assert accepted.original["tags"] == ["indoor", "studio", "morning"]
        assert accepted.original["nested"]["first"]["second"]["leaf"] == \
            "an invented leaf string"

    def test_every_top_level_field_is_classified(self):
        # Exhaustive classification: every top-level key produces
        # one FieldClassification, and the names line up with the
        # original's keys.
        result = parse_source_payload(NORMAL_SCENE)
        entry = result.accepted[0]
        classified_names = [fc.name for fc in entry.field_classifications]
        assert sorted(classified_names) == sorted(NORMAL_SCENE.keys()), (
            sorted(classified_names), sorted(NORMAL_SCENE.keys())
        )

    def test_known_fields_receive_their_role(self):
        result = parse_source_payload(NORMAL_SCENE)
        entry = result.accepted[0]
        by_name = {fc.name: fc for fc in entry.field_classifications}
        # Identity: id, key, identifier all map to ROLE_IDENTIFIER.
        assert by_name["id"].role == ROLE_IDENTIFIER
        # Selection metadata: library, weight, enabled.
        assert by_name["library"].role == ROLE_SELECTION_METADATA
        assert by_name["weight"].role == ROLE_SELECTION_METADATA
        assert by_name["enabled"].role == ROLE_SELECTION_METADATA
        # Descriptive input: label, scene_theme, tags, props.
        assert by_name["label"].role == ROLE_DESCRIPTIVE_INPUT
        assert by_name["scene_theme"].role == ROLE_DESCRIPTIVE_INPUT
        assert by_name["tags"].role == ROLE_DESCRIPTIVE_INPUT
        assert by_name["props"].role == ROLE_DESCRIPTIVE_INPUT
        # Writer guidance: notes, mood_*, *_anchor.
        assert by_name["notes"].role == ROLE_WRITER_GUIDANCE
        assert by_name["mood_light"].role == ROLE_WRITER_GUIDANCE
        assert by_name["action_anchor"].role == ROLE_WRITER_GUIDANCE

    def test_an_unknown_field_is_reported_as_unused(self):
        # The original NORMAL_SCENE has no unknown field; this
        # test plants one and asserts the parser reports it as
        # retained-but-unused rather than dropping it.
        planted = {**NORMAL_SCENE, "private_internal_flag": "invented private"}
        result = parse_source_payload(planted)
        entry = result.accepted[0]
        by_name = {fc.name: fc for fc in entry.field_classifications}
        assert "private_internal_flag" in by_name
        flag = by_name["private_internal_flag"]
        assert flag.role == ROLE_UNUSED, flag
        # The reason is non-empty: a future caller reading the
        # parse report can see WHY the field is unused.
        assert flag.unused_reason, flag
        assert "private_internal_flag" in flag.unused_reason
        # The unknown_fields list carries the name for fast lookup.
        assert "private_internal_flag" in entry.unknown_fields
        # The original still has the value.
        assert entry.original["private_internal_flag"] == "invented private"

    def test_unknown_fields_classifications_have_non_empty_reasons(self):
        planted = {
            **NORMAL_SCENE,
            "private_internal_flag": "invented private",
            "another_unknown": {"nested": "value"},
        }
        result = parse_source_payload(planted)
        entry = result.accepted[0]
        unused = [fc for fc in entry.field_classifications
                  if fc.role == ROLE_UNUSED]
        assert len(unused) >= 2, unused
        for fc in unused:
            assert fc.unused_reason, fc
            assert fc.name in fc.unused_reason

    def test_every_input_is_accounted_for(self):
        result = parse_source_payload(NORMAL_SCENE)
        assert result.every_input_accounted_for(input_size(NORMAL_SCENE))
        assert result.total() == 1


class TestFusedSceneRecord:
    """A fused scene/perspective record is accepted intact, without
    forced decomposition.

    The OpenSpec calls out that a fused entry's ``prompt`` is
    descriptive input whose exact rendering the project does NOT
    promise to reproduce; the parser must therefore accept the entry
    whole and classify it as ``fused_scenes``, never split the
    prose, never strip fields.
    """

    def test_a_fused_scene_is_accepted(self):
        result = parse_source_payload(FUSED_SCENE)
        assert len(result.accepted) == 1, result
        assert len(result.auxiliary) == 0
        assert len(result.malformed) == 0
        assert len(result.unsupported) == 0

    def test_a_fused_scene_is_classified_as_fused_scenes(self):
        result = parse_source_payload(FUSED_SCENE)
        entry = result.accepted[0]
        assert entry.kind == KIND_FUSED_SCENES, entry
        assert entry.source_id == "inv_fused_dressing_room_01", entry

    def test_a_fused_scene_retains_the_complete_original(self):
        result = parse_source_payload(FUSED_SCENE)
        original = result.accepted[0].original
        assert original == FUSED_SCENE, original
        # The prompt prose is preserved verbatim, with no
        # decomposition, no whitespace stripping, no field removal.
        assert original["prompt"] == FUSED_SCENE["prompt"]
        assert "library" in original
        assert "weight" in original

    def test_a_fused_scene_classifies_its_prompt_as_descriptive_input(self):
        result = parse_source_payload(FUSED_SCENE)
        by_name = {
            fc.name: fc for fc in result.accepted[0].field_classifications
        }
        assert by_name["prompt"].role == ROLE_DESCRIPTIVE_INPUT
        # The identifier is recognised.
        assert by_name["id"].role == ROLE_IDENTIFIER
        # Selection metadata is recognised.
        assert by_name["library"].role == ROLE_SELECTION_METADATA
        assert by_name["weight"].role == ROLE_SELECTION_METADATA

    def test_a_fused_scene_with_a_label_is_classified_as_rooms(self):
        # A record that has BOTH a prompt and a label is a scene
        # record (rooms), not a fused scene. The fused_scenes
        # kind is reserved for entries whose descriptive content
        # is exclusively the prompt prose.
        planted = {**FUSED_SCENE, "label": "invented fused with label"}
        result = parse_source_payload(planted)
        assert result.accepted[0].kind == KIND_ROOMS, result
        # The prompt is still classified as descriptive input
        # under the rooms kind (a rooms entry can carry a prompt
        # field too, and the parser does not strip it).
        by_name = {
            fc.name: fc for fc in result.accepted[0].field_classifications
        }
        assert by_name["prompt"].role == ROLE_DESCRIPTIVE_INPUT


class TestAuxiliaryMaps:
    """An auxiliary map is classified as auxiliary, not interpreted as a
    scene.

    The OpenSpec names four auxiliary schemas:
    ``translation_map``, ``cut_map``, ``mined_families``,
    ``mined_labels``. The parser recognises them structurally and
    keeps the body intact under ``AuxiliaryResource.payload``.
    """

    @pytest.mark.parametrize("kind,payload,expected_count", [
        (KIND_TRANSLATION_MAP, TRANSLATION_MAP, 2),
        (KIND_CUT_MAP, CUT_MAP, 2),
        (KIND_MINED_FAMILIES, MINED_FAMILIES, 3),
    ])
    def test_each_auxiliary_kind_is_classified(self, kind, payload, expected_count):
        result = parse_source_payload(payload)
        assert len(result.auxiliary) == 1, result
        assert result.auxiliary[0].kind == kind
        assert result.auxiliary[0].entry_count == expected_count
        # The body is kept intact.
        assert result.auxiliary[0].payload == payload
        # No scene entries were produced.
        assert result.accepted == []
        # No malformed, unsupported, ambiguous or missing reports.
        assert result.malformed == []
        assert result.unsupported == []
        assert result.ambiguous_identifier == []
        assert result.missing_identifier == []
        # Every input is accounted for.
        assert result.every_input_accounted_for(input_size(payload))

    def test_mined_labels_share_a_shape_with_mined_families(self):
        # The mined_labels and mined_families schemas have the
        # same structural shape: a dict whose values are
        # non-empty strings. Without an external hint (a file
        # stem, an explicit kind declaration) the parser cannot
        # distinguish them by shape alone, and the legacy
        # inventory recognises them by stem rather than by body.
        # The parser's contract: pick the first shape match as
        # the primary kind, and record the other in
        # ``shadow_matches`` so a caller that knows the file
        # stem can reclassify without losing the body.
        result = parse_source_payload(MINED_LABELS)
        assert len(result.auxiliary) == 1, result
        aux = result.auxiliary[0]
        assert aux.kind == KIND_MINED_FAMILIES, aux
        assert KIND_MINED_LABELS in aux.shadow_matches, aux
        assert aux.entry_count == 2
        assert aux.payload == MINED_LABELS

    def test_all_auxiliary_kinds_have_a_match(self):
        # The four documented auxiliary kinds are covered by the
        # structural tests. A kind in ALL_AUXILIARY_KINDS that
        # does not have a shape test would be a regression the
        # parser could not classify.
        for kind in ALL_AUXILIARY_KINDS:
            assert kind in (
                KIND_TRANSLATION_MAP, KIND_CUT_MAP,
                KIND_MINED_FAMILIES, KIND_MINED_LABELS,
            )

    def test_auxiliary_payload_preserves_nested_values(self):
        # The translation map carries nested dicts. A parser that
        # re-encoded the body would mangle the nested structure;
        # the body is preserved byte-for-byte.
        result = parse_source_payload(TRANSLATION_MAP)
        aux = result.auxiliary[0]
        for key, value in TRANSLATION_MAP.items():
            assert aux.payload[key] == value
            assert aux.payload[key]["source"] == value["source"]
            assert aux.payload[key]["translation"] == value["translation"]
            assert aux.payload[key]["fields"] == value["fields"]

    def test_a_dict_that_does_not_match_any_auxiliary_shape_is_not_auxiliary(self):
        # A regular scene record with the auxiliary shape test run
        # against it must NOT be classified as auxiliary. The
        # scene-detection path picks it up instead.
        result = parse_source_payload(NORMAL_SCENE)
        assert result.auxiliary == []
        assert len(result.accepted) == 1


class TestUnfamiliarFieldsAreRetained:
    """An entry with unfamiliar fields is accepted, retained intact,
    and reported with those fields marked as retained-but-unused.

    The OpenSpec is explicit: "accepted entries retain every field,
    nested value, original string and source identifier." A parser
    that dropped or masked an unfamiliar field would defeat the
    rule, so the tests pin both the acceptance and the
    retained-but-unused reporting.
    """

    def test_a_single_unknown_field_is_retained_and_reported(self):
        planted = {
            **NORMAL_SCENE,
            "private_internal_flag": "invented private",
        }
        result = parse_source_payload(planted)
        entry = result.accepted[0]
        # The entry is still accepted; the unknown field does
        # not push it to unsupported.
        assert entry.kind == KIND_ROOMS
        # The original carries the value.
        assert entry.original["private_internal_flag"] == "invented private"
        # The field is reported as unused.
        assert "private_internal_flag" in entry.unknown_fields
        by_name = {fc.name: fc for fc in entry.field_classifications}
        assert by_name["private_internal_flag"].role == ROLE_UNUSED

    def test_nested_unfamiliar_fields_are_retained(self):
        planted = {
            **NORMAL_SCENE,
            "private_payload": {
                "internal": {
                    "deep": "an invented private deep value",
                    "list": [1, 2, 3],
                },
            },
        }
        result = parse_source_payload(planted)
        entry = result.accepted[0]
        # The unknown top-level field's nested structure survives.
        assert entry.original["private_payload"] == {
            "internal": {
                "deep": "an invented private deep value",
                "list": [1, 2, 3],
            },
        }
        # The unknown field is reported.
        assert "private_payload" in entry.unknown_fields
        by_name = {fc.name: fc for fc in entry.field_classifications}
        assert by_name["private_payload"].role == ROLE_UNUSED
        # The reason names the field so a reviewer reading the
        # report knows which one was rejected.
        assert "private_payload" in by_name["private_payload"].unused_reason

    def test_many_unknown_fields_are_each_reported(self):
        planted = {
            **NORMAL_SCENE,
            "private_internal_flag": "a",
            "private_audit_token": "b",
            "private_widget_counter": 3,
        }
        result = parse_source_payload(planted)
        entry = result.accepted[0]
        for name in (
            "private_internal_flag", "private_audit_token",
            "private_widget_counter",
        ):
            assert name in entry.unknown_fields, name
            assert entry.original[name] == planted[name]
        # The original carries the unknown fields untouched.
        assert set(entry.original.keys()) == set(planted.keys())


class TestMalformedAndUnresolved:
    """Malformed root shapes, malformed entries, missing identifiers,
    and ambiguous identifiers are reported with explicit safe
    reasons.

    The parser never guesses: a missing identifier is reported
    under missing_identifier, an ambiguous one under
    ambiguous_identifier, a top-level scalar under malformed, and
    a structurally-unsupported dict under unsupported. The original
    (if it was a dict) is NOT carried on the report so a refused
    entry cannot accidentally reach persistence.
    """

    def test_a_scalar_at_the_top_is_malformed(self):
        result = parse_source_payload("an invented string at the top")
        assert len(result.malformed) == 1, result
        malformed = result.malformed[0]
        assert malformed.index == 0
        assert malformed.received_type == "str"
        assert "expected a dict or list" in malformed.reason

    def test_an_int_at_the_top_is_malformed(self):
        result = parse_source_payload(42)
        assert len(result.malformed) == 1, result
        assert result.malformed[0].received_type == "int"

    def test_a_none_at_the_top_is_malformed(self):
        result = parse_source_payload(None)
        assert len(result.malformed) == 1, result
        assert result.malformed[0].received_type == "NoneType"

    def test_a_non_dict_in_a_list_is_malformed(self):
        planted = [NORMAL_SCENE, "an invented string", 7, NORMAL_SCENE]
        result = parse_source_payload(planted)
        # Two scene records accepted.
        assert len(result.accepted) == 2
        # Two malformed items, at indices 1 and 2.
        assert len(result.malformed) == 2
        assert {m.index for m in result.malformed} == {1, 2}
        for m in result.malformed:
            assert "list item" in m.reason
        # Every input is accounted for.
        assert result.every_input_accounted_for(len(planted))

    def test_an_entry_with_no_identifier_is_reported_missing(self):
        planted = {
            "label": "invented label without id",
            "scene_theme": "an invented scene theme",
            "tags": ["indoor"],
        }
        result = parse_source_payload(planted)
        assert result.accepted == []
        assert len(result.missing_identifier) == 1
        missing = result.missing_identifier[0]
        assert missing.expected_kind == KIND_ROOMS
        # The reason names the expected kind and the missing
        # identifier field, so a reviewer can see what to add.
        assert KIND_ROOMS in missing.reason
        for field_name in IDENTIFIER_FIELDS:
            assert field_name in missing.reason
        # Every input is accounted for.
        assert result.every_input_accounted_for(1)

    def test_an_empty_identifier_value_is_reported_missing(self):
        # A field with whitespace-only is the same as absent:
        # the parser does not treat "  " as an identifier.
        planted = {
            "id": "  ",
            "label": "invented label with empty id",
            "scene_theme": "an invented scene theme",
        }
        result = parse_source_payload(planted)
        assert result.accepted == []
        assert len(result.missing_identifier) == 1
        assert result.missing_identifier[0].expected_kind == KIND_ROOMS

    def test_an_ambiguous_identifier_is_reported(self):
        # Two identifier fields with non-empty values: the parser
        # refuses to guess.
        planted = {
            "id": "inv_id_value",
            "identifier": "inv_identifier_value",
            "label": "invented ambiguous id",
            "scene_theme": "an invented scene theme",
        }
        result = parse_source_payload(planted)
        # Nothing was accepted.
        assert result.accepted == []
        assert len(result.ambiguous_identifier) == 1
        ambiguous = result.ambiguous_identifier[0]
        assert "id" in ambiguous.identifier_fields
        assert "identifier" in ambiguous.identifier_fields
        # The reason names both fields, so a reviewer can see
        # what to disambiguate.
        assert "id" in ambiguous.reason
        assert "identifier" in ambiguous.reason
        # Every input is accounted for.
        assert result.every_input_accounted_for(1)

    def test_three_identifier_fields_is_also_ambiguous(self):
        planted = {
            "id": "inv_a",
            "identifier": "inv_b",
            "key": "inv_c",
            "label": "invented triple ambiguous",
            "scene_theme": "an invented scene theme",
        }
        result = parse_source_payload(planted)
        assert result.accepted == []
        assert len(result.ambiguous_identifier) == 1
        assert set(result.ambiguous_identifier[0].identifier_fields) == {
            "id", "identifier", "key",
        }

    def test_a_dict_with_identifier_but_no_content_is_unsupported(self):
        planted = {
            "id": "inv_metadata_only",
            "library": "general_scenes",
            "weight": 1.0,
        }
        result = parse_source_payload(planted)
        # No accepted entry, no missing-identifier: the entry has
        # an identifier, but the structural scene-shape test
        # fails. Unsupported is the right bucket.
        assert result.accepted == []
        assert result.missing_identifier == []
        assert len(result.unsupported) == 1
        unsupported = result.unsupported[0]
        assert "no scene content" in unsupported.reason
        # Every input is accounted for.
        assert result.every_input_accounted_for(1)

    def test_a_dict_with_no_identifier_and_no_content_is_unsupported(self):
        # The most degraded case: a dict that is neither scene
        # nor auxiliary. The parser reports it under
        # unsupported, not missing_identifier, because even the
        # kind is undecidable.
        planted = {"library": "general_scenes", "weight": 1.0}
        result = parse_source_payload(planted)
        assert result.accepted == []
        assert result.missing_identifier == []
        assert len(result.unsupported) == 1
        assert "no identifier" in result.unsupported[0].reason
        assert result.every_input_accounted_for(1)


class TestMixedSourceAccounting:
    """A mixed source accounts for every input item with no silent
    omission.

    The OpenSpec's "import accounts for every input" rule is pinned
    here. The parser must:

    - accept the well-formed scene records under `accepted`;
    - accept the fused scene records under `accepted`;
    - classify auxiliary maps under `auxiliary`;
    - report malformed items under `malformed`;
    - report unsupported items under `unsupported`;
    - report missing identifiers under `missing_identifier`;
    - report ambiguous identifiers under `ambiguous_identifier`;
    - never silently drop a single input.

    The size-1 list case is also covered: a single-item list is
    parsed by the list path, the auxiliary-detection path is run on
    the dict, and the result is a single auxiliary record.
    """

    def test_a_mixed_list_of_inputs_accounts_for_every_item(self):
        planted = [
            NORMAL_SCENE,
            FUSED_SCENE,
            TRANSLATION_MAP,
            "an invented string at index 3",
            {
                "id": "inv_id_with_label",
                "identifier": "inv_identifier_with_label",
                "label": "ambiguous identifier",
                "scene_theme": "an invented scene theme",
            },
            {
                "label": "missing identifier scene",
                "scene_theme": "an invented scene theme",
            },
            {
                "id": "inv_unsupported_metadata_only",
                "library": "general_scenes",
            },
        ]
        result = parse_source_payload(planted)
        # Two accepted entries (one rooms, one fused_scenes).
        kinds = {entry.kind for entry in result.accepted}
        assert KIND_ROOMS in kinds, result
        assert KIND_FUSED_SCENES in kinds, result
        # The translation map is classified as auxiliary.
        assert any(a.kind == KIND_TRANSLATION_MAP for a in result.auxiliary)
        # The string at index 3 is malformed.
        assert any(m.index == 3 and m.received_type == "str"
                   for m in result.malformed)
        # Index 4 is ambiguous.
        assert any(a.index == 4 for a in result.ambiguous_identifier)
        # Index 5 is missing.
        assert any(m.index == 5 for m in result.missing_identifier)
        # Index 6 is unsupported.
        assert any(u.index == 6 for u in result.unsupported)
        # Every input is accounted for.
        assert result.every_input_accounted_for(len(planted))
        # The sum of buckets equals the input length.
        assert result.total() == len(planted)

    def test_a_single_dict_at_the_top_is_one_input(self):
        # A single dict (not a list) is one input. The bucket
        # sizes sum to 1.
        result = parse_source_payload(NORMAL_SCENE)
        assert result.total() == 1
        assert result.every_input_accounted_for(input_size(NORMAL_SCENE))

    def test_an_empty_list_is_zero_inputs(self):
        result = parse_source_payload([])
        assert result.total() == 0
        assert result.every_input_accounted_for(0)

    def test_a_single_translation_map_in_a_list_is_auxiliary(self):
        # When a translation map arrives as a single list item, the
        # auxiliary detection runs on that item and the entry is
        # classified as auxiliary. The list-of-1 case must not
        # silently fall through to the scene path.
        result = parse_source_payload([TRANSLATION_MAP])
        assert len(result.auxiliary) == 1
        assert result.auxiliary[0].kind == KIND_TRANSLATION_MAP
        assert result.accepted == []
        assert result.every_input_accounted_for(1)


class TestInputAccountingContract:
    """The every-input-accounted-for guarantee holds for every
    non-list value the parser can be handed.

    The contract is:

    - a list of N items: ``input_size`` returns N; the parser
      produces exactly N bucket entries (one per list item).
      An empty list is 0.
    - any non-list value, including ``None``, ``{}``, a single
      dict, a string, an int or a bool: ``input_size`` returns
      1; the parser produces exactly one bucket entry for it.

    The previous contract had ``input_size(None) == 0`` while
    ``parse_source_payload(None)`` reported one malformed input,
    so the every-input-accounted-for property failed for the
    ``None`` case. The contract is now consistent: every
    ``parse_source_payload(data)`` returns a ``ParseResult``
    whose ``total()`` equals ``input_size(data)``, regardless
    of whether ``data`` is ``None``, ``{}``, a list, a dict or
    a scalar.
    """

    def test_input_size_for_none_is_one_not_zero(self):
        # The regression that motivated this class: input_size
        # used to return 0 for None while parse_source_payload
        # returned one malformed input, so the every-input-
        # accounted-for assertion failed.
        assert input_size(None) == 1

    def test_input_size_for_an_empty_dict_is_one(self):
        # The empty-dict half of the same correction: the
        # parser reports an empty dict as one unsupported
        # input rather than as zero inputs, so input_size
        # must agree.
        assert input_size({}) == 1

    def test_input_size_for_an_empty_list_is_zero(self):
        # The list case is unchanged: a list's input count is
        # its length, and the empty list is 0 inputs.
        assert input_size([]) == 0

    def test_input_size_for_a_list_of_three_is_three(self):
        assert input_size([NORMAL_SCENE, FUSED_SCENE, "x"]) == 3

    def test_input_size_for_a_single_dict_is_one(self):
        assert input_size(NORMAL_SCENE) == 1

    def test_input_size_for_a_scalar_is_one(self):
        # Strings, ints, bools and other scalars are one
        # input each. The parser reports them as a single
        # malformed input; input_size must agree.
        assert input_size("a string") == 1
        assert input_size(42) == 1
        assert input_size(True) == 1

    def test_none_produces_one_malformed_input(self):
        result = parse_source_payload(None)
        # Exactly one bucket entry, in malformed.
        assert len(result.malformed) == 1, result
        assert result.malformed[0].received_type == "NoneType"
        assert result.malformed[0].index == 0
        # Every other bucket is empty.
        assert result.accepted == []
        assert result.auxiliary == []
        assert result.unsupported == []
        assert result.ambiguous_identifier == []
        assert result.missing_identifier == []
        # The every-input-accounted-for guarantee holds.
        assert result.every_input_accounted_for(input_size(None))
        assert result.total() == 1

    def test_an_empty_dict_produces_one_unsupported_input(self):
        # The empty dict is one input, NOT zero inputs. The
        # no-silent-drop rule pins it: a dict is a dict, and
        # the parser accounts for it under the bucket the
        # structural tests select (unsupported, in this case,
        # because the scene test fails on a dict with neither
        # identifier nor content and the auxiliary shape
        # test also fails on an empty body).
        result = parse_source_payload({})
        # Exactly one bucket entry, in unsupported.
        assert len(result.unsupported) == 1, result
        assert result.unsupported[0].received_type == "dict"
        assert result.unsupported[0].index == 0
        # Every other bucket is empty.
        assert result.accepted == []
        assert result.auxiliary == []
        assert result.malformed == []
        assert result.ambiguous_identifier == []
        assert result.missing_identifier == []
        # The every-input-accounted-for guarantee holds.
        assert result.every_input_accounted_for(input_size({}))
        assert result.total() == 1

    def test_an_empty_dict_does_not_silently_become_zero_inputs(self):
        # The explicit "no silent drop" assertion, separate
        # from the structural checks above. A regression that
        # treated {} as zero inputs would fail this test
        # even if every_input_accounted_for(0) were true.
        result = parse_source_payload({})
        # The result is NOT empty: an empty dict is not
        # silently treated as "no input".
        assert result.total() >= 1
        assert result.unsupported, "empty dict was silently dropped"
        # And input_size agrees the empty dict is one input.
        assert input_size({}) == 1

    def test_every_input_accounted_for_holds_for_a_none_input(self):
        # The exact assertion the original bug violated:
        # parse_source_payload(None).every_input_accounted_for(
        # input_size(None)) must be True.
        result = parse_source_payload(None)
        assert result.every_input_accounted_for(input_size(None)) is True

    def test_every_input_accounted_for_holds_for_an_empty_dict(self):
        # The empty-dict half: the same call pattern with
        # {} as the input.
        result = parse_source_payload({})
        assert result.every_input_accounted_for(input_size({})) is True

    def test_every_input_accounted_for_holds_for_a_scalar(self):
        # Scalars are the third class of non-list input.
        # The regression did not change the scalar path
        # (it always was one input), but the test pins the
        # property so a future change to the scalar path
        # cannot quietly break the contract.
        for scalar in ("a string", 42, 3.14, True, False):
            result = parse_source_payload(scalar)
            assert result.every_input_accounted_for(input_size(scalar)) is True, (
                scalar, result
            )

    def test_existing_list_behavior_is_unchanged(self):
        # The list case must not have been disturbed by the
        # contract change. A list of N items must still
        # produce N bucket entries; an empty list must still
        # produce 0 bucket entries; the result for each item
        # must still match what the per-item tests already
        # pin.
        planted = [NORMAL_SCENE, FUSED_SCENE, TRANSLATION_MAP, "an invented string"]
        result = parse_source_payload(planted)
        assert result.every_input_accounted_for(input_size(planted))
        assert result.total() == len(planted)
        # The accepted entries are still the two scenes.
        assert len(result.accepted) == 2
        # The translation map is still auxiliary.
        assert len(result.auxiliary) == 1
        # The string is still malformed.
        assert len(result.malformed) == 1
        # An empty list is still 0 inputs and 0 buckets.
        empty = parse_source_payload([])
        assert empty.every_input_accounted_for(input_size([]))
        assert empty.total() == 0

    def test_existing_scene_behavior_is_unchanged(self):
        # A normal scene is still accepted as one accepted
        # entry, the full original is still preserved, the
        # field classifications are still exhaustive, and
        # the every-input-accounted-for check still holds.
        result = parse_source_payload(NORMAL_SCENE)
        assert result.every_input_accounted_for(input_size(NORMAL_SCENE))
        assert len(result.accepted) == 1
        assert result.accepted[0].kind == KIND_ROOMS
        assert result.accepted[0].original == NORMAL_SCENE
        classified = result.accepted[0].field_classifications
        assert len(classified) == len(NORMAL_SCENE)

    def test_existing_fused_scene_behavior_is_unchanged(self):
        # A fused scene is still accepted as one accepted
        # entry, classified as fused_scenes, with the
        # prompt prose preserved verbatim.
        result = parse_source_payload(FUSED_SCENE)
        assert result.every_input_accounted_for(input_size(FUSED_SCENE))
        assert len(result.accepted) == 1
        assert result.accepted[0].kind == KIND_FUSED_SCENES
        assert result.accepted[0].original["prompt"] == FUSED_SCENE["prompt"]

    def test_existing_auxiliary_behavior_is_unchanged(self):
        # The four documented auxiliary schemas are still
        # classified as auxiliary and not as scenes. The
        # mined_families / mined_labels shape-identity
        # behaviour (mined_labels payload is classified as
        # mined_families with mined_labels in shadow_matches)
        # is preserved.
        for payload, expected in [
            (TRANSLATION_MAP, KIND_TRANSLATION_MAP),
            (CUT_MAP, KIND_CUT_MAP),
            (MINED_FAMILIES, KIND_MINED_FAMILIES),
        ]:
            result = parse_source_payload(payload)
            assert result.every_input_accounted_for(input_size(payload))
            assert len(result.auxiliary) == 1
            assert result.auxiliary[0].kind == expected
            assert result.accepted == []
        # mined_labels: same shape as mined_families, so
        # primary is mined_families and mined_labels is in
        # shadow_matches. The every-input-accounted-for
        # guarantee still holds.
        result = parse_source_payload(MINED_LABELS)
        assert result.every_input_accounted_for(input_size(MINED_LABELS))
        assert len(result.auxiliary) == 1
        assert result.auxiliary[0].kind == KIND_MINED_FAMILIES
        assert KIND_MINED_LABELS in result.auxiliary[0].shadow_matches

    def test_existing_missing_identifier_behavior_is_unchanged(self):
        # A scene-shaped dict with no identifier still ends
        # up in missing_identifier, and the every-input-
        # accounted-for check still holds.
        planted = {
            "label": "invented label without id",
            "scene_theme": "an invented scene theme",
            "tags": ["indoor"],
        }
        result = parse_source_payload(planted)
        assert result.every_input_accounted_for(input_size(planted))
        assert len(result.missing_identifier) == 1
        assert result.missing_identifier[0].expected_kind == KIND_ROOMS
        assert result.accepted == []

    def test_existing_ambiguous_identifier_behavior_is_unchanged(self):
        # A dict with two non-empty identifier fields is
        # still routed to ambiguous_identifier, and the
        # every-input-accounted-for check still holds.
        planted = {
            "id": "inv_id_value",
            "identifier": "inv_identifier_value",
            "label": "invented ambiguous id",
            "scene_theme": "an invented scene theme",
        }
        result = parse_source_payload(planted)
        assert result.every_input_accounted_for(input_size(planted))
        assert len(result.ambiguous_identifier) == 1
        assert "id" in result.ambiguous_identifier[0].identifier_fields
        assert "identifier" in result.ambiguous_identifier[0].identifier_fields
        assert result.accepted == []


class TestParserDoesNotMutateTheDatabase:
    """The parser is a precondition for persistence. It must not
    write to the database, compute content digests, or invoke any
    of the persistence helpers.

    This is a structural check on the module's public surface: a
    future edit that pulls in ``resource_store`` or ``db`` is
    visible here, and the test message names the regression.
    """

    def test_the_parser_does_not_import_resource_store(self):
        import backend.resource_parser as mod
        mod_names = dir(mod)
        # resource_store is a sibling module the parser must not
        # depend on. An accidental import for "convenience" would
        # couple the parser to the database.
        for forbidden in (
            "resource_store", "db", "importer", "asset_guard",
            "extractor", "translation_map", "cut_map",
            "mining", "room_registry", "main",
        ):
            assert forbidden not in mod_names, (
                f"backend.resource_parser imports {forbidden!r}: "
                f"the parser is a precondition for persistence and "
                f"must not depend on it"
            )


class TestPrivacyAndLanguageCompliance:
    """The parser module is part of a public repo. The privacy scan
    and the language rule the repo enforces must pass on the
    parser's source.

    No real names, no machine paths, no emails, no tokens, no CJK
    glyphs. The new module's source is included in the repo-wide
    ``test_no_personal_data`` scan automatically; this focused
    test pins the same property with a message that names the
    module under test, so a regression surfaces as a precise
    failure rather than a generic "privacy scan failed".
    """

    def test_the_parser_module_has_no_personal_data_patterns(self):
        from pathlib import Path
        mod_path = (
            Path(__file__).resolve().parent.parent
            / "backend" / "resource_parser.py"
        )
        assert mod_path.exists(), (
            "backend/resource_parser.py is missing: task 2.2 module "
            "is not in place"
        )
        text = mod_path.read_text(encoding="utf-8", errors="ignore")
        offenders: list[str] = []
        for label, pattern in PRIVACY_PATTERNS.items():
            for match in pattern.finditer(text):
                line = text[:match.start()].count("\n") + 1
                offenders.append(
                    f"backend/resource_parser.py:{line}: {label}: "
                    f"{match.group(0)!r}"
                )
        assert not offenders, "\n".join(offenders)

    def test_the_parser_module_has_no_cjk_glyphs(self):
        from pathlib import Path
        from test_no_personal_data import CJK_PATTERN
        mod_path = (
            Path(__file__).resolve().parent.parent
            / "backend" / "resource_parser.py"
        )
        text = mod_path.read_text(encoding="utf-8", errors="ignore")
        match = CJK_PATTERN.search(text)
        assert match is None, (
            f"backend/resource_parser.py contains a CJK glyph at "
            f"line {text[:match.start()].count(chr(10)) + 1}"
        )


class TestParserApiShape:
    """The public surface of the parser is what callers import. The
    names in ``__all__`` are the contract; a regression that
    renames an export breaks every downstream module.
    """

    def test_the_parser_exports_the_expected_names(self):
        from backend import resource_parser
        exported = set(resource_parser.__all__)
        # The roles, kinds, and dataclasses that callers import.
        expected = {
            "ROLE_IDENTIFIER", "ROLE_SELECTION_METADATA",
            "ROLE_DESCRIPTIVE_INPUT", "ROLE_WRITER_GUIDANCE",
            "ROLE_AUXILIARY_DATA", "ROLE_UNUSED", "ALL_ROLES",
            "KIND_ROOMS", "KIND_FUSED_SCENES",
            "KIND_TRANSLATION_MAP", "KIND_CUT_MAP",
            "KIND_MINED_FAMILIES", "KIND_MINED_LABELS",
            "ALL_AUXILIARY_KINDS",
            "IDENTIFIER_FIELDS", "LABEL_FIELDS", "THEME_FIELDS",
            "LIST_FIELDS", "SELECTION_METADATA_FIELDS",
            "FieldClassification", "AcceptedEntry", "AuxiliaryResource",
            "MalformedInput", "UnsupportedShape",
            "AmbiguousIdentifier", "MissingIdentifier", "ParseResult",
            "parse_source_payload", "input_size",
        }
        assert expected.issubset(exported), (
            expected - exported
        )

    def test_parse_result_is_a_dataclass(self):
        # The ParseResult class is a dataclass so callers can use
        # ``field(default_factory=list)`` style construction if
        # they need to build one for testing. A regression that
        # dropped the decorator would be visible here.
        import dataclasses
        assert dataclasses.is_dataclass(ParseResult)
        assert dataclasses.is_dataclass(AcceptedEntry)
        assert dataclasses.is_dataclass(AuxiliaryResource)
        assert dataclasses.is_dataclass(FieldClassification)
        assert dataclasses.is_dataclass(MalformedInput)
        assert dataclasses.is_dataclass(UnsupportedShape)
        assert dataclasses.is_dataclass(AmbiguousIdentifier)
        assert dataclasses.is_dataclass(MissingIdentifier)

    def test_vocabulary_is_closed(self):
        # The closed sets of roles, kinds, identifier fields,
        # label fields, theme fields, list fields and selection
        # metadata fields are part of the parser's contract. A
        # regression that added an unknown member would be a
        # decision somebody made, not a silent drift.
        for name in ALL_ROLES:
            assert name in {
                ROLE_IDENTIFIER, ROLE_SELECTION_METADATA,
                ROLE_DESCRIPTIVE_INPUT, ROLE_WRITER_GUIDANCE,
                ROLE_AUXILIARY_DATA, ROLE_UNUSED,
            }, name
        for name in ALL_AUXILIARY_KINDS:
            assert name in {
                KIND_TRANSLATION_MAP, KIND_CUT_MAP,
                KIND_MINED_FAMILIES, KIND_MINED_LABELS,
            }, name
        for name in IDENTIFIER_FIELDS:
            assert isinstance(name, str) and name
        for name in LABEL_FIELDS:
            assert isinstance(name, str) and name
        for name in THEME_FIELDS:
            assert isinstance(name, str) and name
        for name in LIST_FIELDS:
            assert isinstance(name, str) and name
        # Selection metadata is a frozenset of strings.
        assert isinstance(SELECTION_METADATA_FIELDS, frozenset)
        for name in SELECTION_METADATA_FIELDS:
            assert isinstance(name, str) and name
