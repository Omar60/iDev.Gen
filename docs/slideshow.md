# Slideshow

A read-only screen that plays finished photographs across every session in a
random order, full screen, advancing on a timer. It exists so the keepers can
be looked at from across the room instead of on the machine that made them.

Open it from the top bar (**Slideshow**), or load the address with your
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

The rating threshold and the interval have pickers in the bar over the
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

The threshold picker is repeated on the "nothing meets this threshold" screen.
Without it that screen would be a dead end, because the bar carrying the
pickers is only drawn over a photograph.

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

A set holding one photograph shows that photograph and does not auto-advance.
An empty set says so on screen rather than showing a blank frame.

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

The top-left **Exit** link, the `Escape` key, and a click anywhere on the
photograph all leave the slideshow and return to **Library**. Exiting the
browser's full-screen mode (the **Fullscreen** button in the overlay, or the
phone's back gesture) leaves the photograph full-window but inside the
browser chrome, which is the same screen minus the browser's own controls.
