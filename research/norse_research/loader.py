"""Load verified, manifested data for research, with the holdout sealed (goal G1.1).

The only way research code receives rows is ``DatasetLoader.load``. It:

* accepts a development manifest, and refuses any request reaching on or past
  2026-01-01, the holdout's start, with ``HoldoutSealed``, with one exception: a
  loader also given the holdout manifest (``holdout=``) returns holdout rows inside
  the unsealed holdout run, which only the runner enters, after the harness has
  pushed the run's open entry and found the unseal entry on the remote's ledger
  (G1.6, ``norse_research.seal``). The unseal needs the pre-registration hash, the
  owner's key and the frozen ledger's holdout candidate, so outside that one run
  holdout rows cannot leave this package through the loader;
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
* returns rows from a registered out-of-sample window (from 2018-05-01, the first
  return day of H1's window) only inside an open ledger entry, which the harness
  sets after the entry is pushed (G1.5). This is the threat model's speed bump for
  T2: a script calling the loader directly is stopped and told why, though nothing
  stops it reading files itself. The REST comparison of G1.1, which checks archive
  bars against the exchange and computes no return, passes ``integrity_audit=True``
  and is the only caller that may (a test checks this).

Every result carries the manifest's SHA-256 and the files read, which is what a
ledger entry cites (G1.5).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from .formats import DataIntegrityError, DatasetSpec, Gap
from .manifest import Manifest, ManifestError, gap_record, on_grid_and_gaps, read_series_file
from .periods import (
    DEVELOPMENT,
    HOLDOUT,
    HOLDOUT_END,
    HOLDOUT_END_MS,
    HOLDOUT_START,
    HOLDOUT_START_MS,
    LAST_DEVELOPMENT_MONTH,
    OUT_OF_SAMPLE_START,
    OUT_OF_SAMPLE_START_MS,
    HoldoutSealed,
    iso_time,
    unsealed_run,
)
from .store import ArchiveStore
from .universe import months_between

FUNDING_SLACK_MS = 8 * 3_600_000 + 60_000


class MissingData(LookupError):
    """The requested range runs past the data the manifest pins."""


class GapError(DataIntegrityError):
    """The requested range contains a gap that the caller has not accepted."""


class OutOfSampleUnledgered(PermissionError):
    """Rows of a registered out-of-sample window were requested outside an open
    ledger entry (G1.5, threat T2)."""


# The open ledger entry the current run belongs to; set only by the harness.
_OPEN_ENTRY: ContextVar[str | None] = ContextVar("norse_open_ledger_entry", default=None)


@contextmanager
def inside_ledger_entry(entry_hash: str) -> Iterator[None]:
    """Loads in this block belong to the open ledger entry ``entry_hash``. The
    harness enters it only after the entry is committed and pushed."""
    if not isinstance(entry_hash, str) or len(entry_hash) != 64:
        raise ValueError("an open entry is named by its full SHA-256")
    token = _OPEN_ENTRY.set(entry_hash)
    try:
        yield
    finally:
        _OPEN_ENTRY.reset(token)


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
    # The frozen holdout manifest's SHA-256, for a load inside the holdout run only.
    holdout_manifest_sha256: str | None = None


def _ms(moment: datetime) -> int:
    if moment.tzinfo is None or moment.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{moment!r} must be timezone-aware UTC")
    return int(moment.timestamp() * 1000)


def _month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m")


def _parse_time(text: str) -> int:
    """A manifest time ("YYYY-MM-DD HH:MM", UTC) in ms."""
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp() * 1000)


def rehearsal_split(manifest: Manifest, month: str) -> tuple[Manifest, Manifest, int]:
    """The development manifest split for the holdout rehearsal (``seal rehearse``):
    the months before ``month``, and the months from it to the end of development, which
    stand in for the holdout, with the boundary between them. The rehearsal runs the
    holdout run's loading path, across a boundary, on development data only."""
    if not (isinstance(month, str) and len(month) == 7 and month <= LAST_DEVELOPMENT_MONTH):
        raise ValueError(f"a rehearsal boundary is a development month, not {month!r}")
    before, after = {}, {}
    for name, entry in manifest.datasets.items():
        early = [f for f in entry["files"] if f[0] < month]
        late = [f for f in entry["files"] if f[0] >= month]
        if early:
            before[name] = {**entry, "files": early}
        if late:
            after[name] = {**entry, "files": late}
    boundary = int(datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC).timestamp() * 1000)
    return (
        Manifest(manifest.path, manifest.sha256, DEVELOPMENT, before),
        Manifest(manifest.path, manifest.sha256, HOLDOUT, after),
        boundary,
    )


class DatasetLoader:
    def __init__(
        self,
        manifest: Manifest,
        store: ArchiveStore,
        integrity_audit: bool = False,
        holdout: Manifest | None = None,
        boundary_ms: int = HOLDOUT_START_MS,
    ) -> None:
        if manifest.period != DEVELOPMENT:
            raise HoldoutSealed(
                f"{manifest.path.name} is the {manifest.period} manifest; holdout data "
                "loads only in the holdout run after the unseal (G1.6), which passes it as "
                "the holdout manifest"
            )
        if holdout is not None and (holdout.period != HOLDOUT or integrity_audit):
            raise HoldoutSealed("the holdout manifest is for the holdout run alone (G1.6)")
        if boundary_ms > HOLDOUT_START_MS or (boundary_ms != HOLDOUT_START_MS and holdout is None):
            raise ValueError("a rehearsal boundary lies in development, with its stand-in part")
        self.manifest = manifest
        self.store = store
        self.integrity_audit = integrity_audit
        self.holdout = holdout
        # The holdout's start, or, in the rehearsal alone, an earlier month whose
        # development data stands in for the holdout (``rehearsal_split``).
        self.boundary_ms = boundary_ms
        # Every recorded gap a load returned, as [series, after, before, missing]: the
        # runner hands them to the harness for the run's ledger entry (section 5, Gaps).
        self._crossed: set[tuple] = set()

    def crossed_gaps(self) -> list[list]:
        """The recorded gaps this loader's loads crossed, sorted, without repeats."""
        return [list(g) for g in sorted(self._crossed)]

    def coverage(self, series: str) -> list[tuple[int, int]]:
        """Each archived run of ``series`` this loader can read, as (first, last) times in
        ms, from the committed manifests' metadata alone: no data file is opened, so the
        holdout run learns the holdout's spans without reading a holdout file first.

        A run breaks where a recorded gap skips a month the manifests pin no file for
        (FTTUSDT's suspension from 2022-12 to 2023-08, for example), since the loader
        refuses an unpinned month; a gap inside pinned months stays inside one run and is
        crossed as recorded (section 5, Gaps). Times are the manifests' minute precision,
        exact for bars; a funding settlement's ``calc_time`` can be up to a minute later.
        A development run reads the development manifest only; the holdout run (and the
        rehearsal) reads the holdout part too, joined to it at the boundary. Strategies
        take spans from here rather than committing them, so a span error is a
        data-loading fix (section 11, item 6)."""
        parts = [m.datasets[series] for m in (self.manifest, self.holdout)
                 if m is not None and series in m.datasets]
        if not parts:
            raise ManifestError(f"{series} is in no manifest this run reads")
        pinned = {f[0] for part in parts for f in part["files"]}
        breaks = {(_parse_time(a), _parse_time(b)) for part in parts for a, b, _ in part["gaps"]}
        if len(parts) == 2:  # the boundary between the development and holdout parts
            breaks.add((_parse_time(parts[0]["last"]), _parse_time(parts[1]["first"])))
        start = min(_parse_time(part["first"]) for part in parts)
        last = max(_parse_time(part["last"]) for part in parts)
        runs = []
        for after, before in sorted(breaks):
            if after >= before or after < start:
                continue
            if any(m not in pinned for m in months_between(_month(after), _month(before))):
                runs.append((start, after))
                start = before
        runs.append((start, last))
        return runs

    def _entry(self, series: str, reaches_holdout: bool) -> dict:
        """The series' pins: the development manifest's, and inside the holdout run
        the holdout manifest's beside them (a pair can be in either or both)."""
        if not reaches_holdout:
            return self.manifest.entry(series)
        parts = [m.datasets[series] for m in (self.manifest, self.holdout) if series in m.datasets]
        if not parts:
            raise ManifestError(f"{series} is in neither the development nor the holdout manifest")
        return {
            "files": [f for part in parts for f in part["files"]],
            "gaps": [g for part in parts for g in part["gaps"]],
            "repeated": [t for part in parts for t in part.get("repeated", [])],
            "off_grid": [t for part in parts for t in part.get("off_grid", [])],
        }

    def load(
        self,
        series: str,
        start: datetime,
        end: datetime,
        accept_recorded_gaps: bool = False,
    ) -> Loaded:
        """Rows of ``series`` with time in [start, end), verified against the manifest."""
        start_ms, end_ms = _ms(start), _ms(end)
        rehearsal = self.boundary_ms != HOLDOUT_START_MS
        reaches_holdout = end_ms > self.boundary_ms
        if (end_ms > HOLDOUT_START_MS and rehearsal) or (
            reaches_holdout and (self.holdout is None or unsealed_run() is None)
        ):
            raise HoldoutSealed(
                f"{series}: requested until {end.isoformat()}, past the holdout start "
                f"{HOLDOUT_START.isoformat()}; holdout rows load only inside the holdout "
                "run the ledger ties to the unseal entry (G1.6)"
            )
        if end_ms > HOLDOUT_END_MS:
            raise MissingData(f"{series}: no registered data after {HOLDOUT_END.date()}")
        if start_ms >= end_ms:
            raise ValueError("start must be before end")
        if end_ms > OUT_OF_SAMPLE_START_MS and not self.integrity_audit:
            if _OPEN_ENTRY.get() is None:
                raise OutOfSampleUnledgered(
                    f"{series}: rows from {OUT_OF_SAMPLE_START.date()} on overlap a "
                    "registered out-of-sample window, and load only inside an open trial "
                    "ledger entry: run through the harness (G1.5)"
                )
        spec = DatasetSpec.from_name(series)
        entry = self._entry(series, reaches_holdout)
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
            self._crossed.update((series, *gap_record(g)) for g in gaps)
        selected = [r for r in on_grid if start_ms <= r[0] < end_ms]
        set_aside = [t for t in off_grid if start_ms <= t < end_ms]
        if not reaches_holdout and any(r[0] >= HOLDOUT_START_MS for r in selected):
            raise HoldoutSealed("a holdout row reached the loader's output")  # pragma: no cover
        holdout_sha = self.holdout.sha256 if reaches_holdout else None
        return Loaded(series, selected, gaps, self.manifest.sha256, keys, set_aside, holdout_sha)

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
