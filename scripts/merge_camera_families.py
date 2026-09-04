"""Directed's cameras were named twice, so its judging menu offered the same
sentence under two keys.

`catalogue-seed.json` holds the nine furniture-free forms as sentences and
`directed-cameras-seed.json` holds the terms, and four cameras carry a
different `family` in each file. The readings were word-for-word identical --
session 383 had three photographs of ten come back with no majority because the
votes split between `shoulder` and `over-shoulder`, which are one sentence.

The merge keeps the sentence vocabulary, because that is the one the written
path plans with (`cameraPlan`, `fitCameras`) and the one the arrangement rows
name. The earlier attempt was reverted for renaming the components and leaving
those lists behind; this moves both, plus the readings and the verdicts already
recorded under a spelling that is about to stop existing.

Idempotent: a second run finds nothing to do. Run with `--dry-run` to see the
counts first, `--self-check` to exercise the list rewrite.

    python scripts/merge_camera_families.py --dry-run
    python scripts/merge_camera_families.py
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

MANNER = "directed"
# old family -> the name the sentences and the arrangement rows already use
MERGES = {
    "side-level": "side",
    "over-shoulder": "shoulder",
    "rear": "behind",
    "ground-level": "floor",
}


def db_path() -> Path:
    data = Path(os.environ.get("IDEVGEN_DATA_DIR")
                or Path(__file__).resolve().parent.parent / "data")
    return data / "idevgen.db"


def rewrite_cameras(value: str) -> str:
    """A comma-separated family list with the merged names replaced, order kept
    and a name that would now appear twice kept once."""
    out: list[str] = []
    for name in (v for v in (value or "").split(",") if v):
        merged = MERGES.get(name, name)
        if merged not in out:
            out.append(merged)
    return ",".join(out)


def migrate(conn: sqlite3.Connection, apply: bool) -> list[str]:
    lines: list[str] = []
    olds = tuple(MERGES)
    marks = ",".join("?" * len(olds))

    for old, new in MERGES.items():
        n = conn.execute(
            "SELECT count(*) FROM component WHERE slot='camera' AND manner=? AND family=?",
            (MANNER, old)).fetchone()[0]
        if n:
            lines.append(f"component.family {old} -> {new}: {n}")
            if apply:
                conn.execute(
                    "UPDATE component SET family=? WHERE slot='camera' AND manner=? AND family=?",
                    (new, MANNER, old))

    rows = conn.execute(
        "SELECT id, concept_key, cameras FROM component "
        "WHERE slot='act' AND manner=? AND cameras IS NOT NULL AND cameras != ''",
        (MANNER,)).fetchall()
    for row in rows:
        after = rewrite_cameras(row["cameras"])
        if after != row["cameras"]:
            lines.append(f"act {row['concept_key']}.cameras {row['cameras']!r} -> {after!r}")
            if apply:
                conn.execute("UPDATE component SET cameras=? WHERE id=?", (after, row["id"]))

    live = {r[0] for r in conn.execute(
        "SELECT key FROM reading WHERE slot='camera' AND manner=?", (MANNER,))}
    for old, new in MERGES.items():
        if old not in live:
            continue
        if new in live:
            lines.append(f"reading {old}: deleted, {new} says the same sentence")
            if apply:
                conn.execute(
                    "DELETE FROM reading WHERE slot='camera' AND manner=? AND key=?", (MANNER, old))
        else:
            lines.append(f"reading {old} -> {new}")
            if apply:
                conn.execute(
                    "UPDATE reading SET key=? WHERE slot='camera' AND manner=? AND key=?",
                    (new, MANNER, old))

    shots = conn.execute(
        "SELECT s.id, s.verdicts FROM shot s JOIN session e ON e.id = s.session_id "
        f"WHERE e.manner=? AND s.verdicts IS NOT NULL AND s.verdicts != ''", (MANNER,)).fetchall()
    for shot in shots:
        try:
            verdicts = json.loads(shot["verdicts"])
        except (TypeError, ValueError):
            continue
        camera = verdicts.get("camera") if isinstance(verdicts, dict) else None
        if camera in MERGES:
            lines.append(f"shot {shot['id']}.verdicts.camera {camera} -> {MERGES[camera]}")
            if apply:
                verdicts["camera"] = MERGES[camera]
                conn.execute("UPDATE shot SET verdicts=? WHERE id=?",
                             (json.dumps(verdicts), shot["id"]))

    left = conn.execute(
        f"SELECT count(*) FROM component WHERE slot='camera' AND manner=? AND family IN ({marks})",
        (MANNER, *olds)).fetchone()[0]
    if apply and left:
        raise SystemExit(f"{left} camera rows still carry a merged family - rolled back")
    return lines


def self_check() -> None:
    assert rewrite_cameras("side-level") == "side"
    assert rewrite_cameras("front,side-level,mirror") == "front,side,mirror"
    # Both names of one camera on one list is one name after the merge.
    assert rewrite_cameras("side,side-level") == "side"
    assert rewrite_cameras("") == ""
    assert rewrite_cameras("front,arm") == "front,arm"
    print("self-check ok")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    ap.add_argument("--self-check", action="store_true", help="exercise the list rewrite and exit")
    args = ap.parse_args()
    if args.self_check:
        self_check()
        return 0

    path = db_path()
    if not path.exists():
        print(f"no store at {path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        lines = migrate(conn, apply=not args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()

    print("\n".join(lines) if lines else "nothing to merge")
    print(f"{len(lines)} change(s)" + (" (dry run, nothing written)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
