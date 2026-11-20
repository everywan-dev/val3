"""`val3 view`: the local server's refusals, headers, timeouts and shutdown, driven over HTTP."""
import signal
import socket
import time

from helpers import make_vault
from viewer import start, viewing

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'none'")


def assert_secured(headers):
    assert headers["Content-Security-Policy"] == CSP
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cache-Control"] == "no-store"
    assert "Access-Control-Allow-Origin" not in headers
    assert "Set-Cookie" not in headers


def test_serves_the_page_and_its_two_resources(env):
    make_vault(env)
    with viewing(env) as view:
        assert view.url == f"http://127.0.0.1:{view.port}/{view.token}/"
        assert len(view.token) == 64 and int(view.token, 16) >= 0
        status, headers, body = view.get("")
        assert status == 200 and headers["Content-Type"] == "text/html; charset=utf-8"
        assert_secured(headers)
        assert b"<title>VAL3 vault view</title>" in body
        assert b"<script" in body and b'src="view.js"' in body and b'href="view.css"' in body
        for name, kind in (("view.js", "text/javascript; charset=utf-8"), ("view.css", "text/css; charset=utf-8")):
            status, headers, body = view.get(name)
            assert status == 200 and headers["Content-Type"] == kind and body
            assert_secured(headers)
        for other in ("index.html", "../view.js", "view.js/", "vault.sql", "api", "api/", "api/nothing", "x"):
            status, headers, _ = view.get(other)
            assert status == 404, other
            assert_secured(headers)


def test_the_token_is_the_first_path_segment(env):
    make_vault(env)
    with viewing(env) as view:
        for path in ("/", f"/{view.token}", f"/{view.token.upper()}/", f"/{view.token[:-1]}/",
                     f"/{view.token}0/", f"/x/{view.token}/", "/favicon.ico", f"/%2F{view.token}/"):
            status, headers, body = view.get(path)
            assert status == 404, path
            assert "Location" not in headers
            assert view.token.encode() not in body
        status, _, _ = view.get(f"/{view.token}/?x=1")
        assert status == 200
        code, stderr = view.stop()
    assert view.token not in stderr  # the access log prints no path


def test_only_its_own_host_and_origin(env):
    make_vault(env)
    with viewing(env) as view:
        assert view.get("", headers={"Host": f"localhost:{view.port}"})[0] == 200
        for host in ("evil.example", f"evil.example:{view.port}", "127.0.0.1", f"127.0.0.1:{view.port + 1}",
                     f"127.0.0.1:{view.port}.evil", f"0.0.0.0:{view.port}", f"[::1]:{view.port}", ""):
            status, headers, _ = view.get("", headers={"Host": host})
            assert status == 403, host
            assert_secured(headers)
        no_host = view.raw(f"GET /{view.token}/ HTTP/1.0\r\n\r\n".encode())
        assert no_host.startswith(b"HTTP/1.0 403"), no_host[:80]
        two = view.raw(f"GET /{view.token}/ HTTP/1.1\r\nHost: {view.host}\r\nHost: {view.host}\r\n\r\n".encode())
        assert two.startswith(b"HTTP/1.0 403"), two[:80]
        assert view.get("", headers={"Origin": f"http://{view.host}"})[0] == 200
        for origin in ("http://evil.example", "null", f"https://{view.host}", f"http://localhost:{view.port}"):
            assert view.get("", headers={"Origin": origin})[0] == 403, origin


def test_without_the_token_every_request_is_not_found(env):
    make_vault(env)
    with viewing(env) as view:
        for method, headers in (("POST", {}), ("DELETE", {}), ("GET", {"Host": "evil.example"}),
                                ("GET", {"Origin": "http://evil.example"})):
            assert view.request(method, f"/{'0' * 64}/api/summary", headers=headers)[0] == 404, (method, headers)


def test_a_browser_that_stays_open_does_not_hold_the_server(env):
    make_vault(env)
    opened = env.root / "opened"
    browser = env.root / "browser"
    browser.write_text(f"#!/bin/sh\necho \"$1\" > {opened}\nsleep 5\n")
    browser.chmod(0o755)
    env.environ["BROWSER"] = str(browser)
    with viewing(env, browser=True) as view:
        started = time.monotonic()
        assert view.get("", timeout=4)[0] == 200
        assert time.monotonic() - started < 3
        env.wait(opened.exists, timeout=5)
        assert opened.read_text().strip() == view.url


def test_get_and_head_only(env):
    make_vault(env)
    with viewing(env) as view:
        status, headers, body = view.request("HEAD", "")
        assert status == 200 and body == b"" and int(headers["Content-Length"]) > 0
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "CONNECT", "PROPFIND"):
            status, headers, _ = view.request(method, "api/summary")
            assert status == 405, method
            assert_secured(headers)


def test_refusals_come_before_any_database_connection(env):
    """A database that cannot be reached: refusals and HEAD answer at once, without trying it."""
    env.config(service="val3down")
    with viewing(env) as view:
        started = time.monotonic()
        assert view.get(f"/{'0' * 64}/api/summary")[0] == 404
        assert view.get("api/summary", headers={"Host": "evil.example"})[0] == 403
        assert view.request("POST", "api/summary")[0] == 405
        status, headers, body = view.request("HEAD", "api/summary")
        assert status == 200 and body == b"" and headers["Content-Type"] == "application/json; charset=utf-8"
        assert time.monotonic() - started < 2
        status, headers, body = view.get("api/summary")
        assert status == 503 and b"cannot connect" in body
        assert_secured(headers)


def test_an_unfinished_request_does_not_block_another(env):
    make_vault(env)
    with viewing(env) as view:
        held = socket.create_connection(("127.0.0.1", view.port))
        try:
            held.sendall(f"GET /{view.token}/ HTTP/1.1\r\nHost: {view.host}\r\n".encode())  # no blank line
            started = time.monotonic()
            assert view.get("", timeout=5)[0] == 200
            assert time.monotonic() - started < 2
            held.settimeout(20)
            assert held.recv(100) == b""  # closed by the read timeout, with no answer
            assert time.monotonic() - started < 15
        finally:
            held.close()
        assert view.get("")[0] == 200


def test_many_idle_connections_do_not_stop_the_server(env):
    make_vault(env)
    with viewing(env) as view:
        idle = [socket.create_connection(("127.0.0.1", view.port)) for _ in range(12)]
        try:
            time.sleep(0.3)
            refused = 0
            for sock in idle[8:]:
                sock.settimeout(2)
                try:
                    refused += sock.recv(100).startswith(b"HTTP/1.0 503")
                except TimeoutError:
                    pass
            assert refused == 4  # 8 worker threads at most; the rest are answered "busy" at once
        finally:
            for sock in idle:
                sock.close()
        time.sleep(0.3)
        assert view.get("")[0] == 200


def test_stops_cleanly_on_sigterm_and_sigint(env):
    make_vault(env)
    for sig in (signal.SIGTERM, signal.SIGINT):
        with viewing(env) as view:
            assert view.get("")[0] == 200
            code, stderr = view.stop(sig)
            assert code == 0, stderr
            assert "Traceback" not in stderr and "stopped" in stderr
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", view.port)) != 0


def test_a_busy_port_is_an_error(env):
    make_vault(env)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        proc, line = start(env, "--port", str(port))
        proc.wait(20)
        assert proc.returncode == 1 and line is None
        assert f"port {port} is in use" in proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()


def test_usage_errors(env):
    make_vault(env)
    for port in ("-1", "65536", "x"):
        result = env.run("view", "--no-open", "--port", port)
        assert result.returncode == 2, port
    empty = env.root / "empty"
    empty.mkdir()
    result = env.run("view", "--no-open", "--port", "0", cwd=empty)
    assert result.returncode == 1 and "no val3.toml" in result.stderr
