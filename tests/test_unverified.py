"""ADR 0012 on an upgraded vault (`vault create` makes one): what has no evidence is admitted unverified, with less
authority; nothing waits for a person."""
import getpass
import json

from helpers import OUTPUT, QUOTE, TOOL, embedding_vault, fact, make_vault, old_vault, recipe, save
from test_import import apply, manifest, note_candidate, verified_manifest

AGENT = {"CLAUDECODE": "1"}
QUERY = [{"kind": "query", "reproduction": "SELECT count(*) AS invoice_count FROM invoice", "quote": QUOTE}]


def tool(title="graph-cli", **changes):
    return {**TOOL, "title": title, **changes}


def tool_add(env, name="code-graph", description="Answers which module overrides a method."):
    result = env.run("tool", "add", name, "--type", "cli", "--description", description)
    assert result.returncode == 0, result.stderr
    return result


def undecided(env):
    return env.sql(f"SELECT count(*) FROM {env.vault}.review_item r WHERE NOT EXISTS "
                   f"(SELECT 1 FROM {env.vault}.review_decision d WHERE d.review_id = r.id)")[0][0]


def test_save_admits_a_candidate_without_evidence_as_unverified(env):
    make_vault(env)
    result = save(env, tool(), env.transcript())
    assert (result.returncode, result.stdout) == (0, f"admitted unverified {env.vault}#1\n"), result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.attestation") == [(0,)]
    again = save(env, tool(), env.transcript())
    assert again.stdout == f"duplicate of {env.vault}#1\n"


def test_a_fact_preference_or_decision_without_evidence_is_admitted_unverified(env):
    """ADR 0012, amendment 1, point 1."""
    make_vault(env)
    for n, kind in enumerate(("fact", "preference", "decision"), start=1):
        result = save(env, fact(kind=kind, title=f"{kind} without evidence", evidence=[]), env.transcript())
        assert (result.returncode, result.stdout) == (0, f"admitted unverified {env.vault}#{n}\n"), result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.evidence") == [(0,)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]


def test_a_fact_without_evidence_is_queued_on_a_vault_not_upgraded(env):
    """Amendment 1, point 1: a deliberate exception to the old rules, which refused it."""
    old_vault(env)
    result = save(env, fact(evidence=[]), env.transcript())
    assert (result.returncode, result.stdout) == (0, "queued for review: review #1 (no-evidence)\n"), result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(0,)]


def test_tool_add_attests_the_tool(env):
    make_vault(env)
    tool_add(env)
    assert env.sql(f"SELECT entry_id, attested_by, how FROM {env.vault}.attestation") == [
        (1, getpass.getuser(), "tool add")]


def test_an_unverified_entry_does_not_supersede_one_with_backing(env):
    make_vault(env)
    v = env.vault
    tool_add(env)  # #1, attested
    result = save(env, tool(supersedes=[f"{v}#1"]), env.transcript())
    assert (result.returncode, result.stdout) == (0, f"admitted unverified {v}#2\n"), result.stderr
    assert result.stderr == (f"val3: warning: {v}#2 does not supersede {v}#1: an unverified entry cannot supersede "
                             "an entry with evidence or attestation\n")
    newer = save(env, tool("graph-cli-2", supersedes=[f"{v}#2"]), env.transcript())  # unverified over unverified
    assert (newer.stdout, newer.stderr) == (f"admitted unverified {v}#3\n", "")
    assert env.sql(f"SELECT old_id, new_id FROM {v}.supersession") == [(2, 3)]


def test_settle_replace_and_drop_follow_the_backing(env, embedder):
    embedding_vault(env, embedder.url)
    v = env.vault
    tool_add(env, "code-graph", "Answers which module overrides a method")  # #1, attested
    near = save(env, tool("code-graph-cli", statement="Answers which module overrides a method"), env.transcript())
    assert near.stdout == f"admitted unverified {v}#2\n" and f"near {v}#1" in near.stderr, near.stderr
    refused = env.run("settle", f"{v}#2", f"{v}#1", "--replace")
    assert refused.returncode == 1
    assert refused.stderr == (f"val3: error: {v}#2 cannot supersede {v}#1: an unverified entry cannot supersede an "
                              "entry with evidence or attestation\n")
    dropped = env.run("settle", f"{v}#2", f"{v}#1", "--drop")  # toward an entry of higher backing
    assert dropped.stdout == f"dropped {v}#2 (duplicate of {v}#1)\n", dropped.stderr


def test_drop_toward_an_entry_of_lower_backing_is_refused(env, embedder):
    embedding_vault(env, embedder.url)
    v = env.vault
    assert save(env, tool("code-graph-cli", statement="Answers which module overrides a method"),
                env.transcript()).stdout == f"admitted unverified {v}#1\n"
    tool_add(env, "code-graph", "Answers which module overrides a method")  # #2, attested, near #1
    refused = env.run("settle", f"{v}#2", f"{v}#1", "--drop")
    assert refused.returncode == 1
    assert refused.stderr == (f"val3: error: {v}#2 is attested and {v}#1 unverified: a duplicate is withdrawn only "
                              "toward an entry of equal or higher backing\n")
    assert env.run("settle", f"{v}#2", f"{v}#1", "--replace").returncode == 0  # attested over unverified


def test_a_recipe_cannot_cite_an_unverified_entry(env):
    make_vault(env)
    v = env.vault
    assert save(env, tool(), env.transcript()).stdout == f"admitted unverified {v}#1\n"
    env.sql(f"INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
            "VALUES ('fact', 'raw', 'a fact without evidence', 'h', 'p', 'm')")  # #2
    for steps in ([{"action": "Ask the graph", "tool": f"{v}#1"}], [{"action": "Use it", "facts": [f"{v}#2"]}]):
        result = save(env, recipe(f"{v}#2", recipe={"steps": steps}), env.transcript())
        cited = steps[0].get("tool") or steps[0]["facts"][0]
        assert (result.returncode, result.stderr) == (
            1, f"val3: error: {cited} is unverified: a recipe cites only entries with evidence or attestation\n")
    tool_add(env)  # #3, attested
    cites = save(env, recipe(f"{v}#3", recipe={"steps": [{"action": "Ask the graph", "tool": f"{v}#3"}]}),
                 env.transcript())
    assert cites.stdout == f"admitted unverified {v}#4\n", cites.stderr


def unverified_decision(env):
    """An import whose quote did not verify: entry #2, a decision without evidence nor attestation."""
    path = verified_manifest(env, note_candidate(evidence=[{"path": "docs/adr/0001.md",
                                                            "quote": "we never decided this at all"}]))
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    return result


def test_an_import_whose_quotes_did_not_all_verify_is_admitted_unverified(env):
    result = unverified_decision(env)
    batch = manifest(env)["batch"]
    assert result.stdout.strip() == (f"batch {batch}: 2 admitted, 0 queued, 0 already queued, 0 previously rejected, "
                                     "0 duplicate, 0 already imported, 0 refused")
    assert env.sql(f"SELECT count(*) FROM {env.vault}.evidence WHERE entry_id = 2") == [(0,)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.attestation") == [(0,)]
    assert env.sql(f"SELECT d.decision, d.decided_by, d.entry_id, i.payload->'import'->'evidence'->0->>'status' "
                   f"FROM {env.vault}.review_decision d JOIN {env.vault}.review_item i ON i.id = d.review_id "
                   "WHERE d.review_id = 2") == [("approved", "val3 (mechanical)", 2, "mismatched")]
    assert undecided(env) == 0


def test_a_partly_verified_import_keeps_no_evidence(env):
    path = verified_manifest(env, note_candidate(evidence=[
        {"path": "docs/adr/0001.md", "quote": "route every message by its thread owner"},
        {"path": "docs/adr/0001.md", "quote": "we never decided this at all"}]))
    assert apply(env, path).returncode == 0
    assert env.sql(f"SELECT count(*) FROM {env.vault}.evidence WHERE entry_id = 2") == [(0,)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.attestation") == [(0,)]


def test_an_import_without_quotes_is_attested_by_the_person_who_ran_it(env):
    path = verified_manifest(env, note_candidate(evidence=[]))
    assert apply(env, path).returncode == 0
    batch = manifest(env)["batch"]
    assert env.sql(f"SELECT entry_id, attested_by, how FROM {env.vault}.attestation") == [
        (2, getpass.getuser(), f"import --apply, batch {batch}")]


def test_evidence_added_later_verifies_an_unverified_entry(env):
    unverified_decision(env)
    v = env.vault
    quote = "route every message by its thread owner"
    transcript = env.transcript(user_messages=[f"We {quote}."])
    candidate = {**{k: note_candidate()[k] for k in ("kind", "title", "statement", "keywords")},
                 "evidence": [{"kind": "user", "quote": quote}]}
    result = save(env, candidate, transcript)
    assert (result.returncode, result.stdout) == (0, f"verified {v}#2\n"), result.stderr
    assert env.sql(f"SELECT kind, quote, session_id FROM {v}.evidence WHERE entry_id = 2") == [
        ("user", quote, env.session)]
    assert env.sql(f"SELECT entry_id FROM {v}.event WHERE action = 'verified'") == [(2,)]
    again = save(env, candidate, transcript)
    assert again.stdout == f"duplicate of {v}#2\n"
    assert env.sql(f"SELECT count(*) FROM {v}.evidence WHERE entry_id = 2") == [(1,)]


def test_an_import_whose_quotes_all_verified_verifies_an_equal_unverified_entry(env):
    """Amendment 1, point 6: under the import's lock; the batch keeps its record, the entry existed before it."""
    path = verified_manifest(env)
    v = env.vault
    same = {k: note_candidate()[k] for k in ("kind", "title", "statement", "keywords")}
    assert save(env, same, env.transcript()).stdout == f"admitted unverified {v}#1\n"
    result = apply(env, path)
    batch = manifest(env)["batch"]
    assert result.stdout.strip() == (f"batch {batch}: 1 admitted, 0 queued, 0 already queued, 0 previously rejected, "
                                     "1 duplicate, 0 already imported, 0 refused"), result.stderr
    assert env.sql(f"SELECT kind, quote FROM {v}.evidence WHERE entry_id = 1") == [
        ("file", "route every message by its thread owner")]
    assert env.sql(f"SELECT entry_id FROM {v}.event WHERE action = 'verified'") == [(1,)]
    assert env.sql(f"SELECT entry_id FROM {v}.review_decision ORDER BY entry_id") == [(1,), (2,)]
    done = withdraw(env, "--batch", batch, "--obsolete", "--reason", "r", tty=True)
    assert done.stdout.strip() == (f"batch {batch}: 1 withdrawn, 0 pending rejected, "
                                   "1 not withdrawn (existed before the batch), 0 already withdrawn"), done.stderr


def test_a_recipe_or_a_tool_is_not_verified_by_saving_it_again(env):
    make_vault(env)
    assert save(env, tool(), env.transcript()).stdout == f"admitted unverified {env.vault}#1\n"
    result = save(env, tool(evidence=QUERY), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 1
    assert result.stderr == (f"val3: error: {env.vault}#1 is unverified, and a tool is not verified by saving it "
                             "again: its hash does not cover its steps, type or environment\n")


def test_capture_admits_unverified_and_queues_nothing(env):
    make_vault(env)
    env.respond([{"note": None, "candidate": tool()}])
    result = env.run("extract", str(env.transcript(tool_outputs=[OUTPUT])))
    assert result.returncode == 0, result.stderr
    assert ": admitted 1, duplicate 0, repeat 0, queued 0," in result.stdout
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]


FAILING = {"kind": "query", "reproduction": "SELECT 1", "quote": "not in the transcript at all"}


def test_capture_admits_a_candidate_whose_quote_fails_unverified_without_any_of_its_evidence(env):
    """Amendment 1, point 2: one failed quote takes the backing of the whole candidate, valid quotes included."""
    make_vault(env)
    v = env.vault
    env.respond([{"note": None, "candidate": fact(evidence=fact()["evidence"] + [FAILING])}])
    result = env.run("extract", str(env.transcript(tool_outputs=[OUTPUT])))
    assert result.returncode == 0, result.stderr
    assert ": admitted 1, duplicate 0, repeat 0, " in result.stdout
    assert "rejected 0, unverified 1, quote-failed 1;" in result.stdout, result.stdout
    assert env.sql(f"SELECT count(*) FROM {v}.evidence") == [(0,)]
    assert env.run("show", f"{v}#1").stdout.count("UNVERIFIED") == 1
    assert env.sql(f"SELECT detail FROM {v}.event WHERE action = 'admitted'") == [
        ({"via": "capture", "failed_quotes": 1},)]
    [(detail,)] = env.sql(f"SELECT detail FROM {v}.event WHERE action = 'discarded'")
    assert detail["title"] == fact()["title"]


def test_save_still_fails_on_a_quote_that_does_not_check(env):
    make_vault(env)
    result = save(env, fact(evidence=fact()["evidence"] + [FAILING]), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 1 and "discarded: evidence quote not found" in result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(0,)]


def test_capture_rejects_a_failed_quote_on_a_vault_not_upgraded(env):
    old_vault(env)
    env.respond([{"note": None, "candidate": fact(evidence=[FAILING])}])
    result = env.run("extract", str(env.transcript(tool_outputs=[OUTPUT])))
    assert result.returncode == 0, result.stderr
    assert "rejected 1, unverified 0, quote-failed 0;" in result.stdout, result.stdout
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(0,)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.event WHERE action = 'discarded'") == [(1,)]


def test_capture_still_rejects_a_malformed_candidate(env):
    make_vault(env)
    env.respond([{"note": None, "candidate": fact(evidence=[{**FAILING, "quote": "short"}])}])
    result = env.run("extract", str(env.transcript(tool_outputs=[OUTPUT])))
    assert "admitted 0," in result.stdout and "rejected 1, unverified 0, quote-failed 0;" in result.stdout
    assert env.sql(f"SELECT action FROM {env.vault}.event") == [("invalid",)]


def withdraw(env, *args, environ=None, tty=False):
    return env.run("withdraw", *args, environ=environ, tty=tty)


def test_a_withdrawal_without_evidence_is_for_a_person_at_a_terminal(env):
    make_vault(env)
    v = env.vault
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).stdout == f"admitted {v}#1\n"
    message = ("val3: error: a withdrawal without evidence, or of a batch, is a person's: run it in a terminal "
               "outside the agent session (a withdrawal is final, and the same text cannot be saved again)\n")
    for environ, tty in ((AGENT, True), (None, False)):
        refused = withdraw(env, f"{v}#1", "--wrong", "--reason", "r", environ=environ, tty=tty)
        assert (refused.returncode, refused.stderr) == (1, message)
    assert env.sql(f"SELECT count(*) FROM {v}.review_item") == [(0,)]
    done = withdraw(env, f"{v}#1", "--wrong", "--reason", "r", tty=True)
    assert (done.returncode, done.stdout) == (0, f"withdrawn {v}#1\n"), done.stderr
    assert env.sql(f"SELECT withdrawn_by FROM {v}.withdrawal") == [(getpass.getuser(),)]


def test_a_batch_withdrawal_is_for_a_person_at_once(env):
    unverified_decision(env)
    batch = manifest(env)["batch"]
    refused = withdraw(env, "--batch", batch, "--obsolete", "--reason", "r", environ=AGENT, tty=True)
    assert refused.returncode == 1 and "is a person's" in refused.stderr
    done = withdraw(env, "--batch", batch, "--obsolete", "--reason", "r", tty=True)
    assert done.stdout.strip() == (f"batch {batch}: 2 withdrawn, 0 pending rejected, "
                                   "0 not withdrawn (existed before the batch), 0 already withdrawn"), done.stderr


def test_an_agent_withdrawal_with_evidence_is_at_once(env):
    make_vault(env)
    v = env.vault
    transcript = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), transcript).stdout == f"admitted {v}#1\n"
    evidence = json.dumps(QUERY)
    done = env.run("withdraw", f"{v}#1", "--obsolete", "--reason", "r", "--evidence", "-", "--transcript",
                   str(transcript), input=evidence, environ=AGENT)
    assert (done.returncode, done.stdout) == (0, f"withdrawn {v}#1\n"), done.stderr


def test_no_command_leaves_an_undecided_review_item(env):
    make_vault(env)
    v = env.vault
    transcript = env.transcript(tool_outputs=[OUTPUT])
    save(env, fact(), transcript)
    save(env, tool(), transcript)
    save(env, recipe(f"{v}#1"), transcript)
    tool_add(env)
    withdraw(env, f"{v}#2", "--wrong", "--reason", "r", environ=AGENT)
    env.respond([{"note": None, "candidate": tool("captured-tool")}])
    env.run("extract", str(transcript))
    assert undecided(env) == 0
