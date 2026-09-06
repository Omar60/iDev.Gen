## Why

Another local ComfyUI console drives the same Moody graph, the same node
mapping and the same rgthree seed type as this app, and it ships 525 hand-tuned
scene descriptions where this repo has nine. Sessions 391 and 392 shot its
method against ours on the bench, blind-judged, and the split is clean:

* Its rooms are better. One long, dense, hand-weighted `scene_theme` string
  builds a room our `look` prose does not match for texture, props and light.
* Its angles are not better. Our catalogue camera rows landed 6/6 behind when
  the line was not overloaded; its own line came back frontal 3/3 and knee-up
  6/6 whenever its camera library was switched off, which is how its config
  ships.
* Its camera library, switched on, works: rear 2/2 behind, facial POV 2/2
  overhead, fisheye 2/2 overhead, and the fisheye rendered real barrel
  distortion - a lens term that is NOT inert on this sampler, unlike the 25
  shot-size and lens terms this repo already measured and discarded.

So the material worth adopting is the room text and the camera vocabulary, and
the shape worth keeping is ours: the measured trio of camera, act and framing.
Importing their entries as they are shaped - camera, act, room and wardrobe
fused into one string - would deliver good photographs and destroy the ability
to say which component earned them.

## What Changes

* Rooms become a first-class, registered library that grows from 9 to 428 — 384
  imported as rooms and 44 more left over from mined camera entries — with their
  English `scene_theme` stored **verbatim**.
* **The imported material is not committed.** The source's prose stays out of
  this public repo: what ships is the importer, the deny-list, the tests and
  their invented fixtures. Two things follow. The picker reads imported rooms at
  runtime instead of bundling them, because a build-time import of an untracked
  file is a fresh clone whose frontend does not build. And a verdict is stored
  apart from the text it was measured against, keyed by the room's key and
  carrying no source prose, so a measurement this project made is never lost
  with material that is only stored here.
* A translation pass carries the source's non-English fields into English — 731
  unique strings, of which the labels are the bulk and the `notes` and
  `*_anchor` fields are the value, because those state why an entry is shaped
  the way it is and what breaks it. Translations are stored as text this repo
  authored, in fields distinct from the ones holding source text, and recorded
  once in an untracked map so a re-import cannot reword a room. This is a
  precondition, not a nicety: AGENTS.md is English-only and the room seed stores
  a label.
* A config-declared registry replaces hard-coded seed filenames, so adding a
  library is a config entry and not a code change. Each declared source names
  the kind of material it carries and every destination its entries reach.
* Importing becomes an operation in the app as well as a command, over one
  implementation. Re-uploading a refreshed source updates the rows it already
  produced and keeps the verdicts measured against them; an upload carrying
  untranslated text is refused in whole and lists what is missing.
* Rooms are stored as the place alone. The nine that exist today fuse the
  candid register into their text, which would force one stored row per manner;
  they are split so one row serves every manner and the register is composed in.
* Their fused `perspective_scenes` entries are **mined into separate rows** -
  camera clause to `component`/`camera`, act to `component`/`act`, remaining
  room prose to the room seed - never imported as one row.
* Two camera concepts this catalogue does not have enter as unverified
  candidates: a feet-first low POV and an overhead camera over a kneeling
  subject.
* The composer gains two gates: a room that names other bodies is refused
  unless the run carries the two-body token, and a room over its word budget is
  refused by rule rather than silently spending the line's camera.
* The writer's field list grows from seven to twelve: `accessories`, `marks`,
  `style`, `props` and `story` join it. `makeup` and `hair` were measured with
  them and are held back for a reason that is not the measurement, below.
  Measured, not proposed — three text arms, five runs a side at n=25, 362 lines,
  plus a paired render session. The fourteen-field arm matched the seven-field
  control on the handed framing (87.4% against 86.8%), on the camera (a dead
  heat once the per-run spread is read) and on the arrival of the fields the
  control already had (98-99% in both). The paired render arm — one line shot
  with the seven candidate blocks and without, same seed — came back 9/16
  against 9/16. Twelve is that arm minus two, so the measurement is a ceiling
  this ships under rather than one it spends.
* `tattoo`, `pet` and `liquids` become run-level switches rather than fields.
  Measured, they are the cost: a field whose subject is absent from most
  photographs fights the rule that every key is written, and the writer invents
  to satisfy it — one arm wrote the same fictitious tattoo forty times on a
  character who has none, and dragged every other field down from 98-99% to
  92-94% while doing it. Off, they leave the list entirely; on, the run supplies
  the subject and the writer carries it.
* `lighting`, `makeup` and `hair` stay session-level, and only `lighting` was
  ever going to be uncontroversial. The argument for the other two is real -
  makeup that smudges over a shoot is the same face with time on it, which is
  the shape the wardrobe already has here - but the wardrobe is a *default* a
  take may override, and the look is a *constant* prepended to every line. The
  nine rooms' register carries the hair sentence, and this change keeps it
  there, so a per-photograph `hair` field would put two answers to one question
  in every composed line. They ship the day the look stops defining them, which
  is a change to the session's oldest invariant and not this one.
* **Every importer gains a deny-list**, enforced in code and covered by a test
  that fails if the list is bypassed.

**BREAKING**: none. Every existing room, component row and session keeps its
current meaning; the room seed grows and gains fields with defaults.

## Capabilities

### New Capabilities

- `asset-import-guard`: the deny-list that every import path must pass, the
  test that proves it cannot be bypassed, and the rules on the form imported
  text takes — source English stored byte-identical, non-English carried across
  as an authored translation, and neither one ever mistaken for the other.
  School-set entries, minor-coded body profiles and real people's names never
  enter this repo, by rule and not by the judgment of whoever runs the script,
  and a refused entry is never translated either.
- `asset-refresh`: one import pipeline with two entries - an upload inside the
  app and a command line - so a source library that changes upstream is a file
  the operator drops in and a diff they review, not a script with remembered
  arguments. Covers every source, not only the scenes.
- `room-library`: rooms as a registered, verbatim seed store - the registry, the
  import, the derived `offers` field, the room's own verdict, and the split that
  keeps a room the place alone so one row serves every manner.
- `perspective-mining`: splitting a fused camera-act-room entry into separate
  candidate rows so each stays independently judgeable.
- `room-composition-gate`: the compose-time refusals - the multi-body gate and
  the room word budget.
- `writer-field-set`: which fields the writer answers in, the bar a new one
  clears before it ships, and the rule that keeps a usually-empty field from
  inventing its own subject — it is switched on per run and supplied, or it is
  not in the list at all.

### Modified Capabilities

None. The six specs under `openspec/specs/` (bulk-reshoot, contact-sheet,
lan-access, photo-slideshow, session-export, session-library) are untouched.
The in-flight `component-catalogue`, `prompt-components`, `component-matrix` and
`shot-composer` deltas are not modified either: this change adds rows to the
catalogue those specs describe, and adds gates beside the composer's existing
crop law, without changing a requirement either one states.

## Impact

* **Data**: `data/candid-rooms-seed.json` keeps its nine rooms, tracked, and has
  them split into register and place. Imported rooms, mined candidates and the
  translation map go to untracked paths. A new tracked verdict store, keyed by
  room key and carrying no source text.
* **Backend**: `backend/main.py` import routes (a registry read replaces
  hard-coded filenames), the composer's refusal path, and `backend/db.py` for
  one column: the key of the room a session's look was filled from, which is
  what the gates resolve against. The room's verdict needs no column - it lives
  in the tracked verdict store.
* **Frontend**: the room picker in ModelDetail reads a larger list, needs a
  filter, and gains the detach that clears a look's room key without touching
  its text; the compose panel in SessionView gains the three run switches and
  their subject inputs beside the two checkboxes it already carries; a screen
  for uploading a source library and reading back what the import did; `frontend/src/kinds.js` for the writer's field list, and
  `backend/enhance.py` for the joiner's headings — the two move together or the
  test that binds them fails.
* **Scripts**: a new one-shot translator from the external libraries into the
  seed formats. It reads a path given on the command line - no machine path is
  written into a tracked file.
* **Tests**: the deny-list test, the existing `offers`-names-the-furniture test
  extended over the new rows, and a test that the registry and the seed files on
  disk agree.
* **Docs**: README.md and the matching page under `docs/` for the registry
  setting and the room picker.
* **External**: the source libraries stay outside this repo. No source file is
  copied wholesale, no celebrity list is imported, and no non-English character
  is written anywhere in the tree — the non-English fields arrive only as
  English translations this repo authored and owns.
