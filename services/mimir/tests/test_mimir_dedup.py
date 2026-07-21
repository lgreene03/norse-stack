"""Idempotent ingestion: the same feature event stored twice is kept once.

Mimir consumes from Kafka and is warm-re-read by Heimdall on boot, so the same
record can legitimately arrive more than once. Ingestion is keyed on the
payload's eventId (a UNIQUE index + INSERT OR IGNORE) so a replay does not bloat
the history or the point-in-time answer. Events without an eventId cannot be
identified, so they are not deduped (NULL keys are all distinct in SQLite).

Run with: python3 -m pytest services/mimir/tests/  (kafka is stubbed in conftest)
"""

import sqlite3

import pytest

import mimir


@pytest.fixture
def fs(tmp_path):
    return mimir.FeatureStore(db_path=str(tmp_path / "mimir-dedup.db"))


def _feat(obi, event_id=None):
    f = {"featureName": "obi", "values": {"obi": obi}}
    if event_id is not None:
        f["eventId"] = event_id
    return f


def _count(fs, instrument):
    with fs.lock:
        return fs.conn.execute(
            "SELECT COUNT(*) FROM features WHERE instrument = ?", (instrument,)
        ).fetchone()[0]


def test_same_event_id_ingested_once(fs):
    for _ in range(3):  # deliver the SAME event three times (replay)
        fs.store("BTC-USDT", "2026-06-20T00:00:00Z", _feat(0.5, "evt-1"),
                 ingest_time="2026-06-20T00:00:00Z")
    assert _count(fs, "BTC-USDT") == 1, "a replayed eventId must be stored once"


def test_distinct_event_ids_both_stored(fs):
    fs.store("BTC-USDT", "2026-06-20T00:00:00Z", _feat(0.5, "evt-1"),
             ingest_time="2026-06-20T00:00:00Z")
    fs.store("BTC-USDT", "2026-06-20T00:01:00Z", _feat(0.6, "evt-2"),
             ingest_time="2026-06-20T00:01:00Z")
    assert _count(fs, "BTC-USDT") == 2


def test_events_without_id_are_not_deduped(fs):
    # No eventId -> NULL key -> both insert (cannot dedup what carries no identity).
    for _ in range(2):
        fs.store("ETH-USDT", "2026-06-20T00:00:00Z", _feat(0.5),
                 ingest_time="2026-06-20T00:00:00Z")
    assert _count(fs, "ETH-USDT") == 2


def test_replay_does_not_change_as_of_result(fs):
    # Idempotency must not alter query results: storing twice == storing once.
    for _ in range(2):
        fs.store("BTC-USDT", "2026-06-20T00:00:00Z", _feat(0.7, "e1"),
                 ingest_time="2026-06-20T00:00:00Z")
    res = fs.query_as_of(as_of="2026-06-20T01:00:00Z", instrument="BTC-USDT")
    assert len(res["features"]) == 1
    assert res["features"][0]["feature"]["values"]["obi"] == 0.7


def test_migrates_legacy_table_without_event_id(tmp_path):
    """A pre-migration DB (features table with no event_id column) is migrated
    on open, and dedup works thereafter without disturbing the legacy row."""
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE features (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "instrument TEXT NOT NULL, event_time TEXT NOT NULL, "
        "ingest_time TEXT NOT NULL, feature_json TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO features (instrument, event_time, ingest_time, feature_json) "
        "VALUES ('BTC-USDT','2026-06-20T00:00:00Z','2026-06-20T00:00:00Z','{}')"
    )
    conn.commit()
    conn.close()

    fs = mimir.FeatureStore(db_path=db)  # opening must ALTER in event_id + index
    for _ in range(2):  # a replayed new event
        fs.store("BTC-USDT", "2026-06-20T00:01:00Z", _feat(0.5, "evt-new"),
                 ingest_time="2026-06-20T00:01:00Z")
    assert _count(fs, "BTC-USDT") == 2, "1 legacy row + 1 new event (replay deduped)"
