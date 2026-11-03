# Next iteration: from working plumbing to a verdict

Set 2026-11-03. Time box: six weeks, ending 2026-12-15.

## The goal

By 2026-12-15 Norse returns a pre-registered, cost-honest verdict on whether any
strategy that holds for hours to weeks clears the evaluation gate below, on
2019 to 2025 history and then on a sealed 2026 holdout. If one does, it becomes the
live paper strategy, with an edge model calibrated from that evaluation rather than
assumed, and a weekly report comparing paper results against the backtest. If none
does, the trading claim is retired in writing and Norse is described as what it
demonstrably is: a multi-venue market-data recording and research platform.

Success is the verdict, not a profit figure. A clean, documented "no" is a
successful iteration; another month of tuning without a pre-registered test is
not.

## Where the product stands

The platform now works end to end, which it did not a month ago. muninn has
recorded Binance and OKX BTC-USDT trades and 100 ms order books continuously since
2026-10-27: 25.8 million Binance and 5.1 million OKX events in the first seven
days, with 100% scrape uptime, 6 and 32 reconnects respectively, and about 270 MB
a day in the `muninn-raw` bucket. huginn and sleipnir run on supported Go, and
obi-bridge's timestamps are honest, so the latency SLO now measures something real.

The trading side has produced no evidence at all. huginn has emitted 119 order
intents in its lifetime and none since it was restarted on 2026-10-27. Its cost
gate only admits an entry whose expected edge clears the round-trip cost, and the
edge estimate (a fixed 60 bps per unit of score, never calibrated) never clears
20 bps when typical composite scores sit around 0.1 to 0.2. That refusal is the
correct behaviour for a strategy with no edge, and the evidence below says it has
none. The consequence is that running paper trading longer, as configured, cannot
teach us anything.

## What the evidence says

### The live signal, tested on our own books for the first time

Every earlier verdict (see [EDGE_VERDICT.md](EDGE_VERDICT.md)) tested trade-flow
imbalance on bars, because no order-book history existed. The recorder changed
that, so on 2026-11-03 the live signal, top-of-book size imbalance, was tested
directly on three days of recorded books (2026-10-28 to 10-30): 2,583,666 Binance
snapshots over the top 10 levels and 1,609,001 OKX snapshots over the top 5.
[scripts/research/obi_decile_test.py](../scripts/research/obi_decile_test.py)
reproduces it; the full output is in
[research/obi-decile-2026-10-28-to-30.txt](research/obi-decile-2026-10-28-to-30.txt).

| Binance BTC-USDT, forward mid return | 1 s | 5 s | 60 s | 5 min | 1 h |
|---|---|---|---|---|---|
| Top minus bottom decile, entry at observation | 0.66 bps | 1.36 bps | 2.27 bps | not significant | not significant |
| Same, entry 2 s later (huginn's latency) | 0.18 bps | 0.59 bps | 1.34 bps | not significant | not significant |
| t statistic of the top decile, non-overlapping samples | 58.7 | 23.3 | 2.2 | -1.0 | 0.5 |

The signal is real. Returns rise monotonically across the deciles, the effect is
overwhelmingly significant at seconds, and OKX reproduces it independently (0.65
bps at 1 s, 1.36 bps at 60 s). It is also economically worthless to us. The best
gross move it predicts is about 1 bp, the round trip costs about 20, and huginn's
2 s decision latency removes 40% (at 60 s) to 75% (at 1 s) of the little there is. This matches the
literature: a 2026 study of Binance perpetuals found the order-flow effect gone
within about 10 s and worth about 0.5 bp per trade gross, "roughly one-tenth of a
single taker fee". Order-book imbalance is retired as an alpha. It may still be
used to time entries, where it is worth at most about a tick.

### Costs set the horizon

At the lowest retail tier a round trip costs 20 bps on Binance (15 with the BNB
discount) and 16 to 20 bps on OKX, while BTC-USDT's quoted spread is a single tick
(0.001 bps), so fees, not spread, are the hurdle. Maker orders do not escape it,
because Binance's entry tier charges makers the same as takers. Kraken (40 / 80
bps a side since July 2026) and Coinbase Advanced (50 / 90 bps) are useful as data
sources only. At 45% annualised volatility BTC typically moves about 6 bps in a
minute and about 48 bps in an hour, so a 20 bps cost is small only for holding
periods of an hour or more. The published evidence that credibly survives costs
also sits there: time-series momentum at daily and weekly frequency (Liu and
Tsyvinski, 2021) and, with weaker evidence, funding, basis and open interest as
regime and crash indicators.

### Statistical power sets the method

Telling an annualised Sharpe ratio of 1.0 from zero at one-sided 95% confidence
needs about 141 weeks of out-of-sample returns; a Sharpe of 3 needs about 16
weeks. Trading more often does not shorten this. No paper-trading run inside one
iteration can therefore establish a modest edge; only long history can. That
history exists for free at the horizons that matter: Binance spot klines and
trades from 2017, and perpetual funding rates from 2019. It does not exist for
spot order books. data.binance.vision publishes no spot depth, its perpetual
`bookDepth` bands are about 100 times too coarse for top-of-book work (see
[huginn's BOOKDEPTH_FINDING.md](https://github.com/lgreene03/huginn/blob/main/docs/BOOKDEPTH_FINDING.md)),
and Tardis.dev offers only the first day of each month without a key. Recording
forward was the right choice and continues; the book history it builds serves the
work after this iteration.

## The evaluation gate, fixed before any test runs

A strategy passes only if all of the following hold. They are written down now
so that no result can move them.

Returns are net of 20 bps per round trip plus the slippage model huginn's executor
already applies. Development uses walk-forward evaluation on 2019-01-01 to
2025-12-31. The holdout, 2026-01-01 to 2026-10-31, is sealed: the harness refuses
to evaluate it until the final run, and it is run once. Every configuration tried
is recorded in a trial ledger, and the ledger's count is the N in the deflated
Sharpe ratio. With seven years of development data, keeping the best zero-skill
backtest at an expected Sharpe of 1 or below limits the budget to about 30 trials
across the whole iteration (from the minimum backtest length, 2 ln N divided by
the squared expected maximum Sharpe). The pass mark is a deflated Sharpe ratio
above 0.95 on walk-forward out-of-sample returns, a probability of backtest
overfitting below 0.5 by combinatorially symmetric cross-validation over 16
blocks, and a positive holdout Sharpe inside the walk-forward confidence
interval. Ten months of holdout can only catch a large failure, not confirm a
modest success, and this document says so rather than pretending otherwise.

## Milestones

**M0, by 2026-11-10: close the books on order-book imbalance.** Update
EDGE_VERDICT.md with the decile test. Remove OBI's weight as an alpha in the live
composite, keeping it available as an entry-timing input. Decide who owns
`features.obi.v1`. The recommendation is that muninn publishes its features under
its own prefix: the signal is no longer worth migrating huginn's consumer for,
and obi-bridge keeps serving huginn unchanged. Wire an Alertmanager receiver,
which needs a channel and credentials from the owner.

**M1, by 2026-11-17: build the gate.** Extend huginn's existing walk-forward
research path with the trial ledger, the deflated Sharpe ratio with that N,
combinatorially symmetric PBO, a minimum-track-record report and the holdout seal.
Fix the two known defects first: the hit rate hardcoded to 0.0% at
research.go:402 and the unexplained -10,000 result in fold 4 of the six-month run.
The gate passes its own tests before it judges any strategy.

**M2, by 2026-12-01: test two hypotheses.** H1 is time-series trend on BTC, ETH,
SOL, XRP and DOGE, on daily and four-hour bars, volatility-targeted and penalised
for turnover, using public klines. H2 uses perpetual funding, basis and open
interest as a regime filter that cuts H1's exposure when positioning is extreme,
using public funding history (Binance's public funding endpoint needs no key; its
coverage is to be confirmed at the start of M2). H2 is only evaluated as a
modification of H1, because crashes are too rare to test a standalone crash
signal. Both share the 30-trial budget.

**M3, by 2026-12-15: the verdict.** If a strategy passes, it is added as an alpha
through [ADDING_AN_ALPHA.md](ADDING_AN_ALPHA.md), huginn's edge model is replaced
by the expectation measured in the evaluation, and paper trading starts with a
weekly report breaking the gap between paper and backtest into fees, spread,
delay and missed fills. If nothing passes, EDGE_VERDICT.md records the kill and
the README is rewritten around the platform's data and research value.

## Kill criteria

If H1 and H2 both fail the gate, strategy search stops for this product: no
re-tuning, no new hypotheses invented after seeing results. If the trial budget
is exhausted before a pass, that is a fail. If a passing strategy's realised cost
per trade on paper exceeds the backtest's assumption by more than half over the
first four weeks, it is suspended pending a cost review. That last threshold is
this project's own judgement; the literature gives no accepted figure.

## Out of scope for this iteration

Further microstructure alpha search, which the evidence above closes. New
execution venues, because Kraken's and Coinbase's fees rule them out. Training
the served ML model, until a gated alpha exists for it to filter. Live trading of
any kind. Note also that Binance's public-data terms (CC BY-NC-SA 4.0, clause 4.2)
prohibit live proprietary trading execution based on that data, so any move to
live trading would need a different data basis, not only a different risk
appetite.

## Keeps running

The recorder keeps writing both venues' trades and books, at about 8 GB a month.
The decile test is re-run monthly on the growing history, since a signal that is
worthless at 20 bps today would matter at a lower fee tier.

## Decisions for the owner

Who owns `features.obi.v1` (recommendation above). Where alerts should go. Whether
the weekly scheduled task `norse-parity-advance` should be repointed at this goal,
since its parity target matters less once order-book imbalance is retired as an
alpha.
