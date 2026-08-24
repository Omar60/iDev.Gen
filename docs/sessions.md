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

Hair, makeup, the place, the light. Written once, at the top of the session, and
identical in every photo it produces. That is what makes the output a session
instead of a pile of unrelated renders: change the light and you are shooting a
different session, so make a different one.

```text
hair down with a centre part, soft natural makeup, on a beach at golden hour
```

## The wardrobe

The clothes, kept separate from the look — because they are the half of a shoot
that moves.

The session's wardrobe is a **starting point**, not a constant. It is written
into every take rather than stated once above them, and a take that carries its
own wardrobe wins:

```text
white linen midi dress, thin straps, square neckline, bare legs, flat tan sandals
```

That arrangement is the whole trick. Stated once, a wardrobe is prepended to the
very take that asks for the dress off, and a positive that both describes and
denies a dress keeps the dress. Written per take, each photograph states its own
truth and nothing in the prompt argues with itself — and the pieces the takes do
*not* touch stay word for word identical from frame to frame, which is what held
the wardrobe together in the first place.

So a shoot can walk: dressed, straps off one shoulder, dress at the waist, none
of it — one take each, in one session, with the hair, the makeup and the light
unchanged throughout.

## Tags and the library

A session carries free-text **tags** edited on the session view. They are the
half of a shoot that says *what the whole batch is about* — a place, a mood, a
garment the wardrobe does not name. Trimmed of surrounding whitespace,
compared case-insensitively (so `Balcony` and `balcony` is one tag), and an
empty string is discarded rather than stored. Editing them does not touch the
session's shots, look, wardrobe or settings. They survive a session being
cloned, so a clone of *Balcony shoot* is itself a *Balcony* session.

**Library** lists every session across every model, newest first, with a search
box and the tags currently in use as chips above the list. The search reads
the session's name, look and wardrobe as a case-insensitive substring; a tag
chip is a whole-tag match (so `night` lists the session tagged `night` and not
the one tagged `nightclub`). Both filters given, both must hold. Each row
shows the session's cover photograph — the highest-rated, non-rejected
finished shot — so the screen shows one frame per session without a request
per row. A query that matches nothing reads as *Nothing matched* rather than an
empty area.

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

Do not write the wardrobe here — every take has its own wardrobe box, under the
take itself, and clothes written in both boxes are two sentences about one
garment in one prompt. A take's wardrobe box left empty wears the session's,
which is the normal case: only the takes that *change* need anything in it.

## How the prompt is built

`trigger, model base prompt, session look, the take's wardrobe, shot`. If the
shot text contains `{trigger}` the trigger goes exactly there instead of being
prepended:

| Piece | Value |
|---|---|
| Model trigger | `4da woman` |
| Model base prompt | `photo, 35mm` |
| Session look | `hair down, on a beach` |
| Wardrobe (this take's, or the session's) | `white summer dress` |
| Shot | `full body, walking` |
| **Sent to ComfyUI** | `4da woman, photo, 35mm, hair down, on a beach, white summer dress, full body, walking` |

A shot's own negative overrides the model's default negative; leave it empty to
inherit.

### The length of the whole thing

Those five pieces are one prompt, and it has a ceiling. Past roughly 85 composed
words this sampler keeps the coarse facts and picks its own composition: the
position and framing a take asks for stop arriving. Measured on a six-rung ladder
of the same eight takes — they rendered what they described at 50 and at 63
composed words and stopped at 87, and the 24 words that crossed the line were the
look's sentence about the room, which describes nobody.

The look is the largest block sitting in front of the take, so it is the one to
drop. The **Look** checkbox on the new-session form switches it off for that
session: the text stays where it is and can be switched back on, it just is not
written into any prompt while it is off. A session that cares more about the pose
than about a constant place is better off without one.

## Writing the prompts

Everything on this page is a rule about text, and the app can now apply them
itself. Open **Setup** and press **Find an assistant**: it looks on the ports a
local Ollama, LM Studio or llama.cpp listens on, and lists the models that one
has. Save, and the ✨ buttons appear. With no endpoint set nothing changes and no
button shows.

It only ever writes into a box you can edit. It queues nothing, changes no shot
that already exists, and composes nothing: what it writes is the take's own
line, and the trigger, base prompt and look are still prepended by the app.

| Button | Where | What it writes |
|---|---|---|
| **🎲** | next to the brief | the brief itself — a shoot to run, from the look and the wardrobe |
| **✨ N takes** | above the takes | the look, the wardrobe *and* N takes that vary the pose, from one sentence |
| **🎬 The whole shoot** | above the takes | N takes **in order** and a wardrobe for each of them, from the same sentence |
| **👗 Wardrobe per take** | above the takes | one wardrobe for each take you already have, walking the clothes across the shoot |
| **✨** | on a take | that line again, by the rules of the kind |
| **👗** | on a take | what is worn in that take, every garment described properly |
| **↩** | on a rewritten take | what it said before |
| **✨ Describe it precisely** | under the look, and under the wardrobe | that box again, properly |
| **📷 Look and wardrobe from a photo…** | under the look | a photo you pick, read into both boxes |
| **✨ Pick** | in the angle picker | ticks chips, never a take |

The expression picker writes nothing at all — its four lines are already written,
see [expressions](#expressions).

**🎲 writes the brief.** With a photo read into the two boxes there is nothing
left for you to type: the look and the wardrobe *are* that photograph in words,
so a shoot written from them is one it could be a frame of — the room comes from
the look, and what there is to take off comes from the wardrobe. A different one
every time, because how far the shoot goes, how fast, and how it reads are
picked at random here and handed to the assistant as constraints. Asked the same
question about the same wardrobe it writes the same sentence back, however warm
the sampler is, so the dice cannot be left to it.

**How far the shoot goes is picked, not rolled.** The box beside 🎲 has four
settings, and the dice roll *inside* the one you choose:

| Setting | The shoot |
|---|---|
| **Clothed throughout** | begins and ends in the wardrobe it started in; what moves is the pose, the place and the expression |
| **Dressed to undressed** | the full wardrobe to bare skin, alone — how far and how fast are still rolled |
| **Dressed to penetration** | the whole arc, ending with a second person in frame and the act named |
| **Explicit throughout** | no build-up: already undressed and already with him in photograph one |

Rolling all of those together is what it used to do, and a shoot briefed for a
lingerie set and one briefed to end in penetration came out of the same button.
The ending and the pace both come from the setting because they are one question
asked twice: a shoot that never undresses has no undressing to pace, and one
that is explicit from the first frame does not spend its first half getting
there.

Two of the four needed the base instruction loosened before they worked. Asked
for a clothed session, the writer still wrote one that ended in the neckerchief
alone; asked for an explicit one it still opened *“still in the blouse and
skirt”* and spent the session getting undressed. The rule that says
*“`undresses a step at a time` is the whole middle of the brief”* is now
conditional on the shoot undressing at all, and the explicit setting has to say
*begins already undressed* in the brief's first clause — inferred rather than
written, the writer of the lines reads it as a shoot that starts dressed. Four
rolls in four say it now.

**The brief is the shoot, so the setting has to be chosen before the roll.** The
writer of the lines reads that sentence and nothing else, which means moving the
box after rolling changes nothing at all — a session set to *Explicit throughout*
whose brief had been rolled for the arc came back with five photographs dressed
before anything happened. Handing the setting to the writer as a second
paragraph was tried and is worse: two texts that disagree, and which one wins is
a coin toss — the same brief and setting run twice gave once undressed from line
one and once dressed for all six. So the panel says the brief is out of date
instead, and one thing is decided in code rather than in prose: on *Explicit
throughout* the writer is not handed the wardrobe as *what she wears in
photograph 1*, because a concrete outfit beats a sentence every time.

Roll until one reads right, edit it, or ignore it and type your own.

**The brief.** One sentence — *“a rooftop at sunset, streetwear, standing,
sitting and walking”* — becomes the look, the wardrobe and N takes. The box next
to the buttons is N: four for a set of variations, forty for a shoot that goes
somewhere. Only the boxes still empty are filled: one you typed, or read off a
photo, was decided. The takes are written knowing both, and told not to repeat
them: that is the rule from [the shots](#the-shots) applied by the writer instead
of by you.

**✨ N takes** writes variations — a different axis each, because four rewordings
of one pose are one take with four variations, which the `count` box already does
for free. Nothing is in order and nothing changes clothes.

**🎬 The whole shoot** is the other kind of session. The same brief —
*“starts dressed and undresses step by step, keeping the stockings on”* — becomes
**one line per photograph**: what she is wearing at that moment, what her body is
doing, and her expression, written together in one sentence. The wardrobe box on
those rows is left empty on purpose, because the line already states its own
clothes and the session's must not be appended behind it.

One line and not two, and this is the whole reason the button works. Written as
two streams — a wardrobe walking from dressed to undressed, and takes writing
poses — the two get the same brief and never speak. Measured, forty frames
briefed to end explicit: the wardrobe reached bare by frame thirty and take forty
was still *standing square to the mirror with her arms hanging loose*. Nothing
was broken; the halves simply had no way to agree. In one line they cannot
disagree, and the leak becomes impossible by construction — there is no second
text left to name a garment this one has already put down.

Each line copies word for word whatever the line before it did not change,
changes one thing, and never names a garment that has come off: the piece is
simply absent, and what stands in its place is the skin. Rows come in with
`count = 1` — a step of a progression is one photograph, and four variations of a
step is a decision to make afterwards on the steps worth it.

**Every line walks the whole body** — chest and torso, hips and legs, feet — even
where there is no garment, because an unstated part is not a bare part, it is a
part the reader dresses for you. This is the rule that fails silently and fails
late: on a real run the lines stopped saying `bare chest` at photograph
twenty-four, and from there the shoot came back in a black nightgown nobody had
written. Worse, it does not recover on its own — copying the previous line word
for word is exactly what makes a dropped body part permanent — so each round is
told to check the line it inherits and put back what it has lost.

**Every line opens with its framing** — *a full-length photograph, head to
feet*, *a three-quarter photograph from the knees up*, *a waist-up photograph*,
*a close-up* — and changes it from line to line, with at least one in four full
length. This was missing for a while and nothing complained: a fifty-frame shoot
came back as forty-five lines that never said where the camera stood, and the
gallery read as one photograph taken forty-five times, four of them full length
by luck. With the rule in, twelve lines from the same brief and the same look
came back with a framing in twelve of twelve and seven of the photographs whole
figure. The framing is not precise — a `close-up` still comes back waist-up —
but the set stops being one shot repeated.

**And every line OPENS with where the camera is** — *Taken from directly in front
of her*, *from behind her left shoulder*, *from her right side, in full profile*,
*from her left side, in full profile*, *from directly behind her* — with the
framing right after it. That is a different rule from the framing, and leaving it out is why a
shoot can be perfectly framed and still monotonous: thirty photographs written
with a framing in every line and no camera position in any of them came back
thirty frontal shots.

Where the clause sits in the line decides whether it survives. Written *after*
the framing, in a seventy-frame run, fifty-three lines asked for something other
than a front view and about ten photographs delivered one — the clause was buried
behind eighty words of clothes, and the reader frames what it meets first. Moved
to the front of the line, eighteen of twenty-four asked and ten or eleven
delivered. `floor level` and `over her shoulder` come back frontal wherever they
sit, so neither is offered — and *from above her, looking down* was on the list
until it was measured the same way: two seeds of one line against each of the
five clauses, then four more inside a real shoot. Front, shoulder and profile
obeyed every time; *directly behind her* in one of two; *above her* in none of
six, always as the ordinary straight-on view. It is off the list, and *from her
left side* took its place.

**In a two-person frame, on a bed, there is no full-length photograph.** Thirteen
tries without one — five lines of a real shoot and eight of a controlled pair —
and the room's depth does not buy it either, which is the lever that buys it
everywhere else: the floor duly appears in the picture and the crop does not
move. The two bodies fill the frame. Those lines ask for waist-up or
three-quarter instead, and the words go nowhere else.

The framing says how much of her is in frame, never how much of her to write.
Every line still walks the whole body, close-ups included: the clothes are what
the next photograph copies word for word, and a line that drops them to match a
crop drops them for every line after it too.

**A line runs long, and that is the price of the wardrobe.** Each photograph is
its own prompt, so each one carries the whole outfit — which puts a line at
ninety to a hundred and twenty words however firmly the instruction asks for
sixty. Shortening it was tried the obvious way, describing each garment fully
once and carrying it afterwards as *the white sailor top*: the writer obeyed, the
lines lost eight words on the median, and the white fishnet stockings came back
**black** for the last four frames of twelve, where the long version held them
white in all twelve. A short name is a smaller handle for the sampler to hold,
and what it does not hold it re-rolls.

So nothing asks the writer to cut a line any more. Asked, it does shorten — and
what it deletes is the fact: one rewrite dropped `pleated mini skirt`, another
dropped `erect penis pressed`, against about three words of genuine filler per
line. Every repair now has to keep the garment attributes it was given and keep
the act, and under that rule not one shortening survived, which is the answer:
in a prompt the words *are* the garment.

What is left is a flag, and it is measured against the shoot rather than against
a number — a line half as long again as its neighbours has started listing
something twice, whatever the outfit. An absolute number could not do this job:
sixty flagged nine rows in twelve, a hundred and ten flagged twelve in twelve,
and a flag every row wears is not a flag. Rows that are simply long are left
alone, and a run of twelve at a hundred and sixty words a line came back with the
outfit identical in all twelve.

**The end of a long shoot is written more than once.** Forty-five photographs
briefed to end in penetration: it reached its ending — the act from photograph
thirty-two, all of the last three explicit — and then wrote that ending five
times. Photographs forty-one to forty-five shared 1.00, 0.92, 0.98 and 0.95 of
their vocabulary with the line before them, the same camera clause and the same
pose, differing by a *bare* here and a framing word there. Five rows, one
photograph, five renders, and the duplicate check saw nothing because it compared
byte for byte. It now compares vocabularies, and a line sharing 85 per cent of
its words with one the shoot already has is dropped and asked for again — the
rest of that shoot topped out at 0.79, so the gap is wide. Re-run on the same
brief: no pair above 0.84, and forty-four of the forty-five asked for, which the
panel says out loud rather than quietly shipping a shoot with its ending on
repeat.

**And when she is introduced, it spreads.** `of a woman in a white jersey` where
`of her` belongs is two words, and the trigger at the front of the prompt has
already said who she is — so the photograph gets a stranger standing where the
character should be. The writer barely does it: seventy-two lines across nine
rounds of one shoot's opening chunk, not one. Neither does the repair, in
twenty-four. But every round is handed the previous photograph and told to carry
it over word for word, which is what holds a wardrobe together over forty-five
frames and is also how two words become permanent: seeded with one such line, the
next chunk copied it in **sixteen lines of seventeen**, and the same line with
`of her` in it in none of twenty-four. On the real run it appeared at photograph
one and thirteen rows carried it, none of which the repair could clear.

So this one the code rewrites rather than asking the model to — the only place it
does. What it deletes is not a fact about the photograph: not a garment, not a
body part, not the act, which is what every other shortening was refused for. A
line that *opens* on her is left alone and flagged instead, because `Her stands
square to the mirror` is not a repair. `a naked man` is untouched: the second body
is introduced on purpose. Re-run at forty-five photographs: thirteen rows down to
none.

**The line that takes a piece off must not name it**, and that one is checked
now. `GARMENT_CARRY` has said so for a while and nothing enforced it: the check
beside it only ever asked whether a garment had come *back*, so *“the ruched
drawstrings of the olive green crop top gone from her body”* went out unflagged,
and a prompt that names a crop top has a crop top in it. Seven lines out of a
hundred and ten across three real sessions said something like it. A piece that
is merely *moved* is different and still named — pushed up, pulled aside,
unbuttoned, off one shoulder — so only words that mean it is off count: gone,
removed, discarded, set aside, lying on the floor.

**And it must not introduce her.** Under pressure — a setting pulling one way and
a brief the other — the writer opened four lines in six with *“a young woman”*
and *“the same young woman”*. The trigger at the front of the prompt already
says who she is. A second person is still named as a body: *a naked man*.

That asymmetry is the whole rule, and it took two attempts to make it stick.
Written out in the brief, with its own measurement attached, it was ignored:
nineteen lines of twenty opened *“a naked man and a naked woman”*, and the one
the repair could not fix came back as a photograph of two women. It now rides on
the system message with the rest of the explicit register, and the brief does not
restate it — 0 of 20 on the next run, same look, same wardrobe, same brief. Where
a standing rule is read turns out to matter more than how firmly it is worded.

**On the explicit setting the rule stands, and it travels in the system
message.** Written as *when the shoot is explicit, the line is explicit*, it was
half-answered: the act came back as a pose — *arched into him*, *sitting astride
him* — in twelve lines of twelve. Three things fixed it, and none of them is
stronger wording. The rule stands rather than triggers, so the writer never
judges whether this photograph qualifies. It forbids the manoeuvre — softening,
implying, answering with a pose — rather than listing euphemisms, because
banning *moving against her* only buys you *arched into him*. And it names the
vocabulary instead of gesturing at it. The fourth is placement: a standing rule
about *how* to write rides on the system message, where it is read first and
once, not in paragraph thirty of a two-thousand-word brief. Same look, same
wardrobe, same setting: lines naming the act went from none of twelve to twelve
of twelve, and the renders from two to about six.

**A brief that ends in an act needs the act named.** Left to itself the writer
hedges the last stretch — *moving against her*, *straddling his lap*, a partner
still half-dressed — and a hedge is painted as what it says: two people standing
near each other. Measured, thirty frames briefed to end in one: none of them
showed it. With the line required to say plainly what is happening, who is naked
and that there are two people in frame, three of the last four frames came back
as the photograph the brief asked for, and the one that missed was the take shot
from floor level, which cropped the second person out. The camera position and
the naming carry equal weight here — a frame that names everything and says
nothing about where the camera stands is a frame where the act is out of view.

**No stage may cover more than a quarter of the shoot.** The shoot is planned in
stages before a line of it is written, and asked for seventy photographs the
planner wrote five stages and gave *forty of them to the first one*: forty frames
of the same photograph, then top, skirt and stockings all gone between frame
forty and forty-one, with no frame in between wearing one of them. So the plan is
checked, and a plan with a stage that big is asked for again once, with the
offending range quoted back. Re-planned that way, a twenty-four frame shoot came
back in seven stages, the longest four frames, and the undressing walked: blouse
open, blouse off, skirt alone, stockings down, and only then the ending.

**Undressing is the first half, not the subject.** Read for where the brief
*ends*, not for the first state that satisfies it: a run that treated `undressed`
as the destination got the clothes off by photograph seven and spent the
remaining thirty-three on variations of standing in a mirror. Everything the
brief asks for after the last garment is the part someone actually wanted, and
running out of wardrobe is the halfway mark, not a reason to stall.

**👗 Wardrobe per take** does only the wardrobe half, on takes that already
exist — written by hand, kept from an earlier shoot, added since. Rows marked
`ref` or `verbatim` are skipped: they carry no wardrobe of their own.

### What a forty-take shoot cost to get right

Everything below was measured on one session — forty takes, a six-piece
wardrobe — and each rule is there because the run before it came back wrong.

**Asked for forty lines at once, an assistant does not answer forty.** It
answered thirty-two takes, and they were stubs: `Close-up, hands clasped low.`
against an instruction asking for a fifteen-to-thirty word caption. A long ask is
answered *shorter*, and the middle of the shoot is what falls out. So both
buttons ask in **rounds of eight** and stitch the answers, each call told where in
the shoot it is and what the line before it said. Forty takes is ten calls and a
few minutes; the button counts up while it works.

**A wardrobe has as many states as it has pieces, not as many as the shoot has
photographs.** Asked for forty wardrobes from six garments, it was bare by the
fifteenth, repeated itself while it had nothing left to remove — and the repeats
were dropped as duplicates, so the count never reached forty — and then **put her
in a schoolgirl uniform** so there was something to take off again. The session
came back as two shoots. Now at most twelve states are asked for, each held for a
stretch of takes, and the writer is told to stop early rather than invent: only
the pieces in the wardrobe box exist in that shoot.

**A take written without knowing the clothes puts them back on.** With the two
halves written in parallel, row twelve came back `both hands sliding the jersey
hem upward` — twenty rows after the wardrobe had put the jersey down, and a
prompt that names a jersey has a jersey in it. So the wardrobe is written first
and the takes second, each round told what she is wearing across the stretch it
is writing.

**And it still slips, nought to four rows in twenty-five.** At the seam where a
garment goes, the take reaches for it as the thing that changed: `both hands
sliding the jersey hem upward`, on a row whose wardrobe has no jersey. Four
rewrites of the instruction did not close it, and it is not the brief's doing
either — the same wardrobe gave 0 in one run and 4 in the next, from briefs
written the same way. It is variance, it always lands at the transition, and
guessing at it costs a four-minute run each time.

So it is caught instead of argued with: those rows are **outlined in red**. The
check is a word in the session's wardrobe that this take's own wardrobe no longer
has, which is precisely a garment that has come off — a colour or a bare shoulder
appears in both and does not flag. Press ✨ on the row, or say what the body does
— `arms lifted overhead, elbows high` — and let the wardrobe say the rest.

In an existing session the brief writes takes only, and the **add shots** panel
has the session's wardrobe in an editable box: twenty takes in, a shoot is rarely
still wearing what it started in, and what you leave there is what the next takes
start from. The look belongs to the session and does not change halfway — that is
what makes it a session.

### Detail is a zoom lens

The one rule that costs a whole session to learn, so it is written here instead:
**the frame goes where the words go, and reaches as far down the body as they
do.** Not as far as the take asks. As far as the words reach.

A session that spent nineteen of its eighty-six look words on a bra, its straps,
a brief and a necklace — with nothing above the collarbone, nothing below the
thigh and no room to stand in — came back as a catalogue photograph of the
underwear, cropped above the mouth. `full-body` was in the take. It changed
nothing: ten words of take against eighty of look, and the eighty say where to
point the camera.

Fixing it is not a stronger word for *full-length*. It is moving the words:

- **Below the hip has to exist.** When a garment stops at the thigh, the legs
  below it are still in the photograph — bare skin, knees, calves, ankles, the
  feet and what they stand on. A look whose words stop at the thigh is a
  photograph that stops at the thigh.
- **No section may outweigh the rest.** Underwear is the trap, because *Upper
  body* and *Lower body* then describe the same handspan twice. Spend the same
  care on the hair, the feet and the room.
- **The room needs depth**, not a backdrop — and this one turned out to be the
  whole game. See below.
- **The take names its framing outright and first** — `Full-length, head to
  feet`, `Three-quarter, from the knees up`, `Waist-up`, `Close-up` — with at
  least one full-length in every batch. Blunt on purpose, and never clever:
  `thigh-to-hair framing` is a crop at the thigh whatever it was meant to say.

The assistant follows all four now. Walking that list moved one real session from
*chin to mid-thigh, head out of frame* to *head to feet*, on the same seed, the
same wardrobe and the same 832×1216 canvas.

### The room is the framing control

Framing resisted every lever aimed at it, and gave way to one aimed somewhere
else entirely. Four seeds of the same take, changing one thing at a time:

| What was changed | Full-length frames |
|---|---|
| The take's wording — three phrasings of *head to feet* in one line | **0 of 4** |
| The canvas — 832×1216 → 832×1472, a 9:16 frame | **0 of 4** |
| The character LoRA — strength 1.0 → 0.0 | **0 of 4** |
| **The room — a wall behind her → a floor running away from the camera** | **4 of 4** |

A taller canvas does not lower the crop; it adds ceiling. The figure stays the
same size in frame and the extra pixels go to empty wall. Turning the character
off changes nothing either, which rules out a LoRA trained on portraits.

What works is geometry. `A tall mirror against a pale wall` is a backdrop:
nothing stands between the camera and her, so nothing obliges the camera to
stand anywhere in particular, and it frames at the distance the model likes —
a mid-shot. `The bare wooden floor running away from the camera past the foot of
the bed to a mirror at the opposite wall` **cannot be painted from close up**.
To show that floor the camera has to be across the room, and from there the whole
figure is in the picture without anyone asking for it.

So the framing is not requested. It is made unavoidable, and it is done in the
look — the one part of the prompt that never changes — rather than in the take.
`ROOM_DEPTH` in `kinds.js` is that rule, and it is in every instruction that
writes a place.

The take still names its framing, and that is not a contradiction of the table
above. What the table rules out is asking a *flat* room for a full-length frame.
Inside a room with depth the words do move the camera — same look, same brief,
twelve takes: with no framing written the shoot came back all mid-shots, with one
written per line seven of twelve were the whole figure. The room decides what is
possible; the line picks from what the room allows.

A session already shot that way is not lost, and it does not need starting over.
The look cannot be edited after creation — that is what makes it a session — but
**⟳ More like this** copies the whole composed prompt into the panel as a
`verbatim` take, where it *can* be edited: add `bare feet on wooden floorboards`
and `full-length, head to feet` to it and queue that.

**A take undresses through its wardrobe box, never through its own line.** The
take says the pose and the framing; the wardrobe box under it says what is on the
body. Written in the take instead, `straps pushed off the shoulder` lands beside
the wardrobe the app also prepends, and two sentences about one camisole in one
prompt is a photograph wearing neither. Write the state in the wardrobe box —
`ivory silk camisole at the waist, bare shoulders, bare chest` — and the take
stays a pose. That is what 👗 does for a whole shoot at once.

**The kind decides what a take is**, so ✨ writes a description for a photoshoot
take and an imperative instruction for a `ref` one, with no trigger and no look
in it. That is the same split as [reference takes](#reference-takes), and the
reason a reference take is never given the look as context: nothing prepends it,
so nothing must restate it.

**Look from a photo** copies the clothes, the hair, the makeup, the place and
the light — the garment at the level of detail this page asks for, `white linen
midi dress, thin straps, square neckline` rather than `white dress`.

It is read **head to toe, one line per part of the body**: hair and makeup, upper
body, lower body, feet, accessories, then the place and the light. The sections
are a checklist before they are an ordering — what varies between frames is
overwhelmingly what nobody wrote down, and a single "describe the clothes" is how
a look ends up with no trousers named in it at all. Each line has to say what
identifies the garment: colour and pattern, fabric, cut and fit, then the
detail — neckline, sleeves, hem, buttons, closure — and how it is worn.

And it has to **count**. How many straps, buckles, rings, spikes: a number is the
attribute that drifts hardest when nobody writes one, and the first thing the eye
checks between two frames. `multiple silver spikes` is re-rolled into a different
boot in every photo; `three silver conical spikes on the front of each` is the
same boot every time. A count that is off by one still holds the session
together, which is why the assistant is told to commit to a number rather than
hedge.

How much of that you actually get is the model's doing, not the app's. A large
vision model fills all six with the fabric and the hardware named; a small local
one fills them too, but leaks what it was told to leave out — a pose, a feature
of the person, a `no shirt` that puts a shirt in the photo — and starts dropping
a section as the request gets longer. If the wardrobe is the point of the
session, this is the button worth pointing at the best model you have. It is told
never to describe the person: no face, no body, no age, no expression. The
identity is the LoRA's job, and another person's features sitting in the look
would fight it in every frame of the session. Pose and framing are left out too;
those are the takes. The photo is read and thrown away — it is not uploaded to
ComfyUI and does not become a reference take.

**Camera angles get chips, not prose.** The angle LoRA reads a closed
vocabulary, so *“from behind, a bit lower”* ticks `back view` and
`low-angle shot` and drops everything else. A word the LoRA would ignore looks
exactly like a word it read, which is why it is dropped before it reaches a take
rather than after.

### The canvas

**Canvas** on the model form and on the new-session panel is a menu of five
shapes, and the width and height boxes stay beside it for anything off the list.

| Canvas | Pixels | What it delivers |
|---|---|---|
| Portrait (default) | 832x1216 | Pinterest 1000x1500; Instagram 1080x1350 with a sliver cropped |
| Portrait 4:5 | 896x1120 | Instagram portrait 1080x1350, exactly |
| Square 1:1 | 1024x1024 | Instagram 1080x1080, Facebook 1200x1200 |
| Tall 9:16 | 720x1280 | Stories, Reels, TikTok 1080x1920, exactly |
| Wide 16:9 | 1280x720 | X 1600x900, Facebook post 1200x630 cropped |

The list stops at 16:9 on purpose, and a platform's own pixel size is not on it.
Those are two halves of one rule: **a delivery size is a crop of a finished
photograph, not a canvas.** Cropping costs nothing and can be redone; shooting
the delivery size means re-running the session to change a crop, which for a
forty-frame shoot at twenty-five seconds a frame is nineteen minutes per ratio.
And the wide formats do not survive being shot — a Facebook cover at 820x312 is
2.6:1, and asking the sampler for a frame that wide with a whole body in it
paints two of her.

832x1216 stays the default and is a preset of its own rather than being rounded
to a true 2:3. Every session in this document was shot on it.

**The menu changes the shape of the photograph, not what is in it.** That is the
measurement above: a taller canvas adds ceiling and does not lower the crop, 0
of 4 frames. The limit is that this was measured on vertical canvases only.
Square and wide follow the same reasoning and have not been measured, so a
full-length take on a square canvas may put the figure further away than the
words asked for. The default is untouched, so nothing changes until you pick.

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
consistent is describing it precisely in the wardrobe (`white linen midi dress,
thin straps, square neckline` rather than `white dress`), leaving it word for
word the same in the takes that do not change it, and keeping the shot line short
so it does not compete for attention. For a genuinely identical garment,
work from a photo instead of from words — see *Reference takes* below.

## Reference takes

Text to image has a limit you hit fast: **a prompt cannot deny what it also
says.** A take that says `without the jacket` is sent as
`…leather jacket…, without the jacket`. The positive both describes the jacket
and denies it, and the jacket usually wins.

Two answers, and they are for different jobs. The
[per-take wardrobe](#the-wardrobe) is the answer when you are shooting new
photographs: nothing denies anything, because the take that has no jacket never
names one. A reference take is the answer when the photograph already exists and
has to *stay* that photograph — same face, same pose, same room, one thing
changed. It works by changing what the prompt *is*: instead of describing a
photo, it gives an instruction on one you already have.

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

### Expressions

An expression is the one thing a take cannot ask for. Measured across nine
Krea-2-family checkpoints at a single seed: written into the prompt, every one of
them trades a feature for another — the mouth opens and the wink goes, the lips
purse and the joy goes — and no checkpoint on the list escapes it. The same words
as an **edit** on a finished photograph get all of it in one pass, because the
edit moves the mouth and leaves the eyes, the hands, the crop and the wardrobe
where they already are.

**Anchor on a photo where her face fills the frame.** This is the rule that
decides whether any of it works, and it is the opposite of the camera-angle rule
one section down — angles want the widest frame you have, expressions want the
tightest. An expression is a few hundred pixels of mouth, and on a full-length
photograph there is nothing there for the edit to move: measured, ten presets
against a full-length anchor came back *identical to it*, every one except the
wink, which survives because a closed eyelid is the most contrasted change a face
has. The same ten against a head-and-shoulders anchor moved the mouth on the
first try. Nothing fails and nothing is reported — the photographs simply come
back the same, which is why the rule is printed above the picker.

So an edit session shows a row of chips above the takes, and clicking one adds an
ordinary `ref` take with the instruction already in it:

| Chip | What it does |
|---|---|
| **soft smile** | closed lips, corners up |
| **warm smile** | corners up, lips just parted |
| **happy** | mouth open, upper teeth showing |
| **laughing** | mouth wide open — and both eyes shut, so a wink does not survive it |

Those four were run on the editing graph against a real photo. The rest are
written to the same shape — one movement, in plain anatomy — and marked **·** on
the chip, because nobody has looked at what they come back as yet:

| Chip | What it asks for |
|---|---|
| **kiss ·** | lips pushed forward into a kiss towards the camera |
| **pout ·** | lower lip forward, lips closed |
| **biting her lip ·** | lower lip caught between the front teeth |
| **tongue out ·** | mouth open, tongue out towards the camera |
| **wink ·** | left eye closed, right eye open, mouth closed |
| **sultry ·** | eyelids lowered, lips slightly parted |

**Every preset moves the face and nothing else**, and *kiss* is why the rule is
written down. It said *blowing a kiss* for one commit, which is an idiom about a
hand: the keep-clause holds the two hands the photograph already has, the edit
adds the one the gesture needs, and the frame comes back with three. The gesture
does not arrive even when nothing is holding the hands still — asked fourteen
ways in text-to-image (*cupped palm-up below her chin*, *open beside her cheek*,
*having just thrown a kiss*) the hand landed at mid-chest every time, blowing
across a palm nowhere near her lips. What renders is the mouth, so the mouth is
what the chip asks for. A gesture belongs in a take you write, where the pose is
yours to move.

The last two carry a **shorter keep-clause**, without the eyes in it: *close her
left eye in a wink, and keep her eyes exactly as they are* is one instruction
arguing with itself, and a prompt that both asks for a thing and denies it comes
back denying it — the oldest finding in this project, and the reason a reference
take carries no look.

Two things worth knowing before you reach for a dial:

- **The wording is the intensity, not `Reference strength`.** The same instruction
  at 4, 3 and 2 came back indistinguishable. The four rows above are the
  gradation; there is no slider between them, which is why they are four chips
  and not one with a number beside it.
- **Every preset ends with a keep-clause** — *keep her eyes, her hands, her pose
  and her clothes exactly as they are* — because without it the edit moves what
  the photograph already got right. It is inert when it names something the photo
  does not have, so the same tail is safe on every anchor.

The same instruction lands differently depending on the face it starts from: on
pursed lips *lips just parted* barely opens them, on a neutral closed mouth it
opens them a lot. Add the two you are choosing between, run both, and read them
in the before/after wipe against the anchor rather than expecting one of them to
be right.

The picker is in **+ Shots** on a session whose kind is *Photo edit*, and not on
the new-session panel: an expression edits a photograph, and a session being
created has none yet. 📎 the frame you want first.

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

**Two people is where this kind earns its keep, and where it stops.** Measured
against the same scene written as a plain text take: the two-anchor graph is the
only one that gives back *both* faces — each subject is recognisably the photo it
came from — but it puts them side by side however the take describes them
interacting, and it ignores the camera position in the take. Written as a text
take instead, the interaction and the camera both land and the faces are whatever
the model invents. So: the graph for who they are, a text take for what they are
doing, and no way yet to have both at once.

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
  than silently reading as *no choice made*. If some graph is *written for* the
  model you pick — its own loader names that checkpoint — the panel says so and
  offers it in one click. Offered, never applied: swapping the graph because a
  dropdown moved is the silent change the rest of this panel warns about.
- **Denoise** and **LoRA strength** — the two dials an identity pass is made of.
  They used to be settable only while creating the session, which is before you
  have the photo whose face drifted.

Photos already shot keep what they were shot with; these apply to what runs
next. The reason this exists: you learn a graph is in the wrong slot when Run is
refused, and by then the session is an imported photo and seventy takes.
Delete-and-start-over should not be the cure for a dropdown.

**What the session actually drives.** A slot a workflow does not map is never
patched — the graph's own widget value stands — so the panel lists those slots by
name, and the line under the session title strikes the numbers that do not reach
this graph. That is not a fault to fix: a graph tuned for one checkpoint carries
its own steps, cfg and sampler, and leaving them unmapped is how you keep them.
The strike is only there so a session shooting at 12 steps and printing 30 stops
being a mystery. Map the slot in **Workflows** if you do want the session to
drive it.

While the session is **running** they are locked, and the panel says why: the
runner re-reads the session before every take, so a graph swapped mid-queue
would quietly send the rest of the shoot somewhere else.

One related rule, so a refusal points at the right thing: the base model and
LoRA checks apply to the graph that will actually run. A session whose pending
takes are **all** reference edits never loads the first workflow, so a base
model it does not map is not being ignored — nothing is going to ignore it — and
the run is allowed.

## Cloning a session onto another model

**⧉ Clone** copies the session and changes two things: the **base model** and the
**steps**. Everything else comes across untouched — the look, the wardrobe, the
takes in their order, the composed prompts word for word, and the seed of every
shot. What differs between the two galleries is then the model, which is the
only way to judge one: a single frame is luck, twenty frames are the model.

The panel opens on a list holding one row: what this session already shoots with.
Press **Create** and that is the plain copy. Pick another model from *Add a base
model* and it becomes a second row — **as many as you want to try, each one its
own copy, from one press**. They are named after their model (`shoot —
moodyKrea2Mix_v70`), because three sessions called *(copy)* are three sessions
you have to open to tell apart.

Each row carries **its own steps**, prefilled from this session. Mixing a
distilled checkpoint with a full one is the normal case, and 8 steps on the full
one wastes the whole copy — twenty photos before it is visible. Anything else a
copy needs is a **⚙ Settings** away on it: it is an ordinary session, and the
workflows, denoise and LoRA strength are editable there as on any other.

**And its own workflow, picked for you.** A checkpoint wants its own sampler and
scheduler, and those live inside the graph rather than in a slot the session can
drive — so the honest way to shoot a second model is the graph written for it.
Every workflow already names the model it loads, so choosing the base model
chooses that graph, and the row says *written for this model*. There is nothing
to keep in step: it is read from the graph, not stored beside it. No graph names
that checkpoint, and the row shoots on this session's, as it always did — and
the dropdown is still yours to override, because putting one model through
another's graph on purpose is a comparison too. A row whose graph does not map
**steps** greys the box: that graph's own value is what runs, and typing a
number there would be describing a copy that is not the one being shot.

The copies are drafts. The queue is serial — one session at a time, one GPU — so
you run them one after another, and each one that finishes joins the comparison
below.

- Every copy lands as a **draft** with every take **pending**. Nothing is queued;
  Run is yours to press, and two sessions cannot run at once anyway.
- A photo **imported** into the source is copied as a file, finished, because
  nothing generated it and there is no prompt to repaint. Its file is a real
  copy: deleting either session leaves the other's gallery whole.
- A **reference photo** follows its own copy. If the source painted its anchor,
  the clone repaints it too — it sits earlier in the queue than the takes that
  edit it, the same order the source shot them in, so it has a photo by the time
  they run. If the anchor was imported, the copy edits the copied file.
- Ratings and rejects do not come across: they were judgements of photos that no
  longer exist.

The base model still has to be mapped by the workflow — a clone onto a
checkpoint the graph does not read is refused at Run like any other session, and
a family mismatch (a Krea LoRA on a Z-Image model) fails there too.

### Comparing the copies

A clone records the session it came from (`cloned_from`, in its settings), and
the **Compare with…** box above the gallery offers *only* those copies. That
restriction is the feature, not caution: two photos are comparable when they are
the same take on the same noise, and nothing but a clone gives that. A clone of a
clone joins the same family rather than starting a chain, so all the copies of
one shoot see each other however many there are.

Pick one and every photo that has a twin opens as a **before/after wipe** — the
other session on the left of the slider, this one on the right, both on the same
frame — with the two base models named underneath. Two checkpoints rarely differ
by more than a face and a fabric, and that is invisible when the eye has to
travel between two windows.

The pairing is the **take's id** — the shot in the session that was cloned,
carried by every copy of that take (`origin_shot_id`). Deliberately *not* the
seed: **↺ Reshoot** rolls a new seed by design, and a pair keyed on the seed
would come apart at the exact moment you reshot the photo you wanted to compare.
The id survives a reshoot, a rename, a rating and a change of steps, on either
side.

When the two sides no longer share a seed the wipe says so underneath
(`seed A vs B — one side was reshot`). The comparison is still worth having —
same take, same prompt — but it is no longer the model alone, and a wipe that
kept quiet would read as if it were. Reshooting *both* sides is how you get back
to comparing only the model.

A photo the other session has not shot yet has no twin: its caption says so
(`—` instead of `⇄`) and it opens as a plain photo. Copies made before take ids
existed fall back to the old pairing — the take's row and its seed — so the
comparisons that already worked keep working.

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
- **↺** reshoots a photo you do not want at all: it is deleted and that same
  take goes back in the queue with a fresh seed, in the row it already had — so
  the shoot keeps one card per take instead of collecting the frames that came
  back wrong. It asks first, because the photo is gone. The seed is dropped on
  purpose: the same noise would hand back the same picture, which is what ⚖ is
  for. Refused on the session's reference photo — the takes that edit it would
  have nothing to edit; unpin it or pick another reference first.
- **Reshoot below N★** does ↺ for every finished shot under the threshold in
  one click — every photo deleted, every take back in the queue on a new seed,
  a `done` session reopened to `draft`. The count is on the button; the
  threshold follows the filter (4★ with *Picks only*, 1★ otherwise). A running
  shot and the session's reference are left alone, the response counts what was
  re-queued and what was skipped, and the screen can say what happened. It asks
  first, naming the count, because the photos are gone.
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

Both append to the current session and keep its look: a shoot whose hair, place
and light changed halfway is two sessions, so the look is read from the session
and not from whatever the panel sends. The wardrobe is read from the session too,
as the default the new takes start from — and that one *is* editable in the
panel, because it is the half of a shoot that was always meant to move. A finished session goes back to *draft* and can
be run again. Reshooting a reference take stays a reference take — coming back
as a fresh text to image would quietly be a different picture.

## Exporting

**Download picks** next to the rating filter packages the session's finished
shots into a single ZIP named `session_<id>.zip`. It takes the shots rated at or
above the threshold that are not rejected — that rule, and not whatever the
gallery filter is showing. Inside, the files are numbered in the order they were
shot, so the shoot's progression survives outside the app.

**Contact sheet** beside it compiles those same selected shots into a single
image grid in shooting order, with every cell labelled by its file name so any
frame can be found again among the session files without opening the app. A name
too long for its cell is trimmed from the front — the counter at the end is the
part that tells two variations of one take apart. Like the export, it reads the
session's photos and writes nothing into the session folder.

## Where the files are

`<data folder>/sessions/<session id>/<shot id>_<shot label>.png`. They are moved out
of ComfyUI's output, so nothing is duplicated and ComfyUI's folder stays clean.
Deleting a session from the app deletes its folder too. If a file is locked and
the folder survives, the app says so instead of leaving it quietly behind —
session numbers are reused, and a leftover folder would show its photos inside a
later session.
