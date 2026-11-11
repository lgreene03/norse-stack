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
| Recording gap audit | `RECORDING_AUDIT_DIR`, default `./data/recording-audit`; metrics on http://127.0.0.1:8098/metrics (§6.3) |
| Notification when recording stops | `make heartbeat-install` / `make heartbeat-uninstall` (macOS, §6.4) |
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
`./data/minio` inside this checkout, which git ignores.

Until 2026-11-10 `/data` was an anonymous volume declared by the MinIO image.
Three commands run `docker compose down -v` and so deleted it, with every
recording in it: `docker compose down -v` itself, `make down`, and
`./scripts/smoke.sh --teardown`. A plain `down` followed by `up` left it orphaned
behind a new, empty one. None of them touches the folder now; to wipe the
recordings, stop MinIO and delete the folder yourself. Redpanda's data, which
holds the Kafka backlog the recorder catches up from, is still an anonymous
volume, so those three commands still delete that.

On the owner's Mac the git-ignored `.env` holds exactly these three lines and
nothing else:

```bash
MINIO_DATA_DIR="/Volumes/Mac 4T/norse/minio"
MINIO_DATA_DIR_CREATE=false
MUNINN_HOST_PORT=18080
```

Write those lines by hand. Never create `.env` by copying `.env.example`: its
placeholder credentials would replace the defaults the running stack was set up
with. MinIO would get a new root user, which muninn also signs in with, and the
Postgres passwords, which only take effect when a database is first created,
would no longer match the existing databases, so muninn and huginn could not log
in. On 2026-11-10 the live stack ran with no `.env` at all, on the compose
defaults, with `MUNINN_HOST_PORT=18080` taken from the shell. Rotating those
default credentials is a separate follow-up.

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
  recorder holds as above. When the drive is back, restart MinIO alone with
  `docker compose restart --no-deps minio` rather than trusting the old mount to
  reattach (that it does not reattach by itself is expected, not tested). If
  MinIO is not running at all, for example because the drive was absent when
  Docker started, start it with `docker compose up -d --no-deps minio`. Use
  `--no-deps` with either: on a throwaway project with the same dependency chain
  (minio, minio-init, muninn), neither `restart --no-deps minio` nor
  `up -d --no-deps minio` restarted or recreated the muninn stand-in.
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

The order is chosen so that an early `docker compose up` fails loudly instead of
starting MinIO empty. `.env` names the final folder before this change is
pulled, but the copy goes into a staging folder that is renamed to the final
name only at the switch. Until then the final folder does not exist and creation
is disabled, so any `up` that tries to recreate MinIO on the new mount is refused
(`bind source path does not exist`) and the running MinIO stays on the old
volume. Checked on a throwaway project with the same dependency chain: the
refused recreate left the old container running on its old mount, and the muninn
stand-in was not restarted.

Run every step from the norse-stack checkout that runs the stack, in one shell,
with the drive mounted.

1. **Before pulling this change**, name the old volume, the staging folder and the
   final folder. `OLD` is read from the running container, never typed, and the
   second command confirms it names a volume that exists; if it prints an error,
   stop.

   ```bash
   OLD=$(docker inspect norse-stack-minio-1 --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')
   docker volume inspect --format '{{.Name}}' "$OLD"
   STAGING="/Volumes/Mac 4T/norse/minio.staging"
   FINAL="/Volumes/Mac 4T/norse/minio"
   ```

2. Keep the old volume in use, so that no prune can delete it once MinIO stops
   using it. A container that is created but never started does that. If the
   first command already prints the same name as `$OLD`, the holder exists (it
   was created on 2026-11-10) and the second is not needed:

   ```bash
   docker container inspect minio-old-data-keep --format '{{range .Mounts}}{{.Name}}{{end}}'
   docker create --name minio-old-data-keep -v "$OLD":/data:ro alpine:3.21@sha256:c3f8e73fdb79deaebaa2037150150191b9dcbfba68b4a46d70103204c53f4709 true
   ```

   `docker container prune` and `docker system prune` remove the holder, so run
   neither for the week the old volume is kept.

3. Write `.env` with exactly the three lines at the start of this section, by
   hand, not from `.env.example`. The compose file before this change does not
   read the first two, and the third matches the running stack.

4. Create the empty staging folder, and check that the final folder does not
   exist yet (`ls -A` prints nothing; `ls -d` must fail with "No such file or
   directory"):

   ```bash
   mkdir -p "$STAGING"
   ls -A "$STAGING"
   ls -d "$FINAL"
   ```

5. Pull this change into the checkout. From here until step 10, an `up` that
   touches MinIO is refused, as described above; run none anyway.

6. Take a reading of the live store for the record. The credentials expand inside
   the container, never on the host's command line:

   ```bash
   docker compose exec minio sh -c 'MC_HOST_n="http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@localhost:9000" mc du --depth 2 n/'
   ```

7. Stop MinIO, and nothing else. Do not use `down`, and do not stop muninn:

   ```bash
   docker compose stop minio
   ```

   The recorder now logs "Could not record raw events; holding them and
   retrying". That is expected.

8. Copy into the staging folder, reading the old volume read-only. `--mount`, unlike
   `-v`, never creates a missing source:

   ```bash
   docker run --rm --network none \
     --mount "type=volume,source=$OLD,target=/from,readonly" \
     --mount "type=bind,source=$STAGING,target=/to" \
     alpine:3.21@sha256:c3f8e73fdb79deaebaa2037150150191b9dcbfba68b4a46d70103204c53f4709 cp -a /from/. /to/
   ```

9. Verify:

   ```bash
   ./scripts/verify-minio-copy.sh "$OLD" "$STAGING"
   ```

   The script checks that both sides exist before mounting either, refuses a side
   with no files or no `muninn-raw` bucket (exit 2, `REFUSED`), prints the objects
   in each bucket, the file count and the total bytes for each side, and compares
   every file byte for byte in both directions, with file names passed
   NUL-delimited. Go on only when it exits 0 and prints `VERIFIED`. The object
   counts should also match step 6's reading, plus whatever the recorder stored
   between steps 6 and 7. On `MISMATCH` (exit 1) or `REFUSED`, nothing has changed
   yet: run `docker compose start minio` to resume on the old volume, empty
   `$STAGING`, and find out why.

10. Switch. Rename the staging folder to the final name, then recreate MinIO alone:

    ```bash
    mv "$STAGING" "$FINAL"
    docker compose up -d --no-deps minio
    docker inspect norse-stack-minio-1 --format '{{json .Mounts}}'
    ```

    The rename is on the same drive, so the verified files are not copied again.
    The mount must now be `"Type":"bind"` with the final folder as its source.
    Repeat step 6: every bucket holds at least what step 9 counted. Then confirm
    the recorder has caught up: its log says "Object storage recovered, recording
    resumed" (`docker compose logs --since 15m muninn`), and
    `muninn_recorder_last_flush_age_seconds` is back under 60.

11. After a week without trouble, remove the holder and then the old volume:

    ```bash
    docker rm minio-old-data-keep
    docker volume rm "$OLD"
    ```

    Until then the old volume is the fall back. It cannot be mounted through
    `MINIO_DATA_DIR`, which takes a folder, so falling back means stopping MinIO,
    copying the old volume into a new empty folder and verifying it (steps 7 to
    9), then pointing `MINIO_DATA_DIR` at that folder. Anything recorded after the
    switch exists only in the final folder.

Rehearsed on 2026-11-10, never on the live store, with throwaway MinIOs of the
same release, throwaway volumes, and folders on the Mac's internal APFS disk
shared with Docker the same way as the drive. A copy made as in step 8 verified,
and a MinIO started on it listed the same objects and accepted new writes. A
Compose recreate from an anonymous volume to this bind mount kept the old volume
intact, and the holder container stopped it being listed as dangling, which is
what `docker volume prune` removes. With the new compose file in place and the
final folder missing, `docker compose start minio` (step 9's roll back) restarted
the stopped old container on its anonymous volume. After the security review the check in step 9
was re-run: it refused (exit 2) a missing source volume and a missing copy folder
without creating either, an empty source, an empty copy, a copy with no
`muninn-raw` and a relative path; it printed `MISMATCH` (exit 1) for one copied
file changed without changing its size, whose name contained a newline; and it
printed `VERIFIED` (exit 0) for a good copy of 105 files.

### 6.3 Recording gap audit (G0.15)

The `recording-audit` service measures what the recorder actually kept. For each
venue and UTC day it records the minutes covered, the gaps longer than 60 seconds
and the bytes recorded, to a file and to Prometheus. It works from the event
timestamps inside the stored objects (`eventTime` on every line), never from file
or object times: a recorder that held and then caught up writes old events with new
file times, and a muninn that was down writes nothing at all. It only reads the
store, through MinIO's S3 API.

Every 15 minutes it audits today so far, then every earlier day back to 2026-10-27,
when recording began, that has no complete report yet, newest first. A day becomes
complete ten minutes after it ends (`AUDIT_FINALISE_DELAY_SECONDS`), which gives the
last events time to arrive, and its report is never rewritten after that. Today's
window stops two minutes short of now, rounded down to the minute
(`AUDIT_SETTLE_SECONDS`), so the recorder's once a minute flush is never mistaken
for a gap.

**The files** are in `RECORDING_AUDIT_DIR` on the host, by default
`./data/recording-audit` in the checkout that runs the stack (ignored by git):

- `YYYY-MM-DD.json`, one per UTC day: per venue and per stream, the minutes covered,
  every gap with its start, end and length, bytes, records and objects. `complete`
  says whether the day is final, and the definitions are repeated in the file.
- `daily.csv`: one row per day and venue, which is what P3 is measured from, with
  `coverage_pct` and `p3_met` (coverage of at least 99.5%).

**The metrics** are at http://127.0.0.1:8098/metrics, scraped by Prometheus as job
`recording-audit`. Every per-day series carries `day="today"` or `day="yesterday"`;
the date itself is not a label, so the series stay bounded, and
`recording_audit_day_start_timestamp_seconds` says which day each one is.

| Metric | Labels | Meaning |
|--------|--------|---------|
| `recording_audit_venue_minutes_covered` | venue, day | Minutes with a trade and a book event from the venue (P3) |
| `recording_audit_venue_coverage_ratio` | venue, day | The same over the minutes audited; P3 needs at least 0.995 |
| `recording_audit_venue_minutes_covered_all_streams` | venue, day | Minutes in which all ten of the venue's streams recorded |
| `recording_audit_venue_gaps` | venue, day | Silences longer than 60 s in which the venue recorded nothing |
| `recording_audit_venue_longest_gap_seconds` | venue, day | The longest of those inside the day |
| `recording_audit_venue_recorded_bytes` | venue, day | Stored bytes filed under the day |
| `recording_audit_venue_unreadable_objects` | venue, day | Objects that would not decompress |
| `recording_audit_stream_minutes_covered`, `_gaps`, `_longest_gap_seconds`, `_recorded_bytes`, `_records` | venue, event_type (`trade` or `book`), instrument, day | The same per stream |
| `recording_audit_window_minutes`, `recording_audit_report_complete` | day | Minutes audited so far (1440 once complete); 1 when final |
| `recording_audit_last_success_timestamp_seconds`, `recording_audit_runs_total{result}`, `recording_audit_last_run_duration_seconds`, `recording_audit_objects_read_total` | | Whether the audit itself is running |

**Definitions.** A stream is one event type (trades or book snapshots) from one venue
for one instrument: two venues, five pairs and two event types make twenty. A
stream's minutes covered are the UTC minutes holding at least one of its events. A
venue's minutes covered follow P3: minutes with at least one trade and at least one
book event from that venue, on any instrument. A gap is a silence longer than 60
seconds between consecutive events, clipped to the day; one that crosses midnight is
listed on both days, and `silence_seconds` gives its whole length. A venue gap is a
silence in which the venue recorded nothing on any stream. Bytes recorded are the
stored (gzip) sizes of the objects filed under the day. Objects are filed by Kafka
arrival hour, not event hour, so a day's audit also reads the hour either side of it.

**Reading it.** Book snapshots arrive several times a second, so a gap on a book
stream, and any venue gap, means the recorder or the venue connection stopped. A
trade stream on a quiet pair can show short gaps that are only a quiet market.

**Operating it.**

```bash
docker compose up -d --no-deps recording-audit   # start or update it alone
docker compose logs --tail=20 recording-audit    # one line per day audited
curl -s http://127.0.0.1:8098/metrics | grep venue_minutes_covered
cat data/recording-audit/daily.csv
```

When MinIO cannot be reached the run fails, `recording_audit_runs_total{result="failure"}`
rises, `recording_audit_last_success_timestamp_seconds` stops moving, and the next run
tries again; the service has no `depends_on`, so it starts even when MinIO cannot. To
audit a day again, delete its JSON file and the next run rebuilds it with its CSV
rows. It reads the store with the same credentials MinIO and muninn take from `.env`,
passed through the environment, never on a command line. A read-only user scoped to
`muninn-raw` would be better (G0.4); mount its keys as files and set
`S3_ACCESS_KEY_FILE` and `S3_SECRET_KEY_FILE`, which take precedence.

### 6.4 Data heartbeat: a notification when recording stops

Alerting is deferred (decision D2), with one exception the owner asked for on
2026-11-11: a notification on the Mac when data stops flowing, because muninn's
ingestion was once silently down for about four months. `scripts/data-heartbeat.sh`
runs from launchd every 15 minutes and at login. It reads MinIO's data folder
directly, not through MinIO, so it still works when MinIO, muninn or Docker is down,
and checks two things:

1. that the folder named by `MINIO_DATA_DIR` in the stack's `.env` exists, which on
   the owner's Mac means "Mac 4T" is mounted;
2. that each of the twenty streams (two venues, five pairs, trades and book
   snapshots) has an object written in the last 15 minutes. The recorder flushes at
   least once a minute, so a healthy stream is never close.

It never lists a folder on the drive. macOS does not let a launchd job read the
contents of a folder on an external drive without a privacy grant (Removable
Volumes), though it may read a folder's metadata. The first install on 2026-11-11
used `ls` and `find`, was refused (`kTCCServiceSystemPolicyRemovableVolumes` for
`/bin/bash`, in the unified log), and reported every stream as unreadable. So each
check names the current and previous UTC hour partitions of each stream from the
clock and reads only their modification times: MinIO keeps every object as a new
folder inside its arrival-hour partition, so that time is when the newest object was
stored. No privacy setting needs changing.

It shows a notification only when the state changes: from OK to FAILING, naming the
failing streams or the missing drive, and from FAILING back to OK. A first run that
finds everything OK shows nothing, and a check that is still failing stays quiet. If
a notification cannot be shown, the old state is kept so the next run tries again.
Nothing leaves the machine: there is no webhook, email or network call of any kind.
It is not a replacement for the gap audit (§6.3), which measures what was lost; the
heartbeat only says that something is being lost now.

**Install, check and remove**, from the checkout that runs the stack:

```bash
make heartbeat-install     # install and load the LaunchAgent; it runs a check at once
make heartbeat-status      # loaded or not, the current state, the last five runs
make heartbeat-uninstall   # unload it and delete everything it installed
```

`make heartbeat-install` copies the script to `~/.local/bin/norse-data-heartbeat`, so
that switching branches in the checkout cannot break it (install again after changing
the script), and writes `~/Library/LaunchAgents/io.github.lgreene03.norse.data-heartbeat.plist`
from the template in `scripts/launchd/`. Each check reads `MINIO_DATA_DIR` from this
checkout's `.env`, parsed and never sourced, so moving the recordings needs no
reinstall. Running the install again replaces both files.

The state is in `~/.local/state/norse/`: `data-heartbeat.state` (OK or FAILING, since
when, and the detail), `data-heartbeat.log` (one line per run, trimmed to the last
2,000) and `data-heartbeat.launchd.log` (anything the script wrote to stderr; normally
empty). With launchctl directly:

```bash
launchctl print gui/$(id -u)/io.github.lgreene03.norse.data-heartbeat     # loaded? last exit code
launchctl kickstart gui/$(id -u)/io.github.lgreene03.norse.data-heartbeat # run a check now
```

A last exit code of 0 means OK and 1 means FAILING. To remove it without make:

```bash
launchctl bootout gui/$(id -u)/io.github.lgreene03.norse.data-heartbeat
rm ~/Library/LaunchAgents/io.github.lgreene03.norse.data-heartbeat.plist
rm ~/.local/bin/norse-data-heartbeat ~/.local/state/norse/data-heartbeat.*
```

**When no notification appears.** macOS shows these notifications as coming from
Script Editor, so they appear only if System Settings, Notifications, Script Editor
allows them. `./scripts/data-heartbeat.sh test-notification` shows one on demand. If
it reports the drive missing while "Mac 4T" is mounted, a future macOS may have
started refusing metadata reads too; the unified log will name the service
(`log show --last 1h --predicate 'subsystem == "com.apple.TCC"'`), and whether to
grant it is the owner's decision.

---

## 7. Escalation / deeper docs

- Architecture & known limitations: `docs/ARCHITECTURE_REVIEW.md`
- Topic contracts & retention: `docs/CONTRACTS.md`
- Getting started / first boot: `docs/GETTING_STARTED.md`
- Security trust boundary: `docs/SECURITY.md`
