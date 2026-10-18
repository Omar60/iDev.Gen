# resource-workflow-ux Specification

## Purpose

Let ordinary users turn selected mined resource files into a reviewed photographic session with few initial inputs, actionable readiness, optional assistant help and retained expert control.

## Requirements

### Requirement: Normal ingestion uses file selection and readable outcomes

The normal source-import UI SHALL use browser file selection without typed paths or library keys for valid declared sources with no historical ambiguity. Preview SHALL present new, updated, unchanged and needs-attention outcomes separately from readiness. Import SHALL require a current preview. Advanced compatibility targeting SHALL support legacy identities and supported non-envelope sources without source JSON editing.

Safe technical diagnostics SHALL remain expandable. The UI SHALL retain the highest server selection revision and consume only the safe selection/preview projection. Physical staging paths, raw fingerprints and nanosecond timestamps SHALL NOT be displayed or round-tripped. File/target changes and late responses SHALL NOT allow stale Import actions. Import SHALL NOT automatically translate, create a session or generate images.

Resource Browser free-text filtering SHALL use only fields delivered by the safe library-list response: library key, source ID, content digest and translated display values. It SHALL NOT assume an omitted raw payload is present or expose original source payload merely to support search.

#### Scenario: Normal source selection
- **WHEN** a user selects supported files with valid unambiguous declarations
- **THEN** preview and import are available without typing paths, keys or JSON

#### Scenario: Selection changes during preview
- **WHEN** an old preview response arrives after the selection changes
- **THEN** it cannot enable Import for the newer selection

#### Scenario: Server fingerprint exceeds JavaScript precision
- **WHEN** a selected file has server metadata outside JavaScript's safe integer range
- **THEN** the UI continues through opaque string tokens/digests without parsing or echoing that metadata

#### Scenario: Browser filters a safe library-list response
- **WHEN** a library-list row omits the raw resource payload
- **THEN** search uses its delivered safe identifiers, digest and translation values without throwing or silently depending on payload fields

#### Scenario: Compatibility choice is necessary
- **WHEN** a source lacks a usable declaration or overlaps a differently keyed historical library
- **THEN** the UI explains the issue and offers explicit target selection

### Requirement: Imported resources expose an actionable readiness journey

The UI SHALL distinguish imported, needs translation, ready and needs source correction. Missing translations SHALL link directly to editable source-backed rows, optional assistant suggestions or selected translation maps. Invalid structure/unmapped fields SHALL identify the correction needed. Required translation sidecars SHALL NOT be bypassed to make import appear successful.

Users SHALL review translation proposals and explicitly preview/apply them. No map or assistant SHALL be required to enter valid translations manually. After application, readiness SHALL refresh and ready scenes SHALL offer Create session.

#### Scenario: Import succeeds but preparation is blocked
- **WHEN** accepted entries lack required translations
- **THEN** the UI shows needs translation and an immediate translation action
- **AND** it does not label them ready

#### Scenario: No map exists
- **WHEN** the user has no translation file
- **THEN** editable rows and optional suggestions provide a path to readiness without creating a JSON file externally

#### Scenario: Source is malformed
- **WHEN** readiness fails because a required scalar is a list
- **THEN** the UI identifies a source correction instead of offering translation as a cure

### Requirement: Guided creation starts with four understandable inputs

The existing manual/assisted creation surface SHALL retain character, scene, photo count and optional brief as its initial authoring inputs. An optional exact saved-look version selector SHALL be available during manual/assisted creation as well as Full Auto creation. An opt-in Full Auto surface SHALL additionally offer profile readiness and one Generate without pauses action, without per-take fields. Known valid character/scene selections SHALL carry forward. Photo count SHALL default to twelve and require an integer from 1 through 500 for guided creation; brief SHALL be limited to 2,000 characters. Automatic SHALL be the default when an assistant is configured; otherwise manual SHALL be the default with an action to configure an assistant. The server SHALL use the selected character's default workflow unless Advanced supplies an override. A character with neither SHALL show an actionable workflow-required error before creation; it SHALL NOT create an orphan or guess a workflow.

Mode selection, complete fixed/vary policy, look/wardrobe overrides and raw creative fields SHALL remain available without being mandatory initial inputs. The normal policy SHALL vary all four creative dimensions. Fixed dimensions SHALL require explicit values only when users choose them. Automatic SHALL remain a valid persisted mode without current assistant availability, with synthesis disabled/explained rather than the draft invalidated.

Full Auto SHALL show exact look version, stage endpoints/count, total takes, effective canvas and framing intent before consent. Eligible selections SHALL create and start the complete journey with that single action. Ineligible looks SHALL offer a reusable profile correction in Looks. Missing facts SHALL NOT be hidden by disabling manual creation. Choosing Full Auto SHALL not silently switch back to manual or substitute another look/version.

#### Scenario: User requests twelve photos
- **WHEN** character and ready room scene are selected with the default count
- **THEN** the user can create the draft without filling camera, framing, pose or expression

#### Scenario: No assistant is configured
- **WHEN** guided creation opens without an assistant
- **THEN** manual authoring and Configure assistant are available
- **AND** explicitly choosing automatic preserves a valid draft with synthesis unavailable

#### Scenario: Advanced control is wanted
- **WHEN** a user opens Advanced
- **THEN** workflow override, manual editing, shared overrides and variation locks are available without another session type

#### Scenario: Character has no default workflow
- **WHEN** the selected character has no workflow and Advanced has no override
- **THEN** creation identifies assigning a default or choosing an override as the two remedies

#### Scenario: Full Auto creation needs no follow-up buttons
- **WHEN** the user selects eligible inputs and clicks Generate without pauses once
- **THEN** the UI navigates to durable run progress without asking to apply a look, preview progression, prepare each batch, approve or submit

### Requirement: Shared choices and fused limitations are visible

Shared-state suggestions SHALL show proposed look/wardrobe before acceptance. Users SHALL be able to edit suggestions or keep no additional constraint. Effective shared values and their source SHALL be visible before take preparation. Manual wardrobe progression SHALL require an explicit preview and application; Full Auto SHALL use its recorded all-stage authorization in place of preview confirmation while retaining an inspectable timeline. Existing scoped wardrobe controls SHALL remain available.

A fused resource SHALL offer advanced editing or selection of a structured scene instead of silently entering simple automatic authoring. Review SHALL show the complete authorized description and any separately approved adaptation.

#### Scenario: User accepts shared suggestions
- **WHEN** proposed shared values are accepted
- **THEN** they become authoritative through plan save before any take consumes them

#### Scenario: User chooses a fused scene
- **WHEN** a fused scene is inspected for automatic use
- **THEN** its advanced path and limitation are explained without losing the selected resource

### Requirement: Preparation progress and review are user operations

The UI SHALL show the backend operation ID/state plus completed, failed and remaining takes, with Prepare, Cancel and Resume actions reflecting backend ownership. Each preparation operation SHALL prepare at most twenty takes; manual/assisted larger sessions SHALL offer Continue preparation, while Full Auto SHALL chain bounded operations without intermediate clicks. Saved work SHALL survive browser closure. Start retries SHALL reuse a client request ID; a second tab SHALL display active operation progress from `409 authoring_active` rather than launching another call. Stale-revision, assistant-unavailable, cancelled, expired and failed states SHALL retain distinct next actions.

Effective choices SHALL be the primary review surface; final prompts, provenance and diagnostics SHALL be inspectable. Editing SHALL explain affected downstream work. Exact duplicate tuples SHALL be flagged for explicit review without removing deliberate repetitions.

Outside Full Auto, preparation SHALL stop at Review and SHALL NOT approve review, submit shots or start generation. Explicitly authorized Full Auto SHALL continue through validated policy approval, submission and generation using the same underlying services and visible phase boundaries. Explicit review approval, submission and runner actions SHALL remain clearly distinguishable. A small reviewed test selection SHALL remain available before the full session.

Full Auto progress SHALL distinguish preparation, validation, approval, submission, GPU waiting, execution and recovery, with per-take outcomes and retained partial results. Browser closure SHALL not cancel backend work. Cancel and Resume SHALL reflect current server ownership and reject stale controls. A blocked run without pending cancel intent SHALL identify actual failed inputs and offer correction followed by continuation. A blocked run with cancel intent SHALL offer `Reconcile cancellation` for read-only recovery; a manual-transfer request MAY remain available but SHALL stay pending and fenced until verified settlement. Opening Review or switching tabs alone SHALL never authorize work.

#### Scenario: User resumes a partial session
- **WHEN** three of twelve takes were saved before interruption
- **THEN** reopening shows those results and offers preparation of the remaining work

#### Scenario: Another tab already owns authoring
- **WHEN** Prepare receives active-operation status from another tab
- **THEN** the UI adopts that operation view and offers status/cancel controls without another assistant call

#### Scenario: Session exceeds one batch
- **WHEN** twenty of forty takes finish outside Full Auto
- **THEN** the UI offers Continue preparation without truncating the plan or regenerating the completed batch

#### Scenario: Automatic preparation finishes
- **WHEN** all requested takes are prepared outside Full Auto
- **THEN** the user sees Review
- **AND** no review approval, shot submission or runner start occurs implicitly

#### Scenario: User edits an earlier automatic take
- **WHEN** an edit affects later ungenerated authoring
- **THEN** the UI explains which results need preparation again before saving the edit

Reopening SHALL discover the current run from the session-scoped Full Auto endpoint without browser storage. The UI SHALL distinguish waiting_authoring, waiting_runner, blocked and cancellation still draining a remote attempt. Call allowances, policy version and authorization expiry SHALL be visible before consent and during recovery.

For blocked runs without pending cancel intent, Changed-scope Resume SHALL display an old/new comparison of source/profile identities, remaining targets, creative/generation inputs, provider identity/model, budgets and deadline, then submit the current backend-issued scope digest with explicit acceptance. Unchanged Resume SHALL not add another routine approval step. A stale comparison SHALL refresh without dispatching work.

#### Scenario: Browser closes during Full Auto
- **WHEN** the user closes and reopens the browser after some takes finish
- **THEN** current server progress and results are restored without creating another run or duplicating completed work

#### Scenario: Full Auto stops on a real conflict
- **WHEN** a take has a detected unresolved conflict
- **THEN** the UI shows its sources, affected take, correction action and preserved progress
- **AND** continuation uses the corrected revision rather than repeating successful work

### Requirement: End-to-end usability is demonstrated

Acceptance SHALL demonstrate a normal journey from unimported invented source files to a ready reviewed twelve-photo plan without typed paths, keys, external JSON editing or four-field entry per photo. It SHALL include missing translations resolved through reviewed proposals, shared-state decisions and explicit generation gates. A separate no-assistant demonstration SHALL reach review using manual translation and take input. Fused conflicts and historical identity compatibility SHALL also be demonstrated.

#### Scenario: Ordinary automatic journey
- **WHEN** a user starts with source files and a configured fake assistant
- **THEN** file selection, reviewed translations, guided creation and automatic preparation reach review without technical-field prerequisites
- **AND** no generation occurs until explicit authorization

#### Scenario: Offline manual journey
- **WHEN** no assistant or translation map exists
- **THEN** manual translation rows and manual take choices allow preparation and review using the same persistence and generation gates

### Requirement: Resource session lists use plan-owned constants

Resource-v1 session detail, cards and search SHALL display/filter the authoritative plan look and initial wardrobe. They SHALL NOT display stale legacy session-column values after a plan edit. Legacy session cards/search SHALL continue using existing session values. A resource-v1 session missing its plan SHALL show an inconsistent-state diagnostic rather than silently substituting legacy columns.

#### Scenario: Plan look changes after guided creation
- **WHEN** a resource-v1 look changes through plan CAS
- **THEN** session detail, listing and search use the changed plan value without a mirrored session write

#### Scenario: Legacy session remains searchable
- **WHEN** a legacy session has only session-row look and wardrobe
- **THEN** its existing display and search results remain unchanged

### Requirement: Reusable looks have a discoverable personal editor

The application SHALL offer a personal Looks surface with Create, Import JSON, Export JSON and From photo actions. Users SHALL be able to name looks, edit constant appearance, add/reuse individual garments and confirm removal order without technical keys. Image extraction SHALL be an explicit action producing an editable proposal, not an immediate saved look.

Outside Full Auto, guided sessions SHALL offer an optional Saved look selector both at creation and in the shared-state summary and selecting a preset SHALL not introduce a mandatory additional initial input. Full Auto SHALL require its exact look selection before authorization. Users SHALL see the distinction between appearance that stays fixed and clothing that may change. Manual plan clothing changes SHALL show a timeline preview, final stage and interval controls, while Keep clothing remains the manual default. Full Auto SHALL expose profile readiness and the authorized all-stage full-interval policy, with an inspectable timeline but no mandatory preview confirmation. Photo references SHALL be identified as extraction inputs rather than generation references.

Looks SHALL allow reusable coverage and appearance-ownership correction at the look/profile level and explain why insufficient legacy text cannot establish those facts. The session UI SHALL distinguish manual versus derived coverage and show source version and stage. Full-body framing and canvas controls SHALL explicitly describe intent, not guaranteed feet, anatomy or garment fidelity.

#### Scenario: User creates a look from a photo
- **WHEN** a user selects From photo and explicitly requests extraction
- **THEN** visible descriptions and garments appear for editing
- **AND** Save requires review and explicit removal-order confirmation

#### Scenario: Saved look is used again
- **WHEN** a user selects an existing look in a new session
- **THEN** its appearance and outfit are available without re-uploading or retyping them

#### Scenario: User plans clothing changes
- **WHEN** the user opts into manual progression
- **THEN** a preview shows what remains worn for each affected take before applying
- **AND** constant appearance remains visibly separate

#### Scenario: User has no vision assistant
- **WHEN** the text assistant is configured but no explicit verified/declarative vision model exists
- **THEN** Extract is unavailable before image transmission and offers Configure vision
- **AND** the same editor still allows manual descriptions and garment creation

#### Scenario: Old look lacks automation facts
- **WHEN** a user selects a legacy text-only look for Full Auto
- **THEN** the UI identifies missing reusable profile information and offers Looks correction or the unchanged manual flow without requesting coverage for every take

#### Scenario: Manual creation includes an exact look
- **WHEN** a user selects an optional saved-look version during manual creation
- **THEN** the first plan contains that snapshot without automatic progression or a Full Auto run
- **AND** later manual profile application is explicit and retains preview, review, submit and Run controls

#### Scenario: Authorization includes unattended restart
- **WHEN** the Full Auto consent summary is shown
- **THEN** it states that unchanged authorized work continues after browser closure and automatically resumes after backend restart until cancellation, a blocker, budget exhaustion or expiry
- **AND** its effective workflow/provider, allowances and exact deadline match the backend preflight scope

### Requirement: Users can archive saved looks from Looks

Looks SHALL provide an accessible archive action with confirmation that says history is retained. Cancel sends no request. Success removes the look from the active list; archive is unavailable while edits or photo staging are pending. Failures retain the list and show a readable error.

#### Scenario: Confirm archive
- **WHEN** the user confirms archiving a saved look
- **THEN** the client sends one archive request and removes the look from the active list after success
- **AND** the existing look versions and session snapshots remain available

#### Scenario: Cancel archive
- **WHEN** the user cancels the archive confirmation
- **THEN** no request is sent and the active list and selection remain unchanged

#### Scenario: Archive fails
- **WHEN** the archive request fails
- **THEN** the look remains in the active list and the view presents a readable error

### Requirement: Archived looks stay out of active selectors

Archived looks SHALL not appear in active selectors for new sessions. Exact historical version reads SHALL remain available. This change adds no archive browser or restore action.

#### Scenario: Select a look for a new session
- **WHEN** a user opens an active saved-look selector after a look was archived
- **THEN** the archived look is not offered for selection

### Requirement: Resource drift has an explicit recovery action

Review and recovery SHALL expose stale prepared inputs and offer Refresh resources with the affected takes and the need to prepare/review again. Refresh SHALL use the dedicated CAS operation without asking users to edit creative fields. Stale results SHALL NOT appear approved or submittable. Missing translations SHALL link to existing readiness correction. Refresh SHALL NOT trigger assistant calls or generation; subsequent Prepare and Approve remain explicit.

#### Scenario: Ready take no longer matches authorized resources
- **WHEN** a user opens Review after a consumed translation changes
- **THEN** the UI explains why the prepared result is stale and offers Refresh resources
- **AND** refreshing preserves creative choices while showing the new revision and preparation required

### Requirement: Manual transfer waits for cancellation settlement

Switching to manual SHALL request Cancel and show the transfer as pending. Full Auto ownership and manual-control fencing SHALL remain until all owned attempts are verified settled. Only then SHALL the pending transfer complete with outputs preserved and without resuming or dispatching Full Auto.

#### Scenario: Manual transfer waits for cancellation settlement
- **WHEN** the user switches to manual while an owned attempt is unknown after bounded probes
- **THEN** the UI shows the transfer pending and keeps manual controls unavailable while Full Auto remains owner
- **AND** verified settlement enables transfer with completed outputs preserved and no Full Auto restart

### Requirement: Pending cancellation recovery cannot resume work

When a blocked run retains cancel intent, the UI SHALL label the existing Resume control `Reconcile cancellation` and offer it only as a user-triggered, read-only status check. It SHALL show blocked on outage/unknown, cancel_requested for a verified running attempt, and cancelled after verified settlement. It SHALL offer no ordinary continuation or risk-accepted retry while intent remains.

#### Scenario: Blocked cancellation shows reconciliation only
- **WHEN** the user opens a run blocked with pending cancel intent
- **THEN** the control is labeled `Reconcile cancellation` and no ordinary continuation or risk-accepted retry is offered

#### Scenario: Reconciliation reports remote state without dispatch
- **WHEN** the user invokes `Reconcile cancellation`
- **THEN** the UI shows the reconciled blocked, cancel_requested or cancelled state without starting a new attempt

### Requirement: Principal look review includes automation preparation

Looks SHALL integrate automation-profile preparation into the principal look editor for new manual looks, photo descriptions, outfit-to-look conversion and new look versions. Users SHALL review appearance ownership, ordered garments, coverage, baseline and inventory in that surface. A complete reviewed Save SHALL create the look and its profile without a second editor or Publish action.

#### Scenario: A reviewed photo look is saved for Full Auto
- **WHEN** extraction and the integrated automation review are complete
- **THEN** one Save action creates the principal look and profile revision 1
- **AND** the success view shows the saved look version and Full Auto readiness

#### Scenario: A manually entered look is saved with automation facts
- **WHEN** a user enters the required structured facts manually in the same look editor
- **THEN** the same combined save is available without an assistant or a separate profile publication

#### Scenario: An imported outfit becomes a named look
- **WHEN** the user starts a look from an imported outfit
- **THEN** its ordered items enter the integrated review before the named look and profile are saved
- **AND** importing the outfit itself creates neither a named look nor an accepted automation profile

### Requirement: Incomplete automation retains an explicit normal save

The integrated editor SHALL show missing automation facts and preserve an explicit normal-only save when assistance fails or review is incomplete. It SHALL distinguish saved-but-ineligible looks from automation-ready results. It SHALL not mark unknown facts accepted, silently drop an invalid combined request into a normal save, or require automation setup for ordinary look use.

#### Scenario: A profile proposal fails
- **WHEN** an assistant request fails after the look description is available
- **THEN** the current look edits remain and the user can complete facts manually or save only the ordinary look

#### Scenario: Combined save is rejected
- **WHEN** the server rejects the requested look/profile save
- **THEN** the form retains its edits and shows the actual error
- **AND** no success notice claims a saved ordinary look or an eligible profile

### Requirement: Exact unambiguous profiles are selected visibly

For a newly selected exact look version during guided creation, the UI SHALL automatically select its sole ready profile revision when no choice exists yet, showing the exact identity and readiness before consent. Multiple ready revisions SHALL require a visible choice. Refresh SHALL not replace an existing choice with latest, change a historical session binding or silently accept changed consent.

#### Scenario: A newly saved look has one ready profile
- **WHEN** the user selects its exact version for Full Auto
- **THEN** the exact ready revision is selected and shown without requiring a redundant empty-selector choice
- **AND** preflight and creation send that verified revision and digest

#### Scenario: Multiple ready revisions exist
- **WHEN** the selected version has more than one ready profile and no retained selection
- **THEN** the UI asks which exact revision to use and does not choose the latest silently

#### Scenario: No ready revision exists
- **WHEN** profile discovery finds no eligible revision
- **THEN** the UI displays the missing-profile or readiness diagnostics and a correction action
- **AND** normal creation remains available

#### Scenario: An earlier discovery response arrives
- **WHEN** the user changes the selected look version before a profile-list response arrives
- **THEN** that response cannot select a profile for the newer selection or enable its consent action

#### Scenario: The selected profile changes before authorization
- **WHEN** a visible selection or its verified identity changes after preflight
- **THEN** the old consent summary is invalidated and the next generation requires the current exact summary and explicit authorization

### Requirement: The removal destination is visible before generation

Full Auto SHALL show the confirmed ordered removal stages, intermediate states, final empty inventory and minimum take count before authorization. The user SHALL see that clothing and accessories are progressively removed through the complete inventory. A profile save SHALL not start a session or generate images. Output fidelity SHALL remain an explicitly stated limitation.

#### Scenario: Ordered clothing includes a final moved-aside state
- **WHEN** the user inspects the Full Auto progression for that look
- **THEN** the timeline shows all canonical stages, including the moved-aside state and the later all-items-absent final stage
- **AND** insufficient take count has a readable correction instead of skipping stages

#### Scenario: A look and ready profile are saved
- **WHEN** combined save succeeds
- **THEN** neither preparation nor generation starts until the existing session creation and Generate without pauses authorization occur
