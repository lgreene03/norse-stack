#!/bin/sh
# Copy recorded BTC-USDT book snapshots for both venues out of the stack's object
# store, for offline research such as obi_decile_test.py.
#
#   scripts/research/fetch-recorded-books.sh OUT_DIR YEAR MONTH DAY [DAY ...]
#   scripts/research/fetch-recorded-books.sh /tmp/books 2026 10 28 29 30
#
# Reads the muninn-raw bucket that muninn's raw recorder writes. Credentials come from
# the running MinIO container and are never printed. Needs the stack running.
set -eu

if [ "$#" -lt 4 ]; then
  echo "usage: $0 OUT_DIR YEAR MONTH DAY [DAY ...]" >&2
  exit 2
fi
OUT=$1
YEAR=$2
MONTH=$3
shift 3

mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
U=$(docker exec norse-stack-minio-1 printenv MINIO_ROOT_USER)
P=$(docker exec norse-stack-minio-1 printenv MINIO_ROOT_PASSWORD)

for src in binance.spot.v1 okx.spot.v1; do
  for day in "$@"; do
    docker run --rm --network norse-stack_default \
      -e "MC_HOST_s=http://$U:$P@minio:9000" \
      -v "$OUT:/out" \
      minio/mc:RELEASE.2025-01-17T23-25-50Z \
      mirror --quiet \
        "s/muninn-raw/events.book.snapshot/source=$src/instrument=BTC-USDT/year=$YEAR/month=$MONTH/day=$day" \
        "/out/$src/day=$day" >/dev/null
    echo "$src $YEAR-$MONTH-$day copied"
  done
done
