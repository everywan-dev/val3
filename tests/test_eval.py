import json

from helpers import OUTPUT, before_0012, embedding_vault, fact, make_vault, save

A = {"title": "Sample mailbox address", "statement": "Samples arrive at samples@example.org."}
B = {"title": "Duplicate invoices rule", "statement": "Repeated supplier numbers are refused."}


def labelled(env, cases):
    path = env.root / "set.jsonl"
    path.write_text("".join(json.dumps(case) + "\n" for case in cases))
    return path


def two_entries(env, embedder):
    embedding_vault(env, embedder.url)
    t = env.transcript(tool_outputs=[OUTPUT])
    for entry in (A, B):
        assert save(env, fact(keywords=[], **entry), t).returncode == 0


def test_eval_prints_one_row_per_floor(env, embedder):
    two_entries(env, embedder)
    path = labelled(env, [
        {"query": f"{A['title']}\n{A['statement']}", "relevant": [f"{env.vault}#1"]},
        {"query": f"{B['title']}\n{B['statement']}", "relevant": [f"{env.vault}#1"]},
        {"query": "penguin colony weather report today please", "relevant": []},
    ])
    result = env.run("eval", str(path))
    assert result.returncode == 0, result.stderr
    rows = {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines() if line.strip()}
    assert rows["floor"] == ["injected", "needed", "served", "silent"]
    assert "full-text" in rows
    for floor in ("0.40", "0.45", "0.50", "0.55", "0.60*", "0.65", "0.70", "0.75", "0.80"):
        assert rows[floor] == ["2", "50%", "1/2", "1/1"], floor
    assert rows["latency:"][0] == "mean"


MANY = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
        "SELECT 'fact', 'Invoice note ' || n, 'invoices ledger', 'h' || n, 'p', 'm' FROM generate_series(1, 4) n")


def test_eval_selects_what_the_prompt_hook_delivers(env, embedder):
    embedding_vault(env, embedder.url)
    before_0012(env)  # entries inserted without evidence are delivered as before ADR 0012
    env.sql(MANY.format(v=env.vault))
    assert env.run("tool", "add", "ledger-tool", "--type", "cli",
                   "--description", "invoices ledger note").returncode == 0  # scores below the four notes
    prompt = "invoice note invoices ledger"
    payload = {"session_id": env.session, "cwd": str(env.project), "hook_event_name": "UserPromptSubmit",
               "prompt": prompt}
    assert env.run("hook", "prompt", input=json.dumps(payload)).returncode == 0
    log = env.state / "prompts" / "project.jsonl"
    delivered = [item["ref"] for item in json.loads(log.read_text().splitlines()[-1])["injected"]]
    assert len(delivered) == 3 and f"{env.vault}#5" in delivered  # the tool comes first
    result = env.run("eval", str(labelled(env, [{"query": prompt, "relevant": delivered}])))
    rows = {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines() if line.strip()}
    assert rows["0.60*"] == ["3", "100%", "1/1", "0/0"]


def test_eval_refuses_cases_not_labelled_yet(env, embedder):
    two_entries(env, embedder)
    result = env.run("eval", str(labelled(env, [{"query": "x", "relevant": []}, {"query": "y", "relevant": None}])))
    assert result.returncode == 1
    assert "set.jsonl:2: not labelled yet" in result.stderr


def test_eval_reads_the_vault_as_it_was_at_the_prompt(env, embedder):
    two_entries(env, embedder)
    at = env.sql("SELECT now()")[0][0].isoformat()
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(keywords=[], title="Sample mailbox address moved",
                          statement="Samples now arrive at the sample mailbox.", supersedes=f"{env.vault}#1"),
                t).returncode == 0
    query = f"{A['title']}\n{A['statement']}"
    then = labelled(env, [{"query": query, "relevant": [f"{env.vault}#1"], "at": at}])
    rows = {line.split()[0]: line.split()[1:] for line in env.run("eval", str(then)).stdout.splitlines() if line}
    assert rows["0.60*"][2] == "1/1"
    now = labelled(env, [{"query": query, "relevant": [f"{env.vault}#1"]}])
    rows = {line.split()[0]: line.split()[1:] for line in env.run("eval", str(now)).stdout.splitlines() if line}
    assert rows["0.60*"][2] == "0/1"


def test_eval_reads_backing_as_it_was_at_the_prompt(env, embedder):
    """ADR 0012, amendment 1, point 7: an entry verified after the prompt was unverified then, and not served."""
    embedding_vault(env, embedder.url)
    v = env.vault
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(keywords=[], evidence=[], **A), t).stdout == f"admitted unverified {v}#1\n"
    at = env.sql("SELECT now()")[0][0].isoformat()
    assert save(env, fact(keywords=[], **A), t).stdout == f"verified {v}#1\n"
    query = f"{A['title']}\n{A['statement']}"
    for case, served in (({"at": at}, "0/1"), ({}, "1/1")):
        path = labelled(env, [{"query": query, "relevant": [f"{v}#1"], **case}])
        rows = {line.split()[0]: line.split()[1:] for line in env.run("eval", str(path)).stdout.splitlines() if line}
        assert rows["full-text"][2] == served and rows["0.60*"][2] == served, (case, rows)


def test_eval_counts_the_upgrade_s_attestation_at_any_time(env, embedder):
    """The upgrade attests what a person had admitted before it: such an entry had backing at a prompt older than
    the upgrade."""
    embedding_vault(env, embedder.url)
    v = env.vault
    assert save(env, fact(keywords=[], evidence=[], **A), env.transcript()).stdout == f"admitted unverified {v}#1\n"
    at = env.sql("SELECT now()")[0][0].isoformat()
    path = labelled(env, [{"query": f"{A['title']}\n{A['statement']}", "relevant": [f"{v}#1"], "at": at}])
    served = lambda: [line.split()[3] for line in env.run("eval", str(path)).stdout.splitlines()  # noqa: E731
                      if line.startswith("full-text")]
    assert served() == ["0/1"]
    env.sql(f"INSERT INTO {v}.attestation (entry_id, attested_by, how) VALUES (1, 'val3 upgrade', 'before ADR 0012')")
    assert served() == ["1/1"]


def test_eval_rejects_a_malformed_set(env, embedder):
    two_entries(env, embedder)
    path = env.root / "bad.jsonl"
    path.write_text('{"query": "x", "relevant": []}\nnot json\n')
    result = env.run("eval", str(path))
    assert result.returncode == 1
    assert "bad.jsonl:2: not valid JSON" in result.stderr
    path.write_text('{"query": "x"}\n')
    assert "expected query" in env.run("eval", str(path)).stderr


def test_eval_needs_the_embedding_table(env):
    make_vault(env)
    result = env.run("eval", str(labelled(env, [{"query": "x", "relevant": []}])))
    assert result.returncode == 1
    assert "no [embedding] table" in result.stderr


def test_draft_turns_the_prompt_log_into_an_unlabelled_set(env):
    log = env.root / "prompts.jsonl"
    log.write_text("".join(json.dumps(record) + "\n" for record in (
        {"at": "2026-10-10T08:00:00+00:00", "prompt": "yes", "previous": "Shall I?",
         "injected": [{"ref": "v#1", "score": 0.7}]},
        {"at": "2026-10-10T08:01:00+00:00", "prompt": "yes", "previous": "Shall I?", "injected": []},
        {"at": "2026-10-10T08:02:00+00:00", "prompt": "Who grants access?", "previous": "", "injected": []},
    )) + "garbage\n")
    result = env.run("eval", "--draft", str(log))
    assert result.returncode == 0, result.stderr
    assert [json.loads(line) for line in result.stdout.splitlines()] == [
        {"at": "2026-10-10T08:00:00+00:00", "session": None, "query": "yes", "previous": "Shall I?",
         "relevant": None, "injected": ["v#1"]},
        {"at": "2026-10-10T08:02:00+00:00", "session": None, "query": "Who grants access?", "previous": "",
         "relevant": None, "injected": []},
    ]


def test_draft_survives_bytes_that_are_not_utf8(env):
    log = env.root / "prompts.jsonl"
    record = {"at": "2026-10-10T08:00:00+00:00", "prompt": "Who grants access?", "previous": "", "injected": []}
    log.write_bytes(json.dumps(record).encode() + b"\n\xff\n")
    result = env.run("eval", "--draft", str(log))
    assert result.returncode == 0, result.stderr
    assert [json.loads(line)["query"] for line in result.stdout.splitlines()] == ["Who grants access?"]


def test_eval_ignores_spaces_around_a_relevant_ref(env, embedder):
    two_entries(env, embedder)
    query = f"{A['title']}\n{A['statement']}"
    rows = []
    for ref in (f"{env.vault}#1", f" {env.vault}#1 "):
        result = env.run("eval", str(labelled(env, [{"query": query, "relevant": [ref]}])))
        assert result.returncode == 0, result.stderr
        rows.append([line for line in result.stdout.splitlines() if line.startswith("0.60*")])
    assert rows[0] == rows[1] and rows[0][0].split()[1:] == ["1", "100%", "1/1", "0/0"]


def test_eval_lists_a_configured_floor_off_the_grid(env, embedder):
    embedding_vault(env, embedder.url, floor=0.63)
    assert save(env, fact(keywords=[], **A), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    result = env.run("eval", str(labelled(env, [{"query": A["title"], "relevant": [f"{env.vault}#1"]}])))
    assert result.returncode == 0, result.stderr
    labels = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    assert "0.63*" in labels and "0.60" in labels and "0.65" in labels
    assert labels.index("0.60") < labels.index("0.63*") < labels.index("0.65")


def test_a_deeply_nested_line_is_reported_with_its_place(env, embedder):
    embedding_vault(env, embedder.url)
    path = env.root / "set.jsonl"
    path.write_text("[" * 100000 + "\n")
    result = env.run("eval", str(path))
    assert result.returncode == 1
    assert f"{path}:1: not valid JSON" in result.stderr


def test_refs_to_undeclared_vaults_are_refused_before_embedding(env, embedder):
    embedding_vault(env, embedder.url)
    path = labelled(env, [{"query": "anything", "relevant": ["other_vault#1"]}])
    result = env.run("eval", str(path))
    assert result.returncode == 1
    assert f"{path}:1: other_vault#1 is in vault other_vault" in result.stderr
    assert embedder.inputs == []


def test_a_malformed_ref_is_reported_with_its_place(env, embedder):
    embedding_vault(env, embedder.url)
    path = labelled(env, [{"query": "anything", "relevant": []}, {"query": "more", "relevant": ["not a ref"]}])
    result = env.run("eval", str(path))
    assert result.returncode == 1
    assert f"{path}:2: invalid entry reference" in result.stderr
