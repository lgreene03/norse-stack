# Pre-registration of H1 and H2 (G0.7)

**Status: draft of 2026-11-11 for the CTPO's review and the owner's signature. It binds
nothing until the commit holding it is tagged `prereg-v1`.** Owner: quant. The CTPO
approves; the owner signs. The goal is G0.7 in [ITERATION_GOALS.md](ITERATION_GOALS.md);
the hypotheses and the gate are set out in [NEXT_ITERATION.md](NEXT_ITERATION.md).

This document fixes, before any trial is run and before any price, return, signal or
performance figure has been computed on real data, everything that decides whether H1 or
H2 passes: the data and its periods, the walk-forward design and the out-of-sample
windows, the universe and instrument rules, the signals, sizing, rebalance and fill rules,
every configuration to be tried, the trial budget and how N is counted, the costs, the
benchmarks and control runs, every pass criterion with its formula, the rule that picks
the single holdout candidate, and what each criterion implies in net Sharpe.

**The machine-readable form governs.** [preregistration.json](preregistration.json)
holds the same rules in a form the ledger (G1.5) and the seal (G1.6) load and hash. If it
and this prose ever disagree, the JSON file governs. The genesis entry of the trial ledger
records the SHA-256 of both files and the `prereg-v1` commit.

## Contents

1. What was seen before this was written
2. Data and periods
3. Walk-forward design and out-of-sample windows
4. Instruments, halts and delistings
5. Fills, marks, costs and gaps
6. H1, dated-universe trend
7. H2, funding carry
8. Benchmarks, control runs and cost sensitivity
9. What a trial is, how N is counted, and the budget
10. Pass criteria
11. The holdout candidate, the single holdout run and the verdict
12. The net Sharpe each criterion implies
13. What the gate cannot detect
14. Decisions this rests on
15. Where NEXT_ITERATION.md and the register disagree with this
16. Change control and signatures
17. References

## 1. What was seen before this was written

A pre-registration is worth only as much as its blindness, so this states what its author
had seen. No price, return, signal or performance statistic was computed on development
or holdout data for this document, and no holdout file was read. What was read is coverage
metadata already published in the repository: first and last dates, row counts, gaps,
off-grid bars, listing dates and the volume ranks of each development month end, from
[data-coverage.md](research/data-coverage.md), [datasets.md](research/datasets.md),
`research/coverage/universe-candidates.json` and `research/coverage/series-coverage.json`.
Two checks were run on that metadata alone (no volume figure, only published ranks and
dates): that at every development month end from 2018-04 to 2025-11 at least five of the
ten largest pairs are eligible under the rules in section 6, and how many month ends
have a too-young pair in the raw top five (28 of 92, matching G0.6).

**Disclosure about the holdout universe.** G0.6's coverage report names the pairs that
reach a top ten only at holdout month ends. Three of them are not crypto assets in the
ordinary sense: PAXGUSDT and XAUTUSDT (gold tokens) and SNDKBUSDT (a tokenised share).
Everyone working on this iteration has seen those names, and 2026 gold prices are public
knowledge that every model involved has been trained on (decision D4 already records who
has seen 2026 prices). The universe exclusions in section 6 were written knowing these
names exist. They are defined by what a token is, apply identically to both periods and
change nothing in development, and the reasoning is recorded in decision D14 so a reader
can judge it. The arithmetic in section 12 uses only synthetic numbers and stated
assumptions.

## 2. Data and periods

All data is Binance's public archive, data.binance.vision, under its licence (CC BY-NC-SA
4.0: research and paper trading only). No API key, account, paid feed or trading endpoint
is used. Every load goes through the loader of G1.1 against a committed manifest:

| Period | Dates | Manifest | SHA-256 |
|---|---|---|---|
| Development | up to 2025-12-31 | `research/manifests/binance-vision-development.json` | `c27499e97edd1d2288a7d38a7f0a890f9be108919dda22f7e3c9c119e725a55d` |
| Holdout | 2026-01-01 to 2026-10-31 | `research/manifests/binance-vision-holdout.json` | `806e1d6acdd90c5454297b78c42c37cfb6f522ebcb81f01b773ba66b9308ea77` |

The archive is taken as published. G1.1 found that before 2022-04-14 some archive bars
carry fewer trades than Binance's REST API reports (about ten daily bars each for BTC and
ETH); they are not patched from the API, because the API cannot be pinned by a manifest,
covers only pairs still listed, and would let the data change after this document is
signed. The effect is small and the same for every configuration.

The owner decided on 2026-11-11 that the holdout files are removed from the local mirror
until the single unseal, then fetched again and verified file by file against the
committed holdout manifest. A holdout run never starts on a file that fails that check.

## 3. Walk-forward design and out-of-sample windows

**Nothing is fitted.** Every quantity H1 and H2 use is either a registered constant or
computed causally from data strictly before the decision it informs: lookback returns,
the exponentially weighted volatility, the volume ranking and trailing funding. So the
walk-forward is an expanding window anchored at the start of the archive, with calendar
years as its test folds, in which each fold's training window supplies only the history
those quantities read. No parameter is chosen on any fold. The joined walk-forward
out-of-sample series of a configuration is therefore its daily net returns over the
whole window below.

"Out of sample" here means out of sample for every fitted quantity, of which there are
none. It does not mean unseen: the configurations were chosen by people who know how
crypto behaved from 2018 to 2025. The one choice made in sample is which configuration is
best, and the deflated Sharpe ratio (with N from the ledger), the PBO and the single
holdout run exist to charge for exactly that choice.

**The windows**, exactly. A daily decision is taken at 00:00 UTC on the close of the
previous day's bar, and the return of UTC day D is the equity marked at 00:00 on D+1
divided by the equity marked at 00:00 on D, minus one.

| Window | First decision | Last decision | Return days | Days | Years |
|---|---|---|---|---|---|
| H1 (every configuration) | 2018-05-01 00:00 | 2025-12-31 00:00 (20:00 for four-hour bars) | 2018-05-01 to 2025-12-31 | 2,802 | 7.67 |
| H2 (every configuration) | 2020-01-08 00:00 | 2025-12-31 00:00 | 2020-01-08 to 2025-12-31 | 2,185 | 5.98 |
| Holdout (the one candidate) | 2026-01-01 00:00 | 2026-10-31 00:00 (20:00 for four-hour bars) | 2026-01-01 to 2026-10-31 | 304 | 0.83 |

Years are days divided by 365.25, as in G0.6. H1 starts at the 2018-04 month end, the
first at which every pair in the dated universe has a close 120 days earlier (G0.6). H2
starts on 2020-01-08, the first day with a full seven days of archive funding (the
archive's funding begins on 2020-01-01). Every configuration of a hypothesis shares its
window, so the H2 configuration with a three-day lookback starts on 2020-01-08 too.

**Development trading stops at the last development close.** No development run takes a
decision at 2026-01-01 00:00 (the close of 2025-12-31), because its fill would fall in the
holdout, which the seal refuses. Positions open at the end of a window are marked at the
last close and never closed, so no exit cost is charged. The 2025-12 month-end universe
belongs to the holdout run.

**The minimum backtest length condition is met with room.** G0.6 showed that the 12, 12
and 6 split stands at a target Sharpe of 1 only if every trial that can be chosen for the
holdout has at least 4.30 years of joined out-of-sample daily returns, which would have
capped a first training window at about 1.6 years. With no fitted parameters there is no
training window to pay for: H1 gets 7.67 years and H2 5.98, which the minimum backtest
length rule (Bailey, Borwein, López de Prado and Zhu 2014) says would support 202 and 78
trials at a target Sharpe of 1. The H1 funding-filter configuration shares H1's window,
because where no perpetual exists its filter does not apply (section 6).

## 4. Instruments, halts and delistings

**Symbols map to instruments by continuity.** An instrument is a maximal run of
consecutive daily bars of one spot symbol in the pinned archive. A missing daily bar ends
the instrument, and later bars of the same symbol form a new instrument with no history.
Instruments are never linked across symbols: renames, redenominations, migrations and
forks start afresh. In development this gives:

* LUNAUSDT is two instruments: the original Terra to 2022-05-13 and the new Terra from
  2022-05-31. The original's continuation, LUNCUSDT from 2022-09-09, is a third instrument
  with no history of its own before that date.
* FTTUSDT is two: to 2022-11-15, and from 2023-09-22 after its ten-month suspension.
* BCCUSDT is two (to 2018-11-15, and one day on 2018-11-20); BCHABCUSDT and BCHUSDT are
  separate instruments although BCH continues BCHABC.
* ETHBULLUSDT and ETHBEARUSDT split too, but are excluded as leveraged tokens (section 6).

No exchange-wide outage removed a daily bar in development, so every missing daily bar is
specific to its pair. The rule needs no hand-typed table of corporate actions, which the
archive does not publish and which would be written with hindsight. Its cost is that a
renamed coin re-enters H1's universe only once its new symbol has 120 days of history.

A spot pair's perpetual is the USDT-margined perpetual on the same coin, including the
contracts quoted per thousand coins (1000PEPEUSDT, 1000SHIBUSDT, 1000FLOKIUSDT and
1000LUNCUSDT), whose funding rates compare directly because a rate is per unit of
notional. A funding settlement applies to the spot instrument whose dates contain it.

**Halted and delisted pairs (decision D12).** When an instrument ends before the end of
the window being run, a long position in it is closed at the **low of the instrument's last
daily bar**, then charged slippage and the fee as any sell, and booked at that bar's close;
a short position is closed at that bar's high. The slot holds cash until the next month-end
universe update. This never invents a price, since the low was traded, and it errs
against the strategy: no position is assumed to have escaped at the last print of a
collapse. It is not a worst case. A coin can end worth less than its last day's low, and
the rule ignores anything holders received afterwards (a successor token, a fork, an
airdrop), which also errs against the strategy. The rule looks one bar ahead only to
learn that a bar is the last one; it uses no price the strategy could not have traded.
It applies in the holdout run in the same way (three candidates end on 2026-04-01).

## 5. Fills, marks, costs and gaps

**Fills.** An order decided at time t fills at the volume-weighted average price of the
15-minute bar that opens at t, computed as the bar's quote-asset volume divided by its
base volume. If that bar is missing or has no volume, the next 15-minute bar with volume
is used; if none trades within 24 hours, the order lapses, the position is unchanged and
the next decision re-evaluates. A position changes at the close of its fill bar. A
perpetual leg fills on the perpetual's own 15-minute bar by the same rule.

**Costs: 10 bps a side plus the executor's slippage.** The slippage model is huginn's
executor's: the execution price is the reference price moved against the order by
`SlippageBps`, set to 2 bps, with the optional square-root impact term off
(`SlippageImpactK = 0`). The fee is 10 bps of the traded notional at the execution price.
So every fill of every leg, spot or perpetual, entry or exit, costs 12 bps: 24 bps a round
trip on one leg, and 48 bps to open and close an H2 carry position, which trades four
times. The impact term is off because it has never been calibrated and turning it on would
add an unregistered parameter; the top-five pairs and BTC and ETH perpetuals trade far
more than these paper sizes. Ten bps is Binance's retail spot taker fee and above its
retail perpetual taker fee, so the perpetual leg is costed conservatively. Cash earns
nothing, nothing is borrowed, and no fee discount (BNB) or rebate is assumed. G1.9 aligns
huginn's current defaults (5 bps fee plus 2 bps slippage a side, 14 bps a round trip, in
the executor and the research gateway) to these figures before any trial.

**Marks.** Equity is marked at 00:00 UTC on each instrument's daily close, using the
perpetual's own daily close for a short leg. If an exchange-wide outage ever removes a
daily bar, the last close is carried forward; none did in development.

**Funding** is accrued per published settlement at its published rate, at the settlement's
published `calc_time`, on the quantity held at that instant, valued at the close of the
latest perpetual 15-minute bar at or before it (the archive holds no mark-price series in
the manifests; the difference is a fraction of a basis point of a rate that is itself a
few basis points). Never a daily rate times three: 41 candidate perpetuals settle every 4,
2 or 1 hours for some of their history. A settlement counts as known at decision time t if
its `calc_time` is at most t plus 60 seconds, which absorbs the archive's millisecond
jitter around the settlement instant.

**Gaps, off-grid bars and outages.** Every load passes `accept_recorded_gaps=True`, so a
gap the committed manifest records is crossed and listed in the run's ledger entry; a gap
the manifest does not record fails the run, and that trial entry still counts in N. Within
a run:

1. A missing 15-minute bar delays a fill as above.
2. A missing four-hour bar (outages before 2020-02-19) means the four-hour decision at its
   close is skipped.
3. A missing daily bar ends an instrument (section 4).
4. Bars published off the interval grid are never used (the loader does not return them)
   and their slots count as a gap; rows published twice identically are counted once.
5. A missing funding settlement accrues nothing; the H1 funding filter does not apply
   while its trailing window holds a recorded funding gap (SLPUSDT lacks 203 settlements in
   2025).
6. In the holdout, the single missing day of BTC and ETH perpetual klines (2026-10-28) is
   handled by rule 1 and the mark rule above: fills wait and the last close is carried
   forward; funding still accrues, since the funding series has no gap.

## 6. H1, dated-universe trend

Long or flat, spot only, on a universe dated at each month end so that no coin is chosen
with hindsight.

**The universe.** At each month-end close (the decision at 00:00 UTC on the first of the
next month):

1. Rank USDT spot symbols by quote volume over the 30 daily bars closing at the month end,
   delisted symbols included, after removing the 27 fiat and fiat-pegged stablecoin base
   assets G0.6 removed (`STABLE_OR_FIAT` in `research/norse_research/universe.py`), exactly
   as `rank_month_ends` computes it, and take the ten largest. Volume is summed per symbol,
   as G0.6 ranked, so this top ten is the candidate set G1.1 pinned.
2. From those ten, drop any symbol in an excluded category (decision D14, below) and any
   whose current instrument does not have the month-end daily bar or does not have a
   daily close 120 days before the month-end close (decision D13).
3. The universe for the month is the first five remaining in rank order. If fewer than five
   remain, the empty slots hold cash for the month. At every development month end at
   least five remain, so this cap never binds in development.

The cap at the ten largest is a rule of its own, fixed for a practical reason: the holdout
manifest pins four-hour and 15-minute bars only for the pairs in a top ten, and the holdout
files are off the machine until the unseal, so a rule that could reach the eleventh pair
could fail at the one permitted unseal. It also makes the universe's data needs bounded
by what is pinned.

**Exclusions (decision D14).** By category, never by name: fiat and fiat-pegged
stablecoins (removed before ranking as above, and any other found in a top ten);
leveraged tokens, whose value is a leveraged multiple of another coin's return (Binance's
UP, DOWN, BULL and BEAR tokens); tokens designed to track an asset outside crypto
(commodity-backed tokens, tokenised shares, funds or other securities); and wrapped,
bridged or liquid-staking forms of another coin. Applied to the 115 pinned candidates this
drops ETHBULL and ETHBEAR (leveraged; ranked 6th and 10th at the 2020-03 month end, the
only time any excluded category reaches a development top ten) and PAXG, XAUT and SNDKB
(off-chain assets; each reaches a top ten only in the holdout). The other 100 development
candidates are crypto assets. Before signing, the CTPO checks the ten other holdout-only
candidates (BANK, CHIP, DEXE, FOGO, NIGHT, RE, SENT, TAO, TUT and ZAMA) against the
categories from each token's own published description, and any that falls in one is
added to the list in [preregistration.json](preregistration.json) before the tag.

**Pairs younger than the lookback (decision D13).** At 28 of the 92 development month ends
from 2018-04 to 2025-11, a pair in the raw top five has no close 120 days earlier
(BCHSV in 2018-11, PEPE and ARB in 2023-05 and TRUMP in 2025-01, for example). Such a pair
is not eligible, and the next eligible pair by volume takes its place. The 120-day vote is
then always defined.

**Signal.** At each decision t, for each universe member and each lookback L in the
configuration, the vote is 1 if the instrument's close at t divided by its close L days
earlier, minus one, is above zero, and 0 otherwise. The signal s is the mean of the votes:
0, 1/3, 2/3 or 1. This is the long-or-flat form of "an equal blend of the signs": each
lookback runs a third of the slot, long when its return is positive and flat otherwise.
The other reading, the positive part of the mean sign (which maps two positive votes out
of three to 1/3 rather than 2/3), was considered and is not tried.

**Sizing.** Each of the five members gets a slot of one fifth of equity. Volatility σ is the
square root of the exponentially weighted mean of squared daily simple returns,
not demeaned, with span 60 days (α = 2/61) and weights (1 − α)^j over every return of the
instrument (the pandas `ewm(span=60, adjust=True)` convention), annualised by the square
root of 365. The target weight, as a fraction of equity marked at the decision, is

    w* = (1/5) × s × min(1, 0.20 / σ),

so each slot is sized to 20% annualised volatility and never exceeds its fifth of equity
("at most 1x per coin"): the book is never levered and never short. The rest is cash.
Pairs outside the universe have a target of 0. The configuration without volatility
scaling uses w* = (1/5) × s.

**Rebalancing.** Daily, at every 00:00 UTC decision; the universe changes only monthly.
An instrument is traded to its target only when the absolute difference between its
target and its current (drifted) weight exceeds 25% of the target, which means entries and
exits always trade and only resizing is banded.

**Four-hour bars** (H1-06) apply the same rule at every four-hour close (00:00, 04:00 to
20:00 UTC), with lookbacks and the volatility span in the same calendar days (120, 360 and
720 bars; a span of 360 bars), volatility annualised by the square root of 2,190, fills on
the 15-minute bar opening at each decision, and the universe updated only at the month-end
00:00 decision. It isolates one change, how often the same signal is read.

**Funding filter** (H1-07): a member's signal is set to 0 while the sum of its perpetual's
published funding rates with `calc_time` in the seven days up to the decision exceeds
0.63%. The threshold is three times Binance's baseline funding (0.01% per eight hours when
the premium is zero, 0.21% over seven days), about 33% a year: funding that high means
leveraged longs are crowded, which is when trend longs are most exposed to a sharp
reversal. The opposite direction (long only while funding is positive) was considered and
not tried, because in a long-flat trend book funding is usually positive whenever the
signal is long, so that filter would rarely bind. Where a member has no perpetual, or its
perpetual's settlements do not cover the seven days without a recorded gap, the filter does
not apply and the base rule holds; so before 2020 this configuration trades exactly as
H1-01, and it shares H1's window.

**The H1 grid.** Eight configurations, run in this order. Everything not listed equals
H1-01: daily bars, lookbacks of 20, 60 and 120 days, five slots, volatility scaling to 20%
on a 60-day span, at most one fifth of equity per slot, a 25% band and no filter.

| ID | Label | What differs from H1-01 |
|---|---|---|
| H1-01 | base | nothing |
| H1-02 | fast lookbacks | lookbacks 5, 10 and 20 days |
| H1-03 | slow lookbacks | lookbacks 60, 90 and 120 days |
| H1-04 | narrow band | band 10% |
| H1-05 | wide band | band 50% |
| H1-06 | four-hour bars | decisions every four hours, as above |
| H1-07 | funding filter | flat while seven-day funding exceeds 0.63% |
| H1-08 | no volatility scaling | w* = (1/5) × s |

Why these and not others. The lookback sets span one to three weeks, three weeks to four
months, and two to four months; crypto's time-series momentum is reported at daily and
weekly horizons (Liu and Tsyvinski 2021), so the fast set is a genuinely different horizon,
not a nudge. The longest lookback is held at 120 days in every configuration so that all
share one window and one eligibility rule. The volatility target is not varied: below the
one-fifth cap, which binds only when a coin's volatility falls under 20%, it scales
returns without changing the Sharpe ratio, so a second target would spend a trial on
nothing; switching scaling off (H1-08) is the informative contrast. The universe size is
not varied: at 65 of the 92 development month ends fewer than ten of the ten largest pairs
are eligible, so a ten-pair universe would need pairs outside the pinned candidate set.

## 7. H2, funding carry

Decision D3 approved paper short positions on perpetuals for research and paper only, so
H2 is the carry trade, not a filter on H1. It holds BTC and ETH spot long and the
USDT-margined perpetual short in equal quantities, so its price exposure is zero apart
from the basis between the two, and it earns the funding that longs pay shorts.

**Rule.** At each daily decision t, for each coin separately, F is the sum of its published
funding rates with `calc_time` in the L days up to t (21 settlements over seven days, since
BTC and ETH settle every eight hours throughout the archive). With the round-trip cost of a
carry position c = 4 × 12 bps = 0.48% (both legs in and out), the thresholds are

    entry threshold = m_entry × c × L / 28,    exit threshold = m_exit × c × L / 28.

A flat coin enters when F is above the entry threshold; a coin in carry exits when F is
below the exit threshold. With m = 2 and L = 7 this is NEXT_ITERATION.md's rule exactly:
trailing seven-day funding above twice the round-trip cost spread over 28 days, which is
0.24% over seven days (about 12.5% a year), and exit below it.

**Sizing and holding.** On entry, the spot quantity is q = 0.5 × equity divided by the spot
fill's reference price; q spot is bought and q perpetual sold on the same 15-minute bar.
Quantities stay fixed until the exit, which sells q spot and buys q perpetual. Each coin
uses half of equity, and the two coins act independently. At each settlement while short,
the book receives q times the funding mark times the rate, and pays it when the rate is
negative. Spot and perpetual are treated as one jointly margined book, so liquidation of
the short leg in a rally is not modelled; separately margined, a short would need its
margin topped up, which this model does not charge for. That is a stated limitation.

**The H2 grid.** Six configurations, run in this order. Everything not listed equals H2-01:
BTC and ETH, half of equity each, a seven-day lookback and entry and exit multiples of 2.

| ID | Label | Lookback | Entry threshold | Exit threshold |
|---|---|---|---|---|
| H2-01 | base | 7 days | 0.24% | 0.24% |
| H2-02 | low threshold | 7 days | 0.12% (m = 1) | 0.12% |
| H2-03 | high threshold | 7 days | 0.48% (m = 4) | 0.48% |
| H2-04 | hold while positive | 7 days | 0.24% | 0 (exit when F turns negative) |
| H2-05 | three-day lookback | 3 days | 0.103% (m = 2) | 0.103% |
| H2-06 | always on | none | enter at the first decision | never exits |

The threshold dimension runs from always on (the passive carry, the limit of a threshold
of minus infinity) through half, the registered and twice the registered multiple; the
exit rule and the lookback are the two other choices a carry trader actually makes. A
multiple and a horizon change the threshold only through their ratio, so only the multiple
is varied.

## 8. Benchmarks, control runs and cost sensitivity

These runs are ledgered, can never be selected, and do not count towards N or the trial
budget. They are registered here so that the harness can tell them from trials by their
configuration (section 9).

**Benchmarks.** B-H1, for every H1 configuration: at each month-end decision, trade to one
fifth of equity in each H1 universe member (cash for an empty slot), with no trading
between month ends, the same fills and costs and the same rule for halted pairs. It is the
least trading that stays an equal-weight holding of the same dated universe. B-H2, for
every H2 configuration: BTC and ETH spot at half of equity each from the first H2 decision,
traded back to halves at each month-end decision, with the same fills and costs.

**Control runs.** One hundred per hypothesis, seeds 0 to 99, using
`numpy.random.default_rng(seed)`.

* C-H1 (random sign) is H1-01 with its votes computed on a synthetic price series per
  instrument: starting at its first close, compounding its actual daily simple returns
  with each day's sign flipped independently with probability one half. Volatility,
  sizing, the universe and the profit and loss use actual prices. Squared returns are
  unchanged by a sign flip, so the control has the same sizing and a realistic,
  persistent signal and turnover, but no information about direction.
* C-H2 (random timing) is H2-01 with each coin's entry and exit decided on its development
  funding series shifted circularly by k settlements, k drawn uniformly from 540 to M − 540
  (180 days from either end) where M is the coin's number of development settlements;
  funding received and the profit and loss use the actual series. The control is in
  carry about as often and for about as long as the real rule, at times unrelated to the
  funding it collects.

Controls are diagnostics, not pass criteria: the verdict memo reports each candidate's
percentile among its hundred controls' Sharpe ratios. A random-sign trend control still
earns about half the market's drift, so its distribution is not centred on zero; that is
why it is reported rather than used as a threshold.

**Cost sensitivity.** After the ledger freeze, each hypothesis's candidate (section 11) is
re-costed at 7.5, 10 and 15 bps a side in total (round trips of 15, 20 and 30 bps, as G2.4
names them), slippage staying at 2 bps and the fee taking the rest. These runs are
reported in the tear sheet only. Every gate figure uses 10 bps plus 2 bps a side.

## 9. What a trial is, how N is counted, and the budget

**A trial is defined by its configuration and its data window, never by a label.** Any
harness run of a configuration in the H1 or H2 grid that produces a return for any day of
that hypothesis's out-of-sample window is a trial. Whether a run is a trial, a benchmark, a
control, a sensitivity run or the holdout run is decided by the registered configuration
it resolves to; a run that resolves to no registered configuration is refused, and no run
may produce returns outside the registered windows except the holdout run after the
unseal. This closes threat T18 of the [ledger threat model](research/ledger-threat-model.md).

**N** is the number of trial entries in the frozen ledger, from the genesis entry to the
freeze tag, across H1, H2 and the reserve. Every trial entry opened counts once, whether
the run completes, fails or is aborted; every rerun of a configuration counts again; N is
never deduplicated by configuration hash, and no statistic takes N from a caller.
Benchmark, control, sensitivity and holdout entries are outside N. The genesis entry pins
this rule (threat model principle 3 and T1).

**The budget (decision D11).** The minimum backtest length target is a Sharpe of 1.0, so
G0.6's caps stand: at most 12 trial entries for H1, 12 for H2 and 6 in reserve, 30 in all.
The grids register 8 and 6 configurations, 14 in all. The remaining entries exist only
for reruns; no configuration outside the grids is ever run.

**Run rules.**

1. Every registered configuration of a hypothesis is run, in the registered order,
   whatever the earlier results show. Stopping early after a good result would make the
   deflated Sharpe's N too small.
2. A hypothesis is judged only when every one of its registered configurations has a
   selectable run. If it has not by the freeze on 2026-12-01, it cannot pass and its
   entries still count.
3. A rerun requires a recorded defect: what was wrong and the commit that fixes it, written
   in the rerun's entry. A fix to code both hypotheses use (the harness, loaders, fills,
   costs or statistics) reruns every configuration already run; a fix confined to one
   hypothesis's strategy code reruns that hypothesis's configurations already run.
4. A configuration's selectable run is its last completed trial entry at the freeze, and
   all selectable runs of a hypothesis must come from identical harness and strategy code,
   as their entries record.
5. Reserve entries are drawn only after a hypothesis has used its own 12, only for reruns
   under rule 3, each with its recorded reason. When the budget is exhausted no further
   trial runs; a hypothesis that has not passed by then has failed.

With these rules a harness defect found after all 14 configurations have run costs 14 more
entries, which the budget of 30 absorbs once; that is why decision D11 kept the target at
1.0 rather than 0.75, under which the cap would have been 17.

## 10. Pass criteria

All statistics are computed on daily net returns over the configuration's window, per day
and not annualised. Annualised figures, for reporting only, multiply a daily Sharpe ratio
by the square root of 365, since crypto trades every day. Notation: T is the number of
daily returns; SR the daily Sharpe ratio, mean over standard deviation with T − 1 in the
denominator (zero if the standard deviation is zero); γ3 the skewness m3 / m2^1.5 and γ4
the kurtosis m4 / m2^2 (Pearson, so 3 for a normal distribution), with m_k the k-th central
moment computed with 1/T; Φ the standard normal distribution function; and z = 1.6448536...,
its 95% point.

**Deflated Sharpe ratio** (Bailey and López de Prado 2014). The probabilistic Sharpe ratio
against a threshold SR* (Bailey and López de Prado 2012) is

    PSR(SR*) = Φ( (SR − SR*) × √(T − 1) / √(1 − γ3 × SR + (γ4 − 1)/4 × SR²) ),

and the deflated Sharpe ratio is DSR = PSR(SR0), where

    SR0 = √V × [ (1 − γ) × Φ⁻¹(1 − 1/N) + γ × Φ⁻¹(1 − 1/(N e)) ],   γ = 0.5772156649...,

is the Sharpe ratio the best of N skill-less trials is expected to show (taken as 0 when
N = 1). N is the ledger's count from section 9. V is the sample variance (the denominator
is one less than the number of selectable runs) of the daily Sharpe ratios of every
selectable run in the ledger, both hypotheses together, each over its own window: the holdout candidate is
chosen across both, so the deflation charges for the whole family. If fewer than two
selectable runs exist, V = 1/T. Known answers G1.3 must reproduce, from the paper's own
example (N = 100, V of 1/2 in annual terms, an annualised Sharpe of 2.5 over T = 1,250 daily
returns at 250 a year, γ3 = −3, γ4 = 10): SR0 = 0.1132 a day and DSR = 0.9004; with N = 46,
DSR = 0.9505; and with normal returns DSR stays above 0.95 up to N = 88.

**Minimum track record length** (Bailey and López de Prado 2012), reported, not a criterion:

    MinTRL(SR*) = 1 + (1 − γ3 × SR + (γ4 − 1)/4 × SR²) × (z / (SR − SR*))²  days,

for SR* = 0 and SR* = SR0, reported as "not reached" when SR ≤ SR*. Known answer, from the
paper's appendix: monthly mean 2 and standard deviation √12 (so SR = 1/√3), γ3 = −0.72,
γ4 = 5.78 and SR* = 1/√12 give 59.895 months.

**Probability of backtest overfitting** (Bailey, Borwein, López de Prado and Zhu 2017), by
combinatorially symmetric cross-validation. The matrix has one column per selectable run of
the candidate's hypothesis, in registered order, and one row per day of its window. The
earliest T mod 16 rows are dropped and the rest split into 16 contiguous blocks of equal
length. For each of the C(16, 8) = 12,870 ways to choose 8 blocks (the paper's text prints
12,780, a transposition), the chosen blocks in time order are the in-sample set and the
other 8 the out-of-sample set. The column with the highest in-sample Sharpe ratio is
selected (ties to the lowest column index); its out-of-sample Sharpe ratio is ranked
ascending among all columns (1 the lowest, ties averaged); ω = rank / (columns + 1) and
λ = ln(ω / (1 − ω)). The PBO is the share of the 12,870 splits with λ ≤ 0, matching the
paper's integral of the logit distribution up to and including zero. G1.3's known answers:
a matrix in which one column has the highest Sharpe ratio in every block gives 0, and one in
which the in-sample best is always the out-of-sample worst gives 1. A PBO over all
selectable runs of both hypotheses, on their common days, is reported too, but is not a
criterion: each hypothesis's verdict depends on its own trials, and the choice between two
passing candidates is charged by the deflated Sharpe's family-wide N and V.

**The criteria.** A hypothesis passes development only if its candidate (section 11) meets
all five:

| ID | Criterion | Passes when |
|---|---|---|
| DEV-1 | DSR of the candidate's daily net returns | above 0.95 |
| DEV-2 | DSR of the candidate's returns in excess of its benchmark (below) | above 0.95 |
| DEV-3 | PBO of the candidate's hypothesis | below 0.5 |
| DEV-4 | Sharpe ratio of the window's returns with each calendar year removed in turn | every one above 0 |
| DEV-5 | Sharpe ratio of the window's returns without the 20 largest daily returns (ties: earliest first) | above 0 |

For DEV-2, H1's excess return is e = r − k × r_B with r_B the daily return of B-H1 and k the
ratio of the standard deviations of r and r_B over the window, so the benchmark is held at
the strategy's volatility; this is the volatility-matched comparison NEXT_ITERATION.md
names, because a long-flat trend rule can otherwise pass on market exposure alone. H2's
excess return is e = r − β × r_B with r_B the daily return of B-H2 and β the least-squares
slope of r on r_B, intercept included, over the window (decision D15): H2 holds equal
quantities long and short, so there is no market exposure for it to pass on, and a
volatility-matched comparison would instead ask it to beat the market's Sharpe ratio
(section 12). The DSR of an excess series uses the same N, and V computed over every
selectable run's excess-return Sharpe ratio. Calendar years for DEV-4 are 2018 (from
05-01) to 2025 for H1, and 2020 (from 01-08) to 2025 for H2.

**The holdout criterion.** HOLD-1: on the holdout candidate's 304 holdout daily returns,
SR_h > 0, and

    SR_h ≥ SR_w − z × √( (1 − γ3 × SR_w + (γ4 − 1)/4 × SR_w²) / (304 − 1) ),

where SR_w, γ3 and γ4 are the candidate's over its development window. The standard error
is that of a Sharpe ratio estimated over 304 days if the holdout came from the same
process as development; the development estimate's own error is ignored, which makes the
band narrower and the test stricter.

**Reported, never criteria:** the family PBO, the minimum track record length, 20-day
circular block bootstrap intervals (10,000 resamples, seed 20261111, the 5th and 95th
percentiles of the annualised Sharpe), each candidate's percentile among its controls,
the benchmarks' own statistics and the cost sensitivity.

**Calibration.** The gate as registered here, with these windows, a family N of up to 30,
V across both hypotheses and per-hypothesis PBO matrices of 8 and 6 columns, is what G1.7
must calibrate: no strategy is judged until it passes at most 5% of 1,000 simulated
no-skill ledgers at N = 30 and detects a planted Sharpe of 1.5.

## 11. The holdout candidate, the single holdout run and the verdict

The candidate is chosen by a mechanical rule over the frozen ledger, so neither a look at
development results nor a private look at 2026 can change which configuration is judged
(threat T22).

1. **Each hypothesis's candidate** is its selectable run with the highest DEV-1 deflated
   Sharpe ratio; ties go to the earlier registered configuration.
2. **A hypothesis passes development** if and only if its candidate meets DEV-1 to DEV-5. No
   other configuration of that hypothesis is judged, so the rule cannot search the grid for
   one that happens to clear every criterion.
3. **The holdout candidate** is, among hypotheses that pass development, the candidate with
   the higher DEV-1 deflated Sharpe ratio; ties go to H1. If neither passes, there is no
   holdout candidate, no unseal and no holdout run.
4. **The unseal** names the `prereg-v1` commit and file hashes and the holdout candidate's
   configuration hash, and is accepted only with the owner's key (decision D4: the owner
   alone holds it, as a passphrase kept in a password manager, with only its salted hash
   committed under G1.6). The harness refuses an unseal that names any other
   configuration.
5. **The holdout run** is one simulation of the candidate configuration from the start of
   its development window through 2026-10-31, with positions carried across the boundary
   and the 2025-12 month-end universe taking effect at 2026-01-01 00:00. HOLD-1 reads only
   the returns of 2026-01-01 to 2026-10-31. Before it starts, the holdout files are fetched
   again and verified against the holdout manifest; any failure stops the run before a
   return is computed.
6. **One result spends the seal.** A run that fails before producing a result may be
   retried under the same unseal for the same configuration only, every attempt ledgered.
   No other configuration, and no benchmark, control or sensitivity run, ever reads holdout
   data.

**The verdict, per hypothesis**, in words:

| Verdict | When |
|---|---|
| PASS | it passes development, its candidate was the holdout candidate, and HOLD-1 holds |
| PASSED DEVELOPMENT, NOT TESTED ON THE HOLDOUT | it passes development, but the other hypothesis's candidate was the holdout candidate |
| FAIL | otherwise, once every registered configuration has a selectable run |
| NOT RUN | not every registered configuration has a selectable run at the freeze |

## 12. The net Sharpe each criterion implies

This is arithmetic on stated assumptions, not data. Each figure is the annualised net Sharpe
ratio at which the criterion is just met; for a symmetric sampling distribution it is also
the true Sharpe ratio with even odds of passing. The scripts that produced it read no
market data.

**Base assumptions.** Daily net returns independent and normal (γ3 = 0, γ4 = 3); the
windows of section 3 (H1 2,802 days, H2 2,185); V equal to 1/T, the dispersion of Sharpe
estimates among independent skill-less trials (the reading behind NEXT_ITERATION.md's
earlier figures); 365 days a year.

| Criterion | H1, N = 10 | N = 20 | N = 30 | H2, N = 10 | N = 20 | N = 30 |
|---|---|---|---|---|---|---|
| DEV-1, deflated Sharpe above 0.95 | 1.16 | 1.28 | 1.34 | 1.32 | 1.45 | 1.52 |
| DEV-2, as the excess series' own Sharpe | 1.16 | 1.28 | 1.34 | 1.32 | 1.45 | 1.52 |
| DEV-2, as the strategy's Sharpe, illustrative (see below) | 1.40 | 1.49 | 1.54 | 1.32 | 1.45 | 1.52 |
| DEV-3, PBO below 0.5 | no fixed figure (see below) | | | | | |
| DEV-4, each year removed | 0.21 | 0.21 | 0.21 | 0.25 | 0.25 | 0.25 |
| DEV-5, best 20 days removed | 0.38 | 0.38 | 0.38 | 0.47 | 0.47 | 0.47 |
| HOLD-1, on the 304 holdout days | above 0 | above 0 | above 0 | above 0 | above 0 | above 0 |

With no deflation (N = 1) DEV-1 would need 0.59 for H1 and 0.67 for H2. At N = 14, the
count if every registered configuration runs once and nothing is rerun, DEV-1 needs 1.22
and 1.38. These replace NEXT_ITERATION.md's "roughly 1.6 at N = 30, or 1.4 at N = 10", which
assumed about 5.6 years for every hypothesis.

**How the rows were computed.**

* DEV-1 solves PSR(SR0) = 0.95 for SR, with SR0 = √(1/T) × E[max Z_N], where E[max Z_N] is
  1.5746, 1.9007 and 2.0734 at N = 10, 20 and 30. In power terms, the true Sharpe ratio that
  passes DEV-1 four times in five is 1.47, 1.58 and 1.65 for H1 and 1.66, 1.79 and 1.86 for H2.
* DEV-2 needs the same figure from the excess series. For H1, a volatility-matched
  excess has Sharpe (S − S_B) / √(2 − 2ρ), where S_B is the benchmark's Sharpe ratio and ρ
  the correlation, so the strategy needs S ≥ S_B + √(2 − 2ρ) × (the DEV-1 figure). The row
  assumes S_B = 0.5 and ρ = 0.7, which are assumptions and not measurements. Other
  assumptions: S_B = 0.5 and ρ = 0.5 give 1.66, 1.78 and 1.84; S_B = 1.0 and ρ = 0.7 give 1.90,
  1.99 and 2.04; S_B = 1.0 and ρ = 0.5 give 2.16, 2.28 and 2.34. So for H1, DEV-2 is likely to
  bind, and how hard depends on how well buy-and-hold did, which is exactly the point of
  it. For H2, a beta-matched excess at ρ = 0 has the strategy's own Sharpe ratio; at
  ρ = 0.2 and S_B = 1.0 it needs 1.49, 1.62 and 1.69. Had H2 kept the volatility-matched
  comparison, it would need 2.36 to 2.65 with S_B = 0.5 and 2.86 to 3.15 with S_B = 1.0
  (decision D15).
* DEV-3 has no Sharpe threshold of its own: it depends on the other trials. In a synthetic
  family of 8 (H1) or 6 (H2) independent columns where one has true Sharpe S and the rest
  none, the PBO falls below 0.5 with probability 0.52 to 0.53 at S = 0, 0.64 at S = 0.5,
  0.78 to 0.80 at S = 0.75 and 0.88 to 0.91 at S = 1.0 (200 to 300 simulations each). It is a
  weak filter on its own; DEV-1 carries the weight.
* DEV-4 is the true Sharpe ratio at which removing the best calendar year leaves a
  positive Sharpe half the time, by simulation over the actual year lengths; it agrees
  with E[max Z_Y] / (Y − 1) for Y = 8 and 6 years (0.20 and 0.25).
* DEV-5 is the realised Sharpe ratio at which the 20 largest of T standardised normal
  returns, which sum to about 55 and 54 standard deviations, exactly offset the rest.
* HOLD-1's band sits below zero for every development Sharpe ratio under about 1.81, since
  the holdout's standard error is 1.10 in annual terms; so in practice HOLD-1 asks for a
  positive holdout Sharpe ratio.

**Sensitivity of DEV-1** (annualised, same windows):

| Assumption | H1, N = 10 | N = 20 | N = 30 | H2, N = 10 | N = 20 | N = 30 |
|---|---|---|---|---|---|---|
| Base: normal, V = 1/T | 1.16 | 1.28 | 1.34 | 1.32 | 1.45 | 1.52 |
| Fat tails: γ3 = −1, γ4 = 10 | 1.18 | 1.30 | 1.37 | 1.34 | 1.48 | 1.55 |
| Correlated trials: V = 0.25/T (pairwise correlation 0.75) | 0.88 | 0.94 | 0.97 | 0.99 | 1.06 | 1.10 |
| Dispersed trials: V = 2/T | 1.40 | 1.56 | 1.65 | 1.58 | 1.77 | 1.87 |
| Correlated and fat-tailed | 0.89 | 0.95 | 0.98 | 1.01 | 1.08 | 1.12 |

For DEV-5 under fat tails (a Student t with γ4 = 10), the figures rise to 0.52 (H1) and 0.62
(H2). V is the one input the data sets: trials of one hypothesis are correlated, which
lowers V and with it the bar, while a family in which H1 and H2 differ sharply raises it.
Using the empirical V with the raw count N follows the paper: for equally correlated
trials it reproduces the correct expected maximum, whereas a floor at 1/T would charge
twice for the same correlation.

## 13. What the gate cannot detect

* **A modest edge.** At N = 30, a true net Sharpe of 1.0 passes DEV-1 for H1 only about one
  time in six (1.34 needed, standard error 0.36 a year) and for H2 about one time in ten
  (1.52 needed, standard error 0.41). A
  strategy worth trading at a Sharpe of 0.7 to 1.0 would most likely be reported as a fail.
  A fail is therefore "no edge large enough to detect in 6 to 8 years of daily data", not
  "no edge".
* **The holdout catches only a gross failure.** Over 304 days, a strategy with a true
  Sharpe of 1.0 shows a positive holdout Sharpe 82% of the time, and one with 1.5, 91%;
  HOLD-1 can reject a strategy that has stopped working, not confirm one that works.
  The holdout is also contaminated: 2026 prices are public and known to every model
  involved (D4), so it can only catch a failure.
* **The PBO alone** passes a family with no skill at all about half the time.
* **Hindsight in the design.** Development returns are unfitted but not unseen; the
  configurations were written by people who know crypto's history from 2018 to 2025. The
  deflated Sharpe charges for the 14 to 30 trials actually run, not for the many ideas
  rejected in conversation before this was written.
* **Regime.** A pass is a statement about 2018 to 2025 (H1) or 2020 to 2025 (H2) and ten
  months of 2026. Funding carry in particular was attractive on only about 8% of days in
  2025 (D10's research), so a pass driven by 2020 and 2021 would say little about today.
* **Risks the data cannot show.** H2's jointly margined book hides liquidation and
  exchange-failure risk; the D12 rule is conservative but not a worst case.

## 14. Decisions this rests on

All are in [DECISIONS.md](DECISIONS.md). D11 to D15 were decided by the owner on
2026-11-11, on the quant's recommendation; each entry keeps the options that were rejected
and why.

| Decision | What it fixes here |
|---|---|
| D3 | H2 trades paper short perpetual positions; it is not a filter on H1 |
| D4 | the owner alone holds the holdout key; who has seen 2026 prices |
| D11 | the minimum backtest length target is a Sharpe of 1.0: caps of 12, 12 and 6 |
| D12 | a position in a pair that stops trading is closed at its last day's low (high for a short) |
| D13 | a pair joins the universe only once it has a close 120 days back; the next eligible pair takes its place |
| D14 | universe exclusions by category; the holdout names that were seen |
| D15 | H2's excess return is against a beta-matched buy-and-hold; H1's stays volatility-matched |

Two further owner decisions of 2026-11-11 stand behind the ledger and the seal: the
repository now has rulesets that forbid force pushes to and deletion of main, require the
`research` and `validate` checks, and make `prereg-*` and `ledger-*` tags immutable once
created; and the holdout files are deleted from the local mirror until the single unseal,
then fetched again and verified against the committed holdout manifest.

## 15. Where NEXT_ITERATION.md and the register disagree with this

Each with severity (what goes wrong if ignored) and confidence. None changes a criterion
except as decided in D15.

1. **Medium, confirmed by arithmetic.** NEXT_ITERATION.md's volatility-matched comparison,
   applied to H2 as written, would test whether carry beats the market's Sharpe ratio rather
   than whether it is more than market exposure: 2.36 to 3.15 needed under the assumptions
   of section 12. Resolved by D15.
2. **Medium, likely.** "The five largest non-stablecoin pairs", read literally, can need a
   pair ranked eleventh or lower once exclusions and young pairs are removed, and the
   holdout manifest pins finer bars only for a top ten. By their listing dates alone, RE
   and SNDKB can only have reached a holdout top ten while younger than 120 days. Resolved here by ranking within the ten largest
   (section 6); in development it never binds, but some holdout months may leave a slot in
   cash.
3. **Medium, likely.** G1.7 must calibrate this gate as registered (family N and V across
   both hypotheses, the windows of section 3, PBO matrices of 8 and 6 columns). A
   calibration of a simpler gate would not support the 5% false-pass claim for this one.
4. **Low, confirmed.** NEXT_ITERATION.md's "about 5.6 years" and its implied Sharpe ratios
   (1.4 at N = 10, 1.6 at N = 30) are superseded by section 12.
5. **Low, confirmed.** G2.3 says "funding accrued every eight hours"; the rule is per
   published settlement. BTC and ETH settle every eight hours throughout, so H2 is
   unaffected, but the wording should change.
6. **Low, confirmed.** G2.4 headlines returns "net of 20 bps"; the gate's cost basis is
   24 bps a round trip on one leg (10 plus 2 bps a side). The tear sheet's headline should
   use the gate's basis, with 15, 20 and 30 bps as sensitivity.
7. **Low, confirmed.** NEXT_ITERATION.md calls benchmark and control runs "recorded but can
   never be selected" and the threat model calls them "trials marked unselectable"; here
   they are ledgered kinds outside N, told apart by their registered configuration.
8. **Low, confirmed.** NEXT_ITERATION.md leaves open the form of the blended signal, what
   "at most 1x per coin" means, what "four-hour bars" changes and which way the funding
   filter points; section 6 fixes each, with the alternative not tried.
9. **Low, confirmed.** The PBO paper prints 12,780 splits for 16 blocks; the correct count,
   used by G1.3, is 12,870.
10. **Informational.** G0.6's limit of about 1.6 years on a first training window does not
    arise, because nothing is fitted (section 3).
11. **Informational.** The pre-registration was due on 2026-11-07 and is drafted on
    2026-11-11; G2.2 and G2.3 cannot start before it is tagged and G1.7 has passed.

## 16. Change control and signatures

Before the first trial entry, a change to this document or to
[preregistration.json](preregistration.json) needs a new tag (`prereg-v2` and onward, since
tags are immutable) and its reason recorded in the change log below; the ledger's genesis
entry then names the new tag. After the first trial entry, no change is allowed, and the
ledger refuses a new genesis.

**Change log.**

| Tag | Date | Reason |
|---|---|---|
| `prereg-v1` | not yet tagged | first version |

**Signatures.**

| Role | Name | Date | Signature |
|---|---|---|---|
| Quant (drafted) | quant specialist | 2026-11-11 | drafted; not a signature |
| CTPO (approves) | | | |
| Owner (signs) | | | |

## 17. References

* Bailey, D. H. and López de Prado, M. (2012). The Sharpe ratio efficient frontier.
  *Journal of Risk* 15(2), December 2012. The probabilistic Sharpe ratio and minimum track
  record length; the MinTRL known answer is from its appendix A.3.
* Bailey, D. H., Borwein, J. M., López de Prado, M. and Zhu, Q. J. (2014). Pseudo-mathematics
  and financial charlatanism: the effects of backtest overfitting on out-of-sample
  performance. *Notices of the American Mathematical Society* 61(5), 458 to 471. Minimum
  backtest length.
* Bailey, D. H. and López de Prado, M. (2014). The deflated Sharpe ratio: correcting for
  selection bias, backtest overfitting and non-normality. *Journal of Portfolio
  Management* 40(5), 94 to 107.
* Bailey, D. H., Borwein, J. M., López de Prado, M. and Zhu, Q. J. (2017). The probability
  of backtest overfitting. *Journal of Computational Finance* 20(4), 39 to 69.
* Liu, Y. and Tsyvinski, A. (2021). Risks and returns of cryptocurrency. *Review of
  Financial Studies* 34(6), 2689 to 2727.
