# Project Progress

## Current Deployment: A2b-D2 Lifecycle and HTML Integrity

Deployment `idevgen-410-a2bd2` uses Medium on
`main@a9bbc2e0a60724eee78198e06aafd41270d6ae4d`, with the fourteen unstaged A2b
files retained. Their original bytes and SHA256 hashes were captured outside
the repository before editing. A2b functional acceptance remains HOLD.
Main owns implementation and verification; bounded read-only support audited
lifecycle and integrity. No product, API, dependency or CI changes.

## Delivered Correction

The isolated server uses SelectorEventLoop only on Windows, before the egress
guard. A blocking stdin thread signals stop through call_soon_threadsafe. The
shutdown deadline now bounds the actual server task after stop; timeout cannot
emit clean=true. Linux retains its normal event loop.

The launcher observes process exit, close and drained stdout/stderr, preserves
received event order and distinguishes graceful, forced and failed outcomes.
Normal success requires the clean marker, exit zero, no signal and no kill.
Owned-root removal is reported separately. HTML is compared byte-for-byte with
the production build before functional tests and again before document delivery;
SHA256 binds the browser guard to the original build. Differences are rejected
without stripping injected content or permitting another origin.

## Verified Evidence

- Node safety/lifecycle/integrity checks: seven passed.
- Pertinent Python API, harness, lifecycle, privacy/source checks:
  291 passed, one Windows symlink privilege skip.
- Full frontend: 1,007 passed in 67 files.
- Sanitized production build passed with the existing large-chunk warning.
- Real E2E command: integrity preflight rejected 417 build bytes versus 906
  received bytes before functional scenarios ran. Teardown reported graceful,
  forced=false, code=0, signal=none, drained streams, clean=true and root removed.
- Independent audit: owned PID absent, exclusive port rebind successful, no new
  temporary roots. The read-only audit found no required correction.

## Stop Boundary

HOLD for functional A2b revalidation in this environment: it still alters HTML.
No clean-HTML Chromium smoke, full functional E2E or repeats are claimed.
No AdGuard/antivirus/proxy/user settings were changed and no host was allowlisted.
D1 preserves the before-fix reproduction: Proactor retained two active
connections and required a kill; Selector closed cleanly with the same injection.
Windows executed; Linux remains unexecuted. Leave all work unstaged/uncommitted.
