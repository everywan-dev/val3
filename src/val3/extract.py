"""Extraction: unseen transcript lines -> extractor (`claude -p`) -> candidates -> writer."""
import hashlib
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path

import psycopg

from val3 import Val3Error, catalogue, inbox, indexes, neighbour, writer
from val3.candidate import FORMAT, validate
from val3.config import Project, parse_ref, state_dir
from val3.db import backings, has_neighbours
from val3.embedding import EmbeddingError, embed, entry_text
from val3.inbox import NOTE_CLIP
from val3.search import search
from val3.transcript import Chunk, condense, identities
from val3.views import show_entry
from val3.writer import current, store_vectors, submit

PROMPT_VERSION = 7
PROMPT = files("val3").joinpath("extractor_prompt.md").read_text(encoding="utf-8").replace("{FORMAT}", FORMAT)
# [extractor] focus (measured 2026-11-15 on two windows): appended to PROMPT, which stays as it is without the switch
FOCUS = files("val3").joinpath("extractor_focus.md").read_text(encoding="utf-8")
TIMEOUT = 600
SCHEMA = {"type": "object", "required": ["candidates"], "properties": {"candidates": {"type": "array", "items": {
    "type": "object", "required": ["candidate"],
    "properties": {"note": {"type": ["string", "null"]}, "candidate": {"type": "object"}}}}}}
STATUSES = ("admitted", "duplicate", "repeat", "verified", "queued", "already-queued", "previously-rejected",
            "rejected")
COUNTED = ("unverified", "quote-failed")  # of them: admitted without backing; lost their evidence (ADR 0012, am. 1)


class ExtractorError(Exception):
    """The extractor gave no usable answer; the queue entry is retried."""


def extract(conn, project: Project, transcript: Path, session: str, cwd: Path) -> dict:
    """Send the unseen lines of a session transcript, and the notes it wrote, to the extractor.

    The watermark advances after each chunk, so a failure never resends finished chunks. Notes go
    with the last chunk; with no new lines they go with the end of the transcript.

    Reading starts after the cursor and after the records that follow it already processed for this project
    under another session (a resumed session repeats the earlier one's). Each record is processed at least
    once: a crash after a chunk's entries are written and before its records are remembered sends that chunk
    again, and its entries are then refused as duplicates. The caller holds the drain lock (`pending.drain_lock`),
    which also guards the processed set."""
    memdir = inbox.memory_dir(cwd)
    private = (str(memdir),)
    notes = inbox.changed_notes(memdir, session)
    ids = identities(transcript)
    lines, last = _watermark(session)
    if lines > len(ids) or (last is not None and lines and ids[lines - 1][0] != last):
        lines = 0  # the transcript was rewritten: read it again from its start, after the processed prefix
    # after the cursor too: the session that resumed this one may have been captured first
    skipped = _prefix(ids[lines:], _processed(project))
    start = lines + skipped
    _remember(project, ids[lines:start])  # dated again, so a chain of resumed sessions outlives remember_days
    chunks, total = condense(transcript, start, private=private, lines=len(ids))
    if notes and not chunks:
        chunks = condense(transcript, 0, private=private, lines=len(ids))[0][-1:]
    counts = dict.fromkeys(STATUSES + COUNTED, 0) | {"calls": 0, "cost_usd": 0.0, "skipped": skipped}
    mark = start  # first line not yet remembered as processed
    refs: dict[str, list[str]] = {name: [] for name, _ in notes}
    excluded = indexes.excluded(conn, project, project.home)  # D1: names like the project or a vault
    vocabulary = {name: n for name, n in indexes.counts(conn, [project.home]).items() if name not in excluded}
    descriptions = {v: note.description for v, note in catalogue.notes(conn).items()} if project.specialized else {}
    for i, chunk in enumerate(chunks):
        # The user's words alone ("continue") often find nothing; the slice's own text finds what the agent did.
        existing = list({(hit.vault, hit.id): hit for query in (chunk.text[:5_000], chunk.user_text[:5_000])
                         if query.strip() for hit in search(conn, project.vaults, query, limit=30)}.values())
        last = i == len(chunks) - 1
        unverified = {(vault, entry_id) for vault in project.vaults for entry_id, backing in
                      backings(conn, vault, [hit.id for hit in existing if hit.vault == vault]).items()
                      if backing == "unverified"}
        result = run_extractor(project, _material(project, existing, chunk.text, notes if last else (), vocabulary,
                                                  descriptions, unverified),
                               PROMPT + FOCUS if project.extractor_focus else PROMPT)
        if not isinstance(result["structured_output"].get("candidates"), list):
            raise ExtractorError("extractor output is not the expected JSON")
        counts["calls"] += 1
        counts["cost_usd"] += cost(result)
        items = result["structured_output"]["candidates"]
        candidates = [item.get("candidate") if isinstance(item, dict) else item for item in items]
        # the target vault first (design 4.1): each group is compared and submitted as seen from its own vault
        targets: list[Project | None] = []
        for n, data in enumerate(candidates):
            if not isinstance(data, dict) or "vault" not in data:
                targets.append(project)
                continue
            candidates[n] = {k: v for k, v in data.items() if k != "vault"}
            try:
                targets.append(project.writing(data["vault"]))
            except Val3Error as exc:
                title = data.get("title")
                writer._log_best_effort(conn, project.home, "invalid", writer.origin(project, session),
                                        {"title": title if isinstance(title, str) else None, "error": str(exc)})
                targets.append(None)
        plans: list[Plan | None] = [None] * len(candidates)
        groups: dict[str, list[int]] = {}
        for n, target in enumerate(targets):
            if target is not None:
                groups.setdefault(target.home, []).append(n)
        for group in groups.values():
            found, calls, spent = compare(conn, targets[group[0]], chunk, [candidates[n] for n in group], session)
            for n, plan in zip(group, found):
                plans[n] = plan
            counts["calls"] += calls
            counts["cost_usd"] += spent
        for item, data, plan, target in zip(items, candidates, plans, targets):
            note = item.get("note") if isinstance(item, dict) and isinstance(item.get("note"), str) else None
            if target is None:
                counts["rejected"] += 1  # names a vault this project does not write; logged above
                continue
            if plan:  # the second call decides "supersedes" (design K2b)
                data = {**data, "supersedes": plan.supersedes}
            try:
                outcome = submit(conn, target, data, transcript, session, private, plan.settling if plan else None,
                                 via="capture")
            except psycopg.OperationalError:
                raise
            except (Val3Error, psycopg.Error):
                counts["rejected"] += 1  # invalid, discarded (not upgraded; the vault logs discards), refused by the database
                continue
            counts[outcome.status] += 1
            counts["unverified"] += outcome.unverified
            counts["quote-failed"] += bool(outcome.failed)
            if note in refs and outcome.status in ("admitted", "duplicate", "repeat", "verified") and not outcome.reason:
                refs[note].append(outcome.ref)  # live entries only; backing is checked at the end
        # a repeat of an entry another candidate of the slice replaced points at the replacing entry
        refs = {name: [current(conn, parse_ref(ref)[0], ref) for ref in found] for name, found in refs.items()}
        _remember(project, ids[mark:chunk.end])
        mark = max(mark, chunk.end)
        _set_watermark(session, chunk.end, ids)
    _remember(project, ids[mark:total])
    _set_watermark(session, total, ids)
    # the note says "(verified)": only for an entry, at the end of its supersessions, with backing (ADR 0012, am. 1)
    refs = {name: [ref for ref in found if _backed(conn, ref)] for name, found in refs.items()}
    inbox.settle(memdir, notes, refs)
    if project.embedding is not None:
        try:  # entries are admitted with their vector (design K3a); this stores the ones a failure left without
            for vault in project.own:
                store_vectors(conn, project.writing(vault))
        except EmbeddingError as exc:  # the first failure stops the rest; `val3 embed` retries
            counts["embedding_error"] = str(exc)
    return counts


def _backed(conn, ref: str) -> bool:
    """Evidence or attestation; any entry of a vault not upgraded."""
    vault, entry_id = parse_ref(ref)
    return backings(conn, vault, [entry_id]).get(entry_id, "evidence") in ("evidence", "attested")


def run_extractor(project: Project, material: str, prompt: str = PROMPT, schema: dict = SCHEMA) -> dict:
    command = [*shlex.split(os.environ.get("VAL3_EXTRACTOR", "claude")), "-p",
               "--model", project.extractor_model, "--setting-sources", "", "--tools", "",
               "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
               "--output-format", "json", "--json-schema", json.dumps(schema), "--system-prompt", prompt]
    workdir = state_dir() / "extractor"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        done = subprocess.run(command, input=material, capture_output=True, text=True, timeout=TIMEOUT,
                              cwd=workdir, env={**os.environ, "VAL3_EXTRACTING": "1"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExtractorError(f"extractor did not run: {exc}") from None
    if done.returncode != 0:
        raise ExtractorError(f"extractor exited {done.returncode}: {done.stderr.strip()[:300]}")
    try:
        result = json.loads(done.stdout)
        answer = result["structured_output"]
    except (ValueError, KeyError, TypeError, RecursionError):
        raise ExtractorError("extractor output is not the expected JSON") from None
    if result.get("is_error") or not isinstance(answer, dict):
        raise ExtractorError("extractor output is not the expected JSON")
    return result


def cost(result: dict) -> float:
    try:
        return float(result.get("total_cost_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def summary(counts: dict) -> str:
    if not counts["calls"]:
        return "nothing new"
    return (f"admitted {counts['admitted']}, duplicate {counts['duplicate']}, repeat {counts['repeat']}, "
            + (f"verified {counts['verified']}, " if counts["verified"] else "") +
            f"queued {counts['queued']}, "
            f"already queued {counts['already-queued']}, "
            f"previously rejected {counts['previously-rejected']}, rejected {counts['rejected']}, "
            f"unverified {counts['unverified']}, quote-failed {counts['quote-failed']}; "
            f"{counts['calls']} extractor calls, ${counts['cost_usd']:.4f}")


# The second extractor call (design K2): each candidate with close entries, shown whole, against the slice.
SETTLE_PROMPT = files("val3").joinpath("settle_prompt.md").read_text(encoding="utf-8")
SETTLE_SCHEMA = {"type": "object", "required": ["decisions"], "properties": {"decisions": {"type": "array", "items": {
    "type": "object", "required": ["candidate", "decision"], "properties": {
        "candidate": {"type": "integer"}, "decision": {"enum": ["new", "repeat", "replaces", "undecided"]},
        "refs": {"type": "array", "items": {"type": "string"}}}}}}}


@dataclass(frozen=True)
class Plan:
    supersedes: list[str]     # what the candidate is submitted with
    settling: writer.Settling


def compare(conn, project: Project, chunk: Chunk, items: list[object],
            session: str | None = None) -> tuple[list[Plan | None], int, float]:
    """One plan per candidate of the first answer (None: no vector, submit as today), the extractor calls made and
    their cost. Candidates of one answer are not compared with each other (K4). A compared candidate never keeps
    the first answer's "supersedes": the second answer decides it (K2b), and the writer enforces the date (K2a)."""
    plans: list[Plan | None] = [None] * len(items)
    if project.embedding is None or neighbour.off(project) or not has_neighbours(conn, project.home):  # K5
        return plans, 0, 0.0
    valid = {}
    for i, item in enumerate(items):
        try:
            valid[i] = validate(item)
        except Val3Error:
            pass
    if not valid:
        return plans, 0, 0.0
    try:
        space, vectors = embed(project.embedding, [entry_text(c["title"], c["statement"]) for c in valid.values()],
                               writer.VECTOR_TIMEOUT)
    except EmbeddingError:
        return plans, 0, 0.0
    base = {"slice_at": _instant(chunk.at), "by": f"extractor {session}"}
    neighbours: dict[int, list[int]] = {}
    shown = []
    for (i, candidate), vector in zip(valid.items(), vectors):
        close = neighbour.near(conn, project, space, vector, candidate["kind"], candidate["supersedes"])
        plans[i] = Plan([], writer.Settling(space, vector, **base))
        if close:
            neighbours[i] = [n.id for n in close]
            shown += ["", f"### Candidate {i}", json.dumps(candidate, indent=1, ensure_ascii=False)]
            for n in close:
                why = 'named in "supersedes"' if n.score is None else f"similarity {n.score:.2f}"
                shown += [f"#### Close entry {project.home}#{n.id} ({why})", show_entry(conn, project.home, n.id)]
    if not shown:
        return plans, 0, 0.0
    material = "\n".join([f"# Session slice of project {project.name} (home vault {project.home}); "
                           f"last record {chunk.at or 'unknown'}", "", "## Candidates with close entries", *shown[1:],
                           "", "## Transcript", chunk.text])
    try:
        result = run_extractor(project, material, SETTLE_PROMPT, SETTLE_SCHEMA)
    except ExtractorError:  # K3a: the candidates go as today, with their vectors
        return plans, 1, 0.0
    for i, (decision, ids) in _decisions(result["structured_output"], project.home, neighbours).items():
        settling = plans[i].settling
        if decision == "repeat":
            plans[i] = Plan([], replace(settling, repeat_of=ids[0]))
        elif decision in ("new", "replaces"):
            ids = ids if decision == "replaces" else []
            plans[i] = Plan([f"{project.home}#{n}" for n in ids],
                            replace(settling, compared=tuple(neighbours[i]), replaces=tuple(ids)))
    return plans, 1, cost(result)


def _decisions(answer: dict, vault: str, neighbours: dict[int, list[int]]) -> dict[int, tuple[str, list[int]]]:
    """Candidate index -> (decision, neighbour ids it names) for each well-formed decision of the second answer;
    a candidate whose decision fails a check, or that is named twice, is left out (undecided)."""
    decisions = answer.get("decisions")
    found: dict[int, tuple[str, list[int]] | None] = {}
    for d in decisions if isinstance(decisions, list) else []:
        i = d.get("candidate") if isinstance(d, dict) else None
        if type(i) is not int or i not in neighbours:
            continue
        refs = d.get("refs", [])
        known = {f"{vault}#{n}": n for n in neighbours[i]}
        ok = (i not in found and d.get("decision") in ("new", "repeat", "replaces", "undecided")
              and isinstance(refs, list) and all(isinstance(r, str) and r in known for r in refs)
              and {"repeat": len(refs) == 1, "replaces": len(refs) >= 1}.get(d["decision"], True))
        found[i] = (d["decision"], list(dict.fromkeys(known[r] for r in refs))) if ok else None
    return {i: plan for i, plan in found.items() if plan is not None}


def _instant(at: str | None) -> datetime | None:
    """The slice's date as an aware datetime; None when absent or unreadable (then nothing is replaced)."""
    try:
        moment = datetime.fromisoformat(at) if isinstance(at, str) else None
    except ValueError:
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def _material(project: Project, existing, transcript_text: str, notes=(),
              vocabulary: dict[str, int] | None = None, descriptions: dict[str, str] | None = None,
              unverified=frozenset()) -> str:
    lines = [f"# Session slice of project {project.name} (home vault {project.home})", ""]
    if project.specialized:  # without specialized vaults the material is unchanged (PROMPT_VERSION stays)
        lines += ["# Vaults of this project"]
        lines += [f"- {v}: {' '.join(((descriptions or {}).get(v) or '').split()) or '(no description)'}"
                  for v in project.own]
        lines += ['Give "vault" only for a candidate that belongs to a specialized vault; '
                  'leave it out for the home vault.', ""]
    lines += ["## Knowledge indexes",
              "Reuse one of these names; a new name only when none fits; never the project's name."]
    lines += [f"- {name} ({n})" for name, n in sorted((vocabulary or {}).items(), key=lambda item: (-item[1], item[0]))
              ] or ["(none yet)"]
    lines += ["", "## Existing entries",
             "Already stored: do not propose them again, except a fact, preference or decision marked (unverified) "
             "when the slice holds evidence for it, with the same kind, title and statement (given in full, as a "
             'JSON string). A recipe cites no entry marked (unverified). Use "supersedes" only when the slice proves '
             "one wrong."]
    lines += [f"- {hit.ref} [{hit.kind}] {hit.title}" + (" (unverified)" if (hit.vault, hit.id) in unverified else "")
              + f": {_statement(hit, (hit.vault, hit.id) in unverified)}" for hit in existing] or ["(none found)"]
    if notes:
        lines += ["", "## Memory notes",
                  "Written by the session's agent: claims, not evidence. Propose a candidate from a note only "
                  'when the transcript proves it, and set "note" to its file name.']
        for name, text in notes:
            lines += ["", f"### {name}", text[:NOTE_CLIP]]
    lines += ["", "## Transcript", transcript_text]
    return "\n".join(lines)


def _statement(hit, unverified: bool) -> str:
    """Whole and exact for an unverified fact, preference or decision (saving its text again with evidence verifies
    it); otherwise on one line and cut."""
    if unverified and hit.kind in ("fact", "preference", "decision"):
        return json.dumps(hit.statement, ensure_ascii=False)
    return " ".join(hit.statement.split())[:300]


def _watermark_path(session: str) -> Path:
    return state_dir() / "sessions" / f"{hashlib.sha256(session.encode()).hexdigest()}.json"


def _watermark(session: str) -> tuple[int, str | None]:
    """Lines read, and the identity of the last one (None in cursors written before it was kept)."""
    try:
        data = json.loads(_watermark_path(session).read_text())
        last = data.get("last")
        return int(data["lines"]), last if isinstance(last, str) else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return 0, None


def _set_watermark(session: str, lines: int, ids: list[tuple[str, bool]]) -> None:
    path = _watermark_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"lines": lines, "last": ids[lines - 1][0] if 0 < lines <= len(ids) else None}))
    os.replace(tmp, path)


def _prefix(ids: list[tuple[str, bool]], done: set[str]) -> int:
    """Lines of the longest leading run of messages already processed; metadata records do not break it, and the
    run ends at its last processed message. Nothing after the run is filtered."""
    end = 0
    for n, (identity, message) in enumerate(ids):
        if not message:
            continue
        if identity not in done:
            break
        end = n + 1
    return end


def _processed_path(project: Project) -> Path:
    return state_dir() / "processed" / f"{re.sub(r'[^A-Za-z0-9_.-]', '-', project.name)}.tsv"


def _processed(project: Project) -> set[str]:
    """Identities of the records processed for the project in the last `remember_days`; older lines are dropped
    by an atomic rewrite. One line per record: its date (UTC), a tab, its identity."""
    path = _processed_path(project)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=project.remember_days)).isoformat()
    kept = [line for line in lines if line.partition("\t")[2] and line.partition("\t")[0] >= cutoff]
    if len(kept) < len(lines):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        os.replace(tmp, path)
    return {line.partition("\t")[2] for line in kept}


def _remember(project: Project, ids: list[tuple[str, bool]]) -> None:
    if not ids:
        return
    path = _processed_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).date().isoformat()
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("".join(f"{day}\t{identity}\n" for identity, _ in ids))
