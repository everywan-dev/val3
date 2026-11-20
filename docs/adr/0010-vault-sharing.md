# ADR 0010 — Vault sharing: specialized vaults, vault notes, ad hoc reads and mirrors

Date: 2026-11-07. Status: accepted (2026-11-07). Implements `docs/design/2026-11-07-vault-sharing-design.md`.

## Context

A project had one home vault (written) and linked vaults (read), all in the database its `service` names.
Writing to a second vault meant a second project directory and a `cd` to save; the extractor never routed
anything there. Nothing described a vault, so a session found another project's vault only if told its name,
and `val3 show` and `val3 recall` refused any vault that was not home or linked. A vault on another host could
not be read at all: the hosts have no route to each other's PostgreSQL. In the code, what a vault is made of
was spread over several table lists, and the rule "only the home vault is written" was repeated per command.

## Decision

- Specialized vaults: `[vaults] specialized = [...]` in `val3.toml`. The own vaults are the home vault and the
  specialized ones; a project writes only to its own vaults. `save`, `tool add`, `review`, `embed` and
  `index merge` take `--vault V` (default home); `settle`, `withdraw <ref>` and `confirm` write to the ref's
  vault; `import` and `withdraw --batch` stay on the home vault. Capture lists the own vaults with their
  descriptions and a candidate may name its `vault`; the target is resolved before the neighbour check. Marks
  are computed for every own vault. `val3 vault create V --specialized` creates the vault, records its parent
  and description, and adds it to `val3.toml`.
- Vault notes: an append-only `val3_common.vault_note` (vault, parent, description, writer, time); only a role
  that can write a vault describes it (row-level security). `val3 vault describe` appends one; `val3 vault list`
  lists the database's vaults with the role's privilege, parent, description and writer, and a mirror's source
  and last refresh. The session index adds one fixed line when the database holds vaults that are not own or
  linked.
- Ad hoc reads: `val3 show` reads any vault the role can read; `val3 recall --vault` and `val3 index --vault`
  read a given vault; `--service` on these reads and on `vault list` reads another database. No project-level
  writing command (`save`, `settle`, `confirm`, `withdraw`, `review`, `tool add`, `embed`, `index merge`) takes
  `--service`.
- Mirrors: every vault has a fixed identity. `val3 vault dump` writes a vault from one read-only snapshot with
  a counted trailer; `val3 vault mirror` loads it into a staging schema, checks identity, catalogue version,
  columns and that every row of the old mirror is there with the same value in every non-generated column,
  then publishes it by a rename in one short transaction and grants again exactly what the old mirror had
  (schema `USAGE`, `SELECT` per table, `PUBLIC` included). A trigger on its tables refuses every insert outside
  a load, so a mirror is read only; a vault is a mirror while its `entry` carries that trigger, and it is never
  listed as writable. No logical replication: the operator runs the pull (by hand or by cron), for example
  over `ssh`. Vault names starting with `val3_` are reserved for val3's own schemas: `vault create` and
  `vault mirror` refuse them, and vaults so named before stay readable.
- Refactor: one catalogue of a vault's parts drives create, upgrade, the currency check, grant copying, dump
  and mirror; one call returns the project as seen from a target vault.

Detail: `docs/design/2026-11-07-vault-sharing-design.md`.

## Consequences

- One project writes to several vaults without a second directory; a project without `specialized`, on a
  database holding only its own and linked vaults, sees no change in output.
- `val3 vault upgrade` must run on a vault before `vault dump` takes it (the source vault of a mirror), and on
  one vault of a database whose `val3_common` is older before `vault mirror` loads into it and before
  `vault list` shows notes there. A plain `vault create` works on such a database; `--description` and
  `--specialized` ask for the upgrade first.
- Sessions never own vaults: the operator creates them, or `vault create --service OWNER` creates with the
  operator's service and grants the project's role write.
- A mirror is as fresh as its last refresh; `val3 status` shows its age. A refresh with fewer rows, another
  identity or a missing old row is refused and the old mirror stays.
- Citations across vaults and their re-check are deferred; a recipe cites only its own vault.
