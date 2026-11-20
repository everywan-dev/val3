import psycopg
import pytest

INSERT = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
          "VALUES ('fact', 't', 's', %s, 'p', 'm')")


def prepare(env):
    assert env.run("vault", "create", env.vault, "--service", "val3test").returncode == 0
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")


def grant(env, role, mode):
    return env.run("vault", "grant", env.vault, role, mode, "--service", "val3test")


def test_read_grant_allows_select_only(env):
    prepare(env)
    result = grant(env, env.reader, "--read")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"granted read on {env.vault} to {env.reader}"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry", user=env.reader) == [(0,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(INSERT.format(v=env.vault), ("h1",), user=env.reader)


def test_write_grant_allows_insert_but_not_update(env):
    prepare(env)
    assert grant(env, env.writer, "--write").returncode == 0
    env.sql(INSERT.format(v=env.vault), ("h1",), user=env.writer)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(f"UPDATE {env.vault}.entry SET title = 'x'", user=env.writer)


def test_read_grant_after_write_grant_revokes_insert(env):
    prepare(env)
    assert grant(env, env.writer, "--write").returncode == 0
    assert grant(env, env.writer, "--read").returncode == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(INSERT.format(v=env.vault), ("h1",), user=env.writer)


def test_grant_needs_a_mode(env):
    prepare(env)
    assert env.run("vault", "grant", env.vault, env.reader, "--service", "val3test").returncode == 2


def test_grant_on_a_missing_vault_fails(env):
    prepare(env)
    result = env.run("vault", "grant", "v_missing", env.reader, "--read", "--service", "val3test")
    assert result.returncode == 1
    assert "no vault v_missing" in result.stderr
