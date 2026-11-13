"""Checks for the leave-one-year-out and minus-best-days criteria. Synthetic only.

These criteria are the gate's own (docs/NEXT_ITERATION.md), not a published
statistic, so the known answers are hand computations, not a published example.
"""

import math
import random
from datetime import date, timedelta

import pytest
from norse_research.robustness import (
    GATE_BEST_DAYS,
    leave_one_year_out,
    minus_best_days,
    robustness,
)


def days_from(start, n):
    return [start + timedelta(days=i) for i in range(n)]


def test_leave_one_year_out_by_hand():
    dates = [date(2024, 12, 30), date(2024, 12, 31), date(2025, 1, 1), date(2025, 1, 2),
             date(2025, 1, 3)]
    returns = [0.01, 0.03, -0.01, 0.02, 0.05]
    by_year = leave_one_year_out(dates, returns)
    # Without 2024: (-0.01, 0.02, 0.05), mean 0.02, sd 0.03, Sharpe 2/3.
    # Without 2025: (0.01, 0.03), mean 0.02, sd sqrt(2) / 100, Sharpe sqrt(2).
    assert by_year[2024] == pytest.approx(2 / 3)
    assert by_year[2025] == pytest.approx(math.sqrt(2))


def test_minus_best_days_by_hand():
    returns = [0.05, -0.01, 0.02, 0.05, 0.00]
    # Without both 0.05s: (-0.01, 0.02, 0.00), mean 1/300, sd sqrt(7/30000).
    expected = (1 / 300) / math.sqrt(7 / 30000)
    assert minus_best_days(returns, 2) == pytest.approx(expected)
    # A tie at the cut removes the earlier day, so one 0.05 stays.
    assert minus_best_days(returns, 1) == pytest.approx(
        minus_best_days([-0.01, 0.02, 0.05, 0.00], 0)
    )
    assert GATE_BEST_DAYS == 20


def test_undefined_sharpe_fails_rather_than_counting_as_zero():
    # One calendar year only: removing it leaves nothing, so the check fails.
    dates = days_from(date(2025, 1, 1), 30)
    result = robustness(dates, [0.001 * (i % 5) for i in range(30)], best_days=2)
    assert result.by_year_removed == {2025: None}
    assert not result.passes
    # Removing the only positive days of a long-flat strategy leaves no variance.
    assert minus_best_days([0.0, 0.03, 0.0, 0.02, 0.0], 2) is None


def test_dates_must_match_and_increase():
    with pytest.raises(ValueError):
        leave_one_year_out([date(2025, 1, 1)], [0.01, 0.02])
    with pytest.raises(ValueError):
        leave_one_year_out([date(2025, 1, 2), date(2025, 1, 1)], [0.01, 0.02])


def daily_series(rng, start, years, mean, sd=0.01):
    dates = days_from(start, round(years * 365))
    return dates, [rng.gauss(mean, sd) for _ in dates]


def test_a_steady_edge_passes():
    # Annualised Sharpe of about 2 every year for five years.
    rng = random.Random(1)
    dates, returns = daily_series(rng, date(2019, 1, 1), 5, 2 / math.sqrt(365) * 0.01)
    assert robustness(dates, returns).passes


def test_an_edge_from_one_year_fails_leave_one_year_out():
    # No skill in four years, a strong run in one: removing that year leaves a
    # Sharpe ratio at or below zero.
    rng = random.Random(2)
    dates = days_from(date(2019, 1, 1), 5 * 365)
    returns = [
        rng.gauss(0.004 if d.year == 2021 else -0.0005, 0.01) for d in dates
    ]
    result = robustness(dates, returns)
    assert result.by_year_removed[2021] is not None
    assert result.by_year_removed[2021] <= 0
    assert not result.passes


def test_an_edge_from_a_few_jackpot_days_fails_minus_best_20():
    # A slightly losing strategy with 15 very large winning days: positive overall,
    # negative once the best 20 days are removed.
    rng = random.Random(3)
    dates, returns = daily_series(rng, date(2019, 1, 1), 5, -0.0003)
    for i in rng.sample(range(len(returns)), 15):
        returns[i] = 0.15
    result = robustness(dates, returns)
    assert sum(returns) > 0
    assert result.minus_best_days is not None
    assert result.minus_best_days < 0
    assert not result.passes
