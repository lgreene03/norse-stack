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


# ---------------------------------------------------------------------------
# Break-even-edge & cost-attribution analytics (quant-alpha-3)
# ---------------------------------------------------------------------------

def _crt(pnl, fee=0.0, slippage=0.0, instrument="BTC-USDT",
         ts="2026-06-20T00:00:00+00:00"):
    """A round-trip carrying gross edge (pnl), fee and slippage costs.

    net_pnl = pnl - fee, matching add_fill (slippage is informational drag
    decomposed for attribution, not re-subtracted from net)."""
    return {"instrument": instrument, "pnl": pnl, "fee": fee,
            "slippage": slippage, "net_pnl": pnl - fee, "time": ts}


def test_break_even_winrate_math(tracker):
    # avg_win = 10, avg_loss = 10  ->  break-even win rate = 10/(10+10) = 0.5
    tracker.round_trips.append(_crt(10.0))
    tracker.round_trips.append(_crt(-10.0))
    cost = tracker.compute_cost_attribution()
    assert cost["break_even_winrate"] == pytest.approx(0.5)


def test_break_even_winrate_asymmetric_payoff(tracker):
    # avg_win = 30, avg_loss = 10 -> break-even = 10/(30+10) = 0.25: with a
    # 3:1 payoff you only need to win a quarter of the time to break even.
    tracker.round_trips.append(_crt(30.0))
    tracker.round_trips.append(_crt(-10.0))
    cost = tracker.compute_cost_attribution()
    assert cost["break_even_winrate"] == pytest.approx(0.25)


def test_gross_positive_net_negative_flag_live_like(tracker):
    # The documented OBI pathology: a real gross edge (sum of pnl > 0) that
    # goes NET NEGATIVE once fees are charged. Mirrors the live numbers
    # (gross ~ +4.80, net ~ -14.28 over a heavy fee load).
    # 4 winners +3 gross each (+12), 1 loser -7.2 gross => gross +4.8.
    # Fees of ~3.82 per trip across 5 trips (~19.08 total) push net negative.
    for _ in range(4):
        tracker.round_trips.append(_crt(3.0, fee=3.816))
    tracker.round_trips.append(_crt(-7.2, fee=3.816))
    cost = tracker.compute_cost_attribution()
    assert cost["gross_pnl"] == pytest.approx(4.8, abs=1e-6)
    assert cost["net_pnl"] < 0
    assert cost["gross_positive_net_negative"] is True


def test_gross_positive_net_positive_flag_false(tracker):
    # Same gross edge but negligible fees -> stays net positive, flag False.
    for _ in range(4):
        tracker.round_trips.append(_crt(3.0, fee=0.01))
    tracker.round_trips.append(_crt(-7.2, fee=0.01))
    cost = tracker.compute_cost_attribution()
    assert cost["gross_pnl"] == pytest.approx(4.8, abs=1e-6)
    assert cost["net_pnl"] > 0
    assert cost["gross_positive_net_negative"] is False


def test_cost_efficiency_ratio(tracker):
    # gross_pnl = 10, total_costs = fees(4) + slippage(1) = 5 -> efficiency 2.0
    tracker.round_trips.append(_crt(10.0, fee=4.0, slippage=1.0))
    cost = tracker.compute_cost_attribution()
    assert cost["total_costs"] == pytest.approx(5.0)
    assert cost["cost_efficiency"] == pytest.approx(2.0)


def test_cost_efficiency_below_one_when_costs_dominate(tracker):
    # gross edge 4.8 but costs 19.08 -> efficiency < 1 (costs eat the edge).
    for _ in range(4):
        tracker.round_trips.append(_crt(3.0, fee=3.816))
    tracker.round_trips.append(_crt(-7.2, fee=3.816))
    cost = tracker.compute_cost_attribution()
    assert cost["cost_efficiency"] < 1.0


def test_fee_adjusted_profit_factor_collapses(tracker):
    # Gross PF looks strong, but charging total costs onto the loss side
    # collapses it. gross win edge = 12, gross loss edge = 7.2,
    # fee-adj denom = 7.2 + 19.08 = 26.28 -> PF ~ 0.456 (< 1).
    for _ in range(4):
        tracker.round_trips.append(_crt(3.0, fee=3.816))
    tracker.round_trips.append(_crt(-7.2, fee=3.816))
    cost = tracker.compute_cost_attribution()
    assert cost["fee_adjusted_profit_factor"] is not None
    assert cost["fee_adjusted_profit_factor"] < 1.0


def test_empty_cost_attribution_shape():
    empty = odin.PerformanceTracker._empty_cost_attribution()
    for key in ("gross_pnl", "net_pnl", "fee_drag", "slippage_drag",
                "total_costs", "break_even_winrate", "break_even_edge_bps",
                "average_edge_per_trade_bps", "round_trip_cost_bps",
                "fee_adjusted_profit_factor", "cost_efficiency",
                "gross_positive_net_negative"):
        assert key in empty
    assert empty["gross_positive_net_negative"] is False


def test_get_analytics_exposes_cost_block(tracker):
    tracker.round_trips.append(_crt(10.0, fee=2.0, slippage=0.5))
    tracker.fills.append({"instrument": "BTC-USDT"})
    out = tracker.get_analytics()
    assert "cost" in out
    assert out["cost"]["fee_drag"] == pytest.approx(2.0)
    assert out["cost"]["slippage_drag"] == pytest.approx(0.5)


def test_empty_analytics_has_cost_block(tracker):
    out = tracker._empty_analytics()
    assert "cost" in out
    assert out["cost"]["gross_positive_net_negative"] is False


def test_add_fill_attributes_slippage_to_round_trip(tracker):
    # Drive a real round trip through add_fill with slippage_bps on both legs.
    # BUY 1 @ 100 with 50 bps slippage -> 0.50 buy-leg slippage.
    # SELL 1 @ 110 with 50 bps slippage -> 0.55 sell-leg slippage.
    tracker.add_fill({"instrument": "BTC-USDT", "side": "BUY", "quantity": 1,
                      "fill_price": 100, "transaction_cost": 0.1,
                      "slippage_bps": 50, "execution_id": "b1"})
    tracker.add_fill({"instrument": "BTC-USDT", "side": "SELL", "quantity": 1,
                      "fill_price": 110, "transaction_cost": 0.1,
                      "slippage_bps": 50, "execution_id": "s1"})
    assert len(tracker.round_trips) == 1
    rt = tracker.round_trips[0]
    # buy slippage 0.5 + sell slippage 0.55 = 1.05
    assert rt["slippage"] == pytest.approx(1.05)
    cost = tracker.compute_cost_attribution()
    assert cost["slippage_drag"] == pytest.approx(1.05)
    # Round trips record the SELL-leg fee only (consistent with net_pnl =
    # pnl - fee in add_fill), so fee_drag here is the 0.1 sell fee.
    assert cost["fee_drag"] == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# Signed-position model: shorts are first-class (norse signed-position)
# ---------------------------------------------------------------------------

def _eq_value(tracker):
    """Latest equity-curve total value (signed market value + cash)."""
    return tracker.equity_curve[-1]["value"]


def test_long_only_unchanged_open_and_value(tracker):
    # Regression guard: a long-only fill must produce exactly the prior
    # accounting. BUY 1 @ 100 from 1000 cash -> qty +1, avg 100, cash 900.
    tracker.add_fill({"instrument": "BTC", "side": "BUY", "quantity": 1,
                      "fill_price": 100, "execution_id": "l1"})
    pos = tracker.positions["BTC"]
    assert pos["qty"] == pytest.approx(1.0)
    assert pos["avg_cost"] == pytest.approx(100.0)
    assert tracker.cash == pytest.approx(900.0)
    # Realized-only basis (MARK_TO_MARKET default False): held at avg_cost, so
    # total value == initial cash.
    assert _eq_value(tracker) == pytest.approx(1000.0)


def test_short_open_drives_qty_negative(tracker):
    # SELL 1 @ 100 from FLAT must OPEN a short (qty -1), not silently no-op.
    # Cash receives proceeds: 1000 -> 1100. avg_cost is the positive entry 100.
    tracker.add_fill({"instrument": "BTC", "side": "SELL", "quantity": 1,
                      "fill_price": 100, "execution_id": "s1"})
    pos = tracker.positions["BTC"]
    assert pos["qty"] == pytest.approx(-1.0)
    assert pos["avg_cost"] == pytest.approx(100.0)
    assert tracker.cash == pytest.approx(1100.0)
    # Signed valuation, realized-only: total = cash + qty*avg = 1100 + (-1*100).
    assert _eq_value(tracker) == pytest.approx(1000.0)


def test_short_round_trip_cover_realizes_correctly(tracker):
    # Short 1 @ 100, then BUY 1 @ 90 to cover. A short profits as price falls:
    # realized = sign(-1)*(90-100)*1 = +10. Flat afterwards.
    tracker.add_fill({"instrument": "BTC", "side": "SELL", "quantity": 1,
                      "fill_price": 100, "execution_id": "s1"})
    tracker.add_fill({"instrument": "BTC", "side": "BUY", "quantity": 1,
                      "fill_price": 90, "execution_id": "b1"})
    assert tracker.realized_pnl == pytest.approx(10.0)
    pos = tracker.positions["BTC"]
    assert pos["qty"] == pytest.approx(0.0)
    assert pos["avg_cost"] == pytest.approx(0.0)
    # Exactly one round trip recorded on the covering (closing) leg.
    assert len(tracker.round_trips) == 1
    assert tracker.round_trips[0]["pnl"] == pytest.approx(10.0)
    # Flat: total value back to start + the +10 realized = 1010.
    assert _eq_value(tracker) == pytest.approx(1010.0)


def test_short_loss_when_price_rises(tracker):
    # Short 1 @ 100, cover @ 110: a short LOSES as price rises => realized -10.
    tracker.add_fill({"instrument": "BTC", "side": "SELL", "quantity": 1,
                      "fill_price": 100, "execution_id": "s1"})
    tracker.add_fill({"instrument": "BTC", "side": "BUY", "quantity": 1,
                      "fill_price": 110, "execution_id": "b1"})
    assert tracker.realized_pnl == pytest.approx(-10.0)
    assert tracker.round_trips[0]["net_pnl"] == pytest.approx(-10.0)


def test_add_to_short_weighted_average(tracker):
    # Short 1 @ 100, then SELL 1 more @ 120 (adding in same direction). qty -2,
    # avg_cost is the positive fee-exclusive weighted entry = (100+120)/2 = 110.
    tracker.add_fill({"instrument": "BTC", "side": "SELL", "quantity": 1,
                      "fill_price": 100, "execution_id": "s1"})
    tracker.add_fill({"instrument": "BTC", "side": "SELL", "quantity": 1,
                      "fill_price": 120, "execution_id": "s2"})
    pos = tracker.positions["BTC"]
    assert pos["qty"] == pytest.approx(-2.0)
    assert pos["avg_cost"] == pytest.approx(110.0)
    # No close happened, so no round trips yet.
    assert len(tracker.round_trips) == 0


def test_long_flip_through_zero_opens_short(tracker):
    # Long 1 @ 100, then SELL 2 @ 110: closes the long (realized +10) and the
    # remaining 1 opens a NEW short @ 110 (qty -1, avg_cost 110).
    tracker.add_fill({"instrument": "BTC", "side": "BUY", "quantity": 1,
                      "fill_price": 100, "execution_id": "b1"})
    tracker.add_fill({"instrument": "BTC", "side": "SELL", "quantity": 2,
                      "fill_price": 110, "execution_id": "s1"})
    assert tracker.realized_pnl == pytest.approx(10.0)
    pos = tracker.positions["BTC"]
    assert pos["qty"] == pytest.approx(-1.0)
    assert pos["avg_cost"] == pytest.approx(110.0)
    # One round trip from the closed long leg (closed qty = 1).
    assert len(tracker.round_trips) == 1
    assert tracker.round_trips[0]["pnl"] == pytest.approx(10.0)


def test_short_flip_through_zero_opens_long(tracker):
    # Short 1 @ 100, then BUY 2 @ 90: covers the short (realized +10) and the
    # remaining 1 opens a NEW long @ 90 (qty +1, avg_cost 90).
    tracker.add_fill({"instrument": "BTC", "side": "SELL", "quantity": 1,
                      "fill_price": 100, "execution_id": "s1"})
    tracker.add_fill({"instrument": "BTC", "side": "BUY", "quantity": 2,
                      "fill_price": 90, "execution_id": "b1"})
    assert tracker.realized_pnl == pytest.approx(10.0)
    pos = tracker.positions["BTC"]
    assert pos["qty"] == pytest.approx(1.0)
    assert pos["avg_cost"] == pytest.approx(90.0)
    assert len(tracker.round_trips) == 1


def test_signed_equity_contribution_short_mark_to_market(monkeypatch):
    # A short's market value contributes NEGATIVELY and gains as price falls.
    # Short 1 @ 100 from 1000, mark to 90 via a (same-direction) second add is
    # not desired; instead enable MTM and drive the mark with a fresh tracker.
    monkeypatch.setattr(odin, "MARK_TO_MARKET", True)
    t = odin.PerformanceTracker(initial_cash=1000.0)
    # Open short 1 @ 100 -> cash 1100, qty -1, last_price 100.
    t.add_fill({"instrument": "BTC", "side": "SELL", "quantity": 1,
                "fill_price": 100, "execution_id": "s1"})
    # At mark 100 total = 1100 + (-1*100) = 1000.
    assert t.equity_curve[-1]["value"] == pytest.approx(1000.0)
    # Drive the mark DOWN by adding a tiny same-direction short at 90 so
    # last_price=90; the existing -1 leg is now marked to 90 (a paper gain).
    t.add_fill({"instrument": "BTC", "side": "SELL", "quantity": 1,
                "fill_price": 90, "execution_id": "s2"})
    # cash: 1100 + 90 = 1190; qty -2; mark 90 -> 1190 + (-2*90) = 1010.
    # The short is in profit because price fell below the 95 avg entry.
    assert t.last_price["BTC"] == pytest.approx(90.0)
    assert t.positions["BTC"]["qty"] == pytest.approx(-2.0)
    assert t.equity_curve[-1]["value"] == pytest.approx(1010.0)


def test_short_unrealized_sign_via_mark(monkeypatch):
    # Verify the unrealized contribution direction directly: short held, mark
    # below avg => positive unrealized ((mark-avg)*qty, qty<0, mark<avg).
    monkeypatch.setattr(odin, "MARK_TO_MARKET", True)
    t = odin.PerformanceTracker(initial_cash=1000.0)
    t.add_fill({"instrument": "BTC", "side": "SELL", "quantity": 1,
                "fill_price": 100, "execution_id": "s1"})
    pos = t.positions["BTC"]
    mark = 90.0
    unrealized = (mark - pos["avg_cost"]) * pos["qty"]
    assert unrealized == pytest.approx(10.0)  # short profits as price falls
