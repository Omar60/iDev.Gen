"""One grid PNG out of a probe's frames, so a set can be judged in one look.

Every `shoot_*.py` writes `LABEL-sSEED.png` into its own folder and judging them
meant opening each frame. This lays a set out with one row per arm and one
column per seed, labelled down the left edge.

The app has a contact sheet of its own (`GET /api/sessions/{id}/contact-sheet`)
and it cannot do this: it reads shots out of the database and flows them four to
a row by rating. A probe writes straight to disk with no session, and a ladder is
only readable when each arm keeps its own row.

The prefix is matched anywhere in the label, so a session folder can be sliced
one family at a time: `contact_sheet.py close --dir data/sessions/387`.

Usage: python scripts/contact_sheet.py PS- [--dir data/weight-probe] [--cell 420]
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
GUTTER = 4


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix", help="matched anywhere in the label, e.g. PS- or close")
    ap.add_argument("--dir", default="data/weight-probe")
    ap.add_argument("--cell", type=int, default=420, help="cell width in pixels")
    # A cell session names every frame the same thing (`composed-1` .. `-10`), so
    # one frame per arm and the sheet comes out ten rows of one column — a strip
    # nobody can read. `--cols` ignores the arms and flows the matched frames
    # into a grid instead, which is what judging a cell by eye wants.
    ap.add_argument("--cols", type=int, default=0,
                    help="flow all matched frames into N columns instead of one row per arm")
    args = ap.parse_args()

    folder = ROOT / args.dir
    rows: dict[str, list[Path]] = defaultdict(list)
    matched: list[Path] = []
    for png in sorted(folder.glob("*.png")):
        # A sheet is not a frame. `sheet-composed.png` matches the prefix
        # `composed`, so a second run laid the first run's sheet out as an
        # eleventh photograph and reported "11 frames, 3 arms".
        if png.stem.startswith("sheet-"):
            continue
        # A session writes `09298_label-s1.png`; a probe writes `LABEL-s1.png`.
        # Dropping a leading shot id is what lets one sheet lay a session's arms
        # out one per row, which the app's own contact sheet cannot do -- it
        # flows shots four to a row by rating.
        stem = png.stem.split("_", 1)[-1]
        if args.prefix not in stem:
            continue
        rows[stem.rsplit("-s", 1)[0]].append(png)
        matched.append(png)
    if not rows:
        print(f"nothing matching {args.prefix}*.png in {folder}")
        return 1

    if args.cols > 0:
        # FILE order, not label order. Flowing the rows dict instead put
        # `composed-10` second and `composed-2` twelfth: a cell session's ten
        # frames came out shuffled with nothing on the sheet saying so. The
        # filenames carry the shot id, so sorted() over them is shooting order.
        # Zero-padded so row 10 sorts after row 2, and the tile keeps its own
        # stem as its label — under `--cols` the row key names nothing.
        width = len(str(len(matched) // args.cols))
        rows = {str(i // args.cols).zfill(width): matched[i:i + args.cols]
                for i in range(0, len(matched), args.cols)}

    tiles = {label: [Image.open(p) for p in paths] for label, paths in rows.items()}
    labels = {label: [p.stem.split("_", 1)[-1] for p in paths] for label, paths in rows.items()}
    first = next(iter(tiles.values()))[0]
    cell_h = round(args.cell * first.height / first.width)
    cols = max(len(v) for v in tiles.values())
    sheet = Image.new("RGB", (cols * (args.cell + GUTTER), len(tiles) * (cell_h + GUTTER)),
                      "white")
    draw = ImageDraw.Draw(sheet)
    for r, (label, images) in enumerate(sorted(tiles.items())):
        for c, img in enumerate(images):
            y = r * (cell_h + GUTTER)
            sheet.paste(img.resize((args.cell, cell_h)), (c * (args.cell + GUTTER), y))
            draw.text((c * (args.cell + GUTTER) + 6, y + 6), labels[label][c],
                      fill="yellow")

    dest = folder / f"sheet-{args.prefix.rstrip('-')}.png"
    sheet.save(dest)
    print(f"{sum(len(v) for v in tiles.values())} frames, {len(tiles)} arms -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
