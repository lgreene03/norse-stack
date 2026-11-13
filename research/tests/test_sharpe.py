"""Known-answer and synthetic checks for the PSR, MinTRL and deflated Sharpe ratio.

Every known answer below is a number printed in the paper cited beside it, checked
against the paper itself on 2026-11-11. Synthetic data only: no market data.
"""

import math
import random

import pytest
from norse_research.sharpe import (
    PERIODS_PER_YEAR,
    deflated_sharpe,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_passing_sharpe,
    min_track_record_length,
    moments,
    null_sharpe_variance,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)

# --- Deflated Sharpe ratio: Bailey and Lopez de Prado (2014), "A numerical example" ---
#
# A strategist reports an annualised Sharpe ratio of 2.5 over five years of daily
# returns (T = 1250, 250 a year), N = 100 independent trials whose annualised Sharpe
# ratios have variance 1/2, skewness -3 and kurtosis 10. The paper finds SR_0 =
# 0.1132 (non-annualised) and DSR = 0.9004; with N = 46 the DSR would have been
# 0.9505; with normal returns (skew 0, kurtosis 3) it is 0.9505 after N = 88.

PAPER_PPY = 250
PAPER_SR = 2.5 / math.sqrt(PAPER_PPY)
PAPER_V = 0.5 / PAPER_PPY  # the annualised variance 1/2, in per-observation units


def paper_dsr(n_trials, skew=-3.0, kurtosis=10.0):
    return deflated_sharpe_ratio(PAPER_SR, 1250, skew, kurtosis, n_trials, PAPER_V)


def test_dsr_paper_numerical_example_n100():
    assert expected_max_sharpe(100, PAPER_V) == pytest.approx(0.1132, abs=5e-5)
    assert paper_dsr(100) == pytest.approx(0.9004, abs=5e-5)


def test_dsr_paper_numerical_example_n46_is_the_last_pass():
    assert paper_dsr(46) == pytest.approx(0.9505, abs=5e-5)
    assert paper_dsr(46) > 0.95 > paper_dsr(47)


def test_dsr_paper_numerical_example_normal_returns_n88():
    assert paper_dsr(88, skew=0.0, kurtosis=3.0) == pytest.approx(0.9505, abs=5e-5)
    assert paper_dsr(88, 0.0, 3.0) > 0.95 > paper_dsr(89, 0.0, 3.0)


# --- PSR: Bailey and Lopez de Prado (2012), Section 3, Figure 6 ---
#
# A hedge fund's two-year monthly record: SR = 0.458 a month (1.59 annualised),
# skew -2.448, kurtosis 10.164. Assuming normality PSR(0) = 0.982; with the skew and
# kurtosis PSR(0) = 0.913; with three years instead of two PSR(0) = 0.953. The
# paper computed from unrounded inputs and prints three places, so the tolerance
# is one unit in the third place (from 0.458 we get 0.9817, 0.9134 and 0.9535).


def test_psr_paper_hedge_fund_example():
    assert probabilistic_sharpe_ratio(0.458, 24, 0.0, 3.0) == pytest.approx(0.982, abs=1e-3)
    assert probabilistic_sharpe_ratio(0.458, 24, -2.448, 10.164) == pytest.approx(0.913, abs=1e-3)
    assert probabilistic_sharpe_ratio(0.458, 36, -2.448, 10.164) == pytest.approx(0.953, abs=1e-3)


# --- MinTRL: Bailey and Lopez de Prado (2012), Section 5 and Appendix A.3 ---
#
# Appendix A.3: monthly SR 2/sqrt(12) against 1/sqrt(12), skew -0.72, kurtosis
# 5.78, 95% confidence: MinTRL = 59.895 months (4.99 years), and the PSR over
# 59.895 observations is 0.95.


def test_mintrl_paper_appendix_example():
    sr, ref = 2 / math.sqrt(12), 1 / math.sqrt(12)
    months = min_track_record_length(sr, -0.72, 5.78, benchmark=ref)
    assert months == pytest.approx(59.895, abs=1e-3)
    assert probabilistic_sharpe_ratio(sr, months, -0.72, 5.78, benchmark=ref) == pytest.approx(
        0.95, abs=1e-9
    )


# Figures 8 to 11 tabulate MinTRL in years at 95% for an observed annualised Sharpe
# (rows) against a true one (columns). The daily table uses 252 days a year (its
# 5 vs 4.5 entry is 11.36 at 252, 11.37 at 250). Figure 11 is monthly with skew
# -0.72 and kurtosis 5.78.
FIGURE_ENTRIES = [
    # (periods a year, skew, kurtosis, observed, true, years printed)
    (252, 0.0, 3.0, 2.0, 1.0, 2.73),
    (252, 0.0, 3.0, 1.0, 0.0, 2.71),
    (252, 0.0, 3.0, 0.5, 0.0, 10.83),
    (252, 0.0, 3.0, 5.0, 4.5, 11.36),
    (252, 0.0, 3.0, 5.0, 0.0, 0.12),
    (52, 0.0, 3.0, 2.0, 1.0, 2.83),
    (52, 0.0, 3.0, 5.0, 4.5, 13.44),
    (12, 0.0, 3.0, 2.0, 1.0, 3.24),
    (12, 0.0, 3.0, 5.0, 4.5, 22.18),
    (12, -0.72, 5.78, 2.0, 1.0, 4.99),
    (12, -0.72, 5.78, 0.5, 0.0, 12.30),
    (12, -0.72, 5.78, 5.0, 4.5, 49.09),
]


@pytest.mark.parametrize(("ppy", "skew", "kurtosis", "observed", "true", "years"), FIGURE_ENTRIES)
def test_mintrl_paper_figures_8_to_11(ppy, skew, kurtosis, observed, true, years):
    root = math.sqrt(ppy)
    obs = min_track_record_length(observed / root, skew, kurtosis, benchmark=true / root)
    assert round(obs / ppy, 2) == years


def test_mintrl_is_infinite_without_an_edge_over_the_benchmark():
    assert min_track_record_length(0.05, 0.0, 3.0, benchmark=0.05) == math.inf
    assert min_track_record_length(-0.01, 0.0, 3.0) == math.inf


# --- Moments, by hand ---


def test_moments_by_hand():
    m = moments([1.0, 2.0, 3.0, 4.0, 5.0])
    assert m.mean == 3.0
    assert m.sd == pytest.approx(math.sqrt(2.5))  # divisor n - 1
    assert m.skew == pytest.approx(0.0, abs=1e-15)
    assert m.kurtosis == pytest.approx(1.7)  # m4 / m2^2 = 6.8 / 4, not excess
    assert sharpe_ratio([1.0, 2.0, 3.0, 4.0, 5.0]) == pytest.approx(3 / math.sqrt(2.5))


def test_moments_refuse_an_undefined_sharpe_ratio():
    with pytest.raises(ValueError):
        moments([0.01])
    with pytest.raises(ValueError):
        moments([0.0, 0.0, 0.0])


# --- The gate's form: joined returns, N and the variance floor ---


def test_one_trial_deflates_nothing():
    rng = random.Random(1)
    xs = [rng.gauss(0.001, 0.01) for _ in range(500)]
    m = moments(xs)
    result = deflated_sharpe(xs, n_trials=1)
    assert result.expected_max_sharpe == 0.0
    assert result.dsr == pytest.approx(
        probabilistic_sharpe_ratio(m.sharpe, m.n, m.skew, m.kurtosis)
    )


def test_variance_is_floored_at_the_null_variance():
    rng = random.Random(2)
    xs = [rng.gauss(0.001, 0.01) for _ in range(1000)]
    null = null_sharpe_variance(1000)
    # Near-duplicate trials: tiny cross-trial variance, so the floor applies.
    tight = deflated_sharpe(xs, 3, trial_sharpes=[0.050, 0.051, 0.052])
    assert tight.variance_source == "null"
    assert tight.trial_sharpe_variance == null
    assert tight.dsr == deflated_sharpe(xs, 3).dsr
    # Widely dispersed trials: the paper's cross-trial variance is larger and is used.
    wide = deflated_sharpe(xs, 3, trial_sharpes=[-0.2, 0.0, 0.2])
    assert wide.variance_source == "trials"
    assert wide.trial_sharpe_variance == pytest.approx(0.04)
    assert wide.dsr < tight.dsr


def test_trial_sharpes_must_match_n():
    xs = [0.01, -0.01, 0.02, 0.0]
    with pytest.raises(ValueError):
        deflated_sharpe(xs, 3, trial_sharpes=[0.1, 0.2])
    with pytest.raises(ValueError):
        deflated_sharpe(xs, 0)


def test_pass_marks_match_next_iteration():
    # docs/NEXT_ITERATION.md: over about 5.6 years of daily returns, passing needs a
    # net Sharpe of roughly 1.6 at N = 30, or 1.4 at N = 10 (normal returns, null
    # variance). A consistency check of that published arithmetic, not a paper.
    n_obs = round(5.6 * PERIODS_PER_YEAR)
    at_30 = min_passing_sharpe(30, n_obs) * math.sqrt(PERIODS_PER_YEAR)
    at_10 = min_passing_sharpe(10, n_obs) * math.sqrt(PERIODS_PER_YEAR)
    assert round(at_30, 1) == 1.6
    assert round(at_10, 1) == 1.4
    sr = min_passing_sharpe(30, n_obs)
    sr0 = expected_max_sharpe(30, null_sharpe_variance(n_obs))
    assert probabilistic_sharpe_ratio(sr, n_obs, 0.0, 3.0, sr0) == pytest.approx(0.95, abs=1e-9)


# --- Synthetic checks ---


def test_psr_is_uniform_under_the_null():
    # One skill-less strategy (true Sharpe 0, normal returns): PSR(0) should exceed
    # 0.95 in about 5% of samples.
    rng = random.Random(20261111)
    trials, t = 2000, 250
    hits = 0
    for _ in range(trials):
        m = moments([rng.gauss(0, 0.01) for _ in range(t)])
        hits += probabilistic_sharpe_ratio(m.sharpe, m.n, m.skew, m.kurtosis) > 0.95
    assert 0.035 <= hits / trials <= 0.065


def test_dsr_rarely_passes_the_best_of_skill_less_trials_and_finds_a_planted_edge():
    # N = 10 skill-less trials over two years, the best selected: the gate's DSR
    # (null variance) should pass it at most 5% of the time (theory: about 0.7%,
    # since the best of 10 rarely sits 1.645 standard errors above its expectation).
    rng = random.Random(7)
    n, t, sims = 10, 730, 200
    false_passes = 0
    for _ in range(sims):
        series = [[rng.gauss(0, 0.01) for _ in range(t)] for _ in range(n)]
        best = max(series, key=sharpe_ratio)
        false_passes += deflated_sharpe(best, n).dsr > 0.95
    assert false_passes / sims <= 0.05

    # One planted trial with an annualised Sharpe of 3 over four years: the pass
    # mark is about 1.61 annualised with a standard error of 0.5, so theory gives
    # power above 99%.
    t = 1460
    daily = 3 / math.sqrt(PERIODS_PER_YEAR) * 0.01
    detected = 0
    for _ in range(50):
        series = [[rng.gauss(0, 0.01) for _ in range(t)] for _ in range(n - 1)]
        series.append([rng.gauss(daily, 0.01) for _ in range(t)])
        best = max(series, key=sharpe_ratio)
        detected += deflated_sharpe(best, n).dsr > 0.95
    assert detected / 50 >= 0.9
