#!/usr/bin/env bash
# Data heartbeat: a macOS notification when recording stops, and when it resumes.
#
# The one exception the owner made on 2026-11-11 to "no alerts yet" (decision D2):
# muninn's ingestion was once silently down for about four months. launchd runs
# `check` every 15 minutes. It reads MinIO's bind mount directly, so it still works
# when MinIO or Docker is down, and checks:
#
#   1. that the recordings folder (MINIO_DATA_DIR in the stack's .env) exists,
#      which on the owner's Mac means the external drive is mounted;
#   2. that each of the twenty streams (Binance and OKX; BTC, ETH, SOL, XRP and
#      DOGE against USDT; trades and book snapshots) has an object written in the
#      last 15 minutes. The recorder flushes at least once a minute.
#
# It notifies only when the overall state changes: OK to FAILING, naming the failing
# streams or the missing drive, and FAILING to OK. Its state and a short log live
# under ~/.local/state/norse/. Nothing leaves the machine: no network calls at all.
# Operating notes: docs/RUNBOOK.md, section 6.4.
#
# For tests: NORSE_MINIO_DATA_DIR overrides the .env lookup, NORSE_HEARTBEAT_STATE_DIR
# moves the state, NORSE_HEARTBEAT_MAX_AGE_MINUTES changes the 15, and
# NORSE_HEARTBEAT_NOTIFY_LOG appends notifications to a file instead of showing them.
# Written for macOS's /bin/bash 3.2 as well as bash 5.

set -euo pipefail
export LC_ALL=C

LABEL="io.github.lgreene03.norse.data-heartbeat"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
STATE_DIR=${NORSE_HEARTBEAT_STATE_DIR:-$HOME/.local/state/norse}
STATE_FILE="$STATE_DIR/data-heartbeat.state"
LOG_FILE="$STATE_DIR/data-heartbeat.log"
LAUNCHD_LOG="$STATE_DIR/data-heartbeat.launchd.log"
LOG_KEEP=2000
MAX_AGE=${NORSE_HEARTBEAT_MAX_AGE_MINUTES:-15}
INSTALLED_SCRIPT="$HOME/.local/bin/norse-data-heartbeat"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$SCRIPT_DIR/launchd/$LABEL.plist"

BUCKET=muninn-raw
EVENT_TYPES="events.trade events.book.snapshot"
SOURCES="binance.spot.v1 okx.spot.v1"
INSTRUMENTS="BTC-USDT ETH-USDT SOL-USDT XRP-USDT DOGE-USDT"

usage() {
  cat >&2 <<'EOF'
Usage:
  data-heartbeat.sh check --env-file PATH     one check; what launchd runs
  data-heartbeat.sh install --env-file PATH   install the LaunchAgent (make heartbeat-install)
  data-heartbeat.sh uninstall                 remove it and its state (make heartbeat-uninstall)
  data-heartbeat.sh status                    the agent, the last result and the log
  data-heartbeat.sh test-notification         show one notification, to see that they appear
  data-heartbeat.sh plist --env-file PATH     print the plist that install would write

check exits 0 when OK and 1 when FAILING; any command exits 2 on a usage error.
EOF
  exit 2
}

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log_line() {
  local line lines tmp
  line="$(now_utc) $*"
  printf '%s\n' "$line"
  mkdir -p "$STATE_DIR"
  printf '%s\n' "$line" >>"$LOG_FILE"
  # Keep the log short, rewriting the same file rather than replacing it.
  lines=$(wc -l <"$LOG_FILE")
  if [ $((lines)) -gt $((LOG_KEEP * 2)) ]; then
    tmp=$(mktemp "$STATE_DIR/.log.XXXXXX")
    tail -n "$LOG_KEEP" "$LOG_FILE" >"$tmp"
    cat "$tmp" >"$LOG_FILE"
    rm -f "$tmp"
  fi
}

# MINIO_DATA_DIR as docker compose would see it: the last assignment in the .env,
# quotes removed, relative paths taken from the .env's folder, and compose's default
# (./data/minio) when it is not set. The file is parsed, never sourced.
data_dir_from_env() {
  local env_file=$1 line value="" found=0 base
  if [ -r "$env_file" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      line=${line%$'\r'}
      case $line in
        MINIO_DATA_DIR=*) value=${line#MINIO_DATA_DIR=}; found=1 ;;
        "export MINIO_DATA_DIR="*) value=${line#export MINIO_DATA_DIR=}; found=1 ;;
      esac
    done <"$env_file"
  fi
  case $value in
    \"*) value=${value#\"}; value=${value%%\"*} ;;
    \'*) value=${value#\'}; value=${value%%\'*} ;;
    *) value=${value%%[[:space:]]#*}; value=${value%"${value##*[![:space:]]}"} ;;
  esac
  if [ "$found" -eq 0 ] || [ -z "$value" ]; then
    value=./data/minio
  fi
  case $value in
    /*) printf '%s\n' "$value" ;;
    *)
      base=$(cd "$(dirname "$env_file")" 2>/dev/null && pwd) || base=$(dirname "$env_file")
      printf '%s\n' "$base/${value#./}"
      ;;
  esac
}

# Show a notification. Returns non-zero if it could not be shown, so the caller
# keeps the old state and tries again on the next run.
notify() {
  local title=$1 message=$2 sound=${3:-}
  if [ -n "${NORSE_HEARTBEAT_NOTIFY_LOG:-}" ]; then
    printf '%s\t%s\n' "$title" "$message" >>"$NORSE_HEARTBEAT_NOTIFY_LOG"
    return
  fi
  # The text goes in as arguments, never into the script, so none of it is code.
  /usr/bin/osascript \
    -e 'on run argv' \
    -e 'if item 3 of argv is "" then' \
    -e 'display notification (item 2 of argv) with title (item 1 of argv)' \
    -e 'else' \
    -e 'display notification (item 2 of argv) with title (item 1 of argv) sound name (item 3 of argv)' \
    -e 'end if' \
    -e 'end run' \
    "$title" "$message" "$sound" >/dev/null
}

# Prints nothing when the stream has an object newer than MAX_AGE minutes, and
# otherwise why not. Always succeeds. Only the two newest hour folders of the two
# newest days are searched: objects are filed by arrival hour, so anything written
# in the last 15 minutes is there.
stream_problem() {
  local dir=$1 day hour found
  local hours=()
  if [ ! -d "$dir" ]; then
    echo "no folder"
    return 0
  fi
  if ! ls "$dir" >/dev/null 2>&1; then
    echo "unreadable"
    return 0
  fi
  set -- "$dir"/year=*/month=*/day=*
  if [ ! -e "$1" ]; then
    echo "no objects"
    return 0
  fi
  if [ "$#" -gt 2 ]; then shift $(($# - 2)); fi
  for day in "$@"; do
    for hour in "$day"/hour=*; do
      if [ -e "$hour" ]; then hours+=("$hour"); fi
    done
  done
  if [ "${#hours[@]}" -eq 0 ]; then
    echo "no objects"
    return 0
  fi
  if [ "${#hours[@]}" -gt 2 ]; then
    hours=("${hours[@]:$((${#hours[@]} - 2))}")
  fi
  if found=$(find "${hours[@]}" -name xl.meta -mmin "-$MAX_AGE" -print -quit 2>/dev/null); then
    if [ -z "$found" ]; then echo "nothing new for $MAX_AGE min"; fi
  elif [ -z "$found" ]; then
    echo "unreadable"
  fi
  return 0
}

check() {
  local env_file="" data_dir
  while [ "$#" -gt 0 ]; do
    case $1 in
      --env-file) env_file=${2:-}; shift 2 ;;
      *) usage ;;
    esac
  done
  if [ -n "${NORSE_MINIO_DATA_DIR:-}" ]; then
    data_dir=$NORSE_MINIO_DATA_DIR
  elif [ -n "$env_file" ]; then
    data_dir=$(data_dir_from_env "$env_file")
  else
    usage
  fi

  local state detail title="" message="" sound=""
  local failing=() reasons=()
  local total=0 unreadable=0 et src inst problem kind label shown
  if [ ! -d "$data_dir" ]; then
    state=FAILING
    detail="recordings folder not found: $data_dir"
    title="Norse: recordings drive missing"
    message="$data_dir is not there. Is the drive mounted? Nothing can be recorded to it."
  else
    for et in $EVENT_TYPES; do
      kind=trade
      if [ "$et" = events.book.snapshot ]; then kind=book; fi
      for src in $SOURCES; do
        for inst in $INSTRUMENTS; do
          total=$((total + 1))
          problem=$(stream_problem "$data_dir/$BUCKET/$et/source=$src/instrument=$inst")
          if [ -n "$problem" ]; then
            label="${src%%.*} $kind $inst"
            failing+=("$label")
            reasons+=("$label ($problem)")
            if [ "$problem" = unreadable ]; then unreadable=$((unreadable + 1)); fi
          fi
        done
      done
    done
    if [ "${#failing[@]}" -eq 0 ]; then
      state=OK
      detail="all $total streams have an object newer than $MAX_AGE min"
    else
      state=FAILING
      detail="${#failing[@]} of $total streams: $(printf '%s, ' "${reasons[@]}")"
      detail=${detail%, }
      title="Norse: recording stopped"
      if [ "$unreadable" -eq "$total" ]; then
        title="Norse: heartbeat cannot read the recordings"
        message="$data_dir exists but cannot be read; macOS may be blocking access. See RUNBOOK 6.4."
      elif [ "${#failing[@]}" -eq "$total" ]; then
        message="No new data for $MAX_AGE min on any of the $total streams. Is muninn or MinIO down?"
      else
        shown=$(printf '%s, ' "${failing[@]:0:4}")
        message="No new data for $MAX_AGE min on ${#failing[@]} of $total streams: ${shown%, }"
        if [ "${#failing[@]}" -gt 4 ]; then
          message="$message and $((${#failing[@]} - 4)) more"
        fi
      fi
    fi
  fi
  if [ "$state" = FAILING ]; then sound=Basso; fi

  local previous=UNKNOWN
  if [ -r "$STATE_FILE" ]; then
    previous=$(sed -n 1p "$STATE_FILE")
  fi
  case $previous in
    OK | FAILING) ;;
    *) previous=UNKNOWN ;;
  esac
  if [ "$state" = OK ] && [ "$previous" = FAILING ]; then
    title="Norse: recording resumed"
    message="All $total streams have new data again."
  fi

  # A first run that finds everything OK has nothing to say. Every other change does.
  local notified=""
  if [ "$state" != "$previous" ] && [ -n "$title" ]; then
    if notify "$title" "$message" "$sound"; then
      notified=" (notified)"
    else
      # Keep the old state, so the next run tries to tell the owner again.
      log_line "$state: $detail (notification FAILED; will retry)"
      if [ "$state" = OK ]; then return 0; fi
      return 1
    fi
  fi

  mkdir -p "$STATE_DIR"
  local tmp since
  since=$(now_utc)
  if [ "$state" = "$previous" ]; then
    since=$(sed -n 2p "$STATE_FILE")
  fi
  tmp=$(mktemp "$STATE_DIR/.state.XXXXXX")
  printf '%s\n%s\n%s\n' "$state" "$since" "$detail" >"$tmp"
  mv "$tmp" "$STATE_FILE"
  log_line "$state: $detail$notified"
  if [ "$state" = OK ]; then return 0; fi
  return 1
}

# With sed, not ${s//&/...}: from bash 5.2 an unquoted & in that replacement
# stands for the match (patsub_replacement), so < would become <lt; there while
# bash 3.2 gives &lt;. CI caught exactly that.
xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

sed_escape() {
  printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'
}

render_plist() {
  local env_file=$1 script=$2
  sed -e "s|@SCRIPT@|$(sed_escape "$(xml_escape "$script")")|g" \
    -e "s|@ENV_FILE@|$(sed_escape "$(xml_escape "$env_file")")|g" \
    -e "s|@LAUNCHD_LOG@|$(sed_escape "$(xml_escape "$LAUNCHD_LOG")")|g" \
    "$TEMPLATE"
}

absolute() {
  case $1 in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$(pwd)" "${1#./}" ;;
  esac
}

require_macos() {
  if [ "$(uname -s)" != Darwin ]; then
    echo "The data heartbeat is a macOS LaunchAgent; this is $(uname -s)." >&2
    exit 2
  fi
}

install_agent() {
  local env_file="" domain tmp attempt
  while [ "$#" -gt 0 ]; do
    case $1 in
      --env-file) env_file=${2:-}; shift 2 ;;
      *) usage ;;
    esac
  done
  [ -n "$env_file" ] || usage
  require_macos
  env_file=$(absolute "$env_file")
  if [ ! -r "$env_file" ]; then
    echo "No .env at $env_file. Run this from the checkout that runs the stack." >&2
    exit 2
  fi
  domain="gui/$(id -u)"
  mkdir -p "$STATE_DIR" "$(dirname "$INSTALLED_SCRIPT")" "$(dirname "$PLIST_PATH")"
  # launchd runs a copy, so switching branches in the checkout cannot break it.
  cp "$0" "$INSTALLED_SCRIPT.tmp"
  chmod 755 "$INSTALLED_SCRIPT.tmp"
  mv "$INSTALLED_SCRIPT.tmp" "$INSTALLED_SCRIPT"
  tmp=$(mktemp "${TMPDIR:-/tmp}/heartbeat-plist.XXXXXX")
  render_plist "$env_file" "$INSTALLED_SCRIPT" >"$tmp"
  if ! plutil -lint "$tmp" >/dev/null; then
    rm -f "$tmp"
    echo "The rendered plist does not parse; nothing installed." >&2
    exit 1
  fi
  chmod 644 "$tmp"
  mv "$tmp" "$PLIST_PATH"
  if launchctl print "$domain/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "$domain/$LABEL" || true
  fi
  # A bootstrap straight after a bootout can fail while launchd finishes the unload.
  for attempt in 1 2 3 4 5; do
    if launchctl bootstrap "$domain" "$PLIST_PATH" 2>/dev/null; then
      break
    fi
    if [ "$attempt" -eq 5 ]; then
      launchctl bootstrap "$domain" "$PLIST_PATH"
    fi
    sleep 1
  done
  echo "Installed $LABEL: runs every 15 minutes and at login."
  echo "  agent:  $PLIST_PATH"
  echo "  script: $INSTALLED_SCRIPT (a copy; reinstall after changing it)"
  echo "  .env:   $env_file"
  echo "          MINIO_DATA_DIR resolves to $(data_dir_from_env "$env_file")"
  echo "  state:  $STATE_DIR"
  echo "See it with: launchctl print $domain/$LABEL"
}

uninstall_agent() {
  local domain
  require_macos
  domain="gui/$(id -u)"
  if launchctl print "$domain/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "$domain/$LABEL"
    echo "Unloaded $LABEL."
  else
    echo "$LABEL was not loaded."
  fi
  rm -f "$PLIST_PATH" "$INSTALLED_SCRIPT" "$STATE_FILE" "$LOG_FILE" "$LAUNCHD_LOG"
  rmdir "$STATE_DIR" 2>/dev/null || true
  echo "Removed the agent, its copy of the script, and its state and logs."
}

status() {
  local domain
  require_macos
  domain="gui/$(id -u)"
  if launchctl print "$domain/$LABEL" >/dev/null 2>&1; then
    launchctl print "$domain/$LABEL" |
      grep -E '^[[:space:]]*(state|runs|last exit code|run interval) =' || true
  else
    echo "$LABEL is not loaded."
  fi
  if [ -r "$STATE_FILE" ]; then
    echo "State: $(sed -n 1p "$STATE_FILE") since $(sed -n 2p "$STATE_FILE")"
    echo "  $(sed -n 3p "$STATE_FILE")"
  fi
  if [ -r "$LOG_FILE" ]; then
    echo "Last runs:"
    tail -n 5 "$LOG_FILE"
  fi
}

main() {
  local command=${1:-}
  [ -n "$command" ] || usage
  shift
  case $command in
    check) check "$@" ;;
    install) install_agent "$@" ;;
    uninstall) uninstall_agent ;;
    status) status ;;
    test-notification)
      notify "Norse: test notification" "Data heartbeat notifications are working." ""
      ;;
    plist)
      if [ "${1:-}" != --env-file ] || [ -z "${2:-}" ]; then usage; fi
      render_plist "$(absolute "$2")" "$INSTALLED_SCRIPT"
      ;;
    *) usage ;;
  esac
}

main "$@"
