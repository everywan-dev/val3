# VAL3 — automatic admission: the machine admits, the agent settles near duplicates

Date: 2026-10-24. Status: **accepted by the owner on 2026-10-24**. TODO item 2 (automatic admission of mechanically verified entries; a person
sees only what a machine cannot decide).

## 1. Problem

The review list was meant for what a machine cannot check. What it holds today is decided by kind, not by what
the evidence shows (`writer._review_reason`): every recipe and tool, every fact whose only evidence is the user's
words, every import item, and three notices of changes already applied (`supersession`,
`recipe-cites-superseded`, `withdrawn`).

What the person did with it (both development vaults, 2026-10-24):

- First adopter: 18 decisions on tools, recipes and user-only facts; 13 approved, 5 rejected. **All five
  rejections were near duplicates** of an entry already approved or approved the same day: the GitLab CLI twice,
  the mail MCP twice, the code-graph CLI twice, the sample mailbox twice, a reviewer model replaced by a newer
  one. Every approval carried command or user evidence and had no twin. Four tool items are pending; at least
  two are again twins of approved tools.
- Second adopter: 43 import items, 43 approved.
- Owner (2026-10-14): a person's approval is not a reliable check. The owner does not remember what was checked
  on each card (2026-10-24).

So the person's work in review was choosing between near duplicates, not judging whether a claim is true; the
truth check is already mechanical (a quote present in tool output, in a file at a pinned commit, or in a user
message).

Can a machine tell a duplicate? Cosine similarity (bge-m3, title and statement) on the first adopter's review
items:

| Pair | Similarity |
|---|---|
| Rejected item vs. its approved twin (5 pairs) | 0.74 – 0.90 |
| Pending item vs. its likely twin (4 pairs) | 0.62 – 0.80 |
| Highest pairs of distinct items | 0.78, 0.76, 0.74, 0.74, 0.68 |

There is no clean cut: a machine can say "this entry has a close neighbour", not which one should stay.

Live entries already in the vaults, same kind, at or above a similarity (2026-10-24):

| Vault | Live | ≥ 0.70 | ≥ 0.75 | ≥ 0.80 | ≥ 0.85 |
|---|---|---|---|---|---|
| First adopter | 95 | 30 pairs | 14 | 8 | 5 |
| Second adopter (43 imported) | 41 | 1 | 1 | 1 | 1 |

On the first adopter the pairs at 0.85 and above are duplicates (one fact saved twice, a model restriction
saved twice, a mail sender saved twice) or a measurement and its update; one duplicate sits at 0.795 (a tool
installed, saved twice); most pairs from 0.70 to 0.80 are distinct facts on one topic (expense accounts, the
sample vault). No recipe cites a superseded fact today in either vault.

Second gap, already in TODO: tool evidence is any quote present in any tool output of the session, so
`echo "<claim>"` produces evidence that checks. Automatic admission makes this matter more.

## 2. Owner's rulings (2026-10-24)

| # | Ruling |
|---|---|
| A1 | An entry is admitted without a person when its evidence checks mechanically. A person sees only what a machine cannot decide. |
| A2 | When a new entry has a close neighbour, **the agent** decides (it has the session's context), not the person. |
| A3 | Threat model: mistakes and shortcuts of an agent, not an agent that deceives on purpose. A mechanical rule against self-produced quotes is enough. |
| A4 | The playback of 2026-10-24 is accepted, including the points marked as the architect's call (below, *(architect)*). |

## 3. Decisions

### Evidence

| # | Decision |
|---|---|
| E1 | A tool quote counts only when it appears in the result of a tool call **whose input does not contain it** (both normalised as today, `transcript._normal`). This drops `echo`, `printf`, here-documents and `python -c "print(…)"` that print their own arguments; real output (`psql`, `grep`, `ls`, a test run) still counts. The check is per call: the quote must be found in one call's result, not across the joined output of the session. Output of a command the person typed (`! command`) is checked the same way against the typed command. *(A3)* |
| E2 | Unchanged: `user` quotes come from the person's typed messages; `file` quotes from a file at a pinned commit (ADR 0003); `val3`'s own output is never evidence (plan 8). |
| E3 | Out of scope (A3): an agent that writes a file and then reads it, or any other deliberate construction. |

### Admission

| # | Decision |
|---|---|
| D1 | Every kind — fact, preference, decision, recipe, tool — and every import item is **admitted at once** when it has **at least one evidence item and all its items check** (E1, E2), and validation passes. The review reasons `recipe`, `tool`, `user-confirmation` and `import` are no longer produced. A tool, a recipe or an import item **with no evidence** (the schema allows it) is what a machine cannot decide: it goes to review with a new reason, `no-evidence`. *(architect, from A1 and D5)* |
| D1a | Imports keep their review records: an import item whose evidence checks gets its `review_item` and a `review_decision` written by the import itself (`approved`, decided by `val3` and marked as mechanical), so `withdraw --batch`, `writer.batch_entries` and the importer's dedupe on `source_blob` work unchanged. Imported items get the neighbour check like any entry (measured above: one pair on the second adopter's 43). *(architect)* |
| D2 | The writer looks for **close neighbours** of an entry when its first vector in the current space is stored (`writer.store_vectors`, right after admission or later by `val3 embed`): every live entry of the home vault, same kind, with a vector in the same space and cosine similarity at or above a threshold. The later-embedded entry of a pair finds the earlier one, so the order of embedding does not hide a pair. **Every** neighbour above the threshold is marked, one pair each. The threshold starts at **0.70** and is a setting of `[embedding]` (`neighbour`), adjustable per project like `floor`. *(architect: the measured tool twins start at 0.62 and a tool duplicate sits at 0.795; on the first adopter 0.70 gives about one neighbour for every three facts, a false neighbour costs the agent one decision, a missed one leaves a duplicate in the index. Revisit with the agent's settle outcomes.)* |
| D2a | Entries that existed when the vault was upgraded to this version, and entries admitted while the project had no `[embedding]`, are **exempt**: two exempt entries are never paired. Any pair with one non-exempt side is found, whichever side is embedded later, so neither a re-embedding after a new model build nor a project that adds `[embedding]` later produces a wave of marks, and a model change does not hide a pair. *(architect; amended after the plan challenge, 2026-10-24)* |
| D3 | An entry with a close neighbour is **admitted and live**, and marked *to settle* with its neighbour's ref. It is not held back. The mark is shown while both entries of the pair are live; when either is superseded or withdrawn, it stops showing. *(architect: holding it back would need a new state and a timeout; the mark reuses the re-check pattern: shown, never silent.)* |
| D4 | Without vectors — no `[embedding]` table, or the service down — no neighbour check runs and the entry is admitted unmarked. When `val3 embed` later stores the vector (the drainer runs it after each extraction), it runs the neighbour check and marks the entry if one is found. A project without `[embedding]` has no neighbour check. *(architect)* |
| D5 | The review list keeps what a machine cannot decide: withdrawal proposals of plan 7, and any item the writer cannot check. After D1 that is close to nothing; it stays as the place for it. |

### The agent settles a neighbour (A2)

| # | Decision |
|---|---|
| S1 | **Saved by the agent** (`val3 save`): the output names the neighbour and the three ways to settle it, one line each. |
| S2 | **Captured** (detached extractor, no agent present): the mark appears in the next session's index, in the same header block as the *to re-check* entries, with the neighbour's ref and the commands. The per-prompt delivery and `val3 show` show the mark too, as they do for *to re-check*. |
| S3 | Three outcomes, each recorded with who decided (`agent <session>` or the person): **replace** (the new entry supersedes the neighbour, through the existing supersession), **drop** (the new entry is withdrawn as a duplicate of the neighbour), **keep both** (the pair is recorded as distinct and never marked again). None needs tool evidence: the recorded pair is the evidence. *(architect: withdrawal today needs evidence and its causes are `wrong` and `obsolete`; a duplicate is neither, so it gets its own cause or its own record. Command names and shape are the builder's.)* |
| S4 | A mark that nobody settles stays; `val3 status` counts it (`to settle: N`), like `to re-check`. |

### Notices

| # | Decision |
|---|---|
| N1 | `supersession` and `withdrawn` stop producing review items. They are in the event log already; `val3 status` shows how many happened recently with a pointer to where to read them. *(architect)* |
| N2 | `recipe-cites-superseded` becomes a **to re-check** mark on the recipe, computed on read like the file-evidence marks (plan 8): a live recipe one of whose steps cites a fact or a tool that is superseded or withdrawn. It stops showing when the recipe is superseded or withdrawn, or confirmed (`val3 confirm`, with evidence) after the cited entry left delivery. No recipe is marked by this today (measured above). *(architect)* |
| N3 | Withdrawal rule L6 (plan 8) is defined as "the agent's evidence would admit an entry of the same kind without review". It keeps that definition, so after D1 a withdrawal whose evidence checks takes effect at once **for every kind**, a `user` quote on a fact included. Proposals for a person remain for a withdrawal without evidence and for a batch. *(architect)* |

### Rollout

| # | Decision |
|---|---|
| R1 | Items already in the review lists (five on the first adopter, one on the second) stay for one last review by the person; there is no migration of pending items. *(architect: six items; code to re-run them would run once.)* |
| R2 | Schema changes, if any (a duplicate cause or record, the neighbour mark), reach existing vaults through `val3 vault upgrade`; reads tolerate their absence as they tolerate a missing `withdrawal`. |
| R3 | The README sections on review, saving and status, the `val3 save --help` text, the extractor prompt (if it mentions review) and the CLAUDE.md block (`claude_block.md`) are updated to the new flow. An ADR records A1–A3 and supersedes the review-by-kind rule. |

## 4. Non-goals and anti-goals

- Not a defence against an agent that deceives on purpose (A3).
- Not N→1 supersession (deferred by the owner, 2026-10-20).
- No change to per-prompt delivery, the floor or the session index layout beyond the *to settle* lines.
- Anti-goal: a person asked to look at something a machine could have decided. If the review list fills again,
  the design has failed.
- Anti-goal: a near duplicate that enters silently. Every neighbour found is shown until settled.
- Not a cleanup of the duplicates already admitted (D2a). The first adopter holds a few (§1); the agent can
  settle them with the existing commands when it meets them.

## 5. Left to the builder

- Command names and shape for settling a neighbour (S3), and the wording of the marks and of `val3 save` output.
- Where the neighbour pair and its outcome are stored, and the schema change, within R2.
- How E1 maps transcript records to calls (tool_use input ↔ tool_result by `tool_use_id`; typed `! command`
  input ↔ its output).
- How `val3 status` presents the recent supersessions and withdrawals (N1).
- Tests per the project's conventions: CLI as a subprocess against a disposable PostgreSQL and the fake embedder.
