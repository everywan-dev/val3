"""`val3 eval`: the semantic floor measured on a labelled set, and drafts of a set from the prompt log.

A labelled set holds one JSON object per line: `query`, optional `previous` (the end of the previous
assistant turn), `relevant`, the refs of the entries needed at that moment (`[]` when a person
decided none was; `null` while not labelled), and optional `at`, the moment of the prompt: the vault
is read as it was then, so later admissions, supersessions and withdrawals do not change the result.
"""
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from val3 import Val3Error
from val3.config import CONFIG_FILE, parse_ref
from val3.embedding import embed
from val3.search import PROMPT_CAP, candidates, query_texts, search, select

FLOORS = tuple(round(0.40 + 0.05 * i, 2) for i in range(9))
TIMEOUT = 30.0


@dataclass(frozen=True)
class Case:
    query: str
    previous: str
    relevant: frozenset[str]
    at: datetime | None


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise Val3Error(f"cannot read {path}: {exc.strerror}") from None


def load_cases(path: Path, vaults: tuple[str, ...]) -> list[Case]:
    cases = []
    for number, line in enumerate(_lines(path), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except (ValueError, RecursionError):
            raise Val3Error(f"{path}:{number}: not valid JSON") from None
        data = data if isinstance(data, dict) else {}
        query, previous, relevant = data.get("query"), data.get("previous", ""), data.get("relevant")
        if "relevant" in data and relevant is None:
            raise Val3Error(f"{path}:{number}: not labelled yet (relevant is null)")
        if (not isinstance(query, str) or not query.strip() or not isinstance(previous, str)
                or not isinstance(relevant, list) or not all(isinstance(ref, str) for ref in relevant)):
            raise Val3Error(f"{path}:{number}: expected query (text), optional previous (text), "
                            "relevant (list of entry refs) and optional at (ISO time)")
        for ref in relevant:
            try:
                vault, _ = parse_ref(ref)
            except Val3Error as exc:
                raise Val3Error(f"{path}:{number}: {exc}") from None
            if vault not in vaults:
                raise Val3Error(f"{path}:{number}: {ref.strip()} is in vault {vault}, "
                                f"which {CONFIG_FILE} does not declare")
        try:
            at = datetime.fromisoformat(data["at"]) if data.get("at") is not None else None
        except (TypeError, ValueError):
            raise Val3Error(f"{path}:{number}: at must be an ISO time with its offset") from None
        if at is not None and at.tzinfo is None:
            raise Val3Error(f"{path}:{number}: at must be an ISO time with its offset")
        cases.append(Case(query.strip(), previous, frozenset(ref.strip() for ref in relevant), at))
    if not cases:
        raise Val3Error(f"{path}: no cases")
    return cases


def evaluate(conn, project, cases: list[Case]) -> str:
    settings = project.embedding
    if settings is None:
        raise Val3Error(f"no [embedding] table in {project.root / CONFIG_FILE}")
    pools, baseline, timings = [], [], []
    for case in cases:
        started = time.monotonic()
        space, vectors = embed(settings, query_texts(case.query, case.previous), TIMEOUT)
        timings.append(time.monotonic() - started)
        # Every candidate at any floor the hook may deliver; select() below is the hook's own rule (search.find)
        pools.append(candidates(conn, project.vaults, space, vectors, case.query, -1.0, PROMPT_CAP, case.at,
                                delivery=True))
        baseline.append([hit.ref for hit in search(conn, project.vaults, case.query, PROMPT_CAP, as_of=case.at,
                                                   delivery=True)])
    lines = [_line("floor", "injected", "needed", "served", "silent"),
             _line(*_measure("full-text", cases, baseline))]
    for floor in sorted(set(FLOORS) | {settings.floor}):  # the configured floor has a row even off the grid
        injected = [[hit.ref for hit in select(pool, floor, PROMPT_CAP)] for pool in pools]
        label = f"{floor:.2f}" + ("*" if abs(floor - settings.floor) < 1e-9 else "")
        lines.append(_line(*_measure(label, cases, injected)))
    lines.append(f"latency: mean {1000 * sum(timings) / len(timings):.0f} ms, "
                 f"max {1000 * max(timings):.0f} ms over {len(timings)} prompts")
    return "\n".join(lines)


def _measure(label: str, cases: list[Case], injected: list[list[str]]) -> tuple[str, ...]:
    answered = [(case, refs) for case, refs in zip(cases, injected) if case.relevant]
    unanswered = [refs for case, refs in zip(cases, injected) if not case.relevant]
    total = sum(len(refs) for refs in injected)
    needed = sum(len(case.relevant.intersection(refs)) for case, refs in zip(cases, injected))
    served = sum(1 for case, refs in answered if case.relevant.intersection(refs))
    silent = sum(1 for refs in unanswered if not refs)
    share = f"{100 * needed / total:.0f}%" if total else "-"
    return label, str(total), share, f"{served}/{len(answered)}", f"{silent}/{len(unanswered)}"


def _line(*cells: str) -> str:
    return f"{cells[0]:<10}" + "".join(f"{cell:>10}" for cell in cells[1:])


def draft(path: Path) -> str:
    """An unlabelled set from a prompt log: one case per distinct prompt and previous turn, with the time
    and session of its first occurrence; `relevant` is null until a person labels it."""
    seen: set[tuple[str, str]] = set()
    out = []
    for line in _lines(path):
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(record, dict) or not isinstance(record.get("prompt"), str):
            continue
        key = (record["prompt"], record.get("previous") if isinstance(record.get("previous"), str) else "")
        if key in seen:
            continue
        seen.add(key)
        injected = [item.get("ref") for item in record.get("injected") or [] if isinstance(item, dict)]
        out.append(json.dumps({"at": record.get("at"), "session": record.get("session"), "query": key[0],
                               "previous": key[1], "relevant": None, "injected": injected}, ensure_ascii=False))
    return "\n".join(out)
