# Project Progress

## Goal

Implement the active OpenSpec change `simplify-resource-session-workflow` one
independently reviewable task at a time, using an external executor for routine
production and retaining architecture, integration, and acceptance in the main
agent.

## Overall Progress

OpenSpec is a valid `spec-driven` change with 4 of 72 tasks complete. Task 1.1
was accepted in `616a6d54d1ddbb2ec97444b0d2dea2a0e16fe8fd`, Task 1.2 was
accepted in `a51abc3f2feca82d8cc2fcbfa332f0cd8777f059`, and Task 1.3 passed
independent review and its aggregate final gate. Task 1.4 has now passed
independent acceptance and is formally closed.

## Current Position

Task 1.4 is complete. The backend now applies exact declared library identity,
supports browser-only collection envelopes without a library while preserving
bytes and canonical interpretation, and requires explicit auxiliary choices to
match candidates recalculated from staged evidence. Browser-only preview
metadata is isolated in its attestation body; the legacy parser and path/API/CLI
serialized preview shape remain unchanged. Task 1.3's canonical preview,
commit, replay, concurrency, and atomicity contracts remain intact.

## Next Milestone

Task 1.5 remains pending and unimplemented. Do not begin it automatically;
prepare its bounded external implementation handoff only when explicitly
requested.
