import json
import os
import re
import subprocess
import time

from helpers import OUTPUT, fact, make_vault, save

NOTE = ("---\nname: april-invoices\ndescription: April invoice count\nmetadata:\n  type: project\n"
        "  originSessionId: {session}\n---\n\nApril has 42 invoices (checked with psql).\n")
INDEX = "- [April invoices](april.md) — count\n"


def memdir(env, root=None):
    slug = re.sub(r"[^A-Za-z0-9]", "-", str((root or env.project).resolve()))
    directory = env.home / ".claude" / "projects" / slug / "memory"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_note(directory, session, name="april.md"):
    (directory / name).write_text(NOTE.format(session=session))
    (directory / "MEMORY.md").write_text(INDEX)


def extract(env, transcript, cwd=None):
    result = env.run("extract", str(transcript), cwd=cwd)
    assert result.returncode == 0, result.stderr
    return result


def test_changed_notes_reach_the_extractor_and_are_annotated(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    env.respond([{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    stdin = env.calls()[0]["stdin"]
    assert "### april.md" in stdin and "April has 42 invoices (checked with psql)." in stdin
    text = (directory / "april.md").read_text()
    assert text.startswith("---\nname: april-invoices\n")
    assert f"Recorded in VAL3 as {env.vault}#1 (verified)" in text
    assert "checked with psql" in text
    assert (directory / "MEMORY.md").read_text() == INDEX


def test_a_note_is_marked_verified_only_when_its_final_entry_has_backing(env):
    """ADR 0012, amendment 1, point 5: a duplicate of an unverified entry is not "(verified)"; an unverified entry
    replaced, in the same slice, by one with evidence is."""
    make_vault(env)
    v = env.vault
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(evidence=[]), t).stdout == f"admitted unverified {v}#1\n"
    directory = memdir(env)
    write_note(directory, env.session)
    write_note(directory, env.session, "may.md")
    env.respond([{"note": "april.md", "candidate": fact(evidence=[])},  # duplicate of #1
                 {"note": "may.md", "candidate": fact(title="May", evidence=[])},  # #2, unverified
                 {"note": None, "candidate": fact(title="May, checked", supersedes=[f"{v}#2"])}])  # #3 replaces it
    extract(env, t)
    assert "Recorded in VAL3" not in (directory / "april.md").read_text()
    assert f"Recorded in VAL3 as {v}#3 (verified)" in (directory / "may.md").read_text()


def test_notes_of_other_sessions_are_not_sent(env):
    make_vault(env)
    write_note(memdir(env), "another-session")
    extract(env, env.transcript(tool_outputs=[OUTPUT]))
    assert "### april.md" not in env.calls()[0]["stdin"]


def test_notes_without_entries_are_kept_and_not_resent(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    extract(env, t)
    assert (directory / "april.md").read_text() == NOTE.format(session=env.session)
    env.append(t, {"type": "user", "message": {"role": "user", "content": "And for May?"}})
    extract(env, t)
    assert "### april.md" not in env.calls()[1]["stdin"]


def test_no_rewrite_while_the_harness_consolidates(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    (directory / ".consolidate-lock").write_text('{"holderPid": 1}')
    env.respond([{"note": "april.md", "candidate": fact()}], [{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    assert (directory / "april.md").read_text() == NOTE.format(session=env.session)
    (directory / ".consolidate-lock").unlink()
    extract(env, t)
    assert "### april.md" in env.calls()[1]["stdin"]
    assert f"Recorded in VAL3 as {env.vault}#1" in (directory / "april.md").read_text()


def test_memory_directory_follows_the_git_root(env):
    make_vault(env)
    subprocess.run(["git", "init", "-q", str(env.project)], check=True)
    sub = env.project / "sub"
    sub.mkdir()
    write_note(memdir(env), env.session)
    extract(env, env.transcript(tool_outputs=[OUTPUT]), cwd=sub)
    assert "### april.md" in env.calls()[0]["stdin"]


def test_memory_directory_setting_is_honoured(env):
    make_vault(env)
    custom = env.root / "notes"
    custom.mkdir()
    (env.project / ".claude").mkdir()
    (env.project / ".claude" / "settings.json").write_text(json.dumps({"autoMemoryDirectory": str(custom)}))
    write_note(custom, env.session)
    extract(env, env.transcript(tool_outputs=[OUTPUT]))
    assert "### april.md" in env.calls()[0]["stdin"]


def test_notes_written_after_the_last_extraction_use_the_transcript_end(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    extract(env, t)
    write_note(memdir(env), env.session)
    env.respond([], [{"note": "april.md", "candidate": fact()}])
    result = extract(env, t)
    assert "admitted 1" in result.stdout
    second = env.calls()[1]["stdin"]
    assert "### april.md" in second and "TOOL RESULT:\ninvoice_count" in second


def test_a_note_read_back_in_the_session_is_not_evidence(env):
    make_vault(env)
    directory = memdir(env)
    write_note(directory, env.session)
    body = "April has 42 invoices (checked with psql)."
    t = env.transcript(tool_calls=[("Read", {"file_path": str(directory / "april.md")}, body)])
    env.respond([{"note": "april.md", "candidate": fact(evidence=[
        {"kind": "query", "reproduction": "SELECT count(*) FROM invoice", "quote": body}])}])
    result = extract(env, t)  # admitted without the quote (ADR 0012, amendment 1, point 2), the note not annotated
    assert "admitted 1" in result.stdout and "unverified 1, quote-failed 1;" in result.stdout
    assert env.sql(f"SELECT count(*) FROM {env.vault}.evidence") == [(0,)]
    assert "Recorded in VAL3" not in (directory / "april.md").read_text()
    assert "TOOL RESULT (not evidence):\n" + body in env.calls()[0]["stdin"]


def test_a_note_in_a_custom_memory_directory_read_back_is_not_evidence(env):
    make_vault(env)
    custom = env.root / "notes"
    custom.mkdir()
    (env.project / ".claude").mkdir()
    (env.project / ".claude" / "settings.json").write_text(json.dumps({"autoMemoryDirectory": str(custom)}))
    write_note(custom, env.session)
    body = "April has 42 invoices (checked with psql)."
    t = env.transcript(tool_calls=[("Bash", {"command": f"cat {custom}/april.md"}, body)])
    env.respond([{"note": "april.md", "candidate": fact(evidence=[
        {"kind": "query", "reproduction": "SELECT count(*) FROM invoice", "quote": body}])}])
    assert "unverified 1, quote-failed 1;" in extract(env, t).stdout
    assert env.sql(f"SELECT count(*) FROM {env.vault}.evidence") == [(0,)]


def test_a_note_edited_during_extraction_is_not_overwritten(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    (env.fake / "edit-0.json").write_text(json.dumps({"path": str(directory / "april.md"), "text": "Port 8443 is the deploy port.\n"}))
    env.respond([{"note": "april.md", "candidate": fact()}], [])
    extract(env, t)
    text = (directory / "april.md").read_text()
    assert "checked with psql" in text and "Port 8443 is the deploy port." in text
    env.append(t, {"type": "user", "message": {"role": "user", "content": "And for May?"}})
    extract(env, t)
    assert "Port 8443 is the deploy port." in env.calls()[1]["stdin"]


def test_an_annotated_note_keeps_its_body_and_one_reference_line(env):
    # A note that holds several things loses none of them when one becomes an entry.
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    may = fact(title="May has 7 invoices", statement="The ERP holds 7 invoices dated May 2026.")
    env.respond([{"note": "april.md", "candidate": fact()}], [{"note": "april.md", "candidate": may}])
    extract(env, t)
    note = directory / "april.md"
    assert note.read_text() == NOTE.format(session=env.session) + (
        f"\nRecorded in VAL3 as {env.vault}#1 (verified). Read it with `val3 show {env.vault}#1`; "
        "search with `val3 recall <terms>`.\n")
    note.write_text(note.read_text() + "\nMay has 7 invoices.\n")
    env.append(t, {"type": "user", "message": {"role": "user", "content": "And for May?"}})
    extract(env, t)
    text = note.read_text()
    assert "April has 42 invoices (checked with psql)." in text and "May has 7 invoices." in text
    assert text.count("Recorded in VAL3 as") == 1
    assert text.endswith(f"Recorded in VAL3 as {env.vault}#1, {env.vault}#2 (verified). "
                         f"Read it with `val3 show {env.vault}#1`; search with `val3 recall <terms>`.\n")


def unknown_notes(directory, count, size=3000):
    names = []
    for i in range(count):
        path = directory / f"n{i:02}.md"
        head = f"---\nname: n{i:02}\n---\n\n"
        path.write_text(head + "x" * (size - len(head)))
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
        names.append(path.name)
    return names[::-1]  # newest first


def test_unknown_origin_notes_are_capped_newest_first(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    names = unknown_notes(memdir(env), 25)
    first = min(20, 40_000 // 3000)
    extract(env, t)
    sent = [name for name in names if f"### {name}" in env.calls()[0]["stdin"]]
    assert sent == names[:first]
    env.append(t, {"type": "user", "message": {"role": "user", "content": "And for May?"}})
    extract(env, t)
    sent = [name for name in names if f"### {name}" in env.calls()[1]["stdin"]]
    assert sent == names[first:]


def test_a_lock_whose_holder_is_gone_is_stale(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    gone = subprocess.Popen(["true"])
    gone.wait()
    (directory / ".consolidate-lock").write_text(json.dumps({"holderPid": gone.pid}))
    env.respond([{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    assert f"Recorded in VAL3 as {env.vault}#1" in (directory / "april.md").read_text()


def test_a_live_holder_blocks_whatever_the_lock_age(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    lock = directory / ".consolidate-lock"
    lock.write_text(json.dumps({"holderPid": os.getpid()}))
    os.utime(lock, (time.time() - 7200, time.time() - 7200))
    env.respond([{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    assert (directory / "april.md").read_text() == NOTE.format(session=env.session)


def test_an_unreadable_lock_blocks(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    (directory / ".consolidate-lock").write_text("not json")
    env.respond([{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    assert (directory / "april.md").read_text() == NOTE.format(session=env.session)


def test_a_holder_pid_out_of_range_blocks(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    (directory / ".consolidate-lock").write_text(json.dumps({"holderPid": 2**40}))
    env.respond([{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    assert (directory / "april.md").read_text() == NOTE.format(session=env.session)


def test_a_note_whose_entry_was_withdrawn_is_kept(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    assert env.run("withdraw", f"{env.vault}#1", "--obsolete", "--reason", "Gone.", tty=True).returncode == 0
    directory = memdir(env)
    write_note(directory, env.session)
    env.respond([{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    assert (directory / "april.md").read_text() == NOTE.format(session=env.session)
