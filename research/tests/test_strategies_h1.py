"""H1, its benchmark B-H1 and its control C-H1 (G2.2), on synthetic data only.

Each rule of sections 6 and 8 of docs/PREREGISTRATION.md is checked against a value
worked out by hand from the synthetic inputs (the arithmetic is in each test, never
the engine's code), the controls' random numbers against the vectors numpy itself
generated (fixtures/numpy-rng-vectors.json), and the strategy against both drivers and
against section 11, item 5's rule that a longer run's returns start with a shorter
run's. No market data is read and the dataset store is never opened.
"""

from __future__ import annotations

import json
import math
from collections import namedtuple
from pathlib import Path

import pytest
import strategy_synthetic as syn
from norse_research.formats import Funding
from norse_research.sim import (
    Costs,
    Instrument,
    Market,
    SeriesView,
    SimulationError,
    Span,
    Window,
    backtest,
    paper_run,
)
from norse_research.strategies.h1 import trend

FIXTURES = Path(__file__).parent / "fixtures"
COSTS = Costs(10, 2)  # section 5: 10 bps fee plus 2 bps slippage a side
D = syn.DAY
JUNE = syn.ms(2021, 6, 1)


class Probe:
    """Runs a strategy and records each decision it returns, with the signal of every
    live member, without changing what it decides."""

    def __init__(self, inner):
        self.inner = inner
        self.interval = inner.interval
        self.decisions = []
        self.universes = []
        self.signals = []

    def decide(self, history, book):
        decision = self.inner.decide(history, book)
        self.decisions.append((history.now, decision))
        if isinstance(self.inner, trend.Trend) and decision is not None:
            self.universes.append((history.now, sorted(m.symbol for m in self.inner.members)))
            self.signals.append((history.now, {
                m.symbol: self.inner.signal_of(history, m)
                for m in self.inner.members if history.live(m)}))
        return decision


def one_day(rows, strategy, first=JUNE, days=1):
    """Run ``strategy`` over a short window from ``first`` with every series in ``rows``
    and an empty 15-minute series for each spot pair (orders then simply lapse)."""
    rows = dict(rows)
    for name in list(rows):
        if name.startswith("spot/") and name.endswith("/1d"):
            rows.setdefault(name[:-3] + "/15m", [])
    window = Window(syn.iso(first), syn.iso(first + (days - 1) * D), days)
    probe = Probe(strategy)
    result = backtest(probe, Market.from_rows(rows, window.end), window, COSTS)
    return probe, result


def weights_of(decision):
    return {i.symbol: w for i, w in decision.weights.items()}


def h1(cid="H1-01", first="2021-06-01", last="2021-06-01", **kw):
    return syn.configuration(cid, first, last, **kw)


def build(cid, rows, **kw):
    return trend.strategy_for(h1(cid, **kw), rows)


# --- section 6, Signal ------------------------------------------------------------------


def closes_with(points, default=100.0, days=152):
    """Daily closes from 2021-01-01 (day 0) to 2021-06-01 (day 151): ``default`` except
    at the given day indexes. The decision at 2021-06-01 00:00 reads day 150's close."""
    closes = [default] * days
    for day, close in points.items():
        closes[day] = close
    return closes


def test_the_vote_compares_the_close_at_t_with_the_close_l_days_earlier():
    # Decision 2021-06-01 00:00: the close at t is day 150's bar (2021-05-31), and L days
    # earlier is the bar closing at t - L days: day 130 (L=20), 90 (L=60), 30 (L=120).
    # AAA: 105 now; 110 twenty days back (vote 0), 100 sixty days back (vote 1), 104.99
    # 120 days back (vote 1): s = 2/3. BBB: 100 throughout, every ratio exactly 1, which
    # is not "above zero": s = 0. Section 6: w* = (1/5) x s without volatility scaling.
    rows = {
        "spot/klines/AAAUSDT/1d": syn.daily(syn.H1_FIRST, closes_with(
            {150: 105.0, 130: 110.0, 90: 100.0, 30: 104.99})),
        "spot/klines/BBBUSDT/1d": syn.daily(syn.H1_FIRST, closes_with({})),
    }
    probe, _ = one_day(rows, build("H1-08", rows))
    (t, signals), = probe.signals
    assert t == JUNE and signals == {"AAAUSDT": 2 / 3, "BBBUSDT": 0.0}
    (_, decision), = probe.decisions
    assert weights_of(decision) == {"AAAUSDT": pytest.approx(0.2 * 2 / 3, rel=1e-15),
                                    "BBBUSDT": 0.0}
    assert decision.band == 0.25  # section 6, Rebalancing: the 25% band of H1-08


def test_a_vote_one_day_either_side_of_the_lookback_would_differ():
    # Only the bar exactly 20 days back (day 130) is above the close at t; days 129 and
    # 131 are not, so a lookback read one bar off would vote 1, not 0.
    rows = {"spot/klines/AAAUSDT/1d": syn.daily(syn.H1_FIRST, closes_with(
        {150: 105.0, 130: 106.0, 129: 90.0, 131: 90.0}))}
    probe, _ = one_day(rows, build("H1-08", rows))
    assert probe.signals[0][1] == {"AAAUSDT": 2 / 3}


def ewm_sigma(squares, span, per_year):
    """sqrt(sum (1 - a)^j r_j^2 / sum (1 - a)^j) x sqrt(per_year), j = 0 the latest:
    pandas' ewm(span, adjust=True) of squared returns, written out (section 6)."""
    alpha = 2.0 / (span + 1.0)
    weights = [(1 - alpha) ** j for j in range(len(squares))]
    numerator = sum(w * s for w, s in zip(weights, reversed(squares), strict=True))
    return math.sqrt(numerator / sum(weights)) * math.sqrt(per_year)


def test_the_volatility_scaled_weight_is_the_registered_formula():
    # 120 daily returns of +3% then 30 of -1% (days 1 to 150). Votes: 20 days down (0),
    # 60 days 1.03^30 x 0.99^30 > 1 (1), 120 days up (1): s = 2/3. Sigma over every
    # return of the instrument with span 60 (alpha = 2/61), annualised by sqrt(365):
    # 30 squares of 1e-4 (the latest) and 120 of 9e-4. In closed form the weights sum to
    # (1 - d^n) / (1 - d) with d = 59/61.
    returns = [0.03] * 120 + [-0.01] * 30 + [0.0]
    rows = {"spot/klines/AAAUSDT/1d": syn.daily(syn.H1_FIRST, syn.geometric(100.0, returns))}
    probe, _ = one_day(rows, build("H1-01", rows))
    d = 59 / 61
    head = (1 - d ** 30) / (1 - d)
    tail = (d ** 30 - d ** 150) / (1 - d)
    sigma = math.sqrt((1e-4 * head + 9e-4 * tail) / (head + tail)) * math.sqrt(365)
    assert sigma == pytest.approx(ewm_sigma([9e-4] * 120 + [1e-4] * 30, 60, 365), rel=1e-12)
    assert sigma > 0.2  # so the 20% target, not the one-fifth cap, binds
    (_, decision), = probe.decisions
    assert weights_of(decision)["AAAUSDT"] == pytest.approx(0.2 * (2 / 3) * 0.2 / sigma,
                                                             rel=1e-9)


def test_the_slot_cap_binds_when_volatility_is_below_the_target():
    # +0.5% a day: sigma = 0.005 x sqrt(365) = 9.6% < 20%, so min(1, 0.2 / sigma) = 1 and
    # the weight is the whole slot, one fifth of equity, never more ("at most 1x").
    rows = {"spot/klines/AAAUSDT/1d": syn.daily(syn.H1_FIRST, syn.geometric(
        100.0, [0.005] * 150 + [0.0]))}
    probe, _ = one_day(rows, build("H1-01", rows))
    assert weights_of(probe.decisions[0][1])["AAAUSDT"] == pytest.approx(0.2, rel=1e-15)


def four_hour_rows(closes_4h, first=syn.H1_FIRST):
    """Daily bars from 2021-01-01 to 2021-06-01 and four-hour bars with the given closes."""
    return {
        "spot/klines/AAAUSDT/1d": syn.daily(first, [100.0] * 152),
        "spot/klines/AAAUSDT/4h": [syn.bar(first + i * syn.H4, syn.H4, c)
                                   for i, c in enumerate(closes_4h)],
    }


def test_four_hour_bars_read_lookbacks_and_the_span_in_calendar_days():
    # H1-06, section 6, Four-hour bars: lookbacks 120, 360 and 720 bars, a span of 360 bars,
    # volatility annualised by sqrt(2,190). +0.5% a bar: every vote 1 and every squared
    # return 2.5e-5, so sigma = 0.005 x sqrt(2190) and w* = 0.2 x 0.2 / sigma. The closes
    # 120 bars (20 days) before the 00:00 decision are spiked, so a lookback read as 20
    # bars rather than 20 days would see 1.005^20 > 1 and vote 1 there as well.
    n = 151 * 6  # bars opening up to 2021-05-31 20:00, closing at 2021-06-01 00:00
    closes = syn.geometric(100.0, [0.005] * (n + 5))[:n + 6]
    rows = four_hour_rows(closes)
    probe, _ = one_day(rows, build("H1-06", rows))
    t0, decision = probe.decisions[0]
    sigma = 0.005 * math.sqrt(2190)
    assert t0 == JUNE and weights_of(decision) == {
        "AAAUSDT": pytest.approx(0.2 * 0.2 / sigma, rel=1e-9)}
    spiked = list(closes)
    spiked[n - 1 - 120] = closes[n - 1] * 1.5  # the close exactly 20 days before 00:00
    rows = four_hour_rows(spiked)
    probe, _ = one_day(rows, build("H1-06", rows))
    assert probe.signals[0] == (JUNE, {"AAAUSDT": 2 / 3})
    # Six decisions a day, at every four-hour close (section 6).
    assert [t - JUNE for t, _ in probe.decisions] == [i * syn.H4 for i in range(6)]


def test_a_four_hour_decision_without_its_bar_is_skipped_and_changes_nothing():
    # Section 5, Gaps item 2: a missing four-hour bar skips the decision at its close.
    n = 151 * 6
    closes = syn.geometric(100.0, [0.005] * (n + 5))[:n + 6]
    rows = four_hour_rows(closes)
    rows["spot/klines/AAAUSDT/4h"] = [b for b in rows["spot/klines/AAAUSDT/4h"]
                                      if b.open_time != JUNE + syn.H4]  # closes at 08:00
    probe, _ = one_day(rows, build("H1-06", rows))
    by_time = dict(probe.decisions)
    assert by_time[JUNE + 2 * syn.H4] is None
    assert by_time[JUNE + syn.H4] is not None and by_time[JUNE + 3 * syn.H4] is not None


# --- section 6, Funding filter (H1-07) ---------------------------------------------------


def funded_rows(rate_in_window, perp="AAAUSDT", drop=None):
    """AAA rising steadily, and its perpetual's funding every eight hours: 0.0001 before
    the seven days to the 2021-06-01 decision, ``rate_in_window`` for the 21 settlements
    with calc_time in (t - 7 d + 60 s, t + 60 s], and a large rate exactly at t - 7 d,
    which the interval leaves out. Jitter of 5 ms, as the archive's."""
    t = JUNE
    rows = {"spot/klines/AAAUSDT/1d": syn.daily(syn.H1_FIRST, syn.geometric(
        100.0, [0.005] * 150 + [0.0]))}
    out = []
    for k in range(-200, 1):
        calc = t + k * 8 * syn.HOUR
        if drop is not None and k == drop:
            continue
        rate = rate_in_window if k > -21 else (0.05 if k == -21 else 0.0001)
        out.append(Funding(calc + 5 if k > -21 else calc, 8, rate))
    rows[f"um/fundingRate/{perp}"] = out
    return rows


def filtered_weight(rows):
    probe, _ = one_day(rows, build("H1-07", rows))
    return weights_of(probe.decisions[0][1])["AAAUSDT"]


def test_the_funding_filter_flattens_above_the_registered_sum():
    # 21 x (0.0064 / 21) = 0.64% > 0.63%: flat. 21 x (0.0062 / 21) = 0.62%: the base rule.
    # The 5% settlement at exactly t - 7 days is outside the interval, so it never counts.
    assert filtered_weight(funded_rows(0.0064 / 21)) == 0.0
    assert filtered_weight(funded_rows(0.0062 / 21)) == pytest.approx(0.2, rel=1e-15)


def test_the_funding_filter_reads_a_perpetual_quoted_per_thousand_coins():
    # Section 4: KKK's perpetual may be 1000KKKUSDT; its rates compare directly.
    rows = funded_rows(0.0064 / 21, perp="1000AAAUSDT")
    assert filtered_weight(rows) == 0.0


def test_the_funding_filter_does_not_apply_without_a_perpetual_or_across_a_gap():
    # "Where a member has no perpetual, or its perpetual's settlements do not cover the
    # seven days without a recorded gap, the filter does not apply."
    no_perp = funded_rows(0.0064 / 21)
    no_perp.pop("um/fundingRate/AAAUSDT")
    assert filtered_weight(no_perp) == pytest.approx(0.2, rel=1e-15)
    gap = funded_rows(0.0064 / 21 * 21 / 20, drop=-10)  # 20 settlements summing to 0.64%
    assert filtered_weight(gap) == pytest.approx(0.2, rel=1e-15)


def test_the_funding_filter_is_only_h1_07s():
    rows = funded_rows(0.0064 / 21)
    probe, _ = one_day(rows, build("H1-01", rows))
    assert weights_of(probe.decisions[0][1])["AAAUSDT"] == pytest.approx(0.2, rel=1e-15)


# --- section 6, The universe -----------------------------------------------------------


def universe_rows(volumes, first_days=None, last_days=None, quote_outside=None):
    """Daily bars from 2021-01-01 to 2021-06-01 with a flat 30-day quote volume per
    symbol, ``first_days`` and ``last_days`` overriding where a symbol starts or stops,
    and ``quote_outside`` a volume on the bar of 2021-05-01, which closes before the 30
    daily bars that close at the month end (2021-05-02 to 2021-05-31) begin."""
    first_days, last_days, quote_outside = first_days or {}, last_days or {}, quote_outside or {}
    rows = {}
    for symbol, volume in volumes.items():
        first, last = first_days.get(symbol, 0), last_days.get(symbol, 151)
        quotes = []
        for day in range(first, last + 1):
            in_window = 121 <= day <= 150
            quote = volume / 30 if in_window else 1.0
            if day == 120:
                quote = quote_outside.get(symbol, quote)
            quotes.append(quote)
        rows[f"spot/klines/{symbol}/1d"] = syn.daily(
            syn.H1_FIRST + first * D, [100.0] * (last - first + 1), quotes)
    return rows


def members(rows):
    probe, _ = one_day(rows, trend.strategy_for(syn.configuration(
        "B-H1", "2021-06-01", "2021-06-01"), rows))
    return probe.decisions[0][1]


def test_the_universe_is_the_first_five_eligible_of_the_ten_largest():
    # Section 6, steps 1 to 3. USDC is removed before ranking (STABLE_OR_FIAT); ETHBULL
    # (leveraged) and PAXG (off-chain) are dropped by category (D14); YNG's first bar,
    # 2021-03-01, is later than 120 days before the month-end close (D13); STP has no
    # month-end bar (it stopped on 2021-05-30). The ten largest after USDC are ETHBULL,
    # PAXG, YNG, STP and A1 to A6, so the universe is A1 to A5, by volume, each one fifth.
    # A7 and A8 are eligible but outside the ten. A8's huge volume on 2021-05-01 falls
    # outside "the 30 daily bars closing at the month end" and does not count.
    volumes = {"USDCUSDT": 1e12, "ETHBULLUSDT": 9e9, "PAXGUSDT": 8e9, "YNGUSDT": 7e9,
               "STPUSDT": 6e9}
    volumes.update({f"A{i}USDT": 1e9 - i * 1e7 for i in range(1, 9)})
    rows = universe_rows(volumes, first_days={"YNGUSDT": 59}, last_days={"STPUSDT": 149},
                         quote_outside={"A8USDT": 1e15})
    decision = members(rows)
    assert weights_of(decision) == {f"A{i}USDT": 0.2 for i in range(1, 6)}
    assert decision.band == 0.0  # section 8: B-H1 trades any difference


def test_a_slot_holds_cash_when_fewer_than_five_of_the_ten_are_eligible():
    # Section 6, step 3 and section 15, item 8: the ranking stops at the ten largest, so
    # an eligible eleventh pair never fills a slot; the empty slot holds cash.
    volumes = {"ETHBULLUSDT": 9e9, "PAXGUSDT": 8e9, "XAUTUSDT": 7.5e9, "YNGUSDT": 7e9,
               "STPUSDT": 6e9, "YNG2USDT": 5e9}
    volumes.update({f"A{i}USDT": 1e9 - i * 1e7 for i in range(1, 6)})
    rows = universe_rows(volumes, first_days={"YNGUSDT": 59, "YNG2USDT": 40},
                         last_days={"STPUSDT": 149})
    assert weights_of(members(rows)) == {f"A{i}USDT": 0.2 for i in range(1, 5)}


def test_a_pair_one_day_short_of_120_days_is_not_eligible():
    # D13: "a daily close 120 days before the month-end close". The month-end close is
    # 2021-06-01 00:00; the close 120 days before is 2021-02-01 00:00, the close of the
    # bar opening 2021-01-31 (day 30). Starting on day 30 is eligible; day 31 is not.
    rows = universe_rows({"A1USDT": 2e9, "A2USDT": 1e9}, first_days={"A1USDT": 31,
                                                                     "A2USDT": 30})
    assert weights_of(members(rows)) == {"A2USDT": 0.2}


def test_a_halted_member_holds_cash_until_the_next_month_end():
    # Section 4 (D12): HHH's last bar is 2021-06-10; its position is closed at that bar's
    # low and its slot holds cash; the trend names no target for it until 2021-07-01.
    rows = {
        "spot/klines/HHHUSDT/1d": syn.daily(syn.H1_FIRST, syn.geometric(100.0, [0.005] * 160)),
        "spot/klines/AAAUSDT/1d": syn.daily(syn.H1_FIRST, syn.geometric(100.0, [0.005] * 190)),
    }
    rows["spot/klines/HHHUSDT/15m"] = syn.fills(JUNE, JUNE + 12 * D, 120.0)
    rows["spot/klines/AAAUSDT/15m"] = syn.fills(JUNE, JUNE + 32 * D, 120.0)
    probe, result = one_day(rows, build("H1-08", rows, last="2021-07-01"), days=31)
    halts = [f for f in result.fills if f.kind == "halt"]
    assert [(f.symbol, syn.iso(f.at)) for f in halts] == [("HHHUSDT", "2021-06-11")]
    for t, decision in probe.decisions:
        named = weights_of(decision)
        assert ("HHHUSDT" in named) == (t < syn.ms(2021, 6, 11)), syn.iso(t)


# --- section 8, B-H1 ----------------------------------------------------------------------


def test_b_h1_trades_to_one_fifth_at_month_ends_only():
    # A1 and A2 are the only eligible pairs, so the universe has two members and three
    # slots of cash. 2021-06-01: equity 1, each bought at 0.2 of equity (A1 at 100, A2 at
    # 50; fills at the VWAP plus 2 bps, with a 10 bps fee). No order until 2021-07-01, by
    # which A2's close has risen to 75: the orders then are A1 0.2 E - 0.2 bought and A2
    # 0.3 - 0.2 E sold, E the equity marked then.
    closes_a1 = [100.0] * 182
    closes_a2 = [50.0] * 160 + [75.0] * 22
    rows = {"spot/klines/A1USDT/1d": syn.daily(syn.H1_FIRST, closes_a1),
            "spot/klines/A2USDT/1d": syn.daily(syn.H1_FIRST, closes_a2)}
    rows["spot/klines/A1USDT/15m"] = syn.fills(JUNE, JUNE + 32 * D, 100.0)
    rows["spot/klines/A2USDT/15m"] = syn.fills(JUNE, JUNE + 2 * D, 50.0) + syn.fills(
        syn.ms(2021, 7, 1), syn.ms(2021, 7, 2), 75.0)
    strategy = trend.strategy_for(syn.configuration("B-H1", "2021-06-01", "2021-07-01"), rows)
    probe, result = one_day(rows, strategy, days=31)
    assert [syn.iso(t) for t, d in probe.decisions if d is not None] == [
        "2021-06-01", "2021-07-01"]
    first = [(o.symbol, o.side, o.notional) for o in result.orders if o.decided_at == JUNE]
    assert first == [("A1USDT", 1, 0.2), ("A2USDT", 1, 0.2)]
    cost = 0.2 * 1.0002 * 1.001  # each buy: notional at the slipped price, plus the fee
    equity = (1.0 - 2 * cost) + 0.002 * 100.0 + 0.004 * 75.0
    july = [(o.symbol, o.side, o.notional) for o in result.orders if o.decided_at != JUNE]
    assert july == [("A1USDT", 1, pytest.approx(0.2 * equity - 0.2, rel=1e-9)),
                    ("A2USDT", -1, pytest.approx(0.3 - 0.2 * equity, rel=1e-9))]


# --- section 8, C-H1 ----------------------------------------------------------------------

Bar = namedtuple("Bar", "open_time close")


def test_the_sign_flipped_series_compounds_actual_returns_with_flipped_signs():
    # Closes 100, 110, 99, 99, 108.9; flips on days 1 and 3. Synthetic: 100; day 1 +10%
    # flipped, 90; day 2 -10% kept, 81; day 3 0% flipped, 81; day 4 +10% kept, 89.1.
    bars = [Bar(i * D, c) for i, c in enumerate([100.0, 110.0, 99.0, 99.0, 108.9])]
    view = SeriesView("spot/klines/AAAUSDT/1d", bars, [b.open_time for b in bars], len(bars))
    flips = {"AAAUSDT": {b.open_time: i in (1, 3) for i, b in enumerate(bars)}}
    flipped = trend.SignFlipped(flips)
    instrument = Instrument("AAAUSDT", 0)
    got = [flipped.close(instrument, view, i) for i in range(5)]
    assert got == pytest.approx([100.0, 90.0, 81.0, 81.0, 89.1], rel=1e-12)
    # A second instrument of the same symbol starts again at its own first close.
    later = trend.SignFlipped(flips)
    assert later.close(Instrument("AAAUSDT", 2 * D), view, 4) == pytest.approx(
        99.0 * 1.0 * 1.1, rel=1e-12)


def test_c_h1_votes_on_the_flipped_series_and_sizes_on_actual_prices():
    # AAA rises 0.5% a day, every day flipped: the synthetic series falls, every vote is
    # 0 and the control holds nothing, where H1-01 holds the full slot. With no day
    # flipped the control is H1-01 exactly.
    rows = {"spot/klines/AAAUSDT/1d": syn.daily(syn.H1_FIRST, syn.geometric(
        100.0, [0.005] * 150 + [0.0]))}
    days = {b.open_time for b in rows["spot/klines/AAAUSDT/1d"]}
    params = h1("C-H1", seed=0)["parameters"]
    flipped = trend.Trend(params, ["AAAUSDT"], trend.SignFlipped({"AAAUSDT": dict.fromkeys(
        days, True)}))
    probe, _ = one_day(rows, flipped)
    assert weights_of(probe.decisions[0][1]) == {"AAAUSDT": 0.0}
    kept = trend.Trend(params, ["AAAUSDT"], trend.SignFlipped({"AAAUSDT": dict.fromkeys(
        days, False)}))
    probe, _ = one_day(rows, kept)
    assert weights_of(probe.decisions[0][1]) == {"AAAUSDT": pytest.approx(0.2, rel=1e-15)}


def test_c_h1_draws_one_number_per_candidate_day_in_symbol_then_date_order():
    # Section 8: rng.random(n) < 0.5 with n the development daily bars of the candidates,
    # in order of symbol then date. With n = 158,821, the count G1.9 recorded, the draws
    # must be numpy's own: below one half 79,640 times for seed 0, and at each sampled
    # index the flip of the symbol and day that index falls on is numpy's value < 0.5.
    vectors = json.loads((FIXTURES / "numpy-rng-vectors.json").read_text())
    n = vectors["c_h1_n"]
    symbols = [f"S{i:03d}USDT" for i in range(100)]
    sizes = [n // 100] * 99 + [n - 99 * (n // 100)]
    daily = {s: [Bar(d * D, 1.0) for d in range(size)] for s, size in
             zip(reversed(symbols), reversed(sizes), strict=True)}  # insertion order unsorted
    seed0 = next(s for s in vectors["seeds"] if s["seed"] == 0)
    flips = trend.sign_flips(daily, 0)
    assert sum(sum(days.values()) for days in flips.values()) == (
        seed0["random"]["below_half_in_first_c_h1_n"])
    for index, value in seed0["random"]["sample"].items():
        i = int(index)
        if i >= n:
            continue
        k = min(i // sizes[0], 99)
        day = i - k * sizes[0]
        assert flips[symbols[k]][day * D] is (float.fromhex(value) < 0.5), i


def test_c_h1_draws_over_the_candidates_development_bars_only():
    # Section 8: "every development daily bar of the 115 pinned candidates". A pair that
    # is not a candidate and any bar from 2026-01-01 on take no draw.
    first = syn.ms(2025, 12, 20)
    rows = {f"spot/klines/{s}/1d": syn.daily(first, [100.0] * 20)
            for s in ("BTCUSDT", "ETHUSDT", "AAAUSDT")}
    config = syn.configuration("C-H1", "2025-12-20", "2025-12-31", seed=3)
    control = trend.strategy_for(config, rows)
    development = {s: [b for b in rows[f"spot/klines/{s}/1d"] if b.open_time < syn.ms(2026, 1, 1)]
                   for s in ("BTCUSDT", "ETHUSDT")}
    assert control.flipped.flips == trend.sign_flips(development, 3)
    assert {len(v) for v in control.flipped.flips.values()} == {12}


# --- both drivers, and section 11, item 5 -----------------------------------------------


@pytest.fixture(scope="module")
def market_rows():
    return syn.h1_market()


@pytest.mark.parametrize("cid", ["H1-01", "H1-06", "H1-07", "B-H1"])
def test_batch_and_paper_give_identical_orders(market_rows, cid):
    """The same strategy in the backtest and in paper trading: identical orders, fills,
    lapses, skips, marks and returns (G1.9's two drivers; section 5)."""
    config = syn.configuration(cid, "2021-06-01", "2021-07-31")
    window = Window.from_configuration(config)
    batch = backtest(trend.strategy_for(config, market_rows),
                     Market.from_rows(market_rows, window.end), window, COSTS)
    paper = paper_run(trend.strategy_for(config, market_rows), market_rows, window, COSTS)
    for name in ("orders", "fills", "lapses", "accruals", "skips", "marks", "returns"):
        assert getattr(batch, name) == getattr(paper, name), name
    assert batch.orders and len(batch.returns) == 61
    if cid != "B-H1":
        assert {f.kind for f in batch.fills} == {"fill", "halt"}, "HHH stops (D12)"


def test_the_synthetic_market_exercises_the_rules(market_rows):
    # The seeded market should reach the rules it was built for, or the identity test
    # above proves less than it seems.
    config = syn.configuration("H1-01", "2021-06-01", "2021-07-31")
    window = Window.from_configuration(config)
    probe = Probe(trend.strategy_for(config, market_rows))
    result = backtest(probe, Market.from_rows(market_rows, window.end), window, COSTS)
    universes = {syn.iso(t): symbols for t, symbols in probe.universes}
    assert "HHHUSDT" in universes["2021-06-01"]
    assert any(f.kind == "halt" and f.symbol == "HHHUSDT" for f in result.fills), "D12"
    for month in ("2021-06-01", "2021-07-01"):
        assert not {"USDCUSDT", "ETHBULLUSDT", "GGGUSDT"} & set(universes[month]), month
    assert any(lapse.order.symbol == "BBBUSDT" for lapse in result.lapses)
    filtered = syn.configuration("H1-07", "2021-06-01", "2021-07-31")
    base, funded = (backtest(trend.strategy_for(c, market_rows),
                             Market.from_rows(market_rows, window.end), window, COSTS)
                    for c in (config, filtered))
    assert base.returns != funded.returns, "the funding filter binds somewhere"


@pytest.mark.parametrize("cid", ["H1-01", "H1-06", "B-H1"])
def test_a_longer_run_starts_with_the_shorter_runs_returns(market_rows, cid):
    """Section 11, item 5: the holdout run's returns up to 2025-12-31 must equal the
    development run's exactly. Here a run to 2021-07-10 on data stopping there, and one
    to 2021-08-10 on all of it, carrying positions and the month end across."""
    short = syn.configuration(cid, "2021-06-01", "2021-07-10")
    long = syn.configuration(cid, "2021-06-01", "2021-08-10")
    end = Window.from_configuration(short).end
    first = trend.simulate(short, Market.from_rows(syn.truncated(market_rows, end), end))
    second = trend.simulate(long, Market.from_rows(
        market_rows, Window.from_configuration(long).end))
    assert len(first) == 40 and second[:40] == first


# --- the seam, the guards and what each run loads ---------------------------------------


class FakeView:
    """Stands in for the runner's DataView over synthetic rows: records each load."""

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
    with pytest.raises(SimulationError, match="data seam"):
        trend.run(h1(), FakeView(market_rows))


@pytest.mark.parametrize(("cid", "extra"), [
    ("H1-01", set()), ("H1-06", {"4h"}), ("H1-07", {"funding"}), ("B-H1", set()),
    ("C-H1", set())])
def test_each_configuration_loads_only_the_series_it_reads(market_rows, cid, extra):
    config = syn.configuration(cid, "2021-06-01", "2021-06-05",
                               seed=0 if cid == "C-H1" else None)
    view = FakeView(market_rows)
    market = trend.load_market(config, view, spans_of(market_rows))
    kinds = {"4h" if n.endswith("/4h") else "funding" if "fundingRate" in n else n[-2:]
             for n in market.series}
    assert kinds == {"1d"} | extra
    assert all(n.endswith("/1d") or n.endswith("/4h") or "fundingRate" in n
               for n in view.loaded), "15-minute bars are read lazily, for fills"
    if cid != "C-H1":  # the synthetic pairs are not pinned candidates, so they draw no signs
        assert len(trend.simulate(config, market)) == 5
        assert any(n.endswith("/15m") for n in view.loaded)


def test_only_the_holdout_run_reads_past_the_development_period():
    rows = {}
    past = syn.configuration("H1-01", "2025-12-01", "2026-01-01")
    with pytest.raises(SimulationError, match="at the latest"):
        trend.strategy_for(past, rows)
    assert isinstance(trend.strategy_for({**past, "kind": "holdout"}, rows), trend.Trend)
    for cid in ("B-H1", "C-H1"):
        config = syn.configuration(cid, "2025-12-01", "2026-01-01",
                                   seed=0 if cid == "C-H1" else None)
        with pytest.raises(SimulationError, match="at the latest"):
            trend.strategy_for(config, rows)


def test_a_configuration_runs_only_as_its_registered_kind():
    rows = {}
    with pytest.raises(SimulationError, match="benchmark"):
        trend.strategy_for({**h1("B-H1"), "kind": "trial"}, rows)
    with pytest.raises(SimulationError, match="trial, sensitivity or holdout"):
        trend.strategy_for({**h1("H1-01"), "kind": "control"}, rows)
    with pytest.raises(SimulationError, match="not an H1 configuration"):
        trend.strategy_for({**h1("H1-01"), "id": "H2-01"}, rows)
