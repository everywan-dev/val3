from helpers import OUTPUT, fact, legacy, old_vault, recipe, save

NEW_QUOTE = "invoice_count\n-------------\n          43"


def newer(env, **changes):
    candidate = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.",
                     evidence=[{"kind": "query", "reproduction": "SELECT count(*) AS invoice_count FROM invoice",
                                "quote": NEW_QUOTE}],
                     supersedes=f"{env.vault}#1")
    candidate.update(changes)
    return candidate


def transcript(env):
    return env.transcript(tool_outputs=[OUTPUT, NEW_QUOTE + "\n(1 row)"],
                          user_messages=["Please write the reports in Spanish, always."])


def test_superseding_fact_is_admitted_and_linked(env):
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    result = save(env, newer(env), t)
    assert result.stdout.strip() == f"admitted {env.vault}#2", result.stderr
    assert env.sql(f"SELECT old_id, new_id FROM {env.vault}.supersession") == [(1, 2)]
    assert env.run("show", f"{env.vault}#1").stdout.splitlines()[1] == f"SUPERSEDED by {env.vault}#2"
    assert f"supersedes: {env.vault}#1" in env.run("show", f"{env.vault}#2").stdout


def test_a_supersession_queues_no_notice(env):
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    assert save(env, newer(env), t).returncode == 0
    assert env.sql(f"SELECT reason FROM {env.vault}.review_item") == []


def test_a_legacy_supersession_notice_can_only_be_approved(env):
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, newer(env), t)
    legacy(env, {"title": f"{env.vault}#2 supersedes {env.vault}#1"}, reason="supersession", entry_id=2)
    result = env.run("review", "reject", "1")
    assert result.returncode == 1
    assert "can only be approved" in result.stderr
    assert env.run("review", "approve", "1").stdout.strip() == "acknowledged review #1"


def test_recipes_citing_a_superseded_fact_are_flagged(env):
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, recipe(f"{env.vault}#1"), t)
    assert env.run("review", "approve", "1").returncode == 0
    assert save(env, newer(env), t).stdout.strip() == f"admitted {env.vault}#3"
    assert env.sql(f"SELECT reason FROM {env.vault}.review_item") == [("no-evidence",)]
    assert f"{env.vault}#1 [superseded]" in env.run("show", f"{env.vault}#2").stdout


def test_an_entry_is_superseded_once(env):
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, newer(env), t)
    result = save(env, newer(env, title="April has 44 invoices", statement="The ERP holds 44 invoices."), t)
    assert result.returncode == 1
    assert f"{env.vault}#1 was superseded by {env.vault}#2" in result.stderr


def test_supersede_needs_the_same_kind_in_the_home_vault(env):
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    result = save(env, newer(env, supersedes="other_vault#1"), t)
    assert result.returncode == 1
    assert f"may only cite entries of vault {env.vault}" in result.stderr
    preference = {"kind": "preference", "title": "Reports in Spanish",
                  "statement": "Reports to the owner are written in Spanish.",
                  "evidence": [{"kind": "user", "quote": "write the reports in Spanish"}],
                  "supersedes": f"{env.vault}#1"}
    result = save(env, preference, t)
    assert result.returncode == 1
    assert f"{env.vault}#1 is a fact, expected preference" in result.stderr


def test_a_new_recipe_cannot_cite_a_superseded_fact(env):
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, newer(env), t)
    result = save(env, recipe(f"{env.vault}#1"), t)
    assert result.returncode == 1
    assert "was superseded by" in result.stderr


def test_card_of_a_supersession_shows_both_entries(env):
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, newer(env), t)
    legacy(env, {"title": f"{env.vault}#2 supersedes {env.vault}#1"}, reason="supersession", entry_id=2)
    card = env.run("review", "show", "1").stdout
    assert card.startswith("review #1 · supersession (notice) · project · ")
    assert card.index(f"BEFORE {env.vault}#1") < card.index("April has 42 invoices") < card.index(
        f"NOW {env.vault}#2") < card.index("April has 43 invoices")


def test_card_of_a_recipe_notice_shows_the_recipe_and_both_facts(env):
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, recipe(f"{env.vault}#1"), t)
    assert env.run("review", "approve", "1").returncode == 0
    save(env, newer(env), t)
    legacy(env, {"title": f"recipe {env.vault}#2 cites {env.vault}#1, superseded by {env.vault}#3",
                 "recipe": 2, "old": 1, "new": 3}, reason="recipe-cites-superseded", entry_id=2)
    card = env.run("review", "show", "2").stdout
    assert card.startswith("review #2 · recipe-cites-superseded (notice)")
    assert (card.index(f"RECIPE {env.vault}#2") < card.index(f"BEFORE {env.vault}#1")
            < card.index(f"NOW {env.vault}#3"))


def second(env):
    """Fact #2, worded apart from #1."""
    return fact(title="May has 42 invoices", statement="The ERP holds 42 invoices dated May 2026.")


def test_an_entry_supersedes_several(env):
    v = env.vault
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, second(env), t)
    result = save(env, newer(env, supersedes=[f"{v}#1", f"{v}#2"]), t)
    assert result.stdout.strip() == f"admitted {v}#3", result.stderr
    assert env.sql(f"SELECT old_id, new_id FROM {v}.supersession ORDER BY old_id") == [(1, 3), (2, 3)]
    assert f"supersedes: {v}#1, {v}#2" in env.run("show", f"{v}#3").stdout.splitlines()
    assert env.run("show", f"{v}#1").stdout.splitlines()[1] == f"SUPERSEDED by {v}#3"


def test_supersedes_takes_one_ref_as_a_string_or_a_list(env):
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, second(env), t)
    assert save(env, newer(env), t).stdout.strip() == f"admitted {env.vault}#3"
    as_list = newer(env, title="May has 43 invoices", statement="The ERP holds 43 invoices dated May 2026.",
                    supersedes=[f"{env.vault}#2"])
    assert save(env, as_list, t).stdout.strip() == f"admitted {env.vault}#4"
    assert env.sql(f"SELECT old_id, new_id FROM {env.vault}.supersession ORDER BY old_id") == [(1, 3), (2, 4)]


def test_one_bad_ref_refuses_the_whole_candidate(env):
    v = env.vault
    old_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    result = save(env, newer(env, supersedes=[f"{v}#1", f"{v}#9"]), t)
    assert result.returncode == 1
    assert f"{v}#9 does not exist" in result.stderr
    assert env.run("tool", "add", "invoice-cli", "--type", "cli", "--description", "Counts invoices.").returncode == 0
    result = save(env, newer(env, supersedes=[f"{v}#1", f"{v}#2"]), t)
    assert result.returncode == 1
    assert f"{v}#2 is a tool, expected fact" in result.stderr
    assert env.sql(f"SELECT id FROM {v}.entry ORDER BY id") == [(1,), (2,)]
    assert env.sql(f"SELECT count(*) FROM {v}.supersession") == [(0,)]

