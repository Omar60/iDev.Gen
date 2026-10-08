# Tasks: guided canvas and wardrobe coverage

## 1. Implemented behavior and documentation

- [x] 1.1 Add optional, validated per-take `wardrobe_coverage` to resource-v1 plans and preserve it by stable take ID through UI edits and reordering.
- [x] 1.2 Compose non-blank coverage once after the effective wardrobe for text-to-image and guided positive prompts; omit it for reference edits and preserve blank/absent behavior.
- [x] 1.3 Keep coverage user-owned in automatic preparation, include it in plan change impact, and preserve CAS, copy-forward, invalidation, and generated-history rules.
- [x] 1.4 Add the optional paired guided `width`/`height` request fields with strict positive multiples-of-8 validation, session-only persistence, inherited model dimensions when omitted, and request-ID replay identity.
- [x] 1.5 Document the UI, prompt routing, lifecycle, replay contract, and rendering limitations in README and session documentation.
- [x] 1.6 Record the independently reported automated gates: 2,797 Python tests, 605 frontend tests, and frontend build passed.
- [x] 1.7 Validate this change with `openspec validate guided-canvas-and-wardrobe-coverage --strict --no-interactive`.

## 2. Live acceptance

- [x] 2.1 Live UI: session 425 inherited 832×1216 with the override off (draft, ungenerated); session 426 used the 768×1360 override and completed 5/5 through generation.
- [x] 2.2 Live ComfyUI: session 426's frozen prompt matched its queued shot and showed clothing progression, though feet were not reliably complete. Guide session 427 rendered once at its workflow's 928×1664 resolution with coverage once. Its anchor/reference were set through the API because the UI lacks a cross-session selector; no GPU ceiling was established.
