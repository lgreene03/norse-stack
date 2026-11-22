"""The gate's calibration on synthetic no-skill and planted-edge ledgers (G1.7).

docs/research/gate-calibration.md is the report; this module is the study, and
``python -m norse_research.calibration`` reproduces it from a clean clone. Synthetic
data only: nothing here reads market data, a ledger, a manifest or the dataset store.
The one file read is ``docs/preregistration.json``, for the registered windows, grid
sizes and budget, and for the ids of the families ``gate.calibration`` judges.

What is calibrated is the gate section 10 of docs/PREREGISTRATION.md registers and
``norse_research.gate`` implements (``prereg-v2``): DEV-1 to DEV-5 on each hypothesis's
candidate, the candidate rule and the holdout candidate rule (section 11, items 1 to 3),
with N from the budget (30, and 14 as a sensitivity), V the null variance 1/(T - 1) of
the run's window for DEV-1 and of the excess series' window for DEV-2 (decision D22),
the windows of section 3 and PBO matrices of 8 and 6 columns. A simulated ledger
"passes" when it has a holdout candidate, that is when at least one hypothesis passes
development. HOLD-1 has no implementation in the package yet (G3.2), so ``hold_1``
below writes section 10's formula out and is reported as an end-to-end figure only.
``prereg-v1``'s V, the sample variance of every selectable run's Sharpe ratio across
both hypotheses with no floor, is kept only as a counterfactual at N = 30, labelled
``counterfactual_pooled_v`` in the results: it is not the gate, and no verdict reads it.

The gate's public functions take only a ledger the gate issued, so they cannot be called
on a simulated matrix. This module calls the statistics they are built from, on the
same inputs and in the same order: ``gate.daily_sharpe``, ``gate._deflated`` (the DSR
and its z, the zero-variance rule included), ``null_sharpe_variance`` for V, as
``gate.trial_sharpe_variance`` returns it, ``gate.EXCESS_MATCH`` and ``excess_returns``
for DEV-2, ``cscv`` with ``GATE_BLOCKS`` for DEV-3, ``gate.sharpe_without_largest`` for
DEV-5 and ``gate.Development`` for the thresholds. DEV-4
(``gate.sharpe_without_each_year`` takes the ledger) and the two candidate rules are
written out with the gate's expressions. A test runs the gate itself on in-memory
ledgers built from simulated families and requires the same answers.

The verdict is section 10's condition as ``gate.calibration`` pins it: the families it
judges are taken by the ids the registration lists, never by their attributes, and the
families, the seed, the number of ledgers and the simulation are those of #66's commit
c2365ee, which a test pins.

The simulated ledgers. Each hypothesis's configurations are the columns of a factor
model on the registered calendar (H1's window, H2's inside it, then the 304 holdout
days): for configuration j of hypothesis h on day t,

    r = SIGMA * ( alpha + l_h * b_h(t) + v(t) * (a * F(t) + b * G_h(t) + d * e_j(t)) ),

with b_h the standardised benchmark return (its drift included, so a loading carries
market exposure and no skill), F a factor common to both hypotheses, G_h one per
hypothesis and e_j one per configuration, all of unit variance. The loadings set the
correlation within a hypothesis (l_h^2 + a^2 + b^2), across the two (l_1 l_2 c + a^2,
c the benchmarks' correlation) and with the benchmark (l_h). alpha is zero in a
no-skill family and the planted annualised Sharpe ratio over sqrt(365) where an edge is
planted. Shocks are normal, or Student t with 5 degrees of freedom; v is 1, or a
persistent volatility regime shared by every series; a family may give every shock
first-order autocorrelation. Every ledger draws from ``random.Random`` seeded with the
study seed, the family and the ledger's index, so any ledger can be rebuilt alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import date
from functools import cache
from pathlib import Path
from statistics import NormalDist

from . import gate
from .benchmark import excess_returns
from .cscv import GATE_BLOCKS, cscv
from .registration import HYPOTHESES, Registration
from .sharpe import (
    Moments,
    cross_trial_sharpe_variance,
    expected_max_sharpe,
    moments,
    null_sharpe_variance,
    sharpe_standard_error,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRATION_PATH = REPO_ROOT / "docs" / "preregistration.json"
RESULTS_PATH = REPO_ROOT / "research" / "calibration" / "gate-calibration-results.json"
REPORT_PATH = REPO_ROOT / "docs" / "research" / "gate-calibration.md"

SEED = 20261121  # the study seed, fixed before the first full run
LEDGERS = 1000  # per family (section 10, "Calibration")
FALSE_PASS_LIMIT = 0.05  # at most 5% of the no-skill ledgers may pass
DETECTION_POWER = 0.5  # "detects": passes in more than half the ledgers (section 10)
# The planted Sharpe ratio of the four detection families (D22: 2.0 in place of 1.5).
# The verdict takes them by the ids gate.calibration lists, never by this value; a test
# checks that those ids are exactly the single-planted families at it.
DETECTION_SHARPE = 2.0
CONFIDENCE = 0.95  # of the reported Clopper-Pearson intervals
DAYS_PER_YEAR = 365
SIGMA = 0.01  # a configuration's daily volatility; every statistic is scale free
BENCHMARK_SIGMA = 0.03
BENCHMARK_CORRELATION = 0.9  # B-H1 with B-H2 on their common days
T_DEGREES = 5  # Student t shocks: kurtosis 9
REGIME_PERSISTENCE = 0.98  # daily AR(1) coefficient of the log volatility regime
REGIME_SD = 0.4  # standard deviation of the log volatility regime
PLANTED_COLUMN = 0  # a single planted edge sits in the first registered configuration
PBO_ALWAYS = 200  # ledgers per family whose PBO is computed whether or not it can decide
HOLD_Z = NormalDist().inv_cdf(0.95)  # section 10's z for HOLD-1
CRITERIA = ("DEV-1", "DEV-2", "DEV-3", "DEV-4", "DEV-5")

# V in SR0. NULL is the registered one, 1/(T - 1) (section 10; decision D22), the
# default everywhere. POOLED is prereg-v1's, kept only as a reported counterfactual.
NULL = "null"
POOLED = "pooled"
SOURCES = {NULL: "null", POOLED: "pooled across both hypotheses (prereg-v1, counterfactual)"}
# The judgements made of every ledger: N = 30 and N = 14 under the registered gate, and
# prereg-v1's pooled V at N = 30 as a counterfactual. No verdict reads the last.
COUNTERFACTUAL = "counterfactual_pooled_v"
LABELS = ("N30", "N14", COUNTERFACTUAL)

NO_SKILL = "no-skill"  # the families the verdict rests on
SENSITIVITY = "sensitivity"  # no-skill families reported, not judged
POWER = "power"

# The report's results section lies between these markers and is written by the run
# (``--report``), never by hand; a test regenerates it from the committed results.
REPORT_BEGIN = ("<!-- results: begin. Generated from the results file by "
                "`python -m norse_research.calibration --report`; not edited by hand. -->")
REPORT_END = "<!-- results: end -->"
NOT_YET_RUN = "\nThe full rerun has not been run at this commit.\n"


# --- the registered layout ---------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """The registered windows and grid on one calendar of days."""

    ids: dict[str, tuple[str, ...]]  # registered configurations, in order
    start: dict[str, int]  # calendar index of each window's first return day
    years: dict[str, tuple[int, ...]]  # calendar year of each day of each window
    development: int  # calendar days up to the last development day
    holdout: int  # holdout days after it
    budget: int  # the registered total of trial entries: the largest N
    registration_sha256: str
    # The family ids gate.calibration lists: the judged no-skill families, the reported
    # serially correlated ones and the judged detection families, in registered order.
    no_skill: tuple[str, ...]
    sensitivity: tuple[str, ...]
    detection: tuple[str, ...]

    @property
    def calendar(self) -> int:
        return self.development + self.holdout

    @property
    def grid(self) -> int:
        """Configurations in both grids: the smallest N run rule 2 allows."""
        return sum(len(ids) for ids in self.ids.values())


@cache
def layout() -> Layout:
    registration = Registration.from_bytes(REGISTRATION_PATH.read_bytes())
    windows = registration.windows
    first = min(date.fromisoformat(windows[h].first_return_day) for h in HYPOTHESES)
    start = {h: (date.fromisoformat(windows[h].first_return_day) - first).days
             for h in HYPOTHESES}
    ends = {start[h] + windows[h].days for h in HYPOTHESES}
    held = registration.holdout_window
    if len(ends) != 1:
        raise ValueError("the registered windows must end on the same day")
    (development,) = ends
    if (date.fromisoformat(held.first_return_day) - first).days != development:
        raise ValueError("the holdout must start the day after the development windows end")
    pinned = registration.doc["gate"]["calibration"]
    return Layout(
        ids={h: tuple(registration.order[h]) for h in HYPOTHESES},
        start=start,
        years={h: tuple(int(d[:4]) for d in windows[h].day_list()) for h in HYPOTHESES},
        development=development,
        holdout=held.days,
        budget=registration.total_budget,
        registration_sha256=registration.sha256,
        no_skill=tuple(pinned["no_skill_families"]),
        sensitivity=tuple(pinned["sensitivity_families"]),
        detection=tuple(pinned["detection_families"]),
    )


# --- the families ------------------------------------------------------------------


@dataclass(frozen=True)
class Family:
    """One family of simulated ledgers (see the module docstring for the model)."""

    id: str
    group: str  # NO_SKILL, SENSITIVITY or POWER
    within: float  # correlation of two configurations of one hypothesis
    across: float  # correlation of an H1 and an H2 configuration on common days
    loading: tuple[float, float] = (0.0, 0.0)  # correlation with own benchmark, H1 and H2
    benchmark_sharpe: float = 0.0  # annualised, of both benchmarks
    planted: tuple[tuple[str, str, float], ...] = ()  # (hypothesis, single|shared, Sharpe)
    tails: str = "normal"  # or "t5"
    regime: bool = False
    autocorrelation: float = 0.0

    def loadings(self) -> tuple[float, dict[str, float], float]:
        """a, b per hypothesis and d; raises if the correlations cannot coexist."""
        ell = dict(zip(HYPOTHESES, self.loading, strict=True))
        common = self.across - ell["H1"] * ell["H2"] * BENCHMARK_CORRELATION
        own = {h: self.within - ell[h] ** 2 - common for h in HYPOTHESES}
        if common < -1e-12 or min(own.values()) < -1e-12 or not 0 <= self.within <= 1:
            raise ValueError(f"{self.id}: these correlations cannot coexist")
        return (math.sqrt(max(common, 0.0)), {h: math.sqrt(max(v, 0.0)) for h, v in own.items()},
                math.sqrt(1 - self.within))

    def alpha(self, hypothesis: str, column: int) -> float:
        """The planted annualised Sharpe ratio of a configuration; 0 without skill."""
        for h, form, sharpe in self.planted:
            if h == hypothesis and (form == "shared" or column == PLANTED_COLUMN):
                return sharpe
        return 0.0

    @property
    def planted_hypotheses(self) -> tuple[str, ...]:
        return tuple(h for h, _, _ in self.planted)


def _families() -> tuple[Family, ...]:
    out = []
    # No skill, normal and serially independent, benchmarks unrelated to the strategies
    # and without drift: the registered requirement, correlated within and across.
    for within, across in ((0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.9, 0.0), (0.9, 0.5),
                           (0.9, 0.9), (0.99, 0.9), (0.99, 0.99), (0.999, 0.999)):
        out.append(Family(f"null-w{within:g}-a{across:g}", NO_SKILL, within, across))
    # No skill, but H1 long and flat in a rising market and H2 close to delta neutral:
    # DEV-1 can pass on exposure alone, and DEV-2 (volatility matched for H1, beta
    # matched for H2 under D15) must stop it.
    out.extend([
        Family("exposure-sb0.5", NO_SKILL, 0.9, 0.5, (0.7, 0.2), 0.5),
        Family("exposure-sb1", NO_SKILL, 0.9, 0.5, (0.7, 0.2), 1.0),
        Family("exposure-sb1-h2beta0.5", NO_SKILL, 0.9, 0.5, (0.7, 0.5), 1.0),
        Family("null-power-layout-low", NO_SKILL, 0.5, 0.0, (0.5, 0.0)),
        Family("null-power-layout-high", NO_SKILL, 0.9, 0.5, (0.5, 0.0)),
    ])
    # No skill with fat tails and a shared volatility regime.
    for within, across in ((0.0, 0.0), (0.9, 0.9), (0.99, 0.99)):
        out.append(Family(f"fat-w{within:g}-a{across:g}", NO_SKILL, within, across,
                          tails="t5", regime=True))
    # Reported only: serially correlated returns, which the DSR's standard error ignores.
    for within, across in ((0.0, 0.0), (0.9, 0.9)):
        out.append(Family(f"ar0.1-w{within:g}-a{across:g}", SENSITIVITY, within, across,
                          autocorrelation=0.1))
    # Power: an edge in one hypothesis, the other without skill. H1 is correlated 0.5 with
    # its benchmark, so its volatility-matched excess has the planted Sharpe ratio when the
    # benchmark has no drift; H2 is uncorrelated with its own.
    out.extend(
        Family(f"{form}-{h}-s{sharpe:g}-{label}", POWER, within, across, (0.5, 0.0),
               planted=((h, form, sharpe),))
        for label, within, across in (("low", 0.5, 0.0), ("high", 0.9, 0.5))
        for h in HYPOTHESES
        for form in ("single", "shared")
        for sharpe in (1.0, 1.5, 2.0)
    )
    # Where a single planted column's power reaches one half and four fifths.
    out.extend(
        Family(f"single-{h}-s{sharpe:g}-{label}", POWER, within, across, (0.5, 0.0),
               planted=((h, "single", sharpe),))
        for label, within, across in (("low", 0.5, 0.0), ("high", 0.9, 0.5))
        for h in HYPOTHESES
        for sharpe in (2.5, 3.0)
    )
    # Contrast: both hypotheses share one edge of the same size.
    out.extend(
        Family(f"shared-both-s{sharpe:g}-high", POWER, 0.9, 0.5, (0.5, 0.0),
               planted=(("H1", "shared", sharpe), ("H2", "shared", sharpe)))
        for sharpe in (1.0, 1.5, 2.0)
    )
    return tuple(out)


FAMILIES = _families()
BY_ID = {f.id: f for f in FAMILIES}


# --- simulation --------------------------------------------------------------------


@dataclass(frozen=True)
class SimulatedLedger:
    """Each configuration's development and holdout returns, and each benchmark's."""

    returns: dict[str, list[list[float]]]
    holdout: dict[str, list[list[float]]]
    benchmarks: dict[str, list[float]]


def _shocks(rng: random.Random, n: int, family: Family) -> list[float]:
    """``n`` unit-variance shocks: normal or Student t, autocorrelated if the family is."""
    if family.tails == "normal":
        out = [rng.gauss(0.0, 1.0) for _ in range(n)]
    elif family.tails == "t5":
        scale = math.sqrt((T_DEGREES - 2) / T_DEGREES)
        out = [rng.gauss(0.0, 1.0) * scale
               / math.sqrt(rng.gammavariate(T_DEGREES / 2, 2.0) / T_DEGREES) for _ in range(n)]
    else:
        raise ValueError(f"unknown tails {family.tails!r}")
    phi = family.autocorrelation
    if phi:
        keep = math.sqrt(1 - phi * phi)
        for t in range(1, n):
            out[t] = phi * out[t - 1] + keep * out[t]
    return out


def _regime(rng: random.Random, n: int) -> list[float]:
    """A persistent volatility multiplier with E[v^2] = 1."""
    keep = math.sqrt(1 - REGIME_PERSISTENCE**2)
    x = rng.gauss(0.0, 1.0)
    out = []
    for _ in range(n):
        out.append(math.exp(REGIME_SD * x - REGIME_SD**2))
        x = REGIME_PERSISTENCE * x + keep * rng.gauss(0.0, 1.0)
    return out


def simulate(family: Family, index: int) -> SimulatedLedger:
    """Ledger ``index`` of ``family``, rebuilt from its seed alone."""
    lay = layout()
    rng = random.Random(f"{SEED}/{family.id}/{index}")
    n = lay.calendar
    a, b, d = family.loadings()
    vol = _regime(rng, n) if family.regime else [1.0] * n
    market = _shocks(rng, n, family)
    other = _shocks(rng, n, family)
    common = _shocks(rng, n, family)
    own = {h: _shocks(rng, n, family) for h in HYPOTHESES}
    c, c2 = BENCHMARK_CORRELATION, math.sqrt(1 - BENCHMARK_CORRELATION**2)
    drift = family.benchmark_sharpe / math.sqrt(DAYS_PER_YEAR)
    shock = {"H1": market, "H2": [c * m + c2 * o for m, o in zip(market, other, strict=True)]}
    unit = {h: [drift + v * z for v, z in zip(vol, shock[h], strict=True)] for h in HYPOTHESES}
    returns: dict[str, list[list[float]]] = {}
    holdout: dict[str, list[list[float]]] = {}
    benchmarks: dict[str, list[float]] = {}
    for h, ell in zip(HYPOTHESES, family.loading, strict=True):
        first = lay.start[h]
        benchmarks[h] = [BENCHMARK_SIGMA * u for u in unit[h][first:lay.development]]
        base = [ell * u + v * (a * f + b[h] * g)
                for u, v, f, g in zip(unit[h], vol, common, own[h], strict=True)]
        returns[h], holdout[h] = [], []
        for j in range(len(lay.ids[h])):
            mu = family.alpha(h, j) / math.sqrt(DAYS_PER_YEAR)
            e = _shocks(rng, n, family)
            series = [SIGMA * (mu + x + d * v * y) for x, v, y in zip(base, vol, e, strict=True)]
            returns[h].append(series[first:lay.development])
            holdout[h].append(series[lay.development:])
    return SimulatedLedger(returns, holdout, benchmarks)


# --- the gate on a simulated ledger --------------------------------------------------


@dataclass
class Prepared:
    """What every judgement of one ledger shares: each run's moments and excess series,
    and DEV-4 and DEV-5 per run once computed (they do not depend on N or V)."""

    ledger: SimulatedLedger
    moments: dict[str, list[Moments | None]]  # None: no variance, Sharpe ratio 0 (section 10)
    excess: dict[str, list[list[float]]]
    excess_sharpes: dict[str, list[float]]
    robustness: dict[tuple[str, int], tuple[dict[int, float], float]]

    def sharpes(self, h: str) -> list[float]:
        """``gate.daily_sharpe`` of each run, from the moments already computed."""
        return [0.0 if m is None else m.sharpe for m in self.moments[h]]


def _moments(returns: Sequence[float]) -> Moments | None:
    try:
        return moments(returns)
    except ValueError:  # as gate.daily_sharpe and gate._deflated: no variance scores 0
        return None


def prepare(ledger: SimulatedLedger) -> Prepared:
    excess = {
        h: [excess_returns(r, gate.EXCESS_MATCH[h](ledger.benchmarks[h], r))
            for r in ledger.returns[h]]
        for h in HYPOTHESES
    }
    return Prepared(
        ledger,
        {h: [_moments(r) for r in ledger.returns[h]] for h in HYPOTHESES},
        excess,
        {h: [gate.daily_sharpe(e) for e in excess[h]] for h in HYPOTHESES},
        {},
    )


def sharpe_without_each_year(hypothesis: str, returns: Sequence[float]) -> dict[int, float]:
    """DEV-4 as ``gate.sharpe_without_each_year`` computes it, on the registered window."""
    years = layout().years[hypothesis]
    return {
        year: gate.daily_sharpe([v for y, v in zip(years, returns, strict=True) if y != year])
        for year in sorted(set(years))
    }


def judge(
    prepared: Prepared, n_trials: int, variance: str = NULL,
    pbo: dict[str, float] | None = None,
) -> dict[str, gate.Development]:
    """Each hypothesis's candidate against DEV-1 to DEV-5, as ``gate.development`` judges
    it. ``variance=NULL``, the default, is the registered V: 1/(T - 1) of the run's
    window for DEV-1 and of the excess series' window for DEV-2, as
    ``gate.trial_sharpe_variance`` returns it, which reads no run (decision D22).
    ``variance=POOLED`` is prereg-v1's V, the sample variance of every selectable run's
    Sharpe ratio across both hypotheses (of the excess series' for DEV-2) with no floor:
    a counterfactual only, not the gate. A PBO not given is NaN, which fails DEV-3.

    The candidate is ranked by the z of ``gate._deflated`` (the same expression, from the
    moments computed once), and its DSR is then taken from ``gate._deflated`` itself,
    which must give the same z."""
    if variance == POOLED:
        pooled: tuple[float, float] | None = (
            cross_trial_sharpe_variance([s for k in HYPOTHESES for s in prepared.sharpes(k)]),
            cross_trial_sharpe_variance(
                [s for k in HYPOTHESES for s in prepared.excess_sharpes[k]]),
        )
    elif variance == NULL:
        pooled = None
    else:
        raise ValueError(f"unknown variance {variance!r}: {NULL!r} or {POOLED!r}")
    source = SOURCES[variance]

    def v(n_obs: int, criterion: int) -> float:
        """V for DEV-1 (``criterion`` 0) or DEV-2 (1) of a series of ``n_obs`` returns."""
        return null_sharpe_variance(n_obs) if pooled is None else pooled[criterion]

    lay = layout()
    out = {}
    for h in HYPOTHESES:
        best, best_z = -1, -math.inf
        for i, m in enumerate(prepared.moments[h]):  # registered order: ties keep the earlier
            z = -math.inf if m is None else (
                (m.sharpe - expected_max_sharpe(n_trials, v(m.n, 0)))
                / sharpe_standard_error(m.sharpe, m.n, m.skew, m.kurtosis))
            if best < 0 or z > best_z:
                best, best_z = i, z
        series = prepared.ledger.returns[h][best]
        deflated, z = gate._deflated(series, n_trials, v(len(series), 0), source)
        if z != best_z:
            raise AssertionError("the candidate's z differs from gate._deflated's")
        run = gate.SelectableRun(h, lay.ids[h][best], best, None, None, tuple(series))
        if (h, best) not in prepared.robustness:
            prepared.robustness[h, best] = (sharpe_without_each_year(h, series),
                                            gate.sharpe_without_largest(run))
        by_year, without_largest = prepared.robustness[h, best]
        excess = prepared.excess[h][best]
        out[h] = gate.Development(
            h,
            run,
            deflated.dsr,
            z,
            gate._deflated(excess, n_trials, v(len(excess), 1), source)[0].dsr,
            math.nan if pbo is None or h not in pbo else pbo[h],
            by_year,
            without_largest,
        )
    return out


def _needs_pbo(judged: gate.Development) -> bool:
    """Whether DEV-3 can decide this hypothesis: every other criterion is met."""
    return all(met for c, met in judged.criteria.items() if c != "DEV-3")


def holdout_candidate(judged: dict[str, gate.Development]) -> str | None:
    """Section 11, item 3, as the gate applies it: among passing hypotheses the higher
    DEV-1 z, ties to H1; None when neither passes."""
    best = None
    for h in HYPOTHESES:
        if judged[h].passes and (best is None or judged[h].z > judged[best].z):
            best = h
    return best


def hold_1(development: Sequence[float], holdout: Sequence[float]) -> bool:
    """Section 10's HOLD-1, written out (no implementation exists yet, G3.2): the
    holdout Sharpe ratio is positive and at least SR_w - z * sqrt((1 - g3 SR_w +
    (g4 - 1) / 4 SR_w^2) / (n_h - 1)), with SR_w, g3 and g4 the development window's."""
    m = moments(development)
    band = m.sharpe - HOLD_Z * sharpe_standard_error(m.sharpe, len(holdout), m.skew, m.kurtosis)
    sr_h = gate.daily_sharpe(holdout)
    return sr_h > 0 and sr_h >= band


def settings() -> tuple[tuple[str, int, str], ...]:
    """Each judgement's label, N and V: the registered gate at N = 30 and N = 14, and
    prereg-v1's pooled V at N = 30, a counterfactual no verdict reads."""
    lay = layout()
    return (("N30", lay.budget, NULL), ("N14", lay.grid, NULL),
            (COUNTERFACTUAL, lay.budget, POOLED))


def evaluate(family: Family, index: int) -> dict:
    """One ledger's outcome: under the registered gate at N = 30 and N = 14, and under
    the counterfactual with prereg-v1's pooled V (at N = 30).

    The PBO (CSCV over 12,870 splits, most of the cost) is computed for a hypothesis
    whenever it can decide the outcome, that is whenever its candidate meets every other
    criterion under any of the three judgements, and for every hypothesis of the first
    ``PBO_ALWAYS`` ledgers, from which DEV-3's own pass rate is reported. Where it is not
    computed, DEV-3 is recorded as None and the hypothesis fails another criterion anyway,
    so whether a ledger passes is exact for every ledger."""
    ledger = simulate(family, index)
    prepared = prepare(ledger)
    first = {label: judge(prepared, n, variance) for label, n, variance in settings()}
    pbo = {
        h: cscv(ledger.returns[h], blocks=GATE_BLOCKS).pbo
        for h in HYPOTHESES
        if index < PBO_ALWAYS or any(_needs_pbo(j[h]) for j in first.values())
    }
    record: dict = {"index": index, "pbo": [pbo.get(h) for h in HYPOTHESES]}
    for label in LABELS:
        judged = {h: replace(first[label][h], pbo=pbo.get(h, math.nan)) for h in HYPOTHESES}
        chosen = holdout_candidate(judged)
        held = None
        if chosen is not None:
            column = judged[chosen].candidate.order
            held = int(hold_1(ledger.returns[chosen][column], ledger.holdout[chosen][column]))
        record[label] = {
            "candidate": [judged[h].candidate.order for h in HYPOTHESES],
            "criteria": [[None if c == "DEV-3" and h not in pbo else int(judged[h].criteria[c])
                          for c in CRITERIA] for h in HYPOTHESES],
            "holdout": chosen,
            "hold_1": held,
        }
    return record


# --- summaries ---------------------------------------------------------------------


def _binomial_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n or p <= 0:
        return 1.0
    if p >= 1:
        return 0.0
    lp, lq = math.log(p), math.log1p(-p)
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp
             + (n - i) * lq for i in range(k + 1)]
    top = max(terms)
    return min(1.0, math.exp(top) * math.fsum(math.exp(t - top) for t in terms))


def binomial_interval(k: int, n: int, confidence: float = CONFIDENCE) -> tuple[float, float]:
    """The Clopper-Pearson interval for k successes in n trials, by bisection."""
    if not 0 <= k <= n or n < 1:
        raise ValueError("need 0 <= k <= n and n >= 1")
    tail = (1 - confidence) / 2

    def solve(f, target: float) -> float:  # f increasing in p: the p with f(p) = target
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if f(mid) < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    # Lower: P(X >= k | p) = tail. Upper: P(X <= k | p) = tail, that is P(X > k) = 1 - tail.
    lower = 0.0 if k == 0 else solve(lambda p: 1 - _binomial_cdf(k - 1, n, p), tail)
    upper = 1.0 if k == n else solve(lambda p: 1 - _binomial_cdf(k, n, p), 1 - tail)
    return lower, upper


def _rate(count: int, n: int) -> dict:
    lower, upper = binomial_interval(count, n)
    return {"count": count, "n": n, "rate": count / n, "lower": lower, "upper": upper}


def summarise(family: Family, records: Sequence[dict]) -> dict:
    """Counts and rates of one family's ledgers."""
    n = len(records)
    planted = set(family.planted_hypotheses)
    sampled = [r for r in records if r["index"] < PBO_ALWAYS]  # DEV-3 always computed
    out: dict = {}
    for label in LABELS:
        outcomes = [r[label] for r in records]
        block = {
            "gate_passes": _rate(sum(o["holdout"] is not None for o in outcomes), n),
            "end_to_end": _rate(sum(bool(o["hold_1"]) for o in outcomes), n),
        }
        for k, h in enumerate(HYPOTHESES):
            block[h] = {"passes": _rate(sum(all(o["criteria"][k]) for o in outcomes), n)}
            for i, c in enumerate(CRITERIA):
                pool = [r[label] for r in sampled] if c == "DEV-3" else outcomes
                block[h][c] = _rate(sum(o["criteria"][k][i] for o in pool), len(pool))
        if planted:
            index = [HYPOTHESES.index(h) for h in sorted(planted)]
            block["planted_passes"] = _rate(
                sum(any(all(o["criteria"][k]) for k in index) for o in outcomes), n)
            block["holdout_in_planted"] = _rate(
                sum(o["holdout"] in planted for o in outcomes), n)
            block["planted_end_to_end"] = _rate(
                sum(o["holdout"] in planted and bool(o["hold_1"]) for o in outcomes), n)
            if all(form == "single" for _, form, _ in family.planted):
                block["planted_column_passes"] = _rate(sum(
                    any(all(o["criteria"][k]) and o["candidate"][k] == PLANTED_COLUMN
                        for k in index) for o in outcomes), n)
        out[label] = block
    out["pbo_mean"] = [math.fsum(r["pbo"][k] for r in sampled) / len(sampled)
                       for k in range(len(HYPOTHESES))]
    return out


def _digest(records: Sequence[dict]) -> str:
    data = json.dumps(list(records), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def verdict(summaries: dict[str, dict]) -> dict:
    """Section 10's calibration condition on the registered gate at N = 30, as
    ``gate.calibration`` pins it, from the summaries. The judged families are taken by
    the ids the registration lists, never by their attributes. No skill: in each of the
    17 ``no_skill_families`` the gate produces a holdout candidate in at most 5% of the
    ledgers. Detection: in each of the four ``detection_families`` the planted hypothesis
    passes development (``planted_passes``) in more than half of them; the minimum
    decides. Without every one of those 21 families there is no verdict."""
    lay = layout()
    no_skill = {i: summaries[i]["N30"]["gate_passes"] for i in lay.no_skill if i in summaries}
    detection = {
        i: summaries[i]["N30"]["planted_passes"] for i in lay.detection if i in summaries
    }
    complete = len(no_skill) == len(lay.no_skill) and len(detection) == len(lay.detection)
    worst = max(no_skill, key=lambda i: no_skill[i]["count"]) if no_skill else None
    weakest = min(detection, key=lambda i: detection[i]["count"]) if detection else None
    false_pass_ok = bool(no_skill) and all(
        r["rate"] <= FALSE_PASS_LIMIT for r in no_skill.values())
    detects = bool(detection) and all(r["rate"] > DETECTION_POWER for r in detection.values())
    return {
        "complete": complete,
        "no_skill_families": len(no_skill),
        "no_skill_gate_passes": no_skill,
        "worst_family": worst,
        "worst": no_skill.get(worst),
        "false_pass_within_limit": false_pass_ok,
        "detection_families": len(detection),
        "detection_planted_passes": detection,
        "weakest_detection_family": weakest,
        "detection_met": detects,
        "passes_calibration": complete and false_pass_ok and detects,
    }


# --- running the study ---------------------------------------------------------------


def _evaluate_chunk(task: tuple[str, int, int]) -> list[dict]:
    family_id, start, stop = task
    family = BY_ID[family_id]
    return [evaluate(family, i) for i in range(start, stop)]


def run(families: Sequence[Family], ledgers: int, workers: int, chunk: int = 20) -> dict:
    """Every ledger of every family, in parallel when ``workers`` > 1; the result is
    the same whatever the number of workers."""
    lay = layout()
    tasks = [(f.id, s, min(s + chunk, ledgers)) for f in families for s in range(0, ledgers, chunk)]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            chunks = list(pool.map(_evaluate_chunk, tasks))
    else:
        chunks = [_evaluate_chunk(t) for t in tasks]
    by_family: dict[str, list[dict]] = {f.id: [] for f in families}
    for (family_id, _, _), records in zip(tasks, chunks, strict=True):
        by_family[family_id].extend(records)
    summaries = {f.id: summarise(f, by_family[f.id]) for f in families}
    return {
        "study": "G1.7 gate calibration",
        "seed": SEED,
        "ledgers_per_family": ledgers,
        "registration_sha256": lay.registration_sha256,
        "layout": {"days": {h: len(lay.years[h]) for h in HYPOTHESES},
                   "columns": {h: len(lay.ids[h]) for h in HYPOTHESES},
                   "start": lay.start, "holdout_days": lay.holdout,
                   "n_trials": [lay.budget, lay.grid]},
        "settings": {label: {"n_trials": n, "variance": variance}
                     for label, n, variance in settings()},
        "families": [
            {"spec": asdict(f), "records_sha256": _digest(by_family[f.id]),
             "summary": summaries[f.id]}
            for f in families
        ],
        "verdict": verdict(summaries),
    }


def dumps(results: dict) -> bytes:
    return (json.dumps(results, indent=1, sort_keys=True) + "\n").encode()


# --- the report's results section ------------------------------------------------------


def _count(r: dict) -> str:
    return f"{r['count']:,}"


def _pct(r: dict) -> str:
    return f"{100 * r['rate']:.1f}%"


def _interval(r: dict) -> str:
    return f"{100 * r['lower']:.2f} to {100 * r['upper']:.2f}%"


def _listed(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header),
            *("| " + " | ".join(row) + " |" for row in rows)]


def _other(spec: dict) -> str:
    """What sets a no-skill family apart, beyond its correlations."""
    parts = []
    if any(spec["loading"]):
        parts.append(f"benchmark correlation {spec['loading'][0]:g} (H1), "
                     f"{spec['loading'][1]:g} (H2)")
    if spec["benchmark_sharpe"]:
        parts.append(f"benchmark Sharpe {spec['benchmark_sharpe']:g}")
    if spec["tails"] != "normal":
        parts.append(f"Student t({T_DEGREES}) shocks")
    if spec["regime"]:
        parts.append("volatility regime")
    if spec["autocorrelation"]:
        parts.append(f"autocorrelation {spec['autocorrelation']:g}")
    return "; ".join(parts) or "none"


def results_markdown(results: dict) -> str:
    """The report's results section, from a results file's contents alone: the verdict
    in words, then every family's figures. The run writes it into the report
    (``--report``) and a test regenerates it from the committed file, so no figure in
    it is typed by hand."""
    v = results["verdict"]
    n = results["ledgers_per_family"]
    families = results["families"]
    sha = hashlib.sha256(dumps(results)).hexdigest()
    met = {True: "met", False: "not met"}
    out: list[str] = [""]
    if not v["complete"]:
        out.append("**No verdict.** This results file does not hold every family "
                   "`gate.calibration` judges, so it decides nothing.")
    else:
        word = "meets" if v["passes_calibration"] else "does not meet"
        out.append(f"**The gate {word} the restated clause** of section 10 (\"Calibration\") "
                   "of [PREREGISTRATION.md](../PREREGISTRATION.md), as `gate.calibration` in "
                   "[preregistration.json](../preregistration.json) pins it.")
    if v["no_skill_families"]:
        worst = v["worst"]
        out += ["", f"**No-skill half: {met[v['false_pass_within_limit']]}.** In the "
                f"{v['no_skill_families']} no-skill families judged, the gate produced a "
                f"holdout candidate at N = 30 in at most {_count(worst)} of {n:,} ledgers, in "
                f"`{v['worst_family']}` (95% interval {_interval(worst)}); the limit is "
                f"{FALSE_PASS_LIMIT:.0%} of the ledgers in every family."]
    if v["detection_families"]:
        rates = v["detection_planted_passes"]
        weakest = v["weakest_detection_family"]
        out += ["", f"**Detection half: {met[v['detection_met']]}.** The planted hypothesis "
                "passed development at N = 30 in "
                + _listed([f"{_count(r)} (`{i}`)" for i, r in rates.items()])
                + f" of {n:,} ledgers. The minimum, {_count(rates[weakest])} in `{weakest}`, "
                f"decides, and the clause needs more than {DETECTION_POWER:.0%} of the "
                "ledgers in each."]
    if v["complete"] and v["passes_calibration"]:
        out += ["", "Both halves hold, so section 10's calibration condition does not stop the "
                "gate judging a strategy."]
    elif v["complete"]:
        out += ["", "So, under decision D24 and `gate.calibration.if_not_met`, `prereg-v2` is "
                "tagged as it stands, the gate is not revised again in this iteration, and the "
                "iteration closes NOT JUDGED."]
    out += ["", "Every figure in this section was generated from "
            "[gate-calibration-results.json](../../research/calibration/gate-calibration-results.json) "
            "by `calibration.results_markdown` and written here by the run itself; "
            "`test_the_committed_full_run_is_the_one_the_report_cites` regenerates it from the "
            f"committed file and requires it to be identical. The file's SHA-256 is `{sha}`. It "
            f"holds seed {results['seed']}, {n:,} ledgers in each of {len(families)} families, a "
            "SHA-256 of every family's per-ledger records and the SHA-256 of the registration "
            f"the study read, `{results['registration_sha256']}`. Counts are of {n:,} ledgers; "
            f"intervals are Clopper-Pearson at {CONFIDENCE:.0%}. \"Pooled V\" is prereg-v1's "
            "V at N = 30, a counterfactual that is not the gate and that no verdict reads."]

    judged = set(v["no_skill_gate_passes"])
    detection = set(v["detection_planted_passes"])
    no_power = [f for f in families if f["spec"]["group"] != POWER]
    out += ["", "### No-skill families", "",
            "\"Passes\" is the gate producing a holdout candidate. \"Judged\" marks the "
            "`no_skill_families` of `gate.calibration`; the others are reported only.", ""]
    rows = []
    for f in no_power:
        spec, s = f["spec"], f["summary"]
        rows.append((
            f"`{spec['id']}`", "yes" if spec["id"] in judged else "reported only",
            f"{spec['within']:g} / {spec['across']:g}", _other(spec),
            _count(s["N30"]["gate_passes"]), _interval(s["N30"]["gate_passes"]),
            _count(s["N14"]["gate_passes"]), _count(s["N30"]["end_to_end"]),
            _count(s[COUNTERFACTUAL]["gate_passes"]),
        ))
    out += _table(("Family", "Judged", "Within / across", "Other", "N = 30: passes",
                   "95% interval", "N = 14", "With HOLD-1 (N = 30)", "Pooled V (N = 30)"), rows)
    out += ["", "Each criterion's no-skill pass rate at N = 30: the share of ledgers in which "
            "each hypothesis's candidate meets it (DEV-3 over the first "
            f"{PBO_ALWAYS} ledgers of each family, where the PBO is always computed; every "
            "other column over all of them), and the count passing all five.", ""]
    out += _table(
        ("Family", *(f"H1: {c}" if c == CRITERIA[0] else c for c in CRITERIA), "all",
         *(f"H2: {c}" if c == CRITERIA[0] else c for c in CRITERIA), "all"),
        [(f"`{f['spec']['id']}`",
          *(x for h in HYPOTHESES for x in (
              *(_pct(f["summary"]["N30"][h][c]) for c in CRITERIA),
              _count(f["summary"]["N30"][h]["passes"]))))
         for f in no_power])

    def order(f: dict) -> tuple:
        planted = f["spec"]["planted"]
        return (len(planted), planted[0][1] != "single", planted[0][0], f["spec"]["within"],
                planted[0][2])

    power = sorted((f for f in families if f["spec"]["group"] == POWER), key=order)
    out += ["", "### Power", "",
            "The planted hypothesis passing development. \"Judged\" marks the four "
            "`detection_families` of `gate.calibration`; every other row is reported only. "
            "\"Planted column\" counts the passes whose candidate was the planted configuration "
            "(single-column families only). \"With HOLD-1\" also requires the holdout candidate "
            "to be in the planted hypothesis and HOLD-1 to hold on its "
            f"{results['layout']['holdout_days']} further days.", ""]
    rows = []
    for f in power:
        spec, s = f["spec"], f["summary"]
        planted = spec["planted"]
        rows.append((
            _listed([h for h, _, _ in planted]), planted[0][1], f"{planted[0][2]:g}",
            f"{spec['within']:g} / {spec['across']:g}",
            "yes" if spec["id"] in detection else "reported only",
            _count(s["N30"]["planted_passes"]), _interval(s["N30"]["planted_passes"]),
            _count(s["N30"]["planted_column_passes"]) if "planted_column_passes" in s["N30"]
            else "n/a",
            _count(s["N14"]["planted_passes"]), _count(s[COUNTERFACTUAL]["planted_passes"]),
            _count(s["N30"]["planted_end_to_end"]),
        ))
    out += _table(("Hypothesis", "Form", "Sharpe", "Within / across", "Judged",
                   "N = 30: passes", "95% interval", "Planted column", "N = 14",
                   "Pooled V (N = 30)", "With HOLD-1 (N = 30)"), rows)
    out += ["", "Which criterion binds, for each planted hypothesis at N = 30 (DEV-3 over the "
            f"first {PBO_ALWAYS} ledgers of each family):", ""]
    out += _table(
        ("Family", "Hypothesis", *CRITERIA, "all"),
        [(f"`{f['spec']['id']}`", h,
          *(_pct(f["summary"]["N30"][h][c]) for c in CRITERIA),
          _pct(f["summary"]["N30"][h]["passes"]))
         for f in power for h, _, _ in f["spec"]["planted"]])
    return "\n".join(out) + "\n"


def report_section(text: str) -> str:
    """The generated section of the report: what lies between its two markers."""
    if text.count(REPORT_BEGIN) != 1 or text.count(REPORT_END) != 1:
        raise ValueError("the report must hold each results marker exactly once")
    start = text.index(REPORT_BEGIN) + len(REPORT_BEGIN)
    end = text.index(REPORT_END)
    if end < start:
        raise ValueError("the report's results markers are out of order")
    return text[start:end]


def with_report_section(text: str, section: str) -> str:
    """The report with its generated section replaced by ``section``."""
    report_section(text)
    start = text.index(REPORT_BEGIN) + len(REPORT_BEGIN)
    return text[:start] + section + text[text.index(REPORT_END):]


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m norse_research.calibration [--workers K] [--ledgers L] [--families
    ID ...] [--out PATH] [--report]``: the study, written as JSON with its SHA-256
    printed; with ``--report``, its results section is also written into
    docs/research/gate-calibration.md."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--ledgers", type=int, default=LEDGERS)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--families", nargs="+", choices=sorted(BY_ID), default=None)
    parser.add_argument("--out", type=Path, default=RESULTS_PATH)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args(argv)
    if args.report:  # checked before the run, which takes hours
        if args.out.resolve() != RESULTS_PATH.resolve():
            parser.error("--report writes the section that cites the committed results "
                         "file, so it needs the default --out")
        report_section(REPORT_PATH.read_text(encoding="utf-8"))
    families = [BY_ID[i] for i in args.families] if args.families else list(FAMILIES)
    print(f"started {_utc()}", flush=True)
    began = time.perf_counter()
    results = run(families, args.ledgers, args.workers)
    data = dumps(results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)
    if args.report:
        text = REPORT_PATH.read_text(encoding="utf-8")
        REPORT_PATH.write_text(with_report_section(text, results_markdown(results)),
                               encoding="utf-8")
    elapsed = time.perf_counter() - began
    print(f"finished {_utc()}")
    print(f"{len(families)} families x {args.ledgers} ledgers in {elapsed:.0f} s "
          f"with {args.workers} workers")
    print(f"sha256 {hashlib.sha256(data).hexdigest()}  {args.out}")
    print(json.dumps(results["verdict"], indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
