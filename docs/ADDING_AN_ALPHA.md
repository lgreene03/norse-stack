# Adding an Alpha

This is the concrete recipe for shipping a new signal through the Norse Stack
pipeline, from raw data to a validated (or rejected) alpha. It is the companion
to [`PLATFORM.md`](PLATFORM.md), which explains *where* each extension point is;
this page is the *how*.

The guiding principle: **a new alpha is additive and self-contained.** You add one
file, wire it in at boot, and run it through the same validation gate every other
signal faces. You do not modify existing strategies (OBIThreshold, OU) or their
tests.

## 0. Decide whether the data is already there

The live feature event `features.obi.v1` already carries a multi-asset universe
(BTC/ETH/SOL/XRP/DOGE) and a rich `Values` map:

```
obi, midPrice, microPrice, spread,
momentum, momentum1m, momentum15m, emaFast, emaSlow, volatility,
funding, openInterest, fearGreed, mlScore, newsSentiment
```

- **If your signal can be computed from existing `Values` fields** → skip to step 2.
- **If you need a new input** → add a source adapter in Muninn / the OBI bridge
  and emit a new key into the `Values` map (step 1). Existing alphas ignore
  unknown keys, so this is non-breaking.

## 1. (Optional) Add a data source

Add the field in the feature producer ([`services/obi-bridge`](../services/obi-bridge);
Muninn's [adapter framework](https://github.com/lgreene03/muninn/blob/main/docs/adr/0008-multi-exchange-adapter-framework.md)).
Because the contract is a map, downstream strategies that do not read the new key
are unaffected — no schema migration, no recompile of unrelated code.

## 2. Research the signal first (muninn-py)

Before writing any Go, measure whether the signal has predictive content. Pull
features into a DataFrame and compute the information coefficient against forward
returns:

```python
from muninn import MuninnClient
from muninn.notebook import information_coefficient, forward_returns  # pure Polars fns

m = MuninnClient("http://localhost:8080")
df = m.get_features(instrument="BTC-USDT", start=..., end=...)
df = forward_returns(df, horizon=1, price_col="midPrice")
ic = information_coefficient(df, signals=["my_signal"], return_col="fwd_return_1")
```

- **Code:** these are pure Polars-in/Polars-out functions in muninn-py
  (`information_coefficient`, `forward_returns`, `rolling_corr`, `hit_rate`), shown
  end-to-end in
  [`muninn-py/notebooks/alpha_backtest_demo.ipynb`](https://github.com/lgreene03/muninn-py/blob/main/notebooks/alpha_backtest_demo.ipynb)
  and the bundled Streamlit dashboard.
- **Decision:** a near-zero or unstable IC is the cheapest possible "kill" — you
  stop here and never write the strategy. This is the platform doing its job
  before you spend engineering time.

## 3. Implement the `Strategy`

Add a new self-contained file in `huginn/internal/strategy/`. Implement the
one-method interface from
[`strategy.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/strategy.go):

```go
type Strategy interface {
    Name() string
    OnFeature(event model.FeatureEvent) []model.Order
}
```

Rules (enforced by the existing property tests):

- **Never read wall-clock time** — decide off `event.EventTime` so backtests stay
  deterministic and replay-faithful.
- **Return `nil` when no signal fires**, never block.
- **If you hold mutable state, embed a `sync.Mutex`** — `OnFeature` runs alongside
  the state-persister and config-PUT goroutines. The canonical shape is
  [`ema_crossover.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/ema_crossover.go);
  for a fitted-model example with exits, see
  [`ou_reversion.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/ou_reversion.go).

Read your new `Values` field defensively (treat a missing key as "no data, no
trade").

## 4. Size into the signed-position portfolio

Emit signed orders (positive = long, negative = short). Sizing helpers live in
[`sizing.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/sizing.go);
the signed-position book and risk caps are in `huginn/internal/portfolio` and
`huginn/internal/risk`. This is where factor exposure / risk-aware sizing is
applied before execution.

## 5. Gate every trade on net-of-cost — reuse, do not reinvent

Wrap or compose with the existing `CostHurdle` gate
([`cost_hurdle.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/cost_hurdle.go))
so net-negative marginal trades are rejected before they fill. The executor already
models slippage and maker/taker fees — you do not add cost logic to your strategy.

## 6. Wire it in at boot

Register the constructor in `huginn/cmd/huginn/main.go` (alongside `NewOBIThreshold`,
`NewVPINBreakout`, `NewVWAPDeviation`, `NewEMACrossover`) and add its name to the
`STRATEGY_NAME` switch. Add a YAML profile under `huginn/configs/`.

## 7. Add tests

Mirror the existing pattern: a `_test.go` next to your file (unit + table tests),
and make sure the shared `property_test.go` invariants still hold. Run:

```bash
cd huginn
go test ./...
go test -race ./internal/strategy/
```

## 8. Run the validation gate — the non-negotiable step

Put the signal through anchored walk-forward + PBO. Parameters are grid-searched on
each train window and only the in-sample winner is applied to the unseen test
window:

```bash
cd huginn
go run ./cmd/walkforward --data data/btc_test.jsonl --config configs/my_alpha.yaml \
  --folds 4 --thresholds <your-grid>
```

Read the summary the way [`EDGE_VERDICT.md`](EDGE_VERDICT.md) does:

- **OOS folds profitable** — how many of the unseen windows made money.
- **PBO** — Probability of Backtest Overfitting (Bailey & López de Prado, CSCV). A
  PBO near 1.0 means your "best" in-sample config is noise.
- **Deflated Sharpe** — Sharpe adjusted for the number of configurations you tried.

If it fails (as OBI and OU both did: 0/4 OOS folds, PBO = 1.00), **record the
negative result and move on.** A dead signal that you proved is dead is a finished,
honest piece of research — not a failure of the platform.

## Checklist

- [ ] IC measured in muninn-py before any Go was written
- [ ] New file in `internal/strategy/`, no edits to existing strategies
- [ ] No wall-clock reads; mutex if stateful; returns `nil` on no-signal
- [ ] Signed orders; sized via `sizing.go`; risk caps respected
- [ ] Net-of-cost gate reused, not reimplemented
- [ ] Registered in `cmd/huginn/main.go` + YAML profile + `STRATEGY_NAME`
- [ ] Tests added; `go test ./...` and `-race` green; property tests intact
- [ ] Walk-forward + PBO run; result (pass *or* fail) recorded honestly
