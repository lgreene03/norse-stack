"""H2, the funding carry (goal G2.3), with its benchmark B-H2 and control C-H2.

Genesis pins ``run`` in this module for every id in ``CONFIGURATIONS``
(docs/research/trial-ledger.md). The harness resolves each run's configuration from
docs/preregistration.json, which governs, and hands it here frozen with the runner's
``DataView``; this module reads its parameters from that configuration and never from
a copy of the grid. Every rule names the section and item of docs/PREREGISTRATION.md
it implements. The two legs, their joint fill, costs, marks and funding accruals are
the engine's, reached only through ``norse_research.sim``. docs/research/strategies.md
sets out each configuration and every reading of the registration made here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from norse_research.formats import Funding
from norse_research.sim import (
    DAY_MS,
    CarryTarget,
    Decision,
    Market,
    SimulationError,
    Window,
    carry_thresholds,
    day_ms,
    default_rng,
    funding_series,
    iso_day,
    next_carry_state,
    perp_series,
    perpetual_of,
    run_backtest,
    spot_series,
    trailing_funding,
)

# Read as literals by the harness and the genesis step (sources.strategy_declarations).
CONFIGURATIONS = ["H2-01", "H2-02", "H2-03", "H2-04", "H2-05", "H2-06", "B-H2", "C-H2"]
PARAMETERS = [
    "allocation_per_coin", "always_on", "coins", "entry_multiple", "entry_threshold",
    "exit_multiple", "exit_threshold", "lookback_days", "round_trip_cost",
]
# No defaults: every value a grid configuration or the control uses is registered, and
# the benchmark's rule (section 8) has no parameter.
DEFAULTS = {}

# Section 7, The H2 grid; section 8, Benchmarks and Control runs.
GRID = tuple(c for c in CONFIGURATIONS if c.startswith("H2-"))
BENCHMARK = "B-H2"
CONTROL = "C-H2"
CONTROL_BASE = "H2-01"  # preregistration.json controls[C-H2].base
# Section 9: a grid configuration runs as a trial, a sensitivity run or the holdout run.
GRID_KINDS = ("trial", "sensitivity", "holdout")
# preregistration.json periods.development_end_exclusive. Section 11, item 6: no run
# but the holdout run reads past it; section 8: C-H2 shifts development settlements only.
DEVELOPMENT_END = "2026-01-01"

# Section 8, B-H2: "BTC and ETH spot at half of equity each".
BENCHMARK_COINS = ("BTCUSDT", "ETHUSDT")
BENCHMARK_FRACTION = 0.5
# Section 8, C-H2: "k = rng.integers(540, M - 540, endpoint=True) (at least 180 days from
# either end) ... BTC drawn before ETH".
CONTROL_DRAW_ORDER = ("BTCUSDT", "ETHUSDT")
CONTROL_MARGIN = 540


# --- the pinned function ----------------------------------------------------------------


def run(configuration, data_view):
    """The daily net returns of one registered H2 configuration, B-H2 or C-H2, over the
    resolved configuration's window, in the shape the runner hands to the harness. The
    recorded gaps its loads cross reach the ledger through the runner, which takes them
    from the loader (section 5, Gaps). The configuration is checked first, before the
    data view is touched (``check_run``)."""
    check_run(configuration)
    return simulate(configuration, load_market(configuration, data_view))


def series_needed(configuration: Mapping) -> list[str]:
    """The series a run of ``configuration`` reads, all named by the registration: each
    coin's spot daily and 15-minute bars (marks and fills, section 5) and, for a carry
    run, its perpetual's daily and 15-minute bars and funding (section 5, Marks and
    Funding; section 7). Section 4: the perpetual is "the USDT-margined perpetual on the
    same coin", which for BTC and ETH is quoted per coin. B-H2 holds spot only (section 8).
    A series the manifests do not pin fails the run when the engine asks its coverage."""
    coins = tuple(configuration["parameters"].get("coins") or BENCHMARK_COINS)
    wanted = []
    for coin in coins:
        wanted += [spot_series(coin, "1d"), spot_series(coin, "15m")]
        if configuration["id"] != BENCHMARK:
            wanted += [perp_series(coin, "1d"), perp_series(coin, "15m"), funding_series(coin)]
    return sorted(wanted)


def load_market(configuration: Mapping, data_view) -> Market:
    """The run's market, read through the runner's ``DataView`` to the window's end
    (section 3), each series from its first archived row. The engine takes each series'
    archived runs from ``data_view.coverage``, the committed manifests' metadata (both
    manifests for the holdout run), so no span is committed here."""
    window = Window.from_configuration(configuration)
    return Market.from_view(data_view, series_needed(configuration), window.end)


def simulate(configuration: Mapping, market: Market) -> list[tuple[str, float]]:
    """The run on a market already read (the batch driver, ``backtest``)."""
    rows = {name: series.rows for name, series in market.series.items()}
    return run_backtest(strategy_for(configuration, rows), configuration, market)


def check_run(configuration: Mapping) -> None:
    """Refuse a configuration this module does not run, from the configuration alone, so
    ``run`` refuses before any coverage or load (choice 12): an id that is not H2's, a
    kind other than the id's registered kind (section 9), a control on another base or
    other coins, and a window past the development period outside the holdout run."""
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
    elif cid == BENCHMARK:
        if kind != "benchmark":
            raise SimulationError(f"{cid} runs as a benchmark")
    elif cid == CONTROL:
        if kind != "control" or configuration["base"] != CONTROL_BASE:
            raise SimulationError(f"{cid} runs as a control on {CONTROL_BASE}")
        if set(configuration["parameters"]["coins"]) != set(CONTROL_DRAW_ORDER):
            raise SimulationError(f"C-H2 draws for {CONTROL_DRAW_ORDER}")
    else:
        raise SimulationError(f"{cid} is not an H2 configuration, benchmark or control")


def strategy_for(configuration: Mapping, rows: Mapping[str, Sequence]):
    """The strategy object for a resolved configuration. ``rows`` holds the run's daily
    and funding series by name; only C-H2 reads them, to shift its funding series."""
    check_run(configuration)
    cid = configuration["id"]
    if cid in GRID:
        return Carry(configuration["parameters"])
    if cid == BENCHMARK:
        return Halves(Window.from_configuration(configuration).start)
    perps = [n.split("/")[2] for n in rows if n.startswith("um/fundingRate/")]
    end = day_ms(DEVELOPMENT_END)
    development = {
        coin: [s for s in rows[funding_series(_perpetual(coin, perps))] if s.calc_time < end]
        for coin in CONTROL_DRAW_ORDER
    }
    return Carry(configuration["parameters"],
                 shifted_funding(development, configuration["seed"]))


def _perpetual(coin: str, perpetuals: Sequence[str]) -> str:
    """Section 4: "A spot pair's perpetual is the USDT-margined perpetual on the same
    coin"; section 7 holds "the USDT-margined perpetual short"."""
    perp = perpetual_of(coin, perpetuals)
    if perp is None:
        raise SimulationError(f"{coin} has no perpetual in this run's data")
    return perp


def is_month_end(t: int) -> bool:
    """Section 8: "each month-end decision", 00:00 UTC on the first of a month."""
    return t % DAY_MS == 0 and iso_day(t).endswith("-01")


# --- H2: the carry strategy (section 7) -------------------------------------------------


class Carry:
    """Section 7: for each coin separately, long spot and short its perpetual in equal
    quantity while in carry. A flat coin enters when its trailing funding F is above the
    entry threshold and a coin in carry exits when F is below the exit threshold; the
    always-on configuration enters at the first decision and never exits. ``shifted``
    (C-H2 only) replaces the series F is summed over with section 8's shifted one."""

    # Section 7, Rule: "At each daily decision t".
    interval = "1d"

    def __init__(self, parameters: Mapping,
                 shifted: Mapping[str, Sequence[Funding]] | None = None) -> None:
        self.coins = tuple(parameters["coins"])
        if len(set(self.coins)) != len(self.coins) or not self.coins:
            raise SimulationError(f"H2 holds distinct coins, not {self.coins!r}")
        # Section 7, Sizing and holding: "Each coin uses half of equity".
        self.allocation = float(parameters["allocation_per_coin"])
        # H2-06, section 7: "enter at the first decision", "never exits".
        self.always_on = bool(parameters["always_on"])
        self.shifted = shifted
        if self.always_on:
            if parameters["lookback_days"] is not None:
                raise SimulationError("the always-on configuration has no lookback")
            self.lookback = self.entry = self.exit = None
            return
        # Section 7, Rule: entry threshold = m_entry x c x L / 28, exit threshold =
        # m_exit x c x L / 28, with c = 4 x 12 bps. The grid also registers the results;
        # the two must agree exactly, so the rule and the registered figures are one.
        self.lookback = parameters["lookback_days"]
        self.entry, self.exit = carry_thresholds(
            parameters["entry_multiple"], parameters["exit_multiple"],
            parameters["round_trip_cost"], self.lookback)
        registered = (parameters["entry_threshold"], parameters["exit_threshold"])
        if (self.entry, self.exit) != registered:
            raise SimulationError(
                f"thresholds {(self.entry, self.exit)} differ from the registered {registered}")

    def decide(self, history, book):
        targets = []
        for coin in self.coins:
            instrument = history.instrument(coin)
            if instrument is None:
                continue
            offered = [p for p in (coin, "1000" + coin) if history.has(funding_series(p))]
            perp = _perpetual(coin, offered)
            if self.always_on:
                carry = True
            else:
                carry = next_carry_state(book.in_carry(coin), self.trailing(history, coin, perp),
                                         self.entry, self.exit)
            # Section 7, Sizing and holding: "q = 0.5 x equity divided by the spot fill's
            # reference price"; a fraction of zero takes the coin flat.
            targets.append(CarryTarget(instrument, perp, self.allocation if carry else 0.0))
        return Decision(carry=tuple(targets))

    def trailing(self, history, coin: str, perp: str) -> float:
        """Section 7, Rule: "F is the sum of its published funding rates with calc_time in
        the interval (t - L days + 60 s, t + 60 s]"."""
        settlements = (history.series(funding_series(perp)) if self.shifted is None
                       else self.shifted[coin])
        return trailing_funding(settlements, history.now, self.lookback).total


# --- section 8: the benchmark B-H2 ------------------------------------------------------


class Halves:
    """Section 8, B-H2: "BTC and ETH spot at half of equity each from the first H2
    decision, traded back to halves at each month-end decision, with the same fills,
    costs and rule for halted pairs". Single-leg spot positions with a band of zero."""

    interval = "1d"

    def __init__(self, first_decision: int) -> None:
        self.first_decision = first_decision

    def decide(self, history, book):
        t = history.now
        if t != self.first_decision and not is_month_end(t):
            return None
        weights = {}
        for coin in BENCHMARK_COINS:
            instrument = history.instrument(coin)
            if instrument is not None:
                weights[instrument] = BENCHMARK_FRACTION
        return Decision(weights=weights, band=0.0)


# --- section 8: the control C-H2 --------------------------------------------------------


def control_shifts(settlement_counts: Mapping[str, int], seed: int) -> dict[str, int]:
    """Section 8, C-H2: on one ``rng = numpy.random.default_rng(seed)``, each coin's k =
    ``rng.integers(540, M - 540, endpoint=True)`` with M its number of development
    settlements, BTC drawn before ETH."""
    rng = default_rng(seed)
    out = {}
    for coin in CONTROL_DRAW_ORDER:
        m = settlement_counts[coin]
        if m - CONTROL_MARGIN < CONTROL_MARGIN:
            raise SimulationError(f"{coin} has {m} settlements, too few to shift")
        out[coin] = rng.integers(CONTROL_MARGIN, m - CONTROL_MARGIN, endpoint=True)
    return out


def shifted_funding(development: Mapping[str, Sequence[Funding]],
                    seed: int) -> dict[str, list[Funding]]:
    """Section 8, C-H2: "each coin's entry and exit decided on its development funding
    series shifted circularly by k settlements (the value used at settlement i is the
    series' value at index (i + k) mod M)". Each settlement keeps its own calc_time, so
    the trailing interval and when a value is known are unchanged; "funding received and
    the profit and loss use the actual series", which the engine accrues from the data."""
    shifts = control_shifts({c: len(development[c]) for c in CONTROL_DRAW_ORDER}, seed)
    out = {}
    for coin in CONTROL_DRAW_ORDER:
        series, k = development[coin], shifts[coin]
        m = len(series)
        out[coin] = [Funding(s.calc_time, s.interval_hours, series[(i + k) % m].rate)
                     for i, s in enumerate(series)]
    return out
