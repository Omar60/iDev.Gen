"""Node-map detection and patching: what decides whether an imported workflow
obeys the session or keeps rendering the same photo forever."""
from comfy import apply_map, detect_map
from conftest import GRAPH


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


def test_apply_never_overwrites_a_link():
    """Patching a connected input would break the graph, so it is skipped."""
    g = apply_map(GRAPH, {"positive": "6.inputs.positive"}, {"positive": "x"})
    assert g["6"]["inputs"]["positive"] == ["9", 0]


def test_apply_ignores_unmapped_and_null_slots():
    g = apply_map(GRAPH, {"seed": "6.inputs.seed"}, {"seed": None, "positive": "x"})
    assert g["6"]["inputs"]["seed"] == 1
    assert g["3"]["inputs"]["text"] == "hello"
