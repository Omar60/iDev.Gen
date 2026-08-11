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
short so it does not compete for attention. For a genuinely identical garment
you need a reference in the workflow — IP-Adapter, img2img from a keeper,
inpainting — which lives in your ComfyUI graph, not here.

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
be run again.

## Where the files are

`<data folder>/sessions/<session id>/<shot id>_<shot label>.png`. They are moved out
of ComfyUI's output, so nothing is duplicated and ComfyUI's folder stays clean.
Deleting a session from the app deletes its folder too. If a file is locked and
the folder survives, the app says so instead of leaving it quietly behind —
session numbers are reused, and a leftover folder would show its photos inside a
later session.
