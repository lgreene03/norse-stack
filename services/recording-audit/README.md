# recording-audit: the daily recording gap audit (G0.15)

Reads every object muninn's recorder stores in `muninn-raw` and writes, per venue and
UTC day, the minutes covered, the gaps longer than 60 seconds and the bytes recorded,
to a JSON report per day, a CSV index (`daily.csv`) and Prometheus metrics. Coverage
comes from the event timestamps inside the objects, never from file or object times.
Guardrail P3 and the Grafana Recording Health row (G0.13) read it. Operating notes,
the file layout, every metric and the exact definitions are in
[docs/RUNBOOK.md, section 6.3](../../docs/RUNBOOK.md).

Standard library only: a minimal SigV4 client (`s3client.py`) for ListObjectsV2 and
GetObject, the audit itself (`audit.py`), and the scheduler, files, metrics and HTTP
server (`recording_audit.py`).

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `S3_ENDPOINT` | `http://minio:9000` | MinIO's S3 API |
| `S3_BUCKET` | `muninn-raw` | Bucket the recorder writes |
| `S3_ACCESS_KEY_FILE`, `S3_SECRET_KEY_FILE` | unset | Files holding the keys; preferred |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | unset | The keys, when no file is given |
| `AUDIT_SOURCES` | `binance.spot.v1,okx.spot.v1` | Sources audited; the venue is the part before the first dot |
| `AUDIT_INSTRUMENTS` | `BTC-USDT,ETH-USDT,SOL-USDT,XRP-USDT,DOGE-USDT` | Instruments audited |
| `AUDIT_REPORT_DIR` | `/reports` | Where the reports are written |
| `AUDIT_INTERVAL_SECONDS` | `900` | Time between runs |
| `AUDIT_SETTLE_SECONDS` | `120` | How far short of now today's window stops |
| `AUDIT_FINALISE_DELAY_SECONDS` | `600` | How long after midnight a day becomes complete |
| `AUDIT_BACKFILL_DAYS` | `7` | How many past days are kept complete |
| `AUDIT_EARLIEST_DAY` | unset | Never audit before this date |
| `AUDIT_PORT` | `8098` | Port for `/metrics` and `/healthz` |

The streams audited come from configuration, not from what is in the bucket, so a
stream that has stopped entirely still shows, with no minutes covered.

## Test

```bash
python3 -m pytest services/recording-audit/tests -q
```

`tests/test_s3_contract.py` runs against a real MinIO when
`RECORDING_AUDIT_TEST_S3_ENDPOINT` and credential files are set, as CI does with a
throwaway MinIO; locally it skips. Never point it at the live store, which it would
write to.
