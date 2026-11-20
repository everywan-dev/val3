import json

import pytest

from helpers import OLD_REASONS, OUTPUT, QUOTE, TOOL, fact, make_vault, old_vault, recipe, save

PREFERENCE = {"kind": "preference", "title": "Reports in Spanish",
              "statement": "Reports to the owner are written in Spanish.",
              "evidence": [{"kind": "user", "quote": "write the reports in Spanish"}]}


def count(env, table="entry"):
    return env.sql(f"SELECT count(*) FROM {env.vault}.{table}")[0][0]


def test_fact_with_quote_in_tool_output_is_admitted(env):
    make_vault(env)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert env.sql(f"SELECT kind, title, origin_session, keywords FROM {env.vault}.entry") == [
        ("fact", "April has 42 invoices", env.session, "invoices facturas")]
    assert env.sql(f"SELECT kind, quote FROM {env.vault}.evidence") == [("query", QUOTE)]
    assert env.sql(f"SELECT k.name FROM {env.vault}.entry_index ei "
                   f"JOIN {env.vault}.knowledge_index k ON k.id = ei.index_id") == [("billing",)]
    assert env.sql(f"SELECT action FROM {env.vault}.event") == [("admitted",)]


def test_show_prints_statement_and_evidence(env):
    make_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    result = env.run("show", f"{env.vault}#1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"{env.vault}#1  [fact]  April has 42 invoices")
    assert "The ERP holds 42 invoices dated April 2026." in result.stdout
    assert "indexes: billing" in result.stdout
    assert "      > invoice_count" in result.stdout


def test_quote_only_in_assistant_text_is_discarded(env):
    make_vault(env)
    result = save(env, fact(), env.transcript(assistant_texts=[OUTPUT]))
    assert result.returncode == 1
    assert "discarded: evidence quote not found in tool output" in result.stderr
    assert count(env) == 0
    assert env.sql(f"SELECT action FROM {env.vault}.event") == [("discarded",)]


def test_user_quote_must_come_from_a_user_message(env):
    make_vault(env)
    result = save(env, PREFERENCE, env.transcript(tool_outputs=["please write the reports in Spanish"]))
    assert result.returncode == 1
    assert "not found in the user's messages" in result.stderr
    result = save(env, PREFERENCE, env.transcript(user_messages=["Please write the reports in Spanish, always."]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"


def test_same_candidate_twice_is_a_duplicate(env):
    make_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT])
    save(env, fact(), transcript)
    result = save(env, fact(keywords=["other"]), transcript)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"duplicate of {env.vault}#1"
    assert count(env) == 1


def test_quote_matches_output_with_windows_line_endings(env):
    make_vault(env)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT.replace("\n", "\r\n")]))
    assert result.returncode == 0, result.stderr


def test_invalid_candidates_are_rejected(env):
    make_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT])
    cases = [
        (fact(evidence=[{"kind": "query", "reproduction": "SELECT 1", "quote": "42"}]), "at least 8 characters"),
        (fact(colour="red"), "unknown keys: colour"),
        (fact(kind="rumour"), "kind must be one of"),
        (fact(evidence=[{"kind": "query", "quote": "invoice_count"}]), "reproduction is required"),
        (fact(title="x" * 201), "one line of at most 200 characters"),
        (fact(supersedes="nonsense"), "invalid entry reference"),
        (fact(recipe={"steps": []}), '"recipe" is required for kind recipe and allowed only there'),
    ]
    for candidate, message in cases:
        result = save(env, candidate, transcript)
        assert result.returncode == 1, candidate
        assert message in result.stderr, (candidate, result.stderr)
    assert count(env) == 0


def test_invalid_json_is_rejected(env):
    make_vault(env)
    result = env.run("save", input="{not json")
    assert result.returncode == 1
    assert "not valid JSON" in result.stderr


def test_evidence_needs_a_transcript(env):
    make_vault(env)
    result = env.run("save", input=json.dumps(fact()))
    assert result.returncode == 1
    assert "needs the session transcript" in result.stderr


def test_session_defaults_come_from_the_environment(env):
    make_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT])
    result = env.run("save", input=json.dumps(fact()),
                     environ={"VAL3_TRANSCRIPT": str(transcript), "VAL3_SESSION_ID": env.session})
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT origin_session FROM {env.vault}.entry") == [(env.session,)]


def test_save_reads_a_candidate_file(env):
    make_vault(env)
    path = env.root / "candidate.json"
    path.write_text(json.dumps(fact()))
    result = env.run("save", str(path), "--transcript", str(env.transcript(tool_outputs=[OUTPUT])))
    assert result.returncode == 0, result.stderr


def test_show_rejects_missing_vaults_and_entries(env):
    make_vault(env)
    result = env.run("show", "other_vault#1")
    assert result.returncode == 1
    assert "cannot read vault other_vault" in result.stderr
    result = env.run("show", f"{env.vault}#5")
    assert result.returncode == 1
    assert f"no entry {env.vault}#5" in result.stderr


def test_a_discarded_quote_is_not_echoed_into_tool_output(env):
    make_vault(env)
    candidate = fact(evidence=[{"kind": "query", "reproduction": "kubectl get deploy", "quote": "replicas: 9 ready"}])
    first = save(env, candidate, env.transcript(assistant_texts=["replicas: 9 ready"]))
    assert first.returncode == 1
    retry = save(env, candidate, env.transcript(assistant_texts=["replicas: 9 ready"], tool_outputs=[first.stderr]))
    assert retry.returncode == 1
    assert "discarded" in retry.stderr


def test_compaction_summaries_are_not_user_messages(env):
    make_vault(env)
    decision = {"kind": "decision", "title": "Deploy without review", "statement": "Deploy straight to production.",
                "evidence": [{"kind": "user", "quote": "deploy straight to production without review"}]}
    t = env.transcript(compact_summaries=["Summary: the assistant decided to deploy straight to production without review."])
    result = save(env, decision, t)
    assert result.returncode == 1
    assert "not found in the user's messages" in result.stderr


def test_tool_results_holding_model_text_are_not_evidence(env):
    make_vault(env)
    for call in (("Agent", {"prompt": "count invoices"}, OUTPUT),
                 ("Edit", {"file_path": "notes.md"}, OUTPUT),
                 ("Write", {"file_path": "notes.md"}, OUTPUT),
                 ("Bash", {"command": "val3 show v_x#1"}, OUTPUT)):
        result = save(env, fact(), env.transcript(tool_calls=[call]))
        assert result.returncode == 1, call[0]
        assert "discarded" in result.stderr
    result = save(env, fact(), env.transcript(tool_calls=[("Bash", {"command": "psql -c 'SELECT count(*)'"}, OUTPUT)]))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["mcp__val3__memory_read", "mcp__val3__memory_search", "mcp__renamed__memory_read"])
def test_save_rejects_a_quote_from_a_memory_tool_result(env, name):
    make_vault(env)
    result = save(env, fact(), env.transcript(tool_calls=[(name, {"query": "invoices"}, OUTPUT)]))
    assert result.returncode == 1
    assert "discarded" in result.stderr


def test_a_quote_from_another_mcp_tool_still_backs_a_save(env):
    make_vault(env)
    for index, name in enumerate(("mcp__other__lookup", "mcp__other__memory_reader")):
        result = save(env, fact(title=f"April has 42 invoices {index}"),
                      env.transcript(tool_calls=[(name, {"query": "invoices"}, OUTPUT)]))
        assert result.returncode == 0, (name, result.stderr)


def test_index_names_are_normalised(env):
    make_vault(env)
    result = save(env, fact(indexes=["invoice_ocr", "Billing Flows", "billing-flows"]), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT name FROM {env.vault}.knowledge_index ORDER BY name") == [("billing-flows",), ("invoice-ocr",)]
    result = save(env, fact(title="Other", indexes=["__"]), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 1
    assert "invalid index name" in result.stderr


def test_invalid_candidates_are_logged_in_the_vault(env):
    make_vault(env)
    result = save(env, fact(kind="rumour"), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 1
    assert env.sql(f"SELECT action, detail->>'title', detail->>'error' FROM {env.vault}.event") == [
        ("invalid", "April has 42 invoices", "kind must be one of: fact, recipe, preference, decision, tool")]


def test_only_messages_typed_by_the_user_count_as_user_evidence(env):
    make_vault(env)
    t = env.transcript(user_messages=["Please write the reports in Spanish, always."])
    env.append(t, {"type": "user", "origin": {"kind": "task-notification"},
                   "message": {"role": "user", "content": "<task-notification>the deploy goes to production every Friday</task-notification>"}},
               {"type": "user", "origin": {"kind": "human"},
                "message": {"role": "user", "content": "the staging server is called stage-01"}})
    candidate = {"kind": "decision", "title": "Friday deploys", "statement": "Production deploys happen on Fridays.",
                 "evidence": [{"kind": "user", "quote": "the deploy goes to production every Friday"}]}
    result = save(env, candidate, t)
    assert result.returncode == 1
    assert "not found in the user's messages" in result.stderr
    candidate = {"kind": "decision", "title": "Staging server", "statement": "The staging server is stage-01.",
                 "evidence": [{"kind": "user", "quote": "the staging server is called stage-01"}]}
    assert save(env, candidate, t).returncode == 0


def test_a_read_only_grant_shows_the_real_rejection(env):
    make_vault(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    assert env.run("vault", "grant", env.vault, env.reader, "--read").returncode == 0
    env.config(service="val3reader")
    t = env.transcript(tool_outputs=["nothing relevant"])
    invalid = env.run("save", "--transcript", str(t), input='{"kind": "fact"}')
    assert invalid.returncode == 1
    assert "permission denied" not in invalid.stderr and "title" in invalid.stderr
    discarded = env.run("save", "--transcript", str(t), input=json.dumps(fact()))
    assert discarded.returncode == 1
    assert "discarded: evidence quote not found" in discarded.stderr


USER_FACT = fact(title="North warehouse closed", statement="The north warehouse closed in March 2025.",
                 evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}])


def queued(env, t, prompt, kind="human", **record):
    """A queued_command attachment; `kind=None` leaves out the origin, `record` adds fields such as isMeta."""
    attachment = {"type": "queued_command", "prompt": prompt, "humanTurn": True}
    if kind is not None:
        attachment["origin"] = {"kind": kind}
    env.append(t, {"type": "attachment", "attachment": attachment, **record})


def test_a_message_queued_by_the_user_is_user_evidence(env):
    make_vault(env)
    t = env.transcript(tool_outputs=["unrelated"])
    queued(env, t, "by the way, the north warehouse closed in March 2025")
    result = save(env, USER_FACT, t)
    assert result.stdout.strip() == f"admitted {env.vault}#1", result.stderr


def test_harness_turns_queued_as_messages_are_not_evidence(env):
    make_vault(env)
    t = env.transcript(tool_outputs=["unrelated"])
    queued(env, t, "<task-notification>\nthe north warehouse closed in March 2025\n</task-notification>")
    result = save(env, USER_FACT, t)
    assert result.returncode == 1 and "discarded" in result.stderr


PLAIN = "the north warehouse closed in March 2025"


@pytest.mark.parametrize("prompt, options", [
    (PLAIN, {"kind": "coordinator"}),
    (PLAIN, {"kind": "peer"}),
    (PLAIN, {"kind": None}),
    (PLAIN, {"isMeta": True}),
    (f"<agent-message>\n{PLAIN}\n</agent-message>", {}),
    (f"<cross-session-message>\n{PLAIN}\n</cross-session-message>", {}),
    (f"<teammate-message>\n{PLAIN}\n</teammate-message>", {}),
    (f"\n  <task-notification>\n{PLAIN}\n</task-notification>", {}),
    (f"Another Claude session sent a message:\n<cross-session-message>\n{PLAIN}\n</cross-session-message>", {}),
], ids=["coordinator", "peer", "no-origin", "meta", "agent-message", "cross-session-message",
        "teammate-message", "leading-whitespace", "peer-prefix"])
def test_queued_messages_of_the_harness_are_not_user_evidence(env, prompt, options):
    make_vault(env)
    t = env.transcript(tool_outputs=["unrelated"])
    queued(env, t, prompt, **options)
    result = save(env, USER_FACT, t)
    assert result.returncode == 1 and "discarded" in result.stderr


@pytest.mark.parametrize("text", [
    f"Another Claude session sent a message:\n<cross-session-message from=\"p\">\n{PLAIN}\n</cross-session-message>",
    f"<task-notification>\n{PLAIN}\n</task-notification>",
], ids=["peer", "task-notification"])
def test_a_harness_turn_without_an_origin_is_not_user_evidence(env, text):
    """A harness that marks no origin on the record: the text alone tells it is not the user's."""
    make_vault(env)
    t = env.transcript(tool_outputs=["unrelated"], user_messages=[text])
    result = save(env, USER_FACT, t)
    assert result.returncode == 1 and "discarded" in result.stderr


def test_output_of_a_bang_command_is_tool_output_not_user_text(env):
    make_vault(env)
    t = env.transcript(user_messages=["<bash-input>psql -c 'select count(*)'</bash-input>"])
    env.append(t, {"type": "user", "message": {"role": "user", "content":
                   f"<bash-stdout>{QUOTE}</bash-stdout><bash-stderr></bash-stderr>"}})
    assert save(env, fact(), t).stdout.strip() == f"admitted {env.vault}#1"
    as_user = fact(title="Count said by the user", evidence=[{"kind": "user", "quote": QUOTE}])
    result = save(env, as_user, t)
    assert result.returncode == 1 and "discarded" in result.stderr


def test_output_of_a_bang_val3_command_is_not_evidence(env):
    make_vault(env)
    t = env.transcript(user_messages=["<bash-input>val3 show v#1</bash-input>"])
    env.append(t, {"type": "user", "message": {"role": "user", "content":
                   f"<bash-stdout>{QUOTE}</bash-stdout><bash-stderr></bash-stderr>"}})
    result = save(env, fact(), t)
    assert result.returncode == 1 and "discarded" in result.stderr


def bang(env, command, output=QUOTE):
    t = env.transcript(user_messages=[f"<bash-input>{command}</bash-input>"])
    env.append(t, {"type": "user", "message": {"role": "user", "content":
                   f"<bash-stdout>{output}</bash-stdout><bash-stderr></bash-stderr>"}})
    return t


def test_val3_in_a_quoted_shell_and_reads_under_the_harness_directory_are_not_evidence(env):
    make_vault(env)
    for command in ("bash -c 'val3 show v#1'", 'sh -c "val3 recall invoices"',
                    "cd ~/.claude/projects/-project && cat memory/invoices.md",
                    "cat ~/.claude/projects/-project/abc.jsonl"):
        result = save(env, fact(), bang(env, command))
        assert result.returncode == 1 and "discarded" in result.stderr, command
        result = save(env, fact(), env.transcript(tool_calls=[("Bash", {"command": command}, OUTPUT)]))
        assert result.returncode == 1 and "discarded" in result.stderr, command


def test_bang_output_without_its_command_is_not_evidence(env):
    make_vault(env)
    t = env.transcript(tool_outputs=["unrelated"])
    env.append(t, {"type": "user", "message": {"role": "user", "content":
                   f"<bash-stdout>{QUOTE}</bash-stdout><bash-stderr></bash-stderr>"}})
    result = save(env, fact(), t)
    assert result.returncode == 1 and "discarded" in result.stderr


def test_a_candidate_too_deeply_nested_is_not_valid_json(env):
    make_vault(env)
    result = env.run("save", "-", input="[" * 100000)
    assert result.returncode == 1 and "not valid JSON" in result.stderr
    assert "Traceback" not in result.stderr


def test_text_that_only_starts_like_bang_output_stays_user_text(env):
    make_vault(env)
    t = env.transcript(tool_outputs=["unrelated"])
    env.append(t, {"type": "user", "message": {"role": "user", "content":
                   f"<bash-stdout>x</bash-stdout> and then {PLAIN}"}})
    result = save(env, USER_FACT, t)
    assert result.stdout.strip() == f"admitted {env.vault}#1", result.stderr


def test_a_deeply_nested_transcript_line_is_skipped(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    with t.open("a") as handle:
        handle.write("[" * 100000 + "\n")
    assert save(env, fact(), t).stdout.strip() == f"admitted {env.vault}#1"


def test_save_reports_a_new_index(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    result = save(env, fact(), t)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"admitted {env.vault}#1\n"
    assert "val3: new knowledge index: billing (existing: val3 index)" in result.stderr.splitlines()
    second = save(env, fact(title="Another billing fact"), t)
    assert second.returncode == 0, second.stderr
    assert "new knowledge index" not in second.stderr


def test_save_help_states_the_index_rule(env):
    elsewhere = env.root / "elsewhere"
    elsewhere.mkdir()
    result = env.run("save", "--help", cwd=elsewhere)
    assert result.returncode == 0
    assert "never the project's name" in result.stdout


E1 = ("discarded: evidence quote is in the input of the call that printed it (evidence[0]): "
      "quote output the call did not already contain")
ECHOED = "April has 42 invoices in the ERP"


def quoting(text):
    return fact(evidence=[{"kind": "command", "reproduction": "a command", "quote": text}])


def test_a_quote_the_command_printed_from_its_own_text_is_discarded(env):
    make_vault(env)
    t = env.transcript(tool_calls=[("Bash", {"command": f"echo '{ECHOED}'"}, ECHOED)])
    result = save(env, quoting(ECHOED), t)
    assert result.returncode == 1
    assert "evidence quote is in the input of the call that printed it" in result.stderr
    assert ECHOED not in result.stderr
    assert count(env) == 0


def test_a_quote_only_in_another_calls_input_still_counts(env):
    make_vault(env)
    t = env.transcript(tool_calls=[("Bash", {"command": "grep invoices"}, "April has 42 invoices"),
                                   ("Bash", {"command": "echo April has 42 invoices"}, "x")])
    result = save(env, quoting("April has 42 invoices"), t)
    assert result.stdout.strip() == f"admitted {env.vault}#1", result.stderr


def test_a_quote_longer_than_the_search_term_counts(env):
    make_vault(env)
    t = env.transcript(tool_calls=[("Bash", {"command": "grep 'ACME SUPPLIES' suppliers.csv"},
                                    "ACME SUPPLIES SL;B12345678;active")])
    result = save(env, quoting("ACME SUPPLIES"), t)
    assert result.returncode == 1
    assert E1 in result.stderr
    result = save(env, quoting("ACME SUPPLIES SL;B12345678"), t)
    assert result.stdout.strip() == f"admitted {env.vault}#1", result.stderr


def test_a_tool_result_without_its_call_is_not_evidence(env):
    make_vault(env)
    t = env.transcript()
    env.append(t, {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "orphan", "content": OUTPUT}]}})
    result = save(env, fact(), t)
    assert result.returncode == 1
    assert "discarded: evidence quote not found in tool output" in result.stderr


def test_a_typed_command_that_echoes_its_text_is_discarded(env):
    make_vault(env)
    result = save(env, quoting(ECHOED), bang(env, f"echo '{ECHOED}'", ECHOED))
    assert result.returncode == 1
    assert E1 in result.stderr
    assert count(env) == 0


TOOL_OUTPUT = "code-graph 2.1: answers which module overrides a method"
TOOL_EVIDENCE = [{"kind": "command", "reproduction": "code-graph --version", "quote": TOOL_OUTPUT}]


def test_a_tool_with_command_evidence_is_admitted(env):
    make_vault(env)
    result = save(env, {**TOOL, "evidence": TOOL_EVIDENCE}, env.transcript(tool_outputs=[TOOL_OUTPUT]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert count(env, "review_item") == 0


def test_a_recipe_with_evidence_is_admitted(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    result = save(env, recipe(f"{env.vault}#1", evidence=fact()["evidence"]), t)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#2"
    assert count(env, "review_item") == 0


def test_a_fact_with_only_user_evidence_is_admitted(env):
    make_vault(env)
    result = save(env, USER_FACT, env.transcript(user_messages=[f"By the way, {PLAIN}."]))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert count(env, "review_item") == 0


def test_a_tool_without_evidence_goes_to_review(env):
    old_vault(env)
    result = save(env, TOOL, env.transcript())
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "queued for review: review #1 (no-evidence)"
    assert count(env) == 0


def test_an_item_without_evidence_on_a_vault_not_upgraded_names_the_upgrade(env):
    old_vault(env)
    env.sql(OLD_REASONS.format(v=env.vault))  # a vault created before automatic admission
    result = save(env, TOOL, env.transcript())
    assert result.returncode == 1
    assert f"its owner runs `val3 vault upgrade {env.vault}`" in result.stderr
    assert "violates check constraint" not in result.stderr


def test_a_subagents_prompt_is_not_the_users_words(env):
    """The first record of a subagent's transcript is a `user` record holding the parent's prompt (plan 17)."""
    make_vault(env)
    path = env.transcript()
    side = {"isSidechain": True, "agentId": "a1b2c3", "sessionId": env.session}
    env.append(path, {"type": "user", **side, "message": {"role": "user", "content": "Count the April invoices"}},
               {"type": "assistant", **side, "message": {"role": "assistant", "content": [
                   {"type": "tool_use", "id": "t0", "name": "Bash", "input": {"command": "cat report.txt"}}]}},
               {"type": "user", **side, "message": {"role": "user", "content": [
                   {"type": "tool_result", "tool_use_id": "t0", "content": OUTPUT}]}})
    quoted = fact(evidence=[{"kind": "user", "quote": "Count the April invoices"}])
    result = save(env, quoted, path)
    assert result.returncode == 1 and "not found in the user's messages" in result.stderr, result.stderr
    result = save(env, fact(), path)  # the subagent's tool output stays evidence
    assert result.stdout == f"admitted {env.vault}#1\n", result.stderr


def test_a_memory_save_result_is_not_evidence(env):
    make_vault(env)
    printed = "The ledger shows 42 invoices for April"
    t = env.transcript(tool_calls=[("mcp__val3__memory_save", {"candidate": {"title": "x"}}, printed)])
    quoted = fact(evidence=[{"kind": "api", "reproduction": "memory_save", "quote": printed}])
    result = save(env, quoted, t)
    assert result.returncode == 1 and "not found in tool output" in result.stderr, result.stderr
