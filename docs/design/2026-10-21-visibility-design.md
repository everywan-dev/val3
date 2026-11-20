# VAL3 — visibility: an index that fits, a controlled vocabulary, an indicator and `val3 init`

Date: 2026-10-21. Status: **accepted by the owner on 2026-10-21**. Replaces TODO item 1 (why the agent does not look up indexed
titles) and absorbs TODO item 3 (`val3 init`).

## 1. Problem

TODO item 1 assumed the agent sees a title in the session index and does not look the entry up. The one
measured case does not show that: in the replay of 2026-10-14 the only avoided rediscovery was entry #30 of the
first adopter, and in that session the agent ran `val3 show` on it, then re-read the code on purpose and said
so ("I checked again in the code what entry #30 says"). The entry's evidence was a quote of another session's
reply and the question was about code that may have moved; re-checking was reasonable. No measured case of
"saw the title, did not look it up" remains.

What the measurement found instead (2026-10-21):

- **The index no longer fits.** The first adopter's session index is 9,922 characters against the 10,000 cap
  (`hooks.LIMIT`); 21 live entries are not shown, only "21 more entries not shown". Lines are dropped in
  section order, which is alphabetical, so every section after the letter *f* disappears whole (decisions about
  scope, a host alias, the development replica). It grows with every entry. The second adopter is at 7,107.
- **Sections follow spelling, not topic.** 51 knowledge indexes for 102 entries; each entry is listed under the
  alphabetically first of its indexes (`views.session_index`, `min(k.name)`), so the project's own name (44
  entries) and synonyms split the list: `facturas`/`facturacion`, `decisiones`/`decisions`,
  `herramientas`/`tooling`/`tools`, `coste`/`costes`. The extractor is told only "short lowercase names such
  as flows, modules, billing"; it never sees the names already in use.
- **Nobody can see VAL3 working.** Hook context reaches the model, not the person. `val3 status` lists what was
  injected, after the fact.
- **The project's instructions never mention VAL3.** Neither adopter's `CLAUDE.md`/`AGENTS.md` says VAL3
  exists; the only description is the three header lines of the session index. The second adopter's
  `CLAUDE.md` tells the agent to use another memory method. Setting up a project is manual (README §Project,
  §Claude Code hooks).
- Use by the agent: first adopter, 6 of 12 sessions ran a `val3` command (17 `show`, 1 `recall`); second
  adopter (delivery only), 1 of 39.

## 2. Owner's rulings (2026-10-21)

| # | Ruling |
|---|---|
| V1 | The hooks stay as they are; nothing new is forced on the agent. |
| V2 | The person sees on screen when VAL3 delivers something. |
| V3 | Deployment writes into the project's `CLAUDE.md` that VAL3 exists and how to use it, as one more channel even if not deterministic. |
| V4 | `val3 init` and the `CLAUDE.md` block are built together, in this work. |
| V5 | Vocabulary: the architect's call (a new index name is not blocked; see D7). |

## 3. Decisions

Decisions marked *(architect)* are the architect's call under V5 or within the rulings; the owner may overturn
any of them.

### Session index

| # | Decision |
|---|---|
| D1 | Each entry is listed once, under its **section**: of its indexes after alias resolution (D6), excluding an index named like a vault of the project or like any project that wrote to the vault (`entry.origin_project`; `_` and `-` treated alike), so a shared home vault does not show another project's name as a section, the one with the most live entries in the vault; ties by name. No index left: `General`. Tools stay under `Tools`. Sections group by resolved name across the home and linked vaults; each vault's aliases apply to its own entries only. *(architect: largest index gives fewer, broader sections; the alphabetical rule is what split the list. On the first adopter no entry has the project's name as its only index, so `General` stays small.)* |
| D2 | When every line fits under the cap, the index is as today apart from D1. When it does not, `Tools` and the re-check header are always listed in full (unless `Tools` alone exceed the cap; then tool lines are dropped from the end and counted in the not-shown line, amended after the plan challenge); then sections are **collapsed** largest first until the text fits. A collapsed section is one line: `## facturas — 24 entries (2 to re-check): val3 index facturas`; a collapsed `General` points to `val3 index --none`. No entry disappears silently; the "N more entries not shown" line remains only as a last resort if collapsed lines alone overflow. *(architect)* |
| D3 | The header asks for the lookup in imperative form: before working on a topic listed here, read its entries (`val3 show <ref>`, `val3 index <name>` for a collapsed section). *(architect; wording left to the builder, kept to the current three or four lines.)* |
| D4 | The cap stays at `hooks.LIMIT` (10,000, the harness cap for hook context). |

### Vocabulary

| # | Decision |
|---|---|
| D5 | The knowledge indexes of a vault are its vocabulary. The extractor prompt carries the home vault's current index names with their live counts and says: reuse one; a new name only when none fits; never the project's name. `val3 save --help` stays static (no connection) and says the same rule, pointing to `val3 index` for the list; `val3 save` reports when it created an index name that did not exist. *(architect)* |
| D6 | **Merge** without changing history: a new append-only table `index_alias (name text PRIMARY KEY, target_id → knowledge_index, created_at)`. `val3 index merge <from> <into>` inserts the alias. Every read (session index, `val3 index`, status) resolves an entry's indexes through it; every write stores an alias name as its target. Merging into a name that is already an alias stores its final target; merging a name that is already an alias is refused (it is merged); merging an index that other aliases point to is allowed, and reads resolve the chain (a recursive query; cycles cannot form because each alias name is inserted once and always points at a name that was not an alias then). A merge is final, like a withdrawal. *(architect)* Added to new vaults by `vault.sql` and to existing ones by `val3 vault upgrade`; reads tolerate its absence as they tolerate a missing `withdrawal`. *(architect: `knowledge_index` and `entry_index` are append-only, so a rename cannot rewrite them.)* |
| D7 | A new index name is **not blocked** and goes to no review. `val3 status` shows the index count and names the indexes with a single live entry, the fragmentation signal, so a person or the agent merges them. *(architect: blocking would slow capture, and a person's approval is not a reliable check, owner 2026-10-14.)* |
| D8 | `val3 index` lists the home and linked vaults' indexes with live counts; `val3 index <name>` lists the live entries of one index after alias resolution (ref, kind, title, re-check mark), as the session index would show them; `val3 index --none` lists the live entries with no section index (the `General` section). |
| D9 | First cleanup: the architect proposes merges for both development vaults as a list; the owner approves; they are applied with `val3 index merge`. |

### Indicator

| # | Decision |
|---|---|
| D10 | The hooks add a message for the person next to the context for the model: at session start `VAL3: index of 102 entries (2 to re-check, 3 sections collapsed)`; on a typed prompt with deliveries `VAL3: erp#72, erp#56`. Nothing when a prompt delivers nothing. Warnings that today go only to the model are shown to the person too. Mechanism: the hook output's top-level `systemMessage`, next to `hookSpecificOutput.additionalContext`. Checked 2026-10-21 with `claude -p --output-format stream-json` and a test hook: on UserPromptSubmit the person gets it ("UserPromptSubmit says: …") and the model does not, while the model still gets `additionalContext`; on SessionStart `-p` mode surfaces nothing. In an interactive session (Claude Code 2.1.306, checked 2026-10-21 through tmux) both show under the prompt: "SessionStart:startup says: …" and "UserPromptSubmit says: …". |

### `val3 init`

| # | Decision |
|---|---|
| D11 | `val3 init --service <entry> --vault <home> [--linked <vault> …] [--project <name>]` run at the project root. Every step is idempotent, so re-running it is how an existing adopter is updated. |
| D12 | `val3.toml`: written when absent; when present it is read and left untouched (the flags must then be absent or match, otherwise exit 2). |
| D13 | Hooks: if `.claude/settings.json` or `.claude/settings.local.json` already holds any `val3 hook` command, hooks are left as they are and reported (a delivery-only adopter keeps its setup). Otherwise the four hooks of README §Claude Code hooks and `autoDreamEnabled: false` are merged into `.claude/settings.json`, keeping every other key and hook. A file that is not valid JSON is not touched: exit 1 naming it. |
| D14 | `CLAUDE.md` block between `<!-- val3:begin -->` and `<!-- val3:end -->`: replaced in place when present, appended otherwise; the file is created when missing. Nothing outside the markers is changed. The block is a constant shipped with the package (like `extractor_prompt.md`), in English, about fifteen lines: what VAL3 is; that the index and per-prompt entries are verified and injected automatically; look up a listed topic before working on it; `val3 save` for a verified fact, preference, decision or recipe; `val3 confirm` / `val3 withdraw` for an entry marked to re-check; `val3 --help`. |
| D15 | Connection check after writing: the service connects, the home and linked vaults exist and are at the current schema (else name `val3 vault upgrade`), the embedding service answers when configured. Init creates no vault. A failed check exits 1 after the files are written; re-running is safe. |
| D16 | Output: one line per step with what was written, kept or failed. |

## 4. Non-goals

- No new forcing hook, no change to what the hooks inject per prompt (V1).
- No change to the harness's own memory.
- Not resolving the second adopter's instruction to use another memory method: it is outside the VAL3 block;
  the owner edits that project.
- No automatic merging of indexes by similarity.

## 5. Rollout

1. Owner runs `val3 vault upgrade` on both development vaults (adds `index_alias`).
2. Architect proposes the merges (D9); owner approves; applied.
3. `val3 init` re-run in both adopters: writes the block, keeps their hooks.
4. Watch with `val3 status` and the prompt log whether the agent runs `val3 index`/`show` more often.

## 6. Left to the builder

Exact wording of the header, the `CLAUDE.md` block and the indicator lines; module layout; how `val3 index`
formats its lists; test fixtures. Tests drive the CLI as a subprocess, as the rest of the suite.
