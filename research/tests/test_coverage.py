import hashlib
import json

from conftest import make_zip
from norse_research import coverage
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


def test_measure_reports_span_missing_months_gaps_and_the_period_split(tmp_path):
    store = ArchiveStore(tmp_path)
    spec = DatasetSpec("spot", "klines", "XUSDT", "1d")
    months = ["2025-10", "2025-12", "2026-01", "2026-11"]
    for month in months:
        times = days(month)
        if month == "2025-12":
            times = times[:10] + times[12:]  # two missing days
        if month == "2026-01":
            times = times[:9] + [times[9]] + times[9:]  # one row published twice
        put(store, spec, month, times)
    write_listing(store, spec, months)

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
    assert dev["repeated_rows"] == []
    holdout = record["holdout"]
    assert holdout["files"] == 1 and holdout["rows"] == 31 and holdout["gaps"] == 0
    assert holdout["repeated_rows"] == ["2026-01-10 00:00"]


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
    prefixes = [coverage.FUNDING_ROOT + s + "/" for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")]
    record = {"prefix": coverage.FUNDING_ROOT, "listed_at": "x", "objects": []}
    record["prefixes"] = prefixes
    (tmp_path / LISTINGS_DIR / name).write_text(json.dumps(record))
    universe = {"development_candidates": ["BTCUSDT", "SOLUSDT", "LUNAUSDT"]}
    universe["holdout_only_candidates"] = []
    names = {s.name for s in coverage.candidate_specs(store, universe)}
    assert "spot/klines/LUNAUSDT/15m" in names
    assert "um/fundingRate/LUNAUSDT" not in names  # no perpetual listed
    assert "um/fundingRate/SOLUSDT" in names and "um/premiumIndexKlines/SOLUSDT/4h" in names
    assert "um/klines/SOLUSDT/15m" not in names
    assert "um/klines/ETHUSDT/15m" in names and "spot/klines/ETHUSDT/1d" in names
