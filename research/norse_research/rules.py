"""The run rules of section 9 of docs/PREREGISTRATION.md, as one pure function (G1.5).

The harness applies ``run_rule_error`` before a run, to the ledger it was admitted on,
and refuses the append if the ledger moved in between. Verification replays it over the
whole ledger, so a run admitted on a stale ledger, or written by hand, fails
verification even if it got in (threat model T9, T17; security review M2).

The rules:

* a hypothesis's configurations first run in registered order (run rule 1);
* a configuration that has run before runs again only with a recorded defect (rule 3);
  so does a benchmark, and a control with a seed already used, which keeps runs
  outside N bounded (review H2);
* a hypothesis's own 12 entries come first; the 6 reserve entries are drawn only after
  them, only for reruns, each with its reason (rule 5); past both, nothing runs.

The freeze and the sensitivity rules depend on the freeze tag, which lies outside the
chain, so the harness applies them and the replay does not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .ledger import OPEN, Entry, VerificationError
from .registration import BENCHMARK, CONTROL, TRIAL, Registration


def _same_run(entry: Entry, kind: str, cid: str, seed) -> bool:
    body = entry.body
    if body["configuration_id"] != cid or body["kind"] != kind:
        return False
    return kind != CONTROL or body["configuration"].get("seed") == seed


def run_rule_error(
    prior: Sequence[Entry], registration: Registration, run: Mapping
) -> str | None:
    """Why the registered rules forbid ``run`` after the entries ``prior``, or None.

    ``run`` holds ``kind``, ``hypothesis``, ``configuration_id``, ``seed``, ``rerun``
    and ``reserve_reason``, as an open entry records them.
    """
    kind, h, cid = run["kind"], run["hypothesis"], run["configuration_id"]
    rerun, reason, seed = run["rerun"], run["reserve_reason"], run.get("seed")
    opens = [e for e in prior if e.type == OPEN]
    if kind in (BENCHMARK, CONTROL):
        before = [e for e in opens if _same_run(e, kind, cid, seed)]
        what = cid if kind == BENCHMARK else f"{cid} with seed {seed}"
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


def run_of(entry: Entry) -> dict:
    body = entry.body
    return {
        "kind": body["kind"],
        "hypothesis": body["hypothesis"],
        "configuration_id": body["configuration_id"],
        "seed": body["configuration"].get("seed"),
        "rerun": body["rerun"],
        "reserve_reason": body["reserve_reason"],
    }


def check_run_rules(entries: Sequence[Entry], registration: Registration) -> None:
    """Replay the run rules over a whole ledger; name the first entry that breaks one."""
    for index, entry in enumerate(entries):
        if entry.type != OPEN:
            continue
        error = run_rule_error(entries[:index], registration, run_of(entry))
        if error:
            raise VerificationError(f"entry {entry.seq} breaks the run rules: {error}")
