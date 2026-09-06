## Context

See proposal.md — Why. What matters here is the state the code is in today.

The judging screen builds its choices in `frontend/src/judge.js` from
`positionsFor` / `arrangements` / `framings` — the component catalogue for the
session's manner — collapsed to one entry per `family`, plus "None or cannot
tell". A pass just narrowed those to the families actually photographed in the
deck. `judge_shot` scores a hit as "the answer equals the drawn wording key, or
its family equals the drawn wording's family".

So the family is already the unit that decides a hit; what is missing is a
vocabulary of families a photograph can be given that **nothing asked for**. The
measured floor of the current bench is frontal on every photograph with an empty
prompt, which means `front` is an outcome every camera pass should be able to
record and no camera component in the current catalogue asks for.

Constraint that shapes everything below: today's cell counts came out wrong
twice, both times because two calculations that were supposed to agree did not
(a slot the line never asked for scored as a miss; a pass over one slot counted
a photograph toward a cell measuring another). The design below prefers a
refusal at write time over a resolution rule at read time for that reason.

## Goals / Non-Goals

**Goals:**

- A miss records what was seen, not only that the ask failed.
- The base scope and the session scope cannot disagree, by construction rather
  than by a documented precedence.
- A pass whose correct answer is not on the list refuses instead of recording
  misses.

**Non-Goals:**

- Rewriting how a cell's state is derived. `db.cell_state` is untouched.
- Multi-slot answers in one question. A pass still asks one slot.
- Automating the reading list inside the app. The vision model writes candidates
  into `docs/catalogue-candidate-prompt.md`'s contract; a human loads them.
- Backfilling the answers already stored against the old choice set.

## Decisions

### One table, `session_id` NULL for base, and no precedence rule

```sql
CREATE TABLE reading (
  id INTEGER PRIMARY KEY,
  slot TEXT NOT NULL CHECK (slot IN ('camera','act','framing')),
  manner TEXT NOT NULL,
  session_id INTEGER REFERENCES session(id) ON DELETE CASCADE,  -- NULL = base
  key TEXT NOT NULL,
  label TEXT NOT NULL CHECK (length(trim(label)) > 0),
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX reading_base ON reading (slot, manner, key)
  WHERE session_id IS NULL;
CREATE UNIQUE INDEX reading_session ON reading (slot, manner, session_id, key)
  WHERE session_id IS NOT NULL;
```

The union a pass offers is base ∪ this session's. The collision that would need
a precedence rule — the same key in both scopes — is refused at insert with a
422 naming the key.

**The two partial indexes do not catch it**, and that is the trap: they cover
disjoint sets of rows, so `(camera, directed, NULL, front)` and
`(camera, directed, 308, front)` both insert cleanly and the union hands the
judge `front` twice. The refusal is application code and it runs in BOTH
directions — a session reading against the base, and a base reading against
every session reading of that slot and manner. A check on one side only is
reachable by inserting in the other order. **Alternative rejected:** two tables (`reading` and
`session_reading`) with the session one winning. That is a resolution rule, and a
resolution rule is a second calculation; every counting bug in this branch so far
has been two calculations that disagreed. **Alternative rejected:** a single
scope. The user asked for both, and a shoot with an outcome no other shoot
produces is real.

### The reading key IS a component family

No mapping table, no `family_id`. A hit is `chosen_reading_key == family(drawn
component)`, which is the comparison `judge_shot` already makes.

The cost is that the two vocabularies must be kept aligned by hand, and the
guard against drift is the pass-time refusal below rather than a foreign key: a
family can exist with no reading (the catalogue screen can add a component at any
time), and a reading can exist with no family (that is the whole point of a
distractor). A foreign key in either direction would forbid one of those.

### The pass refuses when a photographed family has no reading

`GET /api/sessions/{sid}/judge-pass` already walks the deck to collect the
families present. It compares that set against the reading union and refuses with
422 naming the families with no reading. Alternative rejected: serving the deck
and letting those photographs be answered "cannot tell" — that records ten misses
for a wording that may have rendered perfectly, which is worse than no
measurement.

### Verdicts keep their shape; scoring gains a branch

`shot.verdicts` stays `{slot: answer}`. The answer is a reading key going
forward and was a component key before. `_hit` keeps its existing branches
(exact key, then family) and gains the reading branch first, so the six cells
already measured on this bench still score the same way when re-read.

### "Cannot tell" stays, and the negatives can go

With four plausible readings the deck no longer has a single expected answer, so
the sampled negatives added to the deck are not load-bearing any more. They are
kept for now — they are cheap and they still catch an operator answering by
reflex — and removing them is a separate decision once a pass has run under the
new vocabulary.

### Editing: base readings on the catalogue screen, session readings on the judge screen

Base readings belong with the components they are the counterpart of. A session
reading is written while looking at that session's photographs, which is the
judge screen. Alternative rejected: one screen for both with a scope selector —
more UI than the feature needs, and the two are used at different moments.

## Risks / Trade-offs

- **The reading list becomes another thing that drifts from the catalogue.** →
  The pass refuses rather than silently mismeasuring, and the refusal names what
  to add.
- **A reading list that is not mutually exclusive makes the answer arbitrary**
  (two readings both true of one photograph). → Nothing in the schema can check
  it; the contract in `docs/catalogue-candidate-prompt.md` demands it and the
  operator reads what comes back, the same way component wordings are read
  before loading.
- **More options is more work per photograph, and a longer list is a slower
  pass.** → Keep a slot's list short (four or five). This is a judgement to make
  when writing the list, not a limit to enforce in code.
- **Answers recorded before this change mean "the family asked for" or "not
  that", never "what was seen".** → They keep scoring correctly and carry less
  information; no backfill is attempted, and the cells they produced stand.

## Migration Plan

The table is created empty by the normal on-open migration in `backend/db.py`.
Every judging pass refuses until readings exist for the families that were
photographed — the same shape as the component catalogue starting empty and the
app refusing to compose. Seeding is a manual step: add the readings from the
catalogue screen, or import them, before the next pass.

Rollback is dropping the table and reverting; stored verdicts remain valid under
the older scoring because `_hit` keeps the component-key and family branches.
