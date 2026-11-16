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
* before the freeze no sensitivity run, after it nothing else (section 8; review N2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .ledger import OPEN, Entry, VerificationError
from .registration import (
    BENCHMARK,
    CONTROL,
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


def check_run_rules(
    entries: Sequence[Entry],
    registration: Registration,
    genesis: Entry | None = None,
    frozen_length: int | None = None,
) -> None:
    """Replay the run rules over a whole ledger; name the first entry that breaks one.

    With ``genesis``, every open entry must also be what the registration resolves it
    to; ``frozen_length`` is the number of entries at the freeze anchor, if any."""
    for index, entry in enumerate(entries):
        if entry.type != OPEN:
            continue
        error = run_rule_error(entries[:index], registration, run_of(entry))
        if error is None and genesis is not None:
            error = registration_error(entry, registration, genesis)
        if error is None:
            after = frozen_length is not None and index >= frozen_length
            if entry.body["kind"] == SENSITIVITY and not after:
                error = "a sensitivity run before the freeze"
            elif entry.body["kind"] != SENSITIVITY and after:
                error = f"a {entry.body['kind']} run after the freeze"
        if error:
            raise VerificationError(f"entry {entry.seq} breaks the run rules: {error}")
