"""Do the camera words Qwen's multiangle node emits work as plain t2i prompt text?

The node (`custom_nodes/comfyui-qwenmultiangle/nodes.py`) computes nothing. It
maps three numbers to three phrases and concatenates them:

    prompt = f"<sks> {h_direction} {v_direction} {distance}"

8 horizontals x 4 verticals x 3 distances = 96 strings. Ninety-two of them are
already answered here and are not shot:

  * the distance third (`wide shot`, `medium shot`, `close-up`) is a crop term,
    and the crop law measured 2026-08-28 says the frame reaches the LOWEST body
    part the line names wherever it is named - the framing word moved nothing
    12/12. This bench line names stockings down to her feet. `close-up` is
    banned outright besides: a close frame on the face renders oral sex 32/32 on
    all nine checkpoints.
  * the vertical third is already in the catalogue as `low angle`, `eye level`
    and `high angle`. Only `elevated shot` is new, and a height is not what this
    asks.
  * `<sks>` is the multiangle LoRA's trigger. There is no such LoRA in a t2i
    session, and this project already puts `zchar_jir.` at the head of the line.

What is left is the eight horizontals, and four of those are the mirror of the
other four. So: five horizontals, and the question is not whether the CAMERA
works - three of the five are verified families - but whether QWEN'S SPELLING
works. The node emits bare noun phrases with no subject in them. The two
wordings that died on 2026-08-28 both died by making something other than the
person the subject of the main verb, so a bare `right side view` could fail for
a reason that has nothing to do with where the camera is.

That is the confound this bench is built to remove. Every horizontal is shot
TWICE on the same seed: once in Qwen's spelling, once in the house clause. A
bare form that fails where its clause lands is a spelling result. Both failing
together is a camera result.

    arm         Qwen's spelling             the house clause
    ------------------------------------------------------------------------
    front       front view                  Taken from directly in front of her
    front-right front-right quarter view    Taken from her right front, ...
    right-side  right side view             Taken from her right side, ...
    back-right  back-right quarter view     Taken from behind her right ...
    back        back view                   Taken from directly behind her

Plus ONE arm carrying the node's literal output, `<sks>` and distance included
and no framing added, because "does it work if I just paste it" is the question
that was actually asked. It differs from its bare twin in three ways at once and
is a probe, not an isolating arm.

TWO CHECKPOINTS, because the prize is the ninety-degree profile and Krea 2 is
measured as unable to reach it from any wording, while finepornV4 renders one
from the same clause. Running this on the Moody premium alone buys a null in the
one row that matters. Same line, same seeds, same arms on both.

Everything else is `shoot_camera_forms.py` verbatim - the look, the framing, the
pose, the wardrobe, the expression, the three seeds - and it is imported rather
than copied so the two benches cannot drift apart.

Usage: python scripts/shoot_qwen_camera_words.py [--base URL] [--dry-run]
Two sessions are created as drafts and NOT run. Judge afterwards with:
    python scripts/judge_camera.py <id> --question turn --static
    python scripts/judge_camera.py <id> --question side --static
`--static` is required: the judge prefers the component catalogue when it can
reach it, and the 49 imported camera rows carry families (`side-level`, `rear`,
`shoulder-level`) that are not the six words the judge answers in.
"""
from __future__ import annotations

import argparse
import sys

from shoot_camera_forms import FRAMING, LOOK, REST, SEEDS, SETTINGS, create_session, prompt_for

# key, Qwen's bare string, the house clause, what the arm is for.
HORIZONTALS = [
    ("front", "front view",
     "Taken from directly in front of her",
     "control - both spellings must land or the rig is wrong"),
    ("front-right", "front-right quarter view",
     "Taken from her right front, her body turned three-quarters toward the camera",
     "the family the directed catalogue does not have"),
    ("right-side", "right side view",
     "Taken from her right side, her body in full profile",
     "the prize - 0 of 9 on each checkpoint in sessions 326 and 327, three seeds"),
    ("back-right", "back-right quarter view",
     "Taken from behind her right shoulder, her back three-quarters to the camera",
     "the shoulder family, a catalogue row in candid and selfie"),
    ("back", "back view",
     "Taken from directly behind her",
     "control on the far side - a verified family in a bare spelling"),
]

# The node's literal output for the profile at eye level, mid zoom. Pasted whole,
# with no framing added after it: the string already carries `medium shot`, and
# appending the bench framing would be two crop terms arguing.
AS_SHIPPED = "<sks> right side view eye-level shot medium shot"

# name, the file, and the settings that differ. Both were read off what has
# actually been run: the premium off session 321, finepornV4 off `config.json`.
CHECKPOINTS = [
    ("premium", "Moody-Krea-Mix-premium_00002__clean_nvfp4.safetensors",
     {"steps": 8, "sampler": "euler_ancestral", "scheduler": "beta"}),
    ("finepornV4", "finepornV4INT8NVFP4BF16_v4Nvfp4.safetensors",
     {"steps": 12, "sampler": "er_sde", "scheduler": "beta"}),
]

# --profile: the one row of the first pass worth more seeds. Sessions 326 and
# 327 put the ninety-degree profile at 0 of 9 on each checkpoint - every
# photograph came back `threequarter` or `facing`, in all three spellings - and
# three seeds an arm is not enough to call a wording dead. This shoots the same
# three profile spellings at ten, with the front control carried along at ten so
# a second flat result is readable as the wording failing rather than the rig.
#
# The first three seeds ARE the first pass's three, so the same wording on the
# same noise can be laid against 326 and 327 directly; the other seven are new.
PROFILE_SEEDS = list(SEEDS) + [
    123454321, 606060606, 918273645, 555000555, 314159265, 271828182, 141421356,
]

# Which horizontals the deeper pass shoots, and in which spellings. The profile
# row goes in both, because the spelling is half the question. `front` goes in
# ONE, because it is only here to prove the rig is alive at ten seeds and the
# first pass already tied its two spellings 3/3 against 3/3 on both checkpoints -
# a second control arm would be forty more photographs buying a number that is
# already in.
#
# `Z-asshipped` is not keyed here; it carries its own text and is always shot.
PROFILE_ARMS = {"front": ("H",), "right-side": ("Q", "H")}


def prompt_as_shipped() -> str:
    """The literal node output in the camera slot, with no framing appended.

    `prompt_for` puts `, {FRAMING}.` after whatever it is handed; this is the
    same line with that clause left out, which is the only difference.
    """
    return f"zchar_jir.\n\n{LOOK}\n\nAngle & Framing:\n{AS_SHIPPED}.\n{REST}"


def arms(profile: bool = False) -> list[tuple[str, str, str]]:
    """label, the text that goes in the camera slot, why - in the order shot.

    The controls are not first here the way they are in `shoot_camera_forms.py`.
    The pairing is what this bench reads, so the two spellings of one horizontal
    sit next to each other and a contact sheet shows them side by side.
    """
    out = []
    for key, bare, clause, why in HORIZONTALS:
        want = PROFILE_ARMS.get(key, ()) if profile else ("Q", "H")
        if "Q" in want:
            out.append((f"Q-{key}", bare, f"qwen spelling: {why}"))
        if "H" in want:
            out.append((f"H-{key}", clause, f"house spelling: {why}"))
    out.append(("Z-asshipped", AS_SHIPPED,
                "the node's literal output, trigger and distance included, no framing"))
    return out


def shots_for(profile: bool = False) -> list[dict]:
    seeds = PROFILE_SEEDS if profile else SEEDS
    return [
        {"label": f"{label}-s{i + 1}",
         "prompt": prompt_as_shipped() if label == "Z-asshipped" else prompt_for(camera),
         "verbatim": True, "seed": seed, "count": 1}
        for label, camera, _ in arms(profile)
        for i, seed in enumerate(seeds)
    ]


def _selfcheck() -> None:
    """The one runnable check: the arms and the prompts they build.

    Runs under --dry-run and before either session is posted. It fails if a
    camera clause goes missing from the line it is supposed to be the only
    difference in, if the as-shipped arm picks up the framing it is defined by
    not having, or if the two spellings of a horizontal stop being paired on the
    same seeds.
    """
    a = arms()
    assert len(a) == 2 * len(HORIZONTALS) + 1, len(a)
    for label, camera, _ in a:
        p = prompt_as_shipped() if label == "Z-asshipped" else prompt_for(camera)
        assert p.count(camera) == 1, f"{label}: camera text appears {p.count(camera)} times"
        assert LOOK in p and REST in p, f"{label}: lost the shared bench line"
    shipped = prompt_as_shipped()
    assert FRAMING not in shipped, "the as-shipped arm must carry no added framing"
    assert AS_SHIPPED in shipped, "the as-shipped arm lost the literal node output"
    for key, bare, clause, _ in HORIZONTALS:
        assert bare != clause, key
    for is_profile, seeds in ((False, SEEDS), (True, PROFILE_SEEDS)):
        shots = shots_for(is_profile)
        assert len(shots) == len(arms(is_profile)) * len(seeds), len(shots)
        seeds_of: dict[str, list[int]] = {}
        for s in shots:
            seeds_of.setdefault(s["label"].rsplit("-s", 1)[0], []).append(s["seed"])
        for key, _, _, _ in HORIZONTALS:
            want = PROFILE_ARMS.get(key, ()) if is_profile else ("Q", "H")
            for spelling in ("Q", "H"):
                if spelling in want:
                    assert seeds_of[f"{spelling}-{key}"] == seeds, key
                else:
                    assert f"{spelling}-{key}" not in seeds_of, key
        # An invisible byte in a prompt has cost this project a whole pass before.
        for s in shots:
            assert chr(8) not in s["prompt"] and chr(92) not in s["prompt"], s["label"]
    # The first pass's seeds survive at the head of the deeper one, so the same
    # wording on the same noise can be laid against sessions 326 and 327.
    assert PROFILE_SEEDS[:len(SEEDS)] == list(SEEDS)
    assert len(set(PROFILE_SEEDS)) == len(PROFILE_SEEDS) == 10, PROFILE_SEEDS
    print(f"selfcheck OK: {len(a)} arms x {len(SEEDS)} seeds, and "
          f"{len(arms(True))} arms x {len(PROFILE_SEEDS)} seeds under --profile")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the selfcheck, print the arms, write nothing")
    ap.add_argument("--profile", action="store_true",
                    help="the profile row alone, at ten seeds, with the front control")
    args = ap.parse_args()

    seeds = PROFILE_SEEDS if args.profile else SEEDS
    a = arms(args.profile)
    for label, camera, why in a:
        print(f"{label:<14} {camera}\n{'':<14} ({why})")
    per = len(a) * len(seeds)
    print(f"\n{len(a)} arms x {len(seeds)} seeds = {per} photographs "
          f"x {len(CHECKPOINTS)} checkpoints = {per * len(CHECKPOINTS)}")

    _selfcheck()
    if args.dry_run:
        print("\n--- the prompt of the first arm ---")
        print(prompt_for(a[0][1]))
        print("\n--- the as-shipped arm ---")
        print(prompt_as_shipped())
        return 0

    shots = shots_for(args.profile)
    title = (f"QWEN CAMERA WORDS perfil - 3 grafias x {len(seeds)} seeds"
             if args.profile
             else "QWEN CAMERA WORDS - qwen vs casa, 5 horizontales x 2 grafias")
    for name, checkpoint, tune in CHECKPOINTS:
        settings = {**SETTINGS, **tune, "checkpoint": checkpoint}
        out = create_session(args.base, f"{title}, {name}", shots, settings)
        print(f"\nsession {out['id']} ({name}) created as a draft, {len(shots)} pending")
        print(f"  run:   curl -X POST {args.base}/api/sessions/{out['id']}/run")
        print(f"  judge: python scripts/judge_camera.py {out['id']} --question turn --static")
        print(f"         python scripts/judge_camera.py {out['id']} --question side --static")
    return 0


if __name__ == "__main__":
    sys.exit(main())
