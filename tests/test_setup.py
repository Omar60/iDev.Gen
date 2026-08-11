"""The Setup screen's routes: reading, detecting and writing config.json."""
import json

import pytest

import main


@pytest.fixture(autouse=True)
def _restore_config():
    """These tests rewrite live globals; put them back so test order stays free."""
    saved = (main.CONFIG, main.COMFY_OUTPUT, main.LORA_DIR, main.comfy.url,
             main.runner.comfy_output_dir)
    yield
    (main.CONFIG, main.COMFY_OUTPUT, main.LORA_DIR, main.comfy.url,
     main.runner.comfy_output_dir) = saved


def test_config_is_created_from_the_example_on_first_read(client):
    cfg = client.get("/api/config").json()
    assert cfg["comfy_url"]
    assert main.CONFIG_PATH.exists()
    # A fresh clone starts with empty paths and an unhappy status, not a guess.
    assert "output_dir_ok" in cfg and "data_dir_resolved" in cfg


def test_saving_config_writes_the_file_and_applies_it_live(client, tmp_path):
    out = tmp_path / "comfy" / "output"
    out.mkdir(parents=True)
    r = client.patch("/api/config", json={
        "comfy_url": "http://127.0.0.1:9999/", "comfy_output_dir": str(out),
        "lora_dir": "", "data_dir": "data"})
    assert r.status_code == 200
    assert r.json()["restart_required"] is False

    saved = json.loads(main.CONFIG_PATH.read_text(encoding="utf-8"))
    assert saved["comfy_output_dir"] == str(out)
    # Applied without a restart: the client and the runner see the new values.
    assert main.comfy.url == "http://127.0.0.1:9999"
    assert main.runner.comfy_output_dir == out
    assert client.get("/api/config").json()["output_dir_ok"] is True


def test_a_folder_that_does_not_exist_is_refused(client, tmp_path):
    r = client.patch("/api/config", json={
        "comfy_url": "http://127.0.0.1:8188", "comfy_output_dir": str(tmp_path / "nope"),
        "lora_dir": "", "data_dir": "data"})
    assert r.status_code == 400
    assert "not an existing folder" in r.json()["detail"]


def test_changing_the_data_folder_asks_for_a_restart(client, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    r = client.patch("/api/config", json={
        "comfy_url": "http://127.0.0.1:8188", "comfy_output_dir": str(out),
        "lora_dir": "", "data_dir": str(tmp_path / "elsewhere")})
    assert r.json()["restart_required"] is True


def test_detect_proposes_the_folders_next_to_comfyui(client, tmp_path, monkeypatch):
    comfy_root = tmp_path / "ComfyUI"
    (comfy_root / "output").mkdir(parents=True)
    (comfy_root / "models" / "loras").mkdir(parents=True)

    async def fake_stats():
        return {"system": {"argv": [str(comfy_root / "main.py"), "--listen"]}}

    monkeypatch.setattr(main.comfy, "stats", fake_stats)
    d = client.post("/api/config/detect").json()
    assert d["comfy_root"] == str(comfy_root)
    assert d["comfy_output_dir"] == {"path": str(comfy_root / "output"), "exists": True}
    assert d["lora_dir"] == {"path": str(comfy_root / "models" / "loras"), "exists": True}


def test_base_models_survive_a_comfyui_without_unetloader(client, monkeypatch):
    """An older ComfyUI has no UNETLoader; the dropdown must still list
    checkpoints instead of failing outright."""
    async def only_checkpoints(path):
        if "CheckpointLoaderSimple" in path:
            return {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sdxl.safetensors"]]}}}}
        raise RuntimeError("404 Not Found")

    monkeypatch.setattr(main.comfy, "_get", only_checkpoints)
    assert client.get("/api/comfy/models").json() == {
        "checkpoints": ["sdxl.safetensors"], "unets": []}


def test_detect_reports_when_comfyui_is_unreachable(client, monkeypatch):
    async def boom():
        raise ConnectionError("connection refused")

    monkeypatch.setattr(main.comfy, "stats", boom)
    r = client.post("/api/config/detect")
    assert r.status_code == 502
    assert "not responding" in r.json()["detail"]
