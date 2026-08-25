# Contributing

**Setup.** `make setup` creates `.venv` (Python 3.13), installs the
requirements and the commit hook (`git config core.hooksPath githooks`);
`cd web && npm ci` for the TypeScript half. Run Python via `.venv/bin/python`.

**Before handing work back.** `make check` green — unit tests, ruff + mypy,
the image/LOC guards, `tsc --noEmit`, the node suites (what CI runs).
`make test-fast` is the inner loop; `make e2e` when the page or studio changed
(it builds the page and installs Chromium itself — no separate setup steps).
Never skip or disable a test to get there: fix it or list it as follow-up.
The reasoning behind every rule here lives in `CLAUDE.md`, which is written
for agents but reads fine for people.

**Rules.** The security posture is local-only by decision, not omission —
read [docs/SECURITY.md](docs/SECURITY.md) before "fixing" auth or Origin
checks. Every tracked text file stays under 500 lines (`tools/check_loc.py`,
prose included — split on a real seam). ruff and mypy clean per `pyproject.toml`.
Tests never touch `tracks/`, `scenes/` or a real castle: use `CASTLE_TRACKS`,
`CASTLE_SCENES` and `CASTLE_HOST` (see CLAUDE.md "Sandboxing").

**Commits.** One-line subject written as a sentence about what changed and why,
in the voice of `git log --oneline`; no conventional-commit prefixes. Bump the
firmware version string whenever the device build changes.
