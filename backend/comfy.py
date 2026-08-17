"""ComfyUI client + workflow node mapping.

A workflow here is a graph in ComfyUI **API format** (Save -> Export (API)) plus a
`node_map`: {slot: "<node_id>.inputs.<field>"} telling us which widget to patch
for each thing a session varies (prompt, seed, lora, size...). `detect_map`
proposes that mapping by walking the graph, so importing a workflow is one click
and the user only fixes what it got wrong.
"""
from __future__ import annotations

import copy
import json
import mimetypes

import httpx

# Slots a session can drive. Anything not present in a workflow's map is simply
# not patched — the workflow's own widget value stands.
SLOTS = [
    "positive", "negative", "seed", "steps", "cfg",
    # Measured across seven Krea 2 finetunes on 2026-08-16: each one names its own
    # sampler and scheduler (euler / euler_ancestral / er_sde / res_2s, over
    # simple / beta / bong_tangent). Mapping the pair is what lets one graph serve
    # every checkpoint instead of one graph per checkpoint differing by two words.
    "sampler", "scheduler",
    "width", "height", "checkpoint", "lora_name", "lora_strength", "filename_prefix",
    # Reference (image-to-image / instruction editing). `reference` is the anchor
    # photo; the extra two are for graphs that take several — Kontext and
    # Qwen-Image-Edit accept a character plus a garment or a background.
    "reference", "reference2", "reference3", "reference_strength", "denoise",
]

REFERENCE_SLOTS = ("reference", "reference2", "reference3")

# The widget that names the base model. `ckpt_name` is the all-in-one checkpoint
# (SDXL and friends); `unet_name` is the diffusion model loaded on its own, which
# is how Flux, Krea and Z-Image are wired. A workflow has one or the other.
CHECKPOINT_FIELDS = ("ckpt_name", "unet_name")

# Where uploaded anchors land inside ComfyUI's input/, so they stay together and
# are recognisable next to whatever the user drops there by hand.
UPLOAD_SUBFOLDER = "idevgen"

_SAMPLER_CLASSES = ("KSampler", "SamplerCustom", "KSamplerAdvanced")
_LATENT_CLASSES = ("EmptyLatentImage", "EmptySD3LatentImage", "EmptyLatentImagePresets")


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    async def _get(self, path: str):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{self.url}{path}")
            r.raise_for_status()
            return r.json()

    async def stats(self) -> dict:
        return await self._get("/system_stats")

    async def loras(self) -> list[str]:
        info = await self._get("/object_info/LoraLoader")
        return info["LoraLoader"]["input"]["required"]["lora_name"][0]

    async def base_models(self) -> dict[str, list[str]]:
        """What can go in the checkpoint slot, by loader kind, plus what the
        sampler and scheduler slots accept — one call, because every screen that
        offers a base model offers the sampler pair beside it.

        Every list is optional: a ComfyUI without `UNETLoader` (or with no
        diffusion models on disk) still answers for checkpoints, and a missing
        node type must not break the dropdown."""
        async def widget(node: str, field: str) -> list[str]:
            try:
                info = await self._get(f"/object_info/{node}")
                return info[node]["input"]["required"][field][0]
            except Exception:  # noqa: BLE001 - node absent or list empty
                return []

        return {"checkpoints": await widget("CheckpointLoaderSimple", "ckpt_name"),
                "unets": await widget("UNETLoader", "unet_name"),
                # Free-typing these is how `bong_tangent` becomes `bong_tangeant`
                # and a run dies at the queue. KSampler is the one node every
                # graph here has, and it carries the full list.
                "samplers": await widget("KSampler", "sampler_name"),
                "schedulers": await widget("KSampler", "scheduler")}

    async def queue_prompt(self, graph: dict, client_id: str) -> str:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.url}/prompt", json={"prompt": graph, "client_id": client_id})
            if r.status_code >= 400:
                raise ComfyError(_error_text(r))
            return r.json()["prompt_id"]

    async def history(self, prompt_id: str) -> dict | None:
        data = await self._get(f"/history/{prompt_id}")
        return data.get(prompt_id)

    async def upload_image(self, path, name: str) -> str:
        """Put a local file in ComfyUI's `input/` and return what a LoadImage takes.

        Uploading rather than copying is what keeps this free of any one ComfyUI
        layout: `config.json` knows `output/` and `models/loras`, never `input/`,
        and a ComfyUI on another machine shares no filesystem at all. `overwrite`
        is on so re-sending the same anchor replaces it instead of piling up
        `anchor (1).png` copies; LoadImage re-reads a changed file.
        """
        # Declare what the file actually is: an imported reference can be a JPEG
        # or a WebP, and calling everything a PNG is a claim that happens to work
        # only because ComfyUI reads the bytes rather than the header.
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        files = {"image": (name, path.read_bytes(), mime)}
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{self.url}/upload/image", files=files,
                             data={"overwrite": "true", "subfolder": UPLOAD_SUBFOLDER})
            if r.status_code >= 400:
                raise ComfyError(_error_text(r))
            out = r.json()
        subfolder = out.get("subfolder") or ""
        return f"{subfolder}/{out['name']}" if subfolder else out["name"]

    async def interrupt(self) -> None:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(f"{self.url}/interrupt")

    async def queue_state(self) -> dict:
        return await self._get("/queue")


def _error_text(r: httpx.Response) -> str:
    try:
        data = r.json()
        err = data.get("error") or {}
        msg = err.get("message") or json.dumps(data)[:400]
        details = err.get("details") or ""
        node_errs = data.get("node_errors") or {}
        if node_errs:
            msg += " | " + json.dumps(node_errs)[:400]
        return f"{msg} {details}".strip()
    except Exception:
        return r.text[:400]


# --------------------------------------------------------------------------- map

def detect_map(graph: dict) -> dict:
    """Best-effort {slot: "node.inputs.field"} for an API-format graph."""
    out: dict[str, str] = {}

    def cls(nid: str) -> str:
        return graph.get(nid, {}).get("class_type", "")

    def has(nid: str, field: str) -> bool:
        return field in graph.get(nid, {}).get("inputs", {})

    sampler = next(
        (nid for nid, n in graph.items()
         if n.get("class_type", "").startswith(_SAMPLER_CLASSES) or
         ("positive" in n.get("inputs", {}) and "negative" in n.get("inputs", {}))),
        None,
    )
    if sampler:
        for field in ("seed", "noise_seed"):
            if has(sampler, field):
                out["seed"] = f"{sampler}.inputs.{field}"
                break
        for field in ("steps", "cfg", "denoise"):
            if has(sampler, field):
                out[field] = f"{sampler}.inputs.{field}"
        # The slot is `sampler`; the widget ComfyUI puts it in is `sampler_name`.
        for slot, field in (("sampler", "sampler_name"), ("scheduler", "scheduler")):
            if has(sampler, field):
                out[slot] = f"{sampler}.inputs.{field}"
        # positive/negative are links: ["<node_id>", slot]
        for slot in ("positive", "negative"):
            link = graph[sampler]["inputs"].get(slot)
            found = _walk_to_text(graph, link)
            if found:
                nid, field = found
                out[slot] = f"{nid}.inputs.{field}"

    for nid, node in graph.items():
        ctype = node.get("class_type", "")
        ins = node.get("inputs", {})
        if ctype in _LATENT_CLASSES or ("width" in ins and "height" in ins and "width" not in out):
            if isinstance(ins.get("width"), (int, float)):
                out.setdefault("width", f"{nid}.inputs.width")
                out.setdefault("height", f"{nid}.inputs.height")
        if "checkpoint" not in out:
            for field in CHECKPOINT_FIELDS:
                if isinstance(ins.get(field), str):
                    out["checkpoint"] = f"{nid}.inputs.{field}"
                    break
        if "lora_name" in ins and "lora_name" not in out:
            out["lora_name"] = f"{nid}.inputs.lora_name"
            for field in ("strength_model", "strength"):
                if field in ins:
                    out["lora_strength"] = f"{nid}.inputs.{field}"
                    break
        if "filename_prefix" in ins and "filename_prefix" not in out:
            out["filename_prefix"] = f"{nid}.inputs.filename_prefix"
        # Each LoadImage takes the next free reference slot, in graph order. A
        # graph with one is plain img2img; Kontext and Qwen-Image-Edit take more.
        if ctype == "LoadImage" and isinstance(ins.get("image"), str):
            slot = next((s for s in REFERENCE_SLOTS if s not in out), None)
            if slot:
                out[slot] = f"{nid}.inputs.image"
        # Only IPAdapter's own weight. A bare `weight` widget sits on half the
        # nodes of a busy graph, and mapping the wrong one drives something else
        # entirely every time the strength is changed.
        if ("IPAdapter" in ctype and "reference_strength" not in out
                and isinstance(ins.get("weight"), (int, float))):
            out["reference_strength"] = f"{nid}.inputs.weight"

    return out


# What a node calls the widget holding the prompt. `text` is CLIPTextEncode and
# almost everything that imitates it; `prompt` is what the instruction-editing
# encoders use, where the text is an order rather than a caption.
TEXT_FIELDS = ("text", "prompt")


def _walk_to_text(graph: dict, link, depth: int = 0) -> tuple[str, str] | None:
    """Follow a link back to the node owning a literal prompt widget.

    Returns (node_id, field). Conditioning rarely comes straight from
    CLIPTextEncode — it passes through ConditioningCombine / FluxGuidance /
    ControlNet nodes. Without the walk the prompt slot silently goes unmapped on
    most real workflows.
    """
    if depth > 6 or not (isinstance(link, list) and link):
        return None
    nid = str(link[0])
    node = graph.get(nid)
    if not node:
        return None
    for field in TEXT_FIELDS:
        if isinstance(node.get("inputs", {}).get(field), str):
            return nid, field
    for value in node.get("inputs", {}).values():
        found = _walk_to_text(graph, value, depth + 1)
        if found:
            return found
    return None


def graph_checkpoint(graph: dict, node_map: dict | None = None) -> str:
    """The base model this graph loads on its own.

    A graph tuned for one checkpoint — its sampler, its steps, its cfg — names
    that checkpoint in its own loader, so the graph already knows which model it
    is for and nothing has to be typed twice. That is what lets picking a base
    model pick the workflow written for it.
    """
    path = (node_map or {}).get("checkpoint")
    if path:
        nid, _, field = path.split(".", 2)
        value = (graph.get(nid) or {}).get("inputs", {}).get(field.replace("inputs.", "", 1))
        if isinstance(value, str):
            return value
    # An unmapped loader still names a model, and leaving the slot unmapped is
    # exactly what a per-model graph does — so the fallback is the common case.
    for node in (graph or {}).values():
        for field in CHECKPOINT_FIELDS:
            value = (node.get("inputs") or {}).get(field)
            if isinstance(value, str):
                return value
    return ""


def apply_map(graph: dict, node_map: dict, values: dict) -> dict:
    """Return a copy of `graph` with each mapped slot patched to `values[slot]`."""
    g = copy.deepcopy(graph)
    for slot, value in values.items():
        path = node_map.get(slot)
        if not path or value is None:
            continue
        nid, _, field = path.split(".", 2)
        field = field.replace("inputs.", "", 1)
        node = g.get(nid)
        if not node or field not in node.get("inputs", {}):
            continue
        current = node["inputs"][field]
        if isinstance(current, list):
            continue  # a link, not a widget — patching it would break the graph
        # Keep the widget's own type: ComfyUI rejects a float in an INT seed slot.
        if isinstance(current, bool):
            node["inputs"][field] = bool(value)
        elif isinstance(current, int):
            node["inputs"][field] = int(value)
        elif isinstance(current, float):
            node["inputs"][field] = float(value)
        else:
            node["inputs"][field] = value
    return g


def output_images(history: dict) -> list[dict]:
    """[{filename, subfolder, type}] from a /history entry, SaveImage nodes only."""
    images = []
    for node_out in (history.get("outputs") or {}).values():
        for img in node_out.get("images", []) or []:
            if img.get("type") == "temp":
                continue
            images.append(img)
    return images
