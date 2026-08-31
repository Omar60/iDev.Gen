"""One grid PNG out of a probe's frames, so a set can be judged in one look.

Every `shoot_*.py` writes `LABEL-sSEED.png` into its own folder and judging them
meant opening each frame. This lays a set out with one row per arm and one
column per seed, labelled down the left edge.

The app has a contact sheet of its own (`GET /api/sessions/{id}/contact-sheet`)
and it cannot do this: it reads shots out of the database and flows them four to
a row by rating. A probe writes straight to disk with no session, and a ladder is
only readable when each arm keeps its own row.

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
    ap.add_argument("prefix", help="label prefix, e.g. PS-")
    ap.add_argument("--dir", default="data/weight-probe")
    ap.add_argument("--cell", type=int, default=420, help="cell width in pixels")
    args = ap.parse_args()

    folder = ROOT / args.dir
    rows: dict[str, list[Path]] = defaultdict(list)
    for png in sorted(folder.glob(f"{args.prefix}*.png")):
        rows[png.stem.rsplit("-s", 1)[0]].append(png)
    if not rows:
        print(f"nothing matching {args.prefix}*.png in {folder}")
        return 1

    tiles = {label: [Image.open(p) for p in paths] for label, paths in rows.items()}
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
            draw.text((c * (args.cell + GUTTER) + 6, y + 6), f"{label} #{c + 1}",
                      fill="yellow")

    dest = folder / f"sheet-{args.prefix.rstrip('-')}.png"
    sheet.save(dest)
    print(f"{sum(len(v) for v in tiles.values())} frames, {len(tiles)} arms -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
