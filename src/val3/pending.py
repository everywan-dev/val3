"""The pending queue: one file per captured session event, drained by one process at a time."""
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from val3.config import load_project, state_dir
from val3.db import connect
from val3.extract import ExtractorError, extract

MAX_ATTEMPTS = 3


def queue_dir() -> Path:
    return state_dir() / "queue"


def enqueue(data: dict) -> Path:
    directory = queue_dir()
    directory.mkdir(parents=True, exist_ok=True)
    entry = {"session_id": str(data["session_id"]), "transcript_path": str(data["transcript_path"]),
             "cwd": str(data.get("cwd") or os.getcwd()), "event": str(data.get("hook_event_name") or "manual"),
             "queued_at": time.time(), "attempts": 0}
    path = directory / f"{time.time_ns()}-{os.getpid()}.json"
    _write(path, entry)
    return path


def entries() -> list[Path]:
    return sorted(queue_dir().glob("*.json"))


def failed_entries() -> list[Path]:
    return sorted((queue_dir() / "failed").glob("*.json"))


def spawn_drain() -> None:
    """Start a detached drainer that outlives the hook that started it."""
    state_dir().mkdir(parents=True, exist_ok=True)
    # -P: a module in the working directory is never imported, as for the MCP child
    subprocess.Popen([sys.executable, "-P", "-m", "val3", "drain", "--if-idle"], cwd=state_dir(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def drain(wait: bool) -> int:
    """Process every pending entry at most once; return how many completed.

    One drainer runs at a time. Without `wait`, a held lock means another drainer is working,
    and it takes the entries queued meanwhile before it releases the lock."""
    seen: set[Path] = set()
    down: set[str] = set()  # services found unreachable in this run
    done = 0
    while any(path not in seen for path in entries()):
        with _lock(wait) as held:
            if not held:
                return done
            for path in entries():
                if path in seen:
                    continue
                seen.add(path)
                done += _process(path, down) == "done"
    return done


def describe() -> str:
    lines = []
    for state, paths in (("pending", entries()), ("failed", failed_entries())):
        for path in paths:
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                entry = {}
            line = (f"{state}  {path.name}  session {entry.get('session_id', '?')}  {entry.get('event', '?')}  "
                    f"attempts {entry.get('attempts', 0)}")
            if entry.get("error"):
                line += f"  error: {entry['error']}"
            lines.append(line)
    return "\n".join(lines) or "queue empty"


@contextmanager
def _lock(wait: bool):
    queue_dir().mkdir(parents=True, exist_ok=True)
    with open(queue_dir() / ".lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


@contextmanager
def drain_lock():
    """Wait for the drain lock: one extraction at a time on this machine, by the drainer or `val3 extract`."""
    with _lock(True):
        yield


def _process(path: Path, down: set[str]) -> str:
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "skip"
    except ValueError:
        return _set_aside(path, {}, "unreadable queue entry")
    try:
        cwd = Path(entry["cwd"])
        transcript = Path(entry["transcript_path"])
        session = str(entry["session_id"])
        project = load_project(cwd)
        if project.service in down:
            return "skip"
        conn = connect(project.service)
    except psycopg.OperationalError as exc:
        return _unreachable(path, entry, project.service, exc, down)
    except Exception as exc:  # Val3Error, a malformed entry, or anything unexpected
        return _set_aside(path, entry, _first_line(exc))
    try:
        with conn:
            counts = extract(conn, project, transcript, session, cwd)
    except psycopg.OperationalError as exc:
        return _unreachable(path, entry, project.service, exc, down)
    except ExtractorError as exc:
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        entry["error"] = str(exc)
        if entry["attempts"] >= MAX_ATTEMPTS:
            return _set_aside(path, entry, str(exc))
        _write(path, entry)
        _log(path, entry, "retry", error=str(exc))
        return "retry"
    except Exception as exc:  # Val3Error, another database error, or anything unexpected
        return _set_aside(path, entry, f"{type(exc).__name__}: {_first_line(exc)}")
    path.unlink(missing_ok=True)
    _log(path, entry, "done", **counts)
    return "done"


def _unreachable(path: Path, entry: dict, service: str, exc: Exception, down: set[str]) -> str:
    """The entry waits, without spending an attempt; other entries of that service wait too."""
    down.add(service)
    entry["error"] = _first_line(exc)
    _write(path, entry)
    _log(path, entry, "stop", error=entry["error"])
    return "stop"


def _set_aside(path: Path, entry: object, error: str) -> str:
    entry = entry if isinstance(entry, dict) else {}
    target = queue_dir() / "failed" / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    _write(target, {**entry, "error": error})
    path.unlink(missing_ok=True)
    _log(path, entry, "failed", error=error)
    return "failed"


# ponytail: extract.log is never rotated; one short line per drained entry.
def _log(path: Path, entry: object, outcome: str, **detail) -> None:
    session = entry.get("session_id") if isinstance(entry, dict) else None
    record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "entry": path.name,
              "session": session, "outcome": outcome, **detail}
    state_dir().mkdir(parents=True, exist_ok=True)
    with open(state_dir() / "extract.log", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__
