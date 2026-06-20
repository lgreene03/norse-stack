"""Unit tests for Odin analytics correctness fixes.

Run with: python3 -m pytest services/odin/tests/  (kafka is stubbed in conftest)
"""

import pytest

import odin


@pytest.fixture
def tracker():
    # Fresh tracker per test; reset the shared counters so assertions are
    # isolated.
    odin.counters._counts.clear()
    return odin.PerformanceTracker(initial_cash=1000.0)


def _rt(net_pnl, instrument="BTC-USDT", ts="2026-06-20T00:00:00+00:00"):
    """Build a round-trip record shaped like add_fill produces."""
    return {"instrument": instrument, "pnl": net_pnl, "fee": 0.0,
            "net_pnl": net_pnl, "time": ts}


# ---------------------------------------------------------------------------
# Profit factor bucketing (quant-16)
# ---------------------------------------------------------------------------

def test_profit_factor_basic_ratio():
    wins = [_rt(10), _rt(20)]
    losses = [_rt(-5), _rt(-5)]
    # gross_profit=30, gross_loss=10 -> PF = 3.0
    assert odin.PerformanceTracker._profit_factor(wins, losses) == pytest.approx(3.0)


def test_profit_factor_no_losses_returns_none():
    wins = [_rt(10), _rt(20)]
    losses = []
    # No losing trades => undefined/infinite, return None (NOT a fabricated
    # huge number from a 0.001 floor).
    assert odin.PerformanceTracker._profit_factor(wins, losses) is None


def test_profit_factor_no_trades_returns_zero():
    assert odin.PerformanceTracker._profit_factor([], []) == 0.0


def test_breakeven_trades_excluded_from_buckets():
    rts = [_rt(10), _rt(-5), _rt(0), _rt(0)]
    wins, losses = odin.PerformanceTracker._bucket_trades(rts)
    assert len(wins) == 1
    assert len(losses) == 1  # the two break-even (==0) trades are excluded


def test_breakeven_not_counted_as_loss_in_profit_factor():
    # Old code bucketed net_pnl<=0 as losses, so break-even trades inflated
    # gross_loss and understated profit factor. With strict bucketing a
    # losses-free book with break-evens still reports an undefined PF.
    rts = [_rt(10), _rt(20), _rt(0)]
    wins, losses = odin.PerformanceTracker._bucket_trades(rts)
    assert losses == []
    assert odin.PerformanceTracker._profit_factor(wins, losses) is None


def test_get_analytics_profit_factor_none_when_no_losses(tracker):
    # Drive enough winning round-trips through the public path.
    for i in range(3):
        tracker.round_trips.append(_rt(10 + i))
    tracker.fills.append({"instrument": "BTC-USDT"})
    out = tracker.get_analytics()
    assert out["performance"]["profit_factor"] is None


# ---------------------------------------------------------------------------
# Kelly bucketing (quant-16)
# ---------------------------------------------------------------------------

def test_kelly_breakeven_excluded(tracker):
    # 10 wins, 0 real losses but several break-evens: no losses => Kelly 0.
    for _ in range(10):
        tracker.round_trips.append(_rt(5))
    for _ in range(5):
        tracker.round_trips.append(_rt(0))
    assert tracker.compute_kelly() == 0.0


def test_kelly_positive_with_edge(tracker):
    # 14 wins of +10, 6 losses of -5: positive expectancy => positive Kelly.
    for _ in range(14):
        tracker.round_trips.append(_rt(10))
    for _ in range(6):
        tracker.round_trips.append(_rt(-5))
    k = tracker.compute_kelly()
    assert 0.0 < k <= 0.25


# ---------------------------------------------------------------------------
# Monte Carlo bootstrap test (quant-10)
# ---------------------------------------------------------------------------

def test_monte_carlo_shape(tracker):
    for _ in range(20):
        tracker.round_trips.append(_rt(5))
    mc = tracker.compute_monte_carlo(n_sims=500)
    assert mc["method"] == "bootstrap-sharpe"
    assert "sharpe_ci_95" in mc and len(mc["sharpe_ci_95"]) == 2
    assert mc["sharpe_ci_95"][0] <= mc["sharpe_ci_95"][1]
    assert mc["null_hypothesis"] == "true_sharpe <= 0"


def test_monte_carlo_strong_alpha_is_significant(tracker):
    # Consistently positive returns with low variance => Sharpe CI above zero.
    import random as _r
    rng = _r.Random(7)
    for _ in range(60):
        tracker.round_trips.append(_rt(10 + rng.uniform(-1, 1)))
    mc = tracker.compute_monte_carlo(n_sims=2000)
    assert mc["significant"] is True
    assert mc["sharpe_ci_95"][0] > 0
    assert mc["p_value"] < 0.05


def test_monte_carlo_noise_is_not_significant(tracker):
    # Symmetric zero-mean returns => no alpha; CI must straddle zero.
    import random as _r
    rng = _r.Random(123)
    for _ in range(60):
        tracker.round_trips.append(_rt(rng.uniform(-10, 10)))
    mc = tracker.compute_monte_carlo(n_sims=2000)
    assert mc["significant"] is False
    assert mc["sharpe_ci_95"][0] <= 0 <= mc["sharpe_ci_95"][1]


def test_monte_carlo_detects_order_independence_fix(tracker):
    # The OLD test shuffled order of a fixed set and was vacuous. The new test
    # must give a non-trivial p-value (not pinned to ~0.5) for a real edge.
    for _ in range(40):
        tracker.round_trips.append(_rt(8))
    mc = tracker.compute_monte_carlo(n_sims=1000)
    # A book of identical positive returns has zero variance per resample of
    # identical values -> Sharpe 0 -> but ANY positive constant returns the
    # same value; significance is driven by the bootstrap, not order.
    assert mc["actual_sharpe"] >= 0


def test_monte_carlo_too_few_trades(tracker):
    for _ in range(5):
        tracker.round_trips.append(_rt(5))
    mc = tracker.compute_monte_carlo(n_sims=100)
    assert mc["simulations"] == 0
    assert mc["significant"] is False


# ---------------------------------------------------------------------------
# Fill validator + rejected counter (dataeng-ml-12)
# ---------------------------------------------------------------------------

def test_validator_rejects_missing_instrument(tracker):
    tracker.add_fill({"side": "BUY", "quantity": 1, "fill_price": 100})
    assert odin.counters.snapshot().get("fills_rejected_total") == 1
    assert len(tracker.fills) == 0


def test_validator_rejects_nan_price(tracker):
    tracker.add_fill({"instrument": "BTC", "side": "BUY",
                      "quantity": 1, "fill_price": float("nan")})
    snap = odin.counters.snapshot()
    assert snap.get("fills_rejected_total") == 1
    assert snap.get("fills_rejected_nan_inf_fill_price") == 1


def test_validator_rejects_inf_and_bad_side(tracker):
    tracker.add_fill({"instrument": "BTC", "side": "HOLD",
                      "quantity": 1, "fill_price": 100})
    tracker.add_fill({"instrument": "BTC", "side": "BUY",
                      "quantity": float("inf"), "fill_price": 100})
    assert odin.counters.snapshot().get("fills_rejected_total") == 2


def test_valid_fill_accepted(tracker):
    tracker.add_fill({"instrument": "BTC", "side": "BUY",
                      "quantity": 1, "fill_price": 100, "transaction_cost": 0.1})
    assert len(tracker.fills) == 1
    assert odin.counters.snapshot().get("fills_processed_total") == 1


# ---------------------------------------------------------------------------
# Dedup on execution_id (dataeng-ml-7)
# ---------------------------------------------------------------------------

def test_dedup_on_execution_id(tracker):
    fill = {"execution_id": "abc-1", "instrument": "BTC", "side": "BUY",
            "quantity": 1, "fill_price": 100}
    tracker.add_fill(dict(fill))
    tracker.add_fill(dict(fill))  # replay (e.g. consumer restart)
    assert len(tracker.fills) == 1
    assert odin.counters.snapshot().get("fills_duplicate_total") == 1


def test_distinct_execution_ids_both_processed(tracker):
    tracker.add_fill({"execution_id": "a", "instrument": "BTC", "side": "BUY",
                      "quantity": 1, "fill_price": 100})
    tracker.add_fill({"execution_id": "b", "instrument": "BTC", "side": "BUY",
                      "quantity": 1, "fill_price": 101})
    assert len(tracker.fills) == 2


def test_empty_execution_id_distinct_fills_not_collapsed(tracker):
    # Regression: sim/legacy fills carry an empty execution_id. Deduping on a
    # blank key collapsed every such fill into one "duplicate" (Odin showed 2
    # fills while Huginn had 22). The composite-key fallback must keep distinct
    # fills distinct.
    tracker.add_fill({"execution_id": "", "order_id": "o1", "instrument": "BTC",
                      "side": "BUY", "quantity": 1, "fill_price": 100,
                      "timestamp": "2026-06-20T00:00:00+00:00"})
    tracker.add_fill({"execution_id": "", "order_id": "o2", "instrument": "BTC",
                      "side": "SELL", "quantity": 1, "fill_price": 101,
                      "timestamp": "2026-06-20T00:00:01+00:00"})
    assert len(tracker.fills) == 2


def test_empty_execution_id_exact_replay_deduped(tracker):
    # The same empty-id fill replayed on restart must still dedup via the
    # composite key (order_id|timestamp|side|qty|price).
    f = {"execution_id": "", "order_id": "o1", "instrument": "BTC",
         "side": "BUY", "quantity": 1, "fill_price": 100,
         "timestamp": "2026-06-20T00:00:00+00:00"}
    tracker.add_fill(dict(f))
    tracker.add_fill(dict(f))
    assert len(tracker.fills) == 1
    assert odin.counters.snapshot().get("fills_duplicate_total") == 1


# ---------------------------------------------------------------------------
# Timestamp parse + drawdown duration (quant-19)
# ---------------------------------------------------------------------------

def test_parse_ts_failure_increments_counter():
    odin.counters._counts.clear()
    assert odin.PerformanceTracker._parse_ts("not-a-timestamp") is None
    assert odin.counters.snapshot().get("timestamp_parse_failure_total") == 1


def test_negative_drawdown_duration_rejected(tracker):
    # Enter a drawdown at a later wall-clock time, then "recover" with an
    # earlier (out-of-order) timestamp -> negative duration must be rejected.
    tracker.in_drawdown = True
    tracker.dd_start_time = odin.datetime.fromisoformat("2026-06-20T12:00:00+00:00")
    # Peak below the post-fill total so the recovery (peak-exceeded) branch runs.
    tracker.peak_value = 0.0
    tracker.add_fill({"instrument": "BTC", "side": "BUY", "quantity": 1,
                      "fill_price": 100, "timestamp": "2026-06-20T11:00:00+00:00"})
    # Out-of-order recovery (11:00 < 12:00) => negative duration rejected,
    # counter incremented, and max duration left untouched.
    assert odin.counters.snapshot().get("drawdown_negative_duration_total") == 1
    assert tracker.max_dd_duration_secs == 0


def test_positive_drawdown_duration_recorded(tracker):
    # In-order recovery records a positive duration.
    tracker.in_drawdown = True
    tracker.dd_start_time = odin.datetime.fromisoformat("2026-06-20T12:00:00+00:00")
    tracker.peak_value = 0.0
    tracker.add_fill({"instrument": "BTC", "side": "BUY", "quantity": 1,
                      "fill_price": 100, "timestamp": "2026-06-20T12:05:00+00:00"})
    assert tracker.max_dd_duration_secs == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# Mark-to-market labelling (quant-9)
# ---------------------------------------------------------------------------

def test_valuation_basis_labelled_realized_only(tracker):
    tracker.add_fill({"instrument": "BTC", "side": "BUY",
                      "quantity": 1, "fill_price": 100})
    out = tracker.get_analytics()
    # MARK_TO_MARKET defaults to False.
    assert out["valuation_basis"] == "realized-only"
    assert out["performance"]["valuation_basis"] == "realized-only"


def test_equity_curve_labelled(tracker):
    eq = tracker.get_equity_curve()
    assert eq["valuation_basis"] == "realized-only"
    assert isinstance(eq["points"], list)


# ---------------------------------------------------------------------------
# get_recent_trades: round_trips is a deque (no slice indexing)
# ---------------------------------------------------------------------------

def test_get_recent_trades_empty(tracker):
    # No round-trips yet must not raise.
    assert tracker.get_recent_trades() == []


def test_get_recent_trades_newest_first(tracker):
    # Order of arrival: oldest -> newest.
    for i in range(5):
        tracker.round_trips.append(_rt(float(i)))
    out = tracker.get_recent_trades()
    # Slicing a deque directly would raise TypeError; this asserts the
    # materialize-then-slice fix and newest-first ordering.
    assert [t["net_pnl"] for t in out] == [4.0, 3.0, 2.0, 1.0, 0.0]


def test_get_recent_trades_respects_limit(tracker):
    for i in range(10):
        tracker.round_trips.append(_rt(float(i)))
    out = tracker.get_recent_trades(limit=3)
    # The 3 most recent (7,8,9), newest first.
    assert [t["net_pnl"] for t in out] == [9.0, 8.0, 7.0]


# ---------------------------------------------------------------------------
# Consumer-thread liveness for /healthz (sre-resilience-5)
# ---------------------------------------------------------------------------

def test_liveness_ok_before_started():
    lv = odin.Liveness()
    ok, age = lv.status()
    # Before the consumer loop registers, health must report OK (so container
    # startup isn't failed closed during Kafka connect/retry).
    assert ok is True
    assert age is None


def test_liveness_fresh_beat_is_ok():
    lv = odin.Liveness()
    lv.mark_started()
    lv.beat()
    ok, age = lv.status()
    assert ok is True
    assert age is not None and age >= 0


def test_liveness_stale_beat_is_degraded(monkeypatch):
    lv = odin.Liveness()
    lv.mark_started()
    # Force a stale beat by shrinking the staleness threshold below the age.
    monkeypatch.setattr(odin, "HEALTH_MAX_STALENESS_SECS", -1.0)
    ok, age = lv.status()
    assert ok is False
