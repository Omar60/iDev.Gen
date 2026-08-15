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

const words = (t) => t.trim().split(/\\s+/).length
const pad = '%(padding)s'.repeat(2)
const short = %(short)s
const long = short.replace('two people in frame', pad + ' two people in frame')
const clothed = %(clothed)s.replace('her lips parted.', pad + ' her lips parted.')

const tag = (line, limit) => problemsWith(line, '', limit).map((p) =>
  /^It is \\d+ words long, and a photograph with two people/.test(p) ? 'TWO_PEOPLE_LONG'
  : /^It is \\d+ words long, half as long again/.test(p) ? 'SHOOT_LONG'
  : /says nothing about/.test(p) ? 'BODY_WALK'
  : p.slice(0, 40))

console.log(JSON.stringify({
  shortWords: words(short), longWords: words(long), clothedWords: words(clothed),
  short: tag(short, 200), long: tag(long, 200), clothed: tag(clothed, 200),
  // The whole-body walk, on a two-person line that names no chest, no legs and
  // no feet: the exemption is the only thing standing between it and three
  // complaints, so a broken exemption shows up here and nowhere else.
  bare: tag('Taken from directly behind her, a waist-up photograph, of a naked man '
            + 'behind her, his penis inside her, two people in frame, her mouth open.', 200),
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
                      "short": json.dumps(TWO_PERSON_LINE),
                      "clothed": json.dumps(CLOTHED_LINE),
                      "padding": " " + PADDING.strip()}
    return _node_json(script, tmp_path_factory.mktemp("shootchecks"))


def test_a_two_person_line_is_exempt_from_the_whole_body_walk(checks):
    """The regex with the backspace in it matched nothing, so this was the state
    of every explicit line ever written: three complaints, three repairs, and the
    repair's job was to add the words that lose the second body."""
    assert "BODY_WALK" not in checks["bare"], checks["bare"]


def test_a_two_person_line_is_capped_at_eighty_words(checks):
    assert checks["shortWords"] <= 80 < checks["longWords"], checks
    assert checks["short"] == [], checks["short"]
    assert checks["long"] == ["TWO_PEOPLE_LONG"], checks["long"]


def test_the_cap_is_the_second_body_and_not_the_length(checks):
    """The same words with nobody else in them are held to the shoot's own limit,
    which is what `lengthLimit` measures and what every clothed line lives by."""
    assert checks["clothedWords"] > 80, checks
    assert "TWO_PEOPLE_LONG" not in checks["clothed"], checks["clothed"]
    assert "SHOOT_LONG" not in checks["clothed"], checks["clothed"]


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
