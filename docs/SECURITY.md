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

- Every port `docker-compose.yml` publishes is **bound to `127.0.0.1`**, so
  none is reachable from another machine on the network, and CI's validate job
  fails any change that publishes a port on another address. Until 2026-11-04
  this document claimed the same while 20 of the 24 published ports used the
  short `HOST:CONTAINER` form, which Docker binds to every interface; that
  exposed both Postgres databases, Grafana, the huginn and sleipnir control
  planes, muninn's ingest endpoints and the research gateway to the local
  network.
- The two Postgres databases publish **no host port at all**; only their own
  service uses each one. For a shell, use `docker compose exec postgres-huginn
  psql -U postgres huginn` or `docker compose exec postgres-muninn psql -U
  muninn muninn`.
- Loopback binding protects the published ports only. On macOS, also turn on
  the firewall (System Settings, Network, Firewall), which only the owner can
  do, so that anything else on the host listening on every interface is not
  open to the network either.
- The optional console dev server (`console/serve.py`, port 8090) is not part
  of the compose stack and currently listens on every interface. It proxies to
  the loopback services, including research run submission, so it re-exposes
  them while it runs; run it only on a trusted network until it binds to
  loopback.
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

- Real secrets live only in an untracked `.env`; the repo ships `.env.example`
  with placeholders. `.gitignore` excludes `.env`, every other `.env.*` file
  and `secrets/` (it did not until 2026-11-04).
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
