# ADR 0002 — The session index is delivered as SessionStart hook context, not through MEMORY.md

Date: 2026-10-08. Status: accepted.

## Context

The design (section 8) had VAL3 regenerate the harness's native `MEMORY.md` at session start so
that the harness would load the index without any model decision.

A probe on 2026-10-08 (Claude Code, `claude -p`, project with a `SessionStart` hook) showed:

- the hook rewrote `MEMORY.md` with a new marker, but the model reported the marker of the
  previous file: the harness reads `MEMORY.md` **before** `SessionStart` hooks run;
- `export` lines appended by the hook to `$CLAUDE_ENV_FILE` were visible to the session's Bash
  commands.

The Claude Code documentation caps hook context (`additionalContext` or plain stdout) at 10 000
characters; longer output is moved to a file and only a preview reaches the model.

## Decision

- The `SessionStart` hook injects the index (recipes, registered tools, preferences, grouped by
  knowledge index) as `additionalContext`, within 10 000 characters, with a closing line pointing
  to `val3 recall` when entries are left out.
- VAL3 does not write `MEMORY.md`; it stays the harness's and the model's (the inbox index).
- The same hook exports `VAL3_SESSION_ID` and `VAL3_TRANSCRIPT` through `$CLAUDE_ENV_FILE`, so
  `val3 save` finds the transcript that holds its evidence.

## Consequences

- The index is fresh in every session, including entries written by other projects or machines
  since the last one.
- The budget is 10 000 characters instead of 200 lines / 25 KB: index lines must stay short
  (reference, kind, title).
- If a later harness version loads `MEMORY.md` after the hooks, this decision still holds; it
  does not depend on the order.

## Update 2026-10-10

Design v2 of delivery and search (`docs/design/2026-10-09-semantic-search-design.md`) widens the
index to every live entry, facts and decisions included; the rest of this decision stands.
