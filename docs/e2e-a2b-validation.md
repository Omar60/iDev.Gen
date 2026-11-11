# Historical A2b Linux validation branch

## Retired workflow and accepted evidence

This guide preserves the original A2b-D4 preparation decisions and publication
procedure as historical records. All commands and execution instructions below
belong to that earlier phase; they are not the current publication procedure.
The A2b workflow is retired during A2d preparation. Its pre-retirement source
is available at
[`d674826`](https://github.com/Omar60/iDev.Gen/blob/d67482658fa9a2a4f8c8c93433e09fc37dfdf248/.github/workflows/e2e-a2b-validation.yml).

A2b was accepted by
[run 42942686174](https://github.com/Omar60/iDev.Gen/actions/runs/42942686174)
at `cf063991826ea895179f2643486571e7eb3e7b10`. A1, A2a, A2b and A2c are now
closed at `d67482658fa9a2a4f8c8c93433e09fc37dfdf248`:
[run 42965352388](https://github.com/Omar60/iDev.Gen/actions/runs/42965352388)
passed three independent 30/30 Chromium inventories, totaling 90/90, including
Review and Approval 8/8 per pass. Complete Python passed 3,729 tests with one
opt-in skip; frontend passed 1,007/1,007, Node 7/7 and privacy/source 46/46.
HTML integrity and graceful lifecycle/resource audits passed in every pass.

A2d integration into main is still in preparation. The retained
`.github/workflows/e2e-a2c-validation.yml` triggers only on push to `e2e-a2c`;
A3 will decide its replacement with permanent CI. It is not a main CI gate.
The cleanup must receive a new SHA and its own Linux validation; the earlier
run does not validate these subsequent changes. See
[the current E2E guide](e2e-testing.md) for commands and acceptance state.

## Historical D4 preparation snapshot

This is a prepared workflow, not evidence of a GitHub run. Nothing was staged,
committed, branched or pushed while preparing it. The nineteen existing A2b/D2
files must retain their captured hashes. The only D4 files are this guide and
`.github/workflows/e2e-a2b-validation.yml`; existing CI and documentation remain
unchanged.

## Historical execution boundary

The workflow runs only on pushes to `e2e-a2b-validation`, with an additional
job-level ref check. It has no pull-request, main, manual-dispatch, schedule,
deployment or reusable-workflow trigger. Publishing this branch automatically
starts it; it does not need the workflow on the default branch. Do not merge
this temporary workflow into main or add it as a required check.

The job uses an ephemeral GitHub-hosted Ubuntu 24.04 runner, Node 22 and Python
3.12. Actions are pinned to checked commit SHAs. `GITHUB_TOKEN` has only
`contents: read`; other permissions are omitted. Checkout does not persist
credentials. No secrets, environments, production endpoints or user profiles
are supplied. Bootstrap downloads project dependencies and browser/system
packages; these downloads are separate from the harness's denied application
egress. This is not an OS network sandbox.

Python dependencies come from `backend/requirements-dev.txt` in a new `.venv`;
frontend dependencies use `npm ci` and the existing lockfile. The existing
`e2e:install` script installs Chromium with Linux dependencies. Playwright
1.65.0 pins Chromium 158.0.8127.18, revision 1254. No dependency file changes.
The job prints actual tool/browser versions. Backend lower-bound requirements,
Node/Python patch resolution and the Ubuntu image can still change; they are
reproducibility limits, not reasons to ignore failures.

## Historical gates and evidence

1. Check ref/SHA/base ancestry and absence of real config/session data.
2. Build with the existing sanitized Vite helper; run all seven Node checks.
3. Start the real isolated server, run the byte/SHA256 HTML preflight, stop it,
   and audit its PID, listener, clean marker, exit and owned temporary roots.
4. Run complete Python and frontend suites.
5. Run the complete Chromium suite three times, with a fresh server each time.
   Inventory at A2b: A1 smoke 1, A2a ModelDetail 3, Submit 5, Prepare 4 and guard
   regression 1: fourteen cases per run. Each uses one worker, zero retries and
   stops at the first failure. A later run never converts a failed run to green.
6. Recheck privacy/source controls and the diff from the approved base.

The original preflight, identity validation, external-provider fakes, browser
HTTP/redirect/WebSocket guards and real temporary SQLite remain active. The
workflow audits only PIDs/ports printed by its own server. Linux `/proc` LISTEN
inspection is read-only and sends no probes to a reused port. It proves no
listener remains; it does not promise immediate reuse through TCP TIME_WAIT.
No process is killed and no directory is deleted by the workflow audit. The
owned harness handles its own shutdown; a forced/failed termination fails the
job. Job cancellation/timeout cannot be accepted as a clean teardown.

Logs remain temporary on success. Only validation failures upload named logs,
lifecycle receipts, traces and error contexts for three days. These contain
synthetic app data and ephemeral runner paths. No config, database, session
folder, environment dump, credentials or entire temporary tree is uploaded.
Installation failures still have the standard GitHub job log. Traces exist only
if a browser test reached tracing. Preflight rejection must leave functional
tests unexecuted and the job red, even when cleanup succeeds.

## Historical commands used by the job

From the repository root on Linux after setting up Node 22/Python 3.12:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
npm ci --prefix frontend
export PLAYWRIGHT_BROWSERS_PATH=0
npm --prefix frontend run e2e:install -- --with-deps
node frontend/e2e/build-production.mjs
node --test frontend/e2e/owned-root.node-check.mjs
node --input-type=module -e "import setup from './frontend/e2e/global-setup.mjs'; const stop=await setup({workers:1}); await stop();"
.venv/bin/python -m pytest -ra
npm --prefix frontend test
node frontend/node_modules/@playwright/test/cli.js test --config=frontend/playwright.config.js --workers=1 --retries=0 --max-failures=1
```

The workflow wraps these phases with logging and lifecycle audits and executes
the final Chromium command three times independently. Use the workflow for the
complete repetition/artifact procedure; do not repeat A1 inside one server with
`--repeat-each`, because its intentional denial ledger survives within that run.

## Historical publication procedure (not current instructions)

Do not execute these commands as part of D4 preparation. Review the complete
21-file change first. Require the nineteen original hashes to match the
preparation receipt, HEAD exactly
`a9bbc2e0a60724eee78198e06aafd41270d6ae4d`, an empty index and no other changes.
Before an acceptance commit, AGENTS requires complete Python/frontend and build
checks; record any environment block honestly. Check that the remote test branch
does not already exist; an existing branch is a stop condition, never force-push.

After publication has been separately authorized, from PowerShell at repo root:

```powershell
git status --short --branch
git rev-parse HEAD
git diff --cached --name-only
git ls-remote --exit-code --heads origin refs/heads/e2e-a2b-validation
# Continue only for exit 2 (no matching ref); stop for exit 0 or any other error.
git switch -c e2e-a2b-validation a9bbc2e0a60724eee78198e06aafd41270d6ae4d
$validationFiles = @(
  'agent_docs/latest_session_work.md',
  'agent_docs/project_core_tech.md',
  'agent_docs/project_diary.md',
  'agent_docs/project_progress.md',
  'agent_docs/project_structure.md',
  'docs/e2e-testing.md',
  'frontend/e2e/fixtures.mjs',
  'frontend/e2e/global-setup.mjs',
  'frontend/e2e/owned-root.node-check.mjs',
  'scripts/e2e_harness/server.py',
  'frontend/e2e/lifecycle.mjs',
  'frontend/e2e/preflight.mjs',
  'frontend/e2e/session-fixture.mjs',
  'frontend/e2e/session-guard.e2e.mjs',
  'frontend/e2e/session-prepare.e2e.mjs',
  'frontend/e2e/session-submit.e2e.mjs',
  'scripts/e2e_harness/session_flow.py',
  'tests/test_e2e_lifecycle.py',
  'tests/test_e2e_session_flow.py',
  '.github/workflows/e2e-a2b-validation.yml',
  'docs/e2e-a2b-validation.md'
)
git add -- $validationFiles
git diff --cached --name-only
git diff --cached --check
# Verify exactly these 21 files and their content; stop on any discrepancy.
git commit -m 'test(e2e): prepare isolated Linux validation'
git show --check HEAD
$validationSha = git rev-parse HEAD
git push --set-upstream origin HEAD:refs/heads/e2e-a2b-validation
gh run list --workflow e2e-a2b-validation.yml --branch e2e-a2b-validation --commit $validationSha --limit 1
```

Do not open a pull request or merge into main to launch this workflow. Select
the returned run ID, verify its exact branch/SHA/event, then inspect its result:

```powershell
$validationRunId = Read-Host 'Run ID listed for the exact validation SHA'
$runInfo = gh run view $validationRunId --json headSha,headBranch,event,conclusion,status,url | ConvertFrom-Json
if ($runInfo.headSha -ne $validationSha -or $runInfo.headBranch -ne 'e2e-a2b-validation' -or $runInfo.event -ne 'push') {
  throw 'Run identity mismatch; stop'
}
$runInfo
gh run view $validationRunId --log-failed
# Only if failure artifacts exist:
$evidenceDirectory = Read-Host 'Absolute evidence directory outside this repository'
gh run download $validationRunId --dir $evidenceDirectory
```

Download applies only if failure artifacts exist. Do not rerun unchanged failing
tests to obtain a green result; diagnose the preserved first failure. Do not
push the branch if commit/publication authority or required local checks are
missing. Main remains on its previous commit; this procedure creates commits
only on the separately authorized test branch.

If the run is not listed immediately after push, wait for scheduling and query
the same branch/SHA again; do not trigger dummy commits or rerun tests. A queued
or running result is not acceptance. Check all gates and receipts after completion.

## Historical GitHub-only limitations and acceptance criteria

Local syntax/route/tool checks cannot demonstrate Ubuntu dependency resolution,
browser installation, unmodified HTML on the hosted runner, Linux startup,
complete suite results, scheduling time, artifact upload policy or teardown.
All must be proven by the published run. Action allowlists/approval policies and
runner image changes can block execution. A branch push is not a passing CI run.

Accept A2b only after the exact published SHA passes all gates and all three
E2E lifecycle receipts show intact HTML, graceful exit0 without kill, no owned
PID/listener and no owned temporary roots. Keep Windows D2 evidence separate
from these still-unexecuted Linux results. No GO for A2b is implied by preparing
this workflow.

References: [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
[context availability](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability),
and [Playwright CI setup](https://playwright.dev/docs/ci-intro).
