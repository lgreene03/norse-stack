"""The scheduled job around the audit: which days, which files, which metrics."""

import csv
import datetime as dt
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from audit import streams_for
from fixtures import FakeStore, every
from recording_audit import (
    CSV_COLUMNS,
    Auditor,
    Config,
    ReportStore,
    make_handler,
    render_metrics,
)
from s3client import ConfigError

SOURCES = ("binance.spot.v1", "okx.spot.v1")
INSTRUMENTS = ("BTC-USDT", "ETH-USDT")


def healthy_store(start="2026-11-09T23:00:00+00:00", end="2026-11-11T01:10:00+00:00"):
    store = FakeStore()
    for s in streams_for(SOURCES, INSTRUMENTS):
        store.record(s, every(start, end, 20))
    return store


def auditor(tmp_path, store, now, **overrides):
    config = Config(
        sources=SOURCES,
        instruments=INSTRUMENTS,
        report_dir=str(tmp_path),
        **{"backfill_days": 1, **overrides},
    )
    clock = {"now": now}
    a = Auditor(config, store, ReportStore(tmp_path), clock=lambda: clock["now"])
    return a, clock


def test_config_defaults_match_the_live_recorder():
    config = Config.from_env({})
    assert config.sources == ("binance.spot.v1", "okx.spot.v1")
    assert config.instruments == ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT")
    assert config.bucket == "muninn-raw"
    assert config.port == 8098


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"AUDIT_INSTRUMENTS": "BTC-USDT,../etc"}, "AUDIT_INSTRUMENTS"),
        ({"AUDIT_SOURCES": " , "}, "AUDIT_SOURCES"),
        ({"AUDIT_INTERVAL_SECONDS": "5"}, "at least 60"),
        ({"AUDIT_BACKFILL_DAYS": "many"}, "whole number"),
        ({"AUDIT_EARLIEST_DAY": "11/10/2026"}, "AUDIT_EARLIEST_DAY"),
    ],
)
def test_config_rejects_bad_values(env, message):
    with pytest.raises(ConfigError, match=message):
        Config.from_env(env)


def test_a_run_writes_a_final_report_for_yesterday_and_a_partial_one_for_today(tmp_path):
    now = dt.datetime(2026, 11, 11, 1, 0, tzinfo=dt.UTC)
    a, _ = auditor(tmp_path, healthy_store(), now)
    assert a.run_once() is True

    yesterday = json.loads((tmp_path / "2026-11-10.json").read_text())
    today = json.loads((tmp_path / "2026-11-11.json").read_text())
    assert yesterday["complete"] is True
    assert yesterday["window"]["minutes"] == 1440
    assert yesterday["venues"]["okx"]["minutes_covered"] == 1440
    assert today["complete"] is False
    assert today["window"]["minutes"] == 58
    assert today["venues"]["binance"]["gap_count"] == 0

    with (tmp_path / "daily.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == CSV_COLUMNS
    assert [(r["day"], r["venue"], r["complete"], r["p3_met"]) for r in rows] == [
        ("2026-11-10", "binance", "true", "true"),
        ("2026-11-10", "okx", "true", "true"),
        ("2026-11-11", "binance", "false", "true"),
        ("2026-11-11", "okx", "false", "true"),
    ]
    assert rows[0]["coverage_pct"] == "100.000"


def test_a_complete_report_is_never_rewritten(tmp_path):
    now = dt.datetime(2026, 11, 11, 1, 0, tzinfo=dt.UTC)
    store = healthy_store()
    a, clock = auditor(tmp_path, store, now)
    a.run_once()
    final = (tmp_path / "2026-11-10.json").read_text()
    gets = store.gets

    clock["now"] = now + dt.timedelta(minutes=15)
    a.run_once()
    assert (tmp_path / "2026-11-10.json").read_text() == final
    assert a.days_due(clock["now"].date()) == [dt.date(2026, 11, 11)]
    # Only the newly listed objects were fetched; today's earlier objects came from cache.
    assert store.gets == gets


def test_rows_for_a_day_are_replaced_not_duplicated(tmp_path):
    now = dt.datetime(2026, 11, 11, 1, 0, tzinfo=dt.UTC)
    a, clock = auditor(tmp_path, healthy_store(), now)
    a.run_once()
    clock["now"] = now + dt.timedelta(minutes=10)
    a.run_once()
    with (tmp_path / "daily.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 4
    today = [r for r in rows if r["day"] == "2026-11-11"]
    assert {r["window_minutes"] for r in today} == {"68"}


def test_backfill_respects_the_earliest_day(tmp_path):
    now = dt.datetime(2026, 11, 11, 1, 0, tzinfo=dt.UTC)
    a, _ = auditor(tmp_path, FakeStore(), now, backfill_days=30,
                   earliest_day=dt.date(2026, 11, 9))
    # Today first, then older days newest first, and nothing before the earliest day.
    assert a.days_due(now.date()) == [
        dt.date(2026, 11, 11),
        dt.date(2026, 11, 10),
        dt.date(2026, 11, 9),
    ]
    a, _ = auditor(tmp_path, FakeStore(), now, earliest_day=dt.date(2026, 11, 12))
    assert a.days_due(now.date()) == []


def test_backfilled_days_do_not_stay_in_the_cache(tmp_path):
    now = dt.datetime(2026, 11, 11, 1, 0, tzinfo=dt.UTC)
    store = healthy_store(start="2026-11-07T23:00:00+00:00")
    a, _ = auditor(tmp_path, store, now, backfill_days=3)
    a.run_once()
    assert sorted(p.name for p in tmp_path.glob("*.json")) == [
        "2026-11-08.json",
        "2026-11-09.json",
        "2026-11-10.json",
        "2026-11-11.json",
    ]
    cached = {k.split("/year=")[1].rsplit("/", 1)[0] for k in a.cache._entries}
    days = {c.split("/hour=")[0] for c in cached}
    # Today and yesterday stay cached, with the hour before yesterday that its audit
    # read; the 8th and the rest of the 9th were backfilled and dropped.
    assert days == {"2026/month=11/day=09", "2026/month=11/day=10", "2026/month=11/day=11"}
    assert {c for c in cached if "day=09" in c} == {"2026/month=11/day=09/hour=23"}


def test_a_store_failure_is_counted_and_the_job_carries_on(tmp_path):
    class Down(FakeStore):
        def list_objects(self, prefix):
            raise ConnectionError("minio unreachable")

    now = dt.datetime(2026, 11, 11, 1, 0, tzinfo=dt.UTC)
    a, _ = auditor(tmp_path, Down(), now)
    assert a.run_once() is False
    assert "minio unreachable" in a.last_error
    text = a.metrics()
    assert 'recording_audit_runs_total{result="failure"} 1' in text
    assert "recording_audit_last_success_timestamp_seconds 0" in text
    assert not list(tmp_path.glob("*.json"))


def test_metrics_cover_venues_and_streams_with_bounded_labels(tmp_path):
    now = dt.datetime(2026, 11, 11, 1, 0, tzinfo=dt.UTC)
    a, _ = auditor(tmp_path, healthy_store(), now)
    a.run_once()
    text = a.metrics()
    assert 'recording_audit_venue_minutes_covered{venue="okx",day="yesterday"} 1440' in text
    assert 'recording_audit_venue_minutes_covered{venue="okx",day="today"} 58' in text
    assert 'recording_audit_report_complete{day="yesterday"} 1' in text
    assert 'recording_audit_report_complete{day="today"} 0' in text
    assert (
        'recording_audit_stream_minutes_covered{venue="binance",event_type="trade",'
        'instrument="ETH-USDT",day="today"} 58'
    ) in text
    assert 'recording_audit_runs_total{result="success"} 1' in text
    stream_series = [
        line for line in text.splitlines() if line.startswith("recording_audit_stream_gaps{")
    ]
    # 2 venues x 2 event types x 2 instruments x 2 days, and nothing else.
    assert len(stream_series) == 16
    for line in text.splitlines():
        if line.startswith("recording_audit_") and "day=" in line:
            assert 'day="today"' in line or 'day="yesterday"' in line


def test_metrics_render_without_any_report_yet():
    text = render_metrics({}, {})
    assert "# TYPE recording_audit_runs_total counter" in text
    assert "recording_audit_venue" not in text


def test_http_serves_metrics_and_health(tmp_path):
    now = dt.datetime(2026, 11, 11, 1, 0, tzinfo=dt.UTC)
    a, _ = auditor(tmp_path, healthy_store(), now)
    a.run_once()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(a))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/metrics", timeout=5) as response:
            assert response.headers["Content-Type"].startswith("text/plain; version=0.0.4")
            assert b"recording_audit_venue_coverage_ratio" in response.read()
        with urllib.request.urlopen(f"{base}/healthz", timeout=5) as response:
            assert json.loads(response.read())["status"] == "ok"
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"{base}/nothing", timeout=5)
        assert missing.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
