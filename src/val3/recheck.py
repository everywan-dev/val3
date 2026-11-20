"""The "to re-check" mark (design L1-L3, §5.1): computed when read, from the home vault's current `file` quotes
against HEAD of the repository val3 runs in. Git is read through objects only, never the working tree."""
import os
import posixpath
import shlex
import signal
import subprocess
import time
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path

from val3 import Val3Error
from val3.config import Project
from val3.db import has_withdrawals, live, vsql
from val3.text import _one_line

BUDGET = 3.0  # seconds for all git calls of one marks() computation; hooks must not wait on git
TIMEOUT = 10.0  # one call outside a computation


@dataclass(frozen=True)
class Change:
    path: str  # the changed file; for a recipe mark, the cited ref
    missing: bool  # no such path at HEAD (or not a file there)
    cited: str | None = None  # a recipe mark: "superseded by <ref>" or "withdrawn"


def _git(repo: Path, args: list[str], deadline: float | None, input: bytes | None = None) -> bytes:
    """stdout of one git call, within the time left before `deadline`. Its own session, so a timeout kills
    whatever the call started too (a wrapper script's children would otherwise hold the pipes open)."""
    left = TIMEOUT if deadline is None else deadline - time.monotonic()
    if left <= 0:
        raise subprocess.TimeoutExpired("git", 0)
    with subprocess.Popen(["git", "-C", str(repo), "--no-optional-locks", *args], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as proc:
        try:
            out, err = proc.communicate(input, timeout=left)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            raise
    if proc.returncode != 0:
        raise Val3Error(f"git {args[0]} failed: {err.decode(errors='replace').strip()}")
    return out


def repository(project: Project, *, deadline: float | None = None) -> Path | None:
    """The project's root when it is the root of a git repository; else, or when git fails, None."""
    try:
        top = _git(project.root, ["rev-parse", "--show-toplevel"], deadline).decode(errors="replace")
        top = top.removesuffix("\n")  # only git's newline: a root may end in a space
    except (OSError, subprocess.SubprocessError, Val3Error):
        return None
    return project.root if top and Path(top).resolve() == project.root.resolve() else None


def head(repo: Path, *, deadline: float | None = None) -> str:
    try:
        return _git(repo, ["rev-parse", "--verify", "HEAD^{commit}"], deadline).decode().strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise Val3Error(f"git did not run: {exc}") from None


def _objects(repo: Path, names: list[str], batch: str, deadline: float | None) -> list[tuple[bytes | None, bytes]]:
    """(type, content) of each object name, type None when git reports none; one NUL-framed cat-file process."""
    out = _git(repo, ["cat-file", batch, "-Z"], deadline, "".join(name + "\0" for name in names).encode())
    found, pos = [], 0
    for _ in names:
        end = out.index(b"\0", pos)
        parts = out[pos:end].rsplit(b" ", 2)
        pos = end + 1
        if len(parts) != 3 or not parts[2].isdigit():  # `<name> missing`, `<name> ambiguous`
            found.append((None, b""))
            continue
        size = int(parts[2]) if batch == "--batch" else -1  # --batch-check prints no content and no NUL after it
        found.append((parts[1], out[pos:pos + max(size, 0)]))
        pos += size + 1
    return found


def read_at(repo: Path, sha: str, paths: Iterable[str], *, deadline: float | None = None) -> dict[str, str | None]:
    """Text of each `<sha>:<path>`; None when the path is missing there or is not a file."""
    paths = list(dict.fromkeys(paths))
    found = _objects(repo, [f"{sha}:{path}" for path in paths], "--batch", deadline) if paths else []
    return {path: content.decode("utf-8", errors="replace").replace("\r\n", "\n") if kind == b"blob" else None
            for path, (kind, content) in zip(paths, found)}


def check_files(repo: Path, sha: str, items: list[dict]) -> None:
    """Refuse a `file` item whose path is not canonical, is excluded, is not a file at `sha` or lacks the quote."""
    from val3.importer import excluded  # here: the importer imports this module
    files = [(i, item) for i, item in enumerate(items) if item["kind"] == "file"]
    for i, item in files:
        path = item["path"]
        if posixpath.normpath(path) != path or path.startswith(("/", "-", ".git/")):
            raise Val3Error(f"evidence[{i}]: {_one_line(path)} is not a canonical path from the repository root")
        if excluded(path):
            raise Val3Error(f"evidence[{i}]: {_one_line(path)} is never accepted as evidence")
    try:
        texts = read_at(repo, sha, (item["path"] for _, item in files))
    except (OSError, subprocess.SubprocessError) as exc:
        raise Val3Error(f"git did not run: {exc}") from None
    for i, item in files:
        text = texts[item["path"]]
        if text is None:
            raise Val3Error(f"evidence[{i}]: {_one_line(item['path'])} is not a file at HEAD {sha[:12]}")
        if item["quote"] not in text:
            raise Val3Error(f"evidence[{i}]: quote not found in {_one_line(item['path'])} at HEAD {sha[:12]}")


def marks(conn, project: Project, ids: Collection[int] | None = None,
          deadline: float | None = None) -> tuple[str | None, dict[int, list[Change]]]:
    """HEAD's sha and the changes of each marked home-vault entry: recipe marks always, file marks only when git
    answered (sha None otherwise). Git gets BUDGET seconds, or the time left before `deadline`."""
    cited = _recipes(conn, project.home, ids)
    sha, files = _files(conn, project, ids, time.monotonic() + BUDGET if deadline is None else deadline)
    return sha, {entry_id: files.get(entry_id, []) + cited.get(entry_id, [])
                 for entry_id in sorted(files.keys() | cited.keys())}


def own_marks(conn, project: Project, ids: dict[str, Collection[int]] | None = None
              ) -> tuple[str | None, dict[tuple[str, int], list[Change]]]:
    """`marks` of every own vault, keyed by (vault, id), within one git budget; with `ids`, of those entries only.
    The sha is None when git did not answer for one of the vaults."""
    deadline, shas, marked = time.monotonic() + BUDGET, [], {}
    for vault in project.own:
        if ids is not None and not ids.get(vault):
            continue
        sha, changes = marks(conn, project.writing(vault), None if ids is None else ids[vault], deadline)
        shas.append(sha)
        marked.update({(vault, entry_id): found for entry_id, found in changes.items()})
    return (shas[0] if shas and None not in shas else None), marked


def _recipes(conn, vault: str, ids: Collection[int] | None) -> dict[int, list[Change]]:
    """Live recipes a step of which cites a fact or a tool that left delivery, with no evidence set newer than
    that (design N2); the earliest of a supersession and a withdrawal counts."""
    gone = "SELECT s.old_id, s.created_at, s.new_id FROM {v}.supersession s"
    if has_withdrawals(conn, vault):
        gone += " UNION ALL SELECT w.entry_id, w.created_at, NULL FROM {v}.withdrawal w"
    query = f"""
        SELECT e.id, c.cited, g.by FROM {{v}}.entry e
        JOIN (SELECT recipe_id, fact_id AS cited FROM {{v}}.step_fact
              UNION SELECT recipe_id, tool_id FROM {{v}}.recipe_step WHERE tool_id IS NOT NULL) c ON c.recipe_id = e.id
        JOIN (SELECT DISTINCT ON (entry_id) * FROM ({gone}) d (entry_id, at, by) ORDER BY entry_id, at) g
          ON g.entry_id = c.cited
        WHERE {live(conn, vault)} AND (%(ids)s::bigint[] IS NULL OR e.id = ANY(%(ids)s))
          AND NOT EXISTS (SELECT 1 FROM {{v}}.evidence ev WHERE ev.entry_id = e.id AND ev.created_at > g.at)
        ORDER BY e.id, c.cited"""
    marked: dict[int, list[Change]] = {}
    for entry_id, cited, by in conn.execute(vsql(vault, query), {"as_of": None, "ids": None if ids is None
                                                                  else list(ids)}).fetchall():
        marked.setdefault(entry_id, []).append(
            Change(f"{vault}#{cited}", False, f"superseded by {vault}#{by}" if by is not None else "withdrawn"))
    return marked


def _files(conn, project: Project, ids: Collection[int] | None,
           deadline: float) -> tuple[str | None, dict[int, list[Change]]]:
    """HEAD's sha and the changed paths of each marked home-vault entry; (None, {}) when not checked."""
    repo = repository(project, deadline=deadline)
    if repo is None:
        return None, {}
    vault = project.home
    query = """
        SELECT ev.entry_id, ev.reproduction, ev.quote FROM {v}.evidence ev JOIN {v}.entry e ON e.id = ev.entry_id
        WHERE ev.kind = 'file' AND /*live*/
          AND ev.created_at = (SELECT max(x.created_at) FROM {v}.evidence x WHERE x.entry_id = ev.entry_id)"""
    if ids is not None:
        query += " AND ev.entry_id = ANY(%(ids)s)"
    rows = conn.execute(vsql(vault, query.replace("/*live*/", live(conn, vault))),
                        {"as_of": None, "ids": list(ids or ())}).fetchall()
    quotes = []  # (entry, commit, path, quote)
    for entry_id, reproduction, quote in rows:
        try:
            words = shlex.split(reproduction)
        except ValueError:
            continue
        if len(words) == 3 and words[:2] == ["git", "show"] and ":" in words[2]:
            quotes.append((entry_id, *words[2].split(":", 1), quote))
    try:
        commits = list(dict.fromkeys(commit for _, commit, _, _ in quotes))
        present = {commit for commit, (kind, _) in
                   zip(commits, _objects(repo, [c + "^{commit}" for c in commits], "--batch-check", deadline)
                       if commits else []) if kind == b"commit"}
        quotes = [item for item in quotes if item[1] in present]
        sha = head(repo, deadline=deadline)
        texts = read_at(repo, sha, (path for _, _, path, _ in quotes), deadline=deadline)
    except (OSError, subprocess.SubprocessError, Val3Error, ValueError):
        return None, {}
    changed: dict[int, dict[str, Change]] = {}
    for entry_id, _, path, quote in quotes:
        text = texts[path]
        if text is None or quote not in text:
            changed.setdefault(entry_id, {})[path] = Change(path, text is None)
    return sha, {entry_id: sorted(by_path.values(), key=lambda change: change.path)
                 for entry_id, by_path in changed.items()}
