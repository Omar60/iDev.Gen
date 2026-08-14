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
- **One look per session, and no look library.** The look — hair, makeup, the
  place, the light — is fixed once and that constraint is the point. The
  wardrobe is not: it rides on every take and a take may change it. But there is
  no way to save either and reuse it in the next session. Copy the text, or use
  **⟳ More like this**.
- **A long shoot is written in rounds, and it takes minutes.** Asked for forty
  lines at once an assistant answers about thirty, shorter than asked, with the
  middle of the shoot missing — so forty takes with their wardrobes is ten calls
  and several minutes. The button counts up while it works. Nothing resumes: a
  browser closed halfway has written nothing.
- **A written shoot still needs reading, and the app says where.** Measured over
  forty takes: three of them named a garment the wardrobe of that same take had
  already put down, always at the seam where it comes off. Those rows are
  outlined in red — the check is a word in the session's wardrobe that this
  take's wardrobe no longer has, so it catches the garment and not the colour or
  the bare shoulder. It points; it never rewrites.
- **One session at a time, serial.** No batching, no multi-GPU, no queue of
  queued sessions.
- **Rating is per shot and local.** No tags, no collections across sessions, no
  search.
- **Retry only re-queues failed and cancelled shots.** It cannot re-roll a shot
  that succeeded; shoot more variations instead.
- **Cancel is not instant.** It interrupts the job ComfyUI is running and
  cancels the rest, but the current image may still finish writing.
- **The prompt assistant writes text, and only text.** It fills a box you then
  edit; it never queues anything and never changes a shot that exists. What it
  writes is as good as the model behind it — a 9B writes usable takes and a
  fairly generic look.
- **Read the wardrobe before you Run.** The three ways it loses a garment are
  all visible in the boxes: one it never named — no trousers, no shoes — is
  invented differently in every frame; a take that mentions clothing at all can
  contradict the wardrobe beside it (`barefoot` under a wardrobe with boots);
  and a wardrobe line that *names* the piece it is taking off (`no top`, `jersey
  removed`) puts it straight back in the photo. The instructions forbid all
  three; a model still slips — measured, on the take rather than the wardrobe —
  and a session is dozens of photos.
- **A reasoning model is asked not to reason.** Thinking about four short lines
  costs ten times the tokens of writing them — minutes per click. The request
  says `reasoning_effort: none`, and an endpoint that rejects the parameter is
  retried without it, which is where those minutes come back.
- **A local assistant and ComfyUI share one GPU.** Asking for takes while a
  session runs makes both wait, and the model may be swapped in and out of VRAM
  between the two. Write the shoot first, then Run — or point the assistant at a
  hosted endpoint, which leaves the card to ComfyUI entirely.
- **A photo picked from disk is scaled to 1024px before it is sent**, which is
  what small vision models read anyway. A photo already in the app — the anchor
  — is sent at full size. A model with no vision answers with an error on the
  photo buttons and works normally on the text ones.
- **Words are not a photograph.** A look written in detail holds the attributes
  it names — colour, fabric, neckline, hem — and nothing else: button count,
  exact drape and the seams no sentence mentions still drift between frames. For
  a garment that is genuinely identical, work from the photo instead of from
  words: shoot it once and edit that frame (*Photo edit*), which is what
  reference takes are for.
- **The first click on a local model pays for loading it.** An 8B vision model
  took over a minute to reach VRAM on a card ComfyUI had been using, before it
  had looked at the photo at all. The request waits five minutes before giving
  up for that reason; the second click is seconds.
- **No authentication.** The server binds to `127.0.0.1` and assumes a single
  local user. Do not expose it to a network. That includes the assistant's API
  key, which sits in `config.json` in plain text like every other setting.
- **Changing the data folder needs a restart.** The database is already open on
  the old one.
- **Moving, not copying.** A finished image leaves ComfyUI's output folder. If
  you also want it in ComfyUI's own gallery, copy it back yourself.
