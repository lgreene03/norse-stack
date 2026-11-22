"""The run rules of section 9 of docs/PREREGISTRATION.md, as pure functions (G1.5).

The harness applies ``run_rule_error`` before a run, to the ledger it was admitted on,
and refuses the append if the ledger moved in between. Verification replays every rule
here over the whole ledger, so a run admitted on a stale ledger, or written by hand,
fails verification even if it got in (threat model T9, T17; security review M2, N7).

The rules:

* a hypothesis's configurations first run in registered order (run rule 1);
* a configuration that has run before runs again only with a recorded defect (rule 3);
  so does a benchmark, a control with a seed already used, and a sensitivity run at a
  cost already used, which keeps runs outside N bounded (review H2, N13);
* a hypothesis's own 12 entries come first; the 6 reserve entries are drawn only after
  them, only for reruns, each with its reason (rule 5); past both, nothing runs;
* every open entry is exactly what the registration resolves its request to: its kind,
  hypothesis, window, costs, seed and registered parameters, with the strategy the
  genesis entry pins for it (review N7);
* the unseal closes development (decision D21; there is no ledger freeze): after it no
  trial, benchmark or control run is opened. The result of one opened before it may
  still land after it; replay accepts it and the gate ignores it, since it computes on
  the entries before the unseal (security review of #65, L-1). A sensitivity run is of
  a hypothesis's candidate, once every registered configuration of that hypothesis has
  a selectable run, before or after the unseal; that needs the gate, so ``gate``
  replays it (section 8);
* the unseal comes once (section 11, item 4; T23);
* a holdout run comes after the unseal, runs the unseal's candidate configuration and
  nothing else, and after one completed holdout result nothing more: a run that failed
  or never finished may be retried, every attempt ledgered (section 11, items 5 and
  6; T22). The commit rule of item 6 needs git, so ``gitledger`` replays it;
* every holdout run, and every attempt within one, is at exactly the commit the unseal
  records, where item 6's rule was applied once, and at most ``HOLDOUT_ATTEMPTS`` runs
  reach the unsealed stage. Nothing a run reports about itself decides anything: on a
  machine with one user no such report can be authenticated (security review round 2,
  N1; the coordinator's decision, in docs/research/holdout-seal.md).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .ledger import (
    ATTEMPT,
    COMPLETED,
    OPEN,
    RESULT,
    UNSEAL,
    Entry,
    VerificationError,
)
from .registration import (
    BENCHMARK,
    CONTROL,
    HOLDOUT,
    SENSITIVITY,
    TRIAL,
    Registration,
    RegistrationError,
)


def same_run(entry: Entry, run: Mapping) -> bool:
    body = entry.body
    if body["configuration_id"] != run["configuration_id"] or body["kind"] != run["kind"]:
        return False
    if run["kind"] == CONTROL:
        return body["configuration"].get("seed") == run.get("seed")
    if run["kind"] == SENSITIVITY:
        return body["configuration"].get("costs") == run.get("costs")
    return True


def run_rule_error(
    prior: Sequence[Entry], registration: Registration, run: Mapping
) -> str | None:
    """Why the registered rules forbid ``run`` after the entries ``prior``, or None.

    ``run`` holds ``kind``, ``hypothesis``, ``configuration_id``, ``seed``, ``costs``,
    ``rerun`` and ``reserve_reason``, as an open entry records them.
    """
    kind, h, cid = run["kind"], run["hypothesis"], run["configuration_id"]
    rerun, reason = run["rerun"], run["reserve_reason"]
    opens = [e for e in prior if e.type == OPEN]
    if kind == HOLDOUT:
        return holdout_rule_error(prior, run)
    if kind in (BENCHMARK, CONTROL, SENSITIVITY):
        before = [e for e in opens if same_run(e, run)]
        what = {
            BENCHMARK: cid,
            CONTROL: f"{cid} with seed {run.get('seed')}",
            SENSITIVITY: f"{cid}'s sensitivity run at this cost",
        }[kind]
        if before and rerun is None:
            return f"{what} has run before; a rerun records its defect (section 9, run rule 3)"
        if not before and rerun is not None:
            return f"{what} has not run, so this is not a rerun"
        return None
    if kind != TRIAL:
        return None
    trials = [e for e in opens if e.body["kind"] == TRIAL]
    before = [e for e in trials if e.body["configuration_id"] == cid]
    if not before:
        if rerun is not None:
            return f"{cid} has not run, so this is not a rerun"
        order = registration.order[h]
        ran = {e.body["configuration_id"] for e in trials}
        missing = [c for c in order[: order.index(cid)] if c not in ran]
        if missing:
            return (
                f"{h}'s configurations run in registered order: {missing} first "
                "(section 9, run rule 1)"
            )
    elif rerun is None:
        return (
            f"{cid} has run before; a rerun records its defect and the commit that fixes it "
            "(section 9, run rule 3)"
        )
    own = sum(1 for e in trials if e.body["hypothesis"] == h and e.body["reserve_reason"] is None)
    reserve = sum(1 for e in trials if e.body["reserve_reason"] is not None)
    if own < registration.own_budget[h]:
        if reason is not None:
            return (
                f"the reserve is drawn only after {h} has used its own "
                f"{registration.own_budget[h]} entries (section 9, run rule 5)"
            )
        return None
    if reserve >= registration.reserve_budget:
        return (
            f"{h} has used its {registration.own_budget[h]} trial entries and the reserve its "
            f"{registration.reserve_budget}: the budget is exhausted (section 9)"
        )
    if not before:
        return "reserve entries are only for reruns (section 9, run rule 5)"
    if reason is None:
        return (
            f"{h}'s own entries are used: a reserve trial needs a recorded reason "
            "(section 9, run rule 5)"
        )
    return None


def holdout_rule_error(prior: Sequence[Entry], run: Mapping) -> str | None:
    """Why the holdout run ``run`` may not follow ``prior``, or None (section 11)."""
    unseal = next((e for e in prior if e.type == UNSEAL), None)
    if unseal is None:
        return "a holdout run comes only after the unseal entry (section 11, item 4)"
    if (run["configuration_id"], run.get("candidate_sha256")) != (
        unseal.body["configuration_id"], unseal.body["configuration_sha256"]
    ):
        return (
            f"the unseal opens the holdout to {unseal.body['configuration_id']}'s "
            f"configuration {unseal.body['configuration_sha256']} alone (section 11, items 4 "
            "and 6; T22)"
        )
    if run["rerun"] is not None or run["reserve_reason"] is not None:
        return "a holdout run carries no rerun record or reserve reason"
    held = {e.hash for e in prior if e.type == OPEN and e.body["kind"] == HOLDOUT}
    if any(e.type == RESULT and e.body["opens"] in held and e.body["status"] == COMPLETED
           for e in prior):
        return "one result spends the seal: the holdout run has its result (section 11, item 6)"
    return holdout_attempt_error(prior, run.get("commit"))


# Section 11, item 6 lets a holdout run that fails before producing a result be retried,
# with no limit stated. This implements it conservatively: at most three holdout runs
# reach the unsealed stage (an attempt entry, written just before the strategy is first
# given the unseal), every one at the unseal's commit, so a retry can only repeat a run
# that failed for a reason outside the code and data (a crash, a full disk), never
# change what it computes (security review round 2, N1 and N3). A run that fails before
# its attempt entry, in the development reproduction or on a network error, read nothing
# of the holdout and does not count, so failures that need no key cannot use the cap
# up (security review round 3, C1).
HOLDOUT_ATTEMPTS = 3


def holdout_attempt_error(prior: Sequence[Entry], commit: str | None) -> str | None:
    """Why a holdout run or attempt at ``commit`` may not follow ``prior``, or None: the
    unseal's commit only, and fewer than ``HOLDOUT_ATTEMPTS`` earlier attempts."""
    problem = holdout_commit_rule(prior, commit)
    if problem:
        return problem
    attempts = sum(1 for e in prior if e.type == ATTEMPT)
    if attempts >= HOLDOUT_ATTEMPTS:
        return (
            f"the holdout has reached its unsealed stage {attempts} times, the most section "
            f"11, item 6 allows here ({HOLDOUT_ATTEMPTS})"
        )
    return None


def holdout_commit_rule(prior: Sequence[Entry], commit: str | None) -> str | None:
    """Why a holdout run or attempt at ``commit`` may not follow ``prior``, or None: it
    must be at exactly the commit the unseal records (security review round 2, N1)."""
    unseal = next((e for e in prior if e.type == UNSEAL), None)
    if unseal is None:
        return "a holdout run comes only after the unseal entry (section 11, item 4)"
    if commit != unseal.body["commit"]:
        return (
            f"every holdout run is at the unseal's commit {unseal.body['commit']}, where item "
            f"6's rule was applied, and not at {commit} (security review round 2, N1)"
        )
    return None


def run_of(entry: Entry) -> dict:
    body = entry.body
    return {
        "kind": body["kind"],
        "hypothesis": body["hypothesis"],
        "configuration_id": body["configuration_id"],
        "seed": body["configuration"].get("seed"),
        "costs": body["configuration"].get("costs"),
        "rerun": body["rerun"],
        "reserve_reason": body["reserve_reason"],
        "candidate_sha256": body["configuration"].get("candidate_sha256"),
        "commit": body["commit"],
    }


class _Declared:
    """The strategy declarations an open entry implies, to resolve it again."""

    def __init__(self, module: str, parameters: Mapping, registered: Mapping) -> None:
        self.module = module
        self.parameters = frozenset(parameters)
        self.defaults = {k: v for k, v in parameters.items() if k not in registered}


def registration_error(entry: Entry, registration: Registration, genesis: Entry) -> str | None:
    """Why an open entry is not what the registration resolves its request to."""
    body = entry.body
    config = body["configuration"]
    cid = body["configuration_id"]
    pin = genesis.body["strategies"].get(cid)
    if pin is None:
        return f"{cid} is not a registered configuration"
    if body["strategy"] != f"{pin['path']}:{pin['function']}":
        return f"its strategy {body['strategy']} is not the one genesis pins for {cid}"
    extra = {
        "seed": config.get("seed"),
        "sensitivity_total_cost_bps_per_side": config.get("sensitivity_total_cost_bps_per_side"),
        "rerun": body["rerun"],
        "reserve_reason": body["reserve_reason"],
        "label": body["label"],
    }
    request = {"configuration": cid, **{k: v for k, v in extra.items() if v is not None}}
    base = registration.grid.get(cid) or registration.grid.get(
        registration.controls.get(cid, {}).get("base", ""), {}
    )
    registered = {k for k in base if k not in ("id", "label")}
    parameters = config.get("parameters")
    if not isinstance(parameters, Mapping):
        return "its configuration has no parameters"
    try:
        resolved = registration.resolve(
            request, _Declared(body["strategy"], parameters, registered)
        )
    except RegistrationError as exc:
        return str(exc)
    if resolved.configuration != config or resolved.kind != body["kind"]:
        return (
            f"it is not what the registration resolves {cid} to (a {resolved.kind} with "
            "the registered window, costs, seed and parameters)"
        )
    return None


def holdout_registration_error(
    entry: Entry, prior: Sequence[Entry], registration: Registration, genesis: Entry
) -> str | None:
    """Why a holdout open entry is not the holdout run of the unseal's candidate, with
    the strategy genesis pins and the registered holdout manifest, or None."""
    unseal = next((e for e in prior if e.type == UNSEAL), None)
    if unseal is None:
        return "a holdout run comes only after the unseal entry"
    candidate = next((e for e in prior if e.hash == unseal.body["candidate"]["open"]), None)
    if candidate is None:
        return "the unseal's candidate is not in the ledger"
    pin = genesis.body["strategies"].get(candidate.body["configuration_id"], {})
    if entry.body["strategy"] != f"{pin.get('path')}:{pin.get('function')}":
        return "its strategy is not the one genesis pins for the candidate"
    try:
        expected = registration.holdout_configuration(
            candidate.body["configuration"], candidate.body["configuration_sha256"],
            genesis.body["manifests"]["holdout"],
        )
    except RegistrationError as exc:
        return str(exc)
    if entry.body["configuration"] != expected:
        return (
            "it is not the holdout run of the unseal's candidate (its configuration, window "
            "through the holdout's end and the registered holdout manifest; section 11, item 5)"
        )
    return None


def budget_exhausted(entries: Sequence[Entry], registration: Registration, h: str) -> bool:
    """Whether hypothesis ``h`` has used its own trial entries and the reserve is used,
    so it can run no further trial (section 9, run rule 5; the budget rule above)."""
    trials = [e for e in entries if e.type == OPEN and e.body["kind"] == TRIAL]
    own = sum(1 for e in trials if e.body["hypothesis"] == h and e.body["reserve_reason"] is None)
    reserve = sum(1 for e in trials if e.body["reserve_reason"] is not None)
    return own >= registration.own_budget[h] and reserve >= registration.reserve_budget


def after_unseal_error(prior: Sequence[Entry], kind: str) -> str | None:
    """Why a run of ``kind`` may not be opened after ``prior``, or None: the unseal
    closes development, so no trial, benchmark or control is opened after it (decision
    D21). Only opens are refused: the result of a run opened before the unseal is
    accepted after it, and the gate ignores it (L-1)."""
    if kind in (TRIAL, BENCHMARK, CONTROL) and any(e.type == UNSEAL for e in prior):
        return (
            f"a {kind} run after the unseal, which closed development (decision D21; "
            "section 11)"
        )
    return None


def check_run_rules(
    entries: Sequence[Entry],
    registration: Registration,
    genesis: Entry | None = None,
) -> None:
    """Replay the run rules over a whole ledger; name the first entry that breaks one.

    With ``genesis``, every open entry must also be what the registration resolves it
    to. Which configuration a sensitivity run may re-cost needs the gate, which replays
    it (``gate.check_sensitivity_entries``)."""
    for index, entry in enumerate(entries):
        if entry.type == ATTEMPT:
            error = holdout_attempt_error(entries[:index], entry.body["commit"])
            if error:
                raise VerificationError(f"entry {entry.seq} breaks the run rules: {error}")
        if entry.type != OPEN:
            continue
        kind = entry.body["kind"]
        error = after_unseal_error(entries[:index], kind)
        if error is None:
            error = run_rule_error(entries[:index], registration, run_of(entry))
        if error is None and genesis is not None:
            error = (
                holdout_registration_error(entry, entries[:index], registration, genesis)
                if kind == HOLDOUT else registration_error(entry, registration, genesis)
            )
        if error:
            raise VerificationError(f"entry {entry.seq} breaks the run rules: {error}")
