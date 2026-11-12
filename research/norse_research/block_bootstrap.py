"""Block bootstrap intervals for the mean return and the Sharpe ratio (G1.3).

Source: D. N. Politis and J. P. Romano, "A circular block-resampling procedure for
stationary data", in R. LePage and L. Billard (eds.), Exploring the Limits of
Bootstrap, Wiley, 1992, pp. 263-270. The moving block bootstrap it modifies is
H. R. Kunsch, "The jackknife and the bootstrap for general stationary
observations", Annals of Statistics 17(3), 1989, pp. 1217-1241.

The method. Daily strategy returns are autocorrelated (positions last days to
weeks), so resampling single days would understate the uncertainty. The circular
block bootstrap wraps the series into a circle, draws blocks of ``block_length``
consecutive days starting at uniformly random days, joins ceil(T / L) blocks and
cuts the result to T days. Wrapping gives every day the same chance of being
drawn, which the plain moving block bootstrap does not, and makes the resampled
mean unbiased for the sample mean (Politis and Romano), which is the known-answer
test. The gate uses L = 20 days.

Intervals are percentile intervals of the resampled statistic. Sums over any
circular stretch come from prefix sums, so each replicate costs ceil(T / L)
lookups rather than T additions.

Reproducibility. ``seed`` is required: a verdict must reproduce exactly from a
clean checkout (P1), so the resampling is a pure function of the data and the seed.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

GATE_BLOCK_LENGTH = 20


@dataclass(frozen=True)
class Interval:
    low: float
    high: float


@dataclass(frozen=True)
class BootstrapResult:
    mean: Interval  # per-observation mean return
    sharpe: Interval  # per-observation Sharpe ratio
    sharpe_standard_error: float  # standard deviation of the resampled Sharpe ratios
    confidence: float
    block_length: int
    reps: int
    seed: int
    n_obs: int


class CircularBlocks:
    """Prefix sums of a series and of its squares, wrapped once, so the sum of any
    circular stretch of at most the block length is two lookups."""

    def __init__(self, returns: Sequence[float], block_length: int):
        n = len(returns)
        if block_length < 1:
            raise ValueError("block_length must be at least 1")
        if n < 2 or block_length > n:
            raise ValueError("need at least two returns and no fewer than one block")
        wrapped = list(returns) + list(returns[:block_length])
        self.n = n
        self.block_length = block_length
        self.blocks = math.ceil(n / block_length)
        self.last_length = n - (self.blocks - 1) * block_length
        self._sum = [0.0]
        self._square = [0.0]
        for x in wrapped:
            self._sum.append(self._sum[-1] + x)
            self._square.append(self._square[-1] + x * x)

    def resample_sums(self, starts: Sequence[int]) -> tuple[float, float]:
        """Sum and sum of squares of the resampled series whose blocks start at
        ``starts`` (one start per block; the last block is cut to length)."""
        s = q = 0.0
        length = self.block_length
        last = len(starts) - 1
        for i, start in enumerate(starts):
            end = start + (self.last_length if i == last else length)
            s += self._sum[end] - self._sum[start]
            q += self._square[end] - self._square[start]
        return s, q


def _quantile(sorted_values: Sequence[float], p: float) -> float:
    """Linear interpolation between order statistics (the 'linear' rule)."""
    position = p * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def block_bootstrap(
    returns: Sequence[float],
    *,
    seed: int,
    block_length: int = GATE_BLOCK_LENGTH,
    reps: int = 10_000,
    confidence: float = 0.95,
) -> BootstrapResult:
    """Circular block bootstrap percentile intervals for the mean and the Sharpe
    ratio of ``returns`` (per observation; annualise the Sharpe for display)."""
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    if reps < 2:
        raise ValueError("reps must be at least 2")
    blocks = CircularBlocks(returns, block_length)
    n = blocks.n
    rng = random.Random(seed)
    starts_range = range(n)
    means: list[float] = []
    sharpes: list[float] = []
    for _ in range(reps):
        s, q = blocks.resample_sums(rng.choices(starts_range, k=blocks.blocks))
        mean = s / n
        variance = (q - s * s / n) / (n - 1)
        means.append(mean)
        if variance > 0:
            sharpes.append(mean / math.sqrt(variance))
        else:
            sharpes.append(0.0 if s == 0 else math.copysign(math.inf, s))
    tail = (1 - confidence) / 2
    means.sort()
    finite = [x for x in sharpes if math.isfinite(x)]
    sharpes.sort()
    centre = math.fsum(finite) / len(finite) if finite else math.nan
    spread = (
        math.sqrt(math.fsum((x - centre) ** 2 for x in finite) / (len(finite) - 1))
        if len(finite) > 1
        else math.nan
    )
    return BootstrapResult(
        mean=Interval(_quantile(means, tail), _quantile(means, 1 - tail)),
        sharpe=Interval(_quantile(sharpes, tail), _quantile(sharpes, 1 - tail)),
        sharpe_standard_error=spread,
        confidence=confidence,
        block_length=block_length,
        reps=reps,
        seed=seed,
        n_obs=n,
    )
