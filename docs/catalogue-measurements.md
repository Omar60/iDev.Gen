# Catalogue Measurements

This document records the measurement history and empirical observations behind
the initial prompt component catalogue.

## Directed Cameras

- Measured in sessions 227 and 228 on directed shoots.
- The 9 furniture-free forms (`front-direct`, `shoulder-left`, `shoulder-right`,
  `side-right`, `side-left`, `behind-direct`, `overhead-direct`, `overhead-high`,
  `floor-low-angle`) reliably establish camera perspective without requiring room
  or furniture detection.
- Bed-anchored forms (such as `Overhead camera directly above the bed` or
  `Side-angle camera at mattress level`) are intentionally omitted from the standard
  catalogue to prevent false furniture insertions in non-bed settings.

## Candid Cameras

- Measured on renders 2026-08-23 (sessions 245-250) across 9 arms and 5 follow-up
  runs with blind evaluation.
- `behind` wordings failed under candid (0/6, returning frontal views).
- `floor` positions failed under candid (0/3).
- Propped mounts (`Phone propped on a high shelf...`) reliably reach overhead perspective
  without requiring a height word.
- Mentioning `phone` does not paint unwanted handheld phones in the frame, except in
  `mirror-selfie` where the mirror reflection naturally shows the device.

## Selfie Cameras

- Based on selfie sessions (sessions 155 and 161).
- Includes candid forms plus two POV forms: `pov-low-chest` (looking down body) and
  `pov-above-back` (looking down while lying on back).

## Arrangements (Acts)

- Evaluated across sessions 265, 266, 269, 270, and 271 with blind judging:
  - `astride`: 18/22 photographs arrived; compatible with front, overhead, mirror, and pov camera families.
  - `reverse`: 3/3 arrived from shoulder family; mirror and overhead rendered weakly.
  - `wall`: 3/3 arrived from mirror family; shoulder is second fallback.
- Dropped arrangements, and what later moved:
  - `back` (0/12 on finepornV4, 0/12 on Krea 2 mix) and `side` (0/9 on finepornV4, 0/8 on Krea 2 mix)
    collapsed to upright front poses. Every one of those attempts swapped the camera;
    none swapped the sentence.
  - `behind` (all fours) failed across sessions 155, 161, 267 and 268, returning front
    or kneeling poses.
  - **Session 296 (2026-08-25, finepornV4) found the route, so "dropped" here means
    dropped from the catalogue and not proved impossible.** A third object that forces
    a height difference between the two bodies beats the collapse: `back` with a bed
    edge and him standing landed 3/3 from the side camera against its own 0/3 plain
    control, and a head-and-shoulder anchor on a pillow rendered all fours 8 of 9
    without the words appearing in the line. The side camera is the only one that
    reads a horizontal body against a vertical one — overhead is 1/18 across six arms.
    Nothing is in the shipped catalogue yet: this was measured on finepornV4 and the
    Krea leg is unjudged.

## The Wardrobe Against Body Geometry

Measured 2026-09-03, sessions 369 and 372-377, 110 photographs on
`Moody-Krea-Mix-premium`, candid's bench, ten shared seeds per arm, judged by
looking at one thing: are her shoulder blades against the mattress.

`on her back` had failed eight wordings on directed's bench and then rendered
20/20 on candid's. Four things differed between the two benches. Each was
substituted back one at a time, everything else held:

| session | what was substituted | on her back |
|---|---|---|
| 369 | the phone removed from her hand (control) | 20/20 |
| 372 | camera -> `side view`, directed's two-word term | 9/10 |
| 373 | look -> room deleted / look deleted entirely | 10/10, 10/10 |
| 374 | wardrobe -> directed's camisole / no clothing sentence | **2/10**, 10/10 |
| 375 | voice -> `The young woman lies` for `She lies` | 10/10 |
| 376 | camisole without its straps and neckline / knickers alone | 4/10, 10/10 |
| 377 | knickers written to the camisole's length, 20 words | 10/10 |

**A written garment on her chest is the only thing in the bench that moved the
geometry.** The camisole arm came back propped against pillows or sitting
upright seven times in ten, which is the failure mode of the original eight. It
also moved the camera to a low POV from her feet on the same frontal camera
clause every other arm carried.

Three things this rules out, each with its own arm rather than by argument:

- **Not the length of the wardrobe sentence.** 20 words below the waist render
  10/10; 19 words including a camisole render 2/10.
- **Not the descriptive detail.** Deleting `with narrow straps and a softly
  rounded neckline` moved 2/10 to 4/10, which is no separation at n=10.
- **Not the camera, the room, the look or the subject phrase.**

The surviving reading is that a written garment has to be SEEN and a chest
garment is only visible on a raised torso. It is a hypothesis, not a
measurement: knickers are equally visible on a body lying flat, so the "must be
seen" account and a plain "clothing above the waist" account have not been
pulled apart.

Two side results from the same runs:

- **The look holds the character.** Blonde 10/10 with candid's full look, 7/10
  with its room deleted, 2/10 with no look at all, and two of the no-look frames
  added glasses. This is the first lever measured on the identity drift.
- **Deleting the room sends the camera overhead** and overrides `full body`: all
  twenty frames of session 373 are top-down and cropped head-to-thigh, on a line
  whose camera clause says `Taken from directly in front of her`. Consistent
  with the room being the framing control.

None of these 110 photographs is recorded in the `cell` table, and none can be:
they are verbatim written lines, so `shot.components` is `{}` and
`POST /api/shots/{id}/judge` refuses them by design; and the cell is keyed on
the trio, which does not include the wardrobe -- the dimension this experiment
moved. Recording them would have meant seven arms landing on one key.

## The Wardrobe Against a Second Geometry

Measured 2026-09-03, sessions 378 and 379, 40 photographs on
`Moody-Krea-Mix-premium`, directed's bench (empty look, `side view`, `full
body`, workflow 8), ten shared seeds per arm, judged by looking at one thing: is
her far shoulder hidden behind the near one.

The act is `wall-facing-forearms`, taken verbatim from
`data/directed-acts-seed.json`. It is the row that delivered a clean ninety-degree
profile in session 366 -- the geometry filed as a base-model limit after nine
wordings at 0/24 -- and 366 wrote no wardrobe at all. Only the wardrobe sentence
moves between arms.

| arm | the wardrobe sentence | words | profile |
|---|---|---|---|
| `unwritten` | none | 0 | **9/10** |
| `camisole` | directed's, chest and knickers | 19 | 0/10 |
| `long-knickers` | below the hip, matched length | 20 | 0/10 |
| `top-only` | the camisole with its knickers clause removed | 15 | 0/10 |

**The chest-garment rule does not generalise.** On `on her back` the same words
written below the waist cost nothing (10/10); here they cost the geometry
outright. `top-only` was shot to ask whether the black knickers common to both
dead arms were the cause, and it says they were not: a camisole alone dies too.

What replicates across the two geometries is weaker and simpler than the rule it
replaces: **a written wardrobe can cost a hard geometry, and where the garment
sits is a property of the pose rather than a law.** The waist split holds for
`on her back` and for nothing else yet measured.

Two things settled on the way:

- **Session 366's profile was not a lucky single frame.** The control arm is
  9/10 -- the one miss is a rear view, not a three-quarter -- so this row is the
  first reliable route to the ninety-degree profile that costs neither a depth
  map nor a second body. It has earned an anchor cell.
- **A written garment closes the crop.** All thirty dressed frames are cropped
  at the thigh with no feet, on a line ending `full body`, against ten full-body
  frames in the control. The dressed arms also turn her back to the lens and
  make her buttocks the subject. Consistent with the wardrobe clause selecting a
  different genre altogether, which is what session 366 said from the other
  side.

Length is ruled out by the control rather than by argument: zero words renders
9/10 and fifteen, nineteen and twenty words all render 0/10, so the arms are not
ordered by length.

## The Wardrobe Against a Third Geometry

Measured 2026-09-03, sessions 384, 385 and 386, 50 photographs on
`Moody-Krea-Mix-premium`, directed's bench with the look that shipped in session
381 (`data/directed-looks-seed.json`), `side view`, `full body`, workflow 8, ten
shared seeds per arm, judged by looking at one thing: are her shoulders on the
floor AND both legs pointing up.

Two geometries disagreed about the wardrobe. `on her back` splits at the waist
-- a chest garment is 2/10 and the same twenty words below the hip are 10/10.
The ninety-degree profile does not split at all -- every written garment is
0/10. The common half is "a written garment costs a hard geometry"; the waist
split is `on her back` only. A third geometry says which half is the law.

### The screen: one candidate was never a geometry

Both candidates were shot with NO wardrobe first, ten seeds each, and only the
survivor was worth dressing. Session 378 could not buy off a dead control in
advance; here it costs ten frames instead of thirty.

| candidate | control, no wardrobe |
|---|---|
| `lying-legs-vertical` | **10/10** |
| `shoulders-down-hips-up` | **0/10** |

`shoulders-down-hips-up` fails in a way worth keeping: eight of its ten
photographs put her ON HER FEET OPERATING THE TRIPOD the look describes. With
an act the sampler cannot parse, the props named in the look become the act.
The row is dead as written and its wording is the suspect, not the pose.

### The wardrobe is inert on this geometry

| arm | the wardrobe sentence | words | legs vertical |
|---|---|---|---|
| `unwritten` (384) | none | 0 | 10/10 |
| `camisole` (385) | directed's, chest and knickers | 19 | **10/10** |
| `long-knickers` (385) | below the hip, matched length | 20 | **10/10** |

**Neither half of the rule is a law.** A camisole that takes the profile to 0/10
and `on her back` to 2/10 costs this geometry nothing at all. What replicates
across three geometries is only this: a written wardrobe costs SOME body
geometries and nothing on others, and both which garment and whether any garment
matters are properties of the pose. There is no wardrobe rule to carry to an
unmeasured row.

### The look, not the wardrobe, is what this geometry is priced in

The premise was that `lying-legs-vertical` is hard, and that rests on session
365, where it came back sitting up at n=1 -- with the camisole AND with the
empty look directed had before session 381. Every arm above carries the look, so
one more arm was owed: 365's exact condition, the camisole with an empty look,
nothing else moved.

| arm | look | legs vertical |
|---|---|---|
| `camisole` (385) | directed's, 90 words | 10/10 |
| `empty-look` (386) | none | **6/10** |

So the geometry is not free. It is held up by the look, and on this bench the
wardrobe cannot be seen to cost anything because the look is already paying.
The four misses are the frontal attractor, not a sitting torso: knees bent and
splayed at the lens, the crop centred between them -- the same photograph
session 368 produced across 25 frames. One miss is outright sat up, which is
365's failure mode.

Two rendered consequences ride on that one written key, and this arm does not
separate them: with no room written the camera goes overhead and the floor
becomes a bed, which is exactly what session 373 measured when it deleted
candid's room. So "the look" here means the register, the hair, the light and
the room together.

## The Look Decides Which Hair Colour a Session Is

Sessions 380 and 381, thirty photographs, 2026-09-03. The control was already
shot: session 378's `unwritten` arm is the same act, the same ten seeds and the
same empty wardrobe, so each arm moves ONE key -- the look.

**Read the first version of this section as retracted.** It counted blondes and
called every brunette frame identity drift. The LoRA was trained on both: Jiroko
is blonde and can be brunette, and neither is the wrong woman. What the counts
actually measure is whether the ten frames of a session agree with each other,
which is the requirement a session has -- one look, held constant, or it is not
one shoot.

| arm | look | wardrobe | blonde | brunette | agree | profile |
|---|---|---|---|---|---|---|
| `unwritten` (378) | empty | none | 7 | 3 | 7/10 | 9/10 |
| `camisole` (378) | empty | 19 words | 4 | 6 | 6/10 | 0/10 |
| `top-only` (379) | empty | 15 words | 2 | 8 | 8/10 | 0/10 |
| `hair-only` (381) | one sentence | none | 1 | 9 | **9/10** | 10/10 |
| `candid-look` (380) | candid's | none | 10 | 0 | **10/10** | 10/10 |
| `directed-look` (381) | directed's | none | 10 | 0 | **10/10** | 10/10 |

**A look of any length pins the colour; an empty look leaves the session
undecided.** Three frames in ten disagreeing is not a wrong character, it is a
shoot that is not one shoot, and that is the defect worth fixing. Which colour
you land on is a property of the look's text, not of its length: one sentence
about her hair lands on brunette 9 times in 10, and either of the two full looks
lands on blonde 10 times in 10.

Session 373 found the same shape from the other side on a different pose and
called it identity too; re-read it as colour agreement.

## What directed's look should say

`directed-look` is the text this session shipped, written to candid's structure
-- register, hair, light, room -- and in directed's own voice: a tripod instead
of a phone, shaped light instead of a bare bulb, a working studio instead of a
bedroom at night. Candid's own text was never a candidate: its first sentence is
an amateur-technique register, and pasting it into directed would not give
directed a look, it would turn directed into candid.

It wins on every count that was measured:

- **colour agreement 10/10**, level with candid's look and above `hair-only`;
- **the profile 10/10**, full body with her feet in frame, against 9/10 and a
  frame cropped at the knee with the hair sentence alone;
- **the room it names is built** -- softbox, tripod, seamless paper roll and
  reflector arrive, which is [[idevgen-room-ban-foreign-only]] behaving as
  measured, and it answers the code's own complaint that a directed shoot with
  no room reads as posed in a void.

`hair-only` is not a failure and is worth keeping in mind: one sentence buys
9/10 agreement. It costs the framing, which is the reason it is not the default.

## The wardrobe moves agreement the same way it moves geometry

On the empty-look bench, writing a garment takes agreement from 7/10 to 6/10 and
8/10 while taking the profile from 9/10 to 0/10. Not decisive at n=10 by itself,
but it points where the geometry pointed: the wardrobe clause selects a whole
photograph -- woman, angle and crop together -- rather than dressing the one the
rest of the line describes.

## The First Anchor Cell for the Profile

Session 382, ten photographs, 2026-09-03. Everything before this on the profile
was a verbatim written line, which leaves `shot.components` empty and makes a
cell impossible by design. This one went through the composer:

    camera   side-view            act  wall-facing-forearms
    framing  crop-full-body       look directed's, from data/directed-looks-seed.json
    wardrobe muted -- `mute_wardrobe: true`, because any written garment takes
             this geometry to 0/10

The composed line came back byte-for-byte identical to session 381's
`directed-look` arm, and the ten photographs reproduce it on fresh random seeds:
the ninety-degree profile 10/10, full body, the studio built, the hair agreeing
10/10. **`cell` now holds `side-view / wall-facing-forearms / crop-full-body`
at judged 10, arrived 10.**

Judged blind by the vision model through the app's own `/api/enhance`, with the
user's explicit go-ahead to send the photographs, using the new
`scripts/judge_cell.py`: the model gets one photograph and the slot's readings
as a lettered menu, shuffled per photograph, and nothing else. Control shots
from other sessions were interleaved in the same run and answered correctly
every time -- lying photographs read `lying` 2/2, thigh-cropped photographs read
`head-to-thigh` 2/2 against a deck that read `whole-body`.

### The first pass found a vocabulary hole, not a dead cell

Run against the catalogue as it stood, the act pass came back **5
`forward-bend`, 5 nothing, 0 `standing`** -- and `wall-facing-forearms` was filed
`standing`, so the cell would have been written `arrived 0/10`: a dead verdict on
a pose that renders perfectly.

The component was mis-filed and the vocabulary had no word for it.
`standing` reads "torso upright, hips not folded"; the three `forward-bend` rows
are all a deep fold with the torso parallel to the ground and the hands at the
shins. A woman leaning onto a wall with her shoulders forward of her hips is
neither. Two directed acts sat in that gap -- `wall-facing-forearms` and
`table-hands-flat` -- so the fix is a family and not a re-filing of one row:

    leaning   She is on her feet with her legs straight, her hands or her
              forearms resting on a surface in front of her, and her shoulders
              carried forward of her hips.

Written from the ten photographs, not from either component's `judge_label`,
which is the contamination path the readings feature exists to remove. Re-run
against it, the same deck reads `leaning` 9/10 and then 10/10, controls 2/2.

### The camera slot cannot be judged this way and was not written

The same pass on the camera came back `hip-level` 6, and `side-level` -- what the
line asked for -- once. That is not a miss: **directed's 21 camera readings mix
two independent axes**, the horizontal position (front, side, back,
over-shoulder) and the height (eye-, shoulder-, hip-, ground-level), and both are
true of every photograph at once. A single-pick question cannot measure that, and
posting it would have marked a camera that is side-on in all ten as arriving in
one.

Nothing was recorded for the camera slot. This is the same defect already
suspected in `shoulder-level` against `eye-level`, and it is now measured rather
than suspected: the vocabulary needs splitting into two questions before any
directed camera cell means anything.

## Splitting the Camera Question, and Re-judging the Cell

Two defects came out of session 382's camera pass, one in the vocabulary and one
in the method. Both are fixed here and the cell was re-judged from scratch.

### One menu has to have one true answer on it

Directed's 21 camera readings asked two independent things at once -- where the
camera stood around her (front, side, rear, over-shoulder, pov) and how high it
was (eye-, shoulder-, hip-, low-, ground-level, high-angle, overhead) -- and both
are true of every photograph. Asked as one menu, a camera that is side-on in 10
of 10 came back `hip-level` 6 and `side-level` 1.

`reading.axis` names the question a reading answers. A pass gives one axis and
gets that menu, plus only the photographs whose drawn family is on it; the rest
asked a different question and are not misses. **A vocabulary that carries axes
now refuses a pass that names none**, so the menu that produced that number
cannot be served again -- and the judging screen grows a "Question" chip row for
exactly the slots that need it. Every other vocabulary -- candid's cameras,
every act, every framing -- carries no axis and is untouched.

Three readings were deleted outright: `back`, `three-quarter-front` and
`three-quarter-back` were families no component uses, and their labels were
word-for-word copies of families that are used.

Four synonym pairs survive and are listed in `tests/test_catalogue_seed.py`:
`catalogue-seed.json` holds the nine furniture-free cameras as sentences and
`directed-cameras-seed.json` holds the 49 terms, and the same camera carries a
different family in each -- `side`/`side-level`, `shoulder`/`over-shoulder`,
`behind`/`rear`, `floor`/`ground-level`. Both families need a reading so both
readings say the same thing. **Re-filing them onto one family each was tried and
reverted**: it breaks `test_the_plan_holds_its_three_properties` and
`test_a_planted_arrangement_gets_a_camera_that_can_see_it`, because the family
names are load-bearing in the arrangement-to-camera compatibility data. That is
its own change.

### The judge samples, so it is asked three times

`backend/enhance.py` calls the model at `temperature: 0.8`. **The same
photograph asked twice gives different answers**, and it cost the first camera
result: a rehearsal read `side-level` 7 / `over-shoulder` 3 and the recording
run -- same deck, same seed, same photographs -- read `side-level` 10. Neither
number was more true than the other.

`judge_cell.py --repeat` now asks each photograph three times and takes a strict
majority; three different answers is recorded as unreadable and posted for
nothing. This is what `judge_camera.py` has always done and what this script
should have done from the start.

### The cell, re-judged

All ten photographs were cleared and every slot re-run at three passes:

| slot | result | controls |
|---|---|---|
| act | `leaning` 10/10 | lying photographs read `lying` 2/2 |
| framing | `whole-body` 9/10, `extreme-wide` 1 | thigh-cropped read `head-to-thigh` 2/2 |
| camera (position) | `side-level` 7/10, `over-shoulder` 2, no majority 1 | rear-three-quarter read `over-shoulder` 2/2 |

**judged 10, arrived 7 -- `dead`**, one photograph below the 8-in-10 bar. The
first recording of this cell said 10/10 and was a single noisy pass; this is the
honest number.

Read it as a verdict on the CAMERA and not on the pose. The act arrives 10 of 10
and the framing 9; what costs the trio is `side view`, directed's deliberately
weak two-word camera term. The obvious next arm is the same act and framing with
a camera clause that is a sentence instead of a term.

## The Camera as a Sentence, Against the Same Term

Session 383, ten photographs, 2026-09-03. Same act, same framing, same look,
same wardrobe muting, same checkpoint as session 382; the only clause that moves
is the camera.

    382   `side view`                                       the two-word term
    383   `Taken from her right side, her body in full profile`   the sentence

The sentence is `side-right` from `catalogue-seed.json`, added to the store for
this run rather than reworded, so the arm measures the catalogue's own form.

| cell | act | framing | camera | judged / arrived | state |
|---|---|---|---|---|---|
| `side view` (382) | `leaning` 10/10 | `whole-body` 9/10 | `side-level` 7/10 | 10 / 7 | dead |
| `side-right` (383) | `leaning` 10/10 | `whole-body` 10/10 | `side` 8/10 | 10 / **8** | **verified** |

**Read this as a threshold crossing and not as a measured difference.** Eight
against seven at n=10 separates nothing; the two cells land on opposite sides of
the 8-in-10 bar by one photograph. What the arm does show is that the sentence
is not WORSE, and that the camera is the limiting slot in both -- the act
arrives 10 of 10 either way and the framing 9 or 10.

Two things the arm changes at once, said out loud: the sentence is longer than
the term AND it names which side she is seen from, where `side view` left the
sampler to choose. Every sentence form in this catalogue names a side, so the
two cannot be separated without writing a form the catalogue does not hold.

The misses in both arms are `over-shoulder`: the judge reads a few frames as
more behind her than beside her. That is the honest residue, and it is what a
third arm would have to attack.

### The first run of this arm was unreadable, and the reason is worth keeping

Judged against the vocabulary as it stood, session 383 came back `side-level` 5,
with **three photographs of ten having no majority in three passes**. The votes
were splitting between `shoulder` and `over-shoulder`, and between `behind` and
`rear` -- pairs that are one sentence under two keys, so two passes could agree
on the answer and still fail to agree on a key.

`judge-pass` now drops a reading whose sentence is already on the menu, keeping
the key the deck actually drew. Re-run against the deduped menu, the same
photographs came back `side` 8, `over-shoulder` 2, no ties at all.

**Merging the families was tried first and reverted.** Camera family names are
shared vocabulary BETWEEN manners -- an arrangement row names the camera
families that can see it, in one list serving directed, candid and selfie -- so
renaming directed's out from under them breaks the camera fitting. The
duplication belongs to the catalogue; what the judge is shown does not have to
carry it. A first attempt also re-filed candid and selfie rows by matching on
slot alone, which the suite caught.

## RETRACTION: the `dead` cell was the judge, and the third camera has no target

The section above records `side view` as a dead cell at 7 of 10 and treats
`over-shoulder` as the honest residue a third camera wording would have to
attack. **Both readings are withdrawn.** The residue is judge noise.

### What the photographs said

The two frames the judge called `over-shoulder` in session 383 were put beside
two it called `side`. They are the same photograph: one breast in outline
against the background, both torso edges showing, the far shoulder hidden, feet
in frame. Nothing distinguishes a miss from a hit by looking.

### What a second judging said

The same ten photographs, judged again at three passes with a different seed and
nothing recorded:

| deck | first run | second run | which frames missed |
|---|---|---|---|
| 383 `side-right` | `side` 8/10 | `side` 9/10 | **different frames each time** |
| 382 `side-view` | `side-level` 7/10 | `side-level` 9/10 | different frames each time |

A defect that moves to a different photograph when you ask again is not in the
photograph.

### The measurement that matters

**At n=10, the verified/dead boundary is inside the judge's own noise.** Four
three-pass runs over two decks that are really the same camera gave 7, 8, 9 and
9. The bar is 8. So a cell judged at three passes anywhere near the bar is not a
verdict, and `side view` was filed dead on the low end of a spread whose high end
is `verified`.

Re-judged at **five** passes, both cells, same controls, method fixed in advance
and applied to both whatever it said:

| cell | camera | judged / arrived | state |
|---|---|---|---|
| `side view` (382) | `side-level` 10/10 | 10 / 9 | **verified** |
| `side-right` (383) | `side` 9/10, 1 lost to an API error | 10 / 10 | **verified** |

Both cameras work. Neither cell is dead. The sentence and the term are the same
measurement, which was the reading of the previous section and is the only part
of it that survives.

### Rules this leaves

- **Five passes, not three,** for any discrimination as fine as side against
  over-shoulder. Three is enough for act and framing, where the controls never
  wavered.
- **Do not call a cell dead within one frame of the bar at n=10.** Either shoot
  ten more photographs or judge more passes; the boundary is narrower than the
  method.
- A cell's counts should come from one method. These two mix five-pass camera
  answers with three-pass act and framing answers, which is flagged here rather
  than hidden: the act and framing controls were unambiguous and re-judging them
  would not move a number.
- Deleting a `cell` row while leaving `shot.verdicts` in place makes the counts
  unrecoverable through the endpoint -- it sees a photograph already asked, adds
  nothing, and creates no row. Both cells here were rebuilt from `shot.verdicts`,
  which is how this repo repaired the same shape once before.
