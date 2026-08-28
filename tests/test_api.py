"""HTTP routes: creating models and sessions, prompt composition, rating."""
import io
import json
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

    The session declares manner and checkpoint, and the cell for
    the trio is pre-seeded as verified, because 3.2 makes strict
    mode the default and a missing manner or unverified cell would
    refuse the compose with 422. The cell here is the same trio the
    test composes against, so strict passes and the assertion is
    about the join, not the strict check.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "compose equals write",
        "look": "white summer dress, hair down, on a beach",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    # Pre-seed a verified cell for the trio so strict mode (the
    # default since 3.2) accepts the compose. 10/8 is the spec
    # admission boundary (cell_state returns "verified" for
    # arrived * 10 >= judged * 8, i.e. 8*10 >= 10*8).
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 10, 8)

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

    # Compose and queue. `count` defaults to 1, so the response is
    # `{"ids": [shot_id], "count": 1}` — the same shape the run-level
    # endpoint (`compose-run`) and 8.5's fill-cell call return when
    # asked for N.
    composed_id = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    }).json()["ids"][0]

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

    The session declares manner and checkpoint, and the cell for
    the trio is pre-seeded as verified, for the same reason as the
    join test above: strict mode (3.2) is the default and a missing
    cell would refuse the compose before this test's assertion
    could run.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "components on row",
        "look": "white summer dress, hair down, on a beach",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 10, 8)

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride",
                         "text": "She is astride him with her knees on either side of his hips and her weight down on him, the two of them joined, two people in frame."}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "a full-length photograph, head to feet"}]}

    composed_id = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    }).json()["ids"][0]

    row = db.one("SELECT * FROM shot WHERE id=?", composed_id)
    db.jload(row, "components")
    assert row["components"] == {
        "camera":  {"concept": "front-direct", "wording": "front-direct"},
        "act":     {"concept": "astride",      "wording": "astride"},
        "framing": {"concept": "full-length",  "wording": "full-length"},
    }


# ----------------------------------------------------------------- 3.2 strict
#
# The trio's cell is the unit a photograph counts toward (design.md
# decision C, spec/component-matrix). Strict mode refuses a compose
# when the cell is not verified for the session's manner and
# checkpoint. The negative case the task names is "a component
# verified on another checkpoint is not drawn": a cell verified for
# finepornV4 does not entitle a session on the Krea 2 mix to draw
# the same trio, because the cell is the trio plus the session's two
# non-trio dimensions and the lookup is exact.
#
# The endpoint has no `mode` field on the payload. Strict is the
# only legal mode today; encoding it as a string would let a wrong
# value bypass the check (an if over a free string is a door open by
# default), and there is no second mode to switch to. The test below
# pins the bypass attempt shut: a request that tries to set
# `mode=anything` is parsed by pydantic with the default
# extra="ignore", the field is silently dropped, the strict check
# runs unconditionally, and the compose is refused. This is the
# shape "validate at the type, not at the branch" — the check is
# structural, not string-equal, and a wrong value cannot turn it off
# by being misspelled.

def test_a_component_verified_on_another_checkpoint_is_not_drawn_in_strict_mode(client, seeded):
    """A trio verified on finepornV4 is not drawable in a session on
    the Krea 2 mix, even with the same three components. The cell
    table is keyed on (camera_wording, act_wording, framing_wording,
    manner, checkpoint); the lookup is exact, and a verified cell
    for (front-direct, astride, full-length, directed, finepornV4)
    does not satisfy (front-direct, astride, full-length, directed,
    Krea 2 mix).

    The 422 message names the trio, the session's manner and
    checkpoint, and the state the lookup found, so the caller can
    see the gap is a missing measurement on the Krea 2 mix rather
    than something else.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "wrong checkpoint",
        "manner": "directed", "checkpoint": "Krea 2 mix",
        "shots": [],
    }).json()["id"]

    # The trio is verified on finepornV4, NOT on the Krea 2 mix the
    # session is shot on. A future "let me cache the cell lookup at
    # the trio level and only check the non-trio dimensions" would
    # silently pass this test on the wrong behaviour, which is the
    # exact regression the cell table is shaped to prevent.
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 10, 8)

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "framing text"}]}

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    })
    assert r.status_code == 422, r.text
    assert "no measurement" in r.json()["detail"] or "unknown" in r.json()["detail"]
    # Nothing queued: the refused compose is a refused compose, not a
    # queued one with a 422. The shot table is the proof.
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0


def test_a_dead_cell_is_not_drawn_in_strict_mode(client, seeded):
    """A cell that is dead (n>=10 with a failed ratio) is not
    drawable in strict mode. Dead is a verdict: the cell is the
    truth of the measurement, and the trio's photograph came back
    as something other than what the wording described. Reusing the
    same trio is a different photograph, not the same one.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "dead cell",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    # 12 judged, 0 arrived. cell_state returns "dead" (n>=10 and
    # arrived*10 < judged*8, i.e. 0 < 96). Same trio, same manner and
    # checkpoint as the session.
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 12, 0)

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "framing text"}]}

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    })
    assert r.status_code == 422, r.text
    assert "dead" in r.json()["detail"]
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0


def test_an_unknown_cell_is_not_drawn_in_strict_mode(client, seeded):
    """A cell with n<10 is unknown, not verified, and the strict
    check refuses the draw the same way it refuses a dead cell. The
    cell table is the only home for "is this trio drawable", and
    the unknown state is the explicit answer for "we have not
    measured enough to know".
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "unknown cell",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    # 3 judged, 3 arrived. n<10, so cell_state returns "unknown"
    # even at a perfect ratio. The trio is the one the 3.1 join
    # test uses, but the cell is under-measured.
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 3, 3)

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "framing text"}]}

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    })
    assert r.status_code == 422, r.text
    assert "unknown" in r.json()["detail"]
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0


def test_a_strict_compose_requires_manner_and_checkpoint_on_the_session(client, seeded):
    """A session that does not declare manner or checkpoint cannot
    be composed in strict mode. The cell table is keyed on the
    five-tuple and the lookup needs both non-trio dimensions to
    resolve. A session with empty manner or empty checkpoint would
    silently find zero cells and read as "not verified" — the
    422 before the lookup names what the session is missing.

    The seeded workflow names its checkpoint in its loader, so the
    backend would derive session.checkpoint from it. To exercise the
    truly-missing path, the session is pointed at a bare workflow
    whose graph has no CheckpointLoaderSimple / UNETLoader: that is
    the shape the derivation sees when the source-of-truth is empty
    on both sides (no override, no loader).
    """
    # A graph with one CLIPTextEncode and no loader: passes
    # _require_api_graph (one class_type) and graph_checkpoint returns
    # '' (no ckpt_name, no unet_name).
    bare_wf = client.post("/api/workflows", json={
        "name": "bare",
        "graph": {"1": {"class_type": "CLIPTextEncode",
                        "inputs": {"text": "x", "clip": ["2", 1]}}},
    }).json()
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "no dimensions",
        "workflow_id": bare_wf["id"],
        # No manner, no checkpoint, no settings.checkpoint, and a
        # workflow with no loader — every source is empty.
        "shots": [],
    }).json()["id"]

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "X"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride", "text": "Y"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "Z"}]}

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    })
    assert r.status_code == 422, r.text
    assert "manner" in r.json()["detail"]
    assert "checkpoint" in r.json()["detail"]
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0


def test_a_session_created_via_the_apps_path_can_be_composed_in_strict_mode(client, seeded):
    """The app's session creation POST has no top-level manner or
    checkpoint. The editor's manner is lifted into the body (so the
    POST carries the value the <select> already shows), and the
    checkpoint is derived server-side from settings.checkpoint or
    the workflow's own loader — the system already names the model
    (comfy.py:35-38, ckpt_name/unet_name; the graph_checkpoint
    function reads it back at comfy.py:252-273), and the operator
    is not asked to type it twice.

    A strict compose against that session must succeed: the cell
    table is keyed on the session's effective dimensions, and the
    dimensions the cell was verified for must be the ones the
    session carries, not the ones the body happened to include.

    The five 3.2 tests above declare manner and checkpoint in the
    POST body, so they pass over a function no real session can
    reach. This one uses the body the app actually sends, and
    would have caught the gap 3.2 closed with: every session
    created in the app was born with both dimensions empty, and
    strict mode refused the compose unconditionally.
    """
    # The app's body: model_id, name, look, wardrobe, settings,
    # shots. No top-level manner or checkpoint. settings.checkpoint
    # is not set, so the backend must derive from the workflow's
    # loader (the fixture's GRAPH has ckpt_name "base.safetensors"
    # at conftest.py:32).
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"],
        "name": "app path",
        "look": "white summer dress, hair down, on a beach",
        "manner": "directed",   # lifted by ShotsEditor.jsx:67,259
        "shots": [],
    }).json()["id"]

    s = client.get(f"/api/sessions/{sid}").json()
    # Manner lifted by the editor (its default is "directed");
    # checkpoint derived from the workflow's loader, exactly the
    # value graph_checkpoint returned at create time.
    assert s["manner"] == "directed"
    assert s["checkpoint"] == "base.safetensors"

    # Pre-seed a cell for the trio, on the session's effective
    # dimensions. 10/8 is the verified boundary (cell_state in
    # db.py returns "verified" for arrived*10 >= judged*8).
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "base.safetensors", 10, 8)

    # Compose. The session carries the dimensions the cell was
    # seeded for, so strict mode accepts the draw and queues the
    # shot — the path the app reaches.
    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": {"key": "front-direct",
                   "wordings": [{"key": "front-direct",
                                 "text": "Taken from directly in front of her"}]},
        "act": {"key": "astride",
                "wordings": [{"key": "astride", "text": "astride text"}]},
        "framing": {"key": "full-length",
                    "wordings": [{"key": "full-length", "text": "framing text"}]},
    })
    assert r.status_code == 200, r.text
    # 8.5 changed the response shape to {ids, count} so the fill-cell
    # call (count=N) and the single-shot call (count=1) return the
    # same shape. With count=1 the list has one element.
    body = r.json()
    assert "ids" in body and body["count"] == 1
    assert len(body["ids"]) == 1
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 1


def test_a_verified_cell_for_the_sessions_dimensions_is_drawn_in_strict_mode(client, seeded):
    """The positive case the strict check is supposed to allow: a
    trio whose cell is verified for the session's exact manner and
    checkpoint is drawable, and the compose queues the shot. The
    assertion is the same as the 3.1 join test but with the cell
    keyed for the session's dimensions rather than another
    checkpoint's, so the strict check is what passes.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "happy path",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 10, 8)

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "framing text"}]}

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    })
    assert r.status_code == 200, r.text
    # 8.5 changed the response shape to {ids, count}; count=1 here.
    body = r.json()
    assert body["count"] == 1 and len(body["ids"]) == 1
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 1


def test_a_request_with_an_unknown_mode_value_is_rejected_at_the_boundary(client, seeded):
    """`mode` on `ComposeIn` is a `Literal["strict", "exploratory"]`,
    not a free string, and pydantic rejects an unknown value
    BEFORE the handler runs. A request that tries to set
    `mode=anything` is a 422 from pydantic — `loc=body.mode`,
    `type=literal_error`, the message names the two legal values.
    The handler's strict check never fires on this path because
    the request never reaches it; that is the whole point of
    the Literal type. A future regression that re-introduces
    `mode: str = "strict"` and guards with
    `if c.mode == "strict":` lets the bypass through: the
    unknown value would be parsed as the string `"anything"`,
    the guard would not match, the strict check would be
    skipped, and the cell lookup would either find nothing
    (a 422 from the no-row branch) or queue a shot. The
    loop-closed assertion (`n_shots == 0`) is the same in
    both shapes; the test that distinguishes them is the
    status code and the response body (pydantic's literal
    error vs the handler's compose-refused message). 6.1
    opened the second mode and closed the door the loose
    type would have left open.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "bypass attempt",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "framing text"}]}

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
        "mode": "anything",
    })
    # Pydantic rejects the unknown value at the boundary; the
    # handler does not run, the strict check is not bypassed.
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # The literal error names the field and the two legal
    # values. Asserted separately so a future "let me
    # generalize the message" that drops one of the literals
    # fails the assertion that names it.
    assert isinstance(detail, list) and any(
        err.get("type") == "literal_error" and err.get("loc") == ["body", "mode"]
        for err in detail
    ), f"expected a pydantic literal_error on body.mode, got: {detail!r}"
    assert any("strict" in (err.get("msg") or "") and "exploratory" in (err.get("msg") or "")
               for err in detail), (
        f"the literal error should name both legal modes, got: {detail!r}"
    )
    # Loop-closed: a request that was rejected at the boundary
    # did not insert anything.
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, (
        f"rejected request queued shots: shot table has {n} rows for session {sid}"
    )


# -------------------------------------------------------------- 3.2 PATCH keep
#
# The cell table is keyed on (manner, checkpoint) and the strict check
# looks up the cell against the session's row. A PATCH that changes
# the source of truth — workflow_id (a new loader) or settings.checkpoint
# (a new override) — must re-derive session.checkpoint, or the cell key
# goes stale and the strict check approves draws against a checkpoint
# the session no longer runs on. The probe + the loop-closed tests
# together pin both halves: the row follows the source, and the strict
# check actually refuses on the wrong side of the swap.


def test_a_workflow_swap_re_derives_session_checkpoint(client, seeded):
    """A PATCH that swaps the session's workflow re-derives
    `session.checkpoint` from the new graph's loader. Without the
    re-derivation, the cell key stays on the old checkpoint and the
    strict check approves draws against a checkpoint the session no
    longer runs on.

    The probe: create with the seeded workflow (loader says
    "base.safetensors"), verify the row's checkpoint, swap to a
    second workflow whose loader says "OTHER.safetensors", verify
    the row followed.
    """
    # The seeded fixture's GRAPH already has ckpt_name "base.safetensors"
    # (conftest.py:32). The new workflow below is the swap target; its
    # loader names a different model.
    other_wf = client.post("/api/workflows", json={
        "name": "other",
        "graph": {"1": {"class_type": "CheckpointLoaderSimple",
                        "inputs": {"ckpt_name": "OTHER.safetensors"}}},
    }).json()

    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "swap",
        "manner": "directed",
        # No settings.checkpoint, no top-level checkpoint — the
        # derivation is what the row reads on create.
        "shots": [],
    }).json()["id"]

    s = client.get(f"/api/sessions/{sid}").json()
    assert s["checkpoint"] == "base.safetensors"

    # Swap. The cell key must follow.
    client.patch(f"/api/sessions/{sid}", json={"workflow_id": other_wf["id"]})
    s = client.get(f"/api/sessions/{sid}").json()
    assert s["workflow_id"] == other_wf["id"]
    assert s["checkpoint"] == "OTHER.safetensors"


def test_a_settings_checkpoint_override_re_derives_session_checkpoint(client, seeded):
    """A PATCH that changes `settings.checkpoint` (the BaseModelSelect
    on the session panel sends exactly this shape) re-derives
    `session.checkpoint` to the new override. The override is the
    next rung above the workflow's loader in the resolution order,
    so a value sent in settings wins over whatever the workflow
    names.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "override",
        "manner": "directed",
        "shots": [],
    }).json()["id"]

    s = client.get(f"/api/sessions/{sid}").json()
    assert s["checkpoint"] == "base.safetensors"

    # Pick a different base model on the session panel. The PATCH
    # carries the override in settings.checkpoint and the cell key
    # must follow.
    client.patch(f"/api/sessions/{sid}", json={
        "settings": {"checkpoint": "krea2-mix.safetensors"},
    })
    s = client.get(f"/api/sessions/{sid}").json()
    assert s["checkpoint"] == "krea2-mix.safetensors"


def test_after_a_workflow_swap_a_cell_verified_on_the_old_checkpoint_is_refused(client, seeded):
    """The loop-closed test: the re-derivation is not just a row
    update, it is what makes the strict check refuse on the wrong
    side of a swap. A cell verified for (trio, directed,
    "base.safetensors") does not entitle a session now running on
    "OTHER.safetensors" to draw the same trio — that is the bypass
    3.2 exists to prevent, and a PATCH that left the cell key on
    the old checkpoint would defeat it.

    Steps: create on workflow 1, seed a verified cell for the trio
    on the seed checkpoint, swap the workflow, compose. The cell
    lookup is for the new checkpoint, finds nothing, and the strict
    check refuses with 422. A regression that drops the re-derivation
    flips this to 200: the row still says "base.safetensors", the
    cell matches, the compose is approved.
    """
    other_wf = client.post("/api/workflows", json={
        "name": "other",
        "graph": {"1": {"class_type": "CheckpointLoaderSimple",
                        "inputs": {"ckpt_name": "OTHER.safetensors"}}},
    }).json()

    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "stale cell",
        "manner": "directed",
        "shots": [],
    }).json()["id"]
    assert client.get(f"/api/sessions/{sid}").json()["checkpoint"] == "base.safetensors"

    # A cell verified for the seed checkpoint. After the swap, the
    # session is on OTHER.safetensors; this cell must NOT satisfy
    # the new lookup.
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "base.safetensors", 10, 8)

    client.patch(f"/api/sessions/{sid}", json={"workflow_id": other_wf["id"]})
    assert client.get(f"/api/sessions/{sid}").json()["checkpoint"] == "OTHER.safetensors"

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct",
                            "text": "Taken from directly in front of her"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "framing text"}]}

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    })
    # The cell is on the OLD checkpoint, the session is on the NEW
    # one. The strict check refuses: the trio is not verified for
    # the session's effective dimensions, and that is exactly the
    # gate 3.2 exists to keep closed across a PATCH.
    assert r.status_code == 422, r.text
    assert "no measurement" in r.json()["detail"] or "OTHER.safetensors" in r.json()["detail"]
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0


# -------------------------------------------------------------- 3.3 run level
#
# 3.1 and 3.2 are one-shot: the caller passes the three components and the
# backend queues a single shot. 3.3 is the run-level: the caller asks for N
# photographs and the backend draws. The endpoint is a sibling of the
# one-shot route (`POST /api/sessions/{sid}/compose` is unchanged; the
# run-level lives at `POST /api/sessions/{sid}/compose-run` with a
# `{"count": N, "candidates": {...}}` payload — the design decision is
# written in tasks.md and is what the test below pins).
#
# The rule 3.3 enforces: in strict mode, a run of N photographs is either
# fully filled (the verified-trio pool is large enough) or fully refused
# (it is not). A refusal queues nothing. The pre-check is what stops a
# "shorter run, delivered" — `db.run` commits per INSERT, so a loop that
# queues k and refuses at k+1 would leave k rows. The check runs up front,
# and the loop-closed test is `n_shots == 0` after a 422, not just the
# status code.
#
# The pool is the set of verified `(camera, act, framing)` trios, NOT a
# DISTINCT count per slot. A component verified alone can still fail in
# combination (design.md:326-329), and counting DISTINCT per slot reads as
# N×M×K trios when only some of them are cells in the table. The schema
# went to five columns for this reason; the picker draws from the set of
# rows that pass the verified predicate, not from per-slot lists zipped
# together. The user's probe (3 trios with no shared components, asked
# for 3) is the case that distinguishes the two readings, and the success
# test below pins it: every queued trio has to be a row in the cell
# table.
#
# The count is on the session's manner and checkpoint, the same way 3.2
# scopes the cell lookup. A trio verified on another checkpoint does not
# add to the pool: the cell is the five-tuple, and a session on a
# different checkpoint looks at a different cell.


def _seed_verified_trio(camera: str, act: str, framing: str,
                        *, manner: str, checkpoint: str,
                        judged: int = 10, arrived: int = 8) -> None:
    """Insert one verified cell with the given (camera, act, framing)
    and (manner, checkpoint). 3.3 seeds specific trios, not a
    cartesian product — a per-slot cartesian product was the broken
    arithmetic the original 3.3 had, and the tests have to use the
    trio shape or they would still pass on it.
    """
    db.run(
        "INSERT INTO cell (camera_wording, act_wording, framing_wording, "
        "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
        camera, act, framing, manner, checkpoint, judged, arrived,
    )


def _candidate(key: str, text: str) -> dict:
    """One catalogue entry in the shape `ComposeIn` takes. Every test
    candidate has a single wording whose key equals the concept key, the
    same shape 3.1's tests use — and the same shape the cell table's
    `camera_wording` / `act_wording` / `framing_wording` columns hold.
    """
    return {"key": key, "wordings": [{"key": key, "text": text}]}


def test_a_strict_run_with_a_too_small_trio_pool_is_refused_with_the_slot_count_and_exploratory(client, seeded):
    """The case the user pinned: 3 verified trios (each with its own
    camera, act, framing — no shared components, the shape that
    catches a per-slot DISTINCT pool that inflates the count); the
    operator asks for 5 photographs; the trio pool is 3 and the run
    is refused. The 422 message names the four literals the user
    listed — the slot, its verified count, the largest fillable
    count, and the word "exploratory" — so the operator can take
    the number (3) or switch to exploratory, rather than bisecting
    by hand.

    The pool is seeded explicitly. The shipped EVIDENCE_SEED has
    no verified trio (every cell is n<10 or dead under the ratio
    reading), so a "rejects" test against the seed would pass on
    completely broken arithmetic. The 3-and-5 numbers are the ones
    the user named; the test asserts the message carries both 3
    (the verified count for the named slot AND the largest
    fillable) and 5 (the requested count, named in the message so
    the operator sees what was asked).

    The four literals are asserted separately, not with a single
    `in` over the whole sentence: each one pins a different fact
    the message has to carry, and a future "let me shorten the
    message" that drops one is caught by the assert that names it.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "too small trio pool",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    # 3 verified trios, no shared components — the shape the
    # user named, the one that would inflate under a per-slot
    # DISTINCT count (3 cameras × 3 acts × 3 framings = 27
    # "trios" by the broken reading, of which only 3 are real).
    trios = [
        ("cam-a", "act-a", "frame-a"),
        ("cam-b", "act-b", "frame-b"),
        ("cam-c", "act-c", "frame-c"),
    ]
    for cam, act, framing in trios:
        _seed_verified_trio(cam, act, framing,
                            manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_candidate(k, f"camera {k} text")  for k, _, _ in trios],
        "act":     [_candidate(k, f"act {k} text")     for _, k, _ in trios],
        "framing": [_candidate(k, f"framing {k} text") for _, _, k in trios],
    }

    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 5, "candidates": candidates,
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]

    # The four literals the user pinned, asserted separately so
    # a future "let me drop the largest fillable" or "let me
    # drop the slot name" fails the test that names the thing
    # it dropped. `in` over the whole sentence would pass on a
    # message that had all four crammed together; the per-thing
    # assert is the one that catches the shape the operator
    # needs.
    assert "camera" in detail, f"slot not named: {detail!r}"
    assert "3" in detail, f"verified count not named: {detail!r}"
    assert "3" in detail, f"largest fillable not named: {detail!r}"
    assert "exploratory" in detail, f"exploratory mode not named: {detail!r}"
    # The requested count is in the message for context — the
    # operator needs to see what was asked, not just the
    # shortfall. Not one of the four user-pinned literals, but
    # the message carries it, and the test pins it because the
    # user's "el mensaje dice 3 y 5" is the visible shape the
    # operator relies on.
    assert "5" in detail, f"requested count not named: {detail!r}"

    # The loop-closed test: a refusal is a refusal, not a
    # partial run. `db.run` commits per INSERT, so the pre-check
    # is what stops a "shorter run, delivered" — the assertion
    # is the shot count, not just the status code. A future
    # "let me queue first, validate after" would flip this to
    # `n > 0`, and that is the regression the test exists to
    # catch.
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, f"refusal must not queue: shot table has {n} rows for session {sid}"


def test_a_strict_run_counts_only_the_sessions_checkpoint_in_the_pool(client, seeded):
    """The count is on the session's manner and checkpoint, the same
    way 3.2 scopes the cell lookup. A trio verified on a different
    checkpoint does not add to the pool — the cell is the
    five-tuple, and a session on a different checkpoint looks at a
    different cell. Without this, a session on the Krea 2 mix
    could claim finepornV4's verified trios as its own pool and
    queue a run that the cell table says nothing about.

    The session is on the Krea 2 mix; the verified trios are
    seeded on finepornV4. The pool scoped to the session is
    empty, and the refusal names a slot with a count of 0. The 0
    case is the one a broken query (e.g., a "let me drop the
    checkpoint from the WHERE") would silently turn into a
    pass: a session on the Krea 2 mix with an empty pool in its
    scope would be told the pool has 3 trios and would queue.
    The test pins the refusal.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "wrong checkpoint pool",
        "manner": "directed", "checkpoint": "Krea 2 mix",
        "shots": [],
    }).json()["id"]

    # 3 verified trios on finepornV4. The session is on the
    # Krea 2 mix, so the scoped pool is empty — a different
    # checkpoint is a different cell, and the cell is the
    # five-tuple.
    for cam, act, framing in [
        ("cam-a", "act-a", "frame-a"),
        ("cam-b", "act-b", "frame-b"),
        ("cam-c", "act-c", "frame-c"),
    ]:
        _seed_verified_trio(cam, act, framing,
                            manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_candidate(k, f"camera {k} text")  for k in ["cam-a", "cam-b", "cam-c"]],
        "act":     [_candidate(k, f"act {k} text")     for k in ["act-a", "act-b", "act-c"]],
        "framing": [_candidate(k, f"framing {k} text") for k in ["frame-a", "frame-b", "frame-c"]],
    }

    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 1, "candidates": candidates,
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # The scoped pool is empty, not 3: the cell table's WHERE
    # scopes the count to the session's manner and checkpoint,
    # and a cell on finepornV4 is a row in a different scope.
    assert "0" in detail, f"scoped count of 0 not named: {detail!r}"
    assert "exploratory" in detail, f"exploratory mode not named: {detail!r}"
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, f"refusal must not queue: shot table has {n} rows for session {sid}"


def test_a_strict_run_queues_only_trios_that_are_verified_cells(client, seeded):
    """The loop-closed test the user named: three verified trios
    with no shared components, asked for 3, and every queued
    trio has to be a row in the cell table. This is the
    assertion that distinguishes "there are verified
    components" (the per-slot reading, which the original 3.3
    had) from "there are verified trios" (the cell-table
    reading, which the trio pool enforces). A zipped picker
    reading 3 cameras × 3 acts as 9 "trios" and drawing
    `(cam-a, act-c, frame-b)` — a trio that nobody verified
    — would queue 3 shots, satisfy the count, and only fail
    this test. The pre-check is what makes the test
    consistent: the pool is the set of rows, the picker
    draws from the set, every queued trio is a cell.

    The shape the user named is the one that catches the
    bug: 3 trios, 3 distinct cameras, 3 distinct acts, 3
    distinct framings — a per-slot DISTINCT count reads the
    same 3 / 3 / 3 from this seed, but a cartesian product
    of the three lists (the per-slot reading) would say
    27 trios, of which 24 are not in the table. The
    original 3.3's first test (`test_a_strict_run_with_a_large_enough_pool_queues_n_distinct_shots`)
    seeded the same shape and passed on the broken
    arithmetic, because the assertion was per-slot
    no-repeat and the per-slot reading happened to satisfy
    it by coincidence. The new test asserts the trio is
    in the seeded set, which the per-slot reading
    cannot satisfy.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "no shared components",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    trios = [
        ("cam-a", "act-a", "frame-a"),
        ("cam-b", "act-b", "frame-b"),
        ("cam-c", "act-c", "frame-c"),
    ]
    for cam, act, framing in trios:
        _seed_verified_trio(cam, act, framing,
                            manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_candidate(k, f"camera {k} text")  for k, _, _ in trios],
        "act":     [_candidate(k, f"act {k} text")     for _, k, _ in trios],
        "framing": [_candidate(k, f"framing {k} text") for _, _, k in trios],
    }

    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 3, "candidates": candidates,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3
    assert len(body["ids"]) == 3

    # The loop-closed assertion: every queued trio is a
    # row in the cell table. The `components` column on
    # the shot row carries the (slot, wording) pair; we
    # read it back and assert each shot's trio is in the
    # seeded set. A picker that zips per-slot lists and
    # produces `(cam-a, act-c, frame-b)` would queue 3
    # shots that are NOT in the seeded set, and the
    # assertion catches it. The test reads the column
    # directly (not the API) because the API's shot
    # representation does not currently include the
    # wording keys, and adding a wire field just for this
    # test would be a 3.3-shaped change, not a test
    # convenience.
    session = client.get(f"/api/sessions/{sid}").json()
    seeded_set = set(trios)
    for shot in session["shots"]:
        row = db.one("SELECT components FROM shot WHERE id=?", shot["id"])
        comps = db.jload(row, "components")
        actual = (
            comps["components"]["camera"]["wording"],
            comps["components"]["act"]["wording"],
            comps["components"]["framing"]["wording"],
        )
        assert actual in seeded_set, (
            f"shot {shot['id']} drew {actual!r}, which is not a "
            f"verified cell — the picker read per-slot DISTINCT and "
            f"zipped, and that is the bug the trio pool exists to prevent"
        )


def test_a_strict_run_with_an_empty_trio_pool_is_refused(client, seeded):
    """The pool is empty: no cell is verified for the session's
    manner and checkpoint, and a request for even one photograph
    is refused. The message names the slot and the count of 0 —
    the "0" is the visible shape the operator reads, and a
    future "let me coerce 0 to 1" or "let me drop the count
    from the message" fails the test that pins it.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "empty pool",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    # No cells seeded. The pool is empty.
    candidates = {
        "camera":  [_candidate("cam-a", "camera text")],
        "act":     [_candidate("act-a", "act text")],
        "framing": [_candidate("frame-a", "framing text")],
    }

    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 1, "candidates": candidates,
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "0" in detail, f"count of 0 not named: {detail!r}"
    assert "exploratory" in detail, f"exploratory mode not named: {detail!r}"
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0


def test_a_strict_run_on_a_session_missing_manner_or_checkpoint_is_refused_before_the_count(client, seeded):
    """The run-level pre-check is the same one 3.2 runs on the
    one-shot endpoint: a session without manner or checkpoint
    cannot have any cell that matches, and the cell lookup
    would silently find zero rows and read as "not verified".
    The refusal is at the session level, before the pool count,
    and it names what is missing.

    The loop-closed property is the same: a refusal is a
    refusal, the shot table is empty, and a future "let me drop
    the missing-dimensions check and rely on the pool being
    empty" would flip this to `n > 0` for a session that
    actually has cells — the test pins the empty result for
    the session that has neither.
    """
    # A bare workflow: no CheckpointLoaderSimple / UNETLoader, so
    # `graph_checkpoint` returns '' and the session's checkpoint
    # ends up empty after derivation. `manner` is also left
    # empty on the body.
    bare_wf = client.post("/api/workflows", json={
        "name": "bare",
        "graph": {"1": {"class_type": "CLIPTextEncode",
                        "inputs": {"text": "x", "clip": ["2", 1]}}},
    }).json()
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "missing dimensions",
        "workflow_id": bare_wf["id"],
        "shots": [],
    }).json()["id"]

    candidates = {
        "camera":  [_candidate("cam-a", "camera text")],
        "act":     [_candidate("act-a", "act text")],
        "framing": [_candidate("frame-a", "framing text")],
    }

    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 1, "candidates": candidates,
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # Session-level refusal, not pool-level. The message names
    # what's missing, not a slot or a count.
    assert "missing" in detail, f"missing-dimensions not named: {detail!r}"
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0


def test_a_strict_run_never_repeats_a_component_within_a_single_run(client, seeded):
    """The loop-closed test the user named: a pool with fewer
    distinct cameras than trios, asked for the per-slot min
    (which the pre-check said fit), and the greedy must never
    deliver a run with a repeated component. The pre-check
    and the draw are the same calculation now — a parallel
    DISTINCT count over-promised and `shuffle(pool)[:count]`
    under-delivered: the user's probe (3 trios, 2 cameras,
    count=2) put both c1 trios first in 2 of 6 shuffles, and
    the old code queued 2 shots with c1 four times in twelve.
    The new greedy skips a trio whose components are already
    in `used`, so every chosen trio lands on a fresh slot
    in every slot. The test runs the endpoint twelve times
    because a single iteration often hits a lucky shuffle
    and the no-repeat assertion passes by chance — twelve is
    the count that surfaced the four failures in the user's
    probe.

    The assertion per iteration: either 422 (the greedy's
    largest fillable was < count) OR 200 with `count` shots
    whose components do not repeat within the run. The old
    code's failure mode was the second half: 200 with
    repeats. The new code is the conjunction: the greedy
    either delivers the count with no repeats, or it refuses.
    The "or" is what makes the test stable across pool
    shapes — a pool where the greedy can fall short of the
    per-slot min (the tripartite-matching ceiling the
    ponytail names) will sometimes 422, and that is also
    correct.

    Each iteration runs against a FRESH session: 3.4's
    tuple dedup refuses a second compose-run on a session
    that already holds the same trios, and the cross-run
    refusal is exactly the behavior the new check exists to
    pin. Reusing one session across iterations would have
    every iteration after the first refuse on the tuple
    axis and the within-run no-repeat would be untested
    after iteration 0. The cell table is shared (the pool
    does not change) so the within-run property is the same
    on every iteration; only the session is fresh.
    """
    # The user's probe pool: 3 trios, 2 distinct cameras. The
    # per-slot min is 2 (cameras), which the pre-check says
    # fits. The shuffle is probabilistic over 3! = 6
    # orderings: 2/6 put both c1 trios first, and the old
    # `shuffle(pool)[:2]` delivered both. The new greedy
    # takes the first c1 trio, skips the second (c1 used),
    # and takes c2.
    trios = [
        ("cam-a", "act-a", "frame-a"),
        ("cam-a", "act-b", "frame-b"),
        ("cam-b", "act-c", "frame-c"),
    ]
    for cam, act, framing in trios:
        _seed_verified_trio(cam, act, framing,
                            manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_candidate(k, f"camera {k} text")  for k in {"cam-a", "cam-b"}],
        "act":     [_candidate(k, f"act {k} text")     for k in {"act-a", "act-b", "act-c"}],
        "framing": [_candidate(k, f"framing {k} text") for k in {"frame-a", "frame-b", "frame-c"}],
    }

    n_iterations = 12
    for i in range(n_iterations):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": f"greedy no-repeat {i}",
            "manner": "directed", "checkpoint": "finepornV4",
            "shots": [],
        }).json()["id"]
        r = client.post(f"/api/sessions/{sid}/compose-run",
                        json={"count": 2, "candidates": candidates})
        if r.status_code == 422:
            # Refused — the greedy's largest fillable was
            # less than 2. Acceptable: the test allows the
            # "or refused" half. The refusal must not have
            # queued shots: a future "let me queue before
            # validating" would flip this to `n_shots > 0`
            # for the refused iteration, and that is the
            # regression this branch pins shut.
            detail = r.json()["detail"]
            assert "largest fillable" in detail, (
                f"iteration {i}: 422 must name largest fillable, got {detail!r}"
            )
            n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
            assert n == 0, (
                f"iteration {i}: 422 must not queue, shot table has {n} rows"
            )
            continue
        assert r.status_code == 200, (
            f"iteration {i}: expected 200 or 422, got {r.status_code}: {r.text}"
        )
        ids = r.json()["ids"]
        assert len(ids) == 2, f"iteration {i}: expected 2 ids, got {ids!r}"

        # The loop-closed assertion: no component is repeated
        # within this run's 2 shots. Read the components
        # column directly (the API does not currently surface
        # wording keys on a shot, and adding a wire field
        # just for this test would be a 3.3-shaped change,
        # not a test convenience). The old code's failure
        # was exactly here: two shots with the same camera
        # (or act, or framing) because the parallel count
        # over-promised and the draw under-delivered.
        rows = [db.one("SELECT components FROM shot WHERE id=?", id) for id in ids]
        comps = [db.jload(row, "components")["components"] for row in rows]
        for slot in ("camera", "act", "framing"):
            used_in_run = [c[slot]["wording"] for c in comps]
            assert len(used_in_run) == len(set(used_in_run)), (
                f"iteration {i}: slot {slot} repeated within the run "
                f"({used_in_run!r}) — the pre-check and the draw were "
                f"two different calculations, and the draw over-promised"
            )


def test_a_strict_run_gives_the_same_verdict_for_the_same_pool_and_count(client, seeded):
    """The loop-closed test for the multi-shuffle greedy: the
    largest fillable reported by a refusal must be the best
    result the greedy can deliver across multiple shuffles,
    not one shuffle's luck. The single-shuffle code gave
    inconsistent verdicts on the same pool+count: the user's
    probe (pool (c1,a1,f1), (c1,a2,f2), (c2,a1,f3), count=2)
    returned 200 nine times in twenty and 422 saying
    "largest fillable is 1" the other eleven — a shuffle
    that starts with (c1,a1,f1) blocks both other trios
    (a1 is used, c1 is used) and the greedy reports 1 even
    though 2 is reachable. The operator refused would retry
    without changing anything and get 200, which is the
    bug the multi-shuffle pass fixes.

    The fix: run the greedy over N_SHUFFLES (=10) shuffles,
    keep the best, stop early when a shuffle reaches
    `count`. The check and the draw are still one
    calculation (selection and number come from the same
    place), and the largest fillable is the best result,
    not one shuffle's draw.

    The test repeats the same pool+count twenty times and
    asserts all twenty return the same status code and
    (if 200) the same shot count. With the multi-shuffle
    greedy, all twenty should return 200 with 2 shots;
    the probability of all-bad shuffles on the user's
    pool is (1/3)^10 ≈ 1.7e-5, well below the 20-call
    test's flake budget. The old single-shuffle code
    fails this test ~100% of the time: roughly half the
    calls return 422 and the verdicts vary.

    Each iteration runs against a fresh session for the
    same reason `test_a_strict_run_never_repeats_a_component_within_a_single_run`
    does: 3.4's tuple dedup refuses a second compose-run
    on a session that already holds the same trios, and
    the multi-shuffle pass is what the verdict is supposed
    to be stable across — the dedup is orthogonal to that
    and the test has to be shaped around it.
    """
    # The user's probe pool: 3 trios, 2 distinct cameras.
    # The maximum independent set is 2 (take
    # (cam-a, act-b, frame-b) and (cam-b, act-a, frame-c),
    # which share no components). A single shuffle that
    # starts with (cam-a, act-a, frame-a) blocks both
    # other trios: act-a is used, blocking the only
    # cam-b trio; cam-a is used, blocking the second
    # cam-a trio. The greedy returns 1, the operator sees
    # "largest fillable is 1", and the 2 that was
    # achievable is silently lost. The multi-shuffle
    # greedy finds 2 on any shuffle that doesn't start
    # with (cam-a, act-a, frame-a).
    trios = [
        ("cam-a", "act-a", "frame-a"),
        ("cam-a", "act-b", "frame-b"),
        ("cam-b", "act-a", "frame-c"),
    ]
    for cam, act, framing in trios:
        _seed_verified_trio(cam, act, framing,
                            manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_candidate(k, f"camera {k} text")  for k in {"cam-a", "cam-b"}],
        "act":     [_candidate(k, f"act {k} text")     for k in {"act-a", "act-b"}],
        "framing": [_candidate(k, f"framing {k} text") for k in {"frame-a", "frame-b", "frame-c"}],
    }

    verdicts = []
    for i in range(20):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": f"verdict consistency {i}",
            "manner": "directed", "checkpoint": "finepornV4",
            "shots": [],
        }).json()["id"]
        r = client.post(f"/api/sessions/{sid}/compose-run",
                        json={"count": 2, "candidates": candidates})
        if r.status_code == 200:
            verdicts.append(("200", len(r.json()["ids"])))
        else:
            verdicts.append((str(r.status_code), r.json()["detail"]))

    # The loop-closed assertion: all 20 calls on the same
    # pool+count return the same verdict. The old code
    # varied between ("200", 2) and ("422", "largest
    # fillable is 1"); the new code is consistent because
    # the multi-shuffle greedy finds 2 on essentially
    # every call.
    assert len(set(verdicts)) == 1, (
        f"verdicts vary across calls: {verdicts!r} — the "
        f"largest fillable is the result of one shuffle, "
        f"not the best across multiple"
    )
    # The pool's maximum is 2, so every call should
    # return 200 with 2 shots. The shape of the
    # assertion is "all the same AND that same is the
    # expected one" — a future "let me always refuse"
    # would flip the verdict to ("422", ...) for all
    # 20 and fail this half too, which is the
    # regression the test exists to catch.
    assert verdicts[0] == ("200", 2), (
        f"expected all calls to return 200 with 2 shots, got {verdicts[0]!r}"
    )


# -------------------------------------------------------------- 3.4 run dedup
#
# 3.3 says "the pool is the set of verified trios, the picker draws
# distinct trios from it". 3.4 adds: even when the pool is large enough
# and the picker drew N distinct trios, the run is still refused if a
# chosen trio collides with what is already in the session. Two
# distinct checks, both running BEFORE any INSERT (`db.run` auto-commits,
# a check that fires at k+1 would leave k rows — the same loop-closed
# property 3.3 pins on the pool-too-small refusal):
#
# 1. **Tuple check.** A candidate `(camera_wording, act_wording,
#    framing_wording)` is refused if it equals a row's stored trio in
#    `shot.components`. A written row has `components='{}'`, the
#    schema's marker for "no trio here" (3.1's note: "A written shot
#    leaves the column at its empty default '{}'"), so it is skipped on
#    the tuple axis — `if not comps: continue` is the explicit answer
#    to the decision the user pinned. The line check below still runs
#    against it.
#
# 2. **Line check.** A candidate composed `prompt` is refused if it
#    equals an existing row's `prompt` text. This is the only check
#    that catches the wink/finger shape: two distinct act keys whose
#    wording text is identical, the tuple key differs, the joined
#    line does not. Two distinct tuples can join to the same line
#    (`design.md:286-291` and the cell-spec decision the trio key
#    encodes), and the line check is the loop-closed test the user
#    named. The within-run case (a wink/finger pair in `best_chosen`,
#    no prior shots) is the same check against an in-loop set: the
#    first candidate adds its line to `seen_lines`, the second fires.
#
# The 422 names the axis that collided: "tuple already enqueued" for
# the trio, "line already enqueued" for the joined line. The two
# messages are asserted separately so a future "let me drop the axis
# name" fails the test that names the dropped word.


def test_a_strict_run_refuses_a_tuple_already_enqueued_by_an_earlier_compose(client, seeded):
    """The tuple-axis case: a previous compose on this session
    enqueued `(cam-a, act-a, frame-a)`, and the next compose-run
    asks the picker to draw the same trio again. The tuple check
    refuses on the FIRST collision (no shorter run, no mid-loop
    INSERT — the 3.3 refusal shape carries over). The 422 names
    the axis (`tuple`) and the trio, so the operator can see
    WHICH trio they re-asked for, not just that they did.

    The session is fresh except for one composed shot on the
    exact trio the test asks for; the pool seeded in the cell
    table is one trio, and the picker would draw it. The check
    fires before the picker would have written the second copy,
    so `n_shots` after the 422 is 1 (the pre-existing composed
    shot), not 0 — the loop-closed property is "the refused
    run queued nothing NEW", not "the table is empty". A
    future "let me queue first, validate after" would flip the
    count to 2 and the test pins that.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "tuple dedup",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    _seed_verified_trio("cam-a", "act-a", "frame-a",
                        manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_candidate("cam-a", "cam-a text")],
        "act":     [_candidate("act-a", "act-a text")],
        "framing": [_candidate("frame-a", "frame-a text")],
    }

    # Pre-populate: the first compose-run on a fresh session
    # enqueues one shot on the trio.
    first = client.post(f"/api/sessions/{sid}/compose-run",
                        json={"count": 1, "candidates": candidates})
    assert first.status_code == 200, first.text
    assert len(first.json()["ids"]) == 1

    # The second compose-run asks for the same trio. The
    # tuple check refuses before any INSERT: `n_shots`
    # after the 422 is the pre-existing 1, not 2.
    r = client.post(f"/api/sessions/{sid}/compose-run",
                    json={"count": 1, "candidates": candidates})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "tuple already enqueued" in detail, (
        f"axis name not in message: {detail!r}"
    )
    assert "('cam-a', 'act-a', 'frame-a')" in detail, (
        f"the trio not named in the message: {detail!r}"
    )
    # Loop-closed: the refused run queued nothing new. The
    # pre-existing shot is the only one in the table — a
    # future "let me queue before validating" would flip
    # this to 2 and the test pins that.
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 1, (
        f"refused run queued new shots: shot table has {n} rows "
        f"for session {sid}, expected 1 (the pre-existing compose)"
    )


def test_a_strict_run_refuses_a_line_already_enqueued_by_an_earlier_compose_wink_finger(client, seeded):
    """The line-axis case the user named: two distinct tuples
    that join to the same composed line. The user's reference
    pair is `wink` and `finger` in KISS_FRAMES — two concepts
    whose wording text is identical by design (tested in
    `tests/test_one_home.py::test_wink_and_finger_are_an_allowed_pair_with_shared_text`).
    The test uses the same pair as the act candidates; the
    camera and framing candidates also share their wording
    text across the two distinct keys, so the joined line is
    identical on both trios.

    Two verified trios in the cell table — `(cam-a, wink,
    frame-a)` and `(cam-b, finger, frame-b)` — and the picker
    draws both. The two tuples are distinct on every key
    (cam-a/cam-b, wink/finger, frame-a/frame-b), so the tuple
    check does NOT fire; the joined line is the same wording
    text concatenated the same way, so the line check DOES
    fire on the second candidate. The 422 names the axis
    (`line`) and the joined prompt.

    Without the line check, the two shots would queue with
    identical prompts and `shot.prompt` would carry the same
    line twice in the gallery — exactly the kind of "two
    photographs of one line" failure `repeats` in
    `enhance.js:99-115` was added to catch on the writer's
    side. The composer side now catches it before the line
    reaches the queue, which is the only place it can be
    caught on a non-LLM path (the model is not in the loop).

    A note on the test shape: 3.3's pool-too-small refusal
    fires before 3.4's line check, so the pool has to be
    large enough for the picker to draw 2. That is the
    reason all three slots carry two distinct keys with the
    same wording text — 2 cameras × 2 acts × 2 framings is
    the shape that lets the no-component-repeat rule draw 2
    from a 2-trio pool, where 1 camera would cap the draw
    at 1 and the run would refuse on 3.3's axis, not 3.4's.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "wink finger line",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    # The wink/finger wording text — identical by design, the
    # documented exception in `tests/test_one_home.py`. Pulled
    # from `KISS_FRAMES` in kinds.js so the test reads as the
    # real pair, not a synthetic stand-in. The test does not
    # import kinds.js (the backend test surface is Python) —
    # the constant below is the wording text the pair shares
    # verbatim, copied once so the test is self-contained.
    kiss_text = (
        "Her lips are pushed forward in a kiss blown at the camera, "
        "her head tilted playfully to one side, and SHE IS WINKING - "
        "one eye squeezed fully shut, the other open and looking "
        "straight at the lens."
    )
    cam_text = "the camera text, shared across cam-a and cam-b."
    frame_text = "the framing text, shared across frame-a and frame-b."

    _seed_verified_trio("cam-a", "wink",   "frame-a",
                        manner="directed", checkpoint="finepornV4")
    _seed_verified_trio("cam-b", "finger", "frame-b",
                        manner="directed", checkpoint="finepornV4")

    candidates = {
        # Two camera keys, SAME wording text. The picker
        # can draw both because the keys differ; the
        # joined line is identical because the text is.
        "camera":  [_candidate("cam-a", cam_text),
                    _candidate("cam-b", cam_text)],
        # The act candidates are the wink/finger pair:
        # two distinct keys, SAME wording text, the
        # pattern the user pinned.
        "act":     [_candidate("wink",   kiss_text),
                    _candidate("finger", kiss_text)],
        "framing": [_candidate("frame-a", frame_text),
                    _candidate("frame-b", frame_text)],
    }

    r = client.post(f"/api/sessions/{sid}/compose-run",
                    json={"count": 2, "candidates": candidates})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "line already enqueued" in detail, (
        f"axis name not in message: {detail!r}"
    )
    # The composed line carries the camera text, the kiss
    # text, and the framing text. The kiss text is the
    # wink/finger-distinguishing content; the camera and
    # framing texts are what makes the two trios'
    # joined lines actually identical. Pin the kiss
    # text in the 422 so the operator sees WHICH line
    # collided.
    assert kiss_text in detail, (
        f"the joined line not in the 422 message: {detail!r}"
    )
    # The tuple axis did NOT fire: "tuple" is not in the
    # message. A future "let me always say tuple" would
    # put it in and this assert catches the regression.
    assert "tuple already enqueued" not in detail, (
        f"tuple axis should not fire on a wink/finger "
        f"line collision: {detail!r}"
    )
    # Loop-closed: the refused run queued nothing.
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, (
        f"refused run queued shots: shot table has {n} rows "
        f"for session {sid}, expected 0"
    )


def test_a_strict_run_refuses_a_line_already_enqueued_by_an_earlier_written_shot(client, seeded):
    """The cross-domain case the user pinned: a written shot
    (the writer's path, not the composer's) carries a `prompt`
    that the composer would join to. The written row's
    `components='{}'` is the explicit case the comparison
    decision answers — tuple check skipped (no trio to
    compare), line check fires (the prompt text is fully
    comparable regardless of how the row was generated).

    The test writes one shot with a prompt that the composer
    would reproduce verbatim, then runs compose-run on a
    trio that joins to the same line. Without the line check
    crossing the written/composed boundary, the run would
    queue a second shot with the same prompt — the gallery
    would carry the same line twice, one written and one
    composed, which is the kind of cross-domain repeat the
    user's "the dedup target includes lines written by the
    writer" callout names. The line check fires on
    `existing_lines` (which includes the written shot's
    `prompt`), the run refuses with the line-axis message.

    The verification of the composed line: the writer's
    `_compose` and the composer's `compose_shot` go through
    the same `_sentences` join, so a take prompt identical
    to the trio's concatenated wording text joins to the
    same line the composer would produce. The test reads
    the written shot's prompt back from the row and asserts
    the 422 message carries it, which is the loop-closed
    property — the message names the line the operator
    already has.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "written line dedup",
        "manner": "directed", "checkpoint": "finepornV4",
        # The look and wardrobe are empty so the joined
        # line is just trigger + base + the take text —
        # matching the composed line below is one
        # substitution away.
        "look": "",
        "wardrobe": "",
        "shots": [],
    }).json()["id"]

    _seed_verified_trio("cam-a", "act-a", "frame-a",
                        manner="directed", checkpoint="finepornV4")

    # The take's `prompt` is what the writer hands to
    # `_compose`. The composed line for the trio below
    # joins to the same line because `compose_shot` and
    # `_compose` use the same `_sentences` (3.1's loop-
    # closed test pins that — `test_a_composed_shot_joins_identically_to_a_written_one`).
    # The take prompt IS the trio's joined sentence.
    take_prompt = "cam-a text. act-a text. frame-a text."
    client.post(f"/api/sessions/{sid}/shots", json={
        "shots": [{"prompt": take_prompt, "count": 1}],
    })
    written = db.one(
        "SELECT id, prompt, components FROM shot WHERE session_id=?",
        sid,
    )
    assert written is not None, "written shot was not created"
    # The written row's `components` is the empty default,
    # the explicit case the comparison decision answers:
    # tuple check skipped, line check runs.
    assert written["components"] == "{}"

    candidates = {
        "camera":  [_candidate("cam-a", "cam-a text")],
        "act":     [_candidate("act-a", "act-a text")],
        "framing": [_candidate("frame-a", "frame-a text")],
    }
    r = client.post(f"/api/sessions/{sid}/compose-run",
                    json={"count": 1, "candidates": candidates})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "line already enqueued" in detail, (
        f"line axis not in message: {detail!r}"
    )
    # The joined line carries the take's text — pin it
    # in the message so the operator sees which line
    # collided.
    assert take_prompt in detail, (
        f"the joined line not in the 422 message: {detail!r}"
    )
    # The tuple axis did NOT fire: the written row has
    # no trio, and the tuple check explicitly skips
    # `components='{}'` rows. A future "let me always
    # check tuple" would put "tuple" in the message
    # and this assert catches the regression.
    assert "tuple already enqueued" not in detail, (
        f"tuple axis should not fire on a written row "
        f"with components='{{}}': {detail!r}"
    )
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    # The refused run queued nothing NEW. The written shot
    # is the only row — a future "let me queue first"
    # would flip this to 2 and the test pins that.
    assert n == 1, (
        f"refused run queued new shots: shot table has {n} rows "
        f"for session {sid}, expected 1 (the written shot)"
    )


def test_a_strict_run_refuses_a_within_run_line_collision(client, seeded):
    """The in-loop line check: two trios in `best_chosen`
    that join to the same line. No prior shots, so
    `existing_lines` is empty — the collision is between
    the first and the second candidate in the run itself.
    The first candidate adds its line to `seen_lines`; the
    second fires the line check against `seen_lines`. The
    422 names the line, not the trio (the trios are
    distinct — that's the whole point).

    This is the within-run shape of the wink/finger
    collision: a session that has never been composed on
    before, the operator asks for 2, and the picker draws
    two trios whose joined lines collide. The session
    never had a chance to "already have" the line, so the
    cross-run case (the previous test) is a different
    axis — the in-loop set is the loop-closed property:
    the pre-check walks `best_chosen` in order, the
    first candidate seeds `seen_lines`, the second one
    is the one that fires. A future "let me only check
    existing_lines" would miss this case: `seen_lines`
    is the in-loop half, and the test pins it.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "within-run line",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    # All three slots carry two distinct keys with the
    # SAME wording text. The pool has 2 trios, one on each
    # key combination: (cam-a, act-a, frame-a) and
    # (cam-b, act-b, frame-b). The joined line is the
    # same on both. The 3.3 within-run component check
    # passes (all three keys are distinct), so the picker
    # draws both — and without the line check, both would
    # queue with the same prompt.
    _seed_verified_trio("cam-a", "act-a", "frame-a",
                        manner="directed", checkpoint="finepornV4")
    _seed_verified_trio("cam-b", "act-b", "frame-b",
                        manner="directed", checkpoint="finepornV4")

    shared = "the same text, word for word, on both trios."
    candidates = {
        "camera":  [_candidate("cam-a", shared),
                    _candidate("cam-b", shared)],
        "act":     [_candidate("act-a", shared),
                    _candidate("act-b", shared)],
        "framing": [_candidate("frame-a", shared),
                    _candidate("frame-b", shared)],
    }

    r = client.post(f"/api/sessions/{sid}/compose-run",
                    json={"count": 2, "candidates": candidates})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "line already enqueued" in detail, (
        f"line axis not in message: {detail!r}"
    )
    # The shared text is in the joined line — pin it so
    # the operator sees which line collided within the
    # run. The first candidate's line is the one that
    # seeds `seen_lines`; the second's collides with it
    # and is the one refused.
    assert shared in detail, (
        f"the joined line not in the 422 message: {detail!r}"
    )
    # Loop-closed: nothing queued. The within-run
    # collision is the second candidate, and the run
    # refused before the insert loop started.
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, (
        f"refused run queued shots: shot table has {n} rows "
        f"for session {sid}, expected 0"
    )


# -------------------------------------------------------------- 3.5 session spread
#
# A whole session is the same draw as 3.3 plus the family-spread
# ordering: no two consecutive photographs share a camera family.
# 3.5 inherits 3.3's draw (verified-trio pool, multi-shuffle greedy
# ceiling) and 3.4's dedup (tuple + line) unchanged, and adds one
# ordering constraint on top. The new endpoint is
# `POST /api/sessions/{sid}/compose-session`, a sibling of
# `compose-run` — the two share the same draw helper and differ only
# at the post-draw step.
#
# The scenario the spec names: a session of several photographs has
# no two consecutive photographs sharing a component family in the
# spread slots. Today only the camera slot is a spread slot (its
# wordings carry `family` in `frontend/src/kinds.js:1671-1690`); the
# act and framing slots carry no family, so the spread is exempt on
# them. The reading "a slot without family falls to its own key as a
# family of size 1" would make the constraint unsatisfiable as soon
# as N exceeded the number of act entries (3 today), and the spec
# phrase "in the spread slots" names the slots the catalogue has
# spread data for.
#
# The constraint is the classical "reorganize string" problem.
# Feasibility: `max(count per family) <= ceil(N/2)`. When violated,
# the run is refused with 422 — same shape as the 3.3 pool-too-small
# and 3.4 dedup refusals, the same loop-closed property
# (`n_shots == 0` after the 422), the same message discipline.
#
# The 422 names four facts: the family, its count in the chosen
# trios, the ceil bound, and the conclusion. A future "let me soften
# the message" that drops one fails the assertion that names the
# dropped fact, the same way the 3.3 and 3.4 tests pin their
# messages.


def _family_candidate(key: str, text: str, family: str | None = None) -> dict:
    """One catalogue entry with an explicit `family` on the first
    wording. The 3.5 tests need a candidate whose `family` is set
    so the spread pre-check can read it; the 3.3 / 3.4
    `_candidate` helper omits the field by design. None means
    "no family on this slot" — the spread treats it as a
    non-spread slot (decision 1 in tasks.md 3.5).
    """
    wording = {"key": key, "text": text}
    if family is not None:
        wording["family"] = family
    return {"key": key, "wordings": [wording]}


def test_a_session_compose_spreads_camera_families_across_consecutive_photographs(client, seeded):
    """The named scenario: a session of 4 photographs, the camera
    slot drawn from 4 different cameras across 3 families
    (2 front, 1 shoulder, 1 overhead), and the ordered run has
    no two consecutive photographs sharing a camera family.

    The pool is 4 trios with all components distinct so the
    3.3 no-component-repeat greedy can draw 4 (the multi-shuffle
    ceiling is 4 with this shape, and the multi-shuffle pass
    converges to it across the 10 shuffles). The 3.5 reorder
    then arranges the 4 so the two `front` cameras are not
    adjacent — the feasibility condition
    `max(count per family) <= ceil(4/2) = 2` is satisfied (2 == 2).

    The loop-closed assertion: read the new shots back in
    `shot_index` order (the column the gallery walks, written
    by `compose_and_queue_shot` as `MAX(shot_index) + 1` per
    shot), extract the camera key from `components`, look up
    the family, and assert no two adjacent indices share it.
    A future "let me reorder by family but ignore N=1 spacing"
    would put the two fronts adjacent and this assert catches
    the regression.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "session spread",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    # 4 trios: 2 from the front family, 1 shoulder, 1 overhead.
    # All cameras / acts / framings distinct so the 3.3 greedy
    # draws 4. Two front cameras at the ceil(N/2) bound — the
    # hardest case the feasibility check still admits.
    trios = [
        ("cam-front-a", "act-a", "frame-a"),
        ("cam-front-b", "act-b", "frame-b"),
        ("cam-shoulder", "act-c", "frame-c"),
        ("cam-overhead", "act-d", "frame-d"),
    ]
    for cam, act, framing in trios:
        _seed_verified_trio(cam, act, framing,
                            manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_family_candidate("cam-front-a", "front-a text",  "front"),
                    _family_candidate("cam-front-b", "front-b text",  "front"),
                    _family_candidate("cam-shoulder", "shoulder text", "shoulder"),
                    _family_candidate("cam-overhead", "overhead text", "overhead")],
        "act":     [_candidate("act-a", "act-a text"),
                    _candidate("act-b", "act-b text"),
                    _candidate("act-c", "act-c text"),
                    _candidate("act-d", "act-d text")],
        "framing": [_candidate("frame-a", "frame-a text"),
                    _candidate("frame-b", "frame-b text"),
                    _candidate("frame-c", "frame-c text"),
                    _candidate("frame-d", "frame-d text")],
    }

    r = client.post(f"/api/sessions/{sid}/compose-session",
                    json={"count": 4, "candidates": candidates})
    assert r.status_code == 200, r.text
    ids = r.json()["ids"]
    assert len(ids) == 4

    # Read back the new shots in shot_index order — the column
    # `compose_and_queue_shot` writes as `MAX(shot_index) + 1`,
    # so the order the gallery walks is the order the reorder
    # produced. The components column carries the (concept,
    # wording) pairs per slot, and the camera's family lives
    # on the candidate's wording (read it from the test's own
    # table — the backend does not store family).
    rows = db.q("SELECT id, shot_index, components FROM shot "
                "WHERE session_id=? AND id IN ({}) "
                "ORDER BY shot_index, id".format(",".join("?" * len(ids))),
                sid, *ids)
    families = []
    family_lookup = {
        "cam-front-a": "front",
        "cam-front-b": "front",
        "cam-shoulder": "shoulder",
        "cam-overhead": "overhead",
    }
    for row in rows:
        comps = db.jload(row, "components")["components"]
        cam_key = comps["camera"]["wording"]
        families.append(family_lookup[cam_key])

    # The two fronts MUST be separated. A 4-shot session of
    # [front, front, shoulder, overhead] would fail the
    # assertion and the test would name the regression.
    for i in range(len(families) - 1):
        assert families[i] != families[i + 1], (
            f"adjacent shot {i} and {i+1} share family "
            f"{families[i]!r}; the 3.5 reorder did not spread "
            f"the camera family"
        )

    # And the run queued exactly what was asked for — 4 shots,
    # no shorter, no extras. A future "let me skip the spread
    # when count==4" would still pass the family check but
    # might drop a row, and the loop-closed count catches it.
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 4, f"expected 4 shots queued, got {n}"


def test_a_session_compose_refuses_a_pool_where_one_family_exceeds_half_the_count(client, seeded):
    """The infeasible case: a pool whose majority family
    exceeds `ceil(N/2)`. With N=4, `ceil(4/2) = 2`, and a
    family with 3 cameras admits no permutation where no two
    consecutive share the family — every position is "next"
    to another of the same family and one is forced adjacent.
    The 422 fires before any INSERT, and `n_shots == 0`
    after the 422 is the loop-closed proof.

    The pool has 4 trios with 3 distinct cameras from the
    `front` family and 1 from `shoulder` — `ceil(4/2) = 2`,
    `3 > 2`, refuses. The 3.3 no-component-repeat greedy
    can still draw 3 (2 fronts + 1 shoulder, the 3rd front
    is skipped by the family constraint), and the 422 is
    the pool-too-small refusal from the draw, not the
    post-draw family-infeasible one.

    Before 6.1's flake fix, the family-spread constraint
    was enforced by `_spread_is_feasible` as a post-draw
    accept on the greedy's result. A greedy that happened
    to take all 3 fronts and the shoulder would return
    `best_chosen = 4` and the caller's reorder would raise
    a 422 naming the family, the count, the ceil bound,
    and the conclusion. With the constraint moved into
    the draw itself (the per-trio `_skip_for_spread`),
    the greedy never returns an invalid set: the chosen
    set is always `max <= ceil(N/2)`, and the only
    shape a refusal can take is the pool-too-small one
    (`largest fillable is 3 of 4 requested`). The test
    pins the new shape — the slot, the pool count, the
    largest fillable, the requested count — and the
    loop-closed `n_shots == 0`.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "session spread refuse",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    # 3 cameras in the `front` family, 1 in `shoulder`. All
    # 4 trios distinct on every component so the 3.3 greedy
    # can draw 4 without the no-component-repeat rule firing.
    trios = [
        ("cam-front-a", "act-a", "frame-a"),
        ("cam-front-b", "act-b", "frame-b"),
        ("cam-front-c", "act-c", "frame-c"),
        ("cam-shoulder", "act-d", "frame-d"),
    ]
    for cam, act, framing in trios:
        _seed_verified_trio(cam, act, framing,
                            manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_family_candidate("cam-front-a", "front-a text",  "front"),
                    _family_candidate("cam-front-b", "front-b text",  "front"),
                    _family_candidate("cam-front-c", "front-c text",  "front"),
                    _family_candidate("cam-shoulder", "shoulder text", "shoulder")],
        "act":     [_candidate("act-a", "act-a text"),
                    _candidate("act-b", "act-b text"),
                    _candidate("act-c", "act-c text"),
                    _candidate("act-d", "act-d text")],
        "framing": [_candidate("frame-a", "frame-a text"),
                    _candidate("frame-b", "frame-b text"),
                    _candidate("frame-c", "frame-c text"),
                    _candidate("frame-d", "frame-d text")],
    }

    r = client.post(f"/api/sessions/{sid}/compose-session",
                    json={"count": 4, "candidates": candidates})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]

    # The four facts the pool-too-small message carries,
    # asserted separately so a future "let me shorten the
    # message" that drops one fails the assertion that names
    # it. The slot is the per-slot min of the pool (here the
    # camera slot's 4 distinct cameras ties with act and
    # framing; ties go to camera, then act, then framing).
    # The pool count is the per-slot min, the largest
    # fillable is the multi-shuffle ceiling, the requested
    # count is what the caller asked for.
    assert "camera" in detail, f"slot not named in 422: {detail!r}"
    assert "4" in detail, f"pool count not in 422: {detail!r}"
    assert "3" in detail, f"largest fillable not in 422: {detail!r}"
    assert "largest fillable" in detail, f"phrase 'largest fillable' missing: {detail!r}"
    # Mode tail: in strict mode the operator can switch to
    # exploratory; the message has to keep that suggestion
    # true. The tail is the wording the spec pinned
    # (`use exploratory mode to compose with ... cells`).
    assert "exploratory" in detail, f"exploratory hint missing: {detail!r}"

    # Loop-closed: the refused run queued nothing. A future
    # "let me queue first, validate after" would flip the
    # count to 4 and the test pins that.
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, (
        f"refused session compose queued shots: shot table has "
        f"{n} rows for session {sid}, expected 0"
    )


def test_a_session_compose_keeps_3_3_and_3_4_invariants(client, seeded):
    """Regression: 3.5 inherits 3.3's "every queued shot is on
    a verified cell" and 3.4's "no tuple or line collision
    with a prior composed or written shot". The endpoint is
    the same draw + a reorder on top, and the loop-closed
    property is that the two invariants still hold on the
    3.5 path.

    Two halves:

    (a) The 3.3 invariant. Run a session compose on a pool of
    3 trios, assert every queued shot's `(camera_wording,
    act_wording, framing_wording)` is a row in `cell` for
    the session's `(manner, checkpoint)`. The cell table is
    the only home for "is this trio drawable" and a 3.5 shot
    not on a cell would be a 3.3-shaped regression smuggled
    in through the new endpoint.

    (b) The 3.4 invariant. A session with one prior composed
    shot on `(cam-a, act-a, frame-a)`; the next call asks for
    the same trio; the dedup fires on the tuple axis (3.4's
    loop-closed). The session compose reuses the same dedup
    helper, so a future "let me skip dedup on the session
    path" would let the duplicate through and this assert
    catches the regression.
    """
    # ---- (a) the 3.3 invariant
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "session keeps 3.3",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    trios = [
        ("cam-a", "act-a", "frame-a"),
        ("cam-b", "act-b", "frame-b"),
        ("cam-c", "act-c", "frame-c"),
    ]
    for cam, act, framing in trios:
        _seed_verified_trio(cam, act, framing,
                            manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_family_candidate("cam-a", "cam-a text", "family-a"),
                    _family_candidate("cam-b", "cam-b text", "family-b"),
                    _family_candidate("cam-c", "cam-c text", "family-c")],
        "act":     [_candidate("act-a", "act-a text"),
                    _candidate("act-b", "act-b text"),
                    _candidate("act-c", "act-c text")],
        "framing": [_candidate("frame-a", "frame-a text"),
                    _candidate("frame-b", "frame-b text"),
                    _candidate("frame-c", "frame-c text")],
    }

    r = client.post(f"/api/sessions/{sid}/compose-session",
                    json={"count": 3, "candidates": candidates})
    assert r.status_code == 200, r.text
    ids = r.json()["ids"]
    assert len(ids) == 3

    # Every queued trio is a row in the cell table for the
    # session's (manner, checkpoint). A 3.5 path that drew a
    # trio not on a cell would be a regression on 3.3, and
    # the explicit query is the loop-closed proof.
    for shot_id in ids:
        row = db.one("SELECT components FROM shot WHERE id=?", shot_id)
        comps = db.jload(row, "components")["components"]
        trio = (comps["camera"]["wording"],
                comps["act"]["wording"],
                comps["framing"]["wording"])
        cell = db.one(
            "SELECT 1 AS hit FROM cell "
            "WHERE camera_wording=? AND act_wording=? AND framing_wording=? "
            "AND manner=? AND checkpoint=?",
            *trio, "directed", "finepornV4",
        )
        assert cell is not None, (
            f"3.5 path queued a trio {trio!r} that is not a "
            f"verified cell for (directed, finepornV4) — 3.3 "
            f"invariant broken on the 3.5 endpoint"
        )

    # ---- (b) the 3.4 invariant
    # A fresh session, pre-populated with one composed shot
    # on (cam-d, act-d, frame-d), the same trio the second
    # call will ask for. The session compose's dedup
    # pre-check refuses on the tuple axis, the same way
    # 3.4's compose-run does.
    sid2 = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "session keeps 3.4",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]
    _seed_verified_trio("cam-d", "act-d", "frame-d",
                        manner="directed", checkpoint="finepornV4")
    candidates_d = {
        "camera":  [_family_candidate("cam-d", "cam-d text", "family-d")],
        "act":     [_candidate("act-d", "act-d text")],
        "framing": [_candidate("frame-d", "frame-d text")],
    }
    first = client.post(f"/api/sessions/{sid2}/compose-session",
                        json={"count": 1, "candidates": candidates_d})
    assert first.status_code == 200, first.text
    assert len(first.json()["ids"]) == 1

    # The same trio, asked for again, must refuse on the
    # tuple axis. The dedup's "tuple" message is the loop-
    # closed property: a future "let me skip dedup on the
    # session path" would queue a duplicate and this
    # assert catches it.
    second = client.post(f"/api/sessions/{sid2}/compose-session",
                         json={"count": 1, "candidates": candidates_d})
    assert second.status_code == 422, second.text
    assert "tuple already enqueued" in second.json()["detail"], (
        f"3.4 dedup did not fire on the 3.5 path: {second.json()['detail']!r}"
    )
    n2 = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid2)["n"]
    assert n2 == 1, (
        f"refused session compose queued a duplicate: shot "
        f"table has {n2} rows for session {sid2}, expected 1"
    )


def test_a_session_compose_is_deterministic_for_the_same_pool_and_count(client, seeded):
    """The 3.3 determinism test, shaped for 3.5: 30 calls on
    the same pool+count return the same verdict. The multi-
    shuffle greedy is the only source of variance (the 3.5
    reorder is a deterministic heap pass given the input
    list), and the 3.3 ceiling (N_SHUFFLES=10) keeps the
    verdict stable. With the old single-shuffle code the
    verdicts varied between ("200", 4) and ("422",
    "largest fillable is 1"); the multi-shuffle pass makes
    30 identical verdicts.

    Each iteration runs against a fresh session, the same
    reason 3.3's test does: 3.4's tuple dedup refuses a
    second compose-session on a session that already holds
    the same trios, and the test has to be shaped around
    it. The cell table is shared (the pool does not
    change); only the session is fresh. The within-iteration
    property (the no-two-consecutive-same-family) is the
    same on every iteration; only the verdict stability
    is what this test pins.

    The pool is 4 trios across 3 families (2 front, 1
    shoulder, 1 overhead) so the spread is feasible and
    every successful iteration must produce exactly 4
    shots, no shorter, no extras. A future "let me return
    the count the greedy reached" would flip the verdict
    to ("200", k) for k < 4 and the assertion that names
    the expected count catches the regression.
    """
    trios = [
        ("cam-front-a", "act-a", "frame-a"),
        ("cam-front-b", "act-b", "frame-b"),
        ("cam-shoulder", "act-c", "frame-c"),
        ("cam-overhead", "act-d", "frame-d"),
    ]
    for cam, act, framing in trios:
        _seed_verified_trio(cam, act, framing,
                            manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_family_candidate("cam-front-a", "front-a text",  "front"),
                    _family_candidate("cam-front-b", "front-b text",  "front"),
                    _family_candidate("cam-shoulder", "shoulder text", "shoulder"),
                    _family_candidate("cam-overhead", "overhead text", "overhead")],
        "act":     [_candidate("act-a", "act-a text"),
                    _candidate("act-b", "act-b text"),
                    _candidate("act-c", "act-c text"),
                    _candidate("act-d", "act-d text")],
        "framing": [_candidate("frame-a", "frame-a text"),
                    _candidate("frame-b", "frame-b text"),
                    _candidate("frame-c", "frame-c text"),
                    _candidate("frame-d", "frame-d text")],
    }

    verdicts = []
    for i in range(30):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": f"verdict consistency {i}",
            "manner": "directed", "checkpoint": "finepornV4",
            "shots": [],
        }).json()["id"]
        r = client.post(f"/api/sessions/{sid}/compose-session",
                        json={"count": 4, "candidates": candidates})
        if r.status_code == 200:
            verdicts.append(("200", len(r.json()["ids"])))
        else:
            verdicts.append((str(r.status_code), r.json()["detail"]))

    assert len(set(verdicts)) == 1, (
        f"verdicts vary across calls: {verdicts!r} — the "
        f"3.5 path is non-deterministic given the same "
        f"pool+count"
    )
    assert verdicts[0] == ("200", 4), (
        f"expected all 30 calls to return 200 with 4 shots, "
        f"got {verdicts[0]!r}"
    )


def test_a_session_compose_draws_a_spreadable_set_when_the_pool_is_larger_than_the_count(client, seeded):
    """The draw obeys the spread, it is not filtered by it.

    Every other 3.5 test runs a pool whose size equals the
    count, so the greedy has no choice to make and the family
    mix of the drawn set is fixed before the reorder ever
    sees it. This one gives the greedy a choice: 6 verified
    trios (4 in the `front` family, 1 shoulder, 1 overhead)
    and a count of 4. `ceil(4/2) = 2`, so a draw that takes 3
    or 4 fronts has no valid ordering while a draw that takes
    2 does, and both are reachable by the shuffle.

    With the spread applied AFTER the draw the verdict
    followed the shuffle's luck: 11 of 30 calls returned 200
    and 19 returned a 422 naming the `front` family, on the
    same pool and the same count, with `(f1, s1, f2, o1)`
    sitting in the pool the whole time. That is the shape of
    the single-shuffle bug 3.3 closed, re-entered through the
    family constraint. Handing the feasibility to the draw as
    its `accept` predicate is the fix, and 30 identical 200s
    is the assertion that a future "let me filter after the
    draw" cannot pass.

    The spread property itself is asserted on every
    iteration, not just the verdict: a draw that satisfies
    the feasibility bound but comes back badly ordered is a
    different regression and this test would name it.
    """
    trios = [("cam-f%d" % i, "act-f%d" % i, "frame-f%d" % i) for i in range(4)]
    trios += [("cam-s1", "act-s1", "frame-s1"), ("cam-o1", "act-o1", "frame-o1")]
    for cam, act, framing in trios:
        _seed_verified_trio(cam, act, framing,
                            manner="directed", checkpoint="finepornV4")

    families = {"cam-s1": "shoulder", "cam-o1": "overhead"}
    for i in range(4):
        families["cam-f%d" % i] = "front"
    candidates = {
        "camera":  [_family_candidate(k, k + " text", families[k]) for k, _, _ in trios],
        "act":     [_candidate(a, a + " text") for _, a, _ in trios],
        "framing": [_candidate(f, f + " text") for _, _, f in trios],
    }

    verdicts = []
    for i in range(30):
        sid = client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": "spreadable draw %d" % i,
            "manner": "directed", "checkpoint": "finepornV4",
            "shots": [],
        }).json()["id"]
        r = client.post(f"/api/sessions/{sid}/compose-session",
                        json={"count": 4, "candidates": candidates})
        if r.status_code != 200:
            verdicts.append((r.status_code, r.json()["detail"]))
            continue
        verdicts.append(("200", len(r.json()["ids"])))
        rows = db.q("SELECT components FROM shot WHERE session_id=? "
                    "ORDER BY shot_index, id", sid)
        drawn = [families[db.jload(row, "components")["components"]["camera"]["wording"]]
                 for row in rows]
        for k in range(len(drawn) - 1):
            assert drawn[k] != drawn[k + 1], (
                f"iteration {i}: adjacent shots {k} and {k+1} share "
                f"family {drawn[k]!r} in {drawn!r}"
            )

    assert set(verdicts) == {("200", 4)}, (
        f"the same pool and count did not give the same verdict: "
        f"{sorted(set(verdicts), key=str)!r} - a spreadable set exists "
        f"in this pool, so a 422 here is the draw ignoring the spread"
    )


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


# ---------------------------------------------------------------- 3.6 origin
#
# The session's origin: `''` (draft, no shots), `'written'`,
# `'composed'`, or `'mixed'`. 3.6's spec scenario "a later
# comparison can tell which produced which photographs" reads
# this column. The four tests below cover the four cases the
# spec scenario names; each one is written so a broken
# implementation of the state machine fails it on the spot
# (the failure modes are noted in the docstring of each test
# and were checked by the author by breaking the code on
# purpose before the test was declared green).

def test_a_written_session_behaves_exactly_as_before_and_records_written(client, seeded):
    """The scenario 3.6 names: a session on the written path
    behaves exactly as it did before the column existed, and
    `origin` reads as `'written'` at every step.

    The test walks the full lifecycle (create, add via
    `/api/sessions/{sid}/shots`, expand a take) and asserts
    every property the written path returned before the
    column was added: the session's status, the prompt of
    every shot, the `components == {}` marker on every shot,
    and the round-trip of the `look` and `wardrobe`. The
    new assertion is the explicit `origin == 'written'` —
    a column that defaults to `'composed'` (a wrong-default
    bug) or that the write path never updates (a no-op
    bug) fails the test on the explicit check.

    Failure modes (verified by breaking the code):
    - The helper is never called from `_expand_shots`:
      `origin` stays at `''`, the explicit assertion fails.
    - The helper defaults to `'composed'`: the session
      reads as `'composed'` for a session that has only
      written shots, the assertion fails.
    - The session insert forgets the `''` default: the
      column is whatever the schema has, the test still
      fails on `'written'`.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "written path",
        "look": "white summer dress, hair down, on a beach",
        "wardrobe": "jacket",
        "shots": [{"label": "wide", "prompt": "full body, walking", "count": 1}],
    }).json()["id"]

    # A second take via the `/api/sessions/{sid}/shots` route,
    # which goes through `_expand_shots` again.
    client.post(f"/api/sessions/{sid}/shots", json={
        "shots": [{"label": "close", "prompt": "close-up", "count": 3}],
    })

    # A take that expands to 3 rows. The status is `pending`
    # on every row until the runner processes them; the test
    # does not call `/run` because that would pull a GPU and
    # the runner is not in scope for 3.6.
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["look"] == "white summer dress, hair down, on a beach"
    assert session["wardrobe"] == "jacket"
    assert len(session["shots"]) == 4
    assert [x["shot_index"] for x in session["shots"]] == [0, 1, 1, 1]
    assert session["origin"] == "written", (
        f"written session must read as 'written', got {session['origin']!r}"
    )
    for shot in session["shots"]:
        assert shot["prompt"], "the written path's prompt is empty"
        row = db.one("SELECT components FROM shot WHERE id=?", shot["id"])
        db.jload(row, "components")
        assert row["components"] == {}, (
            f"a written shot's components must be {{}}, got {row['components']!r}"
        )


def test_a_composed_session_is_recorded_as_composed(client, seeded):
    """The composed side of 3.6: a session that has only composed
    shots reads as `'composed'`.

    The session declares `manner` and `checkpoint` so strict
    mode (3.2) does not refuse the compose, and the cell for
    the trio is pre-seeded as `verified` (the same pattern
    3.1's test uses for the strict path). One compose, then
    read the session back and assert the origin.

    Failure modes (verified by breaking the code):
    - `compose_and_queue_shot` never calls the helper: the
      session's origin is still `''` (the default), the
      assertion fails.
    - The helper always writes `'written'`: a session that
      has only composed shots reads as `'written'`, the
      assertion fails.
    - The cell is missing: the strict check refuses the
      compose with 422 before the test can read the origin.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "composed only",
        "look": "white summer dress, hair down, on a beach",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 10, 8)

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    act = {"key": "astride",
           "wordings": [{"key": "astride", "text": "She is astride him."}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "a full-length photograph, head to feet"}]}

    client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
    })

    session = client.get(f"/api/sessions/{sid}").json()
    assert session["origin"] == "composed", (
        f"a session with one composed shot must read as 'composed', "
        f"got {session['origin']!r}"
    )


def test_a_mixed_session_is_recorded_as_mixed(client, seeded):
    """3.4's spec scenario: a session that carries both written
    and composed shots. The state machine in
    `_update_session_origin` has to flip a `'written'` session
    to `'mixed'` on a subsequent composed shot, and a
    `'composed'` session to `'mixed'` on a subsequent written
    shot, and never regress a `'mixed'` session to a single
    kind.

    The test walks the four transitions the helper has to
    hold: empty -> written (the create), written -> mixed
    (a compose on a written session), mixed stays mixed (a
    second written add). The third insertion is the
    load-bearing one — a "last write wins" implementation
    would let this test read `'written'` at the end.

    Failure modes (verified by breaking the code):
    - The helper's CASE expression drops one of the WHEN
      branches: a transition fires incorrectly and the
      session's origin lands on the wrong value, the
      assertion fails.
    - The helper is called from one path and not the other
      (e.g., from compose but not from expand): the second
      transition doesn't fire, the session reads as
      `'written'`, the assertion fails.
    - The helper overwrites unconditionally: the third
      insertion (a written add on a `'composed'` session)
      reads as `'written'`, the assertion fails.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "mixed",
        "look": "white summer dress, hair down, on a beach",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [{"prompt": "standing", "count": 1}],
    }).json()["id"]
    # Empty -> written after the create.
    assert client.get(f"/api/sessions/{sid}").json()["origin"] == "written"

    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 10, 8)
    client.post(f"/api/sessions/{sid}/compose", json={
        "camera": {"key": "front-direct",
                   "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]},
        "act": {"key": "astride", "wordings": [{"key": "astride", "text": "She is astride him."}]},
        "framing": {"key": "full-length",
                    "wordings": [{"key": "full-length", "text": "a full-length photograph, head to feet"}]},
    })
    # Written -> mixed after the compose.
    assert client.get(f"/api/sessions/{sid}").json()["origin"] == "mixed", (
        "a written session with a composed shot must read as 'mixed'"
    )

    client.post(f"/api/sessions/{sid}/shots", json={
        "shots": [{"prompt": "sitting", "count": 1}],
    })
    # Mixed stays mixed after a second written add. This is
    # the load-bearing assertion: a "last write wins" or
    # "first write wins" helper would let this read as
    # 'mixed' by accident, but a wrong helper that loses
    # the state would let it read as 'written' (last
    # write) or 'composed' (impossible here, but the shape
    # of the bug). A "let me drop the 'mixed' branch"
    # regression lands here as a clear failure.
    assert client.get(f"/api/sessions/{sid}").json()["origin"] == "mixed", (
        "a 'mixed' session must stay 'mixed' on the next insertion, "
        "the state machine does not regress"
    )


# ---------------------------------------------------------------- 6.1 exploratory
#
# Exploratory mode widens the strict draw to include unmeasured
# (`unknown`) cells, and refuses `dead` cells in both modes. The
# mode is a Literal on every composer payload — pydantic rejects
# unknown values at the boundary, so a request that tries to set
# `mode=anything` cannot bypass the check. The four tests below
# cover the four named scenarios from 6.1 and 2.5: an unknown
# cell is drawable in exploratory, a dead cell is undrawable in
# both modes, a free-string mode is rejected at the boundary,
# and the run-level / session-level endpoints inherit the same
# mode semantics. The "dead wording is never drawn" property
# 2.5 asked for is what `test_a_dead_cell_is_undrawable_in_both_modes`
# pins at the one-shot, run, and session levels — the
# loop-closed property 2.5 named, applied to both modes the
# composer now accepts.


def _seed_unknown_trio(camera: str, act: str, framing: str, *, manner: str, checkpoint: str,
                       judged: int = 0, arrived: int = 0) -> None:
    """Insert one cell with `judged < 10` (so `db.cell_state` reads
    `unknown`). 6.1's exploratory mode is allowed to draw from
    these; strict refuses them. The defaults `judged=0, arrived=0`
    are the canonical "never measured" state. A non-zero `arrived`
    would still land as `unknown` while `judged < 10`, and the
    state is the one that drives the draw, not the counts.
    """
    assert judged < 10, "an unknown cell has judged < 10, not a verified cell"
    db.run(
        "INSERT INTO cell (camera_wording, act_wording, framing_wording, "
        "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
        camera, act, framing, manner, checkpoint, judged, arrived,
    )


def _seed_dead_trio(camera: str, act: str, framing: str, *, manner: str, checkpoint: str,
                    judged: int = 12, arrived: int = 0) -> None:
    """Insert one cell that lands as `dead` under `db.cell_state`:
    `judged >= 10` AND `arrived*10 < judged*8`. The defaults
    `judged=12, arrived=0` give 0/12 — the canonical "measured
    and failed". A dead cell is undrawable in BOTH modes; 2.5's
    "a dead wording is never drawn" rests on this helper, and
    6.1's "never from dead wordings" is the same rule, named
    once.
    """
    assert judged >= 10 and arrived * 10 < judged * 8, (
        "a dead cell has judged >= 10 and arrived*10 < judged*8, not any other state"
    )
    db.run(
        "INSERT INTO cell (camera_wording, act_wording, framing_wording, "
        "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
        camera, act, framing, manner, checkpoint, judged, arrived,
    )


def test_an_unknown_cell_is_drawable_in_exploratory_mode(client, seeded):
    """The named 6.1 scenario: a trio whose cell is `unknown`
    (the measurement did not reach n=10) is refused in strict
    mode and accepted in exploratory mode. The one-shot endpoint
    branches on `mode`: strict refuses with a 422 naming the
    state and suggesting the wider mode; exploratory queues the
    shot, the cell stays `unknown` until 6.2 lands a verdict
    on it.

    The cell is seeded explicitly with `judged=0, arrived=0`
    (the canonical "never measured" state). A test against the
    shipped `EVIDENCE_SEED` would not exercise the unknown
    branch — the seed has no row that lands as `unknown` for
    the directed/finepornV4 the seeded fixture uses — and a
    green-bar test on the seed would pass on a hand-coded
    "if state == 'unknown': refuse" that never fires.

    Two halves: strict refuses, exploratory draws. The
    loop-closed `n_shots` is the proof the strict check
    ran (a bypass would queue the strict call and the
    second `n_shots` would read 1 too).
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "exploratory unknown",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]
    _seed_unknown_trio("front-direct", "astride", "full-length",
                        manner="directed", checkpoint="finepornV4")

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}

    # Strict refuses: the cell is unknown, strict only accepts
    # verified. The 422 names the cell and the state, and
    # suggests exploratory.
    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
        "mode": "strict",
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "unknown" in detail, f"state not named in 422: {detail!r}"
    assert "exploratory" in detail, f"exploratory hint missing: {detail!r}"
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, f"strict refused call queued a shot: {n} rows for session {sid}"

    # Exploratory draws: the same trio, same cell, same data,
    # but the wider mode is now legal. The shot is queued.
    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
        "mode": "exploratory",
    })
    assert r.status_code == 200, r.text
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 1, f"exploratory call did not queue a shot: {n} rows for session {sid}"


def test_a_dead_cell_is_undrawable_in_both_modes(client, seeded):
    """The named 2.5 / 6.1 scenario: a dead wording is never
    drawn, in either mode. The 0/12 measurement is a result, not
    a gap, and "let me draw it anyway" would contradict the
    measurement the cell table is asking the operator to honour.
    Strict refuses (3.2 already pins this); exploratory refuses
    too, with the same cell-naming message — the 422 names the
    cell and the state `dead`, and does not suggest a wider
    mode because exploratory IS the wider mode and the refusal
    stands.

    The cell is seeded explicitly with `judged=12, arrived=0`
    (0/12, the canonical "measured and failed"). A test against
    the shipped `EVIDENCE_SEED` could exercise the dead branch
    (the seed has `back` 12/0 on both checkpoints), but the
    test uses its own cell so a future change to the seed
    does not silently retire the test.

    Two halves: strict refuses with "is dead, not verified",
    exploratory refuses with "is dead, not drawable in any
    mode". The loop-closed `n_shots == 0` is the same
    proof on both halves — a regression that swapped the
    branches (a "dead is drawable in exploratory" bug)
    would queue the exploratory call and `n_shots` would
    read 1.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "dead in both modes",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]
    _seed_dead_trio("back-camera", "back", "full-length",
                    manner="directed", checkpoint="finepornV4")

    camera = {"key": "back-camera",
              "wordings": [{"key": "back-camera", "text": "Taken from behind her"}]}
    act = {"key": "back", "wordings": [{"key": "back", "text": "back text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}

    # Strict refuses. The message names the state and the
    # cell, and stops at "not verified" — it does NOT
    # suggest exploratory, because a dead cell is also
    # refused in exploratory mode, and suggesting the wider
    # mode would be a lie the operator would discover on
    # retry.
    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
        "mode": "strict",
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "dead" in detail, f"state not named in 422: {detail!r}"
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, f"strict call queued a shot: {n} rows for session {sid}"

    # Exploratory refuses too. The message still names the
    # state; the wider mode is not a way through.
    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
        "mode": "exploratory",
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "dead" in detail, f"state not named in 422: {detail!r}"
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, f"exploratory call queued a shot: {n} rows for session {sid}"


def test_an_exploratory_run_draws_from_unknown_cells_and_refuses_dead(client, seeded):
    """The run-level shape of the 6.1 scenario: the
    `/api/sessions/{sid}/compose-run` endpoint accepts `mode`
    on its payload, and the pool is the mode-dependent set of
    trios. With a pool of one unknown and one dead trio, the
    unknown is drawable in exploratory and the dead is not, in
    either mode.

    The two halves:

    1. Exploratory: ask for 1, the unknown is in the pool,
       the dead is not, the run queues 1 shot.
    2. Strict: ask for 1, neither unknown nor dead is in the
       strict pool, the pool-too-small refusal fires. The
       message names the slot, the pool count, the largest
       fillable (0 of 1), and the wider mode (the same
       suggestion the one-shot endpoint carries).

    A test that only exercised the strict half would pass on
    a regression that silently dropped the `mode` field (the
    default `"strict"` would still run). A test that only
    exercised the exploratory half would pass on a regression
    that always treated the pool as exploratory. The two
    halves together pin the mode branch and the dead-excluded
    branch.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "exploratory run",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]
    _seed_unknown_trio("cam-unknown", "act-unknown", "frame-unknown",
                        manner="directed", checkpoint="finepornV4")
    _seed_dead_trio("cam-dead", "act-dead", "frame-dead",
                    manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_candidate("cam-unknown", "unknown camera text"),
                    _candidate("cam-dead", "dead camera text")],
        "act":     [_candidate("act-unknown", "unknown act text"),
                    _candidate("act-dead", "dead act text")],
        "framing": [_candidate("frame-unknown", "unknown framing text"),
                    _candidate("frame-dead", "dead framing text")],
    }

    # Exploratory: the unknown trio is in the pool, the dead
    # is not, count=1 fits. The run queues 1 shot.
    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 1, "candidates": candidates, "mode": "exploratory",
    })
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 1, f"exploratory run did not queue: {n} rows for session {sid}"

    # Strict: neither trio is in the pool, the run refuses
    # with the pool-too-small message. The wider-mode
    # suggestion is the only path the operator has to
    # actually draw on this pool.
    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 1, "candidates": candidates, "mode": "strict",
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "largest fillable" in detail, f"phrase missing: {detail!r}"
    assert "exploratory" in detail, f"exploratory hint missing: {detail!r}"
    n_after = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n_after == 1, (
        f"refused run queued shots: shot table has {n_after} rows for session {sid}, "
        f"expected 1 (the exploratory run only)"
    )


def test_an_exploratory_session_compose_inherits_the_mode_and_spreads(client, seeded):
    """The session-level shape of the 6.1 scenario:
    `/api/sessions/{sid}/compose-session` accepts `mode` and
    builds the same mode-dependent pool the run-level does.
    With a pool of 2 unknown trios (one front, one shoulder)
    and `count=2`, the run queues 2 shots and the spread
    reorder places them so no two consecutive photographs
    share a family.

    The pool is 2 unknown trios with all components distinct
    so the no-component-repeat greedy can draw 2, and the
    families are different so the spread's `ceil(2/2)=1`
    bound is satisfied trivially. Strict would refuse the
    same payload (the pool is empty in strict mode);
    exploratory queues 2 shots and the spread's loop-closed
    assertion (no two adjacent families) holds.

    A regression that ignored `mode` on the session-level
    payload would default to strict and refuse — the strict
    422 is the control the test reads against. A regression
    that let the spread skip a check would put the two
    families adjacent in some order; the loop-closed
    adjacency assertion catches that.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "exploratory session",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]
    _seed_unknown_trio("cam-front", "act-a", "frame-a",
                        manner="directed", checkpoint="finepornV4")
    _seed_unknown_trio("cam-shoulder", "act-b", "frame-b",
                        manner="directed", checkpoint="finepornV4")

    candidates = {
        "camera":  [_family_candidate("cam-front", "front text", "front"),
                    _family_candidate("cam-shoulder", "shoulder text", "shoulder")],
        "act":     [_candidate("act-a", "act-a text"),
                    _candidate("act-b", "act-b text")],
        "framing": [_candidate("frame-a", "frame-a text"),
                    _candidate("frame-b", "frame-b text")],
    }

    # Strict: the pool is empty (no verified trios), the
    # run refuses with the pool-too-small message. This is
    # the control the test reads against — the same payload
    # with `mode=strict` cannot draw, and the refusal
    # names the wider mode as the path through.
    r = client.post(f"/api/sessions/{sid}/compose-session", json={
        "count": 2, "candidates": candidates, "mode": "strict",
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "largest fillable" in detail, f"phrase missing: {detail!r}"
    assert "exploratory" in detail, f"exploratory hint missing: {detail!r}"
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, f"strict refused call queued shots: {n} rows for session {sid}"

    # Exploratory: the unknown trios are in the pool, the
    # run queues 2 shots, the spread places them so the two
    # families are not adjacent.
    r = client.post(f"/api/sessions/{sid}/compose-session", json={
        "count": 2, "candidates": candidates, "mode": "exploratory",
    })
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 2, f"exploratory session did not queue 2: {n} rows for session {sid}"

    # The spread property on the queued run: read the new
    # shots back, look up the family, assert no two
    # adjacent share it. The pool had 2 distinct families
    # (`front`, `shoulder`) and the reorder must place
    # them so neither is "next to itself" — trivially
    # satisfied with 2 trios from 2 families, but the
    # assertion is the loop-closed property 3.5 inherits
    # here, the same shape 3.5's own test pins.
    rows = db.q("SELECT id, shot_index, components FROM shot "
                "WHERE session_id=? ORDER BY shot_index, id", sid)
    family_lookup = {"cam-front": "front", "cam-shoulder": "shoulder"}
    families = []
    for row in rows:
        comps = db.jload(row, "components")["components"]
        cam_key = comps["camera"]["wording"]
        families.append(family_lookup[cam_key])
    for k in range(len(families) - 1):
        assert families[k] != families[k + 1], (
            f"adjacent shot {k} and {k+1} share family {families[k]!r}; "
            f"the 3.5 reorder did not spread the camera family"
        )


def test_a_clone_of_a_composed_session_preserves_components_and_origin(client, seeded):
    """The clone bug, in scope for 3.6 (the spec scenario names
    the comparison the clone enables).

    Before the fix, `clone_session` did an INSERT that did
    not name the `components` column, so every cloned shot
    was born with the schema's empty default `'{}'` and
    read as written. A composed session cloned that way
    becomes a written session, and 6.2 has no trio to
    count the reshoot toward. The fix adds `components` to
    the INSERT's column list and VALUES, and stamps the
    clone's session origin with the source's value.

    The test composes two shots with DISTINCT trios (so the
    JSONs are not equal — a bug that copies only the first
    shot's components would still fail this), clones the
    session, reads the clone back, and asserts the
    per-shot components JSONs match the source's and the
    clone's `origin` is `'composed'`.

    Failure modes (verified against the pre-fix code, which
    leaves `components` out of the INSERT entirely):
    - Every cloned shot's `components` is `{}`, the JSON
      equality check fails. This is the bug the test pins.
    - The clone's session origin is `''` (the helper is
      not run on clone): the explicit `'composed'` check
      fails. This is a separate failure mode the test
      pins so a "let me skip the origin write on clone"
      regression is caught.
    - The clone copies only the first shot's components
      onto every row: the second cloned shot's JSON does
      not match the source's second shot's, the per-row
      equality check fails.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "composed source",
        "look": "white summer dress, hair down, on a beach",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 10, 8)
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "overhead", "astride", "full-length", "directed", "finepornV4", 10, 8)

    cam_a = {"key": "front-direct",
             "wordings": [{"key": "front-direct", "text": "Taken from directly in front of her"}]}
    cam_b = {"key": "overhead",
             "wordings": [{"key": "overhead", "text": "Camera looking straight down at her"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "She is astride him."}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "a full-length photograph, head to feet"}]}

    client.post(f"/api/sessions/{sid}/compose", json={"camera": cam_a, "act": act, "framing": framing})
    client.post(f"/api/sessions/{sid}/compose", json={"camera": cam_b, "act": act, "framing": framing})

    clone_id = client.post(f"/api/sessions/{sid}/clone", json={"name": "composed clone"}).json()["id"]

    src_shots = {s["id"]: s for s in client.get(f"/api/sessions/{sid}").json()["shots"]}
    clone_shots = {s["id"]: s for s in client.get(f"/api/sessions/{clone_id}").json()["shots"]}

    # The source has two shots with distinct component JSONs;
    # the clone has the same two shots, and every clone row
    # carries the same components JSON as its source row.
    # The mapping uses `shot_index` to pair them, because
    # clone rows are a fresh insert with their own ids.
    by_index_src = {s["shot_index"]: s for s in src_shots.values()}
    by_index_clone = {s["shot_index"]: s for s in clone_shots.values()}
    assert sorted(by_index_src) == sorted(by_index_clone), (
        f"clone did not preserve shot indices: "
        f"src={sorted(by_index_src)} clone={sorted(by_index_clone)}"
    )
    for idx in by_index_src:
        src_row = db.one("SELECT components FROM shot WHERE id=?", by_index_src[idx]["id"])
        clone_row = db.one("SELECT components FROM shot WHERE id=?", by_index_clone[idx]["id"])
        db.jload(src_row, "components")
        db.jload(clone_row, "components")
        assert clone_row["components"] == src_row["components"], (
            f"clone's shot at index {idx} lost its components: "
            f"src={src_row['components']!r} clone={clone_row['components']!r}"
        )
        # The source's JSON is non-empty (composed); a clone
        # that loses the components lands as `{}`, which is
        # the marker for a written shot. The explicit check
        # names the bug: a clone of a composed session is a
        # composed session, not a written one.
        assert clone_row["components"] != {}, (
            f"clone's shot at index {idx} has empty components — "
            f"the clone lost the trio 3.6 exists to preserve"
        )

    assert client.get(f"/api/sessions/{clone_id}").json()["origin"] == "composed", (
        f"clone of a composed session must read as 'composed', "
        f"got {client.get(f'/api/sessions/{clone_id}').json()['origin']!r}"
    )


# -- 6.2: a judged exploratory photograph counts toward its cell, and the
# cell flips to verified or dead on reaching the n=10 threshold. The 6.1
# end-of-task note names this as the task that opens the bookkeeping: the
# exploratory shot has a `components` JSON, and the cell is a function of
# that trio. The 5.2 judging screen will be what builds the verdict; 6.2
# is the path it lands on, not the screen itself.


def _wording_split_candidate(concept_key: str, wording_key: str, text: str) -> dict:
    """A catalogue entry whose `concept` and `wording` keys differ.

    Every concept in the catalogue today has a single wording whose key
    equals the concept key (1.1's reshape). A future "let me add a
    second wording" lands here as a different `wording` value while
    `concept` stays put, and the cell the photograph counts toward is
    keyed on the wording (3.1's explicit decision, repeated in
    `compose_and_queue_shot`). A test that uses `_candidate` (concept
    key == wording key) would not see a bug that reads `concept`
    instead of `wording`, because the two values coincide. The
    wording-vs-concept test below uses this helper to plant a shot
    whose trio carries a wording key that does not match the concept
    key, then asserts the cell the judgement lands on is the wording
    one. A "let me use `comps[slot]['concept']`" bug would create a
    row with the wrong five-tuple and the test would read it.
    """
    return {"key": concept_key, "wordings": [{"key": wording_key, "text": text}]}


def _composed_shot_in_session(client, seeded, *, manner: str, checkpoint: str,
                              camera: dict, act: dict, framing: dict,
                              session_name: str = "judge test",
                              seed_cell: tuple | None = None) -> int:
    """Create a session, pre-seed an optional cell, compose one shot,
    return the shot id. The composing endpoint is the public path
    6.1 already shipped — the test is the 6.2 layer on top of it, not
    a parallel composing path.

    `seed_cell`, when given, is the (judged, arrived) the test wants
    the cell to start at. A cell with no row is `unknown` (judged=0,
    arrived=0) and a fresh UPSERT will create it; a pre-seeded cell
    gives the test a known starting point for the flip assertions.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": session_name,
        "manner": manner, "checkpoint": checkpoint, "shots": [],
    }).json()["id"]
    if seed_cell is not None:
        # The trio is whatever the caller passed in; the cell
        # matches on (camera_wording, act_wording, framing_wording,
        # manner, checkpoint) and the test reads the same five
        # values back through `shot.components`.
        cam_w = camera["wordings"][0]["key"]
        act_w = act["wordings"][0]["key"]
        framing_w = framing["wordings"][0]["key"]
        db.run(
            "INSERT INTO cell (camera_wording, act_wording, framing_wording, "
            "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
            cam_w, act_w, framing_w, manner, checkpoint,
            seed_cell[0], seed_cell[1],
        )
    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
        "mode": "exploratory",
    })
    assert r.status_code == 200, r.text
    return r.json()["ids"][0]


def test_a_judged_exploratory_photograph_counts_toward_its_cell(client, seeded):
    """The named 6.2 scenario at the one-shot level: a composed
    shot from an unmeasured trio (the 6.1 exploratory draw) is
    judged, the cell is created, and its (judged, arrived) carry
    the per-slot delta. The response carries the new state so
    the operator sees the flip when the threshold is crossed.

    The shot is the one 6.1 already drew, the cell is the one
    `_trio_pool` already uses, and the function that turns the
    counts into a state is `db.cell_state` — three single
    sources of truth, and a future "let me write the state
    myself" bug has nothing to land on.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "judge creates the cell",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
        "mode": "exploratory",
    })
    assert r.status_code == 200, r.text
    shot_id = r.json()["ids"][0]

    # No cell yet: the trio was unmeasured, exploratory drew it,
    # and the judgement is what creates the row. The judge
    # answers all three slots correctly, so the cell lands at
    # (3, 3) — still unknown (3 < 10) but the bookkeeping is in
    # place.
    r = client.post(f"/api/shots/{shot_id}/judge", json={
        "camera": "front-direct", "act": "astride", "framing": "full-length",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cell"] == ["front-direct", "astride", "full-length",
                            "directed", "finepornV4"]
    assert body["judged"] == 1
    assert body["arrived"] == 1
    assert body["state"] == "unknown"

    # The row exists with the right counts, in the cell table
    # the spec says is the only home for "is this trio
    # drawable" (2.1). A "let me write the counts to a
    # different table" bug would skip this read.
    cell = db.one(
        "SELECT judged, arrived FROM cell "
        "WHERE camera_wording=? AND act_wording=? AND framing_wording=? "
        "AND manner=? AND checkpoint=?",
        "front-direct", "astride", "full-length", "directed", "finepornV4",
    )
    assert cell == {"judged": 1, "arrived": 1}

    # The verdicts column on the shot carries the answers, so a
    # re-judge (which the test below covers) is a 409 rather
    # than a double-count.
    shot = db.one("SELECT verdicts FROM shot WHERE id=?", shot_id)
    import json as _json
    assert _json.loads(shot["verdicts"]) == {
        "camera": "front-direct", "act": "astride", "framing": "full-length",
    }


def test_the_judged_cell_uses_the_wording_key_not_the_concept_key(client, seeded):
    """The cell is keyed on the three WORDING keys, not on the
    concept keys. `components` carries both, and a future "let
    me add a second wording" lands here as a different
    `wording` value while `concept` stays put. A code change
    that reads `comps[slot]['concept']` instead of
    `comps[slot]['wording']` would UPSERT a row on the wrong
    five-tuple and the cell the judgement actually belongs to
    would stay at zero counts.

    The fixture plants a shot whose camera's `concept` and
    `wording` keys differ (`cam-concept` vs `cam-wording`),
    composes it through the public endpoint, judges it, and
    reads the cell. The cell must be at the wording key, not
    at the concept key.

    Verified by breaking the code: replacing
    `comps[slot]["wording"]` with `comps[slot]["concept"]`
    in the judge endpoint makes the cell row land on
    (`cam-concept`, `act-concept`, `frame-concept`, ...) and
    leave the wording row empty, and the test fails on the
    `cell` lookup with no row found.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "wording vs concept",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    # concept key `cam-concept`, wording key `cam-wording` —
    # the two are different on purpose. The cell the judgement
    # lands on must be keyed on `cam-wording`, not on
    # `cam-concept`. The act and framing keep the keys
    # identical so the test reads the failure cleanly: the
    # camera axis is the one whose key shape changes.
    camera = _wording_split_candidate("cam-concept", "cam-wording", "front text")
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}

    shot_id = _composed_shot_in_session(
        client, seeded,
        manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing,
        session_name="wording vs concept",
    )

    r = client.post(f"/api/shots/{shot_id}/judge", json={
        "camera": "cam-wording", "act": "astride", "framing": "full-length",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # The cell tuple is the wording key, not the concept key.
    assert body["cell"][0] == "cam-wording", (
        f"judgement landed on concept key {body['cell'][0]!r}, not the wording key"
    )
    assert body["cell"][0] != "cam-concept"

    # The cell row exists at the wording key, and the concept
    # key has no row. A "let me use concept" bug creates a
    # row at the concept key and the test fails on the
    # `WHERE camera_wording='cam-wording'` lookup.
    wording_cell = db.one(
        "SELECT judged, arrived FROM cell WHERE camera_wording=?",
        "cam-wording",
    )
    assert wording_cell == {"judged": 1, "arrived": 1}
    concept_cell = db.one(
        "SELECT judged, arrived FROM cell WHERE camera_wording=?",
        "cam-concept",
    )
    assert concept_cell is None, (
        f"judgement also created a row at the concept key {concept_cell!r}; "
        f"the cell is on the wording, not the concept"
    )


def test_a_correct_answer_increments_arrived_a_wrong_answer_only_judged(client, seeded):
    """`arrived` means the act the line asked for is the act in
    the frame, not that the photograph is good. The spec
    scenario `A wrong answer is kept` is the same fact: a
    judge who picks a different catalogue key records a miss
    on the cell, judged+1 arrived+0, and the wrong key is
    preserved in `verdicts` for the operator to see.

    The test plants three shots on the same trio, judges each
    with a different per-slot pattern, and reads the cell
    after every judgement to verify the deltas are
    independent and add up.
    """
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}

    # Three shots on the same trio. The cell is unknown at
    # n=0; one photograph is one `judged`, so the deltas carry
    # it through 0 -> 1 -> 2 -> 3 however many slots each pass
    # answers.
    shot_ids = [
        _composed_shot_in_session(
            client, seeded, manner="directed", checkpoint="finepornV4",
            camera=camera, act=act, framing=framing,
            session_name=f"arrived {i}",
        ) for i in range(3)
    ]

    # Shot 0: all three correct. One photograph, and every
    # answered slot arrived: judged+=1, arrived+=1.
    r = client.post(f"/api/shots/{shot_ids[0]}/judge", json={
        "camera": "front-direct", "act": "astride", "framing": "full-length",
    })
    assert r.status_code == 200, r.text
    assert r.json()["judged"] == 1 and r.json()["arrived"] == 1
    assert r.json()["state"] == "unknown"

    # Shot 1: camera wrong (a different catalogue key), act
    # "none or cannot tell" (empty string), framing correct.
    # `arrived` is a property of the PHOTOGRAPH: it arrived only
    # if every slot answered is the one the line asked for, and
    # two of these three are misses -> judged+=1, arrived+=0.
    r = client.post(f"/api/shots/{shot_ids[1]}/judge", json={
        "camera": "overhead-direct", "act": "", "framing": "full-length",
    })
    assert r.status_code == 200, r.text
    assert r.json()["judged"] == 2
    assert r.json()["arrived"] == 1
    assert r.json()["state"] == "unknown"

    # Shot 2: act correct, the other two unanswered. One
    # photograph, and the only slot asked arrived: +1 judged,
    # +1 arrived. A slot nobody asked about cannot make the
    # photograph a miss.
    r = client.post(f"/api/shots/{shot_ids[2]}/judge", json={
        "act": "astride",
    })
    assert r.status_code == 200, r.text
    assert r.json()["judged"] == 3
    assert r.json()["arrived"] == 2
    assert r.json()["state"] == "unknown"

    # The cell row carries the totals. A "let me add
    # arrived and judged separately" bug would land
    # different numbers here.
    cell = db.one(
        "SELECT judged, arrived FROM cell "
        "WHERE camera_wording=? AND act_wording=? AND framing_wording=? "
        "AND manner=? AND checkpoint=?",
        "front-direct", "astride", "full-length", "directed", "finepornV4",
    )
    assert cell == {"judged": 3, "arrived": 2}

    # The verdicts on shot 1 keep the wrong camera key and
    # the empty act answer. The operator can see what was
    # picked, the cell just got the counts.
    import json as _json
    verdicts = _json.loads(db.one("SELECT verdicts FROM shot WHERE id=?",
                                  shot_ids[1])["verdicts"])
    assert verdicts == {"camera": "overhead-direct", "act": "", "framing": "full-length"}


def test_a_judged_cell_flips_to_verified_on_reaching_the_threshold(client, seeded):
    """The 9 -> 10 boundary in the positive direction. The cell
    is unknown at 9 judged, the tenth judgement is a pass on
    the act, and the cell flips to verified.

    `db.cell_state` is the only definition of verified/dead/
    unknown: at 10/9, 9*10=90 >= 10*8=80, so `verified`. A
    future "let me also accept 7 of 10" bug is the second
    calculation 6.2 explicitly names, and the test pins the
    8/10 ratio.
    """
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}

    # The cell starts at (9, 8): 9 judged, 8 arrived. Under
    # `db.cell_state`, judged < 10, so the state is `unknown`
    # whatever the ratio. The 10th judgement is the flip.
    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing,
        session_name="flip to verified",
        seed_cell=(9, 8),
    )

    r = client.post(f"/api/shots/{shot_id}/judge", json={
        "act": "astride",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # The act slot answers the question correctly: +1 judged,
    # +1 arrived -> 10/9.
    assert body["judged"] == 10
    assert body["arrived"] == 9
    # 9*10=90 >= 10*8=80 -> verified. The cell flipped.
    assert body["state"] == "verified", (
        f"cell did not flip to verified at 10/9: state={body['state']!r}"
    )

    cell = db.one(
        "SELECT judged, arrived FROM cell "
        "WHERE camera_wording=? AND act_wording=? AND framing_wording=? "
        "AND manner=? AND checkpoint=?",
        "front-direct", "astride", "full-length", "directed", "finepornV4",
    )
    assert cell == {"judged": 10, "arrived": 9}


def test_a_judged_cell_flips_to_dead_on_reaching_the_threshold(client, seeded):
    """The 9 -> 10 boundary in the negative direction. The cell
    is unknown at 9 judged, the tenth judgement is a fail on
    the act (a wrong catalogue key), and the cell flips to
    dead.

    At 10/8, 8*10=80 >= 10*8=80 -> verified (the boundary
    is inclusive at the ratio). The test uses 9/7 so the
    tenth judgement — a miss — moves to 10/7, which is
    70 < 80 -> dead. The starting point is what makes
    this the "flip to dead" half; 9/8 + miss would land at
    10/8 = verified, the same shape as the verified test.
    """
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}

    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing,
        session_name="flip to dead",
        seed_cell=(9, 7),
    )

    # The tenth judgement answers the act slot with a
    # different catalogue key — a miss. Per-slot: +1 judged,
    # +0 arrived -> 10/7.
    r = client.post(f"/api/shots/{shot_id}/judge", json={
        "act": "wall",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["judged"] == 10
    assert body["arrived"] == 7
    # 7*10=70 < 10*8=80 -> dead. The cell flipped the other way.
    assert body["state"] == "dead", (
        f"cell did not flip to dead at 10/7: state={body['state']!r}"
    )


def test_nine_judged_still_reads_as_unknown(client, seeded):
    """The other side of the same boundary: at 9 judged the
    cell is `unknown` whatever the ratio. The 9/9
    hypothetical and the 9/0 hypothetical are both
    `unknown`, and a regression that landed either as
    `verified` or `dead` (a "let me also accept 9 of 10"
    bug) would read the cell_state rule wrong.

    Pre-seeds the cell at (9, 9) — the most-likely shape a
    9/9 surface would take — and reads it back through
    `db.cell_state` rather than the endpoint. The endpoint
    is the 9->10 path, not the 9 alone path, and the
    state at 9 is what the spec calls `unknown`.
    """
    db.run(
        "INSERT INTO cell (camera_wording, act_wording, framing_wording, "
        "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
        "front-direct", "astride", "full-length", "directed", "finepornV4", 9, 9,
    )
    cell = db.one(
        "SELECT judged, arrived FROM cell "
        "WHERE camera_wording=? AND act_wording=? AND framing_wording=? "
        "AND manner=? AND checkpoint=?",
        "front-direct", "astride", "full-length", "directed", "finepornV4",
    )
    assert db.cell_state(cell["judged"], cell["arrived"]) == "unknown"


def test_judging_a_written_shot_is_refused(client, seeded):
    """A written shot has no trio (its `components` is the
    empty default `'{}'`), and the cell is keyed on the
    trio. Counting the shot's rating instead would conflate
    photo quality with the act the line asked for — the
    design note at the top of the file: `arrived` means
    the act the line asked for is the act in the frame, not
    that the photograph is good. The endpoint refuses
    rather than silently counting a rating.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "written shot",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [{"prompt": "standing", "count": 1}],
    }).json()["id"]
    shot_id = client.get(f"/api/sessions/{sid}").json()["shots"][0]["id"]
    # The written row's components are the empty default
    # `'{}'`, which is the marker the test reads.
    assert db.one("SELECT components FROM shot WHERE id=?", shot_id)["components"] == "{}"

    r = client.post(f"/api/shots/{shot_id}/judge", json={
        "act": "astride",
    })
    assert r.status_code == 422, r.text
    assert "no components" in r.json()["detail"]

    # No cell was created: the refusal ran before the
    # UPSERT. A code change that dropped the components
    # check would have created a row at the (None, None,
    # None, ...) five-tuple (which the cell's own NOT NULL
    # on the trio would then reject) or, worse, at
    # ('', '', '', '', '') — a silent injection into the
    # table. The test pins the refusal.
    n = db.one("SELECT COUNT(*) AS n FROM cell")["n"]
    assert n == 0, (
        f"judge created a cell for a written shot: cell table has {n} rows"
    )


def test_judging_a_session_missing_manner_or_checkpoint_is_refused(client, seeded):
    """The cell is keyed on (trio, manner, checkpoint). A
    session with no manner or no checkpoint cannot match
    any cell, and "no cell matches" is a different shape
    from "the cell is unknown" — the former is a
    session-level problem (the operator forgot to declare
    the dimension), the latter is a request-level one
    (the trio is unmeasured). The endpoint refuses
    before the UPSERT, naming what is missing.

    The pre-check is the same one 3.2 / 3.3 already pin
    on their 422s; the test reuses the same refusal
    shape so a "let me unify the refusals" refactor
    fails it loudly if it drops the missing-dimension
    branch.

    The test only checks `manner`: `checkpoint` is
    auto-derived from the model's workflow at create
    time (the same hook 3.2 closed the door on), so a
    freshly created session on the seeded fixture has a
    `checkpoint` already. The missing-manner case is the
    only one a test can build without bypassing the
    create-time derivation, and the test pins the same
    shape 3.2's missing-dimensions test pins: a 422
    that names the missing dimension.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "missing manner",
        "shots": [],
    }).json()["id"]
    # The session was created without a `manner` field.
    # The model's workflow auto-derived `checkpoint` to
    # `base.safetensors` (the seeded GRAPH's loader), so
    # only `manner` is missing. The 422 names what is
    # missing rather than silently finding zero cells
    # and reading as "the cell is unknown" — the
    # silent-substitution trap the user named.
    db.run(
        "INSERT INTO shot (session_id, shot_index, shot_label, prompt, "
        "components, created_at) VALUES (?, 0, 'c', 'p', ?, ?)",
        sid,
        '{"camera":{"concept":"x","wording":"x"},'
        '"act":{"concept":"y","wording":"y"},'
        '"framing":{"concept":"z","wording":"z"}}',
        db.now(),
    )
    shot_id = db.one("SELECT id FROM shot WHERE session_id=? ORDER BY id DESC LIMIT 1",
                     sid)["id"]

    r = client.post(f"/api/shots/{shot_id}/judge", json={"act": "y"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    # The message names the missing dimension. The
    # sentence is shaped so the operator sees what
    # to set without having to read the code.
    assert "manner" in detail, (
        f"422 does not name the missing dimension: {detail!r}"
    )
    assert "set them on the session" in detail, (
        f"422 does not say what to do: {detail!r}"
    )

    # No cell was created.
    n = db.one("SELECT COUNT(*) AS n FROM cell")["n"]
    assert n == 0, (
        f"judge on a session missing dimensions created a cell: {n} rows"
    )


def test_judging_the_same_shot_twice_is_refused_at_409(client, seeded):
    """The idempotence marker is the `verdicts` column on the
    shot: a non-empty value means a judge already answered,
    the second call is a 409, and the cell counts do not
    change.

    A regression that drops the column check surfaces
    two ways: the second UPSERT adds another increment
    (the silent double-count) or the cell's CHECK
    `arrived BETWEEN 0 AND judged` rejects the write
    (the noisy double-count). The first is the failure
    6.2 names; the test pins the first.
    """
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}
    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing,
        session_name="judge twice",
    )

    # First judgement: the cell is created at (3, 3).
    r = client.post(f"/api/shots/{shot_id}/judge", json={
        "act": "astride",
    })
    assert r.status_code == 200, r.text
    assert r.json()["judged"] == 1 and r.json()["arrived"] == 1

    # Second judgement on the same shot: refused at 409.
    r = client.post(f"/api/shots/{shot_id}/judge", json={
        "act": "astride",
    })
    assert r.status_code == 409, r.text
    assert "already has an answer" in r.json()["detail"]

    # The cell count is unchanged: still (1, 1), not (2, 2).
    # A code change that dropped the column check would
    # have arrived at (2, 2), and the test reads it.
    cell = db.one(
        "SELECT judged, arrived FROM cell "
        "WHERE camera_wording=? AND act_wording=? AND framing_wording=? "
        "AND manner=? AND checkpoint=?",
        "front-direct", "astride", "full-length", "directed", "finepornV4",
    )
    assert cell == {"judged": 1, "arrived": 1}, (
        f"second judgement double-counted: cell is {dict(cell)!r}, "
        f"expected (1, 1)"
    )


def test_judging_with_no_answers_is_refused(client, seeded):
    """A pass that asks nothing measures nothing. The endpoint
    refuses at 422 rather than returning 200 with no
    cell update, the same shape `reshoot-below` already
    pins on its 400 — a click that "did nothing" never
    goes through.
    """
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}
    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing,
        session_name="empty pass",
    )

    r = client.post(f"/api/shots/{shot_id}/judge", json={})
    assert r.status_code == 422, r.text
    assert "at least one slot" in r.json()["detail"]

    # No cell was created. The refusal ran before the
    # UPSERT, and a 200 with no work is the silent
    # no-op this test refuses.
    n = db.one("SELECT COUNT(*) AS n FROM cell")["n"]
    assert n == 0, (
        f"empty pass created a cell: cell table has {n} rows"
    )


def test_judging_three_slots_is_still_one_photograph(client, seeded):
    """A pass that answers all three slots is ONE photograph
    judged, not three. The spec counts photographs
    (specs/component-matrix/spec.md:47 and :70, and
    `db.cell_state` itself), and the seeded rows are
    photograph counts. Counting +1 per answered slot was the
    first shape of this endpoint: it reached `judged=3` on a
    single photograph, so a cell flipped to `verified` on four
    of them and the n=10 threshold quietly became n=4.

    `arrived` is the photograph's own property: it arrived only
    if every slot answered is the one the line asked for. Here
    the camera is a different catalogue key, so the photograph
    is a miss whatever the other two say.
    """
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}
    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing,
        session_name="three slots",
    )

    # Two of three correct: camera wrong, act + framing
    # correct -> one photograph judged, and it did not arrive.
    r = client.post(f"/api/shots/{shot_id}/judge", json={
        "camera": "overhead-direct",
        "act": "astride",
        "framing": "full-length",
    })
    assert r.status_code == 200, r.text
    assert r.json()["judged"] == 1
    assert r.json()["arrived"] == 0


def test_a_second_pass_answers_a_new_slot_without_counting_the_photo_again(client, seeded):
    """5.2 asks ONE question per pass over a whole batch, so a
    photograph is judged for its camera on one pass and for its act
    on another. The first shape of this endpoint refused the second
    pass at 409 (the marker was per SHOT), so a photograph could
    never be measured on more than one slot:

        pass 1 (camera only) -> 200  judged=1 arrived=1
        pass 2 (act only)    -> 409  "has already been judged"

    The marker is per SLOT now. The second pass is accepted, the
    photograph is still ONE `judged`, and the answers accumulate on
    the row. Re-answering a slot that already has an answer is what
    stays refused — 5.3's "a disagreement does not overwrite the
    stored verdict" is the same rule.
    """
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}
    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing, session_name="two passes",
    )

    r = client.post(f"/api/shots/{shot_id}/judge", json={"camera": "front-direct"})
    assert r.status_code == 200, r.text
    assert (r.json()["judged"], r.json()["arrived"]) == (1, 1)

    # A different slot on the same photograph: accepted, and the
    # photograph is not counted a second time.
    r = client.post(f"/api/shots/{shot_id}/judge", json={"act": "astride"})
    assert r.status_code == 200, r.text
    assert (r.json()["judged"], r.json()["arrived"]) == (1, 1)

    # The same slot again: refused, and nothing moves.
    r = client.post(f"/api/shots/{shot_id}/judge", json={"camera": "overhead-direct"})
    assert r.status_code == 409, r.text
    assert "camera" in r.json()["detail"], r.json()["detail"]

    # Both answers are on the row; the second pass merged rather
    # than replaced.
    import json as _json
    verdicts = _json.loads(db.one("SELECT verdicts FROM shot WHERE id=?", shot_id)["verdicts"])
    assert verdicts == {"camera": "front-direct", "act": "astride"}, verdicts


def test_a_later_pass_that_misses_takes_the_photograph_out_of_arrived(client, seeded):
    """`arrived` is a property of the photograph, so a slot answered
    on a LATER pass can turn a hit into a miss. The photograph
    arrived on its camera; the act pass says "none or cannot tell";
    the photograph did not arrive after all and `arrived` goes back
    down. Without the recompute, `arrived` would be stuck at the
    value the first pass happened to produce and a cell could read
    10/10 while half its photographs missed on a slot asked later.
    """
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}
    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing, session_name="miss on pass two",
    )

    r = client.post(f"/api/shots/{shot_id}/judge", json={"camera": "front-direct"})
    assert (r.json()["judged"], r.json()["arrived"]) == (1, 1)

    r = client.post(f"/api/shots/{shot_id}/judge", json={"act": ""})
    assert r.status_code == 200, r.text
    assert (r.json()["judged"], r.json()["arrived"]) == (1, 0), r.json()

    # And never below zero: the cell's CHECK is `arrived BETWEEN 0
    # AND judged`, so a double subtraction would raise here rather
    # than store a negative count.
    cell = db.one(
        "SELECT judged, arrived FROM cell WHERE camera_wording=? AND act_wording=? "
        "AND framing_wording=? AND manner=? AND checkpoint=?",
        "front-direct", "astride", "full-length", "directed", "finepornV4",
    )
    assert cell == {"judged": 1, "arrived": 0}


def test_ten_photographs_judged_one_slot_each_reach_the_threshold(client, seeded):
    """The threshold in the unit the spec uses. Ten photographs, one
    question each — the shape 5.2 actually produces — and the cell
    flips on the tenth, not on the fourth.

    This test does NOT catch the +1-per-slot unit collision — one
    question per photograph makes the two rules identical, and it
    passes under both. `test_judging_three_slots_is_still_one_photograph`
    is the one that bites there (verified: it fails `assert 3 == 1`).
    What this one pins is the boundary itself in the shape 5.2 will
    call it: unknown through nine, verified on the tenth.
    """
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}

    seen = []
    for i in range(10):
        shot_id = _composed_shot_in_session(
            client, seeded, manner="directed", checkpoint="finepornV4",
            camera=camera, act=act, framing=framing, session_name=f"threshold {i}",
        )
        # Nine arrive, the tenth is a miss: 9 of 10 is above the
        # 8-in-10 ratio, so the cell lands `verified`.
        answer = "astride" if i < 9 else ""
        r = client.post(f"/api/shots/{shot_id}/judge", json={"act": answer})
        assert r.status_code == 200, r.text
        seen.append((r.json()["judged"], r.json()["state"]))

    # Unknown all the way to nine, whatever the ratio.
    assert [state for _, state in seen[:9]] == ["unknown"] * 9, seen
    assert [judged for judged, _ in seen] == list(range(1, 11)), seen
    assert seen[-1] == (10, "verified"), seen


# ----------------------------------------------------------------- 5.1 / 5.3 judging pass & control tests

def test_judge_pass_returns_only_shot_id_keys_and_exact_structure(client, seeded):
    """5.1 Decision A: GET /api/sessions/{sid}/judge-pass?slot=camera
    returns {"shots": [id, ...], "controls": [id, ...]}. Each entry
    in the lists is an integer ID only — no prompt, no components, no
    wording, no reference, and no label. The screen must not show what
    the photograph was composed from (spec.md:104-107).
    """
    camera = {"key": "front-direct", "wordings": [{"key": "front-direct", "text": "front"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride"}]}
    framing = {"key": "full-length", "wordings": [{"key": "full-length", "text": "full"}]}

    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "judge pass bare",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    shot_id = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    db.run("UPDATE shot SET status='done' WHERE id=?", shot_id)

    r = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera")
    assert r.status_code == 200, r.text
    data = r.json()

    # Exact top-level keys
    assert set(data.keys()) == {"shots", "controls"}
    assert data["shots"] == [shot_id]
    assert data["controls"] == []

    # Elements must be plain integers
    assert all(isinstance(x, int) for x in data["shots"])
    assert all(isinstance(x, int) for x in data["controls"])


def test_judge_pass_default_unjudged_shots_with_empty_verdicts(client, seeded):
    """The default for every shot that has never been judged is
    verdicts=''. It must be included in `shots` and excluded from
    `controls`.
    """
    camera = {"key": "front-direct", "wordings": [{"key": "front-direct", "text": "front"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride"}]}
    framing = {"key": "full-length", "wordings": [{"key": "full-length", "text": "full"}]}

    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "empty verdicts",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    s1 = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    s2 = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    db.run("UPDATE shot SET status='done' WHERE session_id=?", sid)

    # Verify database has verdicts=''
    for row in db.q("SELECT verdicts FROM shot WHERE session_id=?", sid):
        assert row["verdicts"] == ""

    r = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera")
    assert r.status_code == 200
    assert r.json() == {"shots": [s1, s2], "controls": []}


def test_judge_pass_categorizes_judged_shots_as_controls(client, seeded):
    """An already-judged shot for a slot moves to `controls` when that
    slot is queried, while remaining in `shots` for an unjudged slot.
    """
    camera = {"key": "front-direct", "wordings": [{"key": "front-direct", "text": "front"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride"}]}
    framing = {"key": "full-length", "wordings": [{"key": "full-length", "text": "full"}]}

    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "categorize controls",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    s1 = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    s2 = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    db.run("UPDATE shot SET status='done' WHERE session_id=?", sid)

    # Judge s1 on camera only
    client.post(f"/api/shots/{s1}/judge", json={"camera": "front-direct"})

    # Query slot=camera: s1 is control, s2 is unjudged shot
    r_cam = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera")
    assert r_cam.status_code == 200
    assert r_cam.json() == {"shots": [s2], "controls": [s1]}

    # Query slot=act: both are unjudged shots, neither is control
    r_act = client.get(f"/api/sessions/{sid}/judge-pass?slot=act")
    assert r_act.status_code == 200
    assert r_act.json() == {"shots": [s1, s2], "controls": []}


def test_judge_pass_never_leaks_shots_from_another_session(client, seeded):
    """A judging pass on session A must never return shots from session B."""
    camera = {"key": "front-direct", "wordings": [{"key": "front-direct", "text": "front"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride"}]}
    framing = {"key": "full-length", "wordings": [{"key": "full-length", "text": "full"}]}

    sid_a = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "session A",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]
    sid_b = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "session B",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    sa1 = client.post(f"/api/sessions/{sid_a}/compose", json={
        "camera": camera, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    sb1 = client.post(f"/api/sessions/{sid_b}/compose", json={
        "camera": camera, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    db.run("UPDATE shot SET status='done'")

    r = client.get(f"/api/sessions/{sid_a}/judge-pass?slot=camera")
    assert r.status_code == 200
    assert sb1 not in r.json()["shots"]
    assert sb1 not in r.json()["controls"]
    assert r.json() == {"shots": [sa1], "controls": []}


def test_judge_pass_excludes_written_rejected_and_non_done_shots(client, seeded):
    """Written shots (components='{}'), rejected shots, and pending shots
    are never returned for judging.
    """
    camera = {"key": "front-direct", "wordings": [{"key": "front-direct", "text": "front"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride"}]}
    framing = {"key": "full-length", "wordings": [{"key": "full-length", "text": "full"}]}

    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "filters pass",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [{"prompt": "written line", "count": 1}],
    }).json()["id"]

    # Composed shot 1 (done, not rejected) -> INCLUDED
    s1 = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    # Composed shot 2 (rejected) -> EXCLUDED
    s2 = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]
    # Composed shot 3 (pending) -> EXCLUDED
    s3 = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing, "mode": "exploratory",
    }).json()["ids"][0]

    db.run("UPDATE shot SET status='done' WHERE id IN (?, ?)", s1, s2)
    db.run("UPDATE shot SET rejected=1 WHERE id=?", s2)
    # The written shot is also done, but has components='{}'
    written_id = db.one("SELECT id FROM shot WHERE session_id=? AND components='{}'", sid)["id"]
    db.run("UPDATE shot SET status='done' WHERE id=?", written_id)

    r = client.get(f"/api/sessions/{sid}/judge-pass?slot=camera")
    assert r.status_code == 200
    assert r.json() == {"shots": [s1], "controls": []}


def test_judge_pass_refuses_framing_and_invalid_slots(client, seeded):
    """The framing slot returns 422, and the refusal says why.

    It used to say "no catalogue yet", which stopped being true when framing
    moved into the component store: there IS a catalogue, it holds one framing
    per manner, and a forced choice over a list of one is not a question.
    Unknown slots return 422. Nonexistent session returns 404.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "refusals",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    # framing slot refusal
    r_frame = client.get(f"/api/sessions/{sid}/judge-pass?slot=framing")
    assert r_frame.status_code == 422
    detail = r_frame.json()["detail"]
    assert "forced choice needs more than one per manner" in detail
    # The message counts what is actually in the store rather than asserting
    # the catalogue is empty.
    assert "no catalogue" not in detail

    # invalid slot
    r_inv = client.get(f"/api/sessions/{sid}/judge-pass?slot=nonexistent")
    assert r_inv.status_code == 422
    assert "invalid slot" in r_inv.json()["detail"]

    # 404 for missing session
    r_404 = client.get("/api/sessions/999999/judge-pass?slot=camera")
    assert r_404.status_code == 404


def test_judge_control_shot_agreement_does_not_modify_state(client, seeded):
    """5.3 Decision B: Re-presenting an already judged shot with control=True
    compares against the stored verdict and returns agreed=True.
    It writes NOTHING to the database (shot.verdicts and cell counts unchanged).
    """
    camera = {"key": "front-direct", "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length", "wordings": [{"key": "full-length", "text": "full-length text"}]}

    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing, session_name="control agree",
    )

    # Initial judgement: records verdict and cell
    r_init = client.post(f"/api/shots/{shot_id}/judge", json={"camera": "front-direct"})
    assert r_init.status_code == 200
    assert (r_init.json()["judged"], r_init.json()["arrived"]) == (1, 1)

    # Control call agreeing with stored verdict
    r_ctrl = client.post(f"/api/shots/{shot_id}/judge", json={
        "camera": "front-direct", "control": True,
    })
    assert r_ctrl.status_code == 200, r_ctrl.text
    assert r_ctrl.json() == {
        "control": True,
        "slot": "camera",
        "agreed": True,
        "stored": "front-direct",
        "answered": "front-direct",
    }

    # Verify cell counts did NOT change
    cell = db.one(
        "SELECT judged, arrived FROM cell WHERE camera_wording=? AND act_wording=? "
        "AND framing_wording=? AND manner=? AND checkpoint=?",
        "front-direct", "astride", "full-length", "directed", "finepornV4",
    )
    assert cell == {"judged": 1, "arrived": 1}

    # Verify shot verdicts unchanged
    import json as _json
    verdicts = _json.loads(db.one("SELECT verdicts FROM shot WHERE id=?", shot_id)["verdicts"])
    assert verdicts == {"camera": "front-direct"}


def test_judge_control_shot_disagreement_does_not_overwrite_stored_verdict(client, seeded):
    """5.3 Decision B: When an operator answers a control photograph
    differently from its stored verdict, agreed is False and the stored
    verdict is NEVER overwritten (spec.md:141-144).
    """
    camera = {"key": "front-direct", "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length", "wordings": [{"key": "full-length", "text": "full-length text"}]}

    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing, session_name="control disagree",
    )

    # Initial judgement: camera='front-direct'
    client.post(f"/api/shots/{shot_id}/judge", json={"camera": "front-direct"})

    # Control call disagreeing: answered='overhead-direct'
    r_ctrl = client.post(f"/api/shots/{shot_id}/judge", json={
        "camera": "overhead-direct", "control": True,
    })
    assert r_ctrl.status_code == 200, r_ctrl.text
    assert r_ctrl.json() == {
        "control": True,
        "slot": "camera",
        "agreed": False,
        "stored": "front-direct",
        "answered": "overhead-direct",
    }

    # Stored verdict is STILL 'front-direct' (not overwritten)
    import json as _json
    verdicts = _json.loads(db.one("SELECT verdicts FROM shot WHERE id=?", shot_id)["verdicts"])
    assert verdicts == {"camera": "front-direct"}, "stored verdict was overwritten!"

    # Cell counts unchanged
    cell = db.one(
        "SELECT judged, arrived FROM cell WHERE camera_wording=? AND act_wording=? "
        "AND framing_wording=? AND manner=? AND checkpoint=?",
        "front-direct", "astride", "full-length", "directed", "finepornV4",
    )
    assert cell == {"judged": 1, "arrived": 1}


def test_judge_control_shot_on_unjudged_slot_is_refused(client, seeded):
    """control=True on a shot that has no stored verdict for that slot is refused with 422."""
    camera = {"key": "front-direct", "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length", "wordings": [{"key": "full-length", "text": "full-length text"}]}

    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing, session_name="control unjudged",
    )

    # Shot has not been judged for camera
    r = client.post(f"/api/shots/{shot_id}/judge", json={
        "camera": "front-direct", "control": True,
    })
    assert r.status_code == 422
    assert "no stored verdict for slot 'camera'" in r.json()["detail"]


def test_a_control_answering_two_slots_is_refused_rather_than_half_read(client, seeded):
    """A control call carries one slot. The first shape of the control
    branch took `next(iter(answers.items()))` and dropped the rest, so a
    call answering camera correctly and act wrongly came back

        {'control': True, 'slot': 'camera', 'agreed': True, ...}

    with the act disagreement silently lost — an instrument that measures
    the judge quietly discarding one of its own measurements. A pass asks
    one question across a batch (spec.md:118), so more than one slot on a
    control is a caller bug and is refused.
    """
    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "full-length text"}]}
    shot_id = _composed_shot_in_session(
        client, seeded, manner="directed", checkpoint="finepornV4",
        camera=camera, act=act, framing=framing, session_name="control two slots",
    )
    # Both slots answered for real first, so the control has something
    # stored to compare against on each of them.
    client.post(f"/api/shots/{shot_id}/judge", json={"camera": "front-direct"})
    client.post(f"/api/shots/{shot_id}/judge", json={"act": "astride"})

    r = client.post(f"/api/shots/{shot_id}/judge", json={
        "camera": "front-direct", "act": "reverse", "control": True,
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "one slot per call" in detail, detail
    # Both slot names are named, so the caller can see what it sent.
    assert "act" in detail and "camera" in detail, detail


def test_a_slot_with_one_value_does_not_cap_the_run_at_one_photograph(client, seeded):
    """3.4's rule is "no component twice in a run". A slot the pool offers
    ONE value for cannot be spread over, and holding it to that rule caps
    every run at one photograph.

    That is not hypothetical. The compose control shipped in group 8 sends
    a single fixed framing wording, because framing has no catalogue, and
    its own default count came back refused with nothing queued:

        exploratory count=4 -> 422
        compose refused: framing slot has 1 drawable values within the
        trio pool, largest fillable is 1 (of 4 requested)

    Here: three cameras, three acts, ONE framing. A run of 3 must be
    queued with three distinct cameras and three distinct acts, all
    sharing the one framing.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "one framing",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]
    cams = ["cam-a", "cam-b", "cam-c"]
    acts = ["act-a", "act-b", "act-c"]
    candidates = {
        "camera":  [_candidate(k, f"camera {k}") for k in cams],
        "act":     [_candidate(k, f"act {k}") for k in acts],
        "framing": [_candidate("only-framing", "the one framing")],
    }
    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 3, "candidates": candidates, "mode": "exploratory",
    })
    assert r.status_code == 200, r.text
    rows = db.q("SELECT components FROM shot WHERE session_id=?", sid)
    assert len(rows) == 3, rows
    import json as _json
    drawn = [_json.loads(row["components"]) for row in rows]
    # The two slots that HAVE a choice are still spread: no repeats.
    assert len({d["camera"]["wording"] for d in drawn}) == 3, drawn
    assert len({d["act"]["wording"] for d in drawn}) == 3, drawn
    # And the one-value slot is the same on all three, which is the whole
    # point — it had nowhere else to go.
    assert {d["framing"]["wording"] for d in drawn} == {"only-framing"}, drawn


def test_a_slot_with_a_choice_still_refuses_a_run_that_would_repeat_it(client, seeded):
    """The other half: exempting one-value slots must not exempt the rest.
    Three cameras, THREE acts, one framing, and a run of 4 — one more than
    the act list can fill without repeating. Refused, nothing queued, and
    the message names the act slot rather than the exempt framing.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "act runs out",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]
    candidates = {
        "camera":  [_candidate(k, f"camera {k}") for k in ("cam-a", "cam-b", "cam-c", "cam-d")],
        "act":     [_candidate(k, f"act {k}") for k in ("act-a", "act-b", "act-c")],
        "framing": [_candidate("only-framing", "the one framing")],
    }
    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 4, "candidates": candidates, "mode": "exploratory",
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "act slot has 3" in detail, detail
    assert "framing" not in detail, f"the exempt slot was named as the shortfall: {detail!r}"
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, f"a refused run queued {n} shots"


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


def test_the_n_draw_reaches_a_trio_the_cell_table_has_never_heard_of(client, seeded):
    """The half of 6.1 the one-shot tests could not see.

    `test_an_unknown_cell_is_drawable_in_exploratory_mode` seeds the
    cell explicitly and calls `/compose`, one shot at a time. The
    N-draw (`/compose-run`, and `/compose-session` behind it) goes
    through `_trio_pool`, and that used to be a `SELECT` over `cell`
    with a looser predicate — so a trio with NO ROW was not in the
    pool at all. Since a cell nobody measured has no row, and
    `judged < 10` is the definition of `unknown`, exploratory could
    only explore what had already been measured. The one-shot
    endpoint queued the same trio happily. Two calculations that
    were supposed to agree and did not, measured through the API:

        compose (one shot) exploratory, no row -> 200 queued
        compose-run        exploratory, no row -> 422

    and the 422 said "every candidate trio is either dead or outside
    the catalogue", which was false — they were unknown, which is
    the one thing exploratory exists to draw.

    No cell is seeded here on purpose. The assertion is that the run
    is queued anyway, and `count` rows land. A regression to the
    `SELECT` shape refuses with a 422 and the count reads 0.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "never measured",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]
    trios = [("cam-x", "act-x", "frame-x"), ("cam-y", "act-y", "frame-y")]
    candidates = {
        "camera":  [_candidate(k, f"camera {k} text")  for k, _, _ in trios],
        "act":     [_candidate(k, f"act {k} text")     for _, k, _ in trios],
        "framing": [_candidate(k, f"framing {k} text") for _, _, k in trios],
    }

    # Strict first, as the control: with no rows there is nothing
    # verified, so strict refuses and points at the wider mode. This
    # is what makes the exploratory half mean something — without it
    # the test would pass on a composer that ignored `mode` entirely.
    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 2, "candidates": candidates, "mode": "strict",
    })
    assert r.status_code == 422, r.text
    assert "exploratory" in r.json()["detail"], r.json()["detail"]
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, f"strict refused run queued shots: {n} rows for session {sid}"

    r = client.post(f"/api/sessions/{sid}/compose-run", json={
        "count": 2, "candidates": candidates, "mode": "exploratory",
    })
    assert r.status_code == 200, r.text
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 2, f"exploratory run did not queue 2 shots: {n} rows"


def test_the_n_draw_never_reaches_a_dead_trio_even_with_no_other_row(client, seeded):
    """The dead half of 6.1 on the N-draw, which is where the new pool
    shape could have lost it: the pool is now the product of the
    candidates MINUS the dead rows, so "dead is excluded" moved from a
    `WHERE` clause to a set subtraction, and a subtraction silently
    does nothing if the tuple shape drifts.

    ONE candidate per slot, and that single product IS the dead trio.
    The pool is therefore exactly one trio, and it is dead, so the run
    must be refused whatever the mode. A wider candidate list was the
    first shape of this test and it did not bite: with 8 products and
    one of them dead, `count=1` draws a live trio almost every time and
    the test passes on a composer that never subtracts anything. The
    pool has to be all-dead for the refusal to be the only legal
    answer — see the note on tests that cannot fail in the 3.x
    write-ups.

    Verified by deleting the `- matched` subtraction: the exploratory
    call comes back 200 with a queued photograph of the trio the
    measurement already refused.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "dead in the n draw",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]
    _seed_dead_trio("cam-dead", "act-dead", "frame-dead",
                    manner="directed", checkpoint="finepornV4")
    candidates = {
        "camera":  [_candidate("cam-dead", "camera dead text")],
        "act":     [_candidate("act-dead", "act dead text")],
        "framing": [_candidate("frame-dead", "framing dead text")],
    }

    for mode in ("strict", "exploratory"):
        r = client.post(f"/api/sessions/{sid}/compose-run", json={
            "count": 1, "candidates": candidates, "mode": mode,
        })
        assert r.status_code == 422, f"{mode}: {r.text}"
        n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
        assert n == 0, f"{mode} queued a dead trio: {n} rows for session {sid}"


# ---------------------------------------------------------------------- 8.5


def test_compose_with_count_one_queues_one_row_of_the_trio(client, seeded):
    """The 3.1 single-shot case is the count=1 case. The field is
    on the payload (it was added in 8.5), but the default keeps
    the pre-8.5 callers' behaviour: one POST, one row, response
    `{"ids": [id], "count": 1}`.

    Verified by breaking the code: replacing `for _ in range(c.count)`
    with `for _ in range(1)` would make this test pass on count=1
    (the default), but the n=10 test below would fail with 1 row
    instead of 10. The count=1 regression is the cheap half; the
    n=10 test is the loop-closed half.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "fill one",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 10, 8)

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": {"key": "front-direct", "wordings": [{"key": "front-direct", "text": "front text"}]},
        "act": {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]},
        "framing": {"key": "full-length", "wordings": [{"key": "full-length", "text": "framing text"}]},
        "count": 1,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert len(body["ids"]) == 1
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 1


def test_compose_with_count_ten_queues_ten_rows_of_the_same_trio(client, seeded):
    """The 8.5 named scenario: an operator picks one trio and a count
    on an existing session, and the screen queues that many
    photographs of the same trio so the cell can be filled to its
    `judged=10` threshold from the app.

    The trio is verified (10/8), the session carries the cell's
    manner and checkpoint, the request asks for `count=10` on a
    pool that has at least one verified trio. The assertion is
    THREE things, each of which would fail under a different
    pre-8.5 bug:

    1. The response says `count: 10` and the ids list has 10
       elements. A pre-8.5 endpoint that ignored `count` would
       return `count: 1` and a 1-element list, and this
       assertion fails.
    2. The shot table has 10 rows for this session. The same
       pre-8.5 bug surfaces here too — 1 row, not 10.
    3. Every row's `components` JSON is the SAME trio, byte
       for byte. A bug that re-drew the trio per row (e.g. a
       future "let me re-randomise each insert") surfaces
       here, and the cell the 10 photographs are filling is
       the cell every row counts toward.

    The cell check is on the TRIO, not on the COUNT — a cell
    is a row in the cell table, and the same trio, no matter
    how many rows the operator asks for, is one cell. So the
    pre-check passes for the verified trio, and 10 rows queue.

    Verified by breaking the code: replacing
    `for _ in range(c.count)` with `for _ in range(1)` makes
    the assertion `n == 10` fail with `n == 1`. Reverting and
    replacing `c.count` with `1` directly makes the response
    `count: 1` fail. Reverting and reading the trio from
    `best_chosen` (a different trio each iteration) makes the
    `components == same` assertion fail. Three loops, three
    failures, three surfaces a future bug has to trip.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "fill ten",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 10, 8)

    camera = {"key": "front-direct",
              "wordings": [{"key": "front-direct", "text": "front text"}]}
    act = {"key": "astride", "wordings": [{"key": "astride", "text": "astride text"}]}
    framing = {"key": "full-length",
               "wordings": [{"key": "full-length", "text": "framing text"}]}

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": camera, "act": act, "framing": framing,
        "count": 10,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 10
    assert len(body["ids"]) == 10
    # 10 distinct ids. The runner allocates them one per INSERT, and
    # SQLite's autoincrement gives each one a fresh number.
    assert len(set(body["ids"])) == 10

    # Every row is the same trio, recorded in the components column.
    expected_comps = {
        "camera":  {"concept": "front-direct", "wording": "front-direct"},
        "act":     {"concept": "astride",      "wording": "astride"},
        "framing": {"concept": "full-length",  "wording": "full-length"},
    }
    rows = db.q("SELECT id, components, prompt, seed FROM shot WHERE session_id=?", sid)
    assert len(rows) == 10
    for row in rows:
        db.jload(row, "components")
        assert row["components"] == expected_comps, f"row {row['id']} has different components: {row['components']}"

    # Every row carries seed=0 at queue time. The runner rolls a fresh
    # random per row (backend/runner.py:117 reads `shot["seed"] or
    # random.randint(...)`), and N identical prompts DO render N
    # different photographs — that is the runner's job, not 8.5's.
    # The byte-equal prompt check is what 8.5 owns.
    seeds = {row["seed"] for row in rows}
    assert seeds == {0}, f"rows queued with non-zero seed: {seeds}"
    # Every row's prompt is byte-equal: same trigger, same base, same
    # look, same wardrobe, same trio joined the same way. The
    # runner's per-row seed (above) is what differentiates the
    # rendered photographs.
    prompts = {row["prompt"] for row in rows}
    assert len(prompts) == 1, f"expected 1 prompt, got {len(prompts)}: {prompts}"
    # The prompt is non-empty: a real composed line.
    assert next(iter(prompts)) != "", "queued rows have empty prompt"


def test_compose_with_count_ten_refuses_a_dead_trio_before_any_insert(client, seeded):
    """The pre-check protects the loop: a dead cell is refused in
    both modes, and the refusal fires BEFORE any INSERT (db.run
    auto-commits per INSERT; a check that fires at row k+1 would
    leave k rows behind). With `count=10` the loop-closed test is
    loud: 10 rows committed before the refusal would fail this
    assertion with `n == 10` instead of `n == 0`.

    Verified by breaking the code: moving the cell check INSIDE
    the loop (after the first `compose_and_queue_shot`) makes
    rows 0-9 commit and row 10 (the next iteration) refuse. The
    `n == 0` assertion fails with `n == 10` and the test prints
    the count.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "fill ten dead",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]
    # 10/0 lands as `dead` (cell_state returns "dead" for
    # judged >= 10 AND arrived * 10 < judged * 8).
    db.run("INSERT INTO cell (camera_wording, act_wording, framing_wording, "
           "manner, checkpoint, judged, arrived) VALUES (?, ?, ?, ?, ?, ?, ?)",
           "front-direct", "astride", "full-length", "directed", "finepornV4", 10, 0)

    for mode in ("strict", "exploratory"):
        # A fresh session per mode so the loop-closed assertion
        # (n == 0 for THIS session) is unambiguous.
        sub = client.post("/api/sessions", json={
            "model_id": seeded["model_id"], "name": f"fill ten dead {mode}",
            "manner": "directed", "checkpoint": "finepornV4", "shots": [],
        }).json()["id"]
        r = client.post(f"/api/sessions/{sub}/compose", json={
            "camera": {"key": "front-direct",
                       "wordings": [{"key": "front-direct", "text": "front text"}]},
            "act": {"key": "astride",
                    "wordings": [{"key": "astride", "text": "astride text"}]},
            "framing": {"key": "full-length",
                        "wordings": [{"key": "full-length", "text": "framing text"}]},
            "count": 10, "mode": mode,
        })
        assert r.status_code == 422, f"{mode}: {r.text}"
        n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sub)["n"]
        assert n == 0, f"{mode} queued a dead cell on count=10: {n} rows for session {sub}"


def test_compose_with_count_ten_refuses_an_unknown_trio_in_strict(client, seeded):
    """The unknown-trio branch of the pre-check: a trio that was
    never measured is unknown, and strict mode refuses to draw
    it. With `count=10` the loop-closed assertion is the same:
    0 rows for THIS session, not 10. The 422 message names the
    trio, the manner, the checkpoint, and the state (`unknown`)
    so the operator can see what is missing.

    Verified by breaking the code: skipping the pre-check
    (jumping straight to the loop) lets 10 rows commit on an
    unknown trio in strict mode, and `n == 0` fails with
    `n == 10`. The response code is 200, not 422, and the test
    fails on the status check too.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "fill ten unknown strict",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]
    # No cell seeded: the trio is unknown.

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": {"key": "front-direct",
                   "wordings": [{"key": "front-direct", "text": "front text"}]},
        "act": {"key": "astride",
                "wordings": [{"key": "astride", "text": "astride text"}]},
        "framing": {"key": "full-length",
                    "wordings": [{"key": "full-length", "text": "framing text"}]},
        "count": 10, "mode": "strict",
    })
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert "unknown" in detail, f"refusal should name the state: {detail!r}"
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0, f"strict queued an unknown trio on count=10: {n} rows"


def test_compose_with_count_ten_draws_an_unknown_trio_in_exploratory(client, seeded):
    """The exploratory mode widens the draw to include `unknown`
    cells (a cell is not `dead` until it has been measured at
    10+, and an absent cell is `unknown` by definition). With
    `count=10` the loop queues 10 rows of the unknown trio, the
    cell is created on the first judgement (6.2's responsibility,
    not 8.5's), and the response carries the 10 ids.

    The point of this test: the count=10 path is the same draw
    as the count=1 path in exploratory mode, and the loop
    inherits the `unknown is drawable` decision the one-shot
    branch already made. A code change that "let me also
    refuse unknown in exploratory for count>1" lands here as a
    regression on the explorer's intended behaviour.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "fill ten unknown exploratory",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]
    # No cell seeded: the trio is unknown.

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": {"key": "front-direct",
                   "wordings": [{"key": "front-direct", "text": "front text"}]},
        "act": {"key": "astride",
                "wordings": [{"key": "astride", "text": "astride text"}]},
        "framing": {"key": "full-length",
                    "wordings": [{"key": "full-length", "text": "framing text"}]},
        "count": 10, "mode": "exploratory",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 10
    assert len(body["ids"]) == 10
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 10


def test_compose_with_count_zero_is_rejected_at_the_boundary(client, seeded):
    """`count` is a `Field(1, ge=1)` on `ComposeIn`: pydantic
    refuses `count=0` or negative BEFORE the handler runs. A
    code change that drops the bound to `int = 1` (no `ge=1`)
    lets `count=0` slip into the loop, and `range(0)` is
    a no-op — the response is `count: 0` with an empty ids
    list. The assertion on the 422 status fails on `200`.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "fill zero",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    for bad in (0, -1):
        r = client.post(f"/api/sessions/{sid}/compose", json={
            "camera": {"key": "front-direct",
                       "wordings": [{"key": "front-direct", "text": "front text"}]},
            "act": {"key": "astride",
                    "wordings": [{"key": "astride", "text": "astride text"}]},
            "framing": {"key": "full-length",
                        "wordings": [{"key": "full-length", "text": "framing text"}]},
            "count": bad,
        })
        assert r.status_code == 422, f"count={bad}: {r.status_code} {r.text}"


def test_judge_pass_opens_framing_once_a_manner_has_two(client, seeded):
    """The framing pass refuses a list of one and opens at two.

    The refusal was unconditional while every manner carried a single
    framing. It is a count now, and the count is per MANNER: the store
    can hold six framings across three manners and still hand this
    operator a forced choice over one, which is not a question.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "framing pass",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    # The seed ships one framing per manner: still refused, and the
    # message counts what is there for THIS manner.
    r_one = client.get(f"/api/sessions/{sid}/judge-pass?slot=framing")
    assert r_one.status_code == 422
    assert "1 component(s) for manner 'directed'" in r_one.json()["detail"]

    db.run(
        """INSERT INTO component (concept_key, slot, manner, family, faces,
                                  wording, judge_label, created_at)
           VALUES ('chest-up', 'framing', 'directed', 'chest_up', '',
                   'the frame cuts at her lower chest',
                   'Frame ends at the lower chest', ?)""",
        db.now(),
    )
    r_two = client.get(f"/api/sessions/{sid}/judge-pass?slot=framing")
    assert r_two.status_code == 200, r_two.text
    assert r_two.json() == {"shots": [], "controls": []}

    # A second manner is untouched by directed's second framing.
    other = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "candid pass",
        "manner": "candid", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]
    r_other = client.get(f"/api/sessions/{other}/judge-pass?slot=framing")
    assert r_other.status_code == 422
    assert "manner 'candid'" in r_other.json()["detail"]


def test_a_control_arm_composes_with_no_phrase_for_a_slot(client, seeded):
    """A slot handed an empty wording drops out of the line and the
    cell records it as `none`.

    That is the control the eight camera wordings are read against:
    without it a wording that arrives 10 of 10 cannot be told apart
    from a model that was going to render that photograph anyway. The
    empty text is not a catalogue row — the component table's CHECK
    refuses `wording = ''` — so it can only arrive on the payload.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "control",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": {"key": "none", "wordings": [{"key": "none", "text": ""}]},
        "act": {"key": "astride",
                "wordings": [{"key": "astride", "text": "She is astride him"}]},
        "framing": {"key": "framing",
                    "wordings": [{"key": "framing", "text": "a three-quarter photograph"}]},
        "mode": "exploratory",
    })
    assert r.status_code == 200, r.text

    shot = db.one("SELECT prompt, components FROM shot WHERE session_id=?", sid)
    comps = json.loads(shot["components"])
    assert comps["camera"]["wording"] == "none"
    # The empty phrase leaves no trace in the line: no stray full stop, no
    # doubled space where the camera sentence would have been.
    assert "She is astride him." in shot["prompt"]
    assert "  " not in shot["prompt"]
    assert ".." not in shot["prompt"]


def _comp(key, text):
    return {"key": key, "wordings": [{"key": key, "text": text}]}


def test_compose_takes_a_list_per_slot_and_queues_every_combination(client, seeded):
    """A list on more than one slot is the cross product, `count` each."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "cross",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": [_comp("front-direct", "from the front"),
                   _comp("overhead-direct", "from above")],
        "act": [_comp("astride", "astride him"), _comp("wall", "against the wall")],
        "framing": _comp("framing", "a three-quarter photograph"),
        "count": 2, "mode": "exploratory",
    })
    assert r.status_code == 200, r.text
    assert r.json()["cells"] == 4
    assert r.json()["count"] == 8

    trios = {(json.loads(s["components"])["camera"]["wording"],
              json.loads(s["components"])["act"]["wording"])
             for s in db.q("SELECT components FROM shot WHERE session_id=?", sid)}
    assert trios == {("front-direct", "astride"), ("front-direct", "wall"),
                     ("overhead-direct", "astride"), ("overhead-direct", "wall")}


def test_a_dead_cell_anywhere_in_the_batch_queues_nothing(client, seeded):
    """The all-or-nothing rule holds across the cross product.

    `count` already guarantees N rows or zero for one cell. A batch
    that checked each combination as it inserted would leave the
    combinations before the bad one behind — which is the partial
    delivery the pre-check exists to make impossible.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "all or nothing",
        "manner": "directed", "checkpoint": "finepornV4", "shots": [],
    }).json()["id"]

    # The SECOND camera's cell is dead: 12 judged, 1 arrived.
    db.run("""INSERT INTO cell (camera_wording, act_wording, framing_wording,
                                manner, checkpoint, judged, arrived)
              VALUES ('overhead-direct', 'astride', 'framing',
                      'directed', 'finepornV4', 12, 1)""")

    r = client.post(f"/api/sessions/{sid}/compose", json={
        "camera": [_comp("front-direct", "from the front"),
                   _comp("overhead-direct", "from above")],
        "act": _comp("astride", "astride him"),
        "framing": _comp("framing", "a three-quarter photograph"),
        "count": 3, "mode": "exploratory",
    })
    assert r.status_code == 422, r.text
    assert "is dead, not drawable in any mode" in r.json()["detail"]
    # Not the three rows of the first, healthy combination either.
    assert db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"] == 0
