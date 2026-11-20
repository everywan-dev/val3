import json
import sys
import time
from datetime import datetime, timezone

import pytest
from helpers import OUTPUT, QUOTE, before_0012, embedding_vault, fact, make_vault, save
from test_inbox import memdir, write_note
from test_settle import OLD_SCHEMA

STATEMENT = ("The ERP holds 42 invoices dated April 2026, counted on the invoice table by issue date, excluding "
             "drafts and cancelled invoices, including the credit notes issued in the same month, with the count "
             "taken after the nightly import from the billing system finished and the totals reconciled with the "
             "ledger closing report of the finance team, as kept in the shared folder of quarterly paperwork.")
STORED = fact(statement=STATEMENT)
CANDIDATE = fact(title="April has 43 invoices", statement=STATEMENT.replace("42", "43"))
OTHER = fact(title="Ledger closes monthly", statement="Closing happens on day one of each month.")
AT = "2026-10-20T10:00:00.000Z"
SCHEMA = {"type": "object", "required": ["decisions"], "properties": {"decisions": {"type": "array", "items": {
    "type": "object", "required": ["candidate", "decision"], "properties": {
        "candidate": {"type": "integer"}, "decision": {"enum": ["new", "repeat", "replaces", "undecided"]},
        "refs": {"type": "array", "items": {"type": "string"}}}}}}}


def stored(env, *candidates):
    t = env.transcript(tool_outputs=[OUTPUT])
    for candidate in candidates:
        result = save(env, candidate, t)
        assert result.returncode == 0 and result.stdout.startswith("admitted"), result.stdout + result.stderr
    return t


def extract(env, *candidates):
    t = env.transcript(user_messages=["count the April invoices"], tool_outputs=[OUTPUT], at=AT)
    env.respond([{"note": None, "candidate": candidate} for candidate in candidates])
    result = env.run("extract", str(t))
    assert result.returncode == 0, result.stderr
    return result


def argument(call, flag):
    return call["argv"][call["argv"].index(flag) + 1]


def test_a_candidate_with_a_neighbour_gets_a_second_call(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    result = extract(env, CANDIDATE)
    calls = env.calls()
    assert len(calls) == 2
    stdin = calls[1]["stdin"]
    assert stdin.startswith(f"# Session slice of project project (home vault {v}); last record {AT}\n"), stdin
    assert "\n## Candidates with close entries\n### Candidate 0\n" in stdin
    assert f"\n#### Close entry {v}#1 (similarity " in stdin
    assert "shared folder of quarterly paperwork." in stdin
    assert "\n## Transcript\n" in stdin and "USER: count the April invoices" in stdin
    assert json.loads(argument(calls[1], "--json-schema")) == SCHEMA
    assert argument(calls[1], "--system-prompt").startswith("Prompt version: 2.")
    assert "2 extractor calls" in result.stdout


def test_no_neighbour_no_second_call(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, OTHER)
    result = extract(env, fact())
    assert len(env.calls()) == 1 and "admitted 1" in result.stdout, result.stdout
    assert env.sql(f"SELECT count(*) FROM {v}.embedding WHERE entry_id = 2") == [(1,)]


def test_a_named_supersedes_joins_the_neighbours(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED, OTHER)
    extract(env, {**CANDIDATE, "supersedes": f"{v}#2"})
    calls = env.calls()
    assert len(calls) == 2
    stdin = calls[1]["stdin"]
    assert f"\n#### Close entry {v}#1 (similarity " in stdin
    assert f'\n#### Close entry {v}#2 (named in "supersedes")\n' in stdin
    assert stdin.index(f"Close entry {v}#1") < stdin.index(f"Close entry {v}#2")


def test_candidates_of_one_answer_are_not_compared_with_each_other(env, embedder):
    embedding_vault(env, embedder.url)
    result = extract(env, STORED, CANDIDATE)
    assert len(env.calls()) == 1 and "1 extractor calls" in result.stdout, result.stdout


def test_without_embedding_capture_is_unchanged(env):
    make_vault(env)
    stored(env, STORED)
    result = extract(env, CANDIDATE)
    assert len(env.calls()) == 1 and "admitted 1" in result.stdout, result.stdout


def test_a_vault_without_neighbour_tables_is_unchanged(env, embedder):
    embedding_vault(env, embedder.url)
    env.sql(OLD_SCHEMA.format(v=env.vault))
    stored(env, STORED)
    result = extract(env, CANDIDATE)
    assert len(env.calls()) == 1 and "admitted 1" in result.stdout, result.stdout


def test_embedding_service_down_submits_as_today(env):
    v = env.vault
    embedding_vault(env, "http://127.0.0.1:1")
    result = extract(env, fact())
    assert len(env.calls()) == 1 and "admitted 1" in result.stdout, result.stdout
    assert env.sql(f"SELECT count(*) FROM {v}.embedding") == [(0,)]


def test_the_slice_date_is_its_last_record_even_unrendered(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    t = env.transcript(user_messages=["count the April invoices"], tool_outputs=[OUTPUT], at=AT)
    later = "2026-10-21T08:30:00.000Z"
    env.append(t, {"type": "assistant", "timestamp": later,
                   "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"}]}})
    env.respond([{"note": None, "candidate": CANDIDATE}])
    assert env.run("extract", str(t)).returncode == 0
    assert env.calls()[1]["stdin"].startswith(
        f"# Session slice of project project (home vault {v}); last record {later}\n")


def test_a_vector_of_another_size_does_not_stop_the_comparison(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED, OTHER)
    [(model, digest)] = env.sql(f"SELECT DISTINCT model, digest FROM {v}.embedding")
    env.sql(f"INSERT INTO {v}.embedding (entry_id, model, digest, text_version, dims, vector) "
            "VALUES (2, %s, %s, 2, 3, '[1,2,3]')", (model, digest))
    result = extract(env, CANDIDATE)
    assert len(env.calls()) == 2 and "admitted 1" in result.stdout, result.stdout + result.stderr
    assert f"#### Close entry {v}#1 (similarity " in env.calls()[1]["stdin"]


# Task 3: capture applies the second answer.
LATER = "2100-01-01T00:00:00Z"
NEAR = fact(title="April has 41 invoices", statement=STATEMENT.replace("42", "41"))
THIRD = fact(title="April has 44 invoices", statement=STATEMENT.replace("42", "44"))
FOURTH = fact(title="April has 45 invoices", statement=STATEMENT.replace("42", "45"))


def answer(env, candidates, decisions, at=LATER, note=None):
    """Extract one slice: first answer `candidates` (the one at index `note` names the memory note april.md),
    second answer `decisions`."""
    t = env.transcript(user_messages=["count the April invoices"], tool_outputs=[OUTPUT], at=at)
    env.respond([{"note": "april.md" if i == note else None, "candidate": candidate}
                 for i, candidate in enumerate(candidates)], {"decisions": decisions})
    result = env.run("extract", str(t))
    assert result.returncode == 0, result.stderr
    return result


def during_second_call(env, *argv):
    (env.fake / "command-1.json").write_text(json.dumps(
        {"argv": [sys.executable, "-m", "val3", *argv], "cwd": str(env.project)}))


def settlements(env):
    return env.sql(f"SELECT entry_id, other_id, outcome, settled_by FROM {env.vault}.settlement "
                   "ORDER BY entry_id, other_id")


def supersessions(env):
    return env.sql(f"SELECT old_id, new_id FROM {env.vault}.supersession ORDER BY old_id")


def entries(env):
    return [i for (i,) in env.sql(f"SELECT id FROM {env.vault}.entry ORDER BY id")]


def events(env, action):
    return env.sql(f"SELECT entry_id, detail FROM {env.vault}.event WHERE action = %s ORDER BY id", (action,))


def test_new_is_admitted_and_the_pair_kept(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    answer(env, [CANDIDATE], [{"candidate": 0, "decision": "new"}])
    assert entries(env) == [1, 2]
    assert env.sql(f"SELECT count(*) FROM {v}.embedding WHERE entry_id = 2") == [(1,)]
    assert settlements(env) == [(2, 1, "keep", f"extractor {env.session}")]
    assert events(env, "settled") == [(2, {"other": 1, "outcome": "keep", "by": f"extractor {env.session}"})]
    assert "TO SETTLE" not in env.run("show", f"{v}#2").stdout


def test_repeat_is_not_admitted(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    directory = memdir(env)
    write_note(directory, env.session)
    result = answer(env, [CANDIDATE], [{"candidate": 0, "decision": "repeat", "refs": [f"{v}#1"]}], note=0)
    assert entries(env) == [1]
    assert "admitted 0, duplicate 0, repeat 1," in result.stdout, result.stdout
    [(entry_id, detail)] = events(env, "repeat")
    assert entry_id == 1 and detail["candidate"]["title"] == CANDIDATE["title"]
    assert detail["by"] == f"extractor {env.session}"
    assert f"Recorded in VAL3 as {v}#1 (verified)" in (directory / "april.md").read_text()


def test_a_repeat_with_a_quote_not_in_the_transcript_loses_its_evidence(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)  # upgraded: ADR 0012, amendment 1, point 2
    stored(env, STORED)
    unquoted = {**CANDIDATE, "evidence": [{**CANDIDATE["evidence"][0], "quote": "invoice_count 43"}]}
    result = answer(env, [unquoted], [{"candidate": 0, "decision": "repeat", "refs": [f"{v}#1"]}])
    assert "repeat 1," in result.stdout and "rejected 0, unverified 0, quote-failed 1;" in result.stdout, result.stdout
    [(entry_id, detail)] = events(env, "repeat")
    assert entry_id == 1 and detail["candidate"]["evidence"] == []


def test_a_repeat_verifies_an_unverified_entry_only_with_equal_text(env, embedder):
    """ADR 0012, amendment 1, point 5: same kind, title and statement (same hash) verifies; a repeat of other text,
    a narrower restatement say, leaves the entry unverified."""
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, {**STORED, "evidence": []})  # #1, unverified
    result = answer(env, [CANDIDATE, STORED], [{"candidate": n, "decision": "repeat", "refs": [f"{v}#1"]}
                                               for n in (0, 1)])
    assert "repeat 1, verified 1," in result.stdout, result.stdout
    [(entry_id, detail)] = events(env, "repeat")
    assert entry_id == 1 and detail["candidate"]["title"] == CANDIDATE["title"]
    assert env.sql(f"SELECT entry_id, count(*) FROM {v}.evidence GROUP BY 1") == [(1, 1)]  # STORED's one quote
    assert entries(env) == [1]


@pytest.mark.parametrize("change", ["withdraw", "supersede"])
def test_a_repeat_of_an_entry_withdrawn_during_the_second_call_is_admitted(env, embedder, change):
    v = env.vault
    embedding_vault(env, embedder.url)
    t = stored(env, STORED)
    if change == "withdraw":
        before_0012(env)  # a person's withdrawal without a terminal
        during_second_call(env, "withdraw", f"{v}#1", "--wrong", "--reason", "r")
    else:
        replacement = env.root / "replacement.json"
        replacement.write_text(json.dumps({**OTHER, "supersedes": [f"{v}#1"]}))
        during_second_call(env, "save", str(replacement), "--transcript", str(t), "--session", env.session)
    result = answer(env, [CANDIDATE], [{"candidate": 0, "decision": "repeat", "refs": [f"{v}#1"]}])
    assert "admitted 1" in result.stdout, result.stdout
    assert events(env, "repeat") == []
    gone = (env.sql(f"SELECT entry_id FROM {v}.withdrawal") if change == "withdraw" else
            env.sql(f"SELECT old_id FROM {v}.supersession"))
    assert gone == [(1,)]


def test_replaces_supersedes_every_named_neighbour(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED, NEAR)
    answer(env, [CANDIDATE], [{"candidate": 0, "decision": "replaces", "refs": [f"{v}#1", f"{v}#2"]}])
    assert supersessions(env) == [(1, 3), (2, 3)]
    by = f"extractor {env.session}"
    assert settlements(env) == [(3, 1, "replace", by), (3, 2, "replace", by)]
    assert f"supersedes: {v}#1, {v}#2" in env.run("show", f"{v}#3").stdout.splitlines()


def test_replaces_some_keeps_the_others(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED, NEAR)
    answer(env, [CANDIDATE], [{"candidate": 0, "decision": "replaces", "refs": [f"{v}#1"]}])
    assert supersessions(env) == [(1, 3)]
    by = f"extractor {env.session}"
    assert settlements(env) == [(3, 1, "replace", by), (3, 2, "keep", by)]


@pytest.mark.parametrize("first", ["no", "named"])
def test_an_old_slice_cannot_replace_a_newer_entry(env, embedder, first):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    if first == "no":
        answer(env, [CANDIDATE], [{"candidate": 0, "decision": "replaces", "refs": [f"{v}#1"]}],
               at="2000-01-01T00:00:00Z")
    else:
        answer(env, [{**CANDIDATE, "supersedes": [f"{v}#1"]}], [{"candidate": 0, "decision": "undecided"}],
               at="2000-01-01T00:00:00Z")
    assert entries(env) == [1, 2]
    assert supersessions(env) == [] and settlements(env) == []
    assert "TO SETTLE" in env.run("show", f"{v}#2").stdout


@pytest.mark.parametrize("confirmed", [True, False])
def test_a_neighbour_confirmed_during_the_second_call_is_not_replaced(env, embedder, confirmed):
    v = env.vault
    embedding_vault(env, embedder.url)
    t = stored(env, STORED)
    time.sleep(1)  # the slice ends after #1 was admitted
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if confirmed:
        evidence = env.root / "evidence.json"
        evidence.write_text(json.dumps([{"kind": "query", "reproduction": "SELECT 42", "quote": QUOTE}]))
        during_second_call(env, "confirm", f"{v}#1", str(evidence), "--transcript", str(t), "--session", env.session)
    answer(env, [CANDIDATE], [{"candidate": 0, "decision": "replaces", "refs": [f"{v}#1"]}], at=at)
    assert env.sql(f"SELECT count(*) FROM {v}.evidence WHERE entry_id = 1") == [(2 if confirmed else 1,)]
    assert supersessions(env) == ([] if confirmed else [(1, 2)])


def test_without_timestamps_nothing_is_replaced(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    result = answer(env, [CANDIDATE, THIRD], [{"candidate": 0, "decision": "replaces", "refs": [f"{v}#1"]},
                                              {"candidate": 1, "decision": "repeat", "refs": [f"{v}#1"]}], at=None)
    assert "admitted 1, duplicate 0, repeat 1," in result.stdout, result.stdout
    assert entries(env) == [1, 2]
    assert supersessions(env) == [] and settlements(env) == []
    assert "TO SETTLE" in env.run("show", f"{v}#2").stdout


MALFORMED = {
    "a ref not among the neighbours": [{"candidate": 0, "decision": "replaces", "refs": ["{v}#9"]}],
    "repeat with two refs": [{"candidate": 0, "decision": "repeat", "refs": ["{v}#1", "{v}#1"]}],
    "a negative index": [{"candidate": -2, "decision": "repeat", "refs": ["{v}#1"]}],
    "an index not listed": [{"candidate": 5, "decision": "repeat", "refs": ["{v}#1"]}],
    "an index given twice": [{"candidate": 0, "decision": "repeat", "refs": ["{v}#1"]},
                             {"candidate": 0, "decision": "new"}],
    "a decision that is not an object": ["repeat"],
    "refs not a list": [{"candidate": 0, "decision": "repeat", "refs": "{v}#1"}],
}


@pytest.mark.parametrize("case", MALFORMED)
def test_malformed_decisions_leave_their_candidate_undecided(env, embedder, case):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    faulty = json.loads(json.dumps(MALFORMED[case]).replace("{v}", v))
    answer(env, [CANDIDATE, THIRD], [*faulty, {"candidate": 1, "decision": "new"}])
    assert entries(env) == [1, 2, 3]
    assert settlements(env) == [(3, 1, "keep", f"extractor {env.session}")]
    assert "TO SETTLE" in env.run("show", f"{v}#2").stdout


def test_a_failed_second_call_admits_with_the_vector_and_leaves_pairs_open(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    t = env.transcript(user_messages=["count the April invoices"], tool_outputs=[OUTPUT], at=LATER)
    env.respond([{"note": None, "candidate": CANDIDATE}])
    (env.fake / "response-1.json").write_text("not JSON")
    result = env.run("extract", str(t))
    assert result.returncode == 0, result.stderr
    assert "admitted 1" in result.stdout and "2 extractor calls" in result.stdout, result.stdout
    assert env.sql(f"SELECT count(*) FROM {v}.embedding WHERE entry_id = 2") == [(1,)]
    assert env.sql(f"SELECT entry_id, other_id FROM {v}.neighbour") == [(2, 1)]
    assert settlements(env) == []


def test_a_neighbour_replaced_by_an_earlier_candidate_is_dropped(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    directory = memdir(env)
    write_note(directory, env.session)
    replaces = {"decision": "replaces", "refs": [f"{v}#1"]}
    answer(env, [CANDIDATE, THIRD, FOURTH], [{"candidate": 0, "decision": "repeat", "refs": [f"{v}#1"]},
                                             {"candidate": 1, **replaces}, {"candidate": 2, **replaces}], note=0)
    assert entries(env) == [1, 2, 3]  # THIRD is #2, FOURTH #3
    assert supersessions(env) == [(1, 2)]
    assert [row[:3] for row in settlements(env)] == [(2, 1, "replace")]
    assert f"Recorded in VAL3 as {v}#2 (verified)" in (directory / "april.md").read_text()


def test_a_neighbour_withdrawn_during_the_second_call_is_dropped(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    before_0012(env)  # a person's withdrawal without a terminal
    during_second_call(env, "withdraw", f"{v}#1", "--wrong", "--reason", "r")
    result = answer(env, [CANDIDATE], [{"candidate": 0, "decision": "replaces", "refs": [f"{v}#1"]}])
    assert "admitted 1" in result.stdout, result.stdout
    assert env.sql(f"SELECT entry_id FROM {v}.withdrawal") == [(1,)]
    assert supersessions(env) == [] and settlements(env) == []


def test_a_named_neighbour_below_the_threshold_is_replaced_without_a_settlement(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED, OTHER)
    answer(env, [{**CANDIDATE, "supersedes": [f"{v}#2"]}],
           [{"candidate": 0, "decision": "replaces", "refs": [f"{v}#2"]}])
    assert supersessions(env) == [(2, 3)]
    assert env.sql(f"SELECT entry_id, other_id FROM {v}.neighbour WHERE entry_id = 3") == [(3, 1)]
    assert settlements(env) == [(3, 1, "keep", f"extractor {env.session}")]


def test_a_vector_that_cannot_be_stored_keeps_the_admission(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    stored(env, STORED)
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    assert env.run("vault", "grant", v, env.writer, "--write", "--service", "val3test").returncode == 0
    env.sql(f"REVOKE INSERT ON {v}.embedding FROM {env.writer}")
    env.config(service="val3writer", embedding={"url": embedder.url})
    result = answer(env, [CANDIDATE], [{"candidate": 0, "decision": "new"}])
    assert "admitted 1" in result.stdout, result.stdout
    assert entries(env) == [1, 2]
    assert env.sql(f"SELECT count(*) FROM {v}.embedding WHERE entry_id = 2") == [(0,)]
    assert settlements(env) == []


def test_neighbour_false_makes_no_second_call(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url, neighbour=False)
    stored(env, STORED)
    result = extract(env, CANDIDATE)
    assert len(env.calls()) == 1 and "admitted 1" in result.stdout, result.stdout
    assert env.sql(f"SELECT count(*) FROM {v}.neighbour") == [(0,)]
    assert env.sql(f"SELECT count(*) FROM {v}.embedding WHERE entry_id = 2") == [(1,)]
