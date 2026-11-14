# Design

## Context

See `proposal.md` for motivation and `specs/photo-slideshow/spec.md` for the behavior contract. The viewer currently owns one interval keyed by deck length and configured interval; image errors advance through a separate handler. Rating and Retry rebuild the deck without unmounting the viewer, while route exit unmounts it.

## Goals / Non-Goals

**Goals:** Preserve the current playback path while active, stop all advancement while paused, resume after one full interval, and keep the control usable across the viewer's existing states.

**Non-Goals:** No manual photo navigation, URL persistence, backend/API change, `deck.js` change, reduced-motion behavior, or general F03 layout redesign.

## Decisions

- Keep playback state local to `Slideshow`, initialized to playing. Keep interval creation in the existing effect, keyed by deck length, interval, and playing state; cleanup cancels the old timer. Do not key it on index or render. Guard callbacks with the current playback state and accept image events only from the rendered image element so queued timer callbacks and stale image instances cannot alter the current photograph.
- When an image fails during active playback, retain the existing `advance` path. When it fails while paused, record its id and retain the index; render a visible unavailable message in place of the failed image so it cannot be requested again while selected. Resume leaves that message in place until the first full interval expires. Existing all-failed recovery and Retry remain authoritative.
- Keep pause state through rating, Retry, image/list errors, and fullscreen while mounted. List success may replace the deck as it does today; it must not change playback state. A later mount starts in playing state, and no URL setting is added.
- Use one native `type="button"` with the visible dynamic names `Pause slideshow` and `Resume slideshow`. Do not use `aria-pressed` with changing names. Disable it only when no automatic advance is possible; keep it available while a previous multi-photo deck remains during a rating request. If an async failure disables the focused playback button, move focus to the viewer heading; do not move focus when the button was not focused.
- Place the playback and fullscreen buttons in one existing footer action cell, preserving the footer's current grid tracks and photo `object-fit: contain` behavior.

## Risks / Trade-offs

- A paused failed photograph remains selected and unavailable until Resume; the status text must make that state clear without claiming playback continues.
- A disabled playback control can otherwise drop keyboard focus after an asynchronous failure; test both focus transfer from that control and no focus stealing from other controls.
- The short-height landscape layout is constrained by the existing viewer and is not an invitation to implement F03; keep the new control compact and test the requested viewports.

## Migration Plan

No persisted state or data changes. On rollback, remove the local playback state and control; existing URL settings, deck data, and APIs need no migration.
