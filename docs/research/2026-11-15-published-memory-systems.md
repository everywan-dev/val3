# Published agent-memory systems against VAL3 (2026-11-15)

Why: on 2026-11-15 the owner asked what VAL3 should change, compared with published memory systems and
Anthropic's notes. A first answer was given from recollection; this file replaces it with sources. Pages were
fetched on 2026-11-15 by three research agents. **Checked** means the quoted text was found by this session in
the raw page or file; **reported** means it comes from an agent's summarising fetch and was not re-read.

## 1. What the first answer got wrong or could not support

| Said from recollection | Source says |
|---|---|
| mem0 decides add / update / delete / ignore at write time | True of its 2025 paper. Since 2026 it is "Single-pass ADD-only extraction -- one LLM call, no UPDATE/DELETE. Memories accumulate; nothing is overwritten." (checked, README). Its blog: "The new algorithm collapses extraction into a single LLM call that only adds." (checked) |
| Zep/Graphiti gives temporal validity to facts | True (reported: `valid_at`, `invalid_at`, `created_at`, `expired_at` on an edge). Not said then: an open issue reports that invalidation searches the whole graph; in one graph 1 616 of about 3 950 facts carry `invalid_at`, and of four audited by hand three were retired wrongly (checked, issue 1728). |
| Letta consolidates in the background | True of its sleep-time agent (reported). Not said then: Letta's own benchmark has an agent with plain file search reach 74.0 % on LoCoMo "by simply storing conversation histories in files", above the 68.5 % mem0 reported (checked). |
| Anthropic: the agent writes and reads memory on purpose, few durable notes | Supported, and sharper than stated; see §2. |

## 2. Anthropic

Sources: memory tool documentation and cookbook; Claude Code memory documentation; "The components of agent
memory" (research post, published 2026-11-06); Managed Agents memory documentation and "dreaming" cookbook.

- **Who writes.** The model, during work (memory tool, auto memory). Claude Code: "Claude doesn't save something
  every session. It decides what's worth remembering based on whether the information would be useful in a future
  conversation." and it "skips anything it can derive from the codebase" (checked). An offline pass over logs,
  called dreaming, corrects mistakes, creates notes and shapes "what's preemptively loaded into future sessions"
  (checked).
- **What is worth storing.** In the post's game evaluation an older model wrote 413 files and "its detailed
  record was continuously going out of date"; the newest wrote nine. State that "can be read off the screen
  every turn" is redundant to store; "strategy, plans, and tested rules" are what memory is for (checked).
- **Fixed rules about what to write can hurt.** Told to "record only failures", two strong models "lost much of
  the performance boost that memory gave them"; "giving them freedom to judge salience was more effective than
  setting fixed rules in advance" (checked).
- **Reading.** Claude Code loads "the first 200 lines or 25 KB of MEMORY.md" at every start and reads topic
  files on demand (checked). The top layer "has to stay relatively small"; "an incorrect note can be carried
  into every session" (checked). Promoting a note to the loaded layer "means Claude doesn't need to search for
  them, or even to know there's something to search for" (checked). An older model "often didn't read files from
  its memory directory"; prompting it to read memory first "nearly doubled its benchmark score"; a newer one read
  memory unprompted "9 out of 10 times" (checked).
- **Correction.** Dreaming "checked the note against the session transcript, caught its error, and rewrote it.
  The transcript gave Claude a ground truth to validate its memory against." (checked). The dreaming cookbook:
  duplicates are merged; "An early fact must be dated and superseded, not copied"; on contradictions "Do not guess
  which is right" (reported).
- **Risks.** "Memory files are read back into Claude's context, making them a potential vector for prompt
  injection" (reported, cookbook). Concurrent writers to one shared page overwrote each other (reported).
- **Figures.** CL-Bench, fresh session per question, with and without a persisted memory directory: a strong
  model 0.655 to 0.838, an older one 0.330 to 0.364 (reported). Memory tool plus context editing: +39 % on an
  internal 100-turn evaluation whose details are not published (reported).

## 3. Other systems

| | Who writes | Unit | Duplicates and updates | Read | Seen by a person |
|---|---|---|---|---|---|
| mem0 (open source) | extraction call per exchange | fact string, hash, timestamps, optional expiry | exact-hash only; nothing updated or deleted (checked) | vector + BM25 + entity boost | hosted dashboard, graph view on a paid plan (reported) |
| Graphiti / Zep | extraction per ingested episode | entities and dated edges | exact name, fuzzy match, then a model picks; contradicted edges get `invalid_at` (reported; failure mode checked) | BM25 + cosine + graph walk, rerankers | Neo4j or FalkorDB browser; Zep's graph view is hosted (reported) |
| Letta | the agent, through tools; a background agent on shared blocks | always-loaded blocks, searchable archive; now a git-backed memory filesystem | agent edits in place; background agent told to remove outdated text | blocks in context, archive by tool | memory panels in its hosted app; git history (reported) |
| LangMem | agent tools and a debounced background extractor | free text or schema | left to a model pass | store search | none found |
| claude-mem (coding agents) | hooks send each tool call to an observer model | observation | exact normalised title merged; near-duplicates only flagged (reported) | 50 observations injected at session start, skipped on resume (reported) | local web viewer served by its worker (checked: "serves the viewer and search") |
| MCP `memory` server | the agent, through tools | entity, relation, observation; exact names | none | substring match | none |
| Basic Memory | the agent; session-start briefing and pre-compaction checkpoint | Markdown notes with relations | agent edits | full text + vector | the note folder opens in a note-taking app's graph view (reported) |

Benchmarks are disputed among the vendors (Zep and mem0 each publish a different LoCoMo score for the other;
a third-party audit finds 6.4 % of LoCoMo's answer key wrong: all reported). No published figure is used below.

## 3b. Hosted services of the large vendors

Fetched 2026-11-15; quotes taken from the raw pages (228 confirmed by script, those used here re-read).

| | Who writes | Duplicates and changes | Read | Seen by a person | Price |
|---|---|---|---|---|---|
| Google, Agent Platform Memory Bank (formerly Vertex AI Agent Engine Memory Bank) | extraction by a model, per call or over a stream of events; the agent or developer directly | a model creates, updates or deletes against candidates found by similarity ("DELETED: Indicates that the existing memory was deleted, because its information was contradictory to new information"); immutable revisions with rollback; events deduplicated by `event_id` | similarity search, or preload through a tool of its agent kit | console to view, edit, delete | $0.30 per GiB-month, operations and tokens |
| AWS, Bedrock AgentCore Memory | asynchronous extraction after each event; batch API | a model decides ADD, UPDATE or NO-OP against the most similar records; "marking the outdated memories as INVALID instead of instantly deleting them" | semantic search | console | $0.75 per 1 000 records a month, $0.50 per 1 000 retrievals |
| Microsoft, Foundry Agent Service (preview) | a tool after each response, API, remember and forget commands | merge by a model, not specified | profile at start, search per turn | portal | model tokens |
| OpenAI | no hosted memory service in the API found; sessions in its agents SDK and file memory on the client | | | | |
| Cloudflare, Agent Memory (private beta) | ingestion when the harness compacts, and model tools | "Facts and instructions with the same topic key supersede older versions rather than creating duplicates"; the old version is kept; "Sending the same message twice does not create a duplicate" | several strategies in parallel, then a model writes the answer | not found | free in beta |

- **Verification against the source exists elsewhere.** Cloudflare: "The next step is to verify each extracted
  memory against the source transcript. The verifier runs eight checks […] Each item is passed, corrected, or
  dropped accordingly." A second model judges; VAL3 checks a literal quote.
- **A topic key that supersedes** is in production at Cloudflare, without published figures. VAL3 measured an
  exact-match object key on one vault on 2026-11-15 and it was not repeatable there.
- **AWS's own evaluation** puts its managed memory below passing the full history on fact recall (LoCoMo 70.58 %
  against 77.73 %; LongMemEval 73.60 % against 75.2 %) and above it on preferences (79 % against 51 %).
- **Memory poisoning** is documented as a risk by Google and Microsoft, in one line by AWS.
- Google's launch post says Memory Bank is "grounded in" a paper (arXiv 2503.08026); no evaluation of the product
  was found.

## 4. Observations for VAL3 (hypotheses to test, not conclusions)

The tables above are examples read from documentation and one research post, without adoption data; one issue
and a dispute among vendors prove nothing general. Each line below is what the sources show and what VAL3 should
therefore test.

1. **Writing.** Anthropic's tools, Letta, Basic Memory and the MCP server have the agent write on purpose; mem0,
   Graphiti and claude-mem extract; LangMem does both. VAL3 leans on extraction. To test: what a `memory_save`
   tool contributes beside the extractor (plan 15, part F).
2. **Deciding pair by pair at admission.** mem0 gave it up; Graphiti, Google and AWS keep it (a model against
   similar memories), and Graphiti has a reported failure of valid facts retired. VAL3's own figures (6 replacements in 1 464 settled pairs; no repeatable object key) are
   the evidence that counts here; the published cases only show it is not a solved problem elsewhere.
3. **A small layer loaded at the start.** Anthropic reports it as the way an agent learns there is something to
   look up, and warns it carries a wrong note into every session. VAL3's agents do consult the memory, mostly
   through the CLI (on one adopter on 2026-11-15, main session and subagents together: `memory_search` 1,
   `show` 6, `recall` 4, `index` 3, in 664 tool calls); whether they miss what the vault holds is not known.
   To measure before building anything (part D).
4. **What goes stale.** In Anthropic's game evaluation, state readable from the environment was redundant to
   store and went out of date. VAL3's staleness sample put the harm in entries describing a state (4 of 27).
   Neither shows that only such entries go stale.
5. **Rules about what to write.** The published result is about an agent told to record only failures during a
   task. It does not show that a broader instruction given to an extractor afterwards has the same effect. A
   narrower extractor prompt is VAL3's own hypothesis and is tested as an arm (part G).
6. **Evidence and linked stores.** Cloudflare verifies extracted memories against the transcript with a model;
   none read here checks a literal quote, and none links stores across projects with read permissions as VAL3
   does (several share one store among agents or a team).
7. **A view for a person.** mem0, Zep, Letta, claude-mem, Basic Memory and the three cloud vendors offer one;
   LangMem and the MCP server do not.
8. **Session resume and repeated input.** Google deduplicates events by id and Cloudflare by content;
   claude-mem skips its start-of-session injection on resume; VAL3's capture keeps its
   place per session id and re-read 1 201 messages of a resumed session on 2026-11-15.

Source URLs (fetched 2026-11-15): github.com/mem0ai/mem0 (README, `main`);
mem0.ai/blog/mem0-the-token-efficient-memory-algorithm; github.com/getzep/graphiti/issues/1728 (opened
2026-08-04, on 0.29.3); arxiv.org/abs/2501.13956; letta.com/blog/benchmarking-ai-agent-memory (2025-08-12);
anthropic.com/research/components-of-agent-memory (2026-11-06); code.claude.com/docs/en/memory;
platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool; platform.claude.com/docs/en/managed-agents/memory;
github.com/thedotmack/claude-mem (README, `main`); github.com/langchain-ai/langmem;
github.com/modelcontextprotocol/servers; github.com/basicmachines-co/basic-memory;
docs.cloud.google.com/gemini-enterprise-agent-platform/scale/memory-bank (updated 2026-11-12);
aws.amazon.com/blogs (AgentCore long-term memory deep dive, 2025-10-15) and its pricing page;
blog.cloudflare.com/introducing-agent-memory (2026-04-17);
developers.cloudflare.com/agent-memory/concepts/how-agent-memory-works.

Not found: any published rule for when an agent should consult memory beyond "read it first"; any published
end-to-end measurement on coding work over weeks; any evidence check comparable to VAL3's.
