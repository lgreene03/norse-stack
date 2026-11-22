## Pre-registered verdict on H1 and H2

**H1, dated-universe trend: {{H1_VERDICT}}. H2, funding carry: {{H2_VERDICT}}.**

No hypothesis passed. {{OTHER_HYPOTHESIS}}'s candidate, {{OTHER_CANDIDATE}}, met all five
development criteria registered before any trial ran, but it never read the holdout, and under
this registration it never will. The holdout table below shows which of two reasons applies. If
it names a holdout candidate and an unseal, both hypotheses passed development, and that
candidate ranked higher on the development deflated Sharpe ratio, compared unrounded with ties
going to H1, so the registered rule gave it the holdout, which is open to one candidate only;
on the holdout, sealed in code until the unseal, it did not meet HOLD-1. If the table reads "not
opened", the other hypothesis is NOT RUN: its grid was neither complete nor out of budget when
the verdict was given, and the registration allows no unseal until both grids are, so the
holdout stayed sealed and no candidate read it (decision D23).

{{OTHER_HYPOTHESIS}}'s result is a development pass and nothing more. It is not a pass, and it
is not evidence that the holdout would have gone differently for it: its development figures,
in the table, cleared the same corrections as every other, but it lacks the one check the
registration requires before anything is called a pass. The rules that decide who reads the
holdout were fixed before any trial ran: the one that chooses between two development passes,
so that the choice could not be made after anyone had seen how either did in 2026, and the one
that keeps the holdout shut until both grids are complete or exhausted, so that it could not be
opened for one hypothesis while the other could still run. Presenting {{OTHER_HYPOTHESIS}} as
the one that would have worked would undo those rules, and this text does not.

If the holdout table shows a result entry, the holdout candidate's result is the failure the
holdout exists to catch: a development record that survived every correction for the number of
trials run, followed by a check on months the rule that chose it never read, which it did not
pass. The check is coarse in this direction as well: over 304 days a strategy with a true net
Sharpe ratio of 1.0 shows a positive Sharpe ratio only 82% of the time, so the result is that
no edge appeared on the one window the registration allows, not proof that none exists. If the
table shows an unseal but no result entry, no holdout return was ever computed, and the
registration counts that as a fail without saying anything about 2026. Either way the holdout
candidate's result is a fail, and it stands. If the table reads "not opened", no candidate was
tested on the holdout, and no result in this section comes from 2026.

### Results

Figures are net of costs, computed by the gate from the return series committed in the trial
ledger, and generated on {{GENERATED_AT}}. Sharpe ratios are annualised by the square root of
365, and every criterion is judged on unrounded values. A figure that does not exist for this
outcome reads "not computed", never 0.

| | H1, dated-universe trend | H2, funding carry |
|---|---|---|
| Verdict | {{H1_VERDICT}} | {{H2_VERDICT}} |
| Development window | 2018-05-01 to 2025-12-31, 2,802 days | 2020-01-08 to 2025-12-31, 2,185 days |
| Cost basis | 10 bps fee and 2 bps slippage on every fill | 10 bps fee and 2 bps slippage on every fill of each leg |
| Configurations with a selectable run (pre-registration, section 9) | {{H1_SELECTABLE}} of 8 | {{H2_SELECTABLE}} of 6 |
| Trial entries used | {{H1_TRIALS}} of 12, and {{H1_RESERVE}} from the shared reserve of 6 | {{H2_TRIALS}} of 12, and {{H2_RESERVE}} from the shared reserve of 6 |
| Candidate | {{H1_CANDIDATE}} | {{H2_CANDIDATE}} |
| Candidate's net annualised return | {{H1_RETURN}} | {{H2_RETURN}} |
| Candidate's net Sharpe ratio, with 90% interval | {{H1_SHARPE}} ({{H1_SHARPE_INTERVAL}}) | {{H2_SHARPE}} ({{H2_SHARPE_INTERVAL}}) |
| Benchmark's Sharpe ratio (H1: equal-weight hold of the same dated universe; H2: BTC and ETH held half and half) | {{H1_BENCHMARK_SHARPE}} | {{H2_BENCHMARK_SHARPE}} |
| DEV-1: deflated Sharpe ratio above 0.95 | {{H1_DSR}}, {{H1_DEV1}} | {{H2_DSR}}, {{H2_DEV1}} |
| DEV-2: deflated Sharpe ratio of the return in excess of the benchmark above 0.95 (H1 volatility matched, H2 beta matched) | {{H1_EXCESS_DSR}}, {{H1_DEV2}} | {{H2_EXCESS_DSR}}, {{H2_DEV2}} |
| DEV-3: probability of backtest overfitting below 0.5 | {{H1_PBO}}, {{H1_DEV3}} | {{H2_PBO}}, {{H2_DEV3}} |
| DEV-4: Sharpe ratio above 0 with each calendar year removed (the lowest shown) | {{H1_YEAR_OUT_SHARPE}} without {{H1_YEAR_OUT_YEAR}}, {{H1_DEV4}} | {{H2_YEAR_OUT_SHARPE}} without {{H2_YEAR_OUT_YEAR}}, {{H2_DEV4}} |
| DEV-5: Sharpe ratio above 0 without the 20 best days | {{H1_BEST20_OUT_SHARPE}}, {{H1_DEV5}} | {{H2_BEST20_OUT_SHARPE}}, {{H2_DEV5}} |
| Passed development | {{H1_DEVELOPMENT}} | {{H2_DEVELOPMENT}} |
| Candidate's percentile among its hypothesis's 100 control runs (reported, not a criterion) | {{H1_CONTROL_PERCENTILE}} | {{H2_CONTROL_PERCENTILE}} |

| Holdout, 2026-01-01 to 2026-10-31, 304 days | |
|---|---|
| Holdout candidate | {{HOLDOUT_CANDIDATE}} |
| Unsealed | {{UNSEAL_TIME}}, ledger entry `{{UNSEAL_ENTRY}}`, at commit `{{UNSEAL_COMMIT}}` |
| Holdout runs that reached the holdout data | {{HOLDOUT_RUNS}} |
| Result entry | `{{HOLDOUT_RESULT_ENTRY}}` |
| Net return over the 304 days | {{HOLDOUT_RETURN}} |
| Net Sharpe ratio | {{HOLDOUT_SHARPE}} |
| HOLD-1 lower bound | {{HOLDOUT_BOUND}} |
| HOLD-1: Sharpe ratio above 0 and not below the bound | {{HOLD1}} |

### How it was tested

Both hypotheses, every configuration to be tried, the costs, the benchmarks and every pass mark
were fixed before any trial ran, in the [pre-registration](docs/PREREGISTRATION.md) that the
owner signed, tagged `{{PREREG_TAG}}` at `{{PREREG_COMMIT}}`. At each month end H1 takes the
ten largest Binance USDT spot pairs by 30-day volume, delisted pairs included, and holds the
first five its rules allow, with cash in any empty slot, so that no coin is picked with
hindsight; each is long or flat on the signs of its 20, 60 and 120-day returns, sized to 20%
annualised volatility and never more than a fifth of equity. Its grid varies the lookbacks, the
rebalance band, the bar length, a funding filter and the volatility scaling: eight
configurations. H2 holds BTC and ETH spot long and their USDT-margined perpetuals short in
equal quantities, earning the funding that longs pay shorts, while trailing funding clears a
threshold set by the round-trip cost. Its grid varies the threshold, the exit rule and the
lookback, and includes holding the carry throughout: six configurations. Every fill of every
leg is charged a 10 bps fee and 2 bps of slippage, which is 24 bps for a round trip on one leg
and 48 bps to open and close a carry position, with no fee discount and no rebate. The data is
Binance's public archive alone, every file pinned by hash in the manifests
`{{DEV_MANIFEST_SHA256}}` (development) and `{{HOLDOUT_MANIFEST_SHA256}}` (holdout).

Every run was entered in an append-only, hash-chained trial ledger before its result existed,
and failed runs and reruns count. Development closed on {{DEV_CLOSE_DATE}} at ledger entry
`{{DEV_CLOSE_ENTRY}}`, with {{N}} trial entries across both hypotheses and the reserve, and
every deflated Sharpe ratio above is charged for all of them: given that count and the length
of each window, the best of {{N}} independent trials with no skill would be expected to show an
annualised Sharpe ratio of about {{H1_SR0}} over H1's window and {{H2_SR0}} over H2's. Each
hypothesis is judged on one configuration only, its candidate, the one with the highest
development deflated Sharpe ratio, so no search of its grid can find one that happens to clear
every criterion. Before any strategy was judged, the gate was calibrated on simulated ledgers:
in its worst family of ledgers with no skill it passed {{CAL_NOSKILL_PASS_RATE}} of 1,000 at
N = 30, against a limit of 5% in every family. With one configuration carrying a planted net
Sharpe ratio of 2.0, the planted hypothesis passed development in {{CAL_DETECTION_H1}} of the
ledgers for H1 and {{CAL_DETECTION_H2}} for H2, against a bar of more than half in each such
family. At a planted 1.5 the share was {{CAL_POWER_H1_SINGLE}} for H1 and
{{CAL_POWER_H2_SINGLE}} for H2 when one configuration carries the edge, and
{{CAL_POWER_H1_SHARED}} and {{CAL_POWER_H2_SHARED}} when every configuration shares it; the
[calibration report]({{CAL_REPORT}}) gives the rest, the figures at 1.0 included. The holdout, 2026-01-01 to 2026-10-31, was sealed in code, its files
kept off disk, and could be opened once, with the owner's key, for the single candidate a
mechanical rule selects.

### What the verdict words mean

The words are the registration's own (its section 11), applied to each hypothesis by the gate,
not chosen by anyone. **PASS**: the hypothesis's candidate met all five development criteria,
it was the holdout candidate (the higher development deflated Sharpe ratio of the hypotheses
that passed development, compared unrounded, ties going to H1), and on the holdout its Sharpe
ratio was above zero and no more than 1.645 standard errors below its development estimate
(HOLD-1). **PASSED DEVELOPMENT, NOT TESTED ON THE HOLDOUT**: its candidate met all five
development criteria, but either the other hypothesis's candidate ranked higher and took the
holdout, or the other hypothesis's grid was neither complete nor out of budget when the verdict
was given, so no unseal could happen and the holdout stayed sealed (decision D23). It is not a
pass, and under this registration it can never be tested or become one, because the holdout
opens only once both grids are complete or exhausted, and then to one candidate only. **FAIL**: its candidate missed at least one development
criterion; or it was the holdout candidate and HOLD-1 did not hold, which includes a holdout
run that never returned a result; or its own trial entries and the reserve ran out before it
could pass. **NOT RUN**: when development closed, not every one of its registered
configurations had a selectable run, meaning a completed run over the whole window with all of
them on the same harness and strategy code, and its budget was not exhausted. It is neither a
pass nor a fail and says nothing about whether the strategy works, because a hypothesis is
judged on its whole grid or not at all, so that the order in which configurations happened to
run cannot decide the result.

### What the gate cannot see

A fail here means no edge large enough to detect in six to eight years of daily data, not no
edge. The bar is high by design: at N = 30, the most the budget allows, a strategy with a true
net Sharpe ratio of 1.0 passes the first development criterion only about one time in six for
H1 and one time in ten for H2, so a strategy worth trading at a Sharpe ratio of 0.7 to 1.0
would most likely be reported as a fail. A pass is evidence from one holdout window, not a
guarantee. Over 304 days a strategy with a true Sharpe ratio of 1.0 shows a positive holdout
Sharpe ratio 82% of the time, and one with 1.5 does so 91% of the time, so the holdout can
reject a strategy that has stopped working but cannot confirm one that works. It is also
contaminated by hindsight: 2026 prices are public and known to everyone involved, the AI models
included, so it can only catch a failure. A candidate's development Sharpe ratio belongs to the
configuration chosen as the best of its grid, so it and its interval are biased upward; the
deflated Sharpe ratio corrects the test for that choice, not the estimate.

The probability of backtest overfitting is a weak criterion on its own: it passes a family with
no skill about half the time, and when every configuration of a hypothesis shares one real edge
it fails that edge about half the time. The configurations were written by people who know how
crypto behaved from 2018 to 2025, so nothing was fitted but nothing was unseen either, and the
deflated Sharpe ratio charges for the trials run, not for ideas rejected in conversation before
registration. H1's result speaks for 2018 to 2025 and H2's for 2020 to 2025; only the holdout
candidate's result also speaks for the first ten months of 2026. Funding carry was attractive
on only about 8% of days in 2025, so a carry result that rests on 2020 and 2021 says little
about today. H2's model treats its spot and perpetual legs as one jointly margined book, so it
cannot see a forced liquidation of the short leg in a rally or the failure of an exchange, and
the rule that closes a position in a pair that stops trading at that day's low errs against the
strategy but is not a worst case.

### Not a live product

Neither hypothesis is a live product as things stand, whatever its verdict, and Norse trades on
paper only. Both were developed and tested on Binance's public archive, whose terms (CC
BY-NC-SA 4.0, clause 4.2) prohibit live proprietary trading based on that data, so trading
either with money would need a different data basis, not only a different appetite for risk. H2
also needs a short perpetual position, a crypto derivative that cannot be sold to UK retail
customers; [decision D3](docs/DECISIONS.md) approved that leg for research and paper trading
only. Whether Norse later studies an asset class its owner could trade, such as daily trend
across futures or UCITS ETFs, is an open decision ([D10](docs/DECISIONS.md)), and this verdict
does not settle it.

### What comes next

No hypothesis passed, so the claim that Norse trades with an edge is retired here, in writing,
as it would be on a fail. The kill criteria stop strategy search when both hypotheses fail;
{{OTHER_HYPOTHESIS}} did not fail, but nothing left in this registration can test it. The only
evidence that could is data no one has yet seen, through a new pre-registration with a holdout
of its own or a forward paper test registered as a goal of its own. Whether to do either is the
owner's decision when the next iteration's goal is drafted, and its result would be a new
verdict with its own date, never an amendment to this one. Meanwhile Norse continues as what
this iteration built: a strategy-falsification lab, in which a hypothesis registered before it
runs becomes a verdict anyone can reproduce from a clean clone, with every trial counted in a
hash-chained ledger, every input pinned by hash and a holdout sealed in code, over Binance's
public archive and Norse's own recording of the Binance and OKX spot order books, an archive no
free source offers as history.

### Reproduce it

Every figure above can be regenerated from a clean clone with Python 3.12 and no account, API
key or paid data; the research package uses the standard library only.

```bash
git clone https://github.com/lgreene03/norse-stack.git
cd norse-stack
git checkout {{VERDICT_COMMIT}}
cd research
python -m norse_research.gitledger sync      # fetch the trial ledger
python -m norse_research.gitledger verify    # the hash chain, its anchors and the run rules
python -m norse_research.datasets sync       # mirror the pinned development data (set NORSE_DATASETS_DIR)
python -m norse_research.datasets verify     # every file against its manifest
{{VERDICT_COMMAND}}
```

The last command recomputes every figure in this section from the ledger at `{{LEDGER_HEAD}}`,
marked by the tag `{{LEDGER_TAG}}`, and prints the verdict words. The [security
attestation]({{ATTESTATION}}) runs the same checks from a clean clone and confirms exactly one
unseal. The [independent reproduction]({{REPRODUCTION}}), by an agent with no access to this
iteration's conversations, regenerated these figures exactly on {{REPRODUCTION_DATE}}. The
[verdict memo]({{VERDICT_MEMO}}) gives every criterion with the ledger entry, manifest hash and
commit it rests on.

*This section was written on {{DRAFT_DATE}}, before any trial had run, alongside a text for
every other outcome this iteration could have, in commit `{{DRAFT_COMMIT}}`
([docs/verdict](docs/verdict/README.md)). It is published as written, with only its
placeholders filled from the generated verdict, and anyone can check it against that commit.*
