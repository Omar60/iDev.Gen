"""The 25 directed cameras that are not camera positions: do they do anything?

25 of directed's 49 camera rows are shot sizes, lens terms and camera moves
rather than positions ([[idevgen-directed-catalogue-gaps]]). None has ever been
shot. The standing proposal was to delete or retire them because no cell can
measure them against a position vocabulary -- but a term that visibly changes
the photograph is worth keeping whatever family it is filed under, and nothing
here has been asked. **Shoot before discarding.**

    close     close-up, extreme close-up, headshot, shoulder-up
    medium    medium shot, medium close-up, medium long shot, waist-up, knee-up
    full      full body, three-quarter shot
    wide      wide shot, long shot, extreme wide shot, establishing shot
    lens      35mm, 50mm, 85mm, macro, telephoto, wide-angle, three-point perspective
    movement  pan shot, tilt shot, tracking shot

## The line, and why the framing slot is gone

Directed's line normally ends with a framing term (`full body`), and that term
is a crop instruction. Fifteen of the 25 rows here are ALSO crop instructions,
so a line carrying both would be measuring a contradiction rather than a term
([[idevgen-crop-in-the-composer]] is the gate that refuses this trio when it is
composed rather than written). The framing is therefore dropped for every arm,
including the control, and the camera term is the only thing in the line with
anything to say about the frame.

Nothing is written about clothes either. A written garment closes the crop --
thirty dressed frames stopped at the thigh on a line ending `full body`
([[idevgen-framing-follows-words]]) -- and the crop is the question here.

    zchar_jir. <directed's look> <term>. <act>.

The look is the one shipped in session 381 and is held constant. It is not
neutral about framing (it says `Full-frame camera on a tripod` and furnishes a
studio with a floor), but it is the same in all 26 arms and the alternative --
an empty look -- costs the geometry and sends the camera overhead
([[idevgen-wardrobe-rule-has-no-law]]).

## The control is arm zero

The same line with NO camera term at all. Every row is read against it and not
against its own `judge_label`: a term whose frames are the control's frames did
nothing, whatever it promised. This is the reading the 25 rows have never had.

The act names her feet, so the control has the whole body to crop from and a
term that cuts at the knee has somewhere to cut.

## A screen, not a measurement

Three seeds an arm. Enough to separate "this term moves the frame" from "this
term is the control", which is the only question being asked. A row that moves
the frame has earned a proper arm; a row that is the control 3/3 has earned
deletion, and that is the outcome this run exists to justify.

Usage: python scripts/shoot_unmeasured_cameras.py [--dry-run] [--run]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from shoot_directed_poses import TRIGGER, SETTINGS, create_session
from shoot_wardrobe_third_geometry import directed_look

# The families with no reading, from data/directed-cameras-seed.json. Read out
# of the seed at run time so this script cannot drift from the catalogue
# ([[idevgen-seed-files-drift]]).
UNMEASURED = ("close", "medium", "full", "wide", "lens", "movement")

# The four families that are CROP instructions. `--crop-only` shoots just these,
# for the second act below.
CROP_FAMILIES = ("close", "medium", "full", "wide")

# `stretching-up` names the balls of her feet, so the control is head-to-feet and
# every crop term has somewhere to cut.
#
# And that is exactly what session 387 found it doing: all fifteen crop terms
# came back as the control's frame, head to feet. The crop law says the frame
# reaches the LOWEST BODY PART THE LINE NAMES ([[idevgen-crop-terms-as-cameras]]),
# and the act names her feet, so `close-up` was being asked to overrule a named
# foot rather than to crop an unconstrained frame.
#
# `--act arms-raised` is the arm that separates the two readings. It is the same
# photograph -- standing, both arms over her head -- and its lowest named part is
# HER CHEST:
#
#     term still inert  ->  the term does nothing, and the row can go
#     term now crops    ->  the term works and the ACT was overruling it, which
#                           is a catalogue rule and not a dead row
ACT_KEY = "stretching-up"

SEEDS = [770905001 + i for i in range(3)]


def cameras_from_seed(path: str = "data/directed-cameras-seed.json") -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    return [r for r in rows if r.get("family") in UNMEASURED]


def acts_from_seed(path: str = "data/directed-acts-seed.json") -> dict[str, dict]:
    with open(path, encoding="utf-8") as fh:
        return {a["concept_key"]: a for a in json.load(fh)}


def line(look: str, term: str, act_wording: str) -> str:
    """Trigger, look, camera term, act. No wardrobe and no framing, on purpose."""
    parts = [f"{TRIGGER}.", look]
    if term:
        parts.append(f"{term}.")
    parts.append(f"{act_wording}")
    return " ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--act", default=ACT_KEY,
                    help="the act the terms are asked against (default stretching-up)")
    ap.add_argument("--crop-only", action="store_true",
                    help="only the four crop families, 15 rows")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()

    look = directed_look()
    act = acts_from_seed()[args.act]
    rows = cameras_from_seed()
    if args.crop_only:
        rows = [r for r in rows if r["family"] in CROP_FAMILIES]

    # A framing term in the line would compete with the terms being measured.
    for word in ("full body", "waist-up", "close-up", "portrait"):
        assert word not in act["wording"].lower(), word
    assert len(rows) == (15 if args.crop_only else 25), len(rows)

    # The whole point of the act is where its lowest named part sits, so it is
    # computed here rather than trusted: the crop law is the thing being argued
    # with and reading it out of the same module the composer uses is the only
    # way this script cannot disagree with the app.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))
    import crop  # noqa: E402
    lowest = crop.PART_NAME.get(crop.lowest_named(act["wording"]))
    print(f"act    {args.act}, lowest named part: {lowest}")

    arms = [("control", "")] + [(r["concept_key"], r["wording"]) for r in rows]
    print(f"       {act['wording']}\n")
    for key, term in arms:
        print(f"{key:26} {term!r}")

    shots = [
        {"label": f"{key}-s{i + 1}",
         "prompt": line(look, term, act["wording"]),
         "verbatim": True, "seed": seed, "count": 1}
        for key, term in arms
        for i, seed in enumerate(SEEDS)
    ]
    print(f"\n{len(arms)} arms x {len(SEEDS)} shared seeds = {len(shots)} photographs")

    if args.dry_run:
        print("\n--- the control, then one term ---")
        print(shots[0]["prompt"])
        print()
        print(shots[len(SEEDS)]["prompt"])
        return 0

    name = (f"THE UNMEASURED CAMERAS - {len(rows)} terms against a no-term "
            f"control on {args.act} ({lowest}), 3 seeds each")
    out = create_session(args.base, name, shots, manner="directed")
    sid = out["id"]
    print(f"\nsession {sid} created as a draft, {len(shots)} pending")
    if not args.run:
        print(f"run it with: curl -X POST {args.base}/api/sessions/{sid}/run")
        return 0

    import urllib.request
    req = urllib.request.Request(f"{args.base}/api/sessions/{sid}/run", b"",
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        print("run:", r.read().decode()[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
