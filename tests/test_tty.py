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

from helpers import OUTPUT, TOOL, before_0012, fact, legacy, old_vault, recipe, save
from test_import import adopter, answer, apply, dry_run, note_candidate, unit_candidate

ROWS, COLS = 30, 120
ANSI = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|[()][0-9A-Za-z]|[=>78M])")
SUMMARY = re.compile(r"\d+ approved, \d+ rejected, \d+ skipped, \d+ failed")

CONFIRMED = fact(title="North warehouse closed", statement="The north warehouse closed in March 2025.",
                 evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}])
NEWER = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.")
SHUT = {**CONFIRMED, "title": "North warehouse shut", "statement": "The north warehouse shut in March 2025."}


class Terminal:
    """`val3 review --tty` in a pseudo-terminal of ROWS x COLS: keys in, screen text out."""

    def __init__(self, env, columns=COLS, rows=ROWS, sized_by_env=True, **environ):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.process = subprocess.Popen(
            [sys.executable, "-m", "val3", "review", "--tty"], cwd=env.project, stdin=slave, stdout=slave,
            stderr=subprocess.PIPE,
            env={**env.environ, "TERM": "xterm", "LANG": "C.UTF-8",
                 **({"LINES": str(rows), "COLUMNS": str(columns)} if sized_by_env else {}), **environ})
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

    def resize(self, rows, columns):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        self.process.send_signal(signal.SIGWINCH)

    def settle(self, quiet=0.5):
        """Read until nothing arrives for `quiet` seconds; later waits start after everything shown so far."""
        while select.select([self.master], [], [], quiet)[0] and self._read(0):
            pass
        self.seen = len(self.screen())

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
    old_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT], user_messages=["Yes: the north warehouse closed in March 2025."])
    assert save(env, fact(), transcript).stdout.strip() == f"admitted {env.vault}#1"
    return transcript


def decisions(env):
    return env.sql(f"SELECT review_id, decision FROM {env.vault}.review_decision ORDER BY review_id")


def test_marked_items_are_approved_together_after_confirmation(env):
    t = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), t)
    save(env, TOOL, t)
    legacy(env, CONFIRMED)
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
    legacy(env, CONFIRMED)
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
    admitted_fact(env)
    legacy(env, CONFIRMED)
    terminal = started(env)
    terminal.keys("r")  # nothing marked: the current row
    terminal.wait_for("reject review #1? 1 not opened · y/n")
    terminal.keys("nq")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "0 approved, 0 rejected, 1 skipped, 0 failed"
    assert decisions(env) == []


def test_imported_items_show_their_evidence_and_the_confirmation_counts_the_unsupported(env):
    adopter(env)
    assert env.run("vault", "create", env.vault).returncode == 0
    before_0012(env)
    answer(env, 0, unit_candidate(evidence=[{"path": "config/routing.yaml", "quote": "subscribers: {}"},
                                            {"path": "config/routing.yaml", "quote": "no such line"}]))
    answer(env, 1, note_candidate(evidence=[{"path": "docs/adr/0001.md", "quote": "we never decided this at all"}]))
    assert dry_run(env).returncode == 0  # one quote failed on each: both wait for a person
    assert apply(env, env.root / "manifest.json").returncode == 0
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
    save(env, {**NEWER, "supersedes": f"{env.vault}#1"}, t)
    legacy(env, {"title": f"{env.vault}#2 supersedes {env.vault}#1"}, reason="supersession", entry_id=2)
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
    admitted_fact(env)
    legacy(env, CONFIRMED)
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


def test_items_created_during_the_walk_are_reported(env):
    admitted_fact(env)
    legacy(env, CONFIRMED)
    terminal = started(env)
    legacy(env, SHUT)
    terminal.keys("a")
    terminal.wait_for("y/n")
    terminal.keys("y")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "1 approved, 0 rejected, 0 skipped, 0 failed"
    assert "1 new items pending: run `val3 review --tty` again" in screen


def test_a_database_error_restores_the_terminal_and_exits_1(env):
    admitted_fact(env)
    legacy(env, CONFIRMED)
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
    admitted_fact(env)
    legacy(env, CONFIRMED)
    terminal = started(env)
    terminal.process.send_signal(signal.SIGINT)
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "0 approved, 0 rejected, 1 skipped, 0 failed"
    assert "Traceback" not in stderr


def test_a_narrow_window_cuts_rows(env):
    admitted_fact(env)
    legacy(env, {**CONFIRMED, "title": "North warehouse closed " + "and stays closed " * 10})
    terminal = started(env, columns=60)
    terminal.keys("r")
    terminal.wait_for("reject review #1?")
    terminal.keys("nq")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert "Traceback" not in stderr
    assert "and stays closed " * 10 not in screen


def test_control_characters_in_a_title_are_shown_escaped_on_the_list(env):
    admitted_fact(env)
    legacy(env, {**CONFIRMED, "title": "Clear \x1b[2J and \x1b]0;owned\x07 here"})
    terminal = started(env)
    terminal.keys("q")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    raw = terminal.raw.decode("utf-8", "replace")
    assert "\x1b]0;owned" not in raw and "\x1b[2J and" not in raw
    assert "Clear \\x1b[2J and \\x1b]0;owned\\x07 here" in screen


def test_empty_review_list(env):
    old_vault(env)
    code, screen, stderr = Terminal(env).finish()
    assert code == 0, stderr
    assert screen.strip() == "no pending review items"


def test_a_walk_needs_a_terminal(env):
    old_vault(env)
    result = env.run("review", "--tty", input="")
    assert result.returncode == 2
    assert "val3: error: val3 review --tty needs a terminal" in result.stderr


def test_agent_sessions_cannot_walk_the_review_list(env):
    old_vault(env)
    result = env.run("review", "--tty", input="", environ={"CLAUDECODE": "1"})
    assert result.returncode == 1
    assert "outside the agent session" in result.stderr


def test_tty_takes_no_action(env):
    old_vault(env)
    result = env.run("review", "--tty", "show", "1")
    assert result.returncode == 2
    assert "--tty takes no action" in result.stderr


def test_a_card_is_read_and_decided_from_its_screen(env):
    t = admitted_fact(env)
    save(env, TOOL, t)
    legacy(env, CONFIRMED)
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
    admitted_fact(env)
    statement = "\n".join(f"line {number} of a long statement" for number in range(1, 61)) + "\nlast line"
    legacy(env, {**CONFIRMED, "statement": statement})
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
    admitted_fact(env)
    legacy(env, {**CONFIRMED, "title": "Clear \x1b[2J and \x1b]0;owned\x07 here"})
    terminal = started(env)
    terminal.keys("\r")
    terminal.wait_for("[fact]  Clear \\x1b[2J and \\x1b]0;owned\\x07 here")
    terminal.keys("qq")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr
    raw = terminal.raw.decode("utf-8", "replace")
    assert "\x1b]0;owned" not in raw and "\x1b[2J and" not in raw


def test_a_tab_in_a_card_line_hides_no_text(env):
    admitted_fact(env)
    # two tabs take 14 columns: measured as two characters, the end of the first screen line wraps and the
    # second layout line overwrites it
    legacy(env, {**CONFIRMED, "statement": "Threshold:\t\t40 units, not 400, for every bin"})
    terminal = started(env, columns=40)
    terminal.keys("\r")
    terminal.wait_for("00, for every bin")
    terminal.keys("qq")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr


def test_a_wide_title_keeps_the_evidence_column(env):
    admitted_fact(env)
    legacy(env, {**CONFIRMED, "title": "北の倉庫は閉鎖された" * 5})
    terminal = started(env)
    assert "1 evidence" in terminal.screen() and "…" in terminal.screen()
    terminal.keys("q")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr


def test_keys_typed_before_a_confirmation_do_not_answer_it(env):
    admitted_fact(env)
    legacy(env, CONFIRMED)
    terminal = started(env)
    terminal.keys("ay")  # typed ahead: the prompt is not on screen yet
    terminal.wait_for("approve review #1? 1 not opened · y/n")
    terminal.keys("n")
    terminal.wait_for("cancelled")
    terminal.keys("q")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert decisions(env) == []


def test_a_locale_the_host_lacks_still_walks(env):
    admitted_fact(env)
    legacy(env, CONFIRMED)
    terminal = started(env, LANG="xx_XX.UTF-8", LC_ALL="xx_XX.UTF-8")
    terminal.keys("q")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "0 approved, 0 rejected, 1 skipped, 0 failed"


def test_an_unknown_terminal_type_is_an_error_not_a_traceback(env):
    admitted_fact(env)
    legacy(env, CONFIRMED)
    code, _, stderr = Terminal(env, TERM="no-such-terminal").finish()
    assert code == 1
    assert "val3: error: val3 review --tty cannot drive this terminal (TERM=no-such-terminal)" in stderr
    assert "Traceback" not in stderr


def test_a_confirmation_wraps_in_a_narrow_window(env):
    admitted_fact(env)
    legacy(env, CONFIRMED)
    legacy(env, SHUT)
    terminal = started(env, columns=30)   # "approve 2 marked? 2 not opened · y/n" needs two lines
    terminal.keys("*a")
    terminal.wait_for("opened")
    terminal.wait_for("y/n")
    terminal.keys("nqq")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr


def test_deciding_the_last_rows_refills_the_list_from_above(env):
    admitted_fact(env)
    for n in range(7):
        legacy(env, {**CONFIRMED, "title": f"North warehouse closed ({n})",
                   "statement": f"The north warehouse closed in March 2025 ({n})."})
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
    admitted_fact(env)
    env.config(embedding={"url": embedder.url})
    legacy(env, CONFIRMED)
    legacy(env, SHUT)
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


LONG = "\n".join(f"line {number} of a long statement" for number in range(1, 61)) + "\nlast line"


def test_a_card_keeps_its_place_when_the_window_narrows(env):
    admitted_fact(env)
    legacy(env, {**CONFIRMED, "statement": LONG})
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
    admitted_fact(env)
    legacy(env, {**CONFIRMED, "statement": "The north warehouse closed in March 2025 " + "and stays closed " * 6})
    terminal = started(env, columns=30)
    terminal.keys("\r")
    terminal.wait_for("review #1")
    terminal.wait_for("stays closed")
    terminal.keys("qq")
    code, _, stderr = terminal.finish()
    assert code == 0, stderr
    assert "Traceback" not in stderr


def test_ctrl_c_during_a_batch_reports_what_was_decided(env, embedder):
    admitted_fact(env)
    env.config(embedding={"url": embedder.url})
    for n in range(4):
        legacy(env, {**CONFIRMED, "title": f"North warehouse closed ({n})",
                   "statement": f"The north warehouse closed in March 2025 ({n})."})
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


def test_a_withdrawal_proposal_is_listed_and_approved_on_the_list(env):
    admitted_fact(env)
    assert env.run("withdraw", f"{env.vault}#1", "--wrong", "--reason", "Drafts were counted.",
                   environ={"CLAUDECODE": "1"}).returncode == 0
    terminal = started(env)
    assert "withdrawal" in terminal.screen()  # the rows are drawn before the footer `started` waits for
    terminal.keys("\r")
    terminal.wait_for("approving withdraws (wrong)")
    terminal.keys("q")
    terminal.keys("a")
    terminal.wait_for("y/n")
    terminal.keys("y")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "1 approved, 0 rejected, 0 skipped, 0 failed"
    assert env.sql(f"SELECT entry_id, review_id FROM {env.vault}.withdrawal") == [(1, 1)]
