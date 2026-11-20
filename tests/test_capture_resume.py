"""Capture of resumed and rewritten sessions: the processed set, the shared prefix and the checked cursor."""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

from helpers import OUTPUT, fact, make_vault, save

NOTE = ("---\nname: april-invoices\ndescription: April invoice count\nmetadata:\n  type: project\n"
        "  originSessionId: {session}\n---\n\nApril has 42 invoices (checked with psql).\n")


def message(kind, content, uid, session):
    return {"type": kind, "uuid": uid, "sessionId": session, "promptId": f"prompt-{session}",
            "message": {"role": kind, "content": content}}


def april(session):
    """A first session: a question, a query and its result."""
    return [message("user", "How many invoices in April?", "u1", session),
            message("assistant", [{"type": "tool_use", "id": "t0", "name": "Bash",
                                   "input": {"command": "cat report.txt"}}], "u2", session),
            message("user", [{"type": "tool_result", "tool_use_id": "t0", "content": OUTPUT}], "u3", session)]


def resumed(session, *new):
    """The first session's records under another session id, with metadata records around them, then `new`."""
    return [{"type": "custom-title", "customTitle": "invoices", "sessionId": session}, *april(session),
            {"type": "permission-mode", "permissionMode": "default", "sessionId": session}, *new]


def may(session):
    return [message("user", "And for May?", "u4", session),
            message("assistant", [{"type": "text", "text": "May has 7 invoices."}], "u5", session)]


def write(env, session, records):
    path = env.home / ".claude" / "projects" / "-project" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def extract(env, transcript):
    return env.run("extract", str(transcript))


def processed(env):
    return env.state / "processed" / "project.tsv"


def entries(env):
    return env.sql(f"SELECT count(*) FROM {env.vault}.entry")[0][0]


def test_a_resumed_session_sends_only_its_new_records(env):
    make_vault(env)
    env.respond([{"note": None, "candidate": fact()}],
                [{"note": None, "candidate": fact(title="May count asked", statement="The user asked for May.")}])
    assert "admitted 1" in extract(env, write(env, "first", april("first"))).stdout
    result = extract(env, write(env, "second", resumed("second", *may("second"))))
    assert result.returncode == 0, result.stderr
    second = env.calls()[1]["stdin"]
    assert "USER: And for May?" in second and "May has 7 invoices." in second
    assert "How many invoices in April?" not in second and "invoice_count" not in second
    # the new candidate quotes a record of the skipped prefix: evidence is checked against the whole transcript
    assert "admitted 1" in result.stdout
    assert entries(env) == 2


def test_a_resumed_session_without_new_records_calls_nothing(env):
    make_vault(env)
    extract(env, write(env, "first", april("first")))
    result = extract(env, write(env, "second", resumed("second")))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("nothing new")
    assert len(env.calls()) == 1


def test_a_transcript_rewritten_to_the_same_length_is_read_again(env):
    make_vault(env)
    t = write(env, "s", [message("user", f"old {i}", f"a{i}", "s") for i in range(3)])
    extract(env, t)
    write(env, "s", [message("user", f"new {i}", f"b{i}", "s") for i in range(3)])
    assert extract(env, t).returncode == 0
    second = env.calls()[1]["stdin"]
    assert "USER: new 0" in second and "USER: new 2" in second and "old 0" not in second


def test_a_rewritten_transcript_skips_only_its_processed_prefix(env):
    make_vault(env)
    t = write(env, "s", april("s"))
    extract(env, t)
    write(env, "s", [*april("s")[:2], *may("s")])
    assert extract(env, t).returncode == 0
    second = env.calls()[1]["stdin"]
    assert "USER: And for May?" in second and "How many invoices in April?" not in second


def test_a_crash_between_the_vault_write_and_the_processed_set_admits_nothing_twice(env):
    make_vault(env)
    t = write(env, "s", april("s"))
    env.respond([{"note": None, "candidate": fact()}], [{"note": None, "candidate": fact()}])
    processed(env).parent.mkdir(parents=True)
    processed(env).write_text("")
    os.chmod(processed(env), 0o444)  # the append after the vault write fails
    assert extract(env, t).returncode != 0
    assert entries(env) == 1
    os.chmod(processed(env), 0o644)
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    assert "admitted 0, duplicate 1" in result.stdout
    assert entries(env) == 1
    assert "invoice_count" in env.calls()[1]["stdin"]


def test_a_note_changed_in_a_resumed_session_without_new_records_is_sent(env):
    make_vault(env)
    extract(env, write(env, "first", april("first")))
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(env.project.resolve()))
    notes = env.home / ".claude" / "projects" / slug / "memory"
    notes.mkdir(parents=True)
    (notes / "april.md").write_text(NOTE.format(session="second"))
    (notes / "MEMORY.md").write_text("- [April invoices](april.md) — count\n")
    env.respond([], [{"note": "april.md", "candidate": fact()}])
    result = extract(env, write(env, "second", resumed("second")))
    assert result.returncode == 0, result.stderr
    second = env.calls()[1]["stdin"]
    assert "### april.md" in second and "TOOL RESULT:\ninvoice_count" in second


def test_a_save_quoting_the_skipped_prefix_is_admitted(env):
    make_vault(env)
    extract(env, write(env, "first", april("first")))
    t = write(env, "second", resumed("second", *may("second")))
    extract(env, t)
    result = save(env, fact(), t)
    assert result.returncode == 0, result.stderr
    assert entries(env) == 1


def test_an_old_cursor_with_only_a_line_count_still_works(env):
    make_vault(env)
    t = write(env, "s", april("s"))
    extract(env, t)
    cursor = env.state / "sessions" / f"{hashlib.sha256(b's').hexdigest()}.json"
    cursor.write_text(json.dumps({"lines": 3}))
    env.append(t, *may("s"))
    assert extract(env, t).returncode == 0
    second = env.calls()[1]["stdin"]
    assert "USER: And for May?" in second and "How many invoices in April?" not in second


def test_the_capture_log_counts_the_skipped_records(env):
    make_vault(env)
    extract(env, write(env, "first", april("first")))
    t = write(env, "second", resumed("second", *may("second")))
    data = {"session_id": "second", "transcript_path": str(t), "cwd": str(env.project),
            "hook_event_name": "SessionEnd", "reason": "other"}
    assert env.run("hook", "capture", input=json.dumps(data)).returncode == 0
    log = env.state / "extract.log"
    env.wait(lambda: log.exists() and '"done"' in log.read_text())
    line = json.loads(log.read_text().splitlines()[-1])
    assert line["skipped"] == 4  # the title record and the three shared ones; the trailing metadata is read


def test_processed_records_are_forgotten_after_remember_days(env):
    make_vault(env)
    today = datetime.now(timezone.utc).date()
    processed(env).parent.mkdir(parents=True)
    processed(env).write_text("".join(f"{(today - timedelta(days=10)).isoformat()}\t{uid}\n"
                                      for uid in ("u1", "u2", "u3"))
                              + f"{(today - timedelta(days=40)).isoformat()}\tgone\n")
    with (env.project / "val3.toml").open("a") as handle:
        handle.write("[capture]\nremember_days = 30\n")
    extract(env, write(env, "second", resumed("second", *may("second"))))
    first = env.calls()[0]["stdin"]
    assert "USER: And for May?" in first and "How many invoices in April?" not in first
    assert "\tgone\n" not in processed(env).read_text()
    # the skipped prefix was dated again today: make every line ten days old before shortening the memory
    processed(env).write_text(re.sub(r"(?m)^\S+", (today - timedelta(days=10)).isoformat(), processed(env).read_text()))
    (env.project / "val3.toml").write_text((env.project / "val3.toml").read_text().replace("= 30", "= 5"))
    extract(env, write(env, "third", resumed("third", *may("third"))))
    assert "How many invoices in April?" in env.calls()[1]["stdin"]


def test_remember_days_must_be_a_positive_whole_number(env):
    make_vault(env)
    with (env.project / "val3.toml").open("a") as handle:
        handle.write("[capture]\nremember_days = 0\n")
    result = extract(env, write(env, "s", april("s")))
    assert result.returncode == 1
    assert "remember_days must be a whole number" in result.stderr


def test_the_earlier_session_captured_after_the_one_that_resumed_it_sends_nothing(env):
    make_vault(env)
    env.respond([{"note": None, "candidate": fact()}], [{"note": None, "candidate": None}])
    first = write(env, "first", april("first"))
    extract(env, first)
    extract(env, write(env, "second", resumed("second", *may("second"))))
    calls = len(env.calls())
    write(env, "first", april("first") + may("first"))  # the same messages reach the earlier transcript later
    result = extract(env, first)
    assert result.returncode == 0, result.stderr
    assert len(env.calls()) == calls


def test_a_chain_of_resumed_sessions_outlives_remember_days(env):
    make_vault(env)
    env.respond([{"note": None, "candidate": fact()}])
    extract(env, write(env, "first", april("first")))
    old = (datetime.now(timezone.utc).date() - timedelta(days=80)).isoformat()
    processed(env).write_text(re.sub(r"(?m)^\S+", old, processed(env).read_text()))
    extract(env, write(env, "second", resumed("second")))  # skips the prefix and dates it again
    expired = (datetime.now(timezone.utc).date() - timedelta(days=100)).isoformat()
    processed(env).write_text(processed(env).read_text().replace(old, expired))
    calls = len(env.calls())
    result = extract(env, write(env, "third", resumed("third")))
    assert result.returncode == 0, result.stderr
    assert len(env.calls()) == calls
