"""The data a run reads, and when each row becomes known.

The engine reads only rows: it opens no file, socket or process, and its one way to
data from inside a harness run is the runner's ``DataView`` (``ViewSource``), every
load passing ``accept_recorded_gaps=True`` (section 5, Gaps). Rows are the loader's
``formats.Kline`` and ``formats.Funding`` tuples, so the series it reads are the ones
the development manifest pins: spot klines at 1d, 4h and 15m for the candidates, the
BTC and ETH perpetual klines at 1d, 4h and 15m, and the perpetuals' funding rates.

A row becomes known ("available") at a fixed instant, the same in a backtest and in
paper trading:

* a bar at its close, its open plus its interval (section 3: a decision is taken "on
  the close of the previous day's bar"), which is the kline's ``close_time`` plus 1 ms;
* a funding settlement 60 seconds before its ``calc_time`` (section 5, Funding: known
  at decision time t if its ``calc_time`` is at most t plus 60 seconds).
"""

from __future__ import annotations

import bisect
import itertools
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ..formats import INTERVAL_MS, DatasetSpec, Kline
from .model import DAY_MS, FUNDING_KNOWN_SLACK_MS, Instrument, SimulationError

FILL_INTERVAL = "15m"


def step_of(name: str) -> int | None:
    """A bar series' interval in ms; None for a funding series."""
    spec = DatasetSpec.from_name(name)
    return INTERVAL_MS[spec.interval] if spec.is_bars else None


def available_at(row, step: int | None) -> int:
    if step is None:
        return row.calc_time - FUNDING_KNOWN_SLACK_MS
    return row.open_time + step


def has_volume(bar: Kline) -> bool:
    """Section 5, Fills: a bar "with volume"; its VWAP is quote volume over volume."""
    return bar.volume > 0


def vwap(bar: Kline) -> float:
    """Section 5, Fills: "the volume-weighted average price of the 15-minute bar ...
    computed as the bar's quote-asset volume divided by its base volume"."""
    return bar.quote_volume / bar.volume


class SeriesView(Sequence):
    """The first ``count`` rows of a series: what is known at the moment it is taken.

    The batch and streaming drivers both hand strategies this class over the rows known
    so far, so the two compute from identical inputs in identical order."""

    __slots__ = ("_count", "_rows", "_times", "name")

    def __init__(self, name: str, rows: Sequence, times: Sequence[int], count: int) -> None:
        self.name = name
        self._rows = rows
        self._times = times
        self._count = count

    def __len__(self) -> int:
        return self._count

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self._rows[i] for i in range(*index.indices(self._count))]
        if index < 0:
            index += self._count
        if not 0 <= index < self._count:
            raise IndexError("row not known at this time")
        return self._rows[index]

    def index_of(self, time: int) -> int | None:
        """The index of the row opening (or settling) exactly at ``time``."""
        i = bisect.bisect_left(self._times, time, 0, self._count)
        return i if i < self._count and self._times[i] == time else None

    def at(self, time: int):
        i = self.index_of(time)
        return None if i is None else self._rows[i]

    def last_at_or_before(self, time: int) -> int | None:
        """The index of the last known row opening (or settling) at or before ``time``."""
        i = bisect.bisect_right(self._times, time, 0, self._count) - 1
        return i if i >= 0 else None

    def index_after(self, time: int) -> int:
        """The index of the first known row opening (or settling) after ``time``."""
        return bisect.bisect_right(self._times, time, 0, self._count)


class Segments:
    """Section 4: a spot symbol's daily bars split into instruments at every missing
    daily bar. Fed bars in order, by the backtest all at once and by paper trading as
    they arrive, so both see the same instruments."""

    __slots__ = ("_last", "_start_index", "_starts")

    def __init__(self) -> None:
        self._starts: list[int] = []  # first open of each instrument
        self._start_index: list[int] = []  # index of its first bar
        self._last: int | None = None

    def add(self, open_time: int, index: int) -> None:
        if self._last is None or open_time != self._last + DAY_MS:
            # "A missing daily bar ends the instrument, and later bars of the same
            # symbol form a new instrument with no history."
            self._starts.append(open_time)
            self._start_index.append(index)
        self._last = open_time

    def start_of(self, index: int) -> tuple[int, int]:
        """The first open and first index of the instrument holding bar ``index``."""
        k = bisect.bisect_right(self._start_index, index) - 1
        return self._starts[k], self._start_index[k]


class Series:
    """A whole series held in memory, with each row's availability instant.

    ``horizon`` is where the data was read up to: a daily series whose rows stop
    before it has ended there (section 4), and one that reaches it has not."""

    def __init__(self, name: str, rows: Iterable, horizon: int | None = None) -> None:
        self.name = name
        self.horizon = horizon
        self.step = step_of(name)
        self.rows = list(rows)
        key = (lambda r: r.calc_time) if self.step is None else (lambda r: r.open_time)
        self.times = [key(r) for r in self.rows]
        if any(b <= a for a, b in itertools.pairwise(self.times)):
            raise SimulationError(f"{name}: rows are not in strictly increasing time order")
        self.available = [available_at(r, self.step) for r in self.rows]
        self._ends: dict[Instrument, int | None] = {}
        self.segments: Segments | None = None
        if self.step == DAY_MS and name.startswith("spot/"):
            self.segments = Segments()
            for i, t in enumerate(self.times):
                self.segments.add(t, i)

    def known(self, now: int) -> int:
        """How many rows are known at ``now``."""
        return bisect.bisect_right(self.available, now)

    def view(self, now: int) -> SeriesView:
        return SeriesView(self.name, self.rows, self.times, self.known(now))

    def instrument_end(self, instrument: Instrument) -> int | None:
        """The instant ``instrument`` stops trading: the close of its last daily bar,
        when a later day has no bar. Section 4 looks one bar ahead "only to learn that
        a bar is the last one"; None while the series continues."""
        if instrument not in self._ends:
            self._ends[instrument] = self._instrument_end(instrument)
        return self._ends[instrument]

    def _instrument_end(self, instrument: Instrument) -> int | None:
        i = bisect.bisect_left(self.times, instrument.first_open)
        if i >= len(self.times) or self.times[i] != instrument.first_open:
            raise SimulationError(f"{instrument} is not a run of {self.name}")
        while i + 1 < len(self.times) and self.times[i + 1] == self.times[i] + DAY_MS:
            i += 1
        close = self.times[i] + DAY_MS
        if i + 1 < len(self.times):
            return close  # a later bar follows a missing day
        # The series' last row ends the instrument only if the data was read past it,
        # so a run never ends an instrument at the edge of the data it loaded.
        return close if self.horizon is not None and close < self.horizon else None


# --- fill bars: 15-minute series, read lazily -------------------------------------------


class FillSource:
    """Where the backtest reads 15-minute bars: only to find fills (section 5, Fills)
    and funding marks (section 5, Funding). They are never shown to a strategy."""

    def bars(self, name: str, start: int, end: int) -> list[Kline]:  # pragma: no cover
        raise NotImplementedError


class RowsSource(FillSource):
    """15-minute bars already in memory (synthetic data and small runs)."""

    def __init__(self, series: Mapping[str, Iterable[Kline]]) -> None:
        self._series = {name: Series(name, rows) for name, rows in series.items()}

    def bars(self, name: str, start: int, end: int) -> list[Kline]:
        series = self._series.get(name)
        if series is None:
            raise SimulationError(f"{name} was not loaded")
        lo = bisect.bisect_left(series.times, start)
        hi = bisect.bisect_left(series.times, end)
        return series.rows[lo:hi]


@dataclass(frozen=True)
class Span:
    """The open times of the first and last row of one archived run of a series, as the
    committed coverage metadata (research/coverage/series-coverage.json) records them.
    The loader refuses a range its data does not span and a month the manifest pins no
    file for, so reads stay inside the runs. A series whose archive skips whole months
    (FTTUSDT's suspension from 2022-12 to 2023-08, for example, the coverage file's
    ``missing_months``) is given as a tuple of runs split there; gaps inside a pinned
    month stay inside one run and are crossed as recorded (section 5, Gaps)."""

    first_open: int
    last_open: int


def runs_of(spans) -> tuple[Span, ...]:
    """One span, or several in time order, as a tuple of runs."""
    runs = (spans,) if isinstance(spans, Span) else tuple(spans)
    if not runs or any(not isinstance(r, Span) or r.first_open > r.last_open for r in runs):
        raise SimulationError(f"spans must be Span runs, not {spans!r}")
    if any(b.first_open <= a.last_open for a, b in itertools.pairwise(runs)):
        raise SimulationError("a series' runs must be in time order and not overlap")
    return runs


def _load_runs(load: Callable, name: str, runs: Sequence[Span], lo: int, hi: int,
               gaps: dict[str, set]) -> list:
    """Every row of ``name`` in [lo, hi), read run by run, recording crossed gaps."""
    step = step_of(name) or 1
    rows: list = []
    for run in runs:
        start, end = max(lo, run.first_open), min(hi, run.last_open + step)
        if start < end:
            loaded = load(name, _dt(start), _dt(end), accept_recorded_gaps=True)
            rows.extend(loaded.rows)
            # Section 5, Gaps: "a gap the committed manifest records is crossed and
            # listed in the run's ledger entry".
            gaps.setdefault(name, set()).update(
                (g.after, g.before, g.missing) for g in loaded.gaps
            )
    return rows


class ViewSource(FillSource):
    """15-minute bars read through the runner's ``DataView`` a calendar month at a
    time, as a fill search reaches them, keeping only the months in use: the
    candidates' 15-minute bars would not fit in memory at once."""

    def __init__(self, load: Callable, spans: Mapping, keep_months: int = 3) -> None:
        self._load = load
        self._spans = {name: runs_of(s) for name, s in spans.items()}
        self._cache: dict[tuple[str, int], list[Kline]] = {}
        self._order: list[tuple[str, int]] = []
        self._keep = keep_months * max(1, len(self._spans))
        self.gaps: dict[str, set] = {}

    def _month(self, name: str, month_start: int) -> list[Kline]:
        key = (name, month_start)
        if key not in self._cache:
            runs = self._spans.get(name)
            if runs is None:
                raise SimulationError(f"{name} has no recorded span, so it cannot be read")
            self._cache[key] = _load_runs(self._load, name, runs, month_start,
                                          _next_month(month_start), self.gaps)
            self._order.append(key)
            while len(self._order) > self._keep:
                self._cache.pop(self._order.pop(0), None)
        return self._cache[key]

    def bars(self, name: str, start: int, end: int) -> list[Kline]:
        out: list[Kline] = []
        month = _month_start(start)
        while month < end:
            out.extend(r for r in self._month(name, month) if start <= r.open_time < end)
            month = _next_month(month)
        return out


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def _month_start(ms: int) -> int:
    d = _dt(ms)
    return int(datetime(d.year, d.month, 1, tzinfo=UTC).timestamp() * 1000)


def _next_month(month_start: int) -> int:
    d = _dt(month_start)
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)


# --- the backtest's market --------------------------------------------------------------


class Market:
    """Everything a backtest reads: the series a strategy may see (daily and four-hour
    bars, funding), held whole, and a ``FillSource`` for 15-minute bars.

    ``horizon`` is the end of the data, the run's window end: a daily series whose rows
    stop before it has ended (section 4); one that runs to it has not.
    """

    def __init__(self, series: Mapping[str, Iterable], fills: FillSource, horizon: int) -> None:
        self.series: dict[str, Series] = {}
        for name, rows in series.items():
            if name.endswith("/" + FILL_INTERVAL):
                raise SimulationError(f"{name}: 15-minute bars belong in the fill source")
            self.series[name] = Series(name, rows, horizon)
        self.fills = fills
        self.horizon = horizon
        self.gaps: dict[str, set] = {}

    def crossed_gaps(self) -> dict[str, list]:
        """The recorded gaps every load returned, by series (section 5, Gaps), for the
        run's ledger entry once the runner has a channel for them."""
        merged: dict[str, set] = {n: set(g) for n, g in self.gaps.items()}
        if isinstance(self.fills, ViewSource):
            for name, gaps in self.fills.gaps.items():
                merged.setdefault(name, set()).update(gaps)
        return {name: sorted(gaps) for name, gaps in sorted(merged.items()) if gaps}

    @classmethod
    def from_rows(cls, rows: Mapping[str, Iterable], horizon: int) -> Market:
        """A market from rows in memory, the 15-minute ones read as fill bars."""
        fills = {n: r for n, r in rows.items() if n.endswith("/" + FILL_INTERVAL)}
        rest = {n: r for n, r in rows.items() if n not in fills}
        return cls(rest, RowsSource(fills), horizon)

    @classmethod
    def from_view(cls, data_view, spans: Mapping, horizon: int, history_from: int) -> Market:
        """A market read through the runner's ``DataView``: each series in ``spans``
        (a ``Span`` or a tuple of runs) that is not 15-minute is loaded whole, run by
        run, from ``history_from`` to ``horizon``, and 15-minute ones lazily. Section
        5, Gaps: every load accepts the gaps the committed manifest records, and only
        those."""
        whole: dict[str, list] = {}
        fill_spans: dict[str, tuple[Span, ...]] = {}
        gaps: dict[str, set] = {}
        for name, span in spans.items():
            runs = runs_of(span)
            if name.endswith("/" + FILL_INTERVAL):
                fill_spans[name] = runs
                continue
            whole[name] = _load_runs(data_view.load, name, runs, history_from, horizon, gaps)
        market = cls(whole, ViewSource(data_view.load, fill_spans), horizon)
        market.gaps = gaps
        return market
