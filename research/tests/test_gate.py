"""The gate's statistics take the ledger, never N (G1.5 with G1.3).

Section 10 of docs/PREREGISTRATION.md and its section 15, items 1 to 4; threat model
T19. Synthetic returns only.
"""

import dataclasses
import hashlib
import inspect
import math
import statistics

import pytest
from ledger_fixtures import Book, issued, lcg_returns
from norse_research import gate, harness
from norse_research.cscv import cscv
from norse_research.gate import GateRefused, RegisteredLedger
from norse_research.ledger import LedgerSnapshot, VerificationError, make_entry
from norse_research.sharpe import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_track_record_length,
    moments,
    null_sharpe_variance,
)

FIX = {"defect": "synthetic defect", "fix_commit": "9" * 40, "section": "6"}


def days(book, h):
    return book.registration.windows[h].days


def full_family(book: Book, drifts=None) -> None:
    """Every registered configuration of both hypotheses with a selectable run."""
    for h in ("H1", "H2"):
        for i, cid in enumerate(book.registration.order[h]):
            drift = (drifts or {}).get(cid, 0.0002 * i)
            book.run(cid, lcg_returns(10 + i + (100 if h == "H2" else 0), days(book, h), drift))


def registered(book: Book) -> RegisteredLedger:
    return issued(book)


def test_statistics_take_n_only_from_the_ledger():
    forbidden = {"n", "N", "n_trials", "trials", "trial_count", "num_trials", "n_obs_trials"}
    public = [
        f for name, f in vars(gate).items()
        if inspect.isfunction(f) and f.__module__ == gate.__name__ and not name.startswith("_")
    ]
    assert {f.__name__ for f in public} >= {
        "trial_count", "selectable_runs", "deflated_sharpe_of", "candidates",
        "probability_of_backtest_overfitting", "trial_sharpe_variance", "gate_ledger",
    }
    for function in public:
        assert not set(inspect.signature(function).parameters) & forbidden, function.__name__
    # N is the ledger's registered count: reruns, failed and unfinished trials
    # included, benchmarks and controls not.
    book = Book()
    full_family(book)
    book.run("H1-01", lcg_returns(10, days(book, "H1")), rerun=FIX)
    book.run("H2-02", None, rerun=FIX)
    book.open("H2-03", rerun=FIX)
    book.run("B-H1", lcg_returns(7, days(book, "H1")))
    book.open("C-H2", seed=5)
    ledger = registered(book)
    assert gate.trial_count(ledger) == 14 + 3
    run = gate.selectable_runs(ledger)["H1"][2]
    m = moments(run.returns)
    null = 1 / (m.n - 1)  # V, the null variance of the run's window (decision D22)
    expected = deflated_sharpe_ratio(m.sharpe, m.n, m.skew, m.kurtosis, 17, null)
    result = gate.deflated_sharpe_of(ledger, run)
    assert result.n_trials == 17
    assert result.dsr == pytest.approx(expected, rel=1e-12)
    # The reported minimum track record length against SR_0 uses the same N.
    sr0 = expected_max_sharpe(17, null)
    assert gate.min_track_record_of(ledger, run) == pytest.approx((
        min_track_record_length(m.sharpe, m.skew, m.kurtosis, 0.0),
        min_track_record_length(m.sharpe, m.skew, m.kurtosis, sr0),
    ))


def pooled_v(ledger: RegisteredLedger) -> float:
    """prereg-v1's V, which decision D22 replaced: the sample variance of every
    selectable run's daily Sharpe ratio, both hypotheses together. Only to show what
    the null variance no longer depends on."""
    runs = gate.selectable_runs(ledger).values()
    return statistics.variance([moments(r.returns).sharpe for found in runs for r in found])


def run_of(ledger: RegisteredLedger, cid: str):
    return next(r for found in gate.selectable_runs(ledger).values() for r in found
                if r.configuration_id == cid)


def test_v_does_not_depend_on_any_run_s_sharpe_ratio():
    # Decision D22: V is the null variance 1/(T - 1) of the window, for DEV-1 and DEV-2,
    # whatever the runs show. Fourteen near-identical configurations (prereg-v1's V far
    # below the null), fourteen widely dispersed ones (far above it) and a single run
    # with one failed rerun beside it all give the same V.
    tight, wide, single = Book(), Book(), Book()
    for h in ("H1", "H2"):
        for i, cid in enumerate(tight.registration.order[h]):
            raw = lcg_returns(5 if h == "H1" else 6, days(tight, h))
            mean, sd = statistics.fmean(raw), statistics.stdev(raw)
            tight.run(cid, [(x - mean) / sd * 0.01 + 0.0005 + 1e-7 * i for x in raw])
            wide.run(cid, lcg_returns(60 + i, days(wide, h), 0.004 * (i % 3 - 1)))
        for book in (tight, wide):
            book.run(f"B-{h}", lcg_returns(70, days(book, h)))
    single.run("H1-01", lcg_returns(3, days(single, "H1"), 0.001))
    single.run("H1-02", None)
    single.run("B-H1", lcg_returns(70, days(single, "H1")))
    assert pooled_v(issued(tight)) < null_sharpe_variance(days(tight, "H1")) / 1000
    assert pooled_v(issued(wide)) > 100 * null_sharpe_variance(days(wide, "H2"))
    for book in (tight, wide, single):
        ledger = issued(book)
        for h, runs in gate.selectable_runs(ledger).items():
            null = 1 / (days(book, h) - 1)
            assert gate.trial_sharpe_variance(ledger, days(book, h)) == (null, "null")
            for run in runs:
                assert gate.deflated_sharpe_of(ledger, run).trial_sharpe_variance == null
                excess = gate.excess_deflated_sharpe_of(ledger, run)
                assert excess.trial_sharpe_variance == null
                assert excess.variance_source == "null"
    assert len(gate.selectable_runs(issued(single))["H1"]) == 1


def test_a_planted_strong_run_does_not_raise_its_own_sr0():
    # G1.7: under prereg-v1's pooled V a planted edge inflated V, so its SR0 was 0.554
    # times the edge itself. Under the null variance the planted run faces the bar it
    # would face with no edge at all, for DEV-1 and DEV-2.
    def family(drift: float) -> RegisteredLedger:
        book = Book()
        for h in ("H1", "H2"):
            for i, cid in enumerate(book.registration.order[h]):
                planted = drift if cid == "H1-03" else 0.0
                book.run(cid, lcg_returns(80 + i + (20 if h == "H2" else 0), days(book, h),
                                          planted))
            book.run(f"B-{h}", lcg_returns(91 if h == "H1" else 92, days(book, h)))
        return issued(book)

    plain, planted = family(0.0), family(0.002)
    assert pooled_v(planted) > 5 * pooled_v(plain)  # what prereg-v1's bar would do
    base = gate.deflated_sharpe_of(plain, run_of(plain, "H1-03"))
    strong = gate.deflated_sharpe_of(planted, run_of(planted, "H1-03"))
    assert strong.sharpe > base.sharpe + 0.2
    assert strong.n_trials == base.n_trials == 14
    assert strong.expected_max_sharpe == base.expected_max_sharpe > 0
    assert strong.trial_sharpe_variance == base.trial_sharpe_variance
    assert (gate.excess_deflated_sharpe_of(planted, run_of(planted, "H1-03")).expected_max_sharpe
            == gate.excess_deflated_sharpe_of(plain, run_of(plain, "H1-03")).expected_max_sharpe)
    assert gate.candidates(planted)["H1"].configuration_id == "H1-03"


def test_one_hypothesis_s_sr0_does_not_change_when_the_other_has_no_selectable_runs():
    # Security review L7: under prereg-v1, a hypothesis with no selectable runs (run
    # rule 4: its runs come from different code) left V to the other's runs alone, which
    # lowered the other's bar. Under the null variance it cannot: with N unchanged, H1's
    # SR0, DEV-1 and DEV-2 are the same whether H2's runs count or not.
    def family(h2_disqualified: bool) -> RegisteredLedger:
        book = Book()
        for i, cid in enumerate(book.registration.order["H1"]):
            book.run(cid, lcg_returns(120 + i, days(book, "H1"), 0.0001 * i))
        for i, cid in enumerate(book.registration.order["H2"]):
            trees = None
            if h2_disqualified and i == 0:
                trees = {"harness": "1" * 64, "H1": "1" * 64, "H2": "2" * 64}
            book.run(cid, lcg_returns(140 + i, days(book, "H2"), 0.003), trees=trees)
        for h in ("H1", "H2"):
            book.run(f"B-{h}", lcg_returns(161 if h == "H1" else 162, days(book, h)))
        return issued(book)

    counted, disqualified = family(False), family(True)
    assert len(gate.selectable_runs(counted)["H2"]) == 6
    assert gate.selectable_runs(disqualified)["H2"] == ()
    assert gate.trial_count(counted) == gate.trial_count(disqualified) == 14
    assert pooled_v(disqualified) < pooled_v(counted) / 5  # what prereg-v1's bar would do
    for cid in counted.registration.order["H1"]:
        a, b = run_of(counted, cid), run_of(disqualified, cid)
        assert a.returns == b.returns
        one, other = gate.deflated_sharpe_of(counted, a), gate.deflated_sharpe_of(disqualified, b)
        assert one.expected_max_sharpe == other.expected_max_sharpe and one.dsr == other.dsr
        assert (gate.excess_deflated_sharpe_of(counted, a).dsr
                == gate.excess_deflated_sharpe_of(disqualified, b).dsr)
    one, other = gate.development(counted, "H1"), gate.development(disqualified, "H1")
    assert one.candidate.configuration_id == other.candidate.configuration_id
    assert (one.dsr, one.z, one.excess_dsr) == (other.dsr, other.z, other.excess_dsr)


def test_a_run_with_no_variance_has_a_sharpe_and_a_dsr_of_zero():
    book = Book()
    book.run("H1-01", [0.0] * days(book, "H1"))  # a configuration that never trades
    book.run("H1-02", lcg_returns(4, days(book, "H1"), 0.001))
    ledger = registered(book)
    flat, other = gate.selectable_runs(ledger)["H1"]
    assert gate.daily_sharpe(flat.returns) == 0.0
    result = gate.deflated_sharpe_of(ledger, flat)
    assert result.dsr == 0.0 and result.sharpe == 0.0
    # The other run's bar is the null variance's, as with any neighbour (decision D22).
    assert gate.deflated_sharpe_of(ledger, other).trial_sharpe_variance == 1 / (len(other.returns) - 1)


def test_pbo_columns_are_the_selectable_runs_in_registered_order():
    book = Book()
    full_family(book)
    book.run("H2-01", lcg_returns(999, days(book, "H2")), rerun=FIX)  # replaces H2-01's run
    book.run("H2-02", None, rerun=FIX)  # failed: no column
    book.open("H2-04", rerun=FIX)  # never finished: no column
    ledger = registered(book)
    runs = gate.selectable_runs(ledger)["H2"]
    assert [r.configuration_id for r in runs] == book.registration.order["H2"]
    assert runs[0].returns == tuple(lcg_returns(999, days(book, "H2")))
    result = gate.probability_of_backtest_overfitting(ledger, "H2")
    assert result.n_trials == 6 and result.n_obs == days(book, "H2") and result.splits == 12870
    assert result.pbo == cscv([list(r.returns) for r in runs], blocks=16).pbo


def test_a_run_not_covering_its_window_is_not_selectable():
    book = Book()
    window = book.registration.windows["H1"]
    opened = book.open("H1-01")
    book.result(opened, lcg_returns(1, 100), days=window.day_list()[:100])
    assert gate.selectable_runs(registered(book))["H1"] == ()


def test_candidate_is_the_highest_dsr_with_ties_to_the_earlier_configuration():
    book = Book()
    full_family(book, {"H1-05": 0.003, "H2-04": 0.002})
    found = gate.candidates(registered(book))
    assert found["H1"].configuration_id == "H1-05"
    assert found["H2"].configuration_id == "H2-04"
    tied = Book()
    same = lcg_returns(77, days(tied, "H1"), 0.001)
    for cid in tied.registration.order["H1"]:
        tied.run(cid, same)
    assert gate.candidates(registered(tied))["H1"].configuration_id == "H1-01"


def test_no_candidate_until_every_configuration_has_a_selectable_run():
    book = Book()
    for i, cid in enumerate(book.registration.order["H1"][:-1]):
        book.run(cid, lcg_returns(i, days(book, "H1"), 0.003))
    book.run("H1-08", None)
    assert gate.candidates(registered(book)) == {"H1": None, "H2": None}


def test_selectable_runs_from_different_trees_leave_the_hypothesis_without_any():
    book = Book()
    full_family(book)
    trees = {"harness": "1" * 64, "H1": "2" * 64, "H2": "1" * 64}  # H1's strategy changed
    book.run("H1-03", lcg_returns(3, days(book, "H1")), rerun=FIX, trees=trees)
    runs = gate.selectable_runs(registered(book))
    # H1's selectable runs now come from two trees, so H1 has none; H2's all come from
    # the old tree and stand.
    assert runs["H1"] == () and len(runs["H2"]) == 6


def test_the_bootstrap_interval_passes_every_registered_argument(monkeypatch):
    seen = {}

    def spy(returns, **kwargs):
        seen.update(kwargs)
        return "interval"

    monkeypatch.setattr(gate, "block_bootstrap", spy)
    book = Book()
    book.run("H1-01", lcg_returns(1, days(book, "H1")))
    (run,) = gate.selectable_runs(registered(book))["H1"]
    assert gate.bootstrap_interval(run) == "interval"
    assert seen == {"seed": 20261111, "block_length": 20, "reps": 10000, "confidence": 0.90}
    registered_text = book.registration.doc["statistics"]["bootstrap"]
    assert "seed=20261111, block_length=20, reps=10000, confidence=0.90" in registered_text


def test_gate_refuses_a_ledger_that_is_not_the_verified_one(world):
    for cid in ("H1-01", "B-H1", "B-H2", "H1-02"):
        harness.run(world.repo, {"configuration": cid}, store=world.store)
    current = gate.gate_ledger(world.ledger)  # before the unseal: the current ledger
    assert not current.closed and current.commit == world.ledger.local_head()
    assert gate.trial_count(current) == 2
    entries = current.snapshot.entries
    prefix = LedgerSnapshot(entries[:-2], current.snapshot.series_files)
    with pytest.raises(GateRefused, match="only the verified ledger"):
        gate.gate_ledger(world.ledger, prefix)
    other = Book()
    other.run("H1-01", lcg_returns(1, days(other, "H1")))
    with pytest.raises(GateRefused, match="only the verified ledger"):
        gate.gate_ledger(world.ledger, other.snapshot())
    assert gate.gate_ledger(world.ledger, current.snapshot).snapshot.data == current.snapshot.data


def test_gate_statistics_refuse_a_ledger_the_gate_did_not_issue():
    # A prefix built by hand would compute with a smaller N (M8).
    book = Book()
    full_family(book)
    book.open("H1-01", rerun=FIX)
    ledger = registered(book)
    run = gate.selectable_runs(ledger)["H1"][0]
    entries = ledger.snapshot.entries
    prefix = LedgerSnapshot(entries[:-1], ledger.snapshot.series_files)
    forged = [
        RegisteredLedger(prefix, book.registration, None, True),
        dataclasses.replace(ledger, snapshot=prefix),
    ]
    for candidate in forged:
        for function in (gate.trial_count, gate.selectable_runs, gate.candidates,
                         gate.missing_selectable_runs):
            with pytest.raises(GateRefused, match="gate_ledger issued"):
                function(candidate)
        with pytest.raises(GateRefused):
            gate.deflated_sharpe_of(candidate, run)
        with pytest.raises(GateRefused):
            gate.probability_of_backtest_overfitting(candidate, "H1")
    assert gate.trial_count(ledger) == 15
    # A run that is not one of the ledger's selectable runs is refused too.
    other = Book()
    other.run("H1-01", lcg_returns(5, days(other, "H1"), 0.01))
    foreign = gate.selectable_runs(registered(other))["H1"][0]
    with pytest.raises(GateRefused, match="not a selectable run"):
        gate.deflated_sharpe_of(ledger, foreign)


# --- section 10's DEV-1 to DEV-5 and section 11's holdout candidate (G1.6) ------------
#
# Expected values are computed here from section 10's formulas, written out with the
# standard library, never by calling the code under test (quant review L6).

NORMAL = statistics.NormalDist()
EULER = 0.5772156649015329


def my_sharpe(x):
    sd = statistics.stdev(x)
    return 0.0 if sd == 0 else statistics.fmean(x) / sd


def my_z(x, n_trials, variance):
    """Section 10's PSR statistic against SR0, Pearson moments with 1/T."""
    n = len(x)
    mean = math.fsum(x) / n
    m2 = math.fsum((v - mean) ** 2 for v in x) / n
    g3 = math.fsum((v - mean) ** 3 for v in x) / n / m2**1.5
    g4 = math.fsum((v - mean) ** 4 for v in x) / n / m2**2
    emz = 0.0 if n_trials == 1 else (
        (1 - EULER) * NORMAL.inv_cdf(1 - 1 / n_trials)
        + EULER * NORMAL.inv_cdf(1 - 1 / (n_trials * math.e)))
    sr, sr0 = my_sharpe(x), math.sqrt(variance) * emz
    return (sr - sr0) * math.sqrt(n - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr * sr)


def development_book(skilled="H1-03", benchmark=True, h2=False) -> Book:
    """Every H1 configuration with a selectable run, ``skilled`` the one with an edge;
    with ``h2``, every H2 configuration too, none with an edge, and B-H2."""
    book = Book()
    for i, cid in enumerate(book.registration.order["H1"]):
        drift = 0.002 if cid == skilled else 0.0
        book.run(cid, lcg_returns(200 + i, days(book, "H1"), drift, scale=0.04))
    if benchmark:
        book.run("B-H1", lcg_returns(999, days(book, "H1"), 0.0, scale=0.04))
    if h2:
        for i, cid in enumerate(book.registration.order["H2"]):
            book.run(cid, lcg_returns(500 + i, days(book, "H2"), 0.0, scale=0.04))
        book.run("B-H2", lcg_returns(998, days(book, "H2"), 0.0, scale=0.04))
    return book


def family_book():
    """Both hypotheses' grids and both benchmarks, the strategies exposed to a market."""
    book = Book()
    drifts = {"H1-03": 0.0025, "H1-06": 0.0022, "H2-02": 0.0012, "H2-05": 0.0010}
    series = {}
    for h in ("H1", "H2"):
        for i, cid in enumerate(book.registration.order[h]):
            series[cid] = lcg_returns(300 + i + (50 if h == "H2" else 0), days(book, h),
                                      drifts.get(cid, 0.0001 * i), scale=0.04)
            book.run(cid, series[cid])
    for k, h in enumerate(("H1", "H2")):
        series[f"B-{h}"] = lcg_returns(900 + k, days(book, h), 0.0008, scale=0.06)
        book.run(f"B-{h}", series[f"B-{h}"])
    return book, series


def test_development_applies_dev_1_to_dev_5_to_the_candidate_alone():
    book = development_book()
    ledger = issued(book)
    judged = gate.development(ledger, "H1")
    assert judged.candidate.configuration_id == "H1-03"
    assert judged.criteria == dict.fromkeys(("DEV-1", "DEV-2", "DEV-3", "DEV-4", "DEV-5"), True)
    assert judged.passes and gate.development(ledger, "H2") is None
    assert gate.holdout_candidate(ledger) is judged.candidate


def test_dev_2_matches_an_independent_computation_for_both_hypotheses():
    # H1 volatility matched, H2 beta matched (D15), N from the ledger, V the null
    # variance of the hypothesis's window (decision D22), each against its own
    # hypothesis's benchmark. Swapping the two adjustments would change both answers.
    book, series = family_book()
    ledger = issued(book)

    def excess(cid, beta_for_h2=True):
        h = cid[:2]
        r, rb = series[cid], series[f"B-{h}"]
        if h == "H2" and beta_for_h2:
            k = statistics.linear_regression(rb, r).slope
        else:
            k = statistics.stdev(r) / statistics.stdev(rb)
        return [a - k * b for a, b in zip(r, rb, strict=True)]

    for h, cand in gate.candidates(ledger).items():
        variance = 1 / (days(book, h) - 1)
        expected = NORMAL.cdf(my_z(excess(cand.configuration_id), 14, variance))
        got = gate.excess_deflated_sharpe_of(ledger, cand)
        assert got.n_trials == 14
        assert got.trial_sharpe_variance == pytest.approx(variance, rel=1e-12)
        assert got.dsr == pytest.approx(expected, abs=1e-12), h
    h2 = gate.candidates(ledger)["H2"].configuration_id
    swapped = NORMAL.cdf(my_z(excess(h2, beta_for_h2=False), 14, 1 / (days(book, "H2") - 1)))
    assert abs(swapped - gate.excess_deflated_sharpe_of(
        ledger, gate.candidates(ledger)["H2"]).dsr) > 1e-6


def test_dev_4_removes_each_year_including_the_partial_first_years():
    book, series = family_book()
    ledger = issued(book)
    for h, first_year in (("H1", 2018), ("H2", 2020)):
        cand = gate.candidates(ledger)[h]
        window = book.registration.windows[h].day_list()
        by_year = gate.sharpe_without_each_year(ledger, cand)
        assert sorted(by_year) == list(range(first_year, 2026))
        for year in (first_year, 2023):
            kept = [v for d, v in zip(window, series[cand.configuration_id], strict=True)
                    if not d.startswith(str(year))]
            assert by_year[year] == pytest.approx(my_sharpe(kept), rel=1e-12)


def test_dev_5_removes_the_largest_returns_not_the_first_nor_the_largest_absolute():
    # Twenty large days interleaved late and one large loss: dropping the 20 largest
    # signed returns, the registered rule, differs from dropping the first 20 days and
    # from dropping the 20 largest in absolute value (quant review L6a).
    returns = [0.001] * 100
    for i in range(23, 100, 4):
        returns[i] = 0.02
    returns[60] = -0.05
    run = gate.SelectableRun("H1", "H1-01", 0, None, None, tuple(returns))
    rule = my_sharpe([v for v in returns if v != 0.02])
    first_days = my_sharpe(returns[20:])
    by_size = sorted(range(100), key=lambda i: (-abs(returns[i]), i))[:20]
    absolute = my_sharpe([v for i, v in enumerate(returns) if i not in set(by_size)])
    assert gate.sharpe_without_largest(run) == pytest.approx(rule, rel=1e-12)
    assert abs(rule - first_days) > 0.1 and abs(rule - absolute) > 0.1


def test_the_candidate_rule_ranks_by_z_where_the_dsr_saturates():
    # Quant review M1, its reproduction: every run's DSR rounds to exactly 1.0, so the
    # tie rule on the DSR would pick H1-01 and H2-01. Ranked by z, as the registered
    # ordering by DSR is, the candidates are the runs with the highest z.
    book = Book()
    sat = {}
    for h in ("H1", "H2"):
        for i, cid in enumerate(book.registration.order[h]):
            sat[cid] = lcg_returns(41 if h == "H1" else 43, days(book, h),
                                   0.0020 + 0.00002 * i, scale=0.02)
            book.run(cid, sat[cid])
    ledger = issued(book)
    found = gate.candidates(ledger)
    for h in ("H1", "H2"):
        variance = 1 / (days(book, h) - 1)
        runs = gate.selectable_runs(ledger)[h]
        assert all(gate.deflated_sharpe_of(ledger, r).dsr == 1.0 for r in runs)
        zs = {c: my_z(sat[c], 14, variance) for c in book.registration.order[h]}
        best = max(zs, key=lambda c: (zs[c], -book.registration.order[h].index(c)))
        assert best == book.registration.order[h][-1] == found[h].configuration_id
        assert gate.deflated_sharpe_z(ledger, found[h]) == pytest.approx(zs[best], rel=1e-9)


def test_a_zero_mean_run_with_variance_has_its_registered_dsr():
    # Quant review L4: a Sharpe ratio of exactly 0 with variance is not "no variance".
    # With one trial, SR0 is 0, so z is 0 and the DSR is Phi(0), 0.5.
    book = Book()
    book.run("H1-01", [0.01, -0.01] * (days(book, "H1") // 2))
    ledger = issued(book)
    run = gate.selectable_runs(ledger)["H1"][0]
    assert gate.deflated_sharpe_of(ledger, run).dsr == 0.5
    assert gate.deflated_sharpe_z(ledger, run) == 0.0
    flat = Book()
    flat.run("H1-01", [0.0] * days(flat, "H1"))
    flat_run = gate.selectable_runs(issued(flat))["H1"][0]
    assert gate.deflated_sharpe_of(issued(flat), flat_run).dsr == 0.0
    assert gate.deflated_sharpe_z(issued(flat), flat_run) == -math.inf


def unseal_body_for(book: Book, cid: str = "H1-03") -> dict:
    """An unseal entry naming ``cid``'s selectable run, as the seal would write it."""
    genesis = book.entries[0]
    snapshot = book.snapshot()
    opened = [e for e in snapshot.opens("trial") if e.body["configuration_id"] == cid][-1]
    return {"type": "unseal", "preregistration": genesis.body["preregistration"],
            "configuration_id": cid, "hypothesis": opened.body["hypothesis"],
            "configuration_sha256": opened.body["configuration_sha256"],
            "candidate": {"open": opened.hash, "result": snapshot.result_of(opened).hash},
            "accepted_diff": hashlib.sha256(b"").hexdigest(),
            "holdout_manifest_sha256": genesis.body["manifests"]["holdout"],
            "key_record_sha256": genesis.body["key_record"]["sha256"], "proof": "ab" * 32,
            "commit": "1" * 40}


def test_the_gate_computes_on_the_entries_before_the_unseal():
    # Decision D21: there is no ledger freeze; the unseal closes development. Before it
    # the holdout candidate is provisional; once it exists N, V and the candidate are the
    # entries' before it, whatever was written after it by hand.
    book = development_book(h2=True)
    before = issued(book)
    assert gate.holdout_candidate(before).configuration_id == "H1-03"
    unseal = book.add(unseal_body_for(book))
    book.run("H1-05", lcg_returns(7, days(book, "H1"), 0.01, scale=0.04), rerun=FIX)
    snapshot = book.snapshot()
    closed = gate.development_ledger(snapshot)
    assert closed.data == before.snapshot.data and len(closed.entries) == unseal.seq
    assert gate.development_ledger(before.snapshot) is before.snapshot
    assert gate.trial_count(issued(book)) == gate.trial_count(before) + 1
    gate.check_unseal_candidate(closed, book.registration, unseal)
    other = make_entry({**unseal.body, "configuration_id": "H1-01"})
    with pytest.raises(VerificationError, match="registered rule selects H1-03"):
        gate.check_unseal_candidate(closed, book.registration, other)
    failing = development_book(skilled=None, h2=True)
    assert gate.holdout_candidate(issued(failing)) is None
    with pytest.raises(VerificationError, match="no hypothesis passes development"):
        gate.check_unseal_candidate(failing.snapshot(), failing.registration, unseal)


def test_the_unseal_waits_for_both_grids_or_their_budgets():
    # Decision D21 with G3.1's open question 10: section 11, item 3 compares the two
    # candidates, so the unseal waits until each hypothesis has a selectable run of every
    # registered configuration or has exhausted its own entries and the reserve.
    book = development_book()  # H1 complete and passing, H2 not run
    assert gate.holdout_candidate(issued(book)).configuration_id == "H1-03"
    assert "H2 has neither" in gate.unseal_early_error(issued(book))
    with pytest.raises(VerificationError, match="early unseal: H2 has neither"):
        gate.check_unseal_candidate(book.snapshot(), book.registration, make_entry({
            **unseal_body_for(book), "seq": len(book.entries), "prev": book.entries[-1].hash,
            "time": "2026-11-20T00:00:00Z"}))
    assert gate.unseal_early_error(issued(development_book(h2=True))) is None
    # H2 spends its own 12 entries and the reserve its 6 without a selectable run of
    # every configuration (none of these finished): H2 can run no more, so it is no
    # longer stopped early.
    order = book.registration.order["H2"]
    for i in range(book.registration.own_budget["H2"]):
        book.open(order[i % len(order)], rerun=FIX if i >= len(order) else None)
    assert "H2 has neither" in gate.unseal_early_error(issued(book))
    for _ in range(book.registration.reserve_budget):
        book.open(order[0], rerun=FIX, reserve_reason="a synthetic defect found late")
    assert gate.unseal_early_error(issued(book)) is None
    neither = Book()
    assert "H1 and H2 have neither" in gate.unseal_early_error(issued(neither))


def test_replay_checks_each_sensitivity_run_against_its_candidate():
    # Section 8 with decision D21: a sensitivity run re-costs its hypothesis's candidate,
    # once every registered configuration of the hypothesis has a selectable run, before
    # or after the unseal; after it, the candidate is the one on the entries before it.
    book = development_book()
    book.open("H1-03", cost=7.5)
    gate.check_sensitivity_entries(book.snapshot(), book.registration)
    early = Book()
    for i, cid in enumerate(early.registration.order["H1"][:-1]):
        early.run(cid, lcg_returns(200 + i, days(early, "H1")))
    early.open("H1-01", cost=7.5)
    with pytest.raises(VerificationError, match="once every registered configuration of H1"):
        gate.check_sensitivity_entries(early.snapshot(), early.registration)
    other = development_book()
    other.open("H1-01", cost=7.5)
    with pytest.raises(VerificationError, match=r"H1-01 is not H1's candidate \(H1-03\)"):
        gate.check_sensitivity_entries(other.snapshot(), other.registration)
    after = development_book()
    after.add(unseal_body_for(after))
    after.run("H1-05", lcg_returns(7, days(after, "H1"), 0.01, scale=0.04), rerun=FIX)
    assert gate.candidates(issued(after))["H1"].configuration_id == "H1-05"  # on all of it
    after.open("H1-03", cost=10)
    gate.check_sensitivity_entries(after.snapshot(), after.registration)
    after.open("H1-05", cost=10)
    with pytest.raises(VerificationError, match=r"H1-05 is not H1's candidate \(H1-03\)"):
        gate.check_sensitivity_entries(after.snapshot(), after.registration)


def test_dev_2_without_the_benchmark_run_refuses_rather_than_fails():
    ledger = issued(development_book(benchmark=False))
    with pytest.raises(GateRefused, match="needs a completed B-H1 run"):
        gate.development(ledger, "H1")
    assert gate.missing_benchmarks(ledger) == ["B-H1", "B-H2"]


def test_dev_2_uses_only_a_benchmark_run_on_the_trials_harness_code():
    # Quant review M2: section 8 gives the benchmark the trials' fills, costs and rule
    # for halted pairs, so r_B must come from the same harness code as the selectable
    # runs (their recorded harness digest); a run from other code is never used.
    book = development_book(benchmark=False)
    other_code = {"harness": "2" * 64, "H1": "1" * 64, "H2": "1" * 64}
    book.run("B-H1", lcg_returns(999, days(book, "H1"), 0.0, scale=0.04), trees=other_code)
    ledger = issued(book)
    with pytest.raises(GateRefused, match="on the harness code its selectable runs used"):
        gate.benchmark_returns(ledger, "H1")
    assert gate.missing_benchmarks(ledger) == ["B-H1", "B-H2"]
    same = lcg_returns(998, days(book, "H1"), 0.0, scale=0.04)
    book.run("B-H1", same, rerun={**FIX})
    book.run("B-H2", lcg_returns(997, days(book, "H2")))  # H2 has no runs: any code
    ledger = issued(book)
    assert gate.benchmark_returns(ledger, "H1") == tuple(same)
    assert gate.missing_benchmarks(ledger) == []


def judged_at(h, **values):
    base = {"dsr": 0.99, "z": 3.0, "excess_dsr": 0.99, "pbo": 0.1,
            "sharpe_without_year": {2020: 1.0}, "sharpe_without_largest": 1.0}
    return gate.Development(h, f"candidate {h}", **{**base, **values})


def test_the_development_bars_are_strict():
    # Quant review L6d: above 0.95, below 0.5, above 0; equal fails.
    assert judged_at("H1").passes
    for values, failing in (({"dsr": 0.95}, "DEV-1"), ({"excess_dsr": 0.95}, "DEV-2"),
                            ({"pbo": 0.5}, "DEV-3"), ({"sharpe_without_year": {2020: 0.0}}, "DEV-4"),
                            ({"sharpe_without_largest": 0.0}, "DEV-5")):
        criteria = judged_at("H1", **values).criteria
        assert [k for k, ok in criteria.items() if not ok] == [failing]


def test_the_holdout_candidate_is_the_higher_z_with_ties_to_h1(monkeypatch):
    ledger = issued(Book())
    judged = {h: judged_at(h) for h in ("H1", "H2")}
    monkeypatch.setattr(gate, "development", lambda ledger, h: judged[h])
    assert gate.holdout_candidate(ledger) == "candidate H1"  # a tie goes to H1
    judged["H2"] = dataclasses.replace(judged["H2"], z=3.5)
    assert gate.holdout_candidate(ledger) == "candidate H2"
    # The higher hypothesis fails development, so the other is the holdout candidate.
    judged["H2"] = dataclasses.replace(judged["H2"], pbo=0.5)
    assert gate.holdout_candidate(ledger) == "candidate H1"
    judged["H1"] = dataclasses.replace(judged["H1"], sharpe_without_year={2020: 1.0, 2021: 0.0})
    assert gate.holdout_candidate(ledger) is None  # neither passes: no unseal
