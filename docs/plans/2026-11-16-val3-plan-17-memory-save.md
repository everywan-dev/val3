# Plan 17 — What the agent saves, and `memory_save`

Date: 2026-11-16. Status: stage 0 run, not settled by its rule; the tool is built and merged (stage 1), **with a defect found
after the merge**: where the tool is pre-allowed in an interactive session the transcript lookup cannot succeed
(ADR 0013, last section). Deployed without the permission; the design of a deferred evidence check is next. Owner, 2026-11-16: measure, then build.

## Why
- Plan 16 stopped: the references a capture can be tuned on do not stand for what later work looks for, and
  most of what sessions look for was never in an earlier session (stage 1b; plan 15, part D).
- Agents already save on purpose through the shell: in the transcripts of two adopters, 153 `val3 save` calls
  with 147 entries admitted (third adopter) and 104 calls with 83 entries (fifth), counted once per call (a
  first count, 161 and 158, repeated some records). The rest of those vaults
  comes from the extractor, imports and people. So "saves against capture" can be compared on real use before
  anything is built.
- F0 is settled (kit, `plan-15/f0-session-link.md`): a tool call carries its tool-use id, which is found in
  exactly the transcript of the caller (session, session after `/clear`, subagent); the environment's session
  id goes stale. A save through a tool can be tied to its transcript where the shell's variables cannot be
  trusted.

## Stage 0 — Which entries serve, by who wrote them (no code, existing data)
Material: part D's kit. Two populations, third and fifth adopters:
- **Consultations**: every memory consultation the agents made (318 of the 365), each with the call, the result
  returned and what the agent did after.
- **Episodes**: the knowledge episodes where the deployed vault, searched as of the episode's start, held what
  the agent then found in whole or in part (part D: 3 and 91 over four adopters; the two adopters' share).
Origin of an entry, decided without a model: `saved` (admitted or verified by a `val3 save` call found in a
transcript, main session or subagent), `captured` (admitted by the extractor), `imported`, `other` (a person,
`tool add`, a review). Entries whose origin cannot be told are counted as `unknown`, never guessed.
Per consultation, a reader blind to origin, given the call, the result and what followed, lists the returned
entries the agent **used** (its next steps rely on what the entry states) and those that were **wrong or out
of date**. Per episode, the reader lists the returned entries that hold what the agent then found. A second
reader on a third of each; agreement per returned entry reported.
Measures, by origin and per adopter:
- returned (the exposure), used, wrong; **used per returned**;
- the vault's composition by origin at the time, to read exposure against it;
- for episodes: entries holding the need, by origin;
- by kind of entry, and saved entries by whether the saving session is the consulting one.
Rule, written before any result. It does not decide whether `memory_save` is built (the owner decided); it
decides what the trial after it is asked to show:
- if `saved` entries are used per returned at least 1.5 times as often as `captured` ones, on both adopters,
  with at least 30 used entries of each origin over the two: the trial of stage 2 includes an adopter running
  **saves only** (extractor off) and the extractor's cost has to be justified there;
- if `captured` is used at least as often as `saved`: the extractor stays as it is and `memory_save` is a
  convenience; its trial only checks that it does no harm;
- between, or under 30 used entries of an origin: reported as not settled, both stay, the trial compares.
Limits known beforehand: what is returned depends on ranking and on what each origin tends to write (a saved
entry may be written to be found); the agent that saved an entry may be the one consulting it; `used` is a
reader's judgement; two projects of one owner. Cost: readers only, ceiling 15 dollars.

### Result of stage 0 (2026-11-16, $6.75; kit `eval/origin-2026-11/out/report.md`)
195 consultations (third 84, fifth 111: one per call; part D's 318 counted repeated records) with 1 024
returned entries, and 83 episodes; one Sonnet reader, blind to origin (checked: no packet shows it), a second
pass on a third (same answer on 98.5 % of the pairs; it is the same model on the same input, not a second
judge). Origin by the vaults' own rows and the transcripts: saved 301, captured 1 028, unknown 61 of 1 390.

| consulting adopter | origin | returned | used | used per returned | share of returned | share of the vaults |
|---|---|---|---|---|---|---|
| third | saved | 179 | 33 | 0.184 | 49.7 % | 17.4 % |
| third | captured | 181 | 26 | 0.144 | 50.3 % | 82.6 % |
| fifth | saved | 297 | 22 | 0.074 | 44.7 % | 18.2 % |
| fifth | captured | 367 | 40 | 0.109 | 55.3 % | 81.8 % |
| both | saved | 476 | 55 | 0.116 | 46.5 % | 17.8 % |
| both | captured | 548 | 66 | 0.120 | 53.5 % | 82.2 % |

**By the rule: not settled.** Saved against captured, used per returned: 1.28 on the third adopter, 0.68 on the
fifth, 0.96 together (1.5 on both was asked); used entries 55 and 66 (40 and 47 distinct). Both stay; the
trial compares.
Described, not ruled on:
- Once returned, an entry is used as often whoever wrote it (ranked searches only: 0.097 against 0.093; read by
  ref: 0.243 against 0.274).
- Saved entries are returned far above their share of the vaults (46.5 % against 17.8 %), but mostly through
  index listings and reads by ref; in ranked searches 25.4 % against 17.8 %.
- In the episodes (what the agent went looking for and the vault held in whole or in part), saved entries are
  43 of the 119 holding ones (36 %, against 17 % of the vaults); holding per returned 0.333 against 0.254.
- An agent hardly uses what its own session saved (2 of 62 returned); the use is by other sessions (53 of 414).
- Wrong or out of date among the returned: saved 3, captured 12.
What makes it unfair beyond the limits written beforehand: the fifth adopter's "saved" exposure is mostly the
third's vaults (245 of 297; its own saved entries were returned 52 times and used once); 59 saved entries of
one vault come from a scripted loop over documents, closer to an import (without them saved is 0.127); 10 of
the 66 used captured entries are tool entries, which no save wrote; 61 entries of one day have no origin.

## Stage 1 — `memory_save` (design, revised after its challenge)
A third tool of the memory server, over the present writer. Nothing about what is admitted changes. What it
adds over `val3 save` through the shell is convenience and a call bound to its caller's transcript (also after
`/clear` and in a subagent); it is tried as that, with the shell saves as the baseline. It does not make
evidence stronger: a quote check shows that the text occurred in the transcript, not that it proves the claim,
and a transcript is a local file the agent's shell can write (TODO, "Evidence forged by the agent").

**Tool.** `memory_save(candidate, vault?)`: `candidate` is the object `val3 save` takes (`candidate.FORMAT`
in the description); `vault` names a specialized vault of the project. One instruction, the same words in the
tool's description, the server's `instructions`, the hooks' line and the CLAUDE.md block: "Save durable
project knowledge once a result or a decision is settled, when a later session would need it. Search first
and name in `supersedes` an entry it replaces. Quote the evidence the session shows; without evidence the
entry is stored unverified."

**Which transcript.**
1. The server's loop passes the request's `_meta["claudecode/toolUseId"]` to its child in the envelope, beside
   name and arguments; it is never read from the arguments. Supported client: Claude Code, which sets it.
2. The transcript directory is resolved once, at the first save: the one directory under the harness's
   `projects/` (`CLAUDE_CONFIG_DIR` or `~/.claude`) that holds `<CLAUDE_CODE_SESSION_ID>.jsonl`, by its
   canonical path. Saves are bound to that directory: a session resumed elsewhere, or a start-up file that is
   gone, gives the "no transcript" error below.
3. A file matches when a complete JSON line of it is an assistant record with a `tool_use` block of exactly that
   id and a name ending in `__memory_save`. An id that occurs only inside other text does not match. Files
   `*.jsonl` and `*/subagents/*.jsonl` of the directory whose canonical path stays inside it; a match in two
   distinct files is refused as ambiguous (no "newest wins"). Looked for every 0.2 s for at most 30 s: the
   harness writes the record when the whole assistant message has been streamed (measured on the built tool:
   0.2 to 1.7 s for one call, more than 8 s for the first of three saves sent in one message; a first design
   waited 3 s and failed there). Only files written in the last 10 minutes are read.
The session recorded as origin is the file's session (for a subagent's file, the session directory above it),
and the `admitted` or `verified` event carries `agent` with the subagent's id.

**A subagent's prompt is not the user's words** (a defect of the shared parser the challenge found, checked on
a real file: the first record of a subagent's transcript is a `user` record with `isSidechain` and no origin,
so `_typed` would take the parent's prompt as typed by a person). `transcript._typed` returns nothing for a
record with `isSidechain`. Tool output of the subagent stays eligible.

**Without a transcript.**
- A candidate with no evidence needs none: it is admitted unverified as the writer does today, with the session
  when the lookup gives one and without it otherwise.
- A candidate with evidence and no usable transcript is not saved. Three distinct errors: the client sent no
  call id (no waiting); no transcript of this project holds the call yet (it may be late: try again with the
  same candidate); a quote failed its check (the writer's message, logged `discarded`). None of them tells the
  agent to point the shell command at another transcript; each says the candidate can be sent again unchanged,
  or without its evidence to store it unverified.

**Writing.** The child calls what `val3 save` calls, moved into one function both use: `writer.submit(...,
via="tool")`, then the vectors of entries that lack one and the near-neighbour lines. `submit` alone computes
no vector. The child prints the admission's result as soon as the transaction commits, then does the optional
work and prints the final result; the server keeps the last complete line, so a child killed at the deadline
after the commit still answers with the ref (and "vectors pending") instead of a timeout. An embedding failure
is a warning after admission, as in the command.

**Time.** One deadline of 50 s for the call: at most 30 s of lookup, `lock_timeout` 5 s on the connection (the
writer's advisory lock), the statement timeout the tools already lack stays bounded by the deadline. A lookup
that runs out says the transcript may be late, not that the id does not exist.

**Evidence rules for the tool's own traffic.** `_MEMORY_TOOL` covers `memory_save`: its results and errors are
never evidence, like the other memory tools and VAL3's shell output; its input is model text.

**Capture after a save.** The extractor later reads the session and sees the call. No suppression is built
before it is measured: one integration test saves, then captures the same transcript with an extractor that
proposes the same fact exactly and as a paraphrase, and asserts duplicate or repeat, not a second entry; the
trial counts those outcomes on real sessions.

**Logs.** The database's events are the record of a save (`via: "tool"`, also on `verified`). The usage log
gets a best-effort line per call: tool, response outcome, writer status, ref.

**Limits of a request.** The whole JSON-RPC line holds 65 536 bytes; the description says a candidate must stay
under 48 000 characters and a larger one goes through `val3 save`. Tested at the boundary with non-ASCII text.

**`val3 init`.** The server offers the tool to every project. `permissions.allow` gets
`mcp__val3__memory_save` only with `val3 init --allow-save`; init never adds it otherwise and never removes
it, so a person's choice survives a re-run. Without it the harness asks, or its automatic mode decides.

**Not built.** No confirm, withdraw or settle tools; no change to the extractor's prompt; no request-id
recovery table.

**Tests** (subprocess, as the suite does): the id in a session file, in a second session file, in a subagent's
file (and `agent` in the event); the record written 1 s after the call starts; no `_meta`; an id only quoted
inside another record; the id in two files; a transcript symlinked outside the directory; a subagent's prompt
quoted as `user` evidence (refused, by `val3 save --transcript` too); a failed quote; no evidence without a
transcript; a specialized vault; a child killed after the commit; save then capture; a `memory_save` result
quoted as evidence (refused); `init` with and without `--allow-save`, twice; the usage line; the size boundary.

### Challenge
Codex, gpt-6.1-sol, effort medium, read-only, 2026-11-16: 23 objections, 5 blocking (report outside the
repository, `eval/plan-15/codex-plan17-review.md`). Checked against the code: `mcp._tool_call` passes only name
and arguments (4); `cli.cmd_save` embeds and prints neighbours after `submit` (12, 14); `_MEMORY_TOOL` names
search and read only (10); `init._permissions` re-adds a removed tool (7); a real subagent transcript's first
record (5).
- Taken: 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 23 as written above; 19 in stage 2's
  measures; 20 to 22 in how stage 0 is read (below).
- Taken in part: 3. Paths outside the directory are refused; the quote check still reads the file a second
  time. A single immutable read would change the writer's interface for a file the agent can write anyway.
- Stage 0 was already running when the challenge came. Its ratio is read as a description: an entry first
  captured and later verified by a save counts as captured (creation is what "who wrote it" means), with the
  mixed cases counted apart; rates are per adopter, with unique entries and unique sessions beside the counts;
  opposite directions on the two adopters are "not settled". A saves-only adopter in the trial is itself the
  measurement of what capture alone supplies, not a consequence taken for granted.

## Stage 2 — Trial on adopters
Defined after stages 0 and 1. Baseline: the shell saves of the same adopters before the tool. Measured over two
weeks, per adopter, parent sessions and subagents apart: saves per active session by surface (tool, shell);
writer outcomes (admitted, duplicate, repeat, verified, unverified, discarded); lookup failures and retries;
latency; what capture then does with a saved fact; later use of saved entries (the usage log's refs); wrong
entries and supersessions found by reading a sample; capture's cost. Stop rule of plan 15 kept (more than a
third of the saves failing the evidence check over 30 calls turns the tool off there), with one more: any
backed entry superseded by a wrong save.
