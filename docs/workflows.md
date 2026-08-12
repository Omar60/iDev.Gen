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
  `lora_name`, and the prefix from the save node;
- each `LoadImage` takes the next free reference slot, in graph order, and
  `Reference strength` is only taken from an IPAdapter node — a bare `weight`
  widget sits on half the nodes of a busy graph.

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
| Reference image (1–3) | `LoadImage.image` |
| Denoise | `KSampler.denoise` |
| Reference strength | `IPAdapterAdvanced.weight`, or an editing node's own reference-fidelity dial |

A slot mapped to a **connected** input is ignored on purpose: writing a value
where a link belongs would corrupt the graph.

A slot that takes fractions must be a **decimal** in the graph. Widget types are
preserved, so a dial the workflow ships as `4` is an integer slot and a strength
of `1.5` reaches it as `1` — silently. Write it as `4.0` and it stays a float.

## Reference workflows

A workflow that maps **Reference image** can be assigned to a session as its
*reference workflow*, and the takes marked `ref` run through it instead of the
main one. See [sessions](sessions.md#reference-takes).

Two things work differently there, both on purpose:

- **The base model and the LoRA are not checked.** Everywhere else, picking a
  base model or a LoRA that the workflow does not map refuses the run rather than
  ignoring the choice. A reference workflow is exempt: an editing graph loads its
  own model, and the character comes from the reference photo instead of from the
  LoRA, so `FLUX.1 Kontext` and `Qwen-Image-Edit` graphs legitimately have
  neither slot.
- **Width and height usually go unmapped**, because an editing graph has no
  `EmptyLatentImage` — the size comes from the reference photo. The session's
  size is then simply not applied, which is the same rule every unmapped slot
  follows.

The reference image slot itself is *not* exempt: running a session whose
reference takes would go out with an unmapped reference is refused, because
every photo would come back painted from noise with nothing on screen saying the
reference had been dropped. Marking **more** reference photos than the workflow
reads is refused for the same reason — the extra one uploads, nothing consumes
it, and the result merely looks as if that reference did nothing.

Each reference slot is a **role**, and the role is decided by the workflow's own
wiring, not by anything you write in the prompt. An editing node that takes two
images names them in the order it was trained on — scene first, subject second,
say — so `Reference image` and `Reference image 2` mean whatever that node's
first and second inputs mean. Map them to the wrong inputs and the images swap
jobs. Several references only help when each carries **different** information;
two near-identical views of the same thing are a contradiction, not a
confirmation.

## Editing later

Click a workflow's name to reopen it, change the mapping and save. Models and
sessions keep pointing at it; existing photos are untouched.
