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
