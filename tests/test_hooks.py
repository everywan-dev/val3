import json
import subprocess
import time
from datetime import datetime, timedelta, timezone

import psycopg

from helpers import OUTPUT, before_0012, embedding_vault, fact, old_vault, recipe, save

PREFERENCE = {"kind": "preference", "title": "Reports in Spanish",
              "statement": "Reports to the owner are written in Spanish.", "indexes": ["reporting"],
              "evidence": [{"kind": "user", "quote": "write the reports in Spanish"}]}
PARTIAL = ("This memory is partial and may be out of date: it complements the project's documents, it does not "
           "replace them. Whatever it shows, also search the project's files before answering or concluding that "
           "something is not recorded.")
INSERT_MANY = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
               "SELECT %s, %s || g || ' ' || repeat('x', 150), %s, 'h' || g, 'p', 'm' "
               "FROM generate_series(1, %s) g")


def transcript_path(env):
    return str(env.home / ".claude" / "projects" / "-project" / f"{env.session}.jsonl")


def payload(env, event, cwd=None, **extra):
    return {"session_id": env.session, "transcript_path": transcript_path(env),
            "cwd": str(cwd or env.project), "hook_event_name": event, **extra}


def hook(env, event, data, **environ):
    return env.run("hook", event, input=json.dumps(data), environ=environ)


def context(result, event):
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return None
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == event
    return output["additionalContext"]


def start(env, **environ):
    return hook(env, "session-start", payload(env, "SessionStart", source="startup"), **environ)


def test_session_start_injects_the_index(env):
    old_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT], user_messages=["Please write the reports in Spanish, always."])
    save(env, fact(), t)
    save(env, PREFERENCE, t)
    assert env.run("tool", "add", "code-graph", "--type", "mcp", "--priority", "5",
                   "--description", "Answers which module overrides a method.").returncode == 0
    text = context(start(env), "SessionStart")
    assert "## Tools" in text and f"- {env.vault}#3 [tool] code-graph" in text
    assert "## billing" in text and f"- {env.vault}#1 [fact] April has 42 invoices" in text
    assert "## reporting" in text and f"- {env.vault}#2 [preference] Reports in Spanish" in text
    assert text.index("## Tools") < text.index("## billing") < text.index("## reporting")


def test_session_start_says_the_memory_is_partial(env):
    old_vault(env)
    assert context(start(env), "SessionStart").endswith("\n\n" + PARTIAL)


def test_session_start_exports_the_session_variables(env):
    old_vault(env)
    env_file = env.root / "claude.env"
    assert start(env, CLAUDE_ENV_FILE=str(env_file)).returncode == 0
    shown = subprocess.run(["bash", "-c", f'source "{env_file}"; echo "$VAL3_SESSION_ID|$VAL3_TRANSCRIPT"'],
                           capture_output=True, text=True).stdout.strip()
    assert shown == f"{env.session}|{transcript_path(env)}"


def test_hooks_are_silent_outside_adopted_projects(env):
    elsewhere = env.root / "elsewhere"
    elsewhere.mkdir()
    result = hook(env, "session-start", payload(env, "SessionStart", cwd=elsewhere))
    assert result.returncode == 0
    assert result.stdout == ""


def test_unreachable_server_warns_once_per_session(env):
    env.config(service="val3down")
    assert "VAL3 is unavailable" in context(start(env), "SessionStart")
    second = start(env)
    assert second.returncode == 0
    assert second.stdout == ""


def test_session_index_respects_the_context_limit(env):
    old_vault(env)
    env.sql(INSERT_MANY.format(v=env.vault), ("preference", "Preference number ", "s", 300))
    text = context(start(env), "SessionStart")
    assert len(text) <= 10_000
    assert "## General — 300 entries: val3 index --none" in text
    assert "more entries not shown" not in text


def indexed(env, vault, *index_lists, origin="p"):
    """One fact per list of index names, inserted by SQL; returns their refs."""
    refs = []
    for names in index_lists:
        [(entry_id,)] = env.sql(
            f"INSERT INTO {vault}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
            "VALUES ('fact', 'Fact', 's', gen_random_uuid()::text, %s, 'm') RETURNING id", (origin,))
        for name in names:
            env.sql(f"INSERT INTO {vault}.knowledge_index (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
            env.sql(f"INSERT INTO {vault}.entry_index SELECT %s, id FROM {vault}.knowledge_index WHERE name = %s",
                    (entry_id, name))
        refs.append(f"{vault}#{entry_id}")
    return refs


def section_of(text, ref):
    """The section heading each listing of `ref` falls under."""
    found, heading = [], None
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line[3:]
        elif line.startswith(f"- {ref} "):
            found.append(heading)
    assert len(found) == 1, (ref, found)
    return found[0]


def linked_vault(env):
    shared = env.vault + "_linked"
    assert env.run("vault", "create", shared).returncode == 0
    before_0012(env, shared)
    env.config(linked=[shared])
    return shared


def test_entry_is_listed_under_its_largest_index(env):
    old_vault(env)
    a, b, c = indexed(env, env.vault, ["billing", "zeta"], ["zeta"], ["zeta"])
    text = context(start(env), "SessionStart")
    assert [section_of(text, ref) for ref in (a, b, c)] == ["zeta"] * 3
    assert "## billing" not in text


def test_project_name_index_is_not_a_section(env):
    old_vault(env)
    own = env.vault.replace("_", "-")
    both, only = indexed(env, env.vault, [own, "billing"], [own])
    text = context(start(env), "SessionStart")
    assert section_of(text, both) == "billing"
    assert section_of(text, only) == "General"
    assert f"## {own}" not in text


def test_another_projects_name_is_not_a_section(env):
    old_vault(env)
    both, only = indexed(env, env.vault, ["other-proj", "billing"], ["other-proj"], origin="other_proj")
    text = context(start(env), "SessionStart")
    assert section_of(text, both) == "billing"
    assert section_of(text, only) == "General"


def test_own_project_name_is_not_a_section_before_it_writes(env):
    old_vault(env)
    config = env.project / "val3.toml"
    config.write_text('project = "New_Project"\n' + config.read_text())
    both, only = indexed(env, env.vault, ["new-project", "billing"], ["new-project"], origin="other")
    text = context(start(env), "SessionStart")
    assert section_of(text, both) == "billing"
    assert section_of(text, only) == "General"
    assert "## new-project" not in text


def test_large_index_collapses_largest_section_first(env):
    old_vault(env)
    env.sql(INSERT_MANY.format(v=env.vault), ("fact", "Big fact ", "s", 120))
    env.sql(f"INSERT INTO {env.vault}.knowledge_index (name) VALUES ('big')")
    env.sql(f"INSERT INTO {env.vault}.entry_index SELECT e.id, k.id FROM {env.vault}.entry e, "
            f"{env.vault}.knowledge_index k WHERE k.name = 'big'")
    small = indexed(env, env.vault, ["small"], ["small"], ["small"])
    text = context(start(env), "SessionStart")
    assert len(text) <= 10_000
    assert "## big — 120 entries: val3 index big" in text
    assert [section_of(text, ref) for ref in small] == ["small"] * 3
    assert "more entries not shown" not in text


def test_collapsed_general_points_to_index_none(env):
    old_vault(env)
    env.sql(INSERT_MANY.format(v=env.vault), ("fact", "Loose fact ", "s", 120))
    [billing] = indexed(env, env.vault, ["billing"])
    text = context(start(env), "SessionStart")
    assert "## General — 120 entries: val3 index --none" in text
    assert section_of(text, billing) == "billing"
    assert text.index("## billing") < text.index("## General")


def test_linked_vault_index_merges_with_home_section(env):
    old_vault(env)
    shared = linked_vault(env)
    [home] = indexed(env, env.vault, ["billing"])
    [other] = indexed(env, shared, ["billing"])
    text = context(start(env), "SessionStart")
    assert text.count("## billing") == 1
    assert section_of(text, home) == section_of(text, other) == "billing"


def test_section_ranking_is_per_vault(env):
    old_vault(env)
    shared = linked_vault(env)
    [home] = indexed(env, env.vault, ["a", "b"], ["a"], ["a"])[:1]
    [other] = indexed(env, shared, ["a", "b"], ["b"], ["b"])[:1]
    text = context(start(env), "SessionStart")
    assert section_of(text, home) == "a"
    assert section_of(text, other) == "b"


def test_aliases_apply_to_their_own_vault(env):
    old_vault(env)
    shared = linked_vault(env)
    [home] = indexed(env, env.vault, ["x"])
    [target] = env.sql(f"INSERT INTO {env.vault}.knowledge_index (name) VALUES ('a') RETURNING id")[0]
    env.sql(f"INSERT INTO {env.vault}.index_alias (name, target_id) VALUES ('x', %s)", (target,))
    [other] = indexed(env, shared, ["x"])
    text = context(start(env), "SessionStart")
    assert section_of(text, home) == "a"
    assert section_of(text, other) == "x"


def test_index_overflow_after_collapsing_still_fits(env):
    old_vault(env)
    v = env.vault
    env.sql(f"INSERT INTO {v}.knowledge_index (name) "
            "SELECT 's' || lpad(g::text, 3, '0') || '-' || repeat('x', 55) FROM generate_series(1, 400) g")
    env.sql(INSERT_MANY.format(v=v), ("fact", "Fact ", "s", 1200))
    env.sql(f"INSERT INTO {v}.entry_index SELECT e.id, k.id FROM {v}.entry e "
            f"JOIN {v}.knowledge_index k ON k.id = (e.id - 1) / 3 + 1")
    for name in ("code-graph", "erp-query"):
        assert env.run("tool", "add", name, "--type", "mcp", "--priority", "5",
                       "--description", "Answers questions.").returncode == 0
    text = context(start(env), "SessionStart")
    assert len(text) <= 10_000
    assert "] code-graph" in text and "] erp-query" in text
    shown = sum(1 for line in text.splitlines() if line.startswith("## s") and " — 3 entries: " in line)
    assert 0 < shown < 400
    assert text.endswith(f"\n{3 * (400 - shown)} more entries not shown: use `val3 recall <terms>`.\n\n{PARTIAL}")


LONG = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
        "SELECT 'fact', 'Invoice note ' || g, 'invoices ' || repeat('lorem ', 600), 'p' || g, 'p', 'm' "
        "FROM generate_series(1, 20) g")


def ask(env, text):
    return hook(env, "prompt", payload(env, "UserPromptSubmit", prompt=text))


def test_prompt_injects_matching_entries(env):
    old_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    text = context(ask(env, "How many invoices are there for April?"), "UserPromptSubmit")
    assert (f"- {env.vault}#1 [fact] April has 42 invoices: The ERP holds 42 invoices dated April 2026."
            in text)


def test_prompt_says_the_memory_is_partial_only_when_it_injects(env):
    embedding_vault(env, DOWN)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    warned = context(ask(env, "hello there"), "UserPromptSubmit")
    assert "semantic search is unavailable" in warned and PARTIAL not in warned
    assert context(ask(env, APRIL), "UserPromptSubmit").endswith("\n\n" + PARTIAL)


def test_prompt_injects_recipe_steps(env):
    old_vault(env)  # the recipe is approved on the review list
    t = env.transcript(tool_outputs=[OUTPUT])
    save(env, fact(), t)
    save(env, recipe(f"{env.vault}#1"), t)
    assert env.run("review", "approve", "1").returncode == 0
    text = context(ask(env, "count the invoices of a month"), "UserPromptSubmit")
    assert "1. Run the invoice count query for the month" in text


def test_one_shared_word_in_a_longer_prompt_is_not_a_match(env):
    old_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert context(ask(env, "please fix the error in these invoices"), "UserPromptSubmit") is None


def test_prompt_without_matches_adds_nothing(env):
    old_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert context(ask(env, "hello there"), "UserPromptSubmit") is None
    assert context(ask(env, ""), "UserPromptSubmit") is None


def test_prompt_with_quotes_and_backslashes_is_safe(env):
    old_vault(env)
    result = ask(env, "what's in C:\\temp\\'x' -- ; DROP TABLE entry; '''")
    assert result.returncode == 0
    assert result.stderr == ""


def test_prompt_context_respects_the_limit(env):
    old_vault(env)
    env.sql(LONG.format(v=env.vault))
    text = context(ask(env, "invoices"), "UserPromptSubmit")
    assert len(text) <= 10_000
    assert text.count(f"{env.vault}#") >= 2


DOWN = "http://127.0.0.1:1"
MAILBOX = fact(title="Sample mailbox address", statement="Samples arrive at the shared sample mailbox.", keywords=[])
ASKED = "Shall I read the sample mailbox address now?"
APRIL = "How many invoices are there for April?"


def test_session_start_on_an_empty_vault(env):
    old_vault(env)
    assert "No entries yet." in context(start(env), "SessionStart")


def test_session_start_warms_up_the_embedding_model(env, embedder):
    embedding_vault(env, embedder.url)
    assert context(start(env), "SessionStart").startswith("# VAL3 memory index")
    assert embedder.inputs == ["warm-up"]


def test_session_start_with_the_embedding_service_down_still_gives_the_index(env):
    embedding_vault(env, DOWN)
    assert context(start(env), "SessionStart").startswith("# VAL3 memory index")


def notes(env, embedder, **settings):
    embedding_vault(env, embedder.url, **settings)
    before_0012(env)  # entries inserted without evidence are delivered as before ADR 0012
    env.sql(INSERT_MANY.format(v=env.vault), ("fact", "Invoice note ", "invoices ledger", 6))
    assert env.run("embed").stdout.strip() == "embedded 6 entries"


def test_prompt_injects_at_most_three_entries_above_the_floor(env, embedder):
    notes(env, embedder)
    text = context(ask(env, "invoice note invoices ledger"), "UserPromptSubmit")
    assert text.count(f"\n- {env.vault}#") == 3


def test_prompt_below_the_floor_injects_nothing(env, embedder):
    notes(env, embedder, floor=0.9)
    assert context(ask(env, "invoice note invoices ledger"), "UserPromptSubmit") is None


def test_prompt_filters_entries_without_a_vector_before_the_cap(env, embedder):
    notes(env, embedder, floor=0.99)  # six entries with a vector and a better full-text rank than the one below
    env.sql(f"INSERT INTO {env.vault}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
            "VALUES ('fact', 'Invoice note', 'Kept for audits.', 'h-plain', 'p', 'm')")
    text = context(ask(env, "invoice note invoices ledger"), "UserPromptSubmit")
    assert text.count(f"{env.vault}#") == 1 and f"{env.vault}#7 [fact] Invoice note" in text


def test_short_prompt_is_read_with_the_previous_assistant_turn(env, embedder):
    embedding_vault(env, embedder.url, floor=0.4)
    save(env, MAILBOX, env.transcript(tool_outputs=[OUTPUT], assistant_texts=[ASKED]))
    text = context(ask(env, "yes"), "UserPromptSubmit")
    assert f"{env.vault}#1 [fact] Sample mailbox address" in text
    assert embedder.requests[-1]["input"] == ["yes", f"yes\n{ASKED}"]


def test_short_prompt_on_a_new_topic_is_also_read_alone(env, embedder):
    embedding_vault(env, embedder.url, floor=0.6)
    t = env.transcript(tool_outputs=[OUTPUT], assistant_texts=[ASKED])
    save(env, MAILBOX, t)
    save(env, fact(title="Supplier templates", statement="Supplier templates stay optional.", keywords=[]), t)
    text = context(ask(env, "supplier templates optional?"), "UserPromptSubmit")
    assert f"{env.vault}#2 [fact] Supplier templates" in text and f"{env.vault}#1 " not in text


def test_short_prompt_without_a_previous_turn_is_read_alone(env, embedder):
    embedding_vault(env, embedder.url, floor=0.4)
    save(env, MAILBOX, env.transcript(tool_outputs=[OUTPUT]))
    assert context(ask(env, "yes"), "UserPromptSubmit") is None
    assert embedder.requests[-1]["input"] == ["yes"]


def test_long_prompt_is_read_alone(env, embedder):
    embedding_vault(env, embedder.url, floor=0.4)
    save(env, MAILBOX, env.transcript(tool_outputs=[OUTPUT], assistant_texts=[ASKED]))
    prompt = "Which address do the samples arrive at in the shared mailbox"
    assert f"{env.vault}#1" in context(ask(env, prompt), "UserPromptSubmit")
    assert embedder.inputs[-1] == prompt


def test_unreadable_transcript_is_ignored(env, embedder):
    embedding_vault(env, embedder.url)
    result = hook(env, "prompt", payload(env, "UserPromptSubmit", prompt="yes", transcript_path=str(env.root)))
    assert result.returncode == 0
    assert result.stderr == ""
    assert embedder.inputs[-1] == "yes"


def test_harness_turns_get_nothing(env, embedder):
    notes(env, embedder)
    calls = len(embedder.inputs)
    result = hook(env, "prompt", payload(env, "UserPromptSubmit", prompt="invoice note invoices ledger",
                                         source="system"))
    assert context(result, "UserPromptSubmit") is None
    assert len(embedder.inputs) == calls
    assert not (env.state / "prompts").exists()


HARNESS_PROMPTS = (
    "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>",
    '<agent-message from="a1">\nreport\n</agent-message>',
    '<cross-session-message from="peer">\ninvoice note invoices ledger\n</cross-session-message>',
    '<teammate-message teammate_id="t1">\ninvoice note invoices ledger\n</teammate-message>',
    'Another Claude session sent a message:\n<cross-session-message from="peer">\ninvoice note invoices ledger\n'
    '</cross-session-message>',
)


def test_harness_turns_are_recognised_by_their_text(env, embedder):
    notes(env, embedder)
    calls = len(embedder.inputs)
    for text in HARNESS_PROMPTS:
        assert context(ask(env, text), "UserPromptSubmit") is None, text
    assert len(embedder.inputs) == calls
    assert not (env.state / "prompts").exists()


def test_prompt_falls_back_to_full_text_and_warns_once(env):
    embedding_vault(env, DOWN)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    first = context(ask(env, APRIL), "UserPromptSubmit")
    assert f"{env.vault}#1 [fact] April has 42 invoices" in first
    assert "semantic search is unavailable" in first
    second = context(ask(env, APRIL), "UserPromptSubmit")
    assert f"{env.vault}#1" in second
    assert "semantic search is unavailable" not in second


def test_slow_embedding_service_does_not_hold_the_prompt(env, embedder):
    embedding_vault(env, embedder.url)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    embedder.delay = 4
    started = time.monotonic()
    text = context(ask(env, APRIL), "UserPromptSubmit")
    assert time.monotonic() - started < 3.5
    assert "semantic search is unavailable" in text and f"{env.vault}#1" in text


def test_entries_without_a_vector_reach_the_prompt_by_full_text(env, embedder):
    embedding_vault(env, DOWN)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    env.config(embedding={"url": embedder.url})
    text = context(ask(env, APRIL), "UserPromptSubmit")
    assert f"{env.vault}#1 [fact] April has 42 invoices" in text
    assert "semantic search is unavailable" not in text


def test_a_database_error_in_semantic_search_falls_back(env, embedder):
    embedding_vault(env, embedder.url)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    assert env.run("vault", "grant", env.vault, env.reader, "--read", "--service", "val3test").returncode == 0
    env.sql(f"REVOKE SELECT ON {env.vault}.embedding FROM {env.reader}")
    env.config(service="val3reader", embedding={"url": embedder.url})
    text = context(ask(env, APRIL), "UserPromptSubmit")
    assert f"{env.vault}#1" in text and "semantic search is unavailable" in text


def test_prompt_on_a_vault_without_the_embedding_table(env, embedder):
    embedding_vault(env, embedder.url)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    env.sql(f"DROP TABLE {env.vault}.embedding")
    text = context(ask(env, APRIL), "UserPromptSubmit")
    assert f"{env.vault}#1" in text and f"val3 vault upgrade {env.vault}" in text


def prompt_log(env):
    path = env.state / "prompts" / "project.jsonl"
    return path, [json.loads(line) for line in path.read_text().splitlines()]


def test_prompt_log_records_typed_prompts(env, embedder):
    notes(env, embedder)
    ask(env, "invoice note invoices ledger")
    path, records = prompt_log(env)
    assert len(records) == 1
    record = records[0]
    assert record["session"] == env.session and record["prompt"] == "invoice note invoices ledger"
    assert datetime.fromisoformat(record["at"]).isoformat(timespec="microseconds") == record["at"]
    assert record["method"] == "semantic" and record["previous"] == ""
    assert len(record["injected"]) == 3
    assert all(0.6 <= item["score"] <= 1 for item in record["injected"])  # full-text hits log score null
    assert path.stat().st_mode & 0o777 == 0o600


def test_prompt_log_drops_lines_older_than_thirty_days(env, embedder):
    notes(env, embedder)
    path = env.state / "prompts" / "project.jsonl"
    path.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    path.write_text("".join(json.dumps({"at": (now - timedelta(days=days)).isoformat(timespec="seconds"),
                                        "prompt": f"{days} days"}) + "\n" for days in (40, 10)))
    ask(env, "invoice note invoices ledger")
    _, records = prompt_log(env)
    assert [record["prompt"] for record in records] == ["10 days", "invoice note invoices ledger"]


def test_a_corrupt_prompt_log_does_not_cost_the_injection(env, embedder):
    notes(env, embedder)
    path = env.state / "prompts" / "project.jsonl"
    path.parent.mkdir(parents=True)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
    path.write_bytes(json.dumps({"at": old, "prompt": "old"}).encode() + b"\n\xff\n")
    text = context(ask(env, "invoice note invoices ledger"), "UserPromptSubmit")
    assert text.count(f"\n- {env.vault}#") == 3


def test_prompt_warns_once_when_the_server_is_down(env):
    env.config(service="val3down")
    assert "VAL3 is unavailable" in context(ask(env, "invoices"), "UserPromptSubmit")
    second = ask(env, "invoices")
    assert second.returncode == 0
    assert second.stdout == ""


def test_a_deeply_nested_hook_event_is_ignored(env):
    old_vault(env)
    result = env.run("hook", "prompt", input="[" * 100000)
    assert result.returncode == 0 and result.stdout == ""


def test_a_deeply_nested_last_transcript_line_does_not_cost_the_injection(env, embedder):
    embedding_vault(env, embedder.url)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    with t.open("a") as handle:
        handle.write("[" * 100000 + "\n")
    text = context(ask(env, "invoices april?"), "UserPromptSubmit")
    assert f"{env.vault}#1" in text and "unavailable" not in text


def locked(env):
    conn = psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname=val3")
    conn.execute(f"LOCK TABLE {env.vault}.entry IN ACCESS EXCLUSIVE MODE")
    return conn


def test_a_locked_vault_does_not_hold_the_session_start(env):
    old_vault(env)
    conn = locked(env)
    try:
        started = time.monotonic()
        text = context(start(env), "SessionStart")
        elapsed = time.monotonic() - started
    finally:
        conn.close()
    assert "VAL3 is unavailable" in text and "statement timeout" in text
    assert elapsed < 8


def test_a_locked_vault_does_not_hold_the_prompt(env):
    old_vault(env)
    conn = locked(env)
    try:
        text = context(ask(env, "invoices april"), "UserPromptSubmit")
    finally:
        conn.close()
    assert "VAL3 is unavailable" in text


def test_a_drainer_that_cannot_start_still_gives_the_index(env):
    old_vault(env)
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    queue = env.state / "queue"
    queue.mkdir(parents=True)
    (queue / "1-1.json").write_text(json.dumps({"session_id": "s", "transcript_path": "/nonexistent",
                                                "cwd": str(env.project), "event": "SessionEnd",
                                                "queued_at": 0, "attempts": 0}))
    site = env.root / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        "import subprocess\n_Real = subprocess.Popen\n\n\n"
        "class _Failing(_Real):\n"
        "    def __init__(self, args, *rest, **options):\n"
        "        if 'drain' in list(args):\n"
        "            raise OSError(11, 'Resource temporarily unavailable')\n"
        "        super().__init__(args, *rest, **options)\n\n\n"
        "subprocess.Popen = _Failing\n")
    text = context(start(env, PYTHONPATH=str(site)), "SessionStart")
    assert f"{env.vault}#1" in text
    assert "cannot start the drainer" in text and "val3 drain" in text


def test_tools_alone_over_the_cap_drop_their_last_lines(env):
    old_vault(env)
    env.sql(INSERT_MANY.format(v=env.vault), ("tool", "Tool number ", "s", 120))
    text = context(start(env), "SessionStart")
    assert len(text) <= 10_000
    shown = [int(line.split("#")[1].split(" ")[0]) for line in text.splitlines() if line.startswith(f"- {env.vault}#")]
    assert 0 < len(shown) < 120 and shown == list(range(1, len(shown) + 1))
    assert text.endswith(f"\n{120 - len(shown)} more entries not shown: use `val3 recall <terms>`.\n\n{PARTIAL}")


def notice(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout).get("systemMessage")


def test_session_start_tells_the_person_the_index_size(env):
    old_vault(env)
    indexed(env, env.vault, ["billing"], ["billing"])
    assert notice(start(env)) == "VAL3: index of 2 entries"


def test_session_start_message_counts_collapsed_sections(env):
    old_vault(env)
    env.sql(INSERT_MANY.format(v=env.vault), ("fact", "Big fact ", "s", 120))
    env.sql(f"INSERT INTO {env.vault}.knowledge_index (name) VALUES ('big')")
    env.sql(f"INSERT INTO {env.vault}.entry_index SELECT e.id, k.id FROM {env.vault}.entry e, "
            f"{env.vault}.knowledge_index k WHERE k.name = 'big'")
    indexed(env, env.vault, ["small"], ["small"], ["small"])
    assert notice(start(env)) == "VAL3: index of 123 entries · 1 sections collapsed"


def test_prompt_tells_the_person_what_it_delivered(env):
    old_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert notice(ask(env, APRIL)) == f"VAL3: {env.vault}#1"


def test_prompt_without_deliveries_has_no_message(env):
    old_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert ask(env, "hello there").stdout == ""


def test_unreachable_server_warning_reaches_the_person(env):
    env.config(service="val3down")
    assert "VAL3 is unavailable" in notice(start(env))


def test_embedding_warning_reaches_the_person(env):
    embedding_vault(env, DOWN)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    lines = notice(ask(env, APRIL)).splitlines()
    assert lines[0] == f"VAL3: {env.vault}#1"
    assert "semantic search is unavailable" in lines[1]


def test_prompt_message_names_only_rendered_entries(env):
    old_vault(env)
    env.sql(LONG.format(v=env.vault))
    result = ask(env, "invoices")
    text = context(result, "UserPromptSubmit")
    refs = notice(result).removeprefix("VAL3: ").split(", ")
    rendered = [line[2:].split(" ")[0] for line in text.splitlines() if line.startswith(f"- {env.vault}#")]
    assert refs == rendered
    assert len(rendered) < 20


def test_warning_with_terminal_sequences_is_escaped(env):
    env.config(service="val\\u001b3")
    message = notice(start(env))
    assert "\x1b" not in message
    assert "\\x1b" in message


def test_warning_with_a_line_break_stays_on_one_notice_line(env):
    old_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    env.config(embedding={"url": DOWN + "/a\nb"})
    lines = notice(ask(env, APRIL)).splitlines()
    assert len(lines) == 2
    assert lines[0] == f"VAL3: {env.vault}#1"
    assert "semantic search is unavailable" in lines[1] and "\\n" in lines[1]


TWIN = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.")
SETTLE_HEADER = ('Entries marked "to settle" are close to another entry of the same kind. Settle each pair: '
                 '`val3 settle <ref> <near> --replace` if the new one replaces it, `--drop` if it repeats it, '
                 '`--keep` if both hold (`val3 settle --help`).')


def twins(env, embedder):
    embedding_vault(env, embedder.url)
    before_0012(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    for candidate in (fact(), TWIN):
        assert save(env, candidate, t).returncode == 0


def test_session_index_shows_entries_to_settle(env, embedder):
    twins(env, embedder)
    result = start(env)
    text = context(result, "SessionStart")
    assert f"\n\n{SETTLE_HEADER}\n\n## billing" in text
    assert f"\n- {env.vault}#2 [fact] April has 43 invoices · to settle (near {env.vault}#1)\n" in text + "\n"
    assert f"\n- {env.vault}#1 [fact] April has 42 invoices\n" in text
    assert notice(result) == "VAL3: index of 2 entries · 1 to settle"
    assert env.run("settle", f"{env.vault}#2", f"{env.vault}#1", "--keep").returncode == 0
    result = start(env)
    assert "to settle" not in context(result, "SessionStart") and notice(result) == "VAL3: index of 2 entries"


def test_prompt_marks_an_entry_to_settle(env, embedder):
    twins(env, embedder)
    text = context(ask(env, "The ERP holds 43 invoices dated April 2026."), "UserPromptSubmit")
    assert f"\n- {env.vault}#2 [fact] April has 43 invoices · to settle (near {env.vault}#1): The ERP" in text
    assert f"\n- {env.vault}#1 [fact] April has 42 invoices: The ERP" in text


def test_delivery_leaves_out_a_pair_with_an_unverified_entry(env, embedder):
    """ADR 0012, amendment 1, point 5: automatic delivery shows and counts a pair only when both entries may be
    delivered; explicit inspection still names it."""
    embedding_vault(env, embedder.url)  # upgraded
    v = env.vault
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, {**TWIN, "evidence": []}, t).stdout == f"admitted unverified {v}#1\n"
    assert save(env, fact(), t).stdout == f"admitted {v}#2\n"  # backed, newer: the pair is on it
    result = start(env)
    text = context(result, "SessionStart")
    assert f"\n- {v}#2 [fact] April has 42 invoices\n" in text + "\n" and "to settle" not in text
    assert SETTLE_HEADER not in text and notice(result) == "VAL3: index of 1 entries"
    asked = context(ask(env, "The ERP holds 42 invoices dated April 2026."), "UserPromptSubmit")
    assert f"\n- {v}#2 [fact] April has 42 invoices: The ERP" in asked and "to settle" not in asked
    assert f"TO SETTLE: close to {v}#1" in env.run("show", f"{v}#2").stdout
    assert f"{v}#2" in env.run("status").stdout.split("to settle: 1 (")[1]


def test_a_collapsed_section_counts_its_entries_to_settle(env, embedder):
    twins(env, embedder)
    env.sql(INSERT_MANY.format(v=env.vault), ("fact", "Big fact ", "s", 120))
    env.sql(f"INSERT INTO {env.vault}.entry_index SELECT e.id, k.id FROM {env.vault}.entry e, "
            f"{env.vault}.knowledge_index k WHERE k.name = 'billing' AND e.id > 2")
    text = context(start(env), "SessionStart")
    assert "## billing — 122 entries (1 to settle): val3 index billing" in text
    assert SETTLE_HEADER in text


TOOLS_LINE = ("VAL3 memory is reached with the tools memory_search, memory_read and memory_save. Save durable project "
              "knowledge once a result or a decision is settled, when a later session would need it. Search first and "
              "name in `supersedes` an entry it replaces. Quote the evidence the session shows; without evidence the "
              "entry is stored unverified.")


def deliver(env, **switches):
    with (env.project / "val3.toml").open("a") as handle:
        handle.write("[delivery]\n" + "".join(f"{key} = {json.dumps(value)}\n" for key, value in switches.items()))


def test_index_off_keeps_session_upkeep(env, embedder):
    embedding_vault(env, embedder.url)
    deliver(env, index=False)
    t = env.transcript(tool_outputs=[OUTPUT])
    queue = env.state / "queue"
    queue.mkdir(parents=True)
    (queue / "1-1.json").write_text(json.dumps(
        {"session_id": "earlier", "transcript_path": str(t), "cwd": str(env.project),
         "event": "SessionEnd", "queued_at": 0, "attempts": 0}))
    env.respond([{"note": None, "candidate": fact()}])
    env_file = env.root / "claude.env"
    result = start(env, CLAUDE_ENV_FILE=str(env_file))
    assert context(result, "SessionStart") == f"{TOOLS_LINE}\n\n{PARTIAL}"
    assert notice(result) == "VAL3: index off"
    shown = subprocess.run(["bash", "-c", f'source "{env_file}"; echo "$VAL3_SESSION_ID|$VAL3_TRANSCRIPT"'],
                           capture_output=True, text=True).stdout.strip()
    assert shown == f"{env.session}|{transcript_path(env)}"
    assert embedder.inputs[0] == "warm-up"
    env.wait(lambda: env.sql(f"SELECT count(*) FROM {env.vault}.entry")[0][0] == 1 and not list(queue.glob("*.json")))


def test_prompt_off_injects_nothing_and_logs(env):
    old_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    env.config(service="val3down", embedding={"url": DOWN})
    deliver(env, prompt=False)
    result = ask(env, APRIL)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    _, records = prompt_log(env)
    assert len(records) == 1
    assert records[0]["prompt"] == APRIL and records[0]["injected"] == [] and records[0]["method"] == "off"
