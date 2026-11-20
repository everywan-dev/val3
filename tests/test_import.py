import getpass
import json
import os
import stat
import subprocess

from helpers import OLD_CHECKS, OUTPUT, before_0012, fact, make_vault, save

GIT = ["git", "-c", "user.name=Tester", "-c", "user.email=tester@example.org"]
ROUTING = "subscribers: {}\nowner: billing\n"
ADR = "# ADR 0001\n\nWe decided to route every message by its thread owner.\n"
NOTE = ("---\nname: route-by-owner\ndescription: messages go to the thread owner\nmetadata:\n  type: project\n---\n\n"
        "Every message is routed to its thread owner (`docs/adr/0001.md`).\n")


def git(env, *args):
    result = subprocess.run([*GIT, "-C", str(env.project), *args], capture_output=True, text=True, env=env.environ)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def write(env, path, text):
    target = env.project / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit(env, message="change"):
    git(env, "add", "-A")
    git(env, "commit", "-q", "-m", message)
    return git(env, "rev-parse", "HEAD")


def init(env):
    env.config()
    git(env, "init", "-q", "-b", "main")
    write(env, ".git/info/exclude", "val3.toml\n")


def unit(sha, path="config/routing.yaml", unit_id="kb-0001"):
    return (f"---\nid: {unit_id}\nevidence: verifiable\nprovenance:\n  - {path} at {sha}\n---\n\n"
            "# Routing ships no subscribers\n\nAs of 2026-09-08 routing.yaml has no subscribers.\n")


def adopter(env):
    """A validated-memory adopter: one unit with provenance, one note naming an ADR; val3.toml excluded."""
    init(env)
    write(env, "config/routing.yaml", ROUTING)
    write(env, "docs/adr/0001.md", ADR)
    first = commit(env, "code")
    write(env, "knowledge/kb-0001-routing.md", unit(first))
    write(env, "memory/MEMORY.md", "# Agent memory\n\n- [Route by owner](route-by-owner.md) — thread owner\n")
    write(env, "memory/route-by-owner.md", NOTE)
    second = commit(env, "knowledge")
    return first, second


def answer(env, call, candidate=None, skip=None):
    (env.fake / f"response-{call}.json").write_text(json.dumps(
        {"type": "result", "is_error": False, "total_cost_usd": 0.001,
         "structured_output": {"candidate": candidate, "skip": skip}}))


def unit_candidate(**changes):
    candidate = {"kind": "fact", "title": "Routing ships no subscribers",
                 "statement": "As of 2026-09-08 the routing configuration had no subscribers.",
                 "keywords": ["routing", "subscribers", "suscriptores"],
                 "evidence": [{"path": "config/routing.yaml", "quote": "subscribers: {}"}],
                 "observed": "2026-09-08"}
    candidate.update(changes)
    return candidate


def note_candidate(**changes):
    candidate = {"kind": "decision", "title": "Messages go to the thread owner",
                 "statement": "ADR 0001 recorded the decision to route every message by its thread owner.",
                 "keywords": ["routing", "owner"],
                 "evidence": [{"path": "docs/adr/0001.md", "quote": "route every message by its thread owner"}]}
    candidate.update(changes)
    return candidate


def dry_run(env):
    return env.run("import", "--dry-run", "--out", str(env.root / "manifest.json"))


def manifest(env):
    return json.loads((env.root / "manifest.json").read_text(encoding="utf-8"))


def test_dry_run_checks_quotes_and_writes_a_private_manifest(env):
    first, second = adopter(env)
    answer(env, 0, unit_candidate())
    answer(env, 1, note_candidate(evidence=[
        {"path": "docs/adr/0001.md", "quote": "route every message by its thread owner"},
        {"path": "docs/adr/0001.md", "quote": "we never decided this at all"},
        {"path": "config/routing.yaml", "quote": "owner: billing"},
        {"path": "memory/route-by-owner.md", "quote": "Every message is routed"}]))
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"manifest: {env.root / 'manifest.json'}",
        "sources: 2 ready, 0 skipped, 0 superseded, 0 uncommitted, 0 extractor-failed, 0 invalid, 0 unreadable",
        "evidence: 2 verified (2 at snapshot), 1 mismatched, 2 unbound, 0 excluded, 0 unreadable",
        "2 candidates, 0 unsupported; 0 capped, 0 differ from the working tree; cost $0.0020"]
    assert stat.S_IMODE(os.stat(env.root / "manifest.json").st_mode) == 0o600
    data = manifest(env)
    assert data["format"] == 1 and data["vault"] == env.vault and len(data["batch"]) == 16
    assert data["snapshot"]["commit"] == second
    unit_record, note_record = data["sources"]
    assert unit_record["path"] == "knowledge/kb-0001-routing.md" and unit_record["outcome"] == "ready"
    assert unit_record["candidate"]["evidence"] == [
        {"kind": "file", "path": "config/routing.yaml", "commit": first,
         "reproduction": f"git show {first}:config/routing.yaml", "quote": "subscribers: {}"}]
    meta = unit_record["candidate"]["import"]
    assert (meta["source_label"], meta["source_commit"], meta["observed"]) == ("snapshot", second, "2026-09-08")
    assert (meta["evidence"][0]["label"], meta["evidence"][0]["class"]) == ("source commit", "code")
    note_meta = note_record["candidate"]["import"]
    assert [e["status"] for e in note_meta["evidence"]] == ["verified", "mismatched", "unbound", "unbound"]  # the last: the note's own prose
    assert (note_meta["source_label"], note_meta["evidence"][0]["class"]) == ("note snapshot", "record")
    calls = env.calls()
    assert len(calls) == 2
    assert calls[0]["stdin"].startswith(f"SOURCE knowledge/kb-0001-routing.md (unit; snapshot {second[:7]}, ")
    assert f"FILE config/routing.yaml @ {first[:7]} (source commit, " in calls[0]["stdin"]
    assert f"FILE docs/adr/0001.md @ {second[:7]} (note snapshot, " in calls[1]["stdin"]
    assert "VAL3 importer. Prompt version: 2." in " ".join(calls[0]["argv"])


def test_a_quote_gone_from_the_snapshot_still_verifies_at_its_commit(env):
    adopter(env)
    write(env, "config/routing.yaml", "subscribers: {billing: [ops]}\n")
    third = commit(env, "subscribers")
    answer(env, 0, unit_candidate())
    answer(env, 1, None, "derivable")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "sources: 1 ready, 1 skipped," in result.stdout
    assert "evidence: 1 verified (0 at snapshot)," in result.stdout
    data = manifest(env)
    assert data["snapshot"]["commit"] == third
    assert data["sources"][0]["candidate"]["import"]["evidence"][0]["at_snapshot"] is False
    assert data["sources"][1]["skip"] == "derivable"


def test_unusable_answers_are_reported_and_the_run_goes_on(env):
    adopter(env)
    answer(env, 0, unit_candidate(kind="recipe"))
    answer(env, 1, None, "because")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "sources: 0 ready, 0 skipped, 0 superseded, 0 uncommitted, 0 extractor-failed, 2 invalid," in result.stdout
    errors = [record["error"] for record in manifest(env)["sources"]]
    assert "an imported candidate is a fact, decision or preference" in errors[0]
    assert "neither a candidate nor a known skip" in errors[1]


def test_evidence_of_the_wrong_shape_is_invalid(env):
    adopter(env)
    answer(env, 0, unit_candidate(evidence="config/routing.yaml"))
    answer(env, 1, None, "not-knowledge")
    result = dry_run(env)
    assert "1 invalid" in result.stdout
    assert 'evidence must be a list of {"path", "quote"} objects' in manifest(env)["sources"][0]["error"]


def test_an_extractor_failure_is_counted(env):
    adopter(env)
    (env.fake / "exit").write_text("1")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "2 extractor-failed" in result.stdout
    assert "fake extractor failure" in manifest(env)["sources"][0]["error"]


def test_dry_run_needs_the_repository_root_and_some_sources(env):
    env.config()
    result = dry_run(env)
    assert result.returncode == 1
    assert "is not the root of a git repository" in result.stderr
    init(env)
    write(env, "README.md", "nothing to import\n")
    commit(env)
    result = dry_run(env)
    assert result.returncode == 1
    assert "has no knowledge/*.md or memory/*.md" in result.stderr


def test_paths_with_spaces_and_accents(env):
    init(env)
    write(env, "docs/guía rápida.md", "Las facturas se cierran el día 5 de cada mes.\n")
    write(env, "memory/cierre de facturas.md", "El cierre es el día 5 (`docs/guía rápida.md`).\n")
    sha = commit(env)
    answer(env, 0, {"kind": "fact", "title": "Cierre mensual el día 5",
                    "statement": "La guía rápida fija el cierre de facturas el día 5.",
                    "evidence": [{"path": "docs/guía rápida.md", "quote": "se cierran el día 5 de cada mes"}]})
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "evidence: 1 verified (1 at snapshot)," in result.stdout
    record = manifest(env)["sources"][0]
    assert record["path"] == "memory/cierre de facturas.md"
    assert record["candidate"]["evidence"][0]["reproduction"] == f"git show '{sha}:docs/guía rápida.md'"


def test_a_note_cites_the_path_it_names_not_one_that_ends_the_same(env):
    init(env)
    write(env, "README.md", "Project overview.\n")
    write(env, "docs/README.md", "Routing is documented here.\n")
    write(env, "memory/routing-docs.md", "Routing is documented in `docs/README.md`.\n")
    commit(env)
    answer(env, 0, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert [item["path"] for item in manifest(env)["sources"][0]["cited"]] == ["docs/README.md"]


def test_a_note_cites_relative_and_absolute_paths_but_not_a_longer_name(env):
    init(env)
    for path in ("docs/a.md", "docs/b.md", "docs/c.md", "docs/d.md", "docs/e.md"):
        write(env, path, "Routing is documented here.\n")
    write(env, "docs/d.md.bak", "An old copy.\n")
    write(env, "memory/routing-docs.md",
          f"See `./docs/a.md`, `../docs/b.md`, `{env.project.resolve()}/docs/c.md` and the notes (docs/e.md), "
          "not the old copy `docs/d.md.bak`.\n")
    commit(env)
    answer(env, 0, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert sorted(item["path"] for item in manifest(env)["sources"][0]["cited"]) == [
        "docs/a.md", "docs/b.md", "docs/c.md", "docs/d.md.bak", "docs/e.md"]


def test_secret_files_are_never_cited_or_sent_to_the_extractor(env):
    init(env)
    secrets = [".env", "config/.env", "config/.ENV.production", "certs/server.pem", "certs/deploy.key",
               "ssh/id_ed25519", "config/Secrets.yaml", "certs/store.p12", ".envrc", "secrets/db.yaml",
               "config/.env.d/app.conf"]
    for path in secrets:
        write(env, path, "API_TOKEN=abc12345\n")
    write(env, "config/routing.yaml", ROUTING)
    first = commit(env, "config")
    provenance = "".join(f"  - {path} at {first}\n" for path in [*secrets, "config/routing.yaml"])
    write(env, "knowledge/kb-0001-routing.md", f"---\nid: kb-0001\nprovenance:\n{provenance}---\n\n# Routing\n")
    commit(env, "unit")
    answer(env, 0, unit_candidate(evidence=[
        {"path": "config/.env", "quote": "API_TOKEN=abc12345"},
        {"path": "certs/server.pem", "quote": "API_TOKEN=abc12345"},
        {"path": "config/routing.yaml", "quote": "subscribers: {}"}]))
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "evidence: 1 verified (1 at snapshot), 0 mismatched, 0 unbound, 2 excluded, 0 unreadable" in result.stdout
    record = manifest(env)["sources"][0]
    assert [e["status"] for e in record["candidate"]["import"]["evidence"]] == ["excluded", "excluded", "verified"]
    assert [item["path"] for item in record["cited"]] == ["config/routing.yaml"]
    stdin = env.calls()[0]["stdin"]
    assert "API_TOKEN" not in stdin and "abc12345" not in stdin


def test_save_refuses_file_evidence(env):
    make_vault(env)
    candidate = fact(evidence=[{"kind": "file", "path": "config/routing.yaml", "commit": "a" * 40,
                                "quote": "subscribers: {}"}])
    result = save(env, candidate, env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 1
    assert "file evidence comes only from `val3 import`" in result.stderr


def test_a_provenance_commit_missing_from_the_repository_is_unreadable(env):
    init(env)
    write(env, "config/routing.yaml", ROUTING)
    write(env, "knowledge/kb-0001-routing.md", unit("d" * 40))
    commit(env)
    answer(env, 0, unit_candidate())
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    record = manifest(env)["sources"][0]
    assert record["outcome"] == "ready"
    assert record["cited"] == [{"path": "config/routing.yaml", "commit": "d" * 40, "label": "source commit",
                                "date": "", "readable": False}]
    assert [e["status"] for e in record["candidate"]["import"]["evidence"]] == ["unreadable"]


def test_a_manifest_in_a_missing_directory_is_refused(env):
    adopter(env)
    result = env.run("import", "--dry-run", "--out", str(env.root / "missing" / "manifest.json"))
    assert result.returncode == 1
    assert "val3: error: cannot create" in result.stderr


def test_credentials_in_the_remote_url_are_not_recorded(env):
    adopter(env)
    git(env, "remote", "add", "origin", "https://someone:secret-token@example.org/adopter.git")
    answer(env, 0, None, "derivable")
    answer(env, 1, None, "derivable")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert manifest(env)["remote"] == "https://example.org/adopter.git"
    assert "secret-token" not in (env.root / "manifest.json").read_text(encoding="utf-8")


def test_a_password_with_an_at_sign_or_an_upper_case_scheme_is_not_recorded(env):
    adopter(env)
    git(env, "remote", "add", "origin", "https://placeholder.example/a.git")
    for run, (url, recorded, secret) in enumerate((
            ("https://user:p@ss@example.org/a.git", "https://example.org/a.git", "ss@"),
            ("HTTPS://u:tok@example.org/a.git", "HTTPS://example.org/a.git", "tok"))):
        git(env, "remote", "set-url", "origin", url)
        (env.root / "manifest.json").unlink(missing_ok=True)
        answer(env, 2 * run, None, "derivable")
        answer(env, 2 * run + 1, None, "derivable")
        assert dry_run(env).returncode == 0
        assert manifest(env)["remote"] == recorded
        assert secret not in (env.root / "manifest.json").read_text(encoding="utf-8")


def test_a_superseded_source_is_context_for_its_successor(env):
    first, _ = adopter(env)
    write(env, "knowledge/kb-0002-routing-owner.md", unit(first, unit_id="kb-0002"))
    write(env, "knowledge-index.md", "| id | state |\n|----|-------|\n"
          "| kb-0001 | superseded by kb-0002 |\n| kb-0002 | active |\n")
    write(env, "memory/old-routing.md", "Messages went to a shared queue.\n")
    write(env, "memory/MEMORY.md", "# Agent memory\n\n- [Route by owner](route-by-owner.md) — thread owner\n"
          "- [Old routing](old-routing.md) — superseded by [[route-by-owner]] (before the owner rule)\n")
    commit(env)
    answer(env, 0, None, "not-knowledge")
    answer(env, 1, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "sources: 0 ready, 2 skipped, 2 superseded," in result.stdout
    calls = env.calls()
    assert len(calls) == 2
    assert calls[0]["stdin"].startswith("SOURCE knowledge/kb-0002-routing-owner.md")
    assert "SUPERSEDED SOURCE knowledge/kb-0001-routing.md" in calls[0]["stdin"]
    assert "SUPERSEDED SOURCE memory/old-routing.md" in calls[1]["stdin"] and "shared queue" in calls[1]["stdin"]
    records = {record["path"]: record for record in manifest(env)["sources"]}
    assert records["knowledge/kb-0001-routing.md"]["by"] == "knowledge/kb-0002-routing-owner.md"
    assert records["memory/old-routing.md"]["by"] == "memory/route-by-owner.md"


def test_working_tree_changes_are_reported_and_the_snapshot_is_imported(env):
    adopter(env)
    write(env, "memory/new-note.md", "Written by a running session.\n")
    write(env, "memory/route-by-owner.md", NOTE + "Edited after the commit.\n")
    answer(env, 0, None, "not-knowledge")
    answer(env, 1, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "1 uncommitted" in result.stdout and "1 differ from the working tree" in result.stdout
    calls = env.calls()
    assert len(calls) == 2 and "Edited after the commit" not in calls[1]["stdin"]
    records = {record["path"]: record for record in manifest(env)["sources"]}
    assert records["memory/new-note.md"]["outcome"] == "uncommitted"
    assert records["memory/route-by-owner.md"]["dirty"] is True


def test_large_binary_and_export_files(env):
    init(env)
    write(env, "docs/routing-log.md", "".join(f"line {i:05d} of the routing log\n" for i in range(1000)))
    (env.project / "docs" / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0binary data")
    write(env, "docs/memory.html", "<p>Every message is routed to its thread owner.</p>\n")
    first = commit(env, "docs")
    write(env, "knowledge/kb-0001-log.md",
          f"---\nid: kb-0001\nprovenance:\n  - docs/routing-log.md at {first}\n  - docs/diagram.png at {first}\n"
          f"  - docs/memory.html at {first}\n---\n\n# The routing log\n")
    commit(env, "unit")
    answer(env, 0, {"kind": "fact", "title": "The routing log ends at line 999",
                    "statement": "The routing log at that commit ended at line 999.",
                    "evidence": [{"path": "docs/routing-log.md", "quote": "line 00999 of the routing log"},
                                 {"path": "docs/diagram.png", "quote": "binary data"},
                                 {"path": "docs/memory.html", "quote": "routed to its thread owner"}]})
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "evidence: 1 verified (1 at snapshot), 0 mismatched, 0 unbound, 1 excluded, 1 unreadable" in result.stdout
    assert "1 capped" in result.stdout
    stdin = env.calls()[0]["stdin"]
    assert "[… 10000 more characters]" in stdin
    assert "FILE docs/diagram.png" not in stdin and "FILE docs/memory.html" not in stdin
    cited = manifest(env)["sources"][0]["cited"]
    assert [(item["path"], item["readable"]) for item in cited] == [("docs/routing-log.md", True),
                                                                     ("docs/diagram.png", False)]


def test_a_staged_new_note_is_reported_as_uncommitted(env):
    adopter(env)
    write(env, "memory/staged.md", "Staged but not committed.\n")
    git(env, "add", "memory/staged.md")
    answer(env, 0, None, "not-knowledge")
    answer(env, 1, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "1 uncommitted" in result.stdout and "0 differ from the working tree" in result.stdout
    assert len(env.calls()) == 2
    records = {record["path"]: record for record in manifest(env)["sources"]}
    assert records["memory/staged.md"]["outcome"] == "uncommitted"
    assert records["knowledge/kb-0001-routing.md"]["dirty"] is False


def test_a_staged_rename_reports_both_paths(env):
    adopter(env)
    git(env, "mv", "memory/route-by-owner.md", "memory/routing-owner.md")
    answer(env, 0, None, "not-knowledge")
    answer(env, 1, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "1 uncommitted" in result.stdout and "1 differ from the working tree" in result.stdout
    assert len(env.calls()) == 2
    records = {record["path"]: record for record in manifest(env)["sources"]}
    assert records["memory/route-by-owner.md"]["dirty"] is True
    assert records["memory/route-by-owner.md"]["outcome"] == "skipped"
    assert records["memory/routing-owner.md"]["outcome"] == "uncommitted"
    assert records["knowledge/kb-0001-routing.md"]["dirty"] is False


def test_a_chain_of_superseded_notes_reaches_the_live_one(env):
    init(env)
    for name in "abc":
        write(env, f"memory/{name}.md", f"Note {name.upper()} says something.\n")
    write(env, "memory/MEMORY.md", "# Agent memory\n\n- [A](a.md) — superseded by [[b|note b]]\n"
          "- [B](b.md) — superseded by [[c]]\n- [C](c.md) — current\n")
    commit(env)
    answer(env, 0, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "sources: 0 ready, 1 skipped, 2 superseded," in result.stdout
    calls = env.calls()
    assert len(calls) == 1 and calls[0]["stdin"].startswith("SOURCE memory/c.md")
    stdin = calls[0]["stdin"]
    assert 0 < stdin.index("SUPERSEDED SOURCE memory/b.md") < stdin.index("SUPERSEDED SOURCE memory/a.md")
    records = {record["path"]: record for record in manifest(env)["sources"]}
    assert records["memory/a.md"]["by"] == records["memory/b.md"]["by"] == "memory/c.md"
    assert records["memory/a.md"]["outcome"] == records["memory/b.md"]["outcome"] == "superseded"


def test_supersession_that_reaches_no_live_source_drops_nothing(env):
    init(env)
    write(env, "knowledge/kb-0001-x.md", "---\nid: kb-0001\n---\n\n# Superseded by a unit that is not there\n")
    write(env, "knowledge-index.md", "| id | state |\n|----|-------|\n| kb-0001 | superseded by kb-0009 |\n")
    for name in ("lost", "p", "q"):
        write(env, f"memory/{name}.md", f"Note {name} says something.\n")
    write(env, "memory/MEMORY.md", "# Agent memory\n\n- [Lost](lost.md) — superseded by [[missing-note]]\n"
          "- [P](p.md) — superseded by [[q]]\n- [Q](q.md) — superseded by [[p]]\n")
    commit(env)
    for call in range(4):
        answer(env, call, None, "not-knowledge")
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "sources: 0 ready, 4 skipped, 0 superseded," in result.stdout
    assert len(env.calls()) == 4
    assert all(record["by"] is None for record in manifest(env)["sources"])


def ready_manifest(env):
    """A dry run over the fixture adopter: a verified unit candidate and an unsupported note candidate; the vault
    predates ADR 0012, so the note is queued."""
    adopter(env)
    assert env.run("vault", "create", env.vault).returncode == 0
    before_0012(env)
    answer(env, 0, unit_candidate())
    answer(env, 1, note_candidate(evidence=[{"path": "docs/adr/0001.md", "quote": "we never decided this at all"}]))
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    return env.root / "manifest.json"


def apply(env, path, environ=None):
    return env.run("import", "--apply", str(path), environ=environ)


def edit(path, change):
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_apply_admits_or_queues_the_manifest_once_without_calling_the_model(env):
    path = ready_manifest(env)
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]  # the dry run wrote nothing
    write(env, "docs/later.md", "A commit after the dry run.\n")
    commit(env, "later")  # HEAD moves; the manifest's snapshot does not
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    data = manifest(env)
    assert result.stdout.strip() == (f"batch {data['batch']}: 1 admitted, 1 queued, 0 already queued, "
                                     "0 previously rejected, 0 duplicate, 0 already imported, 0 refused")
    assert len(env.calls()) == 2
    assert env.sql(f"SELECT reason, payload->'import'->>'batch', origin_session FROM {env.vault}.review_item "
                   "ORDER BY id") == [("import", data["batch"], None), ("import", data["batch"], None)]
    assert env.sql(f"SELECT detail->>'batch', detail->>'snapshot', (detail->>'queued')::int FROM {env.vault}.event "
                   "WHERE action = 'import'") == [(data["batch"], data["snapshot"]["commit"], 1)]
    again = apply(env, path)
    assert again.returncode == 0, again.stderr
    assert again.stdout.strip().endswith(
        "0 admitted, 0 queued, 0 already queued, 0 previously rejected, 0 duplicate, 2 already imported, 0 refused")


def test_an_edited_quote_is_refused(env):
    path = ready_manifest(env)

    def change(data):
        candidate = data["sources"][0]["candidate"]
        candidate["evidence"][0]["quote"] = "subscribers: {all}"
        candidate["import"]["evidence"][0]["quote"] = "subscribers: {all}"
    edit(path, change)
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    assert "refused knowledge/kb-0001-routing.md: the manifest does not match the repository" in result.stdout
    assert result.stdout.strip().endswith(
        "0 admitted, 1 queued, 0 already queued, 0 previously rejected, 0 duplicate, 0 already imported, 1 refused")
    assert env.sql(f"SELECT payload->'import'->>'source' FROM {env.vault}.review_item") == [
        ("memory/route-by-owner.md",)]


def test_a_manifest_that_is_not_one_is_rejected_before_git_runs(env):
    path = ready_manifest(env)
    edit(path, lambda data: data["snapshot"].update(commit="--output=/tmp/val3-injected"))
    result = apply(env, path)
    assert result.returncode == 1
    assert "is not a val3 import manifest" in result.stderr
    assert not os.path.exists("/tmp/val3-injected")


def test_a_manifest_batch_is_the_sixteen_hex_characters_the_dry_run_writes(env):
    path = ready_manifest(env)
    edit(path, lambda data: data.update(batch=data["batch"] + "0"))
    result = apply(env, path)
    assert result.returncode == 1
    assert "is not a val3 import manifest" in result.stderr


def test_manifest_text_cannot_redraw_the_terminal(env):
    path = ready_manifest(env)
    forged = "memory/x\x1b]0;owned\x07\nbatch forged.md"
    edit(path, lambda data: data["sources"][1].update(path=forged))
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    assert "\x1b" not in result.stdout and "\x07" not in result.stdout
    assert "refused memory/x\\x1b]0;owned\\x07" in result.stdout
    lines = result.stdout.splitlines()
    assert len(lines) == 2 and lines[-1].startswith(f"batch {manifest(env)['batch']}: 1 admitted, 0 queued,")
    edit(path, lambda data: data.update(vault="vault\x1b[2J\nforged"))
    result = apply(env, path)
    assert result.returncode == 1
    assert "\x1b" not in result.stderr and "is for vault vault\\x1b[2J\\nforged," in result.stderr


def test_apply_logs_the_repository_remote_not_the_manifest_one(env):
    path = ready_manifest(env)
    edit(path, lambda data: data.update(remote="https://u:leaked-token@example.org/x.git"))
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT detail->>'remote' FROM {env.vault}.event WHERE action = 'import'") == [("",)]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.event "
                   "WHERE position('leaked-token' in detail::text) > 0") == [(0,)]


def test_apply_stores_what_it_rebuilt_not_the_manifest_text(env):
    path = ready_manifest(env)
    edit(path, lambda data: data["sources"][0]["candidate"]["import"]["evidence"][0].update(at_snapshot=1))
    result = apply(env, path)  # 1 == True in Python, so the record matches the rebuilt one
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("1 admitted, 1 queued, 0 already queued, 0 previously rejected, "
                                          "0 duplicate, 0 already imported, 0 refused")
    assert env.sql(f"SELECT jsonb_typeof(payload->'import'->'evidence'->0->'at_snapshot') FROM {env.vault}.review_item "
                   "WHERE payload->'import'->>'source' = 'knowledge/kb-0001-routing.md'") == [("boolean",)]  # not 1


def test_apply_checks_the_person_and_the_vault(env):
    path = ready_manifest(env)
    result = apply(env, path, environ={"CLAUDECODE": "1"})
    assert result.returncode == 1 and "outside the agent session" in result.stderr
    edit(path, lambda data: data.update(vault="another_vault"))
    result = apply(env, path)
    assert result.returncode == 1 and "is for vault another_vault" in result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]


def test_the_reproduction_of_an_unusual_path_is_shell_quoted_on_the_card(env):
    init(env)
    write(env, "docs/a;b $(x).md", "The month closes on the fifth day.\n")
    write(env, "memory/closing.md", "Closing: `docs/a;b $(x).md`.\n")
    sha = commit(env)
    answer(env, 0, {"kind": "fact", "title": "The month closes on the fifth day",
                    "statement": "The closing guide fixes the fifth day.",
                    "evidence": [{"path": "docs/a;b $(x).md", "quote": "closes on the fifth day"}]})
    assert env.run("vault", "create", env.vault).returncode == 0
    assert dry_run(env).returncode == 0
    assert apply(env, env.root / "manifest.json").returncode == 0
    card = env.run("review", "show", "1").stdout
    assert f"      reproduce: git show '{sha}:docs/a;b $(x).md'  (not run)" in card


def predating_imports(env):
    """A ready manifest and a vault whose checks are those of a vault created before plan 3b."""
    path = ready_manifest(env)
    for statement in OLD_CHECKS:
        env.sql(statement.format(v=env.vault))
    return path


def test_apply_on_a_vault_that_predates_imports_names_the_upgrade(env):
    path = predating_imports(env)
    result = apply(env, path)
    assert result.returncode == 1
    assert f"vault {env.vault} predates imports: its owner runs `val3 vault upgrade {env.vault}`" in result.stderr
    assert "violates check constraint" not in result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(0,)]


def test_a_failure_mid_apply_still_logs_the_batch_with_its_error(env):
    path = predating_imports(env)
    assert apply(env, path).returncode == 1
    rows = env.sql(f"SELECT detail->>'batch', (detail->>'queued')::int, detail->>'error' FROM {env.vault}.event "
                   "WHERE action = 'import'")
    assert rows == [(manifest(env)["batch"], 0, f"vault {env.vault} predates imports: its owner runs "
                                                 f"`val3 vault upgrade {env.vault}`")]


def test_import_usage(env):
    env.config()
    assert env.run("import").returncode == 2
    assert env.run("import", "--dry-run", "--apply", "m.json").returncode == 2
    assert env.run("import", "--apply", "m.json", "--out", "x.json").returncode == 2


def queued(env):
    path = ready_manifest(env)
    assert apply(env, path).returncode == 0
    return manifest(env)


def test_card_of_an_imported_candidate(env):
    data = queued(env)
    first = data["sources"][0]["candidate"]["evidence"][0]["commit"]
    card = env.run("review", "show", "1").stdout
    lines = card.splitlines()
    assert lines[0].startswith("review #1 · import · project · ")
    assert lines[0].endswith(f" · batch {data['batch']} · approved")  # admitted by the import itself
    assert lines[2].startswith(f"SOURCE knowledge/kb-0001-routing.md (snapshot {data['snapshot']['commit'][:7]}, ")
    assert "observed: 2026-09-08" in card
    assert "[fact]  Routing ships no subscribers" in card
    assert f"  - file config/routing.yaml @ {first[:7]} (" in card
    assert "source commit; at snapshot: yes; code)" in card
    assert "      > subscribers: {}" in card
    assert f"      reproduce: git show {first}:config/routing.yaml  (not run)" in card
    note = env.run("review", "show", "2").stdout
    assert "SOURCE memory/route-by-owner.md (note snapshot " in note
    assert "not verified:\n  - docs/adr/0001.md: mismatched" in note
    assert "no repository evidence: approving attests it" in note


def test_approval_admits_file_evidence_or_the_attestation(env):
    data = queued(env)  # the verified item #1 is admitted by the import; a person approves #2
    first = data["sources"][0]["candidate"]["evidence"][0]["commit"]
    result = env.run("review", "approve", "2", "--by", "owner")
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT e.id, e.kind, v.kind, v.reproduction, v.quote, v.session_id FROM {env.vault}.entry e "
                   f"LEFT JOIN {env.vault}.evidence v ON v.entry_id = e.id ORDER BY e.id") == [
        (1, "fact", "file", f"git show {first}:config/routing.yaml", "subscribers: {}", None),
        (2, "decision", None, None, None, None)]
    assert env.sql(f"SELECT entry_id, decided_by FROM {env.vault}.review_decision ORDER BY review_id") == [
        (1, "val3 (mechanical)"), (2, "owner")]
    shown = env.run("show", f"{env.vault}#1").stdout
    assert f"  - file: git show {first}:config/routing.yaml" in shown and "      > subscribers: {}" in shown
    assert "attested in review" not in shown
    attested = env.run("show", f"{env.vault}#2").stdout
    assert "evidence: none — attested in review #2 by owner on " in attested
    batch = manifest(env)["batch"]  # both entries came from the batch, the approved one too
    withdrawn = env.run("withdraw", "--batch", batch, "--obsolete", "--reason", "r")
    assert withdrawn.stdout.strip() == (f"batch {batch}: 2 withdrawn, 0 pending rejected, "
                                        "0 not withdrawn (existed before the batch), 0 already withdrawn")


def verified_manifest(env, note=None, before=False):
    """A dry run over the fixture adopter whose unit candidate is verified; `note` is the note candidate. `before`:
    the vault predates ADR 0012."""
    adopter(env)
    assert env.run("vault", "create", env.vault).returncode == 0
    if before:
        before_0012(env)
    answer(env, 0, unit_candidate())
    answer(env, 1, note or note_candidate())
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    return env.root / "manifest.json"


def import_items(env):
    return env.sql(f"SELECT i.payload->>'title', d.decision, d.decided_by, d.entry_id FROM {env.vault}.review_item i "
                   f"LEFT JOIN {env.vault}.review_decision d ON d.review_id = i.id ORDER BY i.id")


def test_import_admits_items_with_verified_evidence(env):
    path = verified_manifest(env)
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    batch = manifest(env)["batch"]
    assert result.stdout.strip() == (f"batch {batch}: 2 admitted, 0 queued, 0 already queued, 0 previously rejected, "
                                     "0 duplicate, 0 already imported, 0 refused")
    assert env.sql(f"SELECT id, title FROM {env.vault}.entry ORDER BY id") == [
        (1, unit_candidate()["title"]), (2, note_candidate()["title"])]
    assert import_items(env) == [(unit_candidate()["title"], "approved", "val3 (mechanical)", 1),
                                 (note_candidate()["title"], "approved", "val3 (mechanical)", 2)]
    withdrawn = env.run("withdraw", "--batch", batch, "--obsolete", "--reason", "r", tty=True)  # ADR 0012 §6
    assert withdrawn.returncode == 0, withdrawn.stderr
    assert withdrawn.stdout.strip() == (f"batch {batch}: 2 withdrawn, 0 pending rejected, "
                                        "0 not withdrawn (existed before the batch), 0 already withdrawn")


def test_an_import_item_without_evidence_waits_for_a_person(env):
    path = verified_manifest(env, note_candidate(evidence=[]), before=True)
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("1 admitted, 1 queued, 0 already queued, 0 previously rejected, "
                                          "0 duplicate, 0 already imported, 0 refused")
    assert import_items(env)[1] == (note_candidate()["title"], None, None, None)


def test_an_import_item_with_one_failed_quote_waits_for_a_person(env):
    path = verified_manifest(env, note_candidate(evidence=[
        {"path": "docs/adr/0001.md", "quote": "route every message by its thread owner"},
        {"path": "docs/adr/0001.md", "quote": "we never decided this at all"}]), before=True)
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("1 admitted, 1 queued, 0 already queued, 0 previously rejected, "
                                          "0 duplicate, 0 already imported, 0 refused")
    assert import_items(env)[1] == (note_candidate()["title"], None, None, None)
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]


def test_an_existing_review_row_is_not_decided_mechanically(env):
    adopter(env)
    write(env, "memory/routing-owner.md", "---\nname: routing-owner\n---\n\nRouting names an owner "
          "(`config/routing.yaml`).\n")
    commit(env, "third source")
    assert env.run("vault", "create", env.vault).returncode == 0
    before_0012(env)  # the first run queues
    third = {"kind": "fact", "title": "Routing has an owner field",
             "statement": "The routing configuration names an owner.", "keywords": ["routing"]}
    for call, candidate in enumerate((unit_candidate(), note_candidate(), third)):  # first run: no evidence
        answer(env, call, {**candidate, "evidence": []})
    assert dry_run(env).returncode == 0
    assert apply(env, env.root / "manifest.json").stdout.strip().endswith("3 queued, 0 already queued, "
                                                                           "0 previously rejected, 0 duplicate, "
                                                                           "0 already imported, 0 refused")
    assert env.run("review", "approve", "1").returncode == 0
    assert env.run("review", "reject", "2").returncode == 0
    for source in ("knowledge/kb-0001-routing.md", "memory/route-by-owner.md", "memory/routing-owner.md"):
        write(env, source, (env.project / source).read_text() + "\nChanged.\n")  # same wording, a new blob
    commit(env, "edit sources")
    third["evidence"] = [{"path": "config/routing.yaml", "quote": "owner: billing"}]
    for call, candidate in enumerate((unit_candidate(), note_candidate(), third), start=3):  # now verified
        answer(env, call, candidate)
    (env.root / "manifest.json").unlink()
    assert dry_run(env).returncode == 0
    result = apply(env, env.root / "manifest.json")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("0 admitted, 0 queued, 1 already queued, 1 previously rejected, "
                                          "1 duplicate, 0 already imported, 0 refused")
    assert env.sql(f"SELECT review_id, decision, decided_by FROM {env.vault}.review_decision ORDER BY review_id") == [
        (1, "approved", getpass.getuser()), (2, "rejected", getpass.getuser())]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(1,)]


def test_import_stores_vectors(env, embedder):
    path = verified_manifest(env)
    env.config(embedding={"url": embedder.url})
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT e.id FROM {env.vault}.entry e JOIN {env.vault}.embedding m ON m.entry_id = e.id "
                   "ORDER BY e.id") == [(1,), (2,)]


def test_a_vector_warning_after_import_is_one_escaped_line(env, embedder):
    path = verified_manifest(env)
    env.config(embedding={"url": embedder.url, "model": "bad\nmodel\x1b[2J"})  # a model the service lacks
    result = apply(env, path)
    assert result.returncode == 0, result.stderr
    assert env.sql(f"SELECT count(*) FROM {env.vault}.entry") == [(2,)]
    assert len(result.stderr.splitlines()) == 1 and "\x1b" not in result.stderr
    assert result.stderr.startswith("val3: warning: no vector stored (")
    assert "model bad\\x0amodel\\x1b[2J is not available" in result.stderr


RECORDED = "\nChecked on 2026-09-08:\n\n```\n$ route --dry-run msg-17\nowner=billing thread=4411\n```\n"


def test_a_quote_of_the_source_itself_verifies_only_inside_a_code_block(env):
    """What the source records (a test result, an example) is evidence of class record; its own prose is not."""
    adopter(env)
    note = "memory/route-by-owner.md"
    write(env, note, NOTE + RECORDED)
    last = commit(env, "note with a recorded run")
    answer(env, 0, unit_candidate())
    answer(env, 1, note_candidate(evidence=[
        {"path": note, "quote": "owner=billing thread=4411"},
        {"path": note, "quote": "Every message is routed to its thread owner"}]))
    result = dry_run(env)
    assert result.returncode == 0, result.stderr
    assert "evidence: 2 verified (2 at snapshot), 0 mismatched, 1 unbound, 0 excluded, 0 unreadable" in result.stdout
    candidate = manifest(env)["sources"][1]["candidate"]
    assert candidate["evidence"] == [{"kind": "file", "path": note, "commit": last,
                                      "reproduction": f"git show {last}:{note}", "quote": "owner=billing thread=4411"}]
    first, second = candidate["import"]["evidence"]
    assert (first["status"], first["label"], first["class"]) == ("verified", "the source itself", "record")
    assert second["status"] == "unbound"


def test_an_import_backed_by_what_its_source_records_is_admitted_with_that_evidence(env):
    adopter(env)
    note = "memory/route-by-owner.md"
    write(env, note, NOTE + RECORDED)
    last = commit(env, "note with a recorded run")
    answer(env, 0, unit_candidate())
    answer(env, 1, note_candidate(evidence=[{"path": note, "quote": "owner=billing thread=4411"}]))
    assert dry_run(env).returncode == 0
    assert env.run("vault", "create", env.vault).returncode == 0
    applied = env.run("import", "--apply", str(env.root / "manifest.json"))
    assert applied.returncode == 0, applied.stderr
    assert env.sql(f"SELECT kind, reproduction, quote FROM {env.vault}.evidence WHERE reproduction LIKE %s",
                   (f"%:{note}",)) == [("file", f"git show {last}:{note}", "owner=billing thread=4411")]
    assert env.sql(f"SELECT count(*) FROM {env.vault}.attestation") == [(0,)]
