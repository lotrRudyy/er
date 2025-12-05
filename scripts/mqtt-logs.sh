#!/usr/bin/env bash
set -euo pipefail

LOCAL_BROKER="192.168.0.10"
LOG_DIR="/home/rudyy/er/logs"

ensure_log_dir() {
  mkdir -p "$LOG_DIR"
}

print_help() {
  cat <<'EOF'
Usage: scripts/mqtt-logs.sh <command> [args]

Commands:
  live            Subscribe to esc/#, write logs/er1-DD.MM.YYYY.log, and echo live output.
  tail            tail -f today's logfile.
  grep <pattern>  Search today's logfile for <pattern>.
  help            Show this help text.
EOF
}

timestamp_lines() {
  while IFS= read -r line; do
    printf '[%s] %s\n' "$(date '+%d.%m.%Y %H:%M:%S.%3N')" "$line"
  done
}

run_live() {
  ensure_log_dir
  local current_date file today
  current_date=$(date +%d.%m.%Y)
  file="$LOG_DIR/er1-$current_date.log"

  mosquitto_sub -h "$LOCAL_BROKER" -t 'esc/#' -v \
    | timestamp_lines \
    | while IFS= read -r line; do
        today=$(date +%d.%m.%Y)
        if [[ "$today" != "$current_date" ]]; then
          current_date="$today"
          file="$LOG_DIR/er1-$current_date.log"
        fi
        echo "$line" >> "$file"
        echo "$line"
      done
}

tail_today() {
  ensure_log_dir
  local file="$LOG_DIR/er1-$(date +%d.%m.%Y).log"
  touch "$file"
  tail -f "$file"
}

grep_today() {
  ensure_log_dir
  [[ $# -ge 1 ]] || { echo "Pattern required for grep command." >&2; exit 1; }
  local pattern="$*"
  local file="$LOG_DIR/er1-$(date +%d.%m.%Y).log"
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
