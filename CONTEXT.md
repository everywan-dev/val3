# VAL3 vocabulary

- **Vault** — one PostgreSQL schema holding entries. A project has **own** vaults (written) and any number
  of **linked** vaults (read-only). Several projects may share a home vault.
- **Own vault** — a vault a project writes: its **home** vault (the default target) and its specialized
  vaults. A project writes only to own vaults.
- **Specialized vault** — an own vault with a purpose, listed in `[vaults] specialized` of `val3.toml`, its
  parent the home vault; written with `--vault <name>` (`val3 save`, `tool add`, `review`, `embed`,
  `index merge`) or by capture when a candidate names it.
- **Vault note** — an append-only row of `val3_common.vault_note` with a vault's description and parent and
  the role that wrote it; the latest row holds. Only a role that can write a vault describes it;
  `val3 vault list` shows them.
- **Vault identity** — the fixed id a vault gets at creation (`<vault>.identity`); a mirror keeps its
  source's, so its refs mean the same thing.
- **Mirror** — a read-only copy of a vault of another database, loaded and refreshed by `val3 vault mirror`
  from a `val3 vault dump`; its `entry` table carries the `val3_common.mirror_guard()` trigger. A refresh is
  refused unless the dump has the same identity and every row the mirror already holds, unchanged, and it
  keeps the mirror's grants exactly.
- **Entry** — a row in a vault, referenced as `<vault>#<id>`. Kinds: fact, recipe, preference,
  decision, tool.
- **Fact** — a single statement validated in a session against a database, a program, an API, a
  test or the user, stored with its evidence. Trusted once admitted.
- **Evidence** — the reproduction (query, command, tool call) plus the literal output quoted from
  the transcript, or the user's literal confirmation; for an imported entry, a quote checked in a
  repository file at a commit (`file`).
- **Recipe** — the correct path to answer a kind of question: declared parameters, ordered steps,
  the facts each step uses, and worked examples.
- **Tool** — a registered external skill, MCP server or CLI that answers derivable questions;
  preferred over stored facts for those.
- **Knowledge index** — a named grouping inside a vault (e.g. flows, modules); entries belong to
  many. A vault's index names are its vocabulary: writers reuse one and add a new name only when none
  fits; `val3 index` lists them with live counts.
- **Index alias** — a row of the append-only `index_alias` table written by `val3 index merge <from> <into>`:
  the name `from` resolves to `into` on every read and write; history is not rewritten. A **merge** is final.
- **Section** — the heading an entry is listed under in the session index: of its indexes after aliases,
  the one with the most live entries in the vault (ties by name), leaving out names of the project's vaults
  and of projects that wrote to the vault; `General` when none is left, `Tools` for tools. A **collapsed**
  section is one line naming its count and `val3 index <name>`.
- **Inbox** — the harness's native auto-memory directory; VAL3 reads it, never owns it.
- **Candidate** — what the extractor or `val3 save` proposes; becomes an entry only through the
  writer.
- **Writer** — the single code path that changes a vault.
- **Supersession** — a new entry replacing an old one; the old one is kept and linked. One entry may replace
  several (`supersedes` takes a list); a replaced entry leaves delivery and stays readable.
- **Review list** — what a machine cannot decide and a person does: a tool or a recipe without
  evidence (`no-evidence`), an import item whose quotes did not all verify, a withdrawal proposal. A candidate whose
  evidence checks is admitted at once, whatever its kind.
- **Mechanical decision** — the `review_decision` an import writes itself, decided by `val3 (mechanical)`,
  when every quote of an import item verified: the item is admitted and keeps its review record.
- **Queue entry** — one file per captured `PreCompact`/`SessionEnd`; removed once its extraction
  committed.
- **Drainer** — the single process per machine that extracts queued sessions.
- **Watermark** — per session, the number of transcript lines already extracted.
- **Withdrawal** — an entry taken out of delivery for good, with a cause (`wrong`: never true;
  `obsolete`: true once, no longer needed) and a reason; never undone. Inside the agent it takes
  effect at once when it carries evidence that checks; otherwise it is a proposal on the review list that a
  person approves. `duplicate` is the cause of an entry dropped by `val3 settle --drop`.
- **Current evidence** — an entry's evidence rows with the latest `created_at`; a confirmation replaces
  it as the baseline.
- **Confirmation** — `val3 confirm`: the entry still holds; a new current evidence set is appended
  (`file` quotes checked at HEAD, other quotes against the transcript).
- **To re-check** — the mark on a live home-vault entry whose current `file` quote is no longer in
  `HEAD:<path>`, or on a live recipe citing a fact or tool that was superseded or withdrawn; computed when
  read, never stored. Quotes whose commit is not in the repository are not checked.
- **Neighbour** — a live entry of the home vault, of the same kind, whose vector in the same embedding space
  reaches `[embedding] neighbour` (default 0.70) against an entry whose vector was just stored; the pair is
  recorded once, the newer entry first, when the later of the two vectors is stored.
- **Exempt** — an entry that existed when its vault was upgraded to the neighbour check, or was admitted while
  the project had no `[embedding]`; two exempt entries are never paired.
- **To settle** — the mark on the newer entry of a neighbour pair while both are live and the pair is not
  settled. `val3 settle <ref> <near>` settles it: `--replace` (the new entry supersedes the neighbour),
  `--drop` (the new entry is withdrawn as a `duplicate`) or `--keep` (both hold; never marked again).
- **Repeat** — a capture candidate that a second extractor call judged to add nothing to one neighbour (each of
  its assertions follows from that neighbour; a narrower restatement is a repeat): not admitted, logged as event `repeat` on the neighbour with the candidate. Unlike a **duplicate** (same kind, title
  and statement as a stored entry), a repeat differs in text. The same call may instead answer `new` or
  `replaces`; the pairs it compared are settled by `extractor <session>`, and an undecided one stays *to
  settle*. A neighbour admitted or confirmed after the slice's last record is never replaced.
- **Live** — an entry that is neither superseded nor withdrawn; search, delivery and the session index
  read only live entries.
- **Session index** — the list of every live entry injected at session start, one line each, grouped by
  section; over the 10,000-character cap, sections collapse largest first.
- **Embedding** — the vector of an entry's title and statement in one embedding space, stored in the
  vault's `embedding` table; computed by the local embedding service.
- **Embedding space** — model name, model digest, number of dimensions and version of the embedded
  text; only vectors of the same space are compared.
- **Floor** — the minimum cosine similarity an entry needs to be injected for a prompt.
- **Needed entry** — an entry without which the agent would act against a project decision, ask
  again for something settled, or redo work. Entries on the same topic that change nothing are noise.
- **Prompt log** — the local file with one line per typed prompt and what was injected.
- **Labelled set** — prompts with the entries needed for each (`[]` when none was, `null` while not
  labelled), read by `val3 eval`; one set calibrates the floor, another from other sessions confirms it.
- **Import** — `val3 import`: a validated-memory 2.x adopter's units and notes, checked against its
  repository at one pinned commit; an item whose quotes all verified is admitted with a mechanical
  decision, the rest go to the review list; a **manifest** holds a dry run's exact candidates, a **batch** id
  marks what one apply admitted or queued. Files named like secrets (`.env*`, `*.pem`,
  `*.key`, `id_*`, `*secret*`, `*.p12`), or under a directory named that way, are never read.
- **`val3 init`** — sets a project up at its root: `val3.toml`, the four hooks (unless a `val3 hook` is
  already configured) and the block between `<!-- val3:begin -->` and `<!-- val3:end -->` in `CLAUDE.md`,
  then checks the connection; idempotent, re-run to update a project.
- **Memory tool** — `memory_search`, `memory_read` or `memory_save`, the tools `val3 mcp` serves to the agent
  over the project's configured vaults: two read, one saves as `val3 save` does. Their results are not evidence.
- **Call id** — the tool-use id Claude Code sends with a tool call (`_meta["claudecode/toolUseId"]`); the
  transcript whose assistant record holds it is the one `memory_save` checks evidence against.
- **Partial notice** — the sentence every delivery of memory carries: the memory is partial and may be out of
  date, it complements the project's documents and does not replace them.
- **Delivery switch** — `[delivery] index` or `[delivery] prompt` in `val3.toml`: whether the session index and
  prompt injection are injected. Session upkeep and capture do not depend on them.
- **Held-out set** — questions written before a build is measured and never used to tune its wording; the only
  sets an acceptance decision rests on.
