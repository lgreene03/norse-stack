"""Checks for the circular block bootstrap. Synthetic data only.

No published worked example with numbers was found for a block bootstrap interval,
so the known-answer tests here are the exact bootstrap moments of the mean that
Politis and Romano (1992) prove for the circular block bootstrap, computed by full
enumeration of a small case; they are closed-form results, not a published example.
"""

import itertools
import math
import random

import pytest
from norse_research.block_bootstrap import (
    GATE_BLOCK_LENGTH,
    CircularBlocks,
    block_bootstrap,
)

SERIES = [0.03, -0.01, 0.02, 0.05, -0.04, 0.01]  # T = 6, blocks of L = 2, k = 3


def circular_block_means(xs, length):
    n = len(xs)
    return [sum(xs[(i + j) % n] for j in range(length)) / length for i in range(n)]


def test_exact_bootstrap_mean_and_variance_by_enumeration():
    # Every one of the 6^3 equally likely choices of block starts, enumerated:
    # the resampled mean averages to the sample mean exactly (Politis and Romano),
    # and its variance is (1/k) * (1/T) * sum_i (B_i - mean)^2, where B_i is the
    # mean of the circular block starting at day i.
    blocks = CircularBlocks(SERIES, 2)
    n, k = len(SERIES), blocks.blocks
    assert k == 3
    means = []
    for starts in itertools.product(range(n), repeat=k):
        s, _ = blocks.resample_sums(starts)
        means.append(s / n)
    sample_mean = sum(SERIES) / n
    boot_mean = sum(means) / len(means)
    boot_var = sum((m - boot_mean) ** 2 for m in means) / len(means)
    block_means = circular_block_means(SERIES, 2)
    closed_form = sum((b - sample_mean) ** 2 for b in block_means) / n / k
    assert boot_mean == pytest.approx(sample_mean, abs=1e-15)
    assert boot_var == pytest.approx(closed_form, rel=1e-12)


def test_resample_sums_by_hand():
    blocks = CircularBlocks(SERIES, 2)
    # Starts 5, 0, 3: blocks (0.01, 0.03), (0.03, -0.01), (0.05, -0.04); the last
    # block wraps round the circle.
    s, q = blocks.resample_sums([5, 0, 3])
    expected = [0.01, 0.03, 0.03, -0.01, 0.05, -0.04]
    assert s == pytest.approx(sum(expected))
    assert q == pytest.approx(sum(x * x for x in expected))


def test_last_block_is_cut_to_length():
    xs = [float(i) for i in range(7)]  # T = 7, L = 3: blocks of 3, 3 and 1
    blocks = CircularBlocks(xs, 3)
    assert (blocks.blocks, blocks.last_length) == (3, 1)
    s, _ = blocks.resample_sums([6, 6, 6])  # (6, 0, 1), (6, 0, 1), (6)
    assert s == 20.0


def test_random_resampling_matches_the_exact_variance():
    # The sampling path agrees with the closed form: on T = 200, L = 20, the
    # percentile interval of the mean is close to the mean plus or minus 1.96 exact
    # bootstrap standard deviations.
    rng = random.Random(5)
    xs = [rng.gauss(0.001, 0.02) for _ in range(200)]
    result = block_bootstrap(xs, seed=11, block_length=20, reps=20_000)
    mean = sum(xs) / len(xs)
    block_means = circular_block_means(xs, 20)
    sd = math.sqrt(sum((b - mean) ** 2 for b in block_means) / len(xs) / 10)
    assert result.mean.low == pytest.approx(mean - 1.96 * sd, abs=0.08 * sd)
    assert result.mean.high == pytest.approx(mean + 1.96 * sd, abs=0.08 * sd)


def test_same_seed_same_interval_and_defaults():
    rng = random.Random(9)
    xs = [rng.gauss(0.0005, 0.01) for _ in range(300)]
    a = block_bootstrap(xs, seed=1, reps=500)
    b = block_bootstrap(xs, seed=1, reps=500)
    assert a == b
    assert a.block_length == GATE_BLOCK_LENGTH == 20
    assert a.confidence == 0.95
    assert a.sharpe.low < a.sharpe.high


def test_rejects_meaningless_inputs():
    with pytest.raises(ValueError):
        block_bootstrap([0.01] * 10, seed=1, block_length=20)
    with pytest.raises(ValueError):
        block_bootstrap([0.01, 0.02, 0.03], seed=1, block_length=0)
    with pytest.raises(ValueError):
        block_bootstrap([0.01, 0.02, 0.03], seed=1, block_length=1, confidence=1.0)


def ar1(rng, n, mu, phi, sigma):
    e = rng.gauss(0, sigma / math.sqrt(1 - phi * phi))  # stationary start
    out = []
    for _ in range(n):
        out.append(mu + e)
        e = phi * e + rng.gauss(0, sigma)
    return out


def coverage(block_length, sims, n, reps, seed):
    # 90% intervals on AR(1) returns with positive autocorrelation, as a strategy
    # holding positions for days produces. True values are known in closed form.
    phi, sigma, mu = 0.5, 0.01, 0.001
    true_sharpe = mu * math.sqrt(1 - phi * phi) / sigma
    rng = random.Random(seed)
    hit_mean = hit_sharpe = 0
    for i in range(sims):
        xs = ar1(rng, n, mu, phi, sigma)
        r = block_bootstrap(xs, seed=i, block_length=block_length, reps=reps, confidence=0.9)
        hit_mean += r.mean.low <= mu <= r.mean.high
        hit_sharpe += r.sharpe.low <= true_sharpe <= r.sharpe.high
    return hit_mean / sims, hit_sharpe / sims


def test_coverage_near_nominal_with_20_day_blocks():
    mean_cov, sharpe_cov = coverage(GATE_BLOCK_LENGTH, sims=200, n=1000, reps=400, seed=2026)
    assert 0.83 <= mean_cov <= 0.96
    assert 0.83 <= sharpe_cov <= 0.96


def test_single_day_resampling_undercovers_autocorrelated_returns():
    # The reason for blocks: resampling single days ignores the autocorrelation.
    # With phi = 0.5 the long-run variance is three times the daily variance, so a
    # nominal 90% interval covers only about P(|Z| < 1.645 / sqrt(3)) = 66%.
    mean_cov, _ = coverage(1, sims=150, n=300, reps=300, seed=2027)
    assert mean_cov <= 0.78
