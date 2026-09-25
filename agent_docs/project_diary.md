# Project Diary

## Decisions and Lessons

- Repository-authored code, comments, UI, and documentation are English-only
  and must not contain personal or machine-specific data.
- `AGENTS.md`, the README/docs, source code, tests, and OpenSpec artifacts are
  the project sources of truth; bootstrap documentation records only facts
  verified from them.
- Legacy sessions and the `resource-v1` session-planning path are deliberate
  compatibility boundaries. Future work must preserve their distinction and
  the no-GPU/no-network test boundary.
- The approved `simplify-resource-session-workflow` plan is implemented in task
  order. Task 1.1 is the first real dependency and remains backend-only:
  persisted browser-import selection/file/claim state, reservations, recovery,
  and cleanup. HTTP contracts, canonical import bridging, compatibility
  adapters, and UI belong to later tasks.
- The Task 1.1 architecture uses an additive selection table plus an ordered
  selection-file table, with the active commit claim stored on the selection
  row and acquired by conditional transactional state change. This is enough
  to prove single ownership without introducing a queue or generic operation
  framework early.
- Browser-import staging must remain separate from the existing path-import
  attestation files. Cleanup targets only opaque staged files, remains
  retryable after a terminal transition, and never infers or deletes committed
  resource state.
- Filesystem deletion is not transactional with SQLite. A helper called inside
  a nested `db.transaction()` cannot delete staged bytes before the outermost
  commit: a later rollback restores the database but cannot restore the file.
  Persist the terminal transition first, clean afterward, then persist the
  verified cleanup result or warning separately.
- File finalization must keep one durable path to the bytes across every crash
  boundary. Renaming before the manifest transaction commits can leave the row
  pointing at a missing old name and recovery falsely deleting only that name.
- Bounded recovery needs deterministic forward progress. Limiting an
  unfiltered candidate query can repeatedly revisit ineligible tombstones and
  starve expired claims or cleanup failures; tests must prove both the bound and
  progress. A concurrency test must execute concurrent workers rather than
  assert the same outcomes sequentially.
- Post-commit deletion is a rule for every valid staged-byte removal path, not
  only terminal cancel/commit helpers. File removal and expiry branches must
  also preserve bytes when an enclosing transaction rolls back; checking every
  `_safe_delete_file` and `_clean_selection_files` caller is the reliable
  acceptance method.
- A post-commit callback is not durable work by itself. Recovery queries must
  explicitly select the database state left between a successful commit and a
  process stopping before its callback runs. For file removal, that means an
  open selection can own a `removed` file with pending cleanup; startup,
  record-local, and opportunistic recovery must all make bounded progress on
  that state without waiting for selection expiry.
- Task 1.1 acceptance requires reproducing the callback-loss crash window, not
  only exercising the normal callback. The accepted recovery keeps the removed
  file outside the manifest, preserves revision, counters, and invalidated
  preview fields, and changes only cleanup evidence while startup, record-local,
  and bounded opportunistic paths remove the recorded staged bytes.
- Task 1.2 is an HTTP/projection boundary over Task 1.1, not the canonical
  import bridge. Upload replay must prove exact incoming-byte identity after
  response loss; matching only `upload_id` and display filename is insufficient.
  The public `SelectionView` must be an explicit whitelist that never serializes
  database rows, paths, fingerprints, claim data, cleanup internals, or
  nanosecond timestamps. Preview/commit integration, compatibility parsing, and
  frontend stale-response handling remain in Tasks 1.3, 1.4, and 1.5.
- Green route tests are not semantic proof when they encode optional values that
  OpenSpec requires. Task 1.2 review must explicitly probe omitted revisions,
  empty/extra PATCH bodies, framework-generated validation errors, concurrent
  replay ownership, finalize failures, and persisted nested private/unsafe
  values. A whitelist of top-level `SelectionView` keys is insufficient if
  nested preview or commit dictionaries are passed through unchanged.
- Framework type coercion and multipart parameter binding are part of the HTTP
  trust boundary. A typed `int` model can turn JSON booleans, strings, or
  integral floats into a revision before a domain validator sees them, while a
  single `UploadFile`/`Form` signature can silently select one duplicate part.
  Acceptance must probe raw HTTP shapes and zero-mutation outcomes, not only
  call the post-binding model or domain helper.
- A denylist of private-looking key names is not a canonical projection.
  Unknown keys and path values under innocuous names still escape. Preview and
  commit results need explicit shape-aware allowlists plus persisted sentinel
  tests for both branches.
- A safe projection must preserve the repository's actual authoritative
  canonical report shape. A second test-shaped schema can both discard real
  fields and reasons and add speculative fields, even when its top-level
  allowlist looks restrictive.
- Persisted preview evidence must be validated, not coerced: preview tokens are
  opaque strings, manifest digests are lowercase hexadecimal strings, and
  committability is boolean. Invalid stored evidence fails closed rather than
  becoming a plausible public value.
- Equivalent transport forms belong to one trust boundary. Media types require
  semantic case-insensitive parsing, and every revision transport must reject
  booleans, floats, and strings before framework coercion changes their type.
- Client filenames, invalid identifiers, and multipart field names are
  untrusted display and error inputs. Public filenames must be display-only,
  and stable errors must not echo path-like client data.
- A synthesized public view after an authoritative lookup fails can conceal an
  ownership or transaction-visibility defect. Idempotent replay must return the
  persisted authoritative result or fail explicitly; a fallback response is
  not proof of the invariant.
- Task 1.2 repair 3 must use transport-specific decoding into shared canonical
  values: JSON revisions retain their original type, query revisions use an
  explicit decimal grammar, and both meet one safe-integer range invariant.
- Canonical report safety belongs beside `resource_service.safe_report`.
  SelectionView may validate and copy that exact shape, but must not maintain a
  second legacy/speculative projection or partially repair malformed evidence.
- Corrupt persisted public evidence is a server-integrity failure. Return one
  fixed path-free error without a fabricated current view; never turn invalid
  storage into zeros, strings, booleans, empty lists, or partial reports.
- A validator placed beside a canonical producer is not automatically the same
  contract. It must accept every genuine producer output and reject impossible
  enum, digest, count and phase combinations; otherwise it is still a second
  hand-maintained schema with a new location.
- Validate target values before their CAS transaction. Rejecting them only when
  serializing the response can commit invalid data and a revision bump, then
  return `500`, violating both stable `422` behavior and zero-mutation safety.
- Fail-closed state validation includes relationships, not only scalar types:
  committed state requires its atomic commit result, and an empty serialized
  result is malformed evidence rather than an absent nullable value.
- A durable idempotency decision is incomplete if the HTTP response performs a
  second mutable recovery read. Create/replay now projects its authoritative
  view while the durable decision remains serialized, so concurrent equivalent
  requests converge through one response contract.
- Compatibility wrappers must fail closed after durable state disappears. A
  previously canonical view is still stale evidence after an authoritative
  reload returns no row and must never become a success fallback.
- Task 1.2 aggregate acceptance requires both semantic probes and sustained
  post-load concurrency. Thirty synchronized five-request batches passed after
  the focused suites before the task was marked complete.
- Task 1.3 browser attestations must bind the complete canonical preview,
  selection identity, revision, manifest digest, and effective targets. Browser
  commits therefore use a SQLite claim rather than consuming the legacy
  filesystem `.claimed` marker; path imports retain that marker's one-shot
  semantics.
- Accepted resources, derived coverage, and the selection `commit_result` must
  remain inside one outer SQLite transaction. Crashes before durable commit
  leave zero partial rows and a lease-recoverable selection, while crashes after
  commit replay the stored result without re-import.
- Response loss is a durable-state case, not a new import request. Replay must
  return the authoritative stored result, preserve accounting and revision
  identity, and leave cleanup as retryable post-commit work that cannot remove
  committed resources.
- Task 1.3 acceptance required adversarial fingerprint/attestation mutation,
  ownership-loss, exception-sanitization, atomicity, concurrency, crash-window,
  cleanup, and legacy compatibility probes in addition to the repository gates.
- Browser compatibility is an adapter boundary: collection-only `items`
  envelopes are relaxed only in browser mode, while legacy parser
  classification and path/API/CLI preview serialization remain unchanged.
- An auxiliary choice is valid only when it matches candidates recalculated from
  fingerprint-verified staged bytes. An allowlisted kind is not sufficient;
  empty, out-of-set, or changed choices must fail closed before resource writes.
- Browser-only preview metadata belongs in the browser attestation body and
  must not be added to the legacy serialized preview contract.
- Task 1.5 acceptance uses two separate ordering authorities: `selection_revision`
  decides ordering within one active selection, while an epoch rejects responses
  from a superseded selection. A request that started earlier can still win when
  it returns the higher server revision.
- The browser must reconstruct the closed SelectionView allowlist before storing
  it in React state. Import requires the complete current preview tuple, and HTTP
  202 remains visibly committing until status returns the durable committed view.
- Task 1.6 is formally closed at 6 of 72 tasks after independent backend and
  real frontend contract acceptance. Its integration gate covers stale and
  terminal selection states, duplicate accounting, crash recovery, cancel/commit
  races, safe public response shapes, and an actual payload-free library-list
  fixture. At that checkpoint, Task 2.1 was the next pending task.
- The final SafeCommitReport boundary is structural in the frontend: exact public
  keys, JSON types, nullability, arrays/objects, JavaScript-safe integers, and
  explicit allowlisted reconstruction. Buckets, kinds, classifications,
  identifier vocabulary, library naming, importer accounting, and digest
  relationships remain backend semantics. A present invalid `preview.report`
  rejects the complete SelectionView; there is no canonical-to-legacy fallback.
- A superseded browser attestation may remain as a physical orphan without
  authority. The durable selection row determines the usable preview, TTL is a
  logical expiry boundary, terminal cleanup removes the authoritative token,
  and Task 1.6 introduces no global filesystem sweep.
- Task 2.1 is formally closed after independent acceptance. The reusable opt-in
  `RequestLimitRoute` applies the authoritative 10 MiB actual-streamed-body
  limit before JSON/Pydantic or multipart parsing and writes, including missing
  or false Content-Length. Pre-read replay never performs a second network
  read; incomplete disconnects stop before downstream work, while legacy
  endpoints remain unaffected. The closure adds 27 tests and brings the
  reproducible backend collection to 1,924 tests.
- Task 2.2 is formally closed after independent acceptance. The shared
  `backend.enhance` transport adds field-preserving `run_structured(...)` while
  keeping historical `run(...)`, `clean`, `clean_fields`, and callers intact.
  Structured output preserves fields, values, types, nested arrays, and provider
  order, requires a JSON object root, and rejects duplicate keys. URL, auth,
  timeout, and reasoning fallback are shared; structured retry preserves
  `response_format`. Error sanitization excludes `llm_key`, Authorization, URL
  credentials/query secrets, provider bodies, and exception strings. The 25 new
  tests bring the reproducible collection to 1,949. Future consumers remain
  out of scope; Task 2.3 is next.
- Task 2.3 is formally closed after independent acceptance. The authority
  matrix blocks public raw begin/complete for every plan with authoring
  metadata, blocks automatic direct and bulk preparation, and closes the
  direct Python finalizer bypass before persistence or assistant work.
  Automatic readiness is reserved for the future fenced `prepare_takes`
  operation. Validated manual preparation, historical pre-authoring expert
  preparation, and legacy composition remain compatible. Malformed authoring
  fails closed, `schema_version` accepts only integer `1`, and bulk preflight
  prevents partial mutation. The closure adds 22 authority tests and the
  reproducible collection is 1,971 tests; Task 2.4 is next.
- Task 2.4 is formally closed after independent acceptance. Authoring evidence
  is derived server-side, manual preparation persists only an internally sealed
  result, and raw `complete_preparation` remains restricted to pre-authoring
  plans. Automatic preparation remains blocked until the future fenced
  operation. The read-only validator rejects malformed, non-object, strict-type,
  and closed-shape evidence; Review, Recovery, Approve, and Submit fail closed,
  with sanitized HTTP diagnostics and zero-write rejection paths. The closure
  was verified against 2,046 collected tests; Task 2.5 remains pending.
- Task 2.5 is formally closed after independent acceptance. The offensive A-L
  matrix covers forged final prompts, effective state and take choices,
  assistant output/provenance/request evidence and digests, automatic
  `manual_completion` rejection, locked/shared/resource-derived overrides, raw
  begin/complete compatibility, and pre-authoring expert completion. Arbitrary
  historical provenance remains compatible only for plans without authoring
  metadata. No product code changed; the group 2 closure was verified against
  2,049 tests. Task 3.1 is next.
- Task 3.1 is formally closed after independent acceptance. Selected
  translation-map content is resolved server-side by `selection_id`, `file_id`,
  and `expected_revision`; the staged file is the exclusive byte authority and
  one file handle materializes `raw_bytes` for size, mtime, SHA-256, fingerprint,
  candidate inspection, UTF-8, and JSON checks. This removes the TOCTOU window.
  Preview delegates to `preview_translation_map()`, apply delegates to
  `apply_translation_map()`, and `apply_revision_translation()` is not used.
  The historical bulk validator remains the semantic authority, preserving
  authorization, canonical digest, attestation, and library-fingerprint
  checks. Both routes use the real 10 MiB `RequestLimitRoute` boundary;
  missing/false `Content-Length` is rejected with 413 before parsing/writes,
  including exact-boundary and plus-one tests. Invalid selected maps fail
  through sanitized controlled errors. The verified collection is 2,063 tests;
  Task 3.2 was next.
- Task 3.2 is formally closed after independent acceptance. `GET
  /api/resources/libraries/{library_key}/translations/rows` exposes a safe
  source-backed projection without raw payloads, paths, or internal
  fingerprints. `buildTranslationMapFromRows(rows)` creates a direct
  `translation_map` while preserving scalar/list contracts and keeping list
  items as independent scalar entries. Required already-English strings get
  explicit identity translations; optional English strings do not. Compatible
  duplicate sources merge deterministically, while incompatible translation or
  shape duplicates produce explicit diagnostics; there is no last-write-wins.
  Valid sidecars are preserved. Corrupt sidecars are diagnosed and do not
  produce editable rows, and public diagnostics are sanitized.
- Task 3.2 manual Preview uses canonical bulk `/translations/preview` and
  manual Apply uses canonical bulk `/translations/apply`. Attestation, map
  digest, and library fingerprint remain authoritative. The
  `apply_revision_translation` path and single-revision endpoint are not used.
  Editing, reload, or Apply errors invalidate preview/token authorization, and
  late responses cannot reactivate stale authorization. Task 3.1's selected-map
  flow and `map_path`/direct-map compatibility remain intact. The accepted
  closure was verified against 2,072 backend tests and 362 frontend tests;
  Task 3.3 is next.
- Task 3.3 is formally closed after independent acceptance. Optional proposals
  use `POST /api/resources/libraries/{library_key}/translations/proposals` and
  accept at most twenty identity-only source entries. Scalar values and list
  items are separate entries. The identity is `source_id`,
  `content_digest`, canonical `field`, `source_shape`, and `list_index`; source
  text and eligibility are resolved server-side from the canonical projection.
  Only pending, authorized `ROLE_DESCRIPTIVE_INPUT` rows are eligible, existing
  translations are not implicitly overwritten, and `identity_required` rows do
  not consume the assistant.
- The proposal transport reuses `backend.enhance.run_structured(...)` and keeps
  provider keys (`entry-0` through `entry-N`), canonical/list order, and
  repeated-source identities server-owned. Strict input validation rejects
  unsolicited fields and coercion of `list_index`; schema, stale, and
  ineligible errors are sanitized. Provider output must be exactly the
  expected entry keys mapped to English strings; extra, missing, malformed, or
  non-English output rejects the entire proposal.
- Proposals are write-free until the existing reviewed flow reaches explicit
  Preview and explicit Apply: no sidecar, attestation, implicit Preview/Apply,
  or direct revision shortcut occurs at generation. Manual edits, row reloads,
  global/cross-library reloads, and a second Suggest fence stale responses;
  global reload clears `proposalBusy`. The feature flag reuses
  `is_resource_planning_enabled()` and returns HTTP 503 before body/schema,
  source, or provider processing when disabled; `run_structured` is not called.
  Tasks 3.1 and 3.2 remain compatible, and Task 3.4 was next.
- Task 3.4 is formally closed after independent acceptance. The Resource
  Browser separates Imported from Needs translation, Ready, and Needs source
  correction, with direct Create session, Translate, and Inspect source/
  Re-import actions. Apply and import force readiness refresh. Safe readiness
  projection omits raw payloads and arbitrary coverage/translation markers;
  invalid persisted sidecars remain pending and inspectable. Imported identity
  is authoritative only from `commit_result.files[].accepted` and the complete
  `(library_key, source_id, content_digest)` tuple.
- Proposal, manual-preview, and map-preview fencing rejects stale responses.
  `mapPath` remains editable during Preview and Apply; `mapOperation` permits
  a real A-to-B edit and a new Preview while A is pending, ignores late A,
  preserves B authorization, keeps Apply in flight, and blocks incompatible
  actions. Apply success still requires the readiness refresh. The closure was
  verified with 416 frontend tests, 79 focused resource-service tests, 10
  privacy tests, 36 shoot tests, a successful build, strict OpenSpec
  validation, and clean diff checks. Task 3.5 was pending at that checkpoint.
- Task 3.5 is formally closed after independent acceptance. This test-only
  closure verifies manual source-backed translation without a map or assistant,
  distinguishes missing translation from invalid source, diagnoses invalid
  sidecars, and proves provider failure recovery through valid manual Preview
  and explicit Apply. Malformed provider output is rejected as a whole;
  unauthorized, stale, and ineligible proposals are rejected before the
  provider, with the 20/21 bound and complete independent revision snapshots.
  Compatible duplicate sources merge deterministically while conflicting
  duplicates block preview. Existing stale attestation/library and selected-map
  coverage checks remain intact. Real HTTP bodies above 10 MiB, including
  missing or false `Content-Length`, fail before parsing/writes. The workflow
  remains write-free until Apply and performs no automatic Apply, session, or
  generation. No product defect was found. Group 3, Actionable translation
  readiness, is complete; at that checkpoint Task 4.1 remained pending and
  had not started.
- Task 4.1 is formally closed after independent acceptance. The closed
  authoring-v1 validator enforces exact shapes and policy unions, while generic
  saves echo server-owned blocks and reconcile changed shared-state metadata
  against the current CAS winner without discarding historical evidence.
  Frontend normalization and payload construction preserve authoring, empty
  take arrays, and missing or invalid IDs without fabricating replacements. A
  synchronized concurrent-save regression proves one CAS winner and atomic
  persisted state. The verified gates include 2,153 backend tests, 426
  frontend tests, focused session-plan/preparation-authority/resource-
  preparation tests, privacy and shoot checks, frontend build, strict OpenSpec
  validation, and clean diff checks. Task 4.2 is next and remains unstarted.
- Task 4.2 is formally closed after independent acceptance. Reusable strict
  count validation accepts integer authoring counts 1–500 and rejects bool,
  floats, numeric strings, and invalid values without coercion; brief validation
  is shared and capped at 2,000 characters. Apply the 500 ceiling only when an
  authoring plan grows, using the current persisted plan inside the winning CAS.
  Existing authoring plans above 500 may remain or shrink but cannot grow;
  expert/pre-authoring and legacy behavior stays uncapped, and twenty is not a
  plan limit. The `10 ** 5000` regression protects against exception-message
  formatting raising `ValueError`. The 2,172-test backend suite, focused plan
  tests, privacy/shoot checks, strict OpenSpec validation, and diff checks
  passed. Task 4.3 is next.
- Task 4.3 is formally closed after independent acceptance. Resolve the primary
  workflow server-side from Advanced override then model default; validate the
  existing primary/reference compatibility rules and persist a server-owned
  four-field binding (`workflow_id`, `kind`, `graph_digest`, `node_map_digest`).
  Validate drift at preparation, approval, and submission, including inside
  write transactions. The binding survives later model-default changes;
  changing the primary workflow requires a new authoring session, while
  `reference_workflow_id` stays separate. Preserve pre-authoring and legacy
  behavior. Task 4.4 is pending.
- Task 4.4 guided creation passed independent acceptance and is formally
  closed. Normalize all server defaults before hashing the
  request body, exclude `request_id` from that digest, and store the exact
  success response rather than reconstructing it from mutable session state.
  Persist the idempotency record, session, initial plan, stable take IDs, and
  response in one `BEGIN IMMEDIATE` transaction so response loss, concurrent
  retries, and injected failures cannot create duplicates or orphans.
- A new creation path must preserve established plan semantics, not only schema
  validity. The guided initial plan therefore uses
  `detect_resource_constant_conflicts` before persistence; an unconditional
  empty conflict list hid real look/resource conflicts even though the plan was
  otherwise valid.
- Intermittent failures in two existing concurrent session-plan tests were not
  causally attributable to Task 4.4. Targeted order/stress runs passed and the
  final 2,256-test suite was green. Do not broaden a bounded feature task into a
  shared SQLite concurrency repair without a reproducible mechanism.
- Task 4.5 keeps one effective projection boundary for session detail, list,
  and search: resource-v1 reads validated plan constants, while legacy sessions
  continue reading their row columns. Search must filter the projected values,
  not stale storage columns.
- A resource-v1 session without a readable plan is inconsistent state, not a
  compatibility fallback. Public rows expose a stable diagnostic and null
  effective constants so stale legacy text cannot leak into display or search.
- The generic session PATCH accepts `look` only to detect and reject resource-v1
  attempts with `plan_field_required`; it does not add legacy look mutation.
  Resource-v1 look and wardrobe remain plan-CAS owned.
- Task 4.6 shared summaries reuse the exact selected-revision loader and
  authorized descriptive-input resolver used by preparation. Keep source
  descriptions independent of empty additional constraints, project effective
  values separately from origin metadata, and return fixed diagnostic codes
  instead of raw resource errors when a scene cannot be authorized.
- Task 4.7 checks generated continuity inside the winning plan CAS. Compare
  the full saved-look snapshot as canonical JSON: its content digest alone
  omits logical identity and version, while array order and aside wording are
  significant. Keep the existing constant-change test separate so this freeze
  does not introduce Task 7.3 automatic invalidation. A submitted shot is
  pending while its linked prepared take is already generated, so the freeze
  begins at queueing; scoped wardrobe events remain editable without rewriting
  generated evidence.
- Task 4.8 found that closed policy-shape validation did not compare fixed
  values with explicit take choices. Check that conflict inside the winning plan
  CAS after the generated-continuity guard and before writes, so conflicting
  drafts fail without a revision while existing continuity refusals retain
  precedence. API tests should assert the real response shape and persisted
  state, not only the validator result.
- Task 5.1 stores authoring-operation identity and ordered progress in an
  additive table. A partial unique index excludes concurrent active and
  cancel-requested operations across kinds; terminal rows release the claim
  but retain their request IDs and evidence. A SQL trigger must also prevent
  identity and original-request rewrites: insertion uniqueness alone let a
  terminal row change its request ID and allowed the old ID to be reused.
  Resume may still advance state, fencing, lease, and progress on that row.
- Task 5.2 starts and replays claims inside one transaction and keeps status
  read-only. Shared suggestions select unresolved origins, not false-like
  effective strings: an empty value with `origin=user` is a completed choice.
  A disabled start returns 503 even for an identical replay; GET status remains
  available. OperationView does not expose request digests or fencing state.
  Execution, lease renewal, and recovery remain separate later tasks.
- Task 5.3 binds each renewed lease to a signed snapshot of effective operation
  inputs. Rechecking only the start-request digest and current validity missed
  a valid translation edit under the same resource content digest and plan
  revision. Both operation kinds now compare the renewed input fingerprint
  inside the fenced response transaction; a copied ticket with edited fields
  fails signature validation. Result and ordered progress commit together.
  Actual assistant workers and prepared-take snapshots remain later tasks.
- Task 5.4 recovery preserves completed operation results while expiring prior
  owners, finalizing cancellation, and incrementing fencing on resume. A
  durable input digest is needed in addition to plan revision: authorized
  translations can change without changing that revision or resource content
  digest. Older rows without that digest must reject resume instead of
  reconstructing unprovable authority from current inputs. Recheck the live
  feature flag inside renewal and response write transactions and commit
  cancellation before returning a refusal. Real assistant workers and the
  prepared-take snapshot bridge remain in later tasks.
- Task 5.5 stores reviewed shared-suggestion acceptance and its replay receipt
  in the same transaction as plan CAS. Persist exact proposal input/output and
  edited values with per-field origin; pending proposals do not change the plan.
  Keep assistant request parameters on an allowlist of non-secret transport
  controls before saving or accepting evidence, so credentials cannot enter
  the plan or acceptance response. Replays use the stored result and create no
  second revision. The real suggestion worker remains Task 6.3.
