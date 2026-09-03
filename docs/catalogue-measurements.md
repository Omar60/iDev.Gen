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
