## 1. Browser-selected resource import

- [ ] 1.1 Add a raw-byte browser-selected resource staging boundary that reuses the application's existing raw upload style, preserves exact bytes, treats browser filenames as metadata only, never opens client paths, uses opaque server identifiers, hides physical staging paths, enforces 20 files / 10 MiB each / 50 MiB total per selection, and expires staged state after 24 hours; keep existing path-based service/CLI behavior unchanged.
- [ ] 1.2 Make the supported source envelope's top-level `library` field the sole browser-import `library_key`: require a 1-128 character string with no leading/trailing whitespace, ASCII control characters, `/`, `\\`, `.` or `..`; use the accepted value exactly as declared with no case folding, slugification, file-stem fallback, automatic suffix or browser-side key override. Add fail-closed tests for missing/invalid declarations and same-declared-library multi-file inputs.
- [ ] 1.3 Adapt staged inputs into the canonical resource preview/commit service so browser-selected and path-based equivalents produce matching accepted revisions/reports and preserve stale-preview/fingerprint/atomic rollback guarantees while browser commit state uses only opaque identifiers rather than private stage paths.
- [ ] 1.4 Replace the normal Resources import path/key fields with multi-file selection, declared library identity display, automatic/fresh preview state and a user-oriented summary; keep the full safe canonical report under technical details but do not expose or edit `library_key` in the browser flow.
- [ ] 1.5 Add frontend tests covering file/count/total-size limits, file selection, preview invalidation when selected bytes change, declared identity errors, commit enablement, technical-details visibility and absence of private staging paths from browser-visible state.

## 2. Translation-map file selection

- [ ] 2.1 Reuse and harden the existing direct `translation_map` content boundary for browser-selected translation maps, enforcing a 10 MiB request limit while preserving normalization, authorization, canonical map digest, attestation and stale-preview detection; do not add a second staging subsystem.
- [ ] 2.2 Replace the normal translation map path field with a JSON file picker that sends selected content through `translation_map` and presents a readable preview/apply summary; retain `map_path` behavior needed for local automation under advanced/expert use.
- [ ] 2.3 Add regression tests proving replacing selected map content invalidates the old preview, oversized content is refused, and direct selected-content preview/apply produces the same canonical translation result as the existing supported map input.

## 3. Resource deletion and restoration

- [ ] 3.1 Extend resource persistence with logical deletion state for libraries and individual logical source entries without mutating or physically deleting immutable `asset_revision` payloads. Prefer nullable deletion timestamps for library state and a separate logical entry/tombstone relation keyed by `(library_id, source_id)` or an equivalent non-revision design.
- [ ] 3.2 Add transactional, idempotent backend delete/restore operations for a library and an individual source entry. Library restore SHALL NOT implicitly restore individually deleted entries.
- [ ] 3.3 Update normal inventory, readiness/selectability and new preparation lookups so deleted libraries/entries are excluded or blocked, while historical exact triples used by prepared/generated evidence remain resolvable. Existing unfinalized drafts that require a deleted resource must fail visibly rather than silently substitute another revision.
- [ ] 3.4 Define and implement re-import behavior for deleted libraries: a browser import whose declared `library` matches a soft-deleted library previews restoration/reuse and, on successful atomic commit, clears the library deletion state while preserving the same logical library identity and immutable history. Individual entry tombstones remain deleted until explicit restore.
- [ ] 3.5 Ensure translation preview/apply treats a soft-deleted target library as deleted for its existing conflict semantics.
- [ ] 3.6 Add Resources UI Delete actions for logical libraries and entries with clear confirmation, remove deleted state from the normal inventory immediately after success, and expose Restore controls under Advanced/management UI.
- [ ] 3.7 Add regression tests for delete/restore idempotence, individual-vs-library deletion precedence, historical exact-reference resolution, blocking of new preparation from deleted state, re-import restoration, translation conflicts and immutable revision preservation.

## 4. Resource-session authoring contract

- [ ] 4.1 Extend authoritative backend and frontend plan validation/normalization/save round trips with a normalized `authoring` block containing `mode`, optional brief, one exact scene-anchor revision and complete camera/framing/pose/expression variation policy. Enforce take count 1-20 and brief length <= 2,000 characters before assistant work.
- [ ] 4.2 Validate that the guided scene anchor is an exact selected-resource triple, currently stored, ready, non-deleted and of an allowed scene-defining kind; reject missing/non-ready/deleted anchors, anchors outside selected resources, malformed modes/policies and inconsistent fixed state before assistant work.
- [ ] 4.3 Represent fixed variation values with explicit `value_origin` (`user` or `assistant`). Manual creation requires explicit values for fixed dimensions. Automatic mode may persist an unresolved fixed dimension eligible for assistant resolution.
- [ ] 4.4 Preserve assistant-established fixed-value resolution provenance in authoritative persisted authoring state, including the exact bounded input/context and validated output used to establish the value, before any take preparation consumes it.
- [ ] 4.5 Extend generated-state freeze rules so once any take is generated, scene anchor and variation-policy/fixed-value continuity state cannot be changed in ways that contradict generated output. Verify the existing look/initial-wardrobe/selected-resource freeze behavior remains intact.
- [ ] 4.6 Verify authoring mode and brief mutations still use plan CAS and invalidate only affected ungenerated work according to existing semantics without rewriting immutable ready/generated snapshots.

## 5. Atomic guided session creation

- [ ] 5.1 Add a dedicated guided resource-session creation backend boundary that validates all guided inputs and creates the session row plus initial `resource-v1` plan in one database transaction; a failed validation/save must leave no orphaned guided session.
- [ ] 5.2 Persist exactly the requested number of stable take IDs plus complete authoring state before any assistant call while preserving selected immutable resource triples, workflow binding and initial plan revision/CAS semantics.
- [ ] 5.3 Keep existing legacy/general session creation routes unchanged and add regression tests proving their current behavior is preserved.
- [ ] 5.4 Update frontend guided creation to use the atomic backend boundary rather than sequential create-session then save-plan calls.

## 6. Manual and automatic authoring

- [ ] 6.1 Verify manual authoring is a first-class no-LLM path: creating a manual draft never calls an assistant, exposes camera/framing/pose/expression for explicit completion, and proceeds through deterministic preparation, review, submission and generation with the same gates as automatic mode.
- [ ] 6.2 Implement an assistant adapter/orchestrator for `automatic` mode that reuses the configured OpenAI-compatible `backend.enhance` transport but preserves structured field objects instead of flattening them to historical `{label, prompt}` rows; bridge the async assistant transport to resource preparation explicitly rather than duplicating the transport.
- [ ] 6.3 Request only currently unlocked `camera`, `framing`, `pose` and `expression` fields and route output through existing `resource_preparation` allowlist, placeholder, fixed-state and writer-output validation.
- [ ] 6.4 If an automatic fixed variation dimension is unresolved, establish it at most once through the assistant, persist the resolved value plus `value_origin: assistant` and exact resolution provenance through plan CAS, then prepare takes from that new authoritative revision.
- [ ] 6.5 Extend automatic writer input with bounded persisted authoring context: brief, scene anchor/effective fixed state, current take ID/ordinal/total, variation policy and summaries of at most the five immediately preceding finalized takes in stable plan order. Prior summaries contain only take ID plus camera/framing/pose/expression and exclude images, arbitrary conversation and full historical prompts.
- [ ] 6.6 Preserve exact bounded writer input/context and validated writer output in assistant synthesis provenance for per-take automatic choices; assistant-authored values must never be persisted as manual completion merely to reuse an endpoint shape.
- [ ] 6.7 Implement bounded multi-take automatic preparation with incremental persistence and recovery; verify interruption resumes only incomplete/invalidated work and never re-runs the writer for immutable ready/generated snapshots.
- [ ] 6.8 Keep `automatic` as a valid persisted mode even when no assistant is configured. Disable/explain only the synthesis action; allow later configuration or explicit CAS switch to `manual` without creating another session type.
- [ ] 6.9 Verify fixed-state precedence in both modes: assistant output cannot override character identity, look, wardrobe, resource selection, adaptations, scene anchor, fixed variation values or explicit take choices.

## 7. Guided session and take-review UI

- [ ] 7.1 Add the normal resource-session creation surface for character, one ready non-deleted scene anchor, take count 1-20, optional <=2,000-character brief, constants, complete variation policy and explicit Automatic/Manual authoring choice.
- [ ] 7.2 In automatic mode, make synthesized effective choices the primary Takes/Review representation and keep raw camera/framing/pose/expression editing available for explicit overrides.
- [ ] 7.3 In manual mode, present camera/framing/pose/expression editing as the normal authoring surface; use the same effective review representation after preparation.
- [ ] 7.4 Wire take, authoring-mode and authoring-intent changes through the authoritative plan save/CAS path and verify affected preparation/review invalidation remains identical to direct plan edits.
- [ ] 7.5 Present assistant-unavailable, synthesis-validation, stale-plan, scene-anchor, deleted-resource, conflict and partial-progress states in user-oriented language with exact diagnostics still accessible.
- [ ] 7.6 Verify successful automatic or manual preparation stops at Review and never implicitly approves review, submits prepared takes or starts generation.

## 8. Compatibility, documentation and acceptance

- [ ] 8.1 Add backend regression coverage proving legacy sessions, measured catalogue gates, runner/reference semantics, immutable resource revisions, expert multi-resource plans and path-based resource automation remain unchanged.
- [ ] 8.2 Update frontend regression coverage for browser import, resource delete/restore, atomic guided creation, automatic resource-v1, manual/no-LLM resource-v1, advanced resource controls and legacy control visibility.
- [ ] 8.3 Update README.md and matching resource/session documentation with source-declared browser library identity, import limits/staging lifetime, delete/restore semantics, scene-anchor continuity, authoring limits, explicit automatic/manual choices, optional assistant behavior and retained expert paths.
- [ ] 8.4 Run `python -m pytest`, `npm --prefix frontend test`, `npm --prefix frontend run build`, `git diff --check`, privacy/control-character checks required by AGENTS.md, and `openspec validate simplify-resource-session-workflow --strict --no-interactive`.
- [ ] 8.5 Demonstrate browser resource management with invented English fixtures only: select multiple source files declaring `library`, import without typed paths/keys, reject one invalid/missing declaration, delete/restore one source entry, delete/restore one library, verify deleted resources are unavailable for new selection, re-import a deleted library and confirm the same logical library is restored while an individually deleted entry stays deleted and historical exact references remain resolvable.
- [ ] 8.6 Demonstrate automatic acceptance with invented English fixtures only: choose one ready scene anchor, atomically create twelve automatic takes from persisted intent, interrupt/resume, verify place/light/look/wardrobe continuity and deterministic five-prior-take context while take choices vary, inspect provenance, override one take, re-prepare/review, and confirm no implicit submission or generation.
- [ ] 8.7 Demonstrate the no-LLM acceptance workflow with invented English fixtures only: with no assistant configured, persist an automatic draft and show synthesis unavailable without invalidating it; switch explicitly to manual, or create a manual draft directly, fill take choices, prepare/review them, and confirm the normal explicit submission/generation path remains fully usable.
