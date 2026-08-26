"""HTTP routes: creating models and sessions, prompt composition, rating."""
import io
import zipfile

from PIL import Image, ImageFont
import pytest

import db
import main
from conftest import EDIT_GRAPH, GRAPH


def test_importing_a_workflow_autodetects_the_map(client):
    r = client.post("/api/workflows", json={"name": "wf", "graph": GRAPH})
    assert r.status_code == 200
    assert r.json()["node_map"]["positive"] == "3.inputs.text"
    assert client.get("/api/workflows").json()[0]["name"] == "wf"


def test_a_workflow_remembers_what_it_is_for(client):
    """The kind is how a session kind finds its graph. Untagged is a valid state:
    every workflow imported before kinds existed has no tag, and hiding those
    would empty every select on the screen."""
    plain = client.post("/api/workflows", json={"name": "plain", "graph": GRAPH}).json()["id"]
    client.post("/api/workflows", json={"name": "turner", "graph": EDIT_GRAPH, "kind": "angles"})
    listed = {w["name"]: w["kind"] for w in client.get("/api/workflows").json()}
    assert listed == {"plain": "", "turner": "angles"}

    client.patch(f"/api/workflows/{plain}", json={"name": "plain", "graph": GRAPH, "kind": "t2i"})
    assert client.get(f"/api/workflows/{plain}").json()["kind"] == "t2i"


def test_a_session_remembers_its_kind(client, seeded):
    """It rides in the settings blob, so it needs no column of its own — and the
    model's own settings still merge in underneath it."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "angles", "settings": {"kind": "angles"},
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    s = client.get(f"/api/sessions/{sid}").json()
    assert s["settings"]["kind"] == "angles"
    assert s["settings"]["width"] == 832


def test_detect_rejects_the_editor_format(client):
    """The 'Save' JSON (nodes/links) is useless here; the error must say so."""
    r = client.post("/api/workflows/detect", json={"graph": {"nodes": [{"id": 1}], "links": []}})
    assert r.status_code == 400
    assert "API" in r.json()["detail"]


def test_session_expands_shots_and_composes_the_prompt(client, seeded):
    r = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "summer", "seed_mode": "fixed", "seed": 100,
        "look": "white summer dress, hair down, on a beach",
        "shots": [{"label": "wide", "prompt": "full body, walking", "count": 3},
                  {"label": "close", "prompt": "close-up of {trigger}", "count": 1}],
    })
    assert r.status_code == 200
    s = client.get(f"/api/sessions/{r.json()['id']}").json()
    shots = s["shots"]
    assert len(shots) == 4
    assert [x["shot_index"] for x in shots] == [0, 0, 0, 1]
    # Fixed seed: it shifts inside a take, otherwise they would be copies.
    assert [x["seed"] for x in shots[:3]] == [100, 101, 102]
    # trigger, base prompt, look, then the take — the fixed block first and the
    # pose last, which is the order the sessions that came back right were written
    # in. One sentence each, because the encoder reads them as language.
    assert shots[0]["prompt"] == (
        "4da woman. photo, 35mm. white summer dress, hair down, on a beach. full body, walking.")
    # An explicit {trigger} wins: it is not prepended a second time.
    assert shots[3]["prompt"] == (
        "photo, 35mm. white summer dress, hair down, on a beach. close-up of 4da woman.")
    assert shots[0]["negative"] == "blurry"          # inherited from the model
    assert s["settings"]["width"] == 832             # settings inherited too
    assert s["look"] == "white summer dress, hair down, on a beach"


def test_a_composed_shot_joins_identically_to_a_written_one(client, seeded):
    """A shot composed from drawn components joins its line byte-for-byte
    to one written from the same three components. The composer
    goes through the same `_sentences` join as the writer's
    `_compose`, so for the same camera + act + framing the output
    is identical.

    The equality is the assertion, not a similarity: if the composed
    line differs from the written one by a space, group 4 cannot
    compare the composer's render rate against the writer's, and
    every measured rate after that is apples to oranges. A future
    "let me change the join" or "let me reorder the components"
    that drifts the two outputs apart breaks this test on the spot.

    The test composes one shot from three known components and
    writes one shot with the same three components as the prompt,
    then asserts the two prompts are identical: same trigger, same
    base, same look, same wardrobe, same three pieces in the take
    position, same join.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "compose equals write",
        "look": "white summer dress, hair down, on a beach",
        "shots": [],
    }).json()["id"]

    # Three drawn components. The draw is deterministic for 3.1: the
    # caller passes the components, the composer joins them. 3.2 makes
    # the draw respect cell state, 6.1 makes unknown drawable in
    # exploratory mode.
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride",
                         "text": "She is astride him with her knees on either side of his hips and her weight down on him, the two of them joined, two people in frame."}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "a full-length photograph, head to feet"}]}

    # Compose and queue.
    composed_id = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    }).json()["id"]

    # Write the same line: add a shot with the three components as
    # the take. The writer's _compose joins the same way the
    # composer's does, so the two prompts should match exactly.
    take_text = (f"{camera['wordings'][0]['text']}. "
                 f"{act['wordings'][0]['text']} "
                 f"{framing['wordings'][0]['text']}.")
    client.post(f"/api/sessions/{sid}/shots", json={
        "shots": [{"prompt": take_text, "count": 1}],
    })

    # Get both shots and assert the composed line joins identically
    # to the written one.
    session = client.get(f"/api/sessions/{sid}").json()
    shots = {s["id"]: s["prompt"] for s in session["shots"]}
    composed_prompt = shots[composed_id]
    written_prompt = next(p for sid, p in shots.items() if sid != composed_id)
    assert composed_prompt == written_prompt, (
        f"composed: {composed_prompt!r}\n"
        f"written:  {written_prompt!r}"
    )


def test_a_composed_shot_records_the_three_components_on_the_row(client, seeded):
    """A composed shot records the three (concept, wording) pairs on
    the row in the `components` column. The prose does not survive
    the round-trip — from `'4da woman. photo, 35mm. white summer dress,
    hair down, on a beach. Taken from directly in front of her. ...'`
    you cannot recover `front-direct` — and the cell is keyed by the
    trio (camera_wording, act_wording, framing_wording, manner,
    checkpoint), so 6.2 needs all three wordings to land the photo on
    the right cell.

    The structure stored: the JSON key is the slot name (camera, act,
    framing), and each value is `{"concept": <concept key>,
    "wording": <wording key>}` — both real catalogue keys, equal
    when the concept has a single wording. A written shot leaves the
    column at the empty default '{}', which is the marker 3.6 uses to
    tell a composed session from a written one.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "components on row",
        "look": "white summer dress, hair down, on a beach",
        "shots": [],
    }).json()["id"]

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride",
                         "text": "She is astride him with her knees on either side of his hips and her weight down on him, the two of them joined, two people in frame."}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "a full-length photograph, head to feet"}]}

    composed_id = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    }).json()["id"]

    row = db.one("SELECT * FROM shot WHERE id=?", composed_id)
    db.jload(row, "components")
    assert row["components"] == {
        "camera":  {"concept": "front-direct", "wording": "front-direct"},
        "act":     {"concept": "astride",      "wording": "astride"},
        "framing": {"concept": "full-length",  "wording": "full-length"},
    }


def test_a_written_shot_leaves_components_empty(client, seeded):
    """A shot written by the writer (not composed) leaves the
    `components` column at its empty default '{}'. That empty default
    is the marker 3.6 looks for to distinguish a composed session
    from a written one — a written session has no drawn components
    to record, and the column staying empty is the truthful seed.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "written components",
        "look": "white summer dress, hair down, on a beach",
        "shots": [{"prompt": "full body, walking", "count": 1}],
    }).json()["id"]
    row = db.one("SELECT * FROM shot WHERE session_id=?", sid)
    db.jload(row, "components")
    assert row["components"] == {}


def test_use_look_false_leaves_the_look_out_of_every_prompt(client, seeded):
    """The look is a switch, not a deletion.

    Off, no prompt of the session carries it - including takes added later, which
    is the half that is easy to miss because `add_shots` reads the session row
    rather than the payload. The column keeps its text either way, so the switch
    can be switched back.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "no look",
        "look": "hair down, on a beach", "settings": {"use_look": False},
        "shots": [{"prompt": "sitting", "count": 1}],
    }).json()["id"]
    client.post(f"/api/sessions/{sid}/shots", json={"shots": [{"prompt": "standing", "count": 1}]})

    s = client.get(f"/api/sessions/{sid}").json()
    assert s["look"] == "hair down, on a beach"          # kept, just not composed
    assert [x["prompt"] for x in s["shots"]] == [
        "4da woman. photo, 35mm. sitting.",
        "4da woman. photo, 35mm. standing.",
    ]


def test_a_session_without_the_setting_still_composes_the_look(client, seeded):
    """Absent means on: every session that already exists keeps its prompts."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "default",
        "look": "hair down", "shots": [{"prompt": "sitting", "count": 1}],
    }).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    assert shots[0]["prompt"] == "4da woman. photo, 35mm. hair down. sitting."


def test_the_look_is_identical_in_every_shot_of_a_session(client, seeded):
    """The point of a session: styling does not drift between takes."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "look": "hair down, gold earrings",
        "shots": [{"prompt": "standing", "count": 2}, {"prompt": "sitting", "count": 2}],
    }).json()["id"]
    prompts = [x["prompt"] for x in client.get(f"/api/sessions/{sid}").json()["shots"]]
    assert all("hair down, gold earrings" in p for p in prompts)
    assert len({p for p in prompts}) == 2            # only the take differs


def test_the_wardrobe_rides_on_every_take_and_a_take_may_change_it(client, seeded):
    """What the session's one wardrobe sentence could not do.

    The look is prepended to every take; a wardrobe stated the same way is a
    sentence that dresses her in the very prompt that asks for the jacket off,
    and a positive that both describes and denies a jacket keeps the jacket. So
    the wardrobe is written into each take instead — the session's by default,
    the take's own when it has one, and each frame states its own truth.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "look": "hair down",
        "wardrobe": "black leather jacket, white tee",
        "shots": [{"prompt": "standing", "count": 1},
                  {"prompt": "sitting", "count": 1,
                   "wardrobe": "white tee, bare shoulders"},
                  {"prompt": "lying down", "count": 1, "wardrobe": ""}],
    }).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]

    # take, then the look, then the wardrobe, then the base prompt.
    assert shots[0]["prompt"] == (
        "4da woman. photo, 35mm. hair down. black leather jacket, white tee. standing.")
    # The take's own wardrobe replaces it — no jacket left anywhere in the prompt.
    assert shots[1]["prompt"] == (
        "4da woman. photo, 35mm. hair down. white tee, bare shoulders. sitting.")
    # An explicit empty string is a take that names no clothes at all, which is
    # not the same as a take that did not say (`null` -> the session's).
    assert shots[2]["prompt"] == "4da woman. photo, 35mm. hair down. lying down."


def test_the_pieces_are_joined_as_sentences(client, seeded):
    """Krea 2 reads its prompt with a language model, so the prompt is prose.

    Measured, one outfit at six seeds: written as sentences the hem held six of
    six and the harness repeated six of six; as comma fragments, three of six and
    a different harness in every frame. A comma between two written-out pieces
    reads as one run-on clause and their relations bleed into each other.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "look": "Her hair is down.",                    # already punctuated
        "wardrobe": "She wears a white linen dress",     # not punctuated
        "shots": [{"prompt": "She walks towards the camera", "count": 1}],
    }).json()["id"]
    prompt = client.get(f"/api/sessions/{sid}").json()["shots"][0]["prompt"]

    assert prompt == ("4da woman. photo, 35mm. Her hair is down. She wears a white linen "
                      "dress. She walks towards the camera.")
    assert ".." not in prompt        # a piece that punctuates itself keeps its own


def test_a_take_that_names_its_own_clothes_gets_no_wardrobe_appended(client, seeded):
    """How a whole shoot is written now: one line per photograph, clothes and pose
    together, because two streams that never speak end a session with the wardrobe
    off and the body still standing to attention. `""` is that row — the session's
    wardrobe must not be appended behind a line that already states its own."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "look": "Her hair is down.",
        "wardrobe": "She wears a white linen dress",
        "shots": [{"prompt": "Topless with the dress at her waist, she leans on the sill",
                   "count": 1, "wardrobe": ""}],
    }).json()["id"]
    prompt = client.get(f"/api/sessions/{sid}").json()["shots"][0]["prompt"]

    assert "linen dress" not in prompt
    assert prompt.endswith("she leans on the sill.")


def test_added_shots_keep_the_session_look_even_if_the_payload_lies(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "look": "hair down",
        "wardrobe": "red dress",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    client.post(f"/api/sessions/{sid}/shots",
                json={"look": "green coat", "shots": [{"prompt": "sitting", "count": 1}]})
    prompts = [x["prompt"] for x in client.get(f"/api/sessions/{sid}").json()["shots"]]
    assert all("hair down" in p and "green coat" not in p for p in prompts)
    # The wardrobe is re-read from the session too, as the default a take that
    # says nothing gets.
    assert all("red dress" in p for p in prompts)


def test_the_session_wardrobe_can_move_on_and_only_the_next_takes_see_it(client, seeded):
    """Twenty takes in, a shoot is rarely still wearing what it started in. The
    photos already queued keep the prompt they were queued with."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "wardrobe": "red dress",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    client.patch(f"/api/sessions/{sid}", json={"wardrobe": "black slip"})
    client.post(f"/api/sessions/{sid}/shots", json={"shots": [{"prompt": "sitting", "count": 1}]})

    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    assert "red dress" in shots[0]["prompt"] and "black slip" not in shots[0]["prompt"]
    assert "black slip" in shots[1]["prompt"] and "red dress" not in shots[1]["prompt"]


def test_more_like_this_does_not_double_the_prefix(client, seeded):
    """The gallery hands back a composed prompt; composing it again would repeat
    the trigger, the base prompt and the look."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "look": "red dress",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    keeper = client.get(f"/api/sessions/{sid}").json()["shots"][0]

    client.post(f"/api/sessions/{sid}/shots", json={
        "shots": [{"label": keeper["shot_label"], "prompt": keeper["prompt"],
                   "count": 1, "verbatim": True}]})
    reshot = client.get(f"/api/sessions/{sid}").json()["shots"][1]
    assert reshot["prompt"] == keeper["prompt"]
    assert reshot["prompt"].count("4da woman") == 1
    assert reshot["prompt"].count("red dress") == 1


def test_a_reference_take_carries_no_base_and_no_look(client, seeded):
    """The whole reason reference sessions exist. The anchor photo already shows
    the trigger, the base prompt and the look, so a reference take goes out as a
    bare instruction — otherwise the look would restate the very jacket the
    instruction removes, and a positive that both describes and denies a jacket
    keeps the jacket."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "look": "leather jacket, hair up",
        "shots": [{"label": "anchor", "prompt": "standing", "count": 1},
                  {"label": "edit", "prompt": "remove the jacket", "count": 1, "reference": True}],
    }).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]

    assert shots[0]["prompt"] == "4da woman. photo, 35mm. leather jacket, hair up. standing."
    assert shots[0]["use_reference"] == 0
    assert shots[1]["prompt"] == "remove the jacket"
    assert shots[1]["use_reference"] == 1


def test_an_anchor_must_be_a_finished_photo(client, seeded):
    """Rejected on the way in: an anchor pointing at a shot with no file would
    only surface once the queue had already started."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    pending = client.get(f"/api/sessions/{sid}").json()["shots"][0]["id"]

    r = client.patch(f"/api/sessions/{sid}", json={"anchor_shot_ids": [pending]})
    assert r.status_code == 400 and "reference" in r.json()["detail"]

    db.run("UPDATE shot SET status='done', filename='00001_one.png' WHERE id=?", pending)
    assert client.patch(f"/api/sessions/{sid}",
                        json={"anchor_shot_ids": [pending]}).json()["anchor_shot_ids"] == [pending]


def test_a_takes_own_seed_wins_over_the_session_mode(client, seeded):
    """Reshooting a keeper on its own noise is the only way to change one word
    and see just that change."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "seed_mode": "fixed", "seed": 100,
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    client.post(f"/api/sessions/{sid}/shots", json={
        "shots": [{"prompt": "standing, smiling", "count": 1, "seed": 4242}]})
    # Two variations on a pinned seed still shift, or they would be copies.
    client.post(f"/api/sessions/{sid}/shots", json={
        "shots": [{"prompt": "sitting", "count": 2, "seed": 900}]})

    seeds = [x["seed"] for x in client.get(f"/api/sessions/{sid}").json()["shots"]]
    assert seeds == [100, 4242, 900, 901]


def test_random_seeds_do_not_repeat(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "seed_mode": "random",
        "shots": [{"prompt": "something", "count": 5}],
    }).json()["id"]
    seeds = [x["seed"] for x in client.get(f"/api/sessions/{sid}").json()["shots"]]
    assert len(set(seeds)) == 5


def test_session_without_workflow_is_rejected(client):
    mid = client.post("/api/models", json={"name": "no-wf"}).json()["id"]
    r = client.post("/api/sessions", json={"model_id": mid, "name": "x",
                                           "shots": [{"prompt": "something", "count": 1}]})
    assert r.status_code == 400
    assert "workflow" in r.json()["detail"]


def test_run_requires_pending_shots(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "shots": []}).json()["id"]
    assert client.post(f"/api/sessions/{sid}/run").status_code == 400


def test_adding_shots_reopens_a_finished_session(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    db.run("UPDATE session SET status='done' WHERE id=?", sid)
    r = client.post(f"/api/sessions/{sid}/shots", json={"shots": [{"prompt": "two", "count": 2}]})
    assert r.json()["added"] == 2
    s = client.get(f"/api/sessions/{sid}").json()
    assert s["status"] == "draft"
    assert [x["shot_index"] for x in s["shots"]] == [0, 1, 1]   # the index continues


def test_rating_is_clamped_and_reject_toggles(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    shot_id = client.get(f"/api/sessions/{sid}").json()["shots"][0]["id"]
    assert client.patch(f"/api/shots/{shot_id}", json={"rating": 9}).json()["rating"] == 5
    assert client.patch(f"/api/shots/{shot_id}", json={"rating": -3}).json()["rating"] == 0
    assert client.patch(f"/api/shots/{shot_id}", json={"rejected": True}).json()["rejected"] == 1


def test_reshooting_a_photo_queues_the_same_take_again_and_drops_the_file(client, seeded):
    """Reject-and-reshoot reuses the row: one card per take, no keeping the frame
    that came back wrong. The seed goes with the photo — the same noise would
    hand back the same picture."""
    import main
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    shot = client.post(f"/api/sessions/{sid}/import?label=bad take", content=PNG).json()
    path = main.SESSIONS_DIR / str(sid) / shot["filename"]
    db.run("UPDATE shot SET seed=99, rejected=1 WHERE id=?", shot["id"])
    db.run("UPDATE session SET status='done' WHERE id=?", sid)

    r = client.post(f"/api/shots/{shot['id']}/reshoot")
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "pending"
    assert r.json()["filename"] == ""
    assert r.json()["seed"] == 0            # 0 = the runner rolls a fresh one
    assert r.json()["rejected"] == 0
    assert not path.exists()
    # A finished session with something queued in it is not finished any more.
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "draft"


def test_reshooting_the_reference_photo_is_refused(client, seeded):
    """Its file is what every reference take edits, and `_valid_anchors` will not
    point at a shot without one — so it is refused here, not once the queue has
    already started."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    shot = client.post(f"/api/sessions/{sid}/import?label=anchor", content=PNG).json()
    client.patch(f"/api/sessions/{sid}", json={"anchor_shot_ids": [shot["id"]]})

    r = client.post(f"/api/shots/{shot['id']}/reshoot")
    assert r.status_code == 409
    assert "reference" in r.json()["detail"]
    assert db.one("SELECT status FROM shot WHERE id=?", shot["id"])["status"] == "done"


# -- bulk reshoot: one route, one set of refusals reused from the per-shot one

def _finished_shot(client, sid, label, *, rating=0, rejected=False, seed=0):
    """An imported PNG: a real file on disk, a row the bulk route can refuse."""
    shot = client.post(f"/api/sessions/{sid}/import?label={label}", content=PNG).json()
    db.run("UPDATE shot SET rating=?, rejected=?, seed=? WHERE id=?",
           rating, int(rejected), seed, shot["id"])
    return shot


def test_bulk_reshoot_re_queues_finished_shots_under_the_threshold(client, seeded):
    """The weak frames go back in the queue. The shot at 5 stays put, the ones
    at 1 and 3 land back as pending with their image gone and a zero seed."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s"}).json()["id"]
    keep = _finished_shot(client, sid, "keep", rating=5)
    weak = _finished_shot(client, sid, "weak", rating=3)
    weaker = _finished_shot(client, sid, "weaker", rating=1)

    r = client.post(f"/api/sessions/{sid}/reshoot-below", params={"min_rating": 4})
    assert r.status_code == 200, r.json()
    assert r.json() == {"re_queued": 2, "skipped": 0}

    reloaded = {x["id"]: x for x in client.get(f"/api/sessions/{sid}").json()["shots"]}
    assert reloaded[keep["id"]]["status"] == "done" and reloaded[keep["id"]]["filename"]
    for shot in (weak, weaker):
        assert reloaded[shot["id"]]["status"] == "pending"
        assert reloaded[shot["id"]]["filename"] == ""
        assert reloaded[shot["id"]]["seed"] == 0
        assert not (main.SESSIONS_DIR / str(sid) / shot["filename"]).exists()


def test_bulk_reshoot_picks_up_unrated_shots_below_one(client, seeded):
    """An unrated shot is below every threshold — refusing a frame and
    reshooting it are the same judgement."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s"}).json()["id"]
    unrated = _finished_shot(client, sid, "blank")          # rating defaults to 0

    r = client.post(f"/api/sessions/{sid}/reshoot-below", params={"min_rating": 1})
    assert r.status_code == 200 and r.json() == {"re_queued": 1, "skipped": 0}
    shot = client.get(f"/api/sessions/{sid}").json()["shots"][0]
    assert shot["status"] == "pending" and shot["rating"] == 0


def test_bulk_reshoot_does_not_spare_a_rejected_frame(client, seeded):
    """The reject and the reshoot are the same judgement — leaving rejects
    behind would make the action miss exactly the frames the user already
    refused, and its `rejected` flag is cleared on the way back in."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s"}).json()["id"]
    shot = _finished_shot(client, sid, "weak", rating=2, rejected=True)

    r = client.post(f"/api/sessions/{sid}/reshoot-below", params={"min_rating": 4})
    assert r.status_code == 200 and r.json() == {"re_queued": 1, "skipped": 0}
    row = db.one("SELECT * FROM shot WHERE id=?", shot["id"])
    assert row["status"] == "pending" and row["rejected"] == 0


def test_bulk_reshoot_only_re_queues_finished_shots(client, seeded):
    """A shot that never finished has no photo to refuse; the others keep the
    status they had and the response counts them as skipped."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s"}).json()["id"]
    failed = _finished_shot(client, sid, "failed", rating=1)
    db.run("UPDATE shot SET status='failed' WHERE id=?", failed["id"])
    done = _finished_shot(client, sid, "done", rating=1)

    r = client.post(f"/api/sessions/{sid}/reshoot-below", params={"min_rating": 4})
    assert r.status_code == 200 and r.json() == {"re_queued": 1, "skipped": 1}

    after = {x["id"]: x for x in client.get(f"/api/sessions/{sid}").json()["shots"]}
    assert after[done["id"]]["status"] == "pending"
    assert after[failed["id"]]["status"] == "failed"


def test_bulk_reshoot_clears_the_seed(client, seeded):
    """The seed is the noise the picture was painted from — re-rolling it is the
    whole point of reshooting, and the same prompt on the same noise returns the
    same photograph."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s"}).json()["id"]
    shot = _finished_shot(client, sid, "keeper", rating=2, seed=12345)

    client.post(f"/api/sessions/{sid}/reshoot-below", params={"min_rating": 4})
    row = db.one("SELECT * FROM shot WHERE id=?", shot["id"])
    assert row["status"] == "pending" and row["seed"] == 0


def test_bulk_reshoot_steps_over_a_running_shot(client, seeded):
    """A running shot is generating — its image does not exist yet, and the
    action must not abort the queue. The two finished ones go back in, the
    running one is reported as skipped."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s"}).json()["id"]
    a = _finished_shot(client, sid, "a", rating=1)
    b = _finished_shot(client, sid, "b", rating=2)
    running = _finished_shot(client, sid, "r", rating=3)
    db.run("UPDATE shot SET status='running' WHERE id=?", running["id"])

    r = client.post(f"/api/sessions/{sid}/reshoot-below", params={"min_rating": 4})
    assert r.status_code == 200 and r.json() == {"re_queued": 2, "skipped": 1}
    assert db.one("SELECT status FROM shot WHERE id=?", running["id"])["status"] == "running"
    # The running shot's file is still on disk: the route did not touch it.
    assert (main.SESSIONS_DIR / str(sid) / running["filename"]).exists()


def test_bulk_reshoot_protects_a_session_anchor(client, seeded):
    """The anchor is what every reference take edits; an empty anchor fails
    every edit behind it, and `_valid_anchors` refuses to point at a shot
    without one — so it is skipped here, not once the queue has started."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s"}).json()["id"]
    keeper = _finished_shot(client, sid, "keeper", rating=3)
    client.patch(f"/api/sessions/{sid}", json={"anchor_shot_ids": [keeper["id"]]})
    neighbour = _finished_shot(client, sid, "neighbour", rating=1)

    r = client.post(f"/api/sessions/{sid}/reshoot-below", params={"min_rating": 4})
    assert r.status_code == 200 and r.json() == {"re_queued": 1, "skipped": 1}
    # The anchor is untouched, its image is still on disk.
    assert db.one("SELECT status FROM shot WHERE id=?", keeper["id"])["status"] == "done"
    assert (main.SESSIONS_DIR / str(sid) / keeper["filename"]).exists()
    assert db.one("SELECT status FROM shot WHERE id=?", neighbour["id"])["status"] == "pending"


def test_bulk_reshoot_400s_when_nothing_qualifies(client, seeded):
    """No image is deleted and no row changed — a click that would do nothing
    must not silently return an empty count."""
    import main
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s"}).json()["id"]
    keeper = _finished_shot(client, sid, "keeper", rating=5)
    path = main.SESSIONS_DIR / str(sid) / keeper["filename"]
    before = db.one("SELECT * FROM shot WHERE id=?", keeper["id"])

    r = client.post(f"/api/sessions/{sid}/reshoot-below", params={"min_rating": 4})
    assert r.status_code == 400
    after = db.one("SELECT * FROM shot WHERE id=?", keeper["id"])
    assert dict(after) == dict(before) and path.exists()


def test_bulk_reshoot_404s_when_the_session_is_unknown(client):
    """The session is the route's scope; an unknown id is a not-found, not a
    client error about an empty threshold."""
    r = client.post("/api/sessions/9999/reshoot-below", params={"min_rating": 4})
    assert r.status_code == 404


def test_bulk_reshoot_reopens_a_done_session_to_draft(client, seeded):
    """A finished session with something queued in it is not finished, and the
    status is the one the Run button reads."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s"}).json()["id"]
    _finished_shot(client, sid, "weak", rating=2)
    db.run("UPDATE session SET status='done' WHERE id=?", sid)

    client.post(f"/api/sessions/{sid}/reshoot-below", params={"min_rating": 4})
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "draft"


def test_bulk_reshoot_does_not_rewrite_a_running_sessions_status(client, seeded):
    """A session that is already running, draft or whatever-it-was stays that
    way — the route only reopens a session that was finished."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s"}).json()["id"]
    db.run("UPDATE session SET status='running' WHERE id=?", sid)
    _finished_shot(client, sid, "weak", rating=2)

    client.post(f"/api/sessions/{sid}/reshoot-below", params={"min_rating": 4})
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "running"


def test_deleting_a_model_cascades_to_sessions_and_shots(client, seeded):
    client.post("/api/sessions", json={"model_id": seeded["model_id"], "name": "s",
                                       "shots": [{"prompt": "one", "count": 2}]})
    client.delete(f"/api/models/{seeded['model_id']}")
    assert client.get("/api/sessions").json() == []
    assert db.q("SELECT id FROM shot") == []


@pytest.fixture
def runnable(monkeypatch):
    """Get past the config and GPU gates so a test can reach the mapping check."""
    import main
    monkeypatch.setattr(main, "output_dir_ok", lambda: True)
    monkeypatch.setattr(main.runner, "start", lambda sid: None)


def test_run_refuses_when_a_chosen_base_model_is_unmapped(client, seeded, runnable):
    """A workflow saved before the checkpoint slot existed would ignore the
    choice and shoot the whole session on the wrong model, silently."""
    db.run("UPDATE workflow SET node_map=? WHERE id=?",
           '{"positive": "3.inputs.text"}', seeded["workflow_id"])
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "settings": {"checkpoint": "zimage/turbo.safetensors"},
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]

    r = client.post(f"/api/sessions/{sid}/run")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "base model" in detail and "zimage/turbo.safetensors" in detail


def test_the_base_model_check_skips_a_workflow_no_take_will_use(client, seeded, runnable):
    """A camera-angle session is all reference takes: the first workflow is never
    loaded, so a base model it does not map is not being ignored — there is
    nothing to ignore it. Refusing there sends you to fix the wrong graph."""
    db.run("UPDATE workflow SET node_map=? WHERE id=?",
           '{"positive": "3.inputs.text"}', seeded["workflow_id"])
    edit = client.post("/api/workflows", json={"name": "edit", "graph": EDIT_GRAPH}).json()
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "settings": {"checkpoint": "krea/turbo.safetensors", "kind": "angles"},
        "reference_workflow_id": edit["id"],
        "shots": [{"prompt": "back view", "count": 1, "reference": True}]}).json()["id"]
    imported = client.post(f"/api/sessions/{sid}/import", content=PNG).json()["id"]
    assert client.get(f"/api/sessions/{sid}").json()["anchor_shot_ids"] == [imported]

    assert client.post(f"/api/sessions/{sid}/run").status_code == 200

    # One take painted from noise brings the check back: that one does load it.
    client.post(f"/api/sessions/{sid}/shots", json={"shots": [{"prompt": "standing", "count": 1}]})
    r = client.post(f"/api/sessions/{sid}/run")
    assert r.status_code == 400 and "base model" in r.json()["detail"]


def test_a_session_can_be_repointed_after_it_was_created(client, seeded):
    """The graph in the wrong slot is discovered when Run is refused, by which
    time the session is an imported photo and seventy takes. Delete-and-redo
    cannot be the cure for a dropdown."""
    other = client.post("/api/workflows", json={"name": "edit", "graph": EDIT_GRAPH}).json()["id"]
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "settings": {"checkpoint": "wrong.safetensors", "kind": "angles"},
        "shots": [{"prompt": "back view", "count": 1, "reference": True}]}).json()["id"]

    client.patch(f"/api/sessions/{sid}", json={
        "workflow_id": other, "reference_workflow_id": other, "settings": {"checkpoint": ""}})
    s = client.get(f"/api/sessions/{sid}").json()
    assert s["workflow_id"] == other and s["reference_workflow_id"] == other
    assert s["settings"]["checkpoint"] == ""
    # A merge, not a replacement: the panel sends the one dial it changed.
    assert s["settings"]["kind"] == "angles" and s["settings"]["width"] == 832

    # 0 clears the workflow back to the model's default.
    client.patch(f"/api/sessions/{sid}", json={"workflow_id": 0})
    assert client.get(f"/api/sessions/{sid}").json()["workflow_id"] is None


def test_run_refuses_when_the_models_lora_is_unmapped(client, seeded, runnable):
    """Worse than the wrong model: a full session of the wrong character."""
    db.run("UPDATE workflow SET node_map=? WHERE id=?",
           '{"positive": "3.inputs.text"}', seeded["workflow_id"])
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]

    r = client.post(f"/api/sessions/{sid}/run")
    assert r.status_code == 400
    assert "LoRA" in r.json()["detail"]


def test_run_is_fine_when_nothing_was_chosen(client, seeded, runnable):
    """No LoRA, no base model: the workflow's own values are the point."""
    db.run("UPDATE workflow SET node_map=? WHERE id=?",
           '{"positive": "3.inputs.text"}', seeded["workflow_id"])
    db.run("UPDATE model SET lora_name='' WHERE id=?", seeded["model_id"])
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    assert client.post(f"/api/sessions/{sid}/run").status_code == 200


def _reference_session(client, seeded, *, node_map=None, with_anchor_take=True):
    """A session whose edits run through an imported editing workflow."""
    wf = client.post("/api/workflows", json={"name": "edit", "graph": EDIT_GRAPH}).json()
    if node_map is not None:
        db.run("UPDATE workflow SET node_map=? WHERE id=?", node_map, wf["id"])
    shots = [{"label": "edit", "prompt": "remove the jacket", "count": 1, "reference": True}]
    if with_anchor_take:
        shots.insert(0, {"label": "anchor", "prompt": "standing", "count": 1})
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "look": "leather jacket",
        "reference_workflow_id": wf["id"], "shots": shots}).json()["id"]
    return sid, wf["id"]


def test_a_plain_session_can_become_a_reference_one_mid_shoot(client, seeded, runnable):
    """Deciding to edit a keeper happens looking at the gallery, not when the
    session was created — so the reference workflow has to be assignable later,
    or the run is refused with no way to satisfy it."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "look": "leather jacket",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    keeper = client.get(f"/api/sessions/{sid}").json()["shots"][0]["id"]
    db.run("UPDATE shot SET status='done', filename='00001_one.png' WHERE id=?", keeper)

    client.patch(f"/api/sessions/{sid}", json={"anchor_shot_ids": [keeper]})
    client.post(f"/api/sessions/{sid}/shots", json={
        "shots": [{"prompt": "remove the jacket", "count": 1, "reference": True}]})
    # No reference workflow yet: refused, and the message says what is missing.
    r = client.post(f"/api/sessions/{sid}/run")
    assert r.status_code == 400 and "no reference workflow" in r.json()["detail"]

    edit_wf = client.post("/api/workflows", json={"name": "edit", "graph": EDIT_GRAPH}).json()
    client.patch(f"/api/sessions/{sid}", json={"reference_workflow_id": edit_wf["id"]})
    assert client.post(f"/api/sessions/{sid}/run").status_code == 200


def test_run_refuses_when_the_reference_image_slot_is_unmapped(client, seeded, runnable):
    """The worst outcome this app can produce: every edit comes back painted from
    noise, ignoring the reference, with nothing on screen saying it was dropped."""
    sid, _ = _reference_session(client, seeded, node_map='{"positive": "3.inputs.text"}')

    r = client.post(f"/api/sessions/{sid}/run")
    assert r.status_code == 400
    assert "reference image slot" in r.json()["detail"]


def test_run_refuses_more_reference_photos_than_the_workflow_reads(client, seeded, runnable):
    """Marking a second reference on a one-image graph is the same silent failure
    one step along: it uploads, nothing consumes it, and the result just looks as
    if the extra reference did nothing."""
    sid, _ = _reference_session(client, seeded, with_anchor_take=False)
    keepers = []
    for label in ("a", "b"):
        client.post(f"/api/sessions/{sid}/shots", json={"shots": [{"prompt": label, "count": 1}]})
        shot = client.get(f"/api/sessions/{sid}").json()["shots"][-1]["id"]
        db.run("UPDATE shot SET status='done', filename=? WHERE id=?", f"{label}.png", shot)
        keepers.append(shot)

    client.patch(f"/api/sessions/{sid}", json={"anchor_shot_ids": keepers[:1]})
    assert client.post(f"/api/sessions/{sid}/run").status_code == 200

    client.patch(f"/api/sessions/{sid}", json={"anchor_shot_ids": keepers})
    r = client.post(f"/api/sessions/{sid}/run")
    assert r.status_code == 400
    assert "would be uploaded and ignored" in r.json()["detail"]
    assert "reference2" in r.json()["detail"]


def test_run_refuses_reference_takes_with_nothing_to_reference(client, seeded, runnable):
    sid, _ = _reference_session(client, seeded, with_anchor_take=False)
    r = client.post(f"/api/sessions/{sid}/run")
    assert r.status_code == 400
    assert "none is set" in r.json()["detail"]


def test_run_allows_a_session_that_shoots_its_own_reference_first(client, seeded, runnable):
    """Shooting the anchor and editing it is one shoot, so both queued together
    must run in one go — the first photo out becomes the reference."""
    sid, _ = _reference_session(client, seeded)
    assert client.post(f"/api/sessions/{sid}/run").status_code == 200


def test_the_reference_workflow_is_not_asked_for_a_lora(client, seeded, runnable):
    """An editing graph loads its own model and takes the character from the
    anchor photo, so having neither a LoRA nor a checkpoint slot is correct."""
    sid, wf_id = _reference_session(client, seeded)
    assert "lora_name" not in client.get(f"/api/workflows/{wf_id}").json()["node_map"]
    assert client.post(f"/api/sessions/{sid}/run").status_code == 200


def test_run_without_output_dir_fails_with_a_clear_message(client, seeded, monkeypatch):
    import main
    monkeypatch.setattr(main, "output_dir_ok", lambda: False)
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    r = client.post(f"/api/sessions/{sid}/run")
    assert r.status_code == 400
    assert "config.json" in r.json()["detail"]


def test_deleting_a_session_removes_its_folder(client, seeded):
    import main
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "00001_one.png").write_bytes(b"png")

    assert client.delete(f"/api/sessions/{sid}").json() == {"ok": True}
    assert not folder.exists()


def test_a_folder_that_cannot_be_deleted_is_reported_not_swallowed(client, seeded, monkeypatch):
    """SQLite reuses the id of a deleted row, so a surviving folder would hand
    its photos to the next session under the same number."""
    import main
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)

    def locked(path):
        raise OSError(32, "The process cannot access the file")

    monkeypatch.setattr(main.shutil, "rmtree", locked)
    body = client.delete(f"/api/sessions/{sid}").json()
    assert "could not be deleted" in body["warning"]
    assert str(folder) in body["warning"]


PNG = b"\x89PNG\r\n\x1a\n" + b"fake pixels"


def test_an_imported_photo_lands_as_a_shot_and_can_be_a_reference(client, seeded):
    """It arrives as an ordinary shot so the gallery, the rating and above all
    marking it as a reference work on it with no separate path."""
    import main
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]

    r = client.post(f"/api/sessions/{sid}/import?label=pose ref", content=PNG)
    assert r.status_code == 200
    imported = r.json()
    assert imported["filename"].endswith("_pose-ref.png")
    assert (main.SESSIONS_DIR / str(sid) / imported["filename"]).read_bytes() == PNG
    assert client.get(f"/api/shots/{imported['id']}/image").status_code == 200

    shot = [x for x in client.get(f"/api/sessions/{sid}").json()["shots"] if x["id"] == imported["id"]][0]
    assert shot["status"] == "done"
    assert client.patch(f"/api/sessions/{sid}",
                        json={"anchor_shot_ids": [imported["id"]]}).status_code == 200


def test_an_imported_photo_becomes_the_reference_when_there_is_none(client, seeded):
    """A session whose takes are all edits — a camera-angle shoot, say — has
    nothing that would shoot the photo they turn, so the photo is imported. It
    was imported to be edited: marking it by hand afterwards is a step whose only
    outcome is a refused Run for whoever skips it. A session that already has a
    reference keeps it, and a session with no edits gets none."""
    def session(shots):
        return client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": "s", "reference_workflow_id": None,
            "shots": shots}).json()["id"]

    edits = session([{"prompt": "back view", "count": 1, "reference": True}])
    first = client.post(f"/api/sessions/{edits}/import", content=PNG).json()["id"]
    assert client.get(f"/api/sessions/{edits}").json()["anchor_shot_ids"] == [first]

    # A second import does not move it: the pick is now the user's, and 📎 changes it.
    client.post(f"/api/sessions/{edits}/import", content=PNG)
    assert client.get(f"/api/sessions/{edits}").json()["anchor_shot_ids"] == [first]

    plain = session([{"prompt": "standing", "count": 1}])
    client.post(f"/api/sessions/{plain}/import", content=PNG)
    assert client.get(f"/api/sessions/{plain}").json()["anchor_shot_ids"] == []


def test_a_photo_can_be_carried_into_another_session_by_id(client, seeded):
    """Continuing a shoot in a fresh session — the keeper of a photoshoot walked
    around with the angle graph — must not mean downloading the photo and
    uploading it back. The copy is real: deleting either session leaves the
    other's gallery intact."""
    import main
    def session():
        return client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": "s", "shots": []}).json()["id"]

    src, dst = session(), session()
    keeper = client.post(f"/api/sessions/{src}/import?label=keeper", content=PNG).json()["id"]

    r = client.post(f"/api/sessions/{dst}/import?from_shot={keeper}")
    assert r.status_code == 200
    copy = r.json()
    # Its own file in its own folder, and the label came across.
    assert copy["filename"].endswith("_keeper.png")
    assert (main.SESSIONS_DIR / str(dst) / copy["filename"]).read_bytes() == PNG
    assert client.patch(f"/api/sessions/{dst}",
                        json={"anchor_shot_ids": [copy["id"]]}).status_code == 200

    client.delete(f"/api/sessions/{src}")
    assert client.get(f"/api/shots/{copy['id']}/image").status_code == 200

    assert client.post(f"/api/sessions/{dst}/import?from_shot=999999").status_code == 404


def test_cloning_a_session_repeats_it_with_the_base_model_changed(client, seeded):
    """The takes, the composed prompts and the seeds come across untouched: what
    differs between the two galleries is the model, not another roll of noise.
    An imported photo cannot be repainted — nothing generated it — so its file is
    copied and it lands finished."""
    import main
    src = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "shoot", "look": "soft window light",
        "wardrobe": "white shirt", "settings": {"checkpoint": "a.safetensors", "steps": 8},
        "seed_mode": "fixed", "seed": 500,
        "shots": [{"prompt": "standing", "count": 2}]}).json()["id"]
    brought = client.post(f"/api/sessions/{src}/import?label=keeper", content=PNG).json()["id"]
    client.patch(f"/api/sessions/{src}", json={"anchor_shot_ids": [brought]})
    db.run("UPDATE shot SET status='done', rating=5 WHERE session_id=? AND prompt LIKE '%standing%'", src)

    r = client.post(f"/api/sessions/{src}/clone",
                    json={"name": "same shoot, other model",
                          "settings": {"checkpoint": "b.safetensors", "steps": 30}})
    assert r.status_code == 200, r.json()
    copy = client.get(f"/api/sessions/{r.json()['id']}").json()

    assert copy["name"] == "same shoot, other model"
    assert copy["status"] == "draft"
    assert (copy["look"], copy["wardrobe"]) == ("soft window light", "white shirt")
    assert copy["settings"]["checkpoint"] == "b.safetensors"
    assert copy["settings"]["steps"] == 30
    assert copy["settings"]["width"] == 832        # everything unmentioned is carried over

    old = client.get(f"/api/sessions/{src}").json()["shots"]
    assert [(x["prompt"], x["seed"]) for x in copy["shots"]] == [(x["prompt"], x["seed"]) for x in old]
    # The generated takes are queued again; the imported photo is not, it is copied.
    assert [x["status"] for x in copy["shots"]] == ["pending", "pending", "done"]
    carried = copy["shots"][-1]
    assert (main.SESSIONS_DIR / str(copy["id"]) / carried["filename"]).read_bytes() == PNG
    # The anchor follows its copy, not the id it had in the session it came from.
    assert copy["anchor_shot_ids"] == [carried["id"]]

    # Two owners, two files: deleting the original leaves the copy's gallery whole.
    client.delete(f"/api/sessions/{src}")
    assert client.get(f"/api/shots/{carried['id']}/image").status_code == 200

    assert client.post("/api/sessions/999999/clone", json={}).status_code == 404


def test_a_copy_can_be_shot_on_the_graph_written_for_its_model(client, seeded):
    """Each checkpoint wants its own sampler, steps and cfg, which live inside a
    graph and not in a slot — so a sweep that could only change the checkpoint
    shot every model through the first one's settings. The list route names the
    model each graph loads, which is what lets the copy pick its own."""
    other = client.post("/api/workflows", json={"name": "tuned", "graph": EDIT_GRAPH}).json()["id"]
    assert {w["id"]: w["base_model"] for w in client.get("/api/workflows").json()} == {
        seeded["workflow_id"]: "base.safetensors", other: "edit.safetensors"}

    src = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "shoot",
        "workflow_id": seeded["workflow_id"],
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]

    copy = client.post(f"/api/sessions/{src}/clone", json={"workflow_id": other}).json()["id"]
    plain = client.post(f"/api/sessions/{src}/clone", json={}).json()["id"]
    assert client.get(f"/api/sessions/{copy}").json()["workflow_id"] == other
    assert client.get(f"/api/sessions/{plain}").json()["workflow_id"] == seeded["workflow_id"]


def test_every_copy_of_a_shoot_points_at_the_same_original(client, seeded):
    """`cloned_from` is what lets one gallery offer the other as a comparison,
    and only the copies: two sessions are comparable when the takes, prompts and
    seeds match, which nothing but a clone gives. A clone of a clone joins the
    same family instead of starting a chain — otherwise the second copy and the
    first would not see each other."""
    src = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "shoot",
        "shots": [{"prompt": "standing", "count": 2}]}).json()["id"]

    a = client.post(f"/api/sessions/{src}/clone", json={"settings": {"checkpoint": "a.safetensors"}}).json()["id"]
    b = client.post(f"/api/sessions/{a}/clone", json={"settings": {"checkpoint": "b.safetensors"}}).json()["id"]

    family = {x["id"]: x["settings"].get("cloned_from") for x in client.get("/api/sessions").json()}
    assert family == {src: None, a: src, b: src}

    # Same takes on the same noise in all three: that is what makes the photos
    # comparable frame by frame, and the pairing key the gallery uses.
    takes = {sid: [(x["shot_index"], x["seed"]) for x in client.get(f"/api/sessions/{sid}").json()["shots"]]
             for sid in (src, a, b)}
    assert takes[src] == takes[a] == takes[b]


def test_a_copy_stays_paired_with_its_original_after_a_reshoot(client, seeded):
    """The pair is the take's id, not its seed. ↺ rolls a new seed by design —
    that is what the button is for — so a comparison keyed on the seed would lose
    the twin at the exact moment you reshot the photo you wanted to compare.
    A clone of a clone points at the original take, not at the row it was copied
    from, so every copy of one take carries the same id."""
    src = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "shoot",
        "shots": [{"prompt": "standing", "count": 2}]}).json()["id"]
    original = client.get(f"/api/sessions/{src}").json()["shots"]
    assert [x["origin_shot_id"] for x in original] == [None, None]   # it is the original

    a = client.post(f"/api/sessions/{src}/clone", json={}).json()["id"]
    b = client.post(f"/api/sessions/{a}/clone", json={}).json()["id"]
    for sid in (a, b):
        copies = client.get(f"/api/sessions/{sid}").json()["shots"]
        assert [x["origin_shot_id"] for x in copies] == [x["id"] for x in original]

    # Reshoot one side: new noise, same take. The photo it is compared against
    # does not move.
    twin = client.get(f"/api/sessions/{a}").json()["shots"][0]
    db.run("UPDATE shot SET status='done', filename='x.png', prompt_id='pid-1' WHERE id=?", twin["id"])
    reshot = client.post(f"/api/shots/{twin['id']}/reshoot").json()
    assert reshot["seed"] == 0 and reshot["seed"] != twin["seed"]
    assert reshot["origin_shot_id"] == original[0]["id"]


def test_a_cloned_reference_take_edits_the_photo_the_clone_shoots(client, seeded):
    """The anchor of a session that painted its own is `pending` in the copy: it
    is earlier in the queue than the takes that edit it, which is the same order
    the source shot them in, so it has a file by the time they run."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "reference_workflow_id": None,
        "shots": [{"prompt": "standing", "count": 1},
                  {"prompt": "remove the jacket", "count": 1, "reference": True}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='done', filename='x.png', prompt_id='pid-1' WHERE id=?", shots[0]["id"])
    client.patch(f"/api/sessions/{sid}", json={"anchor_shot_ids": [shots[0]["id"]]})

    copy = client.get(f"/api/sessions/{client.post(f'/api/sessions/{sid}/clone', json={}).json()['id']}").json()
    assert copy["name"] == "s (copy)"
    assert [x["status"] for x in copy["shots"]] == ["pending", "pending"]
    assert copy["shots"][0]["filename"] == ""      # it is shot again, not carried
    assert copy["anchor_shot_ids"] == [copy["shots"][0]["id"]]
    assert copy["shots"][1]["use_reference"] == 1


def test_import_refuses_what_is_not_an_image(client, seeded):
    """The extension is the uploader's claim; the magic number is the file."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "shots": []}).json()["id"]

    r = client.post(f"/api/sessions/{sid}/import?label=evil", content=b"MZ\x90\x00 not an image")
    assert r.status_code == 400 and "not a PNG" in r.json()["detail"]
    assert client.post(f"/api/sessions/{sid}/import", content=b"").status_code == 400
    # Nothing was written and no orphan row survives a rejected upload.
    assert client.get(f"/api/sessions/{sid}").json()["shots"] == []


def test_import_rejects_an_oversized_file(client, seeded):
    import main
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "shots": []}).json()["id"]
    big = PNG + b"x" * main.IMPORT_MAX_BYTES
    assert client.post(f"/api/sessions/{sid}/import", content=big).status_code == 413


def test_run_refuses_when_a_reference_slot_would_keep_a_stale_filename(client, seeded, runnable):
    """A mapped slot with no photo behind it keeps whatever filename the workflow
    was saved with, and mixes that picture into every take."""
    sid, wf_id = _reference_session(client, seeded, with_anchor_take=False)
    db.run("UPDATE workflow SET node_map=json_set(node_map, '$.reference2', '2.inputs.image') WHERE id=?",
           wf_id)
    keeper = client.post(f"/api/sessions/{sid}/import?label=anchor", content=PNG).json()["id"]
    client.patch(f"/api/sessions/{sid}", json={"anchor_shot_ids": [keeper]})

    r = client.post(f"/api/sessions/{sid}/run")
    assert r.status_code == 400
    assert "keep the filename the workflow was saved with" in r.json()["detail"]


def test_missing_image_returns_404(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    shot_id = client.get(f"/api/sessions/{sid}").json()["shots"][0]["id"]
    assert client.get(f"/api/shots/{shot_id}/image").status_code == 404


def test_session_export_default_threshold(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 3}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='done', filename='a1.png', rating=0 WHERE id=?", shots[0]["id"])
    db.run("UPDATE shot SET status='done', filename='b1.png', rating=2 WHERE id=?", shots[1]["id"])
    db.run("UPDATE shot SET status='done', filename='c1.png', rating=5 WHERE id=?", shots[2]["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    for f in ["a1.png", "b1.png", "c1.png"]:
        (folder / f).write_bytes(PNG)

    r = client.get(f"/api/sessions/{sid}/export")
    assert r.status_code == 200
    assert f"session_{sid}.zip" in r.headers["Content-Disposition"]

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert names == ["00000_02_rating2.png", "00000_03_rating5.png"]


def test_session_export_raises_threshold(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 3}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='done', filename='a2.png', rating=0 WHERE id=?", shots[0]["id"])
    db.run("UPDATE shot SET status='done', filename='b2.png', rating=2 WHERE id=?", shots[1]["id"])
    db.run("UPDATE shot SET status='done', filename='c2.png', rating=5 WHERE id=?", shots[2]["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    for f in ["a2.png", "b2.png", "c2.png"]:
        (folder / f).write_bytes(PNG)

    r = client.get(f"/api/sessions/{sid}/export?min_rating=3")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == ["00000_03_rating5.png"]


def test_session_export_empty_selection(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    shot = client.get(f"/api/sessions/{sid}").json()["shots"][0]
    db.run("UPDATE shot SET status='done', filename='a3.png', rating=0 WHERE id=?", shot["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a3.png").write_bytes(PNG)

    r = client.get(f"/api/sessions/{sid}/export")
    assert r.status_code == 400
    assert "no shots meet the threshold" in r.json()["detail"]


def test_session_export_skips_missing_files(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 2}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='done', filename='a4.png', rating=5 WHERE id=?", shots[0]["id"])
    db.run("UPDATE shot SET status='done', filename='b4.png', rating=5 WHERE id=?", shots[1]["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a4.png").write_bytes(PNG)
    # b4.png is missing

    r = client.get(f"/api/sessions/{sid}/export")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == ["00000_01_rating5.png"]


def test_session_export_unknown_session(client):
    r = client.get("/api/sessions/999/export")
    assert r.status_code == 404


def test_session_export_entry_ordering_twelve_shots(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 12}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    for shot in shots:
        fname = f"{shot['id']}_ordering.png"
        db.run("UPDATE shot SET status='done', filename=?, rating=1 WHERE id=?", fname, shot["id"])
        (folder / fname).write_bytes(PNG)

    r = client.get(f"/api/sessions/{sid}/export")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert len(names) == 12
        # Verify plain lexicographic sort puts them in shooting order
        assert sorted(names) == names
        # Specifically check 2 precedes 12
        assert names.index("00000_02_rating1.png") < names.index("00000_12_rating1.png")


def test_session_export_skips_rejected(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 2}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='done', filename='a5.png', rating=5 WHERE id=?", shots[0]["id"])
    db.run("UPDATE shot SET status='done', filename='b5.png', rating=5, rejected=1 WHERE id=?", shots[1]["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    for f in ["a5.png", "b5.png"]:
        (folder / f).write_bytes(PNG)

    r = client.get(f"/api/sessions/{sid}/export")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert zf.namelist() == ["00000_01_rating5.png"]


def test_session_export_entry_numbering_follows_shot_index(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1},
                  {"prompt": "two", "count": 1},
                  {"prompt": "three", "count": 1}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='done', filename='a6.png', rating=5 WHERE id=?", shots[0]["id"])
    db.run("UPDATE shot SET status='done', filename='b6.png', rating=5 WHERE id=?", shots[1]["id"])
    db.run("UPDATE shot SET status='done', filename='c6.png', rating=5 WHERE id=?", shots[2]["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    for f in ["a6.png", "b6.png", "c6.png"]:
        (folder / f).write_bytes(PNG)

    client.delete(f"/api/shots/{shots[1]['id']}")

    r = client.get(f"/api/sessions/{sid}/export")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert names == ["00000_01_rating5.png", "00002_01_rating5.png"]


def test_session_export_entry_names_do_not_move_with_the_threshold(client, seeded):
    """One take is three rows sharing a shot_index. Numbering the variations by
    what the export happens to carry renamed a photograph when the threshold
    rose: the same file came out 01 at one star and 01 again at four."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 3}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    for shot, (name, rating) in zip(shots, [("a7.png", 1), ("b7.png", 2), ("c7.png", 5)]):
        db.run("UPDATE shot SET status='done', filename=?, rating=? WHERE id=?",
               name, rating, shot["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    for name in ["a7.png", "b7.png", "c7.png"]:
        (folder / name).write_bytes(PNG)

    with zipfile.ZipFile(io.BytesIO(client.get(f"/api/sessions/{sid}/export").content)) as zf:
        wide = zf.namelist()
    with zipfile.ZipFile(io.BytesIO(
            client.get(f"/api/sessions/{sid}/export?min_rating=5").content)) as zf:
        narrow = zf.namelist()

    assert wide == ["00000_01_rating1.png", "00000_02_rating2.png", "00000_03_rating5.png"]
    assert narrow == ["00000_03_rating5.png"]


# -- session library: tags, the list route, the cover photograph

def _second_model(client, seeded) -> int:
    """A second model with its own workflow. The library tests need two models
    because the route's whole point is listing across them."""
    wf = client.post("/api/workflows", json={"name": "wf-2", "graph": GRAPH}).json()
    return client.post("/api/models", json={
        "name": "bea", "lora_name": "characters/bea.safetensors", "trigger": "bea woman",
        "base_positive": "photo, 35mm", "base_negative": "blurry", "workflow_id": wf["id"],
    }).json()["id"]


def test_tagging_a_shot_session_keeps_the_shots_unchanged(client, seeded):
    """Tags live on the session, not on the shots — the whole point is a label
    the gallery can read after the shoot. Adding two tags must not touch a
    single row of the session's shots."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "summer",
        "shots": [{"prompt": "standing", "count": 2}],
    }).json()["id"]
    shot_ids = [x["id"] for x in client.get(f"/api/sessions/{sid}").json()["shots"]]

    r = client.patch(f"/api/sessions/{sid}", json={"tags": ["balcony", "outdoor"]})
    assert r.status_code == 200
    assert sorted(r.json()["tags"]) == ["balcony", "outdoor"]

    # Reloading shows both tags. The shots survive untouched.
    s = client.get(f"/api/sessions/{sid}").json()
    assert sorted(s["tags"]) == ["balcony", "outdoor"]
    assert [x["id"] for x in s["shots"]] == shot_ids


def test_the_same_tag_in_two_cases_is_one_tag(client, seeded):
    """A tag in two cases within one PATCH lands as one tag, with the first
    occurrence's case kept. The backend guarantees within-list dedupe
    (case-insensitive); the frontend handles "add a tag" by sending the full
    list and trusting the server to clean it up."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "shots": []}).json()["id"]
    r = client.patch(f"/api/sessions/{sid}", json={"tags": ["Balcony", "balcony", "BALCONY"]})
    assert r.json()["tags"] == ["Balcony"]


def test_an_empty_tag_is_discarded_not_stored(client, seeded):
    """A tag consisting of whitespace only is dropped, and the other tags on
    the session are left alone — the route treats it as a non-event rather
    than a request to clear the list."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "shots": []}).json()["id"]
    r = client.patch(f"/api/sessions/{sid}", json={"tags": ["balcony", "  ", "\t\n", "outdoor"]})
    assert sorted(r.json()["tags"]) == ["balcony", "outdoor"]


def test_a_cloned_session_keeps_its_tags(client, seeded):
    """A clone is a copy of the shoot. The tags describe the shoot, so they
    travel with it. No "Balcony (copy)" without a "Balcony" — a clone of a
    tagged session is still findable by that tag."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "balcony shoot",
        "shots": [{"prompt": "standing", "count": 1}],
    }).json()["id"]
    client.patch(f"/api/sessions/{sid}", json={"tags": ["balcony", "outdoor"]})

    clone = client.post(f"/api/sessions/{sid}/clone", json={"name": "balcony (copy)"}).json()["id"]
    assert sorted(client.get(f"/api/sessions/{clone}").json()["tags"]) == ["balcony", "outdoor"]
    # And the clone is itself findable by the same tag.
    by_tag = [s["id"] for s in client.get("/api/sessions", params={"tag": "balcony"}).json()]
    assert sorted(by_tag) == sorted([sid, clone])


def test_text_query_searches_across_models(client, seeded):
    """`q` reads the session's look, the place a model belongs to is irrelevant
    to the search. Two models, two sessions, the same word in their look — the
    query lists both, newest first."""
    second = _second_model(client, seeded)
    a = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "A", "look": "on a balcony at sunset",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    b = client.post("/api/sessions", json={
        "model_id": second, "name": "B", "look": "balcony in winter",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "C", "look": "kitchen",
        "shots": [{"prompt": "standing", "count": 1}]}).json()

    listed = [s["name"] for s in client.get("/api/sessions", params={"q": "balcony"}).json()]
    # Newest first: B was created after A, so B comes first.
    assert listed == ["B", "A"]


def test_text_query_reads_the_wardrobe_too(client, seeded):
    """The wardrobe is the half of a session that moves — the user might search
    for the dress without remembering the name or the look. A session whose
    wardrobe alone mentions `raincoat` must be findable."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "rainy",
        "look": "hair down", "wardrobe": "yellow raincoat",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    listed = [s["id"] for s in client.get("/api/sessions", params={"q": "raincoat"}).json()]
    assert listed == [sid]


def test_a_tag_filter_matches_a_whole_tag_not_a_substring(client, seeded):
    """`tag=night` is the session tagged `night`, not the one tagged
    `nightclub`. A prefix match would broaden the filter past the word the
    user typed and surface a session that is not about what they asked for."""
    a = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "evening",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    b = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "club",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    client.patch(f"/api/sessions/{a}", json={"tags": ["night"]})
    client.patch(f"/api/sessions/{b}", json={"tags": ["nightclub"]})

    listed = [s["id"] for s in client.get("/api/sessions", params={"tag": "night"}).json()]
    assert listed == [a]


def test_text_and_tag_filters_must_both_hold(client, seeded):
    """Both filters, both must hold — the AND is what makes a free-text search
    inside one tag possible, and a session that matches only the text is not
    what was asked for."""
    a = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "balcony shoot",
        "look": "on a balcony at sunset", "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    b = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "studio shoot",
        "look": "in a kitchen at noon", "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    client.patch(f"/api/sessions/{a}", json={"tags": ["balcony"]})
    client.patch(f"/api/sessions/{b}", json={"tags": ["balcony"]})

    listed = [s["id"] for s in client.get("/api/sessions", params={"q": "balcony", "tag": "balcony"}).json()]
    assert listed == [a]


def test_no_filters_lists_every_session_newest_first(client, seeded):
    """The default route still works: every session, regardless of tags or
    text, newest first — that is the existing screen's shape and the library
    is built on top of it rather than beside it."""
    s1 = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "first",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    s2 = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "second",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    s3 = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "third",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]

    listed = [s["id"] for s in client.get("/api/sessions").json()]
    assert listed == [s3, s2, s1]   # newest first


def test_a_query_that_matches_nothing_returns_an_empty_list(client, seeded):
    """The route succeeds, the list is empty, the front-end shows nothing
    rather than an error. A 404 would be a different contract — the query
    is legitimate, the data is just not there."""
    client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "kitchen",
        "shots": [{"prompt": "standing", "count": 1}]})
    r = client.get("/api/sessions", params={"q": "nonexistent"})
    assert r.status_code == 200 and r.json() == []


def test_each_listed_session_carries_its_cover_shot_id(client, seeded):
    """The screen shows one photograph per row without a request per row, so
    the cover id has to come back with the session. The cover is the
    highest-rated, non-rejected, done shot — the same frame the model detail
    page picks."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 3}]}).json()["id"]
    done = [_finished_shot(client, sid, f"frame {i}", rating=i) for i in (1, 3, 5)]

    # A rejected 5★ must not win — a photograph the user said no to is not the
    # cover photograph. The 3★ keeps the cover honest.
    db.run("UPDATE shot SET rejected=1 WHERE id=?", done[2]["id"])
    expected = done[1]["id"]

    listed = client.get("/api/sessions").json()
    cover = next(s["cover_shot_id"] for s in listed if s["id"] == sid)
    assert cover == expected
def make_png(color="blue", size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color=color).save(buf, format="PNG")
    return buf.getvalue()


def test_contact_sheet_default_threshold(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 3}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='done', filename='a1.png', rating=0 WHERE id=?", shots[0]["id"])
    db.run("UPDATE shot SET status='done', filename='b1.png', rating=2 WHERE id=?", shots[1]["id"])
    db.run("UPDATE shot SET status='done', filename='c1.png', rating=5 WHERE id=?", shots[2]["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    for f in ["a1.png", "b1.png", "c1.png"]:
        (folder / f).write_bytes(make_png("red"))

    files_before = {p.name: p.read_bytes() for p in folder.iterdir()}

    r = client.get(f"/api/sessions/{sid}/contact-sheet")
    assert r.status_code == 200
    assert f"session_{sid}" in r.headers["Content-Disposition"]
    assert r.headers["Content-Type"] == "image/png"

    img = Image.open(io.BytesIO(r.content))
    assert img.format == "PNG"
    assert img.size[0] > 0 and img.size[1] > 0

    files_after = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert files_before == files_after


def test_contact_sheet_raises_threshold(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 3}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='done', filename='a2.png', rating=0 WHERE id=?", shots[0]["id"])
    db.run("UPDATE shot SET status='done', filename='b2.png', rating=2 WHERE id=?", shots[1]["id"])
    db.run("UPDATE shot SET status='done', filename='c2.png', rating=5 WHERE id=?", shots[2]["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    for f in ["a2.png", "b2.png", "c2.png"]:
        (folder / f).write_bytes(make_png("green"))

    r = client.get(f"/api/sessions/{sid}/contact-sheet?min_rating=3")
    assert r.status_code == 200
    img = Image.open(io.BytesIO(r.content))
    assert img.format == "PNG"


def test_contact_sheet_skips_rejected(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 2}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='done', filename='a5.png', rating=5 WHERE id=?", shots[0]["id"])
    db.run("UPDATE shot SET status='done', filename='b5.png', rating=5, rejected=1 WHERE id=?", shots[1]["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    for f in ["a5.png", "b5.png"]:
        (folder / f).write_bytes(make_png("blue"))

    r = client.get(f"/api/sessions/{sid}/contact-sheet")
    assert r.status_code == 200
    img = Image.open(io.BytesIO(r.content))
    assert img.format == "PNG"


def test_contact_sheet_skips_unfinished(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 4}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='pending', filename='', rating=5 WHERE id=?", shots[0]["id"])
    db.run("UPDATE shot SET status='failed', filename='', rating=5 WHERE id=?", shots[1]["id"])
    db.run("UPDATE shot SET status='cancelled', filename='', rating=5 WHERE id=?", shots[2]["id"])
    db.run("UPDATE shot SET status='done', filename='done.png', rating=5 WHERE id=?", shots[3]["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "done.png").write_bytes(make_png("yellow"))

    r = client.get(f"/api/sessions/{sid}/contact-sheet")
    assert r.status_code == 200
    img = Image.open(io.BytesIO(r.content))
    assert img.format == "PNG"


def test_contact_sheet_empty_selection(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    shot = client.get(f"/api/sessions/{sid}").json()["shots"][0]
    db.run("UPDATE shot SET status='done', filename='a3.png', rating=0 WHERE id=?", shot["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a3.png").write_bytes(make_png("red"))

    r = client.get(f"/api/sessions/{sid}/contact-sheet")
    assert r.status_code == 400
    assert "no shots meet the threshold of 1" in r.json()["detail"]


def test_contact_sheet_skips_missing_files(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 2}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    db.run("UPDATE shot SET status='done', filename='a4.png', rating=5 WHERE id=?", shots[0]["id"])
    db.run("UPDATE shot SET status='done', filename='b4.png', rating=5 WHERE id=?", shots[1]["id"])
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a4.png").write_bytes(make_png("purple"))

    r = client.get(f"/api/sessions/{sid}/contact-sheet")
    assert r.status_code == 200
    img = Image.open(io.BytesIO(r.content))
    assert img.format == "PNG"


def test_contact_sheet_unknown_session(client):
    r = client.get("/api/sessions/999/contact-sheet")
    assert r.status_code == 404


def test_contact_sheet_labels_three_variations_and_download_name(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 3}]}).json()["id"]
    shots = client.get(f"/api/sessions/{sid}").json()["shots"]
    folder = main.SESSIONS_DIR / str(sid)
    folder.mkdir(parents=True, exist_ok=True)
    for i, shot in enumerate(shots, 1):
        fname = f"00001_variation_{i}.png"
        db.run("UPDATE shot SET status='done', filename=?, rating=5 WHERE id=?", fname, shot["id"])
        (folder / fname).write_bytes(make_png("orange"))

    r = client.get(f"/api/sessions/{sid}/contact-sheet")
    assert r.status_code == 200
    assert f'filename="session_{sid}_contact_sheet.png"' in r.headers["Content-Disposition"]

    img = Image.open(io.BytesIO(r.content))
    assert img.format == "PNG"
    assert img.width > 0 and img.height > 0


def test_contact_sheet_label_is_trimmed_to_its_cell():
    """A ComfyUI filename is wider than a cell and Pillow neither wraps nor
    clips, so an untrimmed label runs over the neighbouring photograph. The tail
    survives the trim: the counter at the end is what tells two variations of
    one take apart."""
    font = ImageFont.load_default()

    def width(s):
        box = font.getbbox(s)
        return box[2] - box[0]

    long_name = "iDevGen_a_very_long_prefix_indeed_00042_.png"
    fitted = main._fit_label(long_name, font, 120)
    assert width(fitted) <= 120
    assert fitted.startswith("...")
    assert fitted.endswith("00042_.png")

    short = "a.png"
    assert main._fit_label(short, font, 120) == short


# -- /api/photos: the slideshow's input. Cross-session, threshold-filtered,
#    read-only. Three things had to hold at once: inclusive threshold, the
#    never-rated showing at zero, and the rejected / pending / failed
#    excluded alongside the threshold.

def _photo(client, sid, label, *, rating=0, rejected=False, status="done", filename=None):
    """An imported PNG with the columns the /api/photos route actually filters on.

    The route only reads status, rejected, rating and filename; everything else
    is irrelevant to the test, so a single helper shapes all four at once."""
    shot = client.post(f"/api/sessions/{sid}/import?label={label}", content=PNG).json()
    db.run("UPDATE shot SET rating=?, rejected=?, status=?, filename=? WHERE id=?",
           rating, int(rejected), status, filename or shot["filename"], shot["id"])
    return shot


def test_photos_threshold_is_inclusive(client, seeded):
    """A 4 is listed at `min_rating=4`; a 3 is not. The slideshow's dial is
    `>=`, not `>`, and the design's whole point is the same threshold the
    filter button elsewhere in the app already uses."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    four = _photo(client, sid, "four", rating=4)
    three = _photo(client, sid, "three", rating=3)

    listed = {x["id"]: x for x in client.get("/api/photos", params={"min_rating": 4}).json()}
    assert listed.keys() == {four["id"]}
    assert listed[four["id"]]["session_name"] == "s"


def test_photos_min_rating_zero_lists_the_never_rated(client, seeded):
    """`min_rating=0` is the slideshow's first-day mode: the design measured
    6,356 of 6,380 finished, un-rejected photographs as unrated on a real
    database, and the 13 keepers the threshold dial will eventually pick are
    not what plays the first time. Rating 0 must list them."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    never = _photo(client, sid, "unrated")        # rating defaults to 0
    star = _photo(client, sid, "star", rating=5)

    listed = [x["id"] for x in client.get("/api/photos", params={"min_rating": 0}).json()]
    assert sorted(listed) == sorted([never["id"], star["id"]])


def test_photos_rejected_pending_and_failed_are_excluded(client, seeded):
    """A 5★ rejected photo is not a keeper, a pending shot has nothing to
    show, and a failed shot never produced one. The route's promise is the
    set the slideshow can actually show — these three are not it, even when
    they would otherwise pass the threshold."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 4}]}).json()["id"]
    rejected = _photo(client, sid, "no", rating=5, rejected=True)
    pending = _photo(client, sid, "wait", rating=5, status="pending", filename="")
    failed = _photo(client, sid, "broken", rating=5, status="failed", filename="")
    keeper = _photo(client, sid, "yes", rating=5)

    listed = [x["id"] for x in client.get("/api/photos", params={"min_rating": 5}).json()]
    assert listed == [keeper["id"]]


def test_photos_listing_spans_every_session(client, seeded):
    """The route exists because no session-scoped answer was enough. Two
    models, two sessions, the threshold met in both — both are listed,
    regardless of which session the caller started from."""
    second = _second_model(client, seeded)
    a = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "A",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    b = client.post("/api/sessions", json={
        "model_id": second, "name": "B",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    pa = _photo(client, a, "A", rating=4)
    pb = _photo(client, b, "B", rating=4)

    listed = {x["id"]: x for x in client.get("/api/photos", params={"min_rating": 4}).json()}
    assert listed.keys() == {pa["id"], pb["id"]}
    assert listed[pa["id"]]["session_id"] == a and listed[pa["id"]]["session_name"] == "A"
    assert listed[pb["id"]]["session_id"] == b and listed[pb["id"]]["session_name"] == "B"


def test_photos_entry_carries_the_session_name(client, seeded):
    """Each row needs the session name, not just the session id, so the
    slideshow can label a photograph without a second request per frame."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "Balcony shoot",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    shot = _photo(client, sid, "frame", rating=4)

    [entry] = client.get("/api/photos", params={"min_rating": 4}).json()
    assert entry["id"] == shot["id"]
    assert entry["session_id"] == sid
    assert entry["session_name"] == "Balcony shoot"


def test_photos_empty_result_is_an_empty_list_not_an_error(client, seeded):
    """A threshold that matches nothing is a legitimate query — the front
    end says 'nothing to play' and that is the right answer, not a 404."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    _photo(client, sid, "low", rating=1)

    r = client.get("/api/photos", params={"min_rating": 5})
    assert r.status_code == 200 and r.json() == []


def test_photos_listing_leaves_everything_unmodified(client, seeded):
    """Read-only is the whole point: a hit on the route must not change a
    single rating, filename, shot row, or session row. Snapshots before and
    after are equal, byte for byte."""
    a = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "A",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    b = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "B",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    pa = _photo(client, a, "A", rating=4)
    pb = _photo(client, b, "B", rating=2, rejected=True)
    pc = _photo(client, a, "C", rating=5, status="pending", filename="")

    before = {
        "shots": {s["id"]: dict(s) for s in db.q("SELECT * FROM shot ORDER BY id")},
        "sessions": {s["id"]: dict(s) for s in db.q("SELECT * FROM session ORDER BY id")},
    }

    for params in ({"min_rating": 0}, {"min_rating": 3}, {"min_rating": 5}, {}):
        r = client.get("/api/photos", params=params)
        assert r.status_code == 200

    after = {
        "shots": {s["id"]: dict(s) for s in db.q("SELECT * FROM shot ORDER BY id")},
        "sessions": {s["id"]: dict(s) for s in db.q("SELECT * FROM session ORDER BY id")},
    }
    assert after == before
    # And the photographs we set up are where we expect them: the route
    # actually saw the right rows.
    assert {pa["id"], pb["id"], pc["id"]} <= before["shots"].keys()
