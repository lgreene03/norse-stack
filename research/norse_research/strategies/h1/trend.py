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

from norse_research.formats import DatasetSpec
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

# Section 6, The universe, step 1: "Rank USDT spot symbols", a pair read as G0.6's
# universe.base_asset reads one, by the quote at the end of its symbol.
QUOTE = "USDT"
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
    resolved configuration's window, in the shape the runner hands to the harness.

    The series come from ``data_view.list_series()``, every series the run's committed
    manifests pin (both manifests in the holdout run), so no list of pairs is committed
    here and a listing error is a data-loading fix (section 11, item 6)."""
    names = series_needed(configuration, data_view.list_series())
    listed = listed_pairs(data_view, names)
    market = load_market(configuration, data_view, names)
    return simulate(configuration, market, listed)


def series_needed(configuration: Mapping, names) -> list[str]:
    """The series a run of ``configuration`` reads, from the names the data layer lists,
    each parsed by ``DatasetSpec.from_name``.

    Spot klines of USDT pairs only (section 6, step 1: "Rank USDT spot symbols"): every
    daily series, since step 1 ranks them all, "delisted symbols included", and the daily
    bars also define instruments (section 4) and marks (section 5); every 15-minute
    series, read lazily for fills (section 5, Fills); and every four-hour series for
    H1-06 (section 6, Four-hour bars). The perpetuals' funding for H1-07 (section 6,
    Funding filter). Nothing else the manifests pin is read."""
    parameters = configuration["parameters"]
    # Section 6, Four-hour bars: H1-06 reads its signal at four-hour closes; the universe
    # is ranked on daily bars at every bar (step 1: "the 30 daily bars").
    intervals = {"1d", "15m", parameters.get("bar", "1d")}
    filtered = parameters.get("funding_filter") is not None
    out = []
    for name in sorted(names):
        spec = DatasetSpec.from_name(name)
        if spec.market == "spot" and spec.kind == "klines":
            if spec.interval in intervals and is_usdt_pair(spec.symbol):
                out.append(name)
        elif spec.market == "um" and spec.kind == "fundingRate" and filtered:
            out.append(name)
    return out


def is_usdt_pair(symbol: str) -> bool:
    """Section 6, step 1: a USDT spot symbol, read as G0.6's ``universe.base_asset``
    reads one (a base asset followed by the quote)."""
    return symbol.endswith(QUOTE) and len(symbol) > len(QUOTE)


def listed_pairs(data_view, names) -> dict[str, int]:
    """Each listed spot daily pair, by symbol, with the open time of its first archived
    bar, from ``data_view.coverage`` (the committed manifests' metadata, both manifests
    in the holdout run, read without opening a data file).

    ``month_end_ranking`` ranks a pair only at decisions after that bar has closed. In
    the holdout run the listing also holds pairs first archived in 2026, whose coverage
    starts in the holdout, so no development month end ranks them (section 11, item 5:
    the holdout run's returns to 2025-12-31 equal the development run's exactly)."""
    out = {}
    for name in names:
        spec = DatasetSpec.from_name(name)
        if spec.market == "spot" and spec.kind == "klines" and spec.interval == "1d":
            out[spec.symbol] = min(first for first, _ in data_view.coverage(name))
    return out


def load_market(configuration: Mapping, data_view, names) -> Market:
    """The run's market, read through the runner's ``DataView`` to the window's end
    (section 3), each series from its first archived row (section 6: the volatility is
    "over every return of the instrument", and D13 needs the instrument's start). The
    engine takes each series' archived runs from ``data_view.coverage``, the committed
    manifests' metadata, so no span is committed here."""
    window = Window.from_configuration(configuration)
    return Market.from_view(data_view, series_needed(configuration, names), window.end)


def simulate(configuration: Mapping, market: Market,
             listed: Mapping[str, int]) -> list[tuple[str, float]]:
    """The run on a market already read (the batch driver, ``backtest``); ``listed`` is
    ``listed_pairs`` of the same names."""
    rows = {name: series.rows for name, series in market.series.items()}
    return run_backtest(strategy_for(configuration, rows, listed), configuration, market)


def strategy_for(configuration: Mapping, rows: Mapping[str, Sequence],
                 listed: Mapping[str, int]):
    """The strategy object for a resolved configuration. ``listed`` holds the pairs the
    universe ranks, each with its first archived open (``listed_pairs``). ``rows`` holds
    the run's daily (and four-hour and funding) series by name; only C-H1 reads it, to
    draw one random number per development daily bar of the candidates."""
    cid, kind = configuration["id"], configuration["kind"]
    window = Window.from_configuration(configuration)
    if kind != "holdout" and window.end > day_ms(DEVELOPMENT_END):
        # Section 11, item 6: "No other configuration, and no benchmark, control or
        # sensitivity run, ever reads holdout data"; section 3 ends development trading
        # at the last development close.
        raise SimulationError(f"a {kind} run ends at {DEVELOPMENT_END} at the latest")
    if cid in GRID:
        if kind not in GRID_KINDS:
            raise SimulationError(f"{cid} runs as a trial, sensitivity or holdout run")
        return Trend(configuration["parameters"], listed)
    if cid == BENCHMARK:
        if kind != "benchmark":
            raise SimulationError(f"{cid} runs as a benchmark")
        return MonthEndEqualWeight(listed)
    if cid == CONTROL:
        if kind != "control" or configuration["base"] != CONTROL_BASE:
            raise SimulationError(f"{cid} runs as a control on {CONTROL_BASE}")
        end = day_ms(DEVELOPMENT_END)
        daily = {
            s: [bar for bar in rows[spot_series(s, "1d")] if bar.open_time < end]
            for s in CANDIDATES if spot_series(s, "1d") in rows
        }
        flipped = SignFlipped(sign_flips(daily, configuration["seed"]))
        return Trend(configuration["parameters"], listed, flipped)
    raise SimulationError(f"{cid} is not an H1 configuration, benchmark or control")


# --- section 6: the dated universe ------------------------------------------------------


def is_month_end(t: int) -> bool:
    """Section 6, The universe: "At each month-end close (the decision at 00:00 UTC on
    the first of the next month)"."""
    return t % DAY_MS == 0 and iso_day(t).endswith("-01")


def month_end_ranking(history, listed: Sequence[tuple[str, int]]) -> list[str]:
    """Section 6, The universe, step 1, at the month-end decision ``history.now``: the
    ten largest listed pairs by quote volume over the 30 daily bars closing at the month
    end, "exactly as rank_month_ends computes it" (the engine's ``rank_top``, which first
    removes the 27 stablecoin and fiat bases). Only the bars in that 30-day window are
    passed, which ``rank_month_ends`` would read anyway.

    ``listed`` pairs each symbol with its first archived open (``listed_pairs``). A pair
    whose archive starts at or after the decision has no bar closed by then and is skipped
    before ranking: only the holdout run lists such pairs, and skipping them keeps every
    development month's ranking identical to the development run's (section 11, item
    5). ``rank_month_ends`` would leave them out anyway, since it never ranks a pair with
    no volume in the window; the skip makes that hold by the manifests' metadata."""
    t = history.now
    first_open = t - RANK_DAYS * DAY_MS
    volumes = {}
    for symbol, first_archived in listed:
        name = spot_series(symbol, "1d")
        if first_archived >= t or not history.has(name):
            continue
        view = history.series(name)
        volumes[symbol] = quote_volumes(view[view.index_after(first_open - 1):])
    return rank_top(volumes, month_of_decision(t))


def month_end_universe(history, listed: Sequence[tuple[str, int]],
                       size: int) -> list[Instrument]:
    """Section 6, The universe, steps 1 to 3, at the month-end decision ``history.now``.
    Step 1 is ``month_end_ranking``; steps 2 and 3 (exclusions by category, D14, as
    preregistration.json classifies the candidates; the month-end bar and a close 120
    days back, D13; the first five, cash for a missing one) are the engine's
    ``select_universe``."""
    ranked = month_end_ranking(history, listed)
    return select_universe(history, ranked, slots=size, history_days=ELIGIBILITY_DAYS)


# --- H1: the trend strategy (section 6) -------------------------------------------------


class Trend:
    """Section 6: long or flat, spot only, on the month's dated universe.

    At each decision every live member gets w* = (1/slots) x s x min(cap, target / sigma),
    or (1/slots) x s without volatility scaling, as a fraction of equity marked then, and
    is traded to it only outside the band. ``flipped`` (C-H1 only) replaces the closes
    the votes read with section 8's sign-flipped series; nothing else changes."""

    def __init__(self, parameters: Mapping, listed: Mapping[str, int],
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
        # Section 6, step 1: the listed pairs the universe ranks, with their first
        # archived opens (``listed_pairs``).
        self.listed = tuple(sorted(listed.items()))
        self.members: list[Instrument] = []

    def decide(self, history, book):
        t = history.now
        members = self.members
        if is_month_end(t):
            # Section 6, Rebalancing: "the universe changes only monthly"; Four-hour bars:
            # "the universe updated only at the month-end 00:00 decision".
            members = month_end_universe(history, self.listed, self.slots)
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

    def __init__(self, listed: Mapping[str, int]) -> None:
        self.listed = tuple(sorted(listed.items()))

    def decide(self, history, book):
        if not is_month_end(history.now):
            return None
        members = month_end_universe(history, self.listed, UNIVERSE_SIZE)
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
