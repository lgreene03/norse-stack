# The simulation engine (G1.9)

Owner: technical. Written 2026-11-18. The code is in
[research/norse_research/sim](../../research/norse_research/sim) and the tests in
[research/tests](../../research/tests) (`test_sim_rules.py`, `test_sim_golden.py`,
`test_sim_engine.py`, with the fixture `fixtures/sim-golden.json`), run by CI's `research`
job. Everything here was computed on synthetic data. No price, return, signal or
performance figure was computed on market data, and nothing was read from the holdout.

Every H1, H2, benchmark and control run is computed by this engine. It implements
sections 2 to 5 of [PREREGISTRATION.md](../PREREGISTRATION.md) (the window, instruments,
halts and delistings, fills, marks, costs, gaps and funding) and the mechanics that
sections 6 to 8 need: the dated universe, volatility-targeted weights, the rebalance band,
H2's two legs and their funding, and the costs of benchmarks, controls and sensitivity
runs. It does not contain the H1 and H2 strategies or the registered grid, which arrive
with G2.2 and G2.3 under `norse_research/strategies/`. Each rule in the code carries a
comment naming its section of the registration.

## How a strategy uses it

A strategy is an object with an `interval`, either `"1d"` or `"4h"`, and one method,
`decide(history, book)`, which returns a `Decision` or nothing. The engine calls it at
every decision time with a `History`, which shows only the rows known at that moment, and
a read-only view of the book marked then. A decision either sets a complete book of
single-leg spot weights with a band, which is how H1 and both benchmarks express
themselves, or sets the carry state of named coins, which is how H2 expresses itself.

The same strategy object runs in two drivers. `backtest` runs it over a `Market` read up
front through the runner's `DataView`. `Paper` runs it fed one row at a time, and `replay`
turns recorded rows into that feed. The rules that turn decisions into orders and bars
into fills are shared; the drivers differ only in how each learns which bar fills an
order (the backtest searches the loaded 15-minute bars, paper trading waits for them to
arrive) and when an instrument stops (the backtest looks one daily bar ahead, as section 4
allows, and paper trading is told by its feed). The golden-fixture test holds the two to
identical orders, fills, lapses, funding accruals, marks and returns, and holds both to
outputs derived by hand from the registration. A further test holds them identical on a
seeded synthetic market with missing and empty bars, a pair that stops trading, a pair
that stops and returns as a new instrument, a missing four-hour bar, jittered funding with
a missing settlement and a missing day of perpetual klines.

A strategy module's pinned function builds the market and returns the daily returns in
the shape the runner hands to the harness, one value per day of its window, through
`run_backtest`. The engine reads the window from the resolved configuration and assumes
no end date, so the same code runs the holdout run of section 11, item 5, whose window
runs from the development window's start through 2026-10-31.

The engine is a library. It opens no file, socket or process, and reads data only through
the `DataView` the runner passes; a test runs a strategy that uses it inside the runner's
fresh interpreter under its audit hook. Because genesis forbids H1's and H2's strategy
paths from overlapping, a library both share cannot live under either, so the engine sits
in the harness's paths and `norse_research.sim` is added to the strategy allowlist in
`norse_research.sources` (threat T13). Only the package is allowlisted, not its
submodules, and a test checks that the engine itself reaches only the allowlist, its own
modules and G0.6's pure ranking in `norse_research.universe`.

Because the engine sits in the harness's tree group, it is code both hypotheses use: after
the first trial, any fix to it reruns every configuration already run (section 9, run rule
3), and between a candidate's selectable run and the holdout run it cannot change at all
(section 11, item 6, which allows only data loading and the seal). It should therefore be
reviewed as final before G2.1 records the first benchmark.

## The rules

**The window (section 3).** A run's window comes from its configuration. It has one mark
at 00:00 UTC on each return day and one after the last, and the return of day D is the
equity marked at 00:00 on D+1 over the equity marked at 00:00 on D, minus one. Daily
decisions run from the first day's 00:00 to the last day's 00:00, four-hourly ones to the
last day's 20:00, so no development run decides at 2026-01-01 00:00. Positions open at the
end are marked at the last close and never closed. Equity starts at 1 and quantities are
real numbers, so returns do not depend on it.

**Instruments (section 4).** A spot symbol's daily bars are split into instruments at
every missing daily bar, and an instrument is named by its symbol and the open of its
first bar, so a symbol that returns after a gap is a new instrument with no history. A
target names an instrument, not a symbol.

**Halts and delistings (section 4, D12).** When an instrument stops before the end of the
window, a long position in it is sold at the low of its last daily bar less slippage, with
the fee, and booked at that bar's close, just after the mark taken at that instant, so the
loss counts in the day that starts there (choice 7). From then until the next month-end
update its target is held at zero, so its slot holds cash. The backtest learns that a bar is the last
one by looking one daily bar ahead, which section 4 permits for exactly this; a series
whose data simply reaches the window's end has not stopped.

**Fills (section 5).** An order decided at t fills at the volume-weighted average price,
quote volume over base volume, of the 15-minute bar that opens at t, or of the next bar
with volume if that one is missing or empty. If no bar with volume opens before t plus the
decision interval or the window's end, whichever is earlier, the order lapses and the
position is unchanged. A position changes at the close of its fill bar. The two legs of an
H2 carry position fill together on the first bar at which both the spot and the perpetual
bar have volume, each at its own price, or both lapse.

**Orders (section 6).** An order's notional is the change from the current drifted weight
to the target times equity marked at the decision, and its quantity is that notional over
the fill bar's reference price. Within one bar, sells fill first, and if the buys and
their fees would take cash below zero, every buy is scaled by one factor so that cash ends
at zero; a carry entry's two legs are scaled together. An H2 entry buys half of equity
over the spot fill's reference price in spot and sells the same quantity of the
perpetual; the exit sells that spot quantity and buys the perpetual back.

**Costs (section 5; section 8).** The execution price is the reference price moved
against the order by the slippage, a buy up and a sell down, as huginn's executor does
with its impact term off, and the fee is a fraction of the traded notional at the
execution price. The figures come only from the resolved configuration's `costs`, which
the registration sets to a fee of 10 bps and slippage of 2 bps a side for every trial,
benchmark and control, and to the sensitivity total less 2 bps plus 2 bps for a
sensitivity run. The engine has no default, so huginn's old 5 plus 2 bps cannot reach a
run. Tests show a round trip on one leg costs 24 bps and a carry round trip 48 bps.

**Marks (section 5).** Equity at each 00:00 is cash plus each spot position at its daily
close plus each perpetual position's unrealised profit or loss at the perpetual's own
daily close. A perpetual's missing daily bar carries its last close forward. A spot
position never needs that: a missing spot daily bar ends the instrument, whose position is
marked at its last bar's close and closed just after that mark, before any mark that would
need the missing bar.

**Funding (section 5; section 7).** Each published settlement accrues at its `calc_time`
on the perpetual quantity held at that instant, valued at the close of the latest
perpetual 15-minute bar closed by then: a short receives quantity times mark times rate
and pays it when the rate is negative. A settlement stamped exactly at a 00:00 mark
accrues after that mark, in the day that starts there (choice 7), and one at or after the
window's last mark falls in no day of the window. A missing settlement accrues nothing. Funding is
accrued per settlement, never as a daily rate times three, so section 15, item 17's
point about G2.3's "every eight hours" does not arise: BTC and ETH settle every eight
hours, and the engine would follow any other interval the archive publishes.

**Gaps (section 5).** Every load through the `DataView` passes
`accept_recorded_gaps=True`, so a gap the committed manifest records is crossed and any
other fails the run. A missing 15-minute bar delays a fill. A four-hour decision is
skipped when an instrument it holds or would trade has no four-hour bar closing then. A
missing daily bar ends an instrument. The loader never returns an off-grid bar. The
holdout's missing day of BTC and ETH perpetual klines is handled by the joint fill and the
mark rule above: an H2 exit decided that day lapses on both legs, the perpetual's last
close carries forward, and funding still accrues at the latest 15-minute close.

**Volatility-targeted weights (section 6).** The volatility is the square root of the
exponentially weighted mean of squared simple returns, not demeaned, with weights
(1 minus alpha) to the power j over every return of the instrument (pandas'
`ewm(span, adjust=True)`), annualised by the square root of the periods in a year. The
engine keeps it as a running numerator and denominator, so both drivers add the same
terms in the same order. The target weight is a slot of equity times the signal times
the smaller of the slot cap and the volatility target over the volatility; without
volatility scaling it is the slot times the signal. The vote and the signal (the mean of
the votes) are there too.

**The band (section 6).** An instrument is traded to its target only when the absolute
difference between target and current weight exceeds the band times the target. Entries
and exits therefore always trade, and a band of zero, which the benchmarks use, trades any
difference.

**Trailing funding and the carry thresholds (sections 6 and 7).** The trailing sum runs
over settlements with `calc_time` in the interval from t minus the lookback plus 60
seconds, exclusive, to t plus 60 seconds, inclusive, and reports whether the interval is
covered with no gap. The thresholds are the multiple times the 0.48% round trip times the
lookback over 28, and a test checks them against every H2 configuration in
[preregistration.json](../preregistration.json). A flat coin enters above the entry
threshold and a coin in carry exits below the exit threshold.

**The dated universe (section 6).** `rank_top` ranks through G0.6's own `rank_month_ends`
with the 27 stablecoin and fiat bases removed first, and `select_universe` takes the
ten largest, drops the registered exclusions (D14) and any symbol whose current instrument
lacks the month-end bar or a close 120 days before the month-end close (D13), and keeps the
first five. Tests check the stablecoin list and the exclusions against
preregistration.json. `perpetual_of` maps a spot pair to its perpetual, including the four
contracts quoted per thousand coins.

**The controls' random numbers (section 8).** The control runs are registered on
`rng = numpy.random.default_rng(seed)`: C-H1 makes one call `rng.random(n) < 0.5` with n
the 158,821 development daily bars of the 115 pinned candidates, and C-H2 two calls
`rng.integers(540, M - 540, endpoint=True)` on one generator, BTC first, with M = 6,576.
numpy is neither in the standard library nor in the allowlist, so `sim.pcg64` reproduces
those calls bit for bit: SeedSequence seeding from an integer, the PCG64 generator with its
buffered half word, `Generator.random`'s doubles and `Generator.integers`' single draws by
Lemire's method, ported from numpy's own sources. Strategies reach it as
`norse_research.sim.default_rng`. Known answers generated once by numpy 2.5.5, in a virtual
environment outside the repository, are committed as `fixtures/numpy-rng-vectors.json`
with the generating script beside it. They cover the seeds 0, 1, 42 and 99 over 200,000
doubles (a SHA-256 of every one, plus sampled values), C-H2's draws, other 32-bit ranges
including heavy rejection, and interleaved calls. A further test compares every
registered seed, 0 to 99, with numpy directly when numpy is installed; CI installs none and
skips it. Ranges wider than 32 bits, arrays of integers and other dtypes raise an error,
since no control uses them and an untested port would not be known to be exact.

## Choices made

Where the registration was ambiguous or silent, the engine resolves it as follows. Each
is a reading of the registration, not a new parameter, and none changes a pass mark.

1. **The cost basis follows section 5.** The register's G1.9 line says "10 bps a side plus
   slippage". Section 5 fixes the slippage at 2 bps as a move of the execution price, and
   the fee at 10 bps of the notional at that price, so 12 bps a fill. Where the two could
   be read differently, section 5 wins, as section 15, item 18 says of G2.4's "net of 20
   bps". Aligning huginn's executor and research gateway defaults (5 plus 2 bps, 14 bps a
   round trip) is a change in the huginn repository and is not part of this change.
2. **"The change in target weight" is target minus current drifted weight,** so a traded
   instrument lands on its target, as section 6's band sentence ("traded to its target")
   requires.
3. **An exit sells the whole position.** The weight change divided by the fill price
   would leave held quantity times one minus mark over fill price unsold.
4. **A resizing sell never sells more than is held,** since section 6 says the book is
   never short; with a fill price far below the mark the formula could ask for more.
5. **A perpetual is accounted as a future:** no notional changes hands, its fee is paid
   from cash, its unrealised profit or loss counts in equity and is realised into cash as
   it is reduced. This follows "jointly margined" and "nothing is borrowed"; crediting a
   short sale's proceeds to cash would let them fund the spot leg. A consequence is that
   section 6's cash scaling binds slightly when both H2 coins enter on one bar from an
   all-cash book, as section 6 anticipates ("In H2 the same rule scales a coin's two legs
   together").
6. **Sells and buys within a bar** are read by effect: a unit whose legs only reduce
   positions is a sell and fills first, unscaled; a unit that opens or adds to any
   position is a buy, and all buys on a bar share one scale factor.
7. **Events at one instant apply in a fixed order:** fills on bars closing then, lapses,
   the mark, closes of instruments that stopped, funding, then the decision. Revised after
   the quant's review (finding 1): the first version put the closes and the funding before
   the mark, so a settlement stamped exactly at 00:00:00.000 (44 of 72 development month
   starts) or a D12 close counted in the day that ends there. At 2026-01-01 that would make
   the holdout run's 2025-12-31 return differ from the development run's, which has no
   such data; the harness then refuses the holdout run (section 11, item 5), and item 6
   allows no engine fix. With the mark first, everything stamped at a 00:00 instant, exact
   or jittered, counts in the day that starts there, and a day's return never depends on
   data stamped after it. Both readings honour "booked at that bar's close" and "on the
   quantity held at that instant"; only this one keeps the development prefix independent
   of the boundary, which a test checks. Fills on bars closing at the mark still count
   before it, since a position changes at its fill bar's close.
8. **A bar closes at its open plus its interval** (the kline's `close_time` plus 1 ms).
   The funding mark is the latest 15-minute bar closed at or before `calc_time`, read
   literally, with no tolerance; a settlement published a few milliseconds before the hour
   would use the bar before. The archive's jitter seen so far is after the hour.
9. **The sizing mark at a four-hour decision** is each instrument's four-hour close then;
   at a daily decision it is the daily close, the same as the equity mark.
10. **A four-hour decision is skipped** when any instrument it holds or would trade lacks
    its four-hour bar closing then; the registration says "the four-hour decision at its
    close is skipped" without naming whose bar, and an exchange outage removes every pair's.
    An order's lapse deadline stays t plus four hours even if the next decision is skipped.
11. **A missing spot daily bar ends the instrument and is never carried forward;** only a
    perpetual's is. Section 5's carry-forward for an exchange-wide outage and section 4's
    end of an instrument would otherwise conflict; section 4 notes no such outage happened
    in development. So that a conflict cannot pass silently, a run stops when BTCUSDT and
    ETHUSDT spot both stop at one instant (quant review, finding 7), rather than close
    every instrument at its low.
12. **The universe selection looks no bar ahead.** An instrument whose last bar is the
    month-end bar has that bar and is eligible, as section 6, step 2 reads ("does not have
    the month-end daily bar"); section 4 then closes it and its slot holds cash for the
    month. The quant's review (finding 2) set out the other reading, that a stopped
    instrument is not "current" and the next eligible pair takes the slot, and asked for a
    ruling. The lead role decided to keep the literal reading: the frozen registration's
    text controls, and the other reading needs a look-ahead beyond D12's, which section 4
    allows "only to learn that a bar is the last one". The owner has not ruled separately.
    A test pins the behaviour. Section 4 says three candidates end on 2026-04-01, so this
    may bind in the holdout.
13. **The short half of D12 has nothing to close,** since no spot position is ever short.
    A carry pair whose spot instrument stopped is not defined by the registration (H2
    "never holds one leg without the other"); it never happens to BTC or ETH in either
    period, and the engine stops the run rather than invent a rule.
14. **Volatility uses returns between consecutive bars of the instrument.** On four-hour
    bars a missing bar makes one return span eight hours; this affects H1-06 only, before
    2020-02-19.
15. **A trailing funding window is covered** when its first settlement is within one
    declared interval of the window's start, its last within one interval of its end, and
    no two consecutive settlements are further apart than their declared interval (the
    rule `formats.funding_gaps` uses for the gaps the manifest records). A series that
    starts exactly one interval inside the window covers it, since all 21 settlements are
    there.
16. **Paper trading's feed** delivers a bar at its close, a settlement 60 seconds before
    its `calc_time` (when section 5 counts it known; it accrues at `calc_time`), and a
    notice when an instrument stops. A live feed would take that notice from Binance's
    delisting announcement; the replay derives it from the recording.
17. **The controls' generator is numpy's, reimplemented.** Section 8 names numpy's calls,
    and a different generator with the same distribution would draw different signs and
    shifts, so it would not be the registered control. The package stays standard
    library only by porting exactly what those calls compute, held to numpy's own output.

## The data contract, and what is missing

The engine reads `formats.Kline` and `formats.Funding` rows by the development manifest's
series names: spot klines at 1d, 4h and 15m, which the manifest pins for all 102
development candidates; BTC and ETH perpetual klines at 1d, 4h and 15m; and funding for the
102 perpetuals. A test checks the candidates' and both coins' series against the
committed manifest. Fifteen-minute bars are read lazily a month at a time, since all the
candidates' would not fit in memory, and are never shown to a strategy. The archive holds
no mark-price series, as section 5 notes; the 15-minute close stands in for it.

Two guards keep a strategy from loading data that would break the registered rules
(quant review, finding 5). Every series is read from its first archived row: a later
start would start each instrument late, so a pair would miss D13's close 120 days before
a month end it really has, and its volatility would leave out returns section 6 counts
("over every return of the instrument"); `Market.from_view` refuses such a start, and
refuses a load whose first row is later than its declared run. And `backtest` refuses a
market read to any horizon but the window's end, since an instrument ends where its data
stops before the horizon (section 4).

Series spans come from the data-loading layer, not from the strategies (quant review,
finding 3). The loader refuses a range its data does not span and a month the manifest
pins no file for, so the engine must read each series in its archived runs, split where
whole months are missing (three spot series, FTTUSDT's ten-month suspension among them).
`DatasetLoader.coverage` derives those runs from the committed manifests' metadata
alone, each series' first and last row, its recorded gaps and its pinned months, and the
runner's `DataView` exposes it as `coverage(series)`. A run is split only at a recorded
gap that spans a month with no pinned file; a gap inside a pinned month stays inside one
run and is crossed. In the holdout run the loader holds the holdout manifest beside the
development one, so the coverage runs on into the holdout from that manifest's metadata
without reading a holdout file, and in a rehearsal it is the development coverage. No
strategy commits a span constant, and a wrong span is a fix to the loader, which section
11, item 6 permits between a candidate's selectable run and its holdout run.

The gaps a run crossed reach its ledger entry (finding 4; section 5: "a gap the
committed manifest records is crossed and listed in the run's ledger entry"). The loader
records each recorded gap a load accepts, the runner returns them beside the returns,
and the harness writes them to the result entry as `crossed_gaps`, a sorted list of
`[series, after, before, missing]` in the manifest's own form. The harness first checks
each against the registered manifests and fails the run on any it cannot find there.
Replay accepts the field only in that shape, and a failed run lists none. The engine
still collects the same gaps itself (`Market.crossed_gaps`), which the tests use; the
ledger takes the loader's.

Strategies may use the engine but not change it (security review round 3). The source
check refuses assigning to or deleting an attribute or item of any imported name, reading
one's private attribute, and subclassing a class imported from this package, so no line in
a hypothesis file can rebind the engine's fill, cost or funding path for its run (E1). It
also refuses `import norse_research.<x>` and the bare name `norse_research`, since the
package root reaches every module the runner has already imported, the loader, the store
and the runner itself; strategies write `from norse_research.sim import ...` instead
(E2). The check reads names, not values, so a module bound to a second name and changed
through it is not caught here. That is deliberate evasion visible in the committed diff,
and the runner's audit hook stands behind it, as for every other source rule. Genesis,
and replay of a genesis entry, refuse harness paths that do not cover all of
`research/norse_research`, so the engine is always inside the harness tree digest each
open entry records.

huginn's executor and research gateway cost defaults, which G1.9's "Done when" also
names, live in the huginn repository; a separate huginn pull request aligns them.

No drawdown breaker is modelled: the registration has none, and the register's risk about
huginn's replay breaker concerns huginn, not this engine.

Two limitations are stated rather than changed. When both H2 coins enter from cash, the
scaling leaves cash at about zero, and a negative funding settlement then takes it below
zero at no cost, although section 5 says nothing is borrowed (quant review, finding 6);
returns differ only by the borrowing cost that is not charged, and H2-06 is always in
that state. And the backtest values a settlement at the latest perpetual 15-minute close
within three days, failing beyond that, while paper trading takes the latest of any age
(finding 8); neither BTC's nor ETH's perpetual 15-minute series has a gap in development.
