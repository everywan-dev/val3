import json
import os
import select
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager

PARTIAL = ("This memory is partial and may be out of date: it complements the project's documents, it does not "
           "replace them. Whatever it shows, also search the project's files before answering or concluding that "
           "something is not recorded.")

QUOTE = "invoice_count\n-------------\n          42"
OUTPUT = QUOTE + "\n(1 row)"

# the checks of a vault created before plan 3b (no `file` evidence, no `import` review items)
OLD_CHECKS = (
    "ALTER TABLE {v}.evidence DROP CONSTRAINT evidence_kind_check, ADD CONSTRAINT evidence_kind_check "
    "CHECK (kind IN ('query', 'command', 'api', 'test', 'user'))",
    "ALTER TABLE {v}.review_item DROP CONSTRAINT review_item_reason_check, ADD CONSTRAINT review_item_reason_check "
    "CHECK (reason IN ('recipe', 'tool', 'user-confirmation', 'supersession', 'recipe-cites-superseded'))",
)

# the reason check of a vault created before plan 7 (no withdrawal proposals or notices)
OLD_REASONS = ("ALTER TABLE {v}.review_item DROP CONSTRAINT review_item_reason_check, "
               "ADD CONSTRAINT review_item_reason_check CHECK (reason IN ('recipe', 'tool', 'user-confirmation', "
               "'supersession', 'recipe-cites-superseded', 'import'))")


def fact(**changes):
    candidate = {
        "kind": "fact",
        "title": "April has 42 invoices",
        "statement": "The ERP holds 42 invoices dated April 2026.",
        "keywords": ["invoices", "facturas"],
        "indexes": ["billing"],
        "evidence": [{"kind": "query",
                      "reproduction": "SELECT count(*) AS invoice_count FROM invoice WHERE date >= '2026-04-01'",
                      "quote": QUOTE}],
    }
    candidate.update(changes)
    return candidate


def make_vault(env):
    env.config()
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 0, result.stderr


def before_0012(env, vault=None):
    """Make `vault` (default the env's) a vault created before ADR 0012: no attestation table, decisions approved
    or rejected only. It keeps the review queue until `val3 vault upgrade`."""
    vault = vault or env.vault
    env.sql(f"DROP TABLE {vault}.attestation")
    env.sql(f"ALTER TABLE {vault}.review_decision DROP CONSTRAINT review_decision_decision_check, "
            "ADD CONSTRAINT review_decision_decision_check CHECK (decision IN ('approved', 'rejected'))")


def old_vault(env):
    """The env's vault, created before ADR 0012."""
    make_vault(env)
    before_0012(env)


def embedding_vault(env, url, **settings):
    """A vault whose project has semantic search on, served by `url`."""
    make_vault(env)
    env.config(embedding={"url": url, **settings})


def save(env, candidate, transcript, *extra):
    return env.run("save", "--transcript", str(transcript), "--session", env.session, *extra,
                   input=json.dumps(candidate))


TOOL = {"kind": "tool", "title": "code-graph", "statement": "Answers which module overrides a method.",
        "keywords": ["overrides"],
        "tool": {"type": "cli", "environment": "https://example.org/code-graph.git", "priority": 5}}


def recipe(fact_ref, **changes):
    candidate = {
        "kind": "recipe",
        "title": "Count the invoices of a month",
        "statement": "How to count the invoices of one month in the ERP.",
        "keywords": ["invoices", "facturas"],
        "recipe": {"parameters": [{"name": "month", "description": "YYYY-MM"}],
                   "steps": [{"action": "Run the invoice count query for the month", "facts": [fact_ref]},
                             {"action": "Report the count"}],
                   "examples": [{"parameters": {"month": "2026-04"}, "result": "42 invoices"}]},
    }
    candidate.update(changes)
    return candidate


def add_service(env, name, dbname, user="postgres"):
    """A pg_service.conf entry for another database of the test server."""
    with open(env.root / "pg_service.conf", "a") as handle:
        handle.write(f"[{name}]\nhost=127.0.0.1\nport={env.port}\nuser={user}\ndbname={dbname}\nconnect_timeout=3\n\n")


def legacy(env, candidate, reason="user-confirmation", entry_id=None):
    """A review item queued before automatic admission (design R1): it stays for a person to decide.
    `candidate` is an entry candidate, or the payload of a withdrawal proposal with its `entry_id`."""
    defaults = {} if reason == "withdrawal" else {"keywords": [], "indexes": [], "supersedes": None,
                                                  "recipe": None, "tool": None}
    payload = {**defaults, **candidate,
               "evidence": [{"reproduction": "", **item} for item in candidate.get("evidence", [])]}
    env.sql(f"INSERT INTO {env.vault}.review_item (reason, payload, content_hash, entry_id, origin_project, "
            "origin_machine) VALUES (%s, %s::jsonb, %s, %s, 'project', 'm')",
            (reason, json.dumps(payload), json.dumps([reason, payload["title"], payload.get("statement", "")]),
             entry_id))


def setup(env, embedding=None):
    """The home vault and `<home>_s`, both created with `vault create`; val3.toml names `<home>_s` specialized."""
    env.config()
    special = f"{env.vault}_s"
    for vault in (env.vault, special):
        result = env.run("vault", "create", vault)
        assert result.returncode == 0, result.stderr
    configure(env, [special], embedding=embedding)
    return env.vault, special


def configure(env, specialized, linked=(), embedding=None):
    env.config(linked=linked, embedding=embedding)
    path = env.project / "val3.toml"
    names = ", ".join(f'"{name}"' for name in specialized)
    path.write_text(path.read_text().replace("\nlinked = ", f"\nspecialized = [{names}]\nlinked = ", 1))


def with_entry(env, vault, service="val3test", cwd=None, **changes):
    """Vault `vault` with one admitted fact, written from a project whose home it is (`cwd`, default the env's)."""
    project = cwd or env.project
    (project / "val3.toml").write_text(f'service = "{service}"\n[vaults]\nhome = "{vault}"\n')
    assert env.run("vault", "create", vault, cwd=project).returncode == 0
    result = env.run("save", "--transcript", str(env.transcript(tool_outputs=[OUTPUT])), "--session", env.session,
                     input=json.dumps(fact(**changes)), cwd=project)
    assert result.stdout == f"admitted {vault}#1\n", result.stdout + result.stderr


def database(env, service):
    """A database of its own behind `service`, and a project directory for it."""
    name = f"{service}_{uuid.uuid4().hex[:8]}"
    env.sql(f"CREATE DATABASE {name}")
    add_service(env, service, name)
    root = env.root / service
    root.mkdir()
    return root


def source(env, service="val3b"):
    """Vault `env.vault` with one fact in another database; the local project's home is its mirror."""
    root = database(env, service)
    with_entry(env, env.vault, service, root)
    env.config()
    return root


def mirror(env, *args):
    return env.run("vault", "mirror", env.vault, *args)


def mirrored(env, *args):
    result = mirror(env, *(args or ("--from-service", "val3b")))
    assert result.returncode == 0, result.stderr
    return result


MCP = [sys.executable, "-P", "-m", "val3", "mcp"]  # -P: never import from the project directory


def _line(item) -> bytes:
    if isinstance(item, bytes):
        return item
    return (item if isinstance(item, str) else json.dumps(item)).encode()


def mcp_batch(env, lines, cwd=None, timeout=20):
    """Send every line to a fresh `val3 mcp`, close its stdin and return the parsed stdout lines."""
    proc = subprocess.Popen(MCP, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=cwd or env.project, env=env.environ)
    try:
        out, err = proc.communicate(b"".join(_line(item) + b"\n" for item in lines), timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise AssertionError(f"val3 mcp did not exit within {timeout} s")
    answers = []
    for raw in out.splitlines():
        try:
            answers.append(json.loads(raw))
        except ValueError:
            raise AssertionError(f"not a JSON line on stdout: {raw!r}; stderr: {err!r}")
    return answers


class _Live:
    def __init__(self, proc):
        self.proc = proc
        self._buffer = b""

    def send(self, obj):
        self.proc.stdin.write(_line(obj) + b"\n")
        self.proc.stdin.flush()

    def read(self, timeout=15) -> dict:
        """One stdout line, within `timeout` seconds."""
        deadline = time.monotonic() + timeout
        fd = self.proc.stdout.fileno()
        while b"\n" not in self._buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                raise AssertionError(f"val3 mcp did not answer within {timeout} s")
            chunk = os.read(fd, 65536)
            if not chunk:
                raise AssertionError(f"val3 mcp closed stdout; buffered: {self._buffer!r}")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line)


@contextmanager
def mcp_live(env, cwd=None):
    """A running `val3 mcp` to talk to line by line; killed on exit if still running."""
    proc = subprocess.Popen(MCP, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=cwd or env.project, env=env.environ, bufsize=0)
    try:
        yield _Live(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass
