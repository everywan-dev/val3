# Notes: an agent loop that improves VAL3 from its own use (2026-10-31)

Not a design. Owner's question: could a flow of specific agents keep improving the project on its own?
Answer recorded here before a design is written.

## The loop already exists, run by hand

The settler prompt v2 cycle (2026-10-30, ADR 0009 amendment) went: observe the adopters, measure, label cases by
hand, propose a change, challenge it (Sol 6.1), replay it over real slices, run the suite, merge.

| Step | Automatable | Measured cost |
|---|---|---|
| Observe (`val3 status`, `extract.log`, `settlement`, events) | yes, read-only | ~0 |
| Label cases (the ground truth) | not safely, see below | — |
| Propose a change (prompt, threshold) | yes | session tokens, not measured |
| Challenge (Sol 6.1) | yes, recipe known | one Codex run |
| Replay over real slices (`~/.local/share/val3/eval/settle-v2/replay.py`) | yes | ~$0.007 per candidate per prompt version; $0.81 on 2026-10-30 |
| Suite | yes | 8 min, $0 |
| Merge | risky: `main` is the live install of four adopters | — |

For scale: capture cost $1.00 in 14 days (40 extractions, ~$0.025 each). A weekly observe-and-replay cycle costs
under $1 in model calls; implementer and reviewer sessions for code changes are the expensive part, unmeasured.

## Where "recursive" breaks

1. **Ground truth cannot come from the proposer.** The 12 labelled candidates of 2026-10-30 were labelled by the
   same model that wrote v2 (Sol's objection 6). A loop that labels, proposes and judges optimises toward its own
   judgment.
2. **Run-to-run instability.** 6 of 26 candidates got different answers from two runs of the same prompt. Any
   automatic gate needs repeated runs and a margin; small gains stay inside the noise.
3. **Design rulings belong to the owner.** C1-C3, the threat model and append-only storage are not reopened by
   the loop; it tunes what fits within them.

## A quality signal that needs no hand labels

VAL3 records later corrections: `withdraw --wrong` / `--obsolete`, `settle --replace` / `--drop` on what capture
admitted, supersessions by the agent. Each marks an earlier capture decision as wrong. The rate of capture
decisions later corrected is a metric without labelling. Limit: it depends on the agent correcting; on
2026-10-28 the agent settled 9 pairs, all `--keep`, 3 wrong.

## Proposed phases

1. **Weekly observer**, read-only: metrics (later corrections, open marks, `repeat` / `replaces`, cost) and
   suspicious cases; opens items in `TODO.md`. Near-zero risk.
2. **Gate for prompts and thresholds**: a frozen set labelled by the owner and never used for design, two runs,
   no new silent error allowed. Only `settle_prompt.md`, `extractor_prompt.md`, the 0.60 floor and the 0.75
   neighbour threshold.
3. **Code changes** with the plan 8 split (Opus implements; Sol and the architect review). Merge on the owner's
   approval or a passed phase 2 gate, after a period on the delivery-only adopter before the other three.

The gain cannot be estimated until phase 1 has run for a few weeks. Next step if taken up: design of phase 1.
