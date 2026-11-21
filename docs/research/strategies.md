# The registered strategies (G2.2, G2.3)

Owner: technical. Written 2026-11-21. The code is in
[research/norse_research/strategies](../../research/norse_research/strategies), one module per
hypothesis, and the tests are `test_strategies_h1.py`, `test_strategies_h2.py` and
`test_strategy_pins.py` in [research/tests](../../research/tests), run by CI's `research` job.
Everything here was computed on synthetic data. No strategy, and no part of one, has been run
on market data, development or holdout, and nothing opened the dataset store.

These modules are what the genesis entry pins: one module and function for every registered
id. They implement sections 3 to 9 of [PREREGISTRATION.md](../PREREGISTRATION.md) as the
signed text and [preregistration.json](../preregistration.json) register them, on the engine
of [simulation-engine.md](simulation-engine.md), which they reach only through
`norse_research.sim`. Each rule in the code carries a comment naming its section.

## Layout and pins

H1's tree paths are `research/norse_research/strategies/h1`, holding `trend.py`, which runs
H1-01 to H1-08, the benchmark B-H1 and the control C-H1. H2's tree paths are
`research/norse_research/strategies/h2`, holding `carry.py`, which runs H2-01 to H2-06, B-H2
and C-H2. That is where `gitledger.registered_ids` places each id: a benchmark by its window,
a control by its base. The two folders sit inside the harness's paths, which genesis allows,
and do not overlap each other, so neither module imports the other; what both need is in the
engine. Each module declares `CONFIGURATIONS`, `PARAMETERS` and `DEFAULTS` as literals, and
its pinned function is `run(configuration, data_view)`, returning one `(day, daily_return)`
per day of the resolved window.

[pins.example.json](../../research/tests/fixtures/pins.example.json) is an example of the
genesis step's `--strategies` input with those paths. It is not the real genesis input, which
the owner's genesis step supplies. A test runs the real genesis step on a temporary
repository holding this package with those pins and tree paths, then resolves every id
through the harness's own `load_strategy` and `Registration.resolve`, and checks that each
grid configuration resolves to exactly its registered parameters, each control to its
base's, and each benchmark to none.

`DEFAULTS` is empty in both modules. Every value a grid configuration or a control uses is in
its registered configuration, which the harness resolves from the pinned
preregistration.json and hands to the module frozen, so the modules carry no copy of the
grid. The benchmarks' rules have no parameters; the few constants their sections state (one
fifth, halves, BTC and ETH) are named in code with their section.

## The configurations

**H1-01 to H1-08** (section 6). At each month-end decision, the universe is ranked from every
spot daily series the run holds by quote volume over the 30 daily bars closing at the month
end, through the engine's `rank_top` (G0.6's `rank_month_ends`, stablecoin and fiat bases
removed, the ten largest), and selected by `select_universe` (D14's exclusions, D13's 120-day
close and the month-end bar, the first five, cash for a missing one). At every decision each
live member's signal is the mean of its votes, one per registered lookback, each 1 when its
close at t over its close L days earlier, minus one, is above zero. The target weight is
(1/5) x s x min(1, 0.20 / sigma), sigma the engine's EWM volatility with span 60 days
annualised by the square root of 365, or (1/5) x s for H1-08; the engine trades it outside
the registered band (25%, or 10% for H1-04 and 50% for H1-05) and sets every other holding to
zero. H1-02 and H1-03 change only the lookbacks. H1-06 decides at every four-hour close with
lookbacks of 120, 360 and 720 bars read as 20, 60 and 120 calendar days, a span of 360 bars
and annualisation by the square root of 2,190, while the universe still changes only at the
month-end 00:00 decision. H1-07 sets a member's signal to 0 while its perpetual's funding
over (t - 7 days + 60 s, t + 60 s] sums above 0.63%, and applies only where the member has a
perpetual (its own symbol or the one quoted per thousand coins) whose settlements cover the
interval without a gap.

**B-H1** (section 8). At each month-end decision, the same universe at one fifth of equity each
with a band of zero; no decision in between.

**C-H1** (section 8). H1-01, with votes read from a synthetic close series per instrument that
starts at the instrument's first close and compounds its actual daily returns, each day's
sign flipped where its draw from `default_rng(seed).random(n)` is below one half. The n draws
map one to one onto the development daily bars of the 115 pinned candidates, ordered by symbol
and then date. Volatility, sizing, the universe and the profit and loss use actual prices.

**H2-01 to H2-06** (section 7). At each daily decision, each coin's F is the sum of its
perpetual's funding rates over (t - L days + 60 s, t + 60 s]. A flat coin enters above the
entry threshold and a coin in carry exits below the exit threshold, the thresholds being
m x 0.48% x L / 28; entry buys half of equity of spot over the spot fill's reference price and
sells the same quantity of the perpetual on the same bar, which the engine does. H2-02 and
H2-03 change the multiples, H2-04 exits only when F is below zero, H2-05 uses three days, and
H2-06 holds carry from the first decision on and never exits.

**B-H2** (section 8). BTC and ETH spot at half of equity each at the first decision of the
window and again at each month-end decision, band zero, no decision in between.

**C-H2** (section 8). H2-01, with each coin's F summed over its development funding series
shifted circularly by k settlements, k drawn by `rng.integers(540, M - 540, endpoint=True)`
on one generator, BTC first. Each settlement keeps its own time; only its rate moves, to that
of index (i + k) mod M. Funding received and the profit and loss use the actual series, which
the engine accrues from the data.

## What the tests hold

Each signal rule against values worked out by hand from synthetic closes: the vote's
lookback to the exact bar (a close one day either side would change it), its boundary (a
ratio of exactly 1 votes 0), the volatility-scaled weight against pandas' `adjust=True`
formula written in closed form, the slot cap, the four-hour conversion (a spike exactly 20
days back changes the vote, one 20 bars back would not), a skipped four-hour decision, and
the funding filter's ceiling, interval ends, per-thousand perpetual, missing perpetual and
gap. The dated universe: the stablecoin removed before ranking, the leveraged and off-chain
tokens dropped, a pair one day short of 120 days, a pair without its month-end bar, volume
outside the 30 days ignored, the cap at the ten largest leaving a slot in cash, and a halted
member's slot held in cash until the month end. Each H2 configuration's entries and exits on a
funding series built so that each threshold is crossed on a known day, the equal quantities
of the legs, funding accrued per settlement, both legs lapsing together, and the thresholds
equal to the registered figures. Each benchmark's rebalancing, with the notionals worked out
by hand. The controls' random numbers against the vectors numpy 2.5.5 generated for G1.9:
for seed 0, the 158,821 draws below one half 79,640 times and every sampled index landing on
the symbol and day the order of symbol then date puts it on; C-H2's k for the four committed
seeds with M = 6,576.

Both drivers give identical orders, fills, lapses, accruals, skips, marks and returns for
H1-01, H1-06, H1-07, B-H1, H2-01, H2-05, H2-06 and B-H2 on seeded synthetic markets that hold
a halt, a pair that returns as a new instrument, a too-young pair, an excluded token, missing
and empty 15-minute bars, a missing four-hour bar, a funding gap and a missing day of perpetual
klines. A run whose data stops at a date returns exactly the first days of a longer run, the
rule section 11, item 5 applies at 2026-01-01. Each module also runs in the runner's fresh
interpreter under its audit hook, on the frozen configuration the runner passes, and returns
exactly what it returns in process. H2's pinned `run` itself runs there on the registered
loader over a synthetic store and manifest, taking every series' runs from
`DataView.coverage`, and the recorded gap its fill search crosses comes back as the result's
`crossed_gaps`.

## The data seam

The quant's review of G1.9 (finding 3) requires each series' spans, its archived runs split
where whole months are missing, to come from the data-loading layer and not from constants in
strategy code. The engine now provides them: the loader derives each series' runs from the
committed manifests' metadata (both manifests for the holdout run, joined at the boundary
without reading a holdout file), the runner's `DataView.coverage(series)` exposes them, and
`Market.from_view` takes series names and asks for each one's coverage. So both modules commit
no span, and a span error is a data-loading fix (section 11, item 6). The recorded gaps a
run's loads cross reach the ledger without any strategy code: the loader records them, the
runner returns them beside the returns and the harness writes them to the result entry as
`crossed_gaps` (section 5, Gaps).

**H2 is wired end to end.** Every series it reads is named by the registration (BTC's and
ETH's spot and perpetual bars and their funding), so `run` builds its market straight from
those names through the data view.

**H1 still has one open seam.** Section 6, step 1 ranks "USDT spot symbols ... delisted symbols
included", every pinned spot daily series, so H1 needs the names of the series the run's
manifests pin, both manifests for the holdout run so that holdout month ends rank over the
holdout's daily series. The `DataView` gives a named series' coverage and rows but no listing
of names, and committing the list would be the same kind of constant the spans were.
`pinned_series(data_view)` in `trend.py` is the one function that needs it; until the data
view lists the pinned series it refuses with a message naming this section, so no H1, B-H1 or
C-H1 run can load data, or be ledgered, by accident. Everything after it, `load_market(
configuration, data_view, names)` and `simulate`, is tested on synthetic rows through a
stand-in `DataView`. The alternative reading, ranking only the 115 candidates (which agree
by construction, since they are the union of every top ten), would not need the listing; it
is a change to choice 1 below and is left to the lead.

What each configuration reads: H1 reads every spot daily series (619 in the development
manifest, for the ranking), every spot 15-minute series lazily for fills, every spot
four-hour series for H1-06 and every perpetual's funding for H1-07. H2 reads BTC's and ETH's
spot and perpetual daily and 15-minute series and their funding; B-H2 only the spot. Memory
for H1-06, which holds 107 four-hour series whole, has not been measured, since that would need
the real data.

## Choices made

Where the registration was ambiguous or silent, the modules resolve it as follows. Each is a
reading of the registration, not a new parameter.

1. **The ranking covers every spot daily series the run holds,** as section 6, step 1 reads
   ("USDT spot symbols ... delisted symbols included"), not only the 115 candidates. The two
   agree by construction, since the candidates are the union of every top ten, but the
   literal reading does not rest on that.
2. **Only the bars in the 30-day window are passed to the ranking.** `rank_month_ends` reads
   only those, so the result is the same; it keeps the monthly ranking cheap.
3. **A lookback is read in calendar time,** t minus L days, at both bars, as section 6 says of
   four-hour bars ("in the same calendar days"). Where a four-hour bar is missing at that
   instant (outages before 2020-02-19), the latest four-hour close before it within the
   instrument is used, as section 5's marks carry a last close forward. A daily lookback
   always finds its bar, since a missing daily bar ends the instrument and D13 guarantees 120
   days of it.
4. **A skipped four-hour decision changes nothing, the universe included.** Section 5, Gaps
   item 2 skips "the four-hour decision at its close" without naming whose bar. When a live
   member lacks its bar, the strategy returns no decision; when a held instrument lacks it,
   the engine skips before asking (engine choice 10). If that ever happened at a month-end
   00:00 decision, the previous universe would stand for the month. The committed coverage
   metadata records no missing four-hour bar closing at 00:00 on a first, in either period.
5. **H1-07's filter counts only settlements inside the member's instrument** (section 4: "A
   funding settlement applies to the spot instrument whose dates contain it"); D13 makes every
   member at least 120 days old, so this never binds. The filter is evaluated only where the
   signal is above zero, which changes nothing, since it can only set the signal to zero.
6. **C-H1's order of symbol then date is Python's text order** (so `1000SATSUSDT` comes first).
   Each instrument-day's draw belongs to that day's bar; the first bar of an instrument has a
   draw but no return, so its draw is unused. Development bars are those opening before
   2026-01-01. The 115 candidates are committed as a literal list, tested against G0.6's
   file; they are the registered set, not a span. Eight of them (CHIP, FOGO, NIGHT, RE, SENT,
   SNDKB, XAUT, ZAMA) have no development bars and take no draws. By the coverage metadata
   the other 107 hold the 158,821 bars G1.9's vectors use, and the development manifest pins
   all of their daily series; n is counted from the bars the run loads, so the seam must
   supply every one of them.
7. **C-H2's shift moves rates, not times.** Each settlement keeps its `calc_time` and interval,
   so the trailing interval and when a value is known are the real rule's; M counts the
   development settlements, those before 2026-01-01.
8. **H2's thresholds are both the rule and the figures.** The module computes m x c x L / 28
   with the engine's `carry_thresholds` and refuses a configuration whose registered
   `entry_threshold` and `exit_threshold` differ; for all five they are equal to the last bit.
9. **H2's F needs no coverage.** Section 7 sums the published rates in the interval, and a
   missing settlement adds nothing (section 5, Gaps item 5); only H1-07's filter has a
   coverage condition.
10. **H2's state is the book's.** A lapsed entry leaves the coin flat and the next decision
    re-evaluates it (section 5: "the coin's state ... is unchanged"). H2-06 asks for carry at
    every decision, so "enter at the first decision" retries until it fills and then holds.
11. **B-H2's first decision is the window's first,** 2020-01-08 in development; a lapsed
    benchmark order is not retried until the next month end, since section 8 allows "no
    trading between month ends".
12. **A configuration runs only as its registered kind,** and only the holdout run's window may
    end after 2026-01-01; anything else is refused before data is read (section 9; section
    11, item 6).
13. **Nothing patches the engine.** The modules import the engine only as `from
    norse_research.sim import ...` and `Funding` as `from norse_research.formats import
    Funding`, read no private attribute, subclass nothing from the package, and never
    assign to or delete anything they import; both pass the source check's round 3 rules.
