# Plan 12 — Vault sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** One project writes several vaults (specialized), vaults describe themselves, sessions read any vault
they may read, and a vault of another host is read here as a mirror loaded from a dump.

**Architecture:** R1 turns `db.py`'s scattered vault parts into one catalogue that create, upgrade, `is_current`,
dump and mirror read. R2 adds `Project.own` and `Project.writing(vault)`: every writing path keeps working on
`project.home` and receives the project retargeted to the vault it writes. Mirrors are loaded into a staging
schema and published by a short rename.

**Tech Stack:** Python 3.11+ standard library, psycopg 3, PostgreSQL 16 with pgvector; pytest driving the CLI.

**Spec:** `docs/design/2026-11-07-vault-sharing-design.md` (read it whole before any task).

## Global Constraints

- Everything in English; no internal project, host or person names in code, tests or docs.
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error.
- Tests drive `python -m val3` as a subprocess (`tests/conftest.py` `Env`); they never import `val3`.
- Vault tables are append-only (`val3_common.forbid_change()` triggers); nothing in this plan updates or deletes
  a vault row. `val3_common.vault_note` and `val3_common.mirror_sync` get the same two triggers.
- A project with no `specialized` list, on a database holding only its own and linked vaults, produces output
  identical to today. The existing suite passes after every task; an existing test changes only where a task
  names it (Task 2: `tests/test_vault.py` table list gains `identity`).
- Tests that depend on which vaults exist on the server use a database of their own (`CREATE DATABASE` through
  `env.sql`, a service from `helpers.add_service`): the test server's `val3` database is shared by all tests.
- Text written by other roles (descriptions, entries) reaches a terminal only through `views.safe()`.
- Run the suite with `.venv/bin/python -m pytest -q` (about 9 minutes; Docker must be running). Run a task's own
  tests with `-k`, the whole suite before each commit that closes a task.
- Commits: Conventional Commits, branch `feature/vault-sharing`, one commit per task at least.

## Review Focus

1. A mirror refresh while a prompt hook reads the same vault: the hook must answer (old or new content) within
   its timeout, never fail. Test in Task 7 (`test_publish_does_not_block_a_reader_beyond_the_lock_timeout`).
2. A candidate whose `"vault"` names the home vault explicitly, or a specialized vault on a project whose
   extractor answer mixes vaults in one slice: each candidate is compared with neighbours of its own vault only.
   Test in Task 5 (`test_one_slice_routes_candidates_to_two_vaults`).
3. `val3.toml` written by hand (comments, `linked` before `home`, single quotes): `vault create --specialized`
   must not corrupt it; when the layout is not the expected one it changes nothing and prints the line to add.
   Test in Task 4 (`test_unusual_toml_is_left_alone_with_the_line_to_add`).
4. A ref to a specialized vault given to `confirm`, `withdraw`, `settle` from the project: it works there and
   fails from a project that only links it, with the one read-only message. Test in Task 3.
5. A dump read from a pipe that ends early (network cut): nothing changes on the mirror side. Test in Task 7
   (`test_truncated_dump_leaves_the_mirror_intact`).

---

### Task 1: Vault catalogue (R1, no behaviour change)

**Files:**
- Modify: `src/val3/db.py` (whole vault-structure part: `create_vault`, `_CHECKS`, `_stale_checks`,
  `_has_withdrawal_evidence`, `is_current`, `_copy_grants`, `upgrade_vault`)
- Test: `tests/test_vault.py` (existing upgrade tests are the safety net; add one)

**Interfaces:**
- Produces:
  - `CATALOGUE_VERSION: int` (start at `1`).
  - `@dataclass(frozen=True) class Part: file: str; tables: tuple[str, ...]` (tables in foreign-key order) plus
    whatever upgrade data the implementer needs (exemption, added column, widened checks).
  - `PARTS: tuple[Part, ...]` in creation order: `vault.sql`, `embedding.sql`, `withdrawal.sql`,
    `index_alias.sql`, `neighbour.sql`.
  - `vault_tables() -> tuple[str, ...]`: every table of a current vault, foreign-key order, across `PARTS`.
  - `columns(conn, vault: str, table: str) -> list[str]`: the table's non-generated columns in ordinal order
    (`information_schema.columns`, `is_generated = 'NEVER'`).
  - `create_schema(conn, name: str) -> None`: the DDL of every part into an existing empty schema, inside the
    caller's transaction (used by `create_vault` and by Task 7's staging load).
- `create_vault`, `upgrade_vault`, `is_current`, `has_withdrawals`, `has_neighbours` keep their signatures and
  behaviour; `upgrade_vault` and `is_current` derive their table lists and steps from `PARTS`.

- [ ] **Step 1:** Add `test_upgrade_brings_a_vault_without_every_part_to_current` in `tests/test_vault.py`: create
  a vault, drop `index_alias`, `neighbour`, `settlement` and `neighbour_exempt` tables and the `withdrawal`
  `evidence` column via `env.sql` (disable the TRUNCATE/UPDATE triggers is not needed: DROP TABLE is allowed),
  run `val3 vault upgrade`, assert exit 0, `upgraded vault`, the four tables back, a second upgrade prints
  `is up to date`. Run it: PASS on the current code (it guards the refactor).
- [ ] **Step 2:** Refactor as in Interfaces. No message text changes.
- [ ] **Step 3:** Run `tests/test_vault.py` and the whole suite: all pass.
- [ ] **Step 4:** Commit `refactor: one catalogue of a vault's parts`.

### Task 2: Identity, vault notes, `vault describe` and `vault list`

**Files:**
- Create: `src/val3/identity.sql` (`identity (vault_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT
  now())`, a unique index on `((true))` so a second row fails, append-only triggers; no INSERT here)
- Modify: `src/val3/common.sql` (`vault_note`, `mirror_sync`, `mirror_guard()`, RLS), `src/val3/db.py` (part
  `identity.sql` appended to `PARTS`, `CATALOGUE_VERSION = 2`; upgrade inserts the identity row),
  `src/val3/cli.py` (`vault describe`, `vault list`), new `src/val3/catalogue.py` (read side: list rows)
- Test: `tests/test_catalogue.py`

**Interfaces:**
- Consumes: Task 1 `PARTS`, `create_schema`.
- Produces:
  - SQL `val3_common.vault_note (id bigint identity PK, vault text NOT NULL CHECK (vault ~ '^[a-z][a-z0-9_]{0,62}$'),
    parent text CHECK (same pattern), description text NOT NULL CHECK (length(description) BETWEEN 1 AND 300 AND
    description !~ '[\n\r]'), created_by text NOT NULL DEFAULT current_user, created_at timestamptz NOT NULL
    DEFAULT now())`; `ENABLE ROW LEVEL SECURITY`; policies: `SELECT USING (true)` and `INSERT WITH CHECK
    (created_by = current_user AND has_table_privilege(format('%I.entry', vault), 'INSERT'))`; `GRANT SELECT,
    INSERT ON val3_common.vault_note TO PUBLIC`, `GRANT USAGE ON SEQUENCE … TO PUBLIC`.
  - SQL `val3_common.mirror_sync (id identity PK, vault text NOT NULL, vault_id uuid NOT NULL, source text NOT
    NULL, rows jsonb NOT NULL, synced_by text NOT NULL DEFAULT current_user, synced_at timestamptz NOT NULL
    DEFAULT now())`; `GRANT SELECT … TO PUBLIC` only.
  - SQL `val3_common.mirror_guard() RETURNS trigger`: raises `vault % is a mirror and read only` (TG_TABLE_SCHEMA)
    when `current_setting('val3.loading', true) IS DISTINCT FROM 'on'` or when `current_user` is not a member of
    the schema's owner (`pg_has_role(current_user, nspowner, 'MEMBER')`); otherwise `RETURN NEW`.
  - `identity.sql` creates the table only. `create_vault` and `upgrade_vault` insert the identity row
    (`gen_random_uuid()`); `create_schema` (Task 1) never inserts rows, so a staging schema starts empty.
  - Every `catalogue` reader works on a database not yet upgraded (`to_regclass` probe: no notes, no mirrors).
    A vault's entry count is read only when `has_schema_privilege(schema, 'USAGE')` and
    `has_table_privilege(entry, 'SELECT')`; vaults are found from `pg_namespace`/`pg_class`, never by touching
    a schema the role cannot use.
  - `catalogue.notes(conn) -> dict[str, Note]` (latest per vault), `catalogue.vaults(conn) -> list[VaultRow]`
    (`name, entries: int | None, privilege: 'write'|'read'|'none', parent, description, written_by,
    mirror_source, synced_at`), `catalogue.is_mirror(conn, vault) -> bool` (a `mirror_sync` row exists for it).
  - CLI `val3 vault describe NAME --description TEXT [--parent NAME] [--service S]` → `described vault NAME`.
  - CLI `val3 vault list [--service S]`, one line per vault:
    `NAME  write|read|none  <n> entries | -  [parent P]  [mirror of SOURCE, synced <iso time>]` and, when there
    is a description, a second line `    DESCRIPTION (by ROLE)`; `no vaults` when none. A vault is a schema with
    both `entry` and `supersession`, not starting with `val3_`.
- `val3 vault create NAME [--description D] [--parent P]` writes the note in the same transaction.

- [ ] **Step 1: tests** in `tests/test_catalogue.py`:
  - `test_create_with_description_and_list`: create with `--description "Migration difficulties"
    --parent <other>`; `vault list` shows the name, `write`, `0 entries`, `parent <other>`, the description and
    `(by postgres)`.
  - `test_describe_appends_and_latest_wins`: two `describe` calls; list shows the second text.
  - `test_list_shows_privilege_per_role`: grant `env.reader` read (`vault grant --read`, roles created with
    `env.sql("CREATE ROLE … LOGIN")`); `vault list --service val3reader` shows `read` for it and `none` and `-`
    for a vault it has no grant on.
  - `test_reader_cannot_describe`: `vault describe --service val3reader` exits 1, and `vault_note` keeps its
    rows (`env.sql` count).
  - `test_rls_refuses_direct_inserts`: through `env.sql(..., user=env.reader)` a direct `INSERT INTO
    val3_common.vault_note` raises; through `user=env.writer` (write grant) an INSERT with `created_by =
    'postgres'` raises and one without `created_by` succeeds.
  - `test_mirror_guard_refuses_without_the_loading_flag`: a scratch schema owned by `env.writer` with a table
    and the guard trigger: INSERT as `env.writer` without the flag raises; with `SET val3.loading = 'on'` it
    succeeds; as `postgres`-granted `env.reader` holding INSERT and the flag it raises (not the owner).
  - `test_description_rules`: 301 characters and a newline each exit 1 with nothing written.
  - `test_upgrade_adds_identity_and_common_tables`: drop `identity` from a vault and run `vault upgrade`; one
    identity row back; a second upgrade `is up to date`.
- [ ] **Step 2:** Run: FAIL (unknown commands / tables).
- [ ] **Step 3:** Implement as in Interfaces. `common.sql` stays idempotent (`CREATE TABLE IF NOT EXISTS`,
  `DROP POLICY IF EXISTS` before `CREATE POLICY`). `_check_common` also requires both text search
  configurations, `vault_note`, `mirror_sync` and `mirror_guard()`.
- [ ] **Step 4:** Run the new tests and the whole suite: PASS.
- [ ] **Step 5:** Commit `feat: vault identity, descriptions and vault list`.

### Task 3: Own vaults and the target vault (R2)

**Files:**
- Modify: `src/val3/config.py` (`specialized`, `Project.own`, `Project.writing`), `src/val3/cli.py` (every
  `vault != project.home` check, `_declared`; `--vault` on `save`, `tool add`, `review`, `embed`, `index merge`),
  `src/val3/recheck.py` (`marks` takes an optional `deadline`; new `own_marks`), `src/val3/views.py`
  (`session_index`), `src/val3/hooks.py` (prompt hook marks), `src/val3/indexes.py` (marks in listings),
  `src/val3/status.py` (one block per own vault)
- Test: `tests/test_specialized.py`

**Interfaces:**
- Produces:
  - `Project.specialized: tuple[str, ...] = ()` (from `[vaults] specialized`, validated like `linked`; a vault
    appears once across home, specialized, linked).
  - `Project.own -> tuple[str, ...]` (home, then specialized); `Project.vaults -> own + linked`.
  - `Project.writing(vault: str) -> Project`: `dataclasses.replace(self, home=vault, specialized=(),
    linked=tuple(v for v in self.vaults if v != vault))`; raises `Val3Error(f"vault {vault} is read only here:
    write to it from its own project")` when `vault not in self.own`.
  - `recheck.own_marks(conn, project, ids: dict[str, Collection[int]] | None = None) -> tuple[str | None,
    dict[tuple[str, int], list[Change]]]`: `marks` for every own vault with one shared deadline.
- Commands: `save`, `tool add`, `review` (list, show, approve, reject, `--tty`), `embed`, `index merge` take
  `--vault V` (default home) and run with `project.writing(V)`. `settle`, `withdraw REF`, `confirm` run with
  `project.writing(<ref's vault>)`. `show` marks a ref of any own vault.
- `val3 status`: after the existing home line, one line per specialized vault in the same format with
  `specialized vault NAME:` instead of `home vault NAME:`, and its `to settle:` line.

- `Project.writing` keeps two messages: a linked vault → `vault V is read only here: write to it from its own
  project`; a vault not declared at all → the existing `vault V is not declared in <val3.toml path>` (existing
  tests assert it).
- [ ] **Step 1: tests** in `tests/test_specialized.py` (setup: create vaults `env.vault` and
  `f"{env.vault}_s"` with `vault create`, write `val3.toml` by hand with `specialized = ["<v>_s"]`):
  - `test_save_with_vault_writes_the_specialized_vault`: `save --vault <v>_s` prints `admitted <v>_s#1`; a row
    in `<v>_s.entry`, none in the home vault.
  - `test_session_index_lists_both_vaults`: hook `session-start` output holds `<v>#1` and `<v>_s#1`.
  - `test_settle_and_withdraw_in_the_specialized_vault`: two close facts (fake embedder, as in
    `tests/test_settle.py`) in `<v>_s`; `status` names `<v>_s#2` under `to settle`; `settle … --keep` exits 0;
    `withdraw <v>_s#1 --cause obsolete …` follows the rules of `tests/test_withdraw.py`.
  - `test_linked_vault_is_read_only_here`: a second project linking `<v>_s` runs `confirm <v>_s#1`, `settle`,
    `withdraw` and `save --vault <v>_s`: each exits 1 with `vault <v>_s is read only here: write to it from its
    own project`.
  - `test_unknown_vault_option`: `save --vault nope` exits 1 with the same message.
  - `test_toml_rejects_a_vault_twice`: `specialized` repeating home or a linked vault exits 1 on any command.
  - `test_every_writing_command_takes_vault`: in `<v>_s`: `confirm <v>_s#1` with fresh evidence, `tool add
    --vault`, a recipe queued and `review --vault <v>_s` lists it and `review approve` decides it, `index merge
    --vault`, `embed --vault` (fake embedder) stores vectors there only.
  - `test_marks_are_per_vault`: entry `#1` in both vaults, only `<v>_s#1` cites a file that changed (file
    evidence as in `tests/test_recheck.py`): the session index, `val3 index <name>`, `show` and the prompt hook
    mark `<v>_s#1` and not `<v>#1`.
- [ ] **Step 2:** Run: FAIL.
- [ ] **Step 3:** Implement as in Interfaces.
- [ ] **Step 4:** New tests and whole suite: PASS.
- [ ] **Step 5:** Commit `feat: specialized vaults written from one project`.

### Task 4: `vault create --specialized`

**Files:**
- Modify: `src/val3/cli.py` (`vault create` options), `src/val3/init.py` (reuse `_write`, `_inside`; new
  `add_specialized(root: Path, name: str) -> str | None`)
- Test: `tests/test_specialized.py`

**Interfaces:**
- Consumes: Task 2 note writing, Task 3 `Project.specialized`.
- Produces: `val3 vault create NAME --specialized [--description D] [--service OWNER]`, run inside a project:
  creates the vault (with `OWNER` when given, else the project's service), writes the note with `parent` = home,
  and when the `OWNER` role differs from the project service's `current_user`, grants that role write
  (`db.grant(..., write=True)`). Then `add_specialized` edits `val3.toml`: inside `[vaults]`, replace a line
  matching `^specialized\s*=\s*\[.*\]\s*$` with the new list, or insert one after the `home = …` line. Returns
  `None` on success or the line to add when `[vaults]` has no single-line `home` or a multi-line `specialized`.
  `add_specialized` returns `None` when it edited the file and the line to add when it did not. Output: `created
  vault NAME`, then `val3.toml: specialized = [...]` on success, or the warning `val3: warning: val3.toml not
  changed; add under [vaults]: specialized = [...]` (exit 0). The grant goes to the **project's** role (the
  `current_user` of the project's service), not to `OWNER`. The edited file
  must load with `load_project` before it replaces the old one (same temp-file check as `init._config`).

- [ ] **Step 1: tests:**
  - `test_create_specialized_edits_the_toml`: after it, `load_project` sees it (`status` prints a
    `specialized vault` line); `vault list` shows `parent <home>`.
  - `test_create_specialized_with_owner_service_grants_the_project_role`: the project's service is
    `val3writer` (role `env.writer`, granted write on home), `--service val3test`; afterwards `save --vault`
    from the project succeeds.
  - `test_unusual_toml_is_left_alone_with_the_line_to_add`: `val3.toml` with `[vaults]\nlinked = []\nhome =
    'v'` style quoting and a multi-line `specialized = [\n "a",\n]`: file bytes unchanged, warning printed,
    exit 0, vault created.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS + suite.
- [ ] **Step 5:** Commit `feat: create a specialized vault from its project`.

### Task 5: Capture routes candidates to own vaults

**Files:**
- Modify: `src/val3/candidate.py` (`"vault"` optional, a vault name), `src/val3/extract.py` (routing),
  `src/val3/extractor_prompt.md` (a `{VAULTS}` paragraph), `src/val3/inbox.py` only if needed for refs
- Test: `tests/test_extract.py` or new `tests/test_capture_routing.py`

**Interfaces:**
- Consumes: Task 3 `Project.writing`, Task 2 `catalogue.notes`.
- Produces:
  - In `extract.extract`, per slice: read each candidate's `"vault"` (default home); a vault not in
    `project.own` counts as `rejected` and logs `invalid` in the home vault's event log; otherwise strip the key
    and group by target. For each group: `compare(conn, project.writing(t), …)`, `submit(conn,
    project.writing(t), …)`. Note refs use `current(conn, <ref's vault>, ref)`. At the end, `store_vectors` for
    each own vault.
  - `candidate.FORMAT` does not change (it is part of `extract.PROMPT`); `validate` accepts the optional
    `"vault"` key.
  - Prompt: when `project.specialized` is empty, the material and prompt are byte-identical to today (and
    `PROMPT_VERSION` stays 5). Otherwise the material gains one section, `# Vaults of this project`, one line
    per own vault `- NAME: DESCRIPTION` (no description: `(no description)`), and the line `Give "vault" only
    for a candidate that belongs to a specialized vault; leave it out for the home vault.`

- [ ] **Step 1: tests:**
  - `test_one_slice_routes_candidates_to_two_vaults`: fake extractor answers two facts, one with `"vault":
    "<v>_s"`; after `val3 extract`, one entry in each vault; with an existing close entry in `<v>_s` (fake
    embedder), the second extractor call (settle prompt) is made for the routed candidate and names `<v>_s#…`.
  - `test_candidate_for_a_linked_vault_is_rejected`: `"vault": <linked>` → `rejected 1`, no row anywhere.
  - `test_prompt_unchanged_without_specialized`: for a project without `specialized`, the recorded system prompt
    has no `"vault"` and the material no `# Vaults of this project`; with one, the material has the section and
    the description.
  - `test_explicit_home_vault_and_note_refs`: `"vault": <home>` admits into home; an inbox note whose candidate
    goes to `<v>_s` is annotated `Recorded in VAL3 as <v>_s#1`; with `[embedding]`, the routed entry has a
    vector.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS + suite.
- [ ] **Step 5:** Commit `feat: capture routes candidates to specialized vaults`.

### Task 6: Ad hoc reads

**Files:**
- Modify: `src/val3/cli.py` (`show`, `recall`, `index`, `vault list`), `src/val3/views.py` (session index line)
- Test: `tests/test_adhoc_read.py`

**Interfaces:**
- `val3 show REF [--service S]`: any vault (no `_declared` check); marks only for own vaults of the project
  when there is a project and no `--service`.
- `val3 recall TERMS… [--vault V]… [--service S]`: with `--vault`, search exactly those vaults
  (`dataclasses.replace(project, home=V1, specialized=(), linked=rest)` for the read); with `--service` and no
  project, `--vault` is required (usage error, exit 2).
- `val3 index [NAME | --none] [--vault V] [--service S]` for listing; `index merge` refuses `--service`
  (usage error).
- A vault missing or not readable: `val3: error: cannot read vault V (val3 vault grant V <role> --read)`,
  exit 1; `<role>` is the connection's `current_user`.
- Session index: when the database holds a readable vault not in `project.vaults` (and not `val3_*`), the
  header gains the line `Other vaults on this server: val3 vault list`. The hook uses one existence query
  (`catalogue.has_other(conn, names) -> bool`), never entry counts.

- [ ] **Step 1: tests:** `test_show_an_unlinked_vault`, `test_recall_with_vault_searches_only_it`,
  `test_missing_grant_names_the_grant` (as `env.reader` with no grant), `test_service_reads_another_database`
  (second database via `helpers.add_service`, `CREATE DATABASE` through `env.sql`),
  `test_index_line_only_with_a_foreign_vault` (session-start output without, then with, an extra vault).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS + suite.
- [ ] **Step 5:** Commit `feat: read any readable vault on demand`.

### Task 7: Dump and mirror

**Files:**
- Create: `src/val3/mirror.py`
- Modify: `src/val3/cli.py` (`vault dump`, `vault mirror`), `src/val3/status.py` (mirror age on linked
  vaults), `src/val3/writer.py` (a `mirror_guard` refusal raised by PostgreSQL becomes `Val3Error("vault V is a
  mirror and read only")`)
- Test: `tests/test_mirror.py`

**Interfaces:**
- Consumes: Task 1 `PARTS`, `vault_tables`, `columns`, `create_schema`, `CATALOGUE_VERSION`; Task 2
  `mirror_sync`, `mirror_guard`, identity, `catalogue.is_mirror`.
- Produces:
  - Both ends set `client_encoding = 'UTF8'`, `DateStyle = 'ISO'`, `TimeZone = 'UTC'`, `IntervalStyle =
    'postgres'` and `extra_float_digits = 3` before any COPY.
  - `mirror.dump(conn, vault: str, out: BinaryIO) -> dict[str, int]` (row counts). Stream: line 1 JSON
    `{"format": "val3-vault-dump", "version": 1, "catalogue": CATALOGUE_VERSION, "vault", "vault_id",
    "system_identifier", "dumped_at", "tables": {table: [columns]}}`; then per table a JSON line
    `{"table": t}` followed by `COPY (SELECT cols FROM v.t ORDER BY 1) TO STDOUT` text and a line `\.`; last a
    JSON line `{"end": {table: rows}}`. All COPYs in one `REPEATABLE READ READ ONLY` transaction. Refuses a vault
    that is not `is_current`.
  - `mirror.load(conn, vault: str, source: str, stream: BinaryIO) -> dict[str, int]`: the checks, staging load
    and publish of design §4.4, in that order. Staging name `val3_load_<8 hex>`, old name `val3_old_<8 hex>`.
    - The whole operation holds `pg_advisory_lock(hashtext('val3-mirror:' || V))` (session level, released in
      `finally`): two refreshes of one vault run one after the other.
    - Before publishing, refuse when any object outside the old mirror's schema depends on it (`pg_depend`
      joined to `pg_class`/`pg_namespace`), and check continuity on **every** catalogue table: each primary key
      of the old mirror exists in staging, and `entry.content_hash` is equal for every old id. A failure drops
      staging and leaves the old mirror.
    - Publish transaction: `SET LOCAL lock_timeout = '500ms'`; rename old to `val3_old_*`, staging to `V`,
      grants, `mirror_sync` row. Retried up to 5 times with 1 s between attempts outside the transaction, then
      `Val3Error("vault V is busy: try again")` and staging dropped. Readers resolve `V` by name per statement,
      so the rename never waits for them.
    - Then, in its own transaction with the same lock timeout, drop the old schema's catalogue tables in
      reverse order (`DROP TABLE … RESTRICT`) and the schema (`RESTRICT`). If it cannot get the locks, the old
      schema stays as `val3_old_*` and the next `vault mirror` of any vault drops leftover `val3_old_*` schemas
      first. Never `CASCADE`.
  - CLI `val3 vault dump NAME --file F [--service S]` (`-` stdout; a file is created private with
    `config.create_private` and never overwritten) → stderr `dumped vault NAME: <n> entries`.
  - CLI `val3 vault mirror NAME (--from-file F | --from-service S) [--service LOCAL]` → `mirrored vault NAME
    from SOURCE: <n> entries (<k> new)`. `SOURCE` is the service name or the file path (`stdin` for `-`).
  - `val3 status`: for a linked vault that is a mirror, `linked:` shows `NAME (mirror, synced <age>)`.

- [ ] **Step 1: tests** in `tests/test_mirror.py` (a second database `val3b` with `vault create` through a
  `val3b` service from `helpers.add_service`; entries saved there, then dumped and mirrored into `val3`):
  - `test_mirror_has_the_same_refs_and_content`: `show <v>#1` gives the same text on both sides; `vault list`
    shows `read`/`write` and `mirror of val3b`.
  - `test_mirror_cannot_be_written`: `save` from a project whose home is the mirror exits 1 with `is a mirror
    and read only`; a direct `INSERT` through `env.sql` raises.
  - `test_refresh_adds_rows_and_keeps_grants`: grant `env.reader` read, add an entry at the source, mirror
    again: `(1 new)`, the reader still reads.
  - `test_truncated_dump_leaves_the_mirror_intact`: dump to a file, cut the last 100 bytes, `--from-file`:
    exit 1, mirror unchanged (row counts).
  - `test_older_dump_and_other_identity_are_refused`: mirror from a newer dump, then the older file: exit 1
    `older`; a dump of another vault of the same name from a third database: exit 1 `another vault`.
  - `test_local_vault_is_never_replaced`: `vault mirror` onto a non-mirror local vault exits 1.
  - `test_refresh_while_a_reader_holds_the_old_mirror`: a psycopg connection in the test (not `val3`) opens a
    transaction and reads `<mirror>.entry`, and stays open; `vault mirror` (subprocess) then succeeds; a hook
    `prompt` run right after returns the new entry in its context (assert on the output, not only the exit
    code); after the reader commits, the next `vault mirror` leaves no `val3_old_*` schema.
  - `test_external_dependency_refuses_the_refresh`: `CREATE VIEW public.x AS SELECT * FROM <mirror>.entry`;
    `vault mirror` exits 1 naming the dependency; the mirror is unchanged.
  - `test_concurrent_refreshes_publish_in_order`: two `vault mirror` subprocesses started together from an older
    and a newer dump: the final mirror has the newer dump's rows (the older one is refused once the newer one
    published).
  - `test_divergent_dump_is_refused`: a dump whose entry #1 has another `content_hash` (edit the dump file's
    entry COPY row) with the same counts: exit 1, mirror unchanged.
  - `test_dump_through_stdout_and_stdin`: `vault dump --file -` piped into `vault mirror --from-file -`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS + suite.
- [ ] **Step 5:** Commit `feat: read-only mirrors of vaults from another database`.

### Task 8: Documents

**Files:**
- Create: `docs/adr/0010-vault-sharing.md` (context, decision, consequences; points to the design)
- Modify: `CONTEXT.md` (Vault, **Own vault**, **Specialized vault**, **Vault note**, **Mirror**, **Vault
  identity**), `README.md` (configuration, commands, an operations section "Vaults on another host" with the
  `ssh … dump | mirror` line and a cron example), `docs/design/2026-10-08-val3-design.md` §7 one line pointing to
  the new design, `src/val3/init.py` CLAUDE.md block (one sentence: `val3 vault list` shows the server's vaults;
  `val3 save --vault` writes a specialized one), `TODO.md`, `SESSION.md`
- Test: `tests/test_init.py` expectations of the block, if it asserts the text

- [ ] **Step 1:** Write the documents. **Step 2:** whole suite PASS. **Step 3:** Commit `docs: vault sharing`.
