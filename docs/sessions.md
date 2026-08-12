# Sessions

**Model → Session → Shots.** A model is one character: its LoRA, trigger word,
strength, base prompt and default size. A session is one shoot with that model —
and, like a real shoot, one **look**.

## Session kinds

Under the hood there are two paths — paint from noise, or edit a photo — and
four jobs people actually shoot. The **kind**, picked at the top of the new
session panel, says which one this is:

| Kind | Shoots | Reference workflow |
|---|---|---|
| **Photoshoot** | new photos from the look | none |
| **Photo edit** | instructions on one photo: wardrobe off, new pose, new background | an instruction-editing or img2img graph |
| **Camera angles** | the camera walked around one photo | an angle-LoRA graph |
| **Scene + subject** | two photos into one frame | a two-image graph |

The kind changes no generation rule. What it does is stop you rediscovering the
same four things:

- it offers only the workflows tagged for that job (see
  [workflows](workflows.md#kinds)), and picks the graph outright when there is
  only one;
- new takes start the right way round — ticked as `ref` for the three editing
  kinds, unticked for a photoshoot;
- the prompt examples match what that kind's takes look like;
- and it prints the one rule that decides whether the shoot works at all, next
  to the takes instead of in this page.

A session created before kinds existed has none, and behaves exactly as it did:
no badge, no filtering, every workflow offered.

### Carrying a photo into the next job

The four kinds are one workflow, not four: shoot, keep one, edit it, then walk
the camera around what came out. The **→** menu on any finished photo does that
move in one click, and offers each editing kind twice:

- **Continue here** — the session switches kind, picks the graph tagged for the
  new job, marks that photo as the reference and opens the *add shots* panel
  ready for it. One session, one gallery, the graphs taken in turn.
- **In a new session…** — the photo is *copied* into a fresh session of that
  kind, already its reference, and you land there. Copied, not linked: the two
  sessions own their files, so deleting either one leaves the other's gallery
  whole.

Neither needs the photo to leave the app. Downloading a keeper and importing it
back was the old way across sessions, and it lost the model, the settings and
the reason the photo existed.

The look does not travel — an edit take carries none, which is
[the point](#reference-takes) — and neither do the takes. Nothing is
switchable mid-Run: the runner re-reads the session before every take, so the
menu is disabled while the session runs.

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

1. Pick the kind — **Photo edit** — and with it the session's **reference
   workflow**: an img2img or instruction-editing graph with its
   `Reference image` slot mapped. It is a second workflow, next to the usual
   one; the session uses whichever the take needs. Decided later instead? Tick
   `ref` on a take in the *add shots* panel and the selector appears there,
   because deciding to edit a keeper happens looking at the gallery and not
   before the shoot.
2. Tick **ref** on the takes that are edits. Leave it off on the take that shoots
   the photo they edit.
3. Run. The first photo the session produces becomes its reference automatically,
   and the edits that follow work from it — one Run, not two.
4. **📎** on any finished photo makes it the reference instead; **📌** marks the
   current one. Up to three, for graphs that take several images (a character
   plus a garment, say).

Until one is set the session says so, above the gallery, and says which of the
three ways applies to it — because *which photo do these takes edit* is the
question the panel used to answer only by refusing the Run. When **every** take
is an edit and nothing would shoot the photo they work from — a camera-angle
shoot, typically — **Import photo…** brings one in and it becomes the reference
on arrival. You imported it to edit it; marking it by hand afterwards was a step
whose only outcome was a refused Run for whoever forgot.

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

## Camera angles

The dial above trades pose against identity because an editing model is
registered to the photo it was given: it edits that frame, it does not walk
around the subject. Moving the camera is a different job, and it needs a model
trained for it — an instruction model plus a **camera-angle LoRA**, imported and
mapped like any other workflow and tagged *Camera angles*.

Those LoRAs are driven by a short camera line rather than by prose, and the
vocabulary is closed — a handful of directions, three heights, three framings.
Words outside it are ignored, so a take reads like
`<token> right side view eye-level shot medium shot`. That is what the **angle
picker** writes: chips for direction, height and framing, a box for the LoRA's
own trigger token if it has one, and one take per combination you tick. Eight
directions at eye level in a medium shot is one click, not eight lines typed
identically enough to compare. The rows it adds are ordinary takes — edit,
delete or reshoot them like any other.

They are reference takes like any other, too: no session look, no base prompt,
nothing restating the frame.

**Anchor on the widest frame you have.** This is the rule that decides whether
angle takes work at all, so the app now prints it above the picker: the model
can only turn what the photo shows it. Anchored on a close-up, a request for the
subject's back comes back as a mere profile, and any full-length framing invents
the clothing below the crop — a dress can return as a swimsuit. Anchored on a
standing full-body shot, the same prompts land on the first try, wardrobe intact.

Reach for the anchor before reaching for the LoRA strength. Pushing the angle
LoRA past its default buys no extra rotation; it spends the reference's fidelity
instead, and the background starts growing scenery that was never in the photo.

One thing to check when mapping such a workflow: `Steps` and `CFG` live on the
**session**, and the session drives both its workflows. A base model wanting 20
steps and an editing graph on a 4-step turbo LoRA cannot share one number, so
leave those slots unmapped in the editing workflow and let it keep its own — see
[workflows](workflows.md#what-unmapped-means).

### Chaining edits

An instruction model changes the pose too — that is what it is for. Turning the
camera is the part it cannot do alone, which is why the angle graph carries an
angle LoRA. Loaded, that LoRA is trained to hold the pose while the camera
moves, so asking one take for a new pose *and* a new angle asks it for two
opposite things and gets half of each.

**One graph, one job.** The camera-angle graph does angles; a second import of
the same instruction model *without* the angle LoRA, tagged *Photo edit*, does
poses. (A workflow can also be imported twice under two names, with `LoRA
strength` mapped to the angle LoRA's own strength — then a session that sets it
to `0` neutralises it, no second export needed.)

Both jobs in one shoot, in this order:

1. Kind **Photo edit**, the instruction graph. Shoot the pose changes.
2. 📎 the keeper — the widest, cleanest frame you got.
3. **⚙ Settings** → kind **Camera angles**, reference workflow the angle graph.
4. The angle picker builds the sweep; Run.

Pose first, because step 3 wants a wide clean frame and step 1 works from your
best one. The reverse chains a re-pose onto a back view, which is the weakest
frame in the session. Every hop is a generation on top of a generation and
identity drifts a little each time, so keep it to two and anchor on the keeper,
never on the merely acceptable. What already ran keeps the reference it ran
against, so the before/after wipe stays honest across the switch.

### The identity pass

Two or three edits in and the face has drifted a little. The cure is the graph
you started with: **plain img2img with the character LoRA, at a low denoise**.
The LoRA re-imprints the features it was trained on, and a low denoise keeps the
frame it is re-imprinting them onto.

📎 the drifted photo, point the session at the img2img graph (kind *Photo
edit*), set **Denoise** and **LoRA strength** in ⚙ Settings, and add the take.

**The prompt is a description, and this is the one edit where that is true.**
Every other reference take is an instruction because an instruction model reads
one. Plain img2img does not: it repaints the whole frame at whatever the denoise
allows, and the prompt is simply what it repaints *towards*. There is no wording
that means "only the face" — nothing but the denoise limits the area. So the
best prompt is the most faithful description of the photo as it stands, leaving
nothing for the sampler to invent:

```text
4da woman, photo, 35mm, natural skin texture, sharp focus, black dress,
standing, hands on hips, three-quarter view from the right, medium shot,
plain studio wall, soft frontal light
```

Trigger first — the LoRA is doing the work, and a reference take never prepends
it for you. What not to write:

| Not this | Why |
|---|---|
| `keep the same pose`, `same outfit`, `restore the face` | There is no instruction mode here. They describe nothing in the frame and compete with what is in it. |
| `detailed, 8k, masterpiece` | At denoise 0.3 that moves the whole style. |
| `beautiful face`, `perfect face` | Over-weights the face and changes it, which is the one thing you came to avoid. |
| anything not visible in the photo | It gets painted in. |

Describe the photo you have, **not the one you asked for**. After a pose change
and an angle change the original prompt no longer matches the frame: `front
view` over a photo that came back three-quarter turns it a little further. ⟳ on
the session's original photo copies its composed prompt — trigger, base prompt
and look already in it — into the panel; tick `ref` and fix the pose and angle
words to match what you are actually looking at.

Finding the denoise is the usual sweep: four takes, one pinned seed, `0.2 /
0.25 / 0.3 / 0.4`, read with the before/after wipe.

| Denoise | What it does |
|---|---|
| 0.15–0.2 | barely touches the face; sometimes not enough |
| 0.25–0.35 | re-imprints features and skin, keeps pose and wardrobe |
| 0.45+ | identity comes back and the outfit and framing go with it |

If 0.3 is not enough, **two passes at 0.25** — 📎 the output of the first — beat
one at 0.45. And this pass goes at the *end* of the chain, never in the middle:
a high denoise gives you the face back by throwing away the angle you paid for.

## Scene + subject

Some graphs take two photos and put them in one frame: a character and a
garment, a character and a place. They are reference takes with two anchors, and
**slot order is role** — the graph decides which of `Reference image` and
`Reference image 2` is the subject, so mark them with 📎 in that order. The run
is refused unless the number of anchors matches the number of mapped reference
slots, because a two-image graph handed one photo does not fail, it invents the
other half.

Both photos have to exist before the takes run: shoot them, or bring them in
with *Import photo*.

**What this does not fix.** Whether an edit keeps the face while changing the
clothes is the editing model's job, not this app's. A plain img2img model
(FLUX.1 Krea and friends) has no instruction mode: the denoise high enough to
remove a garment also moves the face, and the denoise low enough to keep the face
leaves the garment on. For real edits use an instruction model —
**FLUX.1 Kontext** or **Qwen-Image-Edit** — which is a different workflow to
import and map, and no change to the app.

## Fixing a session

**⚙ Settings** on a session reopens its **kind** and what a refused Run — or a
second batch with a different job — turns out to need. Each saves as you change
it:

- **Kind** — a shoot changes job halfway on purpose; see *Chaining edits* above.
  Without moving the kind, the reference selector filters away the very graph
  the next batch needs.
- **Workflow (new photos)** — the graph for takes with `ref` unticked. An
  editing or camera-angle graph in this box is the classic mix-up: it belongs in
  the next one.
- **Reference workflow (edits)** — the graph for takes marked `ref`.
- **Base model** — only applied to the workflow above, and only if that workflow
  maps the slot. A checkpoint ComfyUI no longer reports is shown as such rather
  than silently reading as *no choice made*.
- **Denoise** and **LoRA strength** — the two dials an identity pass is made of.
  They used to be settable only while creating the session, which is before you
  have the photo whose face drifted.

Photos already shot keep what they were shot with; these apply to what runs
next. The reason this exists: you learn a graph is in the wrong slot when Run is
refused, and by then the session is an imported photo and seventy takes.
Delete-and-start-over should not be the cure for a dropdown.

While the session is **running** they are locked, and the panel says why: the
runner re-reads the session before every take, so a graph swapped mid-queue
would quietly send the rest of the shoot somewhere else.

One related rule, so a refusal points at the right thing: the base model and
LoRA checks apply to the graph that will actually run. A session whose pending
takes are **all** reference edits never loads the first workflow, so a base
model it does not map is not being ignored — nothing is going to ignore it — and
the run is allowed.

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
