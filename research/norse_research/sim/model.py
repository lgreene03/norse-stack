"""The engine's vocabulary: time, instruments, costs, decisions, orders and records.

Every rule here carries the section of docs/PREREGISTRATION.md it implements. Times
are integer milliseconds since the epoch, UTC, as the loader returns them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from datetime import UTC, date, datetime, timedelta

from ..formats import DatasetSpec, EngineClass, read_only

MINUTE_MS = 60_000
FIFTEEN_MINUTES_MS = 900_000
FOUR_HOURS_MS = 14_400_000
DAY_MS = 86_400_000

# Section 5, Funding: "A settlement counts as known at decision time t if its calc_time
# is at most t plus 60 seconds."
FUNDING_KNOWN_SLACK_MS = 60_000

# Section 3: decisions are daily at 00:00 UTC; section 6, Four-hour bars: or at every
# four-hour close. The interval is also the lapse horizon of section 5, Fills.
DECISION_INTERVALS = read_only({"1d": DAY_MS, "4h": FOUR_HOURS_MS})

SPOT = "spot"
PERP = "perp"
BUY = 1
SELL = -1


class SimulationError(ValueError, metaclass=EngineClass):
    """The run asked the engine for something the registration does not define."""


def spot_series(symbol: str, interval: str) -> str:
    """A spot pair's kline series as the development manifest names it."""
    return DatasetSpec("spot", "klines", symbol, interval).name


def perp_series(symbol: str, interval: str) -> str:
    """A USDT-margined perpetual's kline series (section 4: "A spot pair's perpetual")."""
    return DatasetSpec("um", "klines", symbol, interval).name


def funding_series(symbol: str) -> str:
    return DatasetSpec("um", "fundingRate", symbol).name


def iso_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def day_ms(day: str) -> int:
    """00:00 UTC on ``day`` (YYYY-MM-DD)."""
    return int(datetime.combine(date.fromisoformat(day), datetime.min.time(), UTC).timestamp()
               * 1000)


# --- the window (section 3) -------------------------------------------------------------


@_dataclass(frozen=True)
class Window(metaclass=EngineClass):
    """The return days of the window being run, from the resolved configuration.

    Section 3: "the return of UTC day D is the equity marked at 00:00 on D+1 divided by
    the equity marked at 00:00 on D, minus one", so a window of n days has n + 1 marks,
    from 00:00 on its first day to 00:00 after its last. The window is taken from the
    configuration and never assumed: the holdout run (G1.6) passes one that runs
    through 2026-10-31 (section 11, item 5).
    """

    first_day: str
    last_day: str
    days: int

    def __post_init__(self) -> None:
        span = (date.fromisoformat(self.last_day) - date.fromisoformat(self.first_day)).days + 1
        if span != self.days or self.days < 1:
            raise SimulationError(
                f"window {self.first_day} to {self.last_day} is not {self.days} days"
            )

    @classmethod
    def from_configuration(cls, configuration: Mapping) -> Window:
        window = configuration["window"]
        return cls(window["first_return_day"], window["last_return_day"], int(window["days"]))

    @property
    def start(self) -> int:
        """The first mark, which is also the first decision."""
        return day_ms(self.first_day)

    @property
    def end(self) -> int:
        """The last mark: 00:00 after the last return day. Section 3: no decision is taken
        here, and section 5's lapse rule ends every fill search here."""
        return self.start + self.days * DAY_MS

    def marks(self) -> list[int]:
        return [self.start + i * DAY_MS for i in range(self.days + 1)]

    def decisions(self, interval: str) -> list[int]:
        """Section 3's table: daily decisions from the first day's 00:00 to the last
        day's 00:00; four-hour decisions to the last day's 20:00."""
        step = decision_step(interval)
        return list(range(self.start, self.end, step))

    def day_list(self) -> list[str]:
        first = date.fromisoformat(self.first_day)
        return [(first + timedelta(days=i)).isoformat() for i in range(self.days)]


def decision_step(interval: str) -> int:
    try:
        return DECISION_INTERVALS[interval]
    except KeyError:
        raise SimulationError(f"decisions are daily or four-hourly, not {interval!r}") from None


# --- costs (section 5, Costs; section 8, Cost sensitivity) ------------------------------


@_dataclass(frozen=True)
class Costs(metaclass=EngineClass):
    """Section 5: "the execution price is the reference price moved against the order
    by SlippageBps ... The fee is 10 bps of the traded notional at the execution price."

    The engine holds no default: the figures come from the resolved configuration's
    ``costs``, which the registration sets to 10 plus 2 bps a side for every trial,
    benchmark and control, and to the sensitivity total less 2 bps plus 2 bps for a
    sensitivity run (section 8). So no 5 plus 2 bps or 14 bps default can reach a run.
    """

    fee_bps: float
    slippage_bps: float

    def __post_init__(self) -> None:
        for name, value in (("fee_bps", self.fee_bps), ("slippage_bps", self.slippage_bps)):
            if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
                raise SimulationError(f"{name} must be a non-negative number, not {value!r}")

    @classmethod
    def from_configuration(cls, configuration: Mapping) -> Costs:
        costs = configuration["costs"]
        return cls(costs["fee_bps_per_side"], costs["slippage_bps_per_side"])

    def execution_price(self, reference: float, side: int) -> float:
        """Section 5: huginn's executor moves the price against the order, a buy up and
        a sell down, by the slippage in basis points (``SlippageImpactK = 0``)."""
        return reference * (1.0 + side * self.slippage_bps / 10_000.0)

    def fee(self, quantity: float, price: float) -> float:
        """Section 5: the fee on the traded notional at the execution price."""
        return abs(quantity) * price * self.fee_bps / 10_000.0


# --- instruments (section 4) ------------------------------------------------------------


@_dataclass(frozen=True, order=True)
class Instrument(metaclass=EngineClass):
    """Section 4: "An instrument is a maximal run of consecutive daily bars of one spot
    symbol in the pinned archive." It is named by its symbol and the open of its first
    daily bar, so a symbol that returns after a missing daily bar is a new instrument."""

    symbol: str
    first_open: int


# --- what a strategy returns ------------------------------------------------------------


@_dataclass(frozen=True)
class CarryTarget(metaclass=EngineClass):
    """Section 7: one coin's carry position, spot long and its perpetual short in equal
    quantity. ``fraction`` above zero holds the coin in carry, opening it at that
    fraction of equity if it is flat; zero takes it flat. Quantities never resize
    while held ("Quantities stay fixed until the exit")."""

    spot: Instrument
    perp: str
    fraction: float


@_dataclass(frozen=True)
class Decision(metaclass=EngineClass):
    """A strategy's answer at one decision time.

    ``weights`` is a complete book of single-leg spot targets, as fractions of equity
    marked at the decision (section 6, Sizing): an instrument held but not named has a
    target of zero (section 6: "Pairs outside the universe have a target of 0").
    ``band`` is section 6's rebalance band. ``carry`` names the coins whose carry state
    the decision sets (section 7); coins not named keep their state. A decision sets
    one kind or the other; None leaves that kind of position as it is.
    """

    weights: Mapping[Instrument, float] | None = None
    band: float = 0.0
    carry: tuple[CarryTarget, ...] | None = None

    def __post_init__(self) -> None:
        if self.weights is not None and self.carry is not None:
            raise SimulationError("a decision sets single-leg weights or carry, not both")
        if not 0.0 <= self.band:
            raise SimulationError("the band is a non-negative fraction of the target")
        if self.weights is not None:
            bad = [k for k, w in self.weights.items() if not isinstance(k, Instrument) or w < 0]
            if bad:
                raise SimulationError(f"weights are non-negative and keyed by Instrument: {bad}")
            # Section 6: "the book is never levered and never short".
            if sum(self.weights.values()) > 1.0 + 1e-12:
                raise SimulationError("target weights sum to more than equity")
        if self.carry is not None:
            coins = [c.spot.symbol for c in self.carry]
            if len(set(coins)) != len(coins) or any(c.fraction < 0 for c in self.carry):
                raise SimulationError("one non-negative carry target per coin")


# --- what the engine emits --------------------------------------------------------------


@_dataclass(frozen=True)
class Order(metaclass=EngineClass):
    """One leg of an order, as decided; its quantity is fixed only at the fill.

    Section 6, Orders: "An order's quantity is the change in target weight times
    equity marked at the decision, divided by its fill bar's reference price", so a
    resizing order carries a ``notional`` (that weight change times equity). An order
    that closes a position carries its exact ``quantity``. The perpetual leg of a
    carry entry carries neither: it sells the quantity its spot leg buys (section 7).
    Legs with the same ``group`` fill together on one bar (section 5).
    """

    decided_at: int
    market: str
    symbol: str
    side: int
    notional: float | None
    quantity: float | None
    group: str | None
    deadline: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.market, self.symbol)


@_dataclass(frozen=True)
class Fill(metaclass=EngineClass):
    """A change of position. ``kind`` is "fill" for an order filled on a 15-minute bar
    (section 5) and "halt" for section 4's close of an instrument that stopped
    trading, which has no order and no bar."""

    kind: str
    market: str
    symbol: str
    side: int
    at: int  # the instant the position changes
    bar_open: int | None
    reference: float
    price: float
    quantity: float
    fee: float
    scale: float  # the cash scaling of section 6, Orders; 1.0 when it did not bind
    order: Order | None = _field(default=None, compare=True)


@_dataclass(frozen=True)
class Lapse(metaclass=EngineClass):
    """Section 5: an order no bar with volume filled before its deadline."""

    order: Order
    at: int


@_dataclass(frozen=True)
class Accrual(metaclass=EngineClass):
    """Section 5, Funding: one settlement on the perpetual quantity held at its time."""

    symbol: str
    calc_time: int
    rate: float
    quantity: float
    mark: float
    amount: float  # the cash the book received (negative when it paid)


@_dataclass(frozen=True)
class Skip(metaclass=EngineClass):
    """Section 5, Gaps item 2: a four-hour decision skipped for a missing bar."""

    at: int
    symbols: tuple[str, ...]
