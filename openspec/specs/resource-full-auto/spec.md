# resource-full-auto Specification

## Purpose
Let users authorize one complete resource session and recover its preparation and generation without repeated routine actions or duplicate work.

## Requirements

### Requirement: Guided eligibility binds exact inputs

Full Auto SHALL be opt-in through guided creation. It SHALL require a valid resolved character/model record, compatible frozen workflow, exact ready rooms revision, exact saved-look version/digest, exact eligible automation-profile revision/digest and an integer photo count from 1 through 500. Known valid selections SHALL carry forward; the user SHALL only select character/model when unresolved. Fused scenes SHALL retain their manual/advanced path.

#### Scenario: Guided eligibility binds exact inputs
- **WHEN** a request supplies 501 takes or a fused scene
- **THEN** creation is refused and no session is allocated

#### Scenario: Profile or look changes during selection
- **WHEN** the submitted exact version/digest cannot be verified
- **THEN** preflight refuses creation and asks for a current explicit selection without substituting latest

### Requirement: Guided authorization uses a closed schema

The optional guided saved_look object SHALL contain exactly look_key, version and content_digest. Full Auto SHALL additionally accept exactly authorization_version equal to 1, generate_without_pauses equal to true, profile_revision, profile_digest, progression equal to all_stages_full_interval, framing_intent equal to vary or full_body, scope_digest and scope_issued_at as defined below. These SHALL use strict types, positive revision/version integers and canonical SHA-256 digests.

#### Scenario: Guided authorization uses a closed schema
- **WHEN** full_auto includes an unknown field or a string profile_revision
- **THEN** validation rejects the request without coercion

### Requirement: Initial shared inputs are resolved before allocation

Full Auto SHALL require automatic mode and refuse competing nonempty look/wardrobe overrides. It SHALL resolve all initial profile/progression/coverage facts before any assistant call. Invalid requests SHALL leave zero session, plan, receipt or run allocations.

#### Scenario: Initial shared inputs are resolved before allocation
- **WHEN** Full Auto includes a nonempty competing wardrobe override
- **THEN** no session, plan, receipt, run or assistant call is created

### Requirement: One action authorizes the bounded journey

One explicit Generate without pauses action SHALL authorize look application, every authored stage across the full interval, bounded automatic preparation, policy-based review approval, submission and initial execution of every take. The authorization SHALL bind frozen inputs, stable take targets, effective generation settings and policy version. It SHALL NOT authorize semantic-conflict waivers, new identities/looks, silent source adaptation or unbounded image retries.

#### Scenario: One action authorizes the bounded journey
- **WHEN** an eligible twelve-take request receives Generate without pauses authorization
- **THEN** its stored authorization covers the twelve targets and excludes image rerolls

#### Scenario: Eligible one-action journey
- **WHEN** a user selects eligible inputs and authorizes Generate without pauses
- **THEN** the system creates the populated session, prepares all takes, validates, approves, submits and executes without further routine buttons or per-take entry
- **AND** every phase and result remains inspectable

### Requirement: Guided creation commits one replayable result

The first plan, authorization/run and idempotent creation response SHALL persist atomically. New creation SHALL return 201; identical retry SHALL return its stored result with 200; changed normalized content under the same request_id SHALL fail with 409 idempotency_conflict. Only the opt-in response SHALL add full_auto_run_id.

#### Scenario: Guided creation commits one replayable result
- **WHEN** two identical requests race with the same request_id
- **THEN** one session/run is committed and the replay returns the stored result with 200

#### Scenario: Creation response is lost
- **WHEN** the same creation request is retried after commit but before the client sees success
- **THEN** it returns one existing session and run with the same targets and no duplicate scheduling

### Requirement: Preparation reuses bounded stage services

Full Auto SHALL use existing creation, look/progression derivation, preparation, approval, submission and runner services. It SHALL prepare ordered targets in operations of at most twenty and automatically continue subsequent batches up to the guided count limit. Each completed result SHALL persist incrementally. Current valid completed/linked snapshots SHALL be reused, never reauthored solely because the coordinator resumes.

#### Scenario: Preparation reuses bounded stage services
- **WHEN** twenty of forty targets already have current valid preparations
- **THEN** the next operation targets only the remaining twenty

#### Scenario: Forty takes exceed one preparation batch
- **WHEN** the first twenty results persist successfully
- **THEN** the next bounded operation starts automatically without regenerating them or truncating the plan

#### Scenario: A later take fails preparation
- **WHEN** take twenty-three fails in a forty-take session
- **THEN** earlier valid preparation remains saved, failed/remaining work is reported and no shots have yet been submitted

### Requirement: All targets must pass before approval

Before approval or any submission, every target SHALL be ready or already linked with verified provenance and current dependencies. Unresolved structural/ownership conflicts, placeholders, stale adaptations, workflow/resource drift and unreviewed exact duplicate tuples SHALL block. Neutral plan-level descriptive notices alone SHALL remain visible without blocking.

#### Scenario: All targets must pass before approval
- **WHEN** take twenty-three has a stale adaptation
- **THEN** approval and submission write no new records

#### Scenario: Neutral notice is not a contradiction
- **WHEN** validated prepared takes have only descriptive-versus-constant plan notices
- **THEN** policy approval can proceed while keeping the notices inspectable

### Requirement: Approval records exact policy evidence

Approval SHALL bind the current plan revision, exact validated targets/snapshots and Full Auto authorization; it SHALL be identified as policy-based, never falsely human-reviewed. Clients SHALL NOT bypass those gates through another approval or submit route.

#### Scenario: Approval records exact policy evidence
- **WHEN** a valid Full Auto revision is approved
- **THEN** the approval records that revision, target/snapshot identities and policy provenance rather than human review

#### Scenario: Approval is bypassed through another client
- **WHEN** another route attempts to approve or submit a Full Auto session with unresolved blockers
- **THEN** the same authoritative gate rejects the request without new approval or shot writes

### Requirement: Submission reuses verified preparation lineage

Each submission SHALL revalidate current authorization and dependencies and reuse an existing shot link for the same preparation lineage. No direct client final prompt or evidence SHALL become trusted automatic output. Reference/edit/guide rules, mapped-input invariants, saved final prompts and legacy behavior SHALL remain intact.

#### Scenario: Submission reuses verified preparation lineage
- **WHEN** the same prepared target is submitted again
- **THEN** the existing shot link is returned without another shot or prompt recomposition

### Requirement: A session retains one inspectable nonterminal run

Each run SHALL expose a stable identity, run revision, plan revision, phase, state, progress, safe diagnostics and available controls. States SHALL distinguish active, waiting_authoring, waiting_runner, blocked, cancel_requested, cancelled, failed, succeeded and completed_with_errors. A session SHALL have at most one nonterminal Full Auto run; blocked runs retain their identity for explicit recovery.

#### Scenario: A session retains one inspectable nonterminal run
- **WHEN** another start targets a session whose run is blocked
- **THEN** the existing run remains discoverable and no second nonterminal run is created

### Requirement: Mutation boundaries fence obsolete owners

Active mutation authority SHALL use recoverable leases and monotonic fencing, with current authorization/revision checks at every persistence and dispatch boundary.

#### Scenario: Mutation boundaries fence obsolete owners
- **WHEN** a previous lease owner attempts to persist a result after takeover
- **THEN** the write is rejected and the current run remains unchanged

#### Scenario: Plan edit races a response
- **WHEN** a result arrives after plan revision or owner fence changed
- **THEN** the old result is discarded and cannot approve, submit or schedule a new shot

### Requirement: Owned preparation shares manual exclusion

Owned child preparation SHALL share the existing authoring exclusion; competing manual/automatic authoring, approval, submission, retry and runner starts SHALL not launch duplicate work. A deliberate plan edit SHALL first suspend/fence the run, then use normal plan CAS and invalidation. Full Auto ownership SHALL permit status, Cancel and safe reconciliation. Leases and ownership tokens SHALL not be trusted from browser-supplied data or exposed in public views.

#### Scenario: Owned preparation shares manual exclusion
- **WHEN** another tab requests manual preparation while a child operation owns the session
- **THEN** it receives the active ownership status and starts no worker

#### Scenario: Two tabs request work
- **WHEN** another client attempts conflicting work on an owned session
- **THEN** it receives current run/operation status and no second worker starts

### Requirement: Manual transfer waits for owned work to settle

An explicit manual-transfer request SHALL request Cancel and remain pending while any owned attempt is pending or unknown. Full Auto SHALL retain ownership/fencing and manual mutations SHALL remain blocked until every owned attempt is verified settled. After settlement, the pending transfer SHALL release ownership without resuming or dispatching Full Auto; status, Cancel and read-only reconciliation remain available.

#### Scenario: Manual transfer waits through an unknown attempt
- **WHEN** the user requests manual transfer while an owned attempt remains unknown after three ComfyUI probes
- **THEN** Cancel intent remains pending, Full Auto retains ownership/fencing and manual mutations start no worker
- **AND** read-only reconciliation remains available; verified settlement completes the transfer with outputs retained and no Full Auto restart

### Requirement: Remote I/O releases database transactions

Remote calls SHALL not hold database transactions.

#### Scenario: Remote I/O releases database transactions
- **WHEN** a fake assistant holds its response while another connection reads and cancels the run
- **THEN** the cancellation transaction completes before the response is released

### Requirement: Restart recovers unchanged authorized work

The backend SHALL retain responsibility after browser closure. After service restart it SHALL recover expired ownership, reconcile durable phase receipts and automatically continue unchanged authorized non-cancelled work. Cancelled, blocked and ambiguous work SHALL not silently restart. New owner fences SHALL prevent late results from prior owners from changing state. Persisted creation without worker dispatch SHALL be recoverable.

#### Scenario: Restart recovers unchanged authorized work
- **WHEN** the backend restarts with one eligible run and one cancelled run
- **THEN** only the eligible run continues from its persisted receipts

#### Scenario: Backend restarts after partial submission
- **WHEN** some prepared targets already have shot links
- **THEN** recovery reuses those links and submits only remaining targets before starting the runner

#### Scenario: Backend dies after creation commit
- **WHEN** a run exists but its first worker was never dispatched
- **THEN** startup claims and continues that run rather than allocating another session

### Requirement: Cancellation preserves completed work

Cancellation SHALL durably stop further scheduling, fence late authoring results and preserve completed preparations, shot links and images. Pending shots SHALL retain their IDs. GPU interruption SHALL target only work owned by the session runner; an already completed image SHALL be retained and reported. Cancelled work SHALL resume only through explicit Resume, with current run/plan revision and valid authorization.

#### Scenario: Cancellation preserves completed work
- **WHEN** Cancel arrives after three targets completed and a fourth request is in flight
- **THEN** the three results remain and the late fourth response cannot schedule more work

#### Scenario: Cancel during assistant work
- **WHEN** cancellation is persisted before an assistant response returns
- **THEN** the response is discarded and no next item is scheduled
- **AND** completed earlier work survives

#### Scenario: Cancel races GPU completion
- **WHEN** an image finishes before interruption takes effect
- **THEN** it remains a completed result and no replacement shot is allocated on resume

### Requirement: Control requests replay by identity

Same control request IDs and content SHALL replay; different content SHALL conflict without extra writes.

#### Scenario: Control requests replay by identity
- **WHEN** the same Cancel request_id is retried with changed content
- **THEN** the response is an idempotency conflict with no additional state change

### Requirement: Correctable failures expose actual remedies

Correctable failures SHALL expose a stable code, readable cause, affected take/stage/source and a real remedy. Coverage/ownership conflicts SHALL link to profile correction; stale inputs SHALL use explicit refresh/review and changed-scope authorization; assistant failures SHALL allow configuration and remaining-work resume. Bound workflow changes and generated-continuity violations SHALL require a new session when existing rules require it.

#### Scenario: Correctable failures expose actual remedies
- **WHEN** a profile has an unknown aside-region fact
- **THEN** status identifies the affected stage and opens the profile correction action

### Requirement: Resume retains prepared work and honors disablement

Resume SHALL not reapply progression, regenerate current valid preparations, or recreate linked shots. Feature disablement SHALL block new Full Auto mutations and automatic submit/start/resume while retaining read/cancel and safe settlement of already dispatched work.

#### Scenario: Resume retains prepared work and honors disablement
- **WHEN** planning is disabled after targets have policy approval
- **THEN** automatic submission and start remain blocked while reads and Cancel remain available

### Requirement: Dispatch intent precedes remote submission

Full Auto SHALL preserve a durable attempt identity and dispatch intent before remote queue submission, then record acknowledgement and completion evidence. Recovery SHALL distinguish unsent work, acknowledged running work, completed output, known failure/cancellation and unknown remote outcome. A known remote job SHALL be reattached or its result recovered rather than queued again.

#### Scenario: Dispatch intent precedes remote submission
- **WHEN** the process fails immediately after the remote queue accepts a request
- **THEN** the existing attempt intent survives and recovery does not allocate another shot

#### Scenario: Remote queue accepts but acknowledgement is lost
- **WHEN** restart finds an attempt with unknown acknowledgement
- **THEN** it reconciles that attempt or blocks execution_outcome_unknown without another automatic queue submission

#### Scenario: Acknowledged job finishes while backend is down
- **WHEN** recovery finds its existing completed output
- **THEN** the output is adopted once for the existing shot and no new shot or remote job is created

### Requirement: Missing history cannot prove nonexecution

A lost acknowledgement SHALL be reconciled using the attempt's recorded correlation evidence; absence from queue/history alone SHALL not prove it never executed.

#### Scenario: Missing history cannot prove nonexecution
- **WHEN** an unacknowledged attempt is absent from queue and history
- **THEN** recovery retains the unknown outcome instead of declaring the attempt unsent

### Requirement: Unknown outcomes require explicit retry consent

Unreconciled dispatch SHALL block with execution_outcome_unknown and SHALL NOT be automatically retried. A user-authorized retry after that warning SHALL retain prior attempt evidence and the same shot ID. Exactly-once remote execution or billing SHALL not be promised. Ordinary failed/cancelled shots SHALL be retryable explicitly on existing IDs; successful outputs SHALL not be requeued. Each genuinely new attempt SHALL retain unique filename-prefix behavior and existing locked-file recovery.

#### Scenario: Unknown outcomes require explicit retry consent
- **WHEN** an attempt has execution_outcome_unknown and no risk acceptance
- **THEN** no new queue request is sent

### Requirement: The serial runner preserves per-shot outcomes

One durable global runner claim SHALL protect manual and Full Auto starts. A busy runner SHALL put Full Auto in waiting_runner without stealing or interrupting unrelated work. The runner SHALL preserve serial execution and continue after individual known shot failures. All-success, mixed-result and all-failed outcomes SHALL remain distinguishable even if the legacy session status says done for a mixed result.

#### Scenario: The serial runner preserves per-shot outcomes
- **WHEN** one shot fails and the next succeeds
- **THEN** the next shot still executes and the summary distinguishes mixed results from all-failed

#### Scenario: Another session owns the GPU
- **WHEN** all Full Auto shots are submitted while the runner is busy
- **THEN** progress shows waiting_runner and execution starts only after the global claim becomes available

#### Scenario: One generation fails
- **WHEN** a shot definitively fails but other shots can run
- **THEN** serial execution continues and the final summary exposes partial results and failed-shot retry

### Requirement: Execution checks frozen generation inputs

Authorized settings/model/workflow drift SHALL block dispatch rather than silently change inputs.

#### Scenario: Execution checks frozen generation inputs
- **WHEN** the bound model settings change before dispatch
- **THEN** dispatch blocks instead of silently using the new settings

### Requirement: Full-body intent fixes canonical framing

Full Auto SHALL preserve guided canvas validation, inherited effective dimensions and explicit-choice mapping checks. It SHALL show effective canvas dimensions before authorization. Default framing intent SHALL be vary; choosing full_body SHALL set variation_policy.framing to fixed with user origin and the exact value "Full body, head to toe, both feet visible, with space above the head and below the feet." while retaining permitted camera/pose/expression variation.

#### Scenario: Full-body intent fixes canonical framing
- **WHEN** full_body is selected with otherwise varying creative dimensions
- **THEN** framing stores the exact head-to-toe sentence while permitted camera and pose variation remains

### Requirement: Fixed framing and connected inputs are preserved

An already fixed different framing value SHALL fail with framing_conflict; structured fixed-value violations from the writer SHALL block validation. Arbitrary prose SHALL not be claimed exhaustively checked for semantic contradictions. Connected workflow slots SHALL not be overwritten to enforce canvas dimensions.

#### Scenario: Fixed framing and connected inputs are preserved
- **WHEN** full_body competes with a different explicit fixed framing
- **THEN** preflight returns framing_conflict without modifying workflow inputs

### Requirement: Output success does not certify visual fidelity

Coverage describes garment state, framing describes intended composition and canvas describes image dimensions. None SHALL be represented as proof of visible feet, correct anatomy or exact garment reproduction. Generation success SHALL mean an output was produced, not that visual compliance was certified. Visual rerolls SHALL require an explicit user action.

#### Scenario: Output success does not certify visual fidelity
- **WHEN** a generated image crops the feet despite full-body instructions
- **THEN** the image remains visible without a visual-compliance claim or automatic reroll

#### Scenario: Portrait dimensions with cropped output
- **WHEN** a successful generation does not show the feet despite a full-body instruction
- **THEN** the result remains visible with its actual instructions and no false visual-compliance claim
- **AND** the user can request an explicit corrective generation

### Requirement: Automated acceptance isolates external effects

Automated acceptance SHALL use isolated fixtures and fake assistant/ComfyUI boundaries, without GPU/network dependencies or production data writes. It SHALL cover exact-version replay, stage endpoints, structured coverage and layering, accessory ownership, zero-write invalid requests, all-target gates, CAS/fencing races, cancellation, lost responses and restart at every side-effect boundary.

#### Scenario: Automated acceptance isolates external effects
- **WHEN** the contract recovery suite runs with fake providers
- **THEN** it needs no GPU or network and writes only isolated fixture data

### Requirement: UI acceptance proves the uninterrupted journey

UI acceptance SHALL demonstrate one-action eligible runs, multi-batch progress, missing-profile correction, actionable blockers and the unchanged manual alternative.

#### Scenario: UI acceptance proves the uninterrupted journey
- **WHEN** an eligible forty-take journey completes in the browser test
- **THEN** it uses one authorization, multiple batches and no intermediate action

### Requirement: Live acceptance requires separate authorization

A separate user-authorized live acceptance exercise SHALL create a new session with an automation-ready test look containing a progressive accessory, at least twelve takes and enough takes for all stages.

#### Scenario: Live acceptance requires separate authorization
- **WHEN** only this planning request has been authorized
- **THEN** no live generation is started and live acceptance remains incomplete

### Requirement: Live acceptance records protocol and visual evidence

The live acceptance exercise SHALL exercise actual assistant preparation and actual ComfyUI generation through one authorization, record source identities, phase transitions, unique take/shot/attempt links and recovery evidence, and visually inspect initial, intermediate and final outputs for framing and accessory/garment fidelity. Private images and prompts SHALL remain outside tracked artifacts.

#### Scenario: Live acceptance records protocol and visual evidence
- **WHEN** the separately authorized twelve-take exercise finishes
- **THEN** the report records actual phase transitions, unique links, recovery evidence and initial/intermediate/final visual findings

#### Scenario: Final live journey is evaluated
- **WHEN** the separately authorized real generation finishes
- **THEN** the acceptance report records protocol completion, unique allocations, recoverability and visual findings separately
- **AND** a remaining accessory in the final prompt fails the ownership regression even if generation succeeded

### Requirement: Live mismatches remain visible

Any visual mismatch SHALL be reported separately from protocol success; no image SHALL be silently rerolled or counted as proof of visual compliance. Planning alone SHALL not mark this acceptance complete.

#### Scenario: Live mismatches remain visible
- **WHEN** the final live output retains an accessory contrary to its instructions
- **THEN** the mismatch is reported without a hidden reroll or a visual-compliance claim

### Requirement: Discovery returns current or latest run

GET `/api/sessions/{sid}/full-auto` SHALL return 200 with current_run containing the nonterminal public run, otherwise the most recent terminal run, or null if none exists; a missing session SHALL return 404. Reopening or a second tab SHALL discover that record without a locally remembered run ID. Public views SHALL include run revision, epoch, phase, state, progress, policy/authorization versions, expiry, remaining allowances, reason and available actions.

#### Scenario: Discovery returns current or latest run
- **WHEN** a new browser requests status for an existing session without a local run ID
- **THEN** the endpoint returns its nonterminal run, latest terminal run or null as applicable

#### Scenario: Reopen without browser storage
- **WHEN** another browser opens a session without a saved run ID
- **THEN** discovery returns the existing run and progress without allocating or restarting work

### Requirement: Resume opens a fenced execution epoch

Active, waiting_authoring, waiting_runner, blocked and cancel_requested SHALL be nonterminal. Cancelled, failed, completed_with_errors and succeeded SHALL be terminal for the current execution epoch. Explicit Resume MAY open a new fenced epoch on the same cancelled/failed/completed_with_errors run after exclusivity, input and budget checks, preserving receipts and cumulative counters; succeeded SHALL not resume. Historical control replay SHALL not reopen a previous epoch.

#### Scenario: Resume opens a fenced execution epoch
- **WHEN** a cancelled run resumes and its old Cancel request is replayed
- **THEN** the same run retains its targets and the old receipt does not cancel the new epoch

#### Scenario: Resume a cancelled execution epoch
- **WHEN** explicit Resume passes current-input, budget and exclusivity checks
- **THEN** the same run enters a new fenced epoch with existing target and shot identities
- **AND** replay of the earlier Cancel request does not cancel the newer epoch again

### Requirement: Active and waiting transitions are bounded

Active SHALL transition only to waiting_authoring, waiting_runner, blocked, cancel_requested, cancelled or a settled terminal result. waiting_authoring and waiting_runner SHALL each transition only to active, blocked, cancel_requested, cancelled or a settled internal-failure result. Blocked without cancel intent MAY resume only after explicit valid Resume; blocked with cancel intent SHALL follow the read-only reconciliation requirement. Other transitions fail without mutation.

#### Scenario: Active and waiting transitions are bounded
- **WHEN** a waiting run attempts to enter succeeded directly without a settled result
- **THEN** the transition is rejected without changing state

### Requirement: Terminal results require settled or risk-accepted work

Resumable terminal states SHALL transition only to active/a waiting state on explicit Resume. Unexpected internal errors or an all-shot-failed outcome SHALL produce failed; actionable provider/input errors SHALL produce blocked. No terminal result SHALL be declared while an owned remote attempt remains unresolved, unless its unknown outcome was explicitly risk-accepted under the separate retry contract; unknown history SHALL never be mislabeled as a verified failure.

#### Scenario: Terminal results require settled or risk-accepted work
- **WHEN** the last owned attempt remains unresolved without risk acceptance
- **THEN** the run cannot declare a terminal result or label the unknown attempt failed

### Requirement: Persisted cancellation wins the next transition

Persisted cancel intent SHALL take precedence at the next state transition, retaining completed outputs.

#### Scenario: Persisted cancellation wins the next transition
- **WHEN** Cancel commits while a completed output is being settled
- **THEN** the output is retained and cancellation takes precedence over further scheduling

### Requirement: Blocked cancellation recovery is read-only

Probe exhaustion or unknown outcome during cancel_requested SHALL enter blocked with cancel intent and attempt/shot evidence retained. Explicit Resume SHALL perform fenced read-only reconciliation: outage/unknown stays blocked, a verified running attempt returns to cancel_requested, and verified settlement becomes cancelled. The run SHALL never activate or dispatch; risk-accepted retry is forbidden until cancellation settles. Repeated Cancel SHALL be idempotent and SHALL not poll.

#### Scenario: Cancellation outage retains intent
- **WHEN** all three ComfyUI probes fail while cancellation is pending
- **THEN** the run is blocked with cancel intent and attempt/shot evidence retained
- **AND** no new prompt is dispatched and repeated Cancel does not poll

#### Scenario: Explicit cancellation reconciliation does not resume work
- **WHEN** the user invokes Resume for a blocked run with pending cancel intent
- **THEN** outage or unknown outcome remains blocked, a verified running attempt returns to cancel_requested, and verified settlement becomes cancelled
- **AND** no path enters active or dispatches an attempt

#### Scenario: Pending cancellation bars risk-accepted retry
- **WHEN** the user requests an ambiguous-outcome retry while cancel intent remains pending
- **THEN** no replacement attempt is allocated or dispatched

### Requirement: Long I/O renews fenced leases

Coordinator and runner leases SHALL last 60 seconds and renew every 15 seconds while owned work is waiting on remote I/O. Expiry SHALL fence the old owner and require reconciliation, not imply a remote job stopped. Existing authoring operations SHALL retain their ten-minute lease discipline; a replacement coordinator SHALL not steal an unexpired child claim. Blocked/terminal runs SHALL not retain idle worker leases.

#### Scenario: Long I/O renews fenced leases
- **WHEN** remote I/O outlasts sixty seconds while fifteen-second heartbeats succeed
- **THEN** the current owner retains its claim and no replacement dispatch occurs

#### Scenario: A GPU job exceeds a coordinator lease interval
- **WHEN** the owner remains healthy while waiting for the job
- **THEN** independent heartbeats maintain ownership without a second dispatch
- **AND** owner loss requires remote reconciliation before another app attempt

### Requirement: Full Auto assistant admission is FIFO and bounded

Across Full Auto runs, at most one assistant HTTP request SHALL be in flight. Takes SHALL obtain the assistant slot FIFO by persisted ready timestamp and run ID, releasing it after each take. The assistant slot SHALL use a ten-minute lease with 15-second heartbeat and retain quarantine through any in-flight request timeout after owner loss. Manual and external assistant activity SHALL not be falsely described as covered by that limit.

#### Scenario: Full Auto assistant admission is FIFO and bounded
- **WHEN** two runs have ready takes and the first holds the assistant slot
- **THEN** only its request is in flight and the second waits for admission

#### Scenario: Multiple Full Auto sessions request the assistant
- **WHEN** takes from several runs become eligible
- **THEN** only one Full Auto request is in flight and persisted FIFO order selects subsequent takes

#### Scenario: A local request outlives the persisted quarantine deadline
- **WHEN** owner loss quarantines a physical assistant POST and event-loop delay prevents its timeout cancellation from finishing before the persisted deadline
- **THEN** its local completion fence prevents another Full Auto POST until the transport and cancellation cleanup finish
- **AND** the original reservations remain consumed and manual assistant transport remains unchanged

### Requirement: Runner admission skips ineligible waiters

Runner-ready sessions SHALL be admitted FIFO, skipping cancelled/blocked/expired work, and execute serially once admitted.

#### Scenario: Runner admission skips ineligible waiters
- **WHEN** the oldest runner-ready run is blocked and the next is eligible
- **THEN** the eligible run is admitted without the blocked run retaining priority

### Requirement: External queue work delays local dispatch safely

Observed external ComfyUI queue work SHALL keep a new dispatch waiting without monopolizing an idle runner claim. The application SHALL never clear, reorder or interrupt unrelated jobs. It SHALL disclose that external clients can race queue inspection; local claims SHALL not promise exclusive remote-server control. External queued time SHALL not consume the executing-shot timeout.

#### Scenario: External queue work delays local dispatch safely
- **WHEN** another client has a queued job
- **THEN** Full Auto waits without clearing that job or consuming its own executing-shot timeout

### Requirement: The existing feature flag gates Full Auto

Full Auto SHALL use the existing resource_planning_enabled configuration (default true) with IDEVGEN_RESOURCE_PLANNING_ENABLED precedence. Enabled SHALL not imply user authorization. Disablement SHALL block the next new Full Auto dispatch. After re-enabling, explicit Resume SHALL be required for blocked runs without pending cancel intent; reads, Cancel, read-only cancellation reconciliation and safe in-flight settlement SHALL remain available.

#### Scenario: The existing feature flag gates Full Auto
- **WHEN** the environment disables planning while configuration enables it
- **THEN** new Full Auto work is refused and read/cancel remain available

### Requirement: Versioned authorization accounts for physical calls

The request/evidence shape SHALL use authorization_version 1, separately from server-owned policy_version resource-full-auto-v1. Policy changes SHALL not reinterpret old authorization; unsupported recorded policy SHALL block policy_changed. Initial policy SHALL permit at most two assistant HTTP dispatches per take and twice the target count per authorization, including compatibility fallback and crash-lost responses. Counters SHALL be reserved durably before requests.

#### Scenario: Versioned authorization accounts for physical calls
- **WHEN** the first assistant request requires compatibility fallback
- **THEN** both physical requests consume durable reservations under the recorded policy

#### Scenario: Crash consumes the last call allowance
- **WHEN** a provider request was dispatched but its result did not persist
- **THEN** the reservation remains consumed and further dispatch requires budget authorization if the limit was reached

### Requirement: Extra assistant calls require new allowance

Invalid output, transport failures and refusals SHALL not cause automatic retries. Additional calls after exhausting an allowance SHALL require explicit additional authorization without resetting historical counters.

#### Scenario: Extra assistant calls require new allowance
- **WHEN** a take has consumed its two allowed requests and still needs preparation
- **THEN** no third request is sent until additional allowance is explicitly accepted

### Requirement: Authorization expiry stops new dispatch

Initial authorization SHALL expire seven days after the preflight scope_issued_at timestamp, with the exact deadline preserved at creation. Expiry SHALL stop new dispatch and settle/reconcile current work before blocking authorization_expired, without deleting evidence or automatically rerolling images. Waiting runs SHALL use the same deadline. Blocked records SHALL remain available without background polling or idle worker ownership.

#### Scenario: Authorization expiry stops new dispatch
- **WHEN** the seven-day preflight deadline passes while a run waits
- **THEN** new dispatch stops and retained work is settled or reconciled before blocking

#### Scenario: Waiting authorization expires
- **WHEN** a run reaches its deadline before a resource becomes available
- **THEN** it blocks without dispatch and offers explicit renewal or cancellation

### Requirement: Consent shows bounded usage and deadline

The UI SHALL show call allowances and expiry before consent and SHALL not represent request counts as a guaranteed monetary cap.

#### Scenario: Consent shows bounded usage and deadline
- **WHEN** the consent panel renders an eligible twelve-take scope
- **THEN** it displays the call allowance and exact expiry without claiming a monetary cap

### Requirement: Provider failures use structured classifications

Missing assistant configuration SHALL block assistant_unavailable before calling a provider. Transport and rate-limit failures SHALL produce assistant_transport_error or assistant_rate_limited; explicit Resume SHALL respect a supplied Retry-After. A structured refusal/content-filter signal SHALL produce assistant_refused with brief/stage revision or manual-authoring remedies.

#### Scenario: Provider failures use structured classifications
- **WHEN** the provider returns HTTP 429 with Retry-After
- **THEN** the run blocks assistant_rate_limited and Resume respects the supplied delay

#### Scenario: Provider explicitly refuses a take
- **WHEN** the provider returns an explicit refusal signal
- **THEN** the run blocks with assistant_refused, retains completed work and offers correction or manual authoring without silent stage removal

### Requirement: Unclassified prose is not a provider refusal

Unclassified malformed output SHALL remain writer_output_invalid; refusal SHALL not be guessed from arbitrary prose or retried through policy-evading rewrites.

#### Scenario: Unclassified prose is not a provider refusal
- **WHEN** the response is malformed prose without a structured refusal signal
- **THEN** the result is writer_output_invalid and no policy-evading retry occurs

### Requirement: ComfyUI outages allow bounded read-only recovery

An unavailable ComfyUI SHALL permit at most three additional read-only probes after 5, 15 and 30 seconds, persisting the count across restarts, then block comfy_unavailable. Probes SHALL never resend a prompt. An observed busy queue SHALL be polled no more than once per 15 seconds until admission, cancellation or expiry. Connectivity recovery SHALL not clear execution_outcome_unknown without evidence of that attempt's outcome.

#### Scenario: ComfyUI outages allow bounded read-only recovery
- **WHEN** ComfyUI remains unavailable across a backend restart
- **THEN** the persisted 5, 15 and 30 second probes are not reset and no prompt is resent

#### Scenario: Startup has no reachable generation server
- **WHEN** a recovered run cannot read ComfyUI state
- **THEN** bounded read-only probes end in an actionable blocked state without a queue submission

### Requirement: Changed-scope Resume binds the displayed correction

For blocked runs without pending cancel intent, changed inputs, policy, deadline or budgets SHALL require an old/new scope comparison before Resume. The backend SHALL bind its digest to current run/plan revisions. Resume SHALL submit that digest with accept_changed_scope true; the backend SHALL recompute it atomically before storing a linked replacement authorization.

#### Scenario: Changed-scope Resume binds the displayed correction
- **WHEN** a user corrects the profile before Resume
- **THEN** the panel compares old/new scope and acceptance stores a linked replacement authorization

#### Scenario: User corrects a conflict and continues
- **WHEN** the user resolves the reported conflict through existing reviewed CAS operations and accepts the changed scope
- **THEN** continuation validates the new revision and prepares only invalidated or remaining work

### Requirement: Stale Resume cannot bypass continuity

Stale comparison data SHALL produce 409 without new work. Unchanged Resume SHALL retain the existing authorization without routine additional consent. Frozen generated-continuity guards SHALL not be bypassed by reauthorization.

#### Scenario: Stale Resume cannot bypass continuity
- **WHEN** the plan revision changes after the Resume comparison is displayed
- **THEN** Resume returns 409 without dispatching work

#### Scenario: Correction changes after the resume panel opens
- **WHEN** a plan, source or allowance changes before the user submits the displayed digest
- **THEN** Resume rejects the stale scope and refreshes the comparison without dispatching work

### Requirement: Remote identity requires corroborated evidence

Full Auto SHALL prefer a persisted client-generated UUID as prompt_id only when support is verified for the configured server. An acknowledgement SHALL match that ID. Unknown/older servers SHALL use recorded prefix, graph and correlation metadata with queue/history/output evidence. A filename alone SHALL not establish output ownership; absent or multiple verified matches SHALL block execution_outcome_unknown.

#### Scenario: Remote identity requires corroborated evidence
- **WHEN** legacy recovery finds two outputs matching only the filename prefix
- **THEN** it blocks execution_outcome_unknown instead of adopting either output

### Requirement: Client prompt IDs do not authorize replay

Client IDs SHALL not be treated as remote idempotency keys and SHALL never justify automatic replay of an uncertain POST.

#### Scenario: Client prompt IDs do not authorize replay
- **WHEN** a client UUID was sent but acknowledgement was lost
- **THEN** the same uncertain POST is not automatically repeated

#### Scenario: Server accepts repeated client IDs
- **WHEN** an attempt acknowledgement is lost on a server that accepts caller IDs
- **THEN** recovery reads the existing identity and never assumes a second POST is deduplicated

### Requirement: Cancellation uses verified targeting or draining

Remote cancellation SHALL require verified atomic targeting of the owned prompt. A separate queue-running check followed by global interrupt SHALL be prohibited, as SHALL an assumed-safe targeting parameter on an unverified implementation. If safe targeting is unavailable, cancellation SHALL stop new scheduling and drain the owned attempt, clearly displaying that the current generation must finish.

#### Scenario: Cancellation uses verified targeting or draining
- **WHEN** the server has no verified atomic targeted-cancel capability
- **THEN** new scheduling stops and the UI shows that the current generation must finish

#### Scenario: An unrelated job starts during cancellation
- **WHEN** the owned job completes between inspection and cancellation
- **THEN** a verified atomic targeted operation affects no unrelated job, or the application uses draining without any interrupt

### Requirement: Ambiguous retry requires reachable queue evidence

Timeout/outage SHALL leave an unresolved attempt quarantined, not silently requeued or marked settled. Capability discovery SHALL not generate images or issue destructive interrupt probes. The existing user-authorized ambiguous-outcome retry SHALL require a current reachable queue check with no matching active attempt, a duplicate-work warning and accept_unknown_outcome_risk.

#### Scenario: Ambiguous retry requires reachable queue evidence
- **WHEN** the user requests an ambiguous retry while the queue is unreachable
- **THEN** the unresolved attempt stays quarantined and no replacement dispatch occurs

### Requirement: Risk acceptance preserves unknown-attempt history

An explicitly risk-accepted retry SHALL record prior risk-accepted unknown evidence and allocate a new attempt ID on the same shot, without claiming the earlier attempt did not execute. This SHALL not waive deletion or rollback guards for potentially active remote work.

#### Scenario: Risk acceptance preserves unknown-attempt history
- **WHEN** the user authorizes retry after the required queue check and warning
- **THEN** a new attempt uses the same shot while prior unknown evidence and deletion guards remain

### Requirement: Deletion protects active dependencies

Deleting a Full Auto session, linked shot or dependent model/workflow/look/profile SHALL fail with 409 full_auto_in_use while a nonterminal run or unresolved remote attempt depends on it. Guards SHALL cover cascading and direct deletion paths and identify affected sessions with a cancel-and-settle remedy. Once safe deletion is permitted, existing snapshot retention and loud folder-deletion failure behavior SHALL remain. Out-of-band loss SHALL block dependency_missing.

#### Scenario: Deletion protects active dependencies
- **WHEN** model deletion would cascade into a nonterminal Full Auto session
- **THEN** it returns 409 full_auto_in_use and preserves dependent rows

#### Scenario: Model deletion would cascade a preparing session
- **WHEN** a model delete races an active Full Auto run
- **THEN** one serialized decision refuses deletion with affected session details and preserves durable work

### Requirement: Archived looks retain active Full Auto dependencies

Archiving a saved look SHALL retain the exact saved-look version and automation-profile rows used by a Full Auto run. Archiving SHALL not cancel, block, or invalidate that run. Existing Full Auto dependency checks SHALL continue to resolve the retained exact identities; destructive deletion of dependent rows remains subject to the existing `full_auto_in_use` protections.

#### Scenario: Archive a look used by an active run
- **WHEN** a user archives a look referenced by an active or unsettled Full Auto run
- **THEN** archiving succeeds and removes the look from active library listings
- **AND** the run's exact look and profile dependencies remain present and valid

### Requirement: Numeric ID reuse does not revive stale authority

A deleted and later reused numeric session ID SHALL not grant authority to an old run/epoch/fence.

#### Scenario: Numeric ID reuse does not revive stale authority
- **WHEN** a deleted numeric session ID is reused and an old worker responds
- **THEN** its old run/epoch/fence cannot modify the new session

### Requirement: Rollback restores isolated pre-upgrade data

Supported rollback SHALL restore a verified pre-upgrade backup into a separate data/config directory while retaining the upgraded database and images. Its preflight SHALL refuse unresolved upgraded remote work, missing backups or selection of the upgraded directory for the old binary. It SHALL not claim that a new schema marker protects against old code that never checks it. An in-place downgrade of the upgraded database SHALL remain unsupported.

#### Scenario: Rollback restores isolated pre-upgrade data
- **WHEN** rollback selects the upgraded data directory
- **THEN** preflight refuses and leaves the upgraded database and images intact

#### Scenario: Rollback targets the upgraded database
- **WHEN** rollback preflight resolves the old release's data path to the upgraded directory
- **THEN** it refuses to launch the old binary and leaves all data unchanged

### Requirement: Consent preflight is read-only

POST `/api/sessions/guided/full-auto/preflight` SHALL resolve the intended creation inputs without assistant calls, generation or durable allocations and return the complete consent summary, scope_digest and server UTC scope_issued_at.

#### Scenario: Consent preflight is read-only
- **WHEN** the browser requests the Full Auto consent summary
- **THEN** it receives scope_digest and scope_issued_at without session allocation or provider calls

### Requirement: Consent summary binds resolved dependencies

The preflight summary SHALL bind exact look/profile, room/resource/translation revisions, model/base prompt/LoRA and settings, effective workflow graph/map, canvas/framing/creative policy, count/ordered stages, provider endpoint identity/model, policy/compiler versions, call/attempt allowances and expiry seven days after scope_issued_at. Public views SHALL not reveal provider secrets or private endpoint URLs.

#### Scenario: Consent summary binds resolved dependencies
- **WHEN** the selected workflow inherits dimensions and the assistant provider is configured
- **THEN** the summary binds those effective values, source identities, allowances and exact deadline

### Requirement: Scope digests are comparison guards

The preflight scope_digest SHALL hash the canonical resolved summary; it is a comparison guard, not an ownership credential.

#### Scenario: Scope digests are comparison guards
- **WHEN** a client presents only a valid scope digest to an owned-session mutation
- **THEN** it gains no owner authority from that digest

### Requirement: Creation rejects expired or changed consent

Creation SHALL recompute and compare the preflight scope under its serialized allocation boundary, require a non-future scope_issued_at at most 300 seconds old, and refuse expired or changed scope with `409 authorization_scope_changed` before allocating a session/run/creation receipt. A changed summary SHALL be shown for a new explicit click.

#### Scenario: Creation rejects expired or changed consent
- **WHEN** a new creation uses a preflight issued 301 seconds earlier
- **THEN** it returns 409 authorization_scope_changed without allocation and offers a refreshed summary

#### Scenario: Defaults change after the consent screen resolves
- **WHEN** the effective workflow, canvas, model inputs or provider changes before the first creation request commits
- **THEN** no session is allocated and the UI refreshes the changed summary for explicit authorization

### Requirement: Replay and provider drift retain distinct boundaries

Identical committed request replay SHALL be resolved before expiry or live-dependency checks, returning the existing receipt even after the quote expires; changed request content SHALL still conflict. Provider identity/model SHALL be checked before every physical assistant dispatch and at response persistence; drift SHALL discard stale output and block provider_changed pending changed-scope acceptance.

#### Scenario: Replay and provider drift retain distinct boundaries
- **WHEN** the assistant provider changes while a request is in flight
- **THEN** the stale output is discarded and the run blocks provider_changed

#### Scenario: A committed request is replayed after quote expiry
- **WHEN** the identical creation request is retried after its preflight deadline
- **THEN** its stored session/run response is returned without a second allocation or new consent

#### Scenario: Provider changes during a request
- **WHEN** a response arrives from an authorization whose provider configuration no longer matches
- **THEN** its reservation remains consumed but its output is discarded and remaining work blocks provider_changed

### Requirement: Full Auto v1 accepts compatible t2i workflows

Full Auto v1 SHALL require a workflow explicitly classified t2i, compatible with the resolved model and mapped inputs, with no reference takes or required generation reference. Missing/empty kind, edit, guide, angles and scene SHALL fail preflight with `422 full_auto_workflow_unsupported`, offering a compatible workflow or manual flow. The existing manual reference/edit/guide paths SHALL remain unchanged.

#### Scenario: Full Auto v1 accepts compatible t2i workflows
- **WHEN** a guide workflow is selected for Full Auto
- **THEN** preflight returns 422 full_auto_workflow_unsupported and offers the manual flow

#### Scenario: An editing workflow is selected
- **WHEN** Full Auto preflight receives an edit or guide workflow without an automated reference contract
- **THEN** it refuses before allocation and preserves the manual alternative instead of promising the ownership/coverage contract for a bare edit instruction

### Requirement: Reference composition keeps its existing authority

Workflow kind alone SHALL NOT be treated as evidence that the current composer strips session context: existing reference flags and graph-kind rules still govern that behavior. Supporting automated reference acquisition is outside v1.

#### Scenario: Reference composition keeps its existing authority
- **WHEN** a manual reference-edit take is composed
- **THEN** its existing reference-flag and graph-kind rules determine context omission

### Requirement: Duplicate acceptance binds current snapshots

An unreviewed exact duplicate tuple SHALL block with duplicate_review_required and affected take IDs; v1 SHALL NOT rewrite or silently drop repetitions. Explicit review SHALL bind acceptance to the current duplicate set, plan revision and snapshot digests. Unchanged accepted repetitions MAY continue; edits changing that evidence SHALL require review again. Correction MAY instead edit affected takes with normal invalidation and budget checks.

#### Scenario: Duplicate acceptance binds current snapshots
- **WHEN** an accepted duplicate set changes after a take edit
- **THEN** the old acceptance is invalid and explicit review is required again

#### Scenario: A deliberate duplicate is accepted
- **WHEN** the user reviews the exact current duplicate set and resumes
- **THEN** the unchanged snapshots can proceed without regeneration, while a changed duplicate set invalidates that acceptance

### Requirement: Every physical assistant dispatch is fenced

Each physical assistant HTTP dispatch, including the reasoning-parameter compatibility fallback, SHALL reserve allowance and recheck owner fence, cancellation, provider and expiry immediately before sending. The shared transport SHALL expose safe HTTP status, Retry-After and structured refusal metadata to Full Auto without changing unrelated manual retry behavior or exposing raw payloads.

#### Scenario: Every physical assistant dispatch is fenced
- **WHEN** Cancel commits between the initial 400 response and compatibility fallback
- **THEN** the fallback is not sent

### Requirement: Compatibility fallback consumes its reservation

A first-call 400 fallback consumes the second reservation; any further attempt requires explicit additional budget. Unsupported/missing refusal metadata SHALL remain unclassified rather than inferred from prose.

#### Scenario: Compatibility fallback consumes its reservation
- **WHEN** the initial request and fallback both fail
- **THEN** two reservations remain consumed and no third request is sent without extra allowance

#### Scenario: Both allowed calls are consumed by compatibility fallback
- **WHEN** a first request requires fallback and the second request fails
- **THEN** both reservations remain consumed, no third request is sent, and recovery shows the additional allowance required

### Requirement: Manual runner starts share fair admission

Manual Run SHALL remain an immediate operation, not become a queued request. Shared transactional admission SHALL return `409 runner_busy` if a runner is owned or an eligible Full Auto FIFO waiter is already ready; it SHALL expose a readable waiting reason and no new run. A manual start MAY claim an idle runner when no eligible waiter precedes it. Blocked, cancelled and expired runs SHALL not reserve priority; neither path SHALL preempt admitted work.

#### Scenario: Manual Run races a ready Full Auto waiter
- **WHEN** both compete for the idle global runner claim
- **THEN** serialized admission honors the eligible FIFO waiter and manual Run returns a visible busy response without starting another runner

### Requirement: Preflight codes distinguish invalid and stale input

Additional preflight failures SHALL use the detail.code/detail.message envelope and the HTTP/code mapping in design Decision 14: insufficient_stage_count, automation_profile_not_ready, coverage_unknown, inventory_unconfirmed, appearance_progression_conflict, baseline_progression_conflict, full_auto_workflow_unsupported and framing_conflict SHALL return 422; saved_look_changed, automation_profile_changed and authorization_scope_changed SHALL return 409.

#### Scenario: Preflight codes distinguish invalid and stale input
- **WHEN** the count is below the stage count or a selected profile digest is stale
- **THEN** the first returns 422 insufficient_stage_count and the second 409 automation_profile_changed

#### Scenario: Coverage is missing for a specific stage
- **WHEN** preflight finds an unknown region in a selected aside stage
- **THEN** coverage_unknown identifies that stage/region and the Looks correction action without allocating work

### Requirement: Preflight errors expose safe remedies without writes

Errors SHALL identify safe affected fields/stages and the implemented correction action. Existing shape, not-found and feature-disablement semantics SHALL remain. Invalid requests SHALL not allocate a session, run or creation receipt.

#### Scenario: Preflight errors expose safe remedies without writes
- **WHEN** an invalid request fails preflight
- **THEN** it identifies the affected field and correction action without session/run/receipt allocation
