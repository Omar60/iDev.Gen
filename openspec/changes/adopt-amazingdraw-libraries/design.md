## Context

See proposal.md — Why. What matters for the approach:

* The source libraries live outside this repo and always will. Nothing here may
  hard-code a path to them, and every test must pass on a checkout where they
  are absent.
* Rooms already have a home and a recorded reason for it.
  `tests/test_catalogue_seed.py:350` states that a room is a starting text for
  `session.look`, lives in a tracked JSON seed, and is deliberately not a fourth
  `component` slot because `component.slot` is the closed vocabulary of the trio
  and a fourth slot would reach the draw. `backend/db.py:240` records the same
  reasoning for the wardrobe: a cell keyed on four slots multiplies the matrix
  and leaves every measurement in it unreachable.
* The catalogue's known failure mode is drift: an import that skips existing
  rows left two green tests reading a stale file.
* The user's requirement is that the source wording is adopted without being
  trimmed or rewritten. Session 394 then measured that line length is NOT paid
  for out of the camera - 87 to 187 words deleted from a written line, same
  seed, 9/16 either way - so the two requirements no longer pull against each
  other as hard as this change was first written to assume.
* What the source entries are worth is their internal agreement. Session 392
  shot four of them and got the camera right in every family, because one author
  wrote the camera, the act, the room and the wardrobe to fit each other. Any
  design that takes them apart has to keep a way back to the whole.

## Goals / Non-Goals

**Goals:**

* One source of truth per kind of row, with no second copy that can drift.
* Adoption that leaves every component independently judgeable afterwards.
* Refusals that are loud, and a guard that a future importer cannot walk past.
* Each phase shippable and measurable on its own.

**Non-Goals:**

* Adopting the source's eight director dimensions. Those are decisions that
  resolve into slots rather than text that reaches a line, and this repo makes
  the same decisions in the catalogue draw and the run's own properties.
* Rendering parity with the source console. The bench comparison is done; this
  change adopts material, not a pipeline.
* Any change to how a cell is scored, or to the crop law.

## Decisions

### Rooms stay in JSON seeds; nothing copies them into the database

Rooms are read-only at runtime. The app reads the registered seed files and
never writes them. A room's verdict is recorded by rewriting the seed, the same
way a room is imported.

*Why:* the recorded reason above, plus the drift lesson. A verdict column in a
table and a `verdict` field in a tracked seed would be two answers to "has this
room been measured", and this repo has found that bug more than once. One file,
one answer, and the answer is reviewable in a diff.

*Alternative considered:* a `room` table with the seed as an import source, like
`component`. Rejected: components need a table because a cell is computed by
joining rows; a room is never joined against anything, so the table buys nothing
and costs a second copy.

### The registry is config, the deny-list is code

Which libraries exist, whether each is enabled and how it is weighted goes in
config, so adding a library is not a code change.

The deny-list is not config. It lives in code with the guard, and the guard has
no override of any kind.

*Why:* the registry is an operational choice and changes often. The deny-list is
a rule about what this project will not carry; a rule that can be edited by
whoever runs the importer is not a rule. Keeping it in code also means the test
suite can assert on it directly.

### Fused entries are mined, never imported whole

A source entry that names a camera position, an act, a room and a wardrobe in
one string is split into separate candidate rows carrying the same source
identifier.

*Why:* those entries render well precisely because one person wrote all four
parts to agree. Stored fused, a good frame cannot be attributed and a bad one
cannot be diagnosed, and the component matrix stops meaning anything. The
project's whole method is measurement per component.

*Trade-off, and how it is paid:* mining loses the agreement. A mined camera and
a mined act drawn together will not be as coherent as the entry they came from -
one author wrote those parts to agree, and session 392 shot four of those
entries and got the camera right in every family. Throwing that away to gain
rows nobody has seen together would be a bad trade on its own.

So the combination is kept beside the parts. Each mined entry records the rows
it was split into and stays composable exactly as it was, which means the
photograph that rendered can always be shot again, while the parts are judged
separately. The two readings then answer different questions: the parts say
which component earned what, the combination says what the source was worth. A
part that dies on its own does not retire the combination, and a combination
that renders does not verify its parts.

*Alternative considered:* a fifth store for fused "shots". Rejected for the same
reason a fourth component slot is rejected - it reaches the draw and multiplies
the matrix. A recorded combination is not that: it is a reference, composable on
request, and it never enters a random draw.

### A participant camera gets a manner of its own, with nothing written in it

The source's POV families put the camera in a participant's hands. Mined into
`directed`, they would inherit an instruction that says someone is photographing
her - which those entries contradict - and a dead verdict would then mean the
mismatch rather than the row.

So they enter a `pov` manner, created by inheriting the baseline manner and
changing only its identity. The baseline carries no instruction prose of its
own, so this invents nothing: `pov` is `directed`'s instruction until somebody
measures a better one, and the rows get a namespace where their verdicts belong
to them.

*Why not put them in `directed` and move them later:* a cell is keyed on the
manner, so verdicts do not transfer. Whichever manner these rows enter, that is
where their measurements live, and moving them afterwards throws the
measurements away. The choice is not which is cheaper to build - it is where
these rows actually belong.

*Why not write the manner's own instruction block now:* `selfie`'s block is
measured prose - its arm rule went 4 of 12 to 12 of 12 on the same seeds -
and this repo does not ship instruction text it has not measured. The block is
later work, and 8.19 is the arm that says whether it is worth writing.

The fisheye family is the exception and does not need this: its camera is an
unattended device in the room, which is what `candid` already describes.

### A room's manner field becomes a permission, not a description

Today a room's `manner` says which register is fused into its text -
`ModelDetail.jsx:14` records why the filter exists at all: candid's first
sentence is an amateur-technique register, so offering it on a directed session
would not give directed a look, it would turn directed into candid.

Separating the register from the place deletes exactly what that field
describes. Two questions were living inside it, and only one dies: which
register the text carries (gone by construction), and whether the PLACE makes
sense under a manner (still real, and about to become live).

So the field becomes a set of allowed manners, defaulting to all. The 428
imported places are allowed everywhere, because the source says nothing about
manners and inventing a restriction would hide 428 rooms from two thirds of the
app for no reason anybody could point at. The one existing exception - a studio
with a softbox and seamless paper, which is a place but only makes sense when
someone is photographing her - is written down with its reason.

*Alternative considered:* dropping the field and offering every room everywhere.
Rejected: it re-breaks what session 381 fixed, and the studio is the standing
proof that some places are manner-bound.

*Alternative considered:* folding the studio into directed's register so no room
needs a manner. Rejected: a directed shoot can happen in a bedroom, so directed's
register cannot carry seamless paper and a softbox. The studio is a place, not
apparatus.

*Consequence that has to be carried:* allowed is not measured. A room verified
under candid is not verified under directed, so the verdict names the manner it
was taken under and a room can hold one per manner. Without that, the default of
"allowed everywhere" would quietly present 428 unmeasured pairings as measured.

### The body profiles are not adopted

The source's 32 adult body profiles answer "who is she this time". This app
answers that with a LoRA: both models trigger the same `zchar_jir` on the same
LoRA file, so it is a one-character studio and the question is already settled.

Adopting them would not add vocabulary, it would add a way to stop photographing
the character without noticing. A reference loses only to a clause that has to be
SEEN, and a body has to be seen - a written `body_shape` beats the LoRA rather
than qualifying it.

So `amateurs.json` is declared as a source this project does not adopt, and
`asset-refresh` refuses it with that reason. That refusal is deliberately
distinct from a deny-list refusal: the deny-list is about material this repo
will not carry, and weakening it to also mean "we had no use for this" would
blunt the one rule that must never be arguable.

*Alternative considered:* a body-fragment library shaped like the rooms.
Rejected for now - a whole new concept, picker and verdict for a case that does
not exist until there is a second character without a LoRA.

*Deferred to its own change, not lost:* the profiles carry concrete anatomical
vocabulary this repo has written down nowhere - skin texture, marks, areola
detail, pubic styling - and the `marks` field measured at 94% arrival is exactly
where it would go. Mined as a REFERENCE DOCUMENT for a human writing a line, not
as rows a draw deals. Two things gate it: the provenance question that gates
phase 5, and whether 32 explicit body descriptions should be tracked text in a
public repo at all. If the answer to provenance is that imported material stays
out of git, this goes with it.

### Tags, weight and mood words each go where they have a consumer

Three source fields had no home. They are not one decision.

**Tags** are adopted whole - 239 unique over 2088 uses, and the frequent ones
are structural rather than decorative: indoor 184, private 170, public 92,
outdoor 60. The picker's tag filter was already specified with no statement of
where tags come from, which was a gap in this change rather than in the source.
All 239 are taken, including the ones used once: a tag that matches one room
still finds that room, and thresholding buys nothing.

**Mood words** go into the room's authoring guidance, not into a second filter.
220 unique, and **165 of them are used exactly once** - three quarters of the
vocabulary is a one-off. As a filter that is 165 filters returning one room
each, which is noise shaped like a feature. As something a human reads while
writing a line, it is the same kind of material as the `notes` and the anchors.

**Weight** is adopted because it now has a consumer: a session can have its room
drawn instead of chosen, weighted, once, and the drawn room is then an ordinary
default the operator can replace. Without that it would have been built for
nothing - the composer draws camera, act and framing, never rooms.

*The outliers are reported, not silently clamped.* 444 of 446 entries carry a
weight of 1 to 5; two carry 93 and 95. Against a total weight of 1693 those two
rooms take 11 per cent of every draw, which is almost certainly a typo rather
than an intention. The import names them and the operator decides, because the
seed is a tracked file they can edit - and clamping on import while storing the
source value would be two numbers answering "how likely is this room", which is
the shape of bug this repo keeps finding.

### Imported material stays out of git; the measurements taken against it do not

Decided by the operator: the source's prose is not committed to this public
repo. What ships is the importer, the deny-list, the tests and their invented
fixtures. What does not ship is the 428 rooms of their wording, the mined camera
and act candidates, and the translation map, which is derived from their labels.

Two consequences have to be built for rather than discovered.

**The picker cannot import the imported seeds at build time.**
`tests/test_catalogue_seed.py` says why in the failure it was written for:
`ModelDetail.jsx` imports the rooms seed at build time, so an untracked seed is
a fresh clone whose FRONTEND DOES NOT BUILD. The nine rooms this repo wrote and
directed's look stay tracked and stay build-time imports, and nothing about them
changes. The imported rooms are read at runtime through a route instead, so an
absent file is an empty list rather than a broken build. That is also the better
shape regardless of git: bundling 428 rooms into the frontend build was never
going to be right.

**A verdict is ours, and it must not leave with their prose.** Verdicts live in
the room seed today, which would put every measurement taken against an imported
room in an untracked file. Measurements are what this repo produces; losing them
to a licensing decision would be the worst trade in this change.

So they are split. An imported room's TEXT lives in the untracked seed; its
verdict, sample size and the manner it was measured under live in a tracked file
keyed by the room's key, carrying no source prose. A key with no room is
reported as orphaned rather than dropped, so a verdict outlives a source that
disappears upstream.

*What is lost, stated plainly:* the review-by-diff that half of `asset-refresh`
was for. A tracked seed made an import readable as a diff before it was kept, and
an untracked one does not. The import report is now the only review surface, so
it has to carry what a diff would have shown - not just counts, but which rows
changed and how.

*Alternative considered:* checking the licence of the source distribution and
committing the material if it permits it. Not rejected on the merits - nobody
has read it. It remains the better outcome if the answer turns out to be
permissive, and this design does not foreclose it: making the seeds tracked
again is un-ignoring a path and moving the verdicts back.

### Four assertions move from the stored room to the composed look

`tests/test_catalogue_seed.py:374` asserts, for every room in the seed, that its
`manner` is `candid`, that its `look` starts with the capture clause, that it
carries the hair sentence, and that it runs 60 to 110 words. Its docstring says
why: the capture clause and the hair are what make twenty frames one shoot, and
sessions 370 and 371 spliced every candidate behind exactly that text.

Three decisions in this change break all four at once - the register/place
split, the manner becoming a set, and rooms arriving from outside. That test is
not decoration and it is not deleted to get green. The invariant it protects
still holds; what changes is WHERE it is true.

So each assertion moves from the stored row to the composed look: the composed
text starts with the register, carries the hair, and runs 60 to 110 words, and
the room's manner assertion becomes an assertion that the room allows the manner
it is being composed for. A stored place-only room asserts none of those,
because none of them is true of a place.

This is the change AGENTS.md permits - changing a test on purpose rather than
deleting it - and 5.2 is where it happens, in the same task that splits the nine
rooms, so the split and its test never disagree.

### Verbatim storage, gated composition

The source's English text is stored byte-identical. The gates act at compose
time and refuse; they never edit stored text, and a successful compose carries
the room text unchanged.

*Why:* this is how both requirements hold at once. The user's "do not trim their
words" is a statement about the corpus; the measured length tax is a statement
about one line. Refusing a line is honest about the conflict where truncating it
would hide it.

### Non-English source text is translated; a translation is authored, not source

The source's non-English fields are translated into English and stored. They are
stored as **authored** text, in fields distinct from the ones holding source
text, so that the two can never be confused in a seed file or a diff.

*Why:* AGENTS.md is English-only with no exceptions, and the room seed stores a
label. So for the rooms this change adopts, translating is not an improvement to
the plan — it is the precondition for adopting them at all. Deriving a label
from the identifier would satisfy the rule while throwing away the fields that
carry the most method.

*What is worth translating, and in what order.* The 525 labels are the largest
group and the least valuable: they name a room for browsing and filtering.
The fields worth the pass are `notes` and the four `*_anchor` fields, because
they state authoring intent and negative constraints rather than naming — an
`action_anchor` saying the subject is bending to restock and not merely standing
and flashing; a `notes` field forbidding crystal sparkle. That is the entry's
author writing down what makes the shot work and what breaks it, which is the
same kind of knowledge this repo keeps in its own measurements. Only 7 adopted
entries carry the anchors, so the highest-value tier is also the cheapest.

Counted over the source: 731 unique non-English strings — 525 labels, 48 profile
descriptions, 36 identity names, 36 anchors over 16 entries, 24 profile display
names, 15 shot variants, 42 notes, 5 pose hints. Refused entries are not counted
and not translated.

*Determinism.* Translations live in a tracked map keyed by the source string, so
one source string always yields one English string. A re-import reads the map
and does not re-translate; a string missing from the map stops the import rather
than being filled in on the fly. This is what keeps a second import from
silently rewording a room somebody has already shot against, and it is what
makes the translations reviewable in a diff of their own rather than scattered
across 470 seed rows.

*Why a map rather than translating in place:* the same label text recurs across
libraries, and a per-row translation would let the same source string become two
different English strings in two files. It also keeps the human review of 731
strings in one file instead of spread over every seed the import touches.

*Alternative considered:* deriving the label from the identifier and dropping
the rest. Rejected by the user, and it was the wrong trade anyway — it drops the
anchors and the notes, which are the only fields in the corpus that explain
themselves.

### The translator is a one-shot script that takes its source path as an argument

It is not wired into the app, has no default path, and writes seed files the
same way the existing seed importers do — updating rows that already exist
rather than skipping them.

*Why:* no machine path may reach a tracked file, and the drift lesson says a
skip-existing import is how a stale file survives.

### The multi-body marking is computed at import and stored

Whether a room puts other people in the frame is decided once, when the room is
imported, and stored on the room.

*Why:* a second reading of the prose at compose time is a second calculation of
the same fact, which is the shape of bug this repo has found four times. It also
means the marking is visible in the diff when the seed is reviewed.

### The session records which room filled its look

A session stores the key of the room its look was filled from. The gates read
the room's stored marking through that key, and nothing at compose time reads
the look's text back to work out which room it came from.

*Why:* the look is editable by hand and is expected to be edited - the register,
the place and whatever the operator adds are one text by the time it composes.
Recovering the room from that text is a second calculation of a fact the pick
already knew, and it fails silently in exactly the case the gate exists for: an
edited look no longer matches its room's stored text, the lookup misses, and a
multi-body room composes into a single-subject run with no refusal.

The key is a column on `session`, empty for every session that exists today and
for every look written by hand. An empty key means no gate: those words are the
operator's own, and this change does not start refusing lines it never refused.

*Why not gate when the room is picked instead:* `with_him` is a property of the
RUN - `backend/main.py:1846` records that reasoning - decided when the shoot is
written, and the look is filled long before. A gate at pick time asks a question
whose answer does not exist yet.

*Why a column and not a lookup:* a room key on the session is provenance, which
is a thing this app has nowhere else to keep. It is also what makes 6.6's
orphan report reach sessions rather than only the verdict store.

*The trap this opens, and the door out of it.* Reading the room and not the text
means the gate is right when the text was edited badly and wrong when it was
edited well: an operator who deletes the sentence that named other people is
still refused, by a rule about words that are no longer in their line. So the
key is detachable. Detaching clears it and touches nothing else, and the look is
hand-written from then on - which is the same state as a look that was typed,
and gets the same treatment. The alternative was re-reading the edited text at
compose time, which is the second calculation this whole decision exists to
avoid.

### The nine rooms carry prose verdicts, and none of them converts to verified

The nine rooms this repo wrote carry free text where the catalogue carries a
vocabulary: `in use since session 351`, and eight rows of `built 1/1 in session
370, furniture used 1/1 in session 371`. A picker that shows verified and
unverified as visibly distinct cannot read those, and 6.9 as first written told
the rooms to keep them, which left the nine founding rooms in a state the new
shape has no answer for.

So they are converted, and the honest conversion is **unverified at a sample
size of one**, not verified. `built 1/1` is one photograph. This repo's own
judging protocol puts the verified/dead bar above n=10 because at ten the bar
sits inside the judge's noise, and a room that rendered once has not cleared
anything. Calling it verified would put the nine rooms above the 428 imported
ones on evidence that does not support the gap.

The prose is not deleted. It moves to a note beside the verdict, because "built
1/1 in session 370, furniture used 1/1 in session 371" says which run and which
question, and the vocabulary says neither.

*Consequence, stated so nobody is surprised by it:* on the day this ships the
picker shows every room unverified, including the nine. That is the true state
of the library, and 6.12's visible distinction then means something the first
time a room is actually measured.

### The fused entries are cut by hand, and the cuts are a file

The 55 fused entries have no delimiter to parse on. Camera, act, room and
wardrobe are one clause of English - "leaning back against the mirror wall
looking up into the lens" is a piece of furniture, a body geometry and a camera
direction in one breath - and a heuristic over that produces fragments that read
as rows and are not.

So the split is a curated cut map: one entry per source identifier, naming the
substring that becomes the camera candidate, the one that becomes the act and
the one that becomes the room, and leaving out the parts that become nothing.
The importer reads it and cuts nothing on its own; an entry missing from the map
stops rather than being guessed at, the same rule the translation map already
sets.

*Why not a parser:* 55 entries is a human-sized afternoon and a parser for that
prose is not. A wrong cut is not a crash - it is a plausible row that enters the
catalogue and gets judged, which spends a measurement run on a fragment nobody
wrote.

*Where it lives:* untracked, beside the translation map, because the cuts quote
source prose. What ships tracked is its shape, the importer that reads it and
the fixtures its tests run against.

### Two of the seven candidate fields are dropped: the look already answers them

`hair` and `makeup` do not ship. Not because they measured badly - the
fourteen-field arm is the one that matched the control - but because the session
already answers both, once, for the whole shoot.

AGENTS.md states the invariant and the UI repeats it to the operator: the look
is hair, makeup, the place and the light, constant, prepended to every
photograph. A per-photograph `hair` field puts a second answer to that question
in the same line. Where the two agree it is repetition, which this repo has
measured as free between photographs and expensive when one field answers
another field's question. Where they disagree - a look that says her hair is
tied back and a photograph that says it is loose across her face - the line
carries a contradiction, and this sampler resolves contradictions by dropping
the position, which is the thing the whole field set exists to protect.

Nothing in the measurement protocol catches this. Arrival and survival are read
off the written line, and both fields arrive; the collision only exists once the
look is prepended, which happens after the writer is done.

So the field set is twelve, not fourteen: `accessories`, `marks`, `style`,
`props` and `story` on top of the seven that ship today. 9.6 re-runs the
measurement at twelve, and the fourteen-field result stands as what it always
was - evidence that the count is not the price.

*The way back, if it is ever wanted:* stop having the look define hair and
makeup, and the fields become the only answer rather than the second one. That
is a change to the session's oldest invariant and it is not this change.

## Risks / Trade-offs

* **470 unverified rooms drown the 9 measured ones** → the picker filters by
  verdict and defaults to showing verified first; the room's verdict is visible
  in the list, not hidden behind a click.
* **Mined rows are worse than the entries they came from** → they enter
  unverified and are kept out of the strict composer until judged. Accepted
  cost, stated above.
* **The word budget may be guarding nothing** → session 394 deleted 87 to 187
  words from a written line, same seed, everything else identical, and the
  camera came back 9/16 either way. So length is not the mechanism it was taken
  for. The gate still ships, because a 200-word room string is a different
  quantity from seven deleted blocks and nobody has varied THAT alone — but it
  ships with a default high enough to refuse nothing, and 7.9 is what sets it.
  See Open Questions.
* **Five new writer fields is a bigger instruction to obey** → measured before
  proposing: three text arms, five runs a side at n=25, 362 lines. The
  fourteen-field arm matched the seven-field control on the handed framing
  (87.4% against 86.8%), on the camera (a dead heat once the per-run spread is
  read) and on the arrival of the pre-existing fields (98-99% in both). Two of
  those seven are dropped for a reason that has nothing to do with the count,
  above, so what ships is twelve fields and 9.6 re-measures at twelve. The risk
  that did land was a different one, below.
* **A field whose subject is usually absent invents one** → this is what
  `tattoo`, `pet` and `liquids` cost: one arm wrote the same fictitious tattoo
  forty times on a character who has none, and dragged every other field from
  98-99% down to 92-94% doing it. They ship as run-level switches, absent from
  the field list entirely when off, which is why `writer-field-set` states that
  rule rather than leaving it to whoever writes the instruction.
* **The guard is only as good as its markers** → the guard is tested against
  fixtures, and refuses on any of four independent signals (library, identifier,
  tags, theme text) rather than one, so a single mislabelled entry does not slip
  through on a technicality.
* **A large seed file is hard to review** → the seeds are split per source
  library, so a diff is scoped to one library at a time.
* **Enabling a library changes what a random draw produces** → weights and the
  enabled flag live in config, and a disabled library contributes nothing, so a
  measurement run can pin the pool it drew from.

## Migration Plan

Phase order, each shippable alone:

1. The guard and its test. Nothing is imported until this is green.
2. The registry, plus the existing nine rooms moved behind it. No new rooms yet;
   this proves the registry against material already known to work.
3. The room import. One library at a time, largest last.
4. The compose gates, with the budget defaulted high enough that no existing
   session changes behaviour.
5. Perspective mining into candidate rows.
6. The writer field set: the five new fields, and the three run-level switches
   for the subjects that are usually absent.

Rollback: every phase is additive. Phases 2-3 roll back by disabling a registry
entry. Phase 4 rolls back by raising the budget and clearing the multi-body
gate's requirement. Phase 5 rolls back by removing rows that are unverified
anyway. Phase 6 rolls back by restoring the field list and the joiner's headings
together, which the test that binds them will insist on.

## Open Questions

* **What is the room word budget, and is there one at all?** Two sessions have
  now pushed against it and neither found a length effect. Session 391: a
  242-word line kept its camera 3/3 while a 224-word line lost it, so total
  length is not the predictor. Session 394, paired and much stronger: one line
  shot twice, once as written and once with 87 to 187 words of whole blocks
  deleted, same seed, everything else byte-identical — 9/16 against 9/16. What
  391 really found was the open-garment reading, not a word count.

  So the budget may be guarding nothing, and the honest default is one that
  refuses nothing until 7.9 varies room length alone against a fixed camera row.
  If that arm also comes back flat, the requirement stands but the gate becomes
  a tripwire for absurd inputs rather than a real constraint — which is still
  worth having, because it is the difference between a refusal and a silent
  truncation. The answer changes the default and the framing of the risk, not
  the specs.
* **Should a mined act carry its source's wardrobe clause?** The source fuses
  them. Deferring: the act rows enter unverified either way, and the question is
  answerable when the first mined act is judged.
