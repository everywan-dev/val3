import time

from helpers import OUTPUT, before_0012, embedding_vault, fact, make_vault, recipe, save

DOWN = "http://127.0.0.1:1"
TEXT = "April has 42 invoices\nThe ERP holds 42 invoices dated April 2026."
ENTRY = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
         "VALUES ('fact', 't', 's', 'h1', 'p', 'm')")
MANY = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
        "SELECT 'fact', 'note ' || n, 'statement ' || n, 'h' || n, 'p', 'm' FROM generate_series(1, %s) n")


def vectors(env):
    return env.sql(f"SELECT entry_id, model, vector_dims(vector) FROM {env.vault}.embedding ORDER BY entry_id")


def test_save_stores_a_vector_of_title_and_statement(env, embedder):
    embedding_vault(env, embedder.url)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    assert result.stderr == "val3: new knowledge index: billing (existing: val3 index)\n"  # and no warning
    assert vectors(env) == [(1, "bge-m3", 256)]
    assert embedder.inputs == [TEXT]
    assert embedder.requests[0]["model"] == "bge-m3"
    assert embedder.requests[0]["keep_alive"] == "24h"


def test_save_with_the_service_down_admits_and_warns(env):
    embedding_vault(env, DOWN)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert "warning: no vector stored" in result.stderr and "val3 embed" in result.stderr
    assert vectors(env) == []


def test_embed_fills_the_gaps(env, embedder):
    embedding_vault(env, DOWN)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    env.config(embedding={"url": embedder.url})
    first = env.run("embed")
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "embedded 1 entries"
    assert env.run("embed").stdout.strip() == "embedded 0 entries"
    assert vectors(env) == [(1, "bge-m3", 256)]


def test_admission_embeds_one_batch_newest_first(env, embedder):
    embedding_vault(env, embedder.url)
    env.sql(MANY.format(v=env.vault), (20,))
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.stdout.strip() == f"admitted {env.vault}#21"
    assert [row[0] for row in vectors(env)] == list(range(6, 22))
    assert env.run("embed").stdout.strip() == "embedded 5 entries"


def test_a_new_model_build_gets_its_own_vectors(env, embedder):
    embedding_vault(env, embedder.url)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    embedder.digest = "b" * 64
    assert env.run("embed").stdout.strip() == "embedded 1 entries"
    assert env.sql(f"SELECT digest, text_version, dims FROM {env.vault}.embedding ORDER BY created_at") == [
        ("a" * 64, 1, 256), ("b" * 64, 1, 256)]


def test_a_model_the_service_lacks_admits_and_warns(env, embedder):
    embedding_vault(env, embedder.url, model="other-model")
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0
    assert "model other-model is not available" in result.stderr
    assert vectors(env) == []


def test_a_vault_error_while_storing_vectors_keeps_the_admission(env, embedder):
    embedding_vault(env, embedder.url)
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    assert env.run("vault", "grant", env.vault, env.writer, "--write", "--service", "val3test").returncode == 0
    env.sql(f"REVOKE INSERT ON {env.vault}.embedding FROM {env.writer}")
    env.config(service="val3writer", embedding={"url": embedder.url})
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert "warning: no vector stored" in result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]
    assert vectors(env) == []


KILL_ON_INSERT = """
    CREATE FUNCTION {v}.kill_backend() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM pg_terminate_backend(pg_backend_pid());
        PERFORM pg_sleep(5);
        RETURN NEW;
    END $$;
    CREATE TRIGGER kill_on_insert BEFORE INSERT ON {v}.embedding
        FOR EACH ROW EXECUTE FUNCTION {v}.kill_backend()"""


def test_a_lost_connection_while_storing_vectors_keeps_the_admission(env, embedder):
    embedding_vault(env, embedder.url)
    env.sql(KILL_ON_INSERT.format(v=env.vault))
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert "warning: no vector stored" in result.stderr and "val3 embed" in result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]
    assert vectors(env) == []


def test_extraction_with_the_service_down_keeps_the_admission(env):
    embedding_vault(env, DOWN)
    t = env.transcript(tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": fact()}])
    result = env.run("extract", str(t))
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]
    assert vectors(env) == []


def test_nothing_admitted_computes_no_vectors(env, embedder):
    embedding_vault(env, embedder.url)
    before_0012(env)  # the recipe is queued
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    assert save(env, recipe(f"{env.vault}#1"), t).stdout.startswith("queued for review")
    assert env.run("tool", "add", "code-graph", "--type", "cli", "--description", "d").returncode == 0
    env.config(embedding={"url": DOWN})
    again = [save(env, fact(), t), save(env, recipe(f"{env.vault}#1"), t),
             env.run("tool", "add", "code-graph", "--type", "cli", "--description", "d")]
    assert [r.stdout.split()[0] for r in again] == ["duplicate", "already", "duplicate"]
    assert [r.stderr for r in again] == ["", "", ""]
    assert [r.returncode for r in again] == [0, 0, 0]


def test_embed_needs_the_embedding_table(env):
    make_vault(env)
    result = env.run("embed")
    assert result.returncode == 1
    assert "no [embedding] table" in result.stderr


def test_embed_with_the_service_down_fails(env):
    embedding_vault(env, DOWN)
    env.sql(ENTRY.format(v=env.vault))
    result = env.run("embed")
    assert result.returncode == 1
    assert "embedding service" in result.stderr


def test_unusable_answers_store_nothing(env, embedder):
    embedding_vault(env, embedder.url)
    embedder.zero = True
    first = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert first.returncode == 0
    assert "unexpected answer" in first.stderr
    embedder.zero, embedder.status = False, 500
    second = env.run("embed")
    assert second.returncode == 1
    assert "HTTP 500" in second.stderr
    assert vectors(env) == []


def test_tool_add_and_review_approval_store_vectors(env, embedder):
    embedding_vault(env, embedder.url)
    before_0012(env)  # the recipe is approved on the review list
    assert env.run("tool", "add", "code-graph", "--type", "cli",
                   "--description", "Answers which module overrides a method.").returncode == 0
    t = env.transcript(tool_outputs=[OUTPUT])
    save(env, fact(), t)
    save(env, recipe(f"{env.vault}#2"), t)
    assert env.run("review", "approve", "1").returncode == 0
    assert [row[0] for row in vectors(env)] == [1, 2, 3]


def test_extraction_stores_vectors(env, embedder):
    embedding_vault(env, embedder.url)
    t = env.transcript(tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": fact()}])
    result = env.run("extract", str(t))
    assert result.returncode == 0, result.stderr
    assert vectors(env) == [(1, "bge-m3", 256)]


def test_vault_without_the_embedding_table_warns_on_save(env, embedder):
    embedding_vault(env, embedder.url)
    env.sql(f"DROP TABLE {env.vault}.embedding")
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0
    assert f"val3 vault upgrade {env.vault}" in result.stderr


def test_embedding_settings_are_checked(env):
    make_vault(env)
    for line, message in (("dims = 3", "may only hold"), ("floor = 1.5", "floor must be"),
                          ('url = "ftp://x"', "url must be"), ('model = ""', "model must be")):
        (env.project / "val3.toml").write_text(
            f'service = "val3test"\n[vaults]\nhome = "{env.vault}"\n[embedding]\n{line}\n')
        result = env.run("recall", "invoices")
        assert result.returncode == 1 and message in result.stderr, line


def test_neighbour_must_be_between_0_and_1(env):
    make_vault(env)
    for value in ("0", "1", "1.5", "true", '"0.8"'):
        (env.project / "val3.toml").write_text(
            f'service = "val3test"\n[vaults]\nhome = "{env.vault}"\n[embedding]\nneighbour = {value}\n')
        result = env.run("recall", "invoices")
        assert result.returncode == 1 and "[embedding] neighbour must be" in result.stderr, value


def test_superseded_entries_get_vectors_too(env, embedder):
    # `val3 eval` replays past moments, when a superseded entry was still live.
    embedding_vault(env, DOWN)
    t = env.transcript(tool_outputs=[OUTPUT])
    save(env, fact(), t)
    save(env, fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.",
                   supersedes=f"{env.vault}#1"), t)
    env.config(embedding={"url": embedder.url})
    assert env.run("embed").stdout.strip() == "embedded 2 entries"


def test_one_deadline_bounds_digest_and_embedding_after_an_admission(env, embedder):
    embedding_vault(env, embedder.url)
    embedder.tags_delay = 4.0
    embedder.delay = 4.0
    started = time.monotonic()
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    elapsed = time.monotonic() - started
    assert result.returncode == 0
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert "warning: no vector stored" in result.stderr and "timed out" in result.stderr
    assert elapsed < 11.5
    assert vectors(env) == []
