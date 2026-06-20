# 0003. Kafka topic topology

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Project maintainer
- **Related:** [scripts/provision-topics.sh](../../scripts/provision-topics.sh), [docs/CONTRACTS.md](../CONTRACTS.md), [ADR-0001 Redpanda as local broker](0001-redpanda-as-local-broker.md)

## Context

Services in the Norse Stack share no databases and make no direct RPC for the
data path — they communicate only through Kafka topics (the one exception is
Huginn's optional gRPC *query* API). That makes the set of topics, their
partitions, and their retention a stack-level contract that every service
depends on. Two failure modes have to be designed out:

1. **Auto-created topics.** Redpanda will auto-create a topic on first produce
   with broker defaults — a single partition and effectively unbounded
   retention. A single partition caps consumer parallelism; unbounded retention
   silently fills the disk on high-rate topics.
2. **One-size retention.** A sub-second price firehose and a 7-day execution
   audit trail have opposite retention needs; a uniform policy is wrong for at
   least one of them.

## Decision

Topics are **provisioned explicitly and idempotently** at boot by the
`topic-init` container running
[`scripts/provision-topics.sh`](../../scripts/provision-topics.sh), with
partition counts and retention chosen per topic by their role. The topology:

| Topic | Partitions | cleanup.policy | retention | Role |
|---|---:|---|---|---|
| `prices.realtime.v1` | 6 | delete | 6h | High-rate sub-second price ticks (live exit monitoring); more partitions for per-symbol fan-in, short retention for the firehose |
| `features.obi.v1` | 3 | delete | 24h | Order-book-imbalance feature events (one per symbol per poll) |
| `features.vwap.1m.v1` | 3 | delete | 24h | 1-minute windowed VWAP feature |
| `events.trade` | 3 | delete | 24h | Raw ingested trades |
| `executions.intents.v1` | 3 | delete | 7d | Order intents — audit trail |
| `executions.fills.v1` | 3 | delete | 7d | Fills — audit trail |

Retention tiers: **6h** for the price firehose, **24h** for feature/raw-trade
topics (enough for same-day replay and label joins), **7d** for execution audit
topics (intent/fill trail for reconciliation). The script `create`s with explicit
config and then `alter-config`s so re-running converges an already-existing topic
to the desired values. Per-topic rationale also lives in
[docs/CONTRACTS.md](../CONTRACTS.md).

Topic naming is `<domain>.<name>.v<N>` so the version is part of the contract; a
breaking schema change becomes a new `.v(N+1)` topic rather than a silent
in-place change.

## Rationale

- **Explicit beats implicit.** Provisioning topics up front makes parallelism and
  disk usage auditable instead of inheriting whatever the broker defaulted to on
  first produce.
- **Retention follows role.** The firehose cannot keep 7 days of sub-second ticks
  on a laptop; the audit topics must outlive a same-day reconciliation. One
  policy per topic is the only correct answer.
- **Partition counts follow fan-in.** The price topic gets 6 partitions to spread
  the multi-symbol tick stream; the lower-rate feature/execution topics get 3.
- **Versioned names protect consumers.** Embedding `.vN` lets a producer ship a
  new schema on a new topic while existing consumers keep reading the old one.

## Consequences

**Easier.** Every service connects to a known, bounded, parallelism-ready topic
set; the local disk cannot be filled by the price firehose; re-running the
provisioner is safe.

**Harder.** Adding or re-sizing a topic is a deliberate edit to
`provision-topics.sh` plus this ADR and `CONTRACTS.md` — partition count in
particular cannot be lowered in place. That friction is intentional: the topology
is a contract.

## Alternatives Considered

- **Broker auto-create defaults.** Rejected — single partition and unbounded
  retention are wrong for both the firehose and the audit topics.
- **Uniform retention across all topics.** Rejected — no single value fits both a
  6-hour firehose and a 7-day audit trail.
- **Unversioned topic names.** Rejected — makes schema evolution a breaking,
  coordinated change instead of an additive one.
