"""Minimum backtest length (MinBTL), after Bailey, Borwein, Lopez de Prado and Zhu.

Source: D. H. Bailey, J. M. Borwein, M. Lopez de Prado and Q. J. Zhu, "Pseudo-
mathematics and financial charlatanism: the effects of backtest overfitting on
out-of-sample performance", Notices of the American Mathematical Society 61(5),
May 2014, pp. 458-471.

The argument. Take N strategy configurations with no skill: each true annualised
Sharpe ratio is zero. Estimated over y years of IID returns, each annualised Sharpe
estimate is approximately Normal with mean 0 and standard deviation 1/sqrt(y). The
best of the N estimates is then about E[max Z_N] / sqrt(y), where Z_1..Z_N are
independent standard normals and

    E[max Z_N] ~ (1 - g) * Phi^-1(1 - 1/N) + g * Phi^-1(1 - 1/(N e))

with g the Euler-Mascheroni constant and Phi^-1 the standard normal quantile. For
the best in-sample Sharpe of skill-less trials to be expected no higher than a target
S, the backtest must run at least

    MinBTL(N, S) = (E[max Z_N] / S) ** 2 years,

and the paper bounds it above by 2 ln(N) / S**2. Its worked example: with five years
of data, no more than 45 independent configurations should be tried before a Sharpe
of 1 is expected from noise alone, because MinBTL(45, 1) is about 5.0 years.

Two properties matter here. Under the null the Sharpe is zero, so skewness and fat
tails do not change the 1/sqrt(y) standard error to first order (their terms in the
Sharpe's variance are multiplied by the Sharpe). And trials that are correlated count
as fewer than N independent ones, so using the raw trial count is conservative.
"""

from __future__ import annotations

import math
from statistics import NormalDist

EULER_GAMMA = 0.5772156649015329
_Z = NormalDist()


def expected_max_z(n: int) -> float:
    """The paper's approximation to E[max of n independent standard normals].

    For n = 1 the expectation is exactly 0 and the approximation is undefined (its
    first quantile is at probability 0), so 0 is returned.
    """
    if n < 1:
        raise ValueError("n must be at least 1")
    if n == 1:
        return 0.0
    return (1 - EULER_GAMMA) * _Z.inv_cdf(1 - 1 / n) + EULER_GAMMA * _Z.inv_cdf(
        1 - 1 / (n * math.e)
    )


def min_backtest_length(n: int, target_sharpe: float = 1.0) -> float:
    """Years of data below which the best of n skill-less trials is expected to show
    an annualised Sharpe of at least ``target_sharpe``."""
    if target_sharpe <= 0:
        raise ValueError("target Sharpe must be positive")
    return (expected_max_z(n) / target_sharpe) ** 2


def min_backtest_length_bound(n: int, target_sharpe: float = 1.0) -> float:
    """The paper's upper bound, 2 ln(n) / S**2."""
    return 2 * math.log(n) / target_sharpe**2


def max_trials(years: float, target_sharpe: float = 1.0, ceiling: int = 100_000) -> int:
    """The largest n with MinBTL(n, S) at most ``years``: the trial cap history allows.

    MinBTL rises with n, so the cap is found by doubling then bisection.
    """
    if years <= 0:
        return 1
    if min_backtest_length(2, target_sharpe) > years:
        return 1
    lo, hi = 2, 4
    while hi < ceiling and min_backtest_length(hi, target_sharpe) <= years:
        lo, hi = hi, hi * 2
    hi = min(hi, ceiling)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if min_backtest_length(mid, target_sharpe) <= years:
            lo = mid
        else:
            hi = mid
    return lo


def expected_max_sharpe(n: int, years: float) -> float:
    """Expected best annualised Sharpe among n skill-less trials over ``years``."""
    return expected_max_z(n) / math.sqrt(years)
