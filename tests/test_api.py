"""HTTP routes: creating models and sessions, prompt composition, rating."""
import pytest

import db
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
    # trigger, model base prompt, the session's look, then the take — in that order.
    assert shots[0]["prompt"] == (
        "4da woman, photo, 35mm, white summer dress, hair down, on a beach, full body, walking")
    # An explicit {trigger} wins: it is not prepended a second time.
    assert shots[3]["prompt"] == (
        "photo, 35mm, white summer dress, hair down, on a beach, close-up of 4da woman")
    assert shots[0]["negative"] == "blurry"          # inherited from the model
    assert s["settings"]["width"] == 832             # settings inherited too
    assert s["look"] == "white summer dress, hair down, on a beach"


def test_the_look_is_identical_in_every_shot_of_a_session(client, seeded):
    """The point of a session: wardrobe and styling do not drift between takes."""
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "look": "red dress, gold earrings",
        "shots": [{"prompt": "standing", "count": 2}, {"prompt": "sitting", "count": 2}],
    }).json()["id"]
    prompts = [x["prompt"] for x in client.get(f"/api/sessions/{sid}").json()["shots"]]
    assert all("red dress, gold earrings" in p for p in prompts)
    assert len({p for p in prompts}) == 2            # only the take differs


def test_added_shots_keep_the_session_look_even_if_the_payload_lies(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "look": "red dress",
        "shots": [{"prompt": "standing", "count": 1}]}).json()["id"]
    client.post(f"/api/sessions/{sid}/shots",
                json={"look": "green coat", "shots": [{"prompt": "sitting", "count": 1}]})
    prompts = [x["prompt"] for x in client.get(f"/api/sessions/{sid}").json()["shots"]]
    assert all("red dress" in p and "green coat" not in p for p in prompts)


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

    assert shots[0]["prompt"] == "4da woman, photo, 35mm, leather jacket, hair up, standing"
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
