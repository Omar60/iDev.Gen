# Slideshow

A read-only screen that plays finished photographs across every session in a
random order, full screen, advancing on a timer. It exists so the keepers can
be looked at from across the room instead of on the machine that made them.

Open it from the navigation (**Slideshow**), or load the address with your
configuration in the hash so a phone home-screen shortcut restores it on next
open.

## The settings

The address carries the configuration. All three are optional; an absent, an
out-of-range or a non-numeric value falls back to a working default.

| Setting | Default | Range | Hash parameter | On screen |
|---|---|---|---|---|
| Interval between photographs (seconds) | 3 | 1–60 | `interval` | yes |
| Minimum rating to include (inclusive) | 0 | 0–5 | `min_rating` | yes |
| How many photographs to prepare ahead | 3 | 1–10 | `lookahead` | no |

The rating threshold and the interval have labelled pickers below the
photograph, so neither needs the address bar — which matters on the device this
screen is for, where full screen hides the address bar and typing a query
string is the most expensive input there is. Changing a picker writes the new
value back into the address, so the configuration still travels with a saved
address or a home-screen shortcut.

The interval picker offers the values anyone actually chooses rather than all
sixty. An interval that arrives from the address without being one of them —
`interval=7` — joins the list rather than leaving the picker blank.

`lookahead` has no picker on purpose: it is tuning, set once if ever, not
something to reach for while watching.

Both pickers remain available while loading, after a listing error, and when
nothing meets the threshold. Empty and error states also offer an explicit
reload or retry without changing the saved settings.

Example: `#/slideshow?interval=5&min_rating=4&lookahead=3` plays every
photograph rated four or higher, holds each one for five seconds, and keeps
the next three decoded ahead of their turn.

`min_rating=0` is the value that makes the screen useful before rating has
happened. The first time the slideshow is opened, every finished,
un-rejected photograph is unrated — the bar with 13 photographs in it is what
the same screen looks like after a few sessions of rating.

## Order and repeats

The screen shuffles the photographs on load (Fisher–Yates, not
`sort(() => Math.random() - 0.5)` — the latter is a well-known non-uniform
shuffle whose bias is invisible in a large set and pronounced on thirteen
photographs, which is the size this typically runs at).

Every photograph in the set is shown once before any is shown a second time.
When the set is exhausted the order is drawn again, so a second pass is not a
replay of the first.

The new order also never opens with the photograph the previous pass ended on.
Without that one swap the same frame repeats back to back one pass in thirteen,
which is the exact symptom a shuffled deck exists to avoid.

A set holding one photograph shows that photograph and does not auto-advance.
An empty set says so on screen rather than showing a blank frame.

All of it lives in `frontend/src/deck.js`, apart from the screen that renders
it, because it is pure and because both rules above are invisible in review —
they surface as "the slideshow feels wrong", never as a stack trace.
`frontend/src/deck.test.js` holds them, and `npm --prefix frontend test` runs
it. The suite was checked against four deliberately broken versions: dropping
the seam swap, swapping Fisher–Yates for `sort(() => Math.random() - 0.5)`,
never redrawing the order, and advancing two at a time. Each one fails at least
one test, and the biased sort is caught by exactly one — the uniformity check —
because it is a valid permutation that every other test accepts.

## Preparing ahead

The next `lookahead` photographs are fetched and **decoded** before they
appear — not just fetched. A PNG that has been downloaded but not decoded
still pays the decode at the moment it is swapped in, which is the exact
moment this exists to keep smooth. `HTMLImageElement.decode()` is what
actually removes the stall.

A photograph that fails to fetch or decode does not stop the slideshow. The
next photographs are played; the failing one is skipped past.

A photograph deleted while the slideshow is playing is still in the deck and
will 404 on its turn. The per-photograph failure handling covers it: skip
and continue.

## Read-only

The slideshow offers no way to rate, reject, delete, edit or generate. It
shows photographs and changes nothing. A read-only badge is enforced by
construction: the screen has no buttons for those actions, and the underlying
listing route does not write.

## The display, the network, and the things that are not fixed here

- **Display sleeping mid-slideshow.** The Screen Wake Lock API is withheld on
  plain HTTP, and the page is served over plain HTTP by `start.bat`. Set the
  phone's display timeout (Settings → Display → Sleep) to a value longer than
  the slideshow you are running. See [known limitations](known-limitations.md).
- **Reaching the app from the phone.** The default `start.bat` binds
  loopback only. To open the app on a phone, use `start-lan.bat` — it prints
  a warning about the unauthenticated network exposure and then binds every
  interface. See [getting started](getting-started.md#reaching-the-app-from-a-phone).
- **Sustained bandwidth.** At a three-second interval the slideshow sustains
  roughly 3.3 Mbps regardless of the look-ahead. Preparing ahead absorbs
  Wi-Fi variance, not throughput. Below roughly two seconds per photograph
  on 1.25 MB files the network becomes the limit and no look-ahead value
  helps. See [known limitations](known-limitations.md).

## Leaving

The **Exit to Library** link and Escape leave the viewer and return to Library.
Clicking the photograph has no action. An earlier version of this page described
click-to-exit, but the application did not implement it. Browser fullscreen can
consume the first Escape to return to window mode; another Escape exits the
viewer. The Fullscreen button label follows external fullscreen changes.

## Studio viewer and recovery

Opening the Slideshow route remains the explicit action that starts its existing
automatic playback. There is no new play/pause or manual next/previous control.
The photograph has its own area, separate from the session name, rating,
interval and exit controls. Vertical, horizontal and square photographs use
`object-fit: contain` without distortion or controls covering their edges.
Session names wrap, with a visual session-ID fallback when a name is absent.

A native modal viewer with explicit Tab wrapping keeps focus inside its controls
and makes the
underlying navigation inactive. Focus starts on the viewer heading, remains
stable during timed changes, and returns to the application on exit. Body scroll,
listeners, buffer references and the timer are cleaned up on unmount. Fullscreen
is requested only by its button; failures are shown without repeating a request.
Fullscreen entered by the viewer is released when it unmounts, including a
request that finishes after navigation away.

The initial list request has a distinct loading state. Changing the rating keeps
the previous set running until the new list arrives, as before; the viewer says
that the set is updating. Stale list and image events do not replace the current
selection. Changing the interval preserves the set and retunes the existing timer.

Missing images still advance using the unchanged deck transition. If every image
in the set fails, an unavailable-state panel prevents repeated failing image
mounts and offers an explicit retry. The deck and its timer are not changed or
filtered by this panel. Retry reads the list again and builds a fresh shuffled
set; it does not write data. A single available photograph still has no timer.

Preloading retains the existing rolling decode window. An ID repeated by a
lookahead larger than the set is decoded once per window, not once per repeated
position. Decode failures do not stop playback. These changes add no image,
rating, deletion, approval or generation mutations and do not change `deck.js`.
