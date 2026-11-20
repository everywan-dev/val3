"""Connections and vault schema management (DDL and grants only; data goes through the writer)."""
from dataclasses import dataclass
from importlib.resources import files

import psycopg
from psycopg import sql

from val3 import Val3Error
from val3.config import check_vault_name
from val3.text import _one_line, safe


def connect(service: str, timeout: int = 10, statement_timeout_ms: int | None = None) -> psycopg.Connection:
    options = {"options": f"-c statement_timeout={statement_timeout_ms}"} if statement_timeout_ms else {}
    return psycopg.connect(service=service, connect_timeout=timeout, autocommit=True, **options)


def vsql(vault: str, query: str) -> sql.Composed:
    """A query against one vault: `{v}` becomes the quoted schema name."""
    return sql.SQL(query).format(v=sql.Identifier(check_vault_name(vault)))


CATALOGUE_VERSION = 3  # goes up with every change to PARTS


@dataclass(frozen=True)
class Part:
    """One DDL file of a vault and what `val3 vault upgrade` does for a vault created without it or before it
    changed."""
    file: str
    tables: tuple[str, ...]  # foreign-key order
    probe: str | None = None  # the table a vault lacks until upgrade creates the part; None: every vault has it
    exempt: str | None = None  # table upgrade fills with the entries already there: exempt from the part (D2a)
    columns: tuple[tuple[str, str, str], ...] = ()  # (table, column, type) added to a vault created before them
    checks: tuple[tuple[str, str, str, str], ...] = ()  # (table, constraint, condition, value it must hold): widened
    seed: str | None = None  # SQL ({v}: the vault) create and upgrade run once after creating the part; never
                             # create_schema, so a staging schema starts empty


PARTS = (
    Part("vault.sql",
         ("entry", "supersession", "knowledge_index", "entry_index", "evidence", "tool", "recipe_parameter",
          "recipe_step", "step_fact", "recipe_example", "review_item", "review_decision", "event"),
         checks=(("evidence", "evidence_kind_check", "kind IN ('query', 'command', 'api', 'test', 'user', 'file')",
                  "'file'"),
                 ("review_item", "review_item_reason_check",
                  "reason IN ('recipe', 'tool', 'user-confirmation', 'supersession', 'recipe-cites-superseded', "
                  "'import', 'withdrawal', 'withdrawn', 'no-evidence')",
                  "'no-evidence'"),
                 ("review_decision", "review_decision_decision_check",
                  "decision IN ('approved', 'rejected', 'retired')", "'retired'"))),
    Part("embedding.sql", ("embedding",), probe="embedding"),
    Part("withdrawal.sql", ("withdrawal",), probe="withdrawal", columns=(("withdrawal", "evidence", "jsonb"),),
         checks=(("withdrawal", "withdrawal_cause_check", "cause IN ('wrong', 'obsolete', 'duplicate')",
                  "'duplicate'"),)),
    Part("index_alias.sql", ("index_alias",), probe="index_alias"),
    Part("neighbour.sql", ("neighbour_exempt", "neighbour", "settlement"), probe="neighbour",
         exempt="neighbour_exempt"),
    Part("identity.sql", ("identity",), probe="identity",
         seed="INSERT INTO {v}.identity (vault_id) VALUES (gen_random_uuid())"),
    Part("attestation.sql", ("attestation",), probe="attestation"),
)


def vault_tables() -> tuple[str, ...]:
    """Every table of a current vault, in foreign-key order."""
    return tuple(t for part in PARTS for t in part.tables)


def columns(conn, vault: str, table: str) -> list[str]:
    """The table's non-generated columns in ordinal order."""
    return [name for (name,) in conn.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s AND is_generated = 'NEVER'
        ORDER BY ordinal_position""", (vault, table))]


def _has_table(conn, vault: str, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s)", (f"{vault}.{table}",)).fetchone()[0] is not None


def _has_part(conn, vault: str, file: str) -> bool:
    return _has_table(conn, vault, next(p.probe for p in PARTS if p.file == file))


def has_withdrawals(conn, vault: str) -> bool:
    """A vault created before withdrawals lacks the table until `val3 vault upgrade`; it has withdrawn nothing."""
    return _has_part(conn, vault, "withdrawal.sql")


def has_neighbours(conn, vault: str) -> bool:
    """A vault created before the neighbour check lacks its tables until `val3 vault upgrade`; it pairs nothing."""
    return _has_part(conn, vault, "neighbour.sql")


def upgraded(conn, vault: str) -> bool:
    """ADR 0012 §8: a vault with the attestation table runs without a review queue; one without it, as before."""
    return _has_part(conn, vault, "attestation.sql")


RANK = {"unverified": 0, "attested": 1, "evidence": 2}


def backing(alias: str = "e") -> str:
    """SQL expression, for an upgraded vault: entry `alias`'s backing (ADR 0012 §1), 'evidence', 'attested' or
    'unverified'. The text holds `{v}` for vsql."""
    return ("CASE WHEN EXISTS (SELECT 1 FROM {v}.evidence b WHERE b.entry_id = ALIAS.id) THEN 'evidence'"
            " WHEN EXISTS (SELECT 1 FROM {v}.attestation b WHERE b.entry_id = ALIAS.id) THEN 'attested'"
            " ELSE 'unverified' END").replace("ALIAS", alias)


def backings(conn, vault: str, ids) -> dict[int, str]:
    """The one answer to an entry's backing (ADR 0012 §1): id -> 'evidence', 'attested', 'unverified', or
    'not readable' when this role cannot read evidence or attestation; {} on a vault not upgraded."""
    ids = list(ids)
    if not ids or not upgraded(conn, vault):
        return {}
    if not conn.execute("SELECT has_table_privilege(%s, 'SELECT') AND has_table_privilege(%s, 'SELECT')",
                        (f'"{vault}".evidence', f'"{vault}".attestation')).fetchone()[0]:
        return dict.fromkeys(ids, "not readable")
    return dict(conn.execute(vsql(vault, f"SELECT e.id, {backing()} FROM {{v}}.entry e WHERE e.id = ANY(%s)"),
                             (ids,)).fetchall())


_BACKED = ("(EXISTS (SELECT 1 FROM {v}.evidence b WHERE b.entry_id = ID)"
           " OR EXISTS (SELECT 1 FROM {v}.attestation b WHERE b.entry_id = ID))")
# as of the parameter %(as_of)s (null: now), as `live` reads liveness (ADR 0012, amendment 1, §7)
_THEN = " AND (%(as_of)s::timestamptz IS NULL OR b.created_at <= %(as_of)s)"
_BACKED_THEN = ("(EXISTS (SELECT 1 FROM {v}.evidence b WHERE b.entry_id = ID" + _THEN + ")"
                # the upgrade's attestation stands for a person's admission older than it: it counts at any time
                " OR EXISTS (SELECT 1 FROM {v}.attestation b WHERE b.entry_id = ID"
                " AND (b.how = 'before ADR 0012' OR %(as_of)s::timestamptz IS NULL OR b.created_at <= %(as_of)s)))")
_CITED = ("(SELECT f.fact_id FROM {v}.step_fact f WHERE f.recipe_id = ALIAS.id UNION ALL"
          " SELECT s.tool_id FROM {v}.recipe_step s WHERE s.recipe_id = ALIAS.id AND s.tool_id IS NOT NULL) c (id)")


def delivered(conn, vault: str, alias: str = "e") -> str:
    """SQL condition: entry `alias` may be delivered automatically (ADR 0012 §3): it has evidence or attestation, and
    no step of it cites an entry with neither, both as of %(as_of)s. TRUE on a vault not upgraded. The text holds
    `{v}` for vsql."""
    if not upgraded(conn, vault):
        return "TRUE"
    return (_BACKED_THEN.replace("ID", "ALIAS.id") + f" AND NOT EXISTS (SELECT 1 FROM {_CITED} WHERE NOT "
            + _BACKED_THEN.replace("ID", "c.id") + ")").replace("ALIAS", alias)


def citing_unverified(conn, vault: str, ids) -> set[int]:
    """The recipes among `ids` a step of which cites an unverified entry (ADR 0012 §3); none on a vault not upgraded."""
    ids = list(ids)
    if not ids or not upgraded(conn, vault):
        return set()
    return {entry_id for (entry_id,) in conn.execute(vsql(vault, f"""
        SELECT e.id FROM {{v}}.entry e WHERE e.id = ANY(%s) AND e.kind = 'recipe'
          AND EXISTS (SELECT 1 FROM {_CITED.replace("ALIAS", "e")} WHERE NOT {_BACKED.replace("ID", "c.id")})"""),
        (ids,))}


def marks(conn, vault: str, ids) -> dict[int, str]:
    """The mark after an entry's line in an explicit lookup (ADR 0012 §3): unverified, a recipe that cites an
    unverified entry, or backing not readable; nothing for evidence, attestation and a vault not upgraded."""
    found = backings(conn, vault, ids)
    if "not readable" in found.values():
        return dict.fromkeys(found, " · backing not readable")
    cites = citing_unverified(conn, vault, ids)
    return {entry_id: " · unverified" if b == "unverified" else " · cites unverified"
            for entry_id, b in found.items() if b == "unverified" or entry_id in cites}


def writer_lock(conn, vault: str) -> None:
    """Writer transactions of one vault, and its upgrade, run one at a time; the lock ends with the transaction."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"val3 writer {vault}",))


def live(conn, vault: str, alias: str = "e", delivery: bool = False) -> str:
    """SQL condition: entry `alias` is live as of the parameter %(as_of)s (null: now), that is, no supersession and
    no withdrawal of it recorded by then (design §4.1); with `delivery`, also `delivered`. Every reader uses it (the
    writer's `_require` keeps its own probes, to name the cause in its message); the text holds `{v}` for vsql."""
    condition = ("NOT EXISTS (SELECT 1 FROM {v}.supersession s WHERE s.old_id = ALIAS.id"
                 " AND (%(as_of)s::timestamptz IS NULL OR s.created_at <= %(as_of)s))")
    if has_withdrawals(conn, vault):
        condition += (" AND NOT EXISTS (SELECT 1 FROM {v}.withdrawal w WHERE w.entry_id = ALIAS.id"
                      " AND (%(as_of)s::timestamptz IS NULL OR w.created_at <= %(as_of)s))")
    if delivery:
        condition += " AND " + delivered(conn, vault, "ALIAS")
    return condition.replace("ALIAS", alias)


def _exists(conn, vault: str) -> bool:
    return conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (vault,)).fetchone() is not None


def _run_file(conn, name: str) -> None:
    conn.execute(files("val3").joinpath(name).read_text(encoding="utf-8"))


def _vault_path(conn, name: str) -> None:
    """Vault DDL runs with the vault first and public (where the extensions live) after it."""
    conn.execute(sql.SQL("SET LOCAL search_path TO {}, public").format(sql.Identifier(name)))


def _check_common(conn, mirror: bool = False) -> None:
    """A vault needs what common.sql creates; a shared schema left by an older version names the fix. Creating a
    vault needs only what its DDL uses; a mirror also needs the catalogue tables and its guard."""
    functions = ["val3_common.forbid_change()", "val3_common.query_lexemes(regconfig,text)",
                 "val3_common.or_query(text[])"] + (["val3_common.mirror_guard()"] if mirror else [])
    tables = ["val3_common.vault_note", "val3_common.mirror_sync"] if mirror else []
    missing = [name for (name,) in conn.execute("""
        SELECT x FROM unnest(ARRAY['vector', 'unaccent']) AS x
        WHERE NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = x)
        UNION ALL
        SELECT f FROM unnest(%s::text[]) AS f WHERE to_regprocedure(f) IS NULL
        UNION ALL
        SELECT t FROM unnest(%s::text[]) AS t WHERE to_regclass(t) IS NULL
        UNION ALL
        SELECT 'val3_common.' || c FROM unnest(ARRAY['en', 'es']) AS c
        WHERE NOT EXISTS (SELECT 1 FROM pg_ts_config
                          WHERE cfgname = c AND cfgnamespace = 'val3_common'::regnamespace)""", (functions, tables))]
    if missing:
        raise Val3Error(f"the shared schema val3_common is from an older version (missing {', '.join(missing)}): "
                        "its owner runs `val3 vault upgrade <vault>` on an existing vault first")


def create_vault(conn, name: str) -> None:
    check_vault_name(name)
    if name.startswith("val3_"):
        raise Val3Error(f"invalid vault name {name!r}: the val3_ prefix is reserved")
    with conn.transaction():
        if _exists(conn, name):
            raise Val3Error(f"vault {name} already exists")
        if _exists(conn, "val3_common"):
            _check_common(conn)  # owned by whoever created it; vault upgrade (its owner) refreshes it
        else:
            _run_file(conn, "common.sql")
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        create_schema(conn, name)
        for part in PARTS:
            if part.seed:
                conn.execute(vsql(name, part.seed))


def create_schema(conn, name: str) -> None:
    """The DDL of every part into the existing empty schema `name`, inside the caller's transaction."""
    _vault_path(conn, name)
    for part in PARTS:
        _run_file(conn, part.file)


def _stale_checks(conn, name: str) -> list[tuple[str, str, str]]:
    """The widened checks a vault still holds in an older form: (table, constraint, condition)."""
    stale = []
    for table, constraint, condition, value in (check for part in PARTS for check in part.checks):
        current = conn.execute("""
            SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = to_regclass(%s) AND conname = %s""", (f"{name}.{table}", constraint)).fetchone()
        if current is None or value not in current[0]:
            stale.append((table, constraint, condition))
    return stale


def _missing_columns(conn, name: str) -> list[tuple[str, str, str]]:
    """The added columns a vault still lacks: (table, column, type)."""
    return [(table, column, kind) for part in PARTS for table, column, kind in part.columns
            if conn.execute("""
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s AND column_name = %s""",
                            (name, table, column)).fetchone() is None]


def is_current(conn, name: str) -> bool:
    """An existing vault needs nothing from `val3 vault upgrade`: the same probes `upgrade_vault` makes."""
    return (all(_has_table(conn, name, part.probe) for part in PARTS if part.probe)
            and not _missing_columns(conn, name) and not _stale_checks(conn, name))


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


NOTICES = ("supersession", "recipe-cites-superseded", "withdrawn")  # review items of an operation already applied
UPGRADER = "val3 upgrade"


def upgrade_vault(conn, name: str, dry_run: bool = False) -> tuple[list[str], list[str]]:
    """Bring a vault created by an earlier version to the current schema: (schema changes, ADR 0012 changes), both
    empty when nothing changed. With `dry_run` the same work is done and rolled back.

    Each step runs only when the vault lacks it. Roles keep on new tables the SELECT and INSERT privileges
    they hold on `entry`. Creating the attestation table is the switch of ADR 0012 §8: in the same transaction,
    under the writer's lock, every entry without evidence is attested and every undecided review item closed."""
    check_vault_name(name)
    schema, switch = [], []
    with conn.transaction() as tx:
        if not _exists(conn, name):
            raise Val3Error(f"no vault {name}")
        writer_lock(conn, name)  # first: a writer holds it before touching the tables this DDL locks
        _run_file(conn, "common.sql")
        switching = not upgraded(conn, name)
        for part in PARTS:
            if part.probe and not _has_table(conn, name, part.probe):
                _vault_path(conn, name)
                _run_file(conn, part.file)
                for table in part.tables:
                    _copy_grants(conn, name, table)
                if part.seed:
                    conn.execute(vsql(name, part.seed))
                if part.exempt:
                    conn.execute(sql.SQL("INSERT INTO {} (entry_id) SELECT id FROM {}")
                                 .format(sql.Identifier(name, part.exempt), sql.Identifier(name, "entry")))
                schema.append(f"create table {', '.join(part.tables)}")
        for table, column, kind in _missing_columns(conn, name):
            conn.execute(sql.SQL("ALTER TABLE {} ADD COLUMN {} {}")
                         .format(sql.Identifier(name, table), sql.Identifier(column), sql.SQL(kind)))
            schema.append(f"add column {table}.{column}")
        for table, constraint, condition in _stale_checks(conn, name):
            conn.execute(sql.SQL("ALTER TABLE {t} DROP CONSTRAINT IF EXISTS {c}, ADD CONSTRAINT {c} CHECK ({x})")
                         .format(t=sql.Identifier(name, table), c=sql.Identifier(constraint),
                                 x=sql.SQL(condition)))
            schema.append(f"widen check {table}.{constraint}")
        if switching:
            switch = _switch(conn, name)
        if dry_run:
            raise psycopg.Rollback(tx)
    return schema, switch


def _switch(conn, name: str) -> list[str]:
    """ADR 0012 §8, steps 1 and 3: attest each entry without evidence (each was admitted by a person's decision)
    and close each undecided review item as a mechanical actor: a notice approved (its operation is applied; this
    attests nothing), a candidate or a withdrawal proposal retired. Nothing is admitted or withdrawn."""
    lines = []
    for entry_id, kind, title in conn.execute(vsql(name, """
            SELECT e.id, e.kind, e.title FROM {v}.entry e
            WHERE NOT EXISTS (SELECT 1 FROM {v}.evidence x WHERE x.entry_id = e.id) ORDER BY e.id""")).fetchall():
        conn.execute(vsql(name, "INSERT INTO {v}.attestation (entry_id, attested_by, how) VALUES (%s, %s, %s)"),
                     (entry_id, UPGRADER, "before ADR 0012"))
        lines.append(f"attest {name}#{entry_id} [{kind}] {title} (no evidence, admitted before ADR 0012)")
    for review_id, reason, payload in conn.execute(vsql(name, """
            SELECT r.id, r.reason, r.payload FROM {v}.review_item r
            WHERE NOT EXISTS (SELECT 1 FROM {v}.review_decision d WHERE d.review_id = r.id)
            ORDER BY r.id""")).fetchall():
        notice = reason in NOTICES
        conn.execute(vsql(name, "INSERT INTO {v}.review_decision (review_id, decision, decided_by) "
                                "VALUES (%s, %s, %s)"), (review_id, "approved" if notice else "retired", UPGRADER))
        what = str(payload.get("title"))
        if reason not in NOTICES + ("withdrawal",):
            what = f"[{payload.get('kind')}] {what}"
        if reason == "import":
            what += f" (batch {(payload.get('import') or {}).get('batch')})"
        lines.append(f"{'approve notice' if notice else 'retire'} review #{review_id} ({reason}): {what}")
    return [safe(_one_line(line)) for line in lines]


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
