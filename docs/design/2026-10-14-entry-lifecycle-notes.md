# VAL3 — entry lifecycle: notes for discussion

Date: 2026-10-14. Status: **superseded by `2026-10-20-entry-lifecycle-design.md`.** Input for a conversation with the owner after
withdrawal (ADR 0005) is implemented.

## 1. Principle (owner, 2026-10-14)

Entries do not expire. Knowledge that is still valid stays live however old it is. An entry leaves
delivery only when:

| Change | Mechanism | Today |
|---|---|---|
| Mutated: same knowledge, another value | supersession 1→1 | yes; the extractor proposes it only when the session proves the entry wrong (6 supersessions in the first adopter's vault, 0 in the second) |
| Superseded by better knowledge | supersession, often several entries by one (three facts consolidated into a recipe) | 1→1 only: a candidate names one `supersedes` |
| No longer needed | withdrawal, cause `obsolete` | ADR 0005 |
| Never true | withdrawal, cause `wrong` | ADR 0005 |

Age and lack of use are not signals.

## 2. Open question: who notices that knowledge changed

| Signal | Covers | Cost |
|---|---|---|
| Contradiction in a session: the entry was delivered and the session shows otherwise | any evidence kind | exists; works only when the entry was delivered or listed to the extractor |
| The repository moved under `file` evidence: the quote is no longer at HEAD | imported entries and any future `file` evidence | `git show` per quote, no model; marks the entry *to re-check*, never withdraws it |
| Query, command or API evidence | — | no signal without re-running, which the design excludes |

Questions for the owner:

1. When the repository signal fires, who re-checks the entry (the agent in the next session that
   touches it, a scheduled run, a person), and where is the *to re-check* mark shown (session index,
   per-prompt delivery, `val3 status`)?
2. With what authority may the agent supersede or withdraw without a person, given that a person's
   approval is not a reliable check (owner, 2026-10-14)? One option: the agent acts when it brings
   mechanical evidence (a transcript quote or a `git show` that contradicts the entry), as admission
   already works; a person is asked only when no such evidence exists.
3. Should a supersession name several old entries (N→1)?

## 3. Related

- Review by a person approves nearly everything (43/43 at the first import): admission should rest
  on mechanical checks, with withdrawal as the correction path; a person sees only what a machine
  cannot decide.
- Delivery at execution time (TODO): the agent never looked memory up by itself before acting
  (0 `val3 recall` in about 500 tool calls of the first adopter, 2026-10-10 to 2026-10-14).
