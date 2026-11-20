"""`val3 backup`: a pg_dump of the whole val3 database (every vault, val3_common, extensions, privileges)."""
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from psycopg.conninfo import make_conninfo

from val3 import Val3Error
from val3.config import create_private, data_dir
from val3.db import connect

_FILE_SAFE = re.compile(r"[A-Za-z0-9_-]+")


def default_dir() -> Path:
    return data_dir() / "backup"


def backup(service: str, out: Path | None) -> str:
    """Dump the database of `service` into a new private file; return its path, size and vaults."""
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        raise Val3Error("pg_dump not found: install the PostgreSQL client of the server's major version")
    client = _major(subprocess.run([pg_dump, "--version"], capture_output=True, text=True).stdout)
    with connect(service) as conn:
        server = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
        database = conn.execute("SELECT current_database()").fetchone()[0]
        vaults = [name for (name,) in conn.execute("""
            SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'review_item' AND c.relkind = 'r' ORDER BY 1""")]  # catalog only: no schema USAGE needed
    if client < server:
        raise Val3Error(f"pg_dump {client} cannot dump a PostgreSQL {server} server: "
                        f"install postgresql-client-{server}")
    if out is None:
        if not _FILE_SAFE.fullmatch(database):
            raise Val3Error(f"database name {database!r} cannot name a file: pass --out")
        directory = default_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        out = directory / f"{database}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.dump"
    fd = create_private(out)
    try:
        with os.fdopen(fd, "wb") as handle:
            done = subprocess.run([pg_dump, "-Fc", "--no-password", "--enable-row-security",
                                   make_conninfo(service=service)],
                                  stdout=handle, stderr=subprocess.PIPE)
        if done.returncode != 0:
            raise Val3Error(f"pg_dump failed: {done.stderr.decode(errors='replace').strip()}")
    except BaseException:  # failure or interruption: no partial file is left behind
        out.unlink(missing_ok=True)
        raise
    return f"{out}  {out.stat().st_size} bytes  vaults: {', '.join(vaults) or 'none'}"


def _major(version: str) -> int:
    match = re.search(r"(\d+)", version)
    if match is None:
        raise Val3Error(f"cannot read the version of pg_dump: {version.strip()!r}")
    return int(match[1])
