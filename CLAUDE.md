# CLAUDE.md

## What Is norse-stack

Meta-repository and entry point for the Norse Stack — a four-service distributed quantitative trading infrastructure. This repo contains the unified docker-compose, end-to-end smoke test, and architecture documentation. It does not contain application code; each service lives in its own repo.

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

All repos are expected as sibling directories:

```
parent/
  norse-stack/     ← this repo
  muninn/          ← Java feature engine
  huginn/          ← Go strategy engine
  sleipnir/        ← Go execution gateway
  muninn-py/       ← Python research SDK
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

### Huginn AI (ML Signal Predictor)
- `GET /api/predict?instrument=BTC-USDT` — XGBoost confidence score for current signal
- `GET /api/model/status` — model state, sample counts, feature importance
- `GET /api/model/metrics` — precision, recall, F1, accuracy

### News Sentinel (LLM News Sentiment)
- `GET /api/sentiment` — per-instrument aggregate sentiment (BTC, ETH, SOL, XRP, DOGE) via Ollama
- `GET /api/headlines` — recent headlines with individual sentiment scores
- `GET /api/status` — service status, feed health, Ollama connectivity
