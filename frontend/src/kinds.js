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
    blurb: 'New photos painted from the look. No reference photo involved.',
    rule: '',
    refKind: null,
    refDefault: false,
    // Captions, not keywords: the text encoder of a current base model was
    // trained on sentences, and a sentence is what composes a frame. The
    // framing opens each one because it is the part that has to outweigh a look
    // several times its length.
    examples: [
      'Full-length, head to feet, walking towards the camera mid-stride, looking away to one side',
      'Close-up, chin slightly down, eyes to the camera, one hand resting against her jaw',
      'Three-quarter, from the knees up, standing with her weight on one leg, hands in her pockets',
      'Waist-up, sitting by the window and leaning on one arm, turned half away from the lens',
    ],
    footer: 'Pose, angle, place — the trigger, base prompt and the session\'s look are '
          + 'prepended automatically. Leave the seed empty unless you are comparing a change.',
    enhance: {
      line: 'Write one take of a photo session: what the body is doing, and how the '
          + 'photograph is framed.\n'
          + 'Write it as a sentence, the way a caption describes a photograph — not as a '
          + 'string of keywords. Fifteen to thirty words. The prompt is read by a text '
          + 'encoder that was trained on captions: `Standing facing the camera, one hand '
          + 'raised near her face` composes a photograph, where `standing, hand raised` is '
          + 'a list of things that happen to be true.\n'
          + 'Open with the framing, in the plainest words there are — `Full-length, head to '
          + 'feet`, `Three-quarter, from the knees up`, `Waist-up`, `Close-up`. It is the '
          + 'part of the take that has to win against a look several times its length, so '
          + 'it goes first, it is never implied, and it is never clever: '
          + '`thigh-to-hair framing` is a crop at the thigh whatever it was meant to say.\n'
          + 'Write nothing else. No clothes, no shoes, no hair, no place, no light, no '
          + 'weather: every one of those is already in the prompt above, and a take that '
          + 'says them again either repeats them or contradicts them — “barefoot” under a '
          + 'look with boots in it is how a shoot comes back wearing something else.\n'
          + 'Above all, never write a garment coming off — no strap pushed aside, no '
          + 'fabric slipping, pooled, gathered, unbuttoned, peeled or absent. The look is '
          + 'prepended to every take word for word, so a take that undresses fights the '
          + 'very sentence that dressed it, and the photo comes back wearing neither. If '
          + 'that is what the request asks for, write the pose alone and leave the '
          + 'wardrobe out of it: taking something off is done by the Photo edit kind, on '
          + 'a photo that already exists.\n'
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
/** The six lines a look is made of, head to toe.
 *
 *  The sections are a checklist before they are an ordering: what varies between
 *  frames is overwhelmingly what nobody wrote down, and asking for "the clothes"
 *  in one line is how a session ends up with no trousers named at all. One line
 *  per part of the body cannot silently skip a part. */
const LOOK_SECTIONS =
  'Answer as exactly these six lines, in this order, each one starting with its name and '
  + 'a bar:\n'
  + 'Hair and makeup | …\n'
  + 'Upper body | …\n'
  + 'Lower body | …\n'
  + 'Feet | …\n'
  + 'Accessories | …\n'
  + 'Place and light | …\n'
  + 'Upper body is every layer from the skin out; lower body is trousers, skirt or the fall '
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
  + 'Six lines every time. The names are not decoration: they are what stops a section '
  + 'being skipped without anyone noticing, which is how a look ends up with no trousers in '
  + 'it. Do not enumerate what catches the eye — walk the six. A section with genuinely '
  + 'nothing in it is the one exception: write `none` after the bar and it is dropped.\n'
  + 'Never write what is absent — “no shirt”, “no bag”, “without a jacket”. The look is '
  + 'read as a description, not as a list of conditions: “no bag” puts a bag in the photo. '
  + 'Write what is there, or `none`.'

/** Detail is a zoom lens, and this is the rule that keeps it from being one.
 *
 *  A sampler frames what the prompt describes. Nineteen words on a bra, its
 *  straps and a necklace, with nothing said above the collarbone or below the
 *  thigh, is a photograph cropped from collarbone to thigh — head out of frame,
 *  the garment filling it. Measured on a real session: the look that did that
 *  spent a fifth of its words on one span of the body. */
const SECTION_BALANCE =
  'Keep the six lines roughly the same length. Detail is not only description, it is also '
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
  + 'buttons open, belt fastened. `white linen midi dress, thin straps, square neckline` '
  + 'comes back the same twice; `white dress` is a different dress every time. What you do '
  + 'not write is not left out of the photo — it is invented, differently in every frame.\n'
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

export const LOOK_INSTRUCTION =
  'Write the look of one photo session — what is worn and where, held identical across '
  + 'every photo of it.\n'
  + LOOK_SECTIONS + '\n'
  + GARMENT_DETAIL + '\n'
  + SECTION_BALANCE + '\n'
  + 'Comma-separated fragments, not sentences. Ten to twenty words a line: the detail has '
  + 'to fit, and a line that rambles past it stops being read.\n'
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
  + 'Comma-separated fragments, not sentences. Ten to twenty words a line. No filler — '
  + '“visible in frame”, “clearly seen”, “in this setting” describe nothing.\n'
  + 'Only what the photo shows. Never a list of alternatives, and never a guess about what '
  + 'is out of frame: a garment cropped away is a section left out, not a section invented.\n'
  + 'Never describe the person wearing it — no face, no body, no age, no ethnicity, no '
  + 'expression. The character comes from a LoRA, and another person\'s features written '
  + 'here fight it in every frame of the session.\n'
  + 'Never write the pose, the framing or the camera angle either: this is what is worn, '
  + 'not how it was photographed.'

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
