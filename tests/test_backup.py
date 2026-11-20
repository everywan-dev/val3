import re
import shutil
import stat
import subprocess

import psycopg
import pytest

from helpers import OUTPUT, TOOL, add_service, before_0012, embedding_vault, fact, save


def backups(env):
    directory = env.root / "data" / "val3" / "backup"
    return sorted(directory.glob("*")) if directory.exists() else []


def fake_pg_dump(env, version, code=0):
    bin_dir = env.root / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "pg_dump"
    script.write_text(f"#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'pg_dump (PostgreSQL) {version}'; exit 0; fi\n"
                      f"echo partial\necho 'fake failure' >&2\nexit {code}\n")
    script.chmod(0o755)
    return {"PATH": f"{bin_dir}:{env.environ['PATH']}"}


def real_pg_dump_or_skip():
    path = shutil.which("pg_dump")
    if path is None:
        pytest.skip("no pg_dump on PATH")
    major = int(re.search(r"(\d+)", subprocess.run([path, "--version"], capture_output=True, text=True).stdout)[1])
    if major < 16:
        pytest.skip(f"pg_dump {major} is older than the PostgreSQL 16 test server")


def test_an_older_pg_dump_is_refused(env):
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    result = env.run("backup", environ=fake_pg_dump(env, "14.17"))
    assert result.returncode == 1
    assert "pg_dump 14 cannot dump a PostgreSQL 16 server: install postgresql-client-16" in result.stderr
    assert backups(env) == []


def test_a_missing_pg_dump_is_named(env):
    env.config()
    empty = env.root / "empty-bin"
    empty.mkdir()
    result = env.run("backup", environ={"PATH": str(empty)})
    assert result.returncode == 1
    assert "pg_dump not found: install the PostgreSQL client of the server's major version" in result.stderr
    assert backups(env) == []


def test_a_failing_pg_dump_leaves_no_file(env):
    env.config()
    result = env.run("backup", environ=fake_pg_dump(env, "16.15", code=1))
    assert result.returncode == 1
    assert "pg_dump failed: fake failure" in result.stderr
    assert backups(env) == []


def test_an_existing_output_file_is_refused(env):
    env.config()
    out = env.root / "kept.dump"
    out.write_text("keep")
    result = env.run("backup", "--out", str(out), environ=fake_pg_dump(env, "16.15"))
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert out.read_text() == "keep"


def test_default_backup_is_private(env):
    real_pg_dump_or_skip()
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    result = env.run("backup")
    assert result.returncode == 0, result.stderr
    [path] = backups(env)
    assert re.fullmatch(r"val3-\d{8}T\d{6}Z\.dump", path.name)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert str(path) in result.stdout and env.vault in result.stdout


def test_round_trip_restores_every_vault(env, embedder):
    real_pg_dump_or_skip()
    other = env.vault + "_b"
    embedding_vault(env, embedder.url)
    before_0012(env)  # one vault of each shape
    assert env.run("vault", "create", other).returncode == 0
    t = env.transcript(tool_outputs=[OUTPUT], user_messages=["the north warehouse closed in March 2025"])
    assert save(env, fact(), t).stdout.strip() == f"admitted {env.vault}#1"
    newer = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices.", supersedes=f"{env.vault}#1")
    assert save(env, newer, t).stdout.strip() == f"admitted {env.vault}#2"
    assert save(env, TOOL, t).stdout.strip() == "queued for review: review #1 (no-evidence)"
    assert env.run("review", "reject", "1").returncode == 0
    env.config(home=other)
    assert save(env, fact(title="Other vault fact"), t).stdout.strip() == f"admitted {other}#1"
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    assert env.run("vault", "grant", env.vault, env.reader, "--read").returncode == 0

    env.config(linked=[other], embedding={"url": embedder.url})
    reads = (("show", f"{env.vault}#1"), ("show", f"{env.vault}#2"), ("show", f"{other}#1"),
             ("recall", "invoices"), ("review",))
    before = [env.run(*args).stdout for args in reads]
    out = env.root / "all.dump"
    result = env.run("backup", "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert env.vault in result.stdout and other in result.stdout

    restored = "r" + env.vault
    env.sql(f"CREATE DATABASE {restored} TEMPLATE template0")
    dsn = f"host=127.0.0.1 port={env.port} user=postgres dbname={restored}"
    done = subprocess.run(["pg_restore", "--single-transaction", "--exit-on-error", "-d", dsn, str(out)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    add_service(env, "val3restored", restored)
    env.config(linked=[other], service="val3restored", embedding={"url": embedder.url})
    assert [env.run(*args).stdout for args in reads] == before
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute(f"SELECT old_id, new_id FROM {env.vault}.supersession").fetchall() == [(1, 2)]
        assert conn.execute("SELECT has_table_privilege(%s, %s, 'SELECT')",
                            (env.reader, f"{env.vault}.entry")).fetchone() == (True,)
        assert conn.execute(f"SELECT count(*) FROM {env.vault}.embedding").fetchone()[0] >= 2
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(f"UPDATE {env.vault}.entry SET title = 'x'")
    third = fact(title="May has 50 invoices", statement="The ERP holds 50 invoices dated May 2026.")
    assert save(env, third, t).stdout.strip() == f"admitted {env.vault}#3"


def test_a_database_owner_that_is_not_a_superuser_can_back_up(env):
    real_pg_dump_or_skip()
    owner, database = env.vault.replace("v_", "o_", 1), env.vault.replace("v_", "d_", 1)
    env.sql(f"CREATE ROLE {owner} LOGIN")
    env.sql(f"CREATE DATABASE {database} OWNER {owner}")
    with psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname={database}", autocommit=True) as conn:
        conn.execute("CREATE EXTENSION vector")  # needs a superuser
        conn.execute("CREATE EXTENSION unaccent")
    add_service(env, "val3owner", database, user=owner)
    assert env.run("vault", "create", env.vault, "--service", "val3owner").returncode == 0
    out = env.root / "owner.dump"
    result = env.run("backup", "--service", "val3owner", "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert env.vault in result.stdout
    assert out.stat().st_size > 0
