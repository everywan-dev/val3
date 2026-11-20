"""ADR 0012: no decision waits for a person. A vault without the attestation table keeps the review queue; `val3 vault
upgrade` is the switch."""
import getpass
import json

import psycopg
import pytest

from helpers import (OUTPUT, TOOL, database, fact, legacy, make_vault, mirrored, old_vault, save, source,
                     with_entry)

UPGRADER = "val3 upgrade"


def upgrade(env, *args):
    return env.run("vault", "upgrade", env.vault, *args)


def decisions(env):
    return env.sql(f"SELECT review_id, decision, decided_by, entry_id FROM {env.vault}.review_decision "
                   "ORDER BY review_id")


def attested(env):
    return env.sql(f"SELECT entry_id, attested_by, how FROM {env.vault}.attestation ORDER BY id")


def has_attestation(env):
    return env.sql("SELECT to_regclass(%s) IS NOT NULL", (f"{env.vault}.attestation",))[0][0]


def test_a_new_vault_has_the_append_only_attestation_table_and_retired_decisions(env):
    make_vault(env)
    entry = env.sql(f"INSERT INTO {env.vault}.entry (kind, title, statement, content_hash, origin_project, "
                    "origin_machine) VALUES ('tool', 't', 's', 'h', 'p', 'm') RETURNING id")[0][0]
    env.sql(f"INSERT INTO {env.vault}.attestation (entry_id, attested_by, how) VALUES (%s, 'owner', 'test')",
            (entry,))
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        env.sql(f"UPDATE {env.vault}.attestation SET how = 'x'")
    review = env.sql(f"INSERT INTO {env.vault}.review_item (reason, payload, content_hash, origin_project, "
                     "origin_machine) VALUES ('tool', '{}', 'x', 'p', 'm') RETURNING id")[0][0]
    env.sql(f"INSERT INTO {env.vault}.review_decision (review_id, decision, decided_by) VALUES (%s, 'retired', 'u')",
            (review,))


def history(env):
    """An old vault holding an entry with evidence, one without, and one undecided item of each historical reason."""
    old_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), transcript).stdout == f"admitted {env.vault}#1\n"
    assert env.run("tool", "add", "code-graph", "--type", "cli", "--description", "Answers overrides.").stdout == \
        f"admitted {env.vault}#2\n"
    queued = save(env, TOOL | {"title": "graph-cli"}, transcript)
    assert queued.stdout == "queued for review: review #1 (no-evidence)\n", queued.stderr
    legacy(env, TOOL | {"title": "old-tool"}, reason="tool")
    legacy(env, fact(title="The user says so", evidence=[]))
    legacy(env, {"title": "superseded #1"}, reason="supersession", entry_id=1)
    legacy(env, {"title": "withdrawn #2"}, reason="withdrawn", entry_id=2)
    legacy(env, {"title": "recipe cites #1"}, reason="recipe-cites-superseded", entry_id=1)
    legacy(env, {"title": f"withdraw {env.vault}#1 (wrong): never true", "ref": f"{env.vault}#1", "cause": "wrong",
                 "reason": "never true"}, reason="withdrawal", entry_id=1)
    legacy(env, fact(title="Imported fact", evidence=[], **{"import": {"batch": "0123456789abcdef"}}), reason="import")


def test_dry_run_lists_every_change_and_changes_nothing(env):
    history(env)
    result = upgrade(env, "--dry-run")
    assert result.returncode == 0, result.stderr
    v = env.vault
    assert result.stdout == (
        "create table attestation\n"
        "widen check review_decision.review_decision_decision_check\n"
        f"attest {v}#2 [tool] code-graph (no evidence, admitted before ADR 0012)\n"
        "retire review #1 (no-evidence): [tool] graph-cli\n"
        "retire review #2 (tool): [tool] old-tool\n"
        "retire review #3 (user-confirmation): [fact] The user says so\n"
        "approve notice review #4 (supersession): superseded #1\n"
        "approve notice review #5 (withdrawn): withdrawn #2\n"
        "approve notice review #6 (recipe-cites-superseded): recipe cites #1\n"
        f"retire review #7 (withdrawal): withdraw {v}#1 (wrong): never true\n"
        "retire review #8 (import): [fact] Imported fact (batch 0123456789abcdef)\n"
        f"dry run: vault {v} not changed\n")
    assert not has_attestation(env) and decisions(env) == []


def test_upgrade_attests_entries_without_evidence_and_closes_every_undecided_item(env):
    history(env)
    result = upgrade(env)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"attest {env.vault}#2 [tool] code-graph (no evidence, admitted before ADR 0012)"
    assert lines[-1] == f"upgraded vault {env.vault}" and len(lines) == 10
    assert attested(env) == [(2, UPGRADER, "before ADR 0012")]
    assert decisions(env) == [(1, "retired", UPGRADER, None), (2, "retired", UPGRADER, None),
                              (3, "retired", UPGRADER, None), (4, "approved", UPGRADER, None),
                              (5, "approved", UPGRADER, None), (6, "approved", UPGRADER, None),
                              (7, "retired", UPGRADER, None), (8, "retired", UPGRADER, None)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(2,)]  # nothing admitted
    assert env.sql(f"SELECT count(*) FROM {env.vault}.withdrawal") == [(0,)]  # nothing withdrawn
    again = upgrade(env)
    assert (again.returncode, again.stdout) == (0, f"vault {env.vault} is up to date\n")
    assert len(attested(env)) == 1 and len(decisions(env)) == 8


def test_upgrade_waits_for_the_writer_lock(env):
    old_vault(env)
    dsn = f"host=127.0.0.1 port={env.port} user=postgres dbname=val3"
    with psycopg.connect(dsn) as holder:
        holder.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"val3 writer {env.vault}",))
        result = env.run("vault", "upgrade", env.vault, environ={"PGOPTIONS": "-c lock_timeout=1000"})
    assert result.returncode == 1 and "lock timeout" in result.stderr, result.stderr
    assert not has_attestation(env)


def test_a_retired_item_frees_its_hash(env):
    old_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, TOOL, transcript).stdout == "queued for review: review #1 (no-evidence)\n"
    assert upgrade(env).returncode == 0
    again = save(env, TOOL, transcript)  # the vault is upgraded: nothing is queued any more
    assert again.stdout == f"admitted unverified {env.vault}#1\n", again.stderr


def test_upgrade_refuses_a_mirror(env):
    source(env)
    mirrored(env)
    result = upgrade(env)
    assert result.returncode == 1
    assert "is a mirror: refresh it from its source" in result.stderr


def test_the_dump_of_a_vault_not_upgraded_is_refused(env):
    old_vault(env)
    result = env.run("vault", "dump", env.vault, "--file", str(env.root / "d"))
    assert result.returncode == 1
    assert f"vault {env.vault} predates this version: its owner runs `val3 vault upgrade {env.vault}`" in result.stderr


def test_a_dump_of_an_older_catalogue_is_refused(env):
    root = database(env, "val3b")
    with_entry(env, env.vault, "val3b", root)
    env.config()
    path = env.root / "d"
    assert env.run("vault", "dump", env.vault, "--file", str(path), "--service", "val3b").returncode == 0
    lines = path.read_bytes().split(b"\n", 1)
    header = json.loads(lines[0])
    header["catalogue"] = 2
    header["tables"].pop("attestation")
    path.write_bytes(json.dumps(header).encode() + b"\n" + lines[1])
    result = env.run("vault", "mirror", env.vault, "--from-file", str(path))
    assert result.returncode == 1
    assert "catalogue version 2 and this side 3: both ends run the same val3 version" in result.stderr


def test_a_retired_import_item_is_not_already_imported(env):
    from test_import import apply, ready_manifest
    path = ready_manifest(env)  # a vault created before ADR 0012
    assert apply(env, path).stdout.strip().endswith("1 admitted, 1 queued, 0 already queued, 0 previously rejected, "
                                                    "0 duplicate, 0 already imported, 0 refused")
    assert upgrade(env).returncode == 0  # the queued note is retired
    again = apply(env, path)
    assert again.returncode == 0, again.stderr
    assert again.stdout.strip().endswith("1 admitted, 0 queued, 0 already queued, 0 previously rejected, "
                                         "0 duplicate, 1 already imported, 0 refused")


def test_a_mirror_copies_attestations(env):
    root = source(env)
    assert env.run("tool", "add", "code-graph", "--type", "cli", "--description", "Answers overrides.",
                   cwd=root).returncode == 0
    mirrored(env)
    assert [row[:2] for row in attested(env)] == [(2, getpass.getuser())]


RETIRED = "the review list is retired (ADR 0012): nothing waits for a person"


def test_review_lists_history_read_only_once_the_vault_is_upgraded(env):
    history(env)
    assert upgrade(env).returncode == 0
    listed = env.run("review")
    assert listed.returncode == 0, listed.stderr
    lines = listed.stdout.splitlines()
    assert lines[0] == f"{RETIRED}; history of vault {env.vault}:"
    assert lines[1].startswith("review #1  no-evidence               graph-cli  (project, ")
    assert lines[1].endswith(") · retired by val3 upgrade")
    assert lines[4].endswith(") · approved by val3 upgrade") and len(lines) == 9
    assert env.run("review", "show", "1").stdout.startswith("review #1 · no-evidence · project · ")
    for args in (("approve", "1"), ("reject", "2"), ("--tty",)):
        refused = env.run("review", *args)
        assert (refused.returncode, refused.stderr) == (
            1, f"val3: error: {RETIRED}: `val3 review` lists the history of vault {env.vault}\n"), args


def test_review_of_an_upgraded_vault_without_items(env):
    make_vault(env)
    assert env.run("review").stdout == f"{RETIRED}; history of vault {env.vault}:\nno review items\n"


def test_status_has_no_pending_line_once_upgraded(env):
    make_vault(env)
    line = env.run("status").stdout.splitlines()[1]
    assert line.startswith(f"home vault {env.vault}: 0 entries: 0 with evidence, 0 attested, 0 unverified · ")
    assert "pending review" not in line


def test_the_upgrade_keeps_the_roles_privileges_on_attestation(env):
    old_vault(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    for role, mode in ((env.reader, "--read"), (env.writer, "--write")):
        assert env.run("vault", "grant", env.vault, role, mode).returncode == 0
    assert upgrade(env).returncode == 0
    entry = env.sql(f"INSERT INTO {env.vault}.entry (kind, title, statement, content_hash, origin_project, "
                    "origin_machine) VALUES ('tool', 't', 's', 'h', 'p', 'm') RETURNING id", user=env.writer)[0][0]
    insert = f"INSERT INTO {env.vault}.attestation (entry_id, attested_by, how) VALUES (%s, 'u', 'test')"
    env.sql(insert, (entry,), user=env.writer)
    assert env.sql(f"SELECT count(*) FROM {env.vault}.attestation", user=env.reader) == [(1,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        env.sql(insert, (entry,), user=env.reader)


def test_the_decision_check_of_an_old_vault_has_the_name_the_upgrade_widens(env):
    """vault.sql before ADR 0012 left the check unnamed: PostgreSQL names it review_decision_decision_check."""
    schema = env.vault + "_old"
    env.sql(f"CREATE SCHEMA {schema}")
    env.sql(f"CREATE TABLE {schema}.review_decision (decision text NOT NULL CHECK (decision IN ('approved', "
            "'rejected')))")
    assert env.sql("SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'c'",
                   (f"{schema}.review_decision",)) == [("review_decision_decision_check",)]


def test_a_mirror_without_attestation_is_refreshed_from_an_upgraded_source(env):
    root = source(env)
    mirrored(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    assert env.run("vault", "grant", env.vault, env.reader, "--read").returncode == 0
    env.sql(f"DROP TABLE {env.vault}.attestation")  # a mirror loaded before ADR 0012
    assert env.run("tool", "add", "code-graph", "--type", "cli", "--description", "Answers overrides.",
                   cwd=root).returncode == 0
    mirrored(env)
    assert env.sql(f"SELECT entry_id FROM {env.vault}.attestation", user=env.reader) == [(2,)]
