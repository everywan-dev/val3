# VAL3 Plan 2 — Automatic capture

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every closed or compacted session of an adopted project is queued, and a detached extractor (`claude -p`) turns its unseen transcript lines and the inbox notes it wrote into candidates that go through the plan-1 writer.

**Architecture:** `PreCompact` and `SessionEnd` run `val3 hook capture`, which writes one queue file and starts a detached `val3 drain --if-idle`. One drainer per machine (an `flock` on the queue directory) processes entries serially: it condenses the transcript lines after the session's watermark into chunks, sends each chunk with the matching existing entries and the session's changed inbox notes to an isolated `claude -p` with a fixed prompt and a JSON schema, submits every returned candidate through `writer.submit`, advances the watermark, and rewrites notes that produced entries as references. `SessionStart` starts a drainer when entries are left over.

**Tech Stack:** as plan 1 (Python ≥ 3.11 standard library, `psycopg[binary]`, PostgreSQL 16, pytest, Docker) plus the Claude Code CLI as the extractor.

**Spec:** `docs/design/2026-10-08-val3-design.md` (sections 1, 4, 5, 6), ADR 0001, ADR 0002, plan 1 (`docs/plans/2026-10-08-val3-plan-1-core.md`, its decisions still bind).

## Verified facts this plan relies on (Claude Code 2.1.294, 2026-10-08)

1. A process started from a `SessionEnd` hook with a new session (`setsid`) keeps running after the hook returns (probe: the child wrote its marker 6 s after the session ended).
2. The auto-memory directory is `~/.claude/projects/<slug of the git repository root>/memory/`, while transcripts live under the slug of the cwd: a session in `repo/sub` loaded `repo`'s `MEMORY.md`. The slug replaces every non-alphanumeric character with `-`. The setting `autoMemoryDirectory` relocates it.
3. The settings key `autoDreamEnabled` (background consolidation), the lock file `.consolidate-lock` (with `holderPid`, `lastConsolidatedAt`) and the note frontmatter key `originSessionId` exist in the binary.
4. `claude -p --setting-sources "" --tools "" --strict-mcp-config --no-session-persistence --disable-slash-commands --output-format json --json-schema <schema>` works with the subscription login, runs no hooks, loads no `CLAUDE.md` and no MCP servers, writes no transcript, and returns the answer in `structured_output` with `total_cost_usd`. `--bare` is unusable: it requires an API key.
5. `SessionEnd` input: `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `reason`; `PreCompact` adds `trigger`.

## Decisions fixed by this plan

1. **Queue** — `$XDG_STATE_HOME/val3/queue/<time_ns>-<pid>.json` (default `~/.local/state`), written to a `.tmp` file and renamed. Entry: `session_id`, `transcript_path`, `cwd`, `event`, `queued_at`, `attempts`. An entry is deleted only after its extraction committed.
2. **One drainer per machine** — `flock` on `queue/.lock`. Hooks start `val3 drain --if-idle` (non-blocking: if another drainer holds the lock it exits, and the running one picks up new entries before releasing). `val3 drain` without the flag waits for the lock. Each drain run processes every entry at most once. This answers design section 13's "two sessions of one project on one machine": entries are separate files and a single drainer serialises them.
3. **Watermark** — per session, `$XDG_STATE_HOME/val3/sessions/<sha256(session)>.json` holds the number of transcript lines already extracted. It advances after each chunk, so a later `PreCompact` or `SessionEnd` of the same session sends only new lines and a failure never resends finished chunks.
4. **Failures** — server unreachable: the drainer stops, entries stay, attempts are not spent. Extractor failure (non-zero exit, timeout of 600 s, output that is not the expected JSON): `attempts + 1`, retried on the next drain; at 3 the entry moves to `queue/failed/` with its error. Missing transcript, removed `val3.toml`, missing vault: set aside at once. Every outcome is one JSON line in `$XDG_STATE_HOME/val3/extract.log`.
5. **Condensed transcript** — lines `USER:`, `ASSISTANT:` (text only, never thinking), `TOOL CALL <name>: <command or JSON input>`, `TOOL RESULT:` and `TOOL RESULT (not evidence):` (tools of plan 1's model-text rule). Clipping keeps a literal prefix plus `[… N more characters]`: tool results 4 000, tool calls 1 000, user and assistant text 8 000 characters. Chunks hold at most 120 000 characters.
6. **Extractor** — `claude -p` with the flags of fact 4, `--model <[extractor] model>`, `--system-prompt <src/val3/extractor_prompt.md>` (prompt version 1, embedding `candidate.FORMAT`), the material on stdin, cwd `$XDG_STATE_HOME/val3/extractor`, environment `VAL3_EXTRACTING=1`. Output schema: `{"candidates": [{"note": string|null, "candidate": object}]}`; the writer validates each candidate. The command prefix comes from `$VAL3_EXTRACTOR` (default `claude`); tests point it to a fake.
7. **Model** — `val3.toml` `[extractor] model`, default `claude-haiku-5-5`. Task 5 measures Haiku 5.5 against Sonnet 5.5 on real transcripts before the default is confirmed.
8. **Material** — project and home vault; up to 30 existing entries found by `search` over the chunk's user messages (the extractor must not repeat them and uses `supersedes` against them); the session's changed notes (last chunk only); the condensed chunk.
9. **Inbox** — memory directory per fact 2 (git common-dir root, else the cwd; `autoMemoryDirectory` from `.claude/settings.local.json`, `.claude/settings.json`, `~/.claude/settings.json`, in that order). Changed notes: `*.md` except `MEMORY.md` whose SHA-256 differs from the one recorded in `$XDG_STATE_HOME/val3/inbox/<sha256(dir)>.json`, and whose `originSessionId` is the session's or absent. A note that produced admitted or duplicate entries is rewritten as its frontmatter plus `Recorded in VAL3 as <refs> (verified). Read it with `val3 show <ref>`; search with `val3 recall <terms>`.`; others stay as they are. Every processed note's hash is recorded, so it is not resent unless it changes. While `.consolidate-lock` exists, notes are not rewritten and their hashes are not recorded (retried later). When a session has changed notes but no new lines, the last chunk of the whole transcript is sent with them.
10. **Recursion guard** — `val3 hook capture` does nothing when `VAL3_EXTRACTING` is set, outside adopted projects, or without `session_id`/`transcript_path`.
11. **Writer outcome** — `writer.submit` returns `Outcome(status, ref, reason)` whose `str()` is the plan-1 message; `status` is `admitted`, `duplicate`, `queued` or `already-queued`. This also fixes the plan-1 deferred minor where a lost race printed `admitted` for a duplicate.

## Global Constraints

- Plan 1's Global Constraints hold (Python `>=3.11`, standard library plus `psycopg[binary]>=3.2`, English, exit codes `0/1/2`, tests drive the CLI as a subprocess and never import `val3`, writer as the only vault writer, `vsql` for vault SQL, hooks exit 0).
- Tests never call the real `claude`: `$VAL3_EXTRACTOR` points to `tests/fake_extractor.py`. Only Task 5 runs the real extractor, by hand, against the development server (`val3dev`).
- The capture hook does no database work and no extraction: it writes one file and starts a process (the `SessionEnd` budget is 1.5 s).
- Work on branch `feature/plan-2-capture`; Conventional Commits.

## Review Focus

1. **Concurrent captures** — two sessions ending together, or a capture while a drainer runs: each entry is processed exactly once and none is left stranded (test in Task 2).
2. **Broken extractor output** — non-JSON, wrong shape, candidates that are not objects, invalid or unquoted candidates: valid candidates in the same answer are still submitted, the rest are counted as rejected, and a failed run is retried then set aside (tests in Tasks 1 and 2).
3. **Transcript gone or unreadable before the drain** — the entry is set aside with its error and the queue keeps moving (test in Task 2).
4. **Server down during a drain** — entries stay and attempts are not spent (test in Task 2).
5. **Very long sessions** — chunks stay under the limit and the watermark advances per chunk (test in Task 1).

---

## File structure

```
src/val3/config.py             + [extractor] model, state_dir()
src/val3/writer.py             submit/add_tool return Outcome
src/val3/transcript.py         + condense() and Chunk (shared loader with evidence_text)
src/val3/extractor_prompt.md   extractor system prompt, version 1
src/val3/extract.py            extract(), run_extractor(), material, watermark, summary()
src/val3/pending.py            queue: enqueue, entries, drain, spawn_drain, describe
src/val3/inbox.py              memory_dir, changed_notes, settle (note rewriting)
src/val3/hooks.py              + capture handler; session-start starts a drainer
src/val3/cli.py                + extract, drain, queue; hook choice capture
pyproject.toml                 package data *.md
tests/fake_extractor.py        stand-in for claude -p
tests/conftest.py              + fake extractor wiring, respond/calls/append/wait helpers
tests/test_extract.py, tests/test_pending.py, tests/test_inbox.py
```

---

### Task 1: Extraction of a transcript (`val3 extract`)

**Files:**
- Create: `src/val3/extractor_prompt.md`, `src/val3/extract.py`, `tests/fake_extractor.py`, `tests/test_extract.py`
- Modify: `pyproject.toml`, `src/val3/config.py`, `src/val3/writer.py`, `src/val3/transcript.py`, `src/val3/hooks.py`, `src/val3/cli.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: plan 1 `load_project`, `connect`, `search`, `submit`, `candidate.FORMAT`.
- Produces: `config.DEFAULT_EXTRACTOR_MODEL = "claude-haiku-5-5"`; `Project.extractor_model: str`; `config.state_dir() -> Path`; `writer.Outcome(status: str, ref: str, reason: str | None = None)`; `transcript.Chunk(text: str, end: int, user_text: str)`; `transcript.condense(path: Path, start: int, limit: int = 120_000) -> tuple[list[Chunk], int]`; `extract.ExtractorError`; `extract.extract(conn, project, transcript: Path, session: str) -> dict` (keys `admitted, duplicate, queued, already-queued, rejected, calls, cost_usd`); `extract.run_extractor(project, material: str) -> dict`; `extract.summary(counts) -> str`; `extract._material(project, existing, transcript_text) -> str`; `extract._watermark(session) -> int`, `extract._set_watermark(session, lines)`. Tests: `Env.fake`, `Env.state`, `Env.respond(*calls)`, `Env.calls()`, `Env.append(transcript, *records)`, `Env.wait(predicate, timeout=30)`, `Env.transcript(..., session=None)`.

- [ ] **Step 1: Write the fake extractor and extend the harness**

`tests/fake_extractor.py`:

```python
"""Stand-in for `claude -p` in tests: records each call and answers from prepared files.

FAKE_EXTRACTOR_DIR holds response-<n>.json for call n (default: no candidates) and an optional
`exit` file with the exit code to return.
"""
import json
import os
import sys
from pathlib import Path

directory = Path(os.environ["FAKE_EXTRACTOR_DIR"])
call = len(list(directory.glob("call-*.json")))
(directory / f"call-{call}.json").write_text(json.dumps(
    {"argv": sys.argv[1:], "stdin": sys.stdin.read(), "extracting": os.environ.get("VAL3_EXTRACTING")}))
exit_file = directory / "exit"
code = int(exit_file.read_text()) if exit_file.exists() else 0
if code:
    print("fake extractor failure", file=sys.stderr)
    sys.exit(code)
response = directory / f"response-{call}.json"
print(response.read_text() if response.exists() else json.dumps(
    {"type": "result", "is_error": False, "total_cost_usd": 0.0, "structured_output": {"candidates": []}}))
```

In `tests/conftest.py`, add `import shlex` to the imports and after the imports:

```python
FAKE_EXTRACTOR = Path(__file__).with_name("fake_extractor.py")
```

At the end of `Env.__init__`:

```python
        self.fake = root / "fake"
        self.fake.mkdir()
        self.state = root / "state" / "val3"
        self.environ.update(VAL3_EXTRACTOR=f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE_EXTRACTOR))}",
                            FAKE_EXTRACTOR_DIR=str(self.fake))
```

Change the signature of `transcript` to `def transcript(self, tool_outputs=(), user_messages=(), assistant_texts=(), compact_summaries=(), tool_calls=(), session=None):` and its first line to `path = self.home / ".claude" / "projects" / "-project" / f"{session or self.session}.jsonl"`. Append to the class:

```python
    def respond(self, *calls):
        """Prepare the fake extractor's answers: one list of {"note", "candidate"} items per call."""
        for i, items in enumerate(calls):
            (self.fake / f"response-{i}.json").write_text(json.dumps(
                {"type": "result", "is_error": False, "total_cost_usd": 0.001,
                 "structured_output": {"candidates": items}}))

    def calls(self):
        paths = sorted(self.fake.glob("call-*.json"), key=lambda p: int(p.stem.split("-")[1]))
        return [json.loads(p.read_text()) for p in paths]

    def append(self, transcript, *records):
        with transcript.open("a") as handle:
            handle.writelines(json.dumps(record) + "\n" for record in records)

    def wait(self, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.2)
        raise AssertionError("condition not met within the timeout")
```

- [ ] **Step 2: Write the failing tests**

`tests/test_extract.py`:

```python
import json

from helpers import OUTPUT, QUOTE, fact, make_vault, save


def extract(env, transcript, cwd=None):
    return env.run("extract", str(transcript), cwd=cwd)


def test_extract_admits_the_extractor_candidates(env):
    make_vault(env)
    t = env.transcript(user_messages=["How many invoices in April?"], tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": fact()}])
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    assert "admitted 1" in result.stdout and "1 extractor calls" in result.stdout
    assert env.sql(f"SELECT title, origin_session FROM {env.vault}.entry") == [("April has 42 invoices", env.session)]


def test_extractor_runs_isolated_with_the_project_model(env):
    (env.project / "val3.toml").write_text(
        f'service = "val3test"\n[vaults]\nhome = "{env.vault}"\n[extractor]\nmodel = "claude-sonnet-5-5"\n')
    assert env.run("vault", "create", env.vault).returncode == 0
    extract(env, env.transcript(tool_outputs=[OUTPUT]))
    call = env.calls()[0]
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    for flag in ("-p", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "candidates" in json.loads(argv[argv.index("--json-schema") + 1])["properties"]
    assert "Prompt version: 1" in argv[argv.index("--system-prompt") + 1]
    assert '"kind": "fact | recipe | preference | decision | tool"' in argv[argv.index("--system-prompt") + 1]
    assert call["extracting"] == "1"


def test_default_model_is_haiku(env):
    make_vault(env)
    extract(env, env.transcript(tool_outputs=[OUTPUT]))
    argv = env.calls()[0]["argv"]
    assert argv[argv.index("--model") + 1] == "claude-haiku-5-5"


def test_unknown_extractor_settings_are_rejected(env):
    (env.project / "val3.toml").write_text(
        f'service = "val3test"\n[vaults]\nhome = "{env.vault}"\n[extractor]\ntemperature = 1\n')
    result = env.run("vault", "create", env.vault)
    assert result.returncode == 1
    assert "[extractor] may only hold 'model'" in result.stderr


def test_material_is_the_condensed_transcript_without_thinking(env):
    make_vault(env)
    t = env.transcript(user_messages=["count April invoices"], assistant_texts=["Running the count."],
                       tool_calls=[("Bash", {"command": "psql -c 'select 1'"}, OUTPUT),
                                   ("Agent", {"prompt": "look around"}, "subagent says hello")])
    env.append(t, {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "SECRET-THOUGHT"}]}},
               {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "x", "content": "y" * 5000}]}})
    extract(env, t)
    stdin = env.calls()[0]["stdin"]
    assert "USER: count April invoices" in stdin
    assert "ASSISTANT: Running the count." in stdin
    assert "TOOL CALL Bash: psql -c 'select 1'" in stdin
    assert "TOOL RESULT:\n" + QUOTE in stdin
    assert "TOOL RESULT (not evidence):\nsubagent says hello" in stdin
    assert "y" * 4000 + "\n[… 1000 more characters]" in stdin
    assert "SECRET-THOUGHT" not in stdin


def test_existing_entries_are_listed_for_the_extractor(env):
    make_vault(env)
    t = env.transcript(user_messages=["How many invoices were there in April?"], tool_outputs=[OUTPUT])
    save(env, fact(), t)
    extract(env, t)
    assert f"- {env.vault}#1 [fact] April has 42 invoices: The ERP holds" in env.calls()[0]["stdin"]


def test_bad_candidates_are_counted_and_good_ones_kept(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    unquoted = fact(evidence=[{"kind": "query", "reproduction": "SELECT 1", "quote": "not in the transcript at all"}])
    env.respond([{"note": None, "candidate": unquoted}, {"note": None, "candidate": {"kind": "rumour"}},
                 "not an object", {"note": None, "candidate": fact()}])
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    assert "admitted 1" in result.stdout and "rejected 3" in result.stdout
    assert ("discarded",) in env.sql(f"SELECT action FROM {env.vault}.event")


def test_only_new_lines_are_sent_again(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    extract(env, t)
    result = extract(env, t)
    assert result.stdout.strip().endswith("nothing new")
    assert len(env.calls()) == 1
    env.append(t, {"type": "user", "message": {"role": "user", "content": "And for May?"}})
    extract(env, t)
    second = env.calls()[1]["stdin"]
    assert "USER: And for May?" in second and QUOTE not in second


def test_long_transcripts_are_split_into_chunks(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[f"row {i} " + "x" * 3500 for i in range(80)])
    result = extract(env, t)
    assert result.returncode == 0, result.stderr
    calls = env.calls()
    assert len(calls) >= 3
    assert all(len(call["stdin"]) <= 121_000 for call in calls)
    assert "row 0 " in calls[0]["stdin"] and "row 79 " in calls[-1]["stdin"]


def test_a_failed_chunk_is_resent_and_finished_chunks_are_not(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[f"row {i} " + "x" * 3500 for i in range(40)])
    (env.fake / "response-1.json").write_text("not json")
    result = extract(env, t)
    assert result.returncode == 1
    assert "not the expected JSON" in result.stderr
    assert extract(env, t).returncode == 0
    calls = env.calls()
    assert len(calls) == 3
    assert calls[2]["stdin"].count("row ") == calls[1]["stdin"].count("row ")


def test_extractor_failure_is_an_error(env):
    make_vault(env)
    (env.fake / "exit").write_text("1")
    result = extract(env, env.transcript(tool_outputs=[OUTPUT]))
    assert result.returncode == 1
    assert "extractor exited 1: fake extractor failure" in result.stderr
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_extract.py -v`
Expected: FAIL (`invalid choice: 'extract'`; the `[extractor]` test fails on `unknown keys: extractor`).

- [ ] **Step 4: Configuration and state directory**

In `src/val3/config.py` add `import os`, the constant and the function, extend `Project`, and accept `[extractor]`:

```python
DEFAULT_EXTRACTOR_MODEL = "claude-haiku-5-5"


def state_dir() -> Path:
    """Local machine state: queue, watermarks, inbox hashes, warn markers, extraction log."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "val3"
```

Add the field `extractor_model: str = DEFAULT_EXTRACTOR_MODEL` as the last field of `Project`. In `load_project`, replace `unknown = set(data) - {"service", "project", "vaults"}` with `unknown = set(data) - {"service", "project", "vaults", "extractor"}`, and replace the final `return` with:

```python
    extractor = data.get("extractor", {})
    if not isinstance(extractor, dict) or set(extractor) - {"model"}:
        raise Val3Error(f"{path}: [extractor] may only hold 'model'")
    model = extractor.get("model", DEFAULT_EXTRACTOR_MODEL)
    if not isinstance(model, str) or not model.strip():
        raise Val3Error(f"{path}: [extractor] model must be a model name")
    return Project(path.parent, name, service, home, linked, model)
```

In `src/val3/hooks.py`, import `state_dir` (`from val3.config import find_config, load_project, state_dir`), replace the line `state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "val3" / "warned"` with `state = state_dir() / "warned"`, and change the ponytail comment above `_warn_once` to `# ponytail: warn markers are never pruned; one empty file per session.`

- [ ] **Step 5: Writer outcome**

In `src/val3/writer.py`, after `Origin`:

```python
@dataclass(frozen=True)
class Outcome:
    status: str  # admitted | duplicate | queued | already-queued
    ref: str     # <vault>#<id>, or review #<n> for queued items
    reason: str | None = None

    def __str__(self) -> str:
        return {"admitted": f"admitted {self.ref}", "duplicate": f"duplicate of {self.ref}",
                "queued": f"queued for review: {self.ref} ({self.reason})",
                "already-queued": f"already queued: {self.ref}"}[self.status]
```

Change `submit`'s return annotation to `-> Outcome` and its returns to:

```python
            if existing:
                return Outcome("duplicate", f"{vault}#{existing}")
            _check_refs(conn, vault, candidate)
            reason = _review_reason(candidate)
            if reason:
                review_id, created = _queue(conn, vault, reason, candidate, None, org)
                return Outcome("queued" if created else "already-queued", f"review #{review_id}", reason)
            entry_id, created = _admit(conn, vault, candidate, org)
    except psycopg.errors.UniqueViolation:
        raise Val3Error("another session changed the same entries concurrently; run the command again") from None
    return Outcome("admitted" if created else "duplicate", f"{vault}#{entry_id}")
```

`add_tool` keeps returning a string. `cli.cmd_save` already prints the result, which now goes through `__str__`.

- [ ] **Step 6: Condensed transcript**

Replace `src/val3/transcript.py` with:

```python
"""Session transcripts (JSONL): evidence text and the condensed form read by the extractor.

Only two sources count as evidence: tool results (what a command, query, API call or test
returned) and the user's own messages. The assistant's text and thinking never count, nor do
compaction summaries or results of tools that echo text the model wrote. The transcript format
is internal to Claude Code; this module is the only place that reads it.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

from val3 import Val3Error

# Tools whose results hold text the model wrote: subagent answers and echoes of edited files.
MODEL_TEXT_TOOLS = {"Agent", "Task", "Edit", "MultiEdit", "Write", "NotebookEdit"}
# val3 itself prints stored quotes and candidates (show, review show).
_VAL3_COMMAND = re.compile(r"(?:^|[\s;&|(`/])val3[ \t]+(?:save|show|recall|review|tool|hook|vault)\b|-m[ \t]+val3\b")
CHUNK_LIMIT = 120_000
_CLIP = {"tool_result": 4_000, "tool_call": 1_000, "text": 8_000}


@dataclass(frozen=True)
class Chunk:
    text: str       # condensed lines
    end: int        # transcript lines covered once this chunk is extracted (the next watermark)
    user_text: str  # what the user wrote in it, to look up existing entries


def evidence_text(path: Path) -> tuple[str, str]:
    """Return (tool output text, user message text) of a transcript."""
    records = _load(path)
    excluded = _model_text_calls(records)
    tool: list[str] = []
    user: list[str] = []
    for record in records:
        if not _is_user(record):
            continue
        content = _content(record)
        if isinstance(content, str):
            user.append(content)
            continue
        for block in _blocks(content):
            if block.get("type") == "tool_result" and block.get("tool_use_id") not in excluded:
                tool.extend(_texts(block.get("content")))
            elif block.get("type") == "text" and isinstance(block.get("text"), str):
                user.append(block["text"])
    return _normal("\n".join(tool)), _normal("\n".join(user))


def condense(path: Path, start: int, limit: int = CHUNK_LIMIT) -> tuple[list[Chunk], int]:
    """Readable form of the transcript lines after `start`, in chunks of at most `limit` characters.

    Returns the chunks and the number of transcript lines read. Clipped blocks keep a literal
    prefix, so quotes taken from them still match the transcript."""
    records = _load(path)
    excluded = _model_text_calls(records)
    chunks: list[Chunk] = []
    parts: list[str] = []
    users: list[str] = []
    size = 0
    for index in range(start, len(records)):
        rendered = _render(records[index], excluded)
        if not rendered:
            continue
        block = "\n".join(rendered)
        if parts and size + len(block) + 1 > limit:
            chunks.append(Chunk("\n".join(parts), index, "\n".join(users)))
            parts, users, size = [], [], 0
        parts.append(block)
        size += len(block) + 1
        if _is_user(records[index]) and isinstance(_content(records[index]), str):
            users.append(_content(records[index]))
    if parts:
        chunks.append(Chunk("\n".join(parts), len(records), "\n".join(users)))
    return chunks, len(records)


def _load(path: Path) -> list[dict | None]:
    """One item per transcript line: the record, or None for a line that is not a JSON object."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise Val3Error(f"cannot read transcript {path}: {exc.strerror}") from None
    records: list[dict | None] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = None
        records.append(record if isinstance(record, dict) else None)
    return records


def _is_user(record: dict | None) -> bool:
    return (isinstance(record, dict) and record.get("type") == "user" and not record.get("isMeta")
            and not record.get("isCompactSummary") and not record.get("isVisibleInTranscriptOnly"))


def _render(record: dict | None, excluded: set) -> list[str]:
    content = _content(record) if isinstance(record, dict) else None
    if _is_user(record):
        if isinstance(content, str):
            return [f"USER: {_clip(content, 'text')}"]
        out = []
        for block in _blocks(content):
            if block.get("type") == "tool_result":
                label = "TOOL RESULT (not evidence)" if block.get("tool_use_id") in excluded else "TOOL RESULT"
                text = _normal("\n".join(_texts(block.get("content"))))
                out.append(f"{label}:\n{_clip(text, 'tool_result')}")
            elif block.get("type") == "text" and isinstance(block.get("text"), str):
                out.append(f"USER: {_clip(block['text'], 'text')}")
        return out
    if isinstance(record, dict) and record.get("type") == "assistant":
        out = []
        for block in _blocks(content):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                out.append(f"ASSISTANT: {_clip(block['text'], 'text')}")
            elif block.get("type") == "tool_use":
                tool_input = block.get("input")
                command = tool_input.get("command") if isinstance(tool_input, dict) else None
                call = command if isinstance(command, str) else json.dumps(tool_input, ensure_ascii=False)
                out.append(f"TOOL CALL {block.get('name')}: {_clip(call, 'tool_call')}")
        return out
    return []


# ponytail: a command that echoes or cats text the model wrote still passes; no reader can tell.
def _model_text_calls(records: list[dict | None]) -> set[str]:
    """Ids of tool calls whose results hold text the model wrote."""
    ids = set()
    for record in records:
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        for block in _blocks(_content(record)):
            if block.get("type") != "tool_use":
                continue
            tool_input = block.get("input")
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            if block.get("name") in MODEL_TEXT_TOOLS or (
                    block.get("name") == "Bash" and isinstance(command, str) and _VAL3_COMMAND.search(command)):
                ids.add(block.get("id"))
    return ids


def _content(record: dict) -> object:
    message = record.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _blocks(content: object) -> list[dict]:
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def _texts(content: object) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str)]
    return []


def _clip(text: str, kind: str) -> str:
    limit = _CLIP[kind]
    return text if len(text) <= limit else f"{text[:limit]}\n[… {len(text) - limit} more characters]"


def _normal(text: str) -> str:
    return text.replace("\r\n", "\n")
```

- [ ] **Step 7: Extractor prompt**

`src/val3/extractor_prompt.md`:

````markdown
VAL3 extractor. Prompt version: 1.

You read one slice of a coding-agent session and propose entries for a verified memory that later
sessions, on any machine, receive instead of investigating again. Answer only through the JSON
schema: {"candidates": [{"note": <memory note file name or null>, "candidate": <candidate>}]}.
An empty list is the right answer when the slice established nothing worth keeping.

What to keep
- fact: one statement about the environment, data, systems or project that the session checked
  against a database, a program, an API, a test or the user, and that a later session would
  otherwise have to check again.
- recipe: the correct path to answer a kind of question, completed in this session: declared
  parameters, ordered steps, the facts each step uses (only refs listed under "Existing
  entries"), and this session's run as a worked example.
- preference: how the user wants work done, in the user's own words.
- decision: a choice the user made, in the user's own words.
- tool: an external skill, MCP server or CLI that answered a derivable question in this session.

What not to keep
- trial and error, failed attempts, intermediate states, plans, pending work, progress reports;
- anything a live source answers (code structure, schemas, file contents); propose the tool that
  answered it instead, if there was one;
- anything already listed under "Existing entries"; propose "supersedes": "<ref>" only when the
  slice proves that entry wrong, with the new value and its own evidence;
- passwords, tokens, keys and personal data, in any field.

Evidence (the writer rejects candidates that break these rules)
- Every fact, preference and decision needs evidence. A quote is copied character for character
  from the transcript: query, command, api and test quotes from a "TOOL RESULT:" block; user
  quotes from a "USER:" line. Never quote "TOOL RESULT (not evidence):", ASSISTANT, TOOL CALL or
  memory notes. Keep line breaks and spacing; never paraphrase, never join separate blocks, never
  include a "[… N more characters]" marker.
- Quote the distinctive part that proves the statement (at least 8 characters), not generic text
  such as "(1 row)" or "OK".
- reproduction: the exact query, command or call that produced the quoted output, taken from the
  TOOL CALL line before it; empty for user evidence.
- A fact backed only by the user's word goes to human review; use tool evidence when it exists.

Candidate format
{FORMAT}

Write titles and statements in the language the user wrote in. Keywords: 3 to 8 search terms,
in English and in Spanish. indexes: short lowercase names such as flows, modules, billing.

Memory notes
"Memory notes" are what the session's agent wrote down. They are claims, not evidence: propose a
candidate from a note only when the transcript proves it, and set "note" to the note's file name.
````

In `pyproject.toml`, change the package data to `val3 = ["*.sql", "*.md"]`, then reinstall: `.venv/bin/pip install -q -e '.[dev]'`.

- [ ] **Step 8: Write `extract.py`**

```python
"""Extraction: unseen transcript lines -> extractor (`claude -p`) -> candidates -> writer."""
import hashlib
import json
import os
import shlex
import subprocess
from importlib.resources import files
from pathlib import Path

from val3 import Val3Error
from val3.candidate import FORMAT
from val3.config import Project, state_dir
from val3.search import search
from val3.transcript import condense
from val3.writer import submit

PROMPT_VERSION = 1
PROMPT = files("val3").joinpath("extractor_prompt.md").read_text(encoding="utf-8").replace("{FORMAT}", FORMAT)
TIMEOUT = 600
SCHEMA = {"type": "object", "required": ["candidates"], "properties": {"candidates": {"type": "array", "items": {
    "type": "object", "required": ["candidate"],
    "properties": {"note": {"type": ["string", "null"]}, "candidate": {"type": "object"}}}}}}
STATUSES = ("admitted", "duplicate", "queued", "already-queued", "rejected")


class ExtractorError(Exception):
    """The extractor gave no usable answer; the queue entry is retried."""


def extract(conn, project: Project, transcript: Path, session: str) -> dict:
    """Send the unseen lines of a session transcript to the extractor and submit its candidates.

    The watermark advances after each chunk, so a failure never resends finished chunks."""
    chunks, total = condense(transcript, _watermark(session))
    counts = dict.fromkeys(STATUSES, 0) | {"calls": 0, "cost_usd": 0.0}
    for chunk in chunks:
        existing = search(conn, project.vaults, chunk.user_text[:5_000], limit=30) if chunk.user_text.strip() else []
        result = run_extractor(project, _material(project, existing, chunk.text))
        counts["calls"] += 1
        counts["cost_usd"] += float(result.get("total_cost_usd") or 0)
        for item in result["structured_output"]["candidates"]:
            try:
                outcome = submit(conn, project, item.get("candidate") if isinstance(item, dict) else item,
                                 transcript, session)
            except Val3Error:
                counts["rejected"] += 1  # invalid, or discarded (the vault logs discards)
                continue
            counts[outcome.status] += 1
        _set_watermark(session, chunk.end)
    _set_watermark(session, total)
    return counts


def run_extractor(project: Project, material: str) -> dict:
    command = [*shlex.split(os.environ.get("VAL3_EXTRACTOR", "claude")), "-p",
               "--model", project.extractor_model, "--setting-sources", "", "--tools", "",
               "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
               "--output-format", "json", "--json-schema", json.dumps(SCHEMA), "--system-prompt", PROMPT]
    workdir = state_dir() / "extractor"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        done = subprocess.run(command, input=material, capture_output=True, text=True, timeout=TIMEOUT,
                              cwd=workdir, env={**os.environ, "VAL3_EXTRACTING": "1"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExtractorError(f"extractor did not run: {exc}") from None
    if done.returncode != 0:
        raise ExtractorError(f"extractor exited {done.returncode}: {done.stderr.strip()[:300]}")
    try:
        result = json.loads(done.stdout)
        candidates = result["structured_output"]["candidates"]
    except (ValueError, KeyError, TypeError):
        raise ExtractorError("extractor output is not the expected JSON") from None
    if result.get("is_error") or not isinstance(candidates, list):
        raise ExtractorError("extractor output is not the expected JSON")
    return result


def summary(counts: dict) -> str:
    if not counts["calls"]:
        return "nothing new"
    return (f"admitted {counts['admitted']}, duplicate {counts['duplicate']}, queued {counts['queued']}, "
            f"already queued {counts['already-queued']}, rejected {counts['rejected']}; "
            f"{counts['calls']} extractor calls, ${counts['cost_usd']:.4f}")


def _material(project: Project, existing, transcript_text: str) -> str:
    lines = [f"# Session slice of project {project.name} (home vault {project.home})", "",
             "## Existing entries",
             'Already stored: do not propose them again. Use "supersedes" only when the slice proves one wrong.']
    lines += [f"- {hit.ref} [{hit.kind}] {hit.title}: {' '.join(hit.statement.split())[:300]}"
              for hit in existing] or ["(none found)"]
    lines += ["", "## Transcript", transcript_text]
    return "\n".join(lines)


def _watermark_path(session: str) -> Path:
    return state_dir() / "sessions" / f"{hashlib.sha256(session.encode()).hexdigest()}.json"


def _watermark(session: str) -> int:
    try:
        return int(json.loads(_watermark_path(session).read_text())["lines"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _set_watermark(session: str, lines: int) -> None:
    path = _watermark_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"lines": lines}))
    os.replace(tmp, path)
```

- [ ] **Step 9: Add `extract` to `cli.py`**

Add `from val3.extract import ExtractorError, extract, summary`. In `build_parser`, before `return parser`:

```python
    extract_parser = sub.add_parser("extract", help="extract verified entries from a session transcript now")
    extract_parser.add_argument("transcript", type=Path)
    extract_parser.add_argument("--session", help="session id (default: the transcript file name)")
    extract_parser.set_defaults(func=cmd_extract)
```

```python
def cmd_extract(args) -> None:
    project = load_project(Path.cwd())
    with connect(project.service) as conn:
        try:
            counts = extract(conn, project, args.transcript, args.session or args.transcript.stem)
        except ExtractorError as exc:
            raise Val3Error(str(exc)) from None
    print(f"{args.transcript.name}: {summary(counts)}")
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `.venv/bin/pytest -q`
Expected: all passed (plan-1 tests included: `evidence_text` keeps its behaviour, `val3 save` prints the same messages).

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml src tests
git commit -m "feat: val3 extract sends unseen transcript lines to an isolated claude -p extractor"
```

---

### Task 2: Pending queue, drainer and capture hook

**Files:**
- Create: `src/val3/pending.py`, `tests/test_pending.py`
- Modify: `src/val3/hooks.py`, `src/val3/cli.py`

**Interfaces:**
- Consumes: `extract.extract`, `extract.ExtractorError`, `config.state_dir`, `config.load_project`, `db.connect`.
- Produces: `pending.MAX_ATTEMPTS = 3`; `pending.queue_dir() -> Path`; `pending.enqueue(data: dict) -> Path`; `pending.entries() -> list[Path]`; `pending.failed_entries() -> list[Path]`; `pending.spawn_drain() -> None`; `pending.drain(wait: bool) -> int`; `pending.describe() -> str`; hook handler `hooks.capture(data) -> None`. Log lines in `extract.log`: `at, entry, session, outcome` (`done`, `retry`, `failed`, `stop`) plus counts or `error`.

- [ ] **Step 1: Write the failing tests**

`tests/test_pending.py`:

```python
import json

from helpers import OUTPUT, fact, make_vault


def capture(env, transcript, session=None, cwd=None, **environ):
    data = {"session_id": session or env.session, "transcript_path": str(transcript),
            "cwd": str(cwd or env.project), "hook_event_name": "SessionEnd", "reason": "other"}
    return env.run("hook", "capture", input=json.dumps(data), environ=environ)


def queued(env):
    return sorted((env.state / "queue").glob("*.json"))


def log(env):
    path = env.state / "extract.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def entries(env):
    return env.sql(f"SELECT count(*) FROM {env.vault}.entry")[0][0]


def leave_entry(env, transcript, attempts=0):
    queue = env.state / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    (queue / "1-1.json").write_text(json.dumps(
        {"session_id": env.session, "transcript_path": str(transcript), "cwd": str(env.project),
         "event": "SessionEnd", "queued_at": 0, "attempts": attempts}))


def test_session_end_is_queued_and_drained_in_the_background(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    env.respond([{"note": None, "candidate": fact()}])
    result = capture(env, t)
    assert result.returncode == 0 and result.stdout == ""
    env.wait(lambda: entries(env) == 1 and not queued(env))
    assert [line["outcome"] for line in log(env)] == ["done"]
    assert log(env)[0]["admitted"] == 1 and log(env)[0]["session"] == env.session


def test_capture_does_nothing_outside_adopted_projects_or_inside_the_extractor(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    elsewhere = env.root / "elsewhere"
    elsewhere.mkdir()
    assert capture(env, t, cwd=elsewhere).returncode == 0
    assert capture(env, t, VAL3_EXTRACTING="1").returncode == 0
    assert queued(env) == [] and env.calls() == []


def test_two_sessions_captured_together_are_both_drained(env):
    make_vault(env)
    first = env.transcript(tool_outputs=[OUTPUT], session="session-a")
    second = env.transcript(tool_outputs=[OUTPUT], session="session-b")
    env.respond([{"note": None, "candidate": fact()}],
                [{"note": None, "candidate": fact(title="April invoices counted", statement="April: 42 invoices.")}])
    capture(env, first, session="session-a")
    capture(env, second, session="session-b")
    env.wait(lambda: entries(env) == 2 and not queued(env))
    assert sorted(line["session"] for line in log(env) if line["outcome"] == "done") == ["session-a", "session-b"]


def test_server_down_keeps_entries_without_spending_attempts(env):
    env.config(service="val3down")
    t = env.transcript(tool_outputs=[OUTPUT])
    capture(env, t)
    env.wait(lambda: any(line["outcome"] == "stop" for line in log(env)))
    assert len(queued(env)) == 1
    assert json.loads(queued(env)[0].read_text())["attempts"] == 0
    env.config()
    assert env.run("vault", "create", env.vault).returncode == 0
    env.respond([{"note": None, "candidate": fact()}])
    assert env.run("drain").stdout.strip() == "drained 1 entries"
    assert entries(env) == 1 and queued(env) == []


def test_failed_extractions_are_retried_then_set_aside(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    (env.fake / "exit").write_text("1")
    leave_entry(env, t)
    for attempt in (1, 2):
        assert env.run("drain").stdout.strip() == "drained 0 entries"
        assert json.loads(queued(env)[0].read_text())["attempts"] == attempt
    env.run("drain")
    assert queued(env) == []
    listing = env.run("queue").stdout
    assert "failed" in listing and "extractor exited 1" in listing
    assert [line["outcome"] for line in log(env)] == ["retry", "retry", "failed"]


def test_missing_transcript_is_set_aside_and_the_queue_moves_on(env):
    make_vault(env)
    leave_entry(env, env.root / "gone.jsonl")
    t = env.transcript(tool_outputs=[OUTPUT], session="session-b")
    queue = env.state / "queue"
    (queue / "2-1.json").write_text(json.dumps(
        {"session_id": "session-b", "transcript_path": str(t), "cwd": str(env.project),
         "event": "SessionEnd", "queued_at": 0, "attempts": 0}))
    env.respond([{"note": None, "candidate": fact()}])
    assert env.run("drain").stdout.strip() == "drained 1 entries"
    assert "cannot read transcript" in env.run("queue").stdout
    assert entries(env) == 1


def test_session_start_drains_left_over_entries(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    leave_entry(env, t)
    env.respond([{"note": None, "candidate": fact()}])
    data = {"session_id": "next-session", "transcript_path": str(t), "cwd": str(env.project),
            "hook_event_name": "SessionStart", "source": "startup"}
    assert env.run("hook", "session-start", input=json.dumps(data)).returncode == 0
    env.wait(lambda: entries(env) == 1 and not queued(env))


def test_queue_lists_pending_entries(env):
    make_vault(env)
    leave_entry(env, env.transcript(tool_outputs=[OUTPUT]))
    listing = env.run("queue").stdout
    assert f"pending  1-1.json  session {env.session}  SessionEnd  attempts 0" in listing
    (env.state / "queue" / "1-1.json").unlink()
    assert env.run("queue").stdout.strip() == "queue empty"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pending.py -v`
Expected: FAIL (`invalid choice: 'capture'`, `'drain'`, `'queue'`).

- [ ] **Step 3: Write `pending.py`**

```python
"""The pending queue: one file per captured session event, drained by one process at a time."""
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from val3 import Val3Error
from val3.config import load_project, state_dir
from val3.db import connect
from val3.extract import ExtractorError, extract

MAX_ATTEMPTS = 3


def queue_dir() -> Path:
    return state_dir() / "queue"


def enqueue(data: dict) -> Path:
    directory = queue_dir()
    directory.mkdir(parents=True, exist_ok=True)
    entry = {"session_id": str(data["session_id"]), "transcript_path": str(data["transcript_path"]),
             "cwd": str(data.get("cwd") or os.getcwd()), "event": str(data.get("hook_event_name") or "manual"),
             "queued_at": time.time(), "attempts": 0}
    path = directory / f"{time.time_ns()}-{os.getpid()}.json"
    _write(path, entry)
    return path


def entries() -> list[Path]:
    return sorted(queue_dir().glob("*.json"))


def failed_entries() -> list[Path]:
    return sorted((queue_dir() / "failed").glob("*.json"))


def spawn_drain() -> None:
    """Start a detached drainer that outlives the hook that started it."""
    state_dir().mkdir(parents=True, exist_ok=True)
    subprocess.Popen([sys.executable, "-m", "val3", "drain", "--if-idle"], cwd=state_dir(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def drain(wait: bool) -> int:
    """Process every pending entry at most once; return how many completed.

    One drainer runs at a time. Without `wait`, a held lock means another drainer is working,
    and it takes the entries queued meanwhile before it releases the lock."""
    seen: set[Path] = set()
    done = 0
    while any(path not in seen for path in entries()):
        with _lock(wait) as held:
            if not held:
                return done
            for path in entries():
                if path in seen:
                    continue
                seen.add(path)
                outcome = _process(path)
                if outcome == "stop":
                    return done
                done += outcome == "done"
    return done


def describe() -> str:
    lines = []
    for state, paths in (("pending", entries()), ("failed", failed_entries())):
        for path in paths:
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                entry = {}
            line = (f"{state}  {path.name}  session {entry.get('session_id', '?')}  {entry.get('event', '?')}  "
                    f"attempts {entry.get('attempts', 0)}")
            if entry.get("error"):
                line += f"  error: {entry['error']}"
            lines.append(line)
    return "\n".join(lines) or "queue empty"


@contextmanager
def _lock(wait: bool):
    queue_dir().mkdir(parents=True, exist_ok=True)
    with open(queue_dir() / ".lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def _process(path: Path) -> str:
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "skip"
    except ValueError:
        return _set_aside(path, {}, "unreadable queue entry")
    try:
        cwd = Path(entry["cwd"])
        transcript = Path(entry["transcript_path"])
        session = str(entry["session_id"])
        project = load_project(cwd)
        conn = connect(project.service)
    except psycopg.OperationalError as exc:
        _log(path, entry, "stop", error=_first_line(exc))
        return "stop"
    except (Val3Error, KeyError, TypeError) as exc:
        return _set_aside(path, entry, _first_line(exc))
    try:
        with conn:
            counts = extract(conn, project, transcript, session)
    except psycopg.OperationalError as exc:
        _log(path, entry, "stop", error=_first_line(exc))
        return "stop"
    except ExtractorError as exc:
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        entry["error"] = str(exc)
        if entry["attempts"] >= MAX_ATTEMPTS:
            return _set_aside(path, entry, str(exc))
        _write(path, entry)
        _log(path, entry, "retry", error=str(exc))
        return "retry"
    except (Val3Error, psycopg.Error) as exc:
        return _set_aside(path, entry, _first_line(exc))
    path.unlink(missing_ok=True)
    _log(path, entry, "done", **counts)
    return "done"


def _set_aside(path: Path, entry: object, error: str) -> str:
    entry = entry if isinstance(entry, dict) else {}
    target = queue_dir() / "failed" / path.name
    target.parent.mkdir(parents=True, exist_ok=True)
    _write(target, {**entry, "error": error})
    path.unlink(missing_ok=True)
    _log(path, entry, "failed", error=error)
    return "failed"


# ponytail: extract.log is never rotated; one short line per drained entry.
def _log(path: Path, entry: object, outcome: str, **detail) -> None:
    session = entry.get("session_id") if isinstance(entry, dict) else None
    record = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "entry": path.name,
              "session": session, "outcome": outcome, **detail}
    state_dir().mkdir(parents=True, exist_ok=True)
    with open(state_dir() / "extract.log", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__
```

- [ ] **Step 4: Capture handler and drain trigger in `hooks.py`**

Add `from val3 import pending`. Change `_EVENTS` to `{"session-start": "SessionStart", "prompt": "UserPromptSubmit", "capture": None}` (capture output is discarded by the harness), and `_HANDLERS` to `{"session-start": session_start, "prompt": prompt, "capture": capture}`. In `run`, a capture failure must not spend the session's single warning, so replace the `except` body and the output condition:

```python
    except Exception as exc:  # fail open: a hook must never break the session
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        context = _warn_once(data, f"VAL3 is unavailable ({detail}); vault memory is not loaded.") if _EVENTS[event] else None
    if context:
``` In `session_start`, right after `_export_session(data)`:

```python
    if pending.entries():
        pending.spawn_drain()
```

New handler:

```python
def capture(data: dict) -> None:
    """PreCompact and SessionEnd: queue the session for extraction and start a drainer."""
    if os.environ.get("VAL3_EXTRACTING") or not data.get("session_id") or not data.get("transcript_path"):
        return None
    if find_config(Path(data.get("cwd") or os.getcwd())) is None:
        return None
    pending.enqueue(data)
    pending.spawn_drain()
    return None
```

- [ ] **Step 5: Add `drain`, `queue` and the hook choice to `cli.py`**

Add `from val3 import pending` (merge into the existing `from val3 import ...` line). Change the hook choices to `("session-start", "prompt", "capture")`. In `build_parser`, before `return parser`:

```python
    drain_parser = sub.add_parser("drain", help="extract every queued session now")
    drain_parser.add_argument("--if-idle", action="store_true", help="exit at once if another drainer runs")
    drain_parser.set_defaults(func=cmd_drain)

    queue_parser = sub.add_parser("queue", help="list queued and failed captures")
    queue_parser.set_defaults(func=lambda args: print(pending.describe()))
```

```python
def cmd_drain(args) -> None:
    print(f"drained {pending.drain(wait=not args.if_idle)} entries")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest -q`
Expected: all passed.

- [ ] **Step 7: Commit**

```bash
git add src tests
git commit -m "feat: capture hook, pending queue and single detached drainer"
```

---

### Task 3: Inbox notes

**Files:**
- Create: `src/val3/inbox.py`, `tests/test_inbox.py`
- Modify: `src/val3/extract.py`, `src/val3/pending.py`, `src/val3/cli.py`

**Interfaces:**
- Consumes: `config.state_dir`, `transcript.condense`, `writer.Outcome.status/ref`.
- Produces: `inbox.INDEX = "MEMORY.md"`, `inbox.LOCK = ".consolidate-lock"`; `inbox.memory_dir(cwd: Path) -> Path`; `inbox.changed_notes(memdir: Path, session: str) -> list[tuple[str, str]]` (file name, text); `inbox.settle(memdir, notes, refs: dict[str, list[str]]) -> None`. `extract.extract(conn, project, transcript, session, cwd: Path) -> dict` (new `cwd` parameter); `extract._material(project, existing, transcript_text, notes=())`.

- [ ] **Step 1: Write the failing tests**

`tests/test_inbox.py`:

```python
import json
import re
import subprocess

from helpers import OUTPUT, fact, make_vault

NOTE = ("---\nname: april-invoices\ndescription: April invoice count\nmetadata:\n  type: project\n"
        "  originSessionId: {session}\n---\n\nApril has 42 invoices (checked with psql).\n")
INDEX = "- [April invoices](april.md) — count\n"


def memdir(env, root=None):
    slug = re.sub(r"[^A-Za-z0-9]", "-", str((root or env.project).resolve()))
    directory = env.home / ".claude" / "projects" / slug / "memory"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_note(directory, session, name="april.md"):
    (directory / name).write_text(NOTE.format(session=session))
    (directory / "MEMORY.md").write_text(INDEX)


def extract(env, transcript, cwd=None):
    result = env.run("extract", str(transcript), cwd=cwd)
    assert result.returncode == 0, result.stderr
    return result


def test_changed_notes_reach_the_extractor_and_are_rewritten(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    env.respond([{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    stdin = env.calls()[0]["stdin"]
    assert "### april.md" in stdin and "April has 42 invoices (checked with psql)." in stdin
    text = (directory / "april.md").read_text()
    assert text.startswith("---\nname: april-invoices\n")
    assert f"Recorded in VAL3 as {env.vault}#1 (verified)" in text
    assert "checked with psql" not in text
    assert (directory / "MEMORY.md").read_text() == INDEX


def test_notes_of_other_sessions_are_not_sent(env):
    make_vault(env)
    write_note(memdir(env), "another-session")
    extract(env, env.transcript(tool_outputs=[OUTPUT]))
    assert "### april.md" not in env.calls()[0]["stdin"]


def test_notes_without_entries_are_kept_and_not_resent(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    extract(env, t)
    assert (directory / "april.md").read_text() == NOTE.format(session=env.session)
    env.append(t, {"type": "user", "message": {"role": "user", "content": "And for May?"}})
    extract(env, t)
    assert "### april.md" not in env.calls()[1]["stdin"]


def test_no_rewrite_while_the_harness_consolidates(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    directory = memdir(env)
    write_note(directory, env.session)
    (directory / ".consolidate-lock").write_text('{"holderPid": 1}')
    env.respond([{"note": "april.md", "candidate": fact()}], [{"note": "april.md", "candidate": fact()}])
    extract(env, t)
    assert (directory / "april.md").read_text() == NOTE.format(session=env.session)
    (directory / ".consolidate-lock").unlink()
    extract(env, t)
    assert "### april.md" in env.calls()[1]["stdin"]
    assert f"Recorded in VAL3 as {env.vault}#1" in (directory / "april.md").read_text()


def test_memory_directory_follows_the_git_root(env):
    make_vault(env)
    subprocess.run(["git", "init", "-q", str(env.project)], check=True)
    sub = env.project / "sub"
    sub.mkdir()
    write_note(memdir(env), env.session)
    extract(env, env.transcript(tool_outputs=[OUTPUT]), cwd=sub)
    assert "### april.md" in env.calls()[0]["stdin"]


def test_memory_directory_setting_is_honoured(env):
    make_vault(env)
    custom = env.root / "notes"
    custom.mkdir()
    (env.project / ".claude").mkdir()
    (env.project / ".claude" / "settings.json").write_text(json.dumps({"autoMemoryDirectory": str(custom)}))
    write_note(custom, env.session)
    extract(env, env.transcript(tool_outputs=[OUTPUT]))
    assert "### april.md" in env.calls()[0]["stdin"]


def test_notes_written_after_the_last_extraction_use_the_transcript_end(env):
    make_vault(env)
    t = env.transcript(tool_outputs=[OUTPUT])
    extract(env, t)
    write_note(memdir(env), env.session)
    env.respond([], [{"note": "april.md", "candidate": fact()}])
    result = extract(env, t)
    assert "admitted 1" in result.stdout
    second = env.calls()[1]["stdin"]
    assert "### april.md" in second and "TOOL RESULT:\ninvoice_count" in second
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_inbox.py -v`
Expected: FAIL (`### april.md` missing from the material).

- [ ] **Step 3: Write `inbox.py`**

```python
"""The inbox: the harness's auto-memory directory. VAL3 reads it and annotates notes, never owns it."""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from val3.config import state_dir

INDEX = "MEMORY.md"
LOCK = ".consolidate-lock"
_ORIGIN = re.compile(r"^\s*originSessionId:\s*['\"]?([0-9A-Za-z_-]+)", re.M)


# ponytail: the harness shortens slugs of paths over 200 characters; such projects need autoMemoryDirectory.
def memory_dir(cwd: Path) -> Path:
    """The directory the harness uses for this cwd: one per git repository, worktrees included."""
    root = _repository_root(cwd)
    configured = _setting(root, "autoMemoryDirectory")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root)) / "memory"


def changed_notes(memdir: Path, session: str) -> list[tuple[str, str]]:
    """Notes changed since VAL3 last read them, written by this session or by an unknown one."""
    known = _known(memdir)
    notes = []
    for path in sorted(memdir.glob("*.md")) if memdir.is_dir() else []:
        if path.name == INDEX:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if known.get(path.name) == _digest(text):
            continue
        origin = _ORIGIN.search(_frontmatter(text))
        if origin and origin[1] != session:
            continue
        notes.append((path.name, text))
    return notes


def settle(memdir: Path, notes: list[tuple[str, str]], refs: dict[str, list[str]]) -> None:
    """Rewrite notes that produced entries as references and remember every processed note.

    While the harness consolidates the directory, notes are left untouched and unrecorded."""
    if notes and (memdir / LOCK).exists():
        return
    known = _known(memdir)
    for name, text in notes:
        if refs.get(name):
            text = _rewrite(memdir / name, text, list(dict.fromkeys(refs[name])))
        known[name] = _digest(text)
    _write_json(_known_path(memdir), known)


def _rewrite(path: Path, text: str, refs: list[str]) -> str:
    front = _frontmatter_block(text)
    body = (f"Recorded in VAL3 as {', '.join(refs)} (verified). Read it with `val3 show {refs[0]}`; "
            "search with `val3 recall <terms>`.\n")
    new = f"{front}\n\n{body}" if front else body
    tmp = path.with_name(path.name + ".val3tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    return new


def _frontmatter_block(text: str) -> str:
    end = text.find("\n---", 4) if text.startswith("---\n") else -1
    return text[: end + 4] if end != -1 else ""


def _frontmatter(text: str) -> str:
    return _frontmatter_block(text)[4:]


def _repository_root(cwd: Path) -> Path:
    try:
        done = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return cwd.resolve()
    return Path(done.stdout.strip()).parent if done.returncode == 0 else cwd.resolve()


def _setting(root: Path, key: str) -> str | None:
    for path in (root / ".claude" / "settings.local.json", root / ".claude" / "settings.json",
                 Path.home() / ".claude" / "settings.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get(key)
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(value, str) and value.strip():
            return value
    return None


def _known_path(memdir: Path) -> Path:
    return state_dir() / "inbox" / f"{hashlib.sha256(str(memdir).encode()).hexdigest()}.json"


def _known(memdir: Path) -> dict:
    try:
        data = json.loads(_known_path(memdir).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)
```

- [ ] **Step 4: Use the inbox in `extract.py`**

Add `from val3 import inbox`. Replace `extract` and `_material` with:

```python
def extract(conn, project: Project, transcript: Path, session: str, cwd: Path) -> dict:
    """Send the unseen lines of a session transcript, and the notes it wrote, to the extractor.

    The watermark advances after each chunk, so a failure never resends finished chunks. Notes go
    with the last chunk; with no new lines they go with the end of the transcript."""
    memdir = inbox.memory_dir(cwd)
    notes = inbox.changed_notes(memdir, session)
    chunks, total = condense(transcript, _watermark(session))
    if notes and not chunks:
        chunks = condense(transcript, 0)[0][-1:]
    counts = dict.fromkeys(STATUSES, 0) | {"calls": 0, "cost_usd": 0.0}
    refs: dict[str, list[str]] = {name: [] for name, _ in notes}
    for i, chunk in enumerate(chunks):
        existing = search(conn, project.vaults, chunk.user_text[:5_000], limit=30) if chunk.user_text.strip() else []
        last = i == len(chunks) - 1
        result = run_extractor(project, _material(project, existing, chunk.text, notes if last else ()))
        counts["calls"] += 1
        counts["cost_usd"] += float(result.get("total_cost_usd") or 0)
        for item in result["structured_output"]["candidates"]:
            note = item.get("note") if isinstance(item, dict) else None
            try:
                outcome = submit(conn, project, item.get("candidate") if isinstance(item, dict) else item,
                                 transcript, session)
            except Val3Error:
                counts["rejected"] += 1  # invalid, or discarded (the vault logs discards)
                continue
            counts[outcome.status] += 1
            if note in refs and outcome.status in ("admitted", "duplicate"):
                refs[note].append(outcome.ref)
        _set_watermark(session, chunk.end)
    _set_watermark(session, max(total, _watermark(session)))
    inbox.settle(memdir, notes, refs)
    return counts


def _material(project: Project, existing, transcript_text: str, notes=()) -> str:
    lines = [f"# Session slice of project {project.name} (home vault {project.home})", "",
             "## Existing entries",
             'Already stored: do not propose them again. Use "supersedes" only when the slice proves one wrong.']
    lines += [f"- {hit.ref} [{hit.kind}] {hit.title}: {' '.join(hit.statement.split())[:300]}"
              for hit in existing] or ["(none found)"]
    if notes:
        lines += ["", "## Memory notes",
                  "Written by the session's agent: claims, not evidence. Propose a candidate from a note only "
                  'when the transcript proves it, and set "note" to its file name.']
        for name, text in notes:
            lines += ["", f"### {name}", text[:4_000]]
    lines += ["", "## Transcript", transcript_text]
    return "\n".join(lines)
```

Update the two callers: in `cli.cmd_extract`, `extract(conn, project, args.transcript, args.session or args.transcript.stem, Path.cwd())`; in `pending._process`, `extract(conn, project, transcript, session, cwd)`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "feat: inbox notes go to the extractor and are rewritten as vault references"
```

---

### Task 4: Adoption documentation

**Files:**
- Modify: `README.md`, `CONTEXT.md`

**Interfaces:** none.

- [ ] **Step 1: README**

Change the status line to `Status: plans 1 and 2 (store, writer, review list, recall, delivery and capture hooks).`. Replace the hooks snippet in "Claude Code hooks" with:

```json
{
  "autoDreamEnabled": false,
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "val3 hook session-start"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "val3 hook prompt"}]}],
    "PreCompact": [{"hooks": [{"type": "command", "command": "val3 hook capture"}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "val3 hook capture"}]}]
  }
}
```

and add after its explanatory paragraph:

```markdown
`capture` queues the session (a file under `~/.local/state/val3/queue/`) and starts a detached
drainer. The drainer sends the session's unseen transcript lines and the memory notes it wrote to
an isolated `claude -p` (no tools, hooks, MCP servers or settings), submits each candidate through
the writer, and rewrites notes that produced entries as references. Sessions left in the queue
(server down, extractor error) are retried at the next session start; after 3 failed extractions
an entry moves to `queue/failed/`. `autoDreamEnabled: false` stops the harness from consolidating
the notes VAL3 reads.
```

Add `[extractor]` to the `val3.toml` example:

```toml
[extractor]
model = "claude-haiku-5-5"   # optional; the model of the detached extractor
```

Add to "Commands":

```markdown
- `val3 extract <transcript>` — extract a session transcript now (only lines not extracted before).
- `val3 drain` — extract every queued session now; `val3 queue` — queued and failed captures.
```

- [ ] **Step 2: CONTEXT.md**

Append:

```markdown
- **Queue entry** — one file per captured `PreCompact`/`SessionEnd`; removed once its extraction
  committed.
- **Drainer** — the single process per machine that extracts queued sessions.
- **Watermark** — per session, the number of transcript lines already extracted.
```

- [ ] **Step 3: Commit**

```bash
git add README.md CONTEXT.md
git commit -m "docs: capture hooks, extractor and queue in the adoption guide"
```

---

### Task 5: Extractor evaluation on real transcripts (Haiku 5.5 against Sonnet 5.5)

Manual task against the development server; no test files. It answers whether Haiku 5.5 is good enough as the default.

**Files:**
- Modify: `src/val3/extractor_prompt.md` and `src/val3/config.py` only if the results call for it (with a new prompt version or default model, and a `Ruling:` in the ledger).

- [ ] **Step 1: Pick three transcripts**

From `~/.claude/projects/*/`, pick three session transcripts of real work with at least 50 tool results, one of them a VAL3 session. Note their paths and sizes.

- [ ] **Step 2: Run both models into scratch vaults**

For each model in `claude-haiku-5-5` and `claude-sonnet-5-5`: create a scratch project directory under the session scratchpad with

```toml
service = "val3dev"
[vaults]
home = "eval_<haiku|sonnet>"
[extractor]
model = "<model>"
```

then `val3 vault create eval_<haiku|sonnet>` and, with `XDG_STATE_HOME` set to a directory of that model's own (separate watermarks), `val3 extract <transcript>` for each of the three transcripts.

- [ ] **Step 3: Measure**

Per model: extractor calls, cost, admitted, queued, rejected; from the vault, discards (`SELECT count(*) FROM eval_x.event WHERE action = 'discarded'`); and a reading of every admitted and queued title and statement, marking each as worth keeping or not. Record a table in `SESSION.md`.

- [ ] **Step 4: Decide**

Keep `claude-haiku-5-5` as the default unless it admits clearly fewer useful entries or many more worthless ones than Sonnet 5.5. Present the table to the owner with the recommendation before changing the default. Prompt fixes found in the reading (a rule the extractor broke repeatedly) go into `extractor_prompt.md` as prompt version 2, with `PROMPT_VERSION = 2` and the version line updated, followed by `.venv/bin/pytest -q`.

- [ ] **Step 5: Clean up and commit**

`DROP SCHEMA eval_haiku CASCADE; DROP SCHEMA eval_sonnet CASCADE;` on `val3dev`; remove the scratch directories. Commit any prompt or default change with the results summary in the message.

---

### Task 6: Session close

**Files:**
- Modify: `TODO.md`, `SESSION.md`

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all passed.

- [ ] **Step 2: Update `TODO.md` and `SESSION.md`**

`TODO.md`: plan 2 done; plan 3 stays (review page, `--tty`, `val3 import`, `val3 vault backup`, plan-1 deferred minors still open); production host stays. `SESSION.md`: a dated entry with what was built, the test count, the evaluation table and the model decision.

- [ ] **Step 3: Commit**

```bash
git add TODO.md SESSION.md
git commit -m "docs: session log for plan 2"
```
