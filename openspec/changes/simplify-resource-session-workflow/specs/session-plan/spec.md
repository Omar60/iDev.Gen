## MODIFIED Requirements

### Requirement: Session choices precede take variations

A resource session SHALL bind its character to the selected model and store a constant look, initial wardrobe and source selections. Source identity suggestions SHALL NOT replace the selected character. Each take SHALL expose its camera, framing, pose and expression choices and its effective wardrobe. Shared appearance, place and light SHALL remain constant within the session; changing those constants SHALL require a new session or an explicit revision before any take is generated.

For the normal guided resource workflow, the plan SHALL also persist the authoring intent required to resume the chosen authoring path: an `authoring_mode` of `automatic` or `manual`, an optional session brief, one exact scene-anchor revision selected from the plan's immutable resource triples, and a variation policy for camera, framing, pose and expression including any resolved fixed values. The scene anchor SHALL remain authoritative across the session and SHALL NOT be selected or replaced by the assistant. Advanced plans MAY retain additional selected resources under the existing resource-plan contract, but the normal flow SHALL NOT silently combine competing scene-defining resources.

Session creation SHALL NOT require a configured prompt assistant. Both authoring modes SHALL create the same authoritative `resource-v1` draft shape, requested stable take IDs, scene anchor, fixed state, review gates and generation path. `automatic` mode MAY use the configured assistant to fill unlocked take choices. `manual` mode SHALL make no assistant call and SHALL let the user provide the same closed take-choice fields directly before deterministic preparation.

Changing the persisted authoring mode, brief, scene anchor, variation mode or resolved fixed value SHALL be an explicit compare-and-swap plan revision and SHALL invalidate affected ungenerated preparations under the existing revision rules. If an assistant establishes a value for a fixed variation dimension in automatic mode, that resolved value SHALL be persisted authoritatively before per-take preparation proceeds so retries and later takes reuse it rather than asking the assistant to choose again.

#### Scenario: A resource suggests another character or outfit
- **WHEN** a selected source contradicts session identity or clothing
- **THEN** the conflict is shown before preparation and neither choice is silently overwritten

#### Scenario: A session uses one imported scene across many takes
- **WHEN** a normal resource session is authored from one ready scene anchor and multiple takes are requested
- **THEN** every take retains that exact scene anchor and shared place/light state while unlocked camera, framing, pose and expression may vary

#### Scenario: Manual session is created with no assistant configured
- **WHEN** the user chooses manual authoring on a system with no configured prompt assistant
- **THEN** the requested `resource-v1` draft and stable take IDs are created normally
- **AND** no assistant call is required to edit, prepare, review, submit or generate the manually completed takes

#### Scenario: Automatic mode has no assistant configured
- **WHEN** the user chooses automatic authoring but no prompt assistant is configured
- **THEN** automatic take synthesis is reported as unavailable
- **AND** the persisted draft remains valid and may be switched to manual authoring through an explicit plan revision

#### Scenario: Authoring intent changes
- **WHEN** the user changes authoring mode, session brief, scene anchor, variation mode or a resolved fixed value
- **THEN** the change is saved as a new authoritative plan revision and affected ungenerated preparation cannot remain silently valid

#### Scenario: Assistant establishes a fixed dimension
- **WHEN** automatic mode fixes a variation dimension but no user value was supplied and the assistant establishes the value
- **THEN** that value is persisted before per-take preparation and every take in the requested scope reuses it

### Requirement: Draft preparation survives interruption

The system SHALL persist the draft before long preparation begins and save completed take preparation incrementally. Reopening a draft SHALL recover completed work and identify incomplete work without regenerating completed takes automatically. Saving failures SHALL be visible. Any persisted authoring mode, brief, scene anchor, variation policy and resolved fixed values needed to continue the chosen authoring path SHALL survive the same round trip so recovery does not depend on transient browser state or assistant conversational memory.

#### Scenario: Browser closes during preparation
- **WHEN** the browser closes after three of twelve takes have been prepared automatically
- **THEN** reopening restores those three results and offers preparation of the remaining takes

#### Scenario: Browser closes during manual authoring
- **WHEN** the browser closes after a manual resource draft and some explicit take choices have been saved
- **THEN** reopening restores the same manual authoring mode, scene anchor, take IDs and saved take choices
- **AND** continuing the session does not require an assistant

#### Scenario: Browser closes after authoring intent was saved
- **WHEN** automatic authoring resumes after the page or application was closed
- **THEN** the remaining takes are prepared from the persisted brief, scene anchor and variation policy associated with the current plan revision
- **AND** already completed ready/generated snapshots are not regenerated merely to reconstruct context
