## MODIFIED Requirements

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

## ADDED Requirements

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
