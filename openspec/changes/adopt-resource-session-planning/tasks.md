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
- [x] 2.2 Implement shared resource parsing before persistence, retaining accepted unfamiliar fields and reporting unsupported shapes; verify invented fixtures cover fused records, auxiliary files and malformed input, and that no source entry is filtered out of the database or the import report by content.
- [x] 2.3 Implement preview and atomic commit bound to source fingerprints; verify changed-source rejection, duplicate-ID reporting, simulated transaction failure rollback and count reconciliation across all outcomes.
- [x] 2.4 Implement translation-pending readiness separately from original payload retention; verify accepted untranslated data stays private, English output readiness is enforced and legacy translation-first imports retain their behavior.
- [x] 2.5 Add resource list/detail and import operations plus a CLI entry using the same service; verify both entry points produce equal revisions and reports and source refresh does not mutate previous snapshots or evidence.

## 3. Session state and persistence

- [x] 3.1 Add explicit resource composition mode, session-plan persistence and prepared-take snapshots with stable take IDs; verify absent mode keeps legacy behavior and stale draft revisions cannot overwrite newer saves.
- [x] 3.2 Implement effective wardrobe resolution with this-take and from-here changes; verify constant defaults, a jacket from take seven, isolated overrides, removal of a change and reorder across change boundaries.
- [x] 3.3 Enforce constant identity/look and explicit preparation invalidation; verify source suggestions cannot overwrite fixed choices and queued/generated snapshots remain intact after draft edits.
- [x] 3.4 Persist each completed preparation and mark interrupted work resumable; verify reopening after three of twelve takes retains those results and resumes only incomplete work, including visible persistence failures.

## 4. Resource-based prompt preparation

- [x] 4.1 Implement deterministic preparation from declared resource mappings and effective state, with manual completion available without an assistant; verify unused metadata and source instructions never become accidental prompt clauses or executable instructions.
- [x] 4.2 Add visible conflict handling and separately stored adaptations for fused descriptions; verify original payload preservation, fixed wardrobe precedence and unresolved-placeholder refusal using invented non-explicit scenes.
- [x] 4.3 Integrate optional assistant synthesis for unlocked fields and save its inputs/outputs; verify fixed choices survive preparation and repeating a finalized take requires no new writer request.
- [x] 4.4 Save exact prompts with source and preparation versions; verify source refresh, translation edits and model-writer changes cannot rewrite an already finalized prompt.
- [x] 4.5 Connect finalized takes to existing shot creation and serial queue with submission uniqueness; verify retries produce one shot, complete prompts are not prefixed twice, and graph-kind reference rules remain unchanged.
- [x] 4.6 Scope measured-catalogue readiness and uniqueness checks to legacy paths; verify resource drafts work with an empty measured catalogue and twelve intentional same-camera portraits are accepted while legacy checks still fail appropriately.

## 5. Session workflow in the interface

- [x] 5.1 Add a resource browser and import preview with filters, readiness, field roles and a start-session action; verify every inventory outcome is visible and an accepted mapped resource can start a draft without scripts.
- [x] 5.2 Reorder new-session preparation around character, scene/constants, takes and review, reusing current profiles and advanced settings; verify the resource path needs no visit to Catalogue or Judge.
- [x] 5.3 Add wardrobe scope controls and inherited-state display for ordered takes; verify UI changes save the same explicit events the backend resolves and reordering shows required re-preparation.

  > 5.3 status: **complete.** Pure resolution, ordered scope controls, CAS persistence, and re-preparation tracking for wardrobe changes were implemented across the frontend and view layers:
  >
  > - **Pure resolution**: Added `resolveEffectiveWardrobes` and `resolveEffectiveWardrobeDetails` in `frontend/src/sessionPlan.js` matching `backend.session_plan.resolve_effective_wardrobes` exactly. Handles initial wardrobe inheritance, isolated overrides (`this_take`), and persistent transitions (`from_here`) walking ordered takes. Rejects unrecognized scopes with descriptive error.
  > - **Non-coercing normalization**: Updated `normalizePlan` to retain custom/invalid scopes verbatim rather than silently coercing them to `this_take`, ensuring backend CAS validation rejects invalid payloads cleanly.
  > - **Payload contract**: `buildPlanSavePayload` persists only explicit `wardrobe_changes` and never materializes `effective_wardrobe` into persisted takes.
  > - **Pure reordering & editing**: Added `reorderTakes`, `setWardrobeChange`, and `removeWardrobeChange`. Reordering preserves stable `take_id`s, keeps attached events bound to `take_id`, and immediately recalculates effective wardrobe states across `from_here` boundaries.
  > - **Preparation tracking & invalidation**: Controller and view track backend `planPreparation` from `recover_preparation` (`/api/sessions/{sid}/plan`). Edits and take reordering immediately set `planDirty = true` and reset `reviewedRevision = null`. Take cards and review tables surface preparation states (`Ready`, `Requires re-preparation`, `Preparation required`, `Unsaved edits`) and re-preparation alerts.
  > - **Legacy isolation**: Wardrobe scope controls, reorder buttons, and resource preparation steps remain hidden on legacy sessions (`isLegacyControlVisible`).
  >
  > Verification commands and outcomes:
  > - `npm --prefix frontend test` (235 passed across 11 test files, including 31 dedicated Task 5.3 tests)
  > - `npm --prefix frontend run build` (production build clean)
  > - `.venv\Scripts\python.exe -m pytest -q --tb=no` (exit code 0, 100% pass; terminal summary line suppressed by quiet mode, no prior summary figure copied)
  > - `npx --yes @fission-ai/openspec validate adopt-resource-session-planning --strict` (valid)
  > - `python -m pytest tests/test_no_personal_data.py` (10 passed)
  > - `git diff --check` (clean, no whitespace or formatting errors)
- [x] 5.4 Add per-take prompt/provenance review, conflict resolution, resume and selected-take test generation; verify stale review cannot submit and double-click/network retry does not duplicate shots.

  > 5.4 status: **complete.** Per-take prompt and provenance review, conflict adaptation, resume after reload, and selected-take test generation with double-click and stale guards were implemented across backend and frontend:
  >
  > - **Backend review and preparation endpoints (`backend/main.py`)**:
  >   - `GET /api/sessions/{sid}/plan/takes/{take_id}/review`: Returns compiled final prompt, effective state, pinned resources with digests, conflicts, adaptations, compiler/mapping versions, and enforces plan revision validation (HTTP 409 on revision mismatch).
  >   - `GET /api/sessions/{sid}/plan/review`: Plan-wide review endpoint aggregating per-take reviews for a specific revision.
  >   - `POST /api/sessions/{sid}/plan/takes/{take_id}/adaptations`: Persists reviewed adaptations bound to a specific plan revision.
  >   - `POST /api/sessions/{sid}/plan/takes/{take_id}/prepare`: Deterministic take finalization producing ready preparation snapshots.
  >   - `POST /api/sessions/{sid}/plan/preparations/prepare`: Batch finalization of incomplete takes.
  >   - `POST /api/sessions/{sid}/plan/preparations/submit-selected`: Atomically materializes selected takes into pending shots; enforces revision validation (HTTP 409 on stale revision) and guarantees idempotent execution (network retry / duplicate submission returns existing `shot_id` without duplicate shot rows).
  > - **Frontend helpers and controller state machine (`frontend/src/sessionPlan.js`)**:
  >   - Pure and async API helpers: `loadTakeReview`, `loadPlanReviews`, `recordTakeAdaptation`, `prepareTake`, `preparePlanTakes`, `submitPreparedTake`, `submitSelectedTakes`.
  >   - State tracking: `selectedTakeIds` (Set), `submittingTakes` (boolean), `takeReviews` (dictionary), and `preparation` snapshot state.
  >   - Selection management: `toggleTakeSelect`, `selectSingleTake`, `selectAllReadyTakes`, and `clearTakeSelection`.
  >   - Stale review invalidation: Any edit to plan constants, take order, creative choices, or wardrobe changes sets `planDirty = true`, resets `reviewedRevision = null`, and clears `selectedTakeIds`.
  >   - Double-click & in-flight protection: `submitSelectedTakesAction` guards against concurrent requests via `submittingTakes` flag, rejects dirty plans with descriptive error, and verifies takes are in `ready` or `generated` status.
  > - **Frontend review inspector UI (`frontend/src/views/SessionView.jsx`)**:
  >   - Step 4 (Review) table enhancements: "Select All Ready Takes" header checkbox, row-level selection checkboxes for ready ungenerated takes, preparation status badges (`Ready`, `Generated`, `Requires re-prep`, `Pending prep`, `Unsaved edits`), per-take "Review" (expand/collapse) and "Prepare" actions.
  >   - Expandable Take Inspector: Effective wardrobe & look display with provenance source (`initial`, `from_here`, `this_take`), pinned resource revisions with content digests, authoritative backend compiled `final_prompt` in styled `<pre>` container, compiler and mapping metadata, conflict resolution UI with inline input and "Adapt" action calling `recordTakeAdaptation`, and resolved adaptations review.
  >   - Review toolbar: Batch "Prepare Incomplete Takes", "Approve Review (Rev N)" button gating submission on current revision review, and "Test Generate Selected" button with double-click / in-flight protection (`disabled={submittingTakes}`).
  >   - Legacy isolation: Step 4 review tools and test generation controls remain strictly guarded under `isResource` mode.
  >
  > Verification commands and outcomes (all pass):
  > - `npm --prefix frontend test` (251 passed across 11 test files, including 18 dedicated Task 5.4 tests)
  > - `npm --prefix frontend run build` (clean Vite production build)
  > - `.venv\Scripts\python.exe -m pytest` (1245 passed, 3 warnings in 65.24s)
  > - `.venv\Scripts\python.exe -m pytest tests/test_no_personal_data.py tests/test_shoot_checks.py` (46 passed)
  > - `git diff --check` (clean, no whitespace or formatting errors)
  > - `npx --yes @fission-ai/openspec validate adopt-resource-session-planning --strict` (Change 'adopt-resource-session-planning' is valid)

## 6. Migration, acceptance and documentation

- [x] 6.1 Exercise backup, additive upgrade and disable-new-mode rollback on an isolated database; verify legacy sessions, source revisions and finished shots survive and no destructive downgrade runs automatically.

  > 6.1 status: **complete.** Online WAL-consistent database backup, additive migration verification, and non-destructive operational rollback for resource session planning were implemented, documented, and thoroughly verified on isolated databases:
  >
  > - **Atomic online SQLite backup (`backend/backup.py`)**:
  >   - Implemented `backup_database(source_path, target_path, *, overwrite=False, pages=-1, progress=None) -> Path` using Python's standard library `sqlite3.Connection.backup()`.
  >   - Directly opens the source SQLite database without calling `db.connect()`, ensuring pre-migration source databases remain strictly untouched by application upgrade hooks prior to backup.
  >   - Safely captures committed WAL transactions, runs `PRAGMA integrity_check` on the backup connection, writes into a temporary file alongside the target, and atomically publishes the destination.
  >   - For `overwrite=False`, publication uses `os.link` exclusively for atomic collision detection; if `os.link` fails or is unsupported, it fails safely without falling back to `os.rename` or `os.replace` that could overwrite concurrent destinations.
  >   - For `overwrite=True`, publication uses `os.replace` for atomic replacement.
  >   - Ensures temporary files are cleaned up on any exception during backup or publication, preserving previous destination files intact.
  > - **Reproducible backup CLI utility (`scripts/backup_db.py`)**:
  >   - Provides a CLI accepting `--config`, `--data-dir`, `--source`, `--target` (`-o`), `--force` (`-f`), and `--json`.
  >   - Automatically resolves default database paths and writes timestamped backups to `<data_dir>/backups/idevgen-backup-<timestamp>.db` when no explicit destination is given.
  > - **Operational rollback without destructive downgrade (`backend/main.py`, `config.example.json`)**:
  >   - Added `"resource_planning_enabled": true` to `config.example.json` and `ConfigIn`.
  >   - Added `is_resource_planning_enabled()` helper respecting `IDEVGEN_RESOURCE_PLANNING_ENABLED` environment variable and `CONFIG["resource_planning_enabled"]`.
  >   - Added `is_session_resource_mode(session_or_sid)` helper to identify sessions operating in `resource-v1` mode.
  >   - When disabled (`false`), mutating endpoints return HTTP 503 (`Resource planning is disabled by configuration`) before executing persistent writes: session creation with `composition_mode == "resource-v1"`, updates, cloning into resource-v1, draft plan saving, preparation start, adaptation recording, take preparation, and all shot creation/preparation endpoints on resource-v1 sessions (`/shots`, `/compose`, `/compose-run`, `/compose-combination`, `/compose-session`, `/import`, `/reshoot`, `/reshoot-below`).
  >   - Preserves delivery of already prepared and approved snapshots (`submit_plan_preparation`, `submit_selected_plan_preparations`, `run`, `retry`, `cancel`) without preparing new work.
  >   - Fully preserves read operations (`GET /api/sessions/{sid}/plan`, resource libraries, and revision inspections).
  >   - Does not run any destructive schema downgrades or table dropping; re-enabling the flag immediately restores full write capability without data loss.
  > - **Dedicated verification suite (`tests/test_backup_upgrade_rollback.py`)**:
  >   - Added 13 comprehensive integration tests using isolated temporary databases and fake ComfyUI doubles:
  >     1. `test_backup_database_wal_consistency`: Backs up a database in WAL mode with uncheckpointed commits and verifies data integrity.
  >     2. `test_backup_pre_upgrade_preserves_source_schema`: Ensures backing up an unmigrated legacy database does not execute additive migrations on the source.
  >     3. `test_backup_safety_and_failure_handling`: Verifies overwrite guards, directory creation, path collisions, and temp file cleanup on error.
  >     4. `test_backup_publish_failure_cleans_temp_and_preserves_target`: Forces failure during final publish; verifies previous destination remains untouched and all temp files are deleted.
  >     5. `test_backup_refuses_concurrent_target_creation_without_overwrite`: Tests direct atomic collision via `os.link` when destination appears concurrently during backup with `overwrite=False`; preserves concurrent target and cleans up temp files.
  >     6. `test_backup_link_failure_safe_refusal_without_destructive_fallback`: Forces `os.link` failure or unavailability with `overwrite=False`; ensures no destructive fallback (e.g. `os.replace` or `os.rename`) is called, destinations remain untouched, and temp files are deleted.
  >     7. `test_backup_cli_entry_point`: Exercises CLI execution with default and explicit targets and `--json` output.
  >     8. `test_additive_upgrade_from_pre_change_schema`: Upgrades a true pre-feature legacy database (models, legacy sessions, shots, outputs on disk) and verifies zero data loss and all legacy rows/files survive.
  >     9. `test_operational_rollback_preserves_legacy_and_resource_state`: Disables resource planning on a database containing both legacy and resource sessions; verifies legacy workflows run to completion, resource drafts/revisions remain intact, and no tables/columns are dropped.
  >     10. `test_rollback_disables_resource_v1_writes_with_http_503`: Verifies HTTP 503 is returned across all resource draft and planning write endpoints when disabled.
  >     11. `test_rollback_blocks_all_shot_creation_endpoints_for_resource_v1`: Verifies HTTP 503 is returned across all shot creation and preparation endpoints (`shots`, `compose`, `compose-run`, `compose-combination`, `compose-session`, `import`, `reshoot`, `reshoot-below`) for `resource-v1` sessions with 0 inserted or modified rows, while legacy sessions remain unblocked.
  >     12. `test_rollback_keeps_reads_and_queue_actions_functional`: Verifies pre-prepared resource takes can be submitted to the shot queue and executed while planning is disabled.
  >     13. `test_operational_re_enable_restores_resource_planning`: Verifies toggling configuration back to enabled restores full write capabilities seamlessly.
  >
  > Verification commands and outcomes:
  > - `python -m pytest tests/test_backup_upgrade_rollback.py` (13 passed)
  > - `python -m pytest tests/test_backup_upgrade_rollback.py tests/test_db_migrate.py tests/test_resource_store.py tests/test_session_plan.py tests/test_legacy_session_baseline.py tests/test_no_personal_data.py tests/test_shoot_checks.py` (214 passed)
  > - `git diff --check` (clean, no whitespace or formatting errors)
  > - `npx --yes @fission-ai/openspec validate adopt-resource-session-planning --strict` (valid)
- [x] 6.2 Demonstrate the twelve-portrait acceptance workflow with invented adult-character resources, fixed clothing, a jacket change, interruption and recovery; verify effective state and saved prompts independently of the implementation, without GPU or network.

  > 6.2 status: **complete.** The integrated twelve-portrait acceptance flow, a second conservative-invalidation defect 6.2 was set up to discover, and the production invalidation policy that defect required were implemented, documented, and thoroughly verified end to end:
  >
  > - **Integrated acceptance demonstration (`tests/test_resource_session_acceptance.py`, new file, two narrative tests)**:
  >   - `test_twelve_portrait_acceptance_flow` walks the full workflow in clearly labelled phases inside a single test method:
  >     1. builds a fully invented English-only `rooms` JSON resource on `tmp_path` and runs the real import pipeline (`resource_import.preview_import` then `resource_import.commit_import`) to obtain the immutable `library_key` / `source_id` / `content_digest` triple;
  >     2. opens a `resource-v1` session bound to a fictional adult character using the existing `client` fixture and the model seeded by `conftest.py`;
  >     3. creates exactly twelve takes `portrait-01` … `portrait-12` with stable IDs, varied explicit camera / framing / pose / expression, and shared identity / look / initial wardrobe;
  >     4. saves plan revision 1 through the real CAS path and asserts the twelve take IDs, the selected resource revision triple, the empty `wardrobe_changes`, and the per-take expected effective wardrobe (all twelve equal to the initial wardrobe). The expected wardrobe map is built directly from the `ACCEPTANCE_INITIAL_WARDROBE` constant and the `ACCEPTANCE_TAKE_CHOICES` list the test pins — NOT from `resolve_effective_wardrobes`;
  >     5. finalizes takes 1, 2, 3 through the high-level `POST /api/sessions/{sid}/plan/takes/{take_id}/prepare` path and asserts, for each, the exact `final_prompt`, the exact `effective_state` (with the `expected_initial_wardrobes` map), the pinned provenance referencing the imported revision triple, the `mapping_version` / `compiler_version` / `preparation_version` metadata, and the `ready` status;
  >     6. closes the SQLite connection, reopens the same `idevgen.db` file on disk, and asserts through `GET /api/sessions/{sid}/plan` (the public endpoint) that all three completed preparations are recovered byte-for-byte (no regeneration), the remaining nine are still incomplete, and no new shot rows were created as a side effect of the recovery;
  >     7. prepares `portrait-08` as the affected-probe take, capturing its revision-1 snapshot to prove the later jacket change must invalidate it;
  >     8. saves plan revision 2 with exactly one `from_here` wardrobe change at `portrait-07`, asserts through the same public endpoint that takes 1–3 remain `ready` (effective wardrobe, look, final prompt, provenance, version metadata all unchanged), the revision-1 snapshot of `portrait-08` is preserved as immutable history, and the current revision recognises the probe take as requiring re-preparation;
  >     9. finalizes the remaining takes under the new revision and asserts, for every one of the twelve, that the persisted `effective_state["wardrobe"]` equals the literal expected value from `expected_jacket_split` (a map built from the constants and the take list, NOT from the production resolver);
  >     10. approves the current revision and submits a single take twice through the public `POST /api/sessions/{sid}/plan/preparations/submit-selected` path, asserting that the second call returns the same `shot_id` as the first, that the queue is not duplicated, and that the produced shot's prompt equals the persisted `final_prompt` without any actual generation being launched.
  >   - `test_acceptance_file_carries_no_personal_data` scans the new file for paths, emails, IPs, tokens and corpus prose, mirroring the repo-wide guard.
  > - **Genuinely independent oracles**: the test does NOT import `session_plan`, does NOT call `resolve_effective_wardrobes`, `compose_final_prompt` or `assemble_adapted_clauses` to build expectations, and does NOT call `recover_preparation` directly — recovery is read through `GET /api/sessions/{sid}/plan`, the public endpoint whose `preparation` block the test asserts on. The two helpers `_expected_take_clauses` and `_expected_prompt` build the expected prompt as a literal English-only join of trigger, base positive, look, wardrobe, and the four take choices. A drift in the production resolver or composer is detected by the test failing the persisted `effective_state["wardrobe"]` and `final_prompt` byte-for-byte assertions against the literal expected map, not by a self-confirming second opinion.
  > - **Defect discovered and fixed in `backend/session_plan.py`**: the previous `_compute_affected_take_ids_for_plan_change` only compared `resolve_effective_wardrobes` between the old and new plan, so a revision that edited a take's `camera`, `framing`, `pose` or `expression` (the closed `TAKE_DESCRIPTIVE_CHOICES` allowlist the `resource-prompts` spec names as preparation inputs that feed `final_prompt`) left the prior `ready` row at status `ready` even though the new `final_prompt` would differ from the persisted one. The fix is fully generic and adds no hardcoded identifiers (no `portrait-07`, no `portrait-08`, no `jacket`, no twelve-take count):
  >   - Added `_take_by_id(plan)` and `_take_content_signature(take)` (deterministic `json.dumps(sort_keys=True)` projection of every per-take field except `take_id`, the comparison key).
  >   - Extended `_compute_affected_take_ids_for_plan_change` to mark a take affected when ANY of the three conditions holds: the take is in the old plan but missing from the new; the take's effective wardrobe under the new plan differs from the old; the take's per-take content signature under the new plan differs from the old. The third clause catches any per-take field change the `resource-prompts` spec names as preparation input (including future take-level fields) without code changes here.
  >   - Updated the function's docstring so it no longer claims the criterion is limited to the effective wardrobe; it now lists the three clauses and the conservative interpretation the design names.
  >   - `save_draft` already threads the result of this function through `affected_take_ids` to `invalidate_ungenerated_prepared_takes`; the conservative scope (affected set ∪ orphans) is preserved. The CAS, the revision-approval gate, the `generated` / `invalidated` history guarantee, and the constants-strict policy are untouched.
  > - **New regression coverage (`tests/test_session_plan.py`)** that pins the new policy:
  >   - `test_a_take_content_edit_invalidates_the_prior_ready_row` is parameterised over the four `TAKE_DESCRIPTIVE_CHOICES` (`camera`, `framing`, `pose`, `expression`). Each parameterisation saves a plan with three real takes carrying every creative field, plants a `ready` row for each, saves a new revision that edits exactly one creative field on `take-002` and flips the take's `label` (a future take-level field) without touching any `from_here` / `this_take` boundary, and asserts the targeted take's prior row is `invalidated` while the two unrelated takes stay `ready`. None of the four parameters uses an orphan take_id; the take is real and present in `plan.takes` on both revisions.
  >   - `test_a_generated_take_survives_a_take_content_edit` is the companion regression: a `generated` row whose take's content was edited by a later revision stays `generated` and byte-for-byte identical, and the linked shot is untouched. The pass targets only `pending` and `ready` rows; the `generated` history is preserved.
  >   - The existing `test_a_wardrobe_change_preserves_prior_ready_rows` (the `from_here` rule) and `test_a_queued_or_generated_snapshot_remains_unchanged_after_a_draft_edit` (the generated-immutability rule) continue to pin the design's two companion contracts side by side with the new content-edit regression.
  > - **Incidental production fix preserved from the previous pass (`backend/resource_import.py`)**: `commit_import` now forwards the `kind` derived from `rebuilt.accepted_outcomes[0].kind` to `resource_store.ensure_library`, so committed libraries land with the correct `kind` and `finalize_take_preparation` no longer fails on a missing `kind`. No regression introduced.
  >
  > Verification commands and outcomes (all pass):
  > - `python -m pytest tests/test_resource_session_acceptance.py` (2 passed, 3 warnings in 0.45s)
  > - `python -m pytest tests/test_resource_session_acceptance.py tests/test_session_plan.py tests/test_resource_preparation.py tests/test_resource_import.py tests/test_legacy_session_baseline.py tests/test_no_personal_data.py tests/test_shoot_checks.py` (359 passed, 3 warnings in 22.35s)
  > - `python -m pytest` (1268 passed, 0 failed, 3 warnings in 70.98s)
  > - `npx --yes @fission-ai/openspec validate adopt-resource-session-planning --strict` (Change 'adopt-resource-session-planning' is valid)
  > - `git diff --check` (clean: zero matches for `No newline`, `trailing whitespace`, `space before tab` or `indent` errors; only the Windows CRLF/LF informational warnings the rest of the working tree carries)
  > - `git status --short` shows exactly the five intended paths and no private artifact (`config.json`, `data/`, `frontend/dist/`, source corpus, images, operator paths): `M backend/resource_import.py`, `M backend/session_plan.py`, `M openspec/changes/adopt-resource-session-planning/tasks.md`, `M tests/test_session_plan.py`, `?? tests/test_resource_session_acceptance.py`.

- [ ] 6.3 Deliver a private corpus coverage report from the actual operator-selected sources, accounting for every library and field without publishing source prose; verify every accepted supported resource has a declared use and every unsupported item remains explicitly reported. Do not call pending mapping work complete adoption.
- [ ] 6.4 Update README and matching session/import/limitations documentation, including technical restrictions retired, original-language private storage, reference behavior and lack of pixel-level continuity guarantees; verify descriptions against delivered behavior.
- [ ] 6.5 Run the complete backend suite with `python -m pytest`, frontend tests with `npm --prefix frontend test`, and production build with `npm --prefix frontend run build`; verify all pass, data/config/build outputs stay untracked and no new public artifact contains personal data or source corpus text.
- [ ] 6.6 Validate the completed change with OpenSpec and review the acceptance evidence before enabling resource mode by default for new sessions; verify all tasks are supported by actual checks and legacy entry remains available.
