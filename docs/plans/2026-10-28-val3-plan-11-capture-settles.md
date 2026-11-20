# VAL3 plan 11 — capture settles its own near duplicates; one entry may replace several

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** an entry may supersede several entries; capture compares each candidate that has close neighbours with
them in a second extractor call and admits it as new, drops it as a repeat, or admits it replacing the neighbours
it supersedes, settling those pairs itself, so the agent only sees what the slice could not decide.

**Architecture:** `candidate.validate` normalises `supersedes` to a list; `writer._check_refs` and `writer._admit`
loop over it. A new public `writer.near` finds a candidate's neighbours from its vector, the same rule as the
plan 10 pairing query. `settle.compare` (new) embeds a slice's candidates, looks up their neighbours, makes the
second extractor call (`settle_prompt.md`) and turns its answer into one `Plan` per candidate, with the date rule
(K2a) enforced in code under the writer lock. `extract.extract` applies each plan; `writer.submit` takes an
optional `Settling`, drops a repeat or stores the candidate's vector at admission, pairs it, supersedes and writes
the settlements in the admission's transaction (vector and settlements in savepoints, so their failure never
loses the admission).

**Plan challenge (Sol 6.1, high, 2026-10-28):** 15 objections (4 critical, 7 important, 4 minor); 14 accepted, 1
in part. Accepted: a compared candidate never falls back to the first answer's `supersedes` (it would bypass the
date rule); a repeat goes through `submit` (evidence checked, target live under the lock, else admitted); vector
and settlement writes in savepoints; neighbours shown whole, not clipped at 300 characters; a compared candidate
keeps its vector when undecided or when the second call fails; the flow is gated on the neighbour tables; the
answer is validated per candidate in code; the repeat event holds the candidate; note refs follow supersessions
after the slice; function-local import against the extract/settle cycle; `extractor_prompt.md` version line;
the legacy supersession card; fixture ids in the re-check test. In part: the date rule uses the neighbour's newest
evidence too (a `confirm` during the second call counts), but a slice is still dated by its last record (C3).

**Tech Stack:** Python 3.11 standard library, psycopg 3, PostgreSQL 16 with pgvector; pytest driving
`python -m val3` as a subprocess against a disposable PostgreSQL; `tests/fake_extractor.py` and
`tests/fake_embedder.py`.

**Spec:** `docs/design/2026-10-28-capture-settles-neighbours-design.md` (rulings C1-C4; decisions R1-R4, K1-K6,
K2a, K2b, K3a, K3b, M1). Builder choices of design §5 are fixed in this plan (Interfaces of Tasks 2 and 3).

## Global Constraints

- Everything in English: code, comments, messages, docs, commits. No internal project names, hosts or people.
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error.
- Tests drive the CLI as a subprocess and assert on exit codes, output and rows; never import package internals.
- Branch `feature/capture-settles` (exists, holds this plan); Conventional Commits; one logical change per
  commit; commit trailers name the writing model.
- Suite: `.venv/bin/pytest -q` (Docker needed). One test: `.venv/bin/pytest -q tests/<file>::<test>`.
- No schema change and no migration (R1, R2, M1): `supersession`, `neighbour`, `settlement` and `event` already
  hold everything. Vault tables stay append-only.
- Without `[embedding]`, or on a vault without the neighbour tables, capture works exactly as before (K5).
- `val3 save` is unchanged apart from accepting a list in `supersedes` (K6).
- The hooks never block and always exit 0. Person-facing text holding stored text goes through `views.safe` /
  `views._one_line`, as today.
- Settlements written by capture: `settled_by = 'extractor <session>'`; each also logs the event `settled`
  (`detail {"other", "outcome", "by"}`) as `settle.settle` does.
- Output strings exactly as given (`<…>` are values):
  - `val3 show` of an entry that supersedes: `supersedes: <ref>, <ref>` (every one, ascending id, no cap);
  - extraction summary: `admitted <a>, duplicate <d>, repeat <r>, queued <q>, already queued <k>, previously
    rejected <p>, rejected <x>; <n> extractor calls, $<cost>` (`<n>` counts both calls);
  - the event of a repeat: action `repeat`, `entry_id` the neighbour's id, `detail {"candidate": <the validated
    candidate>, "by": "extractor <session>"}`, session the extracted session (the dropped claim stays
    recoverable).
- Second call: prompt file `src/val3/settle_prompt.md`, its first line `Prompt version: 1.`; the first call's
  `PROMPT_VERSION` and the first line of `extractor_prompt.md` become 5 (its FORMAT now says `supersedes` takes a
  list; nothing else in it changes).
- `settle.SCHEMA` (the second call's JSON schema), exactly:
  `{"type": "object", "required": ["decisions"], "properties": {"decisions": {"type": "array", "items": {"type":
  "object", "required": ["candidate", "decision"], "properties": {"candidate": {"type": "integer"}, "decision":
  {"enum": ["new", "repeat", "replaces", "undecided"]}, "refs": {"type": "array", "items": {"type":
  "string"}}}}}}}`.

## Review Focus

1. An old session drained after a newer one stored the current value: the second call says *replaces*, but the
   neighbour was admitted after the slice's last record; nothing is superseded, the candidate goes the plan 10
   way and the pair stays open (Task 3 test). The guarantee is "not after the slice's last record": a slice whose
   old measurement is followed by recent talk is judged on its content (owner's ruling C3).
2. The second call names a ref that is not one of that candidate's neighbours, or says *repeat* with two refs:
   that candidate is undecided; the others of the answer still apply (Task 3 test).
3. A transcript whose records carry no `timestamp`: no candidate may replace; *new* and *repeat* still apply
   (Task 3 test).
4. Two candidates of one slice compared with the same neighbour, the first replacing it: the second is admitted
   without that ref in its `supersedes` and without a settlement for that pair (K3b; Task 3 test).
5. The embedding service is down during extraction: no second call, every candidate submitted as today, the
   summary printed and the exit code 0 (Task 2 test).

---

### Task 1: an entry supersedes several (R1-R4)

**Files:**
- Modify: `src/val3/candidate.py` (`validate`, `FORMAT`), `src/val3/writer.py` (`_check_refs`, `_admit`),
  `src/val3/settle.py` (`settle`, the `replace` branch), `src/val3/views.py` (`show_entry`, `candidate_lines`,
  `review_card` of a legacy `supersession` notice: every predecessor, ascending id),
  `src/val3/extract.py` (`PROMPT_VERSION = 5`), `src/val3/extractor_prompt.md` (its version line), `src/val3/cli.py` (help of `--replace` if it says "one")
- Test: `tests/test_supersede.py`, `tests/test_settle.py`, `tests/test_recall.py`, `tests/test_recheck.py`,
  `tests/test_review.py`, `tests/test_extract.py` (prompt version)

**Interfaces:**
- Produces: `validate(data)["supersedes"]` is `list[str]` — `[]` when absent or null, a one-element list for a
  string, the list deduplicated in order otherwise; an empty list given as `[]` is accepted. Each ref goes through
  `_ref` with the field name `supersedes[]` for a list item. Stored review payloads holding a string or `null`
  still validate (no migration). `views.candidate_lines` prints `approving supersedes <ref>, <ref>` and one
  `BEFORE <ref>` block per ref. `FORMAT` line: `"supersedes": ["<vault>#<id> of each entry this one replaces"]
  (optional)`.
- Behaviour: `_check_refs` checks every ref (same kind, live, home vault); the first failing ref refuses the
  whole candidate with today's message. `_admit` supersedes every ref with the entry's instant. `settle
  --replace` no longer refuses when `<new>` already supersedes another entry.

- [ ] **Step 1: Write the failing tests:**
  - `test_supersede.py::test_an_entry_supersedes_several`: facts #1 and #2 (distinct wording), then #3 saved with
    `supersedes: ["<v>#1", "<v>#2"]` → `admitted <v>#3`; `supersession` rows `[(1, 3), (2, 3)]`; `val3 show <v>#3`
    has the line `supersedes: <v>#1, <v>#2`; `val3 show <v>#1` has `SUPERSEDED by <v>#3`.
  - `test_supersede.py::test_supersedes_takes_one_ref_as_a_string_or_a_list`: `"<v>#1"` and `["<v>#1"]` give the
    same supersession row (two vaults or two runs).
  - `test_supersede.py::test_one_bad_ref_refuses_the_whole_candidate`: `["<v>#1", "<v>#9"]` → exit 1, stderr
    `<v>#9 does not exist`, no entry #2 and no supersession; same with a ref of another kind (`is a decision`).
  - `test_review.py::test_a_queued_payload_with_one_ref_still_approves`: a legacy `review_item` (helpers
    `legacy(..., reason="recipe")` or a direct insert) whose payload has `"supersedes": "<v>#1"` → `val3 review
    approve <n>` admits and supersedes #1; its card shows `approving supersedes <v>#1`.
  - `test_settle.py`: replace `test_replace_refuses_when_the_new_entry_already_supersedes_another` by
    `test_replace_works_when_the_new_entry_already_supersedes_another` — same setup (#3 supersedes #1, open pair
    (3, 2)); `val3 settle <v>#3 <v>#2 --replace` → `replaced <v>#2 by <v>#3`, supersession rows `[(1, 3), (2, 3)]`,
    settlement `(3, 2, 'replace', …)`.
  - `test_recall.py::test_recall_leaves_out_every_replaced_entry`: #1 and #2 replaced by #3 → `val3 recall`
    on their shared words lists #3 only; `val3 eval` replay or `recall` as of before #3 is not needed (R4: replay
    is keyed by the old entry).
  - `test_recheck.py::test_a_recipe_citing_either_replaced_fact_is_marked`, parametrised on the cited fact (#1 or
    #2): facts #1 and #2, a recipe citing one (the `recipe` helper has no evidence: it queues and its approval
    admits it as #3), then #4 replaces #1 and #2 → the recipe's `val3 show` has `TO RE-CHECK: cites <v>#<n>,
    which is superseded by <v>#4`.
  - `test_review.py::test_a_legacy_supersession_card_lists_every_predecessor`: legacy notice for #3, which
    supersedes #1 and #2 → the card shows both.
- [ ] **Step 2: Run them**: `.venv/bin/pytest -q tests/test_supersede.py tests/test_settle.py tests/test_review.py
  tests/test_recall.py tests/test_recheck.py` — expected FAIL on the new tests (a list is rejected today).
- [ ] **Step 3: Implement** per Interfaces; `show_entry` reads every `old_id` of `new_id` (ordered). Bump
  `PROMPT_VERSION` to 5 and update the test asserting `Prompt version: 4.`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — expected PASS.
- [ ] **Step 5: Commit** `feat: an entry may supersede several entries`.

### Task 2: the second extractor call (K1, K2, K2b, K4, K5)

**Files:**
- Create: `src/val3/settle_prompt.md`
- Modify: `src/val3/settle.py` (`compare`, `Plan`, `SCHEMA`, `PROMPT`), `src/val3/writer.py` (`near`, `Near`),
  `src/val3/transcript.py` (`Chunk.at`), `src/val3/extract.py` (calls `compare`), `tests/conftest.py`
  (`transcript(at=…)`, `respond` with a dict), `tests/fake_extractor.py` (`command-<n>.json`)
- Test: `tests/test_capture_settles.py` (new)

**Interfaces:**
- Consumes: `extract.run_extractor(project, material, prompt, schema) -> dict`, `extract.cost(result)`,
  `extract.ExtractorError` — imported **inside** `settle.compare` (extract imports settle at module level; a
  top-level import back would be circular); `embedding.embed`, `embedding.entry_text`, `writer.VECTOR_TIMEOUT`,
  `views.show_entry(conn, vault, id)`, `db.has_neighbours`.
- Produces:
  - `Chunk.at: str | None` — the `timestamp` value of the last record of the chunk that has one (the harness
    JSONL field, ISO 8601), else `None`.
  - `writer.Near` (frozen dataclass): `id: int, score: float | None` (`None` for a neighbour named only by the
    candidate's `supersedes`, K2b).
  - `writer.near(conn, project, space: Space, vector: list[float], kind: str, named: list[str]) -> list[Near]` —
    live home-vault entries of `kind` whose vector in `space` (model, digest, text_version, dims all equal) has
    `1 - (vector <=> given) >= project.embedding.neighbour`, then each ref of `named` that is a live home-vault
    entry of `kind` and not already listed; by score descending, named ones last. Read only, no lock; no
    `neighbour_exempt` condition (a candidate is never exempt).
  - `settle.Plan` (frozen dataclass): `supersedes: list[str]` (what the candidate is submitted with) and
    `settling: writer.Settling` (Task 3; in this task a stand-in with `space`, `vector` and empty tuples).
  - `settle.compare(conn, project, chunk: Chunk, items: list[object]) -> tuple[list[Plan | None], int, float]` —
    `items` are the raw `candidate` values of the first answer; one result per item, the number of extractor
    calls made (0 or 1) and their cost. `None` (submit as today) only when there is no vector for that item: the
    item fails `candidate.validate`, `project.embedding` is `None`, `has_neighbours` is false (K5, legacy vault),
    or `embed` raises `EmbeddingError`. Every other item gets a `Plan` carrying its vector, also when it has no
    neighbour or the second call fails (K3a: a compared candidate is admitted with its vector). No neighbour in
    any item → no second call.
  - Second-call material (stdin), exact layout; `<i>` is the item's position in the first answer:

    ```
    # Session slice of project <name> (home vault <home>); last record <at or "unknown">

    ## Candidates with close entries
    ### Candidate <i>
    <the validated candidate as JSON, indent 1>
    #### Close entry <ref> (similarity <0.00> | named in "supersedes")
    <views.show_entry of that entry, in full>

    ## Transcript
    <chunk.text>
    ```

    Neighbours are shown whole (statement, recipe steps, tool fields, evidence, origin line with its date): a
    clipped neighbour cannot be judged on "keeps every part that still holds".
  - `settle_prompt.md`, first line `Prompt version: 1.`, holds the K2 rules: the slice's most recent measurement
    is the current state; *repeat* when the candidate adds nothing to one close entry (exactly one ref);
    *replaces* when it states the current value of what each named entry states and keeps every part of it that
    still holds (one or more refs); *new* otherwise; *undecided* when the slice cannot tell; never rewrite a
    candidate; exactly one decision per candidate listed.
- Fixtures: `env.transcript(..., at: str | None = None)` writes `"timestamp": at` on every record when given.
  `env.respond(*calls)` writes a call given as a `dict` as that call's `structured_output` unchanged (a list
  keeps today's `{"candidates": …}`). The fake extractor, before answering call `n`, runs `command-<n>.json`
  (`{"argv": [...], "cwd": "..."}`) with `VAL3_EXTRACTING` removed from its environment.

- [ ] **Step 1: Write the failing tests** in `tests/test_capture_settles.py` (fake embedder via
  `embedding_vault`; a stored fact, then a slice whose candidate shares most of its words):
  - `test_a_candidate_with_a_neighbour_gets_a_second_call`: two calls; call 1's stdin starts with `# Session slice
    of project`, holds `### Candidate 0`, `#### Close entry <v>#1 (similarity `, the neighbour's whole statement
    (a statement over 300 characters whose last words appear in stdin) and the slice text; its `--json-schema` is
    `settle.SCHEMA`; its `--system-prompt` starts with `Prompt version: 1.`; summary says `2 extractor calls`.
  - `test_no_neighbour_no_second_call`: a distinct candidate → one call; the entry has a vector after extraction.
  - `test_a_named_supersedes_joins_the_neighbours`: a candidate with `supersedes: "<v>#2"` where #2 shares no
    words → `#### Close entry <v>#2 (named in "supersedes")`.
  - `test_candidates_of_one_answer_are_not_compared_with_each_other` (K4): two near-twin candidates, empty vault
    → one call.
  - `test_without_embedding_capture_is_unchanged` (K5): same slice, no `[embedding]` → one call, `admitted 1`.
  - `test_a_vault_without_neighbour_tables_is_unchanged` (K5): `[embedding]` set, neighbour tables dropped as
    `test_settle.py` `OLD_SCHEMA` does → one call, `admitted 1`.
  - `test_embedding_service_down_submits_as_today` (Review Focus 5): url of a closed port → one call, exit 0,
    `admitted 1`, the entry has no vector.
- [ ] **Step 2: Run them**: `.venv/bin/pytest -q tests/test_capture_settles.py` — expected FAIL.
- [ ] **Step 3: Implement** per Interfaces. `near` mirrors `writer._NEIGHBOURS` with the vector as a literal
  (`vector_literal`). `compare` embeds `entry_text(title, statement)` of every valid item in one `embed` call
  with `VECTOR_TIMEOUT`. In `extract`, call `compare` after the first answer is checked and before the submit
  loop; add its calls and cost to the counts. Until Task 3, `extract` ignores the plans (submits as today).
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — expected PASS.
- [ ] **Step 5: Commit** `feat: capture asks the extractor whether a candidate repeats or replaces its neighbours`.

### Task 3: capture applies the answer and settles (K2a, K3, K3a, K3b)

**Files:**
- Modify: `src/val3/settle.py` (`compare` reads the answer), `src/val3/writer.py` (`Settling`, `Outcome`,
  `submit`, `_admit`, `_store_vector`, `store_vectors`, `current`), `src/val3/extract.py` (applies plans,
  `repeat` count, `summary`, note refs)
- Test: `tests/test_capture_settles.py`, `tests/test_extract.py` (summary text)

**Interfaces:**
- Consumes: `settle.Plan`, `settle.compare`, `writer.Near`, `Chunk.at` from Task 2.
- Produces:
  - `writer.Settling` (frozen dataclass): `space: Space, vector: list[float], compared: tuple[int, ...],
    replaces: tuple[int, ...], repeat_of: int | None, slice_at: datetime | None, by: str`
    (`by = f"extractor {session}"`).
  - `Outcome` gains status `repeat` (`str()`: `repeat of <ref>`); `extract.STATUSES` gains `repeat`.
  - `writer.submit(conn, project, data, transcript, session, private=(), settling: Settling | None = None) ->
    Outcome`. Validation and `_check_evidence` run first, as for every candidate (a repeat is never decided on
    unchecked quotes). Then, in the admission's transaction under the writer lock, in this order:
    1. the exact-text duplicate check, as today;
    2. `repeat_of` set and that entry still live (`_gone` is `None`) → the event `repeat` (`entry_id` the
       neighbour, `detail {"candidate": <validated candidate>, "by": settling.by}`) and `Outcome("repeat",
       "<v>#<id>")`; nothing else is written. Not live → continue as an undecided candidate (no settlements);
    3. each ref of `settling.replaces` is kept only when the entry is live **and** the later of its `created_at`
       and its newest `evidence.created_at` is not after `slice_at` (K2a, re-checked under the lock so a
       `confirm` during the second call counts; `slice_at` `None` keeps none). A dropped ref leaves the
       candidate's `supersedes` and `compared` (that pair stays open; K3b). Never refused for it;
    4. `_check_refs`, `_review_reason` (an entry without evidence still queues), `_admit`.
  - `writer._admit(..., vector: tuple[Space, list[float]] | None = None, threshold: float | None = None)`: after
    the entry insert and **before** the supersessions (the replaced entries must still be live for the pairing
    query), `_store_vector` inside a savepoint (`conn.transaction()` nested); after the supersessions, the
    settlements inside a second savepoint: for each `neighbour` row `(new id, other)` with `other` in
    `compared`, `replace` when `other` is in the final `supersedes`, else `keep`, plus the `settled` event. A
    `psycopg.Error` other than `OperationalError` in either savepoint rolls back that part only: the admission
    stands, the vector is left to `store_vectors`, the pairs stay open. On a vault without the neighbour tables
    only the vector is stored.
  - `writer._store_vector(conn, vault, entry_id, space, vector, threshold) -> int` — the insert and the pairing
    query `store_vectors` runs today; `store_vectors` calls it.
  - `writer.current(conn, vault, ref: str) -> str` — follows `supersession` from `ref` to the entry that is not
    superseded.
  - `settle.compare` reads the answer: it must be an object with a `decisions` list; each decision an object
    whose `candidate` is the index of a listed candidate (not negative, not repeated: a candidate named twice is
    undecided), `decision` one of the four, `refs` a list of refs each among that candidate's neighbours;
    `repeat` exactly one ref, `replaces` one or more. A candidate whose decision fails a check, or that has none,
    is undecided. Plans: *new* → `supersedes []`, `compared` = all its neighbour ids; *replaces* → `supersedes`
    = its refs, `compared` = all, `replaces` = its ref ids; *repeat* → `repeat_of` its ref; *undecided*, a
    failed second call, or an item without neighbours → `supersedes []`, `compared ()`. A compared candidate
    never keeps the first answer's `supersedes`: the second call decides it (K2b), and the date rule is not
    bypassed by a fallback.
- Behaviour in `extract`: each item with a plan is submitted with `candidate["supersedes"] = plan.supersedes` and
  `settling=plan.settling`; `counts[outcome.status] += 1`; a note's refs take admitted, duplicate and repeat
  outcomes; after the slice's submits every collected note ref is replaced by `writer.current(…)` (a repeat of
  an entry another candidate of the slice replaced points at the replacing entry).

- [ ] **Step 1: Write the failing tests** in `tests/test_capture_settles.py` (`at="2100-01-01T00:00:00Z"`
  unless a test says otherwise; second answers via `env.respond([...], {"decisions": [...]})`):
  - `test_new_is_admitted_and_the_pair_kept`: entry #2 admitted with a vector, `settlement (2, 1, 'keep',
    'extractor <session>')`, a `settled` event; `val3 show <v>#2` has no `TO SETTLE` line.
  - `test_repeat_is_not_admitted`: no entry #2; summary has `repeat 1`; event `repeat` with `entry_id` 1 and the
    candidate in `detail`; a memory note that proposed it is settled with `<v>#1`.
  - `test_a_repeat_with_a_quote_not_in_the_transcript_is_discarded`: answer `repeat` → `rejected 1`, no `repeat`
    event.
  - `test_a_repeat_of_an_entry_withdrawn_during_the_second_call_is_admitted`: `command-1.json` runs `val3
    withdraw <v>#1 --wrong --reason r` → candidate admitted, no `repeat` event; same with a supersession of #1
    saved during the call.
  - `test_replaces_supersedes_every_named_neighbour` (two old entries, one new value): #1 and #2 stored,
    candidate answered `replaces ["<v>#1", "<v>#2"]` → #3 admitted, supersession `[(1, 3), (2, 3)]`, settlements
    `replace` for both; `val3 show <v>#3` has `supersedes: <v>#1, <v>#2`.
  - `test_replaces_some_keeps_the_others`: two neighbours, `replaces ["<v>#1"]` → `replace` for 1, `keep` for 2.
  - `test_an_old_slice_cannot_replace_a_newer_entry` (Review Focus 1): `at="2000-01-01T00:00:00Z"`, answer
    `replaces` → no supersession, no settlement, the pair open (`TO SETTLE` on `val3 show <v>#2`); the same when
    the **first** answer already names #1 in `supersedes` and the second answers `undecided`.
  - `test_a_neighbour_confirmed_during_the_second_call_is_not_replaced`: `at` one minute before now (UTC), #1
    admitted earlier; `command-1.json` runs `val3 confirm <v>#1` with fresh evidence → no supersession.
  - `test_without_timestamps_nothing_is_replaced` (Review Focus 3): `at=None`; `replaces` → pair open; a
    `repeat` of another candidate in the same answer still applies.
  - `test_malformed_decisions_leave_their_candidate_undecided` (Review Focus 2), parametrised: a ref not among
    the neighbours; `repeat` with two refs; a negative index; an index not listed; an index given twice; a
    decision that is not an object; `refs` not a list. The faulty candidate is admitted with its pair open; a
    well-formed decision for another candidate of the answer still applies.
  - `test_a_failed_second_call_admits_with_the_vector_and_leaves_pairs_open`: `response-1.json` not JSON → exit
    0, `admitted 1`, `2 extractor calls`, the entry has a vector, `neighbour` row open, no settlement.
  - `test_a_neighbour_replaced_by_an_earlier_candidate_is_dropped` (Review Focus 4): candidates 0 and 1 near #1,
    both answered `replaces ["<v>#1"]` → 0 supersedes #1; 1 admitted with no supersession and no settlement for
    (its id, 1); a third candidate answered `repeat ["<v>#1"]` with a memory note → the note points at 0's ref.
  - `test_a_neighbour_withdrawn_during_the_second_call_is_dropped` (K3b): `command-1.json` withdraws #1; answer
    `replaces ["<v>#1"]` → candidate admitted, no supersession, no settlement for that pair.
  - `test_a_named_neighbour_below_the_threshold_is_replaced_without_a_settlement`: K2b neighbour answered
    `replaces` → supersession row, no `neighbour` or `settlement` row for that pair.
  - `test_a_vector_that_cannot_be_stored_keeps_the_admission`: `REVOKE INSERT ON <v>.embedding` from the writer
    role (as `test_embed.py` does) → `admitted 1`, no vector, no settlement, exit 0.
  - `test_extract.py`: the summary assertions gain `repeat 0` where they spell it out.
- [ ] **Step 2: Run them** — expected FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — expected PASS.
- [ ] **Step 5: Commit** `feat: capture settles a candidate's neighbours at admission`.

### Task 4: documentation (no code)

**Files:**
- Create: `docs/adr/0009-capture-settles-neighbours.md`
- Modify: `README.md` (capture, `supersedes` list, extraction summary, `val3 settle`), `CONTEXT.md` (repeat,
  N->1 supersession), `src/val3/claude_block.md` only if it says an entry supersedes one entry

- [ ] **Step 1:** ADR 0009 records C1-C3 and K2/K2a: capture decides from the slice; one entry replaces all; an
  old slice never replaces a newer entry.
- [ ] **Step 2: Run** `.venv/bin/pytest -q` and `.venv/bin/ruff check src tests` — expected PASS / no new
  findings; `grep -rniE` for internal names over src, tests, README, CONTEXT, design and ADRs — expected none.
- [ ] **Step 3: Commit** `docs: plan 11, capture settles near duplicates, ADR 0009`.

TODO.md and SESSION.md are written by the architect at close.

## Rollout (after merge)

No `vault upgrade`. The third adopter's open marks stay for its agent (M1); it can now settle #46 against both
#22 and #44 with `--replace`.
