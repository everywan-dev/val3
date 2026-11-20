# ADR 0005 — Withdrawal of an entry or an import batch

Date: 2026-10-13. Status: accepted (2026-10-14). Implements decision D4 of `docs/design/2026-10-12-import-design.md`.

## Context

Until now an admitted entry left delivery only when another entry superseded it. A wrong entry with
no replacement, or a whole import batch, could not be taken out. D4 made withdrawal a prerequisite
of automatic admission and of any import into a production vault.

## Decision

- `val3 withdraw <vault>#<id>` and `val3 withdraw --batch <batch>`, each with a cause (`--wrong`: never
  true; `--obsolete`: true once, no longer needed) and a `--reason`.
- A person outside the agent withdraws directly; inside Claude Code the command queues a proposal
  that a person approves on the review list.
- A withdrawal is final and appended to a new table `withdrawal`; the vault stays append-only.
  Withdrawing an entry does not revive an entry it superseded.
- "Live" is defined once, as not superseded and not withdrawn as of an optional moment, and every
  reader uses that definition.

Detail: `docs/design/2026-10-13-withdrawal-design.md`.

## Consequences

- A mistaken withdrawal is undone only by saving the knowledge again as a new entry.
- `val3 vault upgrade` is needed on existing vaults.
- Historical search (`evaluate` with `as_of`) keeps reading the vault as it was.
