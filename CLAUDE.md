# CLAUDE.md

## What Is norse-stack

Meta-repository and entry point for the Norse Stack — a distributed quantitative trading infrastructure that boots as 23 containers (`docker compose config --services | wc -l`) via one-command `make bootstrap`. This repo provides the unified docker-compose, console, monitoring stack, end-to-end smoke test, and architecture documentation. The core engines (muninn, huginn, sleipnir) live in their own sibling repos, but this repo is **not** purely orchestration: the analytics, ML, research, and bridge services under `services/` are application code built directly from here.

## Commands

```bash
# Clone all service repos (safe to re-run)
./scripts/clone-all.sh

# Boot the full stack (builds all service Docker images)
docker compose up -d --build

# Run the end-to-end smoke test
./scripts/smoke.sh

# Tear down
docker compose down -v
```

## Service Repos

The core engines live as sibling directories:

```
parent/
  norse-stack/     ← this repo
  muninn/          ← Java feature engine
  huginn/          ← Go strategy engine (also builds the `research` gateway, cmd/research)
  sleipnir/        ← Go execution gateway
  muninn-py/       ← Python research SDK
```

## In-Repo Services

These services are application code built directly from this repo (`services/<name>/Dockerfile`):

```
norse-stack/services/
  obi-bridge/      ← order-book-imbalance feature bridge (Muninn → Kafka)
  odin/            ← performance / risk analytics (port 8086)
  bragi/           ← trade explainability (port 8087)
  huginn-ai/       ← XGBoost signal-quality model, served; not a live signal by default (port 8092)
  mimir/           ← point-in-time feature store (port 8095)
  forseti/         ← execution TCA + market impact / capacity (port 8096)
  heimdall/        ← market-regime detector (Gaussian HMM) (port 8097)
  news-sentinel/   ← LLM news-sentiment feed (port 8089)
```

## Ports

| Port  | Service            |
|-------|--------------------|
| 8080  | Muninn API         |
| 8083  | Huginn API         |
| 8085  | Sleipnir API       |
| 8086  | Odin Analytics     |
| 8087  | Bragi Explainer    |
| 8088  | Redpanda Console   |
| 8089  | News Sentinel      |
| 8092  | Huginn AI (ML)     |
| 8094  | Research Gateway   |
| 8095  | Mimir Feature Store|
| 8096  | Forseti TCA        |
| 8097  | Heimdall Regime    |
| 9002  | MinIO API          |
| 9003  | MinIO Console      |
| 5437  | PostgreSQL (Muninn)|
| 5436  | PostgreSQL (Huginn)|
| 9091  | Prometheus         |
| 9093  | Alertmanager       |
| 3001  | Grafana            |
| 50051 | Huginn gRPC        |
| 19092 | Redpanda (Kafka)   |

## Analytics Services

### Odin (Performance Monitor)
- `GET /api/analytics` — comprehensive analytics:
  - Rolling Sharpe and Sortino ratios (1h, 6h, 24h windows)
  - Risk: CVaR (95th percentile), Calmar ratio, Kelly criterion
  - Portfolio VaR: variance-covariance method with diversification benefit
  - Drawdown: magnitude, duration, recovery time
  - Monte Carlo permutation test (p-value, percentile, significance)
  - Cross-asset correlation matrix
  - Per-instrument breakdown with win rate, P&L
- `GET /api/equity` — full equity curve time series
- `GET /api/trades` — recent round-trip trades with P&L
- `GET /api/reconciliation` — backtest-vs-live reconciliation: compares live realized PnL / fees / fill count against the committed `docs/RESULTS.md` expectation, models the 5 bps + 2 bps cost on live notional, and flags material divergence (e.g. live net worse than backtest predicted → a fee/fill-model gap). Also surfaced under the `reconciliation` key of `/api/analytics`.

### Bragi (Trade Explainability)
- `GET /api/decisions` — recent decision log with human-readable explanations
- `GET /api/decisions/blocked` — only blocked trades (filter prevented execution)
- `GET /api/decisions/trades` — only executed trades
- `GET /api/decisions/stats` — filter effectiveness breakdown

### Huginn AI (Signal-Quality Model, Served)
Served with versioning, provenance and online retraining; the score is provenance, not a live signal in the shipped config (OBI ML gate off by default, `STRATEGY_OBI_ML_GATE`).
- `GET /api/predict?instrument=BTC-USDT` — XGBoost confidence score for current signal
- `GET /api/model/status` — model state, sample counts, feature importance
- `GET /api/model/metrics` — precision, recall, F1, accuracy

### News Sentinel (LLM News Sentiment)
- `GET /api/sentiment` — per-instrument aggregate sentiment (BTC, ETH, SOL, XRP, DOGE) via Ollama
- `GET /api/headlines` — recent headlines with individual sentiment scores
- `GET /api/status` — service status, feed health, Ollama connectivity

### Research Gateway (validation as a service, `huginn` cmd/research)
- `POST /api/research/runs` — submit a walk-forward + PBO + Deflated-Sharpe validation job
- `GET /api/research/runs` — list runs
- `GET /api/research/runs/{id}` — run status and verdict
- Reuses `huginn/internal/research` (same engine as `cmd/walkforward`), runs out of the live trading process; reproduces the honest result (OBI 0/4 OOS folds, PBO = 1.00, total OOS PnL −146.11)

### Mimir (Point-in-Time Feature Store)
- `GET /api/features?as_of=<t>` — as-of query returning only data known at instant `t` (no lookahead)
- `GET /api/features/history` — record history with event_time + ingest_time
- `GET /api/sources` — registered data sources
- Stamps both event_time and ingest_time; prevents lookahead bias structurally at the data layer

### Forseti (Execution TCA + Market Impact / Capacity)
- `GET /api/tca` — aggregate transaction-cost analysis: slippage, maker/taker, fees, implementation shortfall
- `GET /api/tca/fills` — underlying per-fill records
- `GET /api/impact?instrument=&size=&adv=&sigma=&eta=` — pre-trade market-impact estimate: square-root-law temporary impact (`eta·sigma·sqrt(Q/ADV)`) plus an Almgren-Chriss permanent term, in bps. Missing inputs fall back to documented defaults and the `basis` field states exactly which were defaulted (no silent fabrication).
- `GET /api/impact/schedule?size=&slices=&riskAversion=` — Almgren-Chriss optimal execution schedule (per-slice sizes trading off impact against timing risk)
- `GET /api/capacity?edgeBps=&instrument=` — strategy capacity: the notional at which modelled impact equals an ASSUMED edge. The response labels the edge as illustrative — this simulation has no measured out-of-sample edge (PBO=1.0), so capacity is a "what-if" bound, not a live figure.
- Computed from real fills; reports `null` slippage when there is no arrival price (e.g. paper fills) rather than fabricating a benchmark

### Heimdall (Market-Regime Detection — Gaussian HMM)
- `GET /api/regime` — current market regime via a Gaussian HMM fit with Baum-Welch (EM). Reports the causal forward-filtered state (`currentRegime` id/label/probability), full `stateProbs`, `transitionMatrix`, `stationary` distribution, `logLikelihood`, and observation counts.
- `GET /api/regime/history` — recent regime assignments over the rolling window
- `GET /api/regime/model` — fitted parameters (per-state means/covariances) and the deterministic label derivation
- Consumes `features.obi.v1`, fits a `HEIMDALL_N_STATES`-state HMM (default 3) over a rolling window (default 1000 obs), and refits online. Warm-starts from Mimir's persisted feature history on boot so it is trained immediately rather than waiting for the live feed. Regime **labels** (calm / trending / turbulent / choppy) are derived from the fitted means and covariances — they are descriptive, not predictive, and carry no measured out-of-sample edge (this is a modelling homage to Renaissance/Medallion's HMM lineage, not an edge claim).
