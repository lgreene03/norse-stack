# Next iteration: from working plumbing to a verdict

Set 2026-11-03. Revised 2026-11-04 after independent reviews by the quant, CTPO,
technical, security and user interface specialists, whose definitions live in
[.claude/agents](../.claude/agents). Time box: ending 2026-12-15.

## The goal

By 2026-12-15 Norse returns a pre-registered, cost-honest verdict on whether any
strategy that holds for hours to weeks clears the evaluation gate below. The
verdict comes from a harness that cannot be fooled by its own operators: every
trial is counted in an append-only ledger, every dataset is pinned by hash, and the
holdout is sealed in code. If a strategy passes, the verdict is published and
wiring it into paper trading becomes the next milestone; this iteration does not
bundle the two. If none passes, the trading claim is retired in writing and Norse
becomes an honest strategy-falsification lab over a two-venue spot order-book
archive that no free source offers.

Success is the verdict, not a profit figure. A clean, documented "no" is a
successful iteration.

## What viable means, and how it is measured

Viable means a registered hypothesis becomes a trustworthy verdict cheaply. The
product metric: every verdict reproduces exactly from a clean checkout, run by an
independent agent, within five working days of registration. Two guardrails hold
throughout: zero unledgered trials, and recorder completeness of at least 99.5% of
minutes per venue.

## The team and decision rights

The quant specialist owns the pre-registration, the hypotheses, the gate's
statistics and their known-answer tests, the trial budget and the verdict. The
technical specialist builds the harness, loaders, manifests, ledger and seal
mechanics, and owns the CI gates. The security specialist owns network exposure,
secrets, supply chain, data licences, and the integrity of the ledger and the seal.
The user interface specialist owns every view of results, the honesty rules for how
strategies are presented, and recording health. The CTPO owns scope, dates, the
owner's decisions memo, positioning for both outcomes and the weekly review.

Unsealing the holdout needs the quant specialist and the owner. A written review is
held each Tuesday and a gate review at the end of each milestone. Done means CI
green on main, an independent reproduction, and documentation in the same pull
request.

## Where the product stands

The platform works end to end, which it did not a month ago. muninn has recorded
Binance and OKX BTC-USDT trades and 100 ms books continuously since 2026-10-27:
25.8 million Binance and 5.1 million OKX events in its first seven days, with 100%
scrape uptime and about 270 MB a day in the `muninn-raw` bucket.

The trading side has produced no evidence. huginn's last fill was on 2026-06-22, and
none has occurred inside Kafka's seven-day retention since. The cause is not yet
confirmed. The cost gate was first blamed, but its suppression counter has never
been emitted; the more likely block is the composite strategy's one-position-per-
instrument check, which runs before the cost gate while five positions sit open and
the strategy has no exit path. The reviews also found three defects that would have
corrupted this iteration's results had they gone unnoticed. huginn's risk manager
rolls its daily loss window on the wall clock, so in a backtest the 10,000 "daily"
limit is a lifetime cap: that is the unexplained -10,000 in fold 4 of the six-month
verdict. huginn's PBO is a fold-level proxy, not the combinatorially symmetric
method the gate names, and its deflated Sharpe is computed per fold and averaged.
And 23 of the stack's published ports bind to every interface on a machine whose
firewall is off, exposing both Postgres databases, Grafana and the unauthenticated
research gateway to the home network.

## What the evidence says

### Order-book imbalance, tested on our own books

The live signal was tested on three days of recorded books (2026-10-28 to 10-30):
2,583,666 Binance snapshots over the top 10 levels and 1,609,001 OKX snapshots over
the top 5. [scripts/research/obi_decile_test.py](../scripts/research/obi_decile_test.py)
reproduces it; the output is in
[research/obi-decile-2026-10-28-to-30.txt](research/obi-decile-2026-10-28-to-30.txt).

| Binance BTC-USDT, forward mid return | 1 s | 5 s | 60 s | 5 min and longer |
|---|---|---|---|---|
| Top minus bottom decile, entry at observation | 0.66 bps | 1.36 bps | 2.27 bps | unresolved |
| Same, entry 2 s later (huginn's latency) | 0.18 bps | 0.59 bps | 1.34 bps | unresolved |
| t statistic of the top decile, non-overlapping samples | 58.7 | 23.3 | 2.2 | too few samples |

The signal is real at seconds and reproduces on OKX, and it is economically
worthless to us: about 1 bp at best against a 20 bps round trip, with the 2 s
latency removing 40% to 75% of it. Two caveats from the quant review: beyond five
minutes there are only 8 to 10 independent samples and the market's drift
dominates, so those horizons are unresolved rather than shown to be empty; and the
decile cut-offs were fitted on the data they test. Neither changes the cost
arithmetic, which alone retires order-book imbalance as an alpha. It may still time
entries, where it is worth at most about a tick.

### Costs, power and data

A retail round trip costs 20 bps on Binance (15 with the BNB discount) and 16 to 20
on OKX, against a one-tick BTC spread, so fees set the horizon at an hour or more.
Kraken and Coinbase fees make them data sources only. Telling an annualised Sharpe
of 1 from zero needs about 141 weeks out of sample, so the verdict must come from
history, not paper trading. The free history is shorter than first assumed: on
data.binance.vision, SOL spot begins 2020-08, DOGE spot 2019-07, BTC funding and
premium index 2020-01 and BTC open interest 2020-09. There is no free source of
months of spot order books, so recording forward continues.

## The evaluation gate, fixed before any test runs

The pre-registration is signed and tagged `prereg-v1` on 2026-11-07, before any
trial, and nothing in it moves after a result is seen.

A trial is any run whose out-of-sample result is viewed, reruns after fixes
included. Trials are recorded in an append-only, hash-chained ledger in git; N in
every statistic is read from it; the harness refuses to run on a dirty tree, on
data absent from a committed manifest, or if the ledger write fails; unledgered
results do not count, and a broken chain voids the iteration. Costs are 10 bps a
side plus the executor's slippage model; the harness defaults (14 bps a round trip,
and 5 plus 2 in the gateway) are aligned to this first.

A strategy passes only if all of the following hold. The deflated Sharpe ratio on
the joined daily walk-forward out-of-sample returns exceeds 0.95, with N the ledger
count. The same holds for returns in excess of a volatility-matched, equal-weight
buy-and-hold of the same universe, because a long-flat trend rule can otherwise
pass on market exposure alone. The combinatorially symmetric PBO over 16 blocks is
below 0.5. The Sharpe stays positive with each year removed and with the best 20
days removed. On the holdout, run once for one candidate, the Sharpe is positive and
no more than 1.645 standard errors below the walk-forward estimate.

What that means in practice is published with the pre-registration: over about 5.6
years of daily returns, passing needs a net Sharpe of roughly 1.6 at N = 30, or 1.4
at N = 10. The holdout, 2026-01-01 to 2026-10-31, is sealed in code: the harness
refuses events on or after its start unless an unseal names the committed
pre-registration, and a second unseal fails. It is contaminated by hindsight,
because everyone has seen 2026 prices, so it can only catch a failure; paper
trading after the verdict is the first genuinely unseen evidence. Before judging
any strategy, the gate must pass at most 5% of 1,000 simulated no-skill ledgers at
N = 30 and detect a planted Sharpe of 1.5. Every result cites its dataset manifest
hash and commit SHA.

## Hypotheses

**H1, time-series trend.** At each month end the universe is the five largest
non-stablecoin Binance USDT spot pairs by 30-day volume, delisted pairs included,
so the coins are not chosen with hindsight. The signal is an equal blend of the
signs of 20, 60 and 120-day returns; positions are long or flat, sized to 20%
annualised volatility on a 60-day exponentially weighted estimate, at most 1x per
coin; rebalancing happens on the 00:00 UTC close, filled at the next 15-minute
average price, only when the change exceeds 25% of target. Four-hour bars and a
funding filter are one trial each. Budget: at most 12 trials.

**H2, funding carry.** Long spot and short perpetual on BTC and ETH, entered when
trailing seven-day funding exceeds twice the round-trip cost spread over 28 days and
exited below it, developed on 2020-01 onwards. Its return is a published cash flow
that lasts weeks, so latency does not matter, and it is the hypothesis with the
best chance of being detectable within these power limits. It needs paper short
perpetual positions, which is an owner decision; if that is declined, H2 reverts to
funding as a regime filter on H1. Budget: at most 12 trials. Six further trials are
held in reserve.

Benchmark runs and random-sign control runs are recorded but can never be selected.

## Milestones

**M0, by 2026-11-10: safe, honest, pre-registered.** Security: every published port
bound to 127.0.0.1, database host ports removed, the research gateway and muninn's
ingest endpoints closed to the network, `.env*` and `secrets/` ignored, credentials
passed by file, and the corrected SECURITY.md; re-audit by 11-07. CTPO: the owner's
decisions memo by 11-06 and the signed pre-registration by 11-07; README, ROADMAP
and EDGE_VERDICT stop presenting order-book imbalance as an alpha or parity as
achieved. Technical: the event-time risk fix, proven by a test that reproduces fold
4 before the fix, and muninn's features moved under a `muninn.features.` prefix
with a test forbidding unprefixed topics. Interface: no retired or unverified signal
appears as live anywhere (the console's Alpha Factory, its static verdict banner and
its lifetime-as-today figures), Grafana's broken queries fixed and a Recording
Health row added per venue. Alerts: recorder gaps, flush failures and disk join the
rules, the never-firing NoFillsExecuted is fixed, and the receiver chosen by the
owner is wired through a credential file. Quant: the cause of huginn's idleness
confirmed, and fold 4 rerun with limits off.

**M1, by 2026-11-19: the gate.** The trial ledger with adversarial tests (an edited,
deleted or reordered entry, a dirty tree, unmanifested data, an early or repeated
unseal, an unauthenticated submission); loaders for klines and funding with
checksum verification and a committed manifest; true CSCV; the deflated Sharpe on
joined returns with ledger N; minimum track record length; and the seal. The
calibration report is due 11-17 and the gate is not used until it passes it. A
generated GATE_STATUS page and a console gate panel read the ledger, results and
seal. The date is two days later than first set, and this says so.

**M2, by 2026-12-01: the trials.** H1 and H2 run inside their budgets, with
benchmark and control runs, tear sheets generated per hypothesis (net of 20 bps,
intervals, deflated Sharpe, PBO, minimum track record, cost sensitivity at 15, 20
and 30 bps), and every input checksum-verified with its licence recorded. The ledger
is frozen on 12-01.

**M3, by 2026-12-15: the verdict.** The single holdout run by 12-08, the verdict
memo, a security attestation that the chain verifies from a clean clone with
exactly one unseal, and a generated verdict page. Both README outcomes are drafted
before the unseal; the matching one is merged.

## Kill criteria

If H1 and H2 both fail, strategy search stops for this product: no re-tuning and no
hypotheses invented after seeing results. If the gate fails its no-skill
calibration, no strategy is judged. If the trial budget is exhausted before a pass,
that is a fail. If the ledger's chain breaks, the iteration is void and restarts
from pre-registration.

## Interface rules for the whole iteration

No backtest figure appears without the trials used out of the budget, the deflated
Sharpe, the PBO, the buy-and-hold benchmark, the cost basis, the data window and the
generation date beside it. Net returns are the headline. Missing data reads "no
data", never 0. A status word such as RUNNING is backed by recent events, and stale
values show their age. Verdicts are generated from data, never typed. Holdout
figures appear only once it is opened, with the time it was opened. Text is at
least 12 px with contrast of at least 4.5:1, and pass or fail is shown in words, not
colour alone.

## Out of scope

Further microstructure alpha search. New execution venues. Training the served ML
model. Live trading of any kind; note that Binance's public-data terms (CC BY-NC-SA
4.0, clause 4.2) prohibit live proprietary trading based on that data, so going
live would need a different data basis, not only a different risk appetite.

## Keeps running

The recorder keeps writing both venues at about 8 GB a month, and the decile test
is re-run monthly with drift-adjusted statistics.

## Decisions for the owner

The CTPO's memo carries the rationale; the recommendations are these. Keep
`features.obi.v1` as obi-bridge's, frozen, and move muninn under its own prefix.
Send critical alerts (recorder gaps, disk, flush failures) to a channel you choose,
with its credential kept in an untracked file; a private ntfy topic needs no
account. Repoint the weekly scheduled task `norse-parity-advance` to a read-only
`norse-iteration-watch` that reports drift, completeness and the monthly decile
re-run and never touches the gate, ledger or seal. Approve paper short perpetual
positions for H2, or decline and keep H2 as a filter. Decide who holds the holdout
key, and state who has already looked at 2026 prices. Turn the macOS firewall on,
which only you can do. Approve recording ETH, SOL, XRP and DOGE as well as BTC, at
roughly four times the storage. Approve retiring huginn's unused web app.
