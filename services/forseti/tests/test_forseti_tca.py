"""Unit tests for Forseti transaction-cost-analysis correctness.

Run with: python3 -m pytest services/forseti/tests/  (kafka is stubbed in
conftest). Tests seed fills DIRECTLY into the tracker — no Kafka, no DB.
"""

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
