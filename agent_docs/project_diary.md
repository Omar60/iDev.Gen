# Project Diary

## Decisions and Lessons

- Repository-authored code, comments, UI, and documentation are English-only
  and must not contain personal or machine-specific data.
- `AGENTS.md`, the README/docs, source code, tests, and OpenSpec artifacts are
  the project sources of truth; bootstrap documentation records only facts
  verified from them.
- Legacy sessions and the `resource-v1` session-planning path are deliberate
  compatibility boundaries. Future work must preserve their distinction and
  the no-GPU/no-network test boundary.
