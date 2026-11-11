"""Committed dataset manifests: every archive file pinned by SHA-256 (goal G1.1).

A manifest covers one period, development or holdout, never both. For every series
it records the monthly files (month, SHA-256, bytes, rows), the series' row count,
first and last row, every gap found, every row the archive published twice
identically (collapsed to one), and every bar it published off the interval grid (set
aside, so its slots count in a gap), under the archive's licence. The file is
written deterministically (sorted, one series per line), so rebuilding it from the
same data gives the same bytes, and its SHA-256 is the dataset hash a ledger entry
cites (G1.5).

The archive key of a file is ``DatasetSpec.from_name(series).key(month)``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .binance_archive import DOWNLOAD_URL, sha256_hex
from .formats import (
    LICENCE,
    DataIntegrityError,
    DatasetSpec,
    Gap,
    bar_gaps,
    collapse_repeats,
    funding_gaps,
    parse_funding,
    parse_klines,
    read_csv_member,
    split_off_grid,
)
from .periods import (
    DEVELOPMENT,
    HOLDOUT,
    HOLDOUT_END,
    HOLDOUT_START,
    LAST_DEVELOPMENT_MONTH,
    LAST_HOLDOUT_MONTH,
    iso_time,
    period_of_month,
)
from .store import ArchiveStore

SCHEMA = "norse-dataset-manifest/1"
MANIFEST_DIR = Path(__file__).resolve().parent.parent / "manifests"
MANIFEST_FILES = {
    DEVELOPMENT: MANIFEST_DIR / "binance-vision-development.json",
    HOLDOUT: MANIFEST_DIR / "binance-vision-holdout.json",
}


class ManifestError(DataIntegrityError):
    """A manifest is malformed, or data disagrees with the manifest that pins it."""


def gap_record(gap: Gap) -> list:
    return [iso_time(gap.after), iso_time(gap.before), gap.missing]


def read_series_file(
    store: ArchiveStore, spec: DatasetSpec, month: str
) -> tuple[list, str, int, list[int]]:
    """Rows, SHA-256, size and collapsed exact repeats of one mirrored monthly file,
    verified against its CHECKSUM and size-capped."""
    data, digest = store.read(spec.key(month))
    if len(data) > spec.max_bytes():
        raise ManifestError(f"{spec.key(month)}: {len(data)} bytes exceeds cap {spec.max_bytes()}")
    csv_name = spec.file_name(month)[: -len(".zip")] + ".csv"
    text = read_csv_member(data, csv_name, spec.max_bytes())
    source = spec.key(month)
    rows = parse_funding(text, source) if spec.kind == "fundingRate" else parse_klines(text, source)
    rows, repeats = collapse_repeats(rows, source)
    return rows, digest, len(data), repeats


def on_grid_and_gaps(spec: DatasetSpec, rows: list) -> tuple[list, list[int], list[Gap]]:
    """Rows on the interval grid, the times of bars set aside off it, and the gaps.

    Funding has no fixed grid, so nothing is set aside for it.
    """
    if not spec.is_bars:
        return rows, [], funding_gaps(rows, spec.name)
    on_grid, off_grid = split_off_grid(rows, spec.step_ms)
    return on_grid, off_grid, bar_gaps([r[0] for r in on_grid], spec.step_ms, spec.name)


def series_entry(store: ArchiveStore, spec: DatasetSpec, months: list[str]) -> dict:
    files, rows, repeated = [], [], []
    for month in months:
        file_rows, digest, size, repeats = read_series_file(store, spec, month)
        if not file_rows:
            raise ManifestError(f"{spec.key(month)} holds no rows")
        files.append([month, digest, size, len(file_rows)])
        rows.extend(file_rows)
        repeated.extend(iso_time(t) for t in repeats)
    _, off_grid, gaps = on_grid_and_gaps(spec, rows)
    return {
        "files": files,
        "rows": len(rows),
        "first": iso_time(rows[0][0]),
        "last": iso_time(rows[-1][0]),
        "gaps": [gap_record(g) for g in gaps],
        "repeated": repeated,
        "off_grid": [iso_time(t) for t in off_grid],
    }


def build(store: ArchiveStore, period: str, series: Iterable[tuple[DatasetSpec, list[str]]]) -> dict:
    """A manifest document for one period from mirrored, verified files."""
    datasets = {}
    for spec, months in series:
        in_period = [m for m in months if period_of_month(m) == period]
        if in_period:
            datasets[spec.name] = series_entry(store, spec, in_period)
    window = (
        {"from": None, "until_exclusive": HOLDOUT_START.isoformat()}
        if period == DEVELOPMENT
        else {"from": HOLDOUT_START.isoformat(), "until_exclusive": HOLDOUT_END.isoformat()}
    )
    return {
        "schema": SCHEMA,
        "period": period,
        "window": window,
        "source": DOWNLOAD_URL,
        "licence": LICENCE,
        "verification": "each file's SHA-256 equals its published .CHECKSUM",
        "key": "DatasetSpec.from_name(series).key(month)",
        "file_columns": ["month", "sha256", "bytes", "rows"],
        "gap_columns": ["after", "before", "missing"],
        "datasets": datasets,
    }


def dumps(doc: dict) -> bytes:
    """Deterministic JSON: sorted keys, one series per line, trailing newline."""
    lines = ["{"]
    top = sorted(k for k in doc if k != "datasets")
    lines.extend(f"{json.dumps(key)}: {json.dumps(doc[key], sort_keys=True)}," for key in top)
    lines.append('"datasets": {')
    names = sorted(doc["datasets"])
    for i, name in enumerate(names):
        body = json.dumps(doc["datasets"][name], sort_keys=True, separators=(",", ":"))
        comma = "," if i < len(names) - 1 else ""
        lines.append(f"{json.dumps(name, ensure_ascii=False)}: {body}{comma}")
    lines.append("}")
    lines.append("}")
    return ("\n".join(lines) + "\n").encode()


def write(doc: dict, path: Path) -> str:
    data = dumps(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return sha256_hex(data)


@dataclass(frozen=True)
class Manifest:
    """A committed manifest, read and checked; ``sha256`` is the hash results cite."""

    path: Path
    sha256: str
    period: str
    datasets: dict

    @classmethod
    def load(cls, path: Path) -> Manifest:
        raw = Path(path).read_bytes()
        doc = json.loads(raw)
        if doc.get("schema") != SCHEMA:
            raise ManifestError(f"{path}: schema {doc.get('schema')!r}, expected {SCHEMA}")
        period = doc.get("period")
        if period not in (DEVELOPMENT, HOLDOUT):
            raise ManifestError(f"{path}: unknown period {period!r}")
        last = LAST_DEVELOPMENT_MONTH if period == DEVELOPMENT else LAST_HOLDOUT_MONTH
        for name, entry in doc["datasets"].items():
            DatasetSpec.from_name(name)
            for month, digest, size, rows in entry["files"]:
                if period_of_month(month) != period or month > last:
                    raise ManifestError(f"{path}: {name} {month} is outside the {period} period")
                if len(digest) != 64 or size <= 0 or rows <= 0:
                    raise ManifestError(f"{path}: {name} {month} has a malformed file entry")
        return cls(Path(path), sha256_hex(raw), period, doc["datasets"])

    def entry(self, name: str) -> dict:
        try:
            return self.datasets[name]
        except KeyError:
            raise ManifestError(f"{name} is not in manifest {self.path.name}") from None


def verify(manifest: Manifest, store: ArchiveStore) -> int:
    """Re-verify every file a manifest pins: hash, size and row count. Returns files."""
    checked = 0
    for name, entry in manifest.datasets.items():
        spec = DatasetSpec.from_name(name)
        for month, digest, size, rows in entry["files"]:
            file_rows, actual, actual_size, _ = read_series_file(store, spec, month)
            if (actual, actual_size, len(file_rows)) != (digest, size, rows):
                raise ManifestError(f"{spec.key(month)} does not match {manifest.path.name}")
            checked += 1
    return checked
