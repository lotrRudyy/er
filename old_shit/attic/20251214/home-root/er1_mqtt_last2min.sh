#!/usr/bin/env bash

LOG_FILE="/home/rudyy/logs/er1_mqtt_$(date +'%Y-%m-%d').log"

# Exit if logfile doesn't exist
[ ! -f "$LOG_FILE" ] && exit 0

# Just print the last N lines (safe, fast, always works)
tail -n 2000 "$LOG_FILE"
