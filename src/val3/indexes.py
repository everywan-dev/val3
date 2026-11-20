"""Knowledge index names after aliases: an alias resolves, hop by hop, to the index it was merged into."""
import re
from collections import Counter
from collections.abc import Sequence

from val3 import Val3Error, neighbour, recheck
from val3.db import live, marks, vsql


def has_aliases(conn, vault: str) -> bool:
    """A vault created before aliases lacks the table until `val3 vault upgrade`; its names are all final."""
    return conn.execute("SELECT to_regclass(%s)", (f"{vault}.index_alias",)).fetchone()[0] is not None


def resolved(conn, vault: str) -> str:
    """SQL subquery yielding (entry_id, name): each entry's index names after aliases, without duplicates.
    The text holds `{v}` for vsql."""
    if not has_aliases(conn, vault):
        return ("(SELECT DISTINCT ei.entry_id, k.name FROM {v}.entry_index ei"
                " JOIN {v}.knowledge_index k ON k.id = ei.index_id)")
    return """(WITH RECURSIVE hop (id, target) AS (
            SELECT id, id FROM {v}.knowledge_index
            UNION
            SELECT h.id, a.target_id FROM hop h JOIN {v}.knowledge_index k ON k.id = h.target
            JOIN {v}.index_alias a ON a.name = k.name)
        SELECT DISTINCT ei.entry_id, k.name FROM {v}.entry_index ei
        JOIN hop h ON h.id = ei.index_id JOIN {v}.knowledge_index k ON k.id = h.target
        WHERE NOT EXISTS (SELECT 1 FROM {v}.index_alias a WHERE a.name = k.name))"""


def canonical(conn, vault: str, name: str) -> str:
    """The name `name` finally resolves to, or `name` itself when it is no alias."""
    if not has_aliases(conn, vault):
        return name
    row = conn.execute(vsql(vault, """
        WITH RECURSIVE hop (name) AS (
            SELECT %s::text
            UNION
            SELECT k.name FROM hop h JOIN {v}.index_alias a ON a.name = h.name
            JOIN {v}.knowledge_index k ON k.id = a.target_id)
        SELECT name FROM hop h WHERE NOT EXISTS (SELECT 1 FROM {v}.index_alias a WHERE a.name = h.name)"""),
        (name,)).fetchone()
    return row[0] if row else name


def normalised(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def excluded(conn, project, vault: str) -> set[str]:
    """Index names that never become a section in `vault` (design D1): those named like a vault of the project
    or like the project itself or a project that wrote to the vault."""
    origins = [name for (name,) in conn.execute(vsql(vault, "SELECT DISTINCT origin_project FROM {v}.entry"))]
    return {normalised(name) for name in (project.name, *project.vaults, *origins)}


def counts(conn, vaults: Sequence[str]) -> dict[str, int]:
    """Live entries per index name after aliases, each vault through its own aliases."""
    total: dict[str, int] = {}
    for vault in vaults:
        for name, n in conn.execute(vsql(vault, f"""
                SELECT r.name, count(*) FROM {resolved(conn, vault)} r JOIN {{v}}.entry e ON e.id = r.entry_id
                WHERE {live(conn, vault)} GROUP BY r.name"""), {"as_of": None}):
            total[name] = total.get(name, 0) + n
    return total


def listing(conn, project) -> str:
    from val3 import views  # views imports the writer, which imports this module
    rows = sorted(counts(conn, project.vaults).items(), key=lambda item: (-item[1], item[0]))
    return views.safe("\n".join(f"{name}  {n}" for name, n in rows)) if rows else "(no indexes)"


def members(conn, project, name: str | None, owner) -> str:
    """The live entries of the index `name` after aliases, or with `None` those with no section index (D1),
    in the session index's line format. Marks only on the own vaults of the project `owner` (None: no marks)."""
    from val3 import views  # views imports the writer, which imports this module
    if name is not None and not any(
            conn.execute(vsql(vault, "SELECT 1 FROM {v}.knowledge_index WHERE name = %s"), (name,)).fetchone()
            for vault in project.vaults):
        raise Val3Error(f"no index {name}")
    found = {}
    for vault in project.vaults:
        if name is None:
            condition = (f"e.kind <> 'tool' AND NOT EXISTS (SELECT 1 FROM {resolved(conn, vault)} r"
                         " WHERE r.entry_id = e.id AND r.name <> ALL(%(excluded)s))")
            params = {"excluded": sorted(excluded(conn, project, vault))}
        else:
            condition = f"e.id IN (SELECT r.entry_id FROM {resolved(conn, vault)} r WHERE r.name = %(name)s)"
            params = {"name": canonical(conn, vault, name)}
        found[vault] = conn.execute(vsql(vault, f"""
            SELECT e.id, e.kind, e.title FROM {{v}}.entry e WHERE {condition} AND {live(conn, vault)}
            ORDER BY e.id"""), {"as_of": None, **params}).fetchall()
    own = {vault: [i for i, _, _ in found[vault]] for vault in (owner.own if owner else ()) if found.get(vault)}
    marked = recheck.own_marks(conn, owner, own)[1] if own else {}
    rows = []
    for vault, entries in found.items():
        near = neighbour.open_pairs(conn, vault, own[vault], owner) if vault in own else {}
        backing = marks(conn, vault, [entry_id for entry_id, _, _ in entries])
        rows += [f"- {vault}#{entry_id} [{kind}] {views._one_line(title)}" + backing.get(entry_id, "")
                 + (" · to re-check" if (vault, entry_id) in marked else "") + neighbour.mark(vault, near.get(entry_id))
                 for entry_id, kind, title in entries]
    return views.safe("\n".join(rows)) if rows else "(no entries)"


def sections(conn, project) -> dict[tuple[str, int], str]:
    """(vault, entry id) -> section of every live non-tool entry the session index may deliver (design D1, ADR 0012
    §3): of its indexes after aliases and exclusions, the one with the most such entries in its vault, ties by name;
    none left: `General`.
    Counts and memberships come from one query, so an entry admitted meanwhile cannot leave a name uncounted."""
    result = {}
    for vault in project.vaults:
        rows = conn.execute(vsql(vault, f"""
            SELECT e.id, e.kind, r.name FROM {{v}}.entry e LEFT JOIN {resolved(conn, vault)} r ON r.entry_id = e.id
            WHERE {live(conn, vault, delivery=True)}"""), {"as_of": None}).fetchall()
        size, skip, names = Counter(name for _, _, name in rows if name is not None), excluded(conn, project, vault), {}
        for entry_id, kind, name in rows:
            if kind == "tool":
                continue
            found = names.setdefault(entry_id, [])
            if name is not None and name not in skip:
                found.append(name)
        result.update({(vault, entry_id): min(found, key=lambda n: (-size[n], n)) if found else "General"
                       for entry_id, found in names.items()})
    return result
