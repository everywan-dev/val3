# VAL3 plan 6 — cleanup of the deferred minors

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** close every deferred minor of plans 3, 3b and 5 listed in `TODO.md` before withdrawal is built.

**Architecture:** no new module and no new command. Each task changes one existing function or screen and pins
it with a CLI test: import path citation and secret exclusion (`importer.py`), the transcript rules that
decide what counts as evidence (`transcript.py`, `extractor_prompt.md`), and the full-screen review
(`tty.py`, `cli.py`).

**Tech Stack:** Python 3.11 standard library, psycopg 3, curses; pytest driving `python -m val3` as a
subprocess against a disposable PostgreSQL (`pgvector/pgvector:0.8.1-pg16`).

**Spec:** the deferred items in `TODO.md` (plan 5, plan 3b and plan 3 minors) and the designs they come
from: `docs/design/2026-10-13-review-tui-design.md`, `docs/design/2026-10-12-import-design.md`,
`docs/design/2026-10-11-operations-design.md`.

## Global Constraints

- Everything in English: code, comments, messages, docs, commits.
- No internal project names, hosts or people in the repository.
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error.
- Tests drive the CLI as a subprocess and assert on exit codes, output and rows; never import package internals.
- Branch `feature/cleanup` from `main`; Conventional Commits; one logical change per commit.
- Run the suite with `.venv/bin/pytest -q` (Docker needed). A single test: `.venv/bin/pytest -q tests/<file>::<test>`.

## Review Focus

1. A note that names a path in prose punctuation (`(docs/e.md),`) must still cite it — Task 1 test covers it.
2. A directory whose name contains `secret` for another reason (Spanish `secretaria/`) is now excluded. Accepted:
   the dry run reports it as `excluded`, never silently; the reviewer checks the message says so.
3. Resizing the window while a confirmation waits answers it "no" (`KEY_RESIZE` is not `y`): the safe side;
   the reviewer checks no resize path can approve.
4. A batch of approvals with the embedding service down must show one warning, not one per item, after the walk.
5. Ctrl-C between two decisions of a batch must leave the summary equal to the rows in `review_decision`.

---

### Task 1: Notes cite `./x`, `../x` and absolute paths; `x.bak` no longer cites `x`

**Files:**
- Modify: `src/val3/importer.py` (`_named`, its caller in `_cited`)
- Test: `tests/test_import.py`

**Interfaces:**
- Produces: `_named(repo: Path, path: str, text: str) -> bool` (was `_named(path, text)`; one caller).

- [ ] **Step 1: Write the failing test** (after `test_a_note_cites_the_path_it_names_not_one_that_ends_the_same`)

```python
def test_a_note_cites_relative_and_absolute_paths_but_not_a_longer_name(env):
    init(env)
    for path in ("docs/a.md", "docs/b.md", "docs/c.md", "docs/d.md", "docs/e.md"):
        write(env, path, "Routing is documented here.\n")
    write(env, "docs/d.md.bak", "An old copy.\n")
    write(env, "memory/routing-docs.md",
          f"See `./docs/a.md`, `../docs/b.md`, `{env.project.resolve()}/docs/c.md` and the notes (docs/e.md), "
          "not the old copy `docs/d.md.bak`.\n")
    commit(env)
    answer(env, 0, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert sorted(item["path"] for item in manifest(env)["sources"][0]["cited"]) == [
        "docs/a.md", "docs/b.md", "docs/c.md", "docs/d.md.bak", "docs/e.md"]
```

- [ ] **Step 2: Run it and see it fail** — `a.md`, `b.md`, `c.md` missing and `docs/d.md` present.

- [ ] **Step 3: Implement**

```python
def _named(repo: Path, path: str, text: str) -> bool:
    """`path` appears in `text` as a whole path, bare, after `./` or `../`, or under the repository's absolute
    path: docs/README.md does not name README.md, and x.bak does not name x."""
    prefix = r"(?:" + re.escape(str(repo)) + r"/|(?:\.\.?/)*)"
    return path in text and bool(re.search(r"(?<![\w./-])" + prefix + re.escape(path) + r"(?![\w/-]|\.\w)", text))
```

In `_cited`: `_named(repo, p, text)`.

- [ ] **Step 4: Run `tests/test_import.py`** — all pass.
- [ ] **Step 5: Commit** — `fix: import cites relative and absolute paths and not a path a longer name contains`

### Task 2: Secret names apply to every path component; import events keep psycopg's DETAIL out

**Files:**
- Modify: `src/val3/importer.py` (`SECRET_NAMES`, `excluded`, the `finally` of the apply loop)
- Modify: `README.md` (import section), `CONTEXT.md` (**Import**), `docs/design/2026-10-12-import-design.md` §4 item 4
- Test: `tests/test_import.py`

- [ ] **Step 1: Extend the failing test.** In `test_secret_files_are_never_cited_or_sent_to_the_extractor`, add to
  `secrets`: `".envrc"`, `"secrets/db.yaml"`, `"config/.env.d/app.conf"`. The asserts stay as they are (only
  `config/routing.yaml` is cited; no secret text reaches the extractor).

- [ ] **Step 2: Run it and see it fail** — the three new paths are cited.

- [ ] **Step 3: Implement**

```python
SECRET_NAMES = (".env*", "*.pem", "*.key", "id_*", "*secret*", "*.p12")


def excluded(path: str) -> bool:
    """validated-memory's own files and memory exports (citing them would be circular, design §4.4), and files
    that look like secrets or sit in a directory named like one: those are never cited, sent to the extractor
    or accepted as evidence."""
    parts = path.split("/")
    return (path.startswith(EXCLUDED_DIRS) or path in EXCLUDED_FILES or parts[-1] == "memory.html"
            or any(fnmatch.fnmatchcase(part.lower(), pattern) for part in parts for pattern in SECRET_NAMES))
```

In the apply loop's `finally`, replace `detail["error"] = str(failure) or type(failure).__name__` by
`detail["error"] = _message(failure)` and add:

```python
def _message(exc: BaseException) -> str:
    """An error for the batch event without psycopg's DETAIL, which can quote a row's values."""
    diag = getattr(exc, "diag", None)
    return (getattr(diag, "message_primary", None) or str(exc)) or type(exc).__name__
```

No test for `_message`: no CLI path makes the apply fail with a DETAIL on demand; it is one expression.

- [ ] **Step 4: Docs.** README import paragraph: after the list of secret names add "or inside a directory named
  that way". `CONTEXT.md` **Import**: add "files named like secrets (`.env*`, `*.pem`, `*.key`, `id_*`,
  `*secret*`, `*.p12`), or under a directory named that way, are never read". Design §4 item 4: append the same
  list as a second sentence.
- [ ] **Step 5: Run `tests/test_import.py`** — all pass.
- [ ] **Step 6: Commit** — `fix: import excludes secret directories and .envrc; batch event omits DETAIL`

### Task 3: Evidence gaps of `!` commands and reads under the harness directory

**Files:**
- Modify: `src/val3/transcript.py` (`_VAL3_COMMAND`, `_MEMORY_PATH`, `_model_text_calls`)
- Modify: `src/val3/extractor_prompt.md` (evidence paragraph; version 2 → 3), `src/val3/extract.py`
  (`PROMPT_VERSION = 3`), `tests/test_extract.py:37` (`Prompt version: 3`)
- Test: `tests/test_save.py`

- [ ] **Step 1: Write the failing tests** (after `test_output_of_a_bang_val3_command_is_not_evidence`)

```python
def bang(env, command, output=QUOTE):
    t = env.transcript(user_messages=[f"<bash-input>{command}</bash-input>"])
    env.append(t, {"type": "user", "message": {"role": "user", "content":
                   f"<bash-stdout>{output}</bash-stdout><bash-stderr></bash-stderr>"}})
    return t


def test_val3_in_a_quoted_shell_and_reads_under_the_harness_directory_are_not_evidence(env):
    make_vault(env)
    for command in ("bash -c 'val3 show v#1'", 'sh -c "val3 recall invoices"',
                    "cd ~/.claude/projects/-project && cat memory/invoices.md",
                    "cat ~/.claude/projects/-project/abc.jsonl"):
        result = save(env, fact(), bang(env, command))
        assert result.returncode == 1 and "discarded" in result.stderr, command
        result = save(env, fact(), env.transcript(tool_calls=[("Bash", {"command": command}, OUTPUT)]))
        assert result.returncode == 1 and "discarded" in result.stderr, command


def test_bang_output_without_its_command_is_not_evidence(env):
    make_vault(env)
    t = env.transcript(tool_outputs=["unrelated"])
    env.append(t, {"type": "user", "message": {"role": "user", "content":
                   f"<bash-stdout>{QUOTE}</bash-stdout><bash-stderr></bash-stderr>"}})
    result = save(env, fact(), t)
    assert result.returncode == 1 and "discarded" in result.stderr
```

- [ ] **Step 2: Run them and see them fail.**

- [ ] **Step 3: Implement**

```python
# val3 itself prints stored quotes and candidates (show, review show), also inside `bash -c '…'`.
# The harness's projects directory holds text the model wrote: memory notes and transcripts. A `cd` there
# followed by a relative path names the directory in the same command.
_HARNESS_PROJECTS = re.compile(r"\.claude/projects/")
_VAL3_COMMAND = re.compile(r"(?:^|[\s;&|(`/'\"])val3[ \t]+(?:save|show|recall|review|tool|hook|vault)\b|-m[ \t]+val3\b")
```

Replace `_MEMORY_PATH` by `_HARNESS_PROJECTS` in `_shows_model_text`. In `_model_text_calls`:

```python
            elif _bash_output(content) is not None:
                if typed is None or _shows_model_text(typed, typed, private):  # no command: cannot be trusted
                    ids.add(index)
                typed = None
```

Update the docstring: "…plus the record indexes of `!` command outputs that do or whose command is missing".

`extractor_prompt.md` line 1 → `Prompt version: 3.`; evidence rule: `from a "TOOL RESULT:" block` →
`from a "TOOL RESULT:" or "TOOL RESULT (! command typed by the user):" block`.

- [ ] **Step 4: Run `tests/test_save.py tests/test_extract.py`** — all pass.
- [ ] **Step 5: Commit** — `fix: quoted val3 commands, harness directory reads and orphan ! output are not evidence`

### Task 4: Review list — status wraps, the list refills, vector warnings stay visible

**Files:**
- Modify: `src/val3/tty.py` (`Walk`, `walk`, `_draw_list`, `_decide`)
- Modify: `src/val3/cli.py` (`_store_vectors` and its four callers)
- Test: `tests/test_tty.py`

**Interfaces:**
- Produces: `_store_vectors(conn, project) -> str | None` (the warning, without the `val3: warning: ` prefix);
  `_warn(message: str | None) -> None` prints it to stderr; `Walk.after_approval: Callable[[], str | None]`;
  `Walk.warnings: list[str]`; `Terminal(env, columns=COLS, rows=ROWS, **environ)`.

- [ ] **Step 1: Write the failing tests.** In `Terminal.__init__` add `rows=ROWS` and use it in the `TIOCSWINSZ`
  pack and `LINES`. Then:

```python
SHUT = {**CONFIRMED, "title": "North warehouse shut", "statement": "The north warehouse shut in March 2025."}


def test_a_confirmation_wraps_in_a_narrow_window(env):
    t = admitted_fact(env)
    save(env, CONFIRMED, t)
    save(env, SHUT, t)
    terminal = started(env, columns=30)   # "approve 2 marked? 2 not opened · y/n" needs two lines
    terminal.keys("*a")
    terminal.wait_for("opened")
    terminal.wait_for("y/n")
    terminal.keys("nqq")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr


def test_deciding_the_last_rows_refills_the_list_from_above(env):
    t = admitted_fact(env)
    for n in range(7):
        save(env, {**CONFIRMED, "title": f"North warehouse closed ({n})",
                   "statement": f"The north warehouse closed in March 2025 ({n})."}, t)
    terminal = started(env, rows=8)       # body: 8 - 2 - 1 status line = 5 rows
    terminal.keys("\x1bOF")               # End (xterm keypad mode): cursor on #7, rows #3..#7 shown
    terminal.keys("a")
    terminal.wait_for("y/n")
    terminal.keys("y")
    terminal.wait_for("1 approved")
    frame = terminal.screen().rsplit("review · ", 1)[1]
    assert "#2 " in frame                 # top moved up: #2..#6, no blank row
    terminal.keys("q")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr


def test_a_vector_warning_is_shown_and_repeated_once_after_the_walk(env, embedder):
    t = admitted_fact(env)
    env.config(embedding={"url": embedder.url})
    save(env, CONFIRMED, t)
    save(env, SHUT, t)
    embedder.status = 500
    terminal = started(env)
    terminal.keys("a")
    terminal.wait_for("y/n")
    terminal.keys("y")
    terminal.wait_for("approved; warning:")    # on the status line while the walk goes on
    terminal.keys("a")
    terminal.wait_for("y/n")
    terminal.keys("y")
    code, _, stderr = terminal.finish()        # both decided: the walk ends
    assert code == 0, stderr
    assert stderr.count("val3: warning: no vector stored") == 1   # printed once, after curses ended
```

If the `End` escape is not read as `KEY_END` under `TERM=xterm`, try `"\x1b[F"`.

- [ ] **Step 2: Run them and see them fail.**

- [ ] **Step 3: Implement.** `cli.py`:

```python
def _store_vectors(conn, project) -> str | None:
    """After an admission: embed one batch of what lacks a vector, newest first, so the admitted entry is in it.

    A failure is returned as a warning, even a lost connection: the admission already printed stands and
    `val3 embed` and the drainer store the rest."""
    if project.embedding is None:
        return None
    try:
        store_vectors(conn, project, ADMISSION_TIMEOUT, limit=VECTOR_BATCH)
    except (EmbeddingError, psycopg.Error) as exc:
        return f"no vector stored ({exc}); run `val3 embed` later"
    return None


def _warn(message: str | None) -> None:
    if message:
        print(f"val3: warning: {message}", file=sys.stderr)
```

The three non-TTY callers become `_warn(_store_vectors(conn, project))`; the walk keeps
`lambda: _store_vectors(conn, project)`.

`tty.py`: `import textwrap`; `Walk.after_approval: Callable[[], str | None]`, new field `warnings: list[str] =
field(default_factory=list)`. In `_decide`, `if (warning := w.after_approval()): w.warnings.append(warning)`, and
after the loop append `f"; warning: {w.warnings[-1]}"` to `w.status` when this batch added one. In `walk`, every
place that prints `_summary(w)` calls `_report(w)` instead:

```python
def _report(w: Walk) -> None:
    print(_summary(w))
    for message in dict.fromkeys(w.warnings):  # once each, in order
        print(f"val3: warning: {message}", file=sys.stderr)
```

`_draw_list`:

```python
    height, width = screen.getmaxyx()
    status = textwrap.wrap(w.status, max(width - 1, 10)) or [""]
    body = max(height - 2 - len(status), 1)
    w.cursor = max(0, min(w.cursor, len(w.rows) - 1))
    w.top = max(0, min(max(w.top, w.cursor - body + 1), w.cursor, len(w.rows) - body))
    ...
    attr = w.attrs["warning"] if w.status.endswith("y/n") else 0
    for i, line in enumerate(status):
        _put(screen, height - 1 - len(status) + i, 0, line, attr)
```

- [ ] **Step 4: Run `tests/test_tty.py tests/test_review.py tests/test_save.py tests/test_embed.py`** — all pass.
- [ ] **Step 5: Commit** — `fix: review list wraps its status, refills from above and keeps vector warnings`

### Task 5: Card keeps its place on a width change; resize and narrow-window tests

**Files:**
- Modify: `src/val3/tty.py` (`_card`)
- Test: `tests/test_tty.py` (`Terminal.resize`, `Terminal.settle`, an option to leave `LINES`/`COLUMNS` unset)

ncurses takes the size from `LINES`/`COLUMNS` when they are set and ignores the window size then; the resize
test starts without them.

- [ ] **Step 1: Test helpers and failing tests.** `Terminal.__init__(self, env, columns=COLS, rows=ROWS,
  sized_by_env=True, **environ)`: when `sized_by_env` is false, `LINES` and `COLUMNS` are left out of the child's
  environment. Add:

```python
    def resize(self, rows, columns):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.process.send_signal(signal.SIGWINCH)

    def settle(self, quiet=0.5):
        """Read until nothing arrives for `quiet` seconds; later waits start after everything shown so far."""
        while select.select([self.master], [], [], quiet)[0] and self._read(0):
            pass
        self.seen = len(self.screen())
```

```python
LONG = "\n".join(f"line {number} of a long statement" for number in range(1, 61)) + "\nlast line"


def test_a_card_keeps_its_place_when_the_window_narrows(env):
    t = admitted_fact(env)
    save(env, {**CONFIRMED, "statement": LONG}, t)
    terminal = started(env, sized_by_env=False)
    terminal.keys("\r")
    terminal.wait_for("line 20 of a long statement")
    terminal.keys("j" * 70)
    terminal.wait_for("last line")
    terminal.settle()
    terminal.resize(ROWS, 60)
    terminal.wait_for("review #1 ·")      # redrawn at 60 columns
    terminal.wait_for("last line")        # still at the end, not back at the top
    terminal.keys("qq")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr


def test_a_card_in_a_narrow_window_wraps_and_quits_cleanly(env):
    t = admitted_fact(env)
    save(env, {**CONFIRMED, "statement": "The north warehouse closed in March 2025 " + "and stays closed " * 6}, t)
    terminal = started(env, columns=30)
    terminal.keys("\r")
    terminal.wait_for("review #1")
    terminal.wait_for("stays closed")
    terminal.keys("qq")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr
    assert "Traceback" not in stderr
```

- [ ] **Step 2: Run them; the first fails** (the card goes back to its top).

- [ ] **Step 3: Implement** in `_card`:

```python
        if shown != (row.id, width):
            before = len(lines)
            lines = _layout(review_card(w.conn, w.project.home, row.id), max(width - 1, 1), w.attrs)
            same = shown is not None and shown[0] == row.id and before
            top = round(top * len(lines) / before) if same else 0  # a width change keeps the place
            shown = (row.id, width)
```

- [ ] **Step 4: Run `tests/test_tty.py`** — all pass.
- [ ] **Step 5: Commit** — `fix: a review card keeps its place when the window width changes`

### Task 6: Ctrl-C during a batch

**Files:**
- Test: `tests/test_tty.py`; modify `src/val3/tty.py` (`walk`) only if the test fails.

- [ ] **Step 1: Write the test**

```python
def test_ctrl_c_during_a_batch_reports_what_was_decided(env, embedder):
    t = admitted_fact(env)
    env.config(embedding={"url": embedder.url})
    for n in range(4):
        save(env, {**CONFIRMED, "title": f"North warehouse closed ({n})",
                   "statement": f"The north warehouse closed in March 2025 ({n})."}, t)
    embedder.delay = 1.0                  # each approval then waits about a second for its vector
    terminal = started(env)
    terminal.keys("*a")
    terminal.wait_for("y/n")
    terminal.keys("y")
    terminal.wait_for("deciding 4…")
    time.sleep(1.5)
    terminal.process.send_signal(signal.SIGINT)
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert "Traceback" not in stderr
    approved = sum(decision == "approved" for _, decision in decisions(env))
    assert 0 < approved < 4
    assert summary(screen) == f"{approved} approved, 0 rejected, {4 - approved} skipped, 0 failed"
```

- [ ] **Step 2: Run it.** If it passes, commit the test alone. If the query for new pending items fails after
  the interrupt (a connection left mid-query), the fix is in `walk`: on `KeyboardInterrupt`, call `_report(w)`
  and return without that query.
- [ ] **Step 3: Run `tests/test_tty.py`** — all pass.
- [ ] **Step 4: Commit** — `test: Ctrl-C during a batch leaves the summary equal to the decisions` (or `fix:` with
  the change).

### Task 7: Close

- [ ] **Step 1:** full suite: `.venv/bin/pytest -q` — record the count.
- [ ] **Step 2:** `TODO.md`: remove the plan 5, plan 3b and plan 3 minor items; add `[x] Plan 6 — cleanup`.
  `SESSION.md`: dated entry with the suite count and anything left open.
- [ ] **Step 3:** commit `docs: plan 6 closed`; merge `--no-ff` into `main`; rerun the suite on `main`; scan the
  tracked files for secrets and internal names; push; delete the branch.
