#!/usr/bin/env bash
# Tests for scripts/data-heartbeat.sh against a fixture recordings folder.
#
# No launchd, no real notifications and no network: NORSE_HEARTBEAT_NOTIFY_LOG
# captures what would have been shown. CI runs it on Linux with GNU tools; it also
# runs on macOS with BSD tools and /bin/bash 3.2, which is what launchd uses.
set -euo pipefail
export LC_ALL=C

here=$(cd "$(dirname "$0")" && pwd)
heartbeat="$here/data-heartbeat.sh"
work=$(mktemp -d)
cleanup() {
  chmod -R u+rwx "$work" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

# A space in the path, as on the owner's drive ("Mac 4T").
drive="$work/Mac 4T"
data="$drive/norse/minio"
stack="$work/stack"
env_file="$stack/.env"
notes="$work/notifications"
export NORSE_HEARTBEAT_STATE_DIR="$work/state"
export NORSE_HEARTBEAT_NOTIFY_LOG="$notes"
mkdir -p "$stack"
printf 'MINIO_DATA_DIR="%s"\nMINIO_DATA_DIR_CREATE=false\nMUNINN_HOST_PORT=18080\n' \
  "$data" >"$env_file"
: >"$notes"

failures=0
expect() {
  local description=$1
  shift
  if "$@"; then
    printf 'ok   %s\n' "$description"
  else
    printf 'FAIL %s\n' "$description"
    failures=$((failures + 1))
  fi
}

streams() {
  local et src inst
  for et in events.trade events.book.snapshot; do
    for src in binance.spot.v1 okx.spot.v1; do
      for inst in BTC-USDT ETH-USDT SOL-USDT XRP-USDT DOGE-USDT; do
        printf '%s\n' "$data/muninn-raw/$et/source=$src/instrument=$inst"
      done
    done
  done
}

# put STREAM DAY HOUR [old]: one object as MinIO keeps it, a folder holding xl.meta.
put() {
  local object="$1/year=2026/month=11/day=$2/hour=$3/part-p0-$RANDOM$RANDOM.jsonl.gz"
  mkdir -p "$object"
  : >"$object/xl.meta"
  if [ "${4:-}" = old ]; then touch -t 202001010000 "$object/xl.meta"; fi
}

age() { find "$1" -name xl.meta -exec touch -t 202001010000 {} +; }

# HEARTBEAT_SHELL=/bin/bash on a Mac runs it under bash 3.2, as launchd does.
shell=${HEARTBEAT_SHELL:-bash}
status=0
run_check() {
  status=0
  "$shell" "$heartbeat" check --env-file "${1:-$env_file}" >"$work/out" 2>&1 || status=$?
}

state() { sed -n 1p "$NORSE_HEARTBEAT_STATE_DIR/data-heartbeat.state"; }
since() { sed -n 2p "$NORSE_HEARTBEAT_STATE_DIR/data-heartbeat.state"; }
shown() { wc -l <"$notes" | tr -d ' '; }

doge="$data/muninn-raw/events.book.snapshot/source=okx.spot.v1/instrument=DOGE-USDT"

echo "# all twenty streams fresh"
while IFS= read -r s; do
  put "$s" 10 23 old
  put "$s" 11 00
done < <(streams)
run_check
expect "exits 0" [ "$status" -eq 0 ]
expect "state is OK" [ "$(state)" = OK ]
expect "a first run that is OK shows nothing" [ "$(shown)" -eq 0 ]
expect "reads the quoted path with a space from .env" grep -q "all 20 streams" "$work/out"

echo "# one stream stops"
age "$doge"
run_check
expect "exits 1" [ "$status" -eq 1 ]
expect "state is FAILING" [ "$(state)" = FAILING ]
expect "one notification" [ "$(shown)" -eq 1 ]
expect "it names the stream" grep -q "Norse: recording stopped.*okx book DOGE-USDT" "$notes"
expect "it does not name healthy streams" bash -c "! grep -q 'binance' '$notes'"
first_since=$(since)

echo "# still stopped"
run_check
expect "no second notification" [ "$(shown)" -eq 1 ]
expect "keeps the time it started failing" [ "$(since)" = "$first_since" ]

echo "# it resumes"
put "$doge" 11 00
run_check
expect "exits 0" [ "$status" -eq 0 ]
expect "one resumed notification" [ "$(shown)" -eq 2 ]
expect "says resumed" grep -q "Norse: recording resumed" "$notes"

echo "# across midnight only the newest hours count"
while IFS= read -r s; do
  age "$s"
  put "$s" 12 00
done < <(streams)
run_check
expect "fresh objects in the new day are found" [ "$status" -eq 0 ]

echo "# everything stops"
while IFS= read -r s; do age "$s"; done < <(streams)
run_check
expect "exits 1" [ "$status" -eq 1 ]
expect "says all streams" grep -q "any of the 20 streams" "$notes"
while IFS= read -r s; do put "$s" 12 00; done < <(streams)
run_check
expect "recovers" [ "$status" -eq 0 ]

echo "# the drive is not mounted"
mv "$drive" "$work/unmounted"
before=$(shown)
run_check
expect "exits 1" [ "$status" -eq 1 ]
expect "notifies once" [ "$(shown)" -eq $((before + 1)) ]
expect "says the drive is missing" grep -q "recordings drive missing" "$notes"
expect "names the folder" grep -q "Mac 4T/norse/minio is not there" "$notes"
mv "$work/unmounted" "$drive"
while IFS= read -r s; do put "$s" 12 00; done < <(streams)
run_check
expect "recovers when it is back" [ "$status" -eq 0 ]

echo "# a notification that fails is retried"
age "$doge"
export NORSE_HEARTBEAT_NOTIFY_LOG="$work/no/such/folder/notifications"
run_check
expect "exits 1" [ "$status" -eq 1 ]
expect "keeps the old state" [ "$(state)" = OK ]
expect "logs the failure" grep -q "notification FAILED" "$NORSE_HEARTBEAT_STATE_DIR/data-heartbeat.log"
export NORSE_HEARTBEAT_NOTIFY_LOG="$notes"
before=$(shown)
run_check
expect "the next run notifies" [ "$(shown)" -eq $((before + 1)) ]
expect "and records FAILING" [ "$(state)" = FAILING ]
put "$doge" 12 00
run_check

echo "# an unreadable stream folder"
if [ "$(id -u)" -eq 0 ]; then
  echo "note: running as root, which reads any folder; permission case not exercised"
else
  chmod 000 "$doge"
  run_check
  expect "exits 1" [ "$status" -eq 1 ]
  expect "reports it as unreadable" grep -q "okx book DOGE-USDT (unreadable)" "$work/out"
  chmod 755 "$doge"
  run_check
  expect "recovers" [ "$status" -eq 0 ]
fi

echo "# .env forms"
mkdir -p "$work/other"
printf "MINIO_DATA_DIR='%s'\n" "$data" >"$work/other/single.env"
run_check "$work/other/single.env"
expect "single quotes" [ "$status" -eq 0 ]
printf 'MINIO_DATA_DIR=%s   # a comment\n' "$work/elsewhere" >"$work/other/comment.env"
run_check "$work/other/comment.env"
expect "an unquoted value ends at a comment" grep -q "not found: $work/elsewhere\$" \
  "$NORSE_HEARTBEAT_STATE_DIR/data-heartbeat.state"
printf 'MUNINN_HOST_PORT=18080\n' >"$work/other/unset.env"
run_check "$work/other/unset.env"
expect "unset falls back to compose's ./data/minio beside the .env" \
  grep -q "not found: $work/other/data/minio" "$NORSE_HEARTBEAT_STATE_DIR/data-heartbeat.state"
mkdir -p "$work/other/rel"
ln -s "$data" "$work/other/rel/minio"
printf 'MINIO_DATA_DIR=./rel/minio\n' >"$work/other/relative.env"
run_check "$work/other/relative.env"
expect "a relative path is taken from the .env's folder" [ "$status" -eq 0 ]

echo "# the LaunchAgent plist"
"$shell" "$heartbeat" plist --env-file "$work/a & <b>/.env" >"$work/rendered.plist"
expect "renders and parses, with awkward paths" python3 - "$work/rendered.plist" "$work" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as handle:
    p = plistlib.load(handle)
assert p["Label"] == "io.github.lgreene03.norse.data-heartbeat", p["Label"]
assert p["StartInterval"] == 900 and p["RunAtLoad"] is True
args = p["ProgramArguments"]
assert args[0] == "/bin/bash" and args[2:4] == ["check", "--env-file"], args
assert args[4] == sys.argv[2] + "/a & <b>/.env", args
assert args[1].endswith("/.local/bin/norse-data-heartbeat"), args
assert p["StandardErrorPath"].endswith("/data-heartbeat.launchd.log")
PY

echo "# nothing leaves the machine"
expect "no network tools in the heartbeat" \
  bash -c "! grep -n -E '(^|[^a-z])(curl|wget|nc|ncat|ssh|scp|sendmail|mail)[[:space:]]' '$heartbeat'"

if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed"
  exit 1
fi
echo "all data heartbeat checks passed"
