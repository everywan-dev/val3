# What the sessions looked for, and what the memory held (plan 15, part D)

Measured 2026-11-15 on four adopting projects, two machines, one owner. The kit (scripts, transcripts, verdicts)
is not published: it holds the projects' own sessions. Plan: `docs/plans/2026-11-15-val3-plan-15-usage-and-capture.md`.

## Question and rule

When an agent goes looking for something by itself, did the vault already hold it? Written before any result:
a small layer loaded at session start (part E) is designed only if the tool calls that a returned entry would
have saved are at least 5 % of the sessions' tool calls, and no wrong entry was acted upon.

## Method

- Transcripts touched since 2026-11-01, main sessions and subagents: 96 files, 21 609 tool calls.
- An episode is a run of at least 3 read calls of which at least 2 search (Grep, Glob, grep, rg, find, git log,
  git grep, git blame, a `select` through psql, a web search). The first rule was audited on 30 random cuts
  (half were routine reading), rewritten once as planned, and its 30 verdicts discarded.
- For each episode a model (Haiku) wrote the query from the text before the episode and its first call; the
  deployed search ran on the vault as it was at the episode's start (entries created and live by then).
- One blind judge (Sonnet) per episode: is it a search for knowledge or routine reading; did a returned entry
  hold what the agent then found or was told; how many calls it would have saved, minus one for the lookup.

## Result

| adopter | episodes | knowledge | held | partly | calls saved, net | of tool calls |
|---|---|---|---|---|---|---|
| third | 105 | 57 | 1 | 21 | 6 | 0.1 % |
| fourth | 74 | 23 | 0 | 7 | 0 | 0.0 % |
| sixth | 25 | 14 | 0 | 4 | 0 | 0.0 % |
| fifth | 357 | 226 | 2 | 59 | 19 | 0.1 % |
| all | 561 | 320 | 3 | 91 | 25 | 0.1 % |

**0.1 % against the 5 % asked: part E was not designed.** The answer is the same at 3 % and 8 %. When an agent
goes looking by itself, the vault held what it then found in 3 of 320 knowledge episodes, and something related
in 91.

- **Hindsight ceiling.** On the 317 knowledge episodes not judged "held", the query was written again from the
  whole episode and its outcome: held 4, partly 74. It is not the query: what the sessions look for by
  themselves was never captured.
- **Second judge** on 56 random episodes: same kind in 53, same "held" in 52.

## The consultations the agents did make

All 365 memory consultations in the same transcripts (command line and memory tools), one judge each:

| useful | partly | not useful | replaced a search | wrong or out of date returned | acted upon a wrong entry |
|---|---|---|---|---|---|
| 116 | 133 | 116 | 107 | 4 | 0 |

Two of the four wrong returns were consultations made to correct that very entry. Where agents consult the
memory, two in three answers help at least in part, and no harm was seen.

## Limits

The episode rule sets the denominator. One judge per episode (a second on a tenth). Needs never searched for
are invisible. Four adopters of one owner. Cost of the whole measurement: about $26.
