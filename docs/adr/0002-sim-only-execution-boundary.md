# 0002. Sim-only execution boundary by default

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Project maintainer
- **Related:** [docker-compose.yml](../../docker-compose.yml) (`sleipnir` service, `EXCHANGE_BACKEND: sim`), [docs/RESULTS.md](../RESULTS.md), Huginn [ADR-0003 dual-mode paper/live executor](https://github.com/lgreene03/huginn/blob/main/docs/adr/0003-dual-mode-paper-live-executor.md), Sleipnir [ADR-0001 pluggable exchange connector](https://github.com/lgreene03/sleipnir/blob/main/docs/adr/0001-pluggable-exchange-connector.md)

## Context

The stack ingests **live** market data (Obi-Bridge reads Binance's public order
books) and runs a real strategy engine. The open question is what happens at the
*last* hop: does an order intent become a real order on a real exchange?

A demo/portfolio stack that placed real orders would (a) require exchange API
keys to even boot, (b) risk real money on code that is explicitly presented as a
learning/reference system, and (c) make results irreproducible and unsafe to run
unattended. But the architecture must still *prove* it can execute, not just
pretend execution does not exist.

## Decision

Execution is a **separate service** (Sleipnir) behind a Kafka boundary, and the
default and documented backend is the **sim exchange** (`EXCHANGE_BACKEND: sim`).
No real order is placed and no exchange credentials are required to run the full
stack.

The boundary is deliberately a real, swappable seam, not a stub:

- Huginn publishes `executions.intents.v1` and consumes `executions.fills.v1`
  exactly as it would in live mode (Huginn [ADR-0003](https://github.com/lgreene03/huginn/blob/main/docs/adr/0003-dual-mode-paper-live-executor.md)).
- Sleipnir applies real rate-limiting, pre-trade risk, and TWAP/VWAP slicing,
  then fills against the sim backend, charging a modeled cost
  (`SIM_TX_COST_BPS: "10"`).
- Switching to a live exchange is a connector swap (Sleipnir
  [ADR-0001](https://github.com/lgreene03/sleipnir/blob/main/docs/adr/0001-pluggable-exchange-connector.md)),
  not a rewrite.

Every performance figure produced by the stack is therefore a **simulated-fill**
figure, and this is stated explicitly wherever numbers appear
([docs/RESULTS.md](../RESULTS.md) carries the caveat block).

## Rationale

- **Safety and reproducibility.** A reference stack must be safe to boot, share,
  and run unattended. Sim-by-default guarantees no accidental real orders and no
  credential prerequisite.
- **Honesty about results.** The hard part of quant infrastructure is not faking
  fills — it is being precise about what is and isn't real. Making "sim" the
  default and labelling it everywhere is the honest position; results are
  framed as pipeline demonstrations, not trading claims.
- **The seam is the interesting part.** Keeping intents/fills on Kafka means the
  sim path and a future live path are byte-for-byte the same upstream; only the
  connector changes. That is what makes the boundary credible rather than a
  toy.

## Consequences

**Easier.** `docker compose up` boots end-to-end with zero secrets; anyone can
reproduce [docs/RESULTS.md](../RESULTS.md). CI and unattended runs are safe.

**Harder.** No number from this stack reflects real fills, queue position, or
adverse selection. Readers must keep the sim boundary in mind — hence the
repeated caveat language. Going live is intentionally a deliberate,
configuration-gated step, not a default.

## Alternatives Considered

- **Live execution by default.** Rejected — unsafe, requires credentials,
  irreproducible, and inappropriate for a reference/learning system.
- **No execution service (strategy stops at "intent").** Rejected — it would hide
  the rate-limiting, risk, and TWAP/VWAP logic that are core to the project and
  would make the eventual live swap unproven.
