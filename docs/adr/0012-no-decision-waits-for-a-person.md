# ADR 0012 — No decision waits for a person

Date: 2026-11-15. Status: accepted; merged and applied to every vault on 2026-11-16 on the owner's go (plan 15,
part B: nothing waits on a person; candidates without evidence are admitted marked unverified; a batch withdrawal
is for a person). Revised after its challenge (last section). Supersedes the review parts of ADR 0004 and ADR
0008 on an upgraded vault. Open after the owner's reading: section 2's scope is to widen to facts, preferences
and decisions (an amendment to come), and section 5 waits on his answer.

## Context
Twelve review items waited on five vaults, the oldest since 2026-10-14. An automatic system must not wait on a
person; imperfect memory is better than none (owner, 2026-11-15). Read on 2026-11-15 over seven vaults:
- pending: 2 notices of a supersession already applied; 6 `tool` and 1 `user-confirmation` candidates queued
  under rules that no longer queue them; 2 candidates without evidence; 2 withdrawal proposals;
- decided by a person since October: 57 approved (43 of them imports of one vault), 5 rejected (4 tools, 1 user
  confirmation): a person refused about 1 item in 12, and that filter goes away;
- entries without any evidence row: 15, all in one vault, all imports a person approved.

## Decision

### 1. Three kinds of backing, stored, never guessed
- **evidence**: the entry has at least one evidence row (a quote that checked). As today.
- **attested**: a person vouched for it. Stored in a new append-only table `attestation (entry_id, by, how,
  created_at)`, written by `tool add`, by `import --apply` for what a person's run admits without verified
  quotes under the old rules, and by the upgrade for every entry that has no evidence when the vault is
  upgraded (each of them was admitted by a person's decision). Attested is not verified: readers name it
  "attested by a person", as ADR 0008 keeps the two apart.
- **unverified**: live, no evidence row, no attestation.
One function gives an entry's backing; every reader and every rule below uses it. A reader whose role cannot
read `evidence` or `attestation` says "backing not readable", never "unverified". Mirrors copy `attestation`
with the other tables; a mirror without it is refused by the refresh.
Whether a `file` evidence is still current stays the business of re-check (ADR 0006) and is shown beside the
backing, not merged with it.

### 2. Admission
Scope, as built: only a recipe or a tool can be a candidate without evidence. A fact, a preference or a decision
without evidence is refused by validation before it is a candidate (ADR 0008, unchanged here); whether those
should be admitted unverified too is a separate decision for the owner.
A candidate without evidence is admitted as unverified. `val3 save` prints `admitted unverified <ref>` and exits
0. Capture has no `queued` outcome any more. Nothing is queued by `submit`.

### 3. Less authority, enforced where the operations are shared
- Supersession (`_supersede`, reached from `submit`, `settle --replace` and capture): an unverified entry
  cannot supersede an entry with evidence or attestation. The candidate is admitted without that supersession
  and the output says so; `settle --replace` in that direction is refused.
- Withdrawal as duplicate or by `settle --drop`: allowed only toward an entry of equal or lower backing.
- Recipes: an unverified entry cannot be a cited fact or tool of a new recipe; a reader marks a recipe whose
  existing citation is unverified, and such a recipe is not delivered automatically.
- Automatic delivery (session-start index, prompt hook): entries are filtered to evidence or attestation
  **before** statistics, ranking and limits, in every fallback path; index sections and their collapse are
  computed over eligible entries.
- Explicit lookups (`recall`, `show`, `index`, both memory tools) return unverified entries, each marked on its
  own line, including in cut results; the tools' general notice no longer calls the memory verified.
- The extractor's material marks stored unverified entries as such, and its instruction for them is "propose
  again if this slice holds evidence".

### 4. Evidence added later
Saving a fact, preference or decision whose kind, title and statement equal a **live unverified** entry's, with
evidence that checks, appends the evidence to that entry and logs `verified`, in one transaction under the
writer's lock; repeating it is a no-op. The evidence keeps its own session. Recipes and tools are not verified
this way (their hash does not cover steps, type or environment): the duplicate is refused with that reason.
Consumers that read `confirmed` (the memory tools, re-check, the protection against replacement) treat
`verified` the same. Vectors are untouched. A capture `repeat` does not verify (amendment 1, point 5:
an equal text is this section's case; a narrower one proves less than the entry says).

### 5. Import
Confirmed by the owner on 2026-11-16, who added that validation carried by the imported material counts: the
source itself can be quoted, only inside one of its fenced code blocks (a result, an example it records), as
evidence labelled "the source itself"; it says the note records it, not that it is true. An import candidate that carries no quote at all is admitted and
attested by whoever ran `import --apply` (`how` names the batch); one whose quotes did not all verify is
admitted as unverified, no quote grants backing, and the attempt's results stay in the review row's payload.
The import keeps writing its `review_item` and a mechanical decision for every candidate, as it does today for
verified ones: that row is the batch's record (batch withdrawal and the already-imported check read it), not a
queue.

### 6. Withdrawal
- One entry with evidence that checks: at once, by anyone, as today.
- Without evidence, or a batch: only through the person's path, which now needs both a terminal on standard
  input and no agent marker in the environment; inside an agent it is refused with exit 1. An environment
  variable is a convention, not an authorization boundary; the terminal check narrows it and the README says
  what it is.
- Nothing is proposed any more (`propose_withdrawal` goes).
- A withdrawal stays final, and the same text cannot be saved again (unique hash): unchanged by this ADR and
  said in the refusal message.
- What that evidence proves and its limit: ADR 0006, amendment 1 (2026-11-17).

### 7. The review list is retired
- No path creates a pending item: `submit`, `queue_import` and `propose_withdrawal` are the three producers and
  all three change; a test asserts that no command leaves an undecided `review_item`.
- Notices (`supersession`, `withdrawn`, `recipe-cites-superseded`) become events only.
- `val3 review` lists history read-only and says the list is retired; `approve`, `reject`, `--tty` are
  removed, with the pending line of `val3 status` and of the session-start notice. `decide` is no longer
  reachable. `tool add` and `import --apply` stay.

### 8. Upgrade is the switch, per vault
`val3 vault upgrade`, under the writer's lock, in one transaction, idempotent, with `--dry-run` listing every
change:
1. creates `attestation` and backfills it for every entry without evidence (`how = 'before ADR 0012'`);
2. widens `review_decision.decision` to accept `retired`;
3. closes undecided items with a mechanical actor (`val3 upgrade`), never as a person: notices as `approved`
   (their operation is already applied; this attests nothing), candidates and withdrawal proposals as `retired`.
   Nothing is admitted or withdrawn by the upgrade.
4. `retired` is not a rejection: `submit` and `queue_import` treat a retired item's hash as free, and the
   already-imported check ignores retired rows.
The dry run prints each retired item with what it was, so the owner can save or withdraw again what he wants;
it does not promise a one-line command for payloads `save` cannot take (imports).
A vault that has no `attestation` table is served by the old rules (queue included) by the same code. Merging
the code is still not inert, as built:
- `val3 vault dump` refuses a source vault that is not upgraded (the catalogue version went up), so the mirror
  job stops for a source until it is upgraded;
- a mirror is not upgraded by `vault upgrade` (refused): it takes the new table at its next refresh from an
  upgraded source of the same version; a refresh between different versions is refused;
- the extractor's prompt goes to version 6 for every project (its material marks stored unverified entries);
- `val3 init` and `vault dump` name every vault that still needs the upgrade.
`val3 confirm` (ADR 0006) can still append evidence to an unverified recipe or tool and so give it backing;
this ADR does not change it.

## Deployment order
1. Merge the code and, in the same sitting, upgrade the source vaults (step 2): between the two the mirror job
   cannot dump them.
2. Per source vault: stop its sessions' capture (drain the queue, no drainer running), upgrade, check `status`
   and the backing counts, resume. An older process still running on a non-upgraded checkout cannot exist with
   an editable install on the same host; on the second host the code is updated first.
3. Mirrors after their sources (the refresh refuses a source newer than the mirror's schema and says so).
4. `val3 init` on each adopter for the new instruction block.

## Consequences
- What a person used to refuse now enters, marked, with less authority; withdrawal and the view are the remedy.
- A schema change after all: one table, one widened check.
- The 9 stale candidates and 2 proposals are closed as retired and shown once to the owner.

## Amendment 1 (2026-11-16): every kind can be unverified
Owner, on reading section 2: facts, preferences and decisions are memory worth keeping without explicit
validation; they are marked all the same. Implemented and merged on 2026-11-16 (808 tests pass),
on both hosts.

1. **Validation.** A fact, preference or decision without evidence is a valid candidate. On an upgraded vault it
   is admitted unverified, like a recipe or a tool (`admitted unverified <ref>`, exit 0). On a vault not yet
   upgraded it is queued as `no-evidence`: a deliberate exception to "served by the old rules", which used to
   refuse it.
2. **A quote that fails its check** (not found in the transcript, found only in the input of its call, or found
   only in text the memory itself wrote).
   - `val3 save`: unchanged. The command fails with `discarded`; the caller reads why and can correct the quote
     or save again without evidence, knowingly.
   - Capture, on an upgraded vault only: nobody reads the failure, so the candidate is admitted unverified with
     none of its evidence (as an import whose quotes did not all verify, section 5): one failed quote takes the
     backing of the whole candidate. The check returns a result per quote; the choice between the full evidence
     and none is made inside `submit`'s transaction, after which the reference and authority checks run as for
     any candidate. An invalid shape, a missing transcript and a database error stay rejections. On a vault not
     upgraded a failed quote is rejected as before.
   - Counted: capture's log line gains `unverified` (admitted without backing) and `quote-failed` (candidates
     that lost their evidence); the `admitted` event's detail carries the number of failed quotes, never their
     text. Before the change, over 30 days on five vaults: 21 `discarded` events against about 1 030 admissions
     (both by any path, so an upper bound for capture).
3. **The extractor is still asked for evidence** for facts, preferences and decisions. Its prompt says what
   happens to a candidate without it; its material gives the full statement of unverified facts, preferences
   and decisions (verification needs the identical text), says they may be proposed again with evidence, and
   that a recipe must not cite an unverified entry. Prompt version 7.
4. **What backing means, said plainly.** Evidence is checked provenance: the quote exists where it says. It is
   not a proof that the quote entails the statement, nor that someone other than the agent wrote it. This was
   true before; with unverified facts an agent reaches it more easily (save without evidence, then save the
   same text with a real but unrelated quote, or `val3 confirm`). No promotion check is added now; the remedy
   stays withdrawal and re-check, and the view shows each entry's evidence.
5. **Authority** (section 3) is unchanged, with three corrections found by the challenge:
   - a capture `repeat` verifies an unverified entry only when the candidate's kind, title and statement equal
     the entry's (same hash); a narrower restatement leaves it unverified;
   - automatic delivery shows and counts a neighbour pair only when both entries are eligible for delivery;
   - a memory note is marked verified only when the entry it finally points to has backing.
6. **Duplicates.** A text equal to a live entry's stays a duplicate whatever the backing. Equal to a live
   unverified entry's and carrying evidence that checks, it verifies that entry, by `save`, capture or an import
   whose quotes all verified. Only while the entry is live: a withdrawal or a `settle --drop` is final and the
   same text cannot be saved again; withdrawing an unverified entry directly still needs evidence or a person.
7. **Historical evaluation** reads backing as of the case's time, as it reads liveness; the upgrade's own
   attestations count at any time, since they stand for admissions older than the upgrade.
8. Not changed: withdrawal (section 6), imports otherwise (section 5), what `val3 confirm` accepts.

Owner, 2026-11-16, on the three questions left: unverified entries stay out of automatic delivery for now, to be
looked at again with two or three weeks of them; no promotion check is built, the `verified` events are read
instead; quotes of the imported source itself are accepted (section 5).

Challenge of the amendment: Codex gpt-6.1-sol, `medium`, read-only, 2026-11-16, 13 objections, 3 blocking
(`~/.local/share/val3/eval/plan-15/codex-amendment-review.md`). Taken: no claimed refusal for private data that
the code does not make (1); a typed result per quote and the choice inside the transaction (2); repeats verify
only on equal text (3); backing said to be provenance (4); imports verify an equal unverified entry (5); notes
(6); the extractor's material (7); neighbour marks in delivery (8); evaluation as of the case's time (9); counts
that can be read (10); vaults not upgraded (11); the limit of point 6 (12); which refusals the tests keep (13).

## Challenge
Codex gpt-6.1-sol, `medium`, read-only, 2026-11-15: 28 objections, 12 blocking
(`~/.local/share/val3/eval/plan-15/codex-adr-review.md`). The first draft derived "unverified" from the absence
of evidence and approval, with no schema change. Taken: stored attestation instead (2, 3, 7); one backing
function, permissions and mirrors (4, 5, 6); rules moved into the shared operations, `settle` included (8);
delivery filtered before ranking and index sections over eligible entries (9, 10); extractor material and
repeats (11); evidence attached only to facts, preferences and decisions, transactionally, sharing `confirmed`'s
consumers (12 to 15); existing recipe citations (16); envelopes (17); the person's path needs a terminal (18);
the limits of withdrawal said, not hidden (19); `retired` as a stored value and an atomic, mechanical closing
(21, 22); import deduplication and batch records kept on review rows (23, 24); no promised one-line recovery
(25); all three producers and `decide` (26); capture outcomes (27); upgrade as the per-vault switch and the
order above (28); plan 15's part B table is replaced by this ADR (1). Taken in part: partial import evidence
is kept in the payload but grants no backing (20). Not taken: none.
