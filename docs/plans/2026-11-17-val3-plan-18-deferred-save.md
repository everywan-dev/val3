# Plan 18 — `memory_save`: a save carried out after the call

**Parked, 2026-11-17 (owner's decision): not built now.** Reason: plan 18's Task 0 shows that with the tool
pre-allowed the first save of every message would be deferred in automatic mode too, and every supervised
session runs in automatic mode, where a save already needs no prompt and is answered with its ref. To reopen:
adopters that work in manual mode. The last challenge asked before Task 1 was not run, and `--allow-save` is
not used on any adopter. Task 0 is done; Tasks 1 to 6 are not started.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

Status: written 2026-11-17; challenged by Codex (gpt-6.1-sol, `high`, read-only: 12 objections, 6 blocking, all
taken, see the end), then by Codex gpt-6.2-astra (`medium`) for the supervising session: approved with
conditions, taken below. **Task 0 is done (2026-11-17) and its stop condition is met: with the tool
pre-allowed the record is written only after the call returns, in automatic mode too. Task 1 does not start
until the owner decides whether to build.** Before Task 1, also: a new challenge of design and plan with
gpt-6.2-astra. Code review of each task: Codex gpt-6.2-sol, `high`, and the architect.

**Goal:** a `memory_save` with evidence whose call record is not on disk within 2 s is carried out by a
detached process after the answer, with the same submission and the same evidence rule.

**Architecture:** the child answers `deferred`; the parent writes a job file and, after flushing the answer,
starts `val3 mcp --deferred <call id>`, which locks the job file, waits for the record, reads the candidate
from it and runs today's submission. The parent reports results before the answer of a later memory call. No
schema change.

**Tech stack:** Python 3.11 standard library, psycopg, pytest (subprocess tests against a disposable
PostgreSQL).

**Spec:** `docs/design/2026-11-17-deferred-evidence-check-design.md` (§5 to §7) and
`docs/adr/0013-memory-save-tool.md`, amendment 1. Read both before any task.

## Global constraints

- English everywhere; no internal project, host or person names.
- Tests drive the CLI and the server as subprocesses and never import package internals. Helpers of
  `tests/test_memory_save.py`: `mcp_live`, `save_call`, `use`, `env.append`, `entries`, and `events`, which
  returns tuples (read the detail by position, as the present tests do). The state directory of a test is
  `env.state`. A held drain lock has a precedent in `tests/test_extract.py` (about line 229).
- No fixed sleeps for an outcome: poll for the condition with a limit (10 s unless the test says otherwise).
- **The whole suite (`pytest -q`) passes before each commit.** A task that changes behaviour updates the
  present tests it invalidates in the same commit, and names them in the commit body.
- Constants: `LOOKUP = 2.0`; `SAVE_DEADLINE = 50.0` (unchanged); the process polls every 0.2 s for 10 s, then
  every 2 s, until 120 s from its start (`VAL3_DEFERRED_WAIT` overrides the 120, for tests); a job older than
  3600 s ends `no-transcript`; result files are deleted after 86 400 s; at most 5 notices per answer; a notice
  line holds at most 300 characters; a result file is read up to 4 096 bytes.
- The deferred answer, exact: `not stored yet: the record of this call is not in the transcript, so the save is
  carried out once it is; a later memory call reports its outcome`.
- A notice line, exact: `earlier save "<title>": <line>`, or `earlier save: <line>` when the result has no
  title. Title and line each pass `safe(_one_line(...))` and then have every run of whitespace, tabs included,
  replaced by one space.
- `val3 save` behaves as before.

## Review focus

1. The parent is killed after writing the job file and before starting the process: the save is carried out at
   the server's next start or save (Task 5).
2. Two processes on one job, also a left-over started by two servers at once: one entry, one result (Tasks 2
   and 5).
3. The process dies after its commit and before the result: the rerun reports `duplicate` with the ref, no
   second entry (Task 5).
4. Six results at once, a title with tabs, an answer at `MAX_RESULT`: five safe lines, the sixth at the next
   call, the whole within the limit (Task 4).
5. A job file that is a symbolic link, or names another call id or `cwd`: refused, nothing read or written
   outside `<state>/deferred/` (Task 2).

Not testable from outside, for the code reviewer: `serve` starts a process only after the answer's `flush`
returned (Task 3).

---

### Task 0: measure before any code

The architect, with the present code and a logging server as in ADR 0013's last section. Four cells: the tool
in `permissions.allow` or not, automatic mode or not, in an interactive session. For each, the time from the
call's arrival to (a) the record complete on disk and (b) the answer sent, and the class: within 2 s; between
2 and 30 s while the call is pending; only after the call returned.
- [x] Write the four cells in `SESSION.md` and in the design's §1. Done 2026-11-17 (`eval/plan-18/t0/`): each
  answer held 32 s, a single call and a batch of three per cell, time and order logged apart, "not in 120 s"
  a possible outcome (never seen).
- [x] Reported: "pre-allowed, automatic mode" is "only after the call returned". As written:
  if "pre-allowed, automatic mode" is not "within 2 s", report to the owner before Task 1: with the
  permission every save of his adopters would be deferred (2 s more, no ref in the answer), where without it
  they are answered with the ref and no prompt. Whether to build, and where to allow the tool, is his decision.

### Task 1: evidence before the call, and the call id in the events

**Files:** modify `src/val3/transcript.py`, `src/val3/writer.py`, `src/val3/mcp.py`; tests
`tests/test_memory_save.py`, `tests/test_save.py`.

**Interfaces — produces:**
- `transcript.call_files(directory, call_id, since) -> list[tuple[Path, str, str | None, int]]`: the fourth
  item is the zero-based index of the line holding the call, in the reader's numbering (every complete line
  counts, an invalid one too); one match per matching `tool_use` block, so the id on two lines of one file,
  or in two blocks of one line, gives two matches. Its only caller is `mcp._lookup`.
- `transcript.evidence_text(path, private=(), until: int | None = None)`: with `until`, only lines of index
  below it are read, cut before exclusions and call/result pairs are computed.
- `writer._quote_failures(evidence, transcript, private=(), until=None)`; other callers (`confirm`) unchanged.
- `writer.submit(..., until: int | None = None, call: str | None = None)`: `until` reaches the quote check
  (`None` when there is no transcript); `call` reaches the event's detail as `"call"` through
  `_via(via, agent, call)` for `admitted` and `verified`, through `_discarded` for `discarded`, and into the
  `queued` event `_queue` logs when it creates a review item (same transaction).
- `mcp._lookup(call, directory) -> tuple[Path, str, str | None, int]`; two matches, in one file or two, raise
  `ambiguous`.

- [ ] **Step 1: failing tests** (`tests/test_memory_save.py` unless said):
  - `test_a_quote_after_the_call_is_no_evidence`: the `use(...)` record, then a tool output holding the quote:
    error starting `discarded: `, `entries(env) == []`.
  - `test_the_cut_with_another_tool_in_the_same_record` and `test_the_cut_with_another_tool_in_its_own_record`:
    a Bash call whose result comes after the save's record is no evidence in either shape; one whose result
    comes before it is.
  - `test_user_messages_before_and_after_the_call`: a typed message, and a queued one (`queued_command`, as
    `tests/test_extract.py` builds it), are evidence of kind `user` before the record and not after it.
  - `test_the_id_twice_in_one_file` and `test_two_blocks_of_the_id_in_one_line`: an error, and the usage
    line's `status == "ambiguous"`.
  - `test_an_invalid_line_before_the_cut`: a line that is not JSON before the call's record; a quote in a
    record before it is evidence, one after the call is not.
  - `test_a_queued_review_carries_the_call_id` (a vault not upgraded): the `queued` event's detail holds
    `call`.
  - `test_the_events_carry_the_call_id`: the detail of `admitted`, of `verified` and of `discarded` holds
    `"call": "<the id>"`.
  - `tests/test_save.py::test_save_reads_the_whole_transcript`: `val3 save --transcript` admits a candidate
    whose quote is in the last record.
- [ ] **Step 2:** run `pytest tests/test_memory_save.py tests/test_save.py -q`: the new tests fail.
- [ ] **Step 3:** implement; `_save` passes the line as `until` and the call id as `call`. Update the present
  assertions that compare an event's detail literally (they gain `call`).
- [ ] **Step 4:** `pytest -q`: all pass.
- [ ] **Step 5:** commit `feat: memory_save counts only evidence before its call; events carry the call id`.

### Task 2: the job file and the detached process

**Files:** create `src/val3/deferred.py`, `tests/test_deferred.py`; modify `src/val3/mcp.py`,
`src/val3/cli.py`, and `use` in `tests/test_memory_save.py` (new keyword `vault=None`, put in the `input`).

**Interfaces — produces** (`val3.deferred`):
- `directory() -> Path`: `state_dir() / "deferred"`, created with mode `0o700`.
- `fingerprint(arguments: dict) -> str`: SHA-256 hex of `json.dumps(arguments, sort_keys=True,
  ensure_ascii=False, separators=(",", ":"))`.
- `write_job(call: str, transcripts: Path, cwd: Path, vault: str, arguments: dict) -> int`: `<call>.json`
  holding exactly `{"call", "directory", "cwd", "at", "vault", "args"}` (`args` the fingerprint). The content
  is written whole to `.<call>.<pid>.tmp` (`O_EXCL`, `0o600`), the descriptor takes `flock(LOCK_EX)`,
  `os.link` gives it its final name (an existing job raises `Val3Error`), the temporary name is removed. The
  descriptor, still locked, is returned: the caller closes it after the answer's flush. Never replaced.
- `locked(call: str) -> bool`: whether another process holds the job's lock (try `LOCK_NB`, release).
- `claim(call: str) -> tuple[int, dict] | None`: opens `<call>.json` with `O_NOFOLLOW`, takes
  `flock(LOCK_EX | LOCK_NB)` and returns (descriptor, job); `None` when the lock is held, the file is gone, or
  it was removed while the lock was awaited (compare `os.fstat` with `os.stat` of the path). Raises `Val3Error`
  for: a name not matching `mcp.CALL_ID`, a file that is not a regular file, a job whose `"call"` differs from
  the name. The caller keeps the descriptor open until it exits.
- `publish(call: str, title: str | None, status: str, line: str) -> None`: writes
  `{"title", "status", "line", "at"}` whole to a temporary name, links it to `<call>.result` (an existing
  result is left alone), then removes `<call>.json`.
- `recorded(conn, vault: str, call: str) -> tuple[str, str] | None`: (status, ref) of the event of that vault
  whose detail holds the call id, among `admitted`, `verified`, `queued`.
- `run(call: str, cwd: Path) -> int`: design §5.2. In order: `claim` (`None`: return 0); a job whose `cwd` is
  not `cwd`: `Val3Error`; `<call>.result` already there: remove the job, return 0; `recorded(...)` for the
  job's vault: an event there is published as the outcome and nothing is run (a database that cannot be
  asked: return 0, the job stays); a job older than 3600 s: `publish(…, "no-transcript", …)`; wait for the
  record (`ambiguous` and `no-transcript` are published); the record's input must have the job's fingerprint
  and the vault is the job's (otherwise `publish(…, "error", …)`); `mcp.carry_out` with an `emit` that calls
  `publish` and writes the usage line, then `signal.alarm(50)` before the vectors. Any `Val3Error` after the claim is published with status `error`, or the writer's status
  when it has one (`discarded`).
- CLI: `val3 mcp --deferred <call id>`, hidden as `--call`; exit `0` when it published or another process holds
  the job, `2` for a refused job.
- `mcp.carry_out(candidate, vault, cwd, path, session, agent, until, call, read, emit) -> str`: what `_save`
  does from `load_project` to its returned text; `_save` calls it too.
- The usage line the process writes: `usage.record(project, "memory_save", [vault], [], outcome, status=…,
  ref=…)` with `outcome` `ok` or `error`; none when the project's configuration does not load.

- [ ] **Step 1: failing tests** in `tests/test_deferred.py`; each writes the job file by hand under
  `env.state` and runs `val3 mcp --deferred <id>`:
  - `test_the_record_written_after_the_process_starts`: record appended 1 s later; exit 0; one entry with its
    evidence; the `admitted` event's detail has `via` `tool`, the call id and the session; the result has
    status `admitted` and the ref in `line`; no `<id>.json` left.
  - `test_a_subagents_deferred_save`: the event's detail has `agent`.
  - `test_a_recipe_a_tool_and_a_supersession`; `test_an_equal_unverified_fact_is_verified` (status
    `verified`); `test_a_vault_not_upgraded` (status `queued`).
  - `test_a_failed_quote_stores_nothing`: status `discarded`, `entries(env) == []`.
  - `test_no_record` (`VAL3_DEFERRED_WAIT=1`): status `no-transcript`, nothing stored, no usage ref.
  - `test_a_transcript_older_than_the_call`: the record is in a file whose mtime is 120 s before the job's
    `at`: `no-transcript`.
  - `test_the_id_in_two_files`: status `ambiguous`.
  - `test_two_processes_on_one_job`: ten times, two processes started together: one entry, one result, both
    exit 0.
  - `test_a_result_already_there`: job and result both present: the job is removed, the result unchanged,
    nothing stored.
  - `test_a_job_older_than_an_hour`: status `no-transcript`.
  - `test_a_job_that_is_a_symbolic_link`, `test_a_job_naming_another_id`, `test_a_job_of_another_cwd`: exit 2,
    no result, the link's target unchanged.
  - `test_a_read_only_vault` (`use(..., vault=<a linked vault>)`): status `error`, nothing stored.
  - `test_a_relocated_private_memory_directory`: a quote found only there: `discarded`.
  - `test_a_capture_holding_its_lock`: with the drain lock held, the save is admitted.
  - `test_capture_first_then_the_deferred_save` and `test_the_deferred_save_first_then_capture`, each for a
    fact and for a recipe, capture's quotes failing (admitted unverified by capture): the fact ends `verified`
    by the save; the recipe's save ends `error` and stores no second entry; assert backing, origin and each
    operation's outcome.
  - `test_a_record_without_the_evidence_the_call_sent` and `test_a_record_naming_another_vault`: status
    `error`, `entries(env) == []`.
  - `test_an_event_of_the_call_is_the_outcome`: an `admitted` event with the call id already in the vault and
    the job file left, also with a job two hours old and no transcript: the result has status `admitted` and
    the ref; one entry. The same for `queued`.
  - `test_the_database_cannot_be_asked`: the job file stays, no result.
  - `test_a_job_file_recreated_under_the_name`: a descriptor opened, the file removed and written again:
    `claim` on the old descriptor returns `None`.
  - `test_the_result_does_not_wait_for_the_vectors` (fixture `embedder`, `trickle = True`, the process started
    with `Popen`): the result exists within 10 s of the record; the process exits between 45 and 58 s after
    it; the job file is gone from the moment the result exists.
  - `test_the_usage_line_of_the_process`: one line with `status == "admitted"` and the ref; and, with a
    `val3.toml` made unreadable after the job was written, no line and a result of status `error`.
- [ ] **Step 2:** run `pytest tests/test_deferred.py -q`: all fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** `pytest -q`: all pass.
- [ ] **Step 5:** commit `feat: a detached process carries out a deferred memory_save`.

### Task 3: the server defers

**Files:** modify `src/val3/mcp.py`; tests `tests/test_memory_save.py`.

**Interfaces — consumes** `deferred.write_job`, the CLI of Task 2. **Produces:**
- `_State.__init__` sets, per instance: `after: list[str]` (ids whose process `serve` starts after the
  answer's `flush`), `held: list[int]` (locked descriptors of the jobs just written, closed after the flush),
  `deferred: list[str]` (ids awaiting a notice), `reported: list[str]` (Task 4).
- The child's answer for a deferred save: `read["status"] == "deferred"`, the exact text of Global
  constraints, `is_error` false.

- [ ] **Step 1: failing and changed tests:**
  - change `test_the_record_written_after_the_call_starts`: the answer arrives within 4 s with the deferred
    text; once the record is appended, `entries(env)` holds the entry with its evidence within 10 s.
  - change `test_records_that_only_look_like_the_call`: the answer arrives in under 4 s with the deferred
    text; with `VAL3_DEFERRED_WAIT=1` the result is `no-transcript` and `entries(env) == []`.
  - `test_the_job_file_holds_no_candidate_text`: its keys are exactly `call`, `directory`, `cwd`, `at`,
    `vault`, `args`, and no value holds the candidate's title or a quote.
  - `test_a_job_is_not_claimed_before_the_flush`: a server whose stdout is a pipe nobody reads and already
    full (the flush blocks): `val3 mcp --deferred <id>` run by the test exits 0 without a result while the
    server is blocked; once the pipe is read, the save is carried out.
  - `test_an_invalid_candidate_is_not_deferred`, `test_a_read_only_vault_is_not_deferred`: an error during the
    call, no job file.
  - `test_no_evidence_is_not_deferred`: admitted unverified during the call.
  - `test_a_job_already_there`: a job file of that id exists: the call is an error, the file unchanged.
  - `test_the_usage_line_of_a_deferred_call`: `status == "deferred"`, no ref.
  - `test_the_description_names_the_deferred_save`: the tool's description contains `a later memory call
    reports`.
  - `test_the_server_session_file_gone` and `test_the_directory_is_resolved_once` stay as they are and pass.
- [ ] **Step 2:** run `pytest tests/test_memory_save.py -q`: the new and changed tests fail.
- [ ] **Step 3:** implement. A save is deferred only when the transcripts' directory was resolved; with
  `directory` `None` the error stays as today. `_save`: on `_NoTranscript` of status `no-transcript`, with a
  directory and evidence: `candidate.validate`, `project.writing`, answer deferred. `_tool_call`: on that
  status, `write_job` with the vault the child reported in `read` and the call's arguments, keep the returned
  descriptor in `state.held`, then append the id to `state.after` and `state.deferred`; a `Val3Error` of
  `write_job` is the call's error. `serve`: after `stdout.flush()` returned, close the descriptors of
  `state.held` (releasing the locks), then start each id of `state.after`
  (`[sys.executable, "-P", "-m", "val3", "mcp", "--deferred", id]`, `start_new_session=True`, the three
  streams `DEVNULL`, `cwd` the server's) and empty the list; nothing is started on `BrokenPipeError`. The
  messages that use `AGAIN` no longer say the transcript may be late where the save is now deferred.
- [ ] **Step 4:** `pytest -q`: all pass.
- [ ] **Step 5:** commit `feat: memory_save defers a save whose record is not written within 2 s`.

### Task 4: the notice

**Files:** modify `src/val3/mcp.py`, `src/val3/deferred.py`; tests `tests/test_memory_save.py`.

**Interfaces — produces:** `deferred.results(calls: list[str], limit: int = 5) -> list[tuple[str, str]]`:
(call id, notice line) for the ids that have a result file **and no job file**, at most `limit`, in the order
of `calls`;
`deferred.forget(calls: list[str]) -> None`; `deferred.prune() -> None` (result files older than 86 400 s
whose job file is gone).
`_tool_call` puts the ids it reported in `state.reported`; `serve`, after the answer's `flush` returned,
calls `forget(state.reported)`, removes them from `state.deferred` and empties the list.

- [ ] **Step 1: failing tests:**
  - `test_the_next_memory_call_reports_the_save`: after a deferred save was carried out, a `memory_search`
    answer starts with `earlier save "<title>": admitted <vault>#1`; the next one does not.
  - `test_a_discarded_save_is_reported`: starts `earlier save "<title>": discarded: `.
  - `test_six_results`: result files written by hand for six deferred ids: five lines, then one.
  - `test_a_result_with_tabs_line_breaks_and_no_title` (result files written by hand): one line each, no tab,
    no control character; the one without a title starts `earlier save: `.
  - `test_notices_and_answer_fit_the_limit`: a search answer at `MAX_RESULT`: the text is at most `MAX_RESULT`
    characters and starts with the notice.
  - `test_a_result_that_arrives_during_a_call`: reported by that call.
  - `test_a_result_whose_job_file_still_exists`: not reported and not deleted; reported once the job file is
    gone.
  - `test_a_restarted_server_reports_nothing`; `test_a_result_file_larger_than_the_bound` (ignored beyond
    4 096 bytes, still one line).
- [ ] **Step 2:** run them: fail.
- [ ] **Step 3:** implement: in `_tool_call`, after the child ended, `results(state.deferred)`; the tool's own
  text is cut so that notices plus text fit `MAX_RESULT`.
- [ ] **Step 4:** `pytest -q`: all pass.
- [ ] **Step 5:** commit `feat: a later memory call reports the outcome of a deferred save`.

### Task 5: left-overs

**Files:** modify `src/val3/deferred.py`, `src/val3/mcp.py`; tests `tests/test_deferred.py`.

**Interfaces — produces:** `deferred.left_over(cwd: Path) -> list[str]`: the ids of the `<id>.json` files whose
job's `cwd` is `cwd` and that nobody holds a lock on (`locked`), so no process is started for a running job;
the claim in the process stays the one that decides. Names starting with `.` are not jobs. `serve` starts a
process for each at its start and after the answer of each `memory_save` call, and calls `deferred.prune()` at
its start.

- [ ] **Step 1: failing tests:**
  - `test_the_parent_killed_after_the_job_file`: a job and its record written by hand, a server started: the
    entry exists within 10 s.
  - `test_a_left_over_carried_out_at_a_later_save`: the same with the server already running and a later save
    of another fact.
  - `test_a_process_killed_before_its_result`: an entry already admitted and its job file left: after a server
    starts, one entry, and a result of status `duplicate` with the ref in `line`.
  - `test_a_process_killed_during_the_vectors` (`embedder.trickle`, the process killed once the result
    exists): no job file, one entry, nothing started by the next server.
  - `test_two_servers_start_on_one_left_over`: one entry, one result.
  - `test_a_left_over_of_another_cwd_is_not_touched`; `test_old_results_are_deleted`.
- [ ] **Step 2:** run `pytest tests/test_deferred.py -q`: the new tests fail.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** `pytest -q`: all pass; the count goes in the commit body.
- [ ] **Step 5:** commit `feat: a server carries out the deferred saves a killed process left`.

### Task 6: acceptance by a person, then the adopters

The architect, with the owner's session where needed; a scratch project and a disposable vault.

- [ ] Control, tool not pre-allowed, automatic mode: a save is answered with its ref during the call.
- [ ] Tool in `permissions.allow`, in automatic mode and out of it: a save with good evidence; a bad quote;
  after `/clear`; from a subagent; three saves in one message. Each ends admitted with its evidence, or stores
  nothing for the bad quote. Where a save was deferred, the next memory call reports it.
- [ ] A session closed right after a deferred answer, and one that makes no other memory call: the entry, the
  result file and the usage line are on disk.
- [ ] Times and outcomes in `SESSION.md`.
- [ ] Only if all pass: merge, deploy on both hosts, update ADR 0013's amendment and `TODO.md`, and note the
  deployment date where stage 2 of plan 17 is read: Task 1's cut changes the synchronous path during that
  trial.
- [ ] `val3 init --allow-save` on an adopter only where Task 0 and this trial show its sessions keep an
  immediate ref, or on the owner's decision.

## The plan's challenge

**Codex gpt-6.2-astra, `medium`, read-only, 2026-11-17**, asked by the supervising session on the owner's
behalf (8 points, 4 high; report in that session's repository). Checked against the code; all taken: the
fingerprint of the arguments and the vault in the job (1); the job written whole, linked and held locked until
the flush (2); the result published whole and consumed only without its job file (3); the call id in the
events that change the vault, asked before expiring or repeating (4, the owner's decision for a recoverable
result); Task 0's method (5); capture in both orders (6); exactly one block, the reader's line numbering (7);
left-overs listed without the locked ones (8).


Codex gpt-6.1-sol, `high`, read-only, 2026-11-17 (report kept outside the repository, `eval/plan-18/`).
Citations checked against the code and the tests by the architect. All taken:
- 1, 2, 3: a claim by renaming, with an age for a claimed job, had two windows with two owners, and a
  left-over could be ended while it ran. Replaced by a lock on the job file held by the process; the job ends
  when its result is published; expiry is decided by the process that holds the lock.
- 4: the usage line's fields, and no line when the configuration does not load.
- 5: the suite before each commit; the present tests each task changes are named.
- 6, 7: `_via`, `_discarded`, `_quote_failures` defaults; per-instance lists of `_State`; `events` returns
  tuples; `use` gains `vault`.
- 8: the vectors' limit is an alarm in the process, tested against an embedder that outlasts it.
- 9: no test of "flush before start" by polling; it is a review point. Conditions with limits, no fixed
  sleeps.
- 10, 11: tests added (queued message, existing job, subagent, the 60 s filter, a vault not upgraded, recovery
  without restart, results written by hand for titles).
- 12: Task 0 measures four cells and three classes; Task 6 has a control and the two sessions that never read
  a notice.
