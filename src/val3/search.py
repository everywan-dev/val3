"""Search over a project's vaults: full-text, and semantic when the project has an [embedding] table."""
from dataclasses import dataclass
from datetime import datetime

import psycopg

from val3.db import live, vsql
from val3.embedding import EmbeddingError, Space, embed, no_table, vector_literal

# Words the stock English and Spanish stop lists keep but that say nothing about a topic.
STOP_WORDS = (
    "si así asi puede pueden puedo podemos podría mismo misma mismos mismas ser hay hacer hace hecho "
    "tener tiene tienen ya bien ahora solo sólo cada todo toda todos todas vez aquí allí entonces "
    "además pues luego dónde donde cómo qué cuál debe deben debería sea vale ok favor gracias hola "
    "can could would should also just like get got make need use one now please thanks yes"
)
FLAT_BELOW = 10  # live entries; smaller vaults weigh every shared word alike
PROMPT_CAP = 3          # entries injected per typed prompt
EMBED_TIMEOUT = 1.0     # seconds the prompt hook waits for the embedding service
RECALL_TIMEOUT = 10.0
SHORT_PROMPT = 6        # words; a shorter prompt is read with the end of the previous assistant turn
PREVIOUS_CHARS = 500
QUERY_CHARS = 1_000     # embedding time grows with length; long prompts are clipped
FUSE_K = 60             # reciprocal rank fusion: an entry scores 1 / (FUSE_K + place) in each list that holds it
WORD_DEPTH = 50         # full-text hits fused in an explicit search, whatever the limit
WORD_LEAD = 3           # the best entry by words is listed no lower than this place among non-tools

_QUERY = """
WITH live AS (
    SELECT e.* FROM {v}.entry e
    WHERE (%(as_of)s::timestamptz IS NULL OR e.created_at <= %(as_of)s) AND /*live*/
), n AS (
    SELECT count(*) AS c FROM live
), l AS (
    SELECT ARRAY(SELECT unnest(val3_common.query_lexemes('val3_common.en', %(text)s))
                 EXCEPT SELECT unnest(tsvector_to_array(to_tsvector('val3_common.en', %(stop)s)))) AS en,
           ARRAY(SELECT unnest(val3_common.query_lexemes('val3_common.es', %(text)s))
                 EXCEPT SELECT unnest(tsvector_to_array(to_tsvector('val3_common.es', %(stop)s)))) AS es
), w AS (
    -- Weight of each query word: 0 when most live entries hold it (the project's own vocabulary),
    -- 2 when a single one does, 1 otherwise; every word weighs 1 in a small vault.
    -- ponytail: one scan of the vault per query word; keep document frequencies in a table if vaults grow large.
    SELECT cfg, lexeme,
           CASE WHEN n.c < %(flat)s THEN 1 WHEN 2 * df > n.c THEN 0 WHEN df = 1 THEN 2 ELSE 1 END AS weight
    FROM n, (SELECT 'en' AS cfg, x AS lexeme,
                    (SELECT count(*) FROM live WHERE x = ANY(tsvector_to_array(live.search_en))) AS df
             FROM l, unnest(l.en) x
             UNION ALL
             SELECT 'es', x, (SELECT count(*) FROM live WHERE x = ANY(tsvector_to_array(live.search_es)))
             FROM l, unnest(l.es) x) d
), scored AS (
    SELECT e.id, e.kind, e.title, e.statement,
           greatest((SELECT sum(weight) FROM w WHERE cfg = 'en' AND lexeme = ANY(tsvector_to_array(e.search_en))),
                    (SELECT sum(weight) FROM w WHERE cfg = 'es' AND lexeme = ANY(tsvector_to_array(e.search_es))))
               AS weight,
           greatest(coalesce(ts_rank_cd(e.search_en, val3_common.or_query(l.en)), 0),
                    coalesce(ts_rank_cd(e.search_es, val3_common.or_query(l.es)), 0)) AS rank
    FROM live e, l
    WHERE (e.search_en @@ val3_common.or_query(l.en) OR e.search_es @@ val3_common.or_query(l.es))/*unembedded*/
)
-- Shared words must weigh 2, or all the query holds: one ordinary shared word is noise in a prompt.
-- An explicit lookup (`any`) takes any shared word that weighs.
SELECT id, kind, title, statement, weight + rank / (1 + rank)
FROM scored
WHERE weight >= CASE WHEN %(any)s THEN 1 ELSE least(2, greatest((SELECT sum(weight) FROM w WHERE cfg = 'en'),
                                                               (SELECT sum(weight) FROM w WHERE cfg = 'es'))) END
ORDER BY 5 DESC, id DESC
LIMIT %(limit)s
"""

_UNEMBEDDED = """
      AND NOT EXISTS (SELECT 1 FROM {v}.embedding m WHERE m.entry_id = e.id AND m.model = %(model)s
                        AND m.digest = %(digest)s AND m.text_version = %(text_version)s)"""


@dataclass(frozen=True)
class Hit:
    vault: str
    id: int
    kind: str
    title: str
    statement: str
    rank: float  # full-text weight, or cosine similarity for semantic hits
    method: str = "full-text"  # or "semantic"

    @property
    def ref(self) -> str:
        return f"{self.vault}#{self.id}"


def search(conn, vaults: tuple[str, ...], text: str, limit: int = 10, space: Space | None = None,
           as_of: datetime | None = None, any_word: bool = False, tools_within: int | None = None,
           delivery: bool = False) -> list[Hit]:
    """Full-text hits, tools first. With `space`, only entries without a vector in it (filtered before
    the limit); with `as_of`, entries, supersessions and withdrawals recorded up to that moment; with
    `any_word`, one shared word that weighs is enough; with `tools_within`, a tool is kept only if a search
    with that limit returns it, so that a deeper list brings no tool a shallow one leaves out; with `delivery`,
    only entries that may be delivered automatically, also in the weights (ADR 0012 §3)."""
    query = _QUERY.replace("/*unembedded*/", _UNEMBEDDED if space else "")
    params = {"text": text, "stop": STOP_WORDS, "flat": FLAT_BELOW, "limit": limit, "as_of": as_of, "any": any_word}
    if space:
        params |= {"model": space.model, "digest": space.digest, "text_version": space.text_version}
    by_vault = [[Hit(vault, *row) for row in conn.execute(
        vsql(vault, query.replace("/*live*/", live(conn, vault, delivery=delivery))), params)] for vault in vaults]
    hits = [hit for rows in by_vault for hit in rows]
    if tools_within is not None:  # each vault's rows come by weight: its first `tools_within` are that search's
        kept = {hit.ref for hit in _cut([hit for rows in by_vault for hit in rows[:tools_within]], tools_within)}
        hits = [hit for hit in hits if hit.kind != "tool" or hit.ref in kept]
    return _cut(hits, limit)


def _cut(hits: list[Hit], limit: int) -> list[Hit]:
    return sorted(hits, key=lambda hit: (hit.kind != "tool", -hit.rank))[:limit]


class DatabaseError(EmbeddingError):
    """A database error on the semantic path: `find` falls back to full-text for it unless `strict`."""


_SEMANTIC = """
-- Only vectors of the query's space reach the distance operator, and each is scored once here: scored after
-- the join, a nested loop the planner may pick scores every entry against every vector (n squared).
WITH m AS MATERIALIZED (
    SELECT entry_id, (SELECT max(1 - (vector <=> q::vector)) FROM unnest(%(vectors)s::text[]) q) AS score
    FROM {v}.embedding
    WHERE model = %(model)s AND digest = %(digest)s AND text_version = %(text_version)s AND dims = %(dims)s
)
SELECT e.id, e.kind, e.title, e.statement, m.score
FROM {v}.entry e JOIN m ON m.entry_id = e.id
WHERE m.score >= %(floor)s AND (%(as_of)s::timestamptz IS NULL OR e.created_at <= %(as_of)s) AND /*live*/
"""


def semantic(conn, vaults: tuple[str, ...], space: Space, vectors: list[list[float]], floor: float,
             as_of: datetime | None = None, delivery: bool = False) -> list[Hit]:
    """Live entries whose vector in `space` scores at least `floor` against the closest of `vectors`,
    in no order.

    No LIMIT: the cut happens once, in select(), after tools are put first."""
    params = {"model": space.model, "digest": space.digest, "text_version": space.text_version,
              "dims": space.dims, "vectors": [vector_literal(v) for v in vectors], "floor": floor, "as_of": as_of}
    hits = []
    for vault in vaults:
        try:
            rows = conn.execute(vsql(vault, _SEMANTIC.replace("/*live*/", live(conn, vault, delivery=delivery))),
                                params).fetchall()
        except psycopg.errors.UndefinedTable:
            raise no_table(vault) from None
        except psycopg.OperationalError:
            raise
        except psycopg.Error as exc:  # e.g. no SELECT privilege; autocommit keeps the connection usable
            raise DatabaseError(f"vault {vault}: {str(exc).strip().splitlines()[0]}") from None
        hits += [Hit(vault, *row, method="semantic") for row in rows]
    return hits


def candidates(conn, vaults: tuple[str, ...], space: Space, vectors: list[list[float]], text: str, floor: float,
               limit: int, as_of: datetime | None = None, delivery: bool = False) -> list[Hit]:
    """Semantic hits at or above `floor`, plus full-text hits among entries without a vector in `space`
    (admitted while the service was down, or before a new model build)."""
    hits = semantic(conn, vaults, space, vectors, floor, as_of, delivery)
    try:
        return hits + search(conn, vaults, text, limit, space=space, as_of=as_of, delivery=delivery)
    except psycopg.OperationalError:
        raise
    except psycopg.Error as exc:
        raise DatabaseError(str(exc).strip().splitlines()[0]) from None


def select(hits: list[Hit], floor: float, limit: int) -> list[Hit]:
    """The one rule of delivery, recall and `val3 eval`: semantic hits at or above `floor` and full-text
    hits; tools first, then semantic by score, then full-text by weight; ties by the newest entry. An entry
    found both ways is listed once, where its semantic hit puts it."""
    kept = [hit for hit in hits if hit.method != "semantic" or hit.rank >= floor]
    kept.sort(key=lambda hit: (hit.kind != "tool", hit.method != "semantic", -hit.rank, -hit.id))
    first: dict[str, Hit] = {}
    for hit in kept:
        first.setdefault(hit.ref, hit)
    return list(first.values())[:limit]


def fuse(by_meaning: list[Hit], by_word: list[Hit], limit: int) -> list[Hit]:
    """The order of an explicit search (measured 2026-11-15 on recorded tool calls): tools first; the rest by
    the sum of 1 / (FUSE_K + place) over the two lists, ties to the list by meaning; then the best entry by
    words that is not a tool moves up to place WORD_LEAD among non-tools when it sits lower, so that an
    identifier the embedding misses is still returned. `by_meaning` in no order, `by_word` as search() gives it."""
    by_meaning = sorted(by_meaning, key=lambda hit: (-hit.rank, -hit.id))
    score: dict[str, float] = {}
    first: dict[str, Hit] = {}
    for hits in (by_meaning, by_word):
        for place, hit in enumerate(hits, start=1):
            score[hit.ref] = score.get(hit.ref, 0) + 1 / (FUSE_K + place)
            first.setdefault(hit.ref, hit)
    order = sorted(first.values(), key=lambda hit: -score[hit.ref])  # stable: ties keep the order above
    tools = [hit for hit in order if hit.kind == "tool"]
    rest = [hit for hit in order if hit.kind != "tool"]
    lead = next((first[hit.ref] for hit in by_word if hit.kind != "tool"), None)
    if lead is not None and rest.index(lead) >= WORD_LEAD:
        rest.remove(lead)
        rest.insert(WORD_LEAD - 1, lead)
    return (tools + rest)[:limit]


def previous_part(prompt: str, previous: str) -> str:
    """The end of the previous assistant turn read together with a short prompt; "" for longer ones."""
    return previous[-PREVIOUS_CHARS:] if previous and len(prompt.split()) <= SHORT_PROMPT else ""


def query_texts(prompt: str, previous: str = "") -> list[str]:
    """The prompt alone, and for a short prompt also with the previous turn: a short prompt may answer that
    turn ("yes") or change topic, and an entry scores against the closer of the two."""
    part = previous_part(prompt, previous)
    return [prompt[:QUERY_CHARS]] + ([f"{prompt}\n{part}"[:QUERY_CHARS]] if part else [])


@dataclass(frozen=True)
class Found:
    hits: list[Hit]
    method: str                # semantic | full-text
    warning: str | None = None


def find(conn, project, prompt: str, limit: int, previous: str = "", timeout: float = EMBED_TIMEOUT,
         words: bool = False, strict: bool = False, delivery: bool = False) -> Found:
    """Semantic search when the project has an [embedding] table, else full-text. With `words` (an explicit
    lookup), one shared word that weighs is enough, and full-text hits over every entry are fused with the
    semantic ones (a single word or an identifier scores low by meaning): see fuse().

    Any failure of the semantic path falls back to full-text over the prompt, with a warning; with `strict`, a
    database error is raised instead. With `delivery`, on every path only entries that may be delivered
    automatically are searched (ADR 0012 §3)."""
    settings = project.embedding
    if settings is None:
        return Found(search(conn, project.vaults, prompt, limit, any_word=words, delivery=delivery), "full-text")
    try:
        space, vectors = embed(settings, query_texts(prompt, previous), timeout)
        if words:
            by_meaning = semantic(conn, project.vaults, space, vectors, settings.floor)
            by_word = search(conn, project.vaults, prompt, max(limit, WORD_DEPTH), any_word=True,
                             tools_within=limit)
            return Found(fuse(by_meaning, by_word, limit), "semantic")
        hits = candidates(conn, project.vaults, space, vectors, prompt, settings.floor, limit, delivery=delivery)
    except EmbeddingError as exc:
        if strict and isinstance(exc, DatabaseError):
            raise
        return Found(search(conn, project.vaults, prompt, limit, any_word=words, delivery=delivery), "full-text",
                     f"semantic search is unavailable ({exc}); entries were found by full-text search")
    return Found(select(hits, settings.floor, limit), "semantic")
