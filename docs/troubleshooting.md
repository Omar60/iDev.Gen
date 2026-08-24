# Troubleshooting

Failed shots keep their error where the thumbnail would be. Here is what each
one means.

## "Finish setup" in the top bar

`comfy_output_dir` is empty or points at a folder that does not exist, so
sessions refuse to run. Open **Setup**, press *Detect from ComfyUI*, save.

## "ComfyUI offline"

The backend cannot reach `comfy_url`. Check ComfyUI is running and that the URL
matches its port — and if ComfyUI runs on another machine, that it was started
with `--listen`.

## "Prompt outputs failed validation"

ComfyUI rejected the graph. Usually the mapping points at the wrong widget: a
model name that does not exist on this install, or a value of the wrong type.
Reopen the workflow and check the offending row; the message names the node.

## "Generated file not found"

ComfyUI reported an image that is not on disk. Two causes:

- `comfy_output_dir` points at a different folder than the one ComfyUI actually
  writes to (common when several installs share a machine); or
- ComfyUI answered from its execution cache. iDev.Gen already prevents this by
  giving every attempt a unique filename prefix — if you see it anyway, the
  prefix slot is probably unmapped in that workflow.

## "[WinError 32] The process cannot access the file"

The PNG was still locked when iDev.Gen tried to move it — ComfyUI closing the
handle, an antivirus scanning a freshly written file, a thumbnailer. The move is
retried six times over about eight seconds, so seeing this means the lock
outlived that. **Retry** regenerates the shot.

The image from the failed attempt stays in ComfyUI's `output/idevgen/<session>/`
folder; it is a real photo, so it is not deleted behind your back. Move or
delete it yourself.

## The photos are noise, or the shot dies with a size mismatch

The base model and the LoRA are from different families (a Krea LoRA on a
Z-Image model, say), or the workflow around the model belongs to another family:
swapping the base model changes one widget, not the text encoder, the VAE or the
sampler. Use a workflow built for that family, and a LoRA trained for it.

## "ComfyUI returned no image"

The graph ran but produced no saved image: usually an execution error in
ComfyUI (out of memory, a missing model). ComfyUI's own console has the detail.
Check that the workflow ends in a `SaveImage` node — a preview-only node saves
nothing.

## The shot ran past 900s

One image took more than fifteen minutes and was abandoned so the session could
continue. Expected on very large resolutions or a busy GPU; the limit is
`SHOT_TIMEOUT` in `backend/runner.py`.

## "A session is already running"

One GPU, one session. Wait for it, or cancel it.

## Every shot in a session failed the same way

Fix the cause, then **Retry** — it re-queues the failed and cancelled shots
without touching the good ones.

## No LoRA thumbnails on the model cards

`lora_dir` is not set, or your LoRAs have no `<name>.preview.jpeg` sibling. It
is cosmetic only.

## Sessions stuck as "failed" after closing the app

A run interrupted by shutting the app down cannot be resumed by the process that
died, so it is marked failed on the next start. Retry re-queues it.

## The phone cannot reach the app started with `start-lan.bat`

A Windows Firewall rule is bound to the exact executable path. The rule
that currently admits inbound traffic on port 8777 was created for the
system Python, while `start-lan.bat` runs the virtualenv's Python — a
different binary at a different path. The first run of `start-lan.bat`
therefore raises a fresh firewall prompt, and a reflexive **Cancel** leaves
it silently unreachable.

When the prompt appears, allow the connection; the new rule covers this
exact path. If the prompt was already dismissed, open **Windows Security →
Firewall & network protection → Advanced settings → Inbound Rules**, find
the entry for the virtualenv's `python.exe` (the path is shown in
`start-lan.bat`'s output), and enable it.
