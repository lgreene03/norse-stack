"""Unit tests for Huginn-AI ML soundness fixes.

Run with: python3 -m pytest services/huginn-ai/tests/
(kafka is stubbed and MODEL_DIR is redirected to a temp dir in conftest.)

Covers:
  - extract_features ordering / NaN-Inf guards / default fallbacks (ml-9, ml-13)
  - strictly-preceding feature matching + provenance (ml-11)
  - FIFO round-trip P&L labeling w/ prorated fees + partial fills (ml-10)
  - contamination-free temporal split (ml-1)
  - fill/feature validation + dedup + rejected counters (ml-7, ml-12)
  - feature-schema hash present in obi-bridge event keys (ml-13)
  - end-to-end synthetic train + predict in [0,1], gated promotion (ml-3, ml-9)
  - persistence round-trip + non-fatal on unwritable path (ml-2)
"""

import os

import numpy as np
import pytest

import huginn_ai


@pytest.fixture(autouse=True)
def reset_metrics():
    """Each test starts with clean counters/gauges."""
    huginn_ai.metrics._counts.clear()
    huginn_ai.metrics._gauges.clear()
    yield


@pytest.fixture
def mgr():
    return huginn_ai.ModelManager()


# Skip the XGBoost-dependent tests if the native lib can't load (e.g. missing
# OpenMP runtime on a bare dev box). CI installs libgomp, so they run there.
try:
    import xgboost
    _XGB_OK = bool(xgboost.__version__)
except Exception:  # pragma: no cover
    _XGB_OK = False

requires_xgb = pytest.mark.skipif(not _XGB_OK, reason="xgboost native lib unavailable")


# ---------------------------------------------------------------------------
# Feature extraction (dataeng-ml-9, ml-13)
# ---------------------------------------------------------------------------

def _values(**over):
    base = {
        "obi": 0.5, "midPrice": 100.0, "spread": 1.0, "momentum": 0.01,
        "volatility": 0.02, "fearGreed": 60.0, "volumeRatio": 1.2,
    }
    base.update(over)
    return base


def test_features_to_array_canonical_order():
    feats = huginn_ai.extract_features(_values(), "2026-06-20T12:00:00Z")
    arr = huginn_ai.features_to_array(feats)
    assert arr.shape == (len(huginn_ai.FEATURE_NAMES),)
    # The i-th array slot must equal the i-th FEATURE_NAMES entry.
    for i, name in enumerate(huginn_ai.FEATURE_NAMES):
        assert arr[i] == pytest.approx(feats[name])


def test_extract_features_nan_inf_defaults_to_finite():
    vals = _values(obi=float("nan"), volatility=float("inf"), momentum=float("-inf"))
    feats = huginn_ai.extract_features(vals, "2026-06-20T12:00:00Z")
    arr = huginn_ai.features_to_array(feats)
    assert np.all(np.isfinite(arr))
    # obi fell back to 0 -> abs_obi 0, obi_sign +1 (0 >= 0).
    assert feats["abs_obi"] == 0.0
    assert feats["obi_sign"] == 1.0
    # fallback counters incremented for each bad key.
    counts = huginn_ai.metrics.snapshot()[0]
    assert counts.get("feature_default_total_obi") == 1
    assert counts.get("feature_default_total_volatility") == 1
    assert counts.get("feature_default_total_momentum") == 1


def test_extract_features_missing_key_uses_default_and_counts():
    vals = {"obi": 0.3}  # everything else missing
    feats = huginn_ai.extract_features(vals, "2026-06-20T12:00:00Z")
    assert np.all(np.isfinite(huginn_ai.features_to_array(feats)))
    counts = huginn_ai.metrics.snapshot()[0]
    assert counts.get("feature_default_total_midPrice") == 1
    assert counts.get("feature_default_total_volumeRatio") == 1


def test_extract_features_bad_timestamp_does_not_raise():
    feats = huginn_ai.extract_features(_values(), "not-a-timestamp")
    assert np.all(np.isfinite(huginn_ai.features_to_array(feats)))


# ---------------------------------------------------------------------------
# Schema hash + obi-bridge key coverage (dataeng-ml-13)
# ---------------------------------------------------------------------------

def test_feature_schema_hash_stable_and_short():
    h1 = huginn_ai._feature_schema_hash()
    h2 = huginn_ai._feature_schema_hash()
    assert h1 == h2
    assert len(h1) == 16


def test_every_feature_source_key_in_sample_obi_event():
    """Each engineered feature's raw source key must exist in a real
    obi-bridge `values` payload (sampled from build_feature_event)."""
    sample_event_values = {
        "obi": 0.42, "bidVolume": 10.0, "askVolume": 5.0, "spread": 1.5,
        "midPrice": 65000.0, "levels": 20, "momentum": 0.001,
        "momentum1m": 0.0, "momentum15m": 0.0, "emaFast": 1.0, "emaSlow": 1.0,
        "volatility": 0.003, "atr": 1.0, "volumeRatio": 1.1, "fearGreed": 55.0,
        "fundingRate": 0.0, "oiChange": 0.0, "mlScore": 0.5, "mlReady": 1.0,
        "newsSentiment": 0.0, "regimeVolAnn": 0.0, "regimeHurst": 0.5,
        "regimeAutocorr": 0.0, "regimeConfidence": 0.0,
    }
    for name, src in huginn_ai.FEATURE_SOURCES.items():
        if src is None:
            continue  # time-derived
        assert src in sample_event_values, (
            f"feature {name} sources missing key {src}"
        )
    # spread_pct additionally needs midPrice.
    assert "midPrice" in sample_event_values


# ---------------------------------------------------------------------------
# Strictly-preceding feature matching (dataeng-ml-11)
# ---------------------------------------------------------------------------

def test_find_preceding_uses_strictly_earlier_and_records_event_id(mgr):
    mgr.store_features("BTC", "2026-06-20T12:00:00Z", _values(obi=0.1), "ev-1")
    mgr.store_features("BTC", "2026-06-20T12:00:05Z", _values(obi=0.2), "ev-2")
    mgr.store_features("BTC", "2026-06-20T12:00:30Z", _values(obi=0.9), "ev-3")

    res = mgr._find_preceding_features("BTC", "2026-06-20T12:00:10Z")
    assert res is not None
    event_time, values, event_id = res
    matched_ts = huginn_ai._parse_ts(event_time)
    fill_ts = huginn_ai._parse_ts("2026-06-20T12:00:10Z")
    # Invariant: matched feature time must be <= fill time (strictly earlier).
    assert matched_ts <= fill_ts
    assert matched_ts < fill_ts
    assert event_id == "ev-2"  # ev-3 is after the fill, must not be chosen


def test_find_preceding_returns_none_when_only_later_features(mgr):
    mgr.store_features("BTC", "2026-06-20T12:00:30Z", _values(), "ev-late")
    assert mgr._find_preceding_features("BTC", "2026-06-20T12:00:10Z") is None


def test_find_preceding_skips_on_fill_parse_failure(mgr):
    mgr.store_features("BTC", "2026-06-20T12:00:00Z", _values(), "ev-1")
    # Unparseable fill timestamp -> skip (never fall back to latest).
    assert mgr._find_preceding_features("BTC", "garbage") is None


# ---------------------------------------------------------------------------
# FIFO round-trip P&L labeling (dataeng-ml-10) — golden test
# ---------------------------------------------------------------------------

def _fill(side, price, qty, fee, ts, instrument="BTC", exec_id=None):
    f = {
        "instrument": instrument, "side": side, "fill_price": price,
        "quantity": qty, "transaction_cost": fee, "timestamp": ts,
    }
    if exec_id is not None:
        f["execution_id"] = exec_id
    return f


def _seed_features(mgr, instrument="BTC"):
    # A preceding feature snapshot so every fill can attach features.
    mgr.store_features(instrument, "2026-06-20T11:59:00Z", _values(), "ev-seed")


def test_fifo_golden_profitable_round_trip(mgr):
    _seed_features(mgr)
    mgr.add_fill(_fill("BUY", 100.0, 1.0, 0.10, "2026-06-20T12:00:00Z", exec_id="a"))
    mgr.add_fill(_fill("SELL", 110.0, 1.0, 0.11, "2026-06-20T12:01:00Z", exec_id="b"))

    labeled = [s for s in mgr.samples if s["label"] is not None]
    assert len(labeled) == 1
    entry = labeled[0]
    # gross = (110-100)*1 = 10; fees = 0.10 + 0.11 = 0.21; pnl = 9.79 > 0 -> 1
    assert entry["realized_pnl"] == pytest.approx(9.79)
    assert entry["label"] == 1


def test_fifo_golden_losing_round_trip(mgr):
    _seed_features(mgr)
    mgr.add_fill(_fill("BUY", 100.0, 1.0, 0.10, "2026-06-20T12:00:00Z", exec_id="a"))
    mgr.add_fill(_fill("SELL", 99.0, 1.0, 0.10, "2026-06-20T12:01:00Z", exec_id="b"))
    labeled = [s for s in mgr.samples if s["label"] is not None]
    assert len(labeled) == 1
    # gross = -1; fees 0.20; pnl = -1.20 -> label 0
    assert labeled[0]["realized_pnl"] == pytest.approx(-1.20)
    assert labeled[0]["label"] == 0


def test_fifo_partial_fill_inventory(mgr):
    """BUY 2 then SELL 1: only 1 unit closes; entry lot stays open, no label
    until fully closed. Fees are prorated by matched quantity."""
    _seed_features(mgr)
    mgr.add_fill(_fill("BUY", 100.0, 2.0, 0.20, "2026-06-20T12:00:00Z", exec_id="a"))
    mgr.add_fill(_fill("SELL", 110.0, 1.0, 0.05, "2026-06-20T12:01:00Z", exec_id="b"))
    # Entry lot for the BUY has 1 unit left -> not yet resolved.
    labeled = [s for s in mgr.samples if s["label"] is not None]
    assert labeled == []
    # Close the remaining unit.
    mgr.add_fill(_fill("SELL", 110.0, 1.0, 0.05, "2026-06-20T12:02:00Z", exec_id="c"))
    labeled = [s for s in mgr.samples if s["label"] is not None]
    assert len(labeled) == 1
    # Two closes of 1 unit each at +10 gross. Entry fee prorated: 0.20 total
    # over 2 units -> 0.10 per close. Close fees 0.05 each.
    # pnl = (10 - 0.10 - 0.05) * 2 = 19.70
    assert labeled[0]["realized_pnl"] == pytest.approx(19.70)
    assert labeled[0]["label"] == 1


def test_fifo_short_round_trip(mgr):
    """SELL entry then BUY to cover: profit when buy price < sell price."""
    _seed_features(mgr)
    mgr.add_fill(_fill("SELL", 100.0, 1.0, 0.10, "2026-06-20T12:00:00Z", exec_id="a"))
    mgr.add_fill(_fill("BUY", 90.0, 1.0, 0.10, "2026-06-20T12:01:00Z", exec_id="b"))
    labeled = [s for s in mgr.samples if s["label"] is not None]
    assert len(labeled) == 1
    # gross = (100-90)*1 = 10; fees 0.20; pnl 9.80 -> 1
    assert labeled[0]["realized_pnl"] == pytest.approx(9.80)
    assert labeled[0]["label"] == 1


# ---------------------------------------------------------------------------
# Validation + dedup + rejected counters (dataeng-ml-7, ml-12)
# ---------------------------------------------------------------------------

def test_validate_fill_rejections():
    assert huginn_ai.validate_fill({"side": "BUY"})[0] is False  # missing instrument
    ok, reason = huginn_ai.validate_fill(
        {"instrument": "BTC", "side": "HOLD", "quantity": 1, "fill_price": 1})
    assert ok is False and reason == "bad_side"
    ok, reason = huginn_ai.validate_fill(
        {"instrument": "BTC", "side": "BUY", "quantity": float("nan"),
         "fill_price": 1.0})
    assert ok is False and reason.startswith("nan_inf")
    ok, reason = huginn_ai.validate_fill(
        {"instrument": "BTC", "side": "BUY", "quantity": -1, "fill_price": 1})
    assert ok is False and reason == "non_positive"
    ok, _ = huginn_ai.validate_fill(
        {"instrument": "BTC", "side": "buy", "quantity": 1, "fill_price": 1})
    assert ok is True


def test_invalid_fill_increments_rejected_counter(mgr):
    _seed_features(mgr)
    mgr.add_fill(_fill("HOLD", 1.0, 1.0, 0.0, "2026-06-20T12:00:00Z", exec_id="x"))
    counts = huginn_ai.metrics.snapshot()[0]
    assert counts.get("fills_rejected_total") == 1
    assert counts.get("fills_rejected_bad_side") == 1


def test_duplicate_fill_deduped_on_execution_id(mgr):
    _seed_features(mgr)
    f = _fill("BUY", 100.0, 1.0, 0.0, "2026-06-20T12:00:00Z", exec_id="dup")
    mgr.add_fill(f)
    mgr.add_fill(dict(f))  # same execution_id
    counts = huginn_ai.metrics.snapshot()[0]
    assert counts.get("fills_processed_total") == 1
    assert counts.get("fills_duplicate_total") == 1


def test_validate_feature_event():
    assert huginn_ai.validate_feature_event("BTC", "t", {"obi": 0.1})[0] is True
    assert huginn_ai.validate_feature_event("", "t", {"obi": 0.1})[0] is False
    assert huginn_ai.validate_feature_event("BTC", "t", {})[0] is False
    ok, reason = huginn_ai.validate_feature_event(
        "BTC", "t", {"obi": float("inf")})
    assert ok is False and reason == "nan_inf_obi"


# ---------------------------------------------------------------------------
# Temporal split contamination (dataeng-ml-1)
# ---------------------------------------------------------------------------

def test_temporal_split_no_leakage(mgr):
    """No train row's label-resolution time may be later than any test row's
    feature time."""
    labeled = []
    # Build 20 samples with monotonically increasing feature + label times.
    for i in range(20):
        labeled.append({
            "features": huginn_ai.extract_features(_values(), ""),
            "label": i % 2,
            "feature_event_time": f"2026-06-20T12:{i:02d}:00Z",
            "fill_time": f"2026-06-20T12:{i:02d}:30Z",
            "label_time": f"2026-06-20T12:{i:02d}:45Z",
        })
    train, test = mgr._temporal_split(labeled, test_frac=0.2)
    assert len(train) > 0 and len(test) > 0

    def lt(s):
        return huginn_ai._parse_ts(s["label_time"])

    def ft(s):
        return huginn_ai._parse_ts(s["feature_event_time"])

    train_max_label = max(lt(s) for s in train)
    test_min_feat = min(ft(s) for s in test)
    assert train_max_label <= test_min_feat


def test_temporal_split_holds_out_recent(mgr):
    labeled = []
    for i in range(10):
        labeled.append({
            "features": huginn_ai.extract_features(_values(), ""),
            "label": 1,
            "feature_event_time": f"2026-06-20T12:{i:02d}:00Z",
            "fill_time": f"2026-06-20T12:{i:02d}:30Z",
            "label_time": f"2026-06-20T12:{i:02d}:45Z",
        })
    # Shuffle input order; split must still hold out the latest by time.
    shuffled = list(reversed(labeled))
    train, test = mgr._temporal_split(shuffled, test_frac=0.2)
    latest = max(labeled, key=lambda s: huginn_ai._parse_ts(s["label_time"]))
    assert latest in test


# ---------------------------------------------------------------------------
# End-to-end synthetic train + predict (dataeng-ml-9) + promotion gate (ml-3)
# ---------------------------------------------------------------------------

def _separable_sample(label, t_index):
    """A linearly-separable synthetic sample keyed on abs_obi."""
    obi = 0.9 if label == 1 else 0.05
    vals = _values(obi=obi, momentum=(0.05 if label == 1 else -0.05))
    feats = huginn_ai.extract_features(vals, "2026-06-20T12:00:00Z")
    return {
        "features": feats,
        "label": label,
        "feature_event_time": f"2026-06-20T12:{t_index:02d}:00Z",
        "fill_time": f"2026-06-20T12:{t_index:02d}:30Z",
        "label_time": f"2026-06-20T12:{t_index:02d}:45Z",
        "realized_pnl": 1.0 if label == 1 else -1.0,
    }


@requires_xgb
def test_end_to_end_train_predicts_in_unit_interval(mgr):
    # 40 separable samples, both classes well represented.
    for i in range(40):
        mgr.samples.append(_separable_sample(i % 2, i))
    mgr._train()
    assert mgr.state == "ready"
    assert mgr.model is not None
    assert mgr.model_version is not None

    # Seed a current feature so predict() has a vector to score.
    mgr.store_features("BTC", "2026-06-20T13:00:00Z", _values(obi=0.9), "ev-now")
    out = mgr.predict("BTC")
    assert out["model_ready"] is True
    assert 0.0 <= out["confidence"] <= 1.0
    assert out["model_version"] == mgr.model_version


@requires_xgb
def test_predict_neutral_when_untrained(mgr):
    mgr.store_features("BTC", "2026-06-20T13:00:00Z", _values(), "ev-now")
    out = mgr.predict("BTC")
    assert out["confidence"] == 0.5
    assert out["model_ready"] is False


@requires_xgb
def test_promotion_rejected_when_single_class_train(mgr):
    # All-positive training set: train must be skipped, model stays None.
    for i in range(40):
        mgr.samples.append(_separable_sample(1, i))
    mgr._train()
    assert mgr.model is None
    assert mgr.state == "untrained"


@requires_xgb
def test_promotion_keeps_incumbent_when_no_improvement(mgr):
    for i in range(40):
        mgr.samples.append(_separable_sample(i % 2, i))
    mgr._train()
    first_version = mgr.model_version
    assert first_version is not None
    # Re-train on the identical separable data: challenger can't beat a
    # perfect incumbent -> promotion rejected, version unchanged.
    mgr.labeled_since_last_train = 0
    mgr._train()
    counts = huginn_ai.metrics.snapshot()[0]
    assert mgr.model_version == first_version
    assert counts.get("model_promotion_rejected_total", 0) >= 1


# ---------------------------------------------------------------------------
# Persistence (dataeng-ml-2) + non-fatal unwritable path
# ---------------------------------------------------------------------------

@requires_xgb
def test_persist_and_reload_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(huginn_ai, "MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(huginn_ai, "MODEL_FILE", str(tmp_path / "model.json"))
    monkeypatch.setattr(huginn_ai, "MODEL_META_FILE", str(tmp_path / "meta.json"))

    m = huginn_ai.ModelManager()
    for i in range(40):
        m.samples.append(_separable_sample(i % 2, i))
    m._train()
    assert os.path.exists(str(tmp_path / "model.json"))
    assert os.path.exists(str(tmp_path / "meta.json"))

    import json
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["feature_schema_hash"] == m.schema_hash
    assert meta["model_version"] == m.model_version
    assert "git_sha" in meta and "eval_metrics" in meta
    assert meta["n_samples"] == m.n_train_samples

    # Fresh manager loads the persisted model on boot.
    m2 = huginn_ai.ModelManager()
    assert m2.load_persisted() is True
    assert m2.model is not None
    assert m2.model_version == m.model_version
    assert m2.state == "ready"


@requires_xgb
def test_persist_non_fatal_on_unwritable_path(monkeypatch):
    bad = "/nonexistent-root-xyz/cannot/write/here"
    monkeypatch.setattr(huginn_ai, "MODEL_DIR", bad)
    monkeypatch.setattr(huginn_ai, "MODEL_FILE", os.path.join(bad, "model.json"))
    monkeypatch.setattr(huginn_ai, "MODEL_META_FILE", os.path.join(bad, "meta.json"))

    m = huginn_ai.ModelManager()
    for i in range(40):
        m.samples.append(_separable_sample(i % 2, i))
    # Must NOT raise — training completes, persistence failure is counted.
    m._train()
    assert m.model is not None  # model still live in-memory
    counts = huginn_ai.metrics.snapshot()[0]
    assert counts.get("model_persist_failures_total", 0) >= 1


def test_load_persisted_absent_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(huginn_ai, "MODEL_FILE", str(tmp_path / "nope.json"))
    monkeypatch.setattr(huginn_ai, "MODEL_META_FILE", str(tmp_path / "nope2.json"))
    m = huginn_ai.ModelManager()
    assert m.load_persisted() is False


# ---------------------------------------------------------------------------
# Metrics rendering (dataeng-ml-4)
# ---------------------------------------------------------------------------

def test_metrics_render_counter_and_gauge():
    huginn_ai.metrics.inc("fills_processed_total", 3)
    huginn_ai.metrics.set_gauge("label_positive_rate", 0.42)
    huginn_ai.metrics.set_gauge("feature_psi", 0.1, labels={"feature": "abs_obi"})
    text = huginn_ai.metrics.render_prometheus()
    assert "huginn_fills_processed_total 3" in text
    assert "huginn_label_positive_rate 0.42" in text
    assert 'huginn_feature_psi{feature="abs_obi"} 0.1' in text
    assert "# TYPE huginn_feature_psi gauge" in text


def test_psi_zero_for_identical_distributions():
    rng = np.random.default_rng(0)
    a = rng.normal(size=500)
    assert huginn_ai.ModelManager._psi(a, a) == pytest.approx(0.0, abs=1e-9)
