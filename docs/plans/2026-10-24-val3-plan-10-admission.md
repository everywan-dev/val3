# VAL3 plan 10 — automatic admission: the machine admits, the agent settles near duplicates

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** an entry whose evidence checks is admitted without a person; a tool quote counts only when the call
that printed it did not already contain it; a new entry close to a live one of its kind is marked *to settle*
and the agent settles the pair; notices of changes already applied leave the review list.

**Architecture:** `transcript.evidence_text` returns tool output per call with that call's input, and
`writer._check_evidence` applies E1 per call. `writer._review_reason` keeps one reason, `no-evidence`; imports
admit through `writer.queue_import` with a mechanical decision row, so batch withdrawal is untouched. A new
file `neighbour.sql` adds three append-only tables (`neighbour_exempt`, `neighbour`, `settlement`); the
neighbour check runs in `writer.store_vectors` when an entry's vector is stored. A new module `settle.py` owns
reading pairs and settling them (`val3 settle`). `recheck.marks` adds the recipe marks (N2) to the file marks.

**Tech Stack:** Python 3.11 standard library, psycopg 3, PostgreSQL 16 with pgvector; pytest driving
`python -m val3` as a subprocess against a disposable PostgreSQL; the fake embedder of `tests/fake_embedder.py`.

**Spec:** `docs/design/2026-10-24-automatic-admission-design.md` (rulings A1-A4; decisions E1-E3, D1-D5, S1-S4,
N1-N3, R1-R3).

**Plan challenge (Sol 6.1, high, 2026-10-24):** 10 objections (8 important, 2 minor), all accepted: a tool
result without its call is not evidence; an import admits only when every requested quote verified; the
neighbour scan runs under the writer lock; pairs are found whichever side is embedded later, with old entries
exempt instead of "checked" (a model change no longer hides a pair); an import item whose review row already
exists is never decided mechanically; supersessions and withdrawals take `clock_timestamp()` after the lock so
the N2 comparison with confirmations is ordered; `val3 import --apply` stores vectors; status shows recipe
marks without git; an E1 test term of at least 8 characters; the obsolete extractor and withdrawal texts are
replaced, not only extended.

## Global Constraints

- Everything in English: code, comments, messages, docs, commits. No internal project names, hosts or people.
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error.
- Tests drive the CLI as a subprocess and assert on exit codes, output and rows; never import package internals.
- Branch `feature/automatic-admission` (exists, holds the design); Conventional Commits; one logical change per
  commit; commit trailers name the writing model.
- Suite: `.venv/bin/pytest -q` (Docker needed). One test: `.venv/bin/pytest -q tests/<file>::<test>`.
- Vault tables are append-only. The three new tables get the `append_only` and `append_only_truncate`
  triggers that `withdrawal` has.
- Every reader tolerates a vault without the new tables exactly as it tolerates one without `withdrawal`: no
  marks, no error. `val3 settle` requires them and exits 1 naming `val3 vault upgrade <vault>`.
- The hooks never block and always exit 0.
- Person-facing text that holds stored text goes through `views.safe` / `views._one_line`, as today.
- Output strings exactly as given (`<…>` are values):
  - E1 refusal: `discarded: evidence quote is in the input of the call that printed it (evidence[<i>]): quote
    output the call did not already contain` — the quote itself is never repeated;
  - `val3 save`, after an admission whose entry has neighbours: one line on **stderr** per neighbour,
    `val3: near <ref> (<score>): val3 settle <new> <ref> --replace | --drop | --keep`, score with two decimals;
    stdout keeps its single line;
  - `val3 settle`: `replaced <old> by <new>`, `dropped <new> (duplicate of <old>)`, `kept <new> and <old>`;
  - line mark: ` · to settle (near <ref>, <ref>)` after a title, on the newer entry of each pair;
  - `val3 show`: one line `TO SETTLE: close to <ref> (<score>)` per open pair, after the title line;
  - session index block, shown when any home-vault pair is open, after the re-check block:
    `Entries marked "to settle" are close to another entry of the same kind. Settle each pair: \`val3 settle
    <ref> <near> --replace\` if the new one replaces it, \`--drop\` if it repeats it, \`--keep\` if both hold
    (\`val3 settle --help\`).`
  - session-start `systemMessage`: ` · <k> to settle` after the re-check count when `k > 0`;
  - status: `to settle: 0` or `to settle: <n> (<ref>, …)` (at most 10 refs, then `, …`), after `to re-check`;
    `changes (7 days): none` or `changes (7 days): <s> superseded (<ref>, …), <w> withdrawn (<ref>, …)`
    (parts with 0 left out, at most 10 refs each);
  - `val3 show` of a recipe marked by N2: `TO RE-CHECK: cites <ref>, which is <superseded by <ref> | withdrawn>`.
- Mechanical decisions on import items: `review_decision.decided_by = 'val3 (mechanical)'`.
- Neighbour threshold: `[embedding] neighbour`, default `0.70`, a number strictly between 0 and 1.
- Withdrawal cause for a dropped duplicate: `duplicate`; reason `duplicate of <old ref>`.

## Review Focus

1. A legitimate quote that also appears in the command (a `grep 'ACME SL'` whose quote is `ACME SL`) is refused;
   the message must tell the agent to quote more of the output (Task 1 test).
2. Two unembedded entries embedded in one batch, newest first: the pair is found and stored with the newer
   entry as `entry_id` (Task 4 test).
3. An upgraded vault with old entries and `val3 embed` after a new model build marks nothing (Task 4 test).
4. `val3 settle --replace` on a pair whose old entry was superseded or withdrawn meanwhile, or when the new
   entry already supersedes another: exit 1, nothing written (Task 5 tests).
5. A project without `[embedding]`: entries admitted are recorded as exempt, so adding `[embedding]` later
   marks nothing (Task 4 test).

---

### Task 1: evidence per call (E1)

**Files:**
- Modify: `src/val3/transcript.py` (`evidence_text`), `src/val3/writer.py` (`_check_evidence`),
  `src/val3/extractor_prompt.md`, `src/val3/candidate.py` (the evidence help text)
- Test: `tests/test_save.py`

**Interfaces:**
- Produces: `evidence_text(path: Path, private: tuple[str, ...] = ()) -> tuple[list[tuple[str, str]], str]` —
  `([(call input, call output), …], user text)`, both normalised with `_normal`. The input of a `tool_use` is
  the newline-joined string values of its `input` (recursively); a `tool_result` is paired with its call by
  `tool_use_id`; a `tool_result` with no matching call is **not evidence** (it cannot be checked for E1 or for
  `val3` output). `Env.transcript(tool_outputs=…)` in `tests/conftest.py` writes each output as a complete call
  (a Bash `tool_use` with input `{"command": "cat report.txt"}` and its result), so existing fixtures keep
  working. The input of a typed `!` command is the body of the closest preceding `<bash-input>` record.
  Calls in `_model_text_calls` are left out, as today.

- [ ] **Step 1: Write the failing tests** in `tests/test_save.py`, using `env.transcript(tool_calls=…)` (extend
  the helper only if it cannot pair a call input with its output):
  - `test_a_quote_the_command_printed_from_its_own_text_is_discarded`: a Bash call `echo 'April has 42 invoices
    in the ERP'` with that output; saving a fact quoting `April has 42 invoices in the ERP` exits 1, stderr has
    `evidence quote is in the input of the call that printed it` and does not contain the quote; no entry row.
  - `test_a_quote_only_in_another_calls_input_still_counts`: call A input `grep invoices` output
    `April has 42 invoices`; call B input `echo April has 42 invoices`, output `x`; the quote from A's output
    is admitted (containment is per call).
  - `test_a_quote_longer_than_the_search_term_counts`: call input `grep 'ACME SUPPLIES' suppliers.csv`, output
    `ACME SUPPLIES SL;B12345678;active`; quote `ACME SUPPLIES SL;B12345678` admitted; quote `ACME SUPPLIES`
    alone refused with the exact E1 message (Review Focus 1).
  - `test_a_tool_result_without_its_call_is_not_evidence`: a transcript with only a `tool_result` record
    holding the quote → `discarded: evidence quote not found in tool output`.
  - `test_a_typed_command_that_echoes_its_text_is_discarded`: `<bash-input>echo 'April has 42 invoices in the
    ERP'</bash-input>` then its `<bash-stdout>` record; same refusal.
- [ ] **Step 2: Run them**: `.venv/bin/pytest -q tests/test_save.py -k "quote"` — expected FAIL (the echo cases are
  admitted today).
- [ ] **Step 3: Implement** `evidence_text` as in Interfaces and, in `_check_evidence`, accept a tool quote when
  some pair has `quote in output and quote not in input`; when the quote is in some output but in every such
  call's input, raise the E1 refusal; otherwise the existing "not found" refusal. In `extractor_prompt.md`
  replace the line "A fact backed only by the user's word goes to human review; use tool evidence when it
  exists." by "A quote must be output the call did not already contain in its command; use tool evidence
  when it exists." and add the same rule to the evidence help in `candidate.py`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q tests/test_save.py tests/test_extract.py tests/test_withdraw.py
  tests/test_confirm.py` — expected PASS; fix any existing transcript fixture whose quote sat in its own command.
- [ ] **Step 5: Commit** `feat: a tool quote counts only when its call did not already contain it`.

### Task 2: admission by evidence, not by kind (D1, D1a, D5, N3)

**Files:**
- Modify: `src/val3/writer.py` (`_review_reason`, `queue_import`, `withdraws_at_once`), `src/val3/vault.sql`
  (`review_item_reason_check`), `src/val3/db.py` (`_CHECKS`), `src/val3/importer.py` (counts it prints)
- Test: `tests/test_save.py`, `tests/test_import.py`, `tests/test_withdraw.py`, `tests/test_vault.py`

**Interfaces:**
- Produces: `_review_reason(candidate) -> str | None` returns `"no-evidence"` when `candidate["evidence"]` is
  empty, else `None`. `queue_import(conn, project, payload) -> Outcome` admits (status `admitted`) when the payload has
  evidence **and every item of `payload["import"]["evidence"]` has status `verified`** (the importer keeps only
  verified items in `evidence`, so a non-empty `evidence` is not enough). In one transaction it writes the
  `review_item` (reason `import`), the entry (`_admit`) and a `review_decision` (`approved`, `val3
  (mechanical)`, the entry id). The mechanical decision is written **only when `_queue` created the row**: an
  existing row keeps its outcome (`already-queued` when pending, so a legacy R1 item stays for the person;
  `previously-rejected`; `duplicate` when approved). Otherwise the item queues as today. `cmd_import --apply`
  then stores vectors (`store_vectors(conn, project)`, a failure printed as a warning; the admissions stand).
  `withdraws_at_once` is true for a ref with any evidence.

- [ ] **Step 1: Write the failing tests:**
  - `test_save.py::test_a_tool_with_command_evidence_is_admitted` (stdout `admitted <ref>`, no review row);
    `test_a_recipe_with_evidence_is_admitted`; `test_a_fact_with_only_user_evidence_is_admitted`;
    `test_a_tool_without_evidence_goes_to_review` (stdout `queued for review: review #1 (no-evidence)`).
  - `test_import.py::test_import_admits_items_with_verified_evidence`: after `val3 import --apply`, the
    entries exist, each import item has a decision `approved` by `val3 (mechanical)` with its entry id, the
    batch line counts them as `admitted`; `val3 withdraw --batch <b> --obsolete --reason r` (outside the agent)
    withdraws them.
  - `test_import.py::test_an_import_item_without_evidence_waits_for_a_person`.
  - `test_import.py::test_an_import_item_with_one_failed_quote_waits_for_a_person` (one verified, one
    mismatched quote, as the mixed fixture of `test_import.py` builds).
  - `test_import.py::test_an_existing_review_row_is_not_decided_mechanically`: a pending, an approved and a
    rejected row with the same wording (changed blob) → `already-queued`, `duplicate`, `previously-rejected`;
    no new decision rows.
  - `test_import.py::test_import_stores_vectors` (with the fake embedder: each admitted entry has a vector).
  - `test_withdraw.py::test_user_evidence_withdraws_a_fact_at_once` (inside the agent, `CLAUDECODE=1`).
  - `test_vault.py::test_upgrade_widens_the_review_reasons`: an upgraded old vault accepts `no-evidence`.
- [ ] **Step 2: Run them** — expected FAIL.
- [ ] **Step 3: Implement** per Interfaces; add `'no-evidence'` to the reason check in `vault.sql` and to
  `_CHECKS` (the value it must hold: `'no-evidence'`). In `candidate.WITHDRAWAL_EVIDENCE_FORMAT` replace
  "a tool item (or a "user" item on a preference or a decision) withdraws at once" by "an evidence item that
  checks withdraws at once". The importer's apply counts gain `admitted` if absent.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — expected PASS once the existing tests that asserted
  `queued for review` for tools, recipes, user-only facts and imports are updated to the new outcome (name
  each changed test in the commit body).
- [ ] **Step 5: Commit** `feat: admit every entry whose evidence checks; imports admit with a mechanical decision`.

### Task 3: notices leave the review list; recipes get a re-check mark (N1, N2)

**Files:**
- Modify: `src/val3/writer.py` (`_supersede`, `_withdraw_entry`), `src/val3/recheck.py` (`marks`, `Change`),
  `src/val3/views.py` (`show_entry`), `src/val3/status.py`
- Test: `tests/test_supersede.py`, `tests/test_withdraw.py`, `tests/test_status.py`, `tests/test_recheck.py`

**Interfaces:**
- Produces: `Change(path: str, missing: bool, cited: str | None = None)` — `cited` set (`"superseded by <ref>"`
  or `"withdrawn"`) for a recipe mark, `path` then holds the cited ref. `marks(conn, project, ids=None)`
  returns the recipe marks too, also when git is unavailable (sha `None`): a live recipe is marked when a step
  cites a fact (`step_fact`) or a tool (`recipe_step.tool_id`) that is superseded or withdrawn and the recipe
  has no `evidence` row created after that supersession or withdrawal.

- [ ] **Step 1: Write the failing tests:**
  - `test_supersede.py::test_a_supersession_queues_no_notice`; `test_withdraw.py::test_a_withdrawal_queues_no_notice`
    (no `review_item` rows).
  - `test_recheck.py::test_a_recipe_citing_a_superseded_fact_is_marked`: index line ends ` · to re-check`,
    `val3 show` has `TO RE-CHECK: cites <ref>, which is superseded by <ref>`; after `val3 confirm <recipe>` with
    evidence the mark is gone. Same for a withdrawn tool cited by a step.
  - `test_recheck.py::test_recipe_marks_need_no_git` (project not a git repository): the index mark, `val3 show`
    and `val3 status` all show it.
  - `test_recheck.py::test_a_confirmation_before_a_supersession_does_not_clear_the_mark`: confirm the recipe,
    then supersede its fact → marked.
  - `test_status.py::test_status_lists_recent_changes`: one supersession and one withdrawal →
    `changes (7 days): 1 superseded (<old ref>), 1 withdrawn (<ref>)`; on an empty vault `changes (7 days): none`.
- [ ] **Step 2: Run them** — expected FAIL.
- [ ] **Step 3: Implement**: drop the `_queue` calls in `_supersede` and `_withdraw_entry` (legacy notice items
  stay decidable through `decide`); both inserts set `created_at = clock_timestamp()` (they run after
  `_serialize`, like `confirm`), so a confirmation and a change are ordered by when each held the lock; the recipe query in `marks`; the `TO RE-CHECK: cites …` line in
  `show_entry`; in `status._recheck`, recipe marks are counted also when git is missing or fails:
  `to re-check: <n> (<ref>, …) · file evidence not checked (<reason>)` when `n > 0`, the current line otherwise;
  the status line from `event` rows with action `superseded` / `withdrawn` in the last 7 days
  (refs are the `entry_id` of each event).
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — expected PASS after updating the tests that expected notice items.
- [ ] **Step 5: Commit** `feat: notices of applied changes leave the review list; recipes citing a gone entry are marked`.

### Task 4: neighbour tables and the neighbour check (D2, D2a, D3, D4)

**Files:**
- Create: `src/val3/neighbour.sql`
- Modify: `src/val3/db.py` (`create_vault`, `upgrade_vault`, `_CHECKS` for the withdrawal cause),
  `src/val3/withdrawal.sql` (cause check named `withdrawal_cause_check`, with `duplicate`),
  `src/val3/config.py` (`Embedding.neighbour`), `src/val3/writer.py` (`store_vectors`, `_admit`)
- Test: `tests/test_neighbour.py`, `tests/test_vault.py`, `tests/test_embed.py`

**Interfaces:**
- Produces, in each vault: `neighbour_exempt (entry_id PK → entry, created_at)` — entries never paired with each
  other; `neighbour (entry_id → entry,
  other_id → entry, score real, created_at, PRIMARY KEY (entry_id, other_id), CHECK (entry_id > other_id))`;
  `settlement (entry_id, other_id, outcome CHECK IN ('replace', 'drop', 'keep'), settled_by text NOT NULL,
  created_at, PRIMARY KEY (entry_id, other_id), FOREIGN KEY (entry_id, other_id) → neighbour)`.
  `Embedding(url, model, floor, neighbour)`.
- Behaviour: in `store_vectors`, each vector is stored in its own transaction that first takes
  `writer._serialize(conn, vault)`; in it, after inserting the vector of entry `e`, when `e` is live: insert
  (`ON CONFLICT DO NOTHING`) a `neighbour` row `(greatest(e, o), least(e, o), score)` for every live entry `o`
  of the same kind with a vector in the same space, `1 - (vector <=> vector) >= neighbour`, and **not both `e`
  and `o` in `neighbour_exempt`**. Whichever side of a pair is embedded later finds the other, in any space, so
  a model change never hides a pair; old/old pairs never form. `_admit(conn, vault, candidate, org,
  unembedded: bool = False)` inserts `neighbour_exempt` for the new entry when `unembedded`; every caller
  (`submit`, `decide`, `queue_import`, `add_tool`) passes `project.embedding is None`. A vault without the
  tables skips both. `upgrade_vault` creates the
  tables and inserts `neighbour_exempt` for every existing entry; `create_vault` creates them empty.

- [ ] **Step 1: Write the failing tests** in `tests/test_neighbour.py` (fake embedder; bag-of-words texts):
  - `test_a_close_entry_of_the_same_kind_is_a_neighbour`: two facts sharing most words → one `neighbour` row
    with the newer as `entry_id`, score ≥ 0.70; a decision with the same words is not paired with them.
  - `test_the_threshold_is_a_setting`: `neighbour = 0.99` → no row.
  - `test_two_new_entries_embedded_together_are_paired` (service down at both saves, then `val3 embed`;
    Review Focus 2).
  - `test_upgraded_vault_entries_are_never_paired`: entries inserted before `vault upgrade` (drop the tables to
    simulate the old schema, as `test_vault.py` does for `withdrawal`), then `val3 embed` → no `neighbour` rows;
    a second model build (new digest from the fake embedder) → still none (Review Focus 3).
  - `test_a_new_entry_embedded_before_its_old_twin_is_paired`: upgraded vault with an old fact; new model build;
    save a near twin (its vector stored first, newest first), then `val3 embed` stores the old one's → one pair.
  - `test_concurrent_vector_stores_find_the_pair`: two `val3 embed` processes started together over two
    unembedded twins (as `test_writer_lock.py` starts concurrent writers) → one pair.
  - `test_entries_admitted_without_embedding_are_exempt`: save two twins without `[embedding]`, add it,
    `val3 embed` → no rows (Review Focus 5).
  - `test_embed.py::test_neighbour_must_be_between_0_and_1` (exit 1 naming the key, like `floor`).
  - `test_vault.py::test_upgrade_adds_the_neighbour_tables` (grants copied, triggers present, existing entries
    in `neighbour_exempt`) and `test_upgrade_accepts_the_duplicate_cause`.
- [ ] **Step 2: Run them** — expected FAIL.
- [ ] **Step 3: Implement** per Interfaces; add `"neighbour_exempt", "neighbour", "settlement"` handling to
  `upgrade_vault` as one step (all three from `neighbour.sql`, then the backfill); the `[embedding]` key list
  in `config.py` gains `neighbour`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — expected PASS.
- [ ] **Step 5: Commit** `feat: the neighbour check when an entry's vector is stored`.

### Task 5: `val3 settle` and the marks (S1-S4)

**Files:**
- Create: `src/val3/settle.py`
- Modify: `src/val3/cli.py` (`settle` subcommand, `cmd_save` stderr lines), `src/val3/views.py`
  (`session_index`, `prompt_context`, `show_entry`), `src/val3/hooks.py` (session-start count, per-prompt
  marks), `src/val3/indexes.py` (listing mark), `src/val3/status.py`
- Test: `tests/test_settle.py`, `tests/test_hooks.py`, `tests/test_status.py`

**Interfaces:**
- Produces: `settle.open_pairs(conn, vault: str, ids: Collection[int] | None = None) -> dict[int, list[tuple[int,
  float]]]` — newer entry id → `[(other id, score), …]` for pairs with both entries live and no settlement;
  `{}` when the tables are absent. `settle.settle(conn, project, new_ref: str, old_ref: str, outcome: str, by:
  str) -> str` — one transaction under `writer._serialize`; `replace` inserts the supersession (old by new);
  `drop` withdraws the new entry with cause `duplicate`, reason `duplicate of <old ref>`; all three insert the
  `settlement` row and an event `settled`. `by` is `agent <session>` inside the agent (as `val3 withdraw`
  computes it), else the person's login.
- CLI: `val3 settle <ref> <near> (--replace | --drop | --keep)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_settle.py`:
  - `test_save_names_the_neighbour_on_stderr` (exact stderr line; stdout single `admitted` line).
  - `test_replace_supersedes_the_older_entry`, `test_drop_withdraws_the_new_entry_as_a_duplicate`,
    `test_keep_records_the_pair_and_the_mark_goes` (exact outputs; rows; the line mark gone from the index).
  - `test_settle_refuses_a_pair_that_is_not_open` (no such pair, or already settled: exit 1).
  - `test_replace_refuses_when_the_old_entry_left_delivery` and
    `test_replace_refuses_when_the_new_entry_already_supersedes_another` (exit 1, no rows; Review Focus 4).
  - `test_settle_on_a_vault_without_the_tables_names_the_upgrade`.
  - `test_hooks.py::test_session_index_shows_entries_to_settle` (the block, the line mark, systemMessage
    ` · 1 to settle`); `test_prompt_marks_an_entry_to_settle`; `test_status.py::test_status_counts_pairs_to_settle`.
- [ ] **Step 2: Run them** — expected FAIL.
- [ ] **Step 3: Implement** per Interfaces and the Global Constraints strings. `cmd_save` prints the stderr lines
  after `_store_vectors` when the admitted entry has open pairs. Marks follow the pattern of the re-check mark in
  each view.
- [ ] **Step 4: Run** `.venv/bin/pytest -q` — expected PASS; update existing assertions on exact index or prompt
  text that now carry a settle mark because the fake embedder pairs similar fixtures (name them in the commit).
- [ ] **Step 5: Commit** `feat: val3 settle and the to-settle marks`.

### Task 6: documentation and close (R3)

**Files:**
- Create: `docs/adr/0008-automatic-admission.md`
- Modify: `README.md` (Reviewing, saving, `[embedding]` table, Commands, status), `src/val3/claude_block.md`,
  `CONTEXT.md` (neighbour, to settle, mechanical decision), `TODO.md`, `SESSION.md`

- [ ] **Step 1:** ADR 0008 records A1-A3 and that review by kind is replaced by review of what has no evidence.
- [ ] **Step 2:** README and the CLAUDE.md block describe automatic admission, E1, `val3 settle` and the new
  `[embedding] neighbour` key; the block gains one line for *to settle*.
- [ ] **Step 3: Run** `.venv/bin/pytest -q` and `ruff check src tests` — expected PASS / clean;
  `grep -rn` for internal names in src, tests, README, CONTEXT, design and ADRs — expected none.
- [ ] **Step 4: Commit** `docs: plan 10, automatic admission, ADR 0008`.

## Rollout (owner, after merge)

`val3 vault upgrade` on each vault; the pending items of R1 stay for one review by the person.
