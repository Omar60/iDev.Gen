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
