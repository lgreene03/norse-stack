"""The dataset steps with the holdout's files off disk until the unseal (G1.6).

Each test runs a step on a synthetic mirror whose holdout files have been deleted, as
the real mirror's are, with the committed manifests swapped for synthetic ones.
"""

import json

import pytest
from norse_research import datasets, manifest
from norse_research.manifest import MANIFEST_FILES, Manifest, ManifestError, build, dumps, write
from norse_research.periods import DEVELOPMENT, HOLDOUT, LAST_DEVELOPMENT_MONTH
from norse_research.store import ArchiveStore
from synthetic_store import make_series, month_times, put


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    store = ArchiveStore(tmp_path / "mirror")
    series = [
        make_series(store, "spot/klines/XUSDT/1d", "2025-11", "2026-02"),
        make_series(store, "um/fundingRate/XUSDT", "2025-12", "2026-01"),
    ]
    paths = {period: tmp_path / f"{period}.json" for period in (DEVELOPMENT, HOLDOUT)}
    write(build(store, DEVELOPMENT, series), paths[DEVELOPMENT])
    holdout_sha256 = write(build(store, HOLDOUT, series), paths[HOLDOUT])
    for spec, months in series:
        for month in months:
            if month > LAST_DEVELOPMENT_MONTH:
                path = store.path(spec.key(month))
                path.unlink()
                path.with_name(path.name + ".CHECKSUM").unlink()
    for period, path in paths.items():
        monkeypatch.setitem(MANIFEST_FILES, period, path)
    # raising=False only so that this fixture also runs on code without the constant.
    monkeypatch.setattr(manifest, "HOLDOUT_MANIFEST_SHA256", holdout_sha256, raising=False)
    monkeypatch.setattr(datasets, "series_specs", lambda store: [spec for spec, _ in series])
    monkeypatch.setenv("NORSE_DATASETS_DIR", str(store.root))
    return store, series, paths


def test_manifest_rebuilds_development_only_and_never_the_holdout_manifest(mirror, capsys):
    _, _, paths = mirror
    frozen = paths[HOLDOUT].read_bytes()
    paths[DEVELOPMENT].unlink()

    assert datasets.main(["manifest"]) == 0

    rebuilt = Manifest.load(paths[DEVELOPMENT])
    assert [f[0] for f in rebuilt.datasets["spot/klines/XUSDT/1d"]["files"]] == [
        "2025-11",
        "2025-12",
    ]
    assert paths[HOLDOUT].read_bytes() == frozen
    assert "frozen until the unseal" in capsys.readouterr().out


def test_verify_checks_development_and_the_frozen_holdout_manifest_without_its_files(
    mirror, capsys
):
    store, series, paths = mirror

    assert datasets.main(["verify"]) == 0
    out = capsys.readouterr().out
    assert "development: 3 files match" in out
    assert "holdout: committed manifest unchanged" in out
    assert "its 3 files are off disk until the unseal (G1.6)" in out

    # A holdout file back on disk fails the step; it is counted, never read.
    spec = series[0][0]
    put(store, spec, "2026-01", month_times(spec, "2026-01"))
    assert datasets.main(["verify"]) == 1
    assert "1 of its 3 files are on disk" in capsys.readouterr().out

    # A holdout manifest that is not the frozen one fails the step.
    doc = json.loads(paths[HOLDOUT].read_text())
    doc["datasets"]["spot/klines/XUSDT/1d"]["rows"] += 1
    paths[HOLDOUT].write_bytes(dumps(doc))
    with pytest.raises(ManifestError, match="not the frozen holdout manifest"):
        datasets.main(["verify"])
