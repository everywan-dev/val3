# ADR 0004 — Review in a full-screen terminal interface

Date: 2026-10-13. Status: accepted. Supersedes decision D3 of `docs/design/2026-10-11-operations-design.md`.

## Context

`val3 review --tty` was a line dialogue with `input()` (D3): `curses` was rejected because it could not
be tested as a subprocess without a pseudo-terminal, for three times the code. The first import put 43
candidates on one review list. Read as plain text, one card at a time, titles, labels, quotes and
warnings look alike, and a person cannot see the list, mark several items and decide them together.

## Decision

- `val3 review --tty` is a full-screen `curses` interface (standard library): a list with marks, a
  styled card for each item, and decisions on the marked items after a confirmation that counts the
  marked items with no evidence and those whose card was not opened.
- It replaces the line dialogue. `val3 review show|approve|reject` stay for scripts.
- Style is applied to val3's own line prefixes after the vault text is escaped; vault text never
  carries terminal sequences.
- Tests drive it in a pseudo-terminal (`pty`, standard library).

Detail: `docs/design/2026-10-13-review-tui-design.md`.

## Consequences

- Deciding many items at once makes approval faster than reading; the confirmation names how many of
  the marked items were not opened and how many carry no evidence.
- The review command needs a terminal; without one it exits 2.
- `tests/test_tty.py` is rewritten around a pseudo-terminal.
