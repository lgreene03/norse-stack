"""H2, its benchmark B-H2 and its control C-H2 (G2.3), on synthetic data only.

Each rule of sections 7 and 8 of docs/PREREGISTRATION.md is checked against a value
worked out by hand from the synthetic inputs, the control's shifts against the draws
numpy itself generated (fixtures/numpy-rng-vectors.json), and the strategy against both
drivers and against section 11, item 5's prefix rule. No market data is read and the
dataset store is never opened.
"""

from __future__ import annotations

import json
from collections import namedtuple
from pathlib import Path

import pytest
import strategy_synthetic as syn
from norse_research.formats import Funding
from norse_research.sim import (
    Costs,
    Market,
    SimulationError,
    Span,
    Window,
    backtest,
    paper_run,
)
from norse_research.strategies.h2 import carry

FIXTURES = Path(__file__).parent / "fixtures"
COSTS = Costs(10, 2)  # section 5: 10 bps fee plus 2 bps slippage a side
D = syn.DAY
FEB = syn.ms(2021, 2, 1)
SWITCH = syn.ms(2021, 2, 10)  # BTC's funding falls from 0.02% to 0.01% a settlement here


class Probe:
    """Runs a strategy and records each decision and, for a carry strategy that is not
    always on, the trailing funding F of each coin at that decision."""

    def __init__(self, inner):
        self.inner = inner
        self.interval = inner.interval
        self.decisions = []
        self.trailing = []

    def decide(self, history, book):
        decision = self.inner.decide(history, book)
        self.decisions.append((history.now, decision))
        if isinstance(self.inner, carry.Carry) and not self.inner.always_on:
            self.trailing.append((history.now, {
                coin: self.inner.trailing(history, coin, coin) for coin in self.inner.coins}))
        return decision


def flat_market(first_day=syn.H2_FIRST, days=52, fills_from=FEB, fills_days=21,
                btc_rate=lambda calc: 0.0002 if calc < SWITCH else 0.0001,
                eth_rate=lambda calc: 0.0001):
    """BTC and ETH at constant prices (spot and perpetual alike: BTC 30,000, ETH 1,000),
    with volume on every 15-minute bar of the fill days, and funding settled every eight
    hours from 2021-01-01, 5 ms after the hour."""
    rows = {}
    for coin, price, rate in (("BTCUSDT", 30000.0, btc_rate), ("ETHUSDT", 1000.0, eth_rate)):
        rows[f"spot/klines/{coin}/1d"] = syn.daily(first_day, [price] * days)
        rows[f"um/klines/{coin}/1d"] = syn.daily(first_day, [price] * days)
        rows[f"spot/klines/{coin}/15m"] = syn.fills(fills_from, fills_from + fills_days * D, price)
        rows[f"um/klines/{coin}/15m"] = syn.fills(fills_from, fills_from + fills_days * D, price)
        rows[f"um/fundingRate/{coin}"] = [
            Funding(first_day + k * 8 * syn.HOUR + 5, 8, rate(first_day + k * 8 * syn.HOUR))
            for k in range(days * 3)]
    return rows


def run_config(cid, rows, first="2021-02-01", last="2021-02-20", **kw):
    config = syn.configuration(cid, first, last, **kw)
    window = Window.from_configuration(config)
    probe = Probe(carry.strategy_for(config, rows))
    return probe, backtest(probe, Market.from_rows(rows, window.end), window, COSTS)


def spot_trades(result):
    return [(syn.iso(f.order.decided_at), f.symbol, f.side) for f in result.fills
            if f.market == "spot"]


# --- section 7: the rule, per registered configuration ----------------------------------


@pytest.mark.parametrize(("cid", "expected"), [
    # F at 2021-02-01: BTC 21 x 0.02% = 0.42%, ETH 21 x 0.01% = 0.21%. From 2021-02-10
    # BTC's settlements are 0.01%, three a day: at 2021-02-15 five of its 21 are still
    # 0.02% (F = 0.26%) and at 2021-02-16 two (F = 0.23%).
    ("H2-01", [("2021-02-01", "BTCUSDT", 1), ("2021-02-16", "BTCUSDT", -1)]),  # 0.24% both
    ("H2-02", [("2021-02-01", "BTCUSDT", 1), ("2021-02-01", "ETHUSDT", 1)]),  # 0.12%
    ("H2-03", []),  # 0.48%: BTC's 0.42% never enters
    ("H2-04", [("2021-02-01", "BTCUSDT", 1)]),  # exit only when F turns negative
    # Three days, nine settlements, 0.103%: BTC 0.18% enters; at 2021-02-12 two of nine
    # are 0.02% (F = 0.11%), at 2021-02-13 none (F = 0.09%); ETH's 0.09% never enters.
    ("H2-05", [("2021-02-01", "BTCUSDT", 1), ("2021-02-13", "BTCUSDT", -1)]),
    ("H2-06", [("2021-02-01", "BTCUSDT", 1), ("2021-02-01", "ETHUSDT", 1)]),  # always on
])
def test_each_registered_configuration_enters_and_exits_as_section_7_says(cid, expected):
    _, result = run_config(cid, flat_market())
    assert spot_trades(result) == expected


def test_the_trailing_sum_runs_over_the_settlements_of_the_registered_interval():
    # (t - 7 days + 60 s, t + 60 s]: at 2021-02-12 the 21 settlements from 2021-02-05
    # 08:00 to 2021-02-12 00:00, of which 2021-02-10 00:00 onwards (7) are 0.01%.
    probe, _ = run_config("H2-01", flat_market())
    trailing = dict(probe.trailing)
    assert trailing[syn.ms(2021, 2, 12)] == {
        "BTCUSDT": pytest.approx(14 * 0.0002 + 7 * 0.0001, rel=1e-12),
        "ETHUSDT": pytest.approx(21 * 0.0001, rel=1e-12)}


def test_the_carry_legs_are_equal_quantities_and_funding_accrues_per_settlement():
    # Section 7: q = 0.5 x equity / spot reference price, bought in spot and sold in the
    # perpetual on one bar; while short, each settlement pays q x mark x rate, the mark
    # being the latest perpetual 15-minute close (30,000): 0.5 x rate here. In carry from
    # the 00:15 close of 2021-02-01's first bar to that of 2021-02-16's, so the 45
    # settlements from 2021-02-01 08:00 to 2021-02-16 00:00 accrue.
    _, result = run_config("H2-01", flat_market())
    entry = [f for f in result.fills if f.order.decided_at == FEB]
    assert [(f.market, f.side) for f in entry] == [("spot", 1), ("perp", -1)]
    assert entry[0].quantity == entry[1].quantity == pytest.approx(0.5 / 30000.0, rel=1e-15)
    assert entry[0].scale == 1.0  # one coin enters: the cash scaling does not bind
    accruals = result.accruals
    assert len(accruals) == 45
    assert (syn.iso(accruals[0].calc_time), syn.iso(accruals[-1].calc_time)) == (
        "2021-02-01", "2021-02-16")
    for accrual in accruals:
        assert accrual.mark == 30000.0
        assert accrual.amount == pytest.approx(0.5 * accrual.rate, rel=1e-12)


def test_the_thresholds_are_the_rule_and_the_registered_figures_at_once():
    # Section 7: m x 0.48% x L / 28; the grid also registers the figures. A configuration
    # whose figures disagreed with its multiples would be refused.
    for config in syn.REGISTRATION.doc["hypotheses"]["H2"]["configurations"]:
        strategy = carry.Carry(syn.configuration(config["id"], "2021-02-01",
                                                 "2021-02-01")["parameters"])
        if config["always_on"]:
            assert strategy.always_on and strategy.entry is None
            continue
        assert (strategy.entry, strategy.exit) == (config["entry_threshold"],
                                                   config["exit_threshold"]), config["id"]
        assert strategy.lookback == config["lookback_days"]
    tampered = dict(syn.configuration("H2-01", "2021-02-01", "2021-02-01")["parameters"])
    tampered["entry_threshold"] = 0.0025
    with pytest.raises(SimulationError, match="differ from the registered"):
        carry.Carry(tampered)


# --- section 8, B-H2 ----------------------------------------------------------------------


def test_b_h2_buys_halves_at_the_first_decision_and_rebalances_at_month_ends():
    # From 2021-01-20, BTC and ETH spot at half of equity each. Both buys on one bar cost
    # 2 x 0.5 x 1.0002 x 1.001 > 1, so section 6's scaling applies one factor s =
    # 1 / (1.0002 x 1.001) and cash ends at zero. ETH's close rises from 1,000 to 1,500 on
    # 2021-01-25, so at 2021-02-01 equity is 0.5 s + 0.75 s and the weights are 0.4 and
    # 0.6: buy 0.1 x 1.25 s of BTC and sell as much ETH. No order in between.
    first = syn.ms(2021, 1, 1)
    rows = {
        "spot/klines/BTCUSDT/1d": syn.daily(first, [30000.0] * 40),
        "spot/klines/ETHUSDT/1d": syn.daily(first, [1000.0] * 24 + [1500.0] * 16),
        "spot/klines/BTCUSDT/15m": syn.fills(syn.ms(2021, 1, 20), syn.ms(2021, 2, 3), 30000.0),
        "spot/klines/ETHUSDT/15m": syn.fills(syn.ms(2021, 1, 20), syn.ms(2021, 1, 21), 1000.0)
        + syn.fills(FEB, syn.ms(2021, 2, 3), 1500.0),
    }
    probe, result = run_config("B-H2", rows, first="2021-01-20", last="2021-02-01")
    assert [syn.iso(t) for t, d in probe.decisions if d is not None] == [
        "2021-01-20", "2021-02-01"]
    s = 1 / (1.0002 * 1.001)
    opening = [f for f in result.fills if f.order.decided_at == syn.ms(2021, 1, 20)]
    assert [(f.symbol, f.quantity) for f in opening] == [
        ("BTCUSDT", pytest.approx(0.5 / 30000 * s, rel=1e-12)),
        ("ETHUSDT", pytest.approx(0.5 / 1000 * s, rel=1e-12))]
    rebalance = [(o.symbol, o.side, o.notional) for o in result.orders if o.decided_at == FEB]
    assert rebalance == [("BTCUSDT", 1, pytest.approx(0.125 * s, rel=1e-9)),
                         ("ETHUSDT", -1, pytest.approx(0.125 * s, rel=1e-9))]
    assert all(d.band == 0.0 and all(w == 0.5 for w in d.weights.values())
               for _, d in probe.decisions if d is not None)


# --- section 8, C-H2 ----------------------------------------------------------------------


def test_c_h2_shifts_are_numpys_draws():
    # k = rng.integers(540, M - 540, endpoint=True) on one generator, BTC first, with M =
    # 6,576 as G1.9 recorded: numpy's own draws for every committed seed.
    vectors = json.loads((FIXTURES / "numpy-rng-vectors.json").read_text())
    m = vectors["development_settlements_m"]
    for seed in vectors["seeds"]:
        shifts = carry.control_shifts({"ETHUSDT": m, "BTCUSDT": m}, seed["seed"])
        assert [shifts["BTCUSDT"], shifts["ETHUSDT"]] == seed["c_h2_k"], seed["seed"]


def test_c_h2_uses_the_value_at_i_plus_k_mod_m_at_each_settlement():
    series = {coin: [Funding(i * 8 * syn.HOUR, 8, (i + 1) * (1e-6 if coin == "BTCUSDT" else 2e-6))
                     for i in range(m)]
              for coin, m in (("BTCUSDT", 1200), ("ETHUSDT", 1100))}
    shifts = carry.control_shifts({c: len(s) for c, s in series.items()}, 7)
    shifted = carry.shifted_funding(series, 7)
    for coin, rows in series.items():
        k, m = shifts[coin], len(rows)
        assert 540 <= k <= m - 540
        assert [s.calc_time for s in shifted[coin]] == [r.calc_time for r in rows]
        assert [s.rate for s in shifted[coin]] == [rows[(i + k) % m].rate for i in range(m)]
    with pytest.raises(SimulationError, match="too few"):
        carry.control_shifts({"BTCUSDT": 1079, "ETHUSDT": 2000}, 0)


def test_c_h2_decides_on_the_shifted_series_and_accrues_the_actual_one():
    # A year of funding (1,140 settlements a coin, rising by a step each), a window of
    # two weeks. Each decision's F is the sum of the shifted rates over the registered
    # interval, recomputed here from k; every accrual is the actual rate at its time.
    first = syn.ms(2020, 1, 1)
    days = 380
    rows = flat_market(first_day=first, days=days, fills_from=syn.ms(2021, 1, 1),
                       fills_days=16, btc_rate=lambda c: 1e-4 + (c - first) / D * 1e-6,
                       eth_rate=lambda c: 3e-4 - (c - first) / D * 1e-6)
    config = syn.configuration("C-H2", "2021-01-01", "2021-01-14", seed=11)
    window = Window.from_configuration(config)
    probe = Probe(carry.strategy_for(config, rows))
    result = backtest(probe, Market.from_rows(rows, window.end), window, COSTS)
    actual = {c: rows[f"um/fundingRate/{c}"] for c in ("BTCUSDT", "ETHUSDT")}
    shifts = carry.control_shifts({c: len(a) for c, a in actual.items()}, 11)
    for t, trailing in probe.trailing:
        for coin, value in trailing.items():
            rates = actual[coin]
            inside = [i for i, s in enumerate(rates)
                      if t - 7 * D + 60_000 < s.calc_time <= t + 60_000]
            assert len(inside) == 21
            assert value == pytest.approx(sum(rates[(i + shifts[coin]) % len(rates)].rate
                                              for i in inside), rel=1e-12)
    by_time = {(s.calc_time, coin): s.rate for coin, a in actual.items() for s in a}
    assert result.accruals
    for accrual in result.accruals:
        assert accrual.rate == by_time[(accrual.calc_time, accrual.symbol)]


# --- both drivers, and section 11, item 5 -----------------------------------------------


@pytest.fixture(scope="module")
def market_rows():
    return syn.h2_market()


@pytest.mark.parametrize("cid", ["H2-01", "H2-05", "H2-06", "B-H2"])
def test_batch_and_paper_give_identical_orders(market_rows, cid):
    """The same strategy in the backtest and in paper trading: identical orders, fills,
    lapses, funding accruals, marks and returns (G1.9's two drivers; section 5)."""
    config = syn.configuration(cid, "2021-02-01", "2021-03-31")
    window = Window.from_configuration(config)
    batch = backtest(carry.strategy_for(config, market_rows),
                     Market.from_rows(market_rows, window.end), window, COSTS)
    paper = paper_run(carry.strategy_for(config, market_rows), market_rows, window, COSTS)
    for name in ("orders", "fills", "lapses", "accruals", "skips", "marks", "returns"):
        assert getattr(batch, name) == getattr(paper, name), name
    assert batch.orders and len(batch.returns) == 59


def test_the_synthetic_market_exercises_the_rules(market_rows):
    config = syn.configuration("H2-01", "2021-02-01", "2021-03-31")
    window = Window.from_configuration(config)
    result = backtest(carry.strategy_for(config, market_rows),
                      Market.from_rows(market_rows, window.end), window, COSTS)
    sides = {(f.symbol, f.side) for f in result.fills if f.market == "spot"}
    assert {("BTCUSDT", 1), ("BTCUSDT", -1)} <= sides, "BTC enters and exits"
    assert result.accruals, "funding accrues"
    assert any(f.bar_open != f.order.decided_at for f in result.fills), "a fill waits"


def test_both_legs_lapse_together_and_the_next_decision_re_evaluates():
    # Section 5: the legs fill on the first bar at which both have volume, or both lapse
    # at t + 24 hours and the coin's state is unchanged. BTC spot trades no volume on
    # 2021-02-01, so the entry decided then lapses on both legs and 2021-02-02 enters.
    rows = flat_market()
    rows["spot/klines/BTCUSDT/15m"] = [
        b._replace(volume=0.0) if b.open_time < FEB + D else b
        for b in rows["spot/klines/BTCUSDT/15m"]]
    _, result = run_config("H2-01", rows)
    assert spot_trades(result) == [("2021-02-02", "BTCUSDT", 1), ("2021-02-16", "BTCUSDT", -1)]
    assert [(lapse.order.market, syn.iso(lapse.at)) for lapse in result.lapses] == [
        ("spot", "2021-02-02"), ("perp", "2021-02-02")]
    assert result.lapses[0].order.group == result.lapses[1].order.group


@pytest.mark.parametrize("cid", ["H2-01", "H2-06", "B-H2"])
def test_a_longer_run_starts_with_the_shorter_runs_returns(market_rows, cid):
    """Section 11, item 5: a run to 2021-03-05 on data stopping there, and one to
    2021-04-05 on all of it; the first's returns are the second's first 33."""
    short = syn.configuration(cid, "2021-02-01", "2021-03-05")
    long = syn.configuration(cid, "2021-02-01", "2021-04-05")
    end = Window.from_configuration(short).end
    first = carry.simulate(short, Market.from_rows(syn.truncated(market_rows, end), end))
    second = carry.simulate(long, Market.from_rows(
        market_rows, Window.from_configuration(long).end))
    assert len(first) == 33 and second[:33] == first


# --- the seam, the guards and what each run loads ---------------------------------------


class FakeView:
    def __init__(self, rows):
        self.rows = rows
        self.loaded = set()

    def load(self, series, start, end, accept_recorded_gaps=False):
        assert accept_recorded_gaps is True  # section 5, Gaps
        self.loaded.add(series)
        lo, hi = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        key = (lambda r: r.calc_time) if "fundingRate" in series else (lambda r: r.open_time)
        return namedtuple("Loaded", "rows gaps")([r for r in self.rows[series]
                                                  if lo <= key(r) < hi], [])


def spans_of(rows):
    key = {n: (lambda r: r.calc_time) if "fundingRate" in n else (lambda r: r.open_time)
           for n in rows}
    return {n: Span(key[n](r[0]), key[n](r[-1])) for n, r in rows.items() if r}


def test_the_data_seam_is_closed_until_the_data_layer_supplies_spans(market_rows):
    config = syn.configuration("H2-01", "2021-02-01", "2021-02-05")
    with pytest.raises(SimulationError, match="data seam"):
        carry.run(config, FakeView(market_rows))


def test_each_configuration_loads_only_the_series_it_reads(market_rows):
    loads = {}
    for cid in ("H2-01", "B-H2"):
        config = syn.configuration(cid, "2021-02-01", "2021-02-05")
        view = FakeView(market_rows)
        market = carry.load_market(config, view, spans_of(market_rows))
        loads[cid] = sorted(market.series)
        assert len(carry.simulate(config, market)) == 5
    assert loads["H2-01"] == sorted(
        f"{kind}/{coin}{suffix}" for coin in ("BTCUSDT", "ETHUSDT")
        for kind, suffix in (("spot/klines", "/1d"), ("um/klines", "/1d"), ("um/fundingRate", "")))
    assert loads["B-H2"] == ["spot/klines/BTCUSDT/1d", "spot/klines/ETHUSDT/1d"]
    with pytest.raises(SimulationError, match="offers no"):
        carry.series_needed(syn.configuration("H2-01", "2021-02-01", "2021-02-05"),
                            ["spot/klines/BTCUSDT/1d", "um/fundingRate/BTCUSDT",
                             "um/fundingRate/ETHUSDT"])


def test_only_the_holdout_run_reads_past_the_development_period():
    past = syn.configuration("H2-01", "2025-12-01", "2026-01-01")
    with pytest.raises(SimulationError, match="at the latest"):
        carry.strategy_for(past, {})
    assert isinstance(carry.strategy_for({**past, "kind": "holdout"}, {}), carry.Carry)
    for cid in ("B-H2", "C-H2"):
        config = syn.configuration(cid, "2025-12-01", "2026-01-01",
                                   seed=0 if cid == "C-H2" else None)
        with pytest.raises(SimulationError, match="at the latest"):
            carry.strategy_for(config, {})


def test_a_configuration_runs_only_as_its_registered_kind():
    config = syn.configuration("H2-01", "2021-02-01", "2021-02-01")
    with pytest.raises(SimulationError, match="benchmark"):
        carry.strategy_for({**config, "id": "B-H2"}, {})
    with pytest.raises(SimulationError, match="trial, sensitivity or holdout"):
        carry.strategy_for({**config, "kind": "benchmark"}, {})
    with pytest.raises(SimulationError, match="not an H2 configuration"):
        carry.strategy_for({**config, "id": "H1-01"}, {})
