import json

from helpers import OUTPUT, fact, make_vault, save


def index_id(env, name):
    return env.sql(f"INSERT INTO {env.vault}.knowledge_index (name) VALUES (%s) RETURNING id", (name,))[0][0]


def alias(env, name, target_id):
    env.sql(f"INSERT INTO {env.vault}.index_alias (name, target_id) VALUES (%s, %s)", (name, target_id))


def test_show_lists_indexes_after_aliases(env):
    make_vault(env)
    result = save(env, fact(indexes=["billing", "invoices", "facturas", "ventas"]),
                  env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    ids = {name: id_ for id_, name in env.sql(f"SELECT id, name FROM {env.vault}.knowledge_index")}
    alias(env, "invoices", ids["billing"])  # two aliases converge on billing
    alias(env, "facturas", ids["billing"])
    alias(env, "ventas", ids["invoices"])  # and a two-hop chain: ventas -> invoices -> billing
    lines = env.run("show", f"{env.vault}#1").stdout.splitlines()
    assert [line for line in lines if line.startswith("indexes:")] == ["indexes: billing"]


def test_save_stores_an_alias_name_as_its_target(env):
    make_vault(env)
    target = index_id(env, "facturas")
    index_id(env, "facturacion")
    alias(env, "facturacion", target)
    result = save(env, fact(indexes=["facturacion"]), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT k.name FROM {env.vault}.entry_index ei "
                   f"JOIN {env.vault}.knowledge_index k ON k.id = ei.index_id") == [("facturas",)]


def test_readers_work_on_a_vault_without_the_alias_table(env):
    make_vault(env)
    env.sql(f"DROP TABLE {env.vault}.index_alias")  # a vault created before aliases
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    data = {"session_id": env.session, "transcript_path": "/nonexistent", "cwd": str(env.project),
            "hook_event_name": "SessionStart", "source": "startup"}
    result = env.run("hook", "session-start", input=json.dumps(data))
    assert result.returncode == 0, result.stderr
    text = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert f"## billing\n- {env.vault}#1 [fact]" in text
    assert "indexes: billing" in env.run("show", f"{env.vault}#1").stdout


def saved(env, *index_lists):
    """One fact per list of index names, each with its own title; returns their refs."""
    refs = []
    for n, names in enumerate(index_lists, start=1):
        result = save(env, fact(title=f"Fact {n}", statement=f"Statement {n}.", indexes=names),
                      env.transcript(tool_outputs=[OUTPUT]))
        assert result.returncode == 0, result.stderr
        refs.append(result.stdout.split()[-1])
    return refs


def aliases(env):
    return env.sql(f"SELECT count(*) FROM {env.vault}.index_alias")[0][0]


def merge_events(env):
    return env.sql(f"SELECT count(*) FROM {env.vault}.event WHERE action = 'index-merged'")[0][0]


def test_index_lists_names_with_live_counts(env):
    make_vault(env)
    saved(env, ["billing"], ["billing"], ["flows"])
    result = env.run("index")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["billing  2", "flows  1"]


def test_index_name_lists_its_entries(env):
    make_vault(env)
    first, second, _ = saved(env, ["billing"], ["billing"], ["flows"])
    result = env.run("index", "billing")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"- {first} [fact] Fact 1", f"- {second} [fact] Fact 2"]


def test_index_none_lists_entries_without_an_index(env):
    make_vault(env)
    _, none, vault_named = saved(env, ["billing"], [], [env.vault.replace("_", "-")])  # D1: a vault's name
    result = env.run("index", "--none")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"- {none} [fact] Fact 2", f"- {vault_named} [fact] Fact 3"]


def test_index_usage_errors(env):
    make_vault(env)
    assert env.run("index", "billing", "--none").returncode == 2
    assert env.run("index", "a", "b").returncode == 2
    assert env.run("index", "nomerge", "a", "b").returncode == 2
    assert env.run("index", "nope").returncode == 1


def test_merge_moves_entries_under_the_target(env):
    make_vault(env)
    first, second = saved(env, ["facturacion"], ["facturas"])
    result = env.run("index", "merge", "facturacion", "facturas")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "merged facturacion into facturas (1 entries)"
    assert env.run("index", "facturas").stdout.splitlines() == [f"- {first} [fact] Fact 1",
                                                                f"- {second} [fact] Fact 2"]
    assert env.run("index").stdout.splitlines() == ["facturas  2"]
    assert aliases(env) == 1
    assert env.sql(f"SELECT detail FROM {env.vault}.event WHERE action = 'index-merged'") == [
        ({"from": "facturacion", "into": "facturas"},)]


def test_merge_into_an_alias_stores_its_final_target(env):
    make_vault(env)
    saved(env, ["a"], ["b"], ["c"])
    assert env.run("index", "merge", "a", "b").returncode == 0
    result = env.run("index", "merge", "c", "a")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "merged c into b (1 entries)"
    assert env.sql(f"SELECT a.name, k.name FROM {env.vault}.index_alias a "
                   f"JOIN {env.vault}.knowledge_index k ON k.id = a.target_id ORDER BY a.name") == [
        ("a", "b"), ("c", "b")]


def test_merging_a_target_resolves_the_chain(env):
    make_vault(env)
    refs = saved(env, ["a"], ["b"], ["c"])
    assert env.run("index", "merge", "a", "b").returncode == 0
    result = env.run("index", "merge", "b", "c")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "merged b into c (2 entries)"
    assert env.run("index", "c").stdout.splitlines() == [f"- {ref} [fact] Fact {n}" for n, ref in enumerate(refs, 1)]


def test_merge_refusals(env):
    make_vault(env)
    saved(env, ["a"], ["b"], ["c"])
    assert env.run("index", "merge", "a", "b").returncode == 0
    for words in (("nope", "b"), ("c", "nope"), ("c", "c"), ("a", "c"), ("b", "a")):
        result = env.run("index", "merge", *words)
        assert result.returncode == 1, words
        assert result.stderr.startswith("val3: error: "), words
    assert aliases(env) == 1
    assert merge_events(env) == 1


def test_index_named_merge_is_listed(env):
    make_vault(env)
    (ref,) = saved(env, ["merge"])
    result = env.run("index", "merge")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"- {ref} [fact] Fact 1"]


def test_index_output_escapes_terminal_sequences(env):
    make_vault(env)
    result = save(env, fact(title="Clear \x1b[2J and ‮ flip"), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    out = env.run("index", "billing").stdout
    assert "\x1b" not in out and "‮" not in out
    assert "Clear \\x1b[2J and \\u202e flip" in out


def test_merge_on_a_vault_without_the_alias_table(env):
    make_vault(env)
    saved(env, ["a"], ["b"])
    env.sql(f"DROP TABLE {env.vault}.index_alias")
    result = env.run("index", "merge", "a", "b")
    assert result.returncode == 1
    assert f"val3 vault upgrade {env.vault}" in result.stderr
    assert env.run("index").stdout.splitlines() == ["a  1", "b  1"]
