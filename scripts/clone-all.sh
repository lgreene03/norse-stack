#!/usr/bin/env bash
set -euo pipefail

# Clone all Norse Stack repos as siblings of this repo.
# Safe to re-run — skips repos that already exist.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
PARENT_PARENT="$(dirname "$PARENT_DIR")"

REPOS=(muninn huginn sleipnir muninn-py)
ORG="lgreene03"

for repo in "${REPOS[@]}"; do
    target="$PARENT_PARENT/$repo"
    if [ -d "$target" ]; then
        echo "✓ $repo already cloned at $target"
    else
        echo "→ Cloning $repo..."
        git clone "https://github.com/$ORG/$repo.git" "$target"
        echo "✓ $repo cloned"
    fi
done

echo ""
echo "All repos ready. Run 'docker compose up -d --build' from norse-stack/ to boot the full stack."
