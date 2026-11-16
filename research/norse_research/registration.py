"""The pre-registration as the trial ledger reads it (G1.5).

``docs/preregistration.json`` governs the gate (its own ``precedence`` field). The
harness never reads it from the working tree, a branch or a tag, all of which can
move: it reads the file from the commit the ledger's genesis entry pins and checks
its SHA-256 against that entry first (threat model principle 4, T24).

What a run is comes from here, never from a label the caller supplies (section 9 of
docs/PREREGISTRATION.md, threat T18). A run resolves to one registered configuration:

* a trial: a configuration in the H1 or H2 grid, run over its hypothesis's whole
  out-of-sample window;
* a benchmark (B-H1, B-H2) or a control (C-H1, C-H2 with a seed in the registered
  range), ledgered but outside N;
* a sensitivity run: a hypothesis's candidate re-costed at a registered total cost,
  after the freeze only;
* the holdout run (G1.6), never resolved from a request: it is the unseal's candidate
  configuration with its window carried from the start of the development window
  through 2026-10-31, the candidate's configuration hash and the holdout manifest's
  hash added (``holdout_configuration``; section 11, item 5).

Anything else is refused. The resolved configuration is everything that decides the
run's result: the registered parameters, the strategy's defaults in code, the costs,
the window and the seed. Its hash is the full SHA-256 of its canonical JSON (T16).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .canonical import GIT_OBJECT_ID, loads_strict, sha256_hex, value_hash

PREREGISTRATION_MD = "docs/PREREGISTRATION.md"
PREREGISTRATION_JSON = "docs/preregistration.json"
PREREGISTRATION_FILES = (PREREGISTRATION_MD, PREREGISTRATION_JSON)
SCHEMA = "norse-stack/preregistration/v1"

TRIAL = "trial"
BENCHMARK = "benchmark"
CONTROL = "control"
SENSITIVITY = "sensitivity"
HOLDOUT = "holdout"
KINDS = (TRIAL, BENCHMARK, CONTROL, SENSITIVITY, HOLDOUT)
HYPOTHESES = ("H1", "H2")

# What a caller may put in a run request. Everything else is refused (T16): the grid
# is fixed, so no request can carry a parameter.
REQUEST_KEYS = frozenset(
    {"configuration", "label", "seed", "sensitivity_total_cost_bps_per_side", "rerun",
     "reserve_reason"}
)
RERUN_KEYS = frozenset({"defect", "fix_commit", "section"})
# Fields of a registered grid configuration that name it rather than parameterise it.
_NAMING_KEYS = frozenset({"id", "label"})


class RegistrationError(ValueError):
    """The pre-registration is malformed, or a run does not resolve to it."""


class RunNotRegistered(RegistrationError, PermissionError):
    """A run request resolves to no registered configuration, or breaks a run rule."""


@dataclass(frozen=True)
class Window:
    hypothesis: str
    first_return_day: str
    last_return_day: str
    days: int

    def as_dict(self) -> dict:
        return {
            "first_return_day": self.first_return_day,
            "last_return_day": self.last_return_day,
            "days": self.days,
        }

    def day_list(self) -> list[str]:
        first = date.fromisoformat(self.first_return_day)
        return [(first + timedelta(days=i)).isoformat() for i in range(self.days)]


@dataclass(frozen=True)
class Resolved:
    """A run request resolved against the registration."""

    kind: str
    hypothesis: str
    configuration_id: str
    configuration: dict  # the fully resolved configuration, canonical-JSON ready
    configuration_sha256: str
    window: Window
    label: str | None
    rerun: dict | None
    reserve_reason: str | None
    strategy: str

    @property
    def cost_basis(self) -> dict:
        return dict(self.configuration["costs"])


class Registration:
    """``docs/preregistration.json`` as pinned by the genesis entry."""

    def __init__(self, doc: Mapping[str, Any], sha256: str) -> None:
        if doc.get("schema") != SCHEMA:
            raise RegistrationError(f"schema {doc.get('schema')!r}, expected {SCHEMA}")
        self.doc = doc
        self.sha256 = sha256
        windows = doc["out_of_sample_windows"]
        self.windows = {
            h: Window(h, w["first_return_day"], w["last_return_day"], int(w["days"]))
            for h, w in ((h, windows[h]) for h in HYPOTHESES)
        }
        held = windows[HOLDOUT]
        self.holdout_window = Window(
            HOLDOUT, held["first_return_day"], held["last_return_day"], int(held["days"])
        )
        for window in (*self.windows.values(), self.holdout_window):
            if len(window.day_list()) != window.days or window.day_list()[-1] != (
                window.last_return_day
            ):
                raise RegistrationError(f"{window.hypothesis}'s window does not add up")
        self.grid: dict[str, dict] = {}
        self.order: dict[str, list[str]] = {}
        for h in HYPOTHESES:
            ids = []
            for config in doc["hypotheses"][h]["configurations"]:
                if config["id"] in self.grid:
                    raise RegistrationError(f"{config['id']} is registered twice")
                self.grid[config["id"]] = config
                ids.append(config["id"])
            self.order[h] = ids
        self.benchmarks = {b["id"]: b for b in doc["benchmarks"]}
        self.controls = {c["id"]: c for c in doc["controls"]}
        budgets = doc["budgets"]
        self.own_budget = {h: int(budgets[f"{h}_max_trial_entries"]) for h in HYPOTHESES}
        self.reserve_budget = int(budgets["reserve_trial_entries"])
        self.total_budget = int(budgets["total_max_trial_entries"])
        if sum(self.own_budget.values()) + self.reserve_budget != self.total_budget:
            raise RegistrationError("the budgets do not add up to the registered total")
        rule = doc["n_counting_rule"]
        if rule["deduplicate_by_configuration_hash"] is not False:
            raise RegistrationError("the registered N must never deduplicate")
        self.excluded_from_n = frozenset(rule["excluded_from_N"])
        if self.excluded_from_n != {BENCHMARK, CONTROL, SENSITIVITY, HOLDOUT}:
            raise RegistrationError("this ledger counts trials only; the rule says otherwise")
        costs = doc["costs"]
        self.fee_bps = costs["fee_bps_per_side"]
        self.slippage_bps = costs["slippage_bps_per_side"]
        self.sensitivity_costs = tuple(doc["sensitivity"]["total_cost_bps_per_side"])
        self.development_manifest_sha256 = doc["data"]["development_manifest"]["sha256"]
        self.holdout_manifest_sha256 = doc["data"]["holdout_manifest"]["sha256"]

    @classmethod
    def from_bytes(cls, data: bytes) -> Registration:
        return cls(loads_strict(data), sha256_hex(data))

    @property
    def out_of_sample_start(self) -> str:
        """The first return day of any registered out-of-sample window."""
        return min(w.first_return_day for w in self.windows.values())

    def hypothesis_of(self, configuration_id: str) -> str:
        for h, ids in self.order.items():
            if configuration_id in ids:
                return h
        raise RunNotRegistered(f"{configuration_id} is not in the registered grid")

    # --- resolution -------------------------------------------------------------------

    def resolve(self, request: Mapping[str, Any], strategy) -> Resolved:
        """Resolve a run request with the strategy that will run it.

        ``strategy`` supplies ``module`` (the pinned path and function that runs it),
        ``parameters`` (the configuration keys it reads) and ``defaults`` (its defaults
        in code). The harness builds it from the strategy module committed at HEAD,
        never from the caller (security review M4).
        """
        unknown = set(request) - REQUEST_KEYS
        if unknown:
            raise RunNotRegistered(f"unknown request keys {sorted(unknown)}")
        cid = request.get("configuration")
        if not isinstance(cid, str):
            raise RunNotRegistered("a run names its registered configuration by id")
        label = request.get("label")
        if label is not None and not isinstance(label, str):
            raise RunNotRegistered("a label is text")
        seed = request.get("seed")
        cost = request.get("sensitivity_total_cost_bps_per_side")
        rerun = request.get("rerun")
        reserve_reason = request.get("reserve_reason")
        defaults = dict(strategy.defaults)
        undeclared = set(defaults) - set(strategy.parameters)
        if undeclared:
            raise RunNotRegistered(f"defaults for undeclared parameters {sorted(undeclared)}")

        if cid in self.grid:
            if seed is not None:
                raise RunNotRegistered("a grid configuration takes no seed")
            h = self.hypothesis_of(cid)
            parameters = self._parameters(self.grid[cid], strategy, defaults)
            if cost is None:
                kind, costs = TRIAL, self._costs(self.fee_bps + self.slippage_bps)
            else:
                if cost not in self.sensitivity_costs:
                    raise RunNotRegistered(
                        f"{cost} bps a side is not a registered sensitivity cost "
                        f"{list(self.sensitivity_costs)}"
                    )
                kind, costs = SENSITIVITY, self._costs(cost)
            extra = {"sensitivity_total_cost_bps_per_side": cost} if kind == SENSITIVITY else {}
        elif cid in self.benchmarks:
            if seed is not None or cost is not None:
                raise RunNotRegistered("a benchmark takes no seed or cost")
            h = self.benchmarks[cid]["window"].rsplit(".", 1)[-1]
            parameters = dict(sorted(defaults.items()))
            kind, costs, extra = BENCHMARK, self._costs(self.fee_bps + self.slippage_bps), {}
        elif cid in self.controls:
            control = self.controls[cid]
            if cost is not None:
                raise RunNotRegistered("a control takes no cost")
            first, last = control["seed_first"], control["seed_last"]
            if not isinstance(seed, int) or isinstance(seed, bool) or not first <= seed <= last:
                raise RunNotRegistered(f"{cid} needs a seed from {first} to {last}")
            base = control["base"]
            h = self.hypothesis_of(base)
            parameters = self._parameters(self.grid[base], strategy, defaults)
            kind, costs = CONTROL, self._costs(self.fee_bps + self.slippage_bps)
            extra = {"base": base}
        elif cid == HOLDOUT:
            raise RunNotRegistered(
                "the holdout run is resolved from the unseal entry, never from a request"
            )
        else:
            raise RunNotRegistered(f"{cid} is not a registered configuration")

        if kind != TRIAL and reserve_reason is not None:
            raise RunNotRegistered("only a trial carries a reserve reason")
        if rerun is not None:
            rerun = self._rerun(rerun)
        if reserve_reason is not None and (
            not isinstance(reserve_reason, str) or not reserve_reason.strip()
        ):
            raise RunNotRegistered("a reserve reason is non-empty text")

        window = self.windows[h]
        configuration = {
            "id": cid,
            "kind": kind,
            "hypothesis": h,
            "strategy": strategy.module,
            "parameters": parameters,
            "costs": costs,
            "window": window.as_dict(),
            "seed": seed,
            **extra,
        }
        return Resolved(
            kind=kind,
            hypothesis=h,
            configuration_id=cid,
            configuration=configuration,
            configuration_sha256=value_hash(configuration),
            window=window,
            label=label,
            rerun=rerun,
            reserve_reason=reserve_reason,
            strategy=strategy.module,
        )

    # --- the holdout run (G1.6; section 11, item 5) ---------------------------------

    def holdout_run_window(self, hypothesis: str) -> Window:
        """One simulation from the start of the hypothesis's development window
        through the holdout's last day, positions carried across the boundary."""
        development, holdout = self.windows[hypothesis], self.holdout_window
        after = date.fromisoformat(development.last_return_day) + timedelta(days=1)
        if after.isoformat() != holdout.first_return_day:
            raise RegistrationError(f"{hypothesis}'s window does not end where the holdout starts")
        return Window(
            HOLDOUT, development.first_return_day, holdout.last_return_day,
            development.days + holdout.days,
        )

    def holdout_configuration(
        self, candidate: Mapping, candidate_sha256: str, holdout_manifest_sha256: str
    ) -> dict:
        """The holdout run's resolved configuration: the candidate's, unchanged but
        for its kind and its window, with the candidate's configuration hash, which
        the unseal names, and the frozen holdout manifest's hash added."""
        if candidate.get("kind") != TRIAL or value_hash(candidate) != candidate_sha256:
            raise RunNotRegistered("the holdout run takes a trial configuration and its hash")
        h = candidate["hypothesis"]
        return {
            **candidate,
            "kind": HOLDOUT,
            "window": self.holdout_run_window(h).as_dict(),
            "candidate_sha256": candidate_sha256,
            "holdout_manifest_sha256": holdout_manifest_sha256,
        }

    def resolve_holdout(self, candidate_open: Mapping, holdout_manifest_sha256: str) -> Resolved:
        """The holdout run of the candidate whose open entry body is ``candidate_open``."""
        configuration = self.holdout_configuration(
            candidate_open["configuration"], candidate_open["configuration_sha256"],
            holdout_manifest_sha256,
        )
        h = configuration["hypothesis"]
        return Resolved(
            kind=HOLDOUT,
            hypothesis=h,
            configuration_id=configuration["id"],
            configuration=configuration,
            configuration_sha256=value_hash(configuration),
            window=self.holdout_run_window(h),
            label=None,
            rerun=None,
            reserve_reason=None,
            strategy=configuration["strategy"],
        )

    @staticmethod
    def _parameters(registered: Mapping, strategy, defaults: dict) -> dict:
        keys = set(registered) - _NAMING_KEYS
        unknown = keys - set(strategy.parameters)
        if unknown:
            raise RunNotRegistered(
                f"{registered['id']}: keys {sorted(unknown)} are not defined by {strategy.module}"
            )
        merged = dict(defaults)
        merged.update({k: registered[k] for k in keys})
        return dict(sorted(merged.items()))

    def _costs(self, total_bps_per_side: float) -> dict:
        return {
            "fee_bps_per_side": total_bps_per_side - self.slippage_bps,
            "slippage_bps_per_side": self.slippage_bps,
        }

    @staticmethod
    def _rerun(rerun: Any) -> dict:
        if not isinstance(rerun, Mapping) or set(rerun) != RERUN_KEYS:
            raise RunNotRegistered(f"a rerun record holds exactly {sorted(RERUN_KEYS)}")
        defect, fix, section = rerun["defect"], rerun["fix_commit"], rerun["section"]
        if not isinstance(defect, str) or not defect.strip():
            raise RunNotRegistered("a rerun names its defect")
        if not isinstance(fix, str) or not GIT_OBJECT_ID.fullmatch(fix):
            raise RunNotRegistered("a rerun names the full commit that fixes it")
        if not isinstance(section, str) or not section.strip():
            raise RunNotRegistered(
                "a rerun names the section of docs/PREREGISTRATION.md the code departed from"
            )
        return {"defect": defect, "fix_commit": fix, "section": section}
