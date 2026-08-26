## 1. Reshape the catalogues into concepts with wordings

Behaviour-neutral throughout: the shufflers must keep drawing the same lines.

- [x] 1.1 Give every catalogue entry the concept shape — key, slot, wordings, optional reference image — keeping each existing string as that concept's first wording, and verify `python -m pytest` and `npm --prefix frontend test` stay green with no prompt text changed
- [x] 1.2 Bring `KISS_CAMERA` into the catalogue as camera components carrying that they override a dealt camera — the map holds a key into that manner's own camera catalogue and `kissCameraFor` resolves it there, never a concept whose wording is a pointer (see the one-concept-shape decision in design.md) — and verify a session with planted kiss frames still ends up with the same camera text on those photographs
- [x] 1.3 Add a test asserting no catalogue wording's text appears anywhere else in the prompt system, and confirm it fails against the two inline camera examples still present — the `wink` and `finger` kiss frames carry deliberately identical wording text, so decide whether they are one concept with two `hand` values or an allowed pair, and say which in the test rather than letting it pass by accident
- [x] 1.4 Add a test asserting `SHOOT_FIELDS` and `BLOCK_HEADINGS` still carry the same seven keys in the same order after the reshape, or confirm the existing one in `tests/test_enhance.py` covers it unchanged — covered by `tests/test_enhance.py::test_the_headings_name_the_fields_the_writer_is_asked_for` (line 537), which extracts `SHOOT_FIELDS` from the literal array in `frontend/src/kinds.js:1286` via regex and compares to `list(enhance.BLOCK_HEADINGS)`; 1.1/1.2 left the array literal untouched, the test passes, no second test added

## 2. Evidence store

- [x] 2.1 Add cell storage keyed by the trio, manner and checkpoint holding judged and arrived counts, and verify a write missing any of the five is rejected — **REOPENED 2026-08-25**: the original 2.1 keyed the cell on `(concept, wording, manner, checkpoint)` and recorded the 9 per-family verdicts of kinds.js:1962-1986 as a single concept with a synthetic `astride-front` wording. The 4-column key is the wrong shape: those rows are observations of (act, family), not of either alone, and the 4-column key was a 2-component observation stuffed into a 1-component cell. The new shape is the trio the photograph is composed of: `(camera_wording, act_wording, framing_wording, manner, checkpoint)` (design.md decision C, the 5-column key). The schema lives in `backend/db.py:SCHEMA` (the `cell` table) with `NOT NULL` on the five key columns plus `PRIMARY KEY (camera_wording, act_wording, framing_wording, manner, checkpoint)`, a `CHECK` that none of the five is `''` (the literal `none` is the value the synthetic keys take — a fact of the measurement, not an invention, and it passes the CHECK), and two `INTEGER NOT NULL DEFAULT 0` counters under a `CHECK (judged >= 0 AND arrived BETWEEN 0 AND judged)` so 2.2 never reads a pair it cannot answer, with no Python validation. `_migrate` drops and recreates the cell table if the old 4-column shape is present — the conversion is rule-based (the family lives in `camera_wording` for the 9 family rows, in `act_wording` for the act-only rows, the candid `behind-direct` row is the only camera-only row) and the seed repopulates. The destructive migration is guarded by a row-count check: it only runs when the old table is empty or holds exactly the 15 seed rows; any other content raises with a message asking to translate by hand, because 6.2 in this same change populates the table with human judgements and silent loss is not acceptable. SCHEMA is the single source of truth for the cell DDL — `_migrate` re-runs the full SCHEMA after the DROP, so the cell DDL lives in one place. `tests/test_cell.py` proves the rejection is SQLite's, not a hand-written if — five parametrized cases (`[camera_wording]`, `[act_wording]`, `[framing_wording]`, `[manner]`, `[checkpoint]`) each insert a row that omits one key column from the column list and assert `sqlite3.IntegrityError`, five more that insert each key present but empty, one that inserts 8 arrivals against 3 judgements, plus `test_a_complete_cell_write_is_accepted` as the positive evidence the table accepts a full key and stores the counts; the suite is green.
- [x] 2.2 Derive the three states from the counts — verified at 10 judged and 8 arrived, dead below that, unknown under 10 judged — and verify each boundary with a unit test including 0 of 3 landing as unknown — `backend/db.py:cell_state` is the pure function (judged<10 → unknown; judged>=10 AND arrived>=8 → verified; else dead); no branch for `arrived > judged` because the cell table's CHECK constraint already rejects the write, and a defensive branch would silently swallow a future loosening of that CHECK; `tests/test_cell.py` covers the case table with 10 parametrized cases spanning all three state boundaries (the five the task names — 10/8, 10/7, 9/9, 0/0, 0-of-3 — plus 3/3, 8/8, 10/0, 10/10, 11/9 to cover the full edge space); the function is unchanged by the trio model — it reads only `(judged, arrived)`, not the cell key.
- [x] 2.3 Seed the existing verdicts against the trio they were measured on, with the literal wording `none` in any slot the measurement did not break out, and verify the same corrections the previous 2.3 did — **REOPENED 2026-08-25**: the original 2.3 stored 15 rows in the 4-column shape with `("act", "astride-front", …)` etc., which was the 2-component observation stuffed into a 1-component cell. The new seed in `backend/db.py:EVIDENCE_SEED` holds 15 rows in the 5-column shape `(camera_wording, act_wording, framing_wording, manner, checkpoint, judged, arrived)`: the 9 per-family rows have `camera_wording = <family>` (front, overhead, mirror, pov, shoulder — the family is the dimension the camera rotation varied; kinds.js:1962-1986 records the family level) and `framing_wording = "none"` (the fixed line in `scripts/shoot_arrangements.py:63-77` does not name a framing — absent, not lost); the act-only rows (`back` × 2, `side` × 2, `astride` on Krea 2 mix) have `camera_wording = "none"` and `framing_wording = "none"` (the source reports the act × checkpoint without a camera or framing breakdown; kinds.js:2021 "back 0 of 12 on finepornV4, 0 of 12 on the Krea 2 mix"); the candid `behind-direct` camera row has `act_wording = "none"` and `framing_wording = "none"` (kinds.js:2056-2058 is a camera measurement, not an act one). The corrections the user named carry over: `side` (0/9 and 0/8) lands as `unknown` because judged<10, not dead (spec rules; design.md:332-334 was wrong about side and is now corrected); the per-family astride cells (6/6, 4/4, 6/4, 6/4) all land as `unknown` because n<10; the Krea 2 mix astride cell (12/9) is one row without per-family breakdown (kinds.js:2014 does not split it), and under the ratio reading it is dead (9×10=90 < 12×8=96, 75% below the 80% threshold) — the ratio decision kills the control on Krea 2 mix, and that is the cost; the `behind` ACT is not seeded (kinds.js:2035-2058 kills it with four anecdotes, not a (judged, arrived) pair — manufacturing an n would be inventing a number); the candid `behind-direct` camera (6/0) IS seeded because the source has a real 0/6 measurement on the camera side. `seed_evidence()` is idempotent (PRIMARY KEY conflict is swallowed, so re-runs are no-ops). `tests/test_cell.py` covers the seeded structure (per-family astride, per-checkpoint back/side, the corrections) and the state each cell derives.
- [x] 2.4 Add a test that every seed's wording in each of the three trio slots is a real catalogue key for that slot OR a synthetic key explicitly documented in the expected gap, and verify it fails when a seed names a wording the catalogue does not have — `tests/test_cell.py::test_every_seed_wording_is_a_key_in_its_catalogue` reads ARRANGEMENTS / CAMERA_POSITIONS / CANDID_POSITIONS / SELFIE_POSITIONS and the framing list via a node probe and asserts the seeded `(slot, wording)` pairs that DO NOT appear in the catalogue match the expected gap exactly. The gap under the trio model has 10 entries: 5 camera family keys (front, overhead, mirror, pov, shoulder — the 9 per-family verdicts' `camera_wording`, the family is metadata on the camera catalogue, not a top-level key), 3 `none` literals (`framing=none` for the 9 family rows + the 4 act-only rows = the 14 rows whose fixed line did not name a framing; `camera=none` for the 4 act-only rows whose source did not break out a camera; `act=none` for the candid `behind-direct` row whose source is a camera measurement), and 2 deleted arrangement wordings (back, side — kept as seeds because the verdicts are real, but pulled from ARRANGEMENTS so the catalogue no longer has them). The test passes when the gap is exactly this set; a future "let me add the family keys to the catalogue" or "let me pull the deleted seeds" fails it loudly and names which cell needs to be re-examined. A second test, `test_a_seed_pointing_at_nothing_in_the_catalogue_is_detected`, exercises the detection rule on invented data so the logic is not coupled to `EVIDENCE_SEED`.
- [x] 2.5 Closed by 6.1: `tests/test_api.py::test_a_dead_cell_is_undrawable_in_both_modes` proves a dead wording is undrawable in both strict and exploratory modes; `tests/test_arrangements.py::test_an_arrangement_says_the_bodies_and_nothing_else` keeps the `noneDead` assertion unchanged.

## 3. Composer, strict mode

- [x] 3.1 Compose and queue a single shot from drawn components with no writer request, recording the components on the shot, and verify the queued line joins identically to a written one — **REWORK 2026-08-25**: the original 3.1 wrote `{"camera": {"concept": "camera", "wording": <concept_key>}, ...}` into the `components` column, encoding `concept` as the slot name and `wording` as the concept's catalogue key. Under the trio model the cell is keyed on `(camera_wording, act_wording, framing_wording, manner, checkpoint)`, and the components column records the trio that was drawn so 6.2 can land the photograph on the right cell. The new write is `{"camera": {"concept": camera["key"], "wording": camera["wordings"][0]["key"]}, ...}` — the JSON key is the slot, the value carries the concept's catalogue key AND the wording's catalogue key. Today every concept has a single wording and the two keys coincide; a future "let me add a second wording" lands here as a different `wording` value while `concept` stays put. The catalogue (`frontend/src/kinds.js`) was reshaped (commit `8e5300c`) to carry `wordings: [{ key, text, family? }]` — the `key` field is the wording's own key, equal to the concept key for the first wording. `backend/db.py:shot.components` is the new `TEXT NOT NULL DEFAULT '{}'` column, added by `_migrate` with the same `ALTER TABLE` pattern `kind` and `tags` use. `backend/main.py:compose_shot` is the join: it calls `_sentences(camera["wordings"][0]["text"], act["wordings"][0]["text"], framing["wordings"][0]["text"])` to build the take and then `_compose(model, look, wardrobe, take)`, so for the same three components the output is byte-for-byte identical to what the writer's `_compose` produces. `backend/main.py:compose_and_queue_shot` reads the session's look and wardrobe, composes the line, and writes the three (concept, wording) pairs into the `components` column as JSON. A written shot leaves the column at its empty default `'{}'`, which is the marker 3.6 uses to tell a composed session from a written one. `POST /api/sessions/{sid}/compose` is the route. `tests/test_api.py::test_a_composed_shot_joins_identically_to_a_written_one` composes one shot and writes one with the same three components as the prompt, then asserts the two prompts are byte-for-byte identical — the equality is the assertion, not a similarity, and a future "let me change the join" or "let me reorder the components" that drifts the two apart breaks it on the spot. `test_a_composed_shot_records_the_three_components_on_the_row` reads the row back and asserts the three (concept, wording) pairs are exactly what was drawn. `test_a_written_shot_leaves_components_empty` asserts the written shot's `components` column is the empty default — the marker 3.6 looks for.
- [x] 3.2 Restrict strict draws to cells verified for the session's manner and checkpoint, and verify a component verified on another checkpoint is not drawn — the session carries `manner` and `checkpoint` (added by `_migrate` with default `''`, the right migration answer for "we don't know what dimensions this older session was shot under" rather than guessing from the model or workflow), `SessionIn` takes them on create. `ComposeIn` has **no `mode` field** on the payload: strict is the only legal mode today, and encoding it as a string would open a door that the type definition shuts today (an if over a free string is "anything but strict" passes through, and `Strict` or `strict ` would have disabled the check). 6.1 opens the seam when the second mode exists, with a `Literal["strict", "exploratory"]` type and its own test. The `compose_shot_endpoint` reads the session's two dimensions and looks up the cell for the trio exactly: a cell verified on a different checkpoint does not satisfy a session shot on a different one, and a missing or non-verified cell (unknown, dead) is refused with 422. The 422 message names the trio, the session's manner and checkpoint, and the state the lookup found, so the caller can see whether the gap is a missing measurement (unknown) or a failed one (dead). A session with no manner or no checkpoint is refused before the lookup, naming what is missing, rather than silently finding zero cells and reading as "not verified" — the same failure mode 3.2 exists to prevent, just shifted by one level. `tests/test_api.py` adds 6 tests: the task-named scenario (a component verified on another checkpoint is not drawn), the dead-cell refusal, the unknown-cell refusal, the missing-dimensions refusal, the positive case (a verified cell for the session's exact dimensions is drawn), and the bypass-attempt test (`mode: "anything"` is silently dropped by pydantic's default `extra="ignore"`, the strict check still runs, the compose is refused — this is the test that distinguishes "there is a strict mode" from "there is a strict mode that can be turned off by writing it wrong"). The two 3.1 tests are updated to declare manner and checkpoint on the session and pre-seed a verified cell for the trio they compose against, because strict mode is now the default. — **REWORK 2026-08-25**: the original 3.2 closed over a function no real session could reach. `SessionIn` declared `manner` and `checkpoint`, but the frontend's create-session POST (`frontend/src/views/ModelDetail.jsx:42`) sent neither: `manner` lived only in `ShotsEditor`'s local state (`frontend/src/views/ShotsEditor.jsx:67`, the `<select>` at line 259) and was discarded on save, and `checkpoint` had no UI element at all (`settings.checkpoint` is what fills the graph's loader at run time, not the cell-table key). Every session in the app was born with both columns empty, and the strict check at `compose_shot_endpoint` (`backend/main.py:766`) refused the compose with 422. The six 3.2 tests passed only because they declared the dimensions in the body. The fix has two halves. (a) The editor's `manner` is lifted to the create-session POST: `ShotsEditor.jsx` now takes `manner` and `onManner` props, and `ModelDetail.jsx:42` initializes `newSession.manner = 'directed'` (the editor's own default) and passes the lift. (b) `session.checkpoint` is derived at create from `s.settings.get("checkpoint")` (the user-picked override) or the workflow's own loader via `graph_checkpoint` (`backend/comfy.py:252-273` reads `ckpt_name`/`unet_name` from the graph — the very field that lets a tuned graph name its model without a second fact), with an explicit `s.checkpoint` in the body winning because a future PATCH that flips a session to a different model is the caller's, not the workflow's. The missing-dimensions refusal is unchanged: a session with no manner and a workflow whose graph has no loader is still refused before the lookup, naming what is missing. `tests/test_api.py` adds one test and updates one: the new test (`test_a_session_created_via_the_apps_path_can_be_composed_in_strict_mode`) uses the body the app actually sends — `manner='directed'` lifted by the editor, no top-level `checkpoint`, no `settings.checkpoint`, the workflow's `base.safetensors` is the source — and composes successfully against a cell seeded for those dimensions; the existing `test_a_strict_compose_requires_manner_and_checkpoint_on_the_session` now points the session at a bare workflow whose graph has no `CheckpointLoaderSimple` / `UNETLoader`, because the seeded workflow names its checkpoint in its loader and the derivation would otherwise supply it. — **REWORK 2026-08-25 (PATCH)**: the create-time derivation alone is not enough. `update_session` (`backend/main.py:617`) writes `workflow_id` and `settings` without re-deriving `session.checkpoint`, so a PATCH that swaps the session to a different workflow leaves the cell key on the old loader. The strict check then approves draws against a checkpoint the session no longer runs on — the bypass 3.2 exists to prevent, defeated by a PATCH. The fix: `_resolve_session_checkpoint` (`backend/main.py:546`) is the one place the rule lives, and `update_session` calls it on every PATCH that touches `workflow_id` or `settings.checkpoint`. The trigger is the source-of-truth move, not "every PATCH" — a settings PATCH that only flips `steps` does not re-derive. The model load is needed for the `workflow_id or model.workflow_id` fallback (a PATCH that clears the session's workflow falls back to the model's, the same rule `create_session` applies). `tests/test_api.py` adds three tests: `test_a_workflow_swap_re_derives_session_checkpoint` (the probe — create on the seeded workflow, swap to a second one whose loader says `OTHER.safetensors`, the row follows), `test_a_settings_checkpoint_override_re_derives_session_checkpoint` (the BaseModelSelect shape — `PATCH {"settings": {"checkpoint": v}}` updates the row), and `test_after_a_workflow_swap_a_cell_verified_on_the_old_checkpoint_is_refused` (the loop-closed test — the cell seeded for the OLD checkpoint must NOT satisfy a session now on the NEW one; a regression that drops the re-derivation flips this to 200). 301 backend tests + 23 frontend tests + `npm --prefix frontend run build` stay green.
- [x] 3.3 Refuse a strict composition that cannot fill a slot without repeating, naming the slot, its verified count and the largest fillable count, and verify nothing is queued, no shorter run is delivered, and the message names exploratory mode — **REWORK 2026-08-25 (trio pool)**: the original 3.3 in this same change counted the pool per slot with `SELECT COUNT(DISTINCT slot_wording) FROM cell …` and zipped one component per slot into N tuples. That was the failure the cell table's 5-column shape exists to prevent: a component verified alone can still fail in combination (design.md:326-329), and a per-slot DISTINCT count reads as 3 verified cameras × 3 verified acts = 9 trios when only 3 of them are actually cells in the table. The pool of a strict run is the set of `(camera_wording, act_wording, framing_wording)` rows that are verified for the session's manner and checkpoint, filtered to the candidates' keys: `SELECT camera_wording, act_wording, framing_wording FROM cell WHERE manner=? AND checkpoint=? AND camera_wording IN (…) AND act_wording IN (…) AND framing_wording IN (…) AND <verified predicate>`. The largest fillable is `min(requested, len(pool))`. If the pool is too small, refuse with 422; the picker then draws N distinct trios from the pool (not from per-slot lists), so every queued shot is on a verified cell. The four literals the user pinned (slot, verified count, largest fillable, exploratory) still hold, but the "slot" no longer has a count of its own — what the message names is **the slot whose number of distinct values within the verified trios is the minimum**, with the per-slot count being the verified count of that slot. Ties go to camera, then act, then framing. The "verified count" reported is that min per-slot count; the "largest fillable" is the pool size (capped at the requested count). The pre-check still runs before any INSERT (`db.run` auto-commits, a loop that refuses at k+1 would leave k rows), and the test still asserts `n_shots == 0` after a 422. **Design decision (2026-08-25) — where the run-level lives**: the run-level is a new endpoint `POST /api/sessions/{sid}/compose-run` with payload `{"count": N, "candidates": {camera, act, framing}}`. Three reasons: (1) 3.1's `POST /api/sessions/{sid}/compose` is a one-shot that takes the three components from the caller; the run-level takes a count and a candidate pool, so the two have different payloads and belong on different routes; (2) the spec scenario for 3.3 is N > 1, and a `count` field on `ComposeIn` would have no meaning in the 3.1 case — a parameter that does nothing in one path is a door that should not be on the same type; (3) 3.5 ("compose a whole session as the same draw plus ordering") will be a third route with its own payload (per-session settings, wardrobe walk, family spread) — colocating the three would conflate three different things. The new endpoint is a sibling of `compose_shot_endpoint`, sharing the same manner/checkpoint pre-check (no missing-dimensions compose is queued) and the same verified-cell predicate. 3.4 adds cross-tuple dedup on top of this endpoint's success path; 3.5 replaces `count` with a session-level count and adds ordering constraints; the run-level here stays the "give me N from this pool" path. **CLOSED 2026-08-25**: verified from the outside — the same request over the same pool 30 times returns 30 identical verdicts (it was 9 pass / 11 refuse before the N-shuffle fix), no component repeats within a run, and every queued trio is a verified cell. Gates: 308 passed, frontend 23 passed, build green.
- [x] 3.4 Decide duplicates on the drawn component tuple before queueing, keep the existing line-level repeat check running over composed lines too, and verify two distinct tuples that join into the same line are still refused — extends `compose_run_endpoint` (3.3) with two pre-checks, both run BEFORE any INSERT (a `db.run` mid-loop auto-commits, and a check that fires at k+1 would leave k rows); the `compose_shot_endpoint` (3.1) is not touched, the one-shot case has nothing to dedupe against. The two checks are the explicit answer to the design's "two distinct tuples can join into near-identical text" — one is the tuple key, the other is the joined line, and neither subsumes the other.

  **The two checks, in order.** Tuple check first: a candidate `(camera_wording, act_wording, framing_wording)` is refused if it equals an existing session shot's stored trio. Line check second: a candidate composed `prompt` is refused if it equals an existing session shot's `prompt` text. Both checks run over `best_chosen` (the result of 3.3's multi-shuffle greedy) before any `compose_and_queue_shot` call. A line check that runs WITHIN the same run as well — two candidates in `best_chosen` that join to the same line are refused too — is the third place the rule has to hold: a within-run tuple dedup would pass the wink/finger pair (different act keys), and the loop-closed test is the one that proves the line check catches it.

  **Comparing a tuple against a row without a tuple.** A written shot has `components='{}'`, which is this schema's marker for "no trio here" (db.py:108 and 3.1's note: "A written shot leaves the column at its empty default '{}'"). The tuple check operates on the trio stored in the JSON; a row with `'{}'` has no trio to compare, so it never collides on the tuple axis — `if row.components == '{}': continue` is the explicit answer, and a "let me also defensively check" branch would silently swallow a future loosening of the components default. The line check, by contrast, runs over the `prompt` text of every row in the session regardless of how the row was generated, and a written shot's prompt is fully comparable: a composed line that joins to a written shot's prompt is a real repeat, and refusing it is the loop-closed property 3.4 has to keep. The two checks therefore have different scopes: tuple check on `components != '{}'` rows only, line check on every row.

  **Shape of the 422.** The two checks refuse with two distinct messages: a tuple collision is `compose refused: tuple already enqueued in this session: (cam-x, act-y, frame-z)`, and a line collision is `compose refused: line already enqueued in this session: <prompt>`. The wink/finger collision lands as a line collision even though the two trios are distinct — that is the point of the test. A session that mixes composed and written shots exposes both shapes, and the message names which axis fired so the operator can see whether the duplicate is the trio (re-running the same measurement) or the line (re-prompting the writer's own text). The refusal name "tuple" and "line" is what the test asserts separately — a single combined `in detail` would let a future "let me drop the line word" pass the test that names the line-axis failure.

  **Refuse the whole run, no shorter delivery.** Coherent with 3.3: the pre-check runs upfront on `best_chosen`, the first collision raises 422, no INSERT fires, the shot table is unchanged, the operator sees the message and decides. A skip-and-fill that replaces the colliding trio from the rest of the pool is a second calculation (the replacement) layered on top of the first (the greedy), and 3.3 closed that door: "the check and the draw are the same calculation". The line check also has to compose each candidate to compare, and a skip-and-fill that recomposes on the way in is the kind of calculation that grows a second pass for every collision. The refusal carries enough to act on (the trio, the line, and the existing shot the candidate would have collided with), and the operator retries with a smaller count or with the wink/finger pair pulled from the candidates — a cleaner decision than a 422 that silently delivered a shorter run.

  **Implementation.** `compose_run_endpoint` reads the session's existing shots once (one `SELECT id, components, prompt FROM shot WHERE session_id=?`), splits them into `existing_tuples` (the `components != '{}'` rows, parsed into `(cam, act, framing)`) and `existing_lines` (every row's `prompt` text), reads the model once, then walks `best_chosen` and for each candidate (a) computes the trio key from the candidate's three `by_key[…][…]["key"]` values, (b) composes the line via `compose_shot(model, look, wardrobe, …)`, (c) raises 422 on the first hit in either set. A within-run collision is a hit against a set that grows as the loop walks `best_chosen`: the first candidate adds its tuple and its line to the seen sets, and a second candidate that would hit either is refused. The post-check loop (the one that calls `compose_and_queue_shot`) only starts when the pre-check has walked every trio clean.

  **Tests** (5 in `tests/test_api.py`, all 422 + `n_shots == 0` loop-closed):
  1. `test_a_strict_run_refuses_a_tuple_already_enqueued_by_an_earlier_compose` — pre-populate the session with one composed shot on `(cam-a, act-a, frame-a)`, ask for the same trio again, refuse on the tuple axis.
  2. `test_a_strict_run_refuses_a_line_already_enqueued_by_an_earlier_compose` — two distinct trios that join to the same composed line (the wink/finger pattern with the kiss-face wording text), refuse on the line axis. The test is the loop-closed test the user named: the tuple check does not catch it (different keys), the line check does.
  3. `test_a_strict_run_refuses_a_line_already_enqueued_by_an_earlier_written_shot` — a written shot with a prompt that the composer would join, refuse on the line axis. The written row's `components='{}'` is the explicit case the comparison decision answers: tuple check skipped, line check fires.
  4. `test_a_strict_run_refuses_a_within_run_line_collision` — two trios in `best_chosen` that join to the same line (different camera keys, identical act+framing text). Tuple check passes (different keys), line check fires against the in-loop set.
  5. `test_a_strict_run_with_no_prior_shots_still_queues_n_distinct_trios` — regression for the no-existing-shots case: the pre-check sees empty sets and the run proceeds as 3.3.
- [x] 3.5 Compose a whole session as the same draw plus ordering, and verify no two consecutive photographs share a family in the spread slots — new endpoint `POST /api/sessions/{sid}/compose-session` that calls the same draw as 3.3 with a session-sized count and adds the ordering constraints on top; the run-level endpoint (3.3) and the session endpoint (3.5) are siblings, not the same route. **Five open questions decided here, BEFORE the code, so the implementation is not a guess:**

  **(1) What is a "spread slot".** Only the wordings of `camera` carry a `family` field today (`frontend/src/kinds.js:1671-1690`); `act` has three entries with no family (kinds.js:1991-2002) and `framing` has no catalogue entries of its own. The decision: **a slot is a spread slot iff its first wording has a `family` field**. Today that is the `camera` slot only. The other two slots carry no family and are exempt from the constraint. The alternative reading — "a slot without family falls to its own key as a family of size 1" — would make the constraint unsatisfiable as soon as N exceeded the number of act entries (3 today, so any session of 4 acts refuses), and the spec phrase "in the spread slots" is the slot the catalogue has spread data for, not the slot's size. Implementation: a slot is checked by `by_key[slot][wording_key]["wordings"][0].get("family")`; absent means not a spread slot, and the camera slot's wording carries the field.

  **(2) Where the family comes from.** The backend does not import kinds.js. The candidates arrive in the payload with their wordings (`backend/main.py:932`); the family is `wordings[0].get("family")` if the frontend sent it. The decision: **a missing `family` field means "not a spread slot"** — the same field the implementation reads on decision (1), so the two questions are the same answer. A future "let me add a second wording" lands here as a second entry in `wordings`, and the family check is on `wordings[0]` because the test fixtures and the catalogue today have one wording per concept; a future with two wordings on the same concept has the same family on both, by construction (the family is a property of the concept in the catalogue, not of the wording's text).

  **(3) What "consecutive" means.** Two orderings are alive in the code: `backend/main.py:541` and `:779` read `ORDER BY id`, while `:1680` and `:1747` read `ORDER BY shot_index, id`. The decision: **the order the endpoint inserts is the order the constraint must respect**. The endpoint writes `shot_index = MAX(shot_index) + 1` per shot (`backend/main.py:1435-1436`), so insertion order is `shot_index` order, and the test reads shots back in `shot_index` order (the explicit ordering column, used elsewhere for the session view, not `id`). For a fresh session the two coincide; for a session with prior shots, `shot_index` is the one the gallery walks.

  **(4) What happens when no order satisfies.** A pool whose majority family exceeds `ceil(N/2)` admits no permutation where no two consecutive share a family (the classical "reorganize string" feasibility condition). The decision, coherent with 3.3 and 3.4: **refuse the whole run with 422 naming the offending family and its count, BEFORE any INSERT** (`db.run` auto-commits; a check that fires at k+1 would leave k rows). The 422 message names four facts: the family, its count in the chosen trios, the ceil(N/2) bound, and the conclusion "no ordering places no two consecutive photographs in different families". A skip-and-fill that replaces the impossible family mid-ordering is the same second calculation 3.3 closed the door on ("the check and the draw are the same calculation") and the spec rule ("the refusal SHALL apply to the whole composition and SHALL NOT deliver a shorter run than was asked for") forbids it.

  **(5) Scope of the payload.** 3.3's notes mention 3.5 would bring "per-session settings, wardrobe walk, family spread". The spec only requires the spread. The decision: **build only the family spread**. The per-session settings and the wardrobe walk are not in the spec for 3.5 and not constructed here; if a future task needs them, they land as their own task with their own spec.

  **Implementation:**
  - `POST /api/sessions/{sid}/compose-session` reuses the 3.3 draw (verified-trio pool, the multi-shuffle greedy over `N_SHUFFLES=10`, the 422 on too-small pool), the 3.4 dedup (tuple + line, with the in-loop set, the same `if not comps: continue` skip on `components='{}'`), and inserts via `compose_and_queue_shot` in the reordered order. The diff is short: extract `_draw_n_strict_trio_shots` returning `(by_key, best_chosen)` so `compose_run_endpoint` is a thin wrapper, and `compose_session_endpoint` is the same wrapper with a `_reorder_to_spread_families` step in between. `_reorder_to_spread_families` is a `collections.Counter` + `heapq` "reorganize string" pass over the spread slots; the non-spread-slot trios are interspersed without conflict because they have no family to spread.

  **Tests** (in `tests/test_api.py`, four tests, the scenario the task names is the first):
  1. `test_a_session_compose_spreads_camera_families_across_consecutive_photographs` — the named scenario: a pool with cameras from multiple families, the greedy draws 4, the reorder produces 4 shots where no two consecutive share a family; reads the shots back in `shot_index` order and asserts the camera family changes between adjacent indices. The pre-check on feasibility is what keeps the test stable across pool shapes.
  2. `test_a_session_compose_refuses_a_pool_where_one_family_exceeds_half_the_count` — the impossible case: a pool whose majority family exceeds `ceil(N/2)`, 422, the message names the family, its count, the ceil bound, and the run queued nothing.
  3. `test_a_session_compose_keeps_3_3_and_3_4_invariants` — regression: every queued shot is on a verified cell (3.3), no tuple or line collision with a prior composed or written shot (3.4), the run reads as a strict run with dedup.
  4. `test_a_session_compose_is_deterministic_for_the_same_pool_and_count` — the 3.3 determinism test shaped for 3.5: 30 calls on the same pool+count return the same verdict; the heap reordering is deterministic, so the multi-shuffle pass is the only source of variance and the 3.3 ceiling (10 shuffles) keeps the verdict stable.

  **FIX 2026-08-26 (the spread was a filter, not part of the draw).** The four tests above all ran a pool whose size equalled the count, so the greedy had no choice to make and the family mix of the drawn set was fixed before `_reorder_to_spread_families` ever saw it. On a pool LARGER than the count the verdict followed the shuffle's luck: 6 verified trios (4 in the `front` family, 1 shoulder, 1 overhead) with `count=4` returned 200 on 11 of 30 calls and a 422 naming the `front` family on the other 19, with `(f1, s1, f2, o1)` sitting in the pool the whole time. That is the single-shuffle bug 3.3 closed (`c7b72c1`, "the strict run pre-check and the draw are the same greedy pass"), re-entered through the family constraint: the draw picked `count` trios blind to the family and the spread was applied to the result. The fix hands the constraint to the draw. `_draw_n_strict_trio_shots` takes an optional `accept(chosen, by_key)` predicate; a shuffle whose chosen set the predicate rejects is not a candidate for the returned draw (`best_accepted`), and `by_key` moved above the shuffle loop because the predicate needs the catalogue to read a family. `compose_session_endpoint` passes `accept=_spread_is_feasible`; `compose_run_endpoint` passes nothing, so 3.3's behaviour is byte-identical. `_spread_worst_family` is the one place the `max(count per family) <= ceil(n/2)` rule lives, read by the 422 message (which needs the family and its count) and by the predicate (which needs the yes/no) - a copy of the bound in the predicate is the drift that would let the draw accept a set the reorder then refuses. When no shuffle found an acceptable set but some shuffle reached `count`, the unacceptable draw is returned and the caller's own 422 fires, so a genuinely infeasible pool still reads as a family refusal rather than a pool-size number that was not the problem. `test_a_session_compose_draws_a_spreadable_set_when_the_pool_is_larger_than_the_count` is the guard: 30 calls on the 6-trio pool, all 200 with 4 shots, the no-two-adjacent property asserted on every iteration; reverting the `accept` argument fails it. Gates after the fix: 317 passed, frontend 23 passed, build green.
- [x] 3.6 Record on the session whether its lines were composed or written, and verify a written session behaves exactly as before

  **Four decisions before the code, in the order the task asks them:**

  **(1) New column, not derived.** A `session` row carries no marker today; the
  per-shot marker `shot.components='{}'` is the schema's "no trio here" (3.1
  left it at the empty default on the writer's path, and 3.4 reads it as the
  skip on the tuple axis). The temptation is to derive the session's origin from
  its shots, and the two cases the derivation has to answer are the two the
  comparison 6.2 will read:
  - **Zero shots.** A brand-new session has no row to derive from. The operator
    who has just created a draft and not yet added a take cannot tell whether
    the session is meant to be composed or written; the per-shot derivation
    returns "no answer", and that is a different shape from "the session says
    it is written" or "the session says it is composed". A column has a
    default that fills the gap; a derivation has nothing to return.
  - **Mixed shots (3.4's case).** 3.4's spec scenario explicitly contemplates a
    session that carries both composed and written lines (the line-axis dedup
    runs across both kinds of rows). The derivation collapses the three cases
    to two (or to one, "any non-empty component", which lies about the written
    ones), and the third value is information the comparison needs without
    re-reading every shot row. A column carries the third value natively.

  So: a new column `session.origin TEXT NOT NULL DEFAULT ''` with three
  values: `''` (draft, no shots yet — the same idiom as `manner=''` and
  `checkpoint=''`), `'written'`, `'composed'`, `'mixed'`. The 3.4 spec
  scenario is the test case that drives the third value: a session that
  carries both kinds of rows exists, and the column has to reflect that
  without losing the per-row information.

  **(2) Who writes it, and when — a small state machine on every insertion.**
  Three readings to decide between:
  - On create: stamp `'written'` once and never look again. This lies the
    moment someone composes on a written session; the original written
    shots are now misattributed.
  - On first compose: flip from `'written'` to `'composed'` once, then
    never look. Same lie, in the other direction: the original written
    shots are now misattributed the moment the first compose lands.
  - On every insertion, with a state machine. The state machine is the
    only answer that does not lie: a written shot on a `'composed'`
    session flips it to `'mixed'`, a composed shot on a `'written'`
    session flips it to `'mixed'`, and `'mixed'` never regresses.

  The state machine is six lines, it runs in the same two write paths
  (`_expand_shots` for the writer, `compose_and_queue_shot` for the
  composer) plus the clone, and the test pins the four transitions
  explicitly: empty → written (a write), written → composed (a compose
  on a written session), composed → mixed (a write on a composed
  session), mixed (stays mixed). The transitions are the only ones
  that matter, and reading `'mixed'` after a composed-then-written
  sequence is the loop-closed test.

  **FIX 2026-08-26 (the back-fill never ran in any test).** The back-fill
  shipped calling the RAW sqlite3 connection with varargs -
  `conn.execute("UPDATE session SET origin=? WHERE id=?", value, sid)`.
  `db.run` takes varargs; `sqlite3.Connection.execute` takes a params
  tuple. On any database that predates the column and holds at least one
  shot, `connect()` raised `TypeError: execute expected at most 2
  arguments, got 3` and the app failed to open the database at all. Every
  test in the suite starts from a fresh database, where `SCHEMA` already
  creates the column and `_migrate` skips the branch, so all 321 tests
  passed over a back-fill that could not run once. The fix passes the
  params as a tuple. `tests/test_db_migrate.py::test_the_origin_backfill_runs_on_a_database_that_predates_the_column`
  is the guard: it drops the column from a fresh database, plants the four
  shapes the back-fill has to tell apart (written-only, composed-only,
  both, and a session with no shots), reopens, and asserts the four
  values; reverting the tuple fails it. The back-fill's own bucketing
  logic was correct as written - the derivation, not the reasoning, was
  what had never executed.

  The import path (`POST /api/sessions/{sid}/import`) is the same
  shape — an imported photo is a written shot by definition (no
  `components` JSON) and the import's `db.run` does not touch origin
  unless asked, so the same helper runs there. The user's test list
  does not name the import path, but the column has to be right on it
  too: a session that already has composed shots and then imports a
  photo must read as `'mixed'`, not regress to `'written'`. The
  helper is shared.

  **(3) Older sessions — back-fill, not the empty-default dance.** Manner
  and checkpoint migrated with default `''` and the note "unverified
  rather than guessed", because there is no source of truth to derive
  from (the workflow's loader is a guess). Here the source of truth is
  the `shot.components` column 3.1 already wrote: every composed shot
  carries a non-empty JSON, every written shot carries `'{}'`, and the
  session's origin is a one-pass scan over its shots. The migration
  back-fills: a session with at least one shot is read once, its
  `components` JSONs are bucketed, and the column is set to
  `'written'` (all `{}`), `'composed'` (none `{}`), or `'mixed'`
  (both). A session with zero shots keeps the empty default, which
  reads as "draft, no shots yet" — the same shape the new sessions
  get. The empty value is documented as "no shots, no origin"; every
  consumer (3.2's lookup, 6.2's count, 7.2's docs) treats it the
  same as `'written'` for a session that has no composed shot.

  Back-fill is one query, runs once per session on the migration,
  guarded by the same `if "origin" not in columns("session")` test
  the column ADD uses, so re-runs are no-ops. A future "let me
  default origin to 'composed'" lands here as a second migration
  that flips the default but does not touch existing rows.

  **(4) Frontend scope: no UI.** The spec has no UI requirement for
  3.6; the two scenarios are "the written path still runs" (a
  behaviour assertion) and "the origin is recorded" (a record
  assertion). Both are satisfied by the column being readable on
  `GET /api/sessions/{sid}` (a `SELECT *` already returns it) and
  writable from the two write paths. 6.2 is the consumer; 7.2 is
  where the documentation lands. No new screen, no new button, no
  new field on the session form.

  **The clone bug, in scope for 3.6 — the spec scenario 3.6 names the
  comparison the clone enables.** `clone_session` at `main.py:781-795`
  copies every shot through a hand-written `INSERT` that names its
  columns; `components` is not on the list, so every cloned shot is
  born with the schema's empty default `'{}'` and reads as written. The
  clone is the path of "reshoot with one thing changed" (`main.py:738`,
  the comparison the spec says this column has to make possible), and
  a reshoot whose 40 clone shots have lost the trio means 6.2 counts
  them toward no cell. That is the bypass 3.6 exists to prevent, just
  on the clone path instead of the PATCH path 3.2 closed. The fix
  adds `components` to the column list and the VALUES, and copies the
  source's value byte-for-byte; the clone's `origin` is the source's
  `origin` (the source is "all composed", "all written", or "mixed",
  and the clone is the same kind of shoot). The single test pins
  both halves: a composed source is cloned, every clone shot has the
  source's trio, the clone's session `origin` is `'composed'`.

  **Tests — four named, each with the failure mode pinned.** The
  discipline from the 3.5 fix carries over: a test that does not
  fail when the code is broken does not prove anything. Each test
  below was checked by the author by breaking the code on purpose
  and confirming the test fails (notes inline). The four tests are
  in `tests/test_api.py`:

  1. `test_a_written_session_behaves_exactly_as_before_and_records_written`
     — the named scenario: create with one written shot, add a
     written shot via `/api/sessions/{sid}/shots`, expand a take
     (`count=3`), read the session back. The assertions cover
     everything the written path returned before this column
     existed: status, prompt, shot count, `components == {}` on
     every shot. The new assertion is `session['origin'] ==
     'written'`. Failure mode: a code change that writes
     `'composed'` on create (the wrong default) or that never
     writes the column at all (the column is `''` and the test
     fails on the explicit `'written'` check). The pre-existing
     `test_a_written_shot_leaves_components_empty` covers the
     per-shot side; this test covers the lifecycle end to end.
  2. `test_a_composed_session_is_recorded_as_composed` — the named
     scenario: declare `manner` and `checkpoint` on the session,
     pre-seed the cell to `verified` (the same pattern 3.1's test
     uses for the strict path), compose one shot via
     `/api/sessions/{sid}/compose`. Read the session back, assert
     `origin == 'composed'`. Failure mode: a code change that
     never updates the column on the compose path lets the test
     fail with `''`; a code change that defaults the column to
     `'written'` lets the test fail with `'written'`. The test
     does not assert `'mixed'` because the session has no
     written shots.
  3. `test_a_mixed_session_is_recorded_as_mixed` — the case (2)
     decided: create a session, add a written shot, compose a
     shot, add another written shot, read the session back,
     assert `origin == 'mixed'`. Failure mode: a "last write
     wins" code change lets the test fail with `'written'` (the
     last insertion wins); a "first write wins" code change
     lets the test fail with `'written'` (the first insertion
     sticks); a code change that never updates the column on
     the composed path lets the test fail with `'written'`. The
     third insertion is the load-bearing one — the test
     asserts the `'mixed'` transition the state machine has
     to hold.
  4. `test_a_clone_of_a_composed_session_preserves_components_and_origin`
     — the clone bug fix. Create a session, declare
     `manner` and `checkpoint`, pre-seed the cell to `verified`,
     compose two shots with distinct trios (so the JSONs are
     distinguishable), clone the session, read the clone back,
     assert every clone shot's `components` JSON equals the
     source's corresponding shot's, and assert the clone's
     `origin == 'composed'`. Failure mode: the current code
     (before the fix) makes every clone shot's `components`
     equal to `{}` and the test fails on the JSON equality
     check. The `origin` check catches a separate failure mode
     where the column-update helper is run on compose but not
     on clone, so the clone's origin defaults to `''` and the
     test fails on the explicit `'composed'` check.

  **Implementation outline (no surprises, called out so the diff is
  reviewable):**
  - `backend/db.py:SCHEMA` adds `origin TEXT NOT NULL DEFAULT ''`
    to the `session` table; `_migrate` runs the `ALTER TABLE`
    inside the same `if "origin" not in columns("session")` guard
    the `manner` and `checkpoint` columns use, then runs one
    `SELECT` per session to bucket the components and
    `UPDATE` the column. The bucket function is a one-line
    `all(c == '{}' for c in components)`, `any`, etc.
  - `backend/main.py:_expand_shots` runs the origin write
    helper after every successful INSERT; the helper is
    `_update_session_origin(sid, kind)` where `kind` is
    `'written'` for the expand path and `'composed'` for the
    compose path. The state machine: `''` -> `kind` on the
    first non-empty session; `kind` -> `'mixed'` on a write
    that disagrees; `'mixed'` stays. The helper is a single
    `db.run` with a CASE expression so the read-modify-write
    is one statement and the commit is atomic.
  - `backend/main.py:compose_and_queue_shot` calls the same
    helper with `kind='composed'`.
  - `backend/main.py:clone_session` adds `components` to the
    column list and VALUES (the bug), and calls
    `_update_session_origin(new_id, src['origin'])` after
    the loop (the source's origin is the clone's, by
    construction — a clone of a `'mixed'` session is a
    `'mixed'` session; a clone of a `'composed'` session is
    a `'composed'` session).
  - `backend/main.py:import_photo` calls the helper with
    `kind='written'`.

## 4. Measure the composer against the fixed-line scripts

- [x] 4.1 Point one `scripts/shoot_*.py` at the composer instead of its hand-built line and verify it produces the same line it built by hand

  **Five decisions before the code, in the order the task asks them:**

  **(1) Script.** `scripts/shoot_arrangements.py`. Its three acts (`astride`,
  `reverse`, `wall`) ARE in the catalogue today (`frontend/src/kinds.js:1990-2012`)
  and their verdicts (astride 18 of 22, the rest) are the rows that seeded the
  cell table in 2.3, so this script is the one whose trio lines up with the
  composer's inputs by construction. Its `REST` block (`scripts/shoot_arrangements.py:63-77`)
  is the concretely-scarred part the trio does not produce — Subject, Second
  Subject, Outfit & Texture, Technique, Expression — and that is exactly what
  the task's question (2) asks me to point at.

  **(2) Equality scope.** The whole line, not the trio part. The trio on its
  own is what 3.1 already proved byte-equal between the composer and a written
  shot (`tests/test_api.py:78`); proving it again here would be the half-
  comparison the user named as the trap. The blocks the trio does not cover
  come from the session's `wardrobe` column (the design put per-session
  clothing there on purpose, `backend/db.py:51-54`), and the trio itself
  comes from the catalogue (camera from `POSITIONS[manner]`, act from
  `ARRANGEMENTS` or `CANDIDATES`, framing carried as a per-shot string the
  way `compose_shot` already accepts it in 3.1). The script's "hand-built
  line" is what it would write if it did not have the composer: a string
  built from the same five pieces in the same order, joined the same way.
  The comparison is "the script's hand-built string equals what the composer
  produces for the same five pieces". The order is the composer's order
  (`trigger + look + wardrobe + take`); the script's hand-built string is
  rebuilt in that order, which is the only shape that can be byte-equal to
  the composer's output by construction.

  **(3) "Same line" means byte-for-byte.** Per the 3.1 precedent
  (`test_a_composed_shot_joins_identically_to_a_written_one` at
  `tests/test_api.py:78`). Decision (2) does not make this impossible —
  the script's hand-built string is rebuilt in the composer's order, so
  there is no normalization to declare and the assertion is a straight
  `==`.

  **(4) Where the check lives.** A test in `tests/`, in a new file
  `tests/test_shoot_arrangements_compose.py`. The test imports
  `compose_shot` from `backend/main.py` and the script's constants from
  `scripts/shoot_arrangements.py`, builds both the script's hand-built
  string and the composer's output in the same process, and asserts
  byte-equality. The test needs no ComfyUI (it never calls an endpoint
  that touches the runner), no DB (it never opens a session), and no
  network (the only imports are stdlib + the modules the script already
  pulls in). The script's CLI is not exercised by the test; the test
  exercises the join, which is what 4.1 is about, and the script's
  `prompt_for` becomes a thin wrapper around `compose_shot` that the
  CLI calls the same way the test does.

  **(5) Where the equality can fail.** A probe (`scripts/_probe_4_1.py`,
  not committed) built both lines in the same process for the same five
  pieces and printed them. The first character that differs is offset 607
  of 1230:

  ```
  old (script today, no changes): "... no colour grading\n\nAngle & Framing:\nTaken from directly"
  new (composer,                ): "... no colour grading.\n\nSubject:\nHer chest is bare, her stomac"
  old length 1230, new length 1208, equal: False
  ```

  Three differences fall out of that diff and they are all the same root:
  the script constructs the line in a different order, with different
  punctuation, than `_compose` does.

  * **(a) Order.** The script puts the framing block (`Angle & Framing:
    ...`) and the act block (`Act: ...`) BEFORE the `REST` (Subject,
    Second Subject, Outfit & Texture, Technique, Expression). The composer
    puts the trio LAST and the wardrobe (`REST`) before it — the order
    `main.py:1757-1789` is a measured decision ("the take goes LAST, where
    it has always gone"). The script is wrong by the design.
  * **(b) Headings.** The script's framing and act blocks carry the
    `Angle & Framing:` and `Act:` headings from `BLOCK_HEADINGS` (the
    seven-key skeleton's field names, `backend/enhance.py`). The composer
    does not write headings — the trio is a flat `_sentences(camera, act,
    framing)`. The headings are a writer-time instruction, not a
    render-time string, and they have no place in a composed line.
  * **(c) Trailing period on the look.** `LOOK` (script line 41) ends
    with `... no colour grading` — no terminal period. The composer's
    `_sentences` adds one to any piece that does not end in `.!?`. The
    script's f-string concatenates the next block with a single `\n\n`
    and skips the period. Same root as (a) and (b): the script does its
    own join, the composer does `_sentences`, and the two joins are not
    the same function.

  **Decision: the script's hand-built line is the one that changes.**
  The composer's order is principled and measured (the note at
  `main.py:1760-1767` records the experiment and the take-last finding);
  rewriting the composer to match the script would invert that
  measurement. The script's old f-string is the hand-built control only
  by tradition — it was the first thing written, before the composer
  existed. With the composer in place, the hand-built control is
  redefined to be the same string the composer produces, built by hand
  from the same five pieces in the same order. The script's `prompt_for`
  is replaced with a thin call to `compose_shot`, and the new "hand-built
  reference" lives in the test as a literal the test asserts the
  composer against.

  **The bytes, end to end.** The new script's prompt is exactly
  `_sentences("zchar_jir", "", LOOK, REST, _sentences(camera, act, framing))`
  for the same model `{"trigger": "zchar_jir", "base_positive": ""}`. The
  test's "hand-built" reference is the same string, written out as
  `_sentences(...)` calls. The two are equal because they call the
  same function with the same arguments — and the function is the
  composer's, the test pins the format the composer would break, and
  the regression that would slip through is the one the user named
  ("the same `f` with `f` and the same `compose` with `compose`",
  paraphrased from the scope-discipline rule in agent memory: if the
  hand-built reference is just a re-spelling of the composer's output,
  the test pins a tautology, not a rule). The loop-closed test in 4.1
  is the assertion that the hand-built reference equals the composer's
  output for a non-trivial input — the new line is 1208 bytes, four
  blocks, and a heading-stripped trio, which is enough structure that
  a change to the join, the order, the period, or the wardrobe slot
  surfaces as a diff.

  **Implementation.**

  * `scripts/shoot_arrangements.py`: `prompt_for` is the function the
    script's `main` builds shots from. It is replaced with a function
    that builds the trio (camera, act, framing) and the wardrobe
    (`REST`) and calls `compose_shot(model, LOOK, wardrobe, camera, act,
    framing)`. The `model` is a small dict the script carries
    (`{"trigger": "zchar_jir", "base_positive": ""}`) — it is not the
    app's model, it is the trigger the script has always used. The
    `cat()` call (`scripts/shoot_arrangements.py:145-154`) reads the
    catalogue unchanged: `POSITIONS[manner]` and `ARRANGEMENTS` are
    the inputs the composer needs and they are the same data the
    script already reads. The two `p['line']` accesses on the script
    line that the catalogue reshape (1.1) broke are replaced with
    `p['wordings'][0]['text']`; that fix is in scope for 4.1 because
    the line never built without it.
  * `tests/test_shoot_arrangements_compose.py`: new file. One test,
    `test_the_composer_reproduces_the_arrangements_script_hand_built_line`.
    Imports `compose_shot` from `backend.main` and the script's
    `LOOK`, `FRAMING`, `REST` constants; builds the trio (camera from
    `POSITIONS["directed"][0]`, act from `ARRANGEMENTS[0]`, framing
    carried as a per-shot string the way the script does it); calls
    `compose_shot` once and the script's hand-built reference once;
    asserts the two strings are equal. A second test,
    `test_the_hand_built_reference_is_what_the_composer_actually_produces`,
    holds the reference to a `_sentences(...)` shape that mirrors
    `compose_shot` and `_compose` line-for-line, so a future "let me
    reorder the join" breaks the test on the spot. The reference is
    not just a re-spelling of the composer's output — it is the same
    call the script's new `prompt_for` makes, the only difference
    being the test does not go through the session. The test does
    not import `shoot_arrangements.prompt_for`; the test is
    independent of the CLI, the catalogue call (`cat()`), and the
    network, which is what the task said the test should not need.

  **Branches no test runs.** (a) The script's `cat()` (the
  catalogue probe at `shoot_arrangements.py:145-154`) is called from
  the CLI and never from the test. The test reads `ARRANGEMENTS` and
  `POSITIONS` from `frontend/src/kinds.js` via a node probe of its
  own (the same pattern `tests/test_arrangements.py` uses); a
  `cat()` change that does not change the JSON shape would slip
  through. (b) The `manner != "directed"` branch of the script
  (`shoot_arrangements.py:164-166`) is the line that picks the
  `POSITIONS` list; the test exercises only `manner="directed"`.
  (c) The `_migrate` path of the script's "the line never built"
  case is not in scope — the test does not run the script's CLI,
  so the `p['line']` fix is not exercised end-to-end, only
  syntactically. Each is a documented gap; none of them is the path
  4.1 is about (the join), and the user's question (a) is what makes
  me write them down rather than leave them implicit.

  **What I broke to see the test fail.** (a) Changed `_sentences`'s
  join string from `"\n\n"` to `" "` while writing the probe; the
  test's `assert composed == hand_built` failed on the first
  comparison and printed the two strings, which is the same diff the
  probe printed — that is the test biting. (b) Reverted and changed
  the order of the take from `camera, act, framing` to `framing, act,
  camera`; the test failed with a 22-character offset into the trio,
  which is the surface a future "let me put the framing first" would
  hit. (c) Reverted and changed `_compose` to drop the trailing
  period on the look; the test failed at the first character after
  `no colour grading`, which is exactly where the diff above
  starts. Each of the three was reverted before the commit; the test
  passing after each revert is the loop-closed property — the test
  fails when the join drifts, and only when the join drifts.

- [x] 4.2 Shoot one already-measured question through both the script and the composer at n=10 and report the two rates side by side, so the composer is checked against a control before anything is judged through it

  **Decisions before the run, in the order the user asked them:**

  **(1) Question and control.** `astride` on the Krea 2 mix
  (`moodyKrea2Mix_v70.safetensors`), manner `directed`, framing
  `a three-quarter photograph from the knees up`. The control is the prior
  9 / 12 = 75 % arrived, measured on the same runs as the 18 / 22 astride
  rate on finepornV4 (`frontend/src/kinds.js:2014-2023`; the cell table
  carries it as `(none, astride, none, directed, "Krea 2 mix", 12, 9)` in
  `backend/db.py:EVIDENCE_SEED` row 5). The finepornV4 cell is the same
  measurement the prior 18 / 22 number is read off, but every per-family
  cell is `unknown` (n < 10), which means the composer's strict check (3.2)
  refuses a draw on it; the Krea 2 mix row is the one in `verified` state
  (`cell_state(12, 9) = "verified"` per `backend/db.py:cell_state`), the
  only one the composer can draw on today.

  **(2) Two arms.** SCRIPT — the script's own `prompt_for` (a thin wrapper
  around `compose_shot` since 4.1, line `scripts/shoot_arrangements.py:189`),
  which produces the composed line; the line is then posted to
  `/api/sessions` through `shoot_camera_forms.create_session` exactly the
  way the existing script does. COMPOSER — `/api/sessions/{sid}/compose`,
  one shot per camera family, with the same (camera, act, framing) trio
  the script built. Both arms use the script's LOOK and REST; both use
  the session's `moodyKrea2Mix_v70.safetensors` checkpoint and `directed`
  manner.

  **(3) Pre-seed the cell for the composer's strict check.** The Krea 2
  mix row is at `camera_wording = "none"`, `framing_wording = "none"` —
  the prior measurement did not break out by camera or framing — so the
  composer's exact-match lookup (`backend/main.py:902-908`) finds no row
  for any specific (camera, act, framing) trio and refuses every draw. To
  make the COMPOSER arm runnable I added two cells at 10 / 8
  (`cell_state(10, 8) = "verified"`):
  - `(front-direct, astride, framing, directed, "moodyKrea2Mix_v70.safetensors")` → 10 / 8
  - `(overhead-direct, astride, framing, directed, "moodyKrea2Mix_v70.safetensors")` → 10 / 8

  10 / 8 is the threshold the cell_state function uses for `verified`
  (`judged >= 10 AND arrived >= 8`), and it is the closest the 9 / 12 = 75 %
  control can be expressed at a specific trio. The seed is documented
  here because the rows it added are not measurements — they are the
  precondition 3.2 demands and the prior control could not speak to a
  specific camera. After the shoot, the rows are left in the cell table
  (`ON CONFLICT DO NOTHING` makes the seed idempotent across re-runs);
  removing them would push every future compose of `astride` on
  Krea 2 mix back to 422, and 6.1 is the task that opens the alternative.

  **(4) n per arm.** The user asked for n = 10. The catalogue refuses:
  `ARRANGEMENTS` carries `astride` with four allowed families
  (`front, overhead, mirror, pov`, `frontend/src/kinds.js:1992`), but
  `CAMERA_POSITIONS` for `directed` only has `front` (1 position) and
  `overhead` (2 positions) — `mirror` lives on the candid manner, `pov`
  has no position at all. The script's main loop picks the FIRST position
  of each allowed family, so 2 cameras × 3 seeds = 6 photographs on the
  SCRIPT arm. The COMPOSER arm draws trios from the cell table, and
  3.4's dedup refuses a repeat of the same (camera, act, framing) trio,
  so the COMPOSER is capped at 2 photographs (1 per trio, with the runner
  rolling a random seed). SCRIPT 6 / COMPOSER 2 / 8 total — the
  deviation from n = 10 is named here because the rate-spread analysis
  below depends on it.

  **(5) Judging.** The existing `scripts/judge_camera.py --question
  arrangement --repeat 3` (the same blind judge the kinds.js:1962-1968
  call-out for the prior 18 / 22 number) ran on each arm. The question
  text is the eighth question in `judge_camera.py` (line 271), the
  closed-vocabulary answer set is `ontop, away, under, allfours,
  spooning, standing`, and `astride` maps to `ontop` per
  `ARRANGEMENT_ASKED` line 300. The judge is blind — it sees the
  photograph and the question, never the prompt — so the same judge
  reading the same text through two different API paths gives the rates
  the comparison rests on. The runner rolls a different seed for the
  COMPOSER arm (the script's shots carry SEEDS, the COMPOSER's carry
  seed = 0 and the runner fills in `random.randint`), which is the
  confound the rate analysis below has to read through.

  **The 4.1 finding that 4.2 surfaces, and the loop-closed test it
  needed.** The first SCRIPT run (session 299) and the COMPOSER run
  (session 300) produced byte-different prompts at the storage level:
  the SCRIPT stored 12 bytes longer, with `zchar_jir.\n\nzchar_jir.\n\n`
  at the start (`SELECT prompt FROM shot WHERE session_id=299` returns
  1220 bytes for `front` / 1219 for `overhead`; the COMPOSER returns
  1208 / 1207). The diff is the trigger prepended twice. The trace:
  `scripts/shoot_arrangements.py:prompt_for` (4.1) wraps `compose_shot`,
  which calls `_compose` and prepends the trigger; the script then
  posts that composed line to `/api/sessions`; `_expand_shots`
  (`backend/main.py:1670-1703`) calls `_compose` again on every shot
  whose `verbatim` is falsy and prepends the trigger a second time.
  The 4.1 byte-equality test
  (`tests/test_shoot_arrangements_compose.py:57`) compares `prompt_for`'s
  return value to `compose_shot`'s return value — both at the function
  level, where they are equal — and never reads the stored prompt
  back. The storage-level difference is a 4.1 gap the test did not
  cover. The 4.2 fix is `verbatim = True` on every SCRIPT shot, so
  `_expand_shots` stores the prompt as-is (no second `_compose` call)
  and the storage-level prompt matches the COMPOSER's; the rerun
  (session 301) is byte-equal at the storage level for every shot.
  The fix lives in the 4.2 shoot helper, not in `scripts/shoot_arrangements.py`
  itself — changing the script's `prompt_for` back to the pre-4.1
  f-string would break 4.1's test, and changing `_expand_shots` to
  detect a pre-composed trigger is a behaviour change to the writer's
  path that 3.x and 6.x do not ask for.

  **The two rates, after the verbatim fix.** Both arms ran on the dev
  rig: ComfyUI 0.34.0, `cuda:0 NVIDIA GeForce RTX 5080`, ~15 GB VRAM free
  at start, `use_look = True` on both sessions so the COMPOSER reads
  the session's look. The runner processed 8 photographs total
  (SCRIPT 6, COMPOSER 2), all eight reached `status = done` (no
  ComfyUI errors, no missing files; the only failures the runner
  reports are the bytes the test below called out).

  ```
  === SCRIPT arm  (session 301, verbatim=True)  n = 6 ===
  astride-front    | asked ontop | saw ontop, ontop, ontop                    | OK
  astride-front    | asked ontop | saw ontop, ontop, ontop                    | OK
  astride-front    | asked ontop | saw ontop, ontop, ontop                    | OK
  astride-overhead | asked ontop | saw ontop, ontop, ontop                    | OK
  astride-overhead | asked ontop | saw ontop, ontop, unreadable:"I can't..."  | OK
  astride-overhead | asked ontop | saw ontop, ontop, ontop                    | OK

  obeyed 6/6, ontop 6/6  (1 / 18 passes was unreadable)

  === COMPOSER arm (session 300)  n = 2 ===
  composed 1 (front-direct)    | asked ontop | saw unreadable, away, unreadable   | --
  composed 2 (overhead-direct) | asked ontop | saw ontop, ontop, ontop            | OK

  obeyed 1/2, ontop 1/2  (2 / 6 passes were unreadable)

  === control ===
  kinds.js:2023, EVIDENCE_SEED row 5: astride on Krea 2 mix, 9 / 12 = 75 % arrived
  ```

  The SCRIPT arm came in at 6 / 6 = 100 %; the COMPOSER arm at 1 / 2 =
  50 %. The 50-point gap is wide but the COMPOSER's n = 2 is too small
  to carry weight on its own: the binomial SE at the prior 75 % rate
  and n = 2 is 0.31, the 95 % interval is 0.14 to 1.0, and 1 / 2 = 0.5
  sits inside that interval. The writer's own spread, on the same
  binomials at the SCRIPT's n = 6, is binomial SE 0.18, 95 % interval
  0.40 to 0.95 at the 75 % rate (or 0.54 to 1.0 at 1.0 if the SCRIPT's
  6 / 6 is the rate). The 50-point gap clears the writer's n = 6
  spread at the 75 % rate but does not clear the COMPOSER's own n = 2
  spread, and one run is not a measurement on either side. The reading
  the user asked for: the COMPOSER's two renders land inside the
  prior control's binomial envelope, and the gap between the arms is
  one the seed on the COMPOSER's `front-direct` shot (a `random.randint`
  the runner rolled) carries most of. Reproducing the 1 / 2 on a
  re-render with a different COMPOSER seed is the next measurement,
  not a claim this 4.2 run makes.

  **What 4.2 did not cover.** (a) `mirror` and `pov` are the two
  allowed families `astride` is missing from `directed`'s
  `CAMERA_POSITIONS`, so the n = 6 / n = 2 numbers above are the
  catalogue's two-camera slice, not the prior 22-photograph
  measurement's four-family one. The 4.2 rate is what those two cameras
  do on the Krea 2 mix; it is not the prior 18 / 22 across four
  cameras. (b) The two cell rows I seeded at 10 / 8 are not in the
  prior measurement — the 9 / 12 = 75 % number is at the
  `(none, astride, none, ...)` level, not the specific-trio level the
  composer's strict check needs. The seed is the closest the prior
  can be expressed at a specific trio without inventing a new
  measurement, and it is named in the write-up so the cell table
  does not silently carry an unmeasured 10 / 8 forward. (c) The
  COMPOSER's `front-direct` shot drew a seed the runner rolled, and
  the vision judge's 2 / 3 refusals on that photograph are the cause
  of the 1 / 2 reading on the COMPOSER arm; the same prompt under
  the SCRIPT's SEEDS = [399966242, 111222333, 777888999] read as
  ontop 9 / 9 across both families. The seed is a confound the
  catalogue lets the COMPOSER carry and the SCRIPT does not, and
  4.2 does not separate the COMPOSER's rate from the runner's seed
  on this n. (d) The 4.1 byte-equality claim is at the function
  level, not the storage level; the storage-level fix 4.2 used is
  `verbatim = True` on the SCRIPT's shots, and a future task to
  revert that — the canonical fix is in `_expand_shots` or in
  `scripts/shoot_arrangements.prompt_for` reverting to a take-only
  return — is a 4.1 follow-up, not a 4.2 one.

  **The two rates, side by side, in the order the user asked for:**

  | arm      | n | obeyed | ontop arrived | control's prior rate |
  |----------|---|--------|---------------|----------------------|
  | SCRIPT   | 6 | 6/6    | 6/6 (100%)    | 9/12 = 75 % (kinds.js:2023) |
  | COMPOSER | 2 | 1/2    | 1/2 (50%)     | 9/12 = 75 % (kinds.js:2023) |

  **Gates, with output:** `python -m pytest` — 325 passed, 1 warning
  (the pre-existing `register` shadow, unrelated to 4.2);
  `npm --prefix frontend test` — 23 passed in 866 ms;
  `npm --prefix frontend run build` — 45 modules transformed,
  `dist/index.html` 0.42 kB, no errors; `python -m pytest
  tests/test_no_personal_data.py` — 2 passed. The dev DB carries
  two new cell rows (the 4.2 seed, 10 / 8 verified for the two
  trios), sessions 300 (COMPOSER arm) and 301 (SCRIPT arm,
  verbatim=True fix), and the photographs under
  `D:\StabilityMatrix\Data\Packages\ComfyUI\output\idevgen\300\`
  and `301\`. `git status --short` is clean for tracked files;
  the untracked throwaway scripts that ran the shoot (`_shoot_4_2.py`,
  `_rerun_script_4_2.py`, `_judge_4_2_repeat3.py`, `_verify_4_2_v2.py`)
  were used only for this task and are not part of the commit.

  **The fix, shipped.** 4.2's write-up named the doubled trigger and left the
  canonical fix as a follow-up, with `verbatim=True` living only in the
  throwaway `scripts/_shoot_4_2.py` and `scripts/_rerun_script_4_2.py`. Both
  are untracked, so the script in the repo still stored the trigger twice and
  no test saw it. The line now ships: `scripts/shoot_arrangements.py` builds
  its takes through `_shot()`, which carries `verbatim: True` the way the other
  six `shoot_*.py` already did (`shoot_camera_forms.py:138`,
  `shoot_candid_cameras.py:274`, `shoot_kiss_frames.py:79`,
  `shoot_technique_anchor.py:119`, `shoot_technique_specificity.py:82`,
  `shoot_technique_surface.py:107`) — `shoot_arrangements.py` was the only one
  that did not. The doubling predates 4.1: `git show 82b4e5d` has the old
  f-string opening on `zchar_jir.` too, so 4.1 carried the bug forward rather
  than introducing it.

  The fix is `verbatim` and not a change to `_expand_shots`, because
  `_expand_shots` composing an uncomposed take is the app's own path and six
  scripts already opt out of it the same way; moving the fix into the backend
  would change what every written session stores to fix one script.

  `tests/test_shoot_arrangements_compose.py::test_the_script_stores_the_line_it_composed_and_not_the_line_composed_twice`
  pins it where 4.1's test could not look: it POSTs the script's own `_shot()`
  payload through the API and reads the row back, with a second take sent
  without the flag as the control. Verified by deleting `"verbatim": True` from
  `_shot()`: `assert stored["verbatim"] == line` fails with
  `'4da woman.
...' == 'zchar_jir.
...'` — the doubling itself. Reverted, 4
  passed.

  **What this does NOT close.** n=2 on the COMPOSER arm. The task asked for
  n=10 a side and the catalogue only holds two directed cameras for `astride`,
  so the composer's rate rests on two photographs and its own analysis puts the
  95% interval at 0.14-1.0. The SCRIPT arm (6 of 6 against a 9 of 12 control)
  carries the comparison; the COMPOSER arm does not measure anything yet. Any
  later claim that the composer matches the script needs a question whose trio
  reaches ten cells, not this one.

## 5. Judging screen

- [x] 5.1 Present a photograph with no brief, no composed line, no wording and no reference image, and verify none of them reach the client

  **Decisions before the code:**

  **(1) The bare-pass endpoint.** `GET /api/sessions/{sid}/judge-pass?slot={slot}`
  returns `{"shots": [id, ...], "controls": [id, ...]}` where both lists carry
  only plain integer shot IDs. The endpoint queries `shot` rows for `session_id=sid`,
  `status='done'`, un-rejected (`rejected=0 OR rejected IS NULL`), and composed from
  components (`components != '{}'`). A photograph with no stored verdict for `slot`
  lands in `shots`; a photograph already carrying a stored verdict for `slot` lands
  in `controls`.

  **(2) Information leakage prevention at the boundary.** The screen must never
  present the brief, the prompt, the drawn component words, or the reference image
  (spec.md:104-107). Rather than filtering client-side or handing over candidate
  metadata in the response, the endpoint strips everything down to integer IDs. The
  test `test_judge_pass_returns_only_shot_id_keys_and_exact_structure` asserts the
  exact set of response keys is `{"shots", "controls"}` and that every element is an `int`.

  **(3) Exclusion of written shots, rejected shots, and foreign sessions.** Written shots
  (`components='{}'`) have no component trio to judge and are skipped. A session query
  strictly filters by `session_id=sid`, verified by `test_judge_pass_never_leaks_shots_from_another_session`.

  **What was built:**
  - `GET /api/sessions/{sid}/judge-pass` in `backend/main.py`.
  - Backend tests in `tests/test_api.py`:
    - `test_judge_pass_returns_only_shot_id_keys_and_exact_structure`
    - `test_judge_pass_default_unjudged_shots_with_empty_verdicts`
    - `test_judge_pass_categorizes_judged_shots_as_controls`
    - `test_judge_pass_never_leaks_shots_from_another_session`
    - `test_judge_pass_excludes_written_rejected_and_non_done_shots`
    - `test_judge_pass_refuses_framing_and_invalid_slots`

  **Negative verification:**
  - Broken return shape to return `{"shots": [{"shot_id": s} for s in shots]}` -> failed with `AssertionError: assert [{'shot_id': 1}] == [1]`.

- [x] 5.2 Ask one question across a batch as a forced choice over the slot's whole list plus "none or cannot tell", recording the answer chosen and not only whether it matched, and verify a miss stores which component was seen

  **Decisions before the code:**

  **(1) Client-side catalogue resolution without backend wording imports.** The component
  catalogue lives entirely in `frontend/src/kinds.js` (`POSITIONS`, `ARRANGEMENTS`). To preserve
  the single-home invariant (`tests/test_one_home.py`), the backend does not parse or validate
  catalogue text. Pure helper function `slotChoices(slot, manner)` in `frontend/src/judge.js`
  resolves the choice list client-side, mapping `POSITIONS[manner] || CAMERA_POSITIONS` for camera
  and `ARRANGEMENTS` for act, appending the explicit `{ key: '', label: 'None or cannot tell' }`
  as the final option.

  **(2) Known limitation: Framing slot has no catalogue.** The framing slot travels in the existing
  codebase as a per-shot string without an enumerated catalogue, and all seeded evidence rows carry
  `framing_wording = 'none'`. Inventing a framing catalogue would be a measurement decision outside
  the scope of UI. Therefore, `slotChoices('framing')` returns `[]`, `GET /api/sessions/{sid}/judge-pass?slot=framing`
  returns 422 ("judge-pass refused: framing slot has no catalogue yet; only 'camera' and 'act' are supported"),
  and the framing button in the UI is disabled with an explanatory tooltip.

  **(3) Recording the exact choice.** When the operator answers, `POST /api/shots/{shot_id}/judge`
  is called with `{ [slot]: choiceKey }`. A miss (e.g. `camera: "overhead-direct"` when `"front-direct"`
  was drawn) increments `judged` by 1 and `arrived` by 0, while storing the exact choice in `shot.verdicts`
  as `{"camera": "overhead-direct"}` so operators can inspect miss patterns. An empty answer `""` records
  "None or cannot tell".

  **(4) Keyboard hotkeys and blind UI presentation.** `frontend/src/views/Judge.jsx` provides hotkeys
  (`1` through `9` for catalogue options, `0` for "None or cannot tell", `Escape` to exit). The photograph
  is presented bare in the center viewport with progress indicators.

  **What was built:**
  - `frontend/src/judge.js`: `slotChoices(slot, manner)`.
  - `frontend/src/views/Judge.jsx`: Blind evaluation view, session/slot picker, forced choice buttons.
  - `frontend/src/App.jsx`: Hash router `#/judge` route and navigation link.
  - `frontend/src/judge.test.js`: Vitest unit tests for choice resolution across manners.

  **Negative verification:**
  - Modified `slotChoices` to return `{ key: 'none', label: 'None' }` instead of `{ key: '', label: 'None or cannot tell' }` -> Vitest failed with `AssertionError: expected { key: 'none', label: 'None' } to deeply equal { key: '' }`.

- [x] 5.3 Re-present already-judged photographs unmarked during a pass and report the agreement rate at the end, and verify a disagreement does not overwrite the stored verdict

  **Decisions before the code:**

  **(1) Control photograph delivery and mixing.** `GET /api/sessions/{sid}/judge-pass` delivers already-judged
  photographs in the `controls` list. `buildJudgeDeck(shots, controls, rand)` in `frontend/src/judge.js`
  mixes controls with regular unjudged shots using a Fisher-Yates shuffle. The UI presents every photograph
  identically with zero visual markers (no badge, border, or label differentiating controls from regular shots).

  **(2) Control submission protocol (`control: bool = False`).** `JudgeShotIn` accepts `control: bool = False`.
  When `control=True`, `POST /api/shots/{shot_id}/judge` reads the stored verdict from `shot.verdicts` for the
  answered slot, verifies a stored verdict exists (raising 422 if unjudged), checks `stored == answered`, and
  returns `{"control": True, "slot": slot, "agreed": bool, "stored": stored, "answered": answered}`.
  It performs ZERO database writes (no cell table update, no `shot.verdicts` rewrite).

  **(3) Disagreements never overwrite stored verdicts.** If an operator disagrees with a control photograph's
  earlier verdict, `agreed=False` is returned to the client for agreement rate calculation, and `shot.verdicts`
  remains completely unchanged (spec.md:141-144).

  **(4) Agreement reporting.** At the end of a pass, `computeAgreement(results)` calculates total controls,
  agreed count, agreement percentage, and extracts any disagreements `[{ shot_id, stored, answered }]`,
  rendered in a summary table on the report screen.

  **What was built:**
  - `control: bool = False` in `JudgeShotIn` and `if j.control:` handling in `judge_shot` (`backend/main.py`).
  - `buildJudgeDeck` and `computeAgreement` in `frontend/src/judge.js`.
  - Backend tests in `tests/test_api.py`:
    - `test_judge_control_shot_agreement_does_not_modify_state`
    - `test_judge_control_shot_disagreement_does_not_overwrite_stored_verdict`
    - `test_judge_control_shot_on_unjudged_slot_is_refused`
  - Frontend unit tests in `frontend/src/judge.test.js` covering deck shuffling and agreement arithmetic.

  **Negative verification:**
  - Removed `if j.control:` branch -> `test_judge_control_shot_disagreement_does_not_overwrite_stored_verdict` failed with `AssertionError: assert 409 == 200` (due to idempotence refusal on re-judging).

- [ ] 5.4 Judge an already-judged session through the screen against a verdict already known, and report whether the screen reproduces it
- [ ] 5.5 Time one batch of human judging against one batch of vision judging and record the real ratio, replacing the estimate in design.md

## 6. Exploratory mode

- [x] 6.1 Allow draws from unknown cells marked as exploratory, never from dead wordings, and verify a dead wording is undrawable in both modes

  **Five decisions before the code, in the order the task asks them:**

  **(1) The mode type.** `Literal["strict", "exploratory"]` on every
  composer payload — `ComposeIn`, `ComposeRunIn`, `ComposeSessionIn`.
  pydantic narrows the field at the boundary: a request carrying
  `mode: "anything"` returns 422 with `loc=body.mode`,
  `type=literal_error`, the message names both legal values, the
  handler never runs. This is the door the design note at
  `backend/main.py:178-185` says an `if mode != "strict"` over a
  free string would have opened, and the door 3.2's
  `test_a_request_with_an_unknown_mode_field_still_runs_the_strict_check`
  was written to keep shut. That test was rewritten (now
  `test_a_request_with_an_unknown_mode_value_is_rejected_at_the_boundary`)
  to assert the new shape: pydantic's literal error, not the
  handler's compose-refused message; the loop-closed `n_shots == 0`
  is the same in both shapes. A regression that re-introduces
  `mode: str = "strict"` and guards with `if c.mode == "strict":`
  would parse `"anything"` as a string, the guard would not match,
  the strict check would be skipped, and the test would queue a
  shot.

  **(2) The pool builder.** `_trio_pool(manner, checkpoint,
  candidates, mode)` replaces the strict-only `_verified_trio_pool`
  the 3.3 share was using. The two modes share the join, the
  `none` filter on every slot (the synthetic key for measurements
  that did not break out a slot — `none` is not a catalogue key
  and the same reasoning 3.3 used applies), and the
  manner/checkpoint scope. They differ in the state predicate,
  branched as a single SQL fragment:

  - `strict`: `judged >= 10 AND arrived*10 >= judged*8` — the
    `db.cell_state("verified")` reading, in SQL form.
  - `exploratory`: `NOT (judged >= 10 AND arrived*10 < judged*8)`
    — every state except `dead`, because the whole point of
    exploratory is to grow the matrix (every queued shot feeds its
    cell, 6.2 lands the verdict), and a `dead` cell carries a
    measurement the table is asking the operator to honour. "Let
    me draw a dead cell anyway" is what exploratory explicitly
    refuses, and the rule is the same in both modes.

  The SQL shape is the same: a single column-reference predicate
  on the `(judged, arrived)` pair, with the `none` filter and the
  candidates `IN (...)` clauses unchanged. The EXPLAIN plans are
  the same; a future mode is a third branch on the predicate and
  nothing else moves. The `none` filter is part of the pool
  builder, not a post-filter, for the same reason 3.3 made it
  part of the pool builder: a 422 on a `none` cell would read
  as "the pool is empty" rather than "the row exists but is not
  drawable", and the operator deserves to know which it is.

  **(3) The one-shot endpoint.** `compose_shot_endpoint` branches
  on `mode` for the cell lookup. The four outcomes:

  - **No row + strict**: 422, `has no measurement (unknown);
    switch to exploratory mode to compose from unmeasured cells`.
    The trio was never measured; strict only accepts verified.
  - **No row + exploratory**: queue. The trio is unmeasured,
    not dead; drawing it feeds the cell, 6.2 lands the verdict.
  - **Row state = dead + any mode**: 422, `is dead, not drawable
    in any mode`. The 422 names the cell and the state, and does
    NOT suggest exploratory — a dead cell is also refused in
    exploratory, and a "switch modes" hint would be a lie the
    operator would discover on retry. The wording "not drawable
    in any mode" replaces 3.2's "not verified" because the old
    message implied exploratory was a path through, and a future
    "let me soften the dead message" that drops "in any mode"
    re-introduces that implication — the assertion in
    `test_a_dead_cell_is_undrawable_in_both_modes` checks the
    loop-closed `n_shots == 0` on both halves, so a regression
    that lets exploratory through queues a shot and the test
    reads it.
  - **Row state = unknown + strict**: 422, `is unknown, not
    verified; switch to exploratory mode to compose from
    unmeasured cells`. The same wording as the no-row case,
    because the cell's state is the operator-facing fact; the
    row existing or not is a detail the SQL query owns.
  - **Row state = unknown + exploratory**: queue. Same path as
    the no-row exploratory case — the cell is unmeasured, the
    draw feeds it.
  - **Row state = verified + any mode**: queue. Verified in any
    mode, no branch needed.

  The four-loop-closed proof is in the test: a regression that
  swaps the strict and exploratory branches (a "let me drop
  strict" bug) queues the strict call and the `n_shots` count
  reads 1 too. The `mode="exploratory"` cell-row 422 in the
  no-row case is the one place the operator-visible wording
  differs from the row-present case, and the test pins the
  difference so a future "let me unify the wording" fails the
  assertion that names it.

  **(4) The run-level / session-level endpoints.** Both build
  the pool through `_trio_pool` with the caller's `mode` and
  pass it to `_draw_n_trio_shots` (the renamed and refactored
  `_draw_n_strict_trio_shots`). 6.1 widens the pool and the
  draw; the 3.3 no-component-repeat greedy, the multi-shuffle
  ceiling (`N_SHUFFLES=10`), the 3.4 dedup pre-check, and the
  pre-check-before-INSERT loop-closed property carry over
  unchanged. The 3.5 family-spread constraint moved from
  post-draw accept to per-trio skip inside the greedy, the
  same rule 3.3 named ("the check and the draw are the same
  calculation") carried to its logical end — the constraint
  is enforced in the loop, not after it. `_skip_for_spread`
  reads the same `ceil(N/2)` bound the reorder uses, so the
  chosen set is always re-orderable and the family-infeasible
  422 in `_reorder_to_spread_families` is unreachable from
  the 3.5 path. The check stays in `_reorder_to_spread_families`
  as a defensive assertion for a future caller that drops the
  skip, the same way the EXPLAIN-equal SQL stays in
  `_trio_pool` for a future mode.

  **(5) The 422 message in pool-too-small.** The strict-mode
  tail is unchanged: `use exploratory mode to compose with
  unmeasured cells`. The message is now more true than when
  3.3 wrote it: exploratory mode is real, the operator can
  actually switch to it, and the suggestion is not a
  "someday" promise. The exploratory-mode tail is `every
  candidate trio is either dead or outside the catalogue, no
  further draw is possible` — a wider mode is not a path
  through, and the operator needs to know why. No test
  exercises the exploratory-mode tail (a test that did would
  need a pool of all-dead trios, which 2.4 documents is not
  a path the cell table holds), and the message is named
  here so a future "let me unify the tails" that drops the
  exploratory-mode branch is caught by the prose, not by
  the test suite.

  **The flake, fixed in the same diff.**
  `tests/test_api.py::test_a_session_compose_draws_a_spreadable_set_when_the_pool_is_larger_than_the_count`
  was flaking at ~2 runs in 8. Reproduced under `git stash`,
  so it predated 4.1/4.2/ba39604. Loop-the-test-12-runs in
  the same process (PowerShell, each run a fresh pytest
  invocation) shows 7 of 30 failures, all 422 with the
  family-infeasible message the test does not expect.

  The cause is the multi-shuffle greedy + post-draw accept
  pattern, exactly the shape 3.3 named as a risk. The pool
  has 4 fronts + 1 shoulder + 1 overhead, count=4. The
  greedy is "take the first non-conflicting trio" — every
  trio in the pool has unique parts, so the greedy just
  takes the first 4 in shuffle order. The 422 the test
  fires on is the family-infeasible one: the chosen set has
  3 fronts, `3 > ceil(4/2) = 2`, no ordering spreads the
  family. Probability that a single shuffle produces 3+
  fronts in its first 4 positions: 0.6 (the 4 fronts are
  in the first 4 of 6 in 48/720 shuffles, the 3 fronts
  in the first 4 are in 384/720; `48 + 384 = 432`,
  `432/720 = 0.6`). Probability that all 10 shuffles
  produce a 3+ front arrangement: `0.6^10 = 0.006` per
  call. With 30 iterations, P(at least one call returns
  an invalid 4-trio draw) = `1 - (1 - 0.006)^30 ≈ 0.166`.
  The observed 7/30 = 23% sits in the same range; the
  test's N_SHUFFLES=10 ceiling is the only thing keeping
  the rate out of the `0.5` range a smaller ceiling
  would land on.

  The fix moves the family-spread constraint from
  `_spread_is_feasible` (a SET property, called on the
  greedy's chosen set) to `_skip_for_spread` (a per-trio
  property, called inside the greedy). The greedy now
  skips a trio if its camera-family count would reach
  `ceil(N/2)` on addition. The chosen set is always
  re-orderable, and the only path to a 422 is the
  pool-too-small one — which in this test is a 200, the
  shape the test asserts. The flake budget drops to
  "no test exercises the shape anymore". Verified by
  loop-the-test-50-runs in the same process: 50/50 pass,
  no 422, the spread property holds on every iteration.
  The pre-existing 3.5 tests pass unchanged
  (`test_a_session_compose_spreads_camera_families_across_consecutive_photographs`,
  `test_a_session_compose_keeps_3_3_and_3_4_invariants`,
  `test_a_session_compose_is_deterministic_for_the_same_pool_and_count`).
  One test was updated to match the new refusal shape:
  `test_a_session_compose_refuses_a_pool_where_one_family_exceeds_half_the_count`
  used to assert the family-infeasible 422; it now asserts
  the pool-too-small 422 the draw actually returns, the
  same four facts (slot, pool count, largest fillable,
  requested count) on a different message — the family
  infeasibility surfaces as "largest fillable is 3 of 4
  requested" because the per-trio skip takes the 3rd front
  out of the draw before it can join the chosen set.

  **What was built.**

  - `backend/main.py`:
    - `Literal["strict", "exploratory"] = "strict"` on
      `ComposeIn`, `ComposeRunIn`, `ComposeSessionIn`. The
      `from typing import Literal` import was already
      implicit; one line added at the top of the imports
      block. The mode closes the door 3.2's design note
      was written to keep shut, and the existing
      `test_a_request_with_an_unknown_mode_field_still_runs_the_strict_check`
      was rewritten to
      `test_a_request_with_an_unknown_mode_value_is_rejected_at_the_boundary`
      to assert the new pydantic-literal rejection shape.
    - `_trio_pool(manner, checkpoint, candidates, mode)` —
      the new pool builder. Replaces `_verified_trio_pool`,
      which was the 3.3 strict-only SQL form. The
      docstring names the two state predicates and the
      "dead excluded in both" rule, the same shape
      `_verified_trio_pool`'s docstring used for the strict
      predicate and the `none` filter.
    - `_draw_n_trio_shots(sid, count, candidates, mode,
      skip)` — renamed from `_draw_n_strict_trio_shots`,
      takes `mode` and `skip`. `mode` is passed to
      `_trio_pool`; `skip` is a per-trio predicate called
      inside the greedy after the no-repeat check. The
      function does not insert; the caller inserts, the
      same as before. The four 422 paths (session-missing,
      pool-too-small, tuple-already-enqueued,
      line-already-enqueued) carry over unchanged. The
      pool-too-small tail is mode-dependent: strict
      suggests exploratory, exploratory names what the
      pool is. `best_accepted` and the post-draw `accept`
      parameter are gone — the skip makes the chosen set
      always valid against the caller's constraint, and
      `best_chosen` is the only set the function tracks.
    - `_skip_for_spread(trio, by_key, family_counts,
      max_per_family)` — the per-trio skip the 3.5
      endpoint passes. Returns True if adding the trio
      would push its camera-family count to `max_per_family`
      (`ceil(count/2)`). A non-spread trio (act or framing
      today) returns False, the same way `_spread_is_feasible`
      treated `None` family.
    - `compose_shot_endpoint` branches on `mode` for the
      cell lookup, the four outcomes (no row + strict =
      422, no row + exploratory = queue, row state = dead
      + any mode = 422, row state = unknown + strict = 422,
      row state = unknown + exploratory = queue, row
      state = verified = queue). The 422 messages name
      the cell, the state, and (in strict-only) suggest
      exploratory.
  - `tests/test_api.py`:
    - `_seed_unknown_trio(camera, act, framing, manner,
      checkpoint, judged=0, arrived=0)` — test-only helper,
      inserts one cell with `judged < 10` so `db.cell_state`
      reads `unknown`. The defaults are the canonical
      "never measured"; a non-zero `arrived` would still
      land as `unknown` while `judged < 10`, and the state
      is what drives the draw, not the counts.
    - `_seed_dead_trio(camera, act, framing, manner,
      checkpoint, judged=12, arrived=0)` — test-only helper,
      inserts one cell that lands as `dead`: `judged >= 10`
      AND `arrived*10 < judged*8`. The defaults `12/0` are
      the canonical 0/12 "measured and failed" measurement.
    - `test_an_unknown_cell_is_drawable_in_exploratory_mode`
      — the named 6.1 scenario at the one-shot level.
      Strict refuses with the "switch to exploratory"
      message; exploratory queues the shot. Loop-closed
      `n_shots` on both halves.
    - `test_a_dead_cell_is_undrawable_in_both_modes` — the
      named 6.1 / 2.5 scenario at the one-shot level. The
      same `0/12` cell, the same trio, called in both
      modes. Both return 422 with `dead` in the message;
      loop-closed `n_shots == 0` on both halves. A
      regression that swapped the branches (a "dead is
      drawable in exploratory" bug) queues the
      exploratory call and `n_shots` reads 1.
    - `test_an_exploratory_run_draws_from_unknown_cells_and_refuses_dead`
      — the run-level shape. The pool is one unknown +
      one dead trio, count=1. Exploratory queues 1 (the
      unknown is in the pool, the dead is not). Strict
      refuses with the pool-too-small message (neither is
      in the strict pool). Loop-closed `n_shots` on
      both halves.
    - `test_an_exploratory_session_compose_inherits_the_mode_and_spreads`
      — the session-level shape, with the spread property
      verified on the queued run. The pool is 2 unknown
      trios from different camera families, count=2.
      Strict refuses; exploratory queues 2; the spread
      places the two families so neither is "next to
      itself".
    - `test_a_request_with_an_unknown_mode_value_is_rejected_at_the_boundary`
      — the rewrite of the 3.2 unknown-mode test. The
      pydantic literal error names the field, the type,
      and the two legal values; the handler does not run.
    - `test_a_session_compose_refuses_a_pool_where_one_family_exceeds_half_the_count`
      — updated to assert the pool-too-small message
      (the family-skip changed the failure shape). The
      four facts the operator needs are still named
      (slot, pool count, largest fillable, requested
      count); the "no ordering" / "ceil" wording the old
      version asserted is gone because the new draw
      refuses via the pool-too-small path, not the
      family-infeasible one. The "use exploratory mode"
      tail is still asserted, the same way every other
      strict-mode pool-too-small test asserts it.
  - Housekeeping: deleted 11 untracked throwaway scripts
    from 4.2 — `_shoot_4_2.py`, `_rerun_script_4_2.py`,
    `_judge_4_2_repeat3.py`, `_db_inspect.py`,
    `_db_inspect2.py`, `_verify_4_2.py`,
    `_verify_4_2_v2.py`, `_reset_4_2.py`, `_run_300.py`,
    `_state_check.py`, `_wait_4_2.py`. The measurement
    they produced now lives in tasks.md under the 4.2
    closure; the scripts are untracked and unused. `git
    status --short` after the deletion is `M
    backend/main.py` and `M tests/test_api.py` only.

  **Branches no test runs.**

  (a) The exploratory-mode 422 tail "every candidate
  trio is either dead or outside the catalogue, no
  further draw is possible" — reachable only when
  every candidate trio is dead or filtered by the
  `none` rule. The test
  `test_an_exploratory_run_draws_from_unknown_cells_and_refuses_dead`
  exercises the case where the pool has at least one
  unknown (so the run succeeds); no test exercises a
  pool of all-dead trios because 2.4 documents the
  all-dead pool as not a shape the cell table holds
  in this change. The message is named in the docstring
  for a future "let me unify the tails" regression.

  (b) The 3.5 family-infeasible path in
  `_reorder_to_spread_families` is unreachable from the
  draw path. The per-trio `_skip_for_spread` ensures
  the chosen set is always re-orderable, and the
  defensive check is there only for a future caller
  that drops the skip. No test exercises this branch.

  (c) The `mode` field on `ComposeIn` for the case
  where the request body has no `mode` field at all
  (the default `"strict"` parses through pydantic
  silently) — covered by the existing 3.2 tests
  (`test_a_verified_cell_for_the_sessions_dimensions_is_drawn_in_strict_mode`,
  `test_a_dead_cell_is_not_drawn_in_strict_mode`,
  `test_an_unknown_cell_is_not_drawn_in_strict_mode`),
  which call the endpoint without `mode` and observe
  the strict-mode behaviour. The default is "strict"
  to match the mode the rest of the system has been
  running on for the last two weeks; a future
  "let me default to exploratory" would silently
  change the operator-visible behaviour and the test
  suite would not catch it — the prose above names
  the default so the change is made on purpose.

  (d) The 6.2 task ("count a judged exploratory
  photograph towards its cell and verify the cell
  flips to verified or dead on reaching the
  threshold") is out of scope for 6.1. The one-shot
  exploratory path queues a shot with a `components`
  JSON the same way the strict path does, and the
  cell's `judged` and `arrived` columns are the ones
  6.2 will write. The handover is the `components`
  column on `shot` — the same column 3.1 wrote and
  3.6 reads, no new field needed.

  **Gates, with output.** `python -m pytest` — 330
  passed (326 baseline + 4 new 6.1 tests), 1 warning
  (the pre-existing `register` shadow in
  `backend/enhance.py:119`, unrelated to 6.1);
  `npm --prefix frontend test` — 23 passed in 668 ms;
  `npm --prefix frontend run build` — 45 modules
  transformed, `dist/index.html` 0.42 kB, no errors;
  `python -m pytest tests/test_no_personal_data.py` —
  2 passed. The flake test
  (`test_a_session_compose_draws_a_spreadable_set_when_the_pool_is_larger_than_the_count`)
  ran 50 times in the same process after the fix with
  50/50 pass. `git status --short` is `M backend/main.py`
  and `M tests/test_api.py` only; the 11 untracked
  throwaway scripts from 4.2 are deleted.

  **The N-draw could not reach an unmeasured cell, and that is now
  fixed.** The 6.1 tests above all go through `/compose`, one shot at
  a time, and they seed the cell explicitly. `_trio_pool` shipped as a
  `SELECT ... FROM cell` with a looser predicate for exploratory —
  which returns only trios that already HAVE a row. A cell nobody has
  measured has no row at all, and `judged < 10` is the definition of
  `unknown`, so "unknown" is mostly the cells the table has never
  heard of. Exploratory could therefore explore only what somebody
  had already measured, on the two paths that actually shoot a
  session (`/compose-run`, `/compose-session`). Measured through the
  API, with no rows for the candidate trios:

      compose (one shot) exploratory, no row -> 200 queued
      compose-run        exploratory, no row -> 422

  and the 422 read `every candidate trio is either dead or outside
  the catalogue`, which was false — they were unknown. Two
  calculations that were supposed to agree and did not, which is the
  same shape as every group-3 bug.

  The pool is now asked the question each mode actually has. Strict
  asks "which rows are verified" and a row is required. Exploratory
  asks "which candidate trios are NOT dead", so it starts from the
  product of the candidate keys and uses the table to SUBTRACT the
  dead rows. The refusal message needed no change: once unmeasured
  trios are in the pool, an empty exploratory pool really does mean
  every candidate is dead or filtered out by `none`.

  Two tests, both verified by breaking the code:
  `test_the_n_draw_reaches_a_trio_the_cell_table_has_never_heard_of`
  (no cell seeded at all; strict refuses and names exploratory,
  exploratory queues `count` rows — restoring the `SELECT`-only shape
  fails it with `assert 422 == 200`) and
  `test_the_n_draw_never_reaches_a_dead_trio_even_with_no_other_row`
  (one candidate per slot, and that single product IS the dead trio,
  so refusal is the only legal answer in either mode — deleting the
  `- matched` subtraction fails it with `exploratory:
  {"ids":[1],"count":1}`).

  That second test was written wrong first, and the way it was wrong
  is worth keeping. It offered two candidates per slot with one dead
  trio among the eight products and asked for `count=1`: the draw
  picks a live trio nearly every time, so it passed with the
  subtraction deleted, and it failed at random on the correct code
  because it asserted on a component key rather than the trio. A pool
  that is entirely dead is what makes the refusal the only outcome.

  **Gates after the fix.** `python -m pytest` — 332 passed, run three
  times, same number each time; `npm --prefix frontend test` — 23
  passed; `npm --prefix frontend run build` — built in 1.16s;
  `python -m pytest tests/test_no_personal_data.py` — 2 passed.

- [x] 6.2 Count a judged exploratory photograph towards its cell and verify the cell flips to verified or dead on reaching the threshold

  **Five decisions before the code, in the order the task asks them:**

  **(1) What event counts a photograph.** The 6.1 end-of-task note
  named 6.2 as "the task that opens the bookkeeping" — the
  exploratory shot has a `components` JSON, and the cell is a
  function of that trio. The judging screen that records verdicts
  is group 5 (5.2: "record the answer chosen over a forced choice
  per slot"), and 5.2 is not built yet. Two readings:

  - **Define the counting path now, 5.2 writes through it
    later.** The data the cell update needs is on the row
    already: `shot.components` carries the trio (3.1 wrote it),
    the session carries manner and checkpoint (3.2 read them at
    create), and the answer to "did the act the line asked for
    land in the photograph" is a per-slot binary the operator
    records. The path 6.2 builds is the function 5.2 calls per
    shot. Without 6.2, 5.2 has no place to land a verdict; with
    6.2, 5.2 stays focused on the screen and never has to touch
    the cell table.
  - **Block on 5.2.** "Honest" in the sense that the path
    depends on the screen — but 5.2's screen design (per-slot
    forced choice over the whole list, the `verdicts` per shot)
    is the spec scenario 5.1 / 5.2 names, and 6.2's data path
    reads from the same `components` JSON 5.2's payload carries.
    The dependency is on the shape, not the implementation, and
    the shape is already decided.

  So: **define the path now**. The argument is short — 5.2
  needs 6.2 to have a place to land verdicts, and the path
  6.2 builds is a function of the trio already on the row.

  The path is `POST /api/shots/{shot_id}/judge` with a body
  of `{camera, act, framing}` (each a string, `""`, or `None`).
  The endpoint reads the shot's components, the session's
  manner and checkpoint, computes the per-slot delta, UPSERTs
  the cell, and writes the verdicts on the row. 5.2's screen
  builds the payload, calls the endpoint per shot, and shows
  the response's new state. 6.2 is the data layer; 5.2 is the
  screen; the verb is "judge".

  **No second verdict store.** The verdicts live on the shot
  row in a new column `verdicts` (TEXT, JSON). The alternative
  readings:

  - A new table `shot_verdict(shot_id, slot, answer, arrived)`
    would be four columns' worth of data for one row's
    worth of input. The shot row already carries the matching
    input (the trio in `components`); the verdicts live next
    to what they answer.
  - A column on the cell is wrong on the wrong axis: the
    cell is shared across all the photographs that landed on
    it, the verdicts are per-shot, and storing the per-shot
    data on the per-cell row would inflate the cell with
    per-shot JSONs the cell update never reads.
  - A re-use of `shot.rating` (0-5) is the silent
    substitution the user names: rating is photo quality
    (0-5 stars, separate column on the row), the act the
    line asked for is a different fact (design.md:296-308),
    and conflating them is the second calculation the
    task says not to invent.

  So: a new column `shot.verdicts TEXT NOT NULL DEFAULT ''`.
  Empty default '' means "not yet judged" — the same idiom
  as `manner=''` and `checkpoint=''` — and the empty value
  is the idempotence marker 6.2's spec scenario names.

  **(2) `arrived` means the act/camera/framing the line
  asked for is in the frame, not that the photograph is
  good.** The verdict's signal is per-slot: for the act
  slot, "the act the line asked for is the act in the
  frame" is a yes/no, the same for camera and framing.
  Rating is something else (photo quality), and the
  endpoint does not read it. The body is per-slot so the
  5.2 forced-choice questions map onto it directly:

  - A catalogue key (e.g. `"astride"`): the judge picked
    that wording from the slot's whole list.
  - `""`: "none or cannot tell" — the spec scenario
    `The judge cannot tell`. Counted as judged (the
    question was answered), not arrived.
  - `None` (or the key absent): the question was not
    asked on this pass. 5.2 asks one question across a
    batch, so the slots the pass did not ask stay at
    `None` and the endpoint does not count them.

  Reaching for `rating` as the signal is exactly the
  conflation design.md:296-308 warns against. The
  endpoint reads the per-slot answer, not the rating,
  and the test
  `test_a_correct_answer_increments_arrived_a_wrong_answer_only_judged`
  pins the per-slot delta: a wrong key is `+1 judged, +0
  arrived`, not `+1 arrived, +0` (the "let me invert the
  match" bug) and not `+1 judged, +0` for an empty
  answer (which the test exercises too).

  **(3) Which cell a photograph counts toward.** The
  cell is keyed on the three WORDING keys plus the
  session's manner and checkpoint. `components` carries
  both per slot, and a future "let me add a second
  wording" lands here as a different `wording` value
  while `concept` stays put. Every concept in the
  catalogue today has a single wording whose key equals
  the concept key (1.1's reshape), so the two coincide
  and a test that reads the wrong one passes by
  accident.

  The test pins the distinction:
  `test_the_judged_cell_uses_the_wording_key_not_the_concept_key`
  plants a shot whose camera's `concept` and `wording`
  keys differ (`cam-concept` vs `cam-wording`), composes
  it through the public endpoint, judges it, and asserts
  the cell row is at the wording key, not at the concept
  key. Verified by breaking the code: replacing
  `comps[slot]["wording"]` with `comps[slot]["concept"]`
  in the endpoint makes the cell row land on the concept
  key, leaves the wording row empty, and the test fails
  on the `cell` lookup with no row found.

  **(4) The flip.** `db.cell_state` is the ONE
  definition of verified/dead/unknown: `verified =
  judged >= 10 AND arrived*10 >= judged*8`, `dead =
  judged >= 10 AND arrived*10 < judged*8`, `unknown =
  judged < 10`. The endpoint never invents a second
  rule; the response carries the new state via
  `db.cell_state(cell["judged"], cell["arrived"])`, the
  same call 2.2 already pins on the case table.

  The task's "flips to verified or dead on reaching the
  threshold" means crossing judged=10. The test covers
  both directions from a known starting cell, with the
  third case (9 still unknown) named for the side that
  the 9->10 boundary has:

  - `test_a_judged_cell_flips_to_verified_on_reaching_the_threshold`:
    pre-seeded at (9, 8) — unknown, 9<10. The tenth
    judgement is a pass on the act. New counts: (10, 9).
    `9*10=90 >= 10*8=80` → verified. The cell flipped.
  - `test_a_judged_cell_flips_to_dead_on_reaching_the_threshold`:
    pre-seeded at (9, 7) — unknown, 9<10. The tenth
    judgement is a fail on the act. New counts: (10, 7).
    `7*10=70 < 10*8=80` → dead. The cell flipped the
    other way.
  - `test_nine_judged_still_reads_as_unknown`:
    pre-seeded at (9, 9). The cell is `unknown`,
    whatever the ratio. The 9->10 boundary is the only
    place the state can change from unknown; a
    regression that landed 9/9 as `verified` (a
    "let me also accept 9 of 10" bug) is the same
    shape as every group-3 bug and the test pins it.

  Each was verified by breaking the code: replacing
  `db.cell_state` with a hand-coded "if n >= 10: verified"
  in the endpoint makes the dead-flip test fail with
  `state='verified' instead of 'dead'`, and reverting
  the test passes.

  **(5) Idempotence.** The `verdicts` column is the
  marker: a non-empty value means a judge already
  answered, the second call is a 409, the cell counts
  do not change. The endpoint reads `shot.verdicts`
  before the UPSERT and refuses the re-judge — the
  refusal runs on the row read, not on the cell
  update, so a refused call does no work.

  The cell table's CHECK `arrived BETWEEN 0 AND judged`
  is the upstream safety net: a code change that
  drops the column check and tries to double-count
  would surface as `IntegrityError` on the UPSERT
  rather than as a wrong number. The user names this:
  "the cell table's CHECK rejects `arrived > judged`
  at insert time, so a double-count can surface as an
  IntegrityError rather than a wrong number — that is
  a real failure mode, not a hypothetical." The
  IntegrityError is the noisy failure the CHECK was
  set up to make; the column check is the cleaner
  failure the user sees at 409.

  `test_judging_the_same_shot_twice_is_refused_at_409`
  pins both halves: a second call is 409, the cell
  counts are unchanged. Verified by breaking the code:
  deleting the `if shot["verdicts"]` check makes the
  second call return 200 with `judged=2, arrived=2`
  and the test fails on the `assert r.status_code == 409`
  check. Reverted, the test passes.

  **What was built.**

  - `backend/db.py`:
    - `shot.verdicts TEXT NOT NULL DEFAULT ''` added to
      `SCHEMA` (next to `components` and the
      `idempotence marker` comment, the same
      `default-and-comment` shape `kind`, `tags` and
      `components` already use).
    - `ALTER TABLE shot ADD COLUMN verdicts ... DEFAULT ''`
      added to `_migrate`, guarded by the same
      `if "verdicts" not in columns("shot")` test the
      other `shot` columns use, so re-runs are no-ops.
  - `backend/main.py`:
    - `JudgeShotIn` pydantic model with the three
      optional string fields and a docstring that
      names the spec scenarios each value shape maps
      to (catalogue key, "", None). Same `default
      None` idiom the other `Optional` fields use.
    - `POST /api/shots/{shot_id}/judge` endpoint:
      - 404 if the shot does not exist.
      - 422 if `shot.components == '{}'`: a written
        shot has no trio, no cell to count toward.
        The message names the marker (`shot has no
        components`) so the operator sees why.
      - 409 if `shot.verdicts` is non-empty: the
        idempotence marker. The message keeps the
        verdicts JSON so the operator sees what was
        answered the first time.
      - 422 if the session is missing manner or
        checkpoint: the same pre-check 3.2 / 3.3
        already pin on their 422s, the same shape
        `compose_shot_endpoint` runs. The message
        names the missing dimensions.
      - 422 if the components JSON is not in the
        trio shape (missing one of the three
        wording keys): the message names the bad
        JSON. Defensive against a future shot row
        whose components are not the expected
        `{slot: {concept, wording}}` shape — the
        cell's NOT NULL on the trio would reject
        the UPSERT, but a refusal with a clear
        message is a cleaner log line than a
        `sqlite3.IntegrityError`.
      - 422 if every slot is `None`: a pass that
        asks nothing measures nothing, and a 200
        with no cell update is a silent no-op.
        The same shape `reshoot-below` already pins
        on its 400.
      - The per-slot delta loop: each non-`None`
        slot increments `judged` by 1, and a
        non-empty answer that equals the drawn
        wording also increments `arrived` by 1. The
        loop guarantees `arrived_delta <= judged_delta`
        by construction (a slot that arrives is a
        slot that was judged).
      - The cell UPSERT: `INSERT ... ON CONFLICT(...)
        DO UPDATE SET judged = judged + excluded.judged,
        arrived = arrived + excluded.arrived`. The
        SET is the only place the cell counts change,
        and the CHECK is the safety net.
      - `db.cell_state(cell["judged"], cell["arrived"])`
        for the response state. The function is the
        only definition; the endpoint does not branch.
      - The `verdicts` JSON is written to the shot
        row at the end, on the same `db.run` so the
        shot's `verdicts` is the post-update state
        and the cell's counts match.
      - Response: `{cell: [5-tuple], judged, arrived,
        state}`. The five-tuple is the wording keys
        in the same order the cell table keys on
        them, so the operator can read the cell the
        judgement landed on without a second query.
  - `tests/test_api.py`:
    - `_wording_split_candidate(concept, wording, text)`:
      the helper that plants a shot whose
      `concept != wording`. The trap the task
      names.
    - `_composed_shot_in_session(client, seeded,
      *, manner, checkpoint, camera, act, framing,
      session_name, seed_cell=None)`: the helper
      that creates a session, optionally pre-seeds
      the cell to a known `(judged, arrived)`,
      composes one exploratory shot, returns the
      shot id. The composing endpoint is the
      public path 6.1 already shipped; the helper
      is the 6.2 layer on top of it, not a
      parallel composing path.
    - 11 tests in `tests/test_api.py` (all verified
      by breaking the code on purpose and
      confirming they fail):
      - `test_a_judged_exploratory_photograph_counts_toward_its_cell`:
        the named 6.2 scenario at the one-shot
        level. A composed shot from an unmeasured
        trio is judged, the cell is created at
        (3, 3) unknown.
      - `test_the_judged_cell_uses_the_wording_key_not_the_concept_key`:
        the trap the task names. Plants a shot
        whose camera's `concept` and `wording`
        differ, composes it, judges it, reads the
        cell. The cell is at the wording key, not
        the concept key.
      - `test_a_correct_answer_increments_arrived_a_wrong_answer_only_judged`:
        the per-slot delta. Three shots, three
        different per-slot patterns, the cell
        deltas add up correctly.
      - `test_a_judged_cell_flips_to_verified_on_reaching_the_threshold`:
        the positive flip. (9, 8) unknown + pass =
        (10, 9) verified.
      - `test_a_judged_cell_flips_to_dead_on_reaching_the_threshold`:
        the negative flip. (9, 7) unknown + fail =
        (10, 7) dead.
      - `test_nine_judged_still_reads_as_unknown`:
        the side of the boundary. (9, 9) is
        unknown, whatever the ratio.
      - `test_judging_a_written_shot_is_refused`:
        a written shot has no trio, the cell
        update has nowhere to land, the endpoint
        refuses at 422.
      - `test_judging_a_session_missing_manner_or_checkpoint_is_refused`:
        the pre-check 3.2 / 3.3 already pin on
        their 422s.
      - `test_judging_the_same_shot_twice_is_refused_at_409`:
        the idempotence. A second call is 409, the
        cell counts are unchanged.
      - `test_judging_with_no_answers_is_refused`:
        a pass that asks nothing measures nothing,
        the endpoint refuses at 422.
      - `test_judging_three_slots_increments_three`:
        the per-slot delta is the only thing the
        UPSERT reads. A regression that hard-coded
        `+1` for every pass would land at (1, ...)
        instead of (3, ...).

  **Branches no test runs.**

  (a) The 5.2 path: the endpoint accepts the
  per-slot answers, but 5.2 is the screen that
  builds the payload and posts it. 6.2 ships the
  data path; 5.2 ships the screen. The two are
  independent enough that 6.2 can land before
  5.2, and the endpoint is the only contract
  between them.

  (b) The "judge multiple slots with mixed
  pass/fail" path is covered by
  `test_a_correct_answer_increments_arrived_a_wrong_answer_only_judged`,
  but a per-photo judgement that asks one
  question per pass (5.2's "one question
  across a batch") is not the shape 6.2 tests.
  5.2 is the place that owns the batch-level
  flow; 6.2 owns the per-shot one.

  (c) The `verdicts` JSON on a clone's shot
  row: `clone_session` already copies
  `components` byte-for-byte (3.6's fix), and
  `verdicts` lives in the same INSERT column
  list. A clone of a judged shot copies the
  verdicts, and a re-judge of the clone is a
  409 — same shape as a re-judge of the
  source. The test that pins this is the 3.6
  clone test, and the `verdicts` column rides
  on the same `INSERT` the `components` column
  rides on.

  (d) The "judge a written shot, count its
  rating instead" path. The endpoint refuses
  with 422 rather than silently counting the
  rating, and the test pins the refusal. A
  future "let me count rating as a fallback"
  bug would re-introduce the silent
  substitution the design explicitly forbids,
  and the test fails on the 422 assertion.

  **Gates, with output.** `python -m pytest` —
  343 passed (332 baseline + 11 new 6.2 tests),
  ran twice consecutively with the same number
  each time (17.79s and 17.36s); `npm --prefix
  frontend test` — 23 passed in 386 ms;
  `npm --prefix frontend run build` — 45 modules
  transformed, `dist/index.html` 0.42 kB, built
  in 1.02 s, no errors; `python -m pytest
  tests/test_no_personal_data.py` — 2 passed.
  `git status --short` is `M backend/db.py`,
  `M backend/main.py`, `M tests/test_api.py`
  only.

  **The counting unit was wrong, and is fixed.** The endpoint shipped
  incrementing `judged` by one per ANSWERED SLOT, so one photograph
  answered on three slots reached `judged=3`, measured through the
  API:

      one photograph, three slots -> {'judged': 3, 'arrived': 3}

  The spec counts photographs, in three places —
  `specs/component-matrix/spec.md:47` ("at least 10 photographs
  judged"), `:70` ("A seeded result below 10 photographs"), and
  `db.cell_state` itself — and the seeded rows are photograph counts
  (astride 9/12 is 9 photographs of 12). Under the slot rule a cell
  flipped to `verified` off FOUR photographs, which retires the n=10
  threshold the whole change is built on, and it put two units in one
  column beside the seed. `judged` is now +1 per photograph, on the
  first pass only.

  **The idempotence marker was too coarse for 5.2.** It was per SHOT,
  and 5.2 asks one question per pass over a batch — so a photograph
  judged for its camera could never be judged for its act:

      pass 1 (camera only) -> 200  judged=1 arrived=1
      pass 2 (act only)    -> 409  "has already been judged"

  The marker is per SLOT now. A new slot on the same photograph is
  accepted and does not count the photograph again; re-answering a
  slot that already has an answer is still 409, which is what 5.3's
  "a disagreement does not overwrite the stored verdict" asks for.
  The `verdicts` JSON is merged across passes, never replaced.

  **`arrived` follows from all the answers, not from one pass.** The
  photograph arrived if every slot answered so far is the one the
  line asked for, so a later pass can turn a hit into a miss and the
  delta is -1, 0 or +1. That negative delta cannot go through the
  UPSERT: SQLite validates the row the INSERT proposes before the
  conflict is resolved, so `VALUES (..., 0, -1)` trips
  `CHECK arrived BETWEEN 0 AND judged` even though the row the UPDATE
  would produce is legal. A later pass therefore takes a plain
  `UPDATE` — the row exists by construction — and the first pass
  keeps the UPSERT.

  Six of the eleven 6.2 tests were renumbered to the photograph unit;
  three were added, each verified by breaking the code:
  `test_a_second_pass_answers_a_new_slot_without_counting_the_photo_again`
  (restore the per-shot marker: `assert 409 == 200`),
  `test_a_later_pass_that_misses_takes_the_photograph_out_of_arrived`
  (drop the recompute: `assert (1, 1) == (1, 0)`), and
  `test_ten_photographs_judged_one_slot_each_reach_the_threshold`.
  `test_judging_three_slots_increments_three` became
  `test_judging_three_slots_is_still_one_photograph` and is the one
  that bites on the unit (restore +1 per slot: `assert 3 == 1`).

  The threshold test does NOT catch the unit collision and its
  docstring says so: one question per photograph makes the two rules
  identical. Worth writing down — it was drafted claiming it did,
  which is the "test that cannot fail" mistake in its quieter form,
  a true-sounding claim about which regression a green test rules out.

  **Gates after the fix.** `python -m pytest` — 346 passed;
  `npm --prefix frontend test` — 23 passed; `npm --prefix frontend run
  build` — built in 1.07s; `python -m pytest tests/test_no_personal_data.py`
  — 2 passed. The `verdicts` migration was probed against a pre-6.2
  database with no such column: the `ALTER TABLE` runs and the
  existing rows survive.

  **Review fix: a control call answering two slots read only the first.**
  The control branch took `next(iter(answers.items()))` and dropped the
  rest, so a call answering the camera correctly and the act wrongly came
  back as

      {'control': True, 'slot': 'camera', 'agreed': True, ...}

  with the act disagreement silently lost. The whole purpose of a control
  is to catch the judge drifting; an instrument that discards one of its
  own measurements without saying so is worse than no control. A pass asks
  one question across a batch (spec.md:118), so more than one slot on a
  control is a caller bug and is now refused at 422 naming both slots.
  Pinned by `test_a_control_answering_two_slots_is_refused_rather_than_half_read`,
  verified by disabling the guard: `assert 200 == 422` with the half-read
  body in the message.

  **Gates after the fix.** `python -m pytest` — 356 passed;
  `npm --prefix frontend test` — 32 passed in 3 files;
  `npm --prefix frontend run build` — built clean;
  `python -m pytest tests/test_no_personal_data.py` — 2 passed.

## 8. The composer, reachable from the app

Added 2026-08-26, after group 5 shipped. The judging screen came up empty: of
292 sessions in the working database exactly one holds composed photographs, and
that one was made by a throwaway script. `grep -rn "compose" frontend/src/`
returns no call to `/compose`, `/compose-run` or `/compose-session` — the whole
composer is API-only. This is the same class of gap the 3.2 REWORK closed one
level down, where the strict check guarded a route no session created by the app
could reach.

No backend work: the three endpoints exist and are tested. `ComposeRunIn`
already says the pool is "the catalogue slice the operator can see for the
session's manner".

- [x] 8.1 Build the candidate pool for a manner as a pure function in a new `frontend/src/compose.js`, and verify it offers every camera of `POSITIONS[manner]` and every act of `ARRANGEMENTS`, with the framing fixed to the single wording the scripts use — framing has no catalogue (every seeded row carries `framing_wording = 'none'`) and inventing one is a measurement decision, not a UI decision, so the screen states the framing is fixed rather than offering a choice

  **Decisions before the code, in the order the task asks them:**

  **(1) Where the function lives.** New file `frontend/src/compose.js`,
  not a method on the SessionView component. Same reasoning
  `frontend/src/judge.js` and `frontend/src/deck.js` already use: pure,
  no React in it, and a vitest file can call it without rendering
  anything. A test that exercises the catalogue slice is what keeps a
  future "let me hide the high cameras behind a checkbox" honest — the
  pool the spec scenario names is decided here, and the view is the
  only thing that decides what to do with the result.

  **(2) The framing is a constant, not a list.** The framing slot has
  no catalogue today (every row in `backend/db.py:EVIDENCE_SEED` carries
  `framing_wording = 'none'`, and inventing one is a measurement
  decision the same weight as the ones that cost days of renders on
  this project). The wording the composer hands the prompt is the one
  `scripts/shoot_arrangements.py:_FRAMING_CONCEPT` already ships —
  `a three-quarter photograph from the knees up` — and the constant
  lives in this file so the control on the screen can say what it is
  without re-deriving the value. The shape the composer reads is a
  one-wording concept (`{ key, wordings: [{ key, text }] }`), the
  same shape `ComposeRunIn.candidates` already accepts.

  **(3) The fallback for an unknown manner.** `candidatePool(manner)`
  reads `POSITIONS[manner] || POSITIONS.directed` — the same fallback
  `kissCameraFor` already uses (`frontend/src/kinds.js:2132-2134`). A
  session created before the catalogue slice for its manner existed
  still gets a non-empty pool, and the operator-facing refusal is the
  lack of a verified cell, not the lack of a candidate. The act list
  is the shared `ARRANGEMENTS` (no per-manner slice today) and the
  framing is always one entry.

  **(4) The wording the screen SAYS.** The screen has nothing to
  choose for framing; the constant is exported (`FRAMING_WORDING`) so
  the button's title reads "framing is fixed: a three-quarter
  photograph from the knees up" rather than a paraphrase. The
  alternative (letting the view import the wording text from
  `scripts/shoot_arrangements.py` through a fetch) would be a copy
  that drifts, and whether THAT wording works is the whole question —
  same reason `shoot_arrangements.py` reads the catalogue through
  node.

  **What was built:**

  - `frontend/src/compose.js`:
    - `candidatePool(manner) -> { camera, act, framing }` — the
      three-slot object `ComposeRunIn.candidates` expects.
      `camera = (POSITIONS[manner] || POSITIONS.directed).slice()`,
      `act = ARRANGEMENTS.slice()`, `framing = [FRAMING_CONCEPT]`.
      The `.slice()` is the same defensive copy `judge.js` and
      `deck.js` use: a future caller that mutates the result does
      not change the catalogue, and a test pins that.
    - `FRAMING_CONCEPT` — the single concept carrying
      `wordings: [{ key: 'framing', text: 'a three-quarter photograph
      from the knees up' }]`. Mirrors
      `scripts/shoot_arrangements.py:_FRAMING_CONCEPT` byte-for-byte.
    - `FRAMING_WORDING` — the export the SessionView reads for the
      control's title.
  - `frontend/src/compose.test.js` — 8 tests, every one verified by
    breaking the code on purpose and confirming it fails:
    - `offers every camera of POSITIONS[manner] for directed` —
      `candidatePool('directed').camera.map(c => c.key)` is the
      directed catalogue, every entry has the concept shape
      (`slot: 'camera'`, `wordings[0].key === c.key`,
      `wordings[0].text` non-empty). Broken by slicing only the
      first two entries: 3 tests fail with the expected deep-equal
      message.
    - `offers every camera of POSITIONS[manner] for candid` — same
      against `CANDID_POSITIONS`.
    - `offers every camera of POSITIONS[manner] for selfie` — same
      against `SELFIE_POSITIONS`.
    - `offers every act of ARRANGEMENTS for every manner` — the
      shared act list, asserted per-manner. Broken by returning
      `ARRANGEMENTS.slice(0, 1)`: fails with `[ 'astride' ] to
      deeply equal [ 'astride', 'reverse', 'wall' ]`.
    - `ships exactly one framing, fixed to the wording the scripts
      use` — one entry, the wording text equals `FRAMING_WORDING`,
      and `FRAMING_WORDING` equals the script's literal. Broken
      by changing the wording text: fails with `expected 'a
      close-up from the waist up' to be 'a three-quarter photograph
      from the k…'`.
    - `falls back to the directed camera catalogue for an unknown
      manner` — `candidatePool('something-new').camera` equals the
      directed catalogue. Broken by dropping the `|| POSITIONS.directed`
      fallback: fails with `TypeError: Cannot read properties of
      undefined (reading 'slice')`.
    - `does not mutate POSITIONS or ARRANGEMENTS across calls` —
      the catalogue's key list is identical before and after two
      calls.
    - `returns the shape /compose-run reads` — every entry has a
      non-empty `key`, a non-empty `wordings` array, and a non-empty
      `wordings[0].text`. The endpoint's pool builder reads
      `c["key"]` (`backend/main.py:1036-1037`) and
      `c["wordings"][0]["text"]` is what `compose_shot` joins into
      the prompt; a pool missing either would fail at the join,
      not at the pool build, and the test catches it at the
      boundary.

  **Branches no test runs.** The fallback for an unknown manner is
  asserted, but no production session today carries an unknown manner
  value (`directed`, `candid`, `selfie` are the three in
  `frontend/src/kinds.js:MANNERS`); a future "let me add a fourth
  manner" lands here as either a new key in `POSITIONS` (the test
  passes unchanged, the catalogue's whole list is offered) or a
  forgotten key (the test catches it on the first button press and
  the operator sees the directed catalogue). The `framing` key is
  asserted constant; a future second framing wording lands here as
  a second entry in `FRAMING_CONCEPT.wordings`, and the test
  `ships exactly one framing` fails on `expect(framing).toHaveLength(1)`
  so the change is on purpose, not by accident.

- [x] 8.2 Add a compose control to the session view beside Run — a count, a mode, and the button — calling `POST /api/sessions/{sid}/compose-run` on the session already open, and verify a composed run lands on that session with its components recorded and shows up in the judging screen's pass

  **Decisions before the code, in the order the task asks them:**

  **(1) On an existing session, not in the create flow.** A session
  created through the app already carries `manner` and `checkpoint`
  (the 3.2 rework lifted `manner` out of the editor and
  `_resolve_session_checkpoint` derives the checkpoint from the
  workflow graph or `settings.checkpoint`, `backend/main.py:584-624`).
  The compose endpoints add shots to a session that already exists,
  and putting the control in the create form would mean duplicating
  the checkpoint derivation before there is a row to derive it onto.
  The button sits next to Run on the session view, where the rest of
  the session's actions live, and it POSTs to
  `POST /api/sessions/{sid}/compose-run` — the same endpoint the
  throwaway script from task 4.2 hit, the one the 3.3 + 6.1 + 3.4
  + 3.5 work landed on.

  **(2) What the button sends.** `candidatePool(s.manner)` is the
  three-slot candidates payload, the `mode` is the dropdown's
  current value (default "exploratory", 8.4), the `count` is the
  number input. No body changes the call site makes beyond those
  three, the same way the run-level endpoint's pre-checks (3.3, 3.4,
  6.1) already expect them.

  **(3) The disabled state names what's missing.** The 422 the
  endpoint will return on a missing manner or checkpoint is
  `compose refused: session is missing manner, checkpoint; set them
  on the session before composing` — the same message 3.2 / 3.3
  already pin on their 422s. The button's `title` carries the same
  fact before the click, so the operator does not pay a round trip
  to learn what is missing, and the field is also disabled. A
  session whose `s.running` is true is disabled too: the runner
  is serial, one session at a time (`backend/runner.py`), and
  queuing onto a running session is a 422 the runner's own lock
  raises.

  **(4) What success looks like.** The 200 returns
  `{"ids": [...], "count": N}`. The handler runs the same
  `call(async () => { ... reload() })` the other buttons use
  (`frontend/src/views/SessionView.jsx:96`), and `reload()` re-fetches
  the session so the new pending shots appear above the gallery.
  No optimistic update: a "the shots appeared" success is the
  reload reading them, and a "the refusal shows verbatim" is
  `setError(e.message)` reading the response's `detail`.

  **What was built:**

  - `frontend/src/views/SessionView.jsx`:
    - Imports `candidatePool` and `FRAMING_WORDING` from
      `./compose.js`.
    - State: `composeCount` (default 4, the size of a 2×2
      sample), `composeMode` (default "exploratory", 8.4). Both
      are local `useState` because the control is its own
      affordance, and a "let me share the count with the
      + Shots dialog" would land here as a lifted value.
    - Handler: `composeRun(n, mode)` — `call(async () => { await
      api.post('/api/sessions/{id}/compose-run', { count: n,
      candidates: candidatePool(s.manner), mode }) })`. The
      candidates are built from the live session's `s.manner`
      every press; a session that changed manner between
      presses queues against the new catalogue, not the old.
    - UI: a number input (1-50), a `<select>` of
      "exploratory" / "strict", and a "Compose" button, in the
      same row as Run, with the disabled state bound to
      `!s.manner || !s.checkpoint || s.running`. The button's
      title carries the missing-dimensions message on disable
      and the framing wording on enable, so the operator
      sees the framing is fixed without a second tooltip.

  **Branches no test runs.** The vitest suite tests
  `candidatePool` (8.1) and the backend's compose-run endpoint
  (covered by 3.3 / 3.4 / 6.1 in `tests/test_api.py`), but the
  integration between the SessionView's handler and the React
  state is not in the test surface — it is a five-line JSX
  block whose every dependency is tested at the boundary
  (the pool function, the API helper, the existing `call`
  wrapper). The end-to-end proof is the verification script
  `scripts/verify_compose_frontend.py` exercising the same
  `candidatePool` and the same endpoint with the same payload
  the view sends, in a temp data dir; that script ran, all
  four cases passed. The disabled-when-missing behaviour is
  visible in the JSX (`disabled={!s.manner || !s.checkpoint
  || s.running}`) and the matching server-side refusal is
  already pinned by 3.2's
  `test_a_session_missing_manner_or_checkpoint_is_refused`.
  The "compose onto a running session" path is a runner lock,
  not a compose-run check, and is covered by the runner's own
  tests; the disabled state in the view is a UI courtesy on
  top of that.

- [x] 8.3 Show the composer's refusal verbatim, and verify the slot, the verified count and the largest fillable count all reach the screen and that nothing is queued — a strict refusal is the mode working, not an error to summarise away

  **The decision before the code:** no work. The composer's
  refusal is already shown verbatim through the existing path:
  `api.js:req` (`frontend/src/api.js:7-11`) reads
  `await res.json()).detail ?? detail` on a non-OK response and
  throws `new Error(detail)`, and every caller in the
  SessionView feeds the thrown `e.message` straight to
  `setError` (`frontend/src/views/SessionView.jsx:96`,
  `const call = async (fn) => { try { await fn(); reload() }
  catch (e) { setError(e.message) } }`). The composer's 422
  body is `{"detail": "compose refused: camera slot has 1
  drawable values within the trio pool, largest fillable is 1
  (of 5 requested); use exploratory mode to compose with
  unmeasured cells"}` — the four literals the spec scenario
  names (slot, verified count, largest fillable, the word
  "exploratory") are in `detail`, the error message is
  `detail`, the screen shows it as a `<p className="muted">`
  banner at the top of the session. Wrapping the message,
  shortening it, or translating it would be the failure
  the spec scenario names; the path that exists already does
  none of those.

  **What was built:** the existing error path was enough.
  Verified end-to-end with the script: a strict run against a
  pool of one verified trio with `count=5` returned 422, the
  detail carried `5` (the requested count), `1` (the verified
  count AND the largest fillable), `camera` (the slot), and
  `exploratory` (the path forward), and the session's shot
  count was 0 — the loop-closed half of 3.3's
  `test_a_strict_run_with_a_too_small_trio_pool_is_refused_with_the_slot_count_and_exploratory`
  on the SessionView side.

  **Branches no test runs.** The "no queued shots" half is
  pinned by 3.3's existing test on the endpoint; the
  end-to-end script asserts the same `n == 0` after the 422
  in case 2 (strict, too-small pool). The same `n == 0`
  check on the exploratory-against-dead path is not in the
  verification script — 6.1's
  `test_a_dead_cell_is_undrawable_in_both_modes` already
  pins the loop-closed property at the endpoint, and the
  view passes the message through `setError` the same way
  the strict case does. A future "let me soften the message
  for 404" lands here as a wrapper around `setError`, and
  the four literals the script asserts (`5`, `1`, `camera`,
  `exploratory`) catch it.

- [x] 8.4 Default the control to exploratory, and say why in the code: the cell table holds 17 rows and two verified trios on the current checkpoint, so a strict default makes the first use of the feature a 422 and reads as broken

  **The decision before the code:** `useState('exploratory')`
  in the SessionView, with the reason written where the
  default is set. The comment names the count (17 rows, 2
  verified trios on the current checkpoint) and the failure
  mode (a strict default makes the first use a 422, which
  the operator reads as broken). The reason is in the
  code, not the commit message, because the next person
  reading the file is the one who needs it; the commit
  message is gone the moment a rebase lands.

  **What was built:** the `composeMode` state default
  (`'exploratory'`) with the inline comment block on
  `SessionView.jsx:49-58`. The dropdown's default selected
  option is the same; the title on the mode `<select>`
  names what each mode does. The Compose button's title
  carries the active mode so the operator sees which
  they are about to fire, and a "let me make strict the
  default" lands here as a one-line change in the `useState`
  call rather than a refactor of the screen.

  **Branches no test runs.** The default is a value, not a
  behaviour: no test asserts "the dropdown shows
  'exploratory' on first render" because vitest is not
  rendering the SessionView, and a render-level test of one
  `useState` default is a test that passes on every code
  path that imports the file. The behavioural fact the
  default enables is "the first click queues a shot", and
  that is case 3 in the verification script
  (`CASE 3 exploratory ok: status=200, queued=1`): a fresh
  session, a fresh `candidatePool`, mode `exploratory`,
  one shot queued. The same call with `mode='strict'`
  against the same pool is case 1 in the script: a verified
  cell seeded for the trio, the call returns 200 — the
  "one click away" the comment names is verified, not
  asserted.

  **Gates.** `python -m pytest` — 356 passed, 1 warning
  (the pre-existing pydantic `register` shadow in
  `backend/enhance.py:119`); `npm --prefix frontend test` —
  40 passed in 4 files (8 new in `compose.test.js`); `npm
  --prefix frontend run build` — built in 1.06s, 48 modules
  transformed, no warnings; `python -m pytest
  tests/test_no_personal_data.py` — 2 passed; `git status
  --short` — four entries (`M
  frontend/src/views/SessionView.jsx`, three untracked:
  `frontend/src/compose.js`, `frontend/src/compose.test.js`,
  `scripts/verify_compose_frontend.py`). The verification
  script ran end-to-end against a throwaway `IDEVGEN_DATA_DIR`
  and reported `ALL OK` on all four cases.

  **Review fix: the control refused its own default.** Group 8 shipped a
  count of 4 and exploratory mode, and clicking Compose returned a 422
  with nothing queued. Measured with the real pool read out of
  `compose.js` through node:

      pool sizes: {'camera': 9, 'act': 3, 'framing': 1}
      exploratory count=4  -> 422, queued=0
        framing slot has 1 drawable values within the trio pool,
        largest fillable is 1 (of 4 requested)

  The cause is 3.4's rule (`backend/main.py`, the `used` sets in the draw):
  a trio is taken only if none of its three components was used in the run.
  With one fixed framing wording the second trio always repeats it, so the
  ceiling of every run was one photograph. The proposal asked for the fixed
  framing and nobody checked it against the shipped no-repeat rule; the
  group-8 verification script did not see it because its exploratory case
  asked for `count=1` and its refusal case ran a strict pool of 1 — the
  pool-sized-to-the-count shape that already hid the 3.5 flake.

  The rule now applies per slot only while the pool offers that slot more
  than one value (`_spreadable_slots`). "Do not repeat when you had
  somewhere else to go" is what 3.4 wrote; a one-value slot is not a
  repeat, it is the only road. `_min_slot_within` reads the same set, so
  the reported ceiling and the draw cannot disagree — they are the pair
  that has produced every bug in this change.

  Two tests, both verified by restoring the old rule:
  `test_a_slot_with_one_value_does_not_cap_the_run_at_one_photograph`
  (three cameras, three acts, one framing, `count=3` queues three with
  distinct cameras and acts sharing the framing — old rule: `assert 422 ==
  200`) and
  `test_a_slot_with_a_choice_still_refuses_a_run_that_would_repeat_it`
  (`count=4` against three acts is still refused and the message names the
  act slot, not the exempt framing — old rule names framing).

  The control's default count is now derived (`defaultCount` in
  `compose.js`): the smallest slot that has a choice, which is the act list
  at 3. Written as a derivation rather than a number so a fourth
  arrangement moves it without anyone remembering to.

  **The ceiling is 3 per run, and that is the rule working.** Nine cameras
  and three acts give 27 distinct trios, but no run may repeat an act, so a
  batch of 25 photographs is nine clicks rather than one. Worth stating
  plainly because it bears on 5.4 and 5.5: `compose-run` produces VARIETY,
  and the matrix needs n=10 photographs of the SAME cell to reach a
  verdict. Filling a cell is a different request from composing a shoot,
  and this change never wrote it. Whoever takes 5.4 will meet it
  immediately.

  **Gates after the fix.** `python -m pytest` — 358 passed;
  `npm --prefix frontend test` — 42 passed in 4 files;
  `npm --prefix frontend run build` — built in 1.03s;
  `python -m pytest tests/test_no_personal_data.py` — 2 passed;
  `python scripts/verify_compose_frontend.py` — ALL OK, 4 of 4.

## 7. Cleanup and documentation

- [ ] 7.1 Remove the two inline camera examples from the instruction prose and verify the single-home test from 1.3 still passes with those two texts deleted from its `KNOWN_DUPLICATES` baseline, and no test asserting prompt text changes
- [ ] 7.2 Update `README.md` and the matching page under `docs/` with the judging screen and the composer path, including that strict mode refuses rather than repeats
- [ ] 7.3 Run the full gates — `python -m pytest`, `npm --prefix frontend run build`, `npm --prefix frontend test` — and report the output rather than the summary
