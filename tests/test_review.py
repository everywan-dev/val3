from helpers import OUTPUT, TOOL, fact, legacy, old_vault, recipe, save
from test_supersede import newer, second, transcript as supersede_transcript


def admitted_fact(env):
    old_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT], user_messages=["Yes: the north warehouse closed in March 2025."])
    assert save(env, fact(), transcript).stdout.strip() == f"admitted {env.vault}#1"
    return transcript


def test_recipe_is_queued_and_admitted_on_approval(env):
    transcript = admitted_fact(env)
    result = save(env, recipe(f"{env.vault}#1"), transcript)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "queued for review: review #1 (no-evidence)"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]
    listing = env.run("review").stdout
    assert "#1" in listing and "no-evidence" in listing and "Count the invoices of a month" in listing
    card = env.run("review", "show", "1").stdout
    assert card.startswith("review #1 · no-evidence · project · ")
    assert "[recipe]  Count the invoices of a month" in card
    assert f"  1. Run the invoice count query for the month  (facts: {env.vault}#1)" in card
    result = env.run("review", "approve", "1", "--by", "tester")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"approved review #1: admitted {env.vault}#2"
    assert env.sql(f"SELECT position, action, tool_id FROM {env.vault}.recipe_step ORDER BY position") == [
        (1, "Run the invoice count query for the month", None), (2, "Report the count", None)]
    assert env.sql(f"SELECT recipe_id, position, fact_id FROM {env.vault}.step_fact") == [(2, 1, 1)]
    assert env.sql(f"SELECT name, description FROM {env.vault}.recipe_parameter") == [("month", "YYYY-MM")]
    assert env.sql(f"SELECT parameters, result FROM {env.vault}.recipe_example") == [
        ({"month": "2026-04"}, "42 invoices")]
    assert env.sql(f"SELECT decision, decided_by, entry_id FROM {env.vault}.review_decision") == [
        ("approved", "tester", 2)]
    assert env.run("review").stdout.strip() == "no pending review items"
    shown = env.run("show", f"{env.vault}#2").stdout
    assert f"  1. Run the invoice count query for the month  (facts: {env.vault}#1)" in shown
    assert "  - month: YYYY-MM" in shown
    assert '{"month": "2026-04"} -> 42 invoices' in shown


def test_recipe_with_bad_references_is_rejected(env):
    transcript = admitted_fact(env)
    for ref, message in ((f"{env.vault}#99", "does not exist"), ("other_vault#1", f"may only cite entries of vault {env.vault}")):
        result = save(env, recipe(ref), transcript)
        assert result.returncode == 1, ref
        assert message in result.stderr
    bad_example = recipe(f"{env.vault}#1")
    bad_example["recipe"]["examples"] = [{"parameters": {"year": "2026"}, "result": "x"}]
    result = save(env, bad_example, transcript)
    assert result.returncode == 1
    assert "declared parameters" in result.stderr


def test_recipe_step_must_cite_a_fact(env):
    transcript = admitted_fact(env)
    assert save(env, TOOL, transcript).returncode == 0
    assert env.run("review", "approve", "1").returncode == 0
    result = save(env, recipe(f"{env.vault}#2"), transcript)
    assert result.returncode == 1
    assert f"{env.vault}#2 is a tool, expected fact" in result.stderr


def test_an_item_without_evidence_is_queued_and_can_be_rejected(env):
    transcript = admitted_fact(env)
    result = save(env, TOOL, transcript)
    assert result.stdout.strip() == "queued for review: review #1 (no-evidence)"
    result = env.run("review", "reject", "1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "rejected review #1"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]
    result = env.run("review", "reject", "1")
    assert result.returncode == 1
    assert "already rejected" in result.stderr


def test_fact_with_tool_and_user_evidence_is_admitted(env):
    transcript = admitted_fact(env)
    candidate = fact(title="Warehouse closure confirmed", statement="The north warehouse closed in March 2025.",
                     evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"},
                               fact()["evidence"][0]])
    assert save(env, candidate, transcript).stdout.strip() == f"admitted {env.vault}#2"


def test_same_review_candidate_is_queued_once(env):
    transcript = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), transcript)
    result = save(env, recipe(f"{env.vault}#1"), transcript)
    assert result.stdout.strip() == "already queued: review #1"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(1,)]


def test_tool_candidate_is_queued_and_admitted_with_its_details(env):
    transcript = admitted_fact(env)
    assert save(env, TOOL, transcript).stdout.strip() == "queued for review: review #1 (no-evidence)"
    assert env.run("review", "approve", "1").stdout.strip() == f"approved review #1: admitted {env.vault}#2"
    assert env.sql(f"SELECT tool_type, environment, priority FROM {env.vault}.tool") == [
        ("cli", "https://example.org/code-graph.git", 5)]
    shown = env.run("show", f"{env.vault}#2").stdout
    assert "tool: cli, priority 5" in shown
    assert "environment: https://example.org/code-graph.git" in shown


def test_unknown_review_item(env):
    old_vault(env)
    result = env.run("review", "approve", "7")
    assert result.returncode == 1
    assert "no review item #7" in result.stderr


def test_agent_sessions_cannot_decide_reviews_or_add_tools(env):
    transcript = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), transcript)
    agent = {"CLAUDECODE": "1"}
    for args in (("review", "approve", "1"), ("review", "reject", "1"),
                 ("tool", "add", "x", "--type", "cli", "--description", "d")):
        result = env.run(*args, environ=agent)
        assert result.returncode == 1, args
        assert "outside the agent session" in result.stderr
    assert env.run("review", environ=agent).returncode == 0


def test_list_names_review_items_apart_from_entries(env):
    transcript = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), transcript)
    assert env.run("review").stdout.startswith("review #1  no-evidence")


def test_card_shows_reproductions_as_not_run(env):
    admitted_fact(env)
    legacy(env, {**TOOL, "evidence": fact()["evidence"]}, "tool")
    card = env.run("review", "show", "1").stdout
    assert "[tool]  code-graph" in card and "tool: cli, priority 5" in card
    assert "      > invoice_count" in card
    assert ("      reproduce: SELECT count(*) AS invoice_count FROM invoice WHERE date >= '2026-04-01'  (not run)"
            in card)


def test_card_of_a_superseding_candidate_shows_what_it_replaces(env):
    admitted_fact(env)
    legacy(env, fact(title="North warehouse closed", statement="The north warehouse closed in March 2025.",
                     evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}],
                     supersedes=f"{env.vault}#1"))
    card = env.run("review", "show", "1").stdout
    assert f"approving supersedes {env.vault}#1" in card
    assert f"BEFORE {env.vault}#1" in card and "April has 42 invoices" in card


def test_control_characters_from_the_vault_are_escaped(env):
    transcript = admitted_fact(env)
    candidate = {**TOOL, "title": "Clear \x1b[2J and retitle \x1b]0;owned\x07 here"}  # no evidence: queued
    assert save(env, candidate, transcript).returncode == 0
    for args in (("review",), ("review", "show", "1")):
        out = env.run(*args).stdout
        assert "\x1b" not in out and "\x07" not in out, args
        assert "Clear \\x1b[2J and retitle \\x1b]0;owned\\x07 here" in out, args


def test_a_rejected_candidate_is_not_queued_again(env):
    transcript = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), transcript)
    assert env.run("review", "reject", "1").returncode == 0
    result = save(env, recipe(f"{env.vault}#1"), transcript)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "previously rejected: review #1"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(1,)]
    assert env.run("review").stdout.strip() == "no pending review items"


def test_format_characters_from_the_vault_are_escaped(env):
    transcript = admitted_fact(env)
    candidate = {**TOOL, "title": "Pay ‮eulav‬ and ⁦isolated⁩ zero​width"}  # no evidence: queued
    assert save(env, candidate, transcript).returncode == 0
    for args in (("review",), ("review", "show", "1")):
        out = env.run(*args).stdout
        assert not any(c in out for c in "‮‬⁦⁩​"), args
        assert "Pay \\u202eeulav\\u202c and \\u2066isolated\\u2069 zero\\u200bwidth" in out, args


def test_a_legacy_supersession_card_lists_every_predecessor(env):
    v = env.vault
    old_vault(env)
    t = supersede_transcript(env)
    save(env, fact(), t)
    save(env, second(env), t)
    save(env, newer(env, supersedes=[f"{v}#1", f"{v}#2"]), t)
    legacy(env, {"title": f"{v}#3 supersedes {v}#1, {v}#2"}, reason="supersession", entry_id=3)
    card = env.run("review", "show", "1").stdout
    assert card.index(f"BEFORE {v}#1") < card.index("April has 42 invoices") < card.index(
        f"BEFORE {v}#2") < card.index("May has 42 invoices") < card.index(f"NOW {v}#3")


def test_a_queued_payload_with_one_ref_still_approves(env):
    v = env.vault
    admitted_fact(env)
    legacy(env, fact(title="North warehouse closed", statement="The north warehouse closed in March 2025.",
                     evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}],
                     supersedes=f"{v}#1"))
    assert f"approving supersedes {v}#1" in env.run("review", "show", "1").stdout
    result = env.run("review", "approve", "1")
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT old_id, new_id FROM {v}.supersession") == [(1, 2)]
