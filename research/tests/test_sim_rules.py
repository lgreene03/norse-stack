"""Known answers for each rule the simulation engine implements (G1.9).

Every expected value is worked out by hand in the test from the wording of
docs/PREREGISTRATION.md (cited per test), never taken from the engine's output.
Synthetic data only.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from norse_research.formats import Funding, Kline
from norse_research.sim import (
    BUY,
    DAY_MS,
    PERP,
    SELL,
    SPOT,
    Book,
    Costs,
    Decision,
    History,
    Instrument,
    Market,
    Order,
    SimulationError,
    Window,
    backtest,
    carry_thresholds,
    month_of_decision,
    next_carry_state,
    paper_run,
    rank_top,
    rebalances,
    select_universe,
    signal,
    target_weight,
    trailing_funding,
    vote,
)
from norse_research.sim.execution import execute_bar, first_fill_bar, funding_mark, halt
from norse_research.sim.model import day_ms
from norse_research.universe import STABLE_OR_FIAT
from sim_example import Scripted
from test_sim_golden import rows_of

REPO = Path(__file__).resolve().parents[2]
PREREGISTRATION = json.loads((REPO / "docs" / "preregistration.json").read_text())
GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "sim-golden.json").read_text())
M15 = 900_000
HOUR = 3_600_000
T0 = day_ms("2021-03-01")


def kline(open_time, close, volume=100.0, quote=None, step=M15, low=None, high=None):
    quote = close * volume if quote is None else quote
    return Kline(open_time, close, close if high is None else high,
                 close if low is None else low, close, volume, open_time + step - 1, quote, 1,
                 volume / 2, quote / 2)


def approx(value):
    return pytest.approx(value, rel=1e-12, abs=1e-15)


# --- section 5, Costs -------------------------------------------------------------------


def test_slippage_moves_the_price_against_the_order_and_the_fee_is_on_the_execution_notional():
    costs = Costs(10, 2)
    # "the execution price is the reference price moved against the order by SlippageBps,
    # set to 2 bps": 100 x 1.0002 and 100 x 0.9998.
    assert costs.execution_price(100.0, BUY) == approx(100.02)
    assert costs.execution_price(100.0, SELL) == approx(99.98)
    # "The fee is 10 bps of the traded notional at the execution price": 2 x 100.02 x 0.001.
    assert costs.fee(2.0, 100.02) == approx(0.20004)


def test_a_round_trip_on_one_leg_costs_24_bps():
    # Section 5: "every fill ... costs 12 bps: 24 bps a round trip on one leg". One unit at
    # a reference of 100 both ways: pay 100.02 + 0.10002, receive 99.98 - 0.09998.
    costs = Costs(10, 2)
    book = Book(cash=1000.0)
    for side in (BUY, SELL):
        price = costs.execution_price(100.0, side)
        book.trade(SPOT, "XUSDT", side, 1.0, price, costs.fee(1.0, price))
    assert book.cash == approx(1000.0 - 0.24)
    assert book.spot == {}


def test_a_carry_round_trip_costs_48_bps():
    # Section 7: "c = 4 x 12 bps = 0.48% (both legs in and out)". At constant references of
    # 100: spot in -(100.02 + 0.10002), perpetual sold at 99.98 for a fee of 0.09998, spot
    # out +(99.98 - 0.09998), perpetual bought back at 100.02 for a fee of 0.10002 and a
    # realised 1 x (99.98 - 100.02) = -0.04. Total -0.48 on 100 of notional.
    costs = Costs(10, 2)
    book = Book(cash=1000.0)
    for market, side in ((SPOT, BUY), (PERP, SELL), (SPOT, SELL), (PERP, BUY)):
        price = costs.execution_price(100.0, side)
        book.trade(market, "XUSDT", side, 1.0, price, costs.fee(1.0, price))
    assert book.cash == approx(1000.0 - 0.48)
    assert book.perp == {} and book.spot == {}


def test_costs_are_the_registered_basis_and_sensitivity_keeps_slippage_at_2_bps():
    # preregistration.json: fee 10 and slippage 2 a side for every gate figure; section 8's
    # sensitivity runs at 7.5, 10 and 15 bps a side in total, "slippage staying at 2 bps and
    # the fee taking the rest", as norse_research.registration resolves them.
    registered = PREREGISTRATION["costs"]
    assert (registered["fee_bps_per_side"], registered["slippage_bps_per_side"]) == (10, 2)
    gate = Costs.from_configuration({"costs": {"fee_bps_per_side": 10,
                                               "slippage_bps_per_side": 2}})
    assert (gate.fee_bps, gate.slippage_bps) == (10, 2)
    for total, fee in zip(PREREGISTRATION["sensitivity"]["total_cost_bps_per_side"],
                          (5.5, 8, 13), strict=True):
        costs = Costs.from_configuration({"costs": {"fee_bps_per_side": total - 2,
                                                    "slippage_bps_per_side": 2}})
        assert costs.fee_bps == fee
        # Per unit at a reference of 100: slippage 0.02 plus the fee on 100.02.
        cost = costs.execution_price(100.0, BUY) - 100.0 + costs.fee(1.0, 100.02)
        assert cost == approx(0.02 + 100.02 * fee / 10_000)


# --- section 5, Fills -------------------------------------------------------------------


def test_a_fill_is_at_the_vwap_of_the_first_bar_with_volume_and_skips_missing_and_empty_bars():
    # "fills at the volume-weighted average price of the 15-minute bar that opens at t,
    # computed as the bar's quote-asset volume divided by its base volume. If that bar is
    # missing or has no volume, the next 15-minute bar with volume is used."
    bars = [kline(T0, 99.0, volume=0.0, quote=0.0),  # no volume
            # T0 + 15 min missing
            kline(T0 + 2 * M15, 123.0, volume=100.0, quote=12345.0),
            kline(T0 + 3 * M15, 124.0)]
    found = first_fill_bar({"s": bars}, T0, T0 + DAY_MS)
    assert found is not None
    bar_open, legs = found
    assert bar_open == T0 + 2 * M15
    assert legs["s"].quote_volume / legs["s"].volume == approx(123.45)


def test_an_order_lapses_when_no_bar_with_volume_opens_before_the_deadline():
    # "If no bar with volume opens before the next decision time ... the order lapses."
    bars = [kline(T0, 99.0, volume=0.0, quote=0.0), kline(T0 + DAY_MS, 99.0)]
    assert first_fill_bar({"s": bars}, T0, T0 + DAY_MS) is None


def test_two_legs_fill_together_on_the_first_bar_where_both_have_volume():
    # "on the first 15-minute bar at or after the decision at which both the spot and the
    # perpetual bar have volume". Spot trades at 00:00 but the perpetual has none; the spot
    # bar is missing at 00:15; both trade at 00:30.
    spot = [kline(T0, 100.0), kline(T0 + 2 * M15, 101.0)]
    perp = [kline(T0, 100.0, volume=0.0, quote=0.0), kline(T0 + M15, 100.5),
            kline(T0 + 2 * M15, 101.5)]
    bar_open, legs = first_fill_bar({"spot": spot, "perp": perp}, T0, T0 + DAY_MS)
    assert bar_open == T0 + 2 * M15
    assert (legs["spot"].close, legs["perp"].close) == (101.0, 101.5)


def test_a_position_changes_at_the_close_of_its_fill_bar_and_counts_in_that_marks_equity():
    # "A position changes at the close of its fill bar." The only bar with volume on
    # 03-01 opens at 23:45 and closes at 03-02 00:00, the mark: the fill counts in it.
    # Buy 0.5 of equity 1 at VWAP 100: quantity 0.005, price 100.02, fee 0.0005001, so
    # cash 1 - 0.5001 - 0.0005001 = 0.4993999; at the 03-02 mark (close 104) equity is
    # 0.4993999 + 0.005 x 104 = 1.0193999.
    rows = {
        "spot/klines/XUSDT/1d": [kline(T0 - (3 - i) * DAY_MS, c, step=DAY_MS)
                                 for i, c in enumerate([90.0, 95.0, 100.0, 104.0, 104.0])],
        "spot/klines/XUSDT/15m": [kline(T0, 99.0, volume=0.0, quote=0.0),
                                  kline(T0 + DAY_MS - M15, 100.0)],
    }
    window = Window("2021-03-01", "2021-03-02", 2)
    strategy = Scripted(weights={"2021-03-01": {"XUSDT": 0.5}})
    result = backtest(strategy, Market.from_rows(rows, window.end), window, Costs(10, 2))
    (fill,) = result.fills
    assert fill.at == T0 + DAY_MS and fill.quantity == approx(0.005)
    assert result.marks[1] == (T0 + DAY_MS, approx(1.0193999))
    assert paper_run(strategy, rows, window, Costs(10, 2)).marks == result.marks


# --- section 6, Orders: sells first, then buys scaled to the cash ------------------------


def test_sells_fill_before_buys_and_buys_are_scaled_so_cash_ends_at_zero():
    # Cash 0, one X held. On one bar: sell all X at VWAP 100 (price 99.98, fee 0.09998,
    # cash 99.88002), then buy Y for a notional of 200 at VWAP 50: 4 units at 50.01 plus a
    # fee of 0.20004 is 200.24004, more than the cash, so the buy is scaled by
    # 99.88002 / 200.24004 and cash ends at zero.
    book = Book(cash=0.0)
    book.spot["XUSDT"] = 1.0
    deadline = T0 + DAY_MS
    sell = Order(T0, SPOT, "XUSDT", SELL, None, 1.0, None, deadline)
    buy = Order(T0, SPOT, "YUSDT", BUY, 200.0, None, None, deadline)
    fills = execute_bar(book, Costs(10, 2), [(buy, kline(T0, 50.0)), (sell, kline(T0, 100.0))])
    assert [f.symbol for f in fills] == ["XUSDT", "YUSDT"]
    scale = 99.88002 / 200.24004
    assert fills[1].scale == approx(scale) and fills[1].quantity == approx(4 * scale)
    assert book.cash == pytest.approx(0.0, abs=1e-12)


def test_a_sell_never_takes_a_spot_position_short():
    # Section 6: "the book is never levered and never short". A resizing sell of notional
    # 150 at VWAP 100 asks for 1.5 units with 1 held: it sells the 1.
    book = Book(cash=0.0)
    book.spot["XUSDT"] = 1.0
    order = Order(T0, SPOT, "XUSDT", SELL, 150.0, None, None, T0 + DAY_MS)
    (fill,) = execute_bar(book, Costs(10, 2), [(order, kline(T0, 100.0))])
    assert fill.quantity == 1.0 and book.spot == {}


def test_a_carry_entry_scales_both_legs_together():
    # Section 6: "In H2 the same rule scales a coin's two legs together". Cash 0.5, buy
    # q = 1/100 spot at 100.02 (fee 0.0010002) and sell q perpetual at 99.98 (fee
    # 0.0009998): cost 1.0002 + 0.002 = 1.0022 > 0.5, so both legs get 0.5 / 1.0022.
    book = Book(cash=0.5)
    deadline = T0 + DAY_MS
    spot = Order(T0, SPOT, "XUSDT", BUY, 1.0, None, "carry:XUSDT", deadline)
    perp = Order(T0, PERP, "XUSDT", SELL, None, None, "carry:XUSDT", deadline)
    fills = execute_bar(book, Costs(10, 2), [(perp, kline(T0, 100.0)), (spot, kline(T0, 100.0))])
    scale = 0.5 / 1.0022
    assert [f.market for f in fills] == [SPOT, PERP]
    assert fills[0].quantity == approx(0.01 * scale) == fills[1].quantity
    assert book.cash == pytest.approx(0.0, abs=1e-12)
    assert book.carry == {"XUSDT": "XUSDT"}


# --- section 6, Rebalancing -------------------------------------------------------------


@pytest.mark.parametrize(("target", "current", "band", "trades"), [
    (0.2, 0.16, 0.25, False),  # |0.04| is not above 0.25 x 0.2 = 0.05
    (0.2, 0.14, 0.25, True),  # |0.06| is
    (0.2, 0.25, 0.25, False),  # |0.05| equals the band: "exceeds" is strict
    (0.0, 0.01, 0.25, True),  # exits always trade
    (0.1, 0.0, 0.25, True),  # entries always trade
    (0.0, 0.0, 0.25, False),  # nothing to do
    (0.2, 0.19, 0.0, True),  # a band of 0 (the benchmarks) trades any difference
])
def test_the_band_trades_only_when_the_difference_exceeds_the_band_times_the_target(
        target, current, band, trades):
    assert rebalances(target, current, band) is trades


def test_drift_inside_the_band_places_no_order():
    # The golden scenario's 03-02 weights are 0.5072 (AAA) and 0.3074 (BBB) of equity.
    # Targets of 0.5 and 0.3 differ by 0.0072 and 0.0074, inside 0.125 and 0.075.
    scenario = GOLDEN["weights"]
    rows = rows_of(scenario["rows"])
    window = Window("2021-03-01", "2021-03-02", 2)
    decisions = {"2021-03-01": {"AAAUSDT": 0.5, "BBBUSDT": 0.3},
                 "2021-03-02": {"AAAUSDT": 0.5, "BBBUSDT": 0.3}}
    result = backtest(Scripted(weights=decisions), Market.from_rows(rows, window.end), window,
                      Costs(10, 2))
    assert [o.decided_at for o in result.orders] == [T0, T0]


# --- section 6, Signal and Sizing -------------------------------------------------------


def test_votes_and_the_signal():
    # "the vote is 1 if the instrument's close at t divided by its close L days earlier,
    # minus one, is above zero"; "The signal s is the mean of the votes".
    assert (vote(110.0, 100.0), vote(100.0, 100.0), vote(90.0, 100.0)) == (1, 0, 0)
    assert signal([1, 1, 0]) == approx(2 / 3)
    assert signal([0, 0, 0]) == 0.0


def test_the_volatility_targeted_weight():
    # w* = (1/5) x s x min(1, 0.20 / sigma).
    assert target_weight(2 / 3, 5, 0.4, 0.2) == approx(0.2 * (2 / 3) * 0.5)
    assert target_weight(2 / 3, 5, 0.1, 0.2) == approx(0.2 * (2 / 3))  # capped at the slot
    assert target_weight(1.0, 5, 0.0, 0.2) == approx(0.2)  # constant prices: the cap binds
    # "The configuration without volatility scaling uses w* = (1/5) x s."
    assert target_weight(1 / 3, 5, None, None) == approx(0.2 / 3)


def test_the_exponentially_weighted_volatility_follows_the_pandas_adjusted_convention():
    # Closes 100, 110, 99, 99 give returns 0.1, -0.1 and 0. Span 3: alpha = 2/4 = 0.5 and
    # weights (1 - alpha)^j = 1, 0.5, 0.25 from the latest return back, so the mean square
    # is (0 + 0.5 x 0.01 + 0.25 x 0.01) / 1.75 = 0.0075 / 1.75; annualised by sqrt(365).
    rows = {"spot/klines/XUSDT/1d": [kline(T0 + i * DAY_MS, c, step=DAY_MS)
                                     for i, c in enumerate([100.0, 110.0, 99.0, 99.0])]}
    market = Market.from_rows(rows, T0 + 4 * DAY_MS)
    history = history_at(market, T0 + 4 * DAY_MS)
    instrument = history.instrument("XUSDT")
    sigma = history.ewm_volatility("spot/klines/XUSDT/1d", instrument, 3, 365)
    assert sigma == approx(math.sqrt(0.0075 / 1.75) * math.sqrt(365))
    # One return: the mean square is that return squared.
    early = history_at(market, T0 + 2 * DAY_MS)
    assert early.ewm_volatility("spot/klines/XUSDT/1d", instrument, 3, 365) == approx(
        0.1 * math.sqrt(365))


def history_at(market, now, ended=lambda instrument: False):
    def view(name):
        series = market.series.get(name)
        return series.view(now) if series else None

    def segments(name):
        return market.series[name].segments

    return History(now, "1d", view, segments, ended, {})


# --- section 4: instruments, halts and delistings ---------------------------------------


def test_a_missing_daily_bar_ends_an_instrument_and_later_bars_start_a_new_one():
    # "A missing daily bar ends the instrument, and later bars of the same symbol form a
    # new instrument with no history." Bars on days 0 to 2, none on 3, then 4 to 6.
    days = [0, 1, 2, 4, 5, 6]
    rows = {"spot/klines/XUSDT/1d": [kline(T0 + d * DAY_MS, 10.0 + d, step=DAY_MS)
                                     for d in days]}
    market = Market.from_rows(rows, T0 + 7 * DAY_MS)
    series = market.series["spot/klines/XUSDT/1d"]
    first = Instrument("XUSDT", T0)
    second = Instrument("XUSDT", T0 + 4 * DAY_MS)
    assert history_at(market, T0 + 3 * DAY_MS).instrument("XUSDT") == first
    assert history_at(market, T0 + 4 * DAY_MS).instrument("XUSDT") is None
    assert history_at(market, T0 + 5 * DAY_MS).instrument("XUSDT") == second
    assert series.instrument_end(first) == T0 + 3 * DAY_MS
    # The second runs to the data's horizon, so it has not ended.
    assert series.instrument_end(second) is None
    # Its volatility starts afresh: one return, (15 - 14) / 14.
    sigma = history_at(market, T0 + 6 * DAY_MS).ewm_volatility(
        "spot/klines/XUSDT/1d", second, 60, 365)
    assert sigma == approx(abs(15.0 / 14.0 - 1.0) * math.sqrt(365))


def test_a_halted_long_is_sold_at_its_last_days_low_less_slippage_and_fee():
    # D12: "closed at the low of the instrument's last daily bar, then charged slippage and
    # the fee as any sell". 3 units, low 8: price 8 x 0.9998 = 7.9984, fee 3 x 7.9984 x
    # 0.001 = 0.0239952, so cash rises by 23.9952 - 0.0239952 = 23.9712048.
    book = Book(cash=0.0)
    book.spot["XUSDT"] = 3.0
    last = kline(T0, 9.0, low=8.0, high=11.0, step=DAY_MS)
    fill = halt(book, Costs(10, 2), "XUSDT", last, T0 + DAY_MS)
    assert (fill.kind, fill.reference, fill.at) == ("halt", 8.0, T0 + DAY_MS)
    assert fill.price == approx(7.9984) and fill.fee == approx(0.0239952)
    assert book.cash == approx(23.9712048) and book.spot == {}


def test_a_position_open_at_the_end_of_the_window_is_marked_and_never_closed():
    # Section 3: "Positions open at the end of a window are marked at the last close and
    # never closed, so no exit cost is charged." BBB's data ends exactly at the window's
    # end here, so it has not stopped trading and is not halted.
    scenario = GOLDEN["weights"]
    rows = rows_of(scenario["rows"])
    window = Window("2021-03-01", "2021-03-02", 2)  # ends at 03-03 00:00, BBB's last close
    decisions = {"2021-03-01": {"AAAUSDT": 0.5, "BBBUSDT": 0.3}}
    result = backtest(Scripted(weights=decisions), Market.from_rows(rows, window.end), window,
                      Costs(10, 2))
    assert [f.kind for f in result.fills] == ["fill", "fill"]
    # Equity at 03-03 00:00: the golden cash 0.19903984 plus AAA at 120 and BBB at 21.
    q_aaa, q_bbb = 0.5 / 101, 0.015
    assert result.marks[-1] == (window.end, approx(0.19903984 + q_aaa * 120 + q_bbb * 21))


def test_a_carry_pair_whose_spot_stops_trading_stops_the_run():
    book = Book(cash=0.0)
    book.spot["XUSDT"] = 1.0
    book.carry["XUSDT"] = "XUSDT"
    with pytest.raises(SimulationError, match="not registered"):
        halt(book, Costs(10, 2), "XUSDT", kline(T0, 1.0, step=DAY_MS), T0 + DAY_MS)


# --- section 5, Funding -----------------------------------------------------------------


def test_funding_is_received_by_a_short_and_paid_when_the_rate_is_negative():
    # Section 7: "the book receives q times the funding mark times the rate, and pays it
    # when the rate is negative". Short 2 at a mark of 50000: 2 x 50000 x 0.0001 = 10.
    for quantity, rate, amount in ((-2.0, 0.0001, 10.0), (-2.0, -0.0001, -10.0),
                                   (2.0, 0.0001, -10.0)):
        book = Book(cash=0.0)
        book.trade(PERP, "XUSDT", BUY if quantity > 0 else SELL, abs(quantity), 50000.0, 0.0)
        assert book.accrue("XUSDT", rate, 50000.0) == (quantity, approx(amount))
        assert book.cash == approx(amount)


def test_the_funding_mark_is_the_latest_15_minute_close_at_or_before_the_settlement():
    # "valued at the close of the latest perpetual 15-minute bar at or before it". The
    # 07:45 bar closes at 08:00, the 08:00 bar at 08:15.
    bars = [kline(T0 + 7 * HOUR + 30 * 60_000, 1.0), kline(T0 + 7 * HOUR + 45 * 60_000, 2.0),
            kline(T0 + 8 * HOUR, 3.0)]
    assert funding_mark(bars, T0 + 8 * HOUR + 3) == 2.0  # 3 ms of jitter after the hour
    assert funding_mark(bars, T0 + 8 * HOUR) == 2.0  # closed exactly then
    assert funding_mark(bars, T0 + 8 * HOUR - 2) == 1.0  # 07:45 has not closed yet


def eight_hourly(first, n, rate=lambda k: 0.0001 * (k + 1), jitter=2, interval=8):
    return [Funding(first + k * interval * HOUR + jitter, interval, rate(k)) for k in range(n)]


def test_trailing_funding_sums_the_settlements_in_the_registered_interval():
    # Section 7: calc_time in (t - 7 days + 60 s, t + 60 s], "which holds exactly 21
    # settlements for a coin settling every eight hours". Settlements every 8 h from day 0
    # (rate 0.0001 x (k + 1)), t = day 10: in are k = 9 to 30 less the one at t - 7 days
    # (k = 9), so k = 10 to 30, summing 0.0001 x (11 + ... + 31) = 0.0001 x 441.
    settlements = eight_hourly(T0, 40)
    t = T0 + 10 * DAY_MS
    window = trailing_funding(settlements, t, 7)
    assert (window.count, window.covered) == (21, True)
    assert window.total == approx(0.0441)
    # With the settlement at t replaced by one at t + 61 s, it is not yet known; at t + 59 s
    # it is.
    late = [*settlements[:30], Funding(t + 61_000, 8, 1.0)]
    assert trailing_funding(late, t, 7).total == approx(0.0441 - 0.0031)
    known = [*settlements[:30], Funding(t + 59_000, 8, 1.0)]
    assert trailing_funding(known, t, 7).total == approx(0.0441 - 0.0031 + 1.0)


def test_trailing_funding_reports_a_window_with_a_gap_or_a_short_history_as_uncovered():
    # Section 5, Gaps item 5: "the H1 funding filter does not apply while its trailing
    # window holds a recorded funding gap"; section 6: nor where the settlements "do not
    # cover the seven days".
    settlements = eight_hourly(T0, 40)
    t = T0 + 10 * DAY_MS
    gapped = settlements[:20] + settlements[21:]
    assert (trailing_funding(gapped, t, 7).count, trailing_funding(gapped, t, 7).covered) == (
        20, False)
    young = eight_hourly(T0 + 5 * DAY_MS, 20)  # began 5 days before t
    assert trailing_funding(young, t, 7).covered is False
    stopped = settlements[:28]  # last at t - 16 h
    assert trailing_funding(stopped, t, 7).covered is False
    # Starting exactly one interval inside the window still covers it: all 21 are there.
    exact = eight_hourly(t - 7 * DAY_MS + 8 * HOUR - 2, 21)
    assert (trailing_funding(exact, t, 7).count, trailing_funding(exact, t, 7).covered) == (
        21, True)
    # Four-hourly settlements: 42 in seven days.
    four = eight_hourly(T0, 80, interval=4)
    assert (trailing_funding(four, t, 7).count, trailing_funding(four, t, 7).covered) == (
        42, True)


def test_the_carry_thresholds_are_the_registered_ones():
    # Section 7: m x c x L / 28 with c = 0.48%; the H2 grid lists the results.
    for configuration in PREREGISTRATION["hypotheses"]["H2"]["configurations"]:
        if configuration["always_on"]:
            continue
        entry, exit_ = carry_thresholds(configuration["entry_multiple"],
                                        configuration["exit_multiple"],
                                        configuration["round_trip_cost"],
                                        configuration["lookback_days"])
        assert entry == approx(configuration["entry_threshold"]), configuration["id"]
        assert exit_ == approx(configuration["exit_threshold"]), configuration["id"]
    assert carry_thresholds(2, 2, 0.0048, 7) == (approx(0.0024), approx(0.0024))


@pytest.mark.parametrize(("in_carry", "trailing", "after"), [
    (False, 0.0025, True), (False, 0.0024, False),  # enters only above the entry threshold
    (True, 0.0023, False), (True, 0.0024, True),  # exits only below the exit threshold
])
def test_a_coin_enters_above_the_entry_threshold_and_exits_below_the_exit_threshold(
        in_carry, trailing, after):
    assert next_carry_state(in_carry, trailing, 0.0024, 0.0024) is after


def test_a_missing_day_of_perpetual_klines_lapses_both_legs_and_carries_the_mark_forward():
    # Section 5, Gaps item 6 (the holdout's 2026-10-28), on the golden carry scenario with
    # 03-02's perpetual klines removed. In carry from 03-01 (golden: q = 1e-05 short at
    # 49999.998, cash 0.49889990002 after the entry). The 03-02 exit finds no bar with both
    # legs before 03-03 00:00, so both legs lapse and the coin stays in carry. Funding still
    # accrues, valued at the latest 15-minute close, 03-01 23:45's 50300: on 03-02 at
    # 00:00:00.002, -0.0001, and 08:00:00.001, +0.0001. The 03-03 mark carries the
    # perpetual's last daily close, 50520, forward.
    scenario = GOLDEN["carry"]
    raw = {name: list(items) for name, items in scenario["rows"].items()}
    d2 = T0 + DAY_MS
    raw["um/klines/BTCUSDT/1d"] = [r for r in raw["um/klines/BTCUSDT/1d"] if r[0] != d2]
    raw["um/klines/BTCUSDT/15m"] = [r for r in raw["um/klines/BTCUSDT/15m"]
                                    if not d2 <= r[0] < d2 + DAY_MS]
    rows = rows_of(raw)
    window = Window("2021-03-01", "2021-03-02", 2)
    strategy = Scripted(carry={day: {s: tuple(v) for s, v in d.items()}
                               for day, d in scenario["decisions"].items()})
    result = backtest(strategy, Market.from_rows(rows, window.end), window, Costs(10, 2))
    assert paper_run(strategy, rows, window, Costs(10, 2)).fills == result.fills
    assert [(lapse.order.market, lapse.at) for lapse in result.lapses] == [
        (SPOT, window.end), (PERP, window.end)]
    q, entry, cash = 1e-05, 49999.998, 0.49889990002
    a1, a2 = q * 50100 * 0.0001, q * 50200 * 0.0002
    a3, a4 = q * 50300 * -0.0001, q * 50300 * 0.0001
    assert [a.mark for a in result.accruals] == [50100, 50200, 50300, 50300]
    assert result.accruals[3].amount == approx(a4)
    e2 = cash + a1 + a2 + a3 + a4 + q * 50450 - q * (50520 - entry)
    assert result.marks[-1] == (window.end, approx(e2))


# --- section 6, The universe (D13, D14) -------------------------------------------------


def test_the_exclusions_are_the_registered_ones():
    exclusions = PREREGISTRATION["exclusions"]
    assert set(STABLE_OR_FIAT) == set(exclusions["removed_before_ranking"])
    from norse_research.sim import REGISTERED_EXCLUSIONS
    assert REGISTERED_EXCLUSIONS == {
        base for bases in exclusions["classified_candidates"].values() for base in bases}


def test_ranking_sums_the_30_daily_bars_closing_at_the_month_end_without_stablecoins():
    # 2021-03's month end closes at 2021-04-01 00:00: its 30 bars open 03-02 to 03-31. A
    # bar on 03-01 is outside. USDC is removed before ranking. AAA and BBB tie at 300 and
    # break by symbol.
    first = day_ms("2021-03-02")
    volumes = {
        "USDCUSDT": {first + d * DAY_MS: 1e9 for d in range(30)},
        "AAAUSDT": {first + d * DAY_MS: 10.0 for d in range(30)},
        "BBBUSDT": {first + d * DAY_MS: 10.0 for d in range(30)},
        "CCCUSDT": {first - DAY_MS: 1e6, first: 200.0},
        "DDDUSDT": {first + 29 * DAY_MS: 400.0},
    }
    assert rank_top(volumes, "2021-03") == ["DDDUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT"]
    assert rank_top(volumes, "2021-03", depth=2) == ["DDDUSDT", "AAAUSDT"]


def test_the_universe_is_the_first_five_eligible_of_the_ranked_ten():
    # At the 2021-03 month end (decision 2021-04-01 00:00), from ranked symbols in order:
    # PAXG is excluded (D14); YNG's first close is 119 days before the close (D13: it needs
    # one 120 days before); END's last bar is 03-20, so its instrument lacks the month-end
    # bar; RES returned on 03-10 as a new, young instrument; EDG's first bar opens 121 days
    # before, so it closes exactly 120 days before and is eligible; LST's last bar is the
    # month-end bar itself and stays eligible (the selection looks no bar ahead). So the
    # five are AAA, EDG, LST, BBB, CCC; DDD is not reached.
    close = day_ms("2021-04-01")
    long_ago = close - 400 * DAY_MS

    def daily(first, last):
        return [kline(t, 1.0, step=DAY_MS) for t in range(first, last + 1, DAY_MS)]

    month_end = close - DAY_MS
    rows = {
        "spot/klines/PAXGUSDT/1d": daily(long_ago, month_end),
        "spot/klines/AAAUSDT/1d": daily(long_ago, month_end),
        "spot/klines/YNGUSDT/1d": daily(close - 120 * DAY_MS, month_end),
        "spot/klines/ENDUSDT/1d": daily(long_ago, day_ms("2021-03-20")),
        "spot/klines/RESUSDT/1d": daily(long_ago, day_ms("2021-03-05"))
        + daily(day_ms("2021-03-10"), month_end),
        "spot/klines/EDGUSDT/1d": daily(close - 121 * DAY_MS, month_end),
        "spot/klines/LSTUSDT/1d": daily(long_ago, month_end),
        "spot/klines/BBBUSDT/1d": daily(long_ago, month_end),
        "spot/klines/CCCUSDT/1d": daily(long_ago, month_end),
        "spot/klines/DDDUSDT/1d": daily(long_ago, month_end),
    }
    market = Market.from_rows(rows, close + DAY_MS)
    ranked = ["PAXGUSDT", "AAAUSDT", "YNGUSDT", "ENDUSDT", "RESUSDT", "EDGUSDT", "LSTUSDT",
              "BBBUSDT", "CCCUSDT", "DDDUSDT"]
    universe = select_universe(history_at(market, close), ranked)
    assert [i.symbol for i in universe] == ["AAAUSDT", "EDGUSDT", "LSTUSDT", "BBBUSDT",
                                            "CCCUSDT"]
    assert universe[1] == Instrument("EDGUSDT", close - 121 * DAY_MS)
    # LST stopped at this close: the engine then holds its slot in cash (section 4).
    assert market.series["spot/klines/LSTUSDT/1d"].instrument_end(universe[2]) == close
    assert month_of_decision(close) == "2021-03"
    with pytest.raises(SimulationError):
        month_of_decision(close + DAY_MS)


# --- section 3, the windows -------------------------------------------------------------


@pytest.mark.parametrize("hypothesis", ["H1", "H2"])
def test_the_registered_windows_mark_every_day_and_take_no_decision_at_2026(hypothesis):
    registered = PREREGISTRATION["out_of_sample_windows"][hypothesis]
    window = Window.from_configuration({"window": registered})
    assert len(window.marks()) == registered["days"] + 1
    daily = window.decisions("1d")
    assert len(daily) == registered["days"]
    assert daily[0] == day_ms(registered["first_return_day"])
    assert daily[-1] == day_ms(registered["last_decision_daily"][:10])
    # "No development run takes a decision at 2026-01-01 00:00."
    assert window.end == day_ms("2026-01-01") and window.end not in daily
    if "last_decision_four_hour" in registered:
        four = window.decisions("4h")
        assert four[-1] == day_ms("2025-12-31") + 20 * HOUR
        assert len(four) == 6 * registered["days"]


def test_a_window_through_the_holdout_is_taken_as_given():
    # Section 11, item 5: the holdout run simulates from the development window's start
    # through 2026-10-31 (2802 + 304 days for H1); the engine assumes no end date.
    window = Window("2018-05-01", "2026-10-31", 3106)
    assert window.end == day_ms("2026-11-01")
    with pytest.raises(SimulationError):
        Window("2018-05-01", "2026-10-31", 3105)


def test_a_spot_pair_maps_to_its_perpetual_including_the_thousand_coin_contracts():
    # Section 4 names 1000PEPEUSDT, 1000SHIBUSDT, 1000FLOKIUSDT and 1000LUNCUSDT; every
    # other perpetual has the spot pair's own symbol. The perpetuals are the funding series
    # the development manifest pins.
    from norse_research.sim import perpetual_of

    manifest = json.loads((REPO / "research" / "manifests"
                           / "binance-vision-development.json").read_text())
    perps = [n.split("/")[2] for n in manifest["datasets"] if n.startswith("um/fundingRate/")]
    for coin in ("PEPE", "SHIB", "FLOKI", "LUNC"):
        assert perpetual_of(f"{coin}USDT", perps) == f"1000{coin}USDT"
    assert perpetual_of("BTCUSDT", perps) == "BTCUSDT"
    assert perpetual_of("1000SATSUSDT", perps) == "1000SATSUSDT"
    assert perpetual_of("ETHBULLUSDT", perps) is None


# --- section 3 and section 11, item 5: the development/holdout boundary ------------------


def test_nothing_stamped_on_1_january_moves_the_31_december_return():
    """Quant review 1, finding 1. A carry position held over the 31 December close, a
    funding settlement stamped exactly 2026-01-01 00:00:00.000 (as 44 of 72 development
    month starts are) and a pair whose last daily bar is 31 December. A development run's
    data stops at the boundary; a holdout run's carries on. The mark comes before the
    settlement and the delisting stamped at its instant, so the holdout run's returns to
    31 December equal the development run's exactly, as the harness requires (section
    11, item 5), and both events fall in 1 January's return. Synthetic rows only."""
    dec20, jan1 = day_ms("2025-12-20"), day_ms("2026-01-01")
    aug1 = day_ms("2025-08-01")
    rows = {
        "spot/klines/BTCUSDT/1d": [kline(dec20 + i * DAY_MS, 40_000.0 + 150 * i, step=DAY_MS)
                                   for i in range(14)],  # to 2026-01-02
        "um/klines/BTCUSDT/1d": [kline(dec20 + i * DAY_MS, 40_020.0 + 150 * i, step=DAY_MS)
                                 for i in range(14)],
        # 2025-08-01 to 2025-12-31: the pair stops after the 31 December bar.
        "spot/klines/XXXUSDT/1d": [kline(aug1 + i * DAY_MS, 10.0 + 0.01 * i, step=DAY_MS,
                                         low=9.0) for i in range(153)],
        "spot/klines/BTCUSDT/15m": [kline(day_ms("2025-12-29"), 40_000.0)],
        "spot/klines/XXXUSDT/15m": [kline(day_ms("2025-12-30"), 11.5)],
        "um/klines/BTCUSDT/15m": sorted(
            [kline(day_ms("2025-12-29"), 40_030.0)]
            + [kline(dec20 + k * 8 * HOUR - M15, 40_100.0 + k) for k in range(1, 42)]),
        # Every eight hours, 3 ms after the hour, except exactly on 1 January's midnight.
        "um/fundingRate/BTCUSDT": [
            Funding(dec20 + k * 8 * HOUR + (0 if dec20 + k * 8 * HOUR == jan1 else 3), 8,
                    0.01 if dec20 + k * 8 * HOUR == jan1 else 0.0001)
            for k in range(42)],
    }
    strategy = Scripted(weights={"2025-12-30": {"XXXUSDT": 0.3}},
                        carry={"2025-12-29": {"BTCUSDT": ("BTCUSDT", 0.4)}})
    costs = Costs(10, 2)
    development = Window("2025-12-29", "2025-12-31", 3)
    before = {name: [r for r in series if r[0] < jan1] for name, series in rows.items()}
    dev = backtest(strategy, Market.from_rows(before, development.end), development, costs)
    holdout = Window("2025-12-29", "2026-01-02", 5)
    held = backtest(strategy, Market.from_rows(rows, holdout.end), holdout, costs)

    assert held.returns[:3] == dev.returns  # exactly, as section 11, item 5 requires
    assert [d for d, _ in dev.returns] == ["2025-12-29", "2025-12-30", "2025-12-31"]
    # Both boundary events happen, and only in the run whose data reaches them.
    assert [(f.kind, f.symbol, f.at) for f in held.fills if f.kind == "halt"] == [
        ("halt", "XXXUSDT", jan1)]
    assert [a.amount > 0 for a in held.accruals if a.calc_time == jan1] == [True]
    assert not any(f.kind == "halt" for f in dev.fills)
    assert not any(a.calc_time >= jan1 for a in dev.accruals)
    # They land in 1 January's return: its mark at 2026-01-01 00:00 is the same in both.
    assert held.marks[3] == dev.marks[3]
    assert paper_run(strategy, rows, holdout, costs).returns == held.returns


def test_a_pair_whose_last_bar_is_the_month_end_bar_is_selected_and_its_slot_held_in_cash():
    """Choice 12 (quant review 1, finding 2; kept by the lead's decision). Section 6,
    step 2 asks only whether the current instrument "has the month-end daily bar", so a
    pair whose last bar is the month-end bar is still selected, and section 4 then holds
    its slot in cash for the month: the next pair does not take its place, which would
    need a look-ahead beyond D12's."""
    close = day_ms("2021-04-01")
    first = close - 400 * DAY_MS

    def daily(last_open):
        return [kline(t, 10.0, step=DAY_MS) for t in range(first, last_open + 1, DAY_MS)]

    rows = {
        "spot/klines/LSTUSDT/1d": daily(close - DAY_MS),  # last bar: the month-end bar
        "spot/klines/AAAUSDT/1d": daily(close),
        "spot/klines/BBBUSDT/1d": daily(close),
        "spot/klines/AAAUSDT/15m": [kline(close, 10.0)],
        "spot/klines/BBBUSDT/15m": [kline(close, 10.0)],
    }
    chosen = []

    class TwoSlots:
        interval = "1d"

        def decide(self, history, book):
            universe = select_universe(history, ["LSTUSDT", "AAAUSDT", "BBBUSDT"], slots=2)
            chosen.append((universe, [history.ended(i) for i in universe]))
            return Decision(weights={i: 0.5 for i in universe}, band=0.0)

    window = Window("2021-04-01", "2021-04-01", 1)
    result = backtest(TwoSlots(), Market.from_rows(rows, window.end), window, Costs(10, 2))
    assert chosen == [([Instrument("LSTUSDT", first), Instrument("AAAUSDT", first)],
                       [True, False])]
    assert [(o.symbol, o.side, o.notional) for o in result.orders] == [("AAAUSDT", BUY, 0.5)]
    # LST's half of equity stays in cash: 1 - 0.5 x 1.0002 x 1.001.
    assert result.marks[-1][1] == approx(1 - 0.5 * 1.0002 * 1.001 + 0.05 * 10.0)
