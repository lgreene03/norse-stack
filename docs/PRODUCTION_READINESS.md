# Norse Stack — Production Readiness Scorecard

> **Read this first.** The Norse Stack is a **local, single-host crypto trading
> *simulation*** — paper money only, no exchange connectivity, no custody, no real
> funds. It runs as a Docker Compose stack on one machine inside a `127.0.0.1`
> trust boundary (see [SECURITY.md](SECURITY.md)). This scorecard grades the
> project **as the local simulation it is**, and is explicit about what would have
> to change before any real-money deployment. It is an honest engineering
> readiness audit, not a marketing page: every line is marked **DONE** (with the
> file that backs it) or **GAP** (with what is missing).

Legend: **DONE** = implemented and backed by a named file. **PARTIAL** = present
but with a stated limitation. **GAP** = not present (and, where relevant, why
it is out of scope for the simulation).

Scope note: the strategy/execution quant code (`huginn`, `sleipnir`) and the
feature engine (`muninn`) live in sibling repos; lines referencing them link to
those repos. Everything under `services/`, `monitoring/`, `docs/`, `scripts/`,
`.github/`, and `docker-compose.yml` is in *this* repo.

---

## 1. Reliability

| Item | Status | Evidence / Gap |
|------|--------|----------------|
| **Panic / exception recovery on consume loops** | DONE | Python consumers isolate per-record failures rather than crashing the loop — `_decode_or_dlq` in [`services/huginn-ai/huginn_ai.py`](../services/huginn-ai/huginn_ai.py) wraps every decode; analytics paths in [`services/odin/odin.py`](../services/odin/odin.py) and `services/news-sentinel/sentinel.py` catch and continue. Go services (`huginn`, `sleipnir`) run the strategy/execution loops with their own recovery (sibling repos). |
| **At-least-once delivery + idempotent consumers** | DONE | Kafka consumers read from earliest offset and **dedup on `execution_id`** (composite-key fallback when blank) — see dedup logic in [`services/huginn-ai/huginn_ai.py`](../services/huginn-ai/huginn_ai.py) (~L434, L510) and the `*_fills_duplicate_total` counters surfaced in alerts. Redelivery is expected and absorbed, not assumed-away. |
| **Container healthchecks (liveness)** | DONE | Every long-running service in [`docker-compose.yml`](../docker-compose.yml) has a `healthcheck` keyed to **liveness** (`/healthz`), deliberately *not* readiness, with a comment explaining why gating on `/readyz` would flap an idle-but-healthy gateway (see `huginn`/`sleipnir` blocks). `obi-bridge` (no HTTP server) probes its worker via `/proc` cmdline scan. |
| **Readiness vs liveness split** | DONE | `/healthz` (process up) is distinct from `/readyz` (consumer-loop progress) so orchestration and alerting can use the right signal — documented inline in the compose healthchecks and used by the `TargetDown` alert. |
| **Restart policy** | DONE | All services set `restart: unless-stopped`; one-shot init jobs (e.g. `minio-init`) set `restart: "no"`. See [`docker-compose.yml`](../docker-compose.yml). |
| **Resource limits (mem/cpu/pids)** | PARTIAL | Core services (`muninn`, `huginn`, `sleipnir`, several Python services) set `mem_limit`/`cpus`/`pids_limit` in [`docker-compose.yml`](../docker-compose.yml). Not every auxiliary container has them yet — a few (`obi-bridge` has `mem_limit` only) lack cpu/pids caps. |
| **Graceful degradation** | DONE | DLQ producer is **best-effort**: if it can't be built the service falls back to a counter and keeps consuming (`_make_dlq_producer` returns `None`, [`services/huginn-ai/huginn_ai.py`](../services/huginn-ai/huginn_ai.py) ~L196). Codec/Kafka-connection errors are caught and logged, not fatal. |
| **Startup ordering** | DONE | `depends_on` with `condition: service_healthy` / `service_completed_successfully` gates dependents on broker, DB, and MinIO init — see `muninn`/`huginn` blocks in [`docker-compose.yml`](../docker-compose.yml). |
| **Graceful shutdown / in-flight drain** | PARTIAL | Compose sends SIGTERM and consumers close producers on loop exit (`dlq_producer.close()`), but there is no explicit in-flight-order drain barrier on the Python side; the kill switch (below) halts *new* orders while in-flight fills settle. |

---

## 2. Security

| Item | Status | Evidence / Gap |
|------|--------|----------------|
| **Fail-closed auth on mutating endpoints** | DONE | Control-plane bearer-token middleware **fails closed**: if the token env var is unset the mutating endpoint returns `503 Control plane locked` rather than executing unauthenticated — documented in [RUNBOOK.md](RUNBOOK.md) §1 and wired via `HUGINN_API_TOKEN` / `SLEIPNIR_ADMIN_TOKEN` in [`docker-compose.yml`](../docker-compose.yml). |
| **Localhost trust boundary (documented & enforced)** | DONE | All externally reachable ports are bound to `127.0.0.1` (Redpanda `19092`, Console `8088`, MinIO `9002/9003`) in [`docker-compose.yml`](../docker-compose.yml); the threat model is written up in [SECURITY.md](SECURITY.md) so the absence of TLS/SASL is a *documented decision*, not an oversight. |
| **Secret scanning (CI + pre-commit)** | DONE | `gitleaks` runs as a dedicated `secret-scan` CI job over **full history** (`fetch-depth: 0`) and **fails the build** on any hard-coded credential — [`.github/workflows/ci.yml`](../.github/workflows/ci.yml); mirrored by [`.pre-commit-config.yaml`](../.pre-commit-config.yaml). |
| **Dependency / CVE scanning (SCA)** | PARTIAL | `pip-audit` runs per-service against each pinned `requirements.txt` on every PR and nightly — [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). It is **`continue-on-error` (non-blocking)** by design so a freshly-disclosed transitive CVE can't wall off unrelated PRs; the nightly run is the triage gate. Making it blocking is the production hardening step. |
| **SAST (static analysis)** | PARTIAL | `ruff` lint gates every Python service and `shellcheck` gates all scripts in CI; Go services run `golangci-lint` (sibling repos). There is **no dedicated security-focused SAST** (e.g. `bandit`, CodeQL, `gosec`) wired in this repo's CI yet — **GAP** for a hardened pipeline. |
| **Non-root containers** | DONE | All Python service images drop to an unprivileged user (`USER appuser` in every `services/*/Dockerfile`); Go/Java images run non-root in their own Dockerfiles (sibling repos). |
| **Container privilege hardening** | PARTIAL | Core services set `security_opt: no-new-privileges:true` and `cap_drop: ALL` in [`docker-compose.yml`](../docker-compose.yml) (`muninn`, `huginn`, `sleipnir`, several Python services). Not yet applied to every auxiliary container (e.g. `obi-bridge`), and **no `read_only` root filesystem** is set anywhere — both are GAPs for a hardened deployment. |
| **Secrets hygiene** | PARTIAL | No secrets in the tree (enforced by gitleaks); all credentials come from `.env` with safe `${VAR:-default}` local-dev fallbacks and a committed [`.env.example`](../.env.example) template. **GAP for real money:** local-dev defaults like `localdev-change-me` and `minioadmin` exist for the sim; a real deployment needs a secrets manager (Vault / SOPS / cloud KMS), not `.env`. |
| **Transport security (TLS / Kafka SASL/mTLS)** | GAP (by design) | No TLS between services and no Kafka SASL/mTLS — an **intentional omission** for the loopback trust boundary, justified in [SECURITY.md](SECURITY.md). Required for any multi-host / real deployment. |
| **CORS posture** | PARTIAL | Read-only analytics APIs ship permissive CORS (`*`) on loopback; tightenable per deployment via `ACCESS_CONTROL_ALLOW_ORIGIN` — noted in [SECURITY.md](SECURITY.md). |

---

## 3. Observability

| Item | Status | Evidence / Gap |
|------|--------|----------------|
| **Prometheus scrape of all services** | DONE | [`monitoring/prometheus.yml`](../monitoring/prometheus.yml) scrapes `huginn`, `sleipnir`, `odin`, `huginn-ai`, and `muninn` (`/actuator/prometheus`). A `validate-monitoring.py` CI step checks the config parses. |
| **Alert rules** | DONE | [`monitoring/alerts/norse-stack.yml`](../monitoring/alerts/norse-stack.yml) defines real rules across pipeline liveness (`HuginnNoFeatures`, `NoFillsExecuted`, `SleipnirNoFills`), fill quality (rejection-burst, duplicate-fill), model/state persistence failures, and `TargetDown` — all keyed to metric names that actually exist on each service. |
| **Alertmanager** | DONE | Prometheus routes to `alertmanager:9093`; [`monitoring/alertmanager.yml`](../monitoring/alertmanager.yml) is mounted and the container is wired in [`docker-compose.yml`](../docker-compose.yml). **PARTIAL caveat:** the default receiver is a stub — point it at a real webhook (Slack/PagerDuty) for a live deployment. |
| **Distributed tracing (Tempo)** | DONE | `tempo` service (Grafana Tempo) is in [`docker-compose.yml`](../docker-compose.yml); `huginn`, `sleipnir`, and `muninn` export OTLP to `tempo:4317` (`OTEL_EXPORTER_OTLP_ENDPOINT`). **PARTIAL caveat:** the Python services are not yet OTLP-instrumented — tracing covers the Go/Java hot path, not the analytics tier. |
| **Dashboards** | DONE | Grafana is provisioned with datasources (Prometheus + Tempo) and the **Norse Stack — Trading Pipeline** board ([`monitoring/grafana/`](../monitoring/grafana/)), auto-loaded via provisioning. |
| **Runbook** | DONE | [RUNBOOK.md](RUNBOOK.md) is the operator entry point: quick orientation, the kill-switch / halt-resume procedure, auth model, and links out to deeper docs. |
| **Log aggregation (Loki/ELK)** | GAP | Logs go to stdout/`docker logs` only; no centralized log store. Acceptable for single-host; a GAP for real ops. |

---

## 4. Data Integrity

| Item | Status | Evidence / Gap |
|------|--------|----------------|
| **Deduplication** | DONE | Consumers dedup on `execution_id` with a composite-key fallback — [`services/huginn-ai/huginn_ai.py`](../services/huginn-ai/huginn_ai.py) (~L434/L510). News-sentinel dedups headlines by title hash (`sentinel.py`). Duplicate-fill counters drive the `*DuplicateFills` alerts. |
| **Poison-message DLQ** | DONE | Undecodable records are isolated, counted, and **republished to a per-topic `.dlq`** (`FEATURES_DLQ_TOPIC`, `FILLS_DLQ_TOPIC`) by `_decode_or_dlq` — [`services/huginn-ai/huginn_ai.py`](../services/huginn-ai/huginn_ai.py) (~L1149). One poison record can't stall the consumer. |
| **Schema + provenance tagging** | DONE | Models carry a stable `feature_schema_hash` and a best-effort git SHA; a persisted model is **rejected on load if its schema hash doesn't match** the current schema — [`services/huginn-ai/huginn_ai.py`](../services/huginn-ai/huginn_ai.py) (`_feature_schema_hash`, ~L260; load-guard ~L881). |
| **Checkpoint / state restore** | DONE | Trained models persist to disk and reload on boot when schema-compatible; Huginn strategy state persists with a `huginn_strategy_state_persist_errors_total` counter and a dedicated alert for persistence failure. |
| **Deterministic replay parity** | DONE | Muninn's feature engine is byte-identical for identical inputs, so backtest and live share one computation path — enforced by [`huginn/internal/backtest/parity_test.go`](https://github.com/lgreene03/huginn/blob/main/internal/backtest/parity_test.go) (sibling repo) and Muninn ADR-0002. |
| **Real backups / PITR** | GAP (by design) | Postgres/SQLite/MinIO volumes are local Docker volumes; **no off-host backups or point-in-time recovery.** Fine for a sim; a real deployment needs managed backups + restore drills. |

---

## 5. Testing

| Item | Status | Evidence / Gap |
|------|--------|----------------|
| **Unit tests** | DONE | `odin`, `obi-bridge`, `huginn-ai` ship pytest suites run in the CI matrix; Go services carry extensive `*_test.go` coverage (sibling repos). See [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) `services` job. |
| **Property-based tests** | DONE | Strategy invariants are property-tested in [`huginn/internal/strategy/property_test.go`](https://github.com/lgreene03/huginn/blob/main/internal/strategy/property_test.go) (sibling repo). |
| **Determinism / parity tests** | DONE | `parity_test.go` (above) pins replay determinism. |
| **Integration / contract tests** | PARTIAL | `services/obi-bridge/tests/test_contract.py` pins the inter-service message contract; a gated **end-to-end smoke** (`scripts/smoke.sh`) boots the full stack. The e2e job is **nightly/manual + `continue-on-error`** because multi-repo builds and registry pulls are flaky in CI — so PR signal stays green, e2e is a separate gate. |
| **CI matrix** | DONE | Per-service matrix in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) (lint always; tests where wired) plus compose-config validation, shellcheck, and monitoring-config validation. |
| **Coverage reporting** | DONE | pytest emits term/xml/json coverage, a self-contained SVG badge, a step-summary line, and uploads to Codecov (non-blocking) — [`.github/workflows/ci.yml`](../.github/workflows/ci.yml). **PARTIAL caveat:** `bragi` and `news-sentinel` are **lint-only** (`has_tests: "false"`) until their owning agents add tests — explicitly tracked in the matrix comments. |

---

## 6. Quant Rigor

| Item | Status | Evidence / Gap |
|------|--------|----------------|
| **Walk-forward validation** | DONE | Anchored expanding-train / sliding-test walk-forward with real IS-train / OOS-apply and IS↔OOS Sharpe-decay reporting — [`huginn/cmd/walkforward`](https://github.com/lgreene03/huginn/blob/main/cmd/walkforward/main.go). The **negative** result (0 of 4 OOS folds profitable) is published honestly in [RESULTS.md](RESULTS.md). |
| **Deflated Sharpe Ratio (DSR) + PBO** | DONE | Bailey & López de Prado DSR and Probability of Backtest Overfitting implemented in [`huginn/internal/metrics/deflated.go`](https://github.com/lgreene03/huginn/blob/main/internal/metrics/deflated.go) (sibling repo) to discount in-sample Sharpe for multiple-testing. |
| **Net-of-cost gating** | DONE | The `CostHurdle` gate only takes entries whose expected edge exceeds `k × round-trip cost` — `huginn/internal/strategy/cost_hurdle.go` (sibling). On the 24h fixture, `k=1` flipped realized PnL from −59 (235 fills) to +1 (32 fills), documented inline in [`docker-compose.yml`](../docker-compose.yml) and in the fee-dominance case study. |
| **Cost breakdown / break-even edge** | DONE | `internal/metrics/cost.go` (`CostBreakdown`, `NetSharpe`) and Odin's `get_analytics()["cost"]` expose fee/slippage drag and break-even edge — [`services/odin/odin.py`](../services/odin/odin.py). |
| **Benchmark (buy-and-hold)** | DONE | Backtests compute a buy-and-hold reference over the same event stream — [`huginn/internal/backtest/benchmark.go`](https://github.com/lgreene03/huginn/blob/main/internal/backtest/benchmark.go) — and report gross vs net + Net Sharpe vs the benchmark. |
| **Honest results reporting** | DONE | [RESULTS.md](RESULTS.md) publishes real backtester/calibrator/walk-forward output with an explicit caveat block (short live runs can't yield a meaningful Sharpe; OBI is ~breakeven net, fee-dominated). The README tearsheet is generated verbatim from it. |
| **Live-money quant validation** | GAP | All numbers are from a **simulated ~24h window**, not live trading. Real deployment needs out-of-sample live paper-trading, regime-coverage across longer history, and slippage measured against real fills — not the sim cost model. |

---

## Known limitations / NOT production for real money

This is a **portfolio simulation**. The following are deliberately out of scope,
and each is the work that a real-money deployment would require:

- **No real exchange adapter.** `EXCHANGE_BACKEND=sim` (Sleipnir) — there is no
  live order routing, custody, or settlement. Real money needs a hardened,
  rate-limit-aware, reconciliation-backed exchange adapter and a separate
  compliance/KYC path.
- **No secrets manager.** Credentials come from `.env` with local-dev defaults
  (`localdev-change-me`, `minioadmin`). Real money needs Vault / cloud KMS / SOPS
  and rotation.
- **No TLS / Kafka SASL / mTLS.** Intentional for the `127.0.0.1` trust boundary
  ([SECURITY.md](SECURITY.md)); mandatory once traffic crosses hosts.
- **Single host, no HA.** Docker Compose on one machine — no Kubernetes,
  no replication, no failover, no autoscaling. A single node is a single point of
  failure.
- **No off-host backups / PITR.** Local Docker volumes only; no restore drills.
- **Non-blocking SCA, no security SAST.** `pip-audit` is advisory; there is no
  `bandit`/CodeQL/`gosec` gate. Both should be blocking for real money.
- **Tracing/limits/hardening are partial.** Python tier isn't OTLP-traced; not
  every container has full `cap_drop`/`read_only`/resource caps.
- **Simulated performance only.** Every quant number is a backtest on a short
  window. The headline result is honestly *negative* under walk-forward — that is
  the point of the rigor, not a launch claim.

For the design rationale behind the simulation boundary, see
[SECURITY.md](SECURITY.md), [ARCHITECTURE.md](ARCHITECTURE.md), and the
sim-only execution boundary in [`docs/adr/0002-sim-only-execution-boundary.md`](adr/0002-sim-only-execution-boundary.md).
