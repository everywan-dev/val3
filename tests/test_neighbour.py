"""The neighbour check: an entry whose vector is close to a live entry of the same kind is paired with it."""
import subprocess
import sys
import threading
import time

import psycopg

from helpers import OUTPUT, embedding_vault, fact, make_vault, save

DOWN = "http://127.0.0.1:1"
TWIN = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.")
VECTOR_BATCH = 16  # what `val3 save` embeds after an admission
FILLERS = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
           "SELECT 'preference', 'note ' || n, 'statement ' || n, 'f' || n, 'p', 'm' FROM generate_series(1, %s) n")
OLD_SCHEMA = "DROP TABLE {v}.settlement, {v}.neighbour, {v}.neighbour_exempt"  # a vault created before this plan


def pairs(env):
    return env.sql(f"SELECT entry_id, other_id FROM {env.vault}.neighbour ORDER BY entry_id, other_id")


def save_both(env, *candidates):
    t = env.transcript(tool_outputs=[OUTPUT])
    for candidate in candidates or (fact(), TWIN):
        result = save(env, candidate, t)
        assert result.returncode == 0 and result.stdout.startswith("admitted"), result.stdout + result.stderr


def embed(env):
    result = env.run("embed")
    assert result.returncode == 0, result.stderr
    return result


def test_a_close_entry_of_the_same_kind_is_a_neighbour(env, embedder):
    embedding_vault(env, embedder.url)
    save_both(env, fact(), TWIN, fact(kind="decision"))
    rows = env.sql(f"SELECT entry_id, other_id, score FROM {env.vault}.neighbour")
    assert [(a, b) for a, b, _ in rows] == [(2, 1)]
    assert 0.70 <= rows[0][2] < 1


def test_the_threshold_is_a_setting(env, embedder):
    embedding_vault(env, embedder.url, neighbour=0.99)
    save_both(env)
    assert pairs(env) == []


def test_two_new_entries_embedded_together_are_paired(env, embedder):
    embedding_vault(env, DOWN)
    save_both(env)
    env.config(embedding={"url": embedder.url})
    assert embed(env).stdout.strip() == "embedded 2 entries"
    assert pairs(env) == [(2, 1)]


def test_upgraded_vault_entries_are_never_paired(env, embedder):
    embedding_vault(env, DOWN)
    env.sql(OLD_SCHEMA.format(v=env.vault))
    save_both(env)
    assert env.run("vault", "upgrade", env.vault).returncode == 0
    env.config(embedding={"url": embedder.url})
    assert embed(env).stdout.strip() == "embedded 2 entries"
    assert pairs(env) == []
    embedder.digest = "b" * 64  # a new model build
    assert embed(env).stdout.strip() == "embedded 2 entries"
    assert pairs(env) == []


def test_a_new_entry_embedded_before_its_old_twin_is_paired(env, embedder):
    embedding_vault(env, embedder.url)
    env.sql(OLD_SCHEMA.format(v=env.vault))
    save_both(env, fact())
    assert env.run("vault", "upgrade", env.vault).returncode == 0
    embedder.digest = "b" * 64  # a new model build: the old fact has no vector in the new space
    save_both(env, TWIN)
    embed(env)
    assert pairs(env) == [(2, 1)]


def test_concurrent_vector_stores_find_the_pair(env, embedder):
    embedding_vault(env, DOWN)
    save_both(env)
    env.config(embedding={"url": embedder.url})
    embedder.delay = 0.5  # both processes ask for vectors before either stores one
    procs = [subprocess.Popen([sys.executable, "-m", "val3", "embed"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, cwd=env.project, env=env.environ)
             for _ in range(2)]
    for proc in procs:
        _, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
    assert pairs(env) == [(2, 1)]


def test_embed_waits_for_the_writer_lock(env, embedder):
    embedding_vault(env, DOWN)
    save_both(env)
    env.config(embedding={"url": embedder.url})
    conn = psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname=val3", autocommit=True)
    try:
        conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"val3 writer {env.vault}",))
        proc = subprocess.Popen([sys.executable, "-m", "val3", "embed"], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, cwd=env.project, env=env.environ)
        try:
            time.sleep(1.5)
            assert proc.poll() is None
            assert env.sql(f"SELECT count(*) FROM {env.vault}.embedding") == [(0,)]
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (f"val3 writer {env.vault}",))
        _, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
    finally:
        conn.close()
    assert pairs(env) == [(2, 1)]


def test_entries_admitted_without_embedding_are_exempt(env, embedder):
    make_vault(env)
    save_both(env)
    assert env.sql(f"SELECT entry_id FROM {env.vault}.neighbour_exempt ORDER BY 1") == [(1,), (2,)]
    env.config(embedding={"url": embedder.url})
    assert embed(env).stdout.strip() == "embedded 2 entries"
    assert pairs(env) == []


def test_an_upgrade_while_vectors_are_requested_still_pairs(env, embedder):
    embedding_vault(env, DOWN)
    env.sql(OLD_SCHEMA.format(v=env.vault))
    save_both(env, fact())
    env.config(embedding={"url": embedder.url})
    embedder.hold = release = threading.Event()
    proc = subprocess.Popen([sys.executable, "-m", "val3", "embed"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=env.project, env=env.environ)
    try:
        env.wait(lambda: embedder.requests)  # it chose its entries and asks for the vector of #1
        assert env.run("vault", "upgrade", env.vault).returncode == 0
        env.sql(FILLERS.format(v=env.vault), (VECTOR_BATCH - 1,))  # the twin's save embeds itself and these only
        save_both(env, TWIN)
        assert env.sql(f"SELECT count(*) FROM {env.vault}.embedding WHERE entry_id = 17") == [(1,)]
    finally:
        release.set()
    _, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err
    assert env.sql(f"SELECT entry_id FROM {env.vault}.neighbour WHERE other_id = 1") == [(17,)]
