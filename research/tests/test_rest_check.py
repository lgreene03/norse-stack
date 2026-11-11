"""Closed klines from Binance's public REST API equal the archive rows.

The offline test uses a recorded pair of files for 2026-11-09, a day after the
holdout ends, so it belongs to neither the development nor the holdout period. The
archive side is the published daily file, verified against its CHECKSUM; its spot
timestamps are in microseconds, so the test also proves the unit normalisation.

The live test (``NORSE_LIVE_TESTS=1``) repeats the comparison against the API now,
on development months mirrored under NORSE_DATASETS_DIR and loaded through the
manifest and loader. It reports only whether rows are equal, never a price.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from norse_research.binance_archive import HttpFetcher, parse_checksum, sha256_hex
from norse_research.formats import DataIntegrityError, parse_klines, read_csv_member
from norse_research.rest_check import compare, fetch_rest_klines, parse_rest_klines, rest_url

FIXTURES = Path(__file__).parent / "fixtures"
DAY_START = int(datetime(2026, 11, 9, tzinfo=UTC).timestamp() * 1000)


def archive_rows(interval):
    name = f"BTCUSDT-{interval}-2026-11-09.zip"
    data = (FIXTURES / name).read_bytes()
    assert sha256_hex(data) == parse_checksum((FIXTURES / (name + ".CHECKSUM")).read_bytes(), name)
    return parse_klines(read_csv_member(data, name[:-4] + ".csv", 2_000_000), name)


def rest_rows(interval):
    return parse_rest_klines((FIXTURES / f"rest-BTCUSDT-{interval}-2026-11-09.json").read_bytes())


@pytest.mark.parametrize(("interval", "bars"), [("15m", 96), ("4h", 6)])
def test_recorded_rest_klines_equal_archive_rows(interval, bars):
    archive, rest = archive_rows(interval), rest_rows(interval)
    assert len(archive) == len(rest) == bars
    assert archive[0].open_time == DAY_START  # microseconds normalised to ms
    assert compare(archive, rest) == []


def test_compare_detects_a_changed_value_a_missing_bar_and_an_empty_reply():
    archive, rest = archive_rows("15m"), rest_rows("15m")
    changed = list(rest)
    changed[10] = changed[10]._replace(close=changed[10].close * 1.0001)
    assert compare(archive, changed) == [f"bar at {archive[10].open_time}: close differ"]
    assert compare(archive[:-1], rest[:-1]) == []
    # Bars are matched by time, so one missing bar is one difference, not a cascade.
    missing = archive[50].open_time
    assert compare([*archive[:50], *archive[51:]], rest) == [
        f"bar at {missing}: in the REST API, not the archive"
    ]
    assert compare(archive, [*rest[:50], *rest[51:]]) == [
        f"bar at {missing}: in the archive, not the REST API"
    ]
    assert compare(archive, []) == ["the REST API returned no closed klines"]


def test_rest_parser_refuses_unexpected_shapes():
    with pytest.raises(DataIntegrityError):
        parse_rest_klines(json.dumps([[1, 2, 3]]).encode())


def test_fetch_keeps_only_closed_bars_in_range():
    body = (FIXTURES / "rest-BTCUSDT-4h-2026-11-09.json").read_bytes()
    urls = []

    def fetch(url, max_bytes):
        urls.append(url)
        return body

    end = DAY_START + 86_400_000
    now = DAY_START + 5 * 4 * 3_600_000  # five bars closed, the sixth still forming
    rows = fetch_rest_klines("BTCUSDT", "4h", DAY_START, end, fetch, now)
    assert len(rows) == 5
    assert urls == [rest_url("BTCUSDT", "4h", DAY_START, end)]
    assert urls[0].startswith("https://data-api.binance.vision/api/v3/klines?")


LIVE = os.environ.get("NORSE_LIVE_TESTS") == "1"


@pytest.mark.skipif(not LIVE, reason="live API test; set NORSE_LIVE_TESTS=1")
def test_live_rest_klines_equal_manifested_rows_except_recorded_differences():
    from norse_research.datasets import live_rest_check, load_register

    register = load_register()
    results = live_rest_check(HttpFetcher(min_interval=0.5))
    assert len(results) == 24
    assert all(bars > 0 for bars, _ in results.values())
    unrecorded = {
        window: [d for d in found if tuple(d) not in register[window.split()[0]]]
        for window, (_, found) in results.items()
    }
    assert {k: v for k, v in unrecorded.items() if v} == {}


def test_register_entries_and_the_committed_register_hold_no_prices():
    from norse_research.datasets import REST_REGISTER, _register_entry, load_register

    assert _register_entry("bar at 1520173800000: close, volume differ") == [
        "2018-03-04 14:30",
        "close, volume differ",
    ]
    assert _register_entry("the REST API returned no closed klines") == [
        "",
        "the REST API returned no closed klines",
    ]
    register = load_register(REST_REGISTER)
    assert set(register) == {
        f"spot/klines/{s}/{i}" for s in ("BTCUSDT", "ETHUSDT") for i in ("1d", "4h", "15m")
    }
    words = {w for entries in register.values() for _, what in entries for w in what.split()}
    assert all(not any(c.isdigit() for c in w) for w in words)
