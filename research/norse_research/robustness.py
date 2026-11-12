"""Robustness checks for the evaluation gate (G1.3): the Sharpe ratio with each
calendar year left out, and with the best days removed.

Source: docs/NEXT_ITERATION.md, "The Sharpe stays positive with each year removed
and with the best 20 days removed". These are the gate's own criteria rather than
a published statistic, so there is no paper and no published worked example; the
tests check them by hand-computable cases and synthetic series.

Definitions fixed here:

- A year is a UTC calendar year of the return dates. Every calendar year present
  is left out once, a partial first or last year included, and the Sharpe ratio is
  computed on the remaining days joined in order.
- "The best 20 days removed" removes the 20 highest daily returns (ties broken by
  the earlier date) and computes the Sharpe ratio on the days left. They are
  removed, not set to zero.
- A Sharpe ratio that is undefined (fewer than two days left, or no variance) is
  recorded as None and fails the check, never as zero.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from norse_research.sharpe import sharpe_ratio

GATE_BEST_DAYS = 20


def _sharpe_or_none(returns: Sequence[float]) -> float | None:
    try:
        return sharpe_ratio(returns)
    except ValueError:
        return None


def _check_dates(dates: Sequence[date], returns: Sequence[float]) -> None:
    if len(dates) != len(returns):
        raise ValueError("one date per return is needed")
    if any(later <= earlier for earlier, later in pairwise(dates)):
        raise ValueError("dates must be strictly increasing")


def leave_one_year_out(dates: Sequence[date], returns: Sequence[float]) -> dict[int, float | None]:
    """Per-observation Sharpe ratio of the series with each calendar year removed,
    keyed by the year removed."""
    _check_dates(dates, returns)
    years = sorted({d.year for d in dates})
    return {
        year: _sharpe_or_none([r for d, r in zip(dates, returns, strict=True) if d.year != year])
        for year in years
    }


def minus_best_days(returns: Sequence[float], days: int = GATE_BEST_DAYS) -> float | None:
    """Per-observation Sharpe ratio with the ``days`` highest returns removed."""
    if days < 0:
        raise ValueError("days cannot be negative")
    order = sorted(range(len(returns)), key=lambda i: (-returns[i], i))
    removed = set(order[:days])
    return _sharpe_or_none([r for i, r in enumerate(returns) if i not in removed])


@dataclass(frozen=True)
class Robustness:
    by_year_removed: dict[int, float | None]
    minus_best_days: float | None
    best_days_removed: int

    @property
    def passes(self) -> bool:
        """True when every leave-one-year-out Sharpe ratio and the minus-best-days
        Sharpe ratio are defined and above zero."""
        values = [*self.by_year_removed.values(), self.minus_best_days]
        return all(v is not None and v > 0 for v in values)


def robustness(
    dates: Sequence[date], returns: Sequence[float], best_days: int = GATE_BEST_DAYS
) -> Robustness:
    """Both checks on the joined daily out-of-sample returns of one candidate."""
    return Robustness(
        by_year_removed=leave_one_year_out(dates, returns),
        minus_best_days=minus_best_days(returns, best_days),
        best_days_removed=best_days,
    )
