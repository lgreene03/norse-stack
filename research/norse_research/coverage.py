"""Data coverage for H1 and H2 on data.binance.vision (goal G0.6).

Three steps, each idempotent, all reading only public data:

``universe``  lists every USDT spot pair in the archive, mirrors its daily klines
              (checksum-verified) and ranks 30-day quote volume at every month end,
              writing which pairs the dated universe can select.
``sync``      mirrors the series each candidate needs: spot daily, four-hour and
              15-minute klines, and where a USDT-margined perpetual exists, funding and
              premium index (and, for H2's BTC and ETH, perpetual klines).
``report``    measures every mirrored series (first and last bar, missing months,
              gaps, rows) and writes research/coverage/coverage.json.

Only timestamps, row counts and volume ranks are computed. No return, signal or
price statistic is, because pre-registration (G0.7) has not been signed.

Run from research/: ``python -m norse_research.coverage universe`` with
NORSE_DATASETS_DIR set.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from .binance_archive import HttpFetcher
from .formats import (
    LICENCE,
    DatasetSpec,
    Gap,
    bar_gaps,
    funding_gaps,
    parse_funding,
    parse_klines,
    read_csv_member,
)
from .periods import (
    DEVELOPMENT,
    HOLDOUT,
    LAST_DEVELOPMENT_MONTH,
    LAST_HOLDOUT_MONTH,
    iso_time,
    period_of_month,
)
from .store import ArchiveStore, datasets_root
from .universe import (
    CANDIDATE_DEPTH,
    DAY_MS,
    base_asset,
    candidate_set,
    is_ranked,
    leveraged_bases,
    month_close_ms,
    months_between,
    rank_month_ends,
    trailing_volume,
)

RESEARCH_DIR = Path(__file__).resolve().parent.parent
COVERAGE_DIR = RESEARCH_DIR / "coverage"
UNIVERSE_FILE = COVERAGE_DIR / "universe-candidates.json"
COVERAGE_FILE = COVERAGE_DIR / "coverage.json"

SPOT_KLINES_ROOT = "data/spot/monthly/klines/"
FUNDING_ROOT = "data/futures/um/monthly/fundingRate/"
H2_SYMBOLS = ("BTCUSDT", "ETHUSDT")
BAR_INTERVALS = ("1d", "4h", "15m")
# H1's longest signal lookback: the sign of the 120-day return.
SIGNAL_LOOKBACK_DAYS = 120

Log = Callable[[str], None]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def listed_months(store: ArchiveStore, spec: DatasetSpec) -> list[str]:
    keys = [o.key for o in store.listing(spec.prefix) if o.key.endswith(".zip")]
    return sorted(spec.month_of(k) for k in keys)


def mirror_all(store: ArchiveStore, specs: list[DatasetSpec], log: Log) -> dict[str, list[str]]:
    """List, then mirror, every monthly file of each series up to the holdout's end.

    Returns the months mirrored per series name.
    """
    store.prefetch_listings([spec.prefix for spec in specs])
    months = {
        spec.name: [m for m in listed_months(store, spec) if m <= LAST_HOLDOUT_MONTH]
        for spec in specs
    }
    items = [(spec.key(m), spec.max_bytes()) for spec in specs for m in months[spec.name]]
    log(f"{len(specs)} series listed: {len(items)} monthly files in scope")
    store.mirror(items, progress=lambda done, total: log(f"  {done}/{total} new files"))
    return months


def read_rows(store: ArchiveStore, spec: DatasetSpec, month: str) -> list:
    data, _ = store.read(spec.key(month))
    csv_name = spec.file_name(month)[: -len(".zip")] + ".csv"
    text = read_csv_member(data, csv_name, spec.max_bytes())
    source = spec.key(month)
    return parse_funding(text, source) if spec.kind == "fundingRate" else parse_klines(text, source)


def daily_quote_volume(store: ArchiveStore, symbol: str, months: Iterable[str]) -> dict[int, float]:
    spec = DatasetSpec("spot", "klines", symbol, "1d")
    series: dict[int, float] = {}
    for month in months:
        for row in read_rows(store, spec, month):
            if row.open_time in series:
                raise ValueError(f"{symbol}: duplicate daily bar {row.open_time}")
            series[row.open_time] = row.quote_volume
    return series


def history_milestones(
    ranking: dict[str, list[str]],
    volumes: dict[str, dict[int, float]],
    first_bar: dict[str, int],
    universe_size: int = 5,
    lookback_days: int = SIGNAL_LOOKBACK_DAYS,
) -> dict[str, str | None]:
    """The first month ends at which H1's rule can be applied, under two readings.

    ``five_ranked``: at least five pairs have volume in the 30-day window.
    ``top_five_all_with_history``: every one of the top five already has
    ``lookback_days`` daily bars before the month end, so each signal is defined.
    ``five_with_history``: at least five pairs with that history have volume, which
    is when a rule that ranks only pairs old enough to signal can first fill five.
    """
    lookback = lookback_days * DAY_MS
    milestones: dict[str, str | None] = dict.fromkeys(
        ("five_ranked", "top_five_all_with_history", "five_with_history")
    )
    for month in sorted(ranking):
        close = month_close_ms(month)
        old_enough = {s for s, t in first_bar.items() if t <= close - lookback}
        top = ranking[month][:universe_size]
        if milestones["five_ranked"] is None and len(top) >= universe_size:
            milestones["five_ranked"] = month
        if (
            milestones["top_five_all_with_history"] is None
            and len(top) >= universe_size
            and all(s in old_enough for s in top)
        ):
            milestones["top_five_all_with_history"] = month
        if milestones["five_with_history"] is None:
            active = [s for s in old_enough if trailing_volume(volumes[s], close) > 0]
            if len(active) >= universe_size:
                milestones["five_with_history"] = month
    return milestones


def run_universe(store: ArchiveStore, log: Log) -> dict:
    symbols = [s for s in store.subdirectories(SPOT_KLINES_ROOT) if s.endswith("USDT")]
    unsafe = [s for s in symbols if not s.isalnum()]
    if unsafe:
        raise ValueError(f"archive lists symbols that are not letters and digits: {unsafe}")
    ranked = [s for s in symbols if is_ranked(s)]
    log(f"{len(symbols)} USDT spot pairs listed; {len(ranked)} ranked")
    specs = [DatasetSpec("spot", "klines", symbol, "1d") for symbol in ranked]
    mirrored = mirror_all(store, specs, log)
    volumes = {
        spec.symbol: daily_quote_volume(store, spec.symbol, mirrored[spec.name])
        for spec in specs
        if mirrored[spec.name]
    }
    first_month = min(months[0] for months in mirrored.values() if months)
    dev_months = months_between(first_month, LAST_DEVELOPMENT_MONTH)
    # The ranking at the 2025-12 month end uses December 2025 bars (development data)
    # but decides January 2026 holdings, so it belongs to both periods' candidates.
    holdout_months = months_between(LAST_DEVELOPMENT_MONTH, "2026-09")
    dev_ranking = rank_month_ends(volumes, dev_months, CANDIDATE_DEPTH)
    holdout_ranking = rank_month_ends(volumes, holdout_months, CANDIDATE_DEPTH)
    dev_candidates = candidate_set(dev_ranking)
    holdout_candidates = candidate_set(holdout_ranking)
    leveraged = leveraged_bases(base_asset(s) for s in symbols)
    first_bar = {symbol: min(series) for symbol, series in volumes.items() if series}
    history = history_milestones(dev_ranking, volumes, first_bar)
    result = {
        "generated_at": _now(),
        "source": "data.binance.vision, spot monthly 1d klines, checksum-verified",
        "licence": LICENCE,
        "rule": (
            "At each month end, USDT spot pairs ranked by quote volume over the 30 daily "
            "bars closing at that month end; stablecoins and fiat excluded "
            "(norse_research.universe.STABLE_OR_FIAT); delisted pairs included. "
            f"Candidates are the top {CANDIDATE_DEPTH}, a superset of the top five."
        ),
        "usdt_pairs_listed": len(symbols),
        "usdt_pairs_ranked": len(ranked),
        "excluded_stable_or_fiat": sorted(set(symbols) - set(ranked)),
        "first_month_end": dev_months[0],
        "history_milestones": history,
        "candidate_first_daily_bar": {
            s: iso_time(first_bar[s])
            for s in sorted(set(dev_candidates) | set(holdout_candidates) | set(H2_SYMBOLS))
        },
        "development_ranking": dev_ranking,
        "development_candidates": dev_candidates,
        # Holdout month ends are summarised as a set only, never ranked in public.
        "holdout_only_candidates": sorted(set(holdout_candidates) - set(dev_candidates)),
        "leveraged_token_candidates": sorted(
            s for s in set(dev_candidates) | set(holdout_candidates) if base_asset(s) in leveraged
        ),
    }
    COVERAGE_DIR.mkdir(parents=True, exist_ok=True)
    UNIVERSE_FILE.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    log(f"wrote {UNIVERSE_FILE}: {len(dev_candidates)} development candidates")
    return result


def candidate_specs(store: ArchiveStore, universe: dict) -> list[DatasetSpec]:
    """Every series G1.1 loads: the H1 candidates' and H2's, perpetuals where listed."""
    symbols = sorted(
        set(universe["development_candidates"])
        | set(universe["holdout_only_candidates"])
        | set(H2_SYMBOLS)
    )
    perps = set(store.subdirectories(FUNDING_ROOT))
    specs = []
    for symbol in symbols:
        specs.extend(DatasetSpec("spot", "klines", symbol, i) for i in BAR_INTERVALS)
        if symbol in perps:
            specs.append(DatasetSpec("um", "fundingRate", symbol))
            specs.extend(
                DatasetSpec("um", "premiumIndexKlines", symbol, i) for i in ("1d", "4h")
            )
        if symbol in H2_SYMBOLS:
            specs.append(DatasetSpec("um", "premiumIndexKlines", symbol, "15m"))
            specs.extend(DatasetSpec("um", "klines", symbol, i) for i in BAR_INTERVALS)
    return specs


def run_sync(store: ArchiveStore, log: Log) -> None:
    universe = json.loads(UNIVERSE_FILE.read_text())
    mirror_all(store, candidate_specs(store, universe), log)


def _gap_record(gap: Gap) -> dict:
    return {"after": iso_time(gap.after), "before": iso_time(gap.before), "missing": gap.missing}


def measure(store: ArchiveStore, spec: DatasetSpec) -> dict:
    """Coverage of one series: listing span, missing months, rows and gaps per period."""
    listed = listed_months(store, spec)
    record: dict = {
        "dataset": spec.name,
        "listed_first_month": listed[0] if listed else None,
        "listed_last_month": listed[-1] if listed else None,
        "missing_months": [],
    }
    if not listed:
        return record
    in_scope = [m for m in listed if m <= LAST_HOLDOUT_MONTH]
    record["missing_months"] = sorted(
        set(months_between(listed[0], listed[-1])) - set(listed)
    )
    for period in (DEVELOPMENT, HOLDOUT):
        months = [m for m in in_scope if period_of_month(m) == period]
        rows = [r for m in months for r in read_rows(store, spec, m)]
        if not rows:
            record[period] = None
            continue
        times = [r[0] for r in rows]
        gaps = (
            bar_gaps(times, spec.step_ms, spec.name) if spec.is_bars else funding_gaps(rows, spec.name)
        )
        record[period] = {
            "files": len(months),
            "rows": len(rows),
            "first": iso_time(times[0]),
            "last": iso_time(times[-1]),
            "gaps": len(gaps),
            "missing_rows": sum(g.missing for g in gaps),
            "gap_list": [_gap_record(g) for g in gaps[:50]],
        }
        if not spec.is_bars:
            record[period]["intervals_hours"] = sorted({r.interval_hours for r in rows})
    return record


def run_report(store: ArchiveStore, log: Log) -> dict:
    universe = json.loads(UNIVERSE_FILE.read_text())
    specs = candidate_specs(store, universe)
    measured = [measure(store, spec) for spec in specs]
    report = {
        "generated_at": _now(),
        "licence": LICENCE,
        "last_development_month": LAST_DEVELOPMENT_MONTH,
        "last_holdout_month": LAST_HOLDOUT_MONTH,
        "datasets": measured,
    }
    COVERAGE_FILE.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    log(f"wrote {COVERAGE_FILE}: {len(measured)} series")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=("universe", "sync", "report"))
    parser.add_argument("--offline", action="store_true", help="use the mirror only")
    args = parser.parse_args(argv)
    fetch = None if args.offline else HttpFetcher()
    store = ArchiveStore(datasets_root(), fetch)

    def log(message: str) -> None:
        print(message, flush=True)

    {"universe": run_universe, "sync": run_sync, "report": run_report}[args.step](store, log)
    if fetch is not None:
        log(
            f"network: {fetch.requests} requests, {fetch.bytes_received:,} bytes; "
            f"new files: {store.downloaded_files}, {store.downloaded_bytes:,} bytes"
        )
        fetch.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
