# ADR 0013 — `memory_save`: saving through the memory server, bound to the caller's transcript

Date: 2026-11-16. Status: accepted (2026-11-16). Implements stage 1 of
`docs/plans/2026-11-16-val3-plan-17-memory-save.md`.

## Context

Agents already save on purpose with `val3 save` in a shell. That command finds the transcript its quotes are
checked against through `--transcript` or `VAL3_TRANSCRIPT`, which the session-start hook writes: after `/clear`
it names the previous session, and in a subagent it names the parent's file. A tool call carries something the
shell does not: Claude Code sends the call's tool-use id with the request, and that id is written in exactly
the transcript of the caller (session, session after `/clear`, subagent) when the whole assistant message has
ended: a batch of calls in one message delays the first call's record by the rest of the message (measured on
Claude Code 2.1.325: more than 8 seconds for three saves of about 1 600 characters).

## Decision

- The memory server serves a third tool, `memory_save(candidate, vault?)`, over the present writer. Nothing about
  what is admitted changes; the tool adds convenience and a call bound to its caller's transcript. It does not
  make evidence stronger: a quote check shows that the text occurred in the transcript, which is a local file the
  agent's shell can write.
- **The call id is the link to the transcript.** It is read from the request's `_meta`, never from the
  arguments. The transcripts' directory is the one under the harness's `projects/` that holds the file of the
  session the server started in, resolved at the first save; a file of that directory, or a subagent's file under
  it, matches when a complete line is an assistant record with a `memory_save` tool_use of exactly that id. A file
  whose canonical path leaves the directory is not read; a match in two files is refused, never "newest wins".
  The server waits up to 30 seconds for the record and reads only the files written in the last ten minutes, so
  an ambiguity is detected among recently written files only. A call id is accepted only as letters, digits, `_`
  and `-`; the directory is resolved once, at the first save with a call id, even when none is found. When the
  starting session has no file (it was cleared before its first message), the directory is the one the harness
  names after `CLAUDE_PROJECT_DIR`; two project paths can share that name, which costs reading, not a wrong
  match, since a call id is in one file.
- **A subagent's prompt is not user evidence.** The first record of a subagent's transcript is a `user` record
  with `isSidechain`, holding the prompt its parent wrote; the shared parser no longer reads a sidechain record
  as typed by the user, for `val3 save --transcript` too. The subagent's tool output stays evidence.
- **No save without its evidence when the lookup fails.** A candidate with evidence and no usable transcript is
  refused with one of three errors (no call id; no transcript holds the call yet; the call is in two
  transcripts), and a failed quote is the writer's `discarded`. None of them points at another transcript; each
  says the candidate can be sent again unchanged, or without its evidence to be stored unverified. A candidate
  without evidence needs no transcript.
- The admission is reported before the vectors and the near neighbours are computed, so a call killed at its
  deadline after the commit still answers with the ref.
- **Allowing the tool without asking is a person's choice.** `val3 init` adds the permission only with
  `--allow-save`, never otherwise, and never removes it.
- The tool's results, like the other memory tools', are never evidence.

## Refused

- Reading the session from `CLAUDE_CODE_SESSION_ID` at each call: it is the session the server started in and
  goes stale after `/clear`.
- Storing a candidate that has evidence without its evidence when the lookup fails: the agent would not see that
  the entry lost its backing.
- Suppressing capture of a fact the session saved: the extractor's duplicate and repeat outcomes are measured
  first.
- Confirm, withdraw or settle tools; a table recovering a save's outcome by request id.

## Consequences

- The transcript is read when it is looked up and again when the quotes are checked, by path: a project directory
  renamed or a file swapped in between is not guarded against, since the agent's shell can write it anyway.

- Only clients that send the call id (Claude Code) can save with evidence through the tool; others save
  unverified or use `val3 save`.
- A request line holds at most 65 536 bytes, so a large candidate, or one in non-ASCII text, goes through
  `val3 save`.
- The trial of the tool against the shell saves is stage 2 of the plan.

## Found after the merge (2026-11-16)
The lookup depends on when the harness writes the call's record, which varies by how the call is permitted.
Measured on Claude Code 2.1.325 with a logging server and with the tool itself:
- headless run, tool allowed on the command line: on disk 0.2 to 1.7 s after the call arrives (later for the
  first call of a batch, as above);
- interactive session, not pre-allowed (automatic mode decides): on disk at 0.1 s; a save with evidence is
  admitted, also after `/clear` (bound to the new session);
- interactive session, tool in `permissions.allow`: not on disk for at least 40 s while the call is pending,
  written right after the call returns. A save with evidence always fails there after its 30 s wait.
So the decision "the call id found in the transcript during the call" does not hold in the configuration the
permission flag creates. Until the design changes, `--allow-save` is not used. The change to make: admit at
once and check the evidence after the call has returned (a deferred check keyed by the call id), which no
longer depends on the harness's timing.

## Amendment 1 (2026-11-17): a save carried out after the call

Design: `docs/design/2026-11-17-deferred-evidence-check-design.md`. Status: **parked, not built** (owner,
2026-11-17). The owner decided three points that day (the entry exists only after the check; a deferred save
that fails stores nothing; a recoverable result after a crash), the design was approved with conditions and
corrected, and then set aside: the measure below shows that the permission would defer the first save of
every message in automatic mode too, and every supervised session runs in automatic mode. To reopen:
adopters that work in manual mode. What follows describes the design as parked. It replaces the change named in the section above ("admit at once and check the evidence after the
call"): a first design of that was challenged and dropped, because admitting before checking needs a second
writer (verification by entry id, held supersessions, a race with capture).

- The call waits 2 seconds for its record, not 30. Found: the save is carried out during the call, as above.
- Not found, for a candidate with evidence: the candidate is validated, the request is noted in a job file of
  the state directory (the call id, the transcript directory, the working directory, the time; no candidate
  text), and the answer says the save is not stored yet. After the answer is written the server starts a
  detached process that waits for the record, reads the candidate from it, and runs the same submission.
  Nothing is in the vault before the check: "no save without its evidence" holds, and a deferred save whose
  quote fails, or whose record is never found, stores nothing (owner's decision).
- One process carries a job out: it holds an exclusive lock on the job file while it runs. The job holds the
  hash of the call's arguments and the validated vault, and a record that differs stores nothing. The events
  that change the vault carry the call id in the same transaction, and a job run again after a crash is
  answered from them.
- The outcome is reported by the server before the answer of a later memory call. The notice belongs to the
  server, not to a session or an agent, and names the entry.
- For the tool, on both paths, only the records before the call's record are evidence. `val3 save` reads the
  whole transcript as before.
- The events of a save by the tool carry the call id in their detail, as a trace.
- Limits: a deferred save's answer has no ref; recovery of a job whose process was killed happens at the
  server's next start or save, not on a timer.
- Measured on 2026-11-17 (Claude Code 2.1.326): with the tool pre-allowed the record of a message's first
  call is written only after the call returns, in automatic mode as in manual mode. With the permission an
  adopter in automatic mode would lose the ref it gets today in the answer. `--allow-save` stays unused.
- `--allow-save` is used on an adopter only after a person's trial in an interactive session with the tool
  pre-allowed, in automatic mode and out of it.
