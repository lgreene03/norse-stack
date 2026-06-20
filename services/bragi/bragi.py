#!/usr/bin/env python3
"""
Bragi — Trade Explainability & Decision Log.

Consumes feature events and fills from Kafka, correlates signals with
execution outcomes, and produces human-readable explanations for every
trade decision — including trades that were blocked by filters.

Endpoints:
  GET /api/decisions       — recent decision log (trades + blocks)
  GET /api/decisions/stats — summary: how many blocked by each filter
  GET /healthz             — health check

Named after Bragi, Norse god of poetry — he explains things beautifully.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from collections import defaultdict, deque
from http.server import HTTPServer, BaseHTTPRequestHandler

from kafka import KafkaConsumer
from kafka.errors import KafkaConnectionError

KAFKA_BROKERS = os.environ.get("KAFKA_BROKERS", "redpanda:29092")
FEATURES_TOPIC = os.environ.get("FEATURES_TOPIC", "features.obi.v1")
FILLS_TOPIC = os.environ.get("FILLS_TOPIC", "executions.fills.v1")
FEATURES_DLQ_TOPIC = os.environ.get("FEATURES_DLQ_TOPIC", f"{FEATURES_TOPIC}.dlq")
FILLS_DLQ_TOPIC = os.environ.get("FILLS_DLQ_TOPIC", f"{FILLS_TOPIC}.dlq")
PORT = int(os.environ.get("PORT", "8087"))
OBI_THRESHOLD = float(os.environ.get("OBI_THRESHOLD", "0.90"))

# Offset reset. Bragi reads from "earliest" and dedups on event id so a restart
# rebuilds the decision log deterministically instead of silently losing every
# event produced while it was down. Set to "latest" only if intentional live
# tail is wanted (then the seen-set dedup is a harmless no-op within a session).
AUTO_OFFSET_RESET = os.environ.get("AUTO_OFFSET_RESET", "earliest")

# CORS: default "*" preserves existing behaviour; lock to a single origin in
# hardened deployments via ACCESS_CONTROL_ALLOW_ORIGIN.
ACCESS_CONTROL_ALLOW_ORIGIN = os.environ.get("ACCESS_CONTROL_ALLOW_ORIGIN", "*")

# Liveness: the consumer stamps a heartbeat per poll cycle; /healthz returns
# 503 once it's stale beyond this threshold.
HEALTH_MAX_STALENESS_SECS = float(os.environ.get("HEALTH_MAX_STALENESS_SECS", "120"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("bragi")

shutdown = False


def handle_signal(signum, frame):
    global shutdown
    shutdown = True
    log.info("Shutdown signal received")


class Liveness:
    """Tracks the consumer thread's last-progress timestamp for /healthz.

    Beat once per poll cycle (regardless of message arrival). Reports healthy
    until the loop has started so container startup isn't failed closed during
    Kafka connect/retry.
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
        with self._lock:
            if not self._started or self._last_beat is None:
                return True, None
            age = time.monotonic() - self._last_beat
            return age <= HEALTH_MAX_STALENESS_SECS, age


liveness = Liveness()


def _make_dlq_producer():
    """Best-effort DLQ producer for poison records. Returns producer or None."""
    try:
        from kafka import KafkaProducer
        return KafkaProducer(bootstrap_servers=KAFKA_BROKERS)
    except Exception as e:  # pragma: no cover - depends on kafka availability
        log.warning("DLQ producer unavailable (%s); decode failures counter-only", e)
        return None


class DecisionLog:
    def __init__(self, max_entries=500):
        self.lock = threading.Lock()
        self.decisions = deque(maxlen=max_entries)
        self.fills = {}  # eventId -> fill data for correlation
        self.stats = defaultdict(int)
        self.decode_failures = 0
        self.duplicate_skipped = 0

        # Dedup: the consumer reads from the earliest offset, so a restart
        # replays the whole topic. A bounded seen-set keyed on the source event
        # id keeps the decision log + stats from double-counting on replay
        # while staying memory-flat.
        self._seen_ids = deque(maxlen=50000)
        self._seen_set = set()

    def _is_duplicate(self, event_id):
        """Return True if event_id was already processed (and record it).

        Events with no id are never deduped (always processed). Must be called
        with self.lock held."""
        if not event_id:
            return False
        if event_id in self._seen_set:
            return True
        self._seen_set.add(event_id)
        if len(self._seen_ids) == self._seen_ids.maxlen:
            self._seen_set.discard(self._seen_ids[0])
        self._seen_ids.append(event_id)
        return False

    def add_feature_event(self, event):
        with self.lock:
            # Dedup on the feature eventId so a restart (earliest replay) does
            # not double-count decisions/stats.
            if self._is_duplicate(event.get("eventId")):
                self.duplicate_skipped += 1
                return
            values = event.get("values", {})
            obi = values.get("obi", 0)
            momentum = values.get("momentum", 0)
            volatility = values.get("volatility", 0)
            fear_greed = values.get("fearGreed", 50)
            volume_ratio = values.get("volumeRatio", 1.0)
            mid_price = values.get("midPrice", 0)
            instrument = event.get("instrument", "?")
            timestamp = event.get("eventTime", "")

            # Effective threshold with vol widening
            eff_threshold = OBI_THRESHOLD
            if volatility > 0.015:
                eff_threshold = OBI_THRESHOLD + 0.05

            decision = {
                "timestamp": timestamp,
                "instrument": instrument,
                "type": "no_signal",
                "obi": round(obi, 4),
                "momentum": round(momentum, 6),
                "volatility": round(volatility, 6),
                "fear_greed": fear_greed,
                "volume_ratio": round(volume_ratio, 2),
                "mid_price": mid_price,
                "effective_threshold": round(eff_threshold, 2),
                "explanation": "",
                "blocked_by": None,
                "would_have_traded": False,
            }

            abs_obi = abs(obi)

            if abs_obi < eff_threshold:
                decision["type"] = "no_signal"
                decision["explanation"] = (
                    f"OBI {obi:+.4f} within threshold (+/-{eff_threshold:.2f}). "
                    f"Market balanced — no trade signal."
                )
                self.stats["no_signal"] += 1

            elif obi > eff_threshold:
                # Would be a SELL signal
                decision["would_have_traded"] = True

                if volume_ratio > 3.0:
                    decision["type"] = "blocked"
                    decision["blocked_by"] = "volume_spike"
                    decision["explanation"] = (
                        f"SELL signal (OBI {obi:+.4f} > {eff_threshold:.2f}) "
                        f"BLOCKED: volume spike detected ({volume_ratio:.1f}x normal). "
                        f"Likely news-driven move — unsafe to fade."
                    )
                    self.stats["blocked_volume"] += 1

                elif momentum > 0.002:
                    decision["type"] = "blocked"
                    decision["blocked_by"] = "momentum"
                    decision["explanation"] = (
                        f"SELL signal (OBI {obi:+.4f} > {eff_threshold:.2f}) "
                        f"BLOCKED: momentum is bullish ({momentum:+.4f}). "
                        f"Selling into a genuine uptrend risks catching a trend, "
                        f"not a mean-reversion."
                    )
                    self.stats["blocked_momentum"] += 1

                elif fear_greed > 0 and fear_greed < 20:
                    decision["type"] = "blocked"
                    decision["blocked_by"] = "sentiment"
                    decision["explanation"] = (
                        f"SELL signal (OBI {obi:+.4f} > {eff_threshold:.2f}) "
                        f"BLOCKED: extreme fear (F&G={fear_greed}). "
                        f"Market already oversold — selling into panic is contrarian "
                        f"to an already contrarian crowd."
                    )
                    self.stats["blocked_sentiment"] += 1

                else:
                    decision["type"] = "trade"
                    decision["explanation"] = (
                        f"SELL signal FIRED. OBI {obi:+.4f} > {eff_threshold:.2f} "
                        f"(extreme buy pressure, expect reversion). "
                        f"Momentum neutral/bearish ({momentum:+.4f}), "
                        f"vol regime OK ({volatility:.4f}), "
                        f"sentiment allows (F&G={fear_greed}). "
                        f"Selling {instrument} at ~${mid_price:,.2f}."
                    )
                    self.stats["traded"] += 1

            elif obi < -eff_threshold:
                # Would be a BUY signal
                decision["would_have_traded"] = True

                if volume_ratio > 3.0:
                    decision["type"] = "blocked"
                    decision["blocked_by"] = "volume_spike"
                    decision["explanation"] = (
                        f"BUY signal (OBI {obi:+.4f} < -{eff_threshold:.2f}) "
                        f"BLOCKED: volume spike detected ({volume_ratio:.1f}x normal). "
                        f"Likely news-driven move — unsafe to catch falling knife."
                    )
                    self.stats["blocked_volume"] += 1

                elif momentum < -0.002:
                    decision["type"] = "blocked"
                    decision["blocked_by"] = "momentum"
                    decision["explanation"] = (
                        f"BUY signal (OBI {obi:+.4f} < -{eff_threshold:.2f}) "
                        f"BLOCKED: momentum is bearish ({momentum:+.4f}). "
                        f"Buying into a downtrend risks catching a falling knife, "
                        f"not a mean-reversion bounce."
                    )
                    self.stats["blocked_momentum"] += 1

                elif fear_greed > 80:
                    decision["type"] = "blocked"
                    decision["blocked_by"] = "sentiment"
                    decision["explanation"] = (
                        f"BUY signal (OBI {obi:+.4f} < -{eff_threshold:.2f}) "
                        f"BLOCKED: extreme greed (F&G={fear_greed}). "
                        f"Market overbought — buying into euphoria is dangerous."
                    )
                    self.stats["blocked_sentiment"] += 1

                else:
                    decision["type"] = "trade"
                    decision["explanation"] = (
                        f"BUY signal FIRED. OBI {obi:+.4f} < -{eff_threshold:.2f} "
                        f"(extreme sell pressure, expect reversion). "
                        f"Momentum neutral/bullish ({momentum:+.4f}), "
                        f"vol regime OK ({volatility:.4f}), "
                        f"sentiment allows (F&G={fear_greed}). "
                        f"Buying {instrument} at ~${mid_price:,.2f}."
                    )
                    self.stats["traded"] += 1

            self.decisions.append(decision)

            # Log blocked trades and actual trades
            if decision["type"] in ("blocked", "trade"):
                marker = "BLOCKED" if decision["type"] == "blocked" else "TRADE"
                log.info(
                    "[%s] %s %s | OBI:%+.4f Mom:%+.4f Vol:%.4f F&G:%d",
                    marker, instrument,
                    decision.get("blocked_by", ""),
                    obi, momentum, volatility, fear_greed,
                )

    def add_fill(self, fill):
        with self.lock:
            # Dedup fills on execution_id (falling back to order_id) so replay
            # on restart doesn't re-store stale correlations. order_id is still
            # the correlation key.
            dedup_key = fill.get("execution_id") or fill.get("order_id")
            if self._is_duplicate(f"fill:{dedup_key}" if dedup_key else None):
                self.duplicate_skipped += 1
                return
            self.fills[fill.get("order_id", "")] = fill

    def get_decisions(self, limit=50, filter_type=None):
        with self.lock:
            items = list(self.decisions)
            if filter_type:
                items = [d for d in items if d["type"] == filter_type]
            return list(reversed(items[-limit:]))

    def get_stats(self):
        with self.lock:
            total = sum(self.stats.values())
            return {
                "total_events": total,
                "breakdown": dict(self.stats),
                "decode_failures": self.decode_failures,
                "duplicate_skipped": self.duplicate_skipped,
                "filter_effectiveness": {
                    "blocked_total": (
                        self.stats["blocked_momentum"]
                        + self.stats["blocked_sentiment"]
                        + self.stats["blocked_volume"]
                    ),
                    "traded": self.stats["traded"],
                    "would_have_traded": (
                        self.stats["traded"]
                        + self.stats["blocked_momentum"]
                        + self.stats["blocked_sentiment"]
                        + self.stats["blocked_volume"]
                    ),
                    "block_rate": round(
                        (
                            self.stats["blocked_momentum"]
                            + self.stats["blocked_sentiment"]
                            + self.stats["blocked_volume"]
                        )
                        / max(
                            self.stats["traded"]
                            + self.stats["blocked_momentum"]
                            + self.stats["blocked_sentiment"]
                            + self.stats["blocked_volume"],
                            1,
                        ),
                        3,
                    ),
                },
            }


decision_log = DecisionLog()


class BragiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/api/decisions":
            self._json_response(decision_log.get_decisions())
        elif self.path == "/api/decisions/blocked":
            self._json_response(decision_log.get_decisions(filter_type="blocked"))
        elif self.path == "/api/decisions/trades":
            self._json_response(decision_log.get_decisions(filter_type="trade"))
        elif self.path == "/api/decisions/stats":
            self._json_response(decision_log.get_stats())
        elif self.path == "/healthz" or self.path == "/readyz":
            ok, age = liveness.status()
            self._json_response(
                {
                    "status": "ok" if ok else "degraded",
                    "service": "bragi",
                    "consumer_alive": ok,
                    "consumer_last_beat_age_secs": (
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

    def log_message(self, format, *args):
        pass


def consume_events():
    for attempt in range(30):
        try:
            # Raw-bytes deserialization: JSON decode happens per-message below
            # so a poison record is isolated (counter + DLQ) rather than
            # raising out of poll() and stalling the batch. Reads from
            # AUTO_OFFSET_RESET (earliest by default) with id-based dedup so a
            # restart rebuilds the log without losing or double-counting events.
            consumer = KafkaConsumer(
                FEATURES_TOPIC,
                FILLS_TOPIC,
                bootstrap_servers=KAFKA_BROKERS,
                group_id="bragi-explainer",
                auto_offset_reset=AUTO_OFFSET_RESET,
                consumer_timeout_ms=1000,
            )
            log.info("Connected to Kafka, consuming %s + %s", FEATURES_TOPIC, FILLS_TOPIC)
            break
        except KafkaConnectionError:
            log.warning("Kafka not ready (attempt %d/30)...", attempt + 1)
            time.sleep(2)
    else:
        log.error("Failed to connect to Kafka")
        return

    dlq_producer = _make_dlq_producer()
    liveness.mark_started()

    while not shutdown:
        try:
            records = consumer.poll(timeout_ms=1000)
            liveness.beat()
            for tp, messages in records.items():
                dlq_topic = (
                    FEATURES_DLQ_TOPIC if tp.topic == FEATURES_TOPIC
                    else FILLS_DLQ_TOPIC
                )
                for msg in messages:
                    try:
                        value = json.loads(msg.value.decode("utf-8"))
                    except (ValueError, TypeError, UnicodeDecodeError) as de:
                        with decision_log.lock:
                            decision_log.decode_failures += 1
                        log.warning("Dropping undecodable record (%s): %s",
                                    tp.topic, de)
                        if dlq_producer is not None:
                            try:
                                dlq_producer.send(dlq_topic, value=msg.value)
                            except Exception as pe:  # pragma: no cover
                                log.warning("DLQ publish failed: %s", pe)
                        continue

                    if not isinstance(value, dict):
                        with decision_log.lock:
                            decision_log.decode_failures += 1
                        continue

                    if tp.topic == FEATURES_TOPIC:
                        decision_log.add_feature_event(value)
                    elif tp.topic == FILLS_TOPIC:
                        decision_log.add_fill(value)
        except Exception as e:
            log.error("Consumer error: %s", e)
            time.sleep(1)

    consumer.close()
    if dlq_producer is not None:
        dlq_producer.close()


def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("=" * 60)
    log.info("  BRAGI — Trade Explainability Service")
    log.info("=" * 60)
    log.info("  Features: %s", FEATURES_TOPIC)
    log.info("  Fills:    %s", FILLS_TOPIC)
    log.info("  API port: %d", PORT)
    log.info("  Threshold: %.2f", OBI_THRESHOLD)
    log.info("=" * 60)

    consumer_thread = threading.Thread(target=consume_events, daemon=True)
    consumer_thread.start()

    server = HTTPServer(("0.0.0.0", PORT), BragiHandler)
    server.timeout = 1
    log.info("Bragi HTTP server listening on :%d", PORT)

    while not shutdown:
        server.handle_request()

    server.server_close()
    log.info("Bragi shutdown complete")


if __name__ == "__main__":
    main()
