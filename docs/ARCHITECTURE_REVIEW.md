# Norse Stack — Principal Architecture Review

## Current State Assessment

### Architecture Overview

Norse Stack is a four-service distributed quantitative trading infrastructure built on event-driven, deterministic-replay-first principles. The data flow is:

```
Exchange Data → Muninn (ingest + features) → Huginn (strategy + risk) → Sleipnir (execution) → Fill
                  ↑                                ↑                          ↓
              muninn-py                      React dashboard            Exchange API
            (research SDK)              (equity, fills, halt)        (Binance testnet)
```

**Services:**

| Service | Language | LOC | Purpose |
|---------|----------|-----|---------|
| Muninn | Java 21 / Spring Boot 4.1 | ~8K | Event ingestion, feature computation (VWAP), Parquet storage, deterministic replay, SSE streaming |
| Huginn | Go 1.25 | ~5.5K | Strategy execution (4 strategies), portfolio tracking, risk management, backtesting, calibration |
| Sleipnir | Go 1.26 | ~5.5K | Order routing, rate limiting, sim/live exchange backends, boot reconciliation |
| muninn-py | Python 3.10+ | ~3K | Research SDK, CLI, Streamlit dashboard, Polars/pandas DataFrames |

**Infrastructure:** Redpanda (Kafka-compatible), PostgreSQL 16, MinIO (S3-compatible), DuckDB (analytics).

### Strengths

1. **Immutable event model** — sealed MarketEvent hierarchy (Trade, Candle, BookSnapshot, OrderDelta) with UUIDv7 IDs, event-time ordering, and compile-time exhaustiveness
2. **Deterministic replay** — same FeatureEngineRunner processes both live and replay via EventSource abstraction; output routed to `.replay` suffix topics for side-by-side comparison
3. **Real-time/historical parity** — pure-function FeatureComputer interface (`compute(definition, events) → Map`) guarantees identical results across paths
4. **Pluggable exchange backends** — Sleipnir's ExchangeConnector interface with Binance (production) and Simulator (testing); clean separation of concerns
5. **Boot reconciliation** — Sleipnir queries exchange state on startup, synthesizes backfill fills for any gap between last DB state and exchange state
6. **Composable risk controls** — Huginn: trailing drawdown, daily loss limit, per-instrument position limits, volatility-scaled throttle, staleness watchdog, manual circuit breaker. Sleipnir: YAML-driven per-instrument caps, daily order counts, rate limiting, operator kill switch
7. **Research SDK** — muninn-py provides Polars DataFrames, CLI, Streamlit dashboard, caching, async client — genuine research-to-production bridge
8. **Observability baseline** — Prometheus metrics on all services, OpenTelemetry tracing (opt-in), structured JSON logging, W3C TraceContext across Kafka
9. **Strategy lifecycle** — version-tagged state persistence, hot parameter updates via HTTP, backtester with HTML report generation, grid-search calibrator with walk-forward cross-validation
10. **Architecture enforcement** — ArchUnit rules in Muninn prevent forbidden cross-layer dependencies

### Weaknesses

1. **Single-instrument hardcoding** — Muninn's FeatureEngineRunner hardcodes `"BTC-USDT"` as the partition key; FeatureArchivalConsumer similarly hardcoded; Binance adapter processes only `instruments.getFirst()`
2. **VWAP-only feature engine** — only VwapComputer is wired into the live engine loop; OBI, MicroPrice, VPIN computers exist as classes but are not dispatched
3. **No feature registry** — FeatureDefinition records exist but aren't used to dynamically register or discover features; the engine is hardwired
4. **Flat sim fills** — Sleipnir sim mode fills at order.Price or a flat $50,000 default; no order-book-based matching, no partial fills, no rejection probability
5. **In-memory portfolio** — Huginn's positions and PnL live in memory; recovery replays all fills from PostgreSQL on boot (O(n) in fill count)
6. **Java serialization for checkpoints** — Muninn's CheckpointManager uses Java serialization; not cross-language, no forward/backward versioning
7. **No schema registry** — Kafka topics are strings; no Avro/Protobuf schema registry for contract enforcement
8. **At-least-once semantics** — no Kafka transactions or exactly-once processing; double-counted features possible on crash during publish
9. **Single-partition topics** — no explicit partitioning strategy; cannot parallelize consumption by instrument
10. **No data retention policy** — Parquet files in MinIO grow unbounded; no compaction, no tiered storage

### Missing Capabilities

1. **Multi-instrument concurrent trading** — architecture is single-instrument throughout
2. **Feature catalog and discovery** — no way to register, version, deprecate, or search features
3. **Backtest audit and bias detection** — no lookahead/survivorship/leakage detection
4. **Alpha decay monitoring** — no rolling Sharpe, factor IC, prediction drift tracking
5. **Data lineage and provenance** — inputEventIds captured but no end-to-end lineage graph
6. **Trade explainability** — no capture of which features contributed to each signal, or why risk accepted/rejected
7. **AI-assisted querying** — no natural language interface to strategies, PnL, or pipeline state
8. **Multi-strategy orchestration** — Huginn runs one strategy; multi-strategy requires multiple instances
9. **Smart order routing** — no iceberg, TWAP, VWAP execution algorithms
10. **Cross-venue arbitrage** — single exchange only

---

## Institutional Quant Needs Comparison

| # | Area | Score | Reason |
|---|------|-------|--------|
| 1 | Research → Production | 6/10 | muninn-py SDK + same engine for live/replay is strong; but no feature catalog, no experiment tracking, no model registry, no A/B deployment |
| 2 | Market Data Quality | 4/10 | Binance adapter with reconnection is solid; but no data quality checks (gap detection, duplicate detection, stale quote detection), no cross-source reconciliation, no reference data management |
| 3 | Data Lineage | 3/10 | inputEventIds on FeatureComputedEvent is a start; but no end-to-end lineage graph, no data versioning, no audit trail from signal to fill |
| 4 | Reproducibility | 8/10 | Deterministic replay with event-time watermarks, pure-function feature computers, separate replay topics, divergence detection framework — strongest area |
| 5 | Backtesting Accuracy | 5/10 | Backtester exists with calibrator and walk-forward; but no bias detection (lookahead, survivorship, leakage), sim fills unrealistic (no slippage model, no market impact), no transaction cost modeling beyond flat bps |
| 6 | Strategy Governance | 3/10 | Version-tagged state blobs, hot parameter updates; but no approval workflow, no change audit log, no rollback mechanism, no multi-environment promotion pipeline |
| 7 | Model Governance | 2/10 | No model registry, no model versioning, no champion/challenger framework, no drift detection, no approval gates |
| 8 | Alpha Decay Detection | 1/10 | No rolling performance metrics, no factor IC tracking, no regime detection, no capacity analysis |
| 9 | Risk Infrastructure | 6/10 | Trailing drawdown, daily loss, position limits, staleness watchdog, circuit breaker — solid for single-strategy; but no portfolio-level VaR, no stress testing, no scenario analysis, no Greeks |
| 10 | Developer Productivity | 7/10 | Docker Compose one-command boot, smoke tests, Swagger UI, CLI, Streamlit dashboard, Testcontainers; but no local replay of production data, no notebook integration for live features |
| 11 | AI-Assisted Development | 1/10 | No AI integration; no LLM-powered querying, no automated anomaly explanation, no code generation for strategies |
| 12 | Audit & Compliance | 2/10 | Immutable event log is foundational; but no audit API, no regulatory reporting, no access control, no data classification |
| 13 | Explainability | 2/10 | Strategy source code is readable; but no per-signal feature attribution, no decision capture, no human-readable explanations |
| 14 | Strategy Monitoring | 4/10 | Prometheus metrics + Grafana dashboard; but no rolling Sharpe dashboard, no anomaly detection, no alerting on performance degradation |

**Overall Score: 3.9 / 10** — Strong architectural foundations (reproducibility, event model, research SDK), but significant gaps in governance, monitoring, lineage, and AI assistance that institutional quant teams require.

---

## Proposed Systems

### 1. Heimdall — Backtest Audit & Validation

**Purpose:** Detect statistical and methodological biases in backtests before strategies go live.

**Detects:**
- Lookahead bias (features computed with future data)
- Survivorship bias (universe changes not reflected)
- Data leakage (train/test contamination)
- Feature leakage (target information in features)
- Overfitting (in-sample vs out-of-sample divergence)
- Hyperparameter instability (performance sensitivity to small parameter changes)
- Unrealistic fill assumptions (100% fill rate, no market impact)
- Unrealistic latency assumptions (zero-latency execution)

**Architecture:**
```
Backtest Results → Heimdall Validator → Audit Report + Confidence Score
                      ↓
              Bias Detectors (pluggable)
                      ↓
              Statistical Tests
                      ↓
              Warning Classification
```

**Components:**
- `heimdall/detector/` — pluggable bias detector interface
- `heimdall/detector/lookahead.go` — timestamp analysis: flag if any feature's windowEnd > trade's eventTime
- `heimdall/detector/survivorship.go` — compare backtest universe against historical constituent changes
- `heimdall/detector/leakage.go` — mutual information between features and target across train/test boundary
- `heimdall/detector/overfitting.go` — deflated Sharpe ratio (Harvey et al. 2015), combinatorial symmetric cross-validation
- `heimdall/detector/fill_realism.go` — compare assumed fill rate/latency against exchange microstructure
- `heimdall/detector/stability.go` — parameter perturbation: re-run with ±10% threshold, measure Sharpe variance
- `heimdall/api/` — REST API for submitting backtests and retrieving audit reports
- `heimdall/store/` — PostgreSQL for audit results; S3 for detailed analysis artifacts

**APIs:**
```
POST /api/v1/audits              — submit a backtest for audit (async)
GET  /api/v1/audits/{id}         — get audit status and results
GET  /api/v1/audits/{id}/report  — full audit report with recommendations
```

**Events:**
- Consumes: `backtests.completed.v1` (backtest metadata, fill journal, feature snapshots)
- Produces: `audits.completed.v1` (confidence score, warnings, recommendations)

**Output Schema:**
```json
{
  "strategy": "obi_threshold_v3",
  "confidence_score": 0.72,
  "warnings": [
    {"type": "overfitting", "severity": "high", "detail": "Deflated Sharpe 0.41 vs raw 2.1"},
    {"type": "fill_realism", "severity": "medium", "detail": "100% fill rate assumed; historical avg 73%"}
  ],
  "recommendations": [
    "Run walk-forward validation with 5+ folds",
    "Add 15bps market impact model",
    "Test parameter stability within ±20% band"
  ]
}
```

**Performance:** Audit should complete within 5 minutes for a 1-year daily backtest.

---

### 2. Odin — Alpha Decay & Strategy Health Monitoring

**Purpose:** Continuous monitoring of strategy health, alpha decay, and regime changes in production.

**Tracks:**
- Rolling Sharpe (20d, 60d, 252d windows)
- Drawdown behaviour (current, max, recovery time)
- Factor Information Coefficient (rolling IC of each feature vs forward returns)
- Prediction drift (strategy signal distribution shift over time)
- Feature drift (input feature distribution shift — KL divergence, PSI)
- Regime changes (volatility regime detection via hidden Markov model)
- Capacity degradation (market impact as a function of position size)

**Architecture:**
```
Kafka Topics (fills, features, signals)
      ↓
Odin Aggregator (streaming windows)
      ↓
  ┌───────────┬──────────────┬──────────────┐
  │ Rolling   │ Drift        │ Regime       │
  │ Analytics │ Detectors    │ Classifier   │
  └───────────┴──────────────┴──────────────┘
      ↓              ↓              ↓
  TimescaleDB / PostgreSQL
      ↓
  Alert Engine → Slack/Email/PagerDuty
      ↓
  Dashboard (Grafana)
```

**Components:**
- `odin/aggregator/` — streaming Kafka consumer, tumbling + sliding window aggregation
- `odin/analytics/` — rolling Sharpe, Sortino, Calmar, max drawdown, hit rate, profit factor
- `odin/drift/` — Population Stability Index (PSI), KL divergence, Kolmogorov-Smirnov test
- `odin/regime/` — HMM-based regime classification (low/medium/high volatility)
- `odin/alert/` — rule-based alerting with configurable thresholds and escalation
- `odin/api/` — REST API for querying analytics, historical comparisons
- `odin/dashboard/` — Grafana dashboard JSON provisioning

**Events:**
- Consumes: `executions.fills.v1`, `features.*.v1`, `signals.*.v1`
- Produces: `odin.alerts.v1`, `odin.analytics.v1`

**Metrics:**
```
odin_rolling_sharpe{strategy, window}
odin_max_drawdown{strategy}
odin_feature_drift_psi{feature, window}
odin_prediction_drift_kl{strategy, window}
odin_regime{strategy, current_regime}
odin_alpha_decay_score{strategy}
```

**Data Model:**
```sql
CREATE TABLE strategy_analytics (
  strategy_id    VARCHAR(64),
  timestamp      TIMESTAMPTZ,
  window_days    INT,
  sharpe         DOUBLE PRECISION,
  sortino        DOUBLE PRECISION,
  max_drawdown   DOUBLE PRECISION,
  hit_rate       DOUBLE PRECISION,
  profit_factor  DOUBLE PRECISION,
  PRIMARY KEY (strategy_id, timestamp, window_days)
);

CREATE TABLE feature_drift (
  feature_name   VARCHAR(64),
  timestamp      TIMESTAMPTZ,
  psi            DOUBLE PRECISION,
  kl_divergence  DOUBLE PRECISION,
  ks_statistic   DOUBLE PRECISION,
  PRIMARY KEY (feature_name, timestamp)
);
```

---

### 3. Mimir — Research Lineage & Provenance

**Purpose:** Complete audit trail from raw data through features, signals, risk decisions, to fills.

**Tracks:**
- Data versions (which Parquet files, which Kafka offsets)
- Feature versions (code SHA, config snapshot, FeatureDefinition)
- Strategy versions (strategy name, parameters, state blob SHA)
- Model versions (if ML-based features are added)
- Git commits (service versions at time of computation)
- Config snapshots (full config at time of each computation)
- Experiment metadata (backtest ID, calibration run ID, parameter sweep)

**Enables:**
```bash
norse replay trade_id
```
Reconstructs: the exact data → features → signals → risk decisions → orders that produced a specific trade.

**Architecture:**
```
All Services → Mimir Collector (sidecar or Kafka consumer)
                    ↓
              Lineage Graph (DAG)
                    ↓
              PostgreSQL (metadata) + S3 (artifacts)
                    ↓
              Replay Orchestrator
                    ↓
              Query API
```

**Components:**
- `mimir/collector/` — Kafka consumer for all event topics; extracts lineage edges
- `mimir/graph/` — DAG model: nodes (events, features, signals, orders, fills), edges (derived-from, triggered-by, approved-by)
- `mimir/store/` — PostgreSQL for lineage metadata; S3 for config/state snapshots
- `mimir/replay/` — orchestrates end-to-end replay from raw events to fills
- `mimir/api/` — REST + CLI for lineage queries

**APIs:**
```
GET  /api/v1/lineage/fill/{fillId}     — trace lineage of a fill back to raw events
GET  /api/v1/lineage/feature/{eventId} — trace lineage of a feature computation
POST /api/v1/replay                    — replay a specific trade's decision chain
GET  /api/v1/versions/feature/{name}   — all versions of a feature with diffs
GET  /api/v1/snapshots/{timestamp}     — full system config at a point in time
```

**Metadata Model:**
```sql
CREATE TABLE lineage_nodes (
  node_id     UUID PRIMARY KEY,
  node_type   VARCHAR(32),    -- 'trade_event', 'feature', 'signal', 'intent', 'fill'
  event_id    UUID,
  timestamp   TIMESTAMPTZ,
  service     VARCHAR(32),
  version     VARCHAR(64),    -- git SHA
  metadata    JSONB
);

CREATE TABLE lineage_edges (
  parent_id   UUID REFERENCES lineage_nodes,
  child_id    UUID REFERENCES lineage_nodes,
  edge_type   VARCHAR(32),    -- 'derived_from', 'triggered_by', 'approved_by', 'filled_by'
  PRIMARY KEY (parent_id, child_id)
);

CREATE TABLE config_snapshots (
  snapshot_id   UUID PRIMARY KEY,
  timestamp     TIMESTAMPTZ,
  service       VARCHAR(32),
  config_hash   VARCHAR(64),
  config_blob   JSONB
);
```

---

### 4. Bragi — Trade Explainability

**Purpose:** For every signal and order, capture the complete decision context and generate human-readable explanations.

**Captures:**
- Features used (which features were in the event)
- Feature values (exact values at decision time)
- Feature contributions (how much each feature influenced the signal)
- Risk decisions (which limits were checked, current vs limit)
- Position sizing decisions (vol-scaled limit, gross notional, available capacity)

**Architecture:**
```
Huginn (executor hooks) → Bragi Collector → Decision Store
                                                ↓
                                          Explanation Generator
                                                ↓
                                          REST API + Dashboard
```

**Components:**
- `bragi/collector/` — receives decision context events from Huginn (new Kafka topic)
- `bragi/store/` — PostgreSQL for decision records; indexed by fill ID and timestamp
- `bragi/explain/` — template-based natural language generation
- `bragi/api/` — REST API for querying explanations

**Event Schema (Huginn produces):**
```json
{
  "eventId": "uuid",
  "timestamp": "2026-06-19T10:30:45Z",
  "type": "signal_decision",
  "strategy": "obi_threshold",
  "instrument": "BTC-USDT",
  "decision": "BUY",
  "features": {
    "obi": {"value": 0.96, "threshold": 0.95, "contribution": 0.82},
    "spread": {"value": 0.01, "context": "tight"},
    "bidVolume": {"value": 15.3},
    "askVolume": {"value": 0.8}
  },
  "risk_checks": {
    "drawdown": {"current": 0.02, "limit": 0.20, "passed": true},
    "daily_loss": {"current": -500, "limit": -10000, "passed": true},
    "position_limit": {"current_notional": 12000, "limit": 500000, "passed": true}
  },
  "portfolio_state": {
    "cash": 98500,
    "net_position": 0.02,
    "unrealized_pnl": 150
  },
  "outcome": "accepted"
}
```

**Explanation Output:**
```
Trade BUY 0.01 BTC-USDT at 21:34:45 UTC

REASON: Order Book Imbalance reached +0.96 (threshold: 0.95), indicating 
extreme buy pressure. The OBI mean-reversion strategy expects this to 
reverse, generating a contrarian BUY signal.

FEATURES:
  - OBI: +0.96 (above 0.95 threshold) — primary trigger
  - Bid Volume: 15.3 BTC across 10 levels
  - Ask Volume: 0.8 BTC across 10 levels  
  - Spread: $0.01 (tight, low execution risk)

RISK: All checks passed
  - Drawdown: 2.0% of 20.0% limit
  - Daily Loss: -$500 of -$10,000 limit
  - Position: $12,000 of $500,000 limit

PORTFOLIO: $98,500 cash, 0.02 BTC net long, +$150 unrealized
```

**APIs:**
```
GET /api/v1/explain/fill/{fillId}     — explanation for a specific fill
GET /api/v1/explain/signal/{signalId} — explanation for a signal (even if not filled)
GET /api/v1/explain/latest            — last N decisions with explanations
```

---

### 5. Huginn AI — AI-Powered Platform Assistant

**Purpose:** Natural language interface to the entire Norse Stack — understands strategies, features, risk controls, orders, PnL, and data pipelines.

**Example Queries:**
```
"Why did the OBI strategy lose money yesterday?"
"Which strategies use the VWAP feature?"
"What changed between strategy parameter versions?"
"Which data source caused this anomaly?"
"Show me the equity curve for the last week"
"What would happen if I raised the OBI threshold to 0.98?"
```

**Architecture:**
```
User Query → Huginn AI Agent
                ↓
          Context Retriever (RAG)
                ↓
    ┌───────────┬──────────────┬──────────────┐
    │ Knowledge │ Live State   │ Historical   │
    │ Graph     │ APIs         │ Data         │
    └───────────┴──────────────┴──────────────┘
                ↓
          Claude API (tool use)
                ↓
          Response + Visualisation
```

**Components:**
- `huginn-ai/agent/` — Claude-powered agent with tool definitions for all Norse APIs
- `huginn-ai/context/` — RAG pipeline: embed documentation + code + ADRs, retrieve on query
- `huginn-ai/knowledge/` — knowledge graph of service relationships, feature dependencies, strategy parameters
- `huginn-ai/tools/` — tool definitions wrapping Norse Stack APIs (Muninn query, Huginn snapshot, Sleipnir telemetry, Mimir lineage, Odin analytics)
- `huginn-ai/security/` — read-only by default; write operations require explicit approval

**Tool Definitions (Claude API tool_use):**
```json
[
  {"name": "get_portfolio_snapshot", "description": "Current portfolio state"},
  {"name": "query_feature_timeseries", "description": "Historical feature values"},
  {"name": "get_strategy_config", "description": "Active strategy parameters"},
  {"name": "get_fill_history", "description": "Recent trade fills"},
  {"name": "get_rolling_sharpe", "description": "Rolling performance metrics"},
  {"name": "explain_trade", "description": "Why a specific trade was made"},
  {"name": "trace_lineage", "description": "Data lineage for a trade"},
  {"name": "run_backtest", "description": "Run a backtest with parameters"},
  {"name": "get_feature_drift", "description": "Feature distribution changes"}
]
```

**Security Model:**
- Read-only tools: no approval needed
- Write tools (parameter changes, halt/resume): require explicit user confirmation
- No access to exchange credentials, API keys, or secrets
- Audit log of all queries and tool invocations

---

## Priority Matrix

| Rank | System | Commercial Value | Pain Solved | Effort | Differentiation | OSS Adoption | Acquisition |
|------|--------|-----------------|-------------|--------|-----------------|-------------|-------------|
| 1 | **Odin** | 9/10 | Alpha decay is #1 institutional pain | Medium | High — few open-source alternatives | High — every quant team needs this | High |
| 2 | **Heimdall** | 8/10 | Backtest validation prevents costly mistakes | Medium | Very high — no credible OSS equivalent | Very high — universal need | Very high |
| 3 | **Bragi** | 7/10 | Regulatory requirement (MiFID II, SEC) | Low-Medium | High — most platforms lack this | Medium — compliance-driven | High |
| 4 | **Mimir** | 8/10 | Debugging production issues, audit compliance | High | Medium — some overlap with MLflow/DVC | Medium — niche but critical | High |
| 5 | **Huginn AI** | 6/10 | Developer productivity, onboarding | Medium | Medium — emerging space, many competitors | High — impressive demo value | Medium |

**Foundation work (required before any system):**
- Multi-instrument support in Muninn feature engine
- Feature registry and catalog
- Signal event schema (Huginn must publish decisions, not just fills)

---

## Implementation Plan

### Phase 1 — Foundation (4-6 weeks)

**Goal:** Remove single-instrument limitation, create feature registry, add signal events.

**Repositories affected:** muninn, huginn, norse-stack

**Muninn changes:**
- `feature/engine/FeatureEngineRunner.java` — extract instrument from events instead of hardcoding `"BTC-USDT"`
- `feature/engine/FeatureEngineConfiguration.java` — wire OBI and MicroPrice computers alongside VWAP
- `feature/registry/FeatureRegistry.java` (new) — in-memory registry of active feature definitions
- `feature/registry/FeatureRegistryController.java` (new) — CRUD API for feature registration
- `ingestion/adapter/BinanceWebSocketAdapter.java` — support multiple instruments from config
- `storage/FeatureArchivalConsumer.java` — derive instrument from event, not hardcoded

**Huginn changes:**
- `internal/executor/executor.go` — emit `SignalDecision` event to new Kafka topic `signals.decisions.v1` on every strategy evaluation
- `internal/model/signal.go` (new) — SignalDecision struct with feature values, risk check results, outcome
- `internal/kafka/signal_producer.go` (new) — producer for signal decisions topic

**norse-stack changes:**
- `docker-compose.yml` — add `MUNINN_INGESTION_BINANCE_INSTRUMENTS=btcusdt,ethusdt`

**Event contracts:**
- `signals.decisions.v1` — new topic for Huginn signal decisions (consumed by Bragi, Mimir)

**Testing:**
- Multi-instrument integration test in Muninn (Testcontainers)
- Signal decision event schema validation in Huginn
- End-to-end test: two instruments flow through full pipeline

**Documentation:**
- Update GETTING_STARTED.md with multi-instrument examples
- ADR for signal decision event schema

### Phase 2 — Bragi + Odin Core (6-8 weeks)

**Goal:** Trade explainability and basic alpha monitoring.

**New repository:** `bragi` (Go or Python)

**Bragi components:**
- `bragi/collector/kafka.go` — consume `signals.decisions.v1` and `executions.fills.v1`
- `bragi/store/postgres.go` — persist decision contexts, indexed by fill ID
- `bragi/explain/template.go` — natural language explanation generator
- `bragi/api/http.go` — REST API for querying explanations
- `bragi/Dockerfile` — container for docker-compose integration

**New repository:** `odin` (Python — NumPy/SciPy for analytics)

**Odin components:**
- `odin/aggregator/kafka_consumer.py` — consume fills, features, signals
- `odin/analytics/rolling.py` — rolling Sharpe, Sortino, max drawdown, hit rate
- `odin/drift/psi.py` — Population Stability Index for feature drift
- `odin/alert/rules.py` — configurable alert thresholds
- `odin/api/fastapi.py` — REST API for analytics queries
- `odin/Dockerfile` — container for docker-compose integration

**norse-stack changes:**
- `docker-compose.yml` — add bragi and odin services
- `services/bragi/` — Bragi Dockerfile and config
- `services/odin/` — Odin Dockerfile and config

**Data schemas:**
- PostgreSQL migrations for bragi (decision_contexts, explanations)
- PostgreSQL migrations for odin (strategy_analytics, feature_drift)

**Testing:**
- Bragi: explanation accuracy tests (given decision context, verify output)
- Odin: rolling analytics correctness tests (known data, known Sharpe)

### Phase 3 — Mimir Lineage (8-10 weeks)

**Goal:** End-to-end data lineage from raw events to fills.

**New repository:** `mimir` (Go)

**Components:**
- `mimir/collector/` — multi-topic Kafka consumer for all event types
- `mimir/graph/` — DAG construction: nodes (events) and edges (derived-from relationships)
- `mimir/store/` — PostgreSQL for lineage metadata; S3 for config snapshots
- `mimir/replay/` — replay orchestrator: given a fill ID, reconstruct the full decision chain
- `mimir/api/` — REST API for lineage queries
- `mimir/cli/` — `norse replay <trade_id>` CLI command

**Cross-service changes:**
- All services: add `X-Norse-Trace-ID` header to Kafka messages for correlation
- Muninn: include `inputEventIds` in all FeatureComputedEvents (already present for VWAP)
- Huginn: include `featureEventId` in signal decisions and order intents

**Data schemas:**
- `lineage_nodes` table (UUID, type, timestamp, service, version, metadata)
- `lineage_edges` table (parent, child, edge_type)
- `config_snapshots` table (service, timestamp, config hash, config blob)

**Testing:**
- End-to-end lineage test: inject trade → verify lineage chain from raw event to fill
- Replay accuracy test: replayed fill matches original fill

### Phase 4 — Huginn AI + Polish (6-8 weeks)

**Goal:** AI assistant, dashboard improvements, production hardening.

**New repository:** `huginn-ai` (Python)

**Components:**
- `huginn-ai/agent/` — Claude API integration with tool_use
- `huginn-ai/tools/` — tool definitions wrapping all Norse APIs
- `huginn-ai/context/` — RAG pipeline (embed docs, code, ADRs)
- `huginn-ai/api/` — WebSocket API for chat interface
- `huginn-ai/web/` — React chat UI component

**Cross-cutting improvements:**
- Schema registry (Redpanda Schema Registry or standalone)
- Kafka exactly-once semantics for Muninn feature engine
- Data retention policies for MinIO Parquet files
- Unified Grafana dashboard with Odin analytics
- Helm charts for Kubernetes deployment (production path)

**Testing:**
- AI agent accuracy tests: known queries → expected tool invocations
- Integration tests: agent queries live Norse Stack APIs

**Documentation:**
- Full API documentation for all new services
- Architecture decision records for each system
- Operator runbook for production deployment
