"""The strategy modules as the genesis step, the harness and the runner read them
(G2.2, G2.3; docs/research/trial-ledger.md).

``fixtures/pins.example.json`` is an example of the genesis step's ``--strategies``
input, not the real one: one pinned path and function for every registered id. These
tests hold it, and the two modules it names, to the contract the genesis step and the
harness enforce, by running that very code (``write_genesis``, ``load_strategy``,
``Registration.resolve`` and the runner's fresh interpreter) on a temporary repository
and synthetic data. No market data is read and the dataset store is never opened.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import strategy_synthetic as syn
from conftest import clear_overrides
from ledger_fixtures import KEY_RECORD, World, git
from norse_research.gitledger import registered_ids, write_genesis
from norse_research.harness import _RUNNER, load_strategy
from norse_research.runner import RESULT_PREFIX
from norse_research.sources import disallowed_imports, inside, module_name_for
from norse_research.sources import strategy_declarations as declarations
from norse_research.strategies.h1 import trend
from norse_research.strategies.h2 import carry

FIXTURES = Path(__file__).parent / "fixtures"
PINS = json.loads((FIXTURES / "pins.example.json").read_text())
TREE_PATHS = {"harness": ["research/norse_research"],
              "H1": ["research/norse_research/strategies/h1"],
              "H2": ["research/norse_research/strategies/h2"]}
IDS = registered_ids(syn.REGISTRATION)


def test_the_example_pins_name_exactly_the_registered_ids():
    # H1-01 to H1-08, B-H1 and C-H1 in H1's paths; H2-01 to H2-06, B-H2 and C-H2 in H2's
    # (gitledger.registered_ids: a benchmark by its window, a control by its base).
    assert set(PINS) == set(IDS) and len(PINS) == 18
    for cid, pin in PINS.items():
        assert set(pin) == {"path", "function"} and pin["function"] == "run"
        assert inside(pin["path"], TREE_PATHS[IDS[cid]]), cid
    for hypothesis, path in syn.MODULES.items():
        configurations, parameters, defaults = declarations((syn.REPO / path).read_text(), "run")
        assert configurations == {c for c, h in IDS.items() if h == hypothesis}
        assert defaults == {}
        # Every key a registered configuration of the hypothesis holds is a parameter.
        grid = syn.REGISTRATION.doc["hypotheses"][hypothesis]["configurations"]
        assert parameters == {k for c in grid for k in c} - {"id", "label"}


def test_every_module_in_a_hypothesis_paths_passes_the_source_check():
    for hypothesis in ("H1", "H2"):
        paths = TREE_PATHS[hypothesis]
        files = sorted((syn.REPO / paths[0]).rglob("*.py"))
        assert len(files) == 2
        for path in files:
            rel = path.relative_to(syn.REPO).as_posix()
            assert disallowed_imports(path.read_text(), module_name_for(rel),
                                      rel.endswith("__init__.py"), paths) == [], rel
    # The two hypotheses' paths do not overlap, and neither may reach the other's.
    assert not inside(TREE_PATHS["H1"][0], TREE_PATHS["H2"])
    assert not inside(TREE_PATHS["H2"][0], TREE_PATHS["H1"])
    assert disallowed_imports("from norse_research.strategies.h2 import carry\n",
                              "norse_research.strategies.h1.trend",
                              allowed_paths=TREE_PATHS["H1"]) != []


def test_the_genesis_step_accepts_the_pins_and_each_id_resolves_as_registered(
        tmp_path, monkeypatch):
    """The real genesis step over a temporary repository holding this package: it parses
    every module under each hypothesis's paths and the pinned module's literals. Then
    each id resolves through the harness's own ``load_strategy`` and the registration,
    and the module builds the strategy the registered configuration describes."""
    clear_overrides(monkeypatch)
    world = World(tmp_path, genesis=False)
    try:
        write_genesis(world.ledger, tree_paths=TREE_PATHS, strategies=PINS,
                      key_record=(world.prereg_commit, KEY_RECORD))
        genesis = world.ledger.verify().snapshot.genesis
        assert genesis.body["strategies"] == PINS
        head = git(world.repo, "rev-parse", "HEAD")
        registration = world.registration
        for cid in sorted(PINS):
            strategy = load_strategy(world.ledger.git, head, genesis, registration, cid)
            request = {"configuration": cid}
            if cid in registration.controls:
                request["seed"] = 0
            resolved = registration.resolve(request, strategy)
            parameters = resolved.configuration["parameters"]
            if cid in registration.grid:
                registered = {k: v for k, v in registration.grid[cid].items()
                              if k not in ("id", "label")}
                assert parameters == registered, cid
            elif cid in registration.controls:
                base = registration.controls[cid]["base"]
                assert parameters == {k: v for k, v in registration.grid[base].items()
                                      if k not in ("id", "label")}, cid
            else:
                assert parameters == {}, cid
    finally:
        world.close()


@pytest.mark.parametrize("cid", [c for c in sorted(PINS) if c.startswith(("H1", "B-H1"))])
def test_each_h1_configuration_builds_the_registered_strategy(cid):
    config = syn.configuration(cid, "2021-06-01", "2021-06-01")
    strategy = trend.strategy_for(config, {})
    if cid == "B-H1":
        assert isinstance(strategy, trend.MonthEndEqualWeight)
        return
    registered = syn.REGISTRATION.grid[cid]
    per_day = 6 if registered["bar"] == "4h" else 1
    assert strategy.interval == registered["bar"]
    assert strategy.lookbacks == tuple(registered["lookbacks_days"])
    assert (strategy.slots, strategy.band, strategy.cap) == (
        5, registered["rebalance_band"], 1.0)
    if registered["vol_scaling"]:
        assert (strategy.vol_target, strategy.span, strategy.per_year) == (
            0.2, 60 * per_day, 365 * per_day)
    else:
        assert strategy.vol_target is None
    funding = registered["funding_filter"]
    assert strategy.filter == (None if funding is None else (7.0, 0.0063))
    assert strategy.flipped is None


def test_the_candidates_are_the_115_g06_pinned_and_their_bars_are_c_h1s_n():
    """C-H1 draws one number per development daily bar of the 115 candidates. The list
    in trend.py is G0.6's, and by the committed coverage metadata (row counts only, no
    prices) those candidates hold the 158,821 bars G1.9's numpy vectors were drawn for;
    the development manifest pins a daily series for each candidate that has any."""
    candidates = json.loads((syn.RESEARCH / "coverage" / "universe-candidates.json").read_text())
    assert trend.CANDIDATES == tuple(sorted(candidates["candidate_first_daily_bar"]))
    assert set(trend.CANDIDATES) == (set(candidates["development_candidates"])
                                     | set(candidates["holdout_only_candidates"]))
    coverage = {d["dataset"]: d for d in json.loads(
        (syn.RESEARCH / "coverage" / "series-coverage.json").read_text())["datasets"]}
    vectors = json.loads((FIXTURES / "numpy-rng-vectors.json").read_text())
    with_bars = [s for s in trend.CANDIDATES
                 if (coverage.get(f"spot/klines/{s}/1d") or {}).get("development")]
    assert sum(coverage[f"spot/klines/{s}/1d"]["development"]["rows"]
               for s in with_bars) == vectors["c_h1_n"]
    pinned = json.loads((syn.RESEARCH / "manifests" /
                         "binance-vision-development.json").read_text())["datasets"]
    assert {f"spot/klines/{s}/1d" for s in with_bars} <= set(pinned)
    for coin in carry.CONTROL_DRAW_ORDER:
        assert coverage[f"um/fundingRate/{coin}"]["development"]["rows"] == (
            vectors["development_settlements_m"])


# --- inside the runner ----------------------------------------------------------------

RUNNER_CHECK = '''
from norse_research.formats import Funding, Kline
from norse_research.sim import Market, Window
from norse_research.strategies.h1 import trend
from norse_research.strategies.h2 import carry

CONFIGURATIONS = ["CHECK"]
PARAMETERS = []
DEFAULTS = {}

D = 86_400_000
M15 = 900_000


def bar(t, step, close):
    return Kline(t, close, close, close * 0.99, close, 10.0, t + step - 1, 10.0 * close, 1,
                 5.0, 5.0 * close)


def run(configuration, data_view):
    window = Window.from_configuration(configuration)
    first = window.start - 160 * D
    rows = {}
    for symbol, growth in (("AAAUSDT", 1.004), ("BBBUSDT", 0.997), ("BTCUSDT", 1.001),
                           ("ETHUSDT", 1.002)):
        closes = [100.0 * growth ** i for i in range(170)]
        for market in ("spot", "um"):
            rows[f"{market}/klines/{symbol}/1d"] = [bar(first + i * D, D, c)
                                                    for i, c in enumerate(closes)]
            rows[f"{market}/klines/{symbol}/15m"] = [
                bar(t, M15, closes[160 + (t - window.start) // D])
                for t in range(window.start, window.end, M15)]
        rows[f"um/fundingRate/{symbol}"] = [Funding(first + i * 28_800_000 + 3, 8, 0.0004)
                                            for i in range(170 * 3)]
    spot_only = {n: r for n, r in rows.items() if not n.startswith("um/klines")}
    if configuration["hypothesis"] == "H1":
        return trend.simulate(configuration, Market.from_rows(spot_only, window.end))
    return carry.simulate(configuration, Market.from_rows(rows, window.end))
'''


@pytest.mark.parametrize("cid", ["H1-01", "H2-01"])
def test_the_strategies_run_inside_the_runner_on_a_frozen_configuration(tmp_path, cid):
    """The runner hands the strategy its configuration frozen (mapping proxies and
    tuples) in a fresh interpreter under the audit hook (T13). Each module runs there
    and returns exactly what it returns in this process on a plain dictionary."""
    code_root = tmp_path / "code"
    shutil.copytree(syn.RESEARCH / "norse_research", code_root / "research" / "norse_research",
                    ignore=shutil.ignore_patterns("__pycache__"))
    module = code_root / "research" / "norse_research" / "strategies" / "runner_check.py"
    module.write_text(RUNNER_CHECK)
    pycache = tmp_path / "pycache"
    pycache.mkdir()
    config = syn.configuration(cid, "2021-06-01", "2021-06-03")
    payload = {
        "code_root": str(code_root), "pycache": str(pycache),
        "module_path": "research/norse_research/strategies/runner_check.py",
        "module_name": "norse_research.strategies.runner_check", "function": "run",
        "configuration": config, "entry": "e" * 64,
        "manifest": "research/manifests/none.json", "store_root": None,
    }
    proc = subprocess.run(
        [sys.executable, "-I", "-B", "-X", f"pycache_prefix={pycache}", "-c", _RUNNER,
         str(code_root / "research"), str(code_root)],
        input=json.dumps(payload).encode(), capture_output=True, env={}, cwd=tmp_path,
        check=False,
    )
    lines = [ln for ln in proc.stdout.decode().splitlines() if ln.startswith(RESULT_PREFIX)]
    assert lines, proc.stderr.decode()[-2000:]
    result = json.loads(lines[-1][len(RESULT_PREFIX):])
    assert result["ok"], result
    here: dict = {}
    exec(compile(RUNNER_CHECK, "runner_check", "exec"), here)
    expected = here["run"](config, None)
    assert [list(pair) for pair in expected] == result["returns"]
    assert any(value != 0.0 for _, value in expected), "the strategy traded"
