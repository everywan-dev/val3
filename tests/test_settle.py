"""`val3 settle` and the "to settle" marks (design S1-S4): the agent settles a pair of close entries."""
import getpass

from helpers import OUTPUT, embedding_vault, fact, save

TWIN = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.")
OTHER = fact(title="Ledger closes monthly", statement="Closing happens on day one of each month.")
OLD_SCHEMA = "DROP TABLE {v}.settlement, {v}.neighbour, {v}.neighbour_exempt"
AGENT = {"CLAUDECODE": "1"}


def admit(env, *candidates):
    t = env.transcript(tool_outputs=[OUTPUT])
    results = [save(env, candidate, t) for candidate in candidates]
    for result in results:
        assert result.returncode == 0 and result.stdout.startswith("admitted"), result.stdout + result.stderr
    return results


def pair(env, embedder):
    """#1 and its close twin #2."""
    embedding_vault(env, embedder.url)
    return admit(env, fact(), TWIN)[1]


def score(env):
    [(value,)] = env.sql(f"SELECT score FROM {env.vault}.neighbour")
    return f"{value:.2f}"


def settle(env, *args, environ=None):
    return env.run("settle", *args, environ=environ)


def listing(env):
    result = env.run("index", "billing")
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def settlements(env):
    return env.sql(f"SELECT entry_id, other_id, outcome, settled_by FROM {env.vault}.settlement")


def test_save_names_the_neighbour_on_stderr(env, embedder):
    v = env.vault
    result = pair(env, embedder)
    assert result.stdout == f"admitted {v}#2\n"
    assert result.stderr == f"val3: near {v}#1 ({score(env)}): val3 settle {v}#2 {v}#1 --replace | --drop | --keep\n"
    show = env.run("show", f"{v}#2").stdout.splitlines()
    assert show[1] == f"TO SETTLE: close to {v}#1 ({score(env)})"
    assert "TO SETTLE" not in env.run("show", f"{v}#1").stdout
    assert listing(env) == [f"- {v}#1 [fact] April has 42 invoices",
                            f"- {v}#2 [fact] April has 43 invoices · to settle (near {v}#1)"]


def test_replace_supersedes_the_older_entry(env, embedder):
    v = env.vault
    pair(env, embedder)
    result = settle(env, f"{v}#2", f"{v}#1", "--replace")
    assert (result.returncode, result.stdout) == (0, f"replaced {v}#1 by {v}#2\n"), result.stderr
    assert env.sql(f"SELECT old_id, new_id FROM {v}.supersession") == [(1, 2)]
    assert settlements(env) == [(2, 1, "replace", getpass.getuser())]
    assert env.sql(f"SELECT entry_id, detail->>'outcome' FROM {v}.event WHERE action = 'settled'") == [(2, "replace")]
    assert listing(env) == [f"- {v}#2 [fact] April has 43 invoices"]


def test_drop_withdraws_the_new_entry_as_a_duplicate(env, embedder):
    v = env.vault
    pair(env, embedder)
    result = settle(env, f"{v}#2", f"{v}#1", "--drop", environ={**AGENT, "VAL3_SESSION_ID": env.session})
    assert (result.returncode, result.stdout) == (0, f"dropped {v}#2 (duplicate of {v}#1)\n"), result.stderr
    by = f"agent {env.session}"
    assert env.sql(f"SELECT entry_id, cause, reason, withdrawn_by FROM {v}.withdrawal") == [
        (2, "duplicate", f"duplicate of {v}#1", by)]
    assert settlements(env) == [(2, 1, "drop", by)]
    assert env.sql(f"SELECT count(*) FROM {v}.event WHERE action = 'settled'") == [(1,)]
    assert listing(env) == [f"- {v}#1 [fact] April has 42 invoices"]


def test_keep_records_the_pair_and_the_mark_goes(env, embedder):
    v = env.vault
    pair(env, embedder)
    result = settle(env, f"{v}#2", f"{v}#1", "--keep", environ=AGENT)
    assert (result.returncode, result.stdout) == (0, f"kept {v}#2 and {v}#1\n"), result.stderr
    assert settlements(env) == [(2, 1, "keep", "agent -")]
    assert env.sql(f"SELECT count(*) FROM {v}.supersession") == [(0,)]
    assert env.sql(f"SELECT count(*) FROM {v}.withdrawal") == [(0,)]
    assert listing(env) == [f"- {v}#1 [fact] April has 42 invoices", f"- {v}#2 [fact] April has 43 invoices"]
    assert "TO SETTLE" not in env.run("show", f"{v}#2").stdout


def test_settle_refuses_a_pair_that_is_not_open(env, embedder):
    v = env.vault
    pair(env, embedder)
    for args in ((f"{v}#1", f"{v}#2", "--keep"), (f"{v}#2", f"{v}#9", "--keep")):
        result = settle(env, *args)
        assert result.returncode == 1 and result.stderr.startswith("val3: error: "), result.stderr
    assert settle(env, f"{v}#2", f"{v}#1", "--keep").returncode == 0
    again = settle(env, f"{v}#2", f"{v}#1", "--drop")
    assert again.returncode == 1 and "already settled" in again.stderr
    assert settlements(env) == [(2, 1, "keep", getpass.getuser())]
    assert settle(env, f"{v}#2", f"{v}#1").returncode == 2


def test_replace_refuses_when_the_old_entry_left_delivery(env, embedder):
    v = env.vault
    pair(env, embedder)
    assert env.run("withdraw", f"{v}#1", "--wrong", "--reason", "Miscounted.", tty=True).returncode == 0
    result = settle(env, f"{v}#2", f"{v}#1", "--replace")
    assert result.returncode == 1 and f"{v}#1 was withdrawn" in result.stderr, result.stderr
    assert env.sql(f"SELECT count(*) FROM {v}.supersession") == [(0,)]
    assert settlements(env) == []


def test_settle_refusals_keep_stored_text_on_one_line(env, embedder):
    v = env.vault
    pair(env, embedder)
    assert settle(env, f"{v}#2", f"{v}#1", "--keep", environ={**AGENT, "VAL3_SESSION_ID": "s\nok"}).returncode == 0
    again = settle(env, f"{v}#2", f"{v}#1", "--drop")
    assert again.returncode == 1 and again.stderr.count("\n") == 1 and "by agent s\\x0aok" in again.stderr, again.stderr
    admit(env, fact(title="April has 44 invoices", statement="The ERP holds 44 invoices dated April 2026."))
    # `val3 withdraw` folds the reason onto one line; a row written by another client may not
    env.sql(f"INSERT INTO {v}.withdrawal (entry_id, cause, reason, withdrawn_by) "
            "VALUES (3, 'wrong', %s, 'someone')", ("Miscounted.\nWITHDRAWN",))
    gone = settle(env, f"{v}#3", f"{v}#1", "--replace")
    assert gone.returncode == 1 and gone.stderr.count("\n") == 1 and "Miscounted.\\x0aWITHDRAWN" in gone.stderr, \
        gone.stderr


def test_replace_works_when_the_new_entry_already_supersedes_another(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    admit(env, OTHER, fact(), {**TWIN, "supersedes": f"{v}#1"})
    assert env.sql(f"SELECT entry_id, other_id FROM {v}.neighbour") == [(3, 2)]
    result = settle(env, f"{v}#3", f"{v}#2", "--replace")
    assert (result.returncode, result.stdout) == (0, f"replaced {v}#2 by {v}#3\n"), result.stderr
    assert env.sql(f"SELECT old_id, new_id FROM {v}.supersession ORDER BY old_id") == [(1, 3), (2, 3)]
    assert settlements(env) == [(3, 2, "replace", getpass.getuser())]


def test_settle_on_a_vault_without_the_tables_names_the_upgrade(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    env.sql(OLD_SCHEMA.format(v=v))
    second = admit(env, fact(), TWIN)[1]
    assert "near" not in second.stderr
    result = settle(env, f"{v}#2", f"{v}#1", "--keep")
    assert result.returncode == 1 and f"`val3 vault upgrade {v}`" in result.stderr, result.stderr
    assert env.run("show", f"{v}#2").returncode == 0
    assert listing(env) == [f"- {v}#1 [fact] April has 42 invoices", f"- {v}#2 [fact] April has 43 invoices"]
    assert "\nto settle: 0\n" in env.run("status").stdout


def test_settle_help_names_the_three_outcomes(env):
    lines = env.run("settle", "--help").stdout.splitlines()
    flags = [line.split()[0] for line in lines if line.startswith("  --")]
    assert flags == ["--replace", "--drop", "--keep"]
    assert not any(line.startswith(" " * 14) for line in lines)  # each outcome on one line


def test_replace_refuses_when_the_old_entry_was_superseded(env, embedder):
    v = env.vault
    pair(env, embedder)
    admit(env, {**OTHER, "supersedes": f"{v}#1"})
    before = env.sql(f"SELECT count(*) FROM {v}.event")
    result = settle(env, f"{v}#2", f"{v}#1", "--replace")
    assert result.returncode == 1 and f"{v}#1 was superseded by {v}#3" in result.stderr, result.stderr
    assert env.sql(f"SELECT old_id, new_id FROM {v}.supersession") == [(1, 3)]
    assert env.sql(f"SELECT count(*) FROM {v}.withdrawal") == [(0,)]
    assert settlements(env) == []
    assert env.sql(f"SELECT count(*) FROM {v}.event") == before


def test_save_names_the_neighbour_when_a_later_vector_of_the_batch_fails(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url)
    admit(env, fact())                                     # #1, embedded
    env.config(embedding={"url": "http://127.0.0.1:1"})
    admit(env, OTHER)                                      # #2, no vector
    env.config(embedding={"url": embedder.url})
    env.sql(f"CREATE FUNCTION {v}.refuse() RETURNS trigger LANGUAGE plpgsql AS "
            "$$BEGIN RAISE EXCEPTION 'refused'; END$$")
    env.sql(f"CREATE TRIGGER refuse BEFORE INSERT ON {v}.embedding FOR EACH ROW WHEN (NEW.entry_id = 2) "
            f"EXECUTE FUNCTION {v}.refuse()")
    result = admit(env, TWIN)[0]                           # #3: stored and paired with #1, then #2 fails
    assert result.stdout == f"admitted {v}#3\n"
    lines = result.stderr.splitlines()
    assert lines[0].startswith("val3: warning: no vector stored (") and "refused" in lines[0], result.stderr
    assert lines[1:] == [f"val3: near {v}#1 ({score(env)}): val3 settle {v}#3 {v}#1 --replace | --drop | --keep"]


def test_neighbour_false_records_and_shows_no_pair(env, embedder):
    v = env.vault
    embedding_vault(env, embedder.url, neighbour=False)
    result = admit(env, fact(), TWIN)[1]
    assert result.stdout == f"admitted {v}#2\n" and result.stderr == ""
    assert env.sql(f"SELECT count(*) FROM {v}.neighbour") == [(0,)]
    assert env.sql(f"SELECT count(*) FROM {v}.embedding") == [(2,)]
    assert "to settle: off ([embedding] neighbour = false)" in env.run("status").stdout.splitlines()
    assert env.sql(f"SELECT count(*) FROM {v}.neighbour_exempt") == [(2,)]  # not paired if the check comes back


def test_neighbour_false_hides_the_pairs_recorded_before(env, embedder):
    v = env.vault
    pair(env, embedder)
    assert listing(env)[1].endswith(f"· to settle (near {v}#1)")
    env.config(embedding={"url": embedder.url, "neighbour": False})
    assert listing(env) == [f"- {v}#1 [fact] April has 42 invoices", f"- {v}#2 [fact] April has 43 invoices"]
    assert "TO SETTLE" not in env.run("show", f"{v}#2").stdout
    assert "to settle: off ([embedding] neighbour = false; 1 open before)" in env.run("status").stdout.splitlines()
    env.config(embedding={"url": embedder.url})
    assert listing(env)[1].endswith(f"· to settle (near {v}#1)")


def test_neighbour_true_is_refused(env, embedder):
    embedding_vault(env, embedder.url, neighbour=True)
    result = env.run("status")
    assert result.returncode == 1 and "neighbour must be a number between 0 and 1, or false" in result.stderr
