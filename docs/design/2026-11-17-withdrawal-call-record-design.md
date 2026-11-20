# Withdrawal evidence: the calls that printed the quote

Status: decided 2026-11-17, after its challenge (Codex gpt-6.2-astra, `medium`, read-only: 8 objections,
1 blocking, 4 high; section 9). Owner's decision on section 5: the hash, never the command's text.

## 1. Why

A direct withdrawal with tool evidence takes an entry out of delivery for good, at once, on the agent's word
that a quote bears on it. The writer checks that the quote is in the output of some call whose input does not
contain it (`writer._quote_failures`). It stores `kind`, `reproduction` and `quote` in `withdrawal.evidence`.
`reproduction` is the agent's description ("status of both images from the first node") and is compared with
no call. So after a mistaken withdrawal nobody can see from the vault which command gave the line: a true line
of a query about another object looks the same as the right one.

Not the aim: deciding whether the quote bears on the entry. That stays the agent's judgement (ADR 0006,
amendment 1). The aim is that the attribution can be examined afterwards.

## 2. Decision

When `writer.withdraw` carries out the withdrawal of one entry (`ref`) with evidence, each item whose kind is
not `user` is stored with one more key, `calls`, written by the writer:

```json
{"kind": "query", "reproduction": "…the agent's words…", "quote": "…",
 "calls": [{"tool": "Bash", "id": "toolu_…", "input_sha256": "…", "reproduction_is_command": true}]}
```

- **Which calls: all that qualify, no winner.** The qualifying calls are those the check accepts today (the
  quote in the output, not in the input, not excluded by origin). With one, it is the call. With several, all
  are listed: when two queries about different objects print the same line, the other one is what a reviewer
  needs to see, and the order of results in the transcript is not the order of the calls. Distinct calls are
  told apart by `tool_use` id (a result repeated under one id counts once) and typed `!` commands by their
  record position (`"tool": "!"`, `"id": null`, `"record": n`). At most 20 are stored, with `"more_calls": n` on the item.
  An agent that wants to point at one call quotes a line only that call printed.
- **What identifies the input**: the tool name and a SHA-256 of the complete structured input, serialized
  canonically (`json.dumps(input, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`); for a `!`
  command, of its whole body. The flattened string values the check compares against lose key names, numbers
  and structure (two `Read` calls with different offsets, an MCP call with source and target swapped), so
  they serve the check only.
- **`reproduction_is_command`** is true when the agent's `reproduction` equals, after trimming, the `command`
  of that Bash call or the body of that `!` command. Then the vault holds the real command in the agent's own
  words, checked by the writer, with no text copied that the agent did not write.
- **The agent cannot supply it.** The format of `--evidence` is unchanged; `candidate._evidence` refuses
  unknown keys, so a submitted `calls` is an error.
- **Shown** under the withdrawal, by `val3 show` and by the view's entry panel (which shows no withdrawal
  evidence today): each item's quote and description, then `printed by: Bash` with `(the command above)` when
  the flag is true, or `printed by N calls: …` when several.

## 3. Scope

- Only `writer.withdraw` for a `ref` with evidence: the agent's direct withdrawal and a person's with
  `--evidence`, on vaults upgraded to ADR 0012 or not (both reach `withdraw`).
- `submit`, `confirm` and the `evidence` table are untouched: one predicate over today's complete texts, with
  the same exclusions, messages, indexes and order of failures. Admission does not change.
- `settle --drop`, a batch, and the legacy path (`propose_withdrawal`, `decide`, which rebuilds the
  withdrawal from a stored payload without a transcript) write as today. The enrichment is made in
  `withdraw`, not in `_withdraw_entry`, which those share.
- `user` items record nothing more.

## 4. Storage and compatibility

- `withdrawal.evidence` is `jsonb`: additive, no `val3 vault upgrade`, no backfill. Rows written before keep
  their three keys and stay shown.
- Older code reading a newer row ignores the key (`views.withdrawal_evidence` reads by name). The mirror
  copies columns and checks at each refresh that earlier rows are unchanged: true between ends with the same
  columns, which this does not change.
- The row is append-only, mirrored, and readable by every role that reads the vault, linked projects
  included; the view's API returns the JSON whole.

## 5. The call's input as text

ADR 0006's amendment asks to keep "the call's real input" beside the quote. A real command can carry a
password or a token on its line (`PGPASSWORD=… psql`, a header, a positional credential, a value under an
unknown MCP key). Copied by the writer, it would sit in a permanent, mirrored row that no ordinary path can
clean.

- (a) store the input as text, cut at 2 000 characters. Readable for ever; a secret on a command line becomes
  permanent. The cut protects nothing at the start of the line.
- (b) store the hash only (section 2 as written). Nothing new is copied. While the transcript exists the call
  is found by id and read there; after the harness deletes it, the hash can only confirm a candidate input
  kept elsewhere, and a predictable value can be tried against it. The command is readable in the vault only
  when the agent wrote it as `reproduction` (the flag says the writer checked it).
- (c) text with masks for known forms. Misses forms; a false sense of safety.
- (d) refuse the withdrawal when the input looks like it holds a secret. A new reason to refuse what ADR 0012
  section 6 allows, with (c)'s misses.

**Decided (owner, 2026-11-17): (b).** The vault stores the tool, the call id and the SHA-256 of the
complete structured input, never the text of the command: a secret must not end in an append-only row that
is mirrored. No cut at 2 000 characters, no masks. With it, the instruction to the agent (help of `val3
withdraw`, the CLAUDE.md block):

- in a withdrawal, `reproduction` is the exact command that was run; the writer marks the item when it
  equals the real command (`reproduction_is_command`);
- `reproduction` carries no secrets (environment variables with passwords, tokens, headers): when the real
  command held one, the agent writes it again without it, and the item then gets no mark.

## 6. Code

- The transcript is read **once** per withdrawal. One evaluation per item yields either its failure or its
  qualifying calls; a failure goes to `_discarded` as today; on success `withdraw` passes an enriched copy of
  the `Withdrawal` to the present transaction (the given one is not mutated). The check that the entry can
  still be withdrawn stays under the lock. A second read could see an appended or replaced file and record a
  call that did not authorize the withdrawal.
- `transcript.evidence_calls` returns records (`tool`, `id`, `record`, structured input, flattened input,
  output) and replaces `evidence_text`, pair for pair and in the same order, so `submit` and `confirm` see
  what they saw (a pair still needs only a string id, not a known tool name; the input is flattened once per
  call).
- From the code review: a tool name is stored only when it is a plain name (letters, digits, `_.:!-`), an id
  in ASCII; one id declared with two inputs gives two records; the calls that ran the reproduction are
  listed first, so the limit of 20 never hides one; equality is after trimming only.
- `views.withdrawal_evidence` and `view.js` render the lines through the existing escapes (`_one_line`,
  `safe`; text nodes in the panel).

## 7. Tests (subprocess, as the suite does)

1. One qualifying call: the row holds its tool, id and hash; `reproduction` equal to the command sets the
   flag, a different description does not.
2. Two objects, the same output line: both calls listed, also with results in inverse order and with two
   result blocks under one id (counted once); an excluded call that prints the quote is not listed.
3. A typed `!` command: `tool` `"!"`, `id` null, its record position.
4. An item carrying `calls` in the input is refused, nothing withdrawn.
5. Hashes differ for a `Read` with another offset, a Bash call with another `description`, a nested MCP input
   with two values swapped; a quote that sits in the input beyond 2 000 characters is still refused.
6. A marker present only in a call's argument is absent from the row, the events, the view's API, `val3
   show` and the command's output. Not tested apart: a dump and a mirror refresh (the column and its type do
   not change; the mirror's own tests cover the table).
7. When the second item of a withdrawal fails: no row, no `withdrawn` event, the item's diagnosis kept. A
   last transcript record left incomplete. One unit-level test that fails on a second read of the transcript.
8. `user` and mixed lists keep their order; a row written before (three keys) still shows, in `show` and in
   the panel; a direct withdrawal on a vault that predates ADR 0012; `decide` on a legacy proposal stores the
   payload's items unchanged.
9. `save`, `confirm` and capture: same success, refusal and message as before on the existing cases, plus a
   capture with one failed quote, `confirm` with only `file` items and no transcript, and a valid call beside
   one excluded by `private`.

## 8. Limits kept

- The quote is still not compared with the entry or the reason.
- Text the agent wrote to a file and read back with another call still passes; the recorded call then is the
  read.
- The transcript is a local file the agent's user can write, and `--transcript` chooses it (ADR 0013).
- With (b), after the transcript is gone the vault alone does not show which object a call asked about,
  unless the agent wrote the command.

## 9. Challenge (Codex gpt-6.2-astra, `medium`, read-only, 2026-11-17)

Each point checked against the code before it was taken.

| # | Objection | Taken |
|---|---|---|
| 1 (blocking) | copying arguments puts a secret in a permanent, mirrored row; masks and the cut do not protect | section 5: (b), decided by the owner |
| 2 | "the last call" names a winner the writer cannot know; result order is not call order; one id can give two pairs | all qualifying calls, by identity |
| 3 | "the same transcript" needs one read; an append or a replaced file changes the answer | one read, one evaluation |
| 4 | the flattened strings lose keys, numbers and structure | hash of the canonical structured input |
| 5 | `submit` and `confirm` stay intact only with the same predicate, texts and failure order | section 3, tests 9 |
| 6 | the frontier is the direct withdrawal, upgraded vault or not; `decide` must not be enriched | section 3 |
| 7 | the view's panel shows no withdrawal evidence; a `ran` line under "Evidence" would mislead | shown under the withdrawal |
| 8 | tests missing | section 7 |
