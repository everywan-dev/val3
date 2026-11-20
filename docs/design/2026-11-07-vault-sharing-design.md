# Vault sharing: specialized vaults, a vault catalogue, ad hoc reads and mirrors

Status: agreed 2026-11-07 after a challenge by Codex (gpt-6.1-sol, high; 12 objections, all taken, see §10).
Owner's request (2026-11-07): review the design and structure, refactor, then let sessions share vaults, relate
them, create several related vaults from one project, read other sessions' vaults directly, and connect vaults
on different hosts (PostgreSQL replicas or occasional dumps). Decision record: ADR 0010.

## 1. What happens today

- A project has one **home** vault (written) and **linked** vaults (read). All of them live in the one database
  its `service` names: every read is one connection, and search loops over the schemas.
- Writing to a second vault means a second project directory. The first adopter that wanted a vault per topic
  created `pitfalls/` under its root, with its own `val3.toml` (home `pitfalls`, linked to its parent),
  and has to `cd` there to save. Its extractor never routes anything there.
- Nothing describes a vault: no purpose, no parent, no list of the vaults a server holds. A session finds
  another project's vault only if someone tells it the name.
- `val3 show <ref>` refuses a vault that is not home or linked, and `val3 recall` searches only those.
- A vault on another host cannot be read at all. The same adopter: "VAL3 only writes to the project's home
  vault, and the other project's is in another database, so I cannot share that vault; I'll create one of my
  own here." That host's PostgreSQL listens on 127.0.0.1 only.
- Structure: what a vault is made of is spread over `db.py`. `create_vault` runs five files; `upgrade_vault`,
  `is_current` and the grant copying each keep their own table list; widened checks, added columns and the
  neighbour exemption are separate branches (architecture review 2026-10-31, candidate "vault capabilities").
  In `cli.py` the rule "only the home vault is written" is repeated as `vault != project.home` checks.

## 2. Goals

- G1. One project writes to several vaults: its home vault and **specialized** vaults, each with a purpose.
- G2. Vaults describe themselves: purpose and parent, readable by any session on the server.
- G3. A session reads any vault it has the privilege to read, on demand, without editing `val3.toml`.
- G4. A vault on another host can be read here, as a read-only **mirror** refreshed now and then from a dump.
- G5. The refactor that these need, and no other: one catalogue of a vault's parts, one explicit target vault
  per writing operation.

## 3. Non-goals and anti-goals

- No writes across hosts. A project writes only to vaults in its own database.
- No PostgreSQL logical replication. It needs `wal_level=logical`, a publication owner, a subscription created
  by a role with `pg_create_subscription` and a network path from the subscriber to the publisher; a failed
  apply stops silently until someone looks. The hosts here have no route to each other's PostgreSQL. Mirrors
  are a pull the operator runs when wanted.
- No citations across vaults. A recipe cites facts and tools of its own vault only, as today (`_require`).
  **Relating** vaults means: a parent and a description (G2), linking for search and delivery, and reading on
  demand (G3). Composing knowledge across vaults (a recipe in one vault citing a fact in another, and its
  re-check) is deferred work.
- No import into a specialized vault: `val3 import` and `withdraw --batch` stay on the home vault.
- Anti-goal: a mirror that the VAL3 writer, or any role other than its owner and superusers, can write.
- Anti-goal: a refresh that blocks the hooks for longer than one short statement, or leaves half a vault.
- Anti-goal: a refresh that gives an existing ref another meaning (another source, or an older dump).
- Anti-goal: an adopter that does nothing new sees any change in its output, its hooks or its timing.

## 4. Design

### 4.1 Specialized vaults (G1)

- `val3.toml` gains an optional list: `[vaults] specialized = ["pitfalls", …]`. **Own** vaults are the home
  vault followed by the specialized ones; a vault appears once across home, specialized and linked.
  `project.vaults` is own vaults then linked ones, so search and delivery read them all.
- **Target vault**: `project.writing(vault)` returns the project as seen from that vault: that vault as home,
  every other own or linked vault as linked. It raises for a vault that is not own (one message: read only
  here, write from the vault's own project). The writer, the neighbour check, re-check, the review list and
  `store_vectors` keep working on `project.home` unchanged; they receive the retargeted project. Reading does
  not go through it.
- Commands that write, and their target:
  - `save`, `tool add`, `review`, `embed`, `index merge`: `--vault V`, default home.
  - `settle`, `withdraw <ref>`, `confirm`: the ref's vault (a supersession is a `save` with `supersedes`).
  - `import`, `withdraw --batch`: home only.
- Capture: the extractor prompt lists the own vaults with their descriptions (§4.2) and a candidate may carry
  `"vault"` (missing: home). The extractor loop **resolves the target first**, before the neighbour check: it
  groups the slice's candidates by target and runs, per group with `project.writing(target)`: the comparison
  with neighbours, `submit`, `current()` for the note references, and at the end `store_vectors` for every own
  vault. A candidate naming a vault that is not own is rejected as invalid (logged as today). With no
  specialized vault the prompt is byte-for-byte unchanged (no new prompt version for those adopters' cache).
- Marks: the session index, the prompt hook, `val3 index`, `val3 show` and `val3 status` compute re-check and
  settle marks for every own vault, keyed by `(vault, id)`. The re-check of `file` quotes keeps one git budget
  across all own vaults.
- `val3 vault create V --specialized --description D [--service OWNER]` inside a project creates the vault,
  records its parent (the home vault) and description, grants the project's role write on it when `OWNER` is
  another role, and adds `V` to `specialized` in `val3.toml`. The file is edited line by line: the
  `specialized` line is replaced, or inserted after the `home` line. A `[vaults]` table without that layout is
  not touched; the command then prints the line to add (exit 0, with the warning). A name `val3.toml` already
  declares (home, specialized or linked) is refused before anything is created (exit 2).
- Vault names starting with `val3_` are reserved: `vault create` and the target of `vault mirror` refuse them
  (exit 1). A vault so named before the reservation stays readable; only `val3_common` and the refresh schemas
  `val3_old_<8 hex>` and `val3_load_<8 hex>` are not vaults.

### 4.2 Vault catalogue (G2)

- New append-only table `val3_common.vault_note (vault, parent, description, created_by, created_at)`;
  `created_by` defaults to `current_user` and cannot be given. The latest row of a vault is its description and
  parent. Description at most 300 characters, one line; parent a vault name.
- Row-level security on `vault_note`: `INSERT … WITH CHECK (has_table_privilege(format('%I.entry', vault),
  'INSERT'))`; SELECT open. Only a role that can write a vault describes it. Owners and superusers are outside
  any RLS guarantee (stated, not prevented).
- `val3 vault describe V --description D [--parent P]` appends a row; `vault create` does it at creation. On a
  database whose `val3_common` has no `vault_note` yet, a plain `vault create` works (it checks only what a
  vault's DDL uses), while `--description` and `--specialized` are refused, creating nothing, with the
  `vault describe` message naming `val3 vault upgrade`.
- `val3 vault list [--service S]` lists the vaults of a database: name, live entries when readable, the role's
  privilege (`write`, `read` or `none`), parent, description with its writer, and for a mirror its source and
  last refresh. A vault is a schema holding `entry` and `supersession`, other than `val3_common` and the refresh
  schemas. A mirror is never `write`, not even for its owner.
- Text written by other roles is data: `vault list` shows it through `safe()` with its writer; the extractor
  sees only the own vaults' descriptions; the session index adds one fixed line, `Other vaults on this server:
  val3 vault list`, only when the database holds a vault that is not own or linked.

### 4.3 Ad hoc reads (G3)

- `val3 show <ref>` reads any vault the role can read.
- `val3 recall --vault V` (repeatable) searches the given vaults instead of the project's; `val3 index --vault
  V` lists that vault's indexes.
- `--service S` on `recall`, `show`, `index` (listing only) and `vault list` reads another database directly,
  when the network and `pg_service.conf` allow it (an SSH tunnel, for instance). No project-level writing
  command (`save`, `settle`, `confirm`, `withdraw`, `review`, `tool add`, `embed`, `index merge`) takes it.
- A missing privilege gives one line naming `val3 vault grant V <role> --read`, exit 1.

### 4.4 Mirrors (G4)

- **Identity.** Every vault gets a fixed identity at creation: table `<vault>.identity (vault_id uuid, created_at)`
  with one row, inserted by `vault create` and `vault upgrade` (the schema DDL inserts no rows, so a staging
  schema starts empty). A mirror keeps its source's identity; refs mean the same thing as long as
  the identity is the same.
- `val3 vault dump V --file F` (`-`: stdout) reads the vault in one `REPEATABLE READ READ ONLY` transaction and
  writes: a JSON header line (format version, vault, identity, the source database's `system_identifier`, time,
  the catalogue version, tables and columns), each table as `COPY … TO STDOUT` text between JSON table lines,
  and a JSON trailer with the row count of each table. The source vault must be current (`is_current`).
- `val3 vault mirror V --from-file F` (`-`: stdin) or `--from-service S` (which runs the dump through that
  service) loads it:
  1. Checks before writing: the header's vault is `V`; the catalogue version and every table's columns equal
     this side's; `val3_common` here has what the vault needs (extensions, both text search configurations,
     the functions); `V` here is absent or a mirror of the same identity; the dump has at least as many rows in
     `entry`, `supersession`, `withdrawal` and `event` as the current mirror (append-only: fewer means an older
     dump). Any failure: exit 1, nothing changed. The whole operation holds a per-vault advisory lock, so two
     refreshes of one vault run one after the other.
  2. Load into a staging schema `val3_load_<random>` in its own transaction: the catalogue's DDL, then a
     `BEFORE INSERT` trigger on every table calling `val3_common.mirror_guard()`, which raises unless
     `val3.loading` is `on` and the role is a member of the schema's owner; `SET LOCAL val3.loading = on`; `COPY … FROM STDIN` per table in catalogue order (COPY
     keeps identity values; generated search columns are rebuilt here). The trailer's counts are checked
     against the rows loaded before commit; a truncated or altered stream rolls back.
  3. Before publishing: no object outside the old mirror depends on it, and every row of the old mirror is in
     staging with every non-generated column equal (`SELECT cols FROM old EXCEPT SELECT cols FROM staging` is
     empty for every table). Otherwise staging is dropped and the old mirror stays.
  4. Publish in one short transaction (`lock_timeout` 500 ms, retried 5 times): record the old mirror's grants
     (schema `USAGE` grantees and each table's `SELECT` grantees, `PUBLIC` included), rename it
     `val3_old_<random>`, rename the staging schema to `V`, grant exactly those again on the same tables (a table
     the old mirror lacked gets the grantees of `entry`; never `INSERT`), append a
     `val3_common.mirror_sync` row (vault, identity, source, row counts, synced_at, synced_by). Readers resolve
     `V` by name per statement, so the rename does not wait for them.
  5. Drop the old schema in its own transaction, table by table and `RESTRICT`, never `CASCADE`. If a reader
     still holds it, it stays as `val3_old_*` and the next `vault mirror` drops it: it sweeps only schemas named
     exactly `val3_old_<8 hex>` whose `entry` carries the guard trigger, and staging schemas `val3_load_<8 hex>`
     no running load holds.
- Readers: a hook sees the old mirror or the new one. Its several autocommit queries may straddle a publish;
  the new mirror holds every row of the old one (append-only, and the row-count check), so a straddling read
  sees at most entries that appeared between its queries, as with any concurrent write today.
- A vault is a mirror while its `entry` table carries a trigger calling `val3_common.mirror_guard()` (not because
  a `mirror_sync` row names it): `vault list`, `status` and the refusal to replace a local vault read that. Its
  last refresh is the latest `mirror_sync` row of its name and identity. `vault grant --write` on a mirror is
  refused.
- Writes: the guard trigger refuses every insert outside a load, the writer included. `val3_common.mirror_sync`
  is writable only by its owner (no grant). The owner and superusers can bypass both: mirrors are loaded by an
  operator role; sessions' roles get `--read` only.
- Embeddings travel with the vault; semantic search uses them only when the local embedding space matches.
- Transport is the operator's choice: `ssh other-host val3 vault dump V --file - --service R | val3 vault mirror
  V --from-file - --service L` (outside a project each side names its service), a file copied by `scp`, or
  `--from-service` through a tunnel; by hand or by cron.
  `val3 status` shows each linked mirror's age.

### 4.5 Refactor (G5)

- R1. **Vault catalogue** in `db.py`: one ordered tuple of parts. Each part names its DDL file, its tables in
  foreign-key order with their columns, the probe that says whether a vault has it, and its upgrade step
  (create the part, copy grants, exempt existing entries, add a column, widen a check). A catalogue version
  number goes up with every change. `create_vault`, `upgrade_vault`, `is_current`, grant copying, `vault dump`
  and `vault mirror` read it. The `has_*` probes stay as thin readers of it. No general migration framework.
- R2. **Target vault**: `Project.own`, `Project.writing(vault)` and `Project.vaults` (own, then linked). The
  `vault != project.home` checks in `cli.py` become one call with one message.

## 5. Privileges

- Creating a vault needs `CREATE` on the database. Login roles of sessions do not get it: they would own their
  vaults and could drop the append-only triggers. `vault create --service OWNER` creates with the operator's
  service (on a host where the session has it) and grants the project's role write; otherwise the operator
  creates it.
- A mirror is owned by the operator role that loads it; readers need `--read` grants, kept across refreshes.

## 6. Compatibility

- `val3 vault upgrade` adds `identity` to each vault and `vault_note`, `mirror_sync` and `mirror_guard()` to
  `val3_common`. Until then `vault list` shows no descriptions and `vault dump`/`vault mirror` refuse naming the
  upgrade.
- A `val3.toml` without `specialized`, on a database holding only its own and linked vaults, gives the same
  output as today.

## 7. Tests (black box, as the suite does)

- Specialized: create (with and without `--service`), `val3.toml` edited, save with `--vault`, index and status
  list both vaults with marks, settle and withdraw in the specialized vault, capture routes a candidate with
  `"vault"` and compares it with neighbours of that vault, a candidate naming a linked vault is rejected,
  `store_vectors` reaches the specialized vault.
- Catalogue: describe, list as a writer, a reader and a role with no grant; RLS refuses a description from a
  role without write; the extra index line appears only with a foreign vault.
- Ad hoc reads: `show` and `recall --vault` on an unlinked vault; a missing grant gives the message.
- Mirror, between two databases of the test server: refs and live counts equal at both ends; a write through
  the writer and a direct INSERT both fail; a refresh keeps grants and adds new rows; a truncated dump, an older
  dump, another identity and a local non-mirror vault are refused with the old mirror intact; a reader during
  the publish is not blocked longer than the lock timeout.
- Refactor: the existing suite passes unchanged.

## 8. Left to the plan

- Exact CLI help text and messages; the dump's JSON field names.

## 9. Deferred

- Citations across vaults and their re-check.
- Logical replication, if hosts ever share a network path and refreshes by hand turn out too rare.

## 10. Challenge (Codex, 2026-11-07)

Taken: (1) sessions never own vaults, operator creates or `--service OWNER`; (2) vault identity, refusal of
another source or an older dump; (3) staging schema and a short publish; (4) one source snapshot and a counted
trailer; (5) target resolved before the neighbour check; (6) marks for every own vault; (7) "relate" defined,
citations across vaults deferred; (8) catalogue version and `val3_common` requirements checked, schema `USAGE`
restored; (9) descriptions bounded and attributed, `mirror_sync` owner-only; (10) a target per command,
`--service` on reads only; (11) R1 holds the upgrade decisions, not only lists; (12) replication privileges
stated correctly. Not taken from (12): restoring `pg_dump` into a separate database reached by another service.
It would make every search span two connections; a mirror keeps one connection and the link semantics.
