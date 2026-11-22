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
from dataclasses import dataclass, field

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


@dataclass
class Mine:
    seen: list = field(default_factory=list)


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


VIEW = (Tamper.reach, Reach.book_spot, Reach.spot, Reach.perp, Reach.carry, Reach.marks,
        Reach.cash)
HISTORY = (Reach.history_now, Reach.history_cache, Reach.history_views)


class Tampering(Scripted):
    """Decides exactly as ``Scripted`` does, then tries every route to the engine
    through what it was lent, ending with every slot of the view rewritten."""

    routes = "view"
    attempts = 0

    def decide(self, history, book):
        decision = super().decide(history, book)
        if Tampering.routes == "view":
            attempts = [(a, book) for a in VIEW]
        else:
            attempts = [(a, history) for a in HISTORY]
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


def run_tampered(configuration, data_view):
    Tampering.routes = configuration["routes"]
    window = Window.from_configuration(configuration)
    costs = Costs.from_configuration(configuration)
    for driver in ("backtest", "paper"):
        runs = []
        for kind in (Scripted, Tampering):
            if driver == "backtest":
                runs.append(backtest(golden_strategy(kind), Market.from_rows(golden_rows(), HORIZON),
                                     window, costs))
            else:
                runs.append(paper_run(golden_strategy(kind), golden_rows(), window, costs))
        clean, tampered = runs
        for name in ("orders", "fills", "lapses", "accruals", "marks", "skips", "returns"):
            if getattr(clean, name) != getattr(tampered, name):
                raise AssertionError(f"{driver}: tampering changed the run's {name}")
    if Tampering.attempts == 0:
        raise AssertionError("no decision was taken, so nothing was tried")
    return tampered.returns
'''


@pytest.mark.parametrize("routes", ["view", "history"])
def test_nothing_a_strategy_does_to_what_it_is_lent_changes_the_run(routes, code_root, tmp_path):
    """Security review of PR 61, the route stated after round 2: ``Tamper.reach(view)``,
    a method called through its class with the view as ``self``, reached the live book
    through ``self._book``. A ``BookView`` is now a snapshot holding immutable values
    and no reference to the book, and a strategy gets its own history while the engine
    keeps the one it builds the orders from. Every route below, the exact probe among
    them, leaves the orders, fills, lapses, accruals, marks, skips and returns of both
    drivers as they are without it, and the returns are the golden ones."""
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
