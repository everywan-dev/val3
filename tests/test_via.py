"""Who wrote an entry: the writer records the channel in the detail of its `admitted` event."""
from helpers import OUTPUT, fact, legacy, make_vault, save
from test_import import apply, ready_manifest
from viewer import viewing


def via(env):
    return env.sql(f"SELECT entry_id, detail FROM {env.vault}.event WHERE action = 'admitted' ORDER BY entry_id")


def test_save_capture_and_tool_add_are_recorded(env):
    make_vault(env)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    assert result.stdout == f"admitted {env.vault}#1\n"
    t = env.transcript(user_messages=["How many invoices in May?"], tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": fact(title="May has 42 invoices")}])
    result = env.run("extract", str(t))
    assert result.returncode == 0 and ": admitted 1," in result.stdout, result.stderr
    result = env.run("tool", "add", "code-graph", "--type", "cli", "--description", "Answers overrides.")
    assert result.stdout == f"admitted {env.vault}#3\n", result.stderr
    assert via(env) == [(1, {"via": "save"}), (2, {"via": "capture"}), (3, {"via": "tool-add"})]


def test_import_is_recorded_and_other_approvals_are_not(env):
    path = ready_manifest(env)
    assert apply(env, path).returncode == 0
    [(queued,)] = env.sql(f"SELECT id FROM {env.vault}.review_item r WHERE NOT EXISTS "
                          f"(SELECT 1 FROM {env.vault}.review_decision d WHERE d.review_id = r.id)")
    assert env.run("review", "approve", str(queued)).returncode == 0
    legacy(env, fact(title="Queued before automatic admission"))
    [(waiting,)] = env.sql(f"SELECT max(id) FROM {env.vault}.review_item")
    assert env.run("review", "approve", str(waiting)).returncode == 0
    assert [detail for _, detail in via(env)] == [{"via": "import"}, {"via": "import"}, {}]


def test_the_view_shows_the_channel_and_not_recorded_before_it(env):
    make_vault(env)
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    env.sql(f"INSERT INTO {env.vault}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
            "VALUES ('fact', 'Older', 'Admitted before the channel was recorded.', 'h-older', 'project', 'm')")
    env.sql(f"INSERT INTO {env.vault}.event (action, entry_id, project, machine) VALUES ('admitted', 2, 'project', 'm')")
    with viewing(env) as view:
        rows = {r["ref"]: r["via"] for r in view.json("api/entries")["rows"]}
        [home] = view.json("api/summary")["vaults"]
        filtered = [r["ref"] for r in view.json("api/entries?via=not-recorded")["rows"]]
    assert rows == {f"{env.vault}#1": "save", f"{env.vault}#2": None}
    assert filtered == [f"{env.vault}#2"]
    [day] = home["admitted_per_day"]
    assert day["via"] == {"save": 1, "not-recorded": 1}
