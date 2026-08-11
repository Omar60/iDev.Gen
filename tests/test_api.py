"""HTTP routes: creating models and sessions, prompt composition, rating."""
import pytest

import db
from conftest import GRAPH


def test_importing_a_workflow_autodetects_the_map(client):
    r = client.post("/api/workflows", json={"name": "wf", "graph": GRAPH})
    assert r.status_code == 200
    assert r.json()["node_map"]["positive"] == "3.inputs.text"
    assert client.get("/api/workflows").json()[0]["name"] == "wf"


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


def test_missing_image_returns_404(client, seeded):
    sid = client.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "one", "count": 1}]}).json()["id"]
    shot_id = client.get(f"/api/sessions/{sid}").json()["shots"][0]["id"]
    assert client.get(f"/api/shots/{shot_id}/image").status_code == 404
