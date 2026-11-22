"""The universe check of quant review 1, C1 (``norse_research.reconcile_universe``).

On a synthetic mirror and synthetic manifests only: daily bars with flat, hand-set quote
volumes, so each month end's top ten is worked out by hand below. The check must print
one line and nothing else, refuse the holdout manifest and any month end past 2025-12,
and be out of a strategy's reach.
"""

from __future__ import annotations

import json
import re

import pytest
import strategy_synthetic as syn
from norse_research import reconcile_universe
from norse_research.formats import DatasetSpec
from norse_research.loader import DatasetLoader
from norse_research.manifest import Manifest, build, write
from norse_research.periods import DEVELOPMENT, HOLDOUT, HoldoutSealed
from norse_research.sources import disallowed_imports
from norse_research.store import ArchiveStore

# symbol: (first day, daily quote volume, from 2025-10-01 on). Every pair runs to
# 2025-12-31, ADA to 2026-01-31 so the holdout manifest pins a file. USDC is removed
# before ranking, ETHBULL is ranked (and only later dropped by category), LUNC is first
# archived on 2025-09-01, DOT's volume doubles on 2025-10-01, ADABTC is not a USDT pair.
PAIRS = {
    "ADAUSDT": ("2025-03-01", 9e6, 9e6),
    "BNBUSDT": ("2025-03-01", 8e6, 8e6),
    "CHZUSDT": ("2025-03-01", 7e6, 7e6),
    "DOTUSDT": ("2025-03-01", 6e6, 1.2e7),
    "USDCUSDT": ("2025-03-01", 1e9, 1e9),
    "ETHBULLUSDT": ("2025-03-01", 5e8, 5e8),
    "LUNCUSDT": ("2025-09-01", 2e7, 2e7),
    "ADABTC": ("2025-03-01", 1e15, 1e15),
}
# The top ten over the 30 daily bars closing at each month end, from the volumes above.
PUBLISHED = {
    "2025-08": ["ETHBULLUSDT", "ADAUSDT", "BNBUSDT", "CHZUSDT", "DOTUSDT"],
    "2025-09": ["ETHBULLUSDT", "LUNCUSDT", "ADAUSDT", "BNBUSDT", "CHZUSDT", "DOTUSDT"],
    "2025-10": ["ETHBULLUSDT", "LUNCUSDT", "DOTUSDT", "ADAUSDT", "BNBUSDT", "CHZUSDT"],
    "2025-12": ["ETHBULLUSDT", "LUNCUSDT", "DOTUSDT", "ADAUSDT", "BNBUSDT", "CHZUSDT"],
}
ONE_LINE = re.compile(
    r"(equal at all \d+ month ends"
    r"|differs at \d{4}-\d{2}: computed \[[^\]]*\]( \(the top-ten guard refused it\))?;"
    r" published \[[^\]]*\])\n")


@pytest.fixture
def world(tmp_path):
    """The synthetic mirror, its development and holdout manifests, and a published
    ranking file holding ``PUBLISHED``."""
    return make_world(tmp_path, PAIRS)


def make_world(tmp_path, pairs):
    store = ArchiveStore(tmp_path / "mirror")
    series = []
    for symbol, (first_day, quote, quote_from_october) in pairs.items():
        first = syn.ms(*map(int, first_day.split("-")))
        end = syn.ms(2026, 2, 1) if symbol == "ADAUSDT" else syn.ms(2026, 1, 1)
        bars = [(t, 100.0, quote if t < syn.ms(2025, 10, 1) else quote_from_october)
                for t in range(first, end, syn.DAY)]
        spec = DatasetSpec.from_name(f"spot/klines/{symbol}/1d")
        series.append((spec, syn.put_bars(store, spec, bars)))
    paths = {}
    for period in (DEVELOPMENT, HOLDOUT):
        paths[period] = tmp_path / f"{period}.json"
        write(build(store, period, series), paths[period])
    published = tmp_path / "universe-candidates.json"
    published.write_text(json.dumps({"development_ranking": PUBLISHED}))
    return store, paths, published


def run_main(world, capsys, published=None):
    store, paths, published_file = world
    if published is not None:
        published_file.write_text(json.dumps({"development_ranking": published}))
    code = reconcile_universe.main(paths[DEVELOPMENT], store.root, published_file)
    return code, capsys.readouterr()


def test_the_check_prints_one_line_when_every_month_end_is_equal(world, capsys):
    code, out = run_main(world, capsys)
    assert (code, out.out, out.err) == (0, "equal at all 4 month ends\n", "")
    assert ONE_LINE.fullmatch(out.out)


def test_the_check_prints_the_first_month_end_that_differs_and_both_top_tens(world, capsys):
    altered = {**PUBLISHED, "2025-10": ["ETHBULLUSDT", "LUNCUSDT", "ADAUSDT", "DOTUSDT",
                                        "BNBUSDT", "CHZUSDT"]}
    altered["2025-12"] = ["ETHBULLUSDT"]  # a later difference is never reached
    code, out = run_main(world, capsys, altered)
    assert code == 1 and out.err == "" and ONE_LINE.fullmatch(out.out)
    assert out.out == (
        "differs at 2025-10: computed ['ETHBULLUSDT', 'LUNCUSDT', 'DOTUSDT', 'ADAUSDT', "
        "'BNBUSDT', 'CHZUSDT']; published ['ETHBULLUSDT', 'LUNCUSDT', 'ADAUSDT', 'DOTUSDT', "
        "'BNBUSDT', 'CHZUSDT']\n")


def test_a_non_candidate_in_a_computed_top_ten_is_a_difference(tmp_path, capsys):
    # H1's guard (choice 16) refuses QQQ, which is not one of the 115 candidates; the
    # check reports that month as differing, with the ten it computed.
    pairs = {**PAIRS, "QQQUSDT": ("2025-03-01", 3e7, 3e7)}
    world = make_world(tmp_path, pairs)
    code, out = run_main(world, capsys)
    assert code == 1 and ONE_LINE.fullmatch(out.out)
    assert out.out.startswith("differs at 2025-08: computed ['ETHBULLUSDT', 'QQQUSDT', ")
    assert "(the top-ten guard refused it)" in out.out


def test_the_check_refuses_the_holdout_manifest_and_any_month_end_past_2025(world, tmp_path):
    store, paths, _ = world
    with pytest.raises(HoldoutSealed):  # the loader refuses a holdout manifest outright
        reconcile_universe.main(paths[HOLDOUT], store.root, world[2])
    development, holdout = Manifest.load(paths[DEVELOPMENT]), Manifest.load(paths[HOLDOUT])
    with pytest.raises(HoldoutSealed, match="development manifest alone"):
        reconcile_universe.reconcile(DatasetLoader(development, store, holdout=holdout),
                                     PUBLISHED)
    # A month end past 2025-12 is refused before anything is loaded: the store is empty.
    empty = DatasetLoader(development, ArchiveStore(tmp_path / "nothing"), integrity_audit=True)
    with pytest.raises(HoldoutSealed, match="development month ends only"):
        reconcile_universe.reconcile(empty, {**PUBLISHED, "2026-01": ["ADAUSDT"]})


def test_a_strategy_module_cannot_import_the_check():
    # The check reads out-of-sample development rows outside a ledger entry, so a
    # strategy must not reach it: it is not on the source check's allowlist.
    h1 = ["research/norse_research/strategies/h1"]
    for source in ("from norse_research.reconcile_universe import reconcile\n",
                   "import norse_research.reconcile_universe\n",
                   "from norse_research import reconcile_universe\n"):
        assert disallowed_imports(source, "norse_research.strategies.h1.trend",
                                  allowed_paths=h1) != [], source
