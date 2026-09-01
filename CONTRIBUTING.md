# Contributing

iDev.Gen is a small, self-hosted tool. Issues, ideas and pull requests are
welcome.

Working rules for the codebase — including the invariants that break silently —
live in [AGENTS.md](AGENTS.md). Read that before your first change; it applies to
humans and AI agents alike.

## Before you write code

For anything bigger than a typo, open an issue first. It is the cheapest way to
find out that something is already in progress or does not fit the direction.

Bug reports are far more useful with: what you did, what happened, the shot's
error text from the gallery, and your ComfyUI version (the top bar shows it).

## Dev setup

You only need Python to work on the backend, only Node to work on the frontend.

### Backend

Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.venv\Scripts\python.exe -m uvicorn main:app --app-dir backend --port 8777
```

On Linux/macOS use `.venv/bin/python` instead. `start.bat` does all of the above
on Windows, including building the frontend.

### Frontend

Node 22 or newer.

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

The dev server runs on port 5273 and proxies `/api` to the backend on 8777, so
run both. For a production check, `npm --prefix frontend run build` writes
`frontend/dist/`, which the backend serves at <http://127.0.0.1:8777>.

`frontend/dist/` is **not** committed — it is built at install time by
`start.bat` and by CI.

### Configuration

The app writes `config.json` on first start from `config.example.json`, and the
Setup screen fills in the folders by asking ComfyUI where it was launched from.
`config.json` is gitignored: it holds paths specific to your machine.

## Tests

```bash
.venv\Scripts\python.exe -m pytest
```

No GPU, no running ComfyUI, no network: a fake ComfyUI writes the PNG the real
`SaveImage` would. A fix lands with the test that would have caught the bug.

The frontend has its own suite, `npm --prefix frontend test`, covering the
logic that is wrong in a way review cannot see. Run it when you touch
`frontend/`.

CI runs the Python suite plus a frontend build on every push and pull request.

## Pull requests

- One topic per PR; conventional commit subject (`feat:`, `fix:`, `docs:`…).
- Tests green, and a new test for any behaviour you changed.
- No personal data anywhere — paths, names, emails and tokens are checked by
  `tests/test_no_personal_data.py`.
- Update `README.md` and the matching page in `docs/` when behaviour changes.

## Handing a change to an AI agent

Two features here were built by an outside agent working from written artifacts
and no access to the conversation that planned them. Both landed correct, and
neither was mergeable as delivered. What follows is what the two runs cost.

**The plan travels, the conversation does not.** An executor reads `AGENTS.md`,
a proposal, a spec and a task list. Anything you know but did not write down is
not in the room. Where a spec was silent — it never named the `rejected` column
the gallery already filters on — the agent did not ask; it filled the gap and
then documented the gap as if it had been decided. Name in the spec every field
the existing behaviour depends on.

**Every verification is a command, not a sentence.** `verify: docs updated` let
two false sentences through. Write the line so it can be run and so its output
is either empty or wrong:

```
verify: git diff | grep -nE "^\+.*[ \t]+$" prints nothing
verify: python -m pytest green, and the twelve scenarios in the spec are twelve tests
```

**Ask what could not be verified.** A task that ends "or state that you could
not" gets an honest answer. One run reported two UI scenarios as "verified by
the build" — a build proves the JSX compiles and nothing about a confirmation
dialog. Ask for the gap and it gets named instead of papered over.

**Ask which sentences were checked against the code.** Prose is the one thing
the test suite never reads, so it is where the false claims land: a documented
filename that the route did not produce, a documented selection rule the route
did not apply. Requiring the agent to list the sentences it checked, one by one,
turned that from a defect into a paragraph of the report.

**The reviewer runs the gates.** Not the summary — the commands. Across the two
runs, three claimed passes were false, and every one of them was a check the
agent could have run in seconds. This is the habit the rest depends on; without
it the other four points are decoration.

**Prefer converting a rule into a check.** Trailing whitespace was asked for
twice in prose, in one case with the exact command to run, and arrived anyway.
It stopped being a problem the moment it became a test. If you find yourself
writing the same instruction a second time, that is the signal to spend ten
minutes on a check instead of ten more words.
