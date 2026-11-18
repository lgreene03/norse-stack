"""Example strategies for the simulation engine's tests only (G1.9).

They import nothing but ``norse_research.sim`` and the allowlisted standard library, as
a strategy module under a hypothesis's paths must (test_sim_engine.py parses this file
with the harness's source check). They are not H1 or H2: those, with the registered
grid, arrive with G2.2 and G2.3.
"""

from __future__ import annotations

import math

from norse_research.sim import (
    CarryTarget,
    Decision,
    carry_thresholds,
    funding_series,
    iso_day,
    next_carry_state,
    signal,
    spot_series,
    target_weight,
    trailing_funding,
    vote,
)


class Scripted:
    """Decisions fixed by day: weights {symbol: weight} or carry {symbol: (perp,
    fraction)}. The golden fixture's strategy."""

    def __init__(self, interval="1d", weights=None, carry=None, band=0.25):
        self.interval = interval
        self.weights = weights or {}
        self.carry = carry or {}
        self.band = band

    def decide(self, history, book):
        day = iso_day(history.now)
        if day in self.weights:
            targets = {}
            for symbol, weight in self.weights[day].items():
                instrument = history.instrument(symbol)
                if instrument is not None:
                    targets[instrument] = weight
            return Decision(weights=targets, band=self.band)
        if day in self.carry:
            return Decision(carry=tuple(
                CarryTarget(history.instrument(symbol), perp, fraction)
                for symbol, (perp, fraction) in sorted(self.carry[day].items())
            ))
        return None


class TrendExample:
    """An H1-shaped strategy over a fixed list of symbols: votes over lookbacks,
    volatility-targeted weights in equal slots, a band. Lookbacks and span are in
    bars of ``interval``."""

    def __init__(self, symbols, interval="1d", lookbacks=(5, 10, 20), span=10, band=0.25,
                 vol_target=0.2):
        self.symbols = list(symbols)
        self.interval = interval
        self.lookbacks = lookbacks
        self.span = span
        self.band = band
        self.vol_target = vol_target
        self.per_year = 365 if interval == "1d" else 2190

    def decide(self, history, book):
        step = 86_400_000 if self.interval == "1d" else 14_400_000
        targets = {}
        for symbol in self.symbols:
            instrument = history.instrument(symbol)
            if instrument is None or history.ended(instrument):
                continue
            view = history.series(spot_series(symbol, self.interval))
            now = view.at(history.now - step)
            if now is None:
                continue
            votes = []
            for lookback in self.lookbacks:
                then = view.at(history.now - step - lookback * step)
                if then is None or then.open_time < instrument.first_open:
                    break
                votes.append(vote(now.close, then.close))
            if len(votes) != len(self.lookbacks):
                continue
            sigma = history.ewm_volatility(spot_series(symbol, self.interval), instrument,
                                           self.span, self.per_year)
            if sigma is None or math.isnan(sigma):
                continue
            targets[instrument] = target_weight(signal(votes), len(self.symbols), sigma,
                                                self.vol_target)
        return Decision(weights=targets, band=self.band)


class CarryExample:
    """An H2-shaped strategy: each coin enters carry when its trailing funding is above
    the entry threshold and exits below the exit threshold."""

    interval = "1d"

    def __init__(self, coins, lookback_days=7, entry_multiple=2, exit_multiple=2,
                 allocation=0.5):
        self.coins = list(coins)
        self.lookback_days = lookback_days
        self.entry, self.exit = carry_thresholds(entry_multiple, exit_multiple, 0.0048,
                                                 lookback_days)
        self.allocation = allocation

    def decide(self, history, book):
        targets = []
        for coin in self.coins:
            instrument = history.instrument(coin)
            if instrument is None:
                continue
            window = trailing_funding(history.series(funding_series(coin)), history.now,
                                      self.lookback_days)
            carry = next_carry_state(book.in_carry(coin), window.total, self.entry, self.exit)
            targets.append(CarryTarget(instrument, coin, self.allocation if carry else 0.0))
        return Decision(carry=tuple(targets))
