#!/usr/bin/env bash
set -euo pipefail

LOCAL_BROKER="192.168.0.10"
LOG_DIR="/home/rudyy/er/logs"
TS_FORMAT='[%d.%m.%Y %H:%M:%S.%N]'
SED_EXPR='s/([0-9]{3})[0-9]{6}]/\1]/'

ensure_log_dir() {
  mkdir -p "$LOG_DIR"
}

print_help() {
  cat <<'EOF'
Usage: scripts/mqtt-logs.sh <command> [args]

Commands:
  live            Subscribe to esc/#, write logs/YYYY-MM-DD.log, and echo live output.
  tail            tail -f today's logfile.
  grep <pattern>  Search today's logfile for <pattern>.
  help            Show this help text.
EOF
}

clean_line() {
  sed -E "$SED_EXPR"
}

run_live() {
  ensure_log_dir
  local current_date file cleaned today
  current_date=$(date +%Y-%m-%d)
  file="$LOG_DIR/$current_date.log"

  mosquitto_sub -h "$LOCAL_BROKER" -t 'esc/#' -v \
    | ts "$TS_FORMAT" \
    | while IFS= read -r line; do
        today=$(date +%Y-%m-%d)
        if [[ "$today" != "$current_date" ]]; then
          current_date="$today"
          file="$LOG_DIR/$current_date.log"
        fi
        cleaned=$(echo "$line" | clean_line)
        echo "$cleaned" >> "$file"
        echo "$cleaned"
      done
}

tail_today() {
  ensure_log_dir
  local file="$LOG_DIR/$(date +%Y-%m-%d).log"
  touch "$file"
  tail -f "$file"
}

grep_today() {
  ensure_log_dir
  [[ $# -ge 1 ]] || { echo "Pattern required for grep command." >&2; exit 1; }
  local pattern="$*"
  local file="$LOG_DIR/$(date +%Y-%m-%d).log"
  if [[ ! -f "$file" ]]; then
    echo "No logfile found for today: $file" >&2
    exit 1
  fi
  grep --color=auto "$pattern" "$file"
}

main() {
  local cmd="${1:-help}"
  shift || true

  case "$cmd" in
    live)
      run_live
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
