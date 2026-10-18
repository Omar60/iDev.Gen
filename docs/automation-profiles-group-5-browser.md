# Automation profiles Group 5 browser walkthrough

This walkthrough records the rendered browser path for task 5.2 against the
real frontend and isolated FastAPI application. The assistant and ComfyUI
boundaries are deterministic fakes; the saved-look, profile, preflight,
session, preparation, and submission services run inside the application.

## Photo review and Full Auto

From the repository root, run:

    .venv/Scripts/python.exe scripts/task5_2_automation_profiles_browser_demo.py --mode photo --port 8803

The script builds the frontend, creates disposable config and SQLite state,
starts a loopback server, and prints the browser URL and a generated synthetic
PNG path. Open the printed URL ending in /#/looks and select that PNG for photo
extraction.

The first opt-in automation-companion request is configured to fail. The
ordinary extracted look remains editable. Cancel that staged photo, upload the
same generated image again, and retry extraction; the next proposal includes
unaccepted profile suggestions. Review the source description, garment order,
coverage, inventory completeness, baseline, and any aside wording. Save the
look and profile together. The run recorded look version 1 and its ready
profile revision 1, then a later edited version 2 with its own ready revision
1 while version 1 remained in history.

In Resources, select that exact version 2. Its sole ready revision is selected
and shown with its exact identity. Inspect the seven-stage removal timeline,
choose 13 takes, and authorize Generate without pauses once. The browser run
finished with 13 submitted and completed shots, zero failures, and no
unsettled work. It displayed the final empty inventory after the last
accessory's intermediate aside state. Permanent appearance facts remained
while accessory wording was absent from the final prompt.

## Manual path without an assistant

Run a second isolated process with a free loopback port:

    .venv/Scripts/python.exe scripts/task5_2_automation_profiles_browser_demo.py --mode manual-no-assistant --port 8804

Open its printed URL ending in /#/looks and create a look with incomplete
automation facts. The combined save remains blocked, while the explicit
look-only save succeeds without creating a profile. In Resources the look has
no ready revision, so Full Auto reports the missing readiness and ordinary
manual creation remains available. No assistant URL or model is configured in
this mode.

## Connected API prompt-byte audit

The separate API-wire audit exercises the connected endpoints without browser
clicks. It uses the same isolated app and fake providers:

    .venv/Scripts/python.exe scripts/task5_2_automation_profiles_browser_demo.py --mode photo --port 8807 --api-wire-audit --stop-after-api-wire-audit

The audit saves a reviewed look/profile pair, calls Full Auto preflight and
creation with explicit authorization, then waits for the coordinator and
FakeComfy. It exits nonzero if any of its ten checks fail. The successful run
reported one authorization receipt, 13 prepared takes, 13 submission
receipts, 13 completed shots, all 13 target indices, and 13 captured
ComfyUI-request bodies. For each receipt, the UTF-8 bytes of
prepared_take.final_prompt, shot.prompt, and the positive prompt parsed from
the captured serialized HTTPX request body matched; all 13 comparisons
passed. The audit emitted per-take SHA-256 summaries instead of raw prompts.

Stop the interactive browser run with Ctrl+C. After shutdown, the script prints
sanitized database counts and removes its temporary data. The API audit stops
its server automatically.

## Evidence limits

The browser renders the real UI and calls the real local API. Fakes replace
only assistant, image-generation, and ComfyUI service boundaries; outbound
hosts outside the loopback/demo transports are rejected. The PNGs are synthetic.
Prompt equality proves application protocol and persistence agreement through
the fake boundary. The captured request body is not a real network packet, and
neither the browser walk nor the audit judges generated-image fidelity.

A deliberate bad-byte injection into the audit's negative path was blocked by
the host execution policy before it ran. The positive audit completed with all
checks passing.
