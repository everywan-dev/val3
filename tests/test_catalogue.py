import psycopg
import pytest

from helpers import add_service

NOTE = ("INSERT INTO val3_common.vault_note (vault, description{extra}) VALUES (%s, 'direct'{value})")


def create(env, name=None, *extra):
    result = env.run("vault", "create", name or env.vault, "--service", "val3test", *extra)
    assert result.returncode == 0, result.stderr
    return result


def roles(env):
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")


def listing(env, service="val3test"):
    result = env.run("vault", "list", "--service", service)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def line_of(lines, vault):
    """The vault's line and the description line after it, if any."""
    for i, line in enumerate(lines):
        if line.split("  ")[0] == vault:
            follow = lines[i + 1] if i + 1 < len(lines) and lines[i + 1].startswith("    ") else None
            return line, follow
    raise AssertionError(f"{vault} not listed: {lines}")


def notes(env, vault):
    return env.sql("SELECT count(*) FROM val3_common.vault_note WHERE vault = %s", (vault,))[0][0]


def test_create_with_description_and_list(env):
    other = env.vault + "_p"
    create(env, other)
    create(env, env.vault, "--description", "Migration difficulties", "--parent", other)
    line, follow = line_of(listing(env), env.vault)
    assert line == f"{env.vault}  write  0 entries  parent {other}"
    assert follow == "    Migration difficulties (by postgres)"
    line, follow = line_of(listing(env), other)
    assert line == f"{other}  write  0 entries" and follow is None


def test_describe_appends_and_latest_wins(env):
    create(env)
    for text in ("First text", "Second text"):
        result = env.run("vault", "describe", env.vault, "--description", text, "--service", "val3test")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"described vault {env.vault}"
    assert notes(env, env.vault) == 2
    assert line_of(listing(env), env.vault)[1] == "    Second text (by postgres)"


def test_list_shows_privilege_per_role(env):
    other = env.vault + "_x"
    create(env, env.vault, "--description", "Readable")
    create(env, other)
    roles(env)
    assert env.run("vault", "grant", env.vault, env.reader, "--read", "--service", "val3test").returncode == 0
    lines = listing(env, "val3reader")
    assert line_of(lines, env.vault) == (f"{env.vault}  read  0 entries", "    Readable (by postgres)")
    assert line_of(lines, other) == (f"{other}  none  -", None)


def test_reader_cannot_describe(env):
    create(env, env.vault, "--description", "Kept")
    roles(env)
    assert env.run("vault", "grant", env.vault, env.reader, "--read", "--service", "val3test").returncode == 0
    result = env.run("vault", "describe", env.vault, "--description", "Taken over", "--service", "val3reader")
    assert result.returncode == 1
    assert result.stderr == (f"val3: error: cannot describe vault {env.vault}: describing a vault needs write on it "
                             f"(val3 vault grant {env.vault} {env.reader} --write)\n")
    assert notes(env, env.vault) == 1


def test_rls_refuses_direct_inserts(env):
    create(env)
    roles(env)
    assert env.run("vault", "grant", env.vault, env.reader, "--read", "--service", "val3test").returncode == 0
    assert env.run("vault", "grant", env.vault, env.writer, "--write", "--service", "val3test").returncode == 0
    with pytest.raises(psycopg.Error):
        env.sql(NOTE.format(extra="", value=""), (env.vault,), user=env.reader)
    with pytest.raises(psycopg.Error):
        env.sql(NOTE.format(extra=", created_by", value=", 'postgres'"), (env.vault,), user=env.writer)
    env.sql(NOTE.format(extra="", value=""), (env.vault,), user=env.writer)
    assert env.sql("SELECT created_by FROM val3_common.vault_note WHERE vault = %s", (env.vault,)) == [(env.writer,)]


def test_mirror_guard_refuses_without_the_loading_flag(env):
    create(env)  # val3_common exists
    roles(env)
    scratch = "s" + env.vault
    env.sql(f"CREATE SCHEMA {scratch} AUTHORIZATION {env.writer}")
    env.sql(f"CREATE TABLE {scratch}.t (x int)")
    env.sql(f"CREATE TRIGGER guard BEFORE INSERT ON {scratch}.t "
            "FOR EACH ROW EXECUTE FUNCTION val3_common.mirror_guard()")
    env.sql(f"GRANT INSERT ON {scratch}.t TO {env.writer}, {env.reader}")
    env.sql(f"GRANT USAGE ON SCHEMA {scratch} TO {env.reader}")
    with pytest.raises(psycopg.Error, match="is a mirror and read only"):
        env.sql(f"INSERT INTO {scratch}.t VALUES (1)", user=env.writer)
    env.sql(f"SET val3.loading = 'on'; INSERT INTO {scratch}.t VALUES (2)", user=env.writer)
    with pytest.raises(psycopg.Error, match="is a mirror and read only"):
        env.sql(f"SET val3.loading = 'on'; INSERT INTO {scratch}.t VALUES (3)", user=env.reader)
    assert env.sql(f"SELECT x FROM {scratch}.t") == [(2,)]


def test_description_rules(env):
    create(env)
    for text in ("x" * 301, "two\nlines"):
        result = env.run("vault", "describe", env.vault, "--description", text, "--service", "val3test")
        assert result.returncode == 1, text
        result = env.run("vault", "create", env.vault + "_d", "--description", text, "--service", "val3test")
        assert result.returncode == 1, text
    result = env.run("vault", "create", env.vault + "_d", "--parent", env.vault, "--service", "val3test")
    assert result.returncode == 2 and "--parent goes with --description" in result.stderr
    assert notes(env, env.vault) == 0
    assert env.sql("SELECT 1 FROM pg_namespace WHERE nspname = %s", (env.vault + "_d",)) == []


def test_upgrade_adds_identity_and_common_tables(env):
    database = "c" + env.vault
    env.sql(f"CREATE DATABASE {database}")
    add_service(env, "val3own", database)
    assert env.run("vault", "list", "--service", "val3own").stdout.strip() == "no vaults"
    assert env.run("vault", "create", env.vault, "--service", "val3own").returncode == 0
    dsn = f"host=127.0.0.1 port={env.port} user=postgres dbname={database}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute(f"SELECT count(*) FROM {env.vault}.identity").fetchone() == (1,)
        conn.execute(f"DROP TABLE {env.vault}.identity")
        conn.execute("DROP TABLE val3_common.vault_note, val3_common.mirror_sync")
        conn.execute("DROP FUNCTION val3_common.mirror_guard()")
    result = env.run("vault", "list", "--service", "val3own")  # a database not yet upgraded
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{env.vault}  write  0 entries"
    result = env.run("vault", "upgrade", env.vault, "--service", "val3own")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute(f"SELECT count(*) FROM {env.vault}.identity").fetchone() == (1,)
        assert conn.execute("SELECT to_regclass('val3_common.vault_note'), to_regclass('val3_common.mirror_sync'), "
                            "to_regprocedure('val3_common.mirror_guard()')").fetchone().count(None) == 0
        with pytest.raises(psycopg.Error):
            conn.execute(f"INSERT INTO {env.vault}.identity (vault_id) VALUES (gen_random_uuid())")
    result = env.run("vault", "upgrade", env.vault, "--service", "val3own")
    assert result.stdout.strip() == f"vault {env.vault} is up to date"
    assert env.run("vault", "describe", env.vault, "--description", "Back", "--service", "val3own").returncode == 0


def own_database(env, prefix):
    database = prefix + env.vault
    env.sql(f"CREATE DATABASE {database}")
    add_service(env, "val3own", database)
    assert env.run("vault", "create", env.vault, "--service", "val3own").returncode == 0
    return f"host=127.0.0.1 port={env.port} user=postgres dbname={database}"


def test_list_counts_only_with_every_table_readable(env):
    dsn = own_database(env, "n")
    roles(env)
    assert env.run("vault", "grant", env.vault, env.reader, "--read", "--service", "val3own").returncode == 0
    add_service(env, "val3ownreader", dsn.rsplit("=", 1)[1], user=env.reader)
    assert line_of(listing(env, "val3ownreader"), env.vault)[0] == f"{env.vault}  read  0 entries"
    junk = "j" + env.vault  # a schema with the two table names that is not a vault
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"REVOKE SELECT ON {env.vault}.withdrawal FROM {env.reader}")
        conn.execute(f"CREATE SCHEMA {junk}")
        conn.execute(f"CREATE TABLE {junk}.entry (a int)")
        conn.execute(f"CREATE TABLE {junk}.supersession (b int)")
    assert line_of(listing(env, "val3ownreader"), env.vault)[0] == f"{env.vault}  read  -"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"GRANT SELECT ON {env.vault}.withdrawal TO {env.reader}")
        conn.execute(f"REVOKE SELECT ON {env.vault}.supersession FROM {env.reader}")
    assert line_of(listing(env, "val3ownreader"), env.vault)[0] == f"{env.vault}  read  -"
    lines = listing(env, "val3own")
    assert line_of(lines, junk)[0] == f"{junk}  write  -"
    assert line_of(lines, env.vault)[0] == f"{env.vault}  write  0 entries"


def test_note_refuses_pg_names_also_after_upgrade(env):
    dsn = own_database(env, "g")
    with psycopg.connect(dsn, autocommit=True) as conn:
        for column in ("vault", "parent"):
            conn.execute(f"ALTER TABLE val3_common.vault_note DROP CONSTRAINT vault_note_{column}_check, "
                         f"ADD CONSTRAINT vault_note_{column}_check CHECK ({column} ~ '^[a-z][a-z0-9_]{{0,62}}$')")
    assert env.run("vault", "upgrade", env.vault, "--service", "val3own").returncode == 0
    with psycopg.connect(dsn, autocommit=True) as conn:
        for vault, parent in (("pg_temp", None), (env.vault, "pg_temp")):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("INSERT INTO val3_common.vault_note (vault, parent, description) VALUES (%s, %s, 'x')",
                             (vault, parent))


def test_describe_without_vault_notes_names_the_upgrade(env):
    dsn = own_database(env, "u")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP TABLE val3_common.vault_note")
    result = env.run("vault", "describe", env.vault, "--description", "Any", "--service", "val3own")
    assert result.returncode == 1
    assert result.stderr == ("val3: error: this database has no vault notes yet: its owner runs "
                             "val3 vault upgrade <vault>\n")


def test_create_on_a_database_without_notes(env):
    name = f"val3n_{env.vault[2:]}"
    env.sql(f"CREATE DATABASE {name}")
    add_service(env, "val3n", name)
    assert env.run("vault", "create", env.vault, "--service", "val3n").returncode == 0
    with psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname={name}", autocommit=True) as conn:
        conn.execute("DROP TABLE val3_common.vault_note, val3_common.mirror_sync; "
                     "DROP FUNCTION val3_common.mirror_guard()")  # a val3_common from before vault sharing
        plain = env.run("vault", "create", env.vault + "_x", "--service", "val3n")
        assert plain.returncode == 0, plain.stderr
        described = env.run("vault", "create", env.vault + "_y", "--description", "d", "--service", "val3n")
        assert (described.returncode, described.stderr) == (
            1, "val3: error: this database has no vault notes yet: its owner runs val3 vault upgrade <vault>\n")
        assert conn.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE %s ORDER BY 1",
                            (env.vault + "%",)).fetchall() == [(env.vault,), (env.vault + "_x",)]
