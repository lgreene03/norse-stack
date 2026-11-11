"""Load verified, manifested data for research, with the holdout sealed (goal G1.1).

The only way research code receives rows is ``DatasetLoader.load``. It:

* accepts only a development manifest, and refuses any request reaching on or past
  2026-01-01, the holdout's start, with ``HoldoutSealed``. There is no unseal here
  at all: G1.6 adds the one permitted unseal, which needs the pre-registration hash,
  the owner's key and a ledger entry. Until then holdout rows cannot leave this
  package through the loader;
* refuses a series the manifest does not pin, a month the manifest does not list,
  and a file whose SHA-256, size or row count differs from the manifest;
* collapses a row the archive published twice identically only where the manifest
  records that repeat; any other repeat, or two different rows at one time, fails;
* never returns a bar the archive published off the interval grid (after the
  2018-02-08 outage, for example). Such bars must be recorded in the manifest, their
  slots count as a gap, and the result lists them;
* fails on any gap in the requested range instead of skipping it. A caller that must
  cross a known exchange outage passes ``accept_recorded_gaps=True``: gaps recorded
  in the committed manifest are then allowed and returned with the rows, so the
  run can record them, and any gap the manifest does not record still fails.

Every result carries the manifest's SHA-256 and the files read, which is what a
ledger entry cites (G1.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .formats import DataIntegrityError, DatasetSpec, Gap
from .manifest import Manifest, ManifestError, gap_record, on_grid_and_gaps, read_series_file
from .periods import DEVELOPMENT, HOLDOUT_START, HOLDOUT_START_MS, iso_time
from .store import ArchiveStore
from .universe import months_between

FUNDING_SLACK_MS = 8 * 3_600_000 + 60_000


class HoldoutSealed(PermissionError):
    """Holdout data was requested before the unseal exists (G1.6)."""


class MissingData(LookupError):
    """The requested range runs past the data the manifest pins."""


class GapError(DataIntegrityError):
    """The requested range contains a gap that the caller has not accepted."""


@dataclass(frozen=True)
class Loaded:
    series: str
    rows: list
    gaps: list[Gap]
    manifest_sha256: str
    files: list[str]
    # Bars the archive published off the interval grid in this range: never returned
    # as rows, always inside one of ``gaps``, listed so a run can record them.
    off_grid: list[int]


def _ms(moment: datetime) -> int:
    if moment.tzinfo is None or moment.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{moment!r} must be timezone-aware UTC")
    return int(moment.timestamp() * 1000)


def _month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m")


class DatasetLoader:
    def __init__(self, manifest: Manifest, store: ArchiveStore) -> None:
        if manifest.period != DEVELOPMENT:
            raise HoldoutSealed(
                f"{manifest.path.name} is the {manifest.period} manifest; holdout data "
                "cannot be loaded until the unseal is built (G1.6)"
            )
        self.manifest = manifest
        self.store = store

    def load(
        self,
        series: str,
        start: datetime,
        end: datetime,
        accept_recorded_gaps: bool = False,
    ) -> Loaded:
        """Rows of ``series`` with time in [start, end), verified against the manifest."""
        start_ms, end_ms = _ms(start), _ms(end)
        if end_ms > HOLDOUT_START_MS:
            raise HoldoutSealed(
                f"{series}: requested until {end.isoformat()}, past the holdout start "
                f"{HOLDOUT_START.isoformat()}; sealed until G1.6"
            )
        if start_ms >= end_ms:
            raise ValueError("start must be before end")
        spec = DatasetSpec.from_name(series)
        entry = self.manifest.entry(series)
        pinned = {f[0]: f for f in entry["files"]}

        months = months_between(_month(start_ms), _month(end_ms - 1))
        # Read the month before too when it is pinned, so a gap that began before the
        # range is reported as the gap it is rather than as missing data.
        before = months_between(_month(start_ms - 32 * 86_400_000), months[0])[-2]
        if before in pinned:
            months.insert(0, before)
        rows: list = []
        keys = []
        for month in months:
            if month not in pinned:
                raise MissingData(f"{series}: no file for {month} in {self.manifest.path.name}")
            file_rows, digest, size, repeats = read_series_file(self.store, spec, month)
            _, want_digest, want_size, want_rows = pinned[month]
            if (digest, size, len(file_rows)) != (want_digest, want_size, want_rows):
                raise ManifestError(f"{spec.key(month)} does not match the manifest")
            unrecorded = [t for t in repeats if iso_time(t) not in entry.get("repeated", [])]
            if unrecorded:
                raise ManifestError(f"{spec.key(month)}: repeated rows the manifest omits")
            rows.extend(file_rows)
            keys.append(spec.key(month))

        on_grid, off_grid, all_gaps = on_grid_and_gaps(spec, rows)
        stray = [t for t in off_grid if iso_time(t) not in entry.get("off_grid", [])]
        if stray:
            raise ManifestError(f"{series}: bars off the grid that the manifest omits")
        gaps = self._range_gaps(spec, on_grid, all_gaps, start_ms, end_ms)
        if gaps:
            recorded = {tuple(g) for g in entry["gaps"]}
            unrecorded = [g for g in gaps if tuple(gap_record(g)) not in recorded]
            if not accept_recorded_gaps or unrecorded:
                shown = [gap_record(g) for g in (unrecorded or gaps)[:5]]
                raise GapError(f"{series}: {len(gaps)} gap(s) in range, for example {shown}")
        selected = [r for r in on_grid if start_ms <= r[0] < end_ms]
        set_aside = [t for t in off_grid if start_ms <= t < end_ms]
        if any(r[0] >= HOLDOUT_START_MS for r in selected):  # pragma: no cover
            raise HoldoutSealed("a holdout row reached the loader's output")
        return Loaded(series, selected, gaps, self.manifest.sha256, keys, set_aside)

    @staticmethod
    def _range_gaps(
        spec: DatasetSpec, rows: list, gaps: list[Gap], start_ms: int, end_ms: int
    ) -> list[Gap]:
        """Gaps that touch [start, end), after checking the data spans the range."""
        if not rows:
            raise MissingData(f"{spec.name}: no rows")
        first, last = rows[0][0], rows[-1][0]
        if spec.is_bars:
            step = spec.step_ms
            want_first = -(-start_ms // step) * step
            want_last = (end_ms - 1) // step * step
            if first > want_first or last < want_last:
                raise MissingData(
                    f"{spec.name}: data spans {iso_time(first)} to {iso_time(last)}, "
                    f"requested {iso_time(want_first)} to {iso_time(want_last)}"
                )
            lo, hi = want_first, want_last
        else:
            if first > start_ms + FUNDING_SLACK_MS or last < end_ms - FUNDING_SLACK_MS:
                raise MissingData(
                    f"{spec.name}: funding spans {iso_time(first)} to {iso_time(last)}"
                )
            lo, hi = start_ms, end_ms
        return [g for g in gaps if g.before > lo and g.after < hi]
