"""Check H1's dated-universe ranking against G0.6's published top tens (quant review 1 of
PR 62, check C1; approved by the owner on 2026-11-22: "Run it").

H1 ranks each month end's universe with ``month_end_ranking`` in
``strategies/h1/trend.py`` (section 6, step 1, with the guard of choice 16). G0.6
published the top ten it computed at every development month end in
research/coverage/universe-candidates.json (``development_ranking``). This module ranks
every one of those month ends with H1's own code, on the development manifest's daily
series read through the registered loader, and reports only whether the two agree:

    equal at all 101 month ends

or the first month end where they do not, with both top tens. It prints no price,
volume, signal, return or any other figure, builds no strategy and no decision, and
writes nothing.

It is the loader's second integrity audit (G1.5, threat T2): it reads development rows
from H1's out-of-sample window outside a ledger entry, read-only, for this integrity
check alone. It refuses a holdout manifest and any month end after 2025-12, so no row
from 2026-01-01 on is requested, and the loader refuses those in any case.

Run from research/: ``python -m norse_research.reconcile_universe`` with
NORSE_DATASETS_DIR set.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .loader import DatasetLoader
from .manifest import MANIFEST_FILES, Manifest
from .periods import DEVELOPMENT, HOLDOUT_START_MS, LAST_DEVELOPMENT_MONTH, HoldoutSealed
from .runner import DataView
from .sim import (
    History,
    Market,
    SimulationError,
    month_of_decision,
    quote_volumes,
    rank_top,
    spot_series,
)
from .store import ArchiveStore, datasets_root
from .strategies.h1 import trend
from .universe import month_close_ms

UNIVERSE_FILE = Path(__file__).resolve().parent.parent / "coverage" / "universe-candidates.json"
DAY_MS = 86_400_000


def published_ranking(path: Path = UNIVERSE_FILE) -> dict[str, list[str]]:
    """G0.6's top ten at each development month end, by month."""
    return json.loads(Path(path).read_text())["development_ranking"]


def reconcile(loader: DatasetLoader, published: Mapping[str, Sequence[str]]) -> str:
    """The one line this check reports: every month end of ``published`` ranked by H1's
    ``month_end_ranking`` on ``loader``'s development daily series, compared in order."""
    if loader.holdout is not None or loader.manifest.period != DEVELOPMENT:
        raise HoldoutSealed("the universe check reads the development manifest alone")
    months = sorted(published)
    late = [m for m in months if m > LAST_DEVELOPMENT_MONTH]
    if late or not months:
        raise HoldoutSealed(f"the universe check ranks development month ends only, not {late}")
    horizon = month_close_ms(months[-1])  # 2026-01-01 00:00 at the latest: rows before it
    if horizon > HOLDOUT_START_MS:
        raise HoldoutSealed("the universe check reads no row from 2026-01-01 on")
    view = DataView(loader)
    names = [n for n in trend.series_needed({"parameters": {}}, view.list_series())
             if n.endswith("/1d")]
    listed = trend.listed_pairs(view, names)
    names = [n for n in names if listed[n.split("/")[2]] < horizon]
    pairs = tuple(sorted((s, first) for s, first in listed.items() if first < horizon))
    market = Market.from_view(view, names, horizon)
    for month in months:
        t = month_close_ms(month)
        history = History(t, "1d", _views(market, t), _segments(market), _never_ended, {})
        try:
            computed = trend.month_end_ranking(history, pairs)
            refused = ""
        except SimulationError as error:
            if "pinned candidates" not in str(error):
                raise
            computed = _unguarded_ranking(history, pairs)
            refused = " (the top-ten guard refused it)"
        if list(computed) != list(published[month]):
            return (f"differs at {month}: computed {list(computed)}{refused}; "
                    f"published {list(published[month])}")
    return f"equal at all {len(months)} month ends"


def _views(market: Market, t: int) -> Callable:
    return lambda name: market.series[name].view(t) if name in market.series else None


def _segments(market: Market) -> Callable:
    return lambda name: market.series[name].segments if name in market.series else None


def _never_ended(instrument) -> bool:
    return False  # the ranking asks no instrument whether it has ended


def _unguarded_ranking(history, pairs) -> list[str]:
    """Step 1 as ``month_end_ranking`` computes it, without the guard, only to show the
    computed ten for a month end the guard refuses."""
    t = history.now
    volumes = {}
    for symbol, first in pairs:
        name = spot_series(symbol, "1d")
        if first >= t or not history.has(name):
            continue
        view = history.series(name)
        volumes[symbol] = quote_volumes(view[view.index_after(t - trend.RANK_DAYS * DAY_MS - 1):])
    return rank_top(volumes, month_of_decision(t))


def main(manifest_path: Path | None = None, store_root: Path | None = None,
         published_path: Path = UNIVERSE_FILE) -> int:
    """Rank and compare, printing the one line. The registered development manifest and
    the datasets folder unless given (tests pass a synthetic store and manifest)."""
    manifest = Manifest.load(MANIFEST_FILES[DEVELOPMENT] if manifest_path is None
                             else manifest_path)
    store = ArchiveStore(datasets_root() if store_root is None else store_root)
    # The loader's second integrity audit (G1.5, T2): development rows from H1's
    # out-of-sample window, outside a ledger entry, read-only, for this check alone.
    loader = DatasetLoader(manifest, store, integrity_audit=True)
    line = reconcile(loader, published_ranking(published_path))
    print(line)
    return 0 if line.startswith("equal") else 1


if __name__ == "__main__":
    sys.exit(main())
