"""Dumps and mirrors (design 2026-11-07-vault-sharing-design.md §4.4): a vault of another database is dumped, loaded
here into a staging schema and published by renaming it. Nothing here updates or deletes a vault row: a refresh
replaces the whole schema, and only after checking that the new one holds every row of the old one."""
import json
import secrets
import time
from datetime import datetime, timezone
from typing import BinaryIO

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from val3 import Val3Error, catalogue
from val3.config import check_vault_name
from val3.db import (CATALOGUE_VERSION, _check_common, _exists, _run_file, columns, create_schema, is_current,
                     vault_tables, vsql)

FORMAT, VERSION = "val3-vault-dump", 1
# COPY text depends on these: both ends set them before any COPY
SETTINGS = (("client_encoding", "UTF8"), ("DateStyle", "ISO"), ("TimeZone", "UTC"), ("IntervalStyle", "postgres"),
            ("extra_float_digits", "3"))
APPEND_ONLY = ("entry", "supersession", "withdrawal", "event")  # fewer rows in a dump than here: an older dump
LOCK_TIMEOUT = "500ms"
ATTEMPTS = 5
END = b"\\.\n"
EARLY = "the dump is damaged or ends early"
_LOAD = "val3-load:"  # advisory lock a load holds on its staging schema while it lives


def _settings(conn) -> None:
    for name, value in SETTINGS:
        conn.execute("SELECT set_config(%s, %s, false)", (name, value))


def _line(data: dict) -> bytes:
    return json.dumps(data).encode() + b"\n"


def _fields(names: list[str]) -> sql.Composable:
    return sql.SQL(", ").join(map(sql.Identifier, names))


def dump(conn, vault: str, out: BinaryIO) -> dict[str, int]:
    """Write `vault` to `out` from one snapshot; the row count of each table."""
    check_vault_name(vault)
    _settings(conn)
    conn.isolation_level, conn.read_only = psycopg.IsolationLevel.REPEATABLE_READ, True
    try:
        with conn.transaction():
            if not _exists(conn, vault):
                raise Val3Error(f"no vault {vault}")
            if not is_current(conn, vault):
                raise Val3Error(f"vault {vault} predates this version: its owner runs `val3 vault upgrade {vault}`")
            tables = {table: columns(conn, vault, table) for table in vault_tables()}
            identity = conn.execute(vsql(vault, "SELECT vault_id::text FROM {v}.identity")).fetchone()
            if identity is None:
                raise Val3Error(f"vault {vault} has no identity: its owner runs `val3 vault upgrade {vault}`")
            system = conn.execute("SELECT system_identifier::text FROM pg_control_system()").fetchone()[0]
            out.write(_line({"format": FORMAT, "version": VERSION, "catalogue": CATALOGUE_VERSION, "vault": vault,
                             "vault_id": identity[0], "system_identifier": system,
                             "dumped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "tables": tables}))
            counts = {}
            with conn.cursor() as cur:
                for table, names in tables.items():
                    out.write(_line({"table": table}))
                    with cur.copy(sql.SQL("COPY (SELECT {} FROM {} ORDER BY 1) TO STDOUT")
                                  .format(_fields(names), sql.Identifier(vault, table))) as copy:
                        for data in copy:
                            out.write(data)
                    counts[table] = cur.rowcount
                    out.write(END)
            out.write(_line({"end": counts}))
    finally:
        conn.isolation_level, conn.read_only = None, None
    return counts


def load(conn, vault: str, source: str, stream: BinaryIO) -> dict[str, int]:
    """Load a dump of `vault` and publish it as its mirror here (§4.4 steps 1 to 5); the row count of each table.
    Two refreshes of one vault run one after the other."""
    check_vault_name(vault)
    if vault.startswith("val3_"):
        raise Val3Error(f"invalid vault name {vault!r}: the val3_ prefix is reserved")
    _settings(conn)
    key = f"val3-mirror:{vault}"
    conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (key,))
    try:
        for (name,) in conn.execute(f"""
                SELECT n.nspname FROM pg_namespace n JOIN pg_class e ON e.relnamespace = n.oid AND e.relname = 'entry'
                WHERE n.nspname ~ '^val3_old_[0-9a-f]{{8}}$' AND pg_has_role(n.nspowner, 'MEMBER')
                  AND {catalogue.GUARDED}""").fetchall():
            _drop(conn, name)  # left by a publish while a reader held the old mirror
        for (name,) in conn.execute("""
                SELECT nspname FROM pg_namespace
                WHERE nspname ~ '^val3_load_[0-9a-f]{8}$' AND pg_has_role(nspowner, 'MEMBER')""").fetchall():
            # left by a load that died; a running load of another vault holds its staging lock
            if conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (_LOAD + name,)).fetchone()[0]:
                try:
                    _drop(conn, name)
                finally:
                    conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (_LOAD + name,))
        return _load(conn, vault, source, stream)
    finally:
        if not conn.closed:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))


def _json(line: bytes) -> object:
    try:
        return json.loads(line)
    except (ValueError, RecursionError):
        raise Val3Error(EARLY) from None


def _load(conn, vault: str, source: str, stream: BinaryIO) -> dict[str, int]:
    header = _json(stream.readline())
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise Val3Error("not a val3 vault dump")
    if header.get("version") != VERSION:
        raise Val3Error(f"dump format version {header.get('version')!r}: this val3 reads version {VERSION}")
    if header.get("vault") != vault:
        raise Val3Error(f"the dump holds vault {header.get('vault')!r}, not {vault}")
    if header.get("catalogue") != CATALOGUE_VERSION:
        raise Val3Error(f"the dump has catalogue version {header.get('catalogue')!r} and this side "
                        f"{CATALOGUE_VERSION}: both ends run the same val3 version")
    tables, vault_id = header.get("tables"), header.get("vault_id")
    if not isinstance(tables, dict) or list(tables) != list(vault_tables()) or not isinstance(vault_id, str):
        raise Val3Error("the dump's tables are not those of this val3 version")
    common = _exists(conn, "val3_common")
    if common:
        _check_common(conn, mirror=True)
    old = None
    if _exists(conn, vault):
        if not catalogue.is_mirror(conn, vault):
            raise Val3Error(f"vault {vault} here is not a mirror: a local vault is never replaced")
        here = conn.execute(vsql(vault, "SELECT vault_id::text FROM {v}.identity")).fetchone()
        if here is None or here[0] != vault_id:
            raise Val3Error(f"the dump is of another vault named {vault}: identity {vault_id}, the mirror here "
                            f"has {here and here[0]}")
        old = vault

    staging = f"val3_load_{secrets.token_hex(4)}"
    conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (_LOAD + staging,))
    try:
        return _stage_and_publish(conn, vault, old, staging, tables, vault_id, source, stream)
    finally:
        if not conn.closed:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (_LOAD + staging,))


def _stage_and_publish(conn, vault: str, old: str | None, staging: str, tables: dict, vault_id: str, source: str,
                       stream: BinaryIO) -> dict[str, int]:
    common = _exists(conn, "val3_common")
    with conn.transaction():  # a damaged or truncated stream rolls the staging schema back
        if not common:
            _run_file(conn, "common.sql")
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(staging)))
        create_schema(conn, staging)
        for table in vault_tables():
            if columns(conn, staging, table) != tables[table]:
                raise Val3Error(f"the dump's columns of {table} are not those of this val3 version")
            conn.execute(sql.SQL("CREATE TRIGGER mirror_guard BEFORE INSERT ON {} FOR EACH ROW "
                                 "EXECUTE FUNCTION val3_common.mirror_guard()").format(sql.Identifier(staging, table)))
        conn.execute("SET LOCAL val3.loading = on")
        loaded = _copy_in(conn, staging, tables, stream)
        identity = conn.execute(vsql(staging, "SELECT vault_id::text FROM {v}.identity")).fetchone()
        if identity is None or identity[0] != vault_id:
            raise Val3Error(EARLY)
    try:
        if old:
            _continues(conn, old, staging, loaded)
            _no_dependents(conn, old)
        _publish(conn, vault, old, staging, vault_id, source, loaded)
    except BaseException:
        _drop(conn, staging)
        raise
    return loaded


def _copy_in(conn, staging: str, tables: dict, stream: BinaryIO) -> dict[str, int]:
    counts = {}
    with conn.cursor() as cur:
        for table, names in tables.items():
            if _json(stream.readline()) != {"table": table}:
                raise Val3Error(EARLY)
            with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN")
                          .format(sql.Identifier(staging, table), _fields(names))) as copy:
                while (line := stream.readline()) != END:
                    if not line.endswith(b"\n"):
                        raise Val3Error(EARLY)
                    copy.write(line)
            counts[table] = cur.rowcount
    if _json(stream.readline()) != {"end": counts} or stream.read(1):
        raise Val3Error(EARLY)
    return counts


def _table(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'  # for to_regclass: both are val3 names (lowercase letters, digits, _)


def _continues(conn, old: str, staging: str, loaded: dict[str, int]) -> None:
    """Staging holds every row of the old mirror: append-only tables are not shorter, and every row of every table
    is in staging with each of its non-generated columns equal."""
    for table in APPEND_ONLY:
        if conn.execute("SELECT to_regclass(%s)", (_table(old, table),)).fetchone()[0] is not None:
            here = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(old, table))).fetchone()[0]
            if loaded[table] < here:
                raise Val3Error(f"the dump of vault {old} is older than its mirror here ({table}: {loaded[table]} "
                                f"rows in the dump, {here} here)")
    for table in vault_tables():
        names = columns(conn, old, table)  # none: a table the old mirror lacks
        if names and conn.execute(sql.SQL("SELECT EXISTS ({f} EXCEPT {g})").format(
                f=sql.SQL("SELECT {} FROM {}").format(_fields(names), sql.Identifier(old, table)),
                g=sql.SQL("SELECT {} FROM {}").format(_fields(names), sql.Identifier(staging, table)))).fetchone()[0]:
            raise Val3Error(f"the dump of vault {old} is not a later state of its mirror here: a {table} row is "
                            "missing or changed")


def _no_dependents(conn, old: str) -> None:
    """No object outside the old mirror depends on its relations, sequences or row types: the rename would carry
    it along and the drop would fail. A dependent with no schema of its own (a rule, a column default) is placed
    by the first name of its address: the schema of its table."""
    found = conn.execute("""
        SELECT DISTINCT o.type || ' ' || o.identity
        FROM pg_depend d, pg_identify_object(d.classid, d.objid, d.objsubid) o,
             pg_identify_object_as_address(d.classid, d.objid, d.objsubid) a
        WHERE d.deptype = 'n'  -- internal and automatic dependents (toast, indexes) go with their table
          AND ((d.refclassid = 'pg_class'::regclass
                AND d.refobjid IN (SELECT oid FROM pg_class WHERE relnamespace = to_regnamespace(%(s)s)))
               OR (d.refclassid = 'pg_type'::regclass
                   AND d.refobjid IN (SELECT oid FROM pg_type WHERE typnamespace = to_regnamespace(%(s)s))))
          AND coalesce(o.schema, a.object_names[1]) IS DISTINCT FROM %(s)s
        ORDER BY 1""", {"s": old}).fetchall()
    if found:
        raise Val3Error(f"cannot refresh mirror {old}: {', '.join(name for (name,) in found)} depends on it "
                        "(drop it first: a refresh replaces the whole schema)")


def _lock_timeout(conn) -> None:
    conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(LOCK_TIMEOUT)))


def _grants(conn, schema: str) -> tuple[list, list]:
    """The USAGE grantees of `schema` and the (table, grantee) pairs of SELECT on its tables, owner left out; a
    grantee None is PUBLIC."""
    usage = conn.execute("""
        SELECT r.rolname FROM pg_namespace n, aclexplode(n.nspacl) a LEFT JOIN pg_roles r ON r.oid = a.grantee
        WHERE n.nspname = %s AND a.privilege_type = 'USAGE' AND a.grantee <> n.nspowner""", (schema,)).fetchall()
    select = conn.execute("""
        SELECT c.relname, r.rolname FROM pg_class c, aclexplode(c.relacl) a LEFT JOIN pg_roles r ON r.oid = a.grantee
        WHERE c.relnamespace = to_regnamespace(%s) AND c.relkind IN ('r', 'p') AND a.privilege_type = 'SELECT'
          AND a.grantee <> c.relowner""", (schema,)).fetchall()
    return [role for (role,) in usage], select


def _grantee(role: str | None) -> sql.Composable:
    return sql.SQL("PUBLIC") if role is None else sql.Identifier(role)


def _publish(conn, vault: str, old: str | None, staging: str, vault_id: str, source: str,
             loaded: dict[str, int]) -> None:
    """Rename the old mirror away and staging to `vault` in one short transaction, then drop the old mirror.
    Readers resolve `vault` by name per statement, so the renames do not wait for them. The new mirror gets
    exactly the old one's USAGE and SELECT grants, table by table; a table the old one lacks gets those of
    `entry`."""
    retired = f"val3_old_{secrets.token_hex(4)}"
    v = sql.Identifier(vault)
    for attempt in range(ATTEMPTS):
        if attempt:
            time.sleep(1)
        try:
            with conn.transaction():
                _lock_timeout(conn)
                usage, select = _grants(conn, old) if old else ([], [])
                lacking = [table for table in vault_tables() if old and not columns(conn, old, table)]
                select += [(table, role) for table in lacking for t, role in list(select) if t == "entry"]
                if old:
                    conn.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(v, sql.Identifier(retired)))
                conn.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(sql.Identifier(staging), v))
                for role in usage:
                    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(v, _grantee(role)))
                for table, role in select:
                    conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(vault, table),
                                                                          _grantee(role)))
                conn.execute("INSERT INTO val3_common.mirror_sync (vault, vault_id, source, rows) "
                             "VALUES (%s, %s, %s, %s)", (vault, vault_id, source, Jsonb(loaded)))
            break
        except psycopg.errors.LockNotAvailable:
            continue
    else:
        raise Val3Error(f"vault {vault} is busy: try again")
    if old:
        _drop(conn, retired)


def _drop(conn, schema: str) -> None:
    """Drop a staging or retired schema table by table, never CASCADE. When a reader still holds it, it stays and
    the next `vault mirror` drops it."""
    try:
        with conn.transaction():
            _lock_timeout(conn)
            for table in reversed(vault_tables()):
                conn.execute(sql.SQL("DROP TABLE IF EXISTS {} RESTRICT").format(sql.Identifier(schema, table)))
            conn.execute(sql.SQL("DROP SCHEMA {} RESTRICT").format(sql.Identifier(schema)))
    except psycopg.Error:
        pass
