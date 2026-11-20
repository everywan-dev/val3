# The vault view — design (plan 15, part H)

Status: mockup and decisions approved by the owner on 2026-11-15 (§8); revised for a live page served from the
database. A mockup with invented data exists outside the repository; this file says what the page shows and
where each thing comes from.

## 1. Purpose
A person looks at a vault and understands it: what it holds, how it got there, what is related to what, what
is used and what is not. Today that needs `val3 index`, `show`, `recall` and SQL. The page observes; it decides
nothing and writes nothing (plan 15, part B removes decisions by a person).

Questions it must answer without SQL, taken from what the owner asked in sessions:
1. What does this vault hold, by topic and by kind, and how much of it is live?
2. What did a given session or a given day add, and who wrote it (capture, the agent, an import)?
3. What replaced what; what was withdrawn and why?
4. Which entries have no evidence (unverified, after part B)?
5. Everything recorded about one named thing (a host, a database, a run).
6. Which entries are returned by searches and which never are?
7. What does capture cost per session and per entry?

## 2. Form
`val3 view [--port N] [--no-open]`, run in a project directory: a small web server in the foreground that
serves one page and answers its requests from the database, live. It ends with the command (Ctrl-C). The owner
chose this over a generated file (2026-11-15): the system already requires the PostgreSQL connection, the page
is always current, and no copy of vault text is left on disk.
- Standard library only (`http.server`), one process, one connection per request.
- It prints the address to open, `http://127.0.0.1:<port>/<token>/`. On another host the person forwards the
  port (`ssh -L`); the server never listens on any other address.
- It reads the project's vaults (home, specialized, linked) through the project's own service and role, so the
  database's permissions decide what can be seen, as for every other command.
- The page is static HTML, CSS and JavaScript shipped inside the package (no external request of any kind, no
  web font); it asks the server for JSON.

## 3. What exists to show (measured on one adopter's vault, 2026-11-15)
| Data | Source | Size there | Good for |
|---|---|---|---|
| entries: kind, title, statement, keywords, origin project/session/machine, date | `entry` | 501 | everything |
| index membership | `entry_index`, `knowledge_index` | 10 indexes, 513 memberships, no entry without one | grouping by topic |
| evidence: kind, reproduction, quote | `evidence` | 631 (1.26 per entry) | trust, question 4 |
| supersession | `supersession` | 34 | question 3 |
| withdrawal with cause and reason | `withdrawal` | 20 | question 3 |
| recipe steps, cited facts and tools | `recipe_step`, `step_fact` | 1 recipe, 0 cited facts | almost nothing yet |
| close pairs and their settlement | `neighbour`, `settlement` | hundreds to thousands | only as a faint layer |
| vectors | `embedding` | one per entry | closeness for the map |
| events: admitted, settled, superseded, withdrawn, discarded, repeat, invalid | `event` | 713 | time line, question 2 |
| vault notes, links, mirror syncs | `val3_common` | few | header |
| capture runs: calls, cost, admitted, skipped | `extract.log` (state directory, not the vault) | one line per run | question 7 |
| what searches returned | not recorded today | — | question 6 needs it (§7) |

**Consequence for the graph.** The explicit relations are few: a graph of only supersession and citation edges
would be 500 loose dots with 34 arrows. What gives a vault its shape is topic (index), closeness in meaning,
time, origin, and the exact names entries share. The map is built on those, with the explicit relations drawn
on top where they exist.

## 4. Views
**Header.** Project and vaults with their descriptions, the time of the data, linked and mirrored vaults with
their last sync, a reload control.

**Overview.**
- Counts: entries; live with evidence; unverified; superseded; withdrawn; live and never returned by a search
  (when §7 exists).
- Admitted per day, stacked by who wrote it (capture, agent's save, import).
- Entries per index.
- "Worth a look": most returned entries, superseded this week, admitted without evidence, each a link.

**Map.** Each entry a dot.
- Position: by closeness in meaning. The server returns each entry's three nearest neighbours from the
  vectors (one query per vault), and the page lays them out. Entries of one topic fall together without
  anyone drawing a category.
- Colour, chosen by the viewer: index, who wrote it, age, kind, origin session.
- Filled dot: live. Hollow: superseded, withdrawn. Ring: unverified.
- Size: times returned by a search (when §7 exists), else uniform.
- Edges: solid arrow for supersession, dashed for a recipe's citations, faint for nearest neighbours. Recorded
  `neighbour` pairs are not drawn (too many, and they are the same information as the position).
- Click: the entry panel. Linked vaults are a second layer that can be switched on.
- Bound: above 2 000 entries the map shows one index or one time window at a time, chosen by the viewer.

**Entries.** A table: ref, title, kind, index, state, evidence kind, date, times returned. Filters: text, kind,
index, state, who wrote it, session, date range. Sort by any column. A row opens the entry panel.

**Entry panel** (beside every view).
- Ref, state, title, statement, keywords.
- Kind, indexes, date, who wrote it, session, machine, project.
- Evidence: kind, reproduction, quote.
- Relations: supersedes / superseded by; cited by a recipe / cites; withdrawn (cause, reason, date); nearest
  entries. Each a link.
- History: its events in order.

**Sessions.** One row per origin session: days, entries captured, entries saved by the agent, how many of them
were later superseded or withdrawn, extractor calls and cost (from `extract.log`, joined by session id), records
skipped as already processed. A row filters the table and the map to that session.

**Names.** Exact names shared by two or more entries, found by pattern in titles and statements (identifiers
with a digit, underscore, dot, slash or hyphen; no model): how many entries, how many not live, last date. A name
filters the table and the map. This is question 5, and the deterministic form of "linked by exact names" from
the roadmap.

**Changes** (second version). The event log as a time line: per day, what was admitted, superseded, withdrawn,
discarded and why; pairs of old and new text side by side for a supersession.

## 5. What it does not show
- No editing, approving or withdrawing.
- No raw transcripts.
- No per-prompt delivery log in the first version (it holds the user's prompts).
- Recorded close pairs and their settlement, except as a count in the entry panel.

## 6. Security
The server shows vault text, evidence included, to whoever can reach it; these rules keep that to the person
who started it.
- Listens on 127.0.0.1 only. A request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` is
  refused (a page on another site that resolves its own name to the loopback address must not read the vault).
- A random token per run is the first path segment of every URL; without it the answer is 404. No cookie.
- GET and HEAD only; every other method is refused. No endpoint writes, runs a command or takes SQL.
- Every request runs in a read-only transaction with a statement timeout, on the project's vaults only; vault
  and entry identifiers in a URL are validated against the project's list and as integers, never interpolated
  as given.
- Responses carry `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self';
  connect-src 'self'; img-src 'self' data:`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `Cache-Control: no-store`, and no CORS header.
- Entry text reaches the page as JSON and is inserted with `textContent`, never as markup. `text.safe()` is for
  terminals and is not relied on here.
- Tests: a wrong or missing token, a foreign `Host`, a POST, a vault outside the project, an entry id that is
  not a number, and hostile titles, statements, quotes, index names and session ids rendered without becoming
  markup (asserted on the JSON and by a check that the page's script never assigns `innerHTML`).
- Evidence is shown: the reader already holds the database role that can read it.

## 7. The usage log (approved 2026-11-15)
"Returned by a search" did not exist as data. `val3 recall`, `val3 show`, `memory_search` and `memory_read`
append one line per call to `usage/<project>.jsonl` in the state directory: time, session when known, the
command or tool, the query and its number of words, and the refs returned. The query text is kept since 2026-11-16 (owner's
decision; the first version kept the number of words only), cut to 500 characters, in a file only its owner reads.
Lines older than 30 days are dropped, as for the prompt log. A failure to write never changes the command's
output or exit code. The view reads it for the "returned" figures of the machine it runs on; on another machine
those figures are absent and the page says so. Part D counts consultations from it instead of parsing
transcripts.

## 8. Decisions of the owner (2026-11-15)
1. Live from the database, not a generated file.
2. The usage log of §7: yes.
3. Evidence shown (no copy leaves the database).
4. A project's vaults together, the linked ones as a layer.
5. The page in English.
Open: whether the usage log keeps the query text.

## 9. Order of building
1. Server, token, `Host` check, read-only transaction, security tests.
2. JSON endpoints: summary, entries, entry, map (nearest neighbours from the vectors), sessions, names.
3. The page: header, overview, entries, entry panel, map, sessions, names.
4. The usage log and its figures.
Later: changes (the event log as a time line, old and new text side by side).
Check: the owner answers questions 1 to 7 on the largest vault; a synthetic vault of 5 000 entries answers each
endpoint in under 2 seconds.

## 10. Contracts fixed after the challenge
Codex gpt-6.1-sol, `medium`, read-only, 2026-11-15: 27 objections, 7 blocking
(`~/.local/share/val3/eval/plan-15/codex-view-review.md`); its claims about the code were checked (an admission
does not record its channel; `extract.log` is written by the drainer only; no vector index; `connect()` is
autocommit; package data is `*.sql` and `*.md` only). Nearest neighbours measured on a 705-entry vault: 1.3 s
for all entries, growing with the square of the size. All 27 taken; what changes:

**Trust boundary.** The token (32 random bytes, hex) is the credential: whoever holds it reads what the
server's database role reads. Not protected against: other processes of the same user, a compromised browser or
its extensions. The page says "VAL3 writes no export"; it does not claim that no copy leaves the database.
Token and `Host` (exactly one header, exact value) are checked before any database connection; a foreign
`Origin` is refused. The access log prints no path. The page title is fixed. The token dies with the process.

**Availability.** Threaded server with at most 8 worker threads; 10 s to read a request, 30 s per request in
all; idle connections closed. Each request: one connection, `BEGIN READ ONLY` at repeatable read, statement
timeout 5 s, closed before the response is written. HEAD runs no query.

**Headers.** CSP `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self';
frame-ancestors 'none'; base-uri 'none'; form-action 'none'`. JavaScript and CSS are separate files served from
a closed list of three resources (page, script, style) read with `importlib.resources`; `pyproject.toml` adds
them to the package data. No image, no inline script or style.

**Endpoints** (all under `/<token>/api/`, JSON, ids and refs as strings, times in UTC with offset, every answer
with `at` and, per vault, `ok` or the reason it could not be read; a vault that fails does not fail the others
and never shows as zero):
- `summary`: per vault, counts by the predicates below; admissions per day; entries per index.
- `entries?vault=&q=&kind=&index=&state=&session=&from=&to=&sort=&page=`: 200 rows a page; `q` is a literal
  substring (at most 200 characters) over title, statement and keywords; `sort` from a closed list; statements
  cut to 300 characters.
- `entry?ref=`: one entry in full with evidence (each quote cut to 4 000 characters and marked when cut),
  relations, recorded close pairs with their settlement, events.
- `sessions`: per (project, machine, session); a missing session is "unknown origin"; each source aggregated
  before joining.
- `names?q=`: suggestions (see below), or the entries containing a literal name.
- `map?vault=`: nodes and edges for at most 1 500 entries of one vault (the most recent; the answer says how
  many were left out and how many have no vector), nearest neighbours within one vector space
  (`model, digest, text_version, dims`), computed once per vault and kept for 10 minutes.
- `usage`: see §7.

**Predicates, stated on the page.** *Live*: not superseded and not withdrawn (the code's own `live`).
*Superseded*, *withdrawn*: an entry can be both; both are shown. *No evidence*: no evidence row; apart from it,
*approved by a person* (15 imported entries on one vault). After ADR 0012: *unverified* = live, no evidence, not
approved by a person. Index counts do not add up to the total (an entry can be in several, or in none: "no
index" is a row); aliases are resolved with the existing code.

**Who wrote it.** Not recorded today: an `admitted` event carries no channel. From this change on the writer
records `via` (`capture`, `save`, `import`, `tool-add`) in that event's detail; earlier entries show "not
recorded". No breakdown is drawn from guesses.

**Cost.** `extract.log` holds the drainer's finished runs on this machine: no manual `val3 extract`, no failed
attempt. The page labels it "extractor cost recorded on this machine" per session and shows no cost per entry.

**Map.** Labelled as an approximate layout by closeness, not a classification. Ties broken by entry id so
the layout is the same on every load. Recorded close pairs are not drawn; their state is in the entry panel.

**Names.** Suggestions only: tokens of at least 4 characters holding a digit, underscore, dot, slash or
hyphen, excluding dates and plain numbers, counted once per entry, in title and statement, shown when at least
2 entries share them. Besides it, the literal search of any name over title, statement, keywords and evidence.
No regular expression from the page.

**Usage log (§7), restated.** One line per call at the public boundary, written once: `recall`, `show`,
`memory_search`, `memory_read` (for the tools, after the result is cut to its limit; a call killed at its
deadline is logged by the parent as `timeout`). Fields: format version, time, session when known, project and
machine, surface, a call id, vaults actually read, limit, the query text and its number of words, refs returned, outcome (`ok`,
`empty`, `error`, `timeout`). Searches and direct reads are kept apart. Append with `O_APPEND` in one write;
pruning under a lock with a bounded wait; readers filter by date and skip incomplete lines. The page says
"returned by a search on this machine since <date>": no line is "never", it is "not seen in this window";
hook deliveries are not in it.

**Tests.** A fixture starts the server with `--no-open --port 0`, reads the printed address, and stops it
always. HTTP is driven with the standard library: token, `Host`, `Origin`, method, an unfinished request that
must not block others, a slow query, a vault outside the project, a failing vault beside a working one, long
and hostile text in every field, no row changed in the database. The script is checked for syntax where `node`
exists and is otherwise reviewed by reading; rendering is checked by a person in a browser and the check is
written down. The wheel is built and the three resources are read from the installed package.
