## MODIFIED Requirements

### Requirement: Session choices precede take variations

A resource session SHALL bind its character to the selected model and store a constant look, initial wardrobe and source selections. Source identity suggestions SHALL NOT replace the selected character. Each take SHALL expose its camera, framing, pose and expression choices and its effective wardrobe. Shared appearance, place and light SHALL remain constant within the session; changing those constants SHALL require a new session or an explicit revision before any take is generated.

For the normal guided resource workflow, the plan SHALL persist a normalized `authoring` block containing an `authoring_mode` of `automatic` or `manual`, an optional session brief of at most 2,000 characters, one exact scene-anchor revision selected from the plan's immutable resource triples, and one variation-policy entry for each of camera, framing, pose and expression. Guided creation SHALL accept between 1 and 20 takes inclusive.

The scene anchor SHALL be an exact `(library_key, source_id, content_digest)` triple that also appears in `selected_resources`, resolves to a stored ready non-deleted scene-defining revision, and remains authoritative across the session. It SHALL NOT be selected or replaced by the assistant. Advanced plans MAY retain additional selected resources under the existing resource-plan contract, but the normal flow SHALL NOT silently combine competing scene-defining resources.

A variation-policy entry SHALL declare either `vary` or `fixed`. A fixed entry SHALL persist its resolved value when known and SHALL record `value_origin` as `user` or `assistant`. Manual authoring SHALL require explicit user values for all fixed dimensions before deterministic preparation. Automatic authoring MAY persist an unresolved fixed value temporarily so that the assistant can establish it exactly once. An assistant-established fixed value SHALL be saved by plan CAS together with exact resolution provenance before any take preparation consumes it.

Session creation SHALL NOT require a configured prompt assistant. Both authoring modes SHALL create the same authoritative `resource-v1` draft shape, requested stable take IDs, scene anchor, fixed state, review gates and generation path. `automatic` SHALL remain a valid persisted mode even when no assistant is configured; in that case only synthesis is unavailable. `manual` SHALL make no assistant call and SHALL let the user provide the same closed take-choice fields directly before deterministic preparation.

Changing authoring mode, brief, scene anchor, variation mode or resolved fixed value SHALL be an explicit compare-and-swap plan revision and SHALL invalidate affected ungenerated preparations under existing revision rules. Once any take in the session has reached immutable generated state, scene anchor and variation-policy/fixed-value continuity state SHALL be frozen along with existing generated-session constants so an edit cannot contradict already generated output. Authoring mode or brief changes MAY remain possible for future ungenerated authoring only when existing invalidation rules can preserve generated snapshots unchanged.

#### Scenario: A resource suggests another character or outfit
- **WHEN** a selected source contradicts session identity or clothing
- **THEN** the conflict is shown before preparation and neither choice is silently overwritten

#### Scenario: A session uses one imported scene across many takes
- **WHEN** a normal resource session is authored from one ready non-deleted scene anchor and multiple takes are requested
- **THEN** every take retains that exact scene anchor and shared place/light state while unlocked camera, framing, pose and expression may vary

#### Scenario: Guided authoring bounds are invalid
- **WHEN** the requested take count is outside 1-20 or the brief exceeds 2,000 characters
- **THEN** validation fails before assistant work and before guided session/plan creation commits

#### Scenario: Manual session is created with no assistant configured
- **WHEN** the user chooses manual authoring on a system with no configured prompt assistant
- **THEN** the requested `resource-v1` draft and stable take IDs are created normally
- **AND** no assistant call is required to edit, prepare, review, submit or generate the manually completed takes

#### Scenario: Automatic mode has no assistant configured
- **WHEN** the user chooses automatic authoring but no prompt assistant is configured
- **THEN** the automatic draft remains valid and persisted
- **AND** automatic synthesis is reported as unavailable
- **AND** the same plan may later be synthesized after configuration or switched explicitly to manual through CAS

#### Scenario: Assistant establishes a fixed dimension
- **WHEN** automatic mode fixes a variation dimension but no user value was supplied and the assistant establishes the value
- **THEN** the value, `value_origin = assistant`, and exact resolution provenance are persisted by CAS before per-take preparation
- **AND** every take/retry in that authoritative scope reuses the same value

#### Scenario: Generated state freezes continuity choices
- **WHEN** at least one take has generated output
- **THEN** scene anchor and variation-policy/fixed-value continuity state cannot be changed in a way that would contradict that generated output
- **AND** existing generated snapshots remain immutable

### Requirement: Guided creation persists session and initial plan atomically

The normal guided `resource-v1` creation flow SHALL validate the guided inputs and create the session row plus its initial authoritative plan in one database transaction. The initial plan SHALL contain exactly the requested stable take IDs and complete authoring state before any assistant call. A validation or persistence failure SHALL leave no orphaned or partially initialized guided session. Existing legacy/general session creation behavior SHALL remain unchanged.

#### Scenario: Guided plan validation fails
- **WHEN** the initial scene anchor, authoring state, take count or selected resource set is invalid
- **THEN** guided creation fails atomically
- **AND** no standalone session row remains

#### Scenario: Guided creation succeeds
- **WHEN** all guided inputs are valid
- **THEN** the session and initial `resource-v1` plan become durable together
- **AND** any automatic synthesis starts only afterward against the persisted plan revision

### Requirement: Draft preparation survives interruption

The system SHALL persist the draft before long preparation begins and save completed take preparation incrementally. Reopening a draft SHALL recover completed work and identify incomplete work without regenerating completed takes automatically. Saving failures SHALL be visible. Any persisted authoring mode, brief, scene anchor, variation policy, fixed-value origin/provenance and resolved fixed values needed to continue the chosen authoring path SHALL survive the same round trip so recovery does not depend on transient browser state or assistant conversational memory.

#### Scenario: Browser closes during preparation
- **WHEN** the browser closes after three of twelve takes have been prepared automatically
- **THEN** reopening restores those three results and offers preparation of the remaining takes

#### Scenario: Browser closes during manual authoring
- **WHEN** the browser closes after a manual resource draft and some explicit take choices have been saved
- **THEN** reopening restores the same manual authoring mode, scene anchor, take IDs and saved take choices
- **AND** continuing the session does not require an assistant

#### Scenario: Browser closes after authoring intent was saved
- **WHEN** automatic authoring resumes after the page or application was closed
- **THEN** remaining takes are prepared from the persisted brief, scene anchor, variation policy and resolved fixed state associated with the current plan revision
- **AND** already completed ready/generated snapshots are not regenerated merely to reconstruct context
