#!/usr/bin/env bash
# Compare a copy of MinIO's data folder with its source, file for file.
#
# Usage: scripts/verify-minio-copy.sh SOURCE COPY
#
# SOURCE and COPY are each an existing Docker volume name or an existing absolute
# host path. Both are checked BEFORE anything is mounted: `docker run -v NAME:`
# silently creates a missing volume and `-v /path:` a missing folder, so a mistyped
# side would otherwise be mounted empty, and an empty source compared with an empty
# copy "verifies" with nothing copied. Both sides are then mounted read-only, so the
# check never changes either. Run it with MinIO stopped: a running MinIO rewrites
# files under its data folder, and the two sides would differ for that reason alone.
#
# It refuses a side that holds no files or has no muninn-raw bucket. Otherwise it
# prints, for each side, the objects in every bucket (MinIO's single-drive layout
# keeps one xl.meta per object), the number of files and their total size in bytes,
# then compares every file byte for byte in both directions. File names are passed
# NUL-delimited throughout, so no name can split or merge entries. A copy onto a
# case-insensitive filesystem that merged two keys differing only in case shows up
# as a mismatch. See docs/RUNBOOK.md, section 6.
#
# Exit status: 0 VERIFIED, 1 MISMATCH, 2 REFUSED (bad arguments, a side missing,
# empty or without muninn-raw).
set -euo pipefail

# Pinned by digest: alpine 3.21 as pulled on 2026-11-10. The stack itself does not
# use alpine, so the first run may pull it. BusyBox provides find -print0, xargs -0,
# stat, cmp, tr and awk, and its sh supports pipefail.
IMAGE=alpine:3.21@sha256:c3f8e73fdb79deaebaa2037150150191b9dcbfba68b4a46d70103204c53f4709

refuse() {
  echo "REFUSED: $*" >&2
  exit 2
}

# Prints the --mount argument for one side, or refuses if that side does not exist.
# --mount, unlike -v, never creates a missing bind source.
mount_for() {
  local label=$1 side=$2 target=$3
  if [[ $side == *,* || $side == *\"* ]]; then
    refuse "$label '$side' contains a comma or a double quote, which --mount cannot take"
  elif [[ $side == /* ]]; then
    [[ -d $side ]] || refuse "$label folder '$side' does not exist"
    printf 'type=bind,source=%s,target=%s,readonly' "$side" "$target"
  elif [[ -n $side && $side != */* && $side != .* ]]; then
    docker volume inspect "$side" > /dev/null 2>&1 || refuse "$label volume '$side' does not exist"
    printf 'type=volume,source=%s,target=%s,readonly' "$side" "$target"
  else
    refuse "$label '$side' is neither an absolute path nor a Docker volume name"
  fi
}

if [ "$#" -ne 2 ]; then
  echo "usage: $0 SOURCE COPY  (each an existing Docker volume name or absolute path)" >&2
  exit 2
fi
if [ "$1" = "$2" ]; then
  refuse "SOURCE and COPY are the same: '$1'"
fi

source_mount=$(mount_for SOURCE "$1" /from)
copy_mount=$(mount_for COPY "$2" /to)

docker run --rm -i --network none --mount "$source_mount" --mount "$copy_mount" "$IMAGE" sh -s <<'IN_CONTAINER'
set -eu
set -o pipefail

files() { find "$1" -type f -print0 | tr -cd '\000' | wc -c; }
bytes() { find "$1" -type f -print0 | xargs -0 -r stat -c %s | awk '{ s += $1 } END { print s + 0 }'; }
objects() { find "$1" -type f -name xl.meta -print0 | tr -cd '\000' | wc -c; }

refuse() {
  echo "REFUSED: $*"
  exit 2
}

summarise() {
  echo "$1 ($2)"
  for bucket in "$1"/*/; do
    [ -d "$bucket" ] || continue
    n=$(objects "$bucket")
    printf '  %-22s %10s objects\n' "$(basename "$bucket")" "$n"
  done
  echo "  all files: $3 files, $4 bytes"
}

from_files=$(files /from)
to_files=$(files /to)
[ "$from_files" -gt 0 ] || refuse "the source holds no files: wrong volume or folder?"
[ "$to_files" -gt 0 ] || refuse "the copy holds no files: nothing was copied?"
[ -d /from/muninn-raw ] || refuse "the source has no muninn-raw bucket: not the recorder's MinIO data"
[ -d /to/muninn-raw ] || refuse "the copy has no muninn-raw bucket: not the recorder's MinIO data"

from_bytes=$(bytes /from)
to_bytes=$(bytes /to)
summarise /from source "$from_files" "$from_bytes"
summarise /to copy "$to_files" "$to_bytes"

if [ "$from_files" != "$to_files" ] || [ "$from_bytes" != "$to_bytes" ]; then
  echo "MISMATCH: file count or total size differs"
  exit 1
fi

# Every source file has a byte-identical file at the same path in the copy.
# exit 255 makes xargs stop at the first difference.
if ! (cd /from && find . -type f -print0) | xargs -0 -r sh -c '
    for f; do
      cmp -s "/from/$f" "/to/$f" || { printf "MISMATCH: differs or missing in the copy: %s\n" "$f"; exit 255; }
    done' check; then
  echo "MISMATCH: the copy is not identical to the source"
  exit 1
fi
# And the copy holds no file the source lacks.
if ! (cd /to && find . -type f -print0) | xargs -0 -r sh -c '
    for f; do
      [ -f "/from/$f" ] || { printf "MISMATCH: only in the copy: %s\n" "$f"; exit 255; }
    done' check; then
  echo "MISMATCH: the copy holds files the source does not"
  exit 1
fi

echo "VERIFIED: $from_files files, $from_bytes bytes, identical byte for byte in both directions"
IN_CONTAINER
