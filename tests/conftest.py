"""Test harness: a disposable PostgreSQL in Docker and a CLI runner.

Tests drive `python -m val3` as a subprocess and assert on exit codes, output and
database rows. They never import val3.
"""
import json
import os
import pty
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from fake_embedder import FakeEmbedder

FAKE_EXTRACTOR = Path(__file__).with_name("fake_extractor.py")


@pytest.fixture(scope="session")
def pg_port():
    name = f"val3-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name,
         "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-p", "127.0.0.1::5432", "pgvector/pgvector:0.8.1-pg16"],
        check=True, capture_output=True,
    )
    try:
        out = subprocess.run(["docker", "port", name, "5432/tcp"],
                             check=True, capture_output=True, text=True).stdout
        port = out.splitlines()[0].rsplit(":", 1)[1]
        admin = f"host=127.0.0.1 port={port} user=postgres dbname=postgres"
        for _ in range(120):
            try:
                with psycopg.connect(admin, autocommit=True) as conn:
                    conn.execute("CREATE DATABASE val3")
                break
            except psycopg.OperationalError:
                time.sleep(0.5)
        else:
            raise RuntimeError("the PostgreSQL test container did not accept connections")
        yield port
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


class Env:
    """One test's world: a HOME, a project tree, a service file and a fresh vault name."""

    def __init__(self, root: Path, port: str):
        self.root = root
        self.port = port
        self.home = root / "home"
        self.project = root / "project"
        self.home.mkdir()
        self.project.mkdir()
        suffix = uuid.uuid4().hex[:10]
        self.vault = f"v_{suffix}"
        self.reader = f"r_{suffix}"
        self.writer = f"w_{suffix}"
        self.session = str(uuid.uuid4())
        services = {"val3test": ("postgres", port), "val3down": ("postgres", "1"),
                    "val3reader": (self.reader, port), "val3writer": (self.writer, port)}
        service_file = root / "pg_service.conf"
        service_file.write_text("".join(
            f"[{name}]\nhost=127.0.0.1\nport={p}\nuser={user}\ndbname=val3\nconnect_timeout=3\n\n"
            for name, (user, p) in services.items()))
        self.environ = {k: v for k, v in os.environ.items()
                        if not k.startswith(("VAL3_", "PG", "CLAUDE"))}
        self.environ.update(HOME=str(self.home), PGSERVICEFILE=str(service_file),
                            XDG_STATE_HOME=str(root / "state"), XDG_DATA_HOME=str(root / "data"))
        self.fake = root / "fake"
        self.fake.mkdir()
        self.state = root / "state" / "val3"
        self.environ.update(VAL3_EXTRACTOR=f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE_EXTRACTOR))}",
                            FAKE_EXTRACTOR_DIR=str(self.fake))

    def config(self, home=None, linked=(), service="val3test", embedding=None):
        linked_list = ", ".join(f'"{name}"' for name in linked)
        text = f'service = "{service}"\n[vaults]\nhome = "{home or self.vault}"\nlinked = [{linked_list}]\n'
        if embedding is not None:
            text += "[embedding]\n" + "".join(f"{key} = {json.dumps(value)}\n" for key, value in embedding.items())
        (self.project / "val3.toml").write_text(text)

    def run(self, *args, input=None, cwd=None, environ=None, tty=False):
        """`tty`: standard input is a terminal, as for a person at a shell (no `input` then)."""
        master, slave = pty.openpty() if tty else (None, None)
        try:
            return subprocess.run([sys.executable, "-m", "val3", *args], input=input, text=True,
                                  stdin=slave, capture_output=True, cwd=cwd or self.project,
                                  env={**self.environ, **(environ or {})}, timeout=60)
        finally:
            if tty:
                os.close(slave)
                os.close(master)

    def sql(self, query, params=(), user="postgres"):
        dsn = f"host=127.0.0.1 port={self.port} user={user} dbname=val3"
        with psycopg.connect(dsn, autocommit=True) as conn:
            cur = conn.execute(query, params)
            return cur.fetchall() if cur.description else []

    def transcript(self, tool_outputs=(), user_messages=(), assistant_texts=(), compact_summaries=(), tool_calls=(),
                   session=None, at=None):
        """Write a session transcript in the harness's JSONL shape and return its path.

        tool_outputs: outputs of `cat report.txt` Bash calls.
        tool_calls: (tool name, tool input, output) triples, written as an assistant tool_use
        followed by the user tool_result that answers it.
        """
        path = self.home / ".claude" / "projects" / "-project" / f"{session or self.session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [{"type": "user", "sessionId": self.session,
                  "message": {"role": "user", "content": text}} for text in user_messages]
        lines += [{"type": "assistant", "sessionId": self.session,
                   "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
                  for text in assistant_texts]
        for i, text in enumerate(tool_outputs):  # each one a complete Bash call, so it can be checked
            lines.append({"type": "assistant", "sessionId": self.session, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": "cat report.txt"}}]}})
            lines.append({"type": "user", "sessionId": self.session, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": text}]}})
        lines += [{"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
                   "sessionId": self.session, "message": {"role": "user", "content": text}}
                  for text in compact_summaries]
        for i, (name, tool_input, output) in enumerate(tool_calls):
            lines.append({"type": "assistant", "sessionId": self.session, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"call{i}", "name": name, "input": tool_input}]}})
            lines.append({"type": "user", "sessionId": self.session, "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"call{i}", "content": output}]}})
        if at is not None:
            lines = [{**line, "timestamp": at} for line in lines]
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return path

    def respond(self, *calls):
        """Prepare the fake extractor's answers: one list of {"note", "candidate"} items per call, or a dict
        written as that call's structured output unchanged."""
        for i, answer in enumerate(calls):
            (self.fake / f"response-{i}.json").write_text(json.dumps(
                {"type": "result", "is_error": False, "total_cost_usd": 0.001,
                 "structured_output": answer if isinstance(answer, dict) else {"candidates": answer}}))

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


@pytest.fixture
def env(tmp_path, pg_port):
    return Env(tmp_path, pg_port)


@pytest.fixture
def embedder():
    fake = FakeEmbedder()
    yield fake
    fake.close()
