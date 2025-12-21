#!/usr/bin/env bash
set -e

LOG_DIR="/home/rudyy/logs"
mkdir -p "$LOG_DIR"

DATE="$(date +'%Y-%m-%d')"
LOG_FILE="$LOG_DIR/er1_mqtt_$DATE.log"

echo "Starting mosquitto_sub, logging to $LOG_FILE"
echo "Press Ctrl+C to stop."

mosquitto_sub -h 192.168.0.10 -t 'esc/#' -v |
awk '{
  cmd = "date +\"[%d.%m.%Y %H:%M:%S.%3N]\"";
  cmd | getline ts;
  close(cmd);
  print ts, $0;
}' >> "$LOG_FILE"
