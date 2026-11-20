# VAL3

Session memory for coding agents. A Claude Code session learns things that the next session has to find out
again: how a report is produced, which table holds a figure, what the owner decided. VAL3 keeps those as entries
(facts, recipes, preferences, decisions, tools), each with the evidence that produced it (the command and the
output quoted from the session), and marks the ones without evidence. Entries are captured from session
transcripts by harness hooks, stored in PostgreSQL vaults that projects and machines can share, and delivered
back to the agent through memory tools to search and save, and, when switched on, an index at session start and
matching entries on each prompt.

It is for people who run Claude Code on long-lived projects and can operate a PostgreSQL server.

**Status: experimental.** Used on one owner's projects: 11 vaults on two machines, 2 023 entries (2026-11-20,
`val3 vault list`). What has been measured, not only built:

- When an agent went looking for something by itself, the vault already held what it then found in **3 of 320**
  knowledge episodes (something related in 91); written with hindsight, the query found it in 4 of 317. What
  sessions look for by themselves is mostly not what was captured. Source:
  `docs/research/2026-11-15-what-sessions-looked-for.md`.
- Of the **365** consultations agents did make, **116** were useful, 133 partly and 116 not; 4 returned
  something wrong or out of date and none was acted upon (same source).
- Changing what capture keeps: seven arms on 12 session windows (the present prompt captured twice, a restricted
  prompt, a doubled call as control, a second pass, evidence not required, complete entries); by the rule fixed
  beforehand **no arm passes** (plan 16, stage 2: `docs/plans/2026-11-16-val3-plan-16-what-capture-keeps.md`,
  "Result of stage 2").

Built: plans 1 to 14, 3b and the code parts of 15 (store, writer, recall, delivery with semantic search, capture
hooks, backup, import from validated-memory 2.x, withdrawal, entry lifecycle, knowledge indexes, `val3 init`,
automatic admission, near duplicates settled, vault sharing, memory tools, usage log, vault view) and the
`memory_save` tool of plan 17, with one known defect (see Memory tools). Plans 16 and 18 are measurement and
design work. Design: `docs/design/2026-10-08-val3-design.md`; decisions in `docs/adr/`; what the documents are:
`docs/README.md`. This repository is published as one snapshot per version, without the development history.

## Requirements

- Linux and Python 3.11 or later with `venv` (on Debian and Ubuntu, `python3-venv`); the tests run on 3.11 to
  3.13; other systems are untested. Git.
- PostgreSQL 16 with the `vector` (pgvector) and `unaccent` extensions, for example the image
  `pgvector/pgvector:0.8.1-pg16`.
- Claude Code, for the hooks that capture and deliver and for the memory tools. Capture runs `claude -p` with
  the extractor model (`claude-haiku-5-5` by default), so it spends from the account Claude Code uses.
- Optional: an Ollama server with `bge-m3` for semantic search (see Semantic search); without it, search is
  full-text only.

## Quick start

Needs Git, Python with `venv`, and Docker usable by your user. A disposable database in Docker, one vault, one entry saved and found again. The `postgres` superuser is used
here for brevity; Server setup below gives each project its own role.

```bash
docker run -d --name val3-quickstart -p 127.0.0.1:5434:5432 -e POSTGRES_PASSWORD=quickstart \
  -e POSTGRES_DB=val3 pgvector/pgvector:0.8.1-pg16
until docker exec val3-quickstart pg_isready -q -h 127.0.0.1 -U postgres -d val3; do sleep 1; done

git clone https://github.com/everywan-dev/val3
python3 -m venv val3-venv && val3-venv/bin/pip install ./val3
export PATH="$PWD/val3-venv/bin:$PATH"

printf '[val3]\nhost=127.0.0.1\nport=5434\ndbname=val3\nuser=postgres\n' >> ~/.pg_service.conf
echo '127.0.0.1:5434:*:postgres:quickstart' >> ~/.pgpass && chmod 600 ~/.pgpass

val3 vault create demo --service val3

mkdir demo-project && cd demo-project
val3 init --service val3 --vault demo

echo '{"kind": "fact", "title": "Invoices close on the 5th",
       "statement": "The monthly invoice run closes on the 5th of each month.",
       "keywords": ["invoices", "closing"]}' | val3 save
val3 recall invoices closing
val3 show demo#1
```

Optional, search by meaning: a local Ollama with the `bge-m3` model (about 1.2 GB to download), on its own port
here so that it does not meet another Ollama:

```bash
docker run -d --name val3-quickstart-ollama -p 127.0.0.1:11435:11434 ollama/ollama:0.34.2
until docker exec val3-quickstart-ollama ollama list >/dev/null 2>&1; do sleep 1; done
docker exec val3-quickstart-ollama ollama pull bge-m3
printf '[embedding]\nurl = "http://127.0.0.1:11435"\n' >> val3.toml
val3 embed
val3 recall facturas cierre mensual
```

`val3 embed` stores the vector of the entry saved before the service was configured (`embedded 1 entries`); from
now on vectors are stored on admission. The Spanish query shares no word with the English entry, so full-text
search alone answers `no matches`; by meaning it finds `demo#1`. Hits below the similarity floor (0.60 by
default, see Semantic search) are not listed, so a query of two or three loose words may find nothing.

`val3 save` answers `admitted unverified demo#1`: the entry has no evidence (outside a session there is no
transcript to check a quote against), so it is admitted with less authority. `val3 recall` lists it and
`val3 show` prints it. `val3 init` also wrote the Claude Code hooks and the read-only memory tools' permissions
to `.claude/settings.json`, the memory tools' server to `.mcp.json` and a VAL3 block to `CLAUDE.md`: a Claude
Code session opened in `demo-project` (approve the `val3` server once) can search and save with the memory tools,
and what it learns is captured when it compacts or ends. `init` writes `[delivery] index = false` and
`prompt = false` in `val3.toml` (the tools only); set them to `true` for the index at session start and the
entries on each prompt (see Memory tools). To undo it: `docker rm -f val3-quickstart val3-quickstart-ollama`,
the `[val3]` lines in `~/.pg_service.conf` and `~/.pgpass`, and the directories `val3`, `val3-venv` and
`demo-project`.

The sections below are the reference.

## Install

Python 3.11 or later:

```bash
pip install -e .
```

## Server setup (operator)

1. Create a database named `val3` on a PostgreSQL 16 server with the pgvector extension available
   (for example the image `pgvector/pgvector:0.8.1-pg16`).
2. Create each vault with a role allowed to run `CREATE EXTENSION unaccent` and
   `CREATE EXTENSION vector`: `val3 vault create <vault> --service <service>`.
3. Create one login role per project or machine, then grant it access:
   `val3 vault grant <vault> <role> --write` for its home vault, `--read` for linked vaults.
   Vault tables are append-only: no role can update or delete rows.
4. Vaults created before semantic search, withdrawal, index merges or the neighbour check need their tables:
   `val3 vault upgrade <vault> --service <service>` (idempotent; roles keep their access).

## Connection

VAL3 connects through a `pg_service.conf` service name. Keep the password in `~/.pgpass` or a
secret manager, never in the project:

```ini
# ~/.pg_service.conf
[val3]
host=db.example.org
port=5432
dbname=val3
user=my_project
```

## Project

At the project root:

```bash
val3 init --service val3 --vault erp [--linked shared]... [--project my-project]
```

`val3 init` writes `val3.toml` (when absent), the Claude Code hooks (see Claude Code hooks) and a VAL3 block in
`CLAUDE.md`, then checks the connection: the service connects, the vaults exist and are at the current schema
(otherwise it names `val3 vault upgrade`), and the embedding service answers when configured. It prints one
line per step (`val3.toml: written`, `hooks: kept (...)`, `CLAUDE.md: block replaced`, `connection: ok`).
Every step is idempotent: re-running it is how an existing project is updated. It creates no vault and
writes nothing outside the project directory. A failed connection check exits 1 after the files are
written; re-running is safe.

- `val3.toml` present: read and left untouched; flags given must match it, otherwise exit 2.
- Hooks: if `.claude/settings.json` or `.claude/settings.local.json` already holds any `val3 hook` command,
  they are left as they are (`hooks: kept (...)`), so a delivery-only setup stays. Otherwise the four hooks
  and `autoDreamEnabled: false` are merged into `.claude/settings.json`, keeping every other key and hook. A
  file that is not valid JSON is not touched: exit 1 naming it.
- `CLAUDE.md`: the block between `<!-- val3:begin -->` and `<!-- val3:end -->` is replaced in place when
  present and appended otherwise; the file is created when missing. Nothing outside the markers changes. The
  block says what VAL3 is and which commands to use (`show`, `index`, `recall`, `save`, `confirm`,
  `withdraw`, `settle`); it informs the agent, it does not force anything. For an install kept out of the repository
  (`val3.toml` ignored, hooks in `.claude/settings.local.json`), move the block to `CLAUDE.local.md`: once that
  file holds the markers, init updates the block there and leaves `CLAUDE.md` alone.

What init writes in `val3.toml`, for reference (committed with the project):

```toml
service = "val3"          # pg_service.conf entry; no secrets here
project = "my-project"    # optional; default: the directory holding val3.toml
[vaults]
home = "erp"              # written
specialized = ["erp_billing"]  # own vaults with a purpose, written with --vault; optional
linked = ["shared"]       # read-only; optional
[extractor]
model = "claude-haiku-5-5"   # optional; the model of the detached extractor
focus = true                 # optional, off by default: capture proposes only decisions, procedures
                             # that worked, pitfalls and accepted exceptions, not measurements or states
[delivery]                # written by init: the tools only; without this table both are on
index = false
prompt = false
```

## Semantic search (optional)

VAL3 can find entries by meaning with a local embedding service; no text leaves your network.
Run Ollama next to the PostgreSQL server (add `--gpus all` on a host with an NVIDIA GPU and the
NVIDIA Container Toolkit) and pull the model:

```bash
docker run -d --name val3-ollama --restart unless-stopped -p 127.0.0.1:11434:11434 \
  -v val3-ollama:/root/.ollama ollama/ollama:0.34.2
docker exec val3-ollama ollama pull bge-m3
```

Then add to `val3.toml`:

```toml
[embedding]
url = "http://127.0.0.1:11434"   # optional
model = "bge-m3"                 # optional
floor = 0.60                     # optional; minimum cosine similarity for per-prompt delivery
neighbour = 0.70                 # optional; minimum cosine similarity of a close neighbour, or false
```

Vectors are stored when an entry is admitted. If the service is down, the entry is admitted
anyway, is found by full-text search meanwhile, and `val3 embed` stores the missing vectors later
(the drainer also runs it after each extraction). Vectors are kept per model build: after pulling a
new build of the model, run `val3 embed` once. Without an `[embedding]` table, VAL3 uses full-text
search and runs no neighbour check (see Settling near duplicates).

## Claude Code hooks

`val3 init` adds them; for reference, in the project's `.claude/settings.json`:

```json
{
  "autoDreamEnabled": false,
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "val3 hook session-start"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "val3 hook prompt"}]}],
    "PreCompact": [{"hooks": [{"type": "command", "command": "val3 hook capture"}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "val3 hook capture"}]}]
  }
}
```

`session-start` injects the index of every live entry (one line each, grouped by section),
exports `VAL3_SESSION_ID` and `VAL3_TRANSCRIPT`, and loads the embedding model. `prompt` injects at
most three entries above the floor for each typed prompt; a prompt of six words or fewer is read
both alone and together with the end of the previous answer, and each entry keeps the better score; harness turns (subagent reports, notifications)
receive nothing. If the embedding service does not answer within a second, entries are found by
full-text search and the session is warned once. Each typed prompt is logged with what was injected
in `~/.local/state/val3/prompts/<project>.jsonl` (readable only by you, kept 30 days). Both
stay silent outside projects with a `val3.toml` and never block the session: if the server is
unreachable they warn once per session.

The person sees a `VAL3:` line under the prompt (the hook output's `systemMessage`; the model does not get
it): at session start `VAL3: index of 102 entries · 2 to re-check · 1 to settle · 3 sections collapsed`, and on a typed
prompt that delivered entries `VAL3: erp#72, erp#56`; nothing when a prompt delivers nothing. Warnings sent to
the model are shown to the person too.

`capture` queues the session (a file under `~/.local/state/val3/queue/`) and starts a detached
drainer. The drainer sends the session's unseen transcript lines and the memory notes it wrote to
an isolated `claude -p` (no tools, hooks, MCP servers or settings), submits each candidate through
the writer, and appends to each note that produced entries one line with their references, keeping the
note's body (a note may hold more than its entries do). With `[embedding]`, a candidate close to stored
entries goes through a second extractor call first (see Settling near duplicates). `val3 extract` prints
`<transcript>: admitted <a>, duplicate <d>, repeat <r>, [verified <v>, ]queued <q>, already queued <k>, previously
rejected <p>, rejected <x>, unverified <u>, quote-failed <f>; <n> extractor calls, $<cost>` (`verified` only when
not zero; `unverified` counts the admissions without evidence or attestation, `quote-failed` the candidates that
lost their evidence to a failed quote; `<n>` counts both calls; `nothing new` when there was nothing to extract).
On a vault upgraded for ADR 0012 a candidate with a quote that fails its check (not in the transcript, only in the
input of its call, or only in text the memory itself wrote) is not rejected: nobody reads the failure, so it goes on
without any of its evidence, unverified, valid quotes included. The vault logs a `discarded` event for it, and the
`admitted` event's detail holds `failed_quotes`, a count, never the quotes. A malformed candidate, a missing
transcript and a database error are still rejected, and so is a failed quote on a vault not upgraded. Sessions
left in the queue (server down, extractor error) are retried at the next session start; after 3 failed extractions
an entry moves to `queue/failed/`. `autoDreamEnabled: false` stops the harness from consolidating
the notes VAL3 reads.

Capture remembers which transcript records it has processed for each project, in
`~/.local/state/val3/processed/<project>.tsv` (one line per record: date, then its `uuid`, or a hash of the line
for records without one). A resumed session gets a new session id and a transcript that starts with the earlier
session's records; capture starts after that leading run of records already processed, so they are not sent
again (the drainer's line in `extract.log` counts them as `skipped`). The same rule applies after a session's own
cursor (the earlier session captured after the one that resumed it sends nothing twice), and to a transcript
rewritten under the same session id, which is read again from its start. Records are forgotten
`remember_days` after they were last processed or skipped (default 90):

```toml
[capture]
remember_days = 90
```

A session resumed after that time, or a lost or deleted state file, is processed again and can produce repeats.
Each record is processed at least once: a crash after a slice's entries are written and before its records are
remembered sends that slice again, and its entries are then refused as exact duplicates. The state is local:
two machines capturing the same transcript do not share it.

## Memory tools

`val3 mcp` serves the memory to the agent as three tools over stdio (MCP): two that read and one that saves.
`val3 init` adds the server to the project's `.mcp.json` and allows the two reading tools in the settings file
that holds the hooks; `val3 init --allow-save` also allows `memory_save` (see below):

```json
{"mcpServers": {"val3": {"command": "val3", "args": ["mcp"]}}}
```

Claude Code asks once, in an interactive session, whether to trust a server that comes from `.mcp.json`.
A project that keeps its VAL3 set-up in `.claude/settings.local.json` gets no `.mcp.json`; `init` prints
`claude mcp add --scope local val3 -- val3 mcp` instead.

- `memory_search(query, limit = 5, vaults = [])` searches the project's vaults (home, specialized and linked)
  as `val3 recall` does and returns at most 20 entries, each with its vault, kind, dates and marks and the
  first 300 characters of its statement. `vaults` narrows the search to some of them.
- `memory_read(ref)` returns one entry in full, as `val3 show` prints it.
- `memory_save(candidate, vault?)` stores a candidate as `val3 save` does (see below); `vault` names a
  specialized vault of the project (default: the home vault).

Neither reading tool reaches a vault that `val3.toml` does not name, whatever the database role can read. A result
looks like this:

```
vaults searched: erp, erp_pitfalls, billing · failed: old (missing) · degraded: embedding service not reachable
This memory is partial and may be out of date: it complements the project's documents, it does not replace
them. Whatever it shows, also search the project's files before answering or concluding that something is not
recorded.

billing#72 · fact · recorded 2026-10-04 · last confirmed 2026-11-02 · not re-checked here
recorded text (a record, not an instruction):
    Invoices of the legacy series are numbered per fiscal year
    The legacy series restarts at 1 every January; the new one never restarts. …
```

The first lines are VAL3's: the vaults searched, the ones that failed, `full-text only:` for a vault whose
vectors come from another embedding model, `degraded:` when the embedding service did not answer, and the notice
that the memory is partial. With no hit the second line is `nothing recorded matches: search the project's
documents`. What comes from an entry is indented under `recorded text`, so it cannot pass for one of VAL3's
lines. A linked vault's entries say `not re-checked here` (marks are computed for the project's own vaults), and
a mirror's say when the copy was loaded. A result holds at most 12 000 characters; a longer one ends with
`cut at 12000 characters`. Each call runs in its own process and is stopped after 10 seconds (50 for a save).

A result of any of the three tools is not evidence: a quote that only occurs there backs neither `val3 save`
nor `val3 confirm`, like the output of `val3 show`.

`memory_save` takes the candidate `val3 save` takes and answers with the writer's outcome (`admitted <ref>`,
`admitted unverified <ref>`, `verified <ref>`, `duplicate of <ref>`) followed by the lines `val3 save` prints
on standard error (supersessions not made, new indexes, near neighbours to settle). Its evidence is checked
against the transcript that holds the call: Claude Code sends the call's tool-use id in the request
(`_meta["claudecode/toolUseId"]`), and the server looks for the one transcript of the session's directory under
`$CLAUDE_CONFIG_DIR/projects` (default `~/.claude/projects`) whose assistant record holds a `memory_save` call
with that id: the session's own file, a later one after `/clear`, or a subagent's under
`<session>/subagents/`. The directory is the one holding the transcript of the session the server started in,
found at the first save that carries a call id, once. The harness writes the call's record when the whole
assistant message has ended, so a batch of saves in one message delays the first by the rest of the message;
the server waits up to 30 seconds for it, among the transcripts written in the last ten minutes, and a call id
is accepted only as letters, digits, `_` and `-`. The entry records the session of that file, and its event records `via: "tool"` and, for
a subagent, `agent`. A subagent's prompt is its parent's text, not the user's words: it is not `user` evidence.

A candidate with evidence and no usable transcript is not saved, with one of three errors: the client sent no
call id; no transcript of the project holds the call yet (it may be late: send the same candidate again); the
id is in two of the recently written transcripts. A quote that fails its check is the writer's `discarded` error. Each error says that
the candidate can be sent again unchanged, or without its evidence to be stored unverified; a candidate without
evidence needs no transcript. The answer of a save comes as soon as the entry is admitted, before its vectors:
a call stopped after the admission (at its deadline of 50 seconds, or killed) answers `admitted <ref>; vectors
pending` with the supersessions not made and the new indexes, and `val3 embed` or the drainer stores the
vectors. A request line holds at most 65 536 bytes, so a candidate stays
under about 48 000 characters of ASCII text (fewer otherwise); a larger one goes through `val3 save`.

Saving with the tool asks the person each time unless `mcp__val3__memory_save` is in `permissions.allow`;
`val3 init --allow-save` adds it, and `val3 init` never adds or removes it otherwise, so the choice survives a
re-run. Under the harness's automatic permission mode the harness decides.

**Known defect (Claude Code 2.1.325): do not pre-allow the tool for interactive sessions yet.** In an
interactive session where `mcp__val3__memory_save` is in `permissions.allow`, the harness writes the call's
record to the transcript only after the call has returned, so a save with evidence waits 30 s and answers "no
transcript". Without the pre-allowed permission (the person approves, or the automatic mode decides) and in
headless runs the record is on disk while the call runs and the save works. Until evidence is checked after
the call, leave the tool out of `permissions.allow`.

What the hooks inject is switched per project in `val3.toml`:

```toml
[delivery]
index = true     # the index at session start; false leaves one line naming the tools and the partial notice
prompt = true    # up to three entries on each typed prompt; false only logs the prompt
```

Session export, the drainer, the embedding warm-up and capture do not depend on these switches.
`val3 status` reports `memory tools: configured, answering`, `configured, not answering (<reason>)`,
`configured with another command (not run)`, `local set-up (see "claude mcp list")` or `not configured`. It
starts this installation's own server for the handshake, never a command read from `.mcp.json`.

## Vault view

`val3 view [--port N] [--no-open]`, run in a project directory, serves a read-only web page of the project's
vaults (home, specialized and linked) until Ctrl-C. It shows what the vaults hold by index, kind and state,
what each day and each session added and who wrote it (capture, the agent's save, an import, a tool registered
by a person; "not recorded" for entries admitted before VAL3 recorded it), what was superseded or withdrawn and
why, the entries without evidence, the exact names entries share, a map of the most recent entries by closeness
of their vectors, the extractor cost recorded on this machine per session, and how often searches on this
machine returned each entry. Each entry opens with its evidence, relations and history. It writes nothing.

It prints the address to open, `http://127.0.0.1:<port>/<token>/`, and opens it unless `--no-open` is given.
The server listens on 127.0.0.1 only, on any free port unless `--port` names one. The token is 32 random bytes,
new on every run and gone when the command ends; without it every request is answered 404. A request whose
`Host` is not `127.0.0.1:<port>` or `localhost:<port>`, or whose `Origin` is another site, is refused, so a page
of another site cannot read the vaults through the browser. Only GET and HEAD are answered. Every request reads
the database through the project's service and role, in a read-only transaction with a 5-second statement
timeout; a vault that cannot be read is named on the page and left out of the figures.

On another host, forward the port and open the printed address on your machine:

```
ssh -L 8765:127.0.0.1:8765 host     # then, on the host: val3 view --port 8765 --no-open
```

The local port must be the one given to `--port`: the page is reached as `127.0.0.1:<port>`, and any other
`Host` is refused.

The token is the credential: whoever has the address reads what the project's database role reads, evidence
included. It does not protect against other processes of the same user on that machine, a compromised browser
or its extensions, or a copy of the address shared by mistake. VAL3 writes no export of what the page shows.

## Usage log

`val3 recall`, `val3 show`, `memory_search`, `memory_read` and `memory_save` append one line per call to
`~/.local/state/val3/usage/<project>.jsonl` (under `$XDG_STATE_HOME` when set): time, session id when known,
project and machine, the command or tool, a call id, the vaults read, the limit, the query of a search (cut to
500 characters) and its number of words, the refs returned and the outcome (`ok`, `empty`, `error`, `timeout`);
a save's line adds the writer's `status` (`admitted`, `verified`, `duplicate`, `discarded`, or `no-call-id`,
`no-transcript`, `ambiguous` when its evidence had no transcript) and the `ref` it saved to.
The file is readable by its owner only; do not search for a secret. Lines
older than 30 days are dropped. A failure to write the log never changes a command's output or exit code. The
vault view reads it for the "returned by a search" figures of the machine it runs on; entries never returned in
that window show "not seen", and on a machine without the log the figures show "not recorded". Entries the hooks
inject on a prompt are not in it.

## Knowledge indexes

Each entry belongs to one or more knowledge indexes (short lowercase names such as `billing` or `flows`).
The indexes of a vault are its vocabulary: the extractor sees the home vault's index names with their live
counts and is told to reuse one, to add a new name only when none fits, and never to use the project's name;
`val3 save --help` states the same rule. A new name is not blocked: `val3 save` and `val3 tool add` print
`val3: new knowledge index: <name> (existing: val3 index)` on stderr, and `val3 status` shows
`indexes: <n> · with one entry: …`, the indexes that fragment the list and are worth merging.

The session index lists each entry once, under its **section**: of its indexes after merges, the one with the
most live entries in the vault (ties by name). Index names like a vault of the project or a project that
wrote to the vault are never sections; an entry with no index left goes under `General`, tools under
`Tools`. When the whole list does not fit the 10,000-character cap, `Tools` and the re-check header stay in
full and sections collapse, largest first, into one line each until the text fits:

```
## billing — 24 entries (2 to re-check, 1 to settle): val3 index billing
```

A collapsed `General` points to `val3 index --none`. Only if the collapsed lines alone overflow, the last
ones are dropped and counted in `N more entries not shown`.

```bash
val3 index                        # every index of the home and linked vaults with its live entries
val3 index billing                # the live entries of one index, as the session index lists them
val3 index --none                 # the live entries with no section index (General)
val3 index merge facturacion billing
```

`val3 index merge <from> <into>` merges an index of the home vault into another: it records an alias
(`index_alias`, append-only), and every read and write resolves the alias, so history is not rewritten.
Merging into a name that is itself merged stores its final target; merging a name already merged is refused.
It prints `merged <from> into <into> (<n> entries)`. A merge is final, like a withdrawal. Vaults created
before this version need `val3 vault upgrade <vault>` before a merge; reading works without.

## Calibrating the floor

```bash
val3 eval --draft ~/.local/state/val3/prompts/<project>.jsonl > draft.jsonl
# split draft.jsonl by "session" into calibration.jsonl and validation.jsonl; in each, replace
# "relevant": null with the entries that were needed at that moment (without them the agent
# would have acted against a decision, asked again or redone work), or with [] when none was
val3 eval calibration.jsonl     # choose the floor; set it in val3.toml
val3 eval validation.jsonl      # confirm it: read the row marked *
```

`val3 eval` prints, for floors 0.40 to 0.80 and for full-text search, the entries injected, the
share of them that were needed, the prompts served and the prompts left silent, then the embedding
latency; the configured floor is marked `*`. It applies the same selection as the prompt hook and
reads the vault as it was at each prompt (`at`), so later entries do not change a labelled set. It
refuses a set with prompts still unlabelled (`null`). Labelled sets hold project text: keep them out
of public repositories.

## Commands

- `val3 init [--service S] [--vault V] [--linked V]... [--project NAME] [--allow-save]` — set the project up:
  `val3.toml`, hooks, the memory server, the `CLAUDE.md` block, then check the connection (see Project);
  `--allow-save` lets the agent call `memory_save` without asking (see Memory tools).
- `val3 view [--port N] [--no-open]` — serve a read-only web view of the project's vaults on 127.0.0.1 (see
  Vault view).
- `val3 status [--last N]` — whether VAL3 is active here: project, home vault counts (on a vault upgraded for
  ADR 0012, the backing of its live entries: `<n> with evidence, <n> attested, <n> unverified (<ref>, …)`;
  before, `<n> pending review`), entries to re-check
  (`to re-check: <n> (<ref>, …)`) and to settle (`to settle: <n> (<ref>, …)`), indexes, the supersessions and
  withdrawals of the last 7 days (`changes (7 days): <s> superseded (<ref>, …), <w> withdrawn (<ref>, …)`, or
  `none`; `val3 show <ref>` reads each), search mode, the val3 hooks in the harness settings, the capture queue
  and the last N logged prompts (default 5) with the entries injected. Read only; inside a Claude Code session,
  type `! val3 status`.
- `val3 save [FILE]` — submit a candidate (JSON, stdin by default); `val3 save --help` shows the format. A
  candidate whose evidence checks is admitted at once (`admitted <ref>`); a candidate of any kind without evidence
  is admitted marked unverified (`admitted unverified <ref>`, see Backing) on a vault upgraded for ADR 0012, and
  queued for review (`queued for review: review #<n> (no-evidence)`) on a vault not yet upgraded. A quote that
  does not check fails the command (`discarded: …`): correct it, or save again without evidence, knowingly. Saving
  the kind, title and statement of a live unverified fact, preference or decision with evidence that checks adds
  the evidence to it (`verified <ref>`). A tool quote checks only in the output of one call
  whose input does not contain it, so a command that prints its own argument (`echo`, `printf`) is no evidence:
  quote output the call did not already contain; a `user` quote checks in the person's typed messages. After an
  admission, each close neighbour is named on stderr (see Settling near duplicates).
- `val3 save --vault V` writes to a specialized vault instead of the home vault; `tool add`, `review`, `embed`
  and `index merge` take `--vault V` too.
- `val3 show <vault>#<id> [--service S]` — an entry of any vault this role can read with its evidence, steps
  and supersession links (`SUPERSEDED by <ref>`; `supersedes: <ref>, <ref>` names every entry it replaced).
- `val3 index [NAME | --none] [--vault V] [--service S]` — list the knowledge indexes with live counts, or the
  entries of one; `val3 index merge <from> <into>` merges two (see Knowledge indexes).
- `val3 recall <terms>` — search the home and linked vaults by meaning when `[embedding]` is
  configured, plus by full-text, so a single word or an identifier is found too (the two lists are merged by
  position, and the best entry by words is among the first three); otherwise by full-text.
  `--vault V` (repeatable) searches the given vaults instead; `--service S` reads another database.
- `val3 review` — pending review items (what a machine cannot decide; see Reviewing); `val3 review --tty`
  marks and decides them in a full-screen list; `review show|approve|reject <id>`. On a vault upgraded for
  ADR 0012 the list is retired: `val3 review` lists its history read only, and `approve`, `reject` and `--tty`
  exit 1.
- `val3 confirm <vault>#<id> [FILE]` — the entry still holds: append new evidence (JSON, stdin by default)
  that replaces its current evidence (see Re-checking entries).
- `val3 withdraw <vault>#<id> | --batch <batch> (--wrong | --obsolete) --reason "..." [--evidence FILE|-]` —
  take an entry, or what an import batch admitted, out of delivery for good; inside a Claude Code session it
  withdraws at once with evidence that checks, and otherwise queues a proposal on the review list, or, on a vault
  upgraded for ADR 0012, exits 1: a person runs it in a terminal (see Withdrawing).
- `val3 settle <ref> <near> (--replace | --drop | --keep)` — settle an entry marked *to settle* and its close
  neighbour (see Settling near duplicates).
- `val3 tool add <name> --type skill|mcp|cli --description ...` — register an external tool.
- `val3 vault create <vault> [--description D [--parent P]] [--service S]` /
  `val3 vault grant <vault> <role> --read|--write` — operator commands; `--parent` needs `--description`, and
  names starting with `val3_` are reserved. Inside a project,
  `val3 vault create <vault> --specialized --description D [--service OWNER]` creates a specialized vault
  (parent the home vault), grants the project's role write when `OWNER` is another role, and adds it to
  `val3.toml`; a name `val3.toml` already declares is refused. On a database whose `val3_common` predates
  vault notes, a plain `vault create` works and `--description` or `--specialized` asks for
  `val3 vault upgrade <vault>` first.
- `val3 vault describe <vault> --description D [--parent P]` — record a vault's description and parent (needs
  write on the vault).
- `val3 vault list [--service S]` — the vaults of the database: live entries, this role's privilege
  (`write`, `read` or `none`; a mirror is never `write`), parent, description with its writer, and a mirror's
  source and last refresh.
- `val3 vault dump <vault> --file F|- [--service S]` /
  `val3 vault mirror <vault> --from-file F|- | --from-service S [--service S]` — operator commands: copy a
  vault to another database as a read-only mirror (see Vaults on another host). On `dump`, `--service` is the
  database the vault is in; on `mirror`, it is the database that holds the mirror, and `--from-service` the
  one the vault is dumped from.
- `val3 vault upgrade <vault> [--service S] [--dry-run]` — operator command: add the tables of later versions
  (semantic search, withdrawal, index merges, the neighbour check, attestation) to a vault created before them
  (idempotent). Adding attestation is the switch of ADR 0012 (see Backing): in the same transaction, under the
  writer's lock, every entry without evidence is attested and every undecided review item is closed by
  `val3 upgrade` (notices approved, candidates and withdrawal proposals retired; nothing is admitted or
  withdrawn), and each of them is printed. `--dry-run` prints every change and changes nothing. A mirror is not
  upgraded: refresh it from its upgraded source.
- `val3 backup [--service S] [--out FILE]` — operator command: dump the whole database into a private
  file (see Backup and restore).
- `val3 import --dry-run [--out FILE]` / `val3 import --apply FILE` — import the knowledge of a
  validated-memory 2.x adopter: candidates whose quotes all verified are admitted, the rest queued for review,
  or, on a vault upgraded for ADR 0012, admitted without evidence (see Importing from validated-memory 2.x).
- `val3 embed` — store the vectors home-vault entries lack, with the neighbour check (needs `[embedding]`; see
  Semantic search).
- `val3 eval FILE [--draft]` — measure the floor against a labelled set; `--draft` writes an unlabelled
  set from a prompt log (see Calibrating the floor).
- `val3 extract <transcript>` — extract a session transcript now (only lines not extracted before).
- `val3 drain` — extract every queued session now; `val3 queue` — queued and failed captures.
- `val3 hook session-start|prompt|capture` — harness hook entry points.

`review --tty`, `review approve|reject`, `tool add` and `import --apply` are decisions for a person: they refuse to
run inside a Claude Code session (`CLAUDECODE` set). Run them in a separate terminal.

## Backing

On a vault upgraded for ADR 0012 (`val3 vault upgrade`; `val3 vault create` makes one) nothing waits for a
person, and every entry has one of three kinds of backing, stored, never guessed:

- **evidence**: at least one evidence row (a quote that checked);
- **attested**: a person vouched for it: `val3 tool add`, `val3 import --apply` for a candidate without any
  quote, and the upgrade for every entry that had no evidence then (each was admitted by a person's decision).
  Attested is not verified: `val3 show` prints `evidence: none — attested by a person: <who>, <how>, on <date>`;
- **unverified**: neither. `val3 show` prints `UNVERIFIED: no evidence and no attestation`, and `recall`,
  `val3 index <name>` and `memory_search` mark the entry `· unverified` on its own line (`· backing not readable`
  when the role cannot read `evidence` or `attestation`).

An unverified entry has less authority. It does not supersede an entry with evidence or attestation (`val3 save`
admits it without that supersession and warns; `val3 settle --replace` in that direction exits 1); `settle
--drop` withdraws an entry only toward one of equal or higher backing; a new recipe cannot cite it, and a recipe
that already does is marked `· cites unverified` (its step `[unverified]`). The session index and the prompt hook
deliver only entries with evidence or attestation, filtered before statistics, ranking and limits; explicit
lookups return everything, marked; a pair *to settle* is shown and counted in delivery only when both entries may
be delivered. A memory note is annotated `(verified)` only when the entry it ends at, after its supersessions, has
backing. Evidence comes later when the same kind, title and statement arrive with evidence that checks, through
`val3 save`, capture, or an import whose quotes all verified (counted as a duplicate): `verified <ref>`, a
`verified` event; repeating it changes nothing. A capture `repeat` of other text, a narrower restatement included,
verifies nothing. Only while the entry is live: a withdrawal or a `settle --drop` is final. A recipe or a tool is not
verified this way, since its hash does not cover its steps, type or environment. `val3 eval` with a case's `at`
reads backing as of that moment, as it reads liveness.

What backing means: evidence is checked provenance, the quote exists where it says. It is not a proof that the
quote entails the statement, nor that someone other than the agent wrote it. An agent can save a fact without
evidence and later save the same text with a real but unrelated quote, or `val3 confirm` it; nothing checks
that promotion. The remedies are withdrawal and re-check, and `val3 show` and the view print each entry's
evidence.

A vault without the attestation table is served by the rules below (queue included) by the same code.

## Reviewing

On a vault upgraded for ADR 0012 the review list is retired: no command creates a pending item, `val3 review`
lists the history read only (`the review list is retired (ADR 0012): nothing waits for a person; history of vault
<vault>:`, then each item with its decision), `val3 review show <id>` still prints a card, and `approve`, `reject`
and `--tty` exit 1. The rest of this section describes a vault not yet upgraded.

The review list holds what a machine cannot decide. A candidate of any kind (fact, preference, decision, recipe,
tool) with at least one evidence item, every one of which checks, is admitted at once; a person sees only a
candidate without evidence (reason `no-evidence`, a fact, preference or decision included since ADR 0012's
amendment 1), an imported candidate whose quotes did not all verify
(`import`) and a withdrawal proposal (`withdrawal`). Supersessions and withdrawals queue no notices: they are in
the event log, and `val3 status` counts those of the last 7 days. A vault created before this version needs
`val3 vault upgrade <vault>` before a candidate without evidence can be queued; items already on its list stay
there for a person.

`val3 review --tty` lists the pending items in a full-screen view: mark, `#id`, kind, title and evidence
(`✓ snapshot`, `~ changed`, `✗ unsupported` for imported items; the number of evidence items otherwise).
`↑↓` move, `space` marks, `*` marks every row, `Enter` opens the card, `a` approves and `r` rejects the
marked items (or the current one) after a confirmation that names how many carry no evidence and how
many were not opened. On a card, `a` and `r` decide that item alone; `n` and `p` move to the next or
previous item; `q` goes back. Notices queued by earlier versions cannot be rejected: they record a
supersession or a withdrawal that already happened (`a withdrawal is final`). Cards show evidence quotes
and, for a command, its reproduction marked `(not run)`: VAL3 never runs it. It needs a terminal; `NO_COLOR`
turns colours off. Scripts use `review show|approve|reject`. When a vector cannot be stored after an approval, the warning shows
on the list's status line during the walk and is printed once to stderr after the walk ends. A
supersession notice shows the old entry (`BEFORE invoices#5`) and the new one (`NOW invoices#20`).
Entries are written `<vault>#<id>`, review items `review #<n>`.

## Withdrawing

`val3 withdraw` takes an entry out of delivery for good, in two forms: one entry of the home vault, or every
entry an import batch admitted (`val3 import --apply` prints the batch id). Each states a cause, `--wrong` (the
knowledge was never true) or `--obsolete` (it was true once and is no longer needed), and a `--reason`; a third
cause, `duplicate`, is recorded only by `val3 settle --drop`:

```bash
val3 withdraw invoices#12 --wrong --reason "the column was renamed; this query now fails"
val3 withdraw --batch 0b08190987d3b1bc --obsolete --reason "the module was retired"
```

A withdrawn entry leaves search, `recall` and the session index, and a new recipe cannot cite it
(`<ref> was withdrawn (<cause>): <reason>`). `val3 show` prints `WITHDRAWN (<cause>) <date> by <who>: <reason>`.
A linked vault is read only: withdraw from the project that owns it. Withdrawing an entry that is already
superseded records the reason and changes nothing in delivery.

A withdrawal is final; there is no restore. The knowledge comes back only as a new entry with changed text
(title or statement), admitted again when its evidence checks; saving exactly the same text (kind, title and
statement) is reported as `duplicate of <ref>, withdrawn (<cause>): <reason>; save it with changed text to bring
it back`, because entries are deduplicated by content. Withdrawing an entry that superseded another does not bring the
older one back: save it again if it still holds. A live recipe that cites the withdrawn entry is marked *to
re-check* (see Re-checking entries).

Outside the agent the command withdraws at once and prints `withdrawn invoices#12`. For a batch it prints:

```
batch 0b08190987d3b1bc: 41 withdrawn, 2 pending rejected, 1 not withdrawn (existed before the batch), 0 already withdrawn
```

`pending rejected` counts the batch's items still on the review list, which are rejected in the same step;
`not withdrawn (existed before the batch)` counts entries the vault already held before the batch (its
items were approved as duplicates of them), which stay; `already withdrawn` counts entries withdrawn earlier. A batch that admitted nothing is an
error.

Inside a Claude Code session (`CLAUDECODE` set), with `--evidence` (a JSON list, the format of
`val3 withdraw --help`) that checks against the transcript as `val3 save` checks it — a tool item (`query`,
`command`, `api`, `test`) or a `user` item, for an entry of any kind — the entry is withdrawn at once and the
command prints `withdrawn <ref>`; the withdrawer is recorded as `agent <session id>`. A changed or deleted
file is shown by the output of a `git show` or `git log` command.
A quote not found in the transcript exits 1 and withdraws or proposes nothing; the output of a `val3`
command is never evidence. `--batch` does not take `--evidence`.

Evidence is checked provenance, not proof: that the quote bears on the entry is the withdrawer's judgement. So
that the attribution can be examined afterwards, the withdrawal records for each tool item the calls that
printed its quote: the tool, the call id and a SHA-256 of the call's complete input, never the input's text (a
command line can hold a secret, and the row is permanent and mirrored). For a shell command, write the exact
command as `reproduction`, without secrets: `val3 show` then says `printed by: Bash (the command above,
checked)`; a description instead gives `(the line above is a description, not checked)`. While the session
transcript exists, the call is found there by its id.

On a vault upgraded for ADR 0012 nothing is proposed: a withdrawal without evidence, or of a batch, is a
person's, run in a terminal (standard input is a terminal and `CLAUDECODE` is not set), and takes effect at once;
otherwise the command exits 1 (`a withdrawal without evidence, or of a batch, is a person's: …`). The environment
variable is a convention between val3 and the harness, not an authorization boundary: the terminal check narrows
it, nothing more. On a vault not yet upgraded:

Without `--evidence`, or for a batch, inside a Claude Code session the command withdraws nothing: it checks
the same things and queues a proposal, printing `proposed: review #N` (repeating it prints
`already proposed: review #N`, or
`previously rejected: review #N` after a rejection). The card shows the entry, or the batch and how many
entries it admitted, then `approving withdraws (<cause>): <reason>`. A person approves it with
`val3 review approve <id>` or in `val3 review --tty`, which withdraws as above and records the person who
decided; rejecting it changes nothing. Evidence given with a proposal shows on the card and is stored with
the withdrawal when a person approves it; outside the agent `--evidence` is optional and stored too.

A vault created before this version needs `val3 vault upgrade <vault>` before anything can be withdrawn
from it (a vault upgraded before entry re-check needs it again before `--evidence` can be stored); reading it works without. A val3 older than this version, reading an upgraded vault, still delivers
withdrawn entries: upgrade val3 everywhere the vault is read.

## Re-checking entries

An imported entry cites repository files (`file` evidence, a quote at a commit). When a cited file changes
or disappears at HEAD, the entry is marked *to re-check*. The mark is computed each time it is read, from
the committed tree at HEAD (never the working tree), and only for home-vault entries whose cited commit is
in the repository val3 runs in. A live recipe of the home vault whose steps cite a fact or a tool that was
superseded or withdrawn is marked too, without git; the mark stops when the recipe is confirmed after that,
superseded or withdrawn. It shows in four places:

- the session index: the line ends with ` · to re-check`, and the header says what to do;
- per-prompt delivery: the same suffix after the ref;
- `val3 show <ref>`: `TO RE-CHECK: <path> changed at HEAD <sha12>` or `TO RE-CHECK: <path> is not in HEAD
  <sha12>`; for a recipe, `TO RE-CHECK: cites <ref>, which is superseded by <ref>` or `…, which is withdrawn`;
- `val3 status`: `to re-check: N (<ref>, …)`, or `to re-check: not checked (…)` when the project root is not
  the root of a git repository, or git failed or took longer than 3 s (marked recipes are still counted, with
  `· file evidence not checked (…)`).

All git reads of one check share a 3-second budget; when git fails, entries are left unmarked and the hooks
still answer. A marked entry is resolved by whoever relies on it:

- it still holds: `val3 confirm`;
- it changed: `val3 save` with `"supersedes"` (one ref or a list: one entry may replace several);
- it no longer applies: `val3 withdraw` (with `--evidence` inside the agent, see Withdrawing).

```bash
echo '[{"kind": "file", "path": "config/routing.yaml", "quote": "default_route: eu-west"}]' \
  | val3 confirm invoices#4
```

It prints `confirmed invoices#4 (1 evidence items at <sha12>)`. A `file` item is checked against the
committed file at HEAD and stored at HEAD's sha; other items (`query`, `command`, `api`, `test`, `user`) are
checked against the session transcript, as `val3 save` does (`val3 confirm --help` shows the format). A quote
that does not check exits 1 and stores nothing. The new items replace the entry's current evidence: cite
every file that still backs it. A confirmation without `file` items ends the repository signal for that
entry, which suits an entry whose cited file was deleted. Older evidence stays in the vault. Only live
entries of the home vault can be confirmed.

## Settling near duplicates

With `[embedding]` configured, storing an entry's vector runs the neighbour check: every live entry of the home
vault of the same kind whose vector in the same embedding space has a cosine similarity of at least
`[embedding] neighbour` (default 0.70, a number between 0 and 1) is recorded as a close neighbour. The check runs
right after an admission, or later in `val3 embed` (the drainer runs it after each extraction) when the
embedding service was down, so the later-embedded entry of a pair finds the earlier one. Entries that existed when
the vault was upgraded to this version, and entries admitted while the project had no `[embedding]`, are exempt:
two exempt entries are never paired, so re-embedding after a new model build or adding `[embedding]` later pairs
no two of them; a pair with a non-exempt entry is still found, whichever side is embedded later. Without
`[embedding]` no check runs.

`neighbour = false` turns the check off for the project's vaults: no pair is recorded, capture makes no second
extractor call, pairs recorded before are not shown, and `val3 status` says `to settle: off`. It suits a vault
whose entries are close by nature, such as the iterations of one procedure over a series of objects, where nearly
every pair is settled `--keep`. An entry that replaces another still names it in `supersedes`. Entries admitted
meanwhile are exempt, like those admitted without `[embedding]`: setting a number again pairs no two of them.
The setting is the project's: it covers its home and specialized vaults.

An entry with a neighbour is admitted and live; the newer entry of each pair is marked *to settle* while both are
live and the pair is not settled. The mark shows in:

- `val3 save` stderr, after `admitted <ref>`: one line per neighbour,
  `val3: near <ref> (<score>): val3 settle <new> <ref> --replace | --drop | --keep`;
- the session index, per-prompt delivery and `val3 index <name>`: the line ends with
  ` · to settle (near <ref>, <ref>)`, and the session index header says what to do;
- `val3 show <ref>`: `TO SETTLE: close to <ref> (<score>)` per open pair;
- `val3 status`: `to settle: N (<ref>, …)`.

The agent that meets the mark settles the pair; it needs no evidence, the recorded pair is the evidence:

```bash
val3 settle invoices#31 invoices#12 --replace   # replaced invoices#12 by invoices#31
val3 settle invoices#31 invoices#12 --drop      # dropped invoices#31 (duplicate of invoices#12)
val3 settle invoices#31 invoices#12 --keep      # kept invoices#31 and invoices#12
```

`--replace` makes the new entry supersede its neighbour, also when it already supersedes another (one entry may
replace several);
`--drop` withdraws the new entry with cause `duplicate` and reason `duplicate of <ref>`; `--keep` records both as
distinct, never marked again. The settlement records who decided: `agent <session id>` inside a Claude Code
session, the user otherwise. A pair whose entry was superseded or withdrawn meanwhile, or already settled, is
refused with exit 1 and nothing written. Only pairs of the home vault are settled. A vault created before this
version needs `val3 vault upgrade <vault>` before `val3 settle`; reading it works without marks.

Capture settles what the slice can decide. After the extractor answers a slice, capture computes the vectors of
its candidates and looks up each one's neighbours by the same rule (an entry the candidate names in
`supersedes` is shown whatever its score). If any candidate has neighbours, one more extractor call
(`settle_prompt.md`) reads the slice again with those candidates and their neighbours and answers per candidate:

- `new` — no neighbour is about the same thing (object, property and scope): admitted; each pair it was compared
  with is settled `keep`;
- `repeat` — one neighbour already says everything it says (a narrower restatement included): the candidate is
  not admitted, the event log records it (action
  `repeat`, on the neighbour, with the candidate) and a memory note that proposed it points at the neighbour;
- `replaces` — admitted superseding every neighbour it names, each pair settled `replace`, the rest `keep`;
- `undecided` — a neighbour is about the same thing but the candidate neither repeats nor replaces it (it changes
  a value and drops a part that still holds, or adds a detail and leaves out others): admitted superseding
  nothing, its pairs left *to settle* for the agent. One such neighbour makes the whole candidate undecided.

The slice's most recent measurement counts as the current state. A neighbour admitted or confirmed after the
slice's last transcript record is never replaced (an old session drained after a newer one); that pair stays
open, as it does when the transcript has no timestamps. A decision that names a ref which is not one of the
candidate's neighbours, or a failed second call, is treated as `undecided`; when the vectors cannot be computed
the candidates go as before. Capture's settlements record `extractor <session>` as who decided. Candidates of
one slice are not compared with each other. Without `[embedding]`, or on a vault without the
neighbour tables, capture admits as before.

## Backup and restore

`val3 backup` runs `pg_dump -Fc` over the whole `val3` database into
`~/.local/share/val3/backup/<database>-<time>.dump` (mode 0600, never overwritten). Its role needs
read access to every vault: in practice the owner. `pg_dump` must not be older than the server
(`pg_dump 14 cannot dump a PostgreSQL 16 server`); use the same major version for `pg_dump` and
`pg_restore`.

The dump does not hold extension packages, roles, `pg_service.conf`, `.pgpass` or the projects'
`val3.toml`. On the target server (the same major version as the source) install the `unaccent`
and `vector` packages, restore with a role allowed to create those extensions (usually a
superuser), and create the roles the vaults are granted to, then:

```bash
createdb -T template0 <restored>
pg_restore --single-transaction --exit-on-error -d "service=<restored>" <file>
```

`<restored>` names the new database, and the `-d` service is a `pg_service.conf` entry of that name with
`dbname=<restored>` on the target server.

Use the restored database only when `pg_restore` exits 0. Without the roles, add
`--no-owner --no-privileges` and grant again with `val3 vault grant`. VAL3 never restores over the
live database.

## Vaults on another host

A vault of another database, on a host whose PostgreSQL this one cannot reach, is read here as a **mirror**:
a read-only copy refreshed from a dump. Run `val3 vault upgrade <vault>` on the source vault first: the dump
refuses a vault without its identity. On this side, a `val3_common` from an older version needs
`val3 vault upgrade` of one local vault (the mirror names it). Load or refresh it with an operator role of this
database; outside a project directory each command names its service:

```bash
ssh other-host val3 vault dump shared --file - --service <remote-service> \
  | val3 vault mirror shared --from-file - --service <local-service>
```

`--from-file` also takes a file copied by `scp`, and `--from-service S` dumps through a `pg_service.conf`
entry (an SSH tunnel, for instance). Refresh it by cron, for example every night at 03:00:

```
0 3 * * * ssh other-host val3 vault dump shared --file - --service <remote-service> | val3 vault mirror shared --from-file - --service <local-service>
```

- A mirror is read only: a trigger on its tables refuses every insert outside a load, the writer's included;
  a vault is a mirror while its `entry` table carries that trigger. `vault grant --write` on it is refused.
- A refresh keeps exactly the mirror's grants (schema `USAGE` and `SELECT` table by table, `PUBLIC` included);
  it is refused, with the old mirror intact, when the dump comes from another vault identity, has fewer rows
  than the mirror, or lacks one of its rows or holds it with another value in any column.
- Give sessions read access only: `val3 vault grant shared <role> --read`; link it in `val3.toml` or read it
  with `val3 show`, `recall --vault` and `index --vault`. `val3 status` shows a linked mirror's age.

## Importing from validated-memory 2.x

`val3 import` reads an adopter's knowledge units (`knowledge/kb-*.md`) and memory notes
(`memory/*.md`) and makes one candidate per source: admitted when every quote verified, otherwise put on the
review list. It needs `git` and runs at
the adopter's repository root, next to a `val3.toml` that names the vault to fill. The adopter's
tree is only read; to keep `val3.toml` out of its repository, list it in `.git/info/exclude`.

```bash
val3 import --dry-run            # extract and check quotes; writes a manifest, no database writes
val3 import --apply <manifest>   # a person, outside Claude Code: admit what verified, queue the rest
val3 review --tty                # decide the imported candidates that did not verify
```

The dry run pins HEAD and reads everything from that commit, never from the working tree: a changed
tracked source is imported as committed (reported as differing from the working tree), and
untracked, staged-new and renamed-to paths are reported as `uncommitted`. Superseded units and notes
are not candidates; they are read as context by the source that replaces them (for a chain A to B to
C, by C; a chain ending in a missing source or looping leaves its members as ordinary candidates).
Each source and the files it rests on (a unit's `provenance` commits; for a note, the files it
names, at the note's last commit) go to the extractor, which returns one candidate or a skip
(`derivable`, `retired`, `not-knowledge`). Each quote is checked with `git show` at that commit
(`verified`, and whether it is still at the snapshot), or reported `mismatched`, `unbound` (a file
the source does not cite, or the source's own prose: the source itself is quoted only inside one of its fenced
code blocks, for what it records, such as a test result or an example; that evidence says the note records it,
not that it is true), `excluded` (validated-memory's own files, memory exports and files named
like secrets: `.env*`, `*.pem`, `*.key`, `id_*`, `*secret*`, `*.p12`, or inside a directory named
that way; none is sent to the extractor) or `unreadable` (including a file whose commit is missing
from the repository). A candidate with no verified quote is *unsupported*: approving it attests it,
and `val3 show` says so. The manifest (`~/.local/share/val3/import/`, mode 0600; its remote URL has
any credentials removed) holds the exact candidates. `--apply` re-derives the source, the cited
files and every quote from git, admits or queues the rebuilt candidate, reports a record that no longer
matches as `refused` (manifest text it prints is escaped), and skips sources already imported. A candidate
with evidence whose quotes all verified is admitted at once: its review item gets an approval decided by
`val3 (mechanical)`, so `withdraw --batch` finds it. The rest (unsupported candidates and those with a
`mismatched`, `unbound`, `excluded` or `unreadable` quote) wait on the review list; on a vault upgraded for
ADR 0012 they are admitted with the same mechanical decision and without evidence: one with no quote at all is
attested by the person who ran `--apply`, one whose quotes did not all verify is unverified (the attempt's results
stay in its review item); one whose quotes all verified and whose text equals a live unverified fact, preference or
decision gives it its evidence (`verified` event) and counts as a duplicate. It prints one line of
counts, `batch <batch>: <n> admitted, <n> queued, <n> already queued, <n> previously rejected, <n> duplicate,
<n> already imported, <n> refused`, and stores the vectors of what it admitted when `[embedding]` is configured
(with the neighbour check). The batch event records the repository's own remote URL, also without credentials.

An admitted entry carries `file` evidence (`git show <commit>:<path>`, shell-quoted, and the
quote); the session index, per-prompt delivery, `val3 show` and `val3 status` read the repository
again only to mark entries to re-check (see Re-checking entries); `val3 recall` does not. Vaults
created before this version need `val3 vault upgrade <vault>`.

How much was verified and how each item was decided (replace `invoices` and the batch id):

```sql
SELECT r.payload->'import'->>'source_kind' AS source,
       CASE WHEN jsonb_array_length(r.payload->'evidence') = 0 THEN 'unsupported'
            WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(r.payload->'import'->'evidence') e
                         WHERE e->>'status' = 'verified' AND (e->>'at_snapshot')::boolean) THEN 'verified at snapshot'
            ELSE 'verified, changed since' END AS evidence,
       coalesce(d.decision, 'pending') AS decision, d.decided_by = 'val3 (mechanical)' AS mechanical, count(*)
FROM invoices.review_item r LEFT JOIN invoices.review_decision d ON d.review_id = r.id
WHERE r.reason = 'import' AND r.payload->'import'->>'batch' = '<batch>'
GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;
```

To let one session of the adopter receive the imported entries without capturing anything, put only
the `SessionStart` and `UserPromptSubmit` hooks of Claude Code hooks in its
`.claude/settings.local.json`; without the capture hooks, its `memory/` is never annotated. Remove
that file and `val3.toml` to undo it.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest
```

The tests need Docker: each run starts a disposable `pgvector/pgvector:0.8.1-pg16` container.

For manual work, a persistent development server can run in Docker on the loopback interface:

```bash
docker run -d --name val3-dev --restart unless-stopped -v val3-dev-data:/var/lib/postgresql/data \
  -p 127.0.0.1:5433:5432 -e POSTGRES_USER=val3 -e POSTGRES_DB=val3 \
  -e POSTGRES_PASSWORD='<password>' pgvector/pgvector:0.8.1-pg16
```

with a `[val3dev]` service (`host=127.0.0.1`, `port=5433`, `dbname=val3`, `user=val3`) and the
password in `~/.pgpass`.

## License

Licensed under [Apache-2.0](LICENSE). Security reports: [SECURITY.md](SECURITY.md).
