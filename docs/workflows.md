# Workflows

A workflow in iDev.Gen is your own ComfyUI graph plus a **node map**: which
widget in that graph each session value should drive.

## Exporting from ComfyUI

Use **Workflow → Export (API)**. The regular *Save* format (`{"nodes": [...],
"links": [...]}`) describes the editor canvas, not the executable graph, and is
rejected with a message saying so.

## Importing

**Workflows → Import workflow…** parses the file and proposes a mapping. The
detection walks the graph rather than guessing by node name:

- the sampler is whatever node has both `positive` and `negative` inputs — a
  custom sampler class is fine;
- `seed` also matches `noise_seed`;
- the prompt is found by following the conditioning links back to the node that
  owns a literal `text` widget, so `FluxGuidance`, `ConditioningCombine` and
  friends in between do not hide it;
- size comes from the latent node, the LoRA from the first loader with a
  `lora_name`, and the prefix from the save node.

Fix anything it got wrong in the table: every row is a dropdown of the actual
widgets in your graph, listed as `#id ClassName · widget`.

## What "unmapped" means

A slot you leave as *do not control* keeps whatever the workflow itself has.
That is the point: a graph with an unusual sampler, a refiner pass or three LoRA
loaders still works when only the prompt and the seed are driven. You lose the
ability to change that value per session, nothing else.

Three rows deserve attention:

- **Base model** — map it and you can pick the checkpoint (or the standalone
  diffusion model) per character and per session, from the dropdown, without
  keeping one near-identical workflow per model. Leave it unmapped, or leave the
  dropdown on *the workflow's own*, and the graph's value stands.

- **Filename prefix** — leave it mapped. iDev.Gen overwrites it with
  `idevgen/<session>/<shot>_<random>` so results never mix with the images you
  generate by hand, and so ComfyUI cannot answer with a cached result instead of
  writing a file.
- **LoRA (file)** — map it to the loader that holds your character LoRA. If your
  graph stacks several loaders, the mapping drives the first one found; the
  others keep their own values, which is usually what you want for a style LoRA.

## Slots

| Slot | Typical node |
|---|---|
| Base model | `CheckpointLoaderSimple.ckpt_name`, or `UNETLoader.unet_name` for Flux / Krea / Z-Image |
| Positive / negative prompt | `CLIPTextEncode.text` |
| Seed | `KSampler.seed` or `.noise_seed` |
| Steps, CFG | `KSampler.steps`, `.cfg` |
| Width, height | `EmptyLatentImage.width` / `.height` |
| LoRA file, LoRA strength | `LoraLoader.lora_name`, `.strength_model` |
| Filename prefix | `SaveImage.filename_prefix` |

A slot mapped to a **connected** input is ignored on purpose: writing a value
where a link belongs would corrupt the graph.

## Editing later

Click a workflow's name to reopen it, change the mapping and save. Models and
sessions keep pointing at it; existing photos are untouched.
