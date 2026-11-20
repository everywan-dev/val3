# VAL3 Plan 4 — Delivery and search

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every session starts with an index of every live entry, every typed prompt receives at most three entries chosen by local semantic search above a floor, harness turns receive nothing, and the floor can be measured with `val3 eval`.

**Architecture:** A project turns semantic search on with an `[embedding]` table in `val3.toml`. Vectors of entries (title and statement) are computed by a local HTTP embedding service (Ollama, `bge-m3`) when an entry is admitted and stored in a new append-only table `embedding` of each vault (pgvector). The prompt hook embeds the prompt (plus the end of the previous assistant turn when the prompt is short), scores live entries by cosine similarity in SQL, and injects at most three above the floor; on any failure it falls back to today's full-text search and warns once per session. Each typed prompt leaves a line in a local log, which `val3 eval --draft` turns into a set for a person to label and `val3 eval` measures.

**Tech Stack:** as plans 1 and 2 (Python ≥ 3.11 standard library, `psycopg[binary]`, PostgreSQL 16, pytest, Docker) plus the pgvector extension (image `pgvector/pgvector:0.8.1-pg16`) and an Ollama-compatible embedding service reached with `urllib`.

**Spec:** `docs/design/2026-10-09-semantic-search-design.md` (draft v2 with the decisions of 2026-10-10 in its section 6). Plans 1 and 2 and ADRs 0001-0002 still bind except where this plan says otherwise.

Revised on 2026-10-11 after an external review: one selection rule shared by the hook, recall and `val3 eval` (decision 6); entries without a vector reach the hook by full-text; database errors of the semantic path fall back; vectors keyed by embedding space (decision 13); bounded embedding after an admission (decision 5); unlabelled cases as `null`, vault read as of each case, separate calibration and validation sets (decision 12).

## Verified facts this plan relies on (2026-10-10, development machine)

1. Ollama 0.34.2 `POST /api/embed` with `{"model": "bge-m3", "input": [texts]}` answers `{"model", "embeddings": [[...], ...], "total_duration", "load_duration", "prompt_eval_count"}`; `bge-m3` vectors have 1 024 dimensions and norm 1.0. The body key `keep_alive` (`"24h"`) is accepted and sets how long the model stays loaded. `GET /api/tags` lists the local models with `name` (`bge-m3:latest`) and a 64-hex `digest`, in about 1 ms (2026-10-11).
2. On CPU, a cold call (model not loaded) took 2.4 s and 1.8 s; a warm call took 39 ms. Ollama unloads a model after 5 minutes idle by default. A request abandoned by the client after 0.2 s does **not** load the model.
3. `pgvector/pgvector:0.8.1-pg16` (built 2026-02-13) runs PostgreSQL 16 with extension `vector`. A column of type `vector` without a dimension accepts vectors of any size; `1 - (a <=> b)` is the cosine similarity; comparing vectors of different sizes raises `different vector dimensions` (SQLSTATE class 22, data exception); a zero vector gives `NaN`, and PostgreSQL orders `NaN` above every number, so `NaN >= 0.6` is true.
4. The development container `val3-dev` runs `postgres:16` (16.13, Debian 13) on volume `val3-dev-data`, port `127.0.0.1:5433`, restart policy `unless-stopped`. The pgvector image is Debian 12: moving the data directory between them changes the C library, so the move is done by dump and restore into a new volume.
5. The machine has an NVIDIA GeForce GTX 1060 6 GB. The NVIDIA Container Toolkit 1.20.1 is installed and registered with Docker. Ollama 0.34.2 needs the Windows driver 570 or newer; the driver was updated on 2026-10-10 and, after WSL was restarted, on 2026-10-11 `nvidia-smi` showed driver 581.57 and Ollama detected the GPU (`library=CUDA compute=6.1`, `cuda_v12` libraries; the `cuda_v13` build skips compute 6.1). `bge-m3` on the GPU: cold call 2.3 s, warm short query 42 ms, about 640 tokens 0.24 s. Production is expected to run on CPU: every latency target in this plan is a CPU target.

## Decisions fixed by this plan

1. **Turning it on** — semantic search is on for a project whose `val3.toml` has an `[embedding]` table; `url` (default `http://127.0.0.1:11434`), `model` (default `bge-m3`) and `floor` (default `0.60`, a number strictly between 0 and 1) are optional. Without the table, delivery and recall use full-text search as today.
2. **Cap** — every per-prompt injection holds at most 3 entries (`search.PROMPT_CAP`), semantic or full-text. Tools come first, as today.
3. **Query text** — the prompt; when it has 6 words or fewer, plus a newline and the last 500 characters of the last assistant message with text in the transcript (read from the last 256 KiB of the file). The query text is clipped to 1 000 characters: embedding time grows with length and long multi-topic prompts gain nothing.
4. **Timeouts and loading** — prompt hook 1 s; `val3 recall` 10 s; session-start warm-up 5 s; storing vectors 60 s per batch; `val3 eval` 30 s per query. Every request sends `"keep_alive": "24h"`. The session-start hook sends one warm-up request (a completed one: fact 2) so the first prompt finds the model loaded. Requests bypass proxy settings (`ProxyHandler({})`): the service is local or next to the database.
5. **Vectors** — text `title + "\n" + statement` (`embedding.TEXT_VERSION = 1`); batches of 16; stored for live entries of the home vault that lack a vector in the current space (decision 13), newest first. Right after an admission by `val3 save`, `val3 review approve` or `val3 tool add`, at most one batch (16 entries, so the admitted one is always among them) with a 10 s timeout; the drainer (after each extraction) and `val3 embed` store every missing vector. The admission commits in its own transaction before vectors are stored: a failure of the service or of the vault (for example no `INSERT` privilege on `embedding`) warns and never undoes it. Answers that are not one list of finite, non-zero numbers per text, all of the same size, are refused (fact 3).
6. **One selection rule** — the prompt hook, `val3 recall` and `val3 eval` all choose entries with `search.candidates()` and `search.select()`: candidates are the live entries whose vector in the current space scores at least the floor, plus the full-text hits among live entries **without** a vector in that space (filtered in SQL before the full-text limit, so an entry admitted while the service was down stays reachable by the hook too); `select()` keeps semantic hits at or above the floor, orders tools first, then semantic hits by score, then full-text hits by weight, ties by the newest entry, and cuts at the cap (3 for prompts, `--limit` for recall). The semantic query has no `LIMIT`: the cut happens once, after tools are put first.
7. **Fallback** — no service, a timeout, an unusable answer, a vault without the `embedding` table, or any database error of the semantic path other than a lost connection (for example no `SELECT` privilege on `embedding`): full-text search with the same cap, and one warning per session (its own marker, separate from the "VAL3 is unavailable" one). Connections are in autocommit mode (`db.connect`), so a failed statement leaves the connection usable for the fallback query. A vault without the table names the fix: `val3 vault upgrade <vault>`.
8. **Harness turns** — hook input `source` other than absent, `user` or `sdk`: no injection, no embedding call, no log line.
9. **Prompt log** — `$XDG_STATE_HOME/val3/prompts/<project>.jsonl` (project name with characters outside `[A-Za-z0-9_.-]` replaced by `-`), mode 0600, one line per typed prompt: `at`, `session`, `prompt`, `previous` (the part used, else empty), `method` (`semantic` or `full-text`), `injected` (`ref`, `score`: the cosine similarity, `null` for a full-text hit). Before appending, when the first line is older than 30 days, the file is rewritten without lines older than 30 days. Write failures are ignored.
10. **Schema** — `CREATE EXTENSION IF NOT EXISTS vector` in `common.sql` (schema `public`, like `unaccent`); the table DDL lives in `src/val3/embedding.sql`, run by `vault create` and by `val3 vault upgrade` with `search_path` set to the vault and `public`. `val3 vault upgrade` is idempotent and grants on the new table the `SELECT` and `INSERT` privileges each role holds on `entry`.
11. **Images** — tests and the development server use `pgvector/pgvector:0.8.1-pg16`.
12. **Evaluation** — floors 0.40 to 0.80 in steps of 0.05 (the configured floor marked `*`), plus a `full-text` row (today's search, cap 3, prompt only); columns `injected`, `needed` (share of injected entries listed in `relevant`), `served` (cases with relevant entries that got at least one), `silent` (cases without relevant entries that got nothing); then the mean and maximum embedding latency. Each floor row applies `select()` (decision 6) to the same candidates the hook would get, so it reproduces delivery exactly. `relevant` is `null` in a draft (not labelled yet) and `[]` only when a person decided no entry was needed; `val3 eval` refuses a set with any `null`. A case with `at` (drafts copy it from the prompt log) is evaluated against the vault as it was then: entries created at or before `at`, supersessions recorded at or before `at`; a case without `at` uses the vault as it is. Calibration and validation use two sets from different sessions (drafts carry `session`): the floor is chosen on the first and confirmed on the second (README, "Calibrating the floor").
13. **Embedding space** — vectors are comparable only within one space: model name, model digest (from `GET /api/tags`, entry `name` equal to the model or to `<model>:latest`), number of dimensions and `TEXT_VERSION`. The `embedding` table stores all four; its key is `(entry_id, model, digest, text_version)` and a check ties `dims` to the vector's size. Every search filters on the space of the query vector, so a vector of another build or size is never compared (it counts as missing: the entry is reached by full-text until `val3 embed` stores a vector in the new space). `embed()` asks for the digest and the vectors within one timeout; a model missing from the service is an `EmbeddingError`.

## Global Constraints

- Plan 1 and 2 Global Constraints hold: Python `>=3.11`, standard library plus `psycopg[binary]>=3.2` (no new Python dependency), English everywhere, exit codes `0` ok or warnings only / `1` error / `2` usage, tests drive the CLI as a subprocess and never import `val3`, the writer is the only code that writes vault rows, vault SQL goes through `vsql`, hooks always exit 0.
- Tests never call a real embedding service: they use `tests/fake_embedder.py` (bag-of-words vectors over 256 buckets). Only Task 7 uses Ollama, by hand.
- Nothing leaves the machine: the embedding service is local; no external service is called.
- The prompt hook stays under about 0.5 s for typed prompts on CPU with a warm model (measured in Task 7).
- Work on branch `feature/plan-4-delivery-search`; Conventional Commits; run the suite with `.venv/bin/python -m pytest -q`.

## Review Focus

1. **Vault not upgraded while `[embedding]` is set** — `val3 save` admits and warns naming `val3 vault upgrade <vault>`; `val3 recall` and the prompt hook answer by full-text with that warning (tests in Tasks 2, 3 and 4).
2. **Embedding service down, slow or cold** — entries are admitted, the prompt is answered by full-text within about 1 s, one warning per session; the session-start index still arrives (tests in Tasks 2 and 4).
3. **Unusable answers from the service** — a zero vector (it would score `NaN`, which passes every floor) or an HTTP error stores nothing and warns (test in Task 2).
4. **Upgrade of a vault that already has grants** — readers can read and writers can append the new table without a new `vault grant` (test in Task 1).
5. **Short prompt with a missing or unreadable transcript** — the prompt is read alone, no error, the hook answers (test in Task 4).
6. **Admission never undone** — a service failure and a vault failure while storing vectors (no `INSERT` privilege) both leave the entry admitted and exit 0 with a warning, for `val3 save` and for extraction (tests in Task 2).
7. **Eval reproduces delivery** — a tool ranked fourth by score is delivered by the hook and counted by `val3 eval` alike (test in Task 5); entries without a vector reach the hook by full-text (test in Task 4); a database error in the semantic path falls back on the same connection (test in Task 4).
8. **Embedding space** — a new model digest stores new vectors next to the old ones and searches never mix them (test in Task 2).

---

## File structure

```
src/val3/common.sql        + CREATE EXTENSION vector
src/val3/embedding.sql     new: embedding table and its append-only triggers
src/val3/db.py             create_vault runs embedding.sql; upgrade_vault()
src/val3/config.py         + Embedding, [embedding] parsing, Project.embedding
src/val3/embedding.py      new: embed(), digest(), Space, EmbeddingError, entry_text(), vector_literal(), no_table()
src/val3/writer.py         + store_vectors()
src/val3/extract.py        stores vectors after an extraction
src/val3/search.py         + semantic(), candidates(), select(), find(), Found, query_text(), previous_part(),
                           constants; search() gains space and as_of
src/val3/transcript.py     + last_assistant_text()
src/val3/views.py          session_index lists every live entry
src/val3/hooks.py          prompt: semantic, cap 3, no harness injection, fallback warning, prompt log;
                           session-start warm-up
src/val3/evaluate.py       new: load_cases(), evaluate(), draft()
src/val3/cli.py            + vault upgrade, embed, eval; recall through find(); vectors after admission
tests/conftest.py          pgvector image; embedder fixture; Env.config(embedding=...)
tests/fake_embedder.py     new: stand-in for the embedding service
tests/helpers.py           + embedding_vault()
tests/test_vault.py, test_embed.py (new), test_recall.py, test_hooks.py, test_eval.py (new)
README.md, CONTEXT.md, docs/adr/0002-*.md, docs/design/2026-10-09-semantic-search-design.md
```

---

### Task 1: pgvector and the `embedding` table (`val3 vault upgrade`)

**Files:**
- Create: `src/val3/embedding.sql`
- Modify: `tests/conftest.py:24-27`, `src/val3/common.sql:1`, `src/val3/db.py`, `src/val3/cli.py`, `tests/test_vault.py`

**Interfaces:**
- Consumes: plan 1 `db.create_vault`, `db._exists`, `config.check_vault_name`, `cli._service`.
- Produces: `db.upgrade_vault(conn, name: str) -> bool` (True when it changed the vault); table `<vault>.embedding (entry_id bigint, model text, digest text, text_version smallint, dims integer, vector vector, created_at timestamptz)`, primary key `(entry_id, model, digest, text_version)`, check `vector_dims(vector) = dims`; CLI `val3 vault upgrade NAME [--service S]` printing `upgraded vault NAME` or `vault NAME is up to date`.

- [ ] **Step 1: Move the test server to the pgvector image**

In `tests/conftest.py`, in `pg_port`, replace the image `"postgres:16"` with `"pgvector/pgvector:0.8.1-pg16"`. Pull it once: `docker pull pgvector/pgvector:0.8.1-pg16`.

- [ ] **Step 2: Write the failing tests**

In `tests/test_vault.py`, add `"embedding"` to `TABLES`, add the embedding table to the append-only test, and add the upgrade tests:

```python
TABLES = {"entry", "supersession", "knowledge_index", "entry_index", "evidence", "tool",
          "recipe_parameter", "recipe_step", "step_fact", "recipe_example", "review_item",
          "review_decision", "event", "embedding"}
```

```python
def test_vault_tables_are_append_only(env):
    create(env)
    entry_id = insert_entry(env)
    env.sql(f"INSERT INTO {env.vault}.embedding (entry_id, model, digest, text_version, dims, vector) "
            f"VALUES (%s, 'm', 'd', 1, 2, '[1,0]')", (entry_id,))
    for statement in (f"UPDATE {env.vault}.entry SET title = 'x' WHERE id = {entry_id}",
                      f"DELETE FROM {env.vault}.entry",
                      f"TRUNCATE {env.vault}.event",
                      f"UPDATE {env.vault}.embedding SET model = 'n'",
                      f"TRUNCATE {env.vault}.embedding"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            env.sql(statement)
```

```python
ENTRY = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
         "VALUES ('fact', 't', 's', 'h1', 'p', 'm') RETURNING id")


def test_upgrade_adds_the_embedding_table_and_keeps_grants(env):
    create(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    for role, mode in ((env.reader, "--read"), (env.writer, "--write")):
        assert env.run("vault", "grant", env.vault, role, mode, "--service", "val3test").returncode == 0
    env.sql(f"DROP TABLE {env.vault}.embedding")  # a vault created before this plan
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    entry_id = env.sql(ENTRY.format(v=env.vault), user=env.writer)[0][0]
    env.sql(f"INSERT INTO {env.vault}.embedding (entry_id, model, digest, text_version, dims, vector) "
            "VALUES (%s, 'm', 'd', 1, 2, '[1,0]')", (entry_id,), user=env.writer)
    assert env.sql(f"SELECT count(*) FROM {env.vault}.embedding", user=env.reader) == [(1,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(f"INSERT INTO {env.vault}.embedding (entry_id, model, digest, text_version, dims, vector) "
                "VALUES (%s, 'n', 'd', 1, 2, '[1,0]')", (entry_id,), user=env.reader)


def test_embedding_dimensions_match_the_vector(env):
    create(env)
    entry_id = insert_entry(env)
    with pytest.raises(psycopg.errors.CheckViolation):
        env.sql(f"INSERT INTO {env.vault}.embedding (entry_id, model, digest, text_version, dims, vector) "
                "VALUES (%s, 'm', 'd', 1, 3, '[1,0]')", (entry_id,))


def test_upgrade_of_a_current_vault_changes_nothing(env):
    create(env)
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"vault {env.vault} is up to date"


def test_upgrade_of_a_missing_vault_fails(env):
    result = env.run("vault", "upgrade", "v_missing", "--service", "val3test")
    assert result.returncode == 1
    assert "no vault v_missing" in result.stderr
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_vault.py -q`
Expected: FAIL — `embedding` missing from the created tables, and `invalid choice: 'upgrade'` (exit 2) for the upgrade tests.

- [ ] **Step 4: Implement**

`src/val3/common.sql`, first lines:

```sql
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS vector;
```

Create `src/val3/embedding.sql`:

```sql
-- Vectors of entries, one per embedding space: model, model digest and version of the embedded text.
-- Vectors of different spaces are never compared. Run with search_path set to the vault and public.
CREATE TABLE embedding (
    entry_id bigint NOT NULL REFERENCES entry (id),
    model text NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
    digest text NOT NULL CHECK (length(digest) BETWEEN 1 AND 200),
    text_version smallint NOT NULL CHECK (text_version > 0),
    dims integer NOT NULL CHECK (dims > 0),
    vector vector NOT NULL CHECK (vector_dims(vector) = dims),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (entry_id, model, digest, text_version)
);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON embedding
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON embedding
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
```

`src/val3/db.py` — replace `create_vault` and add `upgrade_vault`:

```python
def _run_file(conn, name: str) -> None:
    conn.execute(files("val3").joinpath(name).read_text(encoding="utf-8"))


def _vault_path(conn, name: str) -> None:
    """Vault DDL runs with the vault first and public (where the extensions live) after it."""
    conn.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(name)))


def create_vault(conn, name: str) -> None:
    check_vault_name(name)
    with conn.transaction():
        if _exists(conn, name):
            raise Val3Error(f"vault {name} already exists")
        _run_file(conn, "common.sql")
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        _vault_path(conn, name)
        _run_file(conn, "vault.sql")
        _run_file(conn, "embedding.sql")


def upgrade_vault(conn, name: str) -> bool:
    """Bring a vault created by an earlier version to the current schema; False when nothing changed.

    Roles keep on new tables the SELECT and INSERT privileges they hold on `entry`."""
    check_vault_name(name)
    with conn.transaction():
        if not _exists(conn, name):
            raise Val3Error(f"no vault {name}")
        _run_file(conn, "common.sql")
        if conn.execute("SELECT to_regclass(%s)", (f"{name}.embedding",)).fetchone()[0] is not None:
            return False
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
    return True
```

`src/val3/cli.py` — import `upgrade_vault` from `val3.db`; in `build_parser`, after the `grant` sub-parser:

```python
    upgrade = vault_sub.add_parser("upgrade", help="bring a vault to the current schema (adds the embedding table)")
    upgrade.add_argument("name")
    upgrade.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    upgrade.set_defaults(func=cmd_vault_upgrade)
```

and next to `cmd_vault_grant`:

```python
def cmd_vault_upgrade(args) -> None:
    check_vault_name(args.name)
    with connect(_service(args)) as conn:
        changed = upgrade_vault(conn, args.name)
    print(f"upgraded vault {args.name}" if changed else f"vault {args.name} is up to date")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (the whole suite runs on the new image).

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_vault.py src/val3/common.sql src/val3/embedding.sql src/val3/db.py src/val3/cli.py
git commit -m "feat: embedding table in vaults and val3 vault upgrade"
```

---

### Task 2: Embedding settings, client and vectors at admission (`val3 embed`)

**Files:**
- Create: `src/val3/embedding.py`, `tests/fake_embedder.py`, `tests/test_embed.py`
- Modify: `src/val3/config.py`, `src/val3/writer.py`, `src/val3/extract.py`, `src/val3/cli.py`, `tests/conftest.py`, `tests/helpers.py`

**Interfaces:**
- Consumes: Task 1 table `embedding`; plan 1/2 `load_project`, `submit`, `decide`, `add_tool`, `extract`.
- Produces: `config.Embedding(url: str, model: str, floor: float)`; `Project.embedding: Embedding | None` (last field, default `None`); `embedding.EmbeddingError(Val3Error)`; `embedding.Space(model: str, digest: str, dims: int, text_version: int = TEXT_VERSION)`; `embedding.digest(settings: Embedding, timeout: float) -> str`; `embedding.embed(settings: Embedding, texts: list[str], timeout: float) -> tuple[Space, list[list[float]]]`; `embedding.entry_text(title: str, statement: str) -> str`; `embedding.vector_literal(vector: list[float]) -> str`; `embedding.no_table(vault: str) -> EmbeddingError`; `embedding.KEEP_ALIVE = "24h"`; `embedding.TEXT_VERSION = 1`; `writer.store_vectors(conn, project, timeout: float = 60.0, limit: int | None = None) -> int`; `writer.VECTOR_BATCH = 16`; `cli.ADMISSION_TIMEOUT = 10.0`; CLI `val3 embed` printing `embedded N entries`. Tests: fixture `embedder` (`FakeEmbedder` with `url`, `inputs`, `requests`, `delay`, `status`, `zero`, `digest`), `Env.config(..., embedding=None)`, `helpers.embedding_vault(env, url, **settings)`.

- [ ] **Step 1: Write the fake service and the test wiring**

Create `tests/fake_embedder.py`:

```python
"""Stand-in for the embedding service in tests: Ollama's POST /api/embed with bag-of-words vectors.

Each word (lowercase, \\w+) adds 1 to one of DIMENSIONS buckets chosen by its SHA-256, so texts that
share words score a high cosine and texts that share none score about 0. A text without words maps
to the first axis, never to a zero vector.
"""
import hashlib
import json
import math
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIMENSIONS = 256


def vector(text: str) -> list[float]:
    values = [0.0] * DIMENSIONS
    for word in re.findall(r"\w+", text.lower()):
        values[int(hashlib.sha256(word.encode()).hexdigest()[:8], 16) % DIMENSIONS] += 1
    if not any(values):
        values[0] = 1.0
    norm = math.sqrt(sum(x * x for x in values))
    return [x / norm for x in values]


class FakeEmbedder:
    def __init__(self):
        self.requests = []  # request bodies, in order
        self.inputs = []    # every text received, in order
        self.delay = 0.0    # seconds to wait before answering
        self.status = 200   # answer with this HTTP status when not 200
        self.zero = False   # answer with zero vectors
        self.digest = "a" * 64  # digest of bge-m3 listed by GET /api/tags
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # /api/tags
                self._json({"models": [{"name": "bge-m3:latest", "model": "bge-m3:latest", "digest": owner.digest}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
                owner.requests.append(body)
                owner.inputs.extend(texts)
                time.sleep(owner.delay)
                if owner.status != 200:
                    self.send_response(owner.status)
                    self.end_headers()
                    return
                vectors = [[0.0] * DIMENSIONS if owner.zero else vector(text) for text in texts]
                self._json({"model": body["model"], "embeddings": vectors})

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
```

In `tests/conftest.py`: add `from fake_embedder import FakeEmbedder` after the `pytest` import; add the fixture at the end:

```python
@pytest.fixture
def embedder():
    fake = FakeEmbedder()
    yield fake
    fake.close()
```

and replace `Env.config` with:

```python
    def config(self, home=None, linked=(), service="val3test", embedding=None):
        linked_list = ", ".join(f'"{name}"' for name in linked)
        text = f'service = "{service}"\n[vaults]\nhome = "{home or self.vault}"\nlinked = [{linked_list}]\n'
        if embedding is not None:
            text += "[embedding]\n" + "".join(f"{key} = {json.dumps(value)}\n" for key, value in embedding.items())
        (self.project / "val3.toml").write_text(text)
```

In `tests/helpers.py`, after `make_vault`:

```python
def embedding_vault(env, url, **settings):
    """A vault whose project has semantic search on, served by `url`."""
    make_vault(env)
    env.config(embedding={"url": url, **settings})
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_embed.py`:

```python
from helpers import OUTPUT, embedding_vault, fact, make_vault, recipe, save

DOWN = "http://127.0.0.1:1"
TEXT = "April has 42 invoices\nThe ERP holds 42 invoices dated April 2026."
ENTRY = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
         "VALUES ('fact', 't', 's', 'h1', 'p', 'm')")
MANY = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
        "SELECT 'fact', 'note ' || n, 'statement ' || n, 'h' || n, 'p', 'm' FROM generate_series(1, %s) n")


def vectors(env):
    return env.sql(f"SELECT entry_id, model, vector_dims(vector) FROM {env.vault}.embedding ORDER BY entry_id")


def test_save_stores_a_vector_of_title_and_statement(env, embedder):
    embedding_vault(env, embedder.url)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert vectors(env) == [(1, "bge-m3", 256)]
    assert embedder.inputs == [TEXT]
    assert embedder.requests[0]["model"] == "bge-m3"
    assert embedder.requests[0]["keep_alive"] == "24h"


def test_save_with_the_service_down_admits_and_warns(env):
    embedding_vault(env, DOWN)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert "warning: no vector stored" in result.stderr and "val3 embed" in result.stderr
    assert vectors(env) == []


def test_embed_fills_the_gaps(env, embedder):
    embedding_vault(env, DOWN)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    env.config(embedding={"url": embedder.url})
    first = env.run("embed")
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "embedded 1 entries"
    assert env.run("embed").stdout.strip() == "embedded 0 entries"
    assert vectors(env) == [(1, "bge-m3", 256)]


def test_admission_embeds_one_batch_newest_first(env, embedder):
    embedding_vault(env, embedder.url)
    env.sql(MANY.format(v=env.vault), (20,))
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.stdout.strip() == f"admitted {env.vault}#21"
    assert [row[0] for row in vectors(env)] == list(range(6, 22))
    assert env.run("embed").stdout.strip() == "embedded 5 entries"


def test_a_new_model_build_gets_its_own_vectors(env, embedder):
    embedding_vault(env, embedder.url)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    embedder.digest = "b" * 64
    assert env.run("embed").stdout.strip() == "embedded 1 entries"
    assert env.sql(f"SELECT digest, text_version, dims FROM {env.vault}.embedding ORDER BY created_at") == [
        ("a" * 64, 1, 256), ("b" * 64, 1, 256)]


def test_a_model_the_service_lacks_admits_and_warns(env, embedder):
    embedding_vault(env, embedder.url, model="other-model")
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0
    assert "model other-model is not available" in result.stderr
    assert vectors(env) == []


def test_a_vault_error_while_storing_vectors_keeps_the_admission(env, embedder):
    embedding_vault(env, embedder.url)
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    assert env.run("vault", "grant", env.vault, env.writer, "--write", "--service", "val3test").returncode == 0
    env.sql(f"REVOKE INSERT ON {env.vault}.embedding FROM {env.writer}")
    env.config(service="val3writer", embedding={"url": embedder.url})
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert "warning: no vector stored" in result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]
    assert vectors(env) == []


def test_extraction_with_the_service_down_keeps_the_admission(env):
    embedding_vault(env, DOWN)
    t = env.transcript(tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": fact()}])
    result = env.run("extract", str(t))
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]
    assert vectors(env) == []


def test_embed_needs_the_embedding_table(env):
    make_vault(env)
    result = env.run("embed")
    assert result.returncode == 1
    assert "no [embedding] table" in result.stderr


def test_embed_with_the_service_down_fails(env):
    embedding_vault(env, DOWN)
    env.sql(ENTRY.format(v=env.vault))
    result = env.run("embed")
    assert result.returncode == 1
    assert "embedding service" in result.stderr


def test_unusable_answers_store_nothing(env, embedder):
    embedding_vault(env, embedder.url)
    embedder.zero = True
    first = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert first.returncode == 0
    assert "unexpected answer" in first.stderr
    embedder.zero, embedder.status = False, 500
    second = env.run("embed")
    assert second.returncode == 1
    assert "HTTP 500" in second.stderr
    assert vectors(env) == []


def test_tool_add_and_review_approval_store_vectors(env, embedder):
    embedding_vault(env, embedder.url)
    assert env.run("tool", "add", "code-graph", "--type", "cli",
                   "--description", "Answers which module overrides a method.").returncode == 0
    t = env.transcript(tool_outputs=[OUTPUT])
    save(env, fact(), t)
    save(env, recipe(f"{env.vault}#2"), t)
    assert env.run("review", "approve", "1").returncode == 0
    assert [row[0] for row in vectors(env)] == [1, 2, 3]


def test_extraction_stores_vectors(env, embedder):
    embedding_vault(env, embedder.url)
    t = env.transcript(tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": fact()}])
    result = env.run("extract", str(t))
    assert result.returncode == 0, result.stderr
    assert vectors(env) == [(1, "bge-m3", 256)]


def test_vault_without_the_embedding_table_warns_on_save(env, embedder):
    embedding_vault(env, embedder.url)
    env.sql(f"DROP TABLE {env.vault}.embedding")
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0
    assert f"val3 vault upgrade {env.vault}" in result.stderr


def test_embedding_settings_are_checked(env):
    make_vault(env)
    for line, message in (("dims = 3", "may only hold"), ("floor = 1.5", "floor must be"),
                          ('url = "ftp://x"', "url must be"), ('model = ""', "model must be")):
        (env.project / "val3.toml").write_text(
            f'service = "val3test"\n[vaults]\nhome = "{env.vault}"\n[embedding]\n{line}\n')
        result = env.run("recall", "invoices")
        assert result.returncode == 1 and message in result.stderr, line
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_embed.py -q`
Expected: FAIL — `unknown keys: embedding` from `load_project`, and `invalid choice: 'embed'`.

- [ ] **Step 4: Implement the settings**

`src/val3/config.py` — after `DEFAULT_EXTRACTOR_MODEL`:

```python
DEFAULT_EMBEDDING_URL = "http://127.0.0.1:11434"
DEFAULT_EMBEDDING_MODEL = "bge-m3"
DEFAULT_FLOOR = 0.60  # measured with bge-m3 on the first adopted project (design 3.3)


@dataclass(frozen=True)
class Embedding:
    url: str
    model: str
    floor: float
```

Add the last field to `Project`: `embedding: Embedding | None = None`. In `load_project`, the allowed keys become `{"service", "project", "vaults", "extractor", "embedding"}`, and before the `return`:

```python
    embedding = data.get("embedding")
    if embedding is not None:
        if not isinstance(embedding, dict) or set(embedding) - {"url", "model", "floor"}:
            raise Val3Error(f"{path}: [embedding] may only hold 'url', 'model' and 'floor'")
        url = embedding.get("url", DEFAULT_EMBEDDING_URL)
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise Val3Error(f"{path}: [embedding] url must be an http:// or https:// URL")
        embedding_model = embedding.get("model", DEFAULT_EMBEDDING_MODEL)
        if not isinstance(embedding_model, str) or not embedding_model.strip():
            raise Val3Error(f"{path}: [embedding] model must be a model name")
        floor = embedding.get("floor", DEFAULT_FLOOR)
        if isinstance(floor, bool) or not isinstance(floor, (int, float)) or not 0 < floor < 1:
            raise Val3Error(f"{path}: [embedding] floor must be a number between 0 and 1")
        embedding = Embedding(url.rstrip("/"), embedding_model, float(floor))
    return Project(path.parent, name, service, home, linked, model, embedding)
```

- [ ] **Step 5: Implement the client**

Create `src/val3/embedding.py`:

```python
"""Client of the local embedding service (Ollama's `GET /api/tags` and `POST /api/embed`)."""
import http.client
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from val3 import Val3Error
from val3.config import Embedding

KEEP_ALIVE = "24h"  # the service keeps the model loaded: a cold load takes seconds
TEXT_VERSION = 1    # version of entry_text(); bump it when that text changes, so old vectors are not compared
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local service: no proxy


class EmbeddingError(Val3Error):
    """The embedding service gave no usable vectors, or a vault cannot hold them."""


@dataclass(frozen=True)
class Space:
    """What makes two vectors comparable: the same model build, size and embedded text."""
    model: str
    digest: str
    dims: int
    text_version: int = TEXT_VERSION


def entry_text(title: str, statement: str) -> str:
    return f"{title}\n{statement}"


def digest(settings: Embedding, timeout: float) -> str:
    """Digest of the configured model as the service lists it; a model name alone does not fix the vectors."""
    return _digest(settings, time.monotonic() + timeout)


def embed(settings: Embedding, texts: list[str], timeout: float) -> tuple[Space, list[list[float]]]:
    """Vectors of `texts` and their space; `timeout` bounds both requests together."""
    deadline = time.monotonic() + timeout
    model_digest = _digest(settings, deadline)
    data = _call(settings, "/api/embed", {"model": settings.model, "input": texts, "keep_alive": KEEP_ALIVE},
                 deadline)
    vectors = data.get("embeddings") if isinstance(data, dict) else None
    if (not isinstance(vectors, list) or not vectors or len(vectors) != len(texts)
            or not all(map(_usable, vectors)) or len({len(vector) for vector in vectors}) != 1):
        raise EmbeddingError(f"embedding service {settings.url}: unexpected answer")
    return Space(settings.model, model_digest, len(vectors[0])), vectors


def _digest(settings: Embedding, deadline: float) -> str:
    data = _call(settings, "/api/tags", None, deadline)
    models = data.get("models") if isinstance(data, dict) else None
    names = (settings.model, f"{settings.model}:latest")
    for model in models if isinstance(models, list) else []:
        if isinstance(model, dict) and model.get("name") in names and isinstance(model.get("digest"), str) \
                and model["digest"]:
            return model["digest"]
    raise EmbeddingError(f"embedding service {settings.url}: model {settings.model} is not available")


def _call(settings: Embedding, path: str, body: dict | None, deadline: float) -> object:
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise EmbeddingError(f"embedding service {settings.url}: timed out")
    request = urllib.request.Request(f"{settings.url}{path}",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise EmbeddingError(f"embedding service {settings.url}: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise EmbeddingError(f"embedding service {settings.url}: {exc.reason}") from None
    except (OSError, http.client.HTTPException, ValueError) as exc:  # timeouts, resets, bad JSON
        raise EmbeddingError(f"embedding service {settings.url}: {str(exc) or type(exc).__name__}") from None


def _usable(vector: object) -> bool:
    """Finite numbers, not all zero: a zero vector scores NaN, which PostgreSQL ranks above every floor."""
    return (isinstance(vector, list) and len(vector) > 0
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in vector)
            and any(x != 0 for x in vector))


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def no_table(vault: str) -> EmbeddingError:
    return EmbeddingError(f"vault {vault} has no embedding table: run `val3 vault upgrade {vault}`")
```

- [ ] **Step 6: Implement storage and its callers**

`src/val3/writer.py` — imports become `from val3.config import CONFIG_FILE, Project, parse_ref` and `from val3.embedding import TEXT_VERSION, EmbeddingError, digest, embed, entry_text, no_table, vector_literal`; add at the end:

```python
VECTOR_BATCH = 16
VECTOR_TIMEOUT = 60.0


def store_vectors(conn, project: Project, timeout: float = VECTOR_TIMEOUT, limit: int | None = None) -> int:
    """Embed live entries of the home vault that have no vector in the service's current space, newest first.

    `limit` bounds the work right after an admission; `val3 embed` and the drainer pass none.
    Runs outside any transaction: each vector commits alone and an admission is never undone.
    Returns how many vectors were stored; raises EmbeddingError when the service or the vault fails."""
    settings = project.embedding
    if settings is None:
        raise Val3Error(f"no [embedding] table in {project.root / CONFIG_FILE}")
    vault = project.home
    stored = 0
    try:
        current = digest(settings, timeout)
        rows = conn.execute(vsql(vault, """
            SELECT e.id, e.title, e.statement FROM {v}.entry e
            WHERE NOT EXISTS (SELECT 1 FROM {v}.embedding m WHERE m.entry_id = e.id AND m.model = %s
                                AND m.digest = %s AND m.text_version = %s)
              AND NOT EXISTS (SELECT 1 FROM {v}.supersession s WHERE s.old_id = e.id)
            ORDER BY e.id DESC LIMIT %s"""), (settings.model, current, TEXT_VERSION, limit)).fetchall()
        for start in range(0, len(rows), VECTOR_BATCH):
            batch = rows[start:start + VECTOR_BATCH]
            space, vectors = embed(settings, [entry_text(title, statement) for _, title, statement in batch],
                                   timeout)
            for (entry_id, _, _), vector in zip(batch, vectors):
                stored += conn.execute(vsql(vault, """
                    INSERT INTO {v}.embedding (entry_id, model, digest, text_version, dims, vector)
                    VALUES (%s, %s, %s, %s, %s, %s::vector) ON CONFLICT DO NOTHING"""),
                    (entry_id, space.model, space.digest, space.text_version, space.dims,
                     vector_literal(vector))).rowcount
    except psycopg.errors.UndefinedTable:
        raise no_table(vault) from None
    except psycopg.OperationalError:
        raise
    except psycopg.Error as exc:  # e.g. no INSERT privilege on the table: the entry stays without a vector
        raise EmbeddingError(f"vault {vault}: {str(exc).strip().splitlines()[0]}") from None
    return stored
```

`src/val3/extract.py` — import `from val3.embedding import EmbeddingError` and `from val3.writer import store_vectors, submit`; in `extract`, replace the final `return counts` with:

```python
    if project.embedding is not None:
        try:
            counts["embedded"] = store_vectors(conn, project)
        except EmbeddingError as exc:  # entries stay without a vector; `val3 embed` retries
            counts["embedding_error"] = str(exc)
    return counts
```

`src/val3/cli.py` — import `from val3.embedding import EmbeddingError` and add `VECTOR_BATCH` and `store_vectors` to the `val3.writer` import. Add the sub-parser after `recall`:

```python
    embed_parser = sub.add_parser("embed", help="store vectors for home-vault entries that have none")
    embed_parser.set_defaults(func=cmd_embed)
```

Add the helpers and command:

```python
ADMISSION_TIMEOUT = 10.0  # seconds; a cold load of the model takes about 2.5 s


def _store_vectors(conn, project) -> None:
    """After an admission: embed one batch of what lacks a vector, newest first, so the admitted entry is in it.

    A failure only warns; `val3 embed` and the drainer store the rest."""
    if project.embedding is None:
        return
    try:
        store_vectors(conn, project, ADMISSION_TIMEOUT, limit=VECTOR_BATCH)
    except EmbeddingError as exc:
        print(f"val3: warning: no vector stored ({exc}); run `val3 embed` later", file=sys.stderr)


def cmd_embed(args) -> None:
    project = load_project(Path.cwd())
    with connect(project.service) as conn:
        print(f"embedded {store_vectors(conn, project)} entries")
```

Call `_store_vectors(conn, project)` inside the `with connect(...)` block of `cmd_save` (after the `print(submit(...))`), of `cmd_tool_add` (after its `print`), and of `cmd_review` after `print(decide(...))` when `approve` is true.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/val3/config.py src/val3/embedding.py src/val3/writer.py src/val3/extract.py src/val3/cli.py \
        tests/fake_embedder.py tests/conftest.py tests/helpers.py tests/test_embed.py
git commit -m "feat: embedding settings, vectors at admission and val3 embed"
```

---

### Task 3: Semantic search and `val3 recall`

**Files:**
- Modify: `src/val3/search.py`, `src/val3/cli.py`, `tests/test_recall.py`

**Interfaces:**
- Consumes: Task 2 `embed`, `Space`, `EmbeddingError`, `vector_literal`, `no_table`, `Project.embedding`; plan 1 `search`, `Hit`.
- Produces: in `val3.search`: `PROMPT_CAP = 3`, `EMBED_TIMEOUT = 1.0`, `RECALL_TIMEOUT = 10.0`, `SHORT_PROMPT = 6`, `PREVIOUS_CHARS = 500`, `QUERY_CHARS = 1_000`; `Hit.method: str = "full-text"` (last field; `"semantic"` for semantic hits, whose `rank` is the cosine similarity); `search(conn, vaults, text, limit=10, space: Space | None = None, as_of: datetime | None = None)` (with `space`, only entries without a vector in it; with `as_of`, the vault as it was then); `previous_part(prompt: str, previous: str) -> str`; `query_text(prompt: str, previous: str = "") -> str`; `semantic(conn, vaults, space: Space, vector: list[float], floor: float, as_of: datetime | None = None) -> list[Hit]` (unordered; raises `EmbeddingError` for a missing table or any database error but a lost connection); `candidates(conn, vaults, space, vector, text: str, floor: float, limit: int, as_of=None) -> list[Hit]`; `select(hits: list[Hit], floor: float, limit: int) -> list[Hit]`; `Found(hits: list[Hit], method: str, warning: str | None = None)`; `find(conn, project, prompt: str, limit: int, previous: str = "", timeout: float = EMBED_TIMEOUT) -> Found`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recall.py` (and add `embedding_vault` to its `helpers` import):

```python
MAILBOX = {"title": "Sample mailbox address", "statement": "Samples arrive at the shared sample mailbox."}
DOWN = "http://127.0.0.1:1"


def test_recall_is_semantic_with_an_embedding_table(env, embedder):
    embedding_vault(env, embedder.url)
    ref = saved(env, **MAILBOX)
    result = env.run("recall", "sample", "mailbox", "address")
    assert result.returncode == 0, result.stderr
    assert f"{ref}  [fact]  Sample mailbox address" in result.stdout
    assert embedder.inputs[-1] == "sample mailbox address"
    assert env.run("recall", "penguin", "colony").stdout.strip() == "no matches"


def test_recall_applies_the_floor(env, embedder):
    embedding_vault(env, embedder.url, floor=0.9)
    saved(env, **MAILBOX)
    assert env.run("recall", "sample", "mailbox", "address").stdout.strip() == "no matches"


def test_recall_lists_entries_without_a_vector_by_full_text(env, embedder):
    embedding_vault(env, DOWN)
    ref = saved(env, **MAILBOX)
    env.config(embedding={"url": embedder.url})
    result = env.run("recall", "sample", "mailbox", "address")
    assert ref in result.stdout
    assert result.stderr == ""


def test_recall_falls_back_to_full_text_when_the_service_is_down(env):
    embedding_vault(env, DOWN)
    ref = saved(env, **MAILBOX)
    result = env.run("recall", "sample", "mailbox", "address")
    assert result.returncode == 0
    assert ref in result.stdout
    assert "semantic search is unavailable" in result.stderr


def test_recall_on_a_vault_without_the_embedding_table(env, embedder):
    embedding_vault(env, embedder.url)
    ref = saved(env, **MAILBOX)
    env.sql(f"DROP TABLE {env.vault}.embedding")
    result = env.run("recall", "sample", "mailbox", "address")
    assert result.returncode == 0
    assert ref in result.stdout
    assert f"val3 vault upgrade {env.vault}" in result.stderr


def test_entries_without_a_vector_are_filtered_before_the_limit(env, embedder):
    embedding_vault(env, embedder.url, floor=0.99)
    saved(env, **MAILBOX)  # has a vector, and a better full-text rank than the entry below
    env.sql(f"INSERT INTO {env.vault}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
            "VALUES ('fact', 'Sample mailbox', 'Kept for audits.', 'h-plain', 'p', 'm')")
    result = env.run("recall", "--limit", "1", "sample", "mailbox")
    assert f"{env.vault}#2  [fact]  Sample mailbox" in result.stdout


def test_superseded_entries_are_not_recalled_semantically(env, embedder):
    embedding_vault(env, embedder.url)
    saved(env, **MAILBOX)
    newer = saved(env, title="Sample mailbox address moved",
                  statement="Samples now arrive at the accounting sample mailbox.", supersedes=f"{env.vault}#1")
    output = env.run("recall", "sample", "mailbox", "address").stdout
    assert newer in output
    assert f"{env.vault}#1 " not in output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_recall.py -q`
Expected: FAIL — recall still runs full-text only: the query never reaches the embedding service, the floor test lists the entry, the fallback warning is missing and `--limit 1` lists the entry that has a vector.

- [ ] **Step 3: Implement**

`src/val3/search.py` — module docstring becomes `"""Search over a project's vaults: full-text, and semantic when the project has an [embedding] table."""`; imports add `from datetime import datetime`, `import psycopg` and `from val3.embedding import EmbeddingError, Space, embed, no_table, vector_literal`.

Full-text search learns two filters. In `_QUERY`, the `live` CTE becomes

```sql
WITH live AS (
    SELECT e.* FROM {v}.entry e
    WHERE (%(as_of)s::timestamptz IS NULL OR e.created_at <= %(as_of)s)
      AND NOT EXISTS (SELECT 1 FROM {v}.supersession s WHERE s.old_id = e.id
                      AND (%(as_of)s::timestamptz IS NULL OR s.created_at <= %(as_of)s))
),
```

and the `WHERE` of the `scored` CTE becomes `WHERE (e.search_en @@ val3_common.or_query(l.en) OR e.search_es @@ val3_common.or_query(l.es))/*unembedded*/`. Then:

```python
_UNEMBEDDED = """
      AND NOT EXISTS (SELECT 1 FROM {v}.embedding m WHERE m.entry_id = e.id AND m.model = %(model)s
                        AND m.digest = %(digest)s AND m.text_version = %(text_version)s)"""
```

and `search` becomes:

```python
def search(conn, vaults: tuple[str, ...], text: str, limit: int = 10, space: Space | None = None,
           as_of: datetime | None = None) -> list[Hit]:
    """Full-text hits, tools first. With `space`, only entries without a vector in it (filtered before
    the limit); with `as_of`, entries and supersessions recorded up to that moment."""
    query = _QUERY.replace("/*unembedded*/", _UNEMBEDDED if space else "")
    params = {"text": text, "stop": STOP_WORDS, "flat": FLAT_BELOW, "limit": limit, "as_of": as_of}
    if space:
        params |= {"model": space.model, "digest": space.digest, "text_version": space.text_version}
    hits = [Hit(vault, *row) for vault in vaults for row in conn.execute(vsql(vault, query), params)]
    hits.sort(key=lambda hit: (hit.kind != "tool", -hit.rank))
    return hits[:limit]
```

After `FLAT_BELOW`:

```python
PROMPT_CAP = 3          # entries injected per typed prompt
EMBED_TIMEOUT = 1.0     # seconds the prompt hook waits for the embedding service
RECALL_TIMEOUT = 10.0
SHORT_PROMPT = 6        # words; a shorter prompt is read with the end of the previous assistant turn
PREVIOUS_CHARS = 500
QUERY_CHARS = 1_000     # embedding time grows with length; long prompts are clipped
```

In `Hit`, comment the field: `rank: float  # full-text weight, or cosine similarity for semantic hits`, and add the last field `method: str = "full-text"  # or "semantic"`. Append:

```python
_SEMANTIC = """
WITH m AS MATERIALIZED (  -- only vectors of the query's space reach the distance operator
    SELECT entry_id, vector FROM {v}.embedding
    WHERE model = %(model)s AND digest = %(digest)s AND text_version = %(text_version)s AND dims = %(dims)s
), scored AS (
    SELECT e.id, e.kind, e.title, e.statement, 1 - (m.vector <=> %(vector)s::vector) AS score
    FROM {v}.entry e JOIN m ON m.entry_id = e.id
    WHERE (%(as_of)s::timestamptz IS NULL OR e.created_at <= %(as_of)s)
      AND NOT EXISTS (SELECT 1 FROM {v}.supersession s WHERE s.old_id = e.id
                      AND (%(as_of)s::timestamptz IS NULL OR s.created_at <= %(as_of)s))
)
SELECT id, kind, title, statement, score FROM scored
WHERE score >= %(floor)s
"""


def semantic(conn, vaults: tuple[str, ...], space: Space, vector: list[float], floor: float,
             as_of: datetime | None = None) -> list[Hit]:
    """Live entries whose vector in `space` scores at least `floor` against `vector`, in no order.

    No LIMIT: the cut happens once, in select(), after tools are put first."""
    params = {"model": space.model, "digest": space.digest, "text_version": space.text_version,
              "dims": space.dims, "vector": vector_literal(vector), "floor": floor, "as_of": as_of}
    hits = []
    for vault in vaults:
        try:
            rows = conn.execute(vsql(vault, _SEMANTIC), params).fetchall()
        except psycopg.errors.UndefinedTable:
            raise no_table(vault) from None
        except psycopg.OperationalError:
            raise
        except psycopg.Error as exc:  # e.g. no SELECT privilege; autocommit keeps the connection usable
            raise EmbeddingError(f"vault {vault}: {str(exc).strip().splitlines()[0]}") from None
        hits += [Hit(vault, *row, method="semantic") for row in rows]
    return hits


def candidates(conn, vaults: tuple[str, ...], space: Space, vector: list[float], text: str, floor: float,
               limit: int, as_of: datetime | None = None) -> list[Hit]:
    """Semantic hits at or above `floor`, plus full-text hits among entries without a vector in `space`
    (admitted while the service was down, or before a new model build)."""
    hits = semantic(conn, vaults, space, vector, floor, as_of)
    try:
        return hits + search(conn, vaults, text, limit, space=space, as_of=as_of)
    except psycopg.OperationalError:
        raise
    except psycopg.Error as exc:
        raise EmbeddingError(str(exc).strip().splitlines()[0]) from None


def select(hits: list[Hit], floor: float, limit: int) -> list[Hit]:
    """The one rule of delivery, recall and `val3 eval`: semantic hits at or above `floor` and full-text
    hits; tools first, then semantic by score, then full-text by weight; ties by the newest entry."""
    kept = [hit for hit in hits if hit.method != "semantic" or hit.rank >= floor]
    kept.sort(key=lambda hit: (hit.kind != "tool", hit.method != "semantic", -hit.rank, -hit.id))
    return kept[:limit]


def previous_part(prompt: str, previous: str) -> str:
    """The end of the previous assistant turn read together with a short prompt; "" for longer ones."""
    return previous[-PREVIOUS_CHARS:] if previous and len(prompt.split()) <= SHORT_PROMPT else ""


def query_text(prompt: str, previous: str = "") -> str:
    part = previous_part(prompt, previous)
    return (f"{prompt}\n{part}" if part else prompt)[:QUERY_CHARS]


@dataclass(frozen=True)
class Found:
    hits: list[Hit]
    method: str                # semantic | full-text
    warning: str | None = None


def find(conn, project, prompt: str, limit: int, previous: str = "", timeout: float = EMBED_TIMEOUT) -> Found:
    """Semantic search when the project has an [embedding] table, else full-text.

    Any failure of the semantic path falls back to full-text over the prompt, with a warning."""
    settings = project.embedding
    if settings is None:
        return Found(search(conn, project.vaults, prompt, limit), "full-text")
    try:
        space, (vector,) = embed(settings, [query_text(prompt, previous)], timeout)
        hits = candidates(conn, project.vaults, space, vector, prompt, settings.floor, limit)
    except EmbeddingError as exc:
        return Found(search(conn, project.vaults, prompt, limit), "full-text",
                     f"semantic search is unavailable ({exc}); entries were found by full-text search")
    return Found(select(hits, settings.floor, limit), "semantic")
```

`src/val3/cli.py` — replace `from val3.search import search` with `from val3.search import RECALL_TIMEOUT, find`, and `cmd_recall` with:

```python
def cmd_recall(args) -> None:
    project = load_project(Path.cwd())
    with connect(project.service) as conn:
        found = find(conn, project, " ".join(args.terms), args.limit, timeout=RECALL_TIMEOUT)
    if found.warning:
        print(f"val3: warning: {found.warning}", file=sys.stderr)
    if not found.hits:
        print("no matches")
    for hit in found.hits:
        print(f"{hit.ref}  [{hit.kind}]  {hit.title}")
        print("    " + " ".join(hit.statement.split())[:200])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass, including the full-text recall tests (projects without `[embedding]`).

- [ ] **Step 5: Commit**

```bash
git add src/val3/search.py src/val3/cli.py tests/test_recall.py
git commit -m "feat: semantic search with a floor; recall falls back to full-text"
```

---

### Task 4: Delivery — session index, prompt hook, prompt log

**Files:**
- Modify: `src/val3/views.py`, `src/val3/transcript.py`, `src/val3/hooks.py`, `tests/test_hooks.py`

**Interfaces:**
- Consumes: Task 3 `find`, `Found`, `PROMPT_CAP`, `previous_part`; Task 2 `embed`, `EmbeddingError`, `Project.embedding`; plan 1 `session_index`, `prompt_context`, `_warn_once`, `state_dir`.
- Produces: `transcript.last_assistant_text(path: Path, tail: int = 262_144) -> str` (never raises); `hooks.WARM_UP_TIMEOUT = 5.0`, `hooks.LOG_DAYS = 30`; `hooks._warn_once(data, message, key="")`; prompt log file per decision 9.

- [ ] **Step 1: Write the failing tests**

In `tests/test_hooks.py`: imports become

```python
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone

from helpers import OUTPUT, embedding_vault, fact, make_vault, recipe, save
```

Replace the body of `test_session_start_injects_the_index` after `text = context(start(env), "SessionStart")` with:

```python
    assert "## Tools" in text and f"- {env.vault}#3 [tool] code-graph" in text
    assert "## billing" in text and f"- {env.vault}#1 [fact] April has 42 invoices" in text
    assert "## reporting" in text and f"- {env.vault}#2 [preference] Reports in Spanish" in text
    assert text.index("## Tools") < text.index("## billing") < text.index("## reporting")
```

Delete `test_prompt_from_the_harness_injects_at_most_three_entries`. Add:

```python
DOWN = "http://127.0.0.1:1"
MAILBOX = fact(title="Sample mailbox address", statement="Samples arrive at the shared sample mailbox.", keywords=[])
ASKED = "Shall I read the sample mailbox address now?"
APRIL = "How many invoices are there for April?"


def test_session_start_on_an_empty_vault(env):
    make_vault(env)
    assert "No entries yet." in context(start(env), "SessionStart")


def test_session_start_warms_up_the_embedding_model(env, embedder):
    embedding_vault(env, embedder.url)
    assert context(start(env), "SessionStart").startswith("# VAL3 memory index")
    assert embedder.inputs == ["warm-up"]


def test_session_start_with_the_embedding_service_down_still_gives_the_index(env):
    embedding_vault(env, DOWN)
    assert context(start(env), "SessionStart").startswith("# VAL3 memory index")


def notes(env, embedder, **settings):
    embedding_vault(env, embedder.url, **settings)
    env.sql(INSERT_MANY.format(v=env.vault), ("fact", "Invoice note ", "invoices ledger", 6))
    assert env.run("embed").stdout.strip() == "embedded 6 entries"


def test_prompt_injects_at_most_three_entries_above_the_floor(env, embedder):
    notes(env, embedder)
    text = context(ask(env, "invoice note invoices ledger"), "UserPromptSubmit")
    assert text.count(f"{env.vault}#") == 3


def test_prompt_below_the_floor_injects_nothing(env, embedder):
    notes(env, embedder, floor=0.9)
    assert context(ask(env, "invoice note invoices ledger"), "UserPromptSubmit") is None


def test_short_prompt_is_read_with_the_previous_assistant_turn(env, embedder):
    embedding_vault(env, embedder.url, floor=0.4)
    save(env, MAILBOX, env.transcript(tool_outputs=[OUTPUT], assistant_texts=[ASKED]))
    text = context(ask(env, "yes"), "UserPromptSubmit")
    assert f"{env.vault}#1 [fact] Sample mailbox address" in text
    assert embedder.inputs[-1] == f"yes\n{ASKED}"


def test_short_prompt_without_a_previous_turn_is_read_alone(env, embedder):
    embedding_vault(env, embedder.url, floor=0.4)
    save(env, MAILBOX, env.transcript(tool_outputs=[OUTPUT]))
    assert context(ask(env, "yes"), "UserPromptSubmit") is None
    assert embedder.inputs[-1] == "yes"


def test_long_prompt_is_read_alone(env, embedder):
    embedding_vault(env, embedder.url, floor=0.4)
    save(env, MAILBOX, env.transcript(tool_outputs=[OUTPUT], assistant_texts=[ASKED]))
    prompt = "Which address do the samples arrive at in the shared mailbox"
    assert f"{env.vault}#1" in context(ask(env, prompt), "UserPromptSubmit")
    assert embedder.inputs[-1] == prompt


def test_unreadable_transcript_is_ignored(env, embedder):
    embedding_vault(env, embedder.url)
    result = hook(env, "prompt", payload(env, "UserPromptSubmit", prompt="yes", transcript_path=str(env.root)))
    assert result.returncode == 0
    assert result.stderr == ""
    assert embedder.inputs[-1] == "yes"


def test_harness_turns_get_nothing(env, embedder):
    notes(env, embedder)
    calls = len(embedder.inputs)
    result = hook(env, "prompt", payload(env, "UserPromptSubmit", prompt="invoice note invoices ledger",
                                         source="system"))
    assert context(result, "UserPromptSubmit") is None
    assert len(embedder.inputs) == calls
    assert not (env.state / "prompts").exists()


def test_prompt_falls_back_to_full_text_and_warns_once(env):
    embedding_vault(env, DOWN)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    first = context(ask(env, APRIL), "UserPromptSubmit")
    assert f"{env.vault}#1 [fact] April has 42 invoices" in first
    assert "semantic search is unavailable" in first
    second = context(ask(env, APRIL), "UserPromptSubmit")
    assert f"{env.vault}#1" in second
    assert "semantic search is unavailable" not in second


def test_slow_embedding_service_does_not_hold_the_prompt(env, embedder):
    embedding_vault(env, embedder.url)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    embedder.delay = 4
    started = time.monotonic()
    text = context(ask(env, APRIL), "UserPromptSubmit")
    assert time.monotonic() - started < 3.5
    assert "semantic search is unavailable" in text and f"{env.vault}#1" in text


def test_entries_without_a_vector_reach_the_prompt_by_full_text(env, embedder):
    embedding_vault(env, DOWN)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    env.config(embedding={"url": embedder.url})
    text = context(ask(env, APRIL), "UserPromptSubmit")
    assert f"{env.vault}#1 [fact] April has 42 invoices" in text
    assert "semantic search is unavailable" not in text


def test_a_database_error_in_semantic_search_falls_back(env, embedder):
    embedding_vault(env, embedder.url)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    assert env.run("vault", "grant", env.vault, env.reader, "--read", "--service", "val3test").returncode == 0
    env.sql(f"REVOKE SELECT ON {env.vault}.embedding FROM {env.reader}")
    env.config(service="val3reader", embedding={"url": embedder.url})
    text = context(ask(env, APRIL), "UserPromptSubmit")
    assert f"{env.vault}#1" in text and "semantic search is unavailable" in text


def test_prompt_on_a_vault_without_the_embedding_table(env, embedder):
    embedding_vault(env, embedder.url)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    env.sql(f"DROP TABLE {env.vault}.embedding")
    text = context(ask(env, APRIL), "UserPromptSubmit")
    assert f"{env.vault}#1" in text and f"val3 vault upgrade {env.vault}" in text


def prompt_log(env):
    path = env.state / "prompts" / "project.jsonl"
    return path, [json.loads(line) for line in path.read_text().splitlines()]


def test_prompt_log_records_typed_prompts(env, embedder):
    notes(env, embedder)
    ask(env, "invoice note invoices ledger")
    path, records = prompt_log(env)
    assert len(records) == 1
    record = records[0]
    assert record["session"] == env.session and record["prompt"] == "invoice note invoices ledger"
    assert record["method"] == "semantic" and record["previous"] == ""
    assert len(record["injected"]) == 3
    assert all(0.6 <= item["score"] <= 1 for item in record["injected"])  # full-text hits log score null
    assert path.stat().st_mode & 0o777 == 0o600


def test_prompt_log_drops_lines_older_than_thirty_days(env, embedder):
    notes(env, embedder)
    path = env.state / "prompts" / "project.jsonl"
    path.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    path.write_text("".join(json.dumps({"at": (now - timedelta(days=days)).isoformat(timespec="seconds"),
                                        "prompt": f"{days} days"}) + "\n" for days in (40, 10)))
    ask(env, "invoice note invoices ledger")
    _, records = prompt_log(env)
    assert [record["prompt"] for record in records] == ["10 days", "invoice note invoices ledger"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_hooks.py -q`
Expected: FAIL — the index leaves out facts, the prompt hook ignores `[embedding]`, no prompt log.

- [ ] **Step 3: Implement the index**

`src/val3/views.py`, in `session_index`: the `WHERE` clause becomes

```sql
            WHERE NOT EXISTS (SELECT 1 FROM {v}.supersession s WHERE s.old_id = e.id)
```

(the kind filter is removed) and the empty message becomes `text += "\n\nNo entries yet."`.

- [ ] **Step 4: Implement the transcript reader**

`src/val3/transcript.py` — add `import os` and:

```python
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
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("type") == "assistant" and not record.get("isSidechain"):
            texts = [block["text"] for block in _blocks(_content(record))
                     if block.get("type") == "text" and isinstance(block.get("text"), str)]
            if texts:
                return "\n".join(texts)
    return ""
```

- [ ] **Step 5: Implement the hooks**

`src/val3/hooks.py` — imports:

```python
import hashlib
import json
import os
import re
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path

from val3 import pending
from val3.config import find_config, load_project, state_dir
from val3.db import connect
from val3.embedding import EmbeddingError, embed
from val3.search import PROMPT_CAP, find, previous_part
from val3.transcript import last_assistant_text
from val3.views import prompt_context, session_index
```

Constants after `LIMIT`:

```python
WARM_UP_TIMEOUT = 5.0  # seconds; a cold load of the embedding model takes about 2 s on CPU
LOG_DAYS = 30
```

`session_start` ends with:

```python
    project = load_project(cwd)
    with connect(project.service, timeout=3) as conn:
        context = session_index(conn, project, LIMIT)
    _warm_up(project)
    return context


def _warm_up(project) -> None:
    """Load the embedding model now, so the prompt hook (1 s) does not wait for a cold load."""
    if project.embedding is None:
        return
    try:
        embed(project.embedding, ["warm-up"], WARM_UP_TIMEOUT)
    except EmbeddingError:
        pass  # the prompt hook falls back and warns
```

Replace `prompt` and delete `_prompt_limit`:

```python
def prompt(data: dict) -> str | None:
    text = str(data.get("prompt") or "").strip()
    cwd = Path(data.get("cwd") or os.getcwd())
    # Harness turns (peer messages, task notifications, wakeups) get nothing: the index covers them.
    if not text or data.get("source") not in (None, "user", "sdk") or find_config(cwd) is None:
        return None
    project = load_project(cwd)
    transcript = data.get("transcript_path")
    previous = last_assistant_text(Path(transcript)) if project.embedding and transcript else ""
    with connect(project.service, timeout=3) as conn:
        found = find(conn, project, text, PROMPT_CAP, previous=previous)
        context = prompt_context(conn, found.hits, LIMIT)
    _log_prompt(project, data, text, previous_part(text, previous) if found.method == "semantic" else "", found)
    warning = _warn_once(data, f"VAL3: {found.warning}.", key="embedding") if found.warning else None
    return "\n\n".join(part for part in (context, warning) if part) or None


def _private(path, flags):
    return os.open(path, flags, 0o600)


# ponytail: two sessions pruning at the same moment may lose a line; the log only feeds calibration.
def _log_prompt(project, data: dict, prompt: str, previous: str, found) -> None:
    """One line per typed prompt for `val3 eval --draft`; lines older than LOG_DAYS days are dropped."""
    path = state_dir() / "prompts" / f"{re.sub(r'[^A-Za-z0-9_.-]', '-', project.name)}.jsonl"
    now = datetime.now(timezone.utc)
    record = {"at": now.isoformat(timespec="seconds"), "session": data.get("session_id"), "prompt": prompt,
              "previous": previous, "method": found.method,
              "injected": [{"ref": hit.ref, "score": round(hit.rank, 4) if hit.method == "semantic" else None}
                           for hit in found.hits]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _prune(path, now - timedelta(days=LOG_DAYS))
        with open(path, "a", encoding="utf-8", opener=_private) as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
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
    except (ValueError, KeyError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)
```

`_warn_once` gains a key, so the embedding warning does not use up the "VAL3 is unavailable" marker:

```python
def _warn_once(data: dict, message: str, key: str = "") -> str | None:
    session = str(data.get("session_id") or "")
    if not session:
        return message
    state = state_dir() / "warned"
    marker = state / hashlib.sha256(f"{session}\0{key}".encode()).hexdigest()
```

(the rest of its body is unchanged).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/val3/views.py src/val3/transcript.py src/val3/hooks.py tests/test_hooks.py
git commit -m "feat: index of every entry, semantic prompt delivery and prompt log"
```

---

### Task 5: Calibration (`val3 eval`)

**Files:**
- Create: `src/val3/evaluate.py`, `tests/test_eval.py`
- Modify: `src/val3/cli.py`

**Interfaces:**
- Consumes: Task 3 `candidates`, `select`, `search`, `query_text`, `PROMPT_CAP`; Task 2 `embed`; Task 4 prompt log; plan 1 `parse_ref`.
- Produces: `evaluate.FLOORS`; `evaluate.Case(query: str, previous: str, relevant: frozenset[str], at: datetime | None)`; `evaluate.load_cases(path: Path) -> list[Case]`; `evaluate.evaluate(conn, project, cases: list[Case]) -> str`; `evaluate.draft(path: Path) -> str`; CLI `val3 eval FILE [--draft]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval.py`:

```python
import json

from helpers import OUTPUT, embedding_vault, fact, make_vault, save

A = {"title": "Sample mailbox address", "statement": "Samples arrive at samples@example.org."}
B = {"title": "Duplicate invoices rule", "statement": "Repeated supplier numbers are refused."}


def labelled(env, cases):
    path = env.root / "set.jsonl"
    path.write_text("".join(json.dumps(case) + "\n" for case in cases))
    return path


def two_entries(env, embedder):
    embedding_vault(env, embedder.url)
    t = env.transcript(tool_outputs=[OUTPUT])
    for entry in (A, B):
        assert save(env, fact(keywords=[], **entry), t).returncode == 0


def test_eval_prints_one_row_per_floor(env, embedder):
    two_entries(env, embedder)
    path = labelled(env, [
        {"query": f"{A['title']}\n{A['statement']}", "relevant": [f"{env.vault}#1"]},
        {"query": f"{B['title']}\n{B['statement']}", "relevant": [f"{env.vault}#1"]},
        {"query": "penguin colony weather report today please", "relevant": []},
    ])
    result = env.run("eval", str(path))
    assert result.returncode == 0, result.stderr
    rows = {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines() if line.strip()}
    assert rows["floor"] == ["injected", "needed", "served", "silent"]
    assert "full-text" in rows
    for floor in ("0.40", "0.45", "0.50", "0.55", "0.60*", "0.65", "0.70", "0.75", "0.80"):
        assert rows[floor] == ["2", "50%", "1/2", "1/1"], floor
    assert rows["latency:"][0] == "mean"


MANY = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
        "SELECT 'fact', 'Invoice note ' || n, 'invoices ledger', 'h' || n, 'p', 'm' FROM generate_series(1, 4) n")


def test_eval_selects_what_the_prompt_hook_delivers(env, embedder):
    embedding_vault(env, embedder.url)
    env.sql(MANY.format(v=env.vault))
    assert env.run("tool", "add", "ledger-tool", "--type", "cli",
                   "--description", "invoices ledger note").returncode == 0  # scores below the four notes
    prompt = "invoice note invoices ledger"
    payload = {"session_id": env.session, "cwd": str(env.project), "hook_event_name": "UserPromptSubmit",
               "prompt": prompt}
    assert env.run("hook", "prompt", input=json.dumps(payload)).returncode == 0
    log = env.state / "prompts" / "project.jsonl"
    delivered = [item["ref"] for item in json.loads(log.read_text().splitlines()[-1])["injected"]]
    assert len(delivered) == 3 and f"{env.vault}#5" in delivered  # the tool comes first
    result = env.run("eval", str(labelled(env, [{"query": prompt, "relevant": delivered}])))
    rows = {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines() if line.strip()}
    assert rows["0.60*"] == ["3", "100%", "1/1", "0/0"]


def test_eval_refuses_cases_not_labelled_yet(env, embedder):
    two_entries(env, embedder)
    result = env.run("eval", str(labelled(env, [{"query": "x", "relevant": []}, {"query": "y", "relevant": None}])))
    assert result.returncode == 1
    assert "set.jsonl:2: not labelled yet" in result.stderr


def test_eval_reads_the_vault_as_it_was_at_the_prompt(env, embedder):
    two_entries(env, embedder)
    at = env.sql("SELECT now()")[0][0].isoformat()
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(keywords=[], title="Sample mailbox address moved",
                          statement="Samples now arrive at the sample mailbox.", supersedes=f"{env.vault}#1"),
                t).returncode == 0
    query = f"{A['title']}\n{A['statement']}"
    then = labelled(env, [{"query": query, "relevant": [f"{env.vault}#1"], "at": at}])
    rows = {line.split()[0]: line.split()[1:] for line in env.run("eval", str(then)).stdout.splitlines() if line}
    assert rows["0.60*"][2] == "1/1"
    now = labelled(env, [{"query": query, "relevant": [f"{env.vault}#1"]}])
    rows = {line.split()[0]: line.split()[1:] for line in env.run("eval", str(now)).stdout.splitlines() if line}
    assert rows["0.60*"][2] == "0/1"


def test_eval_rejects_a_malformed_set(env, embedder):
    two_entries(env, embedder)
    path = env.root / "bad.jsonl"
    path.write_text('{"query": "x", "relevant": []}\nnot json\n')
    result = env.run("eval", str(path))
    assert result.returncode == 1
    assert "bad.jsonl:2: not valid JSON" in result.stderr
    path.write_text('{"query": "x"}\n')
    assert "expected query" in env.run("eval", str(path)).stderr


def test_eval_needs_the_embedding_table(env):
    make_vault(env)
    result = env.run("eval", str(labelled(env, [{"query": "x", "relevant": []}])))
    assert result.returncode == 1
    assert "no [embedding] table" in result.stderr


def test_draft_turns_the_prompt_log_into_an_unlabelled_set(env):
    log = env.root / "prompts.jsonl"
    log.write_text("".join(json.dumps(record) + "\n" for record in (
        {"at": "2026-10-10T08:00:00+00:00", "prompt": "yes", "previous": "Shall I?",
         "injected": [{"ref": "v#1", "score": 0.7}]},
        {"at": "2026-10-10T08:01:00+00:00", "prompt": "yes", "previous": "Shall I?", "injected": []},
        {"at": "2026-10-10T08:02:00+00:00", "prompt": "Who grants access?", "previous": "", "injected": []},
    )) + "garbage\n")
    result = env.run("eval", "--draft", str(log))
    assert result.returncode == 0, result.stderr
    assert [json.loads(line) for line in result.stdout.splitlines()] == [
        {"at": "2026-10-10T08:00:00+00:00", "session": None, "query": "yes", "previous": "Shall I?",
         "relevant": None, "injected": ["v#1"]},
        {"at": "2026-10-10T08:02:00+00:00", "session": None, "query": "Who grants access?", "previous": "",
         "relevant": None, "injected": []},
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_eval.py -q`
Expected: FAIL — `invalid choice: 'eval'` (exit 2).

- [ ] **Step 3: Implement**

Create `src/val3/evaluate.py`:

```python
"""`val3 eval`: the semantic floor measured on a labelled set, and drafts of a set from the prompt log.

A labelled set holds one JSON object per line: `query`, optional `previous` (the end of the previous
assistant turn), `relevant`, the refs of the entries needed at that moment (`[]` when a person
decided none was; `null` while not labelled), and optional `at`, the moment of the prompt: the vault
is read as it was then, so later admissions and supersessions do not change the result.
"""
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from val3 import Val3Error
from val3.config import CONFIG_FILE, parse_ref
from val3.embedding import embed
from val3.search import PROMPT_CAP, candidates, query_text, search, select

FLOORS = tuple(round(0.40 + 0.05 * i, 2) for i in range(9))
TIMEOUT = 30.0


@dataclass(frozen=True)
class Case:
    query: str
    previous: str
    relevant: frozenset[str]
    at: datetime | None


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise Val3Error(f"cannot read {path}: {exc.strerror}") from None


def load_cases(path: Path) -> list[Case]:
    cases = []
    for number, line in enumerate(_lines(path), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except ValueError:
            raise Val3Error(f"{path}:{number}: not valid JSON") from None
        data = data if isinstance(data, dict) else {}
        query, previous, relevant = data.get("query"), data.get("previous", ""), data.get("relevant")
        if "relevant" in data and relevant is None:
            raise Val3Error(f"{path}:{number}: not labelled yet (relevant is null)")
        if (not isinstance(query, str) or not query.strip() or not isinstance(previous, str)
                or not isinstance(relevant, list) or not all(isinstance(ref, str) for ref in relevant)):
            raise Val3Error(f"{path}:{number}: expected query (text), optional previous (text), "
                            "relevant (list of entry refs) and optional at (ISO time)")
        for ref in relevant:
            parse_ref(ref)
        try:
            at = datetime.fromisoformat(data["at"]) if data.get("at") is not None else None
        except (TypeError, ValueError):
            raise Val3Error(f"{path}:{number}: at must be an ISO time with its offset") from None
        if at is not None and at.tzinfo is None:
            raise Val3Error(f"{path}:{number}: at must be an ISO time with its offset")
        cases.append(Case(query.strip(), previous, frozenset(relevant), at))
    if not cases:
        raise Val3Error(f"{path}: no cases")
    return cases


def evaluate(conn, project, cases: list[Case]) -> str:
    settings = project.embedding
    if settings is None:
        raise Val3Error(f"no [embedding] table in {project.root / CONFIG_FILE}")
    pools, baseline, timings = [], [], []
    for case in cases:
        started = time.monotonic()
        space, (vector,) = embed(settings, [query_text(case.query, case.previous)], TIMEOUT)
        timings.append(time.monotonic() - started)
        # Every candidate at any floor; select() below is the hook's own rule (search.find).
        pools.append(candidates(conn, project.vaults, space, vector, case.query, -1.0, PROMPT_CAP, case.at))
        baseline.append([hit.ref for hit in search(conn, project.vaults, case.query, PROMPT_CAP, as_of=case.at)])
    lines = [_line("floor", "injected", "needed", "served", "silent"),
             _line(*_measure("full-text", cases, baseline))]
    for floor in FLOORS:
        injected = [[hit.ref for hit in select(pool, floor, PROMPT_CAP)] for pool in pools]
        label = f"{floor:.2f}" + ("*" if abs(floor - settings.floor) < 1e-9 else "")
        lines.append(_line(*_measure(label, cases, injected)))
    lines.append(f"latency: mean {1000 * sum(timings) / len(timings):.0f} ms, "
                 f"max {1000 * max(timings):.0f} ms over {len(timings)} prompts")
    return "\n".join(lines)


def _measure(label: str, cases: list[Case], injected: list[list[str]]) -> tuple[str, ...]:
    answered = [(case, refs) for case, refs in zip(cases, injected) if case.relevant]
    unanswered = [refs for case, refs in zip(cases, injected) if not case.relevant]
    total = sum(len(refs) for refs in injected)
    needed = sum(len(case.relevant.intersection(refs)) for case, refs in zip(cases, injected))
    served = sum(1 for case, refs in answered if case.relevant.intersection(refs))
    silent = sum(1 for refs in unanswered if not refs)
    share = f"{100 * needed / total:.0f}%" if total else "-"
    return label, str(total), share, f"{served}/{len(answered)}", f"{silent}/{len(unanswered)}"


def _line(*cells: str) -> str:
    return f"{cells[0]:<10}" + "".join(f"{cell:>10}" for cell in cells[1:])


def draft(path: Path) -> str:
    """An unlabelled set from a prompt log: one case per distinct prompt and previous turn, with the time
    and session of its first occurrence; `relevant` is null until a person labels it."""
    seen: set[tuple[str, str]] = set()
    out = []
    for line in _lines(path):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or not isinstance(record.get("prompt"), str):
            continue
        key = (record["prompt"], record.get("previous") if isinstance(record.get("previous"), str) else "")
        if key in seen:
            continue
        seen.add(key)
        injected = [item.get("ref") for item in record.get("injected") or [] if isinstance(item, dict)]
        out.append(json.dumps({"at": record.get("at"), "session": record.get("session"), "query": key[0],
                               "previous": key[1], "relevant": None, "injected": injected}, ensure_ascii=False))
    return "\n".join(out)
```

`src/val3/cli.py` — import `from val3.evaluate import draft, evaluate, load_cases`; add after the `embed` sub-parser:

```python
    eval_parser = sub.add_parser("eval", help="measure the semantic floor against a labelled set")
    eval_parser.add_argument("file", type=Path, help="labelled set (JSON lines), or a prompt log with --draft")
    eval_parser.add_argument("--draft", action="store_true", help="print an unlabelled set from a prompt log")
    eval_parser.set_defaults(func=cmd_eval)
```

and:

```python
def cmd_eval(args) -> None:
    if args.draft:
        text = draft(args.file)
        if text:
            print(text)
        return
    project = load_project(Path.cwd())
    cases = load_cases(args.file)
    with connect(project.service) as conn:
        print(evaluate(conn, project, cases))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/val3/evaluate.py src/val3/cli.py tests/test_eval.py
git commit -m "feat: val3 eval measures the floor; drafts a set from the prompt log"
```

---

### Task 6: Documentation

**Files:**
- Modify: `README.md`, `CONTEXT.md`, `docs/adr/0002-index-delivered-as-session-start-context.md`, `docs/design/2026-10-09-semantic-search-design.md`

- [ ] **Step 1: README**

- Status line: `Status: plans 1, 2 and 4 (store, writer, review list, recall, delivery with semantic search, capture hooks).`
- "Server setup": step 1 becomes `Create a database named val3 on a PostgreSQL 16 server with the pgvector extension available (for example the image pgvector/pgvector:0.8.1-pg16).`; step 2 says the role must be allowed to run `CREATE EXTENSION unaccent` and `CREATE EXTENSION vector`; add step 4: `Vaults created before semantic search need its table: val3 vault upgrade <vault> --service <service> (idempotent; roles keep their access).`
- New section after "Project", **Semantic search (optional)**:

````markdown
## Semantic search (optional)

VAL3 can find entries by meaning with a local embedding service; no text leaves your network.
Run Ollama next to the PostgreSQL server (add `--gpus all` on a host with an NVIDIA GPU and the
NVIDIA Container Toolkit) and pull the model:

```bash
docker run -d --name val3-ollama --restart unless-stopped -p 127.0.0.1:11434:11434 \
  -v val3-ollama:/root/.ollama ollama/ollama:0.34.2
docker exec val3-ollama ollama pull bge-m3
```

Then add to `val3.toml`:

```toml
[embedding]
url = "http://127.0.0.1:11434"   # optional
model = "bge-m3"                 # optional
floor = 0.60                     # optional; minimum cosine similarity for per-prompt delivery
```

Vectors are stored when an entry is admitted. If the service is down, the entry is admitted
anyway, is found by full-text search meanwhile, and `val3 embed` stores the missing vectors later
(the drainer also runs it after each extraction). Vectors are kept per model build: after pulling a
new build of the model, run `val3 embed` once. Without an `[embedding]` table, VAL3 uses full-text
search.
````

- "Claude Code hooks", paragraph on `session-start` and `prompt`, becomes: `session-start` injects the index of every live entry (one line each, grouped by knowledge index), exports `VAL3_SESSION_ID` and `VAL3_TRANSCRIPT`, and loads the embedding model. `prompt` injects at most three entries above the floor for each typed prompt; a prompt of six words or fewer is read together with the end of the previous answer; harness turns (subagent reports, notifications) receive nothing. If the embedding service does not answer within a second, entries are found by full-text search and the session is warned once. Each typed prompt is logged with what was injected in `~/.local/state/val3/prompts/<project>.jsonl` (readable only by you, kept 30 days). Keep the sentence on silence outside adopted projects and on fail-open behaviour.
- New section **Calibrating the floor**:

````markdown
## Calibrating the floor

```bash
val3 eval --draft ~/.local/state/val3/prompts/<project>.jsonl > draft.jsonl
# split draft.jsonl by "session" into calibration.jsonl and validation.jsonl; in each, replace
# "relevant": null with the entries that were needed at that moment (without them the agent
# would have acted against a decision, asked again or redone work), or with [] when none was
val3 eval calibration.jsonl     # choose the floor; set it in val3.toml
val3 eval validation.jsonl      # confirm it: read the row marked *
```

`val3 eval` prints, for floors 0.40 to 0.80 and for full-text search, the entries injected, the
share of them that were needed, the prompts served and the prompts left silent, then the embedding
latency; the configured floor is marked `*`. It applies the same selection as the prompt hook and
reads the vault as it was at each prompt (`at`), so later entries do not change a labelled set. It
refuses a set with prompts still unlabelled (`null`). Labelled sets hold project text: keep them out
of public repositories.
````

- [ ] **Step 2: CONTEXT.md** — append:

```markdown
- **Session index** — the list of every live entry injected at session start, one line each.
- **Embedding** — the vector of an entry's title and statement in one embedding space, stored in the
  vault's `embedding` table; computed by the local embedding service.
- **Embedding space** — model name, model digest, number of dimensions and version of the embedded
  text; only vectors of the same space are compared.
- **Floor** — the minimum cosine similarity an entry needs to be injected for a prompt.
- **Needed entry** — an entry without which the agent would act against a project decision, ask
  again for something settled, or redo work. Entries on the same topic that change nothing are noise.
- **Prompt log** — the local file with one line per typed prompt and what was injected.
- **Labelled set** — prompts with the entries needed for each (`[]` when none was, `null` while not
  labelled), read by `val3 eval`; one set calibrates the floor, another from other sessions confirms it.
```

- [ ] **Step 3: ADR 0002** — append:

```markdown
## Update 2026-10-10

Design v2 of delivery and search (`docs/design/2026-10-09-semantic-search-design.md`) widens the
index to every live entry, facts and decisions included; the rest of this decision stands.
```

- [ ] **Step 4: Design doc** — status line becomes `Status: **v2, decided 2026-10-10; implemented by plan 4 (`docs/plans/2026-10-10-val3-plan-4-delivery-search.md`).**` Then bring it in line with the plan's review of 2026-10-11:
  - 4.3: the table is `embedding (entry_id, model, digest, text_version, dims, vector, created_at)`, unique on `(entry_id, model, digest, text_version)`; vectors are compared only within one embedding space (model, digest, dimensions, text version). After an admission one batch is embedded, newest first; the drainer and `val3 embed` fill the rest.
  - 4.4: replace the last bullet with `Entries without a vector in the current space are found by full-text search among themselves and offered with the semantic hits, after them (plan 4, decision 6), so an entry admitted while the service was down reaches the prompt hook too.`
  - 4.5: `relevant` is `null` while not labelled and `[]` for a decided no-answer case; `val3 eval` refuses unlabelled cases, applies the hook's own selection, reads the vault as of each case's `at`, and the floor is chosen on one set and confirmed on another from other sessions.

- [ ] **Step 5: Commit**

```bash
git add README.md CONTEXT.md docs/adr/0002-index-delivered-as-session-start-context.md docs/design/2026-10-09-semantic-search-design.md
git commit -m "docs: semantic search setup, calibration and vocabulary"
```

---

### Task 7: Check on the development machine (by hand)

No code. Every number and outcome goes to `SESSION.md`, as measured.

- [x] **Step 1: GPU** — done on 2026-10-11 (fact 5).

After WSL was restarted (fact 5): `nvidia-smi --query-gpu=name,driver_version --format=csv` shows a driver of 570 or newer; `docker start val3-ollama` (if not running); `docker logs val3-ollama 2>&1 | grep "inference compute"` names a CUDA device, not `library=cpu`. If the GPU is still unusable, record the error and go on with CPU: nothing below depends on it.

- [ ] **Step 2: Move `val3-dev` to the pgvector image (dump and restore)**

```bash
mkdir -p ~/.local/share/val3/backup
docker exec val3-dev psql -U val3 -d val3 -Atc "SELECT nspname FROM pg_namespace n WHERE EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = n.oid AND c.relname = 'entry')"
docker exec val3-dev psql -U val3 -d val3 -Atc "SELECT count(*) FROM adopter1.entry"
docker exec val3-dev pg_dumpall -U val3 > ~/.local/share/val3/backup/val3dev-2026-10-10.sql
docker stop val3-dev && docker rename val3-dev val3-dev-pg16
SCRATCH=$(mktemp -d) && chmod 700 "$SCRATCH"
docker inspect val3-dev-pg16 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^POSTGRES_' > "$SCRATCH/env"
docker run -d --name val3-dev --restart unless-stopped -p 127.0.0.1:5433:5432 \
  -v val3-dev-pgv:/var/lib/postgresql/data --env-file "$SCRATCH/env" pgvector/pgvector:0.8.1-pg16
rm -r "$SCRATCH"
```

Never print the environment file: it holds the password. Wait until `docker exec val3-dev pg_isready -U val3` succeeds, then restore and compare:

```bash
docker exec -i val3-dev psql -U val3 -d postgres -q < ~/.local/share/val3/backup/val3dev-2026-10-10.sql
docker exec val3-dev psql -U val3 -d val3 -Atc "SELECT count(*) FROM adopter1.entry"
```

Expected errors during the restore: only `role "val3" already exists` and `database "val3" already exists`. The count matches the one taken before. The old container stays stopped with its volume `val3-dev-data` until a week passes without problems (TODO item).

- [ ] **Step 3: Upgrade and embed**

For each vault listed in step 2: `val3 vault upgrade <vault> --service val3dev` prints `upgraded vault <vault>`. In adopter1's `val3.toml` add an `[embedding]` table with no keys (defaults); the file stays uncommitted there, as before, and the adopter1 session is told. In adopter1: `val3 embed` prints `embedded N entries` with N the number of live entries.

- [ ] **Step 4: Latency and behaviour**

In adopter1, for five typed prompts taken from its sessions (two short ones with a transcript path, three long ones), time the hook:

```bash
time (printf '%s' '{"prompt": "<prompt>", "cwd": "<adopter1 path>", "session_id": "val3-probe", "transcript_path": "<transcript>"}' | val3 hook prompt)
```

Record wall time with a warm model. If the GPU is in use, measure it there, then recreate `val3-ollama` without `--gpus all` (same volume), measure on CPU, and recreate it with the GPU. Target: under about 0.5 s on CPU. Then unload the model (`curl -s http://127.0.0.1:11434/api/embed -d '{"model":"bge-m3","input":"x","keep_alive":0}'`), run `val3 hook session-start` once (time it) and one prompt: the prompt must be semantic (no warning). Remove the probe lines (`"session": "val3-probe"`) from `~/.local/state/val3/prompts/adopter1.jsonl` and the probe's warn markers.

- [ ] **Step 5: Suite on the merged result**

`.venv/bin/python -m pytest -q` passes on the branch before merging.

---

### Task 8: Session close

- [ ] `SESSION.md`: dated entry with what was built, the test count, the Task 7 measurements (CPU and GPU), the restore outcome and anything that did not go as planned.
- [ ] `TODO.md`: plan 4 done (merge commit); new items: label a first set from the adopter1 prompt log with the owner and run `val3 eval`; delete `val3-dev-pg16` and volume `val3-dev-data` after a week without problems; development PostgreSQL line now names the pgvector image; plan 3 and the production host stay.
- [ ] Merge `feature/plan-4-delivery-search` into `main` with `--no-ff`, rerun the suite on the merge, scan the diff for secrets and internal names, push, delete the branch.
