# Proposal

## Why

Slideshow currently advances automatically with no way to hold a photograph on screen. A visible Pause/Resume control lets viewers stop the timer while preserving the current deck and its existing playback behavior.

## What Changes

- Add an accessible Pause/Resume button with autoplay enabled on each Slideshow mount.
- Keep the selected photograph and pause state stable through rating changes, retries, image failures, and fullscreen transitions.
- Resume with a full configured interval; keep the current interval-retuning behavior while playing.
- Keep pause local to the mounted view, outside the URL, and preserve read-only behavior.
- Show an unavailable state instead of advancing when the current photograph fails while paused; retain the existing failure skip while playing.
- Keep the control visible and disable it when no automatic advance is possible.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `photo-slideshow`: define playback pause/resume, its accessibility, interval semantics, error handling, and non-playable states.

## Impact

This affects the Slideshow React view, its unit tests and responsive styles, the standalone Chromium browser check, its user documentation, and the `photo-slideshow` OpenSpec capability. It requires no backend/API, dependency, route, hash-setting, or `deck.js` changes.
