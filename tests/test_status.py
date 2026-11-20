import json

from helpers import OUTPUT, embedding_vault, fact, make_vault, save
from test_supersede import newer, transcript as supersede_transcript


def hooks_file(path, *events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {name: [{"hooks": [{"type": "command", "command": f"val3 hook {event}"}]}]
                                          for name, event in events}}))


def ask(env, text):
    data = {"session_id": env.session, "cwd": str(env.project), "hook_event_name": "UserPromptSubmit",
            "prompt": text}
    assert env.run("hook", "prompt", input=json.dumps(data)).returncode == 0


def test_status_of_an_active_project(env):
    make_vault(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    hooks_file(env.project / ".claude" / "settings.local.json",
               ("SessionStart", "session-start"), ("UserPromptSubmit", "prompt"))
    ask(env, "how many invoices in April")
    ask(env, "unrelated question \x1b[2J here")
    result = env.run("status")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert f"project project · {env.project} · service val3test" in out
    assert f"home vault {env.vault}: 1 entries: 1 with evidence, 0 attested, 0 unverified · linked: none" in out
    assert "search: full-text (no [embedding] in val3.toml)" in out
    assert "hooks: session-start, prompt in .claude/settings.local.json · no capture" in out
    assert "captures: queue empty" in out
    lines = out.splitlines()
    first, second = lines[lines.index("last prompts:") + 1:][:2]
    assert env.session[:8] in first and f"1 injected ({env.vault}#1)" in first and "how many invoices" in first
    assert "0 injected" in second and "\x1b" not in out and "\\x1b" in second


def test_status_outside_a_project(env):
    elsewhere = env.root / "elsewhere"
    elsewhere.mkdir()
    result = env.run("status", cwd=elsewhere)
    assert result.returncode == 0
    assert result.stdout.strip() == f"VAL3 is not active here (no val3.toml in {elsewhere} or its parents)"


def test_status_with_the_database_down(env):
    env.config(service="val3down")
    result = env.run("status")
    assert result.returncode == 1
    assert "project project" in result.stdout and "hooks: none" in result.stdout
    assert f"home vault {env.vault}: database unreachable (" in result.stdout
    assert "last prompts: none logged" in result.stdout
    assert "to re-check" not in result.stdout


def test_status_names_a_missing_home_vault(env):
    env.config()
    result = env.run("status")
    assert result.returncode == 1
    assert f"home vault {env.vault}: does not exist (`val3 vault create {env.vault}`)" in result.stdout.splitlines()


def test_status_names_single_entry_indexes(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    for n, name in enumerate(["billing", "billing", "flows"]):
        assert save(env, fact(title=f"Fact {n}", indexes=[name]), t).returncode == 0
    lines = env.run("status").stdout.splitlines()
    recheck = next(i for i, line in enumerate(lines) if line.startswith("to re-check"))
    assert lines[recheck + 1:recheck + 3] == ["to settle: 0", "indexes: 2 · with one entry: flows"]


def test_status_lists_at_most_ten_single_entry_indexes(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    names = [f"i{n:02d}" for n in range(11)]
    assert save(env, fact(indexes=names), t).returncode == 0
    lines = env.run("status").stdout.splitlines()
    assert f"indexes: 11 · with one entry: {', '.join(names[:10])}, …" in lines


def changes_line(env):
    result = env.run("status")
    assert result.returncode == 0, result.stderr
    [line] = [line for line in result.stdout.splitlines() if line.startswith("changes (7 days)")]
    return line


def test_status_lists_recent_changes(env):
    make_vault(env)
    assert changes_line(env) == "changes (7 days): none"
    t = supersede_transcript(env)
    assert save(env, fact(), t).returncode == 0                                   # #1
    assert save(env, newer(env), t).returncode == 0                               # #2 supersedes #1
    assert save(env, fact(title="May has 50 invoices", statement="The ERP holds 50 invoices dated May 2026."),
                t).returncode == 0                                                # #3
    assert env.run("withdraw", f"{env.vault}#3", "--wrong", "--reason", "Miscounted.", tty=True).returncode == 0
    assert changes_line(env) == f"changes (7 days): 1 superseded ({env.vault}#1), 1 withdrawn ({env.vault}#3)"


def test_status_counts_pairs_to_settle(env, embedder):
    embedding_vault(env, embedder.url)
    t = env.transcript(tool_outputs=[OUTPUT])
    twin = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.")
    for candidate in (fact(), twin):
        assert save(env, candidate, t).returncode == 0
    lines = env.run("status").stdout.splitlines()
    recheck = next(i for i, line in enumerate(lines) if line.startswith("to re-check"))
    assert lines[recheck + 1] == f"to settle: 1 ({env.vault}#2)"
    assert env.run("settle", f"{env.vault}#2", f"{env.vault}#1", "--keep").returncode == 0
    assert "to settle: 0" in env.run("status").stdout.splitlines()


def tools_line(env, environ=None):
    result = env.run("status", environ=environ)
    assert "Traceback" not in result.stderr, result.stderr
    [line] = [line for line in result.stdout.splitlines() if line.startswith("memory tools:")]
    return line


def test_status_reports_the_memory_tools(env):
    make_vault(env)
    assert tools_line(env) == "memory tools: not configured"
    # no `val3` on PATH: status runs this installation's own server, not the command in .mcp.json
    assert env.run("init").returncode == 0
    assert tools_line(env) == "memory tools: configured, answering"
    marker = env.root / "ran"
    for server in ({"command": "touch", "args": [str(marker)]}, {"command": "false", "args": []},
                   {"command": "val3"}, {"command": "val3", "args": ["mcp", "--x"]},
                   {"command": "val3", "args": 42}, {"command": "val3", "args": ["mcp\x00"]}):
        (env.project / ".mcp.json").write_text(json.dumps({"mcpServers": {"val3": server}}))
        assert tools_line(env) == "memory tools: configured with another command (not run)", server
    assert not marker.exists()
    (env.project / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    assert tools_line(env) == "memory tools: not configured"


def test_status_reports_a_local_memory_tools_set_up(env):
    make_vault(env)
    claude = env.project / ".claude"
    claude.mkdir()
    (claude / "settings.local.json").write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "val3 hook session-start"}]}]}}))
    assert tools_line(env) == 'memory tools: local set-up (see "claude mcp list")'
