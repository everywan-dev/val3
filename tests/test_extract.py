import fcntl
import json
import subprocess
import sys
import time

from helpers import OUTPUT, QUOTE, fact, make_vault, old_vault, recipe, save


def extract(env, transcript, cwd=None):
    return env.run("extract", str(transcript), cwd=cwd)


def test_extract_admits_the_extractor_candidates(env):
    make_vault(env)
    t = env.transcript(user_messages=["How many invoices in April?"], tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": fact()}])
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(": admitted 1, duplicate 0, repeat 0, queued 0, already queued 0, previously "
                                  "rejected 0, rejected 0, unverified 0, quote-failed 0; 1 extractor calls, $0.0010\n"), result.stdout
    assert env.sql(f"SELECT title, origin_session FROM {env.vault}.entry") == [("April has 42 invoices", env.session)]


def test_extractor_runs_isolated_with_the_project_model(env):
    (env.project / "val3.toml").write_text(
        f'service = "val3test"\n[vaults]\nhome = "{env.vault}"\n[extractor]\nmodel = "claude-sonnet-5-5"\n')
    assert env.run("vault", "create", env.vault).returncode == 0
    extract(env, env.transcript(tool_outputs=[OUTPUT]))
    call = env.calls()[0]
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    for flag in ("-p", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "candidates" in json.loads(argv[argv.index("--json-schema") + 1])["properties"]
    assert "Prompt version: 7." in argv[argv.index("--system-prompt") + 1]
    assert "Work contact data" in argv[argv.index("--system-prompt") + 1]
    assert '"kind": "fact | recipe | preference | decision | tool"' in argv[argv.index("--system-prompt") + 1]
    assert call["extracting"] == "1"


def test_default_model_is_haiku(env):
    make_vault(env)
    extract(env, env.transcript(tool_outputs=[OUTPUT]))
    argv = env.calls()[0]["argv"]
    assert argv[argv.index("--model") + 1] == "claude-haiku-5-5"


def test_unknown_extractor_settings_are_rejected(env):
    (env.project / "val3.toml").write_text(
        f'service = "val3test"\n[vaults]\nhome = "{env.vault}"\n[extractor]\ntemperature = 1\n')
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 1
    assert "[extractor] may only hold 'model'" in result.stderr


def test_material_is_the_condensed_transcript_without_thinking(env):
    make_vault(env)
    t = env.transcript(user_messages=["count April invoices"], assistant_texts=["Running the count."],
                       tool_calls=[("Bash", {"command": "psql -c 'select 1'"}, OUTPUT),
                                   ("Agent", {"prompt": "look around"}, "subagent says hello")])
    env.append(t, {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "SECRET-THOUGHT"}]}},
               {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "x", "content": "y" * 5000}]}})
    extract(env, t)
    stdin = env.calls()[0]["stdin"]
    assert "USER: count April invoices" in stdin
    assert "ASSISTANT: Running the count." in stdin
    assert "TOOL CALL Bash: psql -c 'select 1'" in stdin
    assert "TOOL RESULT:\n" + QUOTE in stdin
    assert "TOOL RESULT (not evidence):\nsubagent says hello" in stdin
    assert "y" * 4000 + "\n[… 1000 more characters]" in stdin
    assert "SECRET-THOUGHT" not in stdin


def test_capture_discards_the_evidence_of_a_candidate_backed_only_by_a_memory_tool_result(env):
    make_vault(env)  # upgraded: admitted without the quote (ADR 0012, amendment 1, point 2)
    t = env.transcript(user_messages=["How many invoices in April?"],
                       tool_calls=[("mcp__val3__memory_search", {"query": "april invoices"}, OUTPUT)])
    env.respond([{"note": None, "candidate": fact()}])
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    assert "admitted 1" in result.stdout and "rejected 0, unverified 1, quote-failed 1;" in result.stdout
    assert "TOOL RESULT (not evidence):\n" + QUOTE in env.calls()[0]["stdin"]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.evidence") == [(0,)]
    assert ("discarded",) in env.sql(f"SELECT action FROM {env.vault}.event")


def test_existing_entries_are_listed_for_the_extractor(env):
    make_vault(env)
    t = env.transcript(user_messages=["How many invoices were there in April?"], tool_outputs=[OUTPUT])
    save(env, fact(), t)
    extract(env, t)
    assert f"- {env.vault}#1 [fact] April has 42 invoices: The ERP holds" in env.calls()[0]["stdin"]


def test_bad_candidates_are_counted_and_good_ones_kept(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    unquoted = fact(evidence=[{"kind": "query", "reproduction": "SELECT 1", "quote": "not in the transcript at all"}])
    env.respond([{"note": None, "candidate": unquoted}, {"note": None, "candidate": {"kind": "rumour"}},
                 "not an object", {"note": None, "candidate": fact()}])
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    # the unquoted one is admitted unverified, and the good one, of the same text, verifies it
    assert "admitted 1" in result.stdout and "repeat 0, verified 1," in result.stdout and "rejected 2," in result.stdout
    assert ("discarded",) in env.sql(f"SELECT action FROM {env.vault}.event")


def test_only_new_lines_are_sent_again(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    extract(env, t)
    result = extract(env, t)
    assert result.stdout.strip().endswith("nothing new")
    assert len(env.calls()) == 1
    env.append(t, {"type": "user", "message": {"role": "user", "content": "And for May?"}})
    extract(env, t)
    second = env.calls()[1]["stdin"]
    assert "USER: And for May?" in second and QUOTE not in second


def test_long_transcripts_are_split_into_chunks(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[f"row {i} " + "x" * 3500 for i in range(80)])
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    calls = env.calls()
    assert len(calls) >= 3
    assert all(len(call["stdin"]) <= 121_000 for call in calls)
    assert "row 0 " in calls[0]["stdin"] and "row 79 " in calls[-1]["stdin"]


def test_a_failed_chunk_is_resent_and_finished_chunks_are_not(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[f"row {i} " + "x" * 3500 for i in range(40)])
    (env.fake / "response-1.json").write_text("not json")
    result = extract(env, t)
    assert result.returncode == 1
    assert "not the expected JSON" in result.stderr
    assert extract(env, t).returncode == 0
    calls = env.calls()
    assert len(calls) == 3
    assert calls[2]["stdin"].count("row ") == calls[1]["stdin"].count("row ")


def test_extractor_failure_is_an_error(env):
    make_vault(env)
    (env.fake / "exit").write_text("1")
    result = extract(env, env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 1
    assert "extractor exited 1: fake extractor failure" in result.stderr


def test_a_partly_written_last_line_is_left_for_the_next_extraction(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    line = json.dumps({"type": "user", "message": {"role": "user", "content": "The deploy port is 8443."}}) + "\n"
    with t.open("a") as handle:
        handle.write(line[:30])
    extract(env, t)
    with t.open("a") as handle:
        handle.write(line[30:])
    extract(env, t)
    assert "USER: The deploy port is 8443." in env.calls()[1]["stdin"]


def test_odd_extractor_answers_do_not_break_the_extraction(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    (env.fake / "response-0.json").write_text(json.dumps(
        {"type": "result", "is_error": False, "total_cost_usd": "n/a", "structured_output": {"candidates": [
            {"note": ["x.md"], "candidate": fact(title="Bad\u0000title")},
            {"note": ["x.md"], "candidate": fact()}]}}))
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    assert "admitted 1" in result.stdout and "rejected 1" in result.stdout


def test_extraction_counts_previously_rejected_candidates(env):
    old_vault(env)
    t = env.transcript(user_messages=["How many invoices in April?"], tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    assert save(env, recipe(f"{env.vault}#1"), t).returncode == 0
    assert env.run("review", "reject", "1").returncode == 0
    env.respond([{"note": None, "candidate": recipe(f"{env.vault}#1")}])
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    assert "previously rejected 1" in result.stdout


def test_user_text_blocks_look_up_existing_entries(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    fresh = env.transcript(session="text-blocks")
    env.append(fresh, {"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": "How many invoices in April?"}]}})
    assert extract(env, fresh).returncode == 0
    assert f"{env.vault}#1 [fact] April has 42 invoices" in env.calls()[0]["stdin"]


def test_a_human_queued_message_looks_up_existing_entries(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    fresh = env.transcript(session="queued")
    env.append(fresh, {"type": "attachment", "attachment": {
        "type": "queued_command", "prompt": "How many invoices in April?",
        "origin": {"kind": "human"}, "humanTurn": True}})
    assert extract(env, fresh).returncode == 0
    assert f"{env.vault}#1 [fact] April has 42 invoices" in env.calls()[0]["stdin"]


def test_a_shrunk_transcript_is_read_again(env):
    make_vault(env)
    t = env.transcript(user_messages=["one", "two", "three"])
    assert extract(env, t).returncode == 0
    t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "And for May?"}}) + "\n")
    assert extract(env, t).returncode == 0
    assert "And for May?" in env.calls()[1]["stdin"]
    env.append(t, {"type": "user", "message": {"role": "user", "content": "And for June?"}})
    assert extract(env, t).returncode == 0
    third = env.calls()[2]["stdin"]
    assert "And for June?" in third and "And for May?" not in third


def test_extract_waits_for_the_drain_lock(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    queue = env.state / "queue"
    queue.mkdir(parents=True)
    with open(queue / ".lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        proc = subprocess.Popen([sys.executable, "-m", "val3", "extract", str(t)], cwd=env.project,
                                env=env.environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.5)
        assert proc.poll() is None
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err


def test_a_chunk_without_user_text_looks_up_existing_entries(env):
    make_vault(env)
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    tools_only = env.transcript(tool_outputs=["invoices of April: 42 rows"], session="tools-only")
    assert extract(env, tools_only).returncode == 0
    assert f"{env.vault}#1 [fact] April has 42 invoices" in env.calls()[0]["stdin"]


def test_a_chunk_whose_user_text_finds_nothing_looks_up_its_own_text(env):
    make_vault(env)
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    terse = env.transcript(user_messages=["continue"], tool_outputs=["invoices of April: 42 rows"], session="terse")
    assert extract(env, terse).returncode == 0
    assert f"{env.vault}#1 [fact] April has 42 invoices" in env.calls()[0]["stdin"]


def test_extractor_sees_the_vault_indexes(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    extract(env, t)
    stdin = env.calls()[0]["stdin"]
    assert "## Knowledge indexes\nReuse one of these names; a new name only when none fits; never the project's name." in stdin
    assert "- billing (1)" in stdin
    assert stdin.index("## Knowledge indexes") < stdin.index("## Existing entries")


def test_extractor_vocabulary_is_the_home_vault_after_aliases(env):
    shared = env.vault + "_linked"
    env.config(home=shared)
    assert env.run("vault", "create", shared).returncode == 0
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(title="Linked fact", indexes=["linkedonly"]), t).returncode == 0
    make_vault(env)
    env.config(linked=[shared])
    for n, name in enumerate(["facturacion", "facturas"]):
        assert save(env, fact(title=f"Fact {n}", indexes=[name]), t).returncode == 0
    assert env.run("index", "merge", "facturacion", "facturas").returncode == 0
    extract(env, t)
    stdin = env.calls()[0]["stdin"]
    assert "- facturas (2)" in stdin
    assert "facturacion" not in stdin.split("## Existing entries")[0]
    assert "linkedonly" not in stdin.split("## Existing entries")[0]


def test_extractor_vocabulary_when_the_vault_has_no_indexes(env):
    make_vault(env)
    extract(env, env.transcript(tool_outputs=[OUTPUT]))
    assert "never the project's name.\n(none yet)\n" in env.calls()[0]["stdin"]


def test_extractor_vocabulary_leaves_out_project_and_vault_names(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(indexes=["billing", env.vault.replace("_", "-"), "project"]), t).returncode == 0
    extract(env, t)
    vocabulary = env.calls()[0]["stdin"].split("## Knowledge indexes\n")[1].split("\n\n")[0].splitlines()[1:]
    assert vocabulary == ["- billing (1)"]


def test_extractor_vocabulary_order_is_count_then_name(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    for n, names in enumerate([["zeta", "beta"], ["zeta", "beta"], ["zeta", "alpha"], ["gamma"]]):
        assert save(env, fact(title=f"Fact {n}", indexes=names), t).returncode == 0
    extract(env, t)
    vocabulary = env.calls()[0]["stdin"].split("## Knowledge indexes\n")[1].split("\n\n")[0].splitlines()[1:]
    assert vocabulary == ["- zeta (3)", "- beta (2)", "- alpha (1)", "- gamma (1)"]


def test_extractor_vocabulary_leaves_out_the_own_project_name_before_it_writes(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(indexes=["new-project", "billing"]), t).returncode == 0
    config = env.project / "val3.toml"
    config.write_text('project = "New_Project"\n' + config.read_text())
    extract(env, t)
    vocabulary = env.calls()[0]["stdin"].split("## Knowledge indexes\n")[1].split("\n\n")[0].splitlines()[1:]
    assert vocabulary == ["- billing (1)"]
