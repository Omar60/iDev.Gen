# Tasks

## 1. Playback behavior and unit coverage

- [x] 1.1 Add mount-local autoplay state and gate the existing single timer on playback without keying it on index or render; verify autoplay, one active timer, mid-interval pause, a full interval after Resume, active and paused speed changes, cleanup, and autoplay after remount in `Slideshow.test.jsx`.
- [x] 1.2 Preserve the current active-playback image-error path and add the paused-image unavailable state without advancing or retrying the failed resource; verify stale same-identity events, multi-photo and retained-pause single-photo errors, full-failure Retry, and resume-after-failure in `Slideshow.test.jsx`.
- [x] 1.3 Add the visible native Pause/Resume control, exact accessible names, disabled states, keyboard activation, and focus handling when async errors disable a focused button; verify Enter/Space, focus retention, Tab wrapping, rating-pending availability, F01, F02, H01, and unchanged hash/read-only behavior in `Slideshow.test.jsx`.

## 2. Responsive presentation and documentation

- [x] 2.1 Integrate playback beside fullscreen within the existing footer action cell; verify no horizontal overflow, photo containment, keyboard reachability, and usable control targets at 320x568, 375x812, 768x1024, 1440x1000, 844x390, and 844x320 without implementing F03.
- [x] 2.2 Update `docs/slideshow.md` for autoplay, Pause/Resume, full-interval resumption, rating and Retry behavior, fullscreen, paused image failures, and autoplay after reopening; verify it no longer claims Pause/Resume is absent or documents manual navigation.

## 3. Independent Chromium and acceptance gates

- [x] 3.1 Extend the standalone Slideshow Chromium check with accessible names, keyboard/focus, real timer pause and full-interval resume, paused image failure and rating, fullscreen/Escape, and the requested responsive viewports; run `node --test frontend/browser-checks/slideshow-focus-fullscreen.node-check.mjs` and confirm all existing F01, F02, and H01 scenarios remain.
- [x] 3.2 Run the complete Slideshow test, frontend suite, frontend build, Python suite, privacy/source controls, `openspec validate slideshow-pause-resume --strict`, and `git diff --check`; report command results and the final in-scope diff without staging or committing.
