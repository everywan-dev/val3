"""`memory_save`: the memory server's third tool, bound to its caller's transcript by the call id (plan 17)."""
import json
import os
import signal
import time
import uuid
from pathlib import Path

import psycopg

import helpers
from helpers import OUTPUT, QUOTE, embedding_vault, make_vault, mcp_batch, mcp_live, save, setup
from test_capture_settles import STATEMENT
from test_mcp import by_id, initialize, request
from viewer import viewing


def fact(**changes):
    """The test fact, in no knowledge index: the answer is the outcome line alone."""
    return helpers.fact(**{"indexes": [], **changes})


SAVE = ("Save durable project knowledge once a result or a decision is settled, when a later session would need it. "
        "Search first and name in `supersedes` an entry it replaces. Quote the evidence the session shows; without "
        "evidence the entry is stored unverified.")
AGAIN = "send the candidate again unchanged, or without its evidence to store it unverified"
MAX_LINE = 65536


def projects(env):
    """The harness's directory of this project's transcripts."""
    return env.home / ".claude" / "projects" / "-project"


def started(env, session=None):
    """The server's environment: the session it was started in."""
    env.environ["CLAUDE_CODE_SESSION_ID"] = session or env.session


def use(call_id, candidate, name="mcp__val3__memory_save", **fields):
    """The assistant record of a memory_save call, as the harness writes it."""
    return {"type": "assistant", "sessionId": "s", **fields, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": call_id, "name": name, "input": {"candidate": candidate}}]}}


def save_call(id, candidate, call_id=None, vault=None):
    params = {"name": "memory_save", "arguments": {"candidate": candidate, **({"vault": vault} if vault else {})}}
    if call_id is not None:
        params["_meta"] = {"claudecode/toolUseId": call_id, "progressToken": id}
    return request(id, "tools/call", params)


def tool_save(env, candidate, call_id=None, vault=None):
    """One memory_save call in a fresh server: (text, isError)."""
    answers = by_id(mcp_batch(env, [initialize(), save_call(1, candidate, call_id, vault)], timeout=60))
    result = answers[1]["result"]
    [content] = result["content"]
    return content["text"], result["isError"]


def events(env, action, vault=None):
    return env.sql(f"SELECT entry_id, session_id, detail FROM {vault or env.vault}.event WHERE action = %s ORDER BY id",
                   (action,))


def entries(env, vault=None):
    return env.sql(f"SELECT id FROM {vault or env.vault}.entry ORDER BY id")


def session_with(env, call_id, candidate=None):
    """The server's session transcript, holding the tool output and the call."""
    started(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    env.append(t, use(call_id, candidate or fact()))
    return t


def test_the_tool_is_listed_with_its_instruction(env):
    env.config()
    [answer, listed] = mcp_batch(env, [initialize(), request(1, "tools/list")])
    assert SAVE in answer["result"]["instructions"]
    [tool] = [tool for tool in listed["result"]["tools"] if tool["name"] == "memory_save"]
    assert SAVE in tool["description"] and '"statement"' in tool["description"]
    assert "48 000" in tool["description"] and "val3 save" in tool["description"]
    assert tool["inputSchema"]["required"] == ["candidate"]
    assert set(tool["inputSchema"]["properties"]) == {"candidate", "vault"}


def test_the_call_in_the_session_file(env):
    make_vault(env)
    session_with(env, "toolu_01")
    assert tool_save(env, helpers.fact(), "toolu_01") == (
        f"admitted {env.vault}#1\nval3: new knowledge index: billing (existing: val3 index)", False)
    assert events(env, "admitted") == [(1, env.session, {"via": "tool"})]
    assert env.sql(f"SELECT quote, session_id FROM {env.vault}.evidence") == [(QUOTE, env.session)]


def test_the_call_in_a_second_session_file(env):
    """After /clear the server's session id is stale: the call is found in the new session's file."""
    make_vault(env)
    started(env)
    env.transcript(user_messages=["before /clear"])
    later = str(uuid.uuid4())
    env.append(env.transcript(tool_outputs=[OUTPUT], session=later), use("toolu_02", fact()))
    assert tool_save(env, fact(), "toolu_02") == (f"admitted {env.vault}#1", False)
    assert events(env, "admitted") == [(1, later, {"via": "tool"})]


def subagent(env, call_id, candidate, prompt="Count the April invoices of the ERP"):
    """A subagent's transcript under the server's session: its prompt, one Bash call, the memory_save call."""
    started(env)
    env.transcript(user_messages=["delegate the count"])
    path = projects(env) / env.session / "subagents" / "agent-a1b2c3.jsonl"
    path.parent.mkdir(parents=True)
    side = {"isSidechain": True, "agentId": "a1b2c3", "sessionId": env.session}
    env.append(path, {"type": "user", **side, "message": {"role": "user", "content": prompt}},
               {"type": "assistant", **side, "message": {"role": "assistant", "content": [
                   {"type": "tool_use", "id": "t0", "name": "Bash", "input": {"command": "psql -c 'SELECT 1'"}}]}},
               {"type": "user", **side, "message": {"role": "user", "content": [
                   {"type": "tool_result", "tool_use_id": "t0", "content": OUTPUT}]}},
               use(call_id, candidate, **side))
    return path


def test_the_call_in_a_subagent_file(env):
    make_vault(env)
    subagent(env, "toolu_03", fact())
    assert tool_save(env, fact(), "toolu_03") == (f"admitted {env.vault}#1", False)
    assert events(env, "admitted") == [(1, env.session, {"via": "tool", "agent": "a1b2c3"})]


def test_verified_carries_via_and_agent(env):
    make_vault(env)
    assert tool_save(env, fact(evidence=[]))[0] == f"admitted unverified {env.vault}#1"
    subagent(env, "toolu_04", fact())
    assert tool_save(env, fact(), "toolu_04") == (f"verified {env.vault}#1", False)
    assert events(env, "verified") == [(1, env.session, {"evidence": 1, "via": "tool", "agent": "a1b2c3"})]


def test_a_subagents_prompt_is_not_the_users_words(env):
    make_vault(env)
    quoted = fact(evidence=[{"kind": "user", "quote": "Count the April invoices of the ERP"}])
    subagent(env, "toolu_05", quoted)
    text, error = tool_save(env, quoted, "toolu_05")
    assert error and text.startswith("discarded: evidence quote not found in the user's messages"), text
    assert AGAIN in text and entries(env) == []


def test_the_record_written_after_the_call_starts(env):
    make_vault(env)
    started(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    with mcp_live(env) as server:
        server.send(initialize())
        server.read()
        server.send(save_call(1, fact(), "toolu_06"))
        time.sleep(5)  # the harness writes the record when the whole assistant message has ended
        env.append(t, use("toolu_06", fact()))
        answer = server.read()
    assert answer["result"] == {"content": [{"type": "text", "text": f"admitted {env.vault}#1"}], "isError": False}


def test_no_call_id(env):
    make_vault(env)
    session_with(env, "toolu_07")
    start = time.monotonic()
    text, error = tool_save(env, fact())
    assert time.monotonic() - start < 3  # no waiting
    assert error and text.startswith("the client sent no call id"), text
    assert AGAIN in text and "--transcript" not in text and "VAL3_TRANSCRIPT" not in text
    assert entries(env) == []


def test_no_evidence_without_a_transcript(env):
    make_vault(env)
    assert tool_save(env, fact(evidence=[])) == (f"admitted unverified {env.vault}#1", False)
    assert events(env, "admitted") == [(1, None, {"via": "tool"})]


def test_no_evidence_takes_the_session_when_the_lookup_gives_one(env):
    make_vault(env)
    session_with(env, "toolu_08", fact(evidence=[]))
    assert tool_save(env, fact(evidence=[]), "toolu_08") == (f"admitted unverified {env.vault}#1", False)
    assert events(env, "admitted") == [(1, env.session, {"via": "tool"})]


def late(text):
    return text.startswith("no transcript of this project holds call toolu_") and "may be late" in text


def test_records_that_only_look_like_the_call(env):
    """The id quoted in other records, a call of another tool with it, and the call in a file linked from outside
    the directory: none of them is the call (one wait of the lookup for all of them)."""
    make_vault(env)
    started(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    env.append(t, {"type": "user", "message": {"role": "user", "content": "the call toolu_09 failed"}},
               use("toolu_other", {"title": "toolu_09"}), use("toolu_09", fact(), name="Bash"),
               {"type": "user", "message": {"role": "user", "content": json.dumps(use("toolu_09", fact()))}})
    outside = env.root / "outside.jsonl"
    outside.write_text("".join(json.dumps(record) + "\n" for record in (
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t0", "name": "Bash", "input": {"command": "cat report.txt"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t0", "content": OUTPUT}]}},
        use("toolu_09", fact()))))
    (projects(env) / f"{uuid.uuid4()}.jsonl").symlink_to(outside)
    start = time.monotonic()
    text, error = tool_save(env, fact(), "toolu_09")
    assert 30 <= time.monotonic() - start < 40
    assert error and late(text) and AGAIN in text, text
    assert "--transcript" not in text and entries(env) == []


def test_a_record_without_its_newline_is_not_read_yet(env):
    make_vault(env)
    started(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    with mcp_live(env) as server:
        server.send(initialize())
        server.read()
        server.send(save_call(1, fact(), "toolu_22"))
        with t.open("a") as handle:
            handle.write(json.dumps(use("toolu_22", fact())))
        try:
            early = server.read(timeout=1.5)
        except AssertionError:
            early = None
        assert early is None, early
        with t.open("a") as handle:
            handle.write("\n")
        answer = server.read()
    assert answer["result"]["content"][0]["text"] == f"admitted {env.vault}#1", answer


def test_a_call_id_with_other_characters_is_no_call_id(env):
    make_vault(env)
    session_with(env, 'toolu_0"1')
    start = time.monotonic()
    text, error = tool_save(env, fact(), 'toolu_0"1')
    assert time.monotonic() - start < 3
    assert error and text.startswith("the client sent no call id"), text


def test_files_that_cannot_be_read_as_transcripts(env):
    make_vault(env)
    session_with(env, "toolu_23")
    os.mkfifo(projects(env) / "pipe.jsonl")  # reading it would block
    (projects(env) / "folder.jsonl").mkdir()
    assert tool_save(env, fact(), "toolu_23") == (f"admitted {env.vault}#1", False)


def test_the_directory_is_resolved_once(env):
    """Resolved at the first save that carries a call id, even when no directory holds the session yet."""
    make_vault(env)
    started(env)
    with mcp_live(env) as server:
        server.send(initialize())
        server.read()
        server.send(save_call(1, fact(), "toolu_24"))
        first = server.read()
        env.append(env.transcript(tool_outputs=[OUTPUT]), use("toolu_25", fact()))
        server.send(save_call(2, fact(), "toolu_25"))
        second = server.read()
    for answer in (first, second):
        assert answer["result"]["isError"] and late(answer["result"]["content"][0]["text"]), answer
    assert entries(env) == []


def test_forged_arguments_change_nothing(env):
    """The call id and the directory come from the request's _meta and the server, never from the arguments."""
    make_vault(env)
    t = session_with(env, "toolu_26")
    forged = {"call": "toolu_26", "directory": str(projects(env)), "transcript": str(t),
              "_meta": {"claudecode/toolUseId": "toolu_26"}}
    answers = by_id(mcp_batch(env, [initialize(), request(1, "tools/call", {
        "name": "memory_save", "arguments": {"candidate": fact(), **forged}})]))
    text = answers[1]["result"]["content"][0]["text"]
    assert answers[1]["result"]["isError"] and text.startswith("the client sent no call id"), text
    text, error = tool_save(env, {**fact(), "_meta": forged["_meta"], "call": "toolu_26"})
    assert error and text.startswith("the client sent no call id"), text  # the lookup comes before validation
    text, error = tool_save(env, {**fact(), "_meta": forged["_meta"]}, "toolu_26")
    assert error and "_meta" in text, text
    elsewhere = env.root / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "other.jsonl").write_text(json.dumps(use("toolu_x", fact())) + "\n")
    answers = by_id(mcp_batch(env, [initialize(), request(1, "tools/call", {
        "name": "memory_save", "_meta": {"claudecode/toolUseId": "toolu_26"},
        "arguments": {"candidate": fact(), "call": "toolu_x", "directory": str(elsewhere)}})]))
    assert answers[1]["result"]["content"][0]["text"] == f"admitted {env.vault}#1", answers
    assert events(env, "admitted") == [(1, env.session, {"via": "tool"})]


def test_the_id_in_two_files(env):
    make_vault(env)
    t = session_with(env, "toolu_10")
    env.append(env.transcript(tool_outputs=[OUTPUT], session=str(uuid.uuid4())), use("toolu_10", fact()))
    text, error = tool_save(env, fact(), "toolu_10")
    assert error and text.startswith("call toolu_10 is in two transcripts"), text
    assert AGAIN in text and entries(env) == []
    assert t.exists()


def test_the_server_session_file_gone(env):
    make_vault(env)
    started(env, "no-such-session")
    env.append(env.transcript(tool_outputs=[OUTPUT]), use("toolu_12", fact()))
    text, error = tool_save(env, fact(), "toolu_12")
    assert error and late(text), text


def test_the_project_directory_when_the_server_session_has_no_file(env):
    """A session cleared before its first message leaves no transcript: the directory is the project's own."""
    make_vault(env)
    started(env, "no-such-session")
    env.environ["CLAUDE_PROJECT_DIR"] = "/project"
    env.append(env.transcript(tool_outputs=[OUTPUT]), use("toolu_12b", fact()))
    assert tool_save(env, fact(), "toolu_12b") == (f"admitted {env.vault}#1", False)


def test_the_projects_directory_follows_claude_config_dir(env):
    make_vault(env)
    t = session_with(env, "toolu_13")
    config = env.root / "claude-config"
    (config / "projects").mkdir(parents=True)
    t.parent.rename(config / "projects" / "-project")
    env.environ["CLAUDE_CONFIG_DIR"] = str(config)
    assert tool_save(env, fact(), "toolu_13") == (f"admitted {env.vault}#1", False)


def test_a_failed_quote(env):
    make_vault(env)
    session_with(env, "toolu_14")
    wrong = fact(evidence=[{"kind": "query", "reproduction": "SELECT 1", "quote": "not printed by anything"}])
    text, error = tool_save(env, wrong, "toolu_14")
    assert error and text.startswith("discarded: evidence quote not found in tool output"), text
    assert AGAIN in text and entries(env) == []
    assert [detail["quote"] for _, _, detail in events(env, "discarded")] == ["not printed by anything"]


def test_an_invalid_candidate(env):
    make_vault(env)
    for arguments, message in (({"candidate": "text"}, "candidate must be"),
                               ({"candidate": {**fact(), "vault": "x"}}, '"vault" is not a candidate key'),
                               ({}, "candidate must be")):
        answers = by_id(mcp_batch(env, [initialize(), request(1, "tools/call",
                                                              {"name": "memory_save", "arguments": arguments})]))
        result = answers[1]["result"]
        assert result["isError"] and message in result["content"][0]["text"], (arguments, result)


def test_a_specialized_vault(env):
    home, special = setup(env)
    session_with(env, "toolu_15")
    assert tool_save(env, fact(), "toolu_15", vault=special) == (f"admitted {special}#1", False)
    assert entries(env, special) == [(1,)] and entries(env, home) == []
    text, error = tool_save(env, fact(evidence=[]), vault="v_elsewhere")
    assert error and "v_elsewhere is not declared" in text, text


def test_a_held_writer_lock_gives_up_within_the_deadline(env):
    make_vault(env)
    with psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname=val3", autocommit=True) as lock:
        lock.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"val3 writer {env.vault}",))
        start = time.monotonic()
        text, error = tool_save(env, fact(evidence=[]))
        assert 4 <= time.monotonic() - start < 10  # 5 s of lock_timeout, timed by the server's wall clock
    assert error and text.startswith("the memory failed: ") and "lock timeout" in text, text
    assert entries(env) == []


def test_a_child_killed_after_the_commit_answers_with_the_ref(env, embedder):
    embedding_vault(env, embedder.url)
    session_with(env, "toolu_16")
    embedder.trickle = True  # the vectors never come: the deadline kills the child after the admission
    with mcp_live(env) as server:
        server.send(initialize())
        server.read()
        start = time.monotonic()
        server.send(save_call(1, fact(), "toolu_16"))
        answer = server.read(timeout=70)
        assert 50 <= time.monotonic() - start < 60
    assert answer["result"] == {"content": [{"type": "text", "text": f"admitted {env.vault}#1; vectors pending"}],
                                "isError": False}
    assert entries(env) == [(1,)]


def children(pid):
    return [int(child) for child in Path(f"/proc/{pid}/task/{pid}/children").read_text().split()]


def test_a_child_killed_from_outside_keeps_its_early_answer(env, embedder):
    """The early answer carries what the admission did not do; a child that exits non-zero after it keeps it."""
    embedding_vault(env, embedder.url)
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    embedder.trickle, posted = True, len(embedder.requests)
    unverified = fact(title="April has 43 invoices", evidence=[], supersedes=[f"{env.vault}#1"])
    with mcp_live(env) as server:
        server.send(initialize())
        server.read()
        server.send(save_call(1, unverified))
        env.wait(lambda: len(embedder.requests) > posted, timeout=15)  # admitted, reported, and waiting for its vectors
        [child] = children(server.proc.pid)
        os.kill(child, signal.SIGKILL)
        answer = server.read()
    assert answer["result"] == {"content": [{"type": "text", "text": (
        f"admitted unverified {env.vault}#2; vectors pending\nval3: warning: {env.vault}#2 does not supersede "
        f"{env.vault}#1: an unverified entry cannot supersede an entry with evidence or attestation")}],
        "isError": False}


def test_the_vectors_and_neighbours_after_a_save(env, embedder):
    embedding_vault(env, embedder.url)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(statement=STATEMENT), t).returncode == 0
    session_with(env, "toolu_17")
    near = fact(title="April has 43 invoices", statement=STATEMENT.replace("42", "43"))
    text, error = tool_save(env, near, "toolu_17")
    assert not error and text.startswith(f"admitted {env.vault}#2\n"), text
    assert f"val3: near {env.vault}#1 (" in text
    assert env.sql(f"SELECT count(*) FROM {env.vault}.embedding WHERE entry_id = 2") == [(1,)]


def test_save_then_capture_the_same_fact(env, embedder):
    """The extractor reads the session that saved: the same fact is a duplicate, a paraphrase a repeat."""
    embedding_vault(env, embedder.url)
    stored = fact(statement=STATEMENT)
    t = session_with(env, "toolu_18", stored)
    assert tool_save(env, stored, "toolu_18") == (f"admitted {env.vault}#1", False)
    paraphrase = fact(title="There are 42 April invoices", statement=STATEMENT.replace("holds", "has"))
    env.respond([{"note": None, "candidate": stored}, {"note": None, "candidate": paraphrase}],
                {"decisions": [{"candidate": 1, "decision": "repeat", "refs": [f"{env.vault}#1"]}]})
    result = env.run("extract", str(t))
    assert result.returncode == 0, result.stderr
    assert "admitted 0, duplicate 1, repeat 1," in result.stdout, result.stdout
    assert f"\n#### Close entry {env.vault}#1 (similarity " in env.calls()[1]["stdin"]
    assert entries(env) == [(1,)]


def test_the_usage_line(env):
    make_vault(env)
    session_with(env, "toolu_19")
    assert tool_save(env, fact(), "toolu_19")[1] is False
    assert tool_save(env, fact(title="Another"))[1] is True
    lines = [json.loads(line) for line in (env.state / "usage" / "project.jsonl").read_text().splitlines()]
    assert [(x["surface"], x["outcome"], x["status"], x["ref"], x["refs"], x["vaults"]) for x in lines] == [
        ("memory_save", "ok", "admitted", f"{env.vault}#1", [], [env.vault]),
        ("memory_save", "error", "no-call-id", None, [], [env.vault])]
    with viewing(env) as view:  # a save is neither a search nor a read
        data = view.json("api/usage")
    assert (data["searches"], data["reads"], data["by_surface"]) == (0, 0, {"memory_save": 2})


def sized(extra):
    """A memory_save request line of exactly MAX_LINE + extra bytes, its statement not ASCII."""
    def line(statement):
        return json.dumps(save_call(1, fact(evidence=[], statement=statement)), ensure_ascii=False).encode()
    statement = "é" * ((MAX_LINE - len(line(""))) // 2)
    statement += "a" * (MAX_LINE + extra - len(line(statement)))
    assert len(line(statement)) == MAX_LINE + extra
    return line(statement)


def test_the_size_boundary(env):
    make_vault(env)
    answers = mcp_batch(env, [initialize(), sized(0), sized(1), request(2, "ping")], timeout=40)
    assert answers[1] == {"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text", "text": f"admitted unverified {env.vault}#1"}], "isError": False}}
    assert answers[2]["id"] is None and answers[2]["error"]["code"] == -32600
    assert answers[3]["result"] == {}
    [(statement,)] = env.sql(f"SELECT statement FROM {env.vault}.entry")
    assert statement.startswith("éé") and len(statement.encode()) > 60000
