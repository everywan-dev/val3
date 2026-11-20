import json
import os
import shutil
import subprocess
import time

import pytest

from helpers import QUOTE, before_0012, fact, old_vault, recipe, save
from test_hooks import ask, context, hook, payload, start
from test_import import (GIT, ROUTING, adopter, answer, commit, dry_run, git, init, note_candidate,
                         unit_candidate, write)
from test_supersede import newer, second, transcript as supersede_transcript

HEADER = ('Entries marked "to re-check" cite a file that changed since they were checked, or a fact or tool that '
          "left\ndelivery. Before relying on one, check it: `val3 confirm` if it holds, `val3 save` with \"supersedes\" "
          "if it\nchanged, `val3 withdraw` if it no longer applies (`val3 confirm --help`).")
CHANGED = "subscribers:\n  - billing\nowner: billing\n"


def admit(env, title):
    """Apply the dry run's manifest: the item titled `title`, its quotes verified, is admitted as entry #1."""
    assert dry_run(env).returncode == 0
    result = env.run("import", "--apply", str(env.root / "manifest.json"))
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT d.entry_id FROM {env.vault}.review_item i JOIN {env.vault}.review_decision d "
                   "ON d.review_id = i.id WHERE i.payload->>'title' = %s", (title,)) == [(1,)]


def imported(env):
    """An adopter whose unit was imported and admitted: entry #1 quotes `subscribers: {}` of config/routing.yaml;
    the note, without evidence, waits for a person (a vault created before ADR 0012)."""
    adopter(env)
    assert env.run("vault", "create", env.vault).returncode == 0
    before_0012(env)
    answer(env, 0, unit_candidate())
    answer(env, 1, note_candidate(evidence=[]))
    admit(env, unit_candidate()["title"])
    return f"{env.vault}#1"


def unit_citing(env, files, evidence):
    """An adopter whose one unit cites `files` (path → text) in its provenance; its candidate quotes `evidence`."""
    init(env)
    for path, text in files.items():
        write(env, path, text)
    first = commit(env, "code")
    provenance = "".join(f"  - {path} at {first}\n" for path in files)
    write(env, "knowledge/kb-0001-routing.md", f"---\nid: kb-0001\nevidence: verifiable\nprovenance:\n{provenance}"
                                               "---\n\n# Routing ships no subscribers\n\nNo subscribers.\n")
    commit(env, "knowledge")
    assert env.run("vault", "create", env.vault).returncode == 0
    answer(env, 0, unit_candidate(evidence=[{"path": path, "quote": quote} for path, quote in evidence]))
    admit(env, unit_candidate()["title"])
    return f"{env.vault}#1"


def index(env, **environ):
    return context(start(env, **environ), "SessionStart")


def shown(env, ref, cwd=None, environ=None):
    result = env.run("show", ref, cwd=cwd, environ=environ)
    assert result.returncode == 0, result.stderr
    return result.stdout


def assert_unmarked(env, ref, **environ):
    assert "to re-check" not in index(env, **environ)
    assert "TO RE-CHECK" not in shown(env, ref, environ=environ)


def test_an_entry_whose_quote_holds_is_not_marked(env):
    ref = imported(env)
    assert_unmarked(env, ref)


def test_a_changed_file_marks_the_entry(env):
    ref = imported(env)
    write(env, "config/routing.yaml", CHANGED)
    sha = commit(env)
    text = index(env)
    [line] = [line for line in text.splitlines() if line.startswith(f"- {ref} ")]
    assert line.endswith("Routing ships no subscribers · to re-check")
    assert "stored facts.\n\n" + HEADER + "\n\n" in text
    assert shown(env, ref).splitlines()[1] == f"TO RE-CHECK: config/routing.yaml changed at HEAD {sha[:12]}"


def test_a_deleted_file_marks_the_entry(env):
    ref = imported(env)
    git(env, "rm", "-q", "config/routing.yaml")
    sha = commit(env)
    assert shown(env, ref).splitlines()[1] == f"TO RE-CHECK: config/routing.yaml is not in HEAD {sha[:12]}"


def test_uncommitted_changes_do_not_mark(env):
    ref = imported(env)
    write(env, "config/routing.yaml", CHANGED)
    assert_unmarked(env, ref)


def test_a_commit_missing_from_the_repository_is_not_checked(env):
    ref = imported(env)
    other = env.root / "other"
    other.mkdir()
    subprocess.run([*GIT, "-C", str(other), "init", "-q", "-b", "main"], check=True)
    (other / "README.md").write_text("other\n")
    subprocess.run([*GIT, "-C", str(other), "add", "README.md"], check=True)
    subprocess.run([*GIT, "-C", str(other), "commit", "-q", "-m", "one"], check=True)
    shutil.copy(env.project / "val3.toml", other / "val3.toml")
    text = context(hook(env, "session-start", payload(env, "SessionStart", cwd=other, source="startup")),
                   "SessionStart")
    assert f"- {ref} " in text and "to re-check" not in text
    assert "TO RE-CHECK" not in shown(env, ref, cwd=other)


def test_a_linked_vault_entry_is_not_marked(env):
    ref = imported(env)
    clone = env.root / "clone"
    subprocess.run([*GIT, "clone", "-q", str(env.project), str(clone)], check=True)
    home = env.vault + "_clone"
    (clone / "val3.toml").write_text(f'service = "val3test"\n[vaults]\nhome = "{home}"\nlinked = ["{env.vault}"]\n')
    assert env.run("vault", "create", home, cwd=clone).returncode == 0
    (clone / "config" / "routing.yaml").write_text(CHANGED)
    subprocess.run([*GIT, "-C", str(clone), "commit", "-q", "-am", "change"], check=True)
    text = context(hook(env, "session-start", payload(env, "SessionStart", cwd=clone, source="startup")),
                   "SessionStart")
    assert f"- {ref} " in text and "to re-check" not in text
    status = env.run("status", cwd=clone)
    assert status.returncode == 0, status.stderr
    assert "\nto re-check: 0\n" in status.stdout
    assert "TO RE-CHECK" not in shown(env, ref, cwd=clone)


def test_outside_a_git_repository_there_are_no_marks(env):
    ref = imported(env)
    shutil.rmtree(env.project / ".git")
    text = index(env)
    assert f"- {ref} " in text and "to re-check" not in text


def test_without_git_on_path_there_are_no_marks(env):
    ref = imported(env)
    write(env, "config/routing.yaml", CHANGED)
    commit(env)
    empty = env.root / "empty"
    empty.mkdir()
    text = index(env, PATH=str(empty))
    assert f"- {ref} " in text and "to re-check" not in text
    assert "TO RE-CHECK" not in shown(env, ref, environ={"PATH": str(empty)})


def test_a_hanging_git_leaves_the_index_unmarked(env):
    ref = imported(env)
    write(env, "config/routing.yaml", CHANGED)
    commit(env)
    hanging = env.root / "hanging"
    hanging.mkdir()
    (hanging / "git").write_text("#!/bin/sh\nsleep 30\n")
    os.chmod(hanging / "git", 0o755)
    began = time.monotonic()
    text = index(env, PATH=f"{hanging}:{env.environ['PATH']}")
    assert time.monotonic() - began < 10
    assert f"- {ref} " in text and "to re-check" not in text


def test_every_current_quote_is_checked(env):
    ref = unit_citing(env, {"config/routing.yaml": ROUTING, "docs/adr/0001.md": "route every message by its "
                            "thread owner\n"},
                      [("config/routing.yaml", "subscribers: {}"),
                       ("docs/adr/0001.md", "route every message by its thread owner")])
    assert_unmarked(env, ref)
    write(env, "docs/adr/0001.md", "route by topic\n")
    sha = commit(env)
    assert "Routing ships no subscribers · to re-check" in index(env)
    assert [line for line in shown(env, ref).splitlines() if line.startswith("TO RE-CHECK")] == [
        f"TO RE-CHECK: docs/adr/0001.md changed at HEAD {sha[:12]}"]
    write(env, "docs/adr/0001.md", "route every message by its thread owner\n")
    write(env, "config/routing.yaml", CHANGED)
    sha = commit(env)
    assert [line for line in shown(env, ref).splitlines() if line.startswith("TO RE-CHECK")] == [
        f"TO RE-CHECK: config/routing.yaml changed at HEAD {sha[:12]}"]


def test_a_path_with_a_line_break_is_checked(env):
    path = "odd\nWITHDRAWN (wrong) name.yaml"
    init(env)
    write(env, path, ROUTING)
    write(env, "memory/odd.md", "---\nname: odd\ndescription: odd\nmetadata:\n  type: project\n---\n\n"
                                f"Routing ships no subscribers (see {path} here).\n")
    commit(env, "code")
    assert env.run("vault", "create", env.vault).returncode == 0
    answer(env, 0, unit_candidate(evidence=[{"path": path, "quote": "subscribers: {}"}]))
    admit(env, unit_candidate()["title"])
    ref = f"{env.vault}#1"
    assert_unmarked(env, ref)
    write(env, path, CHANGED)
    sha = commit(env)
    assert "Routing ships no subscribers · to re-check" in index(env)
    lines = shown(env, ref).splitlines()
    assert lines[1] == f"TO RE-CHECK: odd\\x0aWITHDRAWN (wrong) name.yaml changed at HEAD {sha[:12]}"
    assert not any(line.startswith("WITHDRAWN") for line in lines)


def test_a_root_ending_in_a_space_is_checked(env):
    ref = imported(env)
    spaced = env.root / "project "
    subprocess.run([*GIT, "clone", "-q", str(env.project), str(spaced)], check=True)
    shutil.copy(env.project / "val3.toml", spaced / "val3.toml")
    (spaced / "config" / "routing.yaml").write_text(CHANGED)
    subprocess.run([*GIT, "-C", str(spaced), "commit", "-q", "-am", "change"], check=True)
    text = context(hook(env, "session-start", payload(env, "SessionStart", cwd=spaced, source="startup")),
                   "SessionStart")
    assert "Routing ships no subscribers · to re-check" in text
    assert "TO RE-CHECK: config/routing.yaml changed at HEAD" in shown(env, ref, cwd=spaced)


def test_a_binary_file_at_head_is_marked(env):
    ref = imported(env)
    (env.project / "config" / "routing.yaml").write_bytes(b"\x00\xff\xfe")
    sha = commit(env)
    assert "Routing ships no subscribers · to re-check" in index(env)
    assert shown(env, ref).splitlines()[1] == f"TO RE-CHECK: config/routing.yaml changed at HEAD {sha[:12]}"


def test_paths_with_spaces_and_accents_are_checked(env):
    path = "config/rúta de prueba.yaml"
    ref = unit_citing(env, {path: ROUTING}, [(path, "subscribers: {}")])
    assert_unmarked(env, ref)
    write(env, path, CHANGED)
    sha = commit(env)
    assert "Routing ships no subscribers · to re-check" in index(env)
    assert shown(env, ref).splitlines()[1] == f"TO RE-CHECK: {path} changed at HEAD {sha[:12]}"


def test_a_marked_entry_delivered_for_a_prompt_carries_the_suffix(env):
    ref = imported(env)
    block = f"- {ref} [fact] Routing ships no subscribers"
    assert f"\n{block}: As of" in context(ask(env, "routing subscribers"), "UserPromptSubmit")
    write(env, "config/routing.yaml", CHANGED)
    commit(env)
    assert f"\n{block} · to re-check: As of" in context(ask(env, "routing subscribers"), "UserPromptSubmit")


def status_line(env):
    result = env.run("status")
    assert result.returncode == 0, result.stderr
    [line] = [line for line in result.stdout.splitlines() if line.startswith("to re-check")]
    lines = result.stdout.splitlines()
    assert lines[lines.index(line) - 1].startswith(f"home vault {env.vault}:")
    return line


def test_status_counts_entries_to_re_check(env):
    ref = imported(env)
    assert status_line(env) == "to re-check: 0"
    write(env, "config/routing.yaml", CHANGED)
    commit(env)
    assert status_line(env) == f"to re-check: 1 ({ref})"
    v = env.vault
    for n in range(2, 13):  # eleven more approved entries quoting the same file at the same commit
        env.sql(f"INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
                f"SELECT kind, title || ' ' || %s, statement, content_hash || %s, origin_project, origin_machine "
                f"FROM {v}.entry WHERE id = 1", (str(n), str(n)))
        env.sql(f"INSERT INTO {v}.evidence (entry_id, kind, reproduction, quote, created_at) "
                f"SELECT %s, kind, reproduction, quote, created_at FROM {v}.evidence WHERE entry_id = 1", (n,))
    refs = ", ".join(f"{v}#{n}" for n in range(1, 11))
    assert status_line(env) == f"to re-check: 12 ({refs}, …)"


def test_status_outside_a_git_repository(env):
    imported(env)
    shutil.rmtree(env.project / ".git")
    assert status_line(env) == f"to re-check: not checked ({env.project} is not the root of a git repository)"


def test_status_when_git_fails_inside_the_check(env):
    imported(env)
    wrapper = env.root / "failing"
    wrapper.mkdir()
    (wrapper / "git").write_text(f'#!/bin/sh\nfor a; do [ "$a" = rev-parse ] && exec {shutil.which("git")} "$@"; done\n'
                                 "exit 1\n")
    os.chmod(wrapper / "git", 0o755)
    result = env.run("status", environ={"PATH": f"{wrapper}:{env.environ['PATH']}"})
    assert result.returncode == 0, result.stderr
    assert "\nto re-check: not checked (git failed or took longer than 3 s)\n" in result.stdout


def test_status_with_a_hanging_git_answers_within_the_budget(env):
    imported(env)
    hanging = env.root / "hanging"
    hanging.mkdir()
    (hanging / "git").write_text("#!/bin/sh\nsleep 30\n")
    os.chmod(hanging / "git", 0o755)
    began = time.monotonic()
    result = env.run("status", environ={"PATH": f"{hanging}:{env.environ['PATH']}"})
    assert time.monotonic() - began < 10
    assert result.returncode == 0, result.stderr
    [line] = [line for line in result.stdout.splitlines() if line.startswith("to re-check")]
    assert line in (f"to re-check: not checked ({env.project} is not the root of a git repository)",
                    "to re-check: not checked (git failed or took longer than 3 s)")


def cited(env):
    """Fact #1, tool #2 and recipe #3, whose first step cites the fact and whose second step runs the tool;
    the project is not a git repository; the vault predates ADR 0012, so the recipe is approved. Returns the
    transcript."""
    old_vault(env)
    t = supersede_transcript(env)
    assert save(env, fact(), t).returncode == 0
    assert env.run("tool", "add", "invoice-cli", "--type", "cli", "--description", "Counts invoices.").returncode == 0
    steps = recipe(f"{env.vault}#1")
    steps["recipe"]["steps"][1]["tool"] = f"{env.vault}#2"
    assert save(env, steps, t).returncode == 0
    assert env.run("review", "approve", "1").returncode == 0
    return t


def recipe_line(env):
    [line] = [line for line in index(env).splitlines() if line.startswith(f"- {env.vault}#3 ")]
    return line


def re_check_lines(env, ref):
    return [line for line in shown(env, ref).splitlines() if line.startswith("TO RE-CHECK")]


def confirm_recipe(env, t):
    result = env.run("confirm", f"{env.vault}#3", "--transcript", str(t), input=json.dumps(
        [{"kind": "query", "reproduction": "SELECT count(*) AS invoice_count FROM invoice", "quote": QUOTE}]))
    assert result.returncode == 0, result.stderr


def test_a_recipe_citing_a_superseded_fact_is_marked(env):
    t = cited(env)
    assert not recipe_line(env).endswith("to re-check")
    assert save(env, newer(env), t).stdout.strip() == f"admitted {env.vault}#4"
    assert recipe_line(env).endswith(" · to re-check")
    assert re_check_lines(env, f"{env.vault}#3") == [
        f"TO RE-CHECK: cites {env.vault}#1, which is superseded by {env.vault}#4"]
    confirm_recipe(env, t)
    assert not recipe_line(env).endswith("to re-check")
    assert re_check_lines(env, f"{env.vault}#3") == []



@pytest.mark.parametrize("cited_id", [1, 2])
def test_a_recipe_citing_either_replaced_fact_is_marked(env, cited_id):
    v = env.vault
    old_vault(env)  # the recipe is approved on the review list
    t = supersede_transcript(env)
    assert save(env, fact(), t).returncode == 0
    assert save(env, second(env), t).returncode == 0
    assert save(env, recipe(f"{v}#{cited_id}"), t).returncode == 0
    assert env.run("review", "approve", "1").returncode == 0
    assert save(env, newer(env, supersedes=[f"{v}#1", f"{v}#2"]), t).stdout.strip() == f"admitted {v}#4"
    assert re_check_lines(env, f"{v}#3") == [f"TO RE-CHECK: cites {v}#{cited_id}, which is superseded by {v}#4"]

def test_a_recipe_running_a_withdrawn_tool_is_marked(env):
    t = cited(env)
    assert env.run("withdraw", f"{env.vault}#2", "--obsolete", "--reason", "Replaced.", tty=True).returncode == 0
    assert recipe_line(env).endswith(" · to re-check")
    assert re_check_lines(env, f"{env.vault}#3") == [f"TO RE-CHECK: cites {env.vault}#2, which is withdrawn"]
    confirm_recipe(env, t)
    assert re_check_lines(env, f"{env.vault}#3") == []


def test_recipe_marks_need_no_git(env):
    t = cited(env)
    assert not (env.project / ".git").exists()
    assert save(env, newer(env), t).returncode == 0
    assert recipe_line(env).endswith(" · to re-check")
    assert re_check_lines(env, f"{env.vault}#3") == [
        f"TO RE-CHECK: cites {env.vault}#1, which is superseded by {env.vault}#4"]
    assert status_line(env) == (f"to re-check: 1 ({env.vault}#3) · file evidence not checked "
                                f"({env.project} is not the root of a git repository)")


def test_a_confirmation_before_a_supersession_does_not_clear_the_mark(env):
    t = cited(env)
    confirm_recipe(env, t)
    assert save(env, newer(env), t).returncode == 0
    assert recipe_line(env).endswith(" · to re-check")


def test_a_confirmation_after_a_supersession_stays_clear_when_the_fact_is_withdrawn_later(env):
    t = cited(env)
    assert save(env, newer(env), t).returncode == 0                   # #4 supersedes the cited fact #1
    confirm_recipe(env, t)
    assert env.run("withdraw", f"{env.vault}#1", "--obsolete", "--reason", "Replaced.", tty=True).returncode == 0
    assert not recipe_line(env).endswith("to re-check")
    assert re_check_lines(env, f"{env.vault}#3") == []
