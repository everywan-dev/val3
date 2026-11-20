# VAL3 — development conventions

- Design: `docs/design/2026-10-08-val3-design.md`; decisions in `docs/adr/`; vocabulary in
  `CONTEXT.md`.
- Runtime: Python 3.11+, standard library plus one PostgreSQL driver. pytest for development.
- Everything in this repository (code, comments, CLI messages, docs, commits) is in **English**.
- No references to internal company projects, hosts or people: the repository will be published.
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error.
- Tests drive the CLI as a subprocess against a disposable PostgreSQL and fixture project trees,
  asserting on exit codes, output and database rows. Tests never import package internals.
- Work on `feature/*` branches; never force-push `main`. Conventional Commits.
