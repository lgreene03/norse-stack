"""recording-audit: the daily recording gap audit (G0.15) as a small scheduled service.

Every AUDIT_INTERVAL_SECONDS it audits today so far and every recent day that has no
complete report yet, writes one JSON report per UTC day plus a CSV index, and serves
the latest numbers as Prometheus metrics. It only reads the object store.

Files, under AUDIT_REPORT_DIR (host: RECORDING_AUDIT_DIR, see docs/RUNBOOK.md §6.3):

* ``YYYY-MM-DD.json``: the full report for a day, per venue and per stream, with
  every gap listed. Rewritten each run while the day is open; never again once
  ``complete`` is true.
* ``daily.csv``: one row per day and venue, the summary P3 is measured from.

Endpoints on AUDIT_PORT: ``/metrics`` (Prometheus text) and ``/healthz``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from audit import EPOCH, EVENT_TYPES, ObjectStore, SummaryCache, audit_day, streams_for
from s3client import ConfigError, S3Client, load_credentials

P3_THRESHOLD = 0.995
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
_REPORT_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")

CSV_COLUMNS = [
    "day",
    "venue",
    "complete",
    "window_minutes",
    "minutes_covered",
    "coverage_pct",
    "p3_met",
    "minutes_with_trades",
    "minutes_with_book",
    "minutes_covered_all_streams",
    "gaps_over_60s",
    "longest_gap_seconds",
    "bytes_recorded",
    "records",
    "unreadable_objects",
    "generated_at",
]


def log(message: str) -> None:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} recording-audit {message}", flush=True)


# ── Configuration ──────────────────────────────────────────────────────────


def _names(value: str, setting: str) -> list[str]:
    names = [v.strip() for v in value.split(",") if v.strip()]
    if not names:
        raise ConfigError(f"{setting} must name at least one value")
    for name in names:
        if not _SAFE.match(name):
            raise ConfigError(f"{setting}: {name!r} is not of the form [A-Za-z0-9._-]+")
    return names


def _int(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = env.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        raise ConfigError(f"{name} must be a whole number") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class Config:
    endpoint: str = "http://minio:9000"
    bucket: str = "muninn-raw"
    region: str = "us-east-1"
    sources: tuple[str, ...] = ("binance.spot.v1", "okx.spot.v1")
    instruments: tuple[str, ...] = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT")
    report_dir: str = "/reports"
    interval_seconds: int = 900
    settle_seconds: int = 120
    finalise_seconds: int = 600
    backfill_days: int = 7
    earliest_day: dt.date | None = None
    port: int = 8098

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        earliest = env.get("AUDIT_EARLIEST_DAY", "").strip()
        try:
            earliest_day = dt.date.fromisoformat(earliest) if earliest else None
        except ValueError:
            raise ConfigError("AUDIT_EARLIEST_DAY must be a date such as 2026-11-10") from None
        return cls(
            endpoint=env.get("S3_ENDPOINT", cls.endpoint).strip(),
            bucket=_names(env.get("S3_BUCKET", cls.bucket), "S3_BUCKET")[0],
            region=env.get("S3_REGION", cls.region).strip() or cls.region,
            sources=tuple(_names(env.get("AUDIT_SOURCES", ",".join(cls.sources)), "AUDIT_SOURCES")),
            instruments=tuple(
                _names(env.get("AUDIT_INSTRUMENTS", ",".join(cls.instruments)), "AUDIT_INSTRUMENTS")
            ),
            report_dir=env.get("AUDIT_REPORT_DIR", cls.report_dir).strip() or cls.report_dir,
            interval_seconds=_int(env, "AUDIT_INTERVAL_SECONDS", cls.interval_seconds, 60),
            settle_seconds=_int(env, "AUDIT_SETTLE_SECONDS", cls.settle_seconds, 0),
            finalise_seconds=_int(env, "AUDIT_FINALISE_DELAY_SECONDS", cls.finalise_seconds, 0),
            backfill_days=_int(env, "AUDIT_BACKFILL_DAYS", cls.backfill_days, 0),
            earliest_day=earliest_day,
            port=_int(env, "AUDIT_PORT", cls.port, 1),
        )


# ── Report files ───────────────────────────────────────────────────────────


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def csv_rows(report: dict) -> list[dict]:
    rows = []
    for venue, v in sorted(report["venues"].items()):
        rows.append(
            {
                "day": report["day"],
                "venue": venue,
                "complete": str(report["complete"]).lower(),
                "window_minutes": report["window"]["minutes"],
                "minutes_covered": v["minutes_covered"],
                "coverage_pct": f"{v['coverage_ratio'] * 100:.3f}",
                "p3_met": str(v["coverage_ratio"] >= P3_THRESHOLD).lower(),
                "minutes_with_trades": v["minutes_with_trades"],
                "minutes_with_book": v["minutes_with_book"],
                "minutes_covered_all_streams": v["minutes_covered_all_streams"],
                "gaps_over_60s": v["gap_count"],
                "longest_gap_seconds": f"{v['longest_gap_seconds']:.3f}",
                "bytes_recorded": v["bytes_recorded"],
                "records": v["records"],
                "unreadable_objects": v["unreadable_objects"],
                "generated_at": report["generated_at"],
            }
        )
    return rows


class ReportStore:
    """The report directory: one JSON file per day and a CSV index of all of them."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, day: dt.date) -> Path:
        return self.dir / f"{day.isoformat()}.json"

    def read(self, day: dt.date) -> dict | None:
        try:
            report = json.loads(self.path(day).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return report if isinstance(report, dict) and report.get("day") == day.isoformat() else None

    def is_complete(self, day: dt.date) -> bool:
        report = self.read(day)
        return bool(report and report.get("complete") is True)

    def write(self, report: dict) -> None:
        day = dt.date.fromisoformat(report["day"])
        if self.is_complete(day):
            return  # a complete report is final
        _atomic_write(self.path(day), json.dumps(report, indent=1, sort_keys=False) + "\n")
        self._update_index(report)

    def _update_index(self, report: dict) -> None:
        index = self.dir / "daily.csv"
        rows: dict[tuple[str, str], dict] = {}
        try:
            with index.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    rows[(row["day"], row["venue"])] = row
        except OSError:
            pass
        rows = {k: r for k, r in rows.items() if k[0] != report["day"]}
        for row in csv_rows(report):
            rows[(row["day"], row["venue"])] = row
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow(rows[key])
        _atomic_write(index, out.getvalue())


# ── Metrics ────────────────────────────────────────────────────────────────


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class _Metrics:
    def __init__(self) -> None:
        self._families: dict[str, tuple[str, str, list[str]]] = {}

    def add(self, name: str, kind: str, help_text: str, labels: dict, value: float) -> None:
        family = self._families.setdefault(name, (kind, help_text, []))
        rendered = ",".join(f'{k}="{_label(str(v))}"' for k, v in labels.items())
        family[2].append(f"{name}{{{rendered}}} {value}" if rendered else f"{name} {value}")

    def render(self) -> str:
        lines = []
        for name, (kind, help_text, samples) in self._families.items():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            lines.extend(samples)
        return "\n".join(lines) + "\n"


def render_metrics(reports: Mapping[str, dict], run: Mapping[str, float]) -> str:
    """Prometheus text for the reports labelled "today" and "yesterday", plus run health.

    Labels are bounded by configuration: two day values, the configured venues, the two
    event types and the configured instruments. Dates are not labels; the day's start
    is a value instead.
    """
    m = _Metrics()
    for day_label, report in reports.items():
        d = {"day": day_label}
        day_start = dt.datetime.fromisoformat(report["day"]).replace(tzinfo=dt.UTC)
        m.add("recording_audit_day_start_timestamp_seconds", "gauge",
              "Start of the UTC day this report covers.", d, int((day_start - EPOCH).total_seconds()))
        m.add("recording_audit_report_complete", "gauge",
              "1 once the day is over and its report is final.", d, int(report["complete"]))
        m.add("recording_audit_window_minutes", "gauge",
              "UTC minutes audited so far in the day (1440 when complete).", d,
              report["window"]["minutes"])
        for venue, v in report["venues"].items():
            vd = {"venue": venue, **d}
            m.add("recording_audit_venue_minutes_covered", "gauge",
                  "Minutes with at least one trade and one book event from the venue (P3).",
                  vd, v["minutes_covered"])
            m.add("recording_audit_venue_coverage_ratio", "gauge",
                  "Venue minutes covered over minutes audited; P3 needs at least 0.995.",
                  vd, v["coverage_ratio"])
            m.add("recording_audit_venue_minutes_covered_all_streams", "gauge",
                  "Minutes in which every configured stream of the venue recorded an event.",
                  vd, v["minutes_covered_all_streams"])
            m.add("recording_audit_venue_gaps", "gauge",
                  "Silences longer than 60 s in which the venue recorded nothing.",
                  vd, v["gap_count"])
            m.add("recording_audit_venue_longest_gap_seconds", "gauge",
                  "Longest venue-wide silence inside the day.", vd, v["longest_gap_seconds"])
            m.add("recording_audit_venue_recorded_bytes", "gauge",
                  "Stored (gzip) bytes of the venue's objects filed under the day.",
                  vd, v["bytes_recorded"])
            m.add("recording_audit_venue_unreadable_objects", "gauge",
                  "Objects that could not be decompressed.", vd, v["unreadable_objects"])
            for s in v["streams"]:
                sd = {"venue": venue, "event_type": EVENT_TYPES[s["event_type"]],
                      "instrument": s["instrument"], **d}
                m.add("recording_audit_stream_minutes_covered", "gauge",
                      "Minutes with at least one event on the stream.", sd, s["minutes_covered"])
                m.add("recording_audit_stream_gaps", "gauge",
                      "Silences longer than 60 s on the stream.", sd, s["gap_count"])
                m.add("recording_audit_stream_longest_gap_seconds", "gauge",
                      "Longest silence on the stream inside the day.", sd,
                      s["longest_gap_seconds"])
                m.add("recording_audit_stream_recorded_bytes", "gauge",
                      "Stored (gzip) bytes of the stream's objects filed under the day.",
                      sd, s["bytes_recorded"])
                m.add("recording_audit_stream_records", "gauge",
                      "Events whose own timestamp falls inside the audited window.",
                      sd, s["records"])
    m.add("recording_audit_last_success_timestamp_seconds", "gauge",
          "When the last audit run completed without error (0 before the first).", {},
          run.get("last_success", 0))
    m.add("recording_audit_last_run_duration_seconds", "gauge",
          "Wall time of the last audit run.", {}, run.get("last_duration", 0))
    m.add("recording_audit_runs_total", "counter", "Audit runs by result.",
          {"result": "success"}, run.get("successes", 0))
    m.add("recording_audit_runs_total", "counter", "Audit runs by result.",
          {"result": "failure"}, run.get("failures", 0))
    m.add("recording_audit_objects_read_total", "counter",
          "Objects fetched and summarised (cached objects are not read again).", {},
          run.get("objects_read", 0))
    return m.render()


# ── The scheduled job ──────────────────────────────────────────────────────


@dataclass
class Auditor:
    config: Config
    store: ObjectStore
    reports: ReportStore
    clock: Callable[[], dt.datetime] = field(default=lambda: dt.datetime.now(dt.UTC))
    cache: SummaryCache = field(default_factory=SummaryCache)

    def __post_init__(self) -> None:
        self.streams = streams_for(self.config.sources, self.config.instruments)
        self._lock = threading.Lock()
        self._latest: dict[str, dict] = {}
        self._run = {"successes": 0, "failures": 0, "objects_read": 0}
        self.last_error = ""

    def days_due(self, today: dt.date) -> list[dt.date]:
        """Today, then each earlier day in the backfill window without a complete report.

        Newest first, so today's and yesterday's figures are fresh even while a long
        backfill (a first start, or a lost report directory) works through older days.
        """
        first = today - dt.timedelta(days=self.config.backfill_days)
        if self.config.earliest_day and self.config.earliest_day > first:
            first = self.config.earliest_day
        days = []
        if not self.config.earliest_day or today >= self.config.earliest_day:
            days.append(today)
        day = today - dt.timedelta(days=1)
        while day >= first:
            if not self.reports.is_complete(day):
                days.append(day)
            day -= dt.timedelta(days=1)
        return days

    def _publish(self, today: dt.date) -> None:
        latest = {}
        for label, day in (("today", today), ("yesterday", today - dt.timedelta(days=1))):
            report = self.reports.read(day)
            if report is not None:
                latest[label] = report
        with self._lock:
            self._latest = latest

    def run_once(self) -> bool:
        started = time.monotonic()
        now = self.clock()
        today = now.date()
        fetched_before = self.cache.fetched
        # Only today's and yesterday's objects stay cached between runs; a backfilled
        # day is audited once, so its summaries are dropped as soon as it is done.
        recent: set[str] = set()
        ok = True
        for day in self.days_due(today):
            is_recent = day >= today - dt.timedelta(days=1)
            try:
                report = audit_day(
                    self.store,
                    self.streams,
                    day,
                    now,
                    cache=self.cache,
                    settle_seconds=self.config.settle_seconds,
                    finalise_seconds=self.config.finalise_seconds,
                    bucket=self.config.bucket,
                    seen=recent if is_recent else set(),
                )
                self.reports.write(report)
                log(summary_line(report))
            except Exception as exc:  # one bad day must not stop the others
                ok = False
                self.last_error = f"{day.isoformat()}: {exc}"
                log(f"audit of {day.isoformat()} failed: {exc}")
                traceback.print_exc(file=sys.stdout)
            if is_recent:
                self._publish(today)
            else:
                self.cache.retain(recent)
        self.cache.retain(recent)
        self._publish(today)
        with self._lock:
            self._run["objects_read"] += self.cache.fetched - fetched_before
            self._run["last_duration"] = round(time.monotonic() - started, 3)
            if ok:
                self._run["successes"] += 1
                self._run["last_success"] = int(time.time())
                self.last_error = ""
            else:
                self._run["failures"] += 1
        return ok

    def metrics(self) -> str:
        with self._lock:
            return render_metrics(dict(self._latest), dict(self._run))

    def health(self) -> dict:
        with self._lock:
            return {
                "status": "ok",
                "last_success": self._run.get("last_success"),
                "last_error": self.last_error or None,
                "cached_objects": len(self.cache),
            }

    def loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.run_once()
            stop.wait(self.config.interval_seconds)


def summary_line(report: dict) -> str:
    parts = [
        f"{report['day']} {'complete' if report['complete'] else 'so far'}"
        f" ({report['window']['minutes']} min)"
    ]
    for venue, v in sorted(report["venues"].items()):
        parts.append(
            f"{venue} {v['minutes_covered']}/{report['window']['minutes']} min covered,"
            f" {v['gap_count']} venue gaps, {v['bytes_recorded']} bytes"
        )
    return "; ".join(parts)


def make_handler(auditor: Auditor) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/metrics":
                body = auditor.metrics().encode()
                self._send(200, "text/plain; version=0.0.4; charset=utf-8", body)
            elif self.path == "/healthz":
                self._send(200, "application/json", json.dumps(auditor.health()).encode())
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found\n")

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            pass  # scrapes every 15 s would drown the audit log

    return Handler


def main() -> int:
    try:
        config = Config.from_env(os.environ)
        credentials = load_credentials(os.environ)
        client = S3Client(config.endpoint, config.bucket, credentials, region=config.region)
    except ConfigError as exc:
        log(f"configuration error: {exc}")
        return 2
    auditor = Auditor(config, client, ReportStore(config.report_dir))
    log(
        f"auditing s3 bucket {config.bucket} at {config.endpoint} for "
        f"{len(auditor.streams)} streams every {config.interval_seconds} s; "
        f"reports in {config.report_dir}; metrics on :{config.port}/metrics"
    )
    stop = threading.Event()
    threading.Thread(target=auditor.loop, args=(stop,), name="audit", daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", config.port), make_handler(auditor))
    try:
        server.serve_forever()
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
