"""Sharpe ratio statistics for the evaluation gate (G1.3).

The probabilistic Sharpe ratio (PSR), the minimum track record length (MinTRL) and
the deflated Sharpe ratio (DSR), with the sample moments they read.

Sources:

- D. H. Bailey and M. Lopez de Prado, "The Sharpe Ratio Efficient Frontier",
  Journal of Risk 15(2), 2012: PSR (their Eq. 11) and MinTRL (Eq. 13).
- D. H. Bailey and M. Lopez de Prado, "The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting and Non-Normality", Journal of Portfolio
  Management 40(5), 2014: DSR (Eq. 2), a PSR whose benchmark is the Sharpe ratio
  the best of N skill-less trials is expected to reach.

Units. Every Sharpe ratio in this module is per observation (daily for the gate),
never annualised: the formulas are stated that way and annualising first would give
wrong answers. ``PERIODS_PER_YEAR`` converts for display only.

Moments. ``skew`` is the sample skewness m3 / m2^1.5 and ``kurtosis`` the sample
kurtosis m4 / m2^2, both from central moments with divisor n, and the kurtosis is
not excess: a normal distribution has 3. The papers' formulas use that convention
(their normal case is skew 0, kurtosis 3).

Where N comes from. ``n_trials`` and the trial Sharpe ratios are parameters here
because the trial ledger does not exist yet. G1.5 must feed them from the ledger,
with N the count of trial entries under the counting rule registered in
``prereg-v1`` (docs/research/ledger-threat-model.md, principle 3), and must wrap
these functions in the gate's public functions, which take the ledger and have no
N parameter (threat T19). Nothing here reads the ledger.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist

from norse_research.minbtl import expected_max_z

PERIODS_PER_YEAR = 365  # crypto trades every day; the gate's returns are daily

_Z = NormalDist()


@dataclass(frozen=True)
class Moments:
    """Sample moments of a return series, in per-observation units."""

    n: int
    mean: float
    sd: float  # sample standard deviation, divisor n - 1
    skew: float  # m3 / m2^1.5, divisor n
    kurtosis: float  # m4 / m2^2, divisor n, not excess (3 for a normal)

    @property
    def sharpe(self) -> float:
        """Per-observation Sharpe ratio, mean over sample standard deviation."""
        return self.mean / self.sd


def moments(returns: Sequence[float]) -> Moments:
    """Sample moments of ``returns``. Raises ValueError when the Sharpe ratio is
    undefined: fewer than two returns, or no variance at all."""
    n = len(returns)
    if n < 2:
        raise ValueError("at least two returns are needed")
    mean = math.fsum(returns) / n
    deviations = [r - mean for r in returns]
    m2 = math.fsum(d * d for d in deviations) / n
    if m2 <= 0:
        raise ValueError("returns have no variance, so the Sharpe ratio is undefined")
    m3 = math.fsum(d * d * d for d in deviations) / n
    m4 = math.fsum(d * d * d * d for d in deviations) / n
    return Moments(
        n=n,
        mean=mean,
        sd=math.sqrt(m2 * n / (n - 1)),
        skew=m3 / m2**1.5,
        kurtosis=m4 / (m2 * m2),
    )


def sharpe_ratio(returns: Sequence[float]) -> float:
    """Per-observation Sharpe ratio of ``returns`` (raises when undefined)."""
    return moments(returns).sharpe


def annualise(sharpe: float, periods_per_year: int = PERIODS_PER_YEAR) -> float:
    return sharpe * math.sqrt(periods_per_year)


def _dispersion(sharpe: float, skew: float, kurtosis: float) -> float:
    """1 - skew * SR + (kurtosis - 1) / 4 * SR^2: the Sharpe estimator's variance
    times (n - 1) under non-normal IID returns (Mertens; Bailey and Lopez de Prado
    2012, Eq. 8). Pearson's inequality (kurtosis >= 1 + skew^2) keeps it at least
    (1 - skew * SR / 2)^2, so it is zero only for a degenerate two-point series."""
    value = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe * sharpe
    if value <= 0:
        raise ValueError("degenerate moments: the Sharpe ratio's variance is not positive")
    return value


def sharpe_standard_error(sharpe: float, n_obs: int, skew: float, kurtosis: float) -> float:
    """Standard error of a per-observation Sharpe estimate over ``n_obs`` returns."""
    if n_obs < 2:
        raise ValueError("n_obs must be at least 2")
    return math.sqrt(_dispersion(sharpe, skew, kurtosis) / (n_obs - 1))


def probabilistic_sharpe_ratio(
    sharpe: float, n_obs: int, skew: float, kurtosis: float, benchmark: float = 0.0
) -> float:
    """PSR: the probability that the true Sharpe ratio exceeds ``benchmark``
    (Bailey and Lopez de Prado 2012, Eq. 11). All Sharpe ratios per observation."""
    z = (sharpe - benchmark) / sharpe_standard_error(sharpe, n_obs, skew, kurtosis)
    return _Z.cdf(z)


def min_track_record_length(
    sharpe: float,
    skew: float,
    kurtosis: float,
    benchmark: float = 0.0,
    confidence: float = 0.95,
) -> float:
    """MinTRL in observations: the track record length at which the PSR against
    ``benchmark`` reaches ``confidence`` (Bailey and Lopez de Prado 2012, Eq. 13).

    Infinite when the observed Sharpe ratio does not exceed the benchmark, since no
    length of record makes it significant. Divide by ``PERIODS_PER_YEAR`` for years.
    """
    if not 0 < confidence < 1:
        raise ValueError("confidence must be in (0, 1)")
    if sharpe <= benchmark:
        return math.inf
    z = _Z.inv_cdf(confidence)
    return 1 + _dispersion(sharpe, skew, kurtosis) * (z / (sharpe - benchmark)) ** 2


def expected_max_sharpe(n_trials: int, trial_sharpe_variance: float) -> float:
    """SR_0, the expected best per-observation Sharpe of ``n_trials`` skill-less
    trials whose Sharpe estimates have variance ``trial_sharpe_variance``
    (Bailey and Lopez de Prado 2014, Eq. 2). Reuses the E[max Z_N] approximation of
    the minimum backtest length (norse_research.minbtl); for one trial it is 0, the
    exact expectation, so the DSR is then the PSR against zero."""
    if trial_sharpe_variance < 0:
        raise ValueError("a variance cannot be negative")
    return math.sqrt(trial_sharpe_variance) * expected_max_z(n_trials)


def deflated_sharpe_ratio(
    sharpe: float,
    n_obs: int,
    skew: float,
    kurtosis: float,
    n_trials: int,
    trial_sharpe_variance: float,
) -> float:
    """DSR: the PSR of the selected strategy against SR_0 (Bailey and Lopez de
    Prado 2014, Eq. 2). ``sharpe``, ``skew``, ``kurtosis`` and ``n_obs`` describe the
    selected strategy's returns; ``n_trials`` and ``trial_sharpe_variance`` describe
    the trials it was selected from. Per-observation units throughout."""
    sr0 = expected_max_sharpe(n_trials, trial_sharpe_variance)
    return probabilistic_sharpe_ratio(sharpe, n_obs, skew, kurtosis, benchmark=sr0)


def null_sharpe_variance(n_obs: int) -> float:
    """Variance of a per-observation Sharpe estimate over ``n_obs`` returns when
    the true Sharpe ratio is zero: 1 / (n_obs - 1). Skew and kurtosis enter the
    variance only multiplied by the Sharpe ratio, so they drop out under the null."""
    if n_obs < 2:
        raise ValueError("n_obs must be at least 2")
    return 1 / (n_obs - 1)


def cross_trial_sharpe_variance(trial_sharpes: Sequence[float]) -> float:
    """The paper's V[{SR_n}]: the sample variance (divisor N - 1) of the trials'
    per-observation Sharpe estimates."""
    if len(trial_sharpes) < 2:
        raise ValueError("at least two trial Sharpe ratios are needed for a variance")
    return statistics.variance(trial_sharpes)


@dataclass(frozen=True)
class DeflatedSharpe:
    """The gate's deflated Sharpe ratio with every input it used, for the record."""

    dsr: float
    sharpe: float  # per observation, of the joined out-of-sample returns
    expected_max_sharpe: float  # SR_0, per observation
    n_obs: int
    skew: float
    kurtosis: float
    n_trials: int
    trial_sharpe_variance: float
    variance_source: str  # "null" (1 / (T - 1)) or "trials" (the cross-trial variance)


def deflated_sharpe(
    joined_oos_returns: Sequence[float],
    n_trials: int,
    trial_sharpes: Sequence[float] | None = None,
) -> DeflatedSharpe:
    """The deflated Sharpe ratio as the gate applies it.

    ``joined_oos_returns`` is the candidate's daily out-of-sample returns with every
    walk-forward fold joined into one series, never a statistic per fold averaged
    afterwards. Skew, kurtosis and length are read from that series.

    ``n_trials`` is N, and ``trial_sharpes`` the per-observation Sharpe ratios of
    those N trials' joined out-of-sample returns (the candidate's included). G1.5
    must supply both from the ledger; until then they are parameters.

    The variance in SR_0 is the larger of the null variance 1 / (T - 1) and the
    cross-trial variance of ``trial_sharpes`` (when given and N is at least 2). The
    paper uses the cross-trial variance; the floor keeps the pass mark at least as
    strict as the one implied with the null variance, which is the one published
    with the pre-registration and the only one computable before any trial, so
    near-duplicate trials cannot make the gate more lenient than its published
    table. See docs/research/gate-statistics.md.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be at least 1")
    m = moments(joined_oos_returns)
    variance = null_sharpe_variance(m.n)
    source = "null"
    if trial_sharpes is not None:
        if len(trial_sharpes) != n_trials:
            raise ValueError("trial_sharpes must hold one Sharpe ratio per trial in N")
        if n_trials >= 2:
            cross = cross_trial_sharpe_variance(trial_sharpes)
            if cross > variance:
                variance, source = cross, "trials"
    sr0 = expected_max_sharpe(n_trials, variance)
    return DeflatedSharpe(
        dsr=probabilistic_sharpe_ratio(m.sharpe, m.n, m.skew, m.kurtosis, benchmark=sr0),
        sharpe=m.sharpe,
        expected_max_sharpe=sr0,
        n_obs=m.n,
        skew=m.skew,
        kurtosis=m.kurtosis,
        n_trials=n_trials,
        trial_sharpe_variance=variance,
        variance_source=source,
    )


def min_passing_sharpe(
    n_trials: int,
    n_obs: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    confidence: float = 0.95,
) -> float:
    """The smallest per-observation Sharpe ratio whose DSR reaches ``confidence``
    over ``n_obs`` returns among ``n_trials`` trials, with the null variance in
    SR_0: the pass mark the pre-registration publishes (G0.7's table). Found by
    bisection, since the DSR rises with the Sharpe ratio over the range searched."""
    sr0 = expected_max_sharpe(n_trials, null_sharpe_variance(n_obs))

    def dsr(s: float) -> float:
        return probabilistic_sharpe_ratio(s, n_obs, skew, kurtosis, benchmark=sr0)

    lo, hi = sr0, sr0 + 20 / math.sqrt(n_obs - 1)
    if dsr(hi) < confidence:
        raise ValueError("no Sharpe ratio in the searched range reaches the confidence")
    for _ in range(200):
        mid = (lo + hi) / 2
        if dsr(mid) >= confidence:
            hi = mid
        else:
            lo = mid
    return hi
