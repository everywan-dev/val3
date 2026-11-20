"""The inbox: the harness's auto-memory directory. VAL3 reads it and annotates notes, never owns it."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from val3.config import state_dir

INDEX = "MEMORY.md"
LOCK = ".consolidate-lock"
UNKNOWN_NOTES = 20      # unknown-origin notes sent in one extraction, newest first
UNKNOWN_CHARS = 40_000  # their total size, each counted as sent (clipped at NOTE_CLIP)
NOTE_CLIP = 4_000
_MARK = "Recorded in VAL3 as "
_REFS = re.compile(re.escape(_MARK) + r"(.+?) \(verified\)")
_ORIGIN = re.compile(r"^\s*originSessionId:\s*['\"]?([0-9A-Za-z_-]+)", re.M)


# ponytail: the harness shortens slugs of paths over 200 characters; such projects need autoMemoryDirectory.
def memory_dir(cwd: Path) -> Path:
    """The directory the harness uses for this cwd: one per git repository, worktrees included."""
    root = _repository_root(cwd)
    configured = _setting(root, "autoMemoryDirectory")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root)) / "memory"


def changed_notes(memdir: Path, session: str) -> list[tuple[str, str]]:
    """Notes changed since VAL3 last read them, written by this session or by an unknown one.

    Unknown-origin notes are capped, newest first; the rest stay changed for the next extraction."""
    known = _known(memdir)
    own, unknown = [], []
    for path in sorted(memdir.glob("*.md")) if memdir.is_dir() else []:
        if path.name == INDEX:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            mtime = path.stat().st_mtime
        except OSError:
            continue  # unreadable now; read again at the next extraction
        if known.get(path.name) == _digest(text):
            continue
        origin = _ORIGIN.search(_frontmatter(text))
        if origin and origin[1] != session:
            continue
        if origin:
            own.append((path.name, text))
        else:
            unknown.append((mtime, path.name, text))
    chosen, size = [], 0
    for _, name, text in sorted(unknown, key=lambda note: (note[0], note[1]), reverse=True):
        if len(chosen) == UNKNOWN_NOTES or size + min(len(text), NOTE_CLIP) > UNKNOWN_CHARS:
            break
        chosen.append((name, text))
        size += min(len(text), NOTE_CLIP)
    return sorted(own + chosen)


def settle(memdir: Path, notes: list[tuple[str, str]], refs: dict[str, list[str]]) -> None:
    """Annotate notes that produced entries with their references and remember every processed note.

    While the harness consolidates the directory, notes are left untouched and unrecorded. A note
    edited since it was sent is left too, so its new content goes to the next extraction."""
    if notes and _consolidating(memdir):
        return
    known = _known(memdir)
    for name, text in notes:
        try:
            current = (memdir / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if current != text:
            continue
        if refs.get(name):
            text = _annotate(memdir / name, text, refs[name])
        known[name] = _digest(text)
    _write_json(_known_path(memdir), known)


def _consolidating(memdir: Path) -> bool:
    """The harness holds the lock while it rewrites the directory. Only a lock naming a holder process that
    no longer exists on this machine is stale; a live holder, an unreadable lock or a refused check block."""
    try:
        data = json.loads((memdir / LOCK).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError, RecursionError):
        return True
    holder = data.get("holderPid") if isinstance(data, dict) else None
    if not isinstance(holder, int) or isinstance(holder, bool) or holder <= 0:
        return True
    try:
        os.kill(holder, 0)
    except ProcessLookupError:
        return False
    except (OSError, OverflowError):  # PermissionError: another user's process; OverflowError: pid out of range
        return True
    return True


def _annotate(path: Path, text: str, refs: list[str]) -> str:
    """Append the entries the note became, keeping its body: a note may hold more than its entries do.
    An earlier reference line is merged into the new one, so a note carries one."""
    kept, earlier = [], []
    for line in text.rstrip("\n").split("\n"):
        found = _REFS.match(line)
        if found:
            earlier += found[1].split(", ")
        else:
            kept.append(line)
    refs = list(dict.fromkeys(earlier + refs))
    new = ("\n".join(kept).rstrip("\n") + f"\n\n{_MARK}{', '.join(refs)} (verified). "
           f"Read it with `val3 show {refs[0]}`; search with `val3 recall <terms>`.\n")
    tmp = path.with_name(path.name + ".val3tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    return new


def _frontmatter_block(text: str) -> str:
    end = text.find("\n---", 4) if text.startswith("---\n") else -1
    return text[: end + 4] if end != -1 else ""


def _frontmatter(text: str) -> str:
    return _frontmatter_block(text)[4:]


def _repository_root(cwd: Path) -> Path:
    try:
        done = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return cwd.resolve()
    return Path(done.stdout.strip()).parent if done.returncode == 0 else cwd.resolve()


def _setting(root: Path, key: str) -> str | None:
    for path in (root / ".claude" / "settings.local.json", root / ".claude" / "settings.json",
                 Path.home() / ".claude" / "settings.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get(key)
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(value, str) and value.strip():
            return value
    return None


def _known_path(memdir: Path) -> Path:
    return state_dir() / "inbox" / f"{hashlib.sha256(str(memdir).encode()).hexdigest()}.json"


def _known(memdir: Path) -> dict:
    try:
        data = json.loads(_known_path(memdir).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)
