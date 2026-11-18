"""The gate's statistics, read from the ledger (G1.5 with G1.3).

Every public function here takes the ledger and none takes N: N is always the
ledger's count of trial entries under the registered rule (section 9 of
docs/PREREGISTRATION.md; threat model principle 3 and T19). The statistics are
G1.3's, applied as section 10 registers them:

* the deflated Sharpe ratio (DEV-1) calls ``sharpe.deflated_sharpe_ratio`` directly,
  with N from the ledger and V the sample variance of the daily Sharpe ratios of
  every selectable run, both hypotheses together, with no floor; with fewer than two
  selectable runs, V is 1/(T - 1) of the run's window. ``sharpe.deflated_sharpe``,
  which floors V and wants one Sharpe ratio per trial in N, is not used (section 15,
  item 1);
* a run whose daily returns have no variance has a Sharpe ratio of 0 and a DSR of 0,
  decided here before G1.3's functions are called, since ``sharpe.moments`` raises
  (section 15, item 4);
* the PBO matrix has one column per selectable run of the hypothesis, in registered
  order, and one row per day of its window (section 15, item 2);
* each hypothesis's candidate is its selectable run with the highest DEV-1 DSR, ties
  to the earlier registered configuration, and only once every registered
  configuration has a selectable run (section 11, items 1 and 2; run rule 2). It is
  ranked by the DSR's z, which orders runs exactly as the DSR does but does not round
  to 1.0 in floating point (G1.6 quant review M1);
* the minimum track record length, reported only, is against 0 and against SR_0 with N
  from the ledger;
* the bootstrap intervals pass every registered argument explicitly, since
  ``block_bootstrap``'s default confidence is 0.95 and the registered one 0.90
  (section 15, item 3).

A selectable run is a configuration's last completed trial entry whose returns cover
its hypothesis's whole window; a hypothesis whose selectable runs come from different
files under the harness paths or under its own strategy paths (their recorded digests
differ) has none (run rule 4). Once the freeze anchor exists, the gate takes only the
ledger at that anchor (T19); any figure computed before it is provisional (run rule 3).

The development criteria and the holdout candidate (G1.6, which the unseal needs;
section 10 and section 11, items 1 to 3):

* DEV-1, the candidate's DSR, above 0.95;
* DEV-2, the DSR of its returns in excess of its benchmark, above 0.95: for H1
  ``excess_returns(r, volatility_matched(r_B, r))``, for H2 ``excess_returns(r,
  beta_matched(r_B, r))`` (D15), with the same N and V the sample variance of every
  selectable run's excess-return Sharpe ratio, each against its own hypothesis's
  benchmark, with no floor. r_B is the hypothesis's benchmark run chosen as a
  selectable run is: its last completed entry whose returns cover the window. Without
  one, DEV-2 cannot be computed and the gate refuses rather than call it a fail;
* DEV-3, the PBO of the candidate's hypothesis, below 0.5;
* DEV-4, the Sharpe ratio of the window's returns with each calendar year removed in
  turn, every one above 0;
* DEV-5, the Sharpe ratio without the 20 largest daily returns (ties: earliest dates
  first), above 0.

A hypothesis passes development if and only if its candidate meets all five; the
holdout candidate is, among passing hypotheses, the candidate with the higher DEV-1
DSR, ties to H1, chosen on the frozen ledger only. None passing means no holdout
candidate, no unseal and no holdout run.
"""

from __future__ import annotations

import argparse
import math
import weakref
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .benchmark import beta_matched, excess_returns, volatility_matched
from .block_bootstrap import BootstrapResult, block_bootstrap
from .cscv import GATE_BLOCKS, CSCVResult, cscv
from .environment import check_environment
from .gitledger import FREEZE_ANCHOR, GitLedger, load_registration
from .ledger import COMPLETED, Entry, LedgerError, LedgerSnapshot, VerificationError
from .registration import BENCHMARK, HYPOTHESES, TRIAL, Registration
from .sharpe import (
    DeflatedSharpe,
    cross_trial_sharpe_variance,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_track_record_length,
    moments,
    null_sharpe_variance,
    sharpe_standard_error,
)
from .store import REPO_ROOT

# Section 10: the minimum track record length's confidence (z = 1.6448536...), and the
# reported block-bootstrap intervals, every argument explicit.
MIN_TRACK_RECORD_CONFIDENCE = 0.95
BOOTSTRAP_SEED = 20261111
BOOTSTRAP_BLOCK_LENGTH = 20
BOOTSTRAP_REPS = 10_000
BOOTSTRAP_CONFIDENCE = 0.90
# Section 10's development criteria.
DEV_DSR_ABOVE = 0.95  # DEV-1 and DEV-2
DEV_PBO_BELOW = 0.5  # DEV-3
DEV_LARGEST_REMOVED = 20  # DEV-5
# DEV-2's benchmark adjustment per hypothesis: volatility matched for H1, beta matched
# for H2 (decision D15).
EXCESS_MATCH = {"H1": volatility_matched, "H2": beta_matched}


class GateRefused(LedgerError, PermissionError):
    """The gate was handed a ledger it must not compute on."""


@dataclass(frozen=True, eq=False)
class RegisteredLedger:
    """A verified ledger with the registration its genesis entry pins, the ledger
    commit it was read at and whether that commit is the freeze tag's.

    Only ``gate_ledger`` issues one the gate computes on; every public function
    refuses any other, including a copy with a shorter ledger (security review M8)."""

    snapshot: LedgerSnapshot
    registration: Registration
    commit: str | None
    frozen: bool


_ISSUED: weakref.WeakSet = weakref.WeakSet()


def _issue(
    snapshot: LedgerSnapshot, registration: Registration, commit: str | None, frozen: bool
) -> RegisteredLedger:
    """The gate's only constructor. ``gate_ledger`` calls it after verification;
    tests call it for ledgers built in memory (and an AST test checks no other
    module of the package does)."""
    ledger = RegisteredLedger(snapshot, registration, commit, frozen)
    _ISSUED.add(ledger)
    return ledger


def _require(ledger: RegisteredLedger) -> RegisteredLedger:
    if ledger not in _ISSUED:
        raise GateRefused(
            "the gate computes only on a ledger gate_ledger issued (verified, and the frozen "
            "one once the freeze tag exists)"
        )
    return ledger


@dataclass(frozen=True)
class SelectableRun:
    hypothesis: str
    configuration_id: str
    order: int  # position in the hypothesis's registered grid
    open_entry: Entry
    result_entry: Entry
    returns: tuple[float, ...]


def trial_count(ledger: RegisteredLedger) -> int:
    """N: the ledger's trial entries, reruns and unfinished runs included."""
    return _require(ledger).snapshot.trial_count()


def daily_sharpe(returns: Sequence[float]) -> float:
    """The per-day Sharpe ratio; 0 for a series with no variance (section 10)."""
    if len(returns) < 2:
        return 0.0
    try:
        return moments(returns).sharpe
    except ValueError:
        return 0.0


_SELECTABLE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def selectable_runs(ledger: RegisteredLedger) -> dict[str, tuple[SelectableRun, ...]]:
    cached = _SELECTABLE.get(_require(ledger))
    if cached is None:
        cached = _SELECTABLE[ledger] = _selectable_runs(ledger)
    return dict(cached)


def _selectable_runs(ledger: RegisteredLedger) -> dict[str, tuple[SelectableRun, ...]]:
    snapshot, registration = ledger.snapshot, ledger.registration
    genesis = snapshot.genesis
    if genesis is None:
        raise VerificationError("the ledger has no genesis entry")
    trials = snapshot.opens(TRIAL)
    out: dict[str, tuple[SelectableRun, ...]] = {}
    for h in HYPOTHESES:
        days = registration.windows[h].day_list()
        runs = []
        for order, cid in enumerate(registration.order[h]):
            chosen = None
            for opened in trials:
                if opened.body["configuration_id"] != cid:
                    continue
                result = snapshot.result_of(opened)
                if result is None or result.body["status"] != COMPLETED:
                    continue
                series = snapshot.returns_of(result)
                if [d for d, _ in series] == days:
                    chosen = (opened, result, tuple(v for _, v in series))
            if chosen:
                runs.append(SelectableRun(h, cid, order, *chosen))
        trees = {
            (run.open_entry.body["trees"]["harness"], run.open_entry.body["trees"][h])
            for run in runs
        }
        out[h] = tuple(runs) if len(trees) <= 1 else ()
    return out


def trial_sharpe_variance(ledger: RegisteredLedger, n_obs: int) -> tuple[float, str]:
    """V and where it came from: the selectable runs' Sharpe ratios, or the null
    variance of an ``n_obs`` window when fewer than two exist."""
    sharpes = [daily_sharpe(r.returns) for runs in selectable_runs(ledger).values() for r in runs]
    if len(sharpes) >= 2:
        return cross_trial_sharpe_variance(sharpes), "selectable runs"
    return null_sharpe_variance(n_obs), "null (fewer than two selectable runs)"


def _deflated(
    returns: Sequence[float], n_trials: int, variance: float, source: str
) -> tuple[DeflatedSharpe, float]:
    """The deflated Sharpe ratio of ``returns`` and its PSR statistic z, (SR - SR0)
    over the Sharpe ratio's standard error, so DSR = Phi(z). A series with no variance
    has a Sharpe ratio of 0, a DSR of 0 and a z of minus infinity (section 10); a series
    with variance and a mean of exactly 0 is computed like any other (quant review L4)."""
    sr0 = expected_max_sharpe(n_trials, variance)
    try:
        m = moments(returns)
    except ValueError:  # fewer than two returns, or no variance
        empty = DeflatedSharpe(0.0, 0.0, sr0, len(returns), 0.0, 0.0, n_trials, variance, source)
        return empty, -math.inf
    dsr = deflated_sharpe_ratio(m.sharpe, m.n, m.skew, m.kurtosis, n_trials, variance)
    z = (m.sharpe - sr0) / sharpe_standard_error(m.sharpe, m.n, m.skew, m.kurtosis)
    deflated = DeflatedSharpe(dsr, m.sharpe, sr0, m.n, m.skew, m.kurtosis, n_trials, variance,
                              source)
    return deflated, z


def _dev1(ledger: RegisteredLedger, run: SelectableRun) -> tuple[DeflatedSharpe, float]:
    if run not in selectable_runs(ledger).get(run.hypothesis, ()):
        raise GateRefused(f"{run.configuration_id}'s run is not a selectable run of this ledger")
    variance, source = trial_sharpe_variance(ledger, len(run.returns))
    return _deflated(run.returns, trial_count(ledger), variance, source)


def deflated_sharpe_of(ledger: RegisteredLedger, run: SelectableRun) -> DeflatedSharpe:
    """DEV-1: the deflated Sharpe ratio of a selectable run's daily net returns."""
    return _dev1(ledger, run)[0]


def deflated_sharpe_z(ledger: RegisteredLedger, run: SelectableRun) -> float:
    """DEV-1's PSR statistic z, by which the candidate rules rank (quant review M1).
    DSR = Phi(z) is strictly increasing in z, so ranking by z is ranking by the DSR as
    registered; ranking by the DSR's floating-point value is not, since Phi(z) rounds to
    exactly 1.0 for z above about 8.3 and the tie rule would then pick the earlier run."""
    return _dev1(ledger, run)[1]


def min_track_record_of(ledger: RegisteredLedger, run: SelectableRun) -> tuple[float, float]:
    """Reported, never a criterion: the minimum track record length in days against
    SR* = 0 and against SR* = SR_0, with N from the ledger; infinite ("not reached")
    when the Sharpe ratio does not exceed SR*."""
    deflated = deflated_sharpe_of(ledger, run)
    if deflated.sharpe == 0.0:
        return math.inf, math.inf
    return tuple(
        min_track_record_length(
            deflated.sharpe, deflated.skew, deflated.kurtosis, benchmark=threshold,
            confidence=MIN_TRACK_RECORD_CONFIDENCE,
        )
        for threshold in (0.0, deflated.expected_max_sharpe)
    )


def candidates(ledger: RegisteredLedger) -> dict[str, SelectableRun | None]:
    """Each hypothesis's candidate, the selectable run with the highest DEV-1 DSR, ranked
    by its z so the ranking does not saturate (quant review M1), ties to the earlier
    configuration; None while not every registered configuration has a selectable run
    (section 11, item 1; run rule 2)."""
    out: dict[str, SelectableRun | None] = {}
    for h, runs in selectable_runs(ledger).items():
        if len(runs) != len(ledger.registration.order[h]):
            out[h] = None
            continue
        best, best_z = None, -math.inf
        for run in runs:  # registered order, so a tie keeps the earlier configuration
            z = deflated_sharpe_z(ledger, run)
            if best is None or z > best_z:
                best, best_z = run, z
        out[h] = best
    return out


def probability_of_backtest_overfitting(ledger: RegisteredLedger, hypothesis: str) -> CSCVResult:
    """DEV-3: CSCV PBO over the hypothesis's selectable runs, in registered order."""
    columns = [list(r.returns) for r in selectable_runs(_require(ledger))[hypothesis]]
    return cscv(columns, blocks=GATE_BLOCKS)


def bootstrap_interval(run: SelectableRun) -> BootstrapResult:
    """The registered (reported, never a criterion) block-bootstrap interval."""
    return block_bootstrap(
        list(run.returns),
        seed=BOOTSTRAP_SEED,
        block_length=BOOTSTRAP_BLOCK_LENGTH,
        reps=BOOTSTRAP_REPS,
        confidence=BOOTSTRAP_CONFIDENCE,
    )


# --- the development criteria and the holdout candidate (section 10; section 11) -----


def _benchmark_id(registration: Registration, hypothesis: str) -> str:
    return next(
        b for b, spec in registration.benchmarks.items()
        if spec["window"].rsplit(".", 1)[-1] == hypothesis
    )


def _benchmark_series(
    ledger: RegisteredLedger, hypothesis: str, harness_tree: str | None
) -> tuple[float, ...] | None:
    """The hypothesis's benchmark's last completed run covering its window, from the
    harness tree ``harness_tree`` (any tree when None), or None."""
    snapshot, registration = ledger.snapshot, ledger.registration
    bid = _benchmark_id(registration, hypothesis)
    days = registration.windows[hypothesis].day_list()
    chosen = None
    for opened in snapshot.opens(BENCHMARK):
        if opened.body["configuration_id"] != bid:
            continue
        if harness_tree is not None and opened.body["trees"]["harness"] != harness_tree:
            continue
        result = snapshot.result_of(opened)
        if result is None or result.body["status"] != COMPLETED:
            continue
        series = snapshot.returns_of(result)
        if [d for d, _ in series] == days:
            chosen = tuple(v for _, v in series)
    return chosen


def benchmark_returns(ledger: RegisteredLedger, hypothesis: str) -> tuple[float, ...]:
    """r_B for DEV-2: the hypothesis's benchmark run chosen as a selectable run is, its
    last completed entry whose returns cover the window, and run on the same harness
    code as the hypothesis's selectable runs, since section 8 gives the benchmark the
    same fills, costs and rule for halted pairs (quant review M2). Refused when there is
    none, since DEV-2 then cannot be computed."""
    runs = selectable_runs(_require(ledger)).get(hypothesis, ())
    bid = _benchmark_id(ledger.registration, hypothesis)
    if not runs:
        raise GateRefused(f"{hypothesis} has no selectable runs to compare with {bid}")
    tree = runs[0].open_entry.body["trees"]["harness"]  # one tree for them all (run rule 4)
    chosen = _benchmark_series(ledger, hypothesis, tree)
    if chosen is None:
        raise GateRefused(
            f"DEV-2 needs a completed {bid} run covering {hypothesis}'s window on the harness "
            "code its selectable runs used; there is none"
        )
    return chosen


def missing_benchmarks(ledger: RegisteredLedger) -> list[str]:
    """Benchmarks DEV-2 could not use: with no completed run covering their window, or,
    for a hypothesis with selectable runs, none on those runs' harness code (quant
    review M3). The freeze refuses while any is missing, since no benchmark may run
    after it and DEV-2, and with it every candidate, would be refused for ever."""
    family = selectable_runs(_require(ledger))
    missing = []
    for h in HYPOTHESES:
        runs = family.get(h, ())
        tree = runs[0].open_entry.body["trees"]["harness"] if runs else None
        if _benchmark_series(ledger, h, tree) is None:
            missing.append(_benchmark_id(ledger.registration, h))
    return missing


def excess_series(ledger: RegisteredLedger, run: SelectableRun) -> list[float]:
    """A selectable run's daily returns in excess of its benchmark (DEV-2): volatility
    matched for H1, beta matched for H2 (D15)."""
    benchmark = list(benchmark_returns(ledger, run.hypothesis))
    returns = list(run.returns)
    return excess_returns(returns, EXCESS_MATCH[run.hypothesis](benchmark, returns))


def excess_deflated_sharpe_of(ledger: RegisteredLedger, run: SelectableRun) -> DeflatedSharpe:
    """DEV-2: the deflated Sharpe ratio of a selectable run's excess returns, with N
    from the ledger and V over every selectable run's excess-return Sharpe ratio."""
    family = selectable_runs(ledger)
    if run not in family.get(run.hypothesis, ()):
        raise GateRefused(f"{run.configuration_id}'s run is not a selectable run of this ledger")
    n_trials = trial_count(ledger)
    excess = excess_series(ledger, run)
    sharpes = [daily_sharpe(excess_series(ledger, r)) for runs in family.values() for r in runs]
    if len(sharpes) >= 2:
        variance, source = cross_trial_sharpe_variance(sharpes), "selectable runs' excess returns"
    else:
        variance, source = null_sharpe_variance(len(excess)), "null (fewer than two selectable runs)"
    return _deflated(excess, n_trials, variance, source)[0]


def sharpe_without_each_year(ledger: RegisteredLedger, run: SelectableRun) -> dict[int, float]:
    """DEV-4: the Sharpe ratio of the window's returns with each calendar year in it
    removed in turn (2018 from 05-01 to 2025 for H1, 2020 from 01-08 to 2025 for H2)."""
    days = _require(ledger).registration.windows[run.hypothesis].day_list()
    years = sorted({int(d[:4]) for d in days})
    return {
        year: daily_sharpe([v for d, v in zip(days, run.returns, strict=True) if int(d[:4]) != year])
        for year in years
    }


def sharpe_without_largest(run: SelectableRun) -> float:
    """DEV-5: the Sharpe ratio without the 20 largest daily returns, ties removed
    earliest date first."""
    order = sorted(range(len(run.returns)), key=lambda i: (-run.returns[i], i))
    dropped = set(order[:DEV_LARGEST_REMOVED])
    return daily_sharpe([v for i, v in enumerate(run.returns) if i not in dropped])


@dataclass(frozen=True)
class Development:
    """A hypothesis's candidate judged by DEV-1 to DEV-5 (section 10)."""

    hypothesis: str
    candidate: SelectableRun
    dsr: float  # DEV-1
    z: float  # DEV-1's PSR statistic, by which the holdout candidate is ranked
    excess_dsr: float  # DEV-2
    pbo: float  # DEV-3
    sharpe_without_year: dict[int, float]  # DEV-4
    sharpe_without_largest: float  # DEV-5

    @property
    def criteria(self) -> dict[str, bool]:
        return {
            "DEV-1": self.dsr > DEV_DSR_ABOVE,
            "DEV-2": self.excess_dsr > DEV_DSR_ABOVE,
            "DEV-3": self.pbo < DEV_PBO_BELOW,
            "DEV-4": all(s > 0 for s in self.sharpe_without_year.values()),
            "DEV-5": self.sharpe_without_largest > 0,
        }

    @property
    def passes(self) -> bool:
        return all(self.criteria.values())


def development(ledger: RegisteredLedger, hypothesis: str) -> Development | None:
    """The hypothesis's candidate against DEV-1 to DEV-5, or None while it has no
    candidate (section 11, items 1 and 2). No other configuration is judged."""
    candidate = candidates(_require(ledger)).get(hypothesis)
    if candidate is None:
        return None
    deflated, z = _dev1(ledger, candidate)
    return Development(
        hypothesis,
        candidate,
        deflated.dsr,
        z,
        excess_deflated_sharpe_of(ledger, candidate).dsr,
        probability_of_backtest_overfitting(ledger, hypothesis).pbo,
        sharpe_without_each_year(ledger, candidate),
        sharpe_without_largest(candidate),
    )


def _holdout_candidate(ledger: RegisteredLedger) -> SelectableRun | None:
    best = None
    for h in HYPOTHESES:  # H1 first, so a tie keeps H1 (section 11, item 3)
        judged = development(ledger, h)  # ranked by z, which does not saturate (review M1)
        if judged is not None and judged.passes and (best is None or judged.z > best.z):
            best = judged
    return best.candidate if best is not None else None


def holdout_candidate(ledger: RegisteredLedger) -> SelectableRun | None:
    """Section 11, item 3: among hypotheses that pass development, the candidate with
    the higher DEV-1 DSR, ties to H1; None if neither passes (no unseal, no holdout
    run). Only on the frozen ledger: the rule is mechanical over the ledger at the
    freeze anchor, so nothing seen later can change which configuration is judged."""
    if not _require(ledger).frozen:
        raise GateRefused("the holdout candidate is chosen on the frozen ledger only (section 11)")
    return _holdout_candidate(ledger)


def check_unseal_candidate(
    snapshot: LedgerSnapshot, registration: Registration, freeze: str, unseal: Entry
) -> None:
    """On replay, the unseal entry names exactly the holdout candidate the registered
    rule selects on ``snapshot``, the ledger at the freeze anchor (T22)."""
    try:
        candidate = _holdout_candidate(_issue(snapshot, registration, freeze, True))
    except (GateRefused, ValueError) as exc:
        raise VerificationError(
            f"entry {unseal.seq}: the holdout candidate cannot be computed on the frozen "
            f"ledger ({exc}), so no unseal can stand"
        ) from None
    if candidate is None:
        raise VerificationError(
            f"entry {unseal.seq}: an unseal, but no hypothesis passes development on the "
            "frozen ledger, so there is no holdout candidate (section 11, item 3; T22)"
        )
    named = (unseal.body["configuration_id"], unseal.body["configuration_sha256"],
             unseal.body["candidate"]["open"], unseal.body["candidate"]["result"])
    chosen = (candidate.configuration_id, candidate.open_entry.body["configuration_sha256"],
              candidate.open_entry.hash, candidate.result_entry.hash)
    if named != chosen:
        raise VerificationError(
            f"entry {unseal.seq}: the unseal names {named[0]} ({named[1]}), but the registered "
            f"rule selects {chosen[0]} ({chosen[1]}) on the frozen ledger (section 11; T22)"
        )


def require_frozen(given: LedgerSnapshot, frozen: LedgerSnapshot) -> LedgerSnapshot:
    """The gate takes the ledger it verified and nothing else: not a prefix of it, not
    a longer one, not another (T19)."""
    if given.data != frozen.data:
        raise GateRefused(
            f"the gate takes only the frozen ledger (head {frozen.head_hash}); this ledger "
            f"has head {given.head_hash} and {len(given.entries)} entries, not "
            f"{len(frozen.entries)}"
        )
    return frozen


def gate_ledger(git_ledger: GitLedger, given: LedgerSnapshot | None = None) -> RegisteredLedger:
    """The ledger the gate computes on: the one at the freeze tag once it exists
    (anchored and verified), the current verified ledger before it (provisional,
    binding nothing). A ``given`` ledger that is not exactly that one is refused."""
    verified = git_ledger.verify()
    freeze = verified.freeze
    commit = verified.head if freeze is None else freeze
    snapshot = verified.snapshot if freeze is None else git_ledger.snapshot(freeze)
    if given is not None:
        require_frozen(given, snapshot)
    if snapshot.genesis is None:
        raise GateRefused("the ledger has no genesis entry")
    registration = load_registration(git_ledger.git, snapshot.genesis)
    return _issue(snapshot, registration, commit, freeze is not None)


def missing_selectable_runs(ledger: RegisteredLedger) -> list[str]:
    """Registered configurations without a selectable run, in registered order."""
    runs = selectable_runs(ledger)
    have = {r.configuration_id for found in runs.values() for r in found}
    return [c for h in HYPOTHESES for c in ledger.registration.order[h] if c not in have]


def freeze(git_ledger: GitLedger, message: str, *, allow_incomplete: bool = False) -> str:
    """Tag the ledger head with its freeze anchor, ``ledger-freeze-<head commit>``, which
    is irreversible (G2.7). Refused while a registered configuration lacks a selectable
    run, unless the caller states that the freeze leaves it unrun (review M6, N3); while
    either benchmark lacks a completed run DEV-2 can use, with no override (G1.6 quant
    review M3); and under any environment override (N10). A ledger the gate issued
    before the freeze stays provisional: check ``frozen`` before relying on one (N14)."""
    check_environment(None, git_ledger.git.repo)
    ledger = gate_ledger(git_ledger)
    if ledger.frozen:
        raise GateRefused("the ledger is already frozen")
    lacking = missing_benchmarks(ledger)
    if lacking:
        raise GateRefused(
            f"the freeze needs a completed run of each benchmark that DEV-2 can use, and "
            f"{lacking} have none; no benchmark may run after the freeze (section 8)"
        )
    missing = missing_selectable_runs(ledger)
    if missing and not allow_incomplete:
        raise GateRefused(
            f"{len(missing)} registered configurations have no selectable run ({missing}); "
            "freezing now leaves them unrun, so it needs allow_incomplete"
        )
    name = f"{FREEZE_ANCHOR}{ledger.commit}"
    return git_ledger.tag_milestone(name, message, _anchor=True)


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m norse_research.gate freeze --message M [--allow-incomplete]``."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--remote", default="origin")
    commands = parser.add_subparsers(dest="command", required=True)
    frozen = commands.add_parser("freeze")
    frozen.add_argument("--message", required=True)
    frozen.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args(argv)
    tag = freeze(GitLedger(args.repo, args.remote), args.message,
                 allow_incomplete=args.allow_incomplete)
    print(f"freeze anchor: tag object {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
