# 0001. Redpanda as the local message broker

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Project maintainer
- **Related:** [docker-compose.yml](../../docker-compose.yml) (`redpanda` service), [docs/CONTRACTS.md](../CONTRACTS.md), Muninn [ADR-0003 managed Kafka via MSK](https://github.com/lgreene03/muninn/blob/main/docs/adr/0003-managed-kafka-via-msk.md)

## Context

Every Norse Stack service communicates exclusively over a Kafka-protocol message
bus (see [ADR-0003 topic topology](0003-kafka-topic-topology.md)). The full stack
is meant to run on a single developer machine with `docker compose up`, so the
broker choice for the *local* profile is a stack-level decision: it sets the
resource budget, the operational surface, and what each service connects to.

Candidates for the local broker:

| Option | Pros | Cons |
|---|---|---|
| Apache Kafka + ZooKeeper | Reference implementation, ubiquitous | Two JVM processes (broker + ZK), ~1.5 GB+ RAM, slow start — heavy for a 22-container laptop stack |
| Apache Kafka (KRaft) | No ZooKeeper | Still a JVM broker, still heavy; KRaft tuning is fiddly |
| Redpanda | Single C++ binary, Kafka wire-compatible, sub-second start, low memory (`--memory 512M` here), built-in `rpk` admin | Newer; not the literal Apache codebase |
| Cloud-hosted Kafka | Zero local resource | Defeats the "runs offline on one machine" goal; needs credentials |

## Decision

The local profile uses **Redpanda** (`docker.redpanda.com/redpandadata/redpanda`)
as the single-node broker, capped at `--smp 1 --memory 512M --overprovisioned`
so it co-exists with the other 14 containers. All services connect to it by its
Kafka API; none of them know it is Redpanda rather than Apache Kafka.

The production-reference path stays Apache-Kafka-shaped: Muninn's
[ADR-0003](https://github.com/lgreene03/muninn/blob/main/docs/adr/0003-managed-kafka-via-msk.md)
targets Amazon MSK. Because every service speaks plain Kafka protocol, that swap
is a `bootstrap.servers` change, not a code change.

## Rationale

- **Local-first is a stack principle.** The full stack must boot on one laptop
  without cloud dependencies; a single 512 MB C++ broker leaves room for Muninn's
  JVM, Postgres, MinIO, Grafana, and the Python services.
- **Wire compatibility de-risks the choice.** Redpanda speaks the Kafka protocol,
  so the Go (`segmentio/kafka-go`), Java (Spring Kafka), and Python clients are
  unchanged whether they hit Redpanda locally or MSK in the reference cloud
  profile.
- **`rpk` simplifies provisioning.** Topic creation with explicit partitions and
  retention ([`scripts/provision-topics.sh`](../../scripts/provision-topics.sh))
  uses the bundled `rpk` CLI in the same image — no extra tooling container.
- **Fast feedback loop.** Sub-second broker start keeps `docker compose up` and
  the smoke test responsive.

## Consequences

**Easier.** One small container provides the whole bus; topics are provisioned
idempotently by `topic-init`; the Redpanda Console (`:8088`) gives a UI for
free.

**Harder.** The local broker is single-node — no replication, so it is not a
fidelity test of partition-failure behavior. That is acceptable for a local dev
stack and is explicitly the job of the MSK reference profile.

**Migration.** Local Redpanda → MSK is documented on the Muninn side; the stack's
only coupling is the broker address each service reads from its environment.

## Alternatives Considered

- **Apache Kafka (KRaft).** Rejected for the *local* profile on resource grounds;
  remains the production-reference target via MSK.
- **Cloud-hosted Kafka for local dev.** Rejected — breaks the offline,
  credential-free local experience.
