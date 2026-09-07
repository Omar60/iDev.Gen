## 1. Source coverage and compatibility baseline

- [ ] 1.1 Inventory all operator-selected source libraries and auxiliary maps, recording shapes, field roles and readable consumer evidence without copying source prose into tracked files; verify every discovered file has a coverage-ledger entry and every pending item has a reason.
- [ ] 1.2 Define the supported field mappings and explicit adaptations for accepted scene, perspective and auxiliary resources; verify each source field is classified and no required preparation field is silently unused. Record compiled behavior as unverified rather than claim parity.
- [ ] 1.3 Capture invented legacy session fixtures for text-to-image, editing and guided workflows; verify existing prompt composition, wardrobe overrides and evidence remain unchanged before new-mode work.

## 2. Complete local resource storage

- [ ] 2.1 Add additive SQLite migrations for resource libraries and immutable revisions with separate translation and coverage data; verify full nested-object round trips, revision uniqueness and repeated migration against isolated databases.
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
