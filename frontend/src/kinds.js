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
  + 'Lower body and Feet are almost never `none`, and this is where a look most often '
  + 'goes wrong. When a garment stops at the hip, the legs below it are still in the '
  + 'photograph: write them — bare legs, the skin, tights or stockings and where they end, '
  + 'down through the knees and the ankles. When there are no shoes, write the feet '
  + 'themselves and what they stand on. A look whose words stop at the thigh is a '
  + 'photograph that stops at the thigh, however the take is framed: the frame reaches as '
  + 'far down the body as the words do, and no further.\n'
  + 'The names are not decoration: they are what stops a section being skipped without '
  + 'anyone noticing, which is how a look ends up with no trousers in it. Do not enumerate '
  + 'what catches the eye — walk every one of them.'

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

const LOOK_SECTIONS = `${sections(LOOK_LINES)}\n${SECTION_MEANING}\n${NEVER_ABSENT}`
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
  + 'hair, the makeup or the person: all of those are already in the prompt, above this.'

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
export const SHOOT_LINE_INSTRUCTION =
  'Write one line per photograph of a photo session. Each line is a whole photograph: how it '
  + 'is framed, what she is wearing at that moment, what her body is doing, and her '
  + 'expression — in that order, as prose, one line each, in the order they are shot.\n'
  + PROSE + '\n'
  + '\n'
  + 'EVERY LINE OPENS WITH WHERE THE CAMERA IS, AND THEN ITS FRAMING: `Taken from directly '
  + 'behind her, a full-length photograph, head to feet, of her …`, `Taken from her right '
  + 'side, her body in full profile, a waist-up photograph of her …`. The camera first, '
  + 'because everything after it is eighty words about clothes and the reader frames what it '
  + 'meets first: measured on a seventy-photograph run with the camera named after the '
  + 'framing, fifty-three lines asked for something other than a front view and about ten '
  + 'photographs came back as one.\n'
  + 'The framing is one of three: `a full-length photograph, head to feet`, `a three-quarter '
  + 'photograph from the knees up`, `a waist-up photograph`. Those three and no others. The '
  + 'plainest words there are, never implied and never '
  + 'clever — `thigh-to-hair framing` is a crop at the thigh whatever it was meant to say. '
  + 'A close-up is not on the list on purpose: asked for one, this model returns a waist-up '
  + 'photograph anyway, and a framing that is asked for and not obtained is a line that spent '
  + 'its words on nothing.\n'
  + 'Change it from line to line and spread the four across the shoot, with at least one line '
  + 'in four full-length, head to feet. Measured on this project: forty-five lines written '
  + 'with no framing in any of them came back as forty-five mid-shots, four of which happened '
  + 'to be full length — a shoot read in order was then one photograph taken forty-five '
  + 'times, whatever its clothes were doing.\n'
  + 'THE CAMERA CLAUSE IS ONE OF THESE FIVE: `Taken from directly in front of her`, `Taken '
  + 'from behind her left shoulder, her back three-quarters to the camera`, `Taken from her '
  + 'right side, her body in full profile`, `Taken from directly behind her`, `Taken from '
  + 'above her, looking down`. Those five, and not `from floor level` or `from over her '
  + 'shoulder`: both are ignored by this model and come back frontal, twice measured. Move it '
  + 'around the shoot rather than settling on one, because a distance alone leaves the camera '
  + 'where the model likes it: measured, thirty photographs written with a framing in every '
  + 'line and no camera position in any of them came back thirty frontal shots, and the same '
  + 'prompt with the position named moved the camera in six of eight — including all the way '
  + 'behind her.\n'
  + 'The framing says how much of her is in frame. It never says how much of her to write: every '
  + 'line still walks the whole body, close-ups included, because the state of the clothes is '
  + 'what the next photograph copies and a line that drops it to match a crop drops it for '
  + 'every line after it as well.\n'
  + 'About a hundred words a line — long enough for the framing, the camera, every garment '
  + 'carried word for word, the pose and the face, and no longer. Do not buy the room by '
  + 'shortening a garment: the words are what make it the same garment in the next '
  + 'photograph, and a line that trades `white open-weave fishnet stockings` for `the '
  + 'stockings` is a line whose stockings come back black. Buy it by saying each piece ONCE. '
  + 'Naming a garment twice in one line, or re-listing what the line has already said, is '
  + 'where a hundred and sixty words go.\n'
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
  + 'the camera height and angle, by the pose, by what the hands are doing, and by the '
  + 'expression. Four photographs of one stage are four photographs, not one with three '
  + 'copies — and the wardrobe is what holds still between them, word for word.\n'
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
  + 'A second person is written as a body, never as somebody. Say `two people in frame`, then '
  + 'what his body is doing and where it is against hers. Never his face, his age or who he '
  + 'is: the photograph needs a body, and a described stranger competes with the character the '
  + 'rest of the prompt is painting.\n'
  + 'Nothing above is suspended for these lines. The framing still opens the line, the camera '
  + 'position is still named — a two-person frame needs it more than any other, because it is '
  + 'what decides whether the act is in view at all — and her whole body is still walked.\n'
  + '\n'
  + GARMENT_CARRY + '\n'
  + '\n'
  + 'Never write the hair, the makeup, the room or the light: they are prepended to every '
  + 'line of this shoot already, and writing them again either repeats or contradicts them. '
  + 'Never describe her face, her age or her body beyond what the pose and the expression '
  + 'need. Never write a camera brand, a lens or a film stock.'

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
export const shootChunkNote = (at) =>
  `${paced('photographs', at)}\n`
  + (at.stages?.length
    ? 'The stages covering the photographs you are writing:\n'
      + at.stages.map((s) => `${s.from}-${s.to} | ${s.what}`).join('\n') + '\n'
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
      ? 'This shoot opens with her already undressed, so photograph 1 is where the shoot above '
        + 'says it begins and she wears only what that sentence keeps on her. There is no '
        + 'wardrobe to start from and nothing to take off: write the skin, and whatever the '
        + 'shoot names as staying on.'
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
