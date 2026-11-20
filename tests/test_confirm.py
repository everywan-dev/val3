import json
import shlex
import shutil
import subprocess
import sys

from helpers import QUOTE, fact, save
from test_import import ROUTING, commit, write
from test_recheck import CHANGED, assert_unmarked, imported, index, shown, status_line, unit_citing

OWNER = [{"kind": "file", "path": "config/routing.yaml", "quote": "owner: billing"}]


def confirm(env, ref, evidence, *extra):
    return env.run("confirm", ref, *extra, input=json.dumps(evidence))


def evidence_rows(env):
    return env.sql(f"SELECT entry_id, kind, reproduction, quote FROM {env.vault}.evidence ORDER BY id")


def changed(env):
    """The imported entry, with its file changed at a new HEAD: it is marked."""
    ref = imported(env)
    write(env, "config/routing.yaml", CHANGED)
    sha = commit(env)
    assert "TO RE-CHECK" in shown(env, ref)
    return ref, sha


def test_confirming_with_a_file_quote_at_head_clears_the_mark(env):
    ref, sha = changed(env)
    before = evidence_rows(env)
    reviews = env.sql(f"SELECT count(*) FROM {env.vault}.review_item")
    result = confirm(env, ref, OWNER)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"confirmed {ref} (1 evidence items at {sha[:12]})\n"
    rows = evidence_rows(env)
    assert rows[:len(before)] == before
    assert rows[len(before):] == [(1, "file", f"git show {sha}:config/routing.yaml", "owner: billing")]
    assert_unmarked(env, ref)
    assert status_line(env) == "to re-check: 0"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.event WHERE action = 'confirmed' AND entry_id = 1 "
                   "AND detail->>'evidence' = '1'") == [(1,)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == reviews


def test_a_confirmation_without_file_items_ends_the_mark(env):
    ref, _ = changed(env)
    t = env.transcript(tool_outputs=["subscribers:\n  - billing"])
    item = {"kind": "command", "reproduction": "git show HEAD:config/routing.yaml",
            "quote": "subscribers:\n  - billing"}
    result = confirm(env, ref, [item], "--transcript", str(t))
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"confirmed {ref} (1 evidence items)\n"
    assert_unmarked(env, ref)
    write(env, "config/routing.yaml", "subscribers: []\n")
    commit(env)
    assert_unmarked(env, ref)


def test_confirm_rejects_a_quote_from_a_memory_tool_result(env):
    ref, _ = changed(env)
    before = evidence_rows(env)
    t = env.transcript(tool_calls=[("mcp__val3__memory_read", {"ref": ref}, "subscribers:\n  - billing")])
    item = {"kind": "command", "reproduction": "git show HEAD:config/routing.yaml",
            "quote": "subscribers:\n  - billing"}
    result = confirm(env, ref, [item], "--transcript", str(t))
    assert result.returncode == 1 and "discarded" in result.stderr
    assert evidence_rows(env) == before
    assert env.sql(f"SELECT count(*) FROM {env.vault}.event WHERE action = 'confirmed'") == [(0,)]


def test_confirm_refuses_bad_file_items(env):
    ref, _ = changed(env)
    write(env, ".env", "owner: billing\n")
    commit(env)
    before = evidence_rows(env)
    for item in ({"path": "config/routing.yaml", "quote": "subscribers: {}"},
                 {"path": ".env", "quote": "owner: billing"},
                 {"path": "config", "quote": "owner: billing"},
                 {"path": "config/missing.yaml", "quote": "owner: billing"},
                 {"path": "./config/routing.yaml", "quote": "owner: billing"},
                 {"path": "config//routing.yaml", "quote": "owner: billing"},
                 {"path": "docs/../config/routing.yaml", "quote": "owner: billing"},
                 {"path": "./memory/route-by-owner.md", "quote": "thread owner"},
                 {"path": "memory/route-by-owner.md", "quote": "thread owner"},
                 {"path": "config/routing.yaml", "quote": "billing"},
                 {"path": "config/routing.yaml", "quote": "owner: billing", "commit": "0" * 40}):
        result = confirm(env, ref, [{"kind": "file", **item}])
        assert result.returncode == 1, (item, result.stdout)
        assert "evidence[0]" in result.stderr, (item, result.stderr)
    assert evidence_rows(env) == before


def test_a_file_only_confirmation_needs_no_transcript(env):
    ref, _ = changed(env)
    result = env.run("confirm", ref, input=json.dumps(OWNER), environ={"VAL3_TRANSCRIPT": ""})
    assert result.returncode == 0, result.stderr


def test_confirm_reads_the_evidence_from_a_file(env):
    ref, _ = changed(env)
    path = env.root / "evidence.json"
    path.write_text(json.dumps(OWNER))
    result = env.run("confirm", ref, str(path))
    assert result.returncode == 0, result.stderr


def test_a_confirmation_keeps_only_the_files_it_cites(env):
    ref = unit_citing(env, {"config/routing.yaml": ROUTING, "docs/adr/0001.md": "route every message by its "
                            "thread owner\n"},
                      [("config/routing.yaml", "subscribers: {}"),
                       ("docs/adr/0001.md", "route every message by its thread owner")])
    write(env, "config/routing.yaml", CHANGED)
    write(env, "docs/adr/0001.md", "route by topic\n")
    commit(env)
    assert "Routing ships no subscribers · to re-check" in index(env)
    result = confirm(env, ref, OWNER)
    assert result.returncode == 0, result.stderr
    assert_unmarked(env, ref)
    write(env, "docs/adr/0001.md", "route by thread\n")
    commit(env)
    assert_unmarked(env, ref)
    write(env, "config/routing.yaml", "subscribers: []\n")
    sha = commit(env)
    assert shown(env, ref).splitlines()[1] == f"TO RE-CHECK: config/routing.yaml changed at HEAD {sha[:12]}"


def test_confirm_checks_transcript_quotes(env):
    ref, _ = changed(env)
    before = evidence_rows(env)
    t = env.transcript(tool_outputs=["something else entirely"])
    item = {"kind": "command", "reproduction": "git show HEAD:config/routing.yaml",
            "quote": "subscribers:\n  - billing"}
    result = confirm(env, ref, [*OWNER, item], "--transcript", str(t))
    assert result.returncode == 1 and "discarded" in result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.event WHERE action = 'discarded'") == [(1,)]
    assert evidence_rows(env) == before
    assert env.sql(f"SELECT count(*) FROM {env.vault}.event WHERE action = 'confirmed'") == [(0,)]


def test_confirm_needs_a_live_home_entry(env):
    ref = imported(env)
    v = env.vault
    t = env.transcript(tool_outputs=[QUOTE])
    assert save(env, fact(), t).returncode == 0  # #2
    newer = fact(title="April has 42 invoices, drafts excluded",
                 statement="The ERP holds 42 invoices dated April 2026, drafts excluded.", supersedes=f"{v}#2")
    assert save(env, newer, t).returncode == 0  # #3
    assert env.run("withdraw", f"{v}#3", "--wrong", "--reason", "Drafts were counted.", tty=True).returncode == 0
    shared = v + "_linked"
    assert env.run("vault", "create", shared).returncode == 0
    env.config(linked=[shared])
    before = evidence_rows(env)
    for target, evidence, message in ((f"{v}#2", OWNER, f"was superseded by {v}#3"),
                                      (f"{v}#3", OWNER, "was withdrawn (wrong)"),
                                      (f"{shared}#1", OWNER, "read only"),
                                      (f"{v}#9", OWNER, "does not exist"),
                                      (ref, [], "at least one evidence item")):
        result = confirm(env, target, evidence)
        assert result.returncode == 1 and message in result.stderr, (target, result.stderr)
    result = env.run("confirm", ref, input="not json")
    assert result.returncode == 1 and "not valid JSON" in result.stderr
    assert evidence_rows(env) == before


def test_confirm_with_a_path_with_spaces_and_accents(env):
    path = "config/rúta de prueba.yaml"
    ref = unit_citing(env, {path: ROUTING}, [(path, "subscribers: {}")])
    write(env, path, CHANGED)
    sha = commit(env)
    assert "TO RE-CHECK" in shown(env, ref)
    result = confirm(env, ref, [{"kind": "file", "path": path, "quote": "owner: billing"}])
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT reproduction FROM {env.vault}.evidence WHERE kind = 'file' ORDER BY id DESC LIMIT 1") == [
        (f"git show {shlex.quote(sha + ':' + path)}",)]
    assert_unmarked(env, ref)


def test_confirm_outside_a_git_repository_refuses_file_items(env):
    ref = imported(env)
    shutil.rmtree(env.project / ".git")
    result = confirm(env, ref, OWNER)
    assert result.returncode == 1 and "to be the root of a git repository" in result.stderr


def test_confirm_help_shows_the_evidence_format(env):
    result = env.run("confirm", "--help")
    assert result.returncode == 0
    assert '"kind": "file"' in result.stdout


def test_evidence_that_is_not_utf8_is_an_error(env):
    ref = imported(env)
    bad = env.project / "bad.json"
    bad.write_bytes(b'[{"kind": "user", "quote": "\xff\xfe broken"}]')
    for args, stdin in ((["confirm", ref, str(bad)], None), (["confirm", ref], bad.read_bytes()),
                        (["save", str(bad)], None)):
        result = subprocess.run([sys.executable, "-m", "val3", *args], input=stdin, capture_output=True,
                                cwd=env.project, env=env.environ, timeout=60)
        assert result.returncode == 1, args
        assert b"Traceback" not in result.stderr and b"UTF-8" in result.stderr, result.stderr
