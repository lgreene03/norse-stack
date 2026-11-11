"""The gap audit against fixture objects, including a planted gap."""

import datetime as dt
import gzip

import pytest
from audit import (
    SummaryCache,
    audit_day,
    merge_runs,
    parse_event_time_ms,
    streams_for,
    summarise_object,
)
from fixtures import FakeStore, every, ms

DAY = dt.date(2026, 11, 11)
AFTER_DAY = dt.datetime(2026, 11, 12, 0, 30, tzinfo=dt.UTC)
SOURCES = ["binance.spot.v1", "okx.spot.v1"]
STREAMS = streams_for(SOURCES, ["BTC-USDT"])


def stream(event_type, source, instrument="BTC-USDT"):
    return next(
        s
        for s in streams_for(SOURCES, [instrument])
        if s.event_type == event_type and s.source == source
    )


BINANCE_TRADE = stream("events.trade", "binance.spot.v1")
BINANCE_BOOK = stream("events.book.snapshot", "binance.spot.v1")
OKX_TRADE = stream("events.trade", "okx.spot.v1")
OKX_BOOK = stream("events.book.snapshot", "okx.spot.v1")

ALL_DAY = every("2026-11-10T23:00:00+00:00", "2026-11-12T01:00:00+00:00", 10)


def by_stream(report, s):
    return next(
        r
        for r in report["venues"][s.venue]["streams"]
        if r["event_type"] == s.event_type and r["instrument"] == s.instrument
    )


@pytest.fixture
def healthy_store():
    store = FakeStore()
    for s in STREAMS:
        store.record(s, ALL_DAY)
    return store


def test_a_healthy_day_is_fully_covered_with_no_gaps(healthy_store):
    report = audit_day(healthy_store, STREAMS, DAY, AFTER_DAY)
    assert report["complete"] is True
    assert report["window"] == {
        "start": "2026-11-11T00:00:00.000Z",
        "end": "2026-11-12T00:00:00.000Z",
        "minutes": 1440,
    }
    for venue in ("binance", "okx"):
        v = report["venues"][venue]
        assert v["minutes_covered"] == 1440
        assert v["coverage_ratio"] == 1.0
        assert v["minutes_covered_all_streams"] == 1440
        assert v["gap_count"] == 0
        assert v["records"] == 2 * 8640  # trade and book, one event every 10 s
    assert by_stream(report, OKX_BOOK)["records"] == 8640


def test_planted_gap_is_found_with_its_exact_bounds():
    store = FakeStore()
    for s in (BINANCE_BOOK, OKX_TRADE, OKX_BOOK):
        store.record(s, ALL_DAY)
    # Plant a five-minute hole in Binance trades: nothing strictly between 10:00 and 10:05.
    hole = (ms("2026-11-11T10:00:00+00:00"), ms("2026-11-11T10:05:00+00:00"))
    store.record(BINANCE_TRADE, [t for t in ALL_DAY if not hole[0] < t < hole[1]])

    report = audit_day(store, STREAMS, DAY, AFTER_DAY)
    trade = by_stream(report, BINANCE_TRADE)
    assert trade["gap_count"] == 1
    assert trade["gaps"] == [
        {
            "start": "2026-11-11T10:00:00.000Z",
            "end": "2026-11-11T10:05:00.000Z",
            "seconds": 300.0,
            "silence_seconds": 300.0,
            "ongoing": False,
        }
    ]
    assert trade["longest_gap_seconds"] == 300.0
    # 10:00 holds the last event before the hole; 10:01 to 10:04 hold nothing.
    assert trade["minutes_covered"] == 1436

    binance = report["venues"]["binance"]
    assert binance["minutes_with_trades"] == 1436
    assert binance["minutes_with_book"] == 1440
    assert binance["minutes_covered"] == 1436  # P3 needs a trade and a book event
    assert binance["minutes_covered_all_streams"] == 1436
    # The venue itself never fell silent: its book stream kept recording.
    assert binance["gap_count"] == 0
    assert report["venues"]["okx"]["minutes_covered"] == 1440


def test_a_venue_gap_needs_every_stream_of_the_venue_to_be_silent():
    store = FakeStore()
    hole = (ms("2026-11-11T06:00:00+00:00"), ms("2026-11-11T06:02:30+00:00"))
    for s in (BINANCE_TRADE, BINANCE_BOOK):
        store.record(s, [t for t in ALL_DAY if not hole[0] < t < hole[1]])
    for s in (OKX_TRADE, OKX_BOOK):
        store.record(s, ALL_DAY)
    report = audit_day(store, STREAMS, DAY, AFTER_DAY)
    binance = report["venues"]["binance"]
    assert binance["gap_count"] == 1
    assert binance["gaps"][0]["seconds"] == 150.0
    assert binance["minutes_covered"] == 1439  # only 06:01 is empty; 06:02:30 is recorded
    assert report["venues"]["okx"]["gap_count"] == 0


def test_the_threshold_is_strictly_longer_than_sixty_seconds():
    store = FakeStore()
    sixty = every("2026-11-10T23:00:00+00:00", "2026-11-12T01:00:00+00:00", 60)
    store.record(OKX_TRADE, sixty)
    report = audit_day(store, [OKX_TRADE], DAY, AFTER_DAY)
    assert by_stream(report, OKX_TRADE)["gap_count"] == 0

    store = FakeStore()
    moved = [t + 1000 if t == ms("2026-11-11T12:00:00+00:00") else t for t in sixty]
    store.record(OKX_TRADE, moved)
    report = audit_day(store, [OKX_TRADE], DAY, AFTER_DAY)
    gaps = by_stream(report, OKX_TRADE)["gaps"]
    assert [g["seconds"] for g in gaps] == [61.0]


def test_late_arrivals_filed_under_the_next_day_count_for_their_own_day():
    store = FakeStore()
    # The last two minutes of the day arrive five minutes late, after midnight.
    late_from = ms("2026-11-11T23:58:00+00:00")
    on_time = [t for t in ALL_DAY if t < late_from]
    late = [t for t in ALL_DAY if late_from <= t < ms("2026-11-12T00:00:00+00:00")]
    store.record(OKX_BOOK, on_time)
    store.record(OKX_BOOK, late, arrival_lag_ms=300_000)
    store.record(OKX_BOOK, every("2026-11-12T00:00:00+00:00", "2026-11-12T01:00:00+00:00", 10))

    report = audit_day(store, [OKX_BOOK], DAY, AFTER_DAY)
    book = by_stream(report, OKX_BOOK)
    assert book["minutes_covered"] == 1440
    assert book["gap_count"] == 0

    # Proof that those two minutes came from the next day's partition.
    for key in [k for k in store.objects if "/day=12/" in k]:
        del store.objects[key]
    book = by_stream(audit_day(store, [OKX_BOOK], DAY, AFTER_DAY), OKX_BOOK)
    assert book["minutes_covered"] == 1438


def test_events_from_the_previous_day_are_read_but_not_counted():
    store = FakeStore()
    store.record(OKX_BOOK, ALL_DAY)
    report = audit_day(store, [OKX_BOOK], DAY, AFTER_DAY)
    book = by_stream(report, OKX_BOOK)
    assert book["records"] == 8640
    assert book["gap_count"] == 0  # the 23:00 to 00:00 hour proves no gap at midnight


def test_a_missing_stream_is_one_gap_covering_the_whole_day(healthy_store):
    for key in [k for k in healthy_store.objects if "okx" in k and "book" in k]:
        del healthy_store.objects[key]
    report = audit_day(healthy_store, STREAMS, DAY, AFTER_DAY)
    book = by_stream(report, OKX_BOOK)
    assert book["minutes_covered"] == 0
    assert book["gaps"] == [
        {
            "start": "2026-11-11T00:00:00.000Z",
            "end": "2026-11-12T00:00:00.000Z",
            "seconds": 86400.0,
            # Read from 23:00 the day before to as_of less the settle time (00:28).
            "silence_seconds": 91680.0,
            "ongoing": False,
        }
    ]
    okx = report["venues"]["okx"]
    assert okx["minutes_covered"] == 0  # trades alone do not satisfy P3
    assert okx["minutes_with_trades"] == 1440


def test_a_gap_across_midnight_is_reported_on_both_days():
    store = FakeStore()
    hole = (ms("2026-11-11T23:59:30+00:00"), ms("2026-11-12T00:01:00+00:00"))
    store.record(OKX_BOOK, [t for t in ALL_DAY if not hole[0] < t < hole[1]])
    store.record(OKX_BOOK, every("2026-11-12T01:00:00+00:00", "2026-11-13T01:00:00+00:00", 10))
    first = by_stream(audit_day(store, [OKX_BOOK], DAY, AFTER_DAY), OKX_BOOK)
    assert [(g["seconds"], g["silence_seconds"]) for g in first["gaps"]] == [(30.0, 90.0)]
    next_day = audit_day(
        store, [OKX_BOOK], DAY + dt.timedelta(days=1), AFTER_DAY + dt.timedelta(days=1)
    )
    second = by_stream(next_day, OKX_BOOK)
    assert [(g["seconds"], g["silence_seconds"]) for g in second["gaps"]] == [(60.0, 90.0)]


def test_a_partial_day_stops_short_of_now_and_flags_an_ongoing_gap():
    store = FakeStore()
    store.record(OKX_BOOK, every("2026-11-10T23:00:00+00:00", "2026-11-11T00:30:00+00:00", 10))
    as_of = dt.datetime(2026, 11, 11, 1, 0, 30, tzinfo=dt.UTC)
    report = audit_day(store, [OKX_BOOK], DAY, as_of)
    assert report["complete"] is False
    # Two minutes of settle time for the recorder's flush, rounded down to the minute.
    assert report["window"]["end"] == "2026-11-11T00:58:00.000Z"
    assert report["window"]["minutes"] == 58
    book = by_stream(report, OKX_BOOK)
    assert book["minutes_covered"] == 30
    assert book["gaps"] == [
        {
            "start": "2026-11-11T00:29:50.000Z",
            "end": "2026-11-11T00:58:00.000Z",
            "seconds": 1690.0,
            "silence_seconds": 1690.0,
            "ongoing": True,
        }
    ]


def test_a_live_stream_on_a_partial_day_has_no_trailing_gap():
    store = FakeStore()
    as_of = dt.datetime(2026, 11, 11, 1, 0, 30, tzinfo=dt.UTC)
    # Stored up to one flush interval before now, as a healthy recorder leaves it.
    store.record(OKX_BOOK, every("2026-11-10T23:00:00+00:00", "2026-11-11T00:59:30+00:00", 1))
    book = by_stream(audit_day(store, [OKX_BOOK], DAY, as_of), OKX_BOOK)
    assert book["gap_count"] == 0
    assert book["minutes_covered"] == 58


def test_a_day_only_becomes_complete_after_the_finalise_delay(healthy_store):
    just_after = dt.datetime(2026, 11, 12, 0, 5, tzinfo=dt.UTC)
    assert audit_day(healthy_store, STREAMS, DAY, just_after)["complete"] is False
    assert audit_day(healthy_store, STREAMS, DAY, AFTER_DAY)["complete"] is True


def test_bytes_count_only_objects_filed_under_the_day(healthy_store):
    report = audit_day(healthy_store, [OKX_TRADE], DAY, AFTER_DAY)
    expected = sum(
        len(v) for k, v in healthy_store.objects.items() if k.startswith(OKX_TRADE.prefix(DAY))
    )
    trade = by_stream(report, OKX_TRADE)
    assert trade["bytes_recorded"] == expected
    assert trade["objects"] == 24 * 60
    assert report["venues"]["okx"]["bytes_recorded"] == expected


def test_an_unreadable_object_is_counted_and_does_not_stop_the_audit(healthy_store):
    key = next(k for k in sorted(healthy_store.objects) if k.startswith(OKX_TRADE.prefix(DAY, 5)))
    healthy_store.objects[key] = b"not gzip at all"
    report = audit_day(healthy_store, STREAMS, DAY, AFTER_DAY)
    trade = by_stream(report, OKX_TRADE)
    assert trade["unreadable_objects"] == 1
    assert trade["minutes_covered"] == 1439  # that object held the 05:00 minute


def test_lines_without_an_event_time_are_counted_as_untimed():
    data = gzip.compress(
        b'{"topic":"events.trade","value":{"eventTime":1794355200.549000000}}\n'
        b'{"topic":"events.trade","value":{"eventId":"no-time"}}\n'
        b'{"topic":"events.trade","value":{"eventTime":"garbage"}}\n'
    )
    summary = summarise_object(data)
    assert (summary.records, summary.untimed) == (3, 2)
    assert summary.runs == ((1794355200549, 1794355200549),)


def test_the_cache_reads_each_object_once(healthy_store):
    cache = SummaryCache()
    audit_day(healthy_store, STREAMS, DAY, AFTER_DAY, cache=cache)
    first = healthy_store.gets
    assert first == len(healthy_store.objects)
    audit_day(healthy_store, STREAMS, DAY, AFTER_DAY, cache=cache)
    assert healthy_store.gets == first
    assert cache.fetched == first


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        (b"1794355200.549000000", 1794355200549),
        (b"1794355200.0124915", 1794355200012),
        (b"1794355200", 1794355200000),
        (b"1794355200549", 1794355200549),
        (b"1794355200549000", 1794355200549),
        (b"1794355200549000000", 1794355200549),
        (b"1.794355200549E9", 1794355200549),
        (b'"2026-11-11T00:00:00.549Z"', 1794355200549),
        (b'"2026-11-11T00:00:00.549+00:00"', 1794355200549),
        (b'"1794355200.549"', 1794355200549),
        (b'"not a time"', None),
        (b"-5", None),
    ],
)
def test_event_time_formats(token, expected):
    assert parse_event_time_ms(token) == expected


def test_merging_joins_runs_closer_than_the_threshold():
    assert merge_runs([(0, 10), (70_000, 80_000), (5, 20), (200_000, 200_000)]) == [
        (0, 20),
        (70_000, 80_000),
        (200_000, 200_000),
    ]
    assert merge_runs([(0, 10), (60_010, 70_000)]) == [(0, 70_000)]
    assert merge_runs([(0, 10), (60_011, 70_000)]) == [(0, 10), (60_011, 70_000)]
