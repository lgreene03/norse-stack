"""H1, the dated-universe trend (goal G2.2), with its benchmark B-H1 and control C-H1.

Genesis pins ``run`` in this module for every id in ``CONFIGURATIONS``
(docs/research/trial-ledger.md). The harness resolves each run's configuration from
docs/preregistration.json, which governs, and hands it here frozen with the runner's
``DataView``; this module reads its parameters from that configuration and never from
a copy of the grid. Every rule names the section and item of docs/PREREGISTRATION.md
it implements. The mechanics H1 shares with H2 (fills, costs, marks, halts, the band,
the volatility, the universe ranking) are the engine's, reached only through
``norse_research.sim``. docs/research/strategies.md sets out each configuration and
every reading of the registration made here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from norse_research.sim import (
    DAY_MS,
    FOUR_HOURS_MS,
    Decision,
    Instrument,
    Market,
    SimulationError,
    Window,
    day_ms,
    default_rng,
    funding_series,
    iso_day,
    month_of_decision,
    perpetual_of,
    quote_volumes,
    rank_top,
    run_backtest,
    select_universe,
    signal,
    spot_series,
    target_weight,
    trailing_funding,
    vote,
)

# Read as literals by the harness and the genesis step (sources.strategy_declarations).
CONFIGURATIONS = [
    "H1-01", "H1-02", "H1-03", "H1-04", "H1-05", "H1-06", "H1-07", "H1-08", "B-H1", "C-H1",
]
PARAMETERS = [
    "bar", "funding_filter", "lookbacks_days", "max_fraction_of_slot", "rebalance_band",
    "slots", "vol_ewm_span_days", "vol_scaling", "vol_target_annual",
]
# No defaults: every value a grid configuration or the control uses is registered, and
# the benchmark's rule (section 8) has no parameter.
DEFAULTS = {}

# Section 6, The H1 grid; section 8, Benchmarks and Control runs.
GRID = tuple(c for c in CONFIGURATIONS if c.startswith("H1-"))
BENCHMARK = "B-H1"
CONTROL = "C-H1"
CONTROL_BASE = "H1-01"  # preregistration.json controls[C-H1].base
# Section 9: a grid configuration runs as a trial, a sensitivity run or the holdout run.
GRID_KINDS = ("trial", "sensitivity", "holdout")
# preregistration.json periods.development_end_exclusive. Section 11, item 6: no run
# but the holdout run reads past it; section 8: C-H1 draws over development bars only.
DEVELOPMENT_END = "2026-01-01"

# Section 6, The universe, step 1: "over the 30 daily bars closing at the month end".
RANK_DAYS = 30
# Section 6, step 2 and decision D13: "a daily close 120 days before the month-end close".
ELIGIBILITY_DAYS = 120
# Section 6, step 3: "The universe for the month is the first five remaining"; section 8,
# B-H1: "one fifth of equity in each H1 universe member".
UNIVERSE_SIZE = 5
# Section 6, Four-hour bars: lookbacks and the span "in the same calendar days" at six
# bars a day, volatility "annualised by the square root of 2,190"; daily by 365.
BARS_PER_DAY = {"1d": 1, "4h": 6}
BARS_PER_YEAR = {"1d": 365, "4h": 2190}

# Section 8, C-H1: "every development daily bar of the 115 pinned candidates", the union
# of the top ten at every development and holdout month end that G0.6 published as
# research/coverage/universe-candidates.json (a test holds this list to that file).
CANDIDATES = (
    "1000SATSUSDT", "ADAUSDT", "APEUSDT", "APTUSDT", "ARBUSDT", "ASTERUSDT", "ATOMUSDT",
    "AVAXUSDT", "AVNTUSDT", "AXSUSDT", "BANKUSDT", "BCCUSDT", "BCHABCUSDT", "BCHSVUSDT",
    "BCHUSDT", "BNBUSDT", "BOMEUSDT", "BTCUSDT", "BTTUSDT", "CFXUSDT", "CHIPUSDT",
    "CHZUSDT", "CYBERUSDT", "DASHUSDT", "DEXEUSDT", "DOGEUSDT", "DOGSUSDT", "DOTUSDT",
    "EDUUSDT", "ENAUSDT", "ENJUSDT", "EOSUSDT", "ERAUSDT", "ERDUSDT", "ETCUSDT",
    "ETHBEARUSDT", "ETHBULLUSDT", "ETHUSDT", "FETUSDT", "FILUSDT", "FLOKIUSDT", "FOGOUSDT",
    "FTMUSDT", "FTTUSDT", "GALAUSDT", "GIGGLEUSDT", "GMTUSDT", "GRTUSDT", "HIFIUSDT",
    "HOOKUSDT", "IDUSDT", "IOUSDT", "LINKUSDT", "LOOMUSDT", "LRCUSDT", "LTCUSDT",
    "LUNAUSDT", "LUNCUSDT", "MANAUSDT", "MANTAUSDT", "MATICUSDT", "MEMEUSDT", "NEARUSDT",
    "NEIROUSDT", "NEOUSDT", "NIGHTUSDT", "NOTUSDT", "OGUSDT", "ONEUSDT", "ONTUSDT",
    "OPUSDT", "ORDIUSDT", "PAXGUSDT", "PENGUUSDT", "PEOPLEUSDT", "PEPEUSDT", "PNUTUSDT",
    "QTUMUSDT", "REUSDT", "SANDUSDT", "SEIUSDT", "SENTUSDT", "SHIBUSDT", "SLPUSDT",
    "SNDKBUSDT", "SOLUSDT", "SOMIUSDT", "SUIUSDT", "SUSHIUSDT", "SXPUSDT", "TAOUSDT",
    "THETAUSDT", "TLMUSDT", "TRBUSDT", "TRUMPUSDT", "TRXUSDT", "TUTUSDT", "UNIUSDT",
    "VETUSDT", "WALUSDT", "WIFUSDT", "WINUSDT", "WLDUSDT", "WLFIUSDT", "XAUTUSDT",
    "XLMUSDT", "XPLUSDT", "XRPUSDT", "XTZUSDT", "YFIIUSDT", "YFIUSDT", "YGGUSDT",
    "ZAMAUSDT", "ZECUSDT", "ZILUSDT",
)


# --- the pinned function ----------------------------------------------------------------


def run(configuration, data_view):
    """The daily net returns of one registered H1 configuration, B-H1 or C-H1, over the
    resolved configuration's window, in the shape the runner hands to the harness."""
    market = load_market(configuration, data_view, pinned_series(data_view))
    return simulate(configuration, market)


def pinned_series(data_view):
    """THE DATA SEAM. The name of every series the run's manifests pin (both manifests
    for the holdout run, section 11, item 5), which section 6, step 1 needs: it ranks
    "USDT spot symbols ... delisted symbols included", every pinned spot daily series.

    It must come from the data-loading layer, never from a list committed here, as the
    spans do (quant review of G1.9, finding 3). The runner's ``DataView`` gives each
    named series' coverage and rows but no listing of the names, so until it does no H1
    run can load data, and none can be ledgered by accident. ``load_market`` below takes
    the names as an argument and is tested on synthetic data."""
    raise SimulationError(
        "the data seam is open: the DataView lists no series, and H1 ranks every pinned "
        "spot daily series (docs/research/strategies.md, The data seam)"
    )


def series_needed(configuration: Mapping, names) -> list[str]:
    """The series a run of ``configuration`` reads, from the names the data layer offers.

    Every spot daily series: section 6, step 1 ranks "USDT spot symbols ... delisted
    symbols included", and the daily bars also define instruments (section 4) and marks
    (section 5). Every spot 15-minute series, read lazily for fills (section 5, Fills).
    Spot four-hour series for H1-06 (section 6, Four-hour bars), and the perpetuals'
    funding for H1-07 (section 6, Funding filter)."""
    parameters = configuration["parameters"]
    four_hour = parameters.get("bar") == "4h"
    filtered = parameters.get("funding_filter") is not None
    out = []
    for name in sorted(names):
        parts = name.split("/")
        if parts[:2] == ["spot", "klines"] and parts[3] in ("1d", "15m"):
            out.append(name)
        elif parts[:2] == ["spot", "klines"] and parts[3] == "4h" and four_hour:
            out.append(name)
        elif parts[:2] == ["um", "fundingRate"] and filtered:
            out.append(name)
    return out


def load_market(configuration: Mapping, data_view, names) -> Market:
    """The run's market, read through the runner's ``DataView`` to the window's end
    (section 3), each series from its first archived row (section 6: the volatility is
    "over every return of the instrument", and D13 needs the instrument's start). The
    engine takes each series' archived runs from ``data_view.coverage``, the committed
    manifests' metadata, so no span is committed here."""
    window = Window.from_configuration(configuration)
    return Market.from_view(data_view, series_needed(configuration, names), window.end)


def simulate(configuration: Mapping, market: Market) -> list[tuple[str, float]]:
    """The run on a market already read (the batch driver, ``backtest``)."""
    rows = {name: series.rows for name, series in market.series.items()}
    return run_backtest(strategy_for(configuration, rows), configuration, market)


def strategy_for(configuration: Mapping, rows: Mapping[str, Sequence]):
    """The strategy object for a resolved configuration. ``rows`` holds the run's daily
    (and four-hour and funding) series by name; only their names are used, except by
    C-H1, which draws one random number per development daily bar of the candidates."""
    cid, kind = configuration["id"], configuration["kind"]
    window = Window.from_configuration(configuration)
    if kind != "holdout" and window.end > day_ms(DEVELOPMENT_END):
        # Section 11, item 6: "No other configuration, and no benchmark, control or
        # sensitivity run, ever reads holdout data"; section 3 ends development trading
        # at the last development close.
        raise SimulationError(f"a {kind} run ends at {DEVELOPMENT_END} at the latest")
    symbols = [n.split("/")[2] for n in rows if _is_spot_daily(n)]
    if cid in GRID:
        if kind not in GRID_KINDS:
            raise SimulationError(f"{cid} runs as a trial, sensitivity or holdout run")
        return Trend(configuration["parameters"], symbols)
    if cid == BENCHMARK:
        if kind != "benchmark":
            raise SimulationError(f"{cid} runs as a benchmark")
        return MonthEndEqualWeight(symbols)
    if cid == CONTROL:
        if kind != "control" or configuration["base"] != CONTROL_BASE:
            raise SimulationError(f"{cid} runs as a control on {CONTROL_BASE}")
        end = day_ms(DEVELOPMENT_END)
        daily = {
            s: [bar for bar in rows[spot_series(s, "1d")] if bar.open_time < end]
            for s in CANDIDATES if spot_series(s, "1d") in rows
        }
        flipped = SignFlipped(sign_flips(daily, configuration["seed"]))
        return Trend(configuration["parameters"], symbols, flipped)
    raise SimulationError(f"{cid} is not an H1 configuration, benchmark or control")


# --- section 6: the dated universe ------------------------------------------------------


def is_month_end(t: int) -> bool:
    """Section 6, The universe: "At each month-end close (the decision at 00:00 UTC on
    the first of the next month)"."""
    return t % DAY_MS == 0 and iso_day(t).endswith("-01")


def month_end_universe(history, symbols: Sequence[str], size: int) -> list[Instrument]:
    """Section 6, The universe, steps 1 to 3, at the month-end decision ``history.now``.

    Step 1 ranks every spot pair the run holds by quote volume over the 30 daily bars
    closing at the month end, "exactly as rank_month_ends computes it" (the engine's
    ``rank_top``, which first removes the 27 stablecoin and fiat bases and takes the ten
    largest). Only the bars in that 30-day window are passed, which ``rank_month_ends``
    would read anyway. Steps 2 and 3 (exclusions by category, D14; the month-end bar and
    a close 120 days back, D13; the first five, cash for a missing one) are the engine's
    ``select_universe``."""
    t = history.now
    month = month_of_decision(t)
    first_open = t - RANK_DAYS * DAY_MS
    volumes = {}
    for symbol in symbols:
        name = spot_series(symbol, "1d")
        if not history.has(name):
            continue
        view = history.series(name)
        volumes[symbol] = quote_volumes(view[view.index_after(first_open - 1):])
    ranked = rank_top(volumes, month)
    return select_universe(history, ranked, slots=size, history_days=ELIGIBILITY_DAYS)


# --- H1: the trend strategy (section 6) -------------------------------------------------


class Trend:
    """Section 6: long or flat, spot only, on the month's dated universe.

    At each decision every live member gets w* = (1/slots) x s x min(cap, target / sigma),
    or (1/slots) x s without volatility scaling, as a fraction of equity marked then, and
    is traded to it only outside the band. ``flipped`` (C-H1 only) replaces the closes
    the votes read with section 8's sign-flipped series; nothing else changes."""

    def __init__(self, parameters: Mapping, symbols: Sequence[str],
                 flipped: SignFlipped | None = None) -> None:
        bar = parameters["bar"]
        if bar not in BARS_PER_DAY:
            raise SimulationError(f"H1 decides on daily or four-hour bars, not {bar!r}")
        # Section 3 and section 6, Four-hour bars: "the same rule at every four-hour close".
        self.interval = bar
        self.step = DAY_MS if bar == "1d" else FOUR_HOURS_MS
        lookbacks = tuple(parameters["lookbacks_days"])
        if not lookbacks or any(not isinstance(d, int) or d < 1 for d in lookbacks):
            raise SimulationError(f"lookbacks are whole days, not {lookbacks!r}")
        # Section 6, Signal: one vote per lookback L, in days at either bar.
        self.lookbacks = lookbacks
        # Section 6, Sizing: "Each of the five members gets a slot of one fifth of equity".
        self.slots = int(parameters["slots"])
        self.cap = float(parameters["max_fraction_of_slot"])
        # Section 6, Rebalancing: the band, a fraction of the target.
        self.band = float(parameters["rebalance_band"])
        if parameters["vol_scaling"]:
            # Section 6, Sizing: 20% a year over an EWM span of 60 days; at four-hour bars
            # a span of 360 bars, annualised by the square root of 2,190.
            self.vol_target = float(parameters["vol_target_annual"])
            self.span = parameters["vol_ewm_span_days"] * BARS_PER_DAY[bar]
            self.per_year = BARS_PER_YEAR[bar]
        else:
            # H1-08: "w* = (1/5) x s".
            if (parameters["vol_target_annual"], parameters["vol_ewm_span_days"]) != (None, None):
                raise SimulationError("without volatility scaling there is no target or span")
            self.vol_target = self.span = self.per_year = None
        funding = parameters["funding_filter"]
        # H1-07, section 6, Funding filter: flat while the trailing sum exceeds a ceiling.
        self.filter = None if funding is None else (
            float(funding["lookback_days"]), float(funding["flat_if_sum_above"]))
        if flipped is not None and bar != "1d":
            raise SimulationError("C-H1 is H1-01, on daily bars")
        self.flipped = flipped
        self.symbols = tuple(sorted(symbols))
        self.members: list[Instrument] = []

    def decide(self, history, book):
        t = history.now
        members = self.members
        if is_month_end(t):
            # Section 6, Rebalancing: "the universe changes only monthly"; Four-hour bars:
            # "the universe updated only at the month-end 00:00 decision".
            members = month_end_universe(history, self.symbols, self.slots)
        targets = {}
        for instrument in members:
            if not history.live(instrument):
                # Section 4 (D12): after a halt "the slot holds cash until the next
                # month-end universe update".
                continue
            s = self.signal_of(history, instrument)
            if s is None:
                # Section 5, Gaps item 2: "A missing four-hour bar ... means the four-hour
                # decision at its close is skipped." Nothing changes, the universe included.
                return None
            if s > 0 and self.filter is not None and self.funding_too_high(history, instrument):
                s = 0.0
            targets[instrument] = self.weight(history, instrument, s)
        self.members = members
        # Section 6, Sizing: "Pairs outside the universe have a target of 0" (the engine
        # sets any held instrument not named here to zero).
        return Decision(weights=targets, band=self.band)

    def signal_of(self, history, instrument: Instrument) -> float | None:
        """Section 6, Signal: the mean of one vote per lookback, each 1 if the close at t
        over the close L days earlier, minus one, is above zero. None when a four-hour
        decision has no bar closing at t."""
        name = spot_series(instrument.symbol, self.interval)
        t = history.now
        now = self.close(history, name, instrument, t, exact=True)
        if now is None:
            if self.step == DAY_MS:
                raise SimulationError(f"{instrument} is live with no daily close at {t}")
            return None
        votes = []
        for days in self.lookbacks:
            then = self.close(history, name, instrument, t - days * DAY_MS, exact=False)
            if then is None:
                # D13 makes the 120-day close "always defined" for a member.
                raise SimulationError(f"{instrument} has no close {days} days before {t}")
            votes.append(vote(now, then))
        return signal(votes)

    def close(self, history, name: str, instrument: Instrument, at: int,
              exact: bool) -> float | None:
        """The close of the instrument's bar closing at ``at``: exactly then for the close
        at t, and otherwise the latest at or before it, which differs only where a
        four-hour bar is missing (section 5, Marks carries the last close forward). It
        must be the instrument's own bar (section 4: "no history" across a gap)."""
        view = history.series(name)
        opening = at - self.step
        index = view.index_of(opening) if exact else view.last_at_or_before(opening)
        if index is None or view[index].open_time < instrument.first_open:
            return None
        if self.flipped is not None:
            return self.flipped.close(instrument, view, index)
        return view[index].close

    def funding_too_high(self, history, instrument: Instrument) -> bool:
        """Section 6, Funding filter (H1-07): the signal is 0 while the sum of the
        perpetual's funding rates with calc_time in (t - 7 days + 60 s, t + 60 s] exceeds
        0.63%. "Where a member has no perpetual, or its perpetual's settlements do not
        cover the seven days without a recorded gap, the filter does not apply"."""
        lookback_days, ceiling = self.filter
        symbol, t = instrument.symbol, history.now
        # Section 4: the same coin's USDT-margined perpetual, including those quoted per
        # thousand coins; the run holds funding for every perpetual the manifest pins.
        offered = [p for p in (symbol, "1000" + symbol) if history.has(funding_series(p))]
        perp = perpetual_of(symbol, offered)
        if perp is None:
            return False
        if t - round(lookback_days * DAY_MS) < instrument.first_open:
            # Section 4: "A funding settlement applies to the spot instrument whose dates
            # contain it", so settlements before the instrument began do not cover it.
            return False
        window = trailing_funding(history.series(funding_series(perp)), t, lookback_days)
        return window.covered and window.total > ceiling

    def weight(self, history, instrument: Instrument, s: float) -> float:
        """Section 6, Sizing: w* = (1/5) x s x min(1, 0.20 / sigma), or (1/5) x s."""
        if s == 0:
            return 0.0
        if self.vol_target is None:
            return target_weight(s, self.slots, None, None, self.cap)
        name = spot_series(instrument.symbol, self.interval)
        sigma = history.ewm_volatility(name, instrument, self.span, self.per_year)
        if sigma is None:
            raise SimulationError(f"{instrument} has no return to size it by")
        return target_weight(s, self.slots, sigma, self.vol_target, self.cap)


# --- section 8: the benchmark B-H1 ------------------------------------------------------


class MonthEndEqualWeight:
    """Section 8, B-H1: "at each month-end decision, trade to one fifth of equity in each
    H1 universe member (cash for an empty slot), with no trading between month ends, the
    same fills and costs and the same rule for halted pairs". A band of zero trades any
    difference, so each member lands on one fifth and anything else is sold."""

    interval = "1d"

    def __init__(self, symbols: Sequence[str]) -> None:
        self.symbols = tuple(sorted(symbols))

    def decide(self, history, book):
        if not is_month_end(history.now):
            return None
        members = month_end_universe(history, self.symbols, UNIVERSE_SIZE)
        return Decision(weights={m: 1.0 / UNIVERSE_SIZE for m in members}, band=0.0)


# --- section 8: the control C-H1 --------------------------------------------------------


def sign_flips(daily: Mapping[str, Sequence], seed: int) -> dict[str, dict[int, bool]]:
    """Section 8, C-H1: ``rng = numpy.random.default_rng(seed)`` and one call
    ``rng.random(n) < 0.5``, "one value per instrument-day for every development daily
    bar of the 115 pinned candidates, in order of symbol then date". ``daily`` holds each
    candidate's development daily bars; symbols are ordered as Python sorts text."""
    order = sorted(daily)
    n = sum(len(daily[s]) for s in order)
    draws = default_rng(seed).random(n)
    flips: dict[str, dict[int, bool]] = {}
    i = 0
    for symbol in order:
        days: dict[int, bool] = {}
        previous = None
        for bar in daily[symbol]:
            if previous is not None and bar.open_time <= previous:
                raise SimulationError(f"{symbol}'s daily bars are not in date order")
            days[bar.open_time] = draws[i] < 0.5
            previous = bar.open_time
            i += 1
        flips[symbol] = days
    return flips


class SignFlipped:
    """Section 8, C-H1: the votes read "a synthetic price series per instrument: starting
    at its first close, compounding its actual daily simple returns with a day's sign
    flipped" where that day's draw is below one half. Volatility, sizing, the universe
    and the profit and loss keep the actual prices."""

    def __init__(self, flips: Mapping[str, Mapping[int, bool]]) -> None:
        self.flips = flips
        self.paths: dict[Instrument, list[float]] = {}

    def close(self, instrument: Instrument, view, index: int) -> float:
        start = view.index_of(instrument.first_open)
        flips = self.flips.get(instrument.symbol)
        if start is None or flips is None:
            raise SimulationError(f"{instrument} has no drawn signs (not a pinned candidate)")
        path = self.paths.setdefault(instrument, [])
        while len(path) <= index - start:
            j = start + len(path)
            bar = view[j]
            if not path:
                path.append(bar.close)  # "starting at its first close"
                continue
            if bar.open_time not in flips:
                raise SimulationError(f"{instrument.symbol} has no draw for {bar.open_time}")
            r = bar.close / view[j - 1].close - 1.0
            sign = -1.0 if flips[bar.open_time] else 1.0
            path.append(path[-1] * (1.0 + sign * r))
        return path[index - start]


def _is_spot_daily(name: str) -> bool:
    parts = name.split("/")
    return len(parts) == 4 and parts[:2] == ["spot", "klines"] and parts[3] == "1d"
