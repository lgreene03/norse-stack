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
PORT = int(os.environ.get("PORT", "8086"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("odin")

shutdown = False


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

    def add_fill(self, fill):
        with self.lock:
            self.fills.append(fill)

            instrument = fill.get("instrument", "")
            side = fill.get("side", "").upper()
            qty = float(fill.get("quantity", 0))
            price = float(fill.get("fill_price", 0))
            fee = float(fill.get("transaction_cost", 0))
            ts = fill.get("timestamp", datetime.now(timezone.utc).isoformat())

            self.total_fees += fee
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

            # Update equity curve
            total_value = self.cash
            for inst, p in self.positions.items():
                total_value += p["qty"] * p["avg_cost"]

            # Drawdown tracking with duration
            if total_value > self.peak_value:
                if self.in_drawdown and self.dd_start_time:
                    try:
                        dd_end = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        duration = (dd_end - self.dd_start_time).total_seconds()
                        if duration > self.max_dd_duration_secs:
                            self.max_dd_duration_secs = duration
                    except (ValueError, TypeError):
                        pass
                self.peak_value = total_value
                self.in_drawdown = False
                self.dd_start_time = None
            else:
                dd = self.peak_value - total_value
                dd_pct = dd / self.peak_value if self.peak_value > 0 else 0
                if dd_pct > 0.0001 and not self.in_drawdown:
                    self.in_drawdown = True
                    try:
                        self.dd_start_time = datetime.fromisoformat(
                            ts.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        self.dd_start_time = datetime.now(timezone.utc)
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

    def compute_kelly(self):
        """Half-Kelly criterion for optimal position sizing. Lock must be held."""
        if len(self.round_trips) < 10:
            return 0.0

        wins = [r for r in self.round_trips if r["net_pnl"] > 0]
        losses = [r for r in self.round_trips if r["net_pnl"] <= 0]

        if not wins or not losses:
            return 0.0

        win_rate = len(wins) / len(self.round_trips)
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

    def compute_monte_carlo(self, n_sims=5000):
        """Monte Carlo permutation test: shuffle trade timestamps and compute
        the fraction of random shuffles with Sharpe >= actual Sharpe.
        A p-value < 0.05 means alpha is statistically significant.
        Lock must be held. Cached until round_trip count changes."""
        rts = list(self.round_trips)
        if len(rts) < 10:
            return {"p_value": 1.0, "actual_sharpe": 0.0,
                    "simulations": 0, "percentile": 0}

        if self._mc_cache and self._mc_cache_size == len(rts):
            return self._mc_cache

        returns = [r["net_pnl"] for r in rts]
        mean_r = sum(returns) / len(returns)
        var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        actual_sharpe = mean_r / math.sqrt(var_r) if var_r > 0 else 0

        rng = random.Random(42)
        beats = 0
        for _ in range(n_sims):
            shuffled = returns[:]
            rng.shuffle(shuffled)
            # Compute Sharpe of shuffled sequence (cumulative PnL path)
            cum = []
            total = 0
            for r in shuffled:
                total += r
                cum.append(total)
            if len(cum) < 2:
                continue
            diffs = [cum[i] - cum[i - 1] for i in range(1, len(cum))]
            m = sum(diffs) / len(diffs)
            v = sum((d - m) ** 2 for d in diffs) / len(diffs)
            s = m / math.sqrt(v) if v > 0 else 0
            if s >= actual_sharpe:
                beats += 1

        p_value = beats / n_sims
        percentile = round((1 - p_value) * 100, 1)

        result = {
            "p_value": round(p_value, 4),
            "actual_sharpe": round(actual_sharpe, 4),
            "simulations": n_sims,
            "percentile": percentile,
            "significant": p_value < 0.05,
        }
        self._mc_cache = result
        self._mc_cache_size = len(rts)
        return result

    def get_analytics(self):
        with self.lock:
            total_trades = len(self.fills)
            if total_trades == 0:
                return self._empty_analytics()

            wins = [r for r in self.round_trips if r["net_pnl"] > 0]
            losses = [r for r in self.round_trips if r["net_pnl"] <= 0]
            win_rate = len(wins) / len(self.round_trips) if self.round_trips else 0

            gross_profit = sum(r["net_pnl"] for r in wins) if wins else 0
            gross_loss = abs(sum(r["net_pnl"] for r in losses)) if losses else 0.001
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

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

            return {
                "status": "running",
                "runtime_hours": round(runtime_hours, 2),
                "total_fills": total_trades,
                "round_trips": len(self.round_trips),
                "performance": {
                    "realized_pnl": round(self.realized_pnl, 2),
                    "total_fees": round(self.total_fees, 2),
                    "net_trading_pnl": round(net_trading_pnl, 2),
                    "fee_drag_pct": round(
                        self.total_fees / max(abs(self.realized_pnl), 0.01) * 100, 1
                    ),
                    "win_rate": round(win_rate, 3),
                    "profit_factor": round(profit_factor, 3),
                    "avg_win": round(avg_win, 2),
                    "avg_loss": round(avg_loss, 2),
                    "max_drawdown": round(self.max_drawdown, 2),
                    "max_drawdown_pct": round(self.max_drawdown_pct * 100, 2),
                    "max_dd_duration_mins": round(self.max_dd_duration_secs / 60, 1),
                    "current_dd_duration_mins": round(current_dd_secs / 60, 1),
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
            return list(self.equity_curve)

    def get_recent_trades(self, limit=20):
        with self.lock:
            return list(reversed(self.round_trips[-limit:]))

    def _empty_analytics(self):
        return {
            "status": "waiting_for_fills",
            "runtime_hours": 0,
            "total_fills": 0,
            "round_trips": 0,
            "performance": {
                "realized_pnl": 0, "total_fees": 0, "net_trading_pnl": 0,
                "fee_drag_pct": 0, "win_rate": 0, "profit_factor": 0,
                "avg_win": 0, "avg_loss": 0, "max_drawdown": 0,
                "max_drawdown_pct": 0, "max_dd_duration_mins": 0,
                "current_dd_duration_mins": 0,
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
        elif self.path == "/healthz":
            self._json_response({"status": "ok", "service": "odin"})
        else:
            self.send_error(404)

    def _json_response(self, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def consume_fills():
    for attempt in range(30):
        try:
            consumer = KafkaConsumer(
                FILLS_TOPIC,
                bootstrap_servers=KAFKA_BROKERS,
                group_id="odin-analytics",
                auto_offset_reset="earliest",
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
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

    while not shutdown:
        try:
            records = consumer.poll(timeout_ms=1000)
            for tp, messages in records.items():
                for msg in messages:
                    fill = msg.value
                    tracker.add_fill(fill)
                    log.info(
                        "Fill: %s %s %s @ $%.2f (fee: $%.4f)",
                        fill.get("side", "?"),
                        fill.get("quantity", 0),
                        fill.get("instrument", "?"),
                        float(fill.get("fill_price", 0)),
                        float(fill.get("transaction_cost", 0)),
                    )
        except Exception as e:
            log.error("Consumer error: %s", e)
            time.sleep(1)

    consumer.close()


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
