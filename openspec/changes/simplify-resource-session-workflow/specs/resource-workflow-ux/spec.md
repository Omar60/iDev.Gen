## Purpose

Let ordinary users turn selected mined resource files into a reviewed photographic session with few initial inputs, actionable readiness, optional assistant help and retained expert control.

## ADDED Requirements

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

The initial normal creation surface SHALL show character, scene, photo count and optional brief only as authoring inputs. Known character/scene selections SHALL carry forward. Photo count SHALL default to twelve and require an integer from 1 through 500 for guided creation; brief SHALL be limited to 2,000 characters. Automatic SHALL be the default when an assistant is configured; otherwise manual SHALL be the default with an action to configure an assistant. The server SHALL use the selected character's default workflow unless Advanced supplies an override. A character with neither SHALL show an actionable workflow-required error before creation; it SHALL NOT create an orphan or guess a workflow.

Mode selection, complete fixed/vary policy, look/wardrobe overrides and raw creative fields SHALL remain available without being mandatory initial inputs. The normal policy SHALL vary all four creative dimensions. Fixed dimensions SHALL require explicit values only when users choose them. Automatic SHALL remain a valid persisted mode without current assistant availability, with synthesis disabled/explained rather than the draft invalidated.

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

### Requirement: Shared choices and fused limitations are visible

Shared-state suggestions SHALL show proposed look/wardrobe before acceptance. Users SHALL be able to edit suggestions or keep no additional constraint. Effective shared values and their source SHALL be visible before take preparation. Wardrobe progression SHALL require an explicit preview and application; existing scoped wardrobe controls SHALL remain available.

A fused resource SHALL offer advanced editing or selection of a structured scene instead of silently entering simple automatic authoring. Review SHALL show the complete authorized description and any separately approved adaptation.

#### Scenario: User accepts shared suggestions
- **WHEN** proposed shared values are accepted
- **THEN** they become authoritative through plan save before any take consumes them

#### Scenario: User chooses a fused scene
- **WHEN** a fused scene is inspected for automatic use
- **THEN** its advanced path and limitation are explained without losing the selected resource

### Requirement: Preparation progress and review are user operations

The UI SHALL show the backend operation ID/state plus completed, failed and remaining takes, with Prepare, Cancel and Resume actions reflecting backend ownership. Each automatic action SHALL prepare at most twenty takes; larger sessions SHALL offer Continue preparation. Saved work SHALL survive browser closure. Start retries SHALL reuse a client request ID; a second tab SHALL display active operation progress from `409 authoring_active` rather than launching another call. Stale-revision, assistant-unavailable, cancelled, expired and failed states SHALL retain distinct next actions.

Effective choices SHALL be the primary review surface; final prompts, provenance and diagnostics SHALL be inspectable. Editing SHALL explain affected downstream work. Exact duplicate tuples SHALL be flagged for explicit review without removing deliberate repetitions.

Preparation SHALL stop at Review and SHALL NOT approve review, submit shots or start generation. Explicit review approval, submission and runner actions SHALL remain clearly distinguishable. A small reviewed test selection SHALL remain available before the full session.

#### Scenario: User resumes a partial session
- **WHEN** three of twelve takes were saved before interruption
- **THEN** reopening shows those results and offers preparation of the remaining work

#### Scenario: Another tab already owns authoring
- **WHEN** Prepare receives active-operation status from another tab
- **THEN** the UI adopts that operation view and offers status/cancel controls without another assistant call

#### Scenario: Session exceeds one batch
- **WHEN** twenty of forty takes finish
- **THEN** the UI offers Continue preparation without truncating the plan or regenerating the completed batch

#### Scenario: Automatic preparation finishes
- **WHEN** all requested takes are prepared
- **THEN** the user sees Review
- **AND** no review approval, shot submission or runner start occurs implicitly

#### Scenario: User edits an earlier automatic take
- **WHEN** an edit affects later ungenerated authoring
- **THEN** the UI explains which results need preparation again before saving the edit

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

Guided sessions SHALL offer an optional Saved look selector in the shared-state summary. Selecting a preset SHALL not introduce a mandatory additional initial input. Users SHALL see the distinction between appearance that stays fixed and clothing that may change. Plan clothing changes SHALL show a timeline preview, final stage and interval controls, while Keep clothing remains the default. Photo references SHALL be identified as extraction inputs rather than generation references.

#### Scenario: User creates a look from a photo
- **WHEN** a user selects From photo and explicitly requests extraction
- **THEN** visible descriptions and garments appear for editing
- **AND** Save requires review and explicit removal-order confirmation

#### Scenario: Saved look is used again
- **WHEN** a user selects an existing look in a new session
- **THEN** its appearance and outfit are available without re-uploading or retyping them

#### Scenario: User plans clothing changes
- **WHEN** the user opts into progression
- **THEN** a preview shows what remains worn for each affected take before applying
- **AND** constant appearance remains visibly separate

#### Scenario: User has no vision assistant
- **WHEN** the text assistant is configured but no explicit verified/declarative vision model exists
- **THEN** Extract is unavailable before image transmission and offers Configure vision
- **AND** the same editor still allows manual descriptions and garment creation

### Requirement: Resource drift has an explicit recovery action

Review and recovery SHALL expose stale prepared inputs and offer Refresh resources with the affected takes and the need to prepare/review again. Refresh SHALL use the dedicated CAS operation without asking users to edit creative fields. Stale results SHALL NOT appear approved or submittable. Missing translations SHALL link to existing readiness correction. Refresh SHALL NOT trigger assistant calls or generation; subsequent Prepare and Approve remain explicit.

#### Scenario: Ready take no longer matches authorized resources
- **WHEN** a user opens Review after a consumed translation changes
- **THEN** the UI explains why the prepared result is stale and offers Refresh resources
- **AND** refreshing preserves creative choices while showing the new revision and preparation required
