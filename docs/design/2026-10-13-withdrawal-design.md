# VAL3 — withdrawal of an entry or an import batch

Date: 2026-10-13. Status: **accepted by the owner on 2026-10-14**, with W7 added. Implements decision D4 of
`2026-10-12-import-design.md`. ADR 0005.

## 1. Problem

An admitted entry is delivered until another entry supersedes it. There is no way to take out an
entry that is simply wrong, or a whole import batch, without writing a replacement. D4 made this a
prerequisite of automatic admission and of any import into a production vault.

## 2. Decisions

| # | Decision | Rejected alternative |
|---|---|---|
| W1 | `val3 withdraw <vault>#<id>` and `val3 withdraw --batch <batch>`, both with a required `--reason`. | Entry only, batch later: the batch is what D4 requires before a production import. |
| W2 | Run by a person outside the agent, it withdraws. Run inside Claude Code (`CLAUDECODE` set), it queues a proposal on the review list; a person's approval withdraws. | Refusing it inside the agent: the agent is often the first to see that an entry is wrong. Letting the agent withdraw: removes knowledge without a person. |
| W3 | A withdrawal is final. Knowledge withdrawn by mistake comes back as a new entry through `val3 save` or an import, and is reviewed again. | `val3 restore`: one more state that can flip back and forth, more cases in search and in `as_of`. |
| W4 | Withdrawing an entry that superseded another does not bring the older one back; a notice names it. | Reviving the older entry: knowledge already shown wrong would be delivered again without anyone deciding it. |
| W5 | A table `withdrawal` and one SQL fragment for "live" (not superseded, not withdrawn, both as of an optional moment), used by every reader. | A database view: cannot take `as_of`, so historical search would keep its own copy. Reusing `supersession` with a null `new_id`: changes the meaning of an existing constraint and every reader must tell the two apart. |
| W6 | Proposals from the extractor are deferred. | — |
| W7 | Every withdrawal states its cause: `--wrong` (the knowledge was never true) or `--obsolete` (it was true and is no longer needed). | Free text only: the share of admitted knowledge that was false could not be measured, and that share is what the deferred audit needs. |

## 3. Interface

```
val3 withdraw <vault>#<id> (--wrong | --obsolete) --reason "<text>"
val3 withdraw --batch <batch> (--wrong | --obsolete) --reason "<text>"
```

- Exactly one of `<vault>#<id>` and `--batch`; exactly one of `--wrong` and `--obsolete`; `--reason`
  required and not blank after stripping.
  Otherwise exit 2.
- `<vault>` must be the project's home vault; a linked vault is read only (exit 1).
- Exit 1: no such entry; entry already withdrawn (the message gives when and by whom); batch with no
  admitted entries.
- Withdrawing an entry that is already superseded is allowed: the reason is recorded, delivery does
  not change.

### 3.1 Outside the agent

Withdraws at once, in one transaction under the vault lock (`_serialize`). Output:

```
withdrawn invoices#12
```

For a batch:

```
batch 0b08190987d3b1bc: 41 withdrawn, 2 pending rejected, 1 not withdrawn (existed before the batch), 0 already withdrawn
```

`withdrawn_by` is the current user (`getpass.getuser()`), as `review approve` does.

### 3.2 Inside the agent

When `CLAUDECODE` is set to a non-empty value, nothing is withdrawn. A `review_item` with
`reason = 'withdrawal'` and payload `{"title", "ref" | "batch", "cause", "reason"}` is queued; output
`proposed: review #N`, exit 0. The same reference, cause and reason give the same review item
(`content_hash`), as other queued items.

The checks of §3 run before queueing: a proposal for a missing entry, an entry already withdrawn or
an empty batch fails the same way.

- **Approve** (a person: `val3 review approve`, `val3 review --tty`): withdraws as in §3.1, with
  `withdrawn_by` = the decider and `review_id` set. An entry withdrawn meanwhile makes the decision
  stale (`Val3Error`, handled by the TUI as today); for a batch, entries withdrawn meanwhile are
  counted as already withdrawn.
- **Reject**: no effect on the entry.
- **Card**: `val3 show` of the entry, or for a batch its id and the number of admitted entries, then
  `approving withdraws (<cause>): <reason>` (styled as a warning in the TUI, like `approving supersedes`).

## 4. Data

```sql
CREATE TABLE withdrawal (
    entry_id bigint PRIMARY KEY REFERENCES entry (id),
    cause text NOT NULL CHECK (cause IN ('wrong', 'obsolete')),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    withdrawn_by text NOT NULL,
    review_id bigint REFERENCES review_item (id),
    batch text,
    created_at timestamptz NOT NULL DEFAULT now()
);
```

- Append-only like every vault table (`append_only` triggers).
- `review_item.reason` adds `withdrawal` (proposal) and `withdrawn` (notice).
- `val3 vault upgrade` creates the table on existing vaults, grants each role on it the SELECT and
  INSERT it holds on `entry` (as for `embedding`), and widens the reason check (`_CHECKS`).
- Each withdrawal logs an `event` with `action = 'withdrawn'`, the entry and review ids, and
  `{"cause", "reason", "batch"}` in `detail`.

### 4.1 Live entries

One fragment in Python, `live(alias, as_of)`: an entry is live when it has no `supersession` row as
old entry and no `withdrawal` row, each with `created_at <= as_of` when `as_of` is given. It replaces
the three copies of the supersession test:

- `search.py` (both queries): delivery, `recall`, `evaluate` with `as_of`;
- `views.session_index`;
- `writer._require`: a candidate citing a withdrawn entry fails with
  `<ref> was withdrawn (<cause>): <reason>`.

`val3 show` of a withdrawn entry adds `WITHDRAWN (<cause>) <date> by <who>: <reason>`, a warning in the TUI.

### 4.2 Batch membership

The entries of batch B are the `entry_id` of `review_decision` rows with `decision = 'approved'`
whose `review_item` has `reason = 'import'` and `payload->'import'->>'batch' = B`, excluding those
approved as a duplicate of an entry that existed before (the entry was not created by that
decision: `entry.created_at < review_decision.created_at`). Those are reported as "not withdrawn
(existed before the batch)".

In the same transaction, the batch's import items still pending are rejected with `decided_by` =
the person who withdraws.

## 5. Notices

When a withdrawal has consequences, one `review_item` with `reason = 'withdrawn'` per withdrawn
entry, listing:

- the live recipes that cite the entry as a step fact or a step tool;
- the entry it superseded, if any, so that a person decides whether to save it again.

Like the other notices, it can only be acknowledged (`NOTICES` in the writer).

## 6. Tests

CLI as a subprocess against a disposable PostgreSQL; no package internals.

1. Outside the agent: after `withdraw`, `recall` and the session index no longer return the entry;
   `show` prints `WITHDRAWN`; an `event` row exists.
2. A second `withdraw` of the same entry: exit 1 with when and by whom.
3. Usage: no reference and no `--batch`, both, neither or both of `--wrong` and `--obsolete`, blank
   `--reason`: exit 2. Linked vault: exit 1.
4. Inside the agent (`CLAUDECODE=1`): proposal queued, entry still live; approve: withdrawn with
   `review_id`; reject: still live; same proposal twice: same review id.
5. Proposal approved after a direct withdrawal: stale, exit 1.
6. Batch (fake extractor, as in `test_import.py`): one admitted entry, one duplicate of an older entry,
   one pending; counts as in §3.1; the duplicate stays live; the pending item is rejected.
7. Recipe citing a withdrawn fact: notice queued; saving a new recipe citing it fails.
8. `#2` supersedes `#1`; withdrawing `#2`: `#1` stays out of delivery and is named in the notice.
9. `evaluate` with `as_of` before the withdrawal still finds the entry.
10. `vault upgrade` on a vault without the table: table, grants and reason check added.
11. `UPDATE` and `DELETE` on `withdrawal` fail.

## 7. Out of scope

Extractor proposals (W6), `val3 restore` (W3), withdrawals in `val3 status`.

W2 (a person approves what the agent proposes) is kept for this version and revisited with the entry
lifecycle notes (`2026-10-14-entry-lifecycle-notes.md`): a person's approval is not a reliable check.

## 8. Documentation

- ADR 0005.
- Import design D4: pointer to this document.
- CONTEXT.md: **Withdrawal**; **Session index** and search read "live" as not superseded and not
  withdrawn.
- README: a "Withdrawing" section.
