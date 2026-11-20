"""Capture routes each candidate to an own vault named by its "vault" key (design 4.1)."""
import uuid

from helpers import OUTPUT, fact, make_vault, save
from test_capture_settles import CANDIDATE, STORED, argument, extract
from test_inbox import memdir, write_note
from test_specialized import admit, count, setup

HOME_FACT = fact(title="Ledger closes monthly", statement="Closing happens on day one of each month.")


def test_one_slice_routes_candidates_to_two_vaults(env, embedder):
    home, special = setup(env, embedding={"url": embedder.url})
    admit(env, special, STORED, env.transcript(tool_outputs=[OUTPUT]))
    result = extract(env, HOME_FACT, {**CANDIDATE, "vault": special})
    assert "admitted 2" in result.stdout and "rejected 0" in result.stdout, result.stdout
    assert (count(env, home), count(env, special)) == (1, 2)
    assert env.sql(f"SELECT title FROM {home}.entry") == [(HOME_FACT["title"],)]
    assert env.sql(f"SELECT title FROM {special}.entry WHERE id = 2") == [(CANDIDATE["title"],)]
    calls = env.calls()
    assert len(calls) == 2
    stdin = calls[1]["stdin"]
    assert f"(home vault {special})" in stdin and f"\n#### Close entry {special}#1 (similarity " in stdin, stdin
    assert '"vault"' not in stdin


def test_candidate_for_a_linked_vault_is_rejected(env):
    env.config()
    linked = f"{env.vault}_l"
    for vault in (env.vault, linked):
        assert env.run("vault", "create", vault).returncode == 0
    env.config(linked=[linked])
    result = extract(env, {**fact(), "vault": linked})
    assert "admitted 0" in result.stdout and "rejected 1" in result.stdout, result.stdout
    assert (count(env, env.vault), count(env, linked)) == (0, 0)
    events = env.sql(f"SELECT action, detail->>'error' FROM {env.vault}.event")
    assert events == [("invalid", f"vault {linked} is read only here: write to it from its own project")], events
    assert env.sql(f"SELECT count(*) FROM {linked}.event") == [(0,)]


def test_prompt_unchanged_without_specialized(env):
    make_vault(env)
    extract(env, fact())
    call = env.calls()[0]
    assert '"vault"' not in argument(call, "--system-prompt")
    assert "# Vaults of this project" not in call["stdin"]
    special = f"{env.vault}_s"
    result = env.run("vault", "create", special, "--specialized", "--description", "Billing difficulties.")
    assert result.returncode == 0, result.stderr
    t = env.transcript(tool_outputs=[OUTPUT], session=str(uuid.uuid4()))  # unseen lines: a second call
    assert env.run("extract", str(t)).returncode == 0
    call = env.calls()[1]
    assert argument(call, "--system-prompt") == argument(env.calls()[0], "--system-prompt")
    assert (f"\n# Vaults of this project\n- {env.vault}: (no description)\n- {special}: Billing difficulties.\n"
            'Give "vault" only for a candidate that belongs to a specialized vault; leave it out for the home vault.\n'
            in call["stdin"]), call["stdin"]


def test_explicit_home_vault_and_note_refs(env, embedder):
    home, special = setup(env, embedding={"url": embedder.url})
    directory = memdir(env)
    write_note(directory, env.session)
    t = env.transcript(tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": {**HOME_FACT, "vault": home}},
                 {"note": "april.md", "candidate": {**fact(), "vault": special}}])
    result = env.run("extract", str(t))
    assert result.returncode == 0 and "admitted 2" in result.stdout, result.stdout + result.stderr
    assert (count(env, home), count(env, special)) == (1, 1)
    assert f"Recorded in VAL3 as {special}#1 (verified)" in (directory / "april.md").read_text()
    assert env.sql(f"SELECT count(*) FROM {special}.embedding WHERE entry_id = 1") == [(1,)]
    assert env.sql(f"SELECT count(*) FROM {home}.embedding WHERE entry_id = 1") == [(1,)]


LEDGER = ("The ledger closing report lists every journal posted in the period, grouped by account, with the "
          "opening balance, the debits, the credits and the closing balance of each account, signed by the "
          "controller after the bank reconciliation and archived with the audit trail of the accounting team.")
LEDGER_STORED = fact(title="Ledger report contents", statement=LEDGER)
LEDGER_CANDIDATE = fact(title="Ledger report contents, revised", statement=LEDGER.replace("signed", "approved"))


def test_two_vaults_with_neighbours_get_one_settle_call_each(env, embedder):
    home, special = setup(env, embedding={"url": embedder.url})
    t = env.transcript(tool_outputs=[OUTPUT])
    admit(env, home, STORED, t)
    admit(env, special, LEDGER_STORED, t)
    result = extract(env, CANDIDATE, {**LEDGER_CANDIDATE, "vault": special})
    assert "admitted 2" in result.stdout and "3 extractor calls" in result.stdout, result.stdout
    settles = [call["stdin"] for call in env.calls()[1:]]
    assert len(settles) == 2
    by_vault = {vault: [s for s in settles if f"(home vault {vault})" in s] for vault in (home, special)}
    assert all(len(found) == 1 for found in by_vault.values()), settles
    for vault, other in ((home, special), (special, home)):
        stdin = by_vault[vault][0]
        assert f"#### Close entry {vault}#1 (similarity " in stdin and f"{other}#" not in stdin, stdin
    assert env.sql(f"SELECT title FROM {home}.entry ORDER BY id") == [(STORED["title"],), (CANDIDATE["title"],)]
    assert env.sql(f"SELECT title FROM {special}.entry ORDER BY id") == [
        (LEDGER_STORED["title"],), (LEDGER_CANDIDATE["title"],)]


def test_routed_candidate_cannot_supersede_a_home_entry(env):
    home, special = setup(env)
    save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    result = extract(env, {**fact(title="April has 43 invoices"), "vault": special, "supersedes": [f"{home}#1"]})
    assert "admitted 0" in result.stdout and "rejected 1" in result.stdout, result.stdout
    assert (count(env, home), count(env, special)) == (1, 0)
    # the reason capture swallows, shown by the same candidate through `val3 save`
    direct = save(env, {**fact(title="April has 43 invoices"), "supersedes": [f"{home}#1"]},
                  env.transcript(tool_outputs=[OUTPUT]), "--vault", special)
    assert direct.returncode == 1 and f"may only cite entries of vault {special}" in direct.stderr, direct.stderr


def test_save_rejects_a_vault_key(env):
    home, special = setup(env)
    result = save(env, {**fact(), "vault": special}, env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 1, result.stdout
    assert result.stderr == 'val3: error: "vault" is not a candidate key: use val3 save --vault <name>\n'
    assert (count(env, home), count(env, special)) == (0, 0)
    assert env.sql(f"SELECT count(*) FROM {home}.event") == [(0,)]
