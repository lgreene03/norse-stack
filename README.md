# Norse Stack

**A distributed quantitative trading infrastructure built from first principles.**

[![Muninn CI](https://github.com/lgreene03/muninn/actions/workflows/ci.yml/badge.svg)](https://github.com/lgreene03/muninn/actions/workflows/ci.yml)
[![Huginn CI](https://github.com/lgreene03/huginn/actions/workflows/ci.yml/badge.svg)](https://github.com/lgreene03/huginn/actions/workflows/ci.yml)
[![Sleipnir CI](https://github.com/lgreene03/sleipnir/actions/workflows/ci.yml/badge.svg)](https://github.com/lgreene03/sleipnir/actions/workflows/ci.yml)
[![muninn-py CI](https://github.com/lgreene03/muninn-py/actions/workflows/ci.yml/badge.svg)](https://github.com/lgreene03/muninn-py/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Norse Stack is a four-service distributed trading system that ingests market data, computes deterministic features, executes quantitative strategies, and routes orders to exchanges. Named after figures from Norse mythology, each service has a single responsibility and communicates via Kafka (Redpanda).

---

## Architecture

```
                            ┌──────────────────────────────────────────────┐
                            │              Norse Stack                     │
                            │                                              │
  Exchange ──WebSocket──►   │  ┌─────────┐   features   ┌─────────┐      │
  (Binance)                 │  │ MUNINN  │──────────────►│ HUGINN  │      │
                            │  │ Feature │   (Redpanda)  │ Strategy│      │
                            │  │ Engine  │               │ Engine  │      │
                            │  └─────────┘               └────┬────┘      │
                            │       ▲                         │           │
                            │       │                    intents          │
                            │   muninn-py                     │           │
                            │   (Research                     ▼           │
                            │    SDK)                   ┌──────────┐      │
                            │                           │ SLEIPNIR │      │
                            │                           │ Execution│──►Exchange
                            │                           │ Gateway  │◄──(fills)
                            │                           └──────────┘      │
                            └──────────────────────────────────────────────┘
```

### Services

| Service | Language | Role | Repo |
|---------|----------|------|------|
| **[Muninn](https://github.com/lgreene03/muninn)** | Java 21 / Spring Boot | Feature computation engine. Ingests market data, computes deterministic streaming features (VWAP, OBI), serves them via Kafka topics and a Query API. Central claim: any value emitted live is reproducible byte-for-byte via replay. | [repo](https://github.com/lgreene03/muninn) · [docs](https://lgreene03.github.io/muninn) |
| **[Huginn](https://github.com/lgreene03/huginn)** | Go 1.25 | Strategy execution engine. Consumes features, runs pluggable trading strategies (OBI, VPIN, EMA Crossover, VWAP Deviation), manages risk controls, and publishes order intents. Includes a React operator dashboard. | [repo](https://github.com/lgreene03/huginn) · [docs](https://lgreene03.github.io/huginn) |
| **[Sleipnir](https://github.com/lgreene03/sleipnir)** | Go 1.25 | Order execution gateway. Bridges Huginn's intents to Binance via REST/WebSocket, enforces rate limits and pre-trade risk, reports fills back over Kafka. Sim mode for testing without credentials. | [repo](https://github.com/lgreene03/sleipnir) |
| **[muninn-py](https://github.com/lgreene03/muninn-py)** | Python 3.10+ | Research SDK and CLI. Pulls features into Polars/pandas DataFrames, computes IC/forward returns, includes a Streamlit dashboard. | [repo](https://github.com/lgreene03/muninn-py) · [docs](https://lgreene03.github.io/muninn-py) |

### Data Flow

```
Exchange → Muninn (trades → features via Redpanda)
       → features.obi.v1, features.vwap.1m.v1, ...
              ↓
         Huginn (feature → strategy signal → order intent)
       → executions.intents.v1
              ↓
         Sleipnir (intent → risk check → rate limit → exchange submit)
       → executions.fills.v1
              ↓
         Huginn (fill → portfolio update → PnL tracking)
```

---

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Git

### Clone all repos

```bash
git clone https://github.com/lgreene03/norse-stack.git
cd norse-stack
./scripts/clone-all.sh
```

### Boot the full stack

```bash
docker compose up -d
```

This starts Muninn, Huginn, Sleipnir (sim mode), Redpanda, PostgreSQL, and MinIO. No exchange credentials needed — Sleipnir runs in simulated mode.

### Run the end-to-end smoke test

```bash
./scripts/smoke.sh
```

The smoke test validates the full pipeline: sends a synthetic trade to Muninn, verifies the feature is computed, confirms Huginn fires a strategy signal, and checks that Sleipnir produces a fill.

### Endpoints

| Service | URL | Description |
|---------|-----|-------------|
| Muninn API | http://localhost:8080 | Feature Query API, Swagger UI |
| Muninn Feature Stream | http://localhost:8080/api/v1/features/stream | Live SSE feature stream |
| Huginn API | http://localhost:8083 | Strategy snapshot, metrics |
| Huginn Dashboard | http://localhost:8084 | React operator dashboard |
| Sleipnir API | http://localhost:8085 | Health, readiness, metrics |
| Redpanda Console | http://localhost:8088 | Kafka topic browser |
| MinIO Console | http://localhost:9003 | S3 storage browser |

---

## Key Design Decisions

- **Deterministic replay.** Muninn's feature engine produces byte-identical outputs from the same input events, enforced by ArchUnit rules and CI integration tests. This is the load-bearing architectural invariant.
- **One computation path.** Live and replay use the same code — there is no separate batch pipeline. What runs in production is what runs in backtest.
- **Kafka-native boundaries.** Services communicate exclusively through Redpanda topics. No shared databases, no direct RPC. Each service owns its state.
- **Local-first.** The full stack runs on a single machine under Docker Compose. No cloud services required for development or testing.
- **Sim mode everywhere.** Sleipnir's sim exchange and Muninn's synthetic trade ingestion mean the entire pipeline is testable without exchange credentials.

---

## Project Stats

| Metric | Value |
|--------|-------|
| Total lines of code | ~30,000 |
| Languages | Java, Go, Python, TypeScript |
| Test count | 400+ (unit, integration, contract, determinism, e2e) |
| Architecture Decision Records | 14 |
| Steering documents | 23 |
| Commits | 200+ |

---

## Technology

| Layer | Technology |
|-------|-----------|
| Feature Engine | Java 21, Spring Boot 3.5, DuckDB, Parquet, Iceberg |
| Strategy Engine | Go 1.25, Kafka (segmentio), Prometheus |
| Execution Gateway | Go 1.25, Binance REST/WS, SQLite, token-bucket rate limiter |
| Research SDK | Python 3.10+, Polars, pandas, Pydantic v2, httpx |
| Message Broker | Redpanda (Kafka-compatible) |
| Storage | PostgreSQL 16, MinIO (S3-compatible), Parquet/Iceberg |
| Observability | Prometheus, Grafana, Tempo (distributed tracing) |
| Infrastructure | Docker Compose, Terraform (AWS reference), Helm |

---

## Documentation

Each service maintains its own detailed documentation:

- **Muninn**: 23 steering docs, 9 ADRs, demo walkthrough, deployment guide → [Reading Guide](https://github.com/lgreene03/muninn/blob/main/docs/steering/READING_GUIDE.md)
- **Huginn**: MkDocs site with architecture, strategies, risk model, calibration → [Docs](https://lgreene03.github.io/huginn)
- **Sleipnir**: Contracts, integration test guides, operational runbook → [Repo](https://github.com/lgreene03/sleipnir)
- **muninn-py**: MkDocs API reference, getting-started guide, example notebooks → [Docs](https://lgreene03.github.io/muninn-py)

---

## License

All Norse Stack services are licensed under [Apache License 2.0](LICENSE).
