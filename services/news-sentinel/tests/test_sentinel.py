"""Unit tests for News Sentinel resilience fixes.

Run with: python3 -m pytest services/news-sentinel/tests/
(feedparser is stubbed in conftest.)

Covers sre-resilience-8 / sre-data-ops-15:
  - Ollama is only marked OK on a genuinely parsed response.
  - Failed/unparsed responses increment ollama_failures and the headline is
    tagged unclassified so it is excluded from aggregate sentiment (no
    falsely-neutral signal).
  - /api/status reflects degraded Ollama state.
And sre-resilience-5: poller liveness for /readyz.
"""

import sentinel


# ---------------------------------------------------------------------------
# Ollama response parsing returns an explicit ok flag
# ---------------------------------------------------------------------------

def test_parse_valid_response_is_ok():
    neutral = sentinel._neutral_sentiment()
    raw = ('{"btc":"bullish","eth":"neutral","sol":"neutral","xrp":"neutral",'
           '"doge":"neutral","btc_confidence":0.8}')
    result, ok = sentinel._parse_ollama_response(raw, neutral)
    assert ok is True
    assert result["btc"] == "bullish"
    assert result["btc_confidence"] == 0.8


def test_parse_no_json_is_not_ok():
    neutral = sentinel._neutral_sentiment()
    result, ok = sentinel._parse_ollama_response("the model said nothing useful",
                                                 neutral)
    assert ok is False
    # Result falls back to neutral defaults but caller must treat it as failed.
    assert result["btc"] == "neutral"


def test_parse_malformed_json_is_not_ok():
    neutral = sentinel._neutral_sentiment()
    result, ok = sentinel._parse_ollama_response("{not valid json", neutral)
    assert ok is False


# ---------------------------------------------------------------------------
# Store: failed classifications are excluded from aggregate sentiment
# ---------------------------------------------------------------------------

def _headline(classified, btc="bullish", conf=0.9, epoch=None):
    import time as _t
    h = {
        "title": "BTC to the moon",
        "title_hash": str(id(object())),
        "processed_at_epoch": epoch if epoch is not None else _t.time(),
        "classified": classified,
    }
    for c in sentinel.COINS:
        h[f"{c}_sentiment"] = btc if c == "btc" else "neutral"
        h[f"{c}_confidence"] = conf
    return h


def test_unclassified_headline_excluded_from_sentiment():
    store = sentinel.HeadlineStore()
    # Only an unclassified (Ollama-failed) headline present.
    store.add(_headline(classified=False))
    out = store.compute_sentiment()
    btc = out["BTC-USDT"]
    # Excluded => no contributing headlines, score stays 0 and is flagged.
    assert btc["headlines_count"] == 0
    assert btc["unclassified_skipped"] == 1
    assert btc["score"] == 0.0


def test_classified_headline_drives_sentiment():
    store = sentinel.HeadlineStore()
    store.add(_headline(classified=True, btc="bullish", conf=0.9))
    out = store.compute_sentiment()
    btc = out["BTC-USDT"]
    assert btc["headlines_count"] == 1
    assert btc["score"] > 0


def test_status_reflects_degraded_ollama():
    store = sentinel.HeadlineStore()
    # No successful calls yet => degraded.
    store.mark_ollama_failure()
    status = store.get_status()
    assert status["ollama_status"] == "degraded"
    assert status["ollama_failures"] == 1
    assert status["ollama_ok_count"] == 0

    store.mark_ollama_ok()
    status = store.get_status()
    assert status["ollama_status"] == "ok"
    assert status["ollama_ok_count"] == 1


# ---------------------------------------------------------------------------
# Poller liveness for /readyz (sre-resilience-5)
# ---------------------------------------------------------------------------

def test_liveness_ok_before_first_poll():
    store = sentinel.HeadlineStore()
    ok, age = store.liveness()
    assert ok is True
    assert age is None


def test_liveness_fresh_beat_ok():
    store = sentinel.HeadlineStore()
    store.beat_poll()
    ok, age = store.liveness()
    assert ok is True
    assert age is not None and age >= 0


def test_liveness_stale_is_degraded(monkeypatch):
    store = sentinel.HeadlineStore()
    store.beat_poll()
    monkeypatch.setattr(sentinel, "HEALTH_MAX_STALENESS_SECS", -1.0)
    ok, _ = store.liveness()
    assert ok is False
