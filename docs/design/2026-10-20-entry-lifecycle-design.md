# VAL3 — entry lifecycle: re-check, confirmation and the agent's authority

Date: 2026-10-20. Status: **accepted by the owner on 2026-10-20**; implemented by plan 8. Follows `2026-10-14-entry-lifecycle-notes.md`; amends
ADR 0003 (recall does not re-read the repository) and W2 of `2026-10-13-withdrawal-design.md`.

## 1. Problem

Entries do not expire (owner, 2026-10-14). An entry leaves delivery when it mutates (supersession), when
better knowledge replaces it (supersession), when it is no longer needed (withdrawal, `obsolete`) or when it
was never true (withdrawal, `wrong`). Today the only signal that an entry changed is a session that
contradicts it, and only if that entry was delivered or listed to the extractor. Imported entries carry `file`
evidence, a quote checked once at import with `git show <commit>:<path>`; nothing notices when the repository
moves under it.

Measured on the second adopter's vault (2026-10-20): 28 live entries with 62 `file` quotes; 3 entries
have a quote that is no longer at HEAD (two because the cited file was deleted, one because `TODO.md` changed). Reading
the 62 paths with one `git show` each took 0.08 s.

## 2. Rulings (owner, 2026-10-20)

| # | Ruling |
|---|---|
| R1 | When the repository moves under `file` evidence, the entry is marked *to re-check*. The agent of the next session that receives it re-checks. The mark shows in the session index and in `val3 status`. No scheduled run. |
| R2 | The agent supersedes or withdraws without a person only when it brings mechanical evidence (a transcript quote or a `git show`), as admission already works. Without it, a person decides. |
| R3 | Supersession of several entries by one (N→1) is deferred until the first real case (6 supersessions so far, all 1→1). |

## 3. Decisions

| # | Decision | Rejected alternative |
|---|---|---|
| L1 | *To re-check* is computed when read, never stored: an entry is marked when one of its **current** `file` quotes is not in `HEAD:<path>` of the repository val3 runs in (the file is missing or the text changed). | A stored mark: needs a run that writes it, and it goes stale when HEAD moves on or back. |
| L2 | An entry's current evidence is its evidence rows with the latest `created_at`. A confirmation (L4) appends a new set, which replaces the old one as the baseline: the confirmer re-cites every file that still backs the entry; a confirmation without `file` items ends the repository signal for that entry (an entry whose cited file was deleted is confirmed that way or with a quote from the file that now holds the text). Builder's call. | Carrying forward the `file` rows a confirmation does not repeat: a deleted file would keep the entry marked for good. |
| L3 | A quote is checked only in a repository that holds its commit (`git cat-file -e <commit>^{commit}`). Elsewhere it is neither checked nor marked. Only home-vault entries are marked: `confirm`, `supersedes` and `withdraw` act on the home vault only, so a marked linked entry would ask for an action that fails. Builder's call. | Checking by path in any repository: a vault shared by several projects would mark entries against a repository they never came from. |
| L4 | `val3 confirm <vault>#<id>`: the entry still holds; new evidence is appended. Evidence is required. | Superseding with the same text: refused, identical text is a duplicate (2026-10-14). Superseding with reworded text: a new entry for unchanged knowledge. |
| L5 | `confirm` accepts `file` evidence (`{"kind": "file", "path": …, "quote": …}`); val3 reads `HEAD:<path>`, checks the quote and stores the commit as HEAD's sha. It also accepts the evidence kinds of `save`, checked against the transcript. | Only transcript evidence: an entry confirmed with a `git show` command output loses its `file` evidence, and the repository signal stops working for it. |
| L6 | Inside Claude Code, `val3 withdraw <ref>` withdraws at once, and stores the evidence, when its evidence checks against the transcript and would admit an entry of the same kind without review: any tool item (`query`, `command`, `api`, `test`), or `user` items for a preference or a decision. Without evidence, or with only `user` evidence for a fact, recipe or tool, it stays a proposal a person approves (W2). | `user` evidence never withdrawing at once: a preference or a decision is admitted on the user's literal words, so the same words take it out. |
| L7 | Supersession is unchanged: it follows admission. A fact with tool evidence, or a preference or decision with the user's literal words, is admitted without a person; a fact with only `user` evidence goes to review. | Review for every supersession without tool evidence (plan challenge, 2026-10-20): a preference or a decision has no evidence but the user's words; the user's literal quote is the mechanical evidence R2 asks for. |
| L8 | Outside the agent a person withdraws without evidence, as today; `--evidence` is accepted there too and stored. | Requiring evidence from a person: the person's decision is the attestation outside the agent. |

## 4. Interface

### 4.1 The mark

- **Session index**: a marked line ends with ` · to re-check`. When at least one line is marked, the header adds:
  `Entries marked "to re-check" cite a file that changed since they were checked. Before relying on one, check
  it: \`val3 confirm\` if it holds, \`val3 save\` with "supersedes" if it changed, \`val3 withdraw\` if it no longer
  applies (\`val3 confirm --help\`).`
- **Per-prompt delivery** (`prompt_context`): the same suffix after the ref. The ruling names the session index
  and status; the agent reads the statement in per-prompt delivery, so the mark goes there too (builder's call,
  recorded here).
- **`val3 show <ref>`**: one line per changed quote, `TO RE-CHECK: <path> changed at HEAD <sha12>` or `TO
  RE-CHECK: <path> is not in HEAD <sha12>`, after the title line.
- **`val3 status`**: `to re-check: N (<ref>, <ref>, …)` after the home vault line; at most 10 refs, then `…`.

`val3 confirm` runs inside and outside the agent alike.

The agent is not asked to re-check every marked entry at session start; it re-checks an entry when the entry
bears on its work (R1: "the agent that receives it").

### 4.2 `val3 confirm`

```
val3 confirm <vault>#<id> [file]       # evidence JSON list from file or stdin
             [--transcript <path>] [--session <id>]
```

Input: a JSON list of evidence items, the same objects as a candidate's `evidence`, plus `file` items.

- The entry must be of the home vault and live; `confirm` on a superseded or withdrawn entry names why it is
  not live (the writer's `_gone` text).
- Tool and `user` items are checked against the transcript exactly as `save` checks them; a mismatch discards
  the confirmation (exit 1) and logs `discarded`.
- `file` items: the path is canonical (no `./`, `..`, `//`, leading `/` or `-`) and passes `excluded()` (secret names) and the minimum quote length of the importer; the
  quote must be in `HEAD:<path>` of the project's repository; the stored item is `kind = 'file'`,
  `reproduction = 'git show <HEAD sha>:<path>'`, the quote with `\r\n` folded to `\n` and stripped, as import
  stores it. A repository with uncommitted changes to that path is checked at HEAD all the same.
- At least one item is required. `user`-only evidence is accepted for `confirm` (the knowledge stays as it was;
  nothing leaves or enters delivery).
- Output: `confirmed <ref> (<n> evidence items at <sha12>)` or `confirmed <ref> (<n> evidence items)`.
- The new rows share one `created_at`, read with `clock_timestamp()` after the writer lock, so a confirmation
  that waited for the lock still becomes the newest set.
- Event `confirmed` with the evidence count. No review item.

### 4.3 `val3 withdraw --evidence`

```
val3 withdraw <vault>#<id> (--wrong | --obsolete) --reason "<text>" [--evidence <file|->]
```

- `--evidence` takes the same JSON list as `confirm` without `file` items (a deleted or changed file is shown by
  a `git show` or `git log` command output in the transcript).
- Inside Claude Code: evidence that checks and would admit without review (L6) → withdrawn at once, output
  `withdrawn <ref>`; otherwise a proposal as today, with the evidence in the payload so the card shows it. Evidence is checked against the
  transcript in both cases; a quote not found exits 1 and proposes nothing.
- When a person approves such a proposal, `decide` copies the payload's evidence into `withdrawal.evidence`;
  `withdrawn_by` is the approver, as today, and `review_id` links the proposal (builder's call).
- `--batch` does not take `--evidence` (exit 2): a batch withdrawal stays a person's proposal.
- The `withdrawn` notices of design §5 of the withdrawal design (cited by a recipe, left a superseded entry out)
  are queued as today, whoever withdrew.

## 5. Data

- `withdrawal.evidence jsonb` (null when none), added by `val3 vault upgrade`.
- `withdrawal.withdrawn_by` for an agent withdrawal: `agent <session id>` (the OS user does not tell a person
  from the agent).
- `confirm` appends `evidence` rows; nothing else. The vault stays append-only.
- No table for the mark (L1).

### 5.1 Computing the mark

One function used by the session index, per-prompt delivery, `show` and `status`:

1. The repository root of the project (`git rev-parse --show-toplevel`); none → no marks. HEAD is resolved once
   to a sha and every later read names that sha, so a checkout during the check cannot mix two commits.
2. For the home vault (L3), the current `file` evidence of live entries (L2), parsed from `reproduction`
   (`git show <commit>:<path>`, shlex-quoted when written: parse with `shlex.split`).
3. Keep items whose commit is in this repository (L3), one `cat-file -e` per distinct commit.
4. Read every `<sha>:<path>` once (`git cat-file --batch -Z`, NUL-framed, so a path holding a line break is read
   like any other) and test each quote.

Per-prompt delivery runs it only for the entries it delivers. All git calls of one computation share a budget of
3 seconds; a failure or an exhausted budget leaves every entry unmarked and never fails the hook.

## 6. Tests

CLI as a subprocess against a disposable PostgreSQL and a fixture git repository; assertions on exit codes,
output and rows.

1. An imported entry whose quote is still at HEAD is not marked in the session index, `show` or `status`.
2. The cited file changes at a new commit: the entry is marked in all four places; the header line appears.
3. The cited file is deleted: marked, `show` says `is not in HEAD`.
4. A vault entry whose commit is not in the repository (a second project sharing the vault) is not marked.
5. A directory that is not a git repository: no marks, the hooks still answer.
6. `confirm` with a `file` item at HEAD: new `evidence` row with HEAD's sha; the mark is gone; the older rows are
   kept.
7. `confirm` with a `file` quote not at HEAD, a path named like a secret, a short quote: exit 1, nothing stored.
8. `confirm` with a transcript quote: stored; with a quote absent from the transcript: exit 1, `discarded` event.
9. `confirm` on a superseded entry, a withdrawn entry, an entry of a linked vault, with an empty list: exit 1.
10. Inside Claude Code, `withdraw --evidence` with a command quote in the transcript: withdrawn at once,
    `withdrawn_by = agent <session>`, evidence stored; the entry leaves the index.
11. Inside Claude Code, `withdraw --evidence` with only `user` evidence on a fact, or with none: a proposal, as
    today; with `user` evidence on a decision: withdrawn at once.
12. Inside Claude Code, `withdraw --evidence` with a quote absent from the transcript: exit 1, nothing withdrawn.
13. `withdraw --batch … --evidence`: exit 2.
14. `vault upgrade` on a vault with a `withdrawal` table lacking `evidence` adds the column; existing rows read as
    null.
15. A marked entry delivered for a prompt carries the suffix.
16. A confirmation with only a command quote ends the mark; a later change to the old file does not mark again.
17. An entry of a linked vault whose quote changed is not marked.
18. A person approves an agent's withdrawal proposal with evidence: `withdrawal.evidence` holds it,
    `withdrawn_by` is the approver.

## 7. Out of scope

- N→1 supersession (R3).
- `file` evidence in `val3 save` and in captured candidates (ADR 0003 stays for them): a supersession of an
  imported entry backed by a command quote loses the repository signal for the new entry.
- Extractor proposals of confirmations or withdrawals (W6 of the withdrawal design).
- Uncommitted changes in the working tree: the mark reads HEAD only.
- Signals for `query`, `command` and `api` evidence (would need re-running, which the design excludes).

## 8. Documentation

ADR 0006 (re-check and the agent's authority; amends ADR 0003 and W2); README sections on the session index,
`confirm` and `withdraw --evidence`; `CONTEXT.md`: *to re-check*, *confirmation*, *current evidence*.
