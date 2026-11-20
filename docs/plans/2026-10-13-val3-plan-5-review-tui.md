# Full-screen terminal review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `val3 review --tty` becomes a full-screen `curses` list where a person marks items, opens styled cards and decides the marked items after a confirmation.

**Architecture:** `src/val3/tty.py` is rewritten: `walk()` keeps its signature and runs a `curses.wrapper` with a list screen and a card screen over one `Walk` state object; decisions still go through `writer.decide()` and the `after_approval` callback. `cli.py` adds a terminal check. Tests run the CLI inside a pseudo-terminal and assert on the screen text (escape sequences stripped), the exit code and database rows.

**Tech Stack:** Python 3.11+ standard library (`curses`, `pty`, `termios`, `fcntl`, `select`), psycopg 3, pytest.

**Spec:** `docs/design/2026-10-13-review-tui-design.md` (ADR 0004).

## Global Constraints

- Runtime: Python 3.11+, standard library plus one PostgreSQL driver; no new dependency, runtime or development.
- Everything in the repository in English; no internal project, host or person names.
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error.
- Tests drive the CLI as a subprocess and never import package internals (`from test_import import …` is a test helper, allowed).
- Vault text is printed only through `views.safe()`; style is chosen from val3's own prefixes after escaping.
- Conventional Commits; branch `feature/review-tui`.
- Run tests from the worktree with its own venv: `.venv/bin/pytest` (the main checkout's `val3` serves live hooks and must not change).

## Review Focus

1. A window narrower than a row (60 columns): rows, the status line and the footer are cut, nothing raises `curses.error` — `_put()` clips to the width; Task 1 test `test_a_narrow_window_cuts_rows`.
2. A long status (several failures) is cut to the width, not wrapped over the footer — same `_put()` clipping; the notice-reject test produces a status longer than the window.
3. A card longer than the window scrolls and the last line is reachable — Task 2 test `test_a_long_card_scrolls_to_its_end`.
4. Deciding the last listed item ends the walk without a further key — Task 1 `test_notices_created_during_the_walk_are_reported` exits after `y` alone.
5. A failed item stays listed and is not counted twice when later decided — Task 1 `test_rejecting_a_notice_fails_and_it_stays_listed`.

---

### Task 1: List screen, confirmation and terminal check

**Files:**
- Modify: `src/val3/tty.py` (whole file rewritten)
- Modify: `src/val3/cli.py:61` (help text), `src/val3/cli.py:244-253` (terminal check)
- Test: `tests/test_tty.py` (whole file rewritten)

**Interfaces:**
- Consumes: `writer.decide(conn, project, review_id, approve, decided_by) -> str` (raises `Val3Error` when refused), `writer.NOTICES`, `views.safe(text) -> str`, `views.review_card(conn, vault, review_id) -> str`, `db.vsql(vault, sql)`.
- Produces: `tty.walk(conn, project, decided_by: str, after_approval: Callable[[], None]) -> None` (unchanged signature); `tty.pending_ids(conn, vault) -> list[int]` (unchanged); `Walk` state with fields `rows: list[Row]`, `marked: set[int]`, `opened: set[int]`, `failed: set[int]`, `cursor: int`, `top: int`, `status: str`, `attrs: dict[str, int]`; `Row(id, kind, title, evidence, unsupported)`; `_put(screen, y, x, text, attr=0)`; `_decide(w, targets: list[int], approve: bool) -> None`; `_draw_list(screen, w) -> int` (returns the body height). Task 2 adds `_card(screen, w, index)` and calls it from the list's Enter key.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_tty.py` with:

```python
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

from helpers import OUTPUT, TOOL, fact, make_vault, recipe, save
from test_import import apply, ready_manifest

ROWS, COLS = 30, 120
ANSI = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|[()][0-9A-Za-z]|[=>78M])")
SUMMARY = re.compile(r"\d+ approved, \d+ rejected, \d+ skipped, \d+ failed")

CONFIRMED = fact(title="North warehouse closed", statement="The north warehouse closed in March 2025.",
                 evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}])
NEWER = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.")


class Terminal:
    """`val3 review --tty` in a pseudo-terminal of ROWS x COLS: keys in, screen text out."""

    def __init__(self, env, columns=COLS, **environ):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, columns, 0, 0))
        self.process = subprocess.Popen(
            [sys.executable, "-m", "val3", "review", "--tty"], cwd=env.project, stdin=slave, stdout=slave,
            stderr=subprocess.PIPE,
            env={**env.environ, "TERM": "xterm", "LANG": "C.UTF-8", "LINES": str(ROWS), "COLUMNS": str(columns),
                 **environ})
        os.close(slave)
        self.raw, self.seen = b"", 0  # `seen`: position in screen() after the previous wait's match

    def _read(self, timeout) -> bool:
        """Add what the screen received within `timeout`; False once the terminal is closed."""
        if not select.select([self.master], [], [], timeout)[0]:
            return True
        try:
            data = os.read(self.master, 65536)
        except OSError:  # EIO: every writer of the terminal has exited
            return False
        self.raw += data
        return bool(data)

    def screen(self) -> str:
        return ANSI.sub("", self.raw.decode("utf-8", "replace"))

    def wait_for(self, text, timeout=30):
        """Read until `text` is on the screen after the previous wait's match; waits go in screen order."""
        deadline = time.monotonic() + timeout
        while (found := self.screen().find(text, self.seen)) < 0:
            assert time.monotonic() < deadline and self._read(0.2), (
                f"{text!r} not shown; screen: {self.screen()[self.seen:][-1500:]!r}")
        self.seen = found + len(text)

    def keys(self, text):
        os.write(self.master, text.encode())

    def finish(self, timeout=30):
        """Wait for the exit: the exit code, everything the screen received and stderr."""
        deadline = time.monotonic() + timeout
        while self._read(0.2):
            assert time.monotonic() < deadline, f"review --tty did not exit; screen: {self.screen()[-1500:]!r}"
        code = self.process.wait(timeout=timeout)
        os.close(self.master)
        return code, self.screen(), self.process.stderr.read().decode()


def started(env, **options):
    """A walk whose first list screen is drawn (the footer, drawn last, starts with `↑↓ move`)."""
    terminal = Terminal(env, **options)
    terminal.wait_for("↑↓ move")
    return terminal


def summary(screen):
    return SUMMARY.findall(screen)[-1]


def admitted_fact(env):
    make_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT], user_messages=["Yes: the north warehouse closed in March 2025."])
    assert save(env, fact(), transcript).stdout.strip() == f"admitted {env.vault}#1"
    return transcript


def decisions(env):
    return env.sql(f"SELECT review_id, decision FROM {env.vault}.review_decision ORDER BY review_id")


def test_marked_items_are_approved_together_after_confirmation(env):
    t = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), t)
    save(env, TOOL, t)
    save(env, CONFIRMED, t)
    terminal = started(env)
    screen = terminal.screen()
    assert "3 pending" in screen and "0 marked" in screen
    assert "#1" in screen and "Count the invoices of a month" in screen and "✗ no evidence" in screen
    assert "North warehouse closed" in screen and "1 evidence" in screen
    terminal.keys(" j ")  # mark review #1, move down, mark review #2
    terminal.wait_for("2 marked")
    terminal.keys("a")
    terminal.wait_for("approve 2 marked? 2 without evidence, 2 not opened · y/n")
    terminal.keys("y")
    terminal.wait_for("2 approved")
    terminal.keys("q")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "2 approved, 0 rejected, 1 skipped, 0 failed"
    assert decisions(env) == [(1, "approved"), (2, "approved")]
    assert env.sql(f"SELECT action, review_id FROM {env.vault}.event WHERE action IN ('approved', 'rejected') "
                   "ORDER BY id") == [("approved", 1), ("approved", 2)]


def test_star_marks_every_row_and_clears_them(env):
    t = admitted_fact(env)
    save(env, TOOL, t)
    save(env, CONFIRMED, t)
    terminal = started(env)
    terminal.keys("*")
    terminal.wait_for("2 marked")
    terminal.keys("*")
    terminal.wait_for("0 marked")
    terminal.keys("q")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert decisions(env) == []


def test_a_cancelled_confirmation_decides_nothing(env):
    t = admitted_fact(env)
    save(env, CONFIRMED, t)
    terminal = started(env)
    terminal.keys("r")  # nothing marked: the current row
    terminal.wait_for("reject review #1? 1 not opened · y/n")
    terminal.keys("nq")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "0 approved, 0 rejected, 1 skipped, 0 failed"
    assert decisions(env) == []


def test_imported_items_show_their_evidence_and_the_confirmation_counts_the_unsupported(env):
    assert apply(env, ready_manifest(env)).returncode == 0
    terminal = started(env)
    screen = terminal.screen()
    assert "✓ snapshot" in screen and "✗ unsupported" in screen
    terminal.keys("*a")
    terminal.wait_for("approve 2 marked? 1 without evidence, 2 not opened · y/n")
    terminal.keys("nq")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr
    assert decisions(env) == []


def test_rejecting_a_notice_fails_and_it_stays_listed(env):
    t = admitted_fact(env)
    save(env, {**NEWER, "supersedes": f"{env.vault}#1"}, t)  # review #1: supersession notice
    terminal = started(env)
    assert "supersession (notice)" in terminal.screen()
    terminal.keys("r")
    terminal.wait_for("y/n")
    terminal.keys("y")
    terminal.wait_for("0 rejected, 1 failed: review #1: review #1 is a notice and can only be approved")
    terminal.keys("a")
    terminal.wait_for("approve review #1?")
    terminal.keys("y")  # the last item: the walk ends
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "1 approved, 0 rejected, 0 skipped, 0 failed"
    assert decisions(env) == [(1, "approved")]


def test_an_item_decided_elsewhere_meanwhile_fails_with_the_reason(env):
    t = admitted_fact(env)
    save(env, CONFIRMED, t)
    terminal = started(env)
    assert env.run("review", "approve", "1").returncode == 0
    terminal.keys("a")
    terminal.wait_for("y/n")
    terminal.keys("y")
    terminal.wait_for("0 approved, 1 failed: review #1: review #1 was already approved")
    terminal.keys("q")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "0 approved, 0 rejected, 0 skipped, 1 failed"


def test_notices_created_during_the_walk_are_reported(env):
    t = admitted_fact(env)
    save(env, {**CONFIRMED, "supersedes": f"{env.vault}#1"}, t)
    terminal = started(env)
    terminal.keys("a")
    terminal.wait_for("y/n")
    terminal.keys("y")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "1 approved, 0 rejected, 0 skipped, 0 failed"
    assert "1 new items pending: run `val3 review --tty` again" in screen


def test_a_database_error_restores_the_terminal_and_exits_1(env):
    t = admitted_fact(env)
    save(env, CONFIRMED, t)
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    assert env.run("vault", "grant", env.vault, env.writer, "--write").returncode == 0
    env.sql(f"REVOKE INSERT ON {env.vault}.review_decision FROM {env.writer}")
    env.config(service="val3writer")
    terminal = started(env)
    terminal.keys("a")
    terminal.wait_for("y/n")
    terminal.keys("y")
    code, screen, stderr = terminal.finish()
    assert code == 1
    assert summary(screen) == "0 approved, 0 rejected, 1 skipped, 0 failed"
    assert "val3: error: database: permission denied" in stderr


def test_ctrl_c_quits_with_the_summary(env):
    t = admitted_fact(env)
    save(env, CONFIRMED, t)
    terminal = started(env)
    terminal.process.send_signal(signal.SIGINT)
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "0 approved, 0 rejected, 1 skipped, 0 failed"
    assert "Traceback" not in stderr


def test_a_narrow_window_cuts_rows(env):
    t = admitted_fact(env)
    save(env, {**CONFIRMED, "title": "North warehouse closed " + "and stays closed " * 10}, t)
    terminal = started(env, columns=60)
    terminal.keys("r")
    terminal.wait_for("reject review #1?")
    terminal.keys("nq")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert "Traceback" not in stderr
    assert "and stays closed " * 10 not in screen


def test_control_characters_in_a_title_are_shown_escaped_on_the_list(env):
    t = admitted_fact(env)
    save(env, {**CONFIRMED, "title": "Clear \x1b[2J and \x1b]0;owned\x07 here"}, t)
    terminal = started(env)
    terminal.keys("q")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    raw = terminal.raw.decode("utf-8", "replace")
    assert "\x1b]0;owned" not in raw and "\x1b[2J and" not in raw
    assert "Clear \\x1b[2J and \\x1b]0;owned\\x07 here" in screen


def test_empty_review_list(env):
    make_vault(env)
    code, screen, stderr = Terminal(env).finish()
    assert code == 0, stderr
    assert screen.strip() == "no pending review items"


def test_a_walk_needs_a_terminal(env):
    make_vault(env)
    result = env.run("review", "--tty", input="")
    assert result.returncode == 2
    assert "val3: error: val3 review --tty needs a terminal" in result.stderr


def test_agent_sessions_cannot_walk_the_review_list(env):
    make_vault(env)
    result = env.run("review", "--tty", input="", environ={"CLAUDECODE": "1"})
    assert result.returncode == 1
    assert "outside the agent session" in result.stderr


def test_tty_takes_no_action(env):
    make_vault(env)
    result = env.run("review", "--tty", "show", "1")
    assert result.returncode == 2
    assert "--tty takes no action" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tty.py -q`
Expected: FAIL — most tests time out in `wait_for("↑↓ move")` (the line dialogue never draws a footer); `test_a_walk_needs_a_terminal` fails on the exit code (the dialogue runs on a pipe).

- [ ] **Step 3: Add the terminal check to the CLI**

In `src/val3/cli.py`, change the `--tty` help (line 61):

```python
    review.add_argument("--tty", action="store_true", help="mark and decide the pending items in a full-screen list")
```

and `cmd_review` (lines 244-253) to:

```python
def cmd_review(args) -> int | None:
    if args.tty and args.review_command:
        print("val3: error: --tty takes no action", file=sys.stderr)
        return 2
    if args.tty or args.review_command in ("approve", "reject"):
        _person_only()
    if args.tty and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("val3: error: val3 review --tty needs a terminal", file=sys.stderr)
        return 2
    project = load_project(Path.cwd())
```

(the rest of the function unchanged).

- [ ] **Step 4: Rewrite `src/val3/tty.py`**

```python
"""`val3 review --tty`: a person marks the pending review items in a full-screen list and decides them."""
import curses
import locale
import os
from dataclasses import dataclass, field
from typing import Callable

import psycopg

from val3 import Val3Error
from val3.db import vsql
from val3.views import safe
from val3.writer import NOTICES, decide

LIST_KEYS = "↑↓ move · space mark · * all · enter open · a approve · r reject · q quit"
KIND_WIDTH, EVIDENCE_WIDTH = 24, 16


@dataclass
class Row:
    id: int
    kind: str
    title: str
    evidence: str
    unsupported: bool  # a candidate with no evidence: approving it rests on the approver's word


@dataclass
class Walk:
    conn: object
    project: object
    decided_by: str
    after_approval: Callable[[], None]
    rows: list[Row]
    marked: set[int] = field(default_factory=set)
    opened: set[int] = field(default_factory=set)
    failed: set[int] = field(default_factory=set)
    approved: int = 0
    rejected: int = 0
    cursor: int = 0
    top: int = 0
    status: str = ""
    attrs: dict[str, int] = field(default_factory=dict)


def pending_ids(conn, vault: str) -> list[int]:
    return [review_id for (review_id,) in conn.execute(vsql(vault, """
        SELECT r.id FROM {v}.review_item r
        WHERE NOT EXISTS (SELECT 1 FROM {v}.review_decision d WHERE d.review_id = r.id)
        ORDER BY r.id"""))]


def walk(conn, project, decided_by: str, after_approval: Callable[[], None]) -> None:
    """Decide the items pending at start. A refused decision is counted and the item stays listed; a database
    error restores the terminal, prints the summary and propagates (exit 1); Ctrl-C quits like `q`."""
    vault = project.home
    ids = pending_ids(conn, vault)
    if not ids:
        print("no pending review items")
        return
    w = Walk(conn, project, decided_by, after_approval, list_rows(conn, vault, ids))
    locale.setlocale(locale.LC_ALL, "")
    try:
        curses.wrapper(_list, w)
    except KeyboardInterrupt:
        pass
    except psycopg.Error:
        print(_summary(w))
        raise
    print(_summary(w))
    new = len(set(pending_ids(conn, vault)) - set(ids))
    if new:
        print(f"{new} new items pending: run `val3 review --tty` again")


def list_rows(conn, vault: str, ids: list[int]) -> list[Row]:
    """One row per item; imported items classified as the README's measurement query does."""
    rows = []
    for review_id, reason, payload in conn.execute(vsql(vault, """
            SELECT id, reason, payload FROM {v}.review_item WHERE id = ANY(%s) ORDER BY id"""), (ids,)):
        title = safe(str(payload.get("title", "")))
        if reason in NOTICES:
            rows.append(Row(review_id, f"{reason} (notice)", title, "", False))
            continue
        evidence = payload.get("evidence") or []
        if reason == "import":
            checked = (payload.get("import") or {}).get("evidence") or []
            label = ("✗ unsupported" if not evidence else
                     "✓ snapshot" if any(item.get("status") == "verified" and item.get("at_snapshot")
                                         for item in checked) else "~ changed")
        else:
            label = f"{len(evidence)} evidence" if evidence else "✗ no evidence"
        rows.append(Row(review_id, safe(str(payload.get("kind", reason))), title, label, not evidence))
    return rows


def _attrs() -> dict[str, int]:
    """Attributes by role; colours unless NO_COLOR is set or the terminal has none."""
    colour = curses.has_colors() and not os.environ.get("NO_COLOR")
    if colour:
        curses.use_default_colors()
        for pair, color in ((1, curses.COLOR_CYAN), (2, curses.COLOR_GREEN), (3, curses.COLOR_YELLOW)):
            curses.init_pair(pair, color, -1)
    pair = curses.color_pair if colour else (lambda number: 0)
    return {"bold": curses.A_BOLD, "dim": curses.A_DIM, "block": curses.A_REVERSE | curses.A_BOLD,
            "label": pair(1) or curses.A_BOLD, "quote": pair(2), "warning": pair(3) | curses.A_BOLD}


def _put(screen, y: int, x: int, text: str, attr: int = 0) -> None:
    """Write within the window, cut to its width; curses refuses the bottom-right cell, never needed here."""
    height, width = screen.getmaxyx()
    if 0 <= y < height and 0 <= x < width - 1:
        try:
            screen.addnstr(y, x, text, width - 1 - x, attr)
        except curses.error:
            pass


def _draw_list(screen, w: Walk) -> int:
    """Redraw the whole list (a full repaint keeps every line's text contiguous); return the body height."""
    height, width = screen.getmaxyx()
    body = max(height - 3, 1)
    w.cursor = max(0, min(w.cursor, len(w.rows) - 1))
    w.top = min(max(w.top, w.cursor - body + 1), w.cursor)
    screen.clear()
    _put(screen, 0, 0, f"review · {w.project.home} · {len(w.rows)} pending", w.attrs["bold"])
    marks = f"{len(w.marked)} marked"
    _put(screen, 0, max(width - len(marks) - 2, 0), marks)
    for y, row in enumerate(w.rows[w.top: w.top + body], start=1):
        index = w.top + y - 1
        head = (f"{'>' if index == w.cursor else ' '} [{'x' if row.id in w.marked else ' '}] "
                f"{'·' if row.id in w.opened else ' '} #{row.id:<5} {row.kind:<{KIND_WIDTH}} ")
        # ponytail: one column per character; wide (CJK) titles overflow the evidence column
        room = max(width - 1 - len(head) - EVIDENCE_WIDTH, 10)
        title = row.title if len(row.title) <= room else row.title[: room - 1] + "…"
        _put(screen, y, 0, f"{head}{title:<{room}}  {row.evidence}", curses.A_REVERSE if index == w.cursor else 0)
    _put(screen, height - 2, 0, w.status, w.attrs["warning"] if w.status.endswith("y/n") else 0)
    _put(screen, height - 1, 0, LIST_KEYS, w.attrs["dim"])
    screen.refresh()
    return body


def _list(screen, w: Walk) -> None:
    w.attrs = _attrs()
    curses.set_escdelay(25)
    try:
        curses.curs_set(0)
    except curses.error:  # a terminal that cannot hide the cursor
        pass
    while w.rows:
        body = _draw_list(screen, w)
        key = screen.getch()
        if key in (ord("q"), -1):  # -1: the terminal is gone
            return
        if key in (curses.KEY_DOWN, ord("j")):
            w.cursor += 1
        elif key in (curses.KEY_UP, ord("k")):
            w.cursor -= 1
        elif key == curses.KEY_NPAGE:
            w.cursor += body
        elif key == curses.KEY_PPAGE:
            w.cursor -= body
        elif key == curses.KEY_HOME:
            w.cursor = 0
        elif key == curses.KEY_END:
            w.cursor = len(w.rows) - 1
        elif key == ord(" "):
            w.marked ^= {w.rows[w.cursor].id}
        elif key == ord("*"):
            every = {row.id for row in w.rows}
            w.marked = set() if every <= w.marked else every
        elif key in (ord("a"), ord("r")):
            targets = sorted(w.marked) or [w.rows[w.cursor].id]
            if _confirm(screen, w, targets, key == ord("a")):
                _decide(w, targets, key == ord("a"))


def _confirm(screen, w: Walk, targets: list[int], approve: bool) -> bool:
    """Ask on the status line, naming how many targets carry no evidence and how many were not opened."""
    chosen = [row for row in w.rows if row.id in targets]
    notes = [f"{count} {label}" for count, label in ((sum(row.unsupported for row in chosen), "without evidence"),
                                                     (sum(row.id not in w.opened for row in chosen), "not opened"))
             if count]
    what = f"{len(targets)} marked" if w.marked else f"review #{targets[0]}"
    w.status = f"{'approve' if approve else 'reject'} {what}?{' ' + ', '.join(notes) if notes else ''} · y/n"
    _draw_list(screen, w)
    answer = screen.getch()
    w.status = ""
    return answer == ord("y")


def _decide(w: Walk, targets: list[int], approve: bool) -> None:
    """Decide each target in its own transaction; a refusal is counted and the item stays listed."""
    done, failures = 0, []
    for review_id in targets:
        try:
            decide(w.conn, w.project, review_id, approve, w.decided_by)
        except Val3Error as exc:  # already decided, a cited entry superseded meanwhile, a notice rejected
            w.failed.add(review_id)
            failures.append(f"review #{review_id}: {safe(str(exc))}")
            continue
        done += 1
        w.failed.discard(review_id)
        w.rows = [row for row in w.rows if row.id != review_id]
        if approve:
            w.approved += 1
            w.after_approval()
        else:
            w.rejected += 1
    w.marked -= set(targets)
    w.status = (f"{done} {'approved' if approve else 'rejected'}"
                + (f", {len(failures)} failed: {'; '.join(failures)}" if failures else ""))


def _summary(w: Walk) -> str:
    skipped = sum(row.id not in w.failed for row in w.rows)
    return f"{w.approved} approved, {w.rejected} rejected, {skipped} skipped, {len(w.failed)} failed"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_tty.py -q`
Expected: PASS (15 tests).

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (the previous 272 minus the 11 old `test_tty.py` tests plus the 15 new ones: 276).

- [ ] **Step 7: Commit**

```bash
git add src/val3/tty.py src/val3/cli.py tests/test_tty.py
git commit -m "feat: val3 review --tty is a full-screen list with marks and a confirmation"
```

---

### Task 2: Card screen with styles

**Files:**
- Modify: `src/val3/tty.py` (add `_card`, `styled`, `_layout`, `CARD_KEYS`, the style patterns; bind Enter in `_list`)
- Test: `tests/test_tty.py` (append three tests)

**Interfaces:**
- Consumes: from Task 1 `Walk`, `Row`, `_put`, `_decide`, `w.attrs` keys `bold`, `dim`, `block`, `label`, `quote`, `warning`; `views.review_card(conn, vault, review_id) -> str` (already escaped).
- Produces: `_card(screen, w: Walk, index: int) -> None`; `styled(line: str, first: bool, attrs: dict[str, int]) -> list[tuple[str, int]]`; `_layout(card: str, width: int, attrs) -> list[list[tuple[str, int]]]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tty.py`:

```python
def test_a_card_is_read_and_decided_from_its_screen(env):
    t = admitted_fact(env)
    save(env, TOOL, t)
    save(env, CONFIRMED, t)
    terminal = started(env)
    terminal.keys("\r")
    terminal.wait_for("review #1 · 1/2 · [ ]")
    terminal.wait_for("[tool]  code-graph")
    terminal.wait_for("tool: cli, priority 5")
    terminal.keys("n")
    terminal.wait_for("review #2 · 2/2 · [ ]")
    terminal.wait_for("> the north warehouse closed in March 2025")
    terminal.keys("r")  # this item alone, no confirmation; back to the list
    terminal.wait_for("1 rejected")
    terminal.keys(" a")  # review #1 was opened: only its missing evidence is named
    terminal.wait_for("approve 1 marked? 1 without evidence · y/n")
    terminal.keys("nq")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "0 approved, 1 rejected, 1 skipped, 0 failed"
    assert decisions(env) == [(2, "rejected")]


def test_a_long_card_scrolls_to_its_end(env):
    t = admitted_fact(env)
    statement = "\n".join(f"line {number} of a long statement" for number in range(1, 61)) + "\nlast line"
    save(env, {**CONFIRMED, "statement": statement}, t)
    terminal = started(env)
    terminal.keys("\r")
    terminal.wait_for("line 20 of a long statement")
    assert "last line" not in terminal.screen()
    terminal.keys("j" * 70)
    terminal.wait_for("last line")
    terminal.keys("q")
    terminal.wait_for("↑↓ move")  # back on the list
    terminal.keys("q")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr


def test_control_characters_in_a_title_are_shown_escaped_on_the_card(env):
    t = admitted_fact(env)
    save(env, {**CONFIRMED, "title": "Clear \x1b[2J and \x1b]0;owned\x07 here"}, t)
    terminal = started(env)
    terminal.keys("\r")
    terminal.wait_for("[fact]  Clear \\x1b[2J and \\x1b]0;owned\\x07 here")
    terminal.keys("qq")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr
    raw = terminal.raw.decode("utf-8", "replace")
    assert "\x1b]0;owned" not in raw and "\x1b[2J and" not in raw
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tty.py -q -k "card"`
Expected: FAIL — `wait_for("review #1 · 1/2 · [ ]")` times out (Enter does nothing yet).

- [ ] **Step 3: Implement the card screen**

In `src/val3/tty.py`, add `import re` to the imports, `review_card` to the `val3.views` import (`from val3.views import review_card, safe`), and after `LIST_KEYS`:

```python
CARD_KEYS = "↑↓ PgUp PgDn scroll · space mark · a approve · r reject · n next · p previous · q back"
LABEL = re.compile(r"(\s*)(evidence|steps|keywords|indexes|parameters|examples|origin|tool|environment|observed|"
                   r"supersedes):")
ENTRY = re.compile(r"\S+#\d+  \[|\[[a-z-]+\]  ")  # `vault#7  [fact]  title` or `[fact]  title`
BLOCKS = ("SOURCE ", "BEFORE ", "NOW ", "RECIPE ")
WARNINGS = ("no repository evidence", "not verified:", "SUPERSEDED by", "approving supersedes")
```

Add after `_summary`:

```python
def styled(line: str, first: bool, attrs: dict[str, int]) -> list[tuple[str, int]]:
    """One card line as (text, attribute) pieces, chosen from val3's own prefix; the text is already escaped.

    A statement line that starts like one of these prefixes gets the same style (design §3.3)."""
    body = line.lstrip()
    if first or ENTRY.match(line):
        return [(line, attrs["bold"])]
    if line.startswith(BLOCKS):
        return [(line, attrs["block"])]
    if body.startswith(WARNINGS):
        return [(line, attrs["warning"])]
    if body.startswith("> "):
        return [(line, attrs["quote"])]
    if body.startswith("reproduce: "):
        return [(line, attrs["dim"])]
    label = LABEL.match(line)
    if label:
        return [(label.group(1), 0), (label.group(2) + ":", attrs["label"]), (line[label.end():], 0)]
    return [(line, 0)]


def _layout(card: str, width: int, attrs: dict[str, int]) -> list[list[tuple[str, int]]]:
    """The card as screen lines of at most `width` characters; long lines wrap and keep their pieces' style."""
    lines = []
    for number, line in enumerate(card.splitlines()):
        current, used = [], 0
        for text, attr in styled(line, number == 0, attrs):
            while text:
                piece = text[: width - used]
                current.append((piece, attr))
                used += len(piece)
                text = text[len(piece):]
                if used == width:
                    lines.append(current)
                    current, used = [], 0
        if current or not line:
            lines.append(current)
    return lines


def _card(screen, w: Walk, index: int) -> None:
    """One item's card until the person goes back, decides it (alone, no confirmation) or the terminal is gone."""
    top, shown, lines = 0, None, []
    while True:
        row = w.rows[index]
        w.cursor = index
        w.opened.add(row.id)
        height, width = screen.getmaxyx()
        if shown != (row.id, width):
            lines, shown, top = _layout(review_card(w.conn, w.project.home, row.id), max(width - 1, 1), w.attrs), \
                (row.id, width), 0
        body = max(height - 2, 1)
        top = max(0, min(top, len(lines) - body))
        screen.clear()
        _put(screen, 0, 0, f"review #{row.id} · {index + 1}/{len(w.rows)} · [{'x' if row.id in w.marked else ' '}]",
             w.attrs["block"])
        for y, pieces in enumerate(lines[top: top + body], start=1):
            x = 0
            for text, attr in pieces:
                _put(screen, y, x, text, attr)
                x += len(text)
        _put(screen, height - 1, 0, CARD_KEYS, w.attrs["dim"])
        screen.refresh()
        key = screen.getch()
        if key in (ord("q"), 27, -1):
            return
        if key in (curses.KEY_DOWN, ord("j")):
            top += 1
        elif key in (curses.KEY_UP, ord("k")):
            top -= 1
        elif key == curses.KEY_NPAGE:
            top += body
        elif key == curses.KEY_PPAGE:
            top -= body
        elif key == ord(" "):
            w.marked ^= {row.id}
        elif key == ord("n"):
            index = min(index + 1, len(w.rows) - 1)
        elif key == ord("p"):
            index = max(index - 1, 0)
        elif key in (ord("a"), ord("r")):
            _decide(w, [row.id], key == ord("a"))
            return
```

The card's own header line takes row 0, so the card's first line (`review #… · reason …`) is the first body line, styled bold.

In `_list`, add before the `a`/`r` branch:

```python
        elif key in (10, 13, curses.KEY_ENTER):
            _card(screen, w, w.cursor)
```

and update `LIST_KEYS` stays as is (it already names `enter open`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_tty.py -q`
Expected: PASS (18 tests).

- [ ] **Step 5: Look at it**

Run in a real terminal (not through the agent tool, which has none): with a vault holding a few items, `val3 review --tty` from the worktree's venv; open a card of each kind and check the styles of design §3.3 are applied and nothing else is. Record what was looked at in the task report; if no terminal is available, say so.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: 279 passed.

- [ ] **Step 7: Commit**

```bash
git add src/val3/tty.py tests/test_tty.py
git commit -m "feat: review cards open full screen with styled labels, quotes and warnings"
```

---

### Task 3: Documentation

**Files:**
- Modify: `README.md` (Reviewing section, line ~145 command list, line ~208 import example comment)
- Modify: `docs/design/2026-10-13-review-tui-design.md` (§3.2 header, §5 case 9)
- Modify: `CONTEXT.md` only if it describes `review --tty` as one-by-one (check with `grep -n "tty" CONTEXT.md`)

- [ ] **Step 1: README**

Replace the `val3 review` line of the command list:

```markdown
- `val3 review` — pending review items; `val3 review --tty` marks and decides them in a full-screen list;
  `review show|approve|reject <id>`.
```

Replace the paragraph of the Reviewing section that starts with "`val3 review --tty` shows each pending item as a card and asks" (keep the following paragraphs) with:

```markdown
`val3 review --tty` lists the pending items in a full-screen view: mark, `#id`, kind, title and evidence
(`✓ snapshot`, `~ changed`, `✗ unsupported` for imported items; the number of evidence items otherwise).
`↑↓` move, `space` marks, `*` marks every row, `Enter` opens the card, `a` approves and `r` rejects the
marked items (or the current one) after a confirmation that names how many carry no evidence and how
many were not opened. On a card, `a` and `r` decide that item alone; `n` and `p` move to the next or
previous item; `q` goes back. Reproductions are shown, never run. It needs a terminal; `NO_COLOR`
turns colours off. Scripts use `review show|approve|reject`.
```

Run `grep -n "one by one\|tty" README.md` and fix any other sentence that still says one by one.

- [ ] **Step 2: Design doc**

In `docs/design/2026-10-13-review-tui-design.md` §3.2, replace the header bullet with:

```markdown
- Top line: `review #9 · 3/43 · [x]`; bottom line: the keys `↑↓ PgUp PgDn scroll · space mark · a approve ·
  r reject · n next · p previous · q back`.
```

and in §5 replace case 9 with:

```markdown
9. Ctrl-C (SIGINT): terminal restored, summary printed, exit 0. (End of input cannot be produced in a
   pseudo-terminal whose reader is the test itself; `getch()` returning -1 quits like `q`.)
```

and add after case 10:

```markdown
11. a window of 60 columns cuts rows without error;
12. a card longer than the window scrolls to its last line.
```

Change the Status line to: `Status: **approved by the owner 2026-10-13.** Replaces …` (rest unchanged).

- [ ] **Step 3: Check and commit**

Check `README.md`, the design and ADR 0004 for internal project, host or person names (CLAUDE.md).
Expected: none.

```bash
git add README.md docs/design/2026-10-13-review-tui-design.md CONTEXT.md
git commit -m "docs: full-screen review in the README and the design"
```
