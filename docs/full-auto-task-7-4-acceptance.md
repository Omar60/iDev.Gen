# Full Auto Task 7.4 Live Acceptance

Task 7.4 ran one authorized Full Auto session with twenty real takes, covering
all five saved stages of a progressive accessory profile. The run completed
with twenty linked outputs and twenty unique take, shot and attempt identities.
It consumed twenty assistant reservations, within the authorized forty-call
limit, and required no image rerolls. Stage counts were 5/5/5/4/1; the
accessory was present through the first four stages and absent from the final
stage prompt and permanent appearance projection.

## Controlled recovery

A controlled backend stop during an acknowledged image attempt exposed two
recovery defects. Windows ComfyUI history returned a backslash-separated output
subfolder that the attempt matcher rejected. The matcher now normalizes
separators for comparison while retaining relative-path and filename checks.
Resume also treated server normalization of full-body framing as changed input.
It now validates the original selection against the immutable creation receipt
and continues to compare the current plan and authorization, so genuine scope
changes remain blocked.

Supported cancellation adopted the already completed first output without a
replacement attempt. After a machine restart, unchanged-scope Resume completed
the remaining nineteen targets under the original run. The first remote history
entry was unavailable after the ComfyUI restart; its retained PNG marker and
bytes matched the pre-restart copy. The other nineteen outputs matched current
history by attempt, prompt, graph, model, LoRA and dimensions. All twenty PNGs
were valid at 832 × 1216. The temporary shared checkpoint selection was
restored after the run settled.

Regression coverage was added in `tests/test_full_auto_correlation_independent.py`,
`tests/test_full_auto_startup_independent.py` and
`tests/test_full_auto_conflict_recovery_journeys.py` for Windows path matching,
safe attempt correlation, unchanged-scope Resume, renewal and genuine drift.

## Verification and limits

The final full Python suite passed: 3,592 passed, 2 skipped and 5 warnings.
Strict OpenSpec validation and `git diff --check` passed. No frontend files
changed, so frontend tests and build were not applicable. Independent review
accepted the implementation, recovery behavior and retained live evidence.

This report establishes protocol completion, not visual fidelity. Images were
not visually assessed; framing, visible feet, anatomy, garment fidelity and
accessory fidelity remain for Task 7.5, and overall acceptance remains for
Task 7.6. One provider credential surfaced in an execution output during the
exercise and still requires operator rotation; no credential or private runtime
artifact is included here.
