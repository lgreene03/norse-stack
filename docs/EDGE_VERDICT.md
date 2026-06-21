# Edge Verdict — does the OBI strategy actually have alpha?

**Short answer: no — not on the data we have.** This page records the honest
out-of-sample verdict, run to a conclusion with the project's own validation
tooling. It is deliberately prominent because the most valuable thing a
quant-infrastructure portfolio can demonstrate is the discipline to validate a
strategy and report a *negative* result truthfully.

## What the in-sample numbers looked like (the trap)

On the full 24h fixture, OBI looked promising: ~70% win rate, profit factor 5.85
gross, and with the net-of-cost gate at `k=1` the realized PnL flipped from −59
(235 fills) to +1 (32 fills). That is an *in-sample* result — parameters chosen
and evaluated on the same data.

## What proper walk-forward says (the truth)

Anchored walk-forward (expanding train window, sliding test window), 4 folds,
grid-searching `threshold ∈ {0.5, 0.6, 0.7, 0.8}` on each train window and
applying only the in-sample winner to the next unseen window:

```
═══ Walk-Forward Summary ═══
Folds:                4
OOS folds profitable: 0/4 (0%)
Total OOS PnL:        -146.11
─── Probability of Backtest Overfitting (CSCV) ───
PBO: 1.0000  (fraction of folds where the IS-best config was OOS bottom-half)
```

Two numbers settle it:

- **0 of 4 out-of-sample folds were profitable.** Every time we picked the best
  parameters on the past and traded them on the future, we lost money.
- **PBO = 1.00.** The Probability of Backtest Overfitting (Bailey & López de
  Prado, combinatorially-symmetric cross-validation) is the maximum possible:
  the configuration that looked best in-sample landed in the *bottom half*
  out-of-sample in **every** fold. The parameter selection is pure noise-fitting.

The Deflated Sharpe Ratio reads `n/a` here only because the 24h window is too
short to form a meaningful per-period return series (equity is sampled daily) —
a separate, documented limitation. PBO does not need that and is conclusive.

## Interpretation

OBI order-book imbalance, as implemented, has **no demonstrable out-of-sample
edge on this dataset**. The apparent gross edge is in-sample overfitting that
walk-forward correctly destroys — which is exactly what walk-forward is for.

This re-frames the rest of the system honestly:

- The **net-of-cost gate** is *damage control*, not alpha. It removes
  fee-bleeding marginal trades (turnover 21x → ~3x, net less negative), but it
  cannot manufacture edge that is not in the signal. Useful, not magic.
- **Maker/taker execution**, **signed-position shorting**, and the rest of the
  execution stack are correct engineering that would matter *if* there were
  edge — they do not create it.
- The honest next step for *alpha* is a different signal, not more tuning of OBI:
  the **OU mean-reversion** strategy (a price-process model rather than a
  microstructure heuristic) is the next candidate, and it should be put through
  this same walk-forward + PBO gate before being trusted. A negative result
  there would be reported here too.

## Caveats on the verdict itself

- One instrument (BTC-USD), ~24h, 1,440 one-minute bars. A negative result on a
  short window is suggestive, not the final word — but PBO = 1.0 is a strong
  signal regardless of sample size, and the burden of proof is on the strategy.
- The ML-confidence layer that would normally gate signals is currently
  untrained (emits a near-constant ~0.08), so this verdict is for the bare
  OBI-threshold signal. A trained ML filter could change the picture; that is a
  data problem, recorded in `PRODUCTION_READINESS.md`.

## How to reproduce

```bash
cd huginn
go run ./cmd/walkforward --data data/btc_test.jsonl --config <obi.yaml> \
  --folds 4 --thresholds 0.5,0.6,0.7,0.8
```

See also [RESULTS.md](RESULTS.md) for the full-sample numbers and the
cost-sweep frontier.
