"""H1, its benchmark B-H1 and its control C-H1 (G2.2), on synthetic data only.

Each rule of sections 6 and 8 of docs/PREREGISTRATION.md is checked against a value
worked out by hand from the synthetic inputs (the arithmetic is in each test, never
the engine's code), the controls' random numbers against the vectors numpy itself
generated (fixtures/numpy-rng-vectors.json), and the strategy against both drivers and
against section 11, item 5's rule that a longer run's returns start with a shorter
run's. No market data is read and the dataset store is never opened: the registered
loader reads only a synthetic mirror and synthetic manifests in a temporary directory.
"""

from __future__ import annotations

import json
import math
from collections import namedtuple
from pathlib import Path

import pytest
import strategy_synthetic as syn
from norse_research.formats import Funding
from norse_research.loader import DatasetLoader, inside_ledger_entry
from norse_research.periods import DEVELOPMENT, HOLDOUT, inside_unsealed_run
from norse_research.runner import DataView
from norse_research.sim import (
    Costs,
    Instrument,
    Market,
    SeriesView,
    SimulationError,
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
    return trend.strategy_for(h1(cid, **kw), rows, syn.listed(rows))


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
        "B-H1", "2021-06-01", "2021-06-01"), rows, syn.listed(rows)))
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
    strategy = trend.strategy_for(syn.configuration("B-H1", "2021-06-01", "2021-07-01"), rows,
                                  syn.listed(rows))
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
    flipped = trend.Trend(params, syn.listed(rows), trend.SignFlipped({
        "AAAUSDT": dict.fromkeys(days, True)}))
    probe, _ = one_day(rows, flipped)
    assert weights_of(probe.decisions[0][1]) == {"AAAUSDT": 0.0}
    kept = trend.Trend(params, syn.listed(rows), trend.SignFlipped({
        "AAAUSDT": dict.fromkeys(days, False)}))
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
    control = trend.strategy_for(config, rows, syn.listed(rows))
    development ={s: [b for b in rows[f"spot/klines/{s}/1d"] if b.open_time < syn.ms(2026, 1, 1)]
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
    listed = syn.listed(market_rows)
    batch = backtest(trend.strategy_for(config, market_rows, listed),
                     Market.from_rows(market_rows, window.end), window, COSTS)
    paper = paper_run(trend.strategy_for(config, market_rows, listed), market_rows, window,
                      COSTS)
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
    listed = syn.listed(market_rows)
    probe = Probe(trend.strategy_for(config, market_rows, listed))
    result = backtest(probe, Market.from_rows(market_rows, window.end), window, COSTS)
    universes = {syn.iso(t): symbols for t, symbols in probe.universes}
    assert "HHHUSDT" in universes["2021-06-01"]
    assert any(f.kind == "halt" and f.symbol == "HHHUSDT" for f in result.fills), "D12"
    for month in ("2021-06-01", "2021-07-01"):
        assert not {"USDCUSDT", "ETHBULLUSDT", "GGGUSDT"} & set(universes[month]), month
    assert any(lapse.order.symbol == "BBBUSDT" for lapse in result.lapses)
    filtered = syn.configuration("H1-07", "2021-06-01", "2021-07-31")
    base, funded = (backtest(trend.strategy_for(c, market_rows, listed),
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
    cut = syn.truncated(market_rows, end)
    first = trend.simulate(short, Market.from_rows(cut, end), syn.listed(cut))
    second = trend.simulate(long, Market.from_rows(
        market_rows, Window.from_configuration(long).end), syn.listed(market_rows))
    assert len(first) == 40 and second[:40] == first


# --- the listing, the guards and what each run loads ------------------------------------


def test_a_pair_is_ranked_only_once_its_coverage_starts_before_the_decision():
    # Section 6, step 1 over the data view's listing: a pair whose archive starts at or
    # after the decision has no bar closed by then and is skipped before ranking. Here
    # A1, the largest, is listed as first archived at the 2021-06-01 decision itself
    # (its rows say otherwise, so only the listing can leave it out): A2 to A6 are the
    # universe. Listed as first archived a day earlier, A1 ranks first again.
    rows = universe_rows({f"A{i}USDT": 1e9 - i * 1e7 for i in range(1, 7)})
    config = syn.configuration("B-H1", "2021-06-01", "2021-06-01")
    for first_archived, expected in ((JUNE, range(2, 7)), (JUNE - D, range(1, 6))):
        listed = {**syn.listed(rows), "A1USDT": first_archived}
        probe, _ = one_day(rows, trend.strategy_for(config, rows, listed))
        assert weights_of(probe.decisions[0][1]) == {f"A{i}USDT": 0.2 for i in expected}


@pytest.mark.parametrize(("cid", "extra"), [
    ("H1-01", set()), ("H1-06", {"4h"}), ("H1-07", {"funding"}), ("B-H1", set()),
    ("C-H1", set())])
def test_each_configuration_loads_only_the_series_it_reads(market_rows, cid, extra):
    """The pinned ``run`` takes its series from the data view's listing, each parsed by
    ``DatasetSpec``: spot klines of USDT pairs (daily, 15-minute lazily for fills,
    four-hour for H1-06) and funding for H1-07, each read from its coverage, and nothing
    else the manifests pin. A spot pair quoted in BTC with the largest volume, and a
    perpetual's klines, are listed and never read."""
    config = syn.configuration(cid, "2021-06-01", "2021-06-05",
                               seed=0 if cid == "C-H1" else None)
    offered = {**market_rows,
               "spot/klines/AAABTC/1d": syn.daily(syn.H1_FIRST, [1.0] * syn.H1_DAYS,
                                                  [1e15] * syn.H1_DAYS),
               "um/klines/AAAUSDT/1d": syn.daily(syn.H1_FIRST, [100.0] * syn.H1_DAYS)}
    view = syn.FakeView(offered)
    names = trend.series_needed(config, view.list_series())
    wanted = {n for n in market_rows if n.startswith("spot/") and n.endswith(("/1d", "/15m"))}
    if "4h" in extra:
        wanted |= {n for n in market_rows if n.endswith("/4h")}
    if "funding" in extra:
        wanted |= {n for n in market_rows if "/fundingRate/" in n}
    assert set(names) == wanted
    market = trend.load_market(config, view, names)
    assert view.covered == wanted
    assert view.loaded == {n for n in wanted if not n.endswith("/15m")}, (
        "15-minute bars are read lazily, for fills")
    if cid == "C-H1":  # the synthetic pairs are not pinned candidates, so they draw no signs
        with pytest.raises(SimulationError, match="not a pinned candidate"):
            trend.run(config, syn.FakeView(offered))
        return
    got = trend.simulate(config, market, trend.listed_pairs(view, names))
    assert any(n.endswith("/15m") for n in view.loaded)
    assert trend.run(config, syn.FakeView(offered)) == got
    expected = trend.simulate(config, Market.from_rows(market_rows, market.horizon),
                              syn.listed(market_rows))
    assert got == expected and len(expected) == 5


# --- the listing on synthetic manifests, through the registered loader --------------------

ENTRY, UNSEAL = "e" * 64, "f" * 64
HOLDOUT_START = syn.ms(2026, 1, 1)

# syn.LISTING_SPEC's ten largest at each month end, worked out from its flat volumes over
# the 30 daily bars closing then: USDC is removed before ranking; ALPHABTC is not a USDT
# pair; LATE is first archived on 2025-09-01 and NEW on 2026-01-01, so neither is ranked
# at the decision on its first day; DELTA's volume doubles from 2025-10-01; ECHO's last bar
# is 2025-11-15 (15 bars of 5e6 in November's window, below FOX's 30 of 4e6), and it has
# no volume after.
RANKINGS = {
    "2025-09-01": ["ETHBULLUSDT", "ALPHAUSDT", "BRAVOUSDT", "CHARLIEUSDT", "DELTAUSDT",
                   "ECHOUSDT", "FOXUSDT"],
    "2025-10-01": ["ETHBULLUSDT", "LATEUSDT", "ALPHAUSDT", "BRAVOUSDT", "CHARLIEUSDT",
                   "DELTAUSDT", "ECHOUSDT", "FOXUSDT"],
    "2025-11-01": ["ETHBULLUSDT", "LATEUSDT", "DELTAUSDT", "ALPHAUSDT", "BRAVOUSDT",
                   "CHARLIEUSDT", "ECHOUSDT", "FOXUSDT"],
    "2025-12-01": ["ETHBULLUSDT", "LATEUSDT", "DELTAUSDT", "ALPHAUSDT", "BRAVOUSDT",
                   "CHARLIEUSDT", "FOXUSDT", "ECHOUSDT"],
    "2026-01-01": ["ETHBULLUSDT", "LATEUSDT", "DELTAUSDT", "ALPHAUSDT", "BRAVOUSDT",
                   "CHARLIEUSDT", "FOXUSDT"],
    "2026-02-01": ["ETHBULLUSDT", "NEWUSDT", "LATEUSDT", "DELTAUSDT", "ALPHAUSDT",
                   "BRAVOUSDT", "CHARLIEUSDT", "FOXUSDT"],
}
# Steps 2 and 3: ETHBULL dropped by category (D14); LATE has no close 120 days before a
# month end until 2026-01-01 (2026-01-01 less 121 days is 2025-09-02), NEW none at all
# (D13); ECHO has no month-end bar on 2025-12-01; the first five remaining.
UNIVERSES = {
    "2025-09-01": ["ALPHAUSDT", "BRAVOUSDT", "CHARLIEUSDT", "DELTAUSDT", "ECHOUSDT"],
    "2025-10-01": ["ALPHAUSDT", "BRAVOUSDT", "CHARLIEUSDT", "DELTAUSDT", "ECHOUSDT"],
    "2025-11-01": ["DELTAUSDT", "ALPHAUSDT", "BRAVOUSDT", "CHARLIEUSDT", "ECHOUSDT"],
    "2025-12-01": ["DELTAUSDT", "ALPHAUSDT", "BRAVOUSDT", "CHARLIEUSDT", "FOXUSDT"],
    "2026-01-01": ["LATEUSDT", "DELTAUSDT", "ALPHAUSDT", "BRAVOUSDT", "CHARLIEUSDT"],
    "2026-02-01": ["LATEUSDT", "DELTAUSDT", "ALPHAUSDT", "BRAVOUSDT", "CHARLIEUSDT"],
}


class ListingProbe:
    """Runs a strategy and records, at each decision, the pairs its decision names and, at
    each month end, the ranking (section 6, step 1) and the universe it chose."""

    def __init__(self, inner):
        self.inner = inner
        self.interval = inner.interval
        self.named = {}
        self.rankings = {}
        self.universes = {}

    def decide(self, history, book):
        decision = self.inner.decide(history, book)
        t = history.now
        self.named[t] = set() if decision is None else {i.symbol for i in decision.weights}
        if trend.is_month_end(t):
            self.rankings[syn.iso(t)] = trend.month_end_ranking(history, self.inner.listed)
            self.universes[syn.iso(t)] = [m.symbol for m in self.inner.members]
        return decision


def probed_run(config, view):
    """``trend.run``'s steps on ``view``, with a probe around the strategy."""
    names = trend.series_needed(config, view.list_series())
    listed = trend.listed_pairs(view, names)
    market = trend.load_market(config, view, names)
    rows = {name: series.rows for name, series in market.series.items()}
    probe = ListingProbe(trend.strategy_for(config, rows, listed))
    result = backtest(probe, market, Window.from_configuration(config),
                      Costs.from_configuration(config))
    return {"listing": view.list_series(), "names": names, "listed": listed,
            "probe": probe, "result": result}


@pytest.fixture(scope="module")
def listing_runs(tmp_path_factory):
    """H1-01 twice on the synthetic manifests, each through the registered loader and the
    runner's DataView: as a trial to 2025-12-31 on the development manifest alone, and as
    the holdout run to 2026-02-28 on both (section 11, item 5), whose listing adds NEW."""
    store, manifests = syn.listing_world(tmp_path_factory.mktemp("listing"))
    trial = syn.configuration("H1-01", "2025-09-01", "2025-12-31")
    holdout = syn.configuration("H1-01", "2025-09-01", "2026-02-28", kind_override="holdout")
    with inside_ledger_entry(ENTRY):
        development = probed_run(trial, DataView(DatasetLoader(manifests[DEVELOPMENT], store)))
        pinned = trend.run(trial, DataView(DatasetLoader(manifests[DEVELOPMENT], store)))
        with inside_unsealed_run(UNSEAL):
            both = DatasetLoader(manifests[DEVELOPMENT], store, holdout=manifests[HOLDOUT])
            holdout_run = probed_run(holdout, DataView(both))
    return {"development": development, "pinned": pinned, "holdout": holdout_run}


def test_the_universe_on_given_dates_is_ranked_from_the_listed_series(listing_runs):
    """Section 6, steps 1 to 3, at each month end of both runs, from what the manifests
    list: the ranking and the universe worked out by hand above, and each month-end
    decision naming exactly its universe (the 2025-12 month-end universe from 2026-01-01
    in the holdout run, section 11, item 5)."""
    development, holdout = listing_runs["development"], listing_runs["holdout"]
    assert holdout["probe"].rankings == RANKINGS
    assert holdout["probe"].universes == UNIVERSES
    dev_months = [m for m in RANKINGS if m < "2026"]
    assert development["probe"].rankings == {m: RANKINGS[m] for m in dev_months}
    assert development["probe"].universes == {m: UNIVERSES[m] for m in dev_months}
    for run in (development, holdout):
        for month, members in run["probe"].universes.items():
            assert run["probe"].named[syn.ms(*map(int, month.split("-")))] == set(members)
    # Only spot klines of USDT pairs, and for H1-01 no four-hour bars or funding, are read:
    # ALPHABTC (the largest volume of all) and the perpetual's series are listed only.
    for run in (development, holdout):
        assert {"spot/klines/ALPHABTC/1d", "um/klines/ALPHAUSDT/1d",
                "um/fundingRate/ALPHAUSDT"} <= set(run["listing"])
        assert all(n.startswith("spot/klines/") and "USDT/" in n and n.endswith(("/1d", "/15m"))
                   for n in run["names"])
    assert sum(r != 0.0 for _, r in development["result"].returns) > 100, "it traded"


def test_a_pair_archived_only_in_the_holdout_never_enters_a_development_decision(
        listing_runs):
    """NEW is pinned by the holdout manifest alone, from 2026-01-01. The holdout run lists
    and loads it, its coverage starts at the holdout, and no decision before 2026-01-01
    ranks it, names it or trades it; once a bar of it has closed it is ranked (2026-02-01)."""
    development, holdout = listing_runs["development"], listing_runs["holdout"]
    new = "spot/klines/NEWUSDT/1d"
    assert set(holdout["listing"]) - set(development["listing"]) == {new}
    assert new in holdout["names"] and holdout["listed"]["NEWUSDT"] == HOLDOUT_START
    assert "NEWUSDT" not in development["listed"]
    before = {t: named for t, named in holdout["probe"].named.items() if t < HOLDOUT_START}
    assert len(before) == 122 and not any("NEWUSDT" in named for named in before.values())
    assert [m for m, ranked in holdout["probe"].rankings.items() if "NEWUSDT" in ranked] == [
        "2026-02-01"]
    assert not any(o.symbol == "NEWUSDT" for o in holdout["result"].orders)


def test_the_development_months_rank_the_same_whether_or_not_holdout_pairs_are_listed(
        listing_runs):
    """Section 11, item 5: the holdout run, whose listing holds NEW, ranks every
    development month end exactly as the development run, whose listing does not, and
    decides and returns exactly the same to 2025-12-31. The pinned ``run`` returns what
    the probed run returns."""
    development, holdout = listing_runs["development"], listing_runs["holdout"]
    dev, hold = development["probe"], holdout["probe"]
    assert dev.rankings == {m: r for m, r in hold.rankings.items() if m < "2026"}
    assert dev.universes == {m: u for m, u in hold.universes.items() if m < "2026"}
    assert dev.named == {t: n for t, n in hold.named.items() if t < HOLDOUT_START}
    returns = development["result"].returns
    assert len(returns) == 122 and holdout["result"].returns[:122] == returns
    assert listing_runs["pinned"] == returns


def test_only_the_holdout_run_reads_past_the_development_period():
    rows = {}
    past = syn.configuration("H1-01", "2025-12-01", "2026-01-01")
    with pytest.raises(SimulationError, match="at the latest"):
        trend.strategy_for(past, rows, {})
    assert isinstance(trend.strategy_for({**past, "kind": "holdout"}, rows, {}), trend.Trend)
    for cid in ("B-H1", "C-H1"):
        config = syn.configuration(cid, "2025-12-01", "2026-01-01",
                                   seed=0 if cid == "C-H1" else None)
        with pytest.raises(SimulationError, match="at the latest"):
            trend.strategy_for(config, rows, {})


def test_a_configuration_runs_only_as_its_registered_kind():
    rows = {}
    with pytest.raises(SimulationError, match="benchmark"):
        trend.strategy_for({**h1("B-H1"), "kind": "trial"}, rows, {})
    with pytest.raises(SimulationError, match="trial, sensitivity or holdout"):
        trend.strategy_for({**h1("H1-01"), "kind": "control"}, rows, {})
    with pytest.raises(SimulationError, match="not an H1 configuration"):
        trend.strategy_for({**h1("H1-01"), "id": "H2-01"}, rows, {})
