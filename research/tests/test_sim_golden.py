"""The golden fixture (G1.9): the backtest and paper trading, given the same strategy
object and the same inputs, emit identical orders, fills, lapses, funding and returns,
and those equal outputs derived by hand from the pre-registration.

The fixture (fixtures/sim-golden.json) holds the input rows and the expected outputs.
The expected outputs were computed by straight-line arithmetic from the registered
rules, never from the engine. The derivation, at 10 bps fee and 2 bps slippage a side
(section 5), with equity starting at 1:

Weights scenario, 2021-03-01 to 2021-03-03, band 0.25 (section 6).

* 03-01 00:00, equity 1, all cash. Targets AAA 0.5 and BBB 0.3 are entries, which
  always trade: buy notionals 0.5 and 0.3. AAA's 00:00 bar has no volume, so it fills
  on 00:15 at VWAP 10100/100 = 101: price 101 x 1.0002 = 101.0202, quantity 0.5/101,
  fee 0.5 x 1.0002 x 0.001 = 0.0005001, at 00:30. BBB's 00:00 and 00:15 bars are
  missing, so it fills on 00:30 at VWAP 20: price 20.004, quantity 0.015, fee
  0.00030006, at 00:45 (section 5, Fills).
* 03-02 00:00 mark at closes 110 and 22: equity E1 = 1.0735942954... Targets AAA 0.85
  (current 0.5072, so buy (0.85 - 0.5072) x E1 = 0.36800...) and BBB 0.1 (current
  0.3074, outside the band: sell 0.22264...). AAA's buy at VWAP 110 would cost more
  than the cash left, 0.19903984, so it is scaled by cash / cost = 0.54021... and cash
  ends at zero (section 6, Orders). BBB has no bar with volume all day, so its sell
  lapses at 03-03 00:00.
* 03-03 00:00: BBB's sell lapses; then the mark, at AAA's close 120 and BBB's last
  close 21 (BBB still held); then BBB's end (section 4): its last daily bar was 03-02,
  so its 0.015 is sold at that bar's low 18 less slippage, 17.9964, fee 0.000269946,
  booked at the bar's close. The mark comes before anything stamped at its instant, so
  that loss falls in day 03-03's return, not 03-02's. Targets AAA 0 (an exit, which
  sells the whole position) and BBB 0.3, which is held at 0 because BBB has ended ("the
  slot holds cash"). AAA's exit fills at VWAP 121 x 0.9998.

Carry scenario, 2021-03-01 to 2021-03-02 (section 7).

* 03-01 00:00: carry at half of equity. The spot bar at 00:00 has volume but the
  perpetual's does not, and the spot's 00:15 bar is missing, so both legs fill together
  on 00:30 (section 5): q = 0.5 / 50000 = 1e-05, spot bought at 50000 x 1.0002, the
  perpetual sold at 50010 x 0.9998, a fee on each.
* Funding (section 5): the 00:00:00.004 settlement came before the fill, so nothing
  accrues on it. At 08:00:00.003 the book is short 1e-05 and receives 1e-05 x 50100 x
  0.0001, valued at the close of the 07:45 bar (the 08:00 bar closes after the
  settlement); at 16:00:00.001, 1e-05 x 50200 x 0.0002.
* 03-02 00:00 mark: spot 1e-05 x 50500 plus the short's 1e-05 x (49999.998 - 50520).
  The settlement at 00:00:00.002, after the mark, pays 1e-05 x 50300 x 0.0001 and
  counts in the second day. The exit sells 1e-05 spot at 50400 x 0.9998 and buys the
  perpetual back at 50410 x 1.0002, realising its loss against the entry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from norse_research.formats import Funding, Kline
from norse_research.sim import Costs, Market, Window, backtest, paper_run
from sim_example import Scripted

FIXTURE = Path(__file__).parent / "fixtures" / "sim-golden.json"
RELATIVE = 1e-12


def load():
    return json.loads(FIXTURE.read_text())


def rows_of(raw):
    rows = {}
    for name, items in raw.items():
        kind = Funding if name.startswith("um/fundingRate/") else Kline
        rows[name] = [kind(*item) for item in items]
    return rows


def run_both(scenario, strategy, costs):
    window = Window(**{
        "first_day": scenario["window"]["first_return_day"],
        "last_day": scenario["window"]["last_return_day"],
        "days": scenario["window"]["days"],
    })
    rows = rows_of(scenario["rows"])
    batch = backtest(strategy, Market.from_rows(rows, window.end), window, costs)
    paper = paper_run(strategy, rows, window, costs)
    return batch, paper


def assert_identical(batch, paper):
    assert batch.orders == paper.orders
    assert batch.fills == paper.fills
    assert batch.lapses == paper.lapses
    assert batch.accruals == paper.accruals
    assert batch.skips == paper.skips
    assert batch.marks == paper.marks
    assert batch.returns == paper.returns


def close(a, b):
    return a == pytest.approx(b, rel=RELATIVE, abs=1e-15)


def test_golden_weights_backtest_and_paper_emit_identical_orders():
    fixture = load()
    scenario = fixture["weights"]
    strategy = Scripted("1d", weights=scenario["decisions"], band=scenario["band"])
    batch, paper = run_both(scenario, strategy, Costs(**{
        "fee_bps": fixture["costs"]["fee_bps_per_side"],
        "slippage_bps": fixture["costs"]["slippage_bps_per_side"],
    }))
    assert_identical(batch, paper)
    expected = scenario["expected"]

    assert len(batch.orders) == len(expected["orders"])
    for order, want in zip(batch.orders, expected["orders"], strict=True):
        assert (order.decided_at, order.symbol, order.side) == (
            want["decided_at"], want["symbol"], want["side"])
        if "notional" in want:
            assert close(order.notional, want["notional"]) and order.quantity is None
        else:
            assert close(order.quantity, want["quantity"]) and order.notional is None

    assert len(batch.fills) == len(expected["fills"])
    for fill, want in zip(batch.fills, expected["fills"], strict=True):
        assert (fill.kind, fill.symbol, fill.at) == (want["kind"], want["symbol"], want["at"])
        for name in ("reference", "price", "quantity", "fee", "scale"):
            assert close(getattr(fill, name), want[name]), name

    assert [(lapse.order.symbol, lapse.at) for lapse in batch.lapses] == [
        (want["symbol"], want["at"]) for want in expected["lapses"]]
    assert batch.accruals == []
    assert all(close(e, w) for (_, e), w in zip(batch.marks, expected["marks"], strict=True))
    assert [d for d, _ in batch.returns] == ["2021-03-01", "2021-03-02", "2021-03-03"]
    assert all(close(r, w) for (_, r), w in zip(batch.returns, expected["returns"],
                                                 strict=True))


def test_golden_carry_backtest_and_paper_emit_identical_orders():
    fixture = load()
    scenario = fixture["carry"]
    decisions = {day: {s: tuple(v) for s, v in d.items()}
                 for day, d in scenario["decisions"].items()}
    strategy = Scripted("1d", carry=decisions)
    batch, paper = run_both(scenario, strategy, Costs(10, 2))
    assert_identical(batch, paper)
    expected = scenario["expected"]

    assert len(batch.orders) == len(expected["orders"])
    for order, want in zip(batch.orders, expected["orders"], strict=True):
        assert (order.decided_at, order.market, order.side) == (
            want["decided_at"], want["market"], want["side"])
        assert order.group == "carry:BTCUSDT"
        if "notional" in want:
            assert close(order.notional, want["notional"])
        elif "quantity" in want:
            assert close(order.quantity, want["quantity"])
        else:  # the perpetual leg of an entry sells what its spot leg buys
            assert order.notional is None and order.quantity is None

    assert len(batch.fills) == len(expected["fills"])
    for fill, want in zip(batch.fills, expected["fills"], strict=True):
        assert (fill.market, fill.at) == (want["market"], want["at"])
        for name in ("reference", "price", "quantity", "fee"):
            assert close(getattr(fill, name), want[name]), name

    assert len(batch.accruals) == len(expected["accruals"])
    for accrual, want in zip(batch.accruals, expected["accruals"], strict=True):
        assert accrual.calc_time == want["calc_time"]
        assert close(accrual.mark, want["mark"]) and close(accrual.amount, want["amount"])
    assert batch.lapses == []
    assert all(close(e, w) for (_, e), w in zip(batch.marks, expected["marks"], strict=True))
    assert all(close(r, w) for (_, r), w in zip(batch.returns, expected["returns"],
                                                 strict=True))
