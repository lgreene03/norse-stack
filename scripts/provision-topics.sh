#!/usr/bin/env bash
# Provision Norse Stack Kafka topics with explicit partition counts and
# retention/cleanup policies.
#
# Why: Redpanda auto-creates topics on first produce using broker defaults
# (effectively unbounded retention and a single partition). That is fine for a
# first boot but silently lets high-rate topics grow without bound and pins
# every topic to one partition, capping consumer parallelism. This script makes
# topic config explicit and auditable, mirroring the minio-init bucket step.
#
# Idempotent: re-running is safe. `rpk topic create` no-ops on existing topics;
# we then `alter-config` to converge retention/cleanup to the desired values.
#
# Per-topic rationale is documented in docs/CONTRACTS.md.
#
# Usage:
#   ./scripts/provision-topics.sh                 # uses running compose redpanda
#   RPK="rpk" ./scripts/provision-topics.sh       # run inside the redpanda container
#   BROKERS=localhost:19092 ./scripts/provision-topics.sh   # external listener

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"

# rpk invocation: either a bare `rpk` (when run inside the broker container or
# with rpk on PATH) or via `docker compose exec`. Default to exec so the script
# works from the host with only Docker installed.
if [ -n "${RPK:-}" ]; then
  RPK_CMD=("$RPK")
else
  RPK_CMD=(docker compose -f "$COMPOSE_FILE" exec -T redpanda rpk)
fi

# Optional explicit broker (e.g. external listener). When unset, rpk uses the
# container-local default (redpanda:29092 inside the broker).
BROKER_ARGS=()
if [ -n "${BROKERS:-}" ]; then
  BROKER_ARGS=(--brokers "$BROKERS")
fi

rpk_topic() {
  "${RPK_CMD[@]}" topic "${BROKER_ARGS[@]}" "$@"
}

# topic | partitions | cleanup.policy | retention.ms | rationale
#
# Retention sizing assumes the local single-node dev stack. delete-policy
# topics are time-bounded; the portfolio-state-style topics use compaction so
# the latest value per key survives indefinitely.
#
# prices.realtime.v1   high-rate sub-second ticks  -> short retention, more partitions
# features.obi.v1      one event per symbol per poll -> medium retention
# events.trade         raw ingested trades          -> medium retention
# features.vwap.1m.v1  1m windowed feature           -> medium retention
# executions.intents.v1 order intents (audit trail)  -> longer retention
# executions.fills.v1   fills (audit trail)          -> longer retention
provision() {
  local topic="$1" partitions="$2" cleanup="$3" retention_ms="$4"

  echo "  → ${topic} (partitions=${partitions}, cleanup=${cleanup}, retention.ms=${retention_ms})"

  # Create with explicit partitions + config; no-op if it already exists.
  rpk_topic create "$topic" \
    --partitions "$partitions" \
    --replicas 1 \
    --topic-config "cleanup.policy=${cleanup}" \
    --topic-config "retention.ms=${retention_ms}" \
    2>/dev/null || true

  # Converge config for topics that already existed (create is a no-op then).
  rpk_topic alter-config "$topic" \
    --set "cleanup.policy=${cleanup}" \
    --set "retention.ms=${retention_ms}" \
    >/dev/null 2>&1 || true
}

echo "Provisioning Norse Stack Kafka topics..."

# 6h for the firehose price tick topic (high volume, only needed for live exit
# monitoring); 6 partitions to spread the per-symbol fan-in.
provision "prices.realtime.v1"    6  delete   21600000     # 6h

# 24h for feature + raw trade topics — enough for same-day replay/label joins.
provision "features.obi.v1"       3  delete   86400000     # 24h
provision "features.vwap.1m.v1"   3  delete   86400000     # 24h
provision "events.trade"          3  delete   86400000     # 24h

# 7d for execution audit topics — keep the intent/fill trail for reconciliation.
provision "executions.intents.v1" 3  delete   604800000    # 7d
provision "executions.fills.v1"   3  delete   604800000    # 7d

echo "Topic provisioning complete."
echo
rpk_topic list "${BROKER_ARGS[@]}" 2>/dev/null || true
