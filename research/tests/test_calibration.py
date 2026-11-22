"""The gate's calibration study (G1.7): its layout, its simulated families, its agreement
with the gate itself, and a smoke run.

The full study (1,000 ledgers in each of 54 families) runs only by hand with
``python -m norse_research.calibration --report``; its output is committed and hashed in
docs/research/gate-calibration.md, whose results section it writes, which a test here
checks. prereg-v1's full run is kept beside it as history. What runs in CI is a smoke
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
from norse_research.calibration import NO_SKILL, NULL, POOLED, POWER, SENSITIVITY, Family
from norse_research.cscv import GATE_BLOCKS, cscv
from norse_research.sharpe import moments, sharpe_standard_error

FIX = {"defect": "synthetic defect", "fix_commit": "9" * 40, "section": "6"}
DOC = calibration.REPORT_PATH
PREREG_V1_RESULTS = calibration.RESULTS_PATH.with_name("gate-calibration-results-prereg-v1.json")
PREREG_V1_RESULTS_SHA256 = "2eb307f3da3b425a036bf711207e2670c9641381e1a369d85094ad70037c4d9e"

# Section 10 and gate.calibration pin the families to #66's commit c2365ee, unchanged for
# the rerun but for V and the detection constants. This is the SHA-256 of every family's
# spec there (``_families_sha256`` of ``calibration.FAMILIES`` at c2365ee), and below each
# judged or reported family's parameters at that commit, written out:
# (group, within, across, loading, benchmark_sharpe, planted, tails, regime, autocorrelation).
C2365EE_FAMILIES_SHA256 = "1f9c361d5f8a237ef020d97b754175896cd07c08079cf28db3c53e2ac10c16c2"
_N, _S, _P = NO_SKILL, SENSITIVITY, POWER
C2365EE_REGISTERED_FAMILIES = {
    "null-w0-a0": (_N, 0.0, 0.0, (0.0, 0.0), 0.0, (), "normal", False, 0.0),
    "null-w0.5-a0": (_N, 0.5, 0.0, (0.0, 0.0), 0.0, (), "normal", False, 0.0),
    "null-w0.5-a0.5": (_N, 0.5, 0.5, (0.0, 0.0), 0.0, (), "normal", False, 0.0),
    "null-w0.9-a0": (_N, 0.9, 0.0, (0.0, 0.0), 0.0, (), "normal", False, 0.0),
    "null-w0.9-a0.5": (_N, 0.9, 0.5, (0.0, 0.0), 0.0, (), "normal", False, 0.0),
    "null-w0.9-a0.9": (_N, 0.9, 0.9, (0.0, 0.0), 0.0, (), "normal", False, 0.0),
    "null-w0.99-a0.9": (_N, 0.99, 0.9, (0.0, 0.0), 0.0, (), "normal", False, 0.0),
    "null-w0.99-a0.99": (_N, 0.99, 0.99, (0.0, 0.0), 0.0, (), "normal", False, 0.0),
    "null-w0.999-a0.999": (_N, 0.999, 0.999, (0.0, 0.0), 0.0, (), "normal", False, 0.0),
    "exposure-sb0.5": (_N, 0.9, 0.5, (0.7, 0.2), 0.5, (), "normal", False, 0.0),
    "exposure-sb1": (_N, 0.9, 0.5, (0.7, 0.2), 1.0, (), "normal", False, 0.0),
    "exposure-sb1-h2beta0.5": (_N, 0.9, 0.5, (0.7, 0.5), 1.0, (), "normal", False, 0.0),
    "null-power-layout-low": (_N, 0.5, 0.0, (0.5, 0.0), 0.0, (), "normal", False, 0.0),
    "null-power-layout-high": (_N, 0.9, 0.5, (0.5, 0.0), 0.0, (), "normal", False, 0.0),
    "fat-w0-a0": (_N, 0.0, 0.0, (0.0, 0.0), 0.0, (), "t5", True, 0.0),
    "fat-w0.9-a0.9": (_N, 0.9, 0.9, (0.0, 0.0), 0.0, (), "t5", True, 0.0),
    "fat-w0.99-a0.99": (_N, 0.99, 0.99, (0.0, 0.0), 0.0, (), "t5", True, 0.0),
    "ar0.1-w0-a0": (_S, 0.0, 0.0, (0.0, 0.0), 0.0, (), "normal", False, 0.1),
    "ar0.1-w0.9-a0.9": (_S, 0.9, 0.9, (0.0, 0.0), 0.0, (), "normal", False, 0.1),
    "single-H1-s2-low": (_P, 0.5, 0.0, (0.5, 0.0), 0.0, (("H1", "single", 2.0),), "normal",
                         False, 0.0),
    "single-H1-s2-high": (_P, 0.9, 0.5, (0.5, 0.0), 0.0, (("H1", "single", 2.0),), "normal",
                          False, 0.0),
    "single-H2-s2-low": (_P, 0.5, 0.0, (0.5, 0.0), 0.0, (("H2", "single", 2.0),), "normal",
                         False, 0.0),
    "single-H2-s2-high": (_P, 0.9, 0.5, (0.5, 0.0), 0.0, (("H2", "single", 2.0),), "normal",
                          False, 0.0),
}


def _families_sha256(specs) -> str:
    return hashlib.sha256(
        json.dumps(specs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _registered_calibration() -> dict:
    return json.loads(calibration.REGISTRATION_PATH.read_bytes())["gate"]["calibration"]


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


def test_the_families_are_c2365ee_s_under_the_ids_gate_calibration_lists():
    # The rerun is decisive (D24), so nothing the registration fixed may move: the
    # registered ids, each registered family's parameters, every other family, the seed,
    # the number of ledgers and the simulation's constants, all as at c2365ee.
    pinned = _registered_calibration()
    lay = calibration.layout()
    assert lay.no_skill == tuple(pinned["no_skill_families"])
    assert lay.sensitivity == tuple(pinned["sensitivity_families"])
    assert lay.detection == tuple(pinned["detection_families"])
    assert (len(lay.no_skill), len(lay.sensitivity), len(lay.detection)) == (17, 2, 4)
    assert set(C2365EE_REGISTERED_FAMILIES) == {*lay.no_skill, *lay.sensitivity, *lay.detection}
    for family_id, parameters in C2365EE_REGISTERED_FAMILIES.items():
        f = calibration.BY_ID[family_id]
        assert (f.group, f.within, f.across, f.loading, f.benchmark_sharpe, f.planted, f.tails,
                f.regime, f.autocorrelation) == parameters, family_id
    specs = [dataclasses.asdict(f) for f in calibration.FAMILIES]
    assert _families_sha256(specs) == C2365EE_FAMILIES_SHA256
    # The groups agree with the registered ids, and the four detection families are
    # exactly the single-planted families at the detection Sharpe ratio, 2.0 (D22). The
    # verdict reads the ids, so this is a check on the registration, not a selection.
    assert [f.id for f in calibration.FAMILIES if f.group == NO_SKILL] == list(lay.no_skill)
    assert [f.id for f in calibration.FAMILIES if f.group == SENSITIVITY] == list(lay.sensitivity)
    assert calibration.DETECTION_SHARPE == 2.0
    at_detection_sharpe = [f.id for f in calibration.FAMILIES
                           if len(f.planted) == 1 and f.planted[0][1:] == ("single", 2.0)]
    assert len(at_detection_sharpe) == 4 and set(at_detection_sharpe) == set(lay.detection)
    assert (calibration.SEED, calibration.LEDGERS) == (20261121, 1000)
    assert (calibration.SIGMA, calibration.BENCHMARK_SIGMA, calibration.BENCHMARK_CORRELATION,
            calibration.T_DEGREES, calibration.REGIME_PERSISTENCE, calibration.REGIME_SD,
            calibration.DAYS_PER_YEAR, calibration.PLANTED_COLUMN,
            calibration.PBO_ALWAYS) == (0.01, 0.03, 0.9, 5, 0.98, 0.4, 365, 0, 200)
    assert (calibration.FALSE_PASS_LIMIT, calibration.DETECTION_POWER) == (0.05, 0.5)


def test_the_verdict_takes_the_registered_families_by_id(monkeypatch):
    def summary(passes, planted=None):
        rate = {"count": passes, "n": 1000, "rate": passes / 1000}
        block = {"gate_passes": rate}
        if planted is not None:
            block["planted_passes"] = {"count": planted, "n": 1000, "rate": planted / 1000}
        return {"N30": block}

    lay = calibration.layout()
    summaries = {i: summary(50) for i in lay.no_skill}
    summaries.update({i: summary(0, 501) for i in lay.detection})
    # A family outside the registered ids never enters the verdict, whatever it shows: a
    # single-planted family at 2.0 or a no-skill family added to the study later, or a
    # reported serially correlated family above the limit.
    added = (Family("single-H1-s2-test", POWER, 0.9, 0.5, (0.7, 0.0), 1.0,
                    planted=(("H1", "single", 2.0),)),
             Family("null-test", NO_SKILL, 0.9, 0.9))
    monkeypatch.setattr(calibration, "FAMILIES", calibration.FAMILIES + added)
    monkeypatch.setattr(calibration, "BY_ID", {f.id: f for f in calibration.FAMILIES})
    summaries["single-H1-s2-test"] = summary(0, 0)
    summaries["null-test"] = summary(999)
    summaries["ar0.1-w0-a0"] = summary(999)
    v = calibration.verdict(summaries)
    assert v["complete"] and v["passes_calibration"]
    assert (v["no_skill_families"], v["detection_families"]) == (17, 4)
    assert list(v["no_skill_gate_passes"]) == list(lay.no_skill)
    assert list(v["detection_planted_passes"]) == list(lay.detection)
    # 50 of 1,000 is within the limit and 500 of 1,000 is not more than half.
    assert calibration.verdict({**summaries, "fat-w0-a0": summary(51)})["passes_calibration"] is False
    weak = calibration.verdict({**summaries, "single-H2-s2-low": summary(0, 500)})
    assert weak["detection_met"] is False and weak["weakest_detection_family"] == "single-H2-s2-low"
    # Without every registered family there is no verdict.
    partial = calibration.verdict({i: s for i, s in summaries.items() if i != "single-H1-s2-high"})
    assert not partial["complete"] and not partial["passes_calibration"]


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


# A test-only family, not in calibration.FAMILIES, so none of its ledgers is one of the
# study's (its id seeds them): single-H1-s2-high's parameters. Its ledger 14 is one where
# the two forms of V disagree on the decision itself: H1 passes development under the
# registered null V (DSR 0.998) and fails DEV-1 and DEV-2 under prereg-v1's pooled V (0.862).
DISAGREE = Family("test-pooled-and-null-disagree", POWER, 0.9, 0.5, (0.5, 0.0),
                  planted=(("H1", "single", 2.0),))


@pytest.mark.parametrize(("family", "index", "null_holdout", "pooled_holdout"), [
    (calibration.BY_ID["null-w0.9-a0.9"], 0, None, None),  # neither hypothesis passes
    (calibration.BY_ID["single-H1-s1.5-high"], 500, "H1", "H1"),  # H1 passes development
    (DISAGREE, 14, "H1", None),  # a stale pooled V would fail H1
], ids=["neither", "H1-passes", "pooled-and-null-disagree"])
def test_the_study_judges_a_ledger_exactly_as_the_gate_does(
    family, index, null_holdout, pooled_holdout
):
    ledger = calibration.simulate(family, index)
    registered = issued(_book(ledger))
    assert gate.trial_count(registered) == 30
    pbo = {h: cscv(ledger.returns[h], blocks=GATE_BLOCKS).pbo for h in ("H1", "H2")}
    prepared = calibration.prepare(ledger)
    mine = calibration.judge(prepared, 30, pbo=pbo)  # the default is the registered V
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
    assert (chosen.hypothesis if chosen else None) == expected == null_holdout
    pooled = calibration.judge(prepared, 30, POOLED, pbo)
    assert calibration.holdout_candidate(pooled) == pooled_holdout
    if null_holdout != pooled_holdout:
        assert gate.development(registered, "H1").passes and not pooled["H1"].passes
        assert not pooled["H1"].criteria["DEV-1"] and not pooled["H1"].criteria["DEV-2"]


def _decided(judged: gate.Development) -> tuple:
    """What a judgement decides with, PBO aside (NaN when not given)."""
    return (judged.candidate.order, judged.dsr, judged.z, judged.excess_dsr,
            judged.sharpe_without_year, judged.sharpe_without_largest)


def test_the_null_v_reads_no_run_and_the_counterfactual_is_prereg_v1_s_pooled_v():
    family = dataclasses.replace(calibration.BY_ID["shared-H1-s2-high"],
                                 id="test-shared-H1-s2-high")  # no study ledger
    ledger = calibration.simulate(family, 0)
    prepared = calibration.prepare(ledger)
    null = calibration.judge(prepared, 30)
    pooled = calibration.judge(prepared, 30, POOLED)
    assert _decided(calibration.judge(prepared, 30, NULL)["H1"]) == _decided(null["H1"])
    # The registered V is 1/(T - 1) of the window, for DEV-1 and DEV-2 (decision D22).
    for h, t in (("H1", 2802), ("H2", 2185)):
        returns = null[h].candidate.returns
        assert null[h].dsr == gate._deflated(returns, 30, 1 / (t - 1), "null")[0].dsr
        excess = prepared.excess[h][null[h].candidate.order]
        assert null[h].excess_dsr == gate._deflated(excess, 30, 1 / (t - 1), "null")[0].dsr
    # prereg-v1's V pools both hypotheses' runs: a shared edge in H1 alone counts the gap
    # between H1 and H2 as dispersion, which raises H1's bar.
    sharpes = prepared.sharpes("H1") + prepared.sharpes("H2")
    v = statistics.variance(sharpes)
    assert v > 5 * statistics.variance(prepared.sharpes("H1"))
    assert pooled["H1"].dsr == gate._deflated(pooled["H1"].candidate.returns, 30, v, "x")[0].dsr
    assert null["H1"].dsr > pooled["H1"].dsr
    # Under the null V neither hypothesis's bar depends on the other's runs; under the
    # pooled V it does. Here H2's runs are turned upside down.
    flipped = dataclasses.replace(
        ledger, returns={**ledger.returns, "H2": [[-x for x in r] for r in ledger.returns["H2"]]})
    again = calibration.prepare(flipped)
    assert _decided(calibration.judge(again, 30)["H1"]) == _decided(null["H1"])
    assert calibration.judge(again, 30, POOLED)["H1"].dsr != pooled["H1"].dsr
    assert math.isnan(null["H1"].pbo) and not null["H1"].criteria["DEV-3"]  # no PBO given
    with pytest.raises(ValueError, match="unknown variance"):
        calibration.judge(prepared, 30, "own")


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
    null = by_id[null_family.id]["summary"]
    assert set(null) == {"N30", "N14", calibration.COUNTERFACTUAL, "pbo_mean"}
    assert null["N30"]["gate_passes"]["n"] == 2 and "planted_passes" not in null["N30"]
    planted = by_id["single-H2-s2-high"]["summary"]["N30"]
    assert planted["planted_passes"]["n"] == 2 and "planted_column_passes" in planted
    verdict = results["verdict"]
    assert (verdict["no_skill_families"], verdict["detection_families"]) == (1, 1)
    assert list(verdict["no_skill_gate_passes"]) == [null_family.id]
    assert list(verdict["detection_planted_passes"]) == ["single-H2-s2-high"]
    assert not verdict["complete"] and not verdict["passes_calibration"]
    assert results["layout"]["n_trials"] == [30, 14]
    assert results["settings"] == {
        "N30": {"n_trials": 30, "variance": NULL},
        "N14": {"n_trials": 14, "variance": NULL},
        calibration.COUNTERFACTUAL: {"n_trials": 30, "variance": POOLED},
    }
    data = calibration.dumps(results)
    assert calibration.dumps(json.loads(data)) == data  # the committed form is canonical
    # The report's section renders from the file alone, and gives no verdict on a part.
    text = calibration.results_markdown(json.loads(data))
    assert text.startswith("\n**No verdict.**") and hashlib.sha256(data).hexdigest() in text
    assert "`single-H2-s2-high`" in text and "`null-w0.99-a0.99`" in text


def test_the_report_s_results_section_is_replaced_between_its_markers():
    text = f"# Report\n\n{calibration.REPORT_BEGIN}\nold\n{calibration.REPORT_END}\n\nafter\n"
    new = calibration.with_report_section(text, "\nnew\n")
    assert new == f"# Report\n\n{calibration.REPORT_BEGIN}\nnew\n{calibration.REPORT_END}\n\nafter\n"
    assert calibration.report_section(new) == "\nnew\n"
    for broken in ("no markers", text + calibration.REPORT_END,
                   f"{calibration.REPORT_END}\n{calibration.REPORT_BEGIN}"):
        with pytest.raises(ValueError, match="marker"):
            calibration.report_section(broken)


def test_the_committed_full_run_is_the_one_the_report_cites():
    section = calibration.report_section(DOC.read_text(encoding="utf-8"))
    if not calibration.RESULTS_PATH.exists():
        # The commit that fixes the study before its rerun: no results file, and the
        # report's results section says so. The rerun's commit adds both together.
        assert section == calibration.NOT_YET_RUN
        return
    data = calibration.RESULTS_PATH.read_bytes()
    results = json.loads(data)
    assert results["seed"] == calibration.SEED
    assert results["ledgers_per_family"] == calibration.LEDGERS
    assert results["registration_sha256"] == calibration.layout().registration_sha256
    specs = [f["spec"] for f in results["families"]]
    assert specs == [json.loads(json.dumps(dataclasses.asdict(f))) for f in calibration.FAMILIES]
    assert hashlib.sha256(data).hexdigest() in DOC.read_text(encoding="utf-8")
    verdict = calibration.verdict({f["spec"]["id"]: f["summary"] for f in results["families"]})
    assert verdict == results["verdict"] and verdict["complete"]
    assert calibration.dumps(results) == data
    assert section == calibration.results_markdown(results)


def test_the_prereg_v1_run_is_kept_as_history():
    # prereg-v1's full run (pooled V, the 1.5 clause), which failed its detection half,
    # stays committed and cited under its own name; the rerun does not overwrite it.
    data = PREREG_V1_RESULTS.read_bytes()
    assert hashlib.sha256(data).hexdigest() == PREREG_V1_RESULTS_SHA256
    assert PREREG_V1_RESULTS_SHA256 in DOC.read_text(encoding="utf-8")
    results = json.loads(data)
    assert (results["seed"], results["ledgers_per_family"]) == (calibration.SEED, 1000)
    assert _families_sha256([f["spec"] for f in results["families"]]) == C2365EE_FAMILIES_SHA256
    assert results["verdict"]["passes_calibration"] is False
