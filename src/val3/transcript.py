"""Session transcripts (JSONL): evidence text and the condensed form read by the extractor.

Only two sources count as evidence: tool results (what a command, query, API call or test
returned) and the user's own messages. The assistant's text and thinking never count, nor do
compaction summaries or results of tools that echo text the model wrote. The transcript format
is internal to Claude Code; this module is the only place that reads it.
"""
import glob
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from val3 import Val3Error

# Tools whose results hold text the model wrote: subagent answers and echoes of edited files.
MODEL_TEXT_TOOLS = {"Agent", "Task", "Edit", "MultiEdit", "Write", "NotebookEdit"}
# The memory tools (`val3 mcp`, whatever the client names the server) print stored entries, as `val3 show` does.
# memory_save answers with the writer's outcome and messages, which hold the candidate's own text.
_MEMORY_TOOL = re.compile(r"^mcp__.+__memory_(search|read|save)$")
# val3's own output is never evidence, whatever it runs: it prints stored quotes, candidates and reasons
# (show, review show, confirm on a withdrawn entry) and echoes its arguments in usage errors. Any invocation
# counts: the name, maybe quoted (`"val3"`) or escaped (`\val3`), then a subcommand (maybe quoted) or an option,
# or nothing; also inside `bash -c '…'`, and `python -m val3` or `-mval3`.
# The harness's projects directory holds text the model wrote: memory notes and transcripts. A `cd` there
# followed by a relative path names the directory in the same command.
_HARNESS_PROJECTS = re.compile(r"\.claude/projects/")
_VAL3_COMMAND = re.compile(r"(?:^|[\s;&|(`/'\"\\])val3['\"]?(?:[ \t]+['\"]?(?:[a-z][a-z-]*\b|-)|[ \t]*$)"
                           r"|-m[ \t]*val3\b", re.M)
CHUNK_LIMIT = 120_000
_CLIP = {"tool_result": 4_000, "tool_call": 1_000, "text": 8_000}
# Turns the harness writes into the prompt or the message queue (peer messages, task notifications). A peer
# message comes with a line before its tag.
HARNESS_TEXT = re.compile(r"\s*(?:Another Claude session sent a message:\s*)?"
                          r"<(task-notification|agent-message|cross-session-message|teammate-message)[\s>]")
_BASH_INPUT = re.compile(r"\s*<bash-input>(.*?)</bash-input>\s*", re.S)
_BASH_OUTPUT = re.compile(r"\s*<bash-stdout>(.*?)</bash-stdout>\s*<bash-stderr>(.*?)</bash-stderr>\s*", re.S)


@dataclass(frozen=True)
class Chunk:
    text: str       # condensed lines
    end: int        # transcript lines covered once this chunk is extracted (the next watermark)
    user_text: str  # what the user wrote in it, to look up existing entries
    at: str | None  # timestamp of the last record it covers that has one, rendered or not


@dataclass(frozen=True)
class Call:
    """One tool call of a transcript with its result, as evidence is checked against it."""
    tool: object     # the tool's name; "!" for a command the user typed
    id: str | None   # the tool_use id; None for a typed command
    record: int      # index of the transcript record that holds the result
    input: object    # the structured tool_use input; a typed command's body
    given: str       # the input as the check compares it: its string values, normalised
    output: str


def evidence_calls(path: Path, private: tuple[str, ...] = ()) -> tuple[list[Call], str]:
    """Return (the calls whose output may be quoted, user message text) of a transcript (design E1).

    A call's `given` is the string values of its tool_use input; a typed `!` command's is its bash-input
    body. A tool_result with no tool_use before it cannot be checked and is left out.
    `private` names more directories whose content the model wrote (a relocated memory directory)."""
    records = _load(path)
    excluded = _model_text_calls(records, private)
    inputs: dict[str, tuple[object, object, str]] = {}  # flattened once per call, however many results it has
    calls: list[Call] = []
    user: list[str] = []
    typed = ""
    for index, record in enumerate(records):
        user.extend(_typed(record))
        if isinstance(record, dict) and record.get("type") == "assistant":
            for block in _blocks(_content(record)):
                if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                    inputs[block["id"]] = (block.get("name"), block.get("input"),
                                           _normal("\n".join(_strings(block.get("input")))))
            continue
        if not _is_user(record):
            continue
        content = _content(record)
        if isinstance(content, str):
            if (found := _BASH_INPUT.fullmatch(content)) is not None:
                typed = found.group(1)
            elif (output := _bash_output(content)) is not None:
                if index not in excluded:  # excluded also when no bash-input came before it
                    calls.append(Call("!", None, index, typed, _normal(typed), _normal("\n".join(output))))
                typed = ""
            continue
        for block in _blocks(content):
            call = block.get("tool_use_id")
            if (block.get("type") == "tool_result" and isinstance(call, str) and call in inputs
                    and call not in excluded):
                name, given, flat = inputs[call]
                calls.append(Call(name, call, index, given, flat, _normal("\n".join(_texts(block.get("content"))))))
    return calls, _normal("\n".join(user))


def _strings(value: object) -> list[str]:
    """Every string inside a JSON value, in order."""
    out: list[str] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, (dict, list)):
            stack.extend(reversed(list(item.values()) if isinstance(item, dict) else item))
    return out


def condense(path: Path, start: int, limit: int = CHUNK_LIMIT,
             private: tuple[str, ...] = (), lines: int | None = None) -> tuple[list[Chunk], int]:
    """Readable form of the transcript lines after `start`, in chunks of at most `limit` characters.

    Returns the chunks and the number of transcript lines read: all of them, or the first `lines`.
    Clipped blocks keep a literal prefix, so quotes taken from them still match the transcript."""
    records = _load(path)[:lines]
    excluded = _model_text_calls(records, private)
    chunks: list[Chunk] = []
    parts: list[str] = []
    users: list[str] = []
    size = 0
    at = None
    for index in range(start, len(records)):
        record = records[index]
        rendered = _render(record, excluded, index in excluded)
        if rendered:
            block = "\n".join(rendered)
            if parts and size + len(block) + 1 > limit:
                chunks.append(Chunk("\n".join(parts), index, "\n".join(users), at))
                parts, users, size, at = [], [], 0, None
            parts.append(block)
            size += len(block) + 1
            users.extend(_typed(record))
        if isinstance(record, dict) and isinstance(record.get("timestamp"), str):  # unrendered records count too
            at = record["timestamp"]
    if parts:
        chunks.append(Chunk("\n".join(parts), len(records), "\n".join(users), at))
    return chunks, len(records)


def last_assistant_text(path: Path, tail: int = 262_144) -> str:
    """Text of the last assistant message that has text, read from the end of the transcript; "" if none.

    Never raises: the prompt hook calls it on every prompt."""
    try:
        with open(path, "rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - tail))
            lines = handle.read().decode("utf-8", errors="replace").split("\n")
    except OSError:
        return ""
    if size > tail:
        lines = lines[1:]  # the first line read is cut
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(record, dict) and record.get("type") == "assistant" and not record.get("isSidechain"):
            texts = [block["text"] for block in _blocks(_content(record))
                     if block.get("type") == "text" and isinstance(block.get("text"), str)]
            if texts:
                return "\n".join(texts)
    return ""


def session_directory(session: str | None, project_dir: str | None = None) -> Path | None:
    """The one directory of the harness's projects (`$CLAUDE_CONFIG_DIR/projects`, else `~/.claude/projects`) that
    holds `<session>.jsonl`, by its canonical path; None when more than one does. A session cleared before its
    first message has no file: then the directory the harness names after `project_dir` (every character that is
    not a letter or a digit becomes `-`), when it exists."""
    projects = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    found: set[Path] = set()
    if session and "/" not in session and session not in (".", ".."):
        found = {path.parent.resolve() for path in projects.glob(f"*/{glob.escape(session)}.jsonl")}
    if not found and project_dir:
        named = projects / re.sub(r"[^A-Za-z0-9]", "-", project_dir)
        found = {named.resolve()} if named.is_dir() else set()
    return found.pop() if len(found) == 1 else None


def call_files(directory: Path, call_id: str, since: float) -> list[tuple[Path, str, str | None]]:
    """(canonical path, session, subagent id or None) of each transcript of `directory`, a session's or a
    subagent's (`<session>/subagents/agent-<id>.jsonl`), with a complete assistant record of a `memory_save` call
    whose tool-use id is `call_id`. A file whose canonical path leaves the directory is not read."""
    root = directory.resolve()
    found: dict[Path, tuple[Path, str, str | None]] = {}
    for path in sorted([*root.glob("*.jsonl"), *root.glob("*/subagents/*.jsonl")]):
        real = path.resolve()
        if real in found or not real.is_relative_to(root):
            continue
        try:
            # ponytail: the record is written after the call arrives, so an older file cannot hold it; this keeps
            # the lookup off the project's other sessions. A file whose mtime is set back is not looked at, and an
            # ambiguity is detected among recently written files only.
            if not real.is_file() or real.stat().st_mtime < since:  # a FIFO or a directory is never read
                continue
            lines = _lines(real)
        except (OSError, Val3Error):
            continue
        if any(call_id in line and _saves(_parse(line), call_id) for line in lines):
            parts = real.relative_to(root).parts
            found[real] = ((real, real.stem, None) if len(parts) == 1
                           else (real, parts[0], real.stem.removeprefix("agent-")))
    return list(found.values())


def _saves(record: dict | None, call_id: str) -> bool:
    """An assistant record holding the `memory_save` tool_use `call_id`, whatever the client names the server."""
    return (isinstance(record, dict) and record.get("type") == "assistant"
            and any(block.get("type") == "tool_use" and block.get("id") == call_id
                    and isinstance(block.get("name"), str) and block["name"].endswith("__memory_save")
                    for block in _blocks(_content(record))))


# Record types that carry a `uuid`, unique within a transcript; metadata records (titles, modes, snapshots) carry none.
MESSAGE_TYPES = {"user", "assistant", "system", "attachment"}


def identities(path: Path) -> list[tuple[str, bool]]:
    """(identity, is a message) per complete transcript line, as `_load` counts them.

    The identity is the record's `uuid`, which a resumed session's transcript keeps for the records it repeats,
    or else a hash of the raw line. A message is a record of a type that carries a uuid, with or without one."""
    out = []
    for line in _lines(path):
        record = _parse(line)
        uid = record.get("uuid") if record is not None else None
        out.append((uid if isinstance(uid, str) and uid else "sha256:" + hashlib.sha256(line.encode()).hexdigest(),
                    record is not None and record.get("type") in MESSAGE_TYPES))
    return out


def _lines(path: Path) -> list[str]:
    """The complete lines of a transcript. A last line without its newline is still being written: it is left
    for the next read."""
    try:
        return path.read_text(encoding="utf-8", errors="replace").split("\n")[:-1]
    except OSError as exc:
        raise Val3Error(f"cannot read transcript {path}: {exc.strerror}") from None


def _load(path: Path) -> list[dict | None]:
    """One item per complete transcript line: the record, or None for a line that is not a JSON object."""
    return [_parse(line) for line in _lines(path)]


def _parse(line: str) -> dict | None:
    try:
        record = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return record if isinstance(record, dict) else None


def _is_user(record: dict | None) -> bool:
    """A user-type record that the harness did not inject: typed prompts and tool results.

    Records with an origin other than "human" (task notifications, peer messages) carry model or
    harness text even when they are not marked isMeta."""
    if not isinstance(record, dict) or record.get("type") != "user":
        return False
    origin = record.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return False
    return not (record.get("isMeta") or record.get("isCompactSummary") or record.get("isVisibleInTranscriptOnly"))


def _render(record: dict | None, excluded: set, model_text: bool = False) -> list[str]:
    queued = _queued_text(record)
    if queued is not None:
        return [f"USER: {_clip(queued, 'text')}"]
    content = _content(record) if isinstance(record, dict) else None
    if _is_user(record):
        if isinstance(content, str):
            output = _bash_output(content)
            if output is not None:
                label = "TOOL RESULT (not evidence)" if model_text else "TOOL RESULT (! command typed by the user)"
                return [f"{label}:\n{_clip(_normal(chr(10).join(output)), 'tool_result')}"]
            return [f"USER: {_clip(content, 'text')}"]
        out = []
        for block in _blocks(content):
            if block.get("type") == "tool_result":
                label = "TOOL RESULT (not evidence)" if block.get("tool_use_id") in excluded else "TOOL RESULT"
                text = _normal("\n".join(_texts(block.get("content"))))
                out.append(f"{label}:\n{_clip(text, 'tool_result')}")
            elif block.get("type") == "text" and isinstance(block.get("text"), str):
                out.append(f"USER: {_clip(block['text'], 'text')}")
        return out
    if isinstance(record, dict) and record.get("type") == "assistant":
        out = []
        for block in _blocks(content):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                out.append(f"ASSISTANT: {_clip(block['text'], 'text')}")
            elif block.get("type") == "tool_use":
                tool_input = block.get("input")
                command = tool_input.get("command") if isinstance(tool_input, dict) else None
                call = command if isinstance(command, str) else json.dumps(tool_input, ensure_ascii=False)
                out.append(f"TOOL CALL {block.get('name')}: {_clip(call, 'tool_call')}")
        return out
    return []


def _queued_text(record: dict | None) -> str | None:
    """A message the user typed while the agent was busy: the harness stores it as a queued_command
    attachment. Harness turns arrive the same way and are not the user's."""
    if not isinstance(record, dict) or record.get("type") != "attachment":
        return None
    attachment = record.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
        return None
    prompt, origin = attachment.get("prompt"), attachment.get("origin")
    if (not isinstance(prompt, str) or record.get("isMeta") or HARNESS_TEXT.match(prompt)
            or not (isinstance(origin, dict) and origin.get("kind") == "human")):
        return None
    return prompt


def _bash_output(text: str) -> list[str] | None:
    """Output of a `!` command typed by the user: the whole string is the bash-stdout and bash-stderr
    tags the harness wraps it in. Text that merely starts that way stays the user's."""
    found = _BASH_OUTPUT.fullmatch(text)
    return [body for body in found.groups() if body] if found else None


def _typed(record: dict | None) -> list[str]:
    """What the user typed in a record: a prompt, text blocks or a queued message; never tool or `!` output.

    A subagent's records are sidechain: its first `user` record is the prompt its parent wrote, not the user."""
    if isinstance(record, dict) and record.get("isSidechain"):
        return []
    queued = _queued_text(record)
    if queued is not None:
        return [queued]
    if not _is_user(record):
        return []
    content = _content(record)
    if isinstance(content, str):
        texts = [] if _bash_output(content) is not None else [content]
    else:
        texts = [block["text"] for block in _blocks(content)
                 if block.get("type") == "text" and isinstance(block.get("text"), str)]
    return [text for text in texts if not HARNESS_TEXT.match(text)]  # also when the record carries no origin


# ponytail: a command that echoes or cats text the model wrote still passes; no reader can tell.
def _model_text_calls(records: list[dict | None], private: tuple[str, ...] = ()) -> set:
    """Ids of tool calls whose results hold text the model wrote, plus the record indexes of `!`
    command outputs that do or whose command is missing (the command comes from the preceding
    bash-input record)."""
    ids: set = set()
    typed = None
    for index, record in enumerate(records):
        if _is_user(record) and isinstance(content := _content(record), str):
            if (found := _BASH_INPUT.fullmatch(content)) is not None:
                typed = found.group(1)
            elif _bash_output(content) is not None:
                if typed is None or _shows_model_text(typed, typed, private):  # no command: cannot be trusted
                    ids.add(index)
                typed = None
            continue
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        for block in _blocks(_content(record)):
            if block.get("type") != "tool_use":
                continue
            tool_input = block.get("input")
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            arguments = json.dumps(tool_input, ensure_ascii=False)
            name = block.get("name")
            if not isinstance(block.get("id"), str):  # never paired with a result
                continue
            if (isinstance(name, str) and (name in MODEL_TEXT_TOOLS or _MEMORY_TOOL.match(name))
                    or _shows_model_text(command if name == "Bash" else None, arguments, private)):
                ids.add(block["id"])
    return ids


def _shows_model_text(command: object, arguments: str, private: tuple[str, ...]) -> bool:
    """A shell command running val3, or arguments naming the memory or a private directory."""
    return bool((isinstance(command, str) and _VAL3_COMMAND.search(command))
                or _HARNESS_PROJECTS.search(arguments) or any(directory in arguments for directory in private))


def _content(record: dict) -> object:
    message = record.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _blocks(content: object) -> list[dict]:
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def _texts(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)]
    return []


def _clip(text: str, kind: str) -> str:
    limit = _CLIP[kind]
    return text if len(text) <= limit else f"{text[:limit]}\n[… {len(text) - limit} more characters]"


def _normal(text: str) -> str:
    return text.replace("\r\n", "\n")
