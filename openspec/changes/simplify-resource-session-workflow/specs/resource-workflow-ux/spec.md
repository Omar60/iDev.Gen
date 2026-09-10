## Purpose

Make resource ingestion, management and `resource-v1` session authoring understandable to a normal user by making local file selection, non-destructive resource removal and guided take preparation the default while retaining exact resource provenance, first-class manual authoring, optional assistant automation, expert controls and explicit generation authorization.

## ADDED Requirements

### Requirement: Normal resource import uses browser-selected raw source files

The application SHALL let a user select one or more local JSON resource files through the browser and preview them without typing a filesystem path that the backend must open. Browser-provided file names SHALL be treated as metadata, not trusted client filesystem paths or library identity. The browser SHALL transfer source files as raw bytes without frontend parse/reserialize mutation. The backend SHALL stage and bind the exact selected bytes used for preview and commit behind opaque browser-facing identifiers. Physical server staging paths SHALL remain private implementation state and SHALL NOT be required or exposed in the browser-facing flow.

The normal browser import SHALL accept at most 20 files per selection, at most 10 MiB per file and at most 50 MiB total staged source bytes. Staged browser-import state SHALL expire after 24 hours. The backend SHALL enforce these limits before expensive parsing or persistence.

#### Scenario: User selects resource files
- **WHEN** a user chooses one or more supported JSON files with the normal import control
- **THEN** the files become previewable without the user entering an absolute or relative backend filesystem path
- **AND** the backend does not attempt to open a client-side browser path
- **AND** preview is bound to the raw bytes that were selected

#### Scenario: Browser source selection exceeds a limit
- **WHEN** a selection contains more than 20 files, any file exceeds 10 MiB, or total staged source bytes exceed 50 MiB
- **THEN** the browser-import operation is refused with a clear limit error
- **AND** no accepted resource revision is persisted from that refused selection

#### Scenario: Staging expires or differs after preview
- **WHEN** the staged content bound to an import preview is missing, older than 24 hours, or no longer matches the previewed digest
- **THEN** commit is refused and the user must obtain a fresh selection/preview

#### Scenario: Existing local automation uses path import
- **WHEN** an expert or CLI caller uses the existing path-based resource import contract
- **THEN** that workflow remains supported and produces the same canonical import behavior as equivalent browser-selected content

### Requirement: Browser import identity comes from the source `library` field

For browser-selected resource import, the application SHALL use the supported source envelope's top-level `library` value as the sole authoritative `library_key`. The value SHALL be a string of 1 through 128 characters with no leading/trailing whitespace, ASCII control characters, `/`, `\\`, and SHALL NOT be `.` or `..`. A valid declared value SHALL be used exactly as declared.

The browser workflow SHALL NOT derive a library identity from the file name, browser path or normalized file stem, SHALL NOT case-fold or slugify a declared identity, SHALL NOT silently invent suffixes, and SHALL NOT expose an external `library_key` override. Missing or invalid `library` declarations SHALL be preview errors that require correcting the source file.

#### Scenario: Source declares a valid library identity
- **WHEN** a selected resource envelope contains `"library": "general_scenes"`
- **THEN** browser preview targets `library_key = "general_scenes"`
- **AND** the user is not asked to type or confirm another key

#### Scenario: File name differs from declared identity
- **WHEN** a file named `my-scenes-final.json` declares `"library": "general_scenes"`
- **THEN** the target library remains `general_scenes`
- **AND** the file name does not affect persistence identity

#### Scenario: Source does not declare a valid identity
- **WHEN** the supported envelope is missing `library` or contains an invalid library value
- **THEN** browser preview refuses the file with a library-identity error
- **AND** no file-stem fallback or UI override is offered

#### Scenario: Two selected files declare the same library
- **WHEN** two selected files both declare the same valid `library` value
- **THEN** they intentionally target the same logical library
- **AND** the canonical duplicate-source/content rules decide whether their entries are compatible

### Requirement: Simplified import preserves preview and atomic commit guarantees

The normal import UI SHALL present preview and commit as one user workflow while preserving the existing two-phase integrity boundary. Changing selected files or staged bytes SHALL invalidate the prior preview. The primary summary SHALL describe user-relevant outcomes, while exact outcome classifications and safe technical identifiers remain inspectable.

Browser-facing preview state SHALL NOT serialize physical staging paths even if internal canonical preview objects contain path-backed fingerprints. Commit SHALL resolve canonical server-side preview/staging state from opaque browser identifiers.

#### Scenario: Valid selection is previewed
- **WHEN** selected files pass parsing and declared-identity checks
- **THEN** the UI presents a concise summary of resources that are new, updated, unchanged, restored or need attention
- **AND** the persistence action remains disabled until a valid current preview exists

#### Scenario: User changes selected content
- **WHEN** a user selects different source bytes after preview
- **THEN** the old preview is invalidated
- **AND** commit cannot use the stale preview

#### Scenario: User requests technical details
- **WHEN** a user expands import diagnostics
- **THEN** the full safe canonical report remains available including unresolved items and safe revision identifiers
- **AND** private server staging paths remain undisclosed

### Requirement: Translation maps can be selected as local files

The normal Resources UI SHALL allow a user to select a local JSON translation map without typing a backend-visible map path. The selected JSON SHALL use the existing direct `translation_map` content contract. Translation preview and apply SHALL remain bound to the selected normalized map content and current library state using the translation contract's existing validation, canonical map digest, attestation and stale-preview protections. Browser-selected/direct-content translation input SHALL be limited to 10 MiB.

#### Scenario: User selects a translation map
- **WHEN** a user chooses a supported translation-map JSON file for an imported library
- **THEN** translation preview can run without the user typing a filesystem path

#### Scenario: User replaces the selected translation map
- **WHEN** a different map file is selected after a translation preview
- **THEN** the previous translation preview is invalidated
- **AND** apply requires a preview of the newly selected content

#### Scenario: Translation map exceeds the direct-content limit
- **WHEN** the selected direct-content map exceeds 10 MiB
- **THEN** preview/apply refuses the request before translation persistence

### Requirement: Imported resources can be deleted and restored non-destructively

The Resources workflow SHALL let a user logically delete and restore an imported logical library and an individual logical source entry. Delete SHALL remove the target from normal inventory, new resource selection and new preparation while preserving immutable accepted revisions and historical exact-resource references. Delete and Restore SHALL be transactional and idempotent.

Deleting a library SHALL hide all of its entries through library state without manufacturing individual-entry tombstones. Restoring a library SHALL clear only the library deletion state; source entries deleted individually SHALL remain deleted until explicitly restored.

Existing prepared/generated snapshots that already contain exact immutable resource inputs SHALL remain inspectable/replayable according to their historical snapshot semantics. An unfinalized draft that needs a now-deleted resource for new preparation SHALL be blocked with a readable deleted-resource reason rather than silently selecting another revision.

#### Scenario: User deletes a library
- **WHEN** the user confirms Delete on an imported library
- **THEN** the library and its entries disappear from normal inventory and new resource selection
- **AND** accepted immutable revisions remain stored for historical exact-reference resolution

#### Scenario: User restores a library with an individually deleted entry
- **WHEN** a library is restored after one of its source entries had been individually deleted
- **THEN** the library becomes available again
- **AND** the individually deleted entry remains unavailable until explicitly restored

#### Scenario: Historical generated evidence references deleted content
- **WHEN** an existing generated/prepared snapshot references an exact revision belonging to a now-deleted library or entry
- **THEN** that historical exact revision remains resolvable for inspection/replay of the finalized snapshot

#### Scenario: Unfinalized draft references deleted content
- **WHEN** new preparation is requested for a draft whose required resource was deleted
- **THEN** preparation is blocked with a deleted-resource reason
- **AND** another revision is not silently substituted

### Requirement: Re-import reuses and restores a deleted declared library

A successful browser import whose valid declared `library` matches a soft-deleted logical library SHALL reuse that existing library identity and restore the library as part of the same atomic commit. It SHALL NOT create a duplicate logical library. Individual source-entry deletion state SHALL NOT be silently cleared by library re-import.

#### Scenario: Deleted library is re-imported
- **WHEN** `general_scenes` is soft-deleted and a new source file declares `"library": "general_scenes"`
- **THEN** preview reports reuse/restoration of the existing logical library
- **AND** successful import clears library deletion state while retaining existing immutable revision history

#### Scenario: Re-import contains an individually deleted source ID
- **WHEN** an individually deleted source entry appears again in a re-import of its library
- **THEN** its immutable revisions may be imported according to canonical revision rules
- **AND** the source entry remains logically deleted until an explicit Restore action

### Requirement: Guided resource session creation is atomic

The normal `resource-v1` creation flow SHALL create the session row and its initial authoritative plan as one transactional operation after validating all guided inputs. A failed guided validation or initial plan write SHALL NOT leave an orphaned or partially initialized session. Assistant work SHALL begin only after this transaction succeeds.

Existing legacy/general session creation contracts SHALL remain available unchanged.

#### Scenario: Initial plan validation fails
- **WHEN** guided creation receives an invalid anchor, take count, brief or variation policy
- **THEN** creation is refused before assistant work
- **AND** no orphaned guided session row remains

#### Scenario: Guided creation succeeds
- **WHEN** all guided inputs are valid
- **THEN** the created session and initial `resource-v1` plan become durable together
- **AND** the plan already contains all requested stable take IDs before optional assistant work starts

### Requirement: Resource session creation requests bounded high-level intent

The normal `resource-v1` creation flow SHALL let a user define a session using a character model, one ready non-deleted scene anchor, requested take count from 1 through 20, optional session brief up to 2,000 characters, applicable constant look/wardrobe choices, a complete variation policy and an explicit authoring mode of `automatic` or `manual`. Session creation SHALL NOT require a configured prompt assistant.

Both authoring modes SHALL create the same persisted `resource-v1` draft, exact scene anchor, requested stable take IDs, plan-CAS state and eventual review/generation path. Automatic mode MAY fill unlocked camera, framing, pose and expression through the configured assistant. Manual mode SHALL make no assistant call and SHALL let the user fill the same closed take-choice fields directly.

#### Scenario: User creates a twelve-take automatic resource session
- **WHEN** a user selects a character and ready scene anchor, requests twelve takes and chooses automatic authoring
- **THEN** the system atomically creates a persisted resource draft containing twelve stable take IDs before assistant work
- **AND** the user is not required to type twelve sets of camera, framing, pose and expression values

#### Scenario: User creates a twelve-take manual resource session
- **WHEN** a user selects a character and ready scene anchor, requests twelve takes and chooses manual authoring
- **THEN** the system atomically creates the same persisted twelve-take resource draft without calling an assistant
- **AND** the user may fill camera, framing, pose and expression manually for each take before deterministic preparation

#### Scenario: Requested take count is outside bounds
- **WHEN** guided creation requests fewer than one or more than twenty takes
- **THEN** validation fails before the session/plan transaction and before assistant work

#### Scenario: Session brief exceeds the limit
- **WHEN** the optional brief exceeds 2,000 characters
- **THEN** guided creation or plan mutation refuses it before assistant work

### Requirement: Automatic authoring fills only unlocked take choices

When automatic authoring is selected and a configured prompt assistant is available, the application SHALL fill unlocked take-level descriptive choices only from the closed set `camera`, `framing`, `pose` and `expression`. Assistant output SHALL pass through the resource preparation contract's validation before finalization and SHALL NOT overwrite fixed character identity, look, initial wardrobe, effective wardrobe, resource selection, scene anchor, adaptations, resolved fixed variation values or already locked take choices.

#### Scenario: All four creative choices are unlocked
- **WHEN** automatic preparation runs for a take with camera, framing, pose and expression unlocked
- **THEN** the configured assistant is asked only for those unlocked fields
- **AND** validated values are incorporated into the take's prepared effective state

#### Scenario: Take already fixes camera
- **WHEN** a take explicitly fixes camera but leaves framing, pose and expression unlocked
- **THEN** automatic authoring cannot replace the camera
- **AND** it may fill only framing, pose and expression

#### Scenario: Assistant attempts to change fixed session state
- **WHEN** assistant output contains a field outside the allowed unlocked take choices
- **THEN** preparation refuses that output
- **AND** no fixed session state is changed or silently persisted

### Requirement: Automatic authoring records assistant provenance

Assistant-authored take choices SHALL retain the existing resource writer synthesis provenance semantics, including the bounded writer input, persisted continuity context, validated writer output and synthesis kind. Automatically generated values SHALL NOT be persisted as if the user manually entered them solely to bypass the assistant provenance contract.

If automatic authoring establishes a previously unresolved fixed variation value, the plan SHALL persist the resolved value with `value_origin = assistant` and the exact bounded resolution input/context plus validated output before any take preparation consumes that value. A user-entered fixed value SHALL record `value_origin = user` and SHALL NOT fabricate assistant provenance.

#### Scenario: Assistant successfully fills a take
- **WHEN** automatic authoring supplies valid values for an incomplete take
- **THEN** the prepared snapshot records assistant synthesis provenance for that take
- **AND** reopening the session does not require another assistant call to reconstruct the finalized prompt

#### Scenario: Assistant establishes a fixed value
- **WHEN** an automatic fixed dimension has no value and the assistant resolves it
- **THEN** the authoritative plan records the value, assistant origin and exact resolution provenance through CAS before per-take preparation
- **AND** later takes/retries reuse that value without asking again

#### Scenario: Ready take is prepared again
- **WHEN** automatic authoring is requested for a take whose current prepared snapshot is already ready or generated
- **THEN** the immutable snapshot is reused according to existing preparation semantics
- **AND** the assistant is not called again merely to recreate the same take

### Requirement: Automatic continuity context is deterministically bounded

Each automatic per-take authoring request SHALL receive persisted continuity state plus summaries of at most the five immediately preceding finalized takes in stable plan order. A prior summary SHALL contain only stable take ID plus camera, framing, pose and expression values. Generated images, arbitrary conversation, full historical prompts and unrelated session history SHALL NOT enter this continuity window.

#### Scenario: More than five earlier takes are finalized
- **WHEN** take 12 is authored and more than five earlier takes have finalized choices
- **THEN** the writer receives summaries for at most the five immediately preceding finalized takes in plan order
- **AND** truncation is deterministic

### Requirement: Automatic preparation is recoverable

The application SHALL persist the resource draft and authoring intent before lengthy assistant work and SHALL retain each successfully completed prepared take independently. Retrying after interruption SHALL resume only work that is incomplete or invalidated for the current authoritative plan revision.

#### Scenario: Authoring is interrupted after three takes
- **WHEN** a twelve-take automatic preparation stops after three takes are completed
- **THEN** reopening the session shows those three completed preparations
- **AND** retry continues with the remaining incomplete takes rather than rewriting the completed three

#### Scenario: Plan changes during or after authoring
- **WHEN** the authoritative plan revision changes
- **THEN** stale authoring work cannot overwrite the newer plan
- **AND** existing plan revision and invalidation rules determine which preparations remain valid

### Requirement: Automatic mode remains valid without configured synthesis

`automatic` SHALL be a valid persisted authoring mode independently of current assistant availability. Lack of assistant configuration SHALL disable or explain the synthesis action, not invalidate, delete or rewrite the automatic draft. The user MAY later configure an assistant or explicitly switch the same plan to `manual` through CAS.

#### Scenario: Automatic draft is created without an assistant
- **WHEN** a user creates or opens an automatic draft on a system without a configured prompt assistant
- **THEN** the draft remains valid and durable
- **AND** automatic synthesis reports that configuration is unavailable
- **AND** the user may explicitly switch the same session to manual mode

### Requirement: Effective take choices are reviewable without mandatory raw editing

The normal Takes and Review surfaces SHALL show the effective camera, framing, pose and expression for each prepared take. In automatic mode those values may be synthesized. In manual mode they are supplied explicitly by the user. Raw per-field editing SHALL remain available for manual authoring and explicit overrides without changing the authoritative preparation/review semantics.

#### Scenario: Automatic preparation completes
- **WHEN** synthesized takes are available
- **THEN** the user can review each take's effective creative choices before generation without entering edit mode

#### Scenario: Manual take is completed
- **WHEN** the user supplies the required take choices in manual authoring mode and prepares the take
- **THEN** the same effective choice summary and review path are used without any assistant provenance or assistant call

#### Scenario: User overrides a generated choice
- **WHEN** a user opens editing and changes a generated take choice
- **THEN** the explicit value is saved through the authoritative session plan
- **AND** affected preparation/review state is invalidated according to existing resource-plan rules before the changed take can be generated

### Requirement: Manual authoring is a first-class no-LLM path

The application SHALL support complete `resource-v1` session creation, take editing, deterministic preparation, review, submission and generation with no configured prompt assistant. Manual authoring SHALL use the same resource revisions, scene continuity, fixed-state validation, CAS revisions, conflict handling and generation gates as automatic authoring. Assistant availability SHALL affect synthesis convenience only, not resource-session capability.

#### Scenario: No assistant is configured
- **WHEN** a user creates or opens a manual resource draft on a system without a configured prompt assistant
- **THEN** the session remains fully usable through manual take completion, preparation, review and generation
- **AND** no assistant configuration is requested as a prerequisite for those operations

### Requirement: Authoring does not authorize generation

Automatic or manual session authoring and preparation SHALL stop at review. Neither mode SHALL automatically approve the current plan revision, submit prepared takes, create implicit generation authorization or start the session runner. Existing conflict, workflow, review and pending-shot gates remain authoritative.

#### Scenario: All requested automatic takes are prepared successfully
- **WHEN** automatic preparation finishes every take
- **THEN** the session enters or remains at the review stage
- **AND** no shot is submitted or generated until the existing explicit review and generation actions are performed

#### Scenario: All requested manual takes are prepared successfully
- **WHEN** manual preparation finishes every take
- **THEN** the same explicit review/submission/generation gates apply
- **AND** no shot is generated merely because no assistant step was needed

#### Scenario: A resource conflict remains unresolved
- **WHEN** either authoring mode encounters or leaves an unresolved resource conflict
- **THEN** review/generation remains blocked by the existing conflict gate
- **AND** the system does not silently adapt or discard the conflicting source content

### Requirement: Legacy and expert workflows remain available

The simplified resource workflow SHALL NOT change legacy session behavior or make resource automation a prerequisite for expert workflows. Legacy measured Catalogue/Compose/Fill/Judge behavior, path-based resource automation, expert multi-resource plans and detailed resource inspection SHALL remain available in their current scopes.

#### Scenario: User opens a legacy session
- **WHEN** a session does not use `resource-v1`
- **THEN** its existing composition and measured-catalogue workflow remains unchanged

#### Scenario: Expert inspects resource details
- **WHEN** a user opens advanced resource details
- **THEN** revision identity, readiness, field roles, coverage, deletion state and other existing diagnostics remain inspectable
