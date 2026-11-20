"""Specialized vaults (design 4.1): one project writes its home vault and its specialized vaults; a project that
only links one reads it."""
import json

import pytest

from helpers import OUTPUT, before_0012, configure, fact, recipe, save, setup
from test_confirm import OWNER
from test_hooks import ask, context
from test_import import ROUTING, commit, git, write
from test_pending import capture, log
from test_recheck import index
from test_settle import TWIN

DOWN = "http://127.0.0.1:1"
RESOLD = "subscribers: {}\nowner: sales\n"


def admit(env, vault, candidate, transcript):
    result = save(env, candidate, transcript, "--vault", vault)
    assert result.returncode == 0 and result.stdout.startswith(f"admitted {vault}#"), result.stdout + result.stderr
    return result


def count(env, vault, table="entry"):
    return env.sql(f"SELECT count(*) FROM {vault}.{table}")[0][0]


def read_only(vault):
    return f"vault {vault} is read only here: write to it from its own project"


def test_save_with_vault_writes_the_specialized_vault(env):
    home, special = setup(env)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]), "--vault", special)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"admitted {special}#1\n"
    assert (count(env, special), count(env, home)) == (1, 0)


def test_session_index_lists_both_vaults(env):
    home, special = setup(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    admit(env, special, fact(), t)
    text = index(env)
    assert f"- {home}#1 [fact]" in text and f"- {special}#1 [fact]" in text


def test_settle_and_withdraw_in_the_specialized_vault(env, embedder):
    _, special = setup(env, embedding={"url": embedder.url})
    t = env.transcript(tool_outputs=[OUTPUT])
    admit(env, special, fact(), t)
    admit(env, special, TWIN, t)
    lines = env.run("status").stdout.splitlines()
    assert any(line.startswith("to settle: 1 (") and f"{special}#2" in line for line in lines), lines
    assert f"specialized vault {special}: 2 entries: 2 with evidence, 0 attested, 0 unverified" in lines
    result = env.run("settle", f"{special}#2", f"{special}#1", "--keep")
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT entry_id, other_id, outcome FROM {special}.settlement") == [(2, 1, "keep")]
    result = env.run("withdraw", f"{special}#1", "--obsolete", "--reason", "Replaced by the ledger.",
                     tty=True)  # a person at a terminal (ADR 0012 §6)
    assert (result.returncode, result.stdout.strip()) == (0, f"withdrawn {special}#1"), result.stderr
    assert env.sql(f"SELECT entry_id, cause FROM {special}.withdrawal") == [(1, "obsolete")]


def test_linked_vault_is_read_only_here(env):
    _, special = setup(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    admit(env, special, fact(), t)
    admit(env, special, fact(title="Other", statement="Other statement."), t)
    other = env.root / "other"
    other.mkdir()
    (other / "val3.toml").write_text(f'service = "val3test"\n[vaults]\nhome = "{env.vault}_o"\n'
                                     f'linked = ["{special}"]\n')
    runs = [env.run("confirm", f"{special}#1", "--transcript", str(t), cwd=other,
                    input=json.dumps(fact()["evidence"])),
            env.run("settle", f"{special}#2", f"{special}#1", "--keep", cwd=other),
            env.run("withdraw", f"{special}#1", "--obsolete", "--reason", "Old.", cwd=other),
            env.run("save", "--vault", special, cwd=other, input=json.dumps(fact()))]
    for result in runs:
        assert result.returncode == 1 and read_only(special) in result.stderr, result.stderr
    assert (count(env, special, "evidence"), count(env, special, "withdrawal")) == (2, 0)


def test_unknown_vault_option(env):
    setup(env)
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]), "--vault", "nope")
    assert result.returncode == 1
    assert f"vault nope is not declared in {env.project / 'val3.toml'}" in result.stderr


def test_status_names_a_specialized_vault_that_does_not_exist(env):
    home, _ = setup(env)
    configure(env, [f"{home}_s", f"{home}_x"])
    result = env.run("status")
    assert result.returncode == 1
    assert f"specialized vault {home}_x: does not exist (`val3 vault create {home}_x`)" in result.stdout.splitlines()
    assert f"home vault {home}: does not exist" not in result.stdout


def test_toml_rejects_a_vault_twice(env):
    home, special = setup(env)
    for specialized, linked in (([home], ()), ([special], [special]), ([special, special], ())):
        configure(env, specialized, linked)
        for args in (("status",), ("recall", "invoices")):
            result = env.run(*args)
            assert result.returncode == 1 and "appears twice" in result.stderr, (specialized, linked, result.stderr)


def test_every_writing_command_takes_vault(env, embedder):
    home, special = setup(env, embedding={"url": DOWN})
    before_0012(env, special)  # the recipe is queued and approved
    t = env.transcript(tool_outputs=[OUTPUT])
    admit(env, special, fact(indexes=["facturacion"]), t)
    result = env.run("confirm", f"{special}#1", "--transcript", str(t), input=json.dumps(fact()["evidence"]))
    assert result.returncode == 0 and result.stdout.startswith(f"confirmed {special}#1"), result.stderr
    assert (count(env, special, "evidence"), count(env, home, "evidence")) == (2, 0)
    result = env.run("tool", "add", "code-graph", "--type", "cli", "--description", "d", "--index", "facturas",
                     "--vault", special)
    assert (result.returncode, result.stdout.strip()) == (0, f"admitted {special}#2"), result.stderr
    result = save(env, recipe(f"{special}#1"), t, "--vault", special)
    assert result.stdout.strip() == "queued for review: review #1 (no-evidence)", result.stderr
    assert env.run("review").stdout.strip().endswith(f"history of vault {home}:\nno review items")  # upgraded
    listing = env.run("review", "--vault", special).stdout
    assert "#1" in listing and "Count the invoices of a month" in listing
    result = env.run("review", "--vault", special, "approve", "1", "--by", "tester")
    assert result.stdout.strip() == f"approved review #1: admitted {special}#3", result.stderr
    assert env.run("index", "merge", "facturacion", "facturas").returncode == 1  # the home vault has no such index
    result = env.run("index", "merge", "facturacion", "facturas", "--vault", special)
    assert result.stdout.strip() == "merged facturacion into facturas (1 entries)", result.stderr
    assert count(env, special, "index_alias") == 1
    assert save(env, fact(title="Home fact"), t).returncode == 0
    configure(env, [special], embedding={"url": embedder.url})
    result = env.run("embed", "--vault", special)
    assert (result.returncode, result.stdout.strip()) == (0, "embedded 3 entries"), result.stderr
    assert (count(env, special, "embedding"), count(env, home, "embedding")) == (3, 0)


def test_marks_are_per_vault(env):
    home, special = setup(env)
    git(env, "init", "-q", "-b", "main")
    write(env, ".git/info/exclude", "val3.toml\n")
    write(env, "config/routing.yaml", ROUTING)
    commit(env, "code")
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    admit(env, special, fact(), t)
    assert env.run("confirm", f"{special}#1", input=json.dumps(OWNER)).returncode == 0
    write(env, "config/routing.yaml", RESOLD)
    commit(env)
    text = index(env)
    assert f"- {special}#1 [fact] April has 42 invoices · to re-check" in text
    assert f"- {home}#1 [fact] April has 42 invoices\n" in text + "\n"
    listing = env.run("index", "billing").stdout.splitlines()
    assert listing == [f"- {home}#1 [fact] April has 42 invoices",
                       f"- {special}#1 [fact] April has 42 invoices · to re-check"]
    assert "TO RE-CHECK" in env.run("show", f"{special}#1").stdout
    assert "TO RE-CHECK" not in env.run("show", f"{home}#1").stdout
    prompt = context(ask(env, "How many invoices are there for April?"), "UserPromptSubmit")
    assert f"- {special}#1 [fact] April has 42 invoices · to re-check:" in prompt
    assert f"- {home}#1 [fact] April has 42 invoices:" in prompt


def test_create_specialized_edits_the_toml(env):
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    special = f"{env.vault}_s"
    result = env.run("vault", "create", special, "--specialized", "--description", "Billing difficulties.")
    assert result.returncode == 0, result.stderr
    assert result.stdout == f'created vault {special}\nval3.toml: specialized = ["{special}"]\n'
    status = env.run("status")
    assert f"specialized vault {special}: 0 entries: 0 with evidence, 0 attested, 0 unverified" in \
        status.stdout.splitlines(), status.stdout
    listed = env.run("vault", "list").stdout
    assert any(line.startswith(f"{special} ") and f"parent {env.vault}" in line for line in listed.splitlines())


def test_create_specialized_with_owner_service_grants_the_project_role(env):
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    assert env.run("vault", "grant", env.vault, env.writer, "--write").returncode == 0
    env.config(service="val3writer")
    special = f"{env.vault}_s"
    result = env.run("vault", "create", special, "--specialized", "--description", "Billing difficulties.",
                     "--service", "val3test")
    assert result.returncode == 0, result.stderr
    admit(env, special, fact(), env.transcript(tool_outputs=[OUTPUT]))


def test_unusual_toml_is_left_alone_with_the_line_to_add(env):
    path = env.project / "val3.toml"
    path.write_text(f"service = 'val3test'\n[vaults]\nlinked = []\nhome = '{env.vault}'\nspecialized = [\n \"a\",\n]\n")
    before = path.read_bytes()
    special = f"{env.vault}_s"
    result = env.run("vault", "create", special, "--specialized", "--description", "Billing difficulties.")
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == before
    assert (f"val3: warning: val3.toml not changed; add under [vaults]: specialized = [\"a\", \"{special}\"]"
            in result.stderr), result.stderr
    assert env.sql("SELECT count(*) FROM information_schema.schemata WHERE schema_name = %s", (special,)) == [(1,)]


def test_specialized_needs_a_description_and_a_project(env, tmp_path):
    result = env.run("vault", "create", f"{env.vault}_s", "--specialized")
    assert (result.returncode, result.stderr) == (2, "val3: error: --specialized needs --description\n")
    result = env.run("vault", "create", f"{env.vault}_s", "--specialized", "--description", "D.",
                     "--service", "val3test", cwd=tmp_path)
    assert result.returncode == 2, result.stderr


def test_crlf_toml_keeps_its_bytes_and_line_endings(env):
    path = env.project / "val3.toml"
    before = f'service = "val3test"\r\n[vaults]\r\nhome = "{env.vault}"\r\nlinked = []\r\n'.encode()
    path.write_bytes(before)
    special = f"{env.vault}_s"
    result = env.run("vault", "create", special, "--specialized", "--description", "D.")
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == before.replace(b"linked", f'specialized = ["{special}"]\r\nlinked'.encode())


def test_specialized_parent_must_be_the_home_vault(env):
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    result = env.run("vault", "create", f"{env.vault}_x", "--specialized", "--description", "D.", "--parent", "other")
    assert (result.returncode, result.stderr) == (2, "val3: error: --specialized sets the parent to the home vault\n")
    result = env.run("vault", "create", f"{env.vault}_s", "--specialized", "--description", "D.",
                     "--parent", env.vault)
    assert result.returncode == 0, result.stderr


def test_commented_specialized_line_is_left_alone(env):
    path = env.project / "val3.toml"
    path.write_text(f'service = "val3test"\n[vaults]\nhome = "{env.vault}"\nspecialized = ["{env.vault}_a"]  # keep [x]\n')
    before = path.read_bytes()
    result = env.run("vault", "create", f"{env.vault}_b", "--specialized", "--description", "D.")
    assert result.returncode == 0, result.stderr
    assert path.read_bytes() == before
    assert f'add under [vaults]: specialized = ["{env.vault}_a", "{env.vault}_b"]' in result.stderr


def test_second_specialized_vault_is_appended(env):
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    a, b = f"{env.vault}_a", f"{env.vault}_b"
    for name in (a, b):
        result = env.run("vault", "create", name, "--specialized", "--description", "D.")
        assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(f'val3.toml: specialized = ["{a}", "{b}"]\n')
    lines = env.run("status").stdout.splitlines()
    assert all(any(line.startswith(f"specialized vault {n}:") for line in lines) for n in (a, b)), lines


def test_create_specialized_refuses_a_declared_vault(env):
    home, special = setup(env)
    linked = env.vault + "_l"
    configure(env, [special], linked=[linked])
    for name in (home, special, linked):
        result = env.run("vault", "create", name, "--specialized", "--description", "D.")
        assert (result.returncode, result.stderr) == (2, f"val3: error: {name} is already declared in val3.toml\n")
    assert count_schemas(env, linked) == 0


def count_schemas(env, name):
    return env.sql("SELECT count(*) FROM pg_namespace WHERE nspname = %s", (name,))[0][0]


@pytest.mark.parametrize("specialized", [False, True])
def test_capture_with_the_embedder_down_logs_no_embedded_count(env, specialized):
    _, special = setup(env, embedding={"url": DOWN})
    configure(env, [special] if specialized else [], embedding={"url": DOWN})
    env.respond([{"note": None, "candidate": fact()}])
    assert capture(env, env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    env.wait(lambda: any(line["outcome"] == "done" for line in log(env)))
    done = next(line for line in log(env) if line["outcome"] == "done")
    assert done["admitted"] == 1 and "embedding_error" in done and "embedded" not in done, done
