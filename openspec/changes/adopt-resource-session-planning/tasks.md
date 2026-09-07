## 1. Source coverage and compatibility baseline

- [x] 1.1 Inventory all operator-selected source libraries and auxiliary maps, recording shapes, field roles and readable consumer evidence without copying source prose into tracked files; verify every discovered file has a coverage-ledger entry and every pending item has a reason.

  > 1.1 status: **complete.** The private nine-file ledger was
  > generated successfully and all verification commands passed.
  >
  > The operator-selected corpus (nine scene-library JSON files in
  > the named operator directory) was inventoried by
  > `inventory_source_dir`. The private ledger was written to
  > `data/resource-ledger.json` (a gitignored, untracked location;
  > it does not enter the repository). Every discovered file
  > produced exactly one entry; every non-OK status carries a
  > non-empty `reason`; the two `not_adopted` files are recorded
  > with their structural schema (not silently reduced), each
  > carrying a structural note that names the pattern. The dynamic
  > identity-like outer keys the operator's data uses are NEVER
  > emitted anywhere in the artifact. The `perspective_scenes`
  > library is recorded as a known shape without any claim of
  > compiled behavior parity. No source corpus JSON was copied
  > into the repository.
  >
  > The walk was extended to recognise three patterns the
  > operator's data uses that the previous walk could not:
  > - `keyed_record_collection`: a dict whose values share an
  >   inner record schema;
  > - `list_keyed_collection`: a dict whose values are uniformly
  >   lists;
  > - `scalar_keyed_collection`: a dict of scalars.
  > In all three, the OUTER keys (the part of the source most
  > likely to carry personal or profile identifiers) are not
  > emitted; the inner schema is what the ledger records.
  >
  > Verification commands and outcomes (all pass):
  > - `python -m pytest tests/test_resource_ledger.py` (all pass)
  > - `python -m pytest tests/test_no_personal_data.py` (all pass)
  > - `python -m pytest` (the full backend suite, all pass)
  >
  > The aggregate pass count is reported by pytest at run time;
  > this note does not record a hard-coded total.
- [x] 1.2 Define the supported field mappings and explicit adaptations for accepted scene, perspective and auxiliary resources; verify each source field is classified and no required preparation field is silently unused. Record compiled behavior as unverified rather than claim parity.

  > 1.2 status: **complete.** The explicit preparation contract was
  > defined in `backend/resource_prompts.py` and proven by focused
  > English-only tests. The contract covers six supported resource
  > kinds: `rooms`, `fused_scenes`,
  > `translation_map`, `cut_map`, `mined_families` and
  > `mined_labels`. Every field name the inventory's
  > `PROVISIONAL_FIELD_ROLES` table records is classified in
  > `PREPARATION_FIELD_MAPPING` under one of the six roles the
  > `resource-prompts` spec calls out by name: `identity`,
  > `selection_metadata`, `descriptive_input`, `writer_guidance`,
  > `auxiliary_data` or `intentionally_unused`. Every
  > `intentionally_unused` entry carries a non-empty `reason`.
  >
  > Required fields are pinned: a `rooms` entry needs `id`, `label`
  > and `scene_theme`; a `fused_scenes` entry needs `id` and
  > `prompt`; auxiliary kinds have no required fields. Missing
  > required fields are reported by `validate_resource_entry` with
  > a field-specific reason.
  >
  > The text `weight` adaptation is declared explicitly in
  > `WEIGHT_TEXT_ADAPTATION`: the numeric `weight` field is a
  > selection parameter, MUST NOT be translated into prompt-weight
  > syntax, and is recorded as provenance on the take. The
  > `fused_scenes` compiled behavior is declared explicitly in
  > `FUSED_SCENES_COMPILED_BEHAVIOR`: the splitting of the
  > `prompt` prose into camera, act and room clauses is NOT
  > verifiable from the readable evidence this project has, the
  > contract does NOT claim parity, and the prose is preserved
  > intact, not auto-split. The module is grepped for positive
  > parity claims (`achieves parity`, `guarantees parity`,
  > `byte-for-byte parity`); none are present.
  >
  > An unknown or unmapped field cannot reach the prompt silently:
  > `extract_prompt_inputs` returns ONLY fields whose role is
  > `descriptive_input` for the given kind; `classify_field`
  > returns an `unmapped` sentinel with a non-empty reason for any
  > field the contract does not name; the writer-guidance name
  > rules (`*_anchor` suffix and `mood_*` prefix) are the only
  > way an unmapped name avoids the `unmapped` sentinel, and the
  > writer-guidance role is not a prompt input.
  >
  > Auxiliary schemas are handled safely: the four auxiliary kinds
  > never contribute to a prompt, a `translation_map` or `cut_map`
  > record with an unknown top-level key is reported with a
  > field-specific reason, a scalar auxiliary value must be a
  > non-empty string, and a scene entry that carries a
  > record-shape auxiliary key (`source`, `translation`, `fields`,
  > `camera`, `act`, `room`) is reported as a role mismatch.
  >
  > The new module's source is verified to carry no private
  > corpus markers (operator file stems, structural collection
  > patterns) and no absolute paths, emails, API tokens or CJK
  > glyphs. The repo-wide `tests/test_no_personal_data.py` scan
  > still covers the new module; the focused guards in
  > `test_resource_ledger.py` and `test_no_personal_data.py`
  > pin the new module's behaviour with messages that name the
  > module under test.
  >
  > Verification commands and outcomes (all pass):
  > - `.venv\\Scripts\\python.exe -m pytest tests/test_resource_ledger.py` (63 pass)
  > - `.venv\\Scripts\\python.exe -m pytest tests/test_no_personal_data.py` (10 pass)
  > - `.venv\\Scripts\\python.exe -m pytest` (the full backend suite, 803 pass)
  >
  > The aggregate pass count is reported by pytest at run time;
  > this note does not record a hard-coded total.
- [x] 1.3 Capture invented legacy session fixtures for text-to-image, editing and guided workflows; verify existing prompt composition, wardrobe overrides and evidence remain unchanged before new-mode work.

  > 1.3 status: **complete.** The invented legacy-session fixtures
  > are tracked at `tests/legacy_session_fixtures.json` and the
  > baseline is locked by `tests/test_legacy_session_baseline.py`.
  > The fixture file is hand-written English prose; no source corpus
  > text, no personal data, no machine paths, no real names, no
  > generated images. The fixture carries three session shapes —
  > text-to-image, editing (`kind=edit`) and guided (`kind=guide`) —
  > and the workflow `kind` and `uses_reference_workflow` flags are
  > the data the tests read; the suite refuses to load any future
  > fixture whose `workflow_kind` is not one of the three
  > `workflow_kinds` values it lists.
  >
  > The baseline covers the six legacy invariants the resource-mode
  > work must not break:
  > - text-to-image composition (trigger + base + look + wardrobe +
  >   take, joined with full stops, with an explicit `{trigger}`
  >   placeholder NOT prepended a second time);
  > - the editing branch sends the take's prompt raw (no trigger,
  >   no base, no look, no wardrobe prepended);
  > - the guided branch composes the take's prompt (because the
  >   graph paints from noise, not from a reference image);
  > - the wardrobe override rules: `None` follows the session,
  >   `""` removes the wardrobe clause without a doubled period,
  >   a string wins over the session;
  > - the verbatim branch stores the take's prompt raw, with the
  >   trigger, base, look and wardrobe NEVER prepended;
  > - legacy session creation does not mutate the cell table: a
  >   pre-seeded cell row stays byte-for-byte equal across the
  >   three legacy kinds, and the global totals of judged, arrived
  >   and row count are unchanged. The `/api/sessions/{sid}/shots`
  >   extension is asserted to return `status_code == 200` and
  >   `{"added": 1}` so a 4xx or a silent drop would fail the test.
  >
  > The privacy scan reuses the canonical `PATTERNS` set from
  > `tests/test_no_personal_data.py` (a private copy of the regexes
  > here would be exactly the drift the upstream guard catches);
  > "English only" is asserted by the prose being hand-written
  > invented English, not by ASCII alone.
  >
  > Verification commands and outcomes (all pass):
  > - `.venv\\Scripts\\python.exe -m pytest tests/test_legacy_session_baseline.py tests/test_no_personal_data.py --tb=no` (17 pass)
  > - `.venv\\Scripts\\python.exe -m pytest --tb=no` (810 pass)
  >
  > The aggregate pass count is reported by pytest at run time;
  > this note does not record a hard-coded total.

## 2. Complete local resource storage

- [x] 2.1 Add additive SQLite migrations for resource libraries and immutable revisions with separate translation and coverage data; verify full nested-object round trips, revision uniqueness and repeated migration against isolated databases.

  > 2.1 status: **complete.** Two new tables were added to
  > `backend.db.SCHEMA` additively (no existing table was modified, no
  > legacy row was rewritten, no destructive down-migration runs):
  >
  > - `resource_library(id, library_key UNIQUE, display_name, kind, created_at)` —
  >   a named, safe identity for a source library. The unique key on
  >   `library_key` makes library registration idempotent.
  > - `asset_revision(id, library_id FK CASCADE, source_id, content_digest,
  >   payload, translation DEFAULT '{}', coverage DEFAULT '{}',
  >   created_at, UNIQUE(library_id, source_id, content_digest))` —
  >   the immutable unit of evidence. `payload` stores the complete
  >   original accepted object as JSON (every nested structure and
  >   original string preserved verbatim); `translation` and `coverage`
  >   are stored in their own JSON columns so they can be filled or
  >   updated without rewriting the original payload. The composite
  >   unique key is what makes the spec's "no duplicate revisions for
  >   identical content, new immutable revision for changed content"
  >   rule enforceable at the SQL level.
  >
  > The Python persistence surface is `backend.resource_store`, kept
  > deliberately narrow: `canonical_digest`, `ensure_library`,
  > `record_revision`, `get_revision`, `list_revisions`,
  > `list_libraries`. The digest is canonical (key-sorted) so two
  > payloads that compare equal as JSON values produce the same
  > revision, and whole-number floats are normalised to int so `1.0`
  > and `1` share a digest across platforms.
  >
  > Verified with isolated, invented-data tests in
  > `tests/test_resource_store.py` (46 focused tests):
  > - a full nested payload round-trips byte-for-byte (escapes,
  >   newlines, tabs, nested dicts, nested lists, `None`, empty
  >   containers, whole-number floats);
  > - translation and coverage data remain independent of the original
  >   payload when each is rewritten in isolation;
  > - identical `(library, source_id, content)` does NOT create a
  >   duplicate revision (the unique key refuses a direct second
  >   INSERT at the SQL level, so a future code path cannot quietly
  >   disable the rule);
  > - changed content for the same `(library, source_id)` creates a
  >   new immutable revision alongside the prior one, and the prior
  >   row's stored payload text is byte-for-byte unchanged;
  > - `db.connect(path)` can be called repeatedly on the same
  >   isolated database without raising or losing data, and a
  >   database written before the migration gains the new tables on
  >   reopen while every legacy row survives;
  > - the legacy schema and data required by existing tests remain
  >   byte-for-byte unchanged;
  > - direct SQL UPDATEs of the protected revision identity,
  >   provenance and payload columns are rejected at the schema
  >   level (a `BEFORE UPDATE OF <protected>` trigger raises
  >   `RAISE(ABORT, ...)` and leaves the original row intact),
  >   while `translation` and `coverage` remain independently
  >   writable at the SQL level so a later task can fill or
  >   update them without rewriting the original payload.
  >
  > Verification commands and outcomes (all pass):
  > - `python -m pytest tests/test_resource_store.py` (46 pass)
  > - `python -m pytest tests/test_resource_store.py tests/test_db_migrate.py
  >   tests/test_no_personal_data.py` (60 pass)
  > - `python -m pytest tests/test_shoot_checks.py` (36 pass;
  >   no control characters, no trailing whitespace in the new files)
  > - `git diff --check` is clean (no whitespace errors)
- [ ] 2.2 Implement shared resource parsing before persistence, retaining accepted unfamiliar fields and reporting unsupported shapes; verify invented fixtures cover fused records, auxiliary files and malformed input, and that no source entry is filtered out of the database or the import report by content.
- [ ] 2.3 Implement preview and atomic commit bound to source fingerprints; verify changed-source rejection, duplicate-ID reporting, simulated transaction failure rollback and count reconciliation across all outcomes.
- [ ] 2.4 Implement translation-pending readiness separately from original payload retention; verify accepted untranslated data stays private, English output readiness is enforced and legacy translation-first imports retain their behavior.
- [ ] 2.5 Add resource list/detail and import operations plus a CLI entry using the same service; verify both entry points produce equal revisions and reports and source refresh does not mutate previous snapshots or evidence.

## 3. Session state and persistence

- [ ] 3.1 Add explicit resource composition mode, session-plan persistence and prepared-take snapshots with stable take IDs; verify absent mode keeps legacy behavior and stale draft revisions cannot overwrite newer saves.
- [ ] 3.2 Implement effective wardrobe resolution with this-take and from-here changes; verify constant defaults, a jacket from take seven, isolated overrides, removal of a change and reorder across change boundaries.
- [ ] 3.3 Enforce constant identity/look and explicit preparation invalidation; verify source suggestions cannot overwrite fixed choices and queued/generated snapshots remain intact after draft edits.
- [ ] 3.4 Persist each completed preparation and mark interrupted work resumable; verify reopening after three of twelve takes retains those results and resumes only incomplete work, including visible persistence failures.

## 4. Resource-based prompt preparation

- [ ] 4.1 Implement deterministic preparation from declared resource mappings and effective state, with manual completion available without an assistant; verify unused metadata and source instructions never become accidental prompt clauses or executable instructions.
- [ ] 4.2 Add visible conflict handling and separately stored adaptations for fused descriptions; verify original payload preservation, fixed wardrobe precedence and unresolved-placeholder refusal using invented non-explicit scenes.
- [ ] 4.3 Integrate optional assistant synthesis for unlocked fields and save its inputs/outputs; verify fixed choices survive preparation and repeating a finalized take requires no new writer request.
- [ ] 4.4 Save exact prompts with source and preparation versions; verify source refresh, translation edits and model-writer changes cannot rewrite an already finalized prompt.
- [ ] 4.5 Connect finalized takes to existing shot creation and serial queue with submission uniqueness; verify retries produce one shot, complete prompts are not prefixed twice, and graph-kind reference rules remain unchanged.
- [ ] 4.6 Scope measured-catalogue readiness and uniqueness checks to legacy paths; verify resource drafts work with an empty measured catalogue and twelve intentional same-camera portraits are accepted while legacy checks still fail appropriately.

## 5. Session workflow in the interface

- [ ] 5.1 Add a resource browser and import preview with filters, readiness, field roles and a start-session action; verify every inventory outcome is visible and an accepted mapped resource can start a draft without scripts.
- [ ] 5.2 Reorder new-session preparation around character, scene/constants, takes and review, reusing current profiles and advanced settings; verify the resource path needs no visit to Catalogue or Judge.
- [ ] 5.3 Add wardrobe scope controls and inherited-state display for ordered takes; verify UI changes save the same explicit events the backend resolves and reordering shows required re-preparation.
- [ ] 5.4 Add per-take prompt/provenance review, conflict resolution, resume and selected-take test generation; verify stale review cannot submit and double-click/network retry does not duplicate shots.

## 6. Migration, acceptance and documentation

- [ ] 6.1 Exercise backup, additive upgrade and disable-new-mode rollback on an isolated database; verify legacy sessions, source revisions and finished shots survive and no destructive downgrade runs automatically.
- [ ] 6.2 Demonstrate the twelve-portrait acceptance workflow with invented adult-character resources, fixed clothing, a jacket change, interruption and recovery; verify effective state and saved prompts independently of the implementation, without GPU or network.
- [ ] 6.3 Deliver a private corpus coverage report from the actual operator-selected sources, accounting for every library and field without publishing source prose; verify every accepted supported resource has a declared use and every unsupported item remains explicitly reported. Do not call pending mapping work complete adoption.
- [ ] 6.4 Update README and matching session/import/limitations documentation, including technical restrictions retired, original-language private storage, reference behavior and lack of pixel-level continuity guarantees; verify descriptions against delivered behavior.
- [ ] 6.5 Run the complete backend suite with `python -m pytest`, frontend tests with `npm --prefix frontend test`, and production build with `npm --prefix frontend run build`; verify all pass, data/config/build outputs stay untracked and no new public artifact contains personal data or source corpus text.
- [ ] 6.6 Validate the completed change with OpenSpec and review the acceptance evidence before enabling resource mode by default for new sessions; verify all tasks are supported by actual checks and legacy entry remains available.
