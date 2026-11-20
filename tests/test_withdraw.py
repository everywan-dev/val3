import getpass
import json

import pytest

from helpers import OUTPUT, QUOTE, before_0012, make_vault, embedding_vault, fact, legacy, old_vault, recipe, save
from test_hooks import context, start
from viewer import viewing
from test_import import adopter, answer, commit, dry_run, manifest, note_candidate, unit_candidate, write


def admitted(env):
    """A home vault created before ADR 0012 with one admitted fact, #1; returns the transcript."""
    old_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    return t


def withdraw(env, *args, environ=None):
    return env.run("withdraw", *args, environ=environ)


def withdrawals(env):
    return env.sql(f"SELECT entry_id, cause, reason, review_id, batch FROM {env.vault}.withdrawal ORDER BY entry_id")


def test_withdrawing_an_entry_records_the_cause_and_the_reason(env):
    admitted(env)
    result = withdraw(env, f"{env.vault}#1", "--wrong", "--reason", " The count included\n draft  invoices. ")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"withdrawn {env.vault}#1"
    assert withdrawals(env) == [(1, "wrong", "The count included draft invoices.", None, None)]
    assert env.sql(f"SELECT entry_id, detail->>'cause', detail->>'reason' FROM {env.vault}.event "
                   "WHERE action = 'withdrawn'") == [(1, "wrong", "The count included draft invoices.")]
    by = env.sql(f"SELECT withdrawn_by FROM {env.vault}.withdrawal")[0][0]
    line = env.run("show", f"{env.vault}#1").stdout.splitlines()[1]
    assert line.startswith("WITHDRAWN (wrong) ") and line.endswith(f" by {by}: The count included draft invoices.")


def test_an_entry_is_withdrawn_once(env):
    admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "Replaced by the ledger.").returncode == 0
    result = withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "Again.")
    assert result.returncode == 1
    by = env.sql(f"SELECT withdrawn_by FROM {env.vault}.withdrawal")[0][0]
    assert f"{env.vault}#1 was already withdrawn on " in result.stderr and f" by {by}" in result.stderr
    assert len(withdrawals(env)) == 1


def test_withdraw_usage(env):
    admitted(env)
    ref = f"{env.vault}#1"
    for args in ((ref, "--reason", "x"), (ref, "--wrong", "--obsolete", "--reason", "x"), (ref, "--wrong"),
                 (ref, "--wrong", "--reason", " \n "), ("--wrong", "--reason", "x")):
        assert withdraw(env, *args).returncode == 2, args
    assert withdrawals(env) == []


def test_withdraw_needs_an_entry_of_the_home_vault(env):
    admitted(env)
    shared = env.vault + "_linked"
    assert env.run("vault", "create", shared).returncode == 0
    env.config(linked=[shared])
    for ref, message in ((f"{env.vault}#9", "does not exist"), (f"{shared}#1", "read only"),
                         ("v_undeclared#1", "not declared")):
        result = withdraw(env, ref, "--wrong", "--reason", "x")
        assert result.returncode == 1 and message in result.stderr, ref


def test_a_superseded_entry_can_be_withdrawn(env):
    t = admitted(env)
    newer = fact(title="April has 42 invoices, drafts excluded",
                 statement="The ERP holds 42 invoices dated April 2026, drafts excluded.", supersedes=f"{env.vault}#1")
    assert save(env, newer, t).returncode == 0
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "Drafts were counted.").returncode == 0
    shown = env.run("show", f"{env.vault}#1").stdout
    assert "WITHDRAWN (wrong)" in shown and f"SUPERSEDED by {env.vault}#2" in shown


def test_withdraw_on_a_vault_that_predates_withdrawals_names_the_upgrade(env):
    t = admitted(env)
    env.sql(f"DROP TABLE {env.vault}.withdrawal")
    result = withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "x")
    assert result.returncode == 1 and f"val3 vault upgrade {env.vault}" in result.stderr
    assert env.run("show", f"{env.vault}#1").returncode == 0
    assert save(env, fact(title="May has 50 invoices", statement="The ERP holds 50 invoices dated May 2026."),
                t).returncode == 0


def test_a_reason_cannot_redraw_the_terminal(env):
    admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "bad \x1b]0;owned\x07 count").returncode == 0
    shown = env.run("show", f"{env.vault}#1").stdout
    assert "\x1b" not in shown and "\\x1b]0;owned\\x07" in shown


def test_a_withdrawn_entry_leaves_recall_and_the_session_index(env):
    admitted(env)
    assert f"{env.vault}#1" in env.run("recall", "invoices").stdout
    assert f"- {env.vault}#1 [fact]" in context(start(env), "SessionStart")
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "Drafts were counted.").returncode == 0
    assert env.run("recall", "invoices").stdout.strip() == "no matches"
    assert f"{env.vault}#1" not in context(start(env), "SessionStart")


def test_an_entry_cannot_cite_or_supersede_a_withdrawn_one(env):
    t = admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "Drafts were counted.").returncode == 0
    newer = fact(title="April has 41 invoices", statement="The ERP holds 41 invoices dated April 2026.",
                 supersedes=f"{env.vault}#1")
    for candidate in (recipe(f"{env.vault}#1"), newer):
        result = save(env, candidate, t)
        assert result.returncode == 1
        assert f"{env.vault}#1 was withdrawn (wrong): Drafts were counted." in result.stderr


def test_a_withdrawal_reason_cannot_redraw_the_terminal_through_an_error(env):
    t = admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "bad \x1b]0;owned\x07 count").returncode == 0
    result = save(env, recipe(f"{env.vault}#1"), t)
    assert result.returncode == 1
    assert "\x1b" not in result.stderr and "\\x1b]0;owned\\x07" in result.stderr


def test_eval_reads_a_withdrawn_entry_as_it_was(env, embedder):
    embedding_vault(env, embedder.url)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(keywords=[]), t).returncode == 0
    at = env.sql("SELECT now()")[0][0].isoformat()
    assert env.run("withdraw", f"{env.vault}#1", "--obsolete", "--reason", "The ledger replaced it.",
                   tty=True).returncode == 0  # a person at a terminal (ADR 0012 §6)
    query = f"{fact()['title']}\n{fact()['statement']}"

    def served(case):
        path = env.root / "set.jsonl"
        path.write_text(json.dumps(case) + "\n")
        rows = {line.split()[0]: line.split()[1:] for line in env.run("eval", str(path)).stdout.splitlines() if line}
        return rows["0.60*"][2], rows["full-text"][2]

    assert served({"query": query, "relevant": [f"{env.vault}#1"], "at": at}) == ("1/1", "1/1")
    assert served({"query": query, "relevant": [f"{env.vault}#1"]}) == ("0/1", "0/1")


def test_a_linked_vault_without_the_withdrawal_table_is_still_read(env):
    shared = env.vault + "_old"
    env.config(home=shared)
    assert env.run("vault", "create", shared).returncode == 0
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    env.sql(f"DROP TABLE {shared}.withdrawal")  # created by an older installation, never upgraded
    env.config(linked=[shared])
    assert env.run("vault", "create", env.vault).returncode == 0
    assert f"{shared}#1" in env.run("recall", "invoices").stdout
    assert f"- {shared}#1 [fact]" in context(start(env), "SessionStart")
    assert env.run("show", f"{shared}#1").returncode == 0


def test_a_withdrawal_queues_no_notice(env):
    t = admitted(env)                                                     # fact #1
    assert env.run("tool", "add", "invoice-cli", "--type", "cli",
                   "--description", "Counts invoices.").returncode == 0  # tool #2
    steps = recipe(f"{env.vault}#1")
    steps["recipe"]["steps"][1]["tool"] = f"{env.vault}#2"
    assert save(env, steps, t).returncode == 0                            # queued: review #1
    assert env.run("review", "approve", "1").returncode == 0              # recipe #3
    newer = fact(title="April has 42 invoices, drafts excluded",
                 statement="The ERP holds 42 invoices dated April 2026, drafts excluded.", supersedes=f"{env.vault}#1")
    assert save(env, newer, t).returncode == 0                            # fact #4 supersedes #1
    for ref in ("#2", "#4"):
        assert withdraw(env, env.vault + ref, "--obsolete", "--reason", "The ledger replaced it.").returncode == 0
    assert env.sql(f"SELECT reason FROM {env.vault}.review_item") == [("no-evidence",)]


def test_a_legacy_withdrawn_notice_is_shown_and_can_only_be_approved(env):
    t = admitted(env)
    steps = recipe(f"{env.vault}#1")
    assert save(env, steps, t).returncode == 0                            # queued: review #1
    assert env.run("review", "approve", "1").returncode == 0              # recipe #2
    newer = fact(title="April has 42 invoices, drafts excluded",
                 statement="The ERP holds 42 invoices dated April 2026, drafts excluded.", supersedes=f"{env.vault}#1")
    assert save(env, newer, t).returncode == 0                            # fact #3 supersedes #1
    assert withdraw(env, f"{env.vault}#3", "--wrong", "--reason", "Drafts were not excluded.").returncode == 0
    legacy(env, {"title": f"{env.vault}#3 was withdrawn (wrong)", "entry": 3, "recipes": [2], "superseded": 1},
           reason="withdrawn", entry_id=3)
    card = env.run("review", "show", "2").stdout
    assert card.startswith("review #2 · withdrawn (notice)")
    assert f"RECIPE {env.vault}#2" in card and "WITHDRAWN (wrong)" in card and f"BEFORE {env.vault}#1" in card
    result = env.run("review", "reject", "2")
    assert result.returncode == 1 and "a withdrawal is final" in result.stderr
    assert env.run("review", "approve", "2").stdout.strip() == "acknowledged review #2"


def test_saving_the_text_of_a_withdrawn_entry_says_it_is_not_delivered(env):
    t = admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "The ledger replaced it.").returncode == 0
    result = save(env, fact(), t)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (f"duplicate of {env.vault}#1, withdrawn (obsolete): The ledger replaced it.; "
                                     "save it with changed text to bring it back")
    assert [row[0] for row in withdrawals(env)] == [1]


def test_saving_the_text_of_a_superseded_entry_names_the_newer_one(env):
    t = admitted(env)
    assert save(env, fact(title="April has 42 invoices", statement="The ERP holds 42 invoices dated April.",
                          supersedes=f"{env.vault}#1"), t).returncode == 0
    result = save(env, fact(), t)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"duplicate of {env.vault}#1, superseded by {env.vault}#2"


def test_the_withdrawn_ref_is_stored_as_parsed(env):
    admitted(env)
    result = withdraw(env, f"\x0b{env.vault}#1\x0c", "--wrong", "--reason", "Miscounted.")
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"withdrawn {env.vault}#1\n"


OLDER = {"kind": "fact", "title": "Routing has an owner field",
         "statement": "The routing configuration names an owner.", "keywords": ["routing"]}


def imported_batch(env):
    """An import batch with one new entry (#2), one duplicate of an older entry (#1) and one item pending;
    returns (batch, {title: review id})."""
    adopter(env)
    write(env, "memory/routing-owner.md", "---\nname: routing-owner\n---\n\nRouting names an owner "
          "(`config/routing.yaml`).\n")
    commit(env, "third source")
    assert env.run("vault", "create", env.vault).returncode == 0
    before_0012(env)
    assert save(env, {**OLDER, "evidence": [{"kind": "user", "quote": "routing names an owner"}]},
                env.transcript(user_messages=["Yes, routing names an owner."])).returncode == 0  # entry #1
    answer(env, 0, unit_candidate())
    answer(env, 1, note_candidate(evidence=[]))  # sources are read in path order: kb-0001, route-by-owner, routing-owner
    answer(env, 2, {**OLDER, "evidence": [{"path": "config/routing.yaml", "quote": "owner: billing"}]})
    assert dry_run(env).returncode == 0
    assert len(env.calls()) == 3 and env.calls()[2]["stdin"].startswith("SOURCE memory/routing-owner.md")
    batch = manifest(env)["batch"]
    result = env.run("import", "--apply", str(env.root / "manifest.json"))  # reviews #1-#3
    assert result.stdout.strip().endswith("1 admitted, 1 queued, 0 already queued, 0 previously rejected, "
                                          "1 duplicate, 0 already imported, 0 refused"), result.stderr  # new entry #2
    items = dict(env.sql(f"SELECT payload->>'title', id FROM {env.vault}.review_item WHERE reason = 'import'"))
    return batch, items


def test_withdrawing_a_batch(env):
    batch, items = imported_batch(env)
    result = withdraw(env, "--batch", batch, "--obsolete", "--reason", "The old memory was retired.")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (f"batch {batch}: 1 withdrawn, 1 pending rejected, "
                                     "1 not withdrawn (existed before the batch), 0 already withdrawn")
    assert withdrawals(env) == [(2, "obsolete", "The old memory was retired.", None, batch)]
    by = env.sql(f"SELECT withdrawn_by FROM {env.vault}.withdrawal")[0][0]
    assert env.sql(f"SELECT decision, decided_by FROM {env.vault}.review_decision "
                   "WHERE review_id = %s", (items[note_candidate()["title"]],)) == [("rejected", by)]
    assert f"{env.vault}#1" in env.run("recall", "routing").stdout
    again = withdraw(env, "--batch", batch, "--wrong", "--reason", "Again.")
    assert again.returncode == 0 and again.stdout.strip().endswith(
        "0 withdrawn, 0 pending rejected, 1 not withdrawn (existed before the batch), 1 already withdrawn")
    result = withdraw(env, "--batch", "0" * 16, "--wrong", "--reason", "x")
    assert result.returncode == 1 and "no admitted entries" in result.stderr
    assert withdraw(env, f"{env.vault}#1", "--batch", batch, "--wrong", "--reason", "x").returncode == 2


AGENT = {"CLAUDECODE": "1"}


def test_inside_the_agent_a_withdrawal_is_proposed_and_a_person_decides(env):
    admitted(env)
    ref = f"{env.vault}#1"
    result = withdraw(env, ref, "--wrong", "--reason", "Drafts were counted.", environ=AGENT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "proposed: review #1"
    assert withdrawals(env) == [] and ref in env.run("recall", "invoices").stdout
    again = withdraw(env, ref, "--wrong", "--reason", "Drafts were counted.", environ=AGENT)
    assert again.stdout.strip() == "already proposed: review #1"
    assert env.sql(f"SELECT reason, entry_id, payload->>'cause' FROM {env.vault}.review_item") == \
        [("withdrawal", 1, "wrong")]
    card = env.run("review", "show", "1").stdout
    assert f"{ref}  [fact]" in card and "approving withdraws (wrong): Drafts were counted." in card
    assert env.run("review", "approve", "1").stdout.strip() == f"approved review #1: withdrawn {ref}"
    assert withdrawals(env) == [(1, "wrong", "Drafts were counted.", 1, None)]
    assert env.sql(f"SELECT entry_id FROM {env.vault}.review_decision WHERE review_id = 1") == [(1,)]


def test_a_rejected_proposal_leaves_the_entry_live(env):
    admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "Old.", environ=AGENT).returncode == 0
    assert env.run("review", "reject", "1").returncode == 0
    assert f"{env.vault}#1" in env.run("recall", "invoices").stdout
    again = withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "Old.", environ=AGENT)
    assert again.stdout.strip() == "previously rejected: review #1"


def test_a_proposal_for_an_entry_withdrawn_meanwhile_is_stale(env):
    admitted(env)
    assert withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "Wrong.", environ=AGENT).returncode == 0
    assert withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "Old.").returncode == 0
    result = env.run("review", "approve", "1")
    assert result.returncode == 1 and "already withdrawn" in result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_decision WHERE review_id = 1") == [(0,)]


def test_a_proposal_is_checked_before_it_is_queued(env):
    admitted(env)
    assert withdraw(env, f"{env.vault}#9", "--wrong", "--reason", "x", environ=AGENT).returncode == 1
    assert withdraw(env, "--batch", "0" * 16, "--wrong", "--reason", "x", environ=AGENT).returncode == 1
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]


def test_a_batch_proposal_counts_entries_withdrawn_meanwhile(env):
    batch, items = imported_batch(env)
    result = withdraw(env, "--batch", batch, "--obsolete", "--reason", "Retired.", environ=AGENT)
    assert result.returncode == 0, result.stderr
    review_id = int(result.stdout.strip().rsplit("#", 1)[1])
    assert f"batch {batch}: 1 admitted entries" in env.run("review", "show", str(review_id)).stdout
    assert withdraw(env, f"{env.vault}#2", "--wrong", "--reason", "Wrong.").returncode == 0
    approved = env.run("review", "approve", str(review_id))
    assert approved.returncode == 0, approved.stderr
    assert approved.stdout.strip().endswith("0 withdrawn, 1 pending rejected, "
                                            "1 not withdrawn (existed before the batch), 1 already withdrawn")


GONE = "fatal: path 'config/routing.yaml' does not exist in 'HEAD'"
REMOVED = {"kind": "command", "reproduction": "git show HEAD:config/routing.yaml", "quote": GONE}
SAID = "that routing file is gone, drop the entry"


def agent(env):
    return {"CLAUDECODE": "1", "VAL3_SESSION_ID": env.session}


def withdraw_with(env, ref, evidence, transcript, environ=None, *extra):
    return env.run("withdraw", ref, "--obsolete", "--reason", "Routing file removed.", "--evidence", "-",
                   "--transcript", str(transcript), *extra, input=json.dumps(evidence), environ=environ)


def printed(item, call_id):
    """A tool item as a direct withdrawal stores it when the fixture's `cat report.txt` call printed its quote."""
    import hashlib
    digest = hashlib.sha256(b'{"command":"cat report.txt"}').hexdigest()
    return {**item, "calls": [{"tool": "Bash", "id": call_id, "input_sha256": digest,
                               "reproduction_is_command": False}]}


def stored(*items):
    """Evidence items as checked and stored: a missing reproduction becomes empty."""
    return [{"reproduction": "", **item} for item in items]


def withdrawal_evidence(env):
    return env.sql(f"SELECT entry_id, withdrawn_by, evidence, review_id FROM {env.vault}.withdrawal ORDER BY entry_id")


def test_the_agent_withdraws_at_once_with_tool_evidence(env):
    admitted(env)
    ref = f"{env.vault}#1"
    t = env.transcript(tool_outputs=[OUTPUT, GONE])
    result = withdraw_with(env, ref, [REMOVED], t, agent(env))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"withdrawn {ref}"
    assert withdrawal_evidence(env) == [(1, f"agent {env.session}", [printed(REMOVED, "t1")], None)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]
    assert ref not in context(start(env), "SessionStart")
    assert env.sql(f"SELECT session_id FROM {env.vault}.event WHERE action = 'withdrawn'") == [(env.session,)]


def test_mixed_user_and_tool_evidence_withdraws_at_once(env):
    admitted(env)
    t = env.transcript(tool_outputs=[OUTPUT, GONE], user_messages=[SAID])
    evidence = [{"kind": "user", "quote": SAID}, REMOVED]
    result = withdraw_with(env, f"{env.vault}#1", evidence, t, agent(env))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"withdrawn {env.vault}#1"
    assert withdrawal_evidence(env)[0][1:] == (f"agent {env.session}", [stored(evidence[0])[0], printed(REMOVED, "t1")], None)


def test_user_evidence_withdraws_a_decision_at_once(env):
    admitted(env)
    decided = "we deploy on Fridays from now on"
    decision = {"kind": "decision", "title": "Friday deploys", "statement": "Production deploys happen on Fridays.",
                "evidence": [{"kind": "user", "quote": decided}]}
    assert save(env, decision, env.transcript(user_messages=[decided])).returncode == 0
    later = {"kind": "user", "quote": "forget the Friday deploys, we deploy any day"}
    t = env.transcript(user_messages=[decided, later["quote"]])
    result = withdraw_with(env, f"{env.vault}#2", [later], t, agent(env))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"withdrawn {env.vault}#2"
    assert withdrawal_evidence(env) == [(2, f"agent {env.session}", stored(later), None)]


def test_a_withdrawal_without_evidence_stays_a_proposal(env):
    admitted(env)
    result = withdraw(env, f"{env.vault}#1", "--wrong", "--reason", "x", environ=agent(env))
    assert result.stdout.strip() == "proposed: review #1", result.stderr
    assert withdrawals(env) == []


def test_withdrawal_evidence_must_be_in_the_transcript(env):
    admitted(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    for environ in (agent(env), None):
        result = withdraw_with(env, f"{env.vault}#1", [REMOVED], t, environ)
        assert result.returncode == 1 and "discarded" in result.stderr, result.stderr
    assert withdrawals(env) == []
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]


def test_a_quote_seen_only_in_val3_output_is_refused(env):
    admitted(env)
    ref = f"{env.vault}#1"
    item = {"kind": "command", "reproduction": "cat routing.yaml", "quote": QUOTE}
    for command in (f"val3 show {ref}", f"val3 'show' {ref}", f'val3 "show" {ref}', f"\\val3 show {ref}"):
        t = env.transcript(tool_calls=[("Bash", {"command": command}, OUTPUT)])
        result = withdraw_with(env, ref, [item], t, agent(env))
        assert result.returncode == 1 and "discarded" in result.stderr, command
        result = env.run("confirm", ref, "--transcript", str(t), input=json.dumps([item]))
        assert result.returncode == 1 and "discarded" in result.stderr, command
    assert withdrawals(env) == []
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]
    t = env.transcript(tool_calls=[("Bash", {"command": "cat routing.yaml"}, OUTPUT)])
    result = env.run("confirm", ref, "--transcript", str(t), input=json.dumps([item]))
    assert result.returncode == 0, result.stderr
    result = withdraw_with(env, ref, [item], t, agent(env))
    assert result.returncode == 0 and result.stdout.strip() == f"withdrawn {ref}", result.stderr


def test_a_batch_takes_no_evidence(env):
    admitted(env)
    t = env.transcript(tool_outputs=[GONE])
    result = env.run("withdraw", "--batch", "0" * 16, "--obsolete", "--reason", "x", "--evidence", "-",
                     "--transcript", str(t), input=json.dumps([REMOVED]))
    assert result.returncode == 2 and "--evidence" in result.stderr


def test_file_evidence_is_refused_for_withdrawal(env):
    admitted(env)
    t = env.transcript(tool_outputs=[GONE])
    item = {"kind": "file", "path": "config/routing.yaml", "quote": "owner: billing"}
    result = withdraw_with(env, f"{env.vault}#1", [item], t, agent(env))
    assert result.returncode == 1 and "accepted only by `val3 confirm`" in result.stderr
    assert withdrawals(env) == []


def test_withdraw_evidence_needs_the_upgraded_table(env):
    admitted(env)
    env.sql(f"ALTER TABLE {env.vault}.withdrawal DROP COLUMN evidence")
    t = env.transcript(tool_outputs=[GONE], user_messages=[SAID])
    said = {"kind": "user", "quote": SAID}
    for evidence, environ in (([REMOVED], agent(env)), ([REMOVED], None), ([said], agent(env))):
        result = withdraw_with(env, f"{env.vault}#1", evidence, t, environ)
        assert result.returncode == 1 and f"val3 vault upgrade {env.vault}" in result.stderr, result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]
    result = withdraw(env, f"{env.vault}#1", "--obsolete", "--reason", "Old.")
    assert result.returncode == 0 and result.stdout.strip() == f"withdrawn {env.vault}#1", result.stderr
    assert env.run("show", f"{env.vault}#1").returncode == 0


def test_a_person_may_give_withdrawal_evidence(env):
    admitted(env)
    t = env.transcript(tool_outputs=[GONE])
    result = withdraw_with(env, f"{env.vault}#1", [REMOVED], t)
    assert result.returncode == 0 and result.stdout.strip() == f"withdrawn {env.vault}#1", result.stderr
    assert withdrawal_evidence(env) == [(1, getpass.getuser(), [printed(REMOVED, "t0")], None)]


def test_show_lists_the_withdrawal_evidence(env):
    admitted(env)
    t = env.transcript(tool_outputs=[GONE], user_messages=[SAID])
    said = {"kind": "user", "quote": SAID}
    assert withdraw_with(env, f"{env.vault}#1", [REMOVED, said], t).returncode == 0
    lines = env.run("show", f"{env.vault}#1").stdout.splitlines()
    assert lines[1].startswith("WITHDRAWN (obsolete) ")
    assert lines[2:7] == ["  withdrawal evidence: command: git show HEAD:config/routing.yaml",
                          f"      > {GONE}", "      printed by: Bash (the line above is a description, not checked)",
                          "  withdrawal evidence: user", f"      > {SAID}"]


def test_withdraw_help_shows_the_evidence_format(env):
    result = env.run("withdraw", "--help")
    assert result.returncode == 0
    assert '"kind": "query | command | api | test | user"' in result.stdout and '"file"' not in result.stdout


def test_val3_output_of_any_subcommand_is_not_evidence(env):
    t = admitted(env)
    assert save(env, fact(title="May has 50 invoices", statement="The ERP holds 50 invoices dated May 2026."),
                t).returncode == 0
    reason = "The ERP counts drafts as invoices."
    assert withdraw(env, f"{env.vault}#2", "--wrong", "--reason", reason).returncode == 0
    item = {"kind": "command", "reproduction": "cat notes.txt", "quote": reason}
    output = f"{env.vault}#2 was withdrawn (wrong): {reason}"
    t = env.transcript(tool_calls=[("Bash", {"command": f"val3 confirm {env.vault}#2 -"}, output)])
    result = withdraw_with(env, f"{env.vault}#1", [item], t, agent(env))
    assert result.returncode == 1 and "discarded" in result.stderr, result.stderr
    assert [row[0] for row in withdrawals(env)] == [2]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]
    for command in ("cd /home/user/val3 && cat config/routing.yaml", "cat /srv/val3/notes.txt"):
        t = env.transcript(tool_calls=[("Bash", {"command": command}, output)])
        result = env.run("confirm", f"{env.vault}#1", "--transcript", str(t), input=json.dumps([item]))
        assert result.returncode == 0, (command, result.stderr)


FABRICATED = "fabricated proof from the model"
ARGPARSE_ERROR = ("usage: val3 [-h] [--version] COMMAND ...\n"
                  f"val3: error: argument COMMAND: invalid choice: '--fabricated-proof={FABRICATED}' "
                  "(choose from 'vault', 'save', 'confirm', 'show', 'review', 'withdraw')")


def test_any_invocation_of_val3_is_not_evidence(env):
    admitted(env)
    ref = f"{env.vault}#1"
    calls = [(f"val3 --fabricated-proof='{FABRICATED}'", ARGPARSE_ERROR, FABRICATED),
             (f'"val3" show {ref}', OUTPUT, QUOTE), (f"'val3' show {ref}", OUTPUT, QUOTE),
             (f"python -mval3 show {ref}", OUTPUT, QUOTE), ("val3", OUTPUT, QUOTE)]
    for command, output, quote in calls:
        item = {"kind": "command", "reproduction": "cat notes.txt", "quote": quote}
        t = env.transcript(tool_calls=[("Bash", {"command": command}, output)])
        result = withdraw_with(env, ref, [item], t, agent(env))
        assert result.returncode == 1 and "discarded" in result.stderr, command
        result = env.run("confirm", ref, "--transcript", str(t), input=json.dumps([item]))
        assert result.returncode == 1 and "discarded" in result.stderr, command
    assert withdrawals(env) == []
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.event WHERE action = 'confirmed'") == [(0,)]


def test_a_pending_proposal_does_not_hold_back_a_withdrawal_with_evidence(env):
    admitted(env)
    ref = f"{env.vault}#1"
    t = env.transcript(tool_outputs=[OUTPUT], user_messages=[SAID])
    result = env.run("withdraw", ref, "--obsolete", "--reason", "Routing file removed.", environ=agent(env))
    assert result.stdout.strip() == "proposed: review #1", result.stderr
    said = {"kind": "user", "quote": SAID}
    result = withdraw_with(env, ref, [said], t, agent(env))
    assert result.stdout.strip() == f"withdrawn {ref}", result.stderr
    assert withdrawal_evidence(env) == [(1, f"agent {env.session}", stored(said), None)]
    approved = env.run("review", "approve", "1")
    assert approved.returncode == 1 and "already withdrawn" in approved.stderr


def test_user_evidence_withdraws_a_fact_at_once(env):
    admitted(env)
    ref = f"{env.vault}#1"
    said = {"kind": "user", "quote": SAID}
    result = withdraw_with(env, ref, [said], env.transcript(tool_outputs=[OUTPUT], user_messages=[SAID]), agent(env))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"withdrawn {ref}"
    assert withdrawal_evidence(env) == [(1, f"agent {env.session}", stored(said), None)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]


def test_a_person_approves_a_legacy_withdrawal_proposal_with_evidence(env):
    admitted(env)
    ref = f"{env.vault}#1"
    legacy(env, {"title": f"withdraw {ref} (obsolete): Routing file removed.", "ref": ref, "cause": "obsolete",
                 "reason": "Routing file removed.", "evidence": [REMOVED]}, "withdrawal", entry_id=1)
    card = env.run("review", "show", "1").stdout
    assert f"  withdrawal evidence: command: {REMOVED['reproduction']}\n      > {GONE}" in card
    approved = env.run("review", "approve", "1", "--by", "alice")
    assert approved.stdout.strip() == f"approved review #1: withdrawn {ref}", approved.stderr
    assert withdrawal_evidence(env) == [(1, "alice", [REMOVED], 1)]


def test_a_blank_batch_is_a_usage_error(env):
    result = withdraw(env, "--batch", "", "--wrong", "--reason", "no such batch")
    assert result.returncode == 2 and "--batch must not be blank" in result.stderr


PSQL = "psql service=dev <<'SQL'\nSELECT name, state FROM route WHERE name = 'gw-old-router-17';\nSQL"
ROWS = "       name       |  state\n------------------+---------\n gw-old-router-17 | removed\n(1 row)"


def test_a_row_psql_computed_from_a_heredoc_withdraws(env):
    """The SQL sits in the call's input; the row it returned does not, so it is evidence."""
    admitted(env)
    ref = f"{env.vault}#1"
    t = env.transcript(tool_calls=[("Bash", {"command": PSQL}, ROWS)])
    item = {"kind": "query", "reproduction": "select name, state from route", "quote": "gw-old-router-17 | removed"}
    result = withdraw_with(env, ref, [item], t, agent(env))
    assert result.returncode == 0 and result.stdout.strip() == f"withdrawn {ref}", result.stderr


def test_a_value_psql_was_given_in_its_where_does_not_withdraw(env):
    """The output repeats a value of the WHERE: quoting only that value proves nothing the call did not hold."""
    admitted(env)
    t = env.transcript(tool_calls=[("Bash", {"command": PSQL}, ROWS)])
    item = {"kind": "query", "reproduction": "select name, state from route", "quote": "gw-old-router-17"}
    result = withdraw_with(env, f"{env.vault}#1", [item], t, agent(env))
    assert result.returncode == 1 and "is in the input of the call that printed it" in result.stderr, result.stderr
    assert withdrawals(env) == []


# The calls that printed a quote (ADR 0006, amendment 1)

def sha(value):
    import hashlib
    body = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False,
                                                           separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def stored_evidence(env):
    [(evidence,)] = env.sql(f"SELECT evidence FROM {env.vault}.withdrawal")
    return evidence


SHOW = "git show HEAD:config/routing.yaml"


def test_a_withdrawal_records_the_call_that_printed_the_quote_and_never_its_text(env):
    admitted(env)
    secret = "zz-token-9f31"
    call = {"command": f"TOKEN={secret} {SHOW}", "description": "look at the file"}
    t = env.transcript(tool_calls=[("Bash", call, GONE)])
    result = withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env))
    assert result.returncode == 0, result.stderr
    assert stored_evidence(env) == [{**REMOVED, "calls": [
        {"tool": "Bash", "id": "call0", "input_sha256": sha(call), "reproduction_is_command": False}]}]
    rows = env.sql(f"SELECT w::text FROM {env.vault}.withdrawal w") + env.sql(
        f"SELECT e::text FROM {env.vault}.event e")
    shown = env.run("show", f"{env.vault}#1").stdout
    assert secret not in str(rows) and secret not in shown and secret not in result.stdout + result.stderr
    assert "      printed by: Bash (the line above is a description, not checked)" in shown.splitlines()
    with viewing(env) as view:
        entry = view.json(f"api/entry?ref={env.vault}%231")["entry"]
    assert secret not in json.dumps(entry)
    assert entry["withdrawal"]["evidence"][0]["calls"][0]["input_sha256"] == sha(call)


def test_a_reproduction_equal_to_the_command_is_marked(env):
    admitted(env)
    t = env.transcript(tool_calls=[("Bash", {"command": f"  {SHOW}\n", "description": "x"}, GONE)])
    assert withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env)).returncode == 0
    [item] = stored_evidence(env)
    assert item["calls"][0]["reproduction_is_command"] is True
    assert "      printed by: Bash (the command above, checked)" in env.run("show", f"{env.vault}#1").stdout.splitlines()


def test_every_call_that_printed_the_quote_is_recorded_without_a_winner(env):
    """Two queries about different objects print the same line; one result arrives twice; a call that carries
    the quote in its input and a val3 call do not count."""
    admitted(env)
    a, b = {"command": "state route-a"}, {"command": "state route-b"}
    t = env.transcript(tool_calls=[("Bash", a, GONE), ("Bash", {"command": f"echo \"{GONE}\""}, GONE),
                                   ("Bash", {"command": f"val3 show {env.vault}#1"}, GONE), ("Bash", b, GONE)])
    lines = t.read_text().splitlines()
    t.write_text("\n".join(lines + [lines[1]]) + "\n")  # the first call's result once more, after the others
    assert withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env)).returncode == 0
    [item] = stored_evidence(env)
    assert [(c["id"], c["input_sha256"]) for c in item["calls"]] == [("call0", sha(a)), ("call3", sha(b))]
    assert "more_calls" not in item
    assert ("      printed by 2 calls: Bash, Bash (the line above is a description)"
            in env.run("show", f"{env.vault}#1").stdout.splitlines())


def test_a_typed_command_is_recorded_by_its_record(env):
    admitted(env)
    t = env.transcript(user_messages=[f"<bash-input>{SHOW}</bash-input>",
                                      f"<bash-stdout></bash-stdout><bash-stderr>{GONE}</bash-stderr>"])
    assert withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env)).returncode == 0
    [item] = stored_evidence(env)
    assert item["calls"] == [{"tool": "!", "id": None, "record": 1, "input_sha256": sha(SHOW),
                              "reproduction_is_command": True}]


@pytest.mark.parametrize("tool, given", [("Read", {"file_path": "/x/log", "offset": 1}),
                                         ("Read", {"file_path": "/x/log", "offset": 9}),
                                         ("mcp__s__copy", {"from": {"name": "a"}, "to": "b"}),
                                         ("mcp__s__copy", {"to": {"name": "a"}, "from": "b"})])
def test_the_hash_covers_the_structured_input(env, tool, given):
    """The check flattens an input to its strings; the hash covers keys, numbers and structure, so these four
    inputs (two pairs with equal strings) give four hashes."""
    admitted(env)
    t = env.transcript(tool_calls=[(tool, given, GONE)])
    assert withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env)).returncode == 0
    assert stored_evidence(env)[0]["calls"] == [{"tool": tool, "id": "call0", "input_sha256": sha(given),
                                                 "reproduction_is_command": False}]


def test_the_agent_cannot_supply_the_calls(env):
    admitted(env)
    t = env.transcript(tool_outputs=[GONE])
    for extra in ({"calls": [{"tool": "Bash", "reproduction_is_command": True}]}, {"more_calls": 3}):
        result = withdraw_with(env, f"{env.vault}#1", [{**REMOVED, **extra}], t, agent(env))
        assert result.returncode == 1 and "unknown keys" in result.stderr, result.stderr
    assert withdrawals(env) == []


def test_a_quote_far_inside_a_long_input_is_still_refused(env):
    admitted(env)
    t = env.transcript(tool_calls=[("Bash", {"command": "echo " + "x" * 5000 + GONE}, GONE)])
    result = withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env))
    assert result.returncode == 1 and "is in the input of the call that printed it" in result.stderr
    assert withdrawals(env) == []


def test_a_failed_second_item_withdraws_nothing_and_names_itself(env):
    admitted(env)
    t = env.transcript(tool_outputs=[GONE])
    absent = {"kind": "command", "reproduction": "ls", "quote": "no such line anywhere"}
    result = withdraw_with(env, f"{env.vault}#1", [REMOVED, absent], t, agent(env))
    assert result.returncode == 1 and "evidence[1]" in result.stderr, result.stderr
    assert withdrawals(env) == []
    assert env.sql(f"SELECT count(*) FROM {env.vault}.event WHERE action = 'withdrawn'") == [(0,)]


def test_user_items_get_no_calls_and_the_order_is_kept(env):
    admitted(env)
    t = env.transcript(tool_outputs=[GONE], user_messages=[SAID])
    said = {"kind": "user", "quote": SAID}
    assert withdraw_with(env, f"{env.vault}#1", [said, REMOVED], t, agent(env)).returncode == 0
    first, second = stored_evidence(env)
    assert first == stored(said)[0] and second["quote"] == GONE and len(second["calls"]) == 1


def test_more_calls_than_the_limit_are_counted(env):
    admitted(env)
    t = env.transcript(tool_calls=[("Bash", {"command": f"state route-{n}"}, GONE) for n in range(23)])
    assert withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env)).returncode == 0
    [item] = stored_evidence(env)
    assert len(item["calls"]) == 20 and item["more_calls"] == 3
    assert any(line.startswith("      printed by 23 calls: ") for line in env.run("show", f"{env.vault}#1").stdout.splitlines())


def test_a_last_record_still_being_written_is_not_read(env):
    """A complete result on a last line without its line break: reading it would add a second call."""
    admitted(env)
    t = env.transcript(tool_calls=[("Bash", {"command": "state route-a"}, GONE), ("Bash", {"command": "state route-b"}, GONE)])
    t.write_text(t.read_text().rstrip("\n"))
    assert withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env)).returncode == 0
    assert [call["id"] for call in stored_evidence(env)[0]["calls"]] == ["call0"]


def test_results_in_inverse_order_and_one_id_declared_twice(env):
    admitted(env)
    a, b, c = {"command": "state route-a"}, {"command": "state route-b"}, {"command": SHOW}
    t = env.transcript(tool_calls=[("Bash", a, GONE), ("Bash", b, GONE)])
    use_a, result_a, use_b, result_b = t.read_text().splitlines()
    again = json.loads(use_a)
    again["message"]["content"][0]["input"] = c  # the same id declared again with another input
    t.write_text("\n".join([use_a, use_b, result_b, result_a, json.dumps(again), result_a]) + "\n")
    assert withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env)).returncode == 0
    calls = stored_evidence(env)[0]["calls"]
    assert [(call["id"], call["input_sha256"], call["reproduction_is_command"]) for call in calls] == [
        ("call0", sha(c), True), ("call1", sha(b), False), ("call0", sha(a), False)]


def test_the_limit_never_hides_the_call_that_ran_the_reproduction(env):
    admitted(env)
    t = env.transcript(tool_calls=[("Bash", {"command": f"state route-{n}"}, GONE) for n in range(22)]
                       + [("Bash", {"command": SHOW}, GONE)])
    assert withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env)).returncode == 0
    [item] = stored_evidence(env)
    assert item["calls"][0]["id"] == "call22" and item["calls"][0]["reproduction_is_command"] and item["more_calls"] == 3
    assert ("      printed by 23 calls: " + ", ".join(["Bash"] * 20) + ", 3 more not listed (1 ran the command above, checked)"
            in env.run("show", f"{env.vault}#1").stdout.splitlines())


def test_a_line_break_inside_the_command_is_part_of_it(env):
    admitted(env)
    t = env.transcript(tool_calls=[("Bash", {"command": "git show \\\r\nHEAD:config/routing.yaml"}, GONE)])
    item = {**REMOVED, "reproduction": "git show \\\nHEAD:config/routing.yaml"}
    assert withdraw_with(env, f"{env.vault}#1", [item], t, agent(env)).returncode == 0
    assert stored_evidence(env)[0]["calls"][0]["reproduction_is_command"] is False


@pytest.mark.parametrize("name, call_id", [(["Bash"], "call0"), ("Ba\x00sh", "id\x00x"), ("Bash, Bash\tRead", "id\ud800"),
                                           ("\u202eBash", "call0")])
def test_a_hostile_tool_name_or_id_neither_stops_the_withdrawal_nor_reaches_the_screen(env, name, call_id):
    admitted(env)
    t = env.transcript(tool_calls=[(name, {"command": SHOW}, GONE)])
    t.write_text(t.read_text().replace('"call0"', json.dumps(call_id)))
    result = withdraw_with(env, f"{env.vault}#1", [REMOVED], t, agent(env))
    assert result.returncode == 0, result.stderr
    [call] = stored_evidence(env)[0]["calls"]
    assert call["tool"] is None and call["id"].isascii() and "\x00" not in call["id"]
    assert "      printed by: ? (the line above is a description, not checked)" in env.run(
        "show", f"{env.vault}#1").stdout.splitlines()


def test_a_tool_name_that_is_not_text_does_not_break_save(env):
    make_vault(env)
    t = env.transcript(tool_calls=[(["Write"], {"x": 1}, "nothing"), ("Bash", {"command": "psql"}, OUTPUT)])
    lines = t.read_text().splitlines()
    odd = json.loads(lines[1])
    odd["message"]["content"][0]["tool_use_id"] = {"not": "text"}
    t.write_text("\n".join([lines[0], json.dumps(odd), *lines[2:]]) + "\n")
    assert save(env, fact(), t).returncode == 0
