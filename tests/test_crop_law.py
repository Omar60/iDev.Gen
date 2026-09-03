"""The crop law, in the composer.

The frame reaches the lowest part of her the line names, so a framing that claims
a crop above it measures the anatomy and not the framing. What is checked here is
the arithmetic (`backend/crop.py`) and the two places it has to bite: the one-shot
`/compose` pre-check, which must queue NOTHING when a combination contradicts
itself, and the pool the runs draw from, where a constraint checked after the draw
is a constraint the draw does not have.
"""
import crop
import pytest


class TestTheLadder:
    def test_a_line_that_names_nothing_of_her_has_no_floor(self):
        # Session 313's state, and the only one where a crop term decides the
        # edges on its own.
        assert crop.lowest_named("headshot") is None
        assert crop.lowest_named("") is None

    def test_the_lowest_part_named_anywhere_wins(self):
        assert crop.lowest_named("her chest bare", "her hands at her ribs") == crop.CHEST
        assert crop.lowest_named("her chest bare", "hands clasp her lower legs") == crop.FEET

    def test_a_garment_counts_as_the_body_it_covers(self):
        # Session 318: stockings written to her feet put her head out of frame.
        assert crop.lowest_named("black stockings") == crop.FEET
        assert crop.lowest_named("a satin bra") == crop.CHEST

    def test_a_crop_term_claims_its_own_crop(self):
        assert crop.crop_claimed("waist-up") == crop.WAIST
        assert crop.crop_claimed("full body") == crop.FEET
        assert crop.crop_claimed("headshot") == crop.HEAD

    def test_a_framing_without_a_term_claims_the_anatomy_it_names(self):
        # The catalogue's own wordings, which describe the edges instead of
        # naming a term. `below knees` is a crop at the knees either way.
        assert crop.crop_claimed(
            "Top edge cuts above head, bottom edge cuts below knees") == crop.KNEES

    def test_a_framing_that_is_no_crop_at_all_constrains_nothing(self):
        assert crop.crop_claimed("shot on a grey afternoon") is None
        assert crop.conflict("shot on a grey afternoon", "hands clasp her lower legs") is None


class TestTheConflict:
    def test_a_crop_above_the_act_is_refused(self):
        why = crop.conflict("waist-up", "One young woman bends at the waist, "
                                        "hands clasp her lower legs")
        assert why and "her feet" in why

    def test_a_crop_below_what_the_line_names_is_fine(self):
        assert crop.conflict("full body", "hands clasp her lower legs") is None

    def test_a_crop_exactly_at_what_the_line_names_is_fine(self):
        assert crop.conflict("waist-up", "her hands on her hips") is None

    def test_the_wardrobe_is_read_like_the_act(self):
        # Nothing in the act reaches below her chest; the socks do.
        assert crop.conflict("waist-up", "she stands square to the camera") is None
        assert crop.conflict("waist-up", "she stands square to the camera",
                             "white cotton socks") is not None


@pytest.fixture()
def crop_session(client, seeded):
    """A session with no look and no wardrobe, so the only thing naming a part of
    her is the trio itself. That is the empty bench sessions 311-318 were shot on,
    and it is the only bench a crop term can be measured from."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "crop bench",
        "look": "", "wardrobe": "",
        "manner": "directed", "checkpoint": "finepornV4",
        "settings": {"use_look": False},
        "shots": [],
    }).json()["id"]
    return {"sid": sid}


class TestTheComposerRefuses:
    """Probed through the endpoint, never by reading the branch: six real bugs in
    this file's history were invisible in the diff and obvious from outside."""

    def test_a_contradicting_trio_queues_nothing(self, client, crop_session):
        sid = crop_session["sid"]
        before = len(client.get(f"/api/sessions/{sid}").json()["shots"])
        r = client.post(f"/api/sessions/{sid}/compose", json={
            "camera": {"key": "cam-front", "wordings": [{"key": "cam-front",
                                                         "text": "Taken from directly in front of her"}]},
            "act": {"key": "act-bend", "wordings": [{"key": "act-bend",
                                                     "text": "she bends forward, hands on her lower legs"}]},
            "framing": {"key": "crop-waist-up", "wordings": [{"key": "crop-waist-up",
                                                              "text": "waist-up"}]},
            "count": 1, "mode": "exploratory",
        })
        assert r.status_code == 422
        assert "contradicts itself" in r.json()["detail"]
        after = len(client.get(f"/api/sessions/{sid}").json()["shots"])
        assert after == before

    def test_a_batch_refuses_whole_when_one_combination_contradicts(self, client, crop_session):
        sid = crop_session["sid"]
        before = len(client.get(f"/api/sessions/{sid}").json()["shots"])
        r = client.post(f"/api/sessions/{sid}/compose", json={
            "camera": {"key": "cam-front", "wordings": [{"key": "cam-front",
                                                         "text": "Taken from directly in front of her"}]},
            "act": [{"key": "act-stand", "wordings": [{"key": "act-stand",
                                                       "text": "she stands square to the camera"}]},
                    {"key": "act-bend", "wordings": [{"key": "act-bend",
                                                      "text": "she bends forward, hands on her lower legs"}]}],
            "framing": {"key": "crop-waist-up", "wordings": [{"key": "crop-waist-up",
                                                              "text": "waist-up"}]},
            "count": 1, "mode": "exploratory",
        })
        assert r.status_code == 422
        assert len(client.get(f"/api/sessions/{sid}").json()["shots"]) == before

    def test_a_crop_the_line_can_carry_is_queued(self, client, crop_session):
        sid = crop_session["sid"]
        r = client.post(f"/api/sessions/{sid}/compose", json={
            "camera": {"key": "cam-front", "wordings": [{"key": "cam-front",
                                                         "text": "Taken from directly in front of her"}]},
            "act": {"key": "act-stand", "wordings": [{"key": "act-stand",
                                                      "text": "she stands square to the camera"}]},
            "framing": {"key": "crop-waist-up", "wordings": [{"key": "crop-waist-up",
                                                              "text": "waist-up"}]},
            "count": 1, "mode": "exploratory",
        })
        assert r.status_code == 200, r.text
        shots = client.get(f"/api/sessions/{sid}").json()["shots"]
        assert "waist-up" in shots[-1]["prompt"]

    def test_the_run_pool_never_offers_a_contradicting_trio(self, client, crop_session):
        """The refusal has to live in the DRAW: a run that asks for two shots over
        a pool of one carryable crop and one contradicting one is a run of one,
        and it must be reported as one rather than queued as two."""
        sid = crop_session["sid"]
        body = {"count": 2, "mode": "exploratory", "candidates": {
            "camera": [{"key": "cam-front", "wordings": [{"key": "cam-front",
                                                          "text": "Taken from directly in front of her"}]}],
            "act": [{"key": "act-stand", "wordings": [{"key": "act-stand",
                                                       "text": "she stands square to the camera"}]},
                    {"key": "act-bend", "wordings": [{"key": "act-bend",
                                                      "text": "she bends forward, hands on her lower legs"}]}],
            "framing": [{"key": "crop-waist-up", "wordings": [{"key": "crop-waist-up",
                                                               "text": "waist-up"}]}],
        }}
        r = client.post(f"/api/sessions/{sid}/compose-run", json=body)
        assert r.status_code == 422
        assert "largest fillable is 1" in r.json()["detail"]


def test_every_manner_has_an_act_a_crop_at_her_head_can_be_drawn_against():
    """The tightest framing needs an act that names nothing below her head.

    The frame reaches the lowest part of her the line names, so `headshot` and
    `close-up` are refused against any act that mentions her chest, her arms or
    her legs — which was every candid act there was: both were drawable on 0 of
    37, and a framing pass could only ever have scored five of candid's seven
    values. `head-only-facing` is the row each manner needs, and this is what it
    is for.

    The act is checked ALONE, with no look and no wardrobe, because those are
    the operator's words and vary per session. They are the other two conditions
    in a real line — candid's own look says her hair falls "around her
    shoulders", which reaches her chest and refuses a headshot however the act
    is written — but a catalogue that cannot offer one at all is a catalogue
    problem, and that is what this pins.
    """
    import json as _json
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parents[1]
    acts, framings = [], []
    for path in (root / "data").glob("*-seed.json"):
        # The readings seed carries `slot` too, and a reading is a question
        # rather than a component: it has no wording for the crop law to read.
        if path.name == "readings-seed.json":
            continue
        for item in _json.loads(path.read_text(encoding="utf-8")):
            if not isinstance(item, dict):
                continue
            if item.get("slot") == "act":
                acts.append(item)
            elif item.get("slot") == "framing":
                framings.append(item)

    for manner in sorted({f["manner"] for f in framings}):
        tight = [f for f in framings
                 if f["manner"] == manner and f["concept_key"] in ("crop-headshot", "crop-close-up")]
        if not tight:
            continue
        for framing in tight:
            drawable = [a["concept_key"] for a in acts if a["manner"] == manner
                        and not crop.conflict(framing["wording"], "", a["wording"], "", "")]
            assert drawable, (
                f"{manner}: no act can be drawn against {framing['concept_key']!r} — every one "
                f"names something below her head, so the framing is unmeasurable for this manner")


def test_a_second_crop_term_in_another_clause_is_refused_both_ways():
    """Two crop words in one line is a coin flip, not a framing.

    The anatomy rule cannot see this one: `headshot` and `extreme wide shot`
    name no part of her, so `lowest_named` reads both as None and the trio
    composed happily while the line said both at once. It is not hypothetical —
    directed's camera catalogue carries fifteen crop terms in the CAMERA slot
    (`close-up`, `medium shot`, `full body`, `long shot` and their kin), so the
    pairing is one draw away.

    Refused in BOTH directions, unlike the anatomy rule. A looser second term
    contradicts the framing exactly as a tighter one does: either way the cell
    measures which of two crop words won, and not the framing.
    """
    act = "One young woman stands upright and square to the camera"

    # Tighter camera under a looser framing, and the reverse.
    assert crop.conflict("full body", "close-up", act, "", "")
    assert crop.conflict("headshot", "extreme wide shot", act, "", "")
    assert crop.conflict("extreme wide shot", "headshot", act, "", "")

    # The same rung twice is redundant, not contradictory: both claim her head.
    assert crop.conflict("headshot", "close-up", act, "", "") is None

    # A camera that claims no crop at all is what the slot is for.
    assert crop.conflict("full body", "front view", act, "", "") is None

    # And the anatomy rule still only bites downwards: an act naming her chest
    # under a full-body framing is legal, because the frame reaches the lowest
    # part the line names and her chest is above her feet.
    assert crop.conflict("full body", "", "her shoulders level", "", "") is None
