"""Independent Task 9.6 checks for the shared image transport boundary."""
from __future__ import annotations

import base64
from io import BytesIO

import httpx
from PIL import Image
import pytest

import db
import enhance
import main


@pytest.fixture(autouse=True)
def _clear_assistant_configuration(client):
    yield
    client.patch("/api/config", json={"comfy_url": "http://127.0.0.1:8188"})


def _image_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 3), (23, 81, 144)).save(stream, format="PNG")
    return stream.getvalue()


def _data_uri(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _configure(client, *, url="http://assistant.local/v1", text_model="shared-model", vision_model=""):
    response = client.patch("/api/config", json={
        "comfy_url": "http://127.0.0.1:8188",
        "llm_url": url,
        "llm_model": text_model,
        "llm_vision_model": vision_model,
    })
    assert response.status_code == 200, response.text


def _assert_vision_error(response, status: int, code: str) -> str:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict), detail
    assert detail.get("code") == code, detail
    message = detail.get("message")
    assert isinstance(message, str) and message.strip(), detail
    return message


class _Response:
    def __init__(self, status: int, payload, text: str = ""):
        self.status_code = status
        self.payload = payload
        self.text = text

    def json(self):
        if self.payload is None:
            raise ValueError("not JSON")
        return self.payload


@pytest.fixture
def llm(monkeypatch):
    """Record the actual completion request without opening a network socket."""
    seen: dict = {}

    def install(content: str = "a line", *, status: int = 200, payload=..., error=None):
        seen["bodies"] = []

        class Client:
            def __init__(self, **kwargs):
                seen["timeout"] = kwargs.get("timeout")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None, headers=None):
                seen.update(url=url, body=json, headers=headers)
                seen["bodies"].append(json)
                if error:
                    raise error
                body = ({"choices": [{"message": {"content": content}}]}
                        if payload is ... else payload)
                return _Response(status, body, text=content if isinstance(content, str) else "")

        monkeypatch.setattr(enhance.httpx, "AsyncClient", Client)
        return seen

    return install


@pytest.mark.parametrize("vision_model", ["", " \t\r\n", None, 0, [], {}])
def test_image_without_a_valid_explicit_vision_model_is_rejected_before_http(
    client, llm, monkeypatch, vision_model,
):
    _configure(client)
    config = dict(main.CONFIG)
    config["llm_vision_model"] = vision_model
    monkeypatch.setattr(main, "CONFIG", config)
    seen = llm("must not be requested")

    response = client.post("/api/enhance", json={
        "instruction": "Read the visible clothing.",
        "image": _data_uri(_image_bytes()),
    })

    message = _assert_vision_error(response, 409, "vision_unavailable")
    assert "vision" in message.lower()
    assert "manual" in message.lower() or "setup" in message.lower() or "configure" in message.lower()
    assert seen["bodies"] == []
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == 0


@pytest.mark.parametrize("field", ["llm_url", "llm_model"])
@pytest.mark.parametrize("invalid_value", ["", " \t\r\n", None, [], {}])
def test_image_still_requires_the_ordinary_text_endpoint_and_model(
    client, llm, monkeypatch, field, invalid_value,
):
    _configure(client, vision_model="declared-vision-model")
    config = dict(main.CONFIG)
    config[field] = invalid_value
    monkeypatch.setattr(main, "CONFIG", config)
    seen = llm("must not be requested")

    response = client.post("/api/enhance", json={
        "instruction": "Read the visible clothing.",
        "image": _data_uri(_image_bytes()),
    })

    message = _assert_vision_error(response, 409, "vision_unavailable")
    assert "vision" in message.lower()
    assert "manual" in message.lower() or "setup" in message.lower() or "configure" in message.lower()
    assert seen["bodies"] == []


@pytest.mark.parametrize("field", ["llm_url", "llm_model"])
def test_text_request_without_ordinary_assistant_configuration_keeps_400(
    client, llm, monkeypatch, field,
):
    _configure(client, vision_model="declared-vision-model")
    config = dict(main.CONFIG)
    config[field] = " \t\r\n"
    monkeypatch.setattr(main, "CONFIG", config)
    seen = llm("must not be requested")

    response = client.post("/api/enhance", json={"instruction": "Write a take."})

    assert response.status_code == 400, response.text
    assert seen["bodies"] == []


def test_same_text_and_vision_model_is_sent_explicitly_for_image_but_text_model_for_text(client, llm):
    _configure(client, text_model="shared-vision", vision_model="shared-vision")
    seen = llm("close-up portrait")

    image_response = client.post("/api/enhance", json={
        "instruction": "Read the visible clothing.",
        "image": _data_uri(_image_bytes()),
    })
    text_response = client.post("/api/enhance", json={"instruction": "Write a take."})

    assert image_response.status_code == 200, image_response.text
    assert text_response.status_code == 200, text_response.text
    assert [body["model"] for body in seen["bodies"]] == ["shared-vision", "shared-vision"]
    image_parts = seen["bodies"][0]["messages"][-1]["content"]
    assert image_parts[-1]["image_url"]["url"] == _data_uri(_image_bytes())
    assert text_response.json() == {"lines": [{"label": "", "prompt": "close-up portrait"}]}


def test_nonempty_operator_declared_vision_id_is_sent_without_discovery(client, llm):
    _configure(client, text_model="writer-model", vision_model="operator-declared-model")
    seen = llm("blue jacket")

    response = client.post("/api/enhance", json={
        "instruction": "Read the visible clothing.",
        "image": _data_uri(_image_bytes()),
    })

    assert response.status_code == 200, response.text
    assert seen["body"]["model"] == "operator-declared-model"


@pytest.mark.parametrize("failure", ["refusal", "timeout", "invalid_json", "empty_fields"])
def test_visual_failures_are_safe_and_leave_saved_looks_and_photo_preview_untouched(
    client, llm, failure,
):
    _configure(client, vision_model="declared-vision-model")
    image = _image_bytes()
    upload = client.post(
        "/api/looks/photo-stages",
        files={"file": ("invented-photo.png", image, "image/png")},
    )
    assert upload.status_code == 201, upload.text
    photo_id = upload.json()["photo_id"]
    preview_path = f"/api/looks/photo-stages/{photo_id}/preview"
    before_preview = client.get(preview_path)
    assert before_preview.status_code == 200
    assert before_preview.content == image
    before_stage = client.get(f"/api/looks/photo-stages/{photo_id}").json()
    before_looks = client.get("/api/looks").json()
    before_look_count = db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"]

    if failure == "refusal":
        seen = llm("refused", status=400, payload={"error": {"message": "private provider refusal"}})
        request = {"instruction": "Read the visible clothing.", "image": _data_uri(image)}
    elif failure == "timeout":
        seen = llm(error=httpx.ReadTimeout(""))
        request = {"instruction": "Read the visible clothing.", "image": _data_uri(image)}
    else:
        content = ("This is not structured JSON." if failure == "invalid_json"
                   else '{"photographs": [null]}')
        seen = llm(content)
        request = {
            "instruction": "Read the visible clothing.",
            "image": _data_uri(image),
            "fields": ["appearance", "garments"],
        }

    response = client.post("/api/enhance", json=request)

    assert len(seen["bodies"]) == 1
    assert db.one("SELECT COUNT(*) AS n FROM saved_look_version")["n"] == before_look_count == 0
    assert client.get("/api/looks").json() == before_looks
    after_stage = client.get(f"/api/looks/photo-stages/{photo_id}").json()
    assert after_stage["state"] == before_stage["state"] == "staged"
    assert after_stage["saved_look"] is None
    after_preview = client.get(preview_path)
    assert after_preview.status_code == 200
    assert after_preview.content == image
    message = _assert_vision_error(response, 502, "vision_request_failed")
    assert "private provider refusal" not in message
    assert "traceback" not in message.lower()


def test_visual_fields_keep_the_historical_flattened_lines_shape(client, llm):
    _configure(client, vision_model="declared-vision-model")
    seen = llm('{"photographs": [{"appearance": "short hair", "wardrobe": "blue coat"}]}')

    response = client.post("/api/enhance", json={
        "instruction": "Read the visible appearance and clothes.",
        "image": _data_uri(_image_bytes()),
        "fields": ["appearance", "wardrobe"],
    })

    assert response.status_code == 200, response.text
    assert response.json() == {"lines": [{"label": "", "prompt": "short hair. blue coat."}]}
    assert seen["body"]["model"] == "declared-vision-model"
    assert seen["body"]["response_format"] == {"type": "json_object"}


@pytest.mark.anyio
@pytest.mark.parametrize("content", ["not valid JSON", "[]"])
async def test_structured_image_invalid_json_or_root_shape_is_a_vision_failure(llm, content):
    config = {
        "llm_url": "http://assistant.local/v1",
        "llm_model": "text-model",
        "llm_vision_model": "declared-vision-model",
    }
    seen = llm(content)

    with pytest.raises(enhance.HTTPException) as exc:
        await enhance.run_structured(
            config,
            enhance.EnhanceIn(instruction="Read the visible clothing.", image=_data_uri(_image_bytes())),
        )

    assert exc.value.status_code == 502
    assert exc.value.detail["code"] == "vision_request_failed"
    assert seen["body"]["model"] == "declared-vision-model"
