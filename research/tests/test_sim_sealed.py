"""A strategy uses the engine and never changes it (security review of PR 61, round 2).

Every variant of the review's probe (probe_pr61_sources_r2.py) is refused, by the source
check before anything runs, by the sealed engine in the real runner process, or by
both. The source check reads names, not values, so it cannot follow an engine module
or class through an alias, a container, a parameter, a ``for`` target or a walrus, nor
see a mutating method on an engine table; those are run, with the check out of the way,
in the runner's fresh interpreter, where the sealed engine fails the run (``runner.
seal_engine``). A read, ``eval``, ``__import__``, ``vars(module)[...]`` and
``object.__setattr__`` get past a run-time seal, so for those the source check is the
refusal, as the review intends: it refuses every reference to a forbidden builtin and
every dunder attribute, which is also all that could undo the seal.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from norse_research.harness import _RUNNER
from norse_research.runner import RESULT_PREFIX
from norse_research.sources import disallowed_imports

PACKAGE = Path(__file__).resolve().parent.parent / "norse_research"
SEALED = re.compile(
    r"sealed for this run|'mappingproxy' object|cannot assign to field"
    r"|vars\(\) argument must have __dict__"  # vars() rewords the seal's AttributeError
)

EXECUTION = "from norse_research.sim import execution\n"
MODEL = "from norse_research.sim import model\n"

# name: (source, refused by the source check, refused by the sealed engine at run time)
VARIANTS = {
    # Round 3, re-run.
    "E1 a module attribute": (EXECUTION + "execution._apply = None\n", True, True),
    "E1 a class attribute": (MODEL + "model.Costs.total = 0.0\n", True, True),
    "E2 the package root": ("import norse_research.sim\n", True, False),
    "E3 the bare root": (MODEL + "norse_research.store\n", True, False),
    # Aliases the source check cannot follow.
    "A1 by assignment": (EXECUTION + "m = execution\nm._apply = None\n", False, True),
    "A2 through a container": (EXECUTION + "d = [execution]\nd[0]._apply = None\n", False, True),
    "A3 as a parameter": (EXECUTION + "def f(x):\n    x._apply = None\n\n\nf(execution)\n",
                          False, True),
    "A4 as a for target": (EXECUTION + "for m in (execution,):\n    m._apply = None\n",
                           False, True),
    "A5 by walrus": (EXECUTION + "(m := execution)._apply = None\n", False, True),
    "A6 del through an alias": (MODEL + "m = model.Costs\ndel m.total\n", False, True),
    "A7 a class attribute through an alias": (MODEL + "C = model.Costs\nC.total = 0.0\n",
                                              False, True),
    # No alias needed.
    "M1 a mutating method on an engine table":
        (MODEL + "model.DECISION_INTERVALS.update({'1d': 1})\n", False, True),
    "M2 the same through a submodule import":
        ("from norse_research.sim.model import DECISION_INTERVALS\nDECISION_INTERVALS.clear()\n",
         True, True),
    "M3 a dynamic subclass": ("from norse_research.sim import book\n"
                              "B = type('B', (book.Book,), {})\n", True, False),
    # Builtins reached without calling them by name.
    "B1 setattr aliased": (EXECUTION + "s = setattr\ns(execution, '_apply', None)\n", True, True),
    "B2 getattr aliased": (EXECUTION + "g = getattr\ng(execution, '_apply')\n", True, False),
    "B3 vars aliased, then an item store":
        (EXECUTION + "v = vars\nv(execution)['_apply'] = None\n", True, True),
    "B4 setattr passed to map":
        (EXECUTION + "list(map(setattr, [execution], ['_apply'], [None]))\n", True, True),
    "B5 setattr through functools.partial":
        ("import functools\n" + EXECUTION
         + "functools.partial(setattr, execution)('_apply', None)\n", True, True),
    "B6 __import__ aliased": ("imp = __import__\nimp('o' + 's').getcwd\n", True, False),
    "B7 eval aliased": ("e = eval\ne('1')\n", True, False),
    # Already refused before this round.
    "C1 vars called": (EXECUTION + "vars(execution)\n", True, True),
    "C2 __dict__": (EXECUTION + "execution.__dict__\n", True, True),
    "C3 object.__setattr__": (EXECUTION + "object.__setattr__(execution, '_apply', None)\n",
                              True, False),
    "C4 importlib": ("import importlib\n", True, False),
    "C5 setattr called": (EXECUTION + "setattr(execution, 'x', 1)\n", True, True),
    # The data formats module, sealed with the engine.
    "F1 an item store on a formats table":
        ("from norse_research.formats import INTERVAL_MS\nINTERVAL_MS['1d'] = 1\n", False, True),
    "F2 a formats module attribute through an alias":
        ("from norse_research import formats\nf = formats\nf.LICENCE = 'mine'\n", False, True),
    "F3 a row type's field through an alias":
        ("from norse_research.formats import Kline\nK = Kline\n"
         "K.close = property(lambda row: row[1])\n", False, True),
    "F4 unsealing through an alias":
        ("from norse_research import formats\nf = formats\nf._engine_sealed = False\n",
         False, True),
    "F5 a row type's base through mro":
        ("from norse_research.formats import Kline\nB = Kline.mro()[1]\n"
         "B.close = property(lambda row: row[1])\n", True, False),
    "F6 an engine value object": ("from norse_research.sim import Costs\nc = Costs(10, 2)\n"
                                  "c.fee_bps = 0\n", False, True),
    # Found while applying the review: helpers in the allowlisted standard library that
    # take an attribute or module name as a string and write into what they find. Each
    # replaced the fill price (section 5) in the runner before this change.
    "S1 functools.update_wrapper into a module":
        ("import functools\nfrom norse_research.sim import market\n\n\nclass Fake:\n    pass\n\n\n"
         "fake = Fake()\nfake.vwap = lambda bar: 1.0\n"
         "functools.update_wrapper(market, fake, assigned=())\n", True, True),
    "S2 functools.wraps into a module":
        ("import functools\nfrom norse_research.sim import market\n\n\nclass Fake:\n    pass\n\n\n"
         "fake = Fake()\nfake.vwap = lambda bar: 1.0\nfunctools.wraps(fake, assigned=())(market)\n",
         True, True),
    "S3 enum.global_enum into a module's namespace":
        ("import enum\n\n\nclass Price(enum.Enum):\n"
         "    __module__ = 'norse_research.sim.market'\n    vwap = 3\n\n"
         "    def __call__(self, bar):\n        return 3.0\n\n\nenum.global_enum(Price)\n",
         True, True),
    "S4 functools.update_wrapper into a function's globals":
        ("import functools\nfrom norse_research.sim import market\n\n\nclass Fake:\n"
         "    __globals__ = {'vwap': lambda bar: 4.0}\n\n\n"
         "functools.update_wrapper(market.vwap, Fake(), assigned=(), updated=('__globals__',))\n",
         True, False),
}

HEAD = 'CONFIGURATIONS = ["EXAMPLE"]\nPARAMETERS = []\nDEFAULTS = {}\n\n'
PROBE_RUN = '\n\ndef run(configuration, data_view):\n    return [("2021-03-01", 0.0)]\n'

# Legitimate use: constructing engine objects, calling engine functions, reading its
# tables and changing only what the strategy owns.
USES_THE_ENGINE = '''
from norse_research.formats import INTERVAL_MS, Kline
from norse_research.sim import (
    Costs,
    Decision,
    Instrument,
    default_rng,
    iso_day,
    model,
    spot_series,
)


class Mine:
    def __init__(self):
        self.seen = []


class Strategy:
    interval = "1d"

    def __init__(self):
        self.state = {}

    def decide(self, history, book):
        return None


def run(configuration, data_view):
    mine, strategy = Mine(), Strategy()
    strategy.state["costs"] = Costs(10, 2)
    strategy.interval = "4h"
    table = dict(model.DECISION_INTERVALS)
    table["1w"] = 7 * table["1d"]
    row = Kline(0, 1.0, 1.0, 1.0, 1.0, 2.0, 899_999, 2.0, 1, 1.0, 1.0)
    decision = Decision(weights={Instrument("BTCUSDT", 0): 0.5}, band=0.25)
    mine.seen += [spot_series("BTCUSDT", "1d"), iso_day(0), row._replace(close=2.0).close,
                  default_rng(1).random(), INTERVAL_MS["15m"], len(decision.weights)]
    return [(iso_day(0), float(len(mine.seen)) + table["1w"] / 7 / 86_400_000)]
'''


@pytest.fixture(scope="module")
def code_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("sealed") / "code"
    shutil.copytree(PACKAGE, root / "research" / "norse_research",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(Path(__file__).parent / "sim_example.py",
                root / "research" / "norse_research" / "strategies" / "example.py")
    return root


def run_in_runner(code_root: Path, tmp: Path, source: str, configuration=None,
                  function: str = "run") -> dict:
    """``source`` as a pinned strategy module, run by the real runner in a fresh
    interpreter exactly as the harness starts it; no source check runs here."""
    module = code_root / "research" / "norse_research" / "strategies" / f"probe_{tmp.name}.py"
    module.write_text(source)
    pycache = tmp / "pycache"
    pycache.mkdir()
    payload = {
        "code_root": str(code_root),
        "pycache": str(pycache),
        "module_path": str(module.relative_to(code_root)),
        "module_name": f"norse_research.strategies.{module.stem}",
        "function": function,
        "configuration": configuration or {"window": {"first_return_day": "2021-03-01",
                                                      "days": 1}},
        "entry": "e" * 64,
        "manifest": "research/manifests/none.json",
        "store_root": None,
    }
    proc = subprocess.run(
        [sys.executable, "-I", "-B", "-X", f"pycache_prefix={pycache}", "-c", _RUNNER,
         str(code_root / "research"), str(code_root)],
        input=json.dumps(payload).encode(), capture_output=True, env={}, cwd=tmp, check=False,
    )
    lines = [line for line in proc.stdout.decode().splitlines() if line.startswith(RESULT_PREFIX)]
    assert lines, proc.stderr.decode()[-2000:]
    return json.loads(lines[-1][len(RESULT_PREFIX):])


def test_every_probe_variant_is_refused_by_the_source_check_or_the_sealed_engine():
    for name, (source, by_source, by_seal) in VARIANTS.items():
        assert by_source or by_seal, name
        problems = disallowed_imports(source, "strategies.h1.probe", False, ["strategies/h1"])
        if by_source:
            assert problems, f"{name}: the source check accepted it"


@pytest.mark.parametrize("name", [n for n, (_, _, by_seal) in VARIANTS.items() if by_seal])
def test_a_variant_that_changes_the_engine_fails_its_run_in_the_runner(name, code_root, tmp_path):
    source, _, _ = VARIANTS[name]
    result = run_in_runner(code_root, tmp_path, HEAD + source + PROBE_RUN)
    assert result["ok"] is False, name
    assert SEALED.search(result["error"]), result["error"]


def test_a_strategy_still_constructs_and_calls_the_engine_in_the_runner(code_root, tmp_path):
    source = HEAD + USES_THE_ENGINE
    assert disallowed_imports(source, "strategies.h1.uses", False, ["strategies/h1"]) == []
    result = run_in_runner(code_root, tmp_path, source)
    assert result["ok"], result
    assert result["returns"] == [["1970-01-01", 7.0]]


def test_the_runner_seals_every_engine_module_and_formats(code_root, tmp_path):
    """The runner imports the whole engine before sealing it, so no engine module is
    left unsealed, and a later import cannot add one."""
    source = HEAD + (
        "from norse_research import formats, sim\n"
        "from norse_research.sim import (\n"
        "    book, drivers, execution, history, market, model, pcg64, targeting,\n"
        ")\n\n"
        "MODULES = (sim, book, drivers, execution, history, market, model, pcg64, targeting,"
        " formats)\n\n\n"
        "def run(configuration, data_view):\n"
        "    for i, module in enumerate(MODULES):\n"
        "        try:\n"
        "            module.added_by_the_strategy = 1\n"
        "        except AttributeError:\n"
        "            continue\n"
        "        raise AssertionError(f'module {i} is not sealed')\n"
        "    return [(\"2021-03-01\", float(len(MODULES)))]\n"
    )
    result = run_in_runner(code_root, tmp_path, source)
    assert result["ok"], result
    assert result["returns"] == [["2021-03-01", 10.0]]
    engine = sorted(p.stem for p in (PACKAGE / "sim").glob("*.py") if p.stem != "__init__")
    assert engine == ["book", "drivers", "execution", "history", "market", "model", "pcg64",
                      "targeting"]


# --- the standard library the engine calls ------------------------------------------------

STDLIB = ("bisect", "heapq", "itertools", "math", "io", "zipfile")


def engine_stdlib_names() -> dict[str, list[str]]:
    """Every function the engine and ``formats`` bind from the standard library modules
    a strategy may reach, read from their own imports."""
    names: dict[str, list[str]] = {}
    for path in [*sorted((PACKAGE / "sim").glob("*.py")), PACKAGE / "formats.py"]:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module in STDLIB[:4]:
                names.setdefault(node.module, [])
                names[node.module] += [a.name for a in node.names if a.name not in names[node.module]]
    return names


def test_the_engine_holds_no_standard_library_module_a_strategy_could_rebind():
    """Security review of PR 61, the residual stated in round 2: the engine called
    ``bisect.bisect_left`` and the like through the module, so a strategy that bound
    ``bisect`` to a second name and replaced a function in it changed the engine's
    lookups. Every engine and ``formats`` module now binds what it calls at import."""
    for path in [*sorted((PACKAGE / "sim").glob("*.py")), PACKAGE / "formats.py"]:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert not [a.name for a in node.names if a.name in STDLIB], path.name
    names = engine_stdlib_names()
    assert set(names) == {"bisect", "heapq", "itertools", "math"}
    assert "bisect_left" in names["bisect"] and "sqrt" in names["math"]


def test_rebinding_bisect_no_longer_changes_series_view_index_of(code_root, tmp_path):
    source = HEAD + (
        "import bisect\n\nfrom norse_research.sim import SeriesView\n\n"
        "b = bisect\nb.bisect_left = lambda *args, **kwargs: 0\n\n\n"
        "def run(configuration, data_view):\n"
        "    view = SeriesView('x', [10, 20, 30], [1, 2, 3], 3)\n"
        "    return [('2021-03-01', float(view.index_of(3)))]\n"
    )
    result = run_in_runner(code_root, tmp_path, source)
    assert result["ok"], result
    assert result["returns"] == [["2021-03-01", 2.0]]  # before this change: None, a miss


def test_a_backtest_ignores_every_rebound_standard_library_function(code_root, tmp_path):
    """Every function the engine binds from ``bisect``, ``heapq``, ``itertools`` and
    ``math`` is replaced in its module by one that fails, before the golden fixture's
    scenario runs: the returns are still the golden ones."""
    from test_sim_engine import RUNNER_STRATEGY

    rebind = (
        "import bisect, heapq, itertools, math  # noqa\n\n\n"
        "def _rebound(*args, **kwargs):\n"
        "    raise AssertionError('the engine called a rebound standard library function')\n\n\n"
        "_MODULES = {'bisect': bisect, 'heapq': heapq, 'itertools': itertools, 'math': math}\n"
        f"for _module, _names in {engine_stdlib_names()!r}.items():\n"
        "    for _name in _names:\n"
        "        setattr(_MODULES[_module], _name, _rebound)\n"
    )
    configuration = {
        "window": {"first_return_day": "2021-03-01", "last_return_day": "2021-03-03", "days": 3},
        "costs": {"fee_bps_per_side": 10, "slippage_bps_per_side": 2},
    }
    result = run_in_runner(code_root, tmp_path, rebind + RUNNER_STRATEGY, configuration)
    assert result["ok"], result
    golden = json.loads((Path(__file__).parent / "fixtures" / "sim-golden.json").read_text())
    for (_, got), want in zip(result["returns"], golden["weights"]["expected"]["returns"],
                              strict=True):
        assert got == pytest.approx(want, rel=1e-12)


# --- what a strategy is lent --------------------------------------------------------------

TAMPERING = '''

from norse_research.sim import Costs, Window, backtest, paper_run


class Tamper:
    def reach(self):
        self._book.cash = 1e9


class Reach:
    """``view._book``-style routes, each through a method called through its class, so
    that ``self`` is whatever the strategy was lent."""

    def book_spot(self):
        self._book.spot["AAAUSDT"] = 1e9

    def spot(self):
        self._spot["AAAUSDT"] = 1e9

    def perp(self):
        self._perp.clear()

    def carry(self):
        self._carry.add("AAAUSDT")

    def marks(self):
        self._marks[("spot", "AAAUSDT")] = 0.0

    def cash(self):
        self.cash = 1e9

    def history_now(self):
        self.now = 0

    def history_cache(self):
        self._cache.clear()

    def history_views(self):
        self._views = None

    def view_count(self):
        self._count = 10 ** 9

    def view_lookup(self):
        self.index_of = lambda time: 0

    def view_row_ahead(self):
        return self._row(10 ** 6)


VIEW = (Tamper.reach, Reach.book_spot, Reach.spot, Reach.perp, Reach.carry, Reach.marks,
        Reach.cash)
HISTORY = (Reach.history_now, Reach.history_cache, Reach.history_views)


class Tampering(Scripted):
    """Decides exactly as ``Scripted`` does, then tries every route to the engine
    through what it was lent, ending with every slot of the view rewritten."""

    routes = "view"
    attempts = 0
    market = None

    def decide(self, history, book):
        decision = super().decide(history, book)
        if Tampering.routes == "view":
            attempts = [(a, book) for a in VIEW]
        elif Tampering.routes == "history":
            attempts = [(a, history) for a in HISTORY]
        elif Tampering.routes == "series":
            attempts = []
            for name in ("spot/klines/AAAUSDT/1d", "spot/klines/BBBUSDT/1d"):
                view = history.series(name)
                known = len(view)
                for attempt in (Reach.view_count, Reach.view_lookup):
                    try:
                        attempt(view)
                    except Exception:
                        pass
                    Tampering.attempts += 1
                # The next daily bar opens now and closes a day later: not yet known.
                for ahead in (lambda v=view: Reach.view_row_ahead(v),
                              lambda v=view, k=known: v[k],
                              lambda v=view: v.at(HORIZON - D)):
                    try:
                        row = ahead()
                    except (IndexError, AttributeError):
                        continue
                    if row is not None and row.open_time >= history.now:
                        raise AssertionError(f"read a row not yet known: {row}")
        else:
            attempts = market_attempts(Tampering.market) if Tampering.market else []
        for attempt, lent in attempts:
            try:
                attempt(lent)
            except Exception:
                pass
            Tampering.attempts += 1
        if Tampering.routes == "view":
            for slot in getattr(type(book), "__slots__", ()):
                object.__setattr__(book, slot, None)
        return decision


def market_attempts(market):
    """Everything public on the market a strategy built and passed to the engine."""
    attempts = [(lambda m: setattr(m, "horizon", 0), market),
                (lambda m: setattr(m, "series", {}), market),
                (lambda m: setattr(m, "fills", None), market),
                (lambda m: m.series.clear(), market),
                (lambda m: setattr(m.fills, "_series", {}), market)]
    for series in market.series.values():
        attempts += [(lambda s: setattr(s, "rows", ()), series),
                     (lambda s: setattr(s, "times", ()), series),
                     (lambda s: setattr(s, "horizon", 0), series),
                     (lambda s: s.segments.add(0, 0), series),
                     (lambda s: s.rows.append(None), series)]
    return attempts


def run_tampered(configuration, data_view):
    Tampering.routes = configuration["routes"]
    window = Window.from_configuration(configuration)
    costs = Costs.from_configuration(configuration)
    for driver in ("backtest", "paper"):
        runs = []
        for kind in (Scripted, Tampering):
            if driver == "backtest":
                market = Market.from_rows(golden_rows(), HORIZON)
                Tampering.market = market
                runs.append(backtest(golden_strategy(kind), market, window, costs))
            else:
                Tampering.market = None
                runs.append(paper_run(golden_strategy(kind), golden_rows(), window, costs))
        clean, tampered = runs
        for name in ("orders", "fills", "lapses", "accruals", "marks", "skips", "returns"):
            if getattr(clean, name) != getattr(tampered, name):
                raise AssertionError(f"{driver}: tampering changed the run's {name}")
    if Tampering.attempts == 0:
        raise AssertionError("no decision was taken, so nothing was tried")
    return tampered.returns
'''


@pytest.mark.parametrize("routes", ["view", "history", "series", "market"])
def test_nothing_a_strategy_does_to_what_it_is_lent_changes_the_run(routes, code_root, tmp_path):
    """Security review of PR 61, the route stated after round 2: ``Tamper.reach(view)``,
    a method called through its class with the view as ``self``, reached the live book
    through ``self._book``. A ``BookView`` is now a snapshot holding immutable values
    and no reference to the book, and a strategy gets its own history while the engine
    keeps the one it builds the orders from. Every route below, the exact probe among
    them, leaves the orders, fills, lapses, accruals, marks, skips and returns of both
    drivers as they are without it, and the returns are the golden ones. The sweep adds
    the series views a history lends (a larger ``_count``, replaced lookups, and every
    way to read a row not yet known) and everything public on the market the strategy
    built and passed to the engine."""
    from test_sim_engine import RUNNER_STRATEGY

    configuration = {
        "window": {"first_return_day": "2021-03-01", "last_return_day": "2021-03-03", "days": 3},
        "costs": {"fee_bps_per_side": 10, "slippage_bps_per_side": 2},
        "routes": routes,
    }
    result = run_in_runner(code_root, tmp_path, RUNNER_STRATEGY + TAMPERING, configuration,
                           function="run_tampered")
    assert result["ok"], result
    golden = json.loads((Path(__file__).parent / "fixtures" / "sim-golden.json").read_text())
    for (_, got), want in zip(result["returns"], golden["weights"]["expected"]["returns"],
                              strict=True):
        assert got == pytest.approx(want, rel=1e-12)


def test_a_book_view_holds_no_reference_to_the_live_book():
    from norse_research.sim import Book, BookView, Instrument

    book = Book(cash=0.25)
    book.spot["AAAUSDT"] = 2.0
    book.trade("perp", "BTCUSDT", -1, 0.5, 100.0, 0.0)
    book.carry["BTCUSDT"] = "BTCUSDT"
    marks = {("spot", "AAAUSDT"): 10.0}
    view = BookView.of(book, marks, 20.25)
    assert (view.cash, view.equity, view.held()) == (0.25, 20.25, ["AAAUSDT"])
    assert view.quantity("spot", "AAAUSDT") == 2.0 and view.quantity("perp", "BTCUSDT") == -0.5
    assert view.weight("AAAUSDT") == 2.0 * 10.0 / 20.25 and view.in_carry("BTCUSDT")
    book.cash, book.spot["AAAUSDT"], marks[("spot", "AAAUSDT")] = 9.0, 7.0, 99.0
    assert (view.cash, view.quantity("spot", "AAAUSDT"), view.weight("AAAUSDT")) == (
        0.25, 2.0, 2.0 * 10.0 / 20.25)
    values = [view.cash, view.equity, view._spot, view._perp, view._carry, view._marks]
    assert not any(v is book or v is book.spot or v is book.perp or v is book.carry
                   or v is marks for v in values)
    assert {type(v).__name__ for v in values} == {"float", "mappingproxy", "frozenset"}
    assert Instrument("X", 0) not in view._carry  # a frozenset, not a live dict


# --- security review of PR 61, round 4: its probes, as refusing tests -----------------

FRAMES = """

def caller_frame(depth):
    box = []

    def step():
        yield box[0].gi_frame.f_back

    box.append(step())
    frame = next(box[0])
    for _ in range(depth):
        frame = frame.f_back
    return frame


class Wrapped:
    def __init__(self, *args, **kwargs):
        self.inner = Scripted(*args, **kwargs)
        self.interval = self.inner.interval
"""

EVIL_RUN = """

def run_evil(configuration, data_view):
    return run_backtest(golden_strategy(Evil), configuration,
                        Market.from_rows(golden_rows(), HORIZON))
"""

FILLED = "\n\ndef run_evil(configuration, data_view):\n    return run(configuration, data_view)\n"

GOLDEN_TO_EVIL = """

def run_evil(configuration, data_view):
    if LEAK != b"HOLDOUT-STAND-IN":
        raise AssertionError("not read")
    return [("2021-03-01", 99.0), ("2021-03-02", 99.0), ("2021-03-03", 99.0)]
"""

SEALED_OUT = "StrategyIOBlocked"
# name: (source, refused by the source check, the refusal the runner gives, or None if
# only the source check refuses it)
ROUND_4 = {
    "E1 formats.build on a sealed engine module": (
        "from norse_research.formats import build\nfrom norse_research.sim import execution\n\n"
        "build(execution, vwap=lambda bar: 1.0)\n" + FILLED,
        False, "build gives attributes to the engine's own frozen objects only"),
    "E2 formats.build on an engine value object": (
        "from norse_research.formats import build\nfrom norse_research.sim import Costs, Window,"
        " backtest\n" + """

def run_evil(configuration, data_view):
    costs = Costs.from_configuration(configuration)
    build(costs, fee_bps=0.0)
    return backtest(golden_strategy(), Market.from_rows(golden_rows(), HORIZON),
                    Window.from_configuration(configuration), costs).returns
""", False, "build gives attributes to the engine's own frozen objects only"),
    "F1 frames: the engine's book": (FRAMES + """

class Evil(Wrapped):
    def decide(self, history, book):
        caller_frame(2).f_locals["self"].book.cash += 0.25
        return self.inner.decide(history, book)
""" + EVIL_RUN, True, SEALED_OUT),
    "F2 frames: the engine's costs": (FRAMES + """
from norse_research.sim import Costs


class Evil(Wrapped):
    def decide(self, history, book):
        caller_frame(2).f_locals["self"].costs = Costs(0, 0)
        return self.inner.decide(history, book)
""" + EVIL_RUN, True, SEALED_OUT),
    "F3 frames: a local of the engine's frame": (FRAMES + """

class Evil(Wrapped):
    def decide(self, history, book):
        locals_ = caller_frame(2).f_locals
        locals_["equity"] = locals_["equity"] * 1.5
        return self.inner.decide(history, book)
""" + EVIL_RUN, True, SEALED_OUT),
    "B1 a frame's builtins: the fill price": (
        "from norse_research.sim import execution\n\nB = (lambda: (yield))().gi_frame.f_builtins\n"
        "B['getattr'](execution.execute_bar, '__globals__')['vwap'] = lambda bar: 1.0\n" + FILLED,
        True, SEALED_OUT),
    "B2 a frame's builtins: the engine unsealed": (
        "from norse_research import formats\nfrom norse_research.sim import model\n\n"
        "B = (lambda: (yield))().gi_frame.f_builtins\n"
        "B['getattr'](formats.build, '__globals__')['_engine_sealed'] = False\n" + FILLED,
        True, SEALED_OUT),
    "B3 enum.bltns: the fill price": (
        "import enum\n\nfrom norse_research.sim import execution\n\n"
        "enum.bltns.getattr(execution.execute_bar, '__globals__')['vwap'] = lambda bar: 1.0\n"
        + FILLED, True, None),
    "B5 the garbage collector: the tripwire's own file set": (
        "import enum\n\nSTANDIN = '@STANDIN@'\n_b = enum.bltns\n_gc = _b.eval(\"__import__('gc')\")\n"
        "for _f in _gc.get_objects():\n"
        "    if _b.getattr(_f, '__qualname__', '') == 'install_tripwire.<locals>.allowed':\n"
        "        for _cell in _b.getattr(_f, '__closure__'):\n"
        "            if _b.isinstance(_cell.cell_contents, set):\n"
        "                _cell.cell_contents.add(STANDIN)\n"
        "LEAK = _b.open(STANDIN, 'rb').read()\n" + GOLDEN_TO_EVIL, True, SEALED_OUT),
    "T1 typing.get_type_hints: a string annotation evaluated": (
        "import typing\n\nfrom norse_research.sim import execution\n\n\nclass Note:\n"
        "    hint: \"getattr(execution.execute_bar, '__globals__').__setitem__('vwap', "
        "lambda b: 1.0)\"\n\n\ntry:\n    typing.get_type_hints(Note)\nexcept TypeError:\n"
        "    pass\n" + FILLED, True, None),
    "T2 functools.singledispatch: a string annotation evaluated": (
        "import functools\n\nfrom norse_research.sim import execution\n\n\n"
        "@functools.singledispatch\ndef describe(x):\n    return x\n\n\n@describe.register\n"
        "def _(x: \"getattr(execution.execute_bar, '__globals__').__setitem__('vwap', "
        "lambda b: 1.0)\"):\n    return x\n" + FILLED, True, None),
    "M1 frames at module level: the runner's payload": (FRAMES + """

_runner = caller_frame(2)
_runner.f_locals["payload"]["configuration"]["costs"]["fee_bps_per_side"] = 0
""" + FILLED, True, SEALED_OUT),
    "M2 frames: the tripwire's file set, then open()": (FRAMES + """

STANDIN = "@STANDIN@"
B = (lambda: (yield))().gi_frame.f_builtins
caller_frame(2).f_locals["files"].add(STANDIN)
LEAK = B["open"](STANDIN, "rb").read()
""" + GOLDEN_TO_EVIL, True, SEALED_OUT),
    "A1 a subclass of an engine class through an alias": (
        "from norse_research.sim import Costs, Window, backtest\n\nK = Costs\n\n\nclass Free(K):\n"
        "    def fee(self, quantity, price):\n        return 0.0\n" + """

def run_evil(configuration, data_view):
    return backtest(golden_strategy(), Market.from_rows(golden_rows(), HORIZON),
                    Window.from_configuration(configuration), Free(10, 2)).returns
""", False, "subclassed only by the engine"),
    "A2 __build_class__ builds a subclass of an engine class": (
        "from norse_research.sim import Costs\n\n\ndef body():\n    pass\n\n\n"
        "Sub = __build_class__(body, 'Sub', Costs)\n" + FILLED, True, "subclassed only by the engine"),
    "Z1 formats.ZipFile, the loader's parser": (
        "from norse_research import formats\n\nZ = formats.ZipFile\n" + FILLED,
        False, "has no attribute 'ZipFile'"),
}


def round_4_source(name: str, standin: Path) -> str:
    from test_sim_engine import RUNNER_STRATEGY

    source, _, _ = ROUND_4[name]
    return RUNNER_STRATEGY + source.replace("@STANDIN@", str(standin))


@pytest.mark.parametrize("name", sorted(ROUND_4))
def test_a_round_4_probe_is_refused(name, code_root, tmp_path):
    """Each route of the review, confirmed in the runner at 1d02908, is refused: by the
    source check before anything runs, by the runner as it runs, or both (``build``'s
    target check, the audit hook on frames and the garbage collector, the metaclass on
    subclasses, the private name of the loader's zip reader)."""
    standin = tmp_path / "standin.bin"
    standin.write_bytes(b"HOLDOUT-STAND-IN")
    source = round_4_source(name, standin)
    _, by_source, refusal = ROUND_4[name]
    problems = disallowed_imports(source, "norse_research.strategies.probe", False,
                                  ["research/norse_research/strategies"])
    if by_source:
        assert problems, f"{name}: the source check accepted it"
    if refusal is not None:
        configuration = {
            "window": {"first_return_day": "2021-03-01", "last_return_day": "2021-03-03",
                       "days": 3},
            "costs": {"fee_bps_per_side": 10, "slippage_bps_per_side": 2},
        }
        result = run_in_runner(code_root, tmp_path, source, configuration, function="run_evil")
        assert result["ok"] is False, f"{name}: ran, {result.get('returns')}"
        assert refusal in result["error"], result["error"]


# --- security review round 5 (main b656d5f): the dataclass compile route --------------

# Text the source check never parses becomes part of the __eq__ that dataclass compiles
# from each field name, in the strategy module's own globals (B-H1).
GADGET = """

class Probe:
    k: int = 0
    __annotations__["k or (@CODE@) or self.k"] = int


Probe = @DATACLASS@(init=False, repr=False)(Probe)
Probe() == Probe()
"""
VWAP_CODE = "getattr(execution.execute_bar, '__globals__').__setitem__('vwap', lambda b: 1.0)"
FRAME_CODE = ("[f.f_locals['files'].add('@STANDIN@') for f in "
              "[__import__('sys')._getframe(i) for i in range(1, 5)] "
              "if 'files' in f.f_locals and 'payload' in f.f_locals]")
OPEN_CODE = "LEAK.append(open('@STANDIN@', 'rb').read())"
BOOK_CODE = ("[f.f_locals['self'].book.__setattr__('cash', f.f_locals['self'].book.cash + 0.25) "
             "for f in [__import__('sys')._getframe(i) for i in range(1, 8)] "
             "if type(f.f_locals.get('self')).__name__ == '_Run'][:1]")


def gadget(code: str, decorator: str) -> str:
    return GADGET.replace("@CODE@", code).replace("@DATACLASS@", decorator)


def d4(source_of_dataclass: str) -> str:
    """D4: from decide, the engine's live run reached through sys._getframe."""
    return source_of_dataclass + """

class Box:
    k: int = 0
    __annotations__["k or (""" + BOOK_CODE + """) and 0 or self.k"] = int


Box = dataclass(init=False, repr=False)(Box)


class Wrapped:
    def __init__(self, *args, **kwargs):
        self.inner = Scripted(*args, **kwargs)
        self.interval = self.inner.interval

    def decide(self, history, book):
        Box() == Box()
        return self.inner.decide(history, book)


def run_evil(configuration, data_view):
    return run_backtest(golden_strategy(Wrapped), configuration,
                        Market.from_rows(golden_rows(), HORIZON))
"""


ENGINE_DATACLASS = "from norse_research.formats import dataclass\n"
STDLIB_DATACLASS = "from dataclasses import dataclass\n"
EXECUTION = "from norse_research.sim import execution\n"
COMPILE_REFUSED = "may not compile text"
NOT_HANDED_OUT = "cannot import name 'dataclass'"
# name: (source, refused by the source check, the refusal the runner gives)
ROUND_5 = {
    # The review's probes as written: the engine no longer hands out dataclass.
    "D1 formats.dataclass: the fill price": (
        ENGINE_DATACLASS + EXECUTION + gadget(VWAP_CODE, "dataclass") + FILLED,
        True, NOT_HANDED_OUT),
    "D2 model.dataclass: the fill price": (
        "from norse_research.sim import execution, model\n" + gadget(VWAP_CODE, "model.dataclass")
        + FILLED, True, "has no attribute 'dataclass'"),
    "D3 dataclass and sys._getframe: the tripwire's file set": (
        ENGINE_DATACLASS + "\nLEAK = []\n" + gadget(FRAME_CODE + " and 0 or " + OPEN_CODE, "dataclass")
        + "LEAK = LEAK[0]\n" + GOLDEN_TO_EVIL, True, NOT_HANDED_OUT),
    "D4 dataclass and sys._getframe: the engine's book": (
        d4(ENGINE_DATACLASS), True, NOT_HANDED_OUT),
    # The same gadgets with dataclass taken from the standard library: the source check
    # refuses the import, and the runner refuses the compile however it is reached.
    "D1s the fill price, dataclass from the standard library": (
        STDLIB_DATACLASS + EXECUTION + gadget(VWAP_CODE, "dataclass") + FILLED,
        True, COMPILE_REFUSED),
    "D3s the tripwire's file set, dataclass from the standard library": (
        STDLIB_DATACLASS + "\nLEAK = []\n" + gadget(FRAME_CODE + " and 0 or " + OPEN_CODE, "dataclass")
        + "LEAK = LEAK[0]\n" + GOLDEN_TO_EVIL, True, COMPILE_REFUSED),
    "D4s the engine's book, dataclass from the standard library": (
        d4(STDLIB_DATACLASS), True, COMPILE_REFUSED),
    "C1 eval of text through enum.bltns": (
        "import enum\n\nenum.bltns.eval('1 + 1')\n" + FILLED, True, COMPILE_REFUSED),
    # The frame calls of sys, each refused by the hook before it returns.
    "S1 sys._getframe": ("import sys\n\nsys._getframe(0)\n" + FILLED, True,
                         "may not reach a frame (sys._getframe)"),
    "S2 sys._current_frames": ("import sys\n\nsys._current_frames()\n" + FILLED, True,
                               "may not reach a frame (sys._current_frames)"),
    "S3 sys.settrace": ("import sys\n\nsys.settrace(None)\n" + FILLED, True,
                        "may not reach a frame (sys.settrace)"),
    "S4 sys.setprofile": ("import sys\n\nsys.setprofile(None)\n" + FILLED, True,
                          "may not reach a frame (sys.setprofile)"),
}


@pytest.mark.parametrize("name", sorted(ROUND_5))
def test_a_round_5_probe_is_refused(name, code_root, tmp_path):
    """Security review round 5, B-H1: dataclass compiled text chosen through
    ``__annotations__``, and from there ``sys._getframe`` reached the runner's frame and
    the engine's live run. The source check refuses each probe, and the runner refuses it
    too, whatever the check missed: the engine hands out no compiling helper, every
    compile of text that is not a file is refused once the engine is sealed, and so is
    every frame call of ``sys``."""
    from test_sim_engine import RUNNER_STRATEGY

    standin = tmp_path / "standin.bin"
    standin.write_bytes(b"HOLDOUT-STAND-IN")
    source, by_source, refusal = ROUND_5[name]
    source = RUNNER_STRATEGY + source.replace("@STANDIN@", str(standin))
    problems = disallowed_imports(source, "norse_research.strategies.probe", False,
                                  ["research/norse_research/strategies"])
    if by_source:
        assert problems, f"{name}: the source check accepted it"
    configuration = {
        "window": {"first_return_day": "2021-03-01", "last_return_day": "2021-03-03", "days": 3},
        "costs": {"fee_bps_per_side": 10, "slippage_bps_per_side": 2},
    }
    result = run_in_runner(code_root, tmp_path, source, configuration, function="run_evil")
    assert result["ok"] is False, f"{name}: ran, {result.get('returns')}"
    assert refusal in result["error"], result["error"]


def test_the_golden_run_compiles_only_files_once_sealed(code_root, tmp_path):
    """The compile refusal leaves a legitimate run golden: the strategy module and every
    import are files under the readable roots."""
    from test_sim_engine import RUNNER_STRATEGY

    configuration = {
        "window": {"first_return_day": "2021-03-01", "last_return_day": "2021-03-03", "days": 3},
        "costs": {"fee_bps_per_side": 10, "slippage_bps_per_side": 2},
    }
    result = run_in_runner(code_root, tmp_path, RUNNER_STRATEGY, configuration)
    assert result["ok"], result
    golden = json.loads((Path(__file__).parent / "fixtures" / "sim-golden.json").read_text())
    for (_, got), want in zip(result["returns"], golden["weights"]["expected"]["returns"],
                              strict=True):
        assert got == pytest.approx(want, rel=1e-12)


# --- security review round 5 re-check, R5-L1: the subclass rule without module names ---


def test_an_engine_class_is_not_subclassed_by_naming_an_engine_module_unsealed():
    """R5-L1: ``EngineClass`` trusted the namespace's ``__module__``, a string the caller
    writes, so in an unsealed process (a test, a development backtest, an audit script)
    a three-argument call built a ``Costs`` whose fee is 0.0. The rule now asks for the
    engine's private token, so this process, unsealed, refuses it however it is spelt."""
    from norse_research.formats import EngineClass
    from norse_research.sim import Costs

    free = {"__module__": "norse_research.sim.model", "fee": lambda me, quantity, price: 0.0}
    with pytest.raises(TypeError, match="subclassed only by the engine"):
        EngineClass("Free", (Costs,), dict(free))
    with pytest.raises(TypeError, match="subclassed only by the engine"):
        EngineClass("Free", (Costs,), dict(free), engine=object())
    alias = Costs
    with pytest.raises(TypeError, match="subclassed only by the engine"):
        class Free(alias):
            def fee(self, quantity, price):
                return 0.0
    assert Costs(10, 2).fee(1.0, 100.0) == pytest.approx(0.1)


ROUND_5_RECHECK = {
    "N1 EngineClass with three arguments": (
        "from norse_research.formats import EngineClass\nK = EngineClass('K', (), {'x': 1})\n"),
    "N2 EngineClass with a forged __module__": (
        "from norse_research.formats import EngineClass\nfrom norse_research.sim import Costs\n"
        "Free = EngineClass('Free', (Costs,), {'__module__': 'norse_research.sim.model'})\n"),
    "N3 collections.abc.ABCMeta with an engine base": (
        "from collections.abc import ABCMeta\nfrom norse_research.sim import Costs\n"
        "Free = ABCMeta('Free', (Costs,), {})\n"),
    "N4 a function metaclass feeding EngineClass": (
        "from norse_research.formats import EngineClass\nfrom norse_research.sim import Costs\n\n\n"
        "def grab(name, bases, ns):\n    return EngineClass(name, bases, ns)\n\n\nK = Costs\n\n\n"
        "class Free(K, metaclass=grab):\n    pass\n"),
    "N5 a function metaclass alone": (
        "def grab(name, bases, ns):\n    return ns\n\n\nclass N(metaclass=grab):\n    k = 1\n"),
    "N6 a metaclass on ABCMeta": (
        "from collections.abc import ABCMeta\n\n\nclass Meta(ABCMeta):\n    pass\n\n\n"
        "class Odd(metaclass=Meta):\n    pass\n"),
}


@pytest.mark.parametrize("name", sorted(ROUND_5_RECHECK))
def test_a_strategy_names_no_class_factory_and_sets_no_metaclass(name):
    """R5-L1 and B-I1: ``EngineClass``, ``EngineABCMeta`` and ``ABCMeta`` build a class
    from three arguments, and a ``metaclass=`` keyword hands back the class namespace;
    the source check refuses all of them, as no strategy needs one."""
    problems = disallowed_imports(ROUND_5_RECHECK[name], "strategies.h1.probe", False,
                                  ["strategies/h1"])
    assert problems, f"{name}: the source check accepted it"


def test_the_strategies_on_main_pass_the_class_rules():
    """The registered strategies use neither a class factory nor a metaclass."""
    from norse_research.sources import module_name_for

    research = PACKAGE.parent
    for path in sorted((PACKAGE / "strategies").rglob("*.py")):
        rel = path.relative_to(research.parent).as_posix()
        parts = rel.split("/")
        paths = ["/".join(parts[:4])] if len(parts) > 4 else ["research/norse_research/strategies"]
        assert disallowed_imports(path.read_text(), module_name_for(rel),
                                  path.name == "__init__.py", paths) == [], rel
