# VAL3 — import from validated-memory 2.x (first round)

Date: 2026-10-12. Status: **approved by the owner 2026-10-12.** Decisions taken with the owner on
2026-10-12 after an external review of the evidence model (Codex, gpt-6.1-sol: accept with changes).
Extends sections 6 (admission), 7.1 (data model) and 11 (scope and migration) of
`2026-10-08-val3-design.md`; ADR 0003 amends ADR 0001. Implemented by plan 3b.

## 1. Problem and goal

validated-memory 2.x adopters hold knowledge units (`knowledge/kb-*.md`, with `provenance: <path> at
<sha>`) and harness memory notes (`memory/*.md`). VAL3 admits a fact only when its evidence is quoted
from the session transcript that observed it; for this material there is no transcript (most notes
name no session, and transcripts are deleted after 30 days).

This round answers one question: **how much of what an adopter knows can be verified, and does it
help an agent once delivered.** It fills a vault from one development adopter, puts every candidate
before the owner, and measures the decisions. It is not a migration: the adopter keeps
validated-memory, and its tree is only read.

First adopter: a development checkout on the owner's machine with 15 knowledge units (13 active) and
53 memory notes (41 active), `memory/` versioned in its repository.

Measured on that adopter (external review and own census): every provenance commit is an ancestor of
HEAD and every cited file exists; 29 of 41 active notes name a repository file; every cited file's
last change and every note's last change carry an agent co-author. A quote's presence does not prove
the statement: one unit states a measurement (0 of 196) that none of its cited files holds; two
active notes describe a component an ADR later retired, through an ADR whose text is unchanged.

## 2. Decisions

| # | Decision | Rejected alternative |
|---|---|---|
| D1 | The extractor reads each source with the files it rests on, as they were at a fixed commit, and returns a candidate whose quotes come from those files; VAL3 checks each quote with `git show`. | Every source verbatim to review with no check: measures nothing. |
| D2 | **Every imported candidate goes to the review list.** The owner's decisions are the measurement; automatic admission is decided later with those figures. | Admitting when the quote is at the commit and at HEAD: admits the 0-of-196 unit and the retired-component notes as verified. |
| D3 | Two steps: `--dry-run` writes a manifest (exact candidates, pinned snapshot, quotes, outcomes); `--apply` queues that manifest without calling the model again. Every review item carries the batch id. | One step: a second model run would differ from what was inspected. |
| D4 | Withdrawing an admitted entry or a whole batch is deferred. Required before any automatic admission or any import into a production vault. Built: `docs/design/2026-10-13-withdrawal-design.md` (plan 7). | Building it now: with D2 nothing enters without the owner. |
| D5 | One fixed snapshot per run: HEAD is resolved once; every source and cited file is read from git objects, never from the working tree. Uncommitted or untracked notes are reported, not imported. | Reading the working tree: during the external review HEAD advanced twice and an untracked note was committed. |
| D6 | Superseded sources are not candidates on their own; they are given as context to the source that supersedes them, which may keep part of them. | Skipping them: one unit supersedes its predecessor "only in its final sentence". |
| D7 | Outcomes are counted separately (section 5): unreadable, capped, excluded and mismatched are not "false". | One "discarded" bucket: retrieval failures would read as false claims. |
| D8 | Statements keep their scope and conditions ("ADR 0011 specified…", "the report recorded on 2026-09-21…", "check that the authorization still holds before reuse"). Knowledge derivable from code is not imported, as in capture. | Rewriting into timeless facts: changes their meaning. |
| D9 | Quotes are checked at import time only; `recall` and delivery never re-read the repository. | Re-checking on recall: needs the repository on every machine and changes ADR 0001's contract. |
| D10 | `val3 import --apply` is a person's action (refuses inside Claude Code, like `review approve`); `--dry-run` runs anywhere. An operational guard against an agent's mistake, not proof of human authorship. | No guard: an agent could queue a whole batch into a shared vault. |

## 3. Command

Run at the adopter's repository root, which holds a `val3.toml` naming the home vault:

```
val3 import --dry-run [--out FILE]   # extract and verify; write the manifest; no database writes
val3 import --apply FILE             # queue the manifest's candidates for review
```

- The manifest defaults to `~/.local/share/val3/import/<vault>-<UTC time>.json`, mode 0600 (it holds
  quotes), created with `O_EXCL`. Both commands print the counts of section 5 and the manifest path.
- `--dry-run` refuses a directory that is not a git repository root or has no `knowledge/` and no
  `memory/` at the snapshot.

## 4. Pipeline

1. **Snapshot.** `git rev-parse HEAD` once; its commit date is the snapshot date. Sources are
   `knowledge/*.md` and `memory/*.md` except `memory/MEMORY.md`, listed with `git ls-tree` at the
   snapshot. `git status --porcelain` names uncommitted and untracked sources for the report.
2. **Supersession.** A unit is superseded when `knowledge-index.md` at the snapshot says
   `superseded by kb-NNNN`; a note when its line in `memory/MEMORY.md` says `superseded by [[name]]`.
   A superseded source is added, marked as such, to the material of its direct successor.
3. **Cited files and commits.** For a unit: its `provenance` pairs; the commit is labelled *source
   commit*. For a note: every path named literally in its text and tracked at the last commit that
   changed the note (`git log -1 <snapshot> -- <note>`), labelled *note snapshot*: a repository
   state, not the date of the observation. A pair whose object is missing or binary is *unreadable*.
4. **Excluded paths**, never evidence: `knowledge/`, `memory/`, `knowledge-index.md`,
   `knowledge-extension.md`, `validated-memory.md`, `validated-memory-profile.md`, `journal.jsonl`,
   `.validated-memory/`, and any file named `memory.html` (past exports hold copies of the notes).
   Files named like secrets (`.env*`, `*.pem`, `*.key`, `id_*`, `*secret*`, `*.p12`), or under a
   directory named that way, are never read.
5. **Material.** The source text, its superseded predecessors, and each readable cited file with its
   path, commit, label and commit date. Each file is clipped at 20 000 characters and the whole at
   60 000 (*capped*, reported).
6. **Extraction.** One `claude -p` call per source with the import prompt (same invocation as
   capture: no tools, no settings, JSON schema). It returns either one candidate or a skip with a
   reason (`derivable`, `retired`, `not-knowledge`). The candidate is the capture format, with
   evidence items `{"path", "quote"}` and an optional `observed` date when the source states one.
   Kind: fact, decision or preference; feedback notes become preferences.
7. **Verification.** The importer, not the model, binds each evidence item to a commit: the path must
   be one of the source's cited files, else the item is *unbound*. Since 2026-11-16 (owner's decision) the
   source itself can be quoted too, only inside one of its fenced code blocks (what it records: a result, an
   example), bound to the source's own commit and labelled "the source itself"; its prose stays unbound. The quote (line endings normalised,
   at least 8 characters, as in capture) must occur literally in `git show <commit>:<path>`, else the
   item is *mismatched* and dropped. A bound, matching item records whether the quote also occurs in
   that path at the snapshot (`at_snapshot`) and the file's blob id. Excluded paths are refused.
8. **Manifest.** Batch id (16 hex characters), vault, repository root and `origin` remote URL,
   snapshot sha and date, and one record per source: path, kind (unit, note), source blob id,
   outcome, skip reason, candidate, evidence results, cost.
9. **Apply.** Checks that the manifest's vault is the project's home vault, re-runs step 7 for every
   evidence item (the manifest is a plain file; an edited quote, path or commit fails here and the
   candidate is reported, not queued), skips sources whose blob id an earlier import already queued
   in this vault, and queues each candidate through the writer as review reason `import`, payload
   holding the candidate and its `import` block (batch, source, labels, dates, evidence results).
   One `import` event per batch records the repository, snapshot and counts.

## 5. Outcomes reported

Per source: `superseded` (context only), `uncommitted`, `skipped` (with the extractor's reason),
`extractor-failed`, `queued`. Per evidence item: `verified` (with `at_snapshot` yes/no), `mismatched`,
`unbound`, `excluded`, `unreadable`; plus `capped` sources. A queued candidate with no verified item
is **unsupported**: approving it attests it.

The measurement, after review, is per class: unit or note; verified (at snapshot or not) or
unsupported; approved or rejected. It is read from `review_item` and `review_decision` filtered by
batch (a documented query; no command).

## 6. Review and admission

- Card: `review #N · import · <project> · <date> · batch <id>`; `SOURCE memory/<note>.md (note
  snapshot 7fba562, 2026-10-02)` and the `observed` date when present; the candidate; each evidence
  item as `file docs/adr/0011-….md @ e891fb1 (2026-09-08, source commit; at snapshot: yes)`, the
  quote, and the class of the file (code or configuration by extension; otherwise *record*). Failed
  items are listed as such. An unsupported candidate shows `no repository evidence: approving
  attests it`. Escaping as for every card.
- Approval admits the entry through the writer as today. Verified items become evidence rows of the
  new kind `file`, reproduction `git show <sha>:<path>`, the quote, no session. An unsupported
  candidate is admitted with no evidence rows; the review decision (who, when) is its attestation.
  `show` prints file evidence with its reproduction; the import block stays in the review item,
  reachable through `review_decision.entry_id`.
- The candidate validator accepts `file` evidence and the `import` block only for review items of
  reason `import`. `val3 save`, capture and the extractor schema keep rejecting them.

## 7. Schema

`vault.sql` adds `file` to the `evidence.kind` check and `import` to the `review_item.reason` check.
`val3 vault upgrade` becomes a list of idempotent steps (embedding table when missing; the two
checks replaced when they lack the new values) instead of returning when the embedding table exists.
No row changes; the append-only triggers do not fire on DDL.

## 8. Delivery-only test session

In the adopter, without touching tracked files: a `val3.toml` (home vault, service, `[embedding]`)
listed in `.git/info/exclude`; the `session-start` and `prompt` hooks in `.claude/settings.local.json`
(ignored); no `capture` hook, so `memory/` is never rewritten. The session receives the index and
per-prompt entries of approved items only. Removal: the three local changes. The README documents it.

## 9. Testing

Fixture adopter built by the test (`git init`, commits, a unit with provenance, notes, a superseded
pair, an untracked note, a `memory.html` export), fake extractor through `VAL3_EXTRACTOR`. Cases: the
manifest and its counts; a quote found only at the commit (`at_snapshot` no); a mismatched, an unbound
and an excluded item; a commit after the dry run does not change apply; an edited manifest quote is
refused at apply; a second apply queues nothing; `--apply` refused with `CLAUDECODE` set; card text;
approval of a verified and of an unsupported candidate (evidence rows); `val3 save` refusing `file`
evidence; `vault upgrade` of a vault created by the previous schema; a dry run writes no row.

## 10. Deferred

Automatic admission (decided with this round's figures); withdrawal of an entry or batch (D4);
adopters whose memory is not in their repository; importing on the production host; migration of an
adopter to VAL3 capture (retiring validated-memory).
