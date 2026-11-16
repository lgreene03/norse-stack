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
  configuration has a selectable run (section 11, items 1 and 2; run rule 2);
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
"""

from __future__ import annotations

import argparse
import math
import weakref
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .block_bootstrap import BootstrapResult, block_bootstrap
from .cscv import GATE_BLOCKS, CSCVResult, cscv
from .environment import check_environment
from .gitledger import FREEZE_ANCHOR, GitLedger, load_registration
from .ledger import COMPLETED, Entry, LedgerError, LedgerSnapshot, VerificationError
from .registration import HYPOTHESES, TRIAL, Registration
from .sharpe import (
    DeflatedSharpe,
    cross_trial_sharpe_variance,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_track_record_length,
    moments,
    null_sharpe_variance,
)
from .store import REPO_ROOT

# Section 10: the minimum track record length's confidence (z = 1.6448536...), and the
# reported block-bootstrap intervals, every argument explicit.
MIN_TRACK_RECORD_CONFIDENCE = 0.95
BOOTSTRAP_SEED = 20261111
BOOTSTRAP_BLOCK_LENGTH = 20
BOOTSTRAP_REPS = 10_000
BOOTSTRAP_CONFIDENCE = 0.90


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


def deflated_sharpe_of(ledger: RegisteredLedger, run: SelectableRun) -> DeflatedSharpe:
    """DEV-1: the deflated Sharpe ratio of a selectable run's daily net returns."""
    if run not in selectable_runs(ledger).get(run.hypothesis, ()):
        raise GateRefused(f"{run.configuration_id}'s run is not a selectable run of this ledger")
    n_trials = trial_count(ledger)
    n_obs = len(run.returns)
    variance, source = trial_sharpe_variance(ledger, n_obs)
    sr0 = expected_max_sharpe(n_trials, variance)
    if daily_sharpe(run.returns) == 0.0:
        return DeflatedSharpe(0.0, 0.0, sr0, n_obs, 0.0, 0.0, n_trials, variance, source)
    m = moments(run.returns)
    dsr = deflated_sharpe_ratio(m.sharpe, m.n, m.skew, m.kurtosis, n_trials, variance)
    return DeflatedSharpe(dsr, m.sharpe, sr0, m.n, m.skew, m.kurtosis, n_trials, variance, source)


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
    """Each hypothesis's candidate, or None while not every registered
    configuration has a selectable run (section 11, item 1; run rule 2)."""
    out: dict[str, SelectableRun | None] = {}
    for h, runs in selectable_runs(ledger).items():
        if len(runs) != len(ledger.registration.order[h]):
            out[h] = None
            continue
        best, best_dsr = None, -1.0
        for run in runs:  # registered order, so a tie keeps the earlier configuration
            dsr = deflated_sharpe_of(ledger, run).dsr
            if dsr > best_dsr:
                best, best_dsr = run, dsr
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
    run, unless the caller states that the freeze leaves it unrun (review M6, N3), and
    under any environment override (N10). A ledger the gate issued before the freeze
    stays provisional: check ``frozen`` before relying on one (N14)."""
    check_environment(None, git_ledger.git.repo)
    ledger = gate_ledger(git_ledger)
    if ledger.frozen:
        raise GateRefused("the ledger is already frozen")
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
