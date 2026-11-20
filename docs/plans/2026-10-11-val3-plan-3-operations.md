# VAL3 Plan 3 — Operations: terminal review, database backup, deferred minors

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person decides the review list in one terminal pass with every card complete and safe to read, the whole `val3` database is backed up into a private file that is proven to restore, and the 17 deferred minors still in the code plus one writer race are fixed.

**Architecture:** One card renderer (`views.review_card`) serves `val3 review`, `review show` and the new `review --tty` walk (`tty.walk`), which calls the writer's existing `decide()` per item. `val3 backup` (`backup.backup`) checks `pg_dump`'s version against the server and runs it into a file created exclusively with mode 0600. Writer transactions of a vault take one advisory lock first. The minors are local fixes in the modules that own them.

**Tech Stack:** as plans 1, 2 and 4: Python ≥ 3.11 standard library, `psycopg[binary]`, PostgreSQL 16 with pgvector (`pgvector/pgvector:0.8.1-pg16`), pytest, Docker; plus the PostgreSQL 16 client tools (`pg_dump`, `pg_restore`) on `PATH`.

**Spec:** `docs/design/2026-10-11-operations-design.md` (approved 2026-10-11). The main design `docs/design/2026-10-08-val3-design.md`, plans 1, 2 and 4 and ADRs 0001-0002 still bind except where the spec says otherwise.

## Verified facts this plan relies on (2026-10-11, development machine)

1. The development machine has `pg_dump` 14 and 16 installed; Debian's `pg_wrapper` picks 14 because a local (stopped) 14 cluster exists. `~/.local/bin/pg_dump` and `~/.local/bin/pg_restore` now link to `/usr/lib/postgresql/16/bin/`, and `~/.local/bin` is first on `PATH`: `pg_dump --version` prints `pg_dump (PostgreSQL) 16.15`, and `pg_dump -Fc --no-password "service=val3dev"` dumped the development database (434 216 bytes). `pg_dump` creates its output with mode 0644 by default.
2. Real transcripts store a message typed while the agent is busy as `{"type": "attachment", "attachment": {"type": "queued_command", "prompt": "...", "origin": {"kind": "human"}, "humanTurn": true, ...}}`; harness turns arrive the same way with a prompt starting `<task-notification>`. A `!` command typed by the user is two user records with string content: `<bash-input>pwd</bash-input>` and `<bash-stdout>/home/...</bash-stdout><bash-stderr></bash-stderr>`.
3. `json.loads("[" * 100000)` raises `RecursionError`, which is not a `ValueError`.
4. The harness's `.consolidate-lock` holds JSON with `holderPid` (the existing inbox test writes `{"holderPid": 1}`).
5. Review items of reason `recipe-cites-superseded` in the development database: 0. Supersession notices store the new entry in `review_item.entry_id`; the old one is `supersession.old_id` for that `new_id`.

## Global Constraints

- Python `>=3.11`, standard library plus `psycopg[binary]>=3.2`; no new Python dependency.
- Everything in English (code, comments, CLI messages, docs, commits). No references to internal company projects, hosts or people: the repository will be published (example vault name in docs: `invoices`).
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error. Hooks always exit 0.
- Tests drive the CLI as a subprocess against the disposable PostgreSQL and fixture trees, asserting on exit codes, output and database rows; tests never import `val3`.
- The writer is the only code that writes vault rows; vault SQL goes through `vsql`.
- Decisions on the review list belong to a person: `--tty`, `approve`, `reject` and `tool add` refuse to run when `CLAUDECODE` is set to a non-empty value.
- A reproduction is shown, never executed.
- Text taken from a vault is printed with control characters other than newline and tab escaped (ESC shown as `\x1b`).
- Backup files: mode 0600, created exclusively (`O_CREAT | O_EXCL`), never overwritten; the default directory `~/.local/share/val3/backup/` is created 0700.
- Work on branch `feature/plan-3-operations`; Conventional Commits; run the suite with `.venv/bin/python -m pytest -q`.

## Review Focus

1. **Terminal control sequences in vault text** — a title holding `\x1b[2J` or an OSC sequence is printed escaped by `review`, `review show`, `--tty` and `show`; it cannot clear or retitle the terminal (test in Task 1).
2. **A database error in the middle of a walk** — the walk stops, prints its summary and exits 1 with the database message; a stale item (cited fact superseded meanwhile) is counted as failed and the walk goes on with exit 0 (tests in Task 2).
3. **Backup over an existing file, or `pg_dump` failing half-way** — the existing file is untouched and exit 1; a failed dump leaves no file (tests in Task 5).
4. **A live consolidation lock older than an hour** — notes stay untouched; only a lock whose holder process is gone is stale (tests in Task 7).
5. **A vault table locked by another session while a hook runs** — the hook answers within a few seconds with the "unavailable" warning instead of hanging the session (tests in Task 8).

---

## File structure

```
src/val3/views.py       + safe(), review_card(), candidate_lines(); show_entry and the list escape; list says "review #n"
src/val3/tty.py         new: walk() for `val3 review --tty`
src/val3/writer.py      + _serialize() (advisory lock); previously-rejected outcome; best-effort invalid/discard log;
                        add_tool returns Outcome; recipe notices carry ids; one deadline in store_vectors
src/val3/backup.py      new: backup()
src/val3/cli.py         review --tty; review show uses review_card; backup command; extract takes the drain lock;
                        tool add uses Outcome; eval passes the declared vaults
src/val3/transcript.py  + HARNESS_TEXT, queued messages and `!` output, RecursionError
src/val3/hooks.py       HARNESS_TEXT from transcript; statement timeout; drainer spawn failure; RecursionError
src/val3/db.py          connect(statement_timeout_ms=); create_vault checks an existing val3_common
src/val3/inbox.py       unknown-origin cap; stale consolidation lock
src/val3/extract.py     watermark reset; search with chunk text; previously-rejected count
src/val3/pending.py     + drain_lock()
src/val3/evaluate.py    load_cases(path, vaults); file:line on every record error; RecursionError
tests/helpers.py        + add_service()
tests/fake_embedder.py  + tags_delay
tests/test_review.py, test_supersede.py, test_tty.py (new), test_writer_lock.py (new), test_backup.py (new),
test_save.py, test_hooks.py, test_eval.py, test_inbox.py, test_extract.py, test_vault.py, test_embed.py, test_tool.py
docs/design/2026-10-08-val3-design.md, docs/design/2026-10-09-semantic-search-design.md, README.md, TODO.md
```

---

### Task 1: Review cards and terminal-safe output

**Files:**
- Modify: `src/val3/views.py` (add `safe`, `review_card`, `candidate_lines`; `show_entry` and `pending_reviews` escape; remove `review_detail`)
- Modify: `src/val3/writer.py:243-252` (`_supersede`: recipe notices carry ids)
- Modify: `src/val3/cli.py` (`review show` prints `review_card`)
- Test: `tests/test_review.py`, `tests/test_supersede.py`

**Interfaces:**
- Consumes: `writer.NOTICES`, `views.show_entry(conn, vault, entry_id) -> str`, `config.parse_ref`.
- Produces: `views.safe(text: str) -> str`; `views.review_card(conn, vault: str, review_id: int) -> str` (raises `Val3Error("no review item #<n>")`); `views.pending_reviews(conn, vault) -> str` with lines starting `review #<n>`. Task 2 calls `review_card`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_review.py`, replace the line `assert '"kind": "recipe"' in env.run("review", "show", "1").stdout` with:

```python
    card = env.run("review", "show", "1").stdout
    assert card.startswith("review #1 · recipe · project · ")
    assert "[recipe]  Count the invoices of a month" in card
    assert f"  1. Run the invoice count query for the month  (facts: {env.vault}#1)" in card
```

and append:

```python
def test_list_names_review_items_apart_from_entries(env):
    transcript = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), transcript)
    assert env.run("review").stdout.startswith("review #1  recipe")


def test_card_shows_reproductions_as_not_run(env):
    transcript = admitted_fact(env)
    assert save(env, {**TOOL, "evidence": fact()["evidence"]}, transcript).returncode == 0
    card = env.run("review", "show", "1").stdout
    assert "[tool]  code-graph" in card and "tool: cli, priority 5" in card
    assert "      > invoice_count" in card
    assert ("      reproduce: SELECT count(*) AS invoice_count FROM invoice WHERE date >= '2026-04-01'  (not run)"
            in card)


def test_card_of_a_superseding_candidate_shows_what_it_replaces(env):
    transcript = admitted_fact(env)
    candidate = fact(title="North warehouse closed", statement="The north warehouse closed in March 2025.",
                     evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}],
                     supersedes=f"{env.vault}#1")
    assert save(env, candidate, transcript).stdout.strip() == "queued for review: review #1 (user-confirmation)"
    card = env.run("review", "show", "1").stdout
    assert f"approving supersedes {env.vault}#1" in card
    assert f"BEFORE {env.vault}#1" in card and "April has 42 invoices" in card


def test_control_characters_from_the_vault_are_escaped(env):
    transcript = admitted_fact(env)
    candidate = fact(title="Clear \x1b[2J and retitle \x1b]0;owned\x07 here",
                     statement="The north warehouse closed in March 2025.",
                     evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}])
    assert save(env, candidate, transcript).returncode == 0
    for args in (("review",), ("review", "show", "1")):
        out = env.run(*args).stdout
        assert "\x1b" not in out and "\x07" not in out, args
        assert "Clear \\x1b[2J and retitle \\x1b]0;owned\\x07 here" in out, args
```

In `tests/test_supersede.py`, append:

```python
def test_card_of_a_supersession_shows_both_entries(env):
    make_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, newer(env), t)
    card = env.run("review", "show", "1").stdout
    assert card.startswith(f"review #1 · supersession (notice) · project · ")
    assert f"session {env.session[:8]}" in card.splitlines()[0]
    assert card.index(f"BEFORE {env.vault}#1") < card.index("April has 42 invoices") < card.index(
        f"NOW {env.vault}#2") < card.index("April has 43 invoices")


def test_card_of_a_recipe_notice_shows_the_recipe_and_both_facts(env):
    make_vault(env)
    t = transcript(env)
    save(env, fact(), t)
    save(env, recipe(f"{env.vault}#1"), t)
    assert env.run("review", "approve", "1").returncode == 0
    save(env, newer(env), t)
    card = env.run("review", "show", "3").stdout
    assert card.startswith("review #3 · recipe-cites-superseded (notice)")
    assert (card.index(f"RECIPE {env.vault}#2") < card.index(f"BEFORE {env.vault}#1")
            < card.index(f"NOW {env.vault}#3"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_review.py tests/test_supersede.py`
Expected: the new tests and the edited one FAIL (the card is still JSON; the list starts with `#1`).

- [ ] **Step 3: Implement**

In `src/val3/writer.py`, `_supersede`, give the recipe notice its ids:

```python
    for (recipe_id,) in recipes:
        _queue(conn, vault, "recipe-cites-superseded",
               {"title": f"recipe {vault}#{recipe_id} cites {vault}#{old_id}, superseded by {vault}#{new_id}",
                "recipe": recipe_id, "old": old_id, "new": new_id},
               recipe_id, org)
```

In `src/val3/views.py`: add `import re`, `from val3.config import parse_ref` and `from val3.writer import NOTICES` to the imports, then:

```python
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe(text: str) -> str:
    """Text for a terminal: control characters other than newline and tab shown as escapes.

    Vault text comes from transcripts; an ANSI or OSC sequence must not redraw what a person approves."""
    return _CONTROL.sub(lambda match: f"\\x{ord(match[0]):02x}", text)
```

At the end of `show_entry`, replace `return "\n".join(lines)` with `return safe("\n".join(lines))`.

Replace `pending_reviews` and `review_detail` with:

```python
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
    header = (f"review #{review_id} · {reason}{' (notice)' if reason in NOTICES else ''} · {project} · "
              f"{created:%Y-%m-%d} · session {(session or '-')[:8]}")
    lines = [header + ("" if status == "pending" else f" · {status}"), ""]
    if reason == "supersession":
        older = conn.execute(vsql(vault, "SELECT old_id FROM {v}.supersession WHERE new_id = %s"),
                             (entry_id,)).fetchone()
        if older:
            lines += [f"BEFORE {vault}#{older[0]}", show_entry(conn, vault, older[0]), ""]
        lines += [f"NOW {vault}#{entry_id}", show_entry(conn, vault, entry_id)]
    elif reason == "recipe-cites-superseded":
        ids = [payload.get(key) for key in ("recipe", "old", "new")]
        if all(isinstance(value, int) for value in ids):
            for label, value in zip(("RECIPE", "BEFORE", "NOW"), ids):
                lines += [f"{label} {vault}#{value}", show_entry(conn, vault, value), ""]
        else:  # notices queued before the ids were stored
            lines.append(str(payload.get("title", "")))
    else:
        lines += candidate_lines(conn, vault, payload)
    return safe("\n".join(lines).rstrip())


def candidate_lines(conn, vault: str, candidate: dict) -> list[str]:
    """A queued candidate (validated JSON) in the layout of `val3 show`; reproductions are never run."""
    lines = [f"[{candidate.get('kind')}]  {candidate.get('title')}", "", str(candidate.get("statement", "")), ""]
    supersedes = candidate.get("supersedes")
    if supersedes:
        lines.append(f"approving supersedes {supersedes}")
        ref_vault, old_id = parse_ref(supersedes)
        if ref_vault == vault:
            try:
                lines += [f"BEFORE {supersedes}", show_entry(conn, vault, old_id)]
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
```

In `src/val3/cli.py`: import `review_card` instead of `review_detail` from `val3.views`, and in `cmd_review` replace `print(review_detail(conn, project.home, args.id))` with `print(review_card(conn, project.home, args.id))`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_review.py tests/test_supersede.py tests/test_cli.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/val3/views.py src/val3/writer.py src/val3/cli.py tests/test_review.py tests/test_supersede.py
git commit -m "feat: review cards with both entries of a supersession and escaped vault text"
```

---

### Task 2: `val3 review --tty`

**Files:**
- Create: `src/val3/tty.py`
- Modify: `src/val3/cli.py` (`review --tty`)
- Test: `tests/test_tty.py` (new)

**Interfaces:**
- Consumes: `views.review_card(conn, vault, review_id) -> str` (Task 1); `writer.decide(conn, project, review_id, approve, decided_by) -> str`; `writer.NOTICES`; `cli._store_vectors(conn, project)`; `cli._person_only()`.
- Produces: `tty.walk(conn, project, decided_by: str, after_approval: Callable[[], None]) -> None`. Raises `psycopg.Error` after printing the summary.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tty.py`:

```python
from helpers import OUTPUT, TOOL, fact, make_vault, recipe, save

CONFIRMED = fact(title="North warehouse closed", statement="The north warehouse closed in March 2025.",
                 evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}])
NEWER = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.")


def admitted_fact(env):
    make_vault(env)
    transcript = env.transcript(tool_outputs=[OUTPUT], user_messages=["Yes: the north warehouse closed in March 2025."])
    assert save(env, fact(), transcript).stdout.strip() == f"admitted {env.vault}#1"
    return transcript


def tty(env, answers, **environ):
    return env.run("review", "--tty", input=answers, environ=environ)


def decisions(env):
    return env.sql(f"SELECT review_id, decision FROM {env.vault}.review_decision ORDER BY review_id")


def test_walk_approves_rejects_skips_and_quits_at_end_of_input(env):
    t = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), t)
    save(env, TOOL, t)
    save(env, CONFIRMED, t)
    result = tty(env, "a\nr\ns\n")
    assert result.returncode == 0, result.stderr
    assert "[1/3] review #1 · recipe" in result.stdout and "[3/3] review #3 · user-confirmation" in result.stdout
    assert f"approved review #1: admitted {env.vault}#2" in result.stdout
    assert "rejected review #2" in result.stdout
    assert result.stdout.rstrip().endswith("1 approved, 1 rejected, 1 skipped, 0 failed")
    assert decisions(env) == [(1, "approved"), (2, "rejected")]
    assert env.sql(f"SELECT action, review_id FROM {env.vault}.event WHERE action IN ('approved', 'rejected') "
                   "ORDER BY id") == [("approved", 1), ("rejected", 2)]


def test_quit_stops_the_walk(env):
    t = admitted_fact(env)
    save(env, TOOL, t)
    save(env, CONFIRMED, t)
    result = tty(env, "q\n")
    assert result.returncode == 0
    assert "[2/2]" not in result.stdout
    assert result.stdout.rstrip().endswith("0 approved, 0 rejected, 0 skipped, 0 failed")
    assert decisions(env) == []


def test_a_notice_offers_no_reject(env):
    t = admitted_fact(env)
    save(env, {**NEWER, "supersedes": f"{env.vault}#1"}, t)
    result = tty(env, "r\na\n")
    assert result.returncode == 0, result.stderr
    assert "a approve · s skip · q quit> " in result.stdout
    assert "answer one of: a, s, q" in result.stdout
    assert "acknowledged review #1" in result.stdout
    assert decisions(env) == [(1, "approved")]


def test_a_stale_item_counts_as_failed_and_the_walk_goes_on(env):
    t = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), t)                       # review #1 cites #1
    save(env, {**NEWER, "supersedes": f"{env.vault}#1"}, t)     # #1 superseded: notice review #2
    result = tty(env, "a\na\n")
    assert result.returncode == 0, result.stderr
    assert f"not decided: {env.vault}#1 was superseded by {env.vault}#2" in result.stdout
    assert result.stdout.rstrip().endswith("1 approved, 0 rejected, 0 skipped, 1 failed")
    assert decisions(env) == [(2, "approved")]


def test_notices_created_during_the_walk_are_reported(env):
    t = admitted_fact(env)
    save(env, {**CONFIRMED, "supersedes": f"{env.vault}#1"}, t)
    result = tty(env, "a\n")
    assert result.returncode == 0, result.stderr
    assert "1 new items pending: run `val3 review --tty` again" in result.stdout


def test_a_database_error_stops_the_walk_with_exit_1(env):
    t = admitted_fact(env)
    save(env, CONFIRMED, t)
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    assert env.run("vault", "grant", env.vault, env.writer, "--write").returncode == 0
    env.sql(f"REVOKE INSERT ON {env.vault}.review_decision FROM {env.writer}")
    env.config(service="val3writer")
    result = tty(env, "a\n")
    assert result.returncode == 1
    assert "0 approved, 0 rejected, 0 skipped, 0 failed" in result.stdout
    assert "val3: error: database: permission denied" in result.stderr


def test_empty_review_list(env):
    make_vault(env)
    result = tty(env, "")
    assert result.returncode == 0
    assert result.stdout.strip() == "no pending review items"


def test_agent_sessions_cannot_walk_the_review_list(env):
    make_vault(env)
    result = tty(env, "", CLAUDECODE="1")
    assert result.returncode == 1
    assert "outside the agent session" in result.stderr


def test_tty_takes_no_action(env):
    make_vault(env)
    result = env.run("review", "--tty", "show", "1")
    assert result.returncode == 2
    assert "--tty takes no action" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_tty.py`
Expected: FAIL (`unrecognized arguments: --tty`, exit 2).

- [ ] **Step 3: Implement**

Create `src/val3/tty.py`:

```python
"""`val3 review --tty`: a person walks the pending review items in the terminal, one decision at a time."""
import psycopg

from val3 import Val3Error
from val3.db import vsql
from val3.views import review_card
from val3.writer import NOTICES, decide


def pending_ids(conn, vault: str) -> list[int]:
    return [review_id for (review_id,) in conn.execute(vsql(vault, """
        SELECT r.id FROM {v}.review_item r
        WHERE NOT EXISTS (SELECT 1 FROM {v}.review_decision d WHERE d.review_id = r.id)
        ORDER BY r.id"""))]


def walk(conn, project, decided_by: str, after_approval) -> None:
    """Decide the items pending at start, in id order. A refused decision is counted and skipped; a database
    error prints the summary and propagates (exit 1)."""
    vault = project.home
    ids = pending_ids(conn, vault)
    if not ids:
        print("no pending review items")
        return
    counts = dict.fromkeys(("approved", "rejected", "skipped", "failed"), 0)
    try:
        _decide_all(conn, project, ids, decided_by, after_approval, counts)
    except psycopg.Error:
        print(_summary(counts))
        raise
    print(_summary(counts))
    new = len(set(pending_ids(conn, vault)) - set(ids))
    if new:
        print(f"{new} new items pending: run `val3 review --tty` again")


def _decide_all(conn, project, ids, decided_by, after_approval, counts) -> None:
    vault = project.home
    for number, review_id in enumerate(ids, start=1):
        reason = conn.execute(vsql(vault, "SELECT reason FROM {v}.review_item WHERE id = %s"),
                              (review_id,)).fetchone()[0]
        keys = "asq" if reason in NOTICES else "arsq"
        prompt = " · ".join(label for key, label in (("a", "a approve"), ("r", "r reject"), ("s", "s skip"),
                                                     ("q", "q quit")) if key in keys) + "> "
        print(f"\n[{number}/{len(ids)}] {review_card(conn, vault, review_id)}\n")
        answer = _ask(prompt, keys)
        if answer == "q":
            return
        if answer == "s":
            counts["skipped"] += 1
            continue
        try:
            print(decide(conn, project, review_id, answer == "a", decided_by))
        except Val3Error as exc:  # already decided, a cited entry superseded meanwhile
            print(f"not decided: {exc}")
            counts["failed"] += 1
            continue
        counts["approved" if answer == "a" else "rejected"] += 1
        if answer == "a":
            after_approval()


def _ask(prompt: str, keys: str) -> str:
    while True:
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            print()
            return "q"
        if len(answer) == 1 and answer in keys:
            return answer
        print(f"answer one of: {', '.join(keys)}")


def _summary(counts: dict) -> str:
    return (f"{counts['approved']} approved, {counts['rejected']} rejected, {counts['skipped']} skipped, "
            f"{counts['failed']} failed")
```

In `src/val3/cli.py`: add `tty` to `from val3 import Val3Error, __version__, hooks, inbox, pending` (→ `..., pending, tty`); in `build_parser`, after `review = sub.add_parser(...)`:

```python
    review.add_argument("--tty", action="store_true", help="decide the pending items one by one in this terminal")
```

and replace `cmd_review` with:

```python
def cmd_review(args) -> int | None:
    if args.tty and args.review_command:
        print("val3: error: --tty takes no action", file=sys.stderr)
        return 2
    if args.tty or args.review_command in ("approve", "reject"):
        _person_only()
    project = load_project(Path.cwd())
    with connect(project.service) as conn:
        if args.tty:
            tty.walk(conn, project, getpass.getuser(), lambda: _store_vectors(conn, project))
        elif args.review_command is None:
            print(pending_reviews(conn, project.home))
        elif args.review_command == "show":
            print(review_card(conn, project.home, args.id))
        else:
            approve = args.review_command == "approve"
            print(decide(conn, project, args.id, approve, args.by or getpass.getuser()))
            if approve:
                _store_vectors(conn, project)
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_tty.py tests/test_review.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/val3/tty.py src/val3/cli.py tests/test_tty.py
git commit -m "feat: val3 review --tty walks the review list in the terminal"
```

---

### Task 3: One writer transaction per vault at a time (m20)

**Files:**
- Modify: `src/val3/writer.py` (`_serialize`; called first in the transactions of `submit`, `decide`, `add_tool`)
- Test: `tests/test_writer_lock.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces: the lock key `hashtextextended('val3 writer ' || <vault>, 0)`, taken with `pg_advisory_xact_lock` as the first statement of every writer transaction that admits, supersedes or decides. Tests take the same key.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_writer_lock.py`:

```python
import json
import subprocess
import sys
import time

import psycopg

from helpers import OUTPUT, fact, make_vault, recipe, save

NEW_QUOTE = "invoice_count\n-------------\n          43"
NEWER = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices dated April 2026.",
             evidence=[{"kind": "query", "reproduction": "SELECT count(*) AS invoice_count FROM invoice",
                        "quote": NEW_QUOTE}])
LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"


def setup(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT, NEW_QUOTE + "\n(1 row)"])
    assert save(env, fact(), t).returncode == 0
    return t


def background(env, *args, stdin=""):
    proc = subprocess.Popen([sys.executable, "-m", "val3", *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=env.project, env=env.environ)
    proc.stdin.write(stdin)
    proc.stdin.close()
    return proc


def holding_the_lock(env):
    conn = psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname=val3")
    conn.execute(LOCK, (f"val3 writer {env.vault}",))
    return conn


def test_save_and_approve_wait_for_the_writer_lock(env):
    t = setup(env)
    save(env, recipe(f"{env.vault}#1"), t)
    conn = holding_the_lock(env)
    try:
        saving = background(env, "save", "--transcript", str(t), "--session", env.session,
                            stdin=json.dumps({**NEWER, "title": "Unrelated fact"}))
        approving = background(env, "review", "approve", "1")
        time.sleep(1.5)
        assert saving.poll() is None and approving.poll() is None
    finally:
        conn.rollback()
        conn.close()
    assert saving.wait(timeout=30) == 0, saving.stderr.read()
    assert approving.wait(timeout=30) == 0, approving.stderr.read()


def test_approval_racing_a_supersession_is_refused_or_flagged(env):
    t = setup(env)
    save(env, recipe(f"{env.vault}#1"), t)
    conn = holding_the_lock(env)
    try:
        approving = background(env, "review", "approve", "1")
        superseding = background(env, "save", "--transcript", str(t), "--session", env.session,
                                 stdin=json.dumps({**NEWER, "supersedes": f"{env.vault}#1"}))
        time.sleep(1.0)
    finally:
        conn.rollback()
        conn.close()
    assert superseding.wait(timeout=30) == 0, superseding.stderr.read()
    approving.wait(timeout=30)
    if approving.returncode == 0:   # approved first: the supersession must flag the new recipe
        assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item WHERE reason = 'recipe-cites-superseded'") \
            == [(1,)]
    else:                           # superseded first: the approval must be refused
        assert "was superseded by" in approving.stderr.read()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_writer_lock.py`
Expected: `test_save_and_approve_wait_for_the_writer_lock` FAILS (`saving.poll()` is 0: nothing waits). The race test may pass by chance; it guards the outcome, the first test guards the lock.

- [ ] **Step 3: Implement**

In `src/val3/writer.py` add:

```python
def _serialize(conn, vault: str) -> None:
    """Writer transactions of one vault run one at a time, so a check made inside one (a cited fact is live)
    stays true until it commits. Runs first in the transaction; the lock ends with it."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"val3 writer {vault}",))
```

and call it as the first statement inside each `with conn.transaction():` block:
- `submit`: before `existing = _entry_by_hash(...)`;
- `decide`: before `row = conn.execute(...)`;
- `add_tool`: before `entry_id, created = _admit(...)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_writer_lock.py tests/test_review.py tests/test_supersede.py tests/test_save.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/val3/writer.py tests/test_writer_lock.py
git commit -m "fix: writer transactions of a vault take one advisory lock"
```

---

### Task 4: Writer outcomes — previously rejected, best-effort discard log, add_tool (m2, m4, m14)

**Files:**
- Modify: `src/val3/writer.py` (`Outcome`, `submit`, `_check_evidence`, `add_tool`, `_log_best_effort`)
- Modify: `src/val3/extract.py` (`STATUSES`, `summary`)
- Modify: `src/val3/cli.py` (`cmd_tool_add`)
- Test: `tests/test_review.py`, `tests/test_extract.py`, `tests/test_save.py`

**Interfaces:**
- Consumes: `_serialize` (Task 3) stays first in `submit`.
- Produces: `Outcome.status` gains `"previously-rejected"` (printed `previously rejected: review #<n>`); `add_tool(...) -> Outcome`; extraction counts gain key `"previously-rejected"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review.py`:

```python
def test_a_rejected_candidate_is_not_queued_again(env):
    transcript = admitted_fact(env)
    save(env, recipe(f"{env.vault}#1"), transcript)
    assert env.run("review", "reject", "1").returncode == 0
    result = save(env, recipe(f"{env.vault}#1"), transcript)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "previously rejected: review #1"
    assert env.sql(f"SELECT count(*) FROM {env.vault}.review_item") == [(1,)]
    assert env.run("review").stdout.strip() == "no pending review items"
```

Append to `tests/test_extract.py` (add `recipe` to the `helpers` import):

```python
def test_extraction_counts_previously_rejected_candidates(env):
    make_vault(env)
    t = env.transcript(user_messages=["How many invoices in April?"], tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    assert save(env, recipe(f"{env.vault}#1"), t).returncode == 0
    assert env.run("review", "reject", "1").returncode == 0
    env.respond([{"note": None, "candidate": recipe(f"{env.vault}#1")}])
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    assert "previously rejected 1" in result.stdout
```

Append to `tests/test_save.py` (use its existing imports; add `make_vault` and `fact` from `helpers` if missing):

```python
def test_a_read_only_grant_shows_the_real_rejection(env):
    make_vault(env)
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    assert env.run("vault", "grant", env.vault, env.reader, "--read").returncode == 0
    env.config(service="val3reader")
    t = env.transcript(tool_outputs=["nothing relevant"])
    invalid = env.run("save", "--transcript", str(t), input='{"kind": "fact"}')
    assert invalid.returncode == 1
    assert "permission denied" not in invalid.stderr and "title" in invalid.stderr
    discarded = env.run("save", "--transcript", str(t), input=json.dumps(fact()))
    assert discarded.returncode == 1
    assert "discarded: evidence quote not found" in discarded.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_review.py tests/test_extract.py tests/test_save.py`
Expected: the three new tests FAIL (`already queued: review #1`; no `previously rejected` count; `permission denied for table event`).

- [ ] **Step 3: Implement**

In `src/val3/writer.py`:

```python
@dataclass(frozen=True)
class Outcome:
    status: str  # admitted | duplicate | queued | already-queued | previously-rejected
    ref: str     # <vault>#<id>, or review #<n> for queued items
    reason: str | None = None

    def __str__(self) -> str:
        return {"admitted": f"admitted {self.ref}", "duplicate": f"duplicate of {self.ref}",
                "queued": f"queued for review: {self.ref} ({self.reason})",
                "already-queued": f"already queued: {self.ref}",
                "previously-rejected": f"previously rejected: {self.ref}"}[self.status]
```

In `submit`, replace the queued branch with:

```python
            if reason:
                review_id, created = _queue(conn, vault, reason, candidate, None, org)
                if not created and _decision(conn, vault, review_id) == "rejected":
                    return Outcome("previously-rejected", f"review #{review_id}", reason)
                return Outcome("queued" if created else "already-queued", f"review #{review_id}", reason)
```

and in `submit`'s `except Val3Error` for invalid candidates, and in `_check_evidence` for discards, replace `_log(...)` with `_log_best_effort(...)` (same arguments). Add:

```python
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
```

Use `_decision` in `decide` too (replace the inline `done = ...` query with `done = _decision(conn, vault, review_id)` and `if done: raise Val3Error(f"review #{review_id} was already {done}")`).

`add_tool` returns an `Outcome`:

```python
    with conn.transaction():
        _serialize(conn, project.home)
        entry_id, created = _admit(conn, project.home, candidate, origin(project, None))
    return Outcome("admitted" if created else "duplicate", f"{project.home}#{entry_id}")
```

In `src/val3/cli.py`, `cmd_tool_add`:

```python
        outcome = add_tool(conn, project, args.name, args.type, args.description, args.question,
                           args.environment, args.priority, args.index)
        print(outcome)
        if outcome.status == "admitted":
            _store_vectors(conn, project)
```

In `src/val3/extract.py`:

```python
STATUSES = ("admitted", "duplicate", "queued", "already-queued", "previously-rejected", "rejected")
```

and in `summary`, after `already queued {counts['already-queued']}, ` add `previously rejected {counts['previously-rejected']}, `.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_review.py tests/test_extract.py tests/test_save.py tests/test_tool.py tests/test_pending.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/val3/writer.py src/val3/extract.py src/val3/cli.py tests/test_review.py tests/test_extract.py tests/test_save.py
git commit -m "fix: rejected candidates are not queued again; discard log never hides the reason"
```

---

### Task 5: `val3 backup`

**Files:**
- Create: `src/val3/backup.py`
- Modify: `src/val3/cli.py` (`backup` command)
- Modify: `tests/helpers.py` (`add_service`)
- Test: `tests/test_backup.py` (new)

**Interfaces:**
- Consumes: `db.connect(service)`.
- Produces: `backup.backup(service: str, out: Path | None) -> str` (one line: path, size, vaults); `helpers.add_service(env, name, dbname)` (Task 8 uses it).

- [ ] **Step 1: Write the failing tests**

Append to `tests/helpers.py`:

```python
def add_service(env, name, dbname):
    """A pg_service.conf entry for another database of the test server."""
    with open(env.root / "pg_service.conf", "a") as handle:
        handle.write(f"[{name}]\nhost=127.0.0.1\nport={env.port}\nuser=postgres\ndbname={dbname}\nconnect_timeout=3\n\n")
```

Create `tests/test_backup.py`:

```python
import re
import shutil
import stat
import subprocess

import psycopg
import pytest

from helpers import OUTPUT, add_service, embedding_vault, fact, save


def backups(env):
    directory = env.home / ".local" / "share" / "val3" / "backup"
    return sorted(directory.glob("*")) if directory.exists() else []


def fake_pg_dump(env, version, code=0):
    bin_dir = env.root / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "pg_dump"
    script.write_text(f"#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'pg_dump (PostgreSQL) {version}'; exit 0; fi\n"
                      f"echo partial\necho 'fake failure' >&2\nexit {code}\n")
    script.chmod(0o755)
    return {"PATH": f"{bin_dir}:{env.environ['PATH']}"}


def real_pg_dump_or_skip():
    path = shutil.which("pg_dump")
    if path is None:
        pytest.skip("no pg_dump on PATH")
    major = int(re.search(r"(\d+)", subprocess.run([path, "--version"], capture_output=True, text=True).stdout)[1])
    if major < 16:
        pytest.skip(f"pg_dump {major} is older than the PostgreSQL 16 test server")


def test_an_older_pg_dump_is_refused(env):
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    result = env.run("backup", environ=fake_pg_dump(env, "14.17"))
    assert result.returncode == 1
    assert "pg_dump 14 cannot dump a PostgreSQL 16 server: install postgresql-client-16" in result.stderr
    assert backups(env) == []


def test_a_failing_pg_dump_leaves_no_file(env):
    env.config()
    result = env.run("backup", environ=fake_pg_dump(env, "16.15", code=1))
    assert result.returncode == 1
    assert "pg_dump failed: fake failure" in result.stderr
    assert backups(env) == []


def test_an_existing_output_file_is_refused(env):
    env.config()
    out = env.root / "kept.dump"
    out.write_text("keep")
    result = env.run("backup", "--out", str(out), environ=fake_pg_dump(env, "16.15"))
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert out.read_text() == "keep"


def test_default_backup_is_private(env):
    real_pg_dump_or_skip()
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    result = env.run("backup")
    assert result.returncode == 0, result.stderr
    [path] = backups(env)
    assert re.fullmatch(r"val3-\d{8}T\d{6}Z\.dump", path.name)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert str(path) in result.stdout and env.vault in result.stdout


def test_round_trip_restores_every_vault(env, embedder):
    real_pg_dump_or_skip()
    other = env.vault + "_b"
    embedding_vault(env, embedder.url)
    assert env.run("vault", "create", other).returncode == 0
    t = env.transcript(tool_outputs=[OUTPUT], user_messages=["the north warehouse closed in March 2025"])
    assert save(env, fact(), t).stdout.strip() == f"admitted {env.vault}#1"
    newer = fact(title="April has 43 invoices", statement="The ERP holds 43 invoices.", supersedes=f"{env.vault}#1")
    assert save(env, newer, t).stdout.strip() == f"admitted {env.vault}#2"
    confirmed = fact(title="North warehouse closed", statement="The north warehouse closed in March 2025.",
                     evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}])
    assert save(env, confirmed, t).returncode == 0
    assert env.run("review", "reject", "2").returncode == 0  # review #1 is the supersession notice
    env.config(home=other)
    assert save(env, fact(title="Other vault fact"), t).stdout.strip() == f"admitted {other}#1"
    env.sql(f"CREATE ROLE {env.reader} LOGIN")
    assert env.run("vault", "grant", env.vault, env.reader, "--read").returncode == 0

    env.config(linked=[other], embedding={"url": embedder.url})
    reads = (("show", f"{env.vault}#1"), ("show", f"{env.vault}#2"), ("show", f"{other}#1"),
             ("recall", "invoices"), ("review",))
    before = [env.run(*args).stdout for args in reads]
    out = env.root / "all.dump"
    result = env.run("backup", "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert env.vault in result.stdout and other in result.stdout

    restored = "r" + env.vault
    env.sql(f"CREATE DATABASE {restored} TEMPLATE template0")
    dsn = f"host=127.0.0.1 port={env.port} user=postgres dbname={restored}"
    done = subprocess.run(["pg_restore", "--single-transaction", "--exit-on-error", "-d", dsn, str(out)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    add_service(env, "val3restored", restored)
    env.config(linked=[other], service="val3restored", embedding={"url": embedder.url})
    assert [env.run(*args).stdout for args in reads] == before
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute(f"SELECT old_id, new_id FROM {env.vault}.supersession").fetchall() == [(1, 2)]
        assert conn.execute("SELECT has_table_privilege(%s, %s, 'SELECT')",
                            (env.reader, f"{env.vault}.entry")).fetchone() == (True,)
        assert conn.execute(f"SELECT count(*) FROM {env.vault}.embedding").fetchone()[0] >= 2
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(f"UPDATE {env.vault}.entry SET title = 'x'")
    third = fact(title="May has 50 invoices", statement="The ERP holds 50 invoices dated May 2026.")
    assert save(env, third, t).stdout.strip() == f"admitted {env.vault}#3"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_backup.py`
Expected: FAIL (`invalid choice: 'backup'`, exit 2).

- [ ] **Step 3: Implement**

Create `src/val3/backup.py`:

```python
"""`val3 backup`: a pg_dump of the whole val3 database (every vault, val3_common, extensions, privileges)."""
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from psycopg.conninfo import make_conninfo

from val3 import Val3Error
from val3.db import connect

_FILE_SAFE = re.compile(r"[A-Za-z0-9_-]+")


def default_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "val3" / "backup"


def backup(service: str, out: Path | None) -> str:
    """Dump the database of `service` into a new private file; return its path, size and vaults."""
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        raise Val3Error("pg_dump not found: install the PostgreSQL client of the server's major version")
    client = _major(subprocess.run([pg_dump, "--version"], capture_output=True, text=True).stdout)
    with connect(service) as conn:
        server = int(conn.execute("SHOW server_version_num").fetchone()[0]) // 10000
        database = conn.execute("SELECT current_database()").fetchone()[0]
        vaults = [name for (name,) in conn.execute("""
            SELECT nspname FROM pg_namespace
            WHERE to_regclass(format('%I.review_item', nspname)) IS NOT NULL ORDER BY 1""")]
    if client < server:
        raise Val3Error(f"pg_dump {client} cannot dump a PostgreSQL {server} server: "
                        f"install postgresql-client-{server}")
    if out is None:
        if not _FILE_SAFE.fullmatch(database):
            raise Val3Error(f"database name {database!r} cannot name a file: pass --out")
        directory = default_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        out = directory / f"{database}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.dump"
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise Val3Error(f"{out} already exists") from None
    except OSError as exc:
        raise Val3Error(f"cannot create {out}: {exc.strerror}") from None
    try:
        with os.fdopen(fd, "wb") as handle:
            done = subprocess.run([pg_dump, "-Fc", "--no-password", make_conninfo(service=service)],
                                  stdout=handle, stderr=subprocess.PIPE)
        if done.returncode != 0:
            raise Val3Error(f"pg_dump failed: {done.stderr.decode(errors='replace').strip()}")
    except BaseException:  # failure or interruption: no partial file is left behind
        out.unlink(missing_ok=True)
        raise
    return f"{out}  {out.stat().st_size} bytes  vaults: {', '.join(vaults) or 'none'}"


def _major(version: str) -> int:
    match = re.search(r"(\d+)", version)
    if match is None:
        raise Val3Error(f"cannot read the version of pg_dump: {version.strip()!r}")
    return int(match[1])
```

In `src/val3/cli.py`: add `from val3.backup import backup`, and in `build_parser` before `extract_parser`:

```python
    backup_parser = sub.add_parser("backup", help="dump the whole val3 database into a private file (pg_dump)")
    backup_parser.add_argument("--service", help="pg_service.conf entry (default: the project's)")
    backup_parser.add_argument("--out", type=Path,
                               help="output file (default: ~/.local/share/val3/backup/<database>-<time>.dump)")
    backup_parser.set_defaults(func=lambda args: print(backup(_service(args), args.out)))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_backup.py -rs`
Expected: PASS, no skips (`pg_dump` 16 on this machine: verified fact 1). On a machine with an older client the two real-dump tests are skipped with their reason.

- [ ] **Step 5: Commit**

```bash
git add src/val3/backup.py src/val3/cli.py tests/helpers.py tests/test_backup.py
git commit -m "feat: val3 backup dumps the whole database into a private file"
```

---

### Task 6: Transcript evidence — queued messages, `!` output, deep JSON (m3, m16)

**Files:**
- Modify: `src/val3/transcript.py` (`HARNESS_TEXT`, `_queued_text`, `_bash_output`, `_typed`; `evidence_text`, `condense`, `_render`, `_load`, `last_assistant_text`)
- Modify: `src/val3/hooks.py` (`HARNESS_PROMPT` → `transcript.HARNESS_TEXT`; `run` and `_logged_at` catch `RecursionError`)
- Modify: `src/val3/evaluate.py` (`load_cases`, `draft` catch `RecursionError`)
- Test: `tests/test_save.py`, `tests/test_extract.py`, `tests/test_hooks.py`, `tests/test_eval.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `transcript.HARNESS_TEXT` (compiled regex, `.match` on a prompt); queued human messages count as user text; `<bash-stdout>`/`<bash-stderr>` content counts as tool output; `Chunk.user_text` includes text-block user messages and queued messages.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_save.py` (imports: `json`, and `OUTPUT`, `QUOTE`, `fact`, `make_vault`, `save` from `helpers`):

```python
USER_FACT = fact(title="North warehouse closed", statement="The north warehouse closed in March 2025.",
                 evidence=[{"kind": "user", "quote": "the north warehouse closed in March 2025"}])


def queued(env, t, prompt, kind="human"):
    env.append(t, {"type": "attachment", "attachment": {"type": "queued_command", "prompt": prompt,
                                                         "origin": {"kind": kind}, "humanTurn": True}})


def test_a_message_queued_by_the_user_is_user_evidence(env):
    make_vault(env)
    t = env.transcript(tool_outputs=["unrelated"])
    queued(env, t, "by the way, the north warehouse closed in March 2025")
    result = save(env, USER_FACT, t)
    assert result.stdout.strip() == "queued for review: review #1 (user-confirmation)", result.stderr


def test_harness_turns_queued_as_messages_are_not_evidence(env):
    make_vault(env)
    t = env.transcript(tool_outputs=["unrelated"])
    queued(env, t, "<task-notification>\nthe north warehouse closed in March 2025\n</task-notification>")
    result = save(env, USER_FACT, t)
    assert result.returncode == 1 and "discarded" in result.stderr


def test_output_of_a_bang_command_is_tool_output_not_user_text(env):
    make_vault(env)
    t = env.transcript(user_messages=["<bash-input>psql -c 'select count(*)'</bash-input>"])
    env.append(t, {"type": "user", "message": {"role": "user", "content":
                   f"<bash-stdout>{QUOTE}</bash-stdout><bash-stderr></bash-stderr>"}})
    assert save(env, fact(), t).stdout.strip() == f"admitted {env.vault}#1"
    as_user = fact(title="Count said by the user", evidence=[{"kind": "user", "quote": QUOTE}])
    result = save(env, as_user, t)
    assert result.returncode == 1 and "discarded" in result.stderr


def test_a_deeply_nested_transcript_line_is_skipped(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    with t.open("a") as handle:
        handle.write("[" * 100000 + "\n")
    assert save(env, fact(), t).stdout.strip() == f"admitted {env.vault}#1"
```

Append to `tests/test_extract.py`:

```python
def test_user_text_blocks_look_up_existing_entries(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    fresh = env.transcript(session="text-blocks")
    env.append(fresh, {"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": "How many invoices in April?"}]}})
    assert extract(env, fresh).returncode == 0
    assert f"{env.vault}#1 [fact] April has 42 invoices" in env.calls()[0]["stdin"]
```

Append to `tests/test_hooks.py`:

```python
def test_a_deeply_nested_hook_event_is_ignored(env):
    make_vault(env)
    result = env.run("hook", "prompt", input="[" * 100000)
    assert result.returncode == 0 and result.stdout == ""


def test_a_deeply_nested_last_transcript_line_does_not_cost_the_injection(env, embedder):
    embedding_vault(env, embedder.url)
    t = env.transcript(tool_outputs=[OUTPUT])
    assert save(env, fact(), t).returncode == 0
    with t.open("a") as handle:
        handle.write("[" * 100000 + "\n")
    text = context(ask(env, "invoices april?"), "UserPromptSubmit")
    assert f"{env.vault}#1" in text and "unavailable" not in text
```

Append to `tests/test_eval.py`:

```python
def test_a_deeply_nested_line_is_reported_with_its_place(env, embedder):
    embedding_vault(env, embedder.url)
    path = env.root / "set.jsonl"
    path.write_text("[" * 100000 + "\n")
    result = env.run("eval", str(path))
    assert result.returncode == 1
    assert f"{path}:1: not valid JSON" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_save.py tests/test_extract.py tests/test_hooks.py tests/test_eval.py`
Expected: the new tests FAIL (queued message discarded; bang output counted as user text; `(none found)`; `RecursionError` tracebacks).

- [ ] **Step 3: Implement**

In `src/val3/transcript.py`, add after `_CLIP`:

```python
# Turns the harness writes into the prompt or the message queue (peer messages, task notifications).
HARNESS_TEXT = re.compile(r"\s*<(task-notification|agent-message|cross-session-message|teammate-message)[\s>]")
_BASH_OUTPUT = re.compile(r"<bash-(stdout|stderr)>(.*?)</bash-\1>", re.S)
```

Replace the body of `evidence_text` after `tool, user = [], []` setup with:

```python
    records = _load(path)
    excluded = _model_text_calls(records, private)
    tool: list[str] = []
    user: list[str] = []
    for record in records:
        user.extend(_typed(record))
        if not _is_user(record):
            continue
        content = _content(record)
        if isinstance(content, str):
            tool.extend(_bash_output(content) or [])
            continue
        for block in _blocks(content):
            if block.get("type") == "tool_result" and block.get("tool_use_id") not in excluded:
                tool.extend(_texts(block.get("content")))
    return _normal("\n".join(tool)), _normal("\n".join(user))
```

In `condense`, replace

```python
        if _is_user(records[index]) and isinstance(_content(records[index]), str):
            users.append(_content(records[index]))
```

with `users.extend(_typed(records[index]))`.

At the start of `_render`, add:

```python
    queued = _queued_text(record)
    if queued is not None:
        return [f"USER: {_clip(queued, 'text')}"]
```

and inside the `if _is_user(record):` branch replace `if isinstance(content, str): return [f"USER: ..."]` with:

```python
        if isinstance(content, str):
            output = _bash_output(content)
            if output is not None:
                return [f"TOOL RESULT (! command typed by the user):\n{_clip(_normal(chr(10).join(output)), 'tool_result')}"]
            return [f"USER: {_clip(content, 'text')}"]
```

Add:

```python
def _queued_text(record: dict | None) -> str | None:
    """A message the user typed while the agent was busy: the harness stores it as a queued_command
    attachment. Harness turns arrive the same way and are not the user's."""
    if not isinstance(record, dict) or record.get("type") != "attachment":
        return None
    attachment = record.get("attachment")
    if not isinstance(attachment, dict) or attachment.get("type") != "queued_command":
        return None
    prompt, origin = attachment.get("prompt"), attachment.get("origin")
    if (not isinstance(prompt, str) or (isinstance(origin, dict) and origin.get("kind") not in (None, "human"))
            or HARNESS_TEXT.match(prompt)):
        return None
    return prompt


def _bash_output(text: str) -> list[str] | None:
    """Output of a `!` command typed by the user, which the harness wraps in bash-stdout/bash-stderr tags."""
    if not text.lstrip().startswith("<bash-stdout>"):
        return None
    return [body for _, body in _BASH_OUTPUT.findall(text) if body]


def _typed(record: dict | None) -> list[str]:
    """What the user typed in a record: a prompt, text blocks or a queued message; never tool or `!` output."""
    queued = _queued_text(record)
    if queued is not None:
        return [queued]
    if not _is_user(record):
        return []
    content = _content(record)
    if isinstance(content, str):
        return [] if _bash_output(content) is not None else [content]
    return [block["text"] for block in _blocks(content)
            if block.get("type") == "text" and isinstance(block.get("text"), str)]
```

`RecursionError` (verified fact 3): in `last_assistant_text` change `except ValueError:` to `except (ValueError, RecursionError):`; in `_load` change `except json.JSONDecodeError:` to `except (ValueError, RecursionError):`.

In `src/val3/hooks.py`: remove the `HARNESS_PROMPT` definition, import `HARNESS_TEXT` with `last_assistant_text` (`from val3.transcript import HARNESS_TEXT, last_assistant_text`) and use `HARNESS_TEXT.match(text)` in `prompt`; in `run` change `except ValueError:` to `except (ValueError, RecursionError):`; in `_logged_at` change `except (ValueError, KeyError, TypeError):` to `except (ValueError, KeyError, TypeError, RecursionError):`.

In `src/val3/evaluate.py`: in `load_cases` and in `draft` change `except ValueError:` to `except (ValueError, RecursionError):`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_save.py tests/test_extract.py tests/test_hooks.py tests/test_eval.py tests/test_inbox.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/val3/transcript.py src/val3/hooks.py src/val3/evaluate.py tests/test_save.py tests/test_extract.py tests/test_hooks.py tests/test_eval.py
git commit -m "fix: queued messages and ! output count as evidence; deep JSON lines are skipped"
```

---

### Task 7: Extraction — note cap, stale lock, shrunk transcript, drain lock, tool-only chunks (m8, m9, m10, m12, m13)

**Files:**
- Modify: `src/val3/inbox.py` (`changed_notes` cap; `_consolidating`; `settle`)
- Modify: `src/val3/extract.py` (watermark reset; search text)
- Modify: `src/val3/pending.py` (`drain_lock`)
- Modify: `src/val3/cli.py` (`cmd_extract`)
- Test: `tests/test_inbox.py`, `tests/test_extract.py`

**Interfaces:**
- Consumes: `extract.STATUSES` and `summary` as changed in Task 4.
- Produces: `inbox.UNKNOWN_NOTES = 20`, `inbox.UNKNOWN_CHARS = 40_000`, `inbox.NOTE_CLIP = 4_000` (used by `extract._material` instead of the literal `4_000`); `pending.drain_lock()` (context manager, waits).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_inbox.py` (add `import os` and `import time`):

```python
def unknown_notes(directory, count, size=3000):
    names = []
    for i in range(count):
        path = directory / f"n{i:02}.md"
        head = f"---\nname: n{i:02}\n---\n\n"
        path.write_text(head + "x" * (size - len(head)))
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
        names.append(path.name)
    return names[::-1]  # newest first


def test_unknown_origin_notes_are_capped_newest_first(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    names = unknown_notes(memdir(env), 25)
    first = min(20, 40_000 // 3000)
    extract(env, t)
    sent = [name for name in names if f"### {name}" in env.calls()[0]["stdin"]]
    assert sent == names[:first]
    env.append(t, {"type": "user", "message": {"role": "user", "content": "And for May?"}})
    extract(env, t)
    sent = [name for name in names if f"### {name}" in env.calls()[1]["stdin"]]
    assert sent == names[first:]


def test_a_lock_whose_holder_is_gone_is_stale(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    gone = subprocess.Popen(["true"])
    gone.wait()
    (directory / ".consolidate-lock").write_text(json.dumps({"holderPid": gone.pid}))
    env.respond([{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    assert f"Recorded in VAL3 as {env.vault}#1" in (directory / "april.md").read_text()


def test_a_live_holder_blocks_whatever_the_lock_age(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    lock = directory / ".consolidate-lock"
    lock.write_text(json.dumps({"holderPid": os.getpid()}))
    os.utime(lock, (time.time() - 7200, time.time() - 7200))
    env.respond([{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    assert (directory / "april.md").read_text() == NOTE.format(session=env.session)


def test_an_unreadable_lock_blocks(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    (directory / ".consolidate-lock").write_text("not json")
    env.respond([{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    assert (directory / "april.md").read_text() == NOTE.format(session=env.session)
```

Append to `tests/test_extract.py` (add `import fcntl`, `import subprocess`, `import sys`, `import time`):

```python
def test_a_shrunk_transcript_is_read_again(env):
    make_vault(env)
    t = env.transcript(user_messages=["one", "two", "three"])
    assert extract(env, t).returncode == 0
    t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "And for May?"}}) + "\n")
    assert extract(env, t).returncode == 0
    assert "And for May?" in env.calls()[1]["stdin"]
    env.append(t, {"type": "user", "message": {"role": "user", "content": "And for June?"}})
    assert extract(env, t).returncode == 0
    third = env.calls()[2]["stdin"]
    assert "And for June?" in third and "And for May?" not in third


def test_extract_waits_for_the_drain_lock(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    queue = env.state / "queue"
    queue.mkdir(parents=True)
    with open(queue / ".lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        proc = subprocess.Popen([sys.executable, "-m", "val3", "extract", str(t)], cwd=env.project,
                                env=env.environ, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(1.5)
        assert proc.poll() is None
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err


def test_a_chunk_without_user_text_looks_up_existing_entries(env):
    make_vault(env)
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    tools_only = env.transcript(tool_outputs=["invoices of April: 42 rows"], session="tools-only")
    assert extract(env, tools_only).returncode == 0
    assert f"{env.vault}#1 [fact] April has 42 invoices" in env.calls()[0]["stdin"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_inbox.py tests/test_extract.py`
Expected: the new tests FAIL (all 25 notes sent; dead-holder note not rewritten; second extraction of the shrunk transcript makes no call; `extract` does not wait; `(none found)`).

- [ ] **Step 3: Implement**

In `src/val3/inbox.py`:

```python
UNKNOWN_NOTES = 20      # unknown-origin notes sent in one extraction, newest first
UNKNOWN_CHARS = 40_000  # their total size, each counted as sent (clipped at NOTE_CLIP)
NOTE_CLIP = 4_000
```

Replace `changed_notes` with:

```python
def changed_notes(memdir: Path, session: str) -> list[tuple[str, str]]:
    """Notes changed since VAL3 last read them, written by this session or by an unknown one.

    Unknown-origin notes are capped, newest first; the rest stay changed for the next extraction."""
    known = _known(memdir)
    own, unknown = [], []
    for path in sorted(memdir.glob("*.md")) if memdir.is_dir() else []:
        if path.name == INDEX:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            mtime = path.stat().st_mtime
        except OSError:
            continue  # unreadable now; read again at the next extraction
        if known.get(path.name) == _digest(text):
            continue
        origin = _ORIGIN.search(_frontmatter(text))
        if origin and origin[1] != session:
            continue
        if origin:
            own.append((path.name, text))
        else:
            unknown.append((mtime, path.name, text))
    chosen, size = [], 0
    for _, name, text in sorted(unknown, key=lambda note: (note[0], note[1]), reverse=True):
        if len(chosen) == UNKNOWN_NOTES or size + min(len(text), NOTE_CLIP) > UNKNOWN_CHARS:
            break
        chosen.append((name, text))
        size += min(len(text), NOTE_CLIP)
    return sorted(own + chosen)
```

Replace the first line of `settle` (`if notes and (memdir / LOCK).exists():`) with `if notes and _consolidating(memdir):` and add:

```python
def _consolidating(memdir: Path) -> bool:
    """The harness holds the lock while it rewrites the directory. Only a lock naming a holder process that
    no longer exists on this machine is stale; a live holder, an unreadable lock or a refused check block."""
    try:
        data = json.loads((memdir / LOCK).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError, RecursionError):
        return True
    holder = data.get("holderPid") if isinstance(data, dict) else None
    if not isinstance(holder, int) or isinstance(holder, bool) or holder <= 0:
        return True
    try:
        os.kill(holder, 0)
    except ProcessLookupError:
        return False
    except OSError:  # PermissionError: the process exists under another user
        return True
    return True
```

In `src/val3/extract.py`: import `NOTE_CLIP` (`from val3.inbox import NOTE_CLIP` next to `from val3 import Val3Error, inbox`) and use it in `_material` (`text[:NOTE_CLIP]`). In `extract`, replace

```python
    chunks, total = condense(transcript, _watermark(session), private=private)
```

with

```python
    start = _watermark(session)
    chunks, total = condense(transcript, start, private=private)
    if start > total:  # the transcript was rewritten shorter: read it again from its start
        chunks, total = condense(transcript, 0, private=private)
```

replace the `existing = ...` line with

```python
        query = (chunk.user_text if chunk.user_text.strip() else chunk.text)[:5_000]
        existing = search(conn, project.vaults, query, limit=30) if query.strip() else []
```

and replace `_set_watermark(session, max(total, _watermark(session)))` with `_set_watermark(session, total)`.

In `src/val3/pending.py` add:

```python
@contextmanager
def drain_lock():
    """Wait for the drain lock: one extraction at a time on this machine, by the drainer or `val3 extract`."""
    with _lock(True):
        yield
```

In `src/val3/cli.py`, `cmd_extract`: `with pending.drain_lock(), connect(project.service) as conn:` in place of `with connect(project.service) as conn:`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_inbox.py tests/test_extract.py tests/test_pending.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/val3/inbox.py src/val3/extract.py src/val3/pending.py src/val3/cli.py tests/test_inbox.py tests/test_extract.py
git commit -m "fix: cap unknown notes, stale-lock rule, shrunk transcripts, drain lock for extract"
```

---

### Task 8: Hooks and vault creation — statement timeout, drainer failure, shared schema (m5, m6, m11)

**Files:**
- Modify: `src/val3/db.py` (`connect(statement_timeout_ms=)`; `create_vault`; `_check_common`)
- Modify: `src/val3/hooks.py` (both connections; `session_start` spawn failure)
- Test: `tests/test_hooks.py`, `tests/test_vault.py`

**Interfaces:**
- Consumes: `helpers.add_service` (Task 5).
- Produces: `db.connect(service, timeout=10, statement_timeout_ms: int | None = None)`; `hooks.STATEMENT_TIMEOUT_MS = 2000`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hooks.py` (add `import psycopg`):

```python
def locked(env):
    conn = psycopg.connect(f"host=127.0.0.1 port={env.port} user=postgres dbname=val3")
    conn.execute(f"LOCK TABLE {env.vault}.entry IN ACCESS EXCLUSIVE MODE")
    return conn


def test_a_locked_vault_does_not_hold_the_session_start(env):
    make_vault(env)
    conn = locked(env)
    try:
        started = time.monotonic()
        text = context(start(env), "SessionStart")
        elapsed = time.monotonic() - started
    finally:
        conn.close()
    assert "VAL3 is unavailable" in text and "statement timeout" in text
    assert elapsed < 8


def test_a_locked_vault_does_not_hold_the_prompt(env):
    make_vault(env)
    conn = locked(env)
    try:
        text = context(ask(env, "invoices april"), "UserPromptSubmit")
    finally:
        conn.close()
    assert "VAL3 is unavailable" in text


def test_a_drainer_that_cannot_start_still_gives_the_index(env):
    make_vault(env)
    assert save(env, fact(), env.transcript(tool_outputs=[OUTPUT])).returncode == 0
    queue = env.state / "queue"
    queue.mkdir(parents=True)
    (queue / "1-1.json").write_text(json.dumps({"session_id": "s", "transcript_path": "/nonexistent",
                                                "cwd": str(env.project), "event": "SessionEnd",
                                                "queued_at": 0, "attempts": 0}))
    site = env.root / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        "import subprocess\n_Real = subprocess.Popen\n\n\n"
        "class _Failing(_Real):\n"
        "    def __init__(self, args, *rest, **options):\n"
        "        if 'drain' in list(args):\n"
        "            raise OSError(11, 'Resource temporarily unavailable')\n"
        "        super().__init__(args, *rest, **options)\n\n\n"
        "subprocess.Popen = _Failing\n")
    text = context(start(env, PYTHONPATH=str(site)), "SessionStart")
    assert f"{env.vault}#1" in text
    assert "cannot start the drainer" in text and "val3 drain" in text
```

Append to `tests/test_vault.py` (import `add_service` from `helpers`):

```python
def test_a_role_that_does_not_own_the_shared_schema_creates_a_vault(env):
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    env.sql(f"CREATE ROLE {env.writer} LOGIN")
    env.sql(f"GRANT CREATE ON DATABASE val3 TO {env.writer}")
    result = env.run("vault", "create", env.vault + "_w", "--service", "val3writer")
    assert result.returncode == 0, result.stderr


def test_an_old_shared_schema_names_the_upgrade(env):
    database = "c" + env.vault
    env.sql(f"CREATE DATABASE {database}")
    add_service(env, "val3old", database)
    dsn = f"host=127.0.0.1 port={env.port} user=postgres dbname={database}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA val3_common")
    result = env.run("vault", "create", env.vault, "--service", "val3old")
    assert result.returncode == 1
    assert "val3_common is from an older version" in result.stderr and "vector" in result.stderr
    assert "val3 vault upgrade" in result.stderr
```

(`tests/test_vault.py` needs `import psycopg` if it lacks it.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_hooks.py tests/test_vault.py`
Expected: the locked-vault tests hang until `Env.run`'s 60 s timeout and FAIL; the drainer test FAILS (no index); the non-owner test FAILS (`must be owner of function`); the old-schema test FAILS (common.sql is not run, so the failure is a missing function, not the upgrade message).

- [ ] **Step 3: Implement**

In `src/val3/db.py`:

```python
def connect(service: str, timeout: int = 10, statement_timeout_ms: int | None = None) -> psycopg.Connection:
    options = {"options": f"-c statement_timeout={statement_timeout_ms}"} if statement_timeout_ms else {}
    return psycopg.connect(service=service, connect_timeout=timeout, autocommit=True, **options)
```

In `create_vault`, replace `_run_file(conn, "common.sql")` with:

```python
        if _exists(conn, "val3_common"):
            _check_common(conn)  # owned by whoever created it; vault upgrade (its owner) refreshes it
        else:
            _run_file(conn, "common.sql")
```

and add:

```python
def _check_common(conn) -> None:
    """A vault needs what common.sql creates; a shared schema left by an older version names the fix."""
    missing = [name for (name,) in conn.execute("""
        SELECT x FROM unnest(ARRAY['vector', 'unaccent']) AS x
        WHERE NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = x)
        UNION ALL
        SELECT f FROM unnest(ARRAY['val3_common.forbid_change()', 'val3_common.query_lexemes(regconfig,text)',
                                   'val3_common.or_query(text[])']) AS f
        WHERE to_regprocedure(f) IS NULL""")]
    if missing:
        raise Val3Error(f"the shared schema val3_common is from an older version (missing {', '.join(missing)}): "
                        "its owner runs `val3 vault upgrade <vault>` on an existing vault first")
```

In `src/val3/hooks.py`: add `STATEMENT_TIMEOUT_MS = 2000  # a locked table must not hold the session` near `LIMIT`; pass `statement_timeout_ms=STATEMENT_TIMEOUT_MS` to both `connect(project.service, timeout=3)` calls; in `session_start` replace

```python
    if pending.entries():
        pending.spawn_drain()
```

with

```python
    warning = None
    if pending.entries():
        try:
            pending.spawn_drain()
        except OSError as exc:  # the index still matters more than the catch-up
            warning = f"VAL3: queued sessions were not extracted (cannot start the drainer: {exc}); run `val3 drain`."
```

and its final `return context` with `return "\n\n".join(part for part in (context, warning) if part)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_hooks.py tests/test_vault.py tests/test_grant.py tests/test_pending.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/val3/db.py src/val3/hooks.py tests/test_hooks.py tests/test_vault.py
git commit -m "fix: hooks bound each statement; drainer failure keeps the index; vault create on a shared schema"
```

---

### Task 9: Embedding deadline and eval references (m15, m17, m18)

**Files:**
- Modify: `src/val3/writer.py` (`store_vectors`)
- Modify: `src/val3/evaluate.py` (`load_cases(path, vaults)`)
- Modify: `src/val3/cli.py` (`cmd_eval`)
- Modify: `tests/fake_embedder.py` (`tags_delay`)
- Test: `tests/test_embed.py`, `tests/test_eval.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `evaluate.load_cases(path: Path, vaults: tuple[str, ...]) -> list[Case]`; `FakeEmbedder.tags_delay` (seconds before answering `GET /api/tags`).

- [ ] **Step 1: Write the failing tests**

In `tests/fake_embedder.py`: in `__init__` add `self.tags_delay = 0.0   # seconds to wait before answering GET /api/tags`, and make `do_GET` start with `time.sleep(owner.tags_delay)`.

Append to `tests/test_embed.py` (add `import time` if missing):

```python
def test_one_deadline_bounds_digest_and_embedding_after_an_admission(env, embedder):
    embedding_vault(env, embedder.url)
    embedder.tags_delay = 4.0
    embedder.delay = 4.0
    started = time.monotonic()
    result = save(env, fact(), env.transcript(tool_outputs=[OUTPUT]))
    elapsed = time.monotonic() - started
    assert result.returncode == 0
    assert result.stdout.strip() == f"admitted {env.vault}#1"
    assert "warning: no vector stored" in result.stderr and "timed out" in result.stderr
    assert elapsed < 11.5
    assert vectors(env) == []
```

Append to `tests/test_eval.py`:

```python
def test_refs_to_undeclared_vaults_are_refused_before_embedding(env, embedder):
    embedding_vault(env, embedder.url)
    path = labelled(env, [{"query": "anything", "relevant": ["other_vault#1"]}])
    result = env.run("eval", str(path))
    assert result.returncode == 1
    assert f"{path}:1: other_vault#1 is in vault other_vault" in result.stderr
    assert embedder.inputs == []


def test_a_malformed_ref_is_reported_with_its_place(env, embedder):
    embedding_vault(env, embedder.url)
    path = labelled(env, [{"query": "anything", "relevant": []}, {"query": "more", "relevant": ["not a ref"]}])
    result = env.run("eval", str(path))
    assert result.returncode == 1
    assert f"{path}:2: invalid entry reference" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_embed.py tests/test_eval.py`
Expected: the deadline test FAILS (a vector is stored after about 12 s); the eval tests FAIL (no vault check; the parse error has no `path:2:` prefix).

- [ ] **Step 3: Implement**

In `src/val3/writer.py`, add `import time`, and in `store_vectors` replace

```python
        current = digest(settings, timeout)
```

with

```python
        deadline = time.monotonic() + timeout  # the digest and the first batch share one timeout
        current = digest(settings, timeout)
```

and inside the batch loop replace the `embed(..., timeout)` call with:

```python
            if start:
                deadline = time.monotonic() + timeout  # each later batch gets its own
            space, vectors = embed(settings, [entry_text(title, statement) for _, title, statement in batch],
                                   deadline - time.monotonic())
```

(`embed` with a non-positive timeout raises `EmbeddingError("... timed out")`: `_call` checks the deadline.) Update the docstring line on `limit` to say that the digest lookup and the first batch share `timeout`.

In `src/val3/evaluate.py`, change the signature to `def load_cases(path: Path, vaults: tuple[str, ...]) -> list[Case]:` and replace

```python
        for ref in relevant:
            parse_ref(ref)
```

with

```python
        for ref in relevant:
            try:
                vault, _ = parse_ref(ref)
            except Val3Error as exc:
                raise Val3Error(f"{path}:{number}: {exc}") from None
            if vault not in vaults:
                raise Val3Error(f"{path}:{number}: {ref.strip()} is in vault {vault}, "
                                f"which {CONFIG_FILE} does not declare")
```

In `src/val3/cli.py`, `cmd_eval`: `cases = load_cases(args.file, project.vaults)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_embed.py tests/test_eval.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/val3/writer.py src/val3/evaluate.py src/val3/cli.py tests/fake_embedder.py tests/test_embed.py tests/test_eval.py
git commit -m "fix: one deadline for vectors after an admission; eval checks refs and their place"
```

---

### Task 10: Documentation

**Files:**
- Modify: `docs/design/2026-10-08-val3-design.md` (§7, §10, §11, §12, §13)
- Modify: `docs/design/2026-10-09-semantic-search-design.md` (title, §4.2) — m19
- Modify: `README.md` (Commands; new "Reviewing" and "Backup and restore" sections)
- Modify: `TODO.md`

**Interfaces:** none.

- [ ] **Step 1: Main design**

In `docs/design/2026-10-08-val3-design.md`:
- §7, replace the backup bullet with: `- Backup: \`val3 backup\` (\`pg_dump\` of the whole database: every vault, \`val3_common\`, extensions, privileges); restore with \`pg_restore\` into a new database (operations design, 4.3).`
- §10, replace the paragraph with: `\`val3 review\` lists pending items; \`val3 review --tty\` walks them in the terminal with a card per item (for a supersession, the old and the new entry) and approve/reject/skip/quit; each decision goes through the writer. A reproduction is shown, never run. A local web page is deferred (section 12). Detail: \`docs/design/2026-10-11-operations-design.md\`.`
- §11, replace the `val3 import` bullet's first words with `` `val3 import` (plan 3b) reads ``.
- §12, add `- A local web page for the review list (bound to 127.0.0.1, token in the URL).`
- §13, add: `Plan 3 (\`docs/plans/2026-10-11-val3-plan-3-operations.md\`) implements the operations design.`

- [ ] **Step 2: Semantic design (m19)**

In `docs/design/2026-10-09-semantic-search-design.md`: title line → `# VAL3 — what to deliver and how to find it`; in §4.2 replace `each entry's vector for the configured\n  model` with `each entry's vector in the query's embedding space (4.3)`.

- [ ] **Step 3: README**

In `README.md`, Commands list: replace the `val3 review` line with
`- \`val3 review\` — pending review items; \`val3 review --tty\` decides them one by one; \`review show|approve|reject <id>\`.`
and add after the `val3 vault upgrade` line:
`- \`val3 backup [--service S] [--out FILE]\` — operator command: dump the whole database into a private file (see Backup and restore).`
Change the paragraph after the list to start: `` `review --tty`, `review approve|reject` and `tool add` are decisions for a person ``.

Add before `## Development`:

````markdown
## Reviewing

`val3 review --tty` shows each pending item as a card and asks `a` approve, `r` reject, `s` skip or
`q` quit (notices offer no reject: they record a supersession that already happened). Cards show
evidence quotes and, for a command, its reproduction marked `(not run)`: VAL3 never runs it. A
supersession notice shows the old entry (`BEFORE invoices#5`) and the new one (`NOW invoices#20`).
Entries are written `<vault>#<id>`, review items `review #<n>`.

## Backup and restore

`val3 backup` runs `pg_dump -Fc` over the whole `val3` database into
`~/.local/share/val3/backup/<database>-<time>.dump` (mode 0600, never overwritten). Its role needs
read access to every vault: in practice the owner. `pg_dump` must not be older than the server
(`pg_dump 14 cannot dump a PostgreSQL 16 server`); use the same major version for `pg_dump` and
`pg_restore`.

The dump does not hold extension packages, roles, `pg_service.conf`, `.pgpass` or the projects'
`val3.toml`. On the target server install the `unaccent` and `vector` packages and create the
roles the vaults are granted to, then:

```bash
createdb -T template0 val3_restored
pg_restore --single-transaction --exit-on-error -d "service=<restored>" <file>
```

Use the restored database only when `pg_restore` exits 0. Without the roles, add
`--no-owner --no-privileges` and grant again with `val3 vault grant`. VAL3 never restores over the
live database.
````

- [ ] **Step 4: TODO**

In `TODO.md`: replace the Plan 3 item with `- [ ] Plan 3b — \`val3 import\` (validated-memory 2.x adopters).`; remove the "Plan 4 deferred minors" item (fixed by plan 3, Task 9 and the spec's section 5); add `- [x] Plan 3 — terminal review, database backup, deferred minors (\`docs/plans/2026-10-11-val3-plan-3-operations.md\`).`

- [ ] **Step 5: Check and commit**

Run a search for internal project, company and host names over `README.md` and `docs/design/*.md` — expected: no match.

```bash
git add docs/design/2026-10-08-val3-design.md docs/design/2026-10-09-semantic-search-design.md README.md TODO.md
git commit -m "docs: terminal review, database backup and plan 3 in the design and README"
```

---

### Task 11: Check on the development machine and session close (by hand)

**Files:**
- Modify: `SESSION.md`, `TODO.md`

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest -q -rs`
Expected: all tests pass, no skips.

- [ ] **Step 2: Real backup of the development database**

Run `val3 backup` from the first adopted project (its `val3.toml` names the development service `val3dev`).
Expected: one line with a path under `~/.local/share/val3/backup/`, its size and the vaults; `stat -c %a <path>` prints `600`. Restore it into a scratch database of the `val3-dev` container with `docker exec` + `createdb -T template0` and `pg_restore --single-transaction --exit-on-error`, compare `SELECT count(*)` of the project vault's `entry` table in both, then drop the scratch database.

- [ ] **Step 3: Owner smoke test**

The owner runs `val3 review --tty` in that project from a terminal outside the agent session and decides the remaining pending item. Not a gate.

- [ ] **Step 4: Session close**

Add a dated `SESSION.md` entry (what was implemented, test count, the backup check, rulings), update `TODO.md`, commit `docs: plan 3 implemented`.
