"""The prompt assistant: tidying what a model answers, and the route around it.

No network: the httpx client is replaced by a double that records the request and
returns a canned completion, the same way `FakeComfy` stands in for ComfyUI.
"""
from __future__ import annotations

import base64

import httpx
import pytest
from fastapi import HTTPException

import enhance

ANGLES = ["front view", "front-left quarter view", "left side view", "back view",
          "eye-level shot", "low-angle shot", "close-up", "medium shot", "full shot"]

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake").decode()


class _Resp:
    def __init__(self, status: int, payload, text: str = ""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def llm(monkeypatch):
    """Swap the LLM client for a double. Returns the dict it records into."""
    seen: dict = {}

    def _install(content: str = "a line", *, status: int = 200, payload=..., error=None,
                 rejects_reasoning: bool = False):
        seen["bodies"] = []

        class Client:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None, headers=None):
                seen.update(url=url, body=json, headers=headers)
                seen["bodies"].append(json)
                if error:
                    raise error
                if rejects_reasoning and "reasoning_effort" in json:
                    return _Resp(400, {}, text="Unrecognized request argument: reasoning_effort")
                body = {"choices": [{"message": {"content": content}}]} if payload is ... else payload
                return _Resp(status, body, text=content)

        monkeypatch.setattr(enhance.httpx, "AsyncClient", Client)
        return seen

    return _install


@pytest.fixture
def configured(client):
    """Point the app at an LLM endpoint, through the real config route."""
    client.patch("/api/config", json={"comfy_url": "http://127.0.0.1:8188",
                                      "llm_url": "http://127.0.0.1:11434/v1",
                                      "llm_model": "small",
                                      "llm_vision_model": "small-vl"})
    yield client
    client.patch("/api/config", json={"comfy_url": "http://127.0.0.1:8188"})


# ---------------------------------------------------------------- the gate

def test_unconfigured_says_which_setting(client):
    client.patch("/api/config", json={"comfy_url": "http://127.0.0.1:8188"})
    assert client.get("/api/config").json()["llm_ok"] is False
    r = client.post("/api/enhance", json={"instruction": "write a take"})
    assert r.status_code == 400
    assert "Setup" in r.json()["detail"]


def test_config_keeps_the_llm_keys(client):
    """`save_config` writes model_dump() over the file: a key missing from the
    schema is a key deleted on the next save."""
    client.patch("/api/config", json={"comfy_url": "http://127.0.0.1:8188",
                                      "llm_url": "http://x/v1", "llm_model": "m",
                                      "llm_key": "secret"})
    out = client.get("/api/config").json()
    assert (out["llm_url"], out["llm_model"], out["llm_key"]) == ("http://x/v1", "m", "secret")
    assert out["llm_ok"] is True


# ---------------------------------------------------------------- cleaning

def test_strips_fences_numbering_quotes_and_preamble():
    answer = ('Here are four takes:\n'
              '```text\n'
              '1. "full body, walking, mid-stride"\n'
              '```\n')
    assert enhance.clean(answer, 1) == [{"label": "", "prompt": "full body, walking, mid-stride"}]


def test_splits_label_from_prompt_on_the_first_bar():
    lines = enhance.clean("walking | full body, mid-stride | still", 1)
    assert lines == [{"label": "walking", "prompt": "full body, mid-stride | still"}]


def test_a_line_without_a_bar_is_all_prompt():
    assert enhance.clean("close-up, eyes to camera", 1)[0]["label"] == ""


def test_n_truncates_but_fewer_is_accepted():
    six = "\n".join(f"l{i} | p{i}" for i in range(6))
    assert len(enhance.clean(six, 4)) == 4
    assert len(enhance.clean("a\nb\nc", 4)) == 3


def test_a_reasoning_model_keeps_its_reasoning_to_itself():
    answer = "<think>\nMaybe walking?\nOr sitting.\n</think>\nfull body, walking"
    assert enhance.clean(answer, 4) == [{"label": "", "prompt": "full body, walking"}]


def test_the_same_line_twice_is_one_line():
    """Four identical takes are one take with four variations, which the count
    box already does."""
    assert len(enhance.clean("walking\nWALKING\nsitting", 4)) == 2


def test_clamp_keeps_the_vocabulary_and_drops_the_rest():
    out = enhance.clean("right side view, eye-level shot, dramatic lighting, 8k",
                        1, ANGLES + ["right side view"])
    assert out[0]["prompt"] == "eye-level shot right side view"


def test_clamp_prefers_the_longer_phrase():
    """`front view` is not what `front-left quarter view` means."""
    out = enhance.clean("front-left quarter view close-up", 1, ANGLES)
    assert out[0]["prompt"] == "front-left quarter view close-up"


def test_clamp_drops_a_line_with_nothing_from_the_vocabulary():
    assert enhance.clean("dramatic lighting", 1, ANGLES) == []


# ---------------------------------------------------------------- the request

def test_context_and_vocabulary_reach_the_model(configured, llm):
    seen = llm("from behind | back view, close-up, dramatic lighting")
    r = configured.post("/api/enhance", json={
        "instruction": "write a take", "context": "white dress, on a beach",
        "n": 2, "allowed": ANGLES})
    assert r.status_code == 200
    assert r.json()["lines"] == [{"label": "from behind", "prompt": "back view close-up"}]
    sent = seen["body"]["messages"][-1]["content"]
    assert "white dress, on a beach" in sent
    assert "Do not repeat" in sent and "contradicts" in sent
    assert "back view" in sent
    assert seen["url"].endswith("/chat/completions")
    assert seen["body"]["model"] == "small"


def test_an_endpoint_url_that_is_already_complete_is_left_alone(client, llm):
    client.patch("/api/config", json={"comfy_url": "http://127.0.0.1:8188",
                                      "llm_url": "http://x/v1/chat/completions",
                                      "llm_model": "m"})
    seen = llm("a")
    client.post("/api/enhance", json={"instruction": "write"})
    assert seen["url"] == "http://x/v1/chat/completions"


def test_thinking_is_asked_for_and_dropped_if_refused(configured, llm):
    """A reasoning model spends ten times the tokens thinking about four short
    lines as writing them. An endpoint that does not know the parameter gets the
    same request again without it, rather than an error."""
    seen = llm("a line", rejects_reasoning=True)
    r = configured.post("/api/enhance", json={"instruction": "x"})
    assert r.status_code == 200
    assert [("reasoning_effort" in b) for b in seen["bodies"]] == [True, False]


def test_a_key_is_sent_as_a_bearer_token(client, llm):
    client.patch("/api/config", json={"comfy_url": "http://127.0.0.1:8188",
                                      "llm_url": "http://x/v1", "llm_model": "m",
                                      "llm_key": "abc"})
    seen = llm("a")
    client.post("/api/enhance", json={"instruction": "write"})
    assert seen["headers"]["Authorization"] == "Bearer abc"


# ---------------------------------------------------------------- images

def test_a_picked_photo_travels_with_the_vision_model(configured, llm):
    seen = llm("a black dress, thin straps")
    r = configured.post("/api/enhance", json={"instruction": "read the wardrobe", "image": PNG})
    assert r.status_code == 200
    parts = seen["body"]["messages"][-1]["content"]
    assert parts[-1]["image_url"]["url"] == PNG
    assert seen["body"]["model"] == "small-vl"


def test_a_body_that_is_not_an_image_is_refused(configured, llm):
    llm("a")
    r = configured.post("/api/enhance", json={"instruction": "x", "image": "data:text/html,<b>"})
    assert r.status_code == 400


def test_an_oversized_image_is_refused(configured, llm):
    llm("a")
    big = "data:image/png;base64," + "A" * (enhance.MAX_IMAGE_BYTES // 3 * 4 + 8)
    r = configured.post("/api/enhance", json={"instruction": "x", "image": big})
    assert r.status_code == 413


def test_a_shot_with_no_photo_is_one_readable_line(configured, llm, seeded):
    llm("a")
    sid = configured.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s",
        "shots": [{"prompt": "walking"}]}).json()["id"]
    shot_id = configured.get(f"/api/sessions/{sid}").json()["shots"][0]["id"]
    r = configured.post("/api/enhance", json={"instruction": "x", "shot_id": shot_id})
    assert r.status_code == 400
    assert "\n" not in r.json()["detail"]


def test_a_shot_photo_wins_over_a_body_image(configured, llm, seeded):
    """A photo the app owns is one we can name; the body is whatever the browser
    read off the disk."""
    seen = llm("a")
    sid = configured.post("/api/sessions", json={
        "model_id": seeded["model_id"], "name": "s", "shots": []}).json()["id"]
    shot = configured.post(f"/api/sessions/{sid}/import", content=b"\x89PNG\r\n\x1a\n data").json()
    configured.post("/api/enhance", json={"instruction": "x", "shot_id": shot["id"], "image": PNG})
    sent = seen["body"]["messages"][-1]["content"][-1]["image_url"]["url"]
    assert sent.startswith("data:image/png;base64,") and sent != PNG


# ---------------------------------------------------------------- discovery

TAGS = {"models": [
    {"name": "qwen3.5:9b", "details": {"families": ["qwen35"], "parameter_size": "9.0B"}},
    {"name": "llava:latest", "details": {"families": ["llama", "clip"], "parameter_size": "7B"}},
    {"name": "qwen2.5vl:7b", "details": {"families": ["qwen25vl"], "parameter_size": "8.3B"}},
    {"name": "llama3.2-vision:11b", "details": {"families": ["mllama"], "parameter_size": "10.7B"}},
]}


@pytest.fixture
def endpoints(monkeypatch):
    """Fake hosts answering `/v1/models` and, for an Ollama, `/api/tags` and
    `/api/show`. `caps` is {model: [capability]}; a model missing from it is one
    whose manifest predates the field, so the families decide instead."""
    def _install(answering: dict, tags: dict | None = TAGS, caps: dict | None = None,
                 listing_status: int = 200):
        asked: list[str] = []
        sent_headers: dict = {}

        class Client:
            def __init__(self, **kwargs):
                sent_headers.update(kwargs.get("headers") or {})

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, json=None, headers=None):
                asked.append(url)
                if not url.endswith("/api/show"):
                    raise httpx.ConnectError("no such route")
                return _Resp(200, {"capabilities": (caps or {}).get(json["model"])})

            async def get(self, url):
                asked.append(url)
                if url.endswith("/api/tags"):
                    # `tags=None` is an endpoint that is not Ollama: no such route.
                    if tags is None:
                        raise httpx.ConnectError("no such route")
                    return _Resp(200, tags)
                base = url[: -len("/models")]
                if base not in answering:
                    raise httpx.ConnectError("connection refused")
                if listing_status >= 400:
                    return _Resp(listing_status, {})
                return _Resp(200, {"data": [{"id": i} for i in answering[base]]})

        monkeypatch.setattr(enhance.httpx, "AsyncClient", Client)
        return {"asked": asked, "headers": sent_headers}

    return _install


def test_probing_finds_the_first_endpoint_that_answers(client, endpoints):
    endpoints({enhance.PROBE_URLS[1]: ["a-model"]})
    out = client.post("/api/llm/models", json={}).json()
    assert out["url"] == enhance.PROBE_URLS[1]
    assert out["models"] == [{"id": "a-model", "vision": False, "params": 0.0}]


def test_the_list_arrives_biggest_first(client, endpoints):
    """The one worth offering by default, not the one that sorts first by name."""
    endpoints({"http://x/v1": [m["name"] for m in TAGS["models"]]})
    out = client.post("/api/llm/models", json={"url": "http://x/v1"}).json()
    assert [m["id"] for m in out["models"]][:2] == ["llama3.2-vision:11b", "qwen3.5:9b"]


def test_what_ollama_reports_wins_over_the_families(client, endpoints):
    """A current model can read photos with nothing in its families saying so —
    Qwen3.5 is natively multimodal and calls itself `qwen35`. Only
    `/api/show` knows, so only `/api/show` decides when it answers."""
    tags = {"models": [
        {"name": "new:9b", "details": {"families": ["qwen35"], "parameter_size": "9B"}},
        {"name": "old:9b", "details": {"families": ["llama"], "parameter_size": "9B"}},
    ]}
    endpoints({"http://x/v1": ["new:9b", "old:9b"]}, tags=tags,
              caps={"new:9b": ["completion", "vision"], "old:9b": ["completion"]})
    models = client.post("/api/llm/models", json={"url": "http://x/v1"}).json()["models"]
    assert {m["id"]: m["vision"] for m in models} == {"new:9b": True, "old:9b": False}


def test_a_bolted_on_clip_tower_sorts_under_the_rest(client, endpoints):
    """Bigger, older and worse at reading a garment: offered, but not first."""
    tags = {"models": [
        {"name": "llava:13b", "details": {"families": ["llama", "clip"], "parameter_size": "13B"}},
        {"name": "native:8b", "details": {"families": ["qwen3vl"], "parameter_size": "8B"}},
    ]}
    endpoints({"http://x/v1": ["llava:13b", "native:8b"]}, tags=tags)
    models = client.post("/api/llm/models", json={"url": "http://x/v1"}).json()["models"]
    assert [m["id"] for m in models] == ["native:8b", "llava:13b"]


def test_vision_is_read_from_the_families_not_the_name(client, endpoints):
    """Names that say nothing: `clip` and `mllama` are vision towers, a family
    ending in `vl` is a Qwen-VL, and a plain text family is not."""
    tags = {"models": [
        {"name": "one:8b", "details": {"families": ["llama", "clip"], "parameter_size": "8B"}},
        {"name": "two:8b", "details": {"families": ["mllama"], "parameter_size": "8B"}},
        {"name": "three:8b", "details": {"families": ["qwen25vl"], "parameter_size": "8B"}},
        {"name": "four:8b", "details": {"families": ["qwen35"], "parameter_size": "8B"}},
    ]}
    endpoints({"http://x/v1": [m["name"] for m in tags["models"]]}, tags=tags)
    models = client.post("/api/llm/models", json={"url": "http://x/v1"}).json()["models"]
    assert {m["id"]: m["vision"] for m in models} == {
        "one:8b": True, "two:8b": True, "three:8b": True, "four:8b": False}


def test_an_endpoint_that_says_nothing_claims_nothing(client, endpoints):
    """A hosted endpoint has no `/api/tags` to ask, and guessing from the name is
    a list of fragments that goes stale. Nothing is claimed, and the screen then
    offers every model for the photo job instead of a lucky few."""
    endpoints({"http://x/v1": ["plain-8b", "some-vl-7b"]}, tags=None)
    models = client.post("/api/llm/models", json={"url": "http://x/v1"}).json()["models"]
    assert [m["vision"] for m in models] == [False, False]


def test_a_full_completions_url_is_accepted(client, endpoints):
    endpoints({"http://x/v1": ["m"]})
    out = client.post("/api/llm/models", json={"url": "http://x/v1/chat/completions"}).json()
    assert out["url"] == "http://x/v1"


def test_an_endpoint_that_lists_nothing_is_still_an_endpoint(client, endpoints):
    """A hosted API that only serves chat — or one whose listing needs a key it
    was not given — is perfectly usable: the model name is typed in. Refusing it
    would be the app calling a working endpoint broken."""
    endpoints({"http://api/v1": []}, tags=None, listing_status=401)
    out = client.post("/api/llm/models", json={"url": "http://api/v1"})
    assert out.status_code == 200
    assert out.json() == {"url": "http://api/v1", "models": []}


def test_a_url_nothing_answers_at_is_still_an_error(client, endpoints):
    endpoints({}, tags=None)
    r = client.post("/api/llm/models", json={"url": "http://typo/v1"})
    assert r.status_code == 404
    assert "http://typo/v1" in r.json()["detail"]


def test_the_key_is_sent_when_listing(client, endpoints):
    """A hosted endpoint lists nothing without it."""
    seen = endpoints({"http://api/v1": ["m"]}, tags=None)
    client.post("/api/llm/models", json={"url": "http://api/v1", "key": "secret"})
    assert seen["headers"]["Authorization"] == "Bearer secret"


def test_a_pasted_completions_url_is_reduced_to_its_base(client, endpoints):
    """Providers spell that last part differently — MiniMax's own older path is
    `/text/chatcompletion_v2`."""
    seen = endpoints({"http://api/v1": ["m"]}, tags=None)
    out = client.post("/api/llm/models",
                      json={"url": "http://api/v1/text/chatcompletion_v2"}).json()
    assert out["url"] == "http://api/v1"
    assert "http://api/v1/models" in seen["asked"]


def test_nothing_listening_names_the_ports_it_tried(client, endpoints):
    endpoints({})
    r = client.post("/api/llm/models", json={})
    assert r.status_code == 404
    assert "11434" in r.json()["detail"]


# ---------------------------------------------------------------- failures

def test_an_endpoint_that_is_down_names_the_url(configured, llm):
    llm(error=httpx.ConnectError("connection refused"))
    r = configured.post("/api/enhance", json={"instruction": "x"})
    assert r.status_code == 502
    assert "127.0.0.1:11434" in r.json()["detail"]
    assert "Traceback" not in r.json()["detail"]


def test_a_timeout_still_says_something(configured, llm):
    """`str(ReadTimeout())` is the empty string, and "did not answer: " with
    nothing after it is the least useful line this app could print."""
    llm(error=httpx.ReadTimeout(""))
    detail = configured.post("/api/enhance", json={"instruction": "x"}).json()["detail"]
    assert "ReadTimeout" in detail and str(enhance.TIMEOUT) in detail


def test_a_non_200_is_one_sentence(configured, llm):
    llm("model not found", status=404)
    r = configured.post("/api/enhance", json={"instruction": "x"})
    assert r.status_code == 502
    assert "404" in r.json()["detail"]


def test_an_answer_that_is_not_a_completion_is_refused(configured, llm):
    llm(payload={"unexpected": True})
    r = configured.post("/api/enhance", json={"instruction": "x"})
    assert r.status_code == 502


def test_the_explicit_register_rides_on_the_system_message():
    """A standing rule about how to write belongs in SYSTEM, not in paragraph
    thirty of the caller's brief — which is where it was while the writer went on
    hedging the act into a pose."""
    plain = enhance._messages(enhance.EnhanceIn(instruction="write a line"), "")
    assert enhance.EXPLICIT_SYSTEM not in plain[0]["content"]

    explicit = enhance._messages(
        enhance.EnhanceIn(instruction="write a line", register="explicit"), "")
    assert explicit[0]["role"] == "system"
    assert enhance.EXPLICIT_SYSTEM in explicit[0]["content"]
    # And it does not leak into what the caller asked for.
    assert "penetrating her" not in explicit[1]["content"]


def test_the_register_says_which_of_the_two_bodies_is_introduced():
    """The same lesson twice. Written out in the caller's brief, with its
    measurement attached, it was ignored: nineteen lines of twenty opened `a naked
    man and a naked woman`, and the one the repair could not fix came back as a
    photograph of two women. Moved here, 0 of 20 did it."""
    assert "a naked woman" in enhance.EXPLICIT_SYSTEM
    assert "a naked man" in enhance.EXPLICIT_SYSTEM


def test_fields_come_back_as_headed_blocks():
    """The writer answers in fields; the join is where they become a prompt.

    Headed blocks and not one paragraph, because the paragraph is what made the
    photographs look like a paid shoot: measured 2026-08-21, three photographs
    shot on one seed each came back as a lit set written as ~300 words of prose
    and as a phone snapshot written as blocks, which is also the shape the
    workflow's own default prompt is written in. An empty field brings no
    heading with it.
    """
    answer = ('```json\n{"photographs": [{"camera": "Taken from her left side, a waist-up '
              'photograph", "act": "a naked man behind her, his penis inside her.", '
              '"him": "his bare chest above her back", "face": ""}]}\n```')
    assert enhance.clean_fields(answer, ["camera", "act", "him", "face"], 4) == [
        {"label": "", "prompt": "Angle & Framing:\nTaken from her left side, a waist-up "
                                "photograph.\n\nPose:\na naked man behind her, his penis "
                                "inside her.\n\nSecond Subject:\nhis bare chest above her "
                                "back."}]


def test_a_field_set_that_is_not_the_shoot_still_joins_flat():
    """The headings belong to the six fields of a photograph. Anything else — a
    caller asking for two fields of its own — keeps the join it always had rather
    than growing headings nobody wrote."""
    answer = '{"photographs": [{"a": "one", "b": "two"}]}'
    assert enhance.clean_fields(answer, ["a", "b"], 1) == [
        {"label": "", "prompt": "one. two."}]


def test_an_answer_that_is_not_json_says_so():
    """`response_format` is a request, not a guarantee: every endpoint here is
    OpenAI-*compatible*, and a 502 that names the problem beats a line of prose
    queued as a prompt."""
    with pytest.raises(HTTPException) as exc:
        enhance.clean_fields("Here are your photographs:", ["camera"], 4)
    assert exc.value.status_code == 502


def test_a_truncated_answer_keeps_the_photographs_that_closed():
    """Measured, one answer in eighteen: the model runs out of room in the middle
    of the fourth photograph and the outer object never closes. Throwing all four
    away for the sake of the fourth turns a short round — which the caller simply
    asks again for — into a dead shoot."""
    answer = ('{"photographs": [{"camera": "Taken from her left side", "act": "she stands"}, '
              '{"camera": "Taken from behind her", "act": "she kneels"}, '
              '{"camera": "Taken from directly in front of her", "act": "she lea')
    out = enhance.clean_fields(answer, ["camera", "act"], 4)
    assert [r["prompt"] for r in out] == [
        "Angle & Framing:\nTaken from her left side.\n\nPose:\nshe stands.",
        "Angle & Framing:\nTaken from behind her.\n\nPose:\nshe kneels."]


def test_the_headings_name_the_fields_the_writer_is_asked_for():
    """`SHOOT_FIELDS` lives in the frontend and `BLOCK_HEADINGS` in the backend,
    and nothing joins them: a field added to one and not the other loses its
    heading and lands in the flat fallback, which is the format measured to come
    back as a lit set instead of a phone snapshot. `technique` was added to both
    by hand once already."""
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[1]
           / "frontend/src/kinds.js").read_text(encoding="utf-8")
    listed = re.search(r"SHOOT_FIELDS\s*=\s*\[(.*?)\]", src, re.S)
    assert listed, "SHOOT_FIELDS is no longer a literal array in kinds.js"
    fields = re.findall(r"'([^']+)'", listed.group(1))
    assert fields == list(enhance.BLOCK_HEADINGS), (fields, enhance.BLOCK_HEADINGS)
