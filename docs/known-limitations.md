# Known limitations

Deliberate gaps in the current version. They are listed so nobody has to
discover them mid-shoot.

- **Nothing checks that the base model and the LoRA match.** A LoRA is trained
  against one architecture: a Krea LoRA on a Z-Image model fails to load or
  renders noise. The dropdown lists everything ComfyUI has, because there is no
  reliable way to read a file's family from its name — folders and naming
  conventions are yours, not a standard. Picking the pair correctly is on you;
  ComfyUI's error lands on the failed shot.
- **Swapping the base model does not swap the rest of the pipeline.** The slot
  changes one widget. A workflow wired for Krea keeps its text encoder, its VAE
  and its sampler settings, so switching it to another family usually needs a
  workflow built for that family instead.
- **One LoRA per model.** A session drives a single LoRA loader. Character +
  style combos need a workflow that already stacks them, and the extra loaders
  keep their own fixed values.
- **No contact sheet or export.** Picks live in the session folder; there is no
  "export selection" button yet. The folder is plain PNG files, so a file
  manager works.
- **One look per session, and no look library.** That constraint is the point —
  a session is one wardrobe — but there is no way to save a look or a set of
  shots and reuse them in the next session. Copy the text, or use
  **⟳ More like this**.
- **One session at a time, serial.** No batching, no multi-GPU, no queue of
  queued sessions.
- **Rating is per shot and local.** No tags, no collections across sessions, no
  search.
- **Retry only re-queues failed and cancelled shots.** It cannot re-roll a shot
  that succeeded; shoot more variations instead.
- **Cancel is not instant.** It interrupts the job ComfyUI is running and
  cancels the rest, but the current image may still finish writing.
- **No authentication.** The server binds to `127.0.0.1` and assumes a single
  local user. Do not expose it to a network.
- **Changing the data folder needs a restart.** The database is already open on
  the old one.
- **Moving, not copying.** A finished image leaves ComfyUI's output folder. If
  you also want it in ComfyUI's own gallery, copy it back yourself.
