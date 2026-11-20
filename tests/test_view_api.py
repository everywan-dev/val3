"""`val3 view`: the JSON endpoints, on vaults built with the CLI, read over HTTP."""
import json
import time
from urllib.parse import quote as q

import psycopg

from helpers import OUTPUT, fact, legacy, make_vault, save, with_entry, old_vault, TOOL
from viewer import viewing

HOSTILE = "<script>alert(1)</script><img src=x onerror=alert(2)>\"'&  \x1b[31m"
TITLE = f"T {HOSTILE}"
STATEMENT = ("db-stage-1 " + HOSTILE * 300)[:20000]
QUOTE = ("zz_quote_name " + HOSTILE * 100)[:5000]
REPRODUCTION = ("cat report.txt # " + HOSTILE * 100)[:6000]
REASON = f"wrong: {HOSTILE}"


def world(env):
    """Home vault: #1 hostile, superseded by #2 and withdrawn; #2 live with evidence; #3 a tool without evidence.
    A linked vault with one fact, and a linked vault that does not exist."""
    linked = f"{env.vault}_l"
    other = env.root / "other"
    other.mkdir()
    with_entry(env, linked, cwd=other, statement="The ERP holds 42 invoices on db-stage-1.")
    make_vault(env)
    env.config(linked=[linked, f"{env.vault}_gone"])
    session = f"s-{HOSTILE}"
    hostile = env.transcript(tool_outputs=[QUOTE])
    first = fact(title=TITLE, statement=STATEMENT, keywords=[HOSTILE], indexes=["billing"],
                 evidence=[{"kind": "command", "reproduction": REPRODUCTION, "quote": QUOTE}])
    result = env.run("save", "--transcript", str(hostile), "--session", session, input=json.dumps(first))
    assert result.stdout == f"admitted {env.vault}#1\n", result.stderr
    second = fact(title="April has 43 invoices", statement="Counted again on db-stage-1.",
                  supersedes=[f"{env.vault}#1"])
    assert save(env, second, env.transcript(tool_outputs=[OUTPUT])).stdout == f"admitted {env.vault}#2\n"
    result = env.run("tool", "add", "code-graph", "--type", "cli", "--description", "Answers overrides.")
    assert result.stdout.startswith(f"admitted {env.vault}#3"), result.stderr
    result = env.run("withdraw", f"{env.vault}#1", "--wrong", "--reason", REASON, tty=True)  # a person's, in a terminal
    assert result.returncode == 0, result.stderr
    return linked, session


def snapshot(env, vaults):
    counts = {}
    for vault in vaults:
        for (table,) in env.sql("SELECT tablename FROM pg_tables WHERE schemaname = %s", (vault,)):
            counts[vault, table] = env.sql(f'SELECT count(*) FROM {vault}."{table}"')[0][0]
    for table in ("vault_note", "mirror_sync"):
        counts[table] = env.sql(f"SELECT count(*) FROM val3_common.{table}")[0][0]
    return counts


def by_name(vaults):
    return {vault["name"]: vault for vault in vaults}


def test_summary(env):
    linked, _ = world(env)
    with viewing(env) as view:
        data = view.json("api/summary")
    assert data["at"].endswith("+00:00") and data["project"] == "project"
    vaults = by_name(data["vaults"])
    assert [v["name"] for v in data["vaults"]] == [env.vault, linked, f"{env.vault}_gone"]
    home = vaults[env.vault]
    assert home["ok"] and home["role"] == "home"
    assert home["counts"] == {"entries": 3, "live": 2, "superseded": 1, "withdrawn": 1, "no_evidence": 1,
                              "approved_by_person": 1, "unverified": 0, "live_with_evidence": 1}  # the tool a person registered is attested (ADR 0012)
    assert {i["name"]: (i["entries"], i["live"]) for i in home["indexes"]} == {"billing": (2, 1), None: (1, 1)}
    assert sum(sum(day["via"].values()) for day in home["admitted_per_day"]) == 3
    assert home["superseded_this_week"] == [{"ref": f"{env.vault}#1", "title": TITLE}]
    assert home["unverified_latest"] == []
    assert vaults[linked]["ok"] and vaults[linked]["role"] == "linked" and vaults[linked]["counts"]["entries"] == 1
    assert vaults[f"{env.vault}_gone"] == {"name": f"{env.vault}_gone", "role": "linked", "ok": False,
                                           "reason": "missing"}


def test_a_vault_that_cannot_be_read_beside_one_that_can(env):
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    linked, _ = world(env)
    for vault in (env.vault, linked):
        assert env.run("vault", "grant", vault, env.reader, "--read").returncode == 0
    env.sql(f"REVOKE ALL ON ALL TABLES IN SCHEMA {linked} FROM {env.reader}")
    env.config(linked=[linked], service="val3reader")
    with viewing(env) as view:
        vaults = by_name(view.json("api/summary")["vaults"])
        assert vaults[env.vault]["ok"] and vaults[env.vault]["counts"]["entries"] == 3
        assert vaults[linked] == {"name": linked, "role": "linked", "ok": False, "reason": "not readable"}
        entries = view.json("api/entries")
        assert entries["total"] == 3 and not by_name(entries["vaults"])[linked]["ok"]
        assert view.json(f"api/entry?ref={q(linked)}%231", expect=404)["error"] == f"vault {linked} is not readable"


def test_entries_filters_sort_and_pages(env):
    linked, session = world(env)
    with viewing(env) as view:
        def refs(query=""):
            data = view.json("api/entries" + (f"?{query}" if query else ""))
            return [r["ref"] for r in data["rows"]]

        data = view.json("api/entries")
        assert data["total"] == 4 and data["page"] == 1 and data["page_size"] == 200
        assert {v["name"]: v.get("matched") for v in data["vaults"]} == {env.vault: 3, linked: 1,
                                                                         f"{env.vault}_gone": None}
        first = next(r for r in data["rows"] if r["ref"] == f"{env.vault}#1")
        assert first["id"] == "1" and first["vault"] == env.vault and first["title"] == TITLE
        assert first["statement"] == STATEMENT[:300] and first["statement_cut"]
        assert first["keywords"] == HOSTILE and first["session"] == session
        assert first["superseded"] and first["withdrawn"] and not first["live"]
        assert first["evidence"] == ["command"] and first["indexes"] == ["billing"] and first["via"] == "save"
        assert first["recorded"].endswith("+00:00")
        assert set(refs()) == {f"{env.vault}#{i}" for i in (1, 2, 3)} | {f"{linked}#1"}
        assert refs("sort=ref") == [f"{env.vault}#1", f"{env.vault}#2", f"{env.vault}#3", f"{linked}#1"]
        assert refs("sort=-ref") == [f"{linked}#1", f"{env.vault}#3", f"{env.vault}#2", f"{env.vault}#1"]
        assert refs("sort=title")[:2] == [f"{linked}#1", f"{env.vault}#2"]  # "April has 42 …", "April has 43 …"
        assert refs("state=superseded") == refs("state=withdrawn") == [f"{env.vault}#1"]
        assert refs("state=approved-by-person") == refs("state=no-evidence") == refs("kind=tool") == [f"{env.vault}#3"]
        assert refs("state=unverified") == []
        assert sorted(refs("state=live")) == [f"{env.vault}#2", f"{env.vault}#3", f"{linked}#1"]
        assert sorted(refs("index=billing")) == [f"{env.vault}#1", f"{env.vault}#2", f"{linked}#1"]
        assert refs("index=(none)") == [f"{env.vault}#3"]
        assert refs("q=" + q("<SCRIPT>")) == [f"{env.vault}#1"]  # literal, case-insensitive
        assert refs("q=%25") == refs("q=_") == []  # no pattern characters
        assert refs(f"session={q(session)}") == [f"{env.vault}#1"]
        assert refs("session=(unknown)") == [f"{env.vault}#3"]
        assert refs(f"vault={linked}") == [f"{linked}#1"]
        assert refs("via=tool-add") == [f"{env.vault}#3"] and refs("via=not-recorded") == []
        assert refs("from=2000-01-01&to=2999-12-31&kind=fact&sort=ref") == [f"{env.vault}#1", f"{env.vault}#2",
                                                                             f"{linked}#1"]
        assert refs("to=2000-01-01") == []
        assert refs("page=2") == []
        assert view.json("api/entries?vault=other_vault", expect=404)["error"]
        for bad in ("page=0", "page=1001", "page=x", "sort=id", "kind=note", "state=gone", "via=hand",
                    "q=" + "x" * 201, "from=2026-13-01", "from=20261101", "colour=red", "kind=fact&kind=tool",
                    "q=%00"):
            data = view.json(f"api/entries?{bad}", expect=400)
            assert data["error"] and data["at"], bad


def test_entry_in_full(env):
    linked, session = world(env)
    with viewing(env) as view:
        entry = view.json(f"api/entry?ref={env.vault}%231")["entry"]
        assert entry["statement"] == STATEMENT and not entry["statement_cut"] and entry["title"] == TITLE
        [evidence] = entry["evidence_items"]
        assert evidence["quote"] == QUOTE[:4000] and evidence["quote_cut"]
        assert evidence["reproduction"] == REPRODUCTION[:4000] and evidence["reproduction_cut"]
        assert evidence["session"] == session
        assert [s["ref"] for s in entry["superseded_by"]] == [f"{env.vault}#2"] and entry["supersedes"] == []
        assert entry["withdrawal"]["cause"] == "wrong" and entry["withdrawal"]["reason"] == " ".join(REASON.split())
        assert [e["action"] for e in entry["events"]] == ["admitted", "superseded", "withdrawn"]
        assert all(isinstance(e["id"], str) for e in entry["events"])
        newer = view.json(f"api/entry?ref={env.vault}%232")["entry"]
        assert [s["ref"] for s in newer["supersedes"]] == [f"{env.vault}#1"] and newer["withdrawal"] is None
        tool = view.json(f"api/entry?ref={env.vault}%233")["entry"]
        assert tool["evidence_items"] == [] and tool["tool"] == {"type": "cli", "environment": "", "priority": 0}
        assert tool["session"] is None
        assert view.json(f"api/entry?ref={linked}%231")["entry"]["role"] == "linked"
        assert view.json(f"api/entry?ref={env.vault}%239", expect=404)["error"] == f"no entry {env.vault}#9"
        assert "not one of this project's vaults" in view.json("api/entry?ref=other%231", expect=404)["error"]
        for bad in ("", "?ref=", f"?ref={env.vault}%23x", f"?ref={env.vault}%230", f"?ref={env.vault}",
                    f"?ref={env.vault}%231%20or%201=1", "?ref=Vault%231"):
            assert view.json(f"api/entry{bad}", expect=400)["error"], bad


def test_sessions_and_the_extractor_log(env):
    linked, session = world(env)
    env.state.mkdir(parents=True, exist_ok=True)
    (env.state / "extract.log").write_text(
        json.dumps({"at": "2026-11-15T10:00:00+00:00", "entry": "a.json", "session": session, "outcome": "done",
                    "admitted": 1, "calls": 3, "cost_usd": 0.25, "skipped": 2}) + "\n"
        + json.dumps({"at": "2026-11-15T11:00:00+00:00", "session": session, "outcome": "retry"}) + "\n"
        + '{"at": "2026-11-15T12:00:00+00:00", "session": "x", "outco')  # an incomplete last line
    with viewing(env) as view:
        data = view.json("api/sessions")
    assert data["extractor_log"] is True
    rows = {(r["vaults"][0], r["session"]): r for r in data["sessions"]}
    hostile = rows[env.vault, session]
    assert hostile["entries"] == 1 and hostile["superseded"] == 1 and hostile["withdrawn"] == 1
    assert hostile["via"] == {"save": 1} and hostile["project"] == "project"
    assert hostile["extractor"] == {"runs": 1, "calls": 3, "cost_usd": 0.25, "skipped": 2,
                                    "last": "2026-11-15T10:00:00+00:00"}
    assert rows[env.vault, None]["entries"] == 1 and rows[env.vault, None]["extractor"] is None
    assert rows[env.vault, env.session]["entries"] == 1
    assert rows[linked, env.session]["entries"] == 1


def test_names(env):
    linked, _ = world(env)
    with viewing(env) as view:
        data = view.json("api/names")
        names = {n["name"]: n for n in data["names"]}
        assert names["db-stage-1"]["entries"] == 3 and names["db-stage-1"]["not_live"] == 1
        assert "zz_quote_name" not in names  # evidence is searched, never suggested
        assert all(n["entries"] >= 2 for n in data["names"])
        found = view.json("api/names?q=zz_QUOTE_name")
        assert [r["ref"] for r in found["rows"]] == [f"{env.vault}#1"] and found["total"] == 1
        assert sorted(r["ref"] for r in view.json("api/names?q=db-stage-1")["rows"]) == [
            f"{env.vault}#1", f"{env.vault}#2", f"{linked}#1"]
        assert view.json("api/names?q=" + "x" * 201, expect=400)["error"]


def test_names_are_tokens_with_a_digit_or_separator(env):
    make_vault(env)
    for i, statement in enumerate(["Runs on db-prod-2, see /srv/dumps and run_20261109T1402. Dated 2026-11-15, "
                                   "costs 1.26 and 12345; e.g. it is read-only.",
                                   "db-prod-2 again (/srv/dumps), run_20261109T1402; 2026-11-15 1.26 12345 e.g. "
                                   "read-only."]):
        result = save(env, fact(title=f"Fact {i}", statement=statement), env.transcript(tool_outputs=[OUTPUT]))
        assert result.returncode == 0, result.stderr
    with viewing(env) as view:
        names = {n["name"] for n in view.json("api/names")["names"]}
    assert names == {"db-prod-2", "/srv/dumps", "run_20261109T1402", "read-only"}


def test_hostile_text_comes_back_as_inert_json(env):
    world(env)
    with viewing(env) as view:
        for path in ("api/summary", "api/entries", f"api/entry?ref={env.vault}%231", "api/sessions", "api/names",
                     "api/names?q=" + q("<script>")):
            status, headers, body = view.get(path)
            assert status == 200, path
            assert b"<" not in body and b">" not in body and b"&" not in body and b"\x1b" not in body, path
            assert b"\xe2\x80\xa8" not in body, path  # U+2028 escaped
            json.loads(body)


def test_reading_changes_no_row(env):
    linked, _ = world(env)
    before = snapshot(env, [env.vault, linked])
    with viewing(env) as view:
        for path in ("api/summary", "api/entries", "api/entries?state=live&sort=title", f"api/entry?ref={env.vault}%231",
                     f"api/entry?ref={linked}%231", "api/sessions", "api/names", "api/names?q=db"):
            assert view.get(path)[0] == 200, path
    assert snapshot(env, [env.vault, linked]) == before


def test_a_slow_vault_times_out_and_the_others_answer(env):
    linked, _ = world(env)
    with psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname=val3") as holder:
        holder.execute(f"LOCK TABLE {linked}.entry IN ACCESS EXCLUSIVE MODE")  # held until the block ends
        with viewing(env) as view:
            started = time.monotonic()
            vaults = by_name(view.json("api/summary")["vaults"])
            assert 4 < time.monotonic() - started < 15
            assert vaults[env.vault]["ok"] and vaults[env.vault]["counts"]["entries"] == 3
            assert vaults[linked] == {"name": linked, "role": "linked", "ok": False,
                                      "reason": "the query took too long"}


def test_approved_by_a_person_counts_admissions_only(env):
    old_vault(env)  # a vault that still has the review list
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    legacy(env, {"title": f"{env.vault}#1 was superseded"}, reason="supersession", entry_id=1)  # a notice
    legacy(env, fact(title="Queued before automatic admission"))
    for review in (1, 2):
        result = env.run("review", "approve", str(review))
        assert result.returncode == 0, result.stderr
    with viewing(env) as view:
        [home] = view.json("api/summary")["vaults"]
        approved = [r["ref"] for r in view.json("api/entries?state=approved-by-person")["rows"]]
        decisions = view.json(f"api/entry?ref={env.vault}%231")["entry"]["decisions"]
    assert home["counts"]["approved_by_person"] == 1 and approved == [f"{env.vault}#2"]
    assert [d["reason"] for d in decisions] == ["supersession"]  # shown in the panel, not counted


def test_an_entry_saved_without_evidence_is_unverified_in_the_view(env):
    make_vault(env)
    result = save(env, TOOL, env.transcript())  # an agent's save of a tool carries no evidence
    assert result.stdout == f"admitted unverified {env.vault}#1\n", result.stderr
    with viewing(env) as view:
        [home] = view.json("api/summary")["vaults"]
        rows = [r["ref"] for r in view.json("api/entries?state=unverified")["rows"]]
    assert home["counts"]["unverified"] == 1 and home["counts"]["approved_by_person"] == 0
    assert rows == [f"{env.vault}#1"]
