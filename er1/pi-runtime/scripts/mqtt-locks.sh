#!/usr/bin/env bash
set -euo pipefail

LOCAL_BROKER="100.108.1.80"
REMOTE_BROKER="100.108.1.80"
VALID_IDS=("images" "door_to_r2" "door_to_r3" "slider" "knocking")

usage() {
  cat <<'EOF'
Usage: scripts/mqtt-locks.sh <open|close> <lock_id> [remote]

lock_id: images, door_to_r2, door_to_r3, slider, knocking
scope:  omit for local broker, pass "remote" to hit REMOTE_BROKER.
EOF
}

require_lock_id() {
  local id="$1"
  for valid in "${VALID_IDS[@]}"; do
    [[ "$id" == "$valid" ]] && return 0
  done
  echo "Invalid lock id: $id" >&2
  exit 1
}

select_broker() {
  local scope="${1:-local}"
  if [[ "$scope" == "remote" ]]; then
    printf '%s' "$REMOTE_BROKER"
  elif [[ "$scope" == "local" || -z "$scope" ]]; then
    printf '%s' "$LOCAL_BROKER"
  else
    echo "Unknown scope '$scope' (use 'remote' or omit)" >&2
    exit 1
  fi
}

send_cmd() {
  local action="$1"
  local id="$2"
  local broker="$3"
  mosquitto_pub -h "$broker" -t "esc/ctrl/lock/${id}/cmd" -m "$action"
}

main() {
  if [[ $# -lt 2 || "$1" == "-h" || "$1" == "help" || "$1" == "--help" ]]; then
    usage
    exit 0
  fi

  local action="$1"
  local id="$2"
  local scope="${3:-local}"

  case "$action" in
    open|close) ;;
    *)
      echo "Unknown action: $action (use open|close)" >&2
      usage
      exit 1
      ;;
  esac

  require_lock_id "$id"
  broker="$(select_broker "$scope")"
  send_cmd "${action^^}" "$id" "$broker"
}

main "$@"
