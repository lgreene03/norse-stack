#!/usr/bin/env bash
# Capture live feature events from Kafka for walk-forward backtesting.
#
# Usage:
#   ./scripts/capture-features.sh                  # capture until Ctrl+C
#   ./scripts/capture-features.sh --duration 1h    # capture for 1 hour
#
# Output: data/features-YYYY-MM-DD.jsonl (one event per line)
# Feed to huginn walk-forward: go run ./cmd/walkforward --data data/features-*.jsonl

set -euo pipefail

TOPIC="${TOPIC:-features.obi.v1}"
BROKERS="${BROKERS:-localhost:19092}"
OUTDIR="data"
OUTFILE="${OUTDIR}/features-$(date +%Y-%m-%d).jsonl"

mkdir -p "$OUTDIR"

echo "Capturing ${TOPIC} → ${OUTFILE}"
echo "Press Ctrl+C to stop."

docker exec norse-stack-redpanda-1 \
  rpk topic consume "$TOPIC" \
  --brokers redpanda:29092 \
  --format '%v\n' \
  "$@" >> "$OUTFILE"

echo "Captured $(wc -l < "$OUTFILE") events to ${OUTFILE}"
