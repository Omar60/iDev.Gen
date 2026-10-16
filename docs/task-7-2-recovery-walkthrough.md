# Task 7.2 Full Auto conflict and recovery walkthrough

This guide covers the connected recovery checks for Task 7.2 of
`automate-resource-session-full-auto`. The automated journeys use the real
backend services and SQLite state with synthetic assistant and ComfyUI HTTP
transports. They need no GPU, live provider, or external network.

## Run the connected regression journeys

From the repository root, run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_full_auto_conflict_recovery_journeys.py tests\test_full_auto_revision_recovery_independent.py -q
```

The journeys cover accessory-ownership refusal and correction, stale source
consent, provider and runtime resource drift, duplicate review, cancellation,
expired-owner fencing, and lost-acknowledgement recovery. They compare durable
rows around refusals and interleavings, and check that completed preparation
prefixes and shot identities are retained where continuation is allowed.

Run the two recovery boundaries separately when investigating them:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_full_auto_conflict_recovery_journeys.py::test_room_translation_drift_fences_held_response_and_preserves_prepared_prefix -q
.\.venv\Scripts\python.exe -m pytest tests\test_full_auto_conflict_recovery_journeys.py::test_backend_process_restart_correlates_accepted_unacknowledged_comfy_prompt -q
```

The room-drift journey holds a writer response, changes the bound source,
verifies that the late response cannot extend the prepared prefix, and proves a
stale scope comparison causes no writes. After Cancel settles, it refreshes the
resource revision and explicitly accepts a fresh changed-scope comparison. The
same ordered target set can continue while the run is still in pre-link
preparation; historical operations, children, grants, and reservations remain
unchanged. A different target set or linked/generated Full Auto history cannot
use this rebase. The lost-acknowledgement test kills and restarts an isolated
backend after synthetic ComfyUI accepted a prompt. Its remote ledger and output
survive the process, allowing the test to check recovery against the same
attempt, prompt ID, graph, filename prefix, and output without a second prompt
POST.

The independent revision tests also cover changed-scope Resume without consent,
stale-owner writes, and rollback when resource planning is disabled during
checkpoint validation.

## Observe browser close and reopen

The Task 7.1 demo also provides a rendered browser path for server-owned run
discovery. Start it with the first synthetic writer response held:

```powershell
.\.venv\Scripts\python.exe scripts\task7_1_full_auto_browser_demo.py --hold-first-assistant
```

Follow the setup in the [Task 7.1 browser walkthrough](task-7-1-browser-walkthrough.md)
through the first **Generate without pauses** action. The launcher prints a
marker when the writer request is held and prints the release-marker path. Note
the session URL, close that browser tab, and reopen the same URL before releasing
the writer. The Session page should rediscover the existing run and its progress
from the backend. Then create the printed release marker in PowerShell:

```powershell
New-Item -Path '<release-marker path printed by the launcher>' -ItemType File -Force
```

Release the marker within two minutes; the held request has a 120-second
timeout. Wait for the run to finish, then press `Ctrl+C` in the launcher
terminal. It removes its temporary config, database, and images after backend
shutdown; if shutdown times out, it preserves the temporary directory and says
so. This browser procedure observes browser closure and rediscovery. Backend
process restart is exercised separately by the automated test above.

## Evidence boundary

These checks establish protocol and persistence behavior with synthetic
providers. The fake ComfyUI writes 2×2 PNGs; they do not establish visual
fidelity or guarantee that a live provider will produce matching images. The
focused commands above do not replace the full Task 7.3 Python, frontend,
build, strict OpenSpec, and diff gates, or the separately authorized live
generation and visual review.
