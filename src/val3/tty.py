"""`val3 review --tty`: a person marks the pending review items in a full-screen list and decides them."""
import curses
import locale
import os
import re
import sys
import textwrap
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

import psycopg

from val3 import Val3Error
from val3.db import vsql
from val3.views import review_card, safe
from val3.writer import NOTICES, decide

LIST_KEYS = "↑↓ move · space mark · * all · enter open · a approve · r reject · q quit"
CARD_KEYS = "↑↓ PgUp PgDn scroll · space mark · a approve · r reject · n next · p previous · q back"
LABEL = re.compile(r"(\s*)(evidence|steps|keywords|indexes|parameters|examples|origin|tool|environment|observed|"
                   r"supersedes):")
ENTRY = re.compile(r"\S+#\d+  \[|\[[a-z-]+\]  ")  # `vault#7  [fact]  title` or `[fact]  title`
BLOCKS = ("SOURCE ", "BEFORE ", "NOW ", "RECIPE ")
WARNINGS = ("no repository evidence", "not verified:", "SUPERSEDED by", "approving supersedes", "approving withdraws",
            "WITHDRAWN")
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
    after_approval: Callable[[], str | None]
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
    warnings: list[str] = field(default_factory=list)  # from after_approval; repeated once when the walk ends


def pending_ids(conn, vault: str) -> list[int]:
    return [review_id for (review_id,) in conn.execute(vsql(vault, """
        SELECT r.id FROM {v}.review_item r
        WHERE NOT EXISTS (SELECT 1 FROM {v}.review_decision d WHERE d.review_id = r.id)
        ORDER BY r.id"""))]


def walk(conn, project, decided_by: str, after_approval: Callable[[], str | None]) -> None:
    """Decide the items pending at start. A refused decision is counted and the item stays listed; a database
    error restores the terminal, prints the summary and propagates (exit 1); Ctrl-C quits like `q`."""
    vault = project.home
    ids = pending_ids(conn, vault)
    if not ids:
        print("no pending review items")
        return
    w = Walk(conn, project, decided_by, after_approval, list_rows(conn, vault, ids))
    for name in ("", "C.UTF-8"):  # the environment's locale, or UTF-8 when the host lacks it
        try:
            locale.setlocale(locale.LC_ALL, name)
            break
        except locale.Error:
            continue
    try:
        curses.setupterm(None, sys.stdout.fileno())
    except curses.error:
        raise Val3Error(f"val3 review --tty cannot drive this terminal (TERM={os.environ.get('TERM')})") from None
    try:
        curses.wrapper(_list, w)
    except KeyboardInterrupt:
        pass
    except psycopg.Error:
        _report(w)
        raise
    _report(w)
    new = len(set(pending_ids(conn, vault)) - set(ids))
    if new:
        print(f"{new} new items pending: run `val3 review --tty` again")


def _report(w: Walk) -> None:
    print(_summary(w))
    for message in dict.fromkeys(w.warnings):  # once each, in order
        print(f"val3: warning: {message}", file=sys.stderr)


def list_rows(conn, vault: str, ids: list[int]) -> list[Row]:
    """One row per item; imported items classified as the README's measurement query does."""
    rows = []
    for review_id, reason, payload in conn.execute(vsql(vault, """
            SELECT id, reason, payload FROM {v}.review_item WHERE id = ANY(%s) ORDER BY id"""), (ids,)):
        title = safe(str(payload.get("title", ""))).replace("\t", " ")
        if reason in NOTICES:
            rows.append(Row(review_id, f"{reason} (notice)", title, "", False))
            continue
        if reason == "withdrawal":
            rows.append(Row(review_id, "withdrawal", title, safe(str(payload.get("cause", ""))), False))
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


def _columns(text: str) -> int:
    """Screen columns of `text`: wide and full-width characters take two."""
    # ponytail: combining and zero-width characters count as one; they are rare in titles and quotes
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _cut(text: str, columns: int) -> str:
    """The longest start of `text` that fits in `columns`."""
    used = 0
    for index, char in enumerate(text):
        used += _columns(char)
        if used > columns:
            return text[:index]
    return text


def _put(screen, y: int, x: int, text: str, attr: int = 0) -> None:
    """Write within the window, cut to its width; curses refuses the bottom-right cell, never needed here."""
    height, width = screen.getmaxyx()
    if 0 <= y < height and 0 <= x < width - 1:
        try:
            screen.addstr(y, x, _cut(text, width - 1 - x), attr)
        except curses.error:
            pass


def _draw_list(screen, w: Walk) -> int:
    """Redraw the whole list (a full repaint keeps every line's text contiguous); return the body height."""
    height, width = screen.getmaxyx()
    status = textwrap.wrap(w.status, max(width - 1, 10)) or [""]
    body = max(height - 2 - len(status), 1)
    w.cursor = max(0, min(w.cursor, len(w.rows) - 1))
    w.top = max(0, min(max(w.top, w.cursor - body + 1), w.cursor, len(w.rows) - body))
    screen.clear()
    _put(screen, 0, 0, f"review · {w.project.home} · {len(w.rows)} pending", w.attrs["bold"])
    marks = f"{len(w.marked)} marked"
    _put(screen, 0, max(width - len(marks) - 2, 0), marks)
    for y, row in enumerate(w.rows[w.top: w.top + body], start=1):
        index = w.top + y - 1
        head = (f"{'>' if index == w.cursor else ' '} [{'x' if row.id in w.marked else ' '}] "
                f"{'·' if row.id in w.opened else ' '} #{row.id:<5} {row.kind:<{KIND_WIDTH}} ")
        room = max(width - 1 - len(head) - EVIDENCE_WIDTH, 10)
        title = row.title if _columns(row.title) <= room else _cut(row.title, room - 1) + "…"
        title += " " * (room - _columns(title))
        _put(screen, y, 0, f"{head}{title}  {row.evidence}", curses.A_REVERSE if index == w.cursor else 0)
    attr = w.attrs["warning"] if w.status.endswith("y/n") else 0
    for i, line in enumerate(status):
        _put(screen, height - 1 - len(status) + i, 0, line, attr)
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
        elif key in (10, 13, curses.KEY_ENTER):
            _card(screen, w, w.cursor)
        elif key in (ord("a"), ord("r")):
            targets = sorted(w.marked) or [w.rows[w.cursor].id]
            if _confirm(screen, w, targets, key == ord("a")):
                w.status = f"deciding {len(targets)}…"
                _draw_list(screen, w)
                _decide(w, targets, key == ord("a"))


def _confirm(screen, w: Walk, targets: list[int], approve: bool) -> bool:
    """Ask on the status line, naming how many targets carry no evidence and how many were not opened."""
    chosen = [row for row in w.rows if row.id in targets]
    notes = [f"{count} {label}" for count, label in ((sum(row.unsupported for row in chosen), "without evidence"),
                                                     (sum(row.id not in w.opened for row in chosen), "not opened"))
             if count]
    what = f"{len(targets)} marked" if w.marked else f"review #{targets[0]}"
    w.status = f"{'approve' if approve else 'reject'} {what}?{' ' + ', '.join(notes) if notes else ''} · y/n"
    curses.flushinp()  # keys typed before the question is on screen do not answer it
    _draw_list(screen, w)
    answer = screen.getch() == ord("y")
    w.status = "" if answer else "cancelled"
    return answer


def _decide(w: Walk, targets: list[int], approve: bool) -> None:
    """Decide each target in its own transaction; a refusal is counted and the item stays listed."""
    done, failures, warned = 0, [], len(w.warnings)
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
            if (warning := w.after_approval()):
                w.warnings.append(warning)
        else:
            w.rejected += 1
    w.marked -= set(targets)
    w.status = (f"{done} {'approved' if approve else 'rejected'}"
                + (f", {len(failures)} failed: {'; '.join(failures)}" if failures else ""))
    if len(w.warnings) > warned:
        w.status += f"; warning: {w.warnings[-1]}"


def _summary(w: Walk) -> str:
    skipped = sum(row.id not in w.failed for row in w.rows)
    return f"{w.approved} approved, {w.rejected} rejected, {skipped} skipped, {len(w.failed)} failed"


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
    """The card as screen lines of at most `width` columns, tabs expanded; long lines wrap and keep their
    pieces' style."""
    lines = []
    for number, line in enumerate(card.splitlines()):
        current, used = [], 0
        for text, attr in styled(line.expandtabs(), number == 0, attrs):
            while text:
                # an empty cut: the next wide character does not fit; on an empty line it goes anyway
                piece = _cut(text, width - used) or ("" if used else text[0])
                current.append((piece, attr))
                used += _columns(piece)
                text = text[len(piece):]
                if text or used >= width:
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
            before = len(lines)
            lines = _layout(review_card(w.conn, w.project.home, row.id), max(width - 1, 1), w.attrs)
            same = shown is not None and shown[0] == row.id and before
            top = round(top * len(lines) / before) if same else 0  # a width change keeps the place
            shown = (row.id, width)
        body = max(height - 2, 1)
        top = max(0, min(top, len(lines) - body))
        screen.clear()
        _put(screen, 0, 0, f"review #{row.id} · {index + 1}/{len(w.rows)} · [{'x' if row.id in w.marked else ' '}]",
             w.attrs["block"])
        for y, pieces in enumerate(lines[top: top + body], start=1):
            x = 0
            for text, attr in pieces:
                _put(screen, y, x, text, attr)
                x += _columns(text)
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
