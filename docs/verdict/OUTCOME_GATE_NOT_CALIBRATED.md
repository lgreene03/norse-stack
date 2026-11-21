## Pre-registered verdict on H1 and H2

**No verdict on H1 or H2: the gate did not pass its calibration, so no strategy was judged.**

The registration allowed a strategy to be judged only by a gate that had first shown, on
simulated data, that it can tell skill from luck. It had to pass at most 5% of 1,000 simulated
trial ledgers with no skill at N = 30, built on the registered windows with families of trials
correlated within and across the two hypotheses, and it had to detect a planted net Sharpe
ratio of 1.5. It did not pass: either a figure in the table below missed its mark, or the
calibration did not complete before the verdict, and the table shows which. The report gives
the method, the seeds, the code and the figures at 1.0 and 2.0.

| Calibration (G1.7), generated on {{GENERATED_AT}} | |
|---|---|
| Ledgers with no skill the full gate passed, of 1,000 at N = 30 (limit 5%) | {{CAL_NOSKILL_PASS_RATE}}, {{CAL_FALSE_PASS_RESULT}} |
| Planted net Sharpe ratio of 1.5 in one configuration: share of H1 families passed | {{CAL_POWER_H1_SINGLE}} |
| Planted net Sharpe ratio of 1.5 in one configuration: share of H2 families passed | {{CAL_POWER_H2_SINGLE}} |
| Planted net Sharpe ratio of 1.5 shared by every configuration: share of H1 families passed | {{CAL_POWER_H1_SHARED}} |
| Planted net Sharpe ratio of 1.5 shared by every configuration: share of H2 families passed | {{CAL_POWER_H2_SHARED}} |
| Detection bar for the planted Sharpe ratio of 1.5 | {{CAL_POWER_BAR}}, {{CAL_POWER_RESULT}} |
| Report | {{CAL_REPORT}} |

So, as the registration requires, no strategy was judged. This is a result about the
instrument, not about H1 or H2: nothing is known from this iteration about whether either
strategy works, in either direction, and no development or holdout figure is reported here,
because the registration gives such figures standing as evidence only through a calibrated
gate. The holdout was never unsealed, and any trial entries in the ledger ({{N}} when this was
published) stay there unjudged. Declaring the gate failed is not a way out of a bad result:
calibration runs on simulated ledgers, before any strategy is judged, against the 5% limit
registered with everything else and the detection bar the report states, with the date it was
fixed. A gate that did not pass its own specification was not used, which is the rule working
as written.

### What was registered

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
Binance's public archive alone, pinned by hash.

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

No strategy passed, because none was judged, so the claim that Norse trades with an edge is
retired here, in writing, as it would be on a fail. Changing the gate means a new
pre-registration, which the registration's section 16 allows only until the first ledger entry
after the genesis entry; whether to write one and run H1 and H2 under a gate that calibrates is
the owner's decision when the next iteration's goal is drafted, and its result would be a new
verdict with its own date, never an amendment to this one. The calibration report stands as a
finding of its own, reproducible from its recorded seeds: how often a gate of this design, with
this budget and on these windows, would pass strategies with no skill, and how often it would
see a real edge. Norse continues as a strategy-falsification lab, in which a hypothesis
registered before it runs becomes a verdict anyone can reproduce from a clean clone, with every
trial counted in a hash-chained ledger, every input pinned by hash and a holdout sealed in
code, over Binance's public archive and Norse's own recording of the Binance and OKX spot order
books, an archive no free source offers as history.

### Reproduce it

The calibration can be rerun from a clean clone with Python 3.12 and no account, API key or
paid data; the research package uses the standard library only.

```bash
git clone https://github.com/lgreene03/norse-stack.git
cd norse-stack
git checkout {{VERDICT_COMMIT}}
cd research
{{CAL_COMMAND}}
```

The last command reruns the calibration with the seeds its report records and prints the
figures above.

*This section was written on {{DRAFT_DATE}}, before any trial had run, alongside a text for
every other outcome this iteration could have, in commit `{{DRAFT_COMMIT}}`
([docs/verdict](docs/verdict/README.md)). It is published as written, with only its
placeholders filled from the generated verdict, and anyone can check it against that commit.*
