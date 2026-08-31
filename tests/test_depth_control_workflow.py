"""The depth-control graph is data, so the thing that can rot is its wiring.

ComfyUI rejects a graph with a link to a node that isn't there, and `apply_map`
silently skips a slot whose path doesn't resolve — both fail as a dead render
half an hour later rather than as an error here.
"""
import json
from pathlib import Path

from comfy import apply_map

BODY = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "krea2-depth-control-workflow.json")
    .read_text(encoding="utf-8")
)
GRAPH, NODE_MAP = BODY["graph"], BODY["node_map"]


def test_every_link_points_at_a_node_that_exists():
    for nid, node in GRAPH.items():
        for field, value in node["inputs"].items():
            if isinstance(value, list) and value and isinstance(value[0], str):
                assert value[0] in GRAPH, f"{nid}.{field} links to missing node {value[0]}"


def test_the_control_chain_reaches_the_sampler():
    # 900 -> 901 -> 902 -\
    #                822 -> 903 -> 904 -> 599
    assert GRAPH["599"]["inputs"]["model"] == ["904", 0]
    assert GRAPH["904"]["inputs"]["control_latent"] == ["902", 0]
    assert GRAPH["903"]["inputs"]["model"] == ["822", 0]
    assert GRAPH["902"]["inputs"]["control_image"] == ["901", 0]
    assert GRAPH["901"]["inputs"]["image"] == ["900", 0]


def test_every_mapped_slot_resolves():
    for slot, path in NODE_MAP.items():
        nid, _, field = path.split(".", 2)
        field = field.replace("inputs.", "", 1)
        assert nid in GRAPH, f"{slot} maps to missing node {nid}"
        inputs = GRAPH[nid]["inputs"]
        assert field in inputs, f"{slot} maps to missing field {path}"
        assert not isinstance(inputs[field], list), f"{slot} maps to a link, not a widget: {path}"


def test_the_two_new_slots_actually_patch():
    out = apply_map(GRAPH, NODE_MAP, {"reference": "idevgen/pose.png", "reference_strength": 0.65})
    assert out["900"]["inputs"]["image"] == "idevgen/pose.png"
    assert out["903"]["inputs"]["strength"] == 0.65


def test_the_character_lora_is_not_the_control_lora():
    # Both nodes carry a `lora_name`; binding the character slot to the depth
    # lora would swap the identity for a control weight and render a stranger.
    assert NODE_MAP["lora_name"] == "822.inputs.lora_name"
    assert "krea" in GRAPH["822"]["inputs"]["lora_name"]
    assert "depth-control" in GRAPH["903"]["inputs"]["lora_name"]
