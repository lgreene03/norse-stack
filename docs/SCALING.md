# Scaling & replication model

This document maps each Norse Stack service to its scaling class
(**singleton**, **shardable**, or **stateless**) and spells out the
**shared-deduplication requirement** that must be satisfied before any service
runs with `replicas > 1`. It is the companion to the Helm chart
([`deploy/helm/norse-stack`](../deploy/helm/norse-stack)) — every `replicaCount`
in `values.yaml` defaults to `1` precisely because of the constraints below.

## TL;DR

| Service | Class | Safe to replicate? | Gate before `replicas>1` |
|---------|-------|--------------------|--------------------------|
| **huginn** | singleton | No (by default) | Strategy state + position must be shared & deduped across replicas, or partition the symbol universe so no two replicas trade the same instrument. |
| **sleipnir** | shardable | Yes (Kafka consumer group) | Per-replica rate limiter and any local SQLite ledger must move to shared/idempotent stores; fills must be idempotent on `(intent_id)`. |
| **muninn** | singleton (ingestion) / stateless (query) | Partial | Ingestion is a singleton (duplicate market-data writes); the query/API surface is stateless and replicable if split out. |
| **odin / huginn-ai** | singleton consumers | No (by default) | Both maintain in-memory accounting/model state keyed off the fills stream; replicas would double-count unless they share a deduped store. |

## The shared-dedup requirement

Every consumer in the stack reads from Kafka topics delivered **at-least-once**.
A single replica de-duplicates redelivered messages using local state (an
in-memory or on-disk seen-set keyed by event id). The moment a second replica
joins:

1. **Kafka rebalances partitions** across the consumer group, so each replica
   sees a *subset* of messages — but only if every consumer shares the same
   `group.id` AND the work is partition-local.
2. **Local de-dup state no longer covers the whole stream.** Replica A's seen-set
   does not know what replica B processed. A redelivery that lands on a
   different replica after a rebalance is processed twice.

Therefore, **before** raising any `replicaCount`:

> Move per-event de-duplication from per-replica local state to a **shared,
> idempotent store** (e.g. a Postgres unique constraint on the event/intent id,
> or a Redis seen-set with TTL), so dedup is correct regardless of which replica
> handles a given message after a rebalance.

The existing duplicate-fill alerts (`HuginnAiDuplicateFills`, `OdinDuplicateFills`
in `monitoring/alerts/norse-stack.yml`) are the canary: if they fire after you
scale out, the shared-dedup requirement was not met.

## Per-service detail

### huginn — strategy engine (SINGLETON)

- Holds **signed position state** and per-strategy state that it persists
  (`huginn_strategy_state_persist_errors_total` guards this). Two replicas
  consuming the same `features.obi.v1` partitions would each generate orders
  from the same signal ⇒ **double-trading** and a corrupted shared position.
- **To replicate:** either (a) partition the *symbol universe* and run one
  replica per shard with disjoint Kafka partitions and disjoint position
  ledgers, or (b) elect a single active leader (e.g. via a lease) with the
  others as warm standbys. Plain horizontal replication is unsafe.
- Helm: `huginn.replicaCount` defaults to `1`; the Deployment template carries a
  comment pointing here.

### sleipnir — execution gateway (SHARDABLE)

- Consumes `executions.intents.v1` with a Kafka **consumer group**
  (`sleipnir-gateway`), so partitions *do* shard cleanly across replicas — this
  is the most replication-friendly service.
- **Blockers to fix first:**
  - The **rate limiter** (`RATE_LIMIT_RPS`) is per-replica; N replicas multiply
    the effective exchange request rate by N. Use a shared/distributed limiter
    or divide the budget by replica count.
  - The local **SQLite ledger** (`DB_PATH=/app/data/sleipnir.db`) is per-pod;
    move it to a shared DB so the sim ledger is consistent, and ensure fills are
    **idempotent on `intent_id`** so a redelivered intent cannot double-fill.
- With those addressed, `sleipnir.replicaCount > 1` is safe.

### muninn — market data (SINGLETON ingestion / STATELESS query)

- **Ingestion** (Binance stream → warehouse) is a singleton: two ingesters would
  write duplicate raw market data to Postgres/MinIO. Keep `replicaCount: 1` for
  the ingestion role.
- **Query/API** (read-only warehouse access, actuator) is **stateless** and
  could be replicated freely if extracted into a separate deployment that does
  not run ingestion. The current single Deployment couples both, so it inherits
  the singleton constraint until split.

### odin & huginn-ai — analytics / ML consumers (SINGLETON)

- Both consume `executions.fills.v1` and maintain **in-memory accounting**
  (`odin` portfolio from `INITIAL_CASH`) or **model state** (`huginn-ai`
  retraining). Replicas would double-count fills / train divergent models.
- Not part of the core Helm chart; if added, gate on the shared-dedup
  requirement above (shared portfolio store / shared model store + leader
  election for training).

## Stateless infrastructure note

The shared infra (Redpanda/Kafka, Postgres, MinIO, Tempo, Prometheus) is **not**
scaled by this stack's app charts — it is a documented external dependency (see
[`deploy/helm/README.md`](../deploy/helm/README.md)). Scale those via their own
purpose-built operators/charts, not by bumping app `replicaCount`.
