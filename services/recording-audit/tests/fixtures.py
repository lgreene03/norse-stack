"""Fixture objects laid out exactly as muninn's recorder writes them.

Keys follow muninn's RawObjectKeys (filed by Kafka arrival hour, offsets
zero-padded), lines follow RawRecordEncoder (an envelope around the event's own
JSON, eventTime in decimal seconds as Jackson writes an Instant), and each object
is gzipped JSON lines, flushed at least once a minute.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
from collections.abc import Iterable

from audit import EPOCH, Stream
from s3client import StoredObject

ARRIVAL_LAG_MS = 150  # Kafka timestamp minus eventTime: ingest latency


def ms(text: str) -> int:
    """Epoch milliseconds from an ISO-8601 UTC instant such as 2026-11-11T10:00:00Z."""
    moment = dt.datetime.fromisoformat(text)
    return (moment - EPOCH) // dt.timedelta(milliseconds=1)


def every(start: str, end: str, step_s: float) -> list[int]:
    """Event times from start (inclusive) to end (exclusive), step_s apart."""
    first, last, step = ms(start), ms(end), int(step_s * 1000)
    return list(range(first, last, step))


def line(stream: Stream, t_ms: int, offset: int) -> bytes:
    value = {
        "eventId": f"e-{offset}",
        "eventTime": f"{t_ms // 1000}.{t_ms % 1000:03d}000000",
        "ingestTime": f"{(t_ms + 100) // 1000}.{(t_ms + 100) % 1000:03d}000000",
        "source": stream.source,
        "instrument": {"symbol": stream.instrument},
        "schemaVersion": 1,
    }
    if stream.kind == "book":
        value.update({"bids": [[100.0, 1.0]], "asks": [[100.5, 2.0]], "depth": 20})
    else:
        value.update({"price": 100.25, "size": 0.5, "side": "BUY"})
    envelope = (
        f'{{"topic":"{stream.event_type}","partition":0,"offset":{offset},'
        f'"timestamp":{t_ms + ARRIVAL_LAG_MS},"key":"{stream.instrument}","value":'
    )
    return envelope.encode() + json.dumps(value, separators=(",", ":")).encode() + b"}"


class FakeStore:
    """An in-memory object store with the same two calls the S3 client offers."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.gets = 0
        self._offset = 0

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def list_objects(self, prefix: str) -> Iterable[StoredObject]:
        for key in sorted(self.objects):
            if key.startswith(prefix):
                data = self.objects[key]
                yield StoredObject(key, len(data), hashlib.md5(data).hexdigest())

    def get_object(self, key: str) -> bytes:
        self.gets += 1
        return self.objects[key]

    def record(
        self,
        stream: Stream,
        times_ms: Iterable[int],
        *,
        flush_ms: int = 60_000,
        arrival_lag_ms: int = ARRIVAL_LAG_MS,
    ) -> None:
        """Store events as the recorder would: one object per arrival hour and flush."""
        batches: dict[tuple[int, int], list[int]] = {}
        for t in sorted(times_ms):
            arrival = t + arrival_lag_ms
            batches.setdefault((arrival // 3_600_000, arrival // flush_ms), []).append(t)
        for (hour, _), times in sorted(batches.items()):
            first = self._offset
            lines = []
            for t in times:
                lines.append(line(stream, t, self._offset))
                self._offset += 1
            hour_start = EPOCH + dt.timedelta(hours=hour)
            key = (
                f"{stream.prefix(hour_start.date(), hour_start.hour)}"
                f"part-p0-{first:020d}-{self._offset - 1:020d}.jsonl.gz"
            )
            self.put(key, gzip.compress(b"\n".join(lines) + b"\n"))
