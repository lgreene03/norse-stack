"""Unit tests for Bragi resilience fixes.

Run with: python3 -m pytest services/bragi/tests/  (kafka is stubbed in conftest)

Covers:
  - sre-resilience-6: earliest + dedup on event id so a restart replay does not
    double-count decisions/stats.
  - sre-data-ops-6 surface: decode_failures counter exposed via stats.
  - sre-resilience-5: consumer-thread liveness for /healthz.
"""

import pytest

import bragi


@pytest.fixture
def dlog():
    return bragi.DecisionLog()


def _feature_event(event_id, obi=0.99, instrument="BTC-USDT"):
    """Build a feature event that fires a (would-be) trade signal."""
    return {
        "eventId": event_id,
        "instrument": instrument,
        "eventTime": "2026-06-20T00:00:00Z",
        "values": {
            "obi": obi,
            "momentum": 0.0,
            "volatility": 0.001,
            "fearGreed": 50,
            "volumeRatio": 1.0,
            "midPrice": 67000.0,
        },
    }


# ---------------------------------------------------------------------------
# Dedup on feature eventId (sre-resilience-6)
# ---------------------------------------------------------------------------

def test_feature_event_deduped_on_replay(dlog):
    ev = _feature_event("evt-1")
    dlog.add_feature_event(dict(ev))
    dlog.add_feature_event(dict(ev))  # replay (restart from earliest)
    # Only one decision recorded; the duplicate is skipped and counted.
    assert len(dlog.decisions) == 1
    assert dlog.duplicate_skipped == 1


def test_distinct_feature_events_both_processed(dlog):
    dlog.add_feature_event(_feature_event("evt-1"))
    dlog.add_feature_event(_feature_event("evt-2"))
    assert len(dlog.decisions) == 2
    assert dlog.duplicate_skipped == 0


def test_feature_event_without_id_not_deduped(dlog):
    # Events lacking an eventId are always processed (cannot dedup safely).
    ev = _feature_event(None)
    dlog.add_feature_event(dict(ev))
    dlog.add_feature_event(dict(ev))
    assert len(dlog.decisions) == 2
    assert dlog.duplicate_skipped == 0


def test_fill_deduped_on_execution_id(dlog):
    fill = {"execution_id": "x-1", "order_id": "o-1", "instrument": "BTC-USDT"}
    dlog.add_fill(dict(fill))
    dlog.add_fill(dict(fill))  # replay
    assert dlog.duplicate_skipped == 1
    assert dlog.fills.get("o-1") is not None


def test_fill_dedup_falls_back_to_order_id(dlog):
    fill = {"order_id": "o-2", "instrument": "ETH-USDT"}
    dlog.add_fill(dict(fill))
    dlog.add_fill(dict(fill))
    assert dlog.duplicate_skipped == 1


# ---------------------------------------------------------------------------
# Stats surface decode_failures / duplicate_skipped
# ---------------------------------------------------------------------------

def test_stats_expose_decode_and_dup_counters(dlog):
    dlog.decode_failures = 3
    dlog.add_feature_event(_feature_event("evt-1"))
    dlog.add_feature_event(_feature_event("evt-1"))  # dup
    stats = dlog.get_stats()
    assert stats["decode_failures"] == 3
    assert stats["duplicate_skipped"] == 1


def test_duplicate_not_counted_in_total_events(dlog):
    # The dedup counter must NOT inflate total_events / filter math.
    dlog.add_feature_event(_feature_event("evt-1"))
    dlog.add_feature_event(_feature_event("evt-1"))  # dup
    stats = dlog.get_stats()
    # Exactly one real event was tallied into the breakdown.
    assert stats["total_events"] == 1


# ---------------------------------------------------------------------------
# Consumer-thread liveness for /healthz (sre-resilience-5)
# ---------------------------------------------------------------------------

def test_liveness_ok_before_started():
    lv = bragi.Liveness()
    ok, age = lv.status()
    assert ok is True
    assert age is None


def test_liveness_fresh_beat_ok():
    lv = bragi.Liveness()
    lv.mark_started()
    lv.beat()
    ok, age = lv.status()
    assert ok is True
    assert age is not None and age >= 0


def test_liveness_stale_is_degraded(monkeypatch):
    lv = bragi.Liveness()
    lv.mark_started()
    monkeypatch.setattr(bragi, "HEALTH_MAX_STALENESS_SECS", -1.0)
    ok, _ = lv.status()
    assert ok is False
