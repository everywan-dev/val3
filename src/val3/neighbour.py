"""Neighbour pairs, read only (design S1-S4): a pair of close live entries of one kind, recorded by the writer's
neighbour check, stays marked "to settle" on its newer entry until it is settled. Every write is the writer's."""
from collections.abc import Collection
from dataclasses import dataclass

from val3.config import Project, parse_ref
from val3.db import has_neighbours, live, vsql
from val3.embedding import Space, vector_literal

OUTCOMES = ("replace", "drop", "keep")

HEADER = """\
Entries marked "to settle" are close to another entry of the same kind. Settle each pair: `val3 settle <ref> \
<near> --replace` if the new one replaces it, `--drop` if it repeats it, `--keep` if both hold \
(`val3 settle --help`)."""


def off(project: Project | None) -> bool:
    """`[embedding] neighbour = false`: no pair is recorded, capture makes no second call, no mark is shown."""
    return project is not None and project.embedding is not None and project.embedding.neighbour is None


def open_pairs(conn, vault: str, ids: Collection[int] | None = None, project: Project | None = None,
               delivery: bool = False) -> dict[int, list[tuple[int, float]]]:
    """Newer entry id -> [(other id, score), …] of the pairs with both entries live and no settlement; with
    `delivery` (session index, prompt hook), both entries may also be delivered (ADR 0012, amendment 1, §5).
    `{}` on a vault without the neighbour tables, and for a `project` with the check off."""
    if off(project) or not has_neighbours(conn, vault):
        return {}
    rows = conn.execute(vsql(vault, f"""
        SELECT n.entry_id, n.other_id, n.score FROM {{v}}.neighbour n
        JOIN {{v}}.entry e ON e.id = n.entry_id JOIN {{v}}.entry o ON o.id = n.other_id
        WHERE {live(conn, vault, "e", delivery)} AND {live(conn, vault, "o", delivery)}
          AND NOT EXISTS (SELECT 1 FROM {{v}}.settlement s WHERE s.entry_id = n.entry_id AND s.other_id = n.other_id)
          AND (%(ids)s::bigint[] IS NULL OR n.entry_id = ANY(%(ids)s))
        ORDER BY n.entry_id, n.other_id"""), {"as_of": None, "ids": None if ids is None else list(ids)}).fetchall()
    pairs: dict[int, list[tuple[int, float]]] = {}
    for entry_id, other_id, score in rows:
        pairs.setdefault(entry_id, []).append((other_id, score))
    return pairs


def mark(vault: str, near: list[tuple[int, float]] | None) -> str:
    """The line mark after a title; empty without open pairs."""
    return f" · to settle (near {', '.join(f'{vault}#{other}' for other, _ in near)})" if near else ""


@dataclass(frozen=True)
class Near:
    id: int
    score: float | None  # None: named only by the candidate's "supersedes"


def near(conn, project: Project, space: Space, vector: list[float], kind: str, named: list[str]) -> list[Near]:
    """Live home-vault entries of `kind` close to `vector` in `space`, by score descending, then each live entry of
    `kind` that `named` refs and that is not listed yet. No exemption: a candidate is never exempt."""
    vault = project.home
    rows = conn.execute(vsql(vault, f"""
        WITH m AS MATERIALIZED (  -- vectors of the given space only, each scored once (as in search._SEMANTIC)
            SELECT entry_id, 1 - (vector <=> %(vector)s::vector) AS score FROM {{v}}.embedding
            WHERE model = %(model)s AND digest = %(digest)s AND text_version = %(text_version)s AND dims = %(dims)s
        )
        SELECT e.id, m.score FROM {{v}}.entry e JOIN m ON m.entry_id = e.id
        WHERE m.score >= %(threshold)s AND e.kind = %(kind)s AND {live(conn, vault, "e")}
        ORDER BY m.score DESC, e.id"""),
        {"vector": vector_literal(vector), "model": space.model, "digest": space.digest,
         "text_version": space.text_version, "dims": space.dims, "kind": kind, "as_of": None,
         "threshold": project.embedding.neighbour}).fetchall()
    found = [Near(entry_id, score) for entry_id, score in rows]
    for ref in named:
        ref_vault, entry_id = parse_ref(ref)
        if ref_vault == vault and entry_id not in {n.id for n in found} and conn.execute(vsql(vault, f"""
                SELECT 1 FROM {{v}}.entry e WHERE e.id = %(id)s AND e.kind = %(kind)s AND {live(conn, vault, "e")}"""),
                {"id": entry_id, "kind": kind, "as_of": None}).fetchone():
            found.append(Near(entry_id, None))
    return found
