"""The usage log: one line per recall, show, memory_search and memory_read call, written at the public boundary."""
import fcntl
import json
import os
import shutil
import time
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from helpers import OUTPUT, fact, make_vault, mcp_live, save
from test_mcp import call, initialize, read, search
from viewer import viewing


def log(env):
    return env.state / "usage" / "project.jsonl"


def logged(env):
    path = log(env)
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def one_fact(env):
    make_vault(env)
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0


def test_recall_and_show_are_logged(env):
    one_fact(env)
    env.environ["VAL3_SESSION_ID"] = "s-1"
    assert env.run("recall", "invoices", "April").returncode == 0
    assert env.run("recall", "nothing", "--limit", "3").stdout == "no matches\n"
    assert env.run("show", f"{env.vault}#1").returncode == 0
    assert env.run("show", f"{env.vault}#9").returncode == 1
    assert env.run("recall", "invoices", "--service", "val3test").returncode == 0  # another database: not logged
    assert env.run("show", f"{env.vault}#1", "--service", "val3test").returncode == 0
    lines = logged(env)
    assert [(x["surface"], x["refs"], x["outcome"], x["limit"], x["words"], x["query"]) for x in lines] == [
        ("recall", [f"{env.vault}#1"], "ok", 10, 2, "invoices April"), ("recall", [], "empty", 3, 1, "nothing"),
        ("show", [f"{env.vault}#1"], "ok", None, None, None), ("show", [], "error", None, None, None)]
    assert oct(log(env).stat().st_mode & 0o777) == "0o600"
    first = lines[0]
    assert first["v"] == 1 and first["session"] == "s-1" and first["project"] == "project" and first["machine"]
    assert first["vaults"] == [env.vault] and len(first["call"]) == 32 and first["at"].endswith("+00:00")
    assert len({x["call"] for x in lines}) == 4
    assert oct(log(env).stat().st_mode & 0o777) == "0o600"


def test_the_memory_tools_are_logged_by_the_server(env):
    one_fact(env)
    text, error = search(env, query="invoices", limit=4)
    assert not error, text
    assert read(env, f"{env.vault}#1")[1] is False
    assert read(env, f"{env.vault}#9")[1] is True
    lines = logged(env)
    assert [(x["surface"], x["refs"], x["outcome"], x["limit"], x["query"], x["vaults"]) for x in lines] == [
        ("memory_search", [f"{env.vault}#1"], "ok", 4, "invoices", [env.vault]),
        ("memory_read", [f"{env.vault}#1"], "ok", None, None, [env.vault]),
        ("memory_read", [], "error", None, None, [env.vault])]


def test_a_tool_call_killed_at_its_deadline_is_logged_as_timeout(env):
    one_fact(env)
    with mcp_live(env) as server:
        server.send(initialize())
        server.read()
        with psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname=val3") as lock:
            lock.execute(f"LOCK TABLE {env.vault}.entry IN ACCESS EXCLUSIVE MODE")
            server.send(call(1, "memory_search", {"query": "invoices"}))
            assert server.read()["result"]["isError"] is True
    [line] = logged(env)
    assert (line["surface"], line["outcome"], line["refs"], line["words"], line["limit"]) == (
        "memory_search", "timeout", [], 1, 5)


def outputs(env):
    return [(r.returncode, r.stdout, r.stderr) for r in (
        env.run("recall", "invoices"), env.run("show", f"{env.vault}#1"), env.run("show", f"{env.vault}#9"))]


def test_a_state_directory_that_cannot_be_written_changes_nothing(env):
    one_fact(env)
    normal = outputs(env)
    assert len(logged(env)) == 3
    blocked = env.root / "a-file"
    blocked.write_text("")
    env.environ["XDG_STATE_HOME"] = str(blocked)  # the state directory would be under a regular file
    assert outputs(env) == normal
    env.environ["XDG_STATE_HOME"] = str(env.root / "missing" / "state")
    assert outputs(env) == normal
    assert len([json.loads(x) for x in (env.root / "missing" / "state" / "val3" / "usage" / "project.jsonl")
                .read_text().splitlines()]) == 3


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
def test_a_read_only_state_directory_changes_nothing(env):
    one_fact(env)
    normal = outputs(env)
    shutil.rmtree(env.state / "usage")
    env.state.chmod(0o500)
    try:
        assert outputs(env) == normal
    finally:
        env.state.chmod(0o700)
    assert not (env.state / "usage").exists()


def test_a_held_lock_delays_a_line_a_bounded_time(env):
    one_fact(env)
    normal = outputs(env)
    with open(env.state / "usage" / "project.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        started = time.monotonic()
        assert outputs(env) == normal
        assert time.monotonic() - started < 10
    assert len(logged(env)) == 3  # the three calls under the lock wrote nothing


def test_old_lines_are_dropped_and_incomplete_ones_skipped(env):
    one_fact(env)
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    log(env).parent.mkdir(parents=True)
    line = {"v": 1, "session": None, "project": "project", "machine": "m", "surface": "recall", "call": "c",
            "vaults": [env.vault], "limit": 10, "words": 1, "outcome": "ok"}
    log(env).write_text(json.dumps({**line, "at": old, "refs": [f"{env.vault}#1"]}) + "\n"
                        + json.dumps({**line, "at": recent, "refs": [f"{env.vault}#1"]}) + "\n")
    assert env.run("recall", "invoices").returncode == 0
    lines = logged(env)
    assert [x["at"] for x in lines][0] == recent and len(lines) == 2
    with open(log(env), "a") as handle:
        handle.write('{"v": 1, "at": "' + recent + '", "surf')  # a line being written
    with viewing(env) as view:
        data = view.json("api/usage")
    assert data["searches"] == 2 and data["reads"] == 0 and data["since"] == recent[:19] + "+00:00"


def test_the_view_counts_what_searches_returned(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    assert save(env, fact(title="Rules of the ledger", statement="The ledger closes monthly.",
                          keywords=["ledger"]), t).returncode == 0
    with viewing(env) as view:
        empty = view.json("api/usage")
        assert empty["log"] is False and empty["since"] is None and empty["returned"] == {}
        assert {r["ref"]: r["returned"] for r in view.json("api/entries")["rows"]} == {
            f"{env.vault}#1": None, f"{env.vault}#2": None}
        for _ in range(2):
            assert env.run("recall", "invoices").returncode == 0
        assert env.run("show", f"{env.vault}#2").returncode == 0
        data = view.json("api/usage")
        assert data["log"] is True and data["searches"] == 2 and data["reads"] == 1
        assert data["returned"] == {f"{env.vault}#1": 2} and data["read"] == {f"{env.vault}#2": 1}
        assert data["by_surface"] == {"recall": 2, "show": 1} and data["outcomes"] == {"ok": 3}
        [vault] = data["vaults"]
        assert vault["ok"] and vault["live"] == 2 and vault["live_not_seen"] == 1
        assert data["most_returned"] == [{"ref": f"{env.vault}#1", "title": "April has 42 invoices", "times": 2}]
        rows = {r["ref"]: (r["returned"], r["read"]) for r in view.json("api/entries")["rows"]}
        assert rows == {f"{env.vault}#1": (2, 0), f"{env.vault}#2": (0, 1)}
        entry = view.json(f"api/entry?ref={env.vault}%231")["entry"]
        assert (entry["returned"], entry["read"]) == (2, 0)
