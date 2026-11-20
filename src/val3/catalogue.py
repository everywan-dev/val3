"""The vault catalogue, read side (design 2026-11-07-vault-sharing-design.md §4.2): the vaults of a database,
their notes and mirrors. Every reader works on a database not yet upgraded, and never touches a schema the role
cannot use."""
from dataclasses import dataclass
from datetime import datetime

import psycopg

from val3 import Val3Error
from val3.config import check_vault_name
from val3.db import live, vsql


@dataclass(frozen=True)
class Note:
    parent: str | None
    description: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class VaultRow:
    name: str
    entries: int | None  # live entries; None when the role cannot read them
    privilege: str  # write | read | none
    parent: str | None
    description: str | None
    written_by: str | None
    mirror_source: str | None
    synced_at: datetime | None


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] is not None


def notes(conn) -> dict[str, Note]:
    """The latest note of each vault."""
    if not _has(conn, "val3_common.vault_note"):
        return {}
    return {vault: Note(*rest) for vault, *rest in conn.execute("""
        SELECT DISTINCT ON (vault) vault, parent, description, created_by, created_at
        FROM val3_common.vault_note ORDER BY vault, id DESC""")}


# `entry` (pg_class alias e) carries the trigger calling val3_common.mirror_guard(): the vault is a mirror (§4.4)
GUARDED = """EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid
                     WHERE t.tgrelid = e.oid AND p.proname = 'mirror_guard'
                       AND p.pronamespace = to_regnamespace('val3_common'))"""
INTERNAL = "^val3_(old|load)_[0-9a-f]{8}$"  # retired and staging schemas of a mirror refresh


def is_mirror(conn, vault: str) -> bool:
    return conn.execute(f"""
        SELECT EXISTS (SELECT 1 FROM pg_class e JOIN pg_namespace n ON n.oid = e.relnamespace
                       WHERE n.nspname = %s AND e.relname = 'entry' AND {GUARDED})""", (vault,)).fetchone()[0]


def last_sync(conn, vault: str) -> tuple[dict, str, datetime, float] | None:
    """The latest refresh of mirror `vault` with its identity: (row counts, source, synced_at, seconds since); None
    when it is not a mirror, has no such refresh or the role cannot read its identity."""
    if not is_mirror(conn, vault) or not _has(conn, "val3_common.mirror_sync"):
        return None
    try:
        with conn.transaction():
            return conn.execute(vsql(vault, """
                SELECT rows, source, synced_at, extract(epoch FROM now() - synced_at)::float
                FROM val3_common.mirror_sync
                WHERE vault = %s AND vault_id = (SELECT vault_id FROM {v}.identity)
                ORDER BY id DESC LIMIT 1"""), (vault,)).fetchone()
    except psycopg.Error:
        return None


def _valid(name: str) -> bool:
    try:
        check_vault_name(name)
    except Val3Error:
        return False
    return True


_FOUND = f"""
    FROM pg_namespace n
    JOIN pg_class e ON e.relnamespace = n.oid AND e.relname = 'entry' AND e.relkind IN ('r', 'p')
    JOIN pg_class s ON s.relnamespace = n.oid AND s.relname = 'supersession' AND s.relkind IN ('r', 'p')
    WHERE n.nspname <> 'val3_common' AND n.nspname !~ '{INTERNAL}'"""
_READ = "has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(e.oid, 'SELECT')"
# SELECT on every table a read uses: entry, supersession and withdrawal when the vault has it
_FULL_READ = f"""{_READ} AND has_table_privilege(s.oid, 'SELECT')
    AND coalesce((SELECT has_table_privilege(w.oid, 'SELECT') FROM pg_class w
                  WHERE w.relnamespace = n.oid AND w.relname = 'withdrawal'), true)"""


# a mirror is never writable here, not even by its owner
_WRITE = f"has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(e.oid, 'INSERT') AND NOT {GUARDED}"


def vaults(conn) -> list[VaultRow]:
    """Every vault of the database: a schema holding `entry` and `supersession`, other than val3_common and a
    refresh's retired or staging schema. Live entries are counted only where the role reads every table the count
    reads."""
    found = conn.execute(f"""
        SELECT n.nspname, {_READ}, {_WRITE}, {_FULL_READ}, {GUARDED}
        {_FOUND}
        ORDER BY n.nspname""").fetchall()
    described = notes(conn)
    rows = []
    for name, readable, writable, countable, guarded in found:
        entries = None
        if countable and _valid(name):
            try:
                with conn.transaction():  # a schema that only looks like a vault fails here: shown as `-`
                    entries = conn.execute(vsql(name, f"SELECT count(*) FROM {{v}}.entry e WHERE {live(conn, name)}"),
                                           {"as_of": None}).fetchone()[0]
            except psycopg.Error:
                pass
        note, sync = described.get(name), last_sync(conn, name) if guarded and _valid(name) else None
        rows.append(VaultRow(name, entries, "write" if writable else "read" if readable else "none",
                             note and note.parent, note and note.description, note and note.created_by,
                             sync and sync[1], sync and sync[2]))
    return rows


def readable(conn, vault: str) -> bool:
    """`vault` exists and the role can read its entries, supersessions and withdrawals."""
    return conn.execute(f"SELECT EXISTS (SELECT 1 {_FOUND} AND n.nspname = %s AND {_FULL_READ})",
                        (vault,)).fetchone()[0]


def writable(conn, vault: str) -> bool:
    """`vault` exists, is not a mirror and the role can append entries to it."""
    return conn.execute(f"""
        SELECT EXISTS (SELECT 1 {_FOUND} AND n.nspname = %s AND {_WRITE})""",
                        (vault,)).fetchone()[0]


def has_other(conn, names) -> bool:
    """The database holds a vault the role can read that is not one of `names`."""
    return conn.execute(f"SELECT EXISTS (SELECT 1 {_FOUND} AND n.nspname <> ALL(%s) AND {_FULL_READ})",
                        (list(names),)).fetchone()[0]


def has_notes(conn) -> bool:
    return _has(conn, "val3_common.vault_note")
