# Norse Stack — Wire Contracts

Canonical definitions of the Kafka topics and event schemas that cross service
boundaries in the Norse Stack. Producers and consumers MUST agree on these.

- Schema owners are listed per topic.
- Field-loss against these schemas is a contract break and should fail a
  contract test (see `services/obi-bridge/tests/test_contract.py`).

---

## Topics

| Topic                   | Producer        | Consumers              | Cleanup  | Retention | Partitions |
|-------------------------|-----------------|------------------------|----------|-----------|------------|
| `prices.realtime.v1`    | obi-bridge (WS) | huginn                 | delete   | 6h        | 6          |
| `features.obi.v1`       | obi-bridge / muninn | huginn, huginn-ai, bragi | delete | 24h     | 3          |
| `features.vwap.1m.v1`   | muninn          | huginn                 | delete   | 24h       | 3          |
| `events.trade`          | (ingest/smoke)  | muninn                 | delete   | 24h       | 3          |
| `executions.intents.v1` | huginn          | sleipnir               | delete   | 7d        | 3          |
| `executions.fills.v1`   | sleipnir        | huginn, odin           | delete   | 7d        | 3          |

### Per-topic provisioning rationale

These values are applied by `scripts/provision-topics.sh` (and the `topic-init`
compose service) instead of relying on broker auto-create, which yields a single
partition and unbounded retention.

- **`prices.realtime.v1`** — sub-second aggTrade ticks; highest volume on the
  stack and only needed for live exit monitoring. Short **6h** retention caps
  disk on the single-node dev broker; **6 partitions** spread the per-symbol
  fan-in for consumer parallelism.
- **`features.obi.v1` / `features.vwap.1m.v1` / `events.trade`** — one event per
  symbol per poll/window. **24h** retention is enough for same-day replay and
  feature→label joins; **3 partitions** is sufficient at this rate.
- **`executions.intents.v1` / `executions.fills.v1`** — the order audit trail.
  **7d** retention supports reconciliation and post-hoc P&L review.

All topics use `cleanup.policy=delete` (time-bounded). Replication factor is 1
(single-node dev broker).

---

## `features.obi.v1` — OBI feature event

Produced by `services/obi-bridge/bridge.py`. Consumed by Huginn
(`internal/model.FeatureEvent`), Huginn-AI, and Bragi.

Huginn decodes `values` as `map[string]float64`; **every entry in `values` MUST
be numeric** or the whole event is dropped. Non-numeric metadata (ids, version
strings) lives at the top level only.

### Top-level fields

| Field           | Type            | Notes |
|-----------------|-----------------|-------|
| `eventId`       | string (UUID)   | Unique per emitted event. |
| `eventTime`     | string (ISO-8601 Z) | **Exchange data time**, stamped from the most recent 5m kline close time — NOT wall-clock. Makes events replay-deterministic and joinable with future labels. |
| `ingestTime`    | string (ISO-8601 Z) | Wall-clock time the bridge built the event. Diverges from `eventTime` by the poll lag; used for freshness/latency monitoring. |
| `codeVersion`   | string          | Git SHA (or `CODE_VERSION`/`GIT_SHA` env, else `unknown`) of the producing bridge — provenance for reproducibility. |
| `inputEventIds` | array<string>   | Snapshot ids of the inputs that produced this feature: `orderbook:<symbol>:<lastUpdateId>` and `kline5m:<symbol>:<closeMs>`. |
| `featureName`   | string          | `"obi"`. |
| `featureVersion`| string          | `"v1"`. |
| `instrument`    | string          | Canonical instrument, e.g. `BTC-USDT`. |
| `windowStart`   | string (ISO-8601 Z) | Open time of the first 5m kline in the window. |
| `windowEnd`     | string (ISO-8601 Z) | Close time of the most recent 5m kline (== `eventTime`). |
| `signalTimeMs`  | int64           | `windowEnd` as epoch millis. |
| `values`        | object<string,number> | Feature values; see below. |

> When the exchange window is unavailable (e.g. empty kline response),
> `eventTime`/`windowStart`/`windowEnd` fall back to `ingestTime` so the event
> still validates; the divergence is detectable via `inputEventIds` lacking a
> `kline5m:` entry.

### `values` fields (all numeric)

| Key                | Meaning |
|--------------------|---------|
| `obi`              | Order-book imbalance in [-1, 1]. |
| `bidVolume`        | Summed bid volume over the top N levels. |
| `askVolume`        | Summed ask volume over the top N levels. |
| `spread`           | Best ask − best bid. |
| `midPrice`         | (best bid + best ask) / 2. |
| `levels`           | Book levels actually used. |
| `momentum`         | 5m EMA(12)/EMA(26) momentum. |
| `momentum1m`       | 1m momentum. |
| `momentum15m`      | 15m momentum. |
| `emaFast`          | 5m EMA(12). |
| `emaSlow`          | 5m EMA(26). |
| `volatility`       | ATR / last close (fractional). |
| `atr`              | Average true range. |
| `volumeRatio`      | Recent vs. older volume ratio. |
| `fearGreed`        | Crypto Fear & Greed Index (0–100). |
| `fundingRate`      | Perpetual funding rate (per 8h). |
| `oiChange`         | Open-interest % change since last poll. |
| `mlScore`          | Huginn-AI XGBoost confidence (0-1); provenance carried in the feature payload, consulted by the OBI strategy only when `STRATEGY_OBI_ML_GATE` is enabled (off by default). |
| `mlReady`          | 1.0 if the ML model is trained, else 0.0. |
| `newsSentiment`    | News-sentinel sentiment score. |
| `regimeVolAnn`     | Annualized realized volatility. |
| `regimeHurst`      | Hurst-exponent proxy. |
| `regimeAutocorr`   | Lag-1 return autocorrelation. |
| `regimeConfidence` | Regime classifier confidence (0–1). |

A round-trip contract test that builds an event from recorded inputs and
asserts no field loss against this schema lives at
`services/obi-bridge/tests/test_contract.py`.

---

## Distributed tracing

Huginn, Sleipnir, and Muninn export OTLP/gRPC spans to Tempo
(`OTEL_EXPORTER_OTLP_ENDPOINT=tempo:4317`). W3C TraceContext is propagated on
Kafka headers so `huginn → sleipnir → huginn` renders as one trace. Spans are
viewable in Grafana via the Tempo datasource (service graph + trace-to-logs /
trace-to-metrics enabled). See `sleipnir/docs/CONTRACTS.md` for the Kafka-header
trace-propagation contract.
