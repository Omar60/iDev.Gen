## ADDED Requirements

### Requirement: Guided creation persists a compatible authoring plan atomically

`POST /api/sessions/guided` SHALL accept a client `request_id`, selected character, optional Advanced workflow override, ready stored rooms scene anchor, integer photo count from 1 through 500, optional brief of at most 2,000 characters, authoring mode, complete variation policy and explicit look/initial-wardrobe overrides. The server SHALL resolve the effective workflow from the explicit override or the character's current default, in that order. The workflow SHALL exist and satisfy the existing resource preparation/submission compatibility checks. No default and no override SHALL fail with `422 workflow_required` before persistence and identify both available remedies. The exact anchor triple SHALL also appear in selected_resources.

The initial authoring plan SHALL persist a server-owned workflow binding containing the effective workflow ID, kind, canonical graph digest and canonical node-map digest. The session SHALL persist that effective workflow ID. The binding SHALL participate in preparation evidence, the automatic creative-input digest and copy-forward equality; the resource/adaptation-only effective_resource_input_digest SHALL remain unchanged in scope. A later character-default change SHALL NOT affect the session. A missing or content-changed bound workflow SHALL block new preparation, approval and submission with `409 workflow_changed`; authoring-v1 workflow changes after creation SHALL require another session. Existing plans without authoring metadata SHALL retain established workflow behavior.

The server SHALL treat guided `request_id` as globally unique, canonicalize and hash the validated request excluding it, then persist one guided-request record, session, initial plan, stable take IDs and stored success response in one serialized transaction before assistant calls. A new creation SHALL return `201` with session ID, revision one and plan. The same request ID and digest SHALL return that stored result with `200` after a lost response or concurrent retry. The same request ID with different normalized content SHALL return `409 idempotency_conflict`. Failure SHALL leave no request record or orphan session; concurrent calls using one request ID SHALL create at most one session/plan. Errors SHALL use a stable `detail.code` and readable `detail.message`.

The normalized authoring block SHALL use authoring.mode with automatic or manual, brief, scene_anchor, variation_policy and shared-state resolution metadata. The default policy SHALL vary camera, framing, pose and expression. A fixed dimension SHALL require a non-empty user value and SHALL reject conflicting explicit take choices. Automatic batch limits SHALL NOT impose a twenty-take plan limit. Older/expert plans without authoring metadata SHALL retain existing behavior without silent migration.

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

Automatic preparation SHALL consume takes in stable order and record predecessor snapshot identities/revisions with its exact bounded context. Brief, anchor, workflow binding, effective shared-state or policy changes SHALL invalidate affected ungenerated automatic preparations. Editing, removing or reordering a take SHALL conservatively invalidate later ungenerated automatic preparations from the earliest changed position, in addition to existing direct-input and wardrobe rules. Earlier unaffected ready results SHALL be reusable only through the verified current-revision copy-forward contract below. Manual work SHALL retain existing input-based invalidation.

Mode-only changes SHALL revoke review and cancel old in-flight authoring while retaining completed choices with original provenance. Generated/queued snapshots SHALL remain immutable. Generated-state continuity protection SHALL include scene anchor, workflow binding, variation policy and the complete look_snapshot (including null versus non-null); brief changes SHALL affect only future ungenerated work without rewriting historical evidence.

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
- **WHEN** an edit would change scene anchor, workflow binding or fixed continuity policy after generated state exists
- **THEN** it is refused under continuity freeze rules without rewriting history

### Requirement: Authoring operations have exclusive recoverable ownership

The system SHALL prevent concurrent shared-state suggestion and automatic take-preparation operations for the same session from launching duplicate work. Each persisted operation SHALL have opaque operation ID, client request ID, session ID, plan revision, kind (`shared_suggestions` or `prepare_takes`), canonical request digest, state, monotonic fencing token, lease expiry, ordered targets/completed/remaining references, nullable failed item/error, nullable result and timestamps. The client request ID SHALL be unique within the session and MAY be reused in another session. Non-terminal states SHALL be `active` and `cancel_requested`; terminal states SHALL be `succeeded`, `failed`, `cancelled` and `expired`. At most one non-terminal operation SHALL exist per session across both kinds.

`POST /api/sessions/{session_id}/plan/authoring/operations` SHALL start work from request ID, expected revision, kind and kind-valid targets. It SHALL return `202` for a new active operation and `200` for an identical active or terminal replay. `GET .../operations/{operation_id}` SHALL return current state/progress. `POST .../{operation_id}/cancel` SHALL return `202` while an in-flight result must be discarded or `200` once terminal. `POST .../{operation_id}/resume` SHALL return `202` with a new fenced lease or `200` when already active/succeeded. A succeeded shared-suggestion operation SHALL be accepted only through `POST .../{operation_id}/accept` with expected revision and reviewed values; identical acceptance SHALL replay its stored plan result.

The public operation view SHALL expose operation/session/revision/kind/state/timestamps, nullable lease expiry, progress, nullable result/error and `can_cancel`/`can_resume`, but SHALL NOT expose the owner or fencing token. Progress SHALL identify requested, ordered completed, nullable failed take/readable error and ordered remaining items. A different active request SHALL return `409 authoring_active` with that view; stale plan, idempotency conflict, missing assistant and malformed targets SHALL be distinguished. Start/resume/accept SHALL return `503` while resource planning is disabled; status and cancellation SHALL remain available.

The lease SHALL be ten minutes and SHALL be renewed only by the backend immediately before a remote call and before scheduling another item. No transaction SHALL span a remote call. Before accepting each response, one transaction SHALL verify matching operation/fencing token, active/unexpired ownership, unchanged plan revision/request inputs and current resource/workflow validity, then persist result and progress together. A failed check SHALL discard the response. Cancellation SHALL stop new calls and discard late responses. Plan changes SHALL fence and cancel old ownership.

Completed take snapshots SHALL persist incrementally. Failure SHALL terminate with completed, one failed item and remaining work. Resume SHALL retry from the failed item without regenerating completed ready/generated snapshots. Reopening SHALL recover progress and permit resuming failed/cancelled/expired work only while the original revision and inputs remain current. Same request ID and digest SHALL replay one operation; changed content under the ID SHALL conflict. Ready results from older revisions SHALL use verified copy-forward; linked/generated history SHALL remain already-submitted history, never become a new ready copy or trigger duplicate automatic authoring. Retrying an unpersisted remote response after a crash MAY make another assistant call; exactly-once remote billing SHALL NOT be promised.

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

#### Scenario: Start response is lost
- **WHEN** the same request ID and normalized operation body are retried after an unknown response
- **THEN** the existing active or terminal operation is returned without launching duplicate work

#### Scenario: Lease owner returns late
- **WHEN** a prior owner returns after expiry or Resume has issued a newer fencing token
- **THEN** its output is discarded in the response-persistence transaction

#### Scenario: Terminal suggestion acceptance is retried
- **WHEN** an accepted suggestion plan mutation succeeded but its response was lost
- **THEN** the same acceptance content returns the stored plan result without a second revision

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

### Requirement: Authoring version one has a closed wire schema

A plan with authoring SHALL carry exactly the members shown below. All members SHALL be present; only members explicitly declared nullable below SHALL accept null. Old plans SHALL omit authoring rather than acquiring invented provenance. Unknown keys, versions, malformed unions or conflicting effective values SHALL be rejected, not silently dropped. Backend and frontend round trips SHALL preserve this contract from the first authoring implementation, including nullable look fields used by later tasks.

```json
{
  "authoring": {
    "schema_version": 1,
    "mode": "automatic",
    "brief": "",
    "scene_anchor": {
      "library_key": "studio",
      "source_id": "scene-1",
      "content_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    },
    "workflow_binding": {
      "workflow_id": 7,
      "kind": "t2i",
      "graph_digest": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "node_map_digest": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    },
    "variation_policy": {
      "camera": {"mode": "vary"},
      "framing": {"mode": "vary"},
      "pose": {"mode": "vary"},
      "expression": {"mode": "vary"}
    },
    "shared_state": {
      "look": {"origin": "none", "evidence_id": null},
      "initial_wardrobe": {"origin": "none", "evidence_id": null}
    },
    "evidence": [],
    "look_snapshot": null,
    "wardrobe_progression": null
  }
}
```

mode SHALL be automatic or manual; brief SHALL be a string of at most 2,000 characters. scene_anchor SHALL use the existing exact revision triple contract. workflow_binding SHALL be server-owned and contain exactly workflow_id (positive integer), kind (stored workflow kind string), graph_digest and node_map_digest (canonical lowercase SHA-256 values). It SHALL match the session's effective workflow and clients SHALL only echo it unchanged. variation_policy SHALL contain exactly camera, framing, pose and expression. Each member SHALL be exactly {"mode":"vary"} or {"mode":"fixed","value":"non-empty string","value_origin":"user"}. A vary member SHALL NOT carry value/origin; fixed values SHALL NOT be unresolved or assistant-origin in version one.

shared_state SHALL contain exactly look and initial_wardrobe metadata, with origin in none, user, assistant, assistant_edited or saved_look and evidence_id a string or null. Effective strings SHALL remain solely in plan.look and plan.initial_wardrobe. none SHALL require an empty effective string and null evidence_id. user SHALL require null evidence_id; user may explicitly accept an empty value. saved_look SHALL require look_snapshot and null evidence_id. assistant/assistant_edited SHALL reference an evidence entry that records the suggestion and acceptance. Changed accepted assistant suggestions SHALL use assistant_edited rather than mislabeling them as untouched output. Pending suggestions SHALL remain operation state outside the authoritative plan.

evidence SHALL be an array of server-owned records with exactly id (non-empty string), kind ("shared_choices"), input (object), output (object) and accepted (object). IDs SHALL be unique. input SHALL have exactly messages (array of {"role":"system|user|assistant","content":"text"}), model (non-empty string), parameters (JSON object of non-secret generation parameters), and plan_revision (positive integer). output and accepted SHALL contain the same non-empty subset of look and initial_wardrobe, each a string; output records the validated suggestion and accepted the explicit decision. An evidence reference SHALL include the relevant field in both objects and its accepted string SHALL equal the effective plan field. Historical unreferenced evidence MAY remain. Clients SHALL only echo existing evidence; the server SHALL create it from real suggestions and explicit acceptance and reject forged/altered records.

look_snapshot SHALL be null or exactly:
```json
{
  "look_id": "look-1",
  "version": 1,
  "content_digest": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "appearance": "soft makeup",
  "outfit": {
    "outfit_key": "outfit-1",
    "garments": [
      {"key": "jacket-1", "wording": "a blue jacket", "aside": ""}
    ]
  }
}
```
look_id SHALL be a non-empty string, version a positive integer, appearance a string and outfit null or an object with exactly outfit_key (non-empty string) and garments (non-empty array of complete garment objects in removal order). Garment keys SHALL be unique non-empty strings, wording non-empty, aside a string whose empty value means unavailable. No catalogue lookup SHALL be needed to render a snapshot. content_digest SHALL be the canonical SHA-256 digest of {"appearance":appearance,"outfit":outfit}, using the existing resource canonical-digest algorithm. Both digests in examples are illustrative. Origin saved_look for look SHALL match appearance; origin saved_look for initial_wardrobe SHALL match the fully worn outfit wording and require a non-null outfit. Kept/edited overrides SHALL instead use user origin without mutating the snapshot. Applying a look SHALL load its verified version server-side, not trust browser-supplied snapshot values.

wardrobe_progression SHALL be null or exactly {"source_look_digest":"64 lowercase hex characters","start_take_id":"non-empty string","end_take_id":"non-empty string","stage_indices":[0,1],"applied_revision":1}. The digest SHALL match the snapshotted look at application; stage_indices SHALL be a non-empty strictly increasing sequence of valid derived stage indices, and applied_revision a positive integer identifying the CAS result. The record SHALL be server-owned historical application evidence; after later take edits its old boundary IDs need not remain active. Existing wardrobe_changes SHALL remain the sole effective schedule. Reapplying replaces this record explicitly; it never drives implicit redistribution.

#### Scenario: Normalization preserves future look support
- **WHEN** an authoring-v1 plan with null look_snapshot is saved and reopened before Looks is implemented
- **THEN** all authoring members survive unchanged and no alternate schema is invented later

#### Scenario: Policy contains an unsupported shape
- **WHEN** a policy uses vary=true, unknown keys, missing dimensions or fixed without a value
- **THEN** validation refuses it instead of normalizing away the error

#### Scenario: Browser fabricates accepted provenance
- **WHEN** a save changes server-owned evidence or workflow binding or supplies an unverified look snapshot
- **THEN** the change is refused without modifying the plan

### Requirement: Preparation authority is mode-specific

An authoring-v1 automatic take SHALL reach ready state only through a current fenced `prepare_takes` operation. It SHALL reject `manual_completion` and direct begin/complete compatibility calls with `409`; clients SHALL NOT supply final prompts, effective state, versions, assistant request/output, predecessor/resource projections, provenance, duplicate flags or dependency digests. The server SHALL derive and persist all of them from current validated inputs and the actual assistant response.

An authoring-v1 manual take SHALL reach ready state through the validated manual prepare boundary. `manual_completion` SHALL be accepted only for currently unset and unlocked creative fields and SHALL be recorded as manual input; the server SHALL still derive effective state, final prompt, versions, resource projection/digest and provenance. Fixed values, resources, shared state and adaptations SHALL NOT be overridden. Resource-v1 plans without authoring metadata and legacy sessions SHALL retain their established completion/composition behavior without being labelled automatic.

Review, recovery, Approve and Submit SHALL reject an authoring-v1 ready row whose required server-owned evidence is missing, altered, client-invented or inconsistent with its mode. They SHALL NOT backfill current values to legitimize unverifiable history.

#### Scenario: Automatic caller supplies manual completion
- **WHEN** an automatic authoring request includes `manual_completion` or calls the raw begin/complete compatibility route
- **THEN** it is rejected without a ready row or assistant call

#### Scenario: Caller fabricates prepared evidence
- **WHEN** a client supplies a final prompt, effective state, assistant provenance or dependency digest for an authoring-v1 take
- **THEN** the supplied evidence cannot create an approvable or submittable ready snapshot

#### Scenario: Manual authoring fills an unlocked field
- **WHEN** a manual authoring take supplies a valid value for a still-unset unlocked creative field
- **THEN** server preparation records it as manual input and derives the authoritative ready snapshot

#### Scenario: Pre-authoring expert plan completes through compatibility API
- **WHEN** a resource-v1 plan has no authoring block and uses its existing begin/complete flow
- **THEN** the established expert behavior remains available without an automatic-authoring provenance claim

### Requirement: Guided allocation has a separate operational count limit

Guided creation and new or growing authoring-v1 plans SHALL allow 1-500 takes inclusive, validated before allocating take IDs or constructing the initial plan. Booleans, numeric strings and fractional counts SHALL be refused. This allocation limit SHALL remain separate from twenty-take synthesis batches. Existing legacy/expert plans without authoring metadata SHALL retain established count behavior and SHALL NOT be retroactively truncated or rejected solely by the new guided limit.

#### Scenario: Accidental excessive count
- **WHEN** guided creation requests 501 or one million photos
- **THEN** it fails before take allocation, session persistence or assistant work

#### Scenario: Highest allowed count
- **WHEN** valid guided inputs request 500 photos
- **THEN** creation accepts that count and synthesis remains batched at twenty

### Requirement: Unchanged ready snapshots copy forward into the current revision

A successful plan CAS SHALL atomically copy eligible unchanged ready, unlinked snapshots together with their consumed verified adaptations to the destination plan revision and preserve the originals as history. Eligibility SHALL require equivalent take choices, resolved shared/wardrobe state, exact resources and authorized translation/adaptation content, character/workflow binding, compiler/mapping versions and any consumed automatic intent/context, including ordinal and predecessor references. Predecessor equivalence SHALL compare original lineage/content rather than newly allocated copy row IDs. Changed or unverifiable inputs SHALL require preparation instead. Mode-only edits MAY preserve an assistant result when all consumed creative inputs remain equivalent.

The destination SHALL have exactly one ready row per session/revision/take, byte-identical final_prompt and preserved original synthesis/effective evidence, plus copy_forward provenance containing source prepared ID/revision, destination revision, validated input digest and the consumed-adaptation lineage array required below. The original assistant input SHALL NOT be rewritten to claim a new invocation. Destination binding metadata SHALL identify the current revision; original writer/adaptation evidence SHALL keep its source revision, with copied current adaptation authorization resolved through the recorded lineage. Conflicting existing destination state SHALL fail atomically. Review SHALL be revoked; current-revision copies SHALL require explicit approval before submission.

Pending, invalidated or linked/generated rows SHALL NOT be copied into ready state. Linked/generated history SHALL remain visibly already submitted and SHALL NOT be automatically authored or submitted again on resume. Old-revision submission SHALL remain stale even when an eligible current copy exists.

#### Scenario: Later edit preserves earlier ready work
- **WHEN** take three changes and earlier ready take inputs are verified identical
- **THEN** plan save creates current-revision ready copies for eligible earlier takes
- **AND** recovery reports those copies as completed without a writer call

#### Scenario: Current copy is submitted
- **WHEN** the new revision is explicitly reviewed and its copied take is submitted
- **THEN** submission uses the unchanged prompt from the current-revision copy
- **AND** directly submitting the old revision is still refused

#### Scenario: Resource translations changed
- **WHEN** a source triple matches but its consumed authorized translation differs
- **THEN** the old snapshot is not copied as valid

#### Scenario: Copy-forward fails during save
- **WHEN** a destination snapshot/adaptation conflict or write error occurs during copy-forward
- **THEN** plan revision, snapshot copies, adaptation copies and invalidations roll back together

#### Scenario: Earlier take was already submitted
- **WHEN** an earlier snapshot is linked/generated before a later plan edit
- **THEN** it remains historical submitted evidence
- **AND** it produces neither a new ready copy nor a duplicate automatic generation

### Requirement: Selective invalidation excludes total count from creative inputs

Automatic writer requests and copy-forward creative digests SHALL omit total take count. It SHALL remain UI/progress metadata only; take ID and ordinal SHALL remain binding. Removing or adding later takes SHALL NOT alone invalidate equivalent earlier choices. Explicit brief content SHALL remain binding even when it mentions a count.

#### Scenario: Later take is removed
- **WHEN** take ten is removed from twelve prepared takes without changing other earlier inputs
- **THEN** equivalent unlinked ready takes one through nine can copy forward without another assistant call
- **AND** the new total does not independently invalidate them

#### Scenario: Earlier insertion changes ordinals
- **WHEN** a take is inserted before existing automatic takes
- **THEN** changed ordinals invalidate affected later work under the selective rule

### Requirement: Copy-forward carries only consumed verified adaptations

An eligible snapshot copy-forward SHALL copy or reuse all and only its consumed, still-applicable reviewed adaptations under the destination session/revision/take/resource/field identity. Exact resource selection, field authorization, source/translation value, adapted value and fixed-state applicability SHALL be revalidated. source_value and adapted_value SHALL remain unchanged. Destination loaders SHALL continue resolving only the exact current revision.

copy_forward.adaptations SHALL record an array of source_adaptation_id, source_plan_revision, destination_adaptation_id, destination_plan_revision and adaptation_digest for each carried row. adaptation_digest SHALL be distinct from asset_revision.content_digest and SHALL cover the exact resource triple, field, source_value and adapted_value using the existing canonical digest algorithm. Identical existing destination rows SHALL be reused; incompatible destination rows SHALL abort CAS. Missing, changed or no-longer-applicable source approval SHALL make that take ineligible for a ready copy and require fresh review/preparation. Unconsumed adaptations SHALL NOT be inherited.

Snapshot copies, adaptation copies, plan save and invalidation SHALL commit in one transaction. Original approvals and prepared evidence SHALL remain unchanged. General plan revision changes SHALL still not inherit adaptations; this verified carry-forward is the explicit exception.

#### Scenario: Ready take used an adaptation
- **WHEN** a later take edit permits an adapted ready take to copy forward
- **THEN** its consumed adaptation also exists under the new revision with lineage
- **AND** current-revision review and submission can resolve the same approved values

#### Scenario: Destination adaptation conflicts
- **WHEN** the destination already has different source/adapted values for that identity
- **THEN** the entire plan CAS and all copies roll back without overwriting either approval

#### Scenario: Adaptation no longer applies
- **WHEN** consumed source/translation content or fixed state differs
- **THEN** that take is not copied ready and fresh review/preparation is required

### Requirement: Generated output freezes the complete saved-look snapshot

After any take reaches generated/linked state, authoring.look_snapshot SHALL remain identical under canonical JSON comparison, including its null state, logical identity/version/digest, appearance, outfit identity, garment order, wording and aside. Matching effective look or initial_wardrobe strings SHALL NOT permit replacing a different snapshot. Selecting another preset/version SHALL require a new session. Existing explicit scoped wardrobe changes SHALL remain allowed under their established rules without changing the frozen source snapshot.

#### Scenario: Different look version has identical initial text
- **WHEN** generated output exists and a newer preset version preserves look/initial wardrobe text but changes version, order or aside
- **THEN** applying it is refused for that session

#### Scenario: Snapshot is echoed unchanged
- **WHEN** a save echoes an equivalent snapshot without changing its strings or array order
- **THEN** the snapshot freeze guard does not block otherwise permitted wardrobe changes

### Requirement: Server-owned state changes through explicit operations

Generic plan saves SHALL only echo workflow_binding, evidence, look_snapshot and wardrobe_progression unchanged. Legitimate mutation SHALL use explicit compare-and-swap operations: accept a server-issued shared suggestion with user edits; apply a server-verified preset key/version; or apply a server-issued progression preview with reviewed merge/replace choices. Each SHALL validate current revision and generation guards, derive the resulting server-owned content and persist atomically.

For generic user edits of effective look/initial_wardrobe strings, the server SHALL set the corresponding origin to user with null evidence_id, keeping old evidence historical; clients SHALL NOT fabricate provenance. Read normalization SHALL preserve empty take arrays and missing/invalid IDs for authoritative validation rather than inventing take-001 or other content. Stable IDs SHALL be allocated only by guided creation or explicit Add take.

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

### Requirement: Adaptation approval binds the effective authorized descriptive value

take_resource_adaptation.source_value SHALL be the exact effective authorized descriptive input consumed before adaptation, not necessarily the original asset_revision.payload string. Recording SHALL use _prepare_resource() or the same shared field-resolution function as actual preparation, including canonical translation authorization, required-field refusal without payload fallback, and existing optional-field rules. Original payloads SHALL remain immutable.

Before a persisted adaptation is applied in review, finalization, or copy-forward, the shared applicability boundary SHALL compare its source_value exactly with the current effective authorized descriptive input for the same resource triple and field. Identity/revision equality alone SHALL NOT authorize reuse. Missing, unauthorized or changed inputs SHALL make the approval inapplicable and require fresh review/preparation; no stale adapted value SHALL reach a new ready snapshot. Existing rows whose source_value reflects an obsolete payload value SHALL NOT be silently rewritten or reauthorized. Preserve immutable historical rows and obtain new approval under a new plan revision when the existing unique identity cannot hold a replacement. Historical generated snapshots SHALL remain unchanged.

#### Scenario: Translation differs from original payload
- **WHEN** a required descriptive field has an authorized translation different from its payload and the user approves an adaptation
- **THEN** the saved source_value equals the effective translation byte-for-byte
- **AND** review, finalization and copy-forward use the same source-value contract

#### Scenario: Translation changes without changing the resource triple
- **WHEN** an authorized descriptive value changes after adaptation approval, including for a legacy row recorded from payload
- **THEN** the existing approval is inapplicable despite matching resource identity and revision
- **AND** it cannot authorize finalization or ready copy-forward without fresh review/preparation

#### Scenario: Required translation is absent
- **WHEN** adaptation recording targets a required field without a valid authorized translation
- **THEN** recording refuses the request without payload fallback or persistence

### Requirement: Ready snapshots revalidate mutable resource dependencies before submission

Each finalized prepared_take SHALL persist a versioned effective_resource_input_digest and its canonical input projection in provenance. Use the existing canonical SHA-256 algorithm over selected resource triples sorted by library_key/source_id/content_digest, each resource's effective authorized descriptive_inputs resolved by the shared preparation function (preserving field values and list order), and consumed adaptations sorted by resource triple/resource_field with exact source_value/adapted_value. Include all effective descriptive inputs supplied to preparation, even when no adaptation exists. Exclude unused sidecar metadata, timestamps, plan revision and allocated adaptation row IDs; copied equivalent inputs SHALL have the same digest. Missing/invalid required authorization SHALL fail revalidation rather than hash a payload fallback. This digest supplements, not replaces, existing fixed-state, workflow and copy-forward checks.

Review display, authoritative Approve, ready-result recovery and Submit SHALL re-resolve that projection against current dependencies for unlinked ready snapshots. A missing, unverifiable or mismatched digest SHALL make the snapshot non-submittable and the plan's existing approval ineffective; Approve SHALL refuse it and require explicit resource refresh and preparation under a new plan revision. Historical rows without verifiable persisted evidence SHALL NOT be backfilled from current dependencies to claim past consumption. Read-only review MAY expose derived stale status without writing; approval validity SHALL never depend only on a stored approval row after drift. Once drift is recorded, refresh SHALL be required rather than silently rewriting ready evidence.

Approve SHALL validate all unlinked ready snapshots covered by the plan review in the same serialized transaction that records approval. Submit SHALL perform the dependency check inside the same serialized database transaction as approval validation, shot insertion and snapshot linking, including direct domain callers and selected batch submission. Translation/adaptation writes SHALL NOT interleave between validation and linking. A stale member of a selected batch SHALL cause zero new shot/link writes for the batch. A failed transaction SHALL NOT leave a stale approval effective merely because a revocation write rolled back; every approval/submission check SHALL derive validity from current inputs. Changes committed after linking SHALL NOT invalidate or rewrite generated/linked history, nor duplicate shots on an otherwise valid idempotent retry.

#### Scenario: Translation changes after approval without a plan edit
- **WHEN** a take is ready and approved at revision four and its effective translation then changes under the same resource triple
- **THEN** Review reports drift and Submit refuses it with no new shot or link
- **AND** Refresh resources and new preparation/review are required even though the plan revision still equals four

#### Scenario: Translation update races selected submission
- **WHEN** translation persistence races the dependency check and linking of selected takes
- **THEN** serialized transactions produce either submission against the still-current validated inputs or refusal with no partial batch writes
- **AND** a later translation update preserves already-linked historical prompts

#### Scenario: Only unused translation metadata changes
- **WHEN** all effective descriptive inputs and consumed adaptations remain identical
- **THEN** the resource-input digest remains equal and does not alone revoke approval

### Requirement: Explicit resource refresh creates a dependency revision

Provide POST /api/sessions/{session_id}/plan/refresh-resources with expected_revision and the same resource-mode/write gates as plan save. The server SHALL load the stored plan, verify CAS, and diagnose missing/unverifiable or changed effective resource dependencies against current-revision prepared evidence and persisted adaptation source values, including adaptations approved before finalization. Absence of a prepared row or adaptation alone SHALL NOT count as drift; missing verifiable dependency evidence on an existing ready row SHALL. Old-revision invalidated history SHALL NOT repeatedly trigger refresh after a successful dependency revision. When drift exists, one transaction SHALL create revision N+1 with unchanged normalized creative plan fields, stable take IDs, resource selections and frozen look_snapshot; revoke review, fence active authoring, and invalidate affected unlinked work. Apply existing downstream dependency rules and verified copy-forward only to demonstrably unaffected ready takes and consumed adaptations. Changed approvals SHALL remain historical; fresh adaptations can be approved under N+1. The response SHALL identify affected takes and required preparation; refresh SHALL NOT call the assistant, approve, submit or run generation.

An unchanged dependency set SHALL return the current revision without writes. A stale expected_revision SHALL return the existing stale-CAS conflict without creating another revision, including retries after a successful refresh. Ordinary plan save SHALL retain its established behavior; no special no-op-save trigger SHALL be required for resource recovery. Missing/invalid translations MAY leave the refreshed draft blocked for preparation and SHALL be reported rather than substituted. Generated/linked evidence SHALL remain untouched. A further external change SHALL be detected again by the same dependency checks.

#### Scenario: Refresh without a creative edit
- **WHEN** a user explicitly refreshes drifted resources with expected_revision four
- **THEN** the server creates revision five without fabricating creative changes
- **AND** affected takes require new preparation and approval, while eligible unaffected results use verified copy-forward

#### Scenario: Duplicate or unnecessary refresh
- **WHEN** two refresh requests use the same expected revision, or a current request finds no dependency drift
- **THEN** at most one revision is created for drift and stale CAS is refused
- **AND** a no-drift current request leaves the revision unchanged

### Requirement: Resource session presentation projects authoritative plan constants

For a resource-v1 session with a plan, `plan.look` and `plan.initial_wardrobe` SHALL be the sole effective look and wardrobe. Resource preparation SHALL NOT consult or synchronize the legacy session columns. Guided creation SHALL leave those legacy columns empty. Session detail/list responses SHALL project the plan values through the existing readable `look` and `wardrobe` fields, and session free-text filtering SHALL search the same projected values. Legacy sessions SHALL continue to read and search their session columns unchanged.

A resource-v1 session missing its plan SHALL report inconsistent state instead of silently falling back to session-column values. Generic session PATCH of look or wardrobe for resource-v1 SHALL return `409 plan_field_required`; plan CAS remains the only edit path. Existing stored session columns SHALL remain untouched historical compatibility data rather than a second source of truth.

#### Scenario: Resource look changes through plan CAS
- **WHEN** a resource-v1 plan changes look or initial wardrobe
- **THEN** subsequent detail, listing and search project the new plan values
- **AND** no session-column mirror write is required

#### Scenario: Legacy session is listed
- **WHEN** a legacy session has look and wardrobe only in its session row
- **THEN** existing listing, filtering and composition behavior remains unchanged

#### Scenario: Resource constants are patched through the legacy route
- **WHEN** a caller PATCHes session look or wardrobe for a resource-v1 session
- **THEN** the request is refused and identifies plan CAS as the authoritative path

### Requirement: Transient staging and operation ownership recover without deleting durable work

Application startup SHALL expire authoring ownership left by the prior process, preserve completed progress, release an unfinished import commit claim with no atomic result when still within selection lifetime, expire overdue import/photo staging and attempt eligible file cleanup before requests are served. Each status or mutation of a selection, operation or photo stage SHALL also perform record-local lazy recovery plus bounded opportunistic cleanup. Authoring leases SHALL expire ten minutes after their last backend renewal. Selection and photo expiry SHALL remain fixed at 24 hours from creation and SHALL NOT extend on access.

Import commit or cancel and photo save or cancel SHALL remove staged bytes only after terminal database state or saved redacted provenance is durable. Expiry SHALL remove staged bytes and retain a terminal tombstone/status for 24 additional hours before optional purge. Cleanup failure SHALL remain a visible retryable warning and SHALL NOT delete or roll back committed resources, saved looks, plans, prepared snapshots or generated history. No background scheduler or new queue framework SHALL be required.

#### Scenario: Process restarts during authoring
- **WHEN** startup finds an active operation from the prior process
- **THEN** it becomes expired with completed progress preserved for a valid Resume

#### Scenario: Commit bytes outlive a successful import
- **WHEN** canonical import and its durable selection result commit successfully
- **THEN** staged bytes are removed and replay returns the recorded result without re-import

#### Scenario: Cleanup cannot remove a staged file
- **WHEN** filesystem cleanup fails after a terminal database transition
- **THEN** the durable result remains correct and status exposes a warning for a later cleanup retry

### Requirement: Operational disablement protects authoring metadata across downgrade

With `resource_planning_enabled=false`, the system SHALL return `503` for new browser-selection mutations, translation proposal/application, guided creation, authoring plan saves, suggestion start/resume/accept, automatic/manual authoring preparation, adaptation writes, dependency refresh, review approval, saved-look create/edit/import, photo stage/extract/save, preset application and progression application. Existing translation write gates SHALL remain. Selection/operation status and cancellation, resource/session/plan/look/review/recovery reads and photo preview SHALL remain available. Existing path-backed resource import and legacy garment/outfit import SHALL retain their prior flag behavior.

Disabling while a remote call is in flight SHALL prevent another call and SHALL make the fenced response transaction discard output and cancel the operation. Already ready and authoritatively approved snapshots SHALL retain existing idempotent Submit and runner execution behavior; disablement SHALL NOT permit new approval or preparation.

Before starting a rollback-compatible older binary, the operator SHALL create and verify the existing SQLite backup, disable resource planning in persisted configuration or the target process environment, cancel or observe terminal active operations, verify disabled state, and stop the current process cleanly. The older binary SHALL run with the flag disabled so generic plan saves cannot drop unknown authoring metadata. A binary predating the flag SHALL NOT open the upgraded database for writes; rollback SHALL restore the verified pre-upgrade backup instead. Re-enabling writes SHALL require a binary that understands the closed authoring schema.

#### Scenario: New mutation is attempted while disabled
- **WHEN** any newly introduced authoring, selection, translation or look write is requested with the feature flag off
- **THEN** it fails with `503` before remote calls or durable writes

#### Scenario: Approved prepared work exists before disablement
- **WHEN** a ready snapshot was already authoritatively approved before the flag was disabled
- **THEN** existing idempotent submission and runner execution remain available
- **AND** no new work can be prepared or approved

#### Scenario: Compatible downgrade opens an authoring plan
- **WHEN** the verified backup/disable/stop procedure is followed before launching a rollback-compatible older binary
- **THEN** plans remain inspectable while plan mutations are blocked from dropping unknown metadata
