# VAL3 plan 9 — visibility: an index that fits, a controlled vocabulary, an indicator and `val3 init`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for
> tracking.

**Goal:** the session index lists or counts every live entry within the harness cap, under sections chosen by
topic; index names converge on a vocabulary that merges can fix; the person sees on screen what VAL3 delivered;
`val3 init` sets a project up and writes a VAL3 block into its `CLAUDE.md`.

**Architecture:** a new append-only table `index_alias` (file `index_alias.sql`, added by `vault create` and
`vault upgrade`) and a new module `indexes.py` that owns everything about knowledge indexes: alias resolution
(one SQL fragment every reader uses), each entry's section, counts, `val3 index` listings and merges.
`views.session_index` lays out sections from it and collapses the largest ones when the text exceeds the cap.
`hooks.run` adds a top-level `systemMessage` next to `additionalContext`. A new module `init.py` and a packaged
text `claude_block.md` implement `val3 init`.

**Tech Stack:** Python 3.11 standard library, psycopg 3, PostgreSQL 16 with pgvector; pytest driving
`python -m val3` as a subprocess against a disposable PostgreSQL.

**Spec:** `docs/design/2026-10-21-visibility-design.md` (rulings V1-V5, decisions D1-D16).

**Plan challenge (Sol 6.1, high, 2026-10-21):** 19 objections, all accepted: merges in the writer under its lock
(an opposed pair could form a permanent cycle), `show` resolves aliases, `safe()` on new person-facing text, init
confined to the project with atomic writes, TOML-safe values and hook detection by command, exact marker
handling, Tools protected and entry-accurate omission counts, prompt notice limited to rendered refs, vault-scoped
counts, `_admit`'s callers specified, D1 tests per vault and per origin, grants and triggers tested on a seeded
row, `val3 index merge` as an index name, existing assertions named. New-index notices go to stderr so stdout
stays one line.

## Global Constraints

- Everything in English: code, comments, messages, docs, commits.
- No internal project names, hosts or people in the repository.
- Exit codes: `0` ok or warnings only; `1` error; `2` usage error.
- Tests drive the CLI as a subprocess and assert on exit codes, output and rows; never import package internals.
- Branch `feature/visibility` (exists, holds the design); Conventional Commits; one logical change per commit;
  commit trailers name the writing model.
- Suite: `.venv/bin/pytest -q` (Docker needed). One test: `.venv/bin/pytest -q tests/<file>::<test>`.
- Vault tables are append-only; nothing is updated or deleted. `index_alias` gets the same `append_only` and
  `append_only_truncate` triggers as `withdrawal`.
- Every reader tolerates a vault without `index_alias` (created before this plan) exactly as it tolerates one
  without `withdrawal`: no alias resolution, no error. Only `val3 index merge` requires the table and exits 1
  naming `val3 vault upgrade <vault>`.
- The hooks never block and always exit 0 (V1); what they inject per prompt does not change.
- Session index cap: `hooks.LIMIT` (10,000 characters) for `additionalContext`; `systemMessage` is a separate
  field, at most 300 characters.
- Output strings exactly as given (`<…>` are values):
  - session index header (Task 3, verbatim);
  - collapsed section: `## <section> — <n> entries: val3 index <section>`, with ` (<k> to re-check)` after
    `entries` when `k > 0`; for `General` the command is `val3 index --none`;
  - `systemMessage` at session start: `VAL3: index of <n> entries`, then ` · <k> to re-check` when `k > 0`, then
    ` · <c> sections collapsed` when `c > 0`, then ` · <m> not shown` when `m > 0`;
  - `systemMessage` on a prompt with deliveries: `VAL3: <ref>, <ref>` (refs in delivery order);
  - a warning the hook adds to the context is also appended to `systemMessage` on its own line;
  - `val3 index merge`: `merged <from> into <into> (<n> entries)`;
  - `val3 save` and `val3 tool add` after an admission that created index names: one line on **stderr** per new
    name, `val3: new knowledge index: <name> (existing: val3 index)`; stdout is unchanged (existing tests assert
    the single `admitted <ref>` line);
  - status: `indexes: <n>` or `indexes: <n> · with one entry: <name>, …` (at most 10 names, then `, …`);
  - init step lines: `val3.toml: written` / `val3.toml: kept`; `hooks: added to .claude/settings.json` /
    `hooks: kept (<events> in <file>)`; `CLAUDE.md: block written` / `CLAUDE.md: block replaced` /
    `CLAUDE.md: created`; `connection: ok` / `connection: <problem>`.
- Every change to a vault goes through the writer (`writer.py`, CONTEXT "Writer") under its transaction-scoped
  vault lock (`writer._serialize`), taken before any check: a merge validates, resolves, counts, inserts the alias
  and logs its event in that one transaction, so two opposed merges cannot form a cycle (Sol, plan challenge).
- Person-facing text built from vault content or exception text goes through `views.safe()`, and single-line
  fields interpolated into a line through `views._one_line()`, before any truncation: `val3 index` output, the
  `systemMessage`, init's lines. `additionalContext` keeps today's treatment.
- Project-name normalisation for D1: `re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")`; an index whose
  name equals the normalised name of a vault of the project, or of any `entry.origin_project` in that vault,
  never becomes a section.

## Review Focus

1. A project with other hooks and settings in `.claude/settings.json` (other `SessionStart` hooks, permissions,
   a `$schema` key): `val3 init` keeps every key and hook and only adds its own — Task 6 test.
2. A `CLAUDE.md` that contains the begin marker but not the end marker (hand-edited): `val3 init` exits 1 naming
   the file and writes nothing to it — Task 6 test.
3. Merging a name that does not exist, or merging an index into itself: exit 1 with a message, no row — Task 2
   test.
4. An index name in a linked vault equal to one in the home vault: one section in the session index holding the
   entries of both vaults — Task 3 test.
5. A session index where even the collapsed lines do not fit (hundreds of sections): the text stays under the cap
   and ends with the `more entries not shown` line, and `systemMessage` says how many — Task 3 test.

---

### Task 1: `index_alias` table and alias resolution

**Files:**
- Create: `src/val3/index_alias.sql`, `src/val3/indexes.py`
- Modify: `src/val3/db.py` (`create_vault`, `upgrade_vault`), `src/val3/writer.py` (`_admit` and its three
  callers), `src/val3/views.py` (`show_entry`'s `indexes:` line)
- Test: `tests/test_indexes.py` (new), `tests/test_vault.py` (also add `index_alias` to `TABLES`)

**Interfaces:**
- Produces:
  - `index_alias.sql`: `CREATE TABLE index_alias (name text PRIMARY KEY CHECK (name ~ '^[a-z0-9][a-z0-9-]{0,62}$'),
    target_id bigint NOT NULL REFERENCES knowledge_index (id), created_at timestamptz NOT NULL DEFAULT now())`
    plus the two append-only triggers. `create_vault` runs it after `withdrawal.sql`; `upgrade_vault` adds it
    through the same missing-table loop (`for table in ("embedding", "withdrawal", "index_alias")`).
  - `indexes.has_aliases(conn, vault) -> bool` (`to_regclass`, as `db.has_withdrawals`).
  - `indexes.resolved(conn, vault) -> str`: SQL text with `{v}` for `vsql`, a subquery yielding
    `(entry_id bigint, name text)`: each `entry_index` row with its index name replaced by the final target of
    its alias chain (recursive CTE over `index_alias` joined to `knowledge_index`); without the table, the plain
    join. Duplicate (entry, name) pairs collapse (`DISTINCT`).
  - `indexes.canonical(conn, vault, name: str) -> str`: the final target name of `name`, or `name` itself.
  - `writer._admit(conn, vault, candidate, org) -> tuple[int, bool, tuple[str, ...]]`: stores each candidate
    index name through `indexes.canonical` (inside the caller's locked transaction) and returns, third, the names
    it inserted into `knowledge_index` (`INSERT … ON CONFLICT DO NOTHING RETURNING name`); `()` on the duplicate
    branch. Callers: `submit` (`writer.py:78`) passes them to `Outcome.new_indexes` (Task 5), `decide`
    (`writer.py:191`) ignores them, `add_tool` (`writer.py:543`) passes them to its `Outcome`.
  - `show_entry`'s `indexes:` line reads `indexes.resolved` (names after aliases, deduplicated, sorted).

- [ ] **Step 1: Write the failing tests**

`tests/test_vault.py::test_upgrade_adds_the_index_alias_table`: mirror
`test_upgrade_adds_the_withdrawal_table_and_its_review_reasons` (`test_vault.py:125`): grant reader and writer
roles, drop `index_alias`, `vault upgrade` prints `upgraded vault <v>`; the writer role inserts an alias row, the
reader selects it and cannot insert; with that row present `UPDATE`, `DELETE` and `TRUNCATE` each raise; a second
upgrade prints `is up to date`; `vault grant` on a fresh vault covers `index_alias`.

`tests/test_indexes.py::test_show_lists_indexes_after_aliases`: two aliases converging on one target and a
two-hop chain; `val3 show` prints the target once in its `indexes:` line.

`tests/test_indexes.py::test_save_stores_an_alias_name_as_its_target`: insert `knowledge_index` rows `facturas`,
`facturacion` and an `index_alias ('facturacion', <id of facturas>)` with SQL; save `fact(indexes=["facturacion"])`;
assert the entry's only `entry_index` row points at `facturas`.

`tests/test_indexes.py::test_readers_work_on_a_vault_without_the_alias_table`: drop `index_alias`; save a fact;
`val3 hook session-start` exits 0 with the entry under `## billing`.

- [ ] **Step 2: Run them; expect FAIL** (`no such table` / alias name stored as is).
- [ ] **Step 3: Implement** `index_alias.sql`, `indexes.has_aliases`, `indexes.resolved`, `indexes.canonical`,
  the `create_vault`/`upgrade_vault` changes and the `_admit` change.
- [ ] **Step 4: Run** `.venv/bin/pytest -q tests/test_indexes.py tests/test_vault.py`; expect PASS.
- [ ] **Step 5: Commit** `feat: index aliases resolved on every read and write`.

### Task 2: `val3 index` — listings and merge

**Files:**
- Modify: `src/val3/indexes.py`, `src/val3/writer.py` (merge), `src/val3/cli.py`
- Test: `tests/test_indexes.py`

**Interfaces:**
- Consumes: `indexes.resolved`, `indexes.canonical`, `indexes.has_aliases` (Task 1); `db.live`; `recheck.marks`;
  `writer._serialize`, `writer._log`.
- Produces:
  - `indexes.counts(conn, vaults: Sequence[str]) -> dict[str, int]`: live entries per resolved index name over
    the given vaults, each vault resolved through its own aliases. `counts(conn, project.vaults)` for listings,
    `counts(conn, [project.home])` for the vocabulary (Task 5), `counts(conn, [vault])` for D1's per-vault
    ranking (Task 3).
  - `indexes.listing(conn, project) -> str`: one line per resolved name, `<name>  <n>`, sorted by count desc then
    name; `(no indexes)` when empty.
  - `indexes.members(conn, project, name: str | None) -> str`: the live entries of the resolved index `name`
    (`None`: entries with no section index, per D1's exclusions) in the session-index line format
    `- <ref> [<kind>] <title>` plus ` · to re-check` for marked home-vault entries; exit 1
    `no index <name>` when no vault knows the name.
  - `writer.merge_index(conn, project, source: str, into: str) -> str`, in the writer because it changes a vault:
    one transaction, `_serialize` first, then every check. Home vault only; both names must exist in the
    home vault's `knowledge_index`; `source` must not already be an alias; `into` is replaced by
    `canonical(into)`; `source == canonical(into)` is refused; inserts one `index_alias` row and logs an `event`
    row `index-merged` with `{"from": source, "into": target}`; returns `merged <from> into <into> (<n> entries)`
    with `n` the live entries that had `source`. Missing table: `Val3Error` naming `val3 vault upgrade <vault>`.
  - CLI: `val3 index [words…] [--none]`. No word: listing. One word: that index (so an index named `merge` is
    listed with `val3 index merge`). Three words with the first `merge`: merge. Any other count, or a word with
    `--none`: exit 2. Merge is not person-only (D7: a person or the agent merges). Titles and names printed
    through `safe()`/`_one_line()`.

- [ ] **Step 1: Write the failing tests** in `tests/test_indexes.py`:
  - `test_index_lists_names_with_live_counts`: three saved facts under `billing`, `billing`, `flows`;
    `val3 index` prints `billing  2` before `flows  1`.
  - `test_index_name_lists_its_entries`: `val3 index billing` prints both refs with `[fact]` and titles.
  - `test_index_none_lists_entries_without_an_index`: a fact with `indexes: []` appears under `--none`.
  - `test_merge_moves_entries_under_the_target`: merge `facturacion` into `facturas`; output
    `merged facturacion into facturas (1 entries)`; `val3 index facturas` lists both entries; `val3 index`
    no longer lists `facturacion`; one `index_alias` row.
  - `test_merge_into_an_alias_stores_its_final_target` and `test_merging_a_target_resolves_the_chain`
    (`a → b`, then `b → c`: `val3 index c` lists the entries of `a`, `b` and `c`).
  - `test_merge_refusals`: unknown name, `merge x x`, an `a` already merged, and `merge b a` after `merge a b`
    (would close a cycle): each exit 1, neither an `index_alias` row nor an `index-merged` event added.
  - `test_index_named_merge_is_listed`: an entry under `merge`; `val3 index merge` lists it.
  - `test_index_output_escapes_terminal_sequences`: a title holding `\x1b[2J` and `‮` prints as escapes.
  - `test_merge_on_a_vault_without_the_alias_table`: exit 1, message contains `val3 vault upgrade`.
- [ ] **Step 2: Run; expect FAIL** (`invalid choice: 'index'`).
- [ ] **Step 3: Implement** the four functions and the subcommand.
- [ ] **Step 4: Run** `.venv/bin/pytest -q tests/test_indexes.py`; expect PASS.
- [ ] **Step 5: Commit** `feat: val3 index lists and merges knowledge indexes`.

### Task 3: session index by section, collapsing the largest

**Files:**
- Modify: `src/val3/views.py` (`_INDEX_HEADER`, `session_index`), `src/val3/indexes.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `indexes.resolved`, `indexes.counts` (Tasks 1-2).
- Produces:
  - `indexes.sections(conn, project) -> dict[tuple[str, int], str]`: `(vault, entry_id) -> section name` for
    every live non-tool entry, per D1 (the resolved index with most live entries in that vault, ties by name,
    excluding normalised project/vault names; none left → `General`).
  - `views.session_index(conn, project, limit) -> tuple[str, IndexSummary]` with
    `IndexSummary(entries: int, recheck: int, collapsed: int, omitted: int)` (a frozen dataclass in `views.py`).

Header, verbatim:

```
# VAL3 memory index

Verified entries of this project's vaults, grouped by knowledge index. Before working on a topic listed here,
read its entries: `val3 show <ref>` prints one with its evidence and steps, `val3 index <name>` lists a
collapsed section. `val3 recall <terms>` searches; `val3 save --help` shows how to record a verified entry.
Registered tools answer derivable questions: prefer them over stored facts.
```

Layout: as today (`Tools` first with priority order, sections by name, `General` last; the re-check header after
the main header when any entry is marked). If the full text exceeds `limit - 100`, collapse non-`Tools` sections
one at a time, largest first (ties: later name first), recomputing the length after each, until it fits. If all
are collapsed and it still does not fit, drop whole collapsed section lines from the end (never a `Tools` line),
and append `<m> more entries not shown: use \`val3 recall <terms>\`.` where `m` counts the **entries** those
dropped lines represented. Only if `Tools` alone exceeds the budget are tool lines dropped from its end, counted
the same way (design D2 amended: Tools in full unless they alone exceed the cap). `IndexSummary.collapsed`
counts collapsed sections still shown; `IndexSummary.omitted` equals `m`.

- [ ] **Step 1: Write the failing tests** in `tests/test_hooks.py`:
  - `test_entry_is_listed_under_its_largest_index`: entries A (`billing`, `zeta`), B (`zeta`), C (`zeta`):
    A appears under `## zeta`, not `## billing`.
  - `test_project_name_index_is_not_a_section`: an entry with indexes `[<vault name with - for _>, "billing"]`
    is under `## billing`; one with only the vault name is under `## General`.
  - `test_large_index_collapses_largest_section_first`: 120 facts under `big` with 150-character titles
    (`INSERT_MANY` style plus `entry_index` rows) and 3 under `small`: text ≤ 10,000; contains
    `## big — 120 entries: val3 index big`; lists the 3 `small` refs; no `more entries not shown`.
  - `test_collapsed_general_points_to_index_none`.
  - `test_linked_vault_index_merges_with_home_section` (Review Focus 4).
  - `test_index_overflow_after_collapsing_still_fits` (Review Focus 5): 2 tools, then 400 sections with
    60-character names holding 3 entries each; ≤ 10,000; both tool lines present; ends with
    `<m> more entries not shown` where `m` is 3 × the dropped sections (computed from the listed sections).
  - `test_another_projects_name_is_not_a_section`: entries written with `origin_project` `other_proj` (SQL
    insert) under `other-proj` and `billing`: listed under `## billing`.
  - `test_section_ranking_is_per_vault`: home has `a` ×3, `b` ×1; linked has `b` ×3, `a` ×1; an entry in each
    vault indexed `a` and `b` goes to `## a` (home) and `## b` (linked); each ref appears once.
  - `test_aliases_apply_to_their_own_vault`: home aliases `x → a`; the linked vault has its own `x`; the
    linked entry stays under `## x`.
  - Update `test_session_index_respects_the_context_limit` (300 entries without index now collapse `General`:
    assert the collapsed line instead of `more entries not shown`).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** `indexes.sections`, `IndexSummary`, the new header and the collapse; `hooks.session_start`
  uses the text part (the summary is consumed in Task 4).
- [ ] **Step 4: Run** `.venv/bin/pytest -q tests/test_hooks.py`; expect PASS.
- [ ] **Step 5: Commit** `feat: session index sections by largest index and collapsed when over the cap`.

### Task 4: indicator for the person

**Files:**
- Modify: `src/val3/hooks.py` (`run`, `session_start`, `prompt`), `src/val3/views.py` (`prompt_context`)
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `views.session_index` → `(text, IndexSummary)` (Task 3).
- Produces: `views.prompt_context(...) -> tuple[str | None, list[str]]`: the same text as today and the refs whose
  blocks it actually holds, in order. Handlers return `(context: str | None, notice: str | None)`; `run` prints
  `{"systemMessage": safe(notice)[:300], "hookSpecificOutput": {...}}` when there is a context, `systemMessage`
  omitted when `notice` is None. The prompt notice names only the rendered refs. The failure path
  (`VAL3 is unavailable …`) uses the warning as both. `capture` unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `test_session_start_tells_the_person_the_index_size`: two facts → `systemMessage == "VAL3: index of 2 entries"`.
  - `test_session_start_message_counts_collapsed_sections` (reuse Task 3's large fixture): contains
    `· 1 sections collapsed`.
  - `test_prompt_tells_the_person_what_it_delivered`: `systemMessage == f"VAL3: {env.vault}#1"`.
  - `test_prompt_without_deliveries_has_no_message`: stdout empty (unchanged).
  - `test_unreachable_server_warning_reaches_the_person`: `systemMessage` contains `VAL3 is unavailable`.
  - `test_embedding_warning_reaches_the_person`: the fallback warning of
    `test_prompt_falls_back_to_full_text_and_warns_once` appears in `systemMessage` on its own line.
  - `test_prompt_message_names_only_rendered_entries`: the `LONG` fixture (`test_hooks.py`), whose blocks do not
    all fit; every ref in `systemMessage` is in `additionalContext`, in the same order, and no other.
  - `test_warning_with_terminal_sequences_is_escaped`: `val3.toml` with `service = "val\u001b3"` (no such
    service; the connection error quotes the name): `systemMessage` holds `\x1b` as an escape, no raw ESC.
- [ ] **Step 2: Run; expect FAIL** (`KeyError: 'systemMessage'`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/pytest -q tests/test_hooks.py`; expect PASS.
- [ ] **Step 5: Commit** `feat: hooks tell the person what VAL3 delivered`.

### Task 5: vocabulary for writers and the fragmentation signal

**Files:**
- Modify: `src/val3/extract.py` (`extract`, `_material`, `PROMPT_VERSION`), `src/val3/extractor_prompt.md`,
  `src/val3/candidate.py` (`FORMAT` comment line for `indexes`), `src/val3/writer.py` (`Outcome`),
  `src/val3/cli.py` (`cmd_save`, `cmd_tool_add`), `src/val3/status.py`
- Test: `tests/test_extract.py`, `tests/test_save.py`, `tests/test_status.py`

**Interfaces:**
- Consumes: `indexes.counts` (Task 2); names returned by `_admit` (Task 1).
- Produces:
  - `_material(project, existing, transcript_text, notes=(), vocabulary: dict[str, int] | None = None)`;
    `extract` computes `indexes.counts(conn, [project.home])` once per call and passes it. The material gets,
    before `## Existing entries`, a section `## Knowledge indexes` with
    `Reuse one of these names; a new name only when none fits; never the project's name.` and one line
    `- <name> (<n>)` per home-vault index (count desc, then name), or `(none yet)`. `PROMPT_VERSION` becomes 4
    and the first line of `extractor_prompt.md` says `Prompt version: 4.`; the prompt's `indexes:` sentence says
    the same rule. Update `test_extract.py`'s `Prompt version: 3` assertion.
  - `FORMAT`'s indexes line: `"indexes": ["existing knowledge index names (val3 index); a new one only when none
    fits; never the project's name"]`.
  - `Outcome` gains `new_indexes: tuple[str, ...] = ()`; `__str__` unchanged; `cmd_save` and `cmd_tool_add` print
    the stderr lines.
  - status line after the re-check line, per Global Constraints.

- [ ] **Step 1: Write the failing tests:**
  - `test_extract.py::test_extractor_sees_the_vault_indexes`: one saved fact under `billing`; run extraction;
    the recorded call's material contains `## Knowledge indexes` and `- billing (1)`.
  - `test_extract.py::test_extractor_vocabulary_is_the_home_vault_after_aliases`: a linked-only name is absent;
    a merged name appears only as its target with the summed count.
  - `test_save.py::test_save_reports_a_new_index`: first save's stderr has
    `val3: new knowledge index: billing (existing: val3 index)` and stdout is exactly `admitted <ref>`; a second
    fact under `billing` has no such line.
  - `test_save.py::test_save_help_states_the_index_rule`: `val3 save --help` run outside any project contains
    `never the project's name`.
  - `test_status.py::test_status_names_single_entry_indexes`: indexes `billing` ×2, `flows` ×1 →
    `indexes: 2 · with one entry: flows`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the three test files; expect PASS.
- [ ] **Step 5: Commit** `feat: writers see the index vocabulary; status names single-entry indexes`.

### Task 6: `val3 init`

**Files:**
- Create: `src/val3/init.py`, `src/val3/claude_block.md`
- Modify: `src/val3/cli.py`, `src/val3/status.py` (`_hooks` uses `init.hook_events`); package data already covers
  `*.md`
- Test: `tests/test_init.py` (new)

**Interfaces:**
- Consumes: `config.load_project`, `config.check_vault_name`, `db.connect`, `embedding.embed`.
- Produces:
  - `init.hook_events(settings: object) -> list[str]`: the `val3 hook <event>` events named by the `command` of
    command hooks under `settings["hooks"][<Event>][i]["hooks"][j]`, nothing else (a permission rule or a prompt
    hook mentioning `val3 hook prompt` does not count). `status._hooks` switches to it (today it regex-scans the
    whole file, `status.py:69`).
  - `init.run(root: Path, service: str | None, vault: str | None, linked: list[str], project: str | None)
    -> tuple[list[str], int]` (step lines, exit code). CLI `val3 init [--service S] [--vault V] [--linked V]…
    [--project NAME]`, run in the project root (`Path.cwd()`).

File writing rules (all three files): each target, after resolving symlinks, must lie inside `root.resolve()`
(a `CLAUDE.md` linked to `AGENTS.md` in the project is fine; a `.claude` linked outside, or a dangling link, is
exit 1 naming the path, nothing written there). A changed file is written to a temporary file in the same
directory and moved over with `os.replace`, keeping the old file's mode; a new file is created with
`O_EXCL`. Files are read and written as bytes (`CLAUDE.md` decoded as UTF-8 only to find the markers), so CRLF
and text outside the block keep their bytes. `OSError` becomes `Val3Error` naming the path.

Steps, in order (D11-D16):
1. `val3.toml`: absent → `--service` and `--vault` are required (exit 2 naming them otherwise); vault names
   checked with `check_vault_name`, duplicates refused (exit 2); strings written as TOML basic strings with
   `json.dumps(value, ensure_ascii=False)`; the text is parsed back with `load_project` before it is written
   (failure: exit 1, nothing written). Keys: `service`, `project` only when given, `[vaults] home`, `linked` only
   when given. Present → loaded; any flag given that differs from it → exit 2
   `val3.toml exists and says <key> = <value>`; else `kept`.
2. Hooks: parse both `.claude/settings.json` and `.claude/settings.local.json` first (each absent, or a JSON
   object; invalid JSON, invalid UTF-8 or a non-object: exit 1 naming the file, nothing written). Any
   `hook_events` in either → `kept`. None → in `.claude/settings.json` (absent: `{}`) `hooks` must be absent or an
   object whose present event values are lists (else exit 1, unchanged); append to `hooks[<Event>]` the four
   entries of README §Claude Code hooks, set `autoDreamEnabled` to `false`, write with `indent=2` and a trailing
   newline.
3. `CLAUDE.md` at the root: block = `<!-- val3:begin -->\n` + `claude_block.md` + `<!-- val3:end -->\n`. Exactly
   one begin marker followed by exactly one end marker → replace those bytes (identical text → still
   `block replaced`); any other count or order of markers → exit 1 naming the file, unchanged; none → append,
   after a newline if the file does not end with one and one blank line; file absent → created with the block.
4. Connection: connect (timeout 3); each vault exists (`pg_namespace`); `index_alias` and
   `withdrawal.evidence` present, else `connection: vault <v> needs \`val3 vault upgrade <v>\``; with
   `[embedding]`, one embed call (`embedding.embed`, timeout 5) else `connection: embedding service unreachable
   (<url>)`. Any problem → exit 1 after printing all lines.

`claude_block.md` (the builder may polish wording, keeping every point): a `## VAL3 memory` heading; VAL3 holds
verified facts, recipes, preferences, decisions and tools of this project; at session start it injects an index
and on each prompt up to three matching entries, both automatically; before working on a listed topic read its
entries (`val3 show <ref>`, `val3 index <name>`, `val3 recall <terms>`); record a fact verified in this session,
a preference or decision the user stated, or a recipe that worked, with `val3 save` (`val3 save --help`), reusing
an existing index name; an entry marked *to re-check*: `val3 confirm` if it holds, `val3 save` with `supersedes`
if it changed, `val3 withdraw` if it no longer applies; `val3 --help` for the rest. At most 20 lines.

- [ ] **Step 1: Write the failing tests** in `tests/test_init.py` (each in a fresh `env.project` without
  `val3.toml`):
  - `test_init_sets_up_a_project`: `val3 init --service val3test --vault <v>` on a created vault → exit 0;
    `val3.toml` loads (`val3 status` exits 0); `.claude/settings.json` has the four hooks and
    `autoDreamEnabled: false`; `CLAUDE.md` holds the block once; output has `connection: ok`.
  - `test_init_twice_changes_nothing`: second run → `val3.toml: kept`, `hooks: kept (…)`,
    `CLAUDE.md: block replaced`; files byte-identical to after the first run.
  - `test_init_keeps_other_settings_and_hooks` (Review Focus 1).
  - `test_init_keeps_existing_val3_hooks_in_settings_local` (delivery-only adopter: only session-start and prompt
    in `settings.local.json` → `settings.json` not created, `hooks: kept`).
  - `test_init_replaces_only_its_block`: text before and after the markers unchanged after a re-run with a
    different block in place.
  - `test_init_refuses_a_half_marked_claude_md` (Review Focus 2).
  - `test_init_with_differing_flags_exits_2`; `test_init_without_flags_and_without_config_exits_2`.
  - `test_init_reports_a_missing_vault`: exit 1, files written, line names the vault.
  - `test_init_with_invalid_settings_json_exits_1`: file unchanged; same for an invalid `settings.local.json`
    and for `{"hooks": []}`.
  - `test_init_ignores_val3_hook_text_outside_command_hooks`: `{"permissions": {"deny": ["Bash(val3 hook prompt)"]}}`
    → the four hooks are added.
  - `test_init_refuses_repeated_or_reversed_markers`; `test_init_keeps_crlf_and_text_outside_the_block`
    (CRLF file without a final newline, non-ASCII text).
  - `test_init_refuses_a_claude_dir_linked_outside`: `.claude` → a directory outside the project; exit 1; the
    outside directory unchanged.
  - `test_init_writes_safe_toml`: `--project 'a "b"\n[embedding]'` → `val3.toml` loads, `project` equals that
    string, no `[embedding]` table; `--linked` twice with one name → exit 2.
- [ ] **Step 2: Run; expect FAIL** (`invalid choice: 'init'`).
- [ ] **Step 3: Implement** `init.py`, `claude_block.md`, the subcommand.
- [ ] **Step 4: Run** `.venv/bin/pytest -q tests/test_init.py`; expect PASS.
- [ ] **Step 5: Commit** `feat: val3 init writes val3.toml, the hooks and a CLAUDE.md block`.

### Task 7: documentation and close

**Files:**
- Modify: `README.md` (§Project and §Claude Code hooks lead with `val3 init`; new §Knowledge indexes with
  `val3 index` and merge; §Commands), `CONTEXT.md` (Knowledge index, Section, Index alias, Session index,
  `val3 init`), `TODO.md`, `SESSION.md`
- Create: `docs/adr/0007-visibility.md` (V1-V5 and D1, D2, D6, D7, D10, D13, D14 in short)

- [ ] **Step 1:** write the docs; `grep -rniE '<internal names>'` over the changed files returns nothing.
- [ ] **Step 2:** full suite `.venv/bin/pytest -q`; expect all pass; `ruff check src tests` clean.
- [ ] **Step 3: Commit** `docs: plan 9 closed, ADR 0007`.

## Rollout (after merge; owner and architect, not tasks)

1. Owner: `val3 vault upgrade` on both development vaults (adds `index_alias`).
2. Architect proposes merges per vault (D9) as a list; owner approves; applied with `val3 index merge`.
3. `val3 init` in both adopters (keeps their hooks; writes the block). The second adopter's instruction to use
   another memory method stays for the owner to resolve there.
4. Watch `val3 status` and the prompt log for `val3 index`/`show` use.
