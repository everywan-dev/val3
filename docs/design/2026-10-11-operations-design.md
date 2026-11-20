# VAL3 — operations: terminal review and database backup

Date: 2026-10-11. Status: **approved by the owner 2026-10-11, after an external review (Codex,
gpt-6.1-sol: accept with changes, no critical findings) whose changes are applied.** Extends sections 7 (store), 10 (review list) and 11 (scope) of `2026-10-08-val3-design.md`.
Implemented by plan 3. `val3 import` (validated-memory 2.x) moves to a separate plan 3b.

## 1. Problem

The first adopted project accumulated 14 pending review items in three days. Deciding them took a
list, one `val3 review show` per item, and, for each supersession notice, two `val3 show` calls by
hand: `review show` prints a supersession as one line (`invoices#20 supersedes invoices#5`). Review
numbers (`#9`) and entry numbers (`invoices#9`) were confused. Of the 14, 13 were decided in one
sitting once the cards were assembled by hand; one tool entry (`#15`) was found false only because
its reproduction was re-run before approval.

There is no backup command. The development database was backed up by hand with a `pg_dump` run
inside the server container, because the client on the development machine is PostgreSQL 14 and
the server is 16, and `pg_dump` refuses to dump a newer server.

## 2. Decisions

| # | Decision | Rejected alternative |
|---|---|---|
| D1 | Review in the terminal only: `val3 review --tty`. | Local web page with a token (design §10): an HTTP server, a token and cross-origin protection on every decision; deferred to a later version. |
| D2 | A reproduction is shown, never executed. | Offering to run it after confirmation: reproductions are copied from transcripts and some write (one captured reproduction was a `POST` that creates a repository on a code-hosting server); no reliable way to tell reads from writes. |
| D3 | Line dialogue with `input()` (superseded by ADR 0004). | `curses`: not testable as a subprocess without a pseudo-terminal, three times the code. |
| D4 | `val3 backup` dumps the whole database (every vault, `val3_common`, extension, privileges). | Per-vault `val3 vault backup` (design §7): a vault schema alone cannot be restored into an empty database, and during development the owner wants everything. |
| D5 | Backup by `pg_dump` with a version check; restore by documented `pg_restore`. | Own export through the driver plus `val3 vault restore`: a second format to keep in step with every schema change. |
| D6 | Fix the 17 deferred minors still present (two are already fixed), plus the approval/supersession race found in review (m20). | Leaving them for later plans. |

## 3. `val3 review --tty`

### 3.1 Interface

- `val3 review --tty` walks the home vault's pending items in id order.
- `val3 review` without options lists them as today; `show`, `approve` and `reject` stay for scripts.
- Refuses to start when `CLAUDECODE` is set to a non-empty value (the current guard, `cli.py`),
  with the current message (a person decides).

### 3.2 Card

One rendering function serves `--tty`, `val3 review show N` and the `val3 review` list.

- Header: `review #9 · supersession (notice) · invoices · 2026-10-09 · session 3f9a1c20`.
- Candidate items (`tool`, `recipe`, `user-confirmation`): kind, title, statement, evidence (kind,
  quote, `reproduce: <command>  (not run)`); recipes add parameters, steps and examples; tools add
  type and environment. A candidate with `supersedes` shows `approving supersedes invoices#N` and
  the full `BEFORE invoices#N` entry, since approval performs that supersession.
- `supersession` notice: the old entry (`BEFORE invoices#5`, found through
  `supersession.new_id = review_item.entry_id`) and the new one (`NOW invoices#20`), both in full as
  `val3 show` prints them, so a later supersession of the new entry shows as `SUPERSEDED by …`.
- `recipe-cites-superseded` notice: the recipe, the old fact and the new fact. New notices carry
  structured ids in their payload (`recipe`, `old`, `new`) next to the title; a notice without them
  (none exist today) shows its title only.
- Entries are always written `<vault>#<id>`; review items always `review #<n>`, in the list too.
- Terminal safety: every text taken from the vault (titles, statements, quotes, reproductions,
  payload fields) is printed with control characters escaped (ESC shown as `\x1b`), keeping only
  newlines and tabs, and with format characters (Unicode Cf: bidi overrides, zero-width) escaped
  (`\u202e`). Candidates come from transcripts; an ANSI or OSC sequence must not be able to
  redraw the card a person approves, nor a bidi override reorder it.

### 3.3 Dialogue

Superseded on 2026-10-13 by `2026-10-13-review-tui-design.md` (ADR 0004): `val3 review --tty` is a
full-screen interface with marks. The text below describes the line dialogue it replaced.

- The walk takes the pending ids once at start, in id order.
- Prompt per item: `a` approve, `r` reject (not offered for notices), `s` skip, `q` quit; end of
  input means quit; anything else asks again.
- Each decision calls the writer's `decide()` (one transaction) and then stores vectors as
  `val3 review approve` does today.
- A decision refused as stale (already decided, a cited entry superseded meanwhile, a notice
  answered with reject: the writer's `Val3Error`) prints the reason, counts as failed and moves on.
- A database error (connection, permission, any `psycopg.Error`) stops the walk: summary, then
  exit 1.
- At the end: `N approved, M rejected, K skipped, F failed`, and `P new items pending` when
  decisions created notices during the walk (approving a superseding candidate does).
- Exit `0` when the walk ends, skips and stale refusals included; `1` on a database error.

## 4. `val3 backup`

### 4.1 Interface

`val3 backup [--service S] [--out PATH]`; the service defaults to the project's `val3.toml`. It
replaces `val3 vault backup` in design §7. The service's role needs read access to the whole
database (every vault and `val3_common`), beyond ordinary project grants: in practice the owner.

### 4.2 Behaviour

1. Find `pg_dump`; compare its major version with the server's `server_version_num`. Client older
   than server: exit 1 with `pg_dump 14 cannot dump a PostgreSQL 16 server: install
   postgresql-client-16`. Missing `pg_dump`: exit 1. A newer client is accepted; the README
   recommends matching major versions for `pg_dump` and `pg_restore`.
2. Run `pg_dump -Fc --no-password <conninfo>` with an argument list (no shell), the conninfo built
   with `psycopg.conninfo.make_conninfo(service=S)`. The password comes from `.pgpass`; with no
   usable password it fails instead of prompting.
3. Output path: `~/.local/share/val3/backup/<database>-<YYYYMMDDTHHMMSSZ>.dump`, or `--out`. The
   database name is checked against `[A-Za-z0-9_-]+` before it becomes part of a file name. The
   default directory is created 0700. The file is created with `O_CREAT | O_EXCL` and mode 0600
   before `pg_dump` writes to it (an existing file or symlink is refused). On any failure, including
   interruption, the partial file is removed and the command exits 1 with `pg_dump`'s stderr.
4. Print the path, the size and the vaults it holds.

### 4.3 Restore (README, no command)

- Prerequisites on the target server: the same major version, the `unaccent` and `vector` extension
  packages installed, a role allowed to create them (usually a superuser), and the roles the vaults
  are granted to (`vault grant` does not create roles). `pg_service.conf`, `.pgpass` and each
  project's `val3.toml` are not in the dump and are recovered separately.
- `createdb -T template0 <new>`, then `pg_restore --single-transaction --exit-on-error -d
  "service=<new>" <file>`. Use the restored database only when `pg_restore` exits 0.
- Missing roles: either create them first, or restore with `--no-owner --no-privileges` and grant
  again with `val3 vault grant`.
- VAL3 never restores over the live database.

## 5. Deferred minors

From the plan 1, 2 and 4 session-log entries, checked against the code at 0900d40; refined after the
external review.

Already fixed: duplicate race loses the created flag (writer returns `duplicate`); the extractor
deduplicating reruns (per-session watermark and existing entries passed to the extractor).

| # | Fix | Test |
|---|---|---|
| m2 | Saving a candidate whose review item was rejected reports `previously rejected: review #n` instead of `already queued`, and does not queue it again. The new outcome joins the extractor's status counts and summary. | CLI: `save` and extraction after a rejection |
| m3 | Queued user messages (`attachment` of type `queued_command`) count as user text, except harness turns (`<task-notification>` and the other harness tags); bash-mode output (`<bash-stdout>`) counts as command output, not user text. The same normalisation feeds evidence checking and the chunk text used for search, which also gains user messages made of text blocks. | CLI (transcript fixture): queued text, bash output, each harness tag, through evidence and search |
| m4 | Only the invalid/discard log becomes best effort; admission and decision logging stay in their transactions. With a read-only home grant the real invalid or discard message is shown. | CLI (reader role), invalid and discarded candidates |
| m5 | Both hook connections set `statement_timeout` to 2 s; a lock becomes the "unavailable" warning. This bounds each statement, not the whole hook. | CLI (lock held by the test) |
| m6 | `vault create` runs `common.sql` only when `val3_common` is missing. When it exists but lacks what the vault needs (the `vector` extension, current functions), `vault create` exits 1 telling the owner to run `vault upgrade`. `vault upgrade` keeps running `common.sql`. | CLI: non-owner role; `val3_common` without `vector` |
| m8 | Unknown-origin inbox notes are capped, newest first: 20 notes, 40 000 characters in total. Only the selected notes are settled; the rest stay eligible for the next extraction. | CLI: both caps, then the excluded notes processed later |
| m9 | `.consolidate-lock` counts as stale only when it names a holder process that does not exist on this machine. A live holder blocks whatever the lock's age; an unreadable lock or a PID check that is refused also blocks. | CLI: dead holder, live old holder, malformed lock |
| m10 | A transcript shorter than its watermark resets the watermark to 0 before chunks are chosen, and the stored watermark becomes the new line count. | CLI: shrink, append, extract again |
| m11 | A failure to spawn the drainer warns and still delivers the session index. | CLI: the subprocess gets a `sitecustomize` on `PYTHONPATH` that makes the drainer's `Popen` fail (no import of VAL3); assert the warning and the index |
| m12 | `val3 extract` waits for the drain lock and holds it through extraction, watermark writes and settlement. | CLI: lock held by the test, released, extraction completes |
| m13 | Chunks without user text search existing entries with their own text (first 5 000 characters, skipped when blank). | CLI: a tool-only chunk retrieves an existing entry |
| m14 | `add_tool` returns an `Outcome`; the CLI branches on its status. Output unchanged. | existing tests |
| m15 | One monotonic 10 s deadline shared by the digest lookup and the embed calls after an admission; the fake embedder learns to delay `GET /api/tags`. | CLI: cumulative GET and POST delays; the admission stays, with a warning |
| m16 | `RecursionError` handled wherever JSON is read: the hook's event JSON, `last_assistant_text`, the transcript loader, eval loaders, prompt-log pruning. Each reader keeps its current skip or error behaviour. | CLI |
| m17 | `val3 eval` rejects labelled refs to vaults the project does not declare (home or linked), before embedding. | CLI |
| m18 | `val3 eval` errors about a record carry `file:line` (m17 included); file-level errors (unreadable file, empty set) name the file. | CLI |
| m19 | Semantic design: title without "draft v2"; §4.2 "per embedding space" instead of "configured model". | docs |
| m20 | Approving or admitting an entry can miss a supersession committed in parallel: `_require()` reads the citation's state without a lock, so a recipe can be admitted citing a fact superseded meanwhile, with no notice. Every writer transaction that admits, supersedes or decides takes `pg_advisory_xact_lock` on the vault first, so these transactions run one at a time per vault. | CLI: approval and a superseding save from two connections; either the approval is refused or the notice exists |

## 6. Documentation

- Main design: §7 backup is `val3 backup` of the whole database; §10 terminal review only; §11
  `val3 import` in plan 3b; §12 adds the web review page as deferred work.
- README: review section (`--tty`) and backup/restore section (4.3).
- `TODO.md`: plan 3b (`val3 import`); installing `postgresql-client-16` on the development machine.

## 7. Testing

- CLI as a subprocess against the disposable PostgreSQL, fixture trees, no imports of internals.
- `--tty`: stdin `a\nr\ns\nq\n` with assertions on output and on `review_decision` and `event`
  rows; `r` on a notice; an item decided by another connection mid-walk (counted as failed, exit
  0); input ending mid-walk; a candidate with `supersedes` (BEFORE shown, new notice reported);
  `review show` of a supersession prints both entries; a title holding CSI and OSC sequences is
  printed escaped; so is a title holding bidi overrides and zero-width characters.
- Backup: a fake `pg_dump` on `PATH` reporting version 14 (exit 1, message); file mode 0600; an
  existing file at the output path is refused; a failing `pg_dump` leaves no file. Round trip with
  two vaults (entries, evidence, a supersession, a decided review item, a vector, a read grant),
  restored with `--single-transaction --exit-on-error` into a new database of the test container:
  `val3 show` and `val3 recall` equal in both; grants and supersession links present; an UPDATE is
  still refused; a new `val3 save` gets a fresh id. The round trip is skipped with its reason when
  the local `pg_dump` is older than the server.
- Done when the full suite passes with no skips other than that one. Deciding the remaining pending
  item of the first adopted project with a real `--tty` session is the owner's smoke test, not a
  gate.
