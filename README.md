# Norse Stack

**A distributed quantitative trading infrastructure built from first principles.**

[![Muninn CI](https://github.com/lgreene03/muninn/actions/workflows/ci.yml/badge.svg)](https://github.com/lgreene03/muninn/actions/workflows/ci.yml)
[![Huginn CI](https://github.com/lgreene03/huginn/actions/workflows/ci.yml/badge.svg)](https://github.com/lgreene03/huginn/actions/workflows/ci.yml)
[![Sleipnir CI](https://github.com/lgreene03/sleipnir/actions/workflows/ci.yml/badge.svg)](https://github.com/lgreene03/sleipnir/actions/workflows/ci.yml)
[![muninn-py CI](https://github.com/lgreene03/muninn-py/actions/workflows/ci.yml/badge.svg)](https://github.com/lgreene03/muninn-py/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Norse Stack is a 15-container distributed crypto trading system that ingests live Binance market data, computes 10 independent signal layers, executes quantitative strategies with regime-aware threshold adaptation, routes orders through TWAP/VWAP execution algorithms, and monitors everything through Prometheus and Grafana. Named after figures from Norse mythology.

> **Backtest numbers, with the honesty attached:** see **[docs/RESULTS.md](docs/RESULTS.md)** for actual backtester output (per-strategy Sharpe/MDD/hit-rate, buy-and-hold benchmark, walk-forward) plus an explicit caveat block. Short answer up front: a single short live run with a handful of fills *cannot* produce a meaningful Sharpe, and the page explains exactly why.

### Engineering case studies

Two short, specific write-ups of senior-level findings on this stack — real numbers, real file references:

- **[Fee dominance: a real gross edge that fees eat alive](docs/case-studies/fee-dominance.md)** — the OBI strategy wins ~70% of round trips with a 5.85 profit factor yet loses money net, because it trades ~21x turnover and the cost bleed exceeds the edge. How it was caught (Odin `net_trading_pnl` vs realized, and the calibrate grid showing PnL get *less* negative as the threshold rises) and the fix direction (net-of-cost gate, maker execution).
- **[The equity-accounting bug hunt](docs/case-studies/equity-accounting-bug.md)** — two contradictory figures (−10.8% final value vs +4.48% return) traced to two real bugs in `Portfolio.Snapshot` and `StrategyTotalReturn`, a unit test that had encoded the wrong value, and the regression tests added.

### Why this isn't a toy / what's novel

- **Deterministic replay parity.** Muninn's feature engine produces byte-identical output from the same input events, so a backtest and a live run share one computation path — enforced by [`huginn/internal/backtest/parity_test.go`](https://github.com/lgreene03/huginn/blob/main/internal/backtest/parity_test.go) and Muninn's [ADR-0002 event-id determinism](https://github.com/lgreene03/muninn/blob/main/docs/adr/0002-event-id-determinism.md).
- **Live-orderbook microstructure signals.** Signals are computed from real Binance L2 order books (order-book imbalance, VPIN, micro-price, VWAP) rather than from OHLC candles — see [`services/obi-bridge`](services/obi-bridge) and Muninn's [ADR-0008 multi-exchange adapter framework](https://github.com/lgreene03/muninn/blob/main/docs/adr/0008-multi-exchange-adapter-framework.md).
- **Walk-forward validation, not in-sample curve-fitting.** Anchored expanding-train / sliding-test validation with explicit multiple-testing warnings — [`huginn/cmd/walkforward`](https://github.com/lgreene03/huginn/blob/main/cmd/walkforward/main.go), [ADR-0007 walk-forward calibration](https://github.com/lgreene03/huginn/blob/main/docs/adr/0007-walk-forward-calibration-workflow.md), and the negative result is published in [docs/RESULTS.md](docs/RESULTS.md).
- **Honest sim-execution boundary.** Strategy and execution are separate services; Sleipnir runs a sim exchange by default and the boundary is documented as a deliberate decision in [ADR-0002 (stack)](docs/adr/0002-sim-only-execution-boundary.md) and [Huginn ADR-0003 dual-mode executor](https://github.com/lgreene03/huginn/blob/main/docs/adr/0003-dual-mode-paper-live-executor.md).

---

## Architecture

```
                        ┌──────────────────────────────────────────────────────────────┐
  Binance               │                        Norse Stack                           │
  (WebSocket) ──────►   │                                                              │
                        │  ┌──────────┐  features  ┌──────────┐  intents  ┌──────────┐ │
                        │  │ MUNINN   │───────────►│  HUGINN  │─────────►│ SLEIPNIR │ │
                        │  │ Feature  │  (Kafka)   │ Strategy │  (Kafka) │ Execution│ │
                        │  │ Engine   │            │ Engine   │◄─────────│ Gateway  │ │
                        │  └──────────┘            └────┬─────┘  fills   └──────────┘ │
                        │       │                       │                      │       │
                        │       │              ┌────────┼────────┐             │       │
                        │       ▼              ▼        ▼        ▼             │       │
                        │  ┌──────────┐  ┌─────────┐ ┌──────┐ ┌─────────┐    │       │
                        │  │OBI-BRIDGE│  │  ODIN   │ │BRAGI │ │HUGINN-AI│    │       │
                        │  │ 10-Layer │  │Analytics│ │Explain│ │  ML/    │    │       │
                        │  │ Signals  │  │ Monitor │ │Engine │ │ XGBoost │    │       │
                        │  └──────────┘  └─────────┘ └──────┘ └─────────┘    │       │
                        │       │                                             │       │
                        │       ▼              ┌──────────────────┐           │       │
                        │  ┌──────────┐        │   OBSERVABILITY  │           │       │
                        │  │  NEWS    │        │  ┌────────────┐  │           │       │
                        │  │ SENTINEL │        │  │ Prometheus │  │           │       │
                        │  │ LLM/NLP  │        │  │  + Grafana │  │           │       │
                        │  └──────────┘        │  └────────────┘  │           │       │
                        │                      └──────────────────┘           │       │
                        └──────────────────────────────────────────────────────────────┘
```

### Services

| Service | Language | Role |
|---------|----------|------|
| **[Muninn](https://github.com/lgreene03/muninn)** | Java 21 / Spring Boot | Market data ingestion, deterministic feature computation, S3/Postgres storage |
| **[Huginn](https://github.com/lgreene03/huginn)** | Go 1.25 | Strategy execution with 4 pluggable strategies, risk controls, portfolio management, gRPC API |
| **[Sleipnir](https://github.com/lgreene03/sleipnir)** | Go 1.25 | Order execution gateway with TWAP/VWAP algorithms, rate limiting, pre-trade risk |
| **Obi-Bridge** | Python | Real-time 10-layer signal computation from live Binance order books |
| **Odin** | Python | Performance analytics: Sharpe, Sortino, CVaR, VaR, Monte Carlo, correlation matrix |
| **Bragi** | Python | Trade explainability engine with human-readable decision logs |
| **Huginn-AI** | Python | XGBoost ML signal predictor with online retraining |
| **News Sentinel** | Python | LLM-powered crypto news sentiment via Ollama |
| **[muninn-py](https://github.com/lgreene03/muninn-py)** | Python | Research SDK: Polars DataFrames, IC analysis, Streamlit dashboard |

### Signal Layers (Obi-Bridge)

The bridge computes 10 independent signal layers from live Binance data, each capturing a different market dimension:

| # | Signal | Description |
|---|--------|-------------|
| 1 | Order Book Imbalance | Bid/ask volume ratio near the spread |
| 2 | Multi-TF Momentum | Price change across 1m, 5m, 15m windows |
| 3 | Volatility Regime | Bollinger width + ATR classification |
| 4 | Volume Ratio | Current vs. rolling average volume |
| 5 | Fear & Greed Index | Market-wide sentiment indicator |
| 6 | Funding Rate | Perpetual futures positioning pressure |
| 7 | Open Interest Cascade | Futures contract count delta |
| 8 | ML Confidence | XGBoost prediction probability |
| 9 | News Sentiment | Ollama LLM headline analysis |
| 10 | Regime Detection | Hurst exponent + autocorrelation classification |

### Data Flow

```
Exchange → Muninn (trades → features via Redpanda)
       → features.obi.v1
              ↓
  Obi-Bridge (10-layer signal computation)
       → features.obi.v1 (enriched)
              ↓
         Huginn (feature → regime-aware strategy → order intent)
       → executions.intents.v1
              ↓
         Sleipnir (intent → risk → rate limit → TWAP/VWAP → exchange)
       → executions.fills.v1
              ↓
         Huginn (fill → portfolio update → PnL tracking)
              ↓
    Odin (analytics) + Bragi (explainability) + Prometheus/Grafana (metrics)
```

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Git

### Clone and boot

> **Important — this repo builds the engine services (Muninn, Huginn, Sleipnir)
> from sibling checkouts.** A bare `git clone` of `norse-stack` alone does **not**
> contain `../muninn`, `../huginn`, or `../sleipnir`, so a plain
> `docker compose up -d` will fail with a *build context not found* error until
> those siblings exist. Pick one of the two paths below.

**Path A — one-command bootstrap (recommended).** Clones the sibling repos, then
builds and boots everything:

```bash
git clone https://github.com/lgreene03/norse-stack.git
cd norse-stack
make bootstrap        # = scripts/clone-all.sh  +  docker compose up -d --build
```

Equivalent without `make`:

```bash
./scripts/clone-all.sh        # clone ../muninn ../huginn ../sleipnir ../muninn-py
docker compose up -d --build  # build from those siblings and boot
```

**Path B — prebuilt images, no sibling checkouts.** Huginn and Sleipnir publish
images to GHCR via their `release.yml` workflows. Once images are published you
can skip cloning the engine repos entirely:

```bash
make up-ghcr
# = docker compose -f docker-compose.yml -f docker-compose.ghcr.yml up -d
```

The [`docker-compose.ghcr.yml`](docker-compose.ghcr.yml) override swaps the
engine services' `build:` for `image: ghcr.io/lgreene03/<svc>:${TAG:-latest}`.
**Failure mode:** if no image tag has been published yet, the pull fails — fall
back to Path A (build from source). Pin a tag with
`HUGINN_TAG=… SLEIPNIR_TAG=… MUNINN_TAG=…`.

Either path starts all 15 containers. No exchange credentials needed — Sleipnir runs in simulated mode, Obi-Bridge connects to Binance's public (unauthenticated) WebSocket streams.

### Run the end-to-end smoke test

```bash
./scripts/smoke.sh
```

### Capture data for walk-forward backtesting

```bash
./scripts/capture-features.sh              # run for a few hours, Ctrl+C to stop
cd ../huginn
go run ./cmd/walkforward --data ../norse-stack/data/features-*.jsonl
```

---

## Endpoints

| Port | Service | URL |
|------|---------|-----|
| 8080 | Muninn API | http://localhost:8080 |
| 8083 | Huginn API | http://localhost:8083/api/snapshot |
| 8084 | Huginn Dashboard | http://localhost:8084 |
| 8085 | Sleipnir API | http://localhost:8085/healthz |
| 8086 | Odin Analytics | http://localhost:8086/api/analytics |
| 8087 | Bragi Explainer | http://localhost:8087/api/decisions |
| 8088 | Redpanda Console | http://localhost:8088 |
| 8089 | News Sentinel | http://localhost:8089/api/sentiment |
| 8092 | Huginn-AI ML | http://localhost:8092/api/model/status |
| 9091 | Prometheus | http://localhost:9091 |
| 9093 | Alertmanager | http://localhost:9093 |
| 3001 | Grafana | http://localhost:3001 (admin/norse) |
| 50051 | Huginn gRPC | `grpcurl -plaintext localhost:50051 huginn.HuginnService/GetSnapshot` |

---

## Key Features

### Strategy Engine (Huginn)
- 4 pluggable strategies: OBI Threshold, VPIN Breakout, EMA Crossover, VWAP Deviation
- Regime-aware threshold adaptation using Hurst exponent and autocorrelation
- Sub-second exit monitoring via real-time price tick consumer
- Signal-to-decision latency: p50 ~3ms (Prometheus histogram)
- gRPC API with dynamic proto descriptors (no protoc, full reflection support)
- Walk-forward backtester with anchored expanding window validation

### Execution Gateway (Sleipnir)
- TWAP: equal-sized slices at uniform time intervals
- VWAP: volume-weighted slices using configurable intraday U-shaped profile
- Token-bucket rate limiting, per-instrument size caps, daily order limits
- Boot-time order reconciliation against exchange state
- Sim mode for credential-free testing

### Analytics (Odin)
- Rolling Sharpe and Sortino ratios (1h, 6h, 24h windows)
- CVaR at 95th percentile, Calmar ratio, half-Kelly criterion
- Portfolio VaR: variance-covariance method with diversification benefit
- Monte Carlo permutation test for strategy significance
- Cross-asset correlation matrix
- Per-instrument P&L breakdown with win rate and profit factor

### Observability
- Prometheus scraping Huginn, Sleipnir, Odin, Huginn-AI, and Muninn metrics (15s interval)
- Alert rules (`monitoring/alerts/`) for pipeline stalls (no features/fills), fill rejection & duplicate bursts, model/state persistence failures, and target-down, routed through Alertmanager
- 11-panel auto-provisioned Grafana dashboard: signal-to-decision latency (p50/p95/p99), feature event age, orders by side, risk halt status, max drawdown gauge, portfolio value, realized PnL, and more
- Distributed tracing via OpenTelemetry (Tempo-compatible)

### Python service resilience
- **Per-message decode isolation:** odin/bragi/huginn-ai decode each Kafka record inside its own try/except; a poison (undecodable) record increments a `*_decode_failure_total` counter and is republished to a `<topic>.dlq` topic (best-effort) instead of stalling or dropping the rest of the batch.
- **Consumer-thread liveness:** each consumer publishes a per-cycle heartbeat. `/healthz` (odin, bragi, huginn-ai) and `/readyz` (news-sentinel) return `503` once the heartbeat is stale beyond `HEALTH_MAX_STALENESS_SECS`, so a wedged consumer is detectable even while the HTTP server is up. Health reports OK until the loop has started so container startup isn't failed closed during Kafka connect/retry.
- **Bragi replay-safety:** Bragi consumes from `earliest` (configurable via `AUTO_OFFSET_RESET`) and dedups on feature `eventId` / fill `execution_id` with a bounded seen-set, so a restart rebuilds the decision log without losing or double-counting events.
- **News Sentinel honest Ollama state:** sentiment is only counted when Ollama returns a genuinely parsed classification. Failed/unparsed responses increment `ollama_failures`, tag the headline `unclassified`, and exclude it from aggregate sentiment (no falsely-neutral signal). `/api/status` exposes `ollama_status` (`ok`/`degraded`).
- **obi-bridge batched flush:** the producer flushes once per poll cycle (all symbols together) rather than synchronously per message.
- **Configurable CORS:** the read-only HTTP APIs default to `Access-Control-Allow-Origin: *` but can be locked to a single origin via `ACCESS_CONTROL_ALLOW_ORIGIN` (see `.env.example`).

---

## Key Design Decisions

- **Deterministic replay.** Muninn's feature engine produces byte-identical outputs from the same input events, enforced by ArchUnit rules and CI integration tests.
- **One computation path.** Live and replay use the same code — no separate batch pipeline.
- **Kafka-native boundaries.** Services communicate exclusively through Redpanda topics. No shared databases, no direct RPC (except Huginn's optional gRPC for programmatic queries).
- **Local-first.** The full 15-container stack runs on a single machine. No cloud services required.
- **Sim mode everywhere.** Sleipnir's sim exchange and Muninn's synthetic trade ingestion mean the entire pipeline is testable without exchange credentials.

---

## Project Stats

| Metric | Value |
|--------|-------|
| Languages | Java, Go, Python, TypeScript |
| Docker containers | 15 |
| Signal layers | 10 |
| Kafka topics | 3 (features, intents, fills) + prices |
| Architecture Decision Records | 28 across the stack (Muninn 14, Huginn 7, Sleipnir 7) + stack-level ADRs in [`docs/adr/`](docs/adr/) |

> Test and line-count totals are intentionally not asserted here — they drift the
> moment code changes and an unsourced figure is worse than none. The real,
> current test counts are whatever the per-repo CI runs report; click the CI
> badges at the top of this README for the authoritative numbers.

---

## Technology

| Layer | Technology |
|-------|-----------|
| Feature Engine | Java 21, Spring Boot 3.5, DuckDB, Parquet, Iceberg |
| Strategy Engine | Go 1.25, Kafka (segmentio), Prometheus, gRPC |
| Execution Gateway | Go 1.25, Binance REST/WS, SQLite, TWAP/VWAP algos |
| Signal Bridge | Python 3.10+, Binance WebSocket, regime detection |
| Analytics | Python 3.10+, Monte Carlo, variance-covariance VaR |
| ML Pipeline | Python, XGBoost, online retraining |
| Research SDK | Python 3.10+, Polars, pandas, Pydantic v2, httpx |
| Message Broker | Redpanda (Kafka-compatible) |
| Storage | PostgreSQL 16, MinIO (S3-compatible), Parquet/Iceberg |
| Observability | Prometheus, Grafana, OpenTelemetry/Tempo |
| Infrastructure | Docker Compose |

---

## Documentation

- **[Backtest Results](docs/RESULTS.md)** — real backtester/calibrator/walk-forward output with an honest caveat block
- **[Demo Assets](docs/demo/)** — asciinema cast of the smoke test + Grafana dashboard snapshots (via `scripts/record-demo.sh`)
- **[Quant Curriculum](docs/QUANT_CURRICULUM.md)** — 10-chapter ground-up guide to quantitative trading, taught through the Norse Stack
- **[Stack ADRs](docs/adr/)** — Redpanda choice, sim-only execution boundary, topic topology
- **Muninn**: steering docs, [14 ADRs](https://github.com/lgreene03/muninn/tree/main/docs/adr), demo walkthrough, deployment guide
- **Huginn**: [7 ADRs](https://github.com/lgreene03/huginn/tree/main/docs/adr), architecture, strategies, risk model, calibration
- **Sleipnir**: [7 ADRs](https://github.com/lgreene03/sleipnir/tree/main/docs/adr), contracts, integration test guides, operational runbook
- **muninn-py**: API reference, getting-started guide, example notebooks

---

## License

All Norse Stack services are licensed under [Apache License 2.0](LICENSE).
