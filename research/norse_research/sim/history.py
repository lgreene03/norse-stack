"""What a strategy sees when it decides: the rows known at that moment, nothing later.

Both drivers build this one class over the rows known at the decision (a backtest by
counting the loaded rows available by then, paper trading by keeping what has
arrived), so a strategy computes from identical inputs in either. A backtest's
strategy cannot read past ``now``: ``SeriesView`` refuses any row not yet known.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass as _dataclass
from math import sqrt

from ..formats import EngineClass
from .market import Segments, SeriesView
from .model import DAY_MS, Instrument, SimulationError, spot_series


@_dataclass
class _Ewm(metaclass=EngineClass):
    next_index: int
    numerator: float
    denominator: float
    previous_close: float | None


class History(metaclass=EngineClass):
    """What a strategy sees at a decision. The engine lends the strategy a copy
    (``lent``) and reads its own, so whatever a strategy sets on the one it holds
    reaches only that copy (security review of PR 61)."""

    def __init__(
        self,
        now: int,
        interval: str,
        views: Callable[[str], SeriesView | None],
        segments: Callable[[str], Segments | None],
        ended: Callable[[Instrument], bool],
        cache: dict,
    ) -> None:
        self.now = now
        self.interval = interval
        self._views = views
        self._segments = segments
        self._ended = ended
        self._cache = cache

    def lent(self) -> History:
        """A history for the strategy alone, over the same rows: the engine reads its
        own after the decision (``now`` among them, to build the orders), so nothing a
        strategy sets on the one it is lent reaches the engine."""
        return History(self.now, self.interval, self._views, self._segments, self._ended,
                       self._cache)

    def series(self, name: str) -> SeriesView:
        """The known rows of a daily, four-hour or funding series. Fifteen-minute bars
        are the engine's, for fills, and are not shown."""
        view = self._views(name)
        if view is None:
            raise SimulationError(f"{name} is not in this run's data")
        return view

    def has(self, name: str) -> bool:
        return self._views(name) is not None

    def instrument(self, symbol: str) -> Instrument | None:
        """Section 4: the instrument holding ``symbol``'s daily bar that closed at the
        latest 00:00 at or before now, or None if it has no such bar. This uses no
        look-ahead: an instrument whose last bar closed now is still returned, and
        ``ended`` says it has stopped (section 6's eligibility asks only whether the
        instrument "has the month-end daily bar")."""
        name = spot_series(symbol, "1d")
        view = self.series(name)
        midnight = self.now - self.now % DAY_MS
        index = view.index_of(midnight - DAY_MS)
        if index is None:
            return None
        segments = self._segments(name)
        if segments is None:
            raise SimulationError(f"{name} is not split into instruments")
        first_open, _ = segments.start_of(index)
        return Instrument(symbol, first_open)

    def ended(self, instrument: Instrument) -> bool:
        """Section 4 (D12): the instrument stopped trading at or before now, before the
        end of the window. The backtest learns it by looking one daily bar ahead, which
        section 4 permits "only to learn that a bar is the last one"; paper trading is
        told by the feed."""
        return self._ended(instrument)

    def live(self, instrument: Instrument) -> bool:
        return self.instrument(instrument.symbol) == instrument and not self.ended(instrument)

    def close_at(self, name: str, close_instant: int, step: int) -> float | None:
        """The close of the bar of ``name`` that closed exactly at ``close_instant``."""
        bar = self.series(name).at(close_instant - step)
        return None if bar is None else bar.close

    def ewm_volatility(
        self, name: str, instrument: Instrument, span: float, periods_per_year: float
    ) -> float | None:
        """Section 6, Sizing: "the square root of the exponentially weighted mean of
        squared daily simple returns, not demeaned, with span 60 days (alpha = 2/61)
        and weights (1 - alpha)^j over every return of the instrument (the pandas
        ewm(span=60, adjust=True) convention), annualised by the square root of 365".

        Over the bars of ``name`` from the instrument's first daily open on, returns
        between consecutive known bars. Kept as a running numerator and denominator,
        updated as bars become known, so a backtest and paper trading add the same
        terms in the same order. None until the instrument has one return.
        """
        if span < 1:
            raise SimulationError("an exponentially weighted span is at least 1")
        alpha = 2.0 / (span + 1.0)
        view = self.series(name)
        key = (name, instrument.first_open, span)
        state = self._cache.get(key)
        if state is None:
            start = view.index_after(instrument.first_open - 1)
            state = self._cache[key] = _Ewm(start, 0.0, 0.0, None)
        decay = 1.0 - alpha
        for i in range(state.next_index, len(view)):
            close = view[i].close
            if state.previous_close is not None:
                r = close / state.previous_close - 1.0
                state.numerator = r * r + decay * state.numerator
                state.denominator = 1.0 + decay * state.denominator
            state.previous_close = close
        state.next_index = max(state.next_index, len(view))
        if state.denominator == 0:
            return None
        return sqrt(state.numerator / state.denominator) * sqrt(periods_per_year)
