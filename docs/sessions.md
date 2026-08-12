# Sessions

**Model → Session → Shots.** A model is one character: its LoRA, trigger word,
strength, base prompt and default size. A session is one shoot with that model —
and, like a real shoot, one **look**.

## The look

Wardrobe, hair, makeup, styling, the place. Written once, at the top of the
session, and identical in every photo it produces. That is what makes the output
a session instead of a pile of unrelated renders: change the dress and you are
shooting a different session, so make a different one.

```text
white summer dress, hair down, gold hoop earrings, on a beach at golden hour
```

## The shots

A shot is what varies within that look: pose, angle, framing, where in the place
the camera is. One line each, plus how many variations you want. Six shots at
four variations is a twenty-four photo session.

```text
full body, walking, mid-stride
close-up, chin slightly down, eyes to camera
three-quarter view, hands in pockets, looking away
sitting on the sand, leaning back on both arms
```

Do not repeat the wardrobe here — it is already in the look, and repeating it
only fights the sampler for attention.

## How the prompt is built

`trigger, model base prompt, session look, shot`. If the shot text contains
`{trigger}` the trigger goes exactly there instead of being prepended:

| Piece | Value |
|---|---|
| Model trigger | `4da woman` |
| Model base prompt | `photo, 35mm` |
| Session look | `white summer dress, on a beach` |
| Shot | `full body, walking` |
| **Sent to ComfyUI** | `4da woman, photo, 35mm, white summer dress, on a beach, full body, walking` |

A shot's own negative overrides the model's default negative; leave it empty to
inherit.

## Seeds

- **Random** — a different seed per shot. What you want for a real shoot.
- **Fixed (from…)** — the session starts at the seed you give and increments per
  variation inside a shot. Reproducible, and comparable across shots. It has to
  increment: the same seed with the same prompt would render the same photo N
  times.

Every shot stores the seed it used, shown under the thumbnail and in the
lightbox, so a keeper can be reproduced.

A word on what a seed does **not** do: it fixes the initial noise, not the
wardrobe. Two takes with the same seed and different prompts share a tendency in
composition, not the same buttons, neckline or fabric. What keeps an outfit
consistent is describing it precisely in the look (`white linen midi dress, thin
straps, square neckline` rather than `white dress`) and keeping the shot line
short so it does not compete for attention. For a genuinely identical garment,
work from a photo instead of from words — see *Reference takes* below.

## Reference takes

Text to image has a limit you hit fast: **you cannot take something off the
look.** The look is prepended to every shot, so a take that says
`without the jacket` is sent as `…leather jacket…, without the jacket`. The
positive both describes the jacket and denies it, and the jacket usually wins.

A reference take solves it by changing what the prompt *is*. Instead of
describing a photo, it gives an instruction on one you already have:

| | Text to image | Reference take |
|---|---|---|
| Starts from | noise | a photo of the session |
| Prompt is | a description | an instruction |
| Sent to ComfyUI | `4da woman, photo, 35mm, leather jacket, standing` | `remove the jacket` |

Nothing restates the jacket, so there is nothing for the instruction to fight.

**How to shoot one:**

1. Assign the session a **reference workflow** — an img2img or instruction-editing
   graph with its `Reference image` slot mapped. It is a second workflow, next to
   the usual one; the session uses whichever the take needs. Pick it when creating
   the session, or later: tick `ref` on a take in the *add shots* panel and the
   selector appears there, because deciding to edit a keeper happens looking at
   the gallery and not before the shoot.
2. Tick **ref** on the takes that are edits. Leave it off on the take that shoots
   the photo they edit.
3. Run. The first photo the session produces becomes its reference automatically,
   and the edits that follow work from it — one Run, not two.
4. **📎** on any finished photo makes it the reference instead; **📌** marks the
   current one. Up to three, for graphs that take several images (a character
   plus a garment, say).

A reference take carries **no trigger, no base prompt and no look** — that is the
whole point, and the reason it works. Its `Denoise` and `Reference strength`
appear in the session settings once a reference workflow is picked; left empty,
the workflow's own values stand.

### The strength dial

`Reference strength` is the one number that decides what a reference take can
do, and the two things you want pull it in opposite directions:

- **High** holds the frame still. A garment edit — lower the jacket, undo the
  zip — lands cleanly, and the pose does not move.
- **Low** loosens the frame so the pose can move, and garment edits get sloppier
  as identity drifts.

Asking one take for both a new pose and a wardrobe change is asking the dial to
be in two places, which is why that take comes out half-done. There is no
setting that wins both; there is a value that suits what you are shooting.

Finding it is one session, not four: the box on a `ref` take overrides the
session, so the same prompt at several strengths sits side by side in the
gallery. **⚖ on a reference photo** fills the panel with exactly that sweep —
one prompt, one seed, four strengths — and the before/after wipe shows what each
one moved. A take with the box empty follows the session, and `0` is a real
setting, not "unset".

**What this does not fix.** Whether an edit keeps the face while changing the
clothes is the editing model's job, not this app's. A plain img2img model
(FLUX.1 Krea and friends) has no instruction mode: the denoise high enough to
remove a garment also moves the face, and the denoise low enough to keep the face
leaves the garment on. For real edits use an instruction model —
**FLUX.1 Kontext** or **Qwen-Image-Edit** — which is a different workflow to
import and map, and no change to the app.

## Running

The queue is **serial** — one photo at a time, one active session — because
there is one GPU. Photos appear in order as they finish.

- **Run** queues the pending shots.
- **Cancel** interrupts the running job in ComfyUI and marks the rest cancelled.
- **Retry** puts failed and cancelled shots back in the queue.
- A single failing shot does not stop the session; it keeps its error text in
  place of the thumbnail.

The final status reads as the outcome, not as "the queue emptied": a session
that produced at least one photo is **done** even with failures among them, one
where *every* shot failed is **failed**, and one you stopped is **cancelled**.

Closing the app mid-run leaves the session marked failed on the next start,
because nothing is polling that job any more. Retry picks it back up.

## Reviewing

- **Stars** rate a shot 1–5. Clicking the same star again clears the rating.
- **✕** rejects a shot: it dims and disappears under the *Without rejects*
  filter. **↩** brings it back. Nothing is deleted.
- **Picks only** shows 4★ and above.
- Clicking a photo opens it full size with its prompt, seed and filename.
- A reference take opens with a **before/after wipe** instead: the reference on
  the left of the slider, the edit on the right, both on the same frame. An edit
  that only moves a collar is invisible when the eye has to travel between two
  pictures. The comparison uses the reference that take actually ran against,
  which is pinned to the shot — re-pointing the session later does not rewrite
  what already happened.

## Shooting more

**⟳ More like this** copies that photo's exact prompt into the *add shots* panel
with four fresh variations — exact, because it already carries the trigger, the
base prompt and the look, and composing it a second time would repeat all three.

**⚖ Tweak on this same seed** does the same but pins the keeper's seed and asks
for one photo. Change a word in the prompt, queue it, and the difference you see
is that word — with a new seed you would be comparing two things at once. The
seed box in the shots table does the same by hand: empty follows the session,
filled pins it (and still shifts by one per variation, so two variations are not
two copies).

**+ Shots** starts from an empty take instead.

Both append to the current session and keep its look: a shoot whose wardrobe
changed halfway is two sessions, so the look is read from the session and not
from whatever the panel sends. A finished session goes back to *draft* and can
be run again. Reshooting a reference take stays a reference take — coming back
as a fresh text to image would quietly be a different picture.

## Where the files are

`<data folder>/sessions/<session id>/<shot id>_<shot label>.png`. They are moved out
of ComfyUI's output, so nothing is duplicated and ComfyUI's folder stays clean.
Deleting a session from the app deletes its folder too. If a file is locked and
the folder survives, the app says so instead of leaving it quietly behind —
session numbers are reused, and a leftover folder would show its photos inside a
later session.
