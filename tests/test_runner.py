"""The runner against a fake ComfyUI: queueing, real graph patching, moving the
file into the session, and what happens when something goes wrong."""
import asyncio
import json

import pytest

import db
import runner as runner_mod
from conftest import EDIT_GRAPH, GRAPH


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(runner_mod, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(runner_mod, "MOVE_RETRY_DELAY", 0.001)


def _session(client, count=2, **model_kw):
    wf = client.post("/api/workflows", json={"name": "wf", "graph": GRAPH}).json()
    payload = {"name": "ada", "lora_name": "characters/ada.safetensors", "trigger": "4da woman",
               "base_negative": "blurry", "workflow_id": wf["id"],
               "settings": {"width": 832, "height": 1216, "steps": 8, "cfg": 1.0}}
    payload.update(model_kw)
    mid = client.post("/api/models", json=payload).json()["id"]
    return client.post("/api/sessions", json={
        "model_id": mid, "name": "shoot", "seed_mode": "fixed", "seed": 77,
        "settings": {"lora_strength": 0.85},
        "shots": [{"label": "beach", "prompt": "on the beach", "count": count}],
    }).json()["id"]


def test_full_run_moves_the_photos(client, make_runner, comfy_output):
    sid = _session(client, count=2)
    r, _ = make_runner()
    asyncio.run(r._run_session(sid))

    shots = db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)
    assert [s["status"] for s in shots] == ["done", "done"]
    assert db.one("SELECT status FROM session WHERE id=?", sid)["status"] == "done"

    for shot in shots:
        dest = r.sessions_dir / str(sid) / shot["filename"]
        assert dest.exists(), dest
        assert shot["filename"].endswith("_beach.png")
    # Nothing left in ComfyUI's output, not even the empty folder.
    assert not (comfy_output / "idevgen").exists()


def test_the_queued_graph_carries_the_session_values(client, make_runner):
    sid = _session(client, count=1)
    r, fake = make_runner()
    asyncio.run(r._run_session(sid))

    g = fake.graphs[0]
    assert g["3"]["inputs"]["text"] == "4da woman. on the beach."
    assert g["4"]["inputs"]["text"] == "blurry"
    assert g["6"]["inputs"]["seed"] == 77
    assert g["6"]["inputs"]["steps"] == 8
    assert g["5"]["inputs"]["width"] == 832 and g["5"]["inputs"]["height"] == 1216
    assert g["2"]["inputs"]["lora_name"] == "characters/ada.safetensors"
    assert g["2"]["inputs"]["strength_model"] == 0.85     # the session override
    assert g["1"]["inputs"]["ckpt_name"] == "base.safetensors"   # untouched: none chosen
    # Own prefix: never collides with what you generate by hand in ComfyUI.
    assert g["8"]["inputs"]["filename_prefix"].startswith(f"idevgen/{sid}/")


def test_the_session_kind_is_not_a_slot(client, make_runner):
    """A session's kind lives in the same settings blob the slot values come
    from, but it is a label for the screens: the runner reads that blob key by
    key, so an unknown key changes nothing in the graph and nothing in the run."""
    wf = client.post("/api/workflows", json={"name": "wf", "graph": GRAPH}).json()
    mid = client.post("/api/models", json={"name": "ada", "workflow_id": wf["id"]}).json()["id"]
    sid = client.post("/api/sessions", json={
        "model_id": mid, "name": "s", "settings": {"kind": "angles"},
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]

    r, fake = make_runner()
    asyncio.run(r._run_session(sid))
    assert db.one("SELECT status FROM session WHERE id=?", sid)["status"] == "done"
    assert "angles" not in str(fake.graphs[0])


def test_the_chosen_base_model_reaches_the_graph(client, make_runner):
    """One workflow per family is enough: the model is picked in the app."""
    wf = client.post("/api/workflows", json={"name": "wf", "graph": GRAPH}).json()
    mid = client.post("/api/models", json={
        "name": "ada", "workflow_id": wf["id"],
        "settings": {"checkpoint": "chosen_by_the_model.safetensors"}}).json()["id"]
    sid = client.post("/api/sessions", json={
        "model_id": mid, "name": "s", "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]

    r, fake = make_runner()
    asyncio.run(r._run_session(sid))
    assert fake.graphs[0]["1"]["inputs"]["ckpt_name"] == "chosen_by_the_model.safetensors"

    # A session overrides the model, like every other setting.
    sid2 = client.post("/api/sessions", json={
        "model_id": mid, "name": "s2", "settings": {"checkpoint": "chosen_by_the_session.safetensors"},
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    r2, fake2 = make_runner()
    asyncio.run(r2._run_session(sid2))
    assert fake2.graphs[0]["1"]["inputs"]["ckpt_name"] == "chosen_by_the_session.safetensors"


def test_every_attempt_gets_its_own_filename_prefix(client, make_runner):
    """Two identical attempts must not share a prefix: ComfyUI would answer with
    a cached SaveImage and write no file at all."""
    sid = _session(client, count=1)
    r, fake = make_runner(fail_queue=True)
    asyncio.run(r._run_session(sid))
    client.post(f"/api/sessions/{sid}/retry")

    r2, fake2 = make_runner()
    asyncio.run(r2._run_session(sid))
    client.post(f"/api/sessions/{sid}/retry")   # forces a third identical attempt
    db.run("UPDATE shot SET status='pending' WHERE session_id=?", sid)
    r3, fake3 = make_runner()
    asyncio.run(r3._run_session(sid))

    assert (fake2.graphs[0]["8"]["inputs"]["filename_prefix"]
            != fake3.graphs[0]["8"]["inputs"]["filename_prefix"])


def test_a_rejected_shot_does_not_stop_the_ones_behind_it(client, make_runner):
    """The run continues past a failure — and a session that still produced a
    photo is `done`, because it produced photos."""
    sid = _session(client, count=3)
    r, _ = make_runner(fail_first=1)
    asyncio.run(r._run_session(sid))

    shots = db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)
    assert [s["status"] for s in shots] == ["failed", "done", "done"]
    assert "validation" in shots[0]["error"]
    assert db.one("SELECT status FROM session WHERE id=?", sid)["status"] == "done"


def test_a_session_where_everything_failed_is_failed(client, make_runner):
    """`done` would read as "the shoot went fine" when nothing came out of it."""
    sid = _session(client, count=2)
    r, _ = make_runner(fail_queue=True)
    asyncio.run(r._run_session(sid))

    shots = db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)
    assert [s["status"] for s in shots] == ["failed", "failed"]
    assert db.one("SELECT status FROM session WHERE id=?", sid)["status"] == "failed"


def test_execution_error_is_recorded_as_one_readable_line(client, make_runner):
    sid = _session(client, count=1)
    r, _ = make_runner(no_images=True)
    asyncio.run(r._run_session(sid))

    shot = db.one("SELECT * FROM shot WHERE session_id=?", sid)
    assert shot["status"] == "failed"
    assert shot["error"] == "KSampler · RuntimeError: Given normalized_shape=[2560]"
    # The traceback and the input dump stay out of the gallery card.
    assert "traceback" not in shot["error"] and "current_inputs" not in shot["error"]


def test_a_file_that_never_shows_up_only_fails_that_shot(client, make_runner):
    sid = _session(client, count=2)
    r, _ = make_runner(write_file=False)
    asyncio.run(r._run_session(sid))

    shots = db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)
    assert [s["status"] for s in shots] == ["failed", "failed"]
    assert "not found" in shots[0]["error"]
    assert db.one("SELECT status FROM session WHERE id=?", sid)["status"] == "failed"


def test_a_briefly_locked_file_is_retried_not_failed(client, make_runner, monkeypatch):
    """Windows sharing violation (WinError 32) right after ComfyUI writes the PNG:
    it clears in under a second, so it must not cost a real generation."""
    sid = _session(client, count=1)
    r, _ = make_runner()
    real_move = runner_mod.shutil.move
    calls = []

    def flaky_move(src, dst):
        calls.append(src)
        if len(calls) < 3:
            raise PermissionError(32, "The process cannot access the file")
        return real_move(src, dst)

    monkeypatch.setattr(runner_mod.shutil, "move", flaky_move)
    asyncio.run(r._run_session(sid))

    shot = db.one("SELECT * FROM shot WHERE session_id=?", sid)
    assert shot["status"] == "done", shot["error"]
    assert len(calls) == 3                                   # two retries, then through
    assert (r.sessions_dir / str(sid) / shot["filename"]).exists()


def test_a_file_locked_for_good_still_fails_the_shot(client, make_runner, monkeypatch):
    sid = _session(client, count=1)
    r, _ = make_runner()

    def always_locked(src, dst):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(runner_mod.shutil, "move", always_locked)
    asyncio.run(r._run_session(sid))

    shot = db.one("SELECT * FROM shot WHERE session_id=?", sid)
    assert shot["status"] == "failed"
    assert "cannot access the file" in shot["error"]


def test_cancelling_marks_the_pending_shots_and_queues_nothing(client, make_runner):
    sid = _session(client, count=3)
    r, fake = make_runner()
    r.cancel(sid)
    asyncio.run(r._run_session(sid))

    shots = db.q("SELECT status FROM shot WHERE session_id=?", sid)
    assert {s["status"] for s in shots} == {"cancelled"}
    assert db.one("SELECT status FROM session WHERE id=?", sid)["status"] == "cancelled"
    assert fake.graphs == []


def test_retry_puts_failed_shots_back_in_the_queue(client, make_runner):
    sid = _session(client, count=1)
    r, _ = make_runner(fail_queue=True)
    asyncio.run(r._run_session(sid))
    assert client.post(f"/api/sessions/{sid}/retry").json()["pending"] == 1

    r2, _ = make_runner()
    asyncio.run(r2._run_session(sid))
    assert db.one("SELECT status FROM shot WHERE session_id=?", sid)["status"] == "done"


def test_session_without_workflow_fails_with_a_message(client, make_runner):
    sid = _session(client, count=1)
    db.run("UPDATE session SET workflow_id=NULL WHERE id=?", sid)
    db.run("UPDATE model SET workflow_id=NULL")
    r, _ = make_runner()
    asyncio.run(r._run_session(sid))

    assert db.one("SELECT status FROM session WHERE id=?", sid)["status"] == "failed"
    assert "workflow" in db.one("SELECT error FROM shot WHERE session_id=?", sid)["error"]


# --------------------------------------------------------------- reference takes

def _reference_session(client, **session_kw):
    """One session, two workflows: the anchor is painted by `wf`, the edit that
    follows is run through `edit_wf` against the anchor photo."""
    wf = client.post("/api/workflows", json={"name": "wf", "graph": GRAPH}).json()
    edit_wf = client.post("/api/workflows", json={"name": "edit", "graph": EDIT_GRAPH}).json()
    mid = client.post("/api/models", json={
        "name": "ada", "lora_name": "characters/ada.safetensors", "trigger": "4da woman",
        "base_positive": "photo, 35mm", "workflow_id": wf["id"]}).json()["id"]
    payload = {"model_id": mid, "name": "shoot", "look": "leather jacket",
               "reference_workflow_id": edit_wf["id"], "settings": {"denoise": 0.55},
               "shots": [{"label": "anchor", "prompt": "standing", "count": 1},
                         {"label": "edit", "prompt": "remove the jacket", "count": 1,
                          "reference": True}]}
    payload.update(session_kw)
    return client.post("/api/sessions", json=payload).json()["id"]


def test_a_reference_take_edits_the_anchor_through_the_other_workflow(client, make_runner):
    sid = _reference_session(client)
    r, fake = make_runner()
    asyncio.run(r._run_session(sid))

    shots = db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)
    assert [s["status"] for s in shots] == ["done", "done"], shots[1]["error"]

    anchor_graph, edit_graph = fake.graphs
    # The anchor is a normal text2image take: composed prompt, own workflow.
    assert anchor_graph["3"]["inputs"]["text"] == "4da woman. photo, 35mm. leather jacket. standing."
    assert "2" in anchor_graph and anchor_graph["2"]["class_type"] == "LoraLoader"

    # The edit went through the reference workflow, with the anchor loaded and the
    # prompt sent as a bare instruction.
    assert edit_graph["2"]["class_type"] == "LoadImage"
    assert edit_graph["2"]["inputs"]["image"] == f"idevgen/idevgen_ref_{shots[0]['id']}.png"
    assert edit_graph["3"]["inputs"]["text"] == "remove the jacket"
    assert edit_graph["6"]["inputs"]["denoise"] == 0.55

    # What was uploaded is the anchor's own file, out of the session folder.
    assert len(fake.uploads) == 1
    assert fake.uploads[0][0].endswith(shots[0]["filename"])


def test_a_reference_take_keeps_its_own_model_and_lora(client, make_runner):
    """An editing graph loads its own base model and its own edit LoRA, and the
    character comes from the anchor photo. Sending the session's checkpoint and the
    model's character LoRA would replace both, with nothing on screen saying the
    edit LoRA was dropped."""
    sid = _reference_session(client, settings={"checkpoint": "chosen.safetensors",
                                               "lora_strength": 0.9, "denoise": 0.55})
    db.run("""UPDATE workflow
                 SET graph=json_set(graph, '$."9"', json(?)),
                     node_map=json_set(node_map, '$.lora_name', '9.inputs.lora_name',
                                       '$.lora_strength', '9.inputs.strength_model')
               WHERE name='edit'""",
           json.dumps({"class_type": "LoraLoader",
                       "inputs": {"lora_name": "krea2_identity_edit.safetensors",
                                  "strength_model": 1.0, "model": ["1", 0]}}))

    r, fake = make_runner()
    asyncio.run(r._run_session(sid))
    assert [s["status"] for s in db.q("SELECT status FROM shot WHERE session_id=? ORDER BY id", sid)] \
        == ["done", "done"]

    anchor_graph, edit_graph = fake.graphs
    # The text2image take is unchanged: it is where those two belong.
    assert anchor_graph["1"]["inputs"]["ckpt_name"] == "chosen.safetensors"
    assert anchor_graph["2"]["inputs"]["lora_name"] == "characters/ada.safetensors"
    assert anchor_graph["2"]["inputs"]["strength_model"] == 0.9
    # The edit graph keeps everything it shipped with — mapped slots and all.
    assert edit_graph["1"]["inputs"]["ckpt_name"] == "edit.safetensors"
    assert edit_graph["9"]["inputs"]["lora_name"] == "krea2_identity_edit.safetensors"
    assert edit_graph["9"]["inputs"]["strength_model"] == 1.0
    # The slots that are the reference take's own still arrive.
    assert edit_graph["6"]["inputs"]["denoise"] == 0.55


def test_a_takes_own_reference_strength_wins_over_the_session(client, make_runner):
    """Finding this number means shooting one prompt at several values, which is
    one session with four takes — not four sessions."""
    sid = _reference_session(client, settings={"reference_strength": 4.0}, shots=[
        {"label": "anchor", "prompt": "standing", "count": 1},
        {"label": "loose", "prompt": "turn a little", "count": 1,
         "reference": True, "reference_strength": 1.5},
        {"label": "session", "prompt": "turn a little", "count": 1, "reference": True},
        # 0 is a real setting for this dial, so it must not read as "unset".
        {"label": "off", "prompt": "turn a little", "count": 1,
         "reference": True, "reference_strength": 0},
    ])
    # A float slot must stay a float: an int widget would truncate 1.5 to 1.
    db.run("UPDATE workflow SET graph=json_set(graph, '$.\"6\".inputs.denoise', 1.0), "
           "node_map=json_set(node_map, '$.reference_strength', '6.inputs.denoise') "
           "WHERE name='edit'")
    r, fake = make_runner()
    asyncio.run(r._run_session(sid))

    assert [s["status"] for s in db.q("SELECT status FROM shot WHERE session_id=? ORDER BY id", sid)] \
        == ["done"] * 4
    _, loose, from_session, off = fake.graphs
    assert loose["6"]["inputs"]["denoise"] == 1.5
    assert from_session["6"]["inputs"]["denoise"] == 4.0
    assert off["6"]["inputs"]["denoise"] == 0


def test_the_reference_a_shot_ran_against_is_pinned_to_it(client, make_runner):
    """The gallery's pick can change later; a before/after that compared against a
    photo the take never saw would be worse than showing no comparison."""
    sid = _reference_session(client)
    r, _ = make_runner()
    asyncio.run(r._run_session(sid))

    anchor, edit = db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)
    assert edit["reference_shot_ids"] == f"[{anchor['id']}]"
    assert anchor["reference_shot_ids"] == "[]"      # a plain take references nothing

    # Re-pointing the session afterwards leaves the finished take alone.
    db.run("UPDATE session SET anchor_shot_ids='[999]' WHERE id=?", sid)
    assert db.one("SELECT reference_shot_ids FROM shot WHERE id=?",
                  edit["id"])["reference_shot_ids"] == f"[{anchor['id']}]"


def test_the_first_photo_becomes_the_reference_by_itself(client, make_runner):
    """Otherwise queueing the anchor and its edits together could never work in
    one Run: every edit would fail on "no reference set" and need a retry."""
    sid = _reference_session(client)
    r, _ = make_runner()
    asyncio.run(r._run_session(sid))

    anchor = db.q("SELECT id FROM shot WHERE session_id=? ORDER BY id", sid)[0]
    assert db.one("SELECT anchor_shot_ids FROM session WHERE id=?", sid)["anchor_shot_ids"] \
        == f"[{anchor['id']}]"


def test_an_anchor_chosen_by_hand_is_not_overwritten(client, make_runner, tmp_path):
    """The gallery's pick wins: adopting one is a default, not a decision."""
    sid = _reference_session(client)
    kept = db.run(
        """INSERT INTO shot (session_id, shot_label, prompt, status, filename, created_at)
           VALUES (?,?,?,?,?,?)""", sid, "kept", "x", "done", "keeper.png", db.now())
    db.run("UPDATE session SET anchor_shot_ids=? WHERE id=?", f"[{kept}]", sid)
    (tmp_path / "sessions" / str(sid)).mkdir(parents=True)
    (tmp_path / "sessions" / str(sid) / "keeper.png").write_bytes(b"\x89PNG fake")

    r, fake = make_runner()
    asyncio.run(r._run_session(sid))

    assert db.one("SELECT anchor_shot_ids FROM session WHERE id=?", sid)["anchor_shot_ids"] \
        == f"[{kept}]"
    assert fake.uploads[0][1] == f"idevgen_ref_{kept}.png"


def test_a_reference_take_without_an_anchor_only_fails_itself(client, make_runner):
    sid = _reference_session(client, shots=[
        {"label": "edit", "prompt": "remove the jacket", "count": 1, "reference": True},
        {"label": "anchor", "prompt": "standing", "count": 1}])
    r, _ = make_runner()
    asyncio.run(r._run_session(sid))

    shots = db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)
    assert [s["status"] for s in shots] == ["failed", "done"]
    assert "reference photo" in shots[0]["error"]
    # One shot's failure is not the session's: a photo did come out.
    assert db.one("SELECT status FROM session WHERE id=?", sid)["status"] == "done"


def test_an_anchor_whose_file_vanished_fails_that_shot_readably(client, make_runner):
    sid = _reference_session(client)
    r, _ = make_runner()
    asyncio.run(r._run_session(sid))
    anchor = db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)[0]
    (r.sessions_dir / str(sid) / anchor["filename"]).unlink()

    db.run("UPDATE shot SET status='pending' WHERE session_id=? AND use_reference=1", sid)
    r2, _ = make_runner()
    asyncio.run(r2._run_session(sid))

    edit = db.q("SELECT * FROM shot WHERE session_id=? ORDER BY id", sid)[1]
    assert edit["status"] == "failed"
    assert "missing from disk" in edit["error"]


def test_two_sessions_at_once_are_refused(client, make_runner):
    sid = _session(client, count=1)
    r, _ = make_runner()

    async def scenario():
        r.start(sid)
        with pytest.raises(RuntimeError):
            r.start(sid)
        await r._task

    asyncio.run(scenario())
    assert db.one("SELECT status FROM session WHERE id=?", sid)["status"] == "done"


def _guide_run(client, make_runner, kind):
    """One reference session whose reference workflow is tagged `kind`, run."""
    sid = _reference_session(client, settings={"checkpoint": "chosen.safetensors",
                                               "lora_strength": 0.9, "denoise": 0.55})
    db.run("UPDATE workflow SET kind=? WHERE name='edit'", kind)
    r, fake = make_runner()
    asyncio.run(r._run_session(sid))
    statuses = [x["status"] for x in db.q("SELECT status FROM shot WHERE session_id=? ORDER BY id", sid)]
    assert statuses == ["done", "done"], statuses
    return fake.graphs[1]


def test_a_guide_take_keeps_the_character_model(client, make_runner):
    """The pop that makes an edit graph work is what made a guide graph impossible.

    An edit graph loads its own model and takes the character from the anchor, so
    the checkpoint and the character LoRA are dropped for it. A GUIDE graph paints
    from noise like any other take and reads its reference as conditioning — drop
    them there and it shoots somebody else. The test is on the graph's KIND, which
    is where the reason actually lives.

    Its twin below is the other half: same session, same anchor, same slots, and
    the only difference is the tag on the workflow.
    """
    ref_graph = _guide_run(client, make_runner, "guide")
    assert ref_graph["1"]["inputs"]["ckpt_name"] == "chosen.safetensors"
    assert ref_graph["2"]["inputs"]["image"].startswith("idevgen/idevgen_ref_")


def test_an_edit_take_still_drops_it(client, make_runner):
    """The twin of the test above: untouched behaviour for every other kind."""
    ref_graph = _guide_run(client, make_runner, "edit")
    assert ref_graph["1"]["inputs"]["ckpt_name"] == "edit.safetensors"


def test_a_takes_own_reference_photo_wins_over_the_sessions(client, make_runner, tmp_path):
    """A guide reference carries a pose or a garment, so two takes of one session
    routinely need two different photographs. The session's pick stays the
    default and the take overrides it, the same way `reference_strength` does."""
    sid = _reference_session(client)
    other = db.run(
        """INSERT INTO shot (session_id, shot_label, prompt, status, filename, created_at)
           VALUES (?,?,?,?,?,?)""", sid, "other", "x", "done", "other.png", db.now())
    (tmp_path / "sessions" / str(sid)).mkdir(parents=True, exist_ok=True)
    (tmp_path / "sessions" / str(sid) / "other.png").write_bytes(b"\x89PNG fake")

    guided = db.one("SELECT id FROM shot WHERE session_id=? AND use_reference=1", sid)["id"]
    assert client.patch(f"/api/shots/{guided}",
                        json={"reference_shot_ids": [other]}).status_code == 200

    r, fake = make_runner()
    asyncio.run(r._run_session(sid))
    assert db.one("SELECT status FROM shot WHERE id=?", guided)["status"] == "done",         db.one("SELECT error FROM shot WHERE id=?", guided)["error"]
    # The upload is the take's own photograph, not the anchor the session adopted.
    assert fake.uploads[0][1] == f"idevgen_ref_{other}.png"
    assert json.loads(db.one("SELECT reference_shot_ids FROM shot WHERE id=?",
                             guided)["reference_shot_ids"]) == [other]


def test_a_take_naming_a_photograph_that_has_none_is_refused(client):
    """Caught at the PATCH, not in the runner: a typo is a red field now, or a
    failed shot in the middle of a run later."""
    sid = _reference_session(client)
    empty = db.run("INSERT INTO shot (session_id, prompt, status, created_at) VALUES (?,?,?,?)",
                   sid, "x", "pending", db.now())
    guided = db.one("SELECT id FROM shot WHERE session_id=? AND use_reference=1", sid)["id"]
    r = client.patch(f"/api/shots/{guided}", json={"reference_shot_ids": [empty]})
    assert r.status_code == 400
    assert "no photograph" in r.json()["detail"]


def test_a_take_that_has_run_keeps_the_reference_it_ran_with(client, make_runner):
    """`ShotPatch` promised this in its docstring and nothing enforced it.

    The column is stamped at queue time so the row records what the take ACTUALLY
    ran against; repainting it afterwards leaves a before/after that compares
    against a photograph the take never saw, which is worse than no comparison.
    """
    sid = _reference_session(client)
    guided = db.one("SELECT id FROM shot WHERE session_id=? AND use_reference=1", sid)["id"]
    r, _ = make_runner()
    asyncio.run(r._run_session(sid))
    ran_with = db.one("SELECT reference_shot_ids, status FROM shot WHERE id=?", guided)
    assert ran_with["status"] == "done"

    anchor = db.one("SELECT id FROM shot WHERE session_id=? AND use_reference=0", sid)["id"]
    refused = client.patch(f"/api/shots/{guided}", json={"reference_shot_ids": [anchor]})
    assert refused.status_code == 409
    assert "keeps the reference it ran with" in refused.json()["detail"]
    assert db.one("SELECT reference_shot_ids FROM shot WHERE id=?",
                  guided)["reference_shot_ids"] == ran_with["reference_shot_ids"]

    # The rating still moves: the guard is on the one field that is a record.
    assert client.patch(f"/api/shots/{guided}", json={"rating": 5}).status_code == 200


def test_patching_a_shot_that_does_not_exist_is_a_404(client):
    """It answered 200 with a null body, so a typo in an id read as success."""
    assert client.patch("/api/shots/999999", json={"rating": 3}).status_code == 404
