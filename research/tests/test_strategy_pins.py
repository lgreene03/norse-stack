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
from norse_research.loader import DatasetLoader, inside_ledger_entry
from norse_research.manifest import Manifest, build, dumps
from norse_research.periods import DEVELOPMENT
from norse_research.runner import RESULT_PREFIX, DataView
from norse_research.sim import SimulationError
from norse_research.sources import disallowed_imports, inside, module_name_for
from norse_research.sources import strategy_declarations as declarations
from norse_research.store import ArchiveStore
from norse_research.strategies.h1 import trend
from norse_research.strategies.h2 import carry
from synthetic_store import make_series

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
    # Round 3's rules (E1, E2) pass too: carry.py's one import beside the engine is an
    # allowlisted from-import, and neither module changes anything it imports.
    assert "from norse_research.formats import Funding\n" in (
        syn.REPO / syn.MODULES["H2"]).read_text()
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
        # The world's registration is synthetic, so the genesis step is told its hashes
        # (gitledger.PREREGISTRATION_SHA256 pins the real one; security review of #65, L-9).
        write_genesis(world.ledger, tree_paths=TREE_PATHS, strategies=PINS,
                      key_record=(world.prereg_commit, KEY_RECORD),
                      registration_sha256=world.registration_sha256)
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
    strategy = trend.strategy_for(config, {}, {})
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


class Untouchable:
    """A data view that fails the test if anything on it is used at all."""

    def __getattribute__(self, name):
        raise AssertionError(f"the data view was touched ({name})")


def refused(hypothesis: str) -> list[tuple[dict, str]]:
    """Configurations a module must refuse, each with the reason it gives: a grid id run
    as a control, a benchmark run as a trial, a control on another base, another
    hypothesis's id, and a trial and a benchmark whose windows end after 2026-01-01."""
    grid, bench, control, other_grid, foreign = {
        "H1": ("H1-01", "B-H1", "C-H1", "H1-02", "H2-01"),
        "H2": ("H2-01", "B-H2", "C-H2", "H2-02", "H1-01")}[hypothesis]
    june = ("2021-06-01", "2021-06-05")
    return [
        ({**syn.configuration(grid, *june), "kind": "control"}, "trial, sensitivity or holdout"),
        ({**syn.configuration(bench, *june), "kind": "trial"}, "runs as a benchmark"),
        ({**syn.configuration(control, *june, seed=0), "base": other_grid}, "control on"),
        ({**syn.configuration(grid, *june), "id": foreign}, f"not an {hypothesis} configuration"),
        (syn.configuration(grid, "2025-12-01", "2026-01-31"), "at the latest"),
        (syn.configuration(bench, "2025-12-01", "2026-01-31"), "at the latest"),
    ]


@pytest.mark.parametrize("hypothesis", ["H1", "H2"])
def test_run_refuses_a_wrong_kind_or_window_before_touching_the_data_view(hypothesis):
    """Quant review 1, L1 (choice 12): ``run`` checks the configuration on its first line,
    so a refused run lists, covers and loads nothing. The view fails the test if any
    attribute of it is read; a run that is not refused touches it at once."""
    module = {"H1": trend, "H2": carry}[hypothesis]
    for configuration, reason in refused(hypothesis):
        with pytest.raises(SimulationError, match=reason):
            module.run(configuration, Untouchable())
    valid = syn.configuration(f"{hypothesis}-01", "2021-06-01", "2021-06-05")
    with pytest.raises(AssertionError, match="touched"):
        module.run(valid, Untouchable())


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
    for symbol, growth in (("ADAUSDT", 1.004), ("BNBUSDT", 0.997), ("BTCUSDT", 1.001),
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
        listed = {n.split("/")[2]: r[0].open_time for n, r in spot_only.items()
                  if n.endswith("/1d")}
        return trend.simulate(configuration, Market.from_rows(spot_only, window.end), listed)
    return carry.simulate(configuration, Market.from_rows(rows, window.end))
'''


def in_runner(tmp_path, module_path: str, configuration: dict, store_root=None,
              manifest="research/manifests/none.json", extra_module: str | None = None):
    """Run ``module_path``'s ``run`` as the harness does: a fresh ``python -I -B``
    process with an empty environment, the audit hook installed, the configuration
    frozen, and the dataset store (synthetic here) behind the registered loader."""
    code_root = tmp_path / "code"
    shutil.copytree(syn.RESEARCH / "norse_research", code_root / "research" / "norse_research",
                    ignore=shutil.ignore_patterns("__pycache__"))
    if extra_module is not None:
        (code_root / module_path).write_text(extra_module)
    if store_root is not None:
        (code_root / manifest).parent.mkdir(parents=True, exist_ok=True)
        (code_root / manifest).write_bytes((tmp_path / "manifest.json").read_bytes())
    pycache = tmp_path / "pycache"
    pycache.mkdir()
    payload = {
        "code_root": str(code_root), "pycache": str(pycache), "module_path": module_path,
        "module_name": module_name_for(module_path), "function": "run",
        "configuration": configuration, "entry": "e" * 64, "manifest": manifest,
        "store_root": None if store_root is None else str(store_root),
    }
    proc = subprocess.run(
        [sys.executable, "-I", "-B", "-X", f"pycache_prefix={pycache}", "-c", _RUNNER,
         str(code_root / "research"), str(code_root)],
        input=json.dumps(payload).encode(), capture_output=True, env={}, cwd=tmp_path,
        check=False,
    )
    lines = [ln for ln in proc.stdout.decode().splitlines() if ln.startswith(RESULT_PREFIX)]
    assert lines, proc.stderr.decode()[-2000:]
    return json.loads(lines[-1][len(RESULT_PREFIX):])


@pytest.mark.parametrize("cid", ["H1-01", "H2-01"])
def test_the_strategies_run_inside_the_runner_on_a_frozen_configuration(tmp_path, cid):
    """The runner hands the strategy its configuration frozen (mapping proxies and
    tuples) in a fresh interpreter under the audit hook (T13). Each module runs there
    and returns exactly what it returns in this process on a plain dictionary."""
    config = syn.configuration(cid, "2021-06-01", "2021-06-03")
    result = in_runner(tmp_path, "research/norse_research/strategies/runner_check.py", config,
                       extra_module=RUNNER_CHECK)
    assert result["ok"], result
    assert result["crossed_gaps"] == []  # no store: no load crossed a gap
    here: dict = {}
    exec(compile(RUNNER_CHECK, "runner_check", "exec"), here)
    expected = here["run"](config, None)
    assert [list(pair) for pair in expected] == result["returns"]
    assert any(value != 0.0 for _, value in expected), "the strategy traded"


DROPPED = syn.ms(2025, 12, 1)  # BTC spot's 15-minute bar opening at the first decision


def synthetic_store(tmp_path, spot_daily_from: str = "2025-11") -> ArchiveStore:
    """A synthetic mirror and development manifest for BTC and ETH, November and December
    2025 (spot daily bars from ``spot_daily_from``), every price 1 and every funding rate
    0.0001 (synthetic_store.put), with one 15-minute bar of BTC spot missing, which the
    manifest records as a gap."""
    store = ArchiveStore(tmp_path / "mirror")
    series = []
    for coin in carry.CONTROL_DRAW_ORDER:
        for name in (f"spot/klines/{coin}/1d", f"spot/klines/{coin}/15m",
                     f"um/klines/{coin}/1d", f"um/klines/{coin}/15m", f"um/fundingRate/{coin}"):
            drop = (DROPPED,) if name == "spot/klines/BTCUSDT/15m" else ()
            first = spot_daily_from if name.startswith("spot/") and name.endswith("/1d") else (
                "2025-11")
            series.append(make_series(store, name, first, "2025-12", drop=drop))
    (tmp_path / "manifest.json").write_bytes(dumps(build(store, DEVELOPMENT, series)))
    return store


def test_h2_runs_through_the_registered_loader_and_reports_the_gaps_it_crossed(tmp_path):
    """The pinned ``run`` itself, in the runner, on the registered loader over a
    synthetic store: every series' runs come from ``DataView.coverage`` (the manifest's
    metadata), and the recorded gap the fill search crossed comes back as
    ``crossed_gaps`` for the result entry (section 5, Gaps). The same call in this
    process gives the same returns and gaps."""
    store = synthetic_store(tmp_path)
    config = syn.configuration("H2-06", "2025-12-01", "2025-12-10")
    result = in_runner(tmp_path, syn.MODULES["H2"], config, store_root=store.root,
                       manifest="research/manifests/synthetic.json")
    assert result["ok"], result
    loader = DatasetLoader(Manifest.load(tmp_path / "manifest.json"), store)
    with inside_ledger_entry("e" * 64):
        expected = carry.run(config, DataView(loader))
    assert [list(pair) for pair in expected] == result["returns"]
    assert len(expected) == 10 and any(value != 0.0 for _, value in expected)
    assert result["crossed_gaps"] == loader.crossed_gaps()
    assert [gap[0] for gap in result["crossed_gaps"]] == ["spot/klines/BTCUSDT/15m"]


@pytest.mark.parametrize("cid", ["B-H1", "H1-01"])
def test_h1_runs_through_the_registered_loader_on_the_series_the_manifest_lists(
        tmp_path, cid):
    """H1's pinned ``run`` itself, in the runner, on the registered loader over a
    synthetic store: the pairs it ranks come from ``DataView.list_series`` (the manifest's
    keys) and their runs from ``DataView.coverage``. Spot daily bars from 2025-06 give BTC
    and ETH a close 120 days before 2025-12-01 (D13), so B-H1 buys a fifth of each, its
    BTC fill crossing the recorded gap, which comes back as ``crossed_gaps``; with every
    price 1 no H1-01 vote is above zero, so H1-01 holds cash and crosses nothing. The same
    call in this process gives the same returns and gaps."""
    store = synthetic_store(tmp_path, spot_daily_from="2025-06")
    config = syn.configuration(cid, "2025-12-01", "2025-12-10")
    result = in_runner(tmp_path, syn.MODULES["H1"], config, store_root=store.root,
                       manifest="research/manifests/synthetic.json")
    assert result["ok"], result
    loader = DatasetLoader(Manifest.load(tmp_path / "manifest.json"), store)
    with inside_ledger_entry("e" * 64):
        expected = trend.run(config, DataView(loader))
    assert [list(pair) for pair in expected] == result["returns"]
    assert result["crossed_gaps"] == loader.crossed_gaps()
    assert len(expected) == 10
    if cid == "B-H1":
        assert expected[0][1] < 0.0 and all(value == 0.0 for _, value in expected[1:])
        assert [gap[0] for gap in result["crossed_gaps"]] == ["spot/klines/BTCUSDT/15m"]
    else:
        assert all(value == 0.0 for _, value in expected) and result["crossed_gaps"] == []
