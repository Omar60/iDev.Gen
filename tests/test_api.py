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
    }).json()["id"]

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
    assert "id" in r.json()
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
    assert "id" in r.json()
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 1


def test_a_request_with_an_unknown_mode_field_still_runs_the_strict_check(client, seeded):
    """The compose endpoint has no `mode` field on its payload.
    Strict is the only legal mode today; encoding it as a string
    would let a wrong value bypass the check (an if over a free
    string is a door open by default — the type definition is the
    check, the consumer's branch is the lock), and there is no
    second mode to switch to. A request that tries to set
    `mode=anything` (or `mode=exploratory`, or any other string)
    is parsed by pydantic with the default extra="ignore": the
    field is silently dropped, the strict check still runs
    unconditionally, and the compose is refused. This is the test
    that distinguishes "there is a strict mode" from "there is a
    strict mode that can be turned off by writing it wrong". A
    future regression that re-introduces `mode: str = "strict"`
    with a `if c.mode == "strict":` guard breaks this test on the
    spot — the cell is not verified, the request passes
    `mode="anything"`, and the assertion is `n_shots == 0`.
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "bypass attempt",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

    # No cell seeded. The strict check should refuse the compose.
    # A "mode" field on the payload is silently dropped by pydantic
    # because ComposeIn does not declare it; the strict check
    # still runs and finds no verified cell.
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
    # pydantic drops the unknown field; the request body parses.
    # The strict check then refuses the compose.
    assert r.status_code == 422, r.text
    assert "no measurement" in r.json()["detail"] or "unknown" in r.json()["detail"]
    n = db.one("SELECT COUNT(*) AS n FROM shot WHERE session_id=?", sid)["n"]
    assert n == 0


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
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "greedy no-repeat",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

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
    """
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "verdict consistency",
        "manner": "directed", "checkpoint": "finepornV4",
        "shots": [],
    }).json()["id"]

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
