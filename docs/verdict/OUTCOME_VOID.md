## Pre-registered verdict on H1 and H2

**No verdict on H1 or H2: the trial ledger's hash chain broke, so the iteration is void.**

The kill criteria set for this iteration say that if the ledger's hash chain breaks, the
iteration is void and restarts from pre-registration. The chain is what makes every count and
every figure checkable: it shows that no trial was left out, edited or reordered, and N in
every deflated Sharpe ratio is read from it. On {{VOID_DATE}} verification found the chain
broken at ledger entry `{{VOID_ENTRY}}`; [the verification record]({{VOID_REPORT}}) shows how
it broke and what was found about the cause.

From that point nothing this iteration produced can be shown to be complete, so no development
or holdout figure is reported here, and whatever the ledger holds stands as evidence of
nothing, in either direction. A verdict that cannot be audited is worth less than no verdict,
which is why the registration voids the iteration rather than repairing the record, and why
this is published as plainly as a verdict would have been.

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

The iteration restarts from pre-registration, as registered. Norse makes no claim to trade with
an edge while there is no verdict. Whether the restart may keep the same holdout depends on
whether it was opened before the break, which [the security attestation]({{ATTESTATION}})
records; that, the new dates and the fix to whatever broke the chain are the owner's decisions,
recorded in [DECISIONS.md](docs/DECISIONS.md) before the restart. The harness, the seal and
Norse's own recording of the Binance and OKX spot order books remain, and the restart runs on
them once the cause of the break is fixed.

### Reproduce it

The break can be confirmed from a clean clone with Python 3.12 and no account, API key or paid
data; the research package uses the standard library only.

```bash
git clone https://github.com/lgreene03/norse-stack.git
cd norse-stack
git checkout {{VERDICT_COMMIT}}
cd research
python -m norse_research.gitledger sync      # fetch the trial ledger
python -m norse_research.gitledger verify    # reports where the chain breaks
```

These commands report the break; the verification record above gives the output verification
produced on {{VOID_DATE}}.

*This section was written on {{DRAFT_DATE}}, before any trial had run, alongside a text for
every other outcome this iteration could have, in commit `{{DRAFT_COMMIT}}`
([docs/verdict](docs/verdict/README.md)). It is published as written, with only its
placeholders filled from the generated verdict, and anyone can check it against that commit.*
