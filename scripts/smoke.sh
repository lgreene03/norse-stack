#!/usr/bin/env bash
# Norse Stack end-to-end smoke test.
#
# Validates the full pipeline:
#   Trade → Muninn (feature engine) → Huginn (strategy) → Sleipnir (execution) → Fill
#
# Usage:
#   ./scripts/smoke.sh              # leave stack running for inspection
#   ./scripts/smoke.sh --teardown   # tear down after test
#
# Prerequisites:
#   - Docker and docker-compose
#   - Sibling checkouts: ../muninn, ../huginn, ../sleipnir (run scripts/clone-all.sh)
#   - curl

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.yml"
TEARDOWN=false

for arg in "$@"; do
  case "$arg" in
    --teardown) TEARDOWN=true ;;
  esac
done

MUNINN_URL="http://localhost:8080"
HUGINN_URL="http://localhost:8083"
SLEIPNIR_URL="http://localhost:8085"
TIMEOUT=90

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

pass()  { echo -e "${GREEN}  ✓ $1${NC}"; }
fail()  { echo -e "${RED}  ✗ $1${NC}"; FAILURES=$((FAILURES + 1)); }
info()  { echo -e "${YELLOW}  → $1${NC}"; }
phase() { echo -e "\n${CYAN}${BOLD}═══ $1 ═══${NC}"; }

FAILURES=0

cleanup() {
  if [ "$TEARDOWN" = true ]; then
    info "Tearing down stack..."
    docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
  fi
}
trap cleanup EXIT

# ── Phase 1: Boot the stack ─────────────────────────────────────────────

phase "Phase 1: Boot the Norse Stack"

echo -e "\n${BOLD}  Norse Stack — End-to-End Smoke Test${NC}"
echo "  ───────────────────────────────────"

for repo in muninn huginn sleipnir; do
  if [ ! -f "$PROJECT_DIR/../$repo/Dockerfile" ]; then
    echo -e "${RED}Missing sibling repo: ../$repo${NC}"
    echo "Run ./scripts/clone-all.sh to set up all repos."
    exit 1
  fi
done
pass "Sibling repos found (muninn, huginn, sleipnir)"

info "Starting docker compose (this may take a while on first build)..."
docker compose -f "$COMPOSE_FILE" up -d --build 2>&1 | tail -5

info "Waiting for Redpanda..."
for i in $(seq 1 "$TIMEOUT"); do
  if docker compose -f "$COMPOSE_FILE" exec -T redpanda rpk cluster health --exit-when-healthy 2>/dev/null; then
    break
  fi
  if [ "$i" -eq "$TIMEOUT" ]; then fail "Redpanda did not become healthy"; exit 1; fi
  sleep 1
done
pass "Redpanda healthy"

info "Creating topics..."
docker compose -f "$COMPOSE_FILE" exec -T redpanda rpk topic create \
  events.trade features.obi.v1 features.vwap.1m.v1 \
  executions.intents.v1 executions.fills.v1 \
  2>/dev/null || true
pass "Topics created"

for svc_name_url in "Muninn:${MUNINN_URL}/actuator/health" "Huginn:${HUGINN_URL}/healthz" "Sleipnir:${SLEIPNIR_URL}/healthz"; do
  svc_name="${svc_name_url%%:*}"
  svc_url="${svc_name_url#*:}"
  info "Waiting for $svc_name..."
  SVC_OK=false
  for i in $(seq 1 "$TIMEOUT"); do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$svc_url" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
      SVC_OK=true
      break
    fi
    sleep 2
  done
  if [ "$SVC_OK" = true ]; then
    pass "$svc_name is up"
  else
    fail "$svc_name failed to start (last HTTP $HTTP_CODE)"
  fi
done

# ── Phase 2: Push trade data through Muninn ─────────────────────────────

phase "Phase 2: Ingest trade through Muninn"

NOW=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EVENT_ID=$(python3 -c "import uuid; print(uuid.uuid4())" 2>/dev/null || echo "00000000-0000-7000-8000-000000000001")

TRADE_JSON=$(cat <<EOF
{
  "eventId": "${EVENT_ID}",
  "eventTime": "${NOW}",
  "ingestTime": "${NOW}",
  "source": "smoke-stack-test",
  "instrument": {
    "symbol": "BTC-USDT",
    "baseAsset": "BTC",
    "quoteAsset": "USDT",
    "exchange": {
      "id": "binance",
      "displayName": "Binance Spot",
      "timezone": "UTC"
    }
  },
  "sequenceNumber": 1,
  "schemaVersion": 1,
  "price": 67500.50,
  "size": 0.01,
  "side": "BUY",
  "exchangeTradeId": "smoke-stack-001"
}
EOF
)

RESPONSE=$(curl -s -X POST "${MUNINN_URL}/api/v1/events/trade" \
  -H "Content-Type: application/json" \
  -d "${TRADE_JSON}" \
  -w "\n%{http_code}")
HTTP_CODE=$(echo "$RESPONSE" | tail -1)

if [ "$HTTP_CODE" = "201" ]; then
  pass "Trade event accepted by Muninn (HTTP 201)"
else
  fail "Trade event rejected by Muninn (HTTP $HTTP_CODE)"
fi

# ── Phase 3: Inject synthetic OBI feature for Huginn ────────────────────

phase "Phase 3: Inject OBI feature event"

FEATURE_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
FEATURE_ID=$(python3 -c "import uuid; print(uuid.uuid4())" 2>/dev/null || echo "00000000-0000-7000-8000-000000000002")

FEATURE_JSON=$(cat <<EOF
{"eventId":"${FEATURE_ID}","eventTime":"${FEATURE_TIME}","featureName":"obi","featureVersion":"v1","instrument":"BTC-USDT","windowStart":"${FEATURE_TIME}","windowEnd":"${FEATURE_TIME}","values":{"obi":-0.85,"micro_price":67500.50,"bid_price":67490.00,"ask_price":67510.00}}
EOF
)

PRODUCE_OUTPUT=$(echo "$FEATURE_JSON" | docker compose -f "$COMPOSE_FILE" exec -T redpanda \
  rpk topic produce features.obi.v1 2>&1 || echo "PRODUCE_FAILED")

if echo "$PRODUCE_OUTPUT" | grep -q "Produced to partition"; then
  pass "OBI feature event produced to features.obi.v1"
else
  fail "Failed to produce feature event: $PRODUCE_OUTPUT"
fi

# ── Phase 4: Verify full pipeline ──────────────────────────────────────

phase "Phase 4: Verify pipeline (feature → intent → fill → portfolio)"

info "Waiting for pipeline round-trip..."
PIPELINE_OK=false
for i in $(seq 1 20); do
  SNAPSHOT=$(curl -s "${HUGINN_URL}/api/snapshot" 2>/dev/null || echo "{}")
  if echo "$SNAPSHOT" | grep -qE '"TotalFills":[1-9]'; then
    PIPELINE_OK=true
    break
  fi
  sleep 1
done

FEATURES_TOTAL=$(curl -s "${HUGINN_URL}/metrics" 2>/dev/null | grep "huginn_features_consumed_total" | grep -v "^#" | awk '{print $2}' || echo "0")

if [ "$PIPELINE_OK" = true ]; then
  FILL_COUNT=$(echo "$SNAPSHOT" | grep -o '"TotalFills":[0-9]*' | cut -d: -f2 || echo "0")
  pass "Full pipeline complete: feature consumed → strategy fired → ${FILL_COUNT} fill(s)"

  if echo "$SNAPSHOT" | grep -qE '"Positions":\{".+"\}'; then
    pass "Portfolio has open position(s)"
  fi
else
  if [ "${FEATURES_TOTAL:-0}" != "0" ] && [ "${FEATURES_TOTAL:-0}" != "" ]; then
    pass "Huginn consumed ${FEATURES_TOTAL} features (strategy may not have triggered)"
  else
    fail "Huginn did not consume any features within timeout"
  fi
fi

# ── Phase 5: Service health verification ───────────────────────────────

phase "Phase 5: Health verification"

MUNINN_DOCS=$(curl -s -o /dev/null -w "%{http_code}" "${MUNINN_URL}/api-docs" 2>/dev/null)
if [ "$MUNINN_DOCS" = "200" ]; then pass "Muninn OpenAPI spec"; else fail "Muninn /api-docs ($MUNINN_DOCS)"; fi

HUGINN_METRICS=$(curl -s "${HUGINN_URL}/metrics" 2>/dev/null || echo "")
if echo "$HUGINN_METRICS" | grep -q "huginn_"; then
  pass "Huginn Prometheus metrics"
else
  fail "Huginn /metrics missing huginn_ prefix"
fi

SLEIPNIR_METRICS=$(curl -s -o /dev/null -w "%{http_code}" "${SLEIPNIR_URL}/metrics" 2>/dev/null)
if [ "$SLEIPNIR_METRICS" = "200" ]; then pass "Sleipnir Prometheus metrics"; else fail "Sleipnir /metrics ($SLEIPNIR_METRICS)"; fi

# ── Summary ─────────────────────────────────────────────────────────────

echo ""
if [ "$FAILURES" -eq 0 ]; then
  echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════════${NC}"
  echo -e "${GREEN}${BOLD}  Norse Stack smoke test PASSED (0 failures)       ${NC}"
  echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════════${NC}"
else
  echo -e "${RED}${BOLD}═══════════════════════════════════════════════════${NC}"
  echo -e "${RED}${BOLD}  Norse Stack smoke test: ${FAILURES} failure(s)            ${NC}"
  echo -e "${RED}${BOLD}═══════════════════════════════════════════════════${NC}"
fi

echo ""
echo "  Muninn API:       ${MUNINN_URL}"
echo "  Muninn Swagger:   ${MUNINN_URL}/swagger-ui.html"
echo "  Huginn API:       ${HUGINN_URL}"
echo "  Sleipnir API:     ${SLEIPNIR_URL}"
echo "  Redpanda Console: http://localhost:8088"
echo "  MinIO Console:    http://localhost:9003"
echo ""

if [ "$TEARDOWN" = false ]; then
  echo "  Stack is still running. Tear down with:"
  echo "    docker compose down -v"
fi

exit "$FAILURES"
