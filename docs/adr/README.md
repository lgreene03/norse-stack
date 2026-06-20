# Stack-level Architecture Decision Records

These ADRs cover decisions that span the whole Norse Stack — choices that no
single service repo owns because they describe how the services fit together.
Service-internal decisions live in each repo's own `docs/adr/`:

- [Muninn ADRs](https://github.com/lgreene03/muninn/tree/main/docs/adr) (14)
- [Huginn ADRs](https://github.com/lgreene03/huginn/tree/main/docs/adr) (7)
- [Sleipnir ADRs](https://github.com/lgreene03/sleipnir/tree/main/docs/adr) (7)

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-redpanda-as-local-broker.md) | Redpanda as the local message broker | Accepted |
| [0002](0002-sim-only-execution-boundary.md) | Sim-only execution boundary by default | Accepted |
| [0003](0003-kafka-topic-topology.md) | Kafka topic topology | Accepted |
