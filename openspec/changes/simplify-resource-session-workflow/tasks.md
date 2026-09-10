## 1. Browser-selected resource import

- [ ] 1.1 Define the staged/browser-selected resource input boundary and tests for exact content preservation, request limits, opaque staging identity, cleanup, refusal of client-side path assumptions and non-disclosure of physical server staging paths; keep existing path-based service/CLI behavior unchanged.
- [ ] 1.2 Implement deterministic library identity inference from supported source-declared identity or normalized file stem, including collision detection and explicit advanced override; verify re-import stability and fail-closed ambiguity handling.
- [ ] 1.3 Adapt staged inputs into the canonical resource preview/commit service so browser-selected and path-based equivalents produce matching accepted revisions/reports and preserve stale-preview/fingerprint/atomic rollback guarantees while browser commit state uses only opaque identifiers rather than private stage paths.
- [ ] 1.4 Replace the normal Resources import path/key fields with multi-file selection, inferred identities, automatic/fresh preview state and a user-oriented summary; keep the full safe canonical report and key override under advanced details.
- [ ] 1.5 Add frontend tests covering file selection, preview invalidation when selection/override changes, collision presentation, commit enablement, technical-details visibility and absence of private staging paths from browser-visible state.

## 2. Translation-map file selection

- [ ] 2.1 Reuse and harden the existing direct `translation_map` content boundary for browser-selected translation maps, including request-size handling as needed, while preserving normalization, authorization, canonical map digest, attestation and stale-preview detection; do not add a second staging subsystem unless a demonstrated boundary requirement makes it necessary.
- [ ] 2.2 Replace the normal translation map path field with a JSON file picker that sends selected content through `translation_map` and presents a readable preview/apply summary; retain `map_path` behavior needed for local automation under advanced/expert use.
- [ ] 2.3 Add regression tests proving replacing selected map content invalidates the old preview and that direct selected-content preview/apply produces the same canonical translation result as the existing supported map input.

## 3. Resource-session automatic authoring

- [ ] 3.1 Extend the authoritative resource-plan validation/round-trip contract with persisted authoring state: optional brief, one exact scene-anchor revision and per-dimension variation policy/resolved fixed values; verify invalid counts, missing/non-ready anchors, anchors outside selected resources, malformed policy and inconsistent fixed-dimension requests fail before assistant work.
- [ ] 3.2 Extend guided resource draft creation to persist exactly the requested number of stable take IDs plus authoring state before assistant calls while preserving `resource-v1`, selected immutable resource triples, workflow binding and plan CAS semantics; backend and frontend normalization/save paths must round-trip authoring state without loss.
- [ ] 3.3 Implement an assistant adapter/orchestrator that reuses the configured OpenAI-compatible `backend.enhance` transport but preserves structured field objects instead of flattening them to historical `{label, prompt}` rows; request only unlocked `camera`, `framing`, `pose` and `expression` fields.
- [ ] 3.4 Extend the resource writer input with bounded persisted authoring context: brief, scene anchor/effective fixed state, take ordinal/total, variation policy and deterministic summaries of already finalized earlier take choices; route output through existing `resource_preparation` writer validation and preserve the exact context/output in assistant provenance.
- [ ] 3.5 If a fixed variation dimension has no user value, establish it at most once through the assistant and persist the resolved value through plan CAS before per-take preparation begins; verify every take/retry uses that same authoritative value.
- [ ] 3.6 Implement bounded multi-take preparation with incremental persistence and recovery; verify interruption after several takes resumes only incomplete/invalidated work from persisted authoring state and never re-runs the writer for an immutable ready/generated snapshot.
- [ ] 3.7 Verify scene continuity and fixed-state precedence: the normal flow keeps one immutable scene anchor across the session, prior-take context may guide variety but cannot change place/light, and assistant output cannot override character identity, look, wardrobe, resource selection, adaptations, scene anchor, fixed variation values or explicit take choices.

## 4. Guided session and take-review UI

- [ ] 4.1 Add the normal resource-session creation surface for character, one ready scene anchor, take count, optional brief, constants and variation policy; make automatic authoring the primary action without visiting Catalogue or Judge, while keeping existing expert/multi-resource controls outside the normal path.
- [ ] 4.2 Rework the resource Takes view so effective synthesized choices are the primary review representation and raw camera/framing/pose/expression fields appear only in an advanced edit control.
- [ ] 4.3 Wire advanced take and authoring-intent overrides through the existing authoritative plan save/CAS path and verify affected preparation/review invalidation remains identical to direct plan edits.
- [ ] 4.4 Present assistant-unavailable, synthesis-validation, stale-plan, scene-anchor, conflict and partial-progress states in user-oriented language with exact diagnostics still accessible.
- [ ] 4.5 Verify successful automatic preparation stops at Review and never implicitly approves review, submits prepared takes or starts generation.

## 5. Compatibility, documentation and acceptance

- [ ] 5.1 Add backend regression coverage proving legacy sessions, measured catalogue gates, runner/reference semantics, immutable resource revisions, expert multi-resource plans and path-based resource automation remain unchanged.
- [ ] 5.2 Update frontend regression coverage for both `resource-v1` simple/advanced paths and legacy control visibility.
- [ ] 5.3 Update README.md and matching resource/session documentation with the browser import flow, scene-anchor continuity model, automatic session-authoring flow, assistant prerequisite/fallback and advanced controls.
- [ ] 5.4 Run `python -m pytest`, `npm --prefix frontend test`, `npm --prefix frontend run build`, `git diff --check`, privacy/control-character checks required by AGENTS.md, and `openspec validate simplify-resource-session-workflow --strict --no-interactive`.
- [ ] 5.5 Demonstrate the acceptance workflow with invented English fixtures only: browser-select multiple source files without typed paths/keys, import after preview, choose one ready scene anchor, create twelve automatically authored takes from persisted intent, interrupt/resume, verify place/light/look/wardrobe continuity while take choices vary, inspect generated choices, override one take, re-prepare/review, and confirm no implicit submission or generation.
