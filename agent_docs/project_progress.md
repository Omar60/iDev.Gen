# Project Progress

## Goal

Implement the active OpenSpec change `simplify-resource-session-workflow` one
independently reviewable task at a time, using an external executor for routine
production and retaining architecture, integration, and acceptance in the main
agent.

## Overall Progress

OpenSpec is a valid `spec-driven` change with 3 of 72 tasks complete. Task 1.1
was accepted in `616a6d54d1ddbb2ec97444b0d2dea2a0e16fe8fd`, and Task 1.2 was
accepted in `a51abc3f2feca82d8cc2fcbfa332f0cd8777f059`. Task 1.3 has passed
independent review and its aggregate final gate.

## Current Position

Task 1.3 is complete. The backend now binds browser previews to canonical
fingerprints, manifests, targets, revisions, and HMAC attestations; persists
accepted resources, coverage, and the selection commit result in one
authoritative transaction; recovers stale claims; and replays durable results
without re-import. Browser selection claims are SQLite-owned while legacy path
imports retain their one-shot `.claimed` semantics. Post-commit staging and
attestation cleanup remain retryable without changing committed resources.

## Next Milestone

Task 1.4 remains pending and unimplemented. Do not begin it automatically;
prepare its bounded external implementation handoff only when explicitly
requested.
