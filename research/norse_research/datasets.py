"""Dataset sync, manifests and verification (goal G1.1).

Run from research/ with NORSE_DATASETS_DIR set (or present in norse-stack's .env):

``python -m norse_research.datasets sync``        mirror the development months of
                                                  every series G1.1 loads
``python -m norse_research.datasets manifest``    rewrite the development manifest
``python -m norse_research.datasets verify``      re-verify every development file, and
                                                  check the frozen holdout manifest
``python -m norse_research.datasets rest-check``  compare 24 windows with the API
``python -m norse_research.datasets rest-sweep``  compare every BTC and ETH bar, and
                                                  rewrite the register of differences

The holdout's files are kept off disk until the unseal (G1.6), which fetches them again
and verifies each against the committed holdout manifest. That manifest is frozen until
then: no step here rewrites it or fetches a holdout month
(docs/research/ledger-threat-model.md, option B).

The series are the universe input (daily klines of every ranked USDT spot pair,
which the dated universe is computed from), and for every pair that universe can
select, delisted ones included, plus H2's BTC and ETH: spot daily, four-hour and
15-minute klines, and where a USDT-margined perpetual exists, funding and premium
index (and perpetual klines for BTC and ETH). The candidates come from
research/coverage/universe-candidates.json (goal G0.6).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .binance_archive import Fetch, HttpFetcher
from .coverage import (
    SPOT_KLINES_ROOT,
    UNIVERSE_FILE,
    candidate_specs,
    development_months,
    mirror_all,
)
from .formats import DatasetSpec
from .loader import DatasetLoader
from .manifest import (
    MANIFEST_DIR,
    MANIFEST_FILES,
    Manifest,
    build,
    frozen_holdout_manifest,
    pinned_files_on_disk,
    verify,
    write,
)
from .periods import DEVELOPMENT, HOLDOUT, HOLDOUT_START_MS, iso_time
from .rest_check import REST_LIMIT, compare, fetch_rest_klines
from .store import ArchiveStore, datasets_root
from .universe import is_ranked

# Development windows the live REST check compares, spread across the archive's eras:
# early spot, the 2021 peak, late millisecond files and microsecond files from 2025.
REST_WINDOWS = ("2018-03", "2021-05", "2024-08", "2025-06")
REST_SERIES = ("BTCUSDT", "ETHUSDT")
# Every development bar of BTC and ETH where the archive and the REST API disagree,
# written by `rest-sweep`. A difference not in it fails the live check.
REST_REGISTER = MANIFEST_DIR / "rest-differences.json"


def series_specs(store: ArchiveStore) -> list[DatasetSpec]:
    universe = json.loads(UNIVERSE_FILE.read_text())
    symbols = [s for s in store.subdirectories(SPOT_KLINES_ROOT) if s.endswith("USDT")]
    specs = {
        spec.name: spec
        for spec in [DatasetSpec("spot", "klines", s, "1d") for s in symbols if is_ranked(s)]
        + candidate_specs(store, universe)
    }
    return [specs[name] for name in sorted(specs)]


def series_months(store: ArchiveStore, specs: list[DatasetSpec]) -> list[tuple[DatasetSpec, list]]:
    """Each series with its development months, the only ones kept on disk."""
    return [(spec, development_months(store, spec)) for spec in specs]


def _register_entry(problem: str) -> list[str]:
    """``bar at <ms>: <what>`` as ``[<UTC minute>, <what>]``; other messages unchanged."""
    head, sep, what = problem.partition(": ")
    if sep and head.startswith("bar at ") and head[7:].isdigit():
        return [iso_time(int(head[7:])), what]
    return ["", problem]


def load_register(path: Path = REST_REGISTER) -> dict[str, set[tuple[str, str]]]:
    doc = json.loads(path.read_text())
    return {name: {tuple(d) for d in s["differences"]} for name, s in doc["series"].items()}


def _loader() -> DatasetLoader:
    return DatasetLoader(Manifest.load(MANIFEST_FILES[DEVELOPMENT]), ArchiveStore(datasets_root()))


def _compare_window(loader, fetch, name: str, start_ms: int, end_ms: int, now_ms: int):
    spec = DatasetSpec.from_name(name)
    window = (
        datetime.fromtimestamp(start_ms / 1000, tz=UTC),
        datetime.fromtimestamp(end_ms / 1000, tz=UTC),
    )
    archive = loader.load(name, *window, accept_recorded_gaps=True).rows
    rest = fetch_rest_klines(spec.symbol, spec.interval, start_ms, end_ms, fetch, now_ms)
    return len(rest), [_register_entry(p) for p in compare(archive, rest)]


def live_rest_check(fetch: Fetch) -> dict[str, tuple[int, list[list[str]]]]:
    """Compare closed REST klines with manifested development rows in 24 windows.

    Returns, per window, the bars compared and the differences found; never a price.
    """
    loader = _loader()
    now_ms = int(time.time() * 1000)
    results = {}
    for symbol in REST_SERIES:
        for interval, days in (("1d", 31), ("4h", 31), ("15m", 10)):
            for month in REST_WINDOWS:
                start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
                start_ms = int(start.timestamp() * 1000)
                end_ms = start_ms + days * 86_400_000
                name = f"spot/klines/{symbol}/{interval}"
                results[f"{name} {month}"] = _compare_window(
                    loader, fetch, name, start_ms, end_ms, now_ms
                )
    return results


def rest_sweep(fetch: Fetch, log) -> dict:
    """Compare every development bar of BTC and ETH spot (daily, four-hour and
    15-minute) with the REST API, 1,000 bars a request, about 630 requests in all.
    Returns the register document; it holds bar times and field names, never a price."""
    loader = _loader()
    now_ms = int(time.time() * 1000)
    series = {}
    for symbol in REST_SERIES:
        for interval in ("1d", "4h", "15m"):
            name = f"spot/klines/{symbol}/{interval}"
            step = DatasetSpec.from_name(name).step_ms
            first = datetime.strptime(loader.manifest.entry(name)["first"], "%Y-%m-%d %H:%M")
            start_ms = int(first.replace(tzinfo=UTC).timestamp() * 1000)
            bars, differences = 0, []
            while start_ms < HOLDOUT_START_MS:
                end_ms = min(start_ms + REST_LIMIT * step, HOLDOUT_START_MS)
                count, found = _compare_window(loader, fetch, name, start_ms, end_ms, now_ms)
                bars += count
                differences.extend(found)
                start_ms = end_ms
            series[name] = {"bars_compared": bars, "differences": differences}
            log(f"{name}: {bars} closed bars compared, {len(differences)} differences")
    return {
        "about": (
            "Development bars where data.binance.vision and Binance's public REST API "
            "(data-api.binance.vision) disagree, from `python -m norse_research.datasets "
            "rest-sweep`. Times are bar open times, UTC."
        ),
        "manifest_sha256": loader.manifest.sha256,
        "series": series,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "step", choices=("sync", "manifest", "verify", "rest-check", "rest-sweep")
    )
    args = parser.parse_args(argv)
    # The REST API counts request weight per minute; four requests a second stays far
    # below Binance's limit for this public endpoint.
    rest = args.step in ("rest-check", "rest-sweep")
    fetch = HttpFetcher(min_interval=0.25 if rest else 0.1) if rest or args.step == "sync" else None
    store = ArchiveStore(datasets_root(), fetch if args.step == "sync" else None)
    status = 0

    def log(message: str) -> None:
        print(message, flush=True)

    if args.step == "sync":
        mirror_all(store, series_specs(store), log)
        log(f"new files: {store.downloaded_files}, {store.downloaded_bytes:,} bytes")
    elif args.step == "manifest":
        doc = build(store, DEVELOPMENT, series_months(store, series_specs(store)))
        digest = write(doc, MANIFEST_FILES[DEVELOPMENT])
        files = sum(len(e["files"]) for e in doc["datasets"].values())
        size = sum(f[2] for e in doc["datasets"].values() for f in e["files"])
        log(
            f"{DEVELOPMENT}: {len(doc['datasets'])} series, {files} files, {size:,} bytes, "
            f"sha256 {digest}"
        )
        log(f"{HOLDOUT}: not rebuilt, the committed manifest is frozen until the unseal (G1.6)")
    elif args.step == "verify":
        manifest = Manifest.load(MANIFEST_FILES[DEVELOPMENT])
        log(f"{DEVELOPMENT}: {verify(manifest, store)} files match, sha256 {manifest.sha256}")
        holdout = frozen_holdout_manifest(MANIFEST_FILES[HOLDOUT])
        pinned = sum(len(e["files"]) for e in holdout.datasets.values())
        present = pinned_files_on_disk(holdout, store)
        log(f"{HOLDOUT}: committed manifest unchanged, sha256 {holdout.sha256}")
        if present:
            log(
                f"{HOLDOUT}: {present} of its {pinned} files are on disk; they must stay off "
                "disk until the unseal (G1.6)"
            )
            status = 1
        else:
            log(f"{HOLDOUT}: its {pinned} files are off disk until the unseal (G1.6), not verified")
    elif args.step == "rest-sweep":
        doc = rest_sweep(fetch, log)
        REST_REGISTER.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        log(f"wrote {REST_REGISTER}")
    else:
        register = load_register()
        results = live_rest_check(fetch)
        unknown = {
            window: [d for d in found if tuple(d) not in register.get(window.split()[0], set())]
            for window, (_, found) in results.items()
        }
        unknown = {k: v for k, v in unknown.items() if v}
        bars = sum(v[0] for v in results.values())
        known = sum(len(v[1]) for v in results.values()) - sum(len(v) for v in unknown.values())
        log(
            f"{len(results)} windows, {bars} closed bars compared, {known} recorded "
            f"differences, {len(unknown)} windows with unrecorded ones"
        )
        for window, found in sorted(unknown.items()):
            log(f"  {window}: {found[:3]}")
        status = 1 if unknown else 0
    if fetch is not None:
        fetch.close()
    return status


if __name__ == "__main__":
    sys.exit(main())
