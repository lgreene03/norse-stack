"""Known-answer and synthetic checks for the minimum backtest length."""

import math
import random

import pytest
from norse_research.minbtl import (
    expected_max_sharpe,
    expected_max_z,
    max_trials,
    min_backtest_length,
    min_backtest_length_bound,
)


def test_paper_worked_example_45_trials_need_five_years():
    # Bailey, Borwein, Lopez de Prado and Zhu (2014): with five years of data, no more
    # than 45 independent configurations before a Sharpe of 1 is expected from noise.
    assert min_backtest_length(45, 1.0) == pytest.approx(5.0, abs=0.01)
    assert max_trials(5.0, 1.0) == 45


def test_cap_is_the_largest_n_that_fits():
    for years in (1.0, 2.5, 4.3, 6.0, 8.0):
        cap = max_trials(years, 1.0)
        assert min_backtest_length(cap, 1.0) <= years
        assert min_backtest_length(cap + 1, 1.0) > years


def test_one_trial_needs_no_length_and_short_history_allows_one():
    assert min_backtest_length(1) == 0.0
    assert max_trials(0.1) == 1
    assert max_trials(0.0) == 1


def test_minbtl_rises_with_n_and_falls_with_target():
    values = [min_backtest_length(n) for n in range(2, 200)]
    assert values == sorted(values)
    assert min_backtest_length(30, 0.5) == pytest.approx(4 * min_backtest_length(30, 1.0))


def test_paper_upper_bound_holds():
    for n in range(2, 1000):
        assert min_backtest_length(n) < min_backtest_length_bound(n)


def test_rejects_meaningless_inputs():
    with pytest.raises(ValueError):
        expected_max_z(0)
    with pytest.raises(ValueError):
        min_backtest_length(10, 0.0)


@pytest.mark.parametrize("n", [10, 30, 100])
def test_expected_max_matches_simulation(n):
    # Synthetic check of the approximation itself: mean of the maximum of n standard
    # normals over 20,000 draws. The paper's formula is an approximation, close for
    # these n (about 2% high at n = 10, under 1% at n = 100).
    rng = random.Random(20261111 + n)
    reps = 20_000
    mean = sum(max(rng.gauss(0, 1) for _ in range(n)) for _ in range(reps)) / reps
    assert expected_max_z(n) == pytest.approx(mean, rel=0.04)


def test_best_skill_less_sharpe_matches_theory():
    # The quantity MinBTL controls: n strategies with zero true Sharpe, daily returns
    # over two 365-day years, best annualised Sharpe. Theory says about
    # E[max Z_n] / sqrt(years).
    rng = random.Random(7)
    n, years, days, reps = 10, 2.0, 730, 300
    best = []
    for _ in range(reps):
        sharpes = []
        for _ in range(n):
            xs = [rng.gauss(0, 0.02) for _ in range(days)]
            mu = sum(xs) / days
            sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (days - 1))
            sharpes.append(mu / sd * math.sqrt(365))
        best.append(max(sharpes))
    simulated = sum(best) / reps
    assert simulated == pytest.approx(expected_max_sharpe(n, years), rel=0.08)
