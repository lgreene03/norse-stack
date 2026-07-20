"""Unit tests for Forseti transaction-cost-analysis correctness.

Run with: python3 -m pytest services/forseti/tests/  (kafka is stubbed in
conftest). Tests seed fills DIRECTLY into the tracker — no Kafka, no DB.
"""

import math
from datetime import datetime, timedelta, timezone

import pytest

import forseti


@pytest.fixture
def tracker():
    # Fresh tracker per test; reset the shared counters so assertions stay
    # isolated.
    forseti.counters._counts.clear()
    return forseti.TCATracker()


def _fill(instrument="BTC-USDT", side="BUY", quantity=1.0, fill_price=100.0,
          transaction_cost=0.0, slippage_bps=0.0, liquidity=None,
          execution_id=None, timestamp="2026-06-22T00:00:01+00:00"):
    """Build a fill record shaped like the live executions.fills.v1 stream."""
    f = {
        "instrument": instrument,
        "side": side,
        "quantity": quantity,
        "fill_price": fill_price,
        "transaction_cost": transaction_cost,
        "slippage_bps": slippage_bps,
        "timestamp": timestamp,
        "execution_id": execution_id or "exec-{}-{}-{}-{}".format(
            instrument, side, fill_price, timestamp
        ),
    }
    if liquidity is not None:
        f["liquidity"] = liquidity
    return f


def _price(instrument, price, timestamp):
    return {"type": "price_tick", "instrument": instrument, "price": price,
            "timestamp": timestamp}


# ---------------------------------------------------------------------------
# (1) Slippage sign convention via arrival benchmark.
#
# A BUY filled ABOVE the arrival price is adverse  -> positive slippage_bps.
# A SELL filled BELOW the arrival price is adverse -> positive slippage_bps.
# The favourable directions must be NEGATIVE. We verify both ways, and that
# arrival benchmarking is preferred only when there's no reported slippage.
# ---------------------------------------------------------------------------

def test_buy_above_arrival_is_positive_adverse_slippage(tracker):
    # Arrival/mid at 100, BUY fills at 101 -> +100 bps adverse.
    tracker.add_price(_price("BTC-USDT", 100.0, "2026-06-22T00:00:00+00:00"))
    tracker.add_fill(_fill(side="BUY", fill_price=101.0, slippage_bps=0.0,
                           timestamp="2026-06-22T00:00:01+00:00"))
    rec = tracker.get_fills()["fills"][0]
    assert rec["slippage_source"] == "arrival"
    assert rec["slippage_bps"] == pytest.approx(100.0, abs=1e-6)
    assert rec["slippage_bps"] > 0


def test_buy_below_arrival_is_negative_favourable_slippage(tracker):
    tracker.add_price(_price("BTC-USDT", 100.0, "2026-06-22T00:00:00+00:00"))
    tracker.add_fill(_fill(side="BUY", fill_price=99.0, slippage_bps=0.0,
                           timestamp="2026-06-22T00:00:01+00:00"))
    rec = tracker.get_fills()["fills"][0]
    assert rec["slippage_bps"] == pytest.approx(-100.0, abs=1e-6)
    assert rec["slippage_bps"] < 0


def test_sell_below_arrival_is_positive_adverse_slippage(tracker):
    # Arrival/mid at 100, SELL fills at 99 (sold cheaper than mid) -> adverse,
    # positive slippage despite the lower price, because sign flips for SELL.
    tracker.add_price(_price("ETH-USDT", 100.0, "2026-06-22T00:00:00+00:00"))
    tracker.add_fill(_fill(instrument="ETH-USDT", side="SELL", fill_price=99.0,
                           slippage_bps=0.0,
                           timestamp="2026-06-22T00:00:01+00:00"))
    rec = tracker.get_fills()["fills"][0]
    assert rec["slippage_source"] == "arrival"
    assert rec["slippage_bps"] == pytest.approx(100.0, abs=1e-6)
    assert rec["slippage_bps"] > 0


def test_sell_above_arrival_is_negative_favourable_slippage(tracker):
    tracker.add_price(_price("ETH-USDT", 100.0, "2026-06-22T00:00:00+00:00"))
    tracker.add_fill(_fill(instrument="ETH-USDT", side="SELL", fill_price=101.0,
                           slippage_bps=0.0,
                           timestamp="2026-06-22T00:00:01+00:00"))
    rec = tracker.get_fills()["fills"][0]
    assert rec["slippage_bps"] == pytest.approx(-100.0, abs=1e-6)
    assert rec["slippage_bps"] < 0


def test_reported_slippage_is_preferred_over_arrival(tracker):
    # A non-zero reported slippage_bps wins; the arrival benchmark is not used.
    tracker.add_price(_price("BTC-USDT", 100.0, "2026-06-22T00:00:00+00:00"))
    tracker.add_fill(_fill(side="BUY", fill_price=101.0, slippage_bps=7.5,
                           timestamp="2026-06-22T00:00:01+00:00"))
    rec = tracker.get_fills()["fills"][0]
    assert rec["slippage_source"] == "reported"
    assert rec["slippage_bps"] == pytest.approx(7.5)


def test_fix_numeric_side_codes_normalize(tracker):
    # FIX convention: 0 = BUY, 1 = SELL. A 0-coded BUY above arrival is adverse.
    tracker.add_price(_price("SOL-USDT", 100.0, "2026-06-22T00:00:00+00:00"))
    tracker.add_fill(_fill(instrument="SOL-USDT", side=0, fill_price=101.0,
                           slippage_bps=0.0,
                           timestamp="2026-06-22T00:00:01+00:00"))
    rec = tracker.get_fills()["fills"][0]
    assert rec["side"] == "BUY"
    assert rec["slippage_bps"] == pytest.approx(100.0, abs=1e-6)


# ---------------------------------------------------------------------------
# (2) Maker/taker ratio from the Liquidity field.
# ---------------------------------------------------------------------------

def test_maker_taker_ratio(tracker):
    for i in range(3):
        tracker.add_fill(_fill(liquidity="maker", execution_id=f"m{i}"))
    for i in range(2):
        tracker.add_fill(_fill(liquidity="taker", execution_id=f"t{i}"))
    tca = tracker.get_tca()
    overall = tca["overall"]
    assert overall["makerCount"] == 3
    assert overall["takerCount"] == 2
    assert overall["makerTakerRatio"] == pytest.approx(1.5)


def test_unknown_liquidity_not_bucketed(tracker):
    # An unrecognized liquidity flag must NOT be silently counted as taker.
    tracker.add_fill(_fill(liquidity="maker", execution_id="m1"))
    tracker.add_fill(_fill(liquidity="weird-venue-code", execution_id="x1"))
    overall = tracker.get_tca()["overall"]
    assert overall["makerCount"] == 1
    assert overall["takerCount"] == 0


# ---------------------------------------------------------------------------
# (3) fee_bps math on a known notional.
# ---------------------------------------------------------------------------

def test_fee_bps_on_known_notional(tracker):
    # Notional = 2 * 50000 = 100_000. Fee = 50 -> 50/100000 * 1e4 = 5 bps.
    tracker.add_fill(_fill(instrument="BTC-USDT", side="BUY", quantity=2.0,
                           fill_price=50000.0, transaction_cost=50.0,
                           slippage_bps=0.0))
    rec = tracker.get_fills()["fills"][0]
    assert rec["notional"] == pytest.approx(100000.0)
    assert rec["fee_bps"] == pytest.approx(5.0)
    # Notional-weighted overall avg fee bps equals 5 for a single fill.
    assert tracker.get_tca()["overall"]["avgFeeBps"] == pytest.approx(5.0)


def test_implementation_shortfall_is_slippage_cost_plus_fees(tracker):
    # Reported slippage 10 bps on notional 100_000 -> slip cost = 100. Fee = 50.
    # implementation_shortfall = 100 + 50 = 150.
    tracker.add_fill(_fill(quantity=2.0, fill_price=50000.0,
                           transaction_cost=50.0, slippage_bps=10.0))
    rec = tracker.get_fills()["fills"][0]
    assert rec["slippage_cost"] == pytest.approx(100.0)
    assert rec["implementation_shortfall"] == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# (4) available:false on zero fills.
# ---------------------------------------------------------------------------

def test_available_false_on_zero_fills(tracker):
    tca = tracker.get_tca()
    assert tca["available"] is False
    assert tca["byInstrument"] == {}
    assert tca["overall"]["totalFills"] == 0
    # /api/tca/fills is also empty.
    assert tracker.get_fills()["available"] is False


# ---------------------------------------------------------------------------
# (5) null slippage + correct basis label when there is NO arrival price.
# ---------------------------------------------------------------------------

def test_null_slippage_and_basis_without_arrival(tracker):
    # No price feed and reported slippage is 0 -> slippage_bps is null, basis
    # is "fees + reported-slippage only", shortfall is fees-only.
    tracker.add_fill(_fill(quantity=2.0, fill_price=50000.0,
                           transaction_cost=50.0, slippage_bps=0.0))
    rec = tracker.get_fills()["fills"][0]
    assert rec["slippage_bps"] is None
    assert rec["slippage_source"] is None
    assert rec["implementation_shortfall"] == pytest.approx(50.0)  # fees only
    tca = tracker.get_tca()
    assert tca["basis"] == forseti.BASIS_NO_ARRIVAL
    # avgSlippageBps is null (never a fabricated 0) when no fill has it defined.
    assert tca["overall"]["avgSlippageBps"] is None
    assert tca["overall"]["slippageDefinedFills"] == 0


def test_basis_flips_to_arrival_when_benchmark_used(tracker):
    tracker.add_price(_price("BTC-USDT", 100.0, "2026-06-22T00:00:00+00:00"))
    tracker.add_fill(_fill(side="BUY", fill_price=101.0, slippage_bps=0.0,
                           timestamp="2026-06-22T00:00:01+00:00"))
    assert tracker.get_tca()["basis"] == forseti.BASIS_WITH_ARRIVAL


def test_stale_arrival_price_is_not_used(tracker):
    # A price far older than ARRIVAL_MAX_AGE_SECS must not benchmark the fill.
    old = datetime(2026, 6, 22, 0, 0, 0, tzinfo=timezone.utc)
    fill_ts = (old + timedelta(seconds=forseti.ARRIVAL_MAX_AGE_SECS + 10))
    tracker.add_price(_price("BTC-USDT", 100.0, old.isoformat()))
    tracker.add_fill(_fill(side="BUY", fill_price=101.0, slippage_bps=0.0,
                           timestamp=fill_ts.isoformat()))
    rec = tracker.get_fills()["fills"][0]
    assert rec["slippage_bps"] is None


# ---------------------------------------------------------------------------
# Aggregation + plumbing.
# ---------------------------------------------------------------------------

def test_per_instrument_and_overall_aggregation(tracker):
    tracker.add_fill(_fill(instrument="BTC-USDT", quantity=1.0, fill_price=100.0,
                           transaction_cost=1.0, liquidity="maker",
                           execution_id="a"))
    tracker.add_fill(_fill(instrument="ETH-USDT", quantity=2.0, fill_price=50.0,
                           transaction_cost=2.0, liquidity="taker",
                           execution_id="b"))
    tca = tracker.get_tca()
    assert tca["available"] is True
    assert set(tca["byInstrument"].keys()) == {"BTC-USDT", "ETH-USDT"}
    assert tca["overall"]["totalFills"] == 2
    assert tca["overall"]["totalFees"] == pytest.approx(3.0)
    # Both fills have notional 100 each -> total 200.
    assert tca["overall"]["totalNotional"] == pytest.approx(200.0)
    assert tca["byInstrument"]["BTC-USDT"]["totalNotional"] == pytest.approx(100.0)


def test_dedup_on_execution_id(tracker):
    f = _fill(execution_id="same-id")
    tracker.add_fill(f)
    tracker.add_fill(dict(f))  # same execution_id -> dropped
    assert tracker.get_tca()["overall"]["totalFills"] == 1
    assert forseti.counters.snapshot().get("fills_duplicate_total") == 1


def test_invalid_fills_rejected(tracker):
    tracker.add_fill({"instrument": "BTC-USDT", "side": "BUY"})  # missing qty/price
    tracker.add_fill(_fill(quantity=-1.0, execution_id="neg"))   # non-positive
    tracker.add_fill(_fill(side="HOLD", execution_id="bad"))     # bad side
    assert tracker.get_tca()["overall"]["totalFills"] == 0
    assert forseti.counters.snapshot().get("fills_rejected_total") == 3


def test_fills_endpoint_newest_first_and_limit(tracker):
    for i in range(5):
        tracker.add_fill(_fill(execution_id=f"e{i}",
                               timestamp=f"2026-06-22T00:00:0{i}+00:00"))
    out = tracker.get_fills(limit=2)
    assert out["count"] == 2
    # Newest-first: the last-added (i=4) comes first.
    assert out["fills"][0]["timestamp"] == "2026-06-22T00:00:04+00:00"
    assert out["fills"][1]["timestamp"] == "2026-06-22T00:00:03+00:00"


def test_maker_taker_ratio_null_when_no_takers(tracker):
    tracker.add_fill(_fill(liquidity="maker", execution_id="m1"))
    overall = tracker.get_tca()["overall"]
    # Ratio undefined (division by zero) -> None, not a fabricated infinity.
    assert overall["makerTakerRatio"] is None


# ===========================================================================
# MARKET-IMPACT + CAPACITY model (the Medallion capacity constraint).
#
# Honest framing: this is a trading SIMULATION with NO measured out-of-sample
# edge (PBO=1.0). These tests verify the MATHEMATICS of the impact/capacity
# technique, not any claim of realisable profit or AUM.
# ===========================================================================

# ---------------------------------------------------------------------------
# (1) Square-root-law temporary impact: monotonicity + the sqrt scaling.
# ---------------------------------------------------------------------------

def test_sqrt_law_rises_with_size():
    small = forseti.sqrt_law_temporary_bps(1.0e6, 1.0e8, 0.02, eta=1.0)
    big = forseti.sqrt_law_temporary_bps(4.0e6, 1.0e8, 0.02, eta=1.0)
    assert big > small


def test_sqrt_law_falls_with_adv():
    thin = forseti.sqrt_law_temporary_bps(1.0e6, 1.0e8, 0.02, eta=1.0)
    deep = forseti.sqrt_law_temporary_bps(1.0e6, 2.0e8, 0.02, eta=1.0)
    # Doubling ADV halves participation -> impact scales by sqrt(1/2).
    assert deep < thin
    assert deep == pytest.approx(thin / math.sqrt(2.0), rel=1e-9)


def test_sqrt_law_scales_linearly_with_sigma():
    base = forseti.sqrt_law_temporary_bps(1.0e6, 1.0e8, 0.02, eta=1.0)
    doubled = forseti.sqrt_law_temporary_bps(1.0e6, 1.0e8, 0.04, eta=1.0)
    assert doubled == pytest.approx(2.0 * base, rel=1e-9)


def test_sqrt_law_four_x_size_is_two_x_impact():
    # A 4x order gives ~2x temporary impact because sqrt(4) == 2.
    one_x = forseti.sqrt_law_temporary_bps(1.0e6, 1.0e8, 0.02, eta=1.0)
    four_x = forseti.sqrt_law_temporary_bps(4.0e6, 1.0e8, 0.02, eta=1.0)
    assert four_x == pytest.approx(2.0 * one_x, rel=1e-9)


def test_sqrt_law_closed_form_value():
    # size/adv = 1e6/1e8 = 0.01, sqrt = 0.1; 1.0 * 0.02 * 0.1 * 1e4 = 20 bps.
    assert forseti.sqrt_law_temporary_bps(1.0e6, 1.0e8, 0.02, eta=1.0) == \
        pytest.approx(20.0, rel=1e-12)


def test_permanent_impact_is_linear_in_size():
    one_x = forseti.permanent_bps(1.0e6, 1.0e8, 0.02, gamma=0.1)
    two_x = forseti.permanent_bps(2.0e6, 1.0e8, 0.02, gamma=0.1)
    # Linear (not sqrt): doubling size doubles permanent impact.
    assert two_x == pytest.approx(2.0 * one_x, rel=1e-9)


def test_impact_endpoint_shape_and_defaults(tracker):
    # Seed a couple of fills so adv/sigma can be estimated from the stream.
    tracker.add_fill(_fill(instrument="BTC-USDT", quantity=1.0, fill_price=100.0,
                           execution_id="i1",
                           timestamp="2026-06-22T00:00:00+00:00"))
    tracker.add_fill(_fill(instrument="BTC-USDT", quantity=1.0, fill_price=101.0,
                           execution_id="i2",
                           timestamp="2026-06-22T01:00:00+00:00"))
    tracker.add_fill(_fill(instrument="BTC-USDT", quantity=1.0, fill_price=100.5,
                           execution_id="i3",
                           timestamp="2026-06-22T02:00:00+00:00"))
    out = forseti.build_impact_response(tracker, instrument="BTC-USDT",
                                        size=1.0e6, adv=1.0e8, sigma=0.02,
                                        eta=1.0)
    assert out["model"] == "sqrt-law + Almgren-Chriss"
    assert out["temporaryBps"] == pytest.approx(20.0, rel=1e-9)
    # permanent = 0.1 * 0.02 * (1e6/1e8) * 1e4 = 0.1*0.02*0.01*1e4 = 0.2 bps.
    assert out["permanentBps"] == pytest.approx(0.2, rel=1e-9)
    assert out["totalBps"] == pytest.approx(20.2, rel=1e-9)
    # Defaults come from tracker estimates when params are omitted.
    dflt = forseti.build_impact_response(tracker, instrument="BTC-USDT")
    assert dflt["adv"] > 0 and dflt["sigma"] > 0 and dflt["size"] > 0


# ---------------------------------------------------------------------------
# (2) Almgren-Chriss optimal-execution schedule.
# ---------------------------------------------------------------------------

def test_ac_schedule_sums_to_parent_size():
    children, _ = forseti.almgren_chriss_schedule(
        size=1000.0, slices=10, risk_aversion=1.0, sigma=0.02)
    assert len(children) == 10
    assert sum(children) == pytest.approx(1000.0, rel=1e-9)


def test_ac_schedule_reduces_to_twap_as_risk_aversion_zero():
    children, kappa = forseti.almgren_chriss_schedule(
        size=1000.0, slices=10, risk_aversion=1e-12, sigma=0.02)
    # Every child ~ equal (uniform / TWAP) and kappa ~ 0.
    assert kappa == pytest.approx(0.0, abs=1e-6)
    for c in children:
        assert c == pytest.approx(100.0, rel=1e-3)


def test_ac_schedule_is_front_loaded_and_more_so_as_risk_aversion_rises():
    low, _ = forseti.almgren_chriss_schedule(
        size=1000.0, slices=10, risk_aversion=0.5, sigma=0.02)
    high, _ = forseti.almgren_chriss_schedule(
        size=1000.0, slices=10, risk_aversion=5.0, sigma=0.02)
    # Front-loaded: first child is the largest and exceeds the uniform 100.
    assert low[0] == max(low)
    assert low[0] > 100.0
    assert high[0] == max(high)
    # More risk-averse -> more front-loaded (bigger first child, smaller last).
    assert high[0] > low[0]
    assert high[-1] < low[-1]
    # Inventory is drawn down monotonically (children never go negative).
    assert all(c >= 0 for c in high)


def test_ac_schedule_endpoint_shape():
    out = forseti.build_schedule_response(size=1000.0, slices=5,
                                          risk_aversion=2.0, sigma=0.02)
    assert out["model"] == "Almgren-Chriss"
    assert out["sliceCount"] == 5
    assert len(out["slices"]) == 5
    # Cumulative reaches the parent size on the final slice.
    assert out["slices"][-1]["cumulative"] == pytest.approx(1000.0, rel=1e-9)
    assert out["slices"][0]["t"] == 1
    # t index is monotonically increasing.
    assert [s["t"] for s in out["slices"]] == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# (3) Capacity crossover: at capacity, modelled impact == assumed edge; a
#     larger size pushes impact above the edge (the edge is eroded).
# ---------------------------------------------------------------------------

def test_capacity_crossover_impact_equals_edge():
    edge = 10.0
    adv, sigma = 1.0e8, 0.02
    cap = forseti.capacity_notional(edge, adv, sigma, eta=1.0, gamma=0.1)
    assert cap is not None and cap > 0
    at_cap = forseti.total_impact_bps(cap, adv, sigma, eta=1.0, gamma=0.1)
    assert at_cap == pytest.approx(edge, rel=1e-6)


def test_capacity_larger_size_erodes_edge():
    edge = 10.0
    adv, sigma = 1.0e8, 0.02
    cap = forseti.capacity_notional(edge, adv, sigma, eta=1.0, gamma=0.1)
    # Just below capacity: impact < edge (edge survives). Above: impact > edge.
    below = forseti.total_impact_bps(0.5 * cap, adv, sigma, eta=1.0, gamma=0.1)
    above = forseti.total_impact_bps(2.0 * cap, adv, sigma, eta=1.0, gamma=0.1)
    assert below < edge
    assert above > edge


def test_capacity_crossover_pure_sqrt_law_when_no_permanent():
    # gamma=0 -> pure sqrt law: a*sqrt(p)=edge closed form.
    edge = 20.0
    adv, sigma = 1.0e8, 0.02
    cap = forseti.capacity_notional(edge, adv, sigma, eta=1.0, gamma=0.0)
    at_cap = forseti.total_impact_bps(cap, adv, sigma, eta=1.0, gamma=0.0)
    assert at_cap == pytest.approx(edge, rel=1e-9)
    # edge 20 bps, temp = 1*0.02*sqrt(p)*1e4 = 20 -> sqrt(p)=0.1 -> p=0.01.
    assert cap == pytest.approx(0.01 * adv, rel=1e-9)


def test_capacity_endpoint_shape_and_crossover(tracker):
    tracker.add_fill(_fill(instrument="ETH-USDT", quantity=1.0, fill_price=100.0,
                           execution_id="c1",
                           timestamp="2026-06-22T00:00:00+00:00"))
    tracker.add_fill(_fill(instrument="ETH-USDT", quantity=1.0, fill_price=101.0,
                           execution_id="c2",
                           timestamp="2026-06-22T01:00:00+00:00"))
    tracker.add_fill(_fill(instrument="ETH-USDT", quantity=1.0, fill_price=100.5,
                           execution_id="c3",
                           timestamp="2026-06-22T02:00:00+00:00"))
    out = forseti.build_capacity_response(tracker, edge_bps=10.0)
    assert out["assumedEdgeBps"] == 10.0
    assert "ETH-USDT" in out["byInstrument"]
    inst = out["byInstrument"]["ETH-USDT"]
    assert inst["capacityNotional"] > 0
    # At the returned crossover, modelled impact ~= the assumed edge.
    assert inst["crossover"]["impactBps"] == pytest.approx(10.0, rel=1e-4)
    # Curve is monotonically increasing in size.
    curve = inst["curve"]
    assert len(curve) > 2
    for a, b in zip(curve, curve[1:]):
        assert b["size"] > a["size"]
        assert b["impactBps"] >= a["impactBps"]


# ---------------------------------------------------------------------------
# (4) Honest labels: the assumed-edge disclaimer must be present.
# ---------------------------------------------------------------------------

def test_capacity_carries_honest_edge_labels(tracker):
    tracker.add_fill(_fill(execution_id="h1"))
    out = forseti.build_capacity_response(tracker, edge_bps=15.0)
    assert "no measured out-of-sample edge" in out["assumedEdgeLabel"]
    assert "assumed edge = 15.0 bps" in out["assumedEdgeLabel"]
    assert "illustrative; no measured edge" in out["note"]
    # The module-level label helper is explicit about the assumption.
    lbl = forseti.assumed_edge_label(7.0)
    assert "assumed edge = 7.0 bps" in lbl
    assert "PBO=1.0" in lbl


def test_capacity_falls_back_to_default_instrument_when_none_tracked(tracker):
    # No fills seeded -> still returns a usable default-instrument capacity.
    out = forseti.build_capacity_response(tracker, edge_bps=10.0)
    assert forseti.DEFAULT_INSTRUMENT in out["byInstrument"]
    inst = out["byInstrument"][forseti.DEFAULT_INSTRUMENT]
    assert inst["capacityNotional"] > 0
    assert inst["advSource"].startswith("default")
    assert inst["sigmaSource"].startswith("default")
