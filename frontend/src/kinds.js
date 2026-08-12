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
    examples: [
      'three-quarter view, hands in pockets, looking away',
      'close-up, chin slightly down, eyes to camera',
      'full body, walking, mid-stride',
      'sitting by the window, leaning on one arm',
    ],
    footer: 'Pose, angle, place — the trigger, base prompt and the session\'s look are '
          + 'prepended automatically. Leave the seed empty unless you are comparing a change.',
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
  },
}

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
