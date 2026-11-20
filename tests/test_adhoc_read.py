"""Ad hoc reads (design 4.3): show any readable vault, recall and index over named vaults, another database with
--service, and the session index naming `val3 vault list` when the database holds other vaults."""
import json

from helpers import OUTPUT, add_service, fact, with_entry
from test_recheck import index


def two_vaults(env):
    """The project's home vault and an unlinked vault `<home>_o`, one fact in each."""
    other = env.vault + "_o"
    with_entry(env, other, title="Other vault invoices")
    with_entry(env, env.vault)
    return other


def test_show_an_unlinked_vault(env):
    other = two_vaults(env)
    result = env.run("show", f"{other}#1")
    assert result.returncode == 0, result.stderr
    assert "Other vault invoices" in result.stdout and "TO RE-CHECK" not in result.stdout


def test_recall_with_vault_searches_only_it(env):
    other = two_vaults(env)
    result = env.run("recall", "invoices")
    assert result.returncode == 0, result.stderr
    assert f"{env.vault}#1" in result.stdout and other not in result.stdout
    result = env.run("recall", "invoices", "--vault", other)
    assert result.returncode == 0, result.stderr
    assert f"{other}#1" in result.stdout and f"{env.vault}#1" not in result.stdout
    result = env.run("recall", "invoices", "--vault", other, "--vault", env.vault)
    assert f"{other}#1" in result.stdout and f"{env.vault}#1" in result.stdout
    result = env.run("index", "--vault", other)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "billing  1\n"
    result = env.run("index", "billing", "--vault", other)
    assert result.stdout == f"- {other}#1 [fact] Other vault invoices\n", result.stderr


def test_missing_grant_names_the_grant(env):
    other = two_vaults(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.config(service="val3reader")
    message = f"val3: error: cannot read vault {other} (val3 vault grant {other} {env.reader} --read)\n"
    for args in (("show", f"{other}#1"), ("recall", "invoices", "--vault", other), ("index", "--vault", other)):
        result = env.run(*args)
        assert (result.returncode, result.stderr) == (1, message), args
    missing = env.vault + "_none"
    result = env.run("show", f"{missing}#1")
    assert result.returncode == 1
    assert result.stderr == message.replace(other, missing)
    assert env.run("vault", "grant", other, env.reader, "--read", "--service", "val3test").returncode == 0
    result = env.run("show", f"{other}#1")
    assert result.returncode == 0, result.stderr
    env.sql(f"REVOKE SELECT ON {other}.supersession FROM {env.reader}")
    result = env.run("show", f"{other}#1")
    assert (result.returncode, result.stderr) == (1, message)


def test_service_reads_another_database(env):
    database = "d" + env.vault
    env.sql(f"CREATE DATABASE {database}")
    add_service(env, "val3other", database)
    remote = env.project.parent / "remote"
    remote.mkdir()
    with_entry(env, env.vault, "val3other", remote, title="Remote invoices")
    bare = env.project.parent / "bare"  # no val3.toml here or above
    bare.mkdir()
    result = env.run("recall", "invoices", "--service", "val3other", cwd=bare)
    assert result.returncode == 2 and "--vault" in result.stderr
    result = env.run("recall", "invoices", "--service", "val3other", "--vault", env.vault, cwd=bare)
    assert result.returncode == 0, result.stderr
    assert f"{env.vault}#1  [fact]  Remote invoices" in result.stdout
    result = env.run("show", f"{env.vault}#1", "--service", "val3other", cwd=bare)
    assert result.returncode == 0 and "Remote invoices" in result.stdout, result.stderr
    result = env.run("index", "--service", "val3other", "--vault", env.vault, cwd=bare)
    assert (result.returncode, result.stdout) == (0, "billing  1\n"), result.stderr
    env.config(home=env.vault + "_here")  # a project on the shared database: --service reads the other one
    result = env.run("show", f"{env.vault}#1", "--service", "val3other")
    assert result.returncode == 0 and "Remote invoices" in result.stdout, result.stderr
    result = env.run("index", "merge", "a", "b", "--service", "val3other")
    assert result.returncode == 2 and "--service" in result.stderr


def test_index_line_only_with_a_foreign_vault(env):
    database = "i" + env.vault
    env.sql(f"CREATE DATABASE {database}")
    add_service(env, "val3idx", database)
    env.config(service="val3idx")
    assert env.run("vault", "create", env.vault).returncode == 0
    line = "Other vaults on this server: val3 vault list"
    assert line not in index(env)
    assert env.run("vault", "create", env.vault + "_x").returncode == 0
    assert line in index(env)
