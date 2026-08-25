/** What a session is for.
 *
 *  A session's kind IS the kind of reference workflow it needs, so one list
 *  serves both: the tag on a workflow row and the choice on the new-session
 *  panel. The kind changes no generation rule — it picks the graph, sets the
 *  defaults and prints the one rule that decides whether that kind of shoot
 *  works at all. Those rules are in docs/sessions.md; a rule you only find in
 *  the docs is a rule you find after the session came back wrong.
 */

// The tag on a workflow row, as the Workflows screen shows it.
export const WORKFLOW_KINDS = {
  t2i: 'Text to image',
  edit: 'Photo edit (instruction)',
  angles: 'Camera angles',
  scene: 'Scene + subject (2 references)',
}

export const KINDS = {
  shoot: {
    label: 'Photoshoot',
    blurb: 'New photos painted from the look. Each take carries the wardrobe, so a shoot '
         + 'can change it. No reference photo involved.',
    rule: '',
    refKind: null,
    refDefault: false,
    // Captions, not keywords: the text encoder of a current base model was
    // trained on sentences, and a sentence is what composes a frame. The
    // framing opens each one because it is the part that has to outweigh a look
    // several times its length.
    examples: [
      'A full-length photograph of {trigger}, head to feet, walking towards the camera mid-stride and looking away to one side',
      'A close-up photograph of {trigger}, her chin slightly down and her eyes to the camera, one hand resting against her jaw',
      'A three-quarter photograph of {trigger} from the knees up, standing with her weight on one leg and her hands in her pockets',
      'A waist-up photograph of {trigger} sitting by the window, leaning on one arm and turned half away from the lens',
    ],
    footer: 'Pose, angle, place — the trigger, the base prompt, the look and the take\'s '
          + 'wardrobe are prepended automatically. A wardrobe box left empty follows the '
          + 'session. Leave the seed empty unless you are comparing a change.',
    enhance: {
      line: 'Write one take of a photo session: what the body is doing, and how the '
          + 'photograph is framed.\n'
          + 'Write it as one sentence, the way a caption describes a photograph — not as a '
          + 'string of keywords. Fifteen to thirty words. `A waist-up photograph of {trigger} '
          + 'standing facing the camera, one hand raised near her face` composes a '
          + 'photograph, where `standing, hand raised` is a list of things that happen to be '
          + 'true.\n'
          + 'Open it the same way every time: `A <framing> photograph of {trigger}, …`, with '
          + 'the framing in the plainest words there are — `full-length, head to feet`, '
          + '`three-quarter, from the knees up`, `waist-up`, `close-up`. Write `{trigger}` '
          + 'exactly like that, in braces; it is replaced with the character\'s own word.\n'
          + 'This sentence opens the whole prompt, and that is why the framing goes first in '
          + 'it: everything after it describes the clothes and the room, several times its '
          + 'length, and whichever of them the reader meets first is the one that decides '
          + 'where the frame falls. Never imply the framing and never be clever about it — '
          + '`thigh-to-hair framing` is a crop at the thigh whatever it was meant to say.\n'
          + 'Write nothing else. No clothes, no shoes, no hair, no place, no light, no '
          + 'weather: every one of those is already in the prompt above, and a take that '
          + 'says them again either repeats them or contradicts them — “barefoot” under a '
          + 'look with boots in it is how a shoot comes back wearing something else.\n'
          + 'Above all, never write a garment coming off — no strap pushed aside, no '
          + 'fabric slipping, pooled, gathered, unbuttoned, peeled or absent. Not because '
          + 'the shoot cannot undress: it can, and each take has its own wardrobe box for '
          + 'exactly that. But wardrobe written here lands *next to* the wardrobe written '
          + 'there, and two sentences about the same garment in one prompt is a photograph '
          + 'wearing neither. The pose is yours; the clothes are that box\'s.\n'
          + 'Never introduce the subject — no “a woman”, no “the model”, no “a young '
          + 'woman in…”: the trigger word already says who this is, and a description of '
          + 'her competes with it. A plain pronoun is what a caption uses and is fine: '
          + '“one hand raised near her face”. No filler either: every word that is not the '
          + 'pose, the framing or the angle is a word taken from the ones that are.',
      batch: 'Every line varies a different axis: framing, pose, camera height, where the '
           + 'subject stands. Two lines that reword the same pose are one take with more '
           + 'variations, not two takes, so make them genuinely different.\n'
           + 'Spread the framings across the set, and make at least one of them full-length, '
           + 'head to feet. Left alone, every take comes back cropped around whatever the '
           + 'look spends its words on, and a set that is all mid-shots is a set with no '
           + 'photo of the outfit in it.',
      // The same batch, ordered. A shoot that walks somewhere — a wardrobe coming
      // off, an evening getting later — is not a set of variations, and takes
      // written as variations put its most open pose in frame three. The order
      // has to be written, because the wardrobe walking alongside it is.
      arc: 'These lines are one shoot, in order. The first is where it starts, the last '
         + 'is where it ends up, and each one between them is a single step along that '
         + 'path. Follow the shoot described above, at its pace, and end on the last '
         + 'line — reaching the end early and then repeating yourself is the failure to '
         + 'avoid.\n'
         + 'What the body is doing moves with the shoot and at the same pace, so that a '
         + 'line reads as a photograph taken after the one before it and before the one '
         + 'after it. Two lines that could swap places without anyone noticing are one '
         + 'line, written twice.\n'
         + 'Framing and camera height still vary line to line — an arc is not an excuse '
         + 'for twenty of the same shot — and at least one line is full-length, head to '
         + 'feet.\n'
         + 'Still never a garment and never a state of undress, not even as the thing '
         + 'that changed: the wardrobe of each photograph is written separately and '
         + 'lands in the very same prompt as this. The body and the camera are yours, '
         + 'and between them they say the whole shoot.\n'
         + 'The hands are the trap, so they have one rule: they are never on a garment. '
         + 'Not lifting, gripping, sliding, dragging, tugging, gathering or holding one. '
         + '`both hands sliding the jersey hem upward` is a jersey in a photograph whose '
         + 'wardrobe put the jersey down twenty photographs ago, and a prompt that names '
         + 'a jersey has a jersey in it. When a photograph is about something coming off, '
         + 'write what the body does — arms raised, elbows high, back arched, shoulders '
         + 'drawn — and let the wardrobe say the rest.',
    },
  },
  edit: {
    label: 'Photo edit',
    blurb: 'Instructions on one photo of the session: wardrobe off, a new pose, another background.',
    rule: 'A take is an instruction, not a description — “remove the jacket”, not “woman in a dress”. '
        + 'It carries no trigger, no base prompt and no look, which is what lets it take something off.',
    refKind: 'edit',
    refDefault: true,
    examples: [
      'remove the jacket, same pose',
      'let the hair down',
      'change the background to a plain grey studio wall',
      'same outfit, new pose: sitting, leaning forward',
    ],
    footer: 'Each take gets a strength box — one dial between “hold the frame” so a garment edit lands '
          + 'clean, and “let the pose move”. Same prompt and seed at a few values is how you find yours.',
    enhance: {
      line: 'Write one instruction to apply to a photo that already exists: “remove the '
          + 'jacket”, “let the hair down”, “change the background to a plain grey studio '
          + 'wall”. Imperative, one edit. Describe only what changes — never the person, '
          + 'never the clothes that stay, never a trigger word or a style tag. The photo '
          + 'already shows all of that, and restating it is what makes an edit fail.',
      batch: 'Every line is a different edit of the same photo.',
    },
  },
  angles: {
    label: 'Camera angles',
    blurb: 'Walk the camera around one photo with an angle LoRA. Closed vocabulary, built below.',
    rule: 'Anchor on the widest frame you have. The model can only turn what the photo shows it: '
        + 'anchored on a close-up, a request for the back comes back a profile and any full-length '
        + 'framing invents the clothing below the crop. Reach for the anchor before the LoRA strength.',
    refKind: 'angles',
    refDefault: true,
    examples: ['built from the picker above — direction, height, framing'],
    footer: 'Words outside the LoRA\'s vocabulary are ignored, so the picker writes the takes. '
          + 'Edit or delete any row afterwards like a normal take.',
    // No free-text writing for this kind: the vocabulary is closed, and prose the
    // LoRA drops looks exactly like prose it read. `ANGLE_FROM_TEXT` maps a
    // request onto the chips instead.
    enhance: null,
  },
  scene: {
    label: 'Scene + subject',
    blurb: 'Two reference photos into one frame — a character and a garment, a character and a place.',
    rule: 'Mark two references with 📎, in the order the graph expects: slot order is role. '
        + 'The run is refused unless the number of anchors matches the number of mapped reference slots.',
    refKind: 'scene',
    refDefault: true,
    examples: [
      'the woman from the first photo wearing the dress from the second',
      'the woman from the first photo standing in the room from the second',
    ],
    footer: 'Both photos have to exist first: shoot or import them, then mark them.',
    enhance: {
      line: 'Write one instruction that puts two photos in one frame, naming them by slot: '
          + '“the woman from the first photo wearing the dress from the second”. Slot order '
          + 'is role. Do not describe either subject beyond what tells them apart.',
      batch: 'Every line is a different combination of the two photos.',
    },
  },
}

/** What the ✨ buttons ask for. The rules are the ones already written down in
 *  docs/sessions.md; putting them here is what turns them into a button.
 *
 *  They live next to `KINDS` on purpose: the backend that talks to the LLM is
 *  kind-blind, the same way the rest of it is, so the guidance sits where the
 *  rest of the per-kind guidance already sits. */
/** The six lines a look is made of, head to toe — and which of the two boxes each
 *  one lands in.
 *
 *  The sections are a checklist before they are an ordering: what varies between
 *  frames is overwhelmingly what nobody wrote down, and asking for "the clothes"
 *  in one line is how a session ends up with no trousers named at all. One line
 *  per part of the body cannot silently skip a part.
 *
 *  `part` is what splits one reading of a photo into the session's two halves:
 *  hair, makeup, the place and the light are the same in every frame, and the
 *  four in between are the garments, which go into every take so a take can
 *  change them. */
export const LOOK_LINES = [
  { name: 'Hair and makeup', part: 'look' },
  { name: 'Upper body', part: 'wardrobe' },
  { name: 'Lower body', part: 'wardrobe' },
  { name: 'Feet', part: 'wardrobe' },
  { name: 'Accessories', part: 'wardrobe' },
  { name: 'Place and light', part: 'look' },
]

/** The four of the six that are the clothes. Every take carries these; the other
 *  two are the session's, written once. */
export const WARDROBE_LINES = LOOK_LINES.filter((l) => l.part === 'wardrobe')

/** What each section covers, and the two mistakes that quietly empty one out.
 *  Shared by the six-line read of a whole look and the four-line write of one
 *  take's wardrobe — the sections mean the same thing in both. */
const SECTION_MEANING =
  'Upper body is every layer from the skin out; lower body is trousers, skirt or the fall '
  + 'of a dress, with tights or socks; accessories are jewellery, belt, bag, glasses, '
  + 'watch. A garment covering both — a dress, a jumpsuit — goes on Upper body, and how it '
  + 'falls on Lower body.\n'
  + 'The names are not decoration: they are what stops a section being skipped without '
  + 'anyone noticing, which is how a look ends up with no trousers in it. Do not enumerate '
  + 'what catches the eye — walk every one of them.'

/** The look is not allowed to repaint the character.
 *
 *  The LoRA at the front of every prompt IS the person: her hair, her face, her
 *  colouring. A look that names a hair colour does not decorate her, it
 *  overrules her — measured twice on this project. Session 150's look opened
 *  `Long dark hair falls loose past her shoulders` and every composed frame of
 *  that session came back dark-haired though the character is blonde; dropping
 *  that sentence (session 178) brought her back. Sessions 200-206 carried `Her
 *  dark hair falls loosely around her shoulders` and rendered a dark-haired
 *  woman for seven sessions, while the same photographs written without it came
 *  back blonde and correct.
 *
 *  So the look writes how the hair is WORN and never what colour it is. */
const NOT_THE_CHARACTER =
  'NEVER NAME A HAIR COLOUR, and never describe her face, her skin or her build. The '
  + 'character comes from the trigger word at the very front of the prompt, which is a '
  + 'trained likeness of one specific person, and a colour written here does not decorate '
  + 'her — it repaints her. Measured twice: a look opening `long dark hair` rendered a '
  + 'dark-haired woman in every frame of a session whose character is blonde, and the same '
  + 'photographs with the colour left out came back as herself. Write only how the hair is '
  + 'WORN — loose, pinned up, pushed back, damp, slept-in — and let the colour be hers.'

/** The rule a negation breaks. Applies to a garment, a bag and an empty room
 *  alike: the prompt is read as a description, so naming a thing puts it there. */
const NEVER_ABSENT =
  'Never write what is absent — “no shirt”, “no bag”, “without a jacket”, “empty room”. '
  + 'This is read as a description, not as a list of conditions: “no bag” puts a bag in the '
  + 'photo. When a garment is off, it is simply not written; what you write instead is the '
  + 'skin under it — “bare shoulders, bare chest” is a body without a shirt, “no shirt” is a '
  + 'shirt. A section with genuinely nothing in it is the one exception: write `none` after '
  + 'the bar and it is dropped.'

const COUNTS = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six']

/** The house style, and the one rule this whole file used to have backwards.
 *
 *  Krea 2 reads its prompt with Qwen3-VL — a language model, not the CLIP text
 *  tower every keyword habit was built for. It parses grammar, so `a jersey over
 *  a harness` puts the harness underneath, where a comma between the two puts
 *  both on top of each other.
 *
 *  Measured on this project, one outfit, six seeds, the same facts written both
 *  ways: as comma fragments the hem written `cut above the ribs` came back cut
 *  in three frames of six and the harness was a different garment in all six.
 *  As sentences the hem held six of six and the harness repeated four of six.
 *  Fragments do not describe a garment to this model — they describe six. */
const PROSE =
  'Write sentences, not a list. Not `black leather jacket, silver zip, collar up` — '
  + '`She wears a black leather jacket with a silver zip, the collar turned up`. The prompt '
  + 'is read by a language model, which is why this matters more than it looks: a sentence '
  + 'can say that one thing is *under* another, or *pushed up over* it, and a comma cannot. '
  + 'Between two fragments the reader has to guess the relation, and it guesses differently '
  + 'in every photograph.\n'
  + 'So say the relations out loud — over, under, tucked into, pushed up above, hanging '
  + 'from, fastened at — and let the grammar carry them.'

/** How long is long enough. Twenty words a line, and that is measured, not a
 *  hunch — the same shoot run at both lengths says so.
 *
 *  There is no token wall here: the encoder is a 4B language model, not the
 *  77-token CLIP tower every "keep it under 75 tokens" habit was built for, and
 *  Krea's docs name no maximum. It is tempting to conclude that more is free.
 *  It is not, and this project has paid for the answer twice over:
 *
 *  At ~150 words of composed prompt, six seeds of one outfit came back with the
 *  same harness six times and the jersey's hem right six times.
 *
 *  At ~320 words — the same instructions with this cap lifted, forty frames — the
 *  jersey turned into a grey printed t-shirt in one frame, the stockings grew
 *  into a whole fishnet bodysuit by the last, the harness was a different design
 *  again, and the share of takes naming a garment their own wardrobe had already
 *  put down went from one in six to nearly two in five. Detail past the point of
 *  identifying a garment does not pin it down harder; it gives the reader more
 *  room to wander, and the framing sentence in front of it drowns.
 *
 *  So the cap is back. What changed against the old rule is the *style* — prose
 *  instead of comma fragments — and only that. */
const LENGTH =
  'Ten to twenty words a line, thirty at the absolute most for the piece that carries the '
  + 'look. This is a real limit, not a nicety: measured on this model, the same wardrobe '
  + 'written long enough to double the prompt came back as a different garment several '
  + 'frames in, where the short version held.\n'
  + 'Write what identifies the garment and then stop — colour, fabric, cut, the one detail '
  + 'that tells it from its neighbours, and the count of anything countable. A third '
  + 'sentence polishing what two already said is not more precision, it is more surface for '
  + 'the reader to reinterpret.\n'
  + 'Keep the lines within a few words of each other. Twice the words on one section is '
  + 'that section filling the frame.'

const sections = (lines) =>
  `Answer as exactly these ${COUNTS[lines.length].toLowerCase()} lines, in this order, each `
  + 'one starting with its name and a bar, and each one written as prose:\n'
  + lines.map((l) => `${l.name} | She …`).join('\n') + '\n'
  + `${COUNTS[lines.length]} lines every time. The names are a checklist, not a style: what `
  + 'follows the bar is one or more complete sentences.'


/** How far down the body the words go, and why it is not decoration.
 *
 *  A sampler frames what the prompt describes: a look whose words stop at the
 *  thigh is a photograph that stops at the thigh, however the take is framed.
 *  Measured - with one line of depth four seeds of a full-length take came back
 *  full length, and without it the same four cropped at the knee.
 *
 *  It belongs to the LOOK only. The wardrobe carried it too until 2026-08-17,
 *  when the wardrobe became a description of the garments and nothing else (see
 *  WARDROBE_INSTRUCTION). If full-length takes start cropping at the thigh, this
 *  paragraph is the first thing to put back - in the look, not the wardrobe. */
const BODY_WALK =
  'Lower body and Feet are almost never `none`, and this is where a look most often '
  + 'goes wrong. When a garment stops at the hip, the legs below it are still in the '
  + 'photograph: write them, the skin, tights or stockings and where they end, down '
  + 'through the knees and the ankles. When there are no shoes, write the feet themselves '
  + 'and what they stand on. A look whose words stop at the thigh is a photograph that '
  + 'stops at the thigh, however the take is framed.'

const LOOK_SECTIONS = `${sections(LOOK_LINES)}\n${SECTION_MEANING}\n${BODY_WALK}\n${NEVER_ABSENT}`
const WARDROBE_SECTIONS = `${sections(WARDROBE_LINES)}\n${SECTION_MEANING}\n${NEVER_ABSENT}`

/** Detail is a zoom lens, and this is the rule that keeps it from being one.
 *
 *  A sampler frames what the prompt describes. Nineteen words on a bra, its
 *  straps and a necklace, with nothing said above the collarbone or below the
 *  thigh, is a photograph cropped from collarbone to thigh — head out of frame,
 *  the garment filling it. Measured on a real session: the look that did that
 *  spent a fifth of its words on one span of the body. */
const SECTION_BALANCE =
  'Keep the lines roughly the same length. Detail is not only description, it is also '
  + 'framing: the photograph closes in on whatever the words dwell on, so a look that '
  + 'spends four lines on one span of the body comes back cropped to that span, with the '
  + 'head or the feet outside the frame. Underwear is the trap — Upper body and Lower body '
  + 'then describe the same handspan twice. When that happens, spend the same care on the '
  + 'hair, the feet and the room, and the frame opens to hold them.'

/** What a garment has to say to come back the same twice. Fabric and cut are
 *  what a sampler reads; a colour on its own is a different dress every frame. */
const GARMENT_DETAIL =
  'For every garment write, in this order: colour and pattern, fabric and weave, cut and '
  + 'fit, and the detail that identifies it — neckline, straps, sleeve length, hem length, '
  + 'buttons, zip, collar, pockets — and how it is worn: tucked in or out, sleeves rolled, '
  + 'buttons open, belt fastened. `She wears a white linen midi dress with thin straps and '
  + 'a square neckline` comes back the same twice; `a white dress` is a different dress '
  + 'every time. What you do not write is not left out of the photo — it is invented, '
  + 'differently in every frame.\n'
  + 'So spend the words: eight at the very least on any garment that is there, and up to '
  + 'twenty on the one that carries the look. `shiny black boots` is three attributes '
  + 'short — height, heel, fastening — and each one of those is a boot that comes back '
  + 'different.\n'
  + 'Count what can be counted: how many straps, buckles, buttons, rings, chains, '
  + 'piercings. A number is the attribute that drifts most when nobody writes it, and the '
  + 'one the eye checks first between two frames — `three vertical straps` holds where '
  + '`strap webbing` is a different harness every time.\n'
  + 'Commit to the number. `multiple`, `several`, `a few` and `many` are the words to '
  + 'avoid: they are not read as a quantity, they are re-rolled into a different one in '
  + 'every photo of the session. A count that is off by one is still the same count in '
  + 'every frame, which is the whole point; write the most likely number and move on.'

/** The room is the framing control. This is the whole finding.
 *
 *  A take can ask for `full-length, head to feet` in three different phrasings
 *  and be ignored in all of them — measured, four seeds, none full-length. Nor
 *  does the canvas move it: the same four seeds at 9:16 came back cropped in the
 *  same place, the taller frame spent on empty ceiling. Nor is it the character
 *  LoRA: at strength zero, still none.
 *
 *  What moved it, on the first try and in four of four, was one sentence in the
 *  ROOM. `A tall mirror against a pale wall` is a backdrop: nothing stands
 *  between the camera and her, so nothing makes the camera step back, and it
 *  frames at whatever distance it likes — which is a mid-shot. `The bare wooden
 *  floor running away from the camera past the foot of the bed to a mirror at the
 *  opposite wall` cannot be painted from close up. To show that floor the camera
 *  has to be across the room, and from there the whole figure is in frame by
 *  geometry rather than by instruction.
 *
 *  So framing is not asked for. It is made unavoidable. */
const ROOM_DEPTH =
  'The place is written as a space with depth, not as a backdrop, and this is the single '
  + 'most load-bearing line of a look. Say what lies BETWEEN the camera and her and what '
  + 'lies behind her: the floor running away underfoot, the furniture it passes, the far '
  + 'wall it reaches. `A mirror against a pale wall` is a flat backdrop and the camera has '
  + 'no reason to stand anywhere in particular. `The bare wooden floor running away from the '
  + 'camera past the foot of the bed to a mirror at the far wall` puts the camera across the '
  + 'room, because there is no other place it could be standing.\n'
  + 'That is how a photograph gets the whole figure in it. Measured on this model: with a '
  + 'flat wall, four seeds of a take asking in three different ways for head-to-feet gave '
  + 'four crops at the knee; with the floor running away, the same four seeds gave four '
  + 'photographs of the whole body. Nothing else moved it — not the wording of the take, not '
  + 'a taller canvas, not turning the character off. A room with depth is worth more than '
  + 'any sentence about framing.'

export const LOOK_INSTRUCTION =
  'Write the look of one photo session — what is worn and where, so that it comes back the '
  + 'same in every photo of it.\n'
  + LOOK_SECTIONS + '\n'
  + GARMENT_DETAIL + '\n'
  + SECTION_BALANCE + '\n'
  + NOT_THE_CHARACTER + '\n'
  + PROSE + '\n'
  + LENGTH + '\n'
  + ROOM_DEPTH + '\n'
  + 'No filler: “visible in frame”, “clearly seen”, “today”, “always”, “in this setting” '
  + 'describe nothing and take attention from the words that do. Never list alternatives '
  + 'or enumerate what something could have been.\n'
  + 'Never write pose, framing or camera angle: those belong to the takes, and here they '
  + 'would repeat in every photo. Never write anything about the person: no face, no body, '
  + 'no age, no expression.'

export const LOOK_FROM_PHOTO_INSTRUCTION =
  'Read the photo and write the look it shows, so that the same clothes can be generated '
  + 'again from the words alone.\n'
  + LOOK_SECTIONS + '\n'
  + GARMENT_DETAIL + '\n'
  + SECTION_BALANCE + '\n'
  + NOT_THE_CHARACTER + '\n'
  + PROSE + '\n'
  + LENGTH + '\n'
  + 'Write the place as the space it is, not as the wall behind her: where the floor goes, '
  + 'what stands on it between her and the camera, what the far side of the room is. A room '
  + 'written as a flat backdrop is a session photographed at arm\'s length, whatever its '
  + 'takes ask for — with one line of depth, four seeds of a full-length take came back full '
  + 'length, and without it the same four cropped at the knee.\n'
  + 'What you cannot do here is invent it: if the photo is a close-up against a wall, the '
  + 'wall is what you write. A depth that is not in the photograph is a different room.\n'
  + 'No filler — “visible in frame”, “clearly seen”, “in this setting” describe nothing.\n'
  + 'Only what the photo shows. Never a list of alternatives, and never a guess about what '
  + 'is out of frame: a garment cropped away is a section left out, not a section invented.\n'
  + 'Never describe the person wearing it — no face, no body, no age, no ethnicity, no '
  + 'expression. The character comes from a LoRA, and another person\'s features written '
  + 'here fight it in every frame of the session.\n'
  + 'Never write the pose, the framing or the camera angle either: this is what is worn, '
  + 'not how it was photographed.'

/** The look box on its own: the two sections that are the same in every frame.
 *
 *  Asking for all six here would write garments into the one sentence that is
 *  prepended unchanged to every take — which is the arrangement this split
 *  exists to undo. */
export const LOOK_ONLY_INSTRUCTION =
  'Write the hair, the makeup, the place and the light of one photo session: the part that '
  + 'is identical in every photo of it.\n'
  + sections(LOOK_LINES.filter((l) => l.part === 'look')) + '\n'
  + NEVER_ABSENT + '\n'
  + NOT_THE_CHARACTER + '\n'
  + PROSE + '\n'
  + ROOM_DEPTH + '\n'
  + 'One to three sentences a line. No filler: “visible in frame”, “clearly seen”, '
  + '“in this setting” describe nothing.\n'
  + 'Never write the clothes. They belong to each photograph separately, are written '
  + 'elsewhere, and a garment written here would be prepended to every take of the session '
  + 'including the ones that take it off.\n'
  + 'Never write pose, framing or camera angle: those belong to the takes. Never write '
  + 'anything about the person: no face, no body, no age, no expression.'

/** One take's wardrobe. Same sections, same detail rules as a look — this is the
 *  same job, asked one photograph at a time, which is the only way a garment
 *  comes off without the prompt arguing with itself. */
export const WARDROBE_INSTRUCTION =
  'Write what is worn in one photograph.\n'
  + WARDROBE_SECTIONS + '\n'
  + GARMENT_DETAIL + '\n'
  + SECTION_BALANCE + '\n'
  + PROSE + '\n'
  + LENGTH + '\n'
  + 'Write the state of the body in this photograph, not the change that led to it: '
  + '“Her cropped jersey is pushed up above her chest, the black harness underneath it '
  + 'showing” is a photograph, '
  + '“she lifts her jersey” is a sentence about one. A piece that is off is not mentioned at '
  + 'all — what you write instead is the skin, and how what remains sits on it.\n'
  + 'Never write pose, framing or camera angle, never the place or the light, never the '
  + 'hair, the makeup or the person: all of those are already in the prompt, above this.\n'
  + 'THE GARMENTS AND NOTHING ELSE. Not the body under them, not bare legs or bare '
  + 'skin, not the feet as feet, not the floor or the rug they stand on. A section '
  + 'with no garment in it is `none`. What the body looks like is not the wardrobe to '
  + 'say - the take says it, and the look says how far down the picture goes.'

/** The one that makes a shoot walk from dressed to undressed.
 *
 *  Measured on real sessions: what holds a wardrobe together across twenty frames
 *  is not describing it once, it is describing it *identically* every time. So
 *  the rule that matters here is not the undressing, it is the verbatim carry of
 *  everything that did not change. */
/** Everything that keeps a wardrobe *the same wardrobe* while it comes off.
 *
 *  Shared by the two writers that touch clothes across a shoot — the wardrobe
 *  progression, and the one-line-per-photograph writer — because the rules are
 *  about the garments, not about the shape of the answer. Most of it is not
 *  about undressing at all: it is about copying, word for word, the pieces that
 *  did not change. That is what holds an outfit together over forty frames, and
 *  every one of these paragraphs was bought with a run that got it wrong. */
const GARMENT_CARRY =
  'Only the pieces given exist in this shoot. A state may have fewer of them, or the same '
  + 'ones worn differently, and nothing else. A garment that is not in the wardrobe below '
  + 'cannot appear in any state, and running out of clothes is never a reason to write one: '
  + 'it is the reason to stop. Measured, on a real session: asked for more states than the '
  + 'wardrobe had left, an assistant put her in a whole new dress halfway through so that '
  + 'there was something to take off again, and the session came back as two shoots.\n'
  // Shortening the carry was tried and measured, and it is the one thing in here
  // that must not be done: told to describe a garment fully once and then carry it
  // as `the white sailor top`, the writer obeyed and the lines dropped from 107
  // words to 99 — and the white fishnet stockings came back BLACK from photograph
  // nine on, four frames of a twelve-frame shoot, where the full-description arm
  // held them white in all twelve. A short name is a smaller handle for the
  // sampler to hold, and what it does not hold it re-rolls. The length is the
  // price of the wardrobe staying the same wardrobe.
  + 'Copy every piece that has not changed word for word from the line before it. Not a '
  + 'synonym, not a shortening, not a tidier phrasing: a garment reworded is a different '
  + 'garment in the photograph, and the whole point of repeating the wardrobe in every line '
  + 'is that the pieces nobody touched come back identical.\n'
  + 'Change one thing at a time, and change it in the order the shoot asks for. One line, one '
  + 'change: two pieces moving in the same line is a photograph nobody asked for and a step '
  + 'of the shoot that never got shot.\n'
  + 'Pace it across all the lines you were asked for, and end on the last one. A shoot with '
  + 'more photographs than pieces to take off spends the difference on moving them — pushed '
  + 'up, pulled aside, unbuttoned, off one shoulder — so that the change from one line to the '
  + 'next is always small. Reaching the end of the shoot in the third line and repeating '
  + 'yourself after it is the failure to avoid: the number of lines is the length of the '
  + 'shoot, not a target to hit early.\n'
  + 'The line where a piece finally comes off is the one that goes wrong, every time. That '
  + 'line does not contain the name of that piece at all — not “jersey removed”, not “jersey '
  + 'off”, not “without the jersey”, not “no top”, not “bare, the jersey gone”. The word '
  + '`jersey` anywhere in the line is a jersey in the photograph, whatever the words around '
  + 'it say, and the same is true of `top`, `bra` or `dress`. The piece is simply not there: '
  + 'the line before it named the garment, this line names skin — “bare shoulders, bare '
  + 'chest, bare arms” — and reads as if that photograph never had one.\n'
  + 'A piece can also move before it goes: pushed up, pulled aside, unbuttoned, off one '
  + 'shoulder, unzipped. Write where it sits now and what it no longer covers.\n'
  + 'Once the body is bare, keep writing what is still on it — the jewellery, the choker, '
  + 'the stockings, the shoes — and say the skin plainly: `nude`, `topless`, `bare from the '
  + 'waist down`. A line that stops at the last garment is a photograph cropped to it.\n'
  + NEVER_ABSENT.split('A section with genuinely')[0]
  + 'This catches the tidy ending too: a line that finishes “no rings, no watch, no bag” '
  + 'has just put a ring, a watch and a bag in the photograph. An inventory of what she is '
  + 'not wearing is not a wardrobe — stop at the last thing she is.\n'
  + 'Bare skin carries over exactly like a garment does. Every line covers the whole body, '
  + 'from the shoulders down through the hips and the legs to the feet, however little is '
  + 'left on it: a chest that was bare in the line before is still bare in this one and is '
  + 'still written. A line that names only the part that changed is a photograph cropped to '
  + 'that part — the frame reaches as far down the body as the words do, and no further.\n'
  + 'And a piece the shoot asks to keep on stays on, in every single line, in the words it '
  + 'started with.'

/** The wardrobe alone, one line per state, for the takes-and-wardrobes writer.
 *
 *  Still here, and still used by the 👗 button, but it is no longer how a whole
 *  session gets written — see SHOOT_LINE_INSTRUCTION for why two streams that
 *  never speak to each other end a shoot with the clothes off and the body still
 *  standing to attention. */
export const WARDROBE_PROGRESSION_INSTRUCTION =
  'Below is the wardrobe of a photo session. Write it once per state the shoot passes '
  + 'through, following the shoot described above.\n'
  + 'One line per state, in order. Each line is a whole wardrobe on its own, written as '
  + 'prose — two to four sentences covering the upper body, the lower body, the feet and '
  + 'the accessories, exactly as the wardrobe below covers them. A line is one line: the '
  + 'sentences run on, and no line ever contains a newline.\n'
  + GARMENT_CARRY + '\n'
  + 'Never write pose, framing, camera angle, expression, the place, the light, the hair or '
  + 'the person. Those are elsewhere in the prompt, and repeating them here repeats them in '
  + 'every photograph of the session.'

/** Handed back to the writer when a line failed a check the code can make.
 *
 *  Detection is the code's job and rewriting is the model's: a line repaired by
 *  string surgery reads like a line repaired by string surgery, and the thing
 *  being repaired is prose. So the complaint is specific and the model writes
 *  the line again. */
export const REPAIR_INSTRUCTION =
  'Rewrite the photograph below so that it fixes the problems listed, and changes nothing '
  + 'else. Same stage of the shoot, same wardrobe state, same pose, same expression — this '
  + 'is a correction, not a new photograph.\n'
  + 'Answer with the WHOLE photograph, restated from its first word to its last, with the '
  + 'fix folded into it. Not the part that changed, not a continuation, not a sentence '
  + 'beginning with an ellipsis: the line you write replaces the line below entirely and is '
  + 'queued exactly as you write it. Asked for a correction, the natural thing is to answer '
  + 'with the corrected fragment — that fragment would become the whole photograph, and the '
  + 'rest of it would be lost.\n'
  + 'Keep it to the same length as the line below, give or take a few words — unless one of '
  + 'the problems is that it is too long, and then cut it to the length that problem asks for. '
  + 'A correction that doubles the length has rewritten the photograph.\n'
  + 'Answer with the corrected line and nothing else.'

/** The brief itself, written from the look and the wardrobe.
 *
 *  Those two are the photograph in words, so a shoot written from them is one
 *  that photograph could be a frame of: the place comes from the look, and the
 *  arc from what is worn — boots and a harness do not begin the way a linen dress
 *  does. */
export const BRIEF_INSTRUCTION =
  'Write one sentence describing a photo session, the kind a photographer would be handed '
  + 'before shooting it.\n'
  + 'Say four things and nothing else: where it happens, how it begins, how it moves along, '
  + 'and where it ends.\n'
  + 'It has to fit the look and the wardrobe below, and it has to fit the shoot asked for '
  + 'underneath: how far this one goes is decided there, not here. The place and the light '
  + 'are already decided by the look; the clothes are what there is to move, and a shoot may '
  + 'only ever undo what is actually being worn.\n'
  + 'Twenty-five to forty-five words. One sentence, concrete, no adjectives doing the work '
  + 'of a fact: “starts sitting on the edge of the bed still in the jacket” is a brief, '
  + '“an intimate, sensual journey” is not.\n'
  + 'Forty-five words is a wall, not a target. A brief that runs long has started writing '
  + 'the photographs, and every take then comes back as that same photograph forty times '
  + 'over. If you are writing what her hands, her eyes or her weight are doing, stop: that '
  + 'is a take, and takes are written separately.\n'
  + 'Never write a pose, a framing or a camera angle for the same reason.\n'
  + 'The place is the one in the look and there is only ever one of it. A brief that starts in '
  + 'the kitchen and ends in the bedroom is a session that cannot be shot: the look is '
  + 'prepended to every photograph and it names one room, so the second room comes back as the '
  + 'first one with the words fighting each other.\n'
  + 'Name a garment twice at most: one for where the shoot starts, one for where it ends. '
  + 'Never narrate the pieces coming off in between — no “as the harness, the briefs and the '
  + 'choker come off”. When the shoot undresses at all, `undresses a step at a time` is its '
  + 'whole middle, and that is deliberate: this sentence is handed to the writer of the '
  + 'poses as well as to '
  + 'the writer of the clothes, and every garment named in it comes back named in a '
  + 'photograph that is no longer wearing it. Measured, on one session: a brief that listed '
  + 'the middle put the jersey, the harness and the briefs into three takes whose own '
  + 'wardrobe had already put them down.\n'
  + 'Never describe the person — no face, no body, no age. Never name the hair, the makeup '
  + 'or the light again: they are in the look already.'

/** What the dice decide.
 *
 *  Left to the model, the same look and the same wardrobe give back the same
 *  sentence however warm the sampler is — so the three things that actually make
 *  one shoot different from another are picked here and handed over as
 *  constraints. Every ending is somewhere along the way from dressed to not; how
 *  far and how fast is the point of rolling. */
/** How far a session goes, and where it starts — the one thing the dice must not
 *  decide for you.
 *
 *  Every ending is somewhere along the way from dressed to not, and rolling all
 *  of them at once means a shoot briefed for a lingerie set and a shoot briefed
 *  to end in penetration come out of the same button. So the reach is picked and
 *  the dice roll *within* it: the ending and the pace both come from the chosen
 *  one, because they are the same question asked twice — a shoot that never
 *  undresses has no undressing to pace, and a shoot that is explicit from the
 *  first photograph does not spend its first half getting there.
 *
 *  `also` is what the brief is told beyond the two rolls. It is only ever a
 *  second person: the writer of the lines will name the act plainly when the
 *  brief does, and will hedge it into a pose when the brief leaves it implied. */
export const REACHES = [
  {
    key: 'sfw',
    label: 'Clothed throughout',
    blurb: 'The wardrobe it starts in is the wardrobe it ends in.',
    endings: [
      'ends in the same clothes it started in, nothing removed',
      'ends with the outer layer open or pushed back, everything still on',
      'ends with one accessory added or taken off and nothing else',
    ],
    paces: [
      'holds the same wardrobe throughout and moves through poses instead',
      'moves through the room rather than through the clothes',
      'stays in one place and lets the pose and the expression carry it',
    ],
    also: 'NOTHING COMES OFF IN THIS SHOOT. It begins and ends in the wardrobe below, and what '
        + 'moves is where she stands, what she is doing and how she looks doing it. A brief that '
        + 'has her undressing a step at a time is the wrong shoot — measured, asked for a clothed '
        + 'session the writer wrote one that ended in the neckerchief alone.',
  },
  {
    key: 'nude',
    label: 'Dressed to undressed',
    blurb: 'Starts in the full wardrobe and undresses. No second person.',
    endings: [
      'ends with the outer layers off and the underwear still on',
      'ends topless, with everything below the waist still on',
      'ends undressed except for one piece that never comes off',
      'ends fully undressed, keeping only the shoes and the jewellery',
      'ends undressed and explicit, alone',
    ],
    paces: [
      'stays fully dressed for the first half and undresses steadily after it',
      'loses a layer early and then spends most of the shoot in what is left',
      'undresses evenly, a little in every photograph',
      'undresses in three clear stages with a stretch of photographs at each one',
      'holds the same wardrobe for a long opening stretch, then moves quickly',
    ],
    also: '',
  },
  {
    key: 'couple',
    label: 'Dressed to penetration',
    blurb: 'The whole arc: starts in the full wardrobe, ends with a man and the act.',
    endings: [
      'ends with a man joining her and explicit penetration, both of them in frame',
      'ends undressed with a man behind her, penetrating her, both of them in frame',
      'ends with her bent over and a man penetrating her from behind, both in frame',
    ],
    paces: [
      'stays fully dressed for the first half, undresses steadily, and he arrives at the end',
      'undresses evenly, a little in every photograph, and he arrives for the last stretch',
      'undresses in three clear stages and spends the last third of it with him',
    ],
    also: 'The last stretch of the shoot is explicit penetration with two people in the frame. '
        + 'Say so plainly in the brief: a shoot briefed for something implied comes back as two '
        + 'people standing near each other.',
  },
  {
    key: 'explicit',
    label: 'Explicit throughout',
    blurb: 'No build-up: undressed and explicit from the first photograph.',
    endings: [
      'ends with explicit penetration, both of them in frame, and it was explicit from the first '
      + 'photograph',
      'ends explicit with a man, having been undressed and explicit throughout',
    ],
    paces: [
      'is undressed from the first photograph and spends the whole shoot explicit',
      'opens already explicit with him and moves through positions rather than through clothes',
    ],
    also: 'THERE IS NO UNDRESSING IN THIS SHOOT and no getting ready either. She is already '
        + 'undressed and already with him in photograph one: the wardrobe below is what she is '
        + 'NOT wearing, save for anything you say she keeps on, and it is never taken off in the '
        + 'brief because it was never on. `starts still in the blouse and skirt` is the failure '
        + 'to avoid, and it is the one this shoot keeps making. What moves through the shoot is '
        + 'what the two of them are doing.\n'
        + 'So the brief SAYS SO IN ITS FIRST CLAUSE, in those words: `begins already naked with '
        + 'him`, `opens undressed on the counter with him`. A brief that leaves it to be '
        + 'inferred is read by the writer of the lines as a shoot that starts dressed, and it '
        + 'then spends the session getting there — measured, twice.',
  },
]

export const REACH = Object.fromEntries(REACHES.map((r) => [r.key, r]))

/** Who is holding the camera, and whether anyone is posing for it.
 *
 *  Orthogonal to the reach on purpose: how far a shoot goes and whether it looks
 *  shot by a photographer are two different questions, and every combination of
 *  the two is a shoot somebody wants. The default is the one this whole
 *  instruction was written for — a directed session, where she poses and someone
 *  shoots her — so `directed` adds nothing at all, and the other one is a block
 *  laid on top of it.
 *
 *  `brief` joins the brief instruction and `line` the writer of the photographs.
 *  Both, and not one: the brief decides what the shoot IS, the lines decide what
 *  the camera is doing, and a candid brief written into the studio camera
 *  vocabulary comes back as a studio shoot of a woman in a lived-in room. */
const BASE_MANNERS = [
  {
    key: 'directed',
    label: 'Directed shoot',
    blurb: 'Someone is photographing her and she is posing for it.',
    brief: '',
    line: '',
  },
  {
    key: 'candid',
    // The capture quality belongs to the LOOK and not to the line, for the same
    // reason the look exists at all: it is the half of a session that never
    // changes, and a writer retyping it forty times is a drift waiting to happen
    // while a block the app prepends cannot drift. Read off session 201, whose
    // fixed block carried exactly this and whose images were the first that read
    // as amateur at all. Appended to the look, never replacing it: the room in
    // the box is the room the shoot is in.
    // Measured 2026-08-21, sessions 214 and 215, same brief and same look but for this clause:
    // naming a wall and a doorway here as the example of flat focus put a device word in the
    // `technique` field of 20 lines of 25 and focus talk in 15, and saying it abstractly put
    // both at 0 of 24 - while the renders kept the sharp background either way. The example
    // was doing nothing the rule was not, and it was teaching two habits nobody asked for.
    look: 'phone camera snapshot, small sensor, everything at every distance equally in '
        + 'focus and nothing softened, sensor noise in the shadows, washed-out colour, '
        + 'slight motion blur, off-center and slightly tilted framing, no studio lighting '
        + 'and no colour grading',
    // For a look being WRITTEN for a candid shoot, where the light is still open.
    // An existing look is left alone — appending a bare bulb to a look that says
    // `a low warm lamp pooling amber light` is a contradiction, and this sampler
    // renders a contradiction as neither.
    lookNote: 'THIS SESSION IS SHOT ON A PHONE BY THE PEOPLE IN IT, so the light is whatever '
            + 'the room already has and never anything placed for a photograph: a bare ceiling '
            + 'bulb, weak daylight through a half-curtained window, a bedside lamp, the '
            + 'television. Never a lamp described for what it does to her — `pooling amber '
            + 'light from the side` is a lighting setup and it renders as one. The room is '
            + 'lived in and a little untidy.',
    label: 'Candid, on a phone',
    blurb: 'No photographer: a phone, a mirror, an ordinary evening. Nobody poses.',
    brief: 'NOBODY IS SHOOTING THIS PROFESSIONALLY. There is no photographer, no studio and no '
         + 'session: the pictures are taken on a phone — held out at full arm stretch, propped on '
         + 'something, pointed at a mirror — in the middle of an ordinary evening, by the people '
         + 'who are in them. So the brief is what she is DOING and how that goes on, never a list '
         + 'of setups: `bored on the sofa on a weeknight, getting comfortable and filming herself '
         + 'through the evening` is this shoot, `an intimate boudoir session` is not.\n'
         + 'Do NOT say where the phone is or what it is propped on. The brief is read by the '
         + 'writer of every photograph, so a phone named once here is a phone named in forty '
         + 'lines and painted into forty photographs - measured, `the phone propped on the '
         + 'armrest` in one brief put a phone in 24 lines of 25.\n'
         + 'Never write `shoot`, `session`, `poses`, `photographer`, `professional`, `studio` or '
        + '`shot` in it. Every one of '
         + 'those words is read by the writer of the photographs as a person standing behind a '
         + 'camera, and it then writes forty posed frames whatever the rest of this sentence '
         + 'said.',
    line: 'NOBODY IS PHOTOGRAPHING THIS. Every photograph was taken on a phone by one of the '
        + 'people in it, and it has to look like it: a shoot that comes back looking shot is the '
        + 'whole failure of this one.\n'
        + 'THE `technique` FIELD IS HOW THE PHOTOGRAPH WAS TAKEN, AND IT IS THE ONE PLACE THAT '
        + 'GOES IN. Not in the camera field, which is where the camera stands and how much of '
        + 'her is in frame, and nowhere else either: a rule with no field of its own is a rule '
        + 'that does not arrive - measured, the whole-room focus asked of the line reached 0 '
        + 'lines of 25 until it was moved somewhere that owns it.\n'
        + 'EVERY CLAUSE IN IT SAYS WHERE THE DEFECT FALLS. A defect named with no attachment '
        + 'at all lands nowhere: measured 2026-08-24, sessions 277 and 278, four arms of eight '
        + 'seeds sharing one hand-fixed line, judged blind on where the blur went. `slightly '
        + 'blurred` put it on her hand in 0 photographs of 8 - which is exactly what a '
        + 'photograph with NO technique clause at all scored, 1 of 8. The bare adjective is not '
        + 'a weaker version of an anchored clause, it is the same photograph as writing '
        + 'nothing.\n'
        + 'What the attachment has to be is smaller than it looks. `slightly blurred where a '
        + 'hand moved` - a where-clause naming nobody - already scores 4 of 8 (against the bare '
        + 'form, p=0.038), and `blurred down her forearm where her hand moved at her side` '
        + 'scores 6 of 8, which is not a real gain over it at this size (p=0.30). So the rule '
        + 'is the attachment and NOT the anatomy: name where it falls, and do not pad the '
        + 'clause chasing detail that does not render. The examples below name a part of her '
        + 'because that is the cheapest way to always have somewhere to attach it.\n'
        + 'What goes in it, three or four of these, chosen for THIS photograph and changed from '
        + 'line to line: `blurred down her forearm where her hand moved`, `the shadow under her '
        + 'arm gone to noise`, `flat and overexposed across her chest and shoulders`, `the '
        + 'colour washed out of her skin`, `the near side of her face a stop too bright`, `the '
        + 'grain heavy in the shadow under her jaw`, `her shoulders running a few degrees off '
        + 'level in the frame`, `her body pushed to one side of the frame with empty space '
        + 'beside her`. Measured over 250 lines, the old list - seven bare adjectives and one '
        + 'anchored form - left 32% of clauses with no attachment of any kind, and this list '
        + 'leaves none: 68% of lines carried one before and 100% carry one after, with the '
        + 'per-run low going from 11 of 25 to 25 of 25. It '
        + 'NAMES NOTHING IN THE ROOM - no wall, no window, no bed, no furniture: the room is in '
        + 'the prompt already and a technique clause that names a corner of one invents a different room. Measured, `empty room down one side` as the example came back as '
        + '`empty bedspread down one side` and a headboard, in a shoot whose look is a living '
        + 'room. It NEVER '
        + 'NAMES A DEVICE - no phone, no camera, no lens - and it NEVER INVENTS A LIGHT THE ROOM '
        + 'HAS NOT GOT: a flash belongs there only when the look says the room is dark. Measured '
        + '2026-08-21, three of the four examples in this paragraph named a flash and nine lines '
        + 'of twenty-five fired one in a room whose look said flat white daylight; the examples '
        + 'were changed and it went to zero of twenty-five. The writer copies the examples, not '
        + 'the rule.\n'
        + '`Snapshot on a phone camera` is the WRONG form of that clause and it is the one this '
        + 'writer reaches for: measured 2026-08-21 on a real run of this instruction, the phone '
        + 'was named in 24 lines of 25 and every one of them named it there. The camera is not '
        + 'in the photograph. What the photograph has is its grain, its flash and its bad '
        + 'framing.\n'
        + 'NEVER WRITE THE WORDS THAT MAKE A PICTURE GLOSSY: `professional`, `studio`, '
        + '`softbox`, `editorial`, `cinematic`, `photoshoot`, `posed`, `high resolution`, `8K`, '
        + '`hyper-realistic`, `photorealistic`, `sharp focus`, `shallow depth of field`, '
        + '`bokeh`, `DSLR`, `85mm`. Every one of them is read as a paid photographer with '
        + 'equipment, and it comes back as the studio shoot this manner exists to avoid.\n'
        + 'WHERE THE CAMERA IS IS ALREADY DECIDED and handed to you with each photograph, in the '
        + 'words this shoot was measured to obey - a phone was put down somewhere, and that '
        + 'place is not yours to move. Open the line with it word for word. The three framings '
        + 'are unchanged and there is still one of them in every line, after the position.\n'
        + 'THE PHONE ITSELF IS ALMOST NEVER IN THE PICTURE, and this is the rule this shoot '
        + 'keeps breaking. Name a phone ONLY when it is genuinely in the frame: her own hand '
        + 'holding it out towards the lens, or the phone up in her hand in a mirror - `Mirror '
        + 'selfie, the phone up in her right hand and visible in the mirror`. That is one line '
        + 'in five or six at the most - UNLESS the brief itself is a shoot she is filming of '
        + 'herself, and then the phone is in her hand in every photograph it is really in, and '
        + 'saying so is not the failure this paragraph is about. In every other line NO DEVICE '
        + 'IS NAMED AT ALL, because a '
        + 'phone or a camera named in a line is painted as an object sitting in the photograph, '
        + 'and forty pictures with a gadget floating in them are as broken as forty studio ones. '
        + 'Amateur is how the picture LOOKS, never a prop it contains.\n'
        + 'THE FRAMING IS CARELESS, THOUGH THE CROP IS NOT. One of the three framings, always - '
        + 'what is sloppy is where she falls inside it: off to one side instead of centred, the '
        + 'horizon tilted a few degrees, an elbow or a knee running out of the edge, a stretch '
        + 'of empty room above her head. It goes in the `camera` field, after the position '
        + 'and the framing, in the same sentence: `Taken from behind her left shoulder, a waist-up '
        + 'photograph, she is off to the left of the frame and the horizon is tilted a few degrees`, '
        + '`Overhead camera directly above her, a full-length photograph, her feet running out of '
        + 'the bottom edge`, `Taken from directly in front of her, a three-quarter photograph, a '
        + 'stretch of empty room above her head`.\n'
        + 'NOTHING IS BLURRED BEHIND HER, and the look already says so: the whole room is in '
        + 'one plane of focus, because the sensor is the size of a fingernail. Never write a '
        + 'line that argues with it - no blurred background, no softened room, nothing `falling '
        + 'away` behind her. Measured 2026-08-21: asked for in the line instead, it arrived in 0 '
        + 'lines of 25 - the writer answered in six fields and none of them owned the depth of '
        + 'field, so it belongs to the look and to nothing else.\n'
        + 'HER EYES ARE NOT ON THE LENS. Not once, unless that line has her holding the phone '
        + 'and looking at its screen, or the photograph is named below as a kiss frame - that '
        + 'one is written out for you in full and it overrides this paragraph for its line '
        + 'only. She is looking at him, at her own hands, at the '
        + 'television, past the camera at nothing, down, away, or her eyes are shut. There is '
        + 'nobody behind the camera to look at, and eye contact with a camera nobody is holding '
        + 'is the posed photograph this whole manner exists to avoid - it survives every other '
        + 'rule in this block, because the body can be mid-step and half turned away and the '
        + 'photograph still reads as posed the moment she is looking at the lens.\n'
        + 'SHE IS NOT POSING, because there is nobody to pose for. Her body is doing something '
        + 'ordinary and half-finished: mid-step, half turned away, her weight dumped on one hip, '
        + 'sitting badly, one arm out of frame, one hand still holding the phone on the few '
        + 'lines where the phone is in frame at all — and say so '
        + 'when it is. `standing square to the camera, one hand raised near her face` is the '
        + 'posed failure to avoid, and it is the one this writer makes by default.\n'
        + 'THE FACE IS CAUGHT, NEVER HELD: talking, laughing, mid-blink, her mouth open on a '
        + 'word, her eyes down on the phone screen instead of on the lens, or not on the camera '
        + 'at all. An expression held towards the lens is a posed photograph however unposed the '
        + 'body underneath it is.\n'
        + 'THE LIGHT IS THE LIGHT THE ROOM ALREADY HAS, and the room says which: read it off '
        + 'the look above and do not invent another. A room lit by a window in the daytime is '
        + 'NEVER lit by a flash and never by a lamp - measured 2026-08-21, seventeen lines of '
        + 'twenty-five fired a flash in a room whose look said flat white daylight and no lamps '
        + 'on. What a phone does to daylight is where the amateur look comes from: the window '
        + 'side a stop too bright, hard shadow on the far side of her, colour washed out and a '
        + 'little cold, noise in every shadow. At night it is the other set - a bare overhead '
        + 'bulb, the flash blowing out the nearest skin, the television. Never a light described '
        + 'by what it does to her: `pooling amber light from the side` is a lighting setup and '
        + 'it renders as one. The room is never lit for the photograph; the photograph is '
        + 'whatever the room already was.',
  },
]

/** The one they filmed themselves, mid-act, on her phone.
 *
 *  `candid` with two of its rules turned around, and nothing else: it is the
 *  same phone, the same room and the same look, so it inherits all of them and
 *  only overrides what it has to. What it has to override is exactly what makes
 *  this shoot the thing it is - `candid` says the phone is almost never in the
 *  frame and her eyes are never on the lens, and here the phone is in her hand
 *  in every photograph and she is looking into it.
 *
 *  Where the wording came from: sessions 155 and 161, twenty-five and twenty
 *  photographs written by hand outside the app and shot straight. What 161 has
 *  that 155 has not, and it is the whole difference between the two batches, is
 *  the named subject (`young woman`, per [[idevgen-doggy-token-banned]]), the
 *  face written out in full, and the act said to be visible in the lower part of
 *  the frame. Those are the three things the delta below asks for, each pointed
 *  at the field that owns it.
 *
 *  SHOT ONCE, SESSION 264: twenty photographs, finepornV4 at 12 steps, the app's
 *  own room, judged blind three passes a photograph. What it says, and none of it
 *  is an arm - one shoot at n=20 with two to four photographs per camera form:
 *
 *  * THE SHOOT IS THE SHOOT. Nineteen of twenty photographs came back with two
 *    bodies and the act in them, against sixteen lines that asked for it: the
 *    four opening lines that asked only for the two of them near each other
 *    rendered the act anyway. Whatever else is wrong here, the genre lands.
 *  * THE CAMERA PLAN IS OBEYED WORD FOR WORD in all twenty lines, which is the
 *    same thing `candid` measures.
 *  * THE PHONE IS NOT CONTROLLABLE FROM THE CAMERA CLAUSE, and this is the one
 *    the manner asks for and does not get. Of ten lines whose camera puts the
 *    phone in her hand, four painted a phone; of ten that ask for no device,
 *    four painted one anyway. The judge was unanimous on fourteen of twenty
 *    photographs, so the spread is in the RENDER and not in the reading.
 *    Broken out: the mirror form painted it 2 of 2 and both `pov` forms once
 *    each, while `Phone held out at arm's length in front of her face` - the
 *    form this manner leans on hardest - painted NO phone in four of four, with
 *    the arm asked for in `act` every time. That is `candid`'s own finding
 *    ([[idevgen-candid-camera-renders]]: the word `phone` paints no phone, the
 *    mirror is the exception) arriving again in the one manner that wanted the
 *    opposite.
 *  * The two `pov` forms render the GENRE and not their geometry: `Phone held
 *    above her face ... as she lies on her back` came back as a POV from under
 *    him with her upright on top. A good photograph of this kind, and not the
 *    one the clause describes.
 *
 *  THE ARM IS THE SWITCH, AND THE CAMERA CLAUSE BUYS ALMOST NOTHING. Sessions
 *  265 and 266, the 227/228/244 protocol: one line fixed by hand, three seeds,
 *  five held camera forms, and the judge asked only who was holding the camera
 *  (`judge_camera.py --question holder`, calibrated 11/11 against the user's own
 *  ratings of 264). With the pose saying nothing about her arms, the four
 *  non-mirror forms read as HERS in 4 photographs of 12. With one clause added
 *  to the pose - her free arm stretched toward the camera, the near hand and
 *  forearm large at the frame edge - the same forms on the same seeds read as
 *  hers 12 of 12, and 11 of the 12 still render the act.
 *
 *  What follows from that, and it is why the first rule of the delta below is
 *  written the way it is:
 *
 *  * A held camera clause WITHOUT the arm is not a selfie, whatever it says. In
 *    264 only 5 photographs of 20 read as hers, and the arm is what those five
 *    lines had.
 *  * The mirror is the exception and stands alone: 3/3 with no arm at all,
 *    because there the phone is really in the reflection.
 *  * The word `phone` inside the camera clause is inert. `Phone held out at
 *    arm's length in front of her face` and `Taken from an arm's length in front
 *    of her face` scored the same in both conditions.
 *  * Which held form is chosen barely matters, so the two `pov` entries stay:
 *    with the arm written they are 3/3 like the rest.
 *
 *  Still open: the shipped wording of the `pov` form that ends `as she lies on
 *  her back` is a POSE inside a camera clause and it contradicts every pose but
 *  one. 266 shot it with that tail cut and it was 3/3; the tail itself has never
 *  been shot against anything.
 */
const SELFIE = {
  ...BASE_MANNERS[1],
  key: 'selfie',
  label: 'Filmed by them, on her phone',
  blurb: 'She holds the phone through it: selfies and mirror shots of the two of them.',
  brief: BASE_MANNERS[1].brief
       + '\nTHE PHONE IS HERS AND IT STAYS IN HER HAND for the whole of it, so the brief is an '
       + 'evening the two of them filmed on it rather than one she happens to be photographed '
       + 'in. That does NOT license naming the phone here - the paragraph above still holds, '
       + 'and where it is is decided per photograph.',
  line: BASE_MANNERS[1].line
      + '\n\nTWO RULES ABOVE ARE TURNED AROUND FOR THIS SHOOT, and they are the two that make '
      + 'it the shoot it is. Everything else in this block stands: the technique field, the '
      + 'flat focus, the room light, the careless framing, the words that make a picture '
      + 'glossy.\n'
      + 'FIRST: SHE IS HOLDING THE PHONE AND HER OWN ARM IS IN THE FRAME. THIS IS THE ONE '
      + 'THING IN THE LINE THAT DECIDES WHETHER THE PHOTOGRAPH READS AS HERS - measured at '
      + '4 of 12 without it and 12 of 12 with it, same seeds, same camera clauses. The camera '
      + 'clause does NOT do it for you: a held form with no arm written into the pose comes '
      + 'back as a photograph somebody else took. Not one line in six - '
      + 'every line whose camera clause is one of the held forms, which is most of them. Write '
      + 'the ARM and never the device: `her arm stretched out towards the lens`, `her near hand '
      + 'far bigger than the rest of her`, `her forearm running out of the bottom of the frame`. '
      + 'It goes in `act`, beside what she is doing with the other hand. The camera clause you '
      + 'are handed has already decided where the phone is; a line that names it again paints a '
      + 'phone sitting in the room. The mirror form is the one exception, and there the phone is '
      + 'genuinely up in her hand in the reflection and is written as such.\n'
      + 'SECOND: HER EYES ARE ON THE LENS. She is looking into the screen she is holding, so the '
      + 'paragraph above about eye contact does not apply to this shoot at all - what it was '
      + 'protecting against was a look held for a photographer who is not there, and here the '
      + 'person she is looking at is herself.\n'
      + 'THE FACE IS WHY THE PHOTOGRAPH WAS TAKEN, and `face` is the field that carries it: '
      + 'written out in full, changed line to line, and never held still. Her mouth open on a '
      + 'sound, her eyes half-shut, her eyes squeezed shut, her head back, her jaw slack, her '
      + 'face turned into the pillow, flushed, wet, sweat in her hair. Read off the two batches '
      + 'this shoot came from: the one with the face written out came back as these photographs '
      + 'and the one with `slight smile` in it did not.\n'
      + 'HE IS IN THE FRAME AND SO IS WHAT THEY ARE DOING. `him` carries his body where it '
      + 'touches hers - behind her, over her, his hands where they are - and `act` says plainly '
      + 'what is happening, in the low words this register already uses. Where a held phone '
      + 'points at her face, say that the act is in the lower part of the frame, under her: '
      + '`the two of them joined in the bottom of the frame`. A line that leaves it out is a '
      + 'photograph of a woman making a face.\n'
      + 'THE FRAMING STILL COMES FROM THE THREE. A held phone is close to her by its nature and '
      + 'that is what `a waist-up photograph` is for; do NOT write a close-up on her face, which '
      + 'renders a different act on every checkpoint (see the framing paragraph above).',
}

export const MANNERS = [...BASE_MANNERS, SELFIE]

export const MANNER = Object.fromEntries(MANNERS.map((m) => [m.key, m]))

export const BRIEF_AXES = {
  register: [
    'begins shy and closed and grows confident',
    'reads throughout as someone photographing herself alone, unhurried',
    'begins playful and teasing and turns direct',
    'reads as getting ready and being interrupted by her own reflection',
    'begins bored and idle and turns deliberate',
  ],
}

/** The shoot laid out before a word of it is written.
 *
 *  The writer used to derive the arc from the brief on every round, which meant
 *  deriving it seven times for a shoot of fifty and hoping the seven agreed.
 *  They did not: a run briefed to end explicit was bare by photograph seven and
 *  spent the remaining thirty-three standing in a mirror, because each round
 *  read `undressed` as the destination and paced towards it.
 *
 *  So the arc is decided once, in numbers, and every round is handed the slice
 *  of it that covers the photographs it is writing. The model still writes the
 *  plan — it is the thing that read the brief — but it writes it once. */
export const STAGE_PLAN_INSTRUCTION =
  'Lay out the stages of the photo session described above, before any of it is written.\n'
  + 'One line per stage, in order, each one starting with the photographs it covers, then a '
  + 'bar, then what happens in it:\n'
  + '1-8 | …\n9-14 | …\n'
  + 'The ranges are contiguous, start at photograph 1, and the last one ends on the last '
  + 'photograph of the shoot. No gaps, no overlaps, no photograph left out.\n'
  + 'A stage is a state of the whole photograph — what is worn AND what she is doing, at the '
  + 'same point on the same path. `topless, harness still on, kneeling and touching herself` '
  + 'is a stage. `undressing` is not: it is the name of a whole shoot.\n'
  + 'Six to twelve stages, whatever the shoot needs.\n'
  + '\n'
  + 'SPEND THE PHOTOGRAPHS WHERE THE SHOOT IS ABOUT SOMETHING. Roughly a fifth on the '
  + 'opening, a quarter getting there, a third on whatever the shoot is actually for, and '
  + 'the last tenth on how it ends. The opening and the ending are the SHORT stages.\n'
  + 'Read the brief for where it *ends*, not for the first state that satisfies it. If the '
  + 'shoot goes somewhere after the last garment, the undressing is the setup and belongs in '
  + 'the first half — getting the clothes off is never the subject of a shoot that was '
  + 'briefed to carry on past it.\n'
  + 'Only the wardrobe given below exists. Stages may have fewer of those pieces, or the '
  + 'same ones worn differently, and nothing else: running out of clothes is a reason for '
  + 'the stages to move on to what she is doing, never a reason to invent a garment.\n'
  + '\n'
  + 'WHEN THE SHOOT IS EXPLICIT, A STAGE IS AN ARRANGEMENT OF TWO BODIES, AND NO TWO STAGES '
  + 'SHARE ONE. With no clothes left to move, the arrangement is the only thing left that can '
  + 'change, so it is what the stages are made of: where each body is, which way each one '
  + 'faces, and what carries the weight of each. A stage that says only what is happening — '
  + '`he penetrates her` — is the name of the whole stretch and not a stage of it.\n'
  + 'Give that stretch MANY short stages rather than one long one: three or four photographs '
  + 'each, never eight. Eight photographs of one arrangement is one photograph shot eight '
  + 'times, and that is what a session reads as when it reads as monotonous. Measured on a '
  + 'twenty-four photograph brief: without this paragraph the plan spent the first twelve '
  + 'photographs on her alone and gave him three arrangements, one of them five photographs '
  + 'long.\n'
  + 'Do not choose from a list, and do not reach only for the arrangements a caption would '
  + 'name. Build each one: where the two of them are — on the bed, across it, at its edge, off '
  + 'it entirely — then where each body rests its weight, then how they face each other. Two '
  + 'bodies and a room make far more arrangements than the handful that have names, and a '
  + 'shoot that spends itself on the handful is the monotonous one.\n'
  + 'Write nothing but the lines.'

/** One photograph, written whole: what is worn, what the body is doing, and how
 *  she looks while doing it — in one line, from one call.
 *
 *  This is the shape that actually produced good sessions by hand, and the
 *  reason is structural rather than stylistic. Written as two streams — a
 *  wardrobe walking from dressed to undressed, and takes writing poses — the two
 *  get the same brief and never speak, so the clothes come off on schedule and
 *  the body keeps standing there. Measured, a forty-frame shoot briefed to end
 *  explicit: the wardrobe reached bare by frame thirty and take forty was still
 *  `standing square to the mirror with her arms hanging loose`. Nothing was
 *  broken. The two halves simply had no way to agree.
 *
 *  In one line they cannot disagree. It also makes the leak impossible by
 *  construction: there is no second text left to name a garment this one has
 *  already put down.
 *
 *  What stays in code is the fixed block — the look, prepended by the app to
 *  every frame. Retyping it per line is how it was done by hand and it held for
 *  twenty-one frames, but a model copying sixty words forty times across chunk
 *  seams is a drift waiting to happen, and prepending it cannot drift at all. */
/** The fields a shoot line arrives in, in the order the app joins them.
 *
 *  Order matters and it is the measured one: the camera first, because a reader
 *  frames what it meets first, and the act right behind it, because a second body
 *  named late is a second body that does not render. `worn` sits after both
 *  bodies for the same reason - the garments are what a long line spends itself
 *  on, and everything that decides whether the photograph is the right photograph
 *  is already said by then. */
export const SHOOT_FIELDS =
  ['camera', 'act', 'her', 'him', 'worn', 'technique', 'face']

export const SHOOT_LINE_INSTRUCTION =
  'Write one photograph per object of a photo session, in the order they are shot. Each '
  + 'object is a whole photograph, and it arrives in seven fields rather than in one line: '
  + 'the app joins them back into the single line the photograph is painted from. '
  + 'Everything below describes how a photograph is written; the fields say where each '
  + 'part of it goes.\n'
  + 'THE SEVEN KEYS ARE `camera`, `act`, `her`, `him`, `worn`, `technique`, `face`, and '
  + 'EVERY OBJECT CARRIES ALL SEVEN. None is optional and none is left empty, with one '
  + 'exception: `him` is empty in a photograph with nobody else in it. A key you have '
  + 'nothing new to say about is still written - measured, a field added to this list '
  + 'without this paragraph arrived in 9 photographs of 25 and the other 16 simply left '
  + 'it out.\n'
  + PROSE + '\n'
  + '\n'
  + 'THE `camera` FIELD IS WHERE THE CAMERA IS, AND THEN ITS FRAMING, IN THAT ORDER: '
  + '`Taken from directly behind her, a full-length photograph, head to feet`, `Taken from '
  + 'her right side, her body in full profile, a waist-up photograph`. It is the first '
  + 'field and it is joined ahead of every other one, so it is what the reader meets '
  + 'first, and the camera comes before the framing inside it: measured on a seventy-'
  + 'photograph run with the camera named after the framing, fifty-three lines asked for '
  + 'something other than a front view and about ten photographs came back as one.\n'
  + 'The framing is one of three: `a full-length photograph, head to feet`, `a three-quarter '
  + 'photograph from the knees up`, `a waist-up photograph`. Those three and no others. The '
  + 'plainest words there are, never implied and never '
  + 'clever — `thigh-to-hair framing` is a crop at the thigh whatever it was meant to say. '
  + 'A close-up is not on the list, and not merely because it comes back as a waist-up shot '
  + 'anyway: a framing written as a close-up ON HER FACE renders a different act. Measured '
  + 'on all nine checkpoints, thirty-two seeds of thirty-two - oral sex, with nothing else '
  + 'the line asked for in the picture. A close framing on the face is already a scene to '
  + 'this sampler and it arrives first.\n'
  + 'Change it from line to line and spread the four across the shoot, with at least one line '
  + 'in four full-length, head to feet. Measured on this project: forty-five lines written '
  + 'with no framing in any of them came back as forty-five mid-shots, four of which happened '
  + 'to be full length — a shoot read in order was then one photograph taken forty-five '
  + 'times, whatever its clothes were doing.\n'
  + 'The framing says how much of her is in frame. It never says how much of her to write: every '
  + 'line still walks the whole body, close-ups included, because the state of the clothes is '
  + 'what the next photograph copies and a line that drops it to match a crop drops it for '
  + 'every line after it as well.\n'
  + 'Say each piece ONCE. Naming a garment twice, or re-listing what a field has already '
  + 'said, is where a hundred and sixty words go, and repetition is the only length worth '
  + 'cutting. Never buy room by shortening a garment: the words are what make it the same '
  + 'garment in the next photograph, and a line that trades `white open-weave fishnet '
  + 'stockings` for `the stockings` is a line whose stockings come back black.\n'
  + 'A line is the photograph and nothing else. Never number it, never label it, never open '
  + 'with `Photograph 12.` or `Frame 12:` — the line is queued exactly as written, so a '
  + 'number at the front of it is a number in the prompt.\n'
  + '\n'
  + 'EVERY LINE WALKS THE WHOLE BODY: the chest and torso, then the hips and legs, then the '
  + 'feet. Every one of the three, in every single line, whether there is a garment there or '
  + 'not — and when there is not, the skin is what you write: `her chest bare`, `bare from '
  + 'the waist down`, `nude but for the boots`.\n'
  + 'This is the rule that most needs saying, because it fails silently and it fails late. A '
  + 'line that lists a choker, stockings and boots has said nothing whatsoever about the '
  + 'torso — and an unstated torso is not a bare torso, it is a torso the reader dresses '
  + 'for you. Measured, on a real run: the lines stopped saying `bare chest` at photograph '
  + 'twenty-four, and from there the shoot came back in a black nightgown nobody had '
  + 'written. Nine frames lost to three missing words.\n'
  + 'Worse, it does not recover. Copying the line before you word for word is what holds a '
  + 'wardrobe together, and it is also what makes a dropped body part permanent: once one '
  + 'line forgets the chest, every line after it inherits the silence. So check the three '
  + 'against the line you were given, and put back anything it has lost.\n'
  + '\n'
  + 'THE SHOOT MOVES IN STAGES, and the stages are given to you: each round is told which '
  + 'ones cover the photographs it is writing. Follow them. A stage is a state of the whole '
  + 'photograph, not of the clothes alone — what is worn AND what she is doing are at the '
  + 'same point on the same path, and they move together. A stage where a garment comes off '
  + 'but the body goes on standing exactly as before is a stage that did not happen.\n'
  + 'Do not re-plan the shoot. Do not reach the end of your stage early and mark time, and '
  + 'do not run past it into the next one: the photographs you were given are the length of '
  + 'the stretch you were given.\n'
  + '\n'
  + 'WITHIN A STAGE, NOTHING REPEATS. Consecutive photographs at the same stage differ by '
  + 'the pose, by what the hands are doing, and by the expression. Four photographs of '
  + 'one stage are four photographs, not one with three '
  + 'copies — and the wardrobe is what holds still between them, word for word.'
  + 'A PHOTOGRAPH HAS NO MOTION IN IT, so how something moves is never what tells two '
  + 'photographs apart. `his hips rocking forward in a steady rhythm` and `his hips '
  + 'snapping forward in short thrusts` are the same picture, and so are `slowly`, '
  + '`faster`, `still`, `now` and `beginning to`. Measured on two real sessions: the last '
  + 'ten photographs of both were two arrangements shot ten times, and the only words '
  + 'that changed between consecutive lines were those. What tells two frames apart is '
  + 'what the frame can show — where the camera stands, which way each body faces, what '
  + 'carries the weight, where each hand is, what touches what, and the face.\n'
  + '\n'
  + 'WHEN THE SHOOT IS EXPLICIT, THE LINE IS EXPLICIT. A brief that ends in sex ends in a '
  + 'photograph of sex, and the line says so in plain anatomical words: what is inside her, '
  + 'whose hands are where, which way each body faces. `moving against her in a steady rhythm` '
  + 'is the failure to avoid — it reads as a pose and it is painted as one. Measured, thirty '
  + 'frames briefed to end in penetration: the lines that hedged like that came back with her '
  + 'standing alone, and the same photographs written plainly came back with both bodies and '
  + 'the act, six times out of six.\n'
  + 'This is what one of those lines looks like: `A waist-up photograph taken from their side, '
  + 'of a naked man standing behind her and penetrating her from behind, his penis inside her, '
  + 'his hands on her hips, she is bent forward over the counter with both hands flat on it, '
  + 'naked but for the stockings, two people in frame, her mouth open and her eyes shut.` '
  + 'Everything softer than that is a pose: `pressed against her`, `pressed up into her`, '
  + '`moving against her`, `straddling his lap`, `joined with him` — every one of those is '
  + 'painted as two people sitting or standing near each other, which is exactly what they '
  + 'say.\n'
  + 'In an explicit stage the other body is bare too, unless the shoot says otherwise. A pair '
  + 'of denim-clad thighs under her in the photograph the brief said ends in penetration is a '
  + 'photograph of something else, and it is what the writing reaches for when it is trying '
  + 'not to say the thing.\n'
  + 'AN UNDRESSED LINE IS SHORT, AND THIS IS THE RULE THAT CHANGED BACK. It said there was '
  + 'no word limit, on the strength of a twelve-photograph shoot that averaged 212 words and '
  + 'rendered two bodies in twelve frames of twelve. Both halves of that are still true, and '
  + 'they were the wrong question: the long lines render the right BODIES and they render '
  + 'them as a paid photograph. Measured 2026-08-21, three photographs shot twice on the same '
  + 'seed - the line as written at 276 to 342 words, and the same photograph at 87 to 89 - '
  + 'the short one came back looking like a phone snapshot in three of three, and the long '
  + 'one like a lit set in three of three. Nothing else differed.\n'
  + 'So an undressed line carries the photograph AND NOTHING ELSE, in this order: how the '
  + 'photograph was taken, where the camera is and its framing, then in one plain sentence '
  + 'who is doing what to whom - `a naked man kneeling between her thighs with his penis '
  + 'inside her, two people in frame, both nude` - and then her face. That is the whole '
  + 'photograph. It comes out short, and the shortness is a consequence and never a '
  + 'target: no number in this file has ever been obeyed, and the one that matters is not '
  + 'a count of words but whether any of them is there twice.\n'
  + 'What goes, and it is most of the length: the walk of the whole body when there are no '
  + 'clothes on it, the second inventory of his chest and shoulders and thighs, the room '
  + '(it is already in the prompt, above your line), and every clause that says again what '
  + 'the sentence before it said. Where clothes are still on, they stay word for word - a '
  + 'dressed line is longer than ninety words and that is correct, because the garments are '
  + 'what the next photograph copies.\n'
  + 'The photograph must still be physically possible: two bodies of ordinary proportions, '
  + 'each with its weight resting on something, every limb where that position actually puts '
  + 'it, and every part the camera is told to see genuinely in its view from where it '
  + 'stands.\n'
  + 'So in these lines: the camera, then the two of them and what they are doing, then only '
  + 'what is still ON her — and nothing else. No inventory of her bare parts, no garment lying '
  + 'on the floor, no accessory set aside. She is nude by then and `nude but for the '
  + 'stockings` is the whole wardrobe of that photograph.\n'
  + 'A second person is written as a body, never as somebody. Say `two people in frame`, then '
  + 'what his body is doing and where it is against hers. Never his face, his age or who he '
  + 'is: the photograph needs a body, and a described stranger competes with the character the '
  + 'rest of the prompt is painting.\n'
  + 'AND HE NEEDS ENOUGH WORDS TO EXIST: name two parts of his body the camera can see from '
  + 'where it stands - his chest, his shoulder, his thigh, his knee - chosen for that '
  + 'position, not the same two every line. The body a line DESCRIBES is the body that '
  + 'renders and the one left in pronouns is the one that vanishes: measured both ways, '
  + 'all-her lines gave her alone with his anatomy grafted on in 14 of 16, all-him lines '
  + 'gave a man alone in 7 of 8. Everything above this take already describes her.\n'
  + 'Nothing above is suspended for these lines but one thing, and it is the full-length '
  + 'quota. In a two-person frame on a bed `a full-length photograph, head to feet` does not '
  + 'come back: measured thirteen times without a single one — five lines of a real shoot and '
  + 'eight of a controlled pair — and the depth of the room does not buy it either, which is '
  + 'the one lever that buys it everywhere else. The two bodies fill the frame and the crop '
  + 'lands at the thigh whatever the line asked for. So in these lines the framing is `a '
  + 'waist-up photograph` or `a three-quarter photograph from the knees up`. That buys no '
  + 'length: the four words head-to-feet would have taken are not spent anywhere else, and '
  + 'the line still ends at eighty. The camera position is still named — '
  + 'a two-person frame needs it more than any other, because it is what decides whether the '
  + 'act is in view at all.\n'
  // Asking the writer here for a freely invented position — with plausibility
  // stated as where each body's weight rests — was tried and reverted on
  // 2026-08-17. Measured over three runs it did NOT buy variety (position-word
  // overlap stayed at 0.67-0.92, seven lines of eight still `on her hands and
  // knees`), the weight rule stuck in only 4 of 9 lines, and the two-person count
  // fell to the bottom of its historical range (1, 5, 3 against 3-14) — the same
  // signature as the reverted edit 2. The reason it fails HERE while the same
  // instruction produced ten distinct positions when asked on its own: this line
  // still carries the full wardrobe, ~38 words of it, and there is no budget left
  // to invent with. Variety needs the carry OUT of the line, which needs somewhere
  // else for it to come from. See idevgen-two-people-limit.

  + '\n'
  + GARMENT_CARRY + '\n'
  + '\n'
  + 'Never write the hair, the makeup, the room or the light: they are prepended to every '
  + 'line of this shoot already, and writing them again either repeats or contradicts them. '
  + 'Never describe her face, her age or her body beyond what the pose and the expression '
  + 'need. Never write a camera brand, a lens or a film stock.\n'
  + '\n'
  // SIX and not seven on purpose: `technique` is the seventh key and this skeleton is
  // the only thing keeping it switched off in `directed`, which defines the field
  // nowhere. Measured 2026-08-21, n=25: the key without a bullet of its own came back
  // as a lighting plan in 23 lines of 23, and with a bullet it changed no render in a
  // 12-seed A/B.
  //
  // The paragraph above that names SEVEN keys is NOT a stale contradiction to tidy
  // away, and the switch is the two of them together: the skeleton is what `directed`
  // obeys, the enumeration is what carries `technique` into `candid`. Measured
  // 2026-08-24 with scripts/measure_writer.mjs, text only, counting `^Technique:` in
  // the joined prompt — `clean_fields` drops an empty field before the block join, so
  // an absent heading is an unwritten one. Correcting the header to SIX and dropping
  // `technique` from it moved `directed` by nothing (0 of 25 on two runs a side, both
  // arms) and took `candid` from 109 of 125 to 8 of 125, five runs a side. So
  // MANNER.candid.line, which spends a paragraph saying the field is where the capture
  // quality goes, delivers 6% on its own: a rule that names a field still needs
  // something else to ASK for that field.
  //
  // And arriving by enumeration is not the same as arriving. Ten runs a side, candid:
  // this six-key skeleton delivers `technique` 193 of 250, and it is wildly unstable
  // run to run — 25, 17, 17, 25, 25, 17, 16, 9, 17, 25. Adding `technique` to THIS
  // skeleton with a bullet of its own takes it to 250 of 250, every run 25, while the
  // five keys already here stay put (`her` 242 of 250 against 249, `face` 248 against
  // 237). A key in the skeleton does not miss; a key carried only by the enumeration
  // misses a quarter of the time.
  //
  // Which is why the seventh key cannot simply be added here: the same bullet run
  // against `directed` fills the field 48 of 48 with a lighting plan, repeated word for
  // word down the run — `Available light from the window, no flash, a still quiet
  // frame` on line after line. That is the 2026-08-21 failure above, reproduced. The
  // field is candid's, and a skeleton that asks for it has to ask only there.
  + 'THE SIX FIELDS. Answer as JSON: `{"photographs": [{"camera": "…", "act": "…", '
  + '"her": "…", "him": "…", "worn": "…", "face": "…"}, …]}`, one object per photograph, '
  + 'in order. Every field is filled on every object, as prose, with no field name repeated '
  + 'inside a field. The app joins them in this order into the one line that is painted:\n'
  + '- `camera`: where the camera is, then the framing, in the words above.\n'
  + '- `act`: what the two of them are doing, in plain anatomical words, and where each body '
  + 'is against the other. With one person in frame, what her body is doing.\n'
  + '  The arrangement is BUILT, not chosen. Decide in this order and write the answer, never '
  + 'the reasoning: where each body is in the room and on the furniture; then WHERE EACH '
  + 'BODY CARRIES ITS WEIGHT — on her knees, on her shoulders, on one hip, on both feet, on '
  + 'his thighs, braced on an arm, held off the bed by his hands; then which way each faces '
  + 'and how they meet. Then what is happening, in plain anatomical words.\n'
  + '  The weight is the half that gets left out and it is the half that decides the '
  + 'photograph: a body whose weight rests nowhere comes back as two people standing and '
  + 'holding each other, because that is the one arrangement that needs no support at all. '
  + 'Measured, it was written into 1 line of 24 unasked and 24 of 24 asked for like this.\n'
  + '  Do not pick from the arrangements that have names. There are far more arrangements than '
  + 'there are names for them, and the named handful is what makes a session monotonous.\n'
  + '- `her`: her chest and torso, her hips and legs, her feet. All three, every time.\n'
  + '- `him`: HIS body, as fully as hers — his chest, his shoulders, his arms, his stomach, '
  + 'his hips, his thighs, his knees, whichever of them this camera can see from where it '
  + 'stands, chosen for that position rather than the same two every line. He is a body and '
  + 'never a person: no face, no age, no who he is. Empty when she is alone in the frame, and '
  + 'never empty when she is not.\n'
  + '- `worn`: what is still on her, word for word from the photograph before. Every garment '
  + 'lives here. Nude is written here too: `nude but for the white fishnet stockings`.\n'
  + '- `face`: her expression — but FIRST decide whether this camera can see her face at all. '
  + 'If `camera` puts the lens behind her — `directly behind her`, `behind her left shoulder`, '
  + '`behind her right shoulder`, a rear camera of any kind — then her face is NOT in this '
  + 'photograph and `face` is the back of her head, unless `act` has already turned her head '
  + 'back over her shoulder. Measured: the abstract form of this rule (`nothing the camera '
  + 'cannot see`) was ignored in seven behind-the-camera lines of thirteen, and the version '
  + 'above missed once in sixteen. A photograph that asks for a face its own camera cannot '
  + 'see is resolved against the position: the camera moves rather than the face, and the '
  + 'photograph comes back as a different position entirely.'

/** The standing version of the explicit rule, for a shoot that is explicit all
 *  the way through.
 *
 *  The rule inside SHOOT_LINE_INSTRUCTION is conditional — *when* the shoot is
 *  explicit — and a conditional is what a model half-answers: it hedged the act
 *  into a pose (`arched into him`, `sitting astride him`, `joined with him`) in
 *  twelve lines of twelve. Borrowed from a chat agent of the user's that stopped
 *  doing the same thing, and what made that one work was not stronger words but
 *  three shapes: the rule stands rather than triggers, it bans the manoeuvre
 *  instead of listing euphemisms, and it names the vocabulary. */
/** Does this stretch of the shoot reach the act?
 *
 *  A `couple` shoot — dressed at photograph 1, penetration at the last — is
 *  explicit for its final third and clothed before it, so the register cannot
 *  ride on the session the way it does for a shoot that is explicit throughout.
 *  It has to ride on the CHUNK, and what says so is the stage plan covering that
 *  chunk.
 *
 *  Found 2026-08-20 in session 196, written by the shipped writer: `bare` was the
 *  only gate, `bare` is only true for reach `explicit`, so a dressed-to-
 *  penetration session got neither the register nor the explicit system message
 *  in any round of it. Its last three photographs came back `his bare hips
 *  pressed flush against her bare hips` — no penis, no penetration, no two people
 *  in frame — which is the pose-instead-of-the-act failure with nothing in place
 *  to stop it, and it is what a session of this kind looks like when the explicit
 *  frames read as two people sitting near each other. */
export const reachesTheAct = (stages) =>
  (stages || []).some((s) => /penetrat|penis|inside her|fuck|\bexplicit\b|his cock|entering her/i
    .test(s?.what || ''))

/** The register for the stretch of a shoot that becomes explicit, where the
 *  standing version would be a lie: this shoot did NOT open explicit, and a rule
 *  that says it did contradicts the stages the same writer was just given. */
export const EXPLICIT_STRETCH =
  'THE PHOTOGRAPHS OF THIS ROUND ARE THE EXPLICIT STRETCH OF THE SHOOT, AND EVERY LINE THAT '
  + 'REACHES IT IS WRITTEN EXPLICITLY. The stages above say where it starts; from there on, '
  + 'nothing is softened, implied or left to be inferred.\n'
  + 'Never answer with a pose where the act belongs. `arched into him`, `her hips tilted up to '
  + 'meet his`, `his hips pressed flush against hers`, `moving against her` are all the same '
  + 'evasion wearing different words, and each of them is painted as two people sitting near '
  + 'each other, which is exactly what they say.\n'
  + 'Say what is happening in plain anatomical words: penis, vagina, inside her, penetrating '
  + 'her, his hips against hers. Say which body part, and say where it is.\n'
  + 'And he is bare by then unless the shoot says otherwise. A pair of denim-clad thighs in '
  + 'the photograph the brief said ends in penetration is a photograph of something else.'

export const EXPLICIT_REGISTER =
  'THIS SHOOT IS EXPLICIT FROM THE FIRST PHOTOGRAPH TO THE LAST, AND EVERY LINE OF IT IS '
  + 'WRITTEN EXPLICITLY. Not the ones you judge to be explicit: all of them.\n'
  + 'Never soften, never imply, never leave it to be inferred, and never answer with a pose '
  + 'where the act belongs. `arched into him`, `sitting astride him`, `joined with him`, '
  + '`moving against him` are all the same evasion wearing different words — the failure is '
  + 'the manoeuvre, not the phrase, so no list of banned phrases will catch the next one.\n'
  + 'Say what is happening in plain anatomical words: penis, vagina, inside her, penetrating '
  + 'her, his hips against hers, her mouth on him. Say which body part, and say where it is.\n'
  // Which of the two bodies gets introduced is NOT stated here any more. It was,
  // at length and with its measurement attached, and it was ignored anyway:
  // nineteen lines of twenty opened `a naked man and a naked woman`, and the one
  // the repair could not fix came back as a photograph of two women. It now rides
  // on the system message with the rest of the register — see EXPLICIT_SYSTEM in
  // `backend/enhance.py`. Do not restate it here: two texts saying the same thing
  // is one text away from two texts disagreeing, and which one wins is a toss.
  + 'His body is written as fully as hers, in a field of its own, and it is never left in '
  + 'pronouns: the body a line describes is the body that renders.'

/** A shoot of forty photographs is written a handful at a time, and this is what
 *  every call is told about where it sits.
 *
 *  Measured, asking for forty in one go: thirty-two takes came back and they were
 *  stubs — `Close-up, hands clasped low.` against an instruction that asks for a
 *  fifteen-to-thirty word caption — and nineteen wardrobes that spent the whole
 *  arc by the nineteenth. A long ask is not answered long; it is answered
 *  shorter, and the middle of the shoot is what gets dropped. */
const paced = (what, { from, want, total }) =>
  `You are writing ${what} ${from} to ${from + want - 1} of ${total}, in order.\n`
  + (from > 1
    ? `The first ${from - 1} are already written; roughly `
      + `${Math.round(((from - 1) / total) * 100)} per cent of the shoot is behind you.\n`
    : 'This is the beginning of the shoot.\n')
  + `Pace it against all ${total}, not against the ${want} lines in front of you: the shoot `
  + `ends at ${total} and nowhere earlier.`

export const takesChunkNote = (at) =>
  `${paced('photographs', at)}\n`
  + (at.previous ? `Photograph ${at.from - 1} was:\n${at.previous}\n` : '')
  + `Write ${at.want} lines: photograph ${at.from}, then ${at.from + 1}, and so on.`

/** A wardrobe has as many states as it has pieces, never as many as the shoot has
 *  photographs — so these lines are the points at which the clothes *change*, and
 *  each one is held for a stretch of the shoot.
 *
 *  Asked for one per photograph instead, over forty: the assistant reached bare
 *  by fifteen, repeated the same line while it had nothing left to remove — and
 *  then invented a whole new outfit to keep undressing, a schoolgirl uniform
 *  halfway through a session that began in a football jersey. */
/** The camera positions this sampler was measured to obey, and nothing else.
 *
 *  The five at her eye level are the ones SHOOT_LINE_INSTRUCTION has always
 *  called reliable. The three off eye level are the survivors of sessions 227
 *  and 228, where the `camera` field was swapped by hand on one fixed line: only
 *  *above* and *from the floor* are concepts this sampler has, the height has to
 *  be the head of the phrase, and a place in the phrase eats the height —
 *  `High camera looking down from the corner of the room` came back level.
 *
 *  The right shoulder is session 244's one adoption, on the same protocol: nine
 *  passes of the blind judge over three seeds, unanimous on where the camera
 *  stood AND on which way it faced her. A left/right mirror is a different
 *  photograph rather than a different wording, and it is the cheapest kind of
 *  entry there is — the sampler already had the concept, nobody had asked.
 *
 *  WHY THERE IS NO NINTH FAMILY, and session 244 is where five candidates died:
 *
 *  A horizontal hung off a verified height is ignored, 0 of 9 photographs, and
 *  on the floor it destroys the height it was hung on. `Low-angle shot from the
 *  floor at her feet` alone is obeyed 3/3; the same head word with ANY tail —
 *  `behind her`, `in front of her` — falls to 1/3 and comes back at eye level.
 *  Overhead survives the weight (3/3) but disobeys the tail just the same. So
 *  228's law goes one further: the vertical must be the head of the phrase and
 *  it carries no passengers. A height is a whole position, not a prefix.
 *
 *  And a front three-quarter was not added because the catalogue already has
 *  one. `Taken from her right front, her body turned three-quarters toward the
 *  camera` moved the torso off square by a little and the judge read it `facing`
 *  in 7 passes of 9, against 9 of 9 for the frontal control — a two-vote
 *  difference is not a measurement. Meanwhile the two `side` entries below
 *  render a three-quarter turn on Krea 2 every time, because a ninety-degree
 *  profile is a concept that base does not have (see
 *  [[idevgen-profile-is-a-base-model-limit]]). The family exists; it is spelled
 *  `side` and labelled for what it ASKS, not for what it renders. A third way of
 *  asking for the same photograph is not a ninth position.
 *
 *  The bed-anchored forms that also render (`Overhead camera directly above the
 *  bed`, `Side-angle camera at mattress level`, …) are deliberately not here:
 *  they name furniture, so they are wrong in any shoot that is not on a bed.
 *  ponytail: no room detection, the furniture-free nine work everywhere.
 */
export const CAMERA_POSITIONS = [
  { family: 'front', line: 'Taken from directly in front of her' },
  { family: 'shoulder', line: 'Taken from behind her left shoulder, her back three-quarters to the camera' },
  { family: 'shoulder', line: 'Taken from behind her right shoulder, her back three-quarters to the camera' },
  { family: 'side', line: 'Taken from her right side, her body in full profile' },
  { family: 'side', line: 'Taken from her left side, her body in full profile' },
  { family: 'behind', line: 'Taken from directly behind her' },
  { family: 'overhead', line: 'Overhead camera directly above her' },
  { family: 'overhead', line: 'High camera looking steeply down at her' },
  { family: 'floor', line: 'Low-angle shot from the floor at her feet' },
]

/** Where the PHONE was, for a shoot nobody is photographing.
 *
 *  `candid` cannot use the list above. Those are the positions of someone
 *  standing behind a camera; these are the places a phone ends up. Measured in
 *  renders 2026-08-23, sessions 245-250: nine arms and then five more, one line
 *  fixed by hand with only the camera clause swapped, three shared seeds, judged
 *  blind three passes a photograph. Only what survived that is here.
 *
 *  What it costs to be on this list, and what it cost to find out:
 *
 *  * `behind` is NOT here. `Taken from directly behind her` and both phone
 *    wordings of it came back frontal 0/6 under the candid look, with the
 *    subject block already fixed. `floor` is not here either - 0/3 every way it
 *    was asked, including the form that is 3/3 for `directed`.
 *  * a MOUNT reaches a height and never a horizontal. `Phone propped on a high
 *    shelf ... looking down at her` is 3/3 overhead with no verified height word
 *    in it at all, which the directed catalogue's grammar says should not work;
 *    hang a horizontal on the same shape - `... behind her left shoulder` - and
 *    it is 0/3. So the shoulder is asked for in the photographer's words, which
 *    is the one place candid borrows them, and it renders 3/3.
 *  * the word `phone` in the clause paints NO phone: 21/21 of the arms that
 *    should show no device showed none, the three that open with `Phone`
 *    included. The mirror is the exception and the only intended one - it is the
 *    single form that renders the device, 3/3, because there it is really in
 *    frame.
 *  * `Phone held above her in his hand` is 3/3 overhead and is deliberately NOT
 *    here: it puts a second person in a shoot that may not have one.
 *    ponytail: no `him` check, the shelf reaches the same overhead alone.
 */
export const CANDID_POSITIONS = [
  { family: 'front', line: 'Taken from directly in front of her' },
  { family: 'front', line: "Phone held out at arm's length in front of her face" },
  { family: 'overhead', line: 'Overhead camera directly above her' },
  { family: 'overhead', line: 'Phone propped on a high shelf across the room, looking down at her' },
  // Both sides, session 251: 3/3 each, shot together so the left is its own
  // control and it reproduced. What the judge verifies is a shoulder
  // three-quarter view and not WHICH shoulder - left and right are one answer to
  // it on purpose, because telling her left from her right is a harder question
  // than the one being asked. The directed catalogue carries both on the same
  // evidence.
  { family: 'shoulder', line: 'Taken from behind her left shoulder, her back three-quarters to the camera' },
  { family: 'shoulder', line: 'Taken from behind her right shoulder, her back three-quarters to the camera' },
  { family: 'mirror', line: 'Mirror selfie, the phone up in her right hand and visible in the mirror' },
]

/** The catalogue a manner plans from.
 *
 *  Down here because it names both lists and they are defined above it. Every
 *  manner is in it, which is why the list of example forms `SHOOT_LINE_INSTRUCTION`
 *  used to carry - `CAMERA_FORMS`, forty lines of measured wording - is gone: it
 *  existed for a writer choosing its own position, and no writer does now. A
 *  manner added without a catalogue gets no camera guidance at all, so
 *  `tests/test_camera_plan.py` fails until it has one; git has the old list if
 *  free-writing ever comes back.
 */
/** Where the phone was when she was holding it through the act.
 *
 *  `candid`'s seven, unchanged - they are the measured ones and a shoot she is
 *  filming is still a shoot in a room - plus the two forms sessions 155 and 161
 *  are full of and the candid catalogue has no way to ask for: the phone pointed
 *  back down her own body, and the phone held above her face while she is on her
 *  back. Both are held in her own hand, which is the shape `candid` measured as
 *  paintless (the word `phone` in the clause put a phone in 0 of 21) and as
 *  reaching a height without a verified height word.
 *
 *  Their own family, `pov`, so the spread cannot run two of them together and
 *  cannot fill a shoot with them either: they are the strongest lines in the
 *  catalogue and a shoot of nothing else is one photograph taken forty times.
 *
 *  ponytail: no `him` gate. These render alone as a woman photographing herself,
 *  which is a shoot somebody wants; what puts him in the picture is the reach.
 */
export const SELFIE_POSITIONS = [
  ...CANDID_POSITIONS,
  { family: 'pov', line: 'Phone held low in her own hand at her chest, angled down along her own body' },
  { family: 'pov', line: 'Phone held above her face in her own outstretched hand as she lies on her back, looking down at her' },
]

export const POSITIONS = { directed: CAMERA_POSITIONS, candid: CANDID_POSITIONS,
                           selfie: SELFIE_POSITIONS }

/** Where the camera stands in each of `n` photographs, decided here and not by
 *  the writer, the way `stagePlan` decides the arc.
 *
 *  Measured 2026-08-22, three arms of n=25 x 5 runs: the writer takes 19-20 of
 *  25 camera fields verbatim from the five examples, and no wording of the
 *  instruction moves that. Deleting the examples does move it — verbatim reuse
 *  falls to about 1 — but the shoot does not change: classified by which side of
 *  her the camera stands on, the free-writing arms had the same 5.8 position
 *  families and the same biggest family as the control. What it did lose was the
 *  field order and every verified form, inventing `at her hip height` and
 *  `at mattress level`, which come back at eye level. So the choice is made here,
 *  from the eight forms above, and the writer only has to word the framing.
 *
 *  Each step takes the least-used position whose family is not the one just
 *  used, ties broken at random. That spreads the eight evenly without any quota
 *  arithmetic and no two photographs running share a family.
 */
export const cameraPlan = (n, rand = Math.random, positions = CAMERA_POSITIONS) => {
  const used = positions.map(() => 0)
  const plan = []
  let last = null
  for (let i = 0; i < n; i += 1) {
    // A single family cannot fill the ban, so there is always something left.
    const open = positions
      .map((p, at) => ({ p, at }))
      .filter(({ p }) => p.family !== last)
    const fewest = Math.min(...open.map(({ at }) => used[at]))
    const pick = open.filter(({ at }) => used[at] === fewest)
    const { p, at } = pick[Math.floor(rand() * pick.length) % pick.length]
    used[at] += 1
    last = p.family
    plan.push(p.line)
  }
  return plan
}

/** The camera plan with the planted photographs given a camera their arrangement
 *  can be seen from.
 *
 *  Only those photographs move. Everything else keeps the spread `cameraPlan`
 *  drew, which is what stops this from quietly becoming a second camera plan:
 *  measured in session 267, an arrangement handed an incompatible camera loses,
 *  every time, and the shoot gets a photograph nobody asked for.
 *
 *  The replacement is drawn from the positions whose family the arrangement
 *  allows, preferring one whose family is not already on the photograph before
 *  or after - the same rule the plan itself holds. When the catalogue has none
 *  it can use, the camera is left alone: a manner with no compatible position is
 *  a manner that cannot take that photograph, and a wrong camera is still better
 *  than an empty one.
 */
export const fitCameras = (cameras, poses, positions, rand = Math.random) => {
  if (!cameras || !positions) return cameras
  const familyOf = (line) => positions.find((p) => p.line === line)?.family
  const out = [...cameras]
  for (const [key, arrangement] of Object.entries(poses || {})) {
    const at = Number(key) - 1
    if (!arrangement.cameras) continue
    // The FIRST family the catalogue offers, not any of them: the lists are
    // ordered by what each one scored when it was shot, and `wall` is 3/3 from
    // a mirror and 0/3 from behind her shoulder. Drawing at random spends a
    // third of the plantings on a form that was measured to fail.
    const family = arrangement.cameras.find((f) => positions.some((p) => p.family === f))
    const open = positions.filter((p) => p.family === family)
    // Nothing this manner can take it from: the camera it was dealt stays, which
    // is wrong, and a wrong camera is still better than none.
    if (!open.length) continue
    if (familyOf(out[at]) === family) continue
    // Inside the family the draw is free - which of its forms is a question the
    // camera catalogue already answered.
    out[at] = open[Math.floor(rand() * open.length) % open.length].line
  }
  return out
}

/** The kiss frame, in four flavours, and the position it is taken from.
 *
 *  Every shoot gets at least one. It came from a photograph the user chased for
 *  several sessions and never got out of a shoot - a kiss blown at the camera
 *  with the eyes shut - and finally got from a prompt written by hand. What that
 *  prompt was doing that a shoot line does not: it named the kiss and the eyes as
 *  ONE gesture in the same clause, with the eyes stated flatly and in capitals,
 *  and it put the camera at arm's length in her own hand. A shoot line spreads
 *  those across `camera` and `face` and softens both, and the eyes come back
 *  open.
 *
 *  So it is a plan and not a suggestion, like the camera: the wording below is
 *  handed over word for word and the writer only places it.
 *
 *  `hand` is what the `act` field must carry when the flavour needs a hand in
 *  frame; it is empty for the three that do not.
 */
export const KISS_FRAMES = [
  { key: 'closed',
    face: 'Her lips are pushed forward in a kiss blown at the camera, her head tilted playfully '
        + 'to one side, and HER EYES ARE COMPLETELY CLOSED - both eyelids shut, no iris and no '
        + 'white showing, a peaceful dreamy expression.',
    hand: '' },
  { key: 'wink',
    face: 'Her lips are pushed forward in a kiss blown at the camera, her head tilted playfully '
        + 'to one side, and SHE IS WINKING - one eye squeezed fully shut, the other open and '
        + 'looking straight at the lens.',
    hand: '' },
  { key: 'open',
    face: 'Her lips are pushed forward in a kiss blown at the camera, her head tilted playfully '
        + 'to one side, both eyes open and looking straight at the lens.',
    hand: '' },
  { key: 'finger',
    face: 'Her lips are pushed forward in a kiss blown at the camera, her head tilted playfully '
        + 'to one side, and SHE IS WINKING - one eye squeezed fully shut, the other open and '
        + 'looking straight at the lens.',
    hand: 'Her free hand is raised beside her face with the middle finger up and the other '
        + 'fingers curled down, held toward the camera.' },
]

/** Which photographs are kiss frames, and which flavour each one is.
 *
 *  One per eight photographs, capped at the four flavours and never fewer than
 *  one, so a short shoot still gets its kiss and a long one gets variety rather
 *  than the same face four times. Spread by dividing the shoot into as many
 *  bands as there are frames and placing one inside each, which keeps two of
 *  them from landing side by side without any interval arithmetic.
 *  ponytail: no per-manner count, one knob (the 8) if a shoot wants more.
 */
export const spreadOver = (n, items, per, rand = Math.random, cap = items?.length ?? 0) => {
  if (n < 1 || !items?.length) return {}
  // `cap` is what stops a long shoot planting more than there is to plant. The
  // kiss frame has four flavours and a fifth kiss would be a repeated face, so
  // it caps at its own length; the arrangements cycle instead, because the same
  // arrangement at photograph 3 and photograph 30 is two different photographs
  // of a shoot that moved. Without the distinction a 45-photograph shoot with
  // three arrangements picked planted three of them - one in fifteen, which is
  // not a pool, it is a garnish.
  const many = Math.max(1, Math.min(cap, Math.floor(n / per)))
  const band = n / many
  const plan = {}
  let last = 0
  for (let i = 0; i < many; i += 1) {
    // 1-based photograph numbers, and photograph 1 is left alone when there is
    // room: it is the frame the whole shoot is measured against.
    const from = Math.floor(i * band)
    const drawn = Math.max(many > 1 || n === 1 ? 1 : 2,
                           from + 1 + Math.floor(rand() * Math.max(1, band - 1)))
    // A band's draw can land at its end and the next one's at its start, which
    // is two planted frames running - the one thing the spread exists to prevent.
    const at = Math.min(n, Math.max(drawn, last + 2))
    plan[at] = items[i % items.length]
    last = at
  }
  return plan
}

export const kissPlan = (n, rand = Math.random) => spreadOver(n, KISS_FRAMES, 8, rand)

/** The arrangements of two bodies that sessions 155 and 161 are made of.
 *
 *  Those two shoots are where the `selfie` manner came from, and copying the
 *  camera out of them left the other half behind: what the two of them are
 *  DOING. The stage plan invents its own arrangements, which is right for a
 *  shoot nobody has a picture of in their head and wrong when there is a
 *  particular photograph being chased.
 *
 *  So they are a pool and not a rule, and NOT one per photograph: nothing is
 *  planted unless a session picks it, and a picked one lands about once in five
 *  photographs, spread by `spreadOver` the way the kiss frame is. Everything
 *  between them is the shoot the stage plan wrote, which is the half that keeps
 *  a session from being one photograph shot forty times.
 *
 *  WHAT THE WORDING IS AND IS NOT. Each `act` below is handed to the writer word
 *  for word, and it names two people plainly because that is the only form this
 *  project has ever measured as rendering the act
 *  ([[idevgen-two-people-limit]]). It says where the two bodies are and nothing
 *  else: no camera, no framing, no expression, no clothes. Those are the line's
 *  own, and the camera in particular is planned separately and must not be
 *  fought.
 *
 *  `cameras` IS WHICH FAMILIES WERE MEASURED TO RENDER IT, in the order they
 *  scored, and it exists because session 267 measured the two plans fighting. Three of its five planted arrangements were
 *  handed a camera behind her shoulder, and all three came back as a DIFFERENT
 *  arrangement: asked for her on top facing him with her back to the lens, the
 *  sampler turned her around on him rather than move the camera. The one that
 *  survived was the one whose camera already agreed with it. The camera outranks
 *  the bodies, which is this project's oldest hierarchy
 *  ([[idevgen-position-collapse-is-contradiction]]) arriving in a new place.
 *
 *  So a planted arrangement takes its camera from these families and the plan
 *  fills the rest of the shoot as before. Read them as what the camera can SEE:
 *  a woman with her front to a wall cannot be photographed from in front, and a
 *  phone propped above a bed cannot see two people standing at one.
 *
 *  Every list keeps at least one family in every catalogue - a restriction that
 *  empties a manner's positions would leave the photograph with no camera at
 *  all, and `tests/test_arrangements.py` fails if one ever does.
 *
 *  ALL THREE ARE SHOT AND JUDGED, sessions 269 and 270 on the arm protocol -
 *  one line fixed by hand, the `act` taken from here word for word, three shared
 *  seeds, the camera swapped through the families each one allowed, read blind
 *  three passes with `judge_camera.py --question arrangement`:
 *
 *  * `astride` 18 of 22 photographs, and every family it lists scored: front
 *    6/6, overhead 4/4, mirror 4/6, pov 4/6. It is also 12 of 12 in sessions 265
 *    and 266 on a different fixed line.
 *  * `reverse` renders from ONE family. Behind her shoulder is 3/3; the mirror
 *    and the overhead are 1/3 each, which is why they are gone from its list.
 *  * `wall` is the mirror, 3/3, and nothing else: from behind her shoulder it is
 *    0/3. The shoulder stays second on its list only as the fallback for a
 *    manner with no mirror in its catalogue - `directed` has none - and it is
 *    known to be weak there.
 *
 *  So a list here is not a guess about what a camera can see any more. It is
 *  what was shot, strongest first, and `fitCameras` takes the first one the
 *  manner's catalogue offers.
 *
 *  WHAT IS VERIFIED, and it is all three. `astride` is the arrangement
 *  sessions 265 and 266 shot on a fixed line: 12 photographs of 12 with the arm
 *  written, the act in 11 of them. The other five are read off what 155 and 161
 *  RENDERED rather than what their prompts asked for - and those two are not the
 *  same thing, which is the whole reason this catalogue is worded from the
 *  photographs, and the numbers above are what each one is worth.
 *
 *  Three more were written and taken out again: see below the list.
 */
export const ARRANGEMENTS = [
  { key: 'astride',
    label: 'She is on top, facing him',
    cameras: ['front', 'overhead', 'mirror', 'pov'],
    act: 'She is astride him with her knees on either side of his hips and her weight down on '
       + 'him, the two of them joined, two people in frame.' },
  { key: 'reverse',
    label: 'She is on top, facing away',
    cameras: ['shoulder'],
    act: 'She is astride him facing away from him with her weight on her feet, the two of them '
       + 'joined, two people in frame.' },
  { key: 'wall',
    label: 'Standing against the wall',
    cameras: ['mirror', 'shoulder'],
    act: 'She is standing with her front to the wall and one leg raised, he is behind her, the '
       + 'two of them joined, two people in frame.' },
]

/** WHY THERE ARE THREE AND NOT SIX.
 *
 *  `back` - her on her back with him over her - and `side` - both of them on
 *  their sides, him behind her - are the two that were shot hardest and never
 *  arrived. Sessions 269 and 271, the same fixed line and the same three seeds
 *  on TWO checkpoints, four cameras for `back` and three for `side`:
 *
 *      back   0 of 12 on finepornV4, 0 of 12 on the Krea 2 mix
 *      side   0 of  9 on finepornV4, 0 of  8 on the Krea 2 mix
 *      astride (control, same runs)  18 of 22 and 9 of 12
 *
 *  It is not the checkpoint and it is not a missing act: the same Krea 2 run
 *  reads `sex` in 29 photographs of 33 on `--question act`. Both collapse into
 *  the same photograph, her upright on top facing the lens, which is this
 *  sampler's default arrangement for two bodies - and an arrangement that
 *  reliably delivers a different photograph is worse than no option at all.
 *
 *  The entries, so nobody writes them a second time:
 *
 *      { key: 'back', label: 'She is on her back, he is over her',
 *        act: 'She is on her back with her legs open and he is over her between
 *              them, the two of them joined, two people in frame.' }
 *      { key: 'side', label: 'Both on their sides',
 *        act: 'They are both on their sides with him behind her and her upper leg
 *              lifted, the two of them joined, two people in frame.' }
 *
 *  What has NOT been tried on either of them is a different wording of the same
 *  arrangement - both were shot in exactly one form. The camera is exhausted;
 *  the sentence is not.
 *
 *  AND THE THIRD, the arrangement everyone asks for.
 *
 *  `behind` - on all fours, him kneeling behind her - failed in every context
 *  there is, on four separate occasions:
 *
 *  * sessions 155 and 161 asked for it in eight of their forty-five hand-written
 *    prompts and NEITHER shoot ever painted it. Both came back as her on top or
 *    on her back;
 *  * planted in session 267, the writer dropped it from the line entirely;
 *  * planted again in 268 with a camera fitted to it and the wording arriving
 *    word for word, the photograph came back as the two of them kneeling face to
 *    face;
 *  * and the camera side of the same shape is just as dead - `Taken from
 *    directly behind her` is 0/6 under the candid look, both wordings
 *    ([[idevgen-candid-camera-renders]]).
 *
 *  An option that reliably delivers a different photograph is worse than no
 *  option: it spends a planted frame and it lies about what the shoot will be.
 *  The wording is kept here rather than in git alone because the next person to
 *  want it will write exactly this entry again.
 *
 *  What is NOT established is why. It may be the base model, and finepornV4 is
 *  the only one it has been asked of - `judge_camera.py --question arrangement`
 *  on a fixed line across the nine checkpoints is the arm that would say.
 *
 *  The entry, so the next person to want it recognises what was already tried
 *  rather than writing it again:
 *
 *      { key: 'behind', label: 'On all fours, he is behind her',
 *        cameras: ['front', 'shoulder', 'side', 'overhead', 'mirror'],
 *        act: 'She is on all fours on the bed and he is kneeling behind her, the two of
 *              them joined, two people in frame.' }
 */

export const ARRANGEMENT = Object.fromEntries(ARRANGEMENTS.map((a) => [a.key, a]))

/** Which photographs carry a picked arrangement. Empty when none is picked, and
 *  that is the default: a session says which of them it wants.
 *
 *  One in five rather than the kiss frame's one in eight, because an arrangement
 *  is what the shoot is about where a kiss is a single frame - but still a
 *  minority of the photographs, which is the point of a pool.
 *  ponytail: no per-manner count, the 5 is the one knob.
 */
export const arrangementPlan = (n, picked, rand = Math.random) =>
  spreadOver(n, ARRANGEMENTS.filter((a) => picked?.includes(a.key)), 5, rand, Infinity)

/** Where the kiss frame is taken from, by manner: her own arm in a candid shoot,
 *  the photographer's frontal position in a directed one. Both are measured
 *  forms - the first is `CANDID_POSITIONS`' arm's-length selfie, 3/3 in session
 *  245, the second is the frontal control that is 3/3 in every session it has
 *  ever been in.
 */
export const KISS_CAMERA = {
  candid: "Phone held out at arm's length in front of her face",
  selfie: "Phone held out at arm's length in front of her face",
  directed: 'Taken from directly in front of her',
}

export const shootChunkNote = (at) =>
  `${paced('photographs', at)}\n`
  + (at.stages?.length
    ? 'The stages covering the photographs you are writing:\n'
      + at.stages.map((s) => `${s.from}-${s.to} | ${s.what}`).join('\n') + '\n'
    : '')
  // Last, so it is what the reader meets last. It was placed here to outrank the
  // example list SHOOT_LINE_INSTRUCTION used to carry; that list is gone now that
  // every manner plans, but last is still where the one thing the writer may not
  // touch belongs.
  + (at.cameras?.length
    ? 'WHERE THE CAMERA STANDS IS ALREADY DECIDED for each of these photographs, and it is '
      + 'the one thing in the line that is not yours:\n'
      // The arrangement rides on the camera's own row rather than in a list of
      // its own. Measured 2026-08-23: handed as a second numbered list, it
      // landed on the photograph BEFORE the one it was given to in five rows of
      // twelve - the camera rows are copied in order and never miscounted, and
      // a number the writer has to match against them is a number it gets
      // wrong. One row per photograph, everything that photograph was given on
      // it.
      + at.cameras.map((c, i) => {
        const pose = at.poses?.find((p) => p.at === at.from + i)
        return `${at.from + i} | ${c}${pose ? `\n${at.from + i} | act: ${pose.arrangement.act}` : ''}`
      }).join('\n') + '\n'
      + 'Open each line with the position given for its photograph, word for word, and then '
      + 'your framing after it. Invent no other position and reword none of these: these are '
      + 'the forms this camera was measured to obey, and a reworded one comes back as a '
      + 'front view. The framing, the pose, the act and the expression are still yours.\n'
    : '')
  // Before the kiss, which is the harder override: an arrangement says what the
  // two of them are doing and a kiss frame replaces the camera as well, so the
  // one that changes more of the line is read last.
  + (at.poses?.length
    ? 'SOME OF THE ROWS ABOVE CARRY AN `act:` LINE. That is what the two of them are doing in '
      + 'that photograph, it is decided, and like the camera on the row above it, it is not '
      + 'yours.\n'
      + 'The `act` field of that photograph OPENS with those words exactly as they are written '
      + 'above, and nothing of yours goes in front of them. Do not reword it, do not shorten it '
      + 'and do not write the same arrangement in your own words - measured, a softer version of '
      + 'this paragraph was obeyed in three photographs of six and the other three came back as '
      + 'a different arrangement altogether, which is a photograph nobody asked for. Anything '
      + 'you want to add about their hands or their weight goes AFTER it, in the same field.\n'
      + 'Everything else in the line is yours and is written around it: the camera it was given, '
      + 'the framing, his body in `him`, her body in `her`, the face. The photographs with no '
      + 'arrangement named are the shoot as usual - an arrangement is a frame the shoot passes '
      + 'through, never the whole stretch.\n'
      + 'AND IT BELONGS TO THAT PHOTOGRAPH AND NO OTHER. Do not carry its words, or the same '
      + 'arrangement said differently, into the photograph after it: that one moves the shoot '
      + 'on, the way every other photograph does. Measured in session 268 - the writer repeated '
      + 'a planted arrangement into the next one and the one after, and those photographs were '
      + 'dealt their own cameras, so `on her back with him over her` came back as her riding him '
      + 'the other way round. The arrangement is fitted to the camera of ITS photograph and to '
      + 'no other, so a copy of it anywhere else is a photograph shot from a position that '
      + 'cannot see it.\n'
    : '')
  // After the camera, because it overrides one of the positions above for its own
  // photograph and a reader meets the exception after the rule.
  + (at.kisses?.length
    ? at.kisses.map(({ at: k, frame, camera }) =>
      `PHOTOGRAPH ${k} IS A KISS FRAME and it is written differently from every other line.\n`
      + `Its camera is \`${camera}\`, which replaces the position given for it above, and its `
      + 'framing is `a waist-up photograph` so the face fills the frame.\n'
      + `Its \`face\` field is this, word for word: ${frame.face}\n`
      + (frame.hand ? `Its \`act\` field carries this: ${frame.hand}\n` : '')
      + 'Do not soften it, do not reword the eyes and do not describe her looking away: the '
      + 'kiss and the eyes are one gesture and this photograph exists for it. Everything else '
      + 'in the line - the pose, the wardrobe, the technique - is the shoot as usual, and it '
      + 'is the next step of the shoot like any other photograph.\n').join('')
    : '')
  + (at.previous
    ? `Photograph ${at.from - 1} was:\n${at.previous}\n`
      + `Your first line is photograph ${at.from}: the next step of the shoot from that one, `
      + 'never a repeat of it, and every garment it did not change carried over from it word '
      + 'for word.\n'
      + 'Before you carry it over, check that line for the chest and torso, the hips and '
      + 'legs, and the feet. It is one line of a long shoot and it may have lost one of the '
      + 'three; carry over what it says and put back what it does not, because from here on '
      + 'the omission is yours and every line after you will inherit it.'
    : at.bare
      ? 'THERE IS NO WARDROBE IN THIS SHOOT AND NONE IS GIVEN TO YOU. She is undressed in '
        + 'photograph 1 and in every photograph after it, wearing only what the shoot above '
        + 'names as staying on her — and if it names nothing, then nothing.\n'
        + 'Which means: invent no clothes. Not a shirt, not a bra, not shoes, not a skirt. '
        + 'Measured, three ways: handed a wardrobe as `what she is wearing` the writer dressed '
        + 'her in it; handed the same list as `what she is NOT wearing` it dressed her in it '
        + 'anyway, in twelve lines of twelve, which is this project\'s oldest finding restated '
        + '— a positive that both describes and denies a garment keeps the garment; and handed '
        + 'nothing at all, without this paragraph, it put her in a polka-dot shirt and stiletto '
        + 'pumps that were in no box anywhere. Every line of this shoot says bare skin where a '
        + 'garment would otherwise go.'
      : 'The wardrobe below is what she is wearing in photograph 1, and your first line dresses '
      + 'her in it exactly as given — only the camera, the framing, the pose and the '
      + 'expression are yours to write there.\n'
      + 'Unless the shoot above says she starts undressed. Then the wardrobe below is what she '
      + 'is NOT wearing, photograph 1 is already where the shoot says it begins, and the only '
      + 'pieces in it are the ones that shoot names. A shoot that opens explicit and a first '
      + 'line that dresses her in the full outfit are two different sessions, and the one that '
      + 'wins is whichever the reader meets last.')

export const wardrobeChunkNote = (at) =>
  `You are writing wardrobe states ${at.from} to ${at.from + at.want - 1} of at most `
  + `${at.total}, in order.\n`
  + 'Each state is held for several photographs. They are the moments the clothes change, '
  + 'not one per photograph.\n'
  + `${at.total} is a ceiling and not a quota. Stop as soon as the shoot has arrived where `
  + 'the brief says it ends and there is nothing left that could come off or move: write '
  + 'fewer lines and stop. Fewer is the right answer, and every photograph after the last '
  + 'state simply stays in it.\n'
  + (at.from > 1
    ? `The wardrobe below is state ${at.from - 1}. Your first line is state ${at.from}: one `
      + 'step on from it, never a copy of it, and everything that has not changed carried '
      + 'over from it word for word.'
    : 'The wardrobe below is state 1, and your first line is it, unchanged.')

/** The expressions, written once and clicked instead of typed.
 *
 *  An expression cannot be composed from text in a take: measured across nine
 *  Krea-2-family checkpoints at one seed, an open mouth kills a wink and pursed
 *  lips kill the joy, and no checkpoint escapes it. The same face *edited* from a
 *  finished photograph gets both in one pass, because the edit moves the mouth
 *  and leaves the eyes, the hands, the crop and the wardrobe alone. So this is an
 *  edit and not a take, and the wording below is the whole feature: it was
 *  measured on krea2edit, and `reference_strength` is not the dial — 4, 3 and 2
 *  came back indistinguishable. The four rows ARE the gradation.
 *
 *  `laughing` closes both eyes, wink included. That is what it does; it is on the
 *  chip rather than fixed, because a laugh with one eye open is not a laugh.
 *
 *  AND EVERY ONE OF THEM NEEDS AN ANCHOR WHOSE FACE FILLS THE FRAME. That is the
 *  rule that decides whether the feature works at all, and it is printed above
 *  the picker for the same reason the angles kind prints its own. An expression
 *  is a few hundred pixels of mouth; on a full-length photograph there is nothing
 *  there to move and the edit hands the frame back untouched. Measured: ten
 *  presets against a full-length anchor came back identical to it — every one
 *  except the wink, which survives because a closed eyelid is the most contrasted
 *  change on a face — and the same ten against a head-and-shoulders anchor moved
 *  the mouth on the first try.
 *
 *  EVERY PRESET MOVES THE FACE AND NOTHING ELSE. Not a rule of taste: the
 *  keep-clause below holds the hands and the pose still, so a preset that needs a
 *  limb is a preset arguing with its own tail — asked to throw a kiss, the edit
 *  keeps the two hands in the photograph and adds the third one the gesture
 *  wants. Anything that reads as a gesture rather than an expression belongs in a
 *  take you write, where the pose is yours to change.
 *
 *  `measured` is the honest half. The first four were run on krea2edit against a
 *  real anchor; the rest are written to the same shape — one movement, named in
 *  plain anatomy, no adjective doing the work — and nobody has looked at what
 *  they return yet. Marked rather than mixed in, because "measured" is the whole
 *  claim this list makes and a preset that quietly joins the measured ones takes
 *  the claim with it. Run one, and if it holds, flip its flag. */
export const EXPRESSIONS = [
  { s: 'soft smile', measured: true,
    v: 'give her a soft closed-lip smile with the corners of her mouth turned up' },
  { s: 'warm smile', measured: true,
    v: 'turn the corners of her mouth up into a warm smile with her lips just parted' },
  { s: 'happy', measured: true,
    v: 'open her mouth into a wide happy smile showing her upper teeth' },
  { s: 'laughing', measured: true,
    v: 'make her laugh out loud with her mouth wide open and her upper teeth showing',
    note: 'closes both eyes — a wink does not survive this one' },
  // `blowing a kiss` and not `a kiss` was the wording here for one commit, and it
  // is the mistake this list is now built to refuse: the idiom is a HAND, not a
  // mouth. Two things then go wrong at once. The keep-clause holds the two hands
  // the photograph already has, the edit adds the one the idiom needs, and the
  // frame comes back with three. And the gesture does not arrive anyway —
  // sessions 112 and 124 asked for it fourteen ways in text-to-image (`cupped
  // palm-up below her chin`, `open beside her cheek`, `having just thrown a
  // kiss`) and the hand landed at mid-chest every time, blowing across a palm
  // nowhere near her lips. So what is left is the half that does render, and it
  // renders well: the mouth.
  { s: 'kiss',
    v: 'push her lips forward into a kiss towards the camera',
    note: 'the mouth only — a hand throwing it grows a third one and never reaches her lips' },
  { s: 'pout',
    v: 'push her lower lip forward into a pout with her lips closed' },
  { s: 'biting her lip',
    v: 'catch her lower lip between her front teeth' },
  { s: 'tongue out',
    v: 'open her mouth and stick her tongue out towards the camera' },
  // The two that move the eyes, and the reason `keeps` exists at all.
  { s: 'wink', eyes: true,
    v: 'close her left eye in a wink and leave her right eye open, her mouth closed' },
  { s: 'sultry', eyes: true,
    v: 'lower her eyelids into a half-lidded look with her lips slightly parted' },
]

/** The tail every preset carries. Without it the edit is free to move what the
 *  photograph already got right, and it is inert when it names something the
 *  photo does not have — tested on an anchor with no hands in frame. */
export const EXPRESSION_KEEP =
  ', and keep her eyes, her hands, her pose and her clothes exactly as they are'

/** The same tail with the eyes left out, for the presets whose whole job is the
 *  eyes. `close her left eye in a wink, and keep her eyes exactly as they are` is
 *  one instruction arguing with itself, and this project has measured what a
 *  prompt that both asks for a thing and denies it comes back as: the denial
 *  wins. */
export const EXPRESSION_KEEP_NO_EYES =
  ', and keep her hands, her pose and her clothes exactly as they are'

export const expressionTake = (e) => e.v + (e.eyes ? EXPRESSION_KEEP_NO_EYES : EXPRESSION_KEEP)

export const ANGLE_FROM_TEXT_INSTRUCTION =
  'Translate the request into the camera vocabulary below. Pick exactly one direction, '
  + 'one height and one framing, and write nothing else.'

/** Workflows worth offering for a slot. An untagged graph is offered everywhere:
 *  every row in a database from before kinds existed is untagged, and filtering
 *  it out would empty every select on the screen. */
export const forKind = (workflows, kind) =>
  (kind ? workflows.filter((w) => !w.kind || w.kind === kind) : workflows)

/** The kind of a session, or null for one created before kinds existed — which
 *  gets no badge, no filtering and no guidance rather than a wrong guess. */
export const sessionKind = (session) => KINDS[session?.settings?.kind] ? session.settings.kind : null

// Steps, cfg, sampler and scheduler, per base model. Nothing here is app
// behaviour: the values come from `checkpoints` in the user's own config.json,
// keyed by the filename ComfyUI reports, because they are a fact about the
// files on this machine and not about iDev.Gen.
export const PROFILE_FIELDS = ['steps', 'cfg', 'sampler', 'scheduler']

/** What a checkpoint asks for, or null when nothing is recorded for it.
 *
 *  Blank fields are dropped rather than written: a profile that names only a
 *  sampler must not silently reset the steps to nothing. Unknown keys are
 *  dropped too — the file is meant to be edited by hand, and a typo there should
 *  do nothing instead of driving an unrelated slot. */
export const checkpointProfile = (config, checkpoint) => {
  const p = checkpoint && (config?.checkpoints || {})[checkpoint]
  if (!p) return null
  const out = Object.fromEntries(
    PROFILE_FIELDS.filter((k) => p[k] !== undefined && p[k] !== null && p[k] !== '').map((k) => [k, p[k]]))
  return Object.keys(out).length ? out : null
}

/** The one-line summary shown next to the base model, so a value that arrived on
 *  its own is visible rather than mysterious. */
export const profileSummary = (p) => [
  p.steps != null && `${p.steps} steps`, p.cfg != null && `cfg ${p.cfg}`, p.sampler, p.scheduler,
].filter(Boolean).join(' · ')

// The angle LoRA answers to a closed vocabulary and ignores everything else, so
// these are chips and not a text box. `s` is the short label on the chip.
export const ANGLE_AXES = [
  { key: 'direction', label: 'Direction', chips: [
    { v: 'front view', s: 'front' },
    { v: 'front-left quarter view', s: 'front-left' },
    { v: 'front-right quarter view', s: 'front-right' },
    { v: 'left side view', s: 'left side' },
    { v: 'right side view', s: 'right side' },
    { v: 'back-left quarter view', s: 'back-left' },
    { v: 'back-right quarter view', s: 'back-right' },
    { v: 'back view', s: 'back' },
  ] },
  { key: 'height', label: 'Height', chips: [
    { v: 'eye-level shot', s: 'eye' },
    { v: 'low-angle shot', s: 'low' },
    { v: 'high-angle shot', s: 'high' },
  ] },
  { key: 'size', label: 'Framing', chips: [
    { v: 'close-up', s: 'close' },
    { v: 'medium shot', s: 'medium' },
    { v: 'full shot', s: 'full' },
  ] },
]
