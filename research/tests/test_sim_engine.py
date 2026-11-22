"""The simulation engine as a whole (G1.9): one strategy object, two drivers, identical
records; what a strategy may see; the engine inside the harness's runner; and its
data contract against the development manifest."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import sim_synthetic
from norse_research import sim
from norse_research.formats import Kline
from norse_research.harness import _RUNNER
from norse_research.runner import RESULT_PREFIX
from norse_research.sim import (
    Costs,
    Decision,
    Market,
    SimulationError,
    Span,
    ViewSource,
    Window,
    backtest,
    paper_run,
    spot_series,
)
from norse_research.sources import ALLOWED_IMPORTS, disallowed_imports
from sim_example import CarryExample, Scripted, TrendExample

RESEARCH = Path(__file__).resolve().parent.parent
PACKAGE = RESEARCH / "norse_research"
FIRST = sim_synthetic.ms(2021, 1, 1)
COSTS = Costs(10, 2)


@pytest.fixture(scope="module")
def rows():
    return sim_synthetic.market()


def window(first_day="2021-02-01", days=88):
    last = datetime.fromtimestamp((sim.day_ms(first_day) + (days - 1) * sim.DAY_MS) / 1000,
                                  tz=UTC).strftime("%Y-%m-%d")
    return Window(first_day, last, days)


def both(strategy, rows, run_window):
    batch = backtest(strategy, Market.from_rows(rows, run_window.end), run_window, COSTS)
    paper = paper_run(strategy, rows, run_window, COSTS)
    return batch, paper


def assert_identical(batch, paper):
    for name in ("orders", "fills", "lapses", "accruals", "skips", "marks", "returns"):
        assert getattr(batch, name) == getattr(paper, name), name


# --- one strategy object, two drivers ---------------------------------------------------


def test_daily_trend_backtest_and_paper_are_identical(rows):
    strategy = TrendExample(["AAAUSDT", "BBBUSDT", "CCCUSDT"])
    batch, paper = both(strategy, rows, window())
    assert_identical(batch, paper)
    kinds = {f.kind for f in batch.fills}
    assert kinds == {"fill", "halt"}, "BBB stops trading inside the window (section 4)"
    assert any(f.bar_open != f.order.decided_at for f in batch.fills if f.kind == "fill"), (
        "some fill waits for a later bar (section 5, Fills)")
    assert {lapse.order.symbol for lapse in batch.lapses} == {"AAAUSDT"}, (
        "orders decided on AAA's days without volume lapse (section 5, Fills)")
    assert len(batch.returns) == 88


def test_four_hour_trend_backtest_and_paper_are_identical(rows):
    strategy = TrendExample(["AAAUSDT", "BBBUSDT"], interval="4h", lookbacks=(30, 60, 120),
                            span=60)
    batch, paper = both(strategy, rows, window())
    assert_identical(batch, paper)
    assert batch.skips, "AAA's missing four-hour bar skips that decision (section 5, item 2)"
    assert all(s.symbols == ("AAAUSDT",) for s in batch.skips)
    assert any(f.order.decided_at % sim.DAY_MS for f in batch.fills if f.kind == "fill"), (
        "decisions between midnights trade (section 6, Four-hour bars)")
    # Section 5: a four-hour decision's order lapses four hours on.
    assert batch.lapses and all(
        lapse.at == lapse.order.decided_at + sim.FOUR_HOURS_MS for lapse in batch.lapses)


def test_carry_backtest_and_paper_are_identical(rows):
    strategy = CarryExample(["BTCUSDT"], entry_multiple=1, exit_multiple=0)
    batch, paper = both(strategy, rows, window(days=80))
    assert_identical(batch, paper)
    assert batch.accruals and {f.market for f in batch.fills} == {"spot", "perp"}
    # Section 7: the legs fill on one bar in equal quantities.
    for spot, perp in zip(batch.fills[::2], batch.fills[1::2], strict=True):
        assert (spot.market, perp.market) == ("spot", "perp")
        assert spot.bar_open == perp.bar_open and spot.quantity == perp.quantity
    # Section 5: on BTC spot's days without volume both legs lapse, never one.
    assert batch.lapses
    for spot, perp in zip(batch.lapses[::2], batch.lapses[1::2], strict=True):
        assert (spot.order.market, perp.order.market) == ("spot", "perp")
        assert spot.order.group == perp.order.group and spot.at == perp.at


def test_returns_cover_the_window_in_the_runners_shape(rows):
    run_window = window(days=30)
    configuration = {"window": {"first_return_day": run_window.first_day,
                                "last_return_day": run_window.last_day, "days": 30},
                     "costs": {"fee_bps_per_side": 10, "slippage_bps_per_side": 2}}
    returns = sim.run_backtest(TrendExample(["AAAUSDT"]), configuration,
                               Market.from_rows(rows, run_window.end))
    assert [d for d, _ in returns] == run_window.day_list()
    assert all(isinstance(v, float) for _, v in returns)


# --- what a strategy may see ------------------------------------------------------------


class Peeking:
    """Records what it can see at each decision."""

    interval = "1d"

    def __init__(self):
        self.seen = []

    def decide(self, history, book):
        view = history.series(spot_series("AAAUSDT", "1d"))
        today = view.at(history.now)  # opens now, closes tomorrow: not known yet
        yesterday = view.at(history.now - sim.DAY_MS)
        funding = history.series("um/fundingRate/BTCUSDT")
        self.seen.append((history.now, today, yesterday is not None, funding[-1].calc_time,
                          len(view)))
        return None


def test_a_backtest_strategy_sees_nothing_after_its_decision(rows):
    strategy = Peeking()
    run_window = window(days=10)
    backtest(strategy, Market.from_rows(rows, run_window.end), run_window, COSTS)
    paper_view = Peeking()
    paper_run(paper_view, rows, run_window, COSTS)
    assert strategy.seen == paper_view.seen
    for now, today, has_yesterday, last_funding, _ in strategy.seen:
        assert today is None and has_yesterday
        # Section 5, Funding: known at t if calc_time is at most t + 60 s.
        assert now - 8 * 3_600_000 < last_funding <= now + 60_000


def test_decision_validation_refuses_leverage_shorts_and_mixed_kinds():
    instrument = sim.Instrument("AAAUSDT", 0)
    with pytest.raises(SimulationError, match="sum to more than equity"):
        Decision(weights={instrument: 0.7, sim.Instrument("BBBUSDT", 0): 0.4})
    with pytest.raises(SimulationError, match="non-negative"):
        Decision(weights={instrument: -0.1})
    with pytest.raises(SimulationError, match="not both"):
        Decision(weights={}, carry=())


def test_costs_have_no_default():
    with pytest.raises(KeyError):
        Costs.from_configuration({"window": {}})
    with pytest.raises(SimulationError):
        Costs(-1, 2)


# --- the engine in the harness ----------------------------------------------------------


def test_the_engine_is_reachable_through_the_strategy_allowlist():
    """A strategy under a hypothesis's paths may import the engine, and the example
    strategies pass the harness's source check exactly as a pinned module would."""
    assert "norse_research.sim" in ALLOWED_IMPORTS
    source = (Path(__file__).parent / "sim_example.py").read_text()
    assert disallowed_imports(source, "norse_research.strategies.h1.example",
                              allowed_paths=["research/norse_research/strategies/h1"]) == []
    # Only the package is allowed, so the engine's surface is what it exports.
    assert disallowed_imports("from norse_research.sim.book import Book\n",
                              "norse_research.strategies.h1.example") != []


# The standard library the engine binds names from. A strategy reaches these only as the
# names the engine binds, and test_strategy_reach.py walks every such path (security
# review of PR 61, round 4): the engine may use more than strategies may import, so
# long as nothing it hands out reaches further.
ENGINE_STDLIB = frozenset({"__future__", "bisect", "collections.abc", "dataclasses", "datetime",
                           "heapq", "itertools", "math"})


def test_the_engine_imports_nothing_a_strategy_may_not():
    """The engine reaches only a fixed set of the standard library, bound by name, its
    own modules, ``formats`` and the pure universe ranking (G0.6): no store, loader,
    path, socket or process module a strategy could reach through it (threat T13)."""
    for path in sorted((PACKAGE / "sim").glob("*.py")):
        name = f"norse_research.sim.{path.stem}" if path.stem != "__init__" else "norse_research.sim"
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                raise AssertionError(f"{path.name}: binds a whole module, {node.names[0].name}")
            if isinstance(node, ast.ImportFrom) and not node.level:
                assert node.module in ENGINE_STDLIB, f"{path.name}: from {node.module}"
        problems = disallowed_imports(path.read_text(), name, path.stem == "__init__",
                                      ["research/norse_research/sim"])
        problems = [p for p in problems if "norse_research.universe" not in p]
        problems = [p for p in problems
                    if not any(f"from {m} import" in p for m in ENGINE_STDLIB)]
        # The engine's own classes build on formats.Frozen and EngineClass; the rule
        # against subclassing a class from this package is about strategies (E1).
        problems = [p for p in problems if "imported from this package" not in p]
        assert problems == [], path.name
    universe_imports = disallowed_imports((PACKAGE / "universe.py").read_text(),
                                          "norse_research.universe")
    assert universe_imports == ["line 19: import calendar"], (
        "universe.py, which the engine imports, should reach only the standard calendar")


def test_the_engine_runs_inside_the_runner_tripwire(tmp_path):
    """The engine opens no file, socket or process: a strategy using it runs in the
    runner's fresh interpreter under the audit hook (T13) and returns its returns."""
    code_root = tmp_path / "code"
    shutil.copytree(PACKAGE, code_root / "research" / "norse_research",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(Path(__file__).parent / "sim_example.py",
                code_root / "research" / "norse_research" / "strategies" / "example.py")
    module = code_root / "research" / "norse_research" / "strategies" / "runner_strategy.py"
    module.write_text(RUNNER_STRATEGY)
    pycache = tmp_path / "pycache"
    pycache.mkdir()
    payload = {
        "code_root": str(code_root),
        "pycache": str(pycache),
        "module_path": "research/norse_research/strategies/runner_strategy.py",
        "module_name": "norse_research.strategies.runner_strategy",
        "function": "run",
        "configuration": {
            "window": {"first_return_day": "2021-03-01", "last_return_day": "2021-03-03",
                       "days": 3},
            "costs": {"fee_bps_per_side": 10, "slippage_bps_per_side": 2},
        },
        "entry": "e" * 64,
        "manifest": "research/manifests/none.json",
        "store_root": None,
    }
    proc = subprocess.run(
        [sys.executable, "-I", "-B", "-X", f"pycache_prefix={pycache}", "-c", _RUNNER,
         str(code_root / "research"), str(code_root)],
        input=json.dumps(payload).encode(), capture_output=True, env={}, cwd=tmp_path,
        check=False,
    )
    lines = [line for line in proc.stdout.decode().splitlines() if line.startswith(RESULT_PREFIX)]
    assert lines, proc.stderr.decode()[-2000:]
    result = json.loads(lines[-1][len(RESULT_PREFIX):])
    assert result["ok"], result
    assert [d for d, _ in result["returns"]] == ["2021-03-01", "2021-03-02", "2021-03-03"]
    golden = json.loads((Path(__file__).parent / "fixtures" / "sim-golden.json").read_text())
    for (_, got), want in zip(result["returns"], golden["weights"]["expected"]["returns"],
                              strict=True):
        assert got == pytest.approx(want, rel=1e-12)


RUNNER_STRATEGY = '''
from norse_research.formats import Kline
from norse_research.sim import Market, run_backtest
from norse_research.strategies.example import Scripted

CONFIGURATIONS = ["EXAMPLE"]
PARAMETERS = []
DEFAULTS = {}

D = 86_400_000
M15 = 900_000
DAY0 = 1614297600000  # 2021-02-26


def bar(open_time, step, close, volume, quote, low=None):
    low = close * 0.95 if low is None else low
    return Kline(open_time, close, close * 1.05, low, close, volume, open_time + step - 1,
                 quote, 10, volume / 2, quote / 2)


MARCH = DAY0 + 3 * D
HORIZON = MARCH + 3 * D


def golden_rows():
    march = MARCH
    return {
        "spot/klines/AAAUSDT/1d": [bar(DAY0 + i * D, D, c, 1000.0, c * 1000.0)
                                   for i, c in enumerate([98.0, 99.0, 100.0, 110.0, 120.0,
                                                          120.0])],
        "spot/klines/BBBUSDT/1d": [bar(DAY0 + i * D, D, c, 1000.0, c * 1000.0,
                                       low=18.0 if i == 4 else None)
                                   for i, c in enumerate([19.0, 19.5, 20.0, 22.0, 21.0])],
        "spot/klines/AAAUSDT/15m": [bar(march, M15, 100.5, 0.0, 0.0),
                                    bar(march + M15, M15, 101.0, 100.0, 10100.0),
                                    bar(march + 2 * M15, M15, 102.0, 100.0, 10200.0),
                                    bar(march + D, M15, 110.0, 100.0, 11000.0),
                                    bar(march + 2 * D, M15, 121.0, 100.0, 12100.0)],
        "spot/klines/BBBUSDT/15m": [bar(march + 2 * M15, M15, 20.0, 100.0, 2000.0),
                                    bar(march + 3 * M15, M15, 21.0, 100.0, 2100.0),
                                    bar(march + D, M15, 21.0, 0.0, 0.0)],
    }


def golden_strategy(kind=Scripted):
    return kind("1d", weights={
        "2021-03-01": {"AAAUSDT": 0.5, "BBBUSDT": 0.3},
        "2021-03-02": {"AAAUSDT": 0.85, "BBBUSDT": 0.1},
        "2021-03-03": {"AAAUSDT": 0.0, "BBBUSDT": 0.3},
    })


def run(configuration, data_view):
    return run_backtest(golden_strategy(), configuration,
                        Market.from_rows(golden_rows(), HORIZON))
'''


# --- the data contract ------------------------------------------------------------------


def test_the_series_the_engine_reads_are_the_ones_the_development_manifest_pins():
    """Spot 1d, 4h and 15m for every development candidate; BTC and ETH perpetual klines
    at 1d and 15m; funding for the perpetuals (G1.1)."""
    manifest = json.loads((RESEARCH / "manifests" / "binance-vision-development.json").read_text())
    pinned = set(manifest["datasets"])
    candidates = json.loads((RESEARCH / "coverage" / "universe-candidates.json").read_text())
    for symbol in candidates["development_candidates"]:
        for interval in ("1d", "4h", "15m"):
            assert spot_series(symbol, interval) in pinned
    for coin in ("BTCUSDT", "ETHUSDT"):
        for interval in ("1d", "15m"):
            assert sim.perp_series(coin, interval) in pinned
        assert sim.funding_series(coin) in pinned


class FakeView:
    """Stands in for the runner's DataView: serves synthetic rows, records each call,
    refuses a range touching a month the manifest pins no file for (as the loader's
    MissingData does), returns the recorded gaps inside the range, and reports each
    series' coverage as the manifests would: given runs, or one run from its first to its
    last row at minute precision."""

    def __init__(self, rows, unpinned=(), gaps=None, coverage=None):
        self.rows = rows
        self.unpinned = set(unpinned)
        self.gaps = gaps or {}
        self.runs = coverage or {}
        self.calls = []

    def coverage(self, series):
        if series in self.runs:
            return self.runs[series]
        first, last = self.rows[series][0][0], self.rows[series][-1][0]
        return [(first - first % 60_000, last - last % 60_000)]

    def load(self, series, start, end, accept_recorded_gaps=False):
        self.calls.append((series, start, end, accept_recorded_gaps))
        lo, hi = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        assert start.tzinfo is not None and lo < hi
        last = datetime.fromtimestamp((hi - 1) / 1000, tz=UTC)
        month, stop = (start.year, start.month), (last.year, last.month)
        while month <= stop:
            if (series, f"{month[0]:04d}-{month[1]:02d}") in self.unpinned:
                raise LookupError(f"{series}: no file for {month}")
            month = (month[0] + 1, 1) if month[1] == 12 else (month[0], month[1] + 1)
        gaps = [g for g in self.gaps.get(series, []) if lo < g.before and g.after < hi]
        return SimpleNamespace(rows=[r for r in self.rows[series] if lo <= r[0] < hi],
                               gaps=gaps)


def test_view_source_reads_a_month_at_a_time_inside_each_series_span(rows):
    name = "spot/klines/BBBUSDT/15m"
    series = rows[name]
    span = Span(series[0].open_time, series[-1].open_time)
    view = FakeView(rows)
    source = ViewSource(view.load, {name: span})
    start = sim_synthetic.ms(2021, 3, 10)
    got = source.bars(name, start, start + sim.DAY_MS)
    assert got == [b for b in series if start <= b.open_time < start + sim.DAY_MS]
    source.bars(name, start + sim.DAY_MS, start + 2 * sim.DAY_MS)
    assert len(view.calls) == 1, "the month is read once and kept"
    (called, lo, hi, accept), = view.calls
    # Section 5, Gaps: every load accepts the recorded gaps.
    assert called == name and accept is True
    assert lo == datetime(2021, 3, 1, tzinfo=UTC)
    # BBB stops on 2021-03-12: the read ends at its last bar, which the loader requires.
    assert hi == datetime.fromtimestamp((span.last_open + 900_000) / 1000, tz=UTC)


def test_market_from_view_loads_whole_series_and_defers_fill_bars(rows):
    """Spans come from the view's coverage (the manifests), not from the strategy. A
    funding series' coverage is minute-precise, so its read reaches a minute past its
    last settlement's minute, which keeps settlements with millisecond jitter."""
    names = ["spot/klines/AAAUSDT/1d", "spot/klines/AAAUSDT/15m", "um/fundingRate/BTCUSDT"]
    view = FakeView(rows)
    horizon = sim_synthetic.ms(2021, 3, 1)
    market = Market.from_view(view, names, horizon, history_from=FIRST)
    assert {c[0] for c in view.calls} == {"spot/klines/AAAUSDT/1d", "um/fundingRate/BTCUSDT"}
    assert all(c[3] is True for c in view.calls)
    assert market.series["spot/klines/AAAUSDT/1d"].rows[-1].open_time == horizon - sim.DAY_MS
    assert "spot/klines/AAAUSDT/15m" not in market.series
    funding = rows["um/fundingRate/BTCUSDT"]
    assert list(market.series["um/fundingRate/BTCUSDT"].rows) == [
        f for f in funding if f.calc_time < horizon]


def test_a_series_whose_archive_skips_whole_months_is_read_run_by_run(rows):
    """FTTUSDT's archive pins no file from 2022-12 to 2023-08, so one load across its
    span fails; given as two runs, each is read alone, and the missing days end the
    first instrument (section 4). A gap inside a pinned month is crossed and recorded
    (section 5, Gaps)."""
    name = "spot/klines/CCCUSDT/1d"
    fifteen = "spot/klines/CCCUSDT/15m"
    daily = rows[name]
    # The manifest here pins no February file: the first run ends on 01-31 and the
    # second starts on 03-01. The loader reports one recorded gap in January.
    gap = SimpleNamespace(after=daily[2].open_time, before=daily[3].open_time, missing=0)
    unpinned = {(n, "2021-02") for n in (name, fifteen)}
    first_run = Span(daily[0].open_time, sim_synthetic.ms(2021, 1, 31))
    second_run = Span(sim_synthetic.ms(2021, 3, 1), daily[-1].open_time)
    one_run = FakeView(rows, unpinned=unpinned, gaps={name: [gap]})
    with pytest.raises(LookupError):
        Market.from_view(one_run, [name], sim_synthetic.ms(2021, 4, 1), history_from=FIRST)
    coverage = {
        name: [(first_run.first_open, first_run.last_open),
               (second_run.first_open, second_run.last_open)],
        fifteen: [(rows[fifteen][0].open_time, sim_synthetic.ms(2021, 1, 31, 23)),
                  (sim_synthetic.ms(2021, 3, 1), rows[fifteen][-1].open_time)],
    }
    view = FakeView(rows, unpinned=unpinned, gaps={name: [gap]}, coverage=coverage)
    horizon = sim_synthetic.ms(2021, 4, 1)
    market = Market.from_view(view, [name, fifteen], horizon, history_from=FIRST)
    assert len(view.calls) == 2 and all(c[3] is True for c in view.calls)
    series = market.series[name]
    assert series.rows[-1].open_time == horizon - sim.DAY_MS
    assert not any(sim_synthetic.ms(2021, 2, 1) <= t < sim_synthetic.ms(2021, 3, 1)
                   for t in series.times)
    first = sim.Instrument("CCCUSDT", daily[0].open_time)
    assert series.instrument_end(first) == sim_synthetic.ms(2021, 2, 1)
    assert market.crossed_gaps() == {name: [(gap.after, gap.before, 0)]}
    # A fill search in the unpinned month reads nothing and does not fail.
    assert market.fills.bars(fifteen, sim_synthetic.ms(2021, 2, 10),
                             sim_synthetic.ms(2021, 2, 11)) == []
    with pytest.raises(SimulationError, match="time order"):
        sim.runs_of((second_run, first_run))


def test_a_load_starting_after_a_series_first_row_is_refused(rows):
    """Quant review 1, finding 5. Loading from later than a series' first archived row
    would start its instrument late, so D13's close 120 days before a month end and
    section 6's volatility over every return would both come out wrong."""
    name = "spot/klines/AAAUSDT/1d"
    horizon = sim_synthetic.ms(2021, 3, 1)
    with pytest.raises(SimulationError, match="D13"):
        Market.from_view(FakeView(rows), [name], horizon, history_from=FIRST + 10 * sim.DAY_MS)
    view = FakeView(rows)
    Market.from_view(view, [name], horizon, history_from=FIRST - sim.DAY_MS)
    assert view.calls[0][1] == datetime(2021, 1, 1, tzinfo=UTC)  # from its first row


def test_a_span_that_starts_before_the_archive_is_refused(rows):
    name = "spot/klines/AAAUSDT/1d"
    early = [(rows[name][0].open_time - 5 * sim.DAY_MS, rows[name][-1].open_time)]
    with pytest.raises(SimulationError, match="first row"):
        Market.from_view(FakeView(rows, coverage={name: early}), [name],
                         sim_synthetic.ms(2021, 3, 1))


def test_the_market_must_be_read_to_exactly_the_window_end(rows):
    """Finding 5: a horizon other than the window's end would move every D12 close."""
    run_window = window(days=10)
    for horizon in (run_window.end - sim.DAY_MS, run_window.end + sim.DAY_MS):
        with pytest.raises(SimulationError, match="window's end"):
            backtest(TrendExample(["AAAUSDT"]), Market.from_rows(rows, horizon), run_window,
                     COSTS)


def test_an_exchange_wide_spot_outage_stops_the_run(rows):
    """Quant review 1, finding 7. Section 5 carries closes forward through an
    exchange-wide outage while section 4 would end every instrument at its low; when
    BTC and ETH spot both stop at one instant the run stops instead of choosing."""
    outage = sim_synthetic.ms(2021, 2, 20)
    btc = "spot/klines/BTCUSDT/1d"
    cut = dict(rows, **{"spot/klines/ETHUSDT/1d": list(rows[btc])})
    run_window = window(days=30)
    for name in (btc, "spot/klines/ETHUSDT/1d"):
        cut[name] = [r for r in cut[name] if r.open_time != outage]
        strategy = TrendExample(["AAAUSDT"])
        if name == btc:  # one major missing a day is that pair's own gap: no outage
            backtest(strategy, Market.from_rows(cut, run_window.end), run_window, COSTS)
            continue
        with pytest.raises(SimulationError, match="exchange-wide outage"):
            backtest(strategy, Market.from_rows(cut, run_window.end), run_window, COSTS)
        with pytest.raises(SimulationError, match="exchange-wide outage"):
            paper_run(strategy, cut, run_window, COSTS)


def test_paper_refuses_events_out_of_order(rows):
    run_window = window(days=3)
    paper = sim.Paper(Scripted(), run_window, COSTS)
    late = Kline(run_window.start, 1, 1, 1, 1, 1, run_window.start + 899_999, 1, 1, 1, 1)
    paper.push(sim.BarEvent("spot/klines/AAAUSDT/15m", late))
    with pytest.raises(SimulationError, match="arrived after"):
        paper.push(sim.Tick(run_window.start))
