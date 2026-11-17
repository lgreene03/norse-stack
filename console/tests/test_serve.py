"""Security behaviour of console/serve.py (G1.2, and G0.2's console clause).

Stdlib only; run with `python -m unittest discover -s console/tests -v` (CI's
validate job does). The console and every backend run on ephemeral loopback
ports, the token is fake, and nothing reaches a real service.
"""
import http.client
import http.server
import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

SERVE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "serve.py")
FAKE_TOKEN = "FAKE0123456789abcdef0123456789abcdef0123456789abcdef0123456789ab"

_TMP = tempfile.mkdtemp()
_TOKEN_FILE = os.path.join(_TMP, "research_token")


def _load_serve(extra_env=None):
    """Import serve.py fresh, with a fake token file and no bind or host overrides."""
    with open(_TOKEN_FILE, "w") as fh:
        fh.write(FAKE_TOKEN + "\n")
    env = {"RESEARCH_TOKEN_FILE": _TOKEN_FILE}
    env.update(extra_env or {})
    with mock.patch.dict(os.environ, env):
        for var in ("CONSOLE_BIND", "CONSOLE_ALLOWED_HOSTS"):
            os.environ.pop(var, None)
        spec = importlib.util.spec_from_file_location("serve_under_test", SERVE_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    module.Handler.log_message = lambda *a: None
    return module


serve = _load_serve()


class _Backend:
    """A fake backend that records every request it receives."""

    def __init__(self, redirect_to=None):
        self.seen = []
        seen = self.seen

        class H(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                seen.append((self.command, self.path, self.headers.get("Authorization")))
                if redirect_to:
                    self.send_response(302)
                    self.send_header("Location", redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps([{"id": "run-1", "status": "done", "strategy": "obi"}]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _answer
            do_POST = _answer

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ConsoleTest(unittest.TestCase):
    def setUp(self):
        with open(_TOKEN_FILE, "w") as fh:
            fh.write(FAKE_TOKEN + "\n")
        serve._last_token_problem[0] = None
        self.gateway = _Backend()
        self.huginn = _Backend()
        self.patches = [
            mock.patch.object(serve, "RESEARCH", ("127.0.0.1", self.gateway.port)),
            mock.patch.object(serve, "HUGINN", ("127.0.0.1", self.huginn.port)),
            mock.patch.dict(serve.GET_ROUTES, {
                "/api/snapshot": ("127.0.0.1", self.huginn.port, "/api/snapshot")}),
        ]
        for p in self.patches:
            p.start()
        self.console = serve.create_server(port=0)
        self.port = self.console.server_address[1]
        threading.Thread(target=self.console.serve_forever, daemon=True).start()

    def tearDown(self):
        self.console.shutdown()
        self.console.server_close()
        for p in self.patches:
            p.stop()
        self.gateway.close()
        self.huginn.close()

    def request(self, method, path, host="default", headers=None, body=None):
        """Send one request to the console with full control of the Host header."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host == "default":
            host = "localhost:%d" % self.port
        if host is not None:
            conn.putheader("Host", host)
        for k, v in (headers or {}).items():
            conn.putheader(k, v)
        if body is not None:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, dict(resp.getheaders()), data

    # ---- F1: loopback bind and Host allowlist ---------------------------
    def test_binds_loopback_by_default(self):
        self.assertEqual(serve.BIND_HOST, "127.0.0.1")
        self.assertEqual(self.console.server_address[0], "127.0.0.1")

    def test_foreign_host_is_refused_before_anything_runs(self):
        foreign = ["rebind.attacker.example:%d" % self.port, "rebind.attacker.example",
                   "192.168.1.20:%d" % self.port, "localhost:1", "127.0.0.1:%d.evil.example" % self.port,
                   "", None]
        requests = [("GET", "/"), ("GET", "/index.html"), ("GET", "/api/research/runs"),
                    ("GET", "/api/research/runs/run-1"), ("HEAD", "/api/research/runs"),
                    ("GET", "/api/snapshot"), ("GET", "/api/health/huginn"),
                    ("POST", "/api/research/runs"), ("POST", "/api/breaker")]
        for host in foreign:
            for method, path in requests:
                with self.subTest(host=host, method=method, path=path):
                    status, _, data = self.request(method, path, host=host,
                                                   body=b"{}" if method == "POST" else None)
                    self.assertEqual(status, 403)
                    self.assertNotIn(b"run-1", data)
        self.assertEqual(self.gateway.seen, [])
        self.assertEqual(self.huginn.seen, [])

    def test_loopback_hosts_are_served(self):
        for host in ("localhost:%d" % self.port, "127.0.0.1:%d" % self.port, "LOCALHOST:%d" % self.port):
            with self.subTest(host=host):
                status, _, data = self.request("GET", "/api/research/runs", host=host)
                self.assertEqual(status, 200)
                self.assertIn(b"run-1", data)

    def test_extra_allowed_hosts_are_opt_in(self):
        status, _, _ = self.request("GET", "/api/research/runs", host="console.internal:9")
        self.assertEqual(status, 403)
        with mock.patch.object(serve, "EXTRA_ALLOWED_HOSTS", frozenset({"console.internal:9"})):
            status, _, _ = self.request("GET", "/api/research/runs", host="console.internal:9")
        self.assertEqual(status, 200)

    # ---- the token goes to the gateway and nowhere else -----------------
    def test_token_is_attached_to_research_reads_only(self):
        self.request("GET", "/api/research/runs/run-1?x=1",
                     headers={"Authorization": "Bearer from-the-browser"})
        self.request("GET", "/api/snapshot")
        self.assertEqual(self.gateway.seen, [("GET", "/api/research/runs/run-1?x=1", "Bearer " + FAKE_TOKEN)])
        self.assertEqual(self.huginn.seen, [("GET", "/api/snapshot", None)])

    def test_research_post_is_refused_and_never_forwarded(self):
        for path in ("/api/research/runs", "/api/research", "/api/research/runs?x=1"):
            for ctype in ("application/json", "text/plain"):
                with self.subTest(path=path, ctype=ctype):
                    status, headers, data = self.request(
                        "POST", path, headers={"Content-Type": ctype}, body=b'{"strategy":"obi"}')
                    self.assertEqual(status, 405)
                    self.assertEqual(headers.get("Allow"), "GET, HEAD")
                    err = json.loads(data)["error"]
                    self.assertIn("disabled", err)
                    self.assertIn("ledger", err)
        self.assertEqual(self.gateway.seen, [])

    def test_breaker_post_still_forwards_its_own_token(self):
        self.request("POST", "/api/breaker", headers={"Authorization": "Bearer huginn-tok"},
                     body=b'{"halted": true}')
        self.assertEqual(self.huginn.seen, [("POST", "/api/breaker/trigger", "Bearer huginn-tok")])
        self.assertEqual(self.gateway.seen, [])

    # ---- F3: a bad token file is never sent and never echoed ------------
    def test_malformed_token_file_is_never_sent_or_echoed(self):
        bad = {
            "two lines from >>": FAKE_TOKEN + "\n" + FAKE_TOKEN + "\n",
            "CRLF ending": FAKE_TOKEN + "\r\n",
            "two trailing newlines": FAKE_TOKEN + "\n\n",
            "leading space": " " + FAKE_TOKEN,
            "control character": FAKE_TOKEN + "\x1f",
            "short": FAKE_TOKEN[:31] + "\n",
            "empty": "",
        }
        for name, content in bad.items():
            with self.subTest(name=name):
                with open(_TOKEN_FILE, "w") as fh:
                    fh.write(content)
                self.gateway.seen.clear()
                token, cause = serve.load_research_token()
                self.assertIsNone(token)
                self.assertNotIn(FAKE_TOKEN[:16], cause)
                self.assertNotIn(FAKE_TOKEN[:16], serve.token_status())
                status, _, data = self.request("GET", "/api/research/runs")
                self.assertNotIn(FAKE_TOKEN[:16].encode(), data)
                self.assertEqual(self.gateway.seen[-1][2], None, "no Authorization sent")

    def test_valid_token_rule_matches_the_gateway(self):
        for content in (FAKE_TOKEN + "\n", FAKE_TOKEN, FAKE_TOKEN[:32] + "\n"):
            with self.subTest(content=repr(content[-3:])):
                with open(_TOKEN_FILE, "w") as fh:
                    fh.write(content)
                token, cause = serve.load_research_token()
                expected = content[:-1] if content.endswith("\n") else content
                self.assertEqual(token, expected)
                self.assertIsNone(cause)
        self.assertNotIn(FAKE_TOKEN[:16], serve.token_status())

    def test_missing_token_file_sends_no_authorization(self):
        os.remove(_TOKEN_FILE)
        self.request("GET", "/api/research/runs")
        self.assertEqual(self.gateway.seen[-1][2], None)
        self.assertIn("REJECTED", serve.token_status())

    def test_unreachable_backend_error_carries_no_detail(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dead_port = s.getsockname()[1]
        with mock.patch.object(serve, "RESEARCH", ("127.0.0.1", dead_port)):
            status, _, data = self.request("GET", "/api/research/runs")
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(data), {"error": "backend unreachable"})
        self.assertNotIn(FAKE_TOKEN[:16].encode(), data)

    # ---- F7: a redirect is not followed ---------------------------------
    def test_gateway_redirect_is_not_followed(self):
        elsewhere = _Backend()
        redirecting = _Backend(redirect_to="http://127.0.0.1:%d/stolen" % elsewhere.port)
        try:
            with mock.patch.object(serve, "RESEARCH", ("127.0.0.1", redirecting.port)):
                status, _, data = self.request("GET", "/api/research/runs")
            self.assertEqual(status, 502)
            self.assertNotIn(FAKE_TOKEN[:16].encode(), data)
            self.assertEqual(len(redirecting.seen), 1)
            self.assertEqual(elsewhere.seen, [], "the redirect target must never be contacted")
        finally:
            redirecting.close()
            elsewhere.close()

    def test_proxy_opener_ignores_proxy_environment_and_redirects(self):
        # Import a fresh copy with http_proxy set: its opener, built at import,
        # must still carry no proxy. (urllib caches its default opener, so a
        # request-level test would depend on test order.)
        fresh = _load_serve({"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1"})
        for h in fresh._OPENER.handlers:
            if isinstance(h, urllib.request.ProxyHandler):
                self.assertEqual(h.proxies, {}, "the console's opener must not use a proxy")
        redirect_handlers = [h for h in fresh._OPENER.handlers
                             if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertTrue(redirect_handlers)
        for h in redirect_handlers:
            self.assertIsInstance(h, fresh._NoRedirect)


if __name__ == "__main__":
    unittest.main()
