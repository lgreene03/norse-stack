"""From a decision to orders, and from a 15-minute bar to fills (sections 4 to 7).

These functions are the rules both drivers share; what differs between a backtest and
paper trading is only how each learns which bar an order fills on, and when.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..formats import Funding, Kline
from .book import Book
from .history import History
from .market import SeriesView, vwap
from .model import (
    BUY,
    DAY_MS,
    FIFTEEN_MINUTES_MS,
    PERP,
    SELL,
    SPOT,
    Accrual,
    Costs,
    Decision,
    Fill,
    Order,
    SimulationError,
    Skip,
    perp_series,
    spot_series,
)
from .targeting import rebalances

CARRY_GROUP = "carry:"


def interval_of(step: int) -> str:
    return "1d" if step == DAY_MS else "4h"


def last_close(view: SeriesView, close_instant: int, step: int) -> float | None:
    """The close of the latest known bar closing at or before ``close_instant``."""
    index = view.last_at_or_before(close_instant - step)
    return None if index is None else view[index].close


def marks_at(history: History, keys: Iterable[tuple[str, str]], step: int) -> dict:
    """Prices at ``history.now`` on bars of ``step``.

    Section 5, Marks: "Equity is marked at 00:00 UTC on each instrument's daily close,
    using the perpetual's own daily close for a short leg. If an exchange-wide outage
    ever removes a daily bar, the last close is carried forward". A spot price is the
    close of its bar that closed exactly now: a missing daily bar ends a spot
    instrument (section 4), whose position is then already closed, and a missing
    four-hour bar skips the decision (section 5, Gaps item 2), so neither carries
    forward. A perpetual's last close carries forward (section 5, Gaps item 6). At a
    four-hour decision the bars are four-hour ones (section 6, Four-hour bars).
    """
    interval = interval_of(step)
    marks: dict[tuple[str, str], float] = {}
    for market, symbol in keys:
        if market == SPOT:
            close = history.close_at(spot_series(symbol, interval), history.now, step)
        else:
            close = last_close(history.series(perp_series(symbol, interval)), history.now, step)
        if close is not None:
            marks[(market, symbol)] = close
    return marks


# --- decisions to orders ----------------------------------------------------------------


def orders_for(
    decision: Decision,
    book: Book,
    history: History,
    marks: dict,
    equity: float,
    step: int,
    deadline: int,
) -> tuple[list[Order], Skip | None]:
    if equity <= 0:
        raise SimulationError(f"equity {equity} at {history.now}: weights are undefined")
    if decision.weights is not None:
        return _weight_orders(decision, book, history, marks, equity, step, deadline)
    if decision.carry is not None:
        return _carry_orders(decision, book, history, equity, deadline), None
    return [], None


def _weight_orders(decision, book, history, marks, equity, step, deadline):
    t = history.now
    targets: dict[str, float] = {}
    for instrument, weight in sorted(decision.weights.items()):
        symbol = instrument.symbol
        if symbol in targets:
            raise SimulationError(f"two instruments of {symbol} in one decision")
        if symbol in book.carry:
            raise SimulationError(f"{symbol} is held as a carry leg, not a single position")
        # Section 4 (D12): once an instrument has stopped trading "the slot holds cash
        # until the next month-end universe update"; a later run of the same symbol is
        # another instrument with no history, so a target names one instrument only.
        targets[symbol] = weight if history.live(instrument) else 0.0
    involved = sorted(
        s for s in set(targets) | set(book.single_leg_spot())
        if targets.get(s, 0.0) > 0 or s in book.spot
    )
    marks.update(marks_at(history, [(SPOT, s) for s in involved if (SPOT, s) not in marks], step))
    missing = tuple(s for s in involved if (SPOT, s) not in marks)
    if missing:
        if step == DAY_MS:
            raise SimulationError(f"no daily close at {t} for {missing}")
        # Section 5, Gaps item 2: "A missing four-hour bar ... means the four-hour
        # decision at its close is skipped."
        return [], Skip(t, missing)
    orders = []
    for symbol in involved:
        target = targets.get(symbol, 0.0)
        held = book.spot.get(symbol, 0.0)
        current = held * marks[(SPOT, symbol)] / equity
        # Section 6, Rebalancing: "An instrument is traded to its target only when the
        # absolute difference between its target and its current (drifted) weight
        # exceeds 25% of the target, which means entries and exits always trade".
        if not rebalances(target, current, decision.band):
            continue
        if target == 0:
            # An exit sells the whole position. Section 6's weight change divided by the
            # fill's price would leave held times (1 - mark / fill price) unsold.
            orders.append(Order(t, SPOT, symbol, SELL, None, held, None, deadline))
        else:
            side = BUY if target > current else SELL
            notional = abs(target - current) * equity  # section 6, Orders
            orders.append(Order(t, SPOT, symbol, side, notional, None, None, deadline))
    return orders, None


def _carry_orders(decision, book, history, equity, deadline):
    t = history.now
    orders = []
    for target in sorted(decision.carry, key=lambda c: c.spot.symbol):
        symbol = target.spot.symbol
        group = CARRY_GROUP + symbol
        if target.fraction > 0 and symbol not in book.carry:
            if symbol in book.spot:
                raise SimulationError(f"{symbol} spot is held outside its carry pair")
            if not history.live(target.spot):
                continue
            # Section 7, Sizing and holding: "the spot quantity is q = 0.5 x equity
            # divided by the spot fill's reference price; q spot is bought and q
            # perpetual sold on the same 15-minute bar".
            orders.append(Order(t, SPOT, symbol, BUY, target.fraction * equity, None, group,
                                deadline))
            orders.append(Order(t, PERP, target.perp, SELL, None, None, group, deadline))
        elif target.fraction == 0 and symbol in book.carry:
            perp = book.carry[symbol]
            if perp != target.perp:
                raise SimulationError(f"{symbol} is carried against {perp}, not {target.perp}")
            # "the exit, which sells q spot and buys q perpetual".
            orders.append(Order(t, SPOT, symbol, SELL, None, book.spot[symbol], group, deadline))
            orders.append(Order(t, PERP, perp, BUY, None, -book.perp[perp].quantity, group,
                                deadline))
    return orders


# --- a bar's fills (section 5, Fills; section 6, Orders) --------------------------------


def unit_of(order: Order) -> str:
    """Legs that fill together share a unit: a carry pair's group, or the order alone."""
    return order.group or f"{order.market}:{order.symbol}"


def fill_series(order: Order) -> str:
    if order.market == SPOT:
        return spot_series(order.symbol, "15m")
    return perp_series(order.symbol, "15m")


def execute_bar(
    book: Book, costs: Costs, legs: Sequence[tuple[Order, Kline]]
) -> list[Fill]:
    """Fill every leg whose fill bar is this 15-minute bar.

    Section 6, Orders: "Within a bar sells fill before buys, and if buys and fees would
    take cash below zero, every buy is scaled down in proportion so that cash ends at
    zero. In H2 the same rule scales a coin's two legs together, so their quantities
    stay equal." Here a unit that only reduces positions (a spot sell, a perpetual
    bought back) is a sell and fills first, unscaled; a unit that opens or adds to any
    position is a buy, and every buy unit is scaled by one factor, legs together.
    """
    if not legs:
        return []
    bar_open = legs[0][1].open_time
    if any(bar.open_time != bar_open for _, bar in legs):
        raise SimulationError("one bar's fills come from bars opening together")
    units: dict[str, list[tuple[Order, Kline]]] = {}
    for order, bar in legs:
        units.setdefault(unit_of(order), []).append((order, bar))
    planned: dict[str, list[list]] = {}
    for unit, unit_legs in units.items():
        unit_legs.sort(key=lambda leg: leg[0].market != SPOT)  # spot leg first
        matched = None
        rows = []
        for order, bar in unit_legs:
            reference = vwap(bar)
            if order.quantity is not None:
                quantity = order.quantity
            elif order.notional is not None:
                quantity = order.notional / reference
            elif matched is not None:
                quantity = matched
            else:
                raise SimulationError(f"{unit}: a matched leg needs its spot leg")
            if order.market == SPOT and order.side == SELL:
                quantity = min(quantity, book.spot.get(order.symbol, 0.0))
            matched = quantity if order.market == SPOT else matched
            rows.append([order, bar, reference, costs.execution_price(reference, order.side),
                         quantity])
        planned[unit] = rows

    def reduces(order: Order, quantity: float) -> bool:
        held = book.quantity(order.market, order.symbol)
        return held * order.side < 0 and quantity <= abs(held)

    sells = sorted(u for u, rows in planned.items() if all(reduces(r[0], r[4]) for r in rows))
    buys = sorted(u for u in planned if u not in sells)
    fills: list[Fill] = []
    for unit in sells:
        fills += _apply(book, costs, unit, planned[unit], 1.0)
    cost = 0.0
    for unit in buys:
        for order, _, _, price, quantity in planned[unit]:
            fee = costs.fee(quantity, price)
            if order.market == SPOT:
                cost += order.side * quantity * price + fee
            else:
                cost += fee
    scale = 1.0
    if cost > book.cash and cost > 0:
        scale = max(book.cash, 0.0) / cost
    for unit in buys:
        fills += _apply(book, costs, unit, planned[unit], scale)
    return fills


def _apply(book: Book, costs: Costs, unit: str, rows: list, scale: float) -> list[Fill]:
    fills = []
    for order, bar, reference, price, quantity in rows:
        quantity *= scale
        fee = costs.fee(quantity, price)
        book.trade(order.market, order.symbol, order.side, quantity, price, fee)
        fills.append(Fill("fill", order.market, order.symbol, order.side,
                          bar.open_time + FIFTEEN_MINUTES_MS, bar.open_time, reference, price,
                          quantity, fee, scale, order))
    if unit.startswith(CARRY_GROUP):
        spot = next(f for f in fills if f.market == SPOT)
        perp = next(f for f in fills if f.market == PERP)
        if spot.side == BUY and spot.quantity > 0:
            book.carry[spot.symbol] = perp.symbol
        elif spot.side == SELL:
            book.carry.pop(spot.symbol, None)
    return fills


# --- section 4: an instrument that stops trading ----------------------------------------


def halt(book: Book, costs: Costs, symbol: str, last_bar: Kline, at: int) -> Fill | None:
    """Section 4 (D12): "a long position in it is closed at the low of the instrument's
    last daily bar, then charged slippage and the fee as any sell, and booked at that
    bar's close". The engine holds no short spot position (section 6: "never short"),
    so the short half of the rule has nothing to close here. A carry pair's spot
    instrument stopping is not defined by the registration, since H2 "never holds one
    leg without the other" (section 5); it never happens to BTC or ETH, and it stops
    the run rather than invent a rule."""
    if symbol in book.carry:
        raise SimulationError(f"{symbol} stopped trading while held in carry; not registered")
    held = book.spot.get(symbol, 0.0)
    if held <= 0:
        return None
    reference = last_bar.low
    price = costs.execution_price(reference, SELL)
    fee = costs.fee(held, price)
    book.trade(SPOT, symbol, SELL, held, price, fee)
    return Fill("halt", SPOT, symbol, SELL, at, None, reference, price, held, fee, 1.0, None)


# --- section 5: funding -----------------------------------------------------------------


def funding_mark(bars: Sequence[Kline], calc_time: int) -> float | None:
    """Section 5, Funding: "valued at the close of the latest perpetual 15-minute bar at
    or before it". A bar counts once it has closed, at its open plus 15 minutes."""
    found = None
    for bar in bars:
        if bar.open_time + FIFTEEN_MINUTES_MS <= calc_time:
            found = bar
        else:
            break
    return None if found is None else found.close


def accrue(book: Book, symbol: str, settlement: Funding, mark: float | None) -> Accrual | None:
    """Section 5, Funding: "accrued per published settlement at its published rate, at
    the settlement's published calc_time, on the quantity held at that instant"."""
    if book.quantity(PERP, symbol) == 0:
        return None
    if mark is None:
        raise SimulationError(f"{symbol}: no 15-minute close before {settlement.calc_time}")
    quantity, amount = book.accrue(symbol, settlement.rate, mark)
    return Accrual(symbol, settlement.calc_time, settlement.rate, quantity, mark, amount)


def first_fill_bar(
    leg_bars: Mapping[str, Sequence[Kline]], start: int, deadline: int
) -> tuple[int, dict[str, Kline]] | None:
    """Section 5, Fills: the first 15-minute bar opening in [start, deadline) on which
    every leg's bar exists and has volume; for one leg, "the next 15-minute bar with
    volume"; for an H2 pair, "the first 15-minute bar at or after the decision at which
    both the spot and the perpetual bar have volume". None means the order lapses."""
    by_open: dict[str, dict[int, Kline]] = {
        name: {b.open_time: b for b in bars if start <= b.open_time < deadline and b.volume > 0}
        for name, bars in leg_bars.items()
    }
    names = sorted(by_open)
    common = set(by_open[names[0]])
    for name in names[1:]:
        common &= set(by_open[name])
    if not common:
        return None
    first = min(common)
    return first, {name: by_open[name][first] for name in names}
