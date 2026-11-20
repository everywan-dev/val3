# ADR 0006 — Re-check against the repository and the agent's authority to withdraw

Date: 2026-10-20. Status: accepted (2026-10-20). Amends ADR 0003 and W2 of ADR 0005
(`docs/design/2026-10-13-withdrawal-design.md`). Implements `docs/design/2026-10-20-entry-lifecycle-design.md`.

## Context

Entries do not expire. An imported entry's `file` quote is checked once, at import; nothing noticed when
the repository moved under it, and the only signal that an entry changed was a session that contradicted
it. Withdrawal inside the agent was always a proposal for a person, yet a person's approval is not a
reliable check: it attests, it does not verify. Admission already trusts mechanical evidence (a quote
from a tool output in the transcript, or the user's literal words for a preference or a decision).

## Decision

- R1: when the repository moves under `file` evidence, the entry is marked *to re-check*; the agent of the
  next session that receives it re-checks. The mark shows in the session index, per-prompt delivery,
  `val3 show` and `val3 status`. No scheduled run.
- L1: the mark is computed when read, never stored: a home-vault entry is marked when one of its current
  `file` quotes is not in `HEAD:<path>` (file missing or text changed). Only quotes whose commit is in the
  repository are checked; git failures or a 3-second budget exhausted leave every entry unmarked.
- L4: `val3 confirm <vault>#<id>` appends a new current evidence set (`file` items checked at HEAD and
  stored at its sha; other items checked against the transcript, as `val3 save` does). The new set replaces
  the old one as the baseline.
- R2 and L6: the agent withdraws without a person only with mechanical evidence that would admit without
  review: any tool item, or `user` items on a preference or a decision. `withdrawn_by` is
  `agent <session id>`. Without such evidence the withdrawal stays a proposal for a person.
- Supersession is unchanged: it follows admission.
- R3: supersession of several entries by one is deferred until a real case.

Detail: `docs/design/2026-10-20-entry-lifecycle-design.md`.

## Consequences

- ADR 0003's "recall does not re-read the repository" no longer holds: reading the index, delivery,
  `show` and `status` read `HEAD` for the home vault's current `file` quotes. Quotes are still checked
  once at import; the read only marks.
- W2 of ADR 0005 (inside the agent a withdrawal is always a proposal) is amended by L6.
- Withdrawal evidence lives in `withdrawal.evidence`; existing vaults need `val3 vault upgrade` before
  `withdraw --evidence` works there.
- A confirmation without `file` items ends the repository signal for that entry; so does a supersession,
  since `val3 save` takes no `file` evidence.
- Output of any `val3` subcommand in the transcript is never evidence, so the agent cannot confirm or
  withdraw by quoting val3's own output.

## Amendment 1, 2026-11-17: what withdrawal evidence proves, and what it does not

The open question "evidence forged by the agent" was challenged with a second model and decided by the owner.

- What the check does: a tool quote must be in the output of one call whose input does not contain it, so a
  command that carries the quote literally (an `echo`, a value given in a query's `WHERE`) gives no evidence;
  output of `val3` and of the memory tools is excluded by origin. The same check serves `save`, `confirm` and
  `withdraw`.
- No list of refused commands (`echo`, `printf`, `cat`, a heredoc): the rule above already covers the literal
  case, and such a list would refuse a query sent by heredoc whose row the database computed. Both cases are
  tests of the withdrawal suite.
- **The limit, declared**: evidence is checked provenance, not proof. That a quote bears on the withdrawn
  entry is the agent's judgement; the writer compares the quote with neither the entry nor the reason, and
  `reproduction` is the agent's description; no call decides the check by it (a direct withdrawal only marks
  it when it equals the command, below). A true line of another query can
  withdraw the wrong entry. Text the agent wrote and then read back with another call still passes, and the
  transcript itself is a local file (ADR 0013).
- To build, with its own design: a direct withdrawal with tool evidence records, beside each quote, the call
  that printed it (all of them when several qualify), by tool, call id and a SHA-256 of the call's complete
  input, so that a wrong attribution can be examined afterwards. The input's text is never copied (owner,
  after the design's challenge): a command line can hold a secret and the row is permanent and mirrored.
  Instead the agent writes the exact command as `reproduction`, without secrets, and the writer marks the
  item when it equals the real command. Detail: `docs/design/2026-11-17-withdrawal-call-record-design.md`.
- Scope: the direct withdrawal of one entry with evidence. `settle --drop` keeps its own rule (the newer
  entry of an open pair withdrawn as a duplicate, without a quote, when the older has equal or better
  backing). Admission is unchanged; recipes and tools keep their rules ("quote checked", not "procedure
  tried").
- No reinstatement: a withdrawal stays final (ADR 0005, ADR 0012 section 6). A mistaken one is recovered by
  saving the knowledge as a new entry with corrected text, under a new ref.
