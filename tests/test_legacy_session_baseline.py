"""Baseline tests for legacy session composition (task 1.3 of
`adopt-resource-session-planning`).

Goal: prove that the legacy pipeline still composes text-to-image, editing
and guided workflows exactly as it did before any resource-mode work
touches the prompt composer. The fixtures in
`tests/legacy_session_fixtures.json` are fully invented (English-only,
no source corpus text, no personal data, no machine paths, no real
names, no copied text from any operator library) and are loaded here
to drive three end-to-end sessions: text-to-image, editing and guided.

Every test asserts the SPECIFIC byte-for-byte behaviour the current
`_expand_shots` produces, so a future change to `_compose`,
`_sentences`, the `verbatim` short-circuit or the `ref_kind != "guide"`
branch in `_expand_shots` is caught by an explicit failure with the
exact line and the exact expected vs actual prompt. The tests are
written against the existing fixtures (no model IDs, no workflow IDs
are invented outside the `seeded` fixture), so a runner without the
`seeded` fixture (a fresh clone, an isolated `pytest` invocation) sees
the same baseline and the same green bar.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import db
from conftest import EDIT_GRAPH, GRAPH
# The canonical privacy-regex set: a private fork of this file would
# silently drift from `test_no_personal_data.py` if it kept a copy, and
# the two sets disagreeing is the leak that scan is supposed to catch.
# Reuse it instead.
from test_no_personal_data import PATTERNS as PRIVACY_PATTERNS


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "legacy_session_fixtures.json"


# ---- Forbiddens: the suite refuses to load the fixtures if they violate
# the public-repo contract AGENTS.md pins. The pattern set is the
# canonical one from `test_no_personal_data.py` (a private copy of the
# regexes here is exactly how a fork diverges from the upstream guard);
# the patterns catch user-home paths, emails and API tokens. They are
# NOT a proof of "English only" on their own — ASCII alone is not, e.g.
# a string of accented Latin glyphs would slip past the privacy scan and
# still violate the rule. The English-only property is asserted by the
# fixture being hand-written invented prose (see the per-session
# `look`, `wardrobe`, `prompt` and `anchor.prompt` strings); the
# privacy scan is the second half. ---------------------------------------
NON_ENGLISH_GLYPHS = re.compile(r"[^\x00-\x7F]")


@pytest.fixture(scope="module")
def legacy_fixtures() -> dict:
    """The invented session fixtures, loaded once per test module.

    The fixture file is the artifact task 1.3 captures: it is a
    tracked, English-only, invented description of three legacy
    sessions (text-to-image, editing, guided). The content is
    structural only: session name, look, wardrobe, the list of takes
    with their flags (`wardrobe`, `verbatim`, `reference`) and the
    expected `trigger`/`base_positive`/`base_negative` for the model
    the test module seeds. No image data, no personal data, no paths.
    """
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ---- Sentinels used in the assertion messages. The legacy model the
# `seeded` fixture creates carries trigger `4da woman` and base
# `photo, 35mm`; the tests pin both so a fixture rename in `conftest`
# shows up as an explicit failure, not as a silent baseline drift. -----
EXPECTED_TRIGGER = "4da woman"
EXPECTED_BASE_POSITIVE = "photo, 35mm"
EXPECTED_BASE_NEGATIVE = "blurry"
EXPECTED_LOOK = (
    "A small studio with a bare grey backdrop. Soft light from a "
    "single large window falls on her right cheek. She wears her hair "
    "loose, brushed forward over one shoulder, with no makeup beyond "
    "a faint pink lip"
)
EXPECTED_WARDROBE = (
    "a thin grey linen shirt with the sleeves rolled to the elbows, "
    "dark cotton trousers, bare feet"
)
EXPECTED_T2I_FIXTURE_ID = "inv_t2i_studio_portrait"
EXPECTED_EDIT_FIXTURE_ID = "inv_edit_remove_layer"
EXPECTED_GUIDE_FIXTURE_ID = "inv_guide_body_pose"


def _find_session(fixtures: dict, fixture_id: str) -> dict:
    for s in fixtures["sessions"]:
        if s["fixture_id"] == fixture_id:
            return s
    raise AssertionError(f"fixture {fixture_id!r} missing from legacy_session_fixtures.json")


def _session_shots(client, sid: int) -> list[dict]:
    return client.get(f"/api/sessions/{sid}").json()["shots"]


def _by_label(shots: list[dict]) -> dict[str, dict]:
    """Index shots by their `shot_label` (the public name of the field
    the shot row carries; the API request body calls it `label` because
    that is the writer's term, but the row's column is `shot_label`).
    A take without a label is the unnamed fallback (`"shot N"`); the
    fixtures always set a label so a missing entry is a real failure."""
    out: dict[str, dict] = {}
    for s in shots:
        lab = s.get("shot_label")
        assert lab, f"shot has no shot_label: {s!r}"
        out[lab] = s
    return out


def _create_workflow(client, *, name: str, kind: str, graph: dict) -> tuple[int, str]:
    """Create a workflow with `kind` and return (id, actual_kind_from_get).

    POST /api/workflows returns only `{id, node_map}`; the kind is read
    back through GET so the test pins what the graph was actually
    recorded as. `kind=""` is the legacy t2i default; the test passes
    `kind="edit"` and `kind="guide"` for the reference graphs.
    """
    wid = client.post("/api/workflows", json={"name": name, "graph": graph,
                                              "kind": kind}).json()["id"]
    actual = client.get(f"/api/workflows/{wid}").json()["kind"]
    assert actual == kind, f"workflow {name} recorded as {actual!r}, expected {kind!r}"
    return wid, actual


# ---- 0. The fixture file itself is well-formed and contract-clean. ---
# The composer will not catch a path that landed in the JSON, so this
# test is the loop-closed half of "the fixtures are public-repo safe":
# the assertions below cover behaviour; this one covers the file.
def test_legacy_fixture_file_is_invented_and_contract_clean(legacy_fixtures):
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    # Privacy scan: reuse the canonical PATTERNS from
    # `test_no_personal_data.py` so a drift between the two files is
    # impossible by construction. ASCII alone is not "English only" —
    # see the comment above — but a non-ASCII glyph in the file is a
    # strong signal the fixture inherited a real string, and the scan
    # catches it cheaply.
    assert NON_ENGLISH_GLYPHS.search(text) is None, (
        "legacy_session_fixtures.json contains a non-ASCII glyph; that is "
        "a strong signal the fixture inherited a real string instead of "
        "an invented English one"
    )
    for label, pattern in PRIVACY_PATTERNS.items():
        m = pattern.search(text)
        assert m is None, (
            f"legacy_session_fixtures.json contains a {label} match: {m.group(0)!r}"
        )
    # Every session is one of the three supported workflow kinds.
    valid_kinds = {fixtures_kind for fixtures_kind in
                   legacy_fixtures["workflow_kinds"].values()}
    for session in legacy_fixtures["sessions"]:
        assert session["workflow_kind"] in valid_kinds, (
            f"session {session['fixture_id']!r} carries an unknown workflow_kind "
            f"{session['workflow_kind']!r}; valid are {sorted(valid_kinds)}"
        )
    # The expected composition block matches the seeded model.
    expected = legacy_fixtures["expected_composition"]
    assert expected["trigger"] == EXPECTED_TRIGGER
    assert expected["base_positive"] == EXPECTED_BASE_POSITIVE
    assert expected["base_negative"] == EXPECTED_BASE_NEGATIVE


# ---- 1. Text-to-image composition is byte-for-byte unchanged. --------
# Three takes: a normal one, a take with its own wardrobe override, and
# a take whose prompt contains an explicit `{trigger}` placeholder. The
# composer is `_compose`, the order is trigger + base + look + wardrobe
# + take, joined with full stops, and the third take is the one
# invariant the test pins: an explicit `{trigger}` in the take is NOT
# prepended a second time.
def test_a_text_to_image_legacy_session_composes_every_take_unmodified(
        client, seeded, legacy_fixtures):
    fixture = _find_session(legacy_fixtures, EXPECTED_T2I_FIXTURE_ID)
    body = {
        "model_id": seeded["model_id"],
        "name": fixture["name"],
        "look": fixture["look"],
        "wardrobe": fixture["wardrobe"],
        "shots": fixture["shots"],
    }
    sid = client.post("/api/sessions", json=body).json()["id"]
    shots = _session_shots(client, sid)
    by_label = _by_label(shots)

    # The session round-trips its constant parts unchanged.
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["look"] == EXPECTED_LOOK
    assert session["wardrobe"] == EXPECTED_WARDROBE

    # 1. Standard take: trigger + base + look + wardrobe + take.
    assert by_label["wide"]["prompt"] == (
        f"{EXPECTED_TRIGGER}. {EXPECTED_BASE_POSITIVE}. {EXPECTED_LOOK}. "
        f"{EXPECTED_WARDROBE}. "
        f"standing square to the camera with her hands at her sides, "
        f"full body in frame."
    ), (
        f"text-to-image composition drifted for the standard take; "
        f"got {by_label['wide']['prompt']!r}"
    )

    # 2. Take with its own wardrobe: the session's wardrobe is NOT
    # prepended, the take's wardrobe wins verbatim.
    assert by_label["jacket on"]["prompt"] == (
        f"{EXPECTED_TRIGGER}. {EXPECTED_BASE_POSITIVE}. {EXPECTED_LOOK}. "
        "the same grey linen shirt, dark cotton trousers, a loose dark "
        "jacket over the shirt, bare feet. "
        "turned three quarters to the camera, one hand on her hip, the "
        "other raised to her collar."
    ), (
        f"per-take wardrobe override drifted; got {by_label['jacket on']['prompt']!r}"
    )
    # Belt and braces: the take's wardrobe appears AND the session's
    # wardrobe does NOT appear in the same line.
    assert EXPECTED_WARDROBE not in by_label["jacket on"]["prompt"], (
        "the take's wardrobe must win over the session's, but the "
        "session's wardrobe leaked into the same line"
    )

    # 3. Explicit `{trigger}` placeholder: the trigger is NOT prepended.
    assert by_label["explicit trigger"]["prompt"] == (
        f"{EXPECTED_BASE_POSITIVE}. {EXPECTED_LOOK}. {EXPECTED_WARDROBE}. "
        f"close-up of {EXPECTED_TRIGGER}, the camera centred on her face."
    ), (
        f"explicit {{trigger}} placeholder was not honoured; got "
        f"{by_label['explicit trigger']['prompt']!r}"
    )

    # 4. Wardrobe="" is a take that names no clothes. The line is
    # `... look. <take>`, no wardrobe clause, no doubled period.
    assert by_label["no clothes at all"]["prompt"] == (
        f"{EXPECTED_TRIGGER}. {EXPECTED_BASE_POSITIVE}. {EXPECTED_LOOK}. "
        "kneeling upright on the floor, both hands resting on her thighs."
    ), (
        f"empty wardrobe on a take drifted; got {by_label['no clothes at all']['prompt']!r}"
    )
    assert EXPECTED_WARDROBE not in by_label["no clothes at all"]["prompt"], (
        "an empty wardrobe on a take must not leak the session's wardrobe"
    )

    # The negative is inherited from the model across every take.
    for s in shots:
        assert s["negative"] == EXPECTED_BASE_NEGATIVE, (
            f"shot {s['label']!r} has negative {s['negative']!r}, expected "
            f"{EXPECTED_BASE_NEGATIVE!r}"
        )


# ---- 2. Editing workflow: a reference take's prompt is sent RAW. -----
# The reference graph's `kind` is `edit` (not `guide`), so the take's
# prompt is NOT composed. Prepending the look would restate the very
# garment the instruction ("remove the dark jacket") removes. The
# fixture pins exactly that: a session whose reference take prompt
# reaches the row verbatim.
def test_an_editing_legacy_take_is_sent_raw_without_composition(
        client, seeded, legacy_fixtures):
    fixture = _find_session(legacy_fixtures, EXPECTED_EDIT_FIXTURE_ID)
    # The fixture is the source of truth for `workflow_kind` and
    # `uses_reference_workflow`; a hard-coded `kind="edit"` here would
    # be a second copy of the same fact and would drift the day the
    # fixture is renamed. The branch reads the same name from JSON.
    assert fixture["uses_reference_workflow"] is True, (
        f"edit fixture {EXPECTED_EDIT_FIXTURE_ID!r} must declare "
        f"uses_reference_workflow=True; got {fixture['uses_reference_workflow']!r}"
    )
    edit_wf, _ = _create_workflow(
        client, name=f"inv-{fixture['fixture_id']}",
        kind=fixture["workflow_kind"], graph=EDIT_GRAPH,
    )
    body = {
        "model_id": seeded["model_id"],
        "name": fixture["name"],
        "look": fixture["look"],
        "wardrobe": fixture["wardrobe"],
        "reference_workflow_id": edit_wf,
        "shots": [fixture["anchor"]] + fixture["shots"],
    }
    sid = client.post("/api/sessions", json=body).json()["id"]
    shots = _session_shots(client, sid)
    by_label = _by_label(shots)

    # The anchor is a normal t2i take: composed.
    assert by_label["anchor"]["prompt"] == (
        f"{EXPECTED_TRIGGER}. {EXPECTED_BASE_POSITIVE}. {fixture['look']}. "
        f"{fixture['wardrobe']}. "
        "standing square to the camera with her hands at her sides, "
        "full body in frame."
    ), (
        f"anchor (text-to-image) drifted; got {by_label['anchor']['prompt']!r}"
    )

    # The reference take on an edit workflow is sent RAW.
    raw = "remove the dark jacket, leave the rest unchanged"
    assert by_label["remove the jacket"]["prompt"] == raw, (
        f"editing workflow must send the take's prompt raw; got "
        f"{by_label['remove the jacket']['prompt']!r}"
    )
    # The session's look and wardrobe are NOT prepended on the raw
    # path: the anchor already carries them.
    assert EXPECTED_TRIGGER not in by_label["remove the jacket"]["prompt"]
    assert fixture["look"] not in by_label["remove the jacket"]["prompt"], (
        "editing take must not re-state the look; that would restate the "
        "garment the instruction is removing"
    )
    assert fixture["wardrobe"] not in by_label["remove the jacket"]["prompt"], (
        "editing take must not re-state the wardrobe; that would restate "
        "the garment the instruction is removing"
    )


# ---- 3. Guided workflow: a reference take's prompt IS composed. ------
# The same flag (`reference=true`) on a graph of kind `guide` is a
# DIFFERENT branch: a guide graph paints from noise, so the trigger,
# base and look are still the only things that put her in the room.
# Sent bare it would render the anchor's own room. The fixture pins
# the guided composition: trigger + base + look + wardrobe + take.
def test_a_guided_legacy_take_is_composed_not_sent_raw(
        client, seeded, legacy_fixtures):
    fixture = _find_session(legacy_fixtures, EXPECTED_GUIDE_FIXTURE_ID)
    # Same source-of-truth contract as the edit test above: the
    # fixture owns `workflow_kind` and `uses_reference_workflow`, and a
    # hard-coded duplicate would drift.
    assert fixture["uses_reference_workflow"] is True, (
        f"guide fixture {EXPECTED_GUIDE_FIXTURE_ID!r} must declare "
        f"uses_reference_workflow=True; got {fixture['uses_reference_workflow']!r}"
    )
    guide_wf, _ = _create_workflow(
        client, name=f"inv-{fixture['fixture_id']}",
        kind=fixture["workflow_kind"], graph=EDIT_GRAPH,
    )
    body = {
        "model_id": seeded["model_id"],
        "name": fixture["name"],
        "look": fixture["look"],
        "wardrobe": fixture["wardrobe"],
        "reference_workflow_id": guide_wf,
        "shots": [fixture["anchor"]] + fixture["shots"],
    }
    sid = client.post("/api/sessions", json=body).json()["id"]
    shots = _session_shots(client, sid)
    by_label = _by_label(shots)

    assert by_label["guided leaning"]["prompt"] == (
        f"{EXPECTED_TRIGGER}. {EXPECTED_BASE_POSITIVE}. {fixture['look']}. "
        f"{fixture['wardrobe']}. "
        "leaning her weight onto her right leg with her left shoulder "
        "against an invisible wall, both hands loose at her sides."
    ), (
        f"guided workflow must compose the take's prompt; got "
        f"{by_label['guided leaning']['prompt']!r}"
    )
    # The guided take carries the trigger, base and look — the three
    # things a noise-painting sampler needs to put her in the room.
    assert EXPECTED_TRIGGER in by_label["guided leaning"]["prompt"]
    assert EXPECTED_BASE_POSITIVE in by_label["guided leaning"]["prompt"]
    assert fixture["look"] in by_label["guided leaning"]["prompt"]


# ---- 4. Wardrobe override rules survive across all three kinds. ------
# The wardrobe rules in `main.py:3541-3543`:
#   worn = wardrobe if take.wardrobe is None else take.wardrobe
#   prompt = take.prompt if raw else _compose(model, look, worn, take.prompt)
# are what holds a shoot together; the legacy baseline is the only place
# they are documented, and task 1.3 captures that baseline. The three
# assertions below are exhaustive on a 4-shot t2i session:
#   - take.wardrobe is None → session's wardrobe
#   - take.wardrobe is "" → no wardrobe clause
#   - take.wardrobe is "..." → take's wardrobe wins
def test_wardrobe_override_rules_match_the_legacy_baseline(
        client, seeded, legacy_fixtures):
    fixture = _find_session(legacy_fixtures, EXPECTED_T2I_FIXTURE_ID)
    body = {
        "model_id": seeded["model_id"],
        "name": "wardrobe override baseline",
        "look": EXPECTED_LOOK,
        "wardrobe": EXPECTED_WARDROBE,
        "shots": fixture["shots"],
    }
    sid = client.post("/api/sessions", json=body).json()["id"]
    shots = _session_shots(client, sid)
    by_label = _by_label(shots)

    # take.wardrobe is None: the session's wardrobe is written.
    assert EXPECTED_WARDROBE in by_label["wide"]["prompt"]
    # take.wardrobe is a string: the take's wins, the session's does not.
    assert ("the same grey linen shirt, dark cotton trousers, a loose dark "
            "jacket over the shirt, bare feet") in by_label["jacket on"]["prompt"]
    assert EXPECTED_WARDROBE not in by_label["jacket on"]["prompt"]
    # take.wardrobe is "": no wardrobe clause at all (look, take, joined
    # with a single full stop between them, not a doubled period).
    assert EXPECTED_WARDROBE not in by_label["no clothes at all"]["prompt"]
    assert ".." not in by_label["no clothes at all"]["prompt"], (
        f"empty wardrobe on a take must not produce a doubled period; "
        f"got {by_label['no clothes at all']['prompt']!r}"
    )


# ---- 4.5. Verbatim invariant: a take flagged `verbatim: true` is
# stored exactly as the writer handed it in. The trigger, base, look
# and wardrobe are NOT prepended: the take is "more like this" — the
# caller already wrote a complete prompt and the system must honour
# it byte-for-byte. The fixture's verbatim take is invented English
# prose with no overlap with the trigger, the base, the look or the
# wardrobe of the session, so any of those strings appearing on the
# row is the composer having re-run on a take that was not supposed
# to be re-composed.
def test_a_verbatim_legacy_take_is_stored_raw_without_composition(
        client, seeded, legacy_fixtures):
    fixture = _find_session(legacy_fixtures, EXPECTED_T2I_FIXTURE_ID)
    # Locate the verbatim take in the fixture so the assertion reads
    # the expected prompt from JSON, not from a hard-coded copy.
    verbatim_take = next(
        (t for t in fixture["shots"] if t.get("verbatim") is True), None,
    )
    assert verbatim_take is not None, (
        f"t2i fixture {EXPECTED_T2I_FIXTURE_ID!r} must declare a take with "
        f"verbatim=True; the test cannot pin the invariant without one"
    )
    expected_prompt = verbatim_take["prompt"]
    # Sanity: the verbatim prompt must not already contain the
    # trigger, the base, the look or the wardrobe — otherwise the
    # "raw" assertion would be circular (the row would contain the
    # strings because the writer typed them, not because the composer
    # prepended them).
    for forbidden in (EXPECTED_TRIGGER, EXPECTED_BASE_POSITIVE,
                      EXPECTED_LOOK, EXPECTED_WARDROBE):
        assert forbidden not in expected_prompt, (
            f"verbatim prompt {expected_prompt!r} must not contain "
            f"{forbidden!r}; otherwise the raw-storage assertion is "
            f"circular"
        )

    body = {
        "model_id": seeded["model_id"],
        "name": fixture["name"],
        "look": fixture["look"],
        "wardrobe": fixture["wardrobe"],
        "shots": [verbatim_take],
    }
    sid = client.post("/api/sessions", json=body).json()["id"]
    shots = _session_shots(client, sid)
    by_label = _by_label(shots)

    actual = by_label["more like this"]["prompt"]
    # Byte-for-byte: the row carries the take's prompt and nothing
    # else. A re-composition would prepend trigger + base + look +
    # wardrobe; the assertion fails on the first non-matching byte.
    assert actual == expected_prompt, (
        f"verbatim take must be stored raw; got {actual!r}, "
        f"expected {expected_prompt!r}"
    )
    # Belt and braces: the four prepended parts are individually
    # absent. A composition that happened to land on the same total
    # string by accident would still trip the next four assertions.
    assert EXPECTED_TRIGGER not in actual
    assert EXPECTED_BASE_POSITIVE not in actual
    assert EXPECTED_LOOK not in actual
    assert EXPECTED_WARDROBE not in actual


# ---- 5. Cell evidence is unchanged by legacy session creation. ------
# The cell table is the unit of evidence (`_evidence_by_component` in
# main.py:705). A legacy session creation path must not write to it:
# a session on the written path is `origin='written'`, no `compose`
# call, no cell insert, no judged/arrived mutation. The test pre-seeds
# a known cell, creates the three legacy sessions, and asserts the
# cell's evidence is byte-for-byte the same after the run.
def test_legacy_session_creation_does_not_mutate_cell_evidence(
        client, seeded, legacy_fixtures):
    # Pre-seed a single cell so the SELECT below has a known shape.
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived, contradicted) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
           "inv-cam", "inv-act", "inv-frm", "directed", "base.safetensors",
           7, 5, 1)
    before = db.one("SELECT * FROM cell WHERE camera_wording='inv-cam'")
    before_total_judged = db.one(
        "SELECT COALESCE(SUM(judged), 0) AS n FROM cell")["n"]
    before_total_arrived = db.one(
        "SELECT COALESCE(SUM(arrived), 0) AS n FROM cell")["n"]
    before_row_count = db.one("SELECT COUNT(*) AS n FROM cell")["n"]

    # Drive all three legacy kinds through create + add-shots. None
    # of these calls `compose`; the cell table is for the composed
    # path and the baseline is "legacy creates do not touch it".
    # The fixture owns `workflow_kind` and `uses_reference_workflow`;
    # the test reads them from JSON rather than hard-coding the kind
    # and the workflow binding here.
    for fid in (EXPECTED_T2I_FIXTURE_ID, EXPECTED_EDIT_FIXTURE_ID, EXPECTED_GUIDE_FIXTURE_ID):
        fixture = _find_session(legacy_fixtures, fid)
        wf_id, _ = _create_workflow(
            client, name=f"inv-{fixture['fixture_id']}",
            kind=fixture["workflow_kind"],
            graph=EDIT_GRAPH if fixture["uses_reference_workflow"] else GRAPH,
        )
        body = {
            "model_id": seeded["model_id"],
            "name": fixture["name"],
            "look": fixture["look"],
            "wardrobe": fixture["wardrobe"],
        }
        if fixture["uses_reference_workflow"]:
            body["reference_workflow_id"] = wf_id
        else:
            body["workflow_id"] = wf_id
        body["shots"] = ([fixture["anchor"]] if "anchor" in fixture else []) + fixture["shots"]
        sid = client.post("/api/sessions", json=body).json()["id"]
        # An `add_shots` extension on the same session also has to
        # be a no-op for the cell table, AND the endpoint has to
        # actually accept and add the take — a silent 4xx that
        # dropped the extension would still leave the cell table
        # untouched, which is not the baseline this test is pinning.
        ext = client.post(f"/api/sessions/{sid}/shots", json={
            "shots": [{"prompt": "baseline extension take", "count": 1}],
        })
        assert ext.status_code == 200, (
            f"add_shots on legacy session {sid} returned {ext.status_code}: "
            f"{ext.text!r}"
        )
        assert ext.json()["added"] == 1, (
            f"add_shots on legacy session {sid} added "
            f"{ext.json().get('added')!r} takes, expected 1"
        )

    after = db.one("SELECT * FROM cell WHERE camera_wording='inv-cam'")
    after_total_judged = db.one(
        "SELECT COALESCE(SUM(judged), 0) AS n FROM cell")["n"]
    after_total_arrived = db.one(
        "SELECT COALESCE(SUM(arrived), 0) AS n FROM cell")["n"]
    after_row_count = db.one("SELECT COUNT(*) AS n FROM cell")["n"]

    # The pre-seeded cell is byte-for-byte unchanged.
    assert dict(after) == dict(before), (
        f"legacy session creation mutated a pre-existing cell row; "
        f"before={dict(before)!r} after={dict(after)!r}"
    )
    # No new cells were inserted, no counts changed.
    assert after_row_count == before_row_count, (
        f"legacy session creation added a cell row; before={before_row_count} "
        f"after={after_row_count}"
    )
    assert after_total_judged == before_total_judged, (
        f"legacy session creation incremented total judged evidence; "
        f"before={before_total_judged} after={after_total_judged}"
    )
    assert after_total_arrived == before_total_arrived, (
        f"legacy session creation incremented total arrived evidence; "
        f"before={before_total_arrived} after={after_total_arrived}"
    )

    # The legacy sessions themselves are recorded as the written path.
    # The evidence is the `origin` column on the session row.
    for sid, expected_kind in (
            (db.one("SELECT id FROM session WHERE name=?",
                    _find_session(legacy_fixtures, EXPECTED_T2I_FIXTURE_ID)["name"])["id"], "written"),
            (db.one("SELECT id FROM session WHERE name=?",
                    _find_session(legacy_fixtures, EXPECTED_EDIT_FIXTURE_ID)["name"])["id"], "written"),
            (db.one("SELECT id FROM session WHERE name=?",
                    _find_session(legacy_fixtures, EXPECTED_GUIDE_FIXTURE_ID)["name"])["id"], "written"),
    ):
        row = db.one("SELECT origin FROM session WHERE id=?", sid)
        assert row["origin"] == expected_kind, (
            f"session {sid} origin drifted; got {row['origin']!r}, "
            f"expected {expected_kind!r}"
        )
