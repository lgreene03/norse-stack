#!/usr/bin/env python3
"""
Mimir — Point-in-Time (no-lookahead) Feature Store.

Consumes feature events from Kafka (features.obi.v1) and persists every record
into a file-backed SQLite store, recording BOTH:

  - event_time  — the feature payload's own timestamp (when the feature is
    *about*, stamped upstream by obi-bridge as ``eventTime``), and
  - ingest_time — the wall-clock instant Mimir physically received the record.

Storing both is the whole point. A query ``as_of=T`` returns, per instrument,
the most recent feature with ``event_time <= T AND ingest_time <= T``. The
ingest_time guard is what prevents look-ahead: a feature whose event_time is
<= T but that physically ARRIVED after T (late-arriving or revised data) must
NOT be visible at T, because a system running *at* time T could not have known
it yet. This makes backtests joined against Mimir leakage-free by construction.

No API key required. Runs as a Docker service alongside the Norse Stack.
"""

import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from kafka import KafkaConsumer
from kafka.errors import KafkaConnectionError

KAFKA_BROKERS = os.environ.get("KAFKA_BROKERS", "redpanda:29092")
FEATURES_TOPIC = os.environ.get("FEATURES_TOPIC", "features.obi.v1")
PORT = int(os.environ.get("MIMIR_PORT", "8095"))
DB_PATH = os.environ.get("MIMIR_DB", "data/mimir.db")

# CORS: default to "*" to preserve existing behaviour (matches odin), but allow
# locking the allowed origin down to a single configured value in hardened
# deployments.
ACCESS_CONTROL_ALLOW_ORIGIN = os.environ.get("ACCESS_CONTROL_ALLOW_ORIGIN", "*")

# Liveness: the consumer thread stamps a heartbeat each poll cycle. /healthz
# returns 503 once the heartbeat is older than this many seconds, so a wedged
# consumer thread is detectable even while the HTTP server stays up. Generous
# default avoids flapping on an idle (but healthy) stream.
HEALTH_MAX_STALENESS_SECS = float(os.environ.get("HEALTH_MAX_STALENESS_SECS", "120"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("mimir")

shutdown = False


def handle_signal(signum, frame):
    global shutdown
    shutdown = True
    log.info("Shutdown signal received")


def now_iso():
    """Wall-clock now as an ISO-8601 UTC string (Z-suffixed)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_iso(ts):
    """Normalize an ISO-8601 timestamp to a canonical, lexicographically
    comparable UTC form. Returns the normalized string, or None on failure.

    SQLite compares the stored event_time / ingest_time / as_of as TEXT, so the
    point-in-time guard is only correct if every timestamp is in the same
    canonical shape. We parse (accepting a trailing ``Z``) and re-emit as
    ``YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00`` in UTC. A value that fails to parse
    is returned as-is upstream callers must guard, but we still try hard here so
    mixed ``Z`` vs ``+00:00`` inputs sort identically.
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


class Liveness:
    """Tracks the consumer thread's last-progress timestamp.

    The consumer stamps `beat()` once per poll cycle (whether or not a message
    arrived) so /healthz can distinguish a live-but-idle loop from a wedged
    one. `started` flips true after the consumer connects; until then /healthz
    reports healthy so container startup isn't failed closed during Kafka
    connect/retry. (Mirrors odin's Liveness.)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_beat = None
        self._started = False

    def mark_started(self):
        with self._lock:
            self._started = True
            self._last_beat = time.monotonic()

    def beat(self):
        with self._lock:
            self._last_beat = time.monotonic()

    def status(self):
        """Return (ok, age_secs_or_None). ok=True before the loop has started."""
        with self._lock:
            if not self._started or self._last_beat is None:
                return True, None
            age = time.monotonic() - self._last_beat
            return age <= HEALTH_MAX_STALENESS_SECS, age


liveness = Liveness()


class FeatureStore:
    """File-backed SQLite point-in-time feature store.

    Each row records (instrument, event_time, ingest_time, feature_json). The
    lock guards SQLite access: a single shared connection is used across the
    consumer thread and the HTTP handler threads, and sqlite3 connections are
    not safe for concurrent use without serialization, so every read/write goes
    through ``self.lock`` (the lock-guarded tracker pattern from odin).
    """

    def __init__(self, db_path=DB_PATH):
        self.lock = threading.Lock()
        self.db_path = db_path
        # Ensure the parent directory exists for a file-backed DB (skip for the
        # special in-memory / shared-cache URIs used by tests).
        if db_path not in (":memory:",) and not db_path.startswith("file:"):
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        # check_same_thread=False: the connection is shared across the consumer
        # and HTTP threads, but every access is serialized by self.lock above,
        # so cross-thread use is safe.
        self.conn = sqlite3.connect(
            db_path, check_same_thread=False,
            uri=db_path.startswith("file:"),
        )
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with self.lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS features (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    instrument   TEXT NOT NULL,
                    event_time   TEXT NOT NULL,
                    ingest_time  TEXT NOT NULL,
                    feature_json TEXT NOT NULL
                )
                """
            )
            # The point-in-time query filters and orders on exactly this triple.
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_features_pit
                ON features (instrument, event_time, ingest_time)
                """
            )
            self.conn.commit()

    def store(self, instrument, event_time, feature, ingest_time=None):
        """Persist one feature record.

        event_time is the feature payload's own timestamp; ingest_time defaults
        to wall-clock now (when Mimir received it). Both are normalized to a
        canonical UTC form so the TEXT comparisons in the PIT query are correct.
        Returns the normalized (event_time, ingest_time) actually stored.
        """
        ev = normalize_iso(event_time) or event_time
        ing = normalize_iso(ingest_time) if ingest_time else None
        if ing is None:
            ing = normalize_iso(now_iso())
        feat_json = json.dumps(feature, separators=(",", ":"))
        with self.lock:
            self.conn.execute(
                "INSERT INTO features (instrument, event_time, ingest_time, "
                "feature_json) VALUES (?, ?, ?, ?)",
                (instrument, ev, ing, feat_json),
            )
            self.conn.commit()
        return ev, ing

    def query_as_of(self, as_of=None, instrument=None):
        """Point-in-time query.

        Returns, per instrument, the single most recent feature satisfying
        BOTH guards:
            event_time  <= as_of   (the feature is about a moment at/before T)
            ingest_time <= as_of   (Mimir had physically received it by T)

        The ingest_time guard is what blocks look-ahead from late-arriving /
        revised data: a feature with event_time <= as_of but ingest_time > as_of
        was not yet known at T and is correctly invisible. When as_of is omitted
        it defaults to "now" (latest known state). When instrument is omitted,
        the latest row is returned for every instrument.
        """
        as_of_norm = normalize_iso(as_of) if as_of else normalize_iso(now_iso())
        if as_of_norm is None:
            # Unparseable as_of: treat as now rather than returning a 500.
            as_of_norm = normalize_iso(now_iso())

        params = [as_of_norm, as_of_norm]
        inst_clause = ""
        if instrument:
            inst_clause = "AND instrument = ? "
            params.append(instrument)

        # For each instrument pick the row with the greatest (event_time,
        # ingest_time) among rows passing both guards. We order by event_time
        # then ingest_time so a later-arriving correction for the SAME
        # event_time (that is still visible at as_of) supersedes the earlier
        # one. A correlated subquery keeps this pure-SQL and index-friendly.
        sql = (
            "SELECT f.instrument, f.event_time, f.ingest_time, f.feature_json "
            "FROM features f "
            "WHERE f.event_time <= ? AND f.ingest_time <= ? " + inst_clause +
            "AND f.id = ("
            "  SELECT g.id FROM features g "
            "  WHERE g.instrument = f.instrument "
            "    AND g.event_time <= ? AND g.ingest_time <= ? "
            "  ORDER BY g.event_time DESC, g.ingest_time DESC, g.id DESC "
            "  LIMIT 1"
            ") "
            "ORDER BY f.instrument ASC"
        )
        params.extend([as_of_norm, as_of_norm])

        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()

        features = [
            {
                "instrument": r["instrument"],
                "event_time": r["event_time"],
                "ingest_time": r["ingest_time"],
                "feature": json.loads(r["feature_json"]),
            }
            for r in rows
        ]
        return {
            "asOf": as_of_norm,
            "basis": "point-in-time (event_time<=as_of AND ingest_time<=as_of)",
            "features": features,
        }

    def history(self, instrument, limit=50):
        """Most recent stored rows for an instrument, newest-first.

        Ordered by ingest_time then event_time (the physical arrival order is
        the most useful "what did we learn, and when" view), most recent first.
        """
        with self.lock:
            rows = self.conn.execute(
                "SELECT instrument, event_time, ingest_time, feature_json "
                "FROM features WHERE instrument = ? "
                "ORDER BY ingest_time DESC, event_time DESC, id DESC LIMIT ?",
                (instrument, int(limit)),
            ).fetchall()
        return {
            "instrument": instrument,
            "count": len(rows),
            "rows": [
                {
                    "instrument": r["instrument"],
                    "event_time": r["event_time"],
                    "ingest_time": r["ingest_time"],
                    "feature": json.loads(r["feature_json"]),
                }
                for r in rows
            ],
        }

    def sources(self):
        """Per-instrument lineage / freshness.

        For each instrument: row count, first/last event_time, last
        ingest_time, and the maximum ingest lag (ingest_time - event_time, in
        seconds) ever observed — a large max lag flags late-arriving data that
        the point-in-time guard is actively protecting backtests from.
        """
        with self.lock:
            rows = self.conn.execute(
                "SELECT instrument, event_time, ingest_time "
                "FROM features ORDER BY instrument ASC"
            ).fetchall()

        agg = {}
        for r in rows:
            inst = r["instrument"]
            ev = r["event_time"]
            ing = r["ingest_time"]
            a = agg.get(inst)
            if a is None:
                a = {
                    "instrument": inst,
                    "count": 0,
                    "first_event_time": ev,
                    "last_event_time": ev,
                    "last_ingest_time": ing,
                    "max_ingest_lag_secs": 0.0,
                }
                agg[inst] = a
            a["count"] += 1
            if ev < a["first_event_time"]:
                a["first_event_time"] = ev
            if ev > a["last_event_time"]:
                a["last_event_time"] = ev
            if ing > a["last_ingest_time"]:
                a["last_ingest_time"] = ing
            lag = self._lag_secs(ev, ing)
            if lag is not None and lag > a["max_ingest_lag_secs"]:
                a["max_ingest_lag_secs"] = round(lag, 3)

        return {"sources": [agg[k] for k in sorted(agg.keys())]}

    @staticmethod
    def _lag_secs(event_time, ingest_time):
        """ingest_time - event_time in seconds, or None if either won't parse."""
        ev = normalize_iso(event_time)
        ing = normalize_iso(ingest_time)
        if ev is None or ing is None:
            return None
        try:
            return (
                datetime.fromisoformat(ing) - datetime.fromisoformat(ev)
            ).total_seconds()
        except (ValueError, TypeError):
            return None


# The store is created lazily (not at import time) so that importing mimir —
# e.g. from the unit tests, which construct their own temp-file FeatureStore —
# does NOT create a stray file-backed DB at MIMIR_DB. The running service
# initializes it via get_store() on first request / in main().
_store = None
_store_lock = threading.Lock()


def get_store():
    """Return the process-wide FeatureStore, creating it on first use."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = FeatureStore()
    return _store


# ---------------------------------------------------------------------------
# Feature record parsing.
#
# obi-bridge (services/obi-bridge/bridge.py:441) emits feature events shaped
# like {"eventTime": ISO, "ingestTime": ISO, "instrument": "BTC-USDT",
# "values": {...}, ...}. event_time is the payload's own ``eventTime``. Mimir
# does NOT trust the upstream ingestTime for the PIT guard — ingest_time is the
# wall-clock instant MIMIR received the record, because that is what governs
# what a consumer running at time T could actually have seen.
# ---------------------------------------------------------------------------
def extract_record(payload):
    """Pull (instrument, event_time, feature_dict) out of a feature event.

    Returns (instrument, event_time, feature) or None if the payload is missing
    the fields Mimir needs (instrument + a usable event timestamp). Accepts both
    the canonical obi-bridge ``eventTime`` and a snake_case ``event_time``
    fallback so the store is forgiving of producer variants.
    """
    if not isinstance(payload, dict):
        return None
    instrument = payload.get("instrument")
    if not instrument or not isinstance(instrument, str):
        return None
    event_time = (
        payload.get("eventTime")
        or payload.get("event_time")
        or payload.get("timestamp")
    )
    if not event_time or not isinstance(event_time, str):
        return None
    return instrument, event_time, payload


class MimirHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/features":
            instrument = qs.get("instrument", [None])[0]
            as_of = qs.get("as_of", [None])[0]
            self._json_response(
                get_store().query_as_of(as_of=as_of, instrument=instrument)
            )
        elif path == "/api/features/history":
            instrument = qs.get("instrument", [None])[0]
            if not instrument:
                self._json_response(
                    {"error": "instrument query param required"}, status=400
                )
                return
            try:
                limit = int(qs.get("limit", ["50"])[0])
            except (ValueError, TypeError):
                limit = 50
            limit = max(1, min(limit, 1000))
            self._json_response(get_store().history(instrument, limit=limit))
        elif path == "/api/sources":
            self._json_response(get_store().sources())
        elif path == "/healthz" or path == "/readyz":
            ok, age = liveness.status()
            payload = {
                "status": "ok" if ok else "degraded",
                "service": "mimir",
                "consumer_alive": ok,
                "consumer_last_beat_age_secs": (
                    round(age, 1) if age is not None else None
                ),
            }
            self._json_response(payload, status=200 if ok else 503)
        else:
            self.send_error(404)

    def _json_response(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", ACCESS_CONTROL_ALLOW_ORIGIN)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _make_features_consumer(consumer_factory=KafkaConsumer):
    """Build the features consumer as a FULL-TOPIC, append-based projection.

    Like odin, Mimir replays the entire topic from the beginning on every
    start: a fresh unique group_id (so Kafka has no committed offset to resume
    from) + auto_offset_reset="earliest" + disabled auto-commit. Unlike odin,
    Mimir does NOT dedup, because a re-run that re-inserts the same event with a
    NEW ingest_time would corrupt the point-in-time history. Instead the store
    is keyed to a persistent file (MIMIR_DB) so the history survives restarts,
    and the consumer's job is purely to append newly-arrived events with a true
    wall-clock ingest_time. (If a full rebuild is ever needed, delete the DB
    file.)
    """
    return consumer_factory(
        FEATURES_TOPIC,
        bootstrap_servers=KAFKA_BROKERS,
        group_id="mimir-feature-store-{}".format(uuid.uuid4().hex),
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
    )


def consume_features():
    for attempt in range(30):
        try:
            consumer = _make_features_consumer()
            log.info("Connected to Kafka, consuming %s", FEATURES_TOPIC)
            break
        except KafkaConnectionError:
            log.warning("Kafka not ready (attempt %d/30)...", attempt + 1)
            time.sleep(2)
    else:
        log.error("Failed to connect to Kafka")
        return

    liveness.mark_started()

    while not shutdown:
        try:
            records = consumer.poll(timeout_ms=1000)
            # Heartbeat once per poll cycle, whether or not records arrived, so
            # /healthz reflects loop liveness rather than message arrival rate.
            liveness.beat()
            for tp, messages in records.items():
                for msg in messages:
                    # Per-message decode: a bad record is isolated to itself.
                    try:
                        payload = json.loads(msg.value.decode("utf-8"))
                    except (ValueError, TypeError, UnicodeDecodeError) as de:
                        log.warning("Dropping undecodable feature record: %s", de)
                        continue

                    rec = extract_record(payload)
                    if rec is None:
                        log.warning("Skipping feature missing instrument/eventTime")
                        continue

                    instrument, event_time, feature = rec
                    # ingest_time is MIMIR's wall-clock receipt time — NOT the
                    # upstream ingestTime — so the PIT guard reflects when this
                    # record was actually knowable to a consumer.
                    ev, ing = get_store().store(instrument, event_time, feature)
                    log.info(
                        "Stored feature: %s event_time=%s ingest_time=%s",
                        instrument, ev, ing,
                    )
        except Exception as e:
            log.error("Consumer error: %s", e)
            time.sleep(1)

    consumer.close()


def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("=" * 60)
    log.info("  MIMIR — Point-in-Time (no-lookahead) Feature Store")
    log.info("=" * 60)
    log.info("  Features topic: %s", FEATURES_TOPIC)
    log.info("  DB path:        %s", DB_PATH)
    log.info("  API port:       %d", PORT)
    log.info("  Endpoints:")
    log.info("    /api/features          — point-in-time feature lookup")
    log.info("    /api/features/history  — recent rows for an instrument")
    log.info("    /api/sources           — per-instrument lineage + ingest lag")
    log.info("  PIT rule: event_time<=as_of AND ingest_time<=as_of")
    log.info("=" * 60)

    consumer_thread = threading.Thread(target=consume_features, daemon=True)
    consumer_thread.start()

    server = HTTPServer(("0.0.0.0", PORT), MimirHandler)
    server.timeout = 1
    log.info("Mimir HTTP server listening on :%d", PORT)

    while not shutdown:
        server.handle_request()

    server.server_close()
    log.info("Mimir shutdown complete")


if __name__ == "__main__":
    main()
