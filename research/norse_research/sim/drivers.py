"""One strategy interface, two drivers: a backtest over loaded data and paper trading
fed one row at a time (G1.9).

A strategy is any object with an ``interval`` ("1d" or "4h") and a method

    decide(history: History, book: BookView) -> Decision | None

called at every decision time with the rows known then and the book marked then. The
backtest (``backtest``) calls it over a ``Market`` read up front; paper trading
(``Paper``) calls the same object as rows arrive. The rules that turn decisions into
orders and bars into fills are shared (``execution``); the drivers differ in how they
learn which bar fills an order and when an instrument stops, and the golden-fixture
test holds them to identical orders, fills, funding and returns.

Events at one instant apply in a fixed order, the same in both drivers: fills on bars
closing then (a position changes at its fill bar's close, section 5); lapses of orders
whose search ends then (section 5, Fills); the equity mark (section 3); closes of
instruments that stopped then (section 4); funding settled then (section 5, Funding);
and last the decision (section 3). The mark comes before the closes and the funding,
so anything stamped exactly at a 00:00 mark counts in the day that starts there: a
day's return never depends on data stamped after it, and a holdout run's returns up
to 2025-12-31 equal the development run's exactly (section 11, item 5), whatever is
stamped at 2026-01-01 00:00:00.000.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from heapq import heappop, heappush
from itertools import count

from ..formats import EngineClass, Funding, Kline
from .book import Book
from .execution import (
    accrue,
    execute_bar,
    fill_series,
    first_fill_bar,
    funding_mark,
    halt,
    marks_at,
    orders_for,
    unit_of,
)
from .history import History
from .market import Market, Segments, Series, SeriesView, available_at, has_volume, step_of
from .model import (
    DAY_MS,
    FIFTEEN_MINUTES_MS,
    PERP,
    SPOT,
    Accrual,
    Costs,
    Decision,
    Fill,
    Instrument,
    Lapse,
    Order,
    SimulationError,
    Skip,
    Window,
    decision_step,
    funding_series,
    iso_day,
    perp_series,
    spot_series,
)

# The order of events at one instant (module docstring). NOTICE and DATA are when paper
# trading's feed delivers an end notice or a settlement; their effects, HALT and
# FUNDING, apply after the MARK.
FILL, LAPSE, NOTICE, DATA, MARK, HALT, FUNDING, DECIDE = range(8)
FUNDING_MARK_LOOKBACK_MS = 3 * DAY_MS
# Section 5, Marks, carries a close forward through an exchange-wide outage, while
# section 4 ends an instrument at any missing daily bar; section 4 notes no such outage
# in development. Both majors stopping at one instant is taken as an outage, and the
# run stops rather than halt every instrument at its low.
OUTAGE_WITNESSES = ("BTCUSDT", "ETHUSDT")


def outage_message(at: int) -> str:
    return (
        f"BTCUSDT and ETHUSDT spot both stop at {iso_day(at)}: an exchange-wide outage, for "
        "which section 5 carries closes forward and section 4 would end every instrument; "
        "neither happened in development, so the run stops rather than choose"
    )


def check_outage(witnesses: list[Series | None], before: int) -> None:
    """Refuse data in which both majors' spot daily series stop at one instant before
    ``before`` (the backtest's half of the outage check; paper trading checks its end
    notices). A witness that is not loaded leaves the check undecided, so it passes."""
    if any(w is None for w in witnesses):
        return
    common = set.intersection(*(set(w.stops()) for w in witnesses))
    early = sorted(t for t in common if t < before)
    if early:
        raise SimulationError(outage_message(early[0]))


class BookView(metaclass=EngineClass):
    """The book as a strategy may see it at a decision: read-only, marked then."""

    def __init__(self, book: Book, marks: Mapping, equity: float) -> None:
        self._book = book
        self._marks = dict(marks)
        self.equity = equity

    @property
    def cash(self) -> float:
        return self._book.cash

    def quantity(self, market: str, symbol: str) -> float:
        return self._book.quantity(market, symbol)

    def weight(self, symbol: str) -> float:
        """A spot position's weight, as a fraction of equity marked at the decision."""
        held = self._book.spot.get(symbol, 0.0)
        return held * self._marks[(SPOT, symbol)] / self.equity if held else 0.0

    def in_carry(self, symbol: str) -> bool:
        return symbol in self._book.carry

    def held(self) -> list[str]:
        return sorted(self._book.spot)


@dataclass
class RunResult(metaclass=EngineClass):
    """Everything a run produced. ``returns`` is what the runner hands back to the
    harness: one (day, return) per day of the window (section 3)."""

    returns: list[tuple[str, float]] = field(default_factory=list)
    marks: list[tuple[int, float]] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    lapses: list[Lapse] = field(default_factory=list)
    accruals: list[Accrual] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)


class _Run(metaclass=EngineClass):
    """The state and rules both drivers share."""

    def __init__(self, strategy, window: Window, costs: Costs, cash: float = 1.0) -> None:
        self.strategy = strategy
        self.window = window
        self.costs = costs
        self.step = decision_step(strategy.interval)
        self.book = Book(cash)
        self.result = RunResult()
        self.pending: dict[str, list[Order]] = {}
        self.cache: dict = {}
        self.decision_times = set(window.decisions(strategy.interval))
        self.mark_times = set(window.marks())

    def times(self) -> list[int]:
        return sorted(self.decision_times | self.mark_times)

    def mark(self, history: History) -> None:
        marks = marks_at(history, self.book.needs_marks(), DAY_MS)
        self.result.marks.append((history.now, self.book.equity(marks)))

    def decide(self, history: History) -> list[Order]:
        t = history.now
        if self.pending:
            raise SimulationError("an unfilled order survived the next decision (section 5)")
        marks = marks_at(history, self.book.needs_marks(), self.step)
        missing = [k for k in self.book.needs_marks() if k not in marks]
        if missing:
            if self.step == DAY_MS:
                raise SimulationError(f"no close at {t} for held {missing}")
            self.result.skips.append(Skip(t, tuple(sorted(s for _, s in missing))))
            return []
        equity = self.book.equity(marks)
        decision = self.strategy.decide(history, BookView(self.book, marks, equity))
        if decision is None:
            return []
        if not isinstance(decision, Decision):
            raise SimulationError("a strategy returns a Decision or None")
        # Section 5, Fills: the search ends at the next decision or the window's end,
        # "whichever is earlier".
        deadline = min(t + self.step, self.window.end)
        orders, skip = orders_for(decision, self.book, history, marks, equity, self.step,
                                  deadline)
        if skip is not None:
            self.result.skips.append(skip)
        for order in orders:
            self.pending.setdefault(unit_of(order), []).append(order)
        self.result.orders.extend(orders)
        return orders

    def fill(self, legs: list[tuple[Order, Kline]]) -> None:
        self.result.fills.extend(execute_bar(self.book, self.costs, legs))
        for unit in {unit_of(order) for order, _ in legs}:
            self.pending.pop(unit, None)

    def lapse(self, unit: str, at: int) -> None:
        for order in self.pending.pop(unit, []):
            self.result.lapses.append(Lapse(order, at))

    def halt(self, symbol: str, last_bar: Kline, at: int) -> None:
        fill = halt(self.book, self.costs, symbol, last_bar, at)
        if fill is not None:
            self.result.fills.append(fill)

    def accrue(self, symbol: str, settlement: Funding, mark: float | None) -> None:
        accrual = accrue(self.book, symbol, settlement, mark)
        if accrual is not None:
            self.result.accruals.append(accrual)

    def finish(self) -> RunResult:
        marks = self.result.marks
        expected = sorted(self.mark_times)
        if [t for t, _ in marks] != expected:
            raise SimulationError("the run did not mark every day of its window")
        values = [e for _, e in marks]
        self.result.returns = [
            (iso_day(t), values[i + 1] / values[i] - 1.0) for i, (t, _) in enumerate(marks[:-1])
        ]
        return self.result


# --- the backtest -----------------------------------------------------------------------


def backtest(strategy, market: Market, window: Window, costs: Costs) -> RunResult:
    """Run ``strategy`` over ``window`` on data read up front."""
    if market.horizon != window.end:
        # An instrument ends where its data stops before the horizon (section 4), so a
        # horizon other than the window's end would move every D12 close.
        raise SimulationError(
            f"the market was read to {iso_day(market.horizon)}, not to the window's end "
            f"{iso_day(window.end)}"
        )
    check_outage([market.series.get(spot_series(s, "1d")) for s in OUTAGE_WITNESSES],
                 window.end)
    run = _Run(strategy, window, costs)
    queue: list = []
    counter = count()

    def schedule(time: int, priority: int, tiebreak: str, action) -> None:
        heappush(queue, (time, priority, tiebreak, next(counter), action))

    def drain(time: int, priority: int) -> None:
        while queue and queue[0][:2] < (time, priority):
            heappop(queue)[-1]()

    def ended(instrument: Instrument) -> bool:
        series = market.series.get(spot_series(instrument.symbol, "1d"))
        end = series.instrument_end(instrument) if series is not None else None
        return end is not None and end < window.end and end <= now[0]

    def view(name: str) -> SeriesView | None:
        series = market.series.get(name)
        return series.view(now[0]) if series is not None else None

    def segments(name: str):
        series = market.series.get(name)
        return series.segments if series is not None else None

    now = [window.start]
    times = run.times()
    for k, t in enumerate(times):
        drain(t, MARK)
        now[0] = t
        history = History(t, strategy.interval, view, segments, ended, run.cache)
        if t in run.mark_times:
            run.mark(history)
        drain(t, DECIDE)  # closes and funding stamped at t, after the mark
        if t not in run.decision_times:
            continue
        run.decide(history)
        following = times[k + 1]
        _schedule_step(run, market, history, t, following, schedule)
    drain(window.end, DECIDE)
    return run.finish()


def _schedule_step(run: _Run, market: Market, history: History, t: int, following: int,
                   schedule) -> None:
    """Everything that happens in (t, following]: fills found by searching the loaded
    15-minute bars, lapses, closes of instruments that stop, and funding."""
    deadline = min(t + run.step, run.window.end)
    batches: dict[int, list[tuple[Order, Kline]]] = {}
    for unit in sorted(run.pending):
        legs = run.pending[unit]
        bars = {fill_series(o): market.fills.bars(fill_series(o), t, deadline) for o in legs}
        found = first_fill_bar(bars, t, deadline)
        if found is None:
            schedule(deadline, LAPSE, unit, lambda u=unit: run.lapse(u, deadline))
            continue
        bar_open, by_series = found
        batches.setdefault(bar_open, []).extend((o, by_series[fill_series(o)]) for o in legs)
    for bar_open, legs in batches.items():
        schedule(bar_open + FIFTEEN_MINUTES_MS, FILL, "", lambda legs=legs: run.fill(legs))

    spot = set(run.book.spot) | {o.symbol for u in run.pending.values() for o in u
                                 if o.market == SPOT}
    for symbol in sorted(spot):
        series = market.series.get(spot_series(symbol, "1d"))
        instrument = history.instrument(symbol)
        if series is None or instrument is None:
            continue
        end = series.instrument_end(instrument)
        if end is not None and t < end <= following and end < run.window.end:
            last_bar = series.view(end).at(end - DAY_MS)
            schedule(end, HALT, symbol,
                     lambda s=symbol, b=last_bar, e=end: run.halt(s, b, e))

    perps = set(run.book.perp) | {o.symbol for u in run.pending.values() for o in u
                                  if o.market == PERP}
    for symbol in sorted(perps):
        name = funding_series(symbol)
        series = market.series.get(name)
        if series is None:
            raise SimulationError(f"{name} is not in this run's data")
        lo = bisect_right(series.times, t)
        # A settlement at the window's last mark or later falls in no day of the window.
        hi = bisect_left(series.times, min(following + 1, run.window.end))
        for settlement in series.rows[lo:hi]:
            schedule(settlement.calc_time, FUNDING, name,
                     lambda s=symbol, f=settlement: run.accrue(
                         s, f, _batch_funding_mark(market, s, f.calc_time)))


def _batch_funding_mark(market: Market, symbol: str, calc_time: int) -> float | None:
    bars = market.fills.bars(perp_series(symbol, "15m"), calc_time - FUNDING_MARK_LOOKBACK_MS,
                             calc_time)
    return funding_mark(bars, calc_time)


def run_backtest(strategy, configuration: Mapping, market: Market) -> list[tuple[str, float]]:
    """The daily returns of ``strategy`` over the window and at the costs of a resolved
    configuration, in the shape the runner hands to the harness."""
    window = Window.from_configuration(configuration)
    return backtest(strategy, market, window, Costs.from_configuration(configuration)).returns


# --- paper trading ----------------------------------------------------------------------


@dataclass(frozen=True)
class BarEvent(metaclass=EngineClass):
    """A bar, delivered at its close."""

    series: str
    row: Kline

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.row.open_time + step_of(self.series), FILL, self.series)


@dataclass(frozen=True)
class SettlementEvent(metaclass=EngineClass):
    """A funding settlement, delivered when section 5 counts it known: 60 seconds
    before its ``calc_time``."""

    series: str
    row: Funding

    @property
    def key(self) -> tuple[int, int, str]:
        return (available_at(self.row, None), DATA, self.series)


@dataclass(frozen=True)
class InstrumentEnded(metaclass=EngineClass):
    """Notice that an instrument stopped trading at the close of its last daily bar.
    Section 4's backtest learns it by looking one bar ahead; a live feed learns it from
    the exchange's delisting notice. It arrives before that instant's mark and the close
    it causes applies after it."""

    symbol: str
    last_open: int

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.last_open + DAY_MS, NOTICE, self.symbol)


@dataclass(frozen=True)
class Tick(metaclass=EngineClass):
    """The clock reaching a mark or decision time, after every row known by then."""

    at: int

    @property
    def key(self) -> tuple[int, int, str]:
        return (self.at, MARK, "")


class Paper(metaclass=EngineClass):
    """Paper trading: the same strategy object, fed one row at a time.

    ``push`` takes events in the order of their ``key`` (``replay`` builds such a feed
    from recorded rows). Fifteen-minute bars are kept only as the latest per series,
    so a long run holds no more than the series a strategy reads."""

    def __init__(self, strategy, window: Window, costs: Costs) -> None:
        self.run = _Run(strategy, window, costs)
        self.window = window
        self._rows: dict[str, list] = {}
        self._times: dict[str, list[int]] = {}
        self._segments: dict[str, Segments] = {}
        self._latest: dict[str, Kline] = {}
        self._ended: set[Instrument] = set()
        self._queue: list = []
        self._counter = count()
        self._last_key: tuple = (-1, -1, "")
        self._checks: set[int] = set()
        self._stops: dict[str, set[int]] = {}

    # --- the feed ------------------------------------------------------------------

    def push(self, event) -> None:
        key = event.key
        if key < self._last_key:
            raise SimulationError(f"event {key} arrived after {self._last_key}")
        self._last_key = key
        if key[0] > self.window.end:
            return
        self._drain(key[0], key[1])
        if isinstance(event, BarEvent):
            self._bar(event)
        elif isinstance(event, SettlementEvent):
            self._append(event.series, event.row, event.row.calc_time)
            symbol = event.series.split("/")[2]
            if event.row.calc_time < self.window.end:  # later ones fall in no day
                self._schedule(event.row.calc_time, FUNDING, event.series,
                               lambda s=symbol, f=event.row: self.run.accrue(
                                   s, f, self._funding_mark(s, f.calc_time)))
        elif isinstance(event, InstrumentEnded):
            self._end(event)
        elif isinstance(event, Tick):
            self._tick(event.at)
        else:
            raise SimulationError(f"unknown event {event!r}")

    def finish(self) -> RunResult:
        self._drain(self.window.end, DECIDE)
        return self.run.finish()

    # --- internals -----------------------------------------------------------------

    def _schedule(self, time: int, priority: int, tiebreak: str, action) -> None:
        heappush(self._queue, (time, priority, tiebreak, next(self._counter), action))

    def _drain(self, time: int, priority: int) -> None:
        while self._queue and self._queue[0][:2] < (time, priority):
            heappop(self._queue)[-1]()

    def _append(self, name: str, row, time: int) -> None:
        self._rows.setdefault(name, []).append(row)
        self._times.setdefault(name, []).append(time)

    def _bar(self, event: BarEvent) -> None:
        name, row = event.series, event.row
        if name.endswith("/15m"):
            self._latest[name] = row
            close = row.open_time + FIFTEEN_MINUTES_MS
            wanted = any(
                fill_series(o) == name and o.decided_at <= row.open_time < o.deadline
                for legs in self.run.pending.values() for o in legs
            )
            if wanted and has_volume(row) and close not in self._checks:
                self._checks.add(close)
                self._schedule(close, FILL, "", lambda c=close: self._fill_check(c))
            return
        self._append(name, row, row.open_time)
        if name.startswith("spot/") and name.endswith("/1d"):
            self._segments.setdefault(name, Segments()).add(row.open_time,
                                                            len(self._rows[name]) - 1)

    def _fill_check(self, close: int) -> None:
        self._checks.discard(close)
        bar_open = close - FIFTEEN_MINUTES_MS
        legs: list[tuple[Order, Kline]] = []
        for unit in sorted(self.run.pending):
            orders = self.run.pending[unit]
            if not all(o.decided_at <= bar_open < o.deadline for o in orders):
                continue
            bars = [self._latest.get(fill_series(o)) for o in orders]
            if all(b is not None and b.open_time == bar_open and has_volume(b) for b in bars):
                legs.extend(zip(orders, bars, strict=True))
        if legs:
            self.run.fill(legs)

    def _end(self, event: InstrumentEnded) -> None:
        end = event.last_open + DAY_MS
        if end >= self.window.end:
            return
        name = spot_series(event.symbol, "1d")
        index = self._view(name).index_of(event.last_open)
        if index is None:
            raise SimulationError(f"{event.symbol}: notice of an end without its last bar")
        first_open, _ = self._segments[name].start_of(index)
        self._ended.add(Instrument(event.symbol, first_open))
        if event.symbol in OUTAGE_WITNESSES:
            self._stops.setdefault(event.symbol, set()).add(end)
            if all(end in self._stops.get(s, ()) for s in OUTAGE_WITNESSES):
                raise SimulationError(outage_message(end))
        self._schedule(end, HALT, event.symbol,
                       lambda s=event.symbol, b=self._rows[name][index]: self.run.halt(s, b, end))

    def _tick(self, t: int) -> None:
        history = History(t, self.run.strategy.interval, self._view_or_none,
                          self._segments.get, lambda i: i in self._ended, self.run.cache)
        if t in self.run.mark_times:
            self.run.mark(history)
        self._drain(t, DECIDE)  # closes and funding stamped at t, after the mark
        if t in self.run.decision_times:
            for order in self.run.decide(history):
                unit = unit_of(order)
                if order is self.run.pending[unit][0]:
                    self._schedule(order.deadline, LAPSE, unit,
                                   lambda u=unit, d=order.deadline: self.run.lapse(u, d))

    def _view(self, name: str) -> SeriesView:
        rows = self._rows.get(name, [])
        return SeriesView(name, rows, self._times.get(name, []), len(rows))

    def _view_or_none(self, name: str) -> SeriesView | None:
        return self._view(name) if name in self._rows else None

    def _funding_mark(self, symbol: str, calc_time: int) -> float | None:
        bar = self._latest.get(perp_series(symbol, "15m"))
        return funding_mark([bar], calc_time) if bar is not None else None


def replay(rows: Mapping[str, Iterable], window: Window, interval: str) -> list:
    """A paper feed from recorded rows: every bar at its close, every settlement when it
    becomes known, a notice for each instrument that stops before the window's end, and
    a tick at each mark and each decision time of ``interval``, in the order paper
    trading takes them."""
    events: list = []
    for name, series_rows in rows.items():
        series = Series(name, series_rows, window.end)
        if series.step is None:
            events.extend(SettlementEvent(name, r) for r in series.rows)
            continue
        events.extend(BarEvent(name, r) for r in series.rows)
        if series.segments is not None:
            symbol = name.split("/")[2]
            for i, open_time in enumerate(series.times):
                follows = i + 1 < len(series.times)
                if follows and series.times[i + 1] == open_time + DAY_MS:
                    continue
                if not follows and open_time + DAY_MS >= window.end:
                    continue
                events.append(InstrumentEnded(symbol, open_time))
    events.extend(Tick(t) for t in sorted(set(window.marks()) | set(window.decisions(interval))))
    events = [e for e in events if e.key[0] <= window.end]
    events.sort(key=lambda e: e.key)
    return events


def paper_run(strategy, rows: Mapping[str, Iterable], window: Window, costs: Costs) -> RunResult:
    """Paper trading over a replay of recorded rows."""
    paper = Paper(strategy, window, costs)
    for event in replay(rows, window, strategy.interval):
        paper.push(event)
    return paper.finish()
