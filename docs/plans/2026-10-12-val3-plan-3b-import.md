# VAL3 Plan 3b — Import from validated-memory 2.x (first round)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `val3 import --dry-run` turns the knowledge units and memory notes of a validated-memory 2.x adopter into one candidate per source, with every quote checked against a repository file at a commit fixed by the importer, and writes a manifest; `val3 import --apply` re-checks that manifest and queues its candidates on the review list, where a person approves or rejects each one.

**Architecture:** A new module `importer.py` reads one pinned commit through `git` (never the working tree), builds the material per source, calls the existing extractor runner with an import prompt and schema, verifies evidence with `git show`, and writes the manifest. The candidate validator accepts `file` evidence and an `import` block only when asked for imports; the writer gains `queue_import` and `log_import`; `decide` admits an import item with `file` evidence rows or none. Two schema checks widen and `val3 vault upgrade` applies them.

**Tech Stack:** as plans 1-4 and 3: Python ≥ 3.11 standard library, `psycopg[binary]`, PostgreSQL 16 with pgvector (`pgvector/pgvector:0.8.1-pg16`), pytest, Docker; plus the `git` command on `PATH`.

**Spec:** `docs/design/2026-10-12-import-design.md` (approved 2026-10-12) and `docs/adr/0003-repository-file-evidence-for-import.md`. The main design `docs/design/2026-10-08-val3-design.md`, the operations design, plans 1-4 and 3, and ADRs 0001-0002 still bind except where the spec says otherwise.

## Verified facts this plan relies on (2026-10-12, development machine)

1. `git ls-tree -r -z --name-only <commit>` lists tracked paths NUL-separated and unquoted, including `docs/guía rápida.md` and `memory/décision de routage.md`.
2. `git rev-parse --verify --quiet <commit>:<path>` prints the blob id, or nothing with exit 1 when the path is missing at that commit. `git log -1 --format=%H <commit> -- <path>` prints the last commit at or before `<commit>` that changed `<path>`. `git show -s --format=%cI <commit>` prints the commit date in ISO 8601 (`2026-10-12T09:04:04+02:00`).
3. `git status --porcelain=v1 -z --untracked-files=all -- <dirs>` prints `XY path` records NUL-separated: `?? memory/new.md` for untracked, ` M memory/x.md` for modified, and for a rename `R  new` followed by a separate `old` record.
4. `git rev-parse --show-toplevel` prints the repository root from any subdirectory and fails (exit 128) outside a repository.
5. The first adopter's units have frontmatter lines `  - <path> at <40-hex sha>` under `provenance:`; `knowledge-index.md` has table rows `| kb-0008 | superseded by kb-0012 | measured | unknown |`; `memory/MEMORY.md` has lines `- [Title](file.md) — superseded by [[name]] (…)`.
6. The test harness passes `VAL3_EXTRACTOR` pointing at `tests/fake_extractor.py`, which answers call *n* with `FAKE_EXTRACTOR_DIR/response-<n>.json` (default: `{"structured_output": {"candidates": []}}`), fails every call when an `exit` file holds a non-zero code, and records each call's `argv` and `stdin` in `call-<n>.json`.
7. Inline column checks are named `<table>_<column>_check` by PostgreSQL: `evidence_kind_check`, `review_item_reason_check` in vaults created by earlier plans. `pg_get_constraintdef` prints them as `CHECK ((kind = ANY (ARRAY['query'::text, …])))`.

## Global Constraints

- Python `>=3.11`, standard library plus `psycopg[binary]>=3.2`; no new Python dependency. `git` is run as a subprocess with `-C <repo>` and argument lists, never through a shell.
- Everything in English (code, comments, CLI messages, docs, commits). No references to internal company projects, hosts or people: the repository will be published (example vault name in docs: `invoices`; the adopter is "the first adopter").
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error.
- Tests drive the CLI as a subprocess against the disposable PostgreSQL and fixture trees, asserting on exit codes, output, files and database rows; tests never import `val3`.
- The writer is the only code that writes vault rows; vault SQL goes through `vsql`.
- `val3 import --apply` refuses to run when `CLAUDECODE` is set (the existing `_person_only()`); `--dry-run` runs anywhere and writes no database row.
- Every imported candidate goes to the review list (reason `import`); nothing is admitted by `val3 import`.
- Quotes are checked at import time only; `recall`, `show` and delivery never read the adopter's repository.
- The commit of an evidence item is fixed by the importer (unit provenance, or the note's last change), never taken from the model or the manifest.
- Excluded as evidence: paths under `knowledge/`, `memory/`, `.validated-memory/`; the files `knowledge-index.md`, `knowledge-extension.md`, `validated-memory.md`, `validated-memory-profile.md`, `journal.jsonl`; any file named `memory.html`.
- Limits: each cited file clipped at 20 000 characters in the material, the whole material at 60 000; a quote has at least 8 characters (`MIN_QUOTE`).
- Manifest: mode 0600, created exclusively (`O_CREAT | O_EXCL`); default `~/.local/share/val3/import/<vault>-<UTC time>-<batch>.json` (directory created 0700, `XDG_DATA_HOME` respected).
- Text taken from a vault is printed through `views.safe()`.
- Work on branch `feature/plan-3b-import`; Conventional Commits; run the suite with `.venv/bin/python -m pytest -q`.

## Review Focus

1. **File names with spaces and accents** in sources and cited files are listed, read and verified like any other (test in Task 2).
2. **A manifest edited to smuggle a git option** (a snapshot of `--output=…`) is rejected before any git call (test in Task 4).
3. **HEAD moving between the dry run and the apply** (the adopter's session keeps committing) does not change what is applied: the manifest's snapshot is re-checked, not HEAD (test in Task 4).
4. **An extractor answer of the wrong shape** (a recipe, a non-list evidence, neither candidate nor skip) is reported per source as `invalid` and the run goes on (test in Task 2).
5. **A cited file that is binary or larger than the clip** is reported `unreadable` or `capped`, never a crash, and a quote past the clip still verifies against the whole file (test in Task 3).

## Rulings recorded while writing the plan

- A tracked source changed in the working tree is imported from the snapshot and reported as "differs from the working tree"; an untracked one is reported as `uncommitted` and not imported (design D5 says uncommitted changes are not imported; the committed text is).
- A note's cited files are the tracked paths, at the note's last commit, that occur literally in its text and contain a `/` or a `.`: a bare word such as `Makefile` is not matched, so common words never become citations.
- When a unit names the same path at two commits, the first provenance line binds it.

---

## File structure

```
src/val3/vault.sql          named checks; evidence kind 'file'; review reason 'import'
src/val3/db.py              upgrade_vault(): idempotent steps (embedding table, widened checks)
src/val3/candidate.py       validate(data, imported=False): 'file' evidence and the 'import' block for imports only
src/val3/extract.py         run_extractor(project, material, prompt, schema); cost() public
src/val3/import_prompt.md   new: the import extractor prompt
src/val3/importer.py        new: dry_run(), apply(), verify(), build() and the git readers
src/val3/writer.py          + queue_import(), log_import(); decide() validates import items as imports
src/val3/views.py           review card of an import item (source, labelled evidence, failed items)
src/val3/cli.py             `val3 import --dry-run [--out FILE] | --apply FILE`
tests/test_import.py        new: fixture adopter (git), dry run, apply, card, approval
tests/test_vault.py         + upgrade widens the checks
README.md, CONTEXT.md, docs/design/2026-10-08-val3-design.md   documentation (Task 6)
```

---

### Task 1: Schema — `file` evidence, `import` review reason, idempotent upgrade

**Files:**
- Modify: `src/val3/vault.sql` (the `evidence` and `review_item` tables)
- Modify: `src/val3/db.py` (`upgrade_vault`)
- Modify: `src/val3/cli.py` (help text of `vault upgrade`)
- Test: `tests/test_vault.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: vaults whose `evidence.kind` accepts `'file'` and whose `review_item.reason` accepts `'import'`; `upgrade_vault(conn, name) -> bool` returns True when any step ran.

- [ ] **Step 1: Write the failing test** (append to `tests/test_vault.py`)

```python
OLD_CHECKS = (
    "ALTER TABLE {v}.evidence DROP CONSTRAINT evidence_kind_check, ADD CONSTRAINT evidence_kind_check "
    "CHECK (kind IN ('query', 'command', 'api', 'test', 'user'))",
    "ALTER TABLE {v}.review_item DROP CONSTRAINT review_item_reason_check, ADD CONSTRAINT review_item_reason_check "
    "CHECK (reason IN ('recipe', 'tool', 'user-confirmation', 'supersession', 'recipe-cites-superseded'))",
)


def test_upgrade_widens_the_checks_for_imports(env):
    create(env)
    for statement in OLD_CHECKS:  # a vault created before plan 3b
        env.sql(statement.format(v=env.vault))
    entry_id = insert_entry(env)
    with pytest.raises(psycopg.errors.CheckViolation):
        env.sql(f"INSERT INTO {env.vault}.evidence (entry_id, kind, quote) VALUES (%s, 'file', 'quoted text')",
                (entry_id,))
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    env.sql(f"INSERT INTO {env.vault}.evidence (entry_id, kind, quote) VALUES (%s, 'file', 'quoted text')",
            (entry_id,))
    env.sql(f"INSERT INTO {env.vault}.review_item (reason, payload, content_hash, origin_project, origin_machine) "
            "VALUES ('import', '{}', 'h', 'p', 'm')")
    again = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert again.stdout.strip() == f"vault {env.vault} is up to date"


def test_a_new_vault_accepts_file_evidence_and_import_items(env):
    create(env)
    entry_id = insert_entry(env)
    env.sql(f"INSERT INTO {env.vault}.evidence (entry_id, kind, quote) VALUES (%s, 'file', 'quoted text')",
            (entry_id,))
    env.sql(f"INSERT INTO {env.vault}.review_item (reason, payload, content_hash, origin_project, origin_machine) "
            "VALUES ('import', '{}', 'h', 'p', 'm')")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_vault.py -k "widens or accepts_file"`
Expected: FAIL — `CheckViolation` on the `'file'` insert of the new vault; the upgrade prints `is up to date`.

- [ ] **Step 3: Name and widen the checks in `src/val3/vault.sql`**

In `CREATE TABLE evidence` replace the `kind` line with:

```sql
    kind text NOT NULL CONSTRAINT evidence_kind_check
        CHECK (kind IN ('query', 'command', 'api', 'test', 'user', 'file')),
```

In `CREATE TABLE review_item` replace the `reason` lines with:

```sql
    reason text NOT NULL CONSTRAINT review_item_reason_check
        CHECK (reason IN ('recipe', 'tool', 'user-confirmation', 'supersession', 'recipe-cites-superseded',
                          'import')),
```

- [ ] **Step 4: Make `upgrade_vault` a list of idempotent steps (`src/val3/db.py`)**

Replace the whole `upgrade_vault` function with:

```python
# (table, constraint, condition, value it must hold): checks widened by later plans; vault.sql has the current ones
_CHECKS = (
    ("evidence", "evidence_kind_check", "kind IN ('query', 'command', 'api', 'test', 'user', 'file')", "'file'"),
    ("review_item", "review_item_reason_check",
     "reason IN ('recipe', 'tool', 'user-confirmation', 'supersession', 'recipe-cites-superseded', 'import')",
     "'import'"),
)


def upgrade_vault(conn, name: str) -> bool:
    """Bring a vault created by an earlier version to the current schema; False when nothing changed.

    Each step runs only when the vault lacks it. Roles keep on new tables the SELECT and INSERT privileges
    they hold on `entry`."""
    check_vault_name(name)
    changed = False
    with conn.transaction():
        if not _exists(conn, name):
            raise Val3Error(f"no vault {name}")
        _run_file(conn, "common.sql")
        if conn.execute("SELECT to_regclass(%s)", (f"{name}.embedding",)).fetchone()[0] is None:
            _vault_path(conn, name)
            _run_file(conn, "embedding.sql")
            grants = conn.execute("""
                SELECT r.rolname, a.privilege_type
                FROM pg_class c, aclexplode(c.relacl) a JOIN pg_roles r ON r.oid = a.grantee
                WHERE c.oid = to_regclass(%s) AND a.grantee <> c.relowner
                  AND a.privilege_type IN ('SELECT', 'INSERT')""", (f"{name}.entry",)).fetchall()
            for role, privilege in grants:
                conn.execute(sql.SQL("GRANT {} ON {}.embedding TO {}").format(
                    sql.SQL(privilege), sql.Identifier(name), sql.Identifier(role)))
            changed = True
        for table, constraint, condition, value in _CHECKS:
            current = conn.execute("""
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid = to_regclass(%s) AND conname = %s""", (f"{name}.{table}", constraint)).fetchone()
            if current is None or value not in current[0]:
                conn.execute(sql.SQL("ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {c}, ADD CONSTRAINT {c} CHECK ({x})")
                             .format(t=sql.Identifier(name, table), c=sql.Identifier(constraint),
                                     x=sql.SQL(condition)))
                changed = True
    return changed
```

In `src/val3/cli.py`, change the `upgrade` parser help to `"bring a vault to the current schema"`.

- [ ] **Step 5: Run the vault tests**

Run: `.venv/bin/python -m pytest -q tests/test_vault.py`
Expected: all pass (the existing embedding-upgrade and up-to-date tests included).

- [ ] **Step 6: Commit**

```bash
git add src/val3/vault.sql src/val3/db.py src/val3/cli.py tests/test_vault.py
git commit -m "feat: file evidence and import review items in the vault schema; upgrade widens old checks"
```

---

### Task 2: `val3 import --dry-run` — sources, cited files, extraction, verification, manifest

**Files:**
- Modify: `src/val3/candidate.py` (`validate`, `_evidence`, new `_file_evidence`)
- Modify: `src/val3/extract.py` (`run_extractor` signature and check; `_cost` renamed `cost`)
- Create: `src/val3/import_prompt.md`
- Create: `src/val3/importer.py`
- Modify: `src/val3/cli.py` (the `import` command, dry run only)
- Test: `tests/test_import.py` (new)

**Interfaces:**
- Consumes: `extract.run_extractor`, `extract.ExtractorError`, `candidate.validate`, `candidate.MIN_QUOTE`, `config.Project`.
- Produces (later tasks rely on these exact names):
  - `candidate.validate(data, imported: bool = False) -> dict` — with `imported=True` accepts the key `import` (a dict, kept in the output), evidence items `{"kind": "file", "path", "commit", "quote"[, "reproduction"]}` normalised to `{"kind": "file", "path", "commit", "reproduction": "git show <commit>:<path>", "quote"}`, and an empty evidence list for any kind. Without it, a `file` item raises `Val3Error("evidence[i]: file evidence comes only from `val3 import`")`.
  - `extract.run_extractor(project, material, prompt=PROMPT, schema=SCHEMA) -> dict` (checks only that `structured_output` is an object); `extract.cost(result) -> float`.
  - `importer.Cited(path, commit, label, date, readable)` (frozen dataclass).
  - `importer.excluded(path) -> bool`, `importer._is_source(path) -> bool`, `importer._read(repo, commit, path) -> (str | None, str | None)`, `importer._root(project) -> Path`, `importer._date(repo, commit, cache) -> str`, `importer._cited(repo, head, path, text, dates) -> (label, commit, list[Cited])`, `importer._meta(repo, head, path, blob, batch, label, commit, dates) -> dict`, `importer.verify(repo, snapshot, cited, path, quote) -> dict`, `importer.build(repo, head, cited, candidate, meta) -> dict`, `importer._source(project, repo, head, path, batch, dates, superseded, predecessors) -> dict`, `importer.dry_run(project, out) -> str`, `importer.MANIFEST_FORMAT == 1`.
  - Manifest JSON: `{"format": 1, "batch", "vault", "project", "repository", "remote", "snapshot": {"commit", "date"}, "sources": [record]}`; a record is `{"path", "kind": "unit"|"note", "blob", "outcome", "by", "skip", "error", "capped", "cited": [Cited as dict], "candidate": validated payload or null, "cost", "dirty"}`; outcomes `ready`, `skipped`, `superseded`, `uncommitted`, `extractor-failed`, `invalid`, `unreadable`.
  - The `import` block of a payload: `{"batch", "source", "source_kind", "source_blob", "source_label": "snapshot"|"note snapshot", "source_commit", "source_date", "snapshot", "observed", "evidence": [result]}`; a result is `{"path", "quote", "status": "verified"|"mismatched"|"unbound"|"excluded"|"unreadable", "commit", "label", "date", "at_snapshot", "blob", "class": "code"|"record"}`.

- [ ] **Step 1: Write the failing tests** (`tests/test_import.py`)

```python
import json
import os
import stat
import subprocess

from helpers import OUTPUT, fact, make_vault, save

GIT = ["git", "-c", "user.name=Tester", "-c", "user.email=tester@example.org"]
ROUTING = "subscribers: {}\nowner: billing\n"
ADR = "# ADR 0001\n\nWe decided to route every message by its thread owner.\n"
NOTE = ("---\nname: route-by-owner\ndescription: messages go to the thread owner\nmetadata:\n  type: project\n---\n\n"
        "Every message is routed to its thread owner (`docs/adr/0001.md`).\n")


def git(env, *args):
    result = subprocess.run([*GIT, "-C", str(env.project), *args], capture_output=True, text=True, env=env.environ)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def write(env, path, text):
    target = env.project / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit(env, message="change"):
    git(env, "add", "-A")
    git(env, "commit", "-q", "-m", message)
    return git(env, "rev-parse", "HEAD")


def init(env):
    env.config()
    git(env, "init", "-q", "-b", "main")
    write(env, ".git/info/exclude", "val3.toml\n")


def unit(sha, path="config/routing.yaml", unit_id="kb-0001"):
    return (f"---\nid: {unit_id}\nevidence: verifiable\nprovenance:\n  - {path} at {sha}\n---\n\n"
            "# Routing ships no subscribers\n\nAs of 2026-09-08 routing.yaml has no subscribers.\n")


def adopter(env):
    """A validated-memory adopter: one unit with provenance, one note naming an ADR; val3.toml excluded."""
    init(env)
    write(env, "config/routing.yaml", ROUTING)
    write(env, "docs/adr/0001.md", ADR)
    first = commit(env, "code")
    write(env, "knowledge/kb-0001-routing.md", unit(first))
    write(env, "memory/MEMORY.md", "# Agent memory\n\n- [Route by owner](route-by-owner.md) — thread owner\n")
    write(env, "memory/route-by-owner.md", NOTE)
    second = commit(env, "knowledge")
    return first, second


def answer(env, call, candidate=None, skip=None):
    (env.fake / f"response-{call}.json").write_text(json.dumps(
        {"type": "result", "is_error": False, "total_cost_usd": 0.001,
         "structured_output": {"candidate": candidate, "skip": skip}}))


def unit_candidate(**changes):
    candidate = {"kind": "fact", "title": "Routing ships no subscribers",
                 "statement": "As of 2026-09-08 the routing configuration had no subscribers.",
                 "keywords": ["routing", "subscribers", "suscriptores"],
                 "evidence": [{"path": "config/routing.yaml", "quote": "subscribers: {}"}],
                 "observed": "2026-09-08"}
    candidate.update(changes)
    return candidate


def note_candidate(**changes):
    candidate = {"kind": "decision", "title": "Messages go to the thread owner",
                 "statement": "ADR 0001 recorded the decision to route every message by its thread owner.",
                 "keywords": ["routing", "owner"],
                 "evidence": [{"path": "docs/adr/0001.md", "quote": "route every message by its thread owner"}]}
    candidate.update(changes)
    return candidate


def dry_run(env):
    return env.run("import", "--dry-run", "--out", str(env.root / "manifest.json"))


def manifest(env):
    return json.loads((env.root / "manifest.json").read_text(encoding="utf-8"))


def test_dry_run_checks_quotes_and_writes_a_private_manifest(env):
    first, second = adopter(env)
    answer(env, 0, unit_candidate())
    answer(env, 1, note_candidate(evidence=[
        {"path": "docs/adr/0001.md", "quote": "route every message by its thread owner"},
        {"path": "docs/adr/0001.md", "quote": "we never decided this at all"},
        {"path": "config/routing.yaml", "quote": "owner: billing"},
        {"path": "memory/route-by-owner.md", "quote": "Every message is routed"}]))
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"manifest: {env.root / 'manifest.json'}",
        "sources: 2 ready, 0 skipped, 0 superseded, 0 uncommitted, 0 extractor-failed, 0 invalid, 0 unreadable",
        "evidence: 2 verified (2 at snapshot), 1 mismatched, 1 unbound, 1 excluded, 0 unreadable",
        "2 candidates, 0 unsupported; 0 capped, 0 differ from the working tree; cost $0.0020"]
    assert stat.S_IMODE(os.stat(env.root / "manifest.json").st_mode) == 0o600
    data = manifest(env)
    assert data["format"] == 1 and data["vault"] == env.vault and len(data["batch"]) == 16
    assert data["snapshot"]["commit"] == second
    unit_record, note_record = data["sources"]
    assert unit_record["path"] == "knowledge/kb-0001-routing.md" and unit_record["outcome"] == "ready"
    assert unit_record["candidate"]["evidence"] == [
        {"kind": "file", "path": "config/routing.yaml", "commit": first,
         "reproduction": f"git show {first}:config/routing.yaml", "quote": "subscribers: {}"}]
    meta = unit_record["candidate"]["import"]
    assert (meta["source_label"], meta["source_commit"], meta["observed"]) == ("snapshot", second, "2026-09-08")
    assert (meta["evidence"][0]["label"], meta["evidence"][0]["class"]) == ("source commit", "code")
    note_meta = note_record["candidate"]["import"]
    assert [e["status"] for e in note_meta["evidence"]] == ["verified", "mismatched", "unbound", "excluded"]
    assert (note_meta["source_label"], note_meta["evidence"][0]["class"]) == ("note snapshot", "record")
    calls = env.calls()
    assert len(calls) == 2
    assert calls[0]["stdin"].startswith(f"SOURCE knowledge/kb-0001-routing.md (unit; snapshot {second[:7]}, ")
    assert f"FILE config/routing.yaml @ {first[:7]} (source commit, " in calls[0]["stdin"]
    assert f"FILE docs/adr/0001.md @ {second[:7]} (note snapshot, " in calls[1]["stdin"]
    assert "VAL3 importer. Prompt version: 1." in " ".join(calls[0]["argv"])


def test_a_quote_gone_from_the_snapshot_still_verifies_at_its_commit(env):
    adopter(env)
    write(env, "config/routing.yaml", "subscribers: {billing: [ops]}\n")
    third = commit(env, "subscribers")
    answer(env, 0, unit_candidate())
    answer(env, 1, None, "derivable")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "sources: 1 ready, 1 skipped," in result.stdout
    assert "evidence: 1 verified (0 at snapshot)," in result.stdout
    data = manifest(env)
    assert data["snapshot"]["commit"] == third
    assert data["sources"][0]["candidate"]["import"]["evidence"][0]["at_snapshot"] is False
    assert data["sources"][1]["skip"] == "derivable"


def test_unusable_answers_are_reported_and_the_run_goes_on(env):
    adopter(env)
    answer(env, 0, unit_candidate(kind="recipe"))
    answer(env, 1, None, "because")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "sources: 0 ready, 0 skipped, 0 superseded, 0 uncommitted, 0 extractor-failed, 2 invalid," in result.stdout
    errors = [record["error"] for record in manifest(env)["sources"]]
    assert "an imported candidate is a fact, decision or preference" in errors[0]
    assert "neither a candidate nor a known skip" in errors[1]


def test_evidence_of_the_wrong_shape_is_invalid(env):
    adopter(env)
    answer(env, 0, unit_candidate(evidence="config/routing.yaml"))
    answer(env, 1, None, "not-knowledge")
    result = dry_run(env)
    assert "1 invalid" in result.stdout
    assert 'evidence must be a list of {"path", "quote"} objects' in manifest(env)["sources"][0]["error"]


def test_an_extractor_failure_is_counted(env):
    adopter(env)
    (env.fake / "exit").write_text("1")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "2 extractor-failed" in result.stdout
    assert "fake extractor failure" in manifest(env)["sources"][0]["error"]


def test_dry_run_needs_the_repository_root_and_some_sources(env):
    env.config()
    result = dry_run(env)
    assert result.returncode == 1
    assert "is not the root of a git repository" in result.stderr
    init(env)
    write(env, "README.md", "nothing to import\n")
    commit(env)
    result = dry_run(env)
    assert result.returncode == 1
    assert "has no knowledge/*.md or memory/*.md" in result.stderr


def test_paths_with_spaces_and_accents(env):
    init(env)
    write(env, "docs/guía rápida.md", "Las facturas se cierran el día 5 de cada mes.\n")
    write(env, "memory/cierre de facturas.md", "El cierre es el día 5 (`docs/guía rápida.md`).\n")
    commit(env)
    answer(env, 0, {"kind": "fact", "title": "Cierre mensual el día 5",
                    "statement": "La guía rápida fija el cierre de facturas el día 5.",
                    "evidence": [{"path": "docs/guía rápida.md", "quote": "se cierran el día 5 de cada mes"}]})
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "evidence: 1 verified (1 at snapshot)," in result.stdout
    assert manifest(env)["sources"][0]["path"] == "memory/cierre de facturas.md"


def test_save_refuses_file_evidence(env):
    make_vault(env)
    candidate = fact(evidence=[{"kind": "file", "path": "config/routing.yaml", "commit": "a" * 40,
                                "quote": "subscribers: {}"}])
    result = save(env, candidate, env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 1
    assert "file evidence comes only from `val3 import`" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_import.py`
Expected: FAIL — `invalid choice: 'import'` (exit 2) for the import tests; the save test fails on the message (`unknown keys: commit, path`).

- [ ] **Step 3: Accept `file` evidence and the `import` block for imports only (`src/val3/candidate.py`)**

Add below `MIN_QUOTE`:

```python
_SHA = re.compile(r"[0-9a-f]{40}")
```

Change the signature and the first lines of `validate`, and the evidence and empty-evidence lines inside it:

```python
def validate(data: object, imported: bool = False) -> dict:
    """A checked, normalised candidate. `imported` admits `file` evidence, an `import` block and no evidence:
    only `val3 import` and the review of its items pass it (ADR 0003)."""
    optional = {"keywords", "indexes", "evidence", "supersedes", "recipe", "tool"} | ({"import"} if imported else set())
    obj = _object(data, "candidate", {"kind", "title", "statement"}, optional)
```

```python
        "evidence": [_evidence(item, i, imported)
                     for i, item in enumerate(_list(obj.get("evidence", []), "evidence"))],
```

```python
    if kind in ("fact", "preference", "decision") and not out["evidence"] and not imported:
        raise Val3Error(f"a {kind} needs at least one evidence item")
    if imported:
        if not isinstance(obj.get("import"), dict):
            raise Val3Error('an imported candidate needs its "import" block')
        out["import"] = obj["import"]
```

Replace `_evidence` with:

```python
def _evidence(data: object, i: int, imported: bool = False) -> dict:
    where = f"evidence[{i}]"
    if isinstance(data, dict) and data.get("kind") == "file":
        if not imported:
            raise Val3Error(f"{where}: file evidence comes only from `val3 import`")
        return _file_evidence(data, where)
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


def _file_evidence(data: dict, where: str) -> dict:
    """A quote the importer found in `path` at `commit`; the reproduction is derived, never supplied."""
    obj = _object(data, where, {"kind", "path", "commit", "quote"}, {"reproduction"})
    path = _text(obj["path"], f"{where}.path").strip()
    if path.startswith("/") or path.startswith("-"):
        raise Val3Error(f"{where}.path must be relative to the repository root")
    commit = obj["commit"]
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        raise Val3Error(f"{where}.commit must be a full 40-character commit id")
    quote = _text(obj["quote"], f"{where}.quote").replace("\r\n", "\n").strip()
    if len(quote) < MIN_QUOTE:
        raise Val3Error(f"{where}.quote must hold at least {MIN_QUOTE} characters")
    return {"kind": "file", "path": path, "commit": commit, "reproduction": f"git show {commit}:{path}",
            "quote": quote}
```

- [ ] **Step 4: Let `run_extractor` take a prompt and a schema (`src/val3/extract.py`)**

Rename `_cost` to `cost` (definition and its one use in `extract`). Replace `run_extractor` with:

```python
def run_extractor(project: Project, material: str, prompt: str = PROMPT, schema: dict = SCHEMA) -> dict:
    command = [*shlex.split(os.environ.get("VAL3_EXTRACTOR", "claude")), "-p",
               "--model", project.extractor_model, "--setting-sources", "", "--tools", "",
               "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
               "--output-format", "json", "--json-schema", json.dumps(schema), "--system-prompt", prompt]
    workdir = state_dir() / "extractor"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        done = subprocess.run(command, input=material, capture_output=True, text=True, timeout=TIMEOUT,
                              cwd=workdir, env={**os.environ, "VAL3_EXTRACTING": "1"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExtractorError(f"extractor did not run: {exc}") from None
    if done.returncode != 0:
        raise ExtractorError(f"extractor exited {done.returncode}: {done.stderr.strip()[:300]}")
    try:
        result = json.loads(done.stdout)
        answer = result["structured_output"]
    except (ValueError, KeyError, TypeError, RecursionError):
        raise ExtractorError("extractor output is not the expected JSON") from None
    if result.get("is_error") or not isinstance(answer, dict):
        raise ExtractorError("extractor output is not the expected JSON")
    return result
```

In `extract`, right after `result = run_extractor(...)`, add:

```python
        if not isinstance(result["structured_output"].get("candidates"), list):
            raise ExtractorError("extractor output is not the expected JSON")
```

- [ ] **Step 5: Write the import prompt (`src/val3/import_prompt.md`)**

```markdown
VAL3 importer. Prompt version: 1.

You read one source of a validated-memory 2.x adopter (a knowledge unit or a harness memory note) and
the repository files it rests on, as they were at a fixed commit. You propose at most one entry for a
verified memory that later sessions receive instead of investigating again. A person reviews every
entry you propose. Answer only through the JSON schema: {"candidate": <candidate or null>,
"skip": <reason or null>}.

Material
- SOURCE: the unit or note to import. SUPERSEDED SOURCE: an older source the SOURCE replaces, wholly
  or in part; keep from it only what the SOURCE says survives.
- FILE <path> @ <commit> (<label>, <date>): a repository file at that commit. "source commit" is the
  commit the source names; "note snapshot" is the last commit that changed the note: a repository
  state, not the date of the observation.

Skip (candidate null) with one of these reasons
- "derivable": everything the source says is answered by reading the code, schema or configuration now;
- "retired": the source describes something it, or its successor, says no longer exists;
- "not-knowledge": progress logs, plans, pending work, indexes of other notes, import bookkeeping.

The candidate
{"kind": "fact | decision | preference",
 "title": "one line, at most 200 characters",
 "statement": "the fact, the decision or the preference",
 "keywords": ["search terms in English and Spanish"],
 "indexes": ["knowledge index names, e.g. routing"],
 "evidence": [{"path": "a FILE path", "quote": "text copied literally from that FILE"}],
 "observed": "YYYY-MM-DD, when the source states when it was observed or decided (optional)"}
- fact: a statement about the environment, data, systems or project; decision: a choice the project or
  the user made; preference: how the user wants work done (feedback notes).
- Keep the source's scope and conditions in the statement: "ADR 0011 specified …", "the report
  recorded on 2026-09-21 …", "an authorization for one goal; check that it still holds before reuse".
  Never turn a dated measurement or a document's claim into a timeless fact.
- Keep the language of the source.
- Never copy passwords, tokens, keys or private personal data into any field.

Evidence
- Quote only FILE blocks, character for character, at least 8 characters: the distinctive part that
  supports the statement. Never quote SOURCE or SUPERSEDED SOURCE, never paraphrase, never include a
  "[… N more characters]" marker.
- Cite only paths given as FILE. A statement no FILE supports gets an empty evidence list: the person
  decides it.
- Each quote must support part of the statement; a quote that only shows that a file exists is not
  evidence.
```

- [ ] **Step 6: Write `src/val3/importer.py`**

```python
"""`val3 import`: the knowledge of a validated-memory 2.x adopter, as review items (import design).

A dry run reads one pinned commit through git objects, never the working tree; asks the extractor for one
candidate per source; checks each quote with `git show` at a commit the importer fixes; and writes a
manifest. Apply re-checks the manifest against the repository and queues its candidates: nothing is admitted
without a person (ADR 0003)."""
import json
import os
import re
import secrets
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from val3 import Val3Error
from val3.candidate import MIN_QUOTE, validate
from val3.config import Project
from val3.extract import ExtractorError, cost, run_extractor

MANIFEST_FORMAT = 1
FILE_CLIP = 20_000
MATERIAL_CLIP = 60_000
KINDS = ("fact", "decision", "preference")
SKIPS = ("derivable", "retired", "not-knowledge")
OUTCOMES = ("ready", "skipped", "superseded", "uncommitted", "extractor-failed", "invalid", "unreadable")
PROMPT = files("val3").joinpath("import_prompt.md").read_text(encoding="utf-8")
SCHEMA = {"type": "object", "required": ["candidate", "skip"], "properties": {
    "candidate": {"type": ["object", "null"]}, "skip": {"type": ["string", "null"]}}}
EXCLUDED_DIRS = ("knowledge/", "memory/", ".validated-memory/")
EXCLUDED_FILES = ("knowledge-index.md", "knowledge-extension.md", "validated-memory.md",
                  "validated-memory-profile.md", "journal.jsonl")
CODE_SUFFIXES = (".py", ".sql", ".yaml", ".yml", ".toml", ".json", ".sh", ".service", ".timer", ".ini",
                 ".cfg", ".conf", ".js", ".ts")
_SOURCE = re.compile(r"(knowledge|memory)/[^/]+\.md")
_PROVENANCE = re.compile(r"^\s*-\s+(\S.*?)\s+at\s+([0-9a-f]{40})\s*$", re.M)


@dataclass(frozen=True)
class Cited:
    path: str
    commit: str
    label: str      # "source commit" (a unit's provenance) or "note snapshot" (a note's last change)
    date: str       # the commit's date, ISO 8601
    readable: bool  # False when the object is missing or binary


def excluded(path: str) -> bool:
    """validated-memory's own files and memory exports: citing them would be circular (design §4.4)."""
    return path.startswith(EXCLUDED_DIRS) or path in EXCLUDED_FILES or path.rsplit("/", 1)[-1] == "memory.html"


def _is_source(path: str) -> bool:
    return bool(_SOURCE.fullmatch(path)) and path != "memory/MEMORY.md"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Val3Error(f"git did not run: {exc}") from None


def _out(repo: Path, *args: str) -> str:
    done = _git(repo, *args)
    if done.returncode != 0:
        raise Val3Error(f"git {args[0]} failed: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout.decode("utf-8", errors="replace")


def _read(repo: Path, commit: str, path: str) -> tuple[str | None, str | None]:
    """(text, blob id) of `path` at `commit`; the text is None when the object is missing or binary."""
    done = _git(repo, "rev-parse", "--verify", "--quiet", f"{commit}:{path}")
    if done.returncode != 0:
        return None, None
    blob = done.stdout.decode().strip()
    data = _git(repo, "cat-file", "blob", blob).stdout
    if b"\0" in data:
        return None, blob
    try:
        return data.decode("utf-8").replace("\r\n", "\n"), blob
    except UnicodeDecodeError:
        return None, blob


def _tree(repo: Path, commit: str) -> list[str]:
    return [path for path in _out(repo, "ls-tree", "-r", "-z", "--name-only", commit).split("\0") if path]


def _date(repo: Path, commit: str, cache: dict) -> str:
    if commit not in cache:
        cache[commit] = _out(repo, "show", "-s", "--format=%cI", commit).strip()
    return cache[commit]


def _root(project: Project) -> Path:
    """The adopter's repository: the directory of val3.toml, which must be the repository root."""
    done = _git(project.root, "rev-parse", "--show-toplevel")
    top = done.stdout.decode().strip() if done.returncode == 0 else ""
    if not top or Path(top).resolve() != project.root.resolve():
        raise Val3Error(f"{project.root} is not the root of a git repository: "
                        "run `val3 import` at the adopter's root, next to its val3.toml")
    return project.root.resolve()


def _remote(repo: Path) -> str:
    done = _git(repo, "remote", "get-url", "origin")
    return done.stdout.decode().strip() if done.returncode == 0 else ""


def _frontmatter(text: str) -> str:
    match = re.match(r"---\n(.*?)\n---", text, re.S)
    return match[1] if match else ""


def _cited(repo: Path, head: str, path: str, text: str, dates: dict) -> tuple[str, str, list[Cited]]:
    """(label, commit) of the source itself and the files it rests on (design §4.3); excluded paths are left out."""
    if path.startswith("knowledge/"):
        pairs = _PROVENANCE.findall(_frontmatter(text))
        label, commit, cited_label = "snapshot", head, "source commit"
    else:
        commit = _out(repo, "log", "-1", "--format=%H", head, "--", path).strip()
        pairs = [(p, commit) for p in _tree(repo, commit) if ("/" in p or "." in p) and p in text]
        label, cited_label = "note snapshot", "note snapshot"
    cited, seen = [], set()
    for cited_path, cited_commit in pairs:
        if cited_path in seen or excluded(cited_path):
            continue
        seen.add(cited_path)
        content, _ = _read(repo, cited_commit, cited_path)
        cited.append(Cited(cited_path, cited_commit, cited_label, _date(repo, cited_commit, dates),
                           content is not None))
    return label, commit, cited


def _meta(repo: Path, head: str, path: str, blob: str, batch: str, label: str, commit: str, dates: dict) -> dict:
    return {"batch": batch, "source": path, "source_kind": "unit" if path.startswith("knowledge/") else "note",
            "source_blob": blob, "source_label": label, "source_commit": commit,
            "source_date": _date(repo, commit, dates), "snapshot": head}


def _material(repo: Path, meta: dict, text: str, older: list[tuple[str, str]], cited: list[Cited]) -> tuple[str, bool]:
    """What the extractor reads, and whether a file was clipped or left out by the limits."""
    parts = [f"SOURCE {meta['source']} ({meta['source_kind']}; {meta['source_label']} "
             f"{meta['source_commit'][:7]}, {meta['source_date']})\n{text}"]
    parts += [f"SUPERSEDED SOURCE {path} (the source above replaces it, wholly or in part)\n{old}"
              for path, old in older]
    size, capped = sum(len(part) for part in parts), False
    for item in cited:
        if not item.readable:
            continue
        content, _ = _read(repo, item.commit, item.path)
        clipped = content[:FILE_CLIP]
        if len(content) > FILE_CLIP:
            clipped += f"\n[… {len(content) - FILE_CLIP} more characters]"
            capped = True
        block = f"FILE {item.path} @ {item.commit[:7]} ({item.label}, {item.date})\n{clipped}"
        if size + len(block) > MATERIAL_CLIP:
            capped = True
            continue
        parts.append(block)
        size += len(block)
    return "\n\n".join(parts), capped


def verify(repo: Path, snapshot: str, cited: dict[str, Cited], path: str, quote: str) -> dict:
    """The outcome of one evidence item (design §4.7): the importer binds the commit, never the model."""
    quote = quote.replace("\r\n", "\n").strip()
    result = {"path": path, "quote": quote, "status": "verified", "commit": None, "label": None, "date": None,
              "at_snapshot": None, "blob": None, "class": "code" if path.endswith(CODE_SUFFIXES) else "record"}
    item = cited.get(path)
    if excluded(path):
        result["status"] = "excluded"
    elif item is None:
        result["status"] = "unbound"
    else:
        result.update(commit=item.commit, label=item.label, date=item.date)
        text, blob = _read(repo, item.commit, path)
        if text is None:
            result["status"] = "unreadable"
        elif len(quote) < MIN_QUOTE or quote not in text:
            result["status"] = "mismatched"
        else:
            now, _ = _read(repo, snapshot, path)
            result.update(blob=blob, at_snapshot=now is not None and quote in now)
    return result


def build(repo: Path, head: str, cited: dict[str, Cited], candidate: object, meta: dict) -> dict:
    """The review payload for one extractor candidate: its verified file evidence plus the import block."""
    if not isinstance(candidate, dict):
        raise Val3Error("the extractor answered neither a candidate nor a known skip")
    if candidate.get("kind") not in KINDS:
        raise Val3Error("an imported candidate is a fact, decision or preference")
    items = candidate.get("evidence", [])
    if not isinstance(items, list) or not all(
            isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("quote"), str)
            for item in items):
        raise Val3Error('evidence must be a list of {"path", "quote"} objects')
    results = [verify(repo, head, cited, item["path"], item["quote"]) for item in items]
    observed = candidate.get("observed")
    data = {key: candidate[key] for key in ("kind", "title", "statement", "keywords", "indexes") if key in candidate}
    data["evidence"] = [{"kind": "file", "path": r["path"], "commit": r["commit"], "quote": r["quote"]}
                        for r in results if r["status"] == "verified"]
    data["import"] = {**meta, "observed": observed if isinstance(observed, str) else None, "evidence": results}
    return validate(data, imported=True)


def _source(project: Project, repo: Path, head: str, path: str, batch: str, dates: dict,
            superseded: dict[str, str], predecessors: list[str]) -> dict:
    """One source through extraction and verification; its manifest record."""
    text, blob = _read(repo, head, path)
    record = {"path": path, "kind": "unit" if path.startswith("knowledge/") else "note", "blob": blob,
              "outcome": "ready", "by": superseded.get(path), "skip": None, "error": None, "capped": False,
              "cited": [], "candidate": None, "cost": 0.0, "dirty": False}
    if path in superseded:
        record["outcome"] = "superseded"
        return record
    if text is None:
        record["outcome"] = "unreadable"
        return record
    label, commit, cited = _cited(repo, head, path, text, dates)
    meta = _meta(repo, head, path, blob, batch, label, commit, dates)
    record["cited"] = [asdict(item) for item in cited]
    older = [(old, _read(repo, head, old)[0] or "") for old in predecessors]
    material, record["capped"] = _material(repo, meta, text, older, cited)
    try:
        result = run_extractor(project, material, PROMPT, SCHEMA)
    except ExtractorError as exc:
        record.update(outcome="extractor-failed", error=str(exc))
        return record
    record["cost"] = cost(result)
    answer = result["structured_output"]
    if answer.get("candidate") is None and answer.get("skip") in SKIPS:
        record.update(outcome="skipped", skip=answer["skip"])
        return record
    try:
        record["candidate"] = build(repo, head, {item.path: item for item in cited}, answer.get("candidate"), meta)
    except Val3Error as exc:
        record.update(outcome="invalid", error=str(exc))
    return record


def default_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "val3" / "import"


def _write_manifest(manifest: dict, out: Path | None) -> Path:
    if out is None:
        directory = default_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        out = directory / f"{manifest['vault']}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{manifest['batch']}.json"
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Val3Error(f"{out} already exists") from None
    except OSError as exc:
        raise Val3Error(f"cannot create {out}: {exc.strerror}") from None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1)
    return out


def _summary(records: list[dict]) -> str:
    ready = [r for r in records if r["outcome"] == "ready"]
    evidence = [e for r in ready for e in r["candidate"]["import"]["evidence"]]

    def status(name: str) -> int:
        return sum(e["status"] == name for e in evidence)

    return "\n".join([
        "sources: " + ", ".join(f"{sum(r['outcome'] == o for r in records)} {o}" for o in OUTCOMES),
        f"evidence: {status('verified')} verified ({sum(e['at_snapshot'] is True for e in evidence)} at snapshot), "
        + ", ".join(f"{status(s)} {s}" for s in ("mismatched", "unbound", "excluded", "unreadable")),
        f"{len(ready)} candidates, {sum(not r['candidate']['evidence'] for r in ready)} unsupported; "
        f"{sum(bool(r.get('capped')) for r in records)} capped, "
        f"{sum(bool(r.get('dirty')) for r in records)} differ from the working tree; "
        f"cost ${sum(r.get('cost', 0.0) for r in records):.4f}"])


def dry_run(project: Project, out: Path | None) -> str:
    """Extract and verify every source at one pinned commit; write the manifest; no database writes."""
    repo = _root(project)
    head = _out(repo, "rev-parse", "HEAD").strip()
    sources = sorted(path for path in _tree(repo, head) if _is_source(path))
    if not sources:
        raise Val3Error(f"{repo} has no knowledge/*.md or memory/*.md at {head[:7]}")
    batch, dates = secrets.token_hex(8), {}
    records = [_source(project, repo, head, path, batch, dates, {}, []) for path in sources]
    manifest = {"format": MANIFEST_FORMAT, "batch": batch, "vault": project.home, "project": project.name,
                "repository": str(repo), "remote": _remote(repo),
                "snapshot": {"commit": head, "date": _date(repo, head, dates)}, "sources": records}
    return f"manifest: {_write_manifest(manifest, out)}\n{_summary(records)}"
```

- [ ] **Step 7: Add the `import` command, dry run only (`src/val3/cli.py`)**

Add `importer` to the `from val3 import …` line. In `build_parser`, after the `backup` parser:

```python
    import_parser = sub.add_parser("import", help="queue the knowledge of a validated-memory 2.x adopter for review")
    import_mode = import_parser.add_mutually_exclusive_group(required=True)
    import_mode.add_argument("--dry-run", action="store_true",
                             help="extract and check quotes; write a manifest, no database writes")
    import_parser.add_argument("--out", type=Path,
                               help="manifest file (default: ~/.local/share/val3/import/<vault>-<time>-<batch>.json)")
    import_parser.set_defaults(func=cmd_import)
```

And the command:

```python
def cmd_import(args) -> int | None:
    project = load_project(Path.cwd())
    print(importer.dry_run(project, args.out))
    return None
```

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/python -m pytest -q tests/test_import.py tests/test_extract.py tests/test_save.py`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/val3/candidate.py src/val3/extract.py src/val3/import_prompt.md src/val3/importer.py src/val3/cli.py tests/test_import.py
git commit -m "feat: val3 import --dry-run checks adopter quotes against git and writes a manifest"
```

---

### Task 3: Dry run — superseded sources, working-tree changes, large and binary files

**Files:**
- Modify: `src/val3/importer.py` (`_superseded`, `_dirty`, `dry_run`)
- Test: `tests/test_import.py`

**Interfaces:**
- Consumes: Task 2's `_read`, `_is_source`, `_out`, `_source(project, repo, head, path, batch, dates, superseded, predecessors)`, `_summary`.
- Produces: `importer._superseded(repo, head, sources) -> dict[str, str]` (superseded path → successor path); `importer._dirty(repo) -> (list[str] untracked sources, set[str] changed sources)`. Records of untracked sources: `{"path", "kind", "outcome": "uncommitted"}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_import.py`)

```python
def test_a_superseded_source_is_context_for_its_successor(env):
    first, _ = adopter(env)
    write(env, "knowledge/kb-0002-routing-owner.md", unit(first, unit_id="kb-0002"))
    write(env, "knowledge-index.md", "| id | state |\n|----|-------|\n"
          "| kb-0001 | superseded by kb-0002 |\n| kb-0002 | active |\n")
    write(env, "memory/old-routing.md", "Messages went to a shared queue.\n")
    write(env, "memory/MEMORY.md", "# Agent memory\n\n- [Route by owner](route-by-owner.md) — thread owner\n"
          "- [Old routing](old-routing.md) — superseded by [[route-by-owner]] (before the owner rule)\n")
    commit(env)
    answer(env, 0, None, "not-knowledge")
    answer(env, 1, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "sources: 0 ready, 2 skipped, 2 superseded," in result.stdout
    calls = env.calls()
    assert len(calls) == 2
    assert calls[0]["stdin"].startswith("SOURCE knowledge/kb-0002-routing-owner.md")
    assert "SUPERSEDED SOURCE knowledge/kb-0001-routing.md" in calls[0]["stdin"]
    assert "SUPERSEDED SOURCE memory/old-routing.md" in calls[1]["stdin"] and "shared queue" in calls[1]["stdin"]
    records = {record["path"]: record for record in manifest(env)["sources"]}
    assert records["knowledge/kb-0001-routing.md"]["by"] == "knowledge/kb-0002-routing-owner.md"
    assert records["memory/old-routing.md"]["by"] == "memory/route-by-owner.md"


def test_working_tree_changes_are_reported_and_the_snapshot_is_imported(env):
    adopter(env)
    write(env, "memory/new-note.md", "Written by a running session.\n")
    write(env, "memory/route-by-owner.md", NOTE + "Edited after the commit.\n")
    answer(env, 0, None, "not-knowledge")
    answer(env, 1, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "1 uncommitted" in result.stdout and "1 differ from the working tree" in result.stdout
    calls = env.calls()
    assert len(calls) == 2 and "Edited after the commit" not in calls[1]["stdin"]
    records = {record["path"]: record for record in manifest(env)["sources"]}
    assert records["memory/new-note.md"]["outcome"] == "uncommitted"
    assert records["memory/route-by-owner.md"]["dirty"] is True


def test_large_binary_and_export_files(env):
    init(env)
    write(env, "docs/routing-log.md", "".join(f"line {i:05d} of the routing log\n" for i in range(1000)))
    (env.project / "docs" / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0binary data")
    write(env, "docs/memory.html", "<p>Every message is routed to its thread owner.</p>\n")
    first = commit(env, "docs")
    write(env, "knowledge/kb-0001-log.md",
          f"---\nid: kb-0001\nprovenance:\n  - docs/routing-log.md at {first}\n  - docs/diagram.png at {first}\n"
          f"  - docs/memory.html at {first}\n---\n\n# The routing log\n")
    commit(env, "unit")
    answer(env, 0, {"kind": "fact", "title": "The routing log ends at line 999",
                    "statement": "The routing log at that commit ended at line 999.",
                    "evidence": [{"path": "docs/routing-log.md", "quote": "line 00999 of the routing log"},
                                 {"path": "docs/diagram.png", "quote": "binary data"},
                                 {"path": "docs/memory.html", "quote": "routed to its thread owner"}]})
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "evidence: 1 verified (1 at snapshot), 0 mismatched, 0 unbound, 1 excluded, 1 unreadable" in result.stdout
    assert "1 capped" in result.stdout
    stdin = env.calls()[0]["stdin"]
    assert "[… 10000 more characters]" in stdin
    assert "FILE docs/diagram.png" not in stdin and "FILE docs/memory.html" not in stdin
    cited = manifest(env)["sources"][0]["cited"]
    assert [(item["path"], item["readable"]) for item in cited] == [("docs/routing-log.md", True),
                                                                     ("docs/diagram.png", False)]
```

- [ ] **Step 2: Run the tests to verify which fail**

Run: `.venv/bin/python -m pytest -q tests/test_import.py -k "superseded or working_tree or large_binary"`
Expected: the superseded and working-tree tests FAIL (no superseded outcome, no uncommitted record); the large/binary/export test PASSES already — it pins Task 2's clip, binary and exclusion behaviour.

- [ ] **Step 3: Add `_superseded` and `_dirty` (`src/val3/importer.py`)**

Add the patterns below `_PROVENANCE`:

```python
_UNIT_ID = re.compile(r"knowledge/(kb-\d+)")
_UNIT_SUPERSEDED = re.compile(r"^\|\s*(kb-\d+)\s*\|\s*superseded by (kb-\d+)", re.M)
_NOTE_SUPERSEDED = re.compile(r"\]\(([^)\s]+\.md)\).*?superseded by \[\[([^\]]+)\]\]")
```

And the functions, above `dry_run`:

```python
def _superseded(repo: Path, head: str, sources: list[str]) -> dict[str, str]:
    """{superseded source: its successor}, from knowledge-index.md and memory/MEMORY.md at the snapshot."""
    out = {}
    units = {match[1]: path for path in sources if (match := _UNIT_ID.match(path))}
    index, _ = _read(repo, head, "knowledge-index.md")
    for old, new in _UNIT_SUPERSEDED.findall(index or ""):
        if old in units:
            out[units[old]] = units.get(new, f"knowledge/{new}")
    notes, _ = _read(repo, head, "memory/MEMORY.md")
    for old, new in _NOTE_SUPERSEDED.findall(notes or ""):
        if f"memory/{old}" in sources:
            out[f"memory/{old}"] = f"memory/{new}.md"
    return out


def _dirty(repo: Path) -> tuple[list[str], set[str]]:
    """(untracked sources, changed tracked sources) in the working tree; only reported (design D5)."""
    fields = _out(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--",
                  "knowledge", "memory").split("\0")
    untracked, changed, i = [], set(), 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if len(field) < 4:
            continue
        status, path = field[:2], field[3:]
        if status[0] in "RC":
            i += 1  # a rename or copy is followed by its original path
        if not _is_source(path):
            continue
        if status == "??":
            untracked.append(path)
        else:
            changed.add(path)
    return sorted(untracked), changed
```

Replace the record-building lines of `dry_run` (from `batch, dates = …` to `records = …`) with:

```python
    untracked, changed = _dirty(repo)
    superseded = _superseded(repo, head, sources)
    predecessors: dict[str, list[str]] = {}
    for old, new in superseded.items():
        predecessors.setdefault(new, []).append(old)
    batch, dates = secrets.token_hex(8), {}
    records = [_source(project, repo, head, path, batch, dates, superseded, predecessors.get(path, []))
               for path in sources]
    for record in records:
        record["dirty"] = record["path"] in changed
    records += [{"path": path, "kind": "unit" if path.startswith("knowledge/") else "note", "outcome": "uncommitted"}
                for path in untracked]
```

- [ ] **Step 4: Run the import tests**

Run: `.venv/bin/python -m pytest -q tests/test_import.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/val3/importer.py tests/test_import.py
git commit -m "feat: import reads superseded sources as context and reports working-tree changes"
```

---

### Task 4: `val3 import --apply` — re-check the manifest, queue for review

**Files:**
- Modify: `src/val3/writer.py` (`queue_import`, `log_import`)
- Modify: `src/val3/importer.py` (`apply`, `_load`, `_recheck`, `_imported_blobs`)
- Modify: `src/val3/cli.py` (`--apply`, `cmd_import`)
- Test: `tests/test_import.py`

**Interfaces:**
- Consumes: Task 2's `build`, `_cited`, `_meta`, `_read`, `_root`, `_is_source`, `validate(…, imported=True)`; writer's `_queue`, `_serialize`, `_decision`, `_log`, `origin`, `Outcome`.
- Produces: `writer.queue_import(conn, project, payload) -> Outcome` (status `queued`, `already-queued` or `previously-rejected`; reason `import`); `writer.log_import(conn, project, detail) -> None` (event action `import`); `importer.apply(conn, project, path) -> str`, whose last line is `batch <id>: N queued, N already queued, N previously rejected, N already imported, N refused`, preceded by one `refused <path>: <reason>` line per refused record.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_import.py`)

```python
def ready_manifest(env):
    """A dry run over the fixture adopter: a verified unit candidate and an unsupported note candidate."""
    adopter(env)
    assert env.run("vault", "create", env.vault).returncode == 0
    answer(env, 0, unit_candidate())
    answer(env, 1, note_candidate(evidence=[{"path": "docs/adr/0001.md", "quote": "we never decided this at all"}]))
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    return env.root / "manifest.json"


def apply(env, path, environ=None):
    return env.run("import", "--apply", str(path), environ=environ)


def edit(path, change):
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_apply_queues_the_manifest_once_without_calling_the_model(env):
    path = ready_manifest(env)
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]  # the dry run wrote nothing
    write(env, "docs/later.md", "A commit after the dry run.\n")
    commit(env, "later")  # HEAD moves; the manifest's snapshot does not
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    data = manifest(env)
    assert result.stdout.strip() == (f"batch {data['batch']}: 2 queued, 0 already queued, 0 previously rejected, "
                                     "0 already imported, 0 refused")
    assert len(env.calls()) == 2
    assert env.sql(f"SELECT reason, payload->'import'->>'batch', origin_session FROM {env.vault}.review_item "
                   "ORDER BY id") == [("import", data["batch"], None), ("import", data["batch"], None)]
    assert env.sql(f"SELECT detail->>'batch', detail->>'snapshot', (detail->>'queued')::int FROM {env.vault}.event "
                   "WHERE action = 'import'") == [(data["batch"], data["snapshot"]["commit"], 2)]
    again = apply(env, path)
    assert again.returncode == 0, again.stderr
    assert again.stdout.strip().endswith(
        "0 queued, 0 already queued, 0 previously rejected, 2 already imported, 0 refused")


def test_an_edited_quote_is_refused(env):
    path = ready_manifest(env)

    def change(data):
        candidate = data["sources"][0]["candidate"]
        candidate["evidence"][0]["quote"] = "subscribers: {all}"
        candidate["import"]["evidence"][0]["quote"] = "subscribers: {all}"
    edit(path, change)
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    assert "refused knowledge/kb-0001-routing.md: the manifest does not match the repository" in result.stdout
    assert result.stdout.strip().endswith(
        "1 queued, 0 already queued, 0 previously rejected, 0 already imported, 1 refused")
    assert env.sql(f"SELECT payload->'import'->>'source' FROM {env.vault}.review_item") == [
        ("memory/route-by-owner.md",)]


def test_a_manifest_that_is_not_one_is_rejected_before_git_runs(env):
    path = ready_manifest(env)
    edit(path, lambda data: data["snapshot"].update(commit="--output=/tmp/val3-injected"))
    result = apply(env, path)
    assert result.returncode == 1
    assert "is not a val3 import manifest" in result.stderr
    assert not os.path.exists("/tmp/val3-injected")


def test_apply_checks_the_person_and_the_vault(env):
    path = ready_manifest(env)
    result = apply(env, path, environ={"CLAUDECODE": "1"})
    assert result.returncode == 1 and "outside the agent session" in result.stderr
    edit(path, lambda data: data.update(vault="another_vault"))
    result = apply(env, path)
    assert result.returncode == 1 and "is for vault another_vault" in result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]


def test_import_usage(env):
    env.config()
    assert env.run("import").returncode == 2
    assert env.run("import", "--dry-run", "--apply", "m.json").returncode == 2
    assert env.run("import", "--apply", "m.json", "--out", "x.json").returncode == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_import.py -k "apply or edited or not_one or usage"`
Expected: FAIL — `unrecognized arguments: --apply` (exit 2).

- [ ] **Step 3: Add `queue_import` and `log_import` to `src/val3/writer.py`** (after `decide`)

```python
def queue_import(conn, project: Project, payload: dict) -> Outcome:
    """Queue one imported candidate for review: an import never admits (ADR 0003)."""
    candidate = validate(payload, imported=True)
    vault = project.home
    with conn.transaction():
        _serialize(conn, vault)
        review_id, created = _queue(conn, vault, "import", candidate, None, origin(project, None))
        if not created and _decision(conn, vault, review_id) == "rejected":
            return Outcome("previously-rejected", f"review #{review_id}", "import")
    return Outcome("queued" if created else "already-queued", f"review #{review_id}", "import")


def log_import(conn, project: Project, detail: dict) -> None:
    """One event per applied batch: repository, snapshot and counts."""
    with conn.transaction():
        _log(conn, project.home, "import", origin(project, None), detail=detail)
```

- [ ] **Step 4: Add `apply` to `src/val3/importer.py`**

Add the imports `from val3.db import vsql` and `from val3.writer import log_import, queue_import`, the pattern `_HEX = re.compile(r"[0-9a-f]+")` below `_SOURCE`, and these functions at the end:

```python
APPLY_COUNTS = ("queued", "already-queued", "previously-rejected", "already-imported", "refused")


def _load(path: Path) -> dict:
    """A manifest whose snapshot and batch are hex before any of it reaches a git argument."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Val3Error(f"cannot read {path}: {exc.strerror}") from None
    except (ValueError, RecursionError):
        manifest = None
    snapshot = manifest.get("snapshot") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT
            or not isinstance(manifest.get("sources"), list) or not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("commit"), str) or not _HEX.fullmatch(snapshot["commit"])
            or len(snapshot["commit"]) != 40 or not isinstance(manifest.get("batch"), str)
            or not _HEX.fullmatch(manifest["batch"]) or not isinstance(manifest.get("repository"), str)):
        raise Val3Error(f"{path} is not a val3 import manifest (format {MANIFEST_FORMAT})")
    return manifest


def _recheck(repo: Path, head: str, batch: str, record: object, dates: dict) -> str | None:
    """Why a manifest record no longer matches the repository, or None (design §4.9): the source blob, the
    cited files and every quote are derived again from git; only the extractor's wording is taken as is."""
    if not isinstance(record, dict) or not isinstance(record.get("path"), str) or not _is_source(record["path"]):
        return "malformed record"
    path, candidate = record["path"], record.get("candidate")
    text, blob = _read(repo, head, path)
    if text is None or blob != record.get("blob"):
        return "the source differs from the snapshot"
    label, commit, cited = _cited(repo, head, path, text, dates)
    try:
        answer = {key: candidate[key] for key in ("kind", "title", "statement", "keywords", "indexes")
                  if key in candidate}
        answer["evidence"] = [{"path": item["path"], "quote": item["quote"]}
                              for item in candidate["import"]["evidence"]]
        answer["observed"] = candidate["import"].get("observed")
        rebuilt = build(repo, head, {item.path: item for item in cited}, answer,
                        _meta(repo, head, path, blob, batch, label, commit, dates))
    except (Val3Error, KeyError, TypeError, AttributeError):
        return "malformed record"
    return None if rebuilt == candidate else "the manifest does not match the repository"


def _imported_blobs(conn, vault: str) -> set[str]:
    return {blob for (blob,) in conn.execute(vsql(vault, """
        SELECT payload->'import'->>'source_blob' FROM {v}.review_item WHERE reason = 'import'"""))}


def apply(conn, project: Project, path: Path) -> str:
    """Queue a manifest's ready candidates for review after checking each against the repository again."""
    manifest = _load(path)
    if manifest.get("vault") != project.home:
        raise Val3Error(f"{path} is for vault {manifest.get('vault')}, not the home vault {project.home}")
    repo = _root(project)
    if manifest["repository"] != str(repo):
        raise Val3Error(f"{path} was made in {manifest['repository']}, not in {repo}")
    head, batch = manifest["snapshot"]["commit"], manifest["batch"]
    if _git(repo, "cat-file", "-e", f"{head}^{{commit}}").returncode != 0:
        raise Val3Error(f"snapshot {head[:7]} of {path} is not in {repo}")
    imported, dates = _imported_blobs(conn, project.home), {}
    counts, lines = dict.fromkeys(APPLY_COUNTS, 0), []
    for record in manifest["sources"]:
        if not isinstance(record, dict) or record.get("outcome") != "ready":
            continue
        reason = _recheck(repo, head, batch, record, dates)
        if reason:
            counts["refused"] += 1
            lines.append(f"refused {record.get('path')}: {reason}")
        elif record["blob"] in imported:
            counts["already-imported"] += 1
        else:
            counts[queue_import(conn, project, record["candidate"]).status] += 1
            imported.add(record["blob"])
    log_import(conn, project, {"batch": batch, "repository": manifest["repository"],
                               "remote": manifest.get("remote"), "snapshot": head, **counts})
    lines.append(f"batch {batch}: " + ", ".join(f"{n} {name.replace('-', ' ')}" for name, n in counts.items()))
    return "\n".join(lines)
```

- [ ] **Step 5: Add `--apply` to the command (`src/val3/cli.py`)**

In the `import` parser, add to the mutually exclusive group:

```python
    import_mode.add_argument("--apply", type=Path, metavar="FILE", help="queue a manifest's candidates for review")
```

Replace `cmd_import` with:

```python
def cmd_import(args) -> int | None:
    if args.apply and args.out:
        print("val3: error: --out goes with --dry-run", file=sys.stderr)
        return 2
    project = load_project(Path.cwd())
    if args.dry_run:
        print(importer.dry_run(project, args.out))
        return None
    _person_only()
    with connect(project.service) as conn:
        print(importer.apply(conn, project, args.apply))
    return None
```

- [ ] **Step 6: Run the import tests**

Run: `.venv/bin/python -m pytest -q tests/test_import.py`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/val3/writer.py src/val3/importer.py src/val3/cli.py tests/test_import.py
git commit -m "feat: val3 import --apply re-checks the manifest against git and queues it for review"
```

---

### Task 5: Review card and approval of import items

**Files:**
- Modify: `src/val3/views.py` (`review_card` header and branch, new `import_lines`)
- Modify: `src/val3/writer.py` (`decide`)
- Test: `tests/test_import.py`

**Interfaces:**
- Consumes: Task 4's queued items (reason `import`, payload = validated candidate with its `import` block).
- Produces: `views.import_lines(conn, vault, payload) -> list[str]`; `decide` validates `import` items with `imported=True`, so approval admits `file` evidence rows (session NULL) or no evidence row.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_import.py`)

```python
def queued(env):
    path = ready_manifest(env)
    assert apply(env, path).returncode == 0
    return manifest(env)


def test_card_of_an_imported_candidate(env):
    data = queued(env)
    first = data["sources"][0]["candidate"]["evidence"][0]["commit"]
    card = env.run("review", "show", "1").stdout
    lines = card.splitlines()
    assert lines[0].startswith("review #1 · import · project · ")
    assert lines[0].endswith(f" · batch {data['batch']}")
    assert lines[2].startswith(f"SOURCE knowledge/kb-0001-routing.md (snapshot {data['snapshot']['commit'][:7]}, ")
    assert "observed: 2026-09-08" in card
    assert "[fact]  Routing ships no subscribers" in card
    assert f"  - file config/routing.yaml @ {first[:7]} (" in card
    assert "source commit; at snapshot: yes; code)" in card
    assert "      > subscribers: {}" in card
    assert f"      reproduce: git show {first}:config/routing.yaml  (not run)" in card
    note = env.run("review", "show", "2").stdout
    assert "SOURCE memory/route-by-owner.md (note snapshot " in note
    assert "not verified:\n  - docs/adr/0001.md: mismatched" in note
    assert "no repository evidence: approving attests it" in note


def test_approval_admits_file_evidence_or_the_attestation(env):
    data = queued(env)
    first = data["sources"][0]["candidate"]["evidence"][0]["commit"]
    for review_id in (1, 2):
        result = env.run("review", "approve", str(review_id), "--by", "owner")
        assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT e.id, e.kind, v.kind, v.reproduction, v.quote, v.session_id FROM {env.vault}.entry e "
                   f"LEFT JOIN {env.vault}.evidence v ON v.entry_id = e.id ORDER BY e.id") == [
        (1, "fact", "file", f"git show {first}:config/routing.yaml", "subscribers: {}", None),
        (2, "decision", None, None, None, None)]
    assert env.sql(f"SELECT entry_id, decided_by FROM {env.vault}.review_decision ORDER BY review_id") == [
        (1, "owner"), (2, "owner")]
    shown = env.run("show", f"{env.vault}#1").stdout
    assert f"  - file: git show {first}:config/routing.yaml" in shown and "      > subscribers: {}" in shown
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_import.py -k "card_of_an_imported or approval_admits"`
Expected: FAIL — the card header ends with `session -`; approval fails with `candidate has unknown keys: import`.

- [ ] **Step 3: Validate import items as imports in `decide` (`src/val3/writer.py`)**

In `decide`, replace `candidate = validate(payload)` with:

```python
                candidate = validate(payload, imported=reason == "import")
```

- [ ] **Step 4: Render import items (`src/val3/views.py`)**

In `review_card`, replace the `header = (…)` statement with:

```python
    tag = (f"batch {(payload.get('import') or {}).get('batch', '-')}" if reason == "import"
           else f"session {(session or '-')[:8]}")
    header = (f"review #{review_id} · {reason}{' (notice)' if reason in NOTICES else ''} · {project} · "
              f"{created:%Y-%m-%d} · {tag}")
```

and add a branch before the final `else:` of the reason dispatch:

```python
    elif reason == "import":
        lines += import_lines(conn, vault, payload)
```

Add the function after `candidate_lines`:

```python
def import_lines(conn, vault: str, payload: dict) -> list[str]:
    """An imported candidate: its source, the candidate, and each quote with the commit it was checked at."""
    meta = payload.get("import") or {}
    lines = [f"SOURCE {meta.get('source')} ({meta.get('source_label')} {str(meta.get('source_commit'))[:7]}, "
             f"{str(meta.get('source_date'))[:10]})"]
    if meta.get("observed"):
        lines.append(f"observed: {meta['observed']}")
    lines.append("")
    lines += candidate_lines(conn, vault, {**payload, "evidence": []})
    results = meta.get("evidence") or []
    verified = [item for item in results if item.get("status") == "verified"]
    if verified:
        lines.append("evidence:")
    for item in verified:
        lines.append(f"  - file {item['path']} @ {item['commit'][:7]} ({str(item['date'])[:10]}, {item['label']}; "
                     f"at snapshot: {'yes' if item['at_snapshot'] else 'no'}; {item['class']})")
        lines.extend(f"      > {line}" for line in item["quote"].splitlines())
        lines.append(f"      reproduce: git show {item['commit']}:{item['path']}  (not run)")
    failed = [item for item in results if item.get("status") != "verified"]
    if failed:
        lines.append("not verified:")
        lines.extend(f"  - {item.get('path')}: {item.get('status')}" for item in failed)
    if not verified:
        lines.append("no repository evidence: approving attests it")
    return lines
```

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/val3/views.py src/val3/writer.py tests/test_import.py
git commit -m "feat: review cards show imported sources and quotes; approval admits file evidence"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md` (command list; new section "Importing from validated-memory 2.x"; Status line)
- Modify: `docs/design/2026-10-08-val3-design.md` (sections 6, 7.1, 11)
- Modify: `CONTEXT.md`

- [ ] **Step 1: README — command list.** After the `val3 backup` line of the command list add:

```markdown
- `val3 import --dry-run [--out FILE]` / `val3 import --apply FILE` — queue the knowledge of a
  validated-memory 2.x adopter for review (see Importing from validated-memory 2.x).
```

and add `import --apply` to the sentence listing the commands that refuse to run inside a Claude Code session.

- [ ] **Step 2: README — new section** before `## Development`:

````markdown
## Importing from validated-memory 2.x

`val3 import` reads an adopter's knowledge units (`knowledge/kb-*.md`) and memory notes
(`memory/*.md`) and puts one candidate per source on the review list. It needs `git` and runs at
the adopter's repository root, next to a `val3.toml` that names the vault to fill. The adopter's
tree is only read; to keep `val3.toml` out of its repository, list it in `.git/info/exclude`.

```bash
val3 import --dry-run            # extract and check quotes; writes a manifest, no database writes
val3 import --apply <manifest>   # a person, outside Claude Code: queue the manifest for review
val3 review --tty                # decide every imported candidate
```

The dry run pins HEAD and reads everything from that commit, never from the working tree: changed
notes are imported as committed and untracked ones are only reported. Superseded units and notes are
not candidates; they are read as context by the source that replaces them. Each source and the files
it rests on (a unit's `provenance` commits; for a note, the files it names, at the note's last
commit) go to the extractor, which returns one candidate or a skip (`derivable`, `retired`,
`not-knowledge`). Each quote is checked with `git show` at that commit (`verified`, and whether it is
still at the snapshot), or reported `mismatched`, `unbound` (a file the source does not cite),
`excluded` (validated-memory's own files and memory exports) or `unreadable`. A candidate with no
verified quote is *unsupported*: approving it attests it. The manifest
(`~/.local/share/val3/import/`, mode 0600) holds the exact candidates; `--apply` checks every quote
again against the repository, queues what still matches, and skips sources already imported.

Approval admits the entry with `file` evidence (`git show <commit>:<path>` and the quote); recall
and delivery never read the repository again. Vaults created before this version need
`val3 vault upgrade <vault>`.

How much was verified, after the review (replace `invoices` and the batch id):

```sql
SELECT r.payload->'import'->>'source_kind' AS source,
       CASE WHEN jsonb_array_length(r.payload->'evidence') = 0 THEN 'unsupported'
            WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(r.payload->'import'->'evidence') e
                         WHERE e->>'status' = 'verified' AND (e->>'at_snapshot')::boolean) THEN 'verified at snapshot'
            ELSE 'verified, changed since' END AS evidence,
       coalesce(d.decision, 'pending') AS decision, count(*)
FROM invoices.review_item r LEFT JOIN invoices.review_decision d ON d.review_id = r.id
WHERE r.reason = 'import' AND r.payload->'import'->>'batch' = '<batch>'
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
```

To let one session of the adopter receive the imported entries without capturing anything, put only
the `SessionStart` and `UserPromptSubmit` hooks of Claude Code hooks in its
`.claude/settings.local.json`; without the capture hooks, its `memory/` is never rewritten. Remove
that file and `val3.toml` to undo it.
````

In the README Status line (line 7), change "plans 1 to 4" to "plans 1 to 4 and 3b" and add "import from validated-memory 2.x" to its list.

- [ ] **Step 3: Main design.** In `docs/design/2026-10-08-val3-design.md`:
  - §6, add a table row after the "Fact with evidence quote" row:
    `| Imported candidate (`val3 import`) | Quotes checked with `git show` at a commit the importer fixes; every candidate goes to the review list (ADR 0003). |`
  - §7.1, `evidence` row: kind `(query/command/api/test/user/file)`.
  - §11, replace the `val3 import` bullet with: "`val3 import` (plan 3b) reads a validated-memory 2.x adopter at one pinned commit, checks each quote against the repository files at a commit it fixes, and puts every candidate on the review list (`docs/design/2026-10-12-import-design.md`, ADR 0003)."

- [ ] **Step 4: CONTEXT.md.** Change the Evidence entry to end "…, or the user's literal confirmation; for an imported entry, a quote checked in a repository file at a commit (`file`)." and add:

```markdown
- **Import** — `val3 import`: a validated-memory 2.x adopter's units and notes, checked against its
  repository at one pinned commit and put on the review list; a **manifest** holds a dry run's exact
  candidates, a **batch** id marks what one apply queued.
```

- [ ] **Step 5: Check and commit**

Run: `.venv/bin/python -m pytest -q` (all pass), then read `git diff` of this task: no internal project, host or person name (Global Constraints).

```bash
git add README.md CONTEXT.md docs/design/2026-10-08-val3-design.md
git commit -m "docs: val3 import in the README, the main design and the vocabulary"
```

---

### Task 7: Rollout with the owner on the first adopter (by hand, after merge)

Done by the controller with the owner; not dispatched. Nothing here is committed to the adopter.

1. Merge `feature/plan-3b-import`, run the suite, scan the diff, push (standing authority).
2. `val3 vault create <vault>` on the development service.
3. In the adopter root: write `val3.toml` (service, `project`, `[vaults] home`, `[embedding]` as in
   the first adopted project) and add `val3.toml` to `.git/info/exclude`; `git status` shows no change.
4. `val3 import --dry-run`; read the manifest; report the counts and a sample of candidates to the owner.
5. The owner runs `val3 import --apply <manifest>` and `val3 review --tty`.
6. Run the README measurement query; record the figures in `SESSION.md`.
7. Add the `SessionStart` and `UserPromptSubmit` hooks to the adopter's `.claude/settings.local.json`
   (ignored); the owner starts the adopter session.
8. Session close: `SESSION.md` entry, `TODO.md` (plan 3b done; next: decide automatic admission with
   these figures; withdrawal before any automatic admission or production import), commit.
