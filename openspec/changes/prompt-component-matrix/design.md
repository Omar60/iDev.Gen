## Context

See proposal.md — Why. What follows is only the state that shapes the approach.

Two layers exist today and they have opposite track records. The **catalogues**
(`CAMERA_POSITIONS`, `CANDID_POSITIONS`, `SELFIE_POSITIONS`, `ARRANGEMENTS`,
`BODY_OPENINGS`, `TECHNIQUE_DEFECTS`, `KISS_FRAMES`, `EXPRESSIONS`) are lists of
`{ family, line }` copied verbatim into the prompt; an entry's effect is local
and adding one has never moved another. The **instruction prose**
(`SHOOT_LINE_INSTRUCTION` and the per-manner text, together far longer than the
catalogues) is one body of text serving every manner and all seven fields at
once; its effects are non-local and measurable only at n≈25 against a writer
whose own run-to-run spread is 5-6 points.

The shufflers already exist — `cameraPlan`, `spreadOver`, `fitCameras`,
`arrangementPlan` — and so does a composer without a writer, eight times over:
each `scripts/shoot_*.py` fixes the look, the framing and the seeds by hand and
swaps one field. That practice, and the `shoot → judge_camera.py --question X
--repeat 3` protocol around it, is the thing this change is naming and moving
into the app.

The camera is already out of the writer's hands: `cameraPlan` deals the position
before the writer is asked, `CAMERA_FORMS` is deleted, and the writer only words
the framing. The kiss frame is the exception and it matters here — `shootLines`
overwrites the dealt camera with `KISS_CAMERA[manner]` at every planted kiss, so
there is a fourth camera source with override semantics, not three lists.

The writer itself still runs and is still the default. It writes `act`, `her`,
`him`, `worn`, `technique` and `face` against `SHOOT_LINE_INSTRUCTION` in
chunks, with the dealt camera handed to it as context. Nothing in this design
removes it.

Field identity is coupled across the two languages: `SHOOT_FIELDS` in
`kinds.js` and `BLOCK_HEADINGS` in `backend/enhance.py` must carry the same
seven keys in the same order, and `tests/test_enhance.py` asserts it. Reshaping
concepts must not silently reshape field names.

Today a failed component is expressed by deleting it. `behind`, `back` and
`side` are gone from `ARRANGEMENTS`, and `tests/test_arrangements.py` asserts
they never return as keys. The failure is preserved only as prose, so the
wording that failed — the one thing a re-test needs — is not in the data.

Constraints that decide the shape:

- One local GPU, one session at a time. Render is ~25 s a photograph. The
  operator has time and no per-call cost, so sample size is cheap.
- The vision judge runs on the same GPU as the sampler, so judging does not run
  alongside rendering — it serialises behind it. Judging is the bottleneck, not
  rendering, by roughly an order of magnitude.
- Two of this project's judge questions returned plausible numbers and were
  wrong; both were caught by opening an image, not by a control.
- A verdict is checkpoint-bound. Krea 2 reaches no ninety-degree torso profile
  and finepornV4 renders one from the same clause.

## Goals / Non-Goals

**Goals:**

- A component's text has exactly one home, so adding one has an effect.
- Every verdict carries the four things that make it a verdict: concept,
  wording, manner, checkpoint.
- The composer can queue a shot with no writer in the loop, which for the first
  time gives a control to measure the writer against.
- Ordinary use fills the matrix, so coverage grows without dedicated runs.

**Non-Goals:**

- Replacing the writer. It stays, unchanged, and remains the default path.
- Full-grid coverage of every manner and checkpoint. The design has to converge
  under partial coverage, because complete coverage is not reachable.
- Calling any external image service at runtime. Reference images are authored
  by hand, outside the app, and land as files.
- Bringing `her`, `him` and `worn` into the catalogue in this change. They are
  the fields with no measured verbatim requirement and they are where the writer
  keeps earning its place.

## Decisions

### A component is a concept with several wordings, not a line

An entry today is one string, so a `0/n` cannot distinguish "the model cannot
paint this" from "this sentence did not describe what I meant". The catalogue
already gestures at the split — `overhead` carries two wordings, `noise` two,
`framing` two — grouped under `family`. This makes it explicit: a concept holds
candidate wordings, and evidence attaches to the wording.

The payoff is the axis that was never varied. `back` and `side` were each shot
in exactly one form, on two checkpoints and up to four cameras — the camera axis
was exhausted and the sentence never was. Under this model they are not dead
concepts; they are one dead wording each, and they sit in a re-test queue.

*Alternative rejected:* a flat list plus a `deny` map of known-bad pairs. It
records that something failed but not what was tried, so the re-test queue
cannot exist and the same wording gets shot a third time.

### One concept shape, and reuse is a key, not a pointer

There is exactly one shape for a catalogue entry — key, slot, wordings — and
nothing else is a concept. A component that reuses another's wording does it by
holding that component's **key**, resolved against the catalogue at the point of
use; it never becomes a second kind of entry whose wording is a pointer.

This was decided against a working implementation. `KISS_CAMERA` — the camera a
kiss frame takes, per manner — carried three plain strings, each a third copy of
text already in `CAMERA_POSITIONS` or `CANDID_POSITIONS`. The first fix gave it
its own concept list whose entries had a `ref` where their `wordings` should be.
It removed the duplication and bought a second entry shape for it: every walk of
the catalogue would then need to know that some concepts must be dereferenced
before they have text — starting with the single-home test in 1.3 and again with
the cell in 2.1, which has nothing to index on an entry with no wording.

What it is instead: `KISS_CAMERA` maps a manner to a key in **that manner's own**
camera catalogue, and `kissCameraFor` returns the camera concept itself with an
`override: 'dealt-camera'` tag on it. A kiss camera is an ordinary camera
component with a tag, and the tag — not a separate list — is what records that it
replaces the camera the spread dealt.

Resolving inside the manner's own catalogue is the part worth keeping. Scanning
every catalogue for the first matching key works only while the manners word a
shared key identically; `front-direct` lives in both the directed and the candid
list today, and the day one of them is reworded a manner-blind scan starts
returning the other manner's sentence.

*Alternative rejected:* the `ref` shape above. It reads as the smaller change
because it keeps the override concepts visible as a list of their own, but the
list is the cost, not the benefit — it is a second thing to keep in step with
the catalogue it points into, and a `ref` naming nothing is a failure that
resolves to `null` far from the typo that caused it.

### The unit of evidence is a cell: (concept, wording, manner, checkpoint)

Anything less is not a verdict. Dropping `manner` merges the directed camera
list with the candid one, and `behind` is 3/3 in the first and 0/6 in the
second. Dropping `checkpoint` merges Krea 2 with finepornV4, which disagree on
the profile from identical text.

A cell holds one of three states, and the third is load-bearing: `verified`,
`dead`, `unknown`. `unknown` is not `dead`. Most of what this project currently
treats as ruled out is `unknown` at n=3.

### n=10 to admit, 8 of 10 to pass

Bounds below are **one-sided 95% Clopper-Pearson**, one method throughout:
`0/3` 0.632, `3/3` 0.368, `0/10` 0.259, `8/10` 0.493.

One-sided because every question asked here is directional. Admitting a cell
asks only how low the true rate could be; killing one asks only how high. A
two-sided interval spends half its alpha on a direction nobody asked about and
makes each bound look worse than the question warrants. Clopper-Pearson rather
than Wilson because these are small samples at the extremes — 0 of n and n of n
— which is exactly where the normal approximation behind Wilson is least
trustworthy.

At n=3 neither outcome is a conclusion: `0/3` admits a true rate up to ~0.63 and
`3/3` admits one as low as ~0.37. n=10 is where `0/10` starts to mean something
(true rate at most ~0.26). Since GPU time carries no per-call cost, the only
thing sample size spends is the judging pass, and the human judge below makes
that cheap.

8 of 10 admits a cell rather than demanding 10 of 10, and its own lower bound is
weak (~0.49) — deliberately. Admission is not certainty; the confusion matrix
below is what distinguishes a tolerable cell from a bad one, and cells that turn
out to matter get topped up to n=20 later. Encoding a stronger statistical rule
now would be inventing precision the instrument does not have.

### Fill the first cell of a row first, and stop there on failure

Every entry is measured first against one fixed anchor line, all else held. Below
8 of 10 there, the rest of that wording's row is never shot. `back` and `side`
would have cost 20 photographs instead of 41.

This is not a separate screening phase with its own rules — at n=10 the anchor
cell is a real measurement, and it is simply the first cell of the row. One
mechanism, not two. What the anchor cell cannot do is *approve*: the rest of the
line is fixed during it, so a pass there means the row is worth continuing, not
that the component is verified in general.

### The judge is a human, answering a forced choice, blind

Judging by vision model is the bottleneck — a full grid at n=10 across the
coupled slots is on the order of 10^5 vision calls on the GPU that is also
rendering. A human at ~3 s a photograph clears the same grid in a couple of
hours, and cannot fail silently the way a mis-designed question does.

The screen shows the photograph **without its brief** and asks which camera
family, act or framing is in it, choosing from the whole list. Not "is the
camera behind her? y/n": an operator who knows the answer being sought will see
it. Forced choice is blind by construction, and it produces the confusion matrix
for free — where the failures land. That `back` and `side` both collapse into
the sampler's default upright arrangement is a fact that cost this project
sessions to notice and would have fallen out of this screen automatically.

One question per pass over a batch, not three questions per photograph:
switching question per item is what makes a human slow.

*Alternative rejected:* keeping the vision judge with the cheap preview chain
(~16x). Still six figures of calls, and it inherits the failure mode where a
badly framed question returns believable numbers.

### The reference image defines the entry; it is never shown to the judge

A reference image is authored outside the app — any capable image generator —
and stored with the concept as what that concept means. It exists because
writing a new pose from nothing is exactly how `back` and `side` entered, and
because the solo `act` catalogue has to be written from scratch.

It is an authoring and documentation aid on the concept, and it is deliberately
absent from the judging screen: showing the target to the judge is the bias the
forced choice was designed to remove. Pose geometry is the same clothed, so this
use is unaffected by any generator's content policy.

Its second use is diagnostic, per wording rather than per cell: a pose the
reference nails and the checkpoint misses is a base-model limit and the wording
should stop being rewritten; one both miss is an ambiguous wording.

### Strict and exploratory draws; one engine for shot and session

`strict` draws only `verified` cells — the everyday path. `exploratory` draws
`unknown` cells and records what came back, so the sessions the operator runs
anyway become the instrument that fills the matrix. This is what makes partial
coverage converge without dedicated grid runs.

A single independent shot and a session are the same draw. The session adds
ordering constraints on top — the wardrobe walks one way, `spreadOver` and
`cameraPlan` already spread the camera across the takes — rather than a second
composer.

### A dead wording is kept and marked, never deleted

Deletion is the current mechanism and it destroys the only thing a re-test
needs. `back` and `side` were each shot in exactly one form; the form is now
recoverable only from a prose comment, and `tests/test_arrangements.py` actively
forbids the keys returning. Under this design a wording carries `dead` with its
`n` and its checkpoint, and the concept stays drawable the moment a second
wording passes. The `noneDead` assertion is replaced by one that says a `dead`
wording is never drawn — same protection, without losing the evidence.

### Strict mode names its behaviour when the pool runs out

The verified pool starts nearly empty and stays small for a while. A 40-take
session drawing strictly from three verified cameras is one photograph taken
forty times, which is the exact failure `cameraPlan` exists to prevent — and
`fitCameras` today papers over a thin list by taking the first family the manner
offers, which is a fallback, not a policy.

So strict mode declares what it does on exhaustion rather than degrading
quietly: it **refuses the whole composition and names the empty slot**, rather
than repeating a component or silently borrowing an `unknown` one. Falling back
to `unknown` would make strict and exploratory the same mode with different
labels, and repeating is the defect the whole catalogue is for.

The granularity is the composition, not the photograph. Forty takes asked for
with thirty-eight fillable is not thirty-eight good photographs plus a warning:
the ordering constraints are computed over the whole run, and a shoot delivered
short without being asked is a surprise in the gallery rather than a decision.
Refusing is not the same as being unhelpful, though — the refusal SHALL carry
the largest count strict mode *could* fill, so the operator can take that number
or switch to exploratory, rather than bisecting by hand.

### `repeats` moves from the line to the draw

`enhance.js` refuses a line that repeats a line the shoot already has, asked at
both the writer and the repair. The composer does not write lines, it assembles
them, so it gains a check the writer cannot make: two photographs are duplicates
when their **component tuple** matches, and that is decidable before anything is
queued rather than after a model answers.

The tuple check is an addition, not a replacement. The two catch different
things and neither subsumes the other — distinct tuples can join into
near-identical text, and identical tuples are trivially identical text. So the
existing joined-line check keeps running over composed lines as well: it is
already written, it costs nothing, and turning it off for the composer is what
would leave a composed shoot able to repeat itself in a way nothing notices.
That also covers a session carrying both composed and written lines, which is
otherwise the one case neither check owns.

### `worn` is a stream and stays outside the catalogue

`her`, `him` and `worn` are out of scope for this change, but not for the same
reason. `her` and `him` are simply prose the writer is good at. `worn` is
structurally different: it is produced by `wardrobeProgression` as an ordered
walk where each state is defined by carrying the previous one verbatim, so it
has no enumerable pool to draw from and no cell to verify — a wardrobe state is
only correct relative to the state before it.

Bringing `worn` in later means modelling garments as items with fixed phrases
and a state as a subset, which is a different data model from a concept with
wordings. Naming that now keeps the cell model from being stretched to cover
something it does not fit.

### The catalogue stays a versioned module; evidence goes in SQLite

The component text stays in the repo, where it is reviewable in a diff and
readable by `scripts/*.py` through node, and where the measured commentary sits
beside the entry it explains. Evidence is the opposite kind of data — it is
written by the app on every judged photograph — and belongs in SQLite with the
sessions.

*Alternative rejected:* the whole catalogue in SQLite. It would split the source
of truth away from the six scripts that read it, and the long measured comments
that carry most of the catalogue's value have nowhere to live in a row.

## Risks / Trade-offs

- **Coverage may never reach the point where strict mode is usable.** The full
  space is on the order of 10^3 cells and 10^4 photographs. → Strict mode
  refusing loudly (above) makes an inadequate pool visible instead of silently
  degrading, and exploratory mode is what grows it. What is *not* yet known is
  how many verified cells a 40-take session actually needs before strict is
  worth offering; that number comes out of the first filled manner, not out of
  this document.
- **The judging-versus-rendering bottleneck is estimated, not measured.** The
  order-of-magnitude claim above comes from arithmetic on call counts, not from
  a timed run. → Time one batch of each before building the screen around the
  assumption; the design does not change if the ratio is smaller, only the
  urgency does.
- **The anchor line is a confounder.** A pass at the anchor cell may be the
  anchor helping. → The anchor cell only continues a row; it never marks a
  component verified. That distinction is in the spec, not in a convention.
- **A human judge is inconsistent across sessions and drifts.** → Forced choice
  over a fixed list rather than free judgement, and a small set of already-judged
  photographs re-shown periodically as a control on the judge.
- **Coverage grows quadratically; the grid is never finished.** → `unknown` is a
  first-class state and `exploratory` mode is the fill mechanism. The system has
  to be useful at partial coverage, and strict mode is exactly that.
- **A component verified alone can still fail in combination.** The coupled trio
  is camera × act × framing — a close framing on the face renders a different
  act on all nine checkpoints. → The cell is defined over the trio, not over
  single components, so combination failure is representable. Slots the evidence
  says do not compete (`technique`, `face`) stay outside the grid.
- **Seeding the matrix imports old verdicts taken at n=3.** → Seeded cells carry
  their real `n`. A seeded 0/3 lands as `unknown`, not `dead`; only `back`
  (0 of 12 and 0 of 12) carries enough to seed as `dead`, split per
  checkpoint as the source records them rather than as the 0-of-41 sum a
  test derives. `side` (0 of 9 and 0 of 8) does NOT carry enough: 9 and 8 are
  below the n=10 threshold the spec says is the minimum for a verdict at
  all, so `side` lands as `unknown` despite the zero ratio. The wording of
  a dead verdict matters: a cell seeded dead for one wording must remain
  `unknown` for the other wordings of the same concept.

## Migration Plan

1. Reshape the catalogues into concepts with wordings, keeping every existing
   string as the first wording of its concept and bringing `KISS_CAMERA` in.
   Behaviour-neutral: the shufflers draw the same lines, and `SHOOT_FIELDS` /
   `BLOCK_HEADINGS` are untouched.
2. Add the evidence store and seed it from the verdicts already paid for,
   including the wordings currently deleted, as `dead` against the one wording
   and checkpoint they were shot on.
3. Add the composer in strict mode beside the writer path, reading the reshaped
   catalogue. Both remain.
4. Measure the composer against the eight fixed-line scripts at n=10 — the
   scripts are the control, since they are hand-built composers of the same
   line. This is the first point at which a before/after is possible at all.
5. Add the judging screen and judge an already-judged session with it, against a
   verdict already known, as a control on the screen itself.
6. Add exploratory mode, once strict mode has a pool worth drawing from.
7. Remove the two inline camera examples. Last, and alone, because it is
   cleanup: verbatim reuse falls to about 1 and the shoot does not change.

Step 4 is where the measurement lands, and it is deliberately after the composer
rather than before it: until the composer exists there is no "after" to compare,
only the writer measuring itself.

The writer path is untouched throughout, so rollback at any step is dropping the
new path rather than restoring the old one.

## Open Questions

- Whether `technique` and `face` eventually need cells of their own. Current
  evidence says they do not compete for the same photograph, and adding them
  later does not change the cell model.
- How the re-test queue orders itself once several dead wordings accumulate.
  Needs the queue to exist first.
