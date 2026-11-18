"""The gate's statistics take the ledger, never N (G1.5 with G1.3).

Section 10 of docs/PREREGISTRATION.md and its section 15, items 1 to 4; threat model
T19. Synthetic returns only.
"""

import dataclasses
import inspect
import math
import statistics

import pytest
from ledger_fixtures import Book, issued, lcg_returns
from norse_research import gate, harness
from norse_research.cscv import cscv
from norse_research.gate import GateRefused, RegisteredLedger
from norse_research.gitledger import FREEZE_ANCHOR
from norse_research.ledger import LedgerSnapshot
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
    sharpes = [moments(r.returns).sharpe for runs in gate.selectable_runs(ledger).values()
               for r in runs]
    m = moments(run.returns)
    expected = deflated_sharpe_ratio(m.sharpe, m.n, m.skew, m.kurtosis, 17,
                                     statistics.variance(sharpes))
    result = gate.deflated_sharpe_of(ledger, run)
    assert result.n_trials == 17
    assert result.dsr == pytest.approx(expected, rel=1e-12)
    # The reported minimum track record length against SR_0 uses the same N.
    sr0 = expected_max_sharpe(17, statistics.variance(sharpes))
    assert gate.min_track_record_of(ledger, run) == pytest.approx((
        min_track_record_length(m.sharpe, m.skew, m.kurtosis, 0.0),
        min_track_record_length(m.sharpe, m.skew, m.kurtosis, sr0),
    ))


def test_v_is_the_variance_of_the_selectable_runs_with_no_floor():
    book = Book()
    # Fourteen configurations with almost the same daily Sharpe ratio, as highly
    # correlated variants would have: V far below the null 1/(T - 1), and used as is.
    for h in ("H1", "H2"):
        for i, cid in enumerate(book.registration.order[h]):
            raw = lcg_returns(5 if h == "H1" else 6, days(book, h))
            mean, sd = statistics.fmean(raw), statistics.stdev(raw)
            book.run(cid, [(x - mean) / sd * 0.01 + 0.0005 + 1e-7 * i for x in raw])
    ledger = registered(book)
    run = gate.selectable_runs(ledger)["H1"][0]
    variance, source = gate.trial_sharpe_variance(ledger, len(run.returns))
    assert source == "selectable runs"
    assert 0 < variance < null_sharpe_variance(len(run.returns)) / 1000
    assert gate.deflated_sharpe_of(ledger, run).trial_sharpe_variance == variance


def test_fewer_than_two_selectable_runs_use_the_null_variance():
    book = Book()
    book.run("H1-01", lcg_returns(3, days(book, "H1"), 0.001))
    book.run("H1-02", None)
    ledger = registered(book)
    (run,) = gate.selectable_runs(ledger)["H1"]
    variance, source = gate.trial_sharpe_variance(ledger, len(run.returns))
    assert variance == 1 / (len(run.returns) - 1) and source.startswith("null")


def test_a_run_with_no_variance_has_a_sharpe_and_a_dsr_of_zero():
    book = Book()
    book.run("H1-01", [0.0] * days(book, "H1"))  # a configuration that never trades
    book.run("H1-02", lcg_returns(4, days(book, "H1"), 0.001))
    ledger = registered(book)
    flat, other = gate.selectable_runs(ledger)["H1"]
    assert gate.daily_sharpe(flat.returns) == 0.0
    result = gate.deflated_sharpe_of(ledger, flat)
    assert result.dsr == 0.0 and result.sharpe == 0.0
    # The flat run still enters V, with a Sharpe ratio of 0.
    variance, _ = gate.trial_sharpe_variance(ledger, len(other.returns))
    assert variance == pytest.approx(statistics.variance([0.0, moments(other.returns).sharpe]))


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


def test_gate_refuses_a_ledger_that_is_not_the_frozen_head(world):
    for cid in ("H1-01", "B-H1", "B-H2", "H1-02"):
        harness.run(world.repo, {"configuration": cid}, store=world.store)
    before_freeze = gate.gate_ledger(world.ledger)  # provisional, the current ledger
    assert not before_freeze.frozen
    gate.freeze(world.ledger, "M2: the ledger freeze", allow_incomplete=True)
    frozen = gate.gate_ledger(world.ledger)
    tags = world.ledger.milestone_tags()
    assert frozen.frozen and tags[f"{FREEZE_ANCHOR}{frozen.commit}"] == frozen.commit
    assert frozen.snapshot.data == before_freeze.snapshot.data
    assert gate.trial_count(frozen) == 2
    entries = frozen.snapshot.entries
    prefix = LedgerSnapshot(entries[:-2], frozen.snapshot.series_files)
    with pytest.raises(GateRefused, match="only the frozen ledger"):
        gate.gate_ledger(world.ledger, prefix)
    other = Book()
    other.run("H1-01", lcg_returns(1, days(other, "H1")))
    with pytest.raises(GateRefused, match="only the frozen ledger"):
        gate.gate_ledger(world.ledger, other.snapshot())
    assert gate.gate_ledger(world.ledger, frozen.snapshot).snapshot.data == frozen.snapshot.data


def test_gate_statistics_refuse_a_ledger_the_gate_did_not_issue():
    # After the freeze, a prefix built by hand would compute with a smaller N (M8).
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


def development_book(skilled="H1-03", benchmark=True) -> Book:
    """Every H1 configuration with a selectable run, ``skilled`` the one with an edge."""
    book = Book()
    for i, cid in enumerate(book.registration.order["H1"]):
        drift = 0.002 if cid == skilled else 0.0
        book.run(cid, lcg_returns(200 + i, days(book, "H1"), drift, scale=0.04))
    if benchmark:
        book.run("B-H1", lcg_returns(999, days(book, "H1"), 0.0, scale=0.04))
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
    ledger = issued(book, frozen=True)
    judged = gate.development(ledger, "H1")
    assert judged.candidate.configuration_id == "H1-03"
    assert judged.criteria == dict.fromkeys(("DEV-1", "DEV-2", "DEV-3", "DEV-4", "DEV-5"), True)
    assert judged.passes and gate.development(ledger, "H2") is None
    assert gate.holdout_candidate(ledger) is judged.candidate


def test_dev_2_matches_an_independent_computation_for_both_hypotheses():
    # H1 volatility matched, H2 beta matched (D15), N from the ledger, V over every
    # selectable run's excess Sharpe ratio across both hypotheses, each against its own
    # hypothesis's benchmark. Swapping the two adjustments would change both answers.
    book, series = family_book()
    ledger = issued(book, frozen=True)

    def excess(cid, beta_for_h2=True):
        h = cid[:2]
        r, rb = series[cid], series[f"B-{h}"]
        if h == "H2" and beta_for_h2:
            k = statistics.linear_regression(rb, r).slope
        else:
            k = statistics.stdev(r) / statistics.stdev(rb)
        return [a - k * b for a, b in zip(r, rb, strict=True)]

    family = book.registration.order["H1"] + book.registration.order["H2"]
    variance = statistics.variance([my_sharpe(excess(c)) for c in family])
    for h, cand in gate.candidates(ledger).items():
        expected = NORMAL.cdf(my_z(excess(cand.configuration_id), 14, variance))
        got = gate.excess_deflated_sharpe_of(ledger, cand)
        assert got.n_trials == 14
        assert got.trial_sharpe_variance == pytest.approx(variance, rel=1e-12)
        assert got.dsr == pytest.approx(expected, abs=1e-12), h
    h2 = gate.candidates(ledger)["H2"].configuration_id
    swapped = NORMAL.cdf(my_z(excess(h2, beta_for_h2=False), 14, variance))
    assert abs(swapped - gate.excess_deflated_sharpe_of(
        ledger, gate.candidates(ledger)["H2"]).dsr) > 1e-6


def test_dev_4_removes_each_year_including_the_partial_first_years():
    book, series = family_book()
    ledger = issued(book, frozen=True)
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
    ledger = issued(book, frozen=True)
    variance = statistics.variance([my_sharpe(v) for v in sat.values()])
    found = gate.candidates(ledger)
    for h in ("H1", "H2"):
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


def test_the_holdout_candidate_is_chosen_on_the_frozen_ledger_only():
    book = development_book()
    with pytest.raises(GateRefused, match="frozen ledger only"):
        gate.holdout_candidate(issued(book))
    assert gate.holdout_candidate(issued(development_book(skilled=None), frozen=True)) is None


def test_dev_2_without_the_benchmark_run_refuses_rather_than_fails():
    ledger = issued(development_book(benchmark=False), frozen=True)
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
    ledger = issued(book, frozen=True)
    with pytest.raises(GateRefused, match="on the harness code its selectable runs used"):
        gate.benchmark_returns(ledger, "H1")
    assert gate.missing_benchmarks(ledger) == ["B-H1", "B-H2"]
    same = lcg_returns(998, days(book, "H1"), 0.0, scale=0.04)
    book.run("B-H1", same, rerun={**FIX})
    book.run("B-H2", lcg_returns(997, days(book, "H2")))  # H2 has no runs: any code
    ledger = issued(book, frozen=True)
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
    ledger = issued(Book(), frozen=True)
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
