#!/usr/bin/env python3
"""Tiny static server + reverse proxy for the Norse Console.

Serves the console/ directory (this script's own directory) with no-cache
headers so edits show up on reload, AND reverse-proxies the live data API to
the backend services so the browser only ever talks to the console origin
(no CORS, no hardcoded service URLs in the page). Stdlib only.

Proxied paths (browser → console origin → backend):
    /api/snapshot      → huginn  :8083/api/snapshot
    /api/metrics       → huginn  :8083/metrics
    /api/alphas        → huginn  :8083/api/alphas
    /api/validation    → huginn  :8083/api/validation
    /api/portfolio     → odin    :8086/api/portfolio
    /api/equity        → odin    :8086/api/equity
    /api/breaker       → huginn  :8083/api/breaker/{trigger,reset}  (POST, HALT)
    /api/health/<svc>  → <svc>/healthz  (huginn|sleipnir|odin|muninn|redpanda-console)

Backend hosts are 127.0.0.1 by default and overridable via env vars
(HUGINN_HOST, ODIN_HOST, SLEIPNIR_HOST, MUNINN_HOST, REDPANDA_CONSOLE_HOST).

A backend being down NEVER crashes the console: the proxy returns 502 with a
JSON {"error": ...} body and short timeouts so a hung backend can't wedge the
server.

Usage:
    python3 serve.py            # serves on http://localhost:8090
    PORT=9000 python3 serve.py  # override the port
"""
import http.server
import json
import os
import socketserver
import urllib.error
import urllib.request

PORT = int(os.environ.get("PORT", "8090"))
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# Short proxy timeout: a hung backend must never wedge a console request.
PROXY_TIMEOUT_SECS = float(os.environ.get("PROXY_TIMEOUT_SECS", "3"))

# Backend hosts, overridable via env (default loopback). Ports are fixed by the
# Norse stack contract.
HUGINN_HOST = os.environ.get("HUGINN_HOST", "127.0.0.1")
ODIN_HOST = os.environ.get("ODIN_HOST", "127.0.0.1")
SLEIPNIR_HOST = os.environ.get("SLEIPNIR_HOST", "127.0.0.1")
MUNINN_HOST = os.environ.get("MUNINN_HOST", "127.0.0.1")
REDPANDA_CONSOLE_HOST = os.environ.get("REDPANDA_CONSOLE_HOST", "127.0.0.1")

HUGINN = (HUGINN_HOST, 8083)
ODIN = (ODIN_HOST, 8086)

# Health-check service map: svc name → (host, port). Matches the footer dots.
HEALTH_TARGETS = {
    "huginn": (HUGINN_HOST, 8083),
    "sleipnir": (SLEIPNIR_HOST, 8085),
    "odin": (ODIN_HOST, 8086),
    "muninn": (MUNINN_HOST, 8080),
    "redpanda-console": (REDPANDA_CONSOLE_HOST, 8088),
}

# GET proxy routes: console path → (host, port, backend path).
GET_ROUTES = {
    "/api/snapshot": (HUGINN[0], HUGINN[1], "/api/snapshot"),
    "/api/metrics": (HUGINN[0], HUGINN[1], "/metrics"),
    "/api/alphas": (HUGINN[0], HUGINN[1], "/api/alphas"),
    "/api/validation": (HUGINN[0], HUGINN[1], "/api/validation"),
    "/api/portfolio": (ODIN[0], ODIN[1], "/api/portfolio"),
    "/api/equity": (ODIN[0], ODIN[1], "/api/equity"),
}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    # ---- helpers --------------------------------------------------------
    def _send_json(self, status, obj):
        # No-cache headers are injected by the end_headers() override below, so
        # every response (static or API) stays uncached during development.
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _proxy(self, method, host, port, backend_path, body=None, headers=None):
        """Forward a request to a backend and stream the response back.

        Never raises: a backend that is down/slow/garbage yields a 502 JSON
        body rather than a 500 or a wedged connection. Status code and the
        Content-Type are passed through on success so JSON stays JSON and
        Prometheus text stays text.
        """
        url = "http://%s:%d%s" % (host, port, backend_path)
        req = urllib.request.Request(url, method=method, data=body)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=PROXY_TIMEOUT_SECS) as resp:
                payload = resp.read()
                ctype = resp.headers.get("Content-Type", "application/octet-stream")
                self.send_response(resp.status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
        except urllib.error.HTTPError as e:
            # Backend answered with a non-2xx: pass the status + body through so
            # the console can show the real upstream error (e.g. 503 locked).
            try:
                payload = e.read()
            except Exception:
                payload = b""
            ctype = "application/octet-stream"
            try:
                ctype = e.headers.get("Content-Type", ctype)
            except Exception:
                pass
            self.send_response(e.code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except Exception as e:  # connection refused, timeout, DNS, etc.
            self._send_json(502, {"error": "backend unreachable",
                                  "detail": str(e),
                                  "target": url})

    def _health_proxy(self, svc):
        """Probe a service's /healthz and report up/down as JSON.

        Returns 200 {"service","status":"up"} when the backend /healthz is
        reachable and 2xx; otherwise 200 {"status":"down"} (a down service is
        not a console error — it is a fact the dots render). redpanda-console
        has no /healthz, so we treat any HTTP response on its root as "up".
        """
        target = HEALTH_TARGETS.get(svc)
        if target is None:
            self._send_json(404, {"error": "unknown service", "service": svc})
            return
        host, port = target
        path = "/healthz"
        if svc == "redpanda-console":
            # No /healthz; reachability of the root is the liveness signal.
            path = "/"
        elif svc == "muninn":
            # Muninn is Spring Boot — liveness is /actuator/health, not /healthz
            # (which 404s and would falsely render the dot red).
            path = "/actuator/health"
        url = "http://%s:%d%s" % (host, port, path)
        try:
            with urllib.request.urlopen(url, timeout=PROXY_TIMEOUT_SECS) as resp:
                up = 200 <= resp.status < 400
                self._send_json(200, {"service": svc,
                                      "status": "up" if up else "down",
                                      "code": resp.status})
        except urllib.error.HTTPError as e:
            # An HTTP error still means the service is listening. For
            # redpanda-console a 404 on "/" is "up"; for the rest, a 503 from
            # /healthz (e.g. degraded readiness) is reported as "degraded".
            if svc == "redpanda-console":
                self._send_json(200, {"service": svc, "status": "up", "code": e.code})
            elif e.code == 503:
                self._send_json(200, {"service": svc, "status": "degraded", "code": e.code})
            else:
                self._send_json(200, {"service": svc, "status": "down", "code": e.code})
        except Exception:
            self._send_json(200, {"service": svc, "status": "down"})

    # ---- request dispatch ----------------------------------------------
    def _try_proxy_get(self):
        """Return True if the path was a proxied API route (and handled)."""
        path = self.path.split("?", 1)[0]
        if path in GET_ROUTES:
            host, port, backend = GET_ROUTES[path]
            self._proxy("GET", host, port, backend)
            return True
        if path.startswith("/api/health/"):
            svc = path[len("/api/health/"):].strip("/")
            self._health_proxy(svc)
            return True
        return False

    def do_GET(self):
        if self._try_proxy_get():
            return
        super().do_GET()

    def do_HEAD(self):
        if self._try_proxy_get():
            return
        super().do_HEAD()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        # HALT / RESUME → huginn breaker. Body {"halted": true|false} selects
        # the trigger (halt) vs reset (resume) endpoint. The Authorization
        # header is forwarded so huginn's token gate still applies.
        if path == "/api/breaker":
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            halted = True
            try:
                halted = bool(json.loads(raw.decode("utf-8")).get("halted", True))
            except Exception:
                halted = True
            backend = "/api/breaker/trigger" if halted else "/api/breaker/reset"
            headers = {}
            auth = self.headers.get("Authorization")
            if auth:
                headers["Authorization"] = auth
            headers["Content-Type"] = "application/json"
            self._proxy("POST", HUGINN[0], HUGINN[1], backend, body=b"", headers=headers)
            return
        self._send_json(404, {"error": "not found", "path": path})

    def end_headers(self):
        # Inject no-cache on every response (static files and API/proxy alike)
        # so dev reloads always re-fetch index.html / live.js and the SPA never
        # caches a stale data payload.
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        # Keep the console quiet but show request lines.
        print("  " + (fmt % args))


class Server(socketserver.ThreadingTCPServer):
    # Threaded so a slow backend proxy call can't block static file serving or
    # other concurrent API polls from the single-page console.
    allow_reuse_address = True
    daemon_threads = True


def main():
    with Server(("", PORT), Handler) as httpd:
        url = "http://localhost:%d/" % PORT
        print("Norse Console serving %s" % DIRECTORY)
        print("  -> %s" % url)
        print("  proxy: huginn=%s:8083  odin=%s:8086" % (HUGINN_HOST, ODIN_HOST))
        print("  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
