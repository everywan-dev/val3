"""A running `val3 view` for tests: started with --no-open --port 0, driven over HTTP, always stopped."""
import http.client
import json
import os
import select
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from urllib.parse import urlsplit


class Viewer:
    def __init__(self, proc, url):
        self.proc = proc
        self.url = url
        parts = urlsplit(url)
        self.port = parts.port
        self.token = parts.path.strip("/")
        self.host = f"127.0.0.1:{self.port}"

    def request(self, method, path, headers=None, timeout=15):
        """(status, headers, body bytes) of one request; `path` is relative to /<token>/ unless absolute."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            full = path if path.startswith("/") else f"/{self.token}/{path}"
            conn.request(method, full, headers={"Host": self.host, **(headers or {})})
            response = conn.getresponse()
            return response.status, response.headers, response.read()
        finally:
            conn.close()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def json(self, path, expect=200):
        status, headers, body = self.get(path)
        assert status == expect, (status, body[:500])
        assert headers["Content-Type"] == "application/json; charset=utf-8"
        return json.loads(body)

    def raw(self, data: bytes, timeout=15) -> bytes:
        """Send bytes on a fresh connection and read until it closes."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as sock:
            sock.sendall(data)
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
            return b"".join(chunks)

    def stop(self, sig=signal.SIGTERM, timeout=10):
        if self.proc.poll() is None:
            self.proc.send_signal(sig)
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise AssertionError("val3 view did not stop")
        return self.proc.returncode, self.proc.stderr.read()


def start(env, *args, cwd=None, browser=False):
    """The process and its first stdout line (or None when it exited first)."""
    proc = subprocess.Popen([sys.executable, "-m", "val3", "view", *([] if browser else ["--no-open"]), *args],
                            cwd=cwd or env.project, env=env.environ, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 20
    fd = proc.stdout.fileno()
    line = b""
    while not line.endswith(b"\n") and time.monotonic() < deadline:
        if select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
            chunk = os.read(fd, 1)
            if not chunk:
                break
            line += chunk
    return proc, line.decode().strip() or None


@contextmanager
def viewing(env, *args, cwd=None, browser=False):
    proc, line = start(env, "--port", "0", *args, cwd=cwd, browser=browser)
    try:
        assert line and line.startswith("http://127.0.0.1:"), (line, proc.poll(),
                                                               proc.stderr.read() if proc.poll() is not None else "")
        yield Viewer(proc, line)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for pipe in (proc.stdout, proc.stderr):
            pipe.close()
