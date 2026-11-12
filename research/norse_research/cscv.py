"""Probability of backtest overfitting by combinatorially symmetric cross-validation.

Source: D. H. Bailey, J. M. Borwein, M. Lopez de Prado and Q. J. Zhu, "The
Probability of Backtest Overfitting", Journal of Computational Finance 20(4),
2017, Section 2.2 (Algorithm 2.3, CSCV) and Section 3.1 (PBO).

The method. Take the T by N matrix M of daily returns, one column per trial, rows
synchronous across trials. Cut the rows into S contiguous blocks. For every way of
choosing S/2 blocks as the in-sample half J (the rest, J-bar, out of sample): rank
the trials by Sharpe ratio on J, take the in-sample best n*, find its relative rank
w = rank / (N + 1) among all trials' Sharpe ratios on J-bar, and form the logit
ln(w / (1 - w)). PBO is the share of splits whose logit is at most zero, that is,
where the in-sample winner does no better than the median trial out of sample.
With S = 16 there are C(16, 8) = 12,870 splits. (The paper prints "12,780", a
transposition; the binomial coefficient is 12,870.)

Choices the paper leaves open, fixed here and documented in
docs/research/gate-statistics.md:

- Blocks when S does not divide T: contiguous blocks whose lengths differ by at
  most one row, the first T mod S blocks one row longer. The paper assumes equal
  blocks; dropping rows instead would compute PBO on different data from the
  deflated Sharpe.
- Ties: the in-sample winner is the lowest-indexed trial among equals, and an
  out-of-sample tie takes the mean of the tied ranks, so a split where every trial
  scores the same has logit 0 and counts as overfit.
- A trial with no variance over a half (flat throughout) scores a Sharpe ratio of
  zero on that half.
- "At most zero" includes a logit of exactly zero, which an odd N makes possible:
  the paper's PBO integrates the logit distribution from minus infinity to zero.

The performance statistic is the Sharpe ratio, the paper's own example. Because J
and J-bar have the same length for every trial, the choice of divisor in the
standard deviation cannot change a rank.

Where the matrix comes from. G1.5 must build it from the ledger: one column per
trial counted in N, each the trial's joined daily out-of-sample returns, aligned
on the same dates. This module only computes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

GATE_BLOCKS = 16


@dataclass(frozen=True)
class CSCVResult:
    pbo: float  # share of splits whose logit is at most zero
    splits: int  # C(S, S/2)
    blocks: int  # S
    n_trials: int  # N
    n_obs: int  # T
    prob_oos_loss: float  # share of splits where the in-sample winner loses out of sample
    logits: tuple[float, ...]  # one per split


def block_bounds(n_obs: int, blocks: int) -> list[tuple[int, int]]:
    """Half-open row ranges of ``blocks`` contiguous blocks covering ``n_obs`` rows,
    lengths differing by at most one, the longer blocks first."""
    base, extra = divmod(n_obs, blocks)
    bounds = []
    start = 0
    for b in range(blocks):
        end = start + base + (1 if b < extra else 0)
        bounds.append((start, end))
        start = end
    return bounds


def _sharpes(count: int, sums: Sequence[float], squares: Sequence[float]) -> list[float]:
    out = []
    for s, q in zip(sums, squares, strict=True):
        variance = (q - s * s / count) / (count - 1)
        if variance <= 0:
            out.append(0.0 if s == 0 else math.copysign(math.inf, s))
        else:
            out.append(s / count / math.sqrt(variance))
    return out


def _logit_of_split(in_sample: list[float], out_of_sample: list[float]) -> tuple[float, bool]:
    n = len(in_sample)
    best = max(range(n), key=in_sample.__getitem__)  # first maximum: lowest index wins ties
    value = out_of_sample[best]
    below = 0
    equal = 0
    for x in out_of_sample:
        if x < value:
            below += 1
        elif x == value:
            equal += 1
    rank = below + (equal + 1) / 2  # 1 is the worst, N the best; ties take the mean rank
    w = rank / (n + 1)
    return math.log(w / (1 - w)), value < 0


def cscv(trial_returns: Sequence[Sequence[float]], blocks: int = GATE_BLOCKS) -> CSCVResult:
    """CSCV PBO of a set of trials.

    ``trial_returns`` holds one return series per trial, all of the same length T:
    the columns of the paper's matrix M. ``blocks`` is S, even; the gate uses 16.
    """
    n_trials = len(trial_returns)
    if n_trials < 2:
        raise ValueError("PBO needs at least two trials")
    n_obs = len(trial_returns[0])
    if any(len(series) != n_obs for series in trial_returns):
        raise ValueError("every trial must have the same number of returns (a true matrix)")
    if blocks < 2 or blocks % 2:
        raise ValueError("the number of blocks must be even and at least 2")
    if n_obs < 2 * blocks:
        raise ValueError("need at least two returns per block")

    bounds = block_bounds(n_obs, blocks)
    counts = [end - start for start, end in bounds]
    # Per block and trial: the sum and the sum of squares, so each split only adds
    # S/2 blocks. Each block sum is exact to rounding (fsum).
    sums = [[math.fsum(series[start:end]) for series in trial_returns] for start, end in bounds]
    squares = [
        [math.fsum(x * x for x in series[start:end]) for series in trial_returns]
        for start, end in bounds
    ]
    total_sums = [sum(column) for column in zip(*sums, strict=True)]
    total_squares = [sum(column) for column in zip(*squares, strict=True)]

    half = blocks // 2
    logits: list[float] = []
    losses = 0
    # Fixing block 0 in J visits every unordered pair {J, J-bar} once; both
    # orientations are evaluated, so all C(S, S/2) ordered splits are counted.
    for rest in combinations(range(1, blocks), half - 1):
        chosen = (0, *rest)
        count = sum(counts[b] for b in chosen)
        s_j = [sum(column) for column in zip(*(sums[b] for b in chosen), strict=True)]
        q_j = [sum(column) for column in zip(*(squares[b] for b in chosen), strict=True)]
        s_k = [t - s for t, s in zip(total_sums, s_j, strict=True)]
        q_k = [t - q for t, q in zip(total_squares, q_j, strict=True)]
        sharpe_j = _sharpes(count, s_j, q_j)
        sharpe_k = _sharpes(n_obs - count, s_k, q_k)
        for in_sample, out_of_sample in ((sharpe_j, sharpe_k), (sharpe_k, sharpe_j)):
            logit, loss = _logit_of_split(in_sample, out_of_sample)
            logits.append(logit)
            losses += loss

    splits = len(logits)
    return CSCVResult(
        pbo=sum(1 for x in logits if x <= 0) / splits,
        splits=splits,
        blocks=blocks,
        n_trials=n_trials,
        n_obs=n_obs,
        prob_oos_loss=losses / splits,
        logits=tuple(logits),
    )
