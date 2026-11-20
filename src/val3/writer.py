"""The writer: the single code path that changes a vault, one transaction per candidate."""
import hashlib
import json
import re
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from val3 import Val3Error, indexes, neighbour, recheck
from val3.candidate import KINDS, evidence_items, validate
from val3.config import CONFIG_FILE, Project, check_vault_name, parse_ref
from val3.db import NOTICES, RANK, backings, has_neighbours, has_withdrawals, live, upgraded, vsql, writer_lock
from val3.embedding import TEXT_VERSION, EmbeddingError, Space, digest, embed, entry_text, no_table, vector_literal
from val3.text import _one_line, safe
from val3.transcript import Call, evidence_calls


@dataclass(frozen=True)
class Origin:
    project: str
    session: str | None
    machine: str


@dataclass(frozen=True)
class Outcome:
    status: str  # admitted | duplicate | repeat | verified | queued | already-queued | previously-rejected
    ref: str     # <vault>#<id>, or review #<n> for queued items
    reason: str | None = None
    new_indexes: tuple[str, ...] = ()  # index names this admission created
    unverified: bool = False  # admitted without evidence nor attestation (ADR 0012 §2)
    kept: tuple[str, ...] = ()  # entries it names in "supersedes" and does not supersede (ADR 0012 §3)
    failed: int = 0  # quotes that failed their check, so capture dropped all its evidence (ADR 0012, amendment 1)

    def __str__(self) -> str:
        duplicate = f"duplicate of {self.ref}" + (f", {self.reason}" if self.reason else "")
        admitted = f"admitted {'unverified ' if self.unverified else ''}{self.ref}"
        return {"admitted": admitted, "duplicate": duplicate, "repeat": f"repeat of {self.ref}",
                "verified": f"verified {self.ref}",
                "queued": f"queued for review: {self.ref} ({self.reason})",
                "already-queued": f"already queued: {self.ref}",
                "previously-rejected": f"previously rejected: {self.ref}"}[self.status]


@contextmanager
def _transaction(conn):
    """A writer transaction: the refusal of `val3_common.mirror_guard()` on a mirror becomes its one message."""
    try:
        with conn.transaction():
            yield
    except psycopg.errors.RaiseException as exc:
        message = exc.diag.message_primary or ""
        if not re.fullmatch(r"vault \S+ is a mirror and read only", message):
            raise
        raise Val3Error(message) from None


def origin(project: Project, session: str | None) -> Origin:
    return Origin(project.name, session, socket.gethostname())


def content_hash(candidate: dict) -> str:
    key = json.dumps([candidate["kind"], candidate["title"], candidate["statement"]])
    return hashlib.sha256(key.encode()).hexdigest()


def submit(conn, project: Project, data: object, transcript: Path | None, session: str | None,
           private: tuple[str, ...] = (), settling: "Settling | None" = None, via: str | None = None,
           agent: str | None = None) -> Outcome:
    """Validate, check the evidence, then admit or queue in one transaction under the writer lock; on a vault upgraded
    for ADR 0012 nothing is queued: what has no evidence is admitted unverified. `via` (capture, save, tool) and
    `agent` (the subagent whose transcript holds the call) are recorded in the `admitted` or `verified` event. With
    `settling`
    (capture compared the candidate with its neighbours, design K3): a repeat of a live entry is logged, not
    admitted; a neighbour no longer live, or newer than the slice, is not replaced; the entry is admitted with
    its vector and the pairs it was compared with are settled. A quote that fails its check fails the command, but
    in capture (`via` capture) on an upgraded vault the candidate goes on without any of its evidence (ADR 0012,
    amendment 1, §2)."""
    org = origin(project, session)
    vault = project.home
    try:
        candidate = validate(data)
    except Val3Error as exc:
        title = data.get("title") if isinstance(data, dict) else None
        _log_best_effort(conn, vault, "invalid", org,
                         {"title": title if isinstance(title, str) else None, "error": str(exc)})
        raise
    failures = _quote_failures(candidate["evidence"], transcript, private)
    discarded = _discarded(conn, vault, candidate["title"], failures, org) if failures else None
    failed = 0
    try:
        with _transaction(conn):
            _serialize(conn, vault)
            switched = upgraded(conn, vault)  # read under the lock: an upgrade either finished or waits
            if discarded:  # all the evidence or none, before any check that reads it
                if not (switched and via == "capture"):
                    raise discarded
                candidate["evidence"], failed = [], len(failures)
            existing = _entry_by_hash(conn, vault, content_hash(candidate))
            if existing:
                if switched and candidate["evidence"] and _unverified(conn, vault, existing):
                    return _verify(conn, vault, existing, candidate, org, _via(via, agent))
                return replace(_duplicate(conn, vault, existing), failed=failed)
            compared: set[int] = set()
            if settling:
                if settling.repeat_of is not None and _gone(conn, vault, settling.repeat_of) is None:
                    # never verifies: an equal text is the hash's case above; a narrower one does not prove the
                    # entry's whole statement (ADR 0012, amendment 1, §5)
                    _log(conn, vault, "repeat", org, entry_id=settling.repeat_of,
                         detail={"candidate": candidate, "by": settling.by})
                    return Outcome("repeat", f"{vault}#{settling.repeat_of}", failed=failed)
                # K2a, K3b: checked again under the lock, so a confirm or withdrawal during the second call counts
                dropped = {old for old in settling.replaces if not _replaceable(conn, vault, old, settling.slice_at)}
                candidate["supersedes"] = [ref for ref in candidate["supersedes"] if parse_ref(ref)[1] not in dropped]
                compared = {other for other in settling.compared if other not in dropped}
            _check_refs(conn, vault, candidate)
            unverified, kept = switched and not candidate["evidence"], ()
            if unverified:  # ADR 0012 §3: admitted without the supersessions it may not make
                found = backings(conn, vault, [parse_ref(ref)[1] for ref in candidate["supersedes"]])
                kept = tuple(ref for ref in candidate["supersedes"] if found[parse_ref(ref)[1]] != "unverified")
                candidate["supersedes"] = [ref for ref in candidate["supersedes"] if ref not in kept]
                compared -= {parse_ref(ref)[1] for ref in kept}
            reason = None if switched else _review_reason(candidate)
            if reason:
                try:
                    review_id, created = _queue(conn, vault, reason, candidate, None, org)
                except psycopg.errors.CheckViolation:  # review_item_reason_check still lacks 'no-evidence'
                    raise Val3Error(f"vault {vault} predates review items without evidence: its owner runs "
                                    f"`val3 vault upgrade {vault}`") from None
                if not created and _decision(conn, vault, review_id) == "rejected":
                    return Outcome("previously-rejected", f"review #{review_id}", reason)
                return Outcome("queued" if created else "already-queued", f"review #{review_id}", reason)
            vector = (settling.space, settling.vector) if settling else None
            threshold = project.embedding.neighbour if project.embedding else None
            entry_id, created, new = _admit(conn, vault, candidate, org, _unchecked(project),
                                            vector=vector, threshold=threshold, via=via, failed=failed, agent=agent)
            if created and compared:
                _settle_compared(conn, vault, entry_id, compared, candidate["supersedes"], settling.by, org)
    except psycopg.errors.UniqueViolation:
        raise Val3Error("another session changed the same entries concurrently; run the command again") from None
    if not created:
        return replace(_duplicate(conn, vault, entry_id), failed=failed)
    return Outcome("admitted", f"{vault}#{entry_id}", new_indexes=new, unverified=unverified, kept=kept,
                   failed=failed)


SUPERSEDES = "an unverified entry cannot supersede an entry with evidence or attestation"
_NAMED = {"evidence": "backed by evidence", "attested": "attested", "unverified": "unverified"}


def _unverified(conn, vault: str, entry_id: int) -> bool:
    """Live, with neither evidence nor attestation (ADR 0012 §1)."""
    return _gone(conn, vault, entry_id) is None and backings(conn, vault, [entry_id])[entry_id] == "unverified"


def _via(via: str | None, agent: str | None) -> dict:
    return ({"via": via} if via else {}) | ({"agent": agent} if agent else {})


def _verify(conn, vault: str, entry_id: int, candidate: dict, org: Origin, via: dict | None = None) -> Outcome:
    """ADR 0012 §4: the candidate's checked evidence becomes the unverified entry's, inside the caller's transaction
    under the writer lock. A recipe or a tool is refused: its hash does not cover steps, type or environment."""
    kind = conn.execute(vsql(vault, "SELECT kind FROM {v}.entry WHERE id = %s"), (entry_id,)).fetchone()[0]
    if kind not in ("fact", "preference", "decision"):
        raise Val3Error(f"{vault}#{entry_id} is unverified, and a {kind} is not verified by saving it again: its hash "
                        "does not cover its steps, type or environment")
    now = _instant(conn)  # one instant after the lock, as a confirmation takes
    for item in candidate["evidence"]:
        conn.execute(vsql(vault, """
            INSERT INTO {v}.evidence (entry_id, kind, reproduction, quote, session_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)"""),
            (entry_id, item["kind"], item["reproduction"], item["quote"], org.session, now))
    _log(conn, vault, "verified", org, entry_id=entry_id, detail={"evidence": len(candidate["evidence"])} | (via or {}))
    return Outcome("verified", f"{vault}#{entry_id}")


def _replaceable(conn, vault: str, entry_id: int, slice_at: datetime | None) -> bool:
    """Live, and neither admitted nor confirmed after the slice's last record (design K2a)."""
    if slice_at is None or _gone(conn, vault, entry_id):
        return False
    newest = conn.execute(vsql(vault, """
        SELECT greatest(e.created_at, (SELECT max(created_at) FROM {v}.evidence WHERE entry_id = e.id))
        FROM {v}.entry e WHERE e.id = %s"""), (entry_id,)).fetchone()
    return newest is not None and newest[0] <= slice_at


def _settle_compared(conn, vault: str, entry_id: int, compared: set[int], supersedes: list[str], by: str,
                     org: Origin) -> None:
    """Settle each pair the pairing query found with a neighbour the second call saw (K3, K3a). In a savepoint:
    a failure leaves the pairs open and the admission standing."""
    replaced = {parse_ref(ref)[1] for ref in supersedes}
    try:
        with conn.transaction():
            if not has_neighbours(conn, vault):
                return
            others = [other for (other,) in conn.execute(vsql(vault, """
                SELECT other_id FROM {v}.neighbour WHERE entry_id = %s AND other_id = ANY(%s) ORDER BY other_id"""),
                (entry_id, list(compared)))]
            for other in others:
                _record_settlement(conn, vault, entry_id, other, "replace" if other in replaced else "keep", by, org)
    except psycopg.OperationalError:
        raise
    except psycopg.Error:
        pass


def _record_settlement(conn, vault: str, entry_id: int, other: int, outcome: str, by: str, org: Origin) -> None:
    """Record one pair as settled and log it, inside the caller's transaction."""
    conn.execute(vsql(vault, """
        INSERT INTO {v}.settlement (entry_id, other_id, outcome, settled_by, created_at)
        VALUES (%s, %s, %s, %s, clock_timestamp())"""), (entry_id, other, outcome, by))
    _log(conn, vault, "settled", org, entry_id=entry_id, detail={"other": other, "outcome": outcome, "by": by})


def settle(conn, project: Project, new_ref: str, old_ref: str, outcome: str, by: str,
           session: str | None = None) -> str:
    """Settle one open pair in one transaction under the writer lock; returns the output line."""
    vault = project.home
    ids = []
    for ref in (new_ref, old_ref):
        ref_vault, entry_id = parse_ref(ref)
        if ref_vault != vault:
            raise Val3Error(f"{ref}: only pairs of vault {vault} can be settled")
        ids.append(entry_id)
    new_id, old_id = ids
    new, old = f"{vault}#{new_id}", f"{vault}#{old_id}"
    if not has_neighbours(conn, vault):
        raise Val3Error(f"vault {vault} predates the neighbour check: its owner runs `val3 vault upgrade {vault}`")
    org = origin(project, session)
    with _transaction(conn):
        _serialize(conn, vault)
        if conn.execute(vsql(vault, "SELECT 1 FROM {v}.neighbour WHERE entry_id = %s AND other_id = %s"),
                        (new_id, old_id)).fetchone() is None:
            raise Val3Error(f"{new} has no neighbour {old} to settle (`val3 show {new}` names its neighbours)")
        done = conn.execute(vsql(vault, """
            SELECT outcome, settled_by FROM {v}.settlement WHERE entry_id = %s AND other_id = %s"""),
            (new_id, old_id)).fetchone()
        if done:
            raise Val3Error(f"{new} and {old} were already settled ({done[0]}, by {_one_line(done[1])})")
        for ref, entry_id in ((new, new_id), (old, old_id)):
            gone = _gone(conn, vault, entry_id)
            if gone:
                raise Val3Error(f"{ref} was {_one_line(gone)}; the pair is no longer open")
        if outcome == "replace":
            _supersede(conn, vault, old_id, new_id, org)
            line = f"replaced {old} by {new}"
        elif outcome == "drop":
            found = backings(conn, vault, [new_id, old_id])  # ADR 0012 §3; {} on a vault not upgraded
            if found and RANK[found[new_id]] > RANK[found[old_id]]:
                raise Val3Error(f"{new} is {_NAMED[found[new_id]]} and {old} {_NAMED[found[old_id]]}: a duplicate is "
                                "withdrawn only toward an entry of equal or higher backing")
            w = Withdrawal(ref=new, batch=None, cause="duplicate", reason=f"duplicate of {old}")
            _withdraw_entry(conn, vault, new_id, w, by, None, org)
            line = f"dropped {new} (duplicate of {old})"
        else:
            line = f"kept {new} and {old}"
        _record_settlement(conn, vault, new_id, old_id, outcome, by, org)
    return line


def current(conn, vault: str, ref: str) -> str:
    """The entry that `ref` ends at when its supersessions are followed."""
    ref_vault, entry_id = parse_ref(ref)
    if ref_vault != vault:
        return ref
    while newer := conn.execute(vsql(vault, "SELECT new_id FROM {v}.supersession WHERE old_id = %s"),
                                (entry_id,)).fetchone():
        entry_id = newer[0]
    return f"{vault}#{entry_id}"


def _duplicate(conn, vault: str, entry_id: int) -> Outcome:
    """A duplicate; `reason` says why it is not delivered when the entry is not live (extraction keeps the note)."""
    gone = _gone(conn, vault, entry_id)
    if gone and gone.startswith("withdrawn"):
        gone += "; save it with changed text to bring it back"
    return Outcome("duplicate", f"{vault}#{entry_id}", gone)


def _gone(conn, vault: str, entry_id: int) -> str | None:
    """Why an entry is out of delivery: "superseded by <ref>" or "withdrawn (<cause>): <reason>"; None when live."""
    newer = conn.execute(vsql(vault, "SELECT new_id FROM {v}.supersession WHERE old_id = %s"), (entry_id,)).fetchone()
    if newer:
        return f"superseded by {vault}#{newer[0]}"
    if has_withdrawals(conn, vault):
        withdrawn = conn.execute(vsql(vault, "SELECT cause, reason FROM {v}.withdrawal WHERE entry_id = %s"),
                                 (entry_id,)).fetchone()
        if withdrawn:
            return f"withdrawn ({withdrawn[0]}): {withdrawn[1]}"
    return None


def _serialize(conn, vault: str) -> None:
    """Writer transactions of one vault run one at a time, so a check made inside one (a cited fact is live)
    stays true until it commits. Runs first in the transaction; the lock ends with it."""
    writer_lock(conn, vault)


def _review_reason(candidate: dict) -> str | None:
    """Design D1: evidence that checks admits any kind; only an entry without evidence waits for a person."""
    return None if candidate["evidence"] else "no-evidence"


def _check_refs(conn, vault: str, candidate: dict) -> None:
    for ref in candidate["supersedes"]:
        _require(conn, vault, ref, (candidate["kind"],))
    cited = {}
    for step in candidate["recipe"]["steps"] if candidate["recipe"] else []:
        for ref in step["facts"]:
            cited[_require(conn, vault, ref, ("fact",))] = ref
        if step["tool"]:
            cited[_require(conn, vault, step["tool"], ("tool",))] = step["tool"]
    found = backings(conn, vault, cited)  # ADR 0012 §3; {} on a vault not upgraded
    for entry_id, ref in cited.items():
        if found.get(entry_id) == "unverified":
            raise Val3Error(f"{ref} is unverified: a recipe cites only entries with evidence or attestation")


def _require(conn, vault: str, ref: str, kinds: tuple[str, ...]) -> int:
    ref_vault, entry_id = parse_ref(ref)
    if ref_vault != vault:
        raise Val3Error(f"{ref}: entries may only cite entries of vault {vault}")
    row = conn.execute(vsql(vault, "SELECT kind FROM {v}.entry WHERE id = %s"), (entry_id,)).fetchone()
    if row is None:
        raise Val3Error(f"{ref} does not exist")
    if row[0] not in kinds:
        raise Val3Error(f"{ref} is a {row[0]}, expected {' or '.join(kinds)}")
    gone = _gone(conn, vault, entry_id)
    if gone:
        raise Val3Error(f"{ref} was {gone}")
    return entry_id


def _queue(conn, vault: str, reason: str, payload: dict, entry_id: int | None, org: Origin) -> tuple[int, bool]:
    key = [reason, payload["title"], payload.get("statement", "")]
    if reason == "withdrawal" and payload.get("evidence"):  # without evidence the hash stays as it was
        key.append(payload["evidence"])
    digest = hashlib.sha256(json.dumps(key).encode()).hexdigest()
    # ADR 0012 §8: a retired item frees its hash; the new item's hash names the one it follows
    while (old := conn.execute(vsql(vault, """
            SELECT r.id FROM {v}.review_item r JOIN {v}.review_decision d ON d.review_id = r.id
            WHERE r.content_hash = %s AND d.decision = 'retired'"""), (digest,)).fetchone()):
        key.append(old[0])
        digest = hashlib.sha256(json.dumps(key).encode()).hexdigest()
    row = conn.execute(vsql(vault, """
        INSERT INTO {v}.review_item (reason, payload, content_hash, entry_id,
                                     origin_project, origin_session, origin_machine)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (content_hash) DO NOTHING RETURNING id"""),
        (reason, Jsonb(payload), digest, entry_id, org.project, org.session, org.machine)).fetchone()
    if row is None:
        existing = conn.execute(vsql(vault, "SELECT id FROM {v}.review_item WHERE content_hash = %s"),
                                (digest,)).fetchone()
        return existing[0], False
    _log(conn, vault, "queued", org, entry_id=entry_id, review_id=row[0], detail={"reason": reason})
    return row[0], True


RETIRED = "the review list is retired (ADR 0012): nothing waits for a person"


def decide(conn, project: Project, review_id: int, approve: bool, decided_by: str) -> str:
    vault = project.home
    org = origin(project, None)
    try:
        with _transaction(conn):
            _serialize(conn, vault)
            if upgraded(conn, vault):  # unreachable from the CLI, which checks first; a walk begun before the upgrade
                raise Val3Error(f"{RETIRED}: `val3 review` lists the history of vault {vault}")
            now = _instant(conn)  # the decision and the entry it admits share it (batch_entries)
            row = conn.execute(vsql(vault, """
                SELECT reason, payload, entry_id, origin_project, origin_session, origin_machine
                FROM {v}.review_item WHERE id = %s"""), (review_id,)).fetchone()
            if row is None:
                raise Val3Error(f"no review item #{review_id}")
            reason, payload, entry_id, *item_origin = row
            done = _decision(conn, vault, review_id)
            if done:
                raise Val3Error(f"review #{review_id} was already {done}")
            if reason in NOTICES:
                if not approve:
                    undo = ("a withdrawal is final: save the knowledge again as a new entry" if reason == "withdrawn"
                            else f"to undo it, save an entry that supersedes {vault}#{entry_id}")
                    raise Val3Error(f"review #{review_id} is a notice and can only be approved; {undo}")
                message = f"acknowledged review #{review_id}"
            elif reason == "withdrawal" and approve:
                w = Withdrawal(payload.get("ref"), payload.get("batch"), payload["cause"], payload["reason"],
                               payload.get("evidence"))
                message = f"approved review #{review_id}: {_carry_out(conn, vault, w, decided_by, review_id, org)}"
            elif approve:
                candidate = validate(payload, imported=reason == "import")
                _check_refs(conn, vault, candidate)
                # an imported item says how it came; for another item the channel was not stored
                entry_id, created, _ = _admit(conn, vault, candidate, Origin(*item_origin),
                                               _unchecked(project), now, via="import" if reason == "import" else None)
                outcome = Outcome("admitted", f"{vault}#{entry_id}") if created else _duplicate(conn, vault, entry_id)
                message = f"approved review #{review_id}: {outcome}"
            else:
                entry_id = None
                message = f"rejected review #{review_id}"
            decision = "approved" if approve else "rejected"
            conn.execute(vsql(vault, """
                INSERT INTO {v}.review_decision (review_id, decision, decided_by, entry_id, created_at)
                VALUES (%s, %s, %s, %s, %s)"""), (review_id, decision, decided_by, entry_id, now))
            _log(conn, vault, decision, org, entry_id=entry_id, review_id=review_id)
    except psycopg.errors.UniqueViolation:
        raise Val3Error(f"review #{review_id} was decided concurrently") from None
    except psycopg.errors.UndefinedColumn:  # withdrawal.evidence, added by `vault upgrade`
        raise _predates(vault) from None
    return message


MECHANICAL = "val3 (mechanical)"


def queue_import(conn, project: Project, payload: dict, by: str) -> Outcome:
    """One imported candidate: admitted with a mechanical decision when all its quotes verified (design D1a),
    else queued for a person. An existing review row keeps its outcome. On a vault upgraded for ADR 0012 (§5) every
    candidate is admitted with that decision: one whose quotes did not all verify with no evidence, unverified,
    the attempt's results staying in the review row; one with no quote at all, attested by `by`, who ran the
    import; one whose quotes all verified, equal to a live unverified fact, preference or decision, gives it its
    evidence and counts as a duplicate. The review row is the batch's record, not a queue."""
    candidate = validate(payload, imported=True)
    vault = project.home
    org = origin(project, None)
    quotes = candidate["import"].get("evidence")
    checks = bool(candidate["evidence"]) and isinstance(quotes, list) and all(
        isinstance(item, dict) and item.get("status") == "verified" for item in quotes)
    try:
        with _transaction(conn):
            _serialize(conn, vault)
            switched = upgraded(conn, vault)
            review_id, created = _queue(conn, vault, "import", candidate, None, org)
            if not created:
                done = _decision(conn, vault, review_id)
                if done == "rejected":
                    return Outcome("previously-rejected", f"review #{review_id}", "import")
                if done == "approved":
                    entry_id = conn.execute(vsql(vault, "SELECT entry_id FROM {v}.review_decision WHERE review_id = %s"),
                                            (review_id,)).fetchone()[0]
                    return Outcome("duplicate", f"{vault}#{entry_id}")
                return Outcome("already-queued", f"review #{review_id}", "import")
            if not checks and not switched:
                return Outcome("queued", f"review #{review_id}", "import")
            admitting = candidate if checks else {**candidate, "evidence": []}
            _check_refs(conn, vault, admitting)
            now = _instant(conn)  # the decision and the entry it admits share it (batch_entries)
            entry_id, admitted, new = _admit(conn, vault, admitting, org, _unchecked(project), now, via="import")
            if (not admitted and checks and switched and candidate["kind"] in ("fact", "preference", "decision")
                    and _unverified(conn, vault, entry_id)):  # ADR 0012, amendment 1, §6
                _verify(conn, vault, entry_id, candidate, org)
            if admitted and not checks and not quotes:
                conn.execute(vsql(vault, """
                    INSERT INTO {v}.attestation (entry_id, attested_by, how, created_at) VALUES (%s, %s, %s, %s)"""),
                    (entry_id, by, f"import --apply, batch {candidate['import'].get('batch')}", now))
            conn.execute(vsql(vault, """
                INSERT INTO {v}.review_decision (review_id, decision, decided_by, entry_id, created_at)
                VALUES (%s, 'approved', %s, %s, %s)"""), (review_id, MECHANICAL, entry_id, now))
            _log(conn, vault, "approved", org, entry_id=entry_id, review_id=review_id)
    except psycopg.errors.CheckViolation:  # review_item_reason_check still lacks 'import'
        raise Val3Error(f"vault {vault} predates imports: its owner runs `val3 vault upgrade {vault}`") from None
    if not admitted:
        return _duplicate(conn, vault, entry_id)
    return Outcome("admitted", f"{vault}#{entry_id}", new_indexes=new, unverified=not checks and bool(quotes))


def log_import(conn, project: Project, detail: dict) -> None:
    """One event per applied batch: repository, snapshot and counts."""
    with _transaction(conn):
        _log(conn, project.home, "import", origin(project, None), detail=detail)


@dataclass(frozen=True)
class Withdrawal:
    """What `val3 withdraw` asks for: one entry (`ref`) or one import batch, its cause and a reason."""
    ref: str | None
    batch: str | None
    cause: str   # wrong: never true; obsolete: true once, no longer needed
    reason: str
    evidence: list[dict] | None = None  # checked items (design L6, L8); null for a batch

    @property
    def title(self) -> str:
        return f"withdraw {self.ref or 'batch ' + str(self.batch)} ({self.cause}): {self.reason}"


def withdraw(conn, project: Project, w: Withdrawal, by: str, transcript: Path | None = None,
             session: str | None = None, private: tuple[str, ...] = ()) -> str:
    """Withdraw now, in one transaction under the vault lock (design §3.1); returns the output line."""
    vault = project.home
    org = origin(project, session)
    if w.evidence:
        # one read of the transcript gives both the verdict and the calls: a second read could see another file
        failures, printed = _evaluate(w.evidence, transcript, private)
        if failures:
            raise _discarded(conn, vault, w.title, failures, org)
        if w.ref:  # ADR 0006, amendment 1: the direct withdrawal records which calls printed each quote
            w = replace(w, evidence=_with_calls(w.evidence, printed))
    try:
        with _transaction(conn):
            _serialize(conn, vault)
            return _carry_out(conn, vault, w, by, None, org)
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        raise _predates(vault) from None


def withdraws_at_once(w: Withdrawal) -> bool:
    """Design L6 and N3: the agent's evidence would admit an entry of any kind without review, so a withdrawal
    of one entry with evidence (checked by `withdraw`) takes effect at once."""
    return bool(w.ref and w.evidence)


def propose_withdrawal(conn, project: Project, w: Withdrawal, session: str | None, transcript: Path | None = None,
                       private: tuple[str, ...] = ()) -> str:
    """Inside the agent: the checks of a withdrawal, then a review item a person decides (design §3.2)."""
    vault = project.home
    org = origin(project, session)
    if w.evidence:
        _check_evidence(conn, vault, w.title, w.evidence, transcript, org, private)
    try:
        with _transaction(conn):
            _serialize(conn, vault)
            if upgraded(conn, vault):  # ADR 0012 §6: unreachable from the CLI, which checks first
                raise Val3Error(f"vault {vault} takes no withdrawal proposals: {PERSONS}")
            if w.evidence:  # approving it writes withdrawal.evidence: refuse now on a table without it
                conn.execute(vsql(vault, "SELECT evidence FROM {v}.withdrawal LIMIT 0"))
            if w.ref:
                entry_id = _withdrawable(conn, vault, w.ref)
            else:
                entry_id = None
                if not any(not existed for _, existed in batch_entries(conn, vault, w.batch)):
                    raise Val3Error(f"batch {w.batch} has no admitted entries")
            payload = {"title": w.title, ("ref" if w.ref else "batch"): w.ref or w.batch, "cause": w.cause,
                       "reason": w.reason}
            if w.evidence:
                payload["evidence"] = w.evidence
            review_id, created = _queue(conn, vault, "withdrawal", payload, entry_id, org)
            done = None if created else _decision(conn, vault, review_id)
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn,
            psycopg.errors.CheckViolation):  # vault not upgraded
        raise _predates(vault) from None
    if created:
        return f"proposed: review #{review_id}"
    return f"{'previously rejected' if done == 'rejected' else 'already proposed'}: review #{review_id}"


PERSONS = ("a withdrawal without evidence, or of a batch, is a person's: run it in a terminal outside the agent "
           "session (a withdrawal is final, and the same text cannot be saved again)")


def _predates(vault: str) -> Val3Error:
    return Val3Error(f"vault {vault} predates withdrawals: its owner runs `val3 vault upgrade {vault}`")


def _withdrawable(conn, vault: str, ref: str) -> int:
    """The id of an entry of the home vault that is not withdrawn yet."""
    ref_vault, entry_id = parse_ref(ref)
    if ref_vault != vault:
        raise Val3Error(f"{ref}: only entries of vault {vault} can be withdrawn")
    if conn.execute(vsql(vault, "SELECT 1 FROM {v}.entry WHERE id = %s"), (entry_id,)).fetchone() is None:
        raise Val3Error(f"{ref} does not exist")
    done = conn.execute(vsql(vault, "SELECT withdrawn_by, created_at FROM {v}.withdrawal WHERE entry_id = %s"),
                        (entry_id,)).fetchone()
    if done:
        raise Val3Error(f"{ref} was already withdrawn on {done[1]:%Y-%m-%d %H:%M} by {done[0]}")
    return entry_id


def _carry_out(conn, vault: str, w: Withdrawal, by: str, review_id: int | None, org: Origin) -> str:
    """The withdrawal itself, inside the caller's transaction; returns the output line."""
    if w.ref:
        _withdraw_entry(conn, vault, _withdrawable(conn, vault, w.ref), w, by, review_id, org)
        return f"withdrawn {w.ref}"
    entries = batch_entries(conn, vault, w.batch)
    created = [entry_id for entry_id, existed in entries if not existed]
    if not created:
        raise Val3Error(f"batch {w.batch} has no admitted entries")
    gone = {entry_id for (entry_id,) in conn.execute(vsql(vault, """
        SELECT entry_id FROM {v}.withdrawal WHERE entry_id = ANY(%s)"""), (created,))}
    for entry_id in created:
        if entry_id not in gone:
            _withdraw_entry(conn, vault, entry_id, w, by, review_id, org)
    pending = [review for (review,) in conn.execute(vsql(vault, """
        SELECT i.id FROM {v}.review_item i
        WHERE i.reason = 'import' AND i.payload->'import'->>'batch' = %s
          AND NOT EXISTS (SELECT 1 FROM {v}.review_decision d WHERE d.review_id = i.id) ORDER BY i.id"""), (w.batch,))]
    for review in pending:
        conn.execute(vsql(vault, """
            INSERT INTO {v}.review_decision (review_id, decision, decided_by) VALUES (%s, 'rejected', %s)"""),
            (review, by))
        _log(conn, vault, "rejected", org, review_id=review, detail={"batch": w.batch})
    return (f"batch {w.batch}: {len(created) - len(gone)} withdrawn, {len(pending)} pending rejected, "
            f"{len(entries) - len(created)} not withdrawn (existed before the batch), {len(gone)} already withdrawn")


def batch_entries(conn, vault: str, batch: str) -> list[tuple[int, bool]]:
    """(entry, existed before the batch) for each entry an approved import item of `batch` points at (design §4.2).

    An entry existed before when no decision of the batch created it: a decision that admits writes the entry in
    its own transaction, and both rows carry the one instant taken after the writer lock; an entry admitted
    earlier holds an older one, since its transaction held the lock first."""
    return conn.execute(vsql(vault, """
        SELECT d.entry_id, bool_and(e.created_at < d.created_at)
        FROM {v}.review_decision d
        JOIN {v}.review_item i ON i.id = d.review_id
        JOIN {v}.entry e ON e.id = d.entry_id
        WHERE d.decision = 'approved' AND i.reason = 'import' AND i.payload->'import'->>'batch' = %s
        GROUP BY d.entry_id ORDER BY d.entry_id"""), (batch,)).fetchall()


def _withdraw_entry(conn, vault: str, entry_id: int, w: Withdrawal, by: str, review_id: int | None,
                    org: Origin) -> None:
    # the evidence column is named only when there is evidence: a table that predates it still takes the rest
    # clock_timestamp: after the lock, so a confirmation and a withdrawal are ordered as they held it (design N2)
    columns = "entry_id, cause, reason, withdrawn_by, review_id, batch" + (", evidence" if w.evidence else "")
    values = (entry_id, w.cause, w.reason, by, review_id, w.batch) + ((Jsonb(w.evidence),) if w.evidence else ())
    conn.execute(vsql(vault, f"INSERT INTO {{v}}.withdrawal ({columns}, created_at) "
                             f"VALUES ({', '.join(['%s'] * len(values))}, clock_timestamp())"), values)
    _log(conn, vault, "withdrawn", org, entry_id=entry_id, review_id=review_id,
         detail={"cause": w.cause, "reason": w.reason, "batch": w.batch})


def _check_evidence(conn, vault: str, title: str, evidence: list[dict], transcript: Path | None, org: Origin,
                    private: tuple[str, ...] = ()) -> None:
    """Every quote but a `file` one (checked against the repository) must be in the transcript: a tool
    quote in the output of one call whose input does not contain it (design E1)."""
    failures = _quote_failures(evidence, transcript, private)
    if failures:
        raise _discarded(conn, vault, title, failures, org)


def _discarded(conn, vault: str, title: str, failures: list[tuple[str, str]], org: Origin) -> Val3Error:
    """Log the candidate's first failed quote as `discarded` and give the error that names it."""
    quote, message = failures[0]
    _log_best_effort(conn, vault, "discarded", org, {"title": title, "quote": quote})
    # The quote is not repeated: this message becomes tool output, and a retry would match it.
    return Val3Error(f"discarded: evidence quote {message}")


def _quote_failures(evidence: list[dict], transcript: Path | None,
                    private: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """(quote, why it fails) for each quote of `evidence` not in the transcript as `_check_evidence` requires, in
    order; raises when there are quotes to check and no transcript."""
    return _evaluate(evidence, transcript, private)[0]


def _evaluate(evidence: list[dict], transcript: Path | None,
              private: tuple[str, ...] = ()) -> tuple[list[tuple[str, str]], dict[int, list[Call]]]:
    """The failures as `_quote_failures` gives them, and for each tool item that checks (by its position in
    `evidence`) the calls that make it check: the quote in their output and not in their input."""
    evidence = [(i, item) for i, item in enumerate(evidence) if item["kind"] != "file"]
    if not evidence:
        return [], {}
    if transcript is None:
        raise Val3Error("evidence needs the session transcript: pass --transcript or set VAL3_TRANSCRIPT")
    calls, user_text = evidence_calls(transcript, private)
    failures, found = [], {}
    for i, item in evidence:
        quote = item["quote"]
        if item["kind"] == "user":
            if quote in user_text:
                continue
            message = f"not found in the user's messages of {transcript} (evidence[{i}], {len(quote)} characters)"
        else:
            printed = [call for call in calls if quote in call.output]
            qualifying = [call for call in printed if quote not in call.given]
            if qualifying:
                found[i] = qualifying
                continue
            message = (f"is in the input of the call that printed it (evidence[{i}]): "
                       "quote output the call did not already contain" if printed else
                       f"not found in tool output of {transcript} (evidence[{i}], {len(quote)} characters)")
        failures.append((quote, message))
    return failures, found


MAX_CALLS = 20  # calls recorded per withdrawal evidence item; the rest are counted


def _with_calls(evidence: list[dict], printed: dict[int, list[Call]]) -> list[dict]:
    """`evidence` with, on each tool item, the distinct calls that printed its quote: tool, id and a SHA-256 of
    the complete input, never the input's text (a command line can hold a secret; the row is permanent and
    mirrored). `reproduction_is_command`: the agent's reproduction is that Bash or typed command, to the letter."""
    out = []
    for i, item in enumerate(evidence):
        distinct: dict[object, dict] = {}
        for call in printed.get(i, []):
            record = _call_record(call, item["reproduction"])
            # a result repeated under one id is one call; one id declared with two inputs is two
            distinct.setdefault((record["id"], record.get("record"), record["input_sha256"]), record)
        if not distinct:
            out.append(item)
            continue
        # those that ran the reproduction first, so that the limit never hides one
        calls = sorted(distinct.values(), key=lambda record: not record["reproduction_is_command"])
        item = {**item, "calls": calls[:MAX_CALLS]}
        if len(calls) > MAX_CALLS:
            item["more_calls"] = len(calls) - MAX_CALLS
        out.append(item)
    return out


_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:!-]{1,128}")


def _call_record(call: Call, reproduction: str) -> dict:
    if call.id is None:  # a typed command: its whole body
        body, command = str(call.input), call.input
    else:
        body = json.dumps(call.input, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        command = call.input.get("command") if call.tool == "Bash" and isinstance(call.input, dict) else None
    # the name and the id come from the transcript: a name that is not a plain one is not kept, and an id is
    # stored in ASCII without NUL (jsonb takes neither a NUL nor a lone surrogate)
    record = {"tool": call.tool if isinstance(call.tool, str) and _TOOL_NAME.fullmatch(call.tool) else None,
              "id": None if call.id is None else call.id.encode("ascii", "backslashreplace").decode().replace(
                  "\x00", "\\x00"),
              "input_sha256": hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest(),
              "reproduction_is_command": isinstance(command, str) and command.strip() == reproduction.strip() != ""}
    if call.id is None:
        record["record"] = call.record
    return record


def confirm(conn, project: Project, ref: str, evidence: list, transcript: Path | None, session: str | None,
            private: tuple[str, ...] = ()) -> str:
    """Append a new current evidence set to a live entry of the home vault (design L2, L4, L5)."""
    vault = project.home
    org = origin(project, session)
    entry_id = _require(conn, vault, ref, KINDS)
    sha = None
    if isinstance(evidence, list) and any(isinstance(item, dict) and item.get("kind") == "file" for item in evidence):
        repo = recheck.repository(project)
        if repo is None:
            raise Val3Error(f"file evidence needs {project.root} to be the root of a git repository")
        sha = recheck.head(repo)
    items = evidence_items(evidence, sha)
    if sha:
        recheck.check_files(repo, sha, items)
    title = conn.execute(vsql(vault, "SELECT title FROM {v}.entry WHERE id = %s"), (entry_id,)).fetchone()[0]
    _check_evidence(conn, vault, title, items, transcript, org, private)
    with _transaction(conn):
        _serialize(conn, vault)
        _require(conn, vault, ref, KINDS)  # again under the lock: it may have gone meanwhile
        # one instant after the lock, so a confirmation that waited for it is still the newest set
        now = _instant(conn)
        for item in items:
            conn.execute(vsql(vault, """
                INSERT INTO {v}.evidence (entry_id, kind, reproduction, quote, session_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)"""),
                (entry_id, item["kind"], item["reproduction"], item["quote"], org.session, now))
        _log(conn, vault, "confirmed", org, entry_id=entry_id, detail={"evidence": len(items)})
    at = f" at {sha[:12]}" if sha else ""
    return f"confirmed {vault}#{entry_id} ({len(items)} evidence items{at})"


def _entry_by_hash(conn, vault: str, digest: str) -> int | None:
    row = conn.execute(vsql(vault, "SELECT id FROM {v}.entry WHERE content_hash = %s"), (digest,)).fetchone()
    return row[0] if row else None


def _instant(conn):
    """One instant after the writer lock, so rows written by a transaction that waited for it are not older than
    rows of the transaction that held it."""
    return conn.execute("SELECT clock_timestamp()").fetchone()[0]


def _unchecked(project: Project) -> bool:
    """The project runs no neighbour check (no [embedding], or `neighbour = false`): what it admits is exempt, so
    turning the check on later pairs no two of those entries."""
    return project.embedding is None or project.embedding.neighbour is None


def _admit(conn, vault: str, candidate: dict, org: Origin, unembedded: bool = False, now=None,
           vector: tuple[Space, list[float]] | None = None,
           threshold: float | None = None, via: str | None = None,
           failed: int = 0, agent: str | None = None) -> tuple[int, bool, tuple[str, ...]]:
    """Insert an entry and its parts; return (id, created, index names it created). Index names are stored after
    aliases. An entry admitted while the project runs no neighbour check (`unembedded`) is exempt from the neighbour
    check against other exempt entries (design D2a). The entry, its evidence and its supersession take one instant,
    `now` (default: taken here, after the caller's lock), so a replay never sees it live beside the entry it
    supersedes. With `vector` (capture computed it, design K3a) the entry's vector is stored and paired before the
    supersessions, while the entries it replaces are still live; in a savepoint, so a failure leaves it to
    `store_vectors`. `via` (capture, save, import, tool-add) goes in the `admitted` event's detail; None records
    nothing; `failed` (quotes capture dropped) goes there too when not zero, never the quotes. Runs inside the
    caller's transaction."""
    now = now or _instant(conn)
    digest = content_hash(candidate)
    row = conn.execute(vsql(vault, """
        INSERT INTO {v}.entry (kind, title, statement, keywords, content_hash,
                               origin_project, origin_session, origin_machine, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (content_hash) DO NOTHING RETURNING id"""),
        (candidate["kind"], candidate["title"], candidate["statement"], " ".join(candidate["keywords"]),
         digest, org.project, org.session, org.machine, now)).fetchone()
    if row is None:
        return _entry_by_hash(conn, vault, digest), False, ()
    entry_id = row[0]
    if unembedded and has_neighbours(conn, vault):
        conn.execute(vsql(vault, "INSERT INTO {v}.neighbour_exempt (entry_id) VALUES (%s)"), (entry_id,))
    for item in candidate["evidence"]:
        conn.execute(vsql(vault, """
            INSERT INTO {v}.evidence (entry_id, kind, reproduction, quote, session_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)"""),
            (entry_id, item["kind"], item["reproduction"], item["quote"], org.session, now))
    new_indexes = []
    for name in candidate["indexes"]:
        name = indexes.canonical(conn, vault, name)
        if conn.execute(vsql(vault, """
                INSERT INTO {v}.knowledge_index (name) VALUES (%s) ON CONFLICT (name) DO NOTHING RETURNING name"""),
                        (name,)).fetchone():
            new_indexes.append(name)
        conn.execute(vsql(vault, """
            INSERT INTO {v}.entry_index (entry_id, index_id)
            SELECT %s, id FROM {v}.knowledge_index WHERE name = %s ON CONFLICT DO NOTHING"""),
            (entry_id, name))
    recipe = candidate["recipe"]
    if recipe:
        for parameter in recipe["parameters"]:
            conn.execute(vsql(vault, """
                INSERT INTO {v}.recipe_parameter (recipe_id, name, description) VALUES (%s, %s, %s)"""),
                (entry_id, parameter["name"], parameter["description"]))
        for position, step in enumerate(recipe["steps"], start=1):
            tool_id = parse_ref(step["tool"])[1] if step["tool"] else None
            conn.execute(vsql(vault, """
                INSERT INTO {v}.recipe_step (recipe_id, position, action, tool_id) VALUES (%s, %s, %s, %s)"""),
                (entry_id, position, step["action"], tool_id))
            for ref in dict.fromkeys(step["facts"]):
                conn.execute(vsql(vault, """
                    INSERT INTO {v}.step_fact (recipe_id, position, fact_id) VALUES (%s, %s, %s)"""),
                    (entry_id, position, parse_ref(ref)[1]))
        for example in recipe["examples"]:
            conn.execute(vsql(vault, """
                INSERT INTO {v}.recipe_example (recipe_id, parameters, result, session_id)
                VALUES (%s, %s, %s, %s)"""),
                (entry_id, Jsonb(example["parameters"]), example["result"], org.session))
    tool = candidate["tool"]
    if tool:
        conn.execute(vsql(vault, """
            INSERT INTO {v}.tool (entry_id, tool_type, environment, priority) VALUES (%s, %s, %s, %s)"""),
            (entry_id, tool["type"], tool["environment"], tool["priority"]))

    if vector:
        try:
            with conn.transaction():
                _store_vector(conn, vault, entry_id, *vector, threshold)
        except psycopg.OperationalError:
            raise
        except psycopg.Error:
            pass
    for ref in candidate["supersedes"]:
        _supersede(conn, vault, parse_ref(ref)[1], entry_id, org, now)
    detail = _via(via, agent) | ({"failed_quotes": failed} if failed else {})
    _log(conn, vault, "admitted", org, entry_id=entry_id, detail=detail or None)
    return entry_id, True, tuple(new_indexes)


def _supersede(conn, vault: str, old_id: int, new_id: int, org: Origin, now=None) -> None:
    """No notice: the event log holds it, and a recipe citing the old entry is marked when read (design N1, N2).
    `now` is the new entry's instant on admission; otherwise one taken here, after the lock. ADR 0012 §3: an
    unverified entry does not supersede one with evidence or attestation."""
    found = backings(conn, vault, [old_id, new_id])
    if found.get(new_id) == "unverified" and found[old_id] != "unverified":
        raise Val3Error(f"{vault}#{new_id} cannot supersede {vault}#{old_id}: {SUPERSEDES}")
    conn.execute(vsql(vault, "INSERT INTO {v}.supersession (old_id, new_id, created_at) "
                             "VALUES (%s, %s, coalesce(%s, clock_timestamp()))"), (old_id, new_id, now))
    _log(conn, vault, "superseded", org, entry_id=old_id, detail={"by": new_id})


def _decision(conn, vault: str, review_id: int) -> str | None:
    row = conn.execute(vsql(vault, "SELECT decision FROM {v}.review_decision WHERE review_id = %s"),
                       (review_id,)).fetchone()
    return row[0] if row else None


def _log_best_effort(conn, vault: str, action: str, org: Origin, detail: dict) -> None:
    """Invalid and discarded candidates are logged when the role may; the reason is reported either way."""
    try:
        _log(conn, vault, action, org, detail=detail)
    except psycopg.errors.InsufficientPrivilege:
        pass


def _log(conn, vault: str, action: str, org: Origin, entry_id=None, review_id=None, detail=None) -> None:
    conn.execute(vsql(vault, """
        INSERT INTO {v}.event (action, entry_id, review_id, project, session_id, machine, detail)
        VALUES (%s, %s, %s, %s, %s, %s, %s)"""),
        (action, entry_id, review_id, org.project, org.session, org.machine, Jsonb(detail or {})))


def add_tool(conn, project: Project, name: str, tool_type: str, description: str, questions: list[str],
             environment: str, priority: int, indexes: list[str], by: str) -> Outcome:
    """Register a tool on a person's command: admitted without review, attested by `by` (ADR 0012 §1)."""
    candidate = validate({"kind": "tool", "title": name, "statement": description, "keywords": questions,
                          "indexes": indexes,
                          "tool": {"type": tool_type, "environment": environment, "priority": priority}})
    with _transaction(conn):
        _serialize(conn, project.home)
        entry_id, created, new = _admit(conn, project.home, candidate, origin(project, None),
                                        _unchecked(project), via="tool-add")
        if not created:
            return _duplicate(conn, project.home, entry_id)
        if upgraded(conn, project.home):
            conn.execute(vsql(project.home, """
                INSERT INTO {v}.attestation (entry_id, attested_by, how) VALUES (%s, %s, 'tool add')"""),
                (entry_id, by))
    return Outcome("admitted", f"{project.home}#{entry_id}", new_indexes=new)


def merge_index(conn, project: Project, source: str, into: str, session: str | None = None) -> str:
    """Merge the home vault's index `source` into `into` (design D6): an alias row, final like a withdrawal.
    Under the writer lock, so two opposed merges cannot both pass the checks and form a cycle."""
    vault = project.home
    with _transaction(conn):
        _serialize(conn, vault)
        if not indexes.has_aliases(conn, vault):
            raise Val3Error(f"vault {vault} predates index merges: its owner runs `val3 vault upgrade {vault}`")
        for name in (source, into):
            if conn.execute(vsql(vault, "SELECT 1 FROM {v}.knowledge_index WHERE name = %s"), (name,)).fetchone() is None:
                raise Val3Error(f"no index {name} in vault {vault}")
        if conn.execute(vsql(vault, "SELECT 1 FROM {v}.index_alias WHERE name = %s"), (source,)).fetchone():
            raise Val3Error(f"{source} is already merged into {indexes.canonical(conn, vault, source)}")
        target = indexes.canonical(conn, vault, into)
        if target == source:
            raise Val3Error(f"cannot merge {source} into itself" if into == source
                            else f"{into} is already merged into {source}")
        n = conn.execute(vsql(vault, f"""
            SELECT count(*) FROM {indexes.resolved(conn, vault)} r JOIN {{v}}.entry e ON e.id = r.entry_id
            WHERE r.name = %(name)s AND {live(conn, vault)}"""), {"name": source, "as_of": None}).fetchone()[0]
        conn.execute(vsql(vault, """
            INSERT INTO {v}.index_alias (name, target_id) SELECT %s, id FROM {v}.knowledge_index WHERE name = %s"""),
            (source, target))
        _log(conn, vault, "index-merged", origin(project, session), detail={"from": source, "into": target})
    return f"merged {source} into {target} ({n} entries)"


# Design D2, D2a: pair entry %(id)s, just given a vector in one space, with every live entry of its kind whose
# vector in that space is close enough, unless both are exempt. The later-embedded side finds the earlier one.
_NEIGHBOURS = """
    WITH m AS MATERIALIZED (  -- only vectors of the given space reach the distance operator
        SELECT entry_id, vector FROM {{v}}.embedding
        WHERE model = %(model)s AND digest = %(digest)s AND text_version = %(text_version)s AND dims = %(dims)s
    )
    INSERT INTO {{v}}.neighbour (entry_id, other_id, score)
    SELECT greatest(e.id, o.id), least(e.id, o.id), 1 - (me.vector <=> mo.vector)
    FROM {{v}}.entry e
    JOIN m me ON me.entry_id = e.id
    JOIN {{v}}.entry o ON o.kind = e.kind AND o.id <> e.id
    JOIN m mo ON mo.entry_id = o.id
    WHERE e.id = %(id)s AND {e} AND {o} AND 1 - (me.vector <=> mo.vector) >= %(threshold)s
      AND NOT (EXISTS (SELECT 1 FROM {{v}}.neighbour_exempt x WHERE x.entry_id = e.id)
               AND EXISTS (SELECT 1 FROM {{v}}.neighbour_exempt x WHERE x.entry_id = o.id))
    ON CONFLICT DO NOTHING"""

VECTOR_BATCH = 16
VECTOR_TIMEOUT = 60.0
ADMISSION_TIMEOUT = 10.0  # seconds; a cold load of the model takes about 2.5 s


def admission_vectors(conn, project: Project) -> str | None:
    """After an admission: embed one batch of what lacks a vector, newest first, so the admitted entry is in it.

    A failure is returned as a warning, even a lost connection: the admission already reported stands and
    `val3 embed` and the drainer store the rest."""
    if project.embedding is None:
        return None
    try:
        store_vectors(conn, project, ADMISSION_TIMEOUT, limit=VECTOR_BATCH)
    except (EmbeddingError, psycopg.Error) as exc:
        return safe(_one_line(f"no vector stored ({exc}); run `val3 embed` later"))  # also shown by review --tty
    return None


def warning(message: str) -> str:
    return f"val3: warning: {safe(_one_line(message))}"  # exception text holds config values


def index_lines(outcome: Outcome) -> list[str]:
    return [safe(f"val3: new knowledge index: {name} (existing: val3 index)") for name in outcome.new_indexes]


def submit_notes(outcome: Outcome) -> list[str]:
    """What `submit` did not do (a supersession it did not make) and the indexes it created, as lines."""
    kept = [warning(f"{outcome.ref} does not supersede {old}: {SUPERSEDES}") for old in outcome.kept]
    return kept + index_lines(outcome)


def after_admission(conn, project: Project, outcome: Outcome) -> list[str]:
    """The work after an admission has committed, and its lines: one batch of vectors and a line per close
    neighbour with the ways to settle it (design S1). Nothing here undoes or fails the admission."""
    lines: list[str] = []
    if outcome.status != "admitted":
        return lines
    if failure := admission_vectors(conn, project):
        lines.append(warning(failure))
    vault, entry_id = parse_ref(outcome.ref)
    try:  # vectors commit one by one: a failure later in the batch leaves this pair
        pairs = neighbour.open_pairs(conn, vault, [entry_id], project).get(entry_id, [])
    except psycopg.Error:  # the mark shows in the next index
        pairs = []
    return lines + [f"val3: near {vault}#{other} ({score:.2f}): val3 settle {outcome.ref} {vault}#{other} "
                    "--replace | --drop | --keep" for other, score in pairs]


@dataclass(frozen=True)
class Settling:
    """What capture settled for a candidate before submitting it (design K3)."""
    space: Space
    vector: list[float]
    compared: tuple[int, ...] = ()
    replaces: tuple[int, ...] = ()
    repeat_of: int | None = None
    slice_at: datetime | None = None
    by: str = ""


def _store_vector(conn, vault: str, entry_id: int, space: Space, vector: list[float],
                  threshold: float | None) -> int:
    """Store one entry's vector and pair it with its neighbours (design D2), inside the caller's transaction under
    the writer lock; returns 1 when stored, 0 when the entry already had a vector in that space."""
    added = conn.execute(vsql(vault, """
        INSERT INTO {v}.embedding (entry_id, model, digest, text_version, dims, vector)
        VALUES (%s, %s, %s, %s, %s, %s::vector) ON CONFLICT DO NOTHING"""),
        (entry_id, space.model, space.digest, space.text_version, space.dims, vector_literal(vector))).rowcount
    # decided under the lock: a `vault upgrade` may have finished while the vector was requested
    if added and threshold is not None and has_neighbours(conn, vault):
        pairing = vsql(vault, _NEIGHBOURS.format(e=live(conn, vault, "e"), o=live(conn, vault, "o")))
        conn.execute(pairing, {"id": entry_id, "model": space.model, "digest": space.digest,
                               "text_version": space.text_version, "dims": space.dims, "as_of": None,
                               "threshold": threshold})
    return added


def store_vectors(conn, project: Project, timeout: float = VECTOR_TIMEOUT, limit: int | None = None) -> int:
    """Embed entries of the home vault that have no vector in the service's current space, newest first.

    Superseded entries are embedded too, so `val3 eval` can replay moments when they were live.

    `limit` bounds the work right after an admission; `val3 embed` and the drainer pass none.
    The digest lookup and the first batch share `timeout`; each later batch has its own.
    Runs outside any transaction: each vector commits alone, under the writer lock, with the neighbour check of
    its entry (design D2), and an admission is never undone.
    Returns how many vectors were stored; raises EmbeddingError when the service or the vault fails."""
    settings = project.embedding
    if settings is None:
        raise Val3Error(f"no [embedding] table in {project.root / CONFIG_FILE}")
    vault = project.home
    stored = 0
    try:
        deadline = time.monotonic() + timeout  # the digest and the first batch share one timeout
        current = digest(settings, timeout)
        rows = conn.execute(vsql(vault, """
            SELECT e.id, e.title, e.statement FROM {v}.entry e
            WHERE NOT EXISTS (SELECT 1 FROM {v}.embedding m WHERE m.entry_id = e.id AND m.model = %s
                                AND m.digest = %s AND m.text_version = %s)
            ORDER BY e.id DESC LIMIT %s"""), (settings.model, current, TEXT_VERSION, limit)).fetchall()
        for start in range(0, len(rows), VECTOR_BATCH):
            batch = rows[start:start + VECTOR_BATCH]
            if start:
                deadline = time.monotonic() + timeout  # each later batch gets its own
            space, vectors = embed(settings, [entry_text(title, statement) for _, title, statement in batch],
                                   deadline - time.monotonic())
            for (entry_id, _, _), vector in zip(batch, vectors):
                with conn.transaction():
                    _serialize(conn, vault)  # a concurrent store of the other side of a pair waits for this one
                    stored += _store_vector(conn, vault, entry_id, space, vector, settings.neighbour)
    except psycopg.errors.UndefinedTable:
        raise no_table(vault) from None
    except psycopg.OperationalError:
        raise
    except psycopg.Error as exc:  # e.g. no INSERT privilege on the table: the entry stays without a vector
        raise EmbeddingError(f"vault {vault}: {str(exc).strip().splitlines()[0]}") from None
    return stored


def describe(conn, vault: str, description: str, parent: str | None) -> None:
    """Append a note to the vault catalogue: the vault's description and parent from now on (design §4.2).
    Row-level security admits it only from a role that can write the vault."""
    check_vault_name(vault)
    if parent is not None:
        check_vault_name(parent)
    if not 1 <= len(description) <= 300 or _one_line(description) != description:
        raise Val3Error("a vault description is one line of 1 to 300 characters")
    conn.execute("INSERT INTO val3_common.vault_note (vault, parent, description) VALUES (%s, %s, %s)",
                 (vault, parent, description))
