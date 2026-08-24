## Context

See `proposal.md` — Why. What shapes the approach is four measurements taken
against the live database and the running machine, not preference.

**The rated set is tiny.** Of 6380 finished, un-rejected photographs: 6356 are
unrated, 11 rated three, 7 rated four, 6 rated five. Thirteen photographs at or
above four, drawn from two sessions. Every decision about randomness and about
the threshold is really a decision about this number.

**The photographs are large.** The common canvases are 832×1216 (4.0 MB decoded)
and 1016×1920 (7.8 MB decoded); PNG files run to a median of 1.25 MB and a
maximum of 10.9 MB. At a three-second interval the slideshow sustains roughly
3.3 Mbps whatever else is done — preparing ahead moves that cost earlier, it
does not remove it.

**The server binds loopback.** `start.bat` runs uvicorn on `127.0.0.1`, and the
app mounts `frontend/dist` at `/` with the API under `/api`. A phone on the same
network gets a refused connection until the bind changes.

**The page will be served over plain HTTP.** This is the constraint that decides
the most, and it is not negotiable without infrastructure the user has ruled out
for now. On an insecure origin the browser withholds the Screen Wake Lock API,
Service Workers and the Cache API. The Fullscreen API is *not* restricted and
does work.

## Goals / Non-Goals

**Goals:**

- One read-only route and one screen, no schema change and no migration.
- The randomness holds up at thirteen photographs, which is where a naive
  implementation visibly breaks.
- Transitions without a visible stall, on a phone, over Wi-Fi.
- Network exposure that has to be chosen and that announces itself.

**Non-Goals (design-level, beyond the proposal's):**

- No thumbnail or resized-derivative pipeline. Serving the full PNG is enough at
  these sizes and intervals; a derivative cache is a second copy of every
  photograph on disk to solve a problem not yet measured.
- No pagination on the listing route. Six thousand rows of ids and short strings
  is a small response; paginating it would also break the shuffle, which needs
  the whole set to guarantee no repeat before exhaustion.
- No new state in the database. The slideshow's settings are the user's, not the
  app's, and they live in the URL.

## Decisions

### The set is fetched whole, once, and shuffled on the client

The route answers with the ids of every photograph meeting the threshold. The
screen shuffles that list and walks it.

*Why not `ORDER BY RANDOM() LIMIT 1` per advance.* It is the obvious shape and it
is wrong here. Independent draws repeat: at thirteen photographs the chance the
next is the same as the last is one in thirteen, so a repeat lands inside the
first minute and reads as a bug. It also costs one request per transition,
forever, and it makes preparing ahead impossible — you cannot decode a
photograph you have not yet asked for.

*Why not stream or paginate.* The shuffle's guarantee is "every photograph before
any repeat", which needs the whole set in hand. A page at a time gives a shuffle
within pages and a fixed order between them.

*Trade-off.* The set is a snapshot. A photograph rated during a running slideshow
does not join until the set is rebuilt. Acceptable: the slideshow is read-only,
rating happens elsewhere, and rebuilding is what changing the threshold already
does.

### The shuffle is a deck, re-drawn on exhaustion

Walk the shuffled list; at the end, shuffle again and walk it again. Use a
Fisher–Yates shuffle, not `sort(() => Math.random() - 0.5)` — the latter is a
well-known non-uniform shuffle whose bias is invisible in a large set and
pronounced in a small one, which is exactly the size this runs at.

The re-draw on exhaustion is what stops a thirteen-photograph set from becoming a
memorised loop after one pass.

### The settings live in the hash, not in storage

The screen reads its interval, threshold and look-ahead count from the URL. The
app already uses a hash router (`App.jsx`, deliberately, in place of
react-router), so this adds no dependency and no state.

*Why not `localStorage`.* Same behaviour, more code, and it loses the property
that motivated the choice: a phone home-screen shortcut stores a URL, so the
settings ride along with it. Two shortcuts with two configurations work for free.

*Fallbacks.* Each value is parsed and clamped to a sane range; anything absent or
unparseable takes the default. A malformed URL must play, not stall — a screen
that came up blank because a query string was hand-edited is the worst outcome
here.

### Preparing ahead means `decode()`, not just `src`

Assigning `src` to a fresh `Image` fetches the file. It does not decode it: the
decode of a 7.8 MB bitmap happens when the image is first painted, which is the
exact moment the transition is meant to be invisible. `HTMLImageElement.decode()`
returns a promise that resolves once the bitmap is ready, and awaiting it is what
actually removes the stall.

Default look-ahead of three. Memory is not the constraint — three decoded frames
is 12–24 MB, unremarkable on a phone. Three is where absorbing Wi-Fi variance
stops improving; the value is in the URL if it ever needs tuning.

Failures are swallowed per photograph. One missing file must not stop the deck.

### Full screen without the Wake Lock API

The Fullscreen API works on an insecure origin and gives the presentation. The
Screen Wake Lock API does not exist there, so the phone's display sleeps on its
own timer and the slideshow dies with it.

*Alternatives considered and rejected.* A hidden looping muted `<video>` used to
keep displays awake; it is unreliable on current mobile browsers and would be a
piece of deliberate trickery to explain forever. HTTPS via a self-signed
certificate does not help — browsers refuse Service Workers on invalid
certificates and treat the origin as untrustworthy anyway.

*What is done instead.* Nothing in code. The user sets the phone's display
timeout while using the slideshow. This is written down in `docs/` rather than
worked around, because the workaround is worse than the limitation.

*The upgrade path, if it ever matters.* A tunnel providing a genuine certificate
(Tailscale Serve or equivalent) turns the origin secure, at which point Wake
Lock, Service Workers and an offline cache all become available without changing
anything decided here. That is infrastructure, not application code, and it is
out of scope now.

### `start-lan.bat` calls `start.bat`; it does not copy it

`start.bat` carries the virtualenv bootstrap and the frontend build. Duplicating
that into a second file gives two copies that drift, and the drift surfaces as
"it works when I start it the other way".

So `start.bat` takes an optional host argument defaulting to the loopback address
it uses today, and `start-lan.bat` is a warning plus a call into it. The filename
is the documentation; the warning it prints is where the missing-authentication
fact is stated, at the moment the choice is made.

*Why not a changed default.* This is a public repository. Making
network-exposure the default hands everyone who clones it an unauthenticated app
listening on their network without them having chosen it.

*Why not an environment variable.* A variable is invisible in the shell history
and easy to leave set. A file that has to be double-clicked is a deliberate act
every time.

## Risks / Trade-offs

**Thirteen photographs is thin, so the feature underwhelms on day one for lack of
material rather than lack of code.** → The threshold dial is the mitigation and
the reason it is in scope: `min_rating=0` plays all 6380 immediately, and the
slideshow improves by itself as rating happens. Documented so the first
experience is not a mystery.

**The app is exposed unauthenticated to the whole network whenever `start-lan.bat`
is used.** → Separate entry point, self-describing name, warning printed at use.
It is reduced, not eliminated: anyone on that network who finds the port has the
full app. This is a real, accepted trade for a home network and it belongs in
`docs/known-limitations.md`, stated plainly.

**The phone display sleeps mid-slideshow.** → Not fixable on plain HTTP; the
display timeout is set on the phone and the limitation is documented along with
the tunnel that would remove it.

**Sustained bandwidth at short intervals.** → Preparing ahead absorbs variance,
not throughput. Below roughly two seconds per photograph on 1.25 MB files, the
network becomes the limit and no look-ahead value helps. Worth a line in the
docs rather than a guard in code.

**A Windows Firewall rule is bound to the exact executable path.** The rule that
currently admits inbound traffic points at the system Python, while `start.bat`
runs the virtualenv's — a different binary at a different path. The first run of
`start-lan.bat` will therefore raise a fresh firewall prompt, and a reflexive
"Cancel" leaves it silently unreachable. → Named in the troubleshooting doc, so
the symptom has an answer already written.

**A snapshot set goes stale against deletion.** A photograph deleted while a
slideshow is playing is still in the deck and 404s on its turn. → The per-photograph
failure handling already covers it: skip and continue.

## Migration Plan

None. No schema change, no migration, no new dependency, no change to any
existing route or screen. `start.bat` gains an optional argument and keeps its
current behaviour when called with none, so nothing that starts the app today
starts it differently.

Rollback is deleting the new route, the new screen, its navigation entry and
`start-lan.bat`.
