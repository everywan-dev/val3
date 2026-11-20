"""`val3 init`: set a project up (val3.toml, Claude Code hooks, a VAL3 block in CLAUDE.md) and check the
connection. Every step is idempotent: re-running it is how an existing project is updated."""
import json
import re
import os
import shlex
import stat
import tempfile
from importlib.resources import files
from pathlib import Path

import psycopg

from val3 import Val3Error, embedding
from val3.config import CONFIG_FILE, Project, check_vault_name, load_project
from val3.db import connect, is_current
from val3.views import _one_line

HOOKS = {"SessionStart": "session-start", "UserPromptSubmit": "prompt", "PreCompact": "capture",
         "SessionEnd": "capture"}
SETTINGS = (".claude/settings.json", ".claude/settings.local.json")
MCP_FILE = ".mcp.json"
SERVER = {"command": "val3", "args": ["mcp"]}
TOOLS = ("mcp__val3__memory_search", "mcp__val3__memory_read")
SAVE_TOOL = "mcp__val3__memory_save"  # allowed only on a person's `--allow-save`, and never taken away
LOCAL_ADD = "claude mcp add --scope local val3 -- val3 mcp"
APPROVE = "approve the val3 server once in an interactive session"
BEGIN, END = b"<!-- val3:begin -->", b"<!-- val3:end -->"
BLOCK = BEGIN + b"\n" + files("val3").joinpath("claude_block.md").read_bytes() + END + b"\n"


class UsageError(Val3Error):
    """A usage error: exit code 2."""


def hook_events(settings: object) -> list[str]:
    """The `val3 hook <event>` events named by the command of command hooks; other text does not count."""
    events = []
    groups_by_event = settings.get("hooks") if isinstance(settings, dict) else None
    for groups in groups_by_event.values() if isinstance(groups_by_event, dict) else ():
        for group in groups if isinstance(groups, list) else ():
            hooks = group.get("hooks") if isinstance(group, dict) else None
            for hook in hooks if isinstance(hooks, list) else ():
                if isinstance(hook, dict) and hook.get("type") == "command" and isinstance(hook.get("command"), str):
                    events += _command_events(hook["command"])
    return list(dict.fromkeys(events))


def _command_events(command: str) -> list[str]:
    """`<…>/val3 hook <event>` read as shell words, so quoting does not hide it, quoted text does not fake it and
    punctuation such as `;` or `||` is a word of its own."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return []
    return [words[i + 2] for i in range(len(words) - 2) if os.path.basename(words[i]) == "val3"
            and words[i + 1] == "hook" and words[i + 2] in ("session-start", "prompt", "capture")]


def run(root: Path, service: str | None, vault: str | None, linked: list[str],
        project: str | None, allow_save: bool = False) -> tuple[list[str], int]:
    """The step lines and the exit code (1 when the connection check finds a problem).

    Every file is read and checked before any is written, so a refusal leaves them all as they were."""
    config, config_line = _config(root, service, vault, linked, project)
    settings = _settings(root)
    for label, data in settings.items():  # both files, though permissions go to one
        _allow(data, label)
    changed: set[str] = set()
    hooks_line, with_hooks = _hooks(settings, changed)
    local = with_hooks == [SETTINGS[1]]
    target = SETTINGS[1] if local else SETTINGS[0]
    mcp, mcp_lines = _mcp(root, local)
    permissions_line = _permissions(settings, target, changed, TOOLS + ((SAVE_TOOL,) if allow_save else ()))
    claude = _claude_md(root)
    writes = [config, *((_inside(root, label), _json(settings[label])) for label in SETTINGS if label in changed),
              mcp, claude[:2]]
    writes = [item for item in writes if item is not None]
    seen: dict[Path, Path] = {}
    for path, _ in writes:  # a link must not make one write undo another
        if (real := path.resolve()) in seen:
            raise Val3Error(f"{seen[real]} and {path} are the same file ({real}); left unchanged")
        seen[real] = path
    for item in writes:
        _write(*item)
    lines = [config_line, hooks_line, mcp_lines[0], permissions_line, *mcp_lines[1:], claude[2]]
    problems = _connection(load_project(root))
    lines += [f"connection: {problem}" for problem in problems] or ["connection: ok"]
    return lines, 1 if problems else 0


def _config(root: Path, service, vault, linked, name) -> tuple[tuple[Path, bytes] | None, str]:
    """The val3.toml to write, if any, and its line."""
    path = _inside(root, CONFIG_FILE)
    if os.path.lexists(path):
        conf = load_project(root)
        for key, given, have in (("service", service, conf.service), ("project", name, conf.name),
                                 ("home", vault, conf.home), ("linked", linked or None, list(conf.linked))):
            if given is not None and given != have:
                raise UsageError(f"{CONFIG_FILE} exists and says {key} = {json.dumps(have, ensure_ascii=False)}")
        return None, f"{CONFIG_FILE}: kept"
    if not service or not vault:
        raise UsageError(f"no {CONFIG_FILE} here: --service and --vault are required")
    try:
        names = [check_vault_name(v) for v in (vault, *linked)]
    except Val3Error as exc:
        raise UsageError(str(exc)) from None
    if len(set(names)) != len(names):
        raise UsageError("a vault is named twice")
    quote = lambda value: json.dumps(value, ensure_ascii=False)  # noqa: E731 - a TOML basic string
    text = f"service = {quote(service)}\n"
    text += f"project = {quote(name)}\n" if name is not None else ""
    text += f"[vaults]\nhome = {quote(vault)}\n"
    text += f"linked = [{', '.join(map(quote, linked))}]\n" if linked else ""
    text += "[delivery]\nindex = false\nprompt = false\n"  # the mix accepted in plan 14: the tools only
    with tempfile.TemporaryDirectory() as tmp:
        try:
            (Path(tmp) / CONFIG_FILE).write_text(text, encoding="utf-8")
            load_project(Path(tmp))
        except OSError as exc:
            raise Val3Error(f"cannot check {path} before writing it: {exc.strerror}") from None
        except Val3Error as exc:
            raise Val3Error(str(exc).replace(str(Path(tmp) / CONFIG_FILE), str(path))) from None
    return (path, text.encode()), f"{CONFIG_FILE}: written"


def _json(data: dict) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()


def _object(root: Path, label: str) -> dict | None:
    """The JSON object in `root/label`, None when there is no such file."""
    path = _inside(root, label)
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Val3Error(f"cannot read {path}: {exc.strerror}") from None
    except (ValueError, RecursionError) as exc:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise Val3Error(f"{label} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise Val3Error(f"{label} is not a JSON object")
    return data


def _settings(root: Path) -> dict[str, dict]:
    return {label: data for label in SETTINGS if (data := _object(root, label)) is not None}


def _hooks(settings: dict[str, dict], changed: set[str]) -> tuple[str, list[str]]:
    """The hooks line and the settings files that hold val3 hooks; adds them to settings.json when none does."""
    found = {label: ", ".join(events) for label, data in settings.items() if (events := hook_events(data))}
    if found:
        return f"hooks: kept ({'; '.join(f'{events} in {label}' for label, events in found.items())})", list(found)
    label = SETTINGS[0]
    data = settings.setdefault(label, {})
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict) or not all(isinstance(groups, list) for groups in hooks.values()):
        raise Val3Error(f"{label}: 'hooks' must be an object of lists; left unchanged")
    for event, name in HOOKS.items():
        hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": f"val3 hook {name}"}]})
    data["autoDreamEnabled"] = False
    changed.add(label)
    return f"hooks: added to {label}", [label]


def _mcp(root: Path, local: bool) -> tuple[tuple[Path, bytes] | None, list[str]]:
    """The .mcp.json to write, if any, and its lines. A local set-up writes no tracked file, but the file is
    checked all the same."""
    data = _object(root, MCP_FILE) or {}
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise Val3Error(f"{MCP_FILE}: 'mcpServers' must be an object; left unchanged")
    have = servers.get("val3")
    if have is not None and (not isinstance(have, dict) or have.get("command") != SERVER["command"]
                             or have.get("args") != SERVER["args"]):
        raise Val3Error(f"{MCP_FILE}: a val3 server with another command is configured "
                        f"({_one_line(json.dumps(have, ensure_ascii=False))}); left unchanged")
    if local:
        return None, [f'mcp: run "{LOCAL_ADD}"']
    if have is None:
        servers["val3"] = dict(SERVER)
        return (_inside(root, MCP_FILE), _json(data)), [f"mcp: added to {MCP_FILE}", APPROVE]
    return None, [f"mcp: kept ({MCP_FILE})"]


def _allow(data: dict, label: str) -> list:
    """`permissions.allow` of the settings `data`, refused when it or `permissions` is of the wrong type."""
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        raise Val3Error(f"{label}: 'permissions' must be an object; left unchanged")
    allow = permissions.get("allow", [])
    if not isinstance(allow, list):
        raise Val3Error(f"{label}: 'permissions.allow' must be a list; left unchanged")
    return allow


def _permissions(settings: dict[str, dict], label: str, changed: set[str], tools: tuple[str, ...]) -> str:
    """Append the `tools` missing from `permissions.allow` of the settings file `label`; nothing is removed."""
    data = settings.setdefault(label, {})
    allow = _allow(data, label)
    missing = [tool for tool in tools if tool not in allow]
    if not missing:
        return f"permissions: kept ({label})"
    permissions = data["permissions"] = data.get("permissions", {})
    permissions["allow"] = allow + missing
    changed.add(label)
    return f"permissions: added to {label}"


def _claude_md(root: Path) -> tuple[Path, bytes, str]:
    """The CLAUDE.md to write and its line. The block goes into CLAUDE.md, or into CLAUDE.local.md once a person
    has moved it there (an install kept out of the repository)."""
    name = "CLAUDE.local.md" if BEGIN in _read(_inside(root, "CLAUDE.local.md")) else "CLAUDE.md"
    path = _inside(root, name)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return path, BLOCK, f"{name}: created"
    except OSError as exc:
        raise Val3Error(f"cannot read {path}: {exc.strerror}") from None
    try:
        data.decode("utf-8")  # the markers are ASCII: found in the bytes, so the rest keeps its bytes
    except UnicodeDecodeError:
        raise Val3Error(f"{path} is not valid UTF-8") from None
    begins, ends = data.count(BEGIN), data.count(END)
    if begins == ends == 0:
        if data and not data.endswith(b"\n"):
            data += b"\n"
        return path, data + (b"\n" if data else b"") + BLOCK, f"{name}: block written"
    start, end = data.find(BEGIN), data.find(END)
    if begins != 1 or ends != 1 or end < start:
        raise Val3Error(f"{path}: expected one {BEGIN.decode()} line followed by one {END.decode()} line; "
                        "left unchanged")
    return path, data[:start] + BLOCK[:-1] + data[end + len(END):], f"{name}: block replaced"  # end keeps its EOL


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""
    except OSError as exc:
        raise Val3Error(f"cannot read {path}: {exc.strerror}") from None


def _connection(project: Project) -> list[str]:
    problems = []
    try:
        with connect(project.service, timeout=3) as conn:
            for vault in project.vaults:
                if conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (vault,)).fetchone() is None:
                    problems.append(f"vault {vault} does not exist (`val3 vault create {vault}`)")
                elif not is_current(conn, vault):
                    problems.append(f"vault {vault} needs `val3 vault upgrade {vault}`")
    except psycopg.Error as exc:
        problems.append(_one_line((str(exc).strip().splitlines() or [type(exc).__name__])[0]))
    if project.embedding is not None:
        try:
            embedding.embed(project.embedding, ["init"], 5.0)
        except embedding.EmbeddingError:
            problems.append(f"embedding service unreachable ({_one_line(project.embedding.url)})")
    return problems


def _inside(root: Path, name: str) -> Path:
    """`root/name`, refused when it or its directory is a dangling link or resolves outside the project."""
    path = root / name
    for item in (path, path.parent):
        if os.path.lexists(item) and not item.exists():
            raise Val3Error(f"{item} is a dangling link")
    if not path.resolve().is_relative_to(root.resolve()):
        raise Val3Error(f"{path} resolves outside the project ({path.resolve()})")
    return path


def _write(path: Path, data: bytes) -> None:
    """Replace a file atomically keeping its mode, or create it; links are written through."""
    real = path.resolve()
    try:
        if real.exists():
            mode = stat.S_IMODE(real.stat().st_mode)
            fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                os.chmod(tmp, mode)
                os.replace(tmp, real)
            except BaseException:
                os.unlink(tmp)
                raise
        else:
            real.parent.mkdir(exist_ok=True)
            with os.fdopen(os.open(real, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666), "wb") as handle:
                handle.write(data)
    except OSError as exc:
        raise Val3Error(f"cannot write {path}: {exc.strerror}") from None


def add_specialized(root: Path, name: str) -> str | None:
    """Add `name` to `[vaults] specialized` in val3.toml, line by line: replace a one-line `specialized`, or insert
    one after a one-line `home`. Returns None when the file was edited, else the line to add by hand."""
    path = _inside(root, CONFIG_FILE)
    project = load_project(root)
    line = f"specialized = [{', '.join(json.dumps(v, ensure_ascii=False) for v in (*project.specialized, name))}]"
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)  # keeps every line ending as it is
    start = next((i for i, text in enumerate(lines) if text.strip() == "[vaults]"), None)
    if start is None:
        return line
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    home = [i for i in range(start + 1, end) if re.match(r"home\s*=\s*(\"[^\"]*\"|'[^']*')\s*$", lines[i])]
    special = [i for i in range(start + 1, end) if re.match(r"specialized\s*=", lines[i])]
    if special:
        old = lines[special[0]]
        if len(special) > 1 or "#" in old or not re.match(r"specialized\s*=\s*\[.*\]\s*$", old):
            return line
        lines[special[0]] = line + _ending(old)
    elif len(home) == 1 and "#" not in lines[home[0]]:
        ending = _ending(lines[home[0]])
        if not lines[home[0]].endswith("\n"):
            lines[home[0]] += ending
        lines.insert(home[0] + 1, line + ending)
    else:
        return line
    text = "".join(lines)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            (Path(tmp) / CONFIG_FILE).write_bytes(text.encode())
            load_project(Path(tmp))
        except (OSError, Val3Error):
            return line
    _write(path, text.encode())
    return None


def _ending(line: str) -> str:
    return line[len(line.rstrip("\r\n")):] or "\n"
