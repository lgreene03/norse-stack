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
    /api/research[/*]  → research:8094/api/research[/*]  (GET only, with the
                         gateway's bearer token; run submission is disabled)
    /api/features      → mimir   :8095/api/features
    /api/features/history → mimir:8095/api/features/history
    /api/sources       → mimir   :8095/api/sources
    /api/tca           → forseti :8096/api/tca
    /api/tca/fills     → forseti :8096/api/tca/fills
    /api/impact[/schedule] → forseti :8096/api/impact[/schedule] (market impact)
    /api/capacity      → forseti :8096/api/capacity  (strategy capacity)
    /api/regime[/*]    → heimdall:8097/api/regime[/history|/model]  (HMM regime)
    /api/health/<svc>  → <svc>/healthz  (huginn|sleipnir|odin|muninn|redpanda-console|heimdall|forseti)

Backend hosts are 127.0.0.1 by default and overridable via env vars
(HUGINN_HOST, ODIN_HOST, SLEIPNIR_HOST, MUNINN_HOST, REDPANDA_CONSOLE_HOST,
RESEARCH_HOST, MIMIR_HOST, FORSETI_HOST, HEIMDALL_HOST).

A backend being down NEVER crashes the console: the proxy returns 502 with a
JSON {"error": ...} body and short timeouts so a hung backend can't wedge the
server. Error bodies never carry exception text or backend URLs; those are
logged here instead. Backend redirects are not followed and HTTP proxy
environment variables are ignored, so a request never leaves for another host.

The console binds 127.0.0.1 (CONSOLE_BIND overrides it) and refuses, with 403,
any request whose Host header is not localhost:PORT or 127.0.0.1:PORT (plus any
in CONSOLE_ALLOWED_HOSTS), which stops the LAN and DNS-rebinding pages (G0.2's
console clause, pulled forward by G1.2).

The research gateway needs a bearer token on every route but /healthz (G1.2).
The console reads it from the file whose path is in RESEARCH_TOKEN_FILE
(default: secrets/research_token at the repo root, the same untracked file
docker-compose.yml mounts into the gateway) and adds it to the requests it
proxies there, and only there. The token is never logged or returned. A file
that fails the gateway's rule (one trailing newline removed, then at least 32
printable, non-space ASCII characters) is not used at all. The gateway runs
nothing, so POST /api/research[/*] is refused here and never forwarded.
docs/RUNBOOK.md, section 1.1.

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

# Loopback only by default (G0.2). Anything else is an explicit opt-in.
BIND_HOST = os.environ.get("CONSOLE_BIND", "127.0.0.1")
# Extra Host header values to accept besides localhost:PORT and 127.0.0.1:PORT,
# comma-separated host:port. Empty by default.
EXTRA_ALLOWED_HOSTS = frozenset(
    h.strip().lower()
    for h in os.environ.get("CONSOLE_ALLOWED_HOSTS", "").split(",")
    if h.strip())

# Short proxy timeout: a hung backend must never wedge a console request.
PROXY_TIMEOUT_SECS = float(os.environ.get("PROXY_TIMEOUT_SECS", "3"))

# Backend hosts, overridable via env (default loopback). Ports are fixed by the
# Norse stack contract.
HUGINN_HOST = os.environ.get("HUGINN_HOST", "127.0.0.1")
ODIN_HOST = os.environ.get("ODIN_HOST", "127.0.0.1")
SLEIPNIR_HOST = os.environ.get("SLEIPNIR_HOST", "127.0.0.1")
MUNINN_HOST = os.environ.get("MUNINN_HOST", "127.0.0.1")
REDPANDA_CONSOLE_HOST = os.environ.get("REDPANDA_CONSOLE_HOST", "127.0.0.1")
# New services (in-compose: research/mimir/forseti; loopback default for dev).
RESEARCH_HOST = os.environ.get("RESEARCH_HOST", "127.0.0.1")
MIMIR_HOST = os.environ.get("MIMIR_HOST", "127.0.0.1")
FORSETI_HOST = os.environ.get("FORSETI_HOST", "127.0.0.1")
HEIMDALL_HOST = os.environ.get("HEIMDALL_HOST", "127.0.0.1")

# Research gateway bearer token: a file named by path, never an env value or a
# command-line argument (G0.4). Read on each proxied request, so creating the
# file needs no console restart.
RESEARCH_TOKEN_FILE = os.environ.get(
    "RESEARCH_TOKEN_FILE",
    os.path.join(os.path.dirname(DIRECTORY), "secrets", "research_token"))

RESEARCH_RUNS_DISABLED = (
    "run submission is disabled: H1 and H2 trials run only through "
    "research/norse_research's harness, which writes every run to the trial ledger")

HUGINN = (HUGINN_HOST, 8083)
ODIN = (ODIN_HOST, 8086)
RESEARCH = (RESEARCH_HOST, 8094)
MIMIR = (MIMIR_HOST, 8095)
FORSETI = (FORSETI_HOST, 8096)
HEIMDALL = (HEIMDALL_HOST, 8097)

# Health-check service map: svc name → (host, port). Matches the footer dots.
HEALTH_TARGETS = {
    "huginn": (HUGINN_HOST, 8083),
    "sleipnir": (SLEIPNIR_HOST, 8085),
    "odin": (ODIN_HOST, 8086),
    "muninn": (MUNINN_HOST, 8080),
    "redpanda-console": (REDPANDA_CONSOLE_HOST, 8088),
    "heimdall": (HEIMDALL_HOST, 8097),
    "forseti": (FORSETI_HOST, 8096),
}

# GET proxy routes: console path → (host, port, backend path).
GET_ROUTES = {
    "/api/snapshot": (HUGINN[0], HUGINN[1], "/api/snapshot"),
    "/api/metrics": (HUGINN[0], HUGINN[1], "/metrics"),
    "/api/alphas": (HUGINN[0], HUGINN[1], "/api/alphas"),
    "/api/validation": (HUGINN[0], HUGINN[1], "/api/validation"),
    "/api/portfolio": (ODIN[0], ODIN[1], "/api/portfolio"),
    "/api/equity": (ODIN[0], ODIN[1], "/api/equity"),
    # Feature store / Mimir (point-in-time, no lookahead).
    "/api/features": (MIMIR[0], MIMIR[1], "/api/features"),
    "/api/features/history": (MIMIR[0], MIMIR[1], "/api/features/history"),
    "/api/sources": (MIMIR[0], MIMIR[1], "/api/sources"),
    # Execution TCA / Forseti.
    "/api/tca": (FORSETI[0], FORSETI[1], "/api/tca"),
    "/api/tca/fills": (FORSETI[0], FORSETI[1], "/api/tca/fills"),
    # Market-impact & capacity / Forseti (square-root law + Almgren-Chriss).
    "/api/impact": (FORSETI[0], FORSETI[1], "/api/impact"),
    "/api/impact/schedule": (FORSETI[0], FORSETI[1], "/api/impact/schedule"),
    "/api/capacity": (FORSETI[0], FORSETI[1], "/api/capacity"),
    # Market-regime / Heimdall (Gaussian HMM, Baum-Welch, causal filtering).
    "/api/regime": (HEIMDALL[0], HEIMDALL[1], "/api/regime"),
    "/api/regime/history": (HEIMDALL[0], HEIMDALL[1], "/api/regime/history"),
    "/api/regime/model": (HEIMDALL[0], HEIMDALL[1], "/api/regime/model"),
}


MIN_TOKEN_LEN = 32


def load_research_token():
    """Read and check the research token. Returns (token, None) or (None, cause).

    The rule is huginn cmd/research's loadToken, byte for byte: remove exactly
    one trailing "\\n", then require at least MIN_TOKEN_LEN characters, each
    printable non-space ASCII (0x21 to 0x7E). So a two-line file left by `>>`,
    a "\\r\\n" ending or a short token is rejected, never sent. The cause never
    contains the file's content. Never raises.
    """
    try:
        with open(RESEARCH_TOKEN_FILE, "rb") as fh:
            raw = fh.read()
    except OSError as e:
        return None, "unreadable (%s)" % type(e).__name__
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if len(raw) < MIN_TOKEN_LEN:
        return None, "holds fewer than %d characters" % MIN_TOKEN_LEN
    if any(b < 0x21 or b > 0x7E for b in raw):
        return None, "holds whitespace, a control character or a non-ASCII byte"
    return raw.decode("ascii"), None


_last_token_problem = [None]


def research_auth_headers():
    """Return the gateway's Authorization header, or {} without a valid token.

    Without one the request goes bare and the gateway answers 401 (or 503 if
    it has no token either), which the research panel shows as not connected.
    A change in the token file's state is logged once, by cause, never value.
    """
    token, cause = load_research_token()
    if cause != _last_token_problem[0]:
        _last_token_problem[0] = cause
        if cause:
            print("  research token rejected: %s %s" % (RESEARCH_TOKEN_FILE, cause))
    return {"Authorization": "Bearer " + token} if token else {}


def token_status():
    """One startup line saying whether the token is usable; never the token."""
    _, cause = load_research_token()
    return "  research token: %s (%s)" % (
        RESEARCH_TOKEN_FILE,
        "present" if cause is None
        else "REJECTED, %s: the research panel will not connect" % cause)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every backend redirect, so a request (and any token on it) never
    follows a Location to another host (G1.2 review, F7)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Every proxied request goes through this opener: no redirects, and no proxy
# from http_proxy and the like, since every backend is on this host.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    # ---- helpers --------------------------------------------------------
    def _host_allowed(self):
        """True when the Host header names this console on loopback.

        A DNS-rebinding page reaches the console under the attacker's name, and
        a LAN client under the Mac's address; both carry a foreign Host.
        """
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        return host in {"localhost:%d" % port, "127.0.0.1:%d" % port} | EXTRA_ALLOWED_HOSTS

    def _refuse_foreign_host(self):
        """Send 403 and return True when the Host header is not allowed."""
        if self._host_allowed():
            return False
        self._send_json(403, {"error": "host not allowed"})
        return True

    def _send_json(self, status, obj, headers=None):
        # No-cache headers are injected by the end_headers() override below, so
        # every response (static or API) stays uncached during development.
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _proxy(self, method, host, port, backend_path, body=None, headers=None):
        """Forward a request to a backend and stream the response back.

        Never raises: a backend that is down/slow/garbage yields a 502 JSON
        body rather than a 500 or a wedged connection. Status code and the
        Content-Type are passed through on success so JSON stays JSON and
        Prometheus text stays text. A redirect is not followed: it becomes a
        502. The client never sees exception text or the backend URL.
        """
        url = "http://%s:%d%s" % (host, port, backend_path)
        req = urllib.request.Request(url, method=method, data=body)
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)
        try:
            with _OPENER.open(req, timeout=PROXY_TIMEOUT_SECS) as resp:
                payload = resp.read()
                ctype = resp.headers.get("Content-Type", "application/octet-stream")
                self.send_response(resp.status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                # A redirect, refused by _NoRedirect: never followed.
                print("  proxy: %s %s answered %d; redirect not followed" % (method, url, e.code))
                e.close()
                self._send_json(502, {"error": "backend redirected; not followed"})
                return
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
            # Log the cause here, with any Authorization value scrubbed, and
            # return nothing of it: exception text can quote request headers.
            detail = str(e)
            for v in (headers or {}).values():
                if v:
                    detail = detail.replace(v, "<redacted>")
            print("  proxy: %s %s failed: %s: %s" % (method, url, type(e).__name__, detail))
            self._send_json(502, {"error": "backend unreachable"})

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
        """Return True if the path was a proxied API route (and handled).

        The query string is forwarded verbatim so endpoints that key on it
        (e.g. Mimir's ?as_of=/?instrument=, ?limit=) work through the proxy.
        """
        parts = self.path.split("?", 1)
        path = parts[0]
        qs = ("?" + parts[1]) if len(parts) == 2 and parts[1] else ""
        if path in GET_ROUTES:
            host, port, backend = GET_ROUTES[path]
            self._proxy("GET", host, port, backend + qs)
            return True
        # Research gateway: exact /api/research plus any /api/research/<...>
        # subpath (e.g. /runs, /runs/{id}) → research:8094, query preserved,
        # with the gateway's bearer token added here (never from the browser).
        if path == "/api/research" or path.startswith("/api/research/"):
            self._proxy("GET", RESEARCH[0], RESEARCH[1], path + qs,
                        headers=research_auth_headers())
            return True
        if path.startswith("/api/health/"):
            svc = path[len("/api/health/"):].strip("/")
            self._health_proxy(svc)
            return True
        return False

    def do_GET(self):
        if self._refuse_foreign_host():
            return
        if self._try_proxy_get():
            return
        super().do_GET()

    def do_HEAD(self):
        if self._refuse_foreign_host():
            return
        if self._try_proxy_get():
            return
        super().do_HEAD()

    def do_POST(self):
        if self._refuse_foreign_host():
            return
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
        # Research run submission is disabled (G1.2): refused here rather than
        # forwarded, so the console never carries a POST to the gateway with
        # the token attached.
        if path == "/api/research" or path.startswith("/api/research/"):
            self._send_json(405, {"error": RESEARCH_RUNS_DISABLED},
                            headers={"Allow": "GET, HEAD"})
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


def create_server(port=None, bind=None):
    """The console's server, on BIND_HOST (127.0.0.1 unless CONSOLE_BIND)."""
    return Server((BIND_HOST if bind is None else bind,
                   PORT if port is None else port), Handler)


def main():
    with create_server() as httpd:
        url = "http://localhost:%d/" % PORT
        print("Norse Console serving %s" % DIRECTORY)
        print("  -> %s  (bound to %s)" % (url, BIND_HOST))
        print("  proxy: huginn=%s:8083  odin=%s:8086" % (HUGINN_HOST, ODIN_HOST))
        print("  proxy: research=%s:8094  mimir=%s:8095  forseti=%s:8096  heimdall=%s:8097"
              % (RESEARCH_HOST, MIMIR_HOST, FORSETI_HOST, HEIMDALL_HOST))
        # Say whether the research token is usable; never print the token.
        print(token_status())
        print("  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
