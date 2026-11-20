# VAL3 — capture settles its own near duplicates; one entry may replace several

Date: 2026-10-28. Status: **approved by the owner 2026-10-28** (builder check: 7 questions, settled by the architect). Follows the automatic-admission design
(`2026-10-24-automatic-admission-design.md`, ADR 0008) after its first days on the third adopter.

## 1. Problem

Plan 10 admits every entry whose evidence checks and marks each close neighbour (same kind, cosine
similarity at or above `[embedding] neighbour`, 0.70) *to settle* for the agent (D2–D3, S1–S4). Three days on
the third adopter (measured 2026-10-27; script, rows and labels in the evaluation directory `eval/lookup/`):

- 153 entries admitted, 55 neighbour pairs, **28 marks open**. 50 of the 55 pairs come from capture, 5 from
  `val3 save`.
- The architect read every pair: 20 are a repeat or a replacement (one tool captured five times
  across four sessions; the host's disks and network port twice or three times), 11 overlap, 19 are distinct.
- **Capture re-proposes what is stored** because the extractor's existing-entry lookup searched only the
  user's words of the slice, and the user's words are "continue", "restarted, continue", "1. yes 2. yes". The
  stored twin was shown to the extractor for 2 of the 20 repeats. Searching the slice's own text too (merged
  2026-10-28, `extract.py`) shows it for 11; adding a search by meaning over the slice, for 14. No search over
  a slice of up to 120,000 characters shows all of them, and once shown the extractor still proposed a near
  entry at least once.
- **The agent does not settle capture's marks.** It settled 9 pairs, all ones its own `val3 save` had just
  raised, all `--keep`. Three of the 9 are wrong: one repeat, and two replacements where `--replace` was
  refused with "an entry supersedes one entry at most" and the agent fell back to `--keep`.
- Same-kind pairs by score: at 0.80 and above 12 of 14 are repeats or replacements; from 0.70 to 0.75, 2 of 19.
  The third adopter's threshold was raised to 0.75 on 2026-10-28.

The case that frames the design (pairs #46/#44 and #46/#22): the host's wired port was named `eth0` on the old
system (#22, #44) and `lan0` since 2026-10-26 (#46). #46 replaced #22; #44 stayed live because an entry could
replace only one.

## 2. Owner's rulings (2026-10-27/28)

| # | Ruling |
|---|---|
| C1 | When several entries describe the same specific thing, they do not live side by side: what holds now is established and **one entry replaces all of them**. |
| C2 | Replacing is archiving: a replaced entry leaves delivery and stays readable (`val3 show`, its evidence, its "superseded by" line) for the rare case of tracing how an item changed. No separate archive. |
| C3 | Capture decides at extraction time, from the slice: its most recent measurement counts as the current state. Running a fresh check on the host is optional, never required. |
| C4 | The three steps go ahead: the lookup fix and the 0.75 threshold (done 2026-10-28) and this design. |

## 3. Decisions

### N→1 supersession

| # | Decision |
|---|---|
| R1 | An entry may supersede **several** entries. The schema already allows it (`supersession.old_id` is the key; `new_id` repeats); the rule "one at most" lives only in `val3 settle` and in reads that take one row. |
| R2 | A candidate's `supersedes` takes one ref or a list of refs, in `val3 save` and from the extractor. Every ref is checked as today (same kind, live, home vault); one bad ref refuses the whole candidate. Stored payloads (`review_item`) and reads accept both forms; no migration. *(architect)* |
| R3 | `val3 settle <new> <near> --replace` works when `<new>` already supersedes another entry. |
| R4 | Reads that show one supersession show all of them: `val3 show` lists every entry replaced on one line (`supersedes: a, b`), without a cap; replay and re-check (`recheck.py`), already keyed by the old entry, need no change beyond what a test proves. |

### Capture settles a candidate's neighbours

| # | Decision |
|---|---|
| K1 | After the extractor answers a slice, and before submitting, capture computes the vectors of its candidates and finds each candidate's close neighbours among the **live entries of the home vault**, same kind, at or above `[embedding] neighbour` — the same rule as the neighbour check of plan 10. |
| K2 | If any candidate of the slice has neighbours, capture makes **one more extractor call for the slice**: the slice again, each such candidate with its neighbours (ref, kind, title, statement, admission date, the dates its evidence names if any). For each candidate the answer is one of: **new** (distinct from every neighbour), **repeat of** a neighbour (the candidate adds nothing), **replaces** one or more neighbours (C1), or **undecided**. The rules given to it: the slice's most recent measurement is the current state (C3); a candidate replaces a neighbour only when it states the current value of what the neighbour states **and** keeps every part of the neighbour that still holds; otherwise it is new. The second call does not rewrite candidates. *(architect: a rewrite would need its own evidence check, its own vector and its own fallback; a candidate that drops detail stays new and the pair is recorded `keep`.)* |
| K2a | The second call is told the slice's date (timestamp of its last transcript record) and each neighbour's admission date. Code enforces the order: a neighbour admitted after the slice's last record cannot be replaced — the drainer may read an old session after a newer one stored the current value; that pair is treated as **undecided**. *(architect: the extractor still judges what holds; the date check only stops an old slice from replacing a newer entry.)* |
| K2b | A neighbour named in the first call's own `supersedes` joins the candidate's neighbours in the second call whatever its score; the second call's answer decides the final `supersedes`. The first call's prompt is unchanged. *(architect)* |
| K3 | Outcomes: **new** — admitted; each pair it was compared with is recorded as settled `keep`, by `extractor <session>`. **repeat** — not admitted; counted in the extraction summary as `repeat` (its own count: `duplicate` stays the exact-text twin of `writer._duplicate`), logged with the neighbour's ref, and a memory note that proposed it gets the neighbour's ref as a duplicate does today. **replaces** — admitted with `supersedes` naming every neighbour it replaces (R2), each pair recorded settled `replace`; pairs with neighbours it does not replace are recorded `keep`. **undecided**, or the second call fails or times out, or vectors cannot be computed — submitted as today, and plan 10's neighbour check marks it *to settle* for the agent. |
| K3a | A candidate compared in the second call is admitted **with its vector**: capture passes the vector it computed in K1, and the admission stores it, runs plan 10's neighbour query and writes the settlements of K3, all in the admission's transaction under the writer lock. Settlements are written only for pairs that query finds; a pair it finds that the second call did not see stays an open mark. *(architect: `settlement` references `neighbour`, which today only `store_vectors` fills after admission; settling in a later pass would leave marks open if it fails.)* |
| K3b | A neighbour withdrawn or superseded between the second call and the admission is dropped from the candidate's `supersedes` and from its settlements; if none is left, the candidate is admitted as new. It is not refused for it. *(architect)* |
| K4 | Candidates of one answer are not compared with each other (the four pairs born in one answer were all distinct). |
| K5 | Without `[embedding]` none of this runs; capture works as today. |
| K6 | `val3 save` is unchanged: the agent saving an entry already sees its neighbours on stderr and settles them (S1). |

### Marks already open

| # | Decision |
|---|---|
| M1 | The 28 open marks of the third adopter and the duplicates already admitted are not migrated. They stay for the agent, as today. *(architect: the session that owns that vault can settle them now that `--replace` takes several; a sweep through the new extractor call would be code that runs once.)* |

## 4. Non-goals and anti-goals

- No fresh check on the host at capture time (C3): the extractor has no tools and keeps none.
- No change to per-prompt delivery, the index layout, the floor or `val3 save`.
- No comparison of a candidate with linked vaults: supersession and settlement act on the home vault only.
- Anti-goal: a stored entry replaced by a candidate whose own evidence is older than the stored entry's.
- Anti-goal: the agent asked to settle a pair the slice could decide. If capture's marks keep piling up, the
  design has failed.
- Anti-goal: an entry silently lost. Every repeat and every replacement is in the event log with both refs.

## 5. Left to the builder

- Shape of the second call: prompt text, its JSON schema, its prompt version, and how much of the slice it
  carries (whole slice or windows around each candidate's evidence quotes) within the extractor's timeout.
- Where capture gets the candidates' vectors (one `embed` call per slice) and how K1 reuses the writer's
  neighbour query.
- Record of a repeat (event detail, summary wording) and of `settled_by` for capture.
- Order of operations when one slice's candidate replaces an entry another candidate of the same slice is
  compared with.
- Whether the vector of K1 is the one stored (same text, same space) or the admission embeds again.
- Tests per the project's conventions: CLI as a subprocess, disposable PostgreSQL, the fake extractor and the
  fake embedder; the second call is driven by the fake extractor's scripted answers.
