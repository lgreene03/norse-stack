import json
from datetime import UTC, datetime

import pytest
from norse_research.formats import DatasetSpec
from norse_research.loader import DatasetLoader, GapError, HoldoutSealed, MissingData
from norse_research.manifest import Manifest, ManifestError, build, dumps, write
from norse_research.periods import DEVELOPMENT, HOLDOUT, HOLDOUT_START_MS
from norse_research.store import ArchiveStore
from synthetic_store import make_series, month_times, put


def utc(*args):
    return datetime(*args, tzinfo=UTC)


@pytest.fixture
def world(tmp_path):
    store = ArchiveStore(tmp_path / "mirror")
    x = make_series(store, "spot/klines/XUSDT/1d", "2025-10", "2026-01")
    dropped = month_times(DatasetSpec.from_name("spot/klines/GUSDT/4h"), "2025-12")[20]
    g = make_series(store, "spot/klines/GUSDT/4h", "2025-11", "2025-12", drop={dropped})
    f = make_series(store, "um/fundingRate/XUSDT", "2025-11", "2025-12")
    series = [x, g, f]
    paths = {}
    for period in (DEVELOPMENT, HOLDOUT):
        paths[period] = tmp_path / f"{period}.json"
        write(build(store, period, series), paths[period])
    loader = DatasetLoader(Manifest.load(paths[DEVELOPMENT]), store)
    return store, paths, loader


def test_the_holdout_manifest_cannot_be_loaded(world):
    store, paths, _ = world
    with pytest.raises(HoldoutSealed, match=r"G1\.6"):
        DatasetLoader(Manifest.load(paths[HOLDOUT]), store)


def test_requests_reaching_into_the_holdout_are_refused(world):
    _, _, loader = world
    with pytest.raises(HoldoutSealed):
        loader.load("spot/klines/XUSDT/1d", utc(2025, 12, 1), utc(2026, 1, 2))
    with pytest.raises(HoldoutSealed):
        loader.load("spot/klines/XUSDT/1d", utc(2026, 1, 1), utc(2026, 1, 5))
    rows = loader.load("spot/klines/XUSDT/1d", utc(2025, 12, 1), utc(2026, 1, 1)).rows
    assert rows[-1].open_time == HOLDOUT_START_MS - 86_400_000
    assert all(r.open_time < HOLDOUT_START_MS for r in rows)


def test_load_returns_rows_in_range_with_the_manifest_hash(world):
    _, paths, loader = world
    result = loader.load("spot/klines/XUSDT/1d", utc(2025, 10, 15), utc(2025, 12, 1))
    assert len(result.rows) == 17 + 30
    assert result.rows[0].open_time == int(utc(2025, 10, 15).timestamp() * 1000)
    assert result.manifest_sha256 == Manifest.load(paths[DEVELOPMENT]).sha256
    x = DatasetSpec.from_name("spot/klines/XUSDT/1d")
    assert result.files == [x.key("2025-10"), x.key("2025-11")]
    assert result.gaps == []


def test_unmanifested_series_and_months_fail(world):
    _, _, loader = world
    with pytest.raises(ManifestError, match="not in manifest"):
        loader.load("spot/klines/ZUSDT/1d", utc(2025, 11, 1), utc(2025, 12, 1))
    with pytest.raises(MissingData, match="2025-09"):
        loader.load("spot/klines/XUSDT/1d", utc(2025, 9, 20), utc(2025, 10, 5))


def test_a_gap_fails_the_load_unless_recorded_and_accepted(world):
    _, _, loader = world
    name = "spot/klines/GUSDT/4h"
    with pytest.raises(GapError, match="1 gap"):
        loader.load(name, utc(2025, 12, 1), utc(2025, 12, 10))
    result = loader.load(name, utc(2025, 12, 1), utc(2025, 12, 10), accept_recorded_gaps=True)
    assert len(result.gaps) == 1 and result.gaps[0].missing == 1
    assert len(result.rows) == 9 * 6 - 1
    clean = loader.load(name, utc(2025, 12, 10), utc(2025, 12, 20))
    assert clean.gaps == [] and len(clean.rows) == 60


def test_a_gap_the_manifest_does_not_record_fails_even_when_accepting(world):
    store, paths, _ = world
    doc = json.loads(paths[DEVELOPMENT].read_text())
    doc["datasets"]["spot/klines/GUSDT/4h"]["gaps"] = []
    paths[DEVELOPMENT].write_bytes(dumps(doc))
    loader = DatasetLoader(Manifest.load(paths[DEVELOPMENT]), store)
    with pytest.raises(GapError):
        loader.load(
            "spot/klines/GUSDT/4h", utc(2025, 12, 1), utc(2025, 12, 10), accept_recorded_gaps=True
        )


def test_a_file_that_differs_from_the_manifest_is_refused(world):
    store, _, loader = world
    x = DatasetSpec.from_name("spot/klines/XUSDT/1d")
    put(store, x, "2025-11", month_times(x, "2025-11")[:-1])  # re-published, one row short
    with pytest.raises(ManifestError, match="does not match the manifest"):
        loader.load("spot/klines/XUSDT/1d", utc(2025, 11, 1), utc(2025, 11, 10))


def test_a_repeat_is_collapsed_only_where_the_manifest_records_it(tmp_path):
    store = ArchiveStore(tmp_path / "mirror")
    spec, months = make_series(store, "spot/klines/RUSDT/1d", "2025-12", "2025-12")
    times = month_times(spec, "2025-12")
    put(store, spec, "2025-12", [*times[:9], times[9], *times[9:]])
    path = tmp_path / "dev.json"
    write(build(store, DEVELOPMENT, [(spec, months)]), path)
    rows = DatasetLoader(Manifest.load(path), store).load(
        spec.name, utc(2025, 12, 1), utc(2026, 1, 1)
    ).rows
    assert len(rows) == 31
    doc = json.loads(path.read_text())
    doc["datasets"][spec.name]["repeated"] = []
    path.write_bytes(dumps(doc))
    with pytest.raises(ManifestError, match="repeated"):
        DatasetLoader(Manifest.load(path), store).load(spec.name, utc(2025, 12, 1), utc(2026, 1, 1))


def test_off_grid_bars_are_never_returned_and_must_be_recorded(tmp_path):
    # As after the 2018-02-08 outage: four bars restart 8 min 16.09 s off the grid.
    store = ArchiveStore(tmp_path / "mirror")
    spec, months = make_series(store, "spot/klines/QUSDT/15m", "2025-12", "2025-12")
    times = month_times(spec, "2025-12")
    step, shift = spec.step_ms, 496_090
    shifted = [t + shift for t in times[100:104]]
    put(store, spec, "2025-12", [*times[:100], *shifted, *times[105:]])
    path = tmp_path / "dev.json"
    write(build(store, DEVELOPMENT, [(spec, months)]), path)
    assert len(json.loads(path.read_text())["datasets"][spec.name]["off_grid"]) == 4
    loader = DatasetLoader(Manifest.load(path), store)
    with pytest.raises(GapError):
        loader.load(spec.name, utc(2025, 12, 1), utc(2025, 12, 3))
    result = loader.load(spec.name, utc(2025, 12, 1), utc(2025, 12, 3), accept_recorded_gaps=True)
    assert result.off_grid == shifted
    assert all(r.open_time % step == 0 for r in result.rows)
    assert len(result.rows) == 2 * 96 - 5 and result.gaps[0].missing == 5
    doc = json.loads(path.read_text())
    doc["datasets"][spec.name]["off_grid"] = []
    path.write_bytes(dumps(doc))
    with pytest.raises(ManifestError, match="off the grid"):
        DatasetLoader(Manifest.load(path), store).load(
            spec.name, utc(2025, 12, 1), utc(2025, 12, 3), accept_recorded_gaps=True
        )


def test_funding_loads_and_bad_arguments_are_refused(world):
    _, _, loader = world
    rows = loader.load("um/fundingRate/XUSDT", utc(2025, 11, 1), utc(2025, 12, 1)).rows
    assert len(rows) == 90
    with pytest.raises(ValueError, match="UTC"):
        loader.load("um/fundingRate/XUSDT", datetime(2025, 11, 1), utc(2025, 12, 1))
    with pytest.raises(ValueError, match="before"):
        loader.load("um/fundingRate/XUSDT", utc(2025, 12, 1), utc(2025, 11, 1))
