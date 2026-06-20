#!/usr/bin/env python3
"""
Huginn-AI -- XGBoost Signal Quality Predictor.

Consumes feature events and fill executions from Kafka, labels trade outcomes
via FIFO round-trip accounting, trains an XGBoost binary classifier on signal
quality, and serves confidence predictions via a REST API.

Graceful degradation: when the model is untrained, every prediction returns
confidence=0.5 (neutral), so downstream strategy decisions are unaffected.

Named after Huginn, Odin's thought-raven -- it learns from observation.
"""

import collections
import hashlib
import json
import logging
import math
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import numpy as np
import xgboost as xgb
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score

from kafka import KafkaConsumer
from kafka.errors import KafkaConnectionError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
KAFKA_BROKERS = os.environ.get("KAFKA_BROKERS", "redpanda:29092")
FEATURES_TOPIC = os.environ.get("FEATURES_TOPIC", "features.obi.v1")
FILLS_TOPIC = os.environ.get("FILLS_TOPIC", "executions.fills.v1")
PORT = int(os.environ.get("PORT", "8091"))
MIN_SAMPLES = int(os.environ.get("MIN_SAMPLES", "30"))
RETRAIN_INTERVAL = int(os.environ.get("RETRAIN_INTERVAL", "50"))
FEATURE_BUFFER_SIZE = int(os.environ.get("FEATURE_BUFFER_SIZE", "100"))
CONSUMER_GROUP = os.environ.get("CONSUMER_GROUP", "huginn-ai-ml")

# DLQ topics for poison (undecodable) records, one per source topic.
FEATURES_DLQ_TOPIC = os.environ.get(
    "FEATURES_DLQ_TOPIC", f"{FEATURES_TOPIC}.dlq"
)
FILLS_DLQ_TOPIC = os.environ.get("FILLS_DLQ_TOPIC", f"{FILLS_TOPIC}.dlq")

# CORS: default "*" preserves existing behaviour; lock to a single origin in
# hardened deployments via ACCESS_CONTROL_ALLOW_ORIGIN.
ACCESS_CONTROL_ALLOW_ORIGIN = os.environ.get("ACCESS_CONTROL_ALLOW_ORIGIN", "*")

# Liveness: each consumer thread stamps a heartbeat per poll cycle; /healthz
# returns 503 once ALL started consumers are stale beyond this threshold.
HEALTH_MAX_STALENESS_SECS = float(os.environ.get("HEALTH_MAX_STALENESS_SECS", "120"))

# Sample buffer cap: bound memory for the labeled/pending training set.
MAX_SAMPLES = int(os.environ.get("MAX_SAMPLES", "2000"))

# Promotion gate: minimum positive (profitable) labels required in the held-out
# test set before a freshly trained challenger may replace the live model.
MIN_TEST_POSITIVES = int(os.environ.get("MIN_TEST_POSITIVES", "1"))

# Model persistence. New persistence is best-effort: if the directory is
# unwritable we warn and keep running in-memory only.
MODEL_DIR = os.environ.get("MODEL_DIR", "/data/huginn-ai")
MODEL_FILE = os.path.join(MODEL_DIR, "model.json")
MODEL_META_FILE = os.path.join(MODEL_DIR, "model_meta.json")

# XGBoost hyperparameters
XGB_MAX_DEPTH = int(os.environ.get("XGB_MAX_DEPTH", "4"))
XGB_N_ESTIMATORS = int(os.environ.get("XGB_N_ESTIMATORS", "100"))
XGB_LEARNING_RATE = float(os.environ.get("XGB_LEARNING_RATE", "0.1"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("huginn-ai")

shutdown = False


def handle_signal(signum, frame):
    global shutdown
    shutdown = True
    log.info("Shutdown signal received")


# ---------------------------------------------------------------------------
# Metrics (stdlib-only, Prometheus-style; mirrors odin's Counters)
# ---------------------------------------------------------------------------
class Metrics:
    """Thread-safe Prometheus-style counters + gauges.

    huginn-ai already depends on numpy/xgboost, but we still avoid pulling in
    prometheus_client to keep the surface small. Counters are monotonic; gauges
    are point-in-time values (PSI per feature, rolling positive-label rate,
    live confidence distribution). All are exposed via /metrics.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counts = collections.defaultdict(int)
        self._gauges = {}

    def inc(self, name, amount=1):
        with self._lock:
            self._counts[name] += amount

    def set_gauge(self, name, value, labels=None):
        key = name
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
            key = f"{name}{{{label_str}}}"
        with self._lock:
            self._gauges[key] = float(value)

    def snapshot(self):
        with self._lock:
            return dict(self._counts), dict(self._gauges)

    def render_prometheus(self):
        counts, gauges = self.snapshot()
        lines = []
        for name, value in sorted(counts.items()):
            metric = f"huginn_{name}"
            lines.append(f"# TYPE {metric} counter")
            lines.append(f"{metric} {value}")
        for name, value in sorted(gauges.items()):
            # name may already carry a {labels} suffix; prefix the base metric.
            if "{" in name:
                base, suffix = name.split("{", 1)
                metric = f"huginn_{base}{{{suffix}"
                type_name = f"huginn_{base}"
            else:
                metric = f"huginn_{name}"
                type_name = metric
            lines.append(f"# TYPE {type_name} gauge")
            lines.append(f"{metric} {value}")
        return "\n".join(lines) + "\n"


metrics = Metrics()


class Liveness:
    """Per-thread last-progress tracker for /healthz liveness.

    Each named consumer registers and beats once per poll cycle. /healthz is
    healthy until every registered consumer has gone stale (so one wedged
    consumer degrades readiness while still surfacing which one). Before any
    consumer registers, health is reported OK so container startup isn't failed
    closed during Kafka connect/retry.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._beats = {}  # name -> monotonic timestamp

    def register(self, name):
        with self._lock:
            self._beats[name] = time.monotonic()

    def beat(self, name):
        with self._lock:
            self._beats[name] = time.monotonic()

    def status(self):
        """Return (ok, detail). ok=True before any consumer registers."""
        with self._lock:
            if not self._beats:
                return True, {}
            now = time.monotonic()
            detail = {}
            any_alive = False
            for name, ts in self._beats.items():
                age = now - ts
                alive = age <= HEALTH_MAX_STALENESS_SECS
                any_alive = any_alive or alive
                detail[name] = {"alive": alive, "age_secs": round(age, 1)}
            return any_alive, detail


liveness = Liveness()


def _make_dlq_producer():
    """Best-effort DLQ producer. Returns a KafkaProducer or None.

    Used to republish poison (undecodable) records so they aren't silently
    lost. If construction fails we degrade to counter-only handling.
    """
    try:
        from kafka import KafkaProducer
        return KafkaProducer(bootstrap_servers=KAFKA_BROKERS)
    except Exception as e:  # pragma: no cover - depends on kafka availability
        log.warning("DLQ producer unavailable (%s); decode failures counter-only", e)
        return None


def _git_sha():
    """Best-effort git SHA for provenance; returns 'unknown' off a checkout."""
    env_sha = os.environ.get("GIT_SHA")
    if env_sha:
        return env_sha
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------
# Canonical feature vector order. Each engineered feature is derived from one
# raw obi-bridge `values` key (or the event time). FEATURE_SOURCES maps the
# engineered name to the raw key it depends on (None == derived from eventTime),
# which lets a test assert every source key is actually present in a real
# obi-bridge event and lets us emit a metric when a key falls back to default.
FEATURE_NAMES = [
    "abs_obi", "obi_sign", "momentum", "volatility",
    "fear_greed_norm", "volume_ratio", "spread_pct",
    "hour_sin", "hour_cos", "day_sin", "day_cos",
]

FEATURE_SOURCES = {
    "abs_obi": "obi",
    "obi_sign": "obi",
    "momentum": "momentum",
    "volatility": "volatility",
    "fear_greed_norm": "fearGreed",
    "volume_ratio": "volumeRatio",
    "spread_pct": "spread",        # also needs midPrice
    "hour_sin": None,
    "hour_cos": None,
    "day_sin": None,
    "day_cos": None,
}

# Raw keys whose absence forces a default fill. spread_pct additionally needs
# midPrice; we track that one explicitly too.
_DEFAULTABLE_KEYS = ["obi", "midPrice", "spread", "momentum",
                     "volatility", "fearGreed", "volumeRatio"]


def _feature_schema_hash():
    """Stable hash of the feature schema (names + sources) for provenance."""
    payload = json.dumps(
        {"names": FEATURE_NAMES, "sources": FEATURE_SOURCES},
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _coerce_float(values, key, default):
    """Pull a finite float from a values dict; emit a metric on fallback.

    Returns (value, used_default). NaN/Inf are treated as missing.
    """
    raw = values.get(key)
    if raw is None:
        metrics.inc(f"feature_default_total_{key}")
        return default, True
    try:
        val = float(raw)
    except (TypeError, ValueError):
        metrics.inc(f"feature_default_total_{key}")
        return default, True
    if math.isnan(val) or math.isinf(val):
        metrics.inc(f"feature_default_total_{key}")
        return default, True
    return val, False


def extract_features(values, event_time_str):
    """Build a numeric feature vector from a feature event's values dict.

    All raw inputs are coerced through _coerce_float so NaN/Inf and missing
    keys collapse to safe defaults (and increment a per-key fallback counter).
    The output is guaranteed finite.
    """
    obi, _ = _coerce_float(values, "obi", 0.0)
    mid_price, _ = _coerce_float(values, "midPrice", 1.0)
    spread, _ = _coerce_float(values, "spread", 0.0)

    try:
        ts = datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        ts = datetime.now(timezone.utc)

    hour = ts.hour + ts.minute / 60.0
    dow = ts.weekday()

    momentum, _ = _coerce_float(values, "momentum", 0.0)
    volatility, _ = _coerce_float(values, "volatility", 0.0)
    fear_greed, _ = _coerce_float(values, "fearGreed", 50.0)
    volume_ratio, _ = _coerce_float(values, "volumeRatio", 1.0)

    return {
        "abs_obi": abs(obi),
        "obi_sign": 1.0 if obi >= 0 else -1.0,
        "momentum": momentum,
        "volatility": volatility,
        "fear_greed_norm": fear_greed / 100.0,
        "volume_ratio": volume_ratio,
        "spread_pct": spread / mid_price if mid_price > 0 else 0.0,
        "hour_sin": math.sin(2 * math.pi * hour / 24.0),
        "hour_cos": math.cos(2 * math.pi * hour / 24.0),
        "day_sin": math.sin(2 * math.pi * dow / 7.0),
        "day_cos": math.cos(2 * math.pi * dow / 7.0),
    }


def features_to_array(feat_dict):
    """Convert a feature dict to a numpy array in canonical order."""
    return np.array([feat_dict[name] for name in FEATURE_NAMES], dtype=np.float32)


def _parse_ts(ts_str):
    """Parse an ISO-8601 timestamp; return None on failure (never 'now')."""
    if not isinstance(ts_str, str):
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Fill validation
# ---------------------------------------------------------------------------
def validate_fill(fill):
    """Validate an inbound fill event.

    Returns (ok, reason). Mirrors odin's validator: required fields, numeric
    coercion, NaN/Inf and non-positive guards.
    """
    if not isinstance(fill, dict):
        return False, "not_dict"

    instrument = fill.get("instrument")
    if not instrument or not isinstance(instrument, str):
        return False, "missing_instrument"

    side = str(fill.get("side", "")).upper()
    if side not in ("BUY", "SELL"):
        return False, "bad_side"

    for field in ("quantity", "fill_price"):
        if fill.get(field) is None:
            return False, f"missing_{field}"

    try:
        qty = float(fill.get("quantity"))
        price = float(fill.get("fill_price"))
        fee = float(fill.get("transaction_cost", 0) or 0)
    except (TypeError, ValueError):
        return False, "non_numeric"

    for name, val in (("quantity", qty), ("fill_price", price), ("fee", fee)):
        if math.isnan(val) or math.isinf(val):
            return False, f"nan_inf_{name}"

    if qty <= 0 or price <= 0:
        return False, "non_positive"

    return True, None


def validate_feature_event(instrument, event_time, values):
    """Validate an inbound feature event. Returns (ok, reason)."""
    if not instrument or not isinstance(instrument, str):
        return False, "missing_instrument"
    if not isinstance(values, dict) or not values:
        return False, "missing_values"
    # At least the core OBI field must be present and finite.
    raw = values.get("obi")
    if raw is None:
        return False, "missing_obi"
    try:
        obi = float(raw)
    except (TypeError, ValueError):
        return False, "non_numeric_obi"
    if math.isnan(obi) or math.isinf(obi):
        return False, "nan_inf_obi"
    return True, None


# ---------------------------------------------------------------------------
# Model manager
# ---------------------------------------------------------------------------
class ModelManager:
    """Thread-safe XGBoost model lifecycle: collect, label, train, predict."""

    STATE_UNTRAINED = "untrained"
    STATE_TRAINING = "training"
    STATE_READY = "ready"

    def __init__(self):
        self.lock = threading.Lock()
        self.model = None
        self.state = self.STATE_UNTRAINED
        self.model_version = None
        self.schema_hash = _feature_schema_hash()

        # Feature ring buffer per instrument: deque of (event_time, values, eventId)
        self.feature_buffer = {}

        # Training samples — capped to bound memory. Oldest evicted at the cap.
        # Each sample: features, label (None until resolved), entry metadata,
        # and label_time (the resolution timestamp, for temporal split).
        self.samples = collections.deque(maxlen=MAX_SAMPLES)

        # FIFO open-lot inventory per instrument. Each lot tracks the entry
        # side/price/remaining qty/fee-per-unit and the index of the sample it
        # was opened from, so the round-trip P&L labels the *entry* sample.
        # Key: instrument -> deque of lot dicts.
        self.open_lots = collections.defaultdict(collections.deque)

        # Dedup on execution_id (consumer reads from earliest offset -> replay).
        self._seen_exec_ids = collections.deque(maxlen=50000)
        self._seen_exec_set = set()

        # Training-window feature stats for PSI (per-feature mean/std + samples).
        self._train_feature_cols = None  # np.ndarray [n_train, n_features]

        # Rolling live confidence values for distribution gauges.
        self._recent_confidences = collections.deque(maxlen=500)

        # Metrics
        self.accuracy = 0.0
        self.precision = 0.0
        self.recall = 0.0
        self.f1 = 0.0
        self.feature_importance = {}
        self.total_predictions = 0
        self.last_trained = None
        self.trained_at = None
        self.n_train_samples = 0
        self.labeled_since_last_train = 0

    # -- Feature storage ---------------------------------------------------

    def store_features(self, instrument, event_time, values, event_id=""):
        with self.lock:
            if instrument not in self.feature_buffer:
                self.feature_buffer[instrument] = collections.deque(
                    maxlen=FEATURE_BUFFER_SIZE
                )
            self.feature_buffer[instrument].append((event_time, values, event_id))

    def get_latest_features(self, instrument):
        with self.lock:
            buf = self.feature_buffer.get(instrument)
            if not buf:
                return None, None, None
            return buf[-1]

    def _find_preceding_features(self, instrument, fill_ts_str):
        """Find the most recent feature snapshot strictly *before* the fill.

        Returns (event_time, values, event_id) or None. On a fill-timestamp
        parse failure we return None (the sample is skipped) rather than
        silently attaching the latest snapshot, which could leak a feature
        captured after the decision.
        """
        buf = self.feature_buffer.get(instrument)
        if not buf:
            return None

        fill_ts = _parse_ts(fill_ts_str)
        if fill_ts is None:
            return None

        for event_time, values, event_id in reversed(buf):
            et = _parse_ts(event_time)
            if et is None:
                continue
            if et < fill_ts:  # strictly preceding
                return (event_time, values, event_id)

        return None

    # -- Fill processing & labeling -----------------------------------------

    def add_fill(self, fill):
        should_train = False
        with self.lock:
            ok, reason = validate_fill(fill)
            if not ok:
                metrics.inc("fills_rejected_total")
                metrics.inc(f"fills_rejected_{reason}")
                log.warning("Rejected fill (%s): %r", reason, fill)
                return

            # Dedup on execution_id.
            exec_id = fill.get("execution_id")
            if exec_id is not None:
                if exec_id in self._seen_exec_set:
                    metrics.inc("fills_duplicate_total")
                    return
                self._seen_exec_set.add(exec_id)
                if len(self._seen_exec_ids) == self._seen_exec_ids.maxlen:
                    evicted = self._seen_exec_ids[0]
                    self._seen_exec_set.discard(evicted)
                self._seen_exec_ids.append(exec_id)

            metrics.inc("fills_processed_total")

            instrument = fill.get("instrument", "")
            side = fill.get("side", "").upper()
            price = float(fill.get("fill_price", 0))
            qty = float(fill.get("quantity", 0))
            fee = float(fill.get("transaction_cost", 0) or 0)
            ts = fill.get("timestamp", "")

            # Attach strictly-preceding features for the (entry) sample.
            feat_pair = self._find_preceding_features(instrument, ts)
            if feat_pair is None:
                metrics.inc("fills_no_preceding_features_total")
                log.warning(
                    "No strictly-preceding features for fill on %s, skipping",
                    instrument,
                )
                return

            event_time, values, matched_event_id = feat_pair
            feat_dict = extract_features(values, event_time)

            labeled = self._fifo_match(
                instrument, side, price, qty, fee, ts,
                feat_dict, matched_event_id, event_time,
            )

            if labeled > 0:
                self.labeled_since_last_train += labeled
                self._update_label_rate_gauge()
                log.info(
                    "Labeled %d round-trip(s) from %s fill on %s",
                    labeled, side, instrument,
                )

            total_labeled = sum(1 for s in self.samples if s["label"] is not None)
            should_train = (
                total_labeled >= MIN_SAMPLES
                and self.labeled_since_last_train >= RETRAIN_INTERVAL
            ) or (
                total_labeled >= MIN_SAMPLES
                and self.state == self.STATE_UNTRAINED
            )

        if should_train:
            self._train()

    def _fifo_match(self, instrument, side, price, qty, fee, ts,
                    feat_dict, matched_event_id, feat_event_time):
        """FIFO round-trip accounting with prorated fees and partial fills.

        A fill on `side` first closes any open lots on the opposite side
        (FIFO). Each closed quantity produces a realized P&L that labels the
        *entry* sample of the lot it closed against. Any quantity not absorbed
        by existing lots opens a new lot (and registers a new entry sample).

        Fees are prorated per unit: an entry lot carries entry_fee/entry_qty as
        its per-unit fee; the closing fill's fee is prorated by the closed
        fraction of this fill's quantity.

        Returns the number of lots that became fully resolved (labeled).
        """
        opposite = "SELL" if side == "BUY" else "BUY"
        lots = self.open_lots[instrument]
        per_unit_close_fee = fee / qty if qty > 0 else 0.0
        remaining = qty
        labeled = 0

        # Close against opposite-side lots FIFO.
        while remaining > 1e-12 and lots and lots[0]["side"] == opposite:
            lot = lots[0]
            matched_qty = min(remaining, lot["qty"])

            entry_fee = lot["per_unit_fee"] * matched_qty
            close_fee = per_unit_close_fee * matched_qty

            if lot["side"] == "BUY":
                # Entry bought, this fill sells.
                gross = (price - lot["price"]) * matched_qty
            else:
                # Entry sold, this fill buys to cover.
                gross = (lot["price"] - price) * matched_qty

            pnl = gross - entry_fee - close_fee

            lot["qty"] -= matched_qty
            remaining -= matched_qty

            if lot["qty"] <= 1e-12:
                # Lot fully closed -> resolve its entry sample's label. Use the
                # stored sample reference rather than an index: the bounded
                # `samples` deque can evict the oldest entry and shift indices.
                entry_sample = lot["sample_ref"]
                # accumulate P&L across the (possibly multiple) closes
                lot["realized_pnl"] += pnl
                entry_sample["realized_pnl"] = lot["realized_pnl"]
                entry_sample["label"] = 1 if lot["realized_pnl"] > 0 else 0
                entry_sample["label_time"] = ts
                labeled += 1
                lots.popleft()
            else:
                lot["realized_pnl"] += pnl

        # Any leftover quantity opens a new lot + entry sample.
        if remaining > 1e-12:
            sample = {
                "features": feat_dict,
                "label": None,
                "instrument": instrument,
                "side": side,
                "price": price,
                "qty": remaining,
                "fee": per_unit_close_fee * remaining,
                "feature_event_id": matched_event_id,
                "feature_event_time": feat_event_time,
                "fill_time": ts,
                "label_time": None,
                "realized_pnl": None,
            }
            self.samples.append(sample)
            # Lots reference the sample dict directly (not by index): the
            # bounded `samples` deque may evict the oldest entry and shift
            # positional indices, so resolution must go through sample_ref.
            lots.append({
                "side": side,
                "price": price,
                "qty": remaining,
                "per_unit_fee": per_unit_close_fee,
                "sample_ref": sample,
                "realized_pnl": 0.0,
            })

        # Bound open-lot inventory so a one-sided stream can't grow unbounded.
        max_lots = max(2 * MAX_SAMPLES, 100)
        while len(lots) > max_lots:
            lots.popleft()

        return labeled

    # -- Training -----------------------------------------------------------

    def _temporal_split(self, labeled, test_frac=0.2):
        """Contamination-free temporal split.

        Sort by label-resolution time, hold out the most-recent `test_frac` as
        test. Critically, no training row's label-resolution time may be later
        than any test row's *feature* time -- otherwise the model would learn
        from an outcome resolved after a test sample's decision was made. We
        therefore advance the split boundary backward until that invariant
        holds.
        """
        def label_t(s):
            return _parse_ts(s.get("label_time")) or datetime.min.replace(
                tzinfo=timezone.utc
            )

        def feat_t(s):
            return _parse_ts(s.get("feature_event_time")) or _parse_ts(
                s.get("fill_time")
            ) or datetime.min.replace(tzinfo=timezone.utc)

        ordered = sorted(labeled, key=label_t)
        n = len(ordered)
        split = max(1, int(n * (1 - test_frac)))
        if split >= n:
            split = n - 1
        if split < 1:
            split = 1

        # Enforce: max label_time over train <= min feature_time over test.
        # Move the boundary left until no train label resolves after the
        # earliest test feature observation.
        while split > 1:
            test_min_feat = min(feat_t(s) for s in ordered[split:])
            train_max_label = max(label_t(s) for s in ordered[:split])
            if train_max_label <= test_min_feat:
                break
            split -= 1

        return ordered[:split], ordered[split:]

    def _train(self):
        with self.lock:
            labeled = [s for s in self.samples if s["label"] is not None]
            if len(labeled) < MIN_SAMPLES:
                return

            self.state = self.STATE_TRAINING
            log.info("Training XGBoost on %d labeled samples...", len(labeled))

            train_samples, test_samples = self._temporal_split(labeled)

            X_train = np.array(
                [features_to_array(s["features"]) for s in train_samples],
                dtype=np.float32,
            )
            y_train = np.array([s["label"] for s in train_samples], dtype=np.int32)
            X_test = np.array(
                [features_to_array(s["features"]) for s in test_samples],
                dtype=np.float32,
            ) if test_samples else np.empty((0, len(FEATURE_NAMES)), np.float32)
            y_test = np.array(
                [s["label"] for s in test_samples], dtype=np.int32
            ) if test_samples else np.empty((0,), np.int32)

            # Require both classes in the training set; otherwise XGBoost learns
            # a constant and predict_proba is meaningless.
            if len(np.unique(y_train)) < 2:
                log.warning(
                    "Training set has a single class (n=%d); skipping train.",
                    len(y_train),
                )
                self.state = (
                    self.STATE_READY if self.model is not None
                    else self.STATE_UNTRAINED
                )
                return

            # scale_pos_weight from label ratio (neg/pos) to counter imbalance.
            n_pos = int((y_train == 1).sum())
            n_neg = int((y_train == 0).sum())
            scale_pos_weight = (n_neg / n_pos) if n_pos > 0 else 1.0

            challenger = xgb.XGBClassifier(
                max_depth=XGB_MAX_DEPTH,
                n_estimators=XGB_N_ESTIMATORS,
                learning_rate=XGB_LEARNING_RATE,
                eval_metric="logloss",
                scale_pos_weight=scale_pos_weight,
                verbosity=0,
            )
            challenger.fit(X_train, y_train)

            # Evaluate challenger on held-out test set.
            if len(X_test) > 0:
                y_pred = challenger.predict(X_test)
                acc = accuracy_score(y_test, y_pred)
                prec = precision_score(y_test, y_pred, zero_division=0)
                rec = recall_score(y_test, y_pred, zero_division=0)
                f1_val = f1_score(y_test, y_pred, zero_division=0)
            else:
                acc = prec = rec = f1_val = 0.0

            promote, why = self._should_promote(
                challenger, X_test, y_test, acc,
            )

            if not promote:
                metrics.inc("model_promotion_rejected_total")
                log.warning("Challenger REJECTED (%s); keeping incumbent.", why)
                self.state = (
                    self.STATE_READY if self.model is not None
                    else self.STATE_UNTRAINED
                )
                self.labeled_since_last_train = 0
                return

            metrics.inc("model_promotion_accepted_total")

            importances = challenger.feature_importances_
            fi = {
                FEATURE_NAMES[i]: round(float(importances[i]), 4)
                for i in range(len(FEATURE_NAMES))
            }

            self.model = challenger
            self.accuracy = round(float(acc), 4)
            self.precision = round(float(prec), 4)
            self.recall = round(float(rec), 4)
            self.f1 = round(float(f1_val), 4)
            self.feature_importance = fi
            self.trained_at = datetime.now(timezone.utc).isoformat()
            self.last_trained = self.trained_at
            self.n_train_samples = len(train_samples)
            self.labeled_since_last_train = 0
            self.state = self.STATE_READY
            self._train_feature_cols = X_train.copy()
            self.model_version = self._compute_version()
            self._update_psi_gauges_locked(X_train)

            log.info(
                "Model PROMOTED v=%s (%s): accuracy=%.3f precision=%.3f "
                "recall=%.3f F1=%.3f",
                self.model_version, why, acc, prec, rec, f1_val,
            )
            log.info("Feature importance: %s", fi)

            self._persist_locked(train_samples)

    def _should_promote(self, challenger, X_test, y_test, acc):
        """Gate model promotion.

        Promote only if:
          - the held-out test set has both classes present, and
          - it has at least MIN_TEST_POSITIVES profitable samples, and
          - the challenger's test accuracy beats the incumbent's test accuracy
            on the *same* held-out set (first ever model auto-promotes).
        """
        if len(y_test) == 0:
            return False, "empty_test_set"
        if len(np.unique(y_test)) < 2:
            return False, "test_single_class"
        if int((y_test == 1).sum()) < MIN_TEST_POSITIVES:
            return False, "insufficient_test_positives"

        if self.model is None:
            return True, "first_model"

        incumbent_pred = self.model.predict(X_test)
        incumbent_acc = accuracy_score(y_test, incumbent_pred)
        if acc > incumbent_acc:
            return True, f"beat_incumbent({acc:.3f}>{incumbent_acc:.3f})"
        return False, f"no_improvement({acc:.3f}<={incumbent_acc:.3f})"

    def _compute_version(self):
        seed = f"{self.trained_at}-{self.schema_hash}-{self.n_train_samples}"
        return hashlib.sha256(seed.encode()).hexdigest()[:12]

    # -- Persistence --------------------------------------------------------

    def _persist_locked(self, train_samples):
        """Persist booster + sidecar metadata. Non-fatal on failure."""
        try:
            os.makedirs(MODEL_DIR, exist_ok=True)
            self.model.save_model(MODEL_FILE)
            meta = {
                "model_version": self.model_version,
                "trained_at": self.trained_at,
                "n_samples": self.n_train_samples,
                "feature_schema_hash": self.schema_hash,
                "feature_names": FEATURE_NAMES,
                "git_sha": _git_sha(),
                "eval_metrics": {
                    "accuracy": self.accuracy,
                    "precision": self.precision,
                    "recall": self.recall,
                    "f1": self.f1,
                },
            }
            tmp = MODEL_META_FILE + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(meta, fh, indent=2)
            os.replace(tmp, MODEL_META_FILE)
            log.info("Persisted model v=%s to %s", self.model_version, MODEL_DIR)
        except OSError as e:
            metrics.inc("model_persist_failures_total")
            log.warning(
                "Could not persist model to %s (%s); continuing in-memory only.",
                MODEL_DIR, e,
            )

    def load_persisted(self):
        """Load a persisted model on boot if present + schema-compatible."""
        if not (os.path.exists(MODEL_FILE) and os.path.exists(MODEL_META_FILE)):
            return False
        try:
            with open(MODEL_META_FILE) as fh:
                meta = json.load(fh)
            if meta.get("feature_schema_hash") != self.schema_hash:
                log.warning(
                    "Persisted model schema hash %s != current %s; ignoring.",
                    meta.get("feature_schema_hash"), self.schema_hash,
                )
                return False
            model = xgb.XGBClassifier()
            model.load_model(MODEL_FILE)
            with self.lock:
                self.model = model
                self.model_version = meta.get("model_version")
                self.trained_at = meta.get("trained_at")
                self.last_trained = meta.get("trained_at")
                self.n_train_samples = meta.get("n_samples", 0)
                em = meta.get("eval_metrics", {})
                self.accuracy = em.get("accuracy", 0.0)
                self.precision = em.get("precision", 0.0)
                self.recall = em.get("recall", 0.0)
                self.f1 = em.get("f1", 0.0)
                self.state = self.STATE_READY
            log.info("Loaded persisted model v=%s from %s",
                     self.model_version, MODEL_DIR)
            return True
        except (OSError, ValueError, KeyError) as e:
            metrics.inc("model_load_failures_total")
            log.warning("Could not load persisted model (%s); starting fresh.", e)
            return False

    # -- Drift / distribution gauges ---------------------------------------

    def _update_psi_gauges_locked(self, X_train):
        """Stash training-window feature columns (PSI computed at predict)."""
        self._train_feature_cols = X_train.copy()

    @staticmethod
    def _psi(expected, actual, bins=10):
        """Population Stability Index between two 1-D samples."""
        if len(expected) == 0 or len(actual) == 0:
            return 0.0
        lo = min(expected.min(), actual.min())
        hi = max(expected.max(), actual.max())
        if hi <= lo:
            return 0.0
        edges = np.linspace(lo, hi, bins + 1)
        e_hist, _ = np.histogram(expected, bins=edges)
        a_hist, _ = np.histogram(actual, bins=edges)
        e_pct = e_hist / max(e_hist.sum(), 1)
        a_pct = a_hist / max(a_hist.sum(), 1)
        eps = 1e-6
        e_pct = np.clip(e_pct, eps, None)
        a_pct = np.clip(a_pct, eps, None)
        return float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))

    def _update_label_rate_gauge(self):
        labeled = [s for s in self.samples if s["label"] is not None]
        if labeled:
            pos = sum(1 for s in labeled if s["label"] == 1)
            metrics.set_gauge("label_positive_rate", pos / len(labeled))

    def _record_confidence(self, confidence):
        self._recent_confidences.append(confidence)
        vals = list(self._recent_confidences)
        if vals:
            arr = np.array(vals, dtype=np.float64)
            metrics.set_gauge("confidence_mean", float(arr.mean()))
            metrics.set_gauge("confidence_p50", float(np.percentile(arr, 50)))
            metrics.set_gauge("confidence_p90", float(np.percentile(arr, 90)))
            metrics.set_gauge("confidence_min", float(arr.min()))
            metrics.set_gauge("confidence_max", float(arr.max()))

    def _update_feature_psi(self, x_vec):
        """Update per-feature PSI gauges using the live feature vector.

        We compare the training-window feature columns against a rolling buffer
        of recent live vectors. To keep this lock-light and stdlib-only, we
        compute PSI against the single most recent vector batched into the
        train distribution's own bins -- a cheap proxy that still moves when the
        live feature drifts out of the training support.
        """
        if self._train_feature_cols is None or len(self._train_feature_cols) == 0:
            return
        for i, name in enumerate(FEATURE_NAMES):
            expected = self._train_feature_cols[:, i]
            actual = np.array([x_vec[i]], dtype=np.float64)
            psi = self._psi(expected, actual)
            metrics.set_gauge("feature_psi", psi, labels={"feature": name})

    # -- Prediction ---------------------------------------------------------

    def predict(self, instrument):
        with self.lock:
            buf = self.feature_buffer.get(instrument)
            event_time, values = (None, None)
            if buf:
                event_time, values, _ = buf[-1]

            if self.model is None or values is None:
                return {
                    "instrument": instrument,
                    "confidence": 0.5,
                    "model_ready": False,
                    "model_version": self.model_version,
                    "features_used": {},
                }

            feat_dict = extract_features(values, event_time)
            x_vec = features_to_array(feat_dict)
            X = x_vec.reshape(1, -1)
            proba = self.model.predict_proba(X)[0]
            confidence = float(proba[1])  # probability of class 1 (profitable)
            self.total_predictions += 1
            self._record_confidence(confidence)
            self._update_feature_psi(x_vec)

            return {
                "instrument": instrument,
                "confidence": round(confidence, 4),
                "model_ready": True,
                "model_version": self.model_version,
                "features_used": feat_dict,
            }

    # -- Status -------------------------------------------------------------

    def get_status(self):
        with self.lock:
            total = len(self.samples)
            labeled = sum(1 for s in self.samples if s["label"] is not None)
            pending = total - labeled

            return {
                "model_state": self.state,
                "model_version": self.model_version,
                "total_samples": total,
                "labeled_samples": labeled,
                "pending_samples": pending,
                "accuracy": self.accuracy,
                "feature_importance": self.feature_importance,
                "feature_schema_hash": self.schema_hash,
                "min_samples_required": MIN_SAMPLES,
                "retrain_interval": RETRAIN_INTERVAL,
                "samples_until_retrain": max(
                    0, RETRAIN_INTERVAL - self.labeled_since_last_train
                ),
                "hyperparameters": {
                    "max_depth": XGB_MAX_DEPTH,
                    "n_estimators": XGB_N_ESTIMATORS,
                    "learning_rate": XGB_LEARNING_RATE,
                },
            }

    def get_metrics(self):
        with self.lock:
            return {
                "precision": self.precision,
                "recall": self.recall,
                "f1": self.f1,
                "accuracy": self.accuracy,
                "total_predictions": self.total_predictions,
                "last_trained": self.last_trained,
                "model_state": self.state,
                "model_version": self.model_version,
            }


# ---------------------------------------------------------------------------
# Global model manager
# ---------------------------------------------------------------------------
manager = ModelManager()


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------
class HuginnAIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/predict":
            instrument = params.get("instrument", [""])[0]
            if not instrument:
                self._json_response({"error": "instrument query param required"}, 400)
                return
            self._json_response(manager.predict(instrument))

        elif path == "/api/model/status":
            self._json_response(manager.get_status())

        elif path == "/api/model/metrics":
            self._json_response(manager.get_metrics())

        elif path == "/metrics":
            self._text_response(metrics.render_prometheus())

        elif path == "/healthz" or path == "/readyz":
            ok, detail = liveness.status()
            self._json_response(
                {
                    "status": "ok" if ok else "degraded",
                    "service": "huginn-ai",
                    "consumers": detail,
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

    def _text_response(self, text):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Access-Control-Allow-Origin", ACCESS_CONTROL_ALLOW_ORIGIN)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress per-request logs


# ---------------------------------------------------------------------------
# Kafka consumers
# ---------------------------------------------------------------------------
def _connect_consumer(topics, group_id, retries=30, delay=2):
    for attempt in range(1, retries + 1):
        try:
            # Raw-bytes deserialization: JSON decode happens per-message in the
            # consume loops so one poison record is isolated (counter + DLQ)
            # instead of raising out of poll() and stalling the whole batch.
            consumer = KafkaConsumer(
                *topics,
                bootstrap_servers=KAFKA_BROKERS,
                group_id=group_id,
                auto_offset_reset="earliest",
                consumer_timeout_ms=1000,
            )
            log.info(
                "Connected to Kafka (group=%s), consuming %s",
                group_id, ", ".join(topics),
            )
            return consumer
        except KafkaConnectionError:
            log.warning(
                "Kafka not ready (attempt %d/%d), retrying in %ds...",
                attempt, retries, delay,
            )
            time.sleep(delay)

    log.error("Failed to connect to Kafka after %d attempts", retries)
    return None


def _decode_or_dlq(msg, dlq_producer, dlq_topic, counter_name):
    """Decode a message's JSON value, isolating poison records.

    Returns the decoded value or None. On failure increments `counter_name`,
    logs, and republishes the raw bytes to `dlq_topic` (best-effort).
    """
    try:
        return json.loads(msg.value.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError) as de:
        metrics.inc(counter_name)
        log.warning("Dropping undecodable record (%s): %s", dlq_topic, de)
        if dlq_producer is not None:
            try:
                dlq_producer.send(dlq_topic, value=msg.value)
            except Exception as pe:  # pragma: no cover
                metrics.inc("dlq_publish_failure_total")
                log.warning("DLQ publish failed: %s", pe)
        return None


def consume_features():
    consumer = _connect_consumer([FEATURES_TOPIC], CONSUMER_GROUP)
    if consumer is None:
        return

    dlq_producer = _make_dlq_producer()
    liveness.register("features")

    while not shutdown:
        try:
            records = consumer.poll(timeout_ms=1000)
            liveness.beat("features")
            for tp, messages in records.items():
                for msg in messages:
                    event = _decode_or_dlq(
                        msg, dlq_producer, FEATURES_DLQ_TOPIC,
                        "features_decode_failure_total",
                    )
                    if event is None or not isinstance(event, dict):
                        if event is not None:
                            metrics.inc("features_decode_failure_total")
                        continue
                    instrument = event.get("instrument", "")
                    event_time = event.get("eventTime", "")
                    event_id = event.get("eventId", "")
                    values = event.get("values", {})
                    ok, reason = validate_feature_event(
                        instrument, event_time, values
                    )
                    if not ok:
                        metrics.inc("features_rejected_total")
                        metrics.inc(f"features_rejected_{reason}")
                        continue
                    manager.store_features(
                        instrument, event_time, values, event_id
                    )
        except Exception as e:
            log.error("Feature consumer error: %s", e)
            time.sleep(1)

    consumer.close()
    if dlq_producer is not None:
        dlq_producer.close()


def consume_fills():
    consumer = _connect_consumer([FILLS_TOPIC], CONSUMER_GROUP)
    if consumer is None:
        return

    dlq_producer = _make_dlq_producer()
    liveness.register("fills")

    while not shutdown:
        try:
            records = consumer.poll(timeout_ms=1000)
            liveness.beat("fills")
            for tp, messages in records.items():
                for msg in messages:
                    fill = _decode_or_dlq(
                        msg, dlq_producer, FILLS_DLQ_TOPIC,
                        "fills_decode_failure_total",
                    )
                    if fill is None:
                        continue
                    manager.add_fill(fill)
                    if isinstance(fill, dict):
                        log.info(
                            "Fill: %s %s %s @ $%.2f (fee: $%.4f)",
                            fill.get("side", "?"),
                            fill.get("quantity", 0),
                            fill.get("instrument", "?"),
                            float(fill.get("fill_price", 0) or 0),
                            float(fill.get("transaction_cost", 0) or 0),
                        )
        except Exception as e:
            log.error("Fill consumer error: %s", e)
            time.sleep(1)

    consumer.close()
    if dlq_producer is not None:
        dlq_producer.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("=" * 60)
    log.info("  HUGINN-AI -- XGBoost Signal Quality Predictor")
    log.info("=" * 60)
    log.info("  Features topic:  %s", FEATURES_TOPIC)
    log.info("  Fills topic:     %s", FILLS_TOPIC)
    log.info("  API port:        %d", PORT)
    log.info("  Consumer group:  %s", CONSUMER_GROUP)
    log.info("  Kafka:           %s", KAFKA_BROKERS)
    log.info("  Min samples:     %d", MIN_SAMPLES)
    log.info("  Retrain every:   %d new labeled samples", RETRAIN_INTERVAL)
    log.info("  Model dir:       %s", MODEL_DIR)
    log.info("  Schema hash:     %s", manager.schema_hash)
    log.info("  XGB max_depth:   %d", XGB_MAX_DEPTH)
    log.info("  XGB estimators:  %d", XGB_N_ESTIMATORS)
    log.info("  XGB learn rate:  %s", XGB_LEARNING_RATE)
    log.info("  Endpoints:       /api/predict, /api/model/status, "
             "/api/model/metrics, /metrics")
    log.info("=" * 60)

    # Load a persisted model on boot (best-effort).
    manager.load_persisted()

    # Start Kafka consumers in background threads
    features_thread = threading.Thread(target=consume_features, daemon=True)
    features_thread.start()

    fills_thread = threading.Thread(target=consume_fills, daemon=True)
    fills_thread.start()

    # Start HTTP server
    server = HTTPServer(("0.0.0.0", PORT), HuginnAIHandler)
    server.timeout = 1
    log.info("Huginn-AI HTTP server listening on :%d", PORT)

    while not shutdown:
        server.handle_request()

    server.server_close()
    log.info("Huginn-AI shutdown complete")


if __name__ == "__main__":
    main()
