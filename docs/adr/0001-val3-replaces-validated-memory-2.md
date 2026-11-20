# ADR 0001 — VAL3 replaces validated-memory 2.x with captured, verified facts

Date: 2026-10-08. Status: accepted.

## Context

validated-memory 2.x stored curated knowledge units and kept them honest with anchors, periodic
probes and freshness verdicts. Field measurement on its adopters showed:

- drift warnings changed what the agent would do in about 10 % of cases;
- stored knowledge was already mostly reliable; what users corrected in session was environment,
  project state, business context and decisions, and the dominant cause was assuming without
  checking, not wrong memory;
- up to 44 % of an adopter's commits were memory maintenance.

The harness's native auto-memory is the memory the model actually uses, but it saves whatever the
model decides, with the whole trial-and-error path, and validates nothing.

## Decision

Build VAL3 in a new repository:

- capture is triggered by harness hooks; a detached extractor turns each session's inbox notes and
  transcript into candidate facts and recipes;
- a fact is admitted when its evidence is quoted literally from the transcript, and is then trusted:
  no re-verification on recall, no periodic probes;
- the store is PostgreSQL, one schema per vault, shared or linked across projects and machines;
- delivery is guaranteed by the harness: a generated `MEMORY.md` index at session start and a
  per-prompt full-text match;
- derivable knowledge is not stored; external tools that answer it are registered and preferred.

The full design is `docs/design/2026-10-08-val3-design.md`.

## Consequences

- validated-memory stays frozen at 2.5.2; its adopters migrate with `val3 import`.
- The runtime adds one dependency (a PostgreSQL driver) and needs a reachable server.
- An admitted fact that later becomes false stays until a session supersedes it; a selective audit
  is deferred work.
