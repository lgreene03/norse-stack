# Edge Verdict — does the OBI strategy actually have alpha?

**Short answer: no — not on the data we have.** This page records the honest
out-of-sample verdict, run to a conclusion with the project's own validation
tooling. It is deliberately prominent because the most valuable thing a
quant-infrastructure portfolio can demonstrate is the discipline to validate a
strategy and report a *negative* result truthfully.

**Two windows are reported here: the original 24h/1,440-bar fixture, and a
2026-09-10 six-month/264,961-bar run over the same instrument.** Both are
published — the 24h numbers are not deleted or replaced. The six-month run
carries the statistical weight and is the load-bearing evidence; the contrast
between the two windows is itself part of the finding (see
[Six-month run](#six-month-run--walk-forward-2026-09-10) below).

**What this tests.** `obi` in this backtest is `(BuyVol − SellVol) / total`
computed from **executed trades** — signed taker flow. It is not the L2
order-book depth imbalance the live stack trades
(`(bid_vol − ask_vol) / total` from book state,
`services/obi-bridge/bridge.py:274`). Everything on this page is a verdict on
the trade-flow proxy only. See [docs/ROADMAP.md](ROADMAP.md) for the parity
gap between the two signals and the plan to re-run this gate against real
book depth.

## What the in-sample numbers looked like (the trap — and where it stops holding)

On the full 24h fixture, OBI looked promising: ~70% win rate, profit factor 5.85
gross, and with the net-of-cost gate at `k=1` the realized PnL flipped from −59
(235 fills) to +1 (32 fills). That is an *in-sample* result — parameters chosen
and evaluated on the same data.

**On six months this trap does not appear.** In-sample Sharpe across the four
walk-forward train windows is also strongly negative (−10.0 to −10.5) — OBI
never looks promising, even in-sample, once the window is long enough. The
apparent gross edge above was an artefact of the 24h window, not something
walk-forward had to unmask. That revises the original framing: it was not
"looks good in-sample, walk-forward destroys it" — on six months, it never
looked good in-sample to begin with.

## 24h fixture — walk-forward (the original gate)

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
  out-of-sample in **every** fold. Read honestly: on this short fixture the
  cross-config OOS Sharpes are near-tied, so PBO pins at the extreme as a
  conservative/degenerate tie-break rather than as an independently strong
  measurement. The **0/4 OOS-folds-profitable** count is the load-bearing
  evidence; PBO corroborates rather than carries the verdict.

The Deflated Sharpe Ratio reads `n/a` here only because the 24h window is too
short to form a meaningful per-period return series (equity is sampled daily) —
a separate, documented limitation. PBO does not depend on that series.

## Six-month run — walk-forward (2026-09-10)

Dataset: BTCUSDT spot, 2026-03-01 to 2026-09-01, 264,961 one-minute bars (vs
the fixture's 1,440). Built with:

```bash
go run ./cmd/fetcher --symbol BTCUSDT --source vision \
  --start 2026-03-01 --end 2026-09-01 --window 1m
```

Same gate, same command shape, longer data:

```bash
go run ./cmd/walkforward --data data/btc_6mo.jsonl --config configs/default.yaml \
  --folds 4 --thresholds 0.5,0.6,0.7,0.8
```

| Metric | 24h fixture (1,440 bars) | Six months (264,961 bars) |
|---|---:|---:|
| OOS folds profitable | 0/4 | 0/4 |
| Total OOS PnL | −146.11 | −18,845.07 |
| OOS Sharpe (mean ± std) | 0.0000 ± 0.0000 | −19.2884 ± 10.5436 |
| Deflated Sharpe Ratio | n/a (insufficient obs) | 0.0005, computed on 4/4 folds |
| P(true Sharpe ≤ 0) | n/a | 0.9995 |
| PBO | 1.0000 | 0.0000 |

**The verdict is unchanged — still 0/4 — but the statistics around it are now
real instead of degenerate.** OOS Sharpe moves from a meaningless `0.0000` with
zero cross-fold dispersion (the 24h fixture's daily-sampled equity curve has
almost nothing to compute a ratio over) to a decisively negative `−19.29`, and
the Deflated Sharpe Ratio — `n/a` on the fixture — now computes on all 4 folds
at 0.0005, with a 99.95% probability the true Sharpe is at or below zero.

### PBO moving to 0.00 is not good news

**PBO dropping from 1.00 to 0.00 must not be read as the strategy improving.**
PBO measures whether the in-sample-best configuration lands in the OOS bottom
half. At 0.00 it never did — every one of the four threshold configs loses
money out-of-sample in roughly equal measure, so there is no bottom half for
the in-sample winner to fall into. Config selection was never the failure mode
here; the signal has no edge across the whole grid, at every threshold.
**PBO cannot detect overfitting when nothing in the grid is profitable to
overfit to.** The 24h fixture's PBO = 1.00 was already flagged above as a
conservative tie-break on near-tied OOS Sharpes, not an independent
measurement; the six-month PBO = 0.00 is the same instrument correctly
reporting "no discrimination possible," not "no overfitting." On either
window, the 0/4 OOS-folds-profitable count and the OOS Sharpe carry the
verdict — PBO corroborates, it does not carry it.

### An unexplained observation: fold 4's PnL

Fold 4's train PnL (−10,000.6075) and test PnL (−10,001.7209) both land within
about 1.2 of a round −10,000, against an `initial_cash` of 100,000. No loss
cap was found in the config or in `internal/research/research.go`. This is
recorded as an observation, not an explanation — nothing in the code or config
reviewed for this page accounts for it.

### Losses scale with turnover

Fold 4 also has the highest fill count of the six-month run (18,224 fills) and
carries the largest single-fold OOS loss (−10,001.72). This is consistent with
the existing [fee-dominance case study](case-studies/fee-dominance.md): more
fills means more fee/slippage drag, and on this signal that drag compounds a
loss rather than eating into a gain.

## Interpretation

OBI (the signed taker-flow proxy, not book-depth imbalance — see above), as
implemented, has **no demonstrable out-of-sample edge on either window
tested**. On the 24h fixture the apparent gross edge is in-sample overfitting
that walk-forward correctly destroys — the textbook walk-forward story. On six
months there is no gross edge to destroy in the first place: in-sample Sharpe
is already strongly negative, so the strategy fails before walk-forward gets
involved. Both are honest negative results, negative for different reasons.
The six-month run is the one with statistical weight; the 24h numbers are
limited by sample size, not by a different underlying conclusion.

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

- One instrument (BTC spot) on both windows tested: the 24h fixture (1,440
  one-minute bars) and the 2026-09-10 six-month run (264,961 bars, BTCUSDT,
  2026-03-01 to 2026-09-01). The six-month run addresses the sample-size
  concern the 24h fixture carried on its own — OOS Sharpe and Deflated Sharpe
  are now computable and decisively negative, and 0/4 profitable OOS folds
  holds on ~184x the data. It remains a single instrument; nothing here
  generalizes to other symbols without re-running the gate on them.
- This tests the trade-flow proxy `obi = (BuyVol − SellVol) / total` computed
  from executed trades, not the L2 order-book depth imbalance the live stack
  trades. See [docs/ROADMAP.md](ROADMAP.md) for the parity gap and the plan to
  re-run this gate against the real book-depth signal.
- The ML-confidence layer that would normally gate signals is currently
  untrained (emits a near-constant ~0.08), so this verdict is for the bare
  OBI-threshold signal. A trained ML filter could change the picture; that is a
  data problem, recorded in `PRODUCTION_READINESS.md`.
- `huginn`'s walk-forward output includes a `Hit: 0.0%` line. That value is
  hardcoded at `internal/research/research.go:402` and is not a measurement —
  it is not cited anywhere on this page or in `RESULTS.md`.

## OU mean-reversion — same gate, same verdict (but more disciplined)

The OU mean-reversion strategy was put through the identical walk-forward + PBO
gate on the same 24h fixture (sweeping the |z| entry band {1.5, 2.0, 2.5, 3.0},
60-bar OLS window):

```
OOS folds profitable: 0/4 (0%)
Total OOS PnL:        -12.83
PBO:                  1.0000
```

**Also no out-of-sample edge** — 0/4 folds, PBO = 1.00. But note the contrast
with OBI: OU loses **−12.83 total vs OBI's −146.11**, because it trades ~10× less
(7–9 OOS fills per window vs OBI's 68–119). Its z-score band + half-life exits +
trend-guard make it far more disciplined, so it *bleeds less* — but discipline is
not edge. It still does not make money out-of-sample on this data.

Honest caveat specific to OU: the test window is ~24h of BTC, which is not
obviously a **mean-reverting** regime (OU's whole premise). The OU trend-guard
correctly refuses to trade when it can't fit a mean-reverting process, which is
why fill counts are low. A fair test of OU needs a dataset/instrument with a
demonstrated mean-reverting (or cointegrated-pair) structure — captured over a
longer window. Until then, OU is "promising machinery, unproven on this data,"
not a validated alpha.

**Bottom line across both strategies:** neither OBI nor OU has demonstrable
out-of-sample edge here. The honest path to alpha is better *signals/data*
(a mean-reverting pair for OU, a trained ML filter, a longer multi-regime window),
validated through this same gate — not more parameter tuning of what we have.

The six-month re-run described above (2026-09-10) was performed for OBI only;
OU has not yet been re-run on the longer window.

## How to reproduce

24h fixture:

```bash
cd huginn
# OBI
go run ./cmd/walkforward --data data/btc_test.jsonl --config <obi.yaml> \
  --folds 4 --thresholds 0.5,0.6,0.7,0.8
# OU (sweep the |z| entry band)
go run ./cmd/walkforward --data data/btc_test.jsonl --config <ou.yaml> \
  --folds 4 --thresholds 1.5,2.0,2.5,3.0
```

Six-month run (OBI only, 2026-09-10):

```bash
cd huginn
go run ./cmd/fetcher --symbol BTCUSDT --source vision \
  --start 2026-03-01 --end 2026-09-01 --window 1m
go run ./cmd/walkforward --data data/btc_6mo.jsonl --config configs/default.yaml \
  --folds 4 --thresholds 0.5,0.6,0.7,0.8
```

See also [RESULTS.md](RESULTS.md) for the full-sample numbers and the
cost-sweep frontier.
