"""The resolved configuration and its hash (G1.5, threat model T16).

The hash covers everything that decides a run's result once its defaults are
applied, as canonical JSON with full SHA-256, so two different runs never look like
one and one run never looks like two.
"""

import inspect
import json

import pytest
from ledger_fixtures import ALL_PARAMETERS, strategy_source, synthetic_registration_bytes
from norse_research import harness
from norse_research.canonical import CanonicalError, dumps, loads_canonical, value_hash
from norse_research.harness import Strategy
from norse_research.registration import Registration, RunNotRegistered


def registration(data: bytes | None = None) -> Registration:
    return Registration.from_bytes(data or synthetic_registration_bytes("d" * 64))


def strategy(parameters=ALL_PARAMETERS, defaults=None) -> Strategy:
    return Strategy("synthetic", lambda c, d: [], frozenset(parameters), dict(defaults or {}))


H1_DEFAULTS = {"fill_reference": "vwap_15m", "bars_per_day_four_hour": 6}
WITH_DEFAULTS = ALL_PARAMETERS | set(H1_DEFAULTS)


def test_config_hash_covers_the_resolved_configuration():
    reg = registration()
    request = {"configuration": "H1-01"}
    base = reg.resolve(request, strategy(WITH_DEFAULTS, H1_DEFAULTS))
    changed = reg.resolve(
        request, strategy(WITH_DEFAULTS, {**H1_DEFAULTS, "fill_reference": "close"})
    )
    assert base.configuration["parameters"]["fill_reference"] == "vwap_15m"
    assert base.configuration_sha256 != changed.configuration_sha256
    # The costs, the window, the seed and the strategy are in it too.
    config = base.configuration
    assert config["costs"] == {"fee_bps_per_side": 10, "slippage_bps_per_side": 2}
    assert config["window"] == {"first_return_day": "2018-05-01", "last_return_day": "2025-12-31",
                                "days": 2802}
    assert config["seed"] is None and config["strategy"] == "synthetic"
    assert base.configuration_sha256 == value_hash(config)
    # A control's seed changes it; a label never does.
    seeds = {reg.resolve({"configuration": "C-H1", "seed": s}, strategy()).configuration_sha256
             for s in (0, 1)}
    assert len(seeds) == 2
    labelled = reg.resolve({"configuration": "H1-01", "label": "smoke"},
                           strategy(WITH_DEFAULTS, H1_DEFAULTS))
    assert labelled.configuration_sha256 == base.configuration_sha256


def _leaves(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, (*path, key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _leaves(item, (*path, i))
    yield path, value


def _changed(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, int | float):
        return value + 1
    if isinstance(value, str):
        return value + "x"
    if value is None:
        return 0
    if isinstance(value, list):
        return [*value, 0]
    return {**value, "added": 1}


def test_every_field_changes_the_hash():
    reg = registration()
    checked = 0
    for cid, extra in (("H1-07", {}), ("H2-05", {}), ("C-H2", {"seed": 7}), ("B-H1", {}),
                       ("H1-06", {"sensitivity_total_cost_bps_per_side": 15})):
        resolved = reg.resolve({"configuration": cid, **extra}, strategy(WITH_DEFAULTS, H1_DEFAULTS))
        config = resolved.configuration
        for path, value in _leaves(config):
            if not path:
                continue
            edited = json.loads(json.dumps(config))
            target = edited
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = _changed(value)
            assert value_hash(edited) != resolved.configuration_sha256, (cid, path)
            checked += 1
        for key in config:
            assert value_hash({k: v for k, v in config.items() if k != key}) != (
                resolved.configuration_sha256
            )
    assert checked > 60


def test_config_hash_is_canonical():
    reg = registration()
    base = reg.resolve({"configuration": "H1-01"}, strategy())
    # The same registration with H1-01's keys reversed and its numbers respelled.
    doc = json.loads(synthetic_registration_bytes("d" * 64))
    config = doc["hypotheses"]["H1"]["configurations"][0]
    doc["hypotheses"]["H1"]["configurations"][0] = dict(reversed(list(config.items())))
    text = json.dumps(doc, indent=1)
    text = text.replace('"vol_target_annual": 0.2', '"vol_target_annual": 2e-1', 1)
    text = text.replace('"max_fraction_of_slot": 1.0', '"max_fraction_of_slot": 1', 1)
    text = text.replace('"rebalance_band": 0.25', '"rebalance_band": 0.250', 1)
    assert text.count("2e-1") == 1 and text.count("0.250") == 1
    respelled = registration(text.encode()).resolve({"configuration": "H1-01"}, strategy())
    assert respelled.configuration_sha256 == base.configuration_sha256
    # The canonical form itself: one spelling per value.
    assert dumps({"b": 1.0, "a": [1e-3, -0.0, 2**60]}) == '{"a":[0.001,0,1152921504606846976],"b":1}'
    assert value_hash({"a": 1}) == value_hash({"a": 1.0})
    for text in ('{"a":1,"a":2}', "{\"a\": 1}", '{"b":1,"a":2}', '{"a":1.0}', '{"a":NaN}'):
        with pytest.raises(CanonicalError):
            loads_canonical(text)
    with pytest.raises(CanonicalError):
        dumps({"a": float("inf")})


def test_unknown_configuration_keys_are_refused():
    reg = registration()
    narrow = ALL_PARAMETERS - {"rebalance_band"}
    with pytest.raises(RunNotRegistered, match=r"\['rebalance_band'\] are not defined"):
        reg.resolve({"configuration": "H1-01"}, strategy(narrow))
    with pytest.raises(RunNotRegistered, match="unknown request keys"):
        reg.resolve({"configuration": "H1-01", "rebalance_band": 0.3}, strategy())
    with pytest.raises(RunNotRegistered, match="undeclared parameters"):
        reg.resolve({"configuration": "H1-01"}, strategy(ALL_PARAMETERS, {"hidden_knob": 1}))
    with pytest.raises(RunNotRegistered, match="names its registered configuration"):
        reg.resolve({"label": "no configuration"}, strategy())


def test_parameters_and_defaults_come_from_the_committed_module(world):
    """A changed default in code changes the hash through the harness itself, and only
    once committed: no caller can add a parameter or a default (review M4)."""
    signature = inspect.signature(harness.run).parameters
    assert not {"strategy", "parameters", "defaults"} & set(signature)
    first = harness.run(world.repo, {"configuration": "H1-01"}, store=world.store)
    assert first.open_entry.body["configuration"]["parameters"].get("fill_reference") is None
    implements = sorted(world.registration.order["H1"] + ["B-H1", "C-H1"])
    world.write(world.paths["H1"], strategy_source(implements, {"fill_reference": "vwap_15m"}))
    with pytest.raises(harness.RunRefused):
        harness.run(world.repo, {"configuration": "H1-01", "rerun": None}, store=world.store)
    fix = world.commit("a default in code")
    world.push()
    rerun = {"defect": "the fill reference was missing", "fix_commit": fix, "section": "5"}
    second = harness.run(world.repo, {"configuration": "H1-01", "rerun": rerun},
                         store=world.store)
    parameters = second.open_entry.body["configuration"]["parameters"]
    assert parameters["fill_reference"] == "vwap_15m"
    assert second.open_entry.body["configuration_sha256"] != (
        first.open_entry.body["configuration_sha256"]
    )
    with pytest.raises(RunNotRegistered, match="unknown request keys"):
        harness.run(world.repo, {"configuration": "H1-01", "extra_lookback": 3})
