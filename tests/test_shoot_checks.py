"""The checks a written shoot line is held to, which live in the frontend.

Two tests and they catch two different failures of the same bug. The first is
pure Python and always runs: a control character in a source file. The second
runs the real JavaScript, because the rule it guards is behaviour and a grep for
it would pass on code that does nothing.

The bug both were written for: `frontend/src/enhance.js` carried a literal
backspace byte (0x08) where `\\b` was meant, in the regex that exempts a
two-person line from the whole-body walk. It matched nothing, so the exemption
never fired once between the commit that added it and the run that found it —
fifteen lines of a twenty-photograph explicit shoot were told they had forgotten
the feet, the repair put them back, and the renders came back with a disembodied
penis and no man in them. Nothing failed; there was nothing to fail.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_no_personal_data import SKIP_SUFFIXES, tracked_files

ROOT = Path(__file__).resolve().parents[1]

# Tab, newline and carriage return are the only ones a source file has any use
# for. Everything else below 0x20 is invisible in every editor, invisible in a
# diff, invisible in code review, and changes what the code means.
LEGAL_CONTROLS = "\t\n\r"


def test_no_control_characters_in_tracked_files():
    offenders = []
    for rel in tracked_files():
        if Path(rel).suffix.lower() in SKIP_SUFFIXES:
            continue
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, char in enumerate(text):
            if ord(char) < 0x20 and char not in LEGAL_CONTROLS:
                line = text[:i].count("\n") + 1
                offenders.append(f"{rel}:{line}: {hex(ord(char))}")

    assert not offenders, ("invisible control characters in tracked files:\n"
                           + "\n".join(offenders))


def test_no_trailing_whitespace_in_tracked_files():
    """The same family as the control characters above: a change nobody can see.

    Trailing spaces survive review because there is nothing to look at — the
    editor shows the line as it would be either way, and the diff shows a `+`
    with nothing after it. They arrive by the handful when a file is edited by
    something that indents a blank line, and they make every later diff of that
    file noisier than the change it carries.

    A rule for this already existed in prose and was worth nothing: asked twice
    to strip them, an assistant answered twice that the check came back clean,
    having never run it. A rule an agent can claim to have followed is not a
    rule. This one is a test, and no summary talks it out of failing.

    No exemption for Markdown two-space hard break: the tree has never used one,
    and a line break worth having is worth an empty line.
    """
    offenders = []
    for rel in tracked_files():
        if Path(rel).suffix.lower() in SKIP_SUFFIXES:
            continue
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for n, line in enumerate(text.split("\n"), 1):
            if line != line.rstrip():
                offenders.append(f"{rel}:{n}: {len(line) - len(line.rstrip())} trailing")

    assert not offenders, ("trailing whitespace in tracked files:\n"
                           + "\n".join(offenders))


def test_the_trailing_whitespace_scan_actually_bites():
    """A guard that cannot fail is decoration."""
    assert "x  " != "x  ".rstrip()
    assert "x\t" != "x\t".rstrip()
    assert "   " != "   ".rstrip()          # an indented blank line is the common one
    assert "x" == "x".rstrip()


# ---------------------------------------------------------------------------
# The behaviour, run for real.

# A line the shoot writer would be proud of: the camera first, then the framing,
# then the two of them and the act. Under the cap, and nothing wrong with it.
TWO_PERSON_LINE = (
    "Taken from her right side, her body in full profile, a full-length photograph, "
    "head to feet, of a naked man kneeling behind her on the white bedding, his penis "
    "inside her, his hands on her hips, two people in frame, her mouth open and her "
    "eyes shut."
)

# A clothed photograph of the same shoot, to hold the cap to the second body and
# not to length itself. Every part of the body walk is named, so the only thing
# that can be wrong with it is how long it is.
CLOTHED_LINE = (
    "Taken from her right side, her body in full profile, a full-length photograph, "
    "head to feet, of her in a white shirt and a black pleated skirt on the white "
    "bedding, her hands at her sides, her bare knees together, her feet bare on the "
    "mattress, her lips parted."
)

# The inventory of her bare parts, which is what the writer actually returns once
# the whole-body walk is asked of a photograph that has no clothes left in it —
# and what makes the man disappear from the render.
PADDING = (" her bare shoulders lifted, her bare ribs turning, her bare stomach "
           "stretched, her bare thighs parted, her bare knees bent, her bare feet "
           "flat on the mattress,")

PROBE = """
import { problemsWith } from '%(src)s'
import { EXPLICIT_REGISTER, SHOOT_LINE_INSTRUCTION } from '%(kinds)s'

const words = (t) => t.trim().split(/\\s+/).length
const pad = '%(padding)s'.repeat(4)
const short = %(short)s
// Past the runaway guard, which is 260 since his body got a field of its own -
// see TWO_PEOPLE_WORDS. The old cap was 110 and a line of this length was the
// failure; it is now an ordinary explicit line.
const long = short.replace('two people in frame', '%(padding)s'.repeat(12) + ' two people in frame')
const clothed = %(clothed)s.replace('her lips parted.', pad + ' her lips parted.')

const tag = (line, limit) => problemsWith(line, '', limit).map((p) =>
  /^It is \\d+ words long, and \\d+ is the runaway guard/.test(p) ? 'TWO_PEOPLE_LONG'
  : /^It is \\d+ words long, half as long again/.test(p) ? 'SHOOT_LONG'
  : /says nothing about/.test(p) ? 'BODY_WALK'
  : p.slice(0, 40))

console.log(JSON.stringify({
  shortWords: words(short), longWords: words(long), clothedWords: words(clothed),
  // 400 so what is under test is the two-person guard itself and not the
  // shoot's own relative limit, which would flag any long line either way.
  short: tag(short, 400), long: tag(long, 400), clothed: tag(clothed, 200),
  // The whole-body walk, on a two-person line that names no chest, no legs and
  // no feet: the exemption is the only thing standing between it and three
  // complaints, so a broken exemption shows up here and nowhere else.
  bare: tag('Taken from directly behind her, a waist-up photograph, of a naked man '
            + 'behind her, his penis inside her, two people in frame, her mouth open.', 200),
  // Which of the two bodies gets introduced is a system-message rule now, and it
  // is only worth moving there if it is not also sitting in the brief: two texts
  // saying the same thing is one edit away from two texts disagreeing.
  namingInTheBrief: /naked woman|young woman/i.test(EXPLICIT_REGISTER + SHOOT_LINE_INSTRUCTION),
}))
"""


# The failure this reproduces, with no model in the room: the repair is handed
# `previous` as "the photograph before this one", and answers with it. Every
# check the repair scores on passes — same framing, same camera, same body walk,
# no new problem — so the shoot goes out with two rows that are one photograph.
REPAIR_PROBE = """
import { repairAll } from '%(src)s'

const good = %(good)s
const broken = %(broken)s
const proper = %(proper)s

// The endpoint the repair asks, answering whatever this test wants it to.
const reply = (text) => {
  globalThis.fetch = async () => ({
    ok: true, status: 200, json: async () => ({ lines: [{ label: '', prompt: text }] }),
  })
}

const run = async (answer) => {
  reply(answer)
  const r = await repairAll([good, broken], '', null, 0, 2, 200)
  return { lines: r.lines, repaired: r.repaired, stillWrong: r.stillWrong }
}

console.log(JSON.stringify({ echoed: await run(good), fixed: await run(proper) }))
"""


def _node_json(script: str, tmp_path: Path) -> dict:
    """Bundle a probe against the real module and run it. esbuild is already a
    dependency of the frontend build; nothing here reaches the network."""
    node = shutil.which("node")
    # `which` and not a glob: the folder holds both a POSIX shell script and a
    # Windows `.cmd` under the same name, and only one of the two will start.
    esbuild = shutil.which("esbuild", path=str(ROOT / "frontend/node_modules/.bin"))
    if not node or not esbuild:
        pytest.skip("needs node and frontend/node_modules (npm install in frontend/)")

    probe, bundle = tmp_path / "probe.mjs", tmp_path / "probe.bundle.mjs"
    probe.write_text(script, encoding="utf-8")
    built = subprocess.run([str(esbuild), str(probe), "--bundle", "--platform=node",
                            "--format=esm", f"--outfile={bundle}"],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    out = subprocess.run([node, str(bundle)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def checks(tmp_path_factory) -> dict:
    script = PROBE % {"src": (ROOT / "frontend/src/enhance.js").as_posix(),
                      "kinds": (ROOT / "frontend/src/kinds.js").as_posix(),
                      "short": json.dumps(TWO_PERSON_LINE),
                      "clothed": json.dumps(CLOTHED_LINE),
                      "padding": " " + PADDING.strip()}
    return _node_json(script, tmp_path_factory.mktemp("shootchecks"))


def test_a_two_person_line_is_exempt_from_the_whole_body_walk(checks):
    """The regex with the backspace in it matched nothing, so this was the state
    of every explicit line ever written: three complaints, three repairs, and the
    repair's job was to add the words that lose the second body."""
    assert "BODY_WALK" not in checks["bare"], checks["bare"]


def test_a_two_person_line_is_held_to_the_runaway_guard(checks):
    """The wall was 110 while both bodies shared one budget. It is 260 since the
    writer answers in fields and his body has one of its own: measured
    2026-08-20, a twelve-photograph shoot written that way averaged 212 words a
    line and rendered two bodies in twelve frames of twelve. What is left here
    catches a line repeating itself, which is the one length worth cutting."""
    assert checks["shortWords"] <= 260 < checks["longWords"], checks
    assert checks["short"] == [], checks["short"]
    assert checks["long"] == ["TWO_PEOPLE_LONG"], checks["long"]


def test_the_cap_is_the_second_body_and_not_the_length(checks):
    """The same words with nobody else in them are held to the shoot's own limit,
    which is what `lengthLimit` measures and what every clothed line lives by."""
    assert checks["clothedWords"] > 110, checks
    assert "TWO_PEOPLE_LONG" not in checks["clothed"], checks["clothed"]
    assert "SHOOT_LONG" not in checks["clothed"], checks["clothed"]


def test_the_brief_does_not_restate_which_body_is_introduced(checks):
    """It said it there once, at length and with its measurement attached, and the
    writer opened nineteen lines of twenty with `a naked man and a naked woman`
    anyway. The rule moved to the system message — see EXPLICIT_SYSTEM in
    `backend/enhance.py` — and 0 of 20 did it on the next run. Restating it in the
    brief is how that becomes two rules that can disagree."""
    assert checks["namingInTheBrief"] is False


# A photograph that needs no repair, and one that does: it opens on the subject
# instead of the camera and never says its framing, which is two content problems
# and the repair path they send a line down.
BROKEN_LINE = (
    "Of a naked man lying beneath her on the white bedding, his penis inside her, "
    "her hands flat on his chest, two people in frame, her lips parted."
)
PROPER_REPAIR = (
    "Taken from above her, looking down, a waist-up photograph, of a naked man lying "
    "beneath her on the white bedding, his penis inside her, her hands flat on his "
    "chest, two people in frame, her lips parted."
)


@pytest.fixture(scope="module")
def repairs(tmp_path_factory) -> dict:
    script = REPAIR_PROBE % {"src": (ROOT / "frontend/src/enhance.js").as_posix(),
                             "good": json.dumps(TWO_PERSON_LINE),
                             "broken": json.dumps(BROKEN_LINE),
                             "proper": json.dumps(PROPER_REPAIR)}
    return _node_json(script, tmp_path_factory.mktemp("repairs"))


def test_a_repair_that_answers_with_the_previous_photograph_is_refused(repairs):
    """Two rows of one photograph is what the shoot ends up with otherwise, and
    the pair is queued twice and shot twice."""
    echoed = repairs["echoed"]
    assert echoed["lines"][1] == BROKEN_LINE, echoed["lines"]
    assert echoed["lines"][0] != echoed["lines"][1], echoed["lines"]
    assert echoed["repaired"] == 0, echoed
    # Refused is not fixed: the row stays broken and has to be visible.
    assert echoed["stillWrong"] == [2], echoed


def test_a_repair_that_is_actually_a_repair_is_still_kept(repairs):
    """The guard above must not cost the repair its job — measured once already
    at three in four, and a check that refuses everything reads the same as one
    that refuses nothing."""
    fixed = repairs["fixed"]
    assert fixed["lines"] == [TWO_PERSON_LINE, PROPER_REPAIR], fixed["lines"]
    assert fixed["repaired"] == 1, fixed
    assert fixed["stillWrong"] == [], fixed


# ---------------------------------------------------------------------------
# The tail of a long shoot, which is where one photograph gets written five times.

# Photograph 44 of a real forty-five frame run, and photograph 45 after it: the
# same camera, the same act, the same pose, differing by a `bare` and a framing.
# Byte-for-byte they are two lines, so the old check let both through and both
# were queued and rendered.
TAIL_LINE = (
    "Taken from behind her left shoulder, her back three-quarters to the camera, a "
    "three-quarter photograph from the knees up, of him thrusting into her with his penis "
    "sliding fully inside her as he holds her by her bare hips and she wraps her bare legs "
    "around his bare hips with her ankles still crossed behind his lower back, her head "
    "tipping back into the mattress, the thin black choker still at her throat."
)
TAIL_REWORDED = (
    "Taken from behind her left shoulder, her back three-quarters to the camera, a waist-up "
    "photograph, of him thrusting into her with his penis sliding fully inside her as he "
    "holds her by her hips and she wraps her legs around his hips with her ankles still "
    "crossed behind his lower back, her head tipping back into the mattress, the thin black "
    "choker still at her throat."
)

# Two wardrobe states, one garment apart. The progression stream is BUILT on
# carrying every unchanged piece over word for word, so these two are meant to
# look alike — which is why the fuzzy threshold is a parameter and not the
# default. Measured on four one-step changes of this wardrobe: the hem lifted
# 0.81, the skirt unzipped 0.81, the skirt off 0.82, the stockings rolled down
# 0.74. All under 0.85, and the widest of them by three hundredths.
STATE = ("a white cropped football jersey, a black pleated mini skirt, white open-weave "
         "fishnet stockings, black leather platform boots, a thin black choker")
NEXT_STATE = ("a white cropped football jersey, white open-weave fishnet stockings rolled "
              "down to her knees, black leather platform boots, a thin black choker")

# Photograph 1 of a real forty-five frame run, shortened. Two words — `a woman`
# where `her` belongs — and the next sixteen photographs of seventeen copied them.
SEEDED_LINE = (
    "Taken from directly in front of her, a full-length photograph, head to feet, of a woman "
    "in a white cropped football jersey and a black pleated mini skirt, white stockings on her "
    "legs, black leather platform boots on her feet, a thin black choker at her throat."
)
# The one shape the substitution leaves alone: replacing it makes worse English
# than it found, and a line that opens on her instead of on the camera is broken
# in a way the check already says out loud.
OPENING_LINE = "A woman stands square to the mirror, her hands at her sides."

NAMING_PROBE = """
import { onlyHer, problemsWith } from '%(src)s'

const seeded = %(seeded)s
const opening = %(opening)s
const twoPeople = %(two)s

const introduces = (line) => problemsWith(line, '', 200).some((p) => /^It introduces her/.test(p))

console.log(JSON.stringify({
  seededWas: introduces(seeded),
  cleaned: onlyHer(seeded),
  cleanedIntroduces: introduces(onlyHer(seeded)),
  openingUntouched: onlyHer(opening) === opening,
  openingStillFlagged: introduces(opening),
  // The second body is introduced on purpose and stays a body.
  secondBodyKept: onlyHer(twoPeople) === twoPeople,
}))
"""


@pytest.fixture(scope="module")
def naming(tmp_path_factory) -> dict:
    script = NAMING_PROBE % {"src": (ROOT / "frontend/src/enhance.js").as_posix(),
                             "seeded": json.dumps(SEEDED_LINE),
                             "opening": json.dumps(OPENING_LINE),
                             "two": json.dumps(TWO_PERSON_LINE)}
    return _node_json(script, tmp_path_factory.mktemp("naming"))


def test_a_line_that_introduces_her_is_rewritten(naming):
    """The one place the code decides what a line says instead of asking the model.
    It earns it by spreading: the writer seeds this in well under a line in
    seventy, and every round after copies its previous photograph word for word —
    16 of the next 17 lines carried it, against 0 of 24 from the same line with
    `of her` in it, and the repair cleared none of the thirteen in a real run."""
    assert naming["seededWas"] is True, naming
    assert "of her in a white cropped football jersey" in naming["cleaned"], naming["cleaned"]
    assert naming["cleanedIntroduces"] is False, naming


def test_the_second_body_is_still_introduced(naming):
    """`a naked man` is how a second person is written and must survive: the
    photograph needs a body there, and the rule was only ever about her."""
    assert naming["secondBodyKept"] is True, naming


def test_a_line_that_opens_on_her_is_left_to_the_check(naming):
    """`Her stands square to the mirror` is not a repair."""
    assert naming["openingUntouched"] is True, naming
    assert naming["openingStillFlagged"] is True, naming


REPEATS_PROBE = """
import { repeats, SAME_PHOTOGRAPH } from '%(src)s'

const tail = %(tail)s
const reworded = %(reworded)s
const other = %(other)s
const state = %(state)s
const next = %(next)s

console.log(JSON.stringify({
  rewordedIsExact: repeats(reworded, [tail]),
  rewordedIsSamePhotograph: repeats(reworded, [tail], SAME_PHOTOGRAPH),
  otherIsSamePhotograph: repeats(other, [tail], SAME_PHOTOGRAPH),
  // What the wardrobe stream would lose if this threshold were the default.
  stateIsExact: repeats(next, [state]),
  stateIsSamePhotograph: repeats(next, [state], SAME_PHOTOGRAPH),
}))
"""


@pytest.fixture(scope="module")
def rewordings(tmp_path_factory) -> dict:
    script = REPEATS_PROBE % {"src": (ROOT / "frontend/src/enhance.js").as_posix(),
                              "tail": json.dumps(TAIL_LINE),
                              "reworded": json.dumps(TAIL_REWORDED),
                              "other": json.dumps(CLOTHED_LINE),
                              "state": json.dumps(STATE),
                              "next": json.dumps(NEXT_STATE)}
    return _node_json(script, tmp_path_factory.mktemp("rewordings"))


def test_a_photograph_written_twice_is_caught_even_reworded(rewordings):
    """Measured on a forty-five frame shoot briefed to end explicit: it reached its
    ending, and then wrote that ending five times. Photographs 41 to 45 scored
    1.00, 0.92, 0.98 and 0.95 against the line before them while every other pair
    in the shoot topped out at 0.79 — five rows, one photograph, five renders."""
    assert rewordings["rewordedIsExact"] is False, "the old check saw two lines here"
    assert rewordings["rewordedIsSamePhotograph"] is True, rewordings


def test_the_next_photograph_is_not_a_repeat(rewordings):
    """A threshold that refuses everything is the same as one that refuses
    nothing."""
    assert rewordings["otherIsSamePhotograph"] is False, rewordings


def test_a_wardrobe_state_one_garment_on_is_not_a_repeat(rewordings):
    """`repeats` is asked by three streams and only the shoot wants the fuzzy
    answer. A wardrobe state that carries five of six pieces over word for word is
    the progression working, and dropping it is how a shoot runs out of clothes and
    invents a schoolgirl uniform to keep undressing. It clears the threshold, but
    by three hundredths — which is the margin this test exists to watch, and the
    reason the shoot's threshold is passed in rather than made the default."""
    assert rewordings["stateIsExact"] is False, rewordings
    assert rewordings["stateIsSamePhotograph"] is False, (
        "a one-step wardrobe change now scores as a repeat: either the threshold "
        "moved or the states got longer, and the progression stream is about to "
        "start dropping its own lines")


# The real line from run 1 of the 2026-08-16 cap runs, 141 words, that the repair
# handed back unchanged. Its bare-part inventory is the fifteen words the code
# now cuts; everything else in it is a fact the photograph needs.
LONG_TWO_PERSON = (
    "Taken from her right side, her body in full profile, a waist-up photograph, of her on "
    "hands and knees on the rug with a naked man behind her, two people in frame, wearing "
    "nothing but the choker and the thigh bands, her bare shoulders, her bare chest, her bare "
    "back, her bare arms, her hips and thighs bare, a slim green choker with a small gold heart "
    "pendant sitting at the base of her throat, two thin green bands encircling each thigh "
    "joined at the sides by small gold rings pressing into the skin above them, his hands "
    "gripping her hips, his cock clearly visible sliding into her from behind, detailed "
    "penetration, her spine curved downward, her head low, both palms pressed into the rug "
    "beneath her shoulders, her face turned toward the camera: lips parted, eyes half closed, "
    "flushed skin."
)

HIS_BODY = (
    " his bare chest lowered along the length of her back, his bare shoulders above hers, his "
    "bare arms reaching forward past her ribs, his bare stomach against her lower back, his "
    "bare hips flush against her hips, his bare thighs outside hers, his bare knees dug into "
    "the rug behind her knees, his weight carried on his knees and on the flat of his left "
    "hand, his right forearm braced across her hip, his shoulders squared to the line of her "
    "spine, his chest rising above the curve of her back, his hips level with the backs of her "
    "thighs, his knees set wider than her knees on the rug beneath them, the long muscles of "
    "his back holding him over her, his elbow bent and his wrist turned in against the rug,"
)

TRIM_PROBE = r"""
import { trimBareClauses, dropListedGarments, problemsWith, namesWhatItSheds } from "%(src)s"
const w = (s) => s.split(/\s+/).filter(Boolean).length
const long = %(long)s
const short = "Taken from her right side, a waist-up photograph of a naked man behind her, "
  + "two people in frame, his cock sliding into her, her bare chest."
const oneP = "Taken from directly in front of her, a full-length photograph, head to feet, of "
  + "her standing on the rug, her bare shoulders, her chest bare, her bare feet on the rug."
const out = trimBareClauses(long)
// On the line itself, not on the trimmed one: both levers are gated at the same
// runaway guard, and the first now shortens the line enough that chaining them
// leaves the second nothing to fire on. They are two independent cuts and the
// app applies both to whatever the writer handed it.
const deduped = dropListedGarments(long)
console.log(JSON.stringify({
  before: w(long), after: w(out), text: out,
  keptAct: /sliding into her/.test(out),
  keptCamera: /^Taken from her right side/.test(out),
  keptGarments: /slim green choker/.test(out) && /thin green bands/.test(out),
  droppedInventory: !/her bare shoulders/.test(out) && !/her bare arms/.test(out),
  shortUntouched: trimBareClauses(short) === short,
  onePersonUntouched: trimBareClauses(oneP) === oneP,
  afterDedup: w(deduped),
  droppedListing: !/wearing nothing but/.test(deduped),
  dedupKeptDescriptions: /slim green choker with a small gold heart/.test(deduped)
    && /two thin green bands encircling/.test(deduped),
  dedupKeptAct: /sliding into her/.test(deduped) && /his hands gripping her hips/.test(deduped),
  dedupKeptCamera: /^Taken from her right side/.test(deduped),
  dedupShortUntouched: dropListedGarments(short) === short,
  dedupOnePersonUntouched: dropListedGarments(oneP) === oneP,
  // `his penis against her` renders as a penis against her: the pose right, both
  // bodies there, no act. Three of eight checkpoints against eight of eight for
  // the same photograph written as penetration.
  contactFlagged: problemsWith(
    "Taken from her right side, her body in full profile, a waist-up photograph, she kneels "
    + "on the bed with her palms flat in front of her, a man behind her with his hands on her "
    + "hips and his penis against her, two people in frame, her mouth open.",
    "", 200).some((p) => p.includes("touching, not joined")),
  contactClean: !problemsWith(
    "Taken from her right side, her body in full profile, a waist-up photograph, of a naked "
    + "man behind her penetrating her from behind, his penis inside her, his hands on her "
    + "hips, two people in frame, her mouth open.",
    "", 200).some((p) => p.includes("touching, not joined")),
  // Two garments an entire shoot was built on, invisible to the check until the
  // renders showed both of them still on.
  shedNoLonger: namesWhatItSheds(
    "she stands beside the bed with the slim green choker no longer present, her chest bare, "
    + "her hips bare, her feet bare on the rug."),
  shedBands: namesWhatItSheds(
    "the thin green bands no longer encircling her thighs, the choker neck band no longer "
    + "resting below her throat, her chest bare, her hips bare, her feet bare."),
  // A garment merely moved is still worn and still named: flagging those would
  // flag the middle of every shoot.
  shedMoved: namesWhatItSheds(
    "the choker still at her throat, the thin green bands pushed down her thighs, the "
    + "bodysuit pulled aside at the hip, her chest bare."),
  // A garments-first two-person line raises no ordering complaint any more: that
  // check was removed for want of evidence (2 of 6 either way when rendered), and
  // this asserts it stays removed rather than coming back on the same hunch.
  // The camera may be written as a camera and not only as `Taken from ...`:
  // measured 2026-08-17, `Overhead camera directly above the bed` came back
  // overhead and `Side-angle camera at mattress level` came back at mattress
  // level, where the same angles written as `from above her, looking down` are
  // ignored in six of six. The check looks for the noun, so both forms pass and
  // a line with no camera in it at all still does not.
  openerNamedCamera: !problemsWith(
    "Overhead camera directly above the bed, a three-quarter photograph from the knees up, "
    + "a naked man beneath her penetrating her, his penis sliding in and out of her, two "
    + "people in frame, his chest and his knee in frame, her mouth open.",
    "", 200).some((p) => p.includes("OPEN with where the camera")),
  openerTakenFrom: !problemsWith(
    "Taken from her right side, her body in full profile, a waist-up photograph, a naked man "
    + "behind her penetrating her, his penis inside her, two people in frame, her mouth open.",
    "", 200).some((p) => p.includes("OPEN with where the camera")),
  openerMissing: problemsWith(
    "She kneels on the bed with her palms flat in front of her, a naked man behind her "
    + "penetrating her, his penis inside her, two people in frame, her mouth open.",
    "", 200).some((p) => p.includes("OPEN with where the camera")),
  noOrderComplaint: !problemsWith(
    "Taken from her right side, her body in full profile, a waist-up photograph, of her on "
    + "all fours on the bed, a slim green choker at the base of her throat, thin green bands "
    + "on both thighs, a man kneeling behind her, his penis inside her, her mouth open.",
    "", 200).some((p) => p.includes("before the act")),
}))
"""


@pytest.fixture(scope="module")
def trimmed(tmp_path_factory) -> dict:
    script = TRIM_PROBE % {"src": (ROOT / "frontend/src/enhance.js").as_posix(),
                           "long": json.dumps(
                               LONG_TWO_PERSON.replace("her spine curved downward,",
                                                       HIS_BODY.strip() + " her spine curved downward,"))}
    return _node_json(script, tmp_path_factory.mktemp("trim"))


def test_the_camera_may_be_named_as_a_camera(trimmed):
    """`from above her, looking down` is ignored by this model and comes back
    frontal; `Overhead camera directly above the bed` is obeyed. Same angle, and
    the difference is whether it is a camera or an adverb - so the opener check
    looks for the noun rather than for one fixed phrase. Both forms pass; a line
    with no camera in it still fails."""
    assert trimmed["openerNamedCamera"], "a named camera should open a line"
    assert trimmed["openerTakenFrom"], "the five `Taken from` clauses still open a line"
    assert trimmed["openerMissing"], "a line with no camera at all must still be flagged"


def test_the_code_cuts_what_the_repair_would_not(trimmed):
    """Five runs of the writer, and the repair handed the worst lines back byte
    for byte — 178 words before and 178 after. The re-ask was already here; what
    was missing was a fallback for when the model simply declines."""
    assert trimmed["before"] > 260, trimmed   # the runaway guard in enhance.js
    assert trimmed["after"] < trimmed["before"], trimmed
    assert trimmed["droppedInventory"], trimmed


def test_the_cut_keeps_the_act_the_camera_and_the_garments(trimmed):
    """The three things a two-person line exists for. A shortening that takes any
    of them is worse than the long line, so the function hands the original back
    instead."""
    assert trimmed["keptAct"], trimmed
    assert trimmed["keptCamera"], trimmed
    assert trimmed["keptGarments"], trimmed


def test_it_leaves_alone_what_it_must(trimmed):
    """A two-person line already inside the cap is not touched, and a ONE-person
    line is never touched at all: there an unstated torso is a torso the model
    dresses for you, which is the whole reason the body walk exists."""
    assert trimmed["shortUntouched"], trimmed
    assert trimmed["onePersonUntouched"], trimmed


def test_the_garment_named_twice_loses_its_listing(trimmed):
    """The measured line says each garment twice: `wearing nothing but the choker
    and the thigh bands` beside the two clauses that describe them — forty-seven
    words for two garments. The listing goes, the descriptions stay, and the act,
    the hands and the camera survive the cut."""
    assert trimmed["droppedListing"], trimmed
    assert trimmed["dedupKeptDescriptions"], trimmed
    assert trimmed["dedupKeptAct"], trimmed
    assert trimmed["dedupKeptCamera"], trimmed
    assert trimmed["afterDedup"] < trimmed["before"], trimmed


def test_the_dedupe_leaves_alone_what_it_must(trimmed):
    """Same exemptions as the bare-clause cut: inside the cap untouched, and a
    one-person line never touched at all."""
    assert trimmed["dedupShortUntouched"], trimmed
    assert trimmed["dedupOnePersonUntouched"], trimmed


def test_clause_order_is_not_checked(trimmed):
    """The ordering complaint this file used to assert is gone, and stays gone.
    It was read off one sweep frame at 3/8 against another at 8/8, but that frame
    also said `his penis against her` — the defect the next test covers — and the
    two causes were confounded. Measured directly: three real garments-first lines
    against the same lines with the garments spliced behind the act, two seeds
    each, judged three times, **2 of 6 either way**. What does measure is the
    garments being in the sentence rather than in a block ahead of it, which is a
    fact about the composer and invisible to a per-line check."""
    assert trimmed["noOrderComplaint"], trimmed


def test_the_act_must_be_penetration_and_not_contact(trimmed):
    """`his penis against her` came back as a penis against her — the pose right,
    both bodies present, no act — on five of eight checkpoints, while the same
    photograph written as penetration rendered on eight of eight. Plain anatomy is
    not the same as the act happening, and `ACT` (which exists to protect anatomy
    from a repair) cannot tell them apart."""
    assert trimmed["contactFlagged"], trimmed
    assert trimmed["contactClean"], trimmed


def test_a_garment_removed_as_no_longer_anything_is_still_named(trimmed):
    """`the slim green choker no longer present` renders with the choker on, eye-
    checked. SHED only knew `no longer on|worn`, and neither the choker nor the
    thigh bands was a garment family at all, so the two pieces this shoot is built
    on were invisible to both this check and the put-back check."""
    assert "choker" in trimmed["shedNoLonger"], trimmed
    assert set(trimmed["shedBands"]) >= {"choker", "bands"}, trimmed


def test_a_garment_merely_moved_is_not_flagged(trimmed):
    """The other half of the rule, and the reason SHED is a closed list: a piece
    pushed down or pulled aside is still worn, still named, and flagging it would
    flag the middle of every shoot."""
    assert trimmed["shedMoved"] == [], trimmed


# The whole writer path with no network: every fetch is answered by a double, so
# what this exercises is the code around the calls - the register gate, the chunk
# notes, the repair - on the shapes the server really returns.
#
# It exists because `reachesTheAct` was used in `shootLines` and never imported.
# The suite was green: esbuild bundles an undefined name without a word, and
# nothing here ran the function that used it. The app threw on the first shoot.
SHOOT_PROBE = """
import { shootLines } from '%(src)s'

const asked = []
// Eight different photographs: the writer drops a line that scores as a repeat of
// one the shoot already has, so eight copies of one line come back as one row.
const CAMERAS = ['Taken from her left side, her body in full profile',
                 'Taken from directly behind her', 'Taken from directly in front of her',
                 'Taken from behind her left shoulder, her back three-quarters to the camera',
                 'Overhead camera directly above the bed', 'Low-angle shot from the foot of the bed',
                 'Taken from her right side, her body in full profile',
                 'Side-angle camera at mattress level']
const ACTS = ['kneels behind her', 'lies under her', 'stands at the edge of the bed',
              'kneels between her knees', 'sits back on his heels', 'leans over her shoulder',
              'kneels astride her thigh', 'crouches at her hip']
const LINE = (i) => `${CAMERAS[i %% 8]}, a waist-up photograph. A naked man `
  + `${ACTS[(i * 3) %% 8]}, his penis inside her vagina, two people in frame. `
  + `${PARTS[(i * 5) %% 8]}. His bare chest and his bare thighs. Nude but for the stockings.`

const PARTS = ['Her chest bare, her hips and legs bare, her feet bare',
               'Her bare shoulders forward, her thighs apart, her toes curled',
               'Her breasts hanging, her waist dipped, her heels lifted',
               'Her torso upright, her knees wide, her soles flat',
               'Her ribs stretched, her hips rolled, her ankles crossed',
               'Her collarbones sharp, her stomach taut, her feet braced',
               'Her back arched, her legs folded under her, her toes pointed',
               'Her chest against the sheets, her hips raised, her feet apart']

let made = 0
globalThis.fetch = async (url, opts) => {
  const body = JSON.parse(opts.body)
  asked.push(body)
  const lines = /Lay out the stages/.test(body.instruction)
    ? [{ label: '1-4', prompt: 'dressed, standing by the bed' },
       { label: '5-8', prompt: 'he is behind her, penetrating her from behind' }]
    : Array.from({ length: body.n }, () => ({ label: '', prompt: LINE(made++) }))
  return { ok: true, status: 200, json: async () => ({ lines }) }
}

// The module reports progress on stdout; the payload is the last line and nothing
// else, so the probe keeps its own writer and silences the rest.
const say = (x) => process.stdout.write(JSON.stringify(x))
console.log = () => {}
console.info = () => {}
console.warn = () => {}

const rows = await shootLines('a shoot that ends in penetration', 'a room', 'a dress', 8,
                              null, 'couple')
const writes = asked.filter((b) => /Write one photograph per object/.test(b.instruction))
const actRound = writes.filter((b) => /EXPLICIT STRETCH OF THE SHOOT/.test(b.instruction))
say({
  rows: rows.length,
  wardrobeBlank: rows.every((r) => r.wardrobe === ''),
  fieldsAsked: writes.every((b) => Array.isArray(b.fields) && b.fields.includes('him')),
  rounds: writes.length,
  // The register rides on the round whose stages reach the act, and on no other.
  actRounds: actRound.length,
  registerOnActRound: actRound.every((b) => b.register === 'explicit'),
  registerOffClothedRound: writes.filter((b) => !/EXPLICIT STRETCH/.test(b.instruction))
    .every((b) => b.register === ''),
})
"""


@pytest.fixture(scope="module")
def shot(tmp_path_factory) -> dict:
    script = SHOOT_PROBE % {"src": (ROOT / "frontend/src/enhance.js").as_posix()}
    return _node_json(script, tmp_path_factory.mktemp("shoot"))


def test_the_writer_runs_end_to_end(shot):
    """A ReferenceError anywhere on this path fails here, which is the only thing
    that would have caught `reachesTheAct` being used and never imported."""
    assert shot["rows"] == 8, shot
    assert shot["wardrobeBlank"], shot


def test_the_writer_asks_for_fields(shot):
    """His body is a field of its own: 83 per cent of lines describe him this way
    against 18 as prose."""
    assert shot["fieldsAsked"], shot


def test_a_shoot_that_becomes_explicit_gets_the_register_when_it_does(shot):
    """Session 196: reach `couple` never received the register in any round,
    because the only gate was `bare`, which is true for reach `explicit` alone.
    Its last three photographs came back as a pose with no act named."""
    assert shot["actRounds"] >= 1, shot
    assert shot["registerOnActRound"], shot
    assert shot["registerOffClothedRound"], shot


# ---------------------------------------------------------------------------
# Two photographs that are one photograph.
#
# Sessions 200 and 201, read by the user as "the last poses are all the same".
# The two lines below are the real 17 and 18 of both, cut to their opening: the
# camera has not moved, both bodies are where they were, and every word that
# changed is a word about tempo — which a still photograph does not have.

SAME_TWICE_A = (
    "Taken from across the room at shelf height, a three-quarter photograph from the knees "
    "up. she is bent forward at the waist with her weight braced on both hands flat on the "
    "sofa cushion, his pelvis pressed flush against her bottom, his penis inside her, two "
    "people in frame, his hips rocking forward into hers in a steady rhythm, her lips parted."
)

SAME_TWICE_B = SAME_TWICE_A.replace("rocking forward into hers in a steady rhythm",
                                    "snapping forward into hers in short thrusts")

# The same arrangement genuinely re-shot: the camera has moved and so has she.
MOVED_ON = (
    "Overhead camera directly above the sofa, a waist-up photograph. she is kneeling on the "
    "seat with her weight dropped onto both forearms, her knees spread wide, his penis inside "
    "her, two people in frame, one of his hands splayed across her lower back, her mouth open."
)

REPEAT_PROBE = """
import { problemsWith } from '%(src)s'

const a = %(a)s
const b = %(b)s
const moved = %(moved)s

const tag = (line, previous) => problemsWith(line, previous, 400).map((p) =>
  /^It is the photograph before it, shot again/.test(p) ? 'SAME_PHOTOGRAPH'
  : /^Its camera has not moved/.test(p) ? 'SAME_CAMERA'
  : p.slice(0, 40))

console.log(JSON.stringify({
  tempoOnly: tag(b, a),
  movedOn: tag(moved, a),
  alone: tag(b, ''),
}))
"""


@pytest.fixture(scope="module")
def repeated(tmp_path_factory) -> dict:
    script = REPEAT_PROBE % {"src": (ROOT / "frontend/src/enhance.js").as_posix(),
                             "a": json.dumps(SAME_TWICE_A),
                             "b": json.dumps(SAME_TWICE_B),
                             "moved": json.dumps(MOVED_ON)}
    return _node_json(script, tmp_path_factory.mktemp("repeat"))


def test_a_line_that_differs_only_in_tempo_is_the_line_before_it(repeated):
    """The complaint that would have caught sessions 200 and 201 before they were
    shot: ten photographs, two arrangements, and the words between them were
    `rocking`, `snapping`, `steady` and `faster`."""
    assert "SAME_PHOTOGRAPH" in repeated["tempoOnly"], repeated
    assert "SAME_CAMERA" in repeated["tempoOnly"], repeated


def test_a_photograph_that_moved_on_is_not_flagged(repeated):
    """The check has to stay quiet on the shoot it is meant to allow: same two
    bodies, same act, but the camera and the weight have both moved."""
    assert repeated["movedOn"] == [], repeated


def test_the_first_line_of_a_shoot_has_nothing_to_repeat(repeated):
    """`previous` is empty for photograph one, and an empty previous is not a
    photograph every line is a copy of."""
    assert repeated["alone"] == [], repeated
