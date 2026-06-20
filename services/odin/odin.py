#!/usr/bin/env python3
"""
Odin — Alpha Decay & Strategy Performance Monitor.

Consumes fills and feature events from Kafka, computes rolling performance
analytics, and serves them via a REST API. Tracks:
  - Rolling Sharpe and Sortino ratios (1h, 6h, 24h windows)
  - Conditional Value-at-Risk (CVaR/ES) at 95th percentile
  - Max drawdown: magnitude, duration, and recovery time
  - Win rate, profit factor, Kelly criterion sizing
  - Equity curve, round-trip P&L, fee drag
  - Per-instrument breakdown with rolling correlation matrix
  - Monte Carlo permutation test for statistical significance

No API key required. Runs as a Docker service alongside the Norse Stack.
"""

import json
import logging
import math
import os
import random
import signal
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

from kafka import KafkaConsumer
from kafka.errors import KafkaConnectionError

KAFKA_BROKERS = os.environ.get("KAFKA_BROKERS", "redpanda:29092")
FILLS_TOPIC = os.environ.get("FILLS_TOPIC", "executions.fills.v1")
DLQ_TOPIC = os.environ.get("FILLS_DLQ_TOPIC", "executions.fills.v1.dlq")
PORT = int(os.environ.get("PORT", "8086"))

# CORS: default to "*" to preserve existing behaviour, but allow locking the
# allowed origin down to a single configured value in hardened deployments.
ACCESS_CONTROL_ALLOW_ORIGIN = os.environ.get("ACCESS_CONTROL_ALLOW_ORIGIN", "*")

# Liveness: the consumer thread stamps a heartbeat each poll cycle. /healthz
# returns 503 once the heartbeat is older than this many seconds, so a wedged
# consumer thread is detectable even while the HTTP server stays up. Generous
# default avoids flapping on an idle (but healthy) stream — the heartbeat is
# published every cycle regardless of whether any message arrived.
HEALTH_MAX_STALENESS_SECS = float(os.environ.get("HEALTH_MAX_STALENESS_SECS", "120"))

# When true, open positions in the equity curve / drawdown / VaR are marked to
# the last seen trade price instead of average cost. Defaults to False so the
# historical "realized-only" behaviour is preserved unless explicitly enabled.
MARK_TO_MARKET = os.environ.get("MARK_TO_MARKET", "false").lower() in ("1", "true", "yes")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("odin")

shutdown = False


class Counters:
    """Lightweight, thread-safe, Prometheus-style monotonic counters.

    Odin ships stdlib-only (see Dockerfile), so we don't pull in
    prometheus_client. These counters are exposed via /metrics and the
    analytics payload so operational issues (parse failures, rejected
    events, duplicate fills) are observable instead of silently swallowed.
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
            metric = f"odin_{name}"
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


class PerformanceTracker:
    def __init__(self, initial_cash=None):
        if initial_cash is None:
            initial_cash = float(os.environ.get("INITIAL_CASH", "1000"))
        self.lock = threading.Lock()
        self.initial_cash = initial_cash
        self.fills = deque(maxlen=5000)
        self.equity_curve = deque(maxlen=5000)
        self.peak_value = initial_cash
        self.max_drawdown = 0.0
        self.max_drawdown_pct = 0.0

        # Drawdown duration tracking
        self.dd_start_time = None
        self.max_dd_duration_secs = 0
        self.in_drawdown = False

        # Per-instrument tracking
        self.positions = defaultdict(lambda: {"qty": 0.0, "avg_cost": 0.0})
        self.cash = initial_cash
        self.realized_pnl = 0.0
        self.total_fees = 0.0

        # Last seen trade price per instrument (used for mark-to-market valuation
        # when MARK_TO_MARKET is enabled). Without a realtime price feed this is
        # the closest available proxy for current market value.
        self.last_price = {}

        # Dedup: the fills consumer reads from the earliest offset, so on a
        # restart it would replay and double-count every fill. We dedup on the
        # execution_id with a bounded seen-set so memory stays flat.
        self._seen_execution_ids = deque(maxlen=50000)
        self._seen_execution_set = set()

        # Trade analytics
        self.round_trips = deque(maxlen=2000)
        self.open_trades = {}

        # Per-instrument P&L series for correlation
        self.instrument_returns = defaultdict(lambda: deque(maxlen=500))

        # Time-series for rolling windows
        self.pnl_series = deque(maxlen=5000)

        # Monte Carlo cache (recomputed periodically)
        self._mc_cache = None
        self._mc_cache_size = 0

        self.equity_curve.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "value": initial_cash,
            "cash": initial_cash,
            "pnl": 0.0,
            "fees": 0.0,
            "fills": 0,
        })

    @staticmethod
    def _validate_fill(fill):
        """Validate an incoming fill. Returns (ok, reason).

        Rejects events missing required fields or carrying NaN/Inf numeric
        values, which would otherwise poison every downstream aggregate
        (mean/variance/Sharpe all become NaN once a single NaN enters).
        """
        if not isinstance(fill, dict):
            return False, "not_a_dict"

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

    def add_fill(self, fill):
        with self.lock:
            ok, reason = self._validate_fill(fill)
            if not ok:
                counters.inc("fills_rejected_total")
                counters.inc(f"fills_rejected_{reason}")
                log.warning("Rejected fill (%s): %r", reason, fill)
                return

            # Dedup so a restart (consumer reads from earliest) doesn't
            # double-count. Prefer execution_id, but fall back to a composite
            # key when it's empty/missing: some fill sources (e.g. the sim
            # connector's older path) leave execution_id blank, and deduping on
            # a blank key would collapse EVERY such fill into one "duplicate".
            exec_id = fill.get("execution_id")
            if not exec_id:
                exec_id = "{}|{}|{}|{}|{}".format(
                    fill.get("order_id", ""), fill.get("timestamp", ""),
                    fill.get("side", ""), fill.get("quantity", ""),
                    fill.get("fill_price", ""))
            if exec_id in self._seen_execution_set:
                counters.inc("fills_duplicate_total")
                return
            self._seen_execution_set.add(exec_id)
            if len(self._seen_execution_ids) == self._seen_execution_ids.maxlen:
                evicted = self._seen_execution_ids[0]
                self._seen_execution_set.discard(evicted)
            self._seen_execution_ids.append(exec_id)

            self.fills.append(fill)
            counters.inc("fills_processed_total")

            instrument = fill.get("instrument", "")
            side = fill.get("side", "").upper()
            qty = float(fill.get("quantity", 0))
            price = float(fill.get("fill_price", 0))
            fee = float(fill.get("transaction_cost", 0) or 0)
            ts = fill.get("timestamp", datetime.now(timezone.utc).isoformat())

            self.total_fees += fee
            self.last_price[instrument] = price
            pos = self.positions[instrument]

            if side == "BUY":
                total_cost = pos["avg_cost"] * pos["qty"] + price * qty
                pos["qty"] += qty
                pos["avg_cost"] = total_cost / pos["qty"] if pos["qty"] > 0 else 0
                self.cash -= (price * qty + fee)

                self.open_trades.setdefault(instrument, []).append({
                    "side": "BUY", "qty": qty, "price": price, "time": ts
                })

            elif side == "SELL":
                if pos["qty"] > 0:
                    pnl = (price - pos["avg_cost"]) * min(qty, pos["qty"])
                    self.realized_pnl += pnl
                    net_pnl = pnl - fee
                    self.round_trips.append({
                        "instrument": instrument,
                        "pnl": pnl,
                        "fee": fee,
                        "net_pnl": net_pnl,
                        "time": ts,
                    })
                    self.instrument_returns[instrument].append(net_pnl)

                pos["qty"] -= qty
                if pos["qty"] <= 0.0001:
                    pos["qty"] = 0
                    pos["avg_cost"] = 0
                self.cash += (price * qty - fee)

                self.open_trades.setdefault(instrument, []).append({
                    "side": "SELL", "qty": qty, "price": price, "time": ts
                })

            # Update equity curve. With MARK_TO_MARKET enabled, open positions
            # are valued at the last seen trade price (best available proxy for
            # market value); otherwise they are held at avg_cost and the curve
            # is REALIZED-ONLY (open P&L not reflected).
            total_value = self.cash
            for inst, p in self.positions.items():
                if MARK_TO_MARKET:
                    mark = self.last_price.get(inst, p["avg_cost"])
                else:
                    mark = p["avg_cost"]
                total_value += p["qty"] * mark

            # Parse this fill's timestamp once; a parse failure is observable
            # (counter) rather than silently swallowed, and it must not corrupt
            # drawdown-duration bookkeeping.
            event_dt = self._parse_ts(ts)

            # Drawdown tracking with duration
            if total_value > self.peak_value:
                if self.in_drawdown and self.dd_start_time and event_dt is not None:
                    duration = (event_dt - self.dd_start_time).total_seconds()
                    # Reject negative durations from out-of-order timestamps.
                    if duration < 0:
                        counters.inc("drawdown_negative_duration_total")
                    elif duration > self.max_dd_duration_secs:
                        self.max_dd_duration_secs = duration
                self.peak_value = total_value
                self.in_drawdown = False
                self.dd_start_time = None
            else:
                dd = self.peak_value - total_value
                dd_pct = dd / self.peak_value if self.peak_value > 0 else 0
                if dd_pct > 0.0001 and not self.in_drawdown:
                    self.in_drawdown = True
                    self.dd_start_time = (
                        event_dt if event_dt is not None
                        else datetime.now(timezone.utc)
                    )
                if dd_pct > self.max_drawdown_pct:
                    self.max_drawdown = dd
                    self.max_drawdown_pct = dd_pct

            self.pnl_series.append((ts, self.realized_pnl - self.total_fees))

            self.equity_curve.append({
                "timestamp": ts,
                "value": round(total_value, 2),
                "cash": round(self.cash, 2),
                "pnl": round(self.realized_pnl, 2),
                "fees": round(self.total_fees, 2),
                "fills": len(self.fills),
            })

    @staticmethod
    def _parse_ts(ts):
        """Parse an ISO-8601 timestamp. Returns a datetime or None.

        On failure, increments a parse-failure counter so the problem is
        observable rather than silently swallowed."""
        if not isinstance(ts, str):
            counters.inc("timestamp_parse_failure_total")
            return None
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            counters.inc("timestamp_parse_failure_total")
            return None

    def _get_returns(self, window_hours=None):
        """Extract P&L change series. Must be called with lock held."""
        if len(self.pnl_series) < 2:
            return []

        now = datetime.now(timezone.utc)
        cutoff_seconds = window_hours * 3600 if window_hours else float("inf")
        recent = []

        for i in range(1, len(self.pnl_series)):
            ts_str = self.pnl_series[i][0]
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                age = (now - ts).total_seconds()
                if age <= cutoff_seconds:
                    ret = self.pnl_series[i][1] - self.pnl_series[i - 1][1]
                    recent.append(ret)
            except (ValueError, TypeError):
                continue

        return recent

    def compute_rolling_sharpe(self, window_hours):
        """Must be called with self.lock held."""
        recent = self._get_returns(window_hours)
        if len(recent) < 2:
            return 0.0

        mean = sum(recent) / len(recent)
        variance = sum((r - mean) ** 2 for r in recent) / len(recent)
        std = math.sqrt(variance) if variance > 0 else 0.001

        trades_per_year = len(recent) / max(window_hours, 1) * 8760
        annualization = math.sqrt(trades_per_year) if trades_per_year > 0 else 1

        return round((mean / std) * annualization, 3)

    def compute_sortino(self, window_hours):
        """Sortino ratio: penalizes only downside volatility. Lock must be held."""
        recent = self._get_returns(window_hours)
        if len(recent) < 2:
            return 0.0

        mean = sum(recent) / len(recent)
        downside = [r for r in recent if r < 0]
        if not downside:
            return 0.0 if mean <= 0 else 99.0

        downside_var = sum(r ** 2 for r in downside) / len(recent)
        downside_dev = math.sqrt(downside_var) if downside_var > 0 else 0.001

        trades_per_year = len(recent) / max(window_hours, 1) * 8760
        annualization = math.sqrt(trades_per_year) if trades_per_year > 0 else 1

        return round((mean / downside_dev) * annualization, 3)

    def compute_cvar(self, percentile=5):
        """Conditional Value-at-Risk (Expected Shortfall). Lock must be held.
        Returns the average loss in the worst `percentile`% of trades."""
        returns = [r["net_pnl"] for r in self.round_trips]
        if len(returns) < 5:
            return 0.0

        sorted_returns = sorted(returns)
        cutoff_idx = max(1, int(len(sorted_returns) * percentile / 100))
        tail = sorted_returns[:cutoff_idx]
        return round(sum(tail) / len(tail), 4)

    @staticmethod
    def _bucket_trades(round_trips):
        """Strictly bucket round-trips into wins (>0) and losses (<0).

        Break-even trades (net_pnl == 0) are EXCLUDED from both buckets — they
        are neither wins nor losses and must not be miscounted as losses (which
        would understate profit factor and distort Kelly)."""
        wins = [r for r in round_trips if r["net_pnl"] > 0]
        losses = [r for r in round_trips if r["net_pnl"] < 0]
        return wins, losses

    @staticmethod
    def _profit_factor(wins, losses):
        """Profit factor = gross_profit / gross_loss.

        Returns None when there are no losing trades (the ratio is undefined /
        infinite) rather than fabricating a finite number via a tiny denominator
        floor. Returns 0.0 when there are wins-less, losses-only or no closed
        trades."""
        gross_profit = sum(r["net_pnl"] for r in wins)
        gross_loss = abs(sum(r["net_pnl"] for r in losses))
        if gross_loss == 0:
            # No losses: profit factor is undefined (infinite) if there were
            # any wins, otherwise there is simply nothing to report.
            return None if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def compute_kelly(self):
        """Half-Kelly criterion for optimal position sizing. Lock must be held."""
        if len(self.round_trips) < 10:
            return 0.0

        wins, losses = self._bucket_trades(self.round_trips)

        # Need both winners and losers, and at least one non-break-even trade
        # in each bucket, to form a meaningful payoff ratio.
        if not wins or not losses:
            return 0.0

        # Win rate is computed over decisive (non-break-even) trades only, so
        # it stays consistent with the strictly-bucketed win/loss counts.
        decisive = len(wins) + len(losses)
        win_rate = len(wins) / decisive
        avg_win = sum(r["net_pnl"] for r in wins) / len(wins)
        avg_loss = abs(sum(r["net_pnl"] for r in losses) / len(losses))

        if avg_loss <= 0:
            return 0.0

        b = avg_win / avg_loss
        f = (b * win_rate - (1 - win_rate)) / b
        f = f / 2  # half-Kelly
        return round(max(0.0, min(f, 0.25)), 4)

    def compute_calmar(self):
        """Calmar ratio: annualized return / max drawdown. Lock must be held."""
        if self.max_drawdown_pct <= 0 or len(self.equity_curve) < 2:
            return 0.0

        try:
            start = datetime.fromisoformat(
                self.equity_curve[0]["timestamp"].replace("Z", "+00:00")
            )
            now = datetime.now(timezone.utc)
            years = max((now - start).total_seconds() / (365.25 * 86400), 0.001)
        except (ValueError, TypeError):
            return 0.0

        current_value = self.equity_curve[-1]["value"]
        total_return = (current_value - self.initial_cash) / self.initial_cash
        annualized = total_return / years

        return round(annualized / self.max_drawdown_pct, 3)

    def compute_correlation_matrix(self):
        """Rolling pairwise correlation between instrument returns. Lock held."""
        instruments = sorted(self.instrument_returns.keys())
        if len(instruments) < 2:
            return {}

        matrix = {}
        for i, a in enumerate(instruments):
            for b in instruments[i + 1:]:
                ra = list(self.instrument_returns[a])
                rb = list(self.instrument_returns[b])
                min_len = min(len(ra), len(rb))
                if min_len < 5:
                    continue
                ra = ra[-min_len:]
                rb = rb[-min_len:]

                mean_a = sum(ra) / len(ra)
                mean_b = sum(rb) / len(rb)
                cov = sum((ra[j] - mean_a) * (rb[j] - mean_b)
                          for j in range(min_len)) / min_len
                std_a = math.sqrt(
                    sum((x - mean_a) ** 2 for x in ra) / len(ra)
                ) or 0.001
                std_b = math.sqrt(
                    sum((x - mean_b) ** 2 for x in rb) / len(rb)
                ) or 0.001

                corr = cov / (std_a * std_b)
                matrix[f"{a}|{b}"] = round(max(-1, min(1, corr)), 3)

        return matrix

    def compute_portfolio_var(self, confidence=0.95):
        """Portfolio Value-at-Risk using the variance-covariance method.

        Combines per-instrument return distributions with the cross-asset
        correlation matrix to compute portfolio-level VaR. This captures
        diversification benefit — correlated positions amplify risk, while
        decorrelated ones reduce it. Lock must be held.
        """
        instruments = sorted(self.instrument_returns.keys())
        if len(instruments) < 1:
            return {"var_95": 0, "var_99": 0, "diversification_benefit": 0,
                    "instruments": 0, "method": "variance-covariance"}

        n = len(instruments)
        # Per-instrument standard deviations
        stds = []
        means = []
        for inst in instruments:
            rets = list(self.instrument_returns[inst])
            if len(rets) < 5:
                stds.append(0)
                means.append(0)
                continue
            m = sum(rets) / len(rets)
            v = sum((r - m) ** 2 for r in rets) / len(rets)
            stds.append(math.sqrt(v))
            means.append(m)

        if all(s == 0 for s in stds):
            return {"var_95": 0, "var_99": 0, "diversification_benefit": 0,
                    "instruments": n, "method": "variance-covariance"}

        # Build correlation matrix
        corr_matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            corr_matrix[i][i] = 1.0
            for j in range(i + 1, n):
                ra = list(self.instrument_returns[instruments[i]])
                rb = list(self.instrument_returns[instruments[j]])
                min_len = min(len(ra), len(rb))
                if min_len < 5:
                    continue
                ra = ra[-min_len:]
                rb = rb[-min_len:]
                ma = sum(ra) / len(ra)
                mb = sum(rb) / len(rb)
                cov = sum((ra[k] - ma) * (rb[k] - mb) for k in range(min_len)) / min_len
                sa = math.sqrt(sum((x - ma) ** 2 for x in ra) / len(ra)) or 1e-10
                sb = math.sqrt(sum((x - mb) ** 2 for x in rb) / len(rb)) or 1e-10
                c = max(-1, min(1, cov / (sa * sb)))
                corr_matrix[i][j] = c
                corr_matrix[j][i] = c

        # Portfolio variance = w' * Sigma * w (equal weight assumption)
        weights = [1.0 / n] * n
        port_var = 0
        for i in range(n):
            for j in range(n):
                port_var += weights[i] * weights[j] * stds[i] * stds[j] * corr_matrix[i][j]

        port_std = math.sqrt(max(port_var, 0))

        # Undiversified VaR (sum of individual VaRs)
        z_95 = 1.645
        z_99 = 2.326
        undiversified = sum(w * s * z_95 for w, s in zip(weights, stds))
        diversified_95 = port_std * z_95
        diversified_99 = port_std * z_99

        div_benefit = 1 - (diversified_95 / undiversified) if undiversified > 0 else 0

        return {
            "var_95": round(diversified_95 * 100, 4),
            "var_99": round(diversified_99 * 100, 4),
            "diversification_benefit": round(div_benefit * 100, 2),
            "instruments": n,
            "method": "variance-covariance",
        }

    @staticmethod
    def _sharpe_of(returns):
        """Per-trade Sharpe (mean / population std) of a return list, or 0."""
        n = len(returns)
        if n < 2:
            return 0.0
        m = sum(returns) / n
        v = sum((r - m) ** 2 for r in returns) / n
        return m / math.sqrt(v) if v > 0 else 0.0

    def compute_monte_carlo(self, n_sims=5000):
        """Bootstrap significance test for per-trade Sharpe.

        The previous implementation shuffled the ORDER of a fixed return set and
        compared the Sharpe of the cumulative-PnL diffs. Shuffling order leaves
        the diff multiset (and therefore mean/variance/Sharpe) unchanged, so it
        could never reject the null — it was statistically vacuous.

        Instead we resample the per-trade returns WITH REPLACEMENT (a stationary
        i.i.d. bootstrap) to build the sampling distribution of the Sharpe ratio.
        From that distribution we report:
          - a two-sided 95% confidence interval on the Sharpe, and
          - a one-sided bootstrap p-value for the null H0: true Sharpe <= 0
            (the fraction of resamples whose Sharpe is <= 0).
        Alpha is "significant" when the whole 95% CI sits above zero
        (equivalently p_value < 0.05). Lock must be held. Cached until the
        round_trip count changes.
        """
        rts = list(self.round_trips)
        if len(rts) < 10:
            return {"p_value": 1.0, "actual_sharpe": 0.0, "simulations": 0,
                    "percentile": 0, "significant": False,
                    "sharpe_ci_95": [0.0, 0.0],
                    "method": "bootstrap-sharpe",
                    "null_hypothesis": "true_sharpe <= 0"}

        if self._mc_cache and self._mc_cache_size == len(rts):
            return self._mc_cache

        returns = [r["net_pnl"] for r in rts]
        n = len(returns)
        actual_sharpe = self._sharpe_of(returns)

        rng = random.Random(42)
        boot_sharpes = []
        le_zero = 0  # resamples with Sharpe <= 0 -> one-sided p-value for H0
        for _ in range(n_sims):
            sample = [returns[rng.randrange(n)] for _ in range(n)]
            s = self._sharpe_of(sample)
            boot_sharpes.append(s)
            if s <= 0:
                le_zero += 1

        boot_sharpes.sort()
        lo = boot_sharpes[int(0.025 * n_sims)]
        hi = boot_sharpes[min(int(0.975 * n_sims), n_sims - 1)]
        p_value = le_zero / n_sims
        percentile = round((1 - p_value) * 100, 1)

        result = {
            "p_value": round(p_value, 4),
            "actual_sharpe": round(actual_sharpe, 4),
            "simulations": n_sims,
            "percentile": percentile,
            # Significant alpha = the bootstrap 95% CI for the Sharpe lies
            # entirely above zero.
            "significant": lo > 0,
            "sharpe_ci_95": [round(lo, 4), round(hi, 4)],
            "method": "bootstrap-sharpe",
            "null_hypothesis": "true_sharpe <= 0",
        }
        self._mc_cache = result
        self._mc_cache_size = len(rts)
        return result

    def get_analytics(self):
        with self.lock:
            total_trades = len(self.fills)
            if total_trades == 0:
                return self._empty_analytics()

            # Strict bucketing: >0 win, <0 loss, ==0 (break-even) excluded.
            wins, losses = self._bucket_trades(self.round_trips)
            decisive = len(wins) + len(losses)
            win_rate = len(wins) / decisive if decisive else 0

            gross_profit = sum(r["net_pnl"] for r in wins) if wins else 0
            gross_loss = abs(sum(r["net_pnl"] for r in losses)) if losses else 0
            # profit_factor is None when there are no losses (undefined/infinite)
            # rather than a fabricated huge number from a denominator floor.
            profit_factor = self._profit_factor(wins, losses)

            avg_win = gross_profit / len(wins) if wins else 0
            avg_loss = gross_loss / len(losses) if losses else 0

            net_trading_pnl = self.realized_pnl - self.total_fees

            by_instrument = {}
            for inst, pos in self.positions.items():
                inst_trades = [f for f in self.fills if f.get("instrument") == inst]
                inst_rts = [r for r in self.round_trips if r["instrument"] == inst]
                inst_wins = [r for r in inst_rts if r["net_pnl"] > 0]
                by_instrument[inst] = {
                    "trades": len(inst_trades),
                    "round_trips": len(inst_rts),
                    "win_rate": len(inst_wins) / len(inst_rts) if inst_rts else 0,
                    "net_pnl": round(sum(r["net_pnl"] for r in inst_rts), 2),
                    "position": pos["qty"],
                    "avg_cost": round(pos["avg_cost"], 2),
                }

            if len(self.equity_curve) > 1:
                try:
                    start = datetime.fromisoformat(
                        self.equity_curve[0]["timestamp"].replace("Z", "+00:00")
                    )
                    now = datetime.now(timezone.utc)
                    runtime_hours = (now - start).total_seconds() / 3600
                except (ValueError, TypeError):
                    runtime_hours = 0
            else:
                runtime_hours = 0

            # Current drawdown duration
            current_dd_secs = 0
            if self.in_drawdown and self.dd_start_time:
                current_dd_secs = (
                    datetime.now(timezone.utc) - self.dd_start_time
                ).total_seconds()

            # Label the equity-curve valuation basis everywhere it's exposed.
            # When MARK_TO_MARKET is off, open positions are held at avg_cost so
            # the curve reflects REALIZED P&L only (open/unrealized P&L excluded).
            valuation_basis = "mark-to-market" if MARK_TO_MARKET else "realized-only"

            return {
                "status": "running",
                "runtime_hours": round(runtime_hours, 2),
                "total_fills": total_trades,
                "round_trips": len(self.round_trips),
                "valuation_basis": valuation_basis,
                "performance": {
                    "realized_pnl": round(self.realized_pnl, 2),
                    "total_fees": round(self.total_fees, 2),
                    "net_trading_pnl": round(net_trading_pnl, 2),
                    "fee_drag_pct": round(
                        self.total_fees / max(abs(self.realized_pnl), 0.01) * 100, 1
                    ),
                    "win_rate": round(win_rate, 3),
                    # None => profit factor undefined (no losing trades).
                    "profit_factor": (
                        round(profit_factor, 3) if profit_factor is not None else None
                    ),
                    "avg_win": round(avg_win, 2),
                    "avg_loss": round(avg_loss, 2),
                    "max_drawdown": round(self.max_drawdown, 2),
                    "max_drawdown_pct": round(self.max_drawdown_pct * 100, 2),
                    "max_dd_duration_mins": round(self.max_dd_duration_secs / 60, 1),
                    "current_dd_duration_mins": round(current_dd_secs / 60, 1),
                    "valuation_basis": valuation_basis,
                },
                "sharpe": {
                    "1h": self.compute_rolling_sharpe(1),
                    "6h": self.compute_rolling_sharpe(6),
                    "24h": self.compute_rolling_sharpe(24),
                },
                "sortino": {
                    "1h": self.compute_sortino(1),
                    "6h": self.compute_sortino(6),
                    "24h": self.compute_sortino(24),
                },
                "risk": {
                    "cvar_95": self.compute_cvar(5),
                    "calmar_ratio": self.compute_calmar(),
                    "kelly_fraction": self.compute_kelly(),
                    "portfolio_var": self.compute_portfolio_var(),
                },
                "correlation_matrix": self.compute_correlation_matrix(),
                "monte_carlo": self.compute_monte_carlo(),
                "by_instrument": by_instrument,
                "equity_curve_points": len(self.equity_curve),
                "latest_equity": self.equity_curve[-1] if self.equity_curve else None,
            }

    def get_equity_curve(self):
        with self.lock:
            return {
                "valuation_basis": (
                    "mark-to-market" if MARK_TO_MARKET else "realized-only"
                ),
                "points": list(self.equity_curve),
            }

    def get_recent_trades(self, limit=20):
        with self.lock:
            # round_trips is a deque, which does not support slice indexing;
            # materialize to a list first before slicing/reversing.
            trades = list(self.round_trips)
            return list(reversed(trades[-limit:]))

    def _empty_analytics(self):
        valuation_basis = "mark-to-market" if MARK_TO_MARKET else "realized-only"
        return {
            "status": "waiting_for_fills",
            "runtime_hours": 0,
            "total_fills": 0,
            "round_trips": 0,
            "valuation_basis": valuation_basis,
            "performance": {
                "realized_pnl": 0, "total_fees": 0, "net_trading_pnl": 0,
                "fee_drag_pct": 0, "win_rate": 0, "profit_factor": None,
                "avg_win": 0, "avg_loss": 0, "max_drawdown": 0,
                "max_drawdown_pct": 0, "max_dd_duration_mins": 0,
                "current_dd_duration_mins": 0,
                "valuation_basis": valuation_basis,
            },
            "sharpe": {"1h": 0, "6h": 0, "24h": 0},
            "sortino": {"1h": 0, "6h": 0, "24h": 0},
            "risk": {"cvar_95": 0, "calmar_ratio": 0, "kelly_fraction": 0,
                     "portfolio_var": {"var_95": 0, "var_99": 0,
                                       "diversification_benefit": 0,
                                       "instruments": 0,
                                       "method": "variance-covariance"}},
            "correlation_matrix": {},
            "monte_carlo": {
                "p_value": 1.0, "actual_sharpe": 0, "simulations": 0,
                "percentile": 0, "significant": False,
                "sharpe_ci_95": [0.0, 0.0], "method": "bootstrap-sharpe",
                "null_hypothesis": "true_sharpe <= 0",
            },
            "by_instrument": {},
            "equity_curve_points": 0,
            "latest_equity": None,
        }


tracker = PerformanceTracker()


class OdinHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/analytics" or self.path == "/":
            self._json_response(tracker.get_analytics())
        elif self.path == "/api/equity":
            self._json_response(tracker.get_equity_curve())
        elif self.path == "/api/trades":
            self._json_response(tracker.get_recent_trades())
        elif self.path == "/healthz" or self.path == "/readyz":
            ok, age = liveness.status()
            payload = {
                "status": "ok" if ok else "degraded",
                "service": "odin",
                "consumer_alive": ok,
                "consumer_last_beat_age_secs": (
                    round(age, 1) if age is not None else None
                ),
                "counters": counters.snapshot(),
            }
            self._json_response(payload, status=200 if ok else 503)
        elif self.path == "/metrics":
            self._text_response(counters.render_prometheus())
        else:
            self.send_error(404)

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


def _make_dlq_producer():
    """Best-effort DLQ producer. Returns a KafkaProducer or None.

    Poison records (undecodable JSON) are republished to DLQ_TOPIC so they are
    not lost. If a producer can't be constructed we degrade to counter-only:
    the record is still skipped (never stalls the batch), just not republished.
    """
    try:
        from kafka import KafkaProducer
        return KafkaProducer(bootstrap_servers=KAFKA_BROKERS)
    except Exception as e:  # pragma: no cover - depends on kafka availability
        log.warning("DLQ producer unavailable (%s); decode failures counter-only", e)
        return None


def consume_fills():
    for attempt in range(30):
        try:
            # Deserialize raw bytes here and decode JSON per-message below, so a
            # single poison record raises inside our per-message try/except
            # (incrementing a counter + DLQ) instead of bubbling out of poll()
            # and stalling/dropping the rest of the batch.
            consumer = KafkaConsumer(
                FILLS_TOPIC,
                bootstrap_servers=KAFKA_BROKERS,
                group_id="odin-analytics",
                auto_offset_reset="earliest",
                consumer_timeout_ms=1000,
            )
            log.info("Connected to Kafka, consuming %s", FILLS_TOPIC)
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
            # Heartbeat once per poll cycle, whether or not records arrived, so
            # /healthz reflects loop liveness rather than message arrival rate.
            liveness.beat()
            for tp, messages in records.items():
                for msg in messages:
                    # Per-message decode: a bad record is isolated to itself.
                    try:
                        fill = json.loads(msg.value.decode("utf-8"))
                    except (ValueError, TypeError, UnicodeDecodeError) as de:
                        counters.inc("decode_failure_total")
                        log.warning("Dropping undecodable fill record: %s", de)
                        if dlq_producer is not None:
                            try:
                                dlq_producer.send(DLQ_TOPIC, value=msg.value)
                            except Exception as pe:  # pragma: no cover
                                counters.inc("dlq_publish_failure_total")
                                log.warning("DLQ publish failed: %s", pe)
                        continue

                    tracker.add_fill(fill)
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
            log.error("Consumer error: %s", e)
            time.sleep(1)

    consumer.close()
    if dlq_producer is not None:
        dlq_producer.close()


def main():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log.info("=" * 60)
    log.info("  ODIN — Alpha Decay & Strategy Performance Monitor")
    log.info("=" * 60)
    log.info("  Fills topic: %s", FILLS_TOPIC)
    log.info("  API port:    %d", PORT)
    log.info("  Endpoints:")
    log.info("    /api/analytics  — full performance analytics")
    log.info("    /api/equity     — equity curve time series")
    log.info("    /api/trades     — recent round-trip trades")
    log.info("  Metrics: Sharpe, Sortino, CVaR, Calmar, Kelly,")
    log.info("           Monte Carlo permutation, correlation matrix")
    log.info("=" * 60)

    consumer_thread = threading.Thread(target=consume_fills, daemon=True)
    consumer_thread.start()

    server = HTTPServer(("0.0.0.0", PORT), OdinHandler)
    server.timeout = 1
    log.info("Odin HTTP server listening on :%d", PORT)

    while not shutdown:
        server.handle_request()

    server.server_close()
    log.info("Odin shutdown complete")


if __name__ == "__main__":
    main()
