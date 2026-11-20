"""`val3 status`: whether VAL3 is active in this directory and what it delivered lately. Read only."""
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg

from val3 import __version__, catalogue, indexes, neighbour, pending
from val3.config import CONFIG_FILE, find_config, load_project, state_dir
from val3.db import backing, connect, live, upgraded, vsql
from val3.init import SERVER, hook_events
from val3.mcp import PROTOCOLS
from val3.recheck import BUDGET, own_marks, repository
from val3.views import _one_line, safe


def report(cwd: Path, last: int) -> tuple[str, int]:
    """The status text and the exit code: 1 when the database cannot be read."""
    if find_config(cwd) is None:
        return f"VAL3 is not active here (no {CONFIG_FILE} in {cwd} or its parents)", 0
    project = load_project(cwd)
    lines = [f"project {project.name} · {project.root} · service {project.service}"]
    code = 0
    failed = project.home  # the vault named when one does not exist
    try:
        with connect(project.service, timeout=3) as conn:
            blocks = []
            for failed in project.own:
                blocks.append(_vault(conn, failed, project))
            failed = project.home
            recheck = _recheck(conn, project)
            changes = _changes(conn, project.home)
            counts = indexes.counts(conn, [project.home])
            linked = [_linked(conn, vault) for vault in project.linked]
        (head, to_settle), *specialized = blocks
        lines.append(f"home vault {project.home}: {head} · linked: {', '.join(linked) or 'none'}")
        lines.append(recheck)
        lines.append(to_settle)
        for vault, (head, to_settle) in zip(project.specialized, specialized):
            lines += [f"specialized vault {vault}: {head}", to_settle]
        single = sorted(name for name, n in counts.items() if n == 1)
        lines.append(safe(f"indexes: {len(counts)}" + (f" · with one entry: {', '.join(single[:10])}"
                                                        f"{', …' if len(single) > 10 else ''}" if single else "")))
        lines.append(changes)
    except psycopg.errors.UndefinedTable:
        which = "home" if failed == project.home else "specialized"
        lines.append(f"{which} vault {failed}: does not exist (`val3 vault create {failed}`)")
        code = 1
    except psycopg.Error as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        lines.append(f"home vault {project.home}: database unreachable ({safe(reason)})")
        code = 1
    lines.append(f"search: semantic ({project.embedding.model} at {project.embedding.url})" if project.embedding
                 else f"search: full-text (no [embedding] in {CONFIG_FILE})")
    lines.append(_hooks(project.root))
    lines.append(_tools(project.root))
    queued, failed = len(pending.entries()), len(pending.failed_entries())
    lines.append(f"captures: {queued} queued, {failed} failed (val3 queue)" if queued or failed
                 else "captures: queue empty")
    lines.extend(_prompts(project.name, last))
    return "\n".join(lines), code


def _vault(conn, vault: str, project) -> tuple[str, str]:
    """A vault's entry and review counts, and its "to settle" line."""
    entries = conn.execute(vsql(vault, "SELECT count(*) FROM {v}.entry")).fetchone()[0]
    if upgraded(conn, vault):  # ADR 0012: no review queue; the backing of the live entries instead
        rows = conn.execute(vsql(vault, f"SELECT e.id, {backing()} FROM {{v}}.entry e WHERE {live(conn, vault)} "
                                        "ORDER BY e.id"), {"as_of": None}).fetchall()
        count = {name: sum(b == name for _, b in rows) for name in ("evidence", "attested", "unverified")}
        unverified = [f"{vault}#{entry_id}" for entry_id, b in rows if b == "unverified"]
        head = (f"{entries} entries: {count['evidence']} with evidence, {count['attested']} attested, "
                f"{count['unverified']} unverified" + (f" ({_refs(unverified)})" if unverified else ""))
    waiting = conn.execute(vsql(vault, """
        SELECT count(*) FROM {v}.review_item r
        WHERE NOT EXISTS (SELECT 1 FROM {v}.review_decision d WHERE d.review_id = r.id)""")).fetchone()[0]
    near = sorted(neighbour.open_pairs(conn, vault))
    if neighbour.off(project):
        settle = f"to settle: off ([embedding] neighbour = false{f'; {len(near)} open before' if near else ''})"
    else:
        settle = f"to settle: {len(near)} ({_refs([f'{vault}#{i}' for i in near])})" if near else "to settle: 0"
    return (head if upgraded(conn, vault) else f"{entries} entries, {waiting} pending review"), settle


def _linked(conn, vault: str) -> str:
    """A linked vault, with its age when it is a mirror (design §4.4)."""
    sync = catalogue.last_sync(conn, vault)
    if sync is None:
        return vault
    minutes = int(sync[3] // 60)
    age = f"{minutes} min" if minutes < 60 else f"{minutes // 60} h" if minutes < 1440 else f"{minutes // 1440} d"
    return f"{vault} (mirror, synced {age} ago)"


def _recheck(conn, project) -> str:
    """Recipe marks need no git (design N2); file marks are counted only when git answered."""
    unchecked = (f"{project.root} is not the root of a git repository"
                 if repository(project, deadline=time.monotonic() + BUDGET) is None else None)
    sha, marked = own_marks(conn, project)
    if sha is None and unchecked is None:
        unchecked = f"git failed or took longer than {BUDGET:g} s"
    refs = [f"{vault}#{entry_id}" for vault, entry_id in marked]  # own vaults in order, ids ascending
    if unchecked and not refs:
        return safe(f"to re-check: not checked ({unchecked})")
    if not refs:
        return "to re-check: 0"
    return safe(f"to re-check: {len(refs)} ({_refs(refs)})"
                + (f" · file evidence not checked ({unchecked})" if unchecked else ""))


def _refs(refs: list[str]) -> str:
    return ", ".join(refs[:10]) + (", …" if len(refs) > 10 else "")


def _changes(conn, vault: str) -> str:
    """Supersessions and withdrawals of the last 7 days, from the event log (design N1)."""
    rows = conn.execute(vsql(vault, """
        SELECT action, entry_id FROM {v}.event
        WHERE action IN ('superseded', 'withdrawn') AND at > now() - interval '7 days' ORDER BY id""")).fetchall()
    parts = []
    for action in ("superseded", "withdrawn"):
        refs = [f"{vault}#{entry_id}" for kind, entry_id in rows if kind == action]
        if refs:
            parts.append(f"{len(refs)} {action} ({_refs(refs)})")
    return f"changes (7 days): {', '.join(parts) or 'none'}"


def _hooks(root: Path) -> str:
    """The val3 hooks the harness settings call, by file. Plugin and managed settings are not read."""
    parts, seen = [], set()
    for label, path in ((".claude/settings.json", root / ".claude" / "settings.json"),
                        (".claude/settings.local.json", root / ".claude" / "settings.local.json"),
                        ("~/.claude/settings.json", Path.home() / ".claude" / "settings.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            parts.append(f"{label} unreadable ({type(exc).__name__})")
            continue
        events = hook_events(data)
        if events:
            parts.append(f"{', '.join(events)} in {label}")
            seen.update(events)
    if not parts:
        return "hooks: none (VAL3 is configured but no harness hook calls it)"
    return "hooks: " + "; ".join(parts) + ("" if "capture" in seen else " · no capture")


def _json_file(path: Path) -> object:
    """The JSON in `path`, None when it is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def _tools(root: Path) -> str:
    """Whether .mcp.json names the val3 server `init` writes and this installation's server answers `initialize`
    within 3 s. The command in the file is never run. Says nothing about the database or the client's approval."""
    data = _json_file(root / ".mcp.json")
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    server = servers.get("val3") if isinstance(servers, dict) else None
    if server is None:
        shared, local = (_json_file(root / ".claude" / name) for name in ("settings.json", "settings.local.json"))
        if hook_events(local) and not hook_events(shared):
            return 'memory tools: local set-up (see "claude mcp list")'
        return "memory tools: not configured"
    if server != SERVER:
        return "memory tools: configured with another command (not run)"
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": PROTOCOLS[0], "capabilities": {},
                          "clientInfo": {"name": "val3-status", "version": __version__}}}
    try:
        child = subprocess.run([sys.executable, "-P", "-m", "val3", "mcp"], cwd=root, capture_output=True,
                               input=json.dumps(request).encode() + b"\n", timeout=3)
    except subprocess.TimeoutExpired:
        return "memory tools: configured, not answering (no answer within 3 s)"
    except OSError as exc:
        return safe(f"memory tools: configured, not answering ({_one_line(exc.strerror or type(exc).__name__)})")
    try:
        answer = json.loads((child.stdout.splitlines() or [b""])[0])
    except (ValueError, RecursionError):
        answer = None
    result = answer.get("result") if isinstance(answer, dict) else None
    info = result.get("serverInfo") if isinstance(result, dict) else None
    if (not isinstance(answer, dict) or answer.get("jsonrpc") != "2.0" or type(answer.get("id")) is not int or answer["id"] != 1 or not isinstance(info, dict)
            or info.get("name") != "val3"):
        return f"memory tools: configured, not answering (no answer to initialize, exit code {child.returncode})"
    return "memory tools: configured, answering"


def _prompts(name: str, last: int) -> list[str]:
    path = state_dir() / "prompts" / f"{re.sub(r'[^A-Za-z0-9_.-]', '-', name)}.jsonl"
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        raw = []
    rows = []
    for line in raw[-last:] if last > 0 else []:
        try:
            record = json.loads(line)
            at = datetime.fromisoformat(record["at"]).astimezone().strftime("%m-%d %H:%M")
        except (ValueError, KeyError, TypeError, RecursionError):
            continue
        refs = [str(item.get("ref")) for item in record.get("injected") or [] if isinstance(item, dict)]
        shown = f"{len(refs)} injected" + (f" ({', '.join(refs)})" if refs else "")
        prompt = " ".join(str(record.get("prompt", "")).split())
        prompt = prompt if len(prompt) <= 60 else prompt[:59] + "…"
        rows.append(safe(f"  {at}  {str(record.get('session') or '?')[:8]}  {shown}  {prompt}"))
    return ["last prompts:", *rows] if rows else ["last prompts: none logged"]
