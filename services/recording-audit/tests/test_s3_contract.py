"""Contract test: the S3 client and the audit against a real S3 server.

CI starts a throwaway SeaweedFS (MinIO's images can no longer be pulled anonymously;
muninn's integration tests made the same move) and sets
RECORDING_AUDIT_TEST_S3_ENDPOINT, with credentials passed by file
(S3_ACCESS_KEY_FILE, S3_SECRET_KEY_FILE). It also sets
RECORDING_AUDIT_REQUIRE_S3_CONTRACT=1, which turns a missing endpoint into a failure,
so the job cannot go green by skipping this file. Locally, without an endpoint, it
skips. Never point it at the live store: it creates a bucket and writes to it.
"""

import datetime as dt
import os
import uuid

import pytest
from audit import audit_day, streams_for
from fixtures import FakeStore, every, ms
from s3client import Credentials, S3Client, S3Error, load_credentials

ENDPOINT = os.environ.get("RECORDING_AUDIT_TEST_S3_ENDPOINT", "")
REQUIRED = os.environ.get("RECORDING_AUDIT_REQUIRE_S3_CONTRACT") == "1"
DAY = dt.date(2026, 11, 11)
AFTER_DAY = dt.datetime(2026, 11, 12, 0, 30, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def client():
    if not ENDPOINT:
        if REQUIRED:
            pytest.fail("RECORDING_AUDIT_REQUIRE_S3_CONTRACT=1 but no test S3 endpoint is set")
        pytest.skip("no test S3 server: set RECORDING_AUDIT_TEST_S3_ENDPOINT to run")
    c = S3Client(ENDPOINT, f"contract-{uuid.uuid4().hex[:12]}", load_credentials(os.environ))
    c.create_bucket()
    return c


def test_the_audit_finds_a_planted_gap_through_real_s3(client):
    streams = streams_for(["binance.spot.v1"], ["BTC-USDT"])
    fixture = FakeStore()
    hole = (ms("2026-11-11T10:00:00+00:00"), ms("2026-11-11T10:05:00+00:00"))
    times = [
        t
        for t in every("2026-11-11T09:00:00+00:00", "2026-11-11T11:00:00+00:00", 10)
        if not hole[0] < t < hole[1]
    ]
    for s in streams:
        fixture.record(s, times, flush_ms=600_000)
    for key, data in fixture.objects.items():
        client.put_object(key, data)

    listed = {o.key: o for o in client.list_objects("events.trade/")}
    expected = {k: v for k, v in fixture.objects.items() if k.startswith("events.trade/")}
    assert set(listed) == set(expected)
    assert all(listed[k].size == len(v) for k, v in expected.items())
    assert all(listed[k].etag for k in listed)

    report = audit_day(client, streams, DAY, AFTER_DAY, bucket=client.bucket)
    trade = next(s for s in report["venues"]["binance"]["streams"] if s["event_type"] == "events.trade")
    planted = [g for g in trade["gaps"] if g["start"] == "2026-11-11T10:00:00.000Z"]
    assert planted == [
        {
            "start": "2026-11-11T10:00:00.000Z",
            "end": "2026-11-11T10:05:00.000Z",
            "seconds": 300.0,
            "silence_seconds": 300.0,
            "ongoing": False,
        }
    ]
    assert trade["minutes_covered"] == 116  # 09:00 to 10:59, less 10:01 to 10:04
    assert trade["bytes_recorded"] == sum(len(v) for v in expected.values())


def test_listing_follows_continuation_tokens(client):
    for i in range(5):
        client.put_object(f"paged/item-{i}", b"x")
    keys = [o.key for o in client.list_objects("paged/", page_size=2)]
    assert keys == [f"paged/item-{i}" for i in range(5)]


def test_a_missing_object_is_an_error_not_an_empty_body(client):
    with pytest.raises(S3Error) as error:
        client.get_object("no/such/key")
    assert error.value.status == 404


def test_a_wrong_secret_is_refused(client):
    good = load_credentials(os.environ)
    wrong = S3Client(ENDPOINT, client.bucket, Credentials(good.access_key, "not-the-secret"))
    with pytest.raises(S3Error) as error:
        list(wrong.list_objects("events.trade/"))
    assert error.value.status == 403
    assert "not-the-secret" not in str(error.value)
