# VAL3 — full-screen terminal review

Date: 2026-10-13. Status: **approved by the owner 2026-10-13.** Replaces section 3.3 (dialogue) and decision D3 of
`2026-10-11-operations-design.md`; section 3.2 (card content) stands. ADR 0004.

## 1. Problem

The first import put 43 candidates on one review list. `val3 review --tty` shows each card as plain
text and asks one question per item: titles, labels, quotes and warnings look alike, and a person
cannot see the list as a whole, mark several items and decide them together.

## 2. Decisions

| # | Decision | Rejected alternative |
|---|---|---|
| T1 | `val3 review --tty` becomes a full-screen `curses` interface and replaces the line dialogue. | Numbered list and ranges typed on one line (`a 1-5,8`): no marks moved with the cursor. A local web page: an HTTP server, a token and cross-origin protection (operations design D1, still deferred). |
| T2 | Marked items are decided together after a confirmation that counts the marked items with no evidence and those whose card was not opened. | Approval only of opened cards: slower, the owner prefers a warning. No confirmation: a wrong mark becomes admitted entries. |
| T3 | Style is chosen per line by its val3 prefix, after the text is escaped. | Structured rendering (style per segment): changes every renderer for the same result on screen. |
| T4 | Tests drive the CLI inside a pseudo-terminal (`pty`, standard library). | Keeping the line dialogue for tests: two interfaces to keep in step. |

## 3. Interface

- `val3 review --tty` refuses to start when `CLAUDECODE` is set to a non-empty value (unchanged), and
  exits 2 with `val3 review --tty needs a terminal` when stdin or stdout is not a terminal.
- `val3 review`, `review show|approve|reject` are unchanged; scripts keep using them.
- `NO_COLOR` set to a non-empty value: bold, dim and reverse only, no colours. A terminal without
  colours gets the same.
- The pending ids are read once at start, in id order, as today.

### 3.1 List screen

One row per pending item:

```
 review · invoices · 43 pending                                    2 marked
 > [x] · #1   decision                Invoices are validated against …  ✓ snapshot
   [x]   #2   fact                    Scanner runs on the batch host    ✓ snapshot
   [ ]   #3   fact                    Margin check uses 2 decimals       ✗ unsupported
   [ ]   #4   supersession (notice)   Deploys go through the MR pipeline
 ↑↓ move · space mark · * all · enter open · a approve · r reject · q quit
```

- Columns: mark, `·` when the card was opened in this walk, `#id`, kind (the candidate's kind, or the
  reason for notices, with `(notice)`), title cut to the width, evidence.
- Evidence column: for imported items, as the README measurement query: `✗ unsupported` (payload
  evidence empty), `✓ snapshot` (a verified quote unchanged at the snapshot), `~ changed` (verified,
  changed since); for captured candidates, `N evidence` or `✗ no evidence`; empty for notices.
- Keys: `↑ ↓ j k` move, `PgUp PgDn Home End`, `space` toggles the mark of the current row, `*`
  marks every row or, when all are marked, clears the marks, `Enter` opens the card, `a` approve the
  marked items, `r` reject them, `q` quit.
- `a` or `r` with no marked item applies to the current row, with the same confirmation.
- Before deciding marked items the bottom line asks, e.g. `approve 12 marked? 3 without evidence, 2
  not opened · y/n`; only `y` goes on, anything else cancels. The counts are omitted when zero.
- Decided items leave the list. The status line keeps the last result: `12 approved, 1 failed:
  review #7: <reason>`.
- When a vector cannot be stored after an approval, the warning shows on the status line during the
  walk and is printed once to stderr after the walk ends.
- Rejecting a marked notice is refused by the writer and counts as failed (unchanged behaviour).

### 3.2 Card screen

- The card is `review_card()` as today (section 3.2 of the operations design), scrolled in a pad.
- Top line: `review #9 · 3/43 · [x]`; bottom line: the keys `↑↓ PgUp PgDn scroll · space mark · a approve ·
  r reject · n next · p previous · q back`.
- `a` and `r` decide this item alone, without confirmation, and return to the list; `n` and `p` move
  to the next or previous pending item; `q` or `Esc` return to the list on the same row.
- Opening a card records it as opened (the `·` of the list and the confirmation count).

### 3.3 Style

Applied per line, after `safe()` has escaped the text, from the line's start:

| Line | Style |
|---|---|
| first line of the card (`review #…`) | bold |
| `[kind]  title` | bold |
| `SOURCE`, `BEFORE`, `NOW`, `RECIPE` blocks' first line | reverse, bold |
| a label before `:` at the start of a line (`evidence:`, `steps:`, `keywords:`, `indexes:`, `parameters:`, `examples:`, `origin:`, `tool:`, `environment:`, `observed:`, `supersedes:`) | label in cyan, value normal |
| quote (`> …` after the indent) | green |
| `reproduce: … (not run)` | dim |
| `no repository evidence: approving attests it`, `not verified:`, `SUPERSEDED by …`, `approving supersedes …` | yellow, bold |

Known limit: a statement line that starts with one of these prefixes gets the same style. Text that
imitates card structure is already possible in plain text; colour does not add to it.

## 4. Decisions and errors

- Each decision calls the writer's `decide()` (one transaction) and then `after_approval()` (vectors),
  as today; marked items are decided one after another in id order, each in its own transaction.
- A `Val3Error` (already decided, a cited entry superseded meanwhile, a notice rejected) counts as
  failed and is shown in the status line; the item stays in the list.
- A `psycopg.Error` restores the terminal, prints the summary and propagates (exit 1).
- `KeyboardInterrupt` restores the terminal and quits like `q`.
- At the end, after the terminal is restored, stdout gets the summary of today (`N approved, M
  rejected, K skipped, F failed`, skipped being the items left undecided) and `P new items pending`
  when decisions created notices.
- `KEY_RESIZE` redraws; rows and titles are cut to the window width.

## 5. Testing

`tests/test_tty.py` runs `val3 review --tty` in a pseudo-terminal (`pty.openpty`, window size set
with `TIOCSWINSZ`, `TERM=xterm`), writes keys and reads the output until exit. Assertions on the exit
code, the final summary, `review_decision` and `event` rows, and a few strings of the screen.

Cases:

1. mark two items, `a`, `y`: both approved, vectors stored, summary `2 approved`;
2. mark, `a`, `n`: nothing decided;
3. the confirmation counts an unsupported imported item and an unopened item;
4. open a card, `r`: that item rejected;
5. a marked notice with `r`: failed, the notice stays pending;
6. a stale item (decided by `val3 review approve` after the walk started): failed with the reason;
7. stdin not a terminal: exit 2, the message;
8. `CLAUDECODE` set: refused as today;
9. Ctrl-C (SIGINT): terminal restored, summary printed, exit 0. (End of input cannot be produced in a
   pseudo-terminal whose reader is the test itself; `getch()` returning -1 quits like `q`.)
10. control and format characters in a title are shown escaped on the list and the card;
11. a window of 60 columns cuts rows without error;
12. a card longer than the window scrolls to its last line.

## 6. Documentation

- ADR 0004 (supersedes operations design D3).
- Operations design §3.3: pointer to this document.
- README, Reviewing section: keys and the confirmation.
