# ADR 0008 — Automatic admission: the machine admits, the agent settles near duplicates

Date: 2026-10-24. Status: accepted (2026-10-24). Supersedes the review-by-kind rule of the design
(`docs/design/2026-10-08-val3-design.md`) and ADR 0003's rule that every imported candidate goes to the review
list; amends L6 of ADR 0006. Implements
`docs/design/2026-10-24-automatic-admission-design.md`.

## Context

The review list was meant for what a machine cannot check, yet what it held was decided by kind: every recipe
and tool, every fact backed only by the user's words, every import item, and notices of supersessions and
withdrawals already applied. On the first adopter every rejection a person made was a near duplicate of an
approved entry, and every approval carried evidence that already checked; on the second adopter all 43 import
items were approved. A person's approval attests, it does not verify, and the person did not remember what was
checked on each card. The truth check was already mechanical; what a person did was pick between near
duplicates. Meanwhile a tool quote counted when it appeared in any tool output of the session, so a command that
printed its own argument produced evidence that checked.

## Decision

- A1: an entry is admitted without a person when its evidence checks mechanically. Review by kind is replaced by
  review of what has no evidence: every kind with at least one evidence item, all of them checked, is admitted at
  once; a tool, recipe or import item with no evidence waits for a person (reason `no-evidence`, or `import` for
  an import item whose quotes did not all verify). An import item whose quotes all verified is admitted with a
  `review_decision` decided by `val3 (mechanical)`. A withdrawal of one entry with evidence that checks takes
  effect at once for every kind.
- A2: when a new entry has a close neighbour, the agent decides, not the person. The neighbour check runs when an
  entry's vector is stored: every live entry of the home vault of the same kind whose vector in the same space
  reaches `[embedding] neighbour` (default 0.70) is recorded as a pair, the newer entry marked *to settle*. The
  entry is admitted and live; the mark shows until either entry leaves delivery or the pair is settled with
  `val3 settle <ref> <near> --replace | --drop | --keep` (supersede the neighbour, withdraw the new entry with
  cause `duplicate`, or record both as distinct). Entries that existed at upgrade and entries admitted without
  `[embedding]` are exempt from being paired with each other.
- A3: the threat model is the mistakes and shortcuts of an agent, not an agent that deceives on purpose. A
  mechanical rule against self-produced quotes is enough: a tool quote counts only when it is in the output of a
  call whose input does not contain it (E1); a tool result without its call is not evidence.
- Supersessions and withdrawals queue no notices: the event log holds them and `val3 status` shows
  `changes (7 days)`. A recipe citing a fact or tool that left delivery is marked *to re-check* when read.

Detail: `docs/design/2026-10-24-automatic-admission-design.md`.

## Consequences

- The review list holds withdrawal proposals, items without evidence and import items whose quotes did not all
  verify. If it fills again, the design has failed.
- A legitimate quote that also appears in the command (a `grep` for the quoted text) is refused: the agent quotes
  more of the output.
- Existing vaults need `val3 vault upgrade` for the neighbour tables, the `duplicate` cause and the `no-evidence`
  reason; reads tolerate their absence and mark nothing; `val3 settle` exits 1 naming the upgrade. `val3 init`
  reports a vault without them.
- Items already pending on the review lists stay for one review by a person; there is no migration.
- Duplicates admitted before the upgrade are not paired; the agent settles them with the existing commands when it
  meets them.
