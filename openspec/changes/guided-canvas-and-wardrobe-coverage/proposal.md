# Guided canvas and per-take wardrobe coverage

## Why

Resource-v1 takes need an explicit, user-owned way to describe intended wardrobe visibility, and guided creation needs a session-only canvas choice. Both must remain part of the existing plan and request-replay contracts.

## What Changes

- Add optional per-take `wardrobe_coverage`, composed once after the effective wardrobe for text-to-image and guided positive prompts, and omitted from reference-edit prompts.
- Extend guided creation with an optional paired `width`/`height` override, while preserving inherited character dimensions when omitted and binding supplied dimensions to request-ID replay identity.
- Keep coverage user-authored and canvas settings session-scoped. Plan edits retain CAS, invalidation, and generated-history behavior.
- Live UI and ComfyUI acceptance covered sessions 425–427: session 425 inherited 832×1216 in an ungenerated draft; session 426 ran 5/5 at 768×1360 with visible clothing progression, though feet were not reliably complete; guided session 427 rendered at its workflow's 928×1664 resolution with coverage once. Its anchor/reference were set through the API because the UI lacks a cross-session selector. No GPU ceiling was established, and prompt text does not guarantee visual coverage.

## Capabilities

### Modified Capabilities

- `session-plan`: per-take coverage state and the guided request canvas contract.
- `resource-prompts`: graph-kind-aware prompt composition for coverage.

## Impact

This change documents the implementation already present in the working tree. The parent-reported independent automated gates passed: 2,797 Python tests, 605 frontend tests, and the frontend build. Live UI and ComfyUI outcomes and their limits are recorded above and in `tasks.md`.
