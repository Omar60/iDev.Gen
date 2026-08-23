"""Turn a run of `measure_writer.mjs` into a render session, so a shoot the real
writer wrote can be shot and judged.

The measuring instruments stop at the text: `measure_writer.mjs` runs the real
`shootLines` and prints the lines, and `analyze_camera.mjs` counts what they say.
Nothing said whether the photographs come out. The camera work up to session 252
answered that one clause at a time, with a line fixed by hand - which is the
right way to compare two wordings and the wrong way to find out whether a whole
planned shoot holds together.

So: write with the app, shoot with the app, judge blind.

    node <bundle> 24 candid > run.json
    python scripts/post_written_shoot.py run.json --manner candid
    curl -X POST http://127.0.0.1:8777/api/sessions/<id>/run
    python scripts/judge_camera.py <id> --question position --repeat 3

The look is the app's own: the room the camera work has always used, plus
`MANNER.candid.look` word for word when the manner is candid. It goes in the
session's look column with `use_look` on, which is how the app composes a real
shoot - the lines carry the wardrobe themselves, as `shootLines` writes them.
"""
from __future__ import annotations

import argparse
import json
import sys

from shoot_camera_forms import SETTINGS, create_session
from shoot_candid_cameras import CANDID_CAPTURE, ROOM


def lines_from(path: str) -> list[str]:
    """`shootLines` logs its check tally to stdout ahead of the array, and the
    runs on disk carry it, so the array is found rather than parsed from byte 0."""
    raw = open(path, encoding="utf8").read()
    at = raw.index("\n[\n")
    return json.loads(raw[at:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="a JSON run from measure_writer.mjs")
    ap.add_argument("--base", default="http://127.0.0.1:8777")
    ap.add_argument("--manner", choices=("candid", "directed", "selfie"), default="candid")
    ap.add_argument("--name", default="")
    # The camera work was all shot on the Krea 2 mix, which is the right base for a
    # question about where a camera stands and the wrong one for a shoot that has
    # to render an act: session 161, the shoot the `selfie` manner came from, was
    # finepornV4 at 12 steps. One flag rather than three, since the two presets
    # are the two that have ever been used here.
    ap.add_argument("--explicit-model", action="store_true",
                    help="finepornV4 at 12 steps / er_sde, as sessions 155 and 161 were shot")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lines = lines_from(args.run)
    # `selfie` is `candid` in the look as much as in the manner: same phone, same
    # room, same capture quality. It inherits the clause word for word.
    look = ROOM + (CANDID_CAPTURE if args.manner in ("candid", "selfie") else "")
    # NOT verbatim, and this is the whole point of the script. `verbatim` means
    # "queue exactly as given" - no trigger, no base prompt, no look - which is
    # right for a hand-fixed arm that writes all three into its own prompt and
    # catastrophic here: session 253 was shot that way and rendered a different
    # woman in a different room with no candid capture at all, then read as the
    # plan obeying 13 of 24. A written line is the take, and the app composes
    # trigger + base + look + wardrobe + take around it, exactly as a real shoot.
    shots = [{"label": f"{i + 1:02d}", "prompt": line, "count": 1}
             for i, line in enumerate(lines)]

    print(f"{len(shots)} photographs, {args.manner}, look {len(look.split())} words")
    if args.dry_run:
        print("\n--- the first line ---")
        print(lines[0])
        return 0

    # `use_look` on, and the look in the session's own column: the app prepends it
    # to every take, which is what makes twenty photographs one shoot.
    settings = {**SETTINGS, "use_look": True}
    if args.explicit_model:
        settings |= {"checkpoint": "finepornV4INT8NVFP4BF16_v4Nvfp4.safetensors",
                     "steps": 12, "sampler": "er_sde", "scheduler": "beta"}
    out = create_session(args.base, args.name or f"WRITTEN SHOOT - {args.manner}, camera plan",
                         shots, settings, look)
    print(f"\nsession {out['id']} created as a draft, {len(shots)} pending")
    print(f"run it with: curl -X POST {args.base}/api/sessions/{out['id']}/run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
