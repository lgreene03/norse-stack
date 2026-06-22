# Getting Started with the Norse Stack

A hands-on guide to running your own quantitative trading pipeline locally. No exchange credentials required.

## Prerequisites

- **Docker Desktop** (or OrbStack/Colima) — needs ~4 GB RAM free
- **Git**
- **Python 3.10+** (for the research SDK)
- ~6 GB disk for image builds

## 1. Clone everything

```bash
git clone https://github.com/lgreene03/norse-stack.git
cd norse-stack
./scripts/clone-all.sh
```

This clones all 5 repos as siblings:

```
~/norse-stack/     # orchestration + docker-compose
~/muninn/          # Java feature engine
~/huginn/          # Go strategy engine
~/sleipnir/        # Go execution gateway
~/muninn-py/       # Python research SDK
```

## 2. Boot the stack

```bash
cd ~/norse-stack
docker compose up -d --build
```

First run takes 3-5 minutes (image builds). Subsequent starts take ~30 seconds.

> **Port conflict?** If port 5437 is in use by another project, edit `docker-compose.yml` and change the postgres-muninn port mapping (e.g. `5438:5432`).

Once running, verify:

```bash
# All 9 containers should be running/healthy
docker compose ps

# Quick health checks
curl -s http://localhost:8080/actuator/health   # Muninn
curl -s http://localhost:8083/healthz            # Huginn
curl -s http://localhost:8085/healthz            # Sleipnir
```

## 3. Send your first trade

```bash
EVENT_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

curl -s -X POST http://localhost:8080/api/v1/events/trade \
  -H "Content-Type: application/json" \
  -d "{
    \"eventId\": \"${EVENT_ID}\",
    \"eventTime\": \"${NOW}\",
    \"ingestTime\": \"${NOW}\",
    \"source\": \"manual\",
    \"instrument\": {
      \"symbol\": \"BTC-USDT\",
      \"baseAsset\": \"BTC\",
      \"quoteAsset\": \"USDT\",
      \"exchange\": {\"id\": \"binance\", \"displayName\": \"Binance Spot\", \"timezone\": \"UTC\"}
    },
    \"sequenceNumber\": 1,
    \"schemaVersion\": 1,
    \"price\": 67500.50,
    \"size\": 0.01,
    \"side\": \"BUY\",
    \"exchangeTradeId\": \"manual-001\"
  }"
```

You should see: `{"eventId":"...","status":"accepted","topic":"events.trade"}`

Muninn ingests this, computes a rolling VWAP feature, and publishes it to Kafka.

## 4. Trigger the strategy pipeline

Huginn watches the `features.obi.v1` Kafka topic for OBI (Order Book Imbalance) features. Inject one to trigger a trade:

```bash
FEATURE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")
NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

echo "{\"eventId\":\"${FEATURE_ID}\",\"eventTime\":\"${NOW}\",\"featureName\":\"obi\",\"featureVersion\":\"v1\",\"instrument\":\"BTC-USDT\",\"windowStart\":\"${NOW}\",\"windowEnd\":\"${NOW}\",\"values\":{\"obi\":0.85}}" \
  | docker exec -i norse-stack-redpanda-1 rpk topic produce features.obi.v1 --key "BTC-USDT"
```

What happens next (in ~2 seconds):
1. **Huginn** consumes the OBI feature (0.85 > threshold 0.7)
2. Huginn fires a **SELL** signal (mean-reversion: high buy pressure suggests price will drop)
3. Huginn publishes an **order intent** to `executions.intents.v1`
4. **Sleipnir** (sim mode) picks up the intent, simulates the fill
5. Sleipnir publishes a **fill** to `executions.fills.v1`
6. **Huginn** receives the fill and updates the portfolio

Check the result:

```bash
curl -s http://localhost:8083/api/snapshot | python3 -m json.tool
```

You should see a fill and updated cash balance.

## 5. Explore the UIs

| URL | What it shows |
|-----|---------------|
| http://localhost:8080/swagger-ui/index.html | Muninn API docs — try endpoints interactively |
| http://localhost:8088 | Redpanda Console — browse Kafka topics and messages |
| http://localhost:9003 | MinIO Console — Parquet files (login: minioadmin/minioadmin) |

## 6. Use the Python SDK

```bash
pip install muninn-py
```

```python
from muninn import MuninnClient

with MuninnClient(host="http://localhost:8080") as client:
    # List registered features
    features = client.list_features()
    print(features)

    # Pull VWAP time-series (needs trades over multiple 1-min windows)
    df = client.get_feature(
        "vwap.1m",
        instrument="BTC-USDT",
        start="2026-06-19T00:00:00Z",
        end="2026-06-19T23:59:59Z"
    )
    print(df)
```

CLI:
```bash
muninn features list
muninn features get vwap.1m --instrument BTC-USDT \
  --start 2026-06-19T00:00:00Z --end 2026-06-19T23:59:59Z
```

Live streaming:
```bash
muninn stream listen --feature vwap.1m
```

## 7. Run the smoke test

The bundled smoke test validates the entire pipeline end-to-end:

```bash
cd ~/norse-stack
./scripts/smoke.sh
```

Or from the muninn repo (more comprehensive):

```bash
cd ~/muninn
bash scripts/smoke-stack.sh --teardown
```

## 8. Switch strategies

Huginn ships with 4 strategies. Change by restarting with a different env var:

| Strategy | `STRATEGY_NAME` | What it does |
|----------|-----------------|--------------|
| **OBI Threshold** | `obi` | Mean-reversion on order book imbalance |
| **VPIN Breakout** | `vpin` | Volume-synchronized probability of informed trading |
| **EMA Crossover** | `ema_crossover` | Classic dual moving average crossover |
| **VWAP Deviation** | `vwap_deviation` | Trade when price deviates from volume-weighted average |

```bash
# Stop Huginn, restart with VPIN strategy
docker compose stop huginn
STRATEGY_NAME=vpin STRATEGY_THRESHOLD=0.6 docker compose up -d huginn
```

## 9. Run a backtest

Huginn includes an offline backtester that replays a JSONL feature file:

```bash
cd ~/huginn
go run ./cmd/backtest --data data/features.jsonl --report report.html
```

Open `report.html` for Sharpe ratio, max drawdown, equity curve, and trade table.

## 10. Calibrate parameters

Grid-search over strategy thresholds to find optimal settings:

```bash
cd ~/huginn
go run ./cmd/calibrate --data data/features.jsonl --output calibration/
```

## 11. Shut down

```bash
cd ~/norse-stack
docker compose down       # stop containers, keep data
docker compose down -v    # stop containers AND delete all data volumes
```

---

## What each service does

```
Exchange data → MUNINN (compute features) → HUGINN (run strategy) → SLEIPNIR (execute orders)
                  ↑                              ↑                         ↓
              MUNINN-PY                    React dashboard            Exchange API
            (research SDK)               (equity, fills, halt)      (Binance testnet)
```

- **Muninn** — ingests raw trades, computes streaming features (VWAP, OBI), stores events in Parquet on MinIO, guarantees byte-identical replay
- **Huginn** — consumes features, runs strategy logic, manages risk limits (drawdown, daily loss, position caps), tracks portfolio P&L
- **Sleipnir** — rate-limits and risk-checks order intents, submits to exchange, reports fills back; `sim` mode for testing without credentials
- **muninn-py** — Python SDK for pulling features into Polars/pandas DataFrames for notebook research

## Going live (when you're ready)

Two config changes move from simulation to real trading:

1. **Enable real market data** — in Muninn, set `MUNINN_INGESTION_BINANCE_ENABLED=true` with your Binance API credentials
2. **Switch Sleipnir to real execution** — set `EXCHANGE_BACKEND=binance`, provide `BINANCE_API_KEY` and `BINANCE_API_SECRET`

**Start with Binance testnet** — use testnet credentials first to validate the full pipeline with real exchange mechanics but fake money.

Copy `~/sleipnir/configs/risk.example.yaml` to `risk.yaml` and set `RISK_CONFIG_PATH` to enforce per-instrument size caps, notional limits, and minimum order sizes.
