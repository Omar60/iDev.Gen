"""Add a `key` field to every wording in kinds.js.

Task 1.1 of the prompt-component-matrix change left each concept with
a single wording and no `key` on the wording object. The new convention
(2.3 + 3.1 rework) reads `wordings[i].key` for the cell's wording key,
so each wording needs its own. For single-wording concepts the wording
key equals the concept key — a second wording, when one is added, will
get a different key.

This script is one-shot. After it runs the catalogue is in the new
shape and 2.4's expected gap can be checked against it. The script
does NOT touch:
  - the concept-level `key`
  - the wording's `text` or `family`
  - anything outside `wordings: [{ ... }]` literals

It also does NOT add the `key` to a wording that already has one
(re-running is a no-op).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KINDS = ROOT / "frontend/src/kinds.js"

# Match a catalogue entry: a leading `{ key: '...'`, anything up to the
# `wordings: [{` literal, and capture the concept key. The match is
# non-greedy so it stops at the first `wordings: [{` of the same entry.
ENTRY = re.compile(
    r"(\{\s*key:\s*'([^']+)'(?:\s*,[^{}]*?)?\s*wordings:\s*)\[\{",
    re.DOTALL,
)


def has_wording_key(text: str, concept_key: str) -> bool:
    """Already-keyed? Skip to avoid double-keying on a re-run."""
    return f"key: '{concept_key}'" in text


def add_key(match: re.Match[str]) -> str:
    head, concept_key = match.group(1), match.group(2)
    return f"{head}[{{ key: '{concept_key}', "


def main() -> None:
    src = KINDS.read_text(encoding="utf-8")
    out = ENTRY.sub(add_key, src)
    n_before = src.count("wordings: [{")
    n_after = out.count("wordings: [{ key:")
    KINDS.write_text(out, encoding="utf-8")
    print(f"entries with `wordings: [...]`: {n_before}")
    print(f"entries now keyed:              {n_after}")
    if n_before != n_after:
        raise SystemExit("not every wording was keyed — inspect kinds.js")


if __name__ == "__main__":
    main()
