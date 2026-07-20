# Architecture

The Norse Stack is a local, end-to-end quantitative trading **simulation**: real
(unauthenticated) Binance public market data flows through feature computation, a
cost-aware strategy engine, and a simulated execution gateway, with analytics,
ML, and full observability layered on top. Execution is **sim-only** — no orders
ever reach a real exchange.

This document is the source of truth for the topology. GitHub renders the
[`mermaid`](https://github.blog/2022-02-14-include-diagrams-markdown-files-mermaid/)
blocks below natively, so no external renderer is needed. Keep it in sync with
[`docker-compose.yml`](../docker-compose.yml) and
[`scripts/provision-topics.sh`](../scripts/provision-topics.sh) (the Kafka topic
contracts are documented in [`CONTRACTS.md`](CONTRACTS.md)).

## Container topology

The 21 long-running services from `docker-compose.yml` (the full stack is 23
containers — `topic-init` and `minio-init` are one-shot bootstrap jobs that
provision Kafka topics / the MinIO bucket and then exit, so they are omitted here).
Solid edges are Kafka topics (labelled with the topic name); dashed edges are
synchronous calls (HTTP/gRPC, JDBC, S3, OTLP) or scrapes.

```mermaid
flowchart LR
    binance["Binance<br/>public WS / REST"]

    subgraph ingest["Market data & features"]
        muninn["muninn<br/>Java / Spring Boot<br/>feature engine"]
        obibridge["obi-bridge<br/>Python<br/>10-layer OBI signals"]
    end

    subgraph strategy["Strategy & execution"]
        huginn["huginn<br/>Go strategy engine<br/>cost-hurdle gate · portfolio"]
        sleipnir["sleipnir<br/>Go execution gateway<br/>sim fills · risk"]
    end

    subgraph analytics["Analytics, explainability & ML"]
        odin["odin<br/>Python<br/>analytics (Sharpe, VaR, cost)"]
        bragi["bragi<br/>Python<br/>trade explainability"]
        huginnai["huginn-ai<br/>Python<br/>XGBoost signal predictor"]
        newssentinel["news-sentinel<br/>Python<br/>LLM news sentiment"]
    end

    subgraph planes["Research, data & execution-quality planes"]
        mimir["mimir<br/>Python<br/>point-in-time feature store"]
        research["research-gateway<br/>Go<br/>walk-forward/PBO as a service"]
        forseti["forseti<br/>Python<br/>execution TCA · market impact"]
        heimdall["heimdall<br/>Python<br/>Gaussian-HMM regime"]
    end

    subgraph infra["Shared infrastructure"]
        redpanda["redpanda<br/>Kafka broker"]
        console["redpanda-console"]
        pgmuninn[("postgres-muninn")]
        pghuginn[("postgres-huginn")]
        minio[("MinIO<br/>S3 object store")]
    end

    subgraph obs["Observability"]
        prometheus["prometheus"]
        grafana["grafana"]
        tempo["tempo<br/>traces (OTLP)"]
        alertmanager["alertmanager"]
    end

    ollama["Ollama<br/>host LLM"]

    %% ── Market data in ──────────────────────────────────────────────
    binance -->|"WS trades"| muninn
    binance -->|"REST order book"| obibridge

    %% ── Kafka feature & execution topics ────────────────────────────
    muninn -->|"events.trade"| redpanda
    muninn -->|"features.vwap.1m.v1"| redpanda
    obibridge -->|"features.obi.v1"| redpanda

    redpanda -->|"features.obi.v1"| huginn
    huginn -->|"executions.intents.v1"| redpanda
    huginn -->|"prices.realtime.v1"| redpanda
    redpanda -->|"executions.intents.v1"| sleipnir
    sleipnir -->|"executions.fills.v1"| redpanda

    %% ── Fills + features fan-out to analytics ───────────────────────
    redpanda -->|"executions.fills.v1"| huginn
    redpanda -->|"executions.fills.v1"| odin
    redpanda -->|"executions.fills.v1"| bragi
    redpanda -->|"features.obi.v1 + fills"| huginnai
    redpanda -->|"features.obi.v1"| bragi

    %% ── Platform planes: feature store, regime, execution-quality ───
    redpanda -->|"features.obi.v1"| mimir
    redpanda -->|"features.obi.v1"| heimdall
    redpanda -->|"executions.fills.v1"| forseti
    heimdall -.->|"warm-start /api/features/history"| mimir
    %% research-gateway is a standalone sidecar: it replays a JSONL dataset over
    %% HTTP (POST /api/research/runs), off the live Kafka path, so it has no topic edge.

    %% ── Synchronous deps (dashed) ───────────────────────────────────
    muninn -.->|JDBC| pgmuninn
    muninn -.->|S3| minio
    huginn -.->|portfolio state| pghuginn
    redpanda -.-> console
    newssentinel -.->|sentiment| ollama

    %% ── Observability (dashed) ──────────────────────────────────────
    muninn -.->|OTLP| tempo
    huginn -.->|OTLP| tempo
    sleipnir -.->|OTLP| tempo
    prometheus -.->|scrape| huginn
    tempo -.->|remote-write| prometheus
    grafana -.->|query| prometheus
    grafana -.->|query| tempo
    prometheus -.->|alerts| alertmanager
```

### Why two feature producers?

- **muninn** is the durable, deterministic feature engine: it ingests raw
  Binance trades, computes windowed features (e.g. `features.vwap.1m.v1`), and
  persists raw + warehouse data to Postgres and MinIO for replay and research.
- **obi-bridge** is the low-latency live path that feeds the strategy. It polls
  the Binance order book and emits 10-layer order-book-imbalance (OBI) features
  on `features.obi.v1`, which is the topic **huginn** actually trades on.

## Single-trade sequence

How one OBI feature event becomes a fill and lands in the portfolio and
analytics. The **cost-hurdle gate** is the load-bearing step: huginn only emits
an intent when the expected net-of-cost edge clears `COST_HURDLE_K x` round-trip
cost (fees + slippage). On the 24h fixture, `k=1` flipped realized PnL from
-59 (235 fills) to +1 (32 fills) by killing fee-bleeding marginal trades — see
[`RESULTS.md`](RESULTS.md).

```mermaid
sequenceDiagram
    autonumber
    participant B as Binance
    participant OB as obi-bridge
    participant K as Redpanda
    participant H as huginn
    participant S as sleipnir
    participant O as odin / bragi / huginn-ai

    B->>OB: order book snapshot
    OB->>OB: compute 10-layer OBI feature
    OB->>K: produce features.obi.v1
    K->>H: deliver features.obi.v1

    H->>H: strategy signal (OBI vs threshold)
    Note over H: Cost-hurdle gate —<br/>edge ≥ k × round-trip cost?

    alt edge clears the hurdle
        H->>H: pre-trade risk check
        H->>K: produce executions.intents.v1
        K->>S: deliver executions.intents.v1
        S->>S: rate limit + sim fill (tx cost bps)
        S->>K: produce executions.fills.v1
        K->>H: deliver executions.fills.v1
        H->>H: update portfolio (cash, position, PnL)
        K->>O: deliver executions.fills.v1
        O->>O: analytics, explainability, ML labels
    else edge below hurdle
        H->>H: block trade (no intent)
        Note over H: logged to bragi as a<br/>blocked decision
    end
```

## Ports & topics

Full port map lives in [`../CLAUDE.md`](../CLAUDE.md); Kafka topic partition /
retention / cleanup contracts live in [`CONTRACTS.md`](CONTRACTS.md) and are
provisioned by [`../scripts/provision-topics.sh`](../scripts/provision-topics.sh).

| Topic | Producer | Consumers |
|-------|----------|-----------|
| `events.trade` | muninn | (warehouse / replay) |
| `features.vwap.1m.v1` | muninn | (warehouse / replay) |
| `features.obi.v1` | obi-bridge | huginn, bragi, huginn-ai |
| `prices.realtime.v1` | huginn | (live exit monitoring) |
| `executions.intents.v1` | huginn | sleipnir |
| `executions.fills.v1` | sleipnir | huginn, odin, bragi, huginn-ai |
