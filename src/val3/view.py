"""`val3 view`: a local, read-only web view of the project's vaults (design 2026-11-15-vault-view-design.md).

One process in the foreground, listening on the loopback address only. The random token in the first path segment
is the credential; the `Host` and `Origin` checks keep pages of other sites out. Every API request reads the
database in its own read-only transaction; nothing here writes."""
import errno
import hmac
import json
import re
import secrets
import signal
import socket
import sys
import threading
import webbrowser
from collections import Counter
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg import IsolationLevel

from val3 import Val3Error, usage
from val3.config import Project
from val3.db import connect

WORKERS = 8               # requests served at once; more connections are answered "busy"
READ_TIMEOUT = 10         # seconds to receive a request, and between reads
REQUEST_TIMEOUT = 30      # seconds for one connection in all
STATEMENT_TIMEOUT_MS = 5000
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'none'")
RESOURCES = {"": ("view.html", "text/html; charset=utf-8"),
             "view.js": ("view.js", "text/javascript; charset=utf-8"),
             "view.css": ("view.css", "text/css; charset=utf-8")}
JSON = "application/json; charset=utf-8"
BUSY = b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


class BadRequest(Exception):
    """A parameter out of range: answered 400 with its message."""


class NotFound(Exception):
    """An endpoint or entry that does not exist: answered 404."""


def inert(data) -> bytes:
    """JSON with no raw `<`, `>`, `&` or non-ASCII character: inert even if a browser sniffed it as markup."""
    text = json.dumps(data, ensure_ascii=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").encode()


def now() -> str:
    return stamp(datetime.now(timezone.utc))


def stamp(at: datetime | None) -> str | None:
    return None if at is None else at.astimezone(timezone.utc).isoformat(timespec="seconds")


class Handler(BaseHTTPRequestHandler):
    server_version = "val3"
    sys_version = ""
    timeout = READ_TIMEOUT

    # ---- refusals, all before any database connection ----

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        if self.route() is None:  # the token first: without it nothing else is told
            self.refuse(404, "not found")
            return False
        if self.command not in ("GET", "HEAD"):
            self.refuse(405, "only GET and HEAD")
            return False
        hosts = self.headers.get_all("Host") or []
        port = self.server.server_address[1]
        if len(hosts) != 1 or hosts[0] not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.refuse(403, "wrong Host")
            return False
        origins = self.headers.get_all("Origin") or []
        if origins and origins != [f"http://{hosts[0]}"]:
            self.refuse(403, "foreign Origin")
            return False
        return True

    def route(self) -> tuple[str, dict[str, list[str]]] | None:
        """(the path after /<token>/, the query) or None when the token is wrong."""
        parts = urlsplit(self.path)
        if not parts.path.startswith("/"):
            return None
        token, slash, rest = parts.path[1:].partition("/")
        if not slash or not hmac.compare_digest(token.encode(), self.server.token.encode()):
            return None
        try:
            query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=bool(parts.query),
                             max_num_fields=20)
        except ValueError:
            query = {"": ["?"]}  # unparseable: refused by the endpoint's parameter check
        return rest, query

    # ---- answers ----

    def do_HEAD(self) -> None:
        self.answer(head=True)

    def do_GET(self) -> None:
        self.answer(head=False)

    def answer(self, head: bool) -> None:
        found = self.route()
        if found is None:
            return self.refuse(404, "not found")
        rest, query = found
        if rest in RESOURCES:
            name, kind = RESOURCES[rest]
            return self.send(200, kind, files("val3").joinpath(name).read_bytes(), head)
        if not rest.startswith("api/") or rest[4:] not in ENDPOINTS:
            return self.refuse(404, "not found")
        if head:  # HEAD runs no query
            return self.send(200, JSON, b"", head)
        status, data = run(self.server.project, ENDPOINTS[rest[4:]], query, self.server)
        self.send(status, JSON, inert(data), head)

    def send(self, status: int, kind: str, body: bytes, head: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def refuse(self, status: int, message: str) -> None:
        self.close_connection = True
        self.send(status, "text/plain; charset=utf-8", (message + "\n").encode(), self.command == "HEAD")

    def send_error(self, code, message=None, explain=None) -> None:
        """The parser's own refusals (bad request line, headers too long): plain text with the same headers."""
        self.refuse(code, "bad request" if code < 500 else "error")

    # ---- the access log: method and status, never the path ----

    def log_request(self, code="-", size="-") -> None:
        method = self.command if self.command in ("GET", "HEAD") else "other"
        print(f"val3 view: {method} {code}", file=sys.stderr, flush=True)

    def log_message(self, format, *args) -> None:
        pass


class Server(HTTPServer):
    """At most WORKERS daemon threads; each connection is closed after REQUEST_TIMEOUT seconds whatever it does."""
    daemon_threads = True

    def __init__(self, port: int, project: Project):
        self.token = secrets.token_hex(32)
        self.project = project
        self.slots = threading.BoundedSemaphore(WORKERS)
        self.cache: dict = {}
        self.cache_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(self, request, client_address) -> None:
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(BUSY)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        threading.Thread(target=self._work, args=(request, client_address), daemon=True).start()

    def _work(self, request, client_address) -> None:
        timer = threading.Timer(REQUEST_TIMEOUT, _cut, (request,))
        timer.daemon = True
        timer.start()
        try:
            self.finish_request(request, client_address)
        except Exception:
            pass  # a broken connection: nothing to answer
        finally:
            timer.cancel()
            self.shutdown_request(request)
            self.slots.release()

    def handle_error(self, request, client_address) -> None:
        pass


def _cut(request) -> None:
    try:
        request.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def run(project: Project, endpoint, query: dict[str, list[str]], server) -> tuple[int, dict]:
    """One API request: its parameters checked, then one connection in a read-only repeatable-read transaction
    with a statement timeout, closed before the answer is written."""
    try:
        params = endpoint.params(query)
    except BadRequest as exc:
        return 400, {"at": now(), "error": str(exc)}
    try:
        conn = connect(project.service, timeout=5, statement_timeout_ms=STATEMENT_TIMEOUT_MS)
    except psycopg.Error as exc:
        return 503, {"at": now(), "error": f"cannot connect to the database: {_first_line(exc)}"}
    try:
        conn.autocommit = False
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        conn.execute("SELECT 1")  # the transaction is open: each vault's `conn.transaction()` is a savepoint
        data = endpoint.read(conn, project, params, server)
        return 200, {"at": now(), **data}
    except NotFound as exc:
        return 404, {"at": now(), "error": str(exc)}
    except psycopg.Error as exc:
        return 503, {"at": now(), "error": f"the database failed: {_first_line(exc)}"}
    finally:
        try:
            conn.rollback()
        except psycopg.Error:
            pass
        conn.close()


def _first_line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


class Endpoint:
    """An API endpoint: the parameters it accepts and how it reads them."""

    def __init__(self, read, **checks):
        self.read = read
        self.checks = checks  # name -> function(text) -> value, raising BadRequest

    def params(self, query: dict[str, list[str]]) -> dict:
        unknown = set(query) - set(self.checks)
        if unknown:
            raise BadRequest(f"unknown parameter: {sorted(unknown)[0][:40]}")
        values = {}
        for name, check in self.checks.items():
            given = query.get(name)
            if given is not None and len(given) != 1:
                raise BadRequest(f"{name} given twice")
            values[name] = check(given[0] if given else None)
        return values


KINDS = ("fact", "recipe", "preference", "decision", "tool")
STATES = {"live": "f.live", "superseded": "f.superseded", "withdrawn": "f.withdrawn",
          "no-evidence": "NOT f.evidenced", "approved-by-person": "f.by_person",
          "unverified": "f.live AND NOT f.evidenced AND NOT f.by_person"}
VIAS = ("capture", "save", "import", "tool-add", "not-recorded")
SORTS = {"recorded": lambda r: (r["recorded"], r["vault"], int(r["id"])),
         "ref": lambda r: (r["vault"], int(r["id"])),
         "title": lambda r: (r["title"].casefold(), r["vault"], int(r["id"])),
         "kind": lambda r: (r["kind"], r["vault"], int(r["id"])),
         "state": lambda r: (_state_rank(r), r["vault"], int(r["id"]))}
PAGE = 200            # rows of one page of entries
MAX_PAGE = 1000
MAX_TEXT = 200        # characters of a literal search
STATEMENT_CUT = 300   # characters of a statement in a list
QUOTE_CUT = 4000      # characters of an evidence quote or reproduction in the entry panel
MAX_EVENTS = 500
MAX_NAMES = 500
NO_INDEX = "(none)"   # cannot be an index name
UNKNOWN = "(unknown)" # an entry recorded without a session id
MECHANICAL = "val3 (mechanical)"
# approvals that admit nothing: a notice acknowledged, a withdrawal carried out
ACKNOWLEDGED = "ARRAY['supersession', 'recipe-cites-superseded', 'withdrawn', 'withdrawal']"


def _state_rank(row: dict) -> int:
    return 0 if row["live"] else 1 + row["superseded"] + 2 * row["withdrawn"]


def _role(project: Project, vault: str) -> str:
    return "home" if vault == project.home else "specialized" if vault in project.specialized else "linked"


def each_vault(conn, project: Project, vaults, read) -> list[dict]:
    """[{name, role, ok, ...read(vault) or reason}] for each vault, each in its own savepoint: a vault that fails
    does not fail the others."""
    from val3.mcp import _failure
    out = []
    for vault in vaults:
        head = {"name": vault, "role": _role(project, vault)}
        try:
            with conn.transaction():
                why = _failure(conn, vault)
                if why:
                    out.append({**head, "ok": False, "reason": why})
                    continue
                out.append({**head, "ok": True, **read(vault)})
        except psycopg.errors.QueryCanceled:
            out.append({**head, "ok": False, "reason": "the query took too long"})
        except psycopg.OperationalError:
            raise
        except psycopg.Error as exc:
            out.append({**head, "ok": False, "reason": _first_line(exc)})
    return out


def facts(conn, vault: str) -> str:
    """SQL subquery `f`: every entry of `vault` with its state, evidence, approval, channel and indexes; the text
    holds `{v}` for vsql and takes %(as_of)s (null) for `live`."""
    from val3.db import has_withdrawals, live, upgraded
    from val3.indexes import resolved
    # ADR 0012: on an upgraded vault a person's vouching is the attestation row, on an older one an approved review
    attested = "EXISTS (SELECT 1 FROM {v}.attestation t WHERE t.entry_id = e.id)" if upgraded(conn, vault) else "false"
    withdrawn = ("EXISTS (SELECT 1 FROM {v}.withdrawal w WHERE w.entry_id = e.id)" if has_withdrawals(conn, vault)
                 else "false")
    return f"""(SELECT e.id, e.kind, e.title, e.statement, e.keywords, e.origin_project, e.origin_session,
               e.origin_machine, e.created_at, {live(conn, vault)} AS live,
               EXISTS (SELECT 1 FROM {{v}}.supersession s WHERE s.old_id = e.id) AS superseded,
               {withdrawn} AS withdrawn, ev.kinds IS NOT NULL AS evidenced, ev.kinds,
               (coalesce(d.by_person, false) OR {attested}) AS by_person, a.via, ix.names AS indexes
        FROM {{v}}.entry e
        LEFT JOIN (SELECT entry_id, array_agg(DISTINCT kind ORDER BY kind) AS kinds FROM {{v}}.evidence
                   GROUP BY entry_id) ev ON ev.entry_id = e.id
        LEFT JOIN (SELECT d.entry_id, bool_or(d.decided_by <> '{MECHANICAL}') AS by_person
                   FROM {{v}}.review_decision d JOIN {{v}}.review_item r ON r.id = d.review_id
                   WHERE d.decision = 'approved' AND d.entry_id IS NOT NULL AND r.reason <> ALL({ACKNOWLEDGED})
                   GROUP BY d.entry_id) d ON d.entry_id = e.id
        LEFT JOIN (SELECT DISTINCT ON (entry_id) entry_id, detail->>'via' AS via FROM {{v}}.event
                   WHERE action = 'admitted' AND entry_id IS NOT NULL ORDER BY entry_id, id) a ON a.entry_id = e.id
        LEFT JOIN (SELECT r.entry_id, array_agg(r.name ORDER BY r.name) AS names FROM {resolved(conn, vault)} r
                   GROUP BY r.entry_id) ix ON ix.entry_id = e.id) f"""


# `|| ''`: left() on a compressed value reads a byte slice that can end inside a character, an error on PostgreSQL 16
ROW = """f.id, f.kind, f.title, left(f.statement || '', %(cut)s + 1), f.keywords, f.origin_project, f.origin_session,
         f.origin_machine, f.created_at, f.live, f.superseded, f.withdrawn, f.kinds, f.by_person, f.via, f.indexes"""


def row(vault: str, values) -> dict:
    (entry_id, kind, title, statement, keywords, project, session, machine, created, is_live, superseded, withdrawn,
     kinds, by_person, via, names) = values
    return {"ref": f"{vault}#{entry_id}", "vault": vault, "id": str(entry_id), "kind": kind, "title": title,
            "statement": statement[:STATEMENT_CUT], "statement_cut": len(statement) > STATEMENT_CUT,
            "keywords": keywords, "project": project, "session": session, "machine": machine,
            "recorded": stamp(created), "live": is_live, "superseded": superseded, "withdrawn": withdrawn,
            "evidence": kinds or [], "approved_by_person": by_person, "via": via, "indexes": names or []}


def read_summary(conn, project: Project, params: dict, server) -> dict:
    from val3 import catalogue
    from val3.db import vsql
    from val3.indexes import resolved
    notes = catalogue.notes(conn)

    def one(vault: str) -> dict:
        f = facts(conn, vault)
        p = {"as_of": None}
        counts = conn.execute(vsql(vault, f"""
            SELECT count(*), count(*) FILTER (WHERE f.live), count(*) FILTER (WHERE f.superseded),
                   count(*) FILTER (WHERE f.withdrawn), count(*) FILTER (WHERE NOT f.evidenced),
                   count(*) FILTER (WHERE f.by_person),
                   count(*) FILTER (WHERE f.live AND NOT f.evidenced AND NOT f.by_person),
                   count(*) FILTER (WHERE f.live AND f.evidenced)
            FROM {f}"""), p).fetchone()
        days: dict[str, dict[str, int]] = {}
        for day, via, n in conn.execute(vsql(vault, f"""
                SELECT to_char(f.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD'), coalesce(f.via, 'not-recorded'),
                       count(*)
                FROM {f} GROUP BY 1, 2 ORDER BY 1, 2"""), p):
            days.setdefault(day, {})[via] = n
        per_index = [{"name": name, "entries": n, "live": k} for name, n, k in conn.execute(vsql(vault, f"""
            SELECT r.name, count(*), count(*) FILTER (WHERE f.live)
            FROM {resolved(conn, vault)} r JOIN {f} ON f.id = r.entry_id
            GROUP BY r.name ORDER BY count(*) DESC, r.name"""), p)]
        n, k = conn.execute(vsql(vault, f"""
            SELECT count(*), count(*) FILTER (WHERE f.live) FROM {f} WHERE f.indexes IS NULL"""), p).fetchone()
        per_index.append({"name": None, "entries": n, "live": k})
        recent = [{"ref": f"{vault}#{i}", "title": t} for i, t in conn.execute(vsql(vault, """
            SELECT e.id, e.title FROM {v}.supersession s JOIN {v}.entry e ON e.id = s.old_id
            WHERE s.created_at > now() - interval '7 days' ORDER BY s.created_at DESC, e.id DESC LIMIT 5"""))]
        unverified = [{"ref": f"{vault}#{i}", "title": t} for i, t in conn.execute(vsql(vault, f"""
            SELECT f.id, f.title FROM {f} WHERE f.live AND NOT f.evidenced AND NOT f.by_person
            ORDER BY f.id DESC LIMIT 5"""), p)]
        note = notes.get(vault)
        sync = catalogue.last_sync(conn, vault)
        return {"description": note and note.description,
                "mirror": sync and {"source": sync[1], "synced_at": stamp(sync[2])},
                "counts": dict(zip(("entries", "live", "superseded", "withdrawn", "no_evidence",
                                    "approved_by_person", "unverified", "live_with_evidence"), counts)),
                "admitted_per_day": [{"day": day, "via": via} for day, via in days.items()],
                "indexes": per_index, "superseded_this_week": recent, "unverified_latest": unverified}

    return {"project": project.name, "vaults": each_vault(conn, project, project.vaults, one)}


def _filters(params: dict) -> str:
    where = ["(%(q)s::text IS NULL OR strpos(lower(f.title || ' ' || f.statement || ' ' || f.keywords), "
             "lower(%(q)s)) > 0)",
             "(%(kind)s::text IS NULL OR f.kind = %(kind)s)",
             "(%(from)s::date IS NULL OR (f.created_at AT TIME ZONE 'UTC')::date >= %(from)s)",
             "(%(to)s::date IS NULL OR (f.created_at AT TIME ZONE 'UTC')::date <= %(to)s)",
             "(%(via)s::text IS NULL OR coalesce(f.via, 'not-recorded') = %(via)s)"]
    if params["state"]:
        where.append(STATES[params["state"]])
    if params["session"] == UNKNOWN:
        where.append("f.origin_session IS NULL")
    elif params["session"]:
        where.append("f.origin_session = %(session)s")
    if params["index"] == NO_INDEX:
        where.append("f.indexes IS NULL")
    elif params["index"]:
        where.append("%(index)s = ANY(f.indexes)")
    return " AND ".join(where)


def read_entries(conn, project: Project, params: dict, server) -> dict:
    from val3.db import vsql
    from val3.indexes import canonical
    rows: list[dict] = []
    condition = _filters(params)

    def one(vault: str) -> dict:
        index = params["index"]
        if index and index != NO_INDEX:
            index = canonical(conn, vault, index)
        # ponytail: every matching row is read and sorted here; fine for thousands, page in SQL beyond that
        found = [row(vault, values) for values in conn.execute(vsql(vault, f"""
            SELECT {ROW} FROM {facts(conn, vault)} WHERE {condition}"""),
            {**params, "index": index, "as_of": None, "cut": STATEMENT_CUT})]
        rows.extend(found)
        return {"matched": len(found)}

    if params["vault"] and params["vault"] not in project.vaults:
        raise NotFound("not one of this project's vaults")
    vaults = each_vault(conn, project, [params["vault"]] if params["vault"] else project.vaults, one)
    _counted(project, rows)
    sort = params["sort"] or "-recorded"
    rows.sort(key=SORTS[sort.lstrip("-")], reverse=sort.startswith("-"))
    start = (params["page"] - 1) * PAGE
    return {"vaults": vaults, "total": len(rows), "page": params["page"], "page_size": PAGE,
            "rows": rows[start:start + PAGE]}


def _clip(text: str | None) -> tuple[str | None, bool]:
    if text is None or len(text) <= QUOTE_CUT:
        return text, False
    return text[:QUOTE_CUT], True


def read_entry(conn, project: Project, params: dict, server) -> dict:
    from val3.db import has_neighbours, has_withdrawals, vsql
    from val3.mcp import _failure
    vault, entry_id = params["ref"]
    if vault not in project.vaults:
        raise NotFound(f"vault {vault} is not one of this project's vaults")
    why = _failure(conn, vault)
    if why:
        raise NotFound(f"vault {vault} is {why}")
    q = lambda text, *args: conn.execute(vsql(vault, text), args or (entry_id,)).fetchall()
    found = conn.execute(vsql(vault, f"SELECT {ROW}, f.statement FROM {facts(conn, vault)} WHERE f.id = %(id)s"),
                         {"as_of": None, "cut": STATEMENT_CUT, "id": entry_id}).fetchone()
    if found is None:
        raise NotFound(f"no entry {vault}#{entry_id}")
    entry = {**row(vault, found[:-1]), "statement": found[-1], "statement_cut": False, "role": _role(project, vault)}
    ref = lambda i: f"{vault}#{i}"
    evidence = []
    for kind, reproduction, quote, session, at in q("""
            SELECT kind, reproduction, quote, session_id, created_at FROM {v}.evidence WHERE entry_id = %s
            ORDER BY id"""):
        reproduction, reproduction_cut = _clip(reproduction)
        quote, quote_cut = _clip(quote)
        evidence.append({"kind": kind, "reproduction": reproduction, "reproduction_cut": reproduction_cut,
                         "quote": quote, "quote_cut": quote_cut, "session": session, "recorded": stamp(at)})
    newer = q("SELECT new_id, created_at FROM {v}.supersession WHERE old_id = %s")
    older = q("SELECT old_id, created_at FROM {v}.supersession WHERE new_id = %s ORDER BY old_id")
    withdrawal = None
    if has_withdrawals(conn, vault):
        for cause, reason, by, at, items in q("""
                SELECT cause, reason, withdrawn_by, created_at, to_jsonb(w)->'evidence' FROM {v}.withdrawal w
                WHERE entry_id = %s"""):
            withdrawal = {"cause": cause, "reason": reason, "by": by, "at": stamp(at), "evidence": items or []}
    steps = [{"position": position, "action": action, "tool": tool and ref(tool), "facts": [ref(i) for i in ids]}
             for position, action, tool, ids in q("""
                 SELECT s.position, s.action, s.tool_id,
                        coalesce(array_agg(x.fact_id ORDER BY x.fact_id) FILTER (WHERE x.fact_id IS NOT NULL),
                                 ARRAY[]::bigint[])
                 FROM {v}.recipe_step s LEFT JOIN {v}.step_fact x ON x.recipe_id = s.recipe_id
                      AND x.position = s.position
                 WHERE s.recipe_id = %s GROUP BY s.position, s.action, s.tool_id ORDER BY s.position""")]
    cited_by = [ref(i) for (i,) in q("""
        SELECT recipe_id FROM {v}.step_fact WHERE fact_id = %s UNION
        SELECT recipe_id FROM {v}.recipe_step WHERE tool_id = %s ORDER BY 1""", entry_id, entry_id)]
    tool = [{"type": t, "environment": environment, "priority": priority} for t, environment, priority in
            q("SELECT tool_type, environment, priority FROM {v}.tool WHERE entry_id = %s")]
    pairs = []
    if has_neighbours(conn, vault):
        pairs = [{"ref": ref(other), "score": round(score, 4), "outcome": outcome, "settled_by": by}
                 for other, score, outcome, by in q("""
                     SELECT CASE WHEN n.entry_id = %s THEN n.other_id ELSE n.entry_id END, n.score, s.outcome,
                            s.settled_by
                     FROM {v}.neighbour n LEFT JOIN {v}.settlement s ON s.entry_id = n.entry_id
                          AND s.other_id = n.other_id
                     WHERE n.entry_id = %s OR n.other_id = %s ORDER BY 1""", entry_id, entry_id, entry_id)]
    events = [{"id": str(i), "at": stamp(at), "action": action, "project": p, "session": session,
               "machine": machine, "review": review and str(review), "detail": detail}
              for i, at, action, p, session, machine, review, detail in q(f"""
                  SELECT id, at, action, project, session_id, machine, review_id, detail FROM {{v}}.event
                  WHERE entry_id = %s ORDER BY id LIMIT {MAX_EVENTS}""")]
    decisions = [{"review": str(i), "reason": reason, "decision": decision, "by": by, "at": stamp(at)}
                 for i, reason, decision, by, at in q("""
                     SELECT d.review_id, r.reason, d.decision, d.decided_by, d.created_at FROM {v}.review_decision d
                     JOIN {v}.review_item r ON r.id = d.review_id WHERE d.entry_id = %s ORDER BY d.review_id""")]
    _counted(project, [entry])
    return {"entry": {**entry, "evidence_items": evidence,
                      "superseded_by": [{"ref": ref(i), "at": stamp(at)} for i, at in newer],
                      "supersedes": [{"ref": ref(i), "at": stamp(at)} for i, at in older],
                      "withdrawal": withdrawal, "steps": steps, "cited_by": cited_by, "tool": tool[0] if tool else None,
                      "close_pairs": pairs, "decisions": decisions, "events": events}}


def extractor_runs() -> dict[str, dict]:
    """Session id -> the drainer's finished runs on this machine (extract.log): runs, calls, cost, skipped."""
    from val3.config import state_dir
    runs: dict[str, dict] = {}
    try:
        with open(state_dir() / "extract.log", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                    if record.get("outcome") != "done" or not isinstance(record.get("session"), str):
                        continue
                    one = runs.setdefault(record["session"], {"runs": 0, "calls": 0, "cost_usd": 0.0, "skipped": 0,
                                                              "last": None})
                    one["runs"] += 1
                    one["calls"] += int(record.get("calls") or 0)
                    one["cost_usd"] += float(record.get("cost_usd") or 0)
                    one["skipped"] += int(record.get("skipped") or 0)
                    one["last"] = max(filter(None, (one["last"], str(record.get("at") or "") or None)), default=None)
                except (ValueError, TypeError, AttributeError, RecursionError):
                    continue  # an incomplete or foreign line
    except OSError:
        return {}
    for one in runs.values():
        one["cost_usd"] = round(one["cost_usd"], 6)
    return runs


def read_sessions(conn, project: Project, params: dict, server) -> dict:
    from val3.db import vsql
    sessions: dict[tuple, dict] = {}

    def one(vault: str) -> dict:
        rows = conn.execute(vsql(vault, f"""
            SELECT f.origin_project, f.origin_machine, f.origin_session, coalesce(f.via, 'not-recorded'),
                   min(f.created_at), max(f.created_at),
                   array_agg(DISTINCT to_char(f.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD')), count(*),
                   count(*) FILTER (WHERE f.superseded), count(*) FILTER (WHERE f.withdrawn)
            FROM {facts(conn, vault)} GROUP BY 1, 2, 3, 4"""), {"as_of": None}).fetchall()
        for p, machine, session, via, first, last, days, n, superseded, withdrawn in rows:
            one = sessions.setdefault((p, machine, session), {
                "project": p, "machine": machine, "session": session, "first": first, "last": last, "days": set(),
                "entries": 0, "via": {}, "superseded": 0, "withdrawn": 0, "vaults": []})
            one["first"], one["last"] = min(one["first"], first), max(one["last"], last)
            one["days"].update(days)
            one["entries"] += n
            one["via"][via] = one["via"].get(via, 0) + n
            one["superseded"] += superseded
            one["withdrawn"] += withdrawn
            if vault not in one["vaults"]:
                one["vaults"].append(vault)
        return {"sessions": len({key for key in sessions})}

    vaults = each_vault(conn, project, project.vaults, one)
    runs = extractor_runs()
    out = []
    for one in sorted(sessions.values(), key=lambda s: (s["last"], s["session"] or ""), reverse=True):
        out.append({**one, "first": stamp(one["first"]), "last": stamp(one["last"]), "days": sorted(one["days"]),
                    "extractor": runs.get(one["session"]) if one["session"] else None})
    return {"vaults": [{k: v for k, v in vault.items() if k != "sessions"} for vault in vaults],
            "extractor_log": bool(runs), "sessions": out}


_TOKEN = re.compile(r"[^\s\"'`()\[\]{}<>,;!?*|=]+")
_SPECIAL = re.compile(r"[0-9_./\-]")
_NUMBER = re.compile(r"[0-9.,:/\-]+")
_TRAILING = ".:-"  # sentence punctuation after a name


def names_in(text: str) -> set[str]:
    """Exact names in a text (design §10): tokens of at least 4 characters holding a digit, underscore, dot, slash or
    hyphen, not a date or a plain number; no model."""
    found = set()
    for token in _TOKEN.findall(text):
        token = token.rstrip(_TRAILING)
        if len(token) >= 4 and _SPECIAL.search(token) and not _NUMBER.fullmatch(token):
            found.add(token)
    return found


def read_names(conn, project: Project, params: dict, server) -> dict:
    from val3.db import vsql
    rows: list[dict] = []
    names: dict[str, dict] = {}
    text = params["q"]

    def one(vault: str) -> dict:
        if text is not None:
            found = [row(vault, values) for values in conn.execute(vsql(vault, f"""
                SELECT {ROW} FROM {facts(conn, vault)}
                WHERE strpos(lower(f.title || ' ' || f.statement || ' ' || f.keywords), lower(%(q)s)) > 0
                   OR EXISTS (SELECT 1 FROM {{v}}.evidence x WHERE x.entry_id = f.id
                              AND strpos(lower(x.quote || ' ' || x.reproduction), lower(%(q)s)) > 0)"""),
                {"q": text, "as_of": None, "cut": STATEMENT_CUT})]
            rows.extend(found)
            return {"matched": len(found)}
        for entry_id, title, statement, is_live, created in conn.execute(vsql(vault, f"""
                SELECT f.id, f.title, f.statement, f.live, f.created_at FROM {facts(conn, vault)}"""),
                {"as_of": None}):
            for name in names_in(f"{title}\n{statement}"):
                one = names.setdefault(name, {"name": name, "entries": 0, "not_live": 0, "last": created})
                one["entries"] += 1
                one["not_live"] += not is_live
                one["last"] = max(one["last"], created)
        return {}

    vaults = each_vault(conn, project, project.vaults, one)
    if text is not None:
        rows.sort(key=SORTS["recorded"], reverse=True)
        return {"vaults": vaults, "q": text, "total": len(rows), "rows": rows[:PAGE]}
    shared = sorted((one for one in names.values() if one["entries"] >= 2), key=lambda n: (-n["entries"], n["name"]))
    return {"vaults": vaults, "names": [{**one, "last": stamp(one["last"])} for one in shared[:MAX_NAMES]],
            "more": max(0, len(shared) - MAX_NAMES)}


MAP_NODES = 1500        # the most recent entries of one vault
MAP_NEAREST = 3
MAP_CHUNK = 250         # source entries per nearest-neighbour statement, each well within the statement timeout
MAP_CACHE = 600         # seconds a vault's map is kept


def read_map(conn, project: Project, params: dict, server) -> dict:
    """Nodes and edges for the most recent MAP_NODES entries of one vault: supersessions, recipe citations and each
    entry's MAP_NEAREST nearest entries by vector, within the one vector space most of them have; ties by entry id.
    Kept for MAP_CACHE seconds per vault, the first answer's time given as `computed_at`."""
    import time
    from val3.db import vsql
    vault = params["vault"] or project.home
    if vault not in project.vaults:
        raise NotFound("not one of this project's vaults")
    with server.cache_lock:
        kept = server.cache.get(("map", vault))
    if kept and time.monotonic() - kept[0] < MAP_CACHE:
        return kept[1]

    def one(vault: str) -> dict:
        p = {"as_of": None, "cut": STATEMENT_CUT, "limit": MAP_NODES}
        total = conn.execute(vsql(vault, "SELECT count(*) FROM {v}.entry")).fetchone()[0]
        nodes = [row(vault, values) for values in conn.execute(vsql(vault, f"""
            SELECT {ROW} FROM {facts(conn, vault)} ORDER BY f.id DESC LIMIT %(limit)s"""), p)]
        ids = [int(n["id"]) for n in nodes]
        space = conn.execute(vsql(vault, """
            SELECT model, digest, text_version, dims, count(*) FROM {v}.embedding WHERE entry_id = ANY(%s)
            GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC, 1, 2, 3, 4 LIMIT 1"""), (ids,)).fetchone() if ids else None
        edges = [{"from": f"{vault}#{new}", "to": f"{vault}#{old}", "type": "supersedes"}
                 for old, new in conn.execute(vsql(vault, """
                     SELECT old_id, new_id FROM {v}.supersession WHERE old_id = ANY(%(ids)s) AND new_id = ANY(%(ids)s)
                     ORDER BY new_id, old_id"""), {"ids": ids})]
        edges += [{"from": f"{vault}#{recipe}", "to": f"{vault}#{cited}", "type": "cites"}
                  for recipe, cited in conn.execute(vsql(vault, """
                      SELECT DISTINCT recipe_id, fact_id FROM {v}.step_fact
                      WHERE recipe_id = ANY(%(ids)s) AND fact_id = ANY(%(ids)s)
                      UNION SELECT DISTINCT recipe_id, tool_id FROM {v}.recipe_step
                      WHERE recipe_id = ANY(%(ids)s) AND tool_id = ANY(%(ids)s) ORDER BY 1, 2"""), {"ids": ids})]
        with_vector = []
        if space:
            model, digest, text_version, dims, _ = space
            sp = {"model": model, "digest": digest, "text_version": text_version, "dims": dims, "ids": ids}
            with_vector = [i for (i,) in conn.execute(vsql(vault, """
                SELECT entry_id FROM {v}.embedding WHERE model = %(model)s AND digest = %(digest)s
                AND text_version = %(text_version)s AND dims = %(dims)s AND entry_id = ANY(%(ids)s)
                ORDER BY entry_id"""), sp)]
            for start in range(0, len(with_vector), MAP_CHUNK):
                for source, near, distance in conn.execute(vsql(vault, f"""
                        WITH m AS MATERIALIZED (
                            SELECT entry_id, vector FROM {{v}}.embedding WHERE model = %(model)s
                            AND digest = %(digest)s AND text_version = %(text_version)s AND dims = %(dims)s
                            AND entry_id = ANY(%(ids)s))
                        SELECT a.entry_id, n.entry_id, n.d FROM m a CROSS JOIN LATERAL (
                            SELECT b.entry_id, a.vector <=> b.vector AS d FROM m b WHERE b.entry_id <> a.entry_id
                            ORDER BY 2, b.entry_id LIMIT {MAP_NEAREST}) n
                        WHERE a.entry_id = ANY(%(chunk)s) ORDER BY a.entry_id, n.d, n.entry_id"""),
                        {**sp, "chunk": with_vector[start:start + MAP_CHUNK]}):
                    edges.append({"from": f"{vault}#{source}", "to": f"{vault}#{near}", "type": "near",
                                  "similarity": round(1 - distance, 4)})
        return {"total": total, "shown": len(nodes), "left_out": total - len(nodes),
                "without_vector": len(nodes) - len(with_vector),
                "space": space and dict(zip(("model", "digest", "text_version", "dims"), space[:4])),
                "nodes": nodes, "edges": edges}

    [answer] = each_vault(conn, project, [vault], one)
    data = {"vault": answer, "computed_at": now()}
    if answer["ok"]:
        with server.cache_lock:
            server.cache[("map", vault)] = (time.monotonic(), data)
    return data


def usage_counts(project: Project) -> tuple[list[dict] | None, Counter, Counter]:
    """(this machine's usage lines of the window or None, times each ref was returned by a search, times read)."""
    lines = usage.lines(project.name)
    returned, opened = Counter(), Counter()
    for line in lines or ():
        (returned if line["surface"] in usage.SEARCHES else opened).update(r for r in line["refs"] if isinstance(r, str))
    return lines, returned, opened


def _counted(project: Project, rows: list[dict]) -> None:
    """Each row's `returned` and `read` on this machine in the window: 0 is "not seen", None "not recorded"."""
    lines, returned, opened = usage_counts(project)
    for one in rows:
        one["returned"] = None if lines is None else returned[one["ref"]]
        one["read"] = None if lines is None else opened[one["ref"]]


def read_usage(conn, project: Project, params: dict, server) -> dict:
    from val3.db import live, vsql
    lines, returned, opened = usage_counts(project)

    def one(vault: str) -> dict:
        ids = [i for (i,) in conn.execute(vsql(vault, f"SELECT e.id FROM {{v}}.entry e WHERE {live(conn, vault)}"),
                                          {"as_of": None})]
        return {"live": len(ids), "live_not_seen": sum(returned[f"{vault}#{i}"] == 0 for i in ids)}

    vaults = each_vault(conn, project, project.vaults, one)
    top = []
    for ref, times in sorted(returned.items(), key=lambda item: (-item[1], item[0]))[:10]:
        vault, entry_id = ref.split("#", 1)
        title = None
        if vault in project.vaults and entry_id.isdigit() and any(v["name"] == vault and v["ok"] for v in vaults):
            found = conn.execute(vsql(vault, "SELECT title FROM {v}.entry WHERE id = %s"), (int(entry_id),)).fetchone()
            title = found and found[0]
        top.append({"ref": ref, "title": title, "times": times})
    return {"log": lines is not None, "days": usage.KEEP_DAYS, "since": stamp(datetime.fromisoformat(lines[0]["at"]))
            if lines else None, "searches": sum(x["surface"] in usage.SEARCHES for x in lines or ()),
            "reads": sum(x["surface"] not in usage.SEARCHES + usage.WRITES for x in lines or ()),
            "by_surface": dict(Counter(x["surface"] for x in lines or ())),
            "outcomes": dict(Counter(str(x.get("outcome")) for x in lines or ())),
            "vaults": vaults if lines is not None else [{**v, "live_not_seen": None} for v in vaults],
            "most_returned": top, "returned": dict(returned), "read": dict(opened)}


# ---- parameter checks ----

def _text(limit: int):
    def check(value):
        if value is None or value == "":
            return None
        if len(value) > limit or "\x00" in value:
            raise BadRequest(f"at most {limit} characters")
        return value
    return check


def _choice(options):
    def check(value):
        if value is None or value == "":
            return None
        if value not in options:
            raise BadRequest(f"not one of {', '.join(options)}")
        return value
    return check


def _date(value):
    if value is None or value == "":
        return None
    try:
        if len(value) != 10:
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise BadRequest("dates are YYYY-MM-DD") from None


def _page(value):
    if value is None or value == "":
        return 1
    if not value.isdigit() or not 1 <= int(value) <= MAX_PAGE:
        raise BadRequest(f"page is a number from 1 to {MAX_PAGE}")
    return int(value)


def _ref(value):
    from val3.config import parse_ref
    if not value:
        raise BadRequest("ref is required: <vault>#<id>")
    try:
        return parse_ref(value[:100])
    except Val3Error:
        raise BadRequest("ref is <vault>#<id>") from None


def _vault_name(value):
    if value is None or value == "":
        return None
    if len(value) > 63:
        raise BadRequest("not a vault name")
    return value


ENDPOINTS = {
    "summary": Endpoint(read_summary),
    "entries": Endpoint(read_entries, vault=_vault_name, q=_text(MAX_TEXT), kind=_choice(KINDS),
                        index=_text(64), state=_choice(tuple(STATES)), session=_text(200), via=_choice(VIAS),
                        sort=_choice(tuple(SORTS) + tuple("-" + s for s in SORTS)), page=_page,
                        **{"from": _date, "to": _date}),
    "entry": Endpoint(read_entry, ref=_ref),
    "sessions": Endpoint(read_sessions),
    "names": Endpoint(read_names, q=_text(MAX_TEXT)),
    "map": Endpoint(read_map, vault=_vault_name),
    "usage": Endpoint(read_usage),
}


def serve(project: Project, port: int, open_browser: bool) -> int:
    try:
        server = Server(port, project)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise Val3Error(f"port {port} is in use: choose another with --port") from None
        raise Val3Error(f"cannot listen on 127.0.0.1:{port}: {exc.strerror}") from None
    url = f"http://127.0.0.1:{server.server_address[1]}/{server.token}/"
    print(url, flush=True)
    print(f"val3 view: the vaults of {project.name}, read only; whoever has this address reads them. "
          "Ctrl-C stops it.", file=sys.stderr, flush=True)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        if open_browser:  # a browser started in the foreground returns only when it ends
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print("val3 view: stopped", file=sys.stderr, flush=True)
    return 0
