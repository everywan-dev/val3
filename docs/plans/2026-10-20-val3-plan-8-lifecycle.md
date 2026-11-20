# VAL3 plan 8 — entry lifecycle: re-check mark, confirmation, withdrawal with evidence

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** an entry whose `file` quote is no longer at HEAD is shown as *to re-check*; the agent clears the mark
with `val3 confirm`, replaces the entry with `val3 save` (`supersedes`), or withdraws it at once when it brings
tool evidence.

**Architecture:** a new module `recheck.py` computes the mark on read (HEAD resolved once to a sha; one `git cat-file --batch-check`
for commits, one `git cat-file --batch -Z` for `<sha>:<path>`; one 3-second budget for all git calls), used by the session index, per-prompt delivery, `show` and
`status`. The writer gains `confirm` and an evidence path for withdrawals; `withdrawal` gains an `evidence jsonb`
column added by `vault upgrade`. Nothing stores the mark.

**Tech Stack:** Python 3.11 standard library, psycopg 3, PostgreSQL 16 with pgvector, git; pytest driving
`python -m val3` as a subprocess against a disposable PostgreSQL.

**Spec:** `docs/design/2026-10-20-entry-lifecycle-design.md` (rulings R1-R3, decisions L1-L8).

## Global Constraints

- Everything in English: code, comments, messages, docs, commits.
- No internal project names, hosts or people in the repository.
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error.
- Tests drive the CLI as a subprocess and assert on exit codes, output and rows; never import package internals.
- Branch `feature/lifecycle` (exists, holds the design); Conventional Commits; one logical change per commit;
  commit trailers name the writing model.
- Suite: `.venv/bin/pytest -q` (Docker needed). One test: `.venv/bin/pytest -q tests/<file>::<test>`.
- Vault tables are append-only; nothing is updated or deleted.
- Git is read through objects only (`<sha>:<path>`, the sha resolved once per computation), never the working
  tree. `recheck.py` has its own git runner: all calls of one computation share a 3-second budget
  (`timeout=` the time left); a failure or an exhausted budget leaves every entry unmarked; hooks never fail
  because of git. `importer._git` (60 s) is not used there.
- The agent may withdraw at once only with evidence that would admit without review: any tool item, or `user`
  items when the entry is a preference or a decision (design L6). Supersession is unchanged (L7).
- Output strings exactly as given:
  - index line suffix ` · to re-check`; index header addition (Task 2, verbatim);
  - per-prompt block `- <ref> [<kind>] <title> · to re-check: <statement>`;
  - `show`: `TO RE-CHECK: <path> changed at HEAD <sha12>` / `TO RE-CHECK: <path> is not in HEAD <sha12>`;
  - status: `to re-check: <N>` or `to re-check: <N> (<ref>, …)` (at most 10 refs, then `, …`), or
    `to re-check: not checked (<root> is not the root of a git repository)`;
  - `confirmed <ref> (<n> evidence items at <sha12>)` when any `file` item, else `confirmed <ref> (<n> evidence items)`;
  - `withdrawn <ref>` (unchanged) for an agent withdrawal.

## Review Focus

1. A cited path with spaces or accents (`reproduction` is shlex-quoted): marked and confirmed like any other —
   Task 2 and Task 4 tests.
2. A cited file that becomes binary or non-UTF-8 at HEAD: marked as changed, no traceback — Task 2 test.
3. `git` missing from `PATH`, or a `git` that hangs: session start, prompt and `show` answer without marks within
   the budget — Task 2 tests.
4. The agent quotes, as withdrawal or confirmation evidence, a quote that reached the transcript only through
   val3's own output, also written `val3 'show' <ref>`, `val3 "show" <ref>` or `\val3 show <ref>`: refused like
   any quote not in tool output. Today `_VAL3_COMMAND` (`transcript.py:22`) misses the quoted and escaped forms —
   Task 5 fixes and tests it.
5. `withdraw --evidence` on a vault whose `withdrawal` table predates the `evidence` column: exit 1 naming
   `val3 vault upgrade`; `withdraw` without evidence keeps working there — Task 5 test.

---

### Task 1: Schema — `withdrawal.evidence`

**Files:**
- Modify: `src/val3/withdrawal.sql`, `src/val3/db.py` (`upgrade_vault`)
- Test: `tests/test_vault.py`

**Interfaces:**
- Produces: column `<vault>.withdrawal.evidence jsonb` (nullable).

- [ ] **Step 1: Failing test** `test_upgrade_adds_the_withdrawal_evidence_column`: create a vault, run
  `ALTER TABLE {v}.withdrawal DROP COLUMN evidence`, insert one entry and one withdrawal row without evidence;
  `vault upgrade` exits 0 and prints the changed message; `information_schema.columns` has `evidence` of type
  `jsonb`; the old row reads `evidence IS NULL`; a second `vault upgrade` reports nothing changed. Add
  `evidence` to the append-only statements of `test_vault_tables_are_append_only`
  (`UPDATE {v}.withdrawal SET evidence = '[]'`).
- [ ] **Step 2:** run it; expect FAIL (no column).
- [ ] **Step 3:** add `evidence jsonb` to `withdrawal.sql`; in `upgrade_vault`, after the table loop, when
  `withdrawal` exists and `information_schema.columns` lacks `evidence`, run
  `ALTER TABLE {v}.withdrawal ADD COLUMN evidence jsonb` and set `changed = True`. Grants are per table, nothing
  to copy.
- [ ] **Step 4:** run `tests/test_vault.py`; expect PASS.
- [ ] **Step 5:** commit `feat: withdrawal evidence column, added by vault upgrade`.

### Task 2: The mark — `recheck.py`, session index, `show`

**Files:**
- Create: `src/val3/recheck.py`, `tests/test_recheck.py`
- Modify: `src/val3/views.py` (`_INDEX_HEADER` use, `session_index`, `show_entry`), `src/val3/cli.py` (`cmd_show`)

**Interfaces:**
- Produces, in `recheck.py`:
  - `@dataclass(frozen=True) class Change: path: str; missing: bool` (missing: no such path at HEAD).
  - `repository(project: Project) -> Path | None`: `project.root` when `git -C <root> rev-parse --show-toplevel`
    resolves to it; else, or on any git failure (`OSError`, timeout 10 s, non-zero exit), None.
  - `marks(conn, project: Project, ids: Collection[int] | None = None) -> tuple[str | None, dict[int, list[Change]]]`:
    HEAD's full sha and, per marked home-vault entry, its changed paths (sorted, one per path). `ids` limits the
    query. Returns `(None, {})` when no repository, on any git failure or when the budget runs out.
  - `head(repo: Path) -> str`: `git rev-parse --verify HEAD^{commit}` (Val3Error on failure).
  - `read_at(repo: Path, sha: str, paths: Iterable[str]) -> dict[str, str | None]`: text of each `<sha>:<path>`
    (`errors="replace"`, `\r\n` → `\n`), None when missing or not a blob; one `git cat-file --batch -Z` process
    (NUL-terminated input and output, git ≥ 2.43 as installed), so paths with line breaks work.
- Produces, in `views.py`: `show_entry(conn, vault, entry_id, changes: Sequence[Change] = (), head: str | None = None)`.
- Consumes: `db.live`, `db.vsql`. Never `importer._git` (its 60-second timeout would stall a hook).

- [ ] **Step 1: Failing tests** in `tests/test_recheck.py`. Fixture `imported(env)`: `adopter(env)`; `vault create`;
  `answer(env, 0, unit_candidate())`, `answer(env, 1, note_candidate())`; `dry_run`; `import --apply`; approve the
  review item whose title is `unit_candidate()["title"]` → entry `#1`, evidence `config/routing.yaml`,
  quote `subscribers: {}`. Helpers from `test_import`, `test_hooks` (`start`, `context`, `payload`, `hook`).
  - `test_an_entry_whose_quote_holds_is_not_marked`: `context(start(env), "SessionStart")` has no
    `to re-check`; `show` has no `TO RE-CHECK`.
  - `test_a_changed_file_marks_the_entry`: write `config/routing.yaml` = `"subscribers:\n  - billing\nowner: billing\n"`,
    commit; the index line for `#1` ends with `Routing ships no subscribers · to re-check`; the header holds the
    exact text below; `show` line 2 is `TO RE-CHECK: config/routing.yaml changed at HEAD <sha[:12]>`.
  - `test_a_deleted_file_marks_the_entry`: `git rm` the file, commit; `show` says `is not in HEAD <sha12>`.
  - `test_uncommitted_changes_do_not_mark`: edit the file without committing; no mark.
  - `test_a_commit_missing_from_the_repository_is_not_checked`: second directory `env.root / "other"` with its own
    `git init`, one commit, the same `val3.toml`; from there `start` and `show` (cwd) carry no mark although
    `config/routing.yaml` is absent there.
  - `test_a_linked_vault_entry_is_not_marked`: `git clone` the adopter into `env.root / "clone"` (it holds the
    commit), give it a `val3.toml` whose home is a new vault and whose `linked` is `[env.vault]`, change the file
    there and commit: from the clone, `start` and `status` show no mark for `{env.vault}#1`.
  - `test_outside_a_git_repository_there_are_no_marks`: remove `env.project / ".git"` after import; `start` exits
    0 with the index and no mark.
  - `test_without_git_on_path_there_are_no_marks`: after changing the file, run `start(env, PATH=str(env.root /
    "empty"))` and `env.run("show", ref, environ={"PATH": …})` (an empty directory): exit 0, no mark.
  - `test_a_hanging_git_leaves_the_index_unmarked`: `PATH` starts with a directory holding an executable `git`
    that sleeps 30 s; `start` exits 0 in under 10 s with the index and no mark.
  - `test_every_current_quote_is_checked`: the unit's answer cites two files (`config/routing.yaml` quote
    `subscribers: {}`, `docs/adr/0001.md` quote `route every message by its thread owner`); changing either one
    alone marks the entry, and `show` names only the changed path.
  - `test_a_path_with_a_line_break_is_checked`: an adopter whose unit cites a committed file named
    `"odd\nname.yaml"`, imported and approved; changing it marks the entry. If the importer cannot cite such a
    path, cover the case in Task 4 with a confirmation that cites it, then change the file.
  - `test_a_binary_file_at_head_is_marked`: replace the file with bytes `b"\x00\xff\xfe"`, commit: marked, exit 0.
  - `test_paths_with_spaces_and_accents_are_checked`: an adopter whose unit cites `config/rúta de prueba.yaml`
    (same content as `ROUTING`), imported and approved; changing that file marks the entry.
- [ ] **Step 2:** run `tests/test_recheck.py`; expect FAIL.
- [ ] **Step 3:** implement `recheck.py`. Current evidence query (L2), home vault only (L3):

```sql
SELECT ev.entry_id, ev.reproduction, ev.quote FROM {v}.evidence ev JOIN {v}.entry e ON e.id = ev.entry_id
WHERE ev.kind = 'file' AND /*live*/
  AND ev.created_at = (SELECT max(x.created_at) FROM {v}.evidence x WHERE x.entry_id = ev.entry_id)
  [AND ev.entry_id = ANY(%(ids)s)]
```

  Parse `reproduction` with `shlex.split` → `["git", "show", "<commit>:<path>"]`, split at the first `:`; skip a
  row that does not parse. Keep rows whose commit `git cat-file --batch-check -Z` reports as `commit` (input
  `<commit>^{commit}`). Resolve `head(repo)` once and read distinct paths with `read_at(repo, sha, …)`; a quote is held when it is a substring of the
  text; an entry is marked when one of its kept quotes is not held. Catch `subprocess.SubprocessError`, `OSError`
  and `Val3Error` around all git work and return `(None, {})`.
- [ ] **Step 4:** in `session_index`, call `marks(conn, project)` once; a marked home-vault line gets the suffix
  ` · to re-check`; when any line is marked, insert after `_INDEX_HEADER`, separated by a blank line, exactly:

```
Entries marked "to re-check" cite a file that changed since they were checked. Before relying on one, check it:
`val3 confirm` if it holds, `val3 save` with "supersedes" if it changed, `val3 withdraw` if it no longer
applies (`val3 confirm --help`).
```

  The suffix counts toward the size limit like the rest of the line.
- [ ] **Step 5:** `show_entry` inserts after the title line (before `SUPERSEDED`/`WITHDRAWN`, which a marked entry
  never has) one `TO RE-CHECK:` line per change; `cmd_show` passes `marks(conn, project, [entry_id])` when the
  vault is the home vault. Review cards keep calling `show_entry` without marks.
- [ ] **Step 6:** run `tests/test_recheck.py tests/test_hooks.py tests/test_cli.py`; expect PASS.
- [ ] **Step 7:** commit `feat: entries whose file quote left HEAD are marked to re-check`.

### Task 3: The mark in per-prompt delivery and `status`

**Files:**
- Modify: `src/val3/views.py` (`prompt_context`), `src/val3/hooks.py` (`prompt`), `src/val3/status.py` (`report`)
- Test: `tests/test_recheck.py`

**Interfaces:**
- Consumes: `recheck.marks`, `recheck.repository` (Task 2).
- Produces: `prompt_context(conn, hits, limit: int, marked: Collection[str] = ())` where `marked` holds refs.

- [ ] **Step 1: Failing tests**:
  - `test_a_marked_entry_delivered_for_a_prompt_carries_the_suffix`: after the change of Task 2's test, the
    prompt hook for `"routing subscribers"` injects a block starting `- {vault}#1 [fact] Routing ships no
    subscribers · to re-check: `; before the change the block has no suffix.
  - `test_status_counts_entries_to_re_check`: before the change `to re-check: 0`; after it
    `to re-check: 1 ({vault}#1)`. With twelve marked entries (twelve units citing one file, approved, one commit
    changing it) the line is `to re-check: 12 (` + ten refs in id order + `, …)`.
  - `test_status_outside_a_git_repository`: `.git` removed: `to re-check: not checked (<root> is not the root of
    a git repository)`.
- [ ] **Step 2:** run; expect FAIL.
- [ ] **Step 3:** in `hooks.prompt`, compute `marks(conn, project, [h.id for h in found.hits if h.vault ==
  project.home])` only when some hit is of the home vault, and pass the marked refs. In `status.report`, after the
  home vault line: `to re-check: …` from `repository` and `marks`; the database-down branch prints nothing for it.
- [ ] **Step 4:** run `tests/test_recheck.py tests/test_hooks.py tests/test_status.py`; expect PASS.
- [ ] **Step 5:** commit `feat: re-check mark in prompt delivery and status`.

### Task 4: `val3 confirm`

**Files:**
- Modify: `src/val3/candidate.py`, `src/val3/recheck.py`, `src/val3/writer.py`, `src/val3/cli.py`
- Test: `tests/test_confirm.py`

**Interfaces:**
- Produces:
  - `candidate.evidence_items(data: object, head: str | None = None) -> list[dict]`: a non-empty JSON list; each
    non-`file` item through `_evidence`; a `file` item must have exactly `{"kind", "path", "quote"}` and is
    accepted only when `head` is given, then returned by `_file_evidence({**item, "commit": head}, where)`.
    Without `head`: `evidence[<i>]: file evidence is accepted only by \`val3 confirm\``. Empty list:
    `at least one evidence item is required`.
  - `recheck.check_files(repo: Path, sha: str, items: list[dict]) -> None`: for each `file` item, refuses
    (Val3Error, message naming `evidence[<i>]` and the path) a path that is not canonical
    (`posixpath.normpath(path) != path`, or it starts with `/`, `-` or `.git/`), a path for which
    `importer.excluded(path)` is true, a path missing at `sha` or not a blob, and a quote not in its text. Reads
    with `read_at(repo, sha, …)`: the sha checked is the sha stored.
  - `writer._check_evidence(conn, vault, title: str, evidence: list[dict], transcript, org, private=())`: the
    current body, with `title` and `evidence` passed instead of the candidate. It drops `file` items first, then
    returns when nothing is left, before the transcript is required: a confirmation with only `file` items needs
    no transcript. `submit` calls it with `candidate["title"], candidate["evidence"]`.
  - `writer.confirm(conn, project, ref: str, evidence: list[dict], transcript: Path | None, session: str | None,
    private: tuple[str, ...] = ()) -> str`.
- CLI: `val3 confirm <ref> [file] [--transcript PATH] [--session ID]`; JSON list from the file or stdin; inside
  and outside the agent alike.

- [ ] **Step 1: Failing tests** in `tests/test_confirm.py` (fixture `imported` and the file change of
  `test_recheck.py`, imported from there):
  - `test_confirming_with_a_file_quote_at_head_clears_the_mark`: after the change, `confirm {v}#1` with
    `[{"kind": "file", "path": "config/routing.yaml", "quote": "owner: billing"}]` → exit 0,
    `confirmed {v}#1 (1 evidence items at <sha12>)`; a new evidence row `kind='file'`,
    `reproduction = 'git show <sha>:config/routing.yaml'`; the old row still there; no mark in index, `show`,
    status; event `confirmed` with `detail->>'evidence' = '1'`; no review item added.
  - `test_a_confirmation_without_file_items_ends_the_mark`: confirm with a command quote present in the transcript
    (`env.transcript(tool_outputs=["subscribers:\n  - billing"])`, reproduction `git show HEAD:config/routing.yaml`)
    → `confirmed {v}#1 (1 evidence items)`; no mark; a later commit changing the file again marks nothing.
  - `test_confirm_refuses_bad_file_items`: quote not at HEAD; path `.env` (committed); path `config` (a tree);
    path missing; `./config/routing.yaml`; `config//routing.yaml`; `docs/../config/routing.yaml`;
    `./memory/route-by-owner.md` (excluded once canonical); quote of 7 characters; an item carrying `"commit"`;
    each exit 1, no evidence row added.
  - `test_a_file_only_confirmation_needs_no_transcript`: no `--transcript`, no `VAL3_TRANSCRIPT`: exit 0.
  - `test_a_confirmation_keeps_only_the_files_it_cites`: entry with the two quotes of Task 2's
    `test_every_current_quote_is_checked`; change both files; confirm citing only `config/routing.yaml` at HEAD →
    no mark; a later change to `docs/adr/0001.md` marks nothing, a later change to `config/routing.yaml` marks.
  - `test_confirm_checks_transcript_quotes`: a command quote absent from the transcript → exit 1, `discarded`
    event, nothing stored.
  - `test_confirm_needs_a_live_home_entry`: superseded entry (`was superseded by`), withdrawn entry (`was withdrawn
    (wrong)`), linked-vault ref (`read only`), missing entry, `[]` (exit 1, `at least one evidence item`),
    not JSON (exit 1).
  - `test_confirm_with_a_path_with_spaces_and_accents`: the accented-path adopter of Task 2; confirm citing it.
- [ ] **Step 2:** run; expect FAIL.
- [ ] **Step 3:** implement. `confirm`: under `_serialize`, `_require(conn, vault, ref, KINDS)` (home vault, live,
  existing); file items resolved before the transaction: `repository(project)` (None → Val3Error `file evidence
  needs <root> to be the root of a git repository`), `sha = head(repo)`, `evidence_items(data, sha)`,
  `check_files(repo, sha, items)`; transcript items through `_check_evidence`; insert the rows as `_admit` does,
  session id of the call, with `created_at` set to one `clock_timestamp()` read after `_serialize` (the column
  default is the transaction start, which can predate a set that committed while this one waited for the lock); `_log(..., "confirmed", entry_id=…, detail={"evidence": n})`. A linked-vault ref is
  refused in the CLI as `withdraw` does (`read only`).
- [ ] **Step 4:** run `tests/test_confirm.py tests/test_save.py tests/test_import.py`; expect PASS.
- [ ] **Step 5:** commit `feat: val3 confirm appends fresh evidence to a live entry`.

### Task 5: `val3 withdraw --evidence`

**Files:**
- Modify: `src/val3/writer.py` (`Withdrawal`, `withdraw`, `propose_withdrawal`, `decide`, `_withdraw_entry`),
  `src/val3/cli.py` (`cmd_withdraw`, parser), `src/val3/views.py` (`withdrawal_lines`, `show_entry`),
  `src/val3/transcript.py` (`_VAL3_COMMAND`)
- Test: `tests/test_withdraw.py`

**Interfaces:**
- Consumes: `candidate.evidence_items` (no `head`), `writer._check_evidence` (Task 4).
- Produces: `Withdrawal.evidence: list[dict] | None = None` (last field); `withdraw(conn, project, w, by,
  transcript=None, session=None, private=())`; `propose_withdrawal(conn, project, w, session, transcript=None,
  private=())`. Both check `w.evidence` against the transcript before the transaction.
- CLI: `--evidence FILE|-` and `--transcript PATH`; `--batch` with `--evidence` → exit 2.

- [ ] **Step 1: Failing tests** (`AGENT = {"CLAUDECODE": "1", "VAL3_SESSION_ID": env.session}` where a session is
  needed):
  - `test_the_agent_withdraws_at_once_with_tool_evidence`: transcript with tool output
    `"fatal: path 'config/routing.yaml' does not exist in 'HEAD'"`; `withdraw {v}#1 --obsolete --reason "Routing
    file removed." --evidence -` with a command item quoting it → exit 0, `withdrawn {v}#1`; `withdrawn_by =
    'agent <session>'`, `evidence` holds the item, `review_id IS NULL`; the entry leaves the index.
  - `test_mixed_user_and_tool_evidence_withdraws_at_once`.
  - `test_user_evidence_withdraws_a_decision_at_once`: a decision admitted on a user quote; withdrawn with a
    later user quote → `withdrawn {v}#N`.
  - `test_user_only_evidence_on_a_fact_or_none_stays_a_proposal`: `proposed: review #N`; the payload holds the evidence;
    `review show N` prints it; approving copies it into `withdrawal.evidence` with `withdrawn_by` the approver.
  - `test_withdrawal_evidence_must_be_in_the_transcript`: quote absent → exit 1, nothing withdrawn or proposed.
  - `test_a_quote_seen_only_in_val3_output_is_refused`: for each command `val3 show {v}#1`, `val3 'show' {v}#1`,
    `val3 "show" {v}#1`, `\val3 show {v}#1`, the transcript holds a `Bash` tool call with that command whose
    output contains the stored quote; withdrawing with that quote as command evidence → exit 1; the same for
    `confirm`. Control: a `Bash` call `cat routing.yaml` with the same output is accepted.
  - `test_a_batch_takes_no_evidence`: exit 2.
  - `test_file_evidence_is_refused_for_withdrawal`: exit 1, `accepted only by \`val3 confirm\``.
  - `test_withdraw_evidence_needs_the_upgraded_table`: drop the `evidence` column; `--evidence` → exit 1 naming
    `val3 vault upgrade`; without `--evidence` → withdrawn.
  - A person outside the agent with `--evidence`: withdrawn, evidence stored, `withdrawn_by` the OS user.
  - `show` of a withdrawn entry with evidence lists it under the `WITHDRAWN` line as
    `  withdrawal evidence: <kind>: <reproduction>` and `      > <quote line>`.
- [ ] **Step 2:** run; expect FAIL.
- [ ] **Step 3:** implement. `_withdraw_entry` names the `evidence` column only when `w.evidence` is set (so an
  older table keeps working without it); `UndefinedColumn` → `_predates(vault)`. `decide` builds `Withdrawal`
  with `payload.get("evidence")`. `cmd_withdraw` inside the agent: tool evidence (any item not `user`) →
  `withdraw(..., by=f"agent {session or '-'}")` when the evidence would admit without review for the entry's kind
  (any non-`user` item, or `user` items on a preference or decision); else `propose_withdrawal`. Origin of an
  agent withdrawal is `origin(project, session)`. In `transcript.py`, `_VAL3_COMMAND` also matches a subcommand
  in single or double quotes and a `val3` preceded by a backslash.
- [ ] **Step 4:** run `tests/test_withdraw.py tests/test_review.py`; expect PASS.
- [ ] **Step 5:** commit `feat: the agent withdraws with tool evidence; otherwise it proposes`.

### Task 6: Documentation and close

**Files:**
- Create: `docs/adr/0006-re-check-and-agent-authority.md`
- Modify: `README.md`, `CONTEXT.md`, `docs/design/2026-10-20-entry-lifecycle-design.md` (status line),
  `docs/design/2026-10-14-entry-lifecycle-notes.md` (status: superseded by the design), `TODO.md`, `SESSION.md`

- [ ] **Step 1:** ADR 0006: context (no signal when the repository moves; a person's approval is not a reliable
  check); decision (R1-R3, L1, L4, L6); consequences (ADR 0003 "recall does not re-read the repository" and W2 of
  ADR 0005 amended; `vault upgrade` needed for withdrawal evidence; a confirmation without `file` items ends the
  signal). Same shape as ADR 0005.
- [ ] **Step 2:** README: the session index mark and what to do; `val3 confirm` with an example; `withdraw
  --evidence`. `CONTEXT.md`: *to re-check*, *confirmation*, *current evidence*; update *Withdrawal* (the agent
  withdraws with tool evidence).
- [ ] **Step 3:** `.venv/bin/pytest -q`; expect all passed (324 before the plan); `ruff check src tests` reports
  nothing new.
- [ ] **Step 4:** commit `docs: plan 8 closed, ADR 0006`.

---

## Plan challenge (Sol 6.1, 2026-10-20)

Accepted: quoted/escaped `val3` subcommands, canonical paths, one pinned sha, `clock_timestamp()` after the lock,
file-only confirmation without a transcript, a 3-second git budget, NUL-framed paths, two-quote tests, `start`
keyword environment. Rejected: review for every supersession without tool evidence (design L7); L6 aligned with
admission instead.

## Self-review notes

- Spec coverage: L1-L3 → Task 2; §4.1 delivery and status → Task 3; L4, L5, §4.2 → Task 4; L6, L8, §4.3, §5
  data → Tasks 1 and 5; L7 unchanged (no task); tests 1-18 of design §6 → Tasks 1-5.
- Out of scope as in design §7.
