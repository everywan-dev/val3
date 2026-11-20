# VAL3 plan 7 — withdrawal of an entry or an import batch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** `val3 withdraw` takes an entry or a whole import batch out of delivery for good, with a cause and a
reason; inside the agent it becomes a proposal a person approves.

**Architecture:** a new append-only table `withdrawal` (own SQL file, added by `vault upgrade`); the writer gains
`withdraw`, `propose_withdrawal` and a `withdrawal` branch in `decide`; one SQL condition `live()` in `db.py`
replaces the three copies of "not superseded" in search and the session index. A vault without the table (linked
from an older installation) reads as having withdrawn nothing.

**Tech Stack:** Python 3.11 standard library, psycopg 3, PostgreSQL 16 with pgvector; pytest driving
`python -m val3` as a subprocess against a disposable PostgreSQL (`pgvector/pgvector:0.8.1-pg16`).

**Spec:** `docs/design/2026-10-13-withdrawal-design.md` (accepted 2026-10-14, W1-W7), ADR 0005.

## Global Constraints

- Everything in English: code, comments, messages, docs, commits.
- No internal project names, hosts or people in the repository.
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error.
- Tests drive the CLI as a subprocess and assert on exit codes, output and rows; never import package internals.
- Branch `feature/withdrawal` from `main`; Conventional Commits; one logical change per commit.
- Run the suite with `.venv/bin/pytest -q` (Docker needed). A single test: `.venv/bin/pytest -q tests/<file>::<test>`.
- Vault tables are append-only (`val3_common.forbid_change()` triggers); nothing is updated or deleted.
- Output strings exactly as the design gives them: `withdrawn <vault>#<id>`; `batch <id>: N withdrawn, N pending
  rejected, N not withdrawn (existed before the batch), N already withdrawn`; `proposed: review #N`;
  `WITHDRAWN (<cause>) <date> by <who>: <reason>`; `approving withdraws (<cause>): <reason>`;
  `<ref> was withdrawn (<cause>): <reason>`.

## Review Focus

1. A linked vault from an older installation has no `withdrawal` table: recall, the session index and `show`
   must keep working and read it as having withdrawn nothing — Task 3 test.
2. A home vault not upgraded yet: `val3 withdraw` fails with exit 1 naming `val3 vault upgrade`, and `save` and
   `show` keep working — Task 2 test.
3. Withdrawing an entry that is already superseded is allowed and recorded; delivery does not change — Task 2 test.
4. A batch proposal approved after some of its entries were withdrawn directly withdraws the rest and counts those
   as already withdrawn; it is not stale — Task 6 test.
5. A reason with control characters or line breaks must not redraw the terminal on `show` or on a card: line
   breaks are folded to spaces when stored, control characters are escaped when shown — Task 2 test.

---

### Task 1: Schema — `withdrawal` table, review reasons, upgrade

**Files:**
- Create: `src/val3/withdrawal.sql`
- Modify: `src/val3/vault.sql` (`review_item_reason_check`), `src/val3/db.py` (`create_vault`, `_CHECKS`,
  `upgrade_vault`, new `_copy_grants`)
- Test: `tests/test_vault.py`, `tests/helpers.py`

**Interfaces:**
- Produces: table `<vault>.withdrawal (entry_id PK, cause, reason, withdrawn_by, review_id, batch, created_at)`;
  `review_item.reason` accepts `'withdrawal'` (proposal) and `'withdrawn'` (notice).

- [ ] **Step 1: Write the failing tests.** In `tests/test_vault.py` add `"withdrawal"` to `TABLES`. In
  `test_vault_tables_are_append_only`, after the embedding insert add
  `env.sql(f"INSERT INTO {env.vault}.withdrawal (entry_id, cause, reason, withdrawn_by) VALUES (%s, 'wrong', 'r', 'u')", (entry_id,))`
  and to the statements `f"UPDATE {env.vault}.withdrawal SET reason = 'x'"`, `f"DELETE FROM {env.vault}.withdrawal"`.
  In `tests/helpers.py` add:

```python
# the reason check of a vault created before plan 7 (no withdrawal proposals or notices)
OLD_REASONS = ("ALTER TABLE {v}.review_item DROP CONSTRAINT review_item_reason_check, "
               "ADD CONSTRAINT review_item_reason_check CHECK (reason IN ('recipe', 'tool', 'user-confirmation', "
               "'supersession', 'recipe-cites-superseded', 'import'))")
```

  and in `tests/test_vault.py` (import `OLD_REASONS` from helpers):

```python
def test_upgrade_adds_the_withdrawal_table_and_its_review_reasons(env):
    create(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    for role, mode in ((env.reader, "--read"), (env.writer, "--write")):
        assert env.run("vault", "grant", env.vault, role, mode, "--service", "val3test").returncode == 0
    env.sql(f"DROP TABLE {env.vault}.withdrawal")  # a vault created before this plan
    env.sql(OLD_REASONS.format(v=env.vault))
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    entry_id = env.sql(ENTRY.format(v=env.vault), user=env.writer)[0][0]
    env.sql(f"INSERT INTO {env.vault}.withdrawal (entry_id, cause, reason, withdrawn_by) "
            "VALUES (%s, 'obsolete', 'r', 'u')", (entry_id,), user=env.writer)
    assert env.sql(f"SELECT count(*) FROM {env.vault}.withdrawal", user=env.reader) == [(1,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(f"INSERT INTO {env.vault}.withdrawal (entry_id, cause, reason, withdrawn_by) "
                "VALUES (%s, 'wrong', 'r', 'u')", (entry_id,), user=env.reader)
    env.sql(f"INSERT INTO {env.vault}.review_item (reason, payload, content_hash, origin_project, origin_machine) "
            "VALUES ('withdrawal', '{{}}', 'x', 'p', 'm')")
    assert env.run("vault", "upgrade", env.vault, "--service", "val3test").stdout.strip() == \
        f"vault {env.vault} is up to date"


def test_a_withdrawal_needs_a_known_cause_and_a_reason(env):
    create(env)
    entry_id = insert_entry(env)
    for cause, reason in (("mistaken", "r"), ("wrong", "  ")):
        with pytest.raises(psycopg.errors.CheckViolation):
            env.sql(f"INSERT INTO {env.vault}.withdrawal (entry_id, cause, reason, withdrawn_by) "
                    "VALUES (%s, %s, %s, 'u')", (entry_id, cause, reason))
```

  (`'{{}}'` because the statement is an f-string.)

- [ ] **Step 2: Run them and see them fail** — `.venv/bin/pytest -q tests/test_vault.py`: no `withdrawal` table.

- [ ] **Step 3: Implement.** `src/val3/withdrawal.sql`:

```sql
-- Entries taken out of delivery for good (design 2026-10-13-withdrawal-design.md). A withdrawal is final.
-- Run with search_path set to the vault and public.
CREATE TABLE withdrawal (
    entry_id bigint PRIMARY KEY REFERENCES entry (id),
    cause text NOT NULL CHECK (cause IN ('wrong', 'obsolete')),
    reason text NOT NULL CHECK (length(btrim(reason)) > 0),
    withdrawn_by text NOT NULL,
    review_id bigint REFERENCES review_item (id),
    batch text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON withdrawal
    FOR EACH ROW EXECUTE FUNCTION val3_common.forbid_change();
CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON withdrawal
    FOR EACH STATEMENT EXECUTE FUNCTION val3_common.forbid_change();
```

`vault.sql`: the reason check becomes
`CHECK (reason IN ('recipe', 'tool', 'user-confirmation', 'supersession', 'recipe-cites-superseded', 'import', 'withdrawal', 'withdrawn'))`
(keep the existing line wrapping style).

`db.py`: `create_vault` runs `withdrawal.sql` after `embedding.sql`. `_CHECKS` review_item row: the condition gets
`'withdrawal', 'withdrawn'` and the value it must hold becomes `"'withdrawn'"`. `upgrade_vault`:

```python
        for table in ("embedding", "withdrawal"):
            if conn.execute("SELECT to_regclass(%s)", (f"{name}.{table}",)).fetchone()[0] is None:
                _vault_path(conn, name)
                _run_file(conn, f"{table}.sql")
                _copy_grants(conn, name, table)
                changed = True
```

replacing the embedding-only block, with the grant loop moved into:

```python
def _copy_grants(conn, name: str, table: str) -> None:
    """Roles get on a table an upgrade adds the SELECT and INSERT they hold on `entry`."""
    grants = conn.execute("""
        SELECT r.rolname, a.privilege_type
        FROM pg_class c, aclexplode(c.relacl) a JOIN pg_roles r ON r.oid = a.grantee
        WHERE c.oid = to_regclass(%s) AND a.grantee <> c.relowner
          AND a.privilege_type IN ('SELECT', 'INSERT')""", (f"{name}.entry",)).fetchall()
    for role, privilege in grants:
        conn.execute(sql.SQL("GRANT {} ON {}.{} TO {}").format(
            sql.SQL(privilege), sql.Identifier(name), sql.Identifier(table), sql.Identifier(role)))
```

Update the `upgrade_vault` docstring ("Roles keep on new tables…" stays true).

- [ ] **Step 4: Run `tests/test_vault.py tests/test_grant.py`** — all pass.
- [ ] **Step 5: Commit** — `feat: withdrawal table and review reasons, added by vault upgrade`

### Task 2: `val3 withdraw <vault>#<id>` outside the agent; `show` marks withdrawn entries

**Files:**
- Modify: `src/val3/db.py` (new `has_withdrawals`), `src/val3/writer.py` (`Withdrawal`, `withdraw`, helpers),
  `src/val3/cli.py` (`withdraw` parser, `cmd_withdraw`), `src/val3/views.py` (`show_entry`)
- Test: `tests/test_withdraw.py` (new)

**Interfaces:**
- Consumes: Task 1's table.
- Produces:
  - `db.has_withdrawals(conn, vault: str) -> bool`
  - `writer.Withdrawal(ref: str | None, batch: str | None, cause: str, reason: str)` (frozen dataclass) with
    property `title -> str`
  - `writer.withdraw(conn, project, w: Withdrawal, by: str) -> str` (the output line)
  - `writer._withdrawable(conn, vault, ref) -> int`, `writer._carry_out(conn, vault, w, by, review_id, org) -> str`,
    `writer._withdraw_entry(conn, vault, entry_id, w, by, review_id, org) -> None`, `writer._predates(vault) -> Val3Error`
  - `cli.cmd_withdraw(args)`; `args.ref`, `args.cause` (`"wrong"`/`"obsolete"`), `args.reason`

- [ ] **Step 1: Write the failing tests** — new file `tests/test_withdraw.py`:

```python
from helpers import OUTPUT, fact, make_vault, save


def admitted(env):
    """A home vault with one admitted fact, #1; returns the transcript."""
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    return t


def withdraw(env, *args, environ=None):
    return env.run("withdraw", *args, environ=environ)


def withdrawals(env):
    return env.sql(f"SELECT entry_id, cause, reason, review_id, batch FROM {env.vault}.withdrawal ORDER BY entry_id")


def test_withdrawing_an_entry_records_the_cause_and_the_reason(env):
    admitted(env)
    result = withdraw(env, f"{env.vault}#1", "--wrong", "--reason", " The count included\n draft  invoices. ")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"withdrawn {env.vault}#1"
    assert withdrawals(env) == [(1, "wrong", "The count included draft invoices.", None, None)]
    assert env.sql(f"SELECT entry_id, detail->>'cause', detail->>'reason' FROM {env.vault}.event "
                   "WHERE action = 'withdrawn'") == [(1, "wrong", "The count included draft invoices.")]
    by = env.sql(f"SELECT withdrawn_by FROM {env.vault}.withdrawal")[0][0]
    line = env.run("show", f"{env.vault}#1").stdout.splitlines()[1]
    assert line.startswith("WITHDRAWN (wrong) ") and line.endswith(f" by {by}: The count included draft invoices.")


def test_an_entry_is_withdrawn_once(env):
    admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "Replaced by the ledger.").returncode == 0
    result = withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "Again.")
    assert result.returncode == 1
    by = env.sql(f"SELECT withdrawn_by FROM {env.vault}.withdrawal")[0][0]
    assert f"{env.vault}#1 was already withdrawn on " in result.stderr and f" by {by}" in result.stderr
    assert len(withdrawals(env)) == 1


def test_withdraw_usage(env):
    admitted(env)
    ref = f"{env.vault}#1"
    for args in ((ref, "--reason", "x"), (ref, "--wrong", "--obsolete", "--reason", "x"), (ref, "--wrong"),
                 (ref, "--wrong", "--reason", " \n "), ("--wrong", "--reason", "x")):
        assert withdraw(env, *args).returncode == 2, args
    assert withdrawals(env) == []


def test_withdraw_needs_an_entry_of_the_home_vault(env):
    admitted(env)
    shared = env.vault + "_linked"
    assert env.run("vault", "create", shared).returncode == 0
    env.config(linked=[shared])
    for ref, message in ((f"{env.vault}#9", "does not exist"), (f"{shared}#1", "read only"),
                         ("v_undeclared#1", "not declared")):
        result = withdraw(env, ref, "--wrong", "--reason", "x")
        assert result.returncode == 1 and message in result.stderr, ref


def test_a_superseded_entry_can_be_withdrawn(env):
    t = admitted(env)
    newer = fact(title="April has 42 invoices, drafts excluded",
                 statement="The ERP holds 42 invoices dated April 2026, drafts excluded.", supersedes=f"{env.vault}#1")
    assert save(env, newer, t).returncode == 0
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "Drafts were counted.").returncode == 0
    shown = env.run("show", f"{env.vault}#1").stdout
    assert "WITHDRAWN (wrong)" in shown and f"SUPERSEDED by {env.vault}#2" in shown


def test_withdraw_on_a_vault_that_predates_withdrawals_names_the_upgrade(env):
    t = admitted(env)
    env.sql(f"DROP TABLE {env.vault}.withdrawal")
    result = withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "x")
    assert result.returncode == 1 and f"val3 vault upgrade {env.vault}" in result.stderr
    assert env.run("show", f"{env.vault}#1").returncode == 0
    assert save(env, fact(title="May has 50 invoices", statement="The ERP holds 50 invoices dated May 2026."),
                t).returncode == 0


def test_a_reason_cannot_redraw_the_terminal(env):
    admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "bad \x1b]0;owned\x07 count").returncode == 0
    shown = env.run("show", f"{env.vault}#1").stdout
    assert "\x1b" not in shown and "\\x1b]0;owned\\x07" in shown
```

- [ ] **Step 2: Run them and see them fail** — `val3 withdraw` does not exist (argparse exit 2 on the first).

- [ ] **Step 3: Implement.** `db.py`:

```python
def has_withdrawals(conn, vault: str) -> bool:
    """A vault created before withdrawals lacks the table until `val3 vault upgrade`; it has withdrawn nothing."""
    return conn.execute("SELECT to_regclass(%s)", (f"{vault}.withdrawal",)).fetchone()[0] is not None
```

`writer.py` (after `queue_import`/`log_import`):

```python
@dataclass(frozen=True)
class Withdrawal:
    """What `val3 withdraw` asks for: one entry (`ref`) or one import batch, its cause and a reason."""
    ref: str | None
    batch: str | None
    cause: str   # wrong: never true; obsolete: true once, no longer needed
    reason: str

    @property
    def title(self) -> str:
        return f"withdraw {self.ref or 'batch ' + str(self.batch)} ({self.cause}): {self.reason}"


def withdraw(conn, project: Project, w: Withdrawal, by: str) -> str:
    """Withdraw now, in one transaction under the vault lock (design §3.1); returns the output line."""
    vault = project.home
    try:
        with conn.transaction():
            _serialize(conn, vault)
            return _carry_out(conn, vault, w, by, None, origin(project, None))
    except psycopg.errors.UndefinedTable:
        raise _predates(vault) from None


def _predates(vault: str) -> Val3Error:
    return Val3Error(f"vault {vault} predates withdrawals: its owner runs `val3 vault upgrade {vault}`")


def _withdrawable(conn, vault: str, ref: str) -> int:
    """The id of an entry of the home vault that is not withdrawn yet."""
    entry_id = parse_ref(ref)[1]
    if conn.execute(vsql(vault, "SELECT 1 FROM {v}.entry WHERE id = %s"), (entry_id,)).fetchone() is None:
        raise Val3Error(f"{ref} does not exist")
    done = conn.execute(vsql(vault, "SELECT withdrawn_by, created_at FROM {v}.withdrawal WHERE entry_id = %s"),
                        (entry_id,)).fetchone()
    if done:
        raise Val3Error(f"{ref} was already withdrawn on {done[1]:%Y-%m-%d %H:%M} by {done[0]}")
    return entry_id


def _carry_out(conn, vault: str, w: Withdrawal, by: str, review_id: int | None, org: Origin) -> str:
    """The withdrawal itself, inside the caller's transaction; returns the output line."""
    entry_id = _withdrawable(conn, vault, w.ref)
    _withdraw_entry(conn, vault, entry_id, w, by, review_id, org)
    return f"withdrawn {w.ref}"


def _withdraw_entry(conn, vault: str, entry_id: int, w: Withdrawal, by: str, review_id: int | None,
                    org: Origin) -> None:
    conn.execute(vsql(vault, """
        INSERT INTO {v}.withdrawal (entry_id, cause, reason, withdrawn_by, review_id, batch)
        VALUES (%s, %s, %s, %s, %s, %s)"""), (entry_id, w.cause, w.reason, by, review_id, w.batch))
    _log(conn, vault, "withdrawn", org, entry_id=entry_id, review_id=review_id,
         detail={"cause": w.cause, "reason": w.reason, "batch": w.batch})
```

`cli.py` — parser (after `review`):

```python
    withdraw_parser = sub.add_parser("withdraw", help="take an entry out of delivery for good")
    withdraw_parser.add_argument("ref", help="<vault>#<id> of the home vault")
    cause = withdraw_parser.add_mutually_exclusive_group(required=True)
    cause.add_argument("--wrong", dest="cause", action="store_const", const="wrong",
                       help="the knowledge was never true")
    cause.add_argument("--obsolete", dest="cause", action="store_const", const="obsolete",
                       help="it was true and is no longer needed")
    withdraw_parser.add_argument("--reason", required=True, help="why; shown wherever the entry is shown")
    withdraw_parser.set_defaults(func=cmd_withdraw)
```

```python
def cmd_withdraw(args) -> int | None:
    reason = " ".join(args.reason.split())  # one line: it is shown inside `show` and on cards
    if not reason:
        print("val3: error: --reason must not be blank", file=sys.stderr)
        return 2
    _person_only()  # inside the agent this becomes a proposal (Task 6)
    project = load_project(Path.cwd())
    vault, _ = _declared(project, args.ref)
    if vault != project.home:
        raise Val3Error(f"{args.ref}: vault {vault} is linked here and read only; withdraw it from its home project")
    with connect(project.service) as conn:
        print(withdraw(conn, project, Withdrawal(args.ref, None, args.cause, reason), getpass.getuser()))
    return None
```

Import `Withdrawal, withdraw` from `val3.writer`.

`views.show_entry`, after the `SUPERSEDED by` insert:

```python
    if has_withdrawals(conn, vault):
        withdrawn = conn.execute(vsql(vault, """
            SELECT cause, reason, withdrawn_by, created_at FROM {v}.withdrawal WHERE entry_id = %s"""),
            (entry_id,)).fetchone()
        if withdrawn:
            cause, reason, by, at = withdrawn
            lines.insert(1, f"WITHDRAWN ({cause}) {at:%Y-%m-%d} by {by}: {reason}")
```

- [ ] **Step 4: Run `tests/test_withdraw.py tests/test_cli.py tests/test_recall.py`** — all pass.
- [ ] **Step 5: Commit** — `feat: val3 withdraw takes an entry out with its cause and reason`

### Task 3: One definition of a live entry; readers and the writer skip withdrawn entries

**Files:**
- Modify: `src/val3/db.py` (new `live`), `src/val3/search.py` (`_QUERY`, `_SEMANTIC`, `search`, `semantic`),
  `src/val3/views.py` (`session_index`), `src/val3/writer.py` (`_require`)
- Test: `tests/test_withdraw.py`

**Interfaces:**
- Consumes: `has_withdrawals` (Task 2), `val3 withdraw <ref>` (Task 2).
- Produces: `db.live(conn, vault: str, alias: str = "e") -> str` — an SQL condition with a `{v}` placeholder (for
  `vsql`) and the named parameter `%(as_of)s` (pass `None` for "now").

- [ ] **Step 1: Write the failing tests** (append to `tests/test_withdraw.py`; add `embedding_vault, recipe` to
  the helpers import and `import json`):

```python
from test_hooks import context, start


def test_a_withdrawn_entry_leaves_recall_and_the_session_index(env):
    admitted(env)
    assert f"{env.vault}#1" in env.run("recall", "invoices").stdout
    assert f"- {env.vault}#1 [fact]" in context(start(env), "SessionStart")
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "Drafts were counted.").returncode == 0
    assert env.run("recall", "invoices").stdout.strip() == "no matches"
    assert f"{env.vault}#1" not in context(start(env), "SessionStart")


def test_an_entry_cannot_cite_or_supersede_a_withdrawn_one(env):
    t = admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "Drafts were counted.").returncode == 0
    newer = fact(title="April has 41 invoices", statement="The ERP holds 41 invoices dated April 2026.",
                 supersedes=f"{env.vault}#1")
    for candidate in (recipe(f"{env.vault}#1"), newer):
        result = save(env, candidate, t)
        assert result.returncode == 1
        assert f"{env.vault}#1 was withdrawn (wrong): Drafts were counted." in result.stderr


def test_eval_reads_a_withdrawn_entry_as_it_was(env, embedder):
    embedding_vault(env, embedder.url)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(keywords=[]), t).returncode == 0
    at = env.sql("SELECT now()")[0][0].isoformat()
    assert withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "The ledger replaced it.").returncode == 0
    query = f"{fact()['title']}\n{fact()['statement']}"

    def served(case):
        path = env.root / "set.jsonl"
        path.write_text(json.dumps(case) + "\n")
        rows = {line.split()[0]: line.split()[1:] for line in env.run("eval", str(path)).stdout.splitlines() if line}
        return rows["0.60*"][2], rows["full-text"][2]

    assert served({"query": query, "relevant": [f"{env.vault}#1"], "at": at}) == ("1/1", "1/1")
    assert served({"query": query, "relevant": [f"{env.vault}#1"]}) == ("0/1", "0/1")


def test_a_linked_vault_without_the_withdrawal_table_is_still_read(env):
    shared = env.vault + "_old"
    env.config(home=shared)
    assert env.run("vault", "create", shared).returncode == 0
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    env.sql(f"DROP TABLE {shared}.withdrawal")  # created by an older installation, never upgraded
    env.config(linked=[shared])
    assert env.run("vault", "create", env.vault).returncode == 0
    assert f"{shared}#1" in env.run("recall", "invoices").stdout
    assert f"- {shared}#1 [fact]" in context(start(env), "SessionStart")
    assert env.run("show", f"{shared}#1").returncode == 0
```

  If the `full-text` row of `val3 eval` does not show `served` in the third column, assert only the `0.60*` row and
  say so in the report.

- [ ] **Step 2: Run them and see them fail** — the withdrawn entry is still recalled and indexed; the save is
  admitted or fails with another message.

- [ ] **Step 3: Implement.** `db.py`:

```python
def live(conn, vault: str, alias: str = "e") -> str:
    """SQL condition: entry `alias` is live as of the parameter %(as_of)s (null: now), that is, no supersession and
    no withdrawal of it recorded by then (design §4.1). Every reader uses it; the text holds `{v}` for vsql."""
    condition = ("NOT EXISTS (SELECT 1 FROM {v}.supersession s WHERE s.old_id = ALIAS.id"
                 " AND (%(as_of)s::timestamptz IS NULL OR s.created_at <= %(as_of)s))")
    if has_withdrawals(conn, vault):
        condition += (" AND NOT EXISTS (SELECT 1 FROM {v}.withdrawal w WHERE w.entry_id = ALIAS.id"
                      " AND (%(as_of)s::timestamptz IS NULL OR w.created_at <= %(as_of)s))")
    return condition.replace("ALIAS", alias)
```

`search.py`: in `_QUERY` the `live` CTE becomes

```sql
WITH live AS (
    SELECT e.* FROM {v}.entry e
    WHERE (%(as_of)s::timestamptz IS NULL OR e.created_at <= %(as_of)s) AND /*live*/
), n AS (
```

and in `_SEMANTIC` the `WHERE` of `scored` becomes
`WHERE (%(as_of)s::timestamptz IS NULL OR e.created_at <= %(as_of)s) AND /*live*/`. In `search`:

```python
    hits = [Hit(vault, *row) for vault in vaults
            for row in conn.execute(vsql(vault, query.replace("/*live*/", live(conn, vault))), params)]
```

and in `semantic`: `rows = conn.execute(vsql(vault, _SEMANTIC.replace("/*live*/", live(conn, vault))), params).fetchall()`.
Update the `search` docstring: "with `as_of`, entries, supersessions and withdrawals recorded up to that moment".

`views.session_index`: `WHERE /*live*/` in place of the supersession test, executed as
`conn.execute(vsql(vault, SQL.replace("/*live*/", live(conn, vault))), {"as_of": None})` (keep the query text
where it is; `SQL` stands for that string).

`writer._require`, after the supersession check:

```python
    if has_withdrawals(conn, vault):
        withdrawn = conn.execute(vsql(vault, "SELECT cause, reason FROM {v}.withdrawal WHERE entry_id = %s"),
                                 (entry_id,)).fetchone()
        if withdrawn:
            raise Val3Error(f"{ref} was withdrawn ({withdrawn[0]}): {withdrawn[1]}")
```

Import `has_withdrawals, live` where used. `evaluate.py` needs no change (it calls `search`/`semantic` with
`as_of`).

- [ ] **Step 4: Run `tests/test_withdraw.py tests/test_recall.py tests/test_hooks.py tests/test_eval.py tests/test_supersede.py`**
  — all pass.
- [ ] **Step 5: Commit** — `feat: withdrawn entries leave search, the session index and citations`

### Task 4: Notices — recipes that cite a withdrawn entry, the entry it superseded

**Files:**
- Modify: `src/val3/writer.py` (`NOTICES`, `_withdraw_entry`, `decide`), `src/val3/views.py` (`review_card`)
- Test: `tests/test_withdraw.py`

**Interfaces:**
- Consumes: `live` (Task 3), `_withdraw_entry` (Task 2).
- Produces: `review_item` with `reason = 'withdrawn'`, `entry_id` = the withdrawn entry, payload
  `{"title", "entry": int, "recipes": [int], "superseded": int | None}`; `NOTICES` includes `"withdrawn"`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_withdraw.py`; add `TOOL` to the helpers import):

```python
def notices(env):
    return env.sql(f"SELECT id, payload->>'title' FROM {env.vault}.review_item WHERE reason = 'withdrawn' ORDER BY id")


def test_recipes_citing_a_withdrawn_fact_or_tool_get_a_notice(env):
    t = admitted(env)                                                     # fact #1
    assert env.run("tool", "add", "invoice-cli", "--type", "cli",
                   "--description", "Counts invoices.").returncode == 0  # tool #2
    steps = recipe(f"{env.vault}#1")
    steps["recipe"]["steps"][1]["tool"] = f"{env.vault}#2"
    assert save(env, steps, t).returncode == 0                            # queued: review #1
    assert env.run("review", "approve", "1").returncode == 0              # recipe #3
    assert save(env, fact(title="May has 50 invoices", statement="The ERP holds 50 invoices dated May 2026."),
                t).returncode == 0                                        # fact #4, cited by nothing
    for ref in ("#2", "#1", "#4"):
        assert withdraw(env, env.vault + ref, "--obsolete", "--reason", "The ledger replaced it.").returncode == 0
    assert [title for _, title in notices(env)] == [
        f"{env.vault}#2 was withdrawn (obsolete); cited by {env.vault}#3",
        f"{env.vault}#1 was withdrawn (obsolete); cited by {env.vault}#3"]
    review_id = notices(env)[0][0]
    card = env.run("review", "show", str(review_id)).stdout
    assert f"RECIPE {env.vault}#3" in card and "WITHDRAWN (obsolete)" in card
    result = env.run("review", "reject", str(review_id))
    assert result.returncode == 1 and "a withdrawal is final" in result.stderr
    assert env.run("review", "approve", str(review_id)).stdout.strip() == f"acknowledged review #{review_id}"


def test_withdrawing_a_newer_entry_does_not_bring_the_older_one_back(env):
    t = admitted(env)
    newer = fact(title="April has 42 invoices, drafts excluded",
                 statement="The ERP holds 42 invoices dated April 2026, drafts excluded.", supersedes=f"{env.vault}#1")
    assert save(env, newer, t).returncode == 0
    assert withdraw(env, f"{env.vault}#2", "--wrong", "--reason", "Drafts were not excluded.").returncode == 0
    assert env.run("recall", "invoices").stdout.strip() == "no matches"
    [(review_id, title)] = notices(env)
    assert title == (f"{env.vault}#2 was withdrawn (wrong); it superseded {env.vault}#1, "
                     "which stays out of delivery")
    assert f"BEFORE {env.vault}#1" in env.run("review", "show", str(review_id)).stdout
```

- [ ] **Step 2: Run them and see them fail** — no `withdrawn` items are queued.

- [ ] **Step 3: Implement.** `writer.py`: `NOTICES = ("supersession", "recipe-cites-superseded", "withdrawn")`.
  At the end of `_withdraw_entry`:

```python
    recipes = [recipe_id for (recipe_id,) in conn.execute(vsql(vault, """
        SELECT e.id FROM {v}.entry e
        WHERE e.id IN (SELECT recipe_id FROM {v}.step_fact WHERE fact_id = %(id)s
                       UNION SELECT recipe_id FROM {v}.recipe_step WHERE tool_id = %(id)s)
          AND /*live*/ ORDER BY e.id""".replace("/*live*/", live(conn, vault))), {"id": entry_id, "as_of": None})]
    older = conn.execute(vsql(vault, "SELECT old_id FROM {v}.supersession WHERE new_id = %s"), (entry_id,)).fetchone()
    if recipes or older:  # design §5: a person sees what the withdrawal leaves behind
        title = f"{vault}#{entry_id} was withdrawn ({w.cause})"
        if recipes:
            title += "; cited by " + ", ".join(f"{vault}#{recipe_id}" for recipe_id in recipes)
        if older:
            title += f"; it superseded {vault}#{older[0]}, which stays out of delivery"
        _queue(conn, vault, "withdrawn", {"title": title, "entry": entry_id, "recipes": recipes,
                                          "superseded": older[0] if older else None}, entry_id, org)
```

  In `decide`, the notice branch:

```python
            if reason in NOTICES:
                if not approve:
                    undo = ("a withdrawal is final: save the knowledge again as a new entry" if reason == "withdrawn"
                            else f"to undo it, save an entry that supersedes {vault}#{entry_id}")
                    raise Val3Error(f"review #{review_id} is a notice and can only be approved; {undo}")
```

  `views.review_card`, a branch before `elif reason == "import":`

```python
    elif reason == "withdrawn":
        lines += [f"WITHDRAWN {vault}#{entry_id}", show_entry(conn, vault, entry_id)]
        for recipe_id in payload.get("recipes") or []:
            lines += ["", f"RECIPE {vault}#{recipe_id}", show_entry(conn, vault, recipe_id)]
        if payload.get("superseded"):
            older = payload["superseded"]
            lines += ["", f"BEFORE {vault}#{older} (out of delivery: save it again if it still holds)",
                      show_entry(conn, vault, older)]
```

  `tty.py`: add `"WITHDRAWN"` to `WARNINGS` (the card header `WITHDRAWN <ref>` and `show`'s `WITHDRAWN (<cause>)`
  line are both styled as warnings).

- [ ] **Step 4: Run `tests/test_withdraw.py tests/test_supersede.py tests/test_review.py tests/test_tty.py`** — all pass.
- [ ] **Step 5: Commit** — `feat: a withdrawal queues a notice for the recipes and the entry it leaves behind`

### Task 5: `val3 withdraw --batch`

**Files:**
- Modify: `src/val3/writer.py` (`batch_entries`, `_carry_out`), `src/val3/cli.py` (parser, `cmd_withdraw`)
- Test: `tests/test_withdraw.py`

**Interfaces:**
- Consumes: `_carry_out`, `_withdraw_entry` (Task 2/4).
- Produces: `writer.batch_entries(conn, vault: str, batch: str) -> list[tuple[int, bool]]` — `(entry_id,
  existed_before)` for every entry an approved import item of the batch points at, ordered by id.

- [ ] **Step 1: Write the failing test** (append to `tests/test_withdraw.py`):

```python
from test_import import adopter, answer, commit, dry_run, manifest, note_candidate, unit_candidate, write

OLDER = {"kind": "fact", "title": "Routing has an owner field",
         "statement": "The routing configuration names an owner.", "keywords": ["routing"]}


def imported_batch(env):
    """An import batch with one new entry (#2), one duplicate of an older entry (#1) and one item pending;
    returns (batch, {title: review id})."""
    adopter(env)
    write(env, "memory/routing-owner.md", "---\nname: routing-owner\n---\n\nRouting names an owner "
          "(`config/routing.yaml`).\n")
    commit(env, "third source")
    assert env.run("vault", "create", env.vault).returncode == 0
    assert save(env, {**OLDER, "evidence": [{"kind": "user", "quote": "routing names an owner"}]},
                env.transcript(user_messages=["Yes, routing names an owner."])).returncode == 0  # review #1
    assert env.run("review", "approve", "1").returncode == 0                                      # entry #1
    answer(env, 0, unit_candidate())
    answer(env, 1, {**OLDER, "evidence": [{"path": "config/routing.yaml", "quote": "owner: billing"}]})
    answer(env, 2, note_candidate())
    assert dry_run(env).returncode == 0
    batch = manifest(env)["batch"]
    assert env.run("import", "--apply", str(env.root / "manifest.json")).returncode == 0  # reviews #2-#4
    items = dict(env.sql(f"SELECT payload->>'title', id FROM {env.vault}.review_item WHERE reason = 'import'"))
    assert env.run("review", "approve", str(items[unit_candidate()["title"]])).returncode == 0  # new entry #2
    assert env.run("review", "approve", str(items[OLDER["title"]])).stdout.strip().endswith(
        f"duplicate of {env.vault}#1")
    return batch, items


def test_withdrawing_a_batch(env):
    batch, items = imported_batch(env)
    result = withdraw(env, "--batch", batch, "--obsolete", "--reason", "The old memory was retired.")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (f"batch {batch}: 1 withdrawn, 1 pending rejected, "
                                     "1 not withdrawn (existed before the batch), 0 already withdrawn")
    assert withdrawals(env) == [(2, "obsolete", "The old memory was retired.", None, batch)]
    by = env.sql(f"SELECT withdrawn_by FROM {env.vault}.withdrawal")[0][0]
    assert env.sql(f"SELECT decision, decided_by FROM {env.vault}.review_decision "
                   "WHERE review_id = %s", (items[note_candidate()["title"]],)) == [("rejected", by)]
    assert f"{env.vault}#1" in env.run("recall", "routing").stdout
    again = withdraw(env, "--batch", batch, "--wrong", "--reason", "Again.")
    assert again.returncode == 0 and again.stdout.strip().endswith("0 withdrawn, 0 pending rejected, "
                                                                   "1 not withdrawn (existed before the batch), 1 already withdrawn")
    result = withdraw(env, "--batch", "0" * 16, "--wrong", "--reason", "x")
    assert result.returncode == 1 and "no admitted entries" in result.stderr
    assert withdraw(env, f"{env.vault}#1", "--batch", batch, "--wrong", "--reason", "x").returncode == 2
```

  The extractor calls follow the order the dry run reads its sources; if `answer` indexes do not line up with
  three sources (check `len(env.calls()) == 3` and the manifest), reorder the three `answer` calls so that each
  candidate reaches the review list, and keep the asserts. The candidate the user confirmed (review #1) gives the
  older entry; the imported duplicate of it must be approved as `duplicate of <vault>#1`.

- [ ] **Step 2: Run it and see it fail** — `--batch` is not an argument (exit 2).

- [ ] **Step 3: Implement.** `writer.py`:

```python
def batch_entries(conn, vault: str, batch: str) -> list[tuple[int, bool]]:
    """(entry, existed before the batch) for each entry an approved import item of `batch` points at (design §4.2).

    An entry existed before when no decision of the batch created it: a decision that admits writes the entry in
    its own transaction, so both rows carry the same `now()`."""
    return conn.execute(vsql(vault, """
        SELECT d.entry_id, bool_and(e.created_at < d.created_at)
        FROM {v}.review_decision d
        JOIN {v}.review_item i ON i.id = d.review_id
        JOIN {v}.entry e ON e.id = d.entry_id
        WHERE d.decision = 'approved' AND i.reason = 'import' AND i.payload->'import'->>'batch' = %s
        GROUP BY d.entry_id ORDER BY d.entry_id"""), (batch,)).fetchall()
```

`_carry_out` gains the batch path:

```python
def _carry_out(conn, vault: str, w: Withdrawal, by: str, review_id: int | None, org: Origin) -> str:
    """The withdrawal itself, inside the caller's transaction; returns the output line."""
    if w.ref:
        _withdraw_entry(conn, vault, _withdrawable(conn, vault, w.ref), w, by, review_id, org)
        return f"withdrawn {w.ref}"
    entries = batch_entries(conn, vault, w.batch)
    created = [entry_id for entry_id, existed in entries if not existed]
    if not created:
        raise Val3Error(f"batch {w.batch} has no admitted entries")
    gone = {entry_id for (entry_id,) in conn.execute(vsql(vault, """
        SELECT entry_id FROM {v}.withdrawal WHERE entry_id = ANY(%s)"""), (created,))}
    for entry_id in created:
        if entry_id not in gone:
            _withdraw_entry(conn, vault, entry_id, w, by, review_id, org)
    pending = [review for (review,) in conn.execute(vsql(vault, """
        SELECT i.id FROM {v}.review_item i
        WHERE i.reason = 'import' AND i.payload->'import'->>'batch' = %s
          AND NOT EXISTS (SELECT 1 FROM {v}.review_decision d WHERE d.review_id = i.id) ORDER BY i.id"""), (w.batch,))]
    for review in pending:
        conn.execute(vsql(vault, """
            INSERT INTO {v}.review_decision (review_id, decision, decided_by) VALUES (%s, 'rejected', %s)"""),
            (review, by))
        _log(conn, vault, "rejected", org, review_id=review, detail={"batch": w.batch})
    return (f"batch {w.batch}: {len(created) - len(gone)} withdrawn, {len(pending)} pending rejected, "
            f"{len(entries) - len(created)} not withdrawn (existed before the batch), {len(gone)} already withdrawn")
```

`cli.py`: the reference becomes optional and exclusive with `--batch`:

```python
    target = withdraw_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("ref", nargs="?", help="<vault>#<id> of the home vault")
    target.add_argument("--batch", help="an import batch: every entry it admitted")
```

(help of the subcommand: "take an entry or an import batch out of delivery for good"). In `cmd_withdraw` the
vault checks run only when `args.ref` is set, and the call passes `Withdrawal(args.ref, args.batch, args.cause,
reason)`. If argparse refuses a positional in a mutually exclusive group on this Python, check the two by hand in
`cmd_withdraw` (exit 2 with `val3: error: give <vault>#<id> or --batch, not both`) and say so in the report.

- [ ] **Step 4: Run `tests/test_withdraw.py tests/test_import.py`** — all pass.
- [ ] **Step 5: Commit** — `feat: val3 withdraw --batch withdraws what an import batch admitted`

### Task 6: Inside the agent — a proposal a person approves

**Files:**
- Modify: `src/val3/writer.py` (`propose_withdrawal`, `decide`), `src/val3/cli.py` (`cmd_withdraw`),
  `src/val3/views.py` (`review_card`, new `withdrawal_lines`), `src/val3/tty.py` (`list_rows`, `WARNINGS`)
- Test: `tests/test_withdraw.py`, `tests/test_tty.py`

**Interfaces:**
- Consumes: `Withdrawal`, `_withdrawable`, `_carry_out`, `batch_entries`, `_predates` (Tasks 2-5).
- Produces: `writer.propose_withdrawal(conn, project, w: Withdrawal, session: str | None) -> str`;
  `review_item` with `reason = 'withdrawal'`, `entry_id` = the entry (null for a batch), payload
  `{"title", "ref" | "batch", "cause", "reason"}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_withdraw.py`):

```python
AGENT = {"CLAUDECODE": "1"}


def test_inside_the_agent_a_withdrawal_is_proposed_and_a_person_decides(env):
    admitted(env)
    ref = f"{env.vault}#1"
    result = withdraw(env, ref, "--wrong", "--reason", "Drafts were counted.", environ=AGENT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "proposed: review #1"
    assert withdrawals(env) == [] and ref in env.run("recall", "invoices").stdout
    again = withdraw(env, ref, "--wrong", "--reason", "Drafts were counted.", environ=AGENT)
    assert again.stdout.strip() == "already proposed: review #1"
    assert env.sql(f"SELECT reason, entry_id, payload->>'cause' FROM {env.vault}.review_item") == \
        [("withdrawal", 1, "wrong")]
    card = env.run("review", "show", "1").stdout
    assert f"{ref}  [fact]" in card and "approving withdraws (wrong): Drafts were counted." in card
    assert env.run("review", "approve", "1").stdout.strip() == f"approved review #1: withdrawn {ref}"
    assert withdrawals(env) == [(1, "wrong", "Drafts were counted.", 1, None)]
    assert env.sql(f"SELECT entry_id FROM {env.vault}.review_decision WHERE review_id = 1") == [(1,)]


def test_a_rejected_proposal_leaves_the_entry_live(env):
    admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "Old.", environ=AGENT).returncode == 0
    assert env.run("review", "reject", "1").returncode == 0
    assert f"{env.vault}#1" in env.run("recall", "invoices").stdout
    again = withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "Old.", environ=AGENT)
    assert again.stdout.strip() == "previously rejected: review #1"


def test_a_proposal_for_an_entry_withdrawn_meanwhile_is_stale(env):
    admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "Wrong.", environ=AGENT).returncode == 0
    assert withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "Old.").returncode == 0
    result = env.run("review", "approve", "1")
    assert result.returncode == 1 and "already withdrawn" in result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_decision WHERE review_id = 1") == [(0,)]


def test_a_proposal_is_checked_before_it_is_queued(env):
    admitted(env)
    assert withdraw(env, f"{env.vault}#9", "--wrong", "--reason", "x", environ=AGENT).returncode == 1
    assert withdraw(env, "--batch", "0" * 16, "--wrong", "--reason", "x", environ=AGENT).returncode == 1
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]
```

  and a batch case reusing Task 5's `imported_batch`:

```python
def test_a_batch_proposal_counts_entries_withdrawn_meanwhile(env):
    batch, items = imported_batch(env)
    result = withdraw(env, "--batch", batch, "--obsolete", "--reason", "Retired.", environ=AGENT)
    assert result.returncode == 0, result.stderr
    review_id = int(result.stdout.strip().rsplit("#", 1)[1])
    assert f"batch {batch}: 1 admitted entries" in env.run("review", "show", str(review_id)).stdout
    assert withdraw(env, f"{env.vault}#2", "--wrong", "--reason", "Wrong.").returncode == 0
    approved = env.run("review", "approve", str(review_id))
    assert approved.returncode == 0, approved.stderr
    assert approved.stdout.strip().endswith("0 withdrawn, 1 pending rejected, "
                                            "1 not withdrawn (existed before the batch), 1 already withdrawn")
```

  `tests/test_tty.py` (uses its `admitted_fact`, `started`, `summary`):

```python
def test_a_withdrawal_proposal_is_listed_and_approved_on_the_list(env):
    admitted_fact(env)
    assert env.run("withdraw", f"{env.vault}#1", "--wrong", "--reason", "Drafts were counted.",
                   environ={"CLAUDECODE": "1"}).returncode == 0
    terminal = started(env)
    terminal.wait_for("withdrawal")
    terminal.keys("\r")
    terminal.wait_for("approving withdraws (wrong)")
    terminal.keys("q")
    terminal.keys("a")
    terminal.wait_for("y/n")
    terminal.keys("y")
    code, screen, stderr = terminal.finish()
    assert code == 0, stderr
    assert summary(screen) == "1 approved, 0 rejected, 0 skipped, 0 failed"
    assert env.sql(f"SELECT entry_id, review_id FROM {env.vault}.withdrawal") == [(1, 1)]
```

  (Use the existing `admitted_fact` if it admits `#1` without a review item; otherwise adjust the review id.)

- [ ] **Step 2: Run them and see them fail** — inside the agent `val3 withdraw` refuses (Task 2's `_person_only`).

- [ ] **Step 3: Implement.** `writer.py`:

```python
def propose_withdrawal(conn, project: Project, w: Withdrawal, session: str | None) -> str:
    """Inside the agent: the checks of a withdrawal, then a review item a person decides (design §3.2)."""
    vault = project.home
    try:
        with conn.transaction():
            _serialize(conn, vault)
            if w.ref:
                entry_id = _withdrawable(conn, vault, w.ref)
            else:
                entry_id = None
                if not any(not existed for _, existed in batch_entries(conn, vault, w.batch)):
                    raise Val3Error(f"batch {w.batch} has no admitted entries")
            payload = {"title": w.title, ("ref" if w.ref else "batch"): w.ref or w.batch, "cause": w.cause,
                       "reason": w.reason}
            review_id, created = _queue(conn, vault, "withdrawal", payload, entry_id, origin(project, session))
            done = None if created else _decision(conn, vault, review_id)
    except (psycopg.errors.UndefinedTable, psycopg.errors.CheckViolation):  # vault not upgraded
        raise _predates(vault) from None
    if created:
        return f"proposed: review #{review_id}"
    return f"{'previously rejected' if done == 'rejected' else 'already proposed'}: review #{review_id}"
```

  `decide`, a branch between the notice branch and `elif approve:`:

```python
            elif reason == "withdrawal" and approve:
                w = Withdrawal(payload.get("ref"), payload.get("batch"), payload["cause"], payload["reason"])
                message = f"approved review #{review_id}: {_carry_out(conn, vault, w, decided_by, review_id, org)}"
```

  (`entry_id` stays the item's: the entry, or null for a batch; a rejection takes the existing `else`.)

  `cli.cmd_withdraw`: drop `_person_only()`; inside the connection:

```python
        w = Withdrawal(args.ref, args.batch, args.cause, reason)
        if os.environ.get("CLAUDECODE"):  # the agent proposes; a person approves (design W2)
            print(propose_withdrawal(conn, project, w, os.environ.get("VAL3_SESSION_ID")))
        else:
            print(withdraw(conn, project, w, getpass.getuser()))
```

  `views.py`:

```python
def withdrawal_lines(conn, vault: str, payload: dict, entry_id: int | None) -> list[str]:
    """A proposed withdrawal: the entry, or the batch and how many entries it admitted; then what approving does."""
    if payload.get("batch"):
        count = sum(not existed for _, existed in batch_entries(conn, vault, payload["batch"]))
        lines = [f"batch {payload['batch']}: {count} admitted entries"]
    else:
        lines = [show_entry(conn, vault, entry_id)]
    return lines + ["", f"approving withdraws ({payload.get('cause')}): {payload.get('reason')}"]
```

  and in `review_card` `elif reason == "withdrawal": lines += withdrawal_lines(conn, vault, payload, entry_id)`
  (import `batch_entries` from `val3.writer`).

  `tty.py`: `"approving withdraws"` joins `WARNINGS`; in `list_rows`, before the evidence logic:

```python
        if reason == "withdrawal":
            rows.append(Row(review_id, "withdrawal", title, safe(str(payload.get("cause", ""))), False))
            continue
```

- [ ] **Step 4: Run `tests/test_withdraw.py tests/test_tty.py tests/test_review.py`** — all pass.
- [ ] **Step 5: Commit** — `feat: inside the agent a withdrawal is a proposal a person approves`

### Task 7: Documentation and close

**Files:**
- Modify: `README.md` (Commands, Reviewing, new "Withdrawing" section after Reviewing), `CONTEXT.md`,
  `docs/design/2026-10-12-import-design.md` (D4), `TODO.md`, `SESSION.md`

- [ ] **Step 1: Docs.**
  - README Commands: `val3 withdraw <vault>#<id> | --batch <batch> (--wrong | --obsolete) --reason "..."` — take an
    entry, or what an import batch admitted, out of delivery for good; inside a Claude Code session it queues a
    proposal on the review list. `vault upgrade` line: "add the tables of later versions (semantic search,
    withdrawal) to a vault created before them (idempotent)".
  - README "Withdrawing": the two forms, the causes (`--wrong`: never true; `--obsolete`: true once, no longer
    needed), that a withdrawal is final (to undo, save the knowledge again), that withdrawing a newer entry does not
    bring back the one it superseded, the batch output line with its four counts, the proposal inside the agent and
    its card line `approving withdraws (<cause>): <reason>`, the `withdrawn` notice, and that a vault created before
    this version needs `val3 vault upgrade` before anything can be withdrawn from it (reading it works without).
  - README Reviewing: notices also record a withdrawal (`a withdrawal is final`).
  - CONTEXT.md: **Withdrawal** — an entry taken out of delivery for good, with a cause (`wrong` or `obsolete`) and a
    reason; never undone; **live** — not superseded and not withdrawn; **Session index** reads "every live entry"
    (already) and **Review list** gains "withdrawal proposals".
  - Import design D4: append "Built: `docs/design/2026-10-13-withdrawal-design.md` (plan 7)."
  - Commit `docs: withdrawal in the README, vocabulary and import design`.
- [ ] **Step 2:** full suite `.venv/bin/pytest -q`; record the count.
- [ ] **Step 3:** `TODO.md`: item 1 of "Next" done (`[x] Plan 7 — withdrawal`), renumber; open item for the owner:
  run `val3 vault upgrade` on the development vaults. `SESSION.md`: dated entry. Commit `docs: plan 7 closed`.
- [ ] **Step 4:** merge `--no-ff` into `main`; rerun the suite on `main`; scan tracked files for secrets and
  internal names; push; delete the branch.
