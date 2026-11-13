import hashlib
import json

import pytest
import synthetic_store as synth
from conftest import make_zip
from norse_research import coverage
from norse_research.binance_archive import DOWNLOAD_URL
from norse_research.formats import INTERVAL_MS, DatasetSpec
from norse_research.store import LISTINGS_DIR, ArchiveStore
from norse_research.universe import month_close_ms

DAY = INTERVAL_MS["1d"]


def put(store, spec, month, open_times):
    lines = [f"{t},1,1,1,1,1,{t + DAY - 1},1,1,1,1,0" for t in open_times]
    data = make_zip(spec.file_name(month)[:-4] + ".csv", "\n".join(lines) + "\n")
    path = store.path(spec.key(month))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_name(path.name + ".CHECKSUM").write_text(f"{digest}  {path.name}\n")


def write_listing(store, spec, months):
    name = spec.prefix.strip("/").replace("/", "__") + ".json"
    target = store.root / LISTINGS_DIR / name
    target.parent.mkdir(parents=True, exist_ok=True)
    objects = [{"key": spec.key(m), "size": 1} for m in months]
    record = {"prefix": spec.prefix, "listed_at": "2026-11-11T00:00:00Z"}
    record |= {"objects": objects, "prefixes": []}
    target.write_text(json.dumps(record))


def days(month):
    end = month_close_ms(month)
    start = month_close_ms(_previous(month))
    return list(range(start, end, DAY))


def _previous(month):
    year, mon = int(month[:4]), int(month[5:])
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def test_measure_reports_span_missing_months_and_gaps_without_holdout_files(tmp_path):
    store = ArchiveStore(tmp_path)
    spec = DatasetSpec("spot", "klines", "XUSDT", "1d")
    on_disk = ["2025-10", "2025-12"]
    for month in on_disk:
        times = days(month)
        if month == "2025-10":
            times = [*times[:9], times[9], *times[9:]]  # one row published twice
        if month == "2025-12":
            times = times[:10] + times[12:]  # two missing days
        put(store, spec, month, times)
    # The archive lists a holdout month and a later one; neither is on disk.
    write_listing(store, spec, [*on_disk, "2026-01", "2026-11"])

    record = coverage.measure(store, spec)

    assert record["listed_first_month"] == "2025-10"
    assert record["listed_last_month"] == "2026-11"
    assert "2025-11" in record["missing_months"]
    dev = record["development"]
    assert dev["files"] == 2
    assert dev["rows"] == 31 + 29
    # The missing November shows as one gap too, at the row level.
    assert dev["gaps"] == 2
    assert dev["missing_rows"] == 30 + 2
    assert dev["first"] == "2025-10-01 00:00" and dev["last"] == "2025-12-31 00:00"
    assert dev["repeated_rows"] == ["2025-10-10 00:00"]
    assert record["holdout"] == coverage.HOLDOUT_OFF_DISK
    assert coverage.measure(store, spec, holdout=None)["holdout"] is None


def test_sync_does_not_mirror_holdout_months_before_the_unseal(tmp_path):
    archive = ArchiveStore(tmp_path / "archive")
    spec, months = synth.make_series(archive, "spot/klines/XUSDT/1d", "2025-11", "2026-02")
    synth.put(archive, spec, "2026-11", synth.month_times(spec, "2026-11"))
    requested = []

    def fetch(url, max_bytes):
        requested.append(url.removeprefix(DOWNLOAD_URL))
        return archive.path(requested[-1]).read_bytes()

    store = ArchiveStore(tmp_path / "mirror", fetch)
    synth.write_listing(store, spec, [*months, "2026-11"])

    mirrored = coverage.mirror_all(store, [spec], lambda _: None)

    assert mirrored == {spec.name: ["2025-11", "2025-12"]}
    assert sorted({key.removesuffix(".CHECKSUM") for key in requested}) == [
        spec.key("2025-11"),
        spec.key("2025-12"),
    ]
    for month in ("2026-01", "2026-02", "2026-11"):
        assert not store.path(spec.key(month)).exists()


def write_root_listing(store, prefix, symbols):
    name = prefix.strip("/").replace("/", "__") + ".json"
    target = store.root / LISTINGS_DIR / name
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"prefix": prefix, "listed_at": "2026-11-11T00:00:00Z", "objects": []}
    record["prefixes"] = [f"{prefix}{s}/" for s in symbols]
    target.write_text(json.dumps(record))


def test_universe_ranks_development_and_carries_the_frozen_holdout_candidates(
    tmp_path, monkeypatch
):
    universe_file = tmp_path / "universe-candidates.json"
    monkeypatch.setattr(coverage, "UNIVERSE_FILE", universe_file)
    monkeypatch.setattr(coverage, "COVERAGE_DIR", tmp_path)

    def fetch(url, max_bytes):
        raise AssertionError(f"fetched {url}")

    store = ArchiveStore(tmp_path / "mirror", fetch)
    # Six pairs with development bars, and a holdout month the archive lists but which
    # is off disk. NEWUSDT was first listed in the holdout: no development file at all.
    pairs = ["AAAUSDT", "BBBUSDT", "BTCUSDT", "CCCUSDT", "DDDUSDT", "ETHUSDT"]
    for symbol in pairs:
        spec, months = synth.make_series(store, f"spot/klines/{symbol}/1d", "2025-11", "2025-12")
        synth.write_listing(store, spec, [*months, "2026-01"])
    synth.write_listing(store, DatasetSpec("spot", "klines", "NEWUSDT", "1d"), ["2026-02"])
    write_root_listing(store, coverage.SPOT_KLINES_ROOT, [*pairs, "NEWUSDT"])

    with pytest.raises(RuntimeError, match="off disk until the unseal"):
        coverage.run_universe(store, lambda _: None)

    committed = {"holdout_only_candidates": ["NEWUSDT"]}
    committed["candidate_first_daily_bar"] = {"NEWUSDT": "2026-02-01 00:00"}
    universe_file.write_text(json.dumps(committed))
    result = coverage.run_universe(store, lambda _: None)

    assert result["development_candidates"] == pairs
    assert result["holdout_only_candidates"] == ["NEWUSDT"]
    assert result["candidate_first_daily_bar"]["NEWUSDT"] == "2026-02-01 00:00"
    assert result["candidate_first_daily_bar"]["AAAUSDT"] == "2025-11-01 00:00"
    assert json.loads(universe_file.read_text())["holdout_only_candidates"] == ["NEWUSDT"]


def test_report_carries_the_committed_holdout_coverage_forward(tmp_path, monkeypatch):
    store = ArchiveStore(tmp_path / "mirror")
    kept, months = synth.make_series(store, "spot/klines/XUSDT/1d", "2025-12", "2025-12")
    added, _ = synth.make_series(store, "spot/klines/YUSDT/1d", "2025-12", "2025-12")
    for spec in (kept, added):
        synth.write_listing(store, spec, [*months, "2026-01"])
    report = tmp_path / "series-coverage.json"
    frozen = {"files": 1, "rows": 31, "first": "2026-01-01 00:00", "last": "2026-01-31 00:00"}
    report.write_text(json.dumps({"datasets": [{"dataset": kept.name, "holdout": frozen}]}))
    universe = tmp_path / "universe-candidates.json"
    universe.write_text("{}")
    monkeypatch.setattr(coverage, "COVERAGE_FILE", report)
    monkeypatch.setattr(coverage, "UNIVERSE_FILE", universe)
    monkeypatch.setattr(coverage, "candidate_specs", lambda store, universe: [kept, added])

    coverage.run_report(store, lambda _: None)

    written = {r["dataset"]: r for r in json.loads(report.read_text())["datasets"]}
    assert written[kept.name]["holdout"] == frozen
    assert written[added.name]["holdout"] == coverage.HOLDOUT_OFF_DISK
    assert written[kept.name]["development"]["rows"] == 31


def test_measure_handles_an_absent_series(tmp_path):
    store = ArchiveStore(tmp_path)
    spec = DatasetSpec("um", "fundingRate", "XUSDT")
    write_listing(store, spec, [])
    assert coverage.measure(store, spec)["listed_first_month"] is None


def test_history_milestones_distinguish_ranked_from_signal_ready():
    # Six pairs: four old ones, two listed in March 2024 that trade heavily at once.
    old_first = month_close_ms("2023-06")
    new_first = month_close_ms("2024-02")
    first_bar = {f"O{i}USDT": old_first for i in range(4)}
    first_bar |= {"N1USDT": new_first, "N2USDT": new_first}
    months = ["2024-03", "2024-04", "2024-05", "2024-06", "2024-07"]
    volumes = {}
    for symbol, first in first_bar.items():
        heavy = symbol.startswith("N")
        volumes[symbol] = {
            t: (100.0 if heavy else 1.0)
            for t in range(first, month_close_ms("2024-07"), DAY)
        }
    from norse_research.universe import rank_month_ends

    ranking = rank_month_ends(volumes, months, depth=10)
    got = coverage.history_milestones(ranking, volumes, first_bar)
    # Five pairs trade from March, but the two new ones lead the ranking without
    # 120 days of history until their 120th day falls before a month end (June 30).
    assert got["five_ranked"] == "2024-03"
    assert got["five_with_history"] == "2024-06"
    assert got["top_five_all_with_history"] == "2024-06"
    assert got["top_five_all_with_history"] >= got["five_with_history"]


def test_candidate_specs_cover_h1_candidates_and_h2_perpetuals(tmp_path):
    store = ArchiveStore(tmp_path)
    name = coverage.FUNDING_ROOT.strip("/").replace("/", "__") + ".json"
    (tmp_path / LISTINGS_DIR).mkdir()
    perps = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "1000PEPEUSDT")
    prefixes = [coverage.FUNDING_ROOT + s + "/" for s in perps]
    record = {"prefix": coverage.FUNDING_ROOT, "listed_at": "x", "objects": []}
    record["prefixes"] = prefixes
    (tmp_path / LISTINGS_DIR / name).write_text(json.dumps(record))
    universe = {"development_candidates": ["BTCUSDT", "SOLUSDT", "LUNAUSDT", "PEPEUSDT"]}
    universe["holdout_only_candidates"] = []
    names = {s.name for s in coverage.candidate_specs(store, universe)}
    # Spot PEPEUSDT's perpetual trades as a multiple: 1000PEPEUSDT.
    assert "um/fundingRate/1000PEPEUSDT" in names and "um/fundingRate/PEPEUSDT" not in names
    assert "spot/klines/LUNAUSDT/15m" in names
    assert "um/fundingRate/LUNAUSDT" not in names  # no perpetual listed
    assert "um/fundingRate/SOLUSDT" in names and "um/premiumIndexKlines/SOLUSDT/4h" in names
    assert "um/klines/SOLUSDT/15m" not in names
    assert "um/klines/ETHUSDT/15m" in names and "spot/klines/ETHUSDT/1d" in names
