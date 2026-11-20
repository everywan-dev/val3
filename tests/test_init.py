import json
import os
import tomllib
import uuid

import pytest

BEGIN, END = "<!-- val3:begin -->", "<!-- val3:end -->"
PARTIAL = ("VAL3 is partial and may be out of date: it complements the project's documents, it does not replace "
           "them. Whatever it shows, also search the project's files before answering or concluding that something "
           "is not recorded.")
EVENTS = {"SessionStart": "session-start", "UserPromptSubmit": "prompt", "PreCompact": "capture",
          "SessionEnd": "capture"}
SERVER = {"command": "val3", "args": ["mcp"]}
TOOLS = ["mcp__val3__memory_search", "mcp__val3__memory_read"]
APPROVE = "approve the val3 server once in an interactive session"
LOCAL = 'mcp: run "claude mcp add --scope local val3 -- val3 mcp"'


def vault(env):
    result = env.run("vault", "create", env.vault, "--service", "val3test")
    assert result.returncode == 0, result.stderr
    return env.vault


def init(env, *extra):
    return env.run("init", "--service", "val3test", "--vault", env.vault, *extra)


def settings(env, name="settings.json"):
    return json.loads((env.project / ".claude" / name).read_text())


def commands(data, event):
    return [hook["command"] for group in data["hooks"].get(event, []) for hook in group["hooks"]]


def snapshot(env):
    return {p.relative_to(env.project): p.read_bytes() for p in env.project.rglob("*") if p.is_file()}


def test_init_sets_up_a_project(env):
    vault(env)
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == ["val3.toml: written", "hooks: added to .claude/settings.json",
                                          "mcp: added to .mcp.json", "permissions: added to .claude/settings.json",
                                          APPROVE, "CLAUDE.md: created", "connection: ok"]
    assert env.run("status").returncode == 0
    data = settings(env)
    assert data["autoDreamEnabled"] is False
    for event, name in EVENTS.items():
        assert commands(data, event) == [f"val3 hook {name}"]
    text = (env.project / "CLAUDE.md").read_text()
    assert text.count(BEGIN) == 1 and text.count(END) == 1 and "## VAL3 memory" in text
    block = text[text.index(BEGIN):text.index(END)]
    assert len(block.splitlines()) <= 30
    assert PARTIAL in " ".join(block.split())


def test_init_twice_changes_nothing(env):
    vault(env)
    assert init(env).returncode == 0
    before = snapshot(env)
    result = env.run("init")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "val3.toml: kept",
        "hooks: kept (session-start, prompt, capture in .claude/settings.json)",
        "mcp: kept (.mcp.json)", "permissions: kept (.claude/settings.json)",
        "CLAUDE.md: block replaced", "connection: ok"]
    assert snapshot(env) == before


def test_init_keeps_other_settings_and_hooks(env):
    vault(env)
    original = {"$schema": "https://example.org/schema.json", "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}],
                          "Stop": [{"hooks": [{"type": "command", "command": "echo bye"}]}]}}
    (env.project / ".claude").mkdir()
    (env.project / ".claude" / "settings.json").write_text(json.dumps(original))
    assert init(env).returncode == 0
    data = settings(env)
    assert data["$schema"] == original["$schema"]
    assert data["permissions"] == {"allow": ["Bash(ls)", *TOOLS]}
    assert commands(data, "SessionStart") == ["echo hi", "val3 hook session-start"]
    assert commands(data, "Stop") == ["echo bye"]
    assert commands(data, "SessionEnd") == ["val3 hook capture"]


def test_init_keeps_existing_val3_hooks_in_settings_local(env):
    vault(env)
    (env.project / ".claude").mkdir()
    local = env.project / ".claude" / "settings.local.json"
    local.write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "val3 hook session-start"}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "val3 hook prompt"}]}]}}))
    before = json.loads(local.read_text())
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "hooks: kept (session-start, prompt in .claude/settings.local.json)" in result.stdout.splitlines()
    assert not (env.project / ".claude" / "settings.json").exists()
    assert json.loads(local.read_text()) == {**before, "permissions": {"allow": TOOLS}}


def test_init_recognises_quoted_val3_hook_commands(env):
    vault(env)
    (env.project / ".claude").mkdir()
    local = env.project / ".claude" / "settings.local.json"
    local.write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": 'val3 hook "session-start"'}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": '"/opt/val3/bin/val3" hook prompt'}]}]}}))
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "hooks: kept (session-start, prompt in .claude/settings.local.json)" in result.stdout.splitlines()
    assert not (env.project / ".claude" / "settings.json").exists()
    status = env.run("status")
    assert "hooks: session-start, prompt in .claude/settings.local.json · no capture" in status.stdout


def test_init_recognises_val3_hook_commands_followed_by_shell_punctuation(env):
    vault(env)
    (env.project / ".claude").mkdir()
    local = env.project / ".claude" / "settings.local.json"
    local.write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "val3 hook session-start;"}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "val3 hook prompt||true"}]}]}}))
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "hooks: kept (session-start, prompt in .claude/settings.local.json)" in result.stdout.splitlines()
    assert not (env.project / ".claude" / "settings.json").exists()


def test_init_ignores_val3_hook_text_inside_a_command(env):
    vault(env)
    (env.project / ".claude").mkdir()
    (env.project / ".claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo 'val3 hook prompt'"}]}]}}))
    result = init(env)
    assert "hooks: added to .claude/settings.json" in result.stdout
    data = settings(env)
    for event, name in EVENTS.items():
        assert commands(data, event) == [f"val3 hook {name}"]


def test_init_replaces_only_its_block(env):
    vault(env)
    path = env.project / "CLAUDE.md"
    path.write_text(f"# Project\n\nbefore\n{BEGIN}\nold block\n{END}\nafter\n")
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLAUDE.md: block replaced" in result.stdout
    text = path.read_text()
    assert text.startswith("# Project\n\nbefore\n" + BEGIN + "\n## VAL3 memory")
    assert text.endswith(END + "\nafter\n") and "old block" not in text


def test_init_replaces_an_older_block_with_the_partial_notice(env):
    vault(env)
    path = env.project / "CLAUDE.md"
    older = ("## VAL3 memory\n\nVAL3 holds the verified facts of this project. At session start it injects an index "
             "of its entries,\nand on each prompt up to three matching entries; both happen automatically.\n\n"
             "- `val3 --help` for the rest.\n")
    path.write_text(f"# Project\n{BEGIN}\n{older}{END}\n")
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLAUDE.md: block replaced" in result.stdout.splitlines()
    text = path.read_text()
    assert text.count(BEGIN) == 1 and text.count(END) == 1
    assert " ".join(text.split()).count(PARTIAL) == 1


def test_init_keeps_the_block_in_claude_local_md(env):
    vault(env)
    shared = env.project / "CLAUDE.md"
    shared.write_text("# Project\n")
    local = env.project / "CLAUDE.local.md"
    local.write_text(f"# Local\n{BEGIN}\nold block\n{END}\n")
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLAUDE.local.md: block replaced" in result.stdout.splitlines()
    assert shared.read_text() == "# Project\n"
    text = local.read_text()
    assert text.startswith("# Local\n" + BEGIN + "\n## VAL3 memory") and "old block" not in text


def test_init_appends_the_block(env):
    vault(env)
    path = env.project / "CLAUDE.md"
    path.write_text("# Project\nno newline")
    result = init(env)
    assert "CLAUDE.md: block written" in result.stdout
    assert path.read_text().startswith("# Project\nno newline\n\n" + BEGIN + "\n")


def test_init_refuses_a_half_marked_claude_md(env):
    vault(env)
    path = env.project / "CLAUDE.md"
    path.write_text(f"# Project\n{BEGIN}\nhand edited\n")
    before = path.read_bytes()
    result = init(env)
    assert result.returncode == 1
    assert "CLAUDE.md" in result.stderr
    assert path.read_bytes() == before
    assert result.stdout == ""
    assert sorted(p.name for p in env.project.iterdir()) == ["CLAUDE.md"]


def test_init_refuses_repeated_or_reversed_markers(env):
    vault(env)
    path = env.project / "CLAUDE.md"
    for text in (f"{BEGIN}\n{END}\n{BEGIN}\n{END}\n", f"{END}\n{BEGIN}\n", f"{BEGIN}\n{BEGIN}\n{END}\n"):
        path.write_text(text)
        result = init(env)
        assert result.returncode == 1 and "CLAUDE.md" in result.stderr
        assert path.read_text() == text


def test_init_keeps_crlf_and_text_outside_the_block(env):
    vault(env)
    path = env.project / "CLAUDE.md"
    original = "# Projekt — größe\r\nzwei Zeilen\r\nno final newline".encode()
    path.write_bytes(original)
    assert init(env).returncode == 0
    data = path.read_bytes()
    assert data.startswith(original + b"\n\n" + BEGIN.encode())
    assert init(env).returncode == 0
    assert path.read_bytes() == data


def test_init_with_differing_flags_exits_2(env):
    vault(env)
    assert init(env).returncode == 0
    result = env.run("init", "--service", "other")
    assert result.returncode == 2
    assert 'val3.toml exists and says service = "val3test"' in result.stderr


def test_init_without_flags_and_without_config_exits_2(env):
    result = env.run("init")
    assert result.returncode == 2
    assert "--service" in result.stderr and "--vault" in result.stderr
    assert not (env.project / "val3.toml").exists()


def test_init_reports_a_missing_vault(env):
    missing = f"v_{uuid.uuid4().hex[:10]}"
    result = env.run("init", "--service", "val3test", "--vault", missing)
    assert result.returncode == 1
    assert (env.project / "val3.toml").exists() and (env.project / "CLAUDE.md").exists()
    assert (env.project / ".claude" / "settings.json").exists()
    assert any(line.startswith("connection: ") and missing in line for line in result.stdout.splitlines())


def test_init_reports_an_old_vault(env):
    vault(env)
    env.sql(f"DROP TABLE {env.vault}.index_alias")
    result = init(env)
    assert result.returncode == 1
    assert f"connection: vault {env.vault} needs `val3 vault upgrade {env.vault}`" in result.stdout


def test_init_reports_a_vault_without_the_neighbour_tables(env):
    vault(env)
    env.sql(f"DROP TABLE {env.vault}.settlement, {env.vault}.neighbour, {env.vault}.neighbour_exempt")
    result = init(env)
    assert result.returncode == 1
    assert f"connection: vault {env.vault} needs `val3 vault upgrade {env.vault}`" in result.stdout


def test_init_reports_a_vault_with_an_old_check(env):
    vault(env)
    env.sql(f"ALTER TABLE {env.vault}.withdrawal DROP CONSTRAINT withdrawal_cause_check, "
            "ADD CHECK (cause IN ('wrong', 'obsolete'))")
    result = init(env)
    assert result.returncode == 1
    assert f"connection: vault {env.vault} needs `val3 vault upgrade {env.vault}`" in result.stdout


def test_init_reports_an_unreachable_server(env):
    result = env.run("init", "--service", "val3down", "--vault", "anything")
    assert result.returncode == 1
    last = result.stdout.splitlines()[-1]
    assert last.startswith("connection: ") and last != "connection: ok"


def test_init_with_invalid_settings_json_exits_1(env):
    vault(env)
    claude = env.project / ".claude"
    claude.mkdir()
    for name, text in (("settings.json", "{not json"), ("settings.local.json", "[1, 2"),
                       ("settings.json", '{"hooks": []}'), ("settings.local.json", "[]")):
        for other in claude.iterdir():
            other.unlink()
        path = claude / name
        path.write_text(text)
        result = init(env)
        assert result.returncode == 1, (name, text)
        assert name in result.stderr
        assert path.read_text() == text
        assert list(claude.iterdir()) == [path]


def test_init_ignores_val3_hook_text_outside_command_hooks(env):
    vault(env)
    (env.project / ".claude").mkdir()
    (env.project / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"deny": ["Bash(val3 hook prompt)"]},
         "hooks": {"Stop": [{"hooks": [{"type": "prompt", "prompt": "val3 hook prompt"}]}]}}))
    result = init(env)
    assert "hooks: added to .claude/settings.json" in result.stdout
    data = settings(env)
    for event, name in EVENTS.items():
        assert commands(data, event) == [f"val3 hook {name}"]


def test_init_refuses_a_claude_dir_linked_outside(env):
    vault(env)
    outside = env.root / "outside"
    outside.mkdir()
    (env.project / ".claude").symlink_to(outside)
    result = init(env)
    assert result.returncode == 1
    assert ".claude" in result.stderr
    assert list(outside.iterdir()) == []


def test_init_accepts_claude_md_linked_inside(env):
    vault(env)
    (env.project / "AGENTS.md").write_text("# Agents\n")
    (env.project / "CLAUDE.md").symlink_to("AGENTS.md")
    assert init(env).returncode == 0
    assert (env.project / "CLAUDE.md").is_symlink()
    assert BEGIN in (env.project / "AGENTS.md").read_text()


def test_init_writes_safe_toml(env):
    vault(env)
    name = 'a "b"\n[embedding]'
    result = init(env, "--project", name)
    assert result.returncode == 0, result.stdout + result.stderr
    data = tomllib.loads((env.project / "val3.toml").read_text())
    assert data["project"] == name and "embedding" not in data
    assert env.run("status").returncode == 0
    (env.project / "val3.toml").unlink()
    result = init(env, "--linked", "shared", "--linked", "shared")
    assert result.returncode == 2
    assert not (env.project / "val3.toml").exists()


def test_init_reports_an_unreachable_embedding_service(env):
    vault(env)
    env.config(embedding={"url": "http://127.0.0.1:1"})
    result = env.run("init")
    assert result.returncode == 1
    assert "connection: embedding service unreachable (http://127.0.0.1:1)" in result.stdout.splitlines()


def test_init_keeps_an_embedding_url_on_one_line(env):
    vault(env)
    env.config(embedding={"url": "http://127.0.0.1:1/\nconnection: ok"})
    result = env.run("init")
    assert result.returncode == 1
    assert len([line for line in result.stdout.splitlines() if line.startswith("connection:")]) == 1


def test_init_with_an_unreadable_config_exits_1(env):
    if os.geteuid() == 0:
        pytest.skip("root reads any file")
    path = env.project / "val3.toml"
    path.write_text('service = "val3test"\n[vaults]\nhome = "x"\n')
    path.chmod(0)
    result = env.run("init")
    assert result.returncode == 1
    assert result.stderr.startswith("val3: error:") and str(path) in result.stderr
    assert "Traceback" not in result.stderr


def mcp_json(env):
    return json.loads((env.project / ".mcp.json").read_text())


def names(env):
    return sorted(p.name for p in env.project.iterdir())


def test_init_writes_the_mcp_server(env):
    vault(env)
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert mcp_json(env) == {"mcpServers": {"val3": SERVER}}
    assert settings(env)["permissions"] == {"allow": TOOLS}


def test_init_keeps_other_mcp_servers_and_keys(env):
    vault(env)
    original = {"mcpServers": {"other": {"command": "other-server", "args": ["--x"]}}, "extra": [1, 2]}
    (env.project / ".mcp.json").write_text(json.dumps(original))
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "mcp: added to .mcp.json" in result.stdout.splitlines()
    assert mcp_json(env) == {"mcpServers": {"other": original["mcpServers"]["other"], "val3": SERVER},
                             "extra": [1, 2]}


def test_init_mcp_is_idempotent(env):
    vault(env)
    assert init(env).returncode == 0
    mcp = (env.project / ".mcp.json").read_bytes()
    local = (env.project / ".claude" / "settings.json").read_bytes()
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert "mcp: kept (.mcp.json)" in lines and "permissions: kept (.claude/settings.json)" in lines
    assert APPROVE not in lines
    assert (env.project / ".mcp.json").read_bytes() == mcp
    assert (env.project / ".claude" / "settings.json").read_bytes() == local


def test_init_refuses_a_conflicting_val3_server(env):
    vault(env)
    conflicting = json.dumps({"mcpServers": {"val3": {"command": "/opt/old/val3", "args": ["mcp"]}}})
    (env.project / ".mcp.json").write_text(conflicting)
    result = init(env)  # a project that has none of the other files
    assert result.returncode == 1
    assert ".mcp.json" in result.stderr
    assert names(env) == [".mcp.json"]
    assert (env.project / ".mcp.json").read_text() == conflicting
    (env.project / ".mcp.json").unlink()
    assert init(env).returncode == 0  # a project that has all of them
    (env.project / ".mcp.json").write_text(json.dumps({"mcpServers": {"val3": {"command": "val3", "args": []}}}))
    before = snapshot(env)
    result = env.run("init")
    assert result.returncode == 1 and ".mcp.json" in result.stderr
    assert result.stdout == ""
    assert snapshot(env) == before


def test_init_refuses_mcp_json_that_is_not_an_object(env):
    vault(env)
    for text in ("[]", "{not json", '{"mcpServers": []}', '{"mcpServers": {"val3": "val3 mcp"}}'):
        (env.project / ".mcp.json").write_text(text)
        result = init(env)
        assert result.returncode == 1, text
        assert ".mcp.json" in result.stderr
        assert names(env) == [".mcp.json"]
        assert (env.project / ".mcp.json").read_text() == text


def test_init_refuses_mcp_json_through_a_symlink(env):
    vault(env)
    outside = env.root / "outside.json"
    outside.write_text("{}")
    (env.project / ".mcp.json").symlink_to(outside)
    result = init(env)
    assert result.returncode == 1
    assert ".mcp.json" in result.stderr
    assert outside.read_text() == "{}"
    assert names(env) == [".mcp.json"]


def test_init_refuses_settings_through_a_symlink(env):
    vault(env)
    outside = env.root / "outside.json"
    outside.write_text("{}")
    (env.project / ".claude").mkdir()
    (env.project / ".claude" / "settings.json").symlink_to(outside)
    result = init(env)
    assert result.returncode == 1
    assert "settings.json" in result.stderr
    assert outside.read_text() == "{}"
    assert names(env) == [".claude"]


def test_init_refuses_permissions_of_the_wrong_type(env):
    vault(env)
    claude = env.project / ".claude"
    claude.mkdir()
    for value in ([], {"allow": "Bash(ls)"}, {"allow": {}}):
        text = json.dumps({"permissions": value})
        (claude / "settings.json").write_text(text)
        result = init(env)
        assert result.returncode == 1, value
        assert "permissions" in result.stderr
        assert (claude / "settings.json").read_text() == text
        assert names(env) == [".claude"]


def test_init_adds_exactly_two_permissions(env):
    vault(env)
    (env.project / ".claude").mkdir()
    (env.project / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"allow": ["Bash(ls)", "mcp__val3__memory_read", "Read"], "deny": ["Bash(rm)", "Write"]}}))
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "permissions: added to .claude/settings.json" in result.stdout.splitlines()
    assert settings(env)["permissions"] == {
        "allow": ["Bash(ls)", "mcp__val3__memory_read", "Read", "mcp__val3__memory_search"],
        "deny": ["Bash(rm)", "Write"]}


def test_init_allows_memory_save_only_when_asked_and_never_removes_it(env):
    """Saving without asking is a person's choice: `--allow-save` adds it, a run without the flag keeps it."""
    vault(env)
    allowed = lambda: settings(env)["permissions"]["allow"]  # noqa: E731
    for _ in range(2):
        result = init(env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert allowed() == TOOLS
    for _ in range(2):
        result = env.run("init", "--allow-save")
        assert result.returncode == 0, result.stdout + result.stderr
        assert allowed() == TOOLS + ["mcp__val3__memory_save"]
    assert "permissions: kept (.claude/settings.json)" in result.stdout.splitlines()
    result = env.run("init")
    assert result.returncode == 0 and "permissions: kept (.claude/settings.json)" in result.stdout.splitlines()
    assert allowed() == TOOLS + ["mcp__val3__memory_save"]


def test_init_with_hooks_in_both_settings_files(env):
    vault(env)
    hook = {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "val3 hook capture"}]}]}}
    (env.project / ".claude").mkdir()
    for name in ("settings.json", "settings.local.json"):
        (env.project / ".claude" / name).write_text(json.dumps(hook))
    local = (env.project / ".claude" / "settings.local.json").read_bytes()
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "permissions: added to .claude/settings.json" in result.stdout.splitlines()
    assert settings(env)["permissions"] == {"allow": TOOLS}
    assert (env.project / ".claude" / "settings.local.json").read_bytes() == local
    assert mcp_json(env) == {"mcpServers": {"val3": SERVER}}


def test_init_local_setup_prints_the_command(env):
    vault(env)
    (env.project / ".claude").mkdir()
    (env.project / ".claude" / "settings.local.json").write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "val3 hook session-start"}]}]}}))
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert LOCAL in lines and "permissions: added to .claude/settings.local.json" in lines
    assert APPROVE not in lines
    assert not (env.project / ".mcp.json").exists()
    assert not (env.project / ".claude" / "settings.json").exists()
    assert settings(env, "settings.local.json")["permissions"] == {"allow": TOOLS}


def test_init_local_setup_still_checks_mcp_json(env):
    vault(env)
    claude = env.project / ".claude"
    claude.mkdir()
    (claude / "settings.local.json").write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "val3 hook session-start"}]}]}}))
    outside = env.root / "outside.json"
    outside.write_text("{}")
    for text in (json.dumps({"mcpServers": {"val3": {"command": "/opt/old/val3", "args": ["mcp"]}}}), "[]", None):
        if text is None:
            (env.project / ".mcp.json").symlink_to(outside)
        else:
            (env.project / ".mcp.json").write_text(text)
        before = snapshot(env)
        result = init(env)
        assert result.returncode == 1 and ".mcp.json" in result.stderr, (text, result.stdout + result.stderr)
        assert snapshot(env) == before and names(env) == [".claude", ".mcp.json"]
        (env.project / ".mcp.json").unlink()
    assert outside.read_text() == "{}"


def test_init_refuses_two_targets_that_are_one_file(env):
    vault(env)
    claude = env.project / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text("{}")
    (env.project / ".mcp.json").symlink_to(claude / "settings.json")
    result = init(env)
    assert result.returncode == 1 and "same file" in result.stderr, result.stdout + result.stderr
    assert (claude / "settings.json").read_text() == "{}"
    assert names(env) == [".claude", ".mcp.json"]


def test_init_checks_permissions_in_both_settings_files(env):
    vault(env)
    claude = env.project / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({"hooks": {
        "SessionEnd": [{"hooks": [{"type": "command", "command": "val3 hook capture"}]}]}}))
    for value in ([], {"allow": "Bash(ls)"}):
        (claude / "settings.local.json").write_text(json.dumps({"permissions": value}))
        before = snapshot(env)
        result = init(env)
        assert result.returncode == 1 and "settings.local.json" in result.stderr, value
        assert "permissions" in result.stderr
        assert snapshot(env) == before and names(env) == [".claude"]


def test_block_names_the_tools(env):
    vault(env)
    assert init(env).returncode == 0
    text = (env.project / "CLAUDE.md").read_text()
    block = " ".join(text[text.index(BEGIN):text.index(END)].split())
    assert "`memory_search`" in block and "`memory_read`" in block and "`memory_save`" in block
    assert PARTIAL in block
    assert ("Save durable project knowledge once a result or a decision is settled, when a later session would need "
            "it. Search first and name in `supersedes` an entry it replaces. Quote the evidence the session shows; "
            "without evidence the entry is stored unverified.") in block
    for command in ("val3 save", "val3 confirm", "val3 settle", "val3 withdraw", "val3 recall", "val3 show"):
        assert f"`{command}" in block, command


def test_init_writes_the_default_delivery(env):
    vault(env)
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    text = (env.project / "val3.toml").read_text()
    assert text.endswith("\n[delivery]\nindex = false\nprompt = false\n")
    assert tomllib.loads(text)["delivery"] == {"index": False, "prompt": False}
    assert env.run("status").returncode == 0
    data = {"session_id": env.session, "cwd": str(env.project), "hook_event_name": "SessionStart",
            "transcript_path": str(env.home / ".claude" / "projects" / "-project" / f"{env.session}.jsonl"),
            "source": "startup"}
    started = env.run("hook", "session-start", input=json.dumps(data))
    assert started.returncode == 0, started.stderr
    context = json.loads(started.stdout)["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("VAL3 memory is reached with the tools")


@pytest.mark.parametrize("delivery", ["[delivery]\nindex = true\n", ""])
def test_init_leaves_an_existing_delivery_alone(env, delivery):
    vault(env)
    path = env.project / "val3.toml"
    path.write_text(f'service = "val3test"\n[vaults]\nhome = "{env.vault}"\n{delivery}')
    before = path.read_bytes()
    result = init(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "val3.toml: kept" in result.stdout.splitlines()
    assert path.read_bytes() == before
