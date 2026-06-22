"""Unit tests for Mimir's point-in-time feature store.

Run with: python3 -m pytest services/mimir/tests/  (kafka is stubbed in conftest)

These seed the SQLite store DIRECTLY via FeatureStore.store() — no Kafka — so
the no-lookahead semantics are tested in isolation. Each test gets its own
temp-file SQLite DB so rows never leak between tests.
"""

import pytest

import mimir


@pytest.fixture
def fs(tmp_path):
    """Fresh file-backed FeatureStore per test (temp dir, real sqlite file)."""
    db = tmp_path / "mimir-test.db"
    return mimir.FeatureStore(db_path=str(db))


def _feat(obi):
    """A minimal feature payload shaped like obi-bridge's ``values`` block."""
    return {"featureName": "obi", "values": {"obi": obi}}


# Canonical timestamps used across the PIT tests. t1 < t2 < t3.
T1 = "2026-06-20T00:00:00Z"  # feature's event_time
T2 = "2026-06-20T00:00:30Z"  # query time strictly between event and ingest
T3 = "2026-06-20T00:01:00Z"  # ingest_time (physically arrived later)


# ---------------------------------------------------------------------------
# (1) THE POINT-IN-TIME / NO-LOOKAHEAD TEST.
#
# A feature with event_time=t1 but ingest_time=t3 must be INVISIBLE to a query
# as_of=t2 (t1<t2<t3): at t2 the data had not physically arrived yet, so a
# consumer running at t2 could not have known it. It becomes visible only at
# as_of>=t3. This ingest_time guard is the whole point of Mimir.
# ---------------------------------------------------------------------------
def test_pit_late_arriving_feature_invisible_before_ingest(fs):
    fs.store("BTC-USDT", T1, _feat(0.7), ingest_time=T3)

    # as_of=t2: event_time<=t2 but ingest_time(t3) > t2 -> NOT known yet.
    res = fs.query_as_of(as_of=T2, instrument="BTC-USDT")
    assert res["features"] == [], "late-arriving feature must not be visible at t2"

    # as_of=t3: now physically received -> visible.
    res = fs.query_as_of(as_of=T3, instrument="BTC-USDT")
    assert len(res["features"]) == 1
    f = res["features"][0]
    assert f["instrument"] == "BTC-USDT"
    assert f["feature"]["values"]["obi"] == 0.7


def test_pit_basis_label_and_asof_echoed(fs):
    fs.store("BTC-USDT", T1, _feat(0.5), ingest_time=T1)
    res = fs.query_as_of(as_of=T3, instrument="BTC-USDT")
    assert res["basis"] == (
        "point-in-time (event_time<=as_of AND ingest_time<=as_of)"
    )
    # asOf is normalized to canonical UTC form.
    assert res["asOf"].startswith("2026-06-20T00:01:00")


def test_pit_event_after_asof_invisible_even_if_ingested(fs):
    """The event_time guard (not just ingest_time): a feature whose event_time
    is in the future relative to as_of must not appear, even if already
    ingested."""
    # event_time=t3, ingested instantly at t3; query as_of=t2 (before event).
    fs.store("BTC-USDT", T3, _feat(0.9), ingest_time=T3)
    res = fs.query_as_of(as_of=T2, instrument="BTC-USDT")
    assert res["features"] == []


# ---------------------------------------------------------------------------
# (2) LATEST-PER-INSTRUMENT.
# ---------------------------------------------------------------------------
def test_latest_per_instrument(fs):
    e1 = "2026-06-20T00:00:00Z"
    e2 = "2026-06-20T01:00:00Z"
    fs.store("BTC-USDT", e1, _feat(0.10), ingest_time=e1)
    fs.store("BTC-USDT", e2, _feat(0.20), ingest_time=e2)

    # as_of after BOTH -> the later event wins.
    res = fs.query_as_of(as_of="2026-06-20T02:00:00Z", instrument="BTC-USDT")
    assert len(res["features"]) == 1
    assert res["features"][0]["feature"]["values"]["obi"] == 0.20
    assert res["features"][0]["event_time"].startswith("2026-06-20T01:00:00")

    # as_of strictly BETWEEN the two -> only the earlier event is known.
    res = fs.query_as_of(as_of="2026-06-20T00:30:00Z", instrument="BTC-USDT")
    assert len(res["features"]) == 1
    assert res["features"][0]["feature"]["values"]["obi"] == 0.10


def test_revision_same_event_time_latest_ingest_wins(fs):
    """Two records for the SAME event_time, the second a later-ingested
    revision. A query whose as_of sees both must return the revision."""
    fs.store("BTC-USDT", T1, _feat(0.40), ingest_time=T1)   # original
    fs.store("BTC-USDT", T1, _feat(0.55), ingest_time=T3)   # revised later
    res = fs.query_as_of(as_of=T3, instrument="BTC-USDT")
    assert len(res["features"]) == 1
    assert res["features"][0]["feature"]["values"]["obi"] == 0.55
    # But before the revision arrived (as_of=t2), the original is what's known.
    res = fs.query_as_of(as_of=T2, instrument="BTC-USDT")
    assert res["features"][0]["feature"]["values"]["obi"] == 0.40


# ---------------------------------------------------------------------------
# (3) MULTI-INSTRUMENT ISOLATION.
# ---------------------------------------------------------------------------
def test_multi_instrument_isolation(fs):
    fs.store("BTC-USDT", T1, _feat(0.7), ingest_time=T1)
    fs.store("ETH-USDT", T1, _feat(0.3), ingest_time=T1)
    fs.store("SOL-USDT", T1, _feat(0.1), ingest_time=T1)

    # No instrument filter -> latest for every instrument, sorted.
    res = fs.query_as_of(as_of=T3)
    insts = [f["instrument"] for f in res["features"]]
    assert insts == ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

    # Filtered -> only that instrument.
    res = fs.query_as_of(as_of=T3, instrument="ETH-USDT")
    assert len(res["features"]) == 1
    assert res["features"][0]["instrument"] == "ETH-USDT"
    assert res["features"][0]["feature"]["values"]["obi"] == 0.3


def test_one_instrument_late_other_visible(fs):
    """PIT guard is per-instrument: BTC late-arriving (hidden at t2) while ETH
    arrived on time (visible at t2)."""
    fs.store("BTC-USDT", T1, _feat(0.7), ingest_time=T3)  # late
    fs.store("ETH-USDT", T1, _feat(0.3), ingest_time=T1)  # on time
    res = fs.query_as_of(as_of=T2)
    insts = [f["instrument"] for f in res["features"]]
    assert insts == ["ETH-USDT"]


# ---------------------------------------------------------------------------
# (4) /api/sources lineage: counts + ingest lag.
# ---------------------------------------------------------------------------
def test_sources_counts_and_ingest_lag(fs):
    # BTC: 2 rows. Second has a 60s ingest lag (event t1, ingest t3).
    fs.store("BTC-USDT", T1, _feat(0.1), ingest_time=T1)  # lag 0
    fs.store("BTC-USDT", "2026-06-20T00:00:00Z", _feat(0.2), ingest_time=T3)  # lag 60s
    # ETH: 1 row, no lag.
    fs.store("ETH-USDT", T1, _feat(0.3), ingest_time=T1)

    out = fs.sources()["sources"]
    by_inst = {s["instrument"]: s for s in out}

    assert by_inst["BTC-USDT"]["count"] == 2
    assert by_inst["ETH-USDT"]["count"] == 1

    # Max ingest lag for BTC is 60 seconds (event t1 -> ingest t3).
    assert by_inst["BTC-USDT"]["max_ingest_lag_secs"] == pytest.approx(60.0)
    assert by_inst["ETH-USDT"]["max_ingest_lag_secs"] == pytest.approx(0.0)

    # last_ingest_time reflects the latest physical arrival.
    assert by_inst["BTC-USDT"]["last_ingest_time"].startswith("2026-06-20T00:01:00")
    assert by_inst["BTC-USDT"]["first_event_time"].startswith("2026-06-20T00:00:00")


# ---------------------------------------------------------------------------
# history endpoint + defaults
# ---------------------------------------------------------------------------
def test_history_newest_first(fs):
    fs.store("BTC-USDT", "2026-06-20T00:00:00Z", _feat(0.1), ingest_time="2026-06-20T00:00:00Z")
    fs.store("BTC-USDT", "2026-06-20T00:01:00Z", _feat(0.2), ingest_time="2026-06-20T00:01:00Z")
    fs.store("BTC-USDT", "2026-06-20T00:02:00Z", _feat(0.3), ingest_time="2026-06-20T00:02:00Z")
    out = fs.history("BTC-USDT", limit=2)
    assert out["count"] == 2
    # Newest first.
    assert out["rows"][0]["feature"]["values"]["obi"] == 0.3
    assert out["rows"][1]["feature"]["values"]["obi"] == 0.2


def test_query_default_asof_is_now_returns_latest(fs):
    # No as_of => defaults to now; a long-ago event ingested long ago is known.
    fs.store("BTC-USDT", T1, _feat(0.42), ingest_time=T1)
    res = fs.query_as_of(instrument="BTC-USDT")
    assert len(res["features"]) == 1
    assert res["features"][0]["feature"]["values"]["obi"] == 0.42


def test_empty_store_returns_no_features(fs):
    res = fs.query_as_of(as_of=T3)
    assert res["features"] == []
    assert fs.sources()["sources"] == []


# ---------------------------------------------------------------------------
# extract_record: parsing the obi-bridge payload shape.
# ---------------------------------------------------------------------------
def test_extract_record_obi_bridge_shape():
    payload = {
        "eventTime": "2026-06-20T00:00:00Z",
        "ingestTime": "2026-06-20T00:00:01Z",
        "instrument": "BTC-USDT",
        "values": {"obi": 0.7},
    }
    rec = mimir.extract_record(payload)
    assert rec is not None
    instrument, event_time, feature = rec
    assert instrument == "BTC-USDT"
    assert event_time == "2026-06-20T00:00:00Z"
    assert feature is payload


def test_extract_record_rejects_missing_fields():
    assert mimir.extract_record({"values": {}}) is None  # no instrument
    assert mimir.extract_record({"instrument": "BTC-USDT"}) is None  # no eventTime
    assert mimir.extract_record("not-a-dict") is None


def test_normalize_iso_z_and_offset_compare_equal():
    z = mimir.normalize_iso("2026-06-20T00:00:00Z")
    off = mimir.normalize_iso("2026-06-20T00:00:00+00:00")
    assert z == off
