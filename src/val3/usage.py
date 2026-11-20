"""The usage log (design 2026-11-15-vault-view-design.md §7, §10): one line per call of `val3 recall`, `val3 show`,
`memory_search`, `memory_read` and `memory_save`, written once at the public boundary, in the state directory of the
machine.

For a search, the query text (owner, 2026-11-16), cut to QUERY_CUT characters, beside its number of words; the
file is readable by its owner only. Each line is appended in one write; lines older than KEEP_DAYS
are dropped under a lock with a bounded wait. Recording fails open: a failure changes no output or exit code."""
import fcntl
import json
import os
import re
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from val3.config import state_dir

FORMAT = 1
KEEP_DAYS = 30
QUERY_CUT = 500
LOCK_WAIT = 0.2  # seconds an appender waits while another process prunes; then the line is not written
SEARCHES = ("recall", "memory_search")  # the other surfaces are direct reads, but WRITES
WRITES = ("memory_save",)  # its line adds the writer's status and the ref it saved to; its refs stay empty


def path(project: str) -> Path:
    return state_dir() / "usage" / f"{re.sub(r'[^A-Za-z0-9_.-]', '-', project)}.jsonl"


def record(project: str, surface: str, vaults, refs, outcome: str, limit: int | None = None,
           query: str | None = None, status: str | None = None, ref: str | None = None) -> None:
    """Append one line; outcome is ok, empty, error or timeout."""
    try:
        now = datetime.now(timezone.utc)
        line = {"v": FORMAT, "at": now.isoformat(timespec="microseconds"), "session": os.environ.get("VAL3_SESSION_ID"),
                "project": project, "machine": socket.gethostname(), "surface": surface, "call": uuid.uuid4().hex,
                "vaults": list(vaults or ()), "limit": limit,
                "words": None if query is None else len(query.split()),
                "query": None if query is None else query[:QUERY_CUT], "refs": list(refs or ()),
                "outcome": outcome} | ({"status": status, "ref": ref} if surface in WRITES else {})
        data = (json.dumps(line, ensure_ascii=True) + "\n").encode()
        log = path(project)
        log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = os.open(log.with_suffix(".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if _lock(lock, fcntl.LOCK_EX, 0):
                _prune(log, now - timedelta(days=KEEP_DAYS))
            elif not _lock(lock, fcntl.LOCK_SH, LOCK_WAIT):
                return
            fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
        finally:
            os.close(lock)  # releases the lock
    except Exception:  # noqa: BLE001  the log must never cost the command
        pass


def _lock(fd: int, kind: int, wait: float) -> bool:
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, kind | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)


def _prune(log: Path, cutoff: datetime) -> None:
    """Rewrite the log without lines older than the cutoff, only when its first line is older (under LOCK_EX)."""
    try:
        with open(log, "rb") as handle:
            first = handle.readline()
    except FileNotFoundError:
        return
    if _at(first) >= cutoff:
        return
    kept = [line for line in log.read_bytes().splitlines(keepends=True) if line.endswith(b"\n") and _at(line) >= cutoff]
    tmp = log.with_name(log.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.writelines(kept)
    os.replace(tmp, log)


def _at(line: bytes) -> datetime:
    try:
        return datetime.fromisoformat(json.loads(line)["at"])
    except (ValueError, KeyError, TypeError, RecursionError):
        return datetime.min.replace(tzinfo=timezone.utc)


def lines(project: str, days: int = KEEP_DAYS) -> list[dict] | None:
    """The complete lines of the last `days` days, oldest first; None when this machine has no log."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        raw = path(project).read_bytes()
    except OSError:
        return None
    found = []
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            continue  # being written
        try:
            item = json.loads(line)
            if (item.get("v") == FORMAT and datetime.fromisoformat(item["at"]) >= cutoff
                    and isinstance(item.get("refs"), list) and isinstance(item.get("surface"), str)):
                found.append(item)
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            continue
    return found
