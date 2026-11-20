from helpers import OUTPUT, embedding_vault, fact, make_vault, save


def saved(env, **changes):
    result = save(env, fact(keywords=[], **changes), env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 0, result.stderr
    return result.stdout.split()[-1]


def test_recall_is_accent_insensitive(env):
    make_vault(env)
    ref = saved(env, title="Cierre de facturación", statement="La facturación de abril se cierra el día 5.")
    for terms in (["facturacion"], ["Facturación"], ["FACTURACIÓN", "abril"]):
        result = env.run("recall", *terms)
        assert result.returncode == 0, result.stderr
        assert f"{ref}  [fact]  Cierre de facturación" in result.stdout, terms


def test_recall_stems_english(env):
    make_vault(env)
    ref = saved(env)
    assert ref in env.run("recall", "invoice").stdout


def test_stop_words_alone_match_nothing(env):
    make_vault(env)
    saved(env, title="Late payers", statement="El cliente que paga tarde recibe un aviso.")
    result = env.run("recall", "que", "the", "de", "la")
    assert result.returncode == 0
    assert result.stdout.strip() == "no matches"


def test_quotes_and_backslashes_in_terms_are_safe(env):
    make_vault(env)
    ref = saved(env, title="O'Brien account", statement="Account O'Brien exports to the C:\\exports folder.")
    result = env.run("recall", "O'Brien", "C:\\exports\\", "'''")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert ref in result.stdout


def test_recall_searches_linked_vaults(env):
    shared = env.vault + "_shared"
    env.config(home=shared)
    assert env.run("vault", "create", shared).returncode == 0
    shared_ref = saved(env)
    env.config(linked=[shared])
    assert env.run("vault", "create", env.vault).returncode == 0
    home_ref = saved(env, title="May has 50 invoices", statement="The ERP holds 50 invoices dated May 2026.")
    output = env.run("recall", "invoices").stdout
    assert shared_ref == f"{shared}#1" and shared_ref in output
    assert home_ref in output


def test_superseded_entries_are_not_recalled(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT, "invoice_count\n-------------\n          43"])
    save(env, fact(), t)
    newer = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.",
                 evidence=[{"kind": "query", "reproduction": "SELECT 43",
                            "quote": "invoice_count\n-------------\n          43"}],
                 supersedes=f"{env.vault}#1")
    save(env, newer, t)
    output = env.run("recall", "invoices").stdout
    assert f"{env.vault}#2" in output
    assert f"{env.vault}#1 " not in output



def test_recall_leaves_out_every_replaced_entry(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT, "invoice_count\n-------------\n          43"])
    save(env, fact(), t)
    save(env, fact(title="May has 42 invoices", statement="The ERP holds 42 invoices dated May 2026."), t)
    newer = fact(title="Spring has 43 invoices", statement="The ERP holds 43 invoices dated spring 2026.",
                 evidence=[{"kind": "query", "reproduction": "SELECT 43",
                            "quote": "invoice_count\n-------------\n          43"}],
                 supersedes=[f"{env.vault}#1", f"{env.vault}#2"])
    assert save(env, newer, t).returncode == 0
    output = env.run("recall", "invoices").stdout
    assert f"{env.vault}#3" in output
    assert f"{env.vault}#1 " not in output and f"{env.vault}#2 " not in output

def test_tools_come_first(env):
    make_vault(env)
    saved(env)
    assert env.run("tool", "add", "invoice-browser", "--type", "cli",
                   "--description", "Lists invoices of any month.").returncode == 0
    lines = env.run("recall", "invoices").stdout.splitlines()
    assert lines[0].startswith(f"{env.vault}#2  [tool]")


def test_accented_spanish_stop_words_match_nothing(env):
    make_vault(env)
    saved(env, title="Informe disponible", statement="El informe está disponible y también más datos.")
    result = env.run("recall", "¿Por", "qué", "no", "está", "más", "claro", "también?")
    assert result.returncode == 0
    assert result.stdout.strip() == "no matches"


NOTES = ("INSERT INTO {v}.entry (kind, title, statement, content_hash, origin_project, origin_machine) "
         "SELECT 'fact', 'Ledger note ' || g, 'The invoices ledger closes batch ' || g || '.', 'n' || g, 'p', 'm' "
         "FROM generate_series(1, %s) g")


def test_one_shared_word_among_terms_no_entry_holds_is_a_match(env):
    make_vault(env)
    env.sql(NOTES.format(v=env.vault), (12,))
    ref = saved(env, title="Host rs700 disks", statement="The rs700 host has four disks.")
    saved(env, title="Host rs700 memory", statement="The rs700 host has 123 GiB.")
    output = env.run("recall", "rs700", "reboot", "mounts").stdout
    assert ref in output and output.count(f"{env.vault}#") == 2


def test_unlisted_function_words_match_nothing(env):
    make_vault(env)
    saved(env, title="Exportación manual", statement="Así puede hacerlo el mismo equipo.")
    result = env.run("recall", "así", "puede", "mismo")
    assert result.returncode == 0
    assert result.stdout.strip() == "no matches"


def test_words_most_entries_hold_do_not_make_a_match(env):
    make_vault(env)
    env.sql(NOTES.format(v=env.vault), (12,))
    result = env.run("recall", "ledger", "invoices", "of", "the", "penguin", "colony")
    assert result.returncode == 0
    assert result.stdout.strip() == "no matches"


def test_a_word_only_one_entry_holds_makes_a_match(env):
    make_vault(env)
    env.sql(NOTES.format(v=env.vault), (12,))
    ref = saved(env, title="Corpus access", statement="An Entra administrator grants the app read access.")
    output = env.run("recall", "who", "gives", "permissions", "in", "the", "Entra", "tenant").stdout
    assert ref in output
    assert output.count(f"{env.vault}#") == 1


MAILBOX = {"title": "Sample mailbox address", "statement": "Samples arrive at the shared sample mailbox."}
DOWN = "http://127.0.0.1:1"


def test_recall_is_semantic_with_an_embedding_table(env, embedder):
    embedding_vault(env, embedder.url)
    ref = saved(env, **MAILBOX)
    result = env.run("recall", "sample", "mailbox", "address")
    assert result.returncode == 0, result.stderr
    assert f"{ref}  [fact]  Sample mailbox address" in result.stdout
    assert embedder.inputs[-1] == "sample mailbox address"
    assert env.run("recall", "penguin", "colony").stdout.strip() == "no matches"


def test_recall_applies_the_floor(env, embedder):
    embedding_vault(env, embedder.url, floor=0.9)
    saved(env, **MAILBOX)
    assert env.run("recall", "at", "the").stdout.strip() == "no matches"  # stop words: no full-text match


def test_recall_finds_entries_with_a_vector_by_their_words(env, embedder):
    embedding_vault(env, embedder.url, floor=0.99)
    ref = saved(env, **MAILBOX)
    result = env.run("recall", "mailbox")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count(ref) == 1 and f"{ref}  [fact]  Sample mailbox address" in result.stdout


def test_recall_lists_entries_without_a_vector_by_full_text(env, embedder):
    embedding_vault(env, DOWN)
    ref = saved(env, **MAILBOX)
    env.config(embedding={"url": embedder.url})
    result = env.run("recall", "sample", "mailbox", "address")
    assert ref in result.stdout
    assert result.stderr == ""


def test_recall_falls_back_to_full_text_when_the_service_is_down(env):
    embedding_vault(env, DOWN)
    ref = saved(env, **MAILBOX)
    result = env.run("recall", "sample", "mailbox", "address")
    assert result.returncode == 0
    assert ref in result.stdout
    assert "semantic search is unavailable" in result.stderr


def test_recall_on_a_vault_without_the_embedding_table(env, embedder):
    embedding_vault(env, embedder.url)
    ref = saved(env, **MAILBOX)
    env.sql(f"DROP TABLE {env.vault}.embedding")
    result = env.run("recall", "sample", "mailbox", "address")
    assert result.returncode == 0
    assert ref in result.stdout
    assert f"val3 vault upgrade {env.vault}" in result.stderr


def test_superseded_entries_are_not_recalled_semantically(env, embedder):
    embedding_vault(env, embedder.url)
    saved(env, **MAILBOX)
    newer = saved(env, title="Sample mailbox address moved",
                  statement="Samples now arrive at the accounting sample mailbox.", supersedes=f"{env.vault}#1")
    output = env.run("recall", "sample", "mailbox", "address").stdout
    assert newer in output
    assert f"{env.vault}#1 " not in output


def test_control_characters_in_recall_and_show_are_escaped(env):
    make_vault(env)
    ref = saved(env, title="Invoices \x1b[2J cleared")
    for args in (("recall", "invoices"), ("show", ref)):
        out = env.run(*args).stdout
        assert "\x1b" not in out and "Invoices \\x1b[2J cleared" in out, args


def test_an_entry_found_by_meaning_and_by_word_keeps_its_semantic_place(env, embedder):
    embedding_vault(env, embedder.url, floor=0.3)
    env.sql(NOTES.format(v=env.vault), (12,))  # close by meaning; their words weigh nothing
    saved(env, title="Ledger batch summary", statement="The invoices ledger closes batch nine late.")
    both = saved(env, title="Ledger batch rs700", statement="The invoices ledger closes batch on rs700.")
    output = env.run("recall", "invoices", "ledger", "closes", "batch", "rs700").stdout
    assert output.startswith(f"{both}  [fact]  Ledger batch rs700") and output.count(both) == 1


def test_an_identifier_the_meaning_misses_is_still_among_the_first(env, embedder):
    embedding_vault(env, embedder.url, floor=0.5)
    env.sql(NOTES.format(v=env.vault), (12,))  # "invoices", "ledger", "closes", "batch" weigh nothing by word
    for name in ("one", "two", "three", "four", "five", "six"):  # close by meaning, and one word that weighs
        saved(env, title=f"Ledger batch {name}", statement=f"The invoices ledger closes batch {name} pending.")
    wanted = saved(env, title="Archive zq17 audit",
                   statement="The zq17 audit archive holds 2280 files and 17 manifests.")
    saved(env, title="Plan zq17", statement="A review of zq17 is planned for next week.")  # not the only holder
    result = env.run("recall", "invoices", "ledger", "closes", "batch", "pending", "zq17", "audit")
    assert result.returncode == 0, result.stderr
    refs = [line.split()[0] for line in result.stdout.splitlines() if line.startswith(f"{env.vault}#")]
    assert refs.index(wanted) == 2 and len(refs) == len(set(refs))


def test_a_tool_sharing_one_word_does_not_lead_a_long_search(env, embedder):
    embedding_vault(env, embedder.url, floor=0.5)
    env.sql(NOTES.format(v=env.vault), (12,))
    for name in ("one", "two", "three", "four", "five", "six"):
        saved(env, title=f"Ledger batch {name}", statement=f"The invoices ledger closes batch {name} pending audit.")
    assert env.run("tool", "add", "queue-browser", "--type", "cli",
                   "--description", "Lists the pending items of the mail queue.").returncode == 0
    lines = env.run("recall", "--limit", "5", "invoices", "ledger", "closes", "batch", "pending", "audit").stdout
    assert "[tool]" not in lines and lines.count(f"{env.vault}#") == 5
    assert "[tool]" in env.run("recall", "--limit", "5", "queue", "browser").stdout
