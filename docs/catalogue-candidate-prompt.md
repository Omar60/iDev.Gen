# Asking an outside model for candidate wordings

A new catalogue component starts as a guess about words. This is the request
that turns "I want this kind of photograph" into candidates worth rendering.

The measurement is still ours: the outside model writes the candidates, the
anchor cell decides which one lives. See `catalogue-measurements.md`.

## Rules of the ask

Measured 2026-08-17 over ~1300 renders, sessions 151-179.

* **Never give a sample line.** A verbatim example is copied whole — the good
  form just as literally as the bad one. Ask for the level of detail instead.
* **Cap the obvious form.** Left free, every candidate is a rewording of the one
  phrasing you would have written yourself.
* **Ask for no ranking and no confidence.** A model that scores its own answers
  biases them toward what it can defend, which is not what renders.
* **Fresh chat per request.** The second request inherits the first.
* **Ban only vocabulary measured to misfire.** A rule with no measurement behind
  it costs a candidate and buys nothing.

## The request

Fill the four braces and paste. Nothing else.

---

You are writing candidate phrasings for a text-to-image prompt system. I
describe one photographic property; you return different ways of asking for it
in words.

What you need to know about where these land:

* The final prompt is one paragraph of plain English sentences joined with full
  stops. Your phrasing is appended to a fixed block — a character trigger, a
  base description, the clothes. The whole paragraph has to stay under 85 words,
  so your phrasing has a hard budget of **{{BUDGET}} words**.
* The subject is a young woman, always named as such in the paragraph.
* The image model is a Krea 2 / SDXL-family finetune. It reads the paragraph as
  language, not as tags. Comma-separated keyword fragments do not work.

**What I want: {{TARGET}}**

Return **{{N}}** candidates that ask for it in genuinely different ways —
different sentence shape, different head word, different vocabulary. Not {{N}}
rewordings of one idea. At most two of them may be built on {{OBVIOUS_FORM}},
which is the phrasing I would have written myself.

{{SLOT_CONSTRAINTS}}

For each candidate return exactly three fields:

* `wording` — what goes in the prompt.
* `judge_label` — what someone LOOKING at the finished photograph would say they
  see, written so they could pick it out of a list without ever having read the
  wording. It must not be the wording restated.
* `family` — one word. Candidates asking for the same photograph share a family.

Return a JSON array and nothing else. No explanation, no ranking, no
confidence, no preamble. I am going to render all of them and count.

---

## The slot constraints

Paste the block for the slot you are asking about.

### camera

* Name the camera as a camera — "an overhead camera above her". The adverbial
  form — "seen from above", "at floor level" — is ignored by this model.
* Say which side of her the camera stands on. A phrasing that leaves it unsaid
  renders frontal.
* A height or a vertical angle must be the HEAD of the phrase and must carry
  nothing after it. Any tail — "behind her", "in front of her" — makes the
  height be ignored and the frame come back at eye level.
* Name no furniture, no room and no object. The phrasing has to be true of a
  photograph taken anywhere.
* Do not say how much of her is in frame, how close the camera is, or where the
  picture cuts. That is a different field.
* Do not ask for a ninety-degree side profile. This model does not have one.

### act

* One sentence, present tense, third person.
* Name both bodies with a noun each. A pronoun is allowed only for a body
  already named in the same sentence. A sentence where only one body is a noun
  renders one person.
* The sentence must state that two people are in the frame.
* Do not name the position by its common name alone. The name on its own renders
  what the word literally means.
* Do not attach a fixed detail of the man's hands. One repeated detail describes
  one geometry and flattens every candidate toward it.
* Say which way each body faces, and what each one is resting on or braced
  against.

### framing

* Distance and crop only: how much of her is in the frame and where it cuts.
* Name no camera position, no angle and no room.
* Do not ask for a close-up on the face. A close-up owns the scene and the
  photograph stops being about anything else.

## What to do with the answer

One catalogue row per candidate, then one anchor cell each: the candidate in its
slot, everything else held at values already verified, same manner and same
checkpoint. Ten photographs, judged blind. Below `judged=10` the cell reads
`unknown`; at ten or more it is `verified` at 8-in-10 or better and `dead`
otherwise. A dead anchor ends that candidate — the rest of its row is not worth
shooting.

## Asking from a photograph instead of from a description

Describing the photograph you want in words puts a translator between you and
the model, and the translator is where the contamination enters: a target
written as "tipped down and toward the camera side" produced eight candidates
that all leaned sideways, and the correction ("no lean to either side") put a
negation in every one of them. A photograph has no translator.

Same output contract as above, plus a `reading` — what the model says it sees —
so a decomposition of the wrong photograph is caught before any render. Three
forms per slot rather than eight: nine cells is what covers all three slots of
one photograph in a day.

Attach the photograph and paste:

---

You are reading one photograph and turning it into three separate prompt
phrases for a text-to-image system. I will render your phrases and count how
often they reproduce the photograph you are looking at.

The system joins three independent phrases into one paragraph of plain English:

* the CAMERA phrase says where the camera stands relative to her.
* the ACT phrase says what the bodies are doing.
* the FRAMING phrase says how much of them the picture holds.

They are measured separately, so each phrase must answer only its own question.
A camera phrase that describes the pose, or an act phrase that says how close
the lens is, cannot be attributed and is wasted.

WHAT NOT TO CARRY OVER. The photograph contains things that belong to other
fields of my system and must appear in none of your phrases: the room, the
furniture, the floor and walls, the clothes, the light, the time of day, her
hair, her face, her age, her build, and anything about who she is. Write only
what would still be true if the same bodies were arranged the same way in an
empty studio.

If the photograph does not settle something — the camera's height is ambiguous,
a hand is hidden — say so in the reading and do not invent it in a phrase.

FIRST, a reading. One object describing what is actually in the photograph:
how many people are in frame, where the camera is relative to them, what each
body is doing and what it is in contact with, and where the picture cuts.

THEN, nine candidates: three per slot, one for each form below.

camera — cap 15 words:
  1. the camera as a noun that does something
  2. a prepositional phrase attached to the photograph ("Taken from…")
  3. an adverbial of viewpoint with no camera named at all

act — cap 35 words:
  1. the geometry named explicitly, every body a noun, and how many are in frame
  2. the arms, the hands and the contact points in detail
  3. written as a photographic subject rather than as an action

framing — cap 15 words:
  1. where the top edge cuts and where the bottom edge cuts
  2. what fills the picture, said as the thing the picture is of
  3. a photographic term of art for this crop, named as such

Each candidate carries exactly five fields:

* slot — camera, act or framing.
* form — 1, 2 or 3, matching the list above.
* wording — what goes in the prompt.
* judge_label — what someone LOOKING at a finished photograph would say they
  see, in plain spoken words, naming only what is visible, written so they could
  pick it out of a list without ever having read the wording. It must not be the
  wording restated.
* family — one word naming the photograph, position or crop it asks for. All
  three candidates of a slot share it, since all three ask for the same thing.

Return one JSON object: {"reading": {...}, "candidates": [...]}. Nothing else.
No explanation, no ranking, no confidence, no preamble.

---

### What comes back still needs reading

Measured on the first run of it, 2026-08-28: three of the nine candidates
answered another slot's question anyway — a camera phrase aimed "at the bent
figure", two framings cropping "the bent torso" and "the folded form" — and the
three act candidates each named the subject differently ("One person", no
subject at all, "The subject"), which moves the subject noun and the form
together and makes a failure unattributable.

Both are defects rather than forms. Edit them out before loading: the wordings
are yours, and what is being measured is the form.
