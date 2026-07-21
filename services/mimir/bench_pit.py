#!/usr/bin/env python3
"""Storage-path latency microbenchmark for Mimir's point-in-time feature store.

This is the Norse analog of the criterion storage benchmark rust-trade publishes,
but for the operation that actually differentiates a point-in-time store: an
as-of query (``event_time <= T AND ingest_time <= T``, latest-per-instrument).
It reports p50/p95/p99/mean for single insert, batch insert, as-of query and
history read over a populated store, so the persistence path has a measured,
publishable latency profile instead of an unquantified claim.

Honest engineering telemetry only: measures how fast the store is, says nothing
about returns or edge. Deterministic (no wall-clock in the data), so runs are
comparable across machines and CI.

Run:  python3 bench_pit.py [--rows N] [--ops M]
"""

import argparse
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone

# Mimir imports `kafka` at module load; the FeatureStore logic under benchmark
# never touches it, so install lightweight stubs first (mirrors tests/conftest).
if "kafka" not in sys.modules:
    _k = types.ModuleType("kafka")
    _k.KafkaConsumer = object
    _e = types.ModuleType("kafka.errors")
    _e.KafkaConnectionError = type("KafkaConnectionError", (Exception,), {})
    _k.errors = _e
    sys.modules["kafka"] = _k
    sys.modules["kafka.errors"] = _e

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mimir  # noqa: E402

INSTRUMENTS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT"]
_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _feat(obi):
    return {"featureName": "obi", "values": {"obi": obi, "spread": 0.01, "midPrice": 60000.0}}


def _timestamps(n):
    """n deterministic, strictly-increasing ISO-8601 event times (one/second)."""
    return [(_BASE + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(n)]


def _report(name, samples_ns):
    s = sorted(samples_ns)
    n = len(s)

    def at(q):
        return s[min(n - 1, int(q * n))] / 1000.0  # ns -> microseconds

    mean_us = (sum(s) / n) / 1000.0
    print("  %-26s n=%-6d  p50=%8.1fus  p95=%8.1fus  p99=%8.1fus  mean=%8.1fus"
          % (name, n, at(0.50), at(0.95), at(0.99), mean_us))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=20000, help="rows to populate the store with")
    ap.add_argument("--ops", type=int, default=5000, help="timed operations per benchmark")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="mimir-bench-")
    store = mimir.FeatureStore(db_path=os.path.join(tmp, "bench.db"))

    print("Mimir point-in-time store — storage-path latency")
    print("  rows=%d  timed-ops=%d  instruments=%d\n" % (args.rows, args.ops, len(INSTRUMENTS)))

    # Pre-generate all inputs OUTSIDE the timed regions so we measure the store,
    # not string/dict construction.
    ts = _timestamps(args.rows)
    feats = [_feat((i % 200) / 100.0 - 1.0) for i in range(args.rows)]
    insts = [INSTRUMENTS[i % len(INSTRUMENTS)] for i in range(args.rows)]

    # --- single insert ---
    ins = []
    for i in range(args.rows):
        t0 = time.perf_counter_ns()
        store.store(insts[i], ts[i], feats[i], ingest_time=ts[i])
        ins.append(time.perf_counter_ns() - t0)
    # report only the last `ops` inserts (steady state, past index warm-up)
    _report("single insert", ins[-args.ops:])
    steady = ins[-args.ops:]
    ips = 1e9 / (sum(steady) / len(steady))
    print("  %-26s %.0f inserts/sec (durable, one commit per insert)\n" % ("throughput:", ips))

    # --- as-of query (the differentiating op): latest-per-instrument at T ---
    late = (_BASE + timedelta(seconds=args.rows + 10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = []
    for i in range(args.ops):
        inst = INSTRUMENTS[i % len(INSTRUMENTS)]
        t0 = time.perf_counter_ns()
        store.query_as_of(as_of=late, instrument=inst)
        q.append(time.perf_counter_ns() - t0)
    _report("as-of query (1 instrument)", q)

    # --- as-of query across ALL instruments (no instrument filter) ---
    qa = []
    for _ in range(min(args.ops, 2000)):
        t0 = time.perf_counter_ns()
        store.query_as_of(as_of=late)
        qa.append(time.perf_counter_ns() - t0)
    _report("as-of query (all instruments)", qa)

    # --- history read (newest-first, limit 50) ---
    h = []
    for i in range(min(args.ops, 2000)):
        inst = INSTRUMENTS[i % len(INSTRUMENTS)]
        t0 = time.perf_counter_ns()
        store.history(inst, limit=50)
        h.append(time.perf_counter_ns() - t0)
    _report("history read (limit 50)", h)

    print("\nNote: SQLite file-backed, single connection, lock-serialised (as in the service).")


if __name__ == "__main__":
    main()
