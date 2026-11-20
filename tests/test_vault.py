import psycopg
import pytest

from helpers import OLD_CHECKS, OLD_REASONS, add_service

TABLES = {"entry", "identity", "supersession", "knowledge_index", "entry_index", "evidence", "tool",
          "recipe_parameter", "recipe_step", "step_fact", "recipe_example", "review_item",
          "review_decision", "event", "embedding", "withdrawal", "index_alias",
          "neighbour_exempt", "neighbour", "settlement", "attestation"}


def create(env):
    result = env.run("vault", "create", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr


def insert_entry(env, statement="s", digest="h1"):
    return env.sql(
        f"INSERT INTO {env.vault}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
        "VALUES ('fact', 't', %s, %s, 'p', 'm') RETURNING id", (statement, digest))[0][0]


def test_create_vault_from_project_config(env):
    env.config()
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"created vault {env.vault}"
    rows = env.sql("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (env.vault,))
    assert {name for (name,) in rows} == TABLES


def test_create_vault_with_explicit_service_outside_a_project(env):
    create(env)


def test_create_existing_vault_fails(env):
    create(env)
    result = env.run("vault", "create", env.vault, "--service", "val3test")
    assert result.returncode == 1
    assert "already exists" in result.stderr


def test_invalid_vault_names_are_rejected(env):
    for name in ("Bad-Name", "pg_x", "public", "val3_common", "1abc", "a;drop"):
        result = env.run("vault", "create", name, "--service", "val3test")
        assert result.returncode == 1, name
        assert "invalid vault name" in result.stderr


def test_missing_config_fails(env):
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 1
    assert "no val3.toml found" in result.stderr


def test_config_errors_are_reported(env):
    (env.project / "val3.toml").write_text('service = "val3test"\ncolour = 1\n[vaults]\nhome = "x"\n')
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 1
    assert "unknown keys: colour" in result.stderr
    env.config(linked=[env.vault])
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 1
    assert "appears twice" in result.stderr


def test_delivery_keys_must_be_booleans(env):
    for table, key in (('[delivery]\nindex = "no"\n', "index"), ("[delivery]\ncolour = true\n", "colour")):
        env.config()
        with (env.project / "val3.toml").open("a") as handle:
            handle.write(table)
        result = env.run("vault", "create", env.vault)
        assert result.returncode == 1, table
        assert str(env.project / "val3.toml") in result.stderr and key in result.stderr, result.stderr


def test_config_is_found_from_a_subdirectory(env):
    env.config()
    sub = env.project / "a" / "b"
    sub.mkdir(parents=True)
    result = env.run("vault", "create", env.vault, cwd=sub)
    assert result.returncode == 0, result.stderr


def test_vault_tables_are_append_only(env):
    create(env)
    entry_id = insert_entry(env)
    env.sql(f"INSERT INTO {env.vault}.embedding (entry_id, model, digest, text_version, dims, vector) "
            f"VALUES (%s, 'm', 'd', 1, 2, '[1,0]')", (entry_id,))
    env.sql(f"INSERT INTO {env.vault}.withdrawal (entry_id, cause, reason, withdrawn_by) "
            "VALUES (%s, 'wrong', 'r', 'u')", (entry_id,))
    for statement in (f"UPDATE {env.vault}.entry SET title = 'x' WHERE id = {entry_id}",
                      f"DELETE FROM {env.vault}.entry",
                      f"TRUNCATE {env.vault}.event",
                      f"UPDATE {env.vault}.embedding SET model = 'n'",
                      f"TRUNCATE {env.vault}.embedding",
                      f"UPDATE {env.vault}.withdrawal SET reason = 'x'",
                      f"UPDATE {env.vault}.withdrawal SET evidence = '[]'",
                      f"DELETE FROM {env.vault}.withdrawal",
                      f"TRUNCATE {env.vault}.withdrawal"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            env.sql(statement)


def test_search_vectors_ignore_accents(env):
    create(env)
    insert_entry(env, statement="Facturación de abril")
    rows = env.sql(f"SELECT search_es @@ to_tsquery('val3_common.es', 'facturacion'), "
                   f"search_en @@ to_tsquery('val3_common.en', 'facturacion') FROM {env.vault}.entry")
    assert rows == [(True, True)]


ENTRY = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
         "VALUES ('fact', 't', 's', 'h1', 'p', 'm') RETURNING id")


def test_upgrade_adds_the_embedding_table_and_keeps_grants(env):
    create(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    for role, mode in ((env.reader, "--read"), (env.writer, "--write")):
        assert env.run("vault", "grant", env.vault, role, mode, "--service", "val3test").returncode == 0
    env.sql(f"DROP TABLE {env.vault}.embedding")  # a vault created before this plan
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    entry_id = env.sql(ENTRY.format(v=env.vault), user=env.writer)[0][0]
    env.sql(f"INSERT INTO {env.vault}.embedding (entry_id, model, digest, text_version, dims, vector) "
            "VALUES (%s, 'm', 'd', 1, 2, '[1,0]')", (entry_id,), user=env.writer)
    assert env.sql(f"SELECT count(*) FROM {env.vault}.embedding", user=env.reader) == [(1,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(f"INSERT INTO {env.vault}.embedding (entry_id, model, digest, text_version, dims, vector) "
                "VALUES (%s, 'n', 'd', 1, 2, '[1,0]')", (entry_id,), user=env.reader)


def test_upgrade_adds_the_withdrawal_table_and_its_review_reasons(env):
    create(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    for role, mode in ((env.reader, "--read"), (env.writer, "--write")):
        assert env.run("vault", "grant", env.vault, role, mode, "--service", "val3test").returncode == 0
    env.sql(f"DROP TABLE {env.vault}.withdrawal")  # a vault created before this plan
    env.sql(OLD_REASONS.format(v=env.vault))
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    entry_id = env.sql(ENTRY.format(v=env.vault), user=env.writer)[0][0]
    env.sql(f"INSERT INTO {env.vault}.withdrawal (entry_id, cause, reason, withdrawn_by) "
            "VALUES (%s, 'obsolete', 'r', 'u')", (entry_id,), user=env.writer)
    assert env.sql(f"SELECT count(*) FROM {env.vault}.withdrawal", user=env.reader) == [(1,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(f"INSERT INTO {env.vault}.withdrawal (entry_id, cause, reason, withdrawn_by) "
                "VALUES (%s, 'wrong', 'r', 'u')", (entry_id,), user=env.reader)
    env.sql(f"INSERT INTO {env.vault}.review_item (reason, payload, content_hash, origin_project, origin_machine) "
            "VALUES ('withdrawal', '{}', 'x', 'p', 'm')")
    assert env.run("vault", "upgrade", env.vault, "--service", "val3test").stdout.strip() == \
        f"vault {env.vault} is up to date"


def test_upgrade_adds_the_index_alias_table(env):
    create(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    for role, mode in ((env.reader, "--read"), (env.writer, "--write")):
        assert env.run("vault", "grant", env.vault, role, mode, "--service", "val3test").returncode == 0
    env.sql(f"DROP TABLE {env.vault}.index_alias")  # a vault created before this plan
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    target = env.sql(f"INSERT INTO {env.vault}.knowledge_index (name) VALUES ('billing') RETURNING id")[0][0]
    env.sql(f"INSERT INTO {env.vault}.index_alias (name, target_id) VALUES ('invoices', %s)", (target,),
            user=env.writer)
    assert env.sql(f"SELECT name FROM {env.vault}.index_alias", user=env.reader) == [("invoices",)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(f"INSERT INTO {env.vault}.index_alias (name, target_id) VALUES ('sales', %s)", (target,),
                user=env.reader)
    for statement in (f"UPDATE {env.vault}.index_alias SET name = 'x'", f"DELETE FROM {env.vault}.index_alias",
                      f"TRUNCATE {env.vault}.index_alias"):
        with pytest.raises(psycopg.errors.RaiseException):
            env.sql(statement)
    assert env.run("vault", "upgrade", env.vault, "--service", "val3test").stdout.strip() == \
        f"vault {env.vault} is up to date"
    other = env.vault + "_b"
    assert env.run("vault", "create", other, "--service", "val3test").returncode == 0
    assert env.run("vault", "grant", other, env.reader, "--read", "--service", "val3test").returncode == 0
    assert env.sql(f"SELECT count(*) FROM {other}.index_alias", user=env.reader) == [(0,)]


def test_upgrade_adds_the_neighbour_tables(env):
    create(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    for role, mode in ((env.reader, "--read"), (env.writer, "--write")):
        assert env.run("vault", "grant", env.vault, role, mode, "--service", "val3test").returncode == 0
    old = [insert_entry(env, digest="h1"), insert_entry(env, digest="h2")]
    env.sql(f"DROP TABLE {env.vault}.settlement, {env.vault}.neighbour, {env.vault}.neighbour_exempt")
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    assert env.sql(f"SELECT entry_id FROM {env.vault}.neighbour_exempt ORDER BY 1", user=env.reader) == \
        [(i,) for i in old]
    new = insert_entry(env, digest="h3")
    env.sql(f"INSERT INTO {env.vault}.neighbour (entry_id, other_id, score) VALUES (%s, %s, 0.8)", (new, old[0]),
            user=env.writer)
    env.sql(f"INSERT INTO {env.vault}.settlement (entry_id, other_id, outcome, settled_by) "
            "VALUES (%s, %s, 'keep', 'u')", (new, old[0]), user=env.writer)
    assert env.sql(f"SELECT count(*) FROM {env.vault}.settlement", user=env.reader) == [(1,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(f"INSERT INTO {env.vault}.neighbour_exempt (entry_id) VALUES (%s)", (new,), user=env.reader)
    for bad in (f"INSERT INTO {env.vault}.neighbour (entry_id, other_id, score) VALUES ({old[0]}, {new}, 0.8)",
                f"INSERT INTO {env.vault}.settlement (entry_id, other_id, outcome, settled_by) "
                f"VALUES ({new}, {old[0]}, 'merge', 'u')"):
        with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation)):
            env.sql(bad)
    for table in ("neighbour_exempt", "neighbour", "settlement"):
        for statement in (f"UPDATE {env.vault}.{table} SET created_at = now()", f"DELETE FROM {env.vault}.{table}",
                          f"TRUNCATE {env.vault}.{table} CASCADE"):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                env.sql(statement)
    assert env.run("vault", "upgrade", env.vault, "--service", "val3test").stdout.strip() == \
        f"vault {env.vault} is up to date"


def test_upgrade_brings_a_vault_without_every_part_to_current(env):
    create(env)
    env.sql(f"DROP TABLE {env.vault}.index_alias, {env.vault}.settlement, {env.vault}.neighbour, "
            f"{env.vault}.neighbour_exempt")
    env.sql(f"ALTER TABLE {env.vault}.withdrawal DROP COLUMN evidence")
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    rows = env.sql("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (env.vault,))
    assert {name for (name,) in rows} == TABLES
    assert env.sql("SELECT count(*) FROM information_schema.columns WHERE table_schema = %s "
                   "AND table_name = 'withdrawal' AND column_name = 'evidence'", (env.vault,)) == [(1,)]
    assert env.run("vault", "upgrade", env.vault, "--service", "val3test").stdout.strip() == \
        f"vault {env.vault} is up to date"


def test_upgrade_accepts_the_duplicate_cause(env):
    create(env)
    entry_id = insert_entry(env)
    env.sql(f"ALTER TABLE {env.vault}.withdrawal DROP CONSTRAINT withdrawal_cause_check, "
            "ADD CHECK (cause IN ('wrong', 'obsolete'))")  # the unnamed check of a vault created before this plan
    duplicate = (f"INSERT INTO {env.vault}.withdrawal (entry_id, cause, reason, withdrawn_by) "
                 "VALUES (%s, 'duplicate', 'duplicate of v#1', 'u')")
    with pytest.raises(psycopg.errors.CheckViolation):
        env.sql(duplicate, (entry_id,))
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    env.sql(duplicate, (entry_id,))
    assert env.sql("SELECT count(*) FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'c' "
                   "AND pg_get_constraintdef(oid) LIKE '%%cause%%'", (f"{env.vault}.withdrawal",)) == [(1,)]


def test_upgrade_adds_the_withdrawal_evidence_column(env):
    create(env)
    env.sql(f"ALTER TABLE {env.vault}.withdrawal DROP COLUMN evidence")  # a vault created before this plan
    entry_id = insert_entry(env)
    env.sql(f"INSERT INTO {env.vault}.withdrawal (entry_id, cause, reason, withdrawn_by) "
            "VALUES (%s, 'wrong', 'r', 'u')", (entry_id,))
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    assert env.sql("SELECT data_type FROM information_schema.columns "
                   "WHERE table_schema = %s AND table_name = 'withdrawal' AND column_name = 'evidence'",
                   (env.vault,)) == [("jsonb",)]
    assert env.sql(f"SELECT evidence IS NULL FROM {env.vault}.withdrawal") == [(True,)]
    assert env.run("vault", "upgrade", env.vault, "--service", "val3test").stdout.strip() == \
        f"vault {env.vault} is up to date"


def test_a_withdrawal_needs_a_known_cause_and_a_reason(env):
    create(env)
    entry_id = insert_entry(env)
    for cause, reason in (("mistaken", "r"), ("wrong", "  ")):
        with pytest.raises(psycopg.errors.CheckViolation):
            env.sql(f"INSERT INTO {env.vault}.withdrawal (entry_id, cause, reason, withdrawn_by) "
                    "VALUES (%s, %s, %s, 'u')", (entry_id, cause, reason))


def test_embedding_dimensions_match_the_vector(env):
    create(env)
    entry_id = insert_entry(env)
    with pytest.raises(psycopg.errors.CheckViolation):
        env.sql(f"INSERT INTO {env.vault}.embedding (entry_id, model, digest, text_version, dims, vector) "
                "VALUES (%s, 'm', 'd', 1, 3, '[1,0]')", (entry_id,))


def test_upgrade_of_a_current_vault_changes_nothing(env):
    create(env)
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"vault {env.vault} is up to date"


def test_upgrade_of_a_missing_vault_fails(env):
    result = env.run("vault", "upgrade", "v_missing", "--service", "val3test")
    assert result.returncode == 1
    assert "no vault v_missing" in result.stderr


def test_a_role_that_does_not_own_the_shared_schema_creates_a_vault(env):
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    env.sql(f"GRANT CREATE ON DATABASE val3 TO {env.writer}")
    result = env.run("vault", "create", env.vault + "_w", "--service", "val3writer")
    assert result.returncode == 0, result.stderr


def test_an_old_shared_schema_names_the_upgrade(env):
    database = "c" + env.vault
    env.sql(f"CREATE DATABASE {database}")
    add_service(env, "val3old", database)
    dsn = f"host=127.0.0.1 port={env.port} user=postgres dbname={database}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA val3_common")
    result = env.run("vault", "create", env.vault, "--service", "val3old")
    assert result.returncode == 1
    assert "val3_common is from an older version" in result.stderr and "vector" in result.stderr
    assert "val3 vault upgrade" in result.stderr


def test_upgrade_widens_the_checks_for_imports(env):
    create(env)
    for statement in OLD_CHECKS:  # a vault created before plan 3b
        env.sql(statement.format(v=env.vault))
    entry_id = insert_entry(env)
    with pytest.raises(psycopg.errors.CheckViolation):
        env.sql(f"INSERT INTO {env.vault}.evidence (entry_id, kind, quote) VALUES (%s, 'file', 'quoted text')",
                (entry_id,))
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"upgraded vault {env.vault}"
    env.sql(f"INSERT INTO {env.vault}.evidence (entry_id, kind, quote) VALUES (%s, 'file', 'quoted text')",
            (entry_id,))
    env.sql(f"INSERT INTO {env.vault}.review_item (reason, payload, content_hash, origin_project, origin_machine) "
            "VALUES ('import', '{}', 'h', 'p', 'm')")
    again = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert again.stdout.strip() == f"vault {env.vault} is up to date"


def test_upgrade_widens_the_review_reasons(env):
    create(env)
    env.sql(OLD_REASONS.format(v=env.vault))  # a vault created before automatic admission
    no_evidence = (f"INSERT INTO {env.vault}.review_item (reason, payload, content_hash, origin_project, "
                   "origin_machine) VALUES ('no-evidence', '{}', 'h', 'p', 'm')")
    with pytest.raises(psycopg.errors.CheckViolation):
        env.sql(no_evidence)
    result = env.run("vault", "upgrade", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    env.sql(no_evidence)


def test_a_new_vault_accepts_file_evidence_and_import_items(env):
    create(env)
    entry_id = insert_entry(env)
    env.sql(f"INSERT INTO {env.vault}.evidence (entry_id, kind, quote) VALUES (%s, 'file', 'quoted text')",
            (entry_id,))
    env.sql(f"INSERT INTO {env.vault}.review_item (reason, payload, content_hash, origin_project, origin_machine) "
            "VALUES ('import', '{}', 'h', 'p', 'm')")
