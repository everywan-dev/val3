# ADR 0009 — Capture settles its own near duplicates; one entry may replace several

Date: 2026-10-28. Status: accepted (2026-10-28). Amends A2 of ADR 0008 (near duplicates found at capture are
settled by the extractor, not left to the agent) and the rule that an entry supersedes one entry at most.
Implements `docs/design/2026-10-28-capture-settles-neighbours-design.md`.

## Context

Automatic admission (ADR 0008) admits every entry whose evidence checks and marks each close neighbour *to
settle* for the agent. After three days on an adopter, 50 of 55 neighbour pairs came from capture and 28 marks
were open; about a third of the pairs were a repeat or a replacement of a stored entry. The extractor re-proposed
what was stored because its lookup of existing entries did not find the stored twin, and no lookup over a large
slice finds all of them. The agent settled only the pairs its own `val3 save` had just raised, and fell back to
`--keep` when `--replace` was refused because the new entry already superseded another: when several entries
described the same thing, one could replace only one of them.

## Decision

- C1, N->1 supersession: when several entries describe the same specific thing, what holds now is established
  and one entry replaces all of them. `supersedes` takes one ref or a list of refs, in `val3 save` and from the
  extractor; every ref is checked as before and one bad ref refuses the candidate. `val3 settle --replace` works
  when the new entry already supersedes another, and `val3 show` lists every entry replaced:
  `supersedes: <ref>, <ref>`. No schema change and no migration.
- C2: replacing is archiving. A replaced entry leaves delivery and stays readable (`val3 show`, its evidence,
  its `SUPERSEDED by` line); there is no separate archive.
- C3, K2, capture decides from the slice: when a candidate of a slice has live entries of the home vault of the
  same kind at or above `[embedding] neighbour`, capture makes one more extractor call for the slice
  (`settle_prompt.md`) with each such candidate and its neighbours shown whole. The answer per candidate is
  `new`, `repeat` (one neighbour; the candidate is not admitted), `replaces` (one or more neighbours; the
  candidate is admitted superseding them) or `undecided`. The slice's most recent measurement is the current
  state; a fresh check on the host is never required. The second call judges candidates as written, it does not
  rewrite them. Pairs the second call compared are recorded settled (`keep` or `replace`) by
  `extractor <session>` in the admission's transaction. `undecided`, a malformed decision, a failed call or no
  vectors leave the pairs open for the agent as before.
- K2a, an old slice never replaces a newer entry: a neighbour admitted or confirmed after the slice's last
  transcript record is not superseded, checked again under the writer lock at admission; that pair stays open.
  A slice without a dated record replaces nothing.
- Without `[embedding]`, or on a vault without the neighbour tables, capture works as before. `val3 save` is
  unchanged apart from accepting a list.

Detail: `docs/design/2026-10-28-capture-settles-neighbours-design.md`.

## Consequences

- Each slice with a near candidate costs one more extractor call; the extraction summary counts both calls and
  reports repeats on their own: `admitted <a>, duplicate <d>, repeat <r>, queued <q>, already queued <k>,
  previously rejected <p>, rejected <x>; <n> extractor calls, $<cost>`.
- A repeat is not an entry: the event log keeps it (action `repeat`, on the neighbour, with the candidate), so
  nothing proposed is lost silently. A memory note that proposed it points at the neighbour.
- The agent sees only the pairs the slice could not decide. If capture's marks keep piling up, the design has
  failed.
- Marks already open and duplicates admitted before this change are not migrated; the agent settles them, now
  with `--replace` for several.
- An old session drained after a newer one stored the current value cannot overwrite it; a slice whose old
  measurement is followed by later talk is judged on its content.

## Amendment, 2026-10-30: the second call's rules (prompt version 2)

In its first two days the second call settled 14 pairs, all `new`; 4 of them were about the same thing as their
neighbour (three narrower restatements of one tool, and a criterion that went from proposed to required) and
were settled `keep` for good, against C1. Version 1 sent every candidate that dropped a still-valid part of its
neighbour to `new`, and `repeat` required "adds nothing" literally.

- The call first decides whether a neighbour is about the same thing (object, property, scope); `new` only when
  none is.
- `repeat` when each assertion of the candidate follows from one neighbour; a narrower restatement is a repeat;
  a candidate that adds a value, a status, a return value, a condition, a scope, an environment or a step is not.
- A candidate about the same thing as a neighbour that neither repeats nor replaces it is `undecided`, and one
  such neighbour makes the whole candidate undecided (the code records `keep` for every compared neighbour that a
  `replaces` does not name). Replacement still keeps every still-valid part (C2).
- Measured by replaying the second call over those pairs with their own slices (version 1 and 2, before merge).

## Amendment, 2026-11-15: the check can be turned off per project

On an adopter whose work is the same procedure repeated over a series of objects, entries are close by nature.
In eleven days its vault recorded 1 737 pairs; 1 464 were settled, 1 458 of them `keep` (1 261 by the second
call, 111 by the agent, 86 by a person) and 6 `replace`. The 6 score between 0.77 and 0.86, where about 570
`keep` pairs sit too: no threshold separates them. All 6 are a later state of the same object, not a similar
text. The second call recorded one `repeat` and one `replace` in that time. 273 pairs stayed open on 66 entries:
the failure signal named above.

- `[embedding] neighbour = false` turns the check off for a project's home and specialized vaults: no pair is
  recorded, capture makes no second call, earlier pairs are not shown, `val3 status` says `to settle: off`.
  The default and every other project are unchanged.
- Entries admitted meanwhile are exempt, so setting a number again pairs no two of them.
- Given up there: the second call's `repeat` and `replace`. An entry that replaces another still names it in
  `supersedes`, from the first answer or from the agent's `val3 save`.
- Not decided here: identity by object rather than by similarity (an optional object key and "as of" date on an
  entry), which is what would tell a later state of the same object from a sibling. Its own design.

## Amendment, 2026-11-17: identity by object is not built

The item left undecided above was measured on that adopter's vault (two rounds, 636 entries) and challenged
with a second model before the owner decided.

- Measured: a key derived from the entry alone repeats between two runs in 53 % (Haiku) and 73 % (Opus) of
  entries. Given in order of creation with the neighbours' keys shown, the pairs agree between runs in 51 %
  and 59 %; Opus finds 4 of the 6 known substitutions that have a recorded pair, and 56 of its 88 judged
  same-key pairs are different objects (45 of 68 when only states are keyed). A substitution by equal key
  would have taken valid entries out of delivery.
- Limits: one vault, eight known substitutions, no held-out run, since no variant passed development. The
  measurement fixes no best neighbour threshold either.
- Decided: no exact-match key. The profile for iterative work is a neighbour setting plus explicit
  `supersedes`, which stays explicit also when two neighbours seem to name the same object.
- Object keys are not built for every vault (the roadmap of the memory-tool design, section 7, item 2, said
  they would be). The question is reopened only by a variant that meets criteria fixed beforehand on
  held-out data, measured for its use: to suggest pairs, coverage and the number of comparisons against the
  neighbour check; to substitute, also how many still-valid entries would be lost, that is, whether it tells
  a substitution from entries that must coexist. Repeatability alone is not a criterion.
- Left open as a hypothesis: a stable identifier observed at capture, when a project supplies one.
