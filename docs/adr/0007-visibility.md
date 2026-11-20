# ADR 0007 — Visibility: an index that fits, a controlled vocabulary, an indicator and `val3 init`

Date: 2026-10-21. Status: accepted (2026-10-21). Implements `docs/design/2026-10-21-visibility-design.md`.

## Context

The session index of the first adopter had reached the 10,000-character cap of hook context: lines were
dropped in section order, which is alphabetical, so whole sections after the letter *f* never reached the
agent. Sections followed spelling, not topic: each entry was listed under the alphabetically first of its
indexes, so the project's own name and synonyms (two spellings of one topic, two languages) split the list,
and the extractor never saw the names already in use. Nobody could see VAL3 working: hook context reaches the
model, not the person. The project's instructions never mentioned VAL3, and setting a project up was manual.

## Decision

- V1: the hooks inject the same context as before; nothing new is forced on the agent.
- V2 and D10: the hooks add a `systemMessage` for the person: `VAL3: index of <n> entries · …` at session
  start, `VAL3: <ref>, …` when a prompt delivers entries, nothing otherwise. Warnings reach the person too.
- V3, V4, D13 and D14: `val3 init` writes `val3.toml`, the four hooks (left alone when any `val3 hook` is
  already configured in `.claude/settings.json` or `settings.local.json`) and a block between
  `<!-- val3:begin -->` and `<!-- val3:end -->` in `CLAUDE.md` saying what VAL3 is and how to use it; then
  it checks the connection. Every step is idempotent and nothing outside the markers changes.
- V5 and D7: the indexes of a vault are its vocabulary. The extractor sees the home vault's index names with
  their counts and reuses one; a new name is not blocked and goes to no review, but `val3 save` and
  `val3 tool add` report it and `val3 status` names the indexes with a single live entry.
- D1: each entry is listed once, under its section: of its indexes after aliases, the one with the most live
  entries in the vault (ties by name), leaving out names of the project's vaults and of projects that wrote
  to the vault; `General` when none is left.
- D2: over the cap, `Tools` and the re-check header stay in full (tool lines are dropped from the end only
  if they alone exceed it); sections collapse largest first into `## <name> — <n> entries: val3 index
  <name>`. The "more entries not shown" line remains only if collapsed lines alone overflow.
- D6: `val3 index merge <from> <into>` inserts a row in a new append-only `index_alias` table; every read
  and write resolves aliases. A merge is final.

Detail: `docs/design/2026-10-21-visibility-design.md`.

## Consequences

- Existing vaults need `val3 vault upgrade` before `val3 index merge` works; reads tolerate the missing
  `index_alias` table.
- Re-running `val3 init` updates an existing adopter: it keeps its `val3.toml` and hooks and writes or
  replaces the `CLAUDE.md` block.
- The block informs the agent; it does not force a lookup. Whether the agent reads listed topics more often
  is measured with `val3 status` and the prompt log.
- Sections depend on index sizes, so an entry can move to another section as the vault grows or after a
  merge.
