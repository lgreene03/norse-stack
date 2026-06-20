# Backtest Results

**These are simulated-execution results on a short window of public data. They are
NOT live-trading results and must not be read as evidence of a profitable
strategy.** See the [Honest Caveats](#honest-caveats) section before quoting any
number on this page.

This page records the *actual, unedited* output of the Huginn backtester,
calibrator, and walk-forward validator run against a real captured feature
dataset. Where a metric comes out as `0.0000` it is reported as `0.0000` — no
number on this page has been hand-edited, smoothed, or invented. The point of
this document is to demonstrate the *measurement machinery* (deterministic
replay, buy-and-hold benchmark, parameter sweep, anchored walk-forward, honest
multiple-testing warnings), not to claim alpha.

---

## Data window & source

| Field | Value |
|-------|-------|
| File | `huginn/data/btc_test.jsonl` |
| Instrument | `BTC-USD` (single instrument) |
| Rows | 1,440 `FeatureEvent` records (1-minute bars) |
| Window start | `2026-05-18T01:01:00+01:00` |
| Window end | `2026-05-19T01:00:00+01:00` |
| Span | ~24 hours (≈2 calendar days) |
| Features per row | `microPrice`, `obi`, `volume`, `vpin`, `vwap` |
| Origin | Feature events captured from the running Norse Stack (Muninn → Redpanda `features.obi.v1`). To capture a fresh window yourself: `./scripts/capture-features.sh --duration 1h` (the stack must be up), then point the backtester at the resulting `data/features-YYYY-MM-DD.jsonl`. |

Buy-and-hold benchmark over the same window: **−0.57%** (BTC-USD drifted slightly
down across the 24h). This benchmark is computed by the backtester itself
(`internal/backtest/benchmark.go` → `BenchmarkBuyHold`) by marking an
equal-notional basket of every priceable instrument to market.

---

## Per-strategy backtest

Command pattern (run from the `huginn/` repo):

```bash
# the journal is append-only, so truncate it before each clean run
: > data/backtest_trades.jsonl
go run ./cmd/backtest --data data/btc_test.jsonl --config <config.yaml>
```

Configs used: a local `obi` config (threshold 0.70, `database.enabled: false`),
`configs/vpin_breakout.yaml`, `configs/vwap_deviation.yaml`,
`configs/ema_crossover.yaml`. Executor cost model in every config:
**transaction cost 5 bps + slippage 2 bps** per fill.

| Strategy | Fills | Realized PnL | Hit rate | Turnover | Sharpe | MaxDD | Strategy ret. | Buy-hold ret. | Excess |
|----------|------:|-------------:|---------:|---------:|-------:|------:|--------------:|--------------:|-------:|
| OBIThreshold(0.70) | 235 | −59.04 | 49.5% | 21.31x | 0.0000 | 0.02% | −0.06% | −0.57% | +0.51% |
| VPINBreakout(0.50) | 548 | 0.00 | 0.0% | 1.00x | 0.0000 | 0.30% | +0.43% | −0.57% | +1.00% |
| VWAPDeviation(0.0005) | 0 | 0.00 | 0.0% | 0.00x | 0.0000 | 0.00% | +0.00% | −0.57% | +0.57% |
| EMACrossover(10,30) | 48 | −28.05 | 16.7% | 47.62x | 0.0000 | 0.00% | −0.03% | −0.57% | +0.54% |

All four strategies land within ±0.5% of flat on this window and all four edge
out passive buy-and-hold only because BTC drifted down −0.57% over the 24h — i.e.
the "excess return" here is mostly *not being long a falling market*, not signal
alpha. The walk-forward section below is the real test, and it is negative.

Raw terminal output for the OBI run (representative; the others are identical in
shape):

```
═══ Backtest Summary ═══
Strategy:        OBIThreshold(0.70)
Initial Cash:    100000.00
Final Value:     99939.37
Realized PnL:    -59.0439
Total Fills:     235
Max Drawdown:    0.02%
Sharpe Ratio:    0.0000
Hit Rate:        49.5%
Turnover:        21.31x
Avg Hold:        12549s
─── vs Buy-and-Hold ───
Strategy Return: -0.06%
Buy-Hold Return: -0.57%  (1 instrument(s))
Excess Return:   +0.51%
Info Ratio:      0.0000
════════════════════════
```

### Why Sharpe, Sortino and MaxDD are 0.0000 here (important)

The equity curve is sampled **once per calendar day**
(`internal/backtest/engine.go`, `Run`). This dataset spans ~24 hours, i.e. only
2–3 daily equity points. Sharpe/Sortino need a *series* of period returns and
MaxDD needs intra-series variation; with 2–3 points there is essentially nothing
to compute a ratio over, so they correctly degrade to `0.0000` rather than
emitting a noisy, meaningless figure. **This is the headline limitation of a
short single-run window: it cannot produce a meaningful risk-adjusted metric.**
(See the next section, which is the direct answer to "can a ~2-hour live run give
me a Sharpe?".)

### Two equity-accounting bugs found (and fixed) while producing this page

An earlier draft of this page reported the OBI run as `Final Value 89,153.86`
with `Strategy Return +4.48%` and `Excess +5.06%`. Those two numbers were
mutually contradictory (a −10.8% terminal value cannot coexist with a +4.5%
return), which is what flagged the problem. Re-deriving the numbers by hand
against the trade journal — net open position of only ~0.03 BTC, realized PnL of
just −59, ~$91 of fees — showed that *neither* figure was right. Two real bugs
were behind it, both now fixed:

1. **`Portfolio.Snapshot` computed total equity as `cash + unrealizedPnL`**
   (`internal/portfolio/portfolio.go`). Equity must be `cash + market value of
   open positions`; cash already paid out each position's cost basis on the buy,
   so adding back only the unrealized PnL silently dropped that cost basis and
   understated total value by it whenever inventory was open. With the OBI
   strategy's sells clipped by the long-only rule, the run ended holding a sizable
   accumulated long, so its cost basis (~$10.8k) vanished from `Final Value`. A
   unit test had even encoded the wrong value (a fair-value buy "halving" the
   account); it is corrected.
2. **`StrategyTotalReturn` measured return from `equity[0]`** — the first
   *daily-sampled* equity point, a mid-window mark — instead of the initial cash
   cost basis. That made it incomparable to the buy-and-hold benchmark (which is
   measured from initial cash) and could flip a losing run positive. It now
   measures `finalValue / initialCash − 1`, the same basis as the benchmark.

After the fix the numbers reconcile: `Final Value 99,939.37`, `Strategy Return
−0.06%`, `Excess +0.51%` — consistent with realized −59 plus fees on a
near-flat book. The bugs corrupted the equity curve, drawdown, and every return
derived from them whenever a position was open (including the live operator
dashboard), so the fix matters well beyond this page. This is left in the record
deliberately: the value of a benchmark + a skeptical re-derivation is that they
catch your own measurement bugs.

---

## Walk-forward validation (the honest answer to "what's the real edge?")

Anchored, expanding-train / sliding-test walk-forward on the OBI strategy,
4 folds, grid-searching `threshold ∈ {0.5, 0.6, 0.7, 0.8}` on each train window
and applying the in-sample winner to the next out-of-sample window:

```bash
go run ./cmd/walkforward --data data/btc_test.jsonl \
  --config <obi.yaml> --folds 4 --thresholds 0.5,0.6,0.7,0.8
```

Real output (summary; per-fold detail below):

```
═══ Walk-Forward Summary ═══
Folds:                4
Combos searched/fold: 4  (best-of-4 in-sample → multiple-testing bias)
OOS folds profitable: 0/4 (0%)
Total OOS PnL:        -122.2957
Avg OOS PnL/fold:     -30.5739
OOS Sharpe:           mean +0.0000  std 0.0000
─── Confidence ───
Mean/std (OOS Sharpe SNR): n/a (zero cross-fold dispersion)
Reminder: 4 combos searched/fold — read the best as upward-biased.
════════════════════════════
```

| Fold | Train events | Test events | IS PnL | OOS PnL |
|-----:|-------------:|------------:|-------:|--------:|
| 1 | 288 | 288 | −45.27 | −57.83 |
| 2 | 576 | 288 | −99.61 | −0.05 |
| 3 | 864 | 288 | −99.01 | −20.82 |
| 4 | 1,152 | 288 | −232.44 | −43.60 |

**Result: 0 of 4 out-of-sample folds were profitable; total OOS PnL −122.30.**
This is the most important number on the page. The full-sample OBI backtest above
looked roughly flat and nominally edged buy-and-hold (+0.51% excess, almost
entirely because BTC fell over the window), but under proper walk-forward — where
parameters are chosen on past data and evaluated on unseen future data — the
strategy loses money on every fold. That gap *is* the value of walk-forward: it
exposes the full-sample figure as having no forward-looking edge, exactly as it
should.

---

## Parameter sweep (calibrator)

```bash
go run ./cmd/calibrate --data data/btc_test.jsonl --strategy obi \
  --grid threshold=0.5,0.6,0.7,0.8 --grid order_size=0.01,0.02 --out calib_obi.csv
```

Real CSV output:

```
strategy,order_size,threshold,sharpe,max_drawdown,fills,realized_pnl,hit_rate,turnover,avg_hold_seconds
obi,0.01,0.5,0.0000,0.000000,455,-235.3942,0.4795,41.2859,6152.1
obi,0.01,0.6,0.0000,0.000000,338,-157.7069,0.4331,30.6528,8626.2
obi,0.01,0.7,0.0000,0.000000,235,-59.0439,0.4952,21.3140,12548.6
obi,0.01,0.8,0.0000,0.000000,111,-31.4835,0.5294,10.0184,14840.0
obi,0.02,0.5,0.0000,0.000000,455,-470.7885,0.4795,41.2859,6152.1
obi,0.02,0.6,0.0000,0.000000,338,-315.4137,0.4331,30.6528,8626.2
obi,0.02,0.7,0.0000,0.000000,235,-118.0877,0.4952,21.3140,12548.6
obi,0.02,0.8,0.0000,0.000000,111,-62.9670,0.5294,10.0184,14840.0
```

Readable pattern: PnL is negative across the whole grid and becomes *less*
negative as the threshold rises (higher threshold → fewer trades → less
cost/slippage bleed). On this window the strategy's economics are dominated by
transaction costs, not by signal edge. Sharpe is `0.0000` everywhere for the same
single-day reason described above.

---

## Honest caveats

Read this block before quoting any number above.

* **Simulated execution on public data.** Fills are simulated by Huginn's paper
  executor against captured public feature data. No order ever hit a real
  exchange. Real fills, queue position, partial fills, rejects, and adverse
  selection are not modeled.
* **Costs are modeled, not measured.** The executor charges a flat
  **5 bps transaction cost + 2 bps slippage** per fill (configurable in YAML).
  Real venue fees, funding, and market-impact for non-trivial size would differ.
* **Extremely short window.** ~24 hours, one instrument, 1,440 bars. This is a
  smoke-test-sized sample, not a statistically meaningful evaluation horizon.
* **Few round trips.** 235 fills for the busiest strategy; most strategies
  traded ≤48 times. There is no way to estimate a reliable distribution of
  returns from a handful of round trips.
* **A single short live run cannot yield a meaningful Sharpe.** This directly
  answers the question that prompted this page: a ~2-hour live paper run that
  produced **8 fills** gives you ~8 trade outcomes over a fraction of a single
  day. Sharpe is the mean of *period returns* divided by their standard
  deviation, annualized — with one sub-day window and a single-digit number of
  fills there is no return *series* to take a mean and standard deviation over.
  The denominator is undefined/zero, which is exactly why the engine emits
  `0.0000` rather than a fabricated ratio. A credible Sharpe needs months of
  data and hundreds-to-thousands of independent return periods. The numbers on
  this page demonstrate the *pipeline*; they are not a performance claim.
* **Walk-forward is the only result here with methodological weight, and it is
  negative** (0/4 OOS folds profitable). The near-flat full-sample "+0.51%
  excess" carries no forward-looking edge, per the walk-forward section.

---

## How to reproduce

```bash
# 1. (optional) capture a fresh window from the running stack
cd norse-stack
./scripts/capture-features.sh --duration 1h     # stack must be up

# 2. run the backtester (using the committed 24h fixture here)
cd ../huginn
: > data/backtest_trades.jsonl
go run ./cmd/backtest --data data/btc_test.jsonl --config configs/vpin_breakout.yaml

# 3. parameter sweep
go run ./cmd/calibrate --data data/btc_test.jsonl --strategy obi \
  --grid threshold=0.5,0.6,0.7,0.8 --out calib.csv

# 4. walk-forward (the result that actually matters)
go run ./cmd/walkforward --data data/btc_test.jsonl --folds 4 \
  --thresholds 0.5,0.6,0.7,0.8
```
