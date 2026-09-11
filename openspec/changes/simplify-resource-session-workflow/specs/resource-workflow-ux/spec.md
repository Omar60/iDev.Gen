## Purpose

Let ordinary users turn selected mined resource files into a reviewed photographic session with few initial inputs, actionable readiness, optional assistant help and retained expert control.

## ADDED Requirements

### Requirement: Normal ingestion uses file selection and readable outcomes

The normal source-import UI SHALL use browser file selection without typed paths or library keys for valid declared sources with no historical ambiguity. Preview SHALL present new, updated, unchanged and needs-attention outcomes separately from readiness. Import SHALL require a current preview. Advanced compatibility targeting SHALL support legacy identities and supported non-envelope sources without source JSON editing.

Safe technical diagnostics SHALL remain expandable. Physical staging paths SHALL NOT be displayed. File/target changes and late responses SHALL NOT allow stale Import actions. Import SHALL NOT automatically translate, create a session or generate images.

#### Scenario: Normal source selection
- **WHEN** a user selects supported files with valid unambiguous declarations
- **THEN** preview and import are available without typing paths, keys or JSON

#### Scenario: Selection changes during preview
- **WHEN** an old preview response arrives after the selection changes
- **THEN** it cannot enable Import for the newer selection

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

The initial normal creation surface SHALL show character, scene, photo count and optional brief only as authoring inputs. Known character/scene selections SHALL carry forward. Photo count SHALL default to twelve and require an integer from 1 through 500 for guided creation; brief SHALL be limited to 2,000 characters. Automatic SHALL be the default when an assistant is configured; otherwise manual SHALL be the default with an action to configure an assistant.

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
- **THEN** manual editing, shared overrides and variation locks are available without another session type

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

The UI SHALL show completed, failed and remaining takes, with Prepare, Cancel and Resume actions reflecting backend ownership. Each automatic action SHALL prepare at most twenty takes; larger sessions SHALL offer Continue preparation. Saved work SHALL survive browser closure.

Effective choices SHALL be the primary review surface; final prompts, provenance and diagnostics SHALL be inspectable. Editing SHALL explain affected downstream work. Exact duplicate tuples SHALL be flagged for explicit review without removing deliberate repetitions.

Preparation SHALL stop at Review and SHALL NOT approve review, submit shots or start generation. Explicit review approval, submission and runner actions SHALL remain clearly distinguishable. A small reviewed test selection SHALL remain available before the full session.

#### Scenario: User resumes a partial session
- **WHEN** three of twelve takes were saved before interruption
- **THEN** reopening shows those results and offers preparation of the remaining work

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
- **WHEN** a photo cannot be analyzed automatically
- **THEN** the same editor allows manual descriptions and garment creation with a clear explanation
