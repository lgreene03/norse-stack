"""Red-team: try to smuggle look-ahead past Mimir's point-in-time guard.

The other suite (``test_mimir_pit.py``) unit-tests each guard in isolation. This
file is adversarial: it plants a *battery* of look-ahead leaks in one store and
asserts the as-of query blocks every one at once, and, just as importantly, that
it does NOT over-block data that was legitimately known. A leak here fails the
build, which is the point: the anti-look-ahead property is Norse's standout
claim, so it should be actively attacked in CI, not merely asserted in prose.

Run with: python3 -m pytest services/mimir/tests/  (kafka is stubbed in conftest)
"""

import pytest

import mimir


@pytest.fixture
def fs(tmp_path):
    """Fresh file-backed FeatureStore per test."""
    return mimir.FeatureStore(db_path=str(tmp_path / "mimir-redteam.db"))


def _feat(obi, tag=None):
    v = {"obi": obi}
    if tag is not None:
        v["tag"] = tag
    return {"featureName": "obi", "values": v}


def _obis(res):
    """The obi values visible in an as-of result, order-independent."""
    return sorted(f["feature"]["values"]["obi"] for f in res["features"])


# The "present": the instant we run every as-of query at.
NOW = "2026-06-20T12:00:00Z"
PAST = "2026-06-20T11:00:00Z"      # a known, past event
FUTURE = "2026-06-20T13:00:00Z"    # strictly after NOW
LATER = "2026-06-20T14:00:00Z"


# ---------------------------------------------------------------------------
# (1) The leak battery: many flavours of look-ahead, none may be visible at NOW.
# ---------------------------------------------------------------------------
def test_redteam_no_leak_survives_as_of_guard(fs):
    """query_as_of returns the single latest-KNOWN row per instrument. Plant a
    battery of leaks and assert the as-of answer is the legitimate boundary row,
    never any leak, and that the guard does not over-block genuinely-known data."""
    inst = "BTC-USDT"

    # --- legitimately known ---
    fs.store(inst, PAST, _feat(0.10), ingest_time=PAST)
    fs.store(inst, NOW, _feat(0.11), ingest_time=NOW)          # boundary: event==ingest==as_of

    # --- leaks: none may ever be the as-of-NOW answer ---
    fs.store(inst, FUTURE, _feat(0.90), ingest_time=FUTURE)               # a) future event
    for i in range(1, 6):                                                  # b) a future window
        et = "2026-06-20T13:%02d:00Z" % i
        fs.store(inst, et, _feat(0.80 + i / 100.0), ingest_time=et)
    fs.store(inst, PAST, _feat(0.91), ingest_time=LATER)                  # c) backfilled past row, known later
    fs.store(inst, NOW, _feat(0.92), ingest_time=FUTURE)                  # d) revision of NOW, not yet known

    # At NOW, the only correct answer is the row known exactly at NOW. Any leak
    # would change this value, so the exact-match assertion is the whole guard.
    res = fs.query_as_of(as_of=NOW, instrument=inst)
    assert len(res["features"]) == 1, "expected exactly one latest-known row"
    obi = res["features"][0]["feature"]["values"]["obi"]
    assert obi == 0.11, (
        "as-of NOW must surface the row known at NOW, got %r, a future/backfill/"
        "revision value leaked past the guard" % obi)

    # And the past row must NOT be over-blocked: as-of between PAST and NOW must
    # still surface it (a guard that hides known data is as broken as one that leaks).
    mid = "2026-06-20T11:30:00Z"
    res_mid = fs.query_as_of(as_of=mid, instrument=inst)
    assert len(res_mid["features"]) == 1
    assert res_mid["features"][0]["feature"]["values"]["obi"] == 0.10, "the past row was over-blocked"


# ---------------------------------------------------------------------------
# (2) A revision is invisible until its OWN ingest_time, then supersedes.
# ---------------------------------------------------------------------------
def test_redteam_revision_hidden_until_its_ingest_time(fs):
    inst = "ETH-USDT"
    # Original value known at PAST; a corrected value for the SAME event_time is
    # backfilled later (ingest_time=LATER). Between PAST and LATER, only the
    # original may be seen; a backtest as-of that window must not peek the fix.
    fs.store(inst, PAST, _feat(0.20, "original"), ingest_time=PAST)
    fs.store(inst, PAST, _feat(0.25, "revised"), ingest_time=LATER)

    at_now = fs.query_as_of(as_of=NOW, instrument=inst)
    seen = [f["feature"]["values"] for f in at_now["features"]]
    assert all(v.get("tag") != "revised" for v in seen), "revision leaked before its ingest_time"
    assert any(v["obi"] == 0.20 for v in seen), "the originally-known value should still be visible"

    at_later = fs.query_as_of(as_of=LATER, instrument=inst)
    vals = [f["feature"]["values"]["obi"] for f in at_later["features"]]
    assert 0.25 in vals, "the revision must be visible once its ingest_time has passed"


# ---------------------------------------------------------------------------
# (3) Building a training set as-of T sees ONLY the past-and-known rows.
# ---------------------------------------------------------------------------
def test_redteam_training_window_is_causally_clean(fs):
    inst = "SOL-USDT"
    # A stream where past rows are interleaved with future rows and one late
    # backfill. A training set assembled as-of NOW must equal exactly the rows
    # that were both about the past AND already ingested by NOW.
    known = [
        ("2026-06-20T09:00:00Z", 0.01),
        ("2026-06-20T10:00:00Z", 0.02),
        ("2026-06-20T11:00:00Z", 0.03),
    ]
    for et, obi in known:
        fs.store(inst, et, _feat(obi), ingest_time=et)
    # future rows + a late backfill of a past row — neither may enter training.
    fs.store(inst, FUTURE, _feat(0.99), ingest_time=FUTURE)
    fs.store(inst, "2026-06-20T10:30:00Z", _feat(0.98), ingest_time=LATER)  # late backfill

    # query_as_of returns the latest-known per instrument; walk the known grid
    # and assert each as-of view is the causal prefix, never the future.
    for et, obi in known:
        res = fs.query_as_of(as_of=et, instrument=inst)
        vals = _obis(res)
        assert vals == [obi], "as-of %s should surface only the row known then, got %s" % (et, vals)
        assert 0.99 not in vals and 0.98 not in vals, "future/backfill leaked into as-of %s" % et
