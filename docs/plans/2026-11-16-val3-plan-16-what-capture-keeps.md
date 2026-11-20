# Plan 16 — What capture keeps

Date: 2026-11-16. Status: revised after its challenge (last section); stages 1 and 1b run (results below). By stage 1b's rule
stage 2 was not to be run; **the owner decided on 2026-11-18 to run it as written** (section "Stage 2 as run"): run on the
development half, no arm passes, nothing is changed. Replaces part F of plan 15 as the next work;
`memory_save` (plan 15, F and F0) stays open behind it.

## Why
The bench on real work (2026-11-16; kit outside the repository, `eval/chain-2026-11/README.md`): 24 windows of
real sessions of two adopters, 110 questions written from them by readers blind to vault and documents.
- With the reference answers planted, sessions answer 103 of 110; with the project's documents alone, 70 to 73.
  A memory that holds the fact in the reference's words is used: the room is 30 points.
- Today's capture, replayed over the windows into an empty vault, is not told apart from the documents (70 to 75
  with the present prompt, 74 to 82 with the restricted one, by two gradings; judges' own noise about 5).
- The entries captured from a question's window fully hold its reference in 27 of 110 (present prompt) and 34
  (restricted); partly in 42 and 17 (one judge). Where it is fully held the session answers right (24 of 27, 30
  of 34).
- By kind of question, fully held (present / restricted, of n): pitfall 10 / 18 of 30; decision 7 / 9 of 29;
  characteristic 6 / 2 of 19; procedure 2 / 3 of 12; location 2 / 1 of 10; preference 0 / 1 of 10.
- Part G of plan 15, on another window and reference: the best arm held a third of the critical claims.
- Volume: 171 entries admitted for the 24 windows with the present prompt, 106 with the restricted one, at about
  one dollar a window with Opus.

Measured on the replay's vaults and logs after the challenge, before this revision:
- What the extractor proposed and the pipeline then lost is small: 0 repeats, 0 duplicates, 2 rejected candidates
  (present) and 3 (restricted), no supersession, no withdrawal; every admitted entry is live and was shown to
  the held judge. 4 entries per arm are unverified (kept out of automatic delivery), 1 quote failed per arm.
  So the misses are upstream of the writer and of the neighbour comparison: in what a call receives or in what
  it proposes.
- The raw answers of the extractor calls were not kept, nor the "Existing entries" list each call received.
- 18 of the 24 windows are sent as 2 calls, 5 as 1, 1 as 3 (85 000 to 140 000 characters per call).
- The 24 windows come from 11 transcripts (third adopter 5, fifth 6); no two transcripts share a record.
- Not measured: a reference that another window's entry holds. The held reading looks only at the entries of
  the question's own window; a later window of the same transcript may have been told "already stored".

So the questions are: where is a reference lost before the writer, why are so many only partly there, and can
capture be changed without filling the vault with noise.

## Limits said first
- One reference (questions written by one kind of reader from the same windows), two projects of one owner. A
  reader who has seen a window asks about what it revealed; later work may never need it. Coverage of these
  questions is a diagnostic of capture, not a measure of useful memory. Part D of plan 15 found sessions rarely
  look for what memory could hold. Coverage is necessary, not sufficient.
- The aggregate figures above (by arm and by kind, over all 110 questions) were read before the split below and
  shaped the list of candidate arms. The held-back half is unread from here on, not unseen.
- Empty-vault replay of separate windows says nothing of months of accumulated entries: stale values, mistaken
  replacements, search among many more entries, proposals suppressed by a long list of existing entries.
- "What is held is used" is the oracle's result for entries worded as the answer, in a small vault. It is not
  shown for longer or differently titled entries in a large one; ranking stays out of this plan for cost, not
  because it is settled.

## The split, fixed now
By transcript, whole: within each project the transcripts are ordered by number of windows (most first), then by
file name; the first goes to development, each next one to the half with fewer windows, a tie to the held-back
half. This uses no result.
- Development (12 windows, 57 questions): a-1 a-5 a-6 a-7; b-3 b-5 b-9 b-11 b-13 b-14 b-15 b-16.
- Held back (12 windows, 53 questions): a-2 a-3 a-4 a-8; b-1 b-2 b-4 b-6 b-7 b-8 b-10 b-12.
The halves differ in their mix (questions asked across projects: 29 in development, 8 held back); reported with
every figure. No reader, judge or script of stages 1 and 2 opens a held-back reference, entry or window until an
arm is frozen.

## Stage 1 — Where a reference is lost (development half, no new capture)
For each of the 57 references and each of the two captured vaults (present, restricted):
1. Two blind judges redo the held reading (held / partly / not held) over the window's entries. Reported: each
   judge's counts, the 3 by 3 table of their readings, agreement on the held / not-held cut. The earlier single
   judge's reading is a third column, not the truth.
2. For every reference not held by both judges, a reader gets the reference, the transcript part of each call's
   input rebuilt with `condense` (the calls' boundaries as they were), the window's entries and the vault's
   other entries, and fills two fields:
   - **loss point**, one, observable:
     `elsewhere` (an entry of another window holds it); `not in the input` (no call's transcript text contains
     what the reference says: clipped, excluded, or split so that no single call has it); `in the input, no
     entry`; `entry incomplete` (an entry is about it and lacks the part the question needs: the reason, the
     value, the condition, the second half); `entry not deliverable` (held by an unverified entry only).
   - **suspected causes**, any of, or `unknown`:
     `rule` (the instructions exclude it as written: a failed attempt that is the pitfall, a state, something
     "a live source answers"); `no admissible evidence` (it is visible only in assistant text or in a tool
     result the evidence rules exclude); `existing entry` (an entry already stored at that time is close enough
     that "do not propose again" may have applied; the list the call saw is not kept, so this is an inference
     from dates and wording); `split` (the fact and its reason or result lie in different calls); `long call`;
     `compound reference` (the question joins facts no single entry would).
   The two rejected or three refused candidates are read from the vaults' events and listed apart.
3. A second reader repeats step 2 on all of them; agreement is reported per field. A table is read only where
   the two readers agree on the loss point in at least 70 % of the references; below that, the labels are
   rewritten once and both read again.
4. Tables: loss point by kind and prompt; causes by loss point; counted in distinct references.
Cost: judges and readers only, estimated under 15 dollars. No change to VAL3.

What stage 1 decides: which arms are worth running. An arm is kept when the references it could recover
(distinct, development half, by loss point and cause) are at least 6, or when the cause is `unknown` for more
than a third of a loss point it targets and the arm is the cheap probe of it. The list and the thresholds of
stage 2 are then fixed in this file before any arm runs.

### Result of stage 1 (2026-11-16, $7.14; kit `out/stage1/report.txt`)
Development half, 57 references, two Sonnet judges and two Sonnet readers; no held-back window opened (checked
by the script).
- Held by both judges: 10 of 57 (present prompt), 13 (restricted). The judges agree on the held / not-held cut
  in 56 of 57 for each prompt; with the earlier single judge in 53 to 56. Reading the same entries twice moves
  the count by about 1.
- Loss point, 91 readings (47 present, 44 restricted), readers agree on 83 (91 %). In distinct references,
  either prompt: **in the input, no entry 33; entry incomplete 20**; not in the input 2; elsewhere 1; entry not
  deliverable 1 (doubtful: both readers describe that entry as incomplete); readers disagree 6.
  By prompt: no entry 21 / 28, incomplete 17 / 10: the restricted prompt writes fewer half-entries and leaves
  more out.
- Causes (the readers give the same set for 33 of 91 only: read "both name it / either names it"):
  - of the 33 with no entry: **no admissible evidence 26 / 30**, rule 7 / 18, compound reference 4 / 12,
    existing entry 1 / 2, long call 0 / 2, split 0 / 0;
  - of the 20 incomplete: **compound reference 14 / 16**, no admissible evidence 8 / 16, split 0 / 4, existing
    entry 1 / 2, rule 1 / 1.
  Where both readers say "no admissible evidence" for a missing entry, their quotes sit in assistant text (33),
  a tool call (24), a tool result marked not evidence (6), an ordinary tool result (15, which may contradict the
  cause; not read one by one), not found (2).
- Rejected candidates in these windows: 3, all for form (two titles over 200 characters, one quote under 8).
- By kind (present / restricted, no entry): preference 4 / 4 of the 5 read, location 3 / 2, characteristic 5 /
  8, pitfall 5 / 6, decision 3 / 5; incomplete is mostly decisions (10 / 7).
Limits: the readers searched the calls' text rather than reading it whole; `existing entry` and `elsewhere` are
undercounted (no entry of a held-back window was shown); causes are two readers' inference, with low agreement
on `rule` and `compound reference`.

A control on the leading cause, without model calls (kit `stage1_control.py`): the question writer's own quote
located in the calls' text, by the block it sits in, against held by both judges. Found for 35 of 57 (the
others are not verbatim). Present / restricted prompt, held of n: assistant text 4 / 5 of 15, tool call 2 / 3
of 6, ordinary tool result (quotable) 4 / 1 of 12, result marked not evidence 0 / 1 of 2. A reference whose
source can be quoted is held no more often than one whose source cannot. The question writers read 2 500
characters of each assistant text and 500 of each result, so most references come from what the assistant
wrote; "no admissible evidence" describes where the references come from and is **not shown to be why the
entry is missing**. The 12 windows hold 8 lines typed by the user: a preference there is stated by the
assistant or in excluded output.

What it says:
- Shown: the references are lost before the writer, and the text reaches the extractor. Of 57, two thirds have
  no entry at all under one prompt or the other, a third an entry without the part asked. Clipping, the size of
  a call, the list of existing entries and the writer are not where they are lost.
- Not shown: why the extractor proposes nothing. Two hypotheses stage 2 tells apart: selection (the call
  produces a handful of entries from 100 000 characters and stops) and the evidence wording (the prompt asks
  evidence for every fact, says in the next sentence that one without is stored unverified, and the replay
  admitted only 4 unverified entries per arm in 24 windows). The control leans to selection.
- A fifth of the misses are entries without the part the question joins to them (`compound reference`): partly
  the question writers' making, not capture's.
- Most of what the questions ask is what the agent concluded in its own text. That is also what the agent
  could save itself (`memory_save`, plan 15 F): see "For the owner".

## Stage 2 — Arms, measured on capture alone (development half)
Capture a window with an arm into an empty vault (the kit's replay, with its re-dating), then the two-judge held
reading. The kit wraps the extractor command (`VAL3_EXTRACTOR`) to keep each call's input and answer; VAL3 is
not changed for that.

Arms, fixed after stage 1 by its rule (at least 6 recoverable distinct references):
- A0 present prompt; A1 restricted prompt. Both are captured again here, so that every arm is read by the same
  judges under the same rule and the variation of one arm between two captures is known (A0 is captured twice).
- A0x2 **equal-budget control**: the present prompt twice, the second answer's candidates appended to the
  first's. Any arm that adds a call is compared with this one, not with A0.
- A5 **evidence not required to propose** (the test of the evidence wording): the present prompt, saying that
  a fact, preference or decision the slice states only in the assistant's text, in a call or in excluded output
  is still proposed, without evidence, and is stored unverified. Nothing else changes: the exclusions readers
  named under `rule` stay (the readers agree on 9 of them; no arm is spent on it). Held is reported for all
  live entries and for verified ones apart. For the unwanted-content reading, an entry is `unsupported` when the
  window does not state it in any block; one the assistant states and no output shows is counted apart as
  `asserted only`, and does not count against A5. As a candidate for deployment A5 depends on the owner's open
  decision on unverified entries (out of automatic delivery, to look at again about 2026-12-07).
- A2 **complete entries** (targets entry incomplete, 20): each statement carries what a reader needs to act on
  it (what, the reason or cause, the value or condition, where it applies). Grouping is not changed. Its gain
  is reported with and without the 14 to 16 references marked `compound reference`.
- A3 **second pass** (targets no entry, 33; the test of selection): a second call with the same
  text and the candidates so far: "what would a later session have to find out again that these do not hold",
  under the present evidence wording. A3 gaining over A0x2 and A5 not over A0 says selection; the reverse says
  the evidence wording.
- Dropped: A4 both prompts (no loss point is its own; A1 stays as a baseline), A6 smaller calls (`long call`
  0 to 2, `split` 0 to 4), A7 without existing entries (`existing entry` 1 to 2), any change to clipping
  (`not in the input` 2).
Each arm is real code on a branch (a prompt file or a switch), run by the kit as capture runs it.

Measures per arm, per window and in total: references held by both judges, by each judge, partly held; entries
admitted, unverified, quote-failed; characters stored; calls and cost; semantic duplicates; and **unwanted
content**, read by a blind judge on every admitted entry of the development half (shuffled across arms), per
entry: wrong or unsupported by the window (the judge has the window), a repeat of another entry of the same
arm, routine (a one-off state, a step, a receipt), already in the documents, or worth keeping. Reported as a
share and as an absolute count of unwanted entries and characters per window. A second judge reads the
entries of any arm within 5 points or 1 entry per window of a threshold.

Rules, fixed before any arm runs:
- Smallest gain that counts: 8 references of 57, and at least twice the difference between the two captures of
  A0 (two readings of the same entries differ by about 1 of 57, stage 1).
- An arm passes if, against its control (A0x2 for an arm with an added call, else the better of A0 and A1), it
  gains at least that many references by both judges and by each judge alone; the gain is not from fewer than
  4 windows; its unwanted entries per window do not exceed the control's by more than 2; wrong or
  unsupported entries do not exceed the control's; and its cost per window is at most twice A0's.
- Among those that pass, the one with the most references gained per added dollar of capture (characters stored
  reported beside it) is frozen and run once on the held-back half with A0 and its control; it stands if it
  passes there by the same rule. Losses by project and by kind are reported; an arm that loses 3 or more
  references of one kind against its control does not stand.
- If none passes, nothing is changed and stage 1's tables are the result.
- Part G's window is a regression check, not a confirmation: the standing arm must not fall below prompt 1's
  count there by more than the run-to-run variation part G measured (5 of 80).

## Stage 2 as run (fixed 2026-11-18, before any arm is captured)
The owner, 2026-11-18: stage 2 is run as fixed above (A0 twice, A1, A0x2, A3, A5, A2 and the held-back run),
about 170 dollars. This sets aside stage 1b's rule, not its finding: whatever stage 2 shows is about holding the
110 references, which stand for 4 of 33 later needs (stage 1b); that limit goes with every result.
- Code under test: branch `feature/plan-16-stage-2-arms` (source as of e6a2fbb; later commits there touch tests and docs only) (a measurement branch, not merged), loaded in every
  capture child. `VAL3_ARM` picks the arm in `extract.extract`; A1 is `[extractor] focus = true`; A0x2 calls the
  extractor twice with the same material and submits both answers' candidates; A3 calls again with the first
  answer's candidates under "Candidates so far". Calls and cost count both calls. The texts, appended to the
  present prompt (`src/val3/extractor_arms.md` on the branch):
  - A5: "Evidence is not required to propose. A fact, preference or decision that the slice states only in the
    assistant's text, in a TOOL CALL or in a "TOOL RESULT (not evidence):" block is still proposed, without
    evidence: it is stored unverified. Give evidence whenever the slice holds it. What to keep and what not to
    keep are unchanged."
  - A2: "Complete entries. Each statement carries what a reader needs to act on it without the session: what
    holds, its reason or cause, the value or condition, and where it applies."
  - A3 (second call only): "Second pass. "Candidates so far" lists what was already proposed for this slice.
    Propose only what they miss: what a later session would have to find out again that these candidates and
    the existing entries do not hold. Same rules and evidence as above. Do not repeat or rephrase a candidate so
    far. An empty list is the right answer when nothing is missing."
- Replay: kit `stage2.py`; one empty vault per arm and project (names that do not spell the arm), the 12
  development windows of each project in order of their first record, re-dated after each window as the full
  run did; the Opus extractor, `neighbour = 0.75`. Every extractor call's argv, input and answer are kept. Unlike
  the full run, the vaults hold no held-back window's entries, so the existing entries a call sees differ from
  stage 1's; A0 is captured again for that reason.
- Readings: the held reading by two blind judges per window with the seven arms as seven items of one packet
  (the same packet for every arm); the unwanted-content reading by one blind reader per window over all arms'
  live entries, sets shuffled, with the window and the project's documents.
- Ceiling for the development half: 140 dollars. The held-back run goes only if an arm passes.

### Result of stage 2, development half (2026-11-18, $126.70; kit `out/stage2/report.txt`)
84 captures of 84 (12 windows, 7 arms), no failed child; `check` passed (each arm's text in its own prompts only,
A0x2's two calls with the same input, A3's second call with the first answer's candidates, the branch's code
loaded). Capture $115.21 (estimate after the trial: about 95), judges $11.49. The two held judges agree on the
held cut in 55 to 56 of 57 per arm.

| arm | held, both judges | A | B | entries live | characters | calls | cost | unwanted | wrong or unsupported |
|---|---|---|---|---|---|---|---|---|---|
| A0 (first) | 11 | 11 | 12 | 79 | 33 779 | 32 | 14.35 | 54 | 1 |
| A0 (second) | 10 | 10 | 12 | 82 | 35 113 | 29 | 13.14 | 60 | 0 |
| A1 restricted | 21 | 21 | 22 | 61 | 30 847 | 25 | 11.36 | 43 | 1 |
| A0x2 | 13 | 13 | 14 | 165 | 73 970 | 54 | 18.72 | 142 (67 repeats) | 0 |
| A3 second pass | 11 | 11 | 13 | 120 | 53 970 | 60 | 27.89 | 85 | 0 |
| A5 evidence not required | 13 | 13 | 14 | 88 | 39 443 | 33 | 15.03 | 59 | 1 |
| A2 complete entries | 13 | 13 | 14 | 77 | 46 638 | 32 | 14.71 | 57 | 3 |

By the rules: the two captures of A0 differ by 1 (3 references change), so the smallest gain is 8. **No arm
passes; nothing is changed.** A3 against A0x2: -2, and its cost per window ($2.32) is over twice A0's ($1.15).
A5 and A2 against the better baseline, A1: -8 each, and each loses 3 or more references of one kind; A2 also has
more wrong entries (3 against 1). The held-back run is not due (no arm stands); it is not run.
- The second reader of unwanted content was due for A5 and A2 (within 1 entry per window of the threshold) and
  was not run: both fail on the gain alone, whatever it reads.
- Unwanted content is mostly `in_documents` (40 to 71 per arm): the documents are of 2026-11-14, later than the
  windows, so they hold what those sessions wrote down (the limit said for the bench). `routine` 0 to 13,
  `wrong` 0 to 3, `unsupported` 0 in every arm.
- Not a result by the rule, seen in the table: **A1, the restricted prompt, holds 21 against 11 and 10** (14
  references gained and 3 or 4 lost against each capture of A0, gains in 7 windows, mostly pitfalls and
  decisions), with fewer entries, fewer unwanted ones and less cost. A1 was a baseline here, captured once, so
  its own variation between captures is not known; in stage 1 (full run's vaults, which held the held-back
  windows too) it held 13 against 10. It is the switch `[extractor] focus` that the fourth adopter is comparing
  in use until 2026-11-30 (10 captures, blind judge, loss check, its README's rule): that comparison decides,
  not this table.
- Selection against wording, the question A3 and A5 were to tell apart: not settled. A3 (selection) gained
  nothing over its control. A5 did not test the wording: its text reached every prompt, but the extractor
  proposed 1 fact, preference or decision without evidence in 91 candidates (A0: 0 of 81 and 0 of 84), and 3 of
  its 88 entries are unverified (A0: 2 and 1). A second call adds entries, not references held (A0x2 doubles
  entries and gains 2).
- Not computed, since no arm comes near the gain: A5's held count over verified entries alone, and A2's gain
  without the references marked `compound reference`.
- A1's 21 comes from one capture, read in a packet of seven arms (stage 1's held reading had two per packet).
- Limit, as stage 1b found: the 57 references stand for few of later needs; a gain here would have been about
  holding them.

## Stage 3 — Does it show in the answers
Only for the arm that stands, and only after the owner's answer below. The bench's task sessions on the 53
held-back questions and, apart, on the 57 of development; arms: documents only, A0's vault, the standing arm's
vault; two runs per cell; each answer graded alone against its reference by two judges, a third on
disagreement, never beside another arm's answer; repeatability measured first on a repeated set.
- Unit: a question; its score in an arm is the number of its two runs graded correct (0, 1, 2). Arm X is better
  than Y on a question when its score is higher; equal scores are ties and are dropped from the test.
- Rule: the standing arm is better than **A0** on more questions than worse, one-sided sign test p < 0.05 over
  all 110, with the held-back 53 alone pointing the same way; against the **documents** it is not worse (not
  more questions worse than better). Reported per transcript, since questions of one transcript are not
  independent: the result does not stand if it comes from one or two transcripts.
- Harm: every answer that is wrong in the standing arm and not wrong in both other arms is read; none may come
  from an entry the arm added that is wrong, unsupported or out of date within the window.
- If it stands, it becomes a switch, turned on per adopter with the comparison plan 15's part G wrote.
The rule is against today's capture, with no harm against the documents; it does not ask that memory beat the
documents (the owner's criterion of 2026-11-14).

## For the owner, before stage 2 is run
Stage 1 adds to this: the references are mostly what the agent concluded in its own text, which is what an
agent's own save would hold. That makes the check of way 2 more worth its cost, and F0 with it.
The challenge's strongest objection is not answered by anything above: questions written from the windows
measure what a reader of those windows found worth asking. An arm can win stages 2 and 3 and still not hold
what later work needs. Three ways, cheapest first:
1. Go on as written and say the limit with the result. No added cost.
2. **Recommended.** Before stage 2, one check with part D's material (already paid for: 320 knowledge episodes
   with what the agent then found): for the episodes of the two projects that fall after one of the 24
   windows in the same transcript, does an entry captured from the earlier windows hold what the agent then
   looked for, and would the reference of any of the 110 questions? If later needs are almost never things the
   windows held, tuning the extractor has little to give and `memory_save` (F0, F) goes first. Judges only,
   estimated under 10 dollars.
3. A chronological replay of whole transcripts into one persistent vault per arm, with later episodes as the
   test. The only design that answers accumulation and staleness; the fifth adopter's capture alone cost 72
   dollars in a week, so several arms would pass the plan's ceiling.
F0 (one interactive session with a logging server) does not depend on any of this and can be done when the
owner has ten minutes.

## Stage 1b — Did later work look for what the windows held (owner, 2026-11-16: way 2)
Material: part D's knowledge episodes (plan 15; each with what the agent then found or was told) of the two
projects that start after the end of a development window, in any transcript of the project; the development
windows' call texts and their captured entries (present and restricted); the 57 development references. No
held-back window, entry or reference is opened.
Per episode, a reader with the texts of the development windows that ended before it:
- `stated`: an earlier window states what the agent then found (quote required); `partly`; `not stated`;
- if stated or partly: do the entries captured from those windows hold it (held / partly / not held), per prompt;
- does any development reference written from those windows ask for it (yes / no).
A second reader on a third of the episodes; agreement on stated / not reported.
Rule, written before any result (thresholds are the architect's):
- under 10 % of the episodes `stated`: what later work looks for is mostly not in the earlier sessions that
  capture reads; tuning the extractor has little to give for it, stage 2 is not run and `memory_save` (F0, F)
  goes first;
- 25 % or more: stage 2 is run as written;
- between: reported to the owner with both readings.
Also reported, whatever the share: of the `stated` ones, how many the captured entries hold, and how many a
reference asks for (how far the 110 questions stand for later needs).
Limits: the 12 development windows are a small part of each project's history, so `not stated` means not in
those windows; part D's episode rule sets what counts as a need; one owner. Cost: readers only, ceiling 15
dollars.

### Result of stage 1b (2026-11-16, $12.10; kit `out/stage1b/report.txt`)
183 knowledge episodes qualify (third adopter 7, fifth 176; main sessions 97, subagents 86), all read; 13
excluded for overlapping a held-back window, 87 for starting before any development window ended. No held-back
material opened (checked by the script). Every quote of a `stated` or `partly` answer is found in the windows
its reader was given.
- **Stated in an earlier development window: 9 of 183 (4.9 %)**; partly 24 (13.1 %); not stated 150. The second
  reader, on 62: same cut in 61.
- Of the 9 stated, the captured entries hold 0 (partly 2, present prompt); of the 24 partly, 2 and 1.
- **A development reference asks for it in 4 of the 33** stated or partly (1 of 9, 3 of 24).
By the rule: under 10 %, stage 2 is not run and `memory_save` (F0 done, F) goes first.

What it says, and how far:
- The 110 questions do not stand for what later work looked for: of the 33 later needs these windows held in
  whole or in part, the questions written from the same windows ask for 4. A capture tuned to hold the
  references would be tuned to the question writers. This does not depend on how much history was read.
- The 4.9 % does: readers had only the development windows that ended before each episode (12 windows, a small
  part of each project's history), so it is the share stated in those windows, a floor for "stated in an
  earlier session". It is in effect the fifth adopter's figure. `not stated` rests on the readers' searches (9
  of 150 checked by hand, none wrong). 19 subagent episodes belong to a session that holds a held-back window;
  without them, 8 of 164.
- Together with part D of plan 15 (the deployed vault held what the agent then found in 3 of 320): most of
  what sessions go looking for is new each time (the state of a run, a version's source, a file's content),
  not something an earlier session knew.
- Not answered here: whether an agent's own save would hold what later work needs. That is F's measurement.

## Not in this plan
- `memory_save` and F0; moving the extractor to the end of the session; the map and the view.
- Ranking and the vault's growth over months (see the limits).

## Cost
Stage 1 cost 7.14 dollars. Stage 2: seven captures of 12 windows (A0 twice, A1, A0x2, A5, A2, A3), two of them
with a second call, about 110 dollars with Opus, plus judges about 15, plus the held-back run of three arms
about 45. Stage 3 about 60 dollars. Ceiling for the plan: 250 dollars, stage by stage, each stage's result reported before the next.

## Challenge
Codex, gpt-6.1-sol, effort medium, read-only, 2026-11-16: 25 objections, 9 blocking (report and prompt kept
outside the repository, `eval/plan-15/codex-plan16-review.md`). Each was checked against the code and the
replay's data.
- Taken as written: 2 (split by transcript, stage 1 on the development half only, the earlier aggregate reading
  said); 4 (loss point and causes, agreement measured on them); 7 (`existing entry` as a cause, arm A7;
  confirmed in `extract.extract`: two searches over the first 5 000 characters, 30 hits each, statements cut at
  300); 9 (the sentence that the loss is selection and completeness is withdrawn: stage 1 measures it per
  reference); 10 (pruning by recoverable distinct references); 11 (A0x2); 12 (A4's order and duplicates;
  confirmed in `extract.compare`); 13 (A6's condition); 14 and 15 (unwanted content per entry with wrong and
  repeated, absolute counts, characters); 16 and 17 (repeatability of the held reading first, baselines read
  again under the same rule, each judge's gain); 18 (gain spread over windows, A0 captured twice); 19 (gain per
  added dollar, losses by kind); 20 (A0 in stage 3's rule); 21 (unit, ties, test); 22 (each new wrong answer
  read); 3 (part G as a regression check).
- Taken in part: 5 and 8. The mechanisms exist, and the replay's own counts bound them: no repeat, duplicate,
  supersession or withdrawal, 2 and 3 rejected candidates, 4 unverified entries per arm. They are listed, not
  made a stage; raw proposals were not kept, and stage 2 keeps them. 6: confirmed there is no cap on
  candidates (`SCHEMA` has no `maxItems`, the loop does not cut); no "higher yield" arm beyond A3. 23: the
  claim is narrowed in the limits; deliverable entries are reported apart; ranking is not added.
- For the owner, not decided here: 1, 24 and 25 (an independent sample of later work, accumulation over months,
  a cheaper experiment first). See "For the owner".
