## ADDED Requirements

### Requirement: Guided creation persists a compatible authoring plan atomically

Guided creation SHALL validate a selected character/workflow, ready stored rooms scene anchor, positive integer photo count, optional brief of at most 2,000 characters and complete variation policy. The exact anchor triple SHALL also appear in selected_resources. Session and initial plan SHALL become durable atomically with stable take IDs before assistant calls. Failure SHALL leave no orphan session.

The normalized authoring block SHALL use authoring.mode with automatic or manual, brief, scene_anchor, variation_policy and shared-state resolution metadata. The default policy SHALL vary camera, framing, pose and expression. A fixed dimension SHALL require a non-empty user value and SHALL reject conflicting explicit take choices. Automatic batch limits SHALL NOT impose a twenty-take plan limit. Older/expert plans without authoring metadata SHALL retain existing behavior without silent migration.

#### Scenario: Guided creation fails
- **WHEN** validation or initial plan persistence fails
- **THEN** no orphan session remains and no assistant call starts

#### Scenario: Forty-photo session
- **WHEN** valid guided inputs request forty photos
- **THEN** the plan persists forty stable take IDs
- **AND** automatic preparation can proceed in batches of at most twenty

#### Scenario: Existing expert plan is reopened
- **WHEN** a saved plan has no authoring metadata
- **THEN** its existing resources, take count and preparation behavior remain usable without a forced anchor or mode migration

#### Scenario: Fixed policy conflicts with a take
- **WHEN** an explicit take value differs from its fixed policy value
- **THEN** validation reports the conflict rather than silently overwriting either value

### Requirement: Shared choices have one authoritative accepted state

The selected model SHALL own character identity. Authorized scene descriptions SHALL supply scene context without heuristic decomposition. Existing plan look and initial wardrobe SHALL remain authoritative, with explicit user values taking precedence. Empty additional constraints SHALL NOT erase source descriptions.

Optional assistant suggestions for missing look/wardrobe SHALL be reviewed and accepted through plan CAS before take preparation consumes them. Persist exact suggestion input/output, accepted values, origins and user edits. Users SHALL be able to keep empty additional constraints explicitly. Manual mode SHALL support the same shared-state decisions without an assistant. Wardrobe progression SHALL remain an explicit scoped user change.

#### Scenario: Shared suggestions await acceptance
- **WHEN** the assistant proposes look or wardrobe values
- **THEN** they are shown for acceptance or editing
- **AND** they do not silently replace effective plan values or enter take preparation

#### Scenario: User edits a suggested value
- **WHEN** an operator changes a shared suggestion before acceptance
- **THEN** persistence records the assistant suggestion and user edit accurately through CAS

#### Scenario: No additional look is requested
- **WHEN** the user keeps an empty extra look or wardrobe constraint
- **THEN** the plan does not invent a value or remove the source description

### Requirement: Automatic authoring edits invalidate downstream dependencies

Automatic preparation SHALL consume takes in stable order and record predecessor snapshot identities/revisions with its exact bounded context. Brief, anchor, effective shared-state or policy changes SHALL invalidate affected ungenerated automatic preparations. Editing, removing or reordering a take SHALL conservatively invalidate later ungenerated automatic preparations from the earliest changed position, in addition to existing direct-input and wardrobe rules. Earlier unaffected results SHALL remain reusable. Manual work SHALL retain existing input-based invalidation.

Mode-only changes SHALL revoke review and cancel old in-flight authoring while retaining completed choices with original provenance. Generated/queued snapshots SHALL remain immutable. Generated-state continuity protection SHALL include scene anchor and variation policy; brief changes SHALL affect only future ungenerated work without rewriting historical evidence.

#### Scenario: Third take changes
- **WHEN** take three changes after twelve automatic takes were prepared
- **THEN** take three and later ungenerated automatic results require preparation again
- **AND** unaffected earlier snapshots and generated history remain intact

#### Scenario: Take order changes
- **WHEN** a take moves earlier in the plan
- **THEN** affected ungenerated automatic work from the earliest changed position is invalidated

#### Scenario: Mode switches after preparation
- **WHEN** automatic authoring changes explicitly to manual
- **THEN** review is revoked and in-flight automatic output is cancelled
- **AND** completed choices retain assistant provenance rather than becoming falsely manual

#### Scenario: Generated continuity cannot change
- **WHEN** an edit would change scene anchor or fixed continuity policy after generated state exists
- **THEN** it is refused under continuity freeze rules without rewriting history

### Requirement: Authoring operations have exclusive recoverable ownership

The system SHALL prevent concurrent shared-state suggestion and automatic take-preparation operations for the same session from launching duplicate work. It SHALL track operation ownership, plan revision and progress and refuse stale or expired owners' writes. Cancellation SHALL stop new calls and discard late responses. Plan changes SHALL revoke old operation ownership.

Completed take snapshots SHALL persist incrementally. Failure SHALL report completed, failed and remaining work. Reopening SHALL recover progress and permit resuming incomplete work after abandoned ownership expires. Ready/generated snapshots SHALL NOT be regenerated merely to resume. Retrying an unpersisted remote response after a crash MAY make another assistant call; exactly-once remote billing SHALL NOT be promised.

#### Scenario: Two tabs prepare simultaneously
- **WHEN** a second request arrives while the same session is being authored
- **THEN** it receives active-operation status rather than launching a duplicate assistant call

#### Scenario: Cancellation during a remote call
- **WHEN** the user cancels while a response is in flight
- **THEN** that late response is not persisted and no next take is scheduled

#### Scenario: Crash after three completed takes
- **WHEN** a twelve-take operation is abandoned after three results persist
- **THEN** recovery reuses those three and resumes remaining work after ownership recovery

#### Scenario: Plan edit races an assistant response
- **WHEN** output returns for an older plan revision or expired owner
- **THEN** it cannot overwrite current state

#### Scenario: Partial failure
- **WHEN** one take fails in a batch
- **THEN** completed results survive and the failed and remaining takes are visible for retry

### Requirement: Selected looks are snapshotted into session state

Applying a reusable look SHALL copy its exact appearance and garment definitions/order into the session plan with preset identity/version/digest and origin metadata. Session prompts SHALL use the accepted snapshot, not mutable live library content. Explicit existing choices SHALL require a Replace or Keep decision. Later library edits SHALL NOT change existing sessions. Reapplying a newer version SHALL require explicit CAS, normal invalidation and generated-constant guards.

An appearance-only preset SHALL leave wardrobe unchanged; an absent outfit SHALL NOT imply a garment-free state. Appearance SHALL remain constant while clothing SHALL be represented through initial_wardrobe and scoped wardrobe changes. Removable garments SHALL NOT be automatically copied into constant appearance.

#### Scenario: One look is reused by two sessions
- **WHEN** a preset is selected for two different characters or scenes
- **THEN** each session receives an independent snapshot without changing character identity or scene

#### Scenario: Library changes after selection
- **WHEN** a preset receives a new version
- **THEN** existing session choices and prepared prompts remain unchanged until explicit permissible reapplication

#### Scenario: Existing shared choices conflict with a look
- **WHEN** a selected preset would replace explicit look or wardrobe values
- **THEN** the user must choose Replace or Keep before the plan changes

### Requirement: Outfit progression requires explicit preview and scoped application

Resource sessions SHALL keep clothing constant by default. Users SHALL be able to request a progression from snapshotted ordered garments, choose the final stage and take interval, and review every resulting state. The system SHALL derive stages using authored order, offering moved-aside wording only under existing final-garment arc semantics; it SHALL NOT guess the order or invent garments.

For K chosen ordered stages and M consecutive takes, automatic distribution SHALL require M >= K when K > 1 and assign stage floor(i * (K - 1) / (M - 1)) at zero-based interval offset i. K = 1 SHALL keep clothing constant. Too few takes SHALL require a wider interval or fewer selected stages. Initial wardrobe SHALL apply before the interval and the final selected stage SHALL carry afterward.

Explicit application SHALL atomically materialize initial_wardrobe and minimal from_here events using stable take IDs through plan CAS. Existing events SHALL require a reviewed replacement/merge without duplicates; this_take overrides SHALL remain supported. Approved events SHALL remain authoritative after reordering or adding takes, without silently redistributing a saved progression. Existing wardrobe resolution, downstream invalidation and generated-history rules SHALL apply.

#### Scenario: Outfit stays unchanged by default
- **WHEN** a saved outfit is selected without requesting progression
- **THEN** every take inherits its initial clothing

#### Scenario: User approves a staged progression
- **WHEN** a user confirms garment order, final stage and sufficient take interval
- **THEN** preview shows every state before an explicit atomic application
- **AND** accepted changes are stored as existing scoped events rather than a live automatic rule

#### Scenario: Too few takes for stages
- **WHEN** the interval has fewer takes than selected stages
- **THEN** the user must widen it or select fewer stages without silent omission

#### Scenario: Preview conflicts with existing wardrobe events
- **WHEN** a proposed progression overlaps saved changes
- **THEN** explicit replacement/merge review is required before CAS save

#### Scenario: Takes are reordered after approval
- **WHEN** a saved progression's takes are reordered
- **THEN** existing stable-ID scope rules recompute effective wardrobe and invalidate affected work
- **AND** no new distribution is silently applied
