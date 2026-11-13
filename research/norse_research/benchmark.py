"""The volatility-matched, equal-weight buy-and-hold benchmark (G1.3).

Source: docs/NEXT_ITERATION.md: the deflated Sharpe criterion must also hold "for
returns in excess of a volatility-matched, equal-weight buy-and-hold of the same
universe, because a long-flat trend rule can otherwise pass on market exposure
alone". It is a construction the gate defines, not a published statistic, so there
is no published worked example; the tests check it by hand-computed cases and
synthetic series.

Definitions fixed here:

- Equal-weight buy-and-hold. At the start of each holding period the benchmark
  holds the period's universe in equal weights, then buys and holds: weights drift
  with prices until the next period, when it rebalances to equal weights over the
  new universe. For H1 a period is a month and the universe is that month's dated
  top five, so the benchmark holds what the strategy could hold, delisted pairs
  included.
- Costs. Optional, ``cost_per_side`` as a fraction (0.001 is 10 bps), charged on
  the turnover of each rebalance, the first purchase included. The default is
  zero, which makes the benchmark harder to beat; the pre-registration (G0.7) fixes
  which the gate uses.
- A missing return for a held asset raises. How a halted or delisted holding is
  valued is G0.7's decision (data-coverage finding 2), so this code never invents
  an exit price.
- Volatility matching. The benchmark's daily returns are multiplied by one
  constant, the ratio of the strategy's sample standard deviation to the
  benchmark's over the same days, so both have the same realised volatility. The
  constant uses the whole evaluation window, as an evaluation benchmark may; it
  leaves the benchmark's Sharpe ratio unchanged and only sets how much market
  exposure the excess return removes.

The gate then applies the deflated Sharpe to ``excess_returns(strategy,
volatility_matched(benchmark, strategy))``.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from itertools import pairwise


def equal_weight_buy_and_hold(
    asset_returns: Mapping[str, Sequence[float | None]],
    periods: Sequence[tuple[int, Collection[str]]],
    n_days: int,
    cost_per_side: float = 0.0,
) -> list[float]:
    """Daily returns of the equal-weight buy-and-hold benchmark.

    ``asset_returns`` maps each asset to its simple daily returns over the same
    ``n_days`` days (None where it has no return, allowed only while not held).
    ``periods`` lists (first day index, universe) in increasing order, the first at
    day 0; each universe is held from its first day until the next period starts.
    """
    if n_days < 1:
        raise ValueError("n_days must be at least 1")
    if not periods or periods[0][0] != 0:
        raise ValueError("the first period must start at day 0")
    starts = [start for start, _ in periods]
    if any(b <= a for a, b in pairwise(starts)) or starts[-1] >= n_days:
        raise ValueError("period starts must increase and fall inside the window")
    if cost_per_side < 0:
        raise ValueError("cost_per_side cannot be negative")
    for name, series in asset_returns.items():
        if len(series) != n_days:
            raise ValueError(f"{name} has {len(series)} returns, expected {n_days}")

    out: list[float] = []
    weights: dict[str, float] = {}  # current weights, summing to 1 once invested
    bounds = [*starts, n_days]
    for (start, universe), end in zip(periods, bounds[1:], strict=True):
        members = sorted(set(universe))
        if not members:
            raise ValueError(f"the period starting at day {start} has an empty universe")
        missing = [m for m in members if m not in asset_returns]
        if missing:
            raise ValueError(f"no returns for {', '.join(missing)}")
        target = {m: 1 / len(members) for m in members}
        turnover = sum(
            abs(target.get(a, 0.0) - weights.get(a, 0.0)) for a in set(target) | set(weights)
        )
        if not weights:
            turnover = 1.0  # the first purchase, from cash
        weights = target
        cost = cost_per_side * turnover
        for day in range(start, end):
            grown: dict[str, float] = {}
            for asset, w in weights.items():
                r = asset_returns[asset][day]
                if r is None:
                    raise ValueError(f"{asset} is held on day {day} but has no return")
                grown[asset] = w * (1 + r)
            growth = math.fsum(grown.values())
            day_return = growth - 1
            if day == start:
                day_return = (1 - cost) * growth - 1
            out.append(day_return)
            # Buy and hold: each weight drifts with its asset's return.
            weights = {a: g / growth for a, g in grown.items()}
    return out


def _sample_sd(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        raise ValueError("at least two returns are needed")
    mean = math.fsum(xs) / n
    return math.sqrt(math.fsum((x - mean) ** 2 for x in xs) / (n - 1))


def volatility_matched(benchmark: Sequence[float], strategy: Sequence[float]) -> list[float]:
    """The benchmark scaled by one constant to the strategy's realised volatility."""
    if len(benchmark) != len(strategy):
        raise ValueError("benchmark and strategy must cover the same days")
    benchmark_sd = _sample_sd(benchmark)
    if benchmark_sd == 0:
        raise ValueError("the benchmark has no variance to match")
    scale = _sample_sd(strategy) / benchmark_sd
    return [scale * b for b in benchmark]


def excess_returns(strategy: Sequence[float], benchmark: Sequence[float]) -> list[float]:
    """Daily strategy return minus benchmark return, day by day."""
    if len(benchmark) != len(strategy):
        raise ValueError("benchmark and strategy must cover the same days")
    return [s - b for s, b in zip(strategy, benchmark, strict=True)]
