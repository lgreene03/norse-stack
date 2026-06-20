# Norse Stack — Security Model & Trust Boundary

This document records the **trust-boundary assumptions** for the Norse Stack so
that the absence of full TLS and Kafka SASL is understood as a deliberate,
documented decision rather than an oversight (sec-secrets-auth-9).

This is a **design statement, not an implementation task.** Nothing here changes
behaviour; it explains the threat model the current configuration is built for.

---

## What the Norse Stack is

A **single-host, localhost crypto trading simulation**. It runs as a Docker
Compose stack on one machine, trades **paper money only** (no exchange
connectivity, no real funds, no custody), and is intended for local development,
demos, and research.

## Trust boundary

The trust boundary is **the host machine and its loopback interface.** Everything
inside the Compose network is treated as a single trusted domain:

- All externally reachable service ports are **bound to `127.0.0.1`** in
  `docker-compose.yml` (e.g. Redpanda `127.0.0.1:19092`, Redpanda Console
  `127.0.0.1:8088`, MinIO `127.0.0.1:9002/9003`). They are **not** exposed on
  the host's public interfaces. An attacker would already need local access to
  the host to reach them.
- Inter-service traffic (HTTP APIs, Kafka, OTLP) stays on the internal Docker
  network and never crosses an untrusted link.

## Consequences — what is intentionally NOT present

Given that boundary, the following are **deliberate omissions**, not gaps:

- **No TLS between services.** Plaintext HTTP / gRPC / Kafka is acceptable
  because the traffic never leaves loopback + the Docker bridge. Adding TLS here
  would buy no security against the modeled threat while adding cert-rotation
  and ops burden.
- **No Kafka SASL / mTLS on Redpanda.** The broker is reachable only from the
  trusted local network and the `127.0.0.1` external listener. Broker-level
  authn/authz is out of scope for the single-host sim.
- **Permissive CORS (`*`) on the read-only analytics APIs** (odin, bragi,
  huginn-ai, news-sentinel). These expose read-only data on loopback;
  `ACCESS_CONTROL_ALLOW_ORIGIN` can be tightened per deployment (see
  `.env.example`) but defaults open for local dashboards.

## What IS enforced (and why)

The one boundary that matters even on a single host is **mutating control-plane
actions** — anything that can change trading behaviour. These are token-gated:

- **Huginn** mutating endpoints (kill switch `/api/breaker/trigger` and
  `/api/breaker/reset`, config writes) require `Authorization: Bearer
  $HUGINN_API_TOKEN`.
- **Sleipnir** admin endpoints require `Authorization: Bearer
  $SLEIPNIR_ADMIN_TOKEN`.
- Both **fail closed**: when the token env var is unset, the endpoint returns
  `503 Control plane locked` instead of executing unauthenticated. Read-only
  endpoints (snapshots, metrics, analytics) remain open.

Tokens are supplied via `.env` (template: [`.env.example`](../.env.example)) and
referenced by `docker-compose.yml` as `${HUGINN_API_TOKEN}` /
`${SLEIPNIR_ADMIN_TOKEN}`. The committed defaults (`localdev-change-me`) are
placeholders and MUST be overridden for any non-throwaway use.

## Secrets handling

- Real secrets live only in an untracked `.env` (gitignored); the repo ships
  `.env.example` with placeholders.
- CI runs a **gitleaks** secret-scanning job, and a matching **pre-commit hook**
  (`.pre-commit-config.yaml`) blocks committing hard-coded credentials locally.
- Python service dependencies are scanned with **pip-audit** in CI
  (sec-supplychain-1); the Python service containers run as a **non-root user**
  (sec-supplychain-2).

## If the trust boundary changes

Moving any service off the single trusted host — exposing a port beyond
`127.0.0.1`, putting Redpanda on a shared network, or connecting to a real
exchange — **invalidates the assumptions above.** At that point TLS for service
links, SASL/mTLS for Kafka, locked-down CORS, and a secrets manager become
required, not optional.
