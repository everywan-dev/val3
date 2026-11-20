"""Harness hook entry points. They always exit 0 and never block the session."""
import hashlib
import json
import os
import re
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path

from val3 import neighbour, pending
from val3.config import find_config, load_project, state_dir
from val3.db import connect
from val3.embedding import EmbeddingError, embed
from val3.recheck import own_marks
from val3.search import PROMPT_CAP, Found, find, previous_part
from val3.transcript import HARNESS_TEXT, last_assistant_text
from val3.views import _one_line, prompt_context, safe, session_index

STATEMENT_TIMEOUT_MS = 2000  # a locked table must not hold the session
LIMIT = 10_000  # harness cap for hook context; longer text is moved to a file the model may not read
WARM_UP_TIMEOUT = 5.0  # seconds; a cold load of the embedding model takes about 2 s on CPU
LOG_DAYS = 30
# Without it the agent takes the memory as complete and stops searching the project's documents.
PARTIAL = ("This memory is partial and may be out of date: it complements the project's documents, it does not replace "
           "them. Whatever it shows, also search the project's files before answering or concluding that something is "
           "not recorded.")
ROOM = LIMIT - len(PARTIAL) - 2  # the notice must survive the cap
# The one instruction for memory_save: the tool's description, the server's instructions, this line, the CLAUDE.md block.
SAVE = ("Save durable project knowledge once a result or a decision is settled, when a later session would need it. "
        "Search first and name in `supersedes` an entry it replaces. Quote the evidence the session shows; without "
        "evidence the entry is stored unverified.")
TOOLS_LINE = "VAL3 memory is reached with the tools memory_search, memory_read and memory_save. " + SAVE
_EVENTS = {"session-start": "SessionStart", "prompt": "UserPromptSubmit", "capture": None}


def run(event: str, stdin) -> int:
    try:
        data = json.load(stdin)
    except (ValueError, RecursionError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        context, notice = _HANDLERS[event](data) or (None, None)
    except Exception as exc:  # fail open: a hook must never break the session
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        message = f"VAL3 is unavailable ({detail}); vault memory is not loaded."
        context = _warn_once(data, message) if _EVENTS[event] else None
        notice = _notice(context)
    if context:
        output = {"hookSpecificOutput": {"hookEventName": _EVENTS[event], "additionalContext": context[:LIMIT]}}
        if notice:
            output = {"systemMessage": safe(notice)[:300], **output}
        print(json.dumps(output))
    return 0


def _notice(*parts: str | None) -> str | None:
    """Lines for the person; each part (a warning too) on its own single line."""
    return "\n".join(_one_line(part) for part in parts if part) or None


def session_start(data: dict) -> tuple[str | None, str | None] | None:
    cwd = Path(data.get("cwd") or os.getcwd())
    if find_config(cwd) is None:
        return None
    _export_session(data)
    warning = None
    if pending.entries():
        try:
            pending.spawn_drain()
        except OSError as exc:  # the index still matters more than the catch-up
            warning = f"VAL3: queued sessions were not extracted (cannot start the drainer: {exc}); run `val3 drain`."
    project = load_project(cwd)
    if not project.delivery_index:
        _warm_up(project)
        context = "\n\n".join(part for part in (TOOLS_LINE, warning, PARTIAL) if part)
        return context, _notice("VAL3: index off", warning)
    with connect(project.service, timeout=3, statement_timeout_ms=STATEMENT_TIMEOUT_MS) as conn:
        context, summary = session_index(conn, project, ROOM)
    _warm_up(project)
    counts = [(summary.recheck, "to re-check"), (summary.settle, "to settle"), (summary.collapsed, "sections collapsed"),
              (summary.omitted, "not shown")]
    size = f"VAL3: index of {summary.entries} entries" + "".join(f" · {n} {label}" for n, label in counts if n)
    return "\n\n".join(part for part in (context, warning, PARTIAL) if part), _notice(size, warning)


def _warm_up(project) -> None:
    """Load the embedding model now, so the prompt hook (1 s) does not wait for a cold load."""
    if project.embedding is None:
        return
    try:
        embed(project.embedding, ["warm-up"], WARM_UP_TIMEOUT)
    except EmbeddingError:
        pass  # the prompt hook falls back and warns


def _export_session(data: dict) -> None:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file:
        return
    lines = [f"export {name}={shlex.quote(str(data[key]))}\n"
             for name, key in (("VAL3_SESSION_ID", "session_id"), ("VAL3_TRANSCRIPT", "transcript_path"))
             if data.get(key)]
    with open(env_file, "a", encoding="utf-8") as handle:
        handle.writelines(lines)


def prompt(data: dict) -> tuple[str | None, str | None] | None:
    text = str(data.get("prompt") or "").strip()
    cwd = Path(data.get("cwd") or os.getcwd())
    # Harness turns (peer messages, task notifications, wakeups) get nothing: the index covers them.
    if (not text or data.get("source") not in (None, "user", "sdk") or HARNESS_TEXT.match(text)
            or find_config(cwd) is None):
        return None
    project = load_project(cwd)
    if not project.delivery_prompt:
        _log_prompt(project, data, text, "", Found([], "off"))
        return None
    transcript = data.get("transcript_path")
    previous = last_assistant_text(Path(transcript)) if project.embedding and transcript else ""
    with connect(project.service, timeout=3, statement_timeout_ms=STATEMENT_TIMEOUT_MS) as conn:
        found = find(conn, project, text, PROMPT_CAP, previous=previous, delivery=True)
        own = {vault: ids for vault in project.own if (ids := [hit.id for hit in found.hits if hit.vault == vault])}
        marked = [f"{vault}#{entry_id}" for vault, entry_id in own_marks(conn, project, own)[1]] if own else []
        near = {f"{vault}#{entry_id}": pairs for vault, ids in own.items()
                for entry_id, pairs in neighbour.open_pairs(conn, vault, ids, project, delivery=True).items()}
        context, rendered = prompt_context(conn, found.hits, ROOM, marked, near)
    _log_prompt(project, data, text, previous_part(text, previous) if found.method == "semantic" else "", found)
    warning = _warn_once(data, f"VAL3: {found.warning}.", key="embedding") if found.warning else None
    delivered = f"VAL3: {', '.join(rendered)}" if rendered else None
    parts = (context, warning, PARTIAL) if context else (warning,)
    return "\n\n".join(part for part in parts if part) or None, _notice(delivered, warning)


def _private(path, flags):
    return os.open(path, flags, 0o600)


# ponytail: two sessions pruning at the same moment may lose a line; the log only feeds calibration.
def _log_prompt(project, data: dict, prompt: str, previous: str, found) -> None:
    """One line per typed prompt for `val3 eval --draft`; lines older than LOG_DAYS days are dropped."""
    path = state_dir() / "prompts" / f"{re.sub(r'[^A-Za-z0-9_.-]', '-', project.name)}.jsonl"
    now = datetime.now(timezone.utc)
    record = {"at": now.isoformat(timespec="microseconds"), "session": data.get("session_id"), "prompt": prompt,
              "previous": previous, "method": found.method,
              "injected": [{"ref": hit.ref, "score": round(hit.rank, 4) if hit.method == "semantic" else None}
                           for hit in found.hits]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _prune(path, now - timedelta(days=LOG_DAYS))
        with open(path, "a", encoding="utf-8", opener=_private) as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, ValueError, TypeError):  # a damaged log must never cost the injection
        pass


def _prune(path: Path, cutoff: datetime) -> None:
    """Rewrite the log without old lines, only when its first line is older than the cutoff."""
    try:
        with open(path, encoding="utf-8") as handle:
            first = handle.readline()
    except FileNotFoundError:
        return
    if _logged_at(first) >= cutoff:
        return
    kept = [line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
            if _logged_at(line) >= cutoff]
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", opener=_private) as handle:
        handle.writelines(kept)
    os.replace(tmp, path)


def _logged_at(line: str) -> datetime:
    try:
        return datetime.fromisoformat(json.loads(line)["at"])
    except (ValueError, KeyError, TypeError, RecursionError):
        return datetime.min.replace(tzinfo=timezone.utc)


# ponytail: warn markers are never pruned; one empty file per session.
def _warn_once(data: dict, message: str, key: str = "") -> str | None:
    session = str(data.get("session_id") or "")
    if not session:
        return message
    state = state_dir() / "warned"
    marker = state / hashlib.sha256(f"{session}\0{key}".encode()).hexdigest()
    try:
        state.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=False)
    except FileExistsError:
        return None
    except OSError:
        pass
    return message


def capture(data: dict) -> None:
    """PreCompact and SessionEnd: queue the session for extraction and start a drainer."""
    if os.environ.get("VAL3_EXTRACTING") or not data.get("session_id") or not data.get("transcript_path"):
        return None
    if find_config(Path(data.get("cwd") or os.getcwd())) is None:
        return None
    if not Path(data["transcript_path"]).is_file():  # a session closed before its first message has no transcript
        return None
    pending.enqueue(data)
    pending.spawn_drain()
    return None


_HANDLERS = {"session-start": session_start, "prompt": prompt, "capture": capture}
