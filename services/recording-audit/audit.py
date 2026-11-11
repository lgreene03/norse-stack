"""Daily recording gap audit (G0.15): what muninn's recorder stored, minute by minute.

Coverage is measured from the event timestamps inside the stored objects
(``value.eventTime`` on every line), never from object or file times, because a
recorder that holds and later catches up writes old events with new file times.

Layout read (muninn ``RawObjectKeys``)::

    <event type>/source=<source>/instrument=<symbol>/year=YYYY/month=MM/day=DD/hour=HH/
        part-p<partition>-<first offset>-<last offset>.jsonl.gz

The hour in the key is the Kafka arrival hour, not the event hour. Auditing day D
therefore reads the whole of D plus the last hour of D-1 and the first hour of D+1,
and files each event by its own timestamp. The extra hours also show whether a
silence at either edge of the day continued across midnight.

Definitions, per stream (one event type, one venue, one instrument) and UTC day:

* minutes covered: UTC minutes in the window holding at least one event.
* gap: a silence longer than 60 seconds between consecutive events, clipped to the
  window. A silence that starts before the earliest hour read counts from there.
* bytes recorded: stored (compressed) size of the objects whose key is filed under
  the day, which is what the day cost on disk.

Per venue, the minutes covered follow guardrail P3: minutes with at least one trade
and at least one book event from that venue, on any instrument. A venue gap is a
silence longer than 60 seconds in which the venue recorded nothing at all.
"""

from __future__ import annotations

import datetime as dt
import gzip
import re
import zlib
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from s3client import StoredObject

SCHEMA = "norse.recording-audit/v1"
GAP_MS = 60_000
MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

# The two recorded event types and the role each plays in guardrail P3.
EVENT_TYPES = {"events.trade": "trade", "events.book.snapshot": "book"}

_EVENT_TIME = re.compile(rb'"eventTime"\s*:\s*("(?:[^"\\]|\\.)*"|-?[0-9][0-9.eE+-]*)')

DEFINITIONS = {
    "minutes_covered": "stream: UTC minutes in the window with at least one event. venue: "
    "minutes with at least one trade and at least one book event from the venue (P3).",
    "minutes_covered_all_streams": "venue: minutes in which every configured stream of the "
    "venue recorded at least one event.",
    "gaps": "silences longer than 60 seconds between consecutive event timestamps, clipped "
    "to the window; silence_seconds is the whole silence as far as the audit read.",
    "bytes_recorded": "stored (gzip) size of the objects whose key is filed under this day.",
    "records": "events whose own timestamp falls inside the window.",
    "time_basis": "value.eventTime inside each stored line, never file or object times.",
}


class ObjectStore(Protocol):
    def list_objects(self, prefix: str) -> Iterable[StoredObject]: ...

    def get_object(self, key: str) -> bytes: ...


@dataclass(frozen=True)
class Stream:
    event_type: str
    source: str
    instrument: str

    @property
    def venue(self) -> str:
        return self.source.split(".", 1)[0]

    @property
    def kind(self) -> str:
        return EVENT_TYPES[self.event_type]

    def prefix(self, day: dt.date, hour: int | None = None) -> str:
        base = (
            f"{self.event_type}/source={self.source}/instrument={self.instrument}/"
            f"year={day.year:04d}/month={day.month:02d}/day={day.day:02d}/"
        )
        return base if hour is None else f"{base}hour={hour:02d}/"


def streams_for(sources: Sequence[str], instruments: Sequence[str]) -> list[Stream]:
    return [
        Stream(event_type, source, instrument)
        for source in sources
        for event_type in EVENT_TYPES
        for instrument in instruments
    ]


# ── Event time parsing ─────────────────────────────────────────────────────


def _number_ms(text: str) -> int | None:
    if text.startswith("-"):
        return None
    if "e" in text or "E" in text:
        value = float(text)
        if value >= 1e17:
            return int(value // 1e6)
        if value >= 1e14:
            return int(value // 1e3)
        if value >= 1e11:
            return int(value)
        return round(value * 1000)
    if "." in text:
        # Decimal seconds, as Jackson writes an Instant: 1794355200.549000000.
        whole, fraction = text.split(".", 1)
        return int(whole or "0") * 1000 + int((fraction + "000")[:3])
    value = int(text)
    if value >= 10**17:
        return value // 1_000_000  # nanoseconds
    if value >= 10**14:
        return value // 1_000  # microseconds
    if value >= 10**11:
        return value  # milliseconds
    return value * 1000  # seconds


def parse_event_time_ms(token: bytes) -> int | None:
    """Epoch milliseconds from an eventTime JSON token, or None when it cannot be read."""
    try:
        if token.startswith(b'"'):
            text = token[1:-1].decode("utf-8").strip()
            if text[:1].isdigit():
                try:
                    return _number_ms(text)
                except ValueError:
                    pass  # not a number after all: try it as an ISO-8601 instant
            moment = dt.datetime.fromisoformat(text)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=dt.UTC)
            return (moment - EPOCH) // dt.timedelta(milliseconds=1)
        return _number_ms(token.decode("ascii"))
    except (ValueError, UnicodeDecodeError, OverflowError):
        return None


# ── Per-object summary ─────────────────────────────────────────────────────


@dataclass(frozen=True, eq=False)
class ObjectSummary:
    records: int
    untimed: int
    minute_counts: dict[int, int]
    runs: tuple[tuple[int, int], ...]
    unreadable: bool = False


def to_runs(sorted_times: Sequence[int]) -> list[tuple[int, int]]:
    """Maximal spans in which no two consecutive events are more than GAP_MS apart."""
    runs: list[tuple[int, int]] = []
    if not sorted_times:
        return runs
    start = previous = sorted_times[0]
    for t in sorted_times[1:]:
        if t - previous > GAP_MS:
            runs.append((start, previous))
            start = t
        previous = t
    runs.append((start, previous))
    return runs


def merge_runs(runs: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union of runs from several objects or streams.

    Two runs closer than GAP_MS join, because the silence between them is not a gap.
    The result has exactly the gaps of the union of the underlying events.
    """
    merged: list[tuple[int, int]] = []
    for start, end in sorted(runs):
        if merged and start - merged[-1][1] <= GAP_MS:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def summarise_object(data: bytes) -> ObjectSummary:
    try:
        raw = gzip.decompress(data)
    except (OSError, EOFError, zlib.error):
        return ObjectSummary(0, 0, {}, (), unreadable=True)
    times: list[int] = []
    records = untimed = 0
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        records += 1
        match = _EVENT_TIME.search(line)
        t = parse_event_time_ms(match.group(1)) if match else None
        if t is None:
            untimed += 1
        else:
            times.append(t)
    times.sort()
    minute_counts = Counter(t // MINUTE_MS for t in times)
    return ObjectSummary(records, untimed, dict(minute_counts), tuple(to_runs(times)))


class SummaryCache:
    """Object summaries keyed by object key and ETag, so a rerun reads only new objects.

    Keys are deterministic (topic, partition and offsets), and a redelivered batch
    overwrites its key with the same content. The ETag guards the rare case where it
    does not.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, int, ObjectSummary]] = {}
        self.fetched = 0

    def get(self, store: ObjectStore, obj: StoredObject) -> ObjectSummary:
        entry = self._entries.get(obj.key)
        if entry and entry[0] == obj.etag and entry[1] == obj.size:
            return entry[2]
        self.fetched += 1
        summary = summarise_object(store.get_object(obj.key))
        self._entries[obj.key] = (obj.etag, obj.size, summary)
        return summary

    def retain(self, keys: set[str]) -> None:
        for key in [k for k in self._entries if k not in keys]:
            del self._entries[key]

    def __len__(self) -> int:
        return len(self._entries)


# ── Day audit ──────────────────────────────────────────────────────────────


def iso(ms: int) -> str:
    moment = EPOCH + dt.timedelta(milliseconds=ms)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def day_start_ms(day: dt.date) -> int:
    return (dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC) - EPOCH) // dt.timedelta(
        milliseconds=1
    )


@dataclass(frozen=True)
class Window:
    """The part of a UTC day being audited, and the wider range read around it."""

    day: dt.date
    start: int  # day start
    end: int  # day end for a complete day, else as_of less the settle time, by minute
    read_start: int
    read_end: int
    complete: bool

    @property
    def minutes(self) -> int:
        return max(0, (self.end - self.start) // MINUTE_MS)

    def minute_range(self) -> range:
        return range(self.start // MINUTE_MS, self.start // MINUTE_MS + self.minutes)


def window_for(day: dt.date, as_of_ms: int, settle_ms: int, finalise_ms: int) -> Window:
    start = day_start_ms(day)
    end_of_day = start + DAY_MS
    settled = (as_of_ms - settle_ms) // MINUTE_MS * MINUTE_MS
    complete = as_of_ms >= end_of_day + finalise_ms
    end = end_of_day if complete else max(start, min(end_of_day, settled))
    read_end = max(end, min(end_of_day + HOUR_MS, settled))
    return Window(day, start, end, start - HOUR_MS, read_end, complete)


def find_gaps(merged: Sequence[tuple[int, int]], window: Window) -> list[dict]:
    """Silences longer than GAP_MS that overlap the window, clipped to it."""
    silences: list[tuple[int, int]] = []
    cursor = window.read_start
    for start, end in merged:
        if start > cursor:
            silences.append((cursor, start))
        cursor = max(cursor, end)
    if window.read_end > cursor:
        silences.append((cursor, window.read_end))
    gaps = []
    for begin, finish in silences:
        if finish - begin <= GAP_MS:
            continue
        low, high = max(begin, window.start), min(finish, window.end)
        if high <= low:
            continue
        gaps.append(
            {
                "start": iso(low),
                "end": iso(high),
                "seconds": (high - low) / 1000,
                "silence_seconds": (finish - begin) / 1000,
                "ongoing": (not window.complete) and finish >= window.read_end,
            }
        )
    return gaps


@dataclass
class _StreamData:
    stream: Stream
    minutes: set[int]
    runs: list[tuple[int, int]]
    result: dict


def _audit_stream(
    store: ObjectStore, cache: SummaryCache, stream: Stream, window: Window, seen: set[str]
) -> _StreamData:
    day = window.day
    previous, following = day - dt.timedelta(days=1), day + dt.timedelta(days=1)
    filed_today = list(store.list_objects(stream.prefix(day)))
    around = list(store.list_objects(stream.prefix(previous, 23)))
    if window.read_end > window.start + DAY_MS:
        around += store.list_objects(stream.prefix(following, 0))

    in_window = window.minute_range()
    lo, hi = in_window.start, in_window.stop
    minutes: set[int] = set()
    runs: list[tuple[int, int]] = []
    records = untimed = unreadable = 0
    for obj, filed_under_day in [(o, True) for o in filed_today] + [(o, False) for o in around]:
        seen.add(obj.key)
        summary = cache.get(store, obj)
        if summary.unreadable:
            unreadable += filed_under_day
            continue
        untimed += summary.untimed if filed_under_day else 0
        for minute, count in summary.minute_counts.items():
            if lo <= minute < hi:
                minutes.add(minute)
                records += count
        runs.extend(summary.runs)
    merged = merge_runs(runs)
    gaps = find_gaps(merged, window)
    result = {
        "event_type": stream.event_type,
        "source": stream.source,
        "instrument": stream.instrument,
        "minutes_covered": len(minutes),
        "coverage_ratio": _ratio(len(minutes), window.minutes),
        "records": records,
        "objects": len(filed_today),
        "bytes_recorded": sum(o.size for o in filed_today),
        "unreadable_objects": unreadable,
        "untimed_records": untimed,
        "gap_count": len(gaps),
        "longest_gap_seconds": max((g["seconds"] for g in gaps), default=0.0),
        "gaps": gaps,
    }
    return _StreamData(stream, minutes, merged, result)


def _ratio(covered: int, expected: int) -> float:
    return round(covered / expected, 6) if expected else 0.0


def audit_day(
    store: ObjectStore,
    streams: Sequence[Stream],
    day: dt.date,
    as_of: dt.datetime,
    *,
    cache: SummaryCache | None = None,
    settle_seconds: int = 120,
    finalise_seconds: int = 600,
    bucket: str = "muninn-raw",
    seen: set[str] | None = None,
) -> dict:
    """Audit one UTC day as of ``as_of`` and return the report as a JSON-ready dict."""
    cache = cache if cache is not None else SummaryCache()
    seen = seen if seen is not None else set()
    as_of_ms = (as_of - EPOCH) // dt.timedelta(milliseconds=1)
    window = window_for(day, as_of_ms, settle_seconds * 1000, finalise_seconds * 1000)

    by_venue: dict[str, list[_StreamData]] = {}
    for stream in streams:
        data = _audit_stream(store, cache, stream, window, seen)
        by_venue.setdefault(stream.venue, []).append(data)

    venues = {}
    for venue, items in by_venue.items():
        trade = set().union(*(d.minutes for d in items if d.stream.kind == "trade"))
        book = set().union(*(d.minutes for d in items if d.stream.kind == "book"))
        every = set.intersection(*(d.minutes for d in items)) if items else set()
        covered = trade & book
        gaps = find_gaps(merge_runs(r for d in items for r in d.runs), window)
        results = [d.result for d in items]
        venues[venue] = {
            "minutes_covered": len(covered),
            "coverage_ratio": _ratio(len(covered), window.minutes),
            "minutes_with_trades": len(trade),
            "minutes_with_book": len(book),
            "minutes_covered_all_streams": len(every),
            "gap_count": len(gaps),
            "longest_gap_seconds": max((g["seconds"] for g in gaps), default=0.0),
            "gaps": gaps,
            "records": sum(r["records"] for r in results),
            "objects": sum(r["objects"] for r in results),
            "bytes_recorded": sum(r["bytes_recorded"] for r in results),
            "unreadable_objects": sum(r["unreadable_objects"] for r in results),
            "untimed_records": sum(r["untimed_records"] for r in results),
            "streams": results,
        }

    return {
        "schema": SCHEMA,
        "day": day.isoformat(),
        "complete": window.complete,
        "generated_at": iso(as_of_ms),
        "bucket": bucket,
        "window": {"start": iso(window.start), "end": iso(window.end), "minutes": window.minutes},
        "gap_threshold_seconds": GAP_MS // 1000,
        "venues": venues,
        "definitions": DEFINITIONS,
    }
