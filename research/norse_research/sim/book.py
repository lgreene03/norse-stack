"""The book: cash, spot positions, perpetual positions and the carry pairs.

Section 5: "Cash earns nothing, nothing is borrowed." A spot buy pays its notional
and fee from cash and a spot sell returns its notional less its fee. A perpetual
exchanges no notional: it holds a signed quantity at an average entry price, pays its
fee from cash, realises its profit or loss into cash only as it is reduced, and counts
its unrealised profit or loss in equity. Section 7 treats "spot and perpetual ... as
one jointly margined book", so that unrealised profit or loss is simply part of
equity and liquidation is not modelled.

Every sum runs over sorted keys, so the backtest and paper trading, which fill the
book in the same order, add its terms in the same order too.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .model import BUY, PERP, SPOT, SimulationError


@dataclass
class PerpPosition:
    quantity: float  # signed: negative is short
    entry: float  # average entry price of the open quantity


class Book:
    def __init__(self, cash: float = 1.0) -> None:
        self.cash = float(cash)
        self.spot: dict[str, float] = {}
        self.perp: dict[str, PerpPosition] = {}
        self.carry: dict[str, str] = {}  # spot symbol -> its perpetual, while in carry

    def quantity(self, market: str, symbol: str) -> float:
        if market == SPOT:
            return self.spot.get(symbol, 0.0)
        position = self.perp.get(symbol)
        return position.quantity if position is not None else 0.0

    def single_leg_spot(self) -> list[str]:
        """Spot positions that are not a carry pair's long leg."""
        return sorted(s for s in self.spot if s not in self.carry)

    def equity(self, marks: Mapping[tuple[str, str], float]) -> float:
        """Section 5, Marks: cash plus every position at its mark, a perpetual at its
        unrealised profit or loss against its entry."""
        total = self.cash
        for symbol in sorted(self.spot):
            total += self.spot[symbol] * _mark(marks, SPOT, symbol)
        for symbol in sorted(self.perp):
            position = self.perp[symbol]
            total += position.quantity * (_mark(marks, PERP, symbol) - position.entry)
        return total

    def needs_marks(self) -> list[tuple[str, str]]:
        return [(SPOT, s) for s in sorted(self.spot)] + [(PERP, s) for s in sorted(self.perp)]

    # --- changes ---------------------------------------------------------------------

    def trade(self, market: str, symbol: str, side: int, quantity: float, price: float,
              fee: float) -> None:
        """Apply one fill of ``quantity`` (non-negative) at ``price`` with ``fee``."""
        if quantity < 0:
            raise SimulationError("a fill's quantity is non-negative; the side gives direction")
        if market == SPOT:
            self._spot(symbol, side, quantity, price, fee)
        else:
            self._perp(symbol, side, quantity, price, fee)

    def _spot(self, symbol: str, side: int, quantity: float, price: float, fee: float) -> None:
        held = self.spot.get(symbol, 0.0)
        if side == BUY:
            self.cash -= quantity * price + fee
            held += quantity
        else:
            # Section 6: "the book is never levered and never short".
            if quantity > held:
                raise SimulationError(f"selling {quantity} {symbol} with {held} held")
            self.cash += quantity * price - fee
            held = 0.0 if quantity == held else held - quantity
        if held > 0:
            self.spot[symbol] = held
        else:
            self.spot.pop(symbol, None)

    def _perp(self, symbol: str, side: int, quantity: float, price: float, fee: float) -> None:
        self.cash -= fee
        position = self.perp.get(symbol)
        signed = side * quantity
        if position is None or position.quantity == 0:
            if quantity > 0:
                self.perp[symbol] = PerpPosition(signed, price)
            return
        if (position.quantity > 0) == (signed > 0):
            total = abs(position.quantity) + quantity
            position.entry = (abs(position.quantity) * position.entry + quantity * price) / total
            position.quantity += signed
            return
        closed = min(quantity, abs(position.quantity))
        direction = 1.0 if position.quantity > 0 else -1.0
        self.cash += closed * (price - position.entry) * direction
        remaining = position.quantity + signed
        if quantity == abs(position.quantity) or remaining == 0:
            del self.perp[symbol]
        elif (remaining > 0) == (position.quantity > 0):
            position.quantity = remaining
        else:
            self.perp[symbol] = PerpPosition(remaining, price)

    def accrue(self, symbol: str, rate: float, mark: float) -> tuple[float, float]:
        """Section 7: "At each settlement while short, the book receives q times the
        funding mark times the rate, and pays it when the rate is negative." For a
        signed quantity that is minus quantity times mark times rate. Returns the
        quantity and the amount."""
        position = self.perp.get(symbol)
        if position is None or position.quantity == 0:
            return 0.0, 0.0
        amount = -position.quantity * mark * rate
        self.cash += amount
        return position.quantity, amount


def _mark(marks: Mapping[tuple[str, str], float], market: str, symbol: str) -> float:
    try:
        return marks[(market, symbol)]
    except KeyError:
        raise SimulationError(f"no mark for the {market} position in {symbol}") from None
