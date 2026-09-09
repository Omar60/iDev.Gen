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
- **Rating is per shot and local.** Tags and the search are whole-session
  (see [sessions](sessions.md#tags-and-the-library)); ratings are not.
- **Retry only re-queues failed and cancelled shots.** A photo that came out is
  re-rolled with **↺ Reshoot** (one at a time) or **Reshoot below N★** (every
  finished shot under the threshold, in one click).
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
  In `resource-v1` sessions, exact plan provenance, deterministic prompt
  composition and explicit wardrobe tracking provide reproducible instructions
  and state history, but **not pixel-level continuity**. Describing the same
  clothing does not guarantee identical garment geometry, drape, buttons or
  seam alignment across different generations. Visual reference workflows
  remain available when an existing photograph must be held while modifying
  specific elements, but neither reference workflows nor text-to-image prompts
  provide pixel-perfect continuity or parity with external compiled runtimes
  (no AmazingDraw rendering parity).
- **The first click on a local model pays for loading it.** An 8B vision model
  took over a minute to reach VRAM on a card ComfyUI had been using, before it
  had looked at the photo at all. The request waits five minutes before giving
  up for that reason; the second click is seconds.
- **No authentication.** The server binds to `127.0.0.1` and assumes a single
  local user. Do not expose it to a network. That includes the assistant's API
  key, which sits in `config.json` in plain text like every other setting.
  Reaching the app from a phone is opt-in through `start-lan.bat`, which
  binds every interface; the warning it prints is the whole story. Anything
  reachable from the loopback interface becomes reachable from every device
  on the network, with no password and no audit trail — including deleting
  sessions and queueing generations. Use it on a trusted network only.
- **The slideshow's phone display sleeps mid-play.** The Screen Wake Lock API
  is withheld on plain HTTP, and the page is served over plain HTTP. The
  slideshow dies when the phone's display sleeps. Set the phone's display
  timeout (Settings → Display → Sleep) to a value longer than the slideshow
  you are running. A tunnel providing a genuine certificate (Tailscale Serve
  or equivalent) turns the origin secure and Wake Lock becomes available;
  until then the limitation is documented rather than worked around.
- **Sustained bandwidth at short intervals.** The slideshow sustains roughly
  3.3 Mbps at a three-second interval regardless of the look-ahead value,
  because preparing ahead moves the same cost earlier rather than removing
  it. Below roughly two seconds per photograph on the common 1.25 MB PNGs,
  the network becomes the limit and no look-ahead value helps. The setting
  is in the URL for when it is worth tuning.
- **Changing the data folder needs a restart.** The database is already open on
  the old one.
- **Moving, not copying.** A finished image leaves ComfyUI's output folder. If
  you also want it in ComfyUI's own gallery, copy it back yourself.

## The catalogue, the composer and judging

- **The stores ship empty.** Nothing is imported on first run and nothing
  imports itself when a screen is opened. Press **Import Measured Catalogue**
  on the Catalogue screen once; until then composing and creating a legacy
  session refuse with a 422 naming the empty slot and manner. This refusal
  applies strictly to legacy sessions and catalogue-composed runs; `resource-v1`
  session drafts do not require the measured catalogue or cell uniqueness, and
  can be created, edited and prepared with an empty catalogue. The same screen
  carries **Import Readings Seed** beside the base readings and **Import Wardrobe
  Seed** in its own panel — a judging pass refuses a slot whose families have
  no reading, and an empty wardrobe leaves the outfit picker offering nothing.
  Rooms come in through the Rooms screen. **Every import is idempotent on the
  key and never re-words a row that is already there**, so pressing one twice
  is safe and editing a shipped row means the seed file plus a fresh data
  folder.
- **Strict mode on a young catalogue queues nothing.** It draws only cells the
  judge has verified, and a table with two verified trios refuses almost every
  request. That is the table being honest; exploratory is the default for that
  reason.
- **A short pool is refused, never padded.** A trio is drawn at most once, so a
  run of N fills N distinct cells. When the pool runs out first the request is
  refused naming the largest count that would have worked. Every check runs
  before any insert: N rows or zero, never some.
- **Judging is a person at a screen.** The blind pass is a human forced choice.
  There is a scripted vision judge (`scripts/judge_cell.py`) but it is a harness
  beside the app, not a button in it, and the human-versus-vision timing ratio
  in the design notes is still an estimate.
- **A mined trio is composable and unmeasurable on its own.** A combination
  mined from a fused source library carries a camera and an act but no framing,
  because the source has no crop field — and a cell is three dimensions, so the
  photograph cannot be recorded. Shoot it with a framing of your own held
  constant (`crop-full-body` is the one this repo used) to make the evidence
  recordable.
- **`pov` is not in the session picker.** The manner exists and the store can
  hold its rows, but a manner whose camera catalogue is empty is refused at
  session creation, and its rows are mined from material that is not in this
  repository. It stays out of the picker rather than being a button that always
  fails.
- **Mining needs three files this repo does not ship.** Cutting a fused library
  into rows reads a curated cut map, a family per entry and a judge label per
  row, all beside the source material on your own machine. None of it is
  guessed: a missing label refuses the whole import rather than deriving one
  from the wording.

## Resource libraries and session planning

- **Untranslated resources remain pending.** In the SQLite resource database path,
  accepted entries can be preserved privately in their original language
  separately from English translations. However, missing required English fields
  marks readiness as pending: a pending resource cannot finalize takes or
  generate prompts until translated. (By contrast, the legacy Rooms import is
  translation-first and refuses to write any seed files if a string lacks a
  translation.)
- **Fused resources and placeholders.** Fused scene resources can be stored
  complete in the resource database without decomposition, but unresolved
  template placeholders block prompt finalization. Decomposing a fused scene
  into camera/act catalogue rows and room seeds is required only when targeting
  measured catalogue components or legacy room seeds.
- **Instructional continuity is not pixel continuity.** Storing identical
  source revisions, deterministic effective wardrobes, and frozen final prompts
  maintains reproducible instructions and state history across takes, but does
  not guarantee pixel-identical renders or exact garment drape and geometry.
