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

import httpx

# Slots a session can drive. Anything not present in a workflow's map is simply
# not patched — the workflow's own widget value stands.
SLOTS = [
    "positive", "negative", "seed", "steps", "cfg",
    "width", "height", "checkpoint", "lora_name", "lora_strength", "filename_prefix",
]

# The widget that names the base model. `ckpt_name` is the all-in-one checkpoint
# (SDXL and friends); `unet_name` is the diffusion model loaded on its own, which
# is how Flux, Krea and Z-Image are wired. A workflow has one or the other.
CHECKPOINT_FIELDS = ("ckpt_name", "unet_name")

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
        """What can go in the checkpoint slot, by loader kind.

        Both lists are optional: a ComfyUI without `UNETLoader` (or with no
        diffusion models on disk) still answers for checkpoints, and a missing
        node type must not break the dropdown."""
        async def widget(node: str, field: str) -> list[str]:
            try:
                info = await self._get(f"/object_info/{node}")
                return info[node]["input"]["required"][field][0]
            except Exception:  # noqa: BLE001 - node absent or list empty
                return []

        return {"checkpoints": await widget("CheckpointLoaderSimple", "ckpt_name"),
                "unets": await widget("UNETLoader", "unet_name")}

    async def queue_prompt(self, graph: dict, client_id: str) -> str:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.url}/prompt", json={"prompt": graph, "client_id": client_id})
            if r.status_code >= 400:
                raise ComfyError(_error_text(r))
            return r.json()["prompt_id"]

    async def history(self, prompt_id: str) -> dict | None:
        data = await self._get(f"/history/{prompt_id}")
        return data.get(prompt_id)

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
        for field in ("steps", "cfg"):
            if has(sampler, field):
                out[field] = f"{sampler}.inputs.{field}"
        # positive/negative are links: ["<node_id>", slot]
        for slot in ("positive", "negative"):
            link = graph[sampler]["inputs"].get(slot)
            text_node = _walk_to_text(graph, link)
            if text_node:
                out[slot] = f"{text_node}.inputs.text"

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

    return out


def _walk_to_text(graph: dict, link, depth: int = 0):
    """Follow a link back to the node that owns a literal `text` widget.

    Conditioning rarely comes straight from CLIPTextEncode — it passes through
    ConditioningCombine / FluxGuidance / ControlNet nodes. Without the walk the
    prompt slot silently goes unmapped on most real workflows.
    """
    if depth > 6 or not (isinstance(link, list) and link):
        return None
    nid = str(link[0])
    node = graph.get(nid)
    if not node:
        return None
    if isinstance(node.get("inputs", {}).get("text"), str):
        return nid
    for value in node.get("inputs", {}).values():
        found = _walk_to_text(graph, value, depth + 1)
        if found:
            return found
    return None


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
