import json

from helpers import OUTPUT, fact, make_vault


def capture(env, transcript, session=None, cwd=None, **environ):
    data = {"session_id": session or env.session, "transcript_path": str(transcript),
            "cwd": str(cwd or env.project), "hook_event_name": "SessionEnd", "reason": "other"}
    return env.run("hook", "capture", input=json.dumps(data), environ=environ)


def queued(env):
    return sorted((env.state / "queue").glob("*.json"))


def log(env):
    path = env.state / "extract.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def entries(env):
    return env.sql(f"SELECT count(*) FROM {env.vault}.entry")[0][0]


def leave_entry(env, transcript, attempts=0):
    queue = env.state / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    (queue / "1-1.json").write_text(json.dumps(
        {"session_id": env.session, "transcript_path": str(transcript), "cwd": str(env.project),
         "event": "SessionEnd", "queued_at": 0, "attempts": attempts}))


def test_session_end_is_queued_and_drained_in_the_background(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": fact()}])
    result = capture(env, t)
    assert result.returncode == 0 and result.stdout == ""
    env.wait(lambda: entries(env) == 1 and not queued(env))
    assert [line["outcome"] for line in log(env)] == ["done"]
    assert log(env)[0]["admitted"] == 1 and log(env)[0]["session"] == env.session


def test_capture_does_nothing_outside_adopted_projects_or_inside_the_extractor(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    elsewhere = env.root / "elsewhere"
    elsewhere.mkdir()
    assert capture(env, t, cwd=elsewhere).returncode == 0
    assert capture(env, t, VAL3_EXTRACTING="1").returncode == 0
    assert queued(env) == [] and env.calls() == []


def test_session_without_a_transcript_is_not_queued(env):
    # The harness writes no transcript for a session closed before its first message.
    make_vault(env)
    assert capture(env, env.root / "never-written.jsonl").returncode == 0
    assert queued(env) == [] and env.calls() == []
    assert "queue empty" in env.run("queue").stdout


def test_two_sessions_captured_together_are_both_drained(env):
    make_vault(env)
    first = env.transcript(tool_outputs=[OUTPUT], session="session-a")
    # a record of its own first: lines identical to the first session's would be skipped as already processed
    second = env.transcript(user_messages=["Count them again."], tool_outputs=[OUTPUT], session="session-b")
    env.respond([{"note": None, "candidate": fact()}],
                [{"note": None, "candidate": fact(title="April invoices counted", statement="April: 42 invoices.")}])
    capture(env, first, session="session-a")
    capture(env, second, session="session-b")
    env.wait(lambda: entries(env) == 2 and not queued(env))
    assert sorted(line["session"] for line in log(env) if line["outcome"] == "done") == ["session-a", "session-b"]


def test_server_down_keeps_entries_without_spending_attempts(env):
    env.config(service="val3down")
    t = env.transcript(tool_outputs=[OUTPUT])
    capture(env, t)
    env.wait(lambda: any(line["outcome"] == "stop" for line in log(env)))
    assert len(queued(env)) == 1
    assert json.loads(queued(env)[0].read_text())["attempts"] == 0
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    env.respond([{"note": None, "candidate": fact()}])
    assert env.run("drain").stdout.strip() == "drained 1 queued sessions"
    assert entries(env) == 1 and queued(env) == []


def test_failed_extractions_are_retried_then_set_aside(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    (env.fake / "exit").write_text("1")
    leave_entry(env, t)
    for attempt in (1, 2):
        assert env.run("drain").stdout.strip() == "drained 0 queued sessions"
        assert json.loads(queued(env)[0].read_text())["attempts"] == attempt
    env.run("drain")
    assert queued(env) == []
    listing = env.run("queue").stdout
    assert "failed" in listing and "extractor exited 1" in listing
    assert [line["outcome"] for line in log(env)] == ["retry", "retry", "failed"]


def test_missing_transcript_is_set_aside_and_the_queue_moves_on(env):
    make_vault(env)
    leave_entry(env, env.root / "gone.jsonl")
    t = env.transcript(tool_outputs=[OUTPUT], session="session-b")
    queue = env.state / "queue"
    (queue / "2-1.json").write_text(json.dumps(
        {"session_id": "session-b", "transcript_path": str(t), "cwd": str(env.project),
         "event": "SessionEnd", "queued_at": 0, "attempts": 0}))
    env.respond([{"note": None, "candidate": fact()}])
    assert env.run("drain").stdout.strip() == "drained 1 queued sessions"
    assert "cannot read transcript" in env.run("queue").stdout
    assert entries(env) == 1


def test_session_start_drains_left_over_entries(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    leave_entry(env, t)
    env.respond([{"note": None, "candidate": fact()}])
    data = {"session_id": "next-session", "transcript_path": str(t), "cwd": str(env.project),
            "hook_event_name": "SessionStart", "source": "startup"}
    assert env.run("hook", "session-start", input=json.dumps(data)).returncode == 0
    env.wait(lambda: entries(env) == 1 and not queued(env))


def test_queue_lists_pending_entries(env):
    make_vault(env)
    leave_entry(env, env.transcript(tool_outputs=[OUTPUT]))
    listing = env.run("queue").stdout
    assert f"pending  1-1.json  session {env.session}  SessionEnd  attempts 0" in listing
    (env.state / "queue" / "1-1.json").unlink()
    assert env.run("queue").stdout.strip() == "queue empty"


def test_an_undecodable_transcript_does_not_stop_the_queue(env):
    make_vault(env)
    broken = env.transcript(tool_outputs=[OUTPUT], session="session-a")
    with broken.open("ab") as handle:
        handle.write(b'{"type": "user", "message": {"content": "caf\xc3')
    healthy = env.transcript(tool_outputs=[OUTPUT], session="session-b")
    queue = env.state / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    for name, session, path in (("1-1.json", "session-a", broken), ("2-1.json", "session-b", healthy)):
        (queue / name).write_text(json.dumps({"session_id": session, "transcript_path": str(path),
                                              "cwd": str(env.project), "event": "SessionEnd",
                                              "queued_at": 0, "attempts": 0}))
    env.respond([{"note": None, "candidate": fact()}],
                [{"note": None, "candidate": fact(title="April invoices counted", statement="April: 42 invoices.")}])
    result = env.run("drain")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "drained 2 queued sessions"


def test_one_unreachable_project_does_not_stop_the_others(env):
    make_vault(env)
    other = env.root / "other"
    other.mkdir()
    (other / "val3.toml").write_text('service = "nosuch"\n[vaults]\nhome = "v_other"\n')
    t = env.transcript(tool_outputs=[OUTPUT])
    queue = env.state / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    for name, cwd in (("1-1.json", other), ("2-1.json", env.project)):
        (queue / name).write_text(json.dumps({"session_id": env.session, "transcript_path": str(t), "cwd": str(cwd),
                                              "event": "SessionEnd", "queued_at": 0, "attempts": 0}))
    env.respond([{"note": None, "candidate": fact()}])
    assert env.run("drain").stdout.strip() == "drained 1 queued sessions"
    assert entries(env) == 1
    listing = env.run("queue").stdout
    assert "pending  1-1.json" in listing and "nosuch" in listing and "attempts 0" in listing


def test_the_drainer_imports_nothing_from_its_working_directory(env):
    """The drainer runs in the state directory: a module left there is not imported in place of the library's."""
    make_vault(env)
    env.state.mkdir(parents=True, exist_ok=True)
    marker = env.state / "imported"
    (env.state / "hashlib.py").write_text(
        f"open({str(marker)!r}, 'w').close()\nraise ImportError('shadowed')\n")
    t = env.transcript(tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": fact()}])
    assert capture(env, t).returncode == 0
    env.wait(lambda: entries(env) == 1 and not queued(env))
    assert not marker.exists()
