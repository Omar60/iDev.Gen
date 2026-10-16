## MODIFIED Requirements

### Requirement: Guided creation persists a compatible authoring plan atomically

`POST /api/sessions/guided` SHALL accept a client `request_id`, selected character, optional Advanced workflow override, ready stored rooms scene anchor, integer photo count from 1 through 500, optional brief of at most 2,000 characters, authoring mode, complete variation policy and explicit look/initial-wardrobe overrides. The server SHALL resolve the effective workflow from the explicit override or the character's current default, in that order. The workflow SHALL exist and satisfy the existing resource preparation/submission compatibility checks. No default and no override SHALL fail with `422 workflow_required` before persistence and identify both available remedies. The exact anchor triple SHALL also appear in selected_resources.

The initial authoring plan SHALL persist a server-owned workflow binding containing the effective workflow ID, kind, canonical graph digest and canonical node-map digest. The session SHALL persist that effective workflow ID. The binding SHALL participate in preparation evidence, the automatic creative-input digest and copy-forward equality; the resource/adaptation-only effective_resource_input_digest SHALL remain unchanged in scope. A later character-default change SHALL NOT affect the session. A missing or content-changed bound workflow SHALL block new preparation, approval and submission with `409 workflow_changed`; authoring-v1 workflow changes after creation SHALL require another session. Existing plans without authoring metadata SHALL retain established workflow behavior.

The server SHALL treat guided `request_id` as globally unique, canonicalize and hash the validated request excluding it, then persist one guided-request record, session, initial plan, stable take IDs and stored success response in one serialized transaction before assistant calls. A new creation SHALL return `201` with session ID, revision one and plan. The same request ID and digest SHALL return that stored result with `200` after a lost response or concurrent retry. The same request ID with different normalized content SHALL return `409 idempotency_conflict`. Failure SHALL leave no request record or orphan session; concurrent calls using one request ID SHALL create at most one session/plan. Errors SHALL use a stable `detail.code` and readable `detail.message`.

The normalized authoring block SHALL use authoring.mode with automatic or manual, brief, scene_anchor, variation_policy and shared-state resolution metadata. The default policy SHALL vary camera, framing, pose and expression. A fixed dimension SHALL require a non-empty user value and SHALL reject conflicting explicit take choices. Automatic batch limits SHALL NOT impose a twenty-take plan limit. Older/expert plans without authoring metadata SHALL retain existing behavior without silent migration.

Full Auto SHALL extend this endpoint through the exact saved-look and authorization contract in resource-full-auto. Optional saved_look SHALL contain exactly look_key, version and content_digest; the server SHALL verify and copy that immutable version during the creation transaction. A Full Auto request SHALL persist the resolved look, materialized progression, evidenced coverage and authorization/run receipt in that same transaction. It SHALL NOT allocate an empty-look session and apply a live latest version later. Non-Full-Auto saved-look creation SHALL use the exact snapshot without automatic progression and SHALL reject competing nonempty overrides before writes, directing the user to the existing manual post-creation Keep/Replace flow. Omitted new fields SHALL preserve previous request-digest and response shapes.

#### Scenario: Guided creation fails
- **WHEN** validation or initial plan persistence fails
- **THEN** no idempotency record or orphan session remains and no assistant call starts

#### Scenario: Character has no effective workflow
- **WHEN** neither the character default nor an Advanced override supplies a compatible workflow
- **THEN** creation fails before writes with actions to assign a default or choose an override

#### Scenario: Creation response is lost
- **WHEN** two concurrent requests or a later retry use the same request ID and normalized body
- **THEN** they resolve to one stored session/plan result without duplicate allocation

#### Scenario: Creation token is reused for another body
- **WHEN** a request ID already bound to one normalized creation is sent with different content
- **THEN** creation returns an idempotency conflict and writes nothing

#### Scenario: Bound workflow changes after creation
- **WHEN** the stored workflow graph or node map no longer matches the frozen binding
- **THEN** new preparation, approval and submission fail without reinterpreting or copying prior work
- **AND** the user is directed to create another session

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

#### Scenario: Exact look selected before creation
- **WHEN** a user authorizes Full Auto with an eligible exact look/profile revision
- **THEN** the first persisted plan already contains its verified snapshot, effective choices, stable-ID progression and coverage
- **AND** a newer library version cannot change that plan or an identical request replay

#### Scenario: Look identity is stale or forged
- **WHEN** the supplied look version does not exist or its digest differs
- **THEN** creation fails before allocating a session, request receipt, run or assistant call

### Requirement: Shared choices have one authoritative accepted state

The selected model SHALL own character identity. Authorized scene descriptions SHALL supply scene context without heuristic decomposition. Existing plan look and initial wardrobe SHALL remain authoritative, with explicit user values taking precedence. Empty additional constraints SHALL NOT erase source descriptions.

Optional assistant suggestions for missing look/wardrobe SHALL be reviewed and accepted through plan CAS before take preparation consumes them. Persist exact suggestion input/output, accepted values, origins and user edits. Users SHALL be able to keep empty additional constraints explicitly. Manual mode SHALL support the same shared-state decisions without an assistant. Wardrobe progression SHALL remain an explicit scoped user change.

A saved-look automation profile accepted before Full Auto creation SHALL count as an explicit scoped decision for its permanent-appearance projection, coverage and all-stage progression. This exception SHALL NOT accept pending assistant suggestions, silently adapt source prose or change other shared choices. Full Auto SHALL record the exact profile and authorization evidence; manual shared suggestions retain their existing acceptance flow.

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

#### Scenario: Eligible Full Auto profile supplies shared choices
- **WHEN** the user authorizes the selected exact saved-look profile
- **THEN** its accepted fixed appearance and initial wardrobe enter the initial plan without a second shared-state acceptance action

### Requirement: Selected looks are snapshotted into session state

Applying a reusable look SHALL copy its exact appearance and garment definitions/order into the session plan with preset identity/version/digest and origin metadata. Session prompts SHALL use the accepted snapshot, not mutable live library content. Explicit existing choices SHALL require a Replace or Keep decision. Later library edits SHALL NOT change existing sessions. Reapplying a newer version SHALL require explicit CAS, normal invalidation and generated-constant guards.

An appearance-only preset SHALL leave wardrobe unchanged; an absent outfit SHALL NOT imply a garment-free state. Appearance SHALL remain constant while clothing SHALL be represented through initial_wardrobe and scoped wardrobe changes. Removable garments SHALL NOT be automatically copied into constant appearance.

Full Auto SHALL retain the complete look snapshot as source evidence while using the profile's explicitly reviewed permanent-appearance projection as the effective look. A projection different from snapshot appearance SHALL use user origin with separate profile evidence, never falsely claim saved_look equality. Progressive garment/accessory clauses SHALL have one owner and SHALL NOT also enter constant appearance. Unknown or contradictory ownership SHALL block Full Auto until corrected. Manual application without a profile SHALL retain existing snapshot semantics. Explicit manual profile application SHALL use the same projection, provenance, CAS, invalidation and post-link freeze rules, but SHALL retain manual progression preview, review, submission and execution controls. Existing profile-free sessions and historical prompts SHALL NOT be rewritten.

#### Scenario: One look is reused by two sessions
- **WHEN** a preset is selected for two different characters or scenes
- **THEN** each session receives an independent snapshot without changing character identity or scene

#### Scenario: Library changes after selection
- **WHEN** a preset receives a new version
- **THEN** existing session choices and prepared prompts remain unchanged until explicit permissible reapplication

#### Scenario: Existing shared choices conflict with a look
- **WHEN** a selected preset would replace explicit look or wardrobe values
- **THEN** the user must choose Replace or Keep before the plan changes

#### Scenario: Appearance mentions a removable accessory
- **WHEN** an accepted profile assigns the appearance clause to the same progressive accessory key
- **THEN** the constant projection omits that clause and the resolved wardrobe alone controls its presence
- **AND** the complete original snapshot remains inspectable

### Requirement: Outfit progression requires explicit preview and scoped application

Resource sessions outside explicitly authorized Full Auto SHALL keep clothing constant by default. Users SHALL be able to request a progression from snapshotted ordered garments, choose the final stage and take interval, and review every resulting state. The system SHALL derive stages using authored order, offering moved-aside wording only under existing final-garment arc semantics; it SHALL NOT guess the order or invent garments.

For K chosen ordered stages and M consecutive takes, automatic distribution SHALL require M >= K when K > 1 and assign stage floor(i * (K - 1) / (M - 1)) at zero-based interval offset i. K = 1 SHALL keep clothing constant. Too few takes SHALL require a wider interval or fewer selected stages. Initial wardrobe SHALL apply before the interval and the final selected stage SHALL carry afterward.

Explicit manual application or recorded Full Auto authorization SHALL atomically materialize initial_wardrobe and minimal from_here events using stable take IDs through plan CAS. Existing events SHALL require a reviewed replacement/merge without duplicates; this_take overrides SHALL remain supported. Approved events SHALL remain authoritative after reordering or adding takes, without silently redistributing a saved progression. Existing wardrobe resolution, downstream invalidation and generated-history rules SHALL apply.

Full Auto SHALL use every derived stage from the saved ordered outfit, including the existing final-item moved-aside stage when present, across the entire ordered take interval. The single authorization SHALL replace the mandatory manual preview confirmation only for this deterministic all-stage policy; the timeline SHALL remain inspectable. N takes for K stages SHALL use the distribution above. N < K SHALL fail before creation without stage omission. An appearance-only look SHALL not imply a zero-garment stage. Resuming SHALL reuse materialized events, never redistribute or apply them again.

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

#### Scenario: Full Auto distributes all stages
- **WHEN** Full Auto requests twelve takes for five saved stages
- **THEN** stage positions are 0,0,0,1,1,1,2,2,2,3,3,4 with first and final stages at the interval endpoints
- **AND** no preview confirmation or intermediate progression action is required

#### Scenario: Full Auto has too few takes
- **WHEN** Full Auto requests fewer takes than derived stages
- **THEN** preflight identifies the minimum count and offers a count change or manual custom progression without allocating a session

### Requirement: Server-owned state changes through explicit operations

Generic plan saves SHALL only echo workflow_binding, evidence, look_snapshot and wardrobe_progression unchanged. Legitimate mutation SHALL use explicit compare-and-swap operations: accept a server-issued shared suggestion with user edits; apply a server-verified preset key/version; or apply a server-issued progression preview with reviewed merge/replace choices. Each SHALL validate current revision and generation guards, derive the resulting server-owned content and persist atomically.

For generic user edits of effective look/initial_wardrobe strings, the server SHALL set the corresponding origin to user with null evidence_id, keeping old evidence historical; clients SHALL NOT fabricate provenance. Read normalization SHALL preserve empty take arrays and missing/invalid IDs for authoritative validation rather than inventing take-001 or other content. Stable IDs SHALL be allocated only by guided creation or explicit Add take.

Atomic guided Full Auto creation SHALL be an additional explicit authorized operation for verified snapshot/progression construction and derived coverage. It SHALL use the same validation and canonical derivation rules as manual application. Generic saves SHALL NOT acquire permission to forge a profile, authorization or server-derived coverage evidence. An authorized recovery edit SHALL use CAS, cancel old ownership and invalidate dependent work before continuation.

#### Scenario: Generic save replaces snapshot
- **WHEN** a caller inserts, removes or changes a server-owned block through generic plan save
- **THEN** it is rejected and the caller must use the appropriate explicit operation

#### Scenario: User applies a saved preset
- **WHEN** the explicit operation receives a valid preset key/version and current CAS revision
- **THEN** the server loads verified content and applies it subject to conflict and freeze rules

#### Scenario: Persisted plan has no takes
- **WHEN** the frontend reads an empty or invalid take list
- **THEN** normalization does not create a synthetic take
- **AND** explicit creation/editing or validation handles the state

#### Scenario: Client forges Full Auto evidence through a generic save
- **WHEN** a client sends changed server-owned authorization, ownership or coverage evidence
- **THEN** the save is refused without plan, preparation, approval or shot writes

### Requirement: Guided canvas override extends the closed creation request

Superseding the exact field list in `simplify-resource-session-workflow/design.md` §7, `POST /api/sessions/guided` SHALL retain all established accepted keys and add optional paired `width` and `height` dimensions, plus the explicitly specified saved_look and full_auto objects; all other unknown keys remain rejected. If supplied, both SHALL be strict JSON integers, at least 8 and divisible by 8; a partial pair, null, coercible value, zero, or negative value SHALL be rejected before writes. If omitted, the new session SHALL inherit the character's current effective dimensions. Supplied values SHALL be stored only in the new session settings and SHALL NOT mutate character settings.

Explicitly supplied dimensions SHALL participate in the normalized request digest used by guided `request_id` replay. When both are omitted, normalization SHALL preserve the established pre-override digest shape. The same request ID and normalized body SHALL return the stored response; reuse with different normalized content, including different dimensions, SHALL return `409 idempotency_conflict` without writes.

The guided UI SHALL leave the override disabled by default. When enabled, its initial custom dimensions SHALL be 768×1360. These are input defaults, not a server canvas default or a rendering guarantee.

#### Scenario: Canvas dimensions are omitted
- **WHEN** guided creation omits both canvas dimensions
- **THEN** the session inherits the character's current effective dimensions without changing the character

#### Scenario: Canvas override is valid
- **WHEN** guided creation supplies width and height as positive integer multiples of 8
- **THEN** the new session stores that pair without changing character settings
- **AND** the supplied pair participates in request-ID replay identity

#### Scenario: Canvas override is partial or invalid
- **WHEN** guided creation supplies only one dimension or either value is not a positive integer multiple of 8
- **THEN** it returns a validation error before creating a request record or session

#### Scenario: Canvas retry changes dimensions
- **WHEN** an existing request ID is retried with different width or height
- **THEN** creation returns `409 idempotency_conflict` and writes nothing

### Requirement: Per-take wardrobe coverage is user-authored plan state

A resource-v1 take MAY store optional `wardrobe_coverage` as a string of at most 2,000 characters without disallowed control characters. The value SHALL remain attached to its stable take ID through reorder and save normalization. The application SHALL NOT infer body areas from free-text wardrobe, takes or resources. As an explicit replacement of the unconditional user-only contract, Full Auto and explicit manual profile application SHALL also permit server-derived coverage from an exact accepted structured automation profile and materialized stage. It SHALL record derived origin, profile/stage/compiler identity and text digest separately from the closed authoring block. Automatic creative writers SHALL NOT request or replace coverage of either origin. Missing or blank coverage SHALL preserve the existing effective-state shape.

Plan edits SHALL use the existing expected-revision compare-and-swap. Changing coverage SHALL invalidate preparation for the affected ungenerated take and SHALL invalidate downstream automatic work when its dependencies require it. Unaffected verified snapshots MAY copy forward. Generated snapshots and linked shots SHALL remain unchanged as historical records.

Derived coverage SHALL satisfy the same text limits and CAS/invalidation rules as manual text. Manual edits SHALL remove the affected derived claim and suspend Full Auto until the changed scope is accepted; generated evidence SHALL remain immutable. Coverage/profile evidence SHALL participate in preparation validation and verified copy-forward. Missing stage facts SHALL block Full Auto, not produce inferred body-region statements. Old manual plans with absent coverage SHALL retain their existing shape.

#### Scenario: Coverage follows its take
- **WHEN** a take with coverage is reordered and the plan is saved
- **THEN** the coverage remains attached to that take's stable ID

#### Scenario: Coverage changes after preparation
- **WHEN** coverage changes in a new plan revision
- **THEN** affected ungenerated preparation is invalidated and unrelated verified snapshots may copy forward
- **AND** existing generated snapshots and linked shots remain unchanged

#### Scenario: Coverage is absent or blank
- **WHEN** a take has no coverage or only whitespace coverage
- **THEN** no coverage value is added to effective state

#### Scenario: Coverage is compiled without per-take entry
- **WHEN** an authorized profile has sufficient structured facts for every selected stage
- **THEN** creation assigns deterministic coverage to each stable take with server-owned provenance
- **AND** the creative writer cannot overwrite it

#### Scenario: Coverage facts are incomplete
- **WHEN** a garment or baseline has unknown applicable coverage or inventory completeness is unconfirmed
- **THEN** Full Auto reports the missing profile facts before creation rather than asserting uncovered regions

### Requirement: Operational disablement protects authoring metadata across downgrade

With `resource_planning_enabled=false`, the system SHALL return `503` for new browser-selection mutations, translation proposal/application, guided creation, authoring plan saves, suggestion start/resume/accept, automatic/manual authoring preparation, adaptation writes, dependency refresh, review approval, saved-look create/edit/import, photo stage/extract/save, preset application and progression application. Existing translation write gates SHALL remain. Selection/operation status and cancellation, resource/session/plan/look/review/recovery reads and photo preview SHALL remain available. Existing path-backed resource import and legacy garment/outfit import SHALL retain their prior flag behavior.

Disabling while a remote call is in flight SHALL prevent another call and SHALL make the fenced response transaction discard output and cancel the operation. Outside Full Auto ownership and policy approval, already ready and authoritatively approved snapshots SHALL retain existing idempotent Submit and runner execution behavior; disablement SHALL NOT permit new approval or preparation.

For databases without Full Auto metadata, before starting a rollback-compatible older binary, the operator SHALL create and verify the existing SQLite backup, disable resource planning in persisted configuration or the target process environment, cancel or observe terminal active operations, verify disabled state, and stop the current process cleanly. The older binary SHALL run with the flag disabled so generic plan saves cannot drop unknown authoring metadata. A binary predating the flag SHALL NOT open the upgraded database for writes; rollback SHALL restore the verified pre-upgrade backup instead. Re-enabling writes SHALL require a binary that understands the closed authoring schema.

#### Scenario: New mutation is attempted while disabled
- **WHEN** any newly introduced authoring, selection, translation or look write is requested with the feature flag off
- **THEN** it fails with `503` before remote calls or durable writes

#### Scenario: Approved prepared work exists before disablement
- **WHEN** a ready snapshot outside Full Auto ownership and policy approval was already authoritatively approved before the flag was disabled
- **THEN** existing idempotent submission and runner execution remain available
- **AND** no new work can be prepared or approved

#### Scenario: Compatible downgrade opens an authoring plan
- **WHEN** a database without Full Auto metadata follows the verified backup/disable/stop procedure before launching a rollback-compatible older binary
- **THEN** plans remain inspectable while plan mutations are blocked from dropping unknown metadata

For Full Auto metadata, disablement SHALL prevent new automatic approval, submission, start and resume even for already policy-approved snapshots; reads, Cancel and safe settlement of dispatched attempts SHALL remain available. Transfer to manual SHALL require enabled planning and explicit current human review under normal gates, not reuse policy approval to bypass disablement. The approved-work scenario above applies only outside Full Auto ownership/policy approval. The compatible-downgrade scenario above applies only to databases without Full Auto metadata.

Rollback of a Full Auto deployment SHALL preserve the upgraded database/images and use a verified pre-upgrade backup restored to a separate data/config directory. The rollback preflight SHALL refuse absent backups, unresolved remote attempts and paths resolving to the upgraded directory, and SHALL NOT launch the older binary on refusal. Older binaries SHALL never be assumed to honor a newly introduced schema marker. Direct old-binary writes to the upgraded database SHALL be unsupported.

#### Scenario: Policy-approved Full Auto work exists before disablement
- **WHEN** Full Auto has approved or linked targets when resource planning is disabled
- **THEN** it cannot automatically submit or start them and already dispatched work may settle safely
- **AND** existing manual submission behavior outside Full Auto remains unchanged

#### Scenario: Older release is requested after Full Auto upgrade
- **WHEN** the operator prepares rollback with Full Auto metadata present
- **THEN** preflight requires settled remote work and a verified isolated pre-upgrade restore rather than in-place older-binary writes
