## Why

Seventeen capabilities live under `openspec/specs/` and not one of them covers
the wardrobe. It shipped before OpenSpec was adopted, so the rules that decide
what a photograph is wearing — and whether an act that needs access may be
dealt to it at all — exist only as comments beside the code that enforces them.
A gap audit on 2026-09-06 found it alongside six other pre-OpenSpec
subsystems; the wardrobe is the one with a store, a route, an import, a
derivation and a per-photograph answer feeding the draw, so it is the one worth
writing down first.

The rules are not obvious and were each paid for once. A state is composed by
naming only what she is still WEARING, because a state written as what came off
puts the garment's word in the line and the crop law then reads it as her feet.
The undressing is derived from the outfit's garment order rather than stored,
because a stored list is a second copy that drifts the moment a garment is
reworded. `access` is answered per photograph rather than per run, because the
run-level flag was wrong in both directions. None of that is discoverable from
the spec set today, and the next agent to touch it re-derives it or breaks it.

## What Changes

- A new `wardrobe` capability spec describing behaviour that ALREADY SHIPS: the
  garment and outfit store, the read and import routes, the derived arc, the
  per-photograph access answer, and the session's wardrobe and its per-take
  override.
- No code changes, no behaviour changes. Every requirement is written from a
  probe against the code that implements it, and a requirement nothing in the
  tree implements is a bug in the requirement, not a task.
- Where a rule is a measured limit rather than a design choice, the spec says
  so and names the measurement — the aside stage exists because a `needs:
  access` act on a garment that cannot be moved renders a naked woman, and the
  empty wardrobe sentence exists because an empty string renders her undressed
  3/3.

## Capabilities

### New Capabilities

- `wardrobe`: the garment and outfit catalogue, the arc an outfit derives, the
  per-photograph access answer the draw reads, and how a session and a single
  take carry what she is wearing.

### Modified Capabilities

None. Nothing in the seventeen existing specs asserts anything about the
wardrobe today, so there is no requirement to change.

## Impact

- Specs only: `openspec/specs/wardrobe/spec.md` is created at sync.
- The code the spec describes and does not touch: `backend/db.py` (the
  `garment` and `outfit` tables), `backend/main.py` (`GET /api/wardrobe`,
  `POST /api/wardrobe/import`, `ComposeRunIn.wardrobes`, `.access`, `.bare`,
  `ShotIn.wardrobe`, `mute_wardrobe`), `backend/crop.py` (`lowest_named`, which
  answers `covers`), `frontend/src/wardrobe.js` (`arcFor`, `statesFor`,
  `wearing`), `frontend/src/views/SessionView.jsx` (`wardrobeArc`, the outfit
  picker) and `data/wardrobe-seed.json`.
- No route, no setting and no screen is added, so README.md and `docs/` need no
  change from this. The docs already describe the wardrobe for an operator;
  this describes it for whoever changes it.
