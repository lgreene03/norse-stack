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
| Tear down (wipe volumes) | `docker compose down -v` (keeps the recordings in `MINIO_DATA_DIR`, §6) |
| Where the recordings are | `MINIO_DATA_DIR`, default `./data/minio` (§6) |
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

## 6. Recordings on disk: MinIO's data folder

MinIO holds every raw trade and book snapshot that muninn's recorder keeps
(bucket `muninn-raw`), with `muninn-checkpoints` and `muninn-warehouse`. This is
the history no backtest can get back once Kafka has expired it. MinIO's `/data` is
a bind mount of the host folder named by `MINIO_DATA_DIR`, by default
`./data/minio` inside this checkout, which git ignores. Until 2026-11-10 it was an
anonymous volume declared by the MinIO image: `docker compose down -v` and
`make down` deleted it, and a plain `down` followed by `up` left it orphaned
behind a new, empty one. Neither command touches the folder now; to wipe the
recordings, stop MinIO and delete the folder yourself.

On the owner's Mac the git-ignored `.env` points it at the external drive, for
example:

```bash
MINIO_DATA_DIR="/Volumes/Mac 4T/norse/minio"
MINIO_DATA_DIR_CREATE=false
MUNINN_HOST_PORT=18080
```

`MINIO_DATA_DIR_CREATE=false` makes Docker refuse to start MinIO when the folder
does not exist, instead of creating it empty. Always set it with
`MINIO_DATA_DIR`: without it a mistyped path, or a folder renamed on the drive,
starts MinIO on a new, empty store, `minio-init` recreates the buckets, and the
recorder quietly writes the next recordings there instead of alongside the old
ones.

Two properties of the drive matter. Docker Desktop must share the folder;
`/Volumes` is in its default file sharing list (Settings, Resources, File
sharing). And the drive is APFS, which is case-insensitive by default, unlike the
ext4 inside Docker's VM: two object keys differing only in case would become one
file. muninn's keys do not do that today, and the copy check in §6.2 would show it
as a mismatch if they did.

### 6.1 When the drive is absent

Checked on 2026-11-10 with Docker Desktop (engine 29.1.3, Compose 2.40.3) on
throwaway containers, not on the live stack, except where marked.

- **MinIO cannot start.** With `MINIO_DATA_DIR_CREATE=false` Docker refuses the
  mount (`bind source path does not exist`), and it does so at every start,
  including a restart of a container that was created while the folder was
  there. Without that setting Docker still fails when the whole drive is absent
  (`mkdir /host_mnt/Volumes/...: permission denied`, because `/Volumes` belongs
  to root), but not when only the folder is missing.
- **`docker compose up` stops at MinIO.** `minio-init` waits for a healthy MinIO
  and muninn waits for `minio-init`, so an `up` that has to create or recreate
  muninn leaves it stopped. muninn is the ingester as well as the recorder: while
  it is stopped nothing reaches Kafka, and that time is lost for good. A muninn
  container that is already running is left alone. After a restart of Docker
  itself, the restart policy starts muninn without waiting for MinIO, because
  Docker's restart policy does not read `depends_on` (expected, not tested).
- **While MinIO is unavailable, the recorder holds and retries.** muninn's
  `RawEventRecorder` keeps its buffer, pauses its Kafka partitions, and retries
  the flush indefinitely with exponential backoff from 500 ms up to `max-backoff`
  (30 s). It commits Kafka offsets only after every object in a flush is stored,
  so nothing is skipped. Ingestion carries on into Kafka meanwhile. Once MinIO is
  back the recorder stores the backlog and logs "Object storage recovered,
  recording resumed".
- **Catching up works only within Kafka retention.** On the live stack on
  2026-11-10, `events.trade` keeps 24 hours (set by
  `scripts/provision-topics.sh`) and `events.book.snapshot` keeps 7 days (the
  broker default; the provisioning script does not set it). An outage longer than
  24 hours loses trades: the recorder's committed offset falls off the log,
  `auto.offset.reset=earliest` resumes at the oldest record left, and the span in
  between is a gap that only the daily recording gap audit (G0.15) will show.
- **If the drive goes while MinIO runs,** its reads and writes fail and the
  recorder holds as above. When the drive is back, restart MinIO with
  `docker compose restart minio` rather than trusting the old mount to reattach
  (expected, not tested).
- **On the owner's Mac, Docker Desktop's own disk image is on the same drive**
  (`/Volumes/Mac 4T/Docker/DockerDesktop/Docker.raw`, seen on the live machine on
  2026-11-10). With the drive absent Docker Desktop cannot run any container,
  muninn included, so nothing is ingested and Kafka has nothing to replay. Hold
  and catch up applies only where Docker runs without the drive.

To see the recorder holding, read muninn's `/actuator/prometheus`:
`muninn_recorder_last_flush_age_seconds` climbs without bound while storage fails
(normally it stays under `max-buffer-age`, 60 s), and
`muninn_recorder_flush_failures_total` and `muninn_recorder_buffered_records`
rise.

### 6.2 Moving existing recordings: copy, verify, switch

Copy, never move. Verify before switching. Keep the old volume untouched for a
week. MinIO must be stopped for the copy and the check, because a running MinIO
changes its files as they are read. muninn keeps running throughout, so the stop
costs no data as long as the switch is done well inside the 24 hours of trade
retention. On 2026-11-10 the store held 74,508 objects (2.6 GiB) in 77,102 files.

**Do it in one sitting, straight after pulling this change.** Once the checkout
has the bind mount, any `docker compose up` that touches MinIO recreates it on
`MINIO_DATA_DIR`. Before step 8 that means the default `./data/minio`, created
empty: MinIO would start with no recordings, `minio-init` would recreate the
buckets, and the recorder would write into the empty store while the old volume
sat unused. Run no other `up` until step 8 is done.

Run these from the norse-stack checkout that runs the stack, in one shell.

1. Name the old volume and the new folder. The volume name is the only handle on
   the old data, for the copy, the check and any fall back, so write it down.

   ```bash
   docker inspect norse-stack-minio-1 --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}'
   OLD=<the name printed above>   # ee56a161... on 2026-11-10
   NEW="/Volumes/Mac 4T/norse/minio"
   ```

2. Protect the old volume. Once MinIO is recreated no container uses it, and
   `docker volume prune` or `docker system prune --volumes` would delete it. A
   container that is created but never started keeps it in use:

   ```bash
   docker create --name minio-old-data-keep -v "$OLD":/data:ro alpine:3.21 true
   ```

   `docker container prune` and `docker system prune` remove that holder, so run
   neither for the week the old volume is kept.

3. Create the new folder and check that it is empty (`ls -A` prints nothing):

   ```bash
   mkdir -p "$NEW"
   ls -A "$NEW"
   ```

4. Take a reading of the live store for the record. The credentials expand inside
   the container, never on the host's command line:

   ```bash
   docker compose exec minio sh -c 'MC_HOST_n="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@localhost:9000" mc du --depth 2 n/'
   ```

5. Stop MinIO, and nothing else. Do not use `down`, and do not stop muninn:

   ```bash
   docker compose stop minio
   ```

   The recorder now logs "Could not record raw events; holding them and
   retrying". That is expected.

6. Copy, reading the old volume read-only:

   ```bash
   docker run --rm -v "$OLD":/from:ro -v "$NEW":/to alpine:3.21 cp -a /from/. /to/
   ```

7. Verify. `scripts/verify-minio-copy.sh` mounts both sides read-only, prints the
   objects in each bucket, the file count and the total size in bytes for each,
   and compares the SHA-256 of every file:

   ```bash
   ./scripts/verify-minio-copy.sh "$OLD" "$NEW"
   ```

   Go on only when it prints `VERIFIED`. The object counts should also match
   step 4's reading, plus whatever the recorder stored between steps 4 and 5. On
   a mismatch nothing has changed yet: run `docker compose start minio` to resume
   on the old volume, empty `$NEW`, and find out why.

8. Switch. Add the `MINIO_DATA_DIR` and `MINIO_DATA_DIR_CREATE=false` lines above
   to `.env`, then recreate MinIO alone:

   ```bash
   docker compose up -d --no-deps minio
   docker inspect norse-stack-minio-1 --format '{{json .Mounts}}'
   ```

   The mount must now be `"Type":"bind"` with the drive folder as its source.
   Repeat step 4: every bucket holds at least what step 7 counted. Then confirm
   the recorder has caught up: its log says "Object storage recovered, recording
   resumed" (`docker compose logs --since 15m muninn`), and
   `muninn_recorder_last_flush_age_seconds` is back under 60.

9. After a week without trouble, remove the holder and then the old volume:

   ```bash
   docker rm minio-old-data-keep
   docker volume rm "$OLD"
   ```

   Until then the old volume is the fall back. It cannot be mounted through
   `MINIO_DATA_DIR`, which takes a folder, so falling back means repeating steps 5
   to 8 with `$NEW` set to another empty folder. Anything recorded after the
   switch exists only on the drive.

Steps 2 and 5 to 8 were rehearsed on 2026-11-10, never on the live store, with a
throwaway MinIO of the same release, a throwaway volume, and a folder on the
Mac's internal APFS disk shared with Docker the same way as the drive. The check
printed `VERIFIED`; a MinIO started on the copy listed the same objects and
accepted new writes; and the check printed `MISMATCH` once the copy had changed. A
Compose recreate from an anonymous volume to this bind mount kept the old volume
intact, and the holder container stopped it being listed as dangling, which is
what `docker volume prune` removes.

---

## 7. Escalation / deeper docs

- Architecture & known limitations: `docs/ARCHITECTURE_REVIEW.md`
- Topic contracts & retention: `docs/CONTRACTS.md`
- Getting started / first boot: `docs/GETTING_STARTED.md`
- Security trust boundary: `docs/SECURITY.md`
