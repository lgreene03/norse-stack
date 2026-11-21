"""The gate's calibration study (G1.7): its layout, its simulated families, its agreement
with the gate itself, and a smoke run.

The full study (1,000 ledgers in each of 54 families) runs only by hand with
``python -m norse_research.calibration``; its output is committed and hashed in
docs/research/gate-calibration.md, which a test here checks. What runs in CI is a smoke
version: a few ledgers, enough to prove the code paths and that the study reproduces.
Synthetic returns only.
"""

import dataclasses
import hashlib
import json
import math
import random
import statistics

import pytest
from ledger_fixtures import Book, issued
from norse_research import calibration, gate
from norse_research.calibration import NO_SKILL, POWER, Family
from norse_research.cscv import GATE_BLOCKS, cscv
from norse_research.sharpe import moments, sharpe_standard_error

FIX = {"defect": "synthetic defect", "fix_commit": "9" * 40, "section": "6"}
DOC = calibration.REPO_ROOT / "docs" / "research" / "gate-calibration.md"


def test_the_layout_is_the_registered_one():
    lay = calibration.layout()
    assert {h: len(lay.ids[h]) for h in ("H1", "H2")} == {"H1": 8, "H2": 6}
    assert {h: len(lay.years[h]) for h in ("H1", "H2")} == {"H1": 2802, "H2": 2185}
    # H2's window starts 617 days into H1's, both end on 2025-12-31, then 304 holdout days.
    assert lay.start == {"H1": 0, "H2": 617}
    assert (lay.development, lay.holdout, lay.calendar) == (2802, 304, 3106)
    assert (lay.budget, lay.grid) == (30, 14)
    assert lay.years["H1"][0] == 2018 and lay.years["H2"][0] == 2020
    assert lay.years["H1"][-1] == lay.years["H2"][-1] == 2025


def test_the_families_cover_what_section_10_requires():
    ids = [f.id for f in calibration.FAMILIES]
    assert len(ids) == len(set(ids)) == 54
    for family in calibration.FAMILIES:
        family.loadings()  # every family's correlations can coexist
    no_skill = [f for f in calibration.FAMILIES if f.group == NO_SKILL]
    assert all(not f.planted for f in no_skill)
    # Correlated within each hypothesis and across the two, up to near identity.
    assert any(f.within >= 0.9 and f.across >= 0.9 for f in no_skill)
    assert any(f.within >= 0.999 and f.across >= 0.999 for f in no_skill)
    assert any(f.within >= 0.9 and f.across == 0 for f in no_skill)
    # Power at 1.0, 1.5 and 2.0 for a single planted column and a family sharing one edge,
    # in each hypothesis.
    power = {(f.planted[0], f.within) for f in calibration.FAMILIES
             if f.group == POWER and len(f.planted) == 1}
    for h in ("H1", "H2"):
        for form in ("single", "shared"):
            for sharpe in (1.0, 1.5, 2.0):
                assert ((h, form, sharpe), 0.5) in power and ((h, form, sharpe), 0.9) in power
    with pytest.raises(ValueError, match="cannot coexist"):
        Family("bad", NO_SKILL, 0.3, 0.5).loadings()


def _corr(x, y):
    return statistics.correlation(x, y)


def test_the_factor_model_has_the_correlations_it_is_given():
    family = Family("test-correlations", NO_SKILL, 0.9, 0.5, (0.5, 0.0), 0.0)
    ledger = calibration.simulate(family, 0)
    h1, h2 = ledger.returns["H1"], ledger.returns["H2"]
    within = statistics.fmean(_corr(h1[i], h1[j]) for i in range(8) for j in range(i + 1, 8))
    assert within == pytest.approx(0.9, abs=0.03)
    across = statistics.fmean(_corr(a[617:], b) for a in h1 for b in h2)
    assert across == pytest.approx(0.5, abs=0.06)
    assert statistics.fmean(_corr(a, ledger.benchmarks["H1"]) for a in h1) == pytest.approx(
        0.5, abs=0.06)
    assert statistics.fmean(_corr(b, ledger.benchmarks["H2"]) for b in h2) == pytest.approx(
        0.0, abs=0.06)
    assert _corr(ledger.benchmarks["H1"][617:], ledger.benchmarks["H2"]) == pytest.approx(
        calibration.BENCHMARK_CORRELATION, abs=0.02)
    assert statistics.stdev(h1[0]) == pytest.approx(calibration.SIGMA, rel=0.05)
    assert [len(x) for x in ledger.holdout["H1"] + ledger.holdout["H2"]] == [304] * 14


def test_a_planted_edge_has_the_planted_sharpe_ratio():
    # Near-identical configurations: the planted one differs from the others by its mean.
    family = Family("test-planted", POWER, 0.999, 0.0, planted=(("H2", "single", 2.0),))
    ledger = calibration.simulate(family, 0)
    planted, other = ledger.returns["H2"][0], ledger.returns["H2"][1]
    gap = statistics.fmean(p - o for p, o in zip(planted, other, strict=True))
    assert gap == pytest.approx(calibration.SIGMA * 2.0 / math.sqrt(365), abs=5e-5)
    assert family.alpha("H2", 0) == 2.0 and family.alpha("H2", 1) == 0.0
    assert family.alpha("H1", 0) == 0.0
    shared = Family("test-shared", POWER, 0.9, 0.0, planted=(("H1", "shared", 1.5),))
    assert [shared.alpha("H1", j) for j in range(8)] == [1.5] * 8
    assert [shared.alpha("H2", j) for j in range(6)] == [0.0] * 6


def test_the_shocks_have_unit_variance_and_the_registered_shape():
    rng = random.Random(1)
    t5 = calibration._shocks(rng, 100_000, Family("t", NO_SKILL, 0, 0, tails="t5"))
    assert statistics.pvariance(t5) == pytest.approx(1.0, abs=0.04)
    assert moments(t5).kurtosis > 6  # Student t with 5 degrees of freedom: 9 in theory
    ar = calibration._shocks(rng, 100_000, Family("a", NO_SKILL, 0, 0, autocorrelation=0.1))
    assert statistics.pvariance(ar) == pytest.approx(1.0, abs=0.02)
    assert _corr(ar[:-1], ar[1:]) == pytest.approx(0.1, abs=0.015)
    regime = calibration._regime(rng, 200_000)
    assert statistics.fmean(v * v for v in regime) == pytest.approx(1.0, abs=0.1)


def test_a_ledger_rebuilds_from_its_seed_alone():
    family = calibration.BY_ID["null-w0.9-a0.5"]
    first, again = calibration.simulate(family, 3), calibration.simulate(family, 3)
    assert first == again
    assert calibration.simulate(family, 4).returns != first.returns
    other = calibration.BY_ID["null-w0.9-a0.9"]
    assert calibration.simulate(other, 3).returns != first.returns


# --- the study's gate is the gate ----------------------------------------------------


def _book(ledger) -> Book:
    """The simulated family as an in-memory ledger: every configuration's run, both
    benchmarks, and failed reruns bringing N to the registered 30."""
    book = Book()
    for h in ("H1", "H2"):
        for j, cid in enumerate(book.registration.order[h]):
            book.run(cid, ledger.returns[h][j])
    for h in ("H1", "H2"):
        book.run(f"B-{h}", ledger.benchmarks[h])
    order = book.registration.order["H1"] + book.registration.order["H2"]
    for k in range(30 - 14):
        book.run(order[k % 14], None, rerun=FIX)
    return book


@pytest.mark.parametrize(("family_id", "index"), [
    ("null-w0.9-a0.9", 0),  # neither hypothesis passes
    ("single-H1-s1.5-high", 500),  # H1 passes development
])
def test_the_study_judges_a_ledger_exactly_as_the_gate_does(family_id, index):
    ledger = calibration.simulate(calibration.BY_ID[family_id], index)
    registered = issued(_book(ledger), frozen=True)
    assert gate.trial_count(registered) == 30
    pbo = {h: cscv(ledger.returns[h], blocks=GATE_BLOCKS).pbo for h in ("H1", "H2")}
    mine = calibration.judge(calibration.prepare(ledger), 30, True, pbo)
    for h in ("H1", "H2"):
        theirs = gate.development(registered, h)
        assert theirs.candidate.configuration_id == mine[h].candidate.configuration_id
        assert theirs.candidate.returns == mine[h].candidate.returns
        for field in ("dsr", "z", "excess_dsr", "pbo", "sharpe_without_year",
                      "sharpe_without_largest"):
            assert getattr(theirs, field) == getattr(mine[h], field), (h, field)
        assert theirs.criteria == mine[h].criteria
    chosen = gate.holdout_candidate(registered)
    expected = calibration.holdout_candidate(mine)
    assert (chosen.hypothesis if chosen else None) == expected
    assert (expected is None) == (family_id == "null-w0.9-a0.9")


def test_the_counterfactual_v_uses_each_hypothesis_s_own_runs():
    ledger = calibration.simulate(calibration.BY_ID["shared-H1-s2-high"], 0)
    prepared = calibration.prepare(ledger)
    pooled = calibration.judge(prepared, 30, True)
    own = calibration.judge(prepared, 30, False)
    # A shared edge in H1 alone: pooled V counts the gap between H1 and H2 as dispersion.
    sharpes = prepared.sharpes("H1") + prepared.sharpes("H2")
    assert statistics.variance(sharpes) > 5 * statistics.variance(prepared.sharpes("H1"))
    assert own["H1"].dsr > pooled["H1"].dsr
    assert math.isnan(pooled["H1"].pbo) and not pooled["H1"].criteria["DEV-3"]


# --- HOLD-1 and the intervals, by hand -----------------------------------------------


def test_hold_1_by_hand():
    # A development Sharpe ratio of about 1 a year: the band is below zero, so HOLD-1
    # asks only for a positive holdout Sharpe ratio (section 12).
    dev = [0.0005 + 0.01 * ((-1) ** i) for i in range(2000)]
    m = moments(dev)
    band = m.sharpe - 1.6448536269514722 * math.sqrt(
        (1 - m.skew * m.sharpe + (m.kurtosis - 1) / 4 * m.sharpe**2) / 303)
    assert band < 0
    assert band == pytest.approx(
        m.sharpe - calibration.HOLD_Z * sharpe_standard_error(m.sharpe, 304, m.skew, m.kurtosis))
    up = [0.0001 + 0.01 * ((-1) ** i) for i in range(304)]
    down = [-0.0001 + 0.01 * ((-1) ** i) for i in range(304)]
    assert calibration.hold_1(dev, up) and not calibration.hold_1(dev, down)
    # A development Sharpe ratio of about 5 a year: the band binds above zero.
    strong = [0.0026 + 0.01 * ((-1) ** i) for i in range(2000)]
    assert not calibration.hold_1(strong, up)
    assert calibration.hold_1(strong, strong[:304])


def test_clopper_pearson_known_answers():
    lower, upper = calibration.binomial_interval(0, 1000)
    assert lower == 0 and upper == pytest.approx(1 - 0.025 ** (1 / 1000), rel=1e-9)
    lower, upper = calibration.binomial_interval(1000, 1000)
    assert upper == 1 and lower == pytest.approx(0.025 ** (1 / 1000), rel=1e-9)
    # At the bounds of 50 of 1,000 the tails are exactly 2.5%, by direct summation.
    lower, upper = calibration.binomial_interval(50, 1000)

    def tail_at_least(k, n, p):
        return math.fsum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))

    assert tail_at_least(50, 1000, lower) == pytest.approx(0.025, rel=1e-6)
    assert 1 - tail_at_least(51, 1000, upper) == pytest.approx(0.025, rel=1e-6)
    assert 0.0373 < lower < 0.0374 and 0.0653 < upper < 0.0654


# --- a smoke run, and the committed full run ----------------------------------------


def test_smoke_run_reproduces_and_summarises():
    # SMOKE: two families, two ledgers each, one process, in chunks of one ledger. The
    # full study is the committed artefact checked below.
    null_family = calibration.BY_ID["null-w0.99-a0.99"]
    families = [null_family, calibration.BY_ID["single-H2-s2-high"]]
    results = calibration.run(families, 2, workers=1, chunk=1)
    by_id = {f["spec"]["id"]: f for f in results["families"]}
    direct = [calibration.evaluate(null_family, i) for i in range(2)]
    assert by_id[null_family.id]["records_sha256"] == calibration._digest(direct)
    null = by_id[null_family.id]["summary"]["N30"]
    assert null["gate_passes"]["n"] == 2 and "planted_passes" not in null
    planted = by_id["single-H2-s2-high"]["summary"]["N30"]
    assert planted["planted_passes"]["n"] == 2 and "planted_column_passes" in planted
    assert results["verdict"]["no_skill_families"] == 1
    assert results["layout"]["n_trials"] == [30, 14]
    data = calibration.dumps(results)
    assert calibration.dumps(json.loads(data)) == data  # the committed form is canonical


def test_the_committed_full_run_is_the_one_the_report_cites():
    data = calibration.RESULTS_PATH.read_bytes()
    results = json.loads(data)
    assert results["seed"] == calibration.SEED
    assert results["ledgers_per_family"] == calibration.LEDGERS
    assert results["registration_sha256"] == calibration.layout().registration_sha256
    specs = [f["spec"] for f in results["families"]]
    assert specs == [json.loads(json.dumps(dataclasses.asdict(f))) for f in calibration.FAMILIES]
    assert hashlib.sha256(data).hexdigest() in DOC.read_text()
    verdict = calibration.verdict({f["spec"]["id"]: f["summary"] for f in results["families"]})
    assert verdict == results["verdict"]
