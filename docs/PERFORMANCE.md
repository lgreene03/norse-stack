# Performance envelope

Latency and throughput numbers for the Norse Stack, with the method to reproduce
each. These are **engineering telemetry** — how fast the machinery is. They say
nothing about returns or edge (the strategy has no measured out-of-sample edge;
see [EDGE_VERDICT.md](EDGE_VERDICT.md)).

Every number here is machine-dependent and, where noted, simulation-scale. The
point is not the absolute figures but that the paths are **measured and
regenerable**, not asserted.

## Storage path — Mimir point-in-time feature store

The differentiating storage operation is a point-in-time *as-of* query
(`event_time <= T AND ingest_time <= T`, latest-per-instrument), which is what
makes backtests causally honest. The microbenchmark populates a file-backed
SQLite store (single connection, lock-serialised, exactly as the service runs)
and times each path.

Reproduce:

```bash
python3 services/mimir/bench_pit.py --rows 20000 --ops 5000
```

Representative run (Apple M4, containerised `python:3.12-slim`; expect different
absolutes on other hardware, similar ratios):

| Operation                        |   p50   |   p95   |   p99   |  mean   |
|----------------------------------|--------:|--------:|--------:|--------:|
| **as-of query (1 instrument)**   |  5.7 µs |  6.5 µs |  8.2 µs |  5.9 µs |
| history read (limit 50)          |  2.79 ms|  3.04 ms|  3.56 ms|  2.81 ms|
| as-of query (all instruments)    |  5.67 ms|  6.60 ms|  7.14 ms|  5.75 ms|
| single insert (durable)          |  724 µs | 1.05 ms | 1.58 ms |  806 µs |
| single-insert throughput         | ~1,240 inserts/sec (one commit per insert) |

Reading these honestly:

- **The headline is the ~6 µs point-in-time query for one instrument.** The
  index on `(instrument, event_time, ingest_time)` makes the correlated
  latest-per-instrument subquery effectively O(log n). For comparison, rust-trade
  publishes a ~10 µs cache *hit*; a PIT as-of query is a strictly more involved
  operation, at comparable latency, without a cache.
- **Single-insert latency is dominated by per-write durability.** Each `store()`
  commits (fsync) on its own, so ~724 µs is disk-sync cost, not compute. Mimir
  ingests from a Kafka poll loop, so batching commits per poll would raise
  throughput by roughly an order of magnitude; the single-insert number is the
  conservative, fully-durable figure.
- **The all-instruments as-of query (~5.7 ms) is the current hotspot.** Without
  an instrument filter the correlated subquery re-evaluates per candidate row, so
  it scales with the row count. Per-instrument queries (the console's and
  Heimdall's actual access pattern) do not hit this path. It is the natural
  target for the shared cache tier on the roadmap, and is called out here rather
  than hidden.

## Decision path — Huginn signal-to-decision latency

Huginn instruments the tick-to-decision hot path with a Prometheus histogram
(`huginn_signal_to_decision_seconds`), reporting a p50 of roughly **3 ms** on the
simulation fixture. This is a self-reported in-process metric on ~1,440 bars, not
a statistical benchmark harness — treat it as indicative, and prefer the Go
micro-benchmarks below for methodologically firmer numbers.

Reproduce the Go micro-benchmarks (ns/op + allocs/op for the OBI and portfolio
hot paths):

```bash
cd ../huginn && make bench
```

## What is not yet measured

Called out honestly so the gaps are visible rather than implied-complete:

- **Sustained end-to-end throughput / backpressure / DLQ behaviour under load,**
  and a **soak** result, are not yet published. The storage microbenchmark above
  is a component measurement, not a full-stack load test.
- **Storage numbers are single-node SQLite**, appropriate for a simulation; they
  are not a claim about a production-scale store.

Both are tracked on the roadmap. Nothing here should be read as a throughput SLA.
