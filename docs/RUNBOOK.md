# Norse Stack — Operator Runbook

Operator entry point for the Norse Stack — a single-host, localhost crypto
**trading simulation** (paper money only). This is the page to open first during
an incident. It indexes the controls you need and links out to the deeper docs.

For the trust-boundary / threat model (why there is no TLS or Kafka SASL by
design), see [SECURITY.md](SECURITY.md).

---

## 1. Quick orientation

| What | Where |
|------|-------|
| Boot the stack | `docker compose up -d --build` |
| Tear down (keep data) | `docker compose down` |
| Tear down (wipe volumes) | `docker compose down -v` |
| End-to-end smoke test | `./scripts/smoke.sh --teardown` |
| Provision Kafka topics | `./scripts/provision-topics.sh` |
| Grafana | http://localhost:3001 (board: **Norse Stack — Trading Pipeline**) |
| Prometheus | http://localhost:9091 |
| Alertmanager | http://localhost:9093 |
| Redpanda Console | http://localhost:8088 |

Service ports and the full topology live in the repo
[README](../README.md) / [CLAUDE.md](../CLAUDE.md).

All control-plane endpoints below require a bearer token. Tokens are configured
via `.env` (template: [`.env.example`](../.env.example)) — `HUGINN_API_TOKEN`
for Huginn, `SLEIPNIR_ADMIN_TOKEN` for Sleipnir. Auth **fails closed**: if the
token env var is unset, the mutating endpoint returns `503 Control plane locked`
rather than executing unauthenticated.

---

## 2. Kill switch — halt and resume trading

Huginn owns the circuit breaker. Halting stops the strategy from generating new
orders immediately; in-flight fills still settle. Resume re-enables order
generation. Both are `POST`, both require the bearer token.

Set the token once for the shell session (matches the value in your `.env`):

```bash
export TOKEN="$(grep -E '^HUGINN_API_TOKEN=' .env | cut -d= -f2-)"
```

**Halt (kill switch ON):**

```bash
curl -fsS -X POST http://127.0.0.1:8083/api/breaker/trigger \
  -H "Authorization: Bearer $TOKEN"
# -> {"status":"halted","message":"Strategy execution manually halted."}
```

**Resume (kill switch OFF):**

```bash
curl -fsS -X POST http://127.0.0.1:8083/api/breaker/reset \
  -H "Authorization: Bearer $TOKEN"
# -> {"status":"running","message":"Strategy execution manually resumed."}
```

**Check current state** (read-only, no token needed) via the snapshot — look for
`halted` / `halt_reason`:

```bash
curl -fsS http://127.0.0.1:8083/api/snapshot | python3 -m json.tool | grep -E 'halt'
```

Notes:
- A missing/empty `HUGINN_API_TOKEN` returns `503 Control plane locked:
  HUGINN_API_TOKEN not configured` — set the token in `.env` and recreate the
  Huginn container.
- Huginn also **auto-halts** via the feature-staleness watchdog if no feature
  event arrives within its threshold window (surfaces as `halt_reason`). Resume
  with `/api/breaker/reset` once features are flowing again.

---

## 3. Monitoring — Grafana board and alerts

- **Grafana board:** *Norse Stack — Trading Pipeline*
  (`monitoring/grafana/dashboards/norse-stack.json`), auto-provisioned at
  http://localhost:3001. Default login `admin` / `norse` (override with
  `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`). Panels: signal-to-decision
  latency, feature event age, order/fill rates, realized PnL, Sleipnir submit
  latency.
- **Alert rules:** `monitoring/alerts/norse-stack.yml`, evaluated by Prometheus,
  routed through Alertmanager (`monitoring/alertmanager.yml`).

| Alert | Fires when | First response |
|-------|------------|----------------|
| `HuginnNoFeatures` | No features consumed for 5m | See §5 "No features" |
| `NoFillsExecuted` | No Huginn fills for 10m | Check breaker not halted (§2); check Sleipnir |
| `SleipnirNoFills` | Sleipnir filled nothing for 10m | Check Sleipnir health + intents topic |
| `HuginnAiFillsRejectedBurst` / `OdinFillsRejectedBurst` | >20% fills rejected | Inspect malformed/duplicate fill events |
| `HuginnAiDuplicateFills` / `OdinDuplicateFills` | Duplicate fills observed | Check consumer offsets / replay overlap |
| `HuginnAiModelPersistFailures` | Model save failing for 10m | See §5 "Model persistence" |
| `HuginnStatePersistErrors` | Strategy-state save failing | Check Huginn Postgres connectivity |
| `TargetDown` | A Prometheus target is `up == 0` | `docker compose ps`, restart the down service |

---

## 4. Topic provisioning

Redpanda auto-creates topics on first produce using broker defaults (effectively
unbounded retention, single partition). `./scripts/provision-topics.sh` makes
topic config explicit and is **idempotent** (safe to re-run). Run it after a
fresh `up` or after wiping volumes:

```bash
./scripts/provision-topics.sh                       # against running compose redpanda
BROKERS=localhost:19092 ./scripts/provision-topics.sh   # via external listener
```

Topics and their retention (per `docs/CONTRACTS.md`):
`prices.realtime.v1` (6h), `features.obi.v1` / `features.vwap.1m.v1` (24h),
`executions.intents.v1` / `executions.fills.v1` (7d, audit trail).

---

## 5. Common failure responses

**No features flowing (`HuginnNoFeatures`, stale feature age, auto-halt):**
1. `docker compose ps` — is Muninn up and healthy?
2. Confirm topics exist: `./scripts/provision-topics.sh` (idempotent).
3. Check Muninn is producing to `features.obi.v1` (Redpanda Console, port 8088).
4. Once features resume, clear any auto-halt with `/api/breaker/reset` (§2).

**No fills (`NoFillsExecuted` / `SleipnirNoFills`):**
1. Confirm the breaker is not halted (§2 snapshot check).
2. `docker compose ps` for Sleipnir; check `executions.intents.v1` has traffic.
3. Inspect Huginn and Sleipnir logs: `docker compose logs --tail=100 huginn sleipnir`.

**A Prometheus target is down (`TargetDown`):**
1. `docker compose ps` to find the unhealthy/exited container.
2. `docker compose logs --tail=100 <service>`.
3. `docker compose up -d <service>` to recreate it.

**huginn-ai model persistence failing (`HuginnAiModelPersistFailures`):**
- The model is written under `MODEL_DIR` (default `/data/huginn-ai`). The
  container runs as a non-root user that owns that directory; if you mount an
  external volume there, ensure it is writable by uid `10001`. Persistence
  failures are non-fatal (the service keeps serving the in-memory model) but
  the model is lost on restart until the next retrain.

**Muninn checkpoint / restart caveat (READ BEFORE restarting Muninn):**
- Muninn's `CheckpointManager` uses **Java serialization** for checkpoints.
  These are **not cross-language and carry no forward/backward versioning**. A
  checkpoint written by one Muninn build may fail to deserialize after a version
  change, blocking a clean restart-from-checkpoint.
- Safe restart of Muninn after a version bump: do **not** rely on an old
  checkpoint surviving the upgrade. Because every derived state in the stack is
  recomputable from the immutable event log, the recovery path is to **replay
  from the event stream** rather than restore a stale checkpoint. If a restart
  hangs or errors on checkpoint load, clear the incompatible checkpoint and let
  Muninn rebuild from events. See `docs/ARCHITECTURE_REVIEW.md` and the Muninn
  repo's deploy docs for the replay/migration sequence.

---

## 6. Escalation / deeper docs

- Architecture & known limitations: `docs/ARCHITECTURE_REVIEW.md`
- Topic contracts & retention: `docs/CONTRACTS.md`
- Getting started / first boot: `docs/GETTING_STARTED.md`
- Security trust boundary: `docs/SECURITY.md`
