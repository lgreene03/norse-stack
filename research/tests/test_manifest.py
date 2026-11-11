import hashlib
import json

import pytest
from norse_research.formats import LICENCE
from norse_research.manifest import Manifest, ManifestError, build, dumps, verify, write
from norse_research.periods import DEVELOPMENT, HOLDOUT
from norse_research.store import ArchiveStore
from synthetic_store import make_series, month_times, put


@pytest.fixture
def mirrored(tmp_path):
    store = ArchiveStore(tmp_path / "mirror")
    spec_x, months_x = make_series(store, "spot/klines/XUSDT/1d", "2025-11", "2026-02")
    dropped = month_times(spec_x, "2025-12")[5]
    spec_y, months_y = make_series(
        store, "spot/klines/YUSDT/1d", "2025-12", "2026-01", drop={dropped}
    )
    fund, fund_months = make_series(store, "um/fundingRate/XUSDT", "2025-12", "2026-01")
    series = [(spec_x, months_x), (spec_y, months_y), (fund, fund_months)]
    return store, series, tmp_path


def test_manifests_split_the_periods_and_pin_every_file(mirrored):
    store, series, _ = mirrored
    dev = build(store, DEVELOPMENT, series)
    hold = build(store, HOLDOUT, series)
    x_dev = dev["datasets"]["spot/klines/XUSDT/1d"]
    x_hold = hold["datasets"]["spot/klines/XUSDT/1d"]
    assert [f[0] for f in x_dev["files"]] == ["2025-11", "2025-12"]
    assert [f[0] for f in x_hold["files"]] == ["2026-01", "2026-02"]
    assert dev["licence"] == hold["licence"] == LICENCE
    assert x_dev["rows"] == 30 + 31
    assert all(len(f[1]) == 64 and f[2] > 0 for f in x_dev["files"])
    assert dev["window"]["until_exclusive"] == "2026-01-01T00:00:00+00:00"
    assert hold["window"]["from"] == "2026-01-01T00:00:00+00:00"


def test_gaps_are_recorded_with_the_series(mirrored):
    store, series, _ = mirrored
    dev = build(store, DEVELOPMENT, series)
    assert dev["datasets"]["spot/klines/YUSDT/1d"]["gaps"] == [
        ["2025-12-05 00:00", "2025-12-07 00:00", 1]
    ]
    assert dev["datasets"]["um/fundingRate/XUSDT"]["gaps"] == []


def test_rows_published_twice_are_recorded_and_counted_once(tmp_path):
    store = ArchiveStore(tmp_path)
    spec, months = make_series(store, "spot/klines/RUSDT/1d", "2025-12", "2025-12")
    times = month_times(spec, "2025-12")
    put(store, spec, "2025-12", [*times[:9], times[9], *times[9:]])
    entry = build(store, DEVELOPMENT, [(spec, months)])["datasets"]["spot/klines/RUSDT/1d"]
    assert entry["repeated"] == ["2025-12-10 00:00"]
    assert entry["rows"] == 31 and entry["files"][0][3] == 31 and entry["gaps"] == []


def test_dumps_is_deterministic_and_valid_json(mirrored):
    store, series, _ = mirrored
    first = dumps(build(store, DEVELOPMENT, series))
    second = dumps(build(store, DEVELOPMENT, list(reversed(series))))
    assert first == second
    assert json.loads(first)["schema"] == "norse-dataset-manifest/1"


def test_load_and_verify_round_trip_and_catch_tampering(mirrored):
    store, series, tmp = mirrored
    path = tmp / "dev.json"
    digest = write(build(store, DEVELOPMENT, series), path)
    manifest = Manifest.load(path)
    assert manifest.sha256 == digest and manifest.period == DEVELOPMENT
    assert verify(manifest, store) == 2 + 1 + 1
    doc = json.loads(path.read_text())
    doc["datasets"]["spot/klines/XUSDT/1d"]["files"][0][3] += 1
    path.write_bytes(dumps(doc))
    with pytest.raises(ManifestError, match="does not match"):
        verify(Manifest.load(path), store)


def test_a_development_manifest_holding_holdout_months_is_refused(mirrored):
    store, series, tmp = mirrored
    doc = build(store, DEVELOPMENT, series)
    doc["datasets"]["spot/klines/XUSDT/1d"]["files"].append(["2026-01", "a" * 64, 10, 31])
    path = tmp / "bad.json"
    path.write_bytes(dumps(doc))
    with pytest.raises(ManifestError, match="outside the development period"):
        Manifest.load(path)
    doc["schema"] = "something-else"
    path.write_bytes(dumps(doc))
    with pytest.raises(ManifestError, match="schema"):
        Manifest.load(path)


def test_files_over_the_size_cap_are_refused(mirrored):
    store, series, _ = mirrored
    spec = series[0][0]
    path = store.path(spec.key("2025-11"))
    big = path.read_bytes() + b"\0" * (spec.max_bytes() + 1)
    path.write_bytes(big)
    digest = hashlib.sha256(big).hexdigest()
    path.with_name(path.name + ".CHECKSUM").write_text(f"{digest}  {path.name}\n")
    with pytest.raises(ManifestError, match="exceeds cap"):
        build(store, DEVELOPMENT, series)
