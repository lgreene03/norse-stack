"""Build a small mirror of synthetic archive files for manifest and loader tests."""

import hashlib
import json

from conftest import make_zip
from norse_research.formats import INTERVAL_MS, DatasetSpec
from norse_research.store import LISTINGS_DIR, ArchiveStore
from norse_research.universe import month_close_ms, months_between

HOUR = 3_600_000


def previous_month(month: str) -> str:
    year, mon = int(month[:4]), int(month[5:])
    return f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"


def month_times(spec: DatasetSpec, month: str) -> list[int]:
    start, end = month_close_ms(previous_month(month)), month_close_ms(month)
    step = spec.step_ms if spec.is_bars else 8 * HOUR
    return list(range(start, end, step))


def put(store: ArchiveStore, spec: DatasetSpec, month: str, times: list[int]) -> None:
    if spec.is_bars:
        step = spec.step_ms
        lines = [f"{t},1,1,1,1,1,{t + step - 1},1,1,1,1,0" for t in times]
    else:
        lines = ["calc_time,funding_interval_hours,last_funding_rate"]
        lines += [f"{t + 3},8,0.0001" for t in times]
    data = make_zip(spec.file_name(month)[:-4] + ".csv", "\n".join(lines) + "\n")
    path = store.path(spec.key(month))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_name(path.name + ".CHECKSUM").write_text(f"{digest}  {path.name}\n")


def write_listing(store: ArchiveStore, spec: DatasetSpec, months: list[str]) -> None:
    name = spec.prefix.strip("/").replace("/", "__") + ".json"
    target = store.root / LISTINGS_DIR / name
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"prefix": spec.prefix, "listed_at": "2026-11-11T00:00:00Z", "prefixes": []}
    record["objects"] = [{"key": spec.key(m), "size": 1} for m in months]
    target.write_text(json.dumps(record))


def make_series(store, name, first, last, drop=()):
    """A series with every row from ``first`` to ``last`` except those in ``drop``."""
    spec = DatasetSpec.from_name(name)
    months = months_between(first, last)
    for month in months:
        put(store, spec, month, [t for t in month_times(spec, month) if t not in drop])
    write_listing(store, spec, months)
    return spec, months


DAY = INTERVAL_MS["1d"]
