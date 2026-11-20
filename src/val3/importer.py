"""`val3 import`: the knowledge of a validated-memory 2.x adopter, as review items (import design).

A dry run reads one pinned commit through git objects, never the working tree; asks the extractor for one
candidate per source; checks each quote with `git show` at a commit the importer fixes; and writes a
manifest. Apply re-checks the manifest against the repository and queues its candidates: an item whose quotes
all verify is admitted with a mechanical decision, any other waits for a person (design D1a)."""
import fnmatch
import json
import os
import re
import secrets
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from val3 import Val3Error
from val3.candidate import MIN_QUOTE, validate
from val3.config import Project, create_private, data_dir
from val3.db import vsql
from val3.extract import ExtractorError, cost, run_extractor
from val3.views import safe
from val3.writer import log_import, queue_import

MANIFEST_FORMAT = 1
FILE_CLIP = 20_000
MATERIAL_CLIP = 60_000
KINDS = ("fact", "decision", "preference")
SKIPS = ("derivable", "retired", "not-knowledge")
OUTCOMES = ("ready", "skipped", "superseded", "uncommitted", "extractor-failed", "invalid", "unreadable")
PROMPT = files("val3").joinpath("import_prompt.md").read_text(encoding="utf-8")
SCHEMA = {"type": "object", "required": ["candidate", "skip"], "properties": {
    "candidate": {"type": ["object", "null"]}, "skip": {"type": ["string", "null"]}}}
EXCLUDED_DIRS = ("knowledge/", "memory/", ".validated-memory/")
EXCLUDED_FILES = ("knowledge-index.md", "knowledge-extension.md", "validated-memory.md",
                  "validated-memory-profile.md", "journal.jsonl")
SECRET_NAMES = (".env*", "*.pem", "*.key", "id_*", "*secret*", "*.p12")
CODE_SUFFIXES = (".py", ".sql", ".yaml", ".yml", ".toml", ".json", ".sh", ".service", ".timer", ".ini",
                 ".cfg", ".conf", ".js", ".ts")
_SOURCE = re.compile(r"(knowledge|memory)/[^/]+\.md")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_BATCH = re.compile(r"[0-9a-f]{16}")
_PROVENANCE = re.compile(r"^\s*-\s+(\S.*?)\s+at\s+([0-9a-f]{40})\s*$", re.M)
_UNIT_ID = re.compile(r"knowledge/(kb-\d+)")
_UNIT_SUPERSEDED = re.compile(r"^\|\s*(kb-\d+)\s*\|\s*superseded by (kb-\d+)", re.M)
_NOTE_SUPERSEDED = re.compile(r"\]\(([^)\s]+\.md)\).*?superseded by \[\[([^\]]+)\]\]")


@dataclass(frozen=True)
class Cited:
    path: str
    commit: str
    label: str      # "source commit" (a unit's provenance) or "note snapshot" (a note's last change)
    date: str       # the commit's date, ISO 8601
    readable: bool  # False when the object is missing or binary


def excluded(path: str) -> bool:
    """validated-memory's own files and memory exports (citing them would be circular, design §4.4), and files
    that look like secrets or sit in a directory named like one: those are never cited, sent to the extractor
    or accepted as evidence."""
    parts = path.split("/")
    return (path.startswith(EXCLUDED_DIRS) or path in EXCLUDED_FILES or parts[-1] == "memory.html"
            or any(fnmatch.fnmatchcase(part.lower(), pattern) for part in parts for pattern in SECRET_NAMES))


def _is_source(path: str) -> bool:
    return bool(_SOURCE.fullmatch(path)) and path != "memory/MEMORY.md"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        # --no-optional-locks: `status` must not take the adopter's index.lock from a session committing there
        return subprocess.run(["git", "-C", str(repo), "--no-optional-locks", *args], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Val3Error(f"git did not run: {exc}") from None


def _out(repo: Path, *args: str) -> str:
    done = _git(repo, *args)
    if done.returncode != 0:
        raise Val3Error(f"git {args[0]} failed: {done.stderr.decode(errors='replace').strip()}")
    return done.stdout.decode("utf-8", errors="replace")


def _read(repo: Path, commit: str, path: str) -> tuple[str | None, str | None]:
    """(text, blob id) of `path` at `commit`; the text is None when the object is missing or binary."""
    done = _git(repo, "rev-parse", "--verify", "--quiet", f"{commit}:{path}")
    if done.returncode != 0:
        return None, None
    blob = done.stdout.decode().strip()
    data = _git(repo, "cat-file", "blob", blob).stdout
    if b"\0" in data:
        return None, blob
    try:
        return data.decode("utf-8").replace("\r\n", "\n"), blob
    except UnicodeDecodeError:
        return None, blob


def _tree(repo: Path, commit: str) -> list[str]:
    return [path for path in _out(repo, "ls-tree", "-r", "-z", "--name-only", commit).split("\0") if path]


def _date(repo: Path, commit: str, cache: dict) -> str:
    if commit not in cache:
        cache[commit] = _out(repo, "show", "-s", "--format=%cI", commit).strip()
    return cache[commit]


def _root(project: Project) -> Path:
    """The adopter's repository: the directory of val3.toml, which must be the repository root."""
    done = _git(project.root, "rev-parse", "--show-toplevel")
    top = done.stdout.decode().strip() if done.returncode == 0 else ""
    if not top or Path(top).resolve() != project.root.resolve():
        raise Val3Error(f"{project.root} is not the root of a git repository: "
                        "run `val3 import` at the adopter's root, next to its val3.toml")
    return project.root.resolve()


def _remote(repo: Path) -> str:
    done = _git(repo, "remote", "get-url", "origin")
    url = done.stdout.decode().strip() if done.returncode == 0 else ""
    # no user:token@ in a stored URL; the token may hold an @, and the scheme any case
    return re.sub(r"^([a-z][a-z0-9+.-]*://)[^/]*@", r"\1", url, flags=re.I)


def _frontmatter(text: str) -> str:
    match = re.match(r"---\n(.*?)\n---", text, re.S)
    return match[1] if match else ""


def _named(repo: Path, path: str, text: str) -> bool:
    """`path` appears in `text` as a whole path, bare, after `./` or `../`, or under the repository's absolute
    path: docs/README.md does not name README.md, and x.bak does not name x."""
    prefix = r"(?:" + re.escape(str(repo)) + r"/|(?:\.\.?/)*)"
    return path in text and bool(re.search(r"(?<![\w./-])" + prefix + re.escape(path) + r"(?![\w/-]|\.\w)", text))


def _message(exc: BaseException) -> str:
    """An error for the batch event without psycopg's DETAIL, which can quote a row's values."""
    diag = getattr(exc, "diag", None)
    return (getattr(diag, "message_primary", None) or str(exc)) or type(exc).__name__


def _cited(repo: Path, head: str, path: str, text: str, dates: dict) -> tuple[str, str, list[Cited]]:
    """(label, commit) of the source itself and the files it rests on (design §4.3); excluded paths are left out."""
    if path.startswith("knowledge/"):
        pairs = _PROVENANCE.findall(_frontmatter(text))
        label, commit, cited_label = "snapshot", head, "source commit"
    else:
        commit = _out(repo, "log", "-1", "--format=%H", head, "--", path).strip()
        pairs = [(p, commit) for p in _tree(repo, commit) if ("/" in p or "." in p) and _named(repo, p, text)]
        label, cited_label = "note snapshot", "note snapshot"
    cited, seen = [], set()
    for cited_path, cited_commit in pairs:
        if cited_path in seen or excluded(cited_path):
            continue
        seen.add(cited_path)
        if _git(repo, "cat-file", "-e", f"{cited_commit}^{{commit}}").returncode != 0:  # provenance names no commit here
            cited.append(Cited(cited_path, cited_commit, cited_label, "", False))
            continue
        content, _ = _read(repo, cited_commit, cited_path)
        cited.append(Cited(cited_path, cited_commit, cited_label, _date(repo, cited_commit, dates),
                           content is not None))
    return label, commit, cited


def _meta(repo: Path, head: str, path: str, blob: str, batch: str, label: str, commit: str, dates: dict) -> dict:
    return {"batch": batch, "source": path, "source_kind": "unit" if path.startswith("knowledge/") else "note",
            "source_blob": blob, "source_label": label, "source_commit": commit,
            "source_date": _date(repo, commit, dates), "snapshot": head}


def _material(repo: Path, meta: dict, text: str, older: list[tuple[str, str]], cited: list[Cited]) -> tuple[str, bool]:
    """What the extractor reads, and whether a file was clipped or left out by the limits."""
    parts = [f"SOURCE {meta['source']} ({meta['source_kind']}; {meta['source_label']} "
             f"{meta['source_commit'][:7]}, {meta['source_date']})\n{text}"]
    parts += [f"SUPERSEDED SOURCE {path} (the source above replaces it, wholly or in part)\n{old}"
              for path, old in older]
    size, capped = sum(len(part) for part in parts), False
    for item in cited:
        if not item.readable:
            continue
        content, _ = _read(repo, item.commit, item.path)
        clipped = content[:FILE_CLIP]
        if len(content) > FILE_CLIP:
            clipped += f"\n[… {len(content) - FILE_CLIP} more characters]"
            capped = True
        block = f"FILE {item.path} @ {item.commit[:7]} ({item.label}, {item.date})\n{clipped}"
        if size + len(block) > MATERIAL_CLIP:
            capped = True
            continue
        parts.append(block)
        size += len(block)
    return "\n\n".join(parts), capped


_FENCED = re.compile(r"^(```|~~~)[^\n]*\n(.*?)^\1", re.MULTILINE | re.DOTALL)
SOURCE_LABEL = "the source itself"


def verify(repo: Path, snapshot: str, cited: dict[str, Cited], path: str, quote: str,
           source: Cited | None = None) -> dict:
    """The outcome of one evidence item (design §4.7): the importer binds the commit, never the model. A quote of
    the `source` itself verifies only inside one of its fenced code blocks (what it records: a result, an example);
    its prose is the claim, not evidence, and stays unbound."""
    quote = quote.replace("\r\n", "\n").strip()
    result = {"path": path, "quote": quote, "status": "verified", "commit": None, "label": None, "date": None,
              "at_snapshot": None, "blob": None, "class": "code" if path.endswith(CODE_SUFFIXES) else "record"}
    item = cited.get(path)
    if source is not None and path == source.path:
        result.update(commit=source.commit, label=source.label, date=source.date)
        text, blob = _read(repo, source.commit, path)
        if text is None:
            result["status"] = "unreadable"
        elif len(quote) < MIN_QUOTE or not any(quote in block for _, block in _FENCED.findall(text)):
            result["status"] = "unbound"
        else:
            now, _ = _read(repo, snapshot, path)
            result.update(blob=blob, at_snapshot=now is not None and quote in now)
    elif excluded(path):
        result["status"] = "excluded"
    elif item is None:
        result["status"] = "unbound"
    else:
        result.update(commit=item.commit, label=item.label, date=item.date)
        text, blob = _read(repo, item.commit, path)
        if text is None:
            result["status"] = "unreadable"
        elif len(quote) < MIN_QUOTE or quote not in text:
            result["status"] = "mismatched"
        else:
            now, _ = _read(repo, snapshot, path)
            result.update(blob=blob, at_snapshot=now is not None and quote in now)
    return result


def build(repo: Path, head: str, cited: dict[str, Cited], candidate: object, meta: dict) -> dict:
    """The review payload for one extractor candidate: its verified file evidence plus the import block."""
    if not isinstance(candidate, dict):
        raise Val3Error("the extractor answered neither a candidate nor a known skip")
    if candidate.get("kind") not in KINDS:
        raise Val3Error("an imported candidate is a fact, decision or preference")
    items = candidate.get("evidence", [])
    if not isinstance(items, list) or not all(
            isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("quote"), str)
            for item in items):
        raise Val3Error('evidence must be a list of {"path", "quote"} objects')
    source = Cited(meta["source"], meta["source_commit"], SOURCE_LABEL, meta["source_date"], True)
    results = [verify(repo, head, cited, item["path"], item["quote"], source) for item in items]
    observed = candidate.get("observed")
    data = {key: candidate[key] for key in ("kind", "title", "statement", "keywords", "indexes") if key in candidate}
    data["evidence"] = [{"kind": "file", "path": r["path"], "commit": r["commit"], "quote": r["quote"]}
                        for r in results if r["status"] == "verified"]
    data["import"] = {**meta, "observed": observed if isinstance(observed, str) else None, "evidence": results}
    return validate(data, imported=True)


def _source(project: Project, repo: Path, head: str, path: str, batch: str, dates: dict,
            superseded: dict[str, str], predecessors: list[str]) -> dict:
    """One source through extraction and verification; its manifest record."""
    text, blob = _read(repo, head, path)
    record = {"path": path, "kind": "unit" if path.startswith("knowledge/") else "note", "blob": blob,
              "outcome": "ready", "by": superseded.get(path), "skip": None, "error": None, "capped": False,
              "cited": [], "candidate": None, "cost": 0.0, "dirty": False}
    if path in superseded:
        record["outcome"] = "superseded"
        return record
    if text is None:
        record["outcome"] = "unreadable"
        return record
    label, commit, cited = _cited(repo, head, path, text, dates)
    meta = _meta(repo, head, path, blob, batch, label, commit, dates)
    record["cited"] = [asdict(item) for item in cited]
    older = [(old, _read(repo, head, old)[0] or "") for old in predecessors]
    material, record["capped"] = _material(repo, meta, text, older, cited)
    try:
        result = run_extractor(project, material, PROMPT, SCHEMA)
    except ExtractorError as exc:
        record.update(outcome="extractor-failed", error=str(exc))
        return record
    record["cost"] = cost(result)
    answer = result["structured_output"]
    if answer.get("candidate") is None and answer.get("skip") in SKIPS:
        record.update(outcome="skipped", skip=answer["skip"])
        return record
    try:
        record["candidate"] = build(repo, head, {item.path: item for item in cited}, answer.get("candidate"), meta)
    except Val3Error as exc:
        record.update(outcome="invalid", error=str(exc))
    return record


def default_dir() -> Path:
    return data_dir() / "import"


def _write_manifest(manifest: dict, out: Path | None) -> Path:
    if out is None:
        directory = default_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        out = directory / f"{manifest['vault']}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{manifest['batch']}.json"
    fd = create_private(out)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=1)
    except BaseException as exc:  # failure or interruption: no partial manifest is left behind
        out.unlink(missing_ok=True)
        if isinstance(exc, OSError):
            raise Val3Error(f"cannot write {out}: {exc.strerror}") from None
        raise
    return out


def _summary(records: list[dict]) -> str:
    ready = [r for r in records if r["outcome"] == "ready"]
    evidence = [e for r in ready for e in r["candidate"]["import"]["evidence"]]

    def status(name: str) -> int:
        return sum(e["status"] == name for e in evidence)

    return "\n".join([
        "sources: " + ", ".join(f"{sum(r['outcome'] == o for r in records)} {o}" for o in OUTCOMES),
        f"evidence: {status('verified')} verified ({sum(e['at_snapshot'] is True for e in evidence)} at snapshot), "
        + ", ".join(f"{status(s)} {s}" for s in ("mismatched", "unbound", "excluded", "unreadable")),
        f"{len(ready)} candidates, {sum(not r['candidate']['evidence'] for r in ready)} unsupported; "
        f"{sum(bool(r.get('capped')) for r in records)} capped, "
        f"{sum(bool(r.get('dirty')) for r in records)} differ from the working tree; "
        f"cost ${sum(r.get('cost', 0.0) for r in records):.4f}"])


def _superseded(repo: Path, head: str, sources: list[str]) -> dict[str, str]:
    """{superseded source: the live source its chain ends at}, nearest to the live source first.

    Links come from knowledge-index.md and memory/MEMORY.md at the snapshot. A chain is followed to a source that has
    no link of its own; one that ends at a missing path or loops is dropped, so its members stay ordinary sources.
    """
    links: dict[str, str | None] = {}
    units = {match[1]: path for path in sources if (match := _UNIT_ID.match(path))}
    index, _ = _read(repo, head, "knowledge-index.md")
    for old, new in _UNIT_SUPERSEDED.findall(index or ""):
        if old in units:
            links[units[old]] = units.get(new)
    notes, _ = _read(repo, head, "memory/MEMORY.md")
    for old, new in _NOTE_SUPERSEDED.findall(notes or ""):
        if f"memory/{old}" in sources:
            links[f"memory/{old}"] = f"memory/{new.split('|')[0].strip()}.md"
    ranked = []
    for start in links:
        node, steps, seen = start, 0, set()
        while node in links and node not in seen:
            seen.add(node)
            node, steps = links[node], steps + 1
        if node in sources and node not in links:
            ranked.append((steps, start, node))
    return {start: live for _, start, live in sorted(ranked, key=lambda item: item[0])}


def _dirty(repo: Path) -> set[str]:
    """Sources that are not clean in the working tree, including the original path of a rename (design D5)."""
    fields = _out(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--",
                  "knowledge", "memory").split("\0")
    paths, i = set(), 0
    while i < len(fields):
        field = fields[i]
        i += 1
        if len(field) < 4:
            continue
        found = [field[3:]]
        if "R" in field[:2] or "C" in field[:2]:
            found += fields[i:i + 1]  # a rename or copy is followed by its original path
            i += 1
        paths.update(path for path in found if _is_source(path))
    return paths


def dry_run(project: Project, out: Path | None) -> str:
    """Extract and verify every source at one pinned commit; write the manifest; no database writes."""
    repo = _root(project)
    head = _out(repo, "rev-parse", "HEAD").strip()
    sources = sorted(path for path in _tree(repo, head) if _is_source(path))
    if not sources:
        raise Val3Error(f"{repo} has no knowledge/*.md or memory/*.md at {head[:7]}")
    dirty = _dirty(repo)
    superseded = _superseded(repo, head, sources)
    predecessors: dict[str, list[str]] = {}
    for old, new in superseded.items():
        predecessors.setdefault(new, []).append(old)
    batch, dates = secrets.token_hex(8), {}
    records = [_source(project, repo, head, path, batch, dates, superseded, predecessors.get(path, []))
               for path in sources]
    for record in records:
        record["dirty"] = record["path"] in dirty
    records += [{"path": path, "kind": "unit" if path.startswith("knowledge/") else "note", "outcome": "uncommitted"}
                for path in sorted(dirty - set(sources))]
    manifest = {"format": MANIFEST_FORMAT, "batch": batch, "vault": project.home, "project": project.name,
                "repository": str(repo), "remote": _remote(repo),
                "snapshot": {"commit": head, "date": _date(repo, head, dates)}, "sources": records}
    return f"manifest: {_write_manifest(manifest, out)}\n{_summary(records)}"


APPLY_COUNTS = ("admitted", "queued", "already-queued", "previously-rejected", "duplicate", "already-imported",
                "refused")


def _load(path: Path) -> dict:
    """A manifest whose snapshot and batch are hex before any of it reaches a git argument."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Val3Error(f"cannot read {path}: {exc.strerror}") from None
    except (ValueError, RecursionError):
        manifest = None
    snapshot = manifest.get("snapshot") if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT
            or not isinstance(manifest.get("sources"), list) or not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("commit"), str) or not _COMMIT.fullmatch(snapshot["commit"])
            or not isinstance(manifest.get("batch"), str) or not _BATCH.fullmatch(manifest["batch"])
            or not isinstance(manifest.get("repository"), str)):
        raise Val3Error(f"{path} is not a val3 import manifest (format {MANIFEST_FORMAT})")
    return manifest


def _shown(value: object) -> str:
    """Manifest text for a message: escapes (including line breaks), so it cannot forge a line or redraw the screen."""
    return safe(str(value)).replace("\n", "\\n")


def _recheck(repo: Path, head: str, batch: str, record: object, dates: dict) -> tuple[str | None, dict | None]:
    """(why a manifest record no longer matches the repository, None) or (None, the payload to queue), design §4.9:
    the source blob, the cited files and every quote are derived again from git; only the extractor's wording is
    taken as is. The payload is the rebuilt one: the manifest's own text is never queued."""
    if not isinstance(record, dict) or not isinstance(record.get("path"), str) or not _is_source(record["path"]):
        return "malformed record", None
    path, candidate = record["path"], record.get("candidate")
    text, blob = _read(repo, head, path)
    if text is None or blob != record.get("blob"):
        return "the source differs from the snapshot", None
    label, commit, cited = _cited(repo, head, path, text, dates)
    try:
        answer = {key: candidate[key] for key in ("kind", "title", "statement", "keywords", "indexes")
                  if key in candidate}
        answer["evidence"] = [{"path": item["path"], "quote": item["quote"]}
                              for item in candidate["import"]["evidence"]]
        answer["observed"] = candidate["import"].get("observed")
        rebuilt = build(repo, head, {item.path: item for item in cited}, answer,
                        _meta(repo, head, path, blob, batch, label, commit, dates))
    except (Val3Error, KeyError, TypeError, AttributeError):
        return "malformed record", None
    return (None, rebuilt) if rebuilt == candidate else ("the manifest does not match the repository", None)


def _imported_blobs(conn, vault: str) -> set[str]:
    return {blob for (blob,) in conn.execute(vsql(vault, """
        SELECT payload->'import'->>'source_blob' FROM {v}.review_item r WHERE reason = 'import'
          AND NOT EXISTS (SELECT 1 FROM {v}.review_decision d WHERE d.review_id = r.id AND d.decision = 'retired')"""))}


def apply(conn, project: Project, path: Path, by: str) -> str:
    """Admit or queue a manifest's ready candidates after checking each against the repository again."""
    manifest = _load(path)
    if manifest.get("vault") != project.home:
        raise Val3Error(f"{path} is for vault {_shown(manifest.get('vault'))}, not the home vault {project.home}")
    repo = _root(project)
    if manifest["repository"] != str(repo):
        raise Val3Error(f"{path} was made in {_shown(manifest['repository'])}, not in {repo}")
    head, batch = manifest["snapshot"]["commit"], manifest["batch"]
    if _git(repo, "cat-file", "-e", f"{head}^{{commit}}").returncode != 0:
        raise Val3Error(f"snapshot {head[:7]} of {path} is not in {repo}")
    imported, dates = _imported_blobs(conn, project.home), {}
    counts, lines, failure = dict.fromkeys(APPLY_COUNTS, 0), [], None
    try:
        for record in manifest["sources"]:
            if not isinstance(record, dict) or record.get("outcome") != "ready":
                continue
            reason, payload = _recheck(repo, head, batch, record, dates)
            if reason:
                counts["refused"] += 1
                lines.append(f"refused {_shown(record.get('path'))}: {reason}")
            elif record["blob"] in imported:
                counts["already-imported"] += 1
            else:
                counts[queue_import(conn, project, payload, by).status] += 1
                imported.add(record["blob"])
    except BaseException as exc:
        failure = exc
        raise
    finally:  # the batch event also records what a failed run had queued before it stopped
        try:
            detail = {"batch": batch, "repository": manifest["repository"], "remote": _remote(repo),
                      "snapshot": head, **counts}
            if failure is not None:
                detail["error"] = _message(failure)
            log_import(conn, project, detail)
        except Exception:
            if failure is None:
                raise  # nothing else is failing: a lost event is the error
    lines.append(f"batch {batch}: " + ", ".join(f"{n} {name.replace('-', ' ')}" for name, n in counts.items()))
    return "\n".join(lines)
