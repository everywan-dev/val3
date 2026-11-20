from helpers import make_vault

ADD = ("tool", "add", "code-graph", "--type", "mcp", "--description", "Answers which module overrides a method.",
       "--question", "which module overrides a method?", "--environment", "https://example.org/code-graph.git",
       "--priority", "5", "--index", "modules")


def test_tool_add_admits_a_tool(env):
    make_vault(env)
    result = env.run(*ADD)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert "val3: new knowledge index: modules (existing: val3 index)" in result.stderr.splitlines()
    assert env.sql(f"SELECT kind, title, keywords FROM {env.vault}.entry") == [
        ("tool", "code-graph", "which module overrides a method?")]
    assert env.sql(f"SELECT tool_type, environment, priority FROM {env.vault}.tool") == [
        ("mcp", "https://example.org/code-graph.git", 5)]
    shown = env.run("show", f"{env.vault}#1").stdout
    assert "tool: mcp, priority 5" in shown and "indexes: modules" in shown
    assert env.run(*ADD).stdout.strip() == f"duplicate of {env.vault}#1"


def test_tool_add_rejects_an_unknown_type(env):
    make_vault(env)
    result = env.run("tool", "add", "x", "--type", "plugin", "--description", "d")
    assert result.returncode == 2
