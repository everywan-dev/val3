"""Mirrors (design 4.4): a vault of another database dumped, loaded into a staging schema and published by a rename;
read only here, refreshed without losing a row, and never replacing a local vault."""
import json
import subprocess
import sys
import time

import psycopg
import pytest

from helpers import OUTPUT, add_service, database, fact, mirror, mirrored, source, with_entry
from test_hooks import ask, context

LEFTOVERS = "SELECT count(*) FROM pg_namespace WHERE nspname LIKE %s"


def add(env, root, title, statement="The ERP holds 7 invoices dated June 2026."):
    result = env.run("save", "--transcript", str(env.transcript(tool_outputs=[OUTPUT])), "--session", env.session,
                     input=json.dumps(fact(title=title, statement=statement)), cwd=root)
    assert result.returncode == 0, result.stderr


def dump(env, name, service="val3b"):
    path = env.root / name
    result = env.run("vault", "dump", env.vault, "--file", str(path), "--service", service)
    assert result.returncode == 0, result.stderr
    assert result.stderr.startswith(f"dumped vault {env.vault}: ")
    return path


def rows(env):
    return [env.sql(f"SELECT count(*) FROM {env.vault}.{table}")[0][0] for table in ("entry", "evidence", "event")]


def leftovers(env, prefix):
    return env.sql(LEFTOVERS, (prefix.replace("_", r"\_") + "%",))[0][0]


def test_mirror_has_the_same_refs_and_content(env):
    root = source(env)
    result = mirrored(env)
    assert result.stdout == f"mirrored vault {env.vault} from val3b: 1 entries (1 new)\n"
    here, there = env.run("show", f"{env.vault}#1"), env.run("show", f"{env.vault}#1", cwd=root)
    assert here.returncode == 0, here.stderr
    assert here.stdout == there.stdout and "April has 42 invoices" in here.stdout
    listed = env.run("vault", "list")
    assert f"{env.vault}  read  1 entries  mirror of val3b, synced " in listed.stdout
    assert env.run("vault", "create", env.vault + "_h").returncode == 0
    env.config(home=env.vault + "_h", linked=[env.vault])
    status = env.run("status")
    assert f"linked: {env.vault} (mirror, synced 0 min ago)" in status.stdout, status.stdout


def test_mirror_cannot_be_written(env):
    source(env)
    mirrored(env)
    result = env.run("save", "--transcript", str(env.transcript(tool_outputs=[OUTPUT])), "--session", env.session,
                     input=json.dumps(fact(title="Local invoices")))
    assert result.returncode == 1
    assert result.stderr == f"val3: error: vault {env.vault} is a mirror and read only\n"
    with pytest.raises(psycopg.Error, match="is a mirror and read only"):
        env.sql(f"INSERT INTO {env.vault}.knowledge_index (name) VALUES ('local')")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    result = env.run("vault", "grant", env.vault, env.writer, "--write")
    assert (result.returncode, result.stderr) == (1, f"val3: error: vault {env.vault} is a mirror and read only\n")
    assert env.sql("SELECT has_table_privilege(%s, %s, 'INSERT')", (env.writer, f"{env.vault}.entry")) == [(False,)]


def test_refresh_adds_rows_and_keeps_grants(env):
    root = source(env)
    mirrored(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    assert env.run("vault", "grant", env.vault, env.reader, "--read").returncode == 0
    add(env, root, "June has 7 invoices")
    assert mirrored(env).stdout == f"mirrored vault {env.vault} from val3b: 2 entries (1 new)\n"
    env.config(service="val3reader")
    result = env.run("show", f"{env.vault}#2")
    assert result.returncode == 0, result.stderr
    assert "June has 7 invoices" in result.stdout


def test_truncated_dump_leaves_the_mirror_intact(env):
    root = source(env)
    mirrored(env)
    before = rows(env)
    add(env, root, "June has 7 invoices")
    path = dump(env, "full.dump")
    assert path.stat().st_mode & 0o777 == 0o600
    again = env.run("vault", "dump", env.vault, "--file", str(path), "--service", "val3b")
    assert again.returncode == 1 and "already exists" in again.stderr
    cut = env.root / "cut.dump"
    cut.write_bytes(path.read_bytes()[:-100])
    result = mirror(env, "--from-file", str(cut))
    assert result.returncode == 1 and "ends early" in result.stderr, result.stderr
    assert rows(env) == before
    assert leftovers(env, "val3_load_") == 0


def test_older_dump_and_other_identity_are_refused(env):
    root = source(env)
    old = dump(env, "old.dump")
    add(env, root, "June has 7 invoices")
    new = dump(env, "new.dump")
    mirrored(env, "--from-file", str(new))
    result = mirror(env, "--from-file", str(old))
    assert result.returncode == 1 and "older" in result.stderr, result.stderr
    third = database(env, "val3c")
    with_entry(env, env.vault, "val3c", third)
    result = mirror(env, "--from-file", str(dump(env, "third.dump", "val3c")))
    assert result.returncode == 1 and "another vault" in result.stderr, result.stderr
    assert rows(env)[0] == 2


def test_local_vault_is_never_replaced(env):
    source(env)
    assert env.run("vault", "create", env.vault).returncode == 0
    result = mirror(env, "--from-service", "val3b")
    assert result.returncode == 1 and "not a mirror" in result.stderr, result.stderr
    assert rows(env)[0] == 0


def test_refresh_while_a_reader_holds_the_old_mirror(env):
    root = source(env)
    mirrored(env)
    add(env, root, "June has 7 invoices")
    with psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname=val3") as reader:
        assert reader.execute(f"SELECT count(*) FROM {env.vault}.entry").fetchone() == (1,)  # transaction stays open
        mirrored(env)
        text = context(ask(env, "How many invoices are there for June?"), "UserPromptSubmit")
        assert f"- {env.vault}#2 [fact] June has 7 invoices" in text
        assert leftovers(env, "val3_old_") >= 1
        reader.commit()
    mirrored(env)
    assert leftovers(env, "val3_old_") == 0


DEPENDENTS = {  # an object outside the mirror on its relations, its row types or its sequences
    "view": ("CREATE VIEW public.{x} AS SELECT * FROM {v}.entry", "DROP VIEW public.{x}"),
    "row type": ("CREATE TABLE public.{x} (e {v}.entry)", "DROP TABLE public.{x}"),
    "function": ("CREATE FUNCTION public.{x}(e {v}.entry) RETURNS int LANGUAGE sql AS 'SELECT 1'",
                 "DROP FUNCTION public.{x}({v}.entry)"),
    "sequence": ("CREATE TABLE public.{x} (id bigint DEFAULT nextval('{v}.entry_id_seq'))", "DROP TABLE public.{x}"),
}


@pytest.mark.parametrize("kind", DEPENDENTS)
def test_external_dependency_refuses_the_refresh(env, kind):
    root = source(env)
    mirrored(env)
    create, drop = (statement.format(x="x_" + env.vault, v=env.vault) for statement in DEPENDENTS[kind])
    env.sql(create)
    add(env, root, "June has 7 invoices")
    result = mirror(env, "--from-service", "val3b")
    assert result.returncode == 1 and f"public.x_{env.vault}" in result.stderr, result.stderr
    assert rows(env)[0] == 1
    assert leftovers(env, "val3_load_") == 0
    env.sql(drop)
    assert mirrored(env).stdout.endswith(": 2 entries (1 new)\n")


def test_leftover_staging_is_dropped_unless_a_load_holds_it(env):
    source(env)
    dead, live = "val3_load_" + env.vault[-8:], "val3_load_" + env.vault[2:10]
    env.sql(f"CREATE SCHEMA {dead}; CREATE SCHEMA {live}")
    with psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname=val3", autocommit=True) as load:
        load.execute("SELECT pg_advisory_lock(hashtext(%s))", ("val3-load:" + live,))
        mirrored(env)
        assert env.sql("SELECT nspname FROM pg_namespace WHERE nspname IN (%s, %s)", (dead, live)) == [(live,)]
    env.sql(f"DROP SCHEMA {live}")


def test_concurrent_refreshes_publish_in_order(env):
    root = source(env)
    mirrored(env)
    add(env, root, "June has 7 invoices")
    old = dump(env, "old.dump")
    add(env, root, "July has 9 invoices", "The ERP holds 9 invoices dated July 2026.")
    new = dump(env, "new.dump")
    runs = [subprocess.Popen([sys.executable, "-m", "val3", "vault", "mirror", env.vault, "--from-file", str(path)],
                             cwd=env.project, env=env.environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True) for path in (old, new)]
    (_, old_err), (_, new_err) = (run.communicate(timeout=60) for run in runs)
    assert runs[1].returncode == 0, new_err
    assert runs[0].returncode == 0 or "older" in old_err, old_err
    assert rows(env)[0] == 3


def edited(env, path, table, column, value):
    """A copy of dump `path` with `column` of the first `table` row set to `value`."""
    lines = path.read_text().split("\n")
    header = json.loads(lines[0])
    at = lines.index(json.dumps({"table": table})) + 1
    fields = lines[at].split("\t")
    fields[header["tables"][table].index(column)] = value
    lines[at] = "\t".join(fields)
    out = env.root / f"edited-{table}-{column}.dump"
    out.write_text("\n".join(lines))
    return out


@pytest.mark.parametrize("table, column, value", [("entry", "content_hash", "0" * 64),
                                                  ("entry", "statement", "The ERP holds 41 invoices."),
                                                  ("evidence", "quote", "41 invoices")])
def test_divergent_dump_is_refused(env, table, column, value):
    source(env)
    path = dump(env, "first.dump")
    mirrored(env, "--from-file", str(path))
    before = env.sql(f"SELECT {column} FROM {env.vault}.{table}")
    result = mirror(env, "--from-file", str(edited(env, path, table, column, value)))
    assert result.returncode == 1 and "not a later state" in result.stderr, result.stderr
    assert env.sql(f"SELECT {column} FROM {env.vault}.{table}") == before and value not in str(before)


def test_dump_through_stdout_and_stdin(env):
    source(env)
    command = [sys.executable, "-m", "val3", "vault"]
    # as the README writes it: outside a project, each side names its service
    dumping = subprocess.Popen([*command, "dump", env.vault, "--file", "-", "--service", "val3b"], cwd=env.root,
                               env=env.environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    result = subprocess.run([*command, "mirror", env.vault, "--from-file", "-", "--service", "val3test"],
                            stdin=dumping.stdout, cwd=env.root, env=env.environ, capture_output=True, text=True,
                            timeout=60)
    dumping.stdout.close()
    assert dumping.wait(timeout=60) == 0
    assert dumping.stderr.read().decode() == f"dumped vault {env.vault}: 1 entries\n"
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"mirrored vault {env.vault} from stdin: 1 entries (1 new)\n"


def test_val3_prefix_is_reserved_where_a_vault_is_made(env):
    source(env)
    path = dump(env, "a.dump")
    refused = "val3: error: invalid vault name 'val3_x': the val3_ prefix is reserved\n"
    for args in (("create", "val3_x"), ("mirror", "val3_x", "--from-file", str(path))):
        result = env.run("vault", *args)
        assert (result.returncode, result.stderr) == (1, refused)
    assert env.sql("SELECT count(*) FROM pg_namespace WHERE nspname = 'val3_x'") == [(0,)]


def test_an_old_vault_named_val3_survives_a_refresh(env):
    source(env)
    older = env.root / "older"
    older.mkdir()
    names = ["val3_old_accounts", "val3_old_" + env.vault[-8:]]  # made before the prefix was reserved
    try:
        for n, name in enumerate(names):
            with_entry(env, f"{env.vault}_{n}", cwd=older)
            env.sql(f"ALTER SCHEMA {env.vault}_{n} RENAME TO {name}")
        mirrored(env)
        assert env.sql("SELECT count(*) FROM pg_namespace WHERE nspname = ANY(%s)", (names,)) == [(2,)]
        result = env.run("show", "val3_old_accounts#1")
        assert result.returncode == 0 and "April has 42 invoices" in result.stdout, result.stderr
        assert any(line.startswith("val3_old_accounts  write  1 entries") for line in
                   env.run("vault", "list").stdout.splitlines())
    finally:
        for name in names:
            env.sql(f"DROP SCHEMA IF EXISTS {name} CASCADE")


ACL = """
    SELECT c.relname, coalesce(r.rolname, 'PUBLIC'), a.privilege_type
    FROM pg_class c, aclexplode(c.relacl) a LEFT JOIN pg_roles r ON r.oid = a.grantee
    WHERE c.relnamespace = to_regnamespace(%(v)s) AND c.relkind = 'r' AND a.grantee <> c.relowner
    UNION ALL
    SELECT '', coalesce(r.rolname, 'PUBLIC'), a.privilege_type
    FROM pg_namespace n, aclexplode(n.nspacl) a LEFT JOIN pg_roles r ON r.oid = a.grantee
    WHERE n.nspname = %(v)s AND a.grantee <> n.nspowner
    ORDER BY 1, 2, 3"""


def test_refresh_keeps_grants_exactly(env):
    root = source(env)
    mirrored(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    assert env.run("vault", "grant", env.vault, env.reader, "--read").returncode == 0
    v, r = env.vault, env.reader
    env.sql(f"REVOKE SELECT ON ALL TABLES IN SCHEMA {v} FROM {r}; GRANT SELECT ON {v}.entry, {v}.supersession TO {r};"
            f"GRANT SELECT ON {v}.entry TO PUBLIC")
    before = env.sql(ACL, {"v": v})
    assert ("entry", "PUBLIC", "SELECT") in before and ("evidence", r, "SELECT") not in before
    add(env, root, "June has 7 invoices")
    assert mirrored(env).stdout.endswith(": 2 entries (1 new)\n")
    assert env.sql(ACL, {"v": v}) == before


def test_a_dropped_mirror_is_not_a_mirror(env):
    source(env)
    mirrored(env)
    env.sql(f"DROP SCHEMA {env.vault} CASCADE")
    assert env.run("vault", "create", env.vault).returncode == 0
    assert f"{env.vault}  write  0 entries\n" in env.run("vault", "list").stdout
    assert env.run("vault", "create", env.vault + "_h").returncode == 0
    env.config(home=env.vault + "_h", linked=[env.vault])
    status = env.run("status")
    assert f"linked: {env.vault}\n" in status.stdout and "(mirror" not in status.stdout, status.stdout
    result = mirror(env, "--from-service", "val3b")
    assert result.returncode == 1 and "not a mirror" in result.stderr, result.stderr


def test_hook_answers_during_publish(env):
    source(env)
    path = dump(env, "same.dump")
    mirrored(env, "--from-file", str(path))
    loop = "for i in 1 2 3 4 5; do \"$0\" -m val3 vault mirror \"$1\" --from-file \"$2\" || exit 1; done"
    refreshing = subprocess.Popen(["sh", "-c", loop, sys.executable, env.vault, str(path)], cwd=env.project,
                                  env=env.environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    runs = 0
    while refreshing.poll() is None or runs < 3:
        started = time.monotonic()
        text = context(ask(env, "How many invoices are there for April?"), "UserPromptSubmit")
        assert time.monotonic() - started < 2
        assert text and f"- {env.vault}#1 [fact] April has 42 invoices" in text
        runs += 1
    _, err = refreshing.communicate(timeout=60)
    assert refreshing.returncode == 0, err
