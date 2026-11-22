"""The registered formulas a strategy composes: H1's dated universe, signal and
volatility-targeted weight (section 6), the rebalance band (section 6), trailing
funding (sections 5 and 7) and H2's carry thresholds (section 7).

Pure functions of their arguments, so each has a hand-computed known answer in
research/tests/test_sim_rules.py. The strategies (G2.2, G2.3) choose the parameters
from the registered grid; nothing here carries a grid value except the registered
exclusions, which are categories applied to every configuration (decision D14).
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from ..formats import FUNDING_TOLERANCE_MS, EngineClass, Funding
from ..universe import base_asset, is_ranked, month_close_ms, rank_month_ends
from .history import History
from .model import DAY_MS, FUNDING_KNOWN_SLACK_MS, Instrument, SimulationError, iso_day

# Section 6, Exclusions (D14), as preregistration.json's classified_candidates lists
# them: leveraged tokens and tokens tracking an asset outside crypto. Fiat and
# fiat-pegged stablecoins are removed before ranking (universe.STABLE_OR_FIAT).
REGISTERED_EXCLUSIONS = frozenset({"ETHBULL", "ETHBEAR", "PAXG", "XAUT", "SNDKB"})
RANK_DEPTH = 10  # section 6, The universe, step 1: "take the ten largest"


# --- section 6: signal and sizing -------------------------------------------------------


def vote(close_now: float, close_then: float) -> int:
    """Section 6, Signal: "the vote is 1 if the instrument's close at t divided by its
    close L days earlier, minus one, is above zero, and 0 otherwise"."""
    return 1 if close_now / close_then - 1.0 > 0 else 0


def signal(votes: Sequence[int]) -> float:
    """Section 6: "The signal s is the mean of the votes: 0, 1/3, 2/3 or 1"."""
    if not votes:
        raise SimulationError("a signal needs at least one vote")
    return sum(votes) / len(votes)


def target_weight(
    s: float,
    slots: int,
    volatility: float | None,
    vol_target: float | None,
    max_fraction_of_slot: float = 1.0,
) -> float:
    """Section 6, Sizing: w* = (1/5) x s x min(1, 0.20 / sigma), and w* = (1/5) x s
    without volatility scaling (``vol_target`` None). preregistration.json writes the
    cap as ``max_fraction_of_slot``, 1.0 in every configuration. A volatility of zero
    (constant prices) leaves the cap binding."""
    slot = 1.0 / slots
    if vol_target is None:
        return slot * s
    if volatility is None:
        raise SimulationError("a volatility-scaled weight needs a volatility")
    scale = max_fraction_of_slot if volatility == 0 else min(max_fraction_of_slot,
                                                              vol_target / volatility)
    return slot * s * scale


def rebalances(target: float, current: float, band: float) -> bool:
    """Section 6, Rebalancing: trade "only when the absolute difference between its
    target and its current (drifted) weight exceeds 25% of the target". With a target
    of zero any holding exceeds it (exits always trade); from a holding of zero any
    positive target does when the band is below 1 (entries always trade)."""
    return abs(target - current) > band * target


def perpetual_of(spot_symbol: str, perpetuals: Iterable[str]) -> str | None:
    """Section 4: "A spot pair's perpetual is the USDT-margined perpetual on the same coin,
    including the contracts quoted per thousand coins (1000PEPEUSDT, 1000SHIBUSDT,
    1000FLOKIUSDT and 1000LUNCUSDT), whose funding rates compare directly because a rate
    is per unit of notional." None when the coin has no perpetual among ``perpetuals``."""
    available = set(perpetuals)
    for candidate in (spot_symbol, "1000" + spot_symbol):
        if candidate in available:
            return candidate
    return None


# --- sections 5 and 7: trailing funding -------------------------------------------------


@dataclass(frozen=True)
class FundingWindow(metaclass=EngineClass):
    """The published settlements with ``calc_time`` in (t - L + 60 s, t + 60 s].

    ``covered`` says the window holds every settlement it should: its first is within
    one declared interval of the window's start, its last within one interval of the
    window's end, and no two consecutive ones are further apart than their declared
    interval (formats.funding_gaps' rule, which is the gap the manifest records).
    Section 6's funding filter "does not apply while its trailing window holds a
    recorded funding gap", nor where the settlements "do not cover the seven days"."""

    total: float
    count: int
    covered: bool


def trailing_funding(settlements: Sequence[Funding], t: int, lookback_days: float) -> FundingWindow:
    """Section 7, Rule: "the sum of its published funding rates with calc_time in the
    interval (t - L days + 60 s, t + 60 s]"; section 6's funding filter uses the same
    interval over seven days. ``settlements`` are in time order (a SeriesView of a
    funding series, or a list)."""
    start = t - round(lookback_days * DAY_MS) + FUNDING_KNOWN_SLACK_MS
    end = t + FUNDING_KNOWN_SLACK_MS
    times = [s.calc_time for s in settlements]
    lo = bisect_right(times, start)
    hi = bisect_right(times, end)
    inside = [settlements[i] for i in range(lo, hi)]
    total = sum(s.rate for s in inside)
    covered = bool(inside)
    if covered:
        hour = 3_600_000
        first, last = inside[0], inside[-1]
        covered = (
            first.calc_time - start <= first.interval_hours * hour
            and end - last.calc_time < last.interval_hours * hour
        )
        for prev, cur in pairwise(inside):
            allowed = {prev.interval_hours * hour, cur.interval_hours * hour}
            if not any(abs(cur.calc_time - prev.calc_time - a) <= FUNDING_TOLERANCE_MS
                       for a in allowed):
                covered = False
    return FundingWindow(total, len(inside), covered)


def carry_thresholds(
    entry_multiple: float, exit_multiple: float, round_trip_cost: float, lookback_days: float
) -> tuple[float, float]:
    """Section 7: entry threshold = m_entry x c x L / 28 and exit threshold = m_exit x c
    x L / 28, with c = 4 x 12 bps = 0.48%."""
    return (entry_multiple * round_trip_cost * lookback_days / 28.0,
            exit_multiple * round_trip_cost * lookback_days / 28.0)


def next_carry_state(in_carry: bool, trailing: float, entry: float, exit_: float) -> bool:
    """Section 7: "A flat coin enters when F is above the entry threshold; a coin in
    carry exits when F is below the exit threshold." Otherwise its state holds."""
    if in_carry:
        return not trailing < exit_
    return trailing > entry


# --- section 6: the dated universe ------------------------------------------------------


def month_of_decision(t: int) -> str:
    """The month whose month-end close is the decision at ``t``, 00:00 on a 1st."""
    if t % DAY_MS or not iso_day(t).endswith("-01"):
        raise SimulationError("the universe updates only at 00:00 on the first of a month")
    return iso_day(t - DAY_MS)[:7]


def quote_volumes(bars: Iterable) -> dict[int, float]:
    """A daily series' quote volume by bar open, as G0.6 ranked it."""
    return {b.open_time: b.quote_volume for b in bars}


def rank_top(volumes: Mapping[str, Mapping[int, float]], month: str,
             depth: int = RANK_DEPTH) -> list[str]:
    """Section 6, The universe, step 1: rank USDT spot symbols by quote volume over the
    30 daily bars closing at the month end, "delisted symbols included, after removing
    the 27 fiat and fiat-pegged stablecoin base assets ... exactly as rank_month_ends
    computes it", and take the ten largest."""
    ranked = {s: v for s, v in volumes.items() if is_ranked(s)}
    return rank_month_ends(ranked, [month], depth)[month]


def select_universe(
    history: History,
    ranked: Sequence[str],
    slots: int = 5,
    history_days: int = 120,
    excluded: frozenset[str] = REGISTERED_EXCLUSIONS,
) -> list[Instrument]:
    """Section 6, The universe, steps 2 and 3, at the month-end decision ``history.now``.

    From the ranked symbols, drop an excluded category (D14) and any "whose current
    instrument does not have the month-end daily bar or does not have a daily close 120
    days before the month-end close" (D13); the universe is the first five remaining in
    rank order, and fewer leave slots in cash. An instrument whose last bar is the
    month-end bar has that bar and stays eligible: the selection looks no bar ahead,
    and section 4 then closes it and holds its slot in cash."""
    close = history.now
    if close != month_close_ms(month_of_decision(close)):
        raise SimulationError("the universe updates only at a month-end close")
    chosen: list[Instrument] = []
    for symbol in ranked:
        if base_asset(symbol) in excluded:
            continue
        instrument = history.instrument(symbol)
        if instrument is None:
            continue  # no month-end daily bar
        if instrument.first_open > close - (history_days + 1) * DAY_MS:
            continue  # no daily close 120 days before the month-end close
        chosen.append(instrument)
        if len(chosen) == slots:
            break
    return chosen
