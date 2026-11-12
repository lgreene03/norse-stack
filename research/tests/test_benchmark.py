"""Checks for the volatility-matched equal-weight buy-and-hold benchmark.

The benchmark is a construction the gate defines (docs/NEXT_ITERATION.md), not a
published statistic, so the known answers are hand computations. Synthetic only.
"""

import math
import random

import pytest
from norse_research.benchmark import (
    equal_weight_buy_and_hold,
    excess_returns,
    volatility_matched,
)
from norse_research.sharpe import sharpe_ratio


def test_buy_and_hold_drifts_by_hand():
    # Two assets, half each at the start, never rebalanced within the period.
    # Day 0: 0.5 * 10% = 5%; weights drift to 0.55/1.05 and 0.50/1.05.
    # Day 1: (0.50/1.05) * 10% = 4.7619%; weights drift back to 0.5 and 0.5.
    # Day 2: 0.5 * -5% = -2.5%. Wealth 1.05 * 1.047619 * 0.975 = 1.0725, which is
    # 0.5 * 1.1 * 0.95 + 0.5 * 1.1.
    assets = {"A": [0.10, 0.0, -0.05], "B": [0.0, 0.10, 0.0]}
    out = equal_weight_buy_and_hold(assets, [(0, {"A", "B"})], 3)
    assert out == pytest.approx([0.05, 0.05 / 1.05, -0.025])
    assert math.prod(1 + r for r in out) == pytest.approx(0.5 * 1.1 * 0.95 + 0.5 * 1.1)


def test_rebalance_to_a_new_universe_with_costs_by_hand():
    # Period 1 (days 0 to 1) holds A and B; period 2 (day 2) holds B and C. At 10
    # bps a side: the first purchase trades 100% (cost 0.1%); the rebalance sells
    # A's 0.5 and buys C's 0.5 (B is already 0.5), turnover 1.0, cost 0.1%.
    assets = {
        "A": [0.10, 0.0, None],  # not held on day 2, so a missing return is allowed
        "B": [0.0, 0.10, 0.02],
        "C": [None, None, 0.04],
    }
    out = equal_weight_buy_and_hold(assets, [(0, ["A", "B"]), (2, ["B", "C"])], 3, 0.001)
    assert out[0] == pytest.approx(0.999 * 1.05 - 1)
    assert out[1] == pytest.approx(0.05 / 1.05)
    assert out[2] == pytest.approx(0.999 * (0.5 * 1.02 + 0.5 * 1.04) - 1)


def test_a_missing_return_for_a_held_asset_is_refused():
    assets = {"A": [0.01, None], "B": [0.0, 0.01]}
    with pytest.raises(ValueError, match="A is held on day 1"):
        equal_weight_buy_and_hold(assets, [(0, ["A", "B"])], 2)


def test_period_validation():
    assets = {"A": [0.01, 0.02, 0.03]}
    with pytest.raises(ValueError):
        equal_weight_buy_and_hold(assets, [(1, ["A"])], 3)  # must start at day 0
    with pytest.raises(ValueError):
        equal_weight_buy_and_hold(assets, [(0, ["A"]), (0, ["A"])], 3)
    with pytest.raises(ValueError):
        equal_weight_buy_and_hold(assets, [(0, ["Z"])], 3)
    with pytest.raises(ValueError):
        equal_weight_buy_and_hold(assets, [(0, [])], 3)


def test_volatility_matching_by_hand():
    benchmark = [0.02, -0.02, 0.04, 0.0]
    strategy = [0.01, -0.01, 0.02, 0.0]  # exactly half the benchmark
    matched = volatility_matched(benchmark, strategy)
    assert matched == pytest.approx(strategy)
    assert excess_returns(strategy, matched) == pytest.approx([0.0] * 4, abs=1e-15)


def market(rng, days, drift=0.0015, vol=0.03):
    # A rising, volatile market: three coins sharing a common factor.
    factor = [rng.gauss(drift, vol) for _ in range(days)]
    return {
        name: [f + rng.gauss(0, vol / 2) for f in factor] for name in ("X", "Y", "Z")
    }


def test_market_exposure_alone_does_not_beat_the_benchmark():
    # A long-flat rule with no timing skill (in the market on random days) has a
    # positive Sharpe ratio in a rising market, but its return in excess of the
    # volatility-matched benchmark is negative: exposure is not edge.
    rng = random.Random(11)
    days = 4 * 365
    assets = market(rng, days)
    bench = equal_weight_buy_and_hold(assets, [(0, assets.keys())], days)
    in_market = [rng.random() < 0.5 for _ in range(days)]
    strategy = [b if on else 0.0 for b, on in zip(bench, in_market, strict=True)]
    excess = excess_returns(strategy, volatility_matched(bench, strategy))
    assert sharpe_ratio(strategy) > 0
    assert sharpe_ratio(excess) < 0


def test_a_planted_timing_edge_beats_the_benchmark():
    # The same rule with foresight on a third of its decisions: out of the market
    # on the worst days it would otherwise hold. Its excess return is positive.
    rng = random.Random(12)
    days = 4 * 365
    assets = market(rng, days)
    bench = equal_weight_buy_and_hold(assets, [(0, assets.keys())], days)
    strategy = []
    for b in bench:
        on = rng.random() < 0.5
        if rng.random() < 1 / 3:
            on = b > 0
        strategy.append(b if on else 0.0)
    excess = excess_returns(strategy, volatility_matched(bench, strategy))
    assert sharpe_ratio(excess) > 0
