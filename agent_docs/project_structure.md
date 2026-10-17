# Project Structure

Verified baseline from the repository layout and application entry points.

## Directory Layout

- `backend/` contains the Python application and domain modules.
- `frontend/src/` contains the React UI, hash-based routing, views, helpers,
  and frontend tests. `frontend/dist/` is generated build output.
- `tests/` contains the backend pytest suite and ComfyUI test doubles.
- `scripts/` contains import, backup, mining, analysis, and operational tools.
- `docs/` contains user-facing guidance; `README.md` is the short project
  overview and documentation index.
- `openspec/` contains the main specifications and change artifacts.
- `data/` and `config.json` are local runtime state and configuration and are
  not repository deliverables.

## Modules and Responsibilities

- `backend/main.py` defines the FastAPI application, request models, HTTP API,
  configuration handling, and static frontend serving.
- `backend/db.py` owns SQLite schema, migrations, transactions, and queries.
  Its `q`/`one` reads share the existing re-entrant lock with transaction scopes.
- `backend/comfy.py` wraps the ComfyUI HTTP API and detects/applies workflow
  node mappings. `backend/runner.py` queues and serializes session shots and
  moves completed images into session folders.
- Session authoring is split across `session_plan.py`,
  `resource_preparation.py`, and prompt/resource modules. Resource import,
  immutable storage, readiness, and translations are handled by the
  `resource_*` modules. `backend/resource_full_auto.py` persists Full Auto run
  state, fenced claims, immutable authorizations and control receipts, status
  projections, and shared-write guards. Session-scoped API routes in
  `backend/main.py` expose run discovery/status, Cancel, Resume, and read-only
  scope comparison. Actions use UUID request IDs and run/current-plan revision
  CAS; exact historical requests replay their stored response. Changed-scope
  Resume requires a fresh digest/timestamp quote and explicit acceptance, then
  appends a linked immutable authorization. Cancel persists intent, fences child
  work, cancels linked targets with no execution attempt in place, and retains
  completed outputs. Owned ComfyUI attempts drain through read-only evidence;
  no atomic targeted-cancel capability is verified, so Full Auto never uses a
  global interrupt or queue clear. Repeated Cancel does not poll. Three
  additional persisted read-only ComfyUI probes after 5, 15, and 30 seconds are
  allowed. Exhaustion or unknown outcome leaves blocked cancellation intent.
  Startup resumes draining
  only from `cancel_requested`; blocked intent requires explicit Resume, which
  performs read-only reconciliation even after authorization expiry or feature
  disablement and never activates or dispatches work. Verified running work stays
  `cancel_requested`; verified settlement becomes `cancelled`. Eligible
  continuation restores only linked targets with no attempt journal under their
  existing shot IDs; completed outputs remain. Assistant transport cleanup must
  finish before its process-local fence clears. `frontend/src/views/SessionView.jsx`
  discovers persisted run state from the server independently of browser storage
  and exposes progress, per-shot results, blocker remedies, Cancel/Resume, scope
  comparison, renewal allowances for expired/budget-blocked runs, and eligible
  retries. Blocked cancellation exposes reconciliation only, which cannot
  restart or dispatch work; user-facing limits are in `docs/sessions.md`.
  Guided Full Auto creation schedules the coordinator after commit; eligible
  Resume does likewise. After cleanup, startup recovery schedules eligible runs
  in `active`, `waiting_authoring`, and `waiting_runner` states. The coordinator
  prepares ordered owned batches of up to twenty, reviews and approves, submits
  in order, then starts the existing serial runner.
  Full Auto `RunClaim`s for preparation and execution have 60-second leases,
  renewed by asynchronous 15-second heartbeats through assistant and ComfyUI
  I/O. Child operation leases remain ten minutes. Preparation uses one durable
  FIFO assistant slot across Full Auto runs and reserves every physical POST,
  including compatibility fallbacks, before dispatch. Its ten-minute lease
  renews every fifteen seconds. Full Auto requests have a 300-second total
  timeout; manual and other non-Full-Auto calls keep their existing transport
  behavior. Full Auto uses `assistant_unavailable`,
  `assistant_transport_error`, `assistant_rate_limited`, `assistant_refused`,
  `writer_output_invalid`, and `provider_changed` blockers. Only structured
  refusal/content-filter metadata counts as refusal; prose is not guessed.
  A live Full Auto POST has a process-local fence bound to its reservation and
  SQLite identity, so quarantine expiry cannot release the slot while the POST
  unwinds. On restart, only the persisted quarantine deadline remains; this is
  not cross-process or provider-side coordination, and manual calls remain
  outside the slot.
  Valid `Retry-After` seconds or dates are persisted and gate Resume until their
  UTC deadline. The runner checks ComfyUI stats before owned dispatch and retries
  failed stats/history GETs with at most three persisted probes after 5, 15, and
  30 seconds, reserving each probe before GET and never resending a prompt.
  Exhaustion blocks `comfy_unavailable`; linked unresolved work keeps Resume
  disabled.
  Local HTTP termination does not establish that remote work stopped.
  Preparation recovery waits for live children to settle, then resumes a
  registered child under a new fence
  after expiry or failure while preserving the completed prefix. A stale
  execution owner stops subsequent writes and dispatches and leaves admission
  unresolved. DELETE APIs and SQLite triggers protect active or unsettled runs
  from deleting sessions, linked shots, and exact model/workflow dependencies
  across direct and cascade paths; `full_auto_in_use` reports affected session
  IDs. SQLite triggers also reject direct deletes of bound saved-look versions
  or profile revisions while protected; no profile DELETE API exists.
  Run claims and lease reconciliation set affected nonterminal runs to
  `blocked`, persist `dependency_missing`, and advance the fence. For
  `cancelled`, `failed`, or `completed_with_errors`, status projects the blocker
  and Resume refuses without changing the terminal row. Claims use the run UUID,
  epoch, and fence; each output-collection retry revalidates the claim and shot
  before moving a file, preventing reused numeric session or shot IDs from
  authorizing stale writes. Profile-evidence foreign keys can still restrict
  direct profile removal after settlement. One signed, fenced SQLite runner
  claim with a 60-second lease and 15-second heartbeat is shared by manual and
  Full Auto starts. Manual starts return `409 runner_busy` for an active owner,
  an eligible Full Auto waiter, or unresolved earlier runner work; they are not
  queued. Full Auto waiters persist `runner_ready_at`; eligible order uses that
  timestamp then run ID. Resume re-enters with fresh priority; blocked,
  cancelled, expired, or
  authority-ineligible runs are skipped. This implements local admission only.
  Before Full Auto dispatch, the runner observes ComfyUI's running and pending
  queues without reserving an idle local runner. A busy queue persists
  `waiting_reason: external_queue_busy`, polls after 15 seconds including after
  restart, and preserves local FIFO readiness between shots. An empty observation
  can race another client's job; Full Auto may send one normal-priority POST for
  that attempt and provides no remote fairness or exclusivity guarantee. Its
  900-second shot timeout starts after owned running evidence, though history can prove
  completion when no running snapshot was observed. Manual runner behavior is
  unchanged.
  Startup recovery protects journaled/admitted rows before failing unclaimed
  legacy running rows. Resumed work uses fresh fences and reconciles attempts
  before continuing remaining work.
  Each attempt freezes hashes of the configured endpoint and identifiable
  ComfyUI server identity before intent; client prompt UUIDs require a strict
  `/features` boolean plus an identifiable `/system_stats` version, with no
  guessed version mapping or minimum. Identity is rechecked before POST and
  output acceptance. A lost or ambiguous POST gets live, read-only queue/history
  correlation using unique attempt metadata, graph digest, and prefix evidence;
  if there is no match, one PNG in the configured output directory can be
  adopted only when its embedded marker and graph match. Missing, mismatched,
  duplicate, or filename-only evidence stays unresolved. PNG-only settlement
  leaves the remote prompt ID unknown, and uncertain POSTs are never resent.
  Startup execution recovery validates frozen server identity and uses persisted
  read-only capability/queue/history probes. A unique matching attempt or
  uniquely validated PNG may be settled; recovery never resends a generation
  POST. For an attempt requiring reconciliation, missing, conflicting,
  duplicate, or prior-epoch evidence blocks; exhausted probes report
  `comfy_unavailable`. Disabled planning or expired authorization preserves
  settled outputs and blocks remaining work. Explicit journaled-attempt retries
  use quote-bound Full Auto Resume controls documented in `docs/sessions.md`;
  the read-only rollback preflight is `backend/full_auto_rollback.py`, with its
  operator runbook in `docs/rollback.md`. The journal is scoped to Full Auto;
  manual reference/guide handling and locked-file move retries remain covered.
  Internal review and policy approval
  validate current snapshots, conflicts, placeholders, adaptations, drift, and
  duplicate acceptance while keeping neutral notices nonblocking. Approval and
  new Full Auto submissions enforce that gate; exact linked-shot replays remain
  no-write. Policy provenance is separate from human review approval. The review
  projection and digest-bound duplicate-acceptance endpoint are available, and
  the coordinator invokes the review/approval gate. It persists ordered
  submission receipts binding prepared snapshots to shot links and digests,
  reuses links, and preserves final prompt bytes. A private
  authenticated runner claim checks current authority/evidence and ordered
  receipts, then records admission before scheduling. An uncertain scheduling
  outcome after admission refuses replay pending reconciliation. The legacy
  runner loop is unchanged.
- `backend/saved_looks.py` stores self-contained saved-look versions and
  separate immutable, digest-bound automation-profile revisions and permanent
  appearance projections. Its coverage compiler binds the exact saved-look and
  profile revisions, emits four deterministic English sentences for chest,
  hips, legs, and feet, and blocks unknown or incomplete accepted facts; partial
  layers never add up to full coverage. It does not certify rendered pixels or
  unsupported body regions. `DELETE /api/looks/{look_key}` writes a permanent
  `saved_look_archive` tombstone, hiding the look from active lists while
  retaining exact versions and related profile, photo, import-receipt, and
  session history and active Full Auto dependencies. Archived looks reject
  writes and new selections; exact historical reads remain available, with no
  restore UI. `backend/photo_staging.py` validates temporary look photos and
  owns their expiry and cleanup. `backend/session_plan.py` applies a selected
  version and a signed, reviewed wardrobe progression to an authoring-v1 plan
  through compare-and-swap, recording exact profile evidence when used.
  `backend/look_automation.py` validates unaccepted candidates for exact manual
  drafts and stores only a safe request projection in an additive 24-hour
  proposal table. `backend/photo_extraction.py` can attach a companion to its
  stage-bound proposal; the server computes its draft digest after validating
  the ordinary proposal. Accepted mapping validates the saved snapshot and
  resolves garment indices from that look's immutable history before the live
  catalogue fallback. `backend/combined_look_save.py` coordinates the optional
  reviewed automation companion for look creation, new versions, and existing
  manual/extracted-photo saves. One outer SQLite transaction stores the final
  look, profile revision 1, accepted evidence, and UUID receipt; exact retries
  replay the stored response, including after staged-photo cleanup, which runs
  only after commit. Prospective garment references map to keys assigned by the
  save, and profile identity/readiness bind to the final look digest. These
  combined review/save operations are API-only; the browser editor does not yet
  expose them. The API contract is in `docs/looks.md`.
  `frontend/src/appearanceOwnership.js` maps browser UTF-16 selection offsets to
  exact Unicode-scalar spans; `frontend/src/views/Looks.jsx` provides saved-look
  editing, JSON import/export, and photo review.
  `frontend/src/views/AutomationProfileEditor.jsx` edits immutable profiles for
  exact look versions; `frontend/src/components/AutomationProfileSelector.jsx`
  loads exact revisions and displays server readiness.
  `frontend/src/views/SessionLookProgression.jsx`, embedded in `SessionView.jsx`,
  handles saved-look selection and wardrobe progression in **Shared Choices**.
- Legacy room imports and catalogue preparation use `importer.py`,
  `room_registry.py`, `extractor.py`, `cut_map.py`, `mining.py`, and
  `translation_map.py`. Catalogue composition, judging, wardrobe, and image
  handling are exposed through the API and corresponding frontend views.

## Main Interfaces and Integration Boundaries

- The browser communicates with the backend through `/api/...` endpoints.
- The backend persists application metadata in SQLite under the configured data
  directory and stores session images in its sessions subdirectory.
- ComfyUI is an external image-generation service reached over HTTP. Workflow
  graphs are stored in ComfyUI API format together with a node map.
- The optional prompt assistant uses an OpenAI-compatible HTTP endpoint through
  `backend/enhance.py`; it is not required for local operation.

## Tests and Supporting Assets

- Backend tests run from `tests/` with pytest. `tests/conftest.py` provides a
  `FakeComfy` that records graphs and writes representative output files, so
  tests do not require a GPU, ComfyUI, or network access.
- Frontend behavior tests are colocated with helpers under `frontend/src/` and
  run with Vitest.
- `config.example.json`, shipped seed files, and workflow fixtures provide
  reproducible defaults without containing machine-specific configuration.
