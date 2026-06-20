#!/usr/bin/env bash
# Capture demo assets for the Norse Stack:
#   1. an asciinema cast of the end-to-end smoke test (terminal recording), and
#   2. PNG snapshots of the Grafana dashboard via the render endpoint.
#
# Nothing here uploads anything or records video — it produces local artifacts
# under docs/demo/ that you can review and commit.
#
# Usage:
#   ./scripts/record-demo.sh            # do both (asciinema cast + grafana PNGs)
#   ./scripts/record-demo.sh --cast     # only the asciinema cast
#   ./scripts/record-demo.sh --grafana  # only the Grafana PNGs
#
# Prereqs:
#   - The stack must be running for the Grafana capture: `make up` / `docker compose up -d --build`
#   - asciinema for the cast:  brew install asciinema   (https://asciinema.org)
#   - Grafana PNG capture needs the grafana-image-renderer plugin (see notes below).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
DEMO_DIR="$ROOT_DIR/docs/demo"
IMG_DIR="$DEMO_DIR/images"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3001}"
GRAFANA_AUTH="${GRAFANA_AUTH:-admin:norse}"
DASH_UID="${DASH_UID:-norse-stack-main}"

mkdir -p "$IMG_DIR"

DO_CAST=true
DO_GRAFANA=true
case "${1:-}" in
  --cast)    DO_GRAFANA=false ;;
  --grafana) DO_CAST=false ;;
  "")        ;;
  *) echo "Unknown arg: $1"; exit 2 ;;
esac

record_cast() {
  local out="$DEMO_DIR/smoke.cast"
  if ! command -v asciinema >/dev/null 2>&1; then
    echo "⚠ asciinema not installed — skipping terminal cast."
    echo "  Install it (brew install asciinema) then re-run: ./scripts/record-demo.sh --cast"
    echo "  To play a cast:   asciinema play docs/demo/smoke.cast"
    echo "  To share a cast:  asciinema upload docs/demo/smoke.cast"
    return 0
  fi
  echo "→ Recording smoke test to $out (Ctrl-D / 'exit' when it finishes)..."
  asciinema rec --overwrite --command "$SCRIPT_DIR/smoke.sh" "$out"
  echo "✓ Cast written: $out"
  echo "  Play:    asciinema play $out"
  echo "  Upload:  asciinema upload $out   # prints a shareable asciinema.org link"
}

capture_grafana() {
  echo "→ Capturing Grafana dashboard PNGs from $GRAFANA_URL (uid=$DASH_UID)..."
  local out="$IMG_DIR/grafana-dashboard.png"
  local code
  code=$(curl -s -u "$GRAFANA_AUTH" -o "$out" -w '%{http_code}' \
    "$GRAFANA_URL/render/d/$DASH_UID/?width=1600&height=900&from=now-6h&to=now&kiosk&theme=dark" || echo "000")

  if [ "$code" != "200" ]; then
    echo "⚠ Grafana render returned HTTP $code. Is the stack up? (make up)"
    rm -f "$out"
    return 0
  fi

  # The render endpoint replies 200 even when the image-renderer plugin is
  # missing — it returns a small "No image renderer available" placeholder.
  # Detect that by size and warn instead of committing a misleading error image.
  local size
  size=$(wc -c < "$out" | tr -d ' ')
  if [ "$size" -lt 20000 ]; then
    echo "⚠ Render produced a tiny image (${size} bytes) — the grafana-image-renderer"
    echo "  plugin is probably NOT installed, so this is the 'No image renderer"
    echo "  available' placeholder, not your dashboard. Removing it."
    echo "  To enable real PNG capture, add the renderer to docker-compose.yml:"
    echo "      grafana:"
    echo "        environment:"
    echo "          GF_INSTALL_PLUGINS: grafana-image-renderer"
    echo "  (or run the separate grafana/grafana-image-renderer container and set"
    echo "   GF_RENDERING_SERVER_URL / GF_RENDERING_CALLBACK_URL). Until then, grab"
    echo "  a manual screenshot of $GRAFANA_URL/d/$DASH_UID and save it to:"
    echo "      $out"
    rm -f "$out"
    return 0
  fi

  echo "✓ Dashboard PNG written: $out (${size} bytes)"
}

$DO_CAST && record_cast
$DO_GRAFANA && capture_grafana

echo ""
echo "Demo assets live under: $DEMO_DIR"
echo "See docs/demo/README.md for how to use and embed them."
