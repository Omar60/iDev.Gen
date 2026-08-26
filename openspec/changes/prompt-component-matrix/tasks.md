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
- [ ] 2.5 Confirm 6.1 is where "a dead wording is never drawn" is proved, and leave `tests/test_arrangements.py` asserting `noneDead` unchanged — the assertion this task originally asked for cannot live in that file. `tests/test_arrangements.py` exercises the FRONTEND shuffler (`arrangementPlan` in `frontend/src/kinds.js`), and no task in this change gives the frontend the cell state: 3.2 restricts the strict COMPOSER's draws, 3.6 requires a written session to behave exactly as before (so the frontend shuffler survives the change intact), and 6.1 already asks for a dead wording to be undrawable in both modes — on the composer. Putting `back` and `side` back into `ARRANGEMENTS` to satisfy the "present in the catalogue" half was tried and reverted: `frontend/src/views/ShotsEditor.jsx:271` renders every `ARRANGEMENTS` entry as an option with no filter anywhere in the plan, so the two keys became selectable in the app and would plant wordings measured 0 of 24. The verdict for those two lives in the cell table, which is the single home this change exists to give it; the frontend catalogue is not a second one. Close this task by checking 6.1's test covers a dead wording in both modes, and delete it if it does.

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

- [ ] 4.1 Point one `scripts/shoot_*.py` at the composer instead of its hand-built line and verify it produces the same line it built by hand
- [ ] 4.2 Shoot one already-measured question through both the script and the composer at n=10 and report the two rates side by side, so the composer is checked against a control before anything is judged through it

## 5. Judging screen

- [ ] 5.1 Present a photograph with no brief, no composed line, no wording and no reference image, and verify none of them reach the client
- [ ] 5.2 Ask one question across a batch as a forced choice over the slot's whole list plus "none or cannot tell", recording the answer chosen and not only whether it matched, and verify a miss stores which component was seen
- [ ] 5.3 Re-present already-judged photographs unmarked during a pass and report the agreement rate at the end, and verify a disagreement does not overwrite the stored verdict
- [ ] 5.4 Judge an already-judged session through the screen against a verdict already known, and report whether the screen reproduces it
- [ ] 5.5 Time one batch of human judging against one batch of vision judging and record the real ratio, replacing the estimate in design.md

## 6. Exploratory mode

- [ ] 6.1 Allow draws from unknown cells marked as exploratory, never from dead wordings, and verify a dead wording is undrawable in both modes
- [ ] 6.2 Count a judged exploratory photograph towards its cell and verify the cell flips to verified or dead on reaching the threshold

## 7. Cleanup and documentation

- [ ] 7.1 Remove the two inline camera examples from the instruction prose and verify the single-home test from 1.3 still passes with those two texts deleted from its `KNOWN_DUPLICATES` baseline, and no test asserting prompt text changes
- [ ] 7.2 Update `README.md` and the matching page under `docs/` with the judging screen and the composer path, including that strict mode refuses rather than repeats
- [ ] 7.3 Run the full gates — `python -m pytest`, `npm --prefix frontend run build`, `npm --prefix frontend test` — and report the output rather than the summary
