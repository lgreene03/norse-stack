"""The target-position engine every H1, H2, benchmark and control run is computed by
(goal G1.9; docs/research/simulation-engine.md).

It implements sections 2 to 5 of docs/PREREGISTRATION.md (the window, instruments,
halts and delistings, fills, marks, costs, gaps and funding) and the mechanics
sections 6 to 8 need (the dated universe, volatility-targeted weights, the band, H2's
two legs and their funding, and the costs of benchmarks, controls and sensitivity
runs). Strategies (G2.2, G2.3) import it from here, the one engine path the harness's
source check allows (``norse_research.sources.ALLOWED_IMPORTS``); it opens no file,
socket or process and reads data only through the runner's ``DataView``.

A strategy is an object with an ``interval`` ("1d" or "4h") and
``decide(history, book) -> Decision | None``. ``backtest`` runs it over data read up
front; ``Paper`` runs the same object fed one row at a time; ``replay`` turns recorded
rows into that feed. The runner seals the engine before a strategy's module runs:
its modules, classes and tables cannot be changed, only used. A strategy module's
pinned function typically reads::

    def run(configuration, data_view):
        market = Market.from_view(data_view, series, horizon, history_from)
        return run_backtest(MyStrategy(configuration["parameters"]), configuration, market)
"""

from .book import Book
from .drivers import (
    BarEvent,
    BookView,
    InstrumentEnded,
    Paper,
    RunResult,
    SettlementEvent,
    Tick,
    backtest,
    paper_run,
    replay,
    run_backtest,
)
from .history import History
from .market import Market, RowsSource, SeriesView, Span, ViewSource, runs_of
from .model import (
    BUY,
    DAY_MS,
    FOUR_HOURS_MS,
    PERP,
    SELL,
    SPOT,
    Accrual,
    CarryTarget,
    Costs,
    Decision,
    Fill,
    Instrument,
    Lapse,
    Order,
    SimulationError,
    Skip,
    Window,
    day_ms,
    funding_series,
    iso_day,
    perp_series,
    spot_series,
)
from .pcg64 import Generator, default_rng
from .targeting import (
    RANK_DEPTH,
    REGISTERED_EXCLUSIONS,
    FundingWindow,
    carry_thresholds,
    month_of_decision,
    next_carry_state,
    perpetual_of,
    quote_volumes,
    rank_top,
    rebalances,
    select_universe,
    signal,
    target_weight,
    trailing_funding,
    vote,
)

__all__ = (
    "BUY",
    "DAY_MS",
    "FOUR_HOURS_MS",
    "PERP",
    "RANK_DEPTH",
    "REGISTERED_EXCLUSIONS",
    "SELL",
    "SPOT",
    "Accrual",
    "BarEvent",
    "Book",
    "BookView",
    "CarryTarget",
    "Costs",
    "Decision",
    "Fill",
    "FundingWindow",
    "Generator",
    "History",
    "Instrument",
    "InstrumentEnded",
    "Lapse",
    "Market",
    "Order",
    "Paper",
    "RowsSource",
    "RunResult",
    "SeriesView",
    "SettlementEvent",
    "SimulationError",
    "Skip",
    "Span",
    "Tick",
    "ViewSource",
    "Window",
    "backtest",
    "carry_thresholds",
    "day_ms",
    "default_rng",
    "funding_series",
    "iso_day",
    "month_of_decision",
    "next_carry_state",
    "paper_run",
    "perp_series",
    "perpetual_of",
    "quote_volumes",
    "rank_top",
    "rebalances",
    "replay",
    "run_backtest",
    "runs_of",
    "select_universe",
    "signal",
    "spot_series",
    "target_weight",
    "trailing_funding",
    "vote",
)
