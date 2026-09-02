"""Ask the vision model where the camera stood, without telling it what was asked.

Usage: python scripts/judge_camera.py 230 [--repeat 1] [--base http://127.0.0.1:8777]

Why blind: the interesting number is not whether a photograph can be read as
matching its line, it is whether someone who never saw the line reads it the same
way. A judge that is shown the prompt agrees with the prompt.

So the judge is handed one photograph and one fixed question with a closed
vocabulary, and nothing else — no prompt, no wardrobe, no shoot, not even the
knowledge that a camera position was asked for. The comparison against what the
line asked happens here, after the answer is in.

It goes through the app's own `/api/enhance` with `shot_id`, which is the path
the app already uses for the vision model: the photo never leaves the machine
except the way the app already sends it, and no key is handled here.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

# The catalogue, by the opening words of the clause the writer was handed. Kept
# here rather than imported from kinds.js because this reads a shoot that has
# already been written: what matters is what the line says, not what the plan
# meant. See CAMERA_POSITIONS in frontend/src/kinds.js.
ASKED = [
    # Longest first: `overhead camera directly above her and behind her head`
    # must not be eaten by the bare form it extends.
    ("overhead", "overhead camera directly above her and behind her head"),
    ("overhead", "overhead camera directly above her"),
    ("overhead", "high camera looking steeply down at her from her right side"),
    ("overhead", "high camera looking steeply down at her"),
    ("floor", "low-angle shot from the floor at her feet"),
    ("floor", "low-angle shot from the floor behind her"),
    ("floor", "low-angle shot from the floor in front of her"),
    ("shoulder", "taken from behind her left shoulder"),
    ("shoulder", "taken from behind her right shoulder"),
    ("behind", "taken from directly behind her"),
    ("side", "taken from her right side"),
    ("side", "taken from her left side"),
    ("front", "taken from her right front"),
    ("front", "taken from directly in front of her"),
    # The candid forms, from `shoot_candid_cameras.py`. They name where the PHONE
    # is rather than where a camera stands, which is the whole question, so the
    # family here is what the mount asks for and not what any verified head word
    # promises.
    ("overhead", "phone propped on a high shelf across the room, looking down at her"),
    ("overhead", "phone held above her in his hand, looking straight down at her"),
    ("floor", "phone set down on the carpet at her feet, tipped up toward her"),
    ("front", "phone held out at arm's length in front of her face"),
    ("front", "mirror selfie, the phone up in her right hand"),
    ("front", "taken from an arm's length in front of her face"),
    # Session 247, the behind and shoulder families in the phone vocabulary.
    ("behind", "phone propped on the shelf behind her, facing her back"),
    ("behind", "phone in his hand behind her, pointed at her back"),
    ("behind", "phone held out behind her at arm's length, pointed back at her"),
    ("shoulder", "phone in his hand just behind her left shoulder, pointed past it"),
    ("shoulder", "mirror selfie with her back to the mirror, looking over her shoulder"),
    ("shoulder", "phone propped on a shelf behind her left shoulder"),
    ("shoulder", "phone propped on a shelf behind her right shoulder"),
    ("shoulder", "phone in his hand just behind her right shoulder, pointed past it"),
    # The bare noun phrases Qwen's multiangle node emits, from
    # `shoot_qwen_camera_words.py`. Longest first, as everywhere in this table:
    # `front view` must not eat `front-right quarter view`. The position question
    # has no word for a FRONT three-quarter, so `front-right` is scored `front`
    # here and the turn table below is what actually reads that arm.
    ("shoulder", "back-right quarter view"),
    ("shoulder", "back-left quarter view"),
    ("front", "front-right quarter view"),
    ("front", "front-left quarter view"),
    ("side", "right side view"),
    ("side", "left side view"),
    ("behind", "back view"),
    ("front", "front view"),
]

# What the same clauses ask of the HORIZONTAL alone, for `--question side`. A
# form whose height is verified carries no horizontal at all - `Overhead camera
# directly above her` is answered `overhead` by the position question whether the
# camera ended up over her face or over her heels, so the one thing a tail form
# is testing is the one thing that question cannot see. `None` means the clause
# asks nothing horizontal and the arm is scored on the position question only.
SIDE_ASKED = [
    ("behind", "overhead camera directly above her and behind her head"),
    (None, "overhead camera directly above her"),
    ("side", "high camera looking steeply down at her from her right side"),
    (None, "high camera looking steeply down at her"),
    ("front", "low-angle shot from the floor at her feet"),
    ("behind", "low-angle shot from the floor behind her"),
    ("front", "low-angle shot from the floor in front of her"),
    ("behind", "taken from behind her left shoulder"),
    ("behind", "taken from behind her right shoulder"),
    ("behind", "taken from directly behind her"),
    ("side", "taken from her right side"),
    ("side", "taken from her left side"),
    ("front", "taken from her right front"),
    ("front", "taken from directly in front of her"),
    # Qwen's bare spellings. `SIDE_WORDS` is three-way, so a front quarter and a
    # front view are both `front` and the two back quarters are both `behind`.
    ("behind", "back-right quarter view"),
    ("behind", "back-left quarter view"),
    ("front", "front-right quarter view"),
    ("front", "front-left quarter view"),
    ("side", "right side view"),
    ("side", "left side view"),
    ("behind", "back view"),
    ("front", "front view"),
]

# What each clause asks of the TURN question, whose four words separate the two
# things `--question position` cannot: a front three-quarter from a front view,
# and a real ninety-degree profile from either. This table is the reason
# `--question turn` is scorable at all - it used to fall through to the camera
# table and compare a family word (`front`, `side`) against turn words
# (`facing`, `profile`), which can never agree.
#
# Longest first. Both spellings of every arm in `shoot_qwen_camera_words.py` are
# here, because the whole bench is one wording read against another.
TURN_ASKED = [
    ("threequarter", "taken from behind her right shoulder"),
    ("threequarter", "taken from behind her left shoulder"),
    ("threequarter", "taken from her right front"),
    ("facing", "taken from directly in front of her"),
    ("back", "taken from directly behind her"),
    ("profile", "taken from her right side"),
    ("profile", "taken from her left side"),
    ("threequarter", "back-right quarter view"),
    ("threequarter", "back-left quarter view"),
    ("threequarter", "front-right quarter view"),
    ("threequarter", "front-left quarter view"),
    ("profile", "right side view"),
    ("profile", "left side view"),
    ("back", "back view"),
    ("facing", "front view"),
]

# One word back, from a closed list, with the list defined in terms of what is
# visible rather than in the words the prompt used — a judge handed the prompt's
# own vocabulary can match it on the words alone.
QUESTION = """Look at this photograph and answer with ONE word and nothing else.

Where was the camera in relation to the woman when it was taken?

front — you see the front of her body and her face is toward the lens
side — you see her from the left or the right, her body roughly in profile
shoulder — you see her from behind and slightly to one side, her back three-quarters turned
behind — you see her back, the camera directly behind her
overhead — the camera is clearly above her looking down, a high angle
floor — the camera is clearly below her looking up, a low angle

If the camera is both high or low AND to one side, answer with the height:
overhead or floor. Answer with exactly one of: front, side, shoulder, behind,
overhead, floor."""

WORDS = ("front", "side", "shoulder", "behind", "overhead", "floor")

# The second question, for the one family the first cannot resolve. Asked for a
# full profile the sampler renders a three-quarter turn, and the six words above
# have no name for that, so the judge is forced to call it front or side and the
# miss is invisible. This asks only how far her body is turned, and it is the
# question to use when every arm asks for the same position and what is being
# compared is the wording. `TURN_WORDS` is ordered longest-first so `threequarter`
# is not eaten by `three` matching somewhere earlier.
TURN = """Look at this photograph and answer with ONE word and nothing else.

How far is the woman's body turned away from the camera? Judge it by her torso,
not by her face.

facing — her chest is square to the camera, both shoulders equally visible
threequarter — her body is turned part way, one shoulder nearer the camera than
  the other, but both her breasts and the front of her chest are still visible
profile — her body is turned a full ninety degrees, her chest pointing at the
  edge of the frame, her far shoulder hidden behind the near one
back — she is turned away, you see her back

Answer with exactly one of: facing, threequarter, profile, back."""

TURN_WORDS = ("threequarter", "three-quarter", "profile", "facing", "back")

# The third question, and the one a tail form is shot for. The position question
# is told to answer with the HEIGHT whenever a photograph is both high and to one
# side, which is right when the catalogue's off-eye forms carry no horizontal -
# and blind to the only thing that changes when one is hung on the end. So this
# asks the horizontal alone and says nothing about height. Left and right are one
# word on purpose: a judge that has to tell her left from her right is answering a
# harder question than the one being asked.
SIDE = """Look at this photograph and answer with ONE word and nothing else.

Ignore how high or low the camera is. Going around her, which way was it facing
her from?

front - you see the front of her body, her chest and the front of her legs
side - you see her from her left or her right, neither her front nor her back
  squarely toward the lens
behind - you see her back, the camera is behind her

Answer with exactly one of: front, side, behind."""

SIDE_WORDS = ("front", "side", "behind")

# The fourth question, and it exists for `candid` alone. That manner's line says
# the phone is almost never in the picture - `a gadget floating in them is as
# broken as forty studio ones` - and its camera clauses are the one place the
# word `phone` is unavoidable, because the position IS the phone. So every
# candid form has to be scored twice: did it reach the position, and did it
# paint the device. Nothing here says what was asked for, the same way the other
# three say nothing.
DEVICE = """Look at this photograph and answer with ONE word and nothing else.

Is a phone, a camera or any other handheld device visible anywhere in the image,
including in a mirror or in her hand?

yes - a phone or camera is visible somewhere in the image
no - no phone or camera is visible

Answer with exactly one of: yes, no."""

DEVICE_WORDS = ("yes", "no")

# The two candid clauses that put the phone in her hand facing her, where the
# manner allows it to be seen. Every other clause - including the ones where the
# phone is doing the photographing from a shelf, the carpet or his hand - asks
# for no device in the frame at all, so `no` is the default and this list is the
# exception.
# The fifth question, for the kiss frame. The eyes are the whole reason that
# frame is planned in code rather than asked for in prose - a kiss blown at the
# camera renders easily and the eyes come back open - so this asks the eyes alone
# and says nothing about lips, hands or where the camera is.
KISS = """Look at this photograph and answer with ONE word and nothing else.

What are the woman's eyes doing?

closed - both of her eyes are shut
wink - one eye is shut and the other is open
open - both of her eyes are open

Answer with exactly one of: closed, wink, open."""

KISS_WORDS = ("closed", "wink", "open")

# What the line asked of the eyes, read off the wording KISS_FRAMES hands over.
KISS_ASKED = [
    ("closed", "her eyes are completely closed"),
    ("wink", "she is winking"),
    ("open", "both eyes open and looking straight at the lens"),
]

# The sixth question, and the one an explicit shoot is actually shot for. Every
# other question here is about where the camera stood; this one asks whether the
# photograph contains the thing the line asked for at all. It exists because the
# `selfie` manner claims two bodies and an act in nearly every frame, and a shoot
# that comes back as a woman alone making a face has failed in a way no camera
# question can see.
ACT = """Look at this photograph and answer with ONE word and nothing else.

How many people are in it, and what are they doing?

alone - only one person is in the photograph
together - two or more people, close or touching, but not having sex
sex - two or more people having sex, their bodies joined

Answer with exactly one of: alone, together, sex."""

ACT_WORDS = ("alone", "together", "sex")

# What the LINE asked for, read off the line. Deliberately crude: any of these
# words in a line means the line asked for the act, and the judge is what decides
# whether the photograph has it. `two people in frame` is here because that is
# the phrase every explicit line in this project carries.
ACT_ASKED = (
    "penetrat", "inside her", "his penis in", "fucking", "joined",
    "riding him", "thrusting",
)

# The seventh question, and the one the `selfie` manner is actually for. The
# device question asks whether a phone was painted, and session 264 showed that
# is the wrong proxy: rated by hand, the photographs that read as HERS are the
# ones the device judge called deviceless, while the one frame with the phone
# plainly in her hand reads as someone else holding the camera - outside a
# mirror, a visible device means a visible photographer. So this asks the only
# thing that matters, and says nothing about phones or arms.
HOLDER = """Look at this photograph and answer with ONE word and nothing else.

Who was holding the camera?

herself - she took it of herself: her own arm reaches toward the lens, or it is
  her reflection in a mirror
someone - another person is holding the camera, or it is resting on something

Answer with exactly one of: herself, someone."""

HOLDER_WORDS = ("herself", "someone")

# The forms that ask to be read as hers. Everything else in either catalogue is
# a camera somebody or something else is holding, including the propped phone -
# a phone on a shelf is a tripod as far as the photograph is concerned.
HOLDER_SELF = (
    "phone held out at arm's length in front of her face",
    "mirror selfie, the phone up in her right hand",
    "mirror selfie with her back to the mirror, looking over her shoulder",
    "phone held low in her own hand at her chest",
    "phone held above her face in her own outstretched hand",
    # The device word taken out, which is a form the catalogue does not carry and
    # `shoot_selfie_cameras.py` shoots as S5. Without this line its arm was scored
    # against `someone` and passed for the wrong reason: it asks to be read as hers
    # exactly as much as the form it is paired with.
    "taken from an arm's length in front of her face",
)

# The eighth question. `act` asks whether there are two bodies and whether they
# are joined; this asks WHICH arrangement of the two, which is the only way to
# find out whether a planted one rendered. Its vocabulary is the six in
# `ARRANGEMENTS`, worded for someone looking at a photograph rather than for
# someone writing one - `ontop` and not `astride`, because the judge is told
# nothing about what was asked.
ARRANGEMENT = """Look at this photograph and answer with ONE word and nothing else.

Two people are having sex in it. How are their bodies arranged?

ontop - she is on top of him, facing him
away - she is on top of him, facing away from him
under - she is on her back or her side underneath him, he is over her, facing her
allfours - she is on her hands and knees, he is behind her
spooning - both of them are lying on their sides, he is behind her
standing - at least one of them is standing up

Answer with exactly one of: ontop, away, under, allfours, spooning, standing."""

ARRANGEMENT_WORDS = ("ontop", "away", "under", "allfours", "spooning", "standing")

# What the LINE asked for, by the wording `ARRANGEMENTS` hands over verbatim, and
# the word the judge would use for it. A line carrying none of these is not
# scored: it is a photograph the plan left to the writer, and there is no
# expectation to compare it against.
ARRANGEMENT_ASKED = [
    # The anchor arms of `shoot_arrangements.py` first, because they are longer
    # wordings of the same two arrangements and the plain forms below would
    # otherwise match some of them first. Each one is the phrase that carries the
    # ANCHOR - the edge, the table, the mattress, the pillow - so a hand-written
    # line that says the arrangement some other way is still read as unplanted.
    ("under", "on her back across the edge of the bed"),
    ("under", "on her back on the table"),
    ("spooning", "lying down flat along the mattress"),
    ("spooning", "her head is down on the pillow"),
    ("ontop", "astride him with her knees"),
    ("away", "astride him facing away"),
    ("under", "on her back with her legs open and he is over her"),
    ("spooning", "both on their sides with him behind her"),
    ("standing", "front to the wall and one leg raised"),
    ("allfours", "on all fours on the bed and he is kneeling behind"),
]

# The ninth question, and the one `shoot_technique_anchor.py` is shot for. The
# `technique` field's whole claim to being WRITTEN rather than picked out of a
# menu by code is that the writer ties a defect to a place - `softly out of
# focus where her hand moved`. So this asks where the blur IS, and nothing about
# whether there is any: an arm that names motion blur and lands it nowhere in
# particular has not earned the sentence that names the hand.
BLUR = """Look at this photograph and answer with ONE word and nothing else.

Where is the motion blur or softness in this image?

hand - one hand, arm or the area right around it is blurred while her face stays sharper
spread - the whole image is about equally soft or blurred
face - her face or head is the blurred part
none - nothing looks blurred; the image is sharp throughout

Answer with exactly one of: hand, spread, face, none."""

BLUR_WORDS = ("hand", "spread", "face", "none")

# The tenth, and the companion control. `blur` can only be read on a photograph
# that has some defect in it at all, and the `none` arm of that session carries
# no Technique block - only the look's own capture clause. If grain comes back
# the same across all three arms, the field is adding nothing the look was not
# already doing, which is the finding that deletes it.
GRAIN = """Look at this photograph and answer with ONE word and nothing else.

How much visible grain, sensor noise or speckle is in this image?

heavy - obvious grain or noise across most of the image
some - visible grain in the darker areas only
none - the image looks clean, with no visible grain or noise

Answer with exactly one of: heavy, some, none."""

GRAIN_WORDS = ("heavy", "some", "none")

# The eleventh question, for `shoot_technique_surface.py`. The candid manner
# forbids the `technique` field naming anything in the room, and the note under
# the ban says a clause that names a corner of one `invents a different room`.
# That has never been shot: the evidence for it is a WRITER defect - an example
# reading `empty room down one side` came back reworded as `empty bedspread`.
# Whether the PAINTER puts furniture in the photograph when the line names it is
# a different claim and this is the question that asks it. A table, because the
# look this is shot in has a sofa, a bed, a carpet and a lamp in it and no table
# at all, so a table in the frame was put there by the clause.
FURNITURE = """Look at this photograph and answer with ONE word and nothing else.

Is a table, desk or other flat-topped piece of furniture visible in the image?

yes - a table, desk or similar flat-topped surface is visible
no - no table or desk is visible

Answer with exactly one of: yes, no."""

FURNITURE_WORDS = ("yes", "no")

# The twelfth, and the one that replaces FURNITURE after session 279 showed the
# eleventh could not discriminate: the look asks for a bedside lamp, the model
# paints a bedside table under it, and the control answered `yes` 7 times in 8.
# An absent object has to be one the model will not supply unasked. A tiled
# kitchen counter in a carpeted bedroom is that, and it is the easiest possible
# case for the effect to show - if a clause about grain cannot drag a kitchen
# in, it is not furnishing anything.
KITCHEN = """Look at this photograph and answer with ONE word and nothing else.

Is any part of a kitchen visible - a tiled counter, a worktop, cupboards, a sink
or kitchen tiling?

yes - some part of a kitchen or a tiled counter is visible
no - no kitchen or tiled counter is visible

Answer with exactly one of: yes, no."""

KITCHEN_WORDS = ("yes", "no")

# The thirteenth. `kitchen` settled the easy half - a surface the model would
# never paint unasked gets painted when the field names it. The half that
# actually costs a shoot is the PLAUSIBLE surface: 16 of the 30 shipped clauses
# that name one name bed dressing, and the look already puts a bed in the room,
# so `is a bed visible` cannot separate the arms any more than `is a table
# visible` could. What the clause claims is not that a bed exists but WHERE it
# is - `a stretch of empty bedspread above her head` puts bedding in the top of
# the frame, and the control's bed sits off to one side at her hip. So the
# question is about the upper third and nothing else.
ABOVE = """Look at this photograph and answer with ONE word and nothing else.

Look only at the top part of the image, above the woman's head. What is there?

bedding — a bed, mattress, duvet, pillows or sheets
wall — a plain wall, a ceiling, a curtain or a window
other — furniture that is none of those, or nothing identifiable

Answer with exactly one of: bedding, wall, other."""

ABOVE_WORDS = ("bedding", "wall", "other")

# The fourteenth, and the one that finishes the plausible-surface question after
# `above` came back inconclusive: `a stretch of empty bedspread above her head`
# is impossible geometry for a waist-up frontal of a standing woman, and a
# contradiction renders as neither, so 0 of 8 could not tell harmless from
# impossible. This asks about a placement the line permits - the look already
# puts a bed against the far wall - so the arms differ by whether NAMING the
# thing brings it forward, not by whether it can exist.
BEDSIZE = """Look at this photograph and answer with ONE word and nothing else.

How much of the background behind the woman is taken up by a bed or bedding?

most - a bed or bedding fills most of the background behind her
edge - a bed is visible but only at one side or in a corner
none - no bed or bedding is visible behind her

Answer with exactly one of: most, edge, none."""

BEDSIZE_WORDS = ("most", "edge", "none")

DEVICE_YES = (
    "phone held out at arm's length in front of her face",
    "mirror selfie, the phone up in her right hand",
    "mirror selfie with her back to the mirror, looking over her shoulder",
    # The `selfie` manner's own two, where the phone is in her own hand and
    # pointed at her: the same shape as the arm's-length form above.
    "phone held low in her own hand at her chest",
    "phone held above her face in her own outstretched hand",
)


def _same(saw: str, want: str) -> bool:
    """One answer against one expectation, with the hyphen ignored.

    `TURN_WORDS` carries both `threequarter` and `three-quarter` because the
    model writes it both ways, and the alternation returns whichever it found -
    so a bare `==` scored half the profile bench as misses on punctuation. No
    other answer word contains a hyphen, so this is a no-op everywhere else.
    """
    return saw.replace("-", "") == want.replace("-", "")


def asked_of(prompt: str, table: list | None = None) -> str | None:
    """The answer the LINE asked for, read off the line itself.

    `None` back from the side table is not a failure to match: it is a clause
    that asks nothing horizontal, and the caller skips the photograph rather than
    scoring it against an expectation nobody wrote.
    """
    low = " ".join(prompt.split()).lower()
    for family, opening in (table or ASKED):
        if opening in low:
            return family
    return "?"


def post(base: str, path: str, body: dict | None = None, tries: int = 8,
         timeout: int = 300) -> dict:
    """Retried with a growing wait, because a judging pass is seventy calls to a
    hosted model and the connection resets constantly — one run died on the first
    photograph, a second died on the ninth after three resets in a row, and every
    one of those calls succeeded on a later attempt. Six tries and a backoff, not
    three and a flat five seconds: the resets arrive in bursts, so what a retry
    has to outlast is the burst.

    ONLY for calls that can be made twice. A reset can arrive after the server
    has already acted, so a retried POST that creates something creates it again:
    retrying `POST /api/sessions` through here made two extra draft sessions
    before the one that came back. Pass `tries=1` for anything that writes."""
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(tries):
        req = urllib.request.Request(base + path, data, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as exc:  # noqa: BLE001 - any transport failure is worth one more go
            if attempt == tries - 1:
                raise
            print(f"    retrying after {type(exc).__name__}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def get(base: str, path: str) -> dict:
    # Through `post`'s retry with no body: the resets come from the LOCAL backend
    # while it is running a render queue, not from the hosted judge, so a plain
    # read of the session is exactly as likely to be dropped as a judging call.
    return post(base, path, None)


def session_shots(base: str, sid: int) -> list[dict]:
    """The session's shots, over HTTP, or straight out of the database.

    `GET /api/sessions/{id}` answers with sixty to eighty kilobytes - every shot
    carries its whole prompt - and on this machine urllib takes a
    ConnectionResetError on a body that size while `curl` fetches the identical
    URL five times of five, headers matched. The server logs `200 OK` every
    time; what is lost is on the client side of the socket.

    That is a transport bug nobody needs to solve to read a bench. The four
    fields used below - `id`, `shot_label`, `prompt`, `filename` - are columns of
    the `shot` table, so a run that cannot fetch reads them itself and says so.
    The judging calls are small and go on using HTTP; this is only the one big
    read.
    """
    # Two tries and not `get`'s eight: the reset is instant and reproducible, so
    # the retry ladder here only spends five minutes proving what one more
    # attempt already showed, and the fallback below cannot fail.
    try:
        return post(base, f"/api/sessions/{sid}", None, tries=2, timeout=30)["shots"]
    except Exception as exc:  # noqa: BLE001 - the fallback exists for any of them
        db = Path(__file__).resolve().parent.parent / "data" / "idevgen.db"
        if not db.exists():
            raise
        print(f"    {type(exc).__name__} on the session fetch - reading {db.name} instead",
              file=sys.stderr)
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, shot_index, shot_label, prompt, filename FROM shot "
            "WHERE session_id = ? ORDER BY shot_index", (sid,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def judge(base: str, shot_id: int, question: str = QUESTION, words: tuple = WORDS) -> str:
    """One pass. A transport failure that outlives the backoff is recorded as a
    pass nobody could read, never raised.

    A judging run is seventy calls to a hosted model, and the hosted model has
    bad half-hours: one of them killed a thirty-three photograph run at the
    fifth photograph and threw away everything already read. A dead pass costs
    one pass; a dead run costs the batch."""
    try:
        lines = post(base, "/api/enhance",
                     {"instruction": question, "shot_id": shot_id, "n": 1})["lines"]
    except Exception as exc:  # noqa: BLE001 - any transport failure is one lost pass
        return f"unreadable:{type(exc).__name__}"
    said = (lines[0].get("prompt") if lines else "") or ""
    # The model answers with the word, sometimes inside a sentence. First hit wins,
    # and an answer with none of the six is kept as itself so it shows up as a miss
    # rather than being silently scored.
    hit = re.search("|".join(words), said.lower())
    return hit.group(0) if hit else f"unreadable:{said[:40]!r}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=int)
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--question",
                    choices=("position", "turn", "side", "device", "kiss", "act", "holder",
                             "arrangement", "blur", "grain", "furniture", "kitchen", "above", "bedsize"),
                    default="position",
                    help="position = which side of her the camera stands on, heights winning "
                         "over horizontals; turn = how far her body is turned, which is the only "
                         "way to see a three-quarter rendered for a profile; side = the "
                         "horizontal alone, ignoring height, which is the only way to see "
                         "whether the tail of an off-eye form landed; device = whether a phone "
                         "was painted into the photograph, which every candid form has to be "
                         "scored on as well as on its position; act = whether the photograph "
                         "has two bodies and the act in it at all, which is the question an "
                         "explicit shoot is shot for; holder = whether the photograph reads "
                         "as one she took of herself, which is the question the `selfie` "
                         "manner exists for and the one the device question was standing in "
                         "for badly; arrangement = which arrangement of the two bodies is in "
                         "the photograph, scored only on the lines a planted one was written "
                         "into; blur = WHERE the motion blur landed, which is the only way to "
                         "see whether tying a defect to a place does anything the bare word "
                         "does not; grain = how much grain is in the photograph at all, the "
                         "companion control that says whether the technique field adds "
                         "anything the look was not already doing")
    ap.add_argument("--static", action="store_true",
                    help="read the tables in this file and ignore the component "
                         "catalogue - required for a bench shoot whose clauses "
                         "were written by hand")
    ap.add_argument("--repeat", type=int, default=1,
                    help="judge each photograph this many times; the judge has its own "
                         "variance and one pass cannot see it")
    args = ap.parse_args()

    shots = [s for s in session_shots(args.base, args.session) if s.get("filename")]
    if not shots:
        print("no finished photographs in that session")
        return 1

    dynamic_camera_asked: list[tuple[str, str]] = []
    dynamic_arrangement_asked: list[tuple[str, str]] = []
    # The catalogue's families are not the judge's answer words. The 49 camera
    # rows imported on 2026-08-28 carry `side-level`, `rear`, `shoulder-level`,
    # `lens`, `medium` - none of which is one of the six words the position
    # question answers in, so a shoot whose clauses come from those rows scores
    # every photograph as a miss against a `want` no answer can equal. A bench
    # shoot writes its lines by hand anyway, which is what the tables above are
    # for, so `--static` skips the catalogue and reads them.
    if not args.static:
        try:
            comps = get(args.base, "/api/components?all=1")
            if isinstance(comps, list):
                for c in comps:
                    if c.get("slot") == "camera" and c.get("family") and c.get("wording"):
                        dynamic_camera_asked.append((c["family"].lower(), c["wording"].lower()))
                    elif c.get("slot") == "act" and c.get("family") and c.get("wording"):
                        dynamic_arrangement_asked.append((c["family"].lower(), c["wording"].lower()))
        except Exception:
            pass

    camera_asked = sorted(dynamic_camera_asked, key=lambda x: -len(x[1])) if dynamic_camera_asked else ASKED
    arrangement_asked = sorted(dynamic_arrangement_asked, key=lambda x: -len(x[1])) if dynamic_arrangement_asked else ARRANGEMENT_ASKED

    turn = args.question == "turn"
    question, words = {
        "turn": (TURN, TURN_WORDS),
        "side": (SIDE, SIDE_WORDS),
        "device": (DEVICE, DEVICE_WORDS),
        "kiss": (KISS, KISS_WORDS),
        "act": (ACT, ACT_WORDS),
        "holder": (HOLDER, HOLDER_WORDS),
        "arrangement": (ARRANGEMENT, ARRANGEMENT_WORDS),
        "blur": (BLUR, BLUR_WORDS),
        "grain": (GRAIN, GRAIN_WORDS),
        "furniture": (FURNITURE, FURNITURE_WORDS),
        "kitchen": (KITCHEN, KITCHEN_WORDS),
        "above": (ABOVE, ABOVE_WORDS),
        "bedsize": (BEDSIZE, BEDSIZE_WORDS),
    }.get(args.question, (QUESTION, WORDS))

    hits, rows, skipped = 0, [], 0
    for shot in shots:
        # The turn question was written for a bench where every arm asks for the
        # same thing - a profile - and the label carries which WORDING asked for
        # it, so the line was not what was compared and `profile` was hardcoded.
        # `shoot_qwen_camera_words.py` is the other shape: five different turns,
        # two spellings each, and there the line is exactly what is compared. So
        # the table is read first and the hardcoded word is what a line the table
        # does not name falls back to, which leaves every earlier bench scoring
        # the number it scored before.
        if turn:
            want = asked_of(shot["prompt"], TURN_ASKED)
            if want == "?":
                want = "profile"
        # Both of these are read per ARM and not per line: every shot is scored
        # against the same word, and what is compared is the rate the three arms
        # reach it. Only one arm asked for `hand` at all, and no arm asks for
        # `heavy` - the look asks for grain in all three.
        elif args.question == "blur":
            want = "hand"
        elif args.question == "grain":
            want = "heavy"
        # No arm asks for furniture. The rate each one reaches is what is read.
        elif args.question in ("furniture", "kitchen"):
            want = "no"
        # No arm asks for bedding overhead except the one being tested; the rate
        # each arm reaches it is what is read.
        elif args.question == "above":
            want = "bedding"
        elif args.question == "bedsize":
            want = "most"
        elif args.question == "kiss":
            want = asked_of(shot["prompt"], KISS_ASKED)
        elif args.question == "device":
            low = " ".join(shot["prompt"].split()).lower()
            want = "yes" if any(c in low for c in DEVICE_YES) else "no"
        elif args.question == "arrangement":
            want = asked_of(shot["prompt"], arrangement_asked)
            # `?` back means the line carries no planted arrangement at all, which
            # is most of a shoot: skipped rather than scored against nothing.
            if want == "?":
                want = None
        elif args.question == "holder":
            low = " ".join(shot["prompt"].split()).lower()
            want = "herself" if any(c in low for c in HOLDER_SELF) else "someone"
        elif args.question == "act":
            low = " ".join(shot["prompt"].split()).lower()
            want = "sex" if any(w in low for w in ACT_ASKED) else "together"
        elif args.question == "side":
            want = asked_of(shot["prompt"], SIDE_ASKED)
        elif args.question == "turn":
            want = asked_of(shot["prompt"], TURN_ASKED)
        else:
            want = asked_of(shot["prompt"], camera_asked)
        # A clause with no horizontal in it is not scored on the horizontal.
        if want is None:
            skipped += 1
            continue
        saw = [judge(args.base, shot["id"], question, words) for _ in range(args.repeat)]
        # A photograph counts as obeyed when the majority of passes agree with the
        # line. At --repeat 1 that is simply the one answer.
        agreed = sum(1 for s in saw if _same(s, want))
        ok = agreed * 2 > len(saw)
        hits += ok
        rows.append((shot.get("shot_label") or shot["shot_index"] + 1, want, saw, ok))
        print(f'{str(rows[-1][0]):>4} | asked {want:<9} | saw {", ".join(saw):<28} | {"OK" if ok else "--"}')

    passes = [p for _, _, saw, _ in rows for p in saw]
    lost = [p for p in passes if p.startswith("unreadable:")]
    if lost:
        print(f"\n{len(lost)} of {len(passes)} passes were unreadable - refusals and transport "
              f"failures both land here, and a run with many of them is not a measurement")
    print(f"\nobeyed {hits}/{len(rows)}"
          + (f" ({skipped} skipped: the line asks nothing this question can score)"
             if skipped else ""))
    by_family: dict[str, list[int]] = {}
    for _, want, _, ok in rows:
        by_family.setdefault(want, []).append(ok)
    for family, oks in sorted(by_family.items()):
        print(f"  {family:<9} {sum(oks)}/{len(oks)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
