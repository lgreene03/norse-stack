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

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass as _dataclass
from datetime import UTC, datetime
from itertools import pairwise

from ..formats import (
    _ENGINE_TOKEN,
    INTERVAL_MS,
    DatasetSpec,
    EngineABCMeta,
    EngineClass,
    Frozen,
    Kline,
    build,
    read_only,
)
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


class SeriesView(Sequence, Frozen, metaclass=EngineABCMeta, engine=_ENGINE_TOKEN):
    """The first ``count`` rows of a series: what is known at the moment it is taken.

    The batch and streaming drivers both hand strategies this class over the rows known
    so far, so the two compute from identical inputs in identical order. Its lookups are
    closures that stop at ``count``, held as its attributes: none leads to a later row
    or to the list the engine reads, and nothing about the view can be changed once it
    is built (security review of PR 61).

    ``index_of(time)``: the index of the row opening (or settling) exactly at ``time``.
    ``at(time)``: that row, or None. ``last_at_or_before(time)``: the index of the last
    known row opening (or settling) at or before ``time``. ``index_after(time)``: the
    index of the first known row opening (or settling) after ``time``."""

    __slots__ = ("_count", "_row", "at", "index_after", "index_of", "last_at_or_before",
                 "name")

    def __init__(self, name: str, rows: Sequence, times: Sequence[int], count: int) -> None:
        def row(index: int):
            if 0 <= index < count:
                return rows[index]
            raise IndexError("row not known at this time")

        def index_of(time: int) -> int | None:
            i = bisect_left(times, time, 0, count)
            return i if i < count and times[i] == time else None

        def at(time: int):
            i = bisect_left(times, time, 0, count)
            return rows[i] if i < count and times[i] == time else None

        def last_at_or_before(time: int) -> int | None:
            i = bisect_right(times, time, 0, count) - 1
            return i if i >= 0 else None

        def index_after(time: int) -> int:
            return bisect_right(times, time, 0, count)

        build(self, name=name, _count=count, _row=row, index_of=index_of, at=at,
              last_at_or_before=last_at_or_before, index_after=index_after)

    def __len__(self) -> int:
        return self._count

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self._row(i) for i in range(*index.indices(self._count))]
        return self._row(index + self._count if index < 0 else index)


class Segments(metaclass=EngineClass):
    """Section 4: a spot symbol's daily bars split into instruments at every missing
    daily bar. Fed bars in order, by the backtest all at once and by paper trading as
    they arrive, so both see the same instruments. Only the paper driver keeps one that
    grows; what a series holds, and what a history lends, is ``fixed()``."""

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
        k = bisect_right(self._start_index, index) - 1
        return self._starts[k], self._start_index[k]

    def fixed(self) -> FixedSegments:
        """The instruments so far, as they can never change."""
        return FixedSegments(self._starts, self._start_index)


class FixedSegments(Frozen, engine=_ENGINE_TOKEN):
    """``Segments`` that can never change: held in tuples, built once."""

    __slots__ = ("_start_index", "_starts")

    def __init__(self, starts: Iterable[int], start_index: Iterable[int]) -> None:
        build(self, _starts=tuple(starts), _start_index=tuple(start_index))

    def start_of(self, index: int) -> tuple[int, int]:
        """The first open and first index of the instrument holding bar ``index``."""
        k = bisect_right(self._start_index, index) - 1
        return self._starts[k], self._start_index[k]


class Series(Frozen, engine=_ENGINE_TOKEN):
    """A whole series held in memory, with each row's availability instant.

    ``horizon`` is where the data was read up to: a daily series whose rows stop
    before it has ended there (section 4), and one that reaches it has not. Its rows,
    times and availability are tuples and nothing about it can be changed once built,
    so a strategy holding the market cannot alter what the engine reads (security
    review of PR 61); the one memo it keeps is inside a closure."""

    def __init__(self, name: str, rows: Iterable, horizon: int | None = None) -> None:
        step = step_of(name)
        rows = tuple(rows)
        key = (lambda r: r.calc_time) if step is None else (lambda r: r.open_time)
        times = tuple(key(r) for r in rows)
        if any(b <= a for a, b in pairwise(times)):
            raise SimulationError(f"{name}: rows are not in strictly increasing time order")
        segments = None
        if step == DAY_MS and name.startswith("spot/"):
            growing = Segments()
            for i, t in enumerate(times):
                growing.add(t, i)
            segments = growing.fixed()
        available = tuple(available_at(r, step) for r in rows)
        ends: dict[Instrument, int | None] = {}
        latest: list = [None, None]  # the known count and its view

        def instrument_end(instrument: Instrument) -> int | None:
            if instrument not in ends:
                ends[instrument] = _instrument_end(name, times, horizon, instrument)
            return ends[instrument]

        def view(now: int) -> SeriesView:
            # A view cannot change, so the engine and the strategy may share the one for
            # the rows known now; a run asks for it many times at one instant.
            count = bisect_right(available, now)
            if latest[0] != count:
                latest[0], latest[1] = count, SeriesView(name, rows, times, count)
            return latest[1]

        build(self, name=name, horizon=horizon, step=step, rows=rows, times=times,
              available=available, segments=segments, _instrument_end=instrument_end,
              _view=view)

    def known(self, now: int) -> int:
        """How many rows are known at ``now``."""
        return bisect_right(self.available, now)

    def view(self, now: int) -> SeriesView:
        return self._view(now)

    def stops(self) -> list[int]:
        """Every instant a run of this daily series stops: the close of each run's last
        bar where a later day has no bar, the series' last one included when it closes
        before the horizon."""
        out = []
        for i, open_time in enumerate(self.times):
            following = self.times[i + 1] if i + 1 < len(self.times) else None
            if following == open_time + DAY_MS:
                continue
            close = open_time + DAY_MS
            if following is None and (self.horizon is None or close >= self.horizon):
                continue
            out.append(close)
        return out

    def instrument_end(self, instrument: Instrument) -> int | None:
        """The instant ``instrument`` stops trading: the close of its last daily bar,
        when a later day has no bar. Section 4 looks one bar ahead "only to learn that
        a bar is the last one"; None while the series continues."""
        return self._instrument_end(instrument)


def _instrument_end(name: str, times: Sequence[int], horizon: int | None,
                    instrument: Instrument) -> int | None:
    i = bisect_left(times, instrument.first_open)
    if i >= len(times) or times[i] != instrument.first_open:
        raise SimulationError(f"{instrument} is not a run of {name}")
    while i + 1 < len(times) and times[i + 1] == times[i] + DAY_MS:
        i += 1
    close = times[i] + DAY_MS
    if i + 1 < len(times):
        return close  # a later bar follows a missing day
    # The series' last row ends the instrument only if the data was read past it,
    # so a run never ends an instrument at the edge of the data it loaded.
    return close if horizon is not None and close < horizon else None


# --- fill bars: 15-minute series, read lazily -------------------------------------------


class FillSource(Frozen, engine=_ENGINE_TOKEN):
    """Where the backtest reads 15-minute bars: only to find fills (section 5, Fills)
    and funding marks (section 5, Funding). They are never shown to a strategy, and each
    call returns a list of its own."""

    def bars(self, name: str, start: int, end: int) -> list[Kline]:  # pragma: no cover
        raise NotImplementedError

    def crossed(self) -> dict[str, set]:
        """The recorded gaps its loads crossed, by series."""
        return {}


class RowsSource(FillSource, engine=_ENGINE_TOKEN):
    """15-minute bars already in memory (synthetic data and small runs)."""

    def __init__(self, series: Mapping[str, Iterable[Kline]]) -> None:
        build(self, _series=read_only({name: Series(name, rows) for name, rows in series.items()}))

    def bars(self, name: str, start: int, end: int) -> list[Kline]:
        series = self._series.get(name)
        if series is None:
            raise SimulationError(f"{name} was not loaded")
        lo = bisect_left(series.times, start)
        hi = bisect_left(series.times, end)
        return list(series.rows[lo:hi])


@_dataclass(frozen=True)
class Span(metaclass=EngineClass):
    """The times of the first and last row of one archived run of a series, as the
    runner's ``DataView.coverage`` reports them from the committed manifests' metadata.
    The loader refuses a range its data does not span and a month the manifest pins no
    file for, so reads stay inside the runs. A series whose archive skips whole months
    (FTTUSDT's suspension from 2022-12 to 2023-08, for example) comes as several runs
    split there; gaps inside pinned months stay inside one run and are crossed as
    recorded (section 5, Gaps)."""

    first_open: int
    last_open: int


def runs_of(spans) -> tuple[Span, ...]:
    """One span, or several in time order, as a tuple of runs."""
    runs = (spans,) if isinstance(spans, Span) else tuple(spans)
    if not runs or any(not isinstance(r, Span) or r.first_open > r.last_open for r in runs):
        raise SimulationError(f"spans must be Span runs, not {spans!r}")
    if any(b.first_open <= a.last_open for a, b in pairwise(runs)):
        raise SimulationError("a series' runs must be in time order and not overlap")
    return runs


def _load_runs(load: Callable, name: str, runs: Sequence[Span], lo: int, hi: int,
               gaps: dict[str, set]) -> list:
    """Every row of ``name`` in [lo, hi), read run by run, recording crossed gaps.

    Coverage times have the manifests' minute precision: exact for a bar, while a
    funding settlement's ``calc_time`` lies up to a minute after it (the archive's
    millisecond jitter), so a funding run's read ends a minute after its last time."""
    step = step_of(name)
    rows: list = []
    for run in runs:
        pad = step if step is not None else FUNDING_KNOWN_SLACK_MS
        start, end = max(lo, run.first_open), min(hi, run.last_open + pad)
        if start < end:
            loaded = load(name, _dt(start), _dt(end), accept_recorded_gaps=True)
            first = loaded.rows[0][0] if loaded.rows else None
            if first is not None:
                # The first on-grid bar, or the settlement's minute.
                first = first if step is not None else first - first % 60_000
            expected = run.first_open if step is None else -(-run.first_open // step) * step
            if start == run.first_open and first != expected:
                # A run that starts later than its span says would move the instrument's
                # first bar (section 4), and with it D13's 120-day rule and section 6's
                # volatility "over every return of the instrument".
                raise SimulationError(
                    f"{name}: the span's run starts at {_dt(run.first_open).isoformat()}, "
                    f"but the archive's first row there is "
                    f"{_dt(first).isoformat() if first is not None else 'missing'}"
                )
            rows.extend(loaded.rows)
            # Section 5, Gaps: "a gap the committed manifest records is crossed and
            # listed in the run's ledger entry".
            gaps.setdefault(name, set()).update(
                (g.after, g.before, g.missing) for g in loaded.gaps
            )
    return rows


class ViewSource(FillSource, engine=_ENGINE_TOKEN):
    """15-minute bars read through the runner's ``DataView`` a calendar month at a
    time, as a fill search reaches them, keeping only the months in use: the
    candidates' 15-minute bars would not fit in memory at once. The cache and the gaps
    crossed are held in closures, so nothing outside can alter what a fill reads."""

    def __init__(self, load: Callable, spans: Mapping, keep_months: int = 3) -> None:
        runs_by_name = {name: runs_of(s) for name, s in spans.items()}
        cache: dict[tuple[str, int], tuple[Kline, ...]] = {}
        order: list[tuple[str, int]] = []
        keep = keep_months * max(1, len(runs_by_name))
        gaps: dict[str, set] = {}

        def month(name: str, month_start: int) -> tuple[Kline, ...]:
            key = (name, month_start)
            if key not in cache:
                runs = runs_by_name.get(name)
                if runs is None:
                    raise SimulationError(f"{name} has no recorded span, so it cannot be read")
                cache[key] = tuple(_load_runs(load, name, runs, month_start,
                                              _next_month(month_start), gaps))
                order.append(key)
                while len(order) > keep:
                    cache.pop(order.pop(0), None)
            return cache[key]

        def crossed() -> dict[str, set]:
            return {name: set(found) for name, found in gaps.items()}

        build(self, _month=month, _crossed=crossed)

    def bars(self, name: str, start: int, end: int) -> list[Kline]:
        out: list[Kline] = []
        month = _month_start(start)
        while month < end:
            out.extend(r for r in self._month(name, month) if start <= r.open_time < end)
            month = _next_month(month)
        return out

    def crossed(self) -> dict[str, set]:
        return self._crossed()


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


class Market(Frozen, engine=_ENGINE_TOKEN):
    """Everything a backtest reads: the series a strategy may see (daily and four-hour
    bars, funding), held whole, and a ``FillSource`` for 15-minute bars. A strategy that
    builds one keeps it, so it is read-only once built, its series in a read-only
    mapping (security review of PR 61).

    ``horizon`` is the end of the data, the run's window end: a daily series whose rows
    stop before it has ended (section 4); one that runs to it has not.
    """

    def __init__(self, series: Mapping[str, Iterable], fills: FillSource, horizon: int,
                 gaps: Mapping[str, Iterable] | None = None) -> None:
        held: dict[str, Series] = {}
        for name, rows in series.items():
            if name.endswith("/" + FILL_INTERVAL):
                raise SimulationError(f"{name}: 15-minute bars belong in the fill source")
            held[name] = Series(name, rows, horizon)
        build(self, series=read_only(held), fills=fills, horizon=horizon,
              gaps=read_only({n: frozenset(g) for n, g in (gaps or {}).items()}))

    def crossed_gaps(self) -> dict[str, list]:
        """The recorded gaps every load returned, by series (section 5, Gaps). The
        ledger takes the loader's; the tests take these."""
        merged: dict[str, set] = {n: set(g) for n, g in self.gaps.items()}
        for name, gaps in self.fills.crossed().items():
            merged.setdefault(name, set()).update(gaps)
        return {name: sorted(gaps) for name, gaps in sorted(merged.items()) if gaps}

    @classmethod
    def from_rows(cls, rows: Mapping[str, Iterable], horizon: int) -> Market:
        """A market from rows in memory, the 15-minute ones read as fill bars."""
        fills = {n: r for n, r in rows.items() if n.endswith("/" + FILL_INTERVAL)}
        rest = {n: r for n, r in rows.items() if n not in fills}
        return cls(rest, RowsSource(fills), horizon)

    @classmethod
    def from_view(cls, data_view, series: Iterable[str], horizon: int,
                  history_from: int | None = None) -> Market:
        """A market read through the runner's ``DataView``: each named series that is not
        15-minute is loaded whole, run by run, from its first archived row to
        ``horizon``, and 15-minute ones lazily. The runs come from
        ``data_view.coverage``, which reads the committed manifests' metadata, so no
        strategy commits a span and a span error is a data-loading fix (section 11, item
        6). Section 5, Gaps: every load accepts the gaps the committed manifest records,
        and only those. ``backtest`` refuses a ``horizon`` other than the window's end.

        ``history_from`` may only start the load at or before every series' first
        archived row: starting later would start each instrument late, so a pair would
        miss D13's close 120 days before a month end it really has, and its volatility
        would leave out returns section 6 counts ("over every return of the
        instrument"). Such a start is refused."""
        whole: dict[str, list] = {}
        fill_spans: dict[str, tuple[Span, ...]] = {}
        gaps: dict[str, set] = {}
        for name in series:
            runs = runs_of([Span(first, last) for first, last in data_view.coverage(name)])
            if name.endswith("/" + FILL_INTERVAL):
                fill_spans[name] = runs
                continue
            if history_from is not None and history_from > runs[0].first_open:
                raise SimulationError(
                    f"{name}: loading from {_dt(history_from).date()} would drop its rows "
                    f"from {_dt(runs[0].first_open).date()}, starting its instrument late "
                    "(D13's 120-day rule, section 6's volatility over every return)"
                )
            whole[name] = _load_runs(data_view.load, name, runs, runs[0].first_open, horizon,
                                     gaps)
        return cls(whole, ViewSource(data_view.load, fill_spans), horizon, gaps)
