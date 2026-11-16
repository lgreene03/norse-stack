"""The gate's statistics take the ledger, never N (G1.5 with G1.3).

Section 10 of docs/PREREGISTRATION.md and its section 15, items 1 to 4; threat model
T19. Synthetic returns only.
"""

import dataclasses
import inspect
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
    for cid in ("H1-01", "B-H1", "H1-02"):
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
