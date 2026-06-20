#!/usr/bin/env python3
"""
News Sentinel -- Crypto News Sentiment Analyzer.

Polls RSS feeds from CoinDesk and CoinTelegraph, classifies each headline
using a local Ollama LLM, and serves per-instrument aggregate sentiment
via a REST API.  Designed for the Norse Stack quantitative trading pipeline.

Endpoints:
  GET /api/sentiment  -- per-instrument aggregate sentiment (BTC, ETH, SOL, XRP, DOGE)
  GET /api/headlines   -- recent headlines with individual sentiment scores
  GET /api/status      -- service status, feed health, Ollama connectivity
  GET /healthz         -- simple health check

Named after the sentinel watchtower -- first to see what's coming.
"""

import hashlib
import json
import logging
import math
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

import feedparser

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))
PORT = int(os.environ.get("PORT", "8089"))

# CORS: default "*" preserves existing behaviour; lock to a single origin in
# hardened deployments via ACCESS_CONTROL_ALLOW_ORIGIN.
ACCESS_CONTROL_ALLOW_ORIGIN = os.environ.get("ACCESS_CONTROL_ALLOW_ORIGIN", "*")

# Liveness: the background poller stamps a heartbeat each cycle; /readyz (and
# /healthz) return 503 once it goes stale beyond this threshold. Default is
# generous relative to POLL_INTERVAL so a long Ollama batch doesn't flap it.
HEALTH_MAX_STALENESS_SECS = float(
    os.environ.get("HEALTH_MAX_STALENESS_SECS", str(max(POLL_INTERVAL * 3, 120)))
)

RSS_FEEDS = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
}

COINS = ["btc", "eth", "sol", "xrp", "doge"]

SENTIMENT_PROMPT = (
    'Classify this crypto news headline for BTC, ETH, SOL, XRP, and DOGE '
    'trading. Respond with ONLY a JSON object, no other text.\n\n'
    'Headline: "{headline}"\n\n'
    'Respond with: {{"btc": "bullish"|"bearish"|"neutral", '
    '"eth": "bullish"|"bearish"|"neutral", '
    '"sol": "bullish"|"bearish"|"neutral", '
    '"xrp": "bullish"|"bearish"|"neutral", '
    '"doge": "bullish"|"bearish"|"neutral", '
    '"btc_confidence": 0.0-1.0, "eth_confidence": 0.0-1.0, '
    '"sol_confidence": 0.0-1.0, "xrp_confidence": 0.0-1.0, '
    '"doge_confidence": 0.0-1.0}}'
)

HEADLINE_WINDOW_SECS = 2 * 3600          # keep last 2 hours
DECAY_HALF_LIFE_SECS = 30 * 60           # 30-minute half-life for weighting
OLLAMA_RATE_LIMIT_SECS = 3               # min gap between Ollama calls
OLLAMA_TIMEOUT_SECS = 30
MAX_HEADLINES_RESPONSE = 50

INSTRUMENTS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("news-sentinel")

shutdown = False


def handle_signal(signum, frame):
    global shutdown
    shutdown = True
    log.info("Shutdown signal received")


# ---------------------------------------------------------------------------
# Headline Store (thread-safe)
# ---------------------------------------------------------------------------

class HeadlineStore:
    """Thread-safe store for processed headlines with deduplication."""

    def __init__(self):
        self.lock = threading.Lock()
        self.headlines = []          # list of dicts, newest last
        self.seen_hashes = set()     # title hashes for dedup
        self.ollama_last_ok = None   # timestamp of last successful Ollama call
        self.ollama_failures = 0     # cumulative failed/unparsed Ollama calls
        self.ollama_ok_count = 0     # cumulative genuinely-parsed responses
        self.feeds_active = set()
        # Poller liveness heartbeat (monotonic seconds); None until first cycle.
        self.last_poll_monotonic = None

    def has_seen(self, title_hash):
        with self.lock:
            return title_hash in self.seen_hashes

    def add(self, headline):
        with self.lock:
            self.seen_hashes.add(headline["title_hash"])
            self.headlines.append(headline)

    def mark_ollama_ok(self):
        with self.lock:
            self.ollama_last_ok = datetime.now(timezone.utc).isoformat()
            self.ollama_ok_count += 1

    def mark_ollama_failure(self):
        with self.lock:
            self.ollama_failures += 1

    def beat_poll(self):
        with self.lock:
            self.last_poll_monotonic = time.monotonic()

    def liveness(self):
        """Return (ok, age_secs_or_None). ok=True before the first poll cycle."""
        with self.lock:
            if self.last_poll_monotonic is None:
                return True, None
            age = time.monotonic() - self.last_poll_monotonic
            return age <= HEALTH_MAX_STALENESS_SECS, age

    def mark_feed_active(self, name):
        with self.lock:
            self.feeds_active.add(name)

    def prune(self):
        """Remove headlines older than the retention window."""
        cutoff = time.time() - HEADLINE_WINDOW_SECS
        with self.lock:
            before = len(self.headlines)
            self.headlines = [
                h for h in self.headlines
                if h["processed_at_epoch"] > cutoff
            ]
            pruned = before - len(self.headlines)
            # Rebuild seen_hashes from surviving headlines
            self.seen_hashes = {h["title_hash"] for h in self.headlines}
        if pruned:
            log.info("Pruned %d expired headlines", pruned)

    def get_recent(self, limit=MAX_HEADLINES_RESPONSE):
        with self.lock:
            return list(reversed(self.headlines[-limit:]))

    def compute_sentiment(self):
        """Compute recency-weighted aggregate sentiment per instrument."""
        now = time.time()
        results = {}
        with self.lock:
            for instrument in INSTRUMENTS:
                key_prefix = instrument.split("-")[0].lower()  # "btc" or "eth"
                sentiment_key = f"{key_prefix}_sentiment"
                confidence_key = f"{key_prefix}_confidence"

                weighted_sum = 0.0
                weight_total = 0.0
                count = 0
                skipped_unclassified = 0
                latest_title = ""

                for h in self.headlines:
                    # Headlines whose Ollama classification failed are tagged
                    # unclassified; excluding them prevents a falsely-neutral
                    # signal (a failed call must not read as "neutral").
                    if not h.get("classified", True):
                        skipped_unclassified += 1
                        continue

                    age_secs = now - h["processed_at_epoch"]
                    weight = math.exp(-age_secs * math.log(2) / DECAY_HALF_LIFE_SECS)

                    direction = _direction_value(h.get(sentiment_key, "neutral"))
                    confidence = h.get(confidence_key, 0.5)

                    weighted_sum += weight * confidence * direction
                    weight_total += weight * confidence if confidence > 0 else weight
                    count += 1

                if weight_total > 0:
                    score = round(weighted_sum / weight_total, 4)
                else:
                    score = 0.0

                if self.headlines:
                    latest_title = self.headlines[-1].get("title", "")

                results[instrument] = {
                    "score": score,
                    "label": _score_label(score),
                    "headlines_count": count,
                    "unclassified_skipped": skipped_unclassified,
                    "latest_headline": latest_title,
                }

        return results

    def get_status(self):
        with self.lock:
            unclassified = sum(
                1 for h in self.headlines if not h.get("classified", True)
            )
            # Ollama is "degraded" if we've never had a parsed response, and
            # "ok" otherwise. This is surfaced separately from the service
            # status so a stale/failing Ollama is visible rather than masked by
            # falsely-neutral sentiment.
            ollama_ok = self.ollama_ok_count > 0
            return {
                "service": "news-sentinel",
                "status": "running",
                "headline_count": len(self.headlines),
                "unclassified_headlines": unclassified,
                "ollama_status": "ok" if ollama_ok else "degraded",
                "ollama_last_ok": self.ollama_last_ok,
                "ollama_ok_count": self.ollama_ok_count,
                "ollama_failures": self.ollama_failures,
                "ollama_model": OLLAMA_MODEL,
                "ollama_host": OLLAMA_HOST,
                "feeds_active": sorted(self.feeds_active),
                "poll_interval_secs": POLL_INTERVAL,
                "uptime_note": "headlines retained for 2h rolling window",
            }


def _direction_value(label):
    if label == "bullish":
        return 1.0
    if label == "bearish":
        return -1.0
    return 0.0


def _score_label(score):
    if score < -0.5:
        return "very_bearish"
    if score < -0.2:
        return "bearish"
    if score < -0.05:
        return "slightly_bearish"
    if score <= 0.05:
        return "neutral"
    if score <= 0.2:
        return "slightly_bullish"
    if score <= 0.5:
        return "bullish"
    return "very_bullish"


store = HeadlineStore()


# ---------------------------------------------------------------------------
# Ollama Client
# ---------------------------------------------------------------------------

def _neutral_sentiment():
    neutral = {}
    for c in COINS:
        neutral[c] = "neutral"
        neutral[f"{c}_confidence"] = 0.5
    return neutral


def query_ollama(headline):
    """Send a headline to Ollama for sentiment classification.

    Returns (result, ok). `ok` is True only when Ollama returned a genuinely
    parsed classification; on any transport error, non-JSON body, or unparseable
    response `ok` is False and `result` holds neutral defaults. The caller must
    not treat an ok=False result as a real neutral signal — it tags the headline
    unclassified so it is excluded from aggregate sentiment.
    """
    neutral = _neutral_sentiment()

    import re
    safe_headline = re.sub(r'[^a-zA-Z0-9\s.,;:!\?\'&$%#@\-\+\(\)/]', '', headline)[:200]
    prompt = SENTIMENT_PROMPT.format(headline=safe_headline)
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }).encode()

    url = f"{OLLAMA_HOST}/api/generate"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_SECS) as resp:
            body = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        log.warning("Ollama unavailable: %s", exc)
        return neutral, False
    except json.JSONDecodeError:
        log.warning("Ollama returned non-JSON response")
        return neutral, False

    raw = body.get("response", "")
    return _parse_ollama_response(raw, neutral)


def _parse_ollama_response(raw, neutral):
    """Extract JSON from Ollama's text response, tolerating markdown fences.

    Returns (result, ok). `ok` is False (and result is the neutral default)
    when no JSON object is present or it is malformed."""
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # Try to find JSON object boundaries
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        log.warning("No JSON object found in Ollama response: %.120s", raw)
        return neutral, False

    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        log.warning("Malformed JSON in Ollama response: %.120s", raw)
        return neutral, False

    # Validate and clamp values
    result = {}
    for coin in COINS:
        val = str(parsed.get(coin, "neutral")).lower().strip()
        if val not in ("bullish", "bearish", "neutral"):
            val = "neutral"
        result[coin] = val

    for coin in COINS:
        key = f"{coin}_confidence"
        try:
            val = float(parsed.get(key, 0.5))
            val = max(0.0, min(1.0, val))
        except (ValueError, TypeError):
            val = 0.5
        result[key] = round(val, 3)

    return result, True


# ---------------------------------------------------------------------------
# RSS Poller
# ---------------------------------------------------------------------------

def poll_feeds():
    """Fetch all RSS feeds and return list of (title, source, published) tuples."""
    entries = []
    for name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                log.warning("Feed %s returned error: %s", name, feed.bozo_exception)
                continue
            store.mark_feed_active(name)
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                published = entry.get("published", "")
                entries.append((title, name, published))
        except Exception as exc:
            log.error("Failed to fetch feed %s: %s", name, exc)
    return entries


def title_hash(title):
    return hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]


def background_poller():
    """Background thread: poll feeds, classify new headlines via Ollama."""
    log.info("Background poller started (interval=%ds)", POLL_INTERVAL)
    last_ollama_call = 0.0

    while not shutdown:
        try:
            entries = poll_feeds()
            new_count = 0

            for title, source, published in entries:
                if shutdown:
                    break

                t_hash = title_hash(title)
                if store.has_seen(t_hash):
                    continue

                # Rate-limit Ollama calls
                elapsed = time.time() - last_ollama_call
                if elapsed < OLLAMA_RATE_LIMIT_SECS:
                    time.sleep(OLLAMA_RATE_LIMIT_SECS - elapsed)

                sentiment, ollama_ok = query_ollama(title)
                last_ollama_call = time.time()

                # Only mark OK on a genuinely parsed response; otherwise record
                # a failure and tag the headline unclassified so its neutral
                # default is excluded from aggregate sentiment.
                if ollama_ok:
                    store.mark_ollama_ok()
                else:
                    store.mark_ollama_failure()

                now = datetime.now(timezone.utc)
                headline = {
                    "title": title,
                    "source": source,
                    "published": published,
                    "title_hash": t_hash,
                    "processed_at": now.isoformat(),
                    "processed_at_epoch": now.timestamp(),
                    "classified": ollama_ok,
                }
                for coin in COINS:
                    headline[f"{coin}_sentiment"] = sentiment[coin]
                    headline[f"{coin}_confidence"] = sentiment[f"{coin}_confidence"]
                store.add(headline)
                new_count += 1

                tags = " ".join(
                    f"{c.upper()}={sentiment[c]}({sentiment[f'{c}_confidence']:.2f})"
                    for c in COINS
                    if sentiment[c] != "neutral"
                )
                if not tags:
                    tags = "all-neutral"
                log.info("Headline [%s] %s: %.80s", source, tags, title)

            if new_count:
                log.info("Processed %d new headlines", new_count)

            # Prune old headlines
            store.prune()

            # Heartbeat: a completed cycle marks the poller alive for /readyz.
            store.beat_poll()

        except Exception as exc:
            log.error("Poller cycle error: %s", exc)

        # Sleep in small increments so shutdown is responsive
        for _ in range(POLL_INTERVAL):
            if shutdown:
                break
            time.sleep(1)

    log.info("Background poller stopped")


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

class SentinelHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/sentiment":
            self._json_response(store.compute_sentiment())
        elif path == "/api/headlines":
            self._json_response(store.get_recent())
        elif path == "/api/status":
            self._json_response(store.get_status())
        elif path == "/healthz":
            # Process-liveness: the HTTP server is up. Kept as a plain 200 so
            # the container healthcheck doesn't fail during the first (possibly
            # slow) poll cycle. Consumer-thread liveness is on /readyz.
            self._json_response({"status": "ok", "service": "news-sentinel"})
        elif path == "/readyz":
            # Readiness gates on the background poller's last-progress beat so a
            # wedged poller thread is detectable even while the server stays up.
            ok, age = store.liveness()
            self._json_response(
                {
                    "status": "ok" if ok else "degraded",
                    "service": "news-sentinel",
                    "poller_alive": ok,
                    "poller_last_beat_age_secs": (
                        round(age, 1) if age is not None else None
                    ),
                },
                status=200 if ok else 503,
            )
        else:
            self.send_error(404)

    def _json_response(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", ACCESS_CONTROL_ALLOW_ORIGIN)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress per-request access logs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("=" * 60)
    log.info("  NEWS SENTINEL -- Crypto News Sentiment Analyzer")
    log.info("=" * 60)
    log.info("  Ollama host:   %s", OLLAMA_HOST)
    log.info("  Ollama model:  %s", OLLAMA_MODEL)
    log.info("  Poll interval: %ds", POLL_INTERVAL)
    log.info("  API port:      %d", PORT)
    log.info("  Feeds:         %s", ", ".join(RSS_FEEDS.keys()))
    log.info("  Endpoints:     /api/sentiment, /api/headlines, /api/status")
    log.info("=" * 60)

    # Start background poller
    poller_thread = threading.Thread(target=background_poller, daemon=True)
    poller_thread.start()

    # Start HTTP server
    server = HTTPServer(("0.0.0.0", PORT), SentinelHandler)
    server.timeout = 1
    log.info("News Sentinel HTTP server listening on :%d", PORT)

    while not shutdown:
        server.handle_request()

    server.server_close()
    log.info("News Sentinel shutdown complete")


if __name__ == "__main__":
    main()
