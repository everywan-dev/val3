import json
import subprocess
import sys
import time

import psycopg
import pytest

from helpers import OUTPUT, fact, old_vault, recipe, save

NEW_QUOTE = "invoice_count\n-------------\n          43"
NEWER = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.",
             evidence=[{"kind": "query", "reproduction": "SELECT count(*) AS invoice_count FROM invoice",
                        "quote": NEW_QUOTE}])
LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
STARTED = []


@pytest.fixture(autouse=True)
def reap_background_processes():
    yield
    while STARTED:   # a failed assertion or a timeout must not leave a CLI process or its pipes behind
        proc = STARTED.pop()
        proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def setup(env):
    old_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT, NEW_QUOTE + "\n(1 row)"])
    assert save(env, fact(), t).returncode == 0
    return t


def background(env, *args, stdin=""):
    proc = subprocess.Popen([sys.executable, "-m", "val3", *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=env.project, env=env.environ)
    STARTED.append(proc)
    proc.stdin.write(stdin)
    proc.stdin.close()
    return proc


def holding_the_lock(env):
    conn = psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname=val3")
    conn.execute(LOCK, (f"val3 writer {env.vault}",))
    return conn


def test_save_and_approve_wait_for_the_writer_lock(env):
    t = setup(env)
    save(env, recipe(f"{env.vault}#1"), t)
    conn = holding_the_lock(env)
    try:
        saving = background(env, "save", "--transcript", str(t), "--session", env.session,
                            stdin=json.dumps({**NEWER, "title": "Unrelated fact"}))
        approving = background(env, "review", "approve", "1")
        time.sleep(1.5)
        assert saving.poll() is None and approving.poll() is None
    finally:
        conn.rollback()
        conn.close()
    assert saving.wait(timeout=30) == 0, saving.stderr.read()
    assert approving.wait(timeout=30) == 0, approving.stderr.read()


def test_approval_racing_a_supersession_is_refused_or_flagged(env):
    t = setup(env)
    save(env, recipe(f"{env.vault}#1"), t)
    conn = holding_the_lock(env)
    try:
        approving = background(env, "review", "approve", "1")
        superseding = background(env, "save", "--transcript", str(t), "--session", env.session,
                                 stdin=json.dumps({**NEWER, "supersedes": f"{env.vault}#1"}))
        time.sleep(1.0)
    finally:
        conn.rollback()
        conn.close()
    assert superseding.wait(timeout=30) == 0, superseding.stderr.read()
    approving.wait(timeout=30)
    if approving.returncode == 0:   # approved first: the supersession must mark the new recipe
        [(recipe_id,)] = env.sql(f"SELECT entry_id FROM {env.vault}.review_decision WHERE review_id = 1")
        assert f"TO RE-CHECK: cites {env.vault}#1, which is superseded by" in \
            env.run("show", f"{env.vault}#{recipe_id}").stdout
    else:                           # superseded first: the approval must be refused
        assert "was superseded by" in approving.stderr.read()


def test_a_superseding_save_that_waited_for_the_lock_takes_one_instant(env):
    t = setup(env)
    conn = holding_the_lock(env)
    try:
        superseding = background(env, "save", "--transcript", str(t), "--session", env.session,
                                 stdin=json.dumps({**NEWER, "supersedes": f"{env.vault}#1"}))
        time.sleep(1.0)
        assert superseding.poll() is None
    finally:
        conn.rollback()
        conn.close()
    assert superseding.wait(timeout=30) == 0, superseding.stderr.read()
    v = env.vault
    assert env.sql(f"""
        SELECT count(DISTINCT t) FROM (
            SELECT created_at FROM {v}.entry WHERE id = 2 UNION ALL
            SELECT created_at FROM {v}.evidence WHERE entry_id = 2 UNION ALL
            SELECT created_at FROM {v}.supersession WHERE new_id = 2) AS instants (t)""") == [(1,)]
    # what `val3 eval` replays at that instant: the new entry alone
    assert env.sql(f"""
        SELECT e.id FROM {v}.entry e, (SELECT created_at FROM {v}.entry WHERE id = 2) AS at (t)
        WHERE e.created_at <= at.t
          AND NOT EXISTS (SELECT 1 FROM {v}.supersession s WHERE s.old_id = e.id AND s.created_at <= at.t)
        ORDER BY e.id""") == [(2,)]
