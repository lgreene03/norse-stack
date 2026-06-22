# Norse Stack as a Quant Alpha Platform

This page reframes Norse Stack for an engineering reviewer: not as a trading bot,
but as an **extensible quant-alpha research-and-execution platform** — the rails a
quant research team actually works on. The point is to show *where you plug in*
when you have a new data source, a new signal, a new portfolio construction rule,
or a new risk model — and that the platform tells you, honestly and automatically,
when a signal is dead.

> The headline result on this dataset is that the bundled signals have **no
> out-of-sample edge** ([`EDGE_VERDICT.md`](EDGE_VERDICT.md)). That is the platform
> *working*: the validation gate killed an overfit signal before any capital was at
> risk. A platform that can prove a negative is more valuable than a backtest that
> only ever prints green.

## The pipeline a quant team extends

```mermaid
flowchart LR
    subgraph DATA[1. Data adapter]
        A1[Binance L2 / funding / OI]
        A2[News / Fear&Greed / ML score]
    end
    subgraph FEAT[2. Feature event]
        F1[features.obi.v1<br/>multi-asset Values map]
    end
    subgraph ALPHA[3. Alpha registry]
        S1[OBIThreshold]
        S2[OU mean-reversion]
        S3[your new alpha]
    end
    subgraph COMBINE[4. Signal combination]
        C1[per-alpha signed signals]
    end
    subgraph PORT[5. Portfolio construction]
        P1[signed positions<br/>factor-aware sizing]
    end
    subgraph EXEC[6. Cost-aware execution]
        E1[CostHurdle gate]
        E2[slippage + fees, maker/taker]
    end
    subgraph GATE[7. Validation gate]
        V1[walk-forward + PBO]
        V2[Deflated Sharpe]
    end
    subgraph RESEARCH[IC research loop]
        R1[muninn-py: IC / forward returns]
    end

    A1 --> F1
    A2 --> F1
    F1 --> S1 & S2 & S3
    S1 & S2 & S3 --> C1
    C1 --> P1
    P1 --> E1 --> E2
    E2 --> V1 --> V2
    F1 -.pull features.-> R1
    R1 -.IC says signal is dead → drop it.-> ALPHA
    V1 -.0/4 OOS folds → reject.-> ALPHA
```

## Stage-by-stage: where the extension points are

### 1. Data adapter — *add a new data source here*

A source adapter turns an external feed into a deterministic feature event. The
live feature event `features.obi.v1` already carries a **multi-asset universe**
(BTC/ETH/SOL/XRP/DOGE) in a rich `Values` map: `obi`, `midPrice`, `microPrice`,
`spread`, `momentum`/`momentum1m`/`momentum15m`, `emaFast`/`emaSlow`,
`volatility`, plus `funding`, `openInterest`, `fearGreed`, `mlScore`,
`newsSentiment`.

- **Extension point:** add a field to the `Values` map (a new source adapter in
  Muninn / the OBI bridge). Existing alphas ignore unknown keys, so this is
  additive.
- **Code:** [`services/obi-bridge`](../services/obi-bridge), Muninn's
  [ADR-0008 multi-exchange adapter framework](https://github.com/lgreene03/muninn/blob/main/docs/adr/0008-multi-exchange-adapter-framework.md).
- **Why it matters:** a new alpha consuming a new `Values` field is the whole
  data-extensibility story — no schema migration, no recompile of unrelated
  strategies.

### 2. Feature event — the deterministic contract

Everything downstream is a pure function of one `model.FeatureEvent`. Strategies
read `EventTime`, never wall-clock time, so a backtest and a live run share one
computation path (deterministic replay parity).

- **Code:** the event/contract is documented in
  [`docs/CONTRACTS.md`](CONTRACTS.md); replay determinism is enforced by
  [`huginn/internal/backtest/parity_test.go`](https://github.com/lgreene03/huginn/blob/main/internal/backtest/parity_test.go).

### 3. Alpha registry — *add a new signal here* (the primary extension point)

Every signal is a `Strategy` implementation: one method,
`OnFeature(model.FeatureEvent) []model.Order`. New alphas are self-contained files
in `huginn/internal/strategy/` and are wired in at boot — adding one does not touch
the existing OBIThreshold or OU strategies.

- **Interface:** [`huginn/internal/strategy/strategy.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/strategy.go).
- **Worked examples:** [`obi_threshold.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/obi_threshold.go)
  (microstructure heuristic) and [`ou_reversion.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/ou_reversion.go)
  (a fitted price-process model with a half-life exit and trend-guard).
- **How-to:** [`ADDING_AN_ALPHA.md`](ADDING_AN_ALPHA.md) — the step-by-step recipe
  for shipping a new signal through the whole pipeline.

### 4. Signal combination — *change how signals blend here*

Each alpha emits a signed signal. Combination today is one-alpha-per-deployment;
the extension point for multi-signal blending (rank/weight/orthogonalize before
sizing) is the boundary between the strategy output and portfolio construction.

- **Code:** position sizing logic in
  [`huginn/internal/strategy/sizing.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/sizing.go).

### 5. Factor-aware portfolio construction — *add a risk model / sizing rule here*

Orders feed a **signed-position portfolio** (long *and* short, not long-only) with
realized/unrealized PnL tracking. This is where factor exposures and risk-aware
sizing are applied before anything reaches the executor.

- **Code:** `huginn/internal/portfolio` (signed positions) and
  `huginn/internal/risk` (drawdown, daily-loss, position/notional caps,
  staleness watchdog).
- **Research side:** factor/IC analysis lives in **muninn-py** —
  `information_coefficient`, `forward_returns`, `rolling_corr`, `hit_rate` (pure
  Polars-in/Polars-out functions), demonstrated end-to-end in
  [`muninn-py/notebooks/alpha_backtest_demo.ipynb`](https://github.com/lgreene03/muninn-py/blob/main/notebooks/alpha_backtest_demo.ipynb)
  and the bundled Streamlit dashboard. This is the IC research loop that tells you
  a signal is worth carrying *before* you wire it into the engine.

### 6. Cost-aware execution — already built, reuse it

The `CostHurdle` gate rejects net-negative marginal trades before they fill; the
executor models slippage and maker/taker fees on a separate service boundary
(Sleipnir runs a sim exchange by default).

- **Code:** [`huginn/internal/strategy/cost_hurdle.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/cost_hurdle.go),
  `huginn/internal/executor`,
  [ADR-0002 sim-only execution boundary](adr/0002-sim-only-execution-boundary.md).
- **Honest framing:** the gate is *damage control*, not alpha — it cannot
  manufacture edge a signal does not have. See `EDGE_VERDICT.md`.

### 7. Validation gate — *the part that keeps you honest*

Before any signal is trusted it goes through **anchored walk-forward** (expanding
train, sliding test) with a **Probability of Backtest Overfitting (PBO)** score and
a **Deflated Sharpe** check. Parameters are chosen on the train window only and
applied to unseen test windows.

- **Code:** [`huginn/cmd/walkforward`](https://github.com/lgreene03/huginn/blob/main/cmd/walkforward/main.go),
  [ADR-0007 walk-forward calibration](https://github.com/lgreene03/huginn/blob/main/docs/adr/0007-walk-forward-calibration-workflow.md).
- **What it found:** OBI scored **0/4 profitable OOS folds, PBO = 1.00**; OU
  scored the same with a smaller loss. Both were rejected. That rejection is the
  platform delivering its core value: it tells you a signal is dead so you stop
  tuning it and move on to better data or a different signal.

## The loop, in one sentence

Pull features → measure IC in muninn-py → if promising, implement a `Strategy` →
size it into the signed-position portfolio → gate every trade on net-of-cost →
**prove or disprove edge with walk-forward + PBO** → keep or kill. Adding a new
data source, signal, or risk model touches one self-contained extension point; the
validation gate is non-negotiable and applies to all of them equally.

## See also

- [`ADDING_AN_ALPHA.md`](ADDING_AN_ALPHA.md) — the concrete recipe.
- [`EDGE_VERDICT.md`](EDGE_VERDICT.md) — the honest no-edge result and why it is a
  feature.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the 15-container service topology.
- [`RESULTS.md`](RESULTS.md) — full unedited backtester/walk-forward output.
