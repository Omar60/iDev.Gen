"""Prompt help: an optional LLM writes the take, the look, or the angle line.

The endpoint is OpenAI-compatible (`/chat/completions`), which is one code path
for Ollama, LM Studio, OpenRouter and OpenAI alike. It is off until `llm_url`
and `llm_model` are set in config.json, and the UI hides the buttons rather than
failing on click.

This module stays **kind-blind**, the same way the rest of the backend is: the
caller sends the instruction — the per-kind guidance lives in `frontend/kinds.js`
next to the rules the UI already prints — and gets clean lines back. What lives
here is the part with actual logic and an actual test: tidying what a small local
model returns, and clamping a closed vocabulary.

Nothing here writes to the database or queues anything. It suggests text.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

# Long because the first call to a local model pays for loading it: an 8B vision
# model took 67 seconds to reach VRAM on a card ComfyUI had been using, before it
# had even looked at the photo. A hosted endpoint never comes close to this.
TIMEOUT = 300

# A photo the LLM has to read, not a photo to generate from: the browser scales
# what it picks down to 1024px, and this is only the wall that stops a raw
# camera file from being base64'd into a JSON body.
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_DATA_URI = re.compile(r"^data:image/(png|jpe?g|webp);base64,[A-Za-z0-9+/=\s]+$")

# Where a local assistant usually listens. Probed in order, and only when the
# user has not typed a URL of their own — the same idea as asking ComfyUI where
# it lives instead of hardcoding an install layout.
PROBE_URLS = (
    "http://127.0.0.1:11434/v1",   # Ollama
    "http://127.0.0.1:1234/v1",    # LM Studio
    "http://127.0.0.1:8080/v1",    # llama.cpp server
)
PROBE_TIMEOUT = 2

# `/v1/models` says what is installed, never what it can do. Ollama's `/api/show`
# does, in `capabilities`, and that is the only source worth trusting: a current
# model can be natively multimodal with nothing in its architecture families
# saying so (Qwen3.5 reads photos and calls itself `qwen35`).
#
# Families are the fallback for a model whose manifest predates `capabilities`:
# `clip` is a vision tower bolted on (llava, moondream, bakllava), `mllama` is
# Llama 3.2 Vision, and a family ending in `vl` is a Qwen-VL.
_VISION_FAMILIES = {"clip", "mllama"}
# A bolted-on CLIP tower is the 2023 design, and it shows on exactly the job this
# app has: reading a garment off a photo. Still offered, but under the models
# that read the image natively rather than above them on size alone.
_LEGACY_VISION_FAMILY = "clip"

# What is added to SYSTEM when the caller asks for the explicit register. Four
# lines, and each one earns its place: the first bans the manoeuvre rather than
# the words (a list of forbidden euphemisms is a game of whack-a-mole — ban
# `moving against her` and back comes `arched into him`), the second names the
# vocabulary, the third says it holds for every line so the model never has to
# decide whether this photograph qualifies.
#
# The fourth is here for the same reason the other three are, and it is the
# second time this project has learned it: the rule was written out at length in
# the caller's own brief and ignored anyway — nineteen lines of twenty opened `a
# naked man and a naked woman`, and the one that survived the repair came back as
# a photograph of two women. Where a standing rule about how to write is READ
# turns out to matter more than how firmly it is worded.
EXPLICIT_SYSTEM = (
    " This request is explicit and you write it explicitly: never soften it, "
    "never imply it, never substitute a pose for the act, and never answer a "
    "request for sex with a request for a photograph of two people near each "
    "other. Name what is happening in plain anatomical words — penis, vagina, "
    "inside her, penetrating her, his hips against hers — and where a body part "
    "or an act is involved, say which. This holds for every line you write here, "
    "with no line left to be inferred."
    " Only one of the two bodies is ever introduced. The subject is already named "
    "at the front of the prompt you are writing a fragment of, so she is `her` and "
    "nothing else — never `a naked woman`, never `a young woman`, never `the same "
    "woman` — while the second body is introduced plainly as `a naked man`. Naming "
    "the two of them symmetrically is the mistake, whatever words it uses: a "
    "photograph asked for as `a naked man and a naked woman` is painted as two "
    "women."
)

SYSTEM = (
    "You write fragments of image-generation prompts. Output the fragments and "
    "nothing else: no numbering, no bullets, no quotes, no explanation, no "
    "preamble, one per line. Never repeat anything the user tells you is already "
    "in the prompt. "
    # The destination is a text encoder trained on English captions, so a prompt
    # written in the language of the request is a prompt the model half reads.
    "Always answer in English, whatever language the request is written in: "
    "translate what it asks for, and never copy words from it verbatim."
)

# The same standing rules, for a caller that asks for fields instead of lines.
# Only the output clause differs, and it has to: "no quotes, one per line" is the
# opposite of a JSON body, and a system message that bans quotes while the user
# message asks for JSON is two texts disagreeing about the answer.
JSON_SYSTEM = SYSTEM.replace(
    "Output the fragments and nothing else: no numbering, no bullets, no quotes, "
    "no explanation, no preamble, one per line. ",
    "Answer with one JSON object and nothing else: no prose around it, no markdown "
    "fence. ",
)


class EnhanceIn(BaseModel):
    """What to write. `instruction` carries the caller's own guidance."""
    instruction: str
    text: str = ""              # the line being rewritten; empty = write from scratch
    context: str = ""           # what is ALREADY in the prompt, so it is not repeated
    n: int = 1                  # 1 = one rewrite, >1 = that many different lines
    allowed: list[str] = Field(default_factory=list)  # closed vocabulary to clamp to
    shot_id: int | None = None  # a photo the app already has
    image: str = ""             # a photo just picked, as a data: URI
    # The register the caller writes in. Only the shoot writer sets it, and only
    # to "explicit". It rides on the SYSTEM message rather than in `instruction`
    # on purpose: a standing rule about how to write belongs where the model
    # reads it first and once, not in paragraph thirty of a two-thousand word
    # brief where it competes with thirty-nine other rules — which is where it
    # was, and where the model went on hedging the act into a pose.
    register: str = ""
    # The fields a line arrives in, when the caller wants one JSON object per
    # line instead of one line of prose. Empty is the old behaviour and the
    # default; anything else asks the endpoint for `json_object` and joins the
    # fields back into the one line the caller gets either way.
    #
    # Measured 2026-08-20, 12 runs an arm on one explicit brief: asked as prose,
    # the second body was described in 18 per cent of lines; given a field of its
    # own it is described in 83, and both of the two rules that decide whether a
    # two-person photograph renders hold at once for the first time. The gain is
    # the field, not the JSON - a JSON container around the same single prose
    # line scored 4 per cent, worse than prose.
    fields: list[str] = Field(default_factory=list)


def configured(config: dict) -> bool:
    return bool(config.get("llm_url") and config.get("llm_model"))


async def run(config: dict, p: EnhanceIn, image: str = "") -> list[dict]:
    """Ask the model, and hand back at most `p.n` clean {label, prompt} lines."""
    if not configured(config):
        raise HTTPException(400, "No prompt assistant is configured. Open Setup and "
                                 "fill in the LLM endpoint and model.")
    image = image or p.image
    if image:
        _check_image(image)

    url = config["llm_url"].rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    model = (config.get("llm_vision_model") or config["llm_model"]) if image else config["llm_model"]

    body = {"model": model, "messages": _messages(p, image), "temperature": 0.8,
            "stream": False,
            **({"response_format": {"type": "json_object"}} if p.fields else {}),
            # A reasoning model spends ten times the tokens thinking about four
            # short lines than it does writing them — minutes instead of seconds,
            # for a task with nothing to reason about. An endpoint that does not
            # know the parameter is retried without it below.
            "reasoning_effort": "none"}
    headers = {"Authorization": f"Bearer {config['llm_key']}"} if config.get("llm_key") else {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(url, json=body, headers=headers)
            if r.status_code == 400 and "reasoning" in r.text.lower():
                plain = {k: v for k, v in body.items() if k != "reasoning_effort"}
                r = await c.post(url, json=plain, headers=headers)
    except httpx.HTTPError as exc:  # noqa: BLE001 - the URL is the useful half
        # A timeout stringifies to nothing at all, and "did not answer: " with
        # nothing after it is the least useful error this app could print.
        raise HTTPException(502, f"The prompt assistant at {url} did not answer: "
                                 f"{str(exc) or type(exc).__name__} "
                                 f"(gave up after {TIMEOUT}s)")
    if r.status_code >= 400:
        # Same rule as a failed shot: the sentence that says what broke, not the
        # provider's whole error document.
        raise HTTPException(502, f"The prompt assistant at {url} answered "
                                 f"{r.status_code}: {r.text[:300]}")
    try:
        answer = r.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(502, f"The prompt assistant at {url} answered something "
                                 f"that is not an OpenAI-compatible completion.")
    if p.fields:
        return clean_fields(answer, p.fields, p.n)
    return clean(answer, p.n, p.allowed)


async def discover(url: str = "", key: str = "") -> dict:
    """Find an assistant and list what it can run.

    With a URL it asks that one; without, it probes the ports a local assistant
    usually listens on. Nothing is written to the config — the Setup screen
    proposes, the user saves, exactly like the ComfyUI detection.
    """
    if url.strip():
        base = _endpoint(url)
        models = await _models(base, key)
        # Answered, but with no list of its own: a hosted endpoint that only
        # serves chat is perfectly usable, and the model name is then typed. Only
        # a URL nothing answers at all is an error.
        if models is None:
            raise HTTPException(404, f"Nothing answered at {base}. Check the URL — it is the "
                                     f"OpenAI-compatible base, usually ending in /v1.")
        return {"url": base, "models": models}
    for base in PROBE_URLS:
        models = await _models(base, key)
        if models:
            return {"url": base, "models": models}
    raise HTTPException(404, "No assistant found on " + ", ".join(
        u.split("//")[1].split("/")[0] for u in PROBE_URLS)
        + ". Start Ollama or LM Studio, or type the endpoint by hand.")


def _endpoint(url: str) -> str:
    """The base an OpenAI-compatible client talks to.

    Pasting the completions URL itself is the obvious mistake, and providers
    spell that last part differently — `/chat/completions`, and MiniMax's older
    `/text/chatcompletion_v2`. Anything with `completion` in the last segment is
    the endpoint, not the base."""
    base = url.strip().rstrip("/")
    head, _, tail = base.rpartition("/")
    if "completion" not in tail.lower():
        return base
    base = head
    head, _, tail = base.rpartition("/")
    return head if tail.lower() in ("chat", "text") else base


async def _models(base: str, key: str = "") -> list[dict] | None:
    """What the endpoint says it has. `[]` when it answered but lists nothing —
    a hosted endpoint that only serves chat — and `None` when nothing answered."""
    # Short on connect so probing a port nothing listens on gives up at once;
    # patient on read, because listing what an assistant has is one request per
    # model and a cold Ollama answers the first one slowly.
    timeout = httpx.Timeout(20, connect=PROBE_TIMEOUT)
    # A hosted endpoint refuses to list anything without it.
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as c:
            r = await c.get(f"{base}/models")
            if r.status_code >= 400:
                return []
            ids = [m["id"] for m in r.json().get("data", []) if m.get("id")]
            detail = await _ollama_detail(c, base)
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None
    out = []
    for i in ids:
        # `vision` is what the endpoint said, never what the name suggests: an
        # endpoint that reports nothing gets `false` across the board, and the
        # screen then offers every model for the job rather than the handful a
        # list of name fragments would have recognised this year.
        vision, params, legacy = detail.get(i) or (False, 0.0, False)
        out.append({"id": i, "params": params, "_legacy": legacy, "vision": bool(vision)})
    # Biggest first — the one worth offering by default, and a list of nineteen
    # tags sorted by name buries it in the middle — with the bolted-on CLIP
    # towers under everything else whatever their size.
    out.sort(key=lambda m: (m["_legacy"], -m["params"], m["id"]))
    return [{k: v for k, v in m.items() if k != "_legacy"} for m in out]


async def _ollama_detail(client: httpx.AsyncClient, base: str
                         ) -> dict[str, tuple[bool, float, bool]]:
    """{name: (reads photos, billions of parameters, old vision design)}.

    Ollama's own routes, which unlike `/v1/models` say what each model *is*. Any
    other endpoint has no such route, and then nothing here is known about any of
    them — which the screen turns into "offer them all"."""
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    try:
        r = await client.get(f"{root}/api/tags")
        models = r.json()["models"]
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return {}
    # One `/api/show` per model, all at once: locally that is under a second, and
    # it is the only answer that is right for a model whose families say nothing.
    caps = await asyncio.gather(*(_capabilities(client, root, m["name"]) for m in models))
    out = {}
    for m, capability in zip(models, caps):
        details = m.get("details") or {}
        families = details.get("families") or []
        by_family = any(f in _VISION_FAMILIES or f.endswith("vl") for f in families)
        out[m["name"]] = (
            "vision" in capability if capability else by_family,
            _billions(details.get("parameter_size")),
            _LEGACY_VISION_FAMILY in families,
        )
    return out


async def _capabilities(client: httpx.AsyncClient, root: str, name: str) -> set[str]:
    """What Ollama says a model can do. Empty when it says nothing — an older
    manifest has no such field, and then the families decide."""
    try:
        r = await client.post(f"{root}/api/show", json={"model": name})
        return set(r.json().get("capabilities") or [])
    except (httpx.HTTPError, TypeError, ValueError):
        return set()


def _billions(size: str | None) -> float:
    """"8.8B" -> 8.8, and "832.06M" -> 0.83: Ollama writes the small ones in
    millions. Anything it does not write sorts last rather than breaking the
    list."""
    text = str(size).upper().strip()
    try:
        return float(text[:-1]) / (1000 if text.endswith("M") else 1)
    except (TypeError, ValueError):
        return 0.0


def _check_image(image: str) -> None:
    if not _DATA_URI.match(image.strip()):
        raise HTTPException(400, "that is not a PNG, JPEG or WebP data: URI")
    payload = image.split(",", 1)[1]
    if len(payload) * 3 // 4 > MAX_IMAGE_BYTES:
        raise HTTPException(413, f"the image is over {MAX_IMAGE_BYTES // (1024 * 1024)} MB; "
                                 f"pick a smaller one")


def _messages(p: EnhanceIn, image: str) -> list[dict]:
    parts = [p.instruction]
    if p.context:
        # Repeating it wastes the sampler's attention; contradicting it is worse,
        # because the two halves fight and the photo comes back wearing whichever
        # won. Both have to be said, and said as one rule.
        parts.append("This is already in the prompt. Do not repeat any of it, and do not "
                     f"write anything that contradicts it:\n{p.context}")
    if p.allowed:
        parts.append("Use only these words, exactly as written, and nothing else:\n"
                     + "\n".join(p.allowed))
    if p.n > 1:
        # Says how many lines and where they end — never what goes inside one.
        # "One per line and nothing else" used to be the wording, and it read as a
        # ban on the `section | text` format the look instruction asks for: the
        # model dropped the section names, and with them the sections it was
        # supposed to account for. The caller owns the shape of a line.
        parts.append(f"Write {p.n} lines. Nothing before the first and nothing after the last.")
    parts.append(f"Rewrite this:\n{p.text}" if p.text.strip() else "")

    text = "\n\n".join(x for x in parts if x)
    system = (JSON_SYSTEM if p.fields else SYSTEM) + (
        EXPLICIT_SYSTEM if p.register == "explicit" else "")
    if not image:
        return [{"role": "system", "content": system}, {"role": "user", "content": text}]
    return [{"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": text},
                                         {"type": "image_url", "image_url": {"url": image}}]}]


_NOISE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_FENCE = re.compile(r"^\s*```")
# A reasoning model reasons in the open unless the endpoint splits it off, and
# its reasoning is many lines that all look like candidate takes.
_THINK = re.compile(r"^.*?</think>", re.DOTALL)


def clean(answer: str, n: int, allowed: list[str] | None = None) -> list[dict]:
    """A small local model answers with fences, numbering, quotes and a "Here are
    four takes:" line however plainly you ask it not to. Everything below is a
    shape one of them actually returns."""
    out: list[dict] = []
    seen: set[str] = set()
    answer = _THINK.sub("", answer, count=1)
    for raw in answer.splitlines():
        line = raw.strip()
        if not line or _FENCE.match(line):
            continue
        line = _NOISE.sub("", line).strip().strip("*").strip()
        # A preamble ("Here are four takes:"), never a prompt: no fragment of a
        # prompt ends in a colon.
        if not line or line.endswith(":"):
            continue
        label, _, prompt = line.partition("|")
        label, prompt = (label.strip(), prompt.strip()) if prompt.strip() else ("", label.strip())
        prompt = _unquote(prompt)
        if allowed:
            prompt = _clamp(prompt, allowed)
        # A model asked for four takes often answers with one, four times over —
        # and four identical takes are one take with four variations, which the
        # count box already does. Dropped here so no caller has to think about it.
        if prompt and prompt.lower() not in seen:
            seen.add(prompt.lower())
            out.append({"label": _unquote(label)[:60], "prompt": prompt})
        if len(out) >= max(1, n):
            break
    return out


def _objects(text: str, fields: list[str]) -> list[dict]:
    """Every `{...}` in the text that parses on its own and carries one of the
    fields asked for, in order.

    Depth-counted rather than regex, and every closing brace is tried rather than
    only the outermost: the answer that needs salvaging is the one whose OUTER
    object never closes, because the model ran out of room in the middle of the
    fourth photograph. The three that did close are still whole photographs.
    """
    wanted, out, stack, in_string, escaped = set(fields), [], [], False, False
    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            try:
                row = json.loads(text[stack.pop():i + 1])
            except ValueError:
                continue
            if isinstance(row, dict) and wanted & set(row):
                out.append(row)
    return out


# What each field of a photograph is called in the joined prompt.
#
# The workflow's own default prompt is written this way — `Style & Medium:`,
# `Angle & Framing:`, `Pose:` — and so are the prompts this checkpoint was
# demonstrably happy with. Measured 2026-08-21 on three photographs shot on one
# seed each: the same content as one 300-word paragraph came back as a lit set
# three times of three, and as headed blocks it came back looking like a phone
# snapshot, the same as the 87-word compression did. The fields were already
# there; only the glue between them changed.
BLOCK_HEADINGS = {
    "camera": "Angle & Framing",
    "act": "Pose",
    "her": "Subject",
    "him": "Second Subject",
    "worn": "Outfit & Texture",
    "technique": "Technique",
    "face": "Expression",
}


def clean_fields(answer: str, fields: list[str], n: int) -> list[dict]:
    """One JSON object per photograph, joined back into the one line the caller
    always gets.

    The transport is the only thing that changed: a field still holds prose, and
    what comes back here is the same `{label, prompt}` row `clean` returns. What
    the fields buy is that a body cannot be skipped by a model running out of
    room — measured, the second body goes from 18 per cent of lines to 83.

    Joined with `. ` and not `, `: each field is a sentence of its own, and a
    comma between two sentences reads as one run-on clause in a prompt that is
    parsed as a bag of phrases anyway.
    """
    answer = _THINK.sub("", answer, count=1).strip()
    # A model told "no markdown fence" writes one anyway often enough to be worth
    # three characters of slicing, and `response_format` is a request, not a
    # guarantee: every endpoint here is OpenAI-*compatible*, not OpenAI.
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end < start:
        raise HTTPException(502, "The prompt assistant was asked for JSON and answered "
                                 "something else.")
    try:
        parsed = json.loads(answer[start:end + 1])
        rows = parsed.get("photographs") if isinstance(parsed, dict) else parsed
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        # One malformed answer in eighteen runs, measured: the object is fine and
        # something after it is not — a stray array, a trailing field. Whole-body
        # parsing throws all four photographs away for the sake of the fourth, and
        # the caller asks again for a SHORT round but dies on an error. So the
        # objects are salvaged one at a time, and only an answer with nothing
        # parsable in it is a failure.
        rows = _objects(answer[start:], fields)
    if not rows:
        raise HTTPException(502, "The prompt assistant was asked for JSON and answered "
                                 "nothing that parses as photographs.")

    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # Trailing punctuation of its own as well as the full stop: a field that
        # ends `head to feet,` joined to the next one reads `head to feet,. She`.
        said = [(f, str(row.get(f) or "").strip().rstrip(" .,;:")) for f in fields]
        said = [(f, text) for f, text in said if text]
        if all(f in BLOCK_HEADINGS for f, _ in said):
            line = "\n\n".join(f"{BLOCK_HEADINGS[f]}:\n{text}." for f, text in said)
        else:
            line = ". ".join(text for _, text in said) + "."
        if line:
            out.append({"label": "", "prompt": line})
        if len(out) >= max(1, n):
            break
    return out


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text


def _clamp(text: str, allowed: list[str]) -> str:
    """Keep only the phrases from the closed vocabulary, in the order they were
    given — which is axis order, so the line reads direction, height, framing.

    The angle LoRA ignores everything outside its vocabulary, so a model that
    adds `dramatic lighting` is not being helpful, it is writing a word the
    generation will drop. Longest first, because one phrase can contain another.
    """
    haystack = text.lower()
    found = set()
    for phrase in sorted(allowed, key=len, reverse=True):
        at = haystack.find(phrase.lower())
        if at >= 0:
            found.add(phrase)
            haystack = haystack[:at] + " " * len(phrase) + haystack[at + len(phrase):]
    return " ".join(phrase for phrase in allowed if phrase in found)
