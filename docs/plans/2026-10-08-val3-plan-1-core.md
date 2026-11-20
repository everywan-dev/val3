# VAL3 Plan 1 — Vault store, writer and delivery

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `val3` CLI that stores verified facts, recipes, preferences, decisions and tools in PostgreSQL vaults through a single writer, searches them, keeps a review list, and delivers them to Claude Code sessions through `SessionStart` and `UserPromptSubmit` hooks.

**Architecture:** One Python package (`src/val3`) with a thin argparse CLI. Every vault change goes through `writer.py`, one transaction per candidate; PostgreSQL enforces append-only storage (triggers plus grants) and concurrency (unique constraints). Reads (`show`, `recall`, hook context) live in `views.py` and `search.py`. Hooks fail open: they always exit 0.

**Tech Stack:** Python ≥ 3.11 (standard library: `argparse`, `tomllib`, `json`, `hashlib`), `psycopg[binary]` ≥ 3.2, PostgreSQL 16 with the `unaccent` extension, pytest, Docker (disposable test server).

**Spec:** `docs/design/2026-10-08-val3-design.md`, ADR 0001, ADR 0002 (`docs/adr/`), vocabulary in `CONTEXT.md`.

## Scope and following plans

This plan delivers the store, the writer, the CLI for manual capture (`val3 save`), the review list on the command line, search, and delivery. Automatic capture and the remaining tools follow:

- **Plan 2 — capture.** Pending queue (one file per entry under `$XDG_STATE_HOME/val3/queue/`, created by `rename`, drained under an `fcntl` lock: this answers "two sessions of one project on one machine"), `PreCompact`/`SessionEnd`/`SessionStart` drain hooks, detached extractor (`claude -p`, versioned prompt) emitting the candidate format fixed here, inbox hash tracking and note rewriting. To verify before planning, because the Claude Code documentation does not cover them: survival of a detached child after `SessionEnd` returns; `autoDreamEnabled`, `.consolidate-lock` and `originSessionId`; the memory directory derives from the git repository root (shared by worktrees), not from the cwd, and `autoMemoryDirectory` relocates it.
- **Plan 3 — operations.** Review page (`127.0.0.1`, random token) and `val3 review --tty`, `val3 import` from validated-memory 2.x, `val3 vault backup`.
- **Open (owner's decision):** host of the production PostgreSQL server.

## Decisions fixed by this plan

These resolve design section 13 and are binding for every task.

1. **Index delivery** — `SessionStart` injects the index as `additionalContext` (≤ 10 000 characters); VAL3 never writes `MEMORY.md` (ADR 0002).
2. **`val3.toml`** — at the project root, found by walking up from the cwd:
   ```toml
   service = "val3"          # pg_service.conf entry; no secrets here
   project = "my-project"    # optional; default: the directory holding val3.toml
   [vaults]
   home = "erp"              # written
   linked = ["shared"]       # read-only; optional
   ```
   Vault names match `[a-z][a-z0-9_]{0,62}`, are not `public`, `val3_common`, `information_schema` nor `pg_*`. Unknown keys are errors.
3. **Database layout** — database `val3`; schema `val3_common` holds the `unaccent` text-search configurations `val3_common.en` and `val3_common.es`, the trigger function `forbid_change()` and the query builder `or_query()`; one schema per vault holds the tables of `src/val3/vault.sql`.
4. **Append-only** — every vault table has `BEFORE UPDATE OR DELETE` and `BEFORE TRUNCATE` triggers that raise. Supersession (`supersession`) and review decisions (`review_decision`) are their own tables, so nothing is ever updated.
5. **Roles** — `val3 vault grant <vault> <role> --write|--read`: `--write` grants `SELECT, INSERT` on the tables and `USAGE` on the sequences; `--read` grants `SELECT` and revokes `INSERT`. Both grant `USAGE` on the vault schema and on `val3_common`. Creating roles and passwords is the operator's job.
6. **Full text** — each entry has two generated `tsvector` columns (`val3_common.en`, `val3_common.es`) over title, statement and keywords. A query ORs the lexemes of the input, dropping lexemes that are stop words in the other language (so Spanish `que`/`la` do not match through the English configuration). Superseded entries are never returned. Tools are listed before other entries (design section 9: tools take precedence).
7. **Candidate format** — the JSON in `candidate.FORMAT` (Task 4), shared by `val3 save`, the plan-2 extractor and the plan-3 importer. Unknown keys are errors.
8. **Evidence** — a quote must appear literally (after `\r\n` → `\n` and trimming) in the transcript: quotes of kind `query`/`command`/`api`/`test` in **tool results**, quotes of kind `user` in **the user's messages**. Assistant text and thinking never count. Minimum quote length: 8 characters. Non-user evidence needs a `reproduction`.
9. **Routing** — fact with at least one non-user evidence item → admitted. Preference or decision with verified evidence → admitted (the user's literal words are their validation). Fact backed only by user evidence, recipe, tool candidate → review list. `val3 tool add` (run by a person) → admitted. Missing quote → discarded, logged as an event, exit 1.
10. **Idempotence** — entries and review items carry a `content_hash` (SHA-256 of kind, title, statement) with a `UNIQUE` constraint; a repeated candidate reports `duplicate of <ref>` / `already queued` and writes nothing. This makes re-extraction safe.
11. **Supersession** — `"supersedes": "<vault>#<id>"` on a candidate of the same kind in the home vault. The writer links old → new, queues a `supersession` notice and one `recipe-cites-superseded` notice per recipe citing the old fact. Notices can only be approved (acknowledged); undoing one means saving an entry that supersedes the new one.
12. **References** — recipes cite only entries of the home vault (enforced with foreign keys). Citing linked vaults is deferred.
13. **Session identity** — the `SessionStart` hook appends `export VAL3_SESSION_ID=…` and `export VAL3_TRANSCRIPT=…` to `$CLAUDE_ENV_FILE`; `val3 save` uses them as defaults for `--session` and `--transcript` (verified 2026-10-08).
14. **Transcript format** — internal to Claude Code and documented as changing between versions. `transcript.py` is the only reader; the shape used in tests was checked on 2026-10-08: lines with `"type": "user"`, `message.content` either a string (user prompt) or a list of blocks; `tool_result` blocks hold the output in `content` (string or list of `{"type": "text"}` blocks); lines with `"isMeta": true` are harness-injected and ignored.

## Global Constraints

- Python `>=3.11`; runtime dependencies: standard library plus `psycopg[binary]>=3.2` only. `pytest>=8` is a dev extra.
- Everything in the repository (code, comments, CLI messages, docs, commits) is in English; no references to internal company projects, hosts or people.
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error (argparse).
- Errors for the user are raised as `val3.Val3Error` and printed as `val3: error: <message>` on stderr with exit 1; `psycopg.Error` is printed as `val3: error: database: <message>` with exit 1.
- Tests drive `python -m val3` as a subprocess against a disposable PostgreSQL and fixture project trees, asserting on exit codes, output and database rows. Tests never import `val3`.
- The code never issues `UPDATE`, `DELETE` or `TRUNCATE` against a vault.
- Every SQL statement against a vault is built with `val3.db.vsql(vault, query)`, which formats `{v}` with `psycopg.sql.Identifier`. Any other brace in such a query must be doubled; prefer `ARRAY[]::bigint[]` to `'{}'`.
- Every vault change goes through `src/val3/writer.py`; `src/val3/db.py` only creates vaults and grants.
- Hook commands (`val3 hook …`) always exit 0, print nothing outside adopted projects, and keep injected context within 10 000 characters.
- Work on branch `feature/plan-1-core`; Conventional Commits; never force-push `main`.

## Review Focus

1. **Self-quoted evidence** — a quote that appears only in the assistant's own text must be discarded, never admitted (test in Task 4).
2. **Trivial quotes** — a quote such as `42` or `ok` matches almost any transcript; candidates with quotes under 8 characters must be rejected (test in Task 4).
3. **Repeated candidates** — the same candidate submitted twice (re-extraction, two sessions) must not create a second entry or review item (tests in Tasks 4 and 5).
4. **Hostile or empty search text** — apostrophes, backslashes, stop words only, accents, or an empty prompt must neither fail nor match noise (tests in Tasks 8 and 10).
5. **Server down or project not adopted during a hook** — the hook exits 0, warns once per session, and never blocks the prompt (tests in Tasks 9 and 10).

---

## File structure

```
pyproject.toml                 package metadata, console script, pytest config
src/val3/__init__.py           __version__, Val3Error
src/val3/__main__.py           `python -m val3`
src/val3/cli.py                argparse commands → module functions; exit codes
src/val3/config.py             val3.toml, vault names, <vault>#<id> references
src/val3/db.py                 connect, vsql, create_vault, grant
src/val3/common.sql            val3_common: extension, text-search configs, functions
src/val3/vault.sql             one vault's tables, indexes, append-only triggers
src/val3/candidate.py          candidate format and validation
src/val3/transcript.py         evidence text from a session transcript
src/val3/writer.py             submit, decide, add_tool (the only writer)
src/val3/views.py              show_entry, recipe/tool lines, review listings, hook texts
src/val3/search.py             full-text search across vaults
src/val3/hooks.py              hook entry points (fail open)
tests/conftest.py              Docker PostgreSQL, Env (project tree, service file, runner)
tests/helpers.py               shared candidates and save helper
tests/test_*.py                one file per task
```

Set up once before Task 1:

```bash
git switch -c feature/plan-1-core
python3 -m venv .venv
```

---

### Task 1: Package scaffold, CLI skeleton and test harness

**Files:**
- Create: `pyproject.toml`, `src/val3/__init__.py`, `src/val3/__main__.py`, `src/val3/cli.py`, `tests/conftest.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `val3.Val3Error`; `val3.__version__ = "0.1.0"`; `cli.build_parser()` creating `sub = parser.add_subparsers(...)` (later tasks insert their commands before `return parser`); `cli.main(argv) -> int`.
- Produces (tests): fixture `env` → `Env` with `root`, `home`, `project`, `vault`, `reader`, `writer`, `session`, `config(home=None, linked=(), service="val3test")`, `run(*args, input=None, cwd=None, environ=None)`, `sql(query, params=(), user="postgres")`, `transcript(tool_outputs=(), user_messages=(), assistant_texts=()) -> Path`. Services in the service file: `val3test` (superuser), `val3down` (closed port), `val3reader`, `val3writer`.

- [ ] **Step 1: Write the packaging files**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "val3"
dynamic = ["version"]
description = "Session memory for coding agents: verified facts and recipes in PostgreSQL vaults"
readme = "README.md"
requires-python = ">=3.11"
dependencies = ["psycopg[binary]>=3.2"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
val3 = "val3.cli:main"

[tool.setuptools.dynamic]
version = {attr = "val3.__version__"}

[tool.setuptools.package-data]
val3 = ["*.sql"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`src/val3/__init__.py`:

```python
"""VAL3: verified session memory for coding agents."""

__version__ = "0.1.0"


class Val3Error(Exception):
    """An error reported to the user with exit code 1."""
```

`src/val3/__main__.py`:

```python
from val3.cli import main

raise SystemExit(main())
```

Install: `.venv/bin/pip install -e '.[dev]'`

- [ ] **Step 2: Write the test harness and the failing tests**

`tests/conftest.py`:

```python
"""Test harness: a disposable PostgreSQL in Docker and a CLI runner.

Tests drive `python -m val3` as a subprocess and assert on exit codes, output and
database rows. They never import val3.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest


@pytest.fixture(scope="session")
def pg_port():
    name = f"val3-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name,
         "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-p", "127.0.0.1::5432", "postgres:16"],
        check=True, capture_output=True,
    )
    try:
        out = subprocess.run(["docker", "port", name, "5432/tcp"],
                             check=True, capture_output=True, text=True).stdout
        port = out.splitlines()[0].rsplit(":", 1)[1]
        admin = f"host=127.0.0.1 port={port} user=postgres dbname=postgres"
        for _ in range(120):
            try:
                with psycopg.connect(admin, autocommit=True) as conn:
                    conn.execute("CREATE DATABASE val3")
                break
            except psycopg.OperationalError:
                time.sleep(0.5)
        else:
            raise RuntimeError("the PostgreSQL test container did not accept connections")
        yield port
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


class Env:
    """One test's world: a HOME, a project tree, a service file and a fresh vault name."""

    def __init__(self, root: Path, port: str):
        self.root = root
        self.port = port
        self.home = root / "home"
        self.project = root / "project"
        self.home.mkdir()
        self.project.mkdir()
        suffix = uuid.uuid4().hex[:10]
        self.vault = f"v_{suffix}"
        self.reader = f"r_{suffix}"
        self.writer = f"w_{suffix}"
        self.session = str(uuid.uuid4())
        services = {"val3test": ("postgres", port), "val3down": ("postgres", "1"),
                    "val3reader": (self.reader, port), "val3writer": (self.writer, port)}
        service_file = root / "pg_service.conf"
        service_file.write_text("".join(
            f"[{name}]\nhost=127.0.0.1\nport={p}\nuser={user}\ndbname=val3\nconnect_timeout=3\n\n"
            for name, (user, p) in services.items()))
        self.environ = {k: v for k, v in os.environ.items()
                        if not k.startswith(("VAL3_", "PG", "CLAUDE_"))}
        self.environ.update(HOME=str(self.home), PGSERVICEFILE=str(service_file),
                            XDG_STATE_HOME=str(root / "state"))

    def config(self, home=None, linked=(), service="val3test"):
        linked_list = ", ".join(f'"{name}"' for name in linked)
        (self.project / "val3.toml").write_text(
            f'service = "{service}"\n[vaults]\nhome = "{home or self.vault}"\nlinked = [{linked_list}]\n')

    def run(self, *args, input=None, cwd=None, environ=None):
        return subprocess.run([sys.executable, "-m", "val3", *args], input=input, text=True,
                              capture_output=True, cwd=cwd or self.project,
                              env={**self.environ, **(environ or {})}, timeout=60)

    def sql(self, query, params=(), user="postgres"):
        dsn = f"host=127.0.0.1 port={self.port} user={user} dbname=val3"
        with psycopg.connect(dsn, autocommit=True) as conn:
            cur = conn.execute(query, params)
            return cur.fetchall() if cur.description else []

    def transcript(self, tool_outputs=(), user_messages=(), assistant_texts=()):
        """Write a session transcript in the harness's JSONL shape and return its path."""
        path = self.home / ".claude" / "projects" / "-project" / f"{self.session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [{"type": "user", "sessionId": self.session,
                  "message": {"role": "user", "content": text}} for text in user_messages]
        lines += [{"type": "assistant", "sessionId": self.session,
                   "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
                  for text in assistant_texts]
        lines += [{"type": "user", "sessionId": self.session,
                   "message": {"role": "user", "content": [
                       {"type": "tool_result", "tool_use_id": f"t{i}", "content": text}]}}
                  for i, text in enumerate(tool_outputs)]
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return path


@pytest.fixture
def env(tmp_path, pg_port):
    return Env(tmp_path, pg_port)
```

`tests/test_cli.py`:

```python
def test_version(env):
    result = env.run("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == "val3 0.1.0"


def test_no_command_is_a_usage_error(env):
    result = env.run()
    assert result.returncode == 2
    assert "usage: val3" in result.stderr


def test_unknown_command_is_a_usage_error(env):
    assert env.run("frobnicate").returncode == 2


def test_harness_database_is_reachable(env):
    assert env.sql("SELECT current_database()") == [("val3",)]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: `test_version` and the usage tests FAIL (`No module named val3.cli`); `test_harness_database_is_reachable` PASSES (the first run pulls `postgres:16` if missing).

- [ ] **Step 4: Write the CLI skeleton**

`src/val3/cli.py`:

```python
"""Command-line interface: parses arguments and maps errors to exit codes."""
import argparse
import sys

import psycopg

from val3 import Val3Error, __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="val3", description="Verified session memory for coding agents.")
    parser.add_argument("--version", action="version", version=f"val3 {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_usage(sys.stderr)
        return 2
    try:
        return args.func(args) or 0
    except Val3Error as exc:
        print(f"val3: error: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"val3: error: database: {str(exc).strip()}", file=sys.stderr)
        return 1
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src tests
git commit -m "feat: package scaffold, CLI skeleton and PostgreSQL test harness"
```

---

### Task 2: Project configuration and `val3 vault create`

**Files:**
- Create: `src/val3/config.py`, `src/val3/db.py`, `src/val3/common.sql`, `src/val3/vault.sql`, `tests/test_vault.py`
- Modify: `src/val3/cli.py`

**Interfaces:**
- Consumes: `Val3Error`, `cli.build_parser` (`sub`).
- Produces: `config.CONFIG_FILE = "val3.toml"`; `config.Project(root: Path, name: str, service: str, home: str, linked: tuple[str, ...])` with property `vaults -> tuple[str, ...]` (home first); `config.check_vault_name(name) -> str`; `config.parse_ref(text) -> tuple[str, int]`; `config.find_config(start: Path) -> Path | None`; `config.load_project(start: Path) -> Project`; `db.connect(service: str, timeout: int = 10) -> psycopg.Connection` (autocommit); `db.vsql(vault: str, query: str) -> psycopg.sql.Composed`; `db.create_vault(conn, name) -> None`. SQL objects: `val3_common.en`, `val3_common.es`, `val3_common.forbid_change()`, `val3_common.or_query(cfg regconfig, other regconfig, text) -> tsquery`; vault tables listed in `vault.sql`.

- [ ] **Step 1: Write the failing tests**

`tests/test_vault.py`:

```python
import psycopg
import pytest

TABLES = {"entry", "supersession", "knowledge_index", "entry_index", "evidence", "tool",
          "recipe_parameter", "recipe_step", "step_fact", "recipe_example", "review_item",
          "review_decision", "event"}


def create(env):
    result = env.run("vault", "create", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr


def insert_entry(env, statement="s", digest="h1"):
    return env.sql(
        f"INSERT INTO {env.vault}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
        "VALUES ('fact', 't', %s, %s, 'p', 'm') RETURNING id", (statement, digest))[0][0]


def test_create_vault_from_project_config(env):
    env.config()
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"created vault {env.vault}"
    rows = env.sql("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (env.vault,))
    assert {name for (name,) in rows} == TABLES


def test_create_vault_with_explicit_service_outside_a_project(env):
    create(env)


def test_create_existing_vault_fails(env):
    create(env)
    result = env.run("vault", "create", env.vault, "--service", "val3test")
    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_invalid_vault_names_are_rejected(env):
    for name in ("Bad-Name", "pg_x", "public", "val3_common", "1abc", "a;drop"):
        result = env.run("vault", "create", name, "--service", "val3test")
        assert result.returncode == 1, name
        assert "invalid vault name" in result.stderr


def test_missing_config_fails(env):
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 1
    assert "no val3.toml found" in result.stderr


def test_config_errors_are_reported(env):
    (env.project / "val3.toml").write_text('service = "val3test"\ncolour = 1\n[vaults]\nhome = "x"\n')
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 1
    assert "unknown keys: colour" in result.stderr
    env.config(linked=[env.vault])
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 1
    assert "appears twice" in result.stderr


def test_config_is_found_from_a_subdirectory(env):
    env.config()
    sub = env.project / "a" / "b"
    sub.mkdir(parents=True)
    result = env.run("vault", "create", env.vault, cwd=sub)
    assert result.returncode == 0, result.stderr


def test_vault_tables_are_append_only(env):
    create(env)
    entry_id = insert_entry(env)
    for statement in (f"UPDATE {env.vault}.entry SET title = 'x' WHERE id = {entry_id}",
                      f"DELETE FROM {env.vault}.entry",
                      f"TRUNCATE {env.vault}.event"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            env.sql(statement)


def test_search_vectors_ignore_accents(env):
    create(env)
    insert_entry(env, statement="Facturación de abril")
    rows = env.sql(f"SELECT search_es @@ to_tsquery('val3_common.es', 'facturacion'), "
                   f"search_en @@ to_tsquery('val3_common.en', 'facturacion') FROM {env.vault}.entry")
    assert rows == [(True, True)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_vault.py -v`
Expected: FAIL (`invalid choice: 'vault'`, exit code 2).

- [ ] **Step 3: Write `config.py`**

```python
"""Project configuration (val3.toml), vault names and entry references."""
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from val3 import Val3Error

CONFIG_FILE = "val3.toml"
_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
_RESERVED = {"public", "val3_common", "information_schema"}
_REF = re.compile(r"([a-z][a-z0-9_]{0,62})#([1-9][0-9]{0,17})")


@dataclass(frozen=True)
class Project:
    root: Path
    name: str
    service: str
    home: str
    linked: tuple[str, ...]

    @property
    def vaults(self) -> tuple[str, ...]:
        return (self.home, *self.linked)


def check_vault_name(name: object) -> str:
    if (not isinstance(name, str) or not _NAME.fullmatch(name)
            or name in _RESERVED or name.startswith("pg_")):
        raise Val3Error(f"invalid vault name {name!r}: use lowercase letters, digits and '_', "
                        "starting with a letter")
    return name


def parse_ref(text: str) -> tuple[str, int]:
    match = _REF.fullmatch(text.strip())
    if not match:
        raise Val3Error(f"invalid entry reference {text!r}: expected <vault>#<id>")
    return check_vault_name(match[1]), int(match[2])


def find_config(start: Path) -> Path | None:
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / CONFIG_FILE).is_file():
            return directory / CONFIG_FILE
    return None


def load_project(start: Path) -> Project:
    path = find_config(start)
    if path is None:
        raise Val3Error(f"no {CONFIG_FILE} found in {start} or its parents")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise Val3Error(f"{path}: {exc}") from None
    unknown = set(data) - {"service", "project", "vaults"}
    if unknown:
        raise Val3Error(f"{path}: unknown keys: {', '.join(sorted(unknown))}")
    service = data.get("service")
    if not isinstance(service, str) or not service.strip():
        raise Val3Error(f"{path}: 'service' must name a pg_service.conf entry")
    name = data.get("project", path.parent.name)
    if not isinstance(name, str) or not name.strip():
        raise Val3Error(f"{path}: 'project' must be a non-empty string")
    vaults = data.get("vaults")
    if not isinstance(vaults, dict) or set(vaults) - {"home", "linked"}:
        raise Val3Error(f"{path}: [vaults] must hold 'home' and optionally 'linked'")
    home = check_vault_name(vaults.get("home"))
    linked = vaults.get("linked", [])
    if not isinstance(linked, list):
        raise Val3Error(f"{path}: 'linked' must be a list of vault names")
    linked = tuple(check_vault_name(vault) for vault in linked)
    if home in linked or len(set(linked)) != len(linked):
        raise Val3Error(f"{path}: a vault appears twice in [vaults]")
    return Project(path.parent, name, service, home, linked)
```

- [ ] **Step 4: Write the SQL**

`src/val3/common.sql` (run before every vault creation; idempotent):

```sql
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE SCHEMA IF NOT EXISTS val3_common;
GRANT USAGE ON SCHEMA val3_common TO PUBLIC;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config
                   WHERE cfgname = 'en' AND cfgnamespace = 'val3_common'::regnamespace) THEN
        CREATE TEXT SEARCH CONFIGURATION val3_common.en (COPY = pg_catalog.english);
        ALTER TEXT SEARCH CONFIGURATION val3_common.en
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, english_stem;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config
                   WHERE cfgname = 'es' AND cfgnamespace = 'val3_common'::regnamespace) THEN
        CREATE TEXT SEARCH CONFIGURATION val3_common.es (COPY = pg_catalog.spanish);
        ALTER TEXT SEARCH CONFIGURATION val3_common.es
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, spanish_stem;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION val3_common.forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'val3 vaults are append-only: % on %.% is not allowed',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
END $$;

-- OR of the lexemes of `t` under `cfg`, without the lexemes that are stop words under `other`.
CREATE OR REPLACE FUNCTION val3_common.or_query(cfg regconfig, other regconfig, t text)
RETURNS tsquery LANGUAGE sql STABLE AS $$
    SELECT string_agg('''' || replace(replace(l, '\', '\\'), '''', '''''') || '''', ' | ')::tsquery
    FROM unnest(tsvector_to_array(to_tsvector(cfg, t))) AS l
    WHERE to_tsvector(other, l) <> ''::tsvector
$$;
```

`src/val3/vault.sql` (run with `search_path` set to the new vault schema):

```sql
CREATE TABLE entry (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('fact', 'recipe', 'preference', 'decision', 'tool')),
    title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    statement text NOT NULL CHECK (length(statement) > 0),
    keywords text NOT NULL DEFAULT '',
    content_hash text NOT NULL UNIQUE,
    origin_project text NOT NULL,
    origin_session text,
    origin_machine text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    search_en tsvector GENERATED ALWAYS AS
        (to_tsvector('val3_common.en'::regconfig, title || ' ' || statement || ' ' || keywords)) STORED,
    search_es tsvector GENERATED ALWAYS AS
        (to_tsvector('val3_common.es'::regconfig, title || ' ' || statement || ' ' || keywords)) STORED
);
CREATE INDEX entry_search_en ON entry USING gin (search_en);
CREATE INDEX entry_search_es ON entry USING gin (search_es);

CREATE TABLE supersession (
    old_id bigint PRIMARY KEY REFERENCES entry (id),
    new_id bigint NOT NULL REFERENCES entry (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (old_id <> new_id)
);

CREATE TABLE knowledge_index (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL UNIQUE CHECK (name ~ '^[a-z0-9][a-z0-9-]{0,62}$')
);

CREATE TABLE entry_index (
    entry_id bigint NOT NULL REFERENCES entry (id),
    index_id bigint NOT NULL REFERENCES knowledge_index (id),
    PRIMARY KEY (entry_id, index_id)
);

CREATE TABLE evidence (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entry_id bigint NOT NULL REFERENCES entry (id),
    kind text NOT NULL CHECK (kind IN ('query', 'command', 'api', 'test', 'user')),
    reproduction text NOT NULL DEFAULT '',
    quote text NOT NULL,
    session_id text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX evidence_entry ON evidence (entry_id);

CREATE TABLE tool (
    entry_id bigint PRIMARY KEY REFERENCES entry (id),
    tool_type text NOT NULL CHECK (tool_type IN ('skill', 'mcp', 'cli')),
    environment text NOT NULL DEFAULT '',
    priority integer NOT NULL DEFAULT 0
);

CREATE TABLE recipe_parameter (
    recipe_id bigint NOT NULL REFERENCES entry (id),
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    PRIMARY KEY (recipe_id, name)
);

CREATE TABLE recipe_step (
    recipe_id bigint NOT NULL REFERENCES entry (id),
    position integer NOT NULL CHECK (position > 0),
    action text NOT NULL,
    tool_id bigint REFERENCES entry (id),
    PRIMARY KEY (recipe_id, position)
);

CREATE TABLE step_fact (
    recipe_id bigint NOT NULL,
    position integer NOT NULL,
    fact_id bigint NOT NULL REFERENCES entry (id),
    PRIMARY KEY (recipe_id, position, fact_id),
    FOREIGN KEY (recipe_id, position) REFERENCES recipe_step (recipe_id, position)
);
CREATE INDEX step_fact_fact ON step_fact (fact_id);

CREATE TABLE recipe_example (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipe_id bigint NOT NULL REFERENCES entry (id),
    parameters jsonb NOT NULL,
    result text NOT NULL,
    session_id text
);

CREATE TABLE review_item (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reason text NOT NULL CHECK (reason IN ('recipe', 'tool', 'user-confirmation',
                                           'supersession', 'recipe-cites-superseded')),
    payload jsonb NOT NULL,
    content_hash text NOT NULL UNIQUE,
    entry_id bigint REFERENCES entry (id),
    origin_project text NOT NULL,
    origin_session text,
    origin_machine text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE review_decision (
    review_id bigint PRIMARY KEY REFERENCES review_item (id),
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected')),
    decided_by text NOT NULL,
    entry_id bigint REFERENCES entry (id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now(),
    action text NOT NULL,
    entry_id bigint,
    review_id bigint,
    project text NOT NULL,
    session_id text,
    machine text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'
);

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['entry', 'supersession', 'knowledge_index', 'entry_index', 'evidence',
                             'tool', 'recipe_parameter', 'recipe_step', 'step_fact', 'recipe_example',
                             'review_item', 'review_decision', 'event'] LOOP
        EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change()', t);
        EXECUTE format('CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON %I '
                       'FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change()', t);
    END LOOP;
END $$;
```

- [ ] **Step 5: Write `db.py`**

```python
"""Connections and vault schema management (DDL and grants only; data goes through the writer)."""
from importlib.resources import files

import psycopg
from psycopg import sql

from val3 import Val3Error
from val3.config import check_vault_name


def connect(service: str, timeout: int = 10) -> psycopg.Connection:
    return psycopg.connect(service=service, connect_timeout=timeout, autocommit=True)


def vsql(vault: str, query: str) -> sql.Composed:
    """A query against one vault: `{v}` becomes the quoted schema name."""
    return sql.SQL(query).format(v=sql.Identifier(check_vault_name(vault)))


def _exists(conn, vault: str) -> bool:
    return conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (vault,)).fetchone() is not None


def create_vault(conn, name: str) -> None:
    check_vault_name(name)
    with conn.transaction():
        if _exists(conn, name):
            raise Val3Error(f"vault {name} already exists")
        conn.execute(files("val3").joinpath("common.sql").read_text(encoding="utf-8"))
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        conn.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(name)))
        conn.execute(files("val3").joinpath("vault.sql").read_text(encoding="utf-8"))
```

- [ ] **Step 6: Add the command to `cli.py`**

Add imports:

```python
from pathlib import Path

from val3.config import check_vault_name, load_project
from val3.db import connect, create_vault
```

In `build_parser`, before `return parser`:

```python
    vault = sub.add_parser("vault", help="create and administer vaults")
    vault_sub = vault.add_subparsers(dest="vault_command", metavar="ACTION", required=True)
    create = vault_sub.add_parser("create", help="create a vault schema in the val3 database")
    create.add_argument("name")
    create.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    create.set_defaults(func=cmd_vault_create)
```

New functions:

```python
def _service(args) -> str:
    return args.service or load_project(Path.cwd()).service


def cmd_vault_create(args) -> None:
    check_vault_name(args.name)
    with connect(_service(args)) as conn:
        create_vault(conn, args.name)
    print(f"created vault {args.name}")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_vault.py tests/test_cli.py -v`
Expected: all passed.

- [ ] **Step 8: Commit**

```bash
git add src tests
git commit -m "feat: val3.toml configuration and vault creation with append-only tables"
```

---

### Task 3: `val3 vault grant`

**Files:**
- Modify: `src/val3/db.py`, `src/val3/cli.py`
- Create: `tests/test_grant.py`

**Interfaces:**
- Consumes: `db.connect`, `db._exists`, `cli._service`.
- Produces: `db.grant(conn, vault: str, role: str, write: bool) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_grant.py`:

```python
import psycopg
import pytest

INSERT = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
          "VALUES ('fact', 't', 's', %s, 'p', 'm')")


def prepare(env):
    assert env.run("vault", "create", env.vault, "--service", "val3test").returncode == 0
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")


def grant(env, role, mode):
    return env.run("vault", "grant", env.vault, role, mode, "--service", "val3test")


def test_read_grant_allows_select_only(env):
    prepare(env)
    result = grant(env, env.reader, "--read")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"granted read on {env.vault} to {env.reader}"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry", user=env.reader) == [(0,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(INSERT.format(v=env.vault), ("h1",), user=env.reader)


def test_write_grant_allows_insert_but_not_update(env):
    prepare(env)
    assert grant(env, env.writer, "--write").returncode == 0
    env.sql(INSERT.format(v=env.vault), ("h1",), user=env.writer)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(f"UPDATE {env.vault}.entry SET title = 'x'", user=env.writer)


def test_read_grant_after_write_grant_revokes_insert(env):
    prepare(env)
    assert grant(env, env.writer, "--write").returncode == 0
    assert grant(env, env.writer, "--read").returncode == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(INSERT.format(v=env.vault), ("h1",), user=env.writer)


def test_grant_needs_a_mode(env):
    prepare(env)
    assert env.run("vault", "grant", env.vault, env.reader, "--service", "val3test").returncode == 2


def test_grant_on_a_missing_vault_fails(env):
    prepare(env)
    result = env.run("vault", "grant", "v_missing", env.reader, "--read", "--service", "val3test")
    assert result.returncode == 1
    assert "no vault v_missing" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_grant.py -v`
Expected: FAIL (`invalid choice: 'grant'`).

- [ ] **Step 3: Implement**

Append to `db.py`:

```python
def grant(conn, vault: str, role: str, write: bool) -> None:
    check_vault_name(vault)
    v, r = sql.Identifier(vault), sql.Identifier(role)
    with conn.transaction():
        if not _exists(conn, vault):
            raise Val3Error(f"no vault {vault}")
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA val3_common, {} TO {}").format(v, r))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(v, r))
        if write:
            conn.execute(sql.SQL("GRANT INSERT ON ALL TABLES IN SCHEMA {} TO {}").format(v, r))
            conn.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA {} TO {}").format(v, r))
        else:
            conn.execute(sql.SQL("REVOKE INSERT ON ALL TABLES IN SCHEMA {} FROM {}").format(v, r))
```

In `cli.py`, change the import to `from val3.db import connect, create_vault, grant`, and in `build_parser` after the `create` parser:

```python
    grant_parser = vault_sub.add_parser("grant", help="grant a database role read or write access to a vault")
    grant_parser.add_argument("name")
    grant_parser.add_argument("role")
    mode = grant_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--read", action="store_true", help="read only (linked vault)")
    mode.add_argument("--write", action="store_true", help="read and append (home vault)")
    grant_parser.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    grant_parser.set_defaults(func=cmd_vault_grant)
```

```python
def cmd_vault_grant(args) -> None:
    with connect(_service(args)) as conn:
        grant(conn, args.name, args.role, args.write)
    print(f"granted {'write' if args.write else 'read'} on {args.name} to {args.role}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_grant.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: vault grants for read-only and append-only roles"
```

---

### Task 4: Candidates, transcript evidence, `val3 save` and `val3 show`

**Files:**
- Create: `src/val3/candidate.py`, `src/val3/transcript.py`, `src/val3/writer.py`, `src/val3/views.py`, `tests/helpers.py`, `tests/test_save.py`
- Modify: `src/val3/cli.py`

**Interfaces:**
- Consumes: `config.Project`, `config.parse_ref`, `config.load_project`, `config.CONFIG_FILE`, `db.connect`, `db.vsql`.
- Produces:
  - `candidate.validate(data: object) -> dict` returning keys `kind, title, statement, keywords (list[str]), indexes (list[str]), evidence (list[{kind, reproduction, quote}]), supersedes (str | None), recipe (dict | None: parameters [{name, description}], steps [{action, tool: str | None, facts: list[str]}], examples [{parameters: dict, result}]), tool (dict | None: type, environment, priority)`. `validate` is idempotent on its own output. Constants `KINDS`, `EVIDENCE_KINDS`, `TOOL_TYPES`, `MIN_QUOTE = 8`, `FORMAT`.
  - `transcript.evidence_text(path: Path) -> tuple[str, str]` (tool output text, user message text).
  - `writer.Origin(project, session, machine)`, `writer.origin(project, session) -> Origin`, `writer.content_hash(candidate) -> str`, `writer.submit(conn, project, data, transcript: Path | None, session: str | None) -> str`, private `_admit(conn, vault, candidate, org) -> tuple[int, bool]`, `_entry_by_hash`, `_check_evidence`, `_log(conn, vault, action, org, entry_id=None, review_id=None, detail=None)`.
  - `views.show_entry(conn, vault, entry_id) -> str`.
  - Tests: `helpers.OUTPUT`, `helpers.QUOTE`, `helpers.fact(**changes)`, `helpers.make_vault(env)`, `helpers.save(env, candidate, transcript, *extra)`.

- [ ] **Step 1: Write the test helpers and the failing tests**

`tests/helpers.py`:

```python
import json

QUOTE = "invoice_count\n-------------\n          42"
OUTPUT = QUOTE + "\n(1 row)"


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


def save(env, candidate, transcript, *extra):
    return env.run("save", "--transcript", str(transcript), "--session", env.session, *extra,
                   input=json.dumps(candidate))
```

`tests/test_save.py`:

```python
import json

from helpers import OUTPUT, QUOTE, fact, make_vault, save

PREFERENCE = {"kind": "preference", "title": "Reports in Spanish",
              "statement": "Reports to the owner are written in Spanish.",
              "evidence": [{"kind": "user", "quote": "write the reports in Spanish"}]}


def count(env, table="entry"):
    return env.sql(f"SELECT count(*) FROM {env.vault}.{table}")[0][0]


def test_fact_with_quote_in_tool_output_is_admitted(env):
    make_vault(env)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert env.sql(f"SELECT kind, title, origin_session, keywords FROM {env.vault}.entry") == [
        ("fact", "April has 42 invoices", env.session, "invoices facturas")]
    assert env.sql(f"SELECT kind, quote FROM {env.vault}.evidence") == [("query", QUOTE)]
    assert env.sql(f"SELECT k.name FROM {env.vault}.entry_index ei "
                   f"JOIN {env.vault}.knowledge_index k ON k.id = ei.index_id") == [("billing",)]
    assert env.sql(f"SELECT action FROM {env.vault}.event") == [("admitted",)]


def test_show_prints_statement_and_evidence(env):
    make_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    result = env.run("show", f"{env.vault}#1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"{env.vault}#1  [fact]  April has 42 invoices")
    assert "The ERP holds 42 invoices dated April 2026." in result.stdout
    assert "indexes: billing" in result.stdout
    assert "      > invoice_count" in result.stdout


def test_quote_only_in_assistant_text_is_discarded(env):
    make_vault(env)
    result = save(env, fact(), env.transcript(assistant_texts=[OUTPUT]))
    assert result.returncode == 1
    assert "discarded: evidence quote not found in tool output" in result.stderr
    assert count(env) == 0
    assert env.sql(f"SELECT action FROM {env.vault}.event") == [("discarded",)]


def test_user_quote_must_come_from_a_user_message(env):
    make_vault(env)
    result = save(env, PREFERENCE, env.transcript(tool_outputs=["please write the reports in Spanish"]))
    assert result.returncode == 1
    assert "not found in the user's messages" in result.stderr
    result = save(env, PREFERENCE, env.transcript(user_messages=["Please write the reports in Spanish, always."]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"


def test_same_candidate_twice_is_a_duplicate(env):
    make_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT])
    save(env, fact(), transcript)
    result = save(env, fact(keywords=["other"]), transcript)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"duplicate of {env.vault}#1"
    assert count(env) == 1


def test_quote_matches_output_with_windows_line_endings(env):
    make_vault(env)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT.replace("\n", "\r\n")]))
    assert result.returncode == 0, result.stderr


def test_invalid_candidates_are_rejected(env):
    make_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT])
    cases = [
        (fact(evidence=[{"kind": "query", "reproduction": "SELECT 1", "quote": "42"}]), "at least 8 characters"),
        (fact(colour="red"), "unknown keys: colour"),
        (fact(evidence=[]), "needs at least one evidence item"),
        (fact(kind="rumour"), "kind must be one of"),
        (fact(evidence=[{"kind": "query", "quote": "invoice_count"}]), "reproduction is required"),
        (fact(title="x" * 201), "one line of at most 200 characters"),
        (fact(supersedes="nonsense"), "invalid entry reference"),
        (fact(recipe={"steps": []}), '"recipe" is required for kind recipe and allowed only there'),
    ]
    for candidate, message in cases:
        result = save(env, candidate, transcript)
        assert result.returncode == 1, candidate
        assert message in result.stderr, (candidate, result.stderr)
    assert count(env) == 0


def test_invalid_json_is_rejected(env):
    make_vault(env)
    result = env.run("save", input="{not json")
    assert result.returncode == 1
    assert "not valid JSON" in result.stderr


def test_evidence_needs_a_transcript(env):
    make_vault(env)
    result = env.run("save", input=json.dumps(fact()))
    assert result.returncode == 1
    assert "needs the session transcript" in result.stderr


def test_session_defaults_come_from_the_environment(env):
    make_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT])
    result = env.run("save", input=json.dumps(fact()),
                     environ={"VAL3_TRANSCRIPT": str(transcript), "VAL3_SESSION_ID": env.session})
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT origin_session FROM {env.vault}.entry") == [(env.session,)]


def test_save_reads_a_candidate_file(env):
    make_vault(env)
    path = env.root / "candidate.json"
    path.write_text(json.dumps(fact()))
    result = env.run("save", str(path), "--transcript", str(env.transcript(tool_outputs=[OUTPUT])))
    assert result.returncode == 0, result.stderr


def test_show_rejects_undeclared_vaults_and_missing_entries(env):
    make_vault(env)
    result = env.run("show", "other_vault#1")
    assert result.returncode == 1
    assert "not declared" in result.stderr
    result = env.run("show", f"{env.vault}#5")
    assert result.returncode == 1
    assert f"no entry {env.vault}#5" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_save.py -v`
Expected: FAIL (`invalid choice: 'save'`).

- [ ] **Step 3: Write `candidate.py`**

```python
"""The candidate format shared by `val3 save`, the extractor and the importer."""
import re

from val3 import Val3Error
from val3.config import parse_ref

KINDS = ("fact", "recipe", "preference", "decision", "tool")
EVIDENCE_KINDS = ("query", "command", "api", "test", "user")
TOOL_TYPES = ("skill", "mcp", "cli")
MIN_QUOTE = 8
_INDEX = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")

FORMAT = """\
{
  "kind": "fact | recipe | preference | decision | tool",
  "title": "one line, at most 200 characters",
  "statement": "the fact, the recipe's purpose, the preference or the decision",
  "keywords": ["search terms in English and Spanish"],
  "indexes": ["knowledge index names, e.g. flows"],
  "evidence": [{"kind": "query | command | api | test | user",
                "reproduction": "the query, command or call that produced the output",
                "quote": "output copied literally from the transcript, at least 8 characters"}],
  "supersedes": "<vault>#<id> of the entry this one replaces (optional)",
  "recipe": {"parameters": [{"name": "month", "description": "YYYY-MM"}],
             "steps": [{"action": "what to do", "tool": "<vault>#<id> (optional)",
                        "facts": ["<vault>#<id>"]}],
             "examples": [{"parameters": {"month": "2026-04"}, "result": "what it gave"}]},
  "tool": {"type": "skill | mcp | cli", "environment": "where it runs", "priority": 0}
}
Facts, preferences and decisions need evidence. A "user" quote must come from the user's own
messages; other quotes from tool output. "recipe" only for kind recipe, "tool" only for kind tool."""


def validate(data: object) -> dict:
    obj = _object(data, "candidate", {"kind", "title", "statement"},
                  {"keywords", "indexes", "evidence", "supersedes", "recipe", "tool"})
    kind = obj["kind"]
    if kind not in KINDS:
        raise Val3Error(f"kind must be one of: {', '.join(KINDS)}")
    title = _text(obj["title"], "title").strip()
    if len(title) > 200 or "\n" in title:
        raise Val3Error("title must be one line of at most 200 characters")
    out = {
        "kind": kind,
        "title": title,
        "statement": _text(obj["statement"], "statement").strip(),
        "keywords": [_text(k, "keywords[]").strip() for k in _list(obj.get("keywords", []), "keywords")],
        "indexes": [_index(name) for name in _list(obj.get("indexes", []), "indexes")],
        "evidence": [_evidence(item, i) for i, item in enumerate(_list(obj.get("evidence", []), "evidence"))],
        "supersedes": None,
        "recipe": None,
        "tool": None,
    }
    if obj.get("supersedes") is not None:
        out["supersedes"] = _ref(obj["supersedes"], "supersedes")
    if kind in ("fact", "preference", "decision") and not out["evidence"]:
        raise Val3Error(f"a {kind} needs at least one evidence item")
    if (kind == "recipe") != (obj.get("recipe") is not None):
        raise Val3Error('"recipe" is required for kind recipe and allowed only there')
    if (kind == "tool") != (obj.get("tool") is not None):
        raise Val3Error('"tool" is required for kind tool and allowed only there')
    if kind == "recipe":
        out["recipe"] = _recipe(obj["recipe"])
    if kind == "tool":
        out["tool"] = _tool(obj["tool"])
    return out


def _evidence(data: object, i: int) -> dict:
    where = f"evidence[{i}]"
    obj = _object(data, where, {"kind", "quote"}, {"reproduction"})
    if obj["kind"] not in EVIDENCE_KINDS:
        raise Val3Error(f"{where}.kind must be one of: {', '.join(EVIDENCE_KINDS)}")
    quote = _text(obj["quote"], f"{where}.quote").replace("\r\n", "\n").strip()
    if len(quote) < MIN_QUOTE:
        raise Val3Error(f"{where}.quote must hold at least {MIN_QUOTE} characters of literal output")
    reproduction = obj.get("reproduction", "")
    if not isinstance(reproduction, str):
        raise Val3Error(f"{where}.reproduction must be a string")
    if obj["kind"] != "user" and not reproduction.strip():
        raise Val3Error(f"{where}.reproduction is required for {obj['kind']} evidence")
    return {"kind": obj["kind"], "reproduction": reproduction.strip(), "quote": quote}


def _recipe(data: object) -> dict:
    obj = _object(data, "recipe", {"steps"}, {"parameters", "examples"})
    parameters = []
    for i, item in enumerate(_list(obj.get("parameters", []), "recipe.parameters")):
        p = _object(item, f"recipe.parameters[{i}]", {"name"}, {"description"})
        description = p.get("description", "")
        if not isinstance(description, str):
            raise Val3Error(f"recipe.parameters[{i}].description must be a string")
        parameters.append({"name": _text(p["name"], f"recipe.parameters[{i}].name").strip(),
                           "description": description.strip()})
    names = [p["name"] for p in parameters]
    if len(set(names)) != len(names):
        raise Val3Error("recipe parameter names must be unique")
    steps = []
    for i, item in enumerate(_list(obj["steps"], "recipe.steps")):
        s = _object(item, f"recipe.steps[{i}]", {"action"}, {"tool", "facts"})
        steps.append({
            "action": _text(s["action"], f"recipe.steps[{i}].action").strip(),
            "tool": _ref(s["tool"], f"recipe.steps[{i}].tool") if s.get("tool") is not None else None,
            "facts": [_ref(ref, f"recipe.steps[{i}].facts[]")
                      for ref in _list(s.get("facts", []), f"recipe.steps[{i}].facts")],
        })
    if not steps:
        raise Val3Error("a recipe needs at least one step")
    examples = []
    for i, item in enumerate(_list(obj.get("examples", []), "recipe.examples")):
        e = _object(item, f"recipe.examples[{i}]", {"parameters", "result"})
        values = e["parameters"]
        if not isinstance(values, dict) or set(values) - set(names):
            raise Val3Error(f"recipe.examples[{i}].parameters must be an object of declared parameters")
        examples.append({"parameters": values, "result": _text(e["result"], f"recipe.examples[{i}].result")})
    return {"parameters": parameters, "steps": steps, "examples": examples}


def _tool(data: object) -> dict:
    obj = _object(data, "tool", {"type"}, {"environment", "priority"})
    if obj["type"] not in TOOL_TYPES:
        raise Val3Error(f"tool.type must be one of: {', '.join(TOOL_TYPES)}")
    environment = obj.get("environment", "")
    priority = obj.get("priority", 0)
    if not isinstance(environment, str):
        raise Val3Error("tool.environment must be a string")
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise Val3Error("tool.priority must be an integer")
    return {"type": obj["type"], "environment": environment.strip(), "priority": priority}


def _object(data: object, where: str, required: set, optional: set = frozenset()) -> dict:
    if not isinstance(data, dict):
        raise Val3Error(f"{where} must be a JSON object")
    missing = required - set(data)
    unknown = set(data) - required - set(optional)
    if missing:
        raise Val3Error(f"{where} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise Val3Error(f"{where} has unknown keys: {', '.join(sorted(unknown))}")
    return data


def _list(data: object, where: str) -> list:
    if not isinstance(data, list):
        raise Val3Error(f"{where} must be a list")
    return data


def _text(data: object, where: str) -> str:
    if not isinstance(data, str) or not data.strip():
        raise Val3Error(f"{where} must be a non-empty string")
    return data


def _index(data: object) -> str:
    if not isinstance(data, str) or not _INDEX.fullmatch(data):
        raise Val3Error(f"invalid index name {data!r}: use lowercase letters, digits and '-'")
    return data


def _ref(data: object, where: str) -> str:
    vault, entry_id = parse_ref(_text(data, where))
    return f"{vault}#{entry_id}"
```

Note: the test case `fact(colour="red")` expects the message `unknown keys: colour`; `_object` produces `candidate has unknown keys: colour`, which contains it.

- [ ] **Step 4: Write `transcript.py`**

```python
"""Evidence text from a harness session transcript (JSONL).

Only two sources count as evidence: tool results (what a command, query, API call or test
returned) and the user's own messages. The assistant's text and thinking never count. The
transcript format is internal to Claude Code; this module is the only place that reads it.
"""
import json
from pathlib import Path

from val3 import Val3Error


def evidence_text(path: Path) -> tuple[str, str]:
    """Return (tool output text, user message text) of a transcript."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise Val3Error(f"cannot read transcript {path}: {exc.strerror}") from None
    tool: list[str] = []
    user: list[str] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("type") != "user" or record.get("isMeta"):
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            user.append(content)
            continue
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                tool.extend(_texts(block.get("content")))
            elif block.get("type") == "text" and isinstance(block.get("text"), str):
                user.append(block["text"])
    return _normal("\n".join(tool)), _normal("\n".join(user))


def _texts(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)]
    return []


def _normal(text: str) -> str:
    return text.replace("\r\n", "\n")
```

- [ ] **Step 5: Write `writer.py`**

```python
"""The writer: the single code path that changes a vault, one transaction per candidate."""
import hashlib
import json
import socket
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from val3 import Val3Error
from val3.candidate import validate
from val3.config import Project
from val3.db import vsql
from val3.transcript import evidence_text


@dataclass(frozen=True)
class Origin:
    project: str
    session: str | None
    machine: str


def origin(project: Project, session: str | None) -> Origin:
    return Origin(project.name, session, socket.gethostname())


def content_hash(candidate: dict) -> str:
    key = json.dumps([candidate["kind"], candidate["title"], candidate["statement"]])
    return hashlib.sha256(key.encode()).hexdigest()


def submit(conn, project: Project, data: object, transcript: Path | None, session: str | None) -> str:
    candidate = validate(data)
    org = origin(project, session)
    vault = project.home
    _check_evidence(conn, vault, candidate, transcript, org)
    try:
        with conn.transaction():
            entry_id, created = _admit(conn, vault, candidate, org)
    except psycopg.errors.UniqueViolation:
        raise Val3Error("another session changed the same entries concurrently; run the command again") from None
    return f"{'admitted' if created else 'duplicate of'} {vault}#{entry_id}"


def _check_evidence(conn, vault: str, candidate: dict, transcript: Path | None, org: Origin) -> None:
    if not candidate["evidence"]:
        return
    if transcript is None:
        raise Val3Error("evidence needs the session transcript: pass --transcript or set VAL3_TRANSCRIPT")
    tool_text, user_text = evidence_text(transcript)
    for item in candidate["evidence"]:
        user = item["kind"] == "user"
        if item["quote"] not in (user_text if user else tool_text):
            _log(conn, vault, "discarded", org, detail={"title": candidate["title"], "quote": item["quote"]})
            where = "the user's messages" if user else "tool output"
            raise Val3Error(f"discarded: evidence quote not found in {where} of {transcript}: "
                            f"{item['quote'][:80]!r}")


def _entry_by_hash(conn, vault: str, digest: str) -> int | None:
    row = conn.execute(vsql(vault, "SELECT id FROM {v}.entry WHERE content_hash = %s"), (digest,)).fetchone()
    return row[0] if row else None


def _admit(conn, vault: str, candidate: dict, org: Origin) -> tuple[int, bool]:
    """Insert an entry and its parts; return (id, created). Runs inside the caller's transaction."""
    digest = content_hash(candidate)
    row = conn.execute(vsql(vault, """
        INSERT INTO {v}.entry (kind, title, statement, keywords, content_hash,
                               origin_project, origin_session, origin_machine)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (content_hash) DO NOTHING RETURNING id"""),
        (candidate["kind"], candidate["title"], candidate["statement"], " ".join(candidate["keywords"]),
         digest, org.project, org.session, org.machine)).fetchone()
    if row is None:
        return _entry_by_hash(conn, vault, digest), False
    entry_id = row[0]
    for item in candidate["evidence"]:
        conn.execute(vsql(vault, """
            INSERT INTO {v}.evidence (entry_id, kind, reproduction, quote, session_id)
            VALUES (%s, %s, %s, %s, %s)"""),
            (entry_id, item["kind"], item["reproduction"], item["quote"], org.session))
    for name in candidate["indexes"]:
        conn.execute(vsql(vault, "INSERT INTO {v}.knowledge_index (name) VALUES (%s) ON CONFLICT (name) DO NOTHING"),
                     (name,))
        conn.execute(vsql(vault, """
            INSERT INTO {v}.entry_index (entry_id, index_id)
            SELECT %s, id FROM {v}.knowledge_index WHERE name = %s ON CONFLICT DO NOTHING"""),
            (entry_id, name))
    _log(conn, vault, "admitted", org, entry_id=entry_id)
    return entry_id, True


def _log(conn, vault: str, action: str, org: Origin, entry_id=None, review_id=None, detail=None) -> None:
    conn.execute(vsql(vault, """
        INSERT INTO {v}.event (action, entry_id, review_id, project, session_id, machine, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s)"""),
        (action, entry_id, review_id, org.project, org.session, org.machine, Jsonb(detail or {})))
```

- [ ] **Step 6: Write `views.py`**

```python
"""Read-only renderings of vault content for the CLI and the hooks."""
from val3 import Val3Error
from val3.db import vsql


def show_entry(conn, vault: str, entry_id: int) -> str:
    row = conn.execute(vsql(vault, """
        SELECT kind, title, statement, keywords, origin_project, origin_session, origin_machine, created_at
        FROM {v}.entry WHERE id = %s"""), (entry_id,)).fetchone()
    if row is None:
        raise Val3Error(f"no entry {vault}#{entry_id}")
    kind, title, statement, keywords, project, session, machine, created = row
    lines = [f"{vault}#{entry_id}  [{kind}]  {title}", "", statement, ""]
    indexes = [name for (name,) in conn.execute(vsql(vault, """
        SELECT k.name FROM {v}.entry_index ei JOIN {v}.knowledge_index k ON k.id = ei.index_id
        WHERE ei.entry_id = %s ORDER BY k.name"""), (entry_id,))]
    if indexes:
        lines.append("indexes: " + ", ".join(indexes))
    if keywords:
        lines.append("keywords: " + keywords)
    lines.append(f"origin: {project} / session {session or '-'} / {machine} / {created:%Y-%m-%d %H:%M}")
    evidence = conn.execute(vsql(vault, """
        SELECT kind, reproduction, quote FROM {v}.evidence WHERE entry_id = %s ORDER BY id"""),
        (entry_id,)).fetchall()
    if evidence:
        lines.append("evidence:")
        for evidence_kind, reproduction, quote in evidence:
            lines.append(f"  - {evidence_kind}: {reproduction}" if reproduction else f"  - {evidence_kind}")
            lines.extend(f"      > {line}" for line in quote.splitlines())
    return "\n".join(lines)
```

- [ ] **Step 7: Add `save` and `show` to `cli.py`**

Add imports:

```python
import json
import os

from val3.candidate import FORMAT
from val3.config import CONFIG_FILE, parse_ref
from val3.views import show_entry
from val3.writer import submit
```

In `build_parser`, before `return parser`:

```python
    save = sub.add_parser("save", help="submit a candidate entry (JSON) to the home vault",
                          epilog="candidate format:\n" + FORMAT,
                          formatter_class=argparse.RawDescriptionHelpFormatter)
    save.add_argument("file", nargs="?", default="-", help="candidate JSON file (default: stdin)")
    save.add_argument("--transcript", type=Path, help="session transcript (default: $VAL3_TRANSCRIPT)")
    save.add_argument("--session", help="session id (default: $VAL3_SESSION_ID)")
    save.set_defaults(func=cmd_save)

    show = sub.add_parser("show", help="print an entry with its evidence")
    show.add_argument("ref", help="<vault>#<id>")
    show.set_defaults(func=cmd_show)
```

New functions:

```python
def cmd_save(args) -> None:
    project = load_project(Path.cwd())
    try:
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    except OSError as exc:
        raise Val3Error(f"cannot read {args.file}: {exc.strerror}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise Val3Error(f"candidate is not valid JSON: {exc}") from None
    transcript = args.transcript or (Path(os.environ["VAL3_TRANSCRIPT"]) if os.environ.get("VAL3_TRANSCRIPT") else None)
    session = args.session or os.environ.get("VAL3_SESSION_ID")
    with connect(project.service) as conn:
        print(submit(conn, project, data, transcript, session))


def _declared(project, ref: str) -> tuple[str, int]:
    vault, entry_id = parse_ref(ref)
    if vault not in project.vaults:
        raise Val3Error(f"vault {vault} is not declared in {project.root / CONFIG_FILE}")
    return vault, entry_id


def cmd_show(args) -> None:
    project = load_project(Path.cwd())
    vault, entry_id = _declared(project, args.ref)
    with connect(project.service) as conn:
        print(show_entry(conn, vault, entry_id))
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests -v`
Expected: all passed.

- [ ] **Step 9: Commit**

```bash
git add src tests
git commit -m "feat: candidate format, transcript evidence check, val3 save and val3 show"
```

---

### Task 5: Review list, recipes and tool candidates

**Files:**
- Modify: `src/val3/writer.py`, `src/val3/views.py`, `src/val3/cli.py`, `tests/helpers.py`
- Create: `tests/test_review.py`

**Interfaces:**
- Consumes: Task 4 writer internals, `candidate.validate`, `config.parse_ref`, `helpers.*`.
- Produces (tests): `helpers.TOOL` and `helpers.recipe(fact_ref, **changes)`, used again in Tasks 6 and 10.
- Produces: `writer.NOTICES = ("supersession", "recipe-cites-superseded")`; `writer.decide(conn, project, review_id: int, approve: bool, decided_by: str) -> str`; private `_review_reason(candidate) -> str | None`, `_check_refs(conn, vault, candidate)`, `_require(conn, vault, ref, kinds) -> int`, `_queue(conn, vault, reason, payload: dict, entry_id, org) -> tuple[int, bool]`. `views.recipe_lines(conn, vault, recipe_id) -> list[str]`, `views.tool_lines(conn, vault, entry_id) -> list[str]`, `views.pending_reviews(conn, vault) -> str`, `views.review_detail(conn, vault, review_id) -> str`. Output strings: `queued for review: review #N (reason)`, `already queued: review #N`, `approved review #N: admitted <ref>`, `rejected review #N`, `acknowledged review #N`, `no pending review items`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/helpers.py`:

```python
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
```

`tests/test_review.py`:

```python
from helpers import OUTPUT, TOOL, fact, make_vault, recipe, save


def admitted_fact(env):
    make_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT], user_messages=["Yes: the north warehouse closed in March 2025."])
    assert save(env, fact(), transcript).stdout.strip() == f"admitted {env.vault}#1"
    return transcript


def test_recipe_is_queued_and_admitted_on_approval(env):
    transcript = admitted_fact(env)
    result = save(env, recipe(f"{env.vault}#1"), transcript)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "queued for review: review #1 (recipe)"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]
    listing = env.run("review").stdout
    assert "#1" in listing and "recipe" in listing and "Count the invoices of a month" in listing
    assert '"kind": "recipe"' in env.run("review", "show", "1").stdout
    result = env.run("review", "approve", "1", "--by", "tester")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"approved review #1: admitted {env.vault}#2"
    assert env.sql(f"SELECT position, action, tool_id FROM {env.vault}.recipe_step ORDER BY position") == [
        (1, "Run the invoice count query for the month", None), (2, "Report the count", None)]
    assert env.sql(f"SELECT recipe_id, position, fact_id FROM {env.vault}.step_fact") == [(2, 1, 1)]
    assert env.sql(f"SELECT name, description FROM {env.vault}.recipe_parameter") == [("month", "YYYY-MM")]
    assert env.sql(f"SELECT parameters, result FROM {env.vault}.recipe_example") == [
        ({"month": "2026-04"}, "42 invoices")]
    assert env.sql(f"SELECT decision, decided_by, entry_id FROM {env.vault}.review_decision") == [
        ("approved", "tester", 2)]
    assert env.run("review").stdout.strip() == "no pending review items"
    shown = env.run("show", f"{env.vault}#2").stdout
    assert f"  1. Run the invoice count query for the month  (facts: {env.vault}#1)" in shown
    assert "  - month: YYYY-MM" in shown
    assert '{"month": "2026-04"} -> 42 invoices' in shown


def test_recipe_with_bad_references_is_rejected(env):
    transcript = admitted_fact(env)
    for ref, message in ((f"{env.vault}#99", "does not exist"), ("other_vault#1", "home vault")):
        result = save(env, recipe(ref), transcript)
        assert result.returncode == 1, ref
        assert message in result.stderr
    bad_example = recipe(f"{env.vault}#1")
    bad_example["recipe"]["examples"] = [{"parameters": {"year": "2026"}, "result": "x"}]
    result = save(env, bad_example, transcript)
    assert result.returncode == 1
    assert "declared parameters" in result.stderr


def test_recipe_step_must_cite_a_fact(env):
    transcript = admitted_fact(env)
    assert save(env, TOOL, transcript).returncode == 0
    assert env.run("review", "approve", "1").returncode == 0
    result = save(env, recipe(f"{env.vault}#2"), transcript)
    assert result.returncode == 1
    assert f"{env.vault}#2 is a tool, expected fact" in result.stderr


def test_fact_confirmed_only_by_the_user_is_queued_and_can_be_rejected(env):
    transcript = admitted_fact(env)
    candidate = fact(title="North warehouse closed", statement="The north warehouse closed in March 2025.",
                     evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}])
    result = save(env, candidate, transcript)
    assert result.stdout.strip() == "queued for review: review #1 (user-confirmation)"
    result = env.run("review", "reject", "1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "rejected review #1"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]
    result = env.run("review", "reject", "1")
    assert result.returncode == 1
    assert "already rejected" in result.stderr


def test_fact_with_tool_and_user_evidence_is_admitted(env):
    transcript = admitted_fact(env)
    candidate = fact(title="Warehouse closure confirmed", statement="The north warehouse closed in March 2025.",
                     evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"},
                               fact()["evidence"][0]])
    assert save(env, candidate, transcript).stdout.strip() == f"admitted {env.vault}#2"


def test_same_review_candidate_is_queued_once(env):
    transcript = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), transcript)
    result = save(env, recipe(f"{env.vault}#1"), transcript)
    assert result.stdout.strip() == "already queued: review #1"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(1,)]


def test_tool_candidate_is_queued_and_admitted_with_its_details(env):
    transcript = admitted_fact(env)
    assert save(env, TOOL, transcript).stdout.strip() == "queued for review: review #1 (tool)"
    assert env.run("review", "approve", "1").stdout.strip() == f"approved review #1: admitted {env.vault}#2"
    assert env.sql(f"SELECT tool_type, environment, priority FROM {env.vault}.tool") == [
        ("cli", "https://example.org/code-graph.git", 5)]
    shown = env.run("show", f"{env.vault}#2").stdout
    assert "tool: cli, priority 5" in shown
    assert "environment: https://example.org/code-graph.git" in shown


def test_unknown_review_item(env):
    make_vault(env)
    result = env.run("review", "approve", "7")
    assert result.returncode == 1
    assert "no review item #7" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_review.py -v`
Expected: FAIL (`invalid choice: 'review'`; recipes are rejected because `_admit` ignores routing).

- [ ] **Step 3: Extend `writer.py`**

Add `from val3.config import Project, parse_ref` (replacing the `Project`-only import). Replace `submit` with:

```python
NOTICES = ("supersession", "recipe-cites-superseded")


def submit(conn, project: Project, data: object, transcript: Path | None, session: str | None) -> str:
    candidate = validate(data)
    org = origin(project, session)
    vault = project.home
    _check_evidence(conn, vault, candidate, transcript, org)
    try:
        with conn.transaction():
            existing = _entry_by_hash(conn, vault, content_hash(candidate))
            if existing:
                return f"duplicate of {vault}#{existing}"
            _check_refs(conn, vault, candidate)
            reason = _review_reason(candidate)
            if reason:
                review_id, created = _queue(conn, vault, reason, candidate, None, org)
                return (f"queued for review: review #{review_id} ({reason})" if created
                        else f"already queued: review #{review_id}")
            entry_id, _ = _admit(conn, vault, candidate, org)
    except psycopg.errors.UniqueViolation:
        raise Val3Error("another session changed the same entries concurrently; run the command again") from None
    return f"admitted {vault}#{entry_id}"


def _review_reason(candidate: dict) -> str | None:
    if candidate["kind"] in ("recipe", "tool"):
        return candidate["kind"]
    if candidate["kind"] == "fact" and all(e["kind"] == "user" for e in candidate["evidence"]):
        return "user-confirmation"
    return None


def _check_refs(conn, vault: str, candidate: dict) -> None:
    for step in candidate["recipe"]["steps"] if candidate["recipe"] else []:
        for ref in step["facts"]:
            _require(conn, vault, ref, ("fact",))
        if step["tool"]:
            _require(conn, vault, step["tool"], ("tool",))


def _require(conn, vault: str, ref: str, kinds: tuple[str, ...]) -> int:
    ref_vault, entry_id = parse_ref(ref)
    if ref_vault != vault:
        raise Val3Error(f"{ref}: entries may only cite entries of the home vault {vault}")
    row = conn.execute(vsql(vault, "SELECT kind FROM {v}.entry WHERE id = %s"), (entry_id,)).fetchone()
    if row is None:
        raise Val3Error(f"{ref} does not exist")
    if row[0] not in kinds:
        raise Val3Error(f"{ref} is a {row[0]}, expected {' or '.join(kinds)}")
    newer = conn.execute(vsql(vault, "SELECT new_id FROM {v}.supersession WHERE old_id = %s"), (entry_id,)).fetchone()
    if newer:
        raise Val3Error(f"{ref} was superseded by {vault}#{newer[0]}")
    return entry_id


def _queue(conn, vault: str, reason: str, payload: dict, entry_id: int | None, org: Origin) -> tuple[int, bool]:
    digest = hashlib.sha256(json.dumps([reason, payload["title"], payload.get("statement", "")]).encode()).hexdigest()
    row = conn.execute(vsql(vault, """
        INSERT INTO {v}.review_item (reason, payload, content_hash, entry_id,
                                     origin_project, origin_session, origin_machine)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (content_hash) DO NOTHING RETURNING id"""),
        (reason, Jsonb(payload), digest, entry_id, org.project, org.session, org.machine)).fetchone()
    if row is None:
        existing = conn.execute(vsql(vault, "SELECT id FROM {v}.review_item WHERE content_hash = %s"),
                                (digest,)).fetchone()
        return existing[0], False
    _log(conn, vault, "queued", org, entry_id=entry_id, review_id=row[0], detail={"reason": reason})
    return row[0], True


def decide(conn, project: Project, review_id: int, approve: bool, decided_by: str) -> str:
    vault = project.home
    org = origin(project, None)
    try:
        with conn.transaction():
            row = conn.execute(vsql(vault, """
                SELECT reason, payload, entry_id, origin_project, origin_session, origin_machine
                FROM {v}.review_item WHERE id = %s"""), (review_id,)).fetchone()
            if row is None:
                raise Val3Error(f"no review item #{review_id}")
            reason, payload, entry_id, *item_origin = row
            done = conn.execute(vsql(vault, "SELECT decision FROM {v}.review_decision WHERE review_id = %s"),
                                (review_id,)).fetchone()
            if done:
                raise Val3Error(f"review #{review_id} was already {done[0]}")
            if reason in NOTICES:
                if not approve:
                    raise Val3Error(f"review #{review_id} is a notice and can only be approved; to undo it, "
                                    f"save an entry that supersedes {vault}#{entry_id}")
                message = f"acknowledged review #{review_id}"
            elif approve:
                candidate = validate(payload)
                _check_refs(conn, vault, candidate)
                entry_id, created = _admit(conn, vault, candidate, Origin(*item_origin))
                message = f"approved review #{review_id}: {'admitted' if created else 'duplicate of'} {vault}#{entry_id}"
            else:
                entry_id = None
                message = f"rejected review #{review_id}"
            decision = "approved" if approve else "rejected"
            conn.execute(vsql(vault, """
                INSERT INTO {v}.review_decision (review_id, decision, decided_by, entry_id)
                VALUES (%s, %s, %s, %s)"""), (review_id, decision, decided_by, entry_id))
            _log(conn, vault, decision, org, entry_id=entry_id, review_id=review_id)
    except psycopg.errors.UniqueViolation:
        raise Val3Error(f"review #{review_id} was decided concurrently") from None
    return message
```

In `_admit`, before `_log(conn, vault, "admitted", ...)`, insert the recipe and tool parts:

```python
    recipe = candidate["recipe"]
    if recipe:
        for parameter in recipe["parameters"]:
            conn.execute(vsql(vault, """
                INSERT INTO {v}.recipe_parameter (recipe_id, name, description) VALUES (%s, %s, %s)"""),
                (entry_id, parameter["name"], parameter["description"]))
        for position, step in enumerate(recipe["steps"], start=1):
            tool_id = parse_ref(step["tool"])[1] if step["tool"] else None
            conn.execute(vsql(vault, """
                INSERT INTO {v}.recipe_step (recipe_id, position, action, tool_id) VALUES (%s, %s, %s, %s)"""),
                (entry_id, position, step["action"], tool_id))
            for ref in dict.fromkeys(step["facts"]):
                conn.execute(vsql(vault, """
                    INSERT INTO {v}.step_fact (recipe_id, position, fact_id) VALUES (%s, %s, %s)"""),
                    (entry_id, position, parse_ref(ref)[1]))
        for example in recipe["examples"]:
            conn.execute(vsql(vault, """
                INSERT INTO {v}.recipe_example (recipe_id, parameters, result, session_id)
                VALUES (%s, %s, %s, %s)"""),
                (entry_id, Jsonb(example["parameters"]), example["result"], org.session))
    tool = candidate["tool"]
    if tool:
        conn.execute(vsql(vault, """
            INSERT INTO {v}.tool (entry_id, tool_type, environment, priority) VALUES (%s, %s, %s, %s)"""),
            (entry_id, tool["type"], tool["environment"], tool["priority"]))
```

- [ ] **Step 4: Extend `views.py`**

Add `import json` at the top. In `show_entry`, before `return "\n".join(lines)`:

```python
    if kind == "recipe":
        lines.extend(recipe_lines(conn, vault, entry_id))
    if kind == "tool":
        lines.extend(tool_lines(conn, vault, entry_id))
```

New functions:

```python
def recipe_lines(conn, vault: str, recipe_id: int) -> list[str]:
    lines = []
    parameters = conn.execute(vsql(vault, """
        SELECT name, description FROM {v}.recipe_parameter WHERE recipe_id = %s ORDER BY name"""),
        (recipe_id,)).fetchall()
    if parameters:
        lines.append("parameters:")
        lines.extend(f"  - {name}: {description}" if description else f"  - {name}"
                     for name, description in parameters)
    lines.append("steps:")
    steps = conn.execute(vsql(vault, """
        SELECT s.position, s.action, s.tool_id,
               coalesce(array_agg(f.fact_id ORDER BY f.fact_id) FILTER (WHERE f.fact_id IS NOT NULL),
                        ARRAY[]::bigint[])
        FROM {v}.recipe_step s
        LEFT JOIN {v}.step_fact f ON f.recipe_id = s.recipe_id AND f.position = s.position
        WHERE s.recipe_id = %s GROUP BY s.position, s.action, s.tool_id ORDER BY s.position"""),
        (recipe_id,)).fetchall()
    for position, action, tool_id, facts in steps:
        refs = [f"{vault}#{fact_id}" for fact_id in facts]
        notes = []
        if refs:
            notes.append("facts: " + ", ".join(refs))
        if tool_id:
            notes.append(f"tool: {vault}#{tool_id}")
        lines.append(f"  {position}. {action}" + (f"  ({'; '.join(notes)})" if notes else ""))
    examples = conn.execute(vsql(vault, """
        SELECT parameters, result FROM {v}.recipe_example WHERE recipe_id = %s ORDER BY id"""),
        (recipe_id,)).fetchall()
    if examples:
        lines.append("examples:")
        lines.extend(f"  - {json.dumps(values, ensure_ascii=False)} -> {result}" for values, result in examples)
    return lines


def tool_lines(conn, vault: str, entry_id: int) -> list[str]:
    row = conn.execute(vsql(vault, "SELECT tool_type, environment, priority FROM {v}.tool WHERE entry_id = %s"),
                       (entry_id,)).fetchone()
    if row is None:
        return []
    tool_type, environment, priority = row
    lines = [f"tool: {tool_type}, priority {priority}"]
    if environment:
        lines.append(f"environment: {environment}")
    return lines


def pending_reviews(conn, vault: str) -> str:
    rows = conn.execute(vsql(vault, """
        SELECT r.id, r.reason, r.payload->>'title', r.origin_project, r.created_at
        FROM {v}.review_item r
        WHERE NOT EXISTS (SELECT 1 FROM {v}.review_decision d WHERE d.review_id = r.id)
        ORDER BY r.id""")).fetchall()
    if not rows:
        return "no pending review items"
    return "\n".join(f"#{review_id}  {reason:<24}  {title}  ({project}, {created:%Y-%m-%d})"
                     for review_id, reason, title, project, created in rows)


def review_detail(conn, vault: str, review_id: int) -> str:
    row = conn.execute(vsql(vault, """
        SELECT r.reason, r.payload, coalesce(d.decision, 'pending')
        FROM {v}.review_item r LEFT JOIN {v}.review_decision d ON d.review_id = r.id
        WHERE r.id = %s"""), (review_id,)).fetchone()
    if row is None:
        raise Val3Error(f"no review item #{review_id}")
    reason, payload, status = row
    return f"#{review_id}  {reason}  ({status})\n{json.dumps(payload, indent=2, ensure_ascii=False)}"
```

- [ ] **Step 5: Add `review` to `cli.py`**

Add `import getpass`; extend imports to `from val3.views import pending_reviews, review_detail, show_entry` and `from val3.writer import decide, submit`. In `build_parser`, before `return parser`:

```python
    review = sub.add_parser("review", help="list and decide the review list of the home vault")
    review_sub = review.add_subparsers(dest="review_command", metavar="ACTION")
    review_show = review_sub.add_parser("show", help="print a review item's candidate")
    review_show.add_argument("id", type=int)
    for action in ("approve", "reject"):
        decide_parser = review_sub.add_parser(action, help=f"{action} a review item")
        decide_parser.add_argument("id", type=int)
        decide_parser.add_argument("--by", default=None, help="who decides (default: the current user)")
    review.set_defaults(func=cmd_review)
```

```python
def cmd_review(args) -> None:
    project = load_project(Path.cwd())
    with connect(project.service) as conn:
        if args.review_command is None:
            print(pending_reviews(conn, project.home))
        elif args.review_command == "show":
            print(review_detail(conn, project.home, args.id))
        else:
            approve = args.review_command == "approve"
            print(decide(conn, project, args.id, approve, args.by or getpass.getuser()))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests -v`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add src tests
git commit -m "feat: review list with recipe, tool and user-confirmation routing"
```

---

### Task 6: Supersession

**Files:**
- Modify: `src/val3/writer.py`, `src/val3/views.py`
- Create: `tests/test_supersede.py`

**Interfaces:**
- Consumes: `_require`, `_queue`, `_log`, `_admit`, `_check_refs`, `views.recipe_lines`.
- Produces: `writer._supersede(conn, vault, old_id, new_id, org) -> None`. Notice titles: `"<vault>#<new> supersedes <vault>#<old>"` and `"recipe <vault>#<r> cites <vault>#<old>, superseded by <vault>#<new>"`. `show` lines `SUPERSEDED by <ref>` (second line) and `supersedes: <ref>`; recipe steps mark cited facts `<ref> [superseded]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_supersede.py`:

```python
from helpers import OUTPUT, fact, make_vault, recipe, save

NEW_QUOTE = "invoice_count\n-------------\n          43"


def newer(env, **changes):
    candidate = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.",
                     evidence=[{"kind": "query", "reproduction": "SELECT count(*) AS invoice_count FROM invoice",
                                "quote": NEW_QUOTE}],
                     supersedes=f"{env.vault}#1")
    candidate.update(changes)
    return candidate


def transcript(env):
    return env.transcript(tool_outputs=[OUTPUT, NEW_QUOTE + "\n(1 row)"],
                          user_messages=["Please write the reports in Spanish, always."])


def test_superseding_fact_is_admitted_and_linked(env):
    make_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    result = save(env, newer(env), t)
    assert result.stdout.strip() == f"admitted {env.vault}#2", result.stderr
    assert env.sql(f"SELECT old_id, new_id FROM {env.vault}.supersession") == [(1, 2)]
    assert env.run("show", f"{env.vault}#1").stdout.splitlines()[1] == f"SUPERSEDED by {env.vault}#2"
    assert f"supersedes: {env.vault}#1" in env.run("show", f"{env.vault}#2").stdout
    listing = env.run("review").stdout
    assert "supersession" in listing and f"{env.vault}#2 supersedes {env.vault}#1" in listing
    result = env.run("review", "reject", "1")
    assert result.returncode == 1
    assert "can only be approved" in result.stderr
    assert env.run("review", "approve", "1").stdout.strip() == "acknowledged review #1"


def test_recipes_citing_a_superseded_fact_are_flagged(env):
    make_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, recipe(f"{env.vault}#1"), t)
    assert env.run("review", "approve", "1").returncode == 0
    assert save(env, newer(env), t).stdout.strip() == f"admitted {env.vault}#3"
    listing = env.run("review").stdout
    assert f"recipe {env.vault}#2 cites {env.vault}#1, superseded by {env.vault}#3" in listing
    assert f"{env.vault}#1 [superseded]" in env.run("show", f"{env.vault}#2").stdout


def test_an_entry_is_superseded_once(env):
    make_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, newer(env), t)
    result = save(env, newer(env, title="April has 44 invoices", statement="The ERP holds 44 invoices."), t)
    assert result.returncode == 1
    assert f"{env.vault}#1 was superseded by {env.vault}#2" in result.stderr


def test_supersede_needs_the_same_kind_in_the_home_vault(env):
    make_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    result = save(env, newer(env, supersedes="other_vault#1"), t)
    assert result.returncode == 1
    assert "home vault" in result.stderr
    preference = {"kind": "preference", "title": "Reports in Spanish",
                  "statement": "Reports to the owner are written in Spanish.",
                  "evidence": [{"kind": "user", "quote": "write the reports in Spanish"}],
                  "supersedes": f"{env.vault}#1"}
    result = save(env, preference, t)
    assert result.returncode == 1
    assert f"{env.vault}#1 is a fact, expected preference" in result.stderr


def test_a_new_recipe_cannot_cite_a_superseded_fact(env):
    make_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, newer(env), t)
    result = save(env, recipe(f"{env.vault}#1"), t)
    assert result.returncode == 1
    assert "was superseded by" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_supersede.py -v`
Expected: FAIL (no supersession row; `show` lacks `SUPERSEDED`).

- [ ] **Step 3: Implement in `writer.py`**

At the start of `_check_refs`:

```python
    if candidate["supersedes"]:
        _require(conn, vault, candidate["supersedes"], (candidate["kind"],))
```

In `_admit`, before `_log(conn, vault, "admitted", ...)`:

```python
    if candidate["supersedes"]:
        _supersede(conn, vault, parse_ref(candidate["supersedes"])[1], entry_id, org)
```

New function:

```python
def _supersede(conn, vault: str, old_id: int, new_id: int, org: Origin) -> None:
    conn.execute(vsql(vault, "INSERT INTO {v}.supersession (old_id, new_id) VALUES (%s, %s)"), (old_id, new_id))
    _log(conn, vault, "superseded", org, entry_id=old_id, detail={"by": new_id})
    _queue(conn, vault, "supersession", {"title": f"{vault}#{new_id} supersedes {vault}#{old_id}"}, new_id, org)
    recipes = conn.execute(vsql(vault, "SELECT DISTINCT recipe_id FROM {v}.step_fact WHERE fact_id = %s ORDER BY 1"),
                           (old_id,)).fetchall()
    for (recipe_id,) in recipes:
        _queue(conn, vault, "recipe-cites-superseded",
               {"title": f"recipe {vault}#{recipe_id} cites {vault}#{old_id}, superseded by {vault}#{new_id}"},
               recipe_id, org)
```

A concurrent second supersession of the same entry violates `supersession`'s primary key; `submit` and `decide` already turn that `UniqueViolation` into a `Val3Error`.

- [ ] **Step 4: Implement in `views.py`**

In `show_entry`, right after `lines = [f"{vault}#{entry_id}  [{kind}]  {title}", "", statement, ""]`:

```python
    newer = conn.execute(vsql(vault, "SELECT new_id FROM {v}.supersession WHERE old_id = %s"), (entry_id,)).fetchone()
    if newer:
        lines.insert(1, f"SUPERSEDED by {vault}#{newer[0]}")
    older = conn.execute(vsql(vault, "SELECT old_id FROM {v}.supersession WHERE new_id = %s"), (entry_id,)).fetchone()
    if older:
        lines.append(f"supersedes: {vault}#{older[0]}")
```

In `recipe_lines`, before `for position, action, tool_id, facts in steps:` add:

```python
    superseded = {old for (old,) in conn.execute(vsql(vault, """
        SELECT s.old_id FROM {v}.supersession s JOIN {v}.step_fact f ON f.fact_id = s.old_id
        WHERE f.recipe_id = %s"""), (recipe_id,))}
```

and replace the `refs = ...` line with:

```python
        refs = [f"{vault}#{fact_id}" + (" [superseded]" if fact_id in superseded else "") for fact_id in facts]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "feat: supersession with review notices and flagged recipes"
```

---

### Task 7: `val3 tool add`

**Files:**
- Modify: `src/val3/writer.py`, `src/val3/cli.py`
- Create: `tests/test_tool.py`

**Interfaces:**
- Consumes: `candidate.validate`, `candidate.TOOL_TYPES`, `writer._admit`, `writer.origin`.
- Produces: `writer.add_tool(conn, project, name: str, tool_type: str, description: str, questions: list[str], environment: str, priority: int, indexes: list[str]) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tool.py`:

```python
from helpers import make_vault

ADD = ("tool", "add", "code-graph", "--type", "mcp", "--description", "Answers which module overrides a method.",
       "--question", "which module overrides a method?", "--environment", "https://example.org/code-graph.git",
       "--priority", "5", "--index", "modules")


def test_tool_add_admits_a_tool(env):
    make_vault(env)
    result = env.run(*ADD)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert env.sql(f"SELECT kind, title, keywords FROM {env.vault}.entry") == [
        ("tool", "code-graph", "which module overrides a method?")]
    assert env.sql(f"SELECT tool_type, environment, priority FROM {env.vault}.tool") == [
        ("mcp", "https://example.org/code-graph.git", 5)]
    shown = env.run("show", f"{env.vault}#1").stdout
    assert "tool: mcp, priority 5" in shown and "indexes: modules" in shown
    assert env.run(*ADD).stdout.strip() == f"duplicate of {env.vault}#1"


def test_tool_add_rejects_an_unknown_type(env):
    make_vault(env)
    result = env.run("tool", "add", "x", "--type", "plugin", "--description", "d")
    assert result.returncode == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_tool.py -v`
Expected: FAIL (`invalid choice: 'tool'`).

- [ ] **Step 3: Implement**

Append to `writer.py`:

```python
def add_tool(conn, project: Project, name: str, tool_type: str, description: str, questions: list[str],
             environment: str, priority: int, indexes: list[str]) -> str:
    """Register a tool on a person's command: admitted without review."""
    candidate = validate({"kind": "tool", "title": name, "statement": description, "keywords": questions,
                          "indexes": indexes,
                          "tool": {"type": tool_type, "environment": environment, "priority": priority}})
    with conn.transaction():
        entry_id, created = _admit(conn, project.home, candidate, origin(project, None))
    return f"{'admitted' if created else 'duplicate of'} {project.home}#{entry_id}"
```

In `cli.py`, extend imports: `from val3.candidate import FORMAT, TOOL_TYPES` and `from val3.writer import add_tool, decide, submit`. In `build_parser`, before `return parser`:

```python
    tool = sub.add_parser("tool", help="register external tools")
    tool_sub = tool.add_subparsers(dest="tool_command", metavar="ACTION", required=True)
    add = tool_sub.add_parser("add", help="register a skill, MCP server or CLI that answers derivable questions")
    add.add_argument("name")
    add.add_argument("--type", required=True, choices=TOOL_TYPES)
    add.add_argument("--description", required=True)
    add.add_argument("--question", action="append", default=[], help="a question the tool answers (repeatable)")
    add.add_argument("--environment", default="", help="where it runs; repository URL to clone")
    add.add_argument("--priority", type=int, default=0)
    add.add_argument("--index", action="append", default=[], help="knowledge index (repeatable)")
    add.set_defaults(func=cmd_tool_add)
```

```python
def cmd_tool_add(args) -> None:
    project = load_project(Path.cwd())
    with connect(project.service) as conn:
        print(add_tool(conn, project, args.name, args.type, args.description, args.question,
                       args.environment, args.priority, args.index))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat: val3 tool add registers external tools"
```

---

### Task 8: Full-text search and `val3 recall`

**Files:**
- Create: `src/val3/search.py`, `tests/test_recall.py`
- Modify: `src/val3/cli.py`

**Interfaces:**
- Consumes: `db.vsql`, SQL function `val3_common.or_query`.
- Produces: `search.Hit(vault, id, kind, title, statement, rank)` with property `ref`; `search.search(conn, vaults: tuple[str, ...], text: str, limit: int = 10) -> list[Hit]` (tools first, then by rank; superseded entries excluded).

- [ ] **Step 1: Write the failing tests**

`tests/test_recall.py`:

```python
from helpers import OUTPUT, fact, make_vault, save


def saved(env, **changes):
    result = save(env, fact(keywords=[], **changes), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    return result.stdout.split()[-1]


def test_recall_is_accent_insensitive(env):
    make_vault(env)
    ref = saved(env, title="Cierre de facturación", statement="La facturación de abril se cierra el día 5.")
    for terms in (["facturacion"], ["Facturación"], ["FACTURACIÓN", "abril"]):
        result = env.run("recall", *terms)
        assert result.returncode == 0, result.stderr
        assert f"{ref}  [fact]  Cierre de facturación" in result.stdout, terms


def test_recall_stems_english(env):
    make_vault(env)
    ref = saved(env)
    assert ref in env.run("recall", "invoice").stdout


def test_stop_words_alone_match_nothing(env):
    make_vault(env)
    saved(env, title="Late payers", statement="El cliente que paga tarde recibe un aviso.")
    result = env.run("recall", "que", "the", "de", "la")
    assert result.returncode == 0
    assert result.stdout.strip() == "no matches"


def test_quotes_and_backslashes_in_terms_are_safe(env):
    make_vault(env)
    ref = saved(env, title="O'Brien account", statement="Account O'Brien exports to the C:\\exports folder.")
    result = env.run("recall", "O'Brien", "C:\\exports\\", "'''")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert ref in result.stdout


def test_recall_searches_linked_vaults(env):
    shared = env.vault + "_shared"
    env.config(home=shared)
    assert env.run("vault", "create", shared).returncode == 0
    shared_ref = saved(env)
    env.config(linked=[shared])
    assert env.run("vault", "create", env.vault).returncode == 0
    home_ref = saved(env, title="May has 50 invoices", statement="The ERP holds 50 invoices dated May 2026.")
    output = env.run("recall", "invoices").stdout
    assert shared_ref == f"{shared}#1" and shared_ref in output
    assert home_ref in output


def test_superseded_entries_are_not_recalled(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT, "invoice_count\n-------------\n          43"])
    save(env, fact(), t)
    newer = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.",
                 evidence=[{"kind": "query", "reproduction": "SELECT 43",
                            "quote": "invoice_count\n-------------\n          43"}],
                 supersedes=f"{env.vault}#1")
    save(env, newer, t)
    output = env.run("recall", "invoices").stdout
    assert f"{env.vault}#2" in output
    assert f"{env.vault}#1 " not in output


def test_tools_come_first(env):
    make_vault(env)
    saved(env)
    assert env.run("tool", "add", "invoice-browser", "--type", "cli",
                   "--description", "Lists invoices of any month.").returncode == 0
    lines = env.run("recall", "invoices").stdout.splitlines()
    assert lines[0].startswith(f"{env.vault}#2  [tool]")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_recall.py -v`
Expected: FAIL (`invalid choice: 'recall'`).

- [ ] **Step 3: Write `search.py`**

```python
"""Full-text search over a project's vaults; the seam for a later semantic backend."""
from dataclasses import dataclass

from val3.db import vsql

_QUERY = """
WITH q AS (
    SELECT val3_common.or_query('val3_common.en', 'val3_common.es', %(text)s) AS en,
           val3_common.or_query('val3_common.es', 'val3_common.en', %(text)s) AS es
)
SELECT e.id, e.kind, e.title, e.statement,
       greatest(coalesce(ts_rank_cd(e.search_en, q.en), 0), coalesce(ts_rank_cd(e.search_es, q.es), 0))
FROM {v}.entry e, q
WHERE (e.search_en @@ q.en OR e.search_es @@ q.es)
  AND NOT EXISTS (SELECT 1 FROM {v}.supersession s WHERE s.old_id = e.id)
ORDER BY 5 DESC, e.id DESC
LIMIT %(limit)s
"""


@dataclass(frozen=True)
class Hit:
    vault: str
    id: int
    kind: str
    title: str
    statement: str
    rank: float

    @property
    def ref(self) -> str:
        return f"{self.vault}#{self.id}"


def search(conn, vaults: tuple[str, ...], text: str, limit: int = 10) -> list[Hit]:
    hits = [Hit(vault, *row)
            for vault in vaults
            for row in conn.execute(vsql(vault, _QUERY), {"text": text, "limit": limit})]
    hits.sort(key=lambda hit: (hit.kind != "tool", -hit.rank))
    return hits[:limit]
```

- [ ] **Step 4: Add `recall` to `cli.py`**

Add `from val3.search import search`. In `build_parser`, before `return parser`:

```python
    recall = sub.add_parser("recall", help="search the project's vaults")
    recall.add_argument("terms", nargs="+")
    recall.add_argument("--limit", type=int, default=10)
    recall.set_defaults(func=cmd_recall)
```

```python
def cmd_recall(args) -> None:
    project = load_project(Path.cwd())
    with connect(project.service) as conn:
        hits = search(conn, project.vaults, " ".join(args.terms), args.limit)
    if not hits:
        print("no matches")
    for hit in hits:
        print(f"{hit.ref}  [{hit.kind}]  {hit.title}")
        print("    " + " ".join(hit.statement.split())[:200])
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests -v`
Expected: all passed. If `test_quotes_and_backslashes_in_terms_are_safe` fails inside `or_query`, inspect `SELECT tsvector_to_array(to_tsvector('val3_common.en', <terms>))` and fix the quoting in `common.sql`, not in Python.

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "feat: bilingual accent-insensitive full-text recall across vaults"
```

---

### Task 9: `SessionStart` hook — session index and session variables

**Files:**
- Create: `src/val3/hooks.py`, `tests/test_hooks.py`
- Modify: `src/val3/views.py`, `src/val3/cli.py`

**Interfaces:**
- Consumes: `config.find_config`, `config.load_project`, `db.connect`, `db.vsql`.
- Produces: `hooks.LIMIT = 10_000`; `hooks.run(event: str, stdin) -> int` (always 0); `hooks.session_start(data: dict) -> str | None`; `hooks._warn_once(data, message) -> str | None`; `views.session_index(conn, project, limit: int) -> str`. Output on stdout: `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}`. Warn markers in `$XDG_STATE_HOME/val3/warned/` (default `~/.local/state`).

- [ ] **Step 1: Write the failing tests**

`tests/test_hooks.py`:

```python
import json
import subprocess

from helpers import OUTPUT, fact, make_vault, save

PREFERENCE = {"kind": "preference", "title": "Reports in Spanish",
              "statement": "Reports to the owner are written in Spanish.", "indexes": ["reporting"],
              "evidence": [{"kind": "user", "quote": "write the reports in Spanish"}]}
INSERT_MANY = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
               "SELECT %s, %s || g || ' ' || repeat('x', 150), %s, 'h' || g, 'p', 'm' "
               "FROM generate_series(1, %s) g")


def transcript_path(env):
    return str(env.home / ".claude" / "projects" / "-project" / f"{env.session}.jsonl")


def payload(env, event, cwd=None, **extra):
    return {"session_id": env.session, "transcript_path": transcript_path(env),
            "cwd": str(cwd or env.project), "hook_event_name": event, **extra}


def hook(env, event, data, **environ):
    return env.run("hook", event, input=json.dumps(data), environ=environ)


def context(result, event):
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return None
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == event
    return output["additionalContext"]


def start(env, **environ):
    return hook(env, "session-start", payload(env, "SessionStart", source="startup"), **environ)


def test_session_start_injects_the_index(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT], user_messages=["Please write the reports in Spanish, always."])
    save(env, fact(), t)
    save(env, PREFERENCE, t)
    assert env.run("tool", "add", "code-graph", "--type", "mcp", "--priority", "5",
                   "--description", "Answers which module overrides a method.").returncode == 0
    text = context(start(env), "SessionStart")
    assert "## Tools" in text and f"- {env.vault}#3 [tool] code-graph" in text
    assert "## reporting" in text and f"- {env.vault}#2 [preference] Reports in Spanish" in text
    assert "April has 42 invoices" not in text
    assert text.index("## Tools") < text.index("## reporting")


def test_session_start_exports_the_session_variables(env):
    make_vault(env)
    env_file = env.root / "claude.env"
    assert start(env, CLAUDE_ENV_FILE=str(env_file)).returncode == 0
    shown = subprocess.run(["bash", "-c", f'source "{env_file}"; echo "$VAL3_SESSION_ID|$VAL3_TRANSCRIPT"'],
                           capture_output=True, text=True).stdout.strip()
    assert shown == f"{env.session}|{transcript_path(env)}"


def test_hooks_are_silent_outside_adopted_projects(env):
    elsewhere = env.root / "elsewhere"
    elsewhere.mkdir()
    result = hook(env, "session-start", payload(env, "SessionStart", cwd=elsewhere))
    assert result.returncode == 0
    assert result.stdout == ""


def test_unreachable_server_warns_once_per_session(env):
    env.config(service="val3down")
    assert "VAL3 is unavailable" in context(start(env), "SessionStart")
    second = start(env)
    assert second.returncode == 0
    assert second.stdout == ""


def test_session_index_respects_the_context_limit(env):
    make_vault(env)
    env.sql(INSERT_MANY.format(v=env.vault), ("preference", "Preference number ", "s", 300))
    text = context(start(env), "SessionStart")
    assert len(text) <= 10_000
    assert "more entries not shown" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_hooks.py -v`
Expected: FAIL (`invalid choice: 'hook'`).

- [ ] **Step 3: Add `session_index` to `views.py`**

```python
_INDEX_HEADER = """\
# VAL3 memory index

Verified entries of this project's vaults. `val3 show <ref>` prints evidence and steps;
`val3 recall <terms>` searches facts; `val3 save --help` shows how to record a verified entry.
Registered tools answer derivable questions: prefer them over stored facts."""


def session_index(conn, project, limit: int) -> str:
    items = []
    for vault in project.vaults:
        rows = conn.execute(vsql(vault, """
            SELECT e.id, e.kind, e.title, min(k.name), coalesce(max(t.priority), 0)
            FROM {v}.entry e
            LEFT JOIN {v}.entry_index ei ON ei.entry_id = e.id
            LEFT JOIN {v}.knowledge_index k ON k.id = ei.index_id
            LEFT JOIN {v}.tool t ON t.entry_id = e.id
            WHERE e.kind IN ('recipe', 'tool', 'preference')
              AND NOT EXISTS (SELECT 1 FROM {v}.supersession s WHERE s.old_id = e.id)
            GROUP BY e.id""")).fetchall()
        for entry_id, kind, title, index, priority in rows:
            section = "Tools" if kind == "tool" else (index or "General")
            items.append(((section != "Tools", section == "General", section, -priority, vault, entry_id),
                          section, f"- {vault}#{entry_id} [{kind}] {title}"))
    items.sort(key=lambda item: item[0])
    text, current, omitted = _INDEX_HEADER, None, 0
    for _, section, line in items:
        chunk = (f"\n\n## {section}\n" if section != current else "\n") + line
        if len(text) + len(chunk) > limit - 100:
            omitted += 1
            continue
        text += chunk
        current = section
    if not items:
        text += "\n\nNo recipes, tools or preferences yet."
    if omitted:
        text += f"\n\n{omitted} more entries not shown: use `val3 recall <terms>`."
    return text
```

The sort key puts Tools first, named indexes next (alphabetically), General last; tools by descending priority.

- [ ] **Step 4: Write `hooks.py`**

```python
"""Harness hook entry points. They always exit 0 and never block the session."""
import hashlib
import json
import os
import shlex
from pathlib import Path

from val3.config import find_config, load_project
from val3.db import connect
from val3.views import session_index

LIMIT = 10_000  # harness cap for hook context; longer text is moved to a file the model may not read
_EVENTS = {"session-start": "SessionStart"}


def run(event: str, stdin) -> int:
    try:
        data = json.load(stdin)
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        context = _HANDLERS[event](data)
    except Exception as exc:  # fail open: a hook must never break the session
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        context = _warn_once(data, f"VAL3 is unavailable ({detail}); vault memory is not loaded.")
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": _EVENTS[event],
                                                 "additionalContext": context[:LIMIT]}}))
    return 0


def session_start(data: dict) -> str | None:
    cwd = Path(data.get("cwd") or os.getcwd())
    if find_config(cwd) is None:
        return None
    _export_session(data)
    project = load_project(cwd)
    with connect(project.service, timeout=3) as conn:
        return session_index(conn, project, LIMIT)


def _export_session(data: dict) -> None:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file:
        return
    lines = [f"export {name}={shlex.quote(str(data[key]))}\n"
             for name, key in (("VAL3_SESSION_ID", "session_id"), ("VAL3_TRANSCRIPT", "transcript_path"))
             if data.get(key)]
    with open(env_file, "a", encoding="utf-8") as handle:
        handle.writelines(lines)


# ponytail: warn markers are never pruned; plan 2 cleans them together with the queue state.
def _warn_once(data: dict, message: str) -> str | None:
    session = str(data.get("session_id") or "")
    if not session:
        return message
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "val3" / "warned"
    marker = state / hashlib.sha256(session.encode()).hexdigest()
    try:
        state.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=False)
    except FileExistsError:
        return None
    except OSError:
        pass
    return message


_HANDLERS = {"session-start": session_start}
```

- [ ] **Step 5: Add `hook` to `cli.py`**

Add `from val3 import hooks` to the imports. In `build_parser`, before `return parser`:

```python
    hook = sub.add_parser("hook", help="entry points for harness hooks (event JSON on stdin)")
    hook.add_argument("event", choices=("session-start",))
    hook.set_defaults(func=lambda args: hooks.run(args.event, sys.stdin))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests -v`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add src tests
git commit -m "feat: SessionStart hook injects the vault index and exports session variables"
```

---

### Task 10: `UserPromptSubmit` hook — per-prompt matches

**Files:**
- Modify: `src/val3/hooks.py`, `src/val3/views.py`, `src/val3/cli.py`, `tests/test_hooks.py`

**Interfaces:**
- Consumes: `search.search`, `search.Hit`, `views.recipe_lines`, `hooks._HANDLERS`, `hooks._EVENTS`.
- Produces: `hooks.prompt(data: dict) -> str | None`; `views.prompt_context(conn, hits: list[Hit], limit: int) -> str | None`. Output event name `UserPromptSubmit`.

- [ ] **Step 1: Add the failing tests to `tests/test_hooks.py`**

Change the helpers import at the top of the file to
`from helpers import OUTPUT, fact, make_vault, recipe, save`, then append:

```python
LONG = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
        "SELECT 'fact', 'Invoice note ' || g, 'invoices ' || repeat('lorem ', 600), 'p' || g, 'p', 'm' "
        "FROM generate_series(1, 20) g")


def ask(env, text):
    return hook(env, "prompt", payload(env, "UserPromptSubmit", prompt=text))


def test_prompt_injects_matching_entries(env):
    make_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    text = context(ask(env, "How many invoices are there for April?"), "UserPromptSubmit")
    assert (f"- {env.vault}#1 [fact] April has 42 invoices: The ERP holds 42 invoices dated April 2026."
            in text)


def test_prompt_injects_recipe_steps(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    save(env, fact(), t)
    save(env, recipe(f"{env.vault}#1"), t)
    assert env.run("review", "approve", "1").returncode == 0
    text = context(ask(env, "count the invoices of a month"), "UserPromptSubmit")
    assert "1. Run the invoice count query for the month" in text


def test_prompt_without_matches_adds_nothing(env):
    make_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert context(ask(env, "hello there"), "UserPromptSubmit") is None
    assert context(ask(env, ""), "UserPromptSubmit") is None


def test_prompt_with_quotes_and_backslashes_is_safe(env):
    make_vault(env)
    result = ask(env, "what's in C:\\temp\\'x' -- ; DROP TABLE entry; '''")
    assert result.returncode == 0
    assert result.stderr == ""


def test_prompt_context_respects_the_limit(env):
    make_vault(env)
    env.sql(LONG.format(v=env.vault))
    text = context(ask(env, "invoices"), "UserPromptSubmit")
    assert len(text) <= 10_000
    assert text.count(f"{env.vault}#") >= 2


def test_prompt_warns_once_when_the_server_is_down(env):
    env.config(service="val3down")
    assert "VAL3 is unavailable" in context(ask(env, "invoices"), "UserPromptSubmit")
    second = ask(env, "invoices")
    assert second.returncode == 0
    assert second.stdout == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_hooks.py -v`
Expected: the new tests FAIL (`invalid choice: 'prompt'`).

- [ ] **Step 3: Add `prompt_context` to `views.py`**

```python
def prompt_context(conn, hits, limit: int) -> str | None:
    if not hits:
        return None
    text = "VAL3 entries matching this prompt (verified; `val3 show <ref>` prints the evidence):"
    for hit in hits:
        block = f"\n- {hit.ref} [{hit.kind}] {hit.title}: {' '.join(hit.statement.split())}"
        if hit.kind == "recipe":
            block += "".join(f"\n    {line}" for line in recipe_lines(conn, hit.vault, hit.id))
        if len(block) > limit // 2:
            block = block[: limit // 2] + " …"
        if len(text) + len(block) <= limit:
            text += block
    return text
```

- [ ] **Step 4: Add the handler to `hooks.py`**

Add `from val3.search import search` and change the views import to `from val3.views import prompt_context, session_index`. Add:

```python
def prompt(data: dict) -> str | None:
    text = str(data.get("prompt") or "").strip()
    cwd = Path(data.get("cwd") or os.getcwd())
    if not text or find_config(cwd) is None:
        return None
    project = load_project(cwd)
    with connect(project.service, timeout=3) as conn:
        return prompt_context(conn, search(conn, project.vaults, text, limit=8), LIMIT)
```

Update the tables at the bottom and top of the module:

```python
_EVENTS = {"session-start": "SessionStart", "prompt": "UserPromptSubmit"}
_HANDLERS = {"session-start": session_start, "prompt": prompt}
```

In `cli.py`, change the hook choices to `choices=("session-start", "prompt")`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests -v`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "feat: UserPromptSubmit hook injects matching vault entries"
```

---

### Task 11: Adoption guide and session close

**Files:**
- Modify: `README.md`, `TODO.md`, `SESSION.md`

**Interfaces:** none (documentation).

- [ ] **Step 1: Rewrite `README.md`**

Sections, with these exact commands and snippets:

1. One-paragraph description (keep the current one) and `Status: plan 1 (store, writer, review list, recall, delivery hooks); automatic capture is plan 2.`
2. **Install:** `pip install -e .` (Python ≥ 3.11).
3. **Server setup (operator):** create database `val3`; `val3 vault create <name> --service <service>` with a role allowed to `CREATE EXTENSION unaccent`; create login roles; `val3 vault grant <vault> <role> --write` (home) or `--read` (linked).
4. **Connection:** a `~/.pg_service.conf` entry, password in `~/.pgpass`:
   ```ini
   [val3]
   host=db.example.org
   port=5432
   dbname=val3
   user=my_project
   ```
5. **Project:** the `val3.toml` example from Decision 2.
6. **Claude Code hooks** in the project's `.claude/settings.json`:
   ```json
   {
     "hooks": {
       "SessionStart": [{"hooks": [{"type": "command", "command": "val3 hook session-start"}]}],
       "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "val3 hook prompt"}]}]
     }
   }
   ```
7. **Commands:** one line each for `save`, `show`, `recall`, `review` (`show`, `approve`, `reject`), `tool add`, `vault create`, `vault grant`, `hook`.
8. **Development:** `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest` (needs Docker; pulls `postgres:16`).

- [ ] **Step 2: Run the whole suite**

Run: `.venv/bin/pytest -v`
Expected: all passed. Record the count in `SESSION.md`.

- [ ] **Step 3: Update `TODO.md` and `SESSION.md`**

`TODO.md`: mark plan 1 done (link the merge commit), keep "Plan 2 — capture" and "Plan 3 — operations" with the items listed in "Scope and following plans", and "Production PostgreSQL host (owner's decision)".

`SESSION.md`: a dated entry at the top with what was built, the test count, and any deviation from this plan.

- [ ] **Step 4: Commit**

```bash
git add README.md TODO.md SESSION.md
git commit -m "docs: adoption guide for plan 1 and session log"
```
