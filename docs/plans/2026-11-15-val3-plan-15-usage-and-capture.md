# Plan 15 — Use, capture and a view: programme

> A programme of parts, not one implementation plan. Parts A and B are specified to the level of tasks. Each
> later part gets its own design and task plan when it starts; here it names what it must measure and what stops
> it. Status: draft for the owner, revised after the Codex challenge (last section).

**Status on 2026-11-15, end of day (TODO.md has the detail).** A: merged and deployed, not yet seen on a real
capture. B: built on its branch as ADR 0012, which replaces this plan's part B text (stored attestation, upgrade
as the per-vault switch); not merged. C: pilot run, six defects to fix. D: measured; its rule was not met, so E
is not built. F: only F0 looked at. G: measured on two windows; `[extractor] focus` exists, off by default.
H: built as a live local server (`docs/design/2026-11-15-vault-view-design.md`), not as the generated file
described below; merged and deployed.

**Why now.** Measurements up to 2026-11-15 (TODO.md, SESSION.md) and the sourced comparison
`docs/research/2026-11-15-published-memory-systems.md`: the store answers when asked (51 of 86 real queries
against 25 for the project's documents); agents consult it little and mostly through the CLI (one adopter, one
day, 664 tool calls: `memory_search` 1, `show` 6, `recall` 4, `index` 3), and whether they miss what it holds is
not known; capture is the costly path; pair settling and object identity did not pay; capture re-read a resumed
session; the review list waits on a person since October; nobody can look at a vault except through terminal
listings; and no figure so far says whether a session with VAL3 does its work better.

**Owner's decisions taken into this plan (2026-11-15).** Fix capture; measure before changing delivery, by
post-processing transcripts; give weight to the agent's own saving; re-examine the extractor's prompt and model;
no new identity or settling mechanism; remove the review by a person (an automatic system must not wait on one);
a dirty transcript is still a source, imperfect memory is better than none; add a visual way to look at the
memory; premises are established by test, not by recollection.

## Global constraints
- Repository rules as in CLAUDE.md: English, no internal names, standard library plus one PostgreSQL driver,
  black-box tests through the CLI, exit codes 0/1/2, `feature/*` branches, Conventional Commits.
- No schema change without `val3 vault upgrade` covering it; mirrors and linked vaults keep one schema.
- Every measurement: acceptance rule, minimum useful effect and budget written in the kit README before a
  result is read; real code for every arm; data and entry text outside the repository; every figure says how
  many judges read each item. A change chosen on one set is confirmed once on items held back from it.
- A part that fails its own rule is closed as "not taken", with the figures, and is not tuned on the same set.
- Frozen: new mechanisms of identity, settling or consolidation. Not frozen: fixes of known defects.

## Order
1. A and B (independent of each other), and the proof of F's session link (F0).
2. D (diagnosis on real transcripts, including harm) and the design and pilot of C, in parallel.
3. F and G as small trials on adopters, judged by D's method; C's full runs for whatever passes.
4. E only if D's rule says so and its security tests pass.
H beside any of them, after its own security tests.

---

## Part A — Capture does not re-read a resumed session

**Problem.** `extract._watermark` keeps a line count per session id (`extract.py`). A resumed session gets a new
id and a transcript that starts with the earlier session's records (1 201 shared message ids on 2026-11-15);
capture read them again and admitted 9 repeats of 52 entries. A second defect found by the challenge: the count
restarts only when the transcript got shorter, so a transcript rewritten to the same or a greater length can
hide new records before the count.

**Change, the smallest that covers both.**
- *Identity of a record.* Its `uuid` field where present, else a hash of the raw line. Task 1 samples real
  transcripts (resume, compaction, subagent, peer messages) and writes down which records carry which.
- *Shared prefix.* A session without a cursor starts after the longest leading run of records already
  processed under another session. No filtering inside the transcript: a result is never left without its
  call. The last records of the skipped run are passed as context marked as such, not as material to extract.
- *Cursor check.* The cursor stores the line count and the identity of the record at that line. If the record
  there differs, the transcript was rewritten: capture starts from its beginning and relies on the prefix rule.
- *Processed set.* One append-only file per project root (the directory holding `val3.toml`; worktrees of one
  project share it through the state directory key already used for the queue), a line per record: identity
  and date, appended when the chunk holding it has been written to the vault. Guarantee stated as such: each
  record is processed at least once; a crash between the vault write and the append re-reads one chunk, whose
  entries are then exact duplicates and are refused by `content_hash`. Under the existing queue lock
  (`pending._lock`). Local state only: two hosts capturing the same transcript are not covered.
- *Retention.* Lines older than `[capture] remember_days` (default 90) are dropped by an atomic rewrite under
  the same lock. A session resumed after that, or a lost file, is processed again and can produce repeats; said
  in the README, not presented as free.
- *Unchanged.* Evidence is checked against the whole transcript, not the chunks. Changed notes with no new
  record still go with the end of the transcript.

**Not covered, kept as an open item.** Evidence that exists only in a subagent's own transcript (subagent
answers are not evidence, and the hook queues only the path it receives).

**Tasks.** (1) Sample and document record identity. (2) Failing tests: a second transcript repeating the
first's records under another id plus new ones sends only the new ones (asserted on the stub extractor's input
and on rows); a transcript rewritten to the same length with different records is read again; a crash after
the vault write and before the append admits nothing twice; a note changed with no new record is still sent; an
agent `save` quoting a record of the skipped prefix is admitted. (3) Implementation. (4) Suite, merge, deploy.

**Check on the adopters.** After deployment, for every capture of a resumed session: no record already in the
processed set is sent again (counted from the capture log, which gains `skipped`). Reported besides, not as the
criterion: a read of the entries admitted that week for real repeats.

**The adopter that has the 9 repeats.** They are withdrawn as duplicates by its agent. `neighbour` stays
`false` there (owner left the choice open, 2026-11-15): set to 0.90 it brought back the 66 entries marked to
settle from pairs recorded at 0.75, which is what was turned off; a high value as a net would first need
recorded pairs below the current threshold not to be shown. This part goes first instead.

## Part B — No decision waits for a person

**Problem.** Twelve items wait on five vaults, the oldest since 2026-10-14. `review_item.reason` has nine values;
some are candidates, some proposals, some notices of something already done (`NOTICES` in `writer.py`, which
can only be acknowledged).

**Step 1, before any code: the decision table, complete.** For every operation and actor that can create a
review item today (candidate without evidence; import with an unverified quote; withdrawal of one entry without
evidence; withdrawal of a batch, which takes no evidence; each notice) the table says what happens instead and
who may do it. Draft:

| Path today | After |
|---|---|
| candidate without evidence (`no-evidence`) | not admitted; kept as a rejected candidate with its full payload so it can be listed and saved again; `val3 save` says so and exits 1 |
| import candidate with an unverified quote | skipped, counted, listed by `import --apply --show-skipped` |
| withdrawal of one entry without evidence | refused, exit 1 |
| withdrawal of a batch | a person only: refused from inside an agent, as `tool add` today, and applied at once when a person runs it (owner, 2026-11-15) |
| notices (`supersession`, `withdrawn`, `recipe-cites-superseded`) | written to the event log only; nothing to acknowledge |
| historical reasons no path creates any more (`recipe`, `tool`, `user-confirmation`) | only the migration below |

**Step 2: candidates without evidence are admitted as `unverified` (owner, 2026-11-15).** Not rejected, in
line with "imperfect memory is better than none". Its own section in ADR 0010, with these rules to design and
test: the mark is stored (schema change through `vault upgrade`, carried by mirrors); every reader shows it
(`show`, `recall`, `index`, both memory tools, the view of Part H); an unverified entry never enters the loaded
layer of Part E, is never evidence or a cited fact of a recipe, and never supersedes or withdraws a verified
one; saving it again with evidence that checks replaces the mark. Before the design, a reading of all pending
items by reason and of what a person approved since October, as a description of what the rule will admit.

**Step 3: migration, in `val3 vault upgrade`.** Under the writer's lock, idempotent, `--dry-run` printing every
item. Notices are acknowledged (their operation is already applied and is not undone). Candidates and proposals
are closed with a decision that says `retired`, distinct from a rejection of the knowledge: a later candidate
with the same content and new evidence must be admitted, so the uniqueness on `content_hash` and the import
hash (which leaves the evidence out) are handled explicitly and tested. Source vaults first, then mirrors
re-synced.

**Step 4: removal.** `val3 review` and its screens, the pending-items line of `val3 status` and of the
session-start notice. `tool add` and `import --apply` stay: commands a person chooses to run, nothing waits on
them. ADR 0010 supersedes the review parts of ADR 0008. Tables stay as history.

**Tests.** One per row of the table, per actor; a test that no command run from inside an agent leaves a
pending item; migration on a vault holding every historical reason, run twice.

## Part C — A bench that measures the work

**Problem.** Every figure so far is about retrieval on questions.

**What.** Synthetic projects with scripted sessions run headless, where later tasks need what earlier sessions
learned and the files do not say: a rule found by failing; a decision taken with the user; a state that changes
between sessions (the old value must not be used); a fact held in a linked vault; a session resumed under a new
id, a compaction, a subagent that learned the fact. Controls: tasks that need no memory and facts the files do
state. Each task checks the outcome and the procedure (the stale value was not used, no forbidden step), and
counts tool calls, tokens, time.

**Unit and isolation.** The unit is a whole sequence run from a clean state (fresh directory, fresh vault, the
harness's own memory emptied or fixed per arm, same model, settings, permissions and token budget). Sequences
are paired across arms. Variants of each scenario are held back and never used to design a change.

**Validity of the bench itself, independent of VAL3's result.** Two reference arms: an *oracle* memory (the
needed facts placed in the vault by hand) and a *wrong* memory (plausible false entries). The bench is kept if
the oracle beats no memory on the memory tasks by the minimum useful effect and the wrong memory does worse or
is refused, with the controls unchanged. Whether VAL3 as deployed beats no memory is then a result, not a
condition.

**Arms.** No memory; the harness's own memory; VAL3 as deployed; later, one candidate per part.

**Budget.** A pilot first (3 scenarios, 2 arms, 2 sequences) to measure cost and noise; the full size, the
estimator and interval, the minimum useful effect and the total cost are fixed from the pilot and approved by
the owner before the full run. A candidate chosen among several is confirmed once on the held-back variants.

**Not claimed.** A synthetic project is not the adopters' work; D is the check on real work.

## Part D — What the sessions needed, missed, or were misled by

**Method (post-processing of transcripts).**
- *Episodes.* A reproducible rule cuts a session where the agent starts looking for something (a run of
  searches or reads before it acts, a question to the user, a failed command followed by a search). The rule is
  code in the kit; a sample of its cuts is audited by a person-readable listing and a judge before use.
- *Query without hindsight.* A model writes the query from the transcript **up to the start** of the episode
  only. The outcome (what the agent then found or was told) is hidden from it and given to the judge. A second
  query written with the whole episode is reported apart as a ceiling.
- *The vault as it was.* An evaluation adapter runs the deployed search on the vault's state at that moment:
  entries created before it, not yet withdrawn or superseded then, and for mirrors only what the last sync
  before it had brought. Episodes whose state cannot be rebuilt are counted and left out.
- *Judging.* Blind: did a returned entry hold what the agent then found; how many of the episode's calls were
  avoidable given that (not all of them: checks stay), net of the lookup.
- *Harm.* Every consultation the agents did make (CLI and tools) is judged too: was a returned entry out of
  date or wrong, and did the agent act on it.

**Reported.** Episodes, missed ones, avoidable calls net, consultations made, harmful ones; with the figures at
other thresholds than the rule's.

**Rule, written before the run.** Part E is designed only if avoidable calls in missed episodes are at least
5 % of the sessions' tool calls on the no-hindsight query, and the harm audit finds no entry acted upon that
was wrong. The 5 % was agreed by the owner on 2026-11-15. One judge per episode, a second on a tenth.

**Result (2026-11-15).** 561 episodes in 96 transcripts of four adopters. The vault held what the agent then
found in 3 of 320 knowledge episodes (4 of 317 with a query written with hindsight); avoidable calls 0.1 % of
tool calls. Of the 365 consultations the agents did make, 249 were useful at least in part and none led to
acting on a wrong entry. The rule is not met: part E is not designed. What sessions look for by themselves was
never captured; parts F and G come next.

## Part E — A small layer loaded at the start (not built: D's rule was not met)

**What.** A per-project page, within the harness's limit for hook context (10 000 characters today) and far
below it, kept by a scheduled pass and loaded at session start and after a compaction: one line per entry that
sessions needed, with its ref. Per-prompt injection stays off. The page is rebuilt, not appended: a withdrawn
or superseded entry leaves at the next build, and `val3` can empty the page at once.

**Security, before any deployment.** An evidence check proves that a quote exists in a transcript, not that
the statement follows from it nor that it is harmless: text from a tool result can pass it. Tests with planted
hostile entries (instructions, false states) on the bench: the layer must not make a session follow them.
Entries reach the page only if their evidence is the user's words or a command's output of the project's own
session, never a mirror's or an import's; provenance is printed. The envelope line is kept and is not counted
as the barrier.

**Measure.** Bench arm and, on adopters, sessions alternated with and without the page over the same period:
task results, errors, total cost. Not "tool calls do not fall".

**Stop.** No gain on the bench, any followed hostile entry, or a wrong line acted upon on an adopter.

## Part F — The agent saves on purpose

**F0, first.** Prove the link from a tool call to the caller's session and transcript: the MCP server gets the
project directory, and the environment the hook exports reaches the shell, not necessarily the server. With
concurrent sessions and subagents. A path supplied by the model is not accepted. If no reliable link exists,
`memory_save` is not built and the agent keeps `val3 save` through the shell.

**What.** `memory_save` over the present writer and its evidence rule, with one line of guidance and no list
of what to save.

**Measure.** Three arms on the bench and, as a two-week trial, on adopters: saves only, extractor only, both.
Unique facts later sessions needed (D's method), and full cost. Windows and minimum sample fixed beforehand.

**Stop, with its action.** The tool is turned off on an adopter when more than a third of its saves fail the
evidence check over a window of 30 calls, or when after two weeks its saves hold none of the needed facts that
the extractor missed.

## Part G — What the extractor is for, and with which model

**Arms, on plan 13's replay kit (half of its reference claims held back) and on the bench, Haiku and Opus:**
1. the present prompt;
2. the present prompt plus guidance on usefulness, without exclusions: "prefer what a later session could not
   find again by reading the project";
3. the present prompt plus "leave out what the transcript itself shows being read from a project file" (the
   extractor has no tools and cannot check the files; it can see what the session read);
4. a prompt asking only for decisions, procedures, pitfalls and accepted exceptions.
Arm 4 is VAL3's own hypothesis. The published result against fixed rules is about an agent's own writing
during a task and does not settle it either way.

**Measure.** Needed facts captured (reference claims, in absolute number, not only as a share), entries per
session, share of entries a blind judge finds stated in the files the session read, dollars per needed fact.

**Rule.** An arm must capture at least as many needed facts as the present prompt less 5 of them per 100;
among those, the cheapest per needed fact; an arm that is cheap because it captures little fails the first
condition. The chosen arm is confirmed once on the held-back claims. The extractor is moved to session end
only after F's three arms show what is lost.

## Part H — Seeing a vault

**First version.** `val3 view [--vault V …] --out FILE`: one self-contained HTML file, read-only, written with
mode 0600, for the vaults named explicitly:
- a table of entries with filters (kind, index, origin session, date, live or not, has evidence);
- a detail panel: statement, what cites it, what it superseded or was superseded by; evidence only with
  `--evidence`;
- a time line of admissions, withdrawals and supersessions per day, by origin.
The three questions it must answer without SQL: which entries a session produced, what superseded what last
week, which entries have no evidence.

**Second version, only for relations that prove useful.** A graph of supersession and of a recipe's cited facts
and tools. Shared-index and neighbour edges are left out unless a use is shown (they grow with the square of an
index's size); the number of edges is bounded and printed.

**Security.** `text.safe()` escapes terminal control characters, not markup. Entry text enters the page only as
data: JSON embedded with `<`, `>`, `&` and line separators escaped so it cannot close its element, inserted
with `textContent`, never as HTML; a content security policy that allows no network and no inline handlers.
Tests with hostile titles, quotes, paths and names, including attempts to load a remote resource. The file is a
copy outside the vault's permissions: the command says so when it writes it.

**Check.** The owner answers the three questions on the largest vault; a vault of 5 000 synthetic entries opens
in under 5 seconds.

## What this plan drops from TODO
The review-dependent items (waiting on `val3 review --tty`, plan 10 rollout's last review, review screen minors
of plans 5 to 8); the "changed since" and object-key lines of the roadmap; ranking tuning beyond what is
deployed.

## Risks
- A: the processed set is lost or outlived: repeats, said as such; the cursor check costs one comparison.
- B: knowledge without evidence is no longer admitted: read before the change, recoverable after it.
- C: cost and false positives from many arms: pilot, budget approved, one confirmation on held-back variants.
- D: the episode rule sets the denominator: audited, thresholds shown with their sensitivity.
- E: a wrong or hostile line in every session: restricted provenance, tests before deployment, instant removal.
- F: no reliable session link: then not built.
- H: a durable copy of vault text: explicit vaults, 0600, evidence opt-in.

## Challenge
Codex gpt-6.1-sol, effort `medium`, read-only, 2026-11-15: 31 objections, 12 blocking, verdict "in favour with
changes" (`~/.local/share/val3/eval/plan-15/codex-review.md`). Its claims about the code were checked against
the source before use (the nine review reasons and the notices, `withdraw --batch` without evidence, one
decision per review item, `text.safe` not escaping markup, the 10 000-character hook limit, the extractor
running without tools, the cursor restarting only on a shorter transcript).
- Taken, 29: A is now a shared-prefix rule with a checked cursor and an at-least-once guarantee instead of
  filtering by id (2 to 8, 10); B starts from a complete decision table, a description of what is lost and a
  migration that retires rather than rejects (11 to 14); C is validated by oracle and wrong-memory arms, with
  clean sequences as the unit, a pilot and a budget (15 to 18); D writes its query without hindsight, rebuilds
  the vault's state, audits the episode rule and adds a harm audit (19 to 21, 28); E gets security tests,
  restricted provenance and an alternated comparison (22, 23); F starts by proving the session link and has
  three arms and stop actions (24, 25); G's file arm is restated as what the transcript shows, a guidance arm
  is added and the rule cannot pick an arm that captures little (26, 27); the order is changed (28); H escapes
  by context, starts with table and detail, 0600 and explicit vaults (29, 30); the comparison's §4 is rewritten
  as bounded observations with URLs, and the usage figure counts every way of consulting (1, 31).
- Taken in part, 2: evidence held only in a subagent's transcript (9) is named as an open item, not solved
  here; deduplication across hosts (6) is declared out of the guarantee rather than built.
- Not taken: none.
