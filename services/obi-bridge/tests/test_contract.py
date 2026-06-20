#!/usr/bin/env python3
"""Contract test for the features.obi.v1 event schema.

Decodes a recorded obi-bridge event built from recorded exchange inputs and
asserts no field loss versus the documented features.obi.v1 schema in
docs/CONTRACTS.md. This guards the wire contract Huginn (model.FeatureEvent)
and the analytics services consume.

Run: python3 -m pytest services/obi-bridge/tests/  (or: python3 tests/test_contract.py)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge  # noqa: E402

# ── Canonical documented schema (mirrors docs/CONTRACTS.md features.obi.v1) ──

REQUIRED_TOP_LEVEL = {
    "eventId",
    "eventTime",
    "ingestTime",
    "codeVersion",
    "inputEventIds",
    "featureName",
    "featureVersion",
    "instrument",
    "windowStart",
    "windowEnd",
    "signalTimeMs",
    "values",
}

REQUIRED_VALUES = {
    "obi", "bidVolume", "askVolume", "spread", "midPrice", "levels",
    "momentum", "momentum1m", "momentum15m", "emaFast", "emaSlow",
    "volatility", "atr", "volumeRatio", "fearGreed", "fundingRate",
    "oiChange", "mlScore", "mlReady", "newsSentiment",
    "regimeVolAnn", "regimeHurst", "regimeAutocorr", "regimeConfidence",
}

# ── Recorded inputs (a single deterministic poll cycle, no network) ─────────

RECORDED_BOOK = {
    "lastUpdateId": 7390127634,
    "bids": [["67490.00", "1.5"], ["67489.00", "2.0"]],
    "asks": [["67510.00", "0.8"], ["67511.00", "1.1"]],
}

# 30 5-minute klines. Binance kline = [openTime, o, h, l, c, vol, closeTime, ...]
RECORDED_KLINES_5M = []
_base_open = 1718884800000  # 2024-06-20T12:00:00Z
for _i in range(30):
    _open = _base_open + _i * 300_000
    _close = _open + 299_999
    _px = 67000.0 + _i * 20.0
    RECORDED_KLINES_5M.append(
        [_open, str(_px), str(_px + 50), str(_px - 40), str(_px + 10),
         str(100.0 + _i), _close, "0", 0, "0", "0", "0"]
    )

RECORDED_TICKER = {"volume": "1234.5"}


def build_recorded_event():
    metrics = bridge.compute_obi(RECORDED_BOOK, 10)
    mom_5m = bridge.compute_momentum(RECORDED_KLINES_5M)
    mom_1m = bridge.compute_momentum(RECORDED_KLINES_5M)
    mom_15m = bridge.compute_momentum(RECORDED_KLINES_5M)
    volatility = bridge.compute_volatility(RECORDED_KLINES_5M)
    volume = bridge.compute_volume_context(RECORDED_KLINES_5M, RECORDED_TICKER)

    detector = bridge.RegimeDetector(window=60)
    for k in RECORDED_KLINES_5M:
        detector.update("BTCUSDT", float(k[4]))
    regime_info = detector.classify("BTCUSDT")

    window_start_ms, window_end_ms = bridge.kline_window(RECORDED_KLINES_5M)
    input_event_ids = [
        f"orderbook:BTCUSDT:{RECORDED_BOOK['lastUpdateId']}",
        f"kline5m:BTCUSDT:{window_end_ms}",
    ]

    return bridge.build_feature_event(
        "BTC-USDT", metrics, mom_5m, mom_1m, mom_15m,
        volatility, volume, 55, 0.0001,
        0.02, 0.73, True, 0.1,
        regime_info,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        input_event_ids=input_event_ids,
    )


def test_no_top_level_field_loss():
    event = build_recorded_event()
    # Round-trip through JSON exactly as it would on the wire.
    event = json.loads(json.dumps(event))
    missing = REQUIRED_TOP_LEVEL - set(event)
    assert not missing, f"missing top-level fields: {sorted(missing)}"


def test_no_values_field_loss():
    event = json.loads(json.dumps(build_recorded_event()))
    missing = REQUIRED_VALUES - set(event["values"])
    assert not missing, f"missing values fields: {sorted(missing)}"
    # All feature values must be numeric (Huginn decodes values as
    # map[string]float64; a non-number would drop the whole event).
    for k, v in event["values"].items():
        assert isinstance(v, (int, float)), f"values.{k} is not numeric: {v!r}"


def test_event_time_is_exchange_window_not_wallclock():
    event = json.loads(json.dumps(build_recorded_event()))
    _, window_end_ms = bridge.kline_window(RECORDED_KLINES_5M)
    expected = bridge.ms_to_iso(window_end_ms)
    assert event["eventTime"] == expected, (
        "eventTime must be stamped from the exchange kline close time "
        f"({expected}), got {event['eventTime']}"
    )
    assert event["signalTimeMs"] == window_end_ms
    assert event["windowStart"] == bridge.ms_to_iso(RECORDED_KLINES_5M[0][0])
    assert event["windowEnd"] == expected
    # ingestTime is wall-clock and distinct in field identity from eventTime.
    assert "ingestTime" in event and event["ingestTime"]


def test_provenance_present():
    event = json.loads(json.dumps(build_recorded_event()))
    assert event["codeVersion"], "codeVersion must be populated"
    ids = event["inputEventIds"]
    assert isinstance(ids, list) and ids, "inputEventIds must be a non-empty list"
    assert any(s.startswith("orderbook:") for s in ids)
    assert any(s.startswith("kline5m:") for s in ids)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as e:
                failures += 1
                print(f"  FAIL {name}: {e}")
    print(f"\n{'PASSED' if failures == 0 else str(failures) + ' FAILURE(S)'}")
    sys.exit(1 if failures else 0)
