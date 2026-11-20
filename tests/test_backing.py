"""ADR 0012 §1 and §3 on the read side: explicit lookups mark unverified entries, automatic delivery leaves them out."""
import getpass
import json

from helpers import OUTPUT, QUOTE, TOOL, fact, make_vault, old_vault, recipe, save
from test_hooks import ask, context, start
from test_mcp import header, read, search
from test_unverified import tool, tool_add, unverified_decision

UNVERIFIED = "UNVERIFIED: no evidence and no attestation"
QUERY = [{"kind": "query", "reproduction": "SELECT count(*) AS invoice_count FROM invoice", "quote": QUOTE}]


def unverified_tool(env, **changes):
    """An upgraded vault holding an unverified tool #1 in index `graphs`."""
    make_vault(env)
    result = save(env, tool(indexes=["graphs"], **changes), env.transcript())
    assert result.stdout == f"admitted unverified {env.vault}#1\n", result.stderr


def test_show_marks_an_unverified_entry(env):
    unverified_tool(env)
    assert env.run("show", f"{env.vault}#1").stdout.splitlines()[1] == UNVERIFIED


def test_show_names_the_person_who_attested(env):
    make_vault(env)
    tool_add(env)
    shown = env.run("show", f"{env.vault}#1").stdout
    assert f"evidence: none — attested by a person: {getpass.getuser()}, tool add, on " in shown
    assert "UNVERIFIED" not in shown


def test_recall_and_index_mark_unverified_entries(env):
    unverified_tool(env)
    recalled = env.run("recall", "graph-cli")
    assert recalled.stdout.splitlines()[0] == f"{env.vault}#1  [tool]  graph-cli  · unverified", recalled.stderr
    assert env.run("index", "graphs").stdout == f"- {env.vault}#1 [tool] graph-cli · unverified\n"


def test_the_session_index_leaves_unverified_entries_out(env):
    make_vault(env)
    v = env.vault
    t = env.transcript(tool_outputs=[OUTPUT])
    save(env, fact(), t)                                             # #1, evidence, index billing
    save(env, recipe(f"{v}#1", indexes=["reports"]), t)              # #2, unverified, only entry of reports
    tool_add(env)                                                    # #3, attested
    text = context(start(env), "SessionStart")
    assert f"- {v}#1 [fact]" in text and f"- {v}#3 [tool] code-graph" in text
    assert f"{v}#2" not in text and "## reports" not in text
    assert "Entries of this project's vaults with evidence or attested by a person" in text
    assert start(env).stdout and "index of 2 entries" in json.loads(start(env).stdout)["systemMessage"]


def test_the_prompt_hook_filters_before_the_cap(env):
    make_vault(env)
    v = env.vault
    for n in range(3):
        save(env, tool(f"invoice-tool-{n}", statement=f"Counts the April invoices, variant {n}."), env.transcript())
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))  # #4
    text = context(ask(env, "How many invoices are there for April?"), "UserPromptSubmit")
    assert f"- {v}#4 [fact] April has 42 invoices" in text
    assert "invoice-tool" not in text
    assert text.startswith("VAL3 entries matching this prompt (with evidence or attested by a person;")


def test_a_recipe_citing_an_unverified_fact_is_marked_and_not_delivered(env):
    make_vault(env)
    v = env.vault
    t = env.transcript(tool_outputs=[OUTPUT])
    save(env, fact(), t)  # #1
    env.sql(f"INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
            "VALUES ('fact', 'raw', 'a fact without evidence', 'h', 'p', 'm')")  # #2, unverified
    assert save(env, recipe(f"{v}#1", evidence=QUERY), t).stdout == f"admitted {v}#3\n"
    env.sql(f"INSERT INTO {v}.step_fact (recipe_id, position, fact_id) VALUES (3, 1, 2)")  # cited before ADR 0012
    assert f"facts: {v}#1, {v}#2 [unverified]" in env.run("show", f"{v}#3").stdout
    assert env.run("recall", "invoices", "month").stdout.splitlines()[0].endswith("· cites unverified")
    assert f"{v}#3" not in context(start(env), "SessionStart")


def test_memory_search_and_read_mark_the_backing(env):
    unverified_tool(env)
    tool_add(env)  # #2
    text, error = search(env, query="code graph overrides")
    assert not error, text
    assert header(text, f"{env.vault}#1").endswith(" · unverified")
    assert header(text, f"{env.vault}#2").endswith(" · attested by a person")
    shown, _ = read(env, f"{env.vault}#1")
    assert header(shown, f"{env.vault}#1").endswith(" · unverified") and UNVERIFIED in shown


def test_a_verified_entry_reads_as_confirmed(env):
    unverified_decision(env)
    from test_import import note_candidate
    quote = "route every message by its thread owner"
    candidate = {**{k: note_candidate()[k] for k in ("kind", "title", "statement", "keywords")},
                 "evidence": [{"kind": "user", "quote": quote}]}
    assert save(env, candidate, env.transcript(user_messages=[f"We {quote}."])).stdout == f"verified {env.vault}#2\n"
    shown, _ = read(env, f"{env.vault}#2")
    line = header(shown, f"{env.vault}#2")
    assert " · last confirmed " in line and "unverified" not in line


def test_status_counts_the_backing_of_an_upgraded_vault(env):
    unverified_tool(env)
    tool_add(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    lines = env.run("status").stdout.splitlines()
    assert lines[1].startswith(f"home vault {env.vault}: 3 entries: 1 with evidence, 1 attested, 1 unverified "
                               f"({env.vault}#1) · linked: none")


def test_the_extractor_sees_which_entries_are_unverified(env):
    unverified_tool(env, statement="Answers which module overrides a method in the invoice graph.")
    env.respond([])
    result = env.run("extract", str(env.transcript(user_messages=["Which module overrides the invoice graph?"],
                                                  tool_outputs=[OUTPUT])))
    assert result.returncode == 0, result.stderr
    call = env.calls()[0]
    assert f"- {env.vault}#1 [tool] graph-cli (unverified): " in call["stdin"]
    argv = call["argv"]
    prompt = argv[argv.index("--system-prompt") + 1]
    assert prompt.startswith("VAL3 extractor. Prompt version: 7.")
    assert "propose it again if this slice\n  holds evidence" in prompt


def test_the_extractor_gets_the_full_statement_of_an_unverified_fact(env):
    """ADR 0012, amendment 1, point 3: verification needs the identical text."""
    make_vault(env)
    v = env.vault
    long = "Invoices of April 2026 in the graph:\n" + "the invoice graph holds one node per invoice. " * 10
    t = env.transcript(user_messages=["Which module overrides the invoice graph?"], tool_outputs=[OUTPUT])
    assert save(env, fact(title="Invoice graph", statement=long, evidence=[]), t).stdout == f"admitted unverified {v}#1\n"
    assert save(env, fact(title="Invoice graph nodes", statement="Backed. " + long), t).stdout == f"admitted {v}#2\n"
    env.respond([])
    assert env.run("extract", str(t)).returncode == 0
    call = env.calls()[0]
    stdin = call["stdin"]
    assert f"- {v}#1 [fact] Invoice graph (unverified): {json.dumps(long.strip(), ensure_ascii=False)}\n" in stdin
    assert f"- {v}#2 [fact] Invoice graph nodes: Backed. Invoices of April 2026 in the graph: the invoice" in stdin
    assert "except a fact, preference or decision marked (unverified)" in stdin
    assert "A recipe cites no entry marked (unverified)." in stdin
    prompt = call["argv"][call["argv"].index("--system-prompt") + 1]
    assert "is stored unverified" in prompt and "(unverified)" in prompt.split("What not to keep")[0]


def test_a_vault_not_upgraded_reads_as_before(env):
    old_vault(env)
    save(env, TOOL, env.transcript())  # queued
    assert env.run("tool", "add", "code-graph-2", "--type", "cli", "--description", "d").returncode == 0
    shown = env.run("show", f"{env.vault}#1").stdout
    assert "attested" not in shown and "UNVERIFIED" not in shown
    text = context(start(env), "SessionStart")
    assert text.startswith("# VAL3 memory index\n\nVerified entries of this project's vaults")
    assert f"- {env.vault}#1 [tool] code-graph-2" in text
    assert env.run("recall", "code-graph-2").stdout.splitlines()[0] == f"{env.vault}#1  [tool]  code-graph-2"


def test_eval_serves_only_what_the_prompt_hook_may_deliver(env, embedder):
    from helpers import embedding_vault
    from test_eval import labelled
    embedding_vault(env, embedder.url)
    v = env.vault
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))  # #1
    save(env, tool("invoice-tool", statement="Counts the April invoices of the ERP."), env.transcript())  # #2
    query = "How many invoices are there for April?"
    result = env.run("eval", str(labelled(env, [{"query": query, "relevant": [f"{v}#2"]}])))
    assert result.returncode == 0, result.stderr
    rows = {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines() if line.strip()}
    assert rows["full-text"][2] == "0/1" and rows["0.60*"][2] == "0/1"  # the unverified tool is never served


def test_a_role_that_cannot_read_attestation_sees_backing_not_readable(env):
    unverified_tool(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    assert env.run("vault", "grant", env.vault, env.reader, "--read").returncode == 0
    env.sql(f"REVOKE SELECT ON {env.vault}.attestation FROM {env.reader}")
    env.config(service="val3reader")
    shown = env.run("show", f"{env.vault}#1")
    assert shown.stdout.splitlines()[1] == "BACKING NOT READABLE: this role cannot read evidence or attestation"
    assert env.run("recall", "graph-cli").stdout.splitlines()[0].endswith("  · backing not readable")
    started = start(env)  # delivery cannot tell what may be delivered: the hook fails open and says why
    assert "VAL3 is unavailable" in started.stdout and "attestation" in started.stdout
