"""Dataset naming, file formats and gap detection for data.binance.vision archives.

Format traps handled here, each with a test:

* Spot kline files dated 2025-01 onwards carry timestamps in microseconds; earlier
  files, and every futures file, use milliseconds. Everything is normalised to ms.
* Futures files gained a header row part-way through their history; spot files have
  none. A header is accepted only on the first line and only if it is the expected one.
* Funding settlement times carry a few milliseconds of jitter (``...800002``), and a
  symbol's funding interval can change (8 hours to 4 or 1), so funding gaps are judged
  against the interval each row declares, within a tolerance.
* A few files repeat a row exactly; ``collapse_repeats`` keeps one and reports it.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import NamedTuple

LICENCE = "CC BY-NC-SA 4.0 (Binance public data, data.binance.vision)"

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "1d": 86_400_000,
}

KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
FUNDING_COLUMNS = ("calc_time", "funding_interval_hours", "last_funding_rate")

# Timestamps at or above this are microseconds: 10**15 ms would be the year 33658.
_MICROSECONDS_FROM = 10**15
FUNDING_TOLERANCE_MS = 60_000
ROW_BYTES_CAP = 512


class DataIntegrityError(ValueError):
    """A file's contents break the format or the time grid it must follow."""


@dataclass(frozen=True)
class DatasetSpec:
    """One archive series: a market, a kind, a symbol and (for bars) an interval."""

    market: str  # "spot" or "um" (USDT-margined futures)
    kind: str  # "klines", "premiumIndexKlines" or "fundingRate"
    symbol: str
    interval: str | None = None

    def __post_init__(self) -> None:
        if self.market not in ("spot", "um"):
            raise ValueError(f"unknown market {self.market}")
        if self.kind not in ("klines", "premiumIndexKlines", "fundingRate"):
            raise ValueError(f"unknown kind {self.kind}")
        if self.market == "spot" and self.kind != "klines":
            raise ValueError("spot has klines only")
        if (self.kind == "fundingRate") != (self.interval is None):
            raise ValueError("funding has no interval; bar series need one")
        if self.interval is not None and self.interval not in INTERVAL_MS:
            raise ValueError(f"unknown interval {self.interval}")
        # Letters and digits only, so a symbol can never carry a path separator. Not
        # ASCII only: the archive lists real pairs named in Chinese (2025 meme coins).
        if not self.symbol.isalnum():
            raise ValueError(f"unsafe symbol {self.symbol!r}")

    @property
    def name(self) -> str:
        parts = [self.market, self.kind, self.symbol]
        if self.interval:
            parts.append(self.interval)
        return "/".join(parts)

    @classmethod
    def from_name(cls, name: str) -> DatasetSpec:
        parts = name.split("/")
        if len(parts) not in (3, 4):
            raise ValueError(f"bad dataset name {name!r}")
        return cls(parts[0], parts[1], parts[2], parts[3] if len(parts) == 4 else None)

    @property
    def prefix(self) -> str:
        root = "data/spot/monthly" if self.market == "spot" else "data/futures/um/monthly"
        if self.kind == "fundingRate":
            return f"{root}/fundingRate/{self.symbol}/"
        return f"{root}/{self.kind}/{self.symbol}/{self.interval}/"

    def file_name(self, month: str) -> str:
        tag = "fundingRate" if self.kind == "fundingRate" else self.interval
        return f"{self.symbol}-{tag}-{month}.zip"

    def key(self, month: str) -> str:
        return self.prefix + self.file_name(month)

    def month_of(self, key: str) -> str:
        name = key.rsplit("/", 1)[-1]
        stem = f"{self.symbol}-{'fundingRate' if self.kind == 'fundingRate' else self.interval}-"
        if not (name.startswith(stem) and name.endswith(".zip")):
            raise ValueError(f"{key} is not a file of {self.name}")
        month = name[len(stem) : -len(".zip")]
        if len(month) != 7 or month[4] != "-" or not (month[:4] + month[5:]).isdigit():
            raise ValueError(f"{key} is not a monthly file")
        return month

    @property
    def is_bars(self) -> bool:
        return self.kind != "fundingRate"

    @property
    def step_ms(self) -> int:
        if self.interval is None:
            raise ValueError("funding has no fixed step")
        return INTERVAL_MS[self.interval]

    def max_rows(self) -> int:
        """Most rows a monthly file can hold: 31 days of bars, or of hourly funding."""
        per_day = 24 if self.interval is None else 86_400_000 // INTERVAL_MS[self.interval]
        return 31 * per_day

    def max_bytes(self) -> int:
        """Size cap for a monthly file, compressed or not."""
        return self.max_rows() * ROW_BYTES_CAP + 4_096


class Kline(NamedTuple):
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    quote_volume: float
    trades: int
    taker_buy_volume: float
    taker_buy_quote_volume: float


class Funding(NamedTuple):
    calc_time: int
    interval_hours: int
    rate: float


@dataclass(frozen=True)
class Gap:
    """Missing rows between two present ones (times in ms since the epoch, UTC)."""

    after: int
    before: int
    missing: int


def to_ms(value: int) -> int:
    return value // 1000 if value >= _MICROSECONDS_FROM else value


def read_csv_member(zip_bytes: bytes, csv_name: str, max_uncompressed: int) -> str:
    """The single CSV inside an archive file, refusing anything else or anything large."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise DataIntegrityError(f"{csv_name}: not a zip archive") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) != 1 or infos[0].filename != csv_name:
            names = [i.filename for i in infos]
            raise DataIntegrityError(f"expected only {csv_name}, archive holds {names}")
        if infos[0].file_size > max_uncompressed:
            raise DataIntegrityError(f"{csv_name}: {infos[0].file_size} bytes exceeds cap")
        with archive.open(infos[0]) as handle:
            data = handle.read(max_uncompressed + 1)
    if len(data) > max_uncompressed:
        raise DataIntegrityError(f"{csv_name}: uncompressed size exceeds cap")
    try:
        return data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise DataIntegrityError(f"{csv_name}: not ASCII") from exc


def _data_lines(text: str, header: tuple[str, ...], source: str) -> list[tuple[int, list[str]]]:
    lines = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = [f.strip() for f in line.split(",")]
        if lineno == 1 and not fields[0].lstrip("-").isdigit():
            if tuple(fields) != header:
                raise DataIntegrityError(f"{source}: unexpected header {fields}")
            continue
        if len(fields) != len(header):
            raise DataIntegrityError(f"{source}:{lineno}: {len(fields)} columns")
        lines.append((lineno, fields))
    return lines


def parse_klines(text: str, source: str) -> list[Kline]:
    rows = []
    for lineno, f in _data_lines(text, KLINE_COLUMNS, source):
        try:
            rows.append(
                Kline(
                    to_ms(int(f[0])),
                    float(f[1]),
                    float(f[2]),
                    float(f[3]),
                    float(f[4]),
                    float(f[5]),
                    to_ms(int(f[6])),
                    float(f[7]),
                    int(f[8]),
                    float(f[9]),
                    float(f[10]),
                )
            )
        except ValueError as exc:
            raise DataIntegrityError(f"{source}:{lineno}: {exc}") from exc
    return rows


def parse_funding(text: str, source: str) -> list[Funding]:
    rows = []
    for lineno, f in _data_lines(text, FUNDING_COLUMNS, source):
        try:
            rows.append(Funding(to_ms(int(f[0])), int(f[1]), float(f[2])))
        except ValueError as exc:
            raise DataIntegrityError(f"{source}:{lineno}: {exc}") from exc
        if rows[-1].interval_hours not in (1, 2, 4, 8):
            raise DataIntegrityError(f"{source}:{lineno}: funding interval {f[1]} hours")
    return rows


def collapse_repeats(rows: list, source: str = "") -> tuple[list, list[int]]:
    """Drop rows that exactly repeat the row before them; return the rest and the times
    repeated.

    The archive holds a few rows published twice, identically, inside one file (for
    example AXSUSDT daily on 2026-02-10). Keeping one loses nothing and invents
    nothing, so the repeat is collapsed and reported; a repeated time whose rows
    differ cannot be resolved without choosing one, so it raises.
    """
    kept: list = []
    repeated: list[int] = []
    for row in rows:
        if kept and row[0] == kept[-1][0]:
            if row != kept[-1]:
                raise DataIntegrityError(f"{source}: two different rows at time {row[0]}")
            repeated.append(row[0])
            continue
        kept.append(row)
    return kept, repeated


def bar_gaps(open_times: Sequence[int], step_ms: int, source: str = "") -> list[Gap]:
    """Missing bars in an ordered series of open times on a fixed UTC grid.

    A duplicate, a time out of order or a time off the grid is not a gap but a broken
    file, and raises.
    """
    for t in open_times:
        if t % step_ms:
            raise DataIntegrityError(f"{source}: open time {t} is off the {step_ms} ms grid")
    gaps = []
    for prev, cur in pairwise(open_times):
        diff = cur - prev
        if diff <= 0:
            raise DataIntegrityError(f"{source}: open time {cur} after {prev} is not increasing")
        if diff != step_ms:
            gaps.append(Gap(prev, cur, diff // step_ms - 1))
    return gaps


def funding_gaps(events: Sequence[Funding], source: str = "") -> list[Gap]:
    """Missing funding settlements, judged against the interval each row declares."""
    gaps = []
    for prev, cur in pairwise(events):
        diff = cur.calc_time - prev.calc_time
        if diff <= 0:
            raise DataIntegrityError(f"{source}: funding time {cur.calc_time} not increasing")
        allowed = {prev.interval_hours * 3_600_000, cur.interval_hours * 3_600_000}
        if any(abs(diff - step) <= FUNDING_TOLERANCE_MS for step in allowed):
            continue
        step = max(allowed)
        gaps.append(Gap(prev.calc_time, cur.calc_time, max(round(diff / step) - 1, 0)))
    return gaps
