#!/usr/bin/env bash
# Compare a copy of MinIO's data folder with its source, file for file.
#
# Usage: scripts/verify-minio-copy.sh SOURCE COPY
#
# SOURCE and COPY are each a Docker volume name or an absolute host path, as
# `docker run -v` accepts them. Both are mounted read-only, so the check never
# changes either side. Run it with MinIO stopped: a running MinIO rewrites files
# under its data folder, and the two sides would differ for that reason alone.
#
# For each side it prints the objects in every bucket (MinIO's single-drive
# layout keeps one xl.meta per object), the number of files and their total size
# in bytes. It then compares the SHA-256 of every file by path, and exits 0 only
# when both sides hold exactly the same files with the same contents. A copy onto
# a case-insensitive filesystem that merged two keys differing only in case shows
# up here as a mismatch. See docs/RUNBOOK.md, section 6.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 SOURCE COPY  (each a Docker volume name or an absolute path)" >&2
  exit 2
fi

# Pinned, and already used by this stack's tooling; busybox provides find,
# stat, sha256sum, sort and cmp.
IMAGE=alpine:3.21

docker run --rm -i -v "$1":/from:ro -v "$2":/to:ro "$IMAGE" sh -eu <<'IN_CONTAINER'
summarise() {
  echo "$1"
  for bucket in "$1"/*/; do
    [ -d "$bucket" ] || continue
    printf '  %-22s %10s objects\n' "$(basename "$bucket")" \
      "$(find "$bucket" -type f -name xl.meta | wc -l)"
  done
  files=$(find "$1" -type f | wc -l)
  bytes=$(find "$1" -type f -exec stat -c %s {} + | awk '{ s += $1 } END { print s + 0 }')
  echo "  all files: $files files, $bytes bytes"
}

manifest() {
  (cd "$1" && find . -type f -exec sha256sum {} + | sort -k 2)
}

summarise /from
summarise /to
manifest /from > /tmp/from.sha256
manifest /to > /tmp/to.sha256

if cmp -s /tmp/from.sha256 /tmp/to.sha256; then
  echo "VERIFIED: $(wc -l < /tmp/from.sha256) files identical in path, size and SHA-256"
else
  echo "MISMATCH: the copy differs from the source (< source, > copy):"
  diff /tmp/from.sha256 /tmp/to.sha256 | head -n 20 || true
  exit 1
fi
IN_CONTAINER
