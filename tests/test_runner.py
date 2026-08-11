"""The runner against a fake ComfyUI: queueing, real graph patching, moving the
file into the session, and what happens when something goes wrong."""
import asyncio

import pytest

import db
import runner as runner_mod
from conftest import GRAPH


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
    assert g["3"]["inputs"]["text"] == "4da woman, on the beach"
    assert g["4"]["inputs"]["text"] == "blurry"
    assert g["6"]["inputs"]["seed"] == 77
    assert g["6"]["inputs"]["steps"] == 8
    assert g["5"]["inputs"]["width"] == 832 and g["5"]["inputs"]["height"] == 1216
    assert g["2"]["inputs"]["lora_name"] == "characters/ada.safetensors"
    assert g["2"]["inputs"]["strength_model"] == 0.85     # the session override
    assert g["1"]["inputs"]["ckpt_name"] == "base.safetensors"   # untouched: none chosen
    # Own prefix: never collides with what you generate by hand in ComfyUI.
    assert g["8"]["inputs"]["filename_prefix"].startswith(f"idevgen/{sid}/")


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
