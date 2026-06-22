# Forseti — Execution Transaction-Cost-Analysis (TCA)

Named for the Norse god of justice and reconciliation, **Forseti** adjudicates
the true cost of execution. It consumes the live fills topic
(`executions.fills.v1`) and, optionally, a realtime price feed
(`prices.realtime.v1`) for an arrival/mid benchmark, then decomposes every fill
into its cost components and aggregates them per-instrument and overall.

Modelled on **Odin** (`services/odin`): same stdlib `http.server` + Kafka
consumer + lock-guarded tracker + JSON/CORS patterns. No API key required.

## Per-fill TCA

For each fill Forseti computes:

| Field | Meaning |
|-------|---------|
| `fees` | The reported `transaction_cost`. |
| `fee_bps` | `fees / notional * 1e4`. |
| `slippage_bps` | **Prefers** the fill's own non-zero `slippage_bps`; **else**, if a price feed gave an arrival/mid `m` at-or-just-before the fill, `sign * (fill_price - m) / m * 1e4` where `sign = +1` BUY / `-1` SELL; **else `null`**. |
| `slippage_source` | `reported`, `arrival`, or `null`. |
| `liquidity` | `maker` / `taker` (normalized from the fill's liquidity field), or `null` if unrecognized. |
| `implementation_shortfall` | `slippage_cost + fees` on the traded notional (fees-only when no slippage benchmark exists). |

### Slippage sign convention

A **BUY filled above** the arrival/mid is **adverse → positive** slippage. A
**SELL filled below** the arrival/mid is also **adverse → positive**. Favourable
directions are negative.

### Honesty rule

Forseti **never fabricates a benchmark**. If no arrival price is available for a
fill and the fill carries no reported slippage, `slippage_bps` is `null` and the
analysis `basis` is labelled **`fees + reported-slippage only`**. Once any fill
in the window is benchmarked against an arrival price, the basis becomes
`fees + arrival/mid slippage where available`.

## Aggregates (per-instrument and overall)

`avgSlippageBps` (mean over fills where slippage is defined), `totalFees`,
`totalNotional`, `totalFills`, `makerCount` / `takerCount` + `makerTakerRatio`,
`avgFeeBps` (notional-weighted), `totalImplementationShortfall`.

## Endpoints (`FORSETI_PORT`, default `8096`)

- `GET /api/tca` — `{available, asOf, basis, overall:{...}, byInstrument:{INSTR:{...}}}`. `available:false` when no fills yet.
- `GET /api/tca/fills?limit=N` — recent per-fill TCA records, newest-first.
- `GET /healthz` — liveness (503 when the consumer thread is wedged).
- `GET /metrics` — Prometheus counters.

## Environment

| Var | Default | Purpose |
|-----|---------|---------|
| `KAFKA_BROKERS` | `redpanda:29092` | Kafka/Redpanda bootstrap. |
| `FILLS_TOPIC` | `executions.fills.v1` | Live fills topic. |
| `PRICES_TOPIC` | `prices.realtime.v1` | Arrival/mid benchmark feed. |
| `PRICES_ENABLED` | `false` | Consume the price feed for arrival benchmarking. |
| `FORSETI_PORT` | `8096` | HTTP port. |
| `ARRIVAL_MAX_AGE_SECS` | `60` | Max age of an arrival price to benchmark a fill. |
| `ACCESS_CONTROL_ALLOW_ORIGIN` | `*` | CORS allowed origin. |

## Run

```bash
# Tests (kafka is stubbed; no Kafka/DB required)
python3 -m pytest services/forseti/tests/ -q

# Local
FORSETI_PORT=8096 python3 services/forseti/forseti.py

# Docker
docker build -t forseti services/forseti
docker run -p 8096:8096 -e KAFKA_BROKERS=redpanda:29092 forseti
```
