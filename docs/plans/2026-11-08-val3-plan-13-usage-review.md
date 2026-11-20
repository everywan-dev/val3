# Plan 13 — Usage and efficiency review of the adopters

> **For agentic workers:** this plan measures and decides; it changes no code in `src/`. Run it with
> superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.
>
> **Status (2026-11-14):** executed; Tasks 0-5 on 2026-11-08, Task 6 on 2026-11-14. Results in the kit's
> `results.md`, summary in `SESSION.md`.

**Goal:** turn the usage survey of 2026-11-08 into measured answers, each closing with a decision the owner
takes (or an explicit "inconclusive"), and apply the accepted decisions to the adopters' configuration.

**Architecture:** every measurement is a script in an evaluation kit outside the repository
(`~/.local/share/val3/eval/usage-2026-11/`), reading the vaults (`val3dev`, the fifth adopter's mirror on it) and
the state logs (`~/.local/state/val3/`). Results go to the kit's `results.md`, one section per task, with counts,
their uncertainty and the decision asked of the owner. Only Task 1 writes to a database, a scratch one.

**Tech Stack:** Python 3.11 standard library, `psql`, the installed `val3` CLI (code at `9ae874d`).

**Source:** the survey in `SESSION.md` (2026-11-08 (3)). Challenged by Codex gpt-6.1-sol `medium` on 2026-11-08:
16 objections, all taken (`<kit>/codex-review.md`).

## Baseline (survey of 2026-11-08, about 16:40 local; windows as named)

| Adopter | Entries: live (all rows) | New, 7 d | Prompts, 7 d (with injection) | Live never injected, 30-d log | Agent save / show / recall, all transcripts | Capture, 7 d |
|---|---|---|---|---|---|---|
| third (+ specialized) | 373 (424) + 72 (75) | 142 + 75 | 46 (23) | 330 of 373; 70 of 72 | 104 / 43 / 25 | 127 admitted, $0.70 |
| fifth (own host, Opus) | not measured (355) | 156 | 18 (9) | not measured | 25 / 16 / 14 | 156 admitted, $41.49 |
| fourth | 261 (277) | 49 | 10 (4) | 235 of 261 | 17 / 9 / 10 | 43 admitted, $0.21 |
| sixth | 45 (51) | 51 | 12 (3) | 42 of 45 | 15 / 3 / 0 | 41 admitted, $0.20 |
| second (delivery only) | 41 (44) | 0 | 25 (3) | 22 of 41 | 3 / 2 / 0 | no capture |
| first | 95 (102) | 0 | 0 (no session since 2026-10-16) | 65 of 95 | 20 / 15 / 1 | — |

Settlements (`keep` / `replace` + `drop`): fourth 154 / 14, third 87 / 27, sixth 30 / 2, specialized 20 / 0,
fifth 661 / 4. Session index at start: 1,700–2,400 tokens. Haiku extraction: $0.004–0.005 per admitted entry;
Opus on the fifth adopter: $0.27 (backfill $41.76 in 66 calls, about $0.63 a call; then $8–13 a day).
The Baseline is kept as recorded; Task 0 records the differences of its own snapshot, it does not have to match.

## Global Constraints

- Live vaults, adopter `val3.toml`, CLAUDE.md, CLAUDE.local.md and `.claude/settings*.json` files are read only
  until Task 6; Task 6 changes them only for decisions the owner accepted in the session, and records each.
- Model spend of the plan: at most $20, enforced before each run (Task 1 Step 3), not projected after.
  An extractor error logs `$0` for a call that may have cost (`extract.py:223`): count each error at the arm's
  measured cost per call.
- Every replay run is isolated: its own `XDG_STATE_HOME` (watermarks, queue lock, logs; `config.py:140`), its own
  database and vault, `--service` given explicitly, a project directory under the kit with no memory directory
  (`inbox.memory_dir`; no global `autoMemoryDirectory` is set, checked 2026-11-08). Never run `val3 extract` from an
  adopter's directory. Before the first model call of a run, print the effective state dir, `current_database()`
  and `current_user` and stop if any is not the run's own.
- Data destinations, listed in `results.md` and cleaned in Task 6: the transcript copy (kit, mode 600), the run's
  state dir, database `val3_eval` (outside `val3`, so not in `val3 backup` dumps), the Anthropic API (the same path
  the fifth adopter's capture already uses for that transcript), the local embedding service. No other.
- Transcripts, entry texts and adopter names stay in those destinations; the plan, `SESSION.md` and `TODO.md`
  name adopters by ordinal.
- Scripts run with `python3 -I`, print the table they add to `results.md`, and end with one assertion-based
  self-check of their arithmetic.
- Proportions from samples are reported as `k/n` with a 95 % interval (Wilson). A decision rests on a sample only
  when its interval excludes the other option; otherwise the answer is "inconclusive", and the owner may still
  take a provisional decision with a review date, recorded as such.

## Review Focus

1. Task 1 must attribute differences to the model: same material, same empty starting vault, same code for both
   arms. Differences that come from accumulated state are out of scope.
2. Vector similarity proposes matches, it does not prove coverage: accepted matches are audited too, and coverage
   is judged by claims (full, partial, missing, contradicted), many-to-many.
3. Settlements are past decisions, not ground truth, and only describe pairs that were recorded; a threshold
   change also changes repeats, open pairs and future pairs. Task 2 reports it as a retrospective description.
4. An entry reaches the agent at different depths: title (session index), excerpt (`recall`, 200 characters),
   full (prompt injection, `show`). "Never read" is replaced by "no observed exposure" at each depth, within the
   30-day prompt log (`hooks.LOG_DAYS`).
5. A short prompt is up to 6 words (`search.SHORT_PROMPT`), read with the previous turn; labels follow the need
   criterion of `CONTEXT.md`, and a rule is chosen on some sessions and checked on others.

---

### Task 0: Evaluation kit and snapshot

**Files:** `~/.local/share/val3/eval/usage-2026-11/{README.md,results.md,survey.py,codex-review.md}`

- [x] **Step 1:** create the kit (mode 700); move the survey script and the Codex review there. `README.md`
  maps ordinals to adopters, vaults, transcript directories and hosts.
- [x] **Step 2:** `survey.py` gains: live and all-row counts per vault, the window of each column, the cut-off time
  in UTC, the last mirror refresh, the code commit and each adopter's `val3.toml`, the transcripts and log days
  available per host. Run it into `snapshot.txt`; record differences from the Baseline in `results.md`.
- [x] **Step 3:** the fifth adopter's live count and 30-day exposure, over ssh, read only (same queries).

### Task 1: Extractor model on the fifth adopter (cost)

Question: on identical material, how much of what Opus extracts does Haiku extract, at what cost?

- [x] **Step 1:** copy the backfill transcript (`ae075904…jsonl`, 42 MB) into `<kit>/replay/` (mode 600). Cut one
  contiguous window of whole lines, starting at a typed user prompt and past the session's set-up turns, of about
  8 MB: `window.jsonl`.
- [x] **Step 2:** `CREATE DATABASE val3_eval` on the `val3dev` server and a `pg_service.conf` entry `val3eval`.
  One arm per model, each with its own vault (`val3 vault create eval_<model> --service val3eval`), its own
  directory `<kit>/replay/<model>/` holding a `val3.toml` (`service = "val3eval"`, that vault as home,
  `[extractor] model`, the fifth adopter's `[embedding]`), and its own `XDG_STATE_HOME=<kit>/replay/<model>/state`.
- [x] **Step 3:** budget gate. Run the Haiku arm first: `val3 extract --session eval-<model> window.jsonl` from
  its directory. Its first-call count is the window's slice count (same chunking for every model). Projected Opus
  cost = slices × $0.63 × 1.3 (second calls); run the Opus arm only if Haiku's spend plus that projection is at
  most $14, otherwise cut the window and start again with fresh arms. Record each arm's summary line and errors.
- [x] **Step 4:** `match.py`: check that both arms' vectors share model and dimension; for each entry of either arm,
  the three most similar entries of the other arm of the same kind. Print the proposals; nothing is counted yet.
- [x] **Step 5:** reference by claims. From the window, a sample of 30 slices: for each, list the claims a later
  session would need (the `CONTEXT.md` meaning of a fact, decision, preference, recipe or tool), without looking at
  either arm. Then, for every listed claim, mark each arm `full`, `partial`, `missing` or `contradicted`, using the
  Step 4 proposals as a finding aid, and mark each entry of those slices `useful`, `trivial` or `wrong`.
- [x] **Step 6:** decision. Report per arm: claims covered (full and partial), contradicted, entries trivial or
  wrong, cost. Default: **switch the fifth adopter to Haiku** when its full-coverage interval overlaps Opus's and it
  contradicts no more claims; **keep Opus** when Haiku misses claims Opus catches beyond the intervals;
  otherwise "inconclusive", with the option of a provisional switch reviewed after 14 days of captures. A Sonnet
  arm is added only if the result is inconclusive and the remaining budget covers its projection (Step 3 rule).

### Task 2: Neighbour threshold (settling load)

Question: what did the neighbour check produce per vault, and what would a higher threshold have changed?

- [x] **Step 1:** `sweep.py`, per vault with settlements (fourth, third, specialized, sixth, fifth via its mirror):
  join `settlement` to `neighbour`; settlements with no `neighbour` row (a neighbour the extractor named in
  `supersedes` below the threshold is shown to it, `neighbour.py:63`, but never recorded) are counted apart and
  stay out of the sweep. For thresholds 0.70 to 0.90
  in steps of 0.025, print `keep` pairs avoided, `replace`/`drop` pairs lost, open pairs, and `repeat` events whose
  pair score is below the threshold (a higher threshold would have admitted them).
- [x] **Step 2:** review every `replace` and `drop` pair (about 50) against both entries: was the decision right?
  Review a sample of `keep` pairs, 10 per vault in score bands 0.70–0.80 and ≥ 0.80; classify each as `snapshot`
  (successive measurements of one thing), `different object`, `older holds a detail` or `should have been
  replace`.
- [x] **Step 3:** decision per vault, with counts and intervals. Default: **keep the threshold** where right
  replacements are under 10 (fewer leave the loss unmeasurable); elsewhere, the highest threshold whose lost
  right replacements are at most 1 in 5 and which avoids at least half the `keep` pairs. The fifth adopter (661
  `keep`, 4 `replace`) is put to the owner as a cost question: settling work against unmeasured loss. If `snapshot`
  dominates the sample, add a TODO item for a design note on telling successive measurements apart.

### Task 3: Exposure, origin and session cost (volume)

Question: which entries reach the agent, at what depth, and does what the extractor stores get used?

- [x] **Step 1:** `reads.py`, per vault and within the 30-day log: refs exposed by injection (prompt log; logged
  hits may exceed what was rendered, so an upper bound), by `show` and by `recall` / `index` output (assistant
  tool calls and their results in the transcripts), and by title in today's session index (sections collapsed
  today expose no titles). Report per depth: title, excerpt, full.
- [x] **Step 2:** origin per entry: `agent` when a `val3 save` call's result in its origin session's transcript
  reads `admitted <ref>` for that ref; `capture` when an `extract.log` run of its origin session admitted at that
  time; otherwise `unknown`.
- [x] **Step 3:** cohorts: entries with at least 7 days and 10 logged prompts of their project after creation.
  Report full-depth exposure by origin, kind and statement length (≤ 400, > 400 characters) on those cohorts only.
- [x] **Step 4:** usefulness of what is not exposed: 20 entries with no observed exposure at any depth, labelled
  `would help a later session`, `trivial` or `wrong`. Cost per active session: index tokens plus injected tokens.
- [x] **Step 5:** decision. Default: **no change** to capture. A TODO item to narrow the extractor prompt only if
  `trivial` dominates Step 4 beyond its interval, with those entries as examples. A TODO item on statement length
  only if long entries of one vault have lower exposure than short ones of the same vault. The index is kept as
  is unless its tokens exceed a share of the session the owner sets.

### Task 4: Injections on short prompts

Question: on prompts of at most 6 words, what do injections bring, against what they cost?

- [x] **Step 1:** `short.py`: from every prompt log, prompts of at most 6 words, classified `approval`,
  `continuation` or `topic change` from the prompt and the previous turn; per class, injections, entries and
  characters.
- [x] **Step 2:** split sessions in two halves (discovery, validation). Label 30 injections of the discovery half,
  stratified by class: `needed` (the entry bears on the next step, `CONTEXT.md`), `harmless` or `noise`.
- [x] **Step 3:** on discovery, compare three policies: current, prompt alone (no previous turn), no injection;
  count needed entries kept and tokens saved. Write the best rule, then label 20 validation injections and
  report it there.
- [x] **Step 4:** decision. Default: **no change** unless the validated rule removes noise beyond the interval and
  loses no `needed` entry on validation; then a TODO item with the rule as a candidate fix. The noise share
  that justifies a change is the owner's preference, asked explicitly.

### Task 5: Pending work and adopters with little use

- [x] **Step 1:** list in `results.md` what waits on the owner, with ages: review items (first 5, second 1,
  third 3, fourth 1, fifth 1) and entries to settle (fifth 4, sixth 4), refreshed from Task 0.
- [x] **Step 2:** first and second adopters, from Tasks 0 and 3: sessions per week since adoption, exposure
  per depth, cost (index tokens per session), and the owner's plans for each project. Options for each: keep as
  is, keep until its next active session and review then, delivery only, or remove VAL3 (hooks, CLAUDE.md or
  CLAUDE.local.md block, `val3.toml`; the vault stays). No default; the owner decides on these numbers.
- [x] **Step 3:** in the fifth adopter's status diagnosis, note with date and scope that `to re-check: not
  checked` affects no entry today (no `file` evidence: command 523, query 187, user 8 on 2026-11-08); it says
  nothing about entries backed by commands or queries.

### Task 6: Decisions, rollout and close

- [x] **Step 1:** present the owner the decisions of Tasks 1–5 in one list, separating changes to apply from
  items that only become TODO entries (with a review date); record each answer in `results.md`.
- [x] **Step 2:** apply the accepted changes and verify each by its effect, not by `val3 status` alone:
  - extractor model: `load_project` in that adopter prints the new model; after its next capture, the cost per
    call in its `extract.log` is in the new model's range;
  - neighbour threshold: the value in `val3.toml`; it applies to pairs recorded from now on (stored pairs stay);
    after the next captures, new `neighbour` rows score at or above it;
  - delivery only or removal: hooks in every settings file, the CLAUDE.md or CLAUDE.local.md block, and a new
    session's start notice.
- [ ] **Step 3:** drop `val3_eval` and its service entry; delete `<kit>/replay/` (transcript copy, state dirs).
  Not done on 2026-11-14: the harness refused the command; left for the owner (`TODO.md`).
- [x] **Step 4:** `SESSION.md` entry (what was measured, decisions, spend, which Codex model challenged the plan),
  `TODO.md` updated (new items; items 2 and 4 of "Next" closed or rewritten with the numbers), commit `docs:`.
