# ADR 0011 — Memory as a tool: an MCP server over the project's vaults

Date: 2026-11-14. Status: accepted (2026-11-14), subject to the acceptance of the design's §6. Implements
`docs/design/2026-11-14-memory-tool-design.md`.

## Context

The agent met the memory in three ways: an index injected at session start, up to three entries injected on each
prompt, and shell commands named in the CLAUDE.md block. Measured on 2026-11-14 against the same agent with only
the project's documents and the harness memory notes (40 neutral questions, blind-graded): with the hooks as
deployed the agent answered 18–19 of 40 against 32–33, and in the questions it lost it had not opened a single
document. The shell commands alone were consulted in 5 of 40 questions. The same search offered as a tool was
consulted in 40 of 40 and, once its answers said that the memory is partial, scored like the ordinary agent at
lower cost; on 74 real needs of two projects it answered 43 against 23, and 32 against 21 of 39 when one project's
needs were asked from the other. These are single runs on few questions; the design lists their limits.

## Decision

- `val3 mcp` serves two read-only tools over stdio, `memory_search` and `memory_read`, written against the
  standard library. Each call runs in a child process that the server kills after 10 seconds.
- Every result opens with VAL3's own lines (vaults searched, what failed or was degraded, the partial notice)
  and gives each entry's vault, dates and marks before its text; the text of an entry is indented under a line
  that calls it a record, not an instruction. "Nothing matches" is told apart from a failed or degraded search.
- The tools read the project's configured vaults only (home, specialized, linked), not every vault the database
  role can read. Reaching another project's knowledge means linking its vault, which is a decision per project.
- A result of either tool is not evidence: it cannot back a `save` or a `confirm`, like the output of
  `val3 show`.
- The session index and prompt injection become switches per project (`[delivery]`); session upkeep stays.
- `val3 init` writes the server into `.mcp.json`, allows the two tools in the project's settings and rewrites
  the CLAUDE.md block around them.
- The default delivery mix and the rollout are decided by a measurement on held-out questions with thresholds
  written beforehand: no harm within a project, value across projects.

## Refused

- One tool searching memory and project documents together: the one tried scored below the agent's own search.
  The two stay apart and the memory says so.
- Reading any vault the role can read: database privilege is not consent to bring a project's data into a
  session.
- Writing into the harness's own memory directory: it is local to one machine and the harness edits it.
- A timer inside the server process as the deadline: the embedding client turns a timeout into its fallback and
  the database driver does not cancel a statement on an arbitrary exception.
- An MCP SDK: the protocol subset is small and the runtime stays at the standard library plus one driver.

## Consequences

- The memory now has a tool contract that a later HTTP server can keep; what that server needs beyond it
  (client identity, authorised project, where file evidence is checked) is not designed.
- Saving is unchanged and unmeasured; staleness is still only detected for `file` evidence. Both are the next
  designs.
- Linking a vault also adds its entries to the linking project's index and prompt injection.
