#!/usr/bin/env python3
"""
Forseti — Execution Transaction-Cost-Analysis (TCA) Monitor.

Named for the Norse god of justice and reconciliation, Forseti adjudicates the
true cost of execution. It consumes the live fills topic (executions.fills.v1)
and, optionally, a realtime price feed (prices.realtime.v1) for an arrival/mid
benchmark, then decomposes every fill into its cost components:

  - fees (the reported transaction_cost) and fee_bps (fees / notional * 1e4)
  - slippage_bps: prefers the fill's own reported slippage_bps; else, if a
    price feed gave an arrival/mid m at-or-just-before the fill, computes the
    signed adverse slippage; else null (NEVER fabricated)
  - liquidity flag: maker vs taker (from the fill's liquidity field)
  - implementation_shortfall: slippage_cost + fees on the traded notional

Aggregates per-instrument and overall: avgSlippageBps (over defined fills),
totalFees, totalNotional, totalFills, maker/taker counts + makerTakerRatio,
avgFeeBps, totalImplementationShortfall.

CRITICAL HONESTY RULE: Forseti never invents a benchmark. If no arrival price
is available for a fill and the fill carries no reported slippage, slippage_bps
is reported as null and the analysis basis is labelled
"fees + reported-slippage only".

No API key required. Runs as a Docker service alongside the Norse Stack.
Modelled on services/odin/odin.py: same stdlib http.server + KafkaConsumer +
lock-guarded tracker + JSON/CORS patterns.
"""

import json
import logging
import math
import os
import signal
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from kafka import KafkaConsumer
from kafka.errors import KafkaConnectionError

KAFKA_BROKERS = os.environ.get("KAFKA_BROKERS", "redpanda:29092")
FILLS_TOPIC = os.environ.get("FILLS_TOPIC", "executions.fills.v1")
PRICES_TOPIC = os.environ.get("PRICES_TOPIC", "prices.realtime.v1")
# Optionally consume the realtime price feed for an arrival/mid benchmark. Off
# by default so Forseti degrades cleanly to "fees + reported-slippage only" in
# deployments where the price feed is absent — and so unit tests never need it.
PRICES_ENABLED = os.environ.get("PRICES_ENABLED", "false").lower() in (
    "1", "true", "yes",
)
PORT = int(os.environ.get("FORSETI_PORT", "8096"))

# CORS: default to "*" to match Odin's behaviour, but allow locking the allowed
# origin down to a single configured value in hardened deployments.
ACCESS_CONTROL_ALLOW_ORIGIN = os.environ.get("ACCESS_CONTROL_ALLOW_ORIGIN", "*")

# Liveness: the consumer thread stamps a heartbeat each poll cycle. /healthz
# returns 503 once the heartbeat is older than this many seconds, so a wedged
# consumer thread is detectable even while the HTTP server stays up. Generous
# default avoids flapping on an idle (but healthy) stream.
HEALTH_MAX_STALENESS_SECS = float(os.environ.get("HEALTH_MAX_STALENESS_SECS", "120"))

# How far back (seconds) an arrival/mid price may be and still benchmark a fill.
# A price older than this is treated as no benchmark (slippage falls back to the
# reported field or null) rather than silently using a stale mark.
ARRIVAL_MAX_AGE_SECS = float(os.environ.get("ARRIVAL_MAX_AGE_SECS", "60"))

# Recent per-fill TCA records retained for /api/tca/fills.
FILLS_HISTORY_MAX = int(os.environ.get("FILLS_HISTORY_MAX", "5000"))

# The two analysis bases Forseti reports. Which one is in force depends on
# whether ANY fill in the window was benchmarked against an arrival price.
BASIS_WITH_ARRIVAL = "fees + arrival/mid slippage where available"
BASIS_NO_ARRIVAL = "fees + reported-slippage only"

# ---------------------------------------------------------------------------
# MARKET-IMPACT + CAPACITY model configuration.
#
# This block extends Forseti from pure post-trade TCA into a pre-trade cost /
# capacity model: the square-root law for temporary impact, the Almgren-Chriss
# permanent+temporary decomposition with its closed-form optimal-execution
# trajectory, and a capacity estimate (the size at which modelled impact eats an
# ASSUMED per-trade edge). Everything is pure stdlib math (no numpy).
#
# HONESTY: the capacity number depends on an assumed edge. THIS SIMULATION HAS
# NO MEASURED OUT-OF-SAMPLE EDGE (walk-forward PBO = 1.0). Any edge fed in is
# illustrative only; the capacity it implies is a demonstration of technique,
# never a claim of realisable AUM or profit.
# ---------------------------------------------------------------------------

# Square-root-law temporary-impact coefficient (dimensionless, ~1 empirically).
#   temporary_impact_bps = eta * sigma * sqrt(Q / ADV) * 1e4
# where sigma is DAILY volatility as a fraction, Q and ADV share units (both a
# notional in quote currency, or both a share/base quantity), so Q/ADV is the
# dimensionless participation rate.
IMPACT_ETA = float(os.environ.get("IMPACT_ETA", "1.0"))

# Permanent-impact coefficient (dimensionless). Permanent impact is LINEAR in
# participation (Almgren-Chriss / Kyle-lambda style), typically a smaller
# coefficient than the temporary sqrt term:
#   permanent_impact_bps = gamma * sigma * (Q / ADV) * 1e4
IMPACT_GAMMA = float(os.environ.get("IMPACT_GAMMA", "0.1"))

# Almgren-Chriss OPTIMAL-EXECUTION trajectory coefficients. These shape the
# child-order schedule (the sinh curve), not the impact-vs-size curve above.
# The schedule works in slice-time units (tau = 1 slice, horizon T = N slices);
# the decay rate kappa is derived from the closed form
#   cosh(kappa*tau) = 1 + (lambda * sigma^2 / eta_tilde) * tau^2 / 2,
#   eta_tilde = AC_ETA - AC_GAMMA*tau/2.
# lambda (risk aversion) is a request parameter; AC_ETA/AC_GAMMA are structural.
AC_ETA = float(os.environ.get("AC_ETA", "0.01"))
AC_GAMMA = float(os.environ.get("AC_GAMMA", "0.001"))
AC_TAU = float(os.environ.get("AC_TAU", "1.0"))
AC_DEFAULT_LAMBDA = float(os.environ.get("AC_DEFAULT_LAMBDA", "1.0"))
# Cap on kappa*T so sinh() never overflows a float (sinh(700) ~ 1e304).
KAPPA_T_CAP = float(os.environ.get("KAPPA_T_CAP", "600.0"))

# ASSUMED per-trade edge for the capacity crossover, in bps. CONFIGURABLE and
# clearly labelled: this simulation measured NO out-of-sample edge, so this is
# an input assumption, never an observed quantity.
DEFAULT_EDGE_BPS = float(os.environ.get("FORSETI_DEFAULT_EDGE_BPS", "10.0"))

# Fallback ADV / sigma / trade-size when Forseti has too little history to
# estimate them from its own fill stream. Illustrative placeholders.
DEFAULT_ADV = float(os.environ.get("FORSETI_DEFAULT_ADV", "1.0e8"))
DEFAULT_SIGMA = float(os.environ.get("FORSETI_DEFAULT_SIGMA", "0.02"))
DEFAULT_SIZE = float(os.environ.get("FORSETI_DEFAULT_SIZE", "1.0e6"))
DEFAULT_INSTRUMENT = os.environ.get("FORSETI_DEFAULT_INSTRUMENT", "BTC-USDT")

# Estimation guards.
SIGMA_MIN = float(os.environ.get("FORSETI_SIGMA_MIN", "1.0e-4"))
SIGMA_MAX = float(os.environ.get("FORSETI_SIGMA_MAX", "2.0"))
ADV_MIN = float(os.environ.get("FORSETI_ADV_MIN", "1.0"))
MIN_SPAN_DAYS = float(os.environ.get("FORSETI_MIN_SPAN_DAYS", str(1.0 / 24)))
MIN_ESTIMATE_SAMPLES = int(os.environ.get("FORSETI_MIN_ESTIMATE_SAMPLES", "3"))
PX_SERIES_MAX = int(os.environ.get("FORSETI_PX_SERIES_MAX", "5000"))

IMPACT_MODEL_NAME = "sqrt-law + Almgren-Chriss"

# The mandatory honesty label attached to every edge-dependent number.
EDGE_DISCLAIMER = (
    "illustrative; this simulation has no measured out-of-sample edge (PBO=1.0)"
)


def assumed_edge_label(edge_bps):
    """Return the mandatory honesty label for an assumed-edge figure."""
    return "assumed edge = {} bps ({})".format(edge_bps, EDGE_DISCLAIMER)


# -- pure impact math (stdlib only) ------------------------------------------

def sqrt_law_temporary_bps(size, adv, sigma, eta=IMPACT_ETA):
    """Square-root-law temporary impact, in bps.

    impact_bps = eta * sigma * sqrt(size / adv) * 1e4

    size and adv must share units (both notional, or both shares); sigma is the
    daily volatility as a fraction. Concave (sqrt) in size: a 4x size gives ~2x
    impact. Returns 0.0 for a non-positive size/adv (no trade, no impact).
    """
    if size <= 0 or adv <= 0 or sigma <= 0:
        return 0.0
    return eta * sigma * math.sqrt(size / adv) * 1e4


def permanent_bps(size, adv, sigma, gamma=IMPACT_GAMMA):
    """Permanent impact, in bps. LINEAR in participation (Almgren-Chriss).

    impact_bps = gamma * sigma * (size / adv) * 1e4
    """
    if size <= 0 or adv <= 0 or sigma <= 0:
        return 0.0
    return gamma * sigma * (size / adv) * 1e4


def total_impact_bps(size, adv, sigma, eta=IMPACT_ETA, gamma=IMPACT_GAMMA):
    """Total modelled impact = temporary (sqrt) + permanent (linear), in bps."""
    return (
        sqrt_law_temporary_bps(size, adv, sigma, eta)
        + permanent_bps(size, adv, sigma, gamma)
    )


def almgren_chriss_kappa(sigma, risk_aversion, tau=AC_TAU,
                         ac_eta=AC_ETA, ac_gamma=AC_GAMMA):
    """Almgren-Chriss trajectory decay rate kappa (per unit time).

    Closed form: cosh(kappa*tau) = 1 + (lambda * sigma^2 / eta_tilde)*tau^2/2,
    eta_tilde = ac_eta - ac_gamma*tau/2. As lambda -> 0, kappa -> 0 (the schedule
    reduces to uniform / TWAP); as lambda rises, kappa rises (front-loading).
    """
    if risk_aversion <= 0 or sigma <= 0 or tau <= 0:
        return 0.0
    eta_tilde = ac_eta - ac_gamma * tau / 2.0
    if eta_tilde <= 0:
        # Degenerate config: fall back to the raw temporary coefficient so the
        # decay rate stays finite and positive rather than blowing up.
        eta_tilde = ac_eta if ac_eta > 0 else 1.0
    kappa_tilde_sq = risk_aversion * sigma * sigma / eta_tilde
    c = 1.0 + kappa_tilde_sq * tau * tau / 2.0
    if c <= 1.0:
        return 0.0
    return math.acosh(c) / tau


def almgren_chriss_schedule(size, slices, risk_aversion, sigma,
                            tau=AC_TAU, ac_eta=AC_ETA, ac_gamma=AC_GAMMA):
    """Closed-form Almgren-Chriss optimal-execution child-order sizes.

    Minimises E[cost] + lambda*Var[cost]. The inventory trajectory is
        x_j = size * sinh(kappa*(T - t_j)) / sinh(kappa*T),  t_j = j*tau,
    and the child order for slice j is n_j = x_{j-1} - x_j. Returns
    (child_sizes, kappa). Child sizes always sum to `size`. Front-loaded for
    lambda > 0 (largest child first); uniform (TWAP) as lambda -> 0.
    """
    n = max(1, int(slices))
    if size <= 0:
        return [0.0] * n, 0.0
    kappa = almgren_chriss_kappa(sigma, risk_aversion, tau, ac_eta, ac_gamma)
    T = n * tau
    kT = kappa * T
    if kT < 1e-9:
        # TWAP limit: sinh(kappa*(T-t))/sinh(kappa*T) -> (T-t)/T. Equal children.
        return [size / n] * n, kappa
    if kT > KAPPA_T_CAP:
        # Clamp to keep sinh() inside float range; the schedule is already
        # essentially "sell everything in the first slice" at this point.
        kappa = KAPPA_T_CAP / T
    sinh_kT = math.sinh(kappa * T)
    x = [size * math.sinh(kappa * (T - j * tau)) / sinh_kT for j in range(n + 1)]
    # x[0] == size, x[n] == 0 (up to rounding); children are the differences.
    children = [x[j - 1] - x[j] for j in range(1, n + 1)]
    # Renormalise away any floating rounding so children sum EXACTLY to size.
    s = sum(children)
    if s > 0:
        children = [c * size / s for c in children]
    return children, kappa


def capacity_notional(edge_bps, adv, sigma, eta=IMPACT_ETA, gamma=IMPACT_GAMMA):
    """Crossover size Q* where total_impact_bps(Q*) == edge_bps.

    Beyond Q* the modelled round-trip impact exceeds the assumed edge, so extra
    size erodes (then destroys) the edge. Closed form: with participation
    p = Q/ADV, a = eta*sigma*1e4, b = gamma*sigma*1e4, solve
        a*sqrt(p) + b*p = edge  ->  b*u^2 + a*u - edge = 0  (u = sqrt(p)).
    Returns the notional Q* (same units as ADV), or None if unsolvable.
    """
    if edge_bps <= 0 or adv <= 0 or sigma <= 0:
        return None
    a = eta * sigma * 1e4
    b = gamma * sigma * 1e4
    if a <= 0 and b <= 0:
        return None
    if b <= 1e-15:
        # Pure sqrt law: a*sqrt(p) = edge.
        u = edge_bps / a
    else:
        disc = a * a + 4.0 * b * edge_bps
        u = (-a + math.sqrt(disc)) / (2.0 * b)
    if u <= 0:
        return None
    return (u * u) * adv


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("forseti")

shutdown = False


class Counters:
    """Lightweight, thread-safe, Prometheus-style monotonic counters.

    Forseti ships stdlib-only (see Dockerfile), so we don't pull in
    prometheus_client. These counters are exposed via /metrics and /healthz so
    operational issues (parse failures, rejected events, duplicate fills) are
    observable instead of silently swallowed.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counts = defaultdict(int)

    def inc(self, name, amount=1):
        with self._lock:
            self._counts[name] += amount

    def snapshot(self):
        with self._lock:
            return dict(self._counts)

    def render_prometheus(self):
        lines = []
        for name, value in sorted(self.snapshot().items()):
            metric = f"forseti_{name}"
            lines.append(f"# TYPE {metric} counter")
            lines.append(f"{metric} {value}")
        return "\n".join(lines) + "\n"


counters = Counters()


class Liveness:
    """Tracks the consumer thread's last-progress timestamp.

    The consumer stamps `beat()` once per poll cycle (whether or not a message
    arrived) so /healthz can distinguish a live-but-idle loop from a wedged
    one. `started` flips true after the consumer connects; until then /healthz
    reports healthy so container startup isn't failed closed during Kafka
    connect/retry.
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


def handle_signal(signum, frame):
    global shutdown
    shutdown = True
    log.info("Shutdown signal received")


def _parse_ts(ts):
    """Parse an ISO-8601 timestamp. Returns a datetime or None.

    On failure, increments a parse-failure counter so the problem is observable
    rather than silently swallowed (matches odin.PerformanceTracker._parse_ts).
    """
    if not isinstance(ts, str):
        counters.inc("timestamp_parse_failure_total")
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        counters.inc("timestamp_parse_failure_total")
        return None


class TCATracker:
    """Lock-guarded transaction-cost-analysis projection over the fills topic.

    Mirrors odin.PerformanceTracker's concurrency model: every mutation and
    every read snapshot happens under a single lock, and the fills consumer is
    a dedup-based full-topic replay so a restart is idempotent.
    """

    # Liquidity tokens, normalized to the {maker, taker} flag. The live fills
    # may carry the flag in several shapes (a string "maker"/"taker", a venue
    # code "M"/"T", or a boolean is_maker / is_buyer_maker), so we normalize
    # defensively rather than assuming one source's convention.
    _MAKER_TOKENS = {"maker", "m", "added", "add", "post", "passive", "true", "1"}
    _TAKER_TOKENS = {"taker", "t", "removed", "remove", "aggressive", "active",
                     "false", "0"}

    def __init__(self):
        self.lock = threading.Lock()

        # Recent per-fill TCA records, newest appended last (newest-first on
        # read). Bounded so memory stays flat over a long-lived stream.
        self.fill_tca = deque(maxlen=FILLS_HISTORY_MAX)

        # Per-instrument running aggregates. Sums are kept incrementally so a
        # read is O(instruments), not O(fills).
        self.agg = defaultdict(self._new_agg)
        # Overall running aggregate (same shape).
        self.overall = self._new_agg()

        # Last arrival/mid price per instrument from the price feed, with its
        # timestamp, used to benchmark fills that carry no reported slippage.
        # {instrument: (price, datetime)}.
        self.last_arrival = {}

        # Dedup: like Odin, Forseti replays the ENTIRE fills topic from the
        # beginning on every start (see _make_fills_consumer), so without dedup
        # a restart would double-count every fill. Dedup on execution_id with a
        # composite-key fallback and a bounded seen-set so the replay is
        # idempotent / restart-safe and memory stays flat.
        self._seen_ids = deque(maxlen=50000)
        self._seen_set = set()

        # Per-instrument price/notional series, used by the impact/capacity
        # model to estimate daily volatility (sigma) and average daily volume
        # (ADV) from Forseti's own fill stream. Bounded so memory stays flat.
        # Each entry: (fill_dt_or_None, fill_price, notional).
        self.px_series = defaultdict(lambda: deque(maxlen=PX_SERIES_MAX))

        # Latest fill timestamp seen, surfaced as asOf.
        self._as_of = None

        # True once at least one fill in the window was benchmarked against an
        # arrival/mid price; controls which analysis basis we report.
        self._any_arrival_benchmark = False

    @staticmethod
    def _new_agg():
        return {
            "fills": 0,
            "fees": 0.0,
            "notional": 0.0,
            "fee_bps_weighted": 0.0,   # sum(fee_bps * notional) for notional-wt avg
            "slippage_bps_sum": 0.0,   # sum over fills where slippage is defined
            "slippage_defined": 0,     # count of fills with a defined slippage_bps
            "maker": 0,
            "taker": 0,
            "implementation_shortfall": 0.0,
        }

    # -- field extraction (tolerant of PascalCase and snake_case) -------------

    @staticmethod
    def _get(fill, *names, default=None):
        """First present value among `names` (handles snake_case/PascalCase)."""
        for n in names:
            if n in fill and fill[n] is not None:
                return fill[n]
        return default

    @classmethod
    def _normalize_side(cls, raw):
        """Normalize a side to 'BUY'/'SELL'. Accepts strings and FIX 0/1 codes.

        FIX convention (per the lane spec): 0 = BUY, 1 = SELL.
        """
        if raw is None:
            return None
        s = str(raw).strip().upper()
        if s in ("BUY", "B", "0"):
            return "BUY"
        if s in ("SELL", "S", "1"):
            return "SELL"
        return None

    @classmethod
    def _normalize_liquidity(cls, raw):
        """Normalize a liquidity flag to 'maker' / 'taker' / None.

        Returns None when the field is absent or unrecognized — an unknown
        liquidity flag must NOT be silently bucketed as taker (which would skew
        the maker/taker ratio); it is simply not counted in either bucket.
        """
        if raw is None:
            return None
        s = str(raw).strip().lower()
        if s in cls._MAKER_TOKENS:
            return "maker"
        if s in cls._TAKER_TOKENS:
            return "taker"
        return None

    # -- price feed -----------------------------------------------------------

    def add_price(self, tick):
        """Record an arrival/mid price tick for benchmarking later fills.

        The price feed (prices.realtime.v1) carries {instrument, price,
        timestamp, ...}; some producers may instead expose a mid under
        values.midPrice. We accept either and keep the most recent per
        instrument under the lock.
        """
        if not isinstance(tick, dict):
            counters.inc("prices_rejected_total")
            return
        instrument = self._get(tick, "instrument", "Instrument")
        price = self._get(tick, "price", "Price", "mid", "midPrice")
        if price is None:
            values = tick.get("values")
            if isinstance(values, dict):
                price = values.get("midPrice")
        if not instrument or price is None:
            counters.inc("prices_rejected_total")
            return
        try:
            price = float(price)
        except (TypeError, ValueError):
            counters.inc("prices_rejected_total")
            return
        if price <= 0 or math.isnan(price) or math.isinf(price):
            counters.inc("prices_rejected_total")
            return
        ts = self._get(tick, "timestamp", "Timestamp")
        dt = _parse_ts(ts) if ts is not None else datetime.now(timezone.utc)
        if dt is None:
            dt = datetime.now(timezone.utc)
        with self.lock:
            self.last_arrival[instrument] = (price, dt)
            counters.inc("prices_processed_total")

    def _arrival_for(self, instrument, fill_dt):
        """Return a usable arrival price for an instrument, or None. Lock held.

        Only returns a price recorded at-or-just-before the fill and no older
        than ARRIVAL_MAX_AGE_SECS, so a stale mark never benchmarks a fill.
        """
        rec = self.last_arrival.get(instrument)
        if rec is None:
            return None
        price, p_dt = rec
        if fill_dt is None or p_dt is None:
            return None
        age = (fill_dt - p_dt).total_seconds()
        if age < 0:
            # Price is AFTER the fill: not an arrival benchmark for this fill.
            return None
        if age > ARRIVAL_MAX_AGE_SECS:
            return None
        return price

    # -- validation -----------------------------------------------------------

    def _validate_fill(self, fill):
        """Validate an incoming fill. Returns (ok, reason).

        Rejects events missing required fields or carrying NaN/Inf/non-positive
        numerics, which would otherwise poison every downstream aggregate.
        Mirrors odin.PerformanceTracker._validate_fill.
        """
        if not isinstance(fill, dict):
            return False, "not_a_dict"

        instrument = self._get(fill, "instrument", "Instrument")
        if not instrument or not isinstance(instrument, str):
            return False, "missing_instrument"

        if self._normalize_side(self._get(fill, "side", "Side")) is None:
            return False, "bad_side"

        qty_raw = self._get(fill, "quantity", "Quantity")
        price_raw = self._get(fill, "fill_price", "FillPrice")
        if qty_raw is None:
            return False, "missing_quantity"
        if price_raw is None:
            return False, "missing_fill_price"

        try:
            qty = float(qty_raw)
            price = float(price_raw)
            fee = float(self._get(fill, "transaction_cost", "TransactionCost",
                                  default=0) or 0)
        except (TypeError, ValueError):
            return False, "non_numeric"

        for name, val in (("quantity", qty), ("fill_price", price), ("fee", fee)):
            if math.isnan(val) or math.isinf(val):
                return False, f"nan_inf_{name}"

        if qty <= 0 or price <= 0:
            return False, "non_positive"

        return True, None

    def _dedup_key(self, fill):
        """Return a dedup key for a fill (execution_id, composite fallback)."""
        exec_id = self._get(fill, "execution_id", "ExecutionID", "ExecutionId")
        if not exec_id:
            exec_id = "{}|{}|{}|{}|{}".format(
                self._get(fill, "order_id", "OrderID", default=""),
                self._get(fill, "timestamp", "Timestamp", default=""),
                self._get(fill, "side", "Side", default=""),
                self._get(fill, "quantity", "Quantity", default=""),
                self._get(fill, "fill_price", "FillPrice", default=""),
            )
        return exec_id

    # -- core TCA -------------------------------------------------------------

    def add_fill(self, fill):
        """Validate, dedup, compute per-fill TCA, and fold into aggregates."""
        with self.lock:
            ok, reason = self._validate_fill(fill)
            if not ok:
                counters.inc("fills_rejected_total")
                counters.inc(f"fills_rejected_{reason}")
                log.warning("Rejected fill (%s): %r", reason, fill)
                return

            key = self._dedup_key(fill)
            if key in self._seen_set:
                counters.inc("fills_duplicate_total")
                return
            self._seen_set.add(key)
            if len(self._seen_ids) == self._seen_ids.maxlen:
                self._seen_set.discard(self._seen_ids[0])
            self._seen_ids.append(key)

            instrument = self._get(fill, "instrument", "Instrument")
            side = self._normalize_side(self._get(fill, "side", "Side"))
            qty = float(self._get(fill, "quantity", "Quantity"))
            price = float(self._get(fill, "fill_price", "FillPrice"))
            fee = float(self._get(fill, "transaction_cost", "TransactionCost",
                                  default=0) or 0)
            reported_slip = self._get(fill, "slippage_bps", "SlippageBps")
            liquidity = self._normalize_liquidity(
                self._get(fill, "liquidity", "Liquidity")
            )
            ts = self._get(fill, "timestamp", "Timestamp")
            fill_dt = _parse_ts(ts) if ts is not None else None

            notional = price * qty
            fee_bps = (fee / notional * 1e4) if notional > 0 else None

            # --- slippage_bps: prefer the reported field, else arrival-based,
            #     else null. NEVER fabricate a benchmark. ---------------------
            slippage_bps = None
            slippage_source = None
            try:
                rs = float(reported_slip) if reported_slip is not None else 0.0
            except (TypeError, ValueError):
                rs = 0.0
            if not (math.isnan(rs) or math.isinf(rs)) and rs != 0.0:
                slippage_bps = rs
                slippage_source = "reported"
            else:
                arrival = self._arrival_for(instrument, fill_dt)
                if arrival is not None and arrival > 0:
                    # sign = +1 for BUY, -1 for SELL. A BUY filled ABOVE the
                    # arrival/mid is adverse (positive); a SELL filled BELOW the
                    # arrival/mid is adverse (positive). The sign factor makes
                    # both adverse cases positive and both favourable cases
                    # negative.
                    sign = 1.0 if side == "BUY" else -1.0
                    slippage_bps = sign * (price - arrival) / arrival * 1e4
                    slippage_source = "arrival"
                    self._any_arrival_benchmark = True
                # else: no reported slip and no arrival -> remains None (null).

            # Slippage cost in quote currency on the traded notional; only
            # defined when slippage_bps is. Implementation shortfall = slippage
            # cost + fees on the notional. With no slippage benchmark, the
            # shortfall is fees-only (and labelled as such via the basis).
            if slippage_bps is not None:
                slippage_cost = slippage_bps / 1e4 * notional
            else:
                slippage_cost = 0.0
            implementation_shortfall = slippage_cost + fee

            record = {
                "instrument": instrument,
                "side": side,
                "quantity": qty,
                "fill_price": price,
                "notional": round(notional, 2),
                "fees": round(fee, 6),
                "fee_bps": round(fee_bps, 4) if fee_bps is not None else None,
                "slippage_bps": (
                    round(slippage_bps, 4) if slippage_bps is not None else None
                ),
                "slippage_source": slippage_source,
                "slippage_cost": round(slippage_cost, 6),
                "liquidity": liquidity,
                "implementation_shortfall": round(implementation_shortfall, 6),
                "timestamp": ts,
            }
            self.fill_tca.append(record)
            self.px_series[instrument].append((fill_dt, price, notional))
            counters.inc("fills_processed_total")
            if ts and (self._as_of is None or str(ts) > self._as_of):
                self._as_of = str(ts)

            # Fold into per-instrument and overall aggregates.
            self._fold(self.agg[instrument], notional, fee, fee_bps,
                       slippage_bps, liquidity, implementation_shortfall)
            self._fold(self.overall, notional, fee, fee_bps,
                       slippage_bps, liquidity, implementation_shortfall)

    @staticmethod
    def _fold(agg, notional, fee, fee_bps, slippage_bps, liquidity, shortfall):
        agg["fills"] += 1
        agg["fees"] += fee
        agg["notional"] += notional
        if fee_bps is not None:
            agg["fee_bps_weighted"] += fee_bps * notional
        if slippage_bps is not None:
            agg["slippage_bps_sum"] += slippage_bps
            agg["slippage_defined"] += 1
        if liquidity == "maker":
            agg["maker"] += 1
        elif liquidity == "taker":
            agg["taker"] += 1
        agg["implementation_shortfall"] += shortfall

    @staticmethod
    def _summarize(agg):
        """Render a running aggregate into the public summary shape."""
        fills = agg["fills"]
        notional = agg["notional"]
        slip_n = agg["slippage_defined"]
        maker = agg["maker"]
        taker = agg["taker"]
        return {
            "totalFills": fills,
            "totalFees": round(agg["fees"], 6),
            "totalNotional": round(notional, 2),
            # Notional-weighted average fee in bps (a fee on a big fill should
            # weigh more than the same bps on a tiny one). Null when no notional.
            "avgFeeBps": (
                round(agg["fee_bps_weighted"] / notional, 4)
                if notional > 0 else None
            ),
            # Simple mean over only the fills where slippage is DEFINED; null
            # when none are (never a fabricated 0).
            "avgSlippageBps": (
                round(agg["slippage_bps_sum"] / slip_n, 4) if slip_n > 0 else None
            ),
            "slippageDefinedFills": slip_n,
            "makerCount": maker,
            "takerCount": taker,
            # maker / taker; null when no takers (ratio undefined) and there are
            # makers, else 0.0 when nothing is classified. Avoids a fabricated
            # infinity from a zero denominator.
            "makerTakerRatio": (
                round(maker / taker, 4) if taker > 0
                else (None if maker > 0 else 0.0)
            ),
            "totalImplementationShortfall": round(
                agg["implementation_shortfall"], 6
            ),
        }

    # -- read endpoints -------------------------------------------------------

    def get_tca(self):
        """Snapshot for GET /api/tca."""
        with self.lock:
            available = self.overall["fills"] > 0
            basis = (
                BASIS_WITH_ARRIVAL if self._any_arrival_benchmark
                else BASIS_NO_ARRIVAL
            )
            if not available:
                return {
                    "available": False,
                    "asOf": self._as_of,
                    "basis": basis,
                    "overall": self._summarize(self._new_agg()),
                    "byInstrument": {},
                }
            return {
                "available": True,
                "asOf": self._as_of,
                "basis": basis,
                "overall": self._summarize(self.overall),
                "byInstrument": {
                    inst: self._summarize(agg)
                    for inst, agg in sorted(self.agg.items())
                },
            }

    def get_fills(self, limit=50):
        """Recent per-fill TCA records, newest-first, for GET /api/tca/fills."""
        with self.lock:
            records = list(self.fill_tca)
        recent = records[-limit:] if limit and limit > 0 else records
        return {
            "available": len(records) > 0,
            "count": len(recent),
            "fills": list(reversed(recent)),
        }

    # -- market-impact model estimators --------------------------------------

    def tracked_instruments(self):
        """Instruments Forseti has seen at least one valid fill for, sorted."""
        with self.lock:
            return sorted(self.agg.keys())

    def estimate_sigma(self, instrument):
        """Estimate daily volatility (fraction) for an instrument.

        Uses the sample standard deviation of consecutive fill-to-fill log
        returns, scaled to a daily figure by the median inter-fill gap. Returns
        (sigma, source). Falls back to the documented default (clearly labelled)
        when there is too little history or the series is degenerate.
        """
        with self.lock:
            series = list(self.px_series.get(instrument, ()))
        if len(series) < MIN_ESTIMATE_SAMPLES:
            return DEFAULT_SIGMA, "default (insufficient price history)"
        rets = []
        gaps = []
        prev = None
        for dt, price, _notional in series:
            if price is None or price <= 0:
                prev = None
                continue
            if prev is not None:
                p_dt, p_price = prev
                rets.append(math.log(price / p_price))
                if dt is not None and p_dt is not None:
                    g = (dt - p_dt).total_seconds()
                    if g > 0:
                        gaps.append(g)
            prev = (dt, price)
        if len(rets) < 2:
            return DEFAULT_SIGMA, "default (insufficient returns)"
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        per_fill_sd = math.sqrt(var)
        if per_fill_sd <= 0:
            return DEFAULT_SIGMA, "default (degenerate volatility)"
        if gaps:
            gaps.sort()
            med = gaps[len(gaps) // 2]
            if med > 0:
                sigma = per_fill_sd * math.sqrt(86400.0 / med)
                source = ("estimated-from-fills "
                          "(fill log-return sd scaled to daily)")
            else:
                sigma = per_fill_sd
                source = "estimated-from-fills (unscaled; no usable time gaps)"
        else:
            sigma = per_fill_sd
            source = "estimated-from-fills (unscaled; no usable time gaps)"
        sigma = max(SIGMA_MIN, min(SIGMA_MAX, sigma))
        return sigma, source

    def estimate_adv(self, instrument):
        """Estimate average daily volume (as a quote-notional) for an instrument.

        Scales Forseti's own executed notional to a per-day figure over the
        observed time span. Returns (adv, source). NOTE: this is Forseti's OWN
        traded notional, a LOWER BOUND on true market ADV, so it is labelled as
        such; falls back to the documented default when history is too thin.
        """
        with self.lock:
            agg = self.agg.get(instrument)
            series = list(self.px_series.get(instrument, ()))
            total_notional = agg["notional"] if agg else 0.0
            fills = agg["fills"] if agg else 0
        if fills < MIN_ESTIMATE_SAMPLES or total_notional <= 0:
            return DEFAULT_ADV, "default (insufficient fills)"
        dts = [dt for dt, _p, _n in series if dt is not None]
        span = (max(dts) - min(dts)).total_seconds() if len(dts) >= 2 else 0.0
        span_days = max(span / 86400.0, MIN_SPAN_DAYS)
        adv = max(total_notional / span_days, ADV_MIN)
        return adv, ("estimated-from-fills (own executed notional / observed "
                     "span; lower bound on market ADV)")

    def estimate_trade_notional(self, instrument):
        """Estimate a representative single-order notional for an instrument."""
        with self.lock:
            agg = self.agg.get(instrument)
            notional = agg["notional"] if agg else 0.0
            fills = agg["fills"] if agg else 0
        if fills > 0 and notional > 0:
            return notional / fills, "estimated-from-fills (avg fill notional)"
        return DEFAULT_SIZE, "default"


tracker = TCATracker()


# ---------------------------------------------------------------------------
# Response builders for the impact / capacity endpoints. Kept as pure module
# functions (not handler methods) so unit tests can exercise them directly with
# a seeded tracker and no HTTP server.
# ---------------------------------------------------------------------------

def build_impact_response(tracker, instrument=None, size=None, adv=None,
                          sigma=None, eta=None):
    """Assemble GET /api/impact. Missing params default to tracked estimates."""
    instrument = instrument or DEFAULT_INSTRUMENT
    eta_val = eta if eta is not None else IMPACT_ETA
    gamma_val = IMPACT_GAMMA

    if adv is None:
        adv, adv_src = tracker.estimate_adv(instrument)
    else:
        adv_src = "query"
    if sigma is None:
        sigma, sigma_src = tracker.estimate_sigma(instrument)
    else:
        sigma_src = "query"
    if size is None:
        size, size_src = tracker.estimate_trade_notional(instrument)
    else:
        size_src = "query"

    temporary = sqrt_law_temporary_bps(size, adv, sigma, eta_val)
    permanent = permanent_bps(size, adv, sigma, gamma_val)
    participation = (size / adv) if adv > 0 else None

    return {
        "instrument": instrument,
        "size": round(size, 6),
        "adv": round(adv, 6),
        "sigma": round(sigma, 8),
        "eta": eta_val,
        "gamma": gamma_val,
        "participation": round(participation, 8) if participation is not None
        else None,
        "temporaryBps": round(temporary, 6),
        "permanentBps": round(permanent, 6),
        "totalBps": round(temporary + permanent, 6),
        "basis": (
            "size={}, adv={}, sigma={}; eta={} (sqrt-law temp coeff), "
            "gamma={} (permanent coeff)"
        ).format(size_src, adv_src, sigma_src, eta_val, gamma_val),
        "model": IMPACT_MODEL_NAME,
        "note": ("pre-trade impact estimate; coefficients are illustrative, not "
                 "calibrated to a measured cost curve"),
    }


def build_schedule_response(size=None, slices=10, risk_aversion=None,
                            sigma=None, instrument=None, tracker=None):
    """Assemble GET /api/impact/schedule (Almgren-Chriss child-order sizes)."""
    n = max(1, int(slices))
    if risk_aversion is None:
        risk_aversion = AC_DEFAULT_LAMBDA
    if size is None:
        if tracker is not None and instrument:
            size, _ = tracker.estimate_trade_notional(instrument)
        else:
            size = DEFAULT_SIZE
    if sigma is None:
        if tracker is not None and instrument:
            sigma, sigma_src = tracker.estimate_sigma(instrument)
        else:
            sigma, sigma_src = DEFAULT_SIGMA, "default"
    else:
        sigma_src = "query"

    children, kappa = almgren_chriss_schedule(size, n, risk_aversion, sigma)
    slice_list = []
    cumulative = 0.0
    for j, qty in enumerate(children, start=1):
        cumulative += qty
        slice_list.append({
            "t": j,
            "qty": round(qty, 6),
            "cumulative": round(cumulative, 6),
        })

    return {
        "instrument": instrument,
        "size": round(size, 6),
        "sliceCount": n,
        "riskAversion": risk_aversion,
        "sigma": round(sigma, 8),
        "sigmaSource": sigma_src,
        "kappa": round(kappa, 8),
        "slices": slice_list,
        "model": "Almgren-Chriss",
        "note": ("closed-form optimal schedule minimising E[cost] + "
                 "lambda*Var[cost]; front-loaded as riskAversion rises, "
                 "uniform (TWAP) as riskAversion -> 0. Slice-time units, "
                 "tau=1 per slice."),
    }


def build_capacity_response(tracker, edge_bps=None, instrument=None,
                            curve_points=12):
    """Assemble GET /api/capacity: impact-vs-size curve + edge crossover.

    The capacity is the size at which modelled impact equals the ASSUMED edge.
    Every edge-dependent figure carries the honesty label.
    """
    if edge_bps is None:
        edge_bps = DEFAULT_EDGE_BPS

    if instrument:
        instruments = [instrument]
    else:
        instruments = tracker.tracked_instruments() or [DEFAULT_INSTRUMENT]

    by_instrument = {}
    for inst in instruments:
        adv, adv_src = tracker.estimate_adv(inst)
        sigma, sigma_src = tracker.estimate_sigma(inst)
        cap = capacity_notional(edge_bps, adv, sigma, IMPACT_ETA, IMPACT_GAMMA)

        curve = []
        if cap is not None and cap > 0:
            # Sample from ~5% of capacity out to 2x capacity so the crossover is
            # visible in the middle of the curve.
            lo, hi = 0.05 * cap, 2.0 * cap
            steps = max(2, int(curve_points))
            for i in range(steps + 1):
                s = lo + (hi - lo) * i / steps
                curve.append({
                    "size": round(s, 6),
                    "impactBps": round(
                        total_impact_bps(s, adv, sigma, IMPACT_ETA,
                                         IMPACT_GAMMA), 6),
                })
            crossover = {
                "size": round(cap, 6),
                "impactBps": round(
                    total_impact_bps(cap, adv, sigma, IMPACT_ETA,
                                     IMPACT_GAMMA), 6),
            }
        else:
            crossover = None

        by_instrument[inst] = {
            "capacityNotional": round(cap, 6) if cap is not None else None,
            "participationAtCapacity": (
                round(cap / adv, 8) if cap is not None and adv > 0 else None
            ),
            "adv": round(adv, 6),
            "advSource": adv_src,
            "sigma": round(sigma, 8),
            "sigmaSource": sigma_src,
            "crossover": crossover,
            "curve": curve,
        }

    return {
        "assumedEdgeBps": edge_bps,
        "assumedEdgeLabel": assumed_edge_label(edge_bps),
        "byInstrument": by_instrument,
        "model": IMPACT_MODEL_NAME,
        "note": ("illustrative; no measured edge. Capacity is the size where "
                 "modelled impact equals the ASSUMED edge (" + EDGE_DISCLAIMER
                 + "); beyond it, per-trade cost exceeds the edge."),
    }


class ForsetiHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/tca" or path == "/":
            self._json_response(tracker.get_tca())
        elif path == "/api/tca/fills":
            self._json_response(tracker.get_fills(self._limit_param(default=50)))
        elif path == "/api/impact/schedule":
            q = self._query()
            self._json_response(build_schedule_response(
                size=self._fparam(q, "size"),
                slices=int(self._fparam(q, "slices") or 10),
                risk_aversion=self._fparam(q, "riskAversion"),
                sigma=self._fparam(q, "sigma"),
                instrument=q.get("instrument"),
                tracker=tracker,
            ))
        elif path == "/api/impact":
            q = self._query()
            self._json_response(build_impact_response(
                tracker,
                instrument=q.get("instrument"),
                size=self._fparam(q, "size"),
                adv=self._fparam(q, "adv"),
                sigma=self._fparam(q, "sigma"),
                eta=self._fparam(q, "eta"),
            ))
        elif path == "/api/capacity":
            q = self._query()
            self._json_response(build_capacity_response(
                tracker,
                edge_bps=self._fparam(q, "edgeBps"),
                instrument=q.get("instrument"),
            ))
        elif path == "/healthz" or path == "/readyz":
            ok, age = liveness.status()
            payload = {
                "status": "ok" if ok else "degraded",
                "service": "forseti",
                "consumer_alive": ok,
                "consumer_last_beat_age_secs": (
                    round(age, 1) if age is not None else None
                ),
                "counters": counters.snapshot(),
            }
            self._json_response(payload, status=200 if ok else 503)
        elif path == "/metrics":
            self._text_response(counters.render_prometheus())
        else:
            self.send_error(404)

    def _query(self):
        """Parse the query string into a {key: first-value} dict."""
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    @staticmethod
    def _fparam(q, key):
        """Return q[key] as a float, or None if absent/unparseable."""
        raw = q.get(key)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    def _limit_param(self, default):
        """Parse ?limit=N from the query string; fall back to default."""
        if "?" not in self.path:
            return default
        query = self.path.split("?", 1)[1]
        for part in query.split("&"):
            if part.startswith("limit="):
                try:
                    return max(1, int(part[len("limit="):]))
                except (ValueError, TypeError):
                    return default
        return default

    def _text_response(self, text):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Access-Control-Allow-Origin", ACCESS_CONTROL_ALLOW_ORIGIN)
        self.end_headers()
        self.wfile.write(body)

    def _json_response(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", ACCESS_CONTROL_ALLOW_ORIGIN)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def _make_fills_consumer(consumer_factory=KafkaConsumer):
    """Build the fills consumer as a FULL-TOPIC, dedup-based projection.

    Like Odin, Forseti is a *projection* of the executions.fills.v1 topic, not
    a cursor over it: its all-time TCA must equal a fold over the entire topic
    and be restart-independent. We use a fresh unique group_id per process (so
    Kafka has no committed offset and falls back to auto_offset_reset), set
    auto_offset_reset="earliest", and disable auto-commit so we never persist
    an offset a future restart could resume from. add_fill dedups every fill on
    execution_id, making the full-topic replay idempotent / side-effect-free.
    """
    return consumer_factory(
        FILLS_TOPIC,
        bootstrap_servers=KAFKA_BROKERS,
        group_id="forseti-tca-{}".format(uuid.uuid4().hex),
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
    )


def _make_prices_consumer(consumer_factory=KafkaConsumer):
    """Build the optional prices consumer for the arrival/mid benchmark.

    Unlike fills, prices are a LATEST-value benchmark, not an all-time fold, so
    this consumer reads from the tail (auto_offset_reset="latest"): we only need
    prices at-or-just-before each incoming fill, never the historical backlog.
    """
    return consumer_factory(
        PRICES_TOPIC,
        bootstrap_servers=KAFKA_BROKERS,
        group_id="forseti-prices-{}".format(uuid.uuid4().hex),
        auto_offset_reset="latest",
        enable_auto_commit=False,
        consumer_timeout_ms=1000,
    )


def consume_prices():
    """Consume the realtime price feed to keep arrival/mid benchmarks fresh."""
    for attempt in range(30):
        try:
            consumer = _make_prices_consumer()
            log.info("Connected to Kafka, consuming prices %s", PRICES_TOPIC)
            break
        except KafkaConnectionError:
            log.warning("Kafka (prices) not ready (attempt %d/30)...", attempt + 1)
            time.sleep(2)
    else:
        log.error("Failed to connect to Kafka for prices; benchmarks disabled")
        return

    while not shutdown:
        try:
            records = consumer.poll(timeout_ms=1000)
            for tp, messages in records.items():
                for msg in messages:
                    try:
                        tick = json.loads(msg.value.decode("utf-8"))
                    except (ValueError, TypeError, UnicodeDecodeError) as de:
                        counters.inc("prices_decode_failure_total")
                        log.warning("Dropping undecodable price tick: %s", de)
                        continue
                    tracker.add_price(tick)
        except Exception as e:
            log.error("Prices consumer error: %s", e)
            time.sleep(1)

    consumer.close()


def consume_fills():
    """Consume the live fills topic and compute per-fill TCA."""
    for attempt in range(30):
        try:
            consumer = _make_fills_consumer()
            log.info("Connected to Kafka, consuming %s", FILLS_TOPIC)
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
                    try:
                        fill = json.loads(msg.value.decode("utf-8"))
                    except (ValueError, TypeError, UnicodeDecodeError) as de:
                        counters.inc("decode_failure_total")
                        log.warning("Dropping undecodable fill record: %s", de)
                        continue
                    tracker.add_fill(fill)
                    if isinstance(fill, dict):
                        log.info(
                            "TCA fill: %s %s %s @ $%.2f (fee: $%.4f)",
                            tracker._normalize_side(
                                fill.get("side", fill.get("Side", "?"))
                            ) or "?",
                            fill.get("quantity", fill.get("Quantity", 0)),
                            fill.get("instrument", fill.get("Instrument", "?")),
                            float(fill.get("fill_price",
                                           fill.get("FillPrice", 0)) or 0),
                            float(fill.get("transaction_cost",
                                           fill.get("TransactionCost", 0)) or 0),
                        )
        except Exception as e:
            log.error("Consumer error: %s", e)
            time.sleep(1)

    consumer.close()


def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("=" * 60)
    log.info("  FORSETI — Execution Transaction-Cost-Analysis (TCA)")
    log.info("=" * 60)
    log.info("  Fills topic:  %s", FILLS_TOPIC)
    log.info("  Prices feed:  %s (%s)", PRICES_TOPIC,
             "enabled" if PRICES_ENABLED else "disabled")
    log.info("  API port:     %d", PORT)
    log.info("  Endpoints:")
    log.info("    /api/tca            — overall + per-instrument cost analysis")
    log.info("    /api/tca/fills      — recent per-fill TCA records (newest-first)")
    log.info("    /api/impact         — sqrt-law + Almgren-Chriss impact estimate")
    log.info("    /api/impact/schedule— Almgren-Chriss optimal child-order sizes")
    log.info("    /api/capacity       — impact-vs-size curve + edge crossover")
    log.info("    /healthz            — liveness")
    log.info("=" * 60)

    consumer_thread = threading.Thread(target=consume_fills, daemon=True)
    consumer_thread.start()

    if PRICES_ENABLED:
        prices_thread = threading.Thread(target=consume_prices, daemon=True)
        prices_thread.start()

    server = HTTPServer(("0.0.0.0", PORT), ForsetiHandler)
    server.timeout = 1
    log.info("Forseti HTTP server listening on :%d", PORT)

    while not shutdown:
        server.handle_request()

    server.server_close()
    log.info("Forseti shutdown complete")


if __name__ == "__main__":
    main()
