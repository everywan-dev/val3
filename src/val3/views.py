"""Read-only renderings of vault content for the CLI and the hooks."""
import json
import re
import shlex
from dataclasses import dataclass
from collections.abc import Collection, Mapping, Sequence

import psycopg

from val3 import Val3Error, catalogue, indexes, neighbour
from val3.config import parse_ref
from val3.db import backings, has_withdrawals, live, upgraded, vsql
from val3.recheck import Change, own_marks
from val3.text import _one_line, safe  # noqa: F401  (re-exported for the CLI and the hooks)
from val3.writer import NOTICES, RETIRED, batch_entries


def predecessors(conn, vault: str, entry_id: int) -> list[int]:
    """The ids an entry supersedes, ascending."""
    return [old_id for (old_id,) in conn.execute(vsql(vault, """
        SELECT old_id FROM {v}.supersession WHERE new_id = %s ORDER BY old_id"""), (entry_id,))]


def show_entry(conn, vault: str, entry_id: int, changes: Sequence[Change] = (), head: str | None = None,
               near: Sequence[tuple[int, float]] = ()) -> str:
    row = conn.execute(vsql(vault, """
        SELECT kind, title, statement, keywords, origin_project, origin_session, origin_machine, created_at
        FROM {v}.entry WHERE id = %s"""), (entry_id,)).fetchone()
    if row is None:
        raise Val3Error(f"no entry {vault}#{entry_id}")
    kind, title, statement, keywords, project, session, machine, created = row
    lines = [f"{vault}#{entry_id}  [{kind}]  {title}", "", statement, ""]
    lines[1:1] = [f"TO RE-CHECK: cites {change.path}, which is {change.cited}" if change.cited else
                  f"TO RE-CHECK: {_one_line(change.path)} " + (f"is not in HEAD {head[:12]}" if change.missing
                                                              else f"changed at HEAD {head[:12]}")
                  for change in changes]
    lines[1:1] = [f"TO SETTLE: close to {vault}#{other} ({score:.2f})" for other, score in near]
    backing = backings(conn, vault, [entry_id]).get(entry_id)  # ADR 0012 §1; None on a vault not upgraded
    if backing in ("unverified", "not readable"):
        lines.insert(1, "UNVERIFIED: no evidence and no attestation" if backing == "unverified"
                     else "BACKING NOT READABLE: this role cannot read evidence or attestation")
    newer = conn.execute(vsql(vault, "SELECT new_id FROM {v}.supersession WHERE old_id = %s"), (entry_id,)).fetchone()
    if newer:
        lines.insert(1, f"SUPERSEDED by {vault}#{newer[0]}")
    if has_withdrawals(conn, vault):
        # to_jsonb: a table that predates the evidence column still reads
        withdrawn = conn.execute(vsql(vault, """
            SELECT cause, reason, withdrawn_by, created_at, to_jsonb(w)->'evidence' FROM {v}.withdrawal w
            WHERE entry_id = %s"""), (entry_id,)).fetchone()
        if withdrawn:
            cause, reason, by, at, evidence = withdrawn
            lines[1:1] = [f"WITHDRAWN ({cause}) {at:%Y-%m-%d} by {by}: {reason}", *withdrawal_evidence(evidence)]
    older = predecessors(conn, vault, entry_id)
    if older:
        lines.append("supersedes: " + ", ".join(f"{vault}#{old_id}" for old_id in older))
    names = [name for (name,) in conn.execute(vsql(vault, f"""
        SELECT r.name FROM {indexes.resolved(conn, vault)} r WHERE r.entry_id = %s ORDER BY r.name"""),
        (entry_id,))]
    if names:
        lines.append("indexes: " + ", ".join(names))
    if keywords:
        lines.append("keywords: " + keywords)
    lines.append(f"origin: {project} / session {session or '-'} / {machine} / {created:%Y-%m-%d %H:%M}")
    evidence = conn.execute(vsql(vault, """
        SELECT kind, reproduction, quote FROM {v}.evidence WHERE entry_id = %s ORDER BY id"""),
        (entry_id,)).fetchall()
    if evidence:
        lines.append("evidence:")
        for evidence_kind, reproduction, quote in evidence:
            lines.append(f"  - {evidence_kind}: {_one_line(reproduction)}" if reproduction
                         else f"  - {evidence_kind}")
            lines.extend(f"      > {line}" for line in quote.splitlines())
    elif backing == "attested":
        for by, how, at in conn.execute(vsql(vault, """
                SELECT attested_by, how, created_at FROM {v}.attestation WHERE entry_id = %s ORDER BY id"""),
                (entry_id,)):
            lines.append(f"evidence: none — attested by a person: {_one_line(by)}, {_one_line(how)}, on {at:%Y-%m-%d}")
    elif backing is None:
        attested = conn.execute(vsql(vault, """
            SELECT d.review_id, d.decided_by, d.created_at FROM {v}.review_decision d
            JOIN {v}.review_item i ON i.id = d.review_id
            WHERE d.entry_id = %s AND i.reason = 'import' ORDER BY d.review_id"""), (entry_id,)).fetchone()
        if attested:  # an imported entry admitted with no file evidence rests on the approver's word
            review_id, by, at = attested
            lines.append(f"evidence: none — attested in review #{review_id} by {by} on {at:%Y-%m-%d}")
    if kind == "recipe":
        lines.extend(recipe_lines(conn, vault, entry_id))
    if kind == "tool":
        lines.extend(tool_lines(conn, vault, entry_id))

    return safe("\n".join(lines))


def recipe_lines(conn, vault: str, recipe_id: int) -> list[str]:
    lines = []
    parameters = conn.execute(vsql(vault, """
        SELECT name, description FROM {v}.recipe_parameter WHERE recipe_id = %s ORDER BY name"""),
        (recipe_id,)).fetchall()
    if parameters:
        lines.append("parameters:")
        lines.extend(f"  - {name}: {description}" if description else f"  - {name}"
                     for name, description in parameters)
    lines.append("steps:")
    steps = conn.execute(vsql(vault, """
        SELECT s.position, s.action, s.tool_id,
               coalesce(array_agg(f.fact_id ORDER BY f.fact_id) FILTER (WHERE f.fact_id IS NOT NULL),
                        ARRAY[]::bigint[])
        FROM {v}.recipe_step s
        LEFT JOIN {v}.step_fact f ON f.recipe_id = s.recipe_id AND f.position = s.position
        WHERE s.recipe_id = %s GROUP BY s.position, s.action, s.tool_id ORDER BY s.position"""),
        (recipe_id,)).fetchall()
    superseded = {old for (old,) in conn.execute(vsql(vault, """
        SELECT s.old_id FROM {v}.supersession s JOIN {v}.step_fact f ON f.fact_id = s.old_id
        WHERE f.recipe_id = %s"""), (recipe_id,))}
    withdrawn = {fact for (fact,) in conn.execute(vsql(vault, """
        SELECT w.entry_id FROM {v}.withdrawal w JOIN {v}.step_fact f ON f.fact_id = w.entry_id
        WHERE f.recipe_id = %s"""), (recipe_id,))} if has_withdrawals(conn, vault) else set()
    cited = backings(conn, vault, {i for _, _, tool_id, facts in steps for i in (*facts, tool_id) if i})
    unverified = {i for i, backing in cited.items() if backing == "unverified"}  # ADR 0012 §3
    for position, action, tool_id, facts in steps:
        refs = [f"{vault}#{fact_id}" + (" [superseded]" if fact_id in superseded else "")
                + (" [withdrawn]" if fact_id in withdrawn else "") + (" [unverified]" if fact_id in unverified else "")
                for fact_id in facts]
        notes = []
        if refs:
            notes.append("facts: " + ", ".join(refs))
        if tool_id:
            notes.append(f"tool: {vault}#{tool_id}" + (" [unverified]" if tool_id in unverified else ""))
        lines.append(f"  {position}. {action}" + (f"  ({'; '.join(notes)})" if notes else ""))
    examples = conn.execute(vsql(vault, """
        SELECT parameters, result FROM {v}.recipe_example WHERE recipe_id = %s ORDER BY id"""),
        (recipe_id,)).fetchall()
    if examples:
        lines.append("examples:")
        lines.extend(f"  - {json.dumps(values, ensure_ascii=False)} -> {result}" for values, result in examples)
    return lines


def tool_lines(conn, vault: str, entry_id: int) -> list[str]:
    row = conn.execute(vsql(vault, "SELECT tool_type, environment, priority FROM {v}.tool WHERE entry_id = %s"),
                       (entry_id,)).fetchone()
    if row is None:
        return []
    tool_type, environment, priority = row
    lines = [f"tool: {tool_type}, priority {priority}"]
    if environment:
        lines.append(f"environment: {environment}")
    return lines


def pending_reviews(conn, vault: str) -> str:
    rows = conn.execute(vsql(vault, """
        SELECT r.id, r.reason, r.payload->>'title', r.origin_project, r.created_at
        FROM {v}.review_item r
        WHERE NOT EXISTS (SELECT 1 FROM {v}.review_decision d WHERE d.review_id = r.id)
        ORDER BY r.id""")).fetchall()
    if not rows:
        return "no pending review items"
    return safe("\n".join(f"review #{review_id}  {reason:<24}  {title}  ({project}, {created:%Y-%m-%d})"
                          for review_id, reason, title, project, created in rows))


def review_history(conn, vault: str) -> str:
    """Every review item of a vault upgraded for ADR 0012, with its decision; read only."""
    rows = conn.execute(vsql(vault, """
        SELECT r.id, r.reason, r.payload->>'title', r.origin_project, r.created_at, d.decision, d.decided_by
        FROM {v}.review_item r LEFT JOIN {v}.review_decision d ON d.review_id = r.id ORDER BY r.id""")).fetchall()
    lines = [f"{RETIRED}; history of vault {vault}:"]
    lines += [f"review #{review_id}  {reason:<24}  {title}  ({project}, {created:%Y-%m-%d}) · "
              + (f"{decision} by {by}" if decision else "undecided")
              for review_id, reason, title, project, created, decision, by in rows] or ["no review items"]
    return safe("\n".join(lines))


def review_card(conn, vault: str, review_id: int) -> str:
    """Everything a person needs to decide one review item; entries are `<vault>#<id>`, items `review #<n>`."""
    row = conn.execute(vsql(vault, """
        SELECT r.reason, r.payload, r.entry_id, r.origin_project, r.origin_session, r.created_at,
               coalesce(d.decision, 'pending')
        FROM {v}.review_item r LEFT JOIN {v}.review_decision d ON d.review_id = r.id
        WHERE r.id = %s"""), (review_id,)).fetchone()
    if row is None:
        raise Val3Error(f"no review item #{review_id}")
    reason, payload, entry_id, project, session, created, status = row
    tag = (f"batch {(payload.get('import') or {}).get('batch', '-')}" if reason == "import"
           else f"session {(session or '-')[:8]}")
    header = (f"review #{review_id} · {reason}{' (notice)' if reason in NOTICES else ''} · {project} · "
              f"{created:%Y-%m-%d} · {tag}")
    lines = [header + ("" if status == "pending" else f" · {status}"), ""]
    if reason == "supersession":
        for old_id in predecessors(conn, vault, entry_id):
            lines += [f"BEFORE {vault}#{old_id}", show_entry(conn, vault, old_id), ""]
        lines += [f"NOW {vault}#{entry_id}", show_entry(conn, vault, entry_id)]
    elif reason == "recipe-cites-superseded":
        ids = [payload.get(key) for key in ("recipe", "old", "new")]
        if all(isinstance(value, int) for value in ids):
            for label, value in zip(("RECIPE", "BEFORE", "NOW"), ids):
                lines += [f"{label} {vault}#{value}", show_entry(conn, vault, value), ""]
        else:  # notices queued before the ids were stored
            lines.append(str(payload.get("title", "")))
    elif reason == "withdrawn":
        lines += [f"WITHDRAWN {vault}#{entry_id}", show_entry(conn, vault, entry_id)]
        for recipe_id in payload.get("recipes") or []:
            lines += ["", f"RECIPE {vault}#{recipe_id}", show_entry(conn, vault, recipe_id)]
        if payload.get("superseded"):
            older = payload["superseded"]
            lines += ["", f"BEFORE {vault}#{older} (out of delivery: save it again if it still holds)",
                      show_entry(conn, vault, older)]
    elif reason == "withdrawal":
        lines += withdrawal_lines(conn, vault, payload, entry_id)
    elif reason == "import":
        lines += import_lines(conn, vault, payload)
    else:
        lines += candidate_lines(conn, vault, payload)
    return safe("\n".join(lines).rstrip())


_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:!-]{1,128}")


def withdrawal_evidence(items: list[dict] | None) -> list[str]:
    lines = []
    for item in items or []:
        reproduction = item.get("reproduction")
        lines.append(f"  withdrawal evidence: {item.get('kind')}: {_one_line(reproduction)}" if reproduction
                     else f"  withdrawal evidence: {item.get('kind')}")
        lines.extend(f"      > {line}" for line in str(item.get("quote")).splitlines())
        lines.extend(_printed_by(item))
    return lines


def _printed_by(item: dict) -> list[str]:
    """The calls the writer found printing the quote (ADR 0006, amendment 1); nothing on a row without them."""
    calls = [call for call in item.get("calls") or [] if isinstance(call, dict)] if isinstance(item, dict) else []
    if not calls:
        return []
    more = item.get("more_calls")
    count = len(calls) + (more if isinstance(more, int) else 0)
    same = sum(call.get("reproduction_is_command") is True for call in calls)
    tools = ", ".join(tool if isinstance(tool := call.get("tool"), str) and _TOOL_NAME.fullmatch(tool) else "?"
                      for call in calls)
    if count == 1:
        return [f"      printed by: {tools}" + (" (the command above, checked)" if same else
                                               " (the line above is a description, not checked)")]
    listed = f", {more} more not listed" if count > len(calls) else ""
    return [f"      printed by {count} calls: {tools}{listed}"
            + (f" ({same} ran the command above, checked)" if same else " (the line above is a description)")]


def withdrawal_lines(conn, vault: str, payload: dict, entry_id: int | None) -> list[str]:
    """A proposed withdrawal: the entry, or the batch and how many entries it admitted; then what approving does."""
    if payload.get("batch"):
        count = sum(not existed for _, existed in batch_entries(conn, vault, payload["batch"]))
        lines = [f"batch {payload['batch']}: {count} admitted entries"]
    else:
        lines = [show_entry(conn, vault, entry_id)]
    return lines + ["", f"approving withdraws ({payload.get('cause')}): {payload.get('reason')}",
                    *withdrawal_evidence(payload.get("evidence"))]


def import_lines(conn, vault: str, payload: dict) -> list[str]:
    """An imported candidate: its source, the candidate, and each quote with the commit it was checked at."""
    meta = payload.get("import") or {}
    lines = [f"SOURCE {meta.get('source')} ({meta.get('source_label')} {str(meta.get('source_commit'))[:7]}, "
             f"{str(meta.get('source_date'))[:10]})"]
    if meta.get("observed"):
        lines.append(f"observed: {meta['observed']}")
    lines.append("")
    lines += candidate_lines(conn, vault, {**payload, "evidence": []})
    results = meta.get("evidence") or []
    verified = [item for item in results if item.get("status") == "verified"]
    if verified:
        lines.append("evidence:")
    for item in verified:
        lines.append(f"  - file {item['path']} @ {item['commit'][:7]} ({str(item['date'])[:10]}, {item['label']}; "
                     f"at snapshot: {'yes' if item['at_snapshot'] else 'no'}; {item['class']})")
        lines.extend(f"      > {line}" for line in item["quote"].splitlines())
        lines.append(f"      reproduce: git show {shlex.quote(item['commit'] + ':' + item['path'])}  (not run)")
    failed = [item for item in results if item.get("status") != "verified"]
    if failed:
        lines.append("not verified:")
        lines.extend(f"  - {item.get('path')}: {item.get('status')}" for item in failed)
    if not verified:
        lines.append("no repository evidence: approving attests it")
    return lines


def candidate_lines(conn, vault: str, candidate: dict) -> list[str]:
    """A queued candidate (validated JSON) in the layout of `val3 show`; reproductions are never run."""
    lines = [f"[{candidate.get('kind')}]  {candidate.get('title')}", "", str(candidate.get("statement", "")), ""]
    supersedes = candidate.get("supersedes") or []
    supersedes = [supersedes] if isinstance(supersedes, str) else supersedes  # queued before the list
    if supersedes:
        lines.append(f"approving supersedes {', '.join(map(str, supersedes))}")
        for ref in supersedes:
            ref_vault, old_id = parse_ref(ref)
            if ref_vault == vault:
                try:
                    lines += [f"BEFORE {ref}", show_entry(conn, vault, old_id)]
                except Val3Error as exc:
                    lines.append(f"  ({exc})")
        lines.append("")
    if candidate.get("keywords"):
        lines.append("keywords: " + ", ".join(candidate["keywords"]))
    if candidate.get("indexes"):
        lines.append("indexes: " + ", ".join(candidate["indexes"]))
    tool = candidate.get("tool")
    if tool:
        lines.append(f"tool: {tool.get('type')}, priority {tool.get('priority', 0)}")
        if tool.get("environment"):
            lines.append(f"environment: {tool['environment']}")
    recipe = candidate.get("recipe")
    if recipe:
        if recipe.get("parameters"):
            lines.append("parameters:")
            lines.extend(f"  - {p['name']}: {p['description']}" if p.get("description") else f"  - {p['name']}"
                         for p in recipe["parameters"])
        lines.append("steps:")
        for position, step in enumerate(recipe.get("steps", []), start=1):
            notes = []
            if step.get("facts"):
                notes.append("facts: " + ", ".join(step["facts"]))
            if step.get("tool"):
                notes.append(f"tool: {step['tool']}")
            lines.append(f"  {position}. {step['action']}" + (f"  ({'; '.join(notes)})" if notes else ""))
        if recipe.get("examples"):
            lines.append("examples:")
            lines.extend(f"  - {json.dumps(e['parameters'], ensure_ascii=False)} -> {e['result']}"
                         for e in recipe["examples"])
    if candidate.get("evidence"):
        lines.append("evidence:")
        for item in candidate["evidence"]:
            lines.append(f"  - {item['kind']}")
            lines.extend(f"      > {line}" for line in item["quote"].splitlines())
            if item.get("reproduction"):
                lines.append(f"      reproduce: {item['reproduction']}  (not run)")
    return lines


_INDEX_HEADER = """\
# VAL3 memory index

Verified entries of this project's vaults, grouped by knowledge index. Before working on a topic listed here,
read its entries: `val3 show <ref>` prints one with its evidence and steps, `val3 index <name>` lists a
collapsed section. `val3 recall <terms>` searches; `val3 save --help` shows how to record a verified entry.
Registered tools answer derivable questions: prefer them over stored facts."""

BACKED = "Entries of this project's vaults with evidence or attested by a person"  # ADR 0012, upgraded vaults

_RECHECK_HEADER = """\
Entries marked "to re-check" cite a file that changed since they were checked, or a fact or tool that left
delivery. Before relying on one, check it: `val3 confirm` if it holds, `val3 save` with "supersedes" if it
changed, `val3 withdraw` if it no longer applies (`val3 confirm --help`)."""


@dataclass(frozen=True)
class IndexSummary:
    entries: int  # live entries the index covers, shown or not
    recheck: int  # of them, own-vault entries marked to re-check
    collapsed: int  # collapsed sections still shown
    omitted: int  # entries in dropped lines
    settle: int = 0  # of them, own-vault entries marked to settle


def session_index(conn, project, limit: int) -> tuple[str, IndexSummary]:
    """Tools first, then sections by name (design D1), `General` last. Over `limit - 100`, sections collapse
    largest first, then collapsed lines and at last tool lines are dropped from the end (D2)."""
    (_, marked), placed, groups = own_marks(conn, project), indexes.sections(conn, project), {}
    settling = {(vault, entry_id): pairs for vault in project.own
                for entry_id, pairs in neighbour.open_pairs(conn, vault, project=project, delivery=True).items()}
    for vault in project.vaults:
        rows = conn.execute(vsql(vault, f"""
            SELECT e.id, e.kind, e.title, coalesce(t.priority, 0) FROM {{v}}.entry e
            LEFT JOIN {{v}}.tool t ON t.entry_id = e.id WHERE {live(conn, vault, delivery=True)}"""),
            {"as_of": None}).fetchall()
        for entry_id, kind, title, priority in rows:
            mark, near = (vault, entry_id) in marked, settling.get((vault, entry_id))
            section = "Tools" if kind == "tool" else placed.get((vault, entry_id), "General")
            groups.setdefault(section, []).append(((-priority, vault, entry_id), mark, bool(near),
                                                   f"- {vault}#{entry_id} [{kind}] {title}"
                                                   + (" · to re-check" if mark else "") + neighbour.mark(vault, near)))
    shown = [(name, [line for *_, line in sorted(groups[name])], sum(item[1] for item in groups[name]),
              sum(item[2] for item in groups[name]))
             for name in sorted(groups, key=lambda name: (name != "Tools", name == "General", name))]
    try:  # design §4.2: one fixed line, never what they hold; the index matters more than the line
        other = catalogue.has_other(conn, project.vaults)
    except psycopg.Error:
        other = False
    head = (_INDEX_HEADER.replace("\nRegistered", "\nOther vaults on this server: val3 vault list\nRegistered")
            if other else _INDEX_HEADER)
    if any(upgraded(conn, vault) for vault in project.vaults):
        head = head.replace("Verified entries of this project's vaults", BACKED)
    head += (f"\n\n{_RECHECK_HEADER}" if marked else "") + (f"\n\n{neighbour.HEADER}" if settling else "")
    collapsed, omitted = set(), 0

    def render() -> str:
        text = head
        for name, lines, k, s in shown:
            if name in collapsed:
                counts = ", ".join(f"{n} {label}" for n, label in ((k, "to re-check"), (s, "to settle")) if n)
                text += (f"\n\n## {name} — {len(lines)} entries" + (f" ({counts})" if counts else "")
                         + f": val3 index {'--none' if name == 'General' else name}")
            elif lines:
                text += f"\n\n## {name}\n" + "\n".join(lines)
        return text

    while len(text := render()) > limit - 100:
        expanded = [section for section in shown if section[0] != "Tools" and section[0] not in collapsed]
        if expanded:
            collapsed.add(max(expanded, key=lambda section: (len(section[1]), section[0]))[0])
        elif shown and shown[-1][0] != "Tools":
            omitted += len(shown.pop()[1])
        elif shown and shown[-1][1]:
            shown[-1][1].pop()
            omitted += 1
        else:
            break
    if not groups:
        text += "\n\nNo entries yet."
    if omitted:
        text += f"\n\n{omitted} more entries not shown: use `val3 recall <terms>`."
    return text, IndexSummary(sum(len(group) for group in groups.values()),
                              sum(item[1] for group in groups.values() for item in group),
                              sum(name in collapsed for name, *_ in shown), omitted,
                              sum(item[2] for group in groups.values() for item in group))


def prompt_context(conn, hits, limit: int, marked: Collection[str] = (),
                   near: Mapping[str, list[tuple[int, float]]] | None = None) -> tuple[str | None, list[str]]:
    """The context for a prompt and the refs whose blocks it holds, in order."""
    if not hits:
        return None, []
    rendered = []
    text = ("VAL3 entries matching this prompt (with evidence or attested by a person; `val3 show <ref>` prints the "
            "backing):" if any(upgraded(conn, vault) for vault in {hit.vault for hit in hits}) else
            "VAL3 entries matching this prompt (verified; `val3 show <ref>` prints the evidence):")
    for hit in hits:
        mark = (" · to re-check" if hit.ref in marked else "") + neighbour.mark(hit.vault, (near or {}).get(hit.ref))
        block = f"\n- {hit.ref} [{hit.kind}] {hit.title}{mark}: {' '.join(hit.statement.split())}"
        if hit.kind == "recipe":
            block += "".join(f"\n    {line}" for line in recipe_lines(conn, hit.vault, hit.id))
        if len(block) > limit // 2:
            block = block[: limit // 2] + " …"
        if len(text) + len(block) <= limit:
            text += block
            rendered.append(hit.ref)
    return text, rendered
