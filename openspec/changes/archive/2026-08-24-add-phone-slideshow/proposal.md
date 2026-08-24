## Why

The photographs a shoot produces are only ever looked at on the machine that
made them, hunched over the screen that ran the sampler. There is no way to sit
across the room and let the keepers play — and the app already holds everything
that would take: a rating on every shot, a route that serves the file, and a
server one flag away from being reachable from a phone on the same network.

## What Changes

- A **slideshow screen**: one photograph at a time, full screen, advancing on a
  timer, drawn from every session at once. Read-only — it shows photographs and
  changes nothing.
- A **rating threshold dial** on that screen. `0` means every finished
  photograph that was not rejected; `4` means the keepers. The threshold is the
  difference between a slideshow with thirteen photographs in it and one with
  six thousand, and which of those the user wants changes by the day.
- A **route listing top-rated photographs across sessions**. Nothing in the API
  answers "which photographs, regardless of session" today: every shot route is
  reached through a session id.
- **`start-lan.bat`**, which binds the server to every interface so a phone on
  the same network can reach it. `start.bat` keeps binding loopback and grows an
  optional host argument that the new file passes; the bootstrap is not
  duplicated.
- The slideshow's settings live in the URL, so adding the page to a phone's home
  screen stores the configuration the user chose.

Non-goals, deliberately: no rating from the phone, no offline copy, no responsive
rework of the existing screens, no authentication. Each is discussed in
`design.md` under the constraint that ruled it out.

**Exposing the server to the network is opt-in and stays opt-in.** The app has no
authentication; anyone on that network can read the gallery, delete sessions and
queue generations. That is why this arrives as a separate file whose name says
what it does, rather than as a changed default in `start.bat`.

## Capabilities

### New Capabilities
- `photo-slideshow`: playing finished photographs across every session in a
  random order that does not repeat until exhausted, at a configurable interval
  and above a configurable rating, full screen, with the next photographs
  decoded ahead of time.
- `lan-access`: serving the app on every network interface through a separate,
  explicitly named entry point, so a phone on the same network reaches it while
  the default stays loopback.

### Modified Capabilities

None. `session-library`, `session-export`, `contact-sheet` and `bulk-reshoot`
keep their requirements exactly. The slideshow reads the same `rating`,
`status` and `rejected` columns those already read, and adds no column and no
migration.

## Impact

- `backend/main.py`: one new read-only route listing photographs across
  sessions. No schema change, no migration, no new dependency.
- `frontend/src/views/`: one new screen. `App.jsx` gains a route for it.
- `start.bat`: takes an optional host, defaulting to the loopback address it
  binds today. `start-lan.bat`: new, two lines and a warning.
- `README.md` and `docs/`: the new screen and the new entry point are both
  user-facing and are documented.
- Gates: `python -m pytest` covers the new route; `npm --prefix frontend run
  build` covers the new screen.
