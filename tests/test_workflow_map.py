"""Node-map detection and patching: what decides whether an imported workflow
obeys the session or keeps rendering the same photo forever."""
from comfy import apply_map, detect_map, graph_checkpoint
from conftest import EDIT_GRAPH, GRAPH


def test_detects_every_slot():
    m = detect_map(GRAPH)
    assert m["positive"] == "3.inputs.text"      # walks through FluxGuidance
    assert m["negative"] == "4.inputs.text"
    assert m["seed"] == "6.inputs.seed"
    assert m["steps"] == "6.inputs.steps"
    assert m["cfg"] == "6.inputs.cfg"
    assert m["width"] == "5.inputs.width"
    assert m["height"] == "5.inputs.height"
    assert m["lora_name"] == "2.inputs.lora_name"
    assert m["lora_strength"] == "2.inputs.strength_model"
    assert m["filename_prefix"] == "8.inputs.filename_prefix"


def test_detects_and_patches_the_sampler_pair():
    """Seven Krea 2 finetunes, seven different sampler/scheduler pairs — mapping
    them is what lets one graph serve every checkpoint. The slot is `sampler`;
    the widget ComfyUI puts it in is `sampler_name`, so the two names differ."""
    m = detect_map(GRAPH)
    assert m["sampler"] == "6.inputs.sampler_name"
    assert m["scheduler"] == "6.inputs.scheduler"
    g = apply_map(GRAPH, m, {"sampler": "res_2s", "scheduler": "beta"})
    assert g["6"]["inputs"]["sampler_name"] == "res_2s"
    assert g["6"]["inputs"]["scheduler"] == "beta"
    # Empty is how a session says "keep the graph's own", and the runner sends it
    # as None. Patching it to "" would queue a graph ComfyUI rejects.
    g = apply_map(GRAPH, m, {"sampler": None, "scheduler": None})
    assert g["6"]["inputs"]["sampler_name"] == "euler"
    assert g["6"]["inputs"]["scheduler"] == "normal"


def test_detects_the_base_model_from_either_loader():
    """SDXL loads an all-in-one checkpoint; Flux, Krea and Z-Image load the
    diffusion model on its own. Both must fill the same slot."""
    assert detect_map(GRAPH)["checkpoint"] == "1.inputs.ckpt_name"

    unet_graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "krea.safetensors",
                                                     "weight_dtype": "default"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
    }
    assert detect_map(unet_graph)["checkpoint"] == "1.inputs.unet_name"


def test_reads_the_base_model_a_graph_loads_by_itself():
    """What lets picking a base model pick the graph written for it. The loader
    is read even when the slot is unmapped — which is exactly what a graph tuned
    for one checkpoint does, so the fallback is the case that matters."""
    assert graph_checkpoint(GRAPH, detect_map(GRAPH)) == "base.safetensors"
    assert graph_checkpoint(GRAPH, {}) == "base.safetensors"
    assert graph_checkpoint(GRAPH, {"checkpoint": "99.inputs.ckpt_name"}) == "base.safetensors"
    assert graph_checkpoint({}, {}) == ""


def test_detects_sampler_not_named_ksampler():
    """A custom sampler is recognised by having positive+negative, not by class."""
    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "ugly"}},
        "3": {"class_type": "MyWeirdSampler", "inputs": {
            "noise_seed": 5, "steps": 4, "positive": ["1", 0], "negative": ["2", 0]}},
    }
    m = detect_map(graph)
    assert m["seed"] == "3.inputs.noise_seed"    # noise_seed counts as the seed
    assert m["positive"] == "1.inputs.text"
    assert m["negative"] == "2.inputs.text"


def test_apply_keeps_widget_types_and_leaves_the_original_alone():
    m = detect_map(GRAPH)
    g = apply_map(GRAPH, m, {
        "positive": "on the beach", "seed": 42.0, "steps": 8, "cfg": 1,
        "width": 832, "lora_name": "characters/ada.safetensors", "lora_strength": 0.9,
    })
    assert g["3"]["inputs"]["text"] == "on the beach"
    assert g["6"]["inputs"]["seed"] == 42 and isinstance(g["6"]["inputs"]["seed"], int)
    assert isinstance(g["6"]["inputs"]["cfg"], float)      # the widget was a float
    assert g["5"]["inputs"]["width"] == 832
    assert g["2"]["inputs"]["lora_name"] == "characters/ada.safetensors"
    assert GRAPH["3"]["inputs"]["text"] == "hello"         # stored graph untouched


def test_the_prompt_is_found_when_the_widget_is_called_prompt():
    """Instruction-editing encoders take the text as `prompt`, not `text`, and
    feed it through a vision encoder along with the photo. Looking only for
    `text` leaves the positive slot unmapped on exactly the graphs that reference
    sessions are for."""
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "example.png"}},
        "2": {"class_type": "Krea2EditGroundedEncode", "inputs": {
            "prompt": "take the jacket off", "grounding_px": 768, "image": ["1", 0]}},
        "3": {"class_type": "Krea2EditGroundedEncode", "inputs": {
            "prompt": "", "grounding_px": 768, "image": ["1", 0]}},
        "4": {"class_type": "KSampler", "inputs": {
            "seed": 1, "steps": 10, "cfg": 1.0, "denoise": 1.0,
            "positive": ["2", 0], "negative": ["3", 0]}},
    }
    m = detect_map(graph)
    assert m["positive"] == "2.inputs.prompt"
    assert m["negative"] == "3.inputs.prompt"
    assert m["reference"] == "1.inputs.image"


def test_detects_the_reference_image_and_denoise():
    m = detect_map(EDIT_GRAPH)
    assert m["reference"] == "2.inputs.image"
    assert m["denoise"] == "6.inputs.denoise"
    # No EmptyLatentImage in an editing graph: the size comes from the photo, so
    # the session's width and height stay unmapped and are simply not applied.
    assert "width" not in m and "height" not in m


def test_each_load_image_takes_the_next_reference_slot():
    """Kontext and Qwen-Image-Edit take several photos — a character plus a
    garment — so more than one LoadImage is normal, not a mistake."""
    graph = dict(EDIT_GRAPH)
    graph["10"] = {"class_type": "LoadImage", "inputs": {"image": "garment.png"}}
    graph["11"] = {"class_type": "LoadImage", "inputs": {"image": "backdrop.png"}}
    graph["12"] = {"class_type": "LoadImage", "inputs": {"image": "spare.png"}}
    m = detect_map(graph)
    assert m["reference"] == "2.inputs.image"
    assert m["reference2"] == "10.inputs.image"
    assert m["reference3"] == "11.inputs.image"
    assert "reference4" not in m           # three slots, the fourth is left alone


def test_reference_strength_only_comes_from_an_ipadapter():
    """`weight` sits on half the nodes of a busy graph; grabbing the wrong one
    would drive something else every time the strength is changed."""
    graph = {
        "1": {"class_type": "LoraLoader", "inputs": {"lora_name": "x.safetensors", "weight": 0.7}},
        "2": {"class_type": "IPAdapterAdvanced", "inputs": {"weight": 1.0, "weight_type": "linear"}},
    }
    assert detect_map(graph)["reference_strength"] == "2.inputs.weight"


def test_the_reference_slot_patches_the_load_image():
    g = apply_map(EDIT_GRAPH, detect_map(EDIT_GRAPH),
                  {"reference": "idevgen/anchor.png", "denoise": 0.55})
    assert g["2"]["inputs"]["image"] == "idevgen/anchor.png"
    assert g["6"]["inputs"]["denoise"] == 0.55
    assert EDIT_GRAPH["2"]["inputs"]["image"] == "example.png"    # stored graph untouched


def test_apply_never_overwrites_a_link():
    """Patching a connected input would break the graph, so it is skipped."""
    g = apply_map(GRAPH, {"positive": "6.inputs.positive"}, {"positive": "x"})
    assert g["6"]["inputs"]["positive"] == ["9", 0]


def test_apply_ignores_unmapped_and_null_slots():
    g = apply_map(GRAPH, {"seed": "6.inputs.seed"}, {"seed": None, "positive": "x"})
    assert g["6"]["inputs"]["seed"] == 1
    assert g["3"]["inputs"]["text"] == "hello"
