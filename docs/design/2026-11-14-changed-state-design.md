# Changed since: marking an entry whose state a later entry shows has changed

Status: **withdrawn 2026-11-14 after its challenge** (Codex gpt-6.1-sol, high: 16 objections, verdict "redo";
checked against the code). Kept as the record of why it was not built; see §9.
Owner's request (2026-11-14): after the measurement of stale entries, prepare the narrow change it points to
instead of a larger consolidation.

## 1. What was measured

- Staleness (2026-11-14, `~/.local/share/val3/eval/staleness-2026-11/results.txt`): 120 live entries older than
  three days from two vaults, each with the later entries closest to it, two blind judges. Outdated by a later
  entry: 16; would mislead if delivered today with its date: 6 (11 by either judge); contradicted: 1. By nature:
  records of one run or measurement 67, 10 outdated, 1 misleading; descriptions of how something is 27, 4 outdated,
  all 4 misleading (a host's mounts, a firewall rule, a gate that no longer blocks, a database since dropped);
  decisions, rules and causes 14, none. A floor: 38 entries had no later entry about the same thing.
- Settling (plan 13, 2026-11-08): of 47 past `replace` / `drop` settlements 5 lost a detail of the older entry
  that still held; of 48 sampled `keep` pairs 11 were successive readings of one thing. Settlements end in `keep`
  in 85 to 99 % of the pairs.

So the harm is narrow: an entry that describes a state stays live and unmarked after a later entry shows the state
moved on. Today the only outcomes of a pair are to replace the older entry, which is refused unless the newer one
restates everything that still holds, or to keep both as if nothing had changed.

## 2. Goals

- **G1** A third thing can be said about two entries: the newer one shows that what the older one states has
  changed. Both stay live; the older one carries a mark.
- **G2** The mark is shown wherever the older entry is delivered or read, and names the newer entry.
- **G3** Capture says it by itself when the slice shows the change; the agent can say it, and can clear it.
- **G4** An entry so marked is never pushed to the agent unasked.

## 3. Non-goals and anti-goals

- No stored "nature" of an entry (event, state, durable). The measurement classified entries, but the decision
  that matters is about a pair, and the settler already reads both entries whole.
- No automatic replacement and no merging of entries.
- No sweep of the pairs already settled. It is the next step and is measured first (§7).
- No change to search ranking.
- Anti-goal: marking the record of one run or measurement as changed because a later run exists. A later run
  does not make the record of an earlier one false; 10 of 67 such entries were outdated and 1 misleading.

## 4. Design

### 4.1 The relation

A new vault part, `changed.sql`, with one append-only table:

```sql
CREATE TABLE changed (
    old_id bigint NOT NULL REFERENCES entry (id),
    new_id bigint NOT NULL REFERENCES entry (id),
    noted_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (old_id, new_id),
    CHECK (old_id <> new_id)
);
```

It is its own table and not a fourth outcome of `settlement` because a settlement is one row per neighbour pair,
for good, and exists only for pairs at or above the neighbour threshold: a pair already settled `keep` could not
be corrected, and two entries below the threshold could not be related at all. `val3 vault upgrade` adds the
part; `CATALOGUE_VERSION` goes up; a mirror carries the table like any other part.

An entry is **marked changed** while it is live and has a `changed` row whose `new_id` is live and which is later
than the entry's latest confirmation. So the mark goes away when the entry is superseded or withdrawn, when the
newer entry is, or when the agent confirms the older entry with fresh evidence (`val3 confirm`, which already
means "I checked, it still holds").

### 4.2 Who writes it

- **Capture.** The settler prompt (version 3) gains a decision `changes`, with refs: *the candidate reports a
  later value, status, location or existence of what each named close entry states as the way something is, and
  does not restate the rest of that entry.* The candidate is admitted as a new entry; each named pair gets a
  `changed` row noted by `extractor <session>` and is settled `keep`; the other compared pairs are settled `keep`
  as today. The prompt keeps `replaces` for a candidate that restates everything that still holds, and says
  explicitly that the record of one run, attempt or measurement is never changed by a later run. The rule that an
  old slice never acts on a newer entry (K2a) applies to `changes` as it does to `replaces`.
- **The agent.** `val3 settle NEW OLD --changed`: writes the row noted by the agent; if the pair is open it is
  settled `keep`; a pair already settled, or two entries that are not a neighbour pair, are accepted (same vault,
  same kind, both live, NEW admitted after OLD). The line printed: `<old> marked changed since <new>`.
- No evidence is asked beyond what the two entries already carry, as for the other outcomes of `settle`.

### 4.3 Where the mark shows

- `val3 show <old>`: a line `CHANGED SINCE: see <new> (noted <date> by <who>)` after the title, next to the
  re-check and settle lines. `val3 show <new>`: `changes: <old>` under `supersedes:`.
- Session index and `val3 index`: ` · changed since (see <new>)` after the title, like ` · to re-check`.
- `memory_search` and `memory_read`: `changed since: see <new>` among the marks of the header line. It is one of
  VAL3's own values (a ref), not text of an entry.
- `val3 recall`: the same mark at the end of the entry's first line.
- Prompt injection (`val3 hook prompt`) skips an entry marked changed. An entry found by an explicit search is
  shown with its mark; an entry pushed unasked must not be a state known to have moved.
- `val3 status`: `changed since: <n>` in the vault's line when there are any.
- The settle header shown to the agent names the third outcome.

Marks are computed for any vault the project reads, own or linked: the relation is stored in the vault, unlike
the re-check mark, which needs the project's git checkout.

## 5. Tests (black box)

Relation: upgrade adds the part to an old vault and is idempotent; a mirror of a vault with rows carries them.
`settle --changed` on an open pair, on a pair settled `keep`, on two entries that are no pair; refused across
vaults or kinds, on a not-live entry, when NEW is older than OLD, and twice for the same pair. The mark appears in
`show` of both entries, the index, `recall`, both memory tools and `status`; it goes away when the older entry is
confirmed afterwards, superseded or withdrawn, and when the newer one is withdrawn. Prompt injection skips a
marked entry that would otherwise be injected and still injects it after a confirmation. Capture with the fake
extractor answering `changes`: candidate admitted, row written, pair settled `keep`, K2a respected; a malformed
`changes` (ref that is not a close entry) leaves the pair open as other malformed decisions do.

## 6. Acceptance: the prompt measured before rollout

The risk is the prompt, not the plumbing: a `changes` given too easily marks sound entries, given too rarely the
change does nothing. Before rollout, with the replay kit of the settler (`~/.local/share/val3/eval/settle-v2/`):

- **Set.** 60 stored pairs with the slice of their newer entry: the pairs behind the 16 outdated entries of §1
  that have one, plus `keep` pairs drawn at random from the two vaults. Each labelled by two blind judges as
  *changed state*, *later run of the same thing*, *different thing* or *restated in full*, before the prompt is
  run on them.
- **A1 precision.** Of the pairs the prompt answers `changes`, at least 8 in 10 are labelled *changed state*, and
  no more than 1 in 10 is a *later run*.
- **A2 recall.** It answers `changes` or `replaces` on at least half of the pairs labelled *changed state*.
- **A3 no regression.** On the labelled set of the version 2 replay the counts of `repeat` and `replaces` do not
  get worse beyond that kit's run-to-run spread.

Failing A1 the prompt does not ship; the relation and `settle --changed` may ship without it. After rollout the
marks written by capture are read weekly for a month (TODO).

## 7. After this plan

A one-off pass over the pairs already settled `keep` (661 in one vault), asking the same question of each. First
measured on a sample: how many would be marked, and at what cost per pair.

## 8. Decisions asked of the owner

- D1 Prompt injection skips marked entries (recommended) or shows them with the mark.
- D2 The cost of §6: about 120 settler calls on the extractor models in use and 240 gradings.

## 9. Why this was not built (challenge of 2026-11-14)

The objections that decide it, each checked:

- **The figures of §1 do not select this solution.** They come from two vaults, a sample stratified by the
  presence of a date, later entries taken from similarity 0.60 (capture pairs entries from the configured
  threshold, 0.70 to 0.75), and no host was checked. They show that some entries describing a state are out of
  date, not that a mark would help more than relaxing `replaces`, better settling instructions, or nothing.
- **The benefit claimed in G4 hardly exists as deployed.** Since plan 14 the adopters run with prompt injection
  off; the mark would only matter if the agent reads it in a tool result, which is not measured.
- **Excluding the marked entry loses what still holds in it**, which is the reason given for not replacing it.
- **The mark is not stable**: it disappears when the newer entry is itself superseded or withdrawn (the old state
  does not become true again), a confirmation of the older entry clears every relation at once, a pair cannot be
  marked again, and the timestamps the definition compares are not taken in a consistent order under the writer
  lock.
- **One decision per candidate cannot express** a candidate that replaces one neighbour, changes another and is
  undecided on a third.
- **It removes the neighbour requirement of `settle` without asking for evidence**, so one wrong call by the
  agent silences an entry, also one confirmed after the newer entry was admitted.
- Recipes that depend on a marked entry, mirrors across the catalogue change, permissions on the new table and
  the cost of computing marks in the session index were all left open.

The mechanism costs a table, a prompt decision, a new settling path and these cases, against 6 misleading entries
in 120 that are now only reached through a search whose results carry their date and the notice that the memory
may be out of date. Decision: not built. What stays open is in `TODO.md`: watch whether out-of-date states appear
in what the tools return, and only then measure the smallest remedy (for instance, naming in a search result the
newer close entries of the same vault, from the pairs already stored, with no new relation and no judgement).
