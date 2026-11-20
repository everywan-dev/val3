# `memory_save`: a save carried out after the call has returned

**Parked, 2026-11-17 (owner's decision): not built now.** Reason: plan 18's Task 0 shows that with the tool
pre-allowed the first save of every message would be deferred in automatic mode too, and every supervised
session runs in automatic mode, where a save already needs no prompt and is answered with its ref. To reopen:
adopters that work in manual mode. The last challenge asked before Task 1 was not run, and `--allow-save` is
not used on any adopter.

Status: second draft 2026-11-17, not approved. The first draft (admit unverified at once, verify later; commit
da1a8b4) was challenged by Codex (gpt-6.1-sol, medium, read-only: 17 objections, 7 blocking, §9) and its
approach dropped. Owner's decisions (2026-11-17, §8): 1 the entry exists only after the check; 2 a deferred save
that fails stores nothing. Challenged twice (§9; reports kept outside the repository, `eval/plan-18/`). The revision after the second
challenge (one process per job by a lock on its file, the 120 s wait, the notice belonging to the server, opportunistic recovery, the
cut for the tool only) was made after the owner's decisions and awaits his review. Then the amendment of ADR 0013 and the plan.

## 1. Why

`memory_save` checks a candidate's quotes against the transcript that holds the call, found by the call id the
client sends (ADR 0013). That needs the call's record on disk while the call is pending. Measured on Claude
Code 2.1.325:

| Configuration | Record on disk | Save with evidence |
|---|---|---|
| headless, tool allowed on the command line | 0.2 to 1.7 s after the call arrives; more than 8 s for the first of three saves in one message | admitted (within the 30 s wait) |
| interactive, not pre-allowed (automatic mode decides) | 0.1 s | admitted |
| interactive, tool in `permissions.allow` | only after the call returns | always fails after 30 s |

The third row is what `val3 init --allow-save` creates. The permission was removed from every adopter.

**Measured again for this design** (plan 18, Task 0; 2026-11-17, Claude Code 2.1.326, interactive, one run
per cell, the answer held 32 s; kit and raw data outside the repository, `eval/plan-18/t0/`). Seconds from the
call's arrival to its complete record on disk:

| Cell | Single call | Three calls in one message: first | second and third |
|---|---|---|---|
| pre-allowed, automatic mode | after the answer (0.15 to 0.17 s after it) | after its answer | on disk at their arrival |
| pre-allowed, manual mode | after the answer | after its answer | on disk at their arrival |
| not pre-allowed, automatic mode | 0.16 s | 0.16 s | on disk at their arrival |
| not pre-allowed, manual mode, approved by hand | 0.16 s | after its answer (one observation) | on disk at their arrival |

No record appeared between 2 and 30 s while its call was pending, and none failed to appear in 120 s. The
record's input equalled the arguments the server received in 15 of 15 calls.

**Who gains, and who would lose.** A session in automatic mode without the permission already saves without a
prompt and gets the ref in the answer; this design does not change it. With the permission, in automatic mode
as in manual mode, the first save of every message would be deferred: 2 s more and no ref in its answer. So
on adopters that run in automatic mode the permission would be a loss, with this design too. The gain is for
sessions outside automatic mode: no prompt per save with the permission, and the first save of a batch, which
today may fail there after 30 s. **Whether to build is the owner's decision; the plan stops before Task 1.**

## 2. Goals

- A save with evidence is carried out, its evidence checked, in all three configurations.
- Where the record is on disk during the call, the tool behaves as it does today.
- What is admitted and what counts as evidence do not change: one admission rule, whenever it runs.
- ADR 0013's rule stays: no candidate with evidence is stored without it.

Not goals: stronger evidence (a transcript is a local file the agent's shell can write); clients that send no
call id; a change to `val3 save`.

Anti-goal: an entry backed by evidence nobody checked, or stored in a weaker state than the caller asked for
without the caller being told.

## 3. What the code gives (read 2026-11-17)

- `writer.submit` validates, checks the quotes and admits in one transaction under the writer lock; for a
  candidate equal by hash to a live unverified fact, preference or decision it gives that entry the evidence.
- The content hash covers kind, title and statement only, so it does not tell two recipes or two tools apart.
- Admitting a candidate without its evidence drops only its supersessions of backed entries; those of
  unverified entries are carried out at once.
- The server is a parent process that answers the client and a child per call; the parent writes the answer
  after the child has ended.
- The capture queue is drained by one process under one lock, and a capture can hold it for minutes.
- `transcript.evidence_text` reads a whole transcript, with no cut; `call_files` returns the file, not the
  line, and does not notice the same id twice in one file.
- **The call's record holds the candidate.** On the real transcripts of this machine: 19 `memory_save`
  tool_use records with a `candidate` object, 18 with its `evidence`.

## 4. Approaches

**D. Short wait, then the same save later (chosen).** The call waits 2 s for its record. Found: today's path.
Not found: the candidate is validated, the request is noted in a small file, and the answer says the save is
carried out after the call. A detached process, started by the parent once the answer is written, waits for
the record, reads the candidate from it, and runs the ordinary `submit`. Nothing exists in the vault before the
check, so there is one admission rule and no half state. Cost: the answer carries no ref, and the entry is not
in the vault until the process has run (seconds).

**A. Short wait, then admit unverified and verify later (first draft, dropped).** The entry and its ref exist
at once. The challenge showed what admitting before checking costs: verifying by entry id cannot rest on the
hash (objection 1); admission and the queued job are two stores that can disagree after a crash (2); evidence
taken off before validation admits what is invalid today (3); an equal unverified entry no longer gets the
evidence (4); supersessions of unverified entries happen before a quote fails (5); the race with capture has
no single outcome (12); a second state machine for resolutions (13). Each has a fix; together they are a
second writer.

**B. Always admit unverified first.** One path, with all of A's problems in every configuration, and it
changes the tool under the trial of plan 17's stage 2.

**C. Capture carries it out.** No process of its own; the save waits for `PreCompact` or `SessionEnd`, hours
in a long session, and a session never captured never saves.

"Admit at once", written in ADR 0013's last section, was the architect's one-line direction, not a decision of
the owner; D drops it. Decision 1 of §8.

## 5. Design (approach D)

**5.1 During the call.** `LOOKUP` goes from 30 s to 2 s; `SAVE_DEADLINE` stays 50 s. With the record found,
nothing changes but the cut of §5.3. Without it, for a candidate with evidence and a call id:
1. The child validates the candidate whole (`candidate.validate`) and checks the vault writable
   (`Project.writing`): an invalid candidate or a wrong vault is an error now, as today. Otherwise it answers
   `deferred`.
2. The parent writes the job file `<state>/deferred/<call id>.json`: the call id, the transcript directory,
   the server's `cwd`, the time, the vault the child validated as writable, and the SHA-256 of the call's
   arguments as canonical JSON. No candidate text and no quote: those are read from the transcript's record.
   The directory is private. The file is written whole under a private temporary name, locked, and then
   linked to its final name (a link never replaces a file: a job of that name already there is an error of the
   call). The parent holds that lock until the answer is flushed, or until it dies: nobody claims a job whose
   answer is not out, and nobody ever sees half a job file.
3. `serve` writes and flushes the answer: `not stored yet: the record of this call is not in the transcript,
   so the save is carried out once it is; a later memory call reports its outcome`. Only then it starts the
   detached process (own session, descriptors closed). The action travels from `_tool_call` to `serve`; it is
   never started before the answer is on its way.
A parent that dies between those steps leaves a job file: a left-over (§5.2). No call id: the `no-call-id`
error stays. A candidate without evidence: admitted unverified during the call, as today.

**5.2 The detached process** (`val3 mcp --deferred <call id>`), outside the capture queue and its lock:
1. **Claims the job** with an exclusive, non-blocking lock (`flock`) on `<id>.json`, held until the process
   ends: one process carries a job out, and a process that dies frees it. A second process finds the lock held
   and exits. With the lock, the file is checked to be still there, the id checked again (`CALL_ID`), the
   file a regular file of the private directory, its `cwd` and call id the ones the process was started with.
   The job file is written once and never replaced. A job older than an hour ends `no-transcript` here.
2. Looks for the record among files written since the call's time less 60 s: every 0.2 s for 10 s, then every
   2 s until 120 s from its start. Two files, or the id twice in one file: `ambiguous`. Not found:
   `no-transcript`. Both are final and nothing is stored.
3. Reads the `tool_use` input of that record. Its canonical JSON must have the job's hash, and the vault is
   the job's: a record whose arguments differ from what the call sent and the child validated ends `error`,
   nothing stored. Loads the project from `cwd` and checks the vault writable again. The record must hold
   exactly one `tool_use` block of that id: two blocks, in one line or two, are `ambiguous`.
4. Runs what `_save` runs today: `submit(candidate, transcript, session, private, via="tool", agent)` with the
   cut of §5.3, then the vectors and neighbour lines. Every outcome of the writer means what it means today:
   admitted, verified, duplicate, discarded (a quote failed: nothing stored), queued on a vault not upgraded,
   an error.
5. As soon as the submission has committed, before the vectors: publishes `<id>.result` (the title, the
   outcome's status and first line; written whole under a temporary name and linked, so it is complete and
   never replaced), removes `<id>.json`, and writes the usage line of the save (final status and ref). The job
   is over when the job file is gone: until then nobody consumes or prunes its result. Vectors and neighbours
   run after that under an alarm of 50 s that ends the process; what they leave undone is left as after any
   save.
The 120 s bound the search for the record, not the result: the submission comes after, and a process killed
before it published leaves the job to recovery. A notice exists when there is a result.

**The call and its effect, in one transaction.** Every event that changes the vault for a save by the tool
carries the call id: `admitted`, `verified`, and `queued` when the review item is created (a vault not
upgraded). Before it expires a job or carries it out, the process asks the job's vault for an event of that
call id; if there is one, that is the job's outcome, published with its ref, and nothing is run again. So a
job whose process died after its commit is never reported as "nothing stored", also after the hour. If the
database cannot be asked, the job stays. Outcomes that change nothing (`discarded`, `duplicate`,
`already-queued`, `previously-rejected`) leave no such event and are simply found again.
<!-- ceiling: the event table has no index on the call id; one sequential scan per deferred save. An index
when a vault's events make that scan slow. -->
**Left-overs** are jobs of a process or a parent that was killed: an `<id>.json` nobody holds a lock on. A
server starts a process for the ones of its own `cwd` when it starts and after each later save, and nobody
else does: recovery is opportunistic. A server lists only the jobs nobody holds a lock on, so it starts no process for a job that is running. A
process that finds `<id>.result` already there only removes the job. A job run again after its commit is
answered from its event, as above; a discard run again logs a second `discarded` event. No lookup of the call id in the events, so no index
and no change of schema: the events carry `call` in their detail as a trace only.

**5.3 Evidence before the call, for the tool only.** Only records before the line of the call's record count
(a zero-based line index, the cut exclusive). `call_files` returns that line; the records are cut there before
exclusions and call/result pairs are computed, with the reader's own numbering (every complete line counts,
an invalid one too). `val3 save` keeps the whole transcript. The rule is: evidence is in complete records
before the call's record. That is what a caller can honestly quote, since the model had not seen a result or
a user message that came after it wrote the call; whether the file's order of queued messages is always the
order the model saw them in is not shown. During the call it removes today's dependence on what
happened to be on disk.

**5.4 Telling the agent.** A notice belongs to the server that deferred the save, not to a session or an
agent: after `/clear`, or with subagents, the reader may not be the caller, so the line names the entry. The
parent keeps the ids it deferred and, when a later memory call has ended, reads the result files of those ids
(a bounded read) and puts one line each before the answer: `earlier save "<title>": admitted <ref>` or
`earlier save "<title>": discarded: evidence[1] not found …; send it again`. Each line is `safe` and one
line, the title quoted as data; notices and answer together fit `MAX_RESULT`, at most five notices per answer;
a result file is deleted only after the answer that holds it has been written, others wait for the next call.
A restarted server reports nothing of its predecessor; any server deletes result files older than a day. A
session that calls no memory tool again is not told; the vault's events and the usage log hold the outcome.

**5.5 Text.** One sentence in the tool's description only (a save may be carried out after the call, and a
later memory call reports it); the other three copies of the saving instruction do not change, so no
`val3 init` is needed for the text. `AGAIN` no longer mentions a late transcript.

**5.6 Records.** Events carry `via: "tool"` and `call`, as stage 2 of plan 17 reads `via`; session and subagent
are known at admission, as today. The usage line of the call has status `deferred` and no ref; the process
writes a second line with the final status and ref. A failure the database cannot record (no transcript, a
configuration error) is in the result file, and in the usage log when the project's configuration loads.

## 6. What changes for whom

| Case | Today | With this design |
|---|---|---|
| record on disk within 2 s (automatic mode, most headless calls) | checked and admitted during the call | the same, with the cut of §5.3 |
| record written 2 to 30 s after the call (first save of a batch in one message) | waits, then admitted with its ref | answered at 2 s without a ref, admitted seconds after the message ends |
| tool in `permissions.allow` | fails after 30 s | answered at 2 s, admitted seconds after the call returns |
| a quote that fails | refused, nothing stored | the same; when deferred, the agent learns it at its next memory call |

Limits: a deferred save has no ref in its answer, so the same message cannot name it in `supersedes`, and a
search right after may not find it. With the tool pre-allowed every save with evidence is deferred, so each
costs the 2 s wait.

## 7. Tests and acceptance

Subprocess tests: the record written after the answer, then the process: admitted with evidence, session and
agent in the event, `call` in its detail, the result file, the two usage lines; a recipe, a tool, a
supersession, an equal unverified fact (`verified`); a failed quote (`discarded`, nothing stored); the cut,
with the call and another tool in one record and in separate records, a user message before and after the
call, a queued message; `val3 save --transcript` still reading the whole file; the id twice in one file and in
two files; an invalid candidate and a read-only vault (error during the call, no job); no record ever
(`no-transcript` result); two processes started on one job (one runs); a process killed after the commit and
the left-over run again (one entry, `duplicate`); a left-over of another `cwd` (not touched), one carried out at a later save without a
restart; a job file that is a symbolic link, names another id, or already exists; a transcript not written
since the call's time less 60 s; a vault not upgraded (`queued`); the subagent in a deferred save's event; a relocated private memory directory; the parent
killed after the job file and after the answer (left-over, carried out at the next save); the notice at the
next memory call and not at the one after, six results at once, a result that arrives during a call, a title
with line breaks, an answer at `MAX_RESULT`; a capture holding its lock while a deferred save goes through; capture and a deferred save of the same
candidate in both orders, for a fact and for a recipe (state, origin and outcome of each); a record whose
arguments differ from the call's (evidence missing, another vault: `error`, nothing stored); two blocks of
the id in one line; an invalid line before the cut; a job claimed by another server before the parent's
flush (refused by the lock); a result read while its job file still exists (not consumed); a process killed
after its commit, the next server started after more than an hour (the result has the ref).
The present test of a record written 5 s late changes: that save is now deferred.

Acceptance, by a person's trial (headless runs do not show the defect), on a disposable vault, tool in
`permissions.allow`, **in automatic mode and out of it**: a save with good evidence is admitted with it; one
with a bad quote stores nothing and the next memory call says so; the same after `/clear` and from a subagent;
three saves in one message. Then `val3 init --allow-save` on the adopters.

## 8. Decisions for the owner

Both decided as drafted, 2026-11-17.

1. **The entry exists only after the check (D), or at once and unverified (A).** Drafted D: one admission
   rule, ADR 0013's "no save without its evidence" kept, far less code. A gives a ref in the answer and an
   entry that exists even if the check never runs, at the cost listed in §4.
2. **A deferred save that fails, or whose record is never found.** Drafted: nothing is stored (as a failed
   save today), the event is in the vault, and the agent is told at its next memory call if there is one.
   Other: admit it unverified without its evidence, as capture does since ADR 0012's amendment 1; the knowledge
   is kept, marked, and "no save without its evidence" no longer holds for the tool.

## 9. The challenge of the first draft

Codex gpt-6.1-sol, `medium`, read-only, 2026-11-17; each citation checked against the code by the architect.
- Answered by changing approach: 1 (hash does not bind a recipe or a tool), 3 (validate before removing
  evidence), 4 (equal unverified entry), 5 and 6 (supersessions), 12 (race with capture), 16 (status and view
  do not count the new events), 7 (compare with admitting after the check: taken as the design).
- Taken into this draft: 2 (nothing is committed before the job file; the call id in the event makes a rerun
  harmless), 8 and 9 (the cut is stricter, on both paths; position of the record; the id twice in a file), 10
  and 11 (outside the capture lock; started by the parent after the answer; the wait counted from there;
  leftovers and a final outcome), 13 (once only by call id), 14 (private directory, vault re-checked), 15
  (the parent alone keeps and reports results), 17 (tests).
- Not taken: a resumed session whose two files hold the call stays `ambiguous`, as today.

**Second challenge**, of §5 to §7 of this approach (Codex gpt-6.1-sol, `medium`, read-only, 2026-11-17; 13
objections, 5 blocking; citations checked by the architect).
- Taken by making the design smaller: 1, 2, 3 and 5 (once-only by a lookup of the call id in the events does
  not hold: `discarded` is logged outside the transaction, `duplicate` logs nothing, and the table has no
  index; replaced by a lock on the job file held by the process, and a rerun that ends `duplicate`); 6 (the process waits
  120 s and always writes a result; recovery of left-overs said to be opportunistic); 12 (`SAVE_DEADLINE`
  stays 50 s; the 5 s case named as deferred).
- Taken as written: 4 (the action reaches `serve` and starts after the flush), 7 (the cut for the tool only,
  both record shapes tested), 8 (notice budget, one safe line, deleted after it was written), 9 (the notice
  belongs to the server and names the entry), 10 (a server recovers the left-overs of its own `cwd` only), 11
  (private directory, exclusive creation, no links, id checked again), 13 (usage lines; failures the database
  cannot record).
- Not taken: a timer in the server for left-overs (3 and 6 in part): a killed process is rare and the next
  save or start recovers it; a separate lock file per job (5): the job file is written once and never replaced, so the lock is on it.

**Fourth challenge**, asked by the supervising session on the owner's behalf (Codex gpt-6.2-astra, `medium`,
read-only, 2026-11-17; 8 points, 4 high; checked against the code by the architect). Taken: 1 (the job holds
the hash of the arguments and the vault; measured equal in 15 of 15 calls), 2 (the job file written whole,
linked, and locked by the parent until the flush), 3 (the result published whole; consumed only once the job
file is gone), 4 (owner's decision through the supervisor: a recoverable result; the call id in the events
that change the vault, the review's creation included, asked before expiring or repeating), 5 (Task 0 as
measured, §1), 6 and 7 (tests; exactly one block; the rule's wording), 8 (the 120 s bound the search;
left-overs listed without the locked ones).

**Challenge of the plan** (Codex gpt-6.1-sol, `high`, read-only, 2026-11-17; 12 objections, 6 blocking). It
changed this section: a claim by renaming the job file, with an age for a claimed job, let two processes own
one job in two windows (objections 1 and 2); the lock replaces it, and the job ends when its result is
written (3). The rest is in plan 18.

## 10. Left to the builder

How `submit` gets the call id for its events' detail; the cut's parameter through `_quote_failures` and
`evidence_text`; the code `_save` and the detached process share; the result file's format.
