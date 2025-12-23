#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${LOG_DIR_OVERRIDE:-$ROOT_DIR/logs}"
BROKER="${LOCAL_BROKER:-127.0.0.1}"
TS_FORMAT="%Y.%m.%d %H:%M:%S.%3N"

ensure_log_dir() {
  mkdir -p "$LOG_DIR"
}

log_file_for_date() {
  local date_part="$1"
  echo "$LOG_DIR/er1-${date_part}.log"
}

timestamp() {
  date +"$TS_FORMAT"
}

subscribe_stream() {
  local include_dbg="${INCLUDE_DBG:-0}"
  local include_time="${INCLUDE_TIME_STATE:-0}"
  local subs=(-t "+/log" -t "+/hb" -t "+/evt" -t "+/state" -t "+/ota")
  if [[ "$include_dbg" == "1" ]]; then
    subs+=(-t "+/dbg")
  fi
  if [[ "$include_time" != "1" ]]; then
    mosquitto_sub -h "$BROKER" "${subs[@]}" -T "time/state" -0 -v
  else
    mosquitto_sub -h "$BROKER" "${subs[@]}" -0 -v
  fi
}

write_stream() {
  local echo_output="$1"
  ensure_log_dir

  local current_date file today stamped
  current_date=$(date +%d.%m.%Y)
  file="$(log_file_for_date "$current_date")"

  # -0 from mosquitto_sub makes each message NUL-terminated, so we can
  # capture multiline payloads as a single record and escape newlines.
  subscribe_stream | while IFS= read -r -d '' line; do
    today=$(date +%d.%m.%Y)
    if [[ "$today" != "$current_date" ]]; then
      current_date="$today"
      file="$(log_file_for_date "$current_date")"
    fi

    local escaped="${line//$'\n'/\\n}"
    stamped="$(timestamp) $escaped"
    echo "$stamped" >> "$file"
    if [[ "$echo_output" == "yes" ]]; then
      echo "$stamped"
    fi
  done
}

tail_today() {
  ensure_log_dir
  local file
  file="$(log_file_for_date "$(date +%d.%m.%Y)")"
  touch "$file"
  tail -f "$file"
}

grep_today() {
  ensure_log_dir
  [[ $# -ge 1 ]] || { echo "Pattern required for grep command." >&2; exit 1; }

  local pattern file
  pattern="$*"
  file="$(log_file_for_date "$(date +%d.%m.%Y)")"

  if [[ ! -f "$file" ]]; then
    echo "No logfile found for today: $file" >&2
    exit 1
  fi

  grep --color=auto "$pattern" "$file"
}

pretty_logs() {
  local script="$ROOT_DIR/scripts/logs_pretty.py"
  if [[ ! -f "$script" ]]; then
    echo "Pretty logger not found at $script" >&2
    exit 1
  fi
  exec env LOCAL_BROKER="$BROKER" python3 "$script"
}

print_help() {
  cat <<'EOF'
Usage: scripts/mqtt_logs.sh <command> [args]

Commands:
  daemon          Capture +/log,+/hb,+/evt,+/state,+/ota to <repo>/logs/er1-DD.MM.YYYY.log (no stdout).
                  Set INCLUDE_DBG=1 to also subscribe to +/dbg. Excludes time/state by default (set INCLUDE_TIME_STATE=1 to include).
  live            Same as daemon but also echoes to stdout.
  pretty          Human-friendly dashboard + OTA merge view (uses time/state).
  tail            tail -f today's logfile.
  grep <pattern>  Search today's logfile for <pattern>.
  help            Show this help text.
Notes:
  - Broker is taken from $LOCAL_BROKER (default: 127.0.0.1).
  - Log timestamp uses: date +"%Y.%m.%d %H:%M:%S.%3N".
EOF
}

main() {
  local cmd="${1:-help}"
  shift || true

  case "$cmd" in
    daemon)
      write_stream "no"
      ;;
    live)
      write_stream "yes"
      ;;
    pretty)
      pretty_logs "$@"
      ;;
    tail)
      tail_today
      ;;
    grep)
      grep_today "$@"
      ;;
    help|-h|--help)
      print_help
      ;;
    *)
      echo "Unknown command: $cmd" >&2
      print_help
      exit 1
      ;;
  esac
}

main "$@"
