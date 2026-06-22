# Mimir — Point-in-Time (no-lookahead) Feature Store

Mimir consumes feature events from Kafka (`features.obi.v1`, produced by
`obi-bridge`) and persists every record into a file-backed SQLite store,
recording **both** timestamps that matter for leakage-free backtesting:

| Column        | Meaning                                                            |
|---------------|--------------------------------------------------------------------|
| `event_time`  | the feature payload's own timestamp (obi-bridge `eventTime`)       |
| `ingest_time` | the wall-clock instant **Mimir** physically received the record    |
| `instrument`  | e.g. `BTC-USDT`                                                     |
| `feature`     | the full feature JSON                                              |

Index: `(instrument, event_time, ingest_time)`.

## The point-in-time guarantee

A query `as_of=T` returns, per instrument, the most recent feature with:

```
event_time  <= T   AND   ingest_time <= T
```

The **`ingest_time` guard is the whole point**. A feature whose `event_time <= T`
but that physically *arrived* after `T` (late-arriving or revised data) must
**not** be visible at `T` — a system running at time `T` could not have known it
yet. This makes any backtest joined against Mimir leakage-free by construction:
no look-ahead from corrections that landed after the decision moment.

## Endpoints (`MIMIR_PORT`, default 8095)

| Method / Path | Description |
|---------------|-------------|
| `GET /api/features?instrument=X&as_of=ISO8601` | Point-in-time lookup per the rule above. `instrument` optional (all instruments). `as_of` optional (defaults to now = latest known). Returns `{asOf, basis, features:[{instrument, event_time, ingest_time, feature}]}`. |
| `GET /api/features/history?instrument=X&limit=N` | Recent stored rows for `X`, newest-first. |
| `GET /api/sources` | Per-instrument lineage: count, first/last `event_time`, last `ingest_time`, max ingest lag (secs). |
| `GET /healthz` | 200 when the consumer loop is alive (503 if wedged). |

## Configuration

| Env var          | Default            | Meaning                              |
|------------------|--------------------|--------------------------------------|
| `KAFKA_BROKERS`  | `redpanda:29092`   | Kafka/Redpanda bootstrap servers     |
| `FEATURES_TOPIC` | `features.obi.v1`  | Topic to consume                     |
| `MIMIR_PORT`     | `8095`             | HTTP API port                        |
| `MIMIR_DB`       | `data/mimir.db`    | SQLite file path (persistent)        |

Mimir replays the full topic from the beginning on every start (fresh unique
group id, `auto_offset_reset=earliest`, no committed offset). Unlike Odin it
does **not** dedup, because re-inserting an event with a new `ingest_time` would
corrupt the point-in-time history — the SQLite file (`MIMIR_DB`) is the durable
record. To fully rebuild, delete the DB file.

## Develop / test

```bash
cd norse-stack
python3 -m py_compile services/mimir/mimir.py
python3 -m pytest services/mimir/tests/ -q   # kafka is stubbed; no broker needed
```

Tests seed the SQLite store directly (no Kafka), each on its own temp-file DB.
